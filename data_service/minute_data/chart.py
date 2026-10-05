"""Read-only chart access to the shared minute dataset."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect
from pynecore.core.exchange_policy import tradingview_hides_zero_volume

from .core import Market


def chart_market(service, session) -> Market | None:
    spec = session.spec
    market = Market(spec.exchange, spec.symbol, spec.market_type)
    if spec.provider == "ccxt" and spec.timeframe != "1m" and market.key in service.config:
        return market
    return None


def overlay_live(payload, live, market, limit=5000, before=None, after=None, *, keep_empty=False):
    bars = {bar["time"]: bar for bar in payload["bars"]}
    for bar in live:
        ts = bar["time"]
        if before is not None and ts >= before or after is not None and ts <= after:
            continue
        if tradingview_hides_zero_volume(market.exchange) and bar["volume"] <= 0:
            continue
        old = bars.get(ts)
        # REST/session/archive rows are authoritative; never add volumes together.
        if old and (old["source"] != "trades" or old["volume"] > bar["volume"]):
            continue
        bars[ts] = bar
    ordered = sorted(bars.values(), key=lambda bar: bar["time"])
    result = {**payload, "bars": ordered[:limit] if after is not None else ordered[-limit:]}
    if len(ordered) > limit:
        result["has_after" if after is not None else "has_before"] = True
    if not keep_empty and tradingview_hides_zero_volume(market.exchange):
        result["bars"] = [bar for bar in result["bars"] if bar["volume"] > 0]
    return result


def read_chart_window(path: Path, market: Market, limit=5000, before=None, after=None, live=(), *, keep_empty=False) -> dict:
    result = {"bars": [], "has_before": False, "has_after": False, "interval": 60,
              "plots": [], "trades": [], "plotchars": [], "overlays_ready": True}
    if not path.exists():
        return overlay_live(result, live, market, limit, before, after)
    limit = max(1, min(5000, int(limit)))
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("BEGIN")
        where = "market=?" + (" AND volume>0" if not keep_empty and tradingview_hides_zero_volume(market.exchange) else "")
        bound = " AND ts>?" if after is not None else " AND ts<?" if before is not None else ""
        params = [market.key]
        if bound:
            params.append(after if after is not None else before)
        order = "ASC" if after is not None else "DESC"
        rows = db.execute(
            f"SELECT ts,open,high,low,close,volume,updated_at,source FROM candles WHERE {where}{bound} ORDER BY ts {order} LIMIT ?",
            [*params, limit],
        ).fetchall()
        if after is None:
            rows.reverse()
        result["bars"] = [dict(zip(("time", "open", "high", "low", "close", "volume", "updated_at", "source"), row)) for row in rows]
        if live and tradingview_hides_zero_volume(market.exchange):
            # Hidden confirmed zero-volume rows still outrank provisional trades.
            hidden = {row[0] for row in db.execute(
                "SELECT ts FROM candles WHERE market=? AND volume=0 AND source!='trades' AND ts IN (%s)"
                % ",".join("?" for _ in live), [market.key, *(bar["time"] for bar in live)])}
            live = [bar for bar in live if bar["time"] not in hidden]
        result = overlay_live(result, live, market, limit, before, after, keep_empty=keep_empty)
        if result["bars"]:
            result["has_before"] |= db.execute(f"SELECT 1 FROM candles WHERE {where} AND ts<? LIMIT 1", (market.key, result["bars"][0]["time"])).fetchone() is not None
            result["has_after"] |= db.execute(f"SELECT 1 FROM candles WHERE {where} AND ts>? LIMIT 1", (market.key, result["bars"][-1]["time"])).fetchone() is not None
        return result
    except sqlite3.OperationalError as exc:
        # The worker may still be creating the database on first startup.
        if "no such table" in str(exc):
            return overlay_live(result, live, market, limit, before, after)
        raise
    finally:
        db.close()


async def stream_chart(ws: WebSocket, registry, session_id: str) -> None:
    session = registry.get(session_id)
    service = registry.minute_data
    market = chart_market(service, session) if session else None
    await ws.accept()
    if market is None:
        await ws.close(code=4404)
        return
    path = service.data_dir / "cache" / "minute_candles.sqlite"
    previous = None
    heartbeat = 0.0
    stored = None
    read_at = 0.0
    try:
        while registry.get(session_id) is session and market.key in service.config:
            # Disk corrections retain their one-second cadence. Live snapshots
            # bypass disk and boundary guards, using the existing worker/feed.
            now = time.monotonic()
            if stored is None or now - read_at >= 1:
                stored = await asyncio.to_thread(read_chart_window, path, market, 12, keep_empty=True)
                read_at = time.monotonic()
            live = service.live_rows(market.key)
            payload = overlay_live(stored, live, market, 12)
            if payload != previous:
                await ws.send_json({"type": "minute_window", **payload})
                previous = payload
                heartbeat = time.monotonic()
            elif time.monotonic() - heartbeat >= 5:
                await ws.send_json({"type": "minute_ping"})
                heartbeat = time.monotonic()
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
        await ws.close(code=4404)
    except (WebSocketDisconnect, RuntimeError):
        pass
