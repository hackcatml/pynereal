from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tomllib
import ast
from script_hash import compute_script_hashes, load_script_hashes, write_script_hashes
from collections import deque
from functools import partial
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
import websockets

from appendable_iter import AppendableIterable
from pynecore.cli.app import app_state
from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.core.exchange_policy import tradingview_hides_zero_volume
from pynecore.core.script_runner import ScriptRunner
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

DATA_WS = ""
SCRIPT_PATH: Path | None = None
SCRIPT_HASH_PATH: Path | None = None  # CSV path for persisted script hashes.
PLOT_PATH: Path | None = None  # per-session plot CSV path (from --plot-path).
SESSION_ID: str = "default"
# Live webhook/telegram toggles for this session (decision 8-1). Updated at
# startup from args/env and at runtime via the "webhook_config" WS message.
WEBHOOK_ENABLED: bool = False
TELEGRAM_ENABLED: bool = False
# Per-session webhook URL / telegram credentials (from the hub's webhook_config WS
# message). Empty => fall back to the script's webhook_url / .env BOT_TOKEN.
WEBHOOK_URL: str = ""
TELEGRAM_TOKEN: str = ""
TELEGRAM_CHAT_ID: str = ""

# Event queue for trade events
trade_event_queue = deque()
# Dictionary for plot options (title -> options mapping)
plot_options = {}
# Event queue for plotchar events
plotchar_event_queue = deque()


def extract_script_title(script_path: Path) -> str:
    try:
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(script_path))
    except Exception:
        return "No title"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id != "script":
                continue
            if func.attr not in {"strategy", "indicator", "library"}:
                continue
            for kw in node.keywords:
                if kw.arg == "title" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value or "No title"
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                return node.args[0].value or "No title"
    return "No title"


def build_script_info_payload(script_path: Path, title: str | None = None) -> dict:
    source = ""
    try:
        source = script_path.read_text(encoding="utf-8")
    except Exception:
        source = ""

    return {
        "type": "script_info",
        "title": title or extract_script_title(script_path),
        "source_name": script_path.name,
        "source": source,
    }


def format_timeframe(period: str | None) -> str:
    """Convert a syminfo period (e.g. "1", "5", "60", "D") to a TradingView-style
    timeframe label (e.g. "1m", "5m", "1h", "1D")."""
    if not period:
        return ""
    period = str(period).strip()
    if period.isdigit():
        n = int(period)
        if n >= 1440 and n % 1440 == 0:
            return f"{n // 1440}D"
        if n >= 60 and n % 60 == 0:
            return f"{n // 60}h"
        return f"{n}m"
    mapping = {"D": "1D", "W": "1W", "M": "1M"}
    return mapping.get(period.upper(), period)


def send_webhook_message(webhook_url: str, message: str, *, script_title: str | None,
                         timeframe: str | None, ticker: str | None,
                         telegram_notification: bool, telegram_token: str | None,
                         telegram_chat_id: str | None) -> None:
    import json
    import re
    import datetime
    import requests

    # Wrap unquoted message fields so JSON parsing succeeds.
    s = re.sub(r'"message"\s*:\s*(?![{["0-9])([A-Za-z][A-Za-z0-9 ]*)',
               r'"message": "\1"',
               message)
    parsed = json.loads(s)
    json_alert_message = parsed.get('message', '')
    if json_alert_message != '' and webhook_url:
        payload = json_alert_message
        try:
            response = requests.post(webhook_url, json=payload, timeout=(5, 10))
            response.raise_for_status()
            print("Webhook response:", response.json())
        except Exception as e:
            print(f"Webhook error: {e}")

    if telegram_notification and telegram_token and telegram_chat_id:
        # Wall-clock time at which the notification is sent.
        time_str = datetime.datetime.now().strftime('%H:%M:%S')

        # Signal original: keep the raw message body, rendering nested dicts readably.
        if isinstance(json_alert_message, str):
            signal_str = json_alert_message
        else:
            signal_str = json.dumps(json_alert_message, ensure_ascii=False).replace('"', '')

        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        payload = {
            "chat_id": telegram_chat_id,
            "text": (
                f"🚨 [{script_title}]\n"
                f"Time: {time_str}\n"
                f"Timeframe: {timeframe or ''}\n"
                f"Ticker: {ticker or ''}\n"
                f"Signal: {signal_str}"
            ),
            # "parse_mode": "Markdown"  # 굵게/이탤릭 등 쓰고 싶으면 선택
        }
        try:
            response = requests.get(url, params=payload, timeout=(5, 10))
            response.raise_for_status()
            print("Telegram response:", response.json())
        except Exception as e:
            print(f"Telegram notification error: {e}")


