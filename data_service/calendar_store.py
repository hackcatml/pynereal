from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_CATEGORIES = {"earnings", "economic", "company", "market", "dividend", "other"}
_MAX_EVENTS = 2000


class CalendarStoreError(ValueError):
    """Calendar data was invalid or could not be persisted."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        for key in ("session_id", "date", "time", "title", "source_url")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _sanitize_event(raw: object, *, session_id: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CalendarStoreError("each calendar event must be an object")

    resolved_session_id = session_id or _text(
        raw.get("session_id"), "session_id", required=True, max_length=200
    )
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
        "session_id": resolved_session_id,
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
    event["id"] = _event_id(event | {"id": raw.get("id")})
    return event


class CalendarEventStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._events: list[dict[str, Any]] = []
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

        return [
            dict(event)
            for event in self._events
            if (allowed is None or event["session_id"] in allowed)
            and (start_date is None or event["date"] >= start_date)
            and (end_date is None or event["date"] <= end_date)
        ]

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

        retained = [
            event for event in self._events
            if not (
                event["session_id"] in target_ids
                and start_date <= event["date"] <= end_date
            )
        ]
        combined = retained + replacements
        unique: dict[str, dict[str, Any]] = {event["id"]: event for event in combined}
        if len(unique) > _MAX_EVENTS:
            raise CalendarStoreError(f"calendar can contain at most {_MAX_EVENTS} events")

        next_events = sorted(
            unique.values(),
            key=lambda event: (
                event["date"], event.get("time") or "", event["session_id"], event["title"]
            ),
        )
        next_updated_at = _utc_now()
        previous_events = self._events
        previous_updated_at = self.updated_at
        self._events = next_events
        self.updated_at = next_updated_at
        try:
            self._save()
        except Exception:
            self._events = previous_events
            self.updated_at = previous_updated_at
            raise
        return {
            "range_start": start_date,
            "range_end": end_date,
            "updated_session_ids": sorted(target_ids),
            "saved_event_count": len(replacements),
            "events": [dict(event) for event in replacements],
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
            self._events = sorted(
                events,
                key=lambda event: (
                    event["date"], event.get("time") or "", event["session_id"], event["title"]
                ),
            )
            self.updated_at = str(payload.get("updated_at") or "") if isinstance(payload, dict) else ""
        except Exception as exc:
            print(f"[calendar] failed to load {self.path.name}: {exc}")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": self.updated_at,
            "events": self._events,
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
