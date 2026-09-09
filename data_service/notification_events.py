"""Small, credential-free notification messages shared by alert producers."""
from __future__ import annotations

import re
import json
import math
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Callable


def notification_error(message: str) -> None:
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
          f"[notifications] {message}", file=sys.stderr)


def delivery_result(*, response=None, error=None, status: str = "sent") -> dict:
    result = {"status": status}
    if error is not None:
        result = {"status": "failed", "error_type": type(error).__name__}
        # A response timeout does not prove the receiver did not process the request.
        if "timeout" in type(error).__name__.lower():
            result["status"] = "unknown"
        response = getattr(error, "response", None)
    if response is not None:
        result["http_status"] = int(response.status_code)
        try:
            body = response.json()
        except (ValueError, TypeError):
            body = None
        if isinstance(body, dict):
            if body.get("ok") is False:
                result["status"] = "failed"
            business_status = body.get("status")
            if business_status in ("pending", "executed", "failed", "error", "success", "ok"):
                result["receiver_status"] = business_status
    return result


def recorded_result(result: dict) -> dict:
    """Normalize existing manual/verification outcomes without copying response bodies."""
    status = result.get("status")
    outcome = {"status": status if isinstance(status, str) else ("sent" if result.get("sent") else "failed")}
    error = str(result.get("error") or "")
    if "timeout" in error.lower():
        outcome["status"] = "unknown"
    if outcome["status"] == "skipped":
        outcome["status"] = "configuration_error"
    http_status = result.get("http_status") or (status if isinstance(status, int) else None)
    match = re.search(r"HTTP (\d{3})", error)
    if http_status or match:
        outcome["http_status"] = int(http_status or match[1])
    if "attempts" in result:
        outcome["attempts"] = result["attempts"]
    try:
        body = json.loads(result.get("body") or "null")
    except (ValueError, TypeError):
        body = None
    if isinstance(body, dict):
        if body.get("ok") is False:
            outcome["status"] = "failed"
        if body.get("status") in ("pending", "executed", "failed", "error", "success", "ok"):
            outcome["receiver_status"] = body["status"]
    return outcome


def safe_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")[:limit]
    text = re.sub(r"https?://[^\s\"'<>]+", "[URL omitted]", text)
    text = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{15,}\b", "[redacted]", text)
    return re.sub(
        r"(?i)(token|password|secret|api_?key|private_?key|chat_?id|authorization)"
        r"([\"']?\s*[:=]\s*[\"']?)[^\s,}\"']+", r"\1\2[redacted]", text,
    )


def safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "..."
    if isinstance(value, dict):
        return {
            safe_text(k, 80): ("[redacted]" if re.search(
                r"token|password|secret|api.?key|private.?key|chat.?id|authorization|url", str(k), re.I
            ) else safe_value(v, depth + 1))
            for k, v in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(item, depth + 1) for item in value[:30]]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return safe_text(value)


def alert_callback(publish: Callable | None, *, session_id: str, origin: str,
                   context: dict, signal: Any, candle_timestamp_ms=None,
                   event_key: str | None = None) -> Callable:
    base = {
        "event_key": event_key or uuid.uuid4().hex,
        "kind": "signal", "origin": origin, "session_id": session_id,
        "occurred_at": time.time(), "candle_timestamp_ms": candle_timestamp_ms,
        "context": context, "signal": signal,
    }

    def report(channel: str, result: dict) -> None:
        if publish is None:
            return
        try:
            publish({**base, channel: result})
        except Exception as exc:
            notification_error(f"enqueue failed: {type(exc).__name__}")
    return report
