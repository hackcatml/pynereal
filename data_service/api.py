from __future__ import annotations

import asyncio
import ast
import json
import urllib.error
import urllib.request
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from pynecore.cli.app import app_state
from pynecore.core.exchange_policy import tradingview_hides_zero_volume
from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.core.csv_file import CSVReader

import ccxt.pro as ccxtpro

from registry import SessionNotFoundError, SessionExistsError, SessionLimitError, SessionRegistry
from runtime import Session
from config import (
    SessionSpec,
    default_telegram_chat_id,
    default_telegram_token,
    default_webhook_url,
    sanitize_manual_alert_templates,
)
from ohlcv_io import make_ccxt_pro_client

# Cache of exchange -> set(symbols) so symbol validation hits the network at most
# once per exchange for the hub's lifetime.
_markets_cache: dict[str, set] = {}


async def _load_exchange_symbols(exchange: str) -> set:
    cached = _markets_cache.get(exchange)
    if cached is not None:
        return cached
    ex = make_ccxt_pro_client(ccxtpro, exchange)
    try:
        await ex.load_markets()
        symbols = set(ex.symbols or [])
    finally:
        try:
            await ex.close()
        except Exception:
            pass
    _markets_cache[exchange] = symbols
    return symbols


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


def _post_json_webhook(url: str, payload: Any) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
            return {"status": int(resp.status), "body": body}
    except urllib.error.HTTPError as e:
        body = e.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def _post_telegram_message(token: str, chat_id: str, text: str) -> dict:
    import requests

    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            timeout=(5, 10),
        )
        resp.raise_for_status()
        return {"status": int(resp.status_code), "body": resp.text[:4096]}
    except requests.HTTPError as e:
        body = e.response.text[:4096] if e.response is not None else ""
        status = e.response.status_code if e.response is not None else "?"
        raise RuntimeError(f"HTTP {status}: {body}") from e
    except requests.RequestException as e:
        raise RuntimeError(type(e).__name__) from e


def _manual_alert_signal_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    try:
        return json.dumps(message, ensure_ascii=False).replace('"', '')
    except Exception:
        return str(message)


def _manual_alert_telegram_text(*, script_title: str | None, timeframe: str,
                                ticker: str, message: Any) -> str:
    time_str = datetime.now().strftime("%H:%M:%S")
    return (
        f"🚨 [M][{script_title or 'No title'}]\n"
        f"Time: {time_str}\n"
        f"Timeframe: {timeframe or ''}\n"
        f"Ticker: {ticker or ''}\n"
        f"Signal: {_manual_alert_signal_text(message)}"
    )


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

        # Match TradingView: OKX/Binance hide zero-volume bars; BITGET/Hyperliquid keep them.
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

    @r.post("/api/{session_id}/manual-alert")
    async def send_manual_alert(session_id: str, payload: dict = Body(default_factory=dict)) -> JSONResponse:
        rt = _rt(session_id)
        if rt is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if "message" not in payload:
            return JSONResponse({"error": "message is required"}, status_code=400)

        wh = rt.spec.webhook
        url = (wh.get("url") or "").strip() or default_webhook_url()
        if not url:
            return JSONResponse({"error": "webhook url is empty"}, status_code=400)
        if not url.startswith(("http://", "https://")):
            return JSONResponse({"error": "webhook url must start with http:// or https://"}, status_code=400)

        try:
            webhook_result = await asyncio.to_thread(_post_json_webhook, url, payload["message"])
        except Exception as e:
            return JSONResponse({"error": f"webhook send failed: {e}"}, status_code=502)

        token = (wh.get("telegram_token") or "").strip() or default_telegram_token()
        chat_id = (wh.get("telegram_chat_id") or "").strip() or default_telegram_chat_id()
        telegram_result: dict[str, Any] = {"sent": False}
        if token and chat_id:
            script_title, _, _, _ = _load_script_source_info(rt.spec, rt.chart_info)
            text = _manual_alert_telegram_text(
                script_title=script_title,
                timeframe=rt.spec.timeframe,
                ticker=rt.spec.symbol,
                message=payload["message"],
            )
            try:
                telegram_result = {
                    "sent": True,
                    **await asyncio.to_thread(_post_telegram_message, token, chat_id, text),
                }
            except Exception as e:
                telegram_result = {"sent": False, "error": str(e)}
        return JSONResponse({"ok": True, "webhook": webhook_result, "telegram": telegram_result})

    return r


# ----------------------------------------------------------------------
# Control-plane router:  /api/sessions ...
# ----------------------------------------------------------------------
def build_control_router(registry: SessionRegistry) -> APIRouter:
    r = APIRouter()

    @r.get("/api/sessions")
    def list_sessions() -> JSONResponse:
        return JSONResponse({"sessions": registry.snapshots()})

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

    @r.post("/api/sessions/{session_id}/runner/start")
    async def runner_start(session_id: str) -> JSONResponse:
        try:
            await registry.start_runner(session_id)
        except SessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
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
            content = log_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(content.splitlines()[-lines:])
        except Exception as e:
            return JSONResponse({"error": f"failed to read log: {e}"}, status_code=500)
        return JSONResponse({"log": tail})

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
            symbols = await _load_exchange_symbols(exchange)
        except Exception as e:
            # Network/market-load failure: don't claim the symbol is invalid.
            return JSONResponse({"exists": None, "error": f"could not load markets: {e}"})
        return JSONResponse({"exists": symbol in symbols})

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
