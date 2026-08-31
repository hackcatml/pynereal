from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from registry import SessionRegistry
from prerun_scheduler import timeframe_seconds
from scripting_history import ScriptingRevisionNotFoundError
from scripting_workspace import (
    ScriptingConflictError,
    ScriptingFileEncodingError,
    ScriptingFileNotFoundError,
    ScriptingFileTooLargeError,
    ScriptingFileTypeError,
    ScriptingNoteError,
    ScriptingPathError,
    ScriptingPathExistsError,
    ScriptingWorkspace,
    ScriptingWorkspaceError,
)


_STRATEGY_TEMPLATES = {"empty", "long_short", "indicator"}


def _strategy_source(template: str, title: str, description: str) -> str:
    title_literal = json.dumps(title, ensure_ascii=False)
    description_lines = [
        f"# {line}" if line else "#"
        for line in description.strip().splitlines()
    ]
    description_block = "\n".join(description_lines)
    if description_block:
        description_block = f"\n    {description_block.replace(chr(10), chr(10) + '    ')}\n"

    if template == "long_short":
        body = f'''from pynecore.lib import close, script, strategy, ta
from pynecore.types import Series


@script.strategy({title_literal}, overlay=True)
def main():{description_block}
    fast_ma: Series[float] = ta.ema(close, 9)
    slow_ma: Series[float] = ta.ema(close, 21)

    if ta.crossover(fast_ma, slow_ma):
        strategy.entry("Long", strategy.long)
    elif ta.crossunder(fast_ma, slow_ma):
        strategy.close("Long")
'''
    elif template == "indicator":
        body = f'''from pynecore.lib import close, color, plot, script, ta
from pynecore.types import Series


@script.strategy({title_literal}, overlay=True)
def main():{description_block}
    value: Series[float] = ta.ema(close, 20)
    plot(value, title="EMA 20", color=color.blue)
'''
    else:
        body = f'''from pynecore.lib import script


@script.strategy({title_literal}, overlay=True)
def main():{description_block}
    pass
'''
    return f'''"""
@pyne
"""

{body}'''


