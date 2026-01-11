from __future__ import annotations

from config import DataServiceConfig
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import Any, Dict, List
from tomlkit import parse, dumps

from pynecore.cli.app import app_state

from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.core.csv_file import CSVReader


def build_api_router(plot_path: Path, ohlcv_path: Path, trades_history: List[Dict[str, Any]] = None,
                    plot_options: Dict[str, Dict[str, Any]] = None,
                    plotchar_history: List[Dict[str, Any]] = None,
                    chart_info: Dict[str, Any] | None = None,
                    cfg: DataServiceConfig = None) -> APIRouter:
    r = APIRouter()
    config_path = app_state.config_dir / "realtime_trade.toml"

    def _load_webhook_config() -> dict:
        webhook_section = cfg.webhook_section or {}
        return {
            "enabled": bool(webhook_section.get("enabled", False)),
            "telegram_notification": bool(webhook_section.get("telegram_notification", False)),
        }

    def _update_webhook_config(enabled: bool | None = None,
                               telegram_notification: bool | None = None) -> dict:
        config = parse(config_path.read_text(encoding="utf-8"))
        webhook = config["webhook"]
        if enabled is not None:
            webhook["enabled"] = enabled
            cfg.webhook_section["enabled"] = enabled
        if telegram_notification is not None:
            webhook["telegram_notification"] = telegram_notification
            cfg.webhook_section["telegram_notification"] = telegram_notification
        config_path.write_text(dumps(config), encoding="utf-8")
        return {
            "enabled": bool(webhook.get("enabled", False)),
            "telegram_notification": bool(webhook.get("telegram_notification", False)),
        }

    @r.get("/api/trades")
    def get_trades() -> JSONResponse:
        """Get all stored trade events (entry and close)"""
        if trades_history is None:
            return JSONResponse([])
        return JSONResponse(trades_history)

    @r.get("/api/plotchar")
    def get_plotchar() -> JSONResponse:
        """Get all stored plotchar events"""
        if plotchar_history is None:
            return JSONResponse([])
        return JSONResponse(plotchar_history)

    @r.get("/api/plot")
    def get_plot(limit: int = 2000) -> JSONResponse:
        """Get plot data from CSV file with options"""
        if plot_options is None or not plot_options:
            return JSONResponse([])

        if not plot_path.exists():
            return JSONResponse([])

        # Read CSV file and build plot data
        result = []
        try:
            with CSVReader(plot_path) as reader:
                # Collect all candles
                candles = []
                for candle in reader:
                    candles.append(candle)

                # Limit the number of candles
                start_idx = max(0, len(candles) - limit)
                candles = candles[start_idx:-1]

                # Build plot data for each title
                for title, options in plot_options.items():
                    series_data = []
                    for candle in candles:
                        # Get value from extra_fields using title as key
                        value = candle.extra_fields.get(title)

                        # Always append data point (convert "" to None for JSON null)
                        series_data.append({
                            "time": int(candle.timestamp),
                            "value": None if (value == "" or value is None) else float(value)
                        })

                    result.append({
                        "title": title,
                        "color": options.get("color"),
                        "linewidth": options.get("linewidth"),
                        "style": options.get("style"),
                        "data": series_data
                    })
                # Close CSV reader
                reader.close()

        except Exception as e:
            print(f"[api] Failed to read plot CSV: {e}")
            return JSONResponse([])

        return JSONResponse(result)

    @r.get("/api/ohlcv")
    def get_ohlcv(limit: int = 2000) -> JSONResponse:
        if not ohlcv_path.exists():
            return JSONResponse([])

        out: List[Dict[str, Any]] = []
        with OHLCVReader(ohlcv_path) as reader:
            size = reader.size
            start = max(0, size - limit)
            for i in range(start, size):
                c = reader.read(i)
                out.append(
                    {
                        "time": int(c.timestamp),
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    }
                )
            reader.close()
        return JSONResponse(out)

    @r.get("/api/info")
    def get_info() -> JSONResponse:
        return JSONResponse(chart_info or {})

    @r.get("/api/webhook-config")
    def get_webhook_config() -> JSONResponse:
        return JSONResponse(_load_webhook_config())

    @r.post("/api/webhook-config")
    def update_webhook_config(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        enabled = payload.get("enabled")
        telegram_notification = payload.get("telegram_notification")
        if enabled is not None and not isinstance(enabled, bool):
            return JSONResponse({"error": "enabled must be boolean"}, status_code=400)
        if telegram_notification is not None and not isinstance(telegram_notification, bool):
            return JSONResponse({"error": "telegram_notification must be boolean"}, status_code=400)
        updated = _update_webhook_config(enabled=enabled, telegram_notification=telegram_notification)
        return JSONResponse(updated)

    return r
