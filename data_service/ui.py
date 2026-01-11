from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response


def build_ui_router() -> APIRouter:
    r = APIRouter()
    template_path = Path(__file__).parent / "templates" / "index.html"
    style_path = Path(__file__).parent / "templates" / "styles.css"

    @r.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(content=template_path.read_text(encoding="utf-8"))

    @r.get("/static/styles.css")
    def styles() -> Response:
        return Response(content=style_path.read_text(encoding="utf-8"), media_type="text/css")

    return r
