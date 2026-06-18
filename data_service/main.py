from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from config import load_hub_config, load_initial_sessions
from registry import SessionRegistry
from api import build_session_api_router, build_control_router, build_validation_router
from ui import build_ui_router

_BANNER = r"""
    ____                   ____             __
   / __ \__  ______  ___  / __ \___  ____ _/ /
  / /_/ / / / / __ \/ _ \/ /_/ / _ \/ __ `/ /
 / ____/ /_/ / / / /  __/ _, _/  __/ /_/ / /
/_/    \__, /_/ /_/\___/_/ |_|\___/\__,_/_/
      /____/
"""


def build_app(registry: SessionRegistry) -> FastAPI:
    app = FastAPI()
    app.include_router(build_ui_router())
    app.include_router(build_control_router(registry))
    app.include_router(build_validation_router())
    app.include_router(build_session_api_router(registry))

    @app.websocket("/ws/hub")
    async def hub_ws(ws: WebSocket):
        await registry.hub_ws.connect(ws)
        await registry.hub_ws.send(ws, {"type": "sessions", "sessions": registry.snapshots()})
        try:
            while True:
                # Dashboard clients only receive pushes; ignore inbound keepalive.
                await ws.receive_text()
        except WebSocketDisconnect:
            await registry.hub_ws.disconnect(ws)
        except Exception:
            await registry.hub_ws.disconnect(ws)

    @app.websocket("/ws/{session_id}")
    async def session_ws(ws: WebSocket, session_id: str):
        rt = registry.get(session_id)
        if rt is None:
            # Legacy alias: a bare /ws or unknown id maps to the sole session, if any.
            rt = _default_session(registry)
            if rt is None:
                await ws.accept()
                await ws.close(code=4404)
                return
        await rt.on_connect(ws)
        try:
            while True:
                msg_text = await ws.receive_text()
                await rt.handle_text(ws, msg_text)
        except WebSocketDisconnect:
            await rt.on_disconnect(ws)
        except Exception:
            await rt.on_disconnect(ws)

    # Legacy single-session websocket alias.
    @app.websocket("/ws")
    async def legacy_ws(ws: WebSocket):
        rt = _default_session(registry)
        if rt is None:
            await ws.accept()
            await ws.close(code=4404)
            return
        await rt.on_connect(ws)
        try:
            while True:
                msg_text = await ws.receive_text()
                await rt.handle_text(ws, msg_text)
        except WebSocketDisconnect:
            await rt.on_disconnect(ws)
        except Exception:
            await rt.on_disconnect(ws)

    return app


def _default_session(registry: SessionRegistry):
    if len(registry.sessions) == 1:
        return next(iter(registry.sessions.values()))
    return None


async def _hub_status_heartbeat(registry: SessionRegistry, interval: float = 10.0) -> None:
    """Periodically push the session snapshot to /ws/hub clients so the dashboard's
    'Last bar' / status stay fresh without each client polling /api/sessions.
    One broadcast serves all connected dashboards (no-op when none are connected)."""
    while True:
        await asyncio.sleep(interval)
        try:
            await registry.notify_hub()
        except Exception:
            pass


async def main() -> None:
    print(_BANNER)
    # Required by PyneCore's NOTICE file (Apache-2.0, Section 4d)
    print("Powered by PyneSys (https://pynesys.io)\n")

    cfg = load_hub_config()
    specs = load_initial_sessions()
    registry = SessionRegistry(port=cfg.port)
    app = build_app(registry)

    await registry.start_all(specs)
    heartbeat = asyncio.create_task(_hub_status_heartbeat(registry))

    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.host, port=cfg.port, loop="asyncio", lifespan="off",
                       ws_ping_interval=None, ws_ping_timeout=None)
    )
    try:
        await server.serve()
    finally:
        heartbeat.cancel()
        await registry.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
