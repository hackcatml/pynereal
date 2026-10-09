from __future__ import annotations

import json
import re
import tempfile
import threading
from pathlib import Path


_FIELDS = {
    "sma": (1, ("period",)),
    "ema": (1, ("period",)),
    "bb": (3, ("period", "multiplier")),
    "rsi": (1, ("period",)),
    "macd": (4, ("fastPeriod", "slowPeriod", "signalPeriod")),
    "smi": (2, ("period", "smooth1", "smooth2", "signalPeriod")),
    "vwap": (1, ()),
}


def validate_indicator_settings(settings: object) -> list[dict]:
    if not isinstance(settings, list) or len(settings) > len(_FIELDS):
        raise ValueError("settings must be a list of at most seven indicators")
    result, seen = [], set()
    for item in settings:
        if not isinstance(item, dict):
            raise ValueError("each indicator must be an object")
        name = item.get("id")
        if not isinstance(name, str) or name not in _FIELDS or name in seen:
            raise ValueError("unknown or duplicate indicator")
        seen.add(name)
        color_count, fields = _FIELDS[name]
        required = {"id", "enabled", "colors", *fields}
        optional = {"collapsed"} if name in {"rsi", "macd", "smi"} else set()
        if name in {"sma", "ema"}:
            optional.add("additionalLines")
        if not required <= set(item) or set(item) - required - optional:
            raise ValueError(f"invalid fields for {name}")
        if not isinstance(item["enabled"], bool):
            raise ValueError("enabled must be boolean")
        if "collapsed" in item and not isinstance(item["collapsed"], bool):
            raise ValueError("collapsed must be boolean")
        colors = item["colors"]
        if not isinstance(colors, list) or len(colors) != color_count or any(
            not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
            for color in colors
        ):
            raise ValueError(f"invalid colors for {name}")
        for field in fields:
            value = item[field]
            fractional = field == "multiplier"
            low, high = (0.1, 10) if fractional else (2 if field == "period" else 1, 500)
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not low <= value <= high
                or (not fractional and value != int(value))
            ):
                raise ValueError(f"invalid {field} for {name}")
        if name == "macd" and item["fastPeriod"] >= item["slowPeriod"]:
            raise ValueError("MACD fast period must be smaller than slow period")
        normalized = dict(item, colors=list(colors))
        if "additionalLines" in item:
            lines = item["additionalLines"]
            if not isinstance(lines, list) or len(lines) > 9:
                raise ValueError("SMA and EMA support at most ten lines each")
            for line in lines:
                if not isinstance(line, dict) or set(line) != {"period", "color"}:
                    raise ValueError("invalid moving average line")
                period, color = line["period"], line["color"]
                if (
                    isinstance(period, bool) or not isinstance(period, (int, float))
                    or not 2 <= period <= 500 or period != int(period)
                    or not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
                ):
                    raise ValueError("invalid moving average length or color")
            normalized["additionalLines"] = [dict(line) for line in lines]
        result.append(normalized)
    return result


class ChartIndicatorSettings:
    """Small per-session preferences, read/written only by chart HTTP requests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", session_id):
            raise ValueError("invalid session ID")
        return self.root / f"{session_id}.json"

    def _read(self, path: Path) -> list[dict] | None:
        try:
            with path.open(encoding="utf-8") as handle:
                return validate_indicator_settings(json.load(handle))
        except FileNotFoundError:
            return None

    def get(self, session_id: str) -> list[dict] | None:
        with self._lock:
            return self._read(self._path(session_id))

    def save(self, session_id: str, settings: object, *, initialize_only: bool = False) -> list[dict]:
        validated = validate_indicator_settings(settings)
        with self._lock:
            path = self._path(session_id)
            if initialize_only:
                existing = self._read(path)
                if existing is not None:
                    return existing
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(validated, handle, ensure_ascii=True, allow_nan=False)
                    handle.write("\n")
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return validated
