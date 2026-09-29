"""Prepare immutable proposals; only the service's approved-action worker applies them."""
from __future__ import annotations

import copy
import difflib
import hashlib
import html
import itertools
import json
import math
import time

from pydantic import BaseModel, ConfigDict, Field

from ai.scripts.manual_alert_tool import ManualAlertToolError
from data_service.manual_alerts import validate_manual_alert_template

from .attachments import TEXT_LIMIT

MUTATION_TOOLS = {
    "propose_manual_alert_trigger": "set_manual_alert_trigger",
    "propose_manual_alert_deletion": "delete_manual_alert_triggers",
    "propose_manual_alert_template_update": "update_manual_alert_template",
    "propose_calendar_event": "add_calendar_event",
    "propose_calendar_replacement": "replace_calendar_events",
}


class ProposalConflict(ValueError):
    pass


class ScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=512)


class ScriptProposal(ScriptRequest):
    base_revision: str = Field(pattern="^[a-f0-9]{64}$")
    content: str = Field(max_length=TEXT_LIMIT)
    note: str | None = Field(default=None, max_length=240)


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _script_diff(path: str, before: str, after: str) -> bytes:
    lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile=path + " (current)", tofile=path + " (proposed)")
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                   for line in lines).encode()


def _script_review(path: str, before: str, after: str) -> bytes:
    lines = _script_diff(path, before, after).decode().splitlines(keepends=True)
    classified = []
    for index, line in enumerate(lines):
        if index < 2:
            kind = "file"
        elif line.startswith("@@ "):
            kind = "hunk"
        else:
            kind = {"+": "added", "-": "removed", "\\": "note"}.get(line[:1], "context")
        classified.append((kind, line))
    added = sum(kind == "added" for kind, _ in classified)
    removed = sum(kind == "removed" for kind, _ in classified)
    # Group adjacent lines to bound markup size even for large, newline-heavy edits.
    blocks = "".join(
        f'<pre class="{kind}">{html.escape("".join(line for _, line in group))}</pre>'
        for kind, group in itertools.groupby(classified, key=lambda item: item[0])
    )
    document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Script changes</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; color: #24292f; background: #fff; font: 14px/1.5 system-ui, sans-serif; }
