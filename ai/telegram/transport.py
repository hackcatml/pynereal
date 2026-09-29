from __future__ import annotations

import asyncio
import ssl
from urllib.parse import quote
from typing import Any

import aiohttp
import certifi

PENDING_TEXT = "\U0001f916 Think..."


class TelegramError(RuntimeError):
    def __init__(
        self, method: str, code: int = 0, retry_after: int = 0, *, error_type: str = "",
    ) -> None:
        # HTTP exception strings can contain the bot token in the URL.
        detail = f", error={error_type}" if error_type else ""
        super().__init__(f"Telegram {method} failed (code={code}{detail})")
        self.method = method
        self.code = code
        self.retry_after = max(0, retry_after)
        self.error_type = error_type


def message_chunks(text: str) -> list[str]:
    # A 1900-code-point chunk also fits Telegram's limit with astral characters.
    text = text.strip() or "No answer returned."
    chunks = []
    while text:
        end = min(len(text), 1900)
        if end < len(text):
            newline = text.rfind("\n", 0, end)
            if newline > end // 2:
                end = newline
        chunks.append("\U0001f916 " + text[:end])
        text = text[end:].lstrip("\n")
    return chunks


class TelegramTransport:
    def __init__(self, token: str) -> None:
        self._base = f"https://api.telegram.org/bot{token}/"
        self._file_base = f"https://api.telegram.org/file/bot{token}/"
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def _ensure_session(self) -> None:
        async with self._session_lock:
            if self._session is None:
                # Some Python installations have no system CA bundle. Keep TLS verification on.
                context = await asyncio.to_thread(ssl.create_default_context, cafile=certifi.where())
                self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context))

    async def call(self, method: str, **payload: Any) -> Any:
        await self._ensure_session()
        return await self._post(method, {"json": payload})

    async def upload(self, method: str, *, chat_id: int, data: bytes,
                     filename: str, mime: str, caption: str = "") -> Any:
        if method not in {"sendPhoto", "sendDocument"}:
            raise ValueError("Unsupported upload method")
        await self._ensure_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        form.add_field("caption", caption[:900])
        form.add_field("photo" if method == "sendPhoto" else "document", data,
                       filename=filename, content_type=mime)
        return await self._post(method, {"data": form})

    async def download(self, file_id: str, limit: int) -> bytes:
        result = await self.call("getFile", file_id=file_id)
        path = result.get("file_path", "")
        size = result.get("file_size", 0)
        # Only paths returned by getFile on the fixed Telegram origin may be fetched.
        if (not isinstance(path, str) or not path or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or any(char in path for char in "\\:?#")
                or type(size) is not int or size > limit):
            raise ValueError("Invalid or oversized Telegram file")
        try:
            async with self._session.get(
                self._file_base + quote(path, safe="/"), allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise TelegramError("download", response.status)
                if response.content_length is not None and response.content_length > limit:
                    raise ValueError("Attachment exceeds its size limit")
                data = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError("Attachment exceeds its size limit")
                return bytes(data)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TelegramError("download", error_type=type(exc).__name__) from None

    async def _post(self, method: str, body: dict) -> Any:
        timeout = 45 if method == "getUpdates" else 20
        try:
            async with self._session.post(
                self._base + method, **body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                data = await response.json(content_type=None)
                if not isinstance(data, dict):
                    raise TelegramError(method, response.status)
                if response.status >= 400 or not data.get("ok"):
                    retry = (data.get("parameters") or {}).get("retry_after", 0)
                    error_type = ""
                    if method == "editMessageText" and data.get("error_code") == 400:
                        # Keep only known classifications, never the upstream description/URL.
                        description = str(data.get("description", "")).lower()
                        for prefix, kind in (
                            ("bad request: message is not modified", "MessageNotModified"),
                            ("bad request: message to edit not found", "MessageToEditNotFound"),
                            ("bad request: message can't be edited", "MessageCantBeEdited"),
                        ):
                            if description.startswith(prefix):
                                error_type = kind
                                break
                    raise TelegramError(
                        method, int(data.get("error_code") or response.status), int(retry),
                        error_type=error_type,
                    )
                return data.get("result")
        except TelegramError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            raise TelegramError(method, error_type=type(exc).__name__) from None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
