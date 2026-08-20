from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import WebSocket

from config import FeedSpec, SessionSpec
from pynecore.core.exchange_policy import tradingview_hides_zero_volume
from log_utils import log_with_time
from manual_alerts import build_manual_alert_payload, send_manual_alert_payload
from ohlcv_paths import make_ohlcv_paths, runtime_output_dir
from state import DataState
from tv_logos import static_logo_info
from ws_manager import WSManager


_MAX_AI_INSTRUCTION_CHARS = 4_000
_MAX_PROCESSED_AI_INSTRUCTION_EVENTS = 500
_MAX_RECENT_RAW_TRADE_KEYS = 10_000


def _plot_wire_value(value: Any, kind: str) -> float | int | None:
    if value is None or str(value) == "":
        return None
    return int(value) if kind == "bgcolor" else float(value)


# ======================================================================
# Paths
# ======================================================================
@dataclass(frozen=True)
class FeedPaths:
    ohlcv_path: Path
    toml_path: Path

    @classmethod
    def build(cls, spec: FeedSpec) -> "FeedPaths":
        ohlcv_path, toml_path = make_ohlcv_paths(
            spec.provider, spec.exchange, spec.symbol, spec.timeframe)
        return cls(ohlcv_path=ohlcv_path, toml_path=toml_path)


@dataclass(frozen=True)
class SessionPaths:
    plot_path: Path
    hash_path: Path
    log_path: Path

    @classmethod
    def build(cls, session_id: str) -> "SessionPaths":
        out_dir = runtime_output_dir(session_id)
        return cls(
            plot_path=out_dir / "plot.csv",
            hash_path=out_dir / "script_hash.csv",
            log_path=out_dir / "runner.log",
        )


