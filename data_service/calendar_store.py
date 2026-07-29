from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_CATEGORIES = {"earnings", "economic", "company", "market", "dividend", "other"}
_MAX_EVENTS = 2000
_MAX_FORECAST_CHARS = 40_000


class CalendarStoreError(ValueError):
    """Calendar data was invalid or could not be persisted."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_precise() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date_value(raw: object, field: str) -> str:
    value = str(raw or "").strip()
    if not _DATE_RE.fullmatch(value):
        raise CalendarStoreError(f"{field} must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise CalendarStoreError(f"{field} is not a valid date") from None
    return value


def _text(raw: object, field: str, *, required: bool, max_length: int) -> str:
    if raw is None:
        value = ""
    elif isinstance(raw, str):
        value = raw.strip()
    else:
        raise CalendarStoreError(f"{field} must be a string")
    if required and not value:
        raise CalendarStoreError(f"{field} is required")
    if len(value) > max_length:
        raise CalendarStoreError(f"{field} exceeds {max_length} characters")
    return value


def _event_id(event: dict[str, Any]) -> str:
    raw = event.get("id")
    if isinstance(raw, str) and _EVENT_ID_RE.fullmatch(raw.strip()):
        return raw.strip()
    identity = "\x1f".join(
        str(event.get(key) or "")
        for key in ("date", "time", "title")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _event_key(event: dict[str, Any]) -> tuple[str, str, str]:
    title = unicodedata.normalize("NFKC", str(event.get("title") or ""))
    normalized_title = " ".join(title.split()).casefold()
    return (
        str(event.get("date") or ""),
        str(event.get("time") or ""),
        normalized_title,
    )


def _session_ids(raw: dict[str, Any], *, session_id: str | None = None) -> list[str]:
    if session_id is not None:
        return [_text(session_id, "session_id", required=True, max_length=200)]

    values = raw.get("session_ids")
    if values is None:
        values = [raw.get("session_id")]
    if not isinstance(values, list):
        raise CalendarStoreError("session_ids must be an array")

    result: list[str] = []
    for value in values:
        resolved = _text(value, "session_id", required=True, max_length=200)
        if resolved not in result:
            result.append(resolved)
    if not result:
        raise CalendarStoreError("calendar event requires at least one session")
    return result


def _with_session_ids(event: dict[str, Any], session_ids: Iterable[str]) -> dict[str, Any]:
    ids = sorted(set(session_ids))
    if not ids:
        raise CalendarStoreError("calendar event requires at least one session")
    updated = dict(event)
    updated["session_id"] = ids[0]
    updated["session_ids"] = ids
    return updated


def _copy_event(event: dict[str, Any]) -> dict[str, Any]:
    copied = dict(event)
    copied["session_ids"] = list(event["session_ids"])
    return copied


def _merge_event(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    # A refresh should update shared event metadata while preserving its stable ID.
    merged = dict(incoming)
    merged["id"] = existing["id"]
    return _with_session_ids(
        merged,
        [*existing["session_ids"], *incoming["session_ids"]],
    )


def _coalesce_events(
    events: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    for event in events:
        key = _event_key(event)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = _copy_event(event)
            aliases[event["id"]] = event["id"]
            continue
        merged = _merge_event(existing, event)
        by_key[key] = merged
        aliases[event["id"]] = merged["id"]
        aliases[existing["id"]] = merged["id"]
    return list(by_key.values()), aliases


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        event["date"],
        event.get("time") or "",
        event["title"],
        event["session_id"],
    )


def _sanitize_event(raw: object, *, session_id: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CalendarStoreError("each calendar event must be an object")

    resolved_session_ids = _session_ids(raw, session_id=session_id)
    event_date = _date_value(raw.get("date"), "event date")
    event_time = _text(raw.get("time"), "time", required=False, max_length=5)
    if event_time and not _TIME_RE.fullmatch(event_time):
        raise CalendarStoreError("time must use HH:MM in 24-hour format")

    source_url = _text(raw.get("source_url"), "source_url", required=False, max_length=2048)
    if source_url:
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CalendarStoreError("source_url must be an http or https URL")

    category = _text(raw.get("category"), "category", required=False, max_length=32).lower()
    if category not in _CATEGORIES:
        category = "other"

    event: dict[str, Any] = {
        "date": event_date,
        "time": event_time,
        "timezone": _text(raw.get("timezone"), "timezone", required=False, max_length=64),
        "title": _text(raw.get("title"), "title", required=True, max_length=200),
        "details": _text(raw.get("details"), "details", required=False, max_length=4000),
        "category": category,
        "source_name": _text(raw.get("source_name"), "source_name", required=False, max_length=120),
        "source_url": source_url,
        "updated_at": _text(raw.get("updated_at"), "updated_at", required=False, max_length=40) or _utc_now(),
    }
    event = _with_session_ids(event, resolved_session_ids)
    event["id"] = _event_id(event | {"id": raw.get("id")})
    return event


def _sanitize_forecast(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise CalendarStoreError("forecast must be an object")
    return {
        "answer": _text(
            raw.get("answer"),
            "forecast answer",
            required=True,
            max_length=_MAX_FORECAST_CHARS,
        ),
        "updated_at": _text(
            raw.get("updated_at"),
            "forecast updated_at",
            required=False,
            max_length=40,
        ) or _utc_now_precise(),
        "viewed_at": _text(
            raw.get("viewed_at"),
            "forecast viewed_at",
            required=False,
            max_length=40,
        ),
    }


class CalendarEventStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._events: list[dict[str, Any]] = []
        self._forecasts: dict[str, dict[str, str]] = {}
        self.updated_at = ""
        self._load()

    def list_events(
        self,
        *,
        active_session_ids: Iterable[str] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        allowed = set(active_session_ids) if active_session_ids is not None else None
        start_date = _date_value(start, "start") if start else None
        end_date = _date_value(end, "end") if end else None
        if start_date and end_date and start_date > end_date:
            raise CalendarStoreError("start must not be after end")

        result: list[dict[str, Any]] = []
        for event in self._events:
            if start_date is not None and event["date"] < start_date:
                continue
            if end_date is not None and event["date"] > end_date:
                continue
            session_ids = event["session_ids"]
            if allowed is not None:
                session_ids = [value for value in session_ids if value in allowed]
                if not session_ids:
                    continue
            result.append(_with_session_ids(event, session_ids))
        return result

    def get_event(
        self,
        event_id: str,
        *,
        active_session_ids: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        allowed = set(active_session_ids) if active_session_ids is not None else None
        for event in self._events:
            if event["id"] != event_id:
                continue
            session_ids = event["session_ids"]
            if allowed is not None:
                session_ids = [value for value in session_ids if value in allowed]
                if not session_ids:
                    return None
            return _with_session_ids(event, session_ids)
        return None

    def get_forecast(self, event_id: str) -> dict[str, str] | None:
        forecast = self._forecasts.get(event_id)
        return dict(forecast) if forecast is not None else None

    def set_forecast(
        self,
        event_id: str,
        answer: str,
        *,
        active_session_ids: Iterable[str],
    ) -> dict[str, str]:
        event = self.get_event(event_id, active_session_ids=active_session_ids)
        if event is None:
            raise CalendarStoreError("calendar event is not active or no longer exists")
        forecast = _sanitize_forecast({"answer": answer})
        previous_forecasts = self._forecasts
        previous_updated_at = self.updated_at
        self._forecasts = {**self._forecasts, event_id: forecast}
        self.updated_at = _utc_now()
        try:
            self._save()
        except Exception:
            self._forecasts = previous_forecasts
            self.updated_at = previous_updated_at
            raise
        return dict(forecast)

    def mark_forecast_viewed(
        self,
        event_id: str,
        *,
        active_session_ids: Iterable[str],
    ) -> dict[str, str]:
        event = self.get_event(event_id, active_session_ids=active_session_ids)
        forecast = self._forecasts.get(event_id)
        if event is None or forecast is None:
            raise CalendarStoreError("calendar forecast is not active or no longer exists")
        if forecast.get("viewed_at"):
            return dict(forecast)

        viewed_forecast = {**forecast, "viewed_at": _utc_now_precise()}
        previous_forecasts = self._forecasts
        previous_updated_at = self.updated_at
        self._forecasts = {**self._forecasts, event_id: viewed_forecast}
        self.updated_at = _utc_now()
        try:
            self._save()
        except Exception:
            self._forecasts = previous_forecasts
            self.updated_at = previous_updated_at
            raise
        return dict(viewed_forecast)

    def replace_range(
        self,
        *,
        start: str,
        end: str,
        session_events: list[dict[str, Any]],
        active_session_ids: Iterable[str],
    ) -> dict[str, Any]:
        start_date = _date_value(start, "range_start")
        end_date = _date_value(end, "range_end")
        if start_date > end_date:
            raise CalendarStoreError("range_start must not be after range_end")
        if not isinstance(session_events, list) or not session_events:
            raise CalendarStoreError("session_events must be a non-empty array")

        active = set(active_session_ids)
        target_ids: set[str] = set()
        replacements: list[dict[str, Any]] = []
        for item in session_events:
            if not isinstance(item, dict):
                raise CalendarStoreError("each session_events item must be an object")
            session_id = _text(item.get("session_id"), "session_id", required=True, max_length=200)
            if session_id not in active:
                raise CalendarStoreError(f"session_id is not active: {session_id}")
            if session_id in target_ids:
                raise CalendarStoreError(f"session_id appears more than once: {session_id}")
            target_ids.add(session_id)

            raw_events = item.get("events")
            if not isinstance(raw_events, list):
                raise CalendarStoreError("events must be an array")
            for raw_event in raw_events:
                event = _sanitize_event(raw_event, session_id=session_id)
                if not start_date <= event["date"] <= end_date:
                    raise CalendarStoreError(
                        f"event date {event['date']} is outside the replacement range"
                    )
                replacements.append(event)

        existing_by_key = {_event_key(event): event for event in self._events}
        for event in replacements:
            existing = existing_by_key.get(_event_key(event))
            if existing is not None:
                event["id"] = existing["id"]

        retained: list[dict[str, Any]] = []
        for event in self._events:
            if not start_date <= event["date"] <= end_date:
                retained.append(event)
                continue
            remaining_ids = [
                session_id
                for session_id in event["session_ids"]
                if session_id not in target_ids
            ]
            if remaining_ids:
                retained.append(_with_session_ids(event, remaining_ids))

        combined, _ = _coalesce_events([*retained, *replacements])
        if len(combined) > _MAX_EVENTS:
            raise CalendarStoreError(f"calendar can contain at most {_MAX_EVENTS} events")

        event_ids = {event["id"] for event in combined}
        next_events = sorted(combined, key=_event_sort_key)
        next_forecasts = {
            event_id: forecast
            for event_id, forecast in self._forecasts.items()
            if event_id in event_ids
        }
        next_updated_at = _utc_now()
        previous_events = self._events
        previous_forecasts = self._forecasts
        previous_updated_at = self.updated_at
        self._events = next_events
        self._forecasts = next_forecasts
        self.updated_at = next_updated_at
        try:
            self._save()
        except Exception:
            self._events = previous_events
            self._forecasts = previous_forecasts
            self.updated_at = previous_updated_at
            raise
        replacement_keys = {_event_key(event) for event in replacements}
        saved_events = [
            _copy_event(event)
            for event in next_events
            if _event_key(event) in replacement_keys
        ]
        return {
            "range_start": start_date,
            "range_end": end_date,
            "updated_session_ids": sorted(target_ids),
            "saved_event_count": len(saved_events),
            "session_event_link_count": len(replacements),
            "events": saved_events,
            "updated_at": self.updated_at,
        }

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            raw_events = payload.get("events", []) if isinstance(payload, dict) else []
            events: list[dict[str, Any]] = []
            if isinstance(raw_events, list):
                for raw in raw_events[:_MAX_EVENTS]:
                    try:
                        events.append(_sanitize_event(raw))
                    except CalendarStoreError as exc:
                        print(f"[calendar] skipped invalid stored event: {exc}")
            events, event_aliases = _coalesce_events(events)
            self._events = sorted(events, key=_event_sort_key)
            valid_event_ids = {event["id"] for event in self._events}
            raw_forecasts = payload.get("forecasts", {}) if isinstance(payload, dict) else {}
            forecasts: dict[str, dict[str, str]] = {}
            if isinstance(raw_forecasts, dict):
                for event_id, raw_forecast in raw_forecasts.items():
                    resolved_event_id = event_aliases.get(event_id, event_id)
                    if resolved_event_id not in valid_event_ids:
                        continue
                    try:
                        forecast = _sanitize_forecast(raw_forecast)
                        existing = forecasts.get(resolved_event_id)
                        if existing is None or forecast["updated_at"] >= existing["updated_at"]:
                            forecasts[resolved_event_id] = forecast
                    except CalendarStoreError as exc:
                        print(f"[calendar] skipped invalid stored forecast: {exc}")
            self._forecasts = forecasts
            self.updated_at = str(payload.get("updated_at") or "") if isinstance(payload, dict) else ""
        except Exception as exc:
            print(f"[calendar] failed to load {self.path.name}: {exc}")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 4,
            "updated_at": self.updated_at,
            "events": self._events,
            "forecasts": self._forecasts,
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
