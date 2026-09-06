from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_CHECKPOINT_INTERVAL = 2048


def _timestamp(value: str) -> int:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).timestamp())


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(slots=True)
class PlotCsvIndex:
    signature: tuple[int, int]
    headers: list[str]
    checkpoints: list[tuple[int, int]]
    row_count: int
    plots: list[dict[str, Any]]


def plot_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _plot_definitions(
    headers: list[str],
    samples: list[list[float]],
    metadata_path: Path | None,
) -> list[dict[str, Any]]:
    palette = ("#2962FF", "#64748B", "#E53935", "#00A884", "#7C3AED", "#F59E0B")
    defaults: list[dict[str, Any]] = []
    for order, title in enumerate(headers[6:]):
        values = samples[order]
        is_encoded_color = bool(values) and all(
            value.is_integer() and 0x01000000 <= value <= 0xFFFFFFFF
            for value in values
        ) and len(set(values)) <= 16
        defaults.append({
            "title": title,
            "kind": "bgcolor" if is_encoded_color else "line",
            "offset": 0,
            "show_last": None,
            "color": palette[order % len(palette)],
            "linewidth": 1,
            "style": None,
            "order": order,
        })

    if metadata_path is not None and metadata_path.is_file():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            plots = payload.get("plots")
            if isinstance(plots, list):
                by_title = {
                    str(plot.get("title")): dict(plot)
                    for plot in plots
                    if isinstance(plot, dict) and plot.get("title") in headers[6:]
                }
                if by_title:
                    return [
                        {**default, **by_title.get(default["title"], {})}
                        for default in defaults
                    ]
        except (OSError, json.JSONDecodeError):
            pass

    return defaults


def build_plot_index(path: Path, metadata_path: Path | None = None) -> PlotCsvIndex:
    signature = plot_signature(path)
    checkpoints: list[tuple[int, int]] = []
    with path.open("rb") as handle:
        header_line = handle.readline()
        headers = next(csv.reader([header_line.decode("utf-8-sig")]))
        samples: list[list[float]] = [[] for _ in headers[6:]]
        row_index = 0
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.strip():
                continue
            if row_index % _CHECKPOINT_INTERVAL == 0:
                checkpoints.append((row_index, offset))
            if samples and any(len(values) < 64 for values in samples):
                fields = line.rstrip(b"\r\n").split(b",")
                for column, values in enumerate(samples, start=6):
                    if len(values) >= 64 or column >= len(fields) or not fields[column]:
                        continue
                    try:
                        number = float(fields[column])
                    except ValueError:
                        continue
                    if math.isfinite(number):
                        values.append(number)
            row_index += 1
    return PlotCsvIndex(
        signature=signature,
        headers=headers,
        checkpoints=checkpoints,
        row_count=row_index,
        plots=_plot_definitions(headers, samples, metadata_path),
    )


def _checkpoint(index: PlotCsvIndex, start_index: int) -> tuple[int, int]:
    selected = index.checkpoints[0]
    for checkpoint in index.checkpoints:
        if checkpoint[0] > start_index:
            break
        selected = checkpoint
    return selected


def _read_bars(
    path: Path,
    index: PlotCsvIndex,
    start_index: int,
    end_index: int,
) -> list[dict[str, Any]]:
    if not index.checkpoints or start_index >= index.row_count:
        return []
    checkpoint_index, offset = _checkpoint(index, start_index)
    bars: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        row_index = checkpoint_index
        while row_index <= end_index:
            line = handle.readline()
            if not line:
                break
            if not line.strip():
                continue
            if row_index >= start_index:
                row = next(csv.reader([line.decode("utf-8")]))
                if len(row) >= 6:
                    try:
                        bar = {
                            "bar_index": row_index,
                            "time": _timestamp(row[0]),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                            "plots": {},
                        }
                        for column, plot in enumerate(index.plots, start=6):
                            if column >= len(row) or not row[column]:
                                continue
                            value = float(row[column])
                            if math.isfinite(value):
                                bar["plots"][plot["title"]] = (
                                    int(value) if plot.get("kind") == "bgcolor" else value
                                )
                        bars.append(bar)
                    except (TypeError, ValueError):
                        pass
            row_index += 1
    return bars


def _read_trade_markers(
    path: Path,
    start_index: int,
    end_index: int,
) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    markers: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        price_field = next(
            (field for field in reader.fieldnames or [] if str(field).startswith("Price ")),
            None,
        )
        for row in reader:
            try:
                bar_index = int(float(str(row.get("Bar Index") or "")))
            except ValueError:
                continue
            if bar_index < start_index or bar_index > end_index:
                continue
            row_type = str(row.get("Type") or "").strip()
            if row_type.startswith("Entry "):
                kind = "entry"
            elif row_type.startswith("Exit "):
                kind = "exit"
            else:
                continue
            try:
                timestamp = _timestamp(str(row.get("Date/Time") or ""))
            except ValueError:
                continue
            signal = str(row.get("Signal") or "").strip()
            key = timestamp, kind, signal
            if key in seen:
                continue
            seen.add(key)
            markers.append({
                "bar_index": bar_index,
                "time": timestamp,
                "kind": kind,
                "direction": (
                    "long" if row_type.endswith(" long")
                    else "short" if row_type.endswith(" short")
                    else ""
                ),
                "signal": signal,
                "trade": str(row.get("Trade #") or "").strip(),
                "price": _number(row.get(price_field)) if price_field else None,
            })
    return markers


def _read_plotchar_markers(
    path: Path,
    start_timestamp: int | None,
    end_timestamp: int | None,
) -> list[dict[str, Any]]:
    if not path.is_file() or start_timestamp is None or end_timestamp is None:
        return []
    markers: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = int(float(str(row.get("Time") or "")))
            except ValueError:
                continue
            if timestamp < start_timestamp or timestamp > end_timestamp:
                continue
            title = str(row.get("Title") or "PlotChar")
            key = timestamp, title
            if key in seen:
                continue
            seen.add(key)
            markers.append({
                "time": timestamp,
                "kind": "plotchar",
                "signal": str(row.get("Text") or row.get("Char") or title),
                "location": str(row.get("Location") or "belowBar"),
                "color": str(row.get("Color") or "#2962FF"),
                "size": _number(row.get("Size")) or 1,
            })
    return markers


def read_chart_window(
    plot_path: Path,
    trades_path: Path,
    plotchars_path: Path,
    index: PlotCsvIndex,
    *,
    start_index: int,
    end_index: int,
) -> dict[str, Any]:
    start = max(0, int(start_index))
    end = min(index.row_count - 1, max(start, int(end_index)))
    bars = _read_bars(plot_path, index, start, end)
    start_timestamp = int(bars[0]["time"]) if bars else None
    end_timestamp = int(bars[-1]["time"]) if bars else None
    return {
        "row_count": index.row_count,
        "start_index": start,
        "end_index": end,
        "bars": bars,
        "plots": index.plots,
        "markers": (
            _read_trade_markers(trades_path, start, end)
            + _read_plotchar_markers(plotchars_path, start_timestamp, end_timestamp)
        ),
    }
