from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Awaitable, Callable, Optional

from config import DataServiceConfig
from pynecore.cli.commands.data import parse_date_or_days
from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.cli.app import app_state
from ohlcv_io import (
    convert_timeframe,
    download_history,
    fix_last_open_if_needed,
    fetch_and_update_ohlcv_data,
    download_history_range_into_cache,
    update_ohlcv_data,
)
from ohlcv_cache import (
    init_cache,
    cache_has_data,
    get_last_ts,
    get_min_ts,
    import_from_ohlcv,
    export_to_ohlcv,
    upsert_bars,
    export_to_ohlcv_since,
)
from ohlcv_paths import make_cache_path
from state import DataState


async def file_update_loop(
    *,
    config: DataServiceConfig,
    ohlcv_path: Path,
    toml_path: Path,
    state: DataState,
    emit_event: Callable[[dict], Awaitable[None]],
    poll_sec: float = 0.1,
) -> None:
    provider = config.provider
    exchange = config.exchange
    symbol = config.symbol
    timeframe = config.timeframe
    cache_path = make_cache_path()
    init_cache(cache_path)
    # print(f"[data_service] sqlite cache path: {cache_path}")

    # Get history_since from the config
    realtime_section: dict = config.realtime_section
    history_since = realtime_section.get("history_since", "")

    start_timestamp: Optional[int] = None
    cache_ready = cache_has_data(cache_path, provider, exchange, symbol, timeframe)
    history_download_complete: bool = False
    desired_dt: Optional[datetime] = None
    export_start_ts: Optional[int] = None
    # Resolve desired start time from history_since or default window.
    if history_since:
        try:
            desired_dt = parse_date_or_days(history_since)
            if desired_dt.tzinfo is None:
                desired_dt = desired_dt.replace(tzinfo=UTC)
        except Exception:
            desired_dt = None
    else:
        tf_modifier = timeframe[-1]
        tf_value = int(timeframe[:-1])
        month_ago = 1 if tf_modifier == "m" and tf_value == 1 else 2
        desired_dt = (datetime.now(UTC) - relativedelta(months=month_ago)).replace(second=0, microsecond=0)

    if ohlcv_path.exists() and desired_dt is not None:
        with OHLCVReader(ohlcv_path) as reader:
            start_ts = reader.start_timestamp
            reader.close()
        if start_ts is not None and int(start_ts) != int(desired_dt.timestamp()):
            export_start_ts = int(desired_dt.timestamp())
            print("[data_service] history_since changed; ohlcv will be regenerated from cache")

    if cache_ready:
        # Ensure toml exists before using cached data.
        if not toml_path.exists():
            try:
                provider_module = __import__(f"pynecore.providers.{provider}", fromlist=[""])
                provider_class = getattr(
                    provider_module,
                    [p for p in dir(provider_module) if p.endswith("Provider")][0],
                )
                provider_instance = provider_class(
                    symbol=f"{exchange}:{symbol}".upper(),
                    timeframe=convert_timeframe(timeframe),
                    ohlv_dir=ohlcv_path.parent,
                    config_dir=app_state.config_dir,
                )
                sym_info = provider_instance.get_symbol_info(force_update=False)
                sym_info.save_toml(toml_path)
                print("[data_service] toml regenerated from provider symbol info")
            except Exception as e:
                import traceback
                print(f"[data_service] toml regeneration failed: {e!r}")
                print(traceback.format_exc())
                cache_ready = False
        else:
            print("[data_service] sqlite cache found; syncing from last_ts")
        # Backfill cache if desired history is older than cached min_ts.
        if cache_ready and desired_dt is not None:
            try:
                desired_ts = int(desired_dt.timestamp())
                min_ts = get_min_ts(cache_path, provider, exchange, symbol, timeframe)
                if min_ts is not None and desired_ts < min_ts:
                    print(f"[data_service] backfilling cache: {desired_ts} -> {min_ts}")
                    with ThreadPoolExecutor() as ex:
                        ok = ex.submit(
                            download_history_range_into_cache,
                            cache_path=cache_path,
                            provider=provider,
                            exchange=exchange,
                            symbol=symbol,
                            timeframe=timeframe,
                            time_from=desired_dt,
                            time_to=datetime.fromtimestamp(min_ts, UTC),
                        ).result()
                    if ok:
                        print("[data_service] backfill updated cache via download_history")
                else:
                    print(f"[data_service] backfill not needed (desired_ts={desired_ts}, min_ts={min_ts})")
            except Exception as e:
                print(f"[data_service] history_since backfill skipped: {e}")
        # Refresh cache from last_ts (include last bar to finalize).
        last_ts = get_last_ts(cache_path, provider, exchange, symbol, timeframe)
        if last_ts is not None:
            with ThreadPoolExecutor() as ex:
                ok = ex.submit(
                    download_history_range_into_cache,
                    cache_path=cache_path,
                    provider=provider,
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    time_from=datetime.fromtimestamp(
                        int(last_ts) - int(convert_timeframe(timeframe, to_ms=True) / 1000),
                        UTC,
                    ),
                    time_to=datetime.now(UTC),
                ).result()
            if ok:
                print("[data_service] sqlite cache updated via download_history")
        # Export cache into ohlcv for runner consumption.
        if export_start_ts is not None:
            export_to_ohlcv_since(
                cache_path,
                provider,
                exchange,
                symbol,
                timeframe,
                ohlcv_path,
                export_start_ts,
            )
        else:
            export_to_ohlcv(cache_path, provider, exchange, symbol, timeframe, ohlcv_path)
        if ohlcv_path.exists():
            print("[data_service] ohlcv regenerated from sqlite cache")
            history_download_complete = True
            state.pending_prerun_event = {
                "type": "prerun_ready_after_history_download",
                "ohlcv_path": str(ohlcv_path),
                "toml_path": str(toml_path),
                "confirmed_bar_and_new_bar": None
            }
    if not cache_ready:
        # No cache: start history download flow.
        print("[data_service] sqlite cache missing; starting history download")
        if ohlcv_path.exists() and history_since == "":
            with OHLCVReader(ohlcv_path) as reader:
                start_timestamp = reader.start_timestamp
                reader.close()
        for file_path in (ohlcv_path, toml_path):
            if file_path.exists():
                file_path.unlink()

    timeframe_ms = convert_timeframe(timeframe, to_ms=True)
    pre_run_script_time = timeframe_ms / 2
    fixed_open_price: float = 0.0
    open_fix_done = False

    # Flag to prevent duplicate prerun_ready events
    prerun_sent_for_bar_ts: Optional[int] = None

    first_fetch_after_download_done: bool = False

    while True:
        await asyncio.sleep(poll_sec)

        async with state.lock:
            bars = state.live_bars

            # 1) file missing -> download history
            if not ohlcv_path.exists():
                # Compute since date for history download.
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
                            fp.unlink()
                    continue
                else:
                    history_download_complete = True
                    first_fetch_after_download_done = False
                    import_from_ohlcv(cache_path, provider, exchange, symbol, timeframe, ohlcv_path)
                    # print("[data_service] sqlite cache populated from downloaded ohlcv")

                    # Store pending event instead of emitting immediately
                    # This will be sent when runner_service connects
                    state.pending_prerun_event = {
                        "type": "prerun_ready_after_history_download",
                        "ohlcv_path": str(ohlcv_path),
                        "toml_path": str(toml_path),
                        "confirmed_bar_and_new_bar": None
                    }
                    # print("[file_update_loop] History download complete. Event will be sent when client connects.")

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
                # Wait for history download and fix last open if needed.
                if not history_download_complete:
                    continue

                # Fetch candles via fetch_ohlcv at the first pre_run after history download
                if not first_fetch_after_download_done:
                    res = fetch_and_update_ohlcv_data(exchange, symbol, timeframe, str(ohlcv_path))
                    if res:
                        # print(f"[data_service] pre_run fetch updated {len(res)} bars")
                        cache_rows = []
                        with OHLCVReader(ohlcv_path) as reader:
                            for i in range(len(res)):
                                bar = reader.read(reader.size - (len(res) - i))
                                cache_rows.append(bar)
                            reader.close()
                        upsert_bars(cache_path, provider, exchange, symbol, timeframe, cache_rows)
                        last_ts = get_last_ts(cache_path, provider, exchange, symbol, timeframe)
                        # print(f"[data_service] sqlite cache updated from pre_run fetch (last_ts={last_ts})")
                    first_fetch_after_download_done = True
                else:
                    fixed_open_price = fix_last_open_if_needed(str(ohlcv_path))
                    if fixed_open_price > 0.0:
                        # Fix the last bar stored in the ohlcv cache
                        cache_rows = []
                        with OHLCVReader(ohlcv_path) as reader:
                            last_bar = reader.read(reader.size - 1)
                            cache_rows.append(last_bar)
                            upsert_bars(cache_path, provider, exchange, symbol, timeframe, cache_rows)
                            reader.close()

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
                # Apply live bars and emit run_ready if ohlcv is updated.
                confirmed_bar_and_new_bar = bars[1:]  # confirmed, new
                state.live_bars = confirmed_bar_and_new_bar

                if not history_download_complete:
                    continue

                if fixed_open_price > 0.0:
                    confirmed_bar_and_new_bar[0][1] = fixed_open_price

                incremented_size = update_ohlcv_data(str(ohlcv_path), confirmed_bar_and_new_bar)
                if incremented_size > 0:
                    # Emit run_ready signal to runner_service
                    await emit_event(
                        {
                            "type": "run_ready",
                            "ohlcv_path": str(ohlcv_path),
                            "toml_path": str(toml_path),
                            "confirmed_bar_and_new_bar": confirmed_bar_and_new_bar,  # confirmed/new
                        }
                    )
                    # SQLite cache sync
                    # print(f"[data_service] ohlcv updated from live bars; syncing sqlite cache, {confirmed_bar_and_new_bar}")
                    with OHLCVReader(ohlcv_path) as reader:
                        last_confirmed_bar = reader.read(reader.size - 2)
                        last_new_bar = reader.read(reader.size - 1)
                        cache_rows = []
                        for cd in [last_confirmed_bar, last_new_bar]:
                            cache_rows.append([cd.timestamp, cd.open, cd.high, cd.low, cd.close, cd.volume])
                        upsert_bars(cache_path, provider, exchange, symbol, timeframe, cache_rows)
                        # print("[data_service] sqlite cache synced")
                        reader.close()
                else:
                    print(f"Failed to update OHLCV file with bars: {confirmed_bar_and_new_bar}")

                fixed_open_price = 0.0
                open_fix_done = False
                prerun_sent_for_bar_ts = None
