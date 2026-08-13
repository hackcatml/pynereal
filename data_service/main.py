from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ai.provider.codex_service import CodexService
from account_data import AccountDataService
from asset_portfolio import AssetPortfolioService
from asset_transfer import AssetTransferService
from calendar_store import CalendarEventStore
from config import ensure_provider_config, load_hub_config, load_initial_sessions
from registry import SessionRegistry
from api import build_session_api_router, build_control_router, build_validation_router
from ui import build_ui_router
from update_service import UpdateService

_BANNER = r"""
    ____                   ____             __
   / __ \__  ______  ___  / __ \___  ____ _/ /
  / /_/ / / / / __ \/ _ \/ /_/ / _ \/ __ `/ /
 / ____/ /_/ / / / /  __/ _, _/  __/ /_/ / /
/_/    \__, /_/ /_/\___/_/ |_|\___/\__,_/_/
      /____/
"""


def build_app(
    registry: SessionRegistry,
    codex_service: CodexService,
    calendar_store: CalendarEventStore,
    account_data_service: AccountDataService,
    asset_portfolio_service: AssetPortfolioService,
    asset_transfer_service: AssetTransferService,
    update_service: UpdateService,
) -> FastAPI:
    app = FastAPI()
    app.include_router(build_ui_router())
    app.include_router(
        build_control_router(
            registry,
            codex_service,
            calendar_store,
            account_data_service,
            asset_portfolio_service,
            asset_transfer_service,
            update_service,
        )
    )
    app.include_router(build_validation_router())
    app.include_router(build_session_api_router(registry))

    @app.websocket("/ws/hub")
    async def hub_ws(ws: WebSocket):
        await registry.hub_ws.connect(ws)
        registry.retry_missing_symbol_logos()
        await registry.hub_ws.send(ws, {
            "type": "sessions",
            "sessions": registry.snapshots(),
            "ai_enabled": codex_service.enabled,
        })
        try:
            while True:
                # Dashboard clients only receive pushes; ignore inbound keepalive.
                await ws.receive_text()
        except WebSocketDisconnect:
            await registry.hub_ws.disconnect(ws)
        except Exception:
            await registry.hub_ws.disconnect(ws)

    @app.websocket("/ws/account")
    async def account_ws(ws: WebSocket):
        await account_data_service.connect_live(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            await account_data_service.disconnect_live(ws)
        except Exception:
            await account_data_service.disconnect_live(ws)

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


async def _hub_status_heartbeat(registry: SessionRegistry, interval: float = 1.0) -> None:
    """Periodically push the session snapshot to /ws/hub clients so the dashboard's
    'Last bar' / price / status stay fresh without each client polling /api/sessions.
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

    ensure_provider_config()
    cfg = load_hub_config()
    specs = load_initial_sessions()
    registry = SessionRegistry(port=cfg.port)
    calendar_store = CalendarEventStore(
        _PROJECT_ROOT / "workdir" / "config" / "calendar_events.json"
    )
    asset_portfolio_service = AssetPortfolioService(
        _PROJECT_ROOT / "workdir" / "config" / "providers.toml"
    )
    account_data_service = AccountDataService(
        _PROJECT_ROOT / "workdir" / "config" / "providers.toml"
    )
    asset_transfer_service = AssetTransferService(
        _PROJECT_ROOT / "workdir" / "config" / "providers.toml"
    )
    codex_service = CodexService(
        project_root=_PROJECT_ROOT,
        session_registry=registry,
        calendar_store=calendar_store,
        startup_enabled=os.environ.get("PYNEREAL_UPDATE_AI_ENABLED") != "0",
    )
    update_shutdown = asyncio.Event()
    update_service = UpdateService(
        repo_root=_PROJECT_ROOT,
        registry=registry,
        port=cfg.port,
        ai_enabled=lambda: codex_service.enabled,
        request_shutdown=update_shutdown.set,
    )
    registry.set_ai_instruction_handler(codex_service.handle_strategy_instruction)
    try:
        await codex_service.start()
    except Exception as e:
        print(f"[ai] Codex app-server startup failed: {e}")
    registry.set_strategy_evaluation_enabled(codex_service.running)
    app = build_app(
        registry,
        codex_service,
        calendar_store,
        account_data_service,
        asset_portfolio_service,
        asset_transfer_service,
        update_service,
    )

    await registry.start_all(specs)
    await asset_portfolio_service.start()
    heartbeat = asyncio.create_task(_hub_status_heartbeat(registry))

    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.host, port=cfg.port, loop="asyncio", lifespan="off",
                       ws_ping_interval=None, ws_ping_timeout=None)
    )

    async def stop_server_for_update() -> None:
        await update_shutdown.wait()
        server.should_exit = True

    async def finish_update_after_start() -> None:
        while not server.started and not server.should_exit:
            await asyncio.sleep(0.05)
        if server.started:
            await update_service.finish_restart()

    update_shutdown_task = asyncio.create_task(stop_server_for_update())
    update_finish_task = (
        asyncio.create_task(finish_update_after_start())
        if update_service.pending_restart()
        else None
    )
    try:
        await server.serve()
    finally:
        update_shutdown_task.cancel()
        shutdown_tasks = [update_shutdown_task]
        if update_finish_task is not None:
            update_finish_task.cancel()
            shutdown_tasks.append(update_finish_task)
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        heartbeat.cancel()
        try:
            await registry.shutdown()
        finally:
            try:
                await account_data_service.close()
            finally:
                try:
                    await asset_portfolio_service.close()
                finally:
                    await codex_service.close()
    if update_shutdown.is_set():
        update_service.apply_and_restart()


if __name__ == "__main__":
    asyncio.run(main())