def clear_local_state() -> None:
    trade_event_queue.clear()
    plotchar_event_queue.clear()
    plot_options.clear()


def on_entry_event(trade, runner=None):
    """Callback for entry events.

    pre_run-gated: the runner is destroyed and rebuilt every cycle, and prerun
    replays the whole history re-filling every order. Emitting markers during that
    replay would duplicate them (and, on OKX, a hidden-bar fill replays onto a
    different visible bar than the real-time fake-bar fill, so dedup-by-time fails).
    We emit only on real-time fills (run_ready, pre_run=False) and on the one-time
    full-history pre-run after a history download (also pre_run=False).
    """
    if runner is not None and getattr(runner.script, "pre_run", False):
        return
    event = {
        "type": "trade_entry",
        "time": int(trade.entry_time / 1000),
        "price": float(trade.entry_price),
        "size": float(trade.size),
        "id": trade.entry_id,
        "comment": trade.entry_comment if trade.entry_comment else ""
    }
    trade_event_queue.append(event)


def on_close_event(trade, runner=None):
    """Callback for close events. pre_run-gated; see on_entry_event."""
    if runner is not None and getattr(runner.script, "pre_run", False):
        return
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


def on_alert_event(message: str, runner: ScriptRunner):
    """Callback for alert events - webhook/telegram notifications"""
    script = runner.script

    # Per-session overrides (from the hub's webhook_config WS message) take
    # precedence over the script's webhook_url / .env credentials. Sending is gated
    # by the live session toggles (decision 8-1). Webhook and telegram are
    # independent: either, both, or neither can fire.
    webhook_url = WEBHOOK_URL or script.webhook_url
    telegram_token = TELEGRAM_TOKEN or script.telegram_token
    telegram_chat_id = TELEGRAM_CHAT_ID or script.telegram_chat_id

    do_webhook = WEBHOOK_ENABLED and bool(webhook_url)
    do_telegram = TELEGRAM_ENABLED and bool(telegram_token) and bool(telegram_chat_id)
    if not do_webhook and not do_telegram:
        return

    syminfo = getattr(runner, "syminfo", None)
    send_webhook_message(
        webhook_url=webhook_url if do_webhook else "",
        message=message,
        script_title=script.title,
        timeframe=format_timeframe(getattr(syminfo, "period", None)),
        ticker=getattr(syminfo, "ticker", None),
        telegram_notification=do_telegram,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
    )


def hide_zero_volume_bars(exchange: str | None) -> bool:
    # TradingView policy differs by exchange:
    # - OKX/Binance: zero-volume candles are hidden and excluded from calculations.
    # - BITGET/Hyperliquid: zero-volume candles remain visible and are included.
    return tradingview_hides_zero_volume(exchange)


