from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, Response

_TEMPLATES = Path(__file__).parent / "templates"

_STATIC_IMAGES = {
    "pwa-icon-192.png",
    "pwa-icon-512.png",
    "pwa-icon-maskable-512.png",
    "apple-touch-icon.png",
}

_STATIC_FILES = {
    "styles.css": "text/css",
    "pwa_chart.css": "text/css",
    "pwa_chart.js": "text/javascript",
    "editor.css": "text/css",
    "codemirror.css": "text/css",
    "state.js": "text/javascript",
    "editor.js": "text/javascript",
    "codemirror.js": "text/javascript",
    "scripting.js": "text/javascript",
    "scripting_ai.js": "text/javascript",
    "scripting_ai.css": "text/css",
    "scripting_backtest.js": "text/javascript",
    "scripting_backtest.css": "text/css",
    "scripting_backtest_chart.js": "text/javascript",
    "scripting_backtest_chart.css": "text/css",
    "ui.js": "text/javascript",
    "bgcolor.js": "text/javascript",
    "chart.js": "text/javascript",
    "measure.js": "text/javascript",
    "measure.css": "text/css",
    "data.js": "text/javascript",
    "ws.js": "text/javascript",
    "main.js": "text/javascript",
    "dashboard.js": "text/javascript",
    "dashboard.css": "text/css",
    "notifications.js": "text/javascript",
    "notifications.css": "text/css",
}


def build_ui_router() -> APIRouter:
    r = APIRouter()

    @r.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(
            content=(_TEMPLATES / "dashboard.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )

    @r.get("/manifest.webmanifest")
    def manifest() -> Response:
        return Response(
            content=(_TEMPLATES / "manifest.webmanifest").read_text(encoding="utf-8"),
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )

    @r.get("/s/{session_id}", response_class=HTMLResponse)
    def chart_page(session_id: str) -> HTMLResponse:
        html = (_TEMPLATES / "index.html").read_text(encoding="utf-8")
        config_script = (
            "<script>\n"
            f"  window.RUNTIME_ID = {json.dumps(session_id)};\n"
            f"  window.API_BASE = {json.dumps('/api/' + session_id)};\n"
            f"  window.WS_PATH = {json.dumps('/ws/' + session_id)};\n"
            "</script>"
        )
        html = html.replace("<!--RUNTIME_CONFIG-->", config_script)
        return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})

    @r.get("/backtests/{job_id}", response_class=HTMLResponse)
    def backtest_chart_page(job_id: str) -> HTMLResponse:
        html = (_TEMPLATES / "scripting_backtest_chart.html").read_text(encoding="utf-8")
        config_script = (
            "<script>\n"
            f"  window.BACKTEST_JOB_ID = {json.dumps(job_id)};\n"
            "</script>"
        )
        return HTMLResponse(
            content=html.replace("<!--BACKTEST_CONFIG-->", config_script),
            headers={"Cache-Control": "no-cache"},
        )

    @r.get("/static/{filename}")
    def static_file(filename: str) -> Response:
        if filename not in _STATIC_FILES and filename not in _STATIC_IMAGES:
            return Response(status_code=404)
        file_path = _TEMPLATES / filename
        if not file_path.exists():
            return Response(status_code=404)
        if filename in _STATIC_IMAGES:
            return FileResponse(
                file_path,
                media_type="image/png",
                headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
            )
        return Response(
            content=file_path.read_text(encoding="utf-8"),
            media_type=_STATIC_FILES[filename],
        )

    return r
