from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from ai.provider.codex_service import CodexService
from api import (
    _AI_CHAT_MAX_HISTORY_MESSAGES,
    _AI_CHAT_MAX_MESSAGE_CHARS,
    _AI_MARKDOWN_STREAM_INTERVAL_SECONDS,
    _render_ai_markdown,
    _sanitize_ai_chat_history,
    _sse_event,
)
from scripting_api import ScriptingExecutor
from scripting_workspace import ScriptingWorkspace, ScriptingWorkspaceError

_MAX_DRAFT_BYTES = 2 * 1024 * 1024


def _scripting_turn_prompt(
    message: str,
    script: dict[str, Any],
    *,
    working_file: str,
    editor_dirty: bool,
) -> str:
    context = {
        "active_file": working_file,
        "editor_file": f"workdir/scripts/{script['path']}",
        "revision": script["revision"],
        "language": script["language"],
        "editor_dirty": editor_dirty,
        "temporary_draft": True,
    }
    return (
        "PyneReal Scripting context for this turn:\n"
        f"{json.dumps(context, ensure_ascii=False)}\n\n"
        "The active_file is a temporary mirror of the current editor content. Use it as the "
        "authoritative source for this turn. If the user requests a change, modify only active_file; "
        "do not modify editor_file or any other file. The result will be returned to the editor as "
        "an unsaved change. "
        "Inspect the active file before making claims about its implementation. "
        "Only modify active_file when the user explicitly requests a change. "
        "Respond in the user's language.\n\n"
        f"User request:\n{message}"
    )


def _create_draft_file(workspace: ScriptingWorkspace, script_path: str, content: str) -> Path:
    draft_root = workspace.root.parent / "data" / "cache" / "scripting_ai_drafts"
    draft_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(script_path).suffix or ".txt"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix="draft-",
        suffix=suffix,
        dir=draft_root,
        delete=False,
    ) as draft:
        draft.write(content)
        return Path(draft.name)


def _read_draft_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _remove_draft_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _scripting_initial_prompt(
    turn_prompt: str,
    history: list[dict[str, str]],
) -> str:
    if not history:
        return turn_prompt
    return (
        "Continue the prior browser conversation below. It is context only; the final item under "
        "Current turn is the request to answer now.\n\n"
        f"Prior conversation:\n{json.dumps(history, ensure_ascii=False)}\n\n"
        f"Current turn:\n{turn_prompt}"
    )


def build_scripting_ai_router(
    workspace: ScriptingWorkspace,
    codex_service: CodexService,
    executor: ScriptingExecutor,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/scripting/ai/chat")
    async def scripting_ai_chat(payload: dict):
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return JSONResponse({"error": "message must be a non-empty string"}, status_code=400)
        message = message.strip()
        if len(message) > _AI_CHAT_MAX_MESSAGE_CHARS:
            return JSONResponse(
                {"error": f"message is too long (max {_AI_CHAT_MAX_MESSAGE_CHARS} chars)"},
                status_code=400,
            )
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            return JSONResponse({"error": "script path is required"}, status_code=400)
        conversation_id = payload.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            return JSONResponse({"error": "conversation_id must be a string"}, status_code=400)
        model = payload.get("model")
        effort = payload.get("effort")
        if model is not None and not isinstance(model, str):
            return JSONResponse({"error": "model must be a string"}, status_code=400)
        if effort is not None and not isinstance(effort, str):
            return JSONResponse({"error": "effort must be a string"}, status_code=400)
        draft_content = payload.get("draft_content")
        if draft_content is not None and not isinstance(draft_content, str):
            return JSONResponse({"error": "draft_content must be a string"}, status_code=400)
        if (
            isinstance(draft_content, str)
            and len(draft_content.encode("utf-8")) > _MAX_DRAFT_BYTES
        ):
            return JSONResponse({"error": "draft_content is too large"}, status_code=400)
        if not codex_service.enabled:
            return JSONResponse({"error": "AI service is disabled"}, status_code=503)

        try:
            script = await executor.run(workspace.read_file, path.strip())
        except ScriptingWorkspaceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        if draft_content is None:
            draft_content = str(script["content"])
        editor_dirty = payload.get("draft_dirty") is True
        try:
            model = await codex_service.validate_model(model)
            effort = await codex_service.validate_effort(effort, model)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if model is None or effort is None:
            try:
                preferences = await codex_service.chat_preferences()
            except Exception:
                preferences = None
            if preferences:
                if model is None:
                    model = preferences["model"]
                if effort is None and model == preferences["model"]:
                    effort = preferences["effort"]

        history = _sanitize_ai_chat_history(payload.get("history"))
        try:
            draft_path = await executor.run(
                _create_draft_file,
                workspace,
                str(script["path"]),
                draft_content,
            )
        except OSError as exc:
            return JSONResponse(
                {"error": f"failed to prepare editor draft: {exc}"},
                status_code=500,
            )
        working_file = draft_path.relative_to(workspace.root.parent.parent).as_posix()
        turn_prompt = _scripting_turn_prompt(
            message,
            script,
            working_file=working_file,
            editor_dirty=editor_dirty,
        )
        initial_prompt = _scripting_initial_prompt(turn_prompt, history)

        async def stream() -> AsyncIterator[str]:
            streamed_answer = ""
            pending_delta = ""
            last_delta_emit = 0.0
            try:
                async for event in codex_service.stream_chat(
                    turn_prompt,
                    conversation_id=(conversation_id or "").strip() or None,
                    initial_context=initial_prompt,
                    history_messages=min(len(history), _AI_CHAT_MAX_HISTORY_MESSAGES),
                    model=model,
                    effort=effort,
                ):
                    if event.event == "delta":
                        delta = str(event.payload.get("text") or "")
                        streamed_answer += delta
                        pending_delta += delta
                        now = time.monotonic()
                        if last_delta_emit and now - last_delta_emit < _AI_MARKDOWN_STREAM_INTERVAL_SECONDS:
                            continue
                        yield _sse_event(
                            "delta",
                            {"text": pending_delta, "html": _render_ai_markdown(streamed_answer)},
                        )
                        pending_delta = ""
                        last_delta_emit = now
                        continue
                    if event.event == "done" and pending_delta:
                        yield _sse_event(
                            "delta",
                            {"text": pending_delta, "html": _render_ai_markdown(streamed_answer)},
                        )
                        pending_delta = ""
                    event_payload = dict(event.payload)
                    if event.event == "done":
                        answer = str(event.payload.get("answer") or "")
                        event_payload["html"] = _render_ai_markdown(answer)
                        try:
                            revised_draft = await executor.run(_read_draft_file, draft_path)
                        except (OSError, UnicodeError):
                            revised_draft = draft_content
                        if revised_draft != draft_content:
                            event_payload["draft_content"] = revised_draft
                    yield _sse_event(event.event, event_payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                yield _sse_event("stream_error", {"error": str(exc)})
            finally:
                try:
                    await executor.run(_remove_draft_file, draft_path)
                except (OSError, RuntimeError):
                    pass

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/api/scripting/ai/chat/reset")
    async def reset_scripting_ai_chat(payload: dict) -> JSONResponse:
        conversation_id = payload.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            await codex_service.reset(conversation_id.strip())
        return JSONResponse({"ok": True})

    return router
