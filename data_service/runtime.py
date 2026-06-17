from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import WebSocket

from config import FeedSpec, SessionSpec
from ohlcv_paths import make_ohlcv_paths, runtime_output_dir
from state import DataState
from tv_logos import static_logo_info
from ws_manager import WSManager


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
# Fans data-plane events (bar / prerun_ready / run_ready) out to every Session
# subscribed to this market.
# ======================================================================
class Feed:
    def __init__(self, spec: FeedSpec) -> None:
        self.spec = spec
        self.paths = FeedPaths.build(spec)
        self.state = DataState()
        self.tasks: List[Any] = []
        self.collector_error: Optional[str] = None
        # session_id -> Session
        self.subscribers: Dict[str, "Session"] = {}

    def _ws_managers(self) -> List[WSManager]:
        return [s.ws_manager for s in self.subscribers.values()]

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
        for wm in self._ws_managers():
            await wm.broadcast_json(payload)

    async def emit_event(self, payload: dict) -> None:
        # prerun_ready / run_ready fan out to every runner subscribed to this feed.
        for wm in self._ws_managers():
            await wm.broadcast_json(payload)

    def history_ready(self) -> bool:
        return self.paths.ohlcv_path.exists()

    def last_bar_time(self) -> Optional[int]:
        bars = self.state.live_bars
        if bars:
            return int(bars[-1][0] // 1000)
        return None

    def collector_status(self) -> str:
        if self.collector_error:
            return "error"
        if self.tasks and any(not t.done() for t in self.tasks):
            return "running"
        return "stopped"


# ======================================================================
# Session: one per strategy instance. Owns the chart/runner websocket clients,
# the per-session plot/trade/chart state, and the runner process. Subscribes to
# a shared Feed for market data. Multiple sessions may share one Feed (e.g. two
# strategies on the same BTC market).
# ======================================================================
class Session:
    def __init__(self, spec: SessionSpec, feed: Feed) -> None:
        self.spec = spec
        self.feed = feed
        self.paths = SessionPaths.build(spec.id)

        self.ws_manager = WSManager()
        self.trades_history: List[Dict[str, Any]] = []
        self.plot_options: Dict[str, Dict[str, Any]] = {}
        self.plotchar_history: List[Dict[str, Any]] = []
        self.client_roles: Dict[WebSocket, Optional[str]] = {}
        self.runner_count = 0
        # True only after the runner finishes its first pre_run (chart plots ready).
        # Drives the dashboard LED: connected-but-prerunning = "starting" (amber),
        # ready = "running" (green).
        self.runner_ready = False
        self.chart_info: Dict[str, Any] = {
            "exchange": spec.exchange,
            "symbol": spec.symbol,
            "timeframe": spec.timeframe,
            "provider": spec.provider,
            "script_title": None,
            "script_source_name": None,
            "script_source": "",
        }
        self.logo_info: Dict[str, str] = static_logo_info(spec.exchange, spec.symbol)
        # registry wires this to push /ws/hub status when runner connect/disconnect.
        self.on_status_change: Optional[Callable[[], Awaitable[None]]] = None

    @property
    def ohlcv_path(self) -> Path:
        return self.feed.paths.ohlcv_path

    async def _notify_status(self) -> None:
        if self.on_status_change is not None:
            try:
                await self.on_status_change()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # WebSocket lifecycle
    # ------------------------------------------------------------------
    async def on_connect(self, ws: WebSocket) -> None:
        await self.ws_manager.connect(ws)
        self.client_roles[ws] = None
        if self.runner_count > 0:
            await ws.send_json({"type": "runner_connected"})

        # Replay the feed's pending after-history prerun to every newly connected
        # runner (each strategy's runner needs its own history prerun). Unlike the
        # single-session version we do NOT clear it on ack, so late-joining runners
        # on the same shared feed still receive it.
        async with self.feed.state.lock:
            if self.feed.state.pending_prerun_event is not None:
                try:
                    await ws.send_json(self.feed.state.pending_prerun_event)
                except Exception as e:
                    print(f"[{self.spec.id}] Failed to send pending event: {e}")

    async def on_disconnect(self, ws: WebSocket) -> None:
        await self.ws_manager.disconnect(ws)
        role = self.client_roles.pop(ws, None)
        if role == "runner":
            self.runner_count -= 1
            if self.runner_count <= 0:
                self.runner_count = 0
                self.runner_ready = False
                await self.ws_manager.broadcast_json({"type": "runner_disconnected"})
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
            self.client_roles[ws] = role
            if role == "runner":
                self.runner_count += 1
                self.runner_ready = False  # fresh runner: pre_run not done yet (amber)
                await ws_manager.broadcast_json({"type": "runner_connected"})
                await self._notify_status()
                # Push this session's webhook/telegram config to the runner only
                # (carries url/token — must not reach chart-page browsers).
                try:
                    await ws.send_json(self._webhook_config_payload())
                except Exception:
                    pass

        elif msg_type == "runner_ready":
            # Runner finished its first pre_run -> flip the LED to green.
            self.runner_ready = True
            await self._notify_status()

        elif msg_type == "last_bar_open_fix":
            last_bar_index = event.get("last_bar_index", -1)
            event_data = event.get("data")
            if isinstance(event_data, dict):
                await ws_manager.broadcast_json({
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
                                "high": float(last_bar.high),
                                "low": float(last_bar.low),
                                "close": float(last_bar.close),
                                "volume": float(last_bar.volume),
                            },
                        }
                        await ws_manager.broadcast_json(payload)
                        reader.close()
                except Exception as e:
                    print(f"[{self.spec.id}] Failed to send confirmed bar: {e}")

        elif msg_type in ("trade_entry", "trade_close"):
            if event not in self.trades_history:
                self.trades_history.append(event)
            await ws_manager.broadcast_json(event)

        elif msg_type == "plotchar":
            if event not in self.plotchar_history:
                self.plotchar_history.append(event)
            await ws_manager.broadcast_json(event)

        elif msg_type == "plot_options":
            self.plot_options.update(event.get("data", {}))
            confirmed_bar_index = event.get("confirmed_bar_index", -1)
            confirmed_bar_time = event.get("confirmed_bar_time")

            if self.plot_options and (confirmed_bar_time is not None or confirmed_bar_index >= 0):
                try:
                    if plot_path.exists():
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

                            for title in self.plot_options.keys():
                                value = candle.extra_fields.get(title)
                                plot_data_event = {
                                    "type": "plot_data",
                                    "title": title,
                                    "time": int(candle.timestamp),
                                    "value": None if (value == "" or value is None) else float(value),
                                }
                                await ws_manager.broadcast_json(plot_data_event)
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
            await ws_manager.broadcast_json({
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
                await ws_manager.broadcast_json({"type": "script_modified"})

        elif msg_type == "ack_prerun_ready_after_history_download":
            # No-op: with a shared feed the pending event must reach every session's
            # runner, so it is not cleared globally (see on_connect).
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

    async def _send_to_runners(self, payload: dict) -> None:
        for ws, role in list(self.client_roles.items()):
            if role == "runner":
                try:
                    await ws.send_json(payload)
                except Exception:
                    pass

    async def push_webhook_config(self) -> None:
        # Runner-only (contains url/token); never broadcast to chart browsers.
        await self._send_to_runners(self._webhook_config_payload())

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
            "timeframe": self.spec.timeframe,
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
            "runner_connected": self.runner_count > 0,
            "last_bar_time": feed.last_bar_time(),
        }
