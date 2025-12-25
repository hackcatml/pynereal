from __future__ import annotations

import asyncio
import os
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from pynecore.cli.app import app_state
from state import DataState
from ohlcv_io import (
    parse_timeframe_to_ms,
    download_history,
    fix_last_open_if_needed,
    fetch_and_update_ohlcv_data,
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
    emit_event: Callable[[dict], Awaitable[None]],
    poll_sec: float = 0.1,
) -> None:
    # Read history_since from realtime.toml
    config_dir = app_state.config_dir
    with open(config_dir / "realtime_trade.toml", "rb") as f:
        realtime_config = tomllib.load(f)
    realtime_section: dict = realtime_config.get("realtime", {})
    history_since = realtime_section.get("history_since", "")

    start_timestamp: Optional[int] = None

    if ohlcv_path.exists() and history_since == "":
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

    # Flag to prevent duplicate prerun_ready events
    prerun_sent_for_bar_ts: Optional[int] = None

    history_download_complete: bool = False
    first_fetch_after_download_done: bool = False

    while True:
        await asyncio.sleep(poll_sec)

        async with state.lock:
            bars = state.live_bars

            # 1) file missing -> download history
            if not ohlcv_path.exists():
                since = None
                if start_timestamp is not None:
                    since = datetime.fromtimestamp(start_timestamp).strftime("%Y-%m-%d")
                elif history_since != "":
                    since = history_since

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
                else:
                    history_download_complete = True
                    first_fetch_after_download_done = False

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
                if not history_download_complete:
                    continue

                # Fetch candles via fetch_ohlcv at the first pre_run after history download
                if not first_fetch_after_download_done:
                    fetch_and_update_ohlcv_data(exchange, symbol, timeframe, str(ohlcv_path))
                    first_fetch_after_download_done = True
                else:
                    fixed_open_price = fix_last_open_if_needed(str(ohlcv_path))

                open_fix_done = True

                # Send pre-run ready signal (confirmed bar and new bar)
                confirmed_bar_and_new_bar = [bars[0], bars[1]]
                if fixed_open_price > 0.0:
                    confirmed_bar_and_new_bar[0][1] = fixed_open_price

                bar_ts = int(confirmed_bar_and_new_bar[1][0])  # new bar timestamp in ms
                if prerun_sent_for_bar_ts != bar_ts:
                    prerun_sent_for_bar_ts = bar_ts
                    await emit_event(
                        {
                            "type": "prerun_ready",
                            "ohlcv_path": str(ohlcv_path),
                            "toml_path": str(toml_path),
                            "confirmed_bar_and_new_bar": confirmed_bar_and_new_bar,  # 2 raw bars in ms
                        }
                    )

            # 3) bars>=3 -> keep 2 and update file
            if len(bars) >= 3 and ohlcv_path.exists():
                confirmed_bar_and_new_bar = bars[1:]  # confirmed, new
                state.live_bars = confirmed_bar_and_new_bar

                if not history_download_complete:
                    continue

                if fixed_open_price > 0.0:
                    confirmed_bar_and_new_bar[0][1] = fixed_open_price

                incremented_size = update_ohlcv_data(str(ohlcv_path), confirmed_bar_and_new_bar)
                if incremented_size > 0:
                    await emit_event(
                        {
                            "type": "run_ready",
                            "ohlcv_path": str(ohlcv_path),
                            "toml_path": str(toml_path),
                            "confirmed_bar_and_new_bar": confirmed_bar_and_new_bar,  # confirmed/new
                        }
                    )
                else:
                    print(f"Failed to update OHLCV file with bars: {confirmed_bar_and_new_bar}")

                fixed_open_price = 0.0
                open_fix_done = False
                prerun_sent_for_bar_ts = None
