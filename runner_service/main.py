from __future__ import annotations

import asyncio
import json
import os
import sys
import tomllib
from script_hash import compute_script_hashes, load_script_hashes, write_script_hashes
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import websockets

from appendable_iter import AppendableIterable
from pynecore.cli.app import app_state
from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.core.script_runner import ScriptRunner
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

DATA_WS = ""
SCRIPT_PATH: Path | None = None
SCRIPT_HASH_PATH: Path | None = None  # CSV path for persisted script hashes.

# Event queue for trade events
trade_event_queue = deque()
# Dictionary for plot options (title -> options mapping)
plot_options = {}
# Event queue for plotchar events
plotchar_event_queue = deque()


def clear_local_state() -> None:
    trade_event_queue.clear()
    plotchar_event_queue.clear()
    plot_options.clear()


def on_entry_event(trade):
    """Callback for entry events"""
    event = {
        "type": "trade_entry",
        "time": int(trade.entry_time / 1000),
        "price": float(trade.entry_price),
        "size": float(trade.size),
        "id": trade.entry_id,
        "comment": trade.entry_comment if trade.entry_comment else ""
    }
    trade_event_queue.append(event)


def on_close_event(trade):
    """Callback for close events"""
    event = {
        "type": "trade_close",
        "time": int(trade.exit_time / 1000),
        "price": float(trade.exit_price),
        "size": float(trade.size),
        "id": trade.entry_id,
        "comment": trade.exit_comment if trade.exit_comment else "",
        "profit": float(trade.profit)
    }
    trade_event_queue.append(event)


def on_plot_event(plot_data):
    """Callback for plot events - stores/updates plot options"""
    title = plot_data['title']
    new_options = {
        "color": plot_data.get('color'),
        "linewidth": plot_data.get('linewidth'),
        "style": plot_data.get('style'),
    }

    # Check if title exists and options are the same
    if title in plot_options:
        if plot_options[title] == new_options:
            return  # Skip if options are identical

    # Store or update plot options
    plot_options[title] = new_options


def on_plotchar_event(plotchar_data):
    """Callback for plotchar events"""
    event = {
        "type": "plotchar",
        "title": plotchar_data.get('title'),
        "time": plotchar_data.get('time'),
        "char": plotchar_data.get('char'),
        "text": plotchar_data.get('text'),
        "location": plotchar_data.get('location'),
        "color": plotchar_data.get('color'),
        "size": plotchar_data.get('size')
    }
    plotchar_event_queue.append(event)


def ready_scrip_runner(script_path: Path, data_path: Path, data_toml_path: Path) -> tuple[ScriptRunner,
AppendableIterable[OHLCV], OHLCVReader] | None:
    """
    A stage for preparing the pre-run script before running the script on the last confirmed candle.
    """
    # Get symbol info for the data
    syminfo = SymInfo.load_toml(data_toml_path)

    # Open data file (don't use 'with' - we return the reader and close it later in main())
    reader = OHLCVReader(data_path)
    reader.open()
    time_from = reader.start_datetime
    time_to = reader.end_datetime

    # Get the iterator
    gaps = sum(1 for ohlcv in reader if ohlcv.volume < 0)
    size = reader.get_size(int(time_from.timestamp()), int(time_to.timestamp()))
    if gaps > 0:
        size = size - gaps
    ohlcv_iter: Iterator[OHLCV] = reader.read_from(int(time_from.timestamp()), int(time_to.timestamp()))
    # Prepare a mutable iterator.
    if ohlcv_iter is not None:
        stream: AppendableIterable[OHLCV] = AppendableIterable(ohlcv_iter, feed_in_background=True)
    else:
        return None

    from pynecore.cli.app import app_state
    # Add lib directory to Python path for library imports
    lib_dir = app_state.scripts_dir / "lib"
    lib_path_added = False
    if lib_dir.exists() and lib_dir.is_dir():
        sys.path.insert(0, str(lib_dir))
        lib_path_added = True

        try:
            #################################### Module calculation ####################################
            # bb1d / weekly high, low calculation
            from modules.bb1d_calc import get_bb1d_lower
            from modules.weekly_hl_calc import get_weekly_high_low
            bb1d_lower = get_bb1d_lower(str(data_path), period=20, mult=2.0,
                                        lookahead_on=True)
            macro_high, macro_low = get_weekly_high_low(str(data_path), ago=2, session_offset_hours=9,
                                                        lookahead_on=True)
            #################################### Module calculation ####################################

            # Create script runner (this is where the import happens)
            config_dir = app_state.config_dir
            plot_path = app_state.output_dir / f"{script_path.stem}.csv"
            with open(config_dir / "realtime_trade.toml", "rb") as f:
                realtime_config = tomllib.load(f)
                runner = ScriptRunner(script_path, stream, syminfo,
                                      last_bar_index=size - 1,
                                      plot_path=plot_path, strat_path=None, trade_path=None,
                                      realtime_config=realtime_config,
                                      custom_inputs={
                                            "bb1d_lower": bb1d_lower,
                                            "macro_high": macro_high,
                                            "macro_low": macro_low
                                      })
                runner.init_step()

                # Register trade event callbacks
                runner.script.position.on_entry_callback = on_entry_event
                runner.script.position.on_close_callback = on_close_event
                # Register plot event callback
                runner.script.on_plot_callback = on_plot_event
                # Register plotchar event callback
                runner.script.on_plotchar_callback = on_plotchar_event
        finally:
            # Remove lib directory from Python path
            if lib_path_added:
                sys.path.remove(str(lib_dir))

        # return reader too. So we can close it later
        return runner, stream, reader


