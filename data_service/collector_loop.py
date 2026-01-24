from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, Optional, Awaitable

import ccxt.pro as ccxt

from state import DataState
from ohlcv_io import convert_timeframe


async def watch_trades_loop(
    exchange_name: str,
    symbol: str,
    timeframe: str,
    state: DataState,
    on_bar: Callable[[list], Awaitable[None]],
) -> None:
    ex = getattr(ccxt, exchange_name)(config={})

    tf = timeframe
    tf_modifier = tf[-1]
    tf_value = int(tf[:-1])
    if tf_modifier == "h":
        tf_multiplier = tf_value * 60
    else:
        tf_multiplier = tf_value

    since = ex.milliseconds() - int(tf_multiplier) * 60 * 1000

    while True:
        try:
            ws_trades = await ex.watch_trades(symbol, since, None, {})
            bar_to_push = None

            async with state.lock:
                state.collected_trades.extend(ws_trades)
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
                        state.collected_trades = ex.filter_by_since_limit(state.collected_trades, ts)
                        bar_to_push = bar

            if bar_to_push is not None:
                await on_bar(bar_to_push)

        except Exception:
            try:
                await ex.close()
            except Exception:
                pass
            ex = getattr(ccxt, exchange_name)(config={})


async def fix_missing_bars_loop(
    exchange_name: str,
    timeframe: str,
    state: DataState,
    check_interval_sec: float = 0.1,
) -> None:
    tf_ms = convert_timeframe(timeframe, to_ms=True)
    grace_ms = 0.2 * 1000
    ex = getattr(ccxt, exchange_name)(config={})

    while True:
        await asyncio.sleep(check_interval_sec)

        try:
            now_ms = await ex.fetch_time()
        except Exception:
            try:
                await ex.close()
            except Exception:
                pass
            ex = getattr(ccxt, exchange_name)(config={})
            now_ms = int(datetime.now().timestamp() * 1000)

        missing_ts: Optional[int] = None

        async with state.lock:
            bars = state.live_bars
            if len(bars) < 2:
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
            fake = [missing_ts, prev_close, prev_close, prev_close, prev_close, 0.01]
            bars.append(fake)
            state.last_fix_bar_ts = missing_ts
