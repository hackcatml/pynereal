from __future__ import annotations

import asyncio
import ast
import json
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse
from markdown_it import MarkdownIt

from pynecore.cli.app import app_state
from pynecore.core.exchange_policy import tradingview_hides_zero_volume
from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.core.csv_file import CSVReader

import ccxt.pro as ccxtpro

from ai.provider.codex_service import CodexService
from calendar_store import CalendarEventStore, CalendarStoreError
from registry import (
    HistoryNotReadyError,
    SessionExistsError,
    SessionLimitError,
    SessionNotFoundError,
    SessionOrderError,
    SessionRegistry,
)
from runtime import Session
from config import (
    SessionSpec,
    default_webhook_url,
    sanitize_manual_alert_templates,
    sanitize_manual_alert_triggers,
    validate_history_since,
)
from manual_alerts import send_manual_alert_payload
from ohlcv_io import make_ccxt_pro_client

# Cache of exchange -> ccxt markets so symbol validation hits the network at most
# once per exchange for the hub's lifetime.
_markets_cache: dict[tuple[str, str], dict] = {}
_AI_CHAT_MAX_MESSAGE_CHARS = 4000
_AI_CHAT_MAX_HISTORY_MESSAGES = 12
_AI_MARKDOWN_STREAM_INTERVAL_SECONDS = 0.05
_AI_MARKDOWN = MarkdownIt(
    "commonmark",
    {"html": False, "linkify": False, "typographer": False},
).enable("table")
_AI_STRONG_WORD_BOUNDARY_TOKEN = "AI_MD_STRONG_WORD_BOUNDARY_7F1C"


def _tail_log_text(log_path: Path, lines: int) -> tuple[str, int]:
    data = log_path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    tail = "\n".join(text.splitlines()[-lines:])
    return tail, len(data)


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _normalize_ai_strong_word_boundaries(content: str) -> str:
    normalized: list[str] = []
    strong_open = False
    index = 0
    while index < len(content):
        if content.startswith("**", index):
            normalized.append("**")
            index += 2
            if strong_open:
                if index < len(content) and (content[index].isalnum() or content[index] == "_"):
                    normalized.append(f" {_AI_STRONG_WORD_BOUNDARY_TOKEN}")
                strong_open = False
            else:
                strong_open = True
            continue
        char = content[index]
        normalized.append(char)
        index += 1
        if char == "\n":
            strong_open = False
    return "".join(normalized)


def _render_ai_markdown(content: str) -> str:
    normalized = _normalize_ai_strong_word_boundaries(content)
    return _AI_MARKDOWN.render(normalized).replace(
        f" {_AI_STRONG_WORD_BOUNDARY_TOKEN}",
        "",
    )


def _present_ai_chat_state(state: dict[str, Any]) -> dict[str, Any]:
    presented = dict(state)
    messages: list[dict[str, Any]] = []
    for raw in state.get("messages", []):
        if not isinstance(raw, dict):
            continue
        message = dict(raw)
        content = message.get("content")
        if message.get("role") == "assistant" and not message.get("error") and isinstance(content, str):
            message["html"] = _render_ai_markdown(content)
        messages.append(message)
    presented["messages"] = messages
    return presented


