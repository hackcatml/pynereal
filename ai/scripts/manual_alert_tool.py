from __future__ import annotations

import asyncio
import json
import math
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from data_service.config import sanitize_manual_alert_templates

_CONTEXT_TOOL_NAME = "get_manual_alert_context"
_SET_TOOL_NAME = "set_manual_alert_trigger"
_DELETE_TOOL_NAME = "delete_manual_alert_triggers"
_MAX_MANUAL_ALERT_TRIGGERS = 50
_MAX_MANUAL_ALERT_TEMPLATES = 50
_TEMPLATE_MESSAGE_PREVIEW_CHARS = 500
_TEMPLATE_AI_PREVIEW_CHARS = 500


class ManualAlertToolError(ValueError):
    """A manual-alert tool request rejected before changing session state."""


class ManualAlertRegistryBridge:
    """Run registry access on the data-service event loop from the SDK reader thread."""

    def __init__(self, registry: Any, *, timeout_seconds: float = 15.0) -> None:
        self._registry = registry
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
            raise ManualAlertToolError("Manual Alert service is not running")
        if threading.get_ident() == self._loop_thread_id:
            raise ManualAlertToolError("Manual Alert tool cannot block the data-service event loop")

        future = asyncio.run_coroutine_threadsafe(
            self._execute_on_loop(operation, arguments),
            loop,
        )
        try:
            return future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            raise ManualAlertToolError("Manual Alert operation timed out") from None

    async def _execute_on_loop(
        self,
        operation: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "context":
            return self._context()
        if operation == "set_trigger":
            return await self._set_trigger(arguments)
        if operation == "delete_triggers":
            return await self._delete_triggers(arguments)
        raise ManualAlertToolError(f"Unknown Manual Alert operation: {operation}")

    def _context(self) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for session in self._registry.sessions.values():
            spec = session.spec
            script_title = str(session.chart_info.get("script_title") or "").strip()
            if not script_title and spec.script_name:
                script_title = Path(spec.script_name).stem
            base_asset = str(spec.symbol).split("/", 1)[0]

            templates = [
                {
                    "index": index,
                    "title": str(template.get("title") or ""),
                    "message_preview": str(template.get("message") or "")[
                        :_TEMPLATE_MESSAGE_PREVIEW_CHARS
                    ],
                    "ai_instruction_preview": str(template.get("ai") or "")[
                        :_TEMPLATE_AI_PREVIEW_CHARS
                    ],
                }
                for index, template in enumerate(spec.manual_alert_templates)
            ]
            active_triggers = [
                {
                    "id": str(trigger.get("id") or ""),
                    "price": trigger.get("price"),
                    "template_title": str((trigger.get("template") or {}).get("title") or ""),
                }
                for trigger in spec.manual_alert_triggers
                if trigger.get("enabled")
            ]
            sessions.append({
                "session_id": spec.id,
                "provider": spec.provider,
                "exchange": spec.exchange,
                "symbol": spec.symbol,
                "base_asset": base_asset,
                "market_type": spec.market_type,
                "timeframe": spec.timeframe,
                "script_name": spec.script_name,
                "script_title": script_title,
                "tv_symbol": str(session.logo_info.get("tv_symbol") or ""),
                "last_price": session.feed.last_price(),
                "templates": templates,
                "active_triggers": active_triggers,
            })

        return {
            "session_count": len(sessions),
            "sessions": sessions,
        }

    async def _set_trigger(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = arguments["session_id"].strip()
        session = self._registry.get(session_id)
        if session is None:
            raise ManualAlertToolError(
                "session_id is not an active session; refresh Manual Alert context"
            )

        price = float(arguments["price"])
        templates = [dict(template) for template in session.spec.manual_alert_templates]
        template_index = arguments.get("template_index")
        custom_title = arguments.get("custom_template_title")
        custom_message = arguments.get("custom_template_message")
        custom_ai = arguments.get("custom_template_ai")

        has_custom_template = (
            custom_title is not None
            or custom_message is not None
            or custom_ai is not None
        )
        if template_index is not None and has_custom_template:
            raise ManualAlertToolError(
                "use either template_index or custom template fields, not both"
            )

        template_added = False
        if template_index is not None:
            if template_index >= len(templates):
                raise ManualAlertToolError(
                    "template_index is out of date; refresh Manual Alert context"
                )
            template = templates[template_index]
            template_source = "configured"
        elif has_custom_template:
            sanitized = sanitize_manual_alert_templates([{
                "title": custom_title,
                "message": custom_message,
                "ai": custom_ai or "",
            }])
            if len(sanitized) != 1:
                raise ManualAlertToolError(
                    "custom template title, message, and optional AI instruction must be valid"
                )
            template = sanitized[0]
            exact_template_index = next(
                (
                    index for index, configured in enumerate(templates)
                    if configured == template
                ),
                None,
            )
            if exact_template_index is not None:
                template_index = exact_template_index
                template = templates[template_index]
                template_source = "configured"
            else:
                title_conflict = next(
                    (
                        configured for configured in templates
                        if configured.get("title") == template["title"]
                    ),
                    None,
                )
                if title_conflict is not None:
                    raise ManualAlertToolError(
                        "a template with this title already exists with different content"
                    )
                if len(templates) >= _MAX_MANUAL_ALERT_TEMPLATES:
                    raise ManualAlertToolError(
                        f"session already has the maximum {_MAX_MANUAL_ALERT_TEMPLATES} Manual Alert templates"
                    )
                template_index = len(templates)
                templates.append(template)
                template_added = True
                template_source = "added"
        else:
            raise ManualAlertToolError(
                "template_index or both custom template fields are required"
            )

        current = [dict(trigger) for trigger in session.spec.manual_alert_triggers]
        replace_existing = bool(arguments.get("replace_existing_triggers", False))
        duplicate = next(
            (
                trigger for trigger in current
                if trigger.get("enabled")
                and float(trigger.get("price")) == price
                and trigger.get("template") == template
            ),
            None,
        )
        if duplicate is not None:
            final_triggers = [duplicate] if replace_existing else current
            replaced_trigger_count = len(current) - len(final_triggers)
            if template_added:
                await self._registry.update_manual_alert_configuration(
                    session_id,
                    templates=templates,
                    triggers=final_triggers,
                )
            elif replaced_trigger_count:
                await self._registry.update_manual_alert_triggers(
                    session_id,
                    final_triggers,
                )
            return self._set_result(
                session=session,
                trigger=duplicate,
                created=False,
                template_source=template_source,
                template_index=template_index,
                template_added=template_added,
                replaced_trigger_count=replaced_trigger_count,
                active_trigger_count=len(final_triggers),
            )

        if not replace_existing and len(current) >= _MAX_MANUAL_ALERT_TRIGGERS:
            raise ManualAlertToolError(
                f"session already has the maximum {_MAX_MANUAL_ALERT_TRIGGERS} Manual Alert triggers"
            )

        new_trigger = {
            "enabled": True,
            "price": price,
            "template": template,
        }
        requested_triggers = [new_trigger] if replace_existing else [*current, new_trigger]
        if template_added:
            updated_configuration = await self._registry.update_manual_alert_configuration(
                session_id,
                templates=templates,
                triggers=requested_triggers,
            )
            updated = updated_configuration["triggers"]
        else:
            updated = await self._registry.update_manual_alert_triggers(
                session_id,
                requested_triggers,
            )
        expected_count = 1 if replace_existing else len(current) + 1
        if len(updated) != expected_count:
            raise RuntimeError("Manual Alert trigger was not persisted")
        return self._set_result(
            session=session,
            trigger=updated[-1],
            created=True,
            template_source=template_source,
            template_index=template_index,
            template_added=template_added,
            replaced_trigger_count=len(current) if replace_existing else 0,
            active_trigger_count=len(updated),
        )

    async def _delete_triggers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = arguments.get("session_id")
        trigger_ids = arguments.get("trigger_ids") or []
        delete_all = bool(arguments.get("delete_all", False))

        if session_id is not None and self._registry.get(session_id) is None:
            raise ManualAlertToolError(
                "session_id is not an active session; refresh Manual Alert context"
            )

        if delete_all:
            if session_id is None:
                selections = {
                    active_session_id: None
                    for active_session_id in self._registry.sessions
                }
                scope = "all_sessions"
            else:
                selections = {session_id: None}
                scope = "session"
        else:
            selections = {session_id: set(trigger_ids)}
            scope = "selected"

        deleted_records = await self._registry.delete_manual_alert_triggers(selections)
        deleted_triggers: list[dict[str, Any]] = []
        deleted_ids: set[str] = set()
        for record in deleted_records:
            trigger = record.get("trigger") or {}
            trigger_id = str(trigger.get("id") or "")
            deleted_ids.add(trigger_id)
            template = trigger.get("template") or {}
            deleted_triggers.append({
                "session_id": record.get("session_id"),
                "exchange": record.get("exchange"),
                "symbol": record.get("symbol"),
                "timeframe": record.get("timeframe"),
                "trigger_id": trigger_id,
                "price": trigger.get("price"),
                "template_title": str(template.get("title") or ""),
            })

        remaining = [
            {
                "session_id": selected_session_id,
                "active_trigger_count": len(
                    self._registry.sessions[selected_session_id].spec.manual_alert_triggers
                ),
            }
            for selected_session_id in selections
        ]
        return {
            "deleted": bool(deleted_triggers),
            "scope": scope,
            "deleted_count": len(deleted_triggers),
            "affected_session_count": len({
                trigger["session_id"] for trigger in deleted_triggers
            }),
            "deleted_triggers": deleted_triggers,
            "not_found_trigger_ids": [
                trigger_id for trigger_id in trigger_ids
                if trigger_id not in deleted_ids
            ],
            "templates_preserved": True,
            "remaining": remaining,
        }

    @staticmethod
    def _set_result(
        *,
        session: Any,
        trigger: dict[str, Any],
        created: bool,
        template_source: str,
        template_index: int,
        template_added: bool,
        replaced_trigger_count: int,
        active_trigger_count: int,
    ) -> dict[str, Any]:
        template = trigger.get("template") or {}
        return {
            "set": True,
            "created": created,
            "session_id": session.spec.id,
            "exchange": session.spec.exchange,
            "symbol": session.spec.symbol,
            "timeframe": session.spec.timeframe,
            "trigger_id": str(trigger.get("id") or ""),
            "price": trigger.get("price"),
            "template_title": str(template.get("title") or ""),
            "has_ai_instruction": bool(str(template.get("ai") or "").strip()),
            "template_source": template_source,
            "template_index": template_index,
            "template_added": template_added,
            "replaced_trigger_count": replaced_trigger_count,
            "active_trigger_count": active_trigger_count,
        }


class ManualAlertTools:
    """Expose context lookup and persistent Manual Alert trigger changes."""

    def __init__(self, registry: Any) -> None:
        self.bridge = ManualAlertRegistryBridge(registry)

    @property
    def names(self) -> set[str]:
        return {_CONTEXT_TOOL_NAME, _SET_TOOL_NAME, _DELETE_TOOL_NAME}

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": _CONTEXT_TOOL_NAME,
                "description": (
                    "List active PyneReal sessions, their exact session IDs, current prices, "
                    "configured Manual Alert templates, and active price triggers. Call this "
                    "before setting or deleting a Manual Alert. Do not guess a session, template, "
                    "or trigger when the user's request can match more than one result."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": _SET_TOOL_NAME,
                "description": (
                    "Persist one Manual Alert price trigger in an active PyneReal session. Use "
                    "only after the user has clearly identified the session, trigger price, and "
                    "alert template. For a session with templates, pass the exact template_index "
                    "from get_manual_alert_context when the requested template exists. If the "
                    "requested template is missing, first ask the user for both its title and "
                    "message format, then pass the custom fields; the tool adds that template and "
                    "sets the trigger together. Never invent missing values."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Exact active session_id returned by get_manual_alert_context.",
                        },
                        "price": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Exact positive trigger price explicitly requested by the user.",
                        },
                        "template_index": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Configured template index. Required only when templates exist.",
                        },
                        "custom_template_title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 100,
                            "description": "User-supplied title for a requested template that is not configured.",
                        },
                        "custom_template_message": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 5000,
                            "description": "User-supplied message format for a template that is not configured.",
                        },
                        "custom_template_ai": {
                            "type": "string",
                            "maxLength": 4000,
                            "description": (
                                "Optional user-supplied AI instruction for a new template. "
                                "It runs only after the Manual Alert webhook succeeds."
                            ),
                        },
                        "replace_existing_triggers": {
                            "type": "boolean",
                            "description": (
                                "Set true only when the user explicitly asks to remove every active "
                                "trigger in this session and replace them with this new trigger. "
                                "Configured templates are always preserved."
                            ),
                        },
                    },
                    "required": ["session_id", "price"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": _DELETE_TOOL_NAME,
                "description": (
                    "Delete selected or all active Manual Alert price triggers while always "
                    "preserving configured templates. First call get_manual_alert_context and "
                    "resolve natural-language targets to internal IDs; never ask the user to "
                    "provide IDs. For selected deletion, pass one session_id and one or more "
                    "trigger_ids. Set delete_all=true with a session_id only when the user asks "
                    "to delete all triggers in that session. Omit session_id with delete_all=true "
                    "only when the user explicitly asks to delete all Manual Alerts across every "
                    "session. Ask for clarification when the deletion scope is ambiguous."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Exact session ID for selected or session-wide deletion.",
                        },
                        "trigger_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 50,
                            "uniqueItems": True,
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 64,
                            },
                            "description": "Exact active trigger IDs for selected deletion.",
                        },
                        "delete_all": {
                            "type": "boolean",
                            "description": "Explicit all-triggers scope; never infer this from an ambiguous request.",
                        },
                    },
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
                raise ManualAlertToolError("Tool arguments must be an object")
            if tool == _CONTEXT_TOOL_NAME:
                self._validate_context_arguments(arguments)
                result = self.bridge.execute("context", arguments)
            elif tool == _SET_TOOL_NAME:
                validated = self._validate_set_arguments(arguments)
                result = self.bridge.execute("set_trigger", validated)
            else:
                validated = self._validate_delete_arguments(arguments)
                result = self.bridge.execute("delete_triggers", validated)
        except ManualAlertToolError as exc:
            return self._tool_response(False, {"error": str(exc)})
        except Exception as exc:
            print(f"[ai] Manual Alert tool failed: {type(exc).__name__}")
            return self._tool_response(
                False,
                {"error": f"Manual Alert operation failed ({type(exc).__name__})"},
            )

        if tool == _SET_TOOL_NAME:
            print(
                f"[ai] Manual Alert set session={result['session_id']} "
                f"price={result['price']} template={result['template_title']!r} "
                f"created={result['created']}"
            )
        elif tool == _DELETE_TOOL_NAME:
            print(
                f"[ai] Manual Alert delete scope={result['scope']} "
                f"deleted={result['deleted_count']}"
            )
        return self._tool_response(True, result)

    @staticmethod
    def _validate_context_arguments(arguments: dict[str, Any]) -> None:
        if arguments:
            raise ManualAlertToolError("get_manual_alert_context does not accept arguments")

    @staticmethod
    def _validate_set_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "session_id",
            "price",
            "template_index",
            "custom_template_title",
            "custom_template_message",
            "custom_template_ai",
            "replace_existing_triggers",
        }
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            raise ManualAlertToolError(
                f"Unexpected argument field(s): {', '.join(unexpected)}"
            )

        session_id = arguments.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ManualAlertToolError("session_id must be a non-empty string")

        price = arguments.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise ManualAlertToolError("price must be a number")
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            raise ManualAlertToolError("price must be a finite positive number")

        template_index = arguments.get("template_index")
        if template_index is not None:
            if isinstance(template_index, bool) or not isinstance(template_index, int):
                raise ManualAlertToolError("template_index must be a non-negative integer")
            if template_index < 0:
                raise ManualAlertToolError("template_index must be a non-negative integer")

        custom_title = arguments.get("custom_template_title")
        if custom_title is not None and not isinstance(custom_title, str):
            raise ManualAlertToolError("custom_template_title must be a string")
        custom_message = arguments.get("custom_template_message")
        if custom_message is not None and not isinstance(custom_message, str):
            raise ManualAlertToolError("custom_template_message must be a string")
        custom_ai = arguments.get("custom_template_ai")
        if custom_ai is not None and not isinstance(custom_ai, str):
            raise ManualAlertToolError("custom_template_ai must be a string")
        if isinstance(custom_title, str) and len(custom_title.strip()) > 100:
            raise ManualAlertToolError("custom_template_title exceeds 100 characters")
        if isinstance(custom_message, str) and len(custom_message.strip()) > 5000:
            raise ManualAlertToolError("custom_template_message exceeds 5000 characters")
        if isinstance(custom_ai, str) and len(custom_ai.strip()) > 4000:
            raise ManualAlertToolError("custom_template_ai exceeds 4000 characters")
        replace_existing = arguments.get("replace_existing_triggers", False)
        if not isinstance(replace_existing, bool):
            raise ManualAlertToolError("replace_existing_triggers must be boolean")

        validated = dict(arguments)
        validated["session_id"] = session_id.strip()
        validated["price"] = price
        validated["replace_existing_triggers"] = replace_existing
        return validated

    @staticmethod
    def _validate_delete_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        unexpected = sorted(set(arguments) - {"session_id", "trigger_ids", "delete_all"})
        if unexpected:
            raise ManualAlertToolError(
                f"Unexpected argument field(s): {', '.join(unexpected)}"
            )

        session_id = arguments.get("session_id")
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id.strip():
                raise ManualAlertToolError("session_id must be a non-empty string")
            session_id = session_id.strip()

        delete_all = arguments.get("delete_all", False)
        if not isinstance(delete_all, bool):
            raise ManualAlertToolError("delete_all must be boolean")

        trigger_ids = arguments.get("trigger_ids")
        if delete_all:
            if trigger_ids is not None:
                raise ManualAlertToolError(
                    "trigger_ids cannot be combined with delete_all=true"
                )
            return {
                "session_id": session_id,
                "delete_all": True,
            }

        if session_id is None:
            raise ManualAlertToolError(
                "session_id is required for selected trigger deletion"
            )
        if not isinstance(trigger_ids, list) or not trigger_ids:
            raise ManualAlertToolError(
                "trigger_ids must be a non-empty array for selected deletion"
            )
        if len(trigger_ids) > 50:
            raise ManualAlertToolError("trigger_ids can contain at most 50 items")

        normalized_ids: list[str] = []
        for trigger_id in trigger_ids:
            if not isinstance(trigger_id, str) or not trigger_id.strip():
                raise ManualAlertToolError("each trigger_id must be a non-empty string")
            trigger_id = trigger_id.strip()
            if len(trigger_id) > 64:
                raise ManualAlertToolError("trigger_id exceeds 64 characters")
            if trigger_id not in normalized_ids:
                normalized_ids.append(trigger_id)

        return {
            "session_id": session_id,
            "trigger_ids": normalized_ids,
            "delete_all": False,
        }

    @staticmethod
    def _tool_response(success: bool, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": success,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ],
        }
