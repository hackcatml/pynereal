from __future__ import annotations

import math
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from ohlcv_cache import delete_bars, import_from_ohlcv
from ohlcv_io import convert_timeframe, download_history_range_to_file
from pynecore.core.ohlcv_file import OHLCVReader


_SQLITE_TIMEOUT_SECONDS = 30.0
_MAX_SAMPLES = 20


class DataIntegrityCancelled(Exception):
    pass


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DataIntegrityCancelled("data integrity operation cancelled")


def _connect(cache_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(cache_path, timeout=_SQLITE_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {int(_SQLITE_TIMEOUT_SECONDS * 1000)}")
    return conn


def _utc_iso(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(int(timestamp), UTC).isoformat()


def _values_differ(local_value: float, reference_value: float, *, volume: bool = False) -> bool:
    relative_tolerance = 2e-5 if volume else 2e-6
    absolute_tolerance = 1e-7 if volume else 1e-8
    return not math.isclose(
        float(local_value),
        float(reference_value),
        rel_tol=relative_tolerance,
        abs_tol=absolute_tolerance,
    )


def _invalid_reasons(row: tuple[Any, ...], interval_seconds: int) -> list[str]:
    timestamp, open_price, high, low, close, volume = row
    reasons: list[str] = []
    values = (open_price, high, low, close, volume)
    if not all(math.isfinite(float(value)) for value in values):
        return ["non-finite value"]
    if int(timestamp) % interval_seconds != 0:
        reasons.append("timestamp is off timeframe grid")
    if min(float(open_price), float(high), float(low), float(close)) <= 0:
        reasons.append("price is not positive")
    if float(volume) < 0:
        reasons.append("volume is negative")
    price_scale = max(abs(float(open_price)), abs(float(high)), abs(float(low)), abs(float(close)), 1.0)
    tolerance = price_scale * 2e-6
    if float(high) + tolerance < max(float(open_price), float(low), float(close)):
        reasons.append("high is below OHLC range")
    if float(low) - tolerance > min(float(open_price), float(high), float(close)):
        reasons.append("low is above OHLC range")
    return reasons


def _append_sample(samples: list[dict[str, Any]], sample: dict[str, Any]) -> None:
    if len(samples) < _MAX_SAMPLES:
        samples.append(sample)


def _timestamp_ranges(timestamps: Iterable[int], interval_seconds: int) -> list[dict[str, Any]]:
    ordered = sorted({int(timestamp) for timestamp in timestamps})
    ranges: list[dict[str, Any]] = []
    if not ordered:
        return ranges
    start = previous = ordered[0]
    count = 1
    for timestamp in ordered[1:]:
        if timestamp == previous + interval_seconds:
            previous = timestamp
            count += 1
            continue
        ranges.append({
            "start": start,
            "end": previous,
            "start_time": _utc_iso(start),
            "end_time": _utc_iso(previous),
            "bars": count,
        })
        start = previous = timestamp
        count = 1
    ranges.append({
        "start": start,
        "end": previous,
        "start_time": _utc_iso(start),
        "end_time": _utc_iso(previous),
        "bars": count,
    })
    return ranges[:_MAX_SAMPLES]


def inspect_data_integrity(
    *,
    cache_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    start_ts: int | None,
    apply_repair: bool = False,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    _raise_if_cancelled(cancel_event)
    interval_seconds = int(convert_timeframe(timeframe, to_ms=True) / 1000)
    now = int(time.time())
    current_bar_start = (now // interval_seconds) * interval_seconds
    confirmed_end = current_bar_start - (2 * interval_seconds)

    with _connect(cache_path) as conn:
        bounds = conn.execute(
            """
            SELECT MIN(ts), MAX(ts)
            FROM bars
            WHERE provider = ? AND exchange = ? AND symbol = ? AND timeframe = ?
              AND (? IS NULL OR ts >= ?) AND ts <= ?
            """,
            (provider, exchange, symbol, timeframe, start_ts, start_ts, confirmed_end),
        ).fetchone()
    local_start = int(bounds[0]) if bounds and bounds[0] is not None else None
    if local_start is None:
        return {
            "status": "no_data",
            "checked_at": datetime.now(UTC).isoformat(),
            "bars_scanned": 0,
            "reference_bars": 0,
            "repairable": False,
            "issues": {},
            "samples": [],
        }

    scan_start = min(local_start, int(start_ts)) if start_ts is not None else local_start
    reference_path: Path
    with TemporaryDirectory() as tmp_dir:
        reference_path = download_history_range_to_file(
            provider=provider,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            time_from=datetime.fromtimestamp(scan_start, UTC),
            time_to=datetime.fromtimestamp(confirmed_end, UTC),
            ohlv_dir=Path(tmp_dir),
            on_progress=lambda _progress: _raise_if_cancelled(cancel_event),
        )
        _raise_if_cancelled(cancel_event)

        repair_timestamps: set[int] = set()
        missing_timestamps: list[int] = []
        samples: list[dict[str, Any]] = []
        counts = {
            "missing_local": 0,
            "extra_local": 0,
            "mismatched": 0,
            "invalid": 0,
            "synthetic_zero_volume": 0,
        }
        local_count = 0
        compared_count = 0

        with _connect(cache_path) as conn, OHLCVReader(reference_path) as reference_reader:
            local_cursor = iter(conn.execute(
                """
                SELECT ts, open, high, low, close, volume
                FROM bars
                WHERE provider = ? AND exchange = ? AND symbol = ? AND timeframe = ?
                  AND ts >= ? AND ts <= ?
                ORDER BY ts
                """,
                (provider, exchange, symbol, timeframe, scan_start, confirmed_end),
            ))
            reference_count = reference_reader.size
            reference_index = 0
            local_row = next(local_cursor, None)
            reference_bar = reference_reader.read(0) if reference_count else None

            while local_row is not None or reference_bar is not None:
                compared_count += 1
                if compared_count % 256 == 0:
                    _raise_if_cancelled(cancel_event)
                local_ts = int(local_row[0]) if local_row is not None else None
                reference_ts = int(reference_bar.timestamp) if reference_bar is not None else None

                if local_row is not None and (reference_ts is None or local_ts < reference_ts):
                    local_count += 1
                    reasons = _invalid_reasons(local_row, interval_seconds)
                    if reasons:
                        counts["invalid"] += 1
                        repair_timestamps.add(local_ts)
                        _append_sample(samples, {
                            "type": "invalid",
                            "timestamp": local_ts,
                            "time": _utc_iso(local_ts),
                            "details": ", ".join(reasons),
                        })
                    if float(local_row[5]) == 0.0 and not reasons:
                        counts["synthetic_zero_volume"] += 1
                    else:
                        counts["extra_local"] += 1
                        repair_timestamps.add(local_ts)
                        _append_sample(samples, {
                            "type": "extra_local",
                            "timestamp": local_ts,
                            "time": _utc_iso(local_ts),
                            "details": "local candle is absent from exchange data",
                        })
                    local_row = next(local_cursor, None)
                    continue

                if reference_bar is not None and (local_ts is None or reference_ts < local_ts):
                    counts["missing_local"] += 1
                    missing_timestamps.append(reference_ts)
                    _append_sample(samples, {
                        "type": "missing_local",
                        "timestamp": reference_ts,
                        "time": _utc_iso(reference_ts),
                        "details": "exchange candle is missing from local cache",
                    })
                    reference_index += 1
                    reference_bar = (
                        reference_reader.read(reference_index)
                        if reference_index < reference_count else None
                    )
                    continue

                assert local_row is not None and reference_bar is not None
                local_count += 1
                reasons = _invalid_reasons(local_row, interval_seconds)
                if reasons:
                    counts["invalid"] += 1
                    repair_timestamps.add(local_ts)
                    _append_sample(samples, {
                        "type": "invalid",
                        "timestamp": local_ts,
                        "time": _utc_iso(local_ts),
                        "details": ", ".join(reasons),
                    })

                field_names = ("open", "high", "low", "close", "volume")
                local_values = local_row[1:]
                reference_values = (
                    reference_bar.open,
                    reference_bar.high,
                    reference_bar.low,
                    reference_bar.close,
                    reference_bar.volume,
                )
                mismatched_fields = [
                    field
                    for index, field in enumerate(field_names)
                    if _values_differ(
                        float(local_values[index]),
                        float(reference_values[index]),
                        volume=field == "volume",
                    )
                ]
                if mismatched_fields:
                    counts["mismatched"] += 1
                    repair_timestamps.add(local_ts)
                    _append_sample(samples, {
                        "type": "mismatched",
                        "timestamp": local_ts,
                        "time": _utc_iso(local_ts),
                        "details": "different " + ", ".join(mismatched_fields),
                    })

                local_row = next(local_cursor, None)
                reference_index += 1
                reference_bar = (
                    reference_reader.read(reference_index)
                    if reference_index < reference_count else None
                )

            reference_reader.close()

        issue_count = sum(counts[key] for key in ("missing_local", "extra_local", "mismatched", "invalid"))
        report: dict[str, Any] = {
            "status": "issues" if issue_count else "verified",
            "checked_at": datetime.now(UTC).isoformat(),
            "range_start": scan_start,
            "range_start_time": _utc_iso(scan_start),
            "range_end": confirmed_end,
            "range_end_time": _utc_iso(confirmed_end),
            "bars_scanned": local_count,
            "reference_bars": reference_count,
            "repairable": issue_count > 0 and reference_count > 0,
            "issues": counts,
            "missing_ranges": _timestamp_ranges(missing_timestamps, interval_seconds),
            "samples": samples,
        }

        if apply_repair and report["repairable"]:
            _raise_if_cancelled(cancel_event)
            deleted = delete_bars(
                cache_path,
                provider,
                exchange,
                symbol,
                timeframe,
                repair_timestamps,
            )
            import_from_ohlcv(
                cache_path,
                provider,
                exchange,
                symbol,
                timeframe,
                reference_path,
            )
            report["status"] = "verified"
            report["repaired_issues"] = counts
            report["issues"] = {
                "missing_local": 0,
                "extra_local": 0,
                "mismatched": 0,
                "invalid": 0,
                "synthetic_zero_volume": counts["synthetic_zero_volume"],
            }
            report["bars_scanned"] = reference_count + counts["synthetic_zero_volume"]
            report["repairable"] = False
            report["missing_ranges"] = []
            report["samples"] = []
            report["repair_applied"] = True
            report["deleted_bars"] = deleted
            report["imported_reference_bars"] = reference_count
        else:
            report["repair_applied"] = False
        return report
