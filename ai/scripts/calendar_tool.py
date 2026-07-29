from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from data_service.calendar_store import CalendarEventStore, CalendarStoreError


_CONTEXT_TOOL_NAME = "get_calendar_context"
_REPLACE_TOOL_NAME = "replace_calendar_events"
_MAX_SESSIONS_PER_UPDATE = 20
_MAX_EVENTS_PER_UPDATE = 500


class CalendarToolError(ValueError):
    """A calendar tool request was rejected before changing persisted data."""


class CalendarRegistryBridge:
    def __init__(
        self,
        registry: Any,
        store: CalendarEventStore,
        *,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._registry = registry
        self._store = store
        self._timeout_seconds = timeout_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread_id = threading.get_ident()

    def unbind_loop(self) -> None:
        self._loop = None
        self._loop_thread_id = None

    def execute(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise CalendarToolError("Calendar service is not running")
        if threading.get_ident() == self._loop_thread_id:
            raise CalendarToolError("Calendar tool cannot block the data-service event loop")
        future = asyncio.run_coroutine_threadsafe(
            self._execute_on_loop(operation, arguments),
            loop,
        )
        try:
            return future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            raise CalendarToolError("Calendar operation timed out") from None

    async def _execute_on_loop(
        self,
        operation: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "context":
            return self._context()
        if operation == "replace":
            return await self._replace(arguments)
        raise CalendarToolError(f"Unknown Calendar operation: {operation}")

    def _context(self) -> dict[str, Any]:
        active_ids = list(self._registry.sessions)
        events = self._store.list_events(active_session_ids=active_ids)
        events_by_session: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            for session_id in event["session_ids"]:
                events_by_session.setdefault(session_id, []).append(event)

        sessions: list[dict[str, Any]] = []
        for session in self._registry.sessions.values():
            spec = session.spec
            script_title = str(session.chart_info.get("script_title") or "").strip()
            if not script_title and spec.script_name:
                script_title = Path(spec.script_name).stem
            sessions.append({
                "session_id": spec.id,
                "exchange": spec.exchange,
                "symbol": spec.symbol,
                "base_asset": str(spec.symbol).split("/", 1)[0],
                "timeframe": spec.timeframe,
                "script_name": spec.script_name,
                "script_title": script_title,
                "tv_symbol": str(session.logo_info.get("tv_symbol") or ""),
                "existing_events": events_by_session.get(spec.id, []),
            })
        return {
            "session_count": len(sessions),
            "sessions": sessions,
            "calendar_updated_at": self._store.updated_at,
        }

    async def _replace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._store.replace_range(
            start=arguments["range_start"],
            end=arguments["range_end"],
            session_events=arguments["session_events"],
            active_session_ids=self._registry.sessions,
        )
        await self._registry.hub_ws.broadcast_json({"type": "calendar_updated"})
        result["updated_sessions"] = [
            self._session_summary(session_id)
            for session_id in result["updated_session_ids"]
        ]
        return result

    def _session_summary(self, session_id: str) -> dict[str, Any]:
        session = self._registry.get(session_id)
        return {
            "session_id": session_id,
            "exchange": session.spec.exchange if session is not None else "",
            "symbol": session.spec.symbol if session is not None else "",
            "timeframe": session.spec.timeframe if session is not None else "",
        }


class CalendarTools:
    def __init__(self, registry: Any, store: CalendarEventStore) -> None:
        self.bridge = CalendarRegistryBridge(registry, store)

    @property
    def names(self) -> set[str]:
        return {_CONTEXT_TOOL_NAME, _REPLACE_TOOL_NAME}

    @property
    def specs(self) -> list[dict[str, Any]]:
        event_properties = {
            "date": {
                "type": "string",
                "description": "Verified event date in YYYY-MM-DD format.",
            },
            "time": {
                "type": "string",
                "description": "Optional verified local event time in HH:MM 24-hour format.",
            },
            "timezone": {
                "type": "string",
                "description": "Timezone for time, such as Asia/Seoul or America/New_York.",
            },
            "title": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "Short user-facing schedule title.",
            },
            "details": {
                "type": "string",
                "maxLength": 4000,
                "description": "Verified event details shared by every affected session.",
            },
            "category": {
                "type": "string",
                "enum": ["earnings", "economic", "company", "market", "dividend", "other"],
            },
            "source_name": {
                "type": "string",
                "maxLength": 120,
                "description": "Concise source name.",
            },
            "source_url": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "Public URL used to verify the date and details.",
            },
        }
        return [
            {
                "type": "function",
                "name": _CONTEXT_TOOL_NAME,
                "description": (
                    "List all active PyneReal sessions and their existing calendar events. "
                    "Call this first for every calendar lookup or refresh. Resolve company, "
                    "asset, symbol, exchange, timeframe, or strategy descriptions to the exact "
                    "session IDs returned here; never ask the user to provide a session ID."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": _REPLACE_TOOL_NAME,
                "description": (
                    "Atomically replace verified calendar events inside one date range for one or "
                    "more active sessions. Include a session with an empty events array when the "
                    "range was researched and no relevant events were found, so stale entries are "
                    "removed. Events outside the range and events for omitted sessions are preserved. "
                    "Events with the same date, time, and normalized title are stored once with all "
                    "affected sessions linked to that shared event."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "range_start": {"type": "string", "description": "YYYY-MM-DD inclusive."},
                        "range_end": {"type": "string", "description": "YYYY-MM-DD inclusive."},
                        "session_events": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": _MAX_SESSIONS_PER_UPDATE,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "session_id": {
                                        "type": "string",
                                        "minLength": 1,
                                        "description": "Exact active session ID from get_calendar_context.",
                                    },
                                    "events": {
                                        "type": "array",
                                        "maxItems": _MAX_EVENTS_PER_UPDATE,
                                        "items": {
                                            "type": "object",
                                            "properties": event_properties,
                                            "required": ["date", "title", "source_url"],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": ["session_id", "events"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["range_start", "range_end", "session_events"],
                    "additionalProperties": False,
                },
            },
        ]

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.bridge.bind_loop(loop)

    def unbind_loop(self) -> None:
        self.bridge.unbind_loop()

    def handle_server_request(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method != "item/tool/call" or not isinstance(params, dict):
            return {}
        tool = params.get("tool")
        if tool not in self.names:
            return {}
        try:
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                raise CalendarToolError("Tool arguments must be an object")
            if tool == _CONTEXT_TOOL_NAME:
                if arguments:
                    raise CalendarToolError("get_calendar_context does not accept arguments")
                result = self.bridge.execute("context", arguments)
            else:
                validated = self._validate_replace(arguments)
                result = self.bridge.execute("replace", validated)
        except (CalendarToolError, CalendarStoreError) as exc:
            return self._tool_response(False, {"error": str(exc)})
        except Exception as exc:
            print(f"[ai] Calendar tool failed: {type(exc).__name__}")
            return self._tool_response(
                False,
                {"error": f"Calendar operation failed ({type(exc).__name__})"},
            )
        if tool == _REPLACE_TOOL_NAME:
            print(
                f"[ai] Calendar updated sessions={len(result['updated_session_ids'])} "
                f"events={result['saved_event_count']} range={result['range_start']}..{result['range_end']}"
            )
        return self._tool_response(True, result)

    @staticmethod
    def _validate_replace(arguments: dict[str, Any]) -> dict[str, Any]:
        unexpected = sorted(set(arguments) - {"range_start", "range_end", "session_events"})
        if unexpected:
            raise CalendarToolError(f"Unexpected argument field(s): {', '.join(unexpected)}")
        for field in ("range_start", "range_end"):
            if not isinstance(arguments.get(field), str) or not arguments[field].strip():
                raise CalendarToolError(f"{field} must be a non-empty string")
        session_events = arguments.get("session_events")
        if not isinstance(session_events, list) or not session_events:
            raise CalendarToolError("session_events must be a non-empty array")
        if len(session_events) > _MAX_SESSIONS_PER_UPDATE:
            raise CalendarToolError(
                f"session_events can contain at most {_MAX_SESSIONS_PER_UPDATE} sessions"
            )
        total_events = 0
        for item in session_events:
            if not isinstance(item, dict):
                raise CalendarToolError("each session_events item must be an object")
            if set(item) - {"session_id", "events"}:
                raise CalendarToolError("session_events items contain unsupported fields")
            if not isinstance(item.get("session_id"), str) or not item["session_id"].strip():
                raise CalendarToolError("session_id must be a non-empty string")
            events = item.get("events")
            if not isinstance(events, list):
                raise CalendarToolError("events must be an array")
            total_events += len(events)
            for event in events:
                if not isinstance(event, dict):
                    raise CalendarToolError("each event must be an object")
                if not isinstance(event.get("source_url"), str) or not event["source_url"].strip():
                    raise CalendarToolError("each event requires a source_url")
        if total_events > _MAX_EVENTS_PER_UPDATE:
            raise CalendarToolError(
                f"one update can contain at most {_MAX_EVENTS_PER_UPDATE} events"
            )
        validated = dict(arguments)
        validated["range_start"] = arguments["range_start"].strip()
        validated["range_end"] = arguments["range_end"].strip()
        return validated

    @staticmethod
    def _tool_response(success: bool, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": success,
            "contentItems": [{
                "type": "inputText",
                "text": json.dumps(payload, ensure_ascii=False),
            }],
        }
