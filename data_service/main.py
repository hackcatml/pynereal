from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from config import load_config
from state import DataState
from ws_manager import WSManager
from ohlcv_paths import make_ohlcv_paths
from api import build_api_router
from ui import build_ui_router
from collector_loop import watch_trades_loop, fix_missing_bars_loop
from file_update_loop import file_update_loop


async def main() -> None:
    cfg = load_config()

    ohlcv_path, toml_path = make_ohlcv_paths(cfg.provider, cfg.exchange, cfg.symbol, cfg.timeframe)

    state = DataState()
    ws_manager = WSManager()

    app = FastAPI()
    app.include_router(build_ui_router())
    app.include_router(build_api_router(ohlcv_path))

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive
        except WebSocketDisconnect:
            await ws_manager.disconnect(ws)
        except Exception:
            await ws_manager.disconnect(ws)

    async def broadcast_bar(bar: list) -> None:
        payload = {
            "type": "bar",
            "data": {
                "time": int(bar[0] // 1000),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
                "volume": float(bar[5]),
            },
        }
        await ws_manager.broadcast_json(payload)

    async def emit_event(payload: dict) -> None:
        await ws_manager.broadcast_json(payload)

    # Background tasks
    t1 = asyncio.create_task(
        watch_trades_loop(
            cfg.exchange,
            cfg.symbol,
            cfg.timeframe,
            state,
            on_bar=broadcast_bar,
        )
    )
    t2 = asyncio.create_task(fix_missing_bars_loop(cfg.exchange, cfg.timeframe, state))
    t3 = asyncio.create_task(
        file_update_loop(
            provider=cfg.provider,
            exchange=cfg.exchange,
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            ohlcv_path=ohlcv_path,
            toml_path=toml_path,
            state=state,
            emit_event=emit_event,
        )
    )

    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.host, port=cfg.port, loop="asyncio", lifespan="off")
    )
    t4 = asyncio.create_task(server.serve())

    await asyncio.gather(t1, t2, t3, t4)


if __name__ == "__main__":
    asyncio.run(main())