header { padding: 16px; border-bottom: 1px solid #d0d7de; }
h1 { margin: 0 0 6px; font-size: 16px; overflow-wrap: anywhere; }
.counts { display: flex; flex-wrap: wrap; gap: 16px; font-variant-numeric: tabular-nums; }
.add-count { color: #116329; } .remove-count { color: #a40e26; }
main { padding: 12px 0; }
pre { margin: 0; padding: 0 12px; border-left: 3px solid transparent;
  font: 13px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre-wrap; overflow-wrap: anywhere; tab-size: 4; }
.added { color: #116329; background: #dafbe1; border-left-color: #2da44e; }
.removed { color: #a40e26; background: #ffebe9; border-left-color: #cf222e; }
.hunk { color: #0550ae; background: #ddf4ff; margin-top: 8px; }
.file, .note { color: #57606a; background: #f6f8fa; }
</style></head><body>
"""
    return (document + f'<header><h1>{html.escape(path)}</h1><div class="counts">'
            f'<span class="add-count">+{added} added</span>'
            f'<span class="remove-count">-{removed} removed</span></div></header>'
            f'<main>{blocks}</main></body></html>').encode()


def _remaining_time(target_ms: int, now_ms: int) -> str:
    seconds = math.ceil((target_ms - now_ms) / 1000)
    if seconds <= 0:
        return "due; waiting to start"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (seconds, "s")) if value]
    return "~" + " ".join(parts)


def action_summary(kind: str, result: dict) -> str:
    if kind == "script":
        lines = ["Script saved." if result.get("saved") else "No source changes.", str(result.get("path") or "")]
        validation = result.get("validation") or {}
        counts = validation.get("summary") or {}
        if validation.get("status") in {"error", "warning"}:
            lines.append(f"Static check: {counts.get('errors', 0)} error(s), {counts.get('warnings', 0)} warning(s).")
        elif validation.get("status") == "passed":
            lines.append("Static check passed.")
        else:
            lines.append("Static check unavailable.")
        if result.get("saved"):
            if validation.get("status") == "error":
                lines.append("Fix the errors before running this script.")
            warmups = result.get("warmups")
            if warmups == []:
                lines.append("No active runner. Applies on next start.")
            elif warmups:
                now_ms = int(time.time() * 1000)
                for item in warmups:
                    target = item.get("at")
                    remaining = _remaining_time(target, now_ms) if target is not None else "time unavailable"
                    label = f"{item['label']}: " if len(warmups) > 1 else ""
                    lines.append(f"{label}Next warm-up: {remaining}")
            else:
                lines.append("Next warm-up: time unavailable.")
        return "\n".join(line for line in lines if line)
    if kind == "set_manual_alert_trigger":
        action = "set" if result.get("created") else "updated"
        return (f"Manual Alert {action}.\n{result.get('exchange', '')} {result.get('symbol', '')} "
                f"{result.get('timeframe', '')}\nPrice: {result.get('price')}\n{result.get('template_title', '')}").strip()
    if kind == "delete_manual_alert_triggers":
        return f"Manual Alerts deleted: {result.get('deleted_count', 0)}."
    if kind == "update_manual_alert_template":
        action = "updated" if result.get("changed") else "unchanged"
        return (f"Manual Alert template {action}.\n{result.get('exchange', '')} {result.get('symbol', '')} "
                f"{result.get('timeframe', '')}\n{result.get('template_title', '')}"
                "\nExisting price triggers unchanged.")
    if kind == "add_calendar_event":
        event = result.get("event") or {}
        return f"Calendar event added.\n{event.get('date', '')} {event.get('title', '')}".strip()
    if kind == "replace_calendar_events":
        return f"Calendar updated: {result.get('saved_event_count', 0)} event(s)."
    return "Change applied."


class TelegramActions:
    def __init__(self, dynamic_tools, workspace, executor) -> None:
        self.tools = dynamic_tools
        self.workspace = workspace
        self.executor = executor

    @property
    def specs(self) -> list[dict]:
        result = []
        for public, original in MUTATION_TOOLS.items():
            spec = next(item for item in self.tools.specs if item["name"] == original)
            result.append({**spec, "name": public, "description": (
                "Prepare a proposal only; no change is applied until the requesting user presses "
                "Apply in Telegram. First read the relevant context. Do not claim completion. "
                + spec["description"]
            )})
        result.extend([
            {"type": "function", "name": "telegram_read_script",
             "description": "Read an existing file under workdir/scripts (relative path), its revision and validation. Use before proposing edits.",
             "inputSchema": ScriptRequest.model_json_schema()},
            {"type": "function", "name": "telegram_list_scripts",
             "description": "List files and directories under workdir/scripts; no filesystem writes.",
             "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"type": "function", "name": "propose_script_change",
             "description": "Propose replacement content for an existing workdir/scripts file using the exact revision just read. Sends a color-coded HTML diff and Save/Cancel buttons; does not save yet. Only for explicit editing requests.",
             "inputSchema": ScriptProposal.model_json_schema()},
        ])
        return result

    async def read_script(self, arguments: dict) -> dict:
        request = ScriptRequest.model_validate(arguments)
        result = await self.executor.run(self.workspace.read_file, request.path)
        if len(result["content"].encode()) > TEXT_LIMIT:
            raise ValueError("Script exceeds Telegram's 256 KB editing limit")
        return result

    async def prepare(self, name: str, arguments: dict) -> tuple[str, dict, dict, str, bytes | None]:
        if name == "propose_script_change":
            request = ScriptProposal.model_validate(arguments)
            if len(request.content.encode()) > TEXT_LIMIT:
                raise ValueError("Script exceeds Telegram's 256 KB editing limit")
            current = await self.read_script({"path": request.path})
            if current["revision"] != request.base_revision:
                raise ProposalConflict("File changed. Read it again before proposing edits.")
            if current["content"] == request.content:
                raise ValueError("No source changes to approve")
            review = await self.executor.run(_script_review, request.path, current["content"], request.content)
            preview = (f"Save script: {current['path']}\nBase revision: {request.base_revision[:12]}"
                       "\nReview changes.html: red = removed, green = added."
                       " Saving may affect running sessions at the next warm-up."
                       + (f"\nNote: {request.note}" if request.note else ""))
            return "script", request.model_dump(), {}, preview, review

        original = MUTATION_TOOLS[name]
        manual = original in {"set_manual_alert_trigger", "delete_manual_alert_triggers", "update_manual_alert_template"}
        if manual:
            tool = self.tools.manual_alert
            validator = {
                "set_manual_alert_trigger": tool._validate_set_arguments,
                "delete_manual_alert_triggers": tool._validate_delete_arguments,
                "update_manual_alert_template": tool._validate_update_template_arguments,
            }[original]
            payload = validator(arguments)
        else:
            tool = self.tools.calendar
            validator = tool._validate_add if original == "add_calendar_event" else tool._validate_replace
            payload = validator(arguments)
            from data_service.calendar_store import _sanitize_event, _date_value
            if original == "add_calendar_event":
                _sanitize_event(payload)
            else:
                start = _date_value(payload["range_start"], "range_start")
                end = _date_value(payload["range_end"], "range_end")
                if start > end:
                    raise ValueError("Calendar range is reversed")
                ids = [item["session_id"] for item in payload["session_events"]]
                if len(ids) != len(set(ids)):
                    raise ValueError("Duplicate calendar session")
                for item in payload["session_events"]:
                    for event in item["events"]:
                        sanitized = _sanitize_event(event, session_id=item["session_id"])
                        if not start <= sanitized["date"] <= end:
                            raise ValueError("Calendar event is outside the replacement range")
        # Snapshot and comparison run on the registry's loop, never the Codex reader thread.
        state = self._state(original, payload)
        guard = {"fingerprint": _fingerprint(state)}
        if original == "update_manual_alert_template":
            session = state["preview"][0]
            review = json.dumps({
                "operation": original,
                "session": {key: session[key] for key in ("session_id", "exchange", "symbol", "timeframe")},
                "template_index": payload["template_index"],
                **state["template_change"],
                "existing_price_triggers": "unchanged",
            }, ensure_ascii=False, indent=2)
            before, after = state["template_change"]["before"], state["template_change"]["after"]
            preview = (f"Edit Manual Alert template: {before['title']}\n"
                       f"{session['exchange']} {session['symbol']} {session['timeframe']}\n"
                       f"Before:\n{json.dumps(before, ensure_ascii=False, indent=2)}\n"
                       f"After:\n{json.dumps(after, ensure_ascii=False, indent=2)}")
            if len(preview) > 1500:
                preview = (f"Edit Manual Alert template: {before['title']}\n"
                           f"{session['exchange']} {session['symbol']} {session['timeframe']}\n"
                           "Review changes.txt for the complete before/after values.")
            preview += "\nExisting price triggers unchanged."
            return original, payload, guard, preview, review.encode()
        review = json.dumps({"operation": original, "requested": payload, "current": state["preview"]},
                            ensure_ascii=False, indent=2)
        if len(review.encode()) > 1024 * 1024:
            raise ValueError("Too many changes; narrow the request")
        title = original.replace("_", " ").capitalize()
        preview = title + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        if len(preview) > 1500:
            preview = title + "\nReview changes.txt for the exact sessions, values and replacement/deletion scope."
        elif manual:
            preview += "\nReview changes.txt for the selected template and current triggers."
        else:
            preview += "\nReview changes.txt for the current events in the affected scope."
        return original, payload, guard, preview, review.encode()

    def _state(self, kind: str, payload: dict) -> dict:
        if kind in {"set_manual_alert_trigger", "delete_manual_alert_triggers", "update_manual_alert_template"}:
            registry = self.tools.manual_alert.bridge._registry
            ids = [payload["session_id"]] if payload.get("session_id") else sorted(registry.sessions)
            sessions = []
            identities = []
            for sid in ids:
                session = registry.get(sid)
                if session is None:
                    raise ProposalConflict("Session no longer exists")
                spec = session.spec
                item = {"session_id": sid, "exchange": spec.exchange, "symbol": spec.symbol,
                        "timeframe": spec.timeframe, "script_name": spec.script_name,
                        "templates": copy.deepcopy(spec.manual_alert_templates),
                        "triggers": copy.deepcopy(spec.manual_alert_triggers)}
                sessions.append(item)
                identities.append(id(session))
            if kind == "set_manual_alert_trigger":
                index = payload.get("template_index")
                custom = any(payload.get(key) is not None for key in (
                    "custom_template_title", "custom_template_message", "custom_template_ai"))
                if index is not None:
                    if custom or index >= len(sessions[0]["templates"]):
                        raise ValueError("Select one current template or supply a custom template")
                    template = sessions[0]["templates"][index]
                elif not payload.get("custom_template_title") or not payload.get("custom_template_message"):
                    raise ValueError("A template or custom title and message are required")
                else:
                    template = {"title": payload["custom_template_title"], "message": payload["custom_template_message"]}
                try:
                    validate_manual_alert_template(template, registry.get(ids[0]).spec)
                except ValueError as exc:
                    raise ProposalConflict(str(exc)) from None
            state = {"preview": sessions, "identities": identities}
            if kind == "update_manual_alert_template":
                try:
                    _, before, after = self.tools.manual_alert.bridge._prepare_template_update(payload)
                except ManualAlertToolError as exc:
                    raise ProposalConflict(str(exc)) from None
                state["template_change"] = {"before": before, "after": after}
            return state
        registry = self.tools.calendar.bridge._registry
        context = self.tools.calendar.bridge._context()
        ids = (payload["session_ids"] if kind == "add_calendar_event"
               else [item["session_id"] for item in payload["session_events"]])
        if any(sid not in registry.sessions for sid in ids):
            raise ProposalConflict("Session no longer exists")
        selected = [item for item in context["sessions"] if item["session_id"] in ids]
        return {"preview": selected, "identities": [id(registry.get(sid)) for sid in sorted(ids)]}

    def _script_warmups(self, path: str) -> list[dict]:
        from scripting_api import next_warmup_at

        registry = self.tools.manual_alert.bridge._registry
        warmups = []
        for session in registry.sessions.values():
            spec = session.spec
            if str(spec.script_name or "").strip().replace("\\", "/") != path:
                continue
            if not (registry.supervisor.is_active(spec.id) or session.runner_count > 0):
                continue
            warmups.append({"label": f"{spec.exchange} {spec.symbol} {spec.timeframe}",
                            "at": next_warmup_at(session)})
        return warmups

    async def apply(self, proposal: dict) -> dict:
        kind, payload = proposal["kind"], proposal["payload"]
        if kind == "script":
            from scripting_workspace import ScriptingConflictError
            try:
                result = await self.executor.run(self.workspace.save_file, payload["path"], payload["content"],
                                                 payload["base_revision"], source="ai", note=payload.get("note"))
            except ScriptingConflictError:
                raise ProposalConflict("The file changed after the preview. Nothing was saved; request a new diff.") from None
            # A status lookup failure must not turn a successful save into an unknown write.
            try:
                warmups = self._script_warmups(result["path"]) if result["saved"] else []
            except Exception:
                warmups = None
            return {"path": result["path"], "saved": result["saved"], "revision": result["revision"],
                    "apply_state": result.get("apply_state"), "validation": result.get("validation"),
                    "warmups": warmups}
        if _fingerprint(self._state(kind, payload)) != proposal["guard"]["fingerprint"]:
            raise ProposalConflict("The session, templates, triggers or calendar changed after the preview. Request a new proposal.")
        if kind in {"set_manual_alert_trigger", "delete_manual_alert_triggers", "update_manual_alert_template"}:
            operation = {
                "set_manual_alert_trigger": "set_trigger",
                "delete_manual_alert_triggers": "delete_triggers",
                "update_manual_alert_template": "update_template",
            }[kind]
            return await self.tools.manual_alert.bridge._execute_on_loop(operation, payload)
        if kind in {"add_calendar_event", "replace_calendar_events"}:
            operation = "add" if kind == "add_calendar_event" else "replace"
            return await self.tools.calendar.bridge._execute_on_loop(operation, payload)
        raise ValueError("Unsupported approved action")
