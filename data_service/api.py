from __future__ import annotations

import asyncio
import ast
import json
import threading
import time
from datetime import UTC, date, datetime
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
from asset_portfolio import AssetPortfolioError, AssetPortfolioService
from asset_transfer import AssetTransferError, AssetTransferService
from calendar_store import CalendarEventStore, CalendarStoreError
from data_integrity import DataIntegrityCancelled, inspect_data_integrity
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
from ohlcv_paths import make_cache_path
from update_service import UpdateService, UpdateServiceError

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


def _calendar_session_context(session: Session) -> dict[str, str]:
    return {
        "session_id": session.spec.id,
        "exchange": session.spec.exchange,
        "symbol": session.spec.symbol,
        "timeframe": session.spec.timeframe,
        "script_name": session.spec.script_name,
        "script_title": str(session.chart_info.get("script_title") or ""),
        "strategy": str(session.chart_info.get("script_title") or session.spec.script_name or ""),
    }


def _calendar_forecast_prompt(
    event: dict[str, Any],
    sessions: list[Session],
) -> str:
    context = {
        "event_id": event["id"],
        "date": event["date"],
        "time": event.get("time"),
        "timezone": event.get("timezone"),
        "title": event["title"],
        "details": event.get("details"),
        "category": event.get("category"),
        "source_name": event.get("source_name"),
        "source_url": event.get("source_url"),
        "affected_sessions": [
            _calendar_session_context(session)
            for session in sessions
        ],
    }
    return (
        "This is a read-only forecast request launched from a verified PyneReal calendar event. "
        "Do not add, update, or remove calendar events, do not call calendar mutation tools, and "
        "do not change any account, file, or strategy state. Use current public information and "
        "authoritative sources to assess the most likely outcome, meaningful upside/downside "
        "scenarios, uncertainty, and the potential impact on every affected session below. "
        "Distinguish confirmed facts from inference. Respond in Korean Markdown and include concise "
        "source links. Do not describe your internal procedure.\n\n"
        f"Calendar event and affected session context:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"User request:\n{event['title']} 전망을 분석해줘."
    )