def _sanitize_ai_chat_history(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        items.append({"role": role, "content": content[:_AI_CHAT_MAX_MESSAGE_CHARS]})
    return items[-_AI_CHAT_MAX_HISTORY_MESSAGES:]


def _market_type_from_market(market: dict) -> str:
    if market.get("spot"):
        return "spot"
    if market.get("linear"):
        return "linear"
    if market.get("inverse"):
        return "inverse"
    return ""


def _market_scope_from_symbol(exchange: str, symbol: str) -> str:
    if exchange != "binance":
        return "all"
    if ":" not in symbol:
        return "spot"
    settle = symbol.rsplit(":", 1)[-1]
    if settle in {"USDT", "USDC"}:
        return "linear"
    return "inverse"


async def _load_exchange_markets(
    exchange: str,
    symbol: str,
    *,
    force_refresh: bool = False,
) -> tuple[dict, bool]:
    scope = _market_scope_from_symbol(exchange, symbol)
    cache_key = (exchange, scope)
    if not force_refresh:
        cached = _markets_cache.get(cache_key)
        if cached is not None:
            return cached, True
    ex = make_ccxt_pro_client(
        ccxtpro,
        exchange,
        market_type=scope if scope != "all" else "",
        symbol=symbol,
    )
    try:
        await ex.load_markets()
        markets = dict(ex.markets or {})
    finally:
        try:
            await ex.close()
        except Exception:
            pass
    _markets_cache[cache_key] = markets
    return markets, False


async def _find_exchange_market(exchange: str, symbol: str) -> dict | None:
    markets, from_cache = await _load_exchange_markets(exchange, symbol)
    market = markets.get(symbol)
    if market is None and from_cache:
        markets, _ = await _load_exchange_markets(exchange, symbol, force_refresh=True)
        market = markets.get(symbol)
    return market


# Cache of script path -> (mtime, is_strategy) so we only AST-parse on change.
_strategy_scan_cache: dict[str, tuple] = {}


def _declares_strategy(path: Path) -> bool:
    """True if the file contains a real `script.strategy(...)` call (AST-checked,
    so matches in comments/strings don't count). Indicators/libraries/plain
    helper modules return False."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    cached = _strategy_scan_cache.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]

    result = False
    try:
        source = path.read_text(encoding="utf-8")
        if "script.strategy" in source:  # cheap gate before parsing large files
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "script"
                        and node.func.attr == "strategy"):
                    result = True
                    break
    except Exception:
        result = False

    _strategy_scan_cache[str(path)] = (mtime, result)
    return result


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------
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


def _resolve_script_path(spec: SessionSpec) -> Path:
    script_name = spec.script_name or ""
    if not isinstance(script_name, str) or not script_name:
        raise ValueError("script_name is empty for this session")
    scripts_dir = app_state.scripts_dir.resolve()
    script_path = (scripts_dir / script_name).resolve()
    try:
        script_path.relative_to(scripts_dir)
    except ValueError:
        raise ValueError("script path must be inside scripts directory")
    if script_path.suffix != ".py":
        raise ValueError("script must be a .py file")
    return script_path


def _script_source_display_name(spec: SessionSpec, script_path: Path | None = None) -> str:
    if isinstance(spec.script_name, str) and spec.script_name:
        return Path(spec.script_name).as_posix()
    if script_path is not None:
        return script_path.name
    return ""


def _load_script_source_info(spec: SessionSpec, info: dict) -> tuple[str | None, str, str, bool]:
    title = info.get("script_title") or None
    name = info.get("script_source_name") or _script_source_display_name(spec)
    source = info.get("script_source") or ""
    has_source = bool(source)

    # Disk is the source of truth, so newly registered but not-yet-started sessions
    # can still expose their script metadata and editable source.
    try:
        script_path = _resolve_script_path(spec)
        if script_path.exists():
            source = script_path.read_text(encoding="utf-8")
            name = _script_source_display_name(spec, script_path)
            title = _extract_script_title_from_source(source) or title
            has_source = True
    except Exception:
        pass

    if not title and name:
        title = name
    return title, name, source, has_source


# ----------------------------------------------------------------------
# Per-session data-plane router:  /api/{session_id}/...
# ----------------------------------------------------------------------
def build_session_api_router(registry: SessionRegistry) -> APIRouter:
    r = APIRouter()

    def _rt(session_id: str) -> Optional[Session]:
        return registry.get(session_id)

    @r.get("/api/{session_id}/trades")
    def get_trades(session_id: str) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse([], status_code=404)
        return JSONResponse(rt.trades_history)

    @r.get("/api/{session_id}/plotchar")
    def get_plotchar(session_id: str) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse([], status_code=404)
        return JSONResponse(rt.plotchar_history)

    @r.get("/api/{session_id}/plot")
    def get_plot(session_id: str, limit: int = 2000) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse([], status_code=404)
        plot_options = rt.plot_options
        plot_path = rt.paths.plot_path
        ohlcv_path = rt.ohlcv_path
        if not plot_options:
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
                        if int(end_ts) <= now_ts < int(end_ts) + int(interval):
                            current_open_ts = int(end_ts)
                    ohlcv_reader.close()
            except Exception as e:
                print(f"[{session_id}] Failed to read OHLCV end timestamp: {e}")

        result = []
        try:
            with CSVReader(plot_path) as reader:
                candles = []
                for candle in reader:
                    if current_open_ts is not None and int(candle.timestamp) >= current_open_ts:
                        continue
                    candles.append(candle)
                start_idx = max(0, len(candles) - limit)
                candles = candles[start_idx:]

                for title, options in plot_options.items():
                    series_data = []
                    for candle in candles:
                        value = candle.extra_fields.get(title)
                        series_data.append({
                            "time": int(candle.timestamp),
                            "value": None if (value == "" or value is None) else float(value),
                        })
                    result.append({
                        "title": title,
                        "color": options.get("color"),
                        "linewidth": options.get("linewidth"),
                        "style": options.get("style"),
                        "data": series_data,
                    })
                reader.close()
        except Exception as e:
            print(f"[{session_id}] Failed to read plot CSV: {e}")
            return JSONResponse([])
        return JSONResponse(result)

    @r.get("/api/{session_id}/ohlcv")
    def get_ohlcv(session_id: str, limit: int = 2000) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse([], status_code=404)
        ohlcv_path = rt.ohlcv_path
        if not ohlcv_path.exists():
            return JSONResponse([])

        # Match TradingView: OKX/Binance/Bybit hide zero-volume bars; BITGET/Hyperliquid keep them.
        skip_zero_volume = tradingview_hides_zero_volume(rt.spec.exchange)
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
                out.append({
                    "time": int(c.timestamp),
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": float(c.volume),
                })
            reader.close()
        return JSONResponse(out)

    @r.get("/api/{session_id}/info")
    def get_info(session_id: str) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        info = rt.chart_info
        script_title, script_source_name, _, has_script_source = _load_script_source_info(rt.spec, info)
        return JSONResponse({
            "id": rt.spec.id,
            "exchange": info.get("exchange"),
            "symbol": info.get("symbol"),
            "timeframe": info.get("timeframe"),
            "provider": info.get("provider"),
            "script_title": script_title,
            "script_source_name": script_source_name,
            "has_script_source": has_script_source,
        })

    @r.get("/api/{session_id}/script-source")
    def get_script_source(session_id: str) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        info = rt.chart_info
        title, name, source, _ = _load_script_source_info(rt.spec, info)
        title = title or "No title"
        return JSONResponse({"title": title, "name": name, "source": source})

    @r.post("/api/{session_id}/script-source")
    def save_script_source(session_id: str, payload: dict = Body(default_factory=dict)) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        source = payload.get("source")
        if not isinstance(source, str):
            return JSONResponse({"error": "source must be string"}, status_code=400)
        try:
            script_path = _resolve_script_path(rt.spec)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if not script_path.exists():
            return JSONResponse({"error": f"script not found: {script_path.name}"}, status_code=404)
        try:
            script_path.write_text(source, encoding="utf-8")
        except Exception as e:
            return JSONResponse({"error": f"failed to save script: {e}"}, status_code=500)

        info = rt.chart_info
        name = _script_source_display_name(rt.spec, script_path)
        title = _extract_script_title_from_source(source) or info.get("script_title") or name or "No title"
        info["script_title"] = title
        info["script_source_name"] = name
        info["script_source"] = source
        return JSONResponse({"ok": True, "title": title, "name": name, "source": source})

    @r.get("/api/{session_id}/webhook-config")
    def get_webhook_config(session_id: str) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        wh = rt.spec.webhook
        url = (wh.get("url") or "").strip() or default_webhook_url()
        return JSONResponse({
            "enabled": bool(wh.get("enabled", False)),
            "url": url,
            "telegram_notification": bool(wh.get("telegram_notification", False)),
            "telegram_token": wh.get("telegram_token", "") or "",
            "telegram_chat_id": wh.get("telegram_chat_id", "") or "",
        })

    @r.post("/api/{session_id}/webhook-config")
    async def update_webhook_config(session_id: str, payload: dict = Body(default_factory=dict)) -> JSONResponse:
        enabled = payload.get("enabled")
        telegram_notification = payload.get("telegram_notification")
        url = payload.get("url")
        telegram_token = payload.get("telegram_token")
        telegram_chat_id = payload.get("telegram_chat_id")
        if enabled is not None and not isinstance(enabled, bool):
            return JSONResponse({"error": "enabled must be boolean"}, status_code=400)
        if telegram_notification is not None and not isinstance(telegram_notification, bool):
            return JSONResponse({"error": "telegram_notification must be boolean"}, status_code=400)
        for fname, fval in (("url", url), ("telegram_token", telegram_token),
                            ("telegram_chat_id", telegram_chat_id)):
            if fval is not None and not isinstance(fval, str):
                return JSONResponse({"error": f"{fname} must be string"}, status_code=400)
        try:
            updated = await registry.update_webhook(
                session_id, enabled=enabled, telegram_notification=telegram_notification,
                url=url, telegram_token=telegram_token, telegram_chat_id=telegram_chat_id)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": f"failed to update webhook: {e}"}, status_code=500)
        return JSONResponse(updated)

    @r.get("/api/{session_id}/manual-alert-templates")
    def get_manual_alert_templates(session_id: str) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return JSONResponse({
            "templates": [dict(t) for t in rt.spec.manual_alert_templates],
        })

    @r.post("/api/{session_id}/manual-alert-templates")
    async def update_manual_alert_templates(session_id: str, payload: dict = Body(default_factory=dict)) -> JSONResponse:
        templates = payload.get("templates")
        if not isinstance(templates, list):
            return JSONResponse({"error": "templates must be array"}, status_code=400)
        if len(templates) > 50:
            return JSONResponse({"error": "templates can contain at most 50 items"}, status_code=400)
        sanitized = sanitize_manual_alert_templates(templates)
        if len(sanitized) != len(templates):
            return JSONResponse({"error": "each template requires string title and message"}, status_code=400)
        try:
            updated = await registry.update_manual_alert_templates(session_id, sanitized)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": f"failed to update templates: {e}"}, status_code=500)
        return JSONResponse({"templates": updated})

    @r.get("/api/{session_id}/manual-alert-trigger")
    def get_manual_alert_trigger(session_id: str) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        triggers = [dict(t) for t in rt.spec.manual_alert_triggers]
        return JSONResponse({"triggers": triggers})

    @r.post("/api/{session_id}/manual-alert-trigger")
    async def update_manual_alert_trigger(session_id: str, payload: dict = Body(default_factory=dict)) -> JSONResponse:
        raw_triggers = payload.get("triggers")
        if not isinstance(raw_triggers, list):
            return JSONResponse({"error": "triggers must be array"}, status_code=400)
        triggers = sanitize_manual_alert_triggers(raw_triggers)
        if len(triggers) != len(raw_triggers):
            return JSONResponse({"error": "each trigger requires valid price and template"}, status_code=400)

        try:
            updated = await registry.update_manual_alert_triggers(session_id, triggers)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": f"failed to update trigger: {e}"}, status_code=500)
        return JSONResponse({"triggers": updated})

    @r.post("/api/{session_id}/manual-alert")
    async def send_manual_alert(session_id: str, payload: dict = Body(default_factory=dict)) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if "message" not in payload:
            return JSONResponse({"error": "message is required"}, status_code=400)

        try:
            script_title, _, _, _ = _load_script_source_info(rt.spec, rt.chart_info)
            result = await asyncio.to_thread(
                send_manual_alert_payload,
                spec=rt.spec,
                script_title=script_title,
                payload=payload,
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": f"webhook send failed: {e}"}, status_code=502)
        return JSONResponse(result)

    return r


# ----------------------------------------------------------------------
# Control-plane router:  /api/sessions ...
# ----------------------------------------------------------------------
def build_control_router(
    registry: SessionRegistry,
    codex_service: CodexService,
    calendar_store: CalendarEventStore,
) -> APIRouter:
    r = APIRouter()

    @r.get("/api/ai/chat")
    async def get_ai_chat() -> JSONResponse:
        return JSONResponse(_present_ai_chat_state(await codex_service.chat_state()))

    @r.get("/api/ai/models")
    async def get_ai_models() -> JSONResponse:
        try:
            models = await codex_service.model_options()
            prefs = await codex_service.chat_preferences()
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=503)
        return JSONResponse({
            "models": models,
            "selected_model": prefs["model"],
            "selected_effort": prefs["effort"],
        })

    @r.put("/api/ai/chat/preferences")
    async def set_ai_chat_preferences(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            return JSONResponse({"error": "model must be a string"}, status_code=400)
        effort = payload.get("effort")
        if effort is not None and not isinstance(effort, str):
            return JSONResponse({"error": "effort must be a string"}, status_code=400)
        try:
            prefs = await codex_service.set_chat_preferences(model, effort)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=503)
        # other connected browsers switch to the same selection
        await registry.hub_ws.broadcast_json({
            "type": "ai_prefs_updated",
            "model": prefs["model"],
            "effort": prefs["effort"],
        })
        return JSONResponse(prefs)

    @r.put("/api/ai/chat/state")
    async def import_ai_chat_state(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        conversation_id = payload.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            return JSONResponse({"error": "conversation_id must be a string"}, status_code=400)
        state = await codex_service.import_chat_state(
            payload.get("messages"),
            (conversation_id or "").strip() or None,
        )
        await registry.hub_ws.broadcast_json({"type": "ai_chat_updated"})
        return JSONResponse(_present_ai_chat_state(state))

    @r.post("/api/ai/chat")
    async def ai_chat(payload: dict = Body(default_factory=dict)):
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return JSONResponse({"error": "message must be a non-empty string"}, status_code=400)
        message = message.strip()
        if len(message) > _AI_CHAT_MAX_MESSAGE_CHARS:
            return JSONResponse(
                {"error": f"message is too long (max {_AI_CHAT_MAX_MESSAGE_CHARS} chars)"},
                status_code=400,
            )
        conversation_id = payload.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            return JSONResponse({"error": "conversation_id must be a string"}, status_code=400)
        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            return JSONResponse({"error": "model must be a string"}, status_code=400)
        effort = payload.get("effort")
        if effort is not None and not isinstance(effort, str):
            return JSONResponse({"error": "effort must be a string"}, status_code=400)
        try:
            model = await codex_service.validate_model(model)
            effort = await codex_service.validate_effort(effort, model)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if model is None or effort is None:
            # fall back to the shared dashboard selection (or its defaults)
            try:
                prefs = await codex_service.chat_preferences()
            except Exception:
                prefs = None
            if prefs:
                if model is None:
                    model = prefs["model"]
                if effort is None and model == prefs["model"]:
                    effort = prefs["effort"]
        history = _sanitize_ai_chat_history(payload.get("history"))
        async def notify_chat_updated() -> None:
            await registry.hub_ws.broadcast_json({"type": "ai_chat_updated"})

        run = codex_service.start_shared_chat(
            message,
            client_conversation_id=(conversation_id or "").strip() or None,
            client_history=history,
            on_state_changed=notify_chat_updated,
            model=model,
            effort=effort,
        )

        async def stream() -> AsyncIterator[str]:
            streamed_answer = ""
            pending_delta = ""
            last_delta_emit = 0.0
            try:
                async for event in run.events():
                    if event.event == "delta":
                        delta = str(event.payload.get("text") or "")
                        streamed_answer += delta
                        pending_delta += delta
                        now = time.monotonic()
                        if last_delta_emit and now - last_delta_emit < _AI_MARKDOWN_STREAM_INTERVAL_SECONDS:
                            continue
                        yield _sse_event(
                            "delta",
                            {"text": pending_delta, "html": _render_ai_markdown(streamed_answer)},
                        )
                        pending_delta = ""
                        last_delta_emit = now
                        continue
                    if event.event == "done" and pending_delta:
                        yield _sse_event(
                            "delta",
                            {"text": pending_delta, "html": _render_ai_markdown(streamed_answer)},
                        )
                        pending_delta = ""
                    event_payload = event.payload
                    if event.event == "done":
                        answer = str(event.payload.get("answer") or "")
                        event_payload = {**event.payload, "html": _render_ai_markdown(answer)}
                    yield _sse_event(event.event, event_payload)
            except asyncio.CancelledError:
                print(
                    f"[ai] stream={run.id} client disconnected; "
                    "background turn continues"
                )
                raise
            except Exception as e:
                await registry.hub_ws.broadcast_json({"type": "ai_chat_updated"})
                yield _sse_event("stream_error", {"error": str(e)})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @r.post("/api/ai/chat/reset")
    async def reset_ai_chat(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        conversation_id = payload.get("conversation_id")
        await codex_service.clear_shared_chat(
            conversation_id.strip() if isinstance(conversation_id, str) else None
        )
        await registry.hub_ws.broadcast_json({"type": "ai_chat_updated"})
        return JSONResponse({"ok": True})

    @r.get("/api/calendar/events")
    async def list_calendar_events(
        start: str | None = None,
        end: str | None = None,
    ) -> JSONResponse:
        try:
            events = calendar_store.list_events(
                active_session_ids=registry.sessions,
                start=start,
                end=end,
            )
        except CalendarStoreError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        result = []
        for event in events:
            session = registry.get(event["session_id"])
            if session is None:
                continue
            item = dict(event)
            item.update({
                "exchange": session.spec.exchange,
                "symbol": session.spec.symbol,
                "timeframe": session.spec.timeframe,
                "script_name": session.spec.script_name,
                "script_title": str(session.chart_info.get("script_title") or ""),
            })
            result.append(item)
        return JSONResponse({
            "events": result,
            "updated_at": calendar_store.updated_at,
        })

    @r.get("/api/sessions")
    def list_sessions() -> JSONResponse:
        return JSONResponse({
            "sessions": registry.snapshots(),
            "ai_enabled": codex_service.enabled,
        })

    @r.put("/api/sessions/order")
    async def reorder_sessions(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        session_ids = payload.get("session_ids")
        if not isinstance(session_ids, list) or not all(isinstance(item, str) for item in session_ids):
            return JSONResponse({"error": "session_ids must be an array of strings"}, status_code=400)
        try:
            ordered = await registry.reorder_sessions(session_ids)
        except SessionOrderError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except Exception as e:
            return JSONResponse({"error": f"failed to reorder sessions: {e}"}, status_code=500)
        return JSONResponse({"sessions": ordered})

    @r.post("/api/sessions")
    async def create_session(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        try:
            spec = SessionSpec.from_dict(payload)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        try:
            await registry.add_session(spec)
        except SessionExistsError:
            return JSONResponse({"error": f"session already exists: {spec.id}"}, status_code=409)
        except SessionLimitError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except Exception as e:
            return JSONResponse({"error": f"failed to add session: {e}"}, status_code=500)
        return JSONResponse({"ok": True, "id": spec.id})

    @r.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, cleanup_output: bool = False) -> JSONResponse:
        try:
            await registry.remove_session(session_id, cleanup_output=cleanup_output)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": f"failed to remove session: {e}"}, status_code=500)
        return JSONResponse({"ok": True})

    @r.patch("/api/sessions/{session_id}/history-since")
    async def update_history_since(session_id: str, payload: dict = Body(default_factory=dict)) -> JSONResponse:
        try:
            value = validate_history_since(payload.get("history_since"))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        try:
            await registry.update_history_since(session_id, value)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": f"failed to update history_since: {e}"}, status_code=500)
        return JSONResponse({"ok": True, "history_since": value})

    @r.post("/api/sessions/{session_id}/runner/start")
    async def runner_start(session_id: str) -> JSONResponse:
        try:
            await registry.start_runner(session_id)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except HistoryNotReadyError:
            return JSONResponse({"error": "market data is still preparing"}, status_code=409)
        return JSONResponse({"ok": True})

    @r.post("/api/sessions/{session_id}/runner/stop")
    async def runner_stop(session_id: str) -> JSONResponse:
        try:
            await registry.stop_runner(session_id)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return JSONResponse({"ok": True})

    @r.post("/api/sessions/{session_id}/runner/restart")
    async def runner_restart(session_id: str) -> JSONResponse:
        try:
            await registry.restart_runner(session_id)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except HistoryNotReadyError:
            return JSONResponse({"error": "market data is still preparing"}, status_code=409)
        return JSONResponse({"ok": True})

    @r.get("/api/sessions/{session_id}/runner/logs")
    def runner_logs(session_id: str, lines: int = 200) -> JSONResponse:
        rt = registry.get(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        log_path = rt.paths.log_path
        if not log_path.exists():
            return JSONResponse({"log": ""})
        try:
            tail, _ = _tail_log_text(log_path, lines)
        except Exception as e:
            return JSONResponse({"error": f"failed to read log: {e}"}, status_code=500)
        return JSONResponse({"log": tail})

    @r.get("/api/sessions/{session_id}/runner/logs/stream", response_model=None)
    async def runner_logs_stream(
        request: Request,
        session_id: str,
        lines: int = 500,
    ):
        rt = registry.get(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        log_path = rt.paths.log_path
        lines = max(1, min(int(lines or 500), 5000))

        async def events() -> AsyncIterator[str]:
            offset = 0
            try:
                if log_path.exists():
                    tail, offset = await asyncio.to_thread(_tail_log_text, log_path, lines)
                else:
                    tail = ""
                yield _sse_event("snapshot", {"log": tail})
            except Exception as e:
                yield _sse_event("stream_error", {"error": f"failed to read log: {e}"})

            heartbeat_after = 0
            while not await request.is_disconnected():
                try:
                    if not log_path.exists():
                        if offset != 0:
                            offset = 0
                            yield _sse_event("snapshot", {"log": ""})
                    else:
                        size = log_path.stat().st_size
                        if size < offset:
                            tail, offset = await asyncio.to_thread(_tail_log_text, log_path, lines)
                            yield _sse_event("snapshot", {"log": tail})
                        elif size > offset:
                            def read_chunk() -> tuple[str, int]:
                                with log_path.open("rb") as fh:
                                    fh.seek(offset)
                                    chunk = fh.read(size - offset)
                                return chunk.decode("utf-8", errors="replace"), size

                            chunk_text, offset = await asyncio.to_thread(read_chunk)
                            if chunk_text:
                                yield _sse_event("append", {"chunk": chunk_text})
                except Exception as e:
                    yield _sse_event("stream_error", {"error": f"failed to stream log: {e}"})

                heartbeat_after += 1
                if heartbeat_after >= 30:
                    heartbeat_after = 0
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @r.delete("/api/sessions/{session_id}/runner/logs")
    def clear_runner_logs(session_id: str) -> JSONResponse:
        rt = registry.get(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        log_path = rt.paths.log_path
        try:
            if log_path.exists():
                # Truncate; a running runner uses append mode and keeps logging from 0.
                log_path.write_text("", encoding="utf-8")
        except Exception as e:
            return JSONResponse({"error": f"failed to clear log: {e}"}, status_code=500)
        return JSONResponse({"ok": True})

    return r


# ----------------------------------------------------------------------
# Validation router: /api/validate/...  (add-form field checks)
# ----------------------------------------------------------------------
def build_validation_router() -> APIRouter:
    r = APIRouter()

    @r.get("/api/validate/exchange")
    async def validate_exchange(provider: str = "ccxt", exchange: str = "") -> JSONResponse:
        exchange = (exchange or "").strip().lower()
        if not exchange:
            return JSONResponse({"exists": False, "error": "exchange is empty"})
        if provider != "ccxt":
            # Only ccxt is validated here; other providers pass through.
            return JSONResponse({"exists": True, "skipped": True})
        return JSONResponse({"exists": exchange in ccxtpro.exchanges})

    @r.get("/api/validate/symbol")
    async def validate_symbol(provider: str = "ccxt", exchange: str = "", symbol: str = "") -> JSONResponse:
        exchange = (exchange or "").strip().lower()
        symbol = (symbol or "").strip().upper()  # ccxt market symbols are uppercase
        if not exchange or not symbol:
            return JSONResponse({"exists": False, "error": "exchange and symbol required"})
        if provider != "ccxt":
            return JSONResponse({"exists": True, "skipped": True})
        if exchange not in ccxtpro.exchanges:
            return JSONResponse({"exists": False, "error": f"unknown exchange: {exchange}"})
        try:
            market = await _find_exchange_market(exchange, symbol)
        except Exception as e:
            # Network/market-load failure: don't claim the symbol is invalid.
            return JSONResponse({"exists": None, "error": f"could not load markets: {e}"})
        if not market:
            return JSONResponse({"exists": False})
        return JSONResponse({"exists": True, "market_type": _market_type_from_market(market)})

    @r.get("/api/scripts")
    def list_scripts() -> JSONResponse:
        """List strategy scripts under workdir/scripts/ recursively (subdirs kept as
        relative paths, e.g. OKX_MU/test.py): .py files that declare a
        script.strategy(...). Indicators/libraries/helpers and lib/__pycache__/hidden
        dirs are excluded."""
        scripts_dir = app_state.scripts_dir
        items: List[str] = []
        if scripts_dir.exists():
            for p in scripts_dir.rglob("*.py"):
                rel = p.relative_to(scripts_dir)
                if any(part.startswith(".") or part in ("__pycache__", "lib") for part in rel.parts):
                    continue
                if _declares_strategy(p):
                    items.append(rel.as_posix())  # forward slashes for the UI/value
        items.sort()
        return JSONResponse({"scripts": items})

    return r