def bar_list_to_ohlcv(bar: list) -> OHLCV:
    # bar: [ts_ms, o, h, l, c, v]
    return OHLCV(
        timestamp=int(bar[0] / 1000),
        open=float(bar[1]),
        high=float(bar[2]),
        low=float(bar[3]),
        close=float(bar[4]),
        volume=float(bar[5]),
        extra_fields={},
    )


@dataclass
class RunnerCtx:
    runner: ScriptRunner
    stream: AppendableIterable[OHLCV] | None
    reader: OHLCVReader | None
    last_new_bar_ts_sec: int


async def ws_loop():
    while True:
        try:
            async with websockets.connect(DATA_WS, ping_interval=None) as ws:
                try:
                    await ws.send(json.dumps({"type": "client_hello", "role": "runner"}))
                except Exception:
                    pass
                try:
                    if SCRIPT_PATH and SCRIPT_PATH.exists():
                        current_hashes = compute_script_hashes(SCRIPT_PATH)
                        previous_hashes = load_script_hashes(SCRIPT_HASH_PATH)
                        if current_hashes != previous_hashes:
                            # Only reset when script contents changed.
                            await ws.send(json.dumps({"type": "reset_history"}))
                            clear_local_state()
                            write_script_hashes(SCRIPT_HASH_PATH, current_hashes)
                except Exception as e:
                    print(f"[runner] Failed to send reset_history: {e}")

                async def keepalive():
                    while True:
                        await asyncio.sleep(15)
                        try:
                            await ws.send("ping")
                        except Exception:
                            return

                ka = asyncio.create_task(keepalive())

                async for raw in ws:
                    yield ws, raw

                ka.cancel()
        except Exception:
            await asyncio.sleep(1)


def parse_timeframe_to_ms(tf: str) -> int:
    unit = tf[-1]
    value = int(tf[:-1])

    if unit == "m":
        return value * 60 * 1000
    elif unit == "h":
        return value * 60 * 60 * 1000
    else:
        return value * 24 * 60 * 60 * 1000


