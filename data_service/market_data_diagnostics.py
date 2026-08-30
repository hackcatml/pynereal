from __future__ import annotations

import atexit
import json
import math
import os
import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DIAGNOSTIC_LOG_NAME = "market_data_diagnostics.jsonl"
_MAX_LOG_BYTES = 20 * 1024 * 1024
_BACKUP_COUNT = 3
_QUEUE_SIZE = 10_000


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else str(number)


def ohlcv_bar_data(bar: Any) -> dict[str, int | float] | None:
    try:
        if bar is None:
            return None
        if isinstance(bar, (list, tuple)) and len(bar) >= 6:
            timestamp = int(bar[0])
            if timestamp < 10_000_000_000:
                timestamp *= 1000
            values = bar[1:6]
        else:
            timestamp = int(getattr(bar, "timestamp")) * 1000
            values = (
                getattr(bar, "open"),
                getattr(bar, "high"),
                getattr(bar, "low"),
                getattr(bar, "close"),
                getattr(bar, "volume"),
            )
        return {
            "timestamp_ms": timestamp,
            "time_utc": datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(),
            "open": float(values[0]),
            "high": float(values[1]),
            "low": float(values[2]),
            "close": float(values[3]),
            "volume": float(values[4]),
        }
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
        return None


class _DiagnosticWriter:
    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Path, dict[str, Any]] | None] = queue.Queue(
            maxsize=_QUEUE_SIZE
        )
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._dropped = 0

    def enqueue(self, path: Path, record: dict[str, Any]) -> None:
        self._ensure_started()
        try:
            self._queue.put_nowait((path, record))
        except queue.Full:
            self._dropped += 1

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="market-data-diagnostics",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            path, record = item
            try:
                if self._dropped:
                    record["writer_dropped_events"] = self._dropped
                    self._dropped = 0
                self._write(path, record)
            except Exception:
                # Diagnostics must never interrupt market-data or strategy execution.
                pass
            finally:
                self._queue.task_done()

    @staticmethod
    def _rotate(path: Path) -> None:
        if not path.exists() or path.stat().st_size < _MAX_LOG_BYTES:
            return
        oldest = path.with_name(f"{path.name}.{_BACKUP_COUNT}")
        oldest.unlink(missing_ok=True)
        for index in range(_BACKUP_COUNT - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate(path)
        line = json.dumps(_json_safe(record), ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")

    def close(self) -> None:
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        try:
            self._queue.put(None, timeout=1.0)
            thread.join(timeout=2.0)
        except Exception:
            pass


_WRITER = _DiagnosticWriter()
atexit.register(_WRITER.close)


def log_session_diagnostic(
    output_dir: Path | str,
    session_id: str,
    event: dict[str, Any],
    *,
    feed: dict[str, Any] | None = None,
) -> None:
    try:
        event_type = str(event.get("event") or event.get("type") or "unknown")
        payload = dict(event)
        payload.pop("type", None)
        payload["event"] = event_type
        record = {
            "schema_version": 1,
            "observed_at": utc_now_iso(),
            "monotonic_ns": time.monotonic_ns(),
            "session_id": session_id,
            "feed": feed or {},
            **payload,
        }
        # Freeze mutable bar/event lists before the background writer sees them.
        _WRITER.enqueue(Path(output_dir) / DIAGNOSTIC_LOG_NAME, _json_safe(record))
    except Exception:
        pass
