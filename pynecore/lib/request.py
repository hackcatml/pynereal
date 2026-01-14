from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any, Callable
import sys
import os

from ..core.resampler import Resampler
from ..core.series import SeriesImpl
from ..types.na import NA
from . import barmerge
from . import timeframe as timeframe_module


@dataclass
class _TimeframeCache:
    resampler: Resampler
    bars: list[dict] = field(default_factory=list)
    base_to_high: list[int] = field(default_factory=list)
    is_closed: list[bool] = field(default_factory=list)
    series_map: dict[str, SeriesImpl] = field(default_factory=dict)
    series_fields: dict[str, str] = field(default_factory=dict)
    last_synced_index: int = -1
    last_bar_time: int | None = None


@dataclass
class _ExprCache:
    last_committed_index: int = -1
    last_value: Any = NA()


@dataclass
class _PersistentModule:
    module: Any
    keys: list[str]


class SecurityContext:
    def __init__(self, script_module, lib_module):
        self._script_module = script_module
        self._lib = lib_module
        self._base_bars: list[Any] = []
        self._base_bar_index: int = -1
        self._cache: dict[str, _TimeframeCache] = {}
        self._expr_cache: dict[tuple[str, Any], _ExprCache] = {}
        self._persistent_modules: list[_PersistentModule] = []
        self._collect_persistent_modules()

    def update_base_bar(self, candle: Any, bar_index: int) -> None:
        if bar_index == len(self._base_bars):
            self._base_bars.append(candle)
        elif bar_index == len(self._base_bars) - 1:
            self._base_bars[bar_index] = candle
        self._base_bar_index = bar_index

    def _collect_persistent_modules(self) -> None:
        modules = []
        for module in sys.modules.values():
            if module is None or not hasattr(module, "__dict__"):
                continue
            keys = [k for k in module.__dict__.keys() if k.startswith("__persistent_")]
            if keys:
                modules.append(_PersistentModule(module=module, keys=keys))
        self._persistent_modules = modules

    def prefill_base_bars(self, bars: list[Any]) -> None:
        # Preload base bars for backtests without consuming a live iterator.
        if not bars:
            return
        self._base_bars = bars

    def _snapshot_persistents(self) -> dict[Any, dict[str, Any]]:
        snapshot: dict[Any, dict[str, Any]] = {}
        for entry in self._persistent_modules:
            module_snapshot = {}
            for key in entry.keys:
                module_snapshot[key] = getattr(entry.module, key)
            snapshot[entry.module] = module_snapshot
        return snapshot

    @staticmethod
    def _restore_persistents(snapshot: dict[Any, dict[str, Any]]) -> None:
        for module, values in snapshot.items():
            for key, value in values.items():
                setattr(module, key, value)

    def _create_cache(self, timeframe: str) -> _TimeframeCache:
        cache = _TimeframeCache(resampler=Resampler.get_resampler(timeframe))
        for name in dir(self._script_module):
            if not name.startswith("__series_"):
                continue
            if "·__lib·" not in name:
                continue
            field = None
            for candidate in ("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"):
                if f"·__lib·{candidate}__" in name:
                    field = candidate
                    break
            if field:
                cache.series_map[name] = SeriesImpl()
                cache.series_fields[name] = field

        return cache

    def _update_cache(self, cache: _TimeframeCache) -> None:
        if len(cache.base_to_high) >= len(self._base_bars):
            return
        for idx in range(len(cache.base_to_high), len(self._base_bars)):
            candle = self._base_bars[idx]
            bar_time_ms = cache.resampler.get_bar_time(int(candle.timestamp * 1000))
            bar_time_sec = int(bar_time_ms // 1000)
            if cache.last_bar_time is None or bar_time_sec != cache.last_bar_time:
                bar = {
                    "time": bar_time_sec,
                    "open": float(candle.open),
                    "high": float(candle.high),
                    "low": float(candle.low),
                    "close": float(candle.close),
                    "volume": float(candle.volume),
                }
                cache.bars.append(bar)
                cache.last_bar_time = bar_time_sec
            else:
                bar = cache.bars[-1]
                bar["high"] = max(bar["high"], float(candle.high))
                bar["low"] = min(bar["low"], float(candle.low))
                bar["close"] = float(candle.close)
                bar["volume"] += float(candle.volume)

            cache.base_to_high.append(len(cache.bars) - 1)
            if idx > 0:
                cache.is_closed[idx - 1] = cache.base_to_high[idx - 1] != cache.base_to_high[idx]
            cache.is_closed.append(False)

    def _get_cache(self, timeframe: str) -> _TimeframeCache:
        cache = self._cache.get(timeframe)
        if cache is None:
            cache = self._create_cache(timeframe)
            self._cache[timeframe] = cache
        self._update_cache(cache)
        return cache

    def _bar_values(self, bar: dict) -> dict[str, float]:
        open_val = bar["open"]
        high_val = bar["high"]
        low_val = bar["low"]
        close_val = bar["close"]
        return {
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "volume": bar["volume"],
            "hl2": (high_val + low_val) / 2.0,
            "hlc3": (high_val + low_val + close_val) / 3.0,
            "ohlc4": (open_val + high_val + low_val + close_val) / 4.0,
            "hlcc4": (high_val + low_val + 2 * close_val) / 4.0,
        }

    def _sync_series(self, cache: _TimeframeCache, high_index: int) -> None:
        if high_index < 0:
            return
        old_bar_index = self._lib.bar_index
        old_last_bar_index = self._lib.last_bar_index
        for i in range(cache.last_synced_index + 1, high_index + 1):
            bar = cache.bars[i]
            values = self._bar_values(bar)
            self._lib.bar_index = i
            self._lib.last_bar_index = i
            for series_name, series_obj in cache.series_map.items():
                field = cache.series_fields[series_name]
                series_obj.add(values[field])
        if high_index <= cache.last_synced_index:
            bar = cache.bars[high_index]
            values = self._bar_values(bar)
            self._lib.bar_index = high_index
            self._lib.last_bar_index = high_index
            for series_name, series_obj in cache.series_map.items():
                field = cache.series_fields[series_name]
                series_obj.set(values[field])
        self._lib.bar_index = old_bar_index
        self._lib.last_bar_index = old_last_bar_index
        cache.last_synced_index = max(cache.last_synced_index, high_index)

    def _build_series_snapshot(self, cache: _TimeframeCache, high_index: int) -> dict[str, SeriesImpl]:
        # Create an isolated series buffer up to high_index to avoid leaking newer HTF bars.
        temp_series_map: dict[str, SeriesImpl] = {
            name: SeriesImpl() for name in cache.series_map.keys()
        }
        if high_index < 0:
            return temp_series_map

        old_bar_index = self._lib.bar_index
        old_last_bar_index = self._lib.last_bar_index
        try:
            for i in range(high_index + 1):
                bar = cache.bars[i]
                values = self._bar_values(bar)
                self._lib.bar_index = i
                self._lib.last_bar_index = i
                for series_name, series_obj in temp_series_map.items():
                    field = cache.series_fields[series_name]
                    series_obj.add(values[field])
        finally:
            self._lib.bar_index = old_bar_index
            self._lib.last_bar_index = old_last_bar_index

        return temp_series_map

    def evaluate(self, timeframe: str, expr: Callable[[], Any], lookahead) -> Any:
        debug_enabled = os.environ.get("PYNE_DEBUG_REQUEST_SECURITY") == "1"
        if not self._base_bars or self._base_bar_index < 0:
            return NA()
        cache = self._get_cache(timeframe)
        if self._base_bar_index >= len(cache.base_to_high):
            return NA()
        high_index = cache.base_to_high[self._base_bar_index]
        base_tf_ms = None
        requested_tf_ms = None
        force_snapshot = False
        if lookahead != barmerge.lookahead_on:
            # For HTF lookahead_off, move to the last confirmed HTF bar.
            if self._base_bar_index > 0:
                base_time_ms = int(self._base_bars[self._base_bar_index].timestamp * 1000)
                prev_time_ms = int(self._base_bars[self._base_bar_index - 1].timestamp * 1000)
                delta_ms = base_time_ms - prev_time_ms
                if delta_ms > 0:
                    base_tf_ms = delta_ms

            if base_tf_ms is None:
                try:
                    base_tf_ms = timeframe_module.in_seconds(self._lib.syminfo.period) * 1000
                except Exception:  # noqa: BLE001
                    base_tf_ms = None

            try:
                requested_tf_ms = timeframe_module.in_seconds(timeframe) * 1000
            except Exception:  # noqa: BLE001
                requested_tf_ms = None

            if requested_tf_ms is None or base_tf_ms is None:
                high_index -= 1
            elif requested_tf_ms > base_tf_ms:
                # Avoid mixing partially built HTF bars into series buffers.
                high_index -= 1
                force_snapshot = True

            if debug_enabled:
                log_mod = getattr(self._lib, "log", None)
                msg = (
                    f"[request.security] tf={timeframe} lookahead={lookahead} "
                    f"base_idx={self._base_bar_index} high_idx={high_index} "
                    f"base_time_ms={base_time_ms if 'base_time_ms' in locals() else None} "
                    f"prev_time_ms={prev_time_ms if 'prev_time_ms' in locals() else None} "
                    f"base_tf_ms={base_tf_ms} requested_tf_ms={requested_tf_ms}"
                )
                if log_mod and hasattr(log_mod, "info"):
                    log_mod.info(msg)
                else:
                    print(msg)
        elif debug_enabled:
            log_mod = getattr(self._lib, "log", None)
            msg = (
                f"[request.security] tf={timeframe} lookahead={lookahead} "
                f"base_idx={self._base_bar_index} high_idx={high_index}"
            )
            if log_mod and hasattr(log_mod, "info"):
                log_mod.info(msg)
            else:
                print(msg)
        if high_index < 0:
            return NA()
        # Cache by expression code so lookahead_on can reuse committed values.
        expr_key = (timeframe, expr.__code__)
        expr_cache = self._expr_cache.get(expr_key)
        if expr_cache is None:
            expr_cache = _ExprCache()
            self._expr_cache[expr_key] = expr_cache
        should_commit = (high_index != expr_cache.last_committed_index)
        # Reuse the last committed value within the same HTF bar for lookahead_on.
        if lookahead == barmerge.lookahead_on and not should_commit:
            return expr_cache.last_value

        # Use a snapshot when we would otherwise reuse a longer series buffer.
        use_snapshot_series = force_snapshot or high_index < cache.last_synced_index
        if not use_snapshot_series:
            self._sync_series(cache, high_index)
            series_map = cache.series_map
        else:
            series_map = self._build_series_snapshot(cache, high_index)

        bar = cache.bars[high_index]
        values = self._bar_values(bar)
        if debug_enabled:
            log_mod = getattr(self._lib, "log", None)
            bar_time = datetime.fromtimestamp(bar["time"], UTC).isoformat()
            prev_time = None
            if high_index > 0:
                prev_time = datetime.fromtimestamp(cache.bars[high_index - 1]["time"], UTC).isoformat()
            prev_close = None
            if high_index > 0:
                prev_close = cache.bars[high_index - 1]["close"]
            msg = (
                f"[request.security] tf={timeframe} high_idx_time={bar_time} "
                f"prev_high_idx_time={prev_time} bars_len={len(cache.bars)} "
                f"close={bar['close']} prev_close={prev_close} "
                f"last_synced={cache.last_synced_index} use_snapshot={use_snapshot_series} "
                f"force_snapshot={force_snapshot} base_tf_ms={base_tf_ms} requested_tf_ms={requested_tf_ms}"
            )
            if log_mod and hasattr(log_mod, "info"):
                log_mod.info(msg)
            else:
                print(msg)

        original_series = {}
        for name, series_obj in series_map.items():
            if hasattr(self._script_module, name):
                original_series[name] = getattr(self._script_module, name)
            setattr(self._script_module, name, series_obj)
            if debug_enabled and "__lib·close__" in name:
                log_mod = getattr(self._lib, "log", None)
                try:
                    sample = [series_obj[i] for i in range(4)]
                except Exception:  # noqa: BLE001
                    sample = []
                msg = (
                    f"[request.security] tf={timeframe} series={name} "
                    f"len={len(series_obj)} sample0-3={sample}"
                )
                if log_mod and hasattr(log_mod, "info"):
                    log_mod.info(msg)
                else:
                    print(msg)

        original_values = {
            "open": self._lib.open,
            "high": self._lib.high,
            "low": self._lib.low,
            "close": self._lib.close,
            "volume": self._lib.volume,
            "hl2": self._lib.hl2,
            "hlc3": self._lib.hlc3,
            "ohlc4": self._lib.ohlc4,
            "hlcc4": self._lib.hlcc4,
            "bar_index": self._lib.bar_index,
            "last_bar_index": self._lib.last_bar_index,
            "_time": self._lib._time,
            "last_bar_time": self._lib.last_bar_time,
            "_datetime": self._lib._datetime,
        }

        # For lookahead_off, re-use the last confirmed HTF value within the same HTF bar.
        if lookahead != barmerge.lookahead_on and not should_commit:
            return expr_cache.last_value
        snapshot = None
        # Protect persistent state when re-evaluating within the same HTF bar.
        if lookahead == barmerge.lookahead_on and not should_commit:
            snapshot = self._snapshot_persistents()
        try:
            self._lib.open = values["open"]
            self._lib.high = values["high"]
            self._lib.low = values["low"]
            self._lib.close = values["close"]
            self._lib.volume = values["volume"]
            self._lib.hl2 = values["hl2"]
            self._lib.hlc3 = values["hlc3"]
            self._lib.ohlc4 = values["ohlc4"]
            self._lib.hlcc4 = values["hlcc4"]
            self._lib.bar_index = high_index
            self._lib.last_bar_index = high_index
            bar_time_ms = int(bar["time"] * 1000)
            self._lib._time = bar_time_ms
            self._lib.last_bar_time = bar_time_ms
            self._lib._datetime = datetime.fromtimestamp(bar["time"], UTC)
            result = expr()
            if should_commit:
                expr_cache.last_committed_index = high_index
            expr_cache.last_value = result
            return result
        finally:
            if snapshot is not None:
                # Restore persistent state to avoid side effects in lookahead_on.
                self._restore_persistents(snapshot)
            for name, original in original_series.items():
                setattr(self._script_module, name, original)
            for key, value in original_values.items():
                setattr(self._lib, key, value)


def security(symbol: str, timeframe: str, expression: Callable[[], Any] | Any,
             lookahead=barmerge.lookahead_off):
    from .. import lib
    ctx: SecurityContext | None = getattr(lib, "_security_ctx", None)
    if ctx is None:
        return expression() if callable(expression) else expression
    if not callable(expression):
        return expression
    return ctx.evaluate(timeframe, expression, lookahead)
