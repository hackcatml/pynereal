from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import requests

try:
    from ..config import (
        default_telegram_chat_id,
        default_telegram_token,
        default_webhook_url,
    )
    from ..manual_alerts import post_telegram_message, webhook_request_timeout
    from ..prerun_scheduler import timeframe_seconds
except ImportError:
    from config import (
        default_telegram_chat_id,
        default_telegram_token,
        default_webhook_url,
    )
    from manual_alerts import post_telegram_message, webhook_request_timeout
    from prerun_scheduler import timeframe_seconds


_WEBHOOK_RETRY_DELAYS = (1.0, 2.0, 4.0)
_QUEUE_LIMIT = 256


@dataclass(frozen=True)
class VerificationDeliveryRequest:
    session_id: str
    script_title: str
    exchange: str
    symbol: str
    timeframe: str
    candle_timestamp_ms: int
    discrepancy: str
    order_signal: dict[str, Any]
    primary_bar: Any
    finalized_bar: Any
    bar_difference: dict[str, Any]
    authoritative_source: str
    notification_toggles: dict[str, bool]
    webhook_config: dict[str, Any]
    on_result: Callable[[dict[str, Any]], None] | None = None


def verification_event_id(request: VerificationDeliveryRequest) -> str:
    signal = request.order_signal
    identity = {
        "session_id": request.session_id,
        "candle_timestamp_ms": request.candle_timestamp_ms,
        "discrepancy": request.discrepancy,
        "action": signal.get("action") or "",
        "order_id": signal.get("order_id") or "",
        "exit_id": signal.get("exit_id") or "",
        "occurrence_index": int(signal.get("occurrence_index") or 0),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strategy_webhook_payload(alert_message: Any) -> Any:
    if not isinstance(alert_message, str) or not alert_message.strip():
        raise ValueError("original alert_message is empty")
    normalized = re.sub(
        r'"message"\s*:\s*(?![{["0-9])([A-Za-z][A-Za-z0-9 ]*)',
        r'"message": "\1"',
        alert_message,
    )
    parsed = json.loads(normalized)
    if not isinstance(parsed, dict) or "message" not in parsed:
        raise ValueError("original alert_message has no message field")
    payload = parsed.get("message")
    if payload == "" or payload is None:
        raise ValueError("original alert_message payload is empty")
    return payload


def _short(value: Any, limit: int = 1200) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _format_timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _format_bar(bar: Any) -> str:
    if not isinstance(bar, (list, tuple)) or len(bar) < 6:
        return "unavailable"
    return (
        f"O={bar[1]} H={bar[2]} L={bar[3]} "
        f"C={bar[4]} V={bar[5]}"
    )


def _format_difference(difference: dict[str, Any]) -> str:
    if not difference:
        return "none"
    parts = []
    for field, values in difference.items():
        if not isinstance(values, dict):
            continue
        parts.append(
            f"{field}: {values.get('primary')} -> {values.get('finalized')}"
        )
    return ", ".join(parts) or "none"


class VerificationDeliveryService:
    """Deliver verification findings without blocking strategy calculations."""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = Path(cache_path)
        self._queue: asyncio.Queue[VerificationDeliveryRequest] = asyncio.Queue(
            maxsize=_QUEUE_LIMIT
        )
        self._worker: asyncio.Task | None = None
        self._closed = False
        self._db_initialized = False

    def enqueue(self, request: VerificationDeliveryRequest) -> bool:
        if self._closed:
            return False
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(),
                name="verification-delivery",
            )
        try:
            self._queue.put_nowait(request)
            return True
        except asyncio.QueueFull:
            self._emit(request, {
                "event": "verification_delivery_queue_full",
                "event_id": verification_event_id(request),
                "discrepancy": request.discrepancy,
            })
            return False

    async def close(self) -> None:
        self._closed = True
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            request = await self._queue.get()
            try:
                result = await asyncio.to_thread(self._deliver, request)
                self._emit(request, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._emit(request, {
                    "event": "verification_delivery_failed",
                    "event_id": verification_event_id(request),
                    "discrepancy": request.discrepancy,
                    "error_type": type(exc).__name__,
                    "error": _short(exc, 300),
                })
            finally:
                self._queue.task_done()

    @staticmethod
    def _emit(
        request: VerificationDeliveryRequest,
        event: dict[str, Any],
    ) -> None:
        if request.on_result is None:
            return
        try:
            request.on_result(event)
        except Exception:
            pass

    def _connect(self) -> sqlite3.Connection:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.cache_path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        if not self._db_initialized:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS verification_deliveries (
                    event_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (event_id, channel)
                )
                """
            )
            connection.commit()
            self._db_initialized = True
        return connection

    def _reserve(self, event_id: str, channel: str) -> bool:
        now = time.time()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO verification_deliveries (
                    event_id, channel, status, attempts, created_at, updated_at
                ) VALUES (?, ?, 'queued', 0, ?, ?)
                """,
                (event_id, channel, now, now),
            )
            connection.commit()
            if cursor.rowcount == 1:
                return True
            row = connection.execute(
                """
                SELECT status
                  FROM verification_deliveries
                 WHERE event_id = ? AND channel = ?
                """,
                (event_id, channel),
            ).fetchone()
            if row is None or row[0] not in {"queued", "retrying"}:
                return False
            connection.execute(
                """
                UPDATE verification_deliveries
                   SET status = 'queued', updated_at = ?
                 WHERE event_id = ? AND channel = ?
                """,
                (now, event_id, channel),
            )
            connection.commit()
            return True

    def _update(
        self,
        event_id: str,
        channel: str,
        *,
        status: str,
        attempts: int,
        error: str | None = None,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE verification_deliveries
                   SET status = ?, attempts = ?, last_error = ?, updated_at = ?
                 WHERE event_id = ? AND channel = ?
                """,
                (
                    status,
                    attempts,
                    _short(error, 500) if error else None,
                    time.time(),
                    event_id,
                    channel,
                ),
            )
            connection.commit()

    def _deliver(self, request: VerificationDeliveryRequest) -> dict[str, Any]:
        event_id = verification_event_id(request)
        toggles = request.notification_toggles
        missing = request.discrepancy == "missing"
        webhook_outcome: dict[str, Any] = {"status": "not_requested", "attempts": 0}
        telegram_outcome: dict[str, Any] = {"status": "not_requested", "attempts": 0}

        if missing and bool(toggles.get("webhook")):
            webhook_outcome = self._deliver_webhook(request, event_id)

        if bool(toggles.get("telegram")):
            telegram_outcome = self._deliver_telegram(
                request,
                event_id,
                webhook_outcome,
            )

        return {
            "event": "verification_delivery_completed",
            "event_id": event_id,
            "discrepancy": request.discrepancy,
            "candle_timestamp_ms": request.candle_timestamp_ms,
            "authoritative_source": request.authoritative_source,
            "webhook": webhook_outcome,
            "telegram": telegram_outcome,
        }

    def _deliver_webhook(
        self,
        request: VerificationDeliveryRequest,
        event_id: str,
    ) -> dict[str, Any]:
        if not self._reserve(event_id, "webhook"):
            return {"status": "duplicate", "attempts": 0}

        url = str(request.webhook_config.get("url") or "").strip()
        if not url:
            url = default_webhook_url()
        try:
            payload = _strategy_webhook_payload(
                request.order_signal.get("alert_message")
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {_short(exc, 300)}"
            self._update(
                event_id,
                "webhook",
                status="skipped",
                attempts=0,
                error=error,
            )
            return {"status": "skipped", "attempts": 0, "error": error}
        try:
            if not url:
                raise ValueError("webhook URL is empty")
            if not url.startswith(("http://", "https://")):
                raise ValueError("webhook URL must start with http:// or https://")
        except Exception as exc:
            error = f"{type(exc).__name__}: {_short(exc, 300)}"
            self._update(
                event_id,
                "webhook",
                status="failed",
                attempts=0,
                error=error,
            )
            return {"status": "failed", "attempts": 0, "error": error}

        last_error = ""
        attempts = 0
        for attempts in range(1, len(_WEBHOOK_RETRY_DELAYS) + 2):
            retryable = False
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers={"X-PyneReal-Event-ID": event_id},
                    timeout=webhook_request_timeout(request.exchange),
                )
                status_code = int(response.status_code)
                if 200 <= status_code < 300:
                    self._update(
                        event_id,
                        "webhook",
                        status="sent",
                        attempts=attempts,
                    )
                    return {
                        "status": "sent",
                        "attempts": attempts,
                        "http_status": status_code,
                    }
                last_error = f"HTTP {status_code}: {_short(response.text, 300)}"
                retryable = (
                    status_code in {408, 429}
                    or status_code >= 500
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"{type(exc).__name__}: {_short(exc, 300)}"
                retryable = True
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {_short(exc, 300)}"

            self._update(
                event_id,
                "webhook",
                status="retrying" if retryable and attempts <= 3 else "failed",
                attempts=attempts,
                error=last_error,
            )
            if not retryable or attempts > len(_WEBHOOK_RETRY_DELAYS):
                break
            time.sleep(_WEBHOOK_RETRY_DELAYS[attempts - 1])

        return {"status": "failed", "attempts": attempts, "error": last_error}

    def _deliver_telegram(
        self,
        request: VerificationDeliveryRequest,
        event_id: str,
        webhook_outcome: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._reserve(event_id, "telegram"):
            return {"status": "duplicate", "attempts": 0}

        token = str(request.webhook_config.get("telegram_token") or "").strip()
        chat_id = str(request.webhook_config.get("telegram_chat_id") or "").strip()
        token = token or default_telegram_token()
        chat_id = chat_id or default_telegram_chat_id()
        if not token or not chat_id:
            error = "Telegram credentials are unavailable"
            self._update(
                event_id,
                "telegram",
                status="failed",
                attempts=0,
                error=error,
            )
            return {"status": "failed", "attempts": 0, "error": error}

        text = self._telegram_text(request, webhook_outcome)
        try:
            response = post_telegram_message(token, chat_id, text)
            status = int(response.get("status") or 0)
            self._update(
                event_id,
                "telegram",
                status="sent",
                attempts=1,
            )
            return {"status": "sent", "attempts": 1, "http_status": status}
        except Exception as exc:
            error = f"{type(exc).__name__}: {_short(exc, 300)}"
            self._update(
                event_id,
                "telegram",
                status="failed",
                attempts=1,
                error=error,
            )
            return {"status": "failed", "attempts": 1, "error": error}

    @staticmethod
    def _telegram_text(
        request: VerificationDeliveryRequest,
        webhook_outcome: dict[str, Any],
    ) -> str:
        signal = request.order_signal
        duration_ms = timeframe_seconds(request.timeframe) * 1000
        delay_seconds = max(
            0.0,
            (time.time() * 1000 - request.candle_timestamp_ms - duration_ms) / 1000,
        )
        order = (
            f"{str(signal.get('action') or '').upper()} "
            f"order_id={signal.get('order_id') or '-'} "
            f"exit_id={signal.get('exit_id') or '-'} "
            f"occurrence={int(signal.get('occurrence_index') or 0) + 1}"
        )
        details = (
            f"Size: {signal.get('size')} | Fill: {signal.get('fill_price')}\n"
            f"Position: {signal.get('position_size_before')} -> "
            f"{signal.get('position_size_after')}\n"
            f"Signal: {_short(signal.get('alert_message') or '(none)')}\n"
            f"Primary candle: {_format_bar(request.primary_bar)}\n"
            f"Finalized candle: {_format_bar(request.finalized_bar)}\n"
            f"Difference: {_format_difference(request.bar_difference)}"
        )
        if request.discrepancy == "missing":
            webhook_status = webhook_outcome.get("status") or "not_requested"
            webhook_attempts = int(webhook_outcome.get("attempts") or 0)
            heading = "[Verification] Missed strategy order detected"
            conclusion = (
                f"Webhook: {webhook_status} (attempts={webhook_attempts})"
            )
        else:
            heading = "[Verification] Possible false strategy order detected"
            conclusion = (
                "No corrective, cancel, or reverse webhook was sent."
            )
        return (
            f"{heading}\n"
            f"Session: {request.session_id}"
            f" ({request.script_title or 'No title'})\n"
            f"Market: {request.exchange} {request.symbol} {request.timeframe}\n"
            f"Candle: {_format_timestamp(request.candle_timestamp_ms)}\n"
            f"Verification source: {request.authoritative_source}\n"
            f"Detected after: {delay_seconds:.1f}s\n"
            f"Order: {order}\n"
            f"{details}\n"
            f"{conclusion}"
        )
