"""Read-only last-trade quotes from existing session feeds; no exchange requests."""
from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from decimal import Decimal

from .commands import match_sessions, session_label
from .session_control import SessionCommandError


def _positive_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _quote(session: dict, feed) -> str:
    _, price, timestamp = feed.raw_trade_cursor()
    price = _positive_number(price)
    lines = [session_label(session)]
    if price is None:
        lines.append("Price unavailable: no valid trades received yet.")
    else:
        value = format(Decimal(str(price)), ",f")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        quote_currency = session["symbol"].partition("/")[2].partition(":")[0]
        lines.append(f"Last trade: {value} {quote_currency}".rstrip())
        timestamp = _positive_number(timestamp)
        try:
            traded_at = datetime.fromtimestamp(timestamp / 1000, UTC) if timestamp else None
        except (ValueError, OverflowError, OSError):
            traded_at = None
        if traded_at is None:
            lines.append("Trade time unavailable; price freshness is unknown.")
        else:
            lines.append(f"Trade time (UTC): {traded_at.isoformat(timespec='milliseconds')}")
            age = time.time() - timestamp / 1000
            if age > 60:
                lines.append(f"Last trade is {int(age):,}s old; the current price may differ.")
            elif age < -5:
                lines.append("Trade time is ahead of the server clock; price freshness is uncertain.")
    if session["collector"] != "running":
        lines.append(f"Feed: {session['collector']}. A live price is not available.")
    return "\n".join(lines)


def price_command(control, request: dict) -> dict:
    if control is None:
        raise SessionCommandError("Session prices are unavailable. Please retry /price later.")
    if request["operation"] == "list":
        sessions = control.sessions()
        query = request.get("query", "")
        if query:
            sessions = match_sessions(sessions, query)
        if not query or len(sessions) != 1:
            return {"view": "list", "purpose": "price", "sessions": sessions, "query": query, "page": 0}
        target = sessions[0]
    elif request["operation"] == "price":
        target = request["target"]
    else:
        raise SessionCommandError("Unsupported price request. Use /price again.")
    session = control.registry.get(target["session_id"])
    if session is None:
        raise SessionCommandError("The session was removed. Use /price to select it again.")
    current = control.snapshot(target["session_id"])
    for key in ("identity", "exchange", "symbol", "timeframe", "market_type"):
        if current[key] != target.get(key):
            raise SessionCommandError("The session changed. Use /price to select it again.")
    return {"view": "price", "report": _quote(current, session.feed)}
