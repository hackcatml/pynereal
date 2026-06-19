from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from fastapi import WebSocket


# Per-client outbound queue depth. A client this far behind is
# treated as hopelessly slow and dropped — it reconnects and resyncs full state via
# REST (OHLCV / trades / plotchar / plot). Generous enough that a healthy client, and
# especially a local runner, never approaches it; only a stuck remote browser does.
MAX_PENDING = 1000
SEND_TIMEOUT_SECONDS = 10.0
CLOSE_TIMEOUT_SECONDS = 2.0


def _coalesce_key(payload: Any) -> Optional[tuple]:
    """Key under which a queued message may be overwritten by a newer one (conflation).

    Returns None  -> must-deliver: never dropped, order preserved.
    Returns a key -> latest-wins: a newer message with the same key overwrites the one
                     already queued (keeping its position), reducing queue growth.

    'bar' is keyed by bar timestamp, so intra-candle tick updates conflate while each
    distinct candle stays in the stream. 'sessions' is the hub's whole-state snapshot,
    so only the latest matters.
    """
    if not isinstance(payload, dict):
        return None
    t = payload.get("type")
    if t == "bar":
        data = payload.get("data") or {}
        return ("bar", data.get("time"))
    if t == "sessions":
        return ("sessions",)
    return None


class _Channel:
    """One outbound queue + dedicated writer task per WebSocket.

    A single writer per socket guarantees sends never overlap (WebSocket framing is not
    safe under concurrent sends), and the producer only ever enqueues — it never awaits a
    client send, so one slow client cannot stall bar delivery to the runner or to other
    clients. Backpressure is handled per client: replaceable messages conflate, and a
    client that falls hopelessly behind is closed (then resyncs on reconnect)."""

    def __init__(self, ws: WebSocket, manager: "WSManager") -> None:
        self.ws = ws
        self._manager = manager
        # each entry is a 2-element list [coalesce_key_or_None, payload]
        self._items: Deque[list] = deque()
        self._wake = asyncio.Event()
        self._overflow = False
        self._closing = False
        self._closed = False
        self.task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    def enqueue(self, payload: Any) -> None:
        """Synchronous and non-blocking: append (or conflate) and wake the writer.
        Being sync, it is atomic with respect to the writer — no lock needed."""
        if self._closing or self._overflow:
            return
        key = _coalesce_key(payload)
        if key is not None:
            for item in self._items:
                if item[0] == key:
                    item[1] = payload          # conflate: keep latest, hold queue position
                    self._wake.set()
                    return
        if len(self._items) >= MAX_PENDING:
            # Hopelessly behind: stop queuing and let the writer close the socket. The
            # client reconnects and resyncs authoritative state.
            self._overflow = True
            self._items.clear()
            self._wake.set()
            return
        self._items.append([key, payload])
        self._wake.set()

    async def _run(self) -> None:
        try:
            while True:
                if self._closing or self._overflow:
                    break
                if self._items:
                    payload = self._items.popleft()[1]
                    await asyncio.wait_for(self.ws.send_json(payload), SEND_TIMEOUT_SECONDS)
                else:
                    self._wake.clear()
                    await self._wake.wait()
        except asyncio.CancelledError:
            pass
        except Exception:
            # send failure / cancellation -> fall through to teardown
            pass
        finally:
            await self._close_ws()
            await self._manager._on_channel_dead(self)

    async def close(self) -> None:
        self._closing = True
        self._items.clear()
        self._wake.set()
        if self.task is None:
            await self._close_ws()
            return
        if self.task is asyncio.current_task():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.task), CLOSE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self.task), CLOSE_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if self.task.done() and not self._closed:
            await self._close_ws()

    async def _close_ws(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.wait_for(self.ws.close(), CLOSE_TIMEOUT_SECONDS)
        except Exception:
            pass


class WSManager:
    def __init__(self, on_disconnect: Optional[Callable[[WebSocket], Awaitable[None]]] = None) -> None:
        self._channels: Dict[WebSocket, _Channel] = {}
        self._lock = asyncio.Lock()
        self._on_disconnect = on_disconnect

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        ch = _Channel(ws, self)
        async with self._lock:
            self._channels[ws] = ch
        ch.start()

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            ch = self._channels.pop(ws, None)
        if ch is not None:
            await ch.close()
            await self._notify_disconnect(ws)

    async def _on_channel_dead(self, ch: _Channel) -> None:
        # Called from a channel's writer teardown. Drop it from the registry; the writer
        # is already exiting so we must not cancel ourselves.
        should_notify = False
        async with self._lock:
            if self._channels.get(ch.ws) is ch:
                self._channels.pop(ch.ws, None)
                should_notify = True
        if should_notify:
            await self._notify_disconnect(ch.ws)

    async def _notify_disconnect(self, ws: WebSocket) -> None:
        if self._on_disconnect is None:
            return
        try:
            await self._on_disconnect(ws)
        except Exception:
            pass

    async def broadcast_json(self, payload: Any) -> None:
        async with self._lock:
            for ch in self._channels.values():
                ch.enqueue(payload)

    async def send(self, ws: WebSocket, payload: Any) -> None:
        """Queue a message to a single client through its writer (same ordering and
        single-writer guarantee as broadcast_json). Use instead of ws.send_json so direct
        sends can't race a concurrent broadcast on the same socket."""
        async with self._lock:
            ch = self._channels.get(ws)
            if ch is not None:
                ch.enqueue(payload)
