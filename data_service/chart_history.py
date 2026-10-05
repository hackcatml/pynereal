"""Bounded, read-only history windows for live charts."""

from __future__ import annotations

import csv
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from pynecore.core.exchange_policy import tradingview_hides_zero_volume
from pynecore.core.ohlcv_file import OHLCVReader


CHART_PAGE_SIZE = 5000


def _bar_position(reader: OHLCVReader, timestamp: int) -> int:
    low, high = 0, reader.size
    while low < high:
        middle = (low + high) // 2
        if reader.read(middle).timestamp < timestamp:
            low = middle + 1
        else:
            high = middle
    return low


def read_candle_window(path: Path, exchange: str, limit: int, before=None, after=None) -> dict:
    result = {"bars": [], "has_before": False, "has_after": False, "interval": None}
    if not path.exists() or not path.stat().st_size:
        return result
    skip_zero = tradingview_hides_zero_volume(exchange)
    with OHLCVReader(path) as reader:
        result["interval"] = reader.interval
        if not reader.size:
            return result
        if after is not None:
            start = _bar_position(reader, after + 1)
            result["has_before"] = start > 0
            positions = range(start, reader.size)
        else:
            end = reader.size if before is None else _bar_position(reader, before)
            positions = range(end - 1, -1, -1)
        selected = []
        for position in positions:
            bar = reader.read(position)
            if bar.volume < 0 or (skip_zero and bar.volume == 0):
                continue
            selected.append((position, {
                "time": int(bar.timestamp), "open": float(bar.open), "high": float(bar.high),
                "low": float(bar.low), "close": float(bar.close), "volume": float(bar.volume),
            }))
            if len(selected) > limit:
                break
        has_more = len(selected) > limit
        selected = selected[:limit]
        if after is None:
            selected.reverse()
        if selected:
            result["bars"] = [bar for _, bar in selected]
            result["has_before"] = start > 0 if after is not None else has_more
            result["has_after"] = has_more if after is not None else end < reader.size
    return result


def _timestamp(value: str) -> int:
    if value.isdigit():
        return int(value)
    return int(datetime.fromisoformat(value).astimezone(UTC).timestamp())


def _seek_plot_time(handle: BinaryIO, data_start: int, timestamp: int) -> None:
    # Generated plot rows contain numeric values on one line. Seek by timestamp
    # without building a full-file index on every warm-up rewrite.
    handle.seek(0, 2)
    low, high = data_start, handle.tell()
    while low < high:
        middle = (low + high) // 2
        handle.seek(middle)
        if middle > data_start:
            handle.readline()
        line = handle.readline()
        if not line or not line.endswith(b"\n"):
            high = middle
            continue
        row = next(csv.reader([line.decode("utf-8")]))
        if not row or _timestamp(row[0]) >= timestamp:
            high = middle
        else:
            low = handle.tell()
    handle.seek(low)


def read_plot_window(path: Path, options: dict, start: int, end: int) -> list:
    plots = []
    for title, option in options.items():
        plots.append({
            "title": title, "kind": str(option.get("kind") or "line"),
            "color": option.get("color"), "linewidth": option.get("linewidth"),
            "style": option.get("style"), "offset": option.get("offset", 0),
            "editable": option.get("editable", True), "show_last": option.get("show_last"),
            "force_overlay": option.get("force_overlay", False), "order": option.get("order", 0),
            "data": [],
        })
    if not plots or end < start:
        return plots
    with path.open("rb") as handle:
        # csv.reader also handles quoted commas/newlines in user-supplied titles.
        headers = next(csv.reader(line.decode("utf-8-sig") for line in handle))
        columns = {name.replace("&quot;", '"'): index for index, name in enumerate(headers)}
        _seek_plot_time(handle, handle.tell(), start)
        for line in handle:
            if not line.endswith(b"\n"):
                break
            row = next(csv.reader([line.decode("utf-8")]))
            if not row:
                continue
            timestamp = _timestamp(row[0])
            if timestamp > end:
                break
            if timestamp < start:
                continue
            for plot in plots:
                column = columns.get(plot["title"])
                value = None
                if column is not None and column < len(row) and row[column]:
                    try:
                        number = float(row[column])
                        if math.isfinite(number):
                            value = int(number) if plot["kind"] == "bgcolor" else number
                    except ValueError:
                        pass
                plot["data"].append({"time": timestamp, "value": value})
    return plots


def _signature(path: Path):
    try:
        stat = path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


def _merge_live_bar(window: dict, snapshot: dict | None, exchange: str, limit: int, before, after) -> None:
    """Overlay the forming candle at the live edge, never change historical pages."""
    interval = window["interval"]
    if snapshot is None or not interval or before is not None or window["has_after"]:
        return
    bar = snapshot["data"]
    timestamp = bar["time"]
    if not timestamp <= datetime.now(UTC).timestamp() < timestamp + interval:
        return
    if bar["volume"] < 0 or (bar["volume"] == 0 and tradingview_hides_zero_volume(exchange)):
        return
    if after is not None and timestamp <= after:
        return
    bars = window["bars"]
    if bars and timestamp < bars[-1]["time"]:
        return
    if bars and timestamp == bars[-1]["time"]:
        bars[-1] = dict(bar)
    else:
        if after is not None and len(bars) >= limit:
            window["has_after"] = True
            return
        bars.append(dict(bar))
        if len(bars) > limit:
            del bars[0]
            window["has_before"] = True
    window["live_bar_sequence"] = snapshot["sequence"]


def read_chart_window(session, limit=CHART_PAGE_SIZE, before=None, after=None, *, candles_only=False) -> dict:
    snapshot = getattr(getattr(session, "feed", None), "latest_chart_bar", None)
    window = read_candle_window(session.ohlcv_path, session.spec.exchange, limit, before, after)
    _merge_live_bar(window, snapshot, session.spec.exchange, limit, before, after)
    window.update(plots=[], trades=[], plotchars=[], overlays_ready=True)
    if candles_only or not window["bars"] or not session.runner_count:
        return window
    window["overlays_ready"] = False
    if session.runner_phase == "prerun_active":
        return window
    path = session.paths.plot_path
    signature = _signature(path)
    options = dict(session.plot_options)
    start, end = window["bars"][0]["time"], window["bars"][-1]["time"]
    interval = window["interval"]
    plot_end = end
    if interval and end <= datetime.now(UTC).timestamp() < end + interval:
        plot_end = end - 1
    try:
        if options and signature and signature[1]:
            plots = read_plot_window(path, options, start, plot_end)
        elif options:
            return window
        else:
            # runner_ready precedes plot_options on the existing runner wire.
            # A file with plot columns must wait for that metadata message.
            if signature and signature[1]:
                with path.open("rb") as handle:
                    headers = next(csv.reader(line.decode("utf-8-sig") for line in handle))
                if len(headers) > 6:
                    return window
            plots = []
        trades = [event for event in session.trades_history if start <= event["time"] <= end]
        plotchars = [event for event in session.plotchar_history if start <= event["time"] <= end]
    except (OSError, ValueError, StopIteration, csv.Error):
        # A runner may start replacing its output while this worker is reading.
        return window
    if session.runner_phase == "prerun_active" or _signature(path) != signature or options != session.plot_options:
        return window
    window.update(plots=plots, trades=trades, plotchars=plotchars, overlays_ready=True)
    return window
