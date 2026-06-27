from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pynecore.cli.app import app_state

# Maximum number of concurrent sessions the hub will manage (decision 8-4).
MAX_SESSIONS = 10


def _slug(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower()


def feed_key(provider: str, exchange: str, symbol: str, timeframe: str) -> str:
    """Data-feed id: one per (provider, exchange, symbol, timeframe), shared by
    all sessions on that market. e.g. ccxt + okx + BTC/USDT:USDT + 1m
    -> ccxt_okx_btc_usdt_usdt_1m."""
    return _slug(f"{provider}_{exchange}_{symbol}_{timeframe}")


def slugify_session_id(exchange: str, symbol: str, timeframe: str, script_name: str = "") -> str:
    """Session id: includes the script path (subdirs kept) so multiple strategies can
    run on the SAME market concurrently and subdir scripts with the same filename
    stay distinct. e.g. okx + BTC/USDT:USDT + 1m + OKX_MU/test.py
    -> okx_btc_usdt_usdt_1m_okx_mu_test."""
    rel = str(Path(script_name).with_suffix("")) if script_name else ""  # keeps subdir: OKX_MU/test
    raw = f"{exchange}_{symbol}_{timeframe}_{rel}" if rel else f"{exchange}_{symbol}_{timeframe}"
    return _slug(raw)


def sanitize_manual_alert_templates(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    templates: list[dict] = []
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        message = item.get("message")
        if not isinstance(title, str) or not isinstance(message, str):
            continue
        title = title.strip()[:100]
        message = message.strip()
        if not title or not message:
            continue
        templates.append({"title": title, "message": message[:5000]})
    return templates


@dataclass(frozen=True)
class SessionSpec:
    """Immutable per-session definition loaded from config / sessions.json."""
    id: str
    provider: str
    exchange: str
    symbol: str
    timeframe: str
    history_since: str
    script_name: str
    webhook: dict  # {"enabled": bool, "telegram_notification": bool}  (decision 8-1)
    autostart_runner: bool = False  # start the runner automatically on hub boot
    manual_alert_templates: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionSpec":
        provider = (d.get("provider") or "").strip()
        exchange = (d.get("exchange") or "").strip()
        # Symbols are canonical-uppercase (ccxt market symbols are uppercase), so a
        # lowercase entry like "btc/usdt:usdt" still resolves.
        symbol = (d.get("symbol") or "").strip().upper()
        timeframe = (d.get("timeframe") or "").strip()
        if not provider or not exchange or not symbol or not timeframe:
            raise ValueError("session requires provider/exchange/symbol/timeframe")
        script_name = (d.get("script_name") or "")
        sid = (d.get("id") or "").strip() or slugify_session_id(exchange, symbol, timeframe, script_name)
        webhook = d.get("webhook") or {}
        return cls(
            id=sid,
            provider=provider,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            history_since=(d.get("history_since") or ""),
            script_name=script_name,
            webhook={
                "enabled": bool(webhook.get("enabled", False)),
                "url": (webhook.get("url") or ""),
                "telegram_notification": bool(webhook.get("telegram_notification", False)),
                "telegram_token": (webhook.get("telegram_token") or ""),
                "telegram_chat_id": (webhook.get("telegram_chat_id") or ""),
            },
            autostart_runner=bool(d.get("autostart_runner", False)),
            manual_alert_templates=sanitize_manual_alert_templates(d.get("manual_alert_templates")),
        )

    def with_webhook(self, *, enabled: bool | None = None,
                     telegram_notification: bool | None = None,
                     url: str | None = None,
                     telegram_token: str | None = None,
                     telegram_chat_id: str | None = None) -> "SessionSpec":
        wh = dict(self.webhook)
        if enabled is not None:
            wh["enabled"] = bool(enabled)
        if telegram_notification is not None:
            wh["telegram_notification"] = bool(telegram_notification)
        if url is not None:
            wh["url"] = str(url)
        if telegram_token is not None:
            wh["telegram_token"] = str(telegram_token)
        if telegram_chat_id is not None:
            wh["telegram_chat_id"] = str(telegram_chat_id)
        return SessionSpec(
            id=self.id, provider=self.provider, exchange=self.exchange,
            symbol=self.symbol, timeframe=self.timeframe,
            history_since=self.history_since, script_name=self.script_name,
            webhook=wh, autostart_runner=self.autostart_runner,
            manual_alert_templates=[dict(t) for t in self.manual_alert_templates],
        )

    def with_manual_alert_templates(self, templates: list[dict]) -> "SessionSpec":
        return SessionSpec(
            id=self.id, provider=self.provider, exchange=self.exchange,
            symbol=self.symbol, timeframe=self.timeframe,
            history_since=self.history_since, script_name=self.script_name,
            webhook=dict(self.webhook), autostart_runner=self.autostart_runner,
            manual_alert_templates=sanitize_manual_alert_templates(templates),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "history_since": self.history_since,
            "script_name": self.script_name,
            "webhook": dict(self.webhook),
            "autostart_runner": self.autostart_runner,
            "manual_alert_templates": [dict(t) for t in self.manual_alert_templates],
        }

    @property
    def feed_id(self) -> str:
        """The shared data-feed this session subscribes to."""
        return feed_key(self.provider, self.exchange, self.symbol, self.timeframe)


@dataclass(frozen=True)
class FeedSpec:
    """Immutable market definition for one shared data feed (data layer)."""
    id: str
    provider: str
    exchange: str
    symbol: str
    timeframe: str
    history_since: str

    @classmethod
    def from_session(cls, s: SessionSpec) -> "FeedSpec":
        return cls(
            id=feed_key(s.provider, s.exchange, s.symbol, s.timeframe),
            provider=s.provider, exchange=s.exchange, symbol=s.symbol,
            timeframe=s.timeframe, history_since=s.history_since,
        )


@dataclass(frozen=True)
class HubConfig:
    host: str
    port: int
    pyne_section: dict


def _toml_path() -> Path:
    return app_state.config_dir / "realtime_trade.toml"


def _read_toml() -> dict:
    with open(_toml_path(), "rb") as f:
        return tomllib.load(f)


def default_webhook_url() -> str:
    try:
        webhook = (_read_toml().get("webhook", {}) or {})
    except Exception:
        return ""
    return str(webhook.get("url") or "").strip()


def _dotenv_value(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value.strip()

    candidates = [
        Path.cwd() / ".env",
        app_state.config_dir.parent.parent / ".env",
    ]
    for path in candidates:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                if key.strip() != name:
                    continue
                return raw_value.strip().strip("\"'")
        except Exception:
            continue
    return ""


def default_telegram_token() -> str:
    return _dotenv_value("BOT_TOKEN")


def default_telegram_chat_id() -> str:
    return _dotenv_value("CHAT_ID")


def load_hub_config() -> HubConfig:
    """Load hub host/port. Prefer [hub]; fall back to legacy
    [realtime].data_service_addr for backward compatibility."""
    cfg = _read_toml()
    pyne = cfg.get("pyne", {})
    if pyne.get("no_logo", False):
        os.environ["PYNE_NO_LOGO"] = "True"
        os.environ["PYNE_QUIET"] = "True"

    hub = cfg.get("hub", {})
    host = hub.get("host")
    port = hub.get("port")

    if host is None or port is None:
        legacy_host, legacy_port = "0.0.0.0", 9001
        addr = (cfg.get("realtime", {}) or {}).get("data_service_addr", "")
        if addr and ":" in addr:
            lh, lp = addr.split(":", 1)
            legacy_host = lh or legacy_host
            try:
                legacy_port = int(lp)
            except ValueError:
                pass
        if host is None:
            host = legacy_host
        if port is None:
            port = legacy_port

    return HubConfig(host=str(host), port=int(port), pyne_section=pyne)


def sessions_json_path() -> Path:
    return app_state.config_dir / "sessions.json"


def load_initial_sessions() -> list[SessionSpec]:
    """Resolve initial sessions. Priority:
    1) sessions.json (runtime CRUD source of truth)
    2) [[session]] arrays in realtime_trade.toml
    3) legacy single [realtime] section
    """
    sj = sessions_json_path()
    if sj.exists():
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
            specs: list[SessionSpec] = []
            for s in data.get("sessions", []):
                try:
                    specs.append(SessionSpec.from_dict(s))
                except Exception as e:
                    print(f"[config] skip invalid session in sessions.json: {e}")
            return _dedupe_by_id(specs)
        except Exception as e:
            print(f"[config] sessions.json load failed, falling back to toml: {e}")

    cfg = _read_toml()

    session_list = cfg.get("session")
    if isinstance(session_list, list) and session_list:
        specs = []
        for s in session_list:
            try:
                specs.append(SessionSpec.from_dict(s))
            except Exception as e:
                print(f"[config] skip invalid [[session]]: {e}")
        return _dedupe_by_id(specs)

    # Legacy single-symbol [realtime] section.
    realtime = cfg.get("realtime", {}) or {}
    if realtime.get("symbol"):
        webhook = cfg.get("webhook", {}) or {}
        try:
            return [SessionSpec.from_dict({
                "provider": realtime.get("provider", ""),
                "exchange": realtime.get("exchange", ""),
                "symbol": realtime.get("symbol", ""),
                "timeframe": realtime.get("timeframe", ""),
                "history_since": realtime.get("history_since", ""),
                "script_name": realtime.get("script_name", ""),
                "webhook": {
                    "enabled": bool(webhook.get("enabled", False)),
                    "telegram_notification": bool(webhook.get("telegram_notification", False)),
                },
            })]
        except Exception as e:
            print(f"[config] legacy [realtime] load failed: {e}")
    return []


def save_sessions(specs: list[SessionSpec]) -> None:
    """Atomically persist sessions to sessions.json (temp -> rename).

    Raises on failure so the caller (and the API) can surface it instead of
    silently dropping the persisted state."""
    sj = sessions_json_path()
    sj.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sessions": [s.to_dict() for s in specs]}
    tmp = sj.with_name(sj.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(sj)


def _dedupe_by_id(specs: list[SessionSpec]) -> list[SessionSpec]:
    seen: set[str] = set()
    out: list[SessionSpec] = []
    for s in specs:
        if s.id in seen:
            print(f"[config] duplicate session id ignored: {s.id}")
            continue
        seen.add(s.id)
        out.append(s)
    return out