def _manual_calendar_event_prompt(
    *,
    event_date: str,
    user_text: str,
    preferred_sessions: list[Session],
) -> str:
    context = {
        "selected_date": event_date,
        "user_text": user_text,
        "preferred_sessions": [
            _calendar_session_context(session)
            for session in preferred_sessions
        ],
    }
    return (
        "This is an explicit calendar-add request submitted from the selected date in the "
        "PyneReal Hub. Call get_calendar_context first. Research only the event described by "
        "the user and verify that it occurs on the selected date with a public source. Resolve "
        "every affected active session from the calendar context. If preferred_sessions is not "
        "empty, use exactly those sessions; otherwise infer the affected sessions from the user "
        "text and active session metadata. Do not ask for a session ID. If the date cannot be "
        "verified or no affected active session can be resolved, do not mutate the calendar and "
        "explain the issue briefly. To save the verified result, call add_calendar_event exactly "
        "once with the selected date, a concise title, useful verified details, source metadata, "
        "and all affected session IDs. Do not call replace_calendar_events and do not remove or "
        "rewrite any existing event. Report only a tool-confirmed save.\n\n"
        f"Calendar input:\n{json.dumps(context, ensure_ascii=False)}"
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
                    kind = str(options.get("kind") or "line")
                    series_data = []
                    for candle in candles:
                        value = candle.extra_fields.get(title)
                        series_data.append({
                            "time": int(candle.timestamp),
                            "value": (
                                None
                                if value is None or str(value) == ""
                                else int(value) if kind == "bgcolor" else float(value)
                            ),
                        })
                    result.append({
                        "title": title,
                        "kind": kind,
                        "color": options.get("color"),
                        "linewidth": options.get("linewidth"),
                        "style": options.get("style"),
                        "offset": options.get("offset", 0),
                        "editable": options.get("editable", True),
                        "show_last": options.get("show_last"),
                        "force_overlay": options.get("force_overlay", False),
                        "order": options.get("order", 0),
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
            return JSONResponse(
                {"error": "each template requires string title, message, and optional AI instruction"},
                status_code=400,
            )
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
        ai_instruction = payload.get("ai_instruction")
        if ai_instruction is not None and not isinstance(ai_instruction, str):
            return JSONResponse({"error": "ai_instruction must be string"}, status_code=400)

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
        await rt.dispatch_manual_alert_ai_instruction(
            payload,
            result,
            mode="send",
        )
        return JSONResponse(result)

    return r


# ----------------------------------------------------------------------
# Control-plane router:  /api/sessions ...
# ----------------------------------------------------------------------
def build_control_router(
    registry: SessionRegistry,
    codex_service: CodexService,
    calendar_store: CalendarEventStore,
    asset_portfolio_service: AssetPortfolioService,
    asset_transfer_service: AssetTransferService,
    update_service: UpdateService,
) -> APIRouter:
    r = APIRouter()
    calendar_forecast_runs: dict[str, int] = {}
    calendar_forecast_tasks: dict[str, set[asyncio.Task[Any]]] = {}
    data_integrity_jobs: dict[str, dict[str, Any]] = {}

    @r.get("/api/update/status")
    async def update_status() -> JSONResponse:
        try:
            result = await update_service.status()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse(result)

    @r.post("/api/update/check")
    async def update_check() -> JSONResponse:
        try:
            result = await update_service.check()
        except UpdateServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return JSONResponse(result)

    @r.post("/api/update/start")
    async def update_start(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        confirmation_token = payload.get("confirmation_token")
        if not isinstance(confirmation_token, str) or not confirmation_token:
            return JSONResponse({"error": "confirmation_token is required"}, status_code=400)
        try:
            result = await update_service.start(confirmation_token)
        except UpdateServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return JSONResponse(result, status_code=202)

    @r.get("/api/assets")
    async def get_assets(refresh: bool = False) -> JSONResponse:
        try:
            result = await asset_portfolio_service.snapshot(force=refresh)
        except AssetPortfolioError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse(result)

    @r.get("/api/assets/transfer/options")
    async def get_asset_transfer_options(
        exchange: str,
        account: str,
        source: str,
    ) -> JSONResponse:
        try:
            result = await asset_transfer_service.options(exchange, account, source)
        except AssetTransferError as exc:
            return JSONResponse(
                {"error": str(exc), **exc.details},
                status_code=exc.status_code,
            )
        return JSONResponse(result)

    @r.post("/api/assets/transfer")
    async def execute_asset_transfer(
        payload: dict = Body(default_factory=dict),
    ) -> JSONResponse:
        try:
            result = await asset_transfer_service.execute(payload)
        except AssetTransferError as exc:
            if exc.details.get("status") in {"partial", "unknown"}:
                await asset_portfolio_service.invalidate()
            return JSONResponse(
                {"error": str(exc), **exc.details},
                status_code=exc.status_code,
            )
        await asset_portfolio_service.invalidate()
        return JSONResponse(result)

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
            affected_sessions = [
                session
                for session_id in event["session_ids"]
                if (session := registry.get(session_id)) is not None
            ]
            if not affected_sessions:
                continue
            item = dict(event)
            item["forecast_running"] = calendar_forecast_runs.get(event["id"], 0) > 0
            forecast = calendar_store.get_forecast(event["id"])
            if forecast is not None:
                item["forecast"] = {
                    **forecast,
                    "html": _render_ai_markdown(forecast["answer"]),
                }
            item["sessions"] = [
                _calendar_session_context(session)
                for session in affected_sessions
            ]
            item.update(item["sessions"][0])
            result.append(item)
        return JSONResponse({
            "events": result,
            "updated_at": calendar_store.updated_at,
        })

    @r.post("/api/calendar/events/manual")
    async def add_manual_calendar_event(payload: dict = Body(default_factory=dict)):
        event_date = payload.get("date")
        if not isinstance(event_date, str):
            return JSONResponse({"error": "date must use YYYY-MM-DD"}, status_code=400)
        event_date = event_date.strip()
        try:
            parsed_event_date = date.fromisoformat(event_date)
        except ValueError:
            return JSONResponse({"error": "date must use YYYY-MM-DD"}, status_code=400)
        if parsed_event_date.isoformat() != event_date:
            return JSONResponse({"error": "date must use YYYY-MM-DD"}, status_code=400)

        user_text = payload.get("text")
        if not isinstance(user_text, str) or not user_text.strip():
            return JSONResponse({"error": "event text must not be empty"}, status_code=400)
        user_text = user_text.strip()
        if len(user_text) > 200:
            return JSONResponse(
                {"error": "event text is too long (max 200 chars)"},
                status_code=400,
            )

        raw_session_ids = payload.get("session_ids", [])
        if not isinstance(raw_session_ids, list):
            return JSONResponse({"error": "session_ids must be an array"}, status_code=400)
        session_ids: list[str] = []
        for raw_session_id in raw_session_ids:
            if not isinstance(raw_session_id, str) or not raw_session_id.strip():
                return JSONResponse(
                    {"error": "session_ids must contain non-empty strings"},
                    status_code=400,
                )
            session_id = raw_session_id.strip()
            if session_id not in session_ids:
                session_ids.append(session_id)
        if len(session_ids) > 20:
            return JSONResponse(
                {"error": "session_ids can contain at most 20 sessions"},
                status_code=400,
            )
        unknown_session_ids = [
            session_id
            for session_id in session_ids
            if registry.get(session_id) is None
        ]
        if unknown_session_ids:
            return JSONResponse(
                {"error": f"calendar session is not active: {unknown_session_ids[0]}"},
                status_code=409,
            )
        if not registry.sessions:
            return JSONResponse(
                {"error": "add a session before creating a calendar event"},
                status_code=409,
            )
        if not codex_service.enabled and not session_ids:
            return JSONResponse(
                {"error": "select at least one session while AI is disabled"},
                status_code=400,
            )

        preferred_sessions = [
            session
            for session_id in session_ids
            if (session := registry.get(session_id)) is not None
        ]

        async def stream() -> AsyncIterator[str]:
            if not codex_service.enabled:
                try:
                    event = calendar_store.add_event(
                        {
                            "date": event_date,
                            "title": user_text,
                            "category": "other",
                            "source_name": "Manual",
                            "session_ids": session_ids,
                        },
                        active_session_ids=registry.sessions,
                    )
                    await registry.hub_ws.broadcast_json({"type": "calendar_updated"})
                except CalendarStoreError as exc:
                    yield _sse_event("stream_error", {"error": str(exc)})
                    return
                yield _sse_event(
                    "done",
                    {
                        "event_id": event["id"],
                        "ai_used": False,
                        "answer": "Event added",
                    },
                )
                return

            conversation_id: str | None = None
            try:
                preferences = await codex_service.chat_preferences()
                model = preferences.get("model") if isinstance(preferences, dict) else None
                effort = preferences.get("effort") if isinstance(preferences, dict) else None
                prompt = _manual_calendar_event_prompt(
                    event_date=event_date,
                    user_text=user_text,
                    preferred_sessions=preferred_sessions,
                )
                async for stream_event in codex_service.stream_chat(
                    user_text,
                    conversation_id=None,
                    initial_context=prompt,
                    model=model,
                    effort=effort,
                ):
                    event_payload = stream_event.payload
                    if stream_event.event == "conversation":
                        conversation_id = (
                            str(event_payload.get("conversation_id") or "") or None
                        )
                    elif stream_event.event == "done":
                        event_payload = {**event_payload, "ai_used": True}
                    yield _sse_event(stream_event.event, event_payload)
            except Exception as exc:
                print(f"[calendar] manual add failed: {type(exc).__name__}")
                yield _sse_event(
                    "stream_error",
                    {"error": f"Calendar event research failed ({type(exc).__name__})"},
                )
            finally:
                if conversation_id:
                    await codex_service.reset(conversation_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @r.post("/api/calendar/events/{event_id}/forecast")
    async def forecast_calendar_event(event_id: str):
        if not codex_service.enabled:
            return JSONResponse({"error": "AI service is disabled"}, status_code=503)
        event = calendar_store.get_event(
            event_id,
            active_session_ids=registry.sessions,
        )
        if event is None:
            return JSONResponse({"error": "calendar event not found"}, status_code=404)
        affected_sessions = [
            session
            for session_id in event["session_ids"]
            if (session := registry.get(session_id)) is not None
        ]
        if not affected_sessions:
            return JSONResponse({"error": "calendar session is not active"}, status_code=409)

        try:
            preferences = await codex_service.chat_preferences()
        except Exception:
            preferences = {}
        if not isinstance(preferences, dict):
            preferences = {}
        model = preferences.get("model")
        effort = preferences.get("effort")
        message = f"{event['title']} 전망을 분석해줘."
        prompt = _calendar_forecast_prompt(event, affected_sessions)

        async def stream() -> AsyncIterator[str]:
            conversation_id: str | None = None
            stream_task = asyncio.current_task()
            if stream_task is not None:
                calendar_forecast_tasks.setdefault(event_id, set()).add(stream_task)
            calendar_forecast_runs[event_id] = calendar_forecast_runs.get(event_id, 0) + 1
            try:
                # Tell open dashboards immediately; dashboards opened later read
                # the same state from GET /api/calendar/events.
                await registry.hub_ws.broadcast_json({
                    "type": "calendar_forecast_running",
                    "event_id": event_id,
                })
                async for stream_event in codex_service.stream_chat(
                    message,
                    conversation_id=None,
                    initial_context=prompt,
                    model=model,
                    effort=effort,
                ):
                    payload = stream_event.payload
                    if stream_event.event == "conversation":
                        conversation_id = str(payload.get("conversation_id") or "") or None
                    elif stream_event.event == "done":
                        answer = str(payload.get("answer") or "").strip()
                        if not answer:
                            yield _sse_event("stream_error", {"error": "AI returned an empty forecast"})
                            return
                        try:
                            forecast = calendar_store.set_forecast(
                                event_id,
                                answer,
                                active_session_ids=registry.sessions,
                            )
                        except CalendarStoreError as exc:
                            yield _sse_event("stream_error", {"error": str(exc)})
                            return
                        payload = {
                            **payload,
                            "event_id": event_id,
                            "answer": answer,
                            "html": _render_ai_markdown(answer),
                            "updated_at": forecast["updated_at"],
                        }
                    yield _sse_event(stream_event.event, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[calendar] forecast failed event={event_id[:8]}: {type(exc).__name__}")
                yield _sse_event(
                    "stream_error",
                    {"error": f"Calendar forecast failed ({type(exc).__name__})"},
                )
            finally:
                remaining = calendar_forecast_runs.get(event_id, 1) - 1
                if remaining > 0:
                    calendar_forecast_runs[event_id] = remaining
                else:
                    calendar_forecast_runs.pop(event_id, None)
                if stream_task is not None:
                    event_tasks = calendar_forecast_tasks.get(event_id)
                    if event_tasks is not None:
                        event_tasks.discard(stream_task)
                        if not event_tasks:
                            calendar_forecast_tasks.pop(event_id, None)
                try:
                    await registry.hub_ws.broadcast_json({
                        "type": "calendar_forecast_updated",
                        "event_id": event_id,
                    })
                except Exception:
                    pass
                if conversation_id:
                    await codex_service.reset(conversation_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @r.post("/api/calendar/events/{event_id}/forecast/cancel")
    async def cancel_calendar_event_forecast(event_id: str) -> JSONResponse:
        event = calendar_store.get_event(
            event_id,
            active_session_ids=registry.sessions,
        )
        if event is None:
            return JSONResponse({"error": "calendar event not found"}, status_code=404)

        tasks = [
            task
            for task in calendar_forecast_tasks.get(event_id, set())
            if not task.done()
        ]
        if not tasks:
            return JSONResponse({"event_id": event_id, "cancelled": False})

        await registry.hub_ws.broadcast_json({
            "type": "calendar_forecast_cancelled",
            "event_id": event_id,
        })
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return JSONResponse({"event_id": event_id, "cancelled": True})

    @r.post("/api/calendar/events/{event_id}/forecast/viewed")
    async def mark_calendar_forecast_viewed(event_id: str) -> JSONResponse:
        try:
            forecast = calendar_store.mark_forecast_viewed(
                event_id,
                active_session_ids=registry.sessions,
            )
        except CalendarStoreError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        await registry.hub_ws.broadcast_json({
            "type": "calendar_forecast_updated",
            "event_id": event_id,
        })
        return JSONResponse({"event_id": event_id, "viewed_at": forecast["viewed_at"]})

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

    @r.get("/api/sessions/{session_id}/data-integrity")
    async def check_data_integrity(session_id: str) -> JSONResponse:
        session = registry.get(session_id)
        if session is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if not session.feed.history_ready():
            return JSONResponse({"error": "market data is still preparing"}, status_code=409)
        spec = session.feed.spec
        if spec.id in data_integrity_jobs:
            return JSONResponse({"error": "data integrity operation already running"}, status_code=409)
        job = {
            "session_id": session_id,
            "operation": "check",
            "cancel_event": threading.Event(),
            "done_event": asyncio.Event(),
        }
        data_integrity_jobs[spec.id] = job
        try:
            async with session.feed.data_integrity_lock:
                report = await asyncio.to_thread(
                    inspect_data_integrity,
                    cache_path=make_cache_path(),
                    provider=spec.provider,
                    exchange=spec.exchange,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    start_ts=session.feed.history_start_time(),
                    cancel_event=job["cancel_event"],
                )
        except DataIntegrityCancelled:
            return JSONResponse({"error": "data integrity check cancelled", "cancelled": True}, status_code=409)
        except Exception as exc:
            print(f"[data_integrity] check failed feed={spec.id}: {type(exc).__name__}: {exc}")
            return JSONResponse(
                {"error": f"data integrity check failed ({type(exc).__name__})"},
                status_code=502,
            )
        finally:
            job["done_event"].set()
            if data_integrity_jobs.get(spec.id) is job:
                data_integrity_jobs.pop(spec.id, None)
        return JSONResponse(report)

    @r.post("/api/sessions/{session_id}/data-integrity/repair")
    async def repair_data_integrity(session_id: str) -> JSONResponse:
        session = registry.get(session_id)
        if session is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if not session.feed.history_ready():
            return JSONResponse({"error": "market data is still preparing"}, status_code=409)
        spec = session.feed.spec
        start_ts = session.feed.history_start_time()
        if spec.id in data_integrity_jobs:
            return JSONResponse({"error": "data integrity operation already running"}, status_code=409)
        job = {
            "session_id": session_id,
            "operation": "repair",
            "cancel_event": threading.Event(),
            "done_event": asyncio.Event(),
        }
        data_integrity_jobs[spec.id] = job

        def repair() -> dict:
            return inspect_data_integrity(
                cache_path=make_cache_path(),
                provider=spec.provider,
                exchange=spec.exchange,
                symbol=spec.symbol,
                timeframe=spec.timeframe,
                start_ts=start_ts,
                apply_repair=True,
                cancel_event=job["cancel_event"],
            )

        try:
            async with session.feed.data_integrity_lock:
                report = await registry.repair_data_integrity(session_id, repair)
        except DataIntegrityCancelled:
            return JSONResponse({"error": "data integrity repair cancelled", "cancelled": True}, status_code=409)
        except Exception as exc:
            print(f"[data_integrity] repair failed feed={spec.id}: {type(exc).__name__}: {exc}")
            return JSONResponse(
                {"error": f"data integrity repair failed ({type(exc).__name__})"},
                status_code=502,
            )
        finally:
            job["done_event"].set()
            if data_integrity_jobs.get(spec.id) is job:
                data_integrity_jobs.pop(spec.id, None)
        return JSONResponse(report)

    @r.post("/api/sessions/{session_id}/data-integrity/cancel")
    async def cancel_data_integrity(session_id: str) -> JSONResponse:
        session = registry.get(session_id)
        if session is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        job = data_integrity_jobs.get(session.feed.spec.id)
        if job is None:
            return JSONResponse({"ok": True, "cancelled": False})
        job["cancel_event"].set()
        await job["done_event"].wait()
        return JSONResponse({"ok": True, "cancelled": True})

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
