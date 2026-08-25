from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from scripting_history import ScriptingRevisionNotFoundError
from scripting_workspace import (
    ScriptingConflictError,
    ScriptingFileEncodingError,
    ScriptingFileNotFoundError,
    ScriptingFileTooLargeError,
    ScriptingFileTypeError,
    ScriptingNoteError,
    ScriptingPathError,
    ScriptingWorkspace,
    ScriptingWorkspaceError,
)


def build_scripting_router(workspace: ScriptingWorkspace) -> APIRouter:
    router = APIRouter()

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
