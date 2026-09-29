"""Explicit Telegram buttons reuse dashboard operations, never model tools."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime
from weakref import WeakKeyDictionary


class SessionCommandError(ValueError):
    """A safe, user-facing session command error."""


SESSION_ACTIONS = {
    "webhook_on": ("webhook", True), "webhook_off": ("webhook", False),
    "telegram_on": ("telegram", True), "telegram_off": ("telegram", False),
    "runner_on": ("runner", True), "runner_off": ("runner", False),
}


class TelegramSessionControl:
    def __init__(self, registry) -> None:
        self.registry = registry
        self._identities = WeakKeyDictionary()

    def snapshot(self, session_id: str) -> dict:
        session = self.registry.get(session_id)
        if session is None:
            raise SessionCommandError("The session was removed. Use /sessions again.")
        token = self._identities.get(session)
        if token is None:
            token = secrets.token_urlsafe(12)
            self._identities[session] = token
        spec = session.spec
        state = self.registry.runner_status(session_id)
        return {
            "session_id": session_id, "identity": token,
            "exchange": spec.exchange, "symbol": spec.symbol, "timeframe": spec.timeframe,
            "script_name": spec.script_name, "market_type": spec.market_type,
            "runner_status": state, "runner": state in {"running", "starting"},
            "history_ready": session.feed.history_ready(), "collector": session.feed.collector_status(),
            "calculation": session.calculation_state_payload().get("status", "unknown"),
            "phase": session.runner_phase,
            "webhook": bool(spec.webhook.get("enabled", False)),
            "telegram": bool(spec.webhook.get("telegram_notification", False)),
            "collected_at": datetime.now(UTC).isoformat(),
        }

    def sessions(self) -> list[dict]:
        return [self.snapshot(sid) for sid in self.registry.sessions]

    async def execute(self, target: dict, action: str) -> dict:
        if action not in {"status", *SESSION_ACTIONS}:
            raise SessionCommandError("Unsupported session action.")
        sid = target["session_id"]
        current = self.snapshot(sid)
        for key in ("identity", "exchange", "symbol", "timeframe", "script_name", "market_type"):
            if current[key] != target.get(key):
                raise SessionCommandError("The session changed. Use /sessions to select it again.")
        if action == "status":
            return current
        field, desired = SESSION_ACTIONS[action]
        if current[field] == desired:
            return current
        if current[field] != target.get(field):
            raise SessionCommandError("The setting changed. Use /sessions to refresh before changing it.")
        if field == "runner":
            if desired and not current["script_name"]:
                raise SessionCommandError("Select a script in the dashboard before starting the runner.")
            if desired and not current["history_ready"]:
                raise SessionCommandError("Market data is still preparing. Retry after it is ready.")
            method = self.registry.start_runner if desired else self.registry.stop_runner
            await method(sid)
        else:
            key = "enabled" if field == "webhook" else "telegram_notification"
            await self.registry.update_webhook(sid, **{key: desired})
        return self.snapshot(sid)
