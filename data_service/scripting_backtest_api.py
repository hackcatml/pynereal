from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from scripting_backtest import ScriptingBacktestError, ScriptingBacktestManager


def _error_response(exc: ScriptingBacktestError) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc), "code": exc.code},
        status_code=exc.status_code,
    )


def _timestamp(value: Any, name: str) -> int:
    if not isinstance(value, str) or not value.strip():
        raise ScriptingBacktestError(f"{name} is required", code="invalid_time_range")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScriptingBacktestError(
            f"{name} must be an ISO 8601 timestamp",
            code="invalid_time_range",
        ) from exc
    if parsed.tzinfo is None:
        raise ScriptingBacktestError(
            f"{name} must include a timezone",
            code="invalid_time_range",
        )
    return int(parsed.astimezone(UTC).timestamp())


def build_scripting_backtest_router(
    manager: ScriptingBacktestManager,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/scripting/backtest/data")
    async def backtest_data(script_path: str) -> JSONResponse:
        try:
            payload = await manager.data_payload(script_path)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(payload)

    @router.get("/api/scripting/backtest/inputs")
    async def backtest_inputs(
        script_path: str,
        base_revision: str,
        data_path: str,
    ) -> JSONResponse:
        try:
            payload = await manager.input_payload(
                script_path=script_path,
                base_revision=base_revision,
                data_path=data_path,
            )
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(payload)

    @router.post("/api/scripting/backtest/data/sync")
    async def sync_backtest_data(payload: dict) -> JSONResponse:
        try:
            result = await manager.sync_data(
                action=str(payload.get("action") or ""),
                data_path=str(payload.get("data_path") or ""),
                exchange=str(payload.get("exchange") or ""),
                symbol=str(payload.get("symbol") or ""),
                timeframe=str(payload.get("timeframe") or ""),
                history_since=str(payload.get("history_since") or ""),
                file_name=str(payload.get("file_name") or ""),
            )
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(result)

    @router.delete("/api/scripting/backtest/data")
    async def delete_backtest_data(data_path: str) -> JSONResponse:
        try:
            result = await manager.delete_data(data_path)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(result)

    @router.get("/api/scripting/backtests/latest")
    async def latest_backtest(script_path: str | None = None) -> JSONResponse:
        try:
            payload = await manager.latest(script_path)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse({"job": payload})

    @router.get("/api/scripting/backtests")
    async def list_backtests(script_path: str | None = None) -> JSONResponse:
        try:
            jobs = await manager.jobs(script_path)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse({"jobs": jobs})

    @router.post("/api/scripting/backtests")
    async def start_backtest(payload: dict) -> JSONResponse:
        try:
            arguments = {
                "script_path": str(payload.get("script_path") or ""),
                "base_revision": str(payload.get("base_revision") or ""),
                "data_path": str(payload.get("data_path") or ""),
                "time_from": _timestamp(payload.get("time_from"), "time_from"),
                "time_to": _timestamp(payload.get("time_to"), "time_to"),
            }
            if "input_values" in payload:
                jobs = await manager.start_variants(
                    **arguments,
                    input_values=payload.get("input_values"),
                )
                return JSONResponse(
                    {"jobs": jobs, "max_concurrent": 10},
                    status_code=202,
                )
            result = await manager.start(**arguments)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(result, status_code=202)

    @router.delete("/api/scripting/backtests")
    async def delete_backtest_results(script_path: str) -> JSONResponse:
        try:
            result = await manager.delete_results(script_path)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(result)

    @router.get("/api/scripting/backtests/{job_id}")
    async def get_backtest(job_id: str) -> JSONResponse:
        try:
            payload = await manager.status(job_id)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(payload)

    @router.get("/api/scripting/backtests/{job_id}/log")
    async def get_backtest_log(
        job_id: str,
        offset: int = 0,
        max_bytes: int = 131072,
    ) -> JSONResponse:
        try:
            payload = await manager.read_log(
                job_id,
                offset=offset,
                max_bytes=max_bytes,
            )
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(payload)

    @router.post("/api/scripting/backtests/{job_id}/stop")
    async def stop_backtest(job_id: str) -> JSONResponse:
        try:
            payload = await manager.stop(job_id)
        except ScriptingBacktestError as exc:
            return _error_response(exc)
        return JSONResponse(payload)

    @router.websocket("/ws/scripting/backtests/{job_id}")
    async def backtest_ws(ws: WebSocket, job_id: str) -> None:
        await ws.accept()
        try:
            offset = max(0, int(ws.query_params.get("offset", "0")))
            while True:
                status = await manager.status(job_id)
                log = await manager.read_log(job_id, offset=offset, max_bytes=65536)
                offset = int(log["next_offset"])
                await ws.send_json({
                    "type": "backtest_update",
                    "job": status,
                    "log": log,
                })
                if status["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                    if log["eof"]:
                        await ws.close()
                        return
                await asyncio.sleep(0.25)
        except WebSocketDisconnect:
            return
        except ScriptingBacktestError as exc:
            await ws.send_json({"type": "error", "error": str(exc), "code": exc.code})
            await ws.close(code=4404 if exc.status_code == 404 else 1011)

    return router