def build_scripting_router(
    workspace: ScriptingWorkspace,
    registry: SessionRegistry | None = None,
) -> APIRouter:
    router = APIRouter()

    def next_warmup_at(session: object) -> int | None:
        now_ms = int(time.time() * 1000)
        scheduled = getattr(session, "next_prerun_at", None)
        phase = str(getattr(session, "runner_phase", "") or "")
        if (
            isinstance(scheduled, (int, float))
            and (scheduled > now_ms or phase in {"prerun_scheduled", "prerun_active"})
        ):
            return int(scheduled)
        spec = getattr(session, "spec", None)
        timeframe = str(getattr(spec, "timeframe", "") or "")
        duration_ms = max(1000, timeframe_seconds(timeframe) * 1000)
        offset_ms = max(
            0,
            int(getattr(session, "prerun_effective_offset_seconds", 0) or 0) * 1000,
        )
        feed = getattr(session, "feed", None)
        last_bar_time = feed.last_bar_time() if feed is not None else None
        if isinstance(last_bar_time, (int, float)) and last_bar_time > 0:
            next_bar_ms = int(last_bar_time * 1000) + duration_ms
        else:
            next_bar_ms = ((now_ms // duration_ms) + 1) * duration_ms
        target_ms = next_bar_ms + offset_ms
        while target_ms <= now_ms:
            target_ms += duration_ms
        return target_ms

    def usage_payload(path: str, *, directory: bool) -> dict:
        prefix = f"{path}/"
        sessions = []
        registered_sessions = (
            list(registry.sessions.values())
            if registry is not None
            else []
        )
        for session in registered_sessions:
            script_path = str(session.spec.script_name or "").strip().replace("\\", "/")
            if script_path != path and not (directory and script_path.startswith(prefix)):
                continue
            active = registry is not None and (
                registry.supervisor.is_active(session.spec.id)
                or session.runner_count > 0
            )
            sessions.append({
                "id": session.spec.id,
                "exchange": session.spec.exchange,
                "symbol": session.spec.symbol,
                "timeframe": session.spec.timeframe,
                "script_path": script_path,
                "active": active,
                "runner": registry.runner_status(session.spec.id) if registry is not None else "stopped",
                "runner_phase": getattr(session, "runner_phase", "stopped"),
                "next_prerun_at": getattr(session, "next_prerun_at", None),
                "next_warmup_at": next_warmup_at(session) if active else None,
            })
        sessions.sort(key=lambda item: (not item["active"], item["id"]))
        return {
            "path": path,
            "type": "directory" if directory else "file",
            "session_count": len(sessions),
            "active_count": sum(1 for item in sessions if item["active"]),
            "sessions": sessions,
        }

    def mutation_blocked(
        usage: dict,
        acknowledged: bool,
        *,
        clear_stopped_sessions: bool = False,
    ) -> JSONResponse | None:
        if usage["active_count"]:
            return JSONResponse(
                {
                    "error": "stop every Runner using this path before changing it",
                    "code": "active_runner",
                    "usage": usage,
                },
                status_code=409,
            )
        if usage["session_count"] and not acknowledged:
            message = (
                "stopped sessions still reference this path; their script selections "
                "will be cleared"
                if clear_stopped_sessions
                else (
                    "stopped sessions still reference this path; their script paths "
                    "will not be changed"
                )
            )
            return JSONResponse(
                {
                    "error": message,
                    "code": "session_usage_confirmation_required",
                    "usage": usage,
                },
                status_code=409,
            )
        return None

    def error_response(exc: Exception) -> JSONResponse:
        if isinstance(exc, ScriptingConflictError):
            return JSONResponse(
                {
                    "error": str(exc),
                    "code": "revision_conflict",
                    "current_revision": exc.current_revision,
                },
                status_code=409,
            )
        if isinstance(exc, ScriptingPathError):
            return JSONResponse(
                {"error": str(exc), "code": "invalid_path"},
                status_code=400,
            )
        if isinstance(exc, ScriptingPathExistsError):
            return JSONResponse(
                {"error": str(exc), "code": "path_exists"},
                status_code=409,
            )
        if isinstance(exc, ScriptingFileNotFoundError):
            return JSONResponse(
                {"error": str(exc), "code": "file_not_found"},
                status_code=404,
            )
        if isinstance(exc, ScriptingFileTypeError):
            return JSONResponse(
                {"error": str(exc), "code": "unsupported_file_type"},
                status_code=415,
            )
        if isinstance(
            exc,
            (ScriptingFileEncodingError, ScriptingFileTooLargeError, ScriptingNoteError),
        ):
            return JSONResponse(
                {"error": str(exc), "code": "invalid_content"},
                status_code=422,
            )
        return JSONResponse(
            {"error": str(exc), "code": "scripting_operation_failed"},
            status_code=500,
        )

    @router.get("/api/scripting/tree")
    async def get_scripting_tree() -> JSONResponse:
        try:
            payload = await asyncio.to_thread(workspace.tree_payload)
        except ScriptingWorkspaceError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "workspace_unavailable"},
                status_code=500,
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/api/scripting/usage")
    async def get_scripting_usage(path: str) -> JSONResponse:
        try:
            kind = await asyncio.to_thread(workspace.path_kind, path)
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(
            usage_payload(path, directory=kind == "directory"),
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/scripting/directories")
    async def create_scripting_directory(payload: dict) -> JSONResponse:
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return JSONResponse(
                {"error": "path is required", "code": "invalid_request"},
                status_code=400,
            )
        try:
            result = await asyncio.to_thread(workspace.create_directory, path)
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/api/scripting/files")
    async def create_scripting_file(payload: dict) -> JSONResponse:
        path = payload.get("path")
        kind = payload.get("kind")
        if not isinstance(path, str) or not path:
            return JSONResponse(
                {"error": "path is required", "code": "invalid_request"},
                status_code=400,
            )
        try:
            if kind == "copy":
                source_path = payload.get("source_path")
                if not isinstance(source_path, str) or not source_path:
                    return JSONResponse(
                        {"error": "source_path is required", "code": "invalid_request"},
                        status_code=400,
                    )
                result = await asyncio.to_thread(
                    workspace.duplicate_path,
                    source_path,
                    path,
                )
            elif kind == "markdown":
                if not path.lower().endswith(".md"):
                    return JSONResponse(
                        {"error": "Markdown files must use .md", "code": "invalid_request"},
                        status_code=400,
                    )
                title = str(payload.get("title") or path.rsplit("/", 1)[-1][:-3]).strip()
                description = str(payload.get("description") or "").strip()
                content = f"# {title}\n"
                if description:
                    content += f"\n{description}\n"
                result = await asyncio.to_thread(workspace.create_file, path, content)
            elif kind == "strategy":
                template = str(payload.get("template") or "empty").strip()
                title = str(payload.get("title") or "").strip()
                description = str(payload.get("description") or "").strip()
                if not path.lower().endswith(".py"):
                    return JSONResponse(
                        {"error": "Strategy files must use .py", "code": "invalid_request"},
                        status_code=400,
                    )
                if not title:
                    return JSONResponse(
                        {"error": "title is required", "code": "invalid_request"},
                        status_code=400,
                    )
                if template not in _STRATEGY_TEMPLATES:
                    return JSONResponse(
                        {"error": "unknown strategy template", "code": "invalid_request"},
                        status_code=400,
                    )
                result = await asyncio.to_thread(
                    workspace.create_file,
                    path,
                    _strategy_source(template, title, description),
                )
            else:
                return JSONResponse(
                    {"error": "unknown file kind", "code": "invalid_request"},
                    status_code=400,
                )
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/api/scripting/rename")
    async def rename_scripting_path(payload: dict) -> JSONResponse:
        path = payload.get("path")
        next_path = payload.get("next_path")
        acknowledged = payload.get("acknowledge_stopped_sessions") is True
        if not isinstance(path, str) or not path or not isinstance(next_path, str) or not next_path:
            return JSONResponse(
                {"error": "path and next_path are required", "code": "invalid_request"},
                status_code=400,
            )
        try:
            kind = await asyncio.to_thread(workspace.path_kind, path)
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        usage = usage_payload(path, directory=kind == "directory")
        blocked = mutation_blocked(usage, acknowledged)
        if blocked is not None:
            return blocked
        try:
            result = await asyncio.to_thread(workspace.rename_path, path, next_path)
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        result["affected_sessions"] = usage["sessions"]
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.delete("/api/scripting/file")
    async def delete_scripting_path(
        path: str,
        acknowledge_stopped_sessions: bool = False,
    ) -> JSONResponse:
        try:
            kind = await asyncio.to_thread(workspace.path_kind, path)
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        usage = usage_payload(path, directory=kind == "directory")
        blocked = mutation_blocked(
            usage,
            acknowledge_stopped_sessions,
            clear_stopped_sessions=True,
        )
        if blocked is not None:
            return blocked
        cleared_assignments: list[tuple[str, str]] = []
        if registry is not None:
            try:
                for item in usage["sessions"]:
                    session_id = str(item["id"])
                    session = registry.sessions.get(session_id)
                    previous_script = str(session.spec.script_name or "") if session else ""
                    if not previous_script:
                        continue
                    await registry.update_script_name(session_id, "")
                    cleared_assignments.append((session_id, previous_script))
            except Exception:
                for session_id, previous_script in reversed(cleared_assignments):
                    try:
                        await registry.update_script_name(session_id, previous_script)
                    except Exception:
                        pass
                return JSONResponse(
                    {
                        "error": "session state changed; stop every affected Runner and retry",
                        "code": "session_state_changed",
                        "usage": usage,
                    },
                    status_code=409,
                )
        try:
            result = await asyncio.to_thread(workspace.delete_path, path)
        except ScriptingWorkspaceError as exc:
            if registry is not None:
                for session_id, previous_script in reversed(cleared_assignments):
                    try:
                        await registry.update_script_name(session_id, previous_script)
                    except Exception:
                        pass
            return error_response(exc)
        result["affected_sessions"] = usage["sessions"]
        result["cleared_sessions"] = [
            session_id for session_id, _previous_script in cleared_assignments
        ]
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/api/scripting/file")
    async def get_scripting_file(path: str) -> JSONResponse:
        try:
            payload = await asyncio.to_thread(workspace.read_file, path)
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.put("/api/scripting/file")
    async def put_scripting_file(payload: dict) -> JSONResponse:
        path = payload.get("path")
        content = payload.get("content")
        base_revision = payload.get("base_revision")
        note = payload.get("note")
        if not isinstance(path, str) or not path:
            return JSONResponse(
                {"error": "path is required", "code": "invalid_request"},
                status_code=400,
            )
        if not isinstance(content, str):
            return JSONResponse(
                {"error": "content must be a string", "code": "invalid_request"},
                status_code=400,
            )
        if not isinstance(base_revision, str) or len(base_revision) != 64:
            return JSONResponse(
                {"error": "base_revision is required", "code": "invalid_request"},
                status_code=400,
            )
        try:
            result = await asyncio.to_thread(
                workspace.save_file,
                path,
                content,
                base_revision,
                note=note,
            )
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/api/scripting/history")
    async def get_scripting_history(path: str, limit: int = 100) -> JSONResponse:
        try:
            result = await asyncio.to_thread(workspace.history_payload, path, limit)
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/api/scripting/history/{revision_id}")
    async def get_scripting_revision(revision_id: int, path: str) -> JSONResponse:
        try:
            result = await asyncio.to_thread(
                workspace.revision_payload,
                path,
                revision_id,
            )
        except ScriptingRevisionNotFoundError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "revision_not_found"},
                status_code=404,
            )
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/api/scripting/diff")
    async def get_scripting_diff(path: str, revision_id: int) -> JSONResponse:
        try:
            result = await asyncio.to_thread(
                workspace.diff_payload,
                path,
                revision_id,
            )
        except ScriptingRevisionNotFoundError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "revision_not_found"},
                status_code=404,
            )
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/api/scripting/restore")
    async def restore_scripting_file(payload: dict) -> JSONResponse:
        path = payload.get("path")
        revision_id = payload.get("revision_id")
        base_revision = payload.get("base_revision")
        if not isinstance(path, str) or not path:
            return JSONResponse(
                {"error": "path is required", "code": "invalid_request"},
                status_code=400,
            )
        if not isinstance(revision_id, int) or isinstance(revision_id, bool):
            return JSONResponse(
                {"error": "revision_id must be an integer", "code": "invalid_request"},
                status_code=400,
            )
        if not isinstance(base_revision, str) or len(base_revision) != 64:
            return JSONResponse(
                {"error": "base_revision is required", "code": "invalid_request"},
                status_code=400,
            )
        try:
            result = await asyncio.to_thread(
                workspace.restore_file,
                path,
                revision_id,
                base_revision,
            )
        except ScriptingRevisionNotFoundError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "revision_not_found"},
                status_code=404,
            )
        except ScriptingWorkspaceError as exc:
            return error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return router
