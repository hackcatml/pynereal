from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TelegramAIConfig:
    enabled: bool = False
    token: str = field(default="", repr=False)
    chat_id: int = 0
    allowed_user_ids: frozenset[int] = frozenset()
    idle_timeout_seconds: int = 900

    @classmethod
    def load(cls, path: Path, *, token: str, chat_id: str) -> TelegramAIConfig:
        if not path.exists():
            return cls()
        with path.open("rb") as handle:
            section = tomllib.load(handle).get("telegram_ai", {})
        if not isinstance(section, dict) or type(section.get("enabled", False)) is not bool:
            raise ValueError("telegram_ai.enabled must be a boolean")
        if not section.get("enabled", False):
            return cls()
        users = section.get("allowed_user_ids", [])
        if not isinstance(users, list) or not users or any(
            type(value) is not int or value <= 0 for value in users
        ):
            raise ValueError("telegram_ai.allowed_user_ids must contain positive numeric user IDs")
        try:
            destination = int(chat_id)
        except (TypeError, ValueError):
            destination = 0
        if not token.strip() or destination == 0:
            raise ValueError("Telegram AI requires BOT_TOKEN and a nonzero numeric CHAT_ID")
        idle = section.get("idle_timeout_seconds", 900)
        if type(idle) is not int or not 60 <= idle <= 86400:
            raise ValueError("telegram_ai.idle_timeout_seconds must be between 60 and 86400")
        return cls(True, token.strip(), destination, frozenset(users), idle)

    def authorized(self, message: dict) -> bool:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        return (
            isinstance(chat, dict) and isinstance(sender, dict)
            and chat.get("type") in {"private", "group", "supergroup"}
            and type(chat.get("id")) is int and chat["id"] == self.chat_id
            and type(sender.get("id")) is int and sender["id"] in self.allowed_user_ids
            and sender.get("is_bot") is False
            and message.get("sender_chat") is None
        )
