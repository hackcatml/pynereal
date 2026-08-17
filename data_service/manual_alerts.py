from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import requests

try:
    from .config import (
        SessionSpec,
        default_telegram_chat_id,
        default_telegram_token,
        default_webhook_url,
    )
except ImportError:
    from config import (
        SessionSpec,
        default_telegram_chat_id,
        default_telegram_token,
        default_webhook_url,
    )
from pynecore.core.exchange_policy import normalize_exchange_name


DEFAULT_WEBHOOK_REQUEST_TIMEOUT = (5, 10)
HYPERLIQUID_WEBHOOK_REQUEST_TIMEOUT = (5, 30)
TELEGRAM_REQUEST_TIMEOUT = (5, 10)
PLACEHOLDER_RE = re.compile(r"\{\{(price|market|time|symbol|ticker|exchange|timeframe|title)\}\}")


def webhook_request_timeout(exchange: str | None) -> tuple[int, int]:
    if normalize_exchange_name(exchange) == "HYPERLIQUID":
        return HYPERLIQUID_WEBHOOK_REQUEST_TIMEOUT
    return DEFAULT_WEBHOOK_REQUEST_TIMEOUT


def post_json_webhook(url: str, payload: Any, exchange: str | None) -> dict:
    try:
        resp = requests.post(url, json=payload, timeout=webhook_request_timeout(exchange))
        resp.raise_for_status()
        return {"status": int(resp.status_code), "body": resp.text[:4096]}
    except requests.HTTPError as e:
        body = e.response.text[:4096] if e.response is not None else ""
        status = e.response.status_code if e.response is not None else "?"
        raise RuntimeError(f"HTTP {status}: {body}") from e
    except requests.RequestException as e:
        raise RuntimeError(type(e).__name__) from e


def post_telegram_message(token: str, chat_id: str, text: str) -> dict:
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            timeout=TELEGRAM_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return {"status": int(resp.status_code), "body": resp.text[:4096]}
    except requests.HTTPError as e:
        body = e.response.text[:4096] if e.response is not None else ""
        status = e.response.status_code if e.response is not None else "?"
        raise RuntimeError(f"HTTP {status}: {body}") from e
    except requests.RequestException as e:
        raise RuntimeError(type(e).__name__) from e


def webhook_delivery_status(error: BaseException | str) -> str:
    reason = str(error).strip()
    if not reason and isinstance(error, BaseException):
        reason = type(error).__name__
    if len(reason) > 117:
        reason = reason[:117] + "..."
    return f"Failed({reason or 'unknown error'})"


def manual_alert_signal_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    try:
        return json.dumps(message, ensure_ascii=False).replace('"', '')
    except Exception:
        return str(message)


def manual_alert_telegram_text(*, script_title: str | None, timeframe: str,
                               ticker: str, message: Any,
                               webhook_status: str) -> str:
    time_str = datetime.now().strftime("%H:%M:%S")
    return (
        f"🚨 [Manual][{script_title or 'No title'}]\n"
        f"Webhook: {webhook_status}\n"
        f"Time: {time_str}\n"
        f"Timeframe: {timeframe or ''}\n"
        f"Ticker: {ticker or ''}\n"
        f"Signal: {manual_alert_signal_text(message)}"
    )


def send_manual_alert_payload(*, spec: SessionSpec, script_title: str | None,
                              payload: dict) -> dict[str, Any]:
    if "message" not in payload:
        raise ValueError("message is required")

    wh = spec.webhook
    url = (wh.get("url") or "").strip() or default_webhook_url()
    webhook_result: dict[str, Any] = {"sent": False}
    if not url:
        webhook_result["error"] = "webhook url is empty"
    elif not url.startswith(("http://", "https://")):
        webhook_result["error"] = "webhook url must start with http:// or https://"
    else:
        try:
            webhook_result = {
                "sent": True,
                **post_json_webhook(url, payload["message"], spec.exchange),
            }
        except Exception as e:
            webhook_result = {"sent": False, "error": str(e) or type(e).__name__}

    webhook_status = (
        "Sent"
        if webhook_result.get("sent")
        else webhook_delivery_status(str(webhook_result.get("error") or "unknown error"))
    )

    token = (wh.get("telegram_token") or "").strip() or default_telegram_token()
    chat_id = (wh.get("telegram_chat_id") or "").strip() or default_telegram_chat_id()
    telegram_result: dict[str, Any] = {"sent": False}
    if token and chat_id:
        text = manual_alert_telegram_text(
            script_title=script_title,
            timeframe=spec.timeframe,
            ticker=spec.symbol,
            message=payload["message"],
            webhook_status=webhook_status,
        )
        try:
            telegram_result = {
                "sent": True,
                **post_telegram_message(token, chat_id, text),
            }
        except Exception as e:
            telegram_result = {"sent": False, "error": str(e)}

    return {"ok": True, "webhook": webhook_result, "telegram": telegram_result}


