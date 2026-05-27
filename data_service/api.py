from __future__ import annotations

from config import DataServiceConfig
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from datetime import datetime, UTC
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

        current_open_ts = None
        if ohlcv_path.exists():
            try:
                with OHLCVReader(ohlcv_path) as ohlcv_reader:
                    end_ts = ohlcv_reader.end_timestamp
                    interval = ohlcv_reader.interval
                    if end_ts is not None and interval is not None:
                        now_ts = int(datetime.now(UTC).timestamp())
                        # Exclude only the actual in-progress open bar. The plot CSV usually
                        # ends at the latest confirmed bar, especially when OKX hides the
                        # zero-volume current bar, so dropping the last CSV row unconditionally
                        # hides one valid confirmed plot point on initial chart load.
                        if int(end_ts) <= now_ts < int(end_ts) + int(interval):
                            current_open_ts = int(end_ts)
                    ohlcv_reader.close()
            except Exception as e:
                print(f"[api] Failed to read OHLCV end timestamp: {e}")

        # Read CSV file and build plot data
        result = []
        try:
            with CSVReader(plot_path) as reader:
                # Collect all candles
                candles = []
                for candle in reader:
                    if current_open_ts is not None and int(candle.timestamp) >= current_open_ts:
                        continue
                    candles.append(candle)

                # Limit the number of candles
                start_idx = max(0, len(candles) - limit)
                candles = candles[start_idx:]

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

        # The chart must receive the same visible candle set as the runner.
        # OKX hides zero-volume bars like TradingView; BITGET/Hyperliquid keep them visible.
        skip_zero_volume = cfg is not None and cfg.exchange.upper() == "OKX"
        out: List[Dict[str, Any]] = []
        with OHLCVReader(ohlcv_path) as reader:
            if reader.start_timestamp is None:
                return JSONResponse([])
            candles = list(
                reader.read_from(
                    reader.start_timestamp,
                    reader.end_timestamp,
                    skip_zero_volume=skip_zero_volume,
                )
            )
            for c in candles[-limit:]:
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
