from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response


def build_ui_router() -> APIRouter:
    r = APIRouter()
    template_path = Path(__file__).parent / "templates" / "index.html"
    templates_dir = Path(__file__).parent / "templates"
    static_files = {
        "styles.css": "text/css",
        "state.js": "text/javascript",
        "ui.js": "text/javascript",
        "chart.js": "text/javascript",
        "data.js": "text/javascript",
        "ws.js": "text/javascript",
        "main.js": "text/javascript",
    }

    @r.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(content=template_path.read_text(encoding="utf-8"))

    @r.get("/static/{filename}")
    def static_file(filename: str) -> Response:
        if filename not in static_files:
            return Response(status_code=404)
        file_path = templates_dir / filename
        return Response(
            content=file_path.read_text(encoding="utf-8"),
            media_type=static_files[filename],
        )

    return r