def alert_template_replacements(spec: SessionSpec, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "{{price}}": context.get("price"),
        "{{market}}": context.get("market"),
        "{{time}}": context.get("time"),
        "{{symbol}}": spec.symbol or "",
        "{{ticker}}": spec.symbol or "",
        "{{exchange}}": spec.exchange or "",
        "{{timeframe}}": spec.timeframe or "",
        "{{title}}": context.get("title") or "",
    }


def is_inside_json_string(text: str, offset: int) -> bool:
    in_string = False
    escaped = False
    for ch in text[:offset]:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
    return in_string


def render_raw_alert_template_json(text: str, spec: SessionSpec, context: dict[str, Any]) -> str:
    replacements = alert_template_replacements(spec, context)

    def replace(match: re.Match[str]) -> str:
        if is_inside_json_string(text, match.start()):
            return match.group(0)
        return json.dumps(replacements.get(match.group(0), ""), ensure_ascii=False)

    return PLACEHOLDER_RE.sub(replace, text)


def parse_alert_template_message(template_text: str, spec: SessionSpec,
                                 context: dict[str, Any]) -> Any:
    try:
        return json.loads(template_text)
    except Exception as initial_error:
        try:
            return json.loads(render_raw_alert_template_json(template_text, spec, context))
        except Exception:
            raise initial_error


def replace_alert_template_value(value: Any, spec: SessionSpec, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        replacements = alert_template_replacements(spec, context)
        if value in replacements:
            return replacements[value]

        def replace(match: re.Match[str]) -> str:
            replacement = replacements.get(match.group(0))
            return "" if replacement is None else str(replacement)

        return PLACEHOLDER_RE.sub(replace, value)
    if isinstance(value, list):
        return [replace_alert_template_value(item, spec, context) for item in value]
    if isinstance(value, dict):
        return {
            key: replace_alert_template_value(item, spec, context)
            for key, item in value.items()
        }
    return value


def build_manual_alert_message(template: dict, spec: SessionSpec,
                               context: dict[str, Any]) -> Any:
    title = str(template.get("title") or "")
    template_text = str(template.get("message") or "")
    full_context = {**context, "title": title}
    parsed = parse_alert_template_message(template_text, spec, full_context)
    return replace_alert_template_value(parsed, spec, full_context)


def build_manual_alert_ai_instruction(template: dict, spec: SessionSpec,
                                      context: dict[str, Any]) -> str:
    title = str(template.get("title") or "")
    instruction = str(template.get("ai") or "").strip()
    if not instruction:
        return ""
    full_context = {**context, "title": title}
    rendered = replace_alert_template_value(instruction, spec, full_context)
    return str(rendered).strip()


def build_manual_alert_payload(*, template: dict, spec: SessionSpec,
                               price: float, market: float | None,
                               time: int | None) -> dict[str, Any]:
    context = {
        "price": price,
        "market": market,
        "time": time,
        "title": template.get("title") or "",
    }
    payload = {
        "title": template.get("title") or "",
        "price": price,
        "market": market,
        "time": time,
        "message": build_manual_alert_message(template, spec, context),
    }
    ai_instruction = build_manual_alert_ai_instruction(template, spec, context)
    if ai_instruction:
        payload["ai_instruction"] = ai_instruction
    return payload
