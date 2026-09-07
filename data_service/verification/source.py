from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import struct
import time
from types import MethodType
from typing import Any, Callable

import ccxt.async_support as ccxt_async
import ccxt.pro as ccxt_pro

from exchange_clock import release_exchange_clock, retain_exchange_clock
from market_data_diagnostics import ohlcv_bar_data
from ohlcv_io import make_ccxt_pro_client
from prerun_scheduler import timeframe_seconds


_MAX_PENDING_CANDLES = 128
_REST_STABILIZED_EXCHANGES = frozenset({"bitget", "hyperliquid"})
_REST_FINALIZED_DELAYS_SECONDS = (1.0, 2.0, 3.0, 5.0, 8.0, 11.0)
_REST_FINALIZED_DEGRADED_RETRY_SECONDS = 5.0
_REST_FINALIZED_MIN_SAMPLE_INTERVAL_MS = 500
_REST_FINALIZED_MAX_CONSECUTIVE_ERRORS = 3
_REST_FINALIZED_LOCKS = {
    exchange_name: asyncio.Lock()
    for exchange_name in _REST_STABILIZED_EXCHANGES
}


@dataclass
class _ReconnectTrace:
    trace_id: int
    candle_timestamp_ms: int
    boundary_timestamp_ms: int
    last_disconnect_monotonic_ns: int
    disconnect_count: int = 1


async def _safe_close(exchange: Any) -> None:
    try:
        await exchange.close()
    except Exception:
        pass


