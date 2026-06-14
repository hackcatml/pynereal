from __future__ import annotations

import ast
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

    def _extract_script_title_from_source(source: str) -> str | None:
        try:
            tree = ast.parse(source)
        except Exception:
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id != "script":
                    continue
                if func.attr not in {"strategy", "indicator", "library"}:
                    continue
                for kw in node.keywords:
                    if kw.arg == "title" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        return kw.value.value or "No title"
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    return node.args[0].value or "No title"
        return None

    def _resolve_script_path() -> Path:
        if cfg is None:
            raise ValueError("config unavailable")
        script_name = (cfg.realtime_section or {}).get("script_name") or ""
        if not isinstance(script_name, str) or not script_name:
            raise ValueError("script_name is empty in realtime_trade.toml")

        scripts_dir = app_state.scripts_dir.resolve()
        script_path = (scripts_dir / script_name).resolve()
        try:
            script_path.relative_to(scripts_dir)
        except ValueError:
            raise ValueError("script path must be inside scripts directory")
        if script_path.suffix != ".py":
            raise ValueError("script must be a .py file")
        return script_path

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
        info = chart_info or {}
        return JSONResponse({
            "exchange": info.get("exchange"),
            "symbol": info.get("symbol"),
            "timeframe": info.get("timeframe"),
            "provider": info.get("provider"),
            "script_title": info.get("script_title"),
            "script_source_name": info.get("script_source_name"),
            "has_script_source": bool(info.get("script_source")),
        })

    @r.get("/api/script-source")
    def get_script_source() -> JSONResponse:
        info = chart_info or {}
        title = info.get("script_title") or "No title"
        name = info.get("script_source_name") or ""
        source = info.get("script_source") or ""
        # 디스크 파일이 최신 저장본이므로 우선 읽는다. 다른 기기에서 저장한 내용이
        # pre_run 전에도 즉시 반영되도록(메모리 chart_info는 fallback).
        try:
            script_path = _resolve_script_path()
            if script_path.exists():
                source = script_path.read_text(encoding="utf-8")
                name = script_path.name
                title = _extract_script_title_from_source(source) or title
        except Exception:
            pass
        return JSONResponse({
            "title": title,
            "name": name,
            "source": source,
        })

    @r.post("/api/script-source")
    def save_script_source(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        source = payload.get("source")
        if not isinstance(source, str):
            return JSONResponse({"error": "source must be string"}, status_code=400)

        try:
            script_path = _resolve_script_path()
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        if not script_path.exists():
            return JSONResponse({"error": f"script not found: {script_path.name}"}, status_code=404)

        try:
            script_path.write_text(source, encoding="utf-8")
        except Exception as e:
            return JSONResponse({"error": f"failed to save script: {e}"}, status_code=500)

        info = chart_info or {}
        title = _extract_script_title_from_source(source) or info.get("script_title") or "No title"
        info["script_title"] = title
        info["script_source_name"] = script_path.name
        info["script_source"] = source
        return JSONResponse({
            "ok": True,
            "title": title,
            "name": script_path.name,
            "source": source,
        })

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