async def main():
    # Load realtime config
    config_dir = app_state.config_dir
    with open(config_dir / "realtime_trade.toml", "rb") as f:
        realtime_config = tomllib.load(f)

    realtime_section: dict = realtime_config.get("realtime", {})
    tf = realtime_section.get("timeframe", "")
    if tf == "":
        raise RuntimeError("timeframe is empty in realtime_trade.toml")

    pyne_section: dict = realtime_config.get("pyne", {})
    if pyne_section.get("no_logo", False):
        os.environ["PYNE_NO_LOGO"] = "True"
        os.environ["PYNE_QUIET"] = "True"

    script_name = realtime_section.get("script_name", "")
    if not script_name:
        raise RuntimeError("script_name is empty in realtime_trade.toml")

    script_path = app_state.scripts_dir / script_name
    if not script_path.exists():
        raise RuntimeError(f"script not found: {script_path}")
    global SCRIPT_PATH
    SCRIPT_PATH = script_path
    global SCRIPT_HASH_PATH
    SCRIPT_HASH_PATH = SCRIPT_PATH.parent / ".script_hash.csv"

    data_service_addr = realtime_section.get("data_service_addr", "")
    data_service_port = int(data_service_addr.split(":")[1]) if data_service_addr else 9001
    global DATA_WS
    DATA_WS = f"ws://127.0.0.1:{data_service_port}/ws"

    ctx: Optional[RunnerCtx] = None

    async for ws, raw in ws_loop():
        try:
            msg = json.loads(raw)
        except Exception:
            continue

        mtype = msg.get("type")

        # -----------------------------
        # Pre script run stage
        # -----------------------------
        if (mtype == "prerun_ready") or (mtype == "prerun_ready_after_history_download"):
            # Send ACK immediately for prerun_ready_after_history_download
            if mtype == "prerun_ready_after_history_download":
                try:
                    await ws.send(json.dumps({"type": "ack_prerun_ready_after_history_download"}))
                    # print("[runner] Sent ACK for prerun_ready_after_history_download")
                except Exception as e:
                    print(f"[runner] Failed to send ACK: {e}")

            ohlcv_path = Path(msg.get("ohlcv_path", ""))
            toml_path = Path(msg.get("toml_path", ""))
            if not ohlcv_path.exists() or not toml_path.exists():
                print("[runner] prerun_ready received but file missing:", ohlcv_path, toml_path)
                continue

            # Prevent the duplicate prerun_ready event
            if ctx is not None:
                continue

            try:
                current_hashes = compute_script_hashes(SCRIPT_PATH)
                previous_hashes = load_script_hashes(SCRIPT_HASH_PATH)
                if current_hashes != previous_hashes:
                    await ws.send(json.dumps({"type": "script_modified"}))
                    clear_local_state()
                    write_script_hashes(SCRIPT_HASH_PATH, current_hashes)
            except Exception as e:
                print(f"[runner] Failed to send script_modified (prerun): {e}")

            # Ready runner + stream
            result = ready_scrip_runner(script_path, ohlcv_path, toml_path)
            if result is None:
                from datetime import datetime
                print(f"[{datetime.now().strftime("%y-%m-%d %H:%M:%S")}] [runner] failed to prepare runner")
                continue
            else:
                runner, stream, reader = result
                result = None

            # print("=== Pre-run start (up to the last bar) ===")
            size = runner.last_bar_index + 1
            prerun_range = size - 1
            runner.script.pre_run = True
            if mtype == "prerun_ready_after_history_download":
                prerun_range = prerun_range + 1
                runner.script.pre_run = False
            for _ in range(prerun_range):
                step_res = runner.step()
                if step_res is None:
                    break
            # Flush plot writer to ensure all data is written
            if runner.plot_writer:
                runner.plot_writer.flush()
            # print("=== Pre-run finished ===")

            # Send last bar index to data_service to fix open price
            try:
                await ws.send(json.dumps({
                    "type": "last_bar_open_fix",
                    "last_bar_index": runner.last_bar_index,
                }))
            except Exception as e:
                print(f"[runner] Failed to send bar confirmation: {e}")

            # Send trade events to data_service
            if trade_event_queue:
                try:
                    await ws.send(json.dumps(list(trade_event_queue)))
                    trade_event_queue.clear()
                except Exception as e:
                    print(f"[runner] Failed to send trade events: {e}")

            # Send plotchar events to data_service
            if plotchar_event_queue:
                try:
                    await ws.send(json.dumps(list(plotchar_event_queue)))
                    plotchar_event_queue.clear()
                except Exception as e:
                    print(f"[runner] Failed to send plotchar events: {e}")

            # Send plot options to data_service
            if plot_options:
                try:
                    plot_options_event = {
                        "type": "plot_options",
                        "data": plot_options,
                        "confirmed_bar_index": runner.last_bar_index - 1,
                    }
                    await ws.send(json.dumps(plot_options_event))
                    # print(f"[runner] Sent plot_options: {plot_options}")
                except Exception as e:
                    print(f"[runner] Failed to send plot options: {e}")

            if mtype == "prerun_ready":
                # confirmed_bar_and_new_bar가 있다면 new bar ts를 추적에 사용
                confirmed_bar_and_new_bar = msg.get("confirmed_bar_and_new_bar")
                # print(f"[runner] pre_run confirmed_bar_and_new_bar: {confirmed_bar_and_new_bar}")
                # print(f"[runner] stream last: {stream.q[-1]}")
                last_new_ts_sec = 0
                if isinstance(confirmed_bar_and_new_bar, list) and len(confirmed_bar_and_new_bar) == 2:
                    last_new_ts_sec = int(confirmed_bar_and_new_bar[1][0] / 1000)
                else:
                    # fallback: Use end_timestamp from the file
                    with OHLCVReader(ohlcv_path) as r:
                        last_new_ts_sec = int(r.end_timestamp)
                        r.close()

                ctx = RunnerCtx(runner=runner, stream=stream, reader=reader, last_new_bar_ts_sec=last_new_ts_sec)
                # print(f"[runner] prerun done. last_new_bar_ts_sec={ctx.last_new_bar_ts_sec}")
            elif mtype == "prerun_ready_after_history_download":
                runner.destroy()
                stream.finish()
                stream = None
                reader.close()

        # -----------------------------
        # Script run stage
        # -----------------------------
        elif mtype == "run_ready":
            if ctx is None:
                continue

            ohlcv_path = msg.get("ohlcv_path", "")
            confirmed_bar_and_new_bar = msg.get("confirmed_bar_and_new_bar")
            # print(f"[runner] run_ready confirmed_bar_and_new_bar: {confirmed_bar_and_new_bar}")
            confirmed_bar = confirmed_bar_and_new_bar[0]
            new_bar = confirmed_bar_and_new_bar[1]

            confirmed_ohlcv = bar_list_to_ohlcv(confirmed_bar)
            new_ohlcv = bar_list_to_ohlcv(new_bar)

            # Replace the last uncompleted bar with the confirmed bar and append the new bar.
            ctx.stream.replace_last(confirmed_ohlcv)
            ctx.stream.append(new_ohlcv)
            ctx.stream.finish()

            # Check the interval is the same as the timeframe. If so, the incremented candle size is 1.
            timeframe_ms = parse_timeframe_to_ms(tf)
            interval_ms = (int(new_ohlcv.timestamp) - int(ctx.last_new_bar_ts_sec)) * 1000
            incremented_size = 1 if interval_ms == timeframe_ms else 0

            if incremented_size > 0:
                #################################### Module calculation ####################################
                # bb1d / weekly high, low calculation
                from modules.bb1d_calc import get_bb1d_lower
                from modules.weekly_hl_calc import get_weekly_high_low
                bb1d_lower = get_bb1d_lower(ohlcv_path, period=20, mult=2.0, lookahead_on=True)
                macro_high, macro_low = get_weekly_high_low(ohlcv_path, ago=2, session_offset_hours=9,
                                                            lookahead_on=True)
                #################################### Module calculation ####################################

                # custom input update
                ctx.runner.script.custom_inputs = {
                    "bb1d_lower": bb1d_lower,
                    "macro_high": macro_high,
                    "macro_low": macro_low
                }

                ctx.runner.last_bar_index += incremented_size
                ctx.runner.script.last_bar_index += incremented_size

                # Calculate the last confirmed bar
                ctx.runner.script.pre_run = False
                while True:
                    step_res = ctx.runner.step()
                    if step_res is None:
                        break

                # Send trade events to data_service
                if trade_event_queue:
                    try:
                        await ws.send(json.dumps(list(trade_event_queue)))
                        trade_event_queue.clear()
                    except Exception as e:
                        print(f"[runner] Failed to send trade events: {e}")

                # Send plotchar events to data_service
                if plotchar_event_queue:
                    try:
                        await ws.send(json.dumps(list(plotchar_event_queue)))
                        plotchar_event_queue.clear()
                    except Exception as e:
                        print(f"[runner] Failed to send plotchar events: {e}")

                # Send plot options to data_service
                if plot_options:
                    try:
                        plot_options_event = {
                            "type": "plot_options",
                            "data": plot_options,
                            "confirmed_bar_index": ctx.runner.last_bar_index - 1,
                        }
                        await ws.send(json.dumps(plot_options_event))
                        # print(f"[runner] Sent plot_options: {plot_options}")
                    except Exception as e:
                        print(f"[runner] Failed to send plot options: {e}")

            # Remove the script_module using destroy() (required). If you don't remove it,
            # the ScriptRunner will reuse the previous candle data even when reloading the script.
            ctx.runner.destroy()
            ctx.stream = None
            ctx.reader.close()
            ctx = None

        else:
            continue


if __name__ == "__main__":
    asyncio.run(main())
