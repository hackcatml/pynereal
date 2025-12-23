from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from state import DataState
from ohlcv_io import (
    parse_timeframe_to_ms,
    download_history,
    fix_last_open_if_needed,
    update_ohlcv_data,
)


async def file_update_loop(
    *,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    ohlcv_path: Path,
    toml_path: Path,
    state: DataState,
    emit_event: Callable[[dict], Awaitable[None]],  # ✅ 추가
    poll_sec: float = 0.1,
) -> None:
    start_timestamp: Optional[int] = None
    if ohlcv_path.exists():
        from pynecore.core.ohlcv_file import OHLCVReader
        with OHLCVReader(ohlcv_path) as reader:
            start_timestamp = reader.start_timestamp
            reader.close()

    # Remove all the data file
    for file_path in [ohlcv_path, toml_path]:
        if file_path.exists():
            os.remove(file_path)

    timeframe_ms = parse_timeframe_to_ms(timeframe)
    pre_run_script_time = timeframe_ms / 2
    fixed_open_price: float = 0.0
    open_fix_done = False

    # ✅ prerun_ready를 중복 발송하지 않기 위한 플래그
    prerun_sent_for_bar_ts: Optional[int] = None

    while True:
        await asyncio.sleep(poll_sec)

        async with state.lock:
            bars = state.live_bars

            # 1) bars==2 and file missing -> download history
            if len(bars) == 2 and (not ohlcv_path.exists()):
                since = None
                if start_timestamp is not None:
                    since = datetime.fromtimestamp(start_timestamp).strftime("%Y-%m-%d")

                with ThreadPoolExecutor() as ex:
                    ok = ex.submit(
                        download_history,
                        provider,
                        exchange,
                        symbol,
                        timeframe,
                        since,
                    ).result()

                if not ok:
                    for fp in (ohlcv_path, toml_path):
                        if fp.exists():
                            os.remove(fp)
                    continue

                fixed_open_price = 0.0
                open_fix_done = False
                prerun_sent_for_bar_ts = None

            # 2) pre-run open fix timing
            if (
                len(bars) == 2
                and ohlcv_path.exists()
                and (not open_fix_done)
                and (datetime.now().timestamp() * 1000 >= bars[1][0] + pre_run_script_time)
            ):
                fixed_open_price = fix_last_open_if_needed(str(ohlcv_path))
                open_fix_done = True

                # ✅ pre-run 준비 신호 발송 (confirmed bar and new bar)
                confirmed_bar_and_new_bar = [bars[0], bars[1]]
                if fixed_open_price > 0.0:
                    confirmed_bar_and_new_bar[0][1] = fixed_open_price

                bar_ts = int(confirmed_bar_and_new_bar[1][0])  # new bar ts(ms)
                if prerun_sent_for_bar_ts != bar_ts:
                    prerun_sent_for_bar_ts = bar_ts
                    await emit_event(
                        {
                            "type": "prerun_ready",
                            "ohlcv_path": str(ohlcv_path),
                            "toml_path": str(toml_path),
                            "fixed_open_price": float(fixed_open_price),
                            "confirmed_bar_and_new_bar": confirmed_bar_and_new_bar,  # ms 기반 raw bar 2개
                        }
                    )

            # 3) bars>=3 -> keep 2 and update file
            if len(bars) >= 3 and ohlcv_path.exists():
                confirmed_bar_and_new_bar = bars[1:]  # confirmed, new
                state.live_bars = confirmed_bar_and_new_bar

                if fixed_open_price > 0.0:
                    confirmed_bar_and_new_bar[0][1] = fixed_open_price

                update_ohlcv_data(str(ohlcv_path), confirmed_bar_and_new_bar)

                await emit_event(
                    {
                        "type": "run_ready",
                        "ohlcv_path": str(ohlcv_path),
                        "toml_path": str(toml_path),
                        "confirmed_bar_and_new_bar": confirmed_bar_and_new_bar,  # confirmed/new
                    }
                )

                fixed_open_price = 0.0
                open_fix_done = False
                prerun_sent_for_bar_ts = None
