from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import Any, Dict, List

from pynecore.core.ohlcv_file import OHLCVReader


def build_api_router(ohlcv_path: Path) -> APIRouter:
    r = APIRouter()

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