def is_visible_ohlcv(ohlcv: OHLCV, *, hide_zero_volume: bool) -> bool:
    # "visible" means the candle should advance bar_index and run strategy logic.
    if ohlcv.volume < 0:
        return False
    if hide_zero_volume and ohlcv.volume == 0:
        return False
    return True


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

    # Build the runner's candle list with the same hidden-bar policy used by TradingView.
    # last_bar_index must be based on this visible list, not the raw file size.
    preload_list = list(
        reader.read_from(
            int(time_from.timestamp()),
            int(time_to.timestamp()),
            skip_zero_volume=hide_zero_volume_bars(syminfo.prefix),
        )
    )
    size = len(preload_list)
    if size == 0:
        reader.close()
        return None
    ohlcv_iter: Iterator[OHLCV] = iter(preload_list)
    # Prepare a mutable iterator.
    if ohlcv_iter is not None:
        stream: AppendableIterable[OHLCV] = AppendableIterable(ohlcv_iter, feed_in_background=True)
    else:
        return None

    from pynecore.cli.app import app_state
    # Add lib directory to Python path for library imports
    lib_dir = app_state.scripts_dir / "lib"
    lib_path_added = False
    # Add lib directory to Python path for library imports only if it exists.
    # The ScriptRunner must be created regardless (a clean workdir may have no lib dir).
    if lib_dir.exists() and lib_dir.is_dir():
        sys.path.insert(0, str(lib_dir))
        lib_path_added = True

    try:
        # #################################### Module calculation ####################################
        # # bb1d / weekly high, low calculation
        # from modules.bb1d_calc import get_bb1d_lower
        # from modules.weekly_hl_calc import get_weekly_high_low
        # bb1d_lower = get_bb1d_lower(str(data_path), period=20, mult=2.0,
        #                             lookahead_on=True)
        # macro_high, macro_low = get_weekly_high_low(str(data_path), ago=2, session_offset_hours=9,
        #                                             lookahead_on=True)
        # #################################### Module calculation ####################################

        # Create script runner (this is where the import happens)
        config_dir = app_state.config_dir
        plot_path = PLOT_PATH if PLOT_PATH is not None else app_state.output_dir / f"{script_path.stem}.csv"
        with open(config_dir / "realtime_trade.toml", "rb") as f:
            realtime_config = tomllib.load(f)
            # Always populate webhook_url / telegram credentials on the script so the
            # per-session runtime toggle can gate sending in both directions without a
            # restart. Actual on/off lives in WEBHOOK_ENABLED / TELEGRAM_ENABLED, which
            # the hub updates live via the "webhook_config" WS message.
            realtime_config = dict(realtime_config)
            _rt_sec = dict(realtime_config.get("realtime", {}))
            _rt_sec["enabled"] = True
            realtime_config["realtime"] = _rt_sec
            _wh_sec = dict(realtime_config.get("webhook", {}))
            _wh_sec["enabled"] = True
            _wh_sec["telegram_notification"] = True
            realtime_config["webhook"] = _wh_sec
            runner = ScriptRunner(script_path, stream, syminfo,
                                  last_bar_index=size - 1,
                                  plot_path=plot_path, strat_path=None, trade_path=None,
                                  realtime_config=realtime_config,
                                  custom_inputs={
                                        # "bb1d_lower": bb1d_lower,
                                        # "macro_high": macro_high,
                                        # "macro_low": macro_low
                                  },
                                  preload_ohlcv=preload_list)
            runner.init_step()

            # Register trade event callbacks
            runner.script.position.on_entry_callback = partial(on_entry_event, runner=runner)
            runner.script.position.on_close_callback = partial(on_close_event, runner=runner)
            runner.script.position.on_alert_callback = partial(on_alert_event, runner=runner)
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


def ohlcv_event_data(ohlcv: OHLCV) -> dict:
    return {
        "time": int(ohlcv.timestamp),
        "open": float(ohlcv.open),
        "high": float(ohlcv.high),
        "low": float(ohlcv.low),
        "close": float(ohlcv.close),
        "volume": float(ohlcv.volume),
    }


def ohlcv_open_fix_event_data(ohlcv: OHLCV) -> dict:
    return {
        "time": int(ohlcv.timestamp),
        "open": float(ohlcv.open),
    }


def get_runner_candle(runner: ScriptRunner, index: int) -> OHLCV | None:
    candles = getattr(runner, "_all_ohlcv", None)
    if not candles or index < 0 or index >= len(candles):
        return None
    return candles[index]


def bar_list_to_ohlcv(bar: list) -> OHLCV:
    # bar: [ts_ms, o, h, l, c, v]
    return OHLCV(
        timestamp=int(bar[0] / 1000),
        # Align realtime bars with file precision (float32) to avoid BB rounding drift.
        open=float(np.float32(bar[1])),
        high=float(np.float32(bar[2])),
        low=float(np.float32(bar[3])),
        close=float(np.float32(bar[4])),
        volume=float(np.float32(bar[5])),
        extra_fields={},
    )


