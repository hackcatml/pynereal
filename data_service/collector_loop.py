from __future__ import annotations

import asyncio
from typing import Callable, Optional, Awaitable

import ccxt.pro as ccxt

from state import DataState
from ohlcv_io import convert_timeframe, make_ccxt_pro_client
from exchange_clock import release_exchange_clock, retain_exchange_clock


async def _safe_close(ex) -> None:
    try:
        await ex.close()
    except Exception:
        pass


def _advance_trade_window(ex, state: DataState, since: int) -> int:
    """Keep the OHLCV trade buffer aligned with the latest live candle."""
    if not state.live_bars:
        return since

    active_since = max(since, int(state.live_bars[-1][0]))
    if active_since == since:
        return since

    state.collected_trades = ex.filter_by_since_limit(
        state.collected_trades,
        active_since,
    )
    return active_since


async def watch_trades_loop(
    exchange_name: str,
    symbol: str,
    timeframe: str,
    state: DataState,
    on_bar: Callable[[list], Awaitable[None]],
    on_trades: Callable[[list], None] | None = None,
    market_type: str = "",
) -> None:
    ex = make_ccxt_pro_client(ccxt, exchange_name, market_type=market_type, symbol=symbol)

    tf = timeframe
    tf_modifier = tf[-1]
    tf_value = int(tf[:-1])
    if tf_modifier == "h":
        tf_multiplier = tf_value * 60
    else:
        tf_multiplier = tf_value

    since = ex.milliseconds() - int(tf_multiplier) * 60 * 1000

    try:
        while True:
            try:
                ws_trades = await ex.watch_trades(symbol, since, None, {})
                bar_to_push = None

                if on_trades is not None:
                    on_trades(ws_trades)

                async with state.lock:
                    since = _advance_trade_window(ex, state, since)
                    state.collected_trades.extend(
                        ex.filter_by_since_limit(ws_trades, since)
                    )
                    generated = ex.build_ohlcvc(state.collected_trades, tf, since)
                    bars = state.live_bars

                    for bar in generated:
                        ts = bar[0]
                        last_ts = bars[-1][0] if bars else 0

                        if ts == last_ts:
                            bars[-1] = bar
                            bar_to_push = bar
                        elif ts > last_ts:
                            bars.append(bar)
                            bar_to_push = bar

                    since = _advance_trade_window(ex, state, since)

                if bar_to_push is not None:
                    await on_bar(bar_to_push)

            except asyncio.CancelledError:
                # Session removed / hub shutting down: close the client and propagate.
                raise
            except Exception:
                await _safe_close(ex)
                ex = make_ccxt_pro_client(ccxt, exchange_name, market_type=market_type, symbol=symbol)
    finally:
        await _safe_close(ex)


async def fix_missing_bars_loop(
    exchange_name: str,
    timeframe: str,
    state: DataState,
    check_interval_sec: float = 0.1,
    time_sync_interval_sec: float = 10 * 60,
) -> None:
    tf_ms = convert_timeframe(timeframe, to_ms=True)
    grace_ms = 0.2 * 1000
    clock = retain_exchange_clock(exchange_name, time_sync_interval_sec)

    try:
        while True:
            await asyncio.sleep(check_interval_sec)

            # Shared per exchange, so many feeds do not stampede the public
            # time endpoint with synchronized fetch_time requests.
            now_ms = await clock.now_ms()

            missing_ts: Optional[int] = None

            async with state.lock:
                bars = state.live_bars
                if len(bars) < 1:
                    continue

                last_open_ts = bars[-1][0]
                expected = last_open_ts + tf_ms

                if now_ms >= expected + grace_ms:
                    has_next = any(b[0] == expected for b in bars)
                    if (not has_next) and (state.last_fix_bar_ts != expected):
                        missing_ts = expected

                if missing_ts is None:
                    continue

                prev_close = bars[-1][4]
                # No trades occurred in this interval. Store the placeholder as true 0-volume.
                # OKX/Binance/Bybit policy will hide it; BITGET/Hyperliquid still treat it as visible.
                fake = [missing_ts, prev_close, prev_close, prev_close, prev_close, 0.0]
                bars.append(fake)
                # print(f"[fix_missing_bars_loop] {exchange_name} bar: {fake}")
                state.last_fix_bar_ts = missing_ts
    finally:
        await release_exchange_clock(exchange_name)
