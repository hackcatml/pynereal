from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

_TEMPLATES = Path(__file__).parent / "templates"

_STATIC_FILES = {
    "styles.css": "text/css",
    "state.js": "text/javascript",
    "ui.js": "text/javascript",
    "chart.js": "text/javascript",
    "data.js": "text/javascript",
    "ws.js": "text/javascript",
    "main.js": "text/javascript",
    "dashboard.js": "text/javascript",
    "dashboard.css": "text/css",
}


def build_ui_router() -> APIRouter:
    r = APIRouter()

    @r.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(content=(_TEMPLATES / "dashboard.html").read_text(encoding="utf-8"))

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
        return HTMLResponse(content=html)

    @r.get("/static/{filename}")
    def static_file(filename: str) -> Response:
        if filename not in _STATIC_FILES:
            return Response(status_code=404)
        file_path = _TEMPLATES / filename
        if not file_path.exists():
            return Response(status_code=404)
        return Response(
            content=file_path.read_text(encoding="utf-8"),
            media_type=_STATIC_FILES[filename],
        )

    return r
