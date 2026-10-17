from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .service import TelegramAIService, TelegramRestartUnavailable


def build_telegram_router(service: TelegramAIService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/telegram/status")
    async def status():
        return JSONResponse(service.status(), headers={"Cache-Control": "no-store"})

    @router.post("/api/telegram/restart")
    async def restart():
        try:
            await service.restart()
        except TelegramRestartUnavailable as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(service.status(), status_code=202)

    return router
