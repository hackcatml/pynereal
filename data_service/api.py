from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import Any, Dict, List

from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.core.csv_file import CSVReader


def build_api_router(plot_path: Path, ohlcv_path: Path, trades_history: List[Dict[str, Any]] = None,
                    plot_options: Dict[str, Dict[str, Any]] = None,
                    plotchar_history: List[Dict[str, Any]] = None) -> APIRouter:
    r = APIRouter()

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
        return JSONResponse(out)

    return r