def _confirmed(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return str(value).strip().lower() in {"1", "true"}


def _normalize_bar(bar: Any) -> dict[str, int | float] | None:
    if isinstance(bar, dict) and "timestamp_ms" in bar:
        try:
            return {
                "timestamp_ms": int(bar["timestamp_ms"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
    return ohlcv_bar_data(bar)


def _float32_bar(
    bar: dict[str, int | float] | None,
) -> dict[str, int | float] | None:
    if bar is None:
        return None

    def as_float32(value: int | float) -> float:
        return struct.unpack("f", struct.pack("f", float(value)))[0]

    return {
        "timestamp_ms": int(bar["timestamp_ms"]),
        "open": as_float32(bar["open"]),
        "high": as_float32(bar["high"]),
        "low": as_float32(bar["low"]),
        "close": as_float32(bar["close"]),
        "volume": as_float32(bar["volume"]),
    }


class FinalizedCandleProbe:
    """Produce verifier inputs from exchange-finalized candles.

    Explicit-finalization exchanges use a read-only WebSocket client. Bitget
    and Hyperliquid use a separate REST client and repeated closed-candle
    stability checks. Neither source mutates live bars, files, caches, or
    primary runner events.
    """

    def __init__(
        self,
        *,
        exchange_name: str,
        symbol: str,
        timeframe: str,
        market_type: str,
        on_event: Callable[[dict[str, Any]], None],
        on_authoritative: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.exchange_name = exchange_name.lower()
        self.symbol = symbol
        self.timeframe = timeframe
        self.market_type = market_type
        self.on_event = on_event
        self.on_authoritative = on_authoritative
        self._timeframe_ms = timeframe_seconds(timeframe) * 1000
        self._finalized_bars: dict[int, dict[str, int | float]] = {}
        self._published_timestamps: set[int] = set()
        self._inferred_latest_bar: dict[str, int | float] | None = None
        self._connection_attempt = 0
        self._awaiting_first_message = False
        self._pending_reconnect_trace: _ReconnectTrace | None = None
        self._disconnect_trace_sequence = 0
        self._primary_volumes: dict[int, float | None] = {}
        self._primary_result_changed = asyncio.Event()

    @property
    def task_name(self) -> str:
        if self.exchange_name in _REST_STABILIZED_EXCHANGES:
            return f"{self.exchange_name}_rest_finalized_candle_probe"
        return "watch_ohlcv_finalized_candle_probe"

    async def run(self) -> None:
        if self.exchange_name in _REST_STABILIZED_EXCHANGES:
            await self.run_rest_stabilized_probe()
            return
        await self.run_watch_ohlcv_probe()

    @property
    def _rest_source_name(self) -> str:
        return f"{self.exchange_name}_rest_stabilized"

    def _rest_event_name(self, suffix: str) -> str:
        return f"{self.exchange_name}_rest_finalized_{suffix}"

    def record_primary_result(self, event: dict[str, Any]) -> None:
        try:
            timestamp_ms = int(event["candle_timestamp_ms"])
        except (KeyError, TypeError, ValueError):
            return

        confirmed_bar = event.get("confirmed_bar")
        volume: float | None = None
        if (
            event.get("result_status") != "not_calculated"
            and isinstance(confirmed_bar, (list, tuple))
            and len(confirmed_bar) >= 6
        ):
            try:
                candidate = float(confirmed_bar[5])
            except (TypeError, ValueError, OverflowError):
                candidate = math.nan
            if math.isfinite(candidate) and candidate >= 0.0:
                volume = candidate

        previous = self._primary_volumes.get(timestamp_ms)
        if volume is not None:
            self._primary_volumes[timestamp_ms] = max(
                volume,
                previous if previous is not None else volume,
            )
        elif timestamp_ms not in self._primary_volumes:
            self._primary_volumes[timestamp_ms] = None
        while len(self._primary_volumes) > _MAX_PENDING_CANDLES:
            self._primary_volumes.pop(min(self._primary_volumes))
        self._primary_result_changed.set()

    async def _wait_for_primary_volume(
        self,
        target_timestamp_ms: int,
    ) -> float | None:
        while True:
            if target_timestamp_ms in self._primary_volumes:
                return self._primary_volumes[target_timestamp_ms]
            if any(
                timestamp_ms > target_timestamp_ms
                for timestamp_ms in self._primary_volumes
            ):
                return None
            self._primary_result_changed.clear()
            if (
                target_timestamp_ms in self._primary_volumes
                or any(
                    timestamp_ms > target_timestamp_ms
                    for timestamp_ms in self._primary_volumes
                )
            ):
                continue
            await self._primary_result_changed.wait()

    def _publish_authoritative(
        self,
        timestamp_ms: int,
        *,
        source: str = "watch_ohlcv_confirmed",
    ) -> None:
        if (
            self.on_authoritative is None
            or timestamp_ms in self._published_timestamps
        ):
            return
        finalized = self._finalized_bars.get(timestamp_ms)
        if finalized is None:
            return
        new_bar = self._fake_new_bar(finalized)
        self._published_timestamps.add(timestamp_ms)
        try:
            self.on_authoritative({
                "type": "verification_run_ready",
                "authoritative_source": source,
                "confirmed_bar": self._raw_bar(finalized),
                "new_bar": self._raw_bar(new_bar),
                "candle_timestamp_ms": timestamp_ms,
            })
        except Exception:
            pass

    def _fake_new_bar(
        self,
        finalized: dict[str, int | float],
    ) -> dict[str, int | float]:
        close = float(finalized["close"])
        return {
            "timestamp_ms": int(finalized["timestamp_ms"]) + self._timeframe_ms,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 0.0,
        }

    @staticmethod
    def _raw_bar(bar: dict[str, int | float]) -> list[int | float]:
        return [
            int(bar["timestamp_ms"]),
            float(bar["open"]),
            float(bar["high"]),
            float(bar["low"]),
            float(bar["close"]),
            float(bar["volume"]),
        ]

    def _trim(self) -> None:
        timestamps = set(self._finalized_bars)
        if len(timestamps) <= _MAX_PENDING_CANDLES:
            return
        for timestamp_ms in sorted(timestamps)[:-_MAX_PENDING_CANDLES]:
            self._finalized_bars.pop(timestamp_ms, None)
            self._published_timestamps.discard(timestamp_ms)

    async def run_rest_stabilized_probe(self) -> None:
        if self.exchange_name not in _REST_STABILIZED_EXCHANGES:
            raise RuntimeError(
                "the stabilized REST probe is only available for "
                "Bitget and Hyperliquid"
            )

        clock = retain_exchange_clock(self.exchange_name)
        rest_lock = _REST_FINALIZED_LOCKS[self.exchange_name]
        source_name = self._rest_source_name
        exchange: Any | None = None
        target_timestamp_ms: int | None = None
        reconnect_delay_seconds = 1.0
        try:
            while True:
                try:
                    exchange = make_ccxt_pro_client(
                        ccxt_async,
                        self.exchange_name,
                        market_type=self.market_type,
                        symbol=self.symbol,
                    )
                    async with rest_lock:
                        await exchange.load_markets()
                    if target_timestamp_ms is None:
                        now_ms = int(await clock.now_ms())
                        target_timestamp_ms = (
                            now_ms // self._timeframe_ms
                        ) * self._timeframe_ms
                    self._emit_event({
                        "event": self._rest_event_name("probe_started"),
                        "source": source_name,
                        "next_candle_timestamp_ms": target_timestamp_ms,
                    })
                    reconnect_delay_seconds = 1.0
                    while True:
                        primary_volume = await self._wait_for_primary_volume(
                            target_timestamp_ms
                        )
                        if primary_volume is None:
                            self._emit_event({
                                "event": self._rest_event_name("skipped"),
                                "source": source_name,
                                "candle_timestamp_ms": target_timestamp_ms,
                                "reason": "primary candle was not calculated",
                            })
                            target_timestamp_ms += self._timeframe_ms
                            continue
                        await self._stabilize_rest_candle(
                            exchange,
                            clock,
                            rest_lock,
                            target_timestamp_ms,
                            primary_volume,
                        )
                        target_timestamp_ms += self._timeframe_ms
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._emit_probe_error(source_name, error)
                    await _safe_close(exchange)
                    exchange = None
                    await asyncio.sleep(reconnect_delay_seconds)
                    reconnect_delay_seconds = min(
                        reconnect_delay_seconds * 2.0,
                        30.0,
                    )
        finally:
            await _safe_close(exchange)
            await release_exchange_clock(self.exchange_name)

    async def _stabilize_rest_candle(
        self,
        exchange: Any,
        clock: Any,
        rest_lock: asyncio.Lock,
        target_timestamp_ms: int,
        primary_volume: float,
    ) -> None:
        boundary_timestamp_ms = target_timestamp_ms + self._timeframe_ms
        source_name = self._rest_source_name
        previous_bar: dict[str, int | float] | None = None
        previous_sample_completed_ms: int | None = None
        attempt = 0
        degraded = False
        consecutive_errors = 0

        while True:
            if attempt < len(_REST_FINALIZED_DELAYS_SECONDS):
                delay_seconds = _REST_FINALIZED_DELAYS_SECONDS[attempt]
            else:
                delay_seconds = (
                    _REST_FINALIZED_DELAYS_SECONDS[-1]
                    + (attempt - len(_REST_FINALIZED_DELAYS_SECONDS) + 1)
                    * _REST_FINALIZED_DEGRADED_RETRY_SECONDS
                )
            requested_at_ms = boundary_timestamp_ms + int(delay_seconds * 1000)
            if previous_sample_completed_ms is not None:
                requested_at_ms = max(
                    requested_at_ms,
                    previous_sample_completed_ms
                    + _REST_FINALIZED_MIN_SAMPLE_INTERVAL_MS,
                )
            await self._sleep_until_exchange_time(clock, requested_at_ms)

            started = time.monotonic()
            try:
                async with rest_lock:
                    rows = await exchange.fetch_ohlcv(
                        self.symbol,
                        self.timeframe,
                        since=target_timestamp_ms,
                        limit=3,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                previous_bar = None
                previous_sample_completed_ms = None
                consecutive_errors += 1
                self._emit_event({
                    "event": self._rest_event_name("probe_error"),
                    "source": source_name,
                    "candle_timestamp_ms": target_timestamp_ms,
                    "boundary_timestamp_ms": boundary_timestamp_ms,
                    "attempt": attempt + 1,
                    "requested_boundary_delay_ms": int(delay_seconds * 1000),
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:500],
                    "consecutive_errors": consecutive_errors,
                })
                attempt += 1
                if (
                    not degraded
                    and delay_seconds >= _REST_FINALIZED_DELAYS_SECONDS[-1]
                ):
                    degraded = True
                    self._emit_rest_pending(
                        target_timestamp_ms,
                        boundary_timestamp_ms,
                        reason="REST request failed before the candle stabilized",
                    )
                if consecutive_errors >= _REST_FINALIZED_MAX_CONSECUTIVE_ERRORS:
                    raise RuntimeError(
                        f"{self.exchange_name} finalized-candle REST client failed "
                        f"{consecutive_errors} consecutive requests"
                    ) from error
                continue

            completed_at_ms = await clock.now_ms()
            consecutive_errors = 0
            normalized = [
                bar
                for bar in (
                    _float32_bar(_normalize_bar(row)) for row in rows or []
                )
                if bar is not None
            ]
            target_bar = next(
                (
                    bar
                    for bar in normalized
                    if int(bar["timestamp_ms"]) == target_timestamp_ms
                ),
                None,
            )
            newer_candle_present = any(
                int(bar["timestamp_ms"]) >= boundary_timestamp_ms
                for bar in normalized
            )
            stable = (
                target_bar is not None
                and newer_candle_present
                and self._bars_match(previous_bar, target_bar)
            )
            rest_volume = (
                float(target_bar["volume"])
                if target_bar is not None
                else None
            )
            latest_primary_volume = self._primary_volumes.get(target_timestamp_ms)
            if latest_primary_volume is not None:
                primary_volume = max(primary_volume, latest_primary_volume)
            volume_covers_primary = (
                rest_volume is not None and rest_volume >= primary_volume
            )
            self._emit_event({
                "event": self._rest_event_name("sample"),
                "source": source_name,
                "candle_timestamp_ms": target_timestamp_ms,
                "boundary_timestamp_ms": boundary_timestamp_ms,
                "attempt": attempt + 1,
                "requested_boundary_delay_ms": int(delay_seconds * 1000),
                "completed_boundary_delay_ms": completed_at_ms - boundary_timestamp_ms,
                "request_elapsed_ms": (time.monotonic() - started) * 1000,
                "response_timestamps_ms": [
                    int(bar["timestamp_ms"]) for bar in normalized
                ],
                "newer_candle_present": newer_candle_present,
                "matches_previous_sample": stable,
                "primary_volume": primary_volume,
                "rest_volume_covers_primary": volume_covers_primary,
                "bar": target_bar,
            })

            if stable and volume_covers_primary and target_bar is not None:
                self._record_rest_finalized(target_bar, source=source_name)
                self._emit_event({
                    "event": self._rest_event_name("accepted"),
                    "source": source_name,
                    "candle_timestamp_ms": target_timestamp_ms,
                    "boundary_timestamp_ms": boundary_timestamp_ms,
                    "accepted_boundary_delay_ms": completed_at_ms - boundary_timestamp_ms,
                    "attempts": attempt + 1,
                    "recovered_from_pending": degraded,
                    "primary_volume": primary_volume,
                    "bar": target_bar,
                })
                return

            previous_bar = (
                target_bar
                if target_bar is not None and newer_candle_present
                else None
            )
            previous_sample_completed_ms = (
                completed_at_ms if previous_bar is not None else None
            )
            attempt += 1
            if (
                not degraded
                and delay_seconds >= _REST_FINALIZED_DELAYS_SECONDS[-1]
            ):
                degraded = True
                reason = "REST candle did not stabilize by the initial deadline"
                if stable and not volume_covers_primary:
                    reason = "REST candle volume remained below Primary volume"
                self._emit_rest_pending(
                    target_timestamp_ms,
                    boundary_timestamp_ms,
                    reason=reason,
                )

    @staticmethod
    async def _sleep_until_exchange_time(clock: Any, target_ms: int) -> None:
        while True:
            remaining_seconds = (target_ms - await clock.now_ms()) / 1000
            if remaining_seconds <= 0.0:
                return
            await asyncio.sleep(remaining_seconds)

    def _emit_rest_pending(
        self,
        candle_timestamp_ms: int,
        boundary_timestamp_ms: int,
        *,
        reason: str,
    ) -> None:
        self._emit_event({
            "event": self._rest_event_name("pending"),
            "source": self._rest_source_name,
            "candle_timestamp_ms": candle_timestamp_ms,
            "boundary_timestamp_ms": boundary_timestamp_ms,
            "reason": reason,
        })

    def _record_rest_finalized(
        self,
        bar: dict[str, int | float],
        *,
        source: str,
    ) -> None:
        timestamp_ms = int(bar["timestamp_ms"])
        if timestamp_ms in self._published_timestamps:
            return
        self._finalized_bars[timestamp_ms] = bar
        self._publish_authoritative(
            timestamp_ms,
            source=source,
        )
        self._trim()

    async def run_watch_ohlcv_probe(self) -> None:
        if self.exchange_name in _REST_STABILIZED_EXCHANGES:
            raise RuntimeError(
                f"{self.exchange_name} verification must use stabilized REST OHLCV"
            )
        while True:
            exchange = make_ccxt_pro_client(
                ccxt_pro,
                self.exchange_name,
                market_type=self.market_type,
                symbol=self.symbol,
            )
            self._connection_attempt += 1
            attempt = self._connection_attempt
            self._awaiting_first_message = True
            self._emit_event({
                "event": "finalized_candle_probe_connection_attempt",
                "source": "watch_ohlcv_confirmed",
                "connection_attempt": attempt,
                "reconnecting": self._pending_reconnect_trace is not None,
                "trace_id": (
                    self._pending_reconnect_trace.trace_id
                    if self._pending_reconnect_trace is not None
                    else None
                ),
            })
            try:
                self._install_raw_ohlcv_observer(exchange, attempt)
                self._inferred_latest_bar = None
                while True:
                    rows = await exchange.watch_ohlcv(
                        self.symbol,
                        self.timeframe,
                        None,
                        None,
                        {},
                    )
                    self._observe_inferred_confirmation(rows)
            except asyncio.CancelledError:
                await _safe_close(exchange)
                raise
            except Exception as error:
                self._begin_disconnect_trace()
                self._emit_probe_error("watch_ohlcv_confirmed", error)
                await _safe_close(exchange)
                await asyncio.sleep(1.0)

    def _install_raw_ohlcv_observer(self, exchange: Any, attempt: int) -> None:
        original_handler = exchange.handle_ohlcv

        def handle_ohlcv(instance: Any, client: Any, message: Any) -> Any:
            try:
                self._observe_raw_ohlcv_message(instance, message, attempt)
                for bar in self._explicit_finalized_bars(instance, message):
                    self._record_ws_finalized(bar)
            except Exception as error:
                self._emit_probe_error("watch_ohlcv_raw_handler", error)
            return original_handler(client, message)

        exchange.handle_ohlcv = MethodType(handle_ohlcv, exchange)

    def _observe_raw_ohlcv_message(
        self,
        exchange: Any,
        message: Any,
        attempt: int,
    ) -> None:
        if self._awaiting_first_message:
            self._awaiting_first_message = False
            trace = self._pending_reconnect_trace
            downtime_ms = None
            if trace is not None:
                downtime_ms = (
                    time.monotonic_ns() - trace.last_disconnect_monotonic_ns
                ) / 1_000_000
            self._emit_event({
                "event": "finalized_candle_probe_first_message",
                "source": "watch_ohlcv_confirmed",
                "connection_attempt": attempt,
                "reconnected": trace is not None,
                "trace_id": trace.trace_id if trace is not None else None,
                "candle_timestamp_ms": (
                    trace.candle_timestamp_ms if trace is not None else None
                ),
                "disconnect_to_first_message_ms": downtime_ms,
                "server_timestamp_ms": self._message_timestamp_ms(message),
                "action": message.get("action") if isinstance(message, dict) else None,
            })
            self._pending_reconnect_trace = None

    @staticmethod
    def _message_timestamp_ms(message: Any) -> int | None:
        if not isinstance(message, dict):
            return None
        try:
            return int(message.get("ts"))
        except (TypeError, ValueError):
            return None

    def _begin_disconnect_trace(self) -> None:
        now_ms = int(time.time() * 1000)
        now_monotonic_ns = time.monotonic_ns()
        candle_timestamp_ms = (now_ms // self._timeframe_ms) * self._timeframe_ms
        self._disconnect_trace_sequence += 1
        trace = _ReconnectTrace(
            trace_id=self._disconnect_trace_sequence,
            candle_timestamp_ms=candle_timestamp_ms,
            boundary_timestamp_ms=candle_timestamp_ms + self._timeframe_ms,
            last_disconnect_monotonic_ns=now_monotonic_ns,
        )
        self._pending_reconnect_trace = trace
        self._emit_event({
            "event": "finalized_candle_probe_disconnected",
            "source": "watch_ohlcv_confirmed",
            "trace_id": trace.trace_id,
            "candle_timestamp_ms": trace.candle_timestamp_ms,
            "boundary_timestamp_ms": trace.boundary_timestamp_ms,
            "boundary_in_ms": trace.boundary_timestamp_ms - now_ms,
            "disconnect_count": trace.disconnect_count,
        })

    @staticmethod
    def _bars_match(
        left: dict[str, int | float] | None,
        right: dict[str, int | float] | None,
    ) -> bool:
        if left is None or right is None:
            return False
        if int(left["timestamp_ms"]) != int(right["timestamp_ms"]):
            return False
        return all(
            math.isclose(
                float(left[field_name]),
                float(right[field_name]),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for field_name in ("open", "high", "low", "close", "volume")
        )

    def _explicit_finalized_bars(
        self,
        exchange: Any,
        message: Any,
    ) -> list[list[Any]]:
        if not isinstance(message, dict):
            return []
        if self.exchange_name == "binance":
            kline = message.get("k")
            if not isinstance(kline, dict) or not _confirmed(kline.get("x")):
                return []
            return [[
                exchange.safe_integer(kline, "t"),
                exchange.safe_float(kline, "o"),
                exchange.safe_float(kline, "h"),
                exchange.safe_float(kline, "l"),
                exchange.safe_float(kline, "c"),
                exchange.safe_float(kline, "v"),
            ]]

        if self.exchange_name == "okx":
            arg = message.get("arg") or {}
            market_id = arg.get("instId")
            market = exchange.safe_market(market_id)
            finalized: list[list[Any]] = []
            for row in message.get("data") or []:
                raw_confirm = row[8] if isinstance(row, list) and len(row) > 8 else None
                if _confirmed(raw_confirm):
                    finalized.append(exchange.parse_ohlcv(row, market))
            return finalized

        if self.exchange_name == "bybit":
            market = exchange.market(self.symbol)
            finalized = []
            for row in message.get("data") or []:
                if not isinstance(row, dict) or not _confirmed(row.get("confirm")):
                    continue
                finalized.append(exchange.parse_ws_ohlcv(row, market))
            return finalized

        return []

    def _observe_inferred_confirmation(self, rows: Any) -> None:
        normalized = [
            bar
            for bar in (_normalize_bar(row) for row in rows or [])
            if bar is not None
        ]
        if not normalized:
            return
        latest = max(normalized, key=lambda item: int(item["timestamp_ms"]))
        previous = self._inferred_latest_bar
        self._inferred_latest_bar = latest
        if previous is None:
            return
        if int(latest["timestamp_ms"]) <= int(previous["timestamp_ms"]):
            return
        previous_timestamp_ms = int(previous["timestamp_ms"])
        finalized = next(
            (
                bar
                for bar in normalized
                if int(bar["timestamp_ms"]) == previous_timestamp_ms
            ),
            previous,
        )
        self._record_ws_finalized(finalized)

    def _record_ws_finalized(self, bar: Any) -> None:
        normalized = _normalize_bar(bar)
        if normalized is None:
            return
        timestamp_ms = int(normalized["timestamp_ms"])
        if timestamp_ms in self._finalized_bars:
            return
        self._finalized_bars[timestamp_ms] = normalized
        self._publish_authoritative(timestamp_ms)
        self._trim()

    def _emit_event(self, event: dict[str, Any]) -> None:
        try:
            self.on_event(event)
        except Exception:
            pass

    def _emit_probe_error(self, source: str, error: Exception) -> None:
        self._emit_event({
            "event": "finalized_candle_probe_error",
            "source": source,
            "error_type": type(error).__name__,
            "error_message": str(error)[:500],
        })