def run_prerun_steps(runner: ScriptRunner, prerun_range: int) -> None:
    for _ in range(prerun_range):
        step_res = runner.step()
        if step_res is None:
            break
    # Flush plot writer to ensure all data is written
    if runner.plot_writer:
        runner.plot_writer.flush()


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
                    await ws.send(json.dumps({"type": "client_hello", "role": "runner",
                                              "session_id": SESSION_ID}))
                except Exception:
                    pass
                try:
                    if SCRIPT_PATH and SCRIPT_PATH.exists():
                        await ws.send(json.dumps(build_script_info_payload(SCRIPT_PATH)))
                except Exception as e:
                    print(f"[runner] Failed to send script_info (connect): {e}")
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


def parse_runner_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PyneReal runner_service")
    p.add_argument("--session-id")
    p.add_argument("--data-service-ws")
    p.add_argument("--provider")
    p.add_argument("--exchange")
    p.add_argument("--symbol")
    p.add_argument("--timeframe")
    p.add_argument("--script-name")
    p.add_argument("--plot-path")
    p.add_argument("--hash-path")
    p.add_argument("--webhook-enabled")
    p.add_argument("--telegram-enabled")
    # Ignore unknown args so a manual launch is forgiving.
    args, _ = p.parse_known_args()
    return args


def _resolve(arg_val, env_key, toml_val, default=None):
    """Resolve a setting: CLI arg > env var > toml value > default."""
    if arg_val is not None and arg_val != "":
        return arg_val
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val
    if toml_val not in (None, ""):
        return toml_val
    return default


def _as_bool(v, fallback: bool) -> bool:
    if v is None:
        return fallback
    return str(v).strip().lower() in ("1", "true", "yes", "on")


async def _parent_watchdog(initial_ppid: int) -> None:
    """Self-terminate if the parent hub dies (orphan cleanup). When the spawning
    hub exits ungracefully (SIGKILL/crash), this child is reparented (its ppid
    changes, typically to 1 on macOS/Linux); without this it would keep
    reconnecting to whatever hub later binds the port and double-run the strategy."""
    while True:
        await asyncio.sleep(2)
        if os.getppid() != initial_ppid:
            print("[runner] parent hub gone; exiting to avoid orphan.")
            os._exit(0)


