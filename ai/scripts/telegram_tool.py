from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

_TOOL_NAME = "send_telegram_message"
_MAX_MESSAGE_CHARS = 12_000
_TELEGRAM_CHUNK_CHARS = 3_900


class TelegramToolError(ValueError):
    """A Telegram delivery rejected before any network request was made."""


def _load_default_token() -> str:
    from data_service.config import default_telegram_token

    return default_telegram_token()


def _load_default_chat_id() -> str:
    from data_service.config import default_telegram_chat_id

    return default_telegram_chat_id()


def _send_default_message(token: str, chat_id: str, text: str) -> dict[str, Any]:
    from data_service.manual_alerts import post_telegram_message

    return post_telegram_message(token, chat_id, text)


class TelegramMessageTool:
    """Send AI results to the server-configured Telegram destination."""

    def __init__(
        self,
        *,
        token_loader: Callable[[], str] = _load_default_token,
        chat_id_loader: Callable[[], str] = _load_default_chat_id,
        sender: Callable[[str, str, str], dict[str, Any]] = _send_default_message,
    ) -> None:
        self._token_loader = token_loader
        self._chat_id_loader = chat_id_loader
        self._sender = sender

    @property
    def name(self) -> str:
        return _TOOL_NAME

    @property
    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": _TOOL_NAME,
            "description": (
                "Send a completed result to the Telegram chat configured by the server's "
                "BOT_TOKEN and CHAT_ID. Use only when the user explicitly asks to send the "
                "result to Telegram. Pass concise plain text; do not include credentials."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _MAX_MESSAGE_CHARS,
                        "description": "Complete plain-text message to deliver.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        }

    def handle_server_request(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method != "item/tool/call" or not isinstance(params, dict):
            return {}
        if params.get("tool") != _TOOL_NAME:
            return {}

        try:
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                raise TelegramToolError("Tool arguments must be an object")
            result = self.send(arguments)
        except TelegramToolError as exc:
            return self._tool_response(False, {"error": str(exc)})
        except Exception as exc:
            print(f"[ai] Telegram send failed: {type(exc).__name__}")
            return self._tool_response(
                False,
                {"error": f"Telegram send failed ({type(exc).__name__})"},
            )

        print(
            f"[ai] Telegram sent messages={result['messages_sent']} "
            f"characters={result['characters']}"
        )
        return self._tool_response(True, result)

    def send(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unexpected = sorted(set(arguments) - {"message"})
        if unexpected:
            raise TelegramToolError(f"Unexpected argument field(s): {', '.join(unexpected)}")

        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip():
            raise TelegramToolError("message must be a non-empty string")
        message = message.strip()
        if len(message) > _MAX_MESSAGE_CHARS:
            raise TelegramToolError(
                f"message exceeds the {_MAX_MESSAGE_CHARS}-character delivery limit"
            )

        token = self._token_loader().strip()
        chat_id = self._chat_id_loader().strip()
        if not token or not chat_id:
            raise TelegramToolError("BOT_TOKEN and CHAT_ID are not configured")

        chunks = self._split_message(message)
        sent = 0
        try:
            for chunk in chunks:
                self._sender(token, chat_id, chunk)
                sent += 1
        except Exception as exc:
            print(
                f"[ai] Telegram partial failure sent={sent}/{len(chunks)} "
                f"error={type(exc).__name__}"
            )
            raise RuntimeError(
                f"Telegram delivery stopped after {sent}/{len(chunks)} message(s)"
            ) from exc

        return {
            "sent": True,
            "messages_sent": sent,
            "characters": len(message),
        }

    @staticmethod
    def _split_message(message: str) -> list[str]:
        chunks: list[str] = []
        remaining = message
        while len(remaining) > _TELEGRAM_CHUNK_CHARS:
            split_at = remaining.rfind("\n", 0, _TELEGRAM_CHUNK_CHARS + 1)
            if split_at <= 0:
                split_at = _TELEGRAM_CHUNK_CHARS
            else:
                split_at += 1
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        if remaining:
            chunks.append(remaining)
        return chunks

    @staticmethod
    def _tool_response(success: bool, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": success,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ],
        }