# ======================================================================
# Feed: one shared data feed per (provider, exchange, symbol, timeframe).
# Owns the collector/file_update tasks, the OHLCV file, and the live DataState.
# Fans data-plane events out to every Session subscribed to this market. Live
# bars are chart-only; prerun_ready/run_ready are runner-only.
# ======================================================================
class Feed:
    def __init__(self, spec: FeedSpec) -> None:
        self.spec = spec
        self.paths = FeedPaths.build(spec)
        self.state = DataState()
        self.tasks: Dict[str, Any] = {}
        self.history_ready_event = asyncio.Event()
        self.data_integrity_lock = asyncio.Lock()
        self.collector_error: Optional[str] = None
        self._history_start_mtime: Optional[float] = None
        self._history_start_time: Optional[int] = None
        self._raw_trade_sequence = 0
        self._last_raw_trade_price: Optional[float] = None
        self._last_raw_trade_time_ms: Optional[int] = None
        self._raw_trade_stream_started = False
        self._recent_raw_trade_keys: deque[tuple[Any, ...]] = deque()
        self._recent_raw_trade_key_set: set[tuple[Any, ...]] = set()
        # session_id -> Session
        self.subscribers: Dict[str, "Session"] = {}

    async def broadcast_bar(self, bar: list) -> None:
        payload = {
            "type": "bar",
            "data": {
                "time": int(bar[0] // 1000),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
                "volume": float(bar[5]),
            },
        }
        for session in list(self.subscribers.values()):
            await session.send_to_charts(payload)

    @staticmethod
    def _raw_trade_key(trade: dict[str, Any]) -> tuple[Any, ...]:
        trade_id = trade.get("id")
        if trade_id is not None and str(trade_id) != "":
            return ("id", str(trade_id))
        return (
            "values",
            trade.get("timestamp"),
            trade.get("price"),
            trade.get("amount"),
            trade.get("side"),
            trade.get("order"),
        )

    def _remember_raw_trade_key(self, key: tuple[Any, ...]) -> bool:
        if key in self._recent_raw_trade_key_set:
            return False
        if len(self._recent_raw_trade_keys) >= _MAX_RECENT_RAW_TRADE_KEYS:
            expired = self._recent_raw_trade_keys.popleft()
            self._recent_raw_trade_key_set.discard(expired)
        self._recent_raw_trade_keys.append(key)
        self._recent_raw_trade_key_set.add(key)
        return True

    def raw_trade_cursor(self) -> tuple[int, float | None, int | None]:
        return (
            self._raw_trade_sequence,
            self._last_raw_trade_price,
            self._last_raw_trade_time_ms,
        )

    def broadcast_trades(self, trades: list) -> None:
        initial_batch = not self._raw_trade_stream_started
        new_trades: list[tuple[int, dict[str, Any]]] = []
        for index, trade in enumerate(trades):
            if not isinstance(trade, dict):
                continue
            if not self._remember_raw_trade_key(self._raw_trade_key(trade)):
                continue
            new_trades.append((index, trade))

        def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
            index, trade = item
            try:
                timestamp = int(trade.get("timestamp"))
            except (TypeError, ValueError):
                timestamp = 0
            return timestamp, index

        new_trades.sort(key=sort_key)
        for _, trade in new_trades:
            try:
                price = float(trade.get("price"))
            except (TypeError, ValueError):
                continue
            try:
                trade_time_ms = int(trade.get("timestamp"))
            except (TypeError, ValueError):
                trade_time_ms = None

            self._raw_trade_sequence += 1
            self._last_raw_trade_price = price
            if trade_time_ms is not None:
                self._last_raw_trade_time_ms = trade_time_ms
            for session in list(self.subscribers.values()):
                if not session.spec.manual_alert_triggers:
                    continue
                session.maybe_fire_manual_alert_trade(
                    price=price,
                    trade_time_ms=trade_time_ms,
                    sequence=self._raw_trade_sequence,
                    initial_batch=initial_batch,
                )
        self._raw_trade_stream_started = True

    async def emit_event(self, payload: dict) -> None:
        # prerun_ready / run_ready fan out to every runner subscribed to this feed.
        for session in list(self.subscribers.values()):
            if session.runner_count <= 0:
                continue
            session_payload = dict(payload)
            if (
                session.strategy_evaluation_enabled
                and payload.get("type") in {
                    "prerun_ready",
                    "prerun_ready_after_history_download",
                    "run_ready",
                }
            ):
                generation_id = await session.begin_calculation(session_payload)
                if generation_id is not None:
                    session_payload["calculation_generation_id"] = generation_id
                    session_payload["strategy_evaluation_enabled"] = True
            await session.send_to_runners(session_payload)

    def history_ready(self) -> bool:
        return self.history_ready_event.is_set()

    def history_start_time(self) -> Optional[int]:
        path = self.paths.ohlcv_path
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._history_start_mtime = None
            self._history_start_time = None
            return None
        if self._history_start_mtime == mtime:
            return self._history_start_time
        try:
            from pynecore.core.ohlcv_file import OHLCVReader
            with OHLCVReader(path) as reader:
                start_ts = reader.start_timestamp
                reader.close()
        except Exception:
            start_ts = None
        self._history_start_mtime = mtime
        self._history_start_time = int(start_ts) if start_ts is not None else None
        return self._history_start_time

    def last_bar_time(self) -> Optional[int]:
        bars = self.state.live_bars
        if bars:
            return int(bars[-1][0] // 1000)
        return None

    def last_price(self) -> Optional[float]:
        bars = self.state.live_bars
        if bars:
            return float(bars[-1][4])
        return None

    def collector_status(self) -> str:
        if self.collector_error:
            return "error"
        if self.tasks and any(not t.done() for t in self.tasks.values()):
            return "running"
        return "stopped"


# ======================================================================
# Session: one per strategy instance. Owns the chart/runner websocket clients,
# the per-session plot/trade/chart state, and the runner process. Subscribes to
# a shared Feed for market data. Multiple sessions may share one Feed (e.g. two
# strategies on the same BTC market).
# ======================================================================
class Session:
    def __init__(
        self,
        spec: SessionSpec,
        feed: Feed,
        *,
        strategy_evaluation_enabled: bool = False,
    ) -> None:
        self.spec = spec
        self.feed = feed
        self.strategy_evaluation_enabled = strategy_evaluation_enabled
        self.paths = SessionPaths.build(spec.id)

        self.ws_manager = WSManager(on_disconnect=self._cleanup_client)
        self.trades_history: List[Dict[str, Any]] = []
        self.plot_options: Dict[str, Dict[str, Any]] = {}
        self.plotchar_history: List[Dict[str, Any]] = []
        self.client_roles: Dict[WebSocket, Optional[str]] = {}
        self.runner_count = 0
        # True only after the runner finishes its first pre_run (chart plots ready).
        # Drives the dashboard LED: connected-but-prerunning = "starting" (amber),
        # ready = "running" (green).
        self.runner_ready = False
        self.history_resync_pending = False
        self.calculation_generation_id = uuid.uuid4().hex
        self.calculation_status = "stopped"
        self.calculation_latest_confirmed_bar: int | None = None
        self.calculated_through: int | None = None
        self.calculation_updated_at = datetime.now(UTC).isoformat()
        self.strategy_snapshot: dict[str, Any] | None = None
        self.strategy_snapshot_generation_id: str | None = None
        self._calculation_condition = asyncio.Condition()
        self.chart_info: Dict[str, Any] = {
            "exchange": spec.exchange,
            "symbol": spec.symbol,
            "market_type": spec.market_type,
            "timeframe": spec.timeframe,
            "provider": spec.provider,
            "script_title": None,
            "script_source_name": None,
            "script_source": "",
        }
        self.logo_info: Dict[str, str] = static_logo_info(spec.exchange, spec.symbol)
        # registry wires this to push /ws/hub status when runner connect/disconnect.
        self.on_status_change: Optional[Callable[[], Awaitable[None]]] = None
        self.on_spec_change: Optional[Callable[[], Awaitable[None]]] = None
        self.on_ai_instruction: Optional[Callable[["Session", dict], Awaitable[None]]] = None
        self._processed_ai_instruction_ids: set[str] = set()
        self._processed_ai_instruction_order: deque[str] = deque()
        self._manual_alert_trigger_sending_ids: set[str] = set()
        self._manual_alert_trigger_gates: dict[str, dict[str, Any]] = {}
        self.reset_manual_alert_trigger_gate()

    def reconfigure_script(self, spec: SessionSpec) -> None:
        """Apply a stopped-session script change without replacing its identity."""
        self.spec = spec
        self.trades_history.clear()
        self.plot_options.clear()
        self.plotchar_history.clear()
        self.runner_ready = False
        self.history_resync_pending = False
        self.calculation_generation_id = uuid.uuid4().hex
        self.calculation_status = "stopped"
        self.calculation_latest_confirmed_bar = None
        self.calculated_through = None
        self.calculation_updated_at = datetime.now(UTC).isoformat()
        self.strategy_snapshot = None
        self.strategy_snapshot_generation_id = None
        self.chart_info["script_title"] = None
        self.chart_info["script_source_name"] = None
        self.chart_info["script_source"] = ""
        self._processed_ai_instruction_ids.clear()
        self._processed_ai_instruction_order.clear()

    @property
    def ohlcv_path(self) -> Path:
        return self.feed.paths.ohlcv_path

    async def _notify_status(self) -> None:
        if self.on_status_change is not None:
            try:
                await self.on_status_change()
            except Exception:
                pass

    async def _notify_spec_change(self) -> None:
        if self.on_spec_change is not None:
            try:
                await self.on_spec_change()
            except Exception:
                pass

    @staticmethod
    def _event_confirmed_bar_time(event: dict[str, Any]) -> int | None:
        bars = event.get("confirmed_bar_and_new_bar")
        if not isinstance(bars, list) or not bars:
            return None
        confirmed = bars[0]
        if not isinstance(confirmed, (list, tuple)) or not confirmed:
            return None
        try:
            return int(confirmed[0] // 1000)
        except (TypeError, ValueError):
            return None

    def _event_confirmed_bar_is_hidden(self, event: dict[str, Any]) -> bool:
        bars = event.get("confirmed_bar_and_new_bar")
        if not isinstance(bars, list) or not bars:
            return False
        confirmed = bars[0]
        if not isinstance(confirmed, (list, tuple)) or len(confirmed) < 6:
            return False
        if not tradingview_hides_zero_volume(self.spec.exchange):
            return False
        try:
            return float(confirmed[5]) == 0.0
        except (TypeError, ValueError):
            return False

    async def begin_calculation(self, event: dict[str, Any] | None = None) -> str | None:
        event = event or {}
        if self._event_confirmed_bar_is_hidden(event):
            return None
        target = self._event_confirmed_bar_time(event)
        async with self._calculation_condition:
            self.calculation_generation_id = uuid.uuid4().hex
            self.calculation_status = (
                "starting" if event.get("type") == "runner_start" else "prerun"
            )
            self.calculation_latest_confirmed_bar = target
            self.calculated_through = None
            self.calculation_updated_at = datetime.now(UTC).isoformat()
            self.strategy_snapshot = None
            self.strategy_snapshot_generation_id = None
            self._calculation_condition.notify_all()
            return self.calculation_generation_id

    async def set_calculation_stopped(self) -> None:
        async with self._calculation_condition:
            self.calculation_status = "stopped"
            self.calculation_updated_at = datetime.now(UTC).isoformat()
            self._calculation_condition.notify_all()

    async def apply_strategy_snapshot(self, event: dict[str, Any]) -> bool:
        event_generation_id = str(event.get("calculation_generation_id") or "")
        try:
            calculated_through = int(event["calculated_through"])
        except (KeyError, TypeError, ValueError):
            calculated_through = None
        snapshot = dict(event)
        snapshot.pop("type", None)
        async with self._calculation_condition:
            if event_generation_id != self.calculation_generation_id:
                return False
            if self.calculation_latest_confirmed_bar is None:
                self.calculation_latest_confirmed_bar = calculated_through
            self.calculated_through = calculated_through
            self.strategy_snapshot = snapshot
            self.strategy_snapshot_generation_id = self.calculation_generation_id
            target = self.calculation_latest_confirmed_bar
            if calculated_through is not None and (target is None or calculated_through >= target):
                self.calculation_status = "ready"
            else:
                self.calculation_status = "prerun"
            self.calculation_updated_at = datetime.now(UTC).isoformat()
            self._calculation_condition.notify_all()
            return True

    def calculation_state_payload(self) -> dict[str, Any]:
        return {
            "status": self.calculation_status,
            "generation_id": self.calculation_generation_id,
            "calculated_through": self.calculated_through,
            "latest_confirmed_bar": self.calculation_latest_confirmed_bar,
            "snapshot_generation_id": self.strategy_snapshot_generation_id,
            "updated_at": self.calculation_updated_at,
        }

    def calculation_ready(self) -> bool:
        target = self.calculation_latest_confirmed_bar
        return bool(
            self.calculation_status == "ready"
            and self.strategy_snapshot is not None
            and self.strategy_snapshot_generation_id == self.calculation_generation_id
            and self.calculated_through is not None
            and (target is None or self.calculated_through >= target)
        )

    async def wait_for_calculation_ready(self, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        async with self._calculation_condition:
            while not self.calculation_ready():
                if self.runner_count <= 0 or self.calculation_status == "stopped":
                    return False
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(
                        self._calculation_condition.wait(),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    return False
            return True

    # ------------------------------------------------------------------
    # WebSocket lifecycle
    # ------------------------------------------------------------------
    async def on_connect(self, ws: WebSocket) -> None:
        await self.ws_manager.connect(ws)
        self.client_roles[ws] = None
        if self.runner_count > 0:
            await self.ws_manager.send(ws, {"type": "runner_connected"})

    async def on_disconnect(self, ws: WebSocket) -> None:
        await self.ws_manager.disconnect(ws)

    async def _cleanup_client(self, ws: WebSocket) -> None:
        role = self.client_roles.pop(ws, None)
        if role == "runner":
            self.runner_count -= 1
            if self.runner_count <= 0:
                self.runner_count = 0
                self.runner_ready = False
                await self.set_calculation_stopped()
                await self.send_to_charts({"type": "runner_disconnected"})
            await self._notify_status()

    async def handle_text(self, ws: WebSocket, msg_text: str) -> None:
        try:
            msg = json.loads(msg_text)
        except json.JSONDecodeError:
            return  # keepalive ping
        events = msg if isinstance(msg, list) else [msg]
        for event in events:
            await self._handle_event(ws, event)

    async def _handle_event(self, ws: WebSocket, event: dict) -> None:
        msg_type = event.get("type")
        ws_manager = self.ws_manager
        ohlcv_path = self.feed.paths.ohlcv_path
        plot_path = self.paths.plot_path

        if msg_type == "client_hello":
            role = event.get("role")
            if role == "runner":
                if self.strategy_evaluation_enabled:
                    await self.begin_calculation({"type": "runner_start"})
                # Replay the feed's pending after-history prerun only after the
                # client identifies as a runner. Until then it receives no live
                # bars, so long pre_run cannot build up a chart-message backlog.
                async with self.feed.state.lock:
                    pending_prerun_event = self.feed.state.pending_prerun_event
                if pending_prerun_event is not None:
                    pending_prerun_event = dict(pending_prerun_event)
                    if self.history_resync_pending:
                        pending_prerun_event["history_resync"] = True
                    if self.strategy_evaluation_enabled:
                        generation_id = await self.begin_calculation(pending_prerun_event)
                        if generation_id is not None:
                            pending_prerun_event["calculation_generation_id"] = generation_id
                            pending_prerun_event["strategy_evaluation_enabled"] = True
                    await ws_manager.send(ws, pending_prerun_event)

                self.client_roles[ws] = role
                self.runner_count += 1
                self.runner_ready = False  # fresh runner: pre_run not done yet (amber)
                await self.send_to_charts({"type": "runner_connected"})
                await self._notify_status()
                # Push this session's webhook/telegram config to the runner only
                # (carries url/token — must not reach chart-page browsers).
                await ws_manager.send(ws, self._webhook_config_payload())
            elif role == "chart":
                self.client_roles[ws] = role
                if self.runner_count > 0:
                    await ws_manager.send(ws, {"type": "runner_connected"})
                await ws_manager.send(ws, {
                    "type": "manual_alert_trigger",
                    "triggers": self.manual_alert_triggers_payload(),
                })
            else:
                self.client_roles[ws] = None

        elif msg_type == "runner_ready":
            # Runner finished its first pre_run -> flip the LED to green.
            self.runner_ready = True
            self.history_resync_pending = False
            await self._notify_status()

        elif msg_type == "strategy_snapshot":
            if self.client_roles.get(ws) != "runner":
                return
            await self.apply_strategy_snapshot(event)

        elif msg_type == "last_bar_open_fix":
            last_bar_index = event.get("last_bar_index", -1)
            event_data = event.get("data")
            if isinstance(event_data, dict):
                await self.send_to_charts({
                    "type": "last_bar_open_fix",
                    "data": event_data,
                })
            elif last_bar_index > 0:
                try:
                    from pynecore.core.ohlcv_file import OHLCVReader
                    with OHLCVReader(ohlcv_path) as reader:
                        last_bar = reader.read(last_bar_index)
                        payload = {
                            "type": "last_bar_open_fix",
                            "data": {
                                "time": int(last_bar.timestamp),
                                "open": float(last_bar.open),
                            },
                        }
                        await self.send_to_charts(payload)
                        reader.close()
                except Exception as e:
                    print(f"[{self.spec.id}] Failed to send confirmed bar: {e}")

        elif msg_type == "ai_instruction":
            if self.client_roles.get(ws) != "runner":
                return
            event_id = str(event.get("event_id") or "").strip()
            instruction = str(event.get("instruction") or "").strip()
            if not event_id or len(event_id) > 128:
                log_with_time(f"[{self.spec.id}] ignored AI instruction with invalid event ID")
                return
            if not instruction or len(instruction) > _MAX_AI_INSTRUCTION_CHARS:
                log_with_time(f"[{self.spec.id}] ignored invalid AI instruction")
                return
            if event_id in self._processed_ai_instruction_ids:
                return

            self._processed_ai_instruction_ids.add(event_id)
            self._processed_ai_instruction_order.append(event_id)
            while len(self._processed_ai_instruction_order) > _MAX_PROCESSED_AI_INSTRUCTION_EVENTS:
                expired_id = self._processed_ai_instruction_order.popleft()
                self._processed_ai_instruction_ids.discard(expired_id)

            if self.on_ai_instruction is None:
                log_with_time(f"[{self.spec.id}] AI instruction skipped: no handler")
                return
            normalized_event = dict(event)
            normalized_event["event_id"] = event_id
            normalized_event["instruction"] = instruction
            try:
                await self.on_ai_instruction(self, normalized_event)
            except Exception as e:
                log_with_time(f"[{self.spec.id}] AI instruction dispatch failed: {e}")

        elif msg_type in ("trade_entry", "trade_close"):
            if event not in self.trades_history:
                self.trades_history.append(event)
            await self.send_to_charts(event)

        elif msg_type == "plotchar":
            if event not in self.plotchar_history:
                self.plotchar_history.append(event)
            await self.send_to_charts(event)

        elif msg_type == "plot_options":
            self.plot_options.update(event.get("data", {}))
            confirmed_bar_index = event.get("confirmed_bar_index", -1)
            confirmed_bar_time = event.get("confirmed_bar_time")
            plot_values = event.get("values")

            if self.plot_options and (confirmed_bar_time is not None or confirmed_bar_index >= 0):
                try:
                    if isinstance(plot_values, dict) and confirmed_bar_time is not None:
                        for title, options in self.plot_options.items():
                            kind = str(options.get("kind") or "line")
                            await self.send_to_charts({
                                "type": "plot_data",
                                "title": title,
                                "kind": kind,
                                "time": int(confirmed_bar_time),
                                "value": _plot_wire_value(plot_values.get(title), kind),
                            })
                    elif plot_path.exists():
                        # Backward-compatible fallback for runners that do not include
                        # current values in the plot_options event.
                        from pynecore.core.csv_file import CSVReader
                        with CSVReader(plot_path) as reader:
                            candle = None
                            if confirmed_bar_time is not None:
                                for row in reader.read_from(int(confirmed_bar_time),
                                                            int(confirmed_bar_time)):
                                    candle = row
                                    break
                            else:
                                candle = reader.read(confirmed_bar_index)

                            if candle is None:
                                return

                            for title, options in self.plot_options.items():
                                kind = str(options.get("kind") or "line")
                                value = candle.extra_fields.get(title)
                                plot_data_event = {
                                    "type": "plot_data",
                                    "title": title,
                                    "kind": kind,
                                    "time": int(candle.timestamp),
                                    "value": _plot_wire_value(value, kind),
                                }
                                await self.send_to_charts(plot_data_event)
                            reader.close()
                except Exception as e:
                    print(f"[{self.spec.id}] Failed to broadcast plot data: {e}")

        elif msg_type == "script_info":
            title = event.get("title") or "No title"
            self.chart_info["script_title"] = title
            self.chart_info["script_source_name"] = (
                event.get("source_name") or self.chart_info.get("script_source_name")
            )
            if "source" in event:
                self.chart_info["script_source"] = event.get("source") or ""
            await self.send_to_charts({
                "type": "script_info",
                "title": title,
                "source_name": self.chart_info.get("script_source_name"),
                "source": self.chart_info.get("script_source") or "",
            })

        elif (msg_type == "reset_history") or (msg_type == "script_modified"):
            self.trades_history.clear()
            self.plot_options.clear()
            self.plotchar_history.clear()
            if msg_type == "script_modified":
                self.chart_info["script_title"] = None
                self.chart_info["script_source_name"] = None
                self.chart_info["script_source"] = ""
                await self.send_to_charts({"type": "script_modified"})

        elif msg_type == "chart_reset":
            # Data window changed (history_since edit): the runner has finished
            # regenerating plot.csv and re-emitting markers, so tell chart pages to
            # drop stale series and reload from the fresh files. Caches were already
            # cleared via the reset_history above.
            await self.send_to_charts({"type": "chart_reset"})

        elif msg_type == "ack_prerun_ready_after_history_download":
            # No-op: with a shared feed the pending event must reach every session's
            # runner, so it is not cleared globally (see client_hello handling).
            pass

    # ------------------------------------------------------------------
    # Broadcast helper for the per-session live webhook toggle (decision 8-1)
    # ------------------------------------------------------------------
    def _webhook_config_payload(self) -> dict:
        wh = self.spec.webhook
        return {
            "type": "webhook_config",
            "enabled": bool(wh.get("enabled", False)),
            "url": wh.get("url", "") or "",
            "telegram_notification": bool(wh.get("telegram_notification", False)),
            "telegram_token": wh.get("telegram_token", "") or "",
            "telegram_chat_id": wh.get("telegram_chat_id", "") or "",
        }

    async def send_to_charts(self, payload: dict) -> None:
        for ws, role in list(self.client_roles.items()):
            if role == "chart":
                await self.ws_manager.send(ws, payload)

    async def send_to_runners(self, payload: dict) -> None:
        for ws, role in list(self.client_roles.items()):
            if role == "runner":
                await self.ws_manager.send(ws, payload)

    def manual_alert_triggers_payload(self) -> list[dict]:
        return [dict(t) for t in self.spec.manual_alert_triggers]

    @staticmethod
    def _manual_alert_trigger_signature(trigger: dict) -> str:
        return json.dumps(
            {
                "price": trigger.get("price"),
                "template": trigger.get("template"),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def reset_manual_alert_trigger_gate(self) -> None:
        sequence, last_price, last_trade_time_ms = self.feed.raw_trade_cursor()
        armed_at_ms = int(time.time() * 1000)
        active_ids: set[str] = set()
        gates: dict[str, dict[str, Any]] = {}
        for trigger in self.spec.manual_alert_triggers:
            if not trigger.get("enabled"):
                continue
            trigger_id = str(trigger.get("id") or "")
            if not trigger_id:
                continue
            active_ids.add(trigger_id)
            signature = self._manual_alert_trigger_signature(trigger)
            previous = self._manual_alert_trigger_gates.get(trigger_id)
            if previous is not None and previous.get("signature") == signature:
                gates[trigger_id] = previous
                continue
            gates[trigger_id] = {
                "signature": signature,
                "armed_sequence": sequence,
                "armed_at_ms": armed_at_ms,
                "last_price": last_price,
                "last_trade_time_ms": last_trade_time_ms,
            }
        self._manual_alert_trigger_gates = gates
        self._manual_alert_trigger_sending_ids.intersection_update(active_ids)

    async def push_manual_alert_trigger(self) -> None:
        await self.send_to_charts({
            "type": "manual_alert_trigger",
            "triggers": self.manual_alert_triggers_payload(),
        })

    async def push_webhook_config(self) -> None:
        # Runner-only (contains url/token); never broadcast to chart browsers.
        await self.send_to_runners(self._webhook_config_payload())

    def _manual_alert_script_title(self) -> str | None:
        title = self.chart_info.get("script_title")
        if title:
            return str(title)
        if self.spec.script_name:
            return Path(self.spec.script_name).stem
        return None

    @staticmethod
    def _manual_alert_trigger_touched(prev_price: float | None, market_price: float,
                                      trigger_price: float) -> bool:
        if prev_price is None:
            return market_price == trigger_price
        low = min(float(prev_price), market_price)
        high = max(float(prev_price), market_price)
        return low <= trigger_price <= high

    def _remove_manual_alert_trigger(self, trigger_id: str) -> bool:
        triggers = [dict(t) for t in self.spec.manual_alert_triggers]
        remaining = [t for t in triggers if str(t.get("id", "")) != trigger_id]
        if len(remaining) == len(triggers):
            return False
        self.spec = self.spec.with_manual_alert_triggers(remaining)
        self._manual_alert_trigger_gates.pop(trigger_id, None)
        return True

    async def _discard_manual_alert_trigger(self, trigger_id: str) -> None:
        if trigger_id and self._remove_manual_alert_trigger(trigger_id):
            await self._notify_spec_change()
            await self.push_manual_alert_trigger()

    async def _send_manual_alert_trigger(self, trigger: dict, trigger_price: float,
                                         market_price: float, bar_time: int) -> None:
        template = trigger.get("template") or {}
        trigger_id = str(trigger.get("id") or "")
        await self._discard_manual_alert_trigger(trigger_id)
        try:
            payload = build_manual_alert_payload(
                template=template,
                spec=self.spec,
                price=trigger_price,
                market=market_price,
                time=bar_time,
            )
            result = await asyncio.to_thread(
                send_manual_alert_payload,
                spec=self.spec,
                script_title=self._manual_alert_script_title(),
                payload=payload,
            )
            webhook = result.get("webhook") if isinstance(result, dict) else None
            if isinstance(webhook, dict) and webhook.get("error"):
                log_with_time(
                    f"[manual_alert_trigger] webhook failed for {self.spec.id}: "
                    f"{webhook['error']}"
                )
            telegram = result.get("telegram") if isinstance(result, dict) else None
            if isinstance(telegram, dict) and telegram.get("error"):
                log_with_time(
                    f"[manual_alert_trigger] Telegram failed for {self.spec.id}: "
                    f"{telegram['error']}"
                )
        except Exception as e:
            log_with_time(
                f"[manual_alert_trigger] send failed for {self.spec.id} "
                f"trigger={trigger_id or '?'} price={trigger_price}: {e}"
            )
        else:
            await self.dispatch_manual_alert_ai_instruction(
                payload,
                result,
                mode="trigger",
                trigger_id=trigger_id,
            )
            await self.send_to_charts({
                "type": "manual_alert_trigger_fired",
                "triggers": self.manual_alert_triggers_payload(),
                "result": result,
            })
        finally:
            if trigger_id:
                self._manual_alert_trigger_sending_ids.discard(trigger_id)

    def maybe_fire_manual_alert_trade(
        self,
        *,
        price: float,
        trade_time_ms: int | None,
        sequence: int,
        initial_batch: bool,
    ) -> None:
        for trigger in self.spec.manual_alert_triggers:
            if not trigger.get("enabled"):
                continue
            trigger_id = str(trigger.get("id") or "")
            if not trigger_id or trigger_id in self._manual_alert_trigger_sending_ids:
                continue
            gate = self._manual_alert_trigger_gates.get(trigger_id)
            if gate is None or sequence <= int(gate.get("armed_sequence", 0)):
                continue
            try:
                trigger_price = float(trigger.get("price"))
            except (TypeError, ValueError):
                continue

            last_trade_time_ms = gate.get("last_trade_time_ms")
            if (
                trade_time_ms is not None
                and last_trade_time_ms is not None
                and trade_time_ms < int(last_trade_time_ms)
            ):
                continue
            armed_at_ms = int(gate.get("armed_at_ms", 0))
            if trade_time_ms is not None and trade_time_ms < armed_at_ms:
                gate["last_price"] = price
                gate["last_trade_time_ms"] = trade_time_ms
                continue
            if initial_batch and trade_time_ms is None:
                gate["last_price"] = price
                continue

            previous_price = gate.get("last_price")
            gate["last_price"] = price
            if trade_time_ms is not None:
                gate["last_trade_time_ms"] = trade_time_ms
            if not self._manual_alert_trigger_touched(previous_price, price, trigger_price):
                continue

            event_time = (
                trade_time_ms // 1000
                if trade_time_ms is not None
                else int(time.time())
            )
            self._manual_alert_trigger_sending_ids.add(trigger_id)
            asyncio.create_task(
                self._send_manual_alert_trigger(
                    dict(trigger),
                    trigger_price,
                    price,
                    event_time,
                )
            )

    async def dispatch_manual_alert_ai_instruction(
        self,
        payload: dict,
        delivery_result: dict,
        *,
        mode: str,
        trigger_id: str = "",
    ) -> bool:
        instruction = str(payload.get("ai_instruction") or "").strip()
        if not instruction:
            return False
        webhook = delivery_result.get("webhook")
        if not isinstance(webhook, dict) or not webhook.get("sent"):
            return False
        if len(instruction) > _MAX_AI_INSTRUCTION_CHARS:
            log_with_time(
                f"[{self.spec.id}] Manual Alert AI instruction skipped: instruction too long"
            )
            return False
        if self.on_ai_instruction is None:
            log_with_time(
                f"[{self.spec.id}] Manual Alert AI instruction skipped: no handler"
            )
            return False

        telegram = delivery_result.get("telegram")
        telegram = telegram if isinstance(telegram, dict) else {}
        event = {
            "event_id": f"manual-alert-{uuid.uuid4().hex}",
            "instruction": instruction,
            "source": "manual_alert",
            "action": "manual_alert",
            "mode": "trigger" if mode == "trigger" else "send",
            "time": payload.get("time"),
            "template_title": str(payload.get("title") or ""),
            "trigger_id": str(trigger_id or ""),
            "trigger_price": payload.get("price"),
            "market_price": payload.get("market"),
            "webhook_sent": True,
            "telegram_sent": bool(telegram.get("sent", False)),
            "telegram_failed": bool(telegram.get("error")),
        }
        try:
            await self.on_ai_instruction(self, event)
        except Exception as e:
            log_with_time(
                f"[{self.spec.id}] Manual Alert AI instruction dispatch failed: {e}"
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Status snapshot (merges feed data-plane state)
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        feed = self.feed
        return {
            "id": self.spec.id,
            "feed_id": feed.spec.id,
            "provider": self.spec.provider,
            "exchange": self.spec.exchange,
            "symbol": self.spec.symbol,
            "market_type": self.spec.market_type,
            "timeframe": self.spec.timeframe,
            "history_since": self.spec.history_since,
            "script_name": self.spec.script_name,
            "tv_symbol": self.logo_info.get("tv_symbol", ""),
            "symbol_logo_url": self.logo_info.get("symbol_logo_url", ""),
            "quote_logo_url": self.logo_info.get("quote_logo_url", ""),
            "exchange_logo_url": self.logo_info.get("exchange_logo_url", ""),
            # Only the booleans go in the broadcast snapshot; url/token are fetched
            # on demand via GET /api/{id}/webhook-config (kept out of /ws/hub).
            "webhook": {
                "enabled": bool(self.spec.webhook.get("enabled", False)),
                "telegram_notification": bool(self.spec.webhook.get("telegram_notification", False)),
            },
            "collector": feed.collector_status(),
            "history_ready": feed.history_ready(),
            "data_since_time": feed.history_start_time(),
            "runner_connected": self.runner_count > 0,
            "runner_ready": self.runner_ready,
            "calculation": self.calculation_state_payload(),
            "last_bar_time": feed.last_bar_time(),
            "last_price": feed.last_price(),
        }