async def main():
    args = parse_runner_args()

    # Load realtime config (still required for pynecore/ScriptRunner behavior; also
    # the fallback source when CLI args / env are absent for a manual launch).
    config_dir = app_state.config_dir
    realtime_config: dict = {}
    try:
        with open(config_dir / "realtime_trade.toml", "rb") as f:
            realtime_config = tomllib.load(f)
    except Exception:
        realtime_config = {}
    realtime_section: dict = realtime_config.get("realtime", {})

    tf = _resolve(args.timeframe, "PYNEREAL_TIMEFRAME", realtime_section.get("timeframe", ""))
    if not tf:
        raise RuntimeError("timeframe is empty (args/env/realtime_trade.toml)")

    pyne_section: dict = realtime_config.get("pyne", {})
    if pyne_section.get("no_logo", False):
        os.environ["PYNE_NO_LOGO"] = "True"
        os.environ["PYNE_QUIET"] = "True"

    script_name = _resolve(args.script_name, "PYNEREAL_SCRIPT_NAME", realtime_section.get("script_name", ""))
    if not script_name:
        raise RuntimeError("script_name is empty (args/env/realtime_trade.toml)")

    script_path = app_state.scripts_dir / script_name
    if not script_path.exists():
        raise RuntimeError(f"script not found: {script_path}")

    global SCRIPT_PATH, SCRIPT_HASH_PATH, DATA_WS, PLOT_PATH, SESSION_ID
    global WEBHOOK_ENABLED, TELEGRAM_ENABLED, WEBHOOK_URL, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    SCRIPT_PATH = script_path
    SESSION_ID = _resolve(args.session_id, "PYNEREAL_SESSION_ID", None, default="default")

    # Per-session script-hash path (decision: avoid multi-runner contention).
    hash_path = _resolve(args.hash_path, "PYNEREAL_HASH_PATH", None)
    SCRIPT_HASH_PATH = Path(hash_path) if hash_path else SCRIPT_PATH.parent / ".script_hash.csv"
    SCRIPT_HASH_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Per-session plot CSV path.
    plot_path = _resolve(args.plot_path, "PYNEREAL_PLOT_PATH", None)
    PLOT_PATH = Path(plot_path) if plot_path else app_state.output_dir / f"{script_path.stem}.csv"
    PLOT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # data_service websocket endpoint.
    data_ws = _resolve(args.data_service_ws, "PYNEREAL_DATA_WS", None)
    if data_ws:
        DATA_WS = data_ws
    else:
        data_service_addr = realtime_section.get("data_service_addr", "")
        data_service_port = int(data_service_addr.split(":")[1]) if data_service_addr else 9001
        DATA_WS = f"ws://127.0.0.1:{data_service_port}/ws"

    # Initial webhook/telegram toggles (live-updated later via webhook_config WS msg).
    webhook_section = realtime_config.get("webhook", {})
    WEBHOOK_ENABLED = _as_bool(_resolve(args.webhook_enabled, "PYNEREAL_WEBHOOK_ENABLED", None),
                               bool(webhook_section.get("enabled", False)))
    TELEGRAM_ENABLED = _as_bool(_resolve(args.telegram_enabled, "PYNEREAL_TELEGRAM_ENABLED", None),
                                bool(webhook_section.get("telegram_notification", False)))

    from datetime import datetime
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{started_at}] [runner] started | session={SESSION_ID} tf={tf} "
          f"script={script_name} ws={DATA_WS}")

    # Exit if the spawning hub dies, so a killed/crashed hub never leaves an
    # orphan runner reconnecting to the next hub instance.
    asyncio.create_task(_parent_watchdog(os.getppid()))

    ctx: Optional[RunnerCtx] = None
    last_ws = None
    # Becomes True after the first pre_run completes; tells the hub to switch the
    # dashboard LED from amber (pre-running) to green. Reset on a fresh connection.
    ready_sent = False
    # Re-emit all trade markers on the next prerun when the connection is fresh
    # (runner restart/reconnect) or the script was edited mid-run. Trade-event
    # callbacks are pre_run-gated, so a cleared trades_history (reset_history on
    # reconnect, or script_modified on edit) would otherwise not be re-populated
    # until a full restart. A redundant re-emit (reconnect without change) is
    # harmless: data_service dedupes by exact event.
    pending_full_reemit = False

    async for ws, raw in ws_loop():
        if ws is not last_ws:
            last_ws = ws
            pending_full_reemit = True
            ready_sent = False
        try:
            msg = json.loads(raw)
        except Exception:
            continue

        mtype = msg.get("type")

        # -----------------------------
        # Live per-session webhook toggle (decision 8-1)
        # -----------------------------
        if mtype == "webhook_config":
            WEBHOOK_ENABLED = bool(msg.get("enabled", WEBHOOK_ENABLED))
            TELEGRAM_ENABLED = bool(msg.get("telegram_notification", TELEGRAM_ENABLED))
            WEBHOOK_URL = msg.get("url", WEBHOOK_URL) or ""
            TELEGRAM_TOKEN = msg.get("telegram_token", TELEGRAM_TOKEN) or ""
            TELEGRAM_CHAT_ID = msg.get("telegram_chat_id", TELEGRAM_CHAT_ID) or ""
            # Only log when something is actually configured (skip the noisy all-off default).
            if WEBHOOK_ENABLED or TELEGRAM_ENABLED or WEBHOOK_URL or TELEGRAM_TOKEN:
                print(f"[runner] webhook_config: webhook={WEBHOOK_ENABLED} telegram={TELEGRAM_ENABLED} "
                      f"url={'set' if WEBHOOK_URL else '-'} token={'set' if TELEGRAM_TOKEN else '-'}")
            continue

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
                    pending_full_reemit = True
                    await ws.send(json.dumps({"type": "script_modified"}))
                    clear_local_state()
                    write_script_hashes(SCRIPT_HASH_PATH, current_hashes)
            except Exception as e:
                print(f"[runner] Failed to send script_modified (prerun): {e}")

            # Ready runner + stream
            result = ready_scrip_runner(script_path, ohlcv_path, toml_path)
            if result is None:
                from datetime import datetime
                print(f"[{datetime.now().strftime('%y-%m-%d %H:%M:%S')}] [runner] failed to prepare runner")
                continue
            else:
                runner, stream, reader = result
                result = None

            # print("=== Pre-run start (up to the last bar) ===")
            size = runner.last_bar_index + 1
            last_visible_bar = get_runner_candle(runner, runner.last_bar_index)
            # In normal realtime pre-run, the file may already contain the open current bar.
            # If that bar is visible, keep it in the stream for the next run_ready and
            # pre-run only up to the last confirmed bar. If the raw last bar is a
            # hidden zero-volume candle, the visible list already ends at a confirmed bar.
            has_unconfirmed_visible_bar = (
                last_visible_bar is not None
                and reader.end_timestamp is not None
                and int(last_visible_bar.timestamp) == int(reader.end_timestamp)
            )
            prerun_range = size - 1 if has_unconfirmed_visible_bar else size
            runner.script.pre_run = True
            if mtype == "prerun_ready_after_history_download":
                prerun_range = size
                runner.script.pre_run = False
            elif pending_full_reemit:
                # Fresh connection (runner restart/reconnect) or mid-run script edit:
                # trades_history was (or may have been) cleared, and trade-event callbacks
                # are pre_run-gated, so a normal (pre_run=True) prerun would NOT re-emit
                # the historical markers, leaving the chart empty until a restart. Run this
                # one prerun with pre_run=False so the gated callbacks re-emit all historical
                # markers. prerun_range stays size-1: the last open bar is still left for
                # run_ready, so no spurious last-bar alert fires (its fill bar isn't stepped).
                runner.script.pre_run = False
            await asyncio.to_thread(run_prerun_steps, runner, prerun_range)
            pending_full_reemit = False

            # First pre_run done -> tell the hub the chart plots are ready (LED green).
            if not ready_sent:
                try:
                    await ws.send(json.dumps({"type": "runner_ready"}))
                    ready_sent = True
                except Exception as e:
                    print(f"[runner] Failed to send runner_ready: {e}")
            # print("=== Pre-run finished ===")

            try:
                title = runner.script.title or "No title"
                await ws.send(json.dumps(build_script_info_payload(script_path, title)))
            except Exception as e:
                print(f"[runner] Failed to send script_info: {e}")

            # Send the visible last candle itself. A visible index can differ from the
            # raw file index when OKX hidden zero-volume bars exist.
            try:
                last_bar = get_runner_candle(runner, runner.last_bar_index)
                event = {
                    "type": "last_bar_open_fix",
                    "last_bar_index": runner.last_bar_index,
                }
                if last_bar is not None:
                    event["data"] = ohlcv_open_fix_event_data(last_bar)
                await ws.send(json.dumps(event))
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
                    # Plot CSV rows are keyed by candle timestamp. Timestamp lookup avoids
                    # using a raw file index that may include hidden OKX bars.
                    confirmed_bar_index = runner.last_bar_index - 1
                    confirmed_bar = get_runner_candle(runner, confirmed_bar_index)
                    confirmed_bar_time = int(confirmed_bar.timestamp) if confirmed_bar is not None else None
                    plot_options_event = {
                        "type": "plot_options",
                        "data": plot_options,
                        "confirmed_bar_index": confirmed_bar_index,
                        "confirmed_bar_time": confirmed_bar_time,
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
            hide_zero_volume = hide_zero_volume_bars(getattr(ctx.runner.syminfo, "prefix", None))
            # confirmed_visible decides whether this just-closed candle should run one strategy step.
            # new_visible decides whether the newly opened candle should be kept as the next open bar.
            confirmed_visible = is_visible_ohlcv(confirmed_ohlcv, hide_zero_volume=hide_zero_volume)
            new_visible = is_visible_ohlcv(new_ohlcv, hide_zero_volume=hide_zero_volume)

            # Hidden fake/no-trade bars can stay in the raw file with volume 0, but they must not
            # enter the runner stream. BITGET/Hyperliquid leave hide_zero_volume=False, so their
            # 0-volume bars still take this visible path.
            confirmed_appended = False
            if confirmed_visible:
                try:
                    ctx.stream.replace_last(confirmed_ohlcv)
                except IndexError:
                    ctx.stream.append(confirmed_ohlcv)
                    confirmed_appended = True
                if new_visible:
                    ctx.stream.append(new_ohlcv)
            ctx.stream.finish()

            # Count only visible bars added to the runner's index space.
            timeframe_ms = parse_timeframe_to_ms(tf)
            interval_ms = (int(new_ohlcv.timestamp) - int(ctx.last_new_bar_ts_sec)) * 1000
            # last_bar_index tracks visible bars. If pre_run skipped a hidden fake open bar,
            # the confirmed bar may be appended as a new visible bar here.
            confirmed_increment = 1 if confirmed_appended else 0
            new_increment = 1 if interval_ms == timeframe_ms and new_visible else 0
            incremented_size = confirmed_increment + new_increment

            if confirmed_visible:
                # #################################### Module calculation ####################################
                # # bb1d / weekly high, low calculation
                # from modules.bb1d_calc import get_bb1d_lower
                # from modules.weekly_hl_calc import get_weekly_high_low
                # bb1d_lower = get_bb1d_lower(ohlcv_path, period=20, mult=2.0, lookahead_on=True)
                # macro_high, macro_low = get_weekly_high_low(ohlcv_path, ago=2, session_offset_hours=9,
                #                                             lookahead_on=True)
                # #################################### Module calculation ####################################

                # custom input update
                ctx.runner.script.custom_inputs = {
                    # "bb1d_lower": bb1d_lower,
                    # "macro_high": macro_high,
                    # "macro_low": macro_low
                }

                if incremented_size > 0:
                    ctx.runner.last_bar_index += incremented_size
                    ctx.runner.script.last_bar_index += incremented_size

                # The confirmed bar index is the visible index just evaluated. When a visible
                # new bar was appended, last_bar_index already points to that new open bar.
                confirmed_bar_index = ctx.runner.last_bar_index - new_increment
                # Strategy code uses strategy.last_bar_index() - 1 as the "last confirmed bar"
                # check in realtime mode. If the next OKX open bar is hidden, it is not appended
                # to the stream, but the confirmed bar should still satisfy that check.
                if not new_visible:
                    ctx.runner.script.last_bar_index = confirmed_bar_index + 1

                # Ensure request.security can see the new bar during confirmed-bar evaluation.
                from pynecore.lib.request import get_security_ctx
                security_ctx = get_security_ctx()
                if security_ctx is not None:
                    security_ctx.update_base_bar(confirmed_ohlcv, confirmed_bar_index)
                    if new_visible:
                        security_ctx.update_base_bar(new_ohlcv, ctx.runner.last_bar_index)

                # Calculate the last confirmed bar
                ctx.runner.script.pre_run = False
                while True:
                    step_res = ctx.runner.step()
                    if step_res is None:
                        break

                # Hidden-bar fix:
                # 새로 열린 봉이 volume 0 hidden bar 면 new_ohlcv 가 stream 에 append 되지
                # 않아(line 619-620) confirmed bar 의 main() 에서 접수된 주문을 체결할
                # process_orders() 패스가 없어 webhook alert 이 나가지 않는다. fake bar 의
                # open(=직전 종가)으로 대기 주문만 체결시켜 alert 을 발생시킨다. main() 은
                # 호출하지 않는다 (hidden bar 는 전략 로직/visible bar_index 에 들어가면 안 됨).
                # script.last_bar_index 는 위에서 confirmed_bar_index + 1 로 맞춰져 있어
                # realtime alert 게이트(order.bar_index == last_bar_index() - 1)가 성립한다.
                # BITGET/Hyperliquid 는 new_visible=True 라 기존 new bar step 경로를 그대로 탄다.
                if not new_visible and ctx.runner.script.position is not None:
                    from pynecore import lib
                    from pynecore.core.script_runner import _set_lib_properties
                    _set_lib_properties(new_ohlcv, confirmed_bar_index + 1,
                                        ctx.runner.tz, lib)
                    # Fill the pending order on the fake bar so the webhook alert fires
                    # AND the chart trade marker is emitted in real time (pre_run is False
                    # here). The duplicate that prerun replay would otherwise create (it
                    # re-fills on the next visible bar) is prevented by pre_run-gating the
                    # trade-event callbacks (see on_entry_event/on_close_event): only this
                    # real-time fill emits a marker, prerun replay does not re-emit.
                    ctx.runner.script.position.process_orders()

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
                            "confirmed_bar_index": confirmed_bar_index,
                            "confirmed_bar_time": int(confirmed_ohlcv.timestamp),
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
