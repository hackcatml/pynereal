from __future__ import annotations

import asyncio
import json

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from config import load_config
from state import DataState
from ws_manager import WSManager
from ohlcv_paths import make_ohlcv_paths, make_plot_path
from api import build_api_router
from ui import build_ui_router
from collector_loop import watch_trades_loop, fix_missing_bars_loop
from file_update_loop import file_update_loop


async def main() -> None:
    cfg = load_config()

    ohlcv_path, toml_path = make_ohlcv_paths(cfg.provider, cfg.exchange, cfg.symbol, cfg.timeframe)
    plot_path = make_plot_path(cfg)

    state = DataState()
    ws_manager = WSManager()

    # Store trade events in memory
    trades_history = []
    # Store plot options (title -> options mapping)
    plot_options = {}

    app = FastAPI()
    app.include_router(build_ui_router())
    app.include_router(build_api_router(plot_path, ohlcv_path, trades_history, plot_options))

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)

        # Send pending prerun event if exists (for runner_service that connects after history download)
        async with state.lock:
            if state.pending_prerun_event is not None:
                try:
                    await ws.send_json(state.pending_prerun_event)
                    # print("[data_service] Sent pending prerun event, waiting for ACK")
                except Exception as e:
                    print(f"[data_service] Failed to send pending event: {e}")

        try:
            while True:
                msg_text = await ws.receive_text()

                # Try to parse as JSON for trade events
                try:
                    msg = json.loads(msg_text)

                    # Handle batch (list) or single event (dict)
                    events = msg if isinstance(msg, list) else [msg]

                    for event in events:
                        msg_type = event.get("type")
                        if msg_type == "last_bar_open_fix":
                            last_bar_index = event.get("last_bar_index", -1)
                            if last_bar_index > 0:
                                try:
                                    from pynecore.core.ohlcv_file import OHLCVReader
                                    with OHLCVReader(ohlcv_path) as reader:
                                        last_bar = reader.read(last_bar_index)
                                        payload = {
                                            "type": "last_bar_open_fix",
                                            "data": {
                                                "time": int(last_bar.timestamp),
                                                "open": float(last_bar.open),
                                                "high": float(last_bar.high),
                                                "low": float(last_bar.low),
                                                "close": float(last_bar.close),
                                                "volume": float(last_bar.volume),
                                            },
                                        }
                                        await ws_manager.broadcast_json(payload)
                                        reader.close()
                                except Exception as e:
                                    print(f"[data_service] Failed to send confirmed bar: {e}")
                        elif msg_type in ("trade_entry", "trade_close"):
                            # Store trade event in history
                            if event not in trades_history:
                                trades_history.append(event)
                            await ws_manager.broadcast_json(event)
                        elif msg_type == "plot_options":
                            # Store plot options from runner_service
                            plot_options.update(event.get("data", {}))
                            confirmed_bar_index = event.get("confirmed_bar_index", -1)
                            # print(f"[data_service] Received plot_options: {plot_options}, confirmed_bar_index: {confirmed_bar_index}")

                            # Read and broadcast plot data after confirmed_bar_index
                            if plot_options and confirmed_bar_index >= 0:
                                try:
                                    if plot_path.exists():
                                        from pynecore.core.csv_file import CSVReader

                                        with CSVReader(plot_path) as reader:
                                            # Read a candle at the confirmed_bar_index
                                            candle = reader.read(confirmed_bar_index)

                                            # Broadcast plot data for each title
                                            for title in plot_options.keys():
                                                value = candle.extra_fields.get(title)
                                                plot_data_event = {
                                                    "type": "plot_data",
                                                    "title": title,
                                                    "time": int(candle.timestamp),
                                                    "value": None if (value == "" or value is None) else float(value)
                                                }
                                                await ws_manager.broadcast_json(plot_data_event)
                                            reader.close()
                                            # print(f"[data_service] Broadcasted plot data")
                                except Exception as e:
                                    print(f"[data_service] Failed to broadcast plot data: {e}")
                        elif msg_type == "ack_prerun_ready_after_history_download":
                            # Clear pending event when ACK is received from runner_service
                            async with state.lock:
                                if state.pending_prerun_event is not None:
                                    state.pending_prerun_event = None
                except json.JSONDecodeError:
                    # Not JSON, likely a keepalive ping
                    pass
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
            config=cfg,
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
