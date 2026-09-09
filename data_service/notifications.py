"""Notification persistence and HTTP API; never used on a strategy calculation thread."""
from __future__ import annotations

import asyncio
import hmac
import json
import queue
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from data_service.notification_events import notification_error, safe_value


class NotificationService:
    def __init__(self, path: Path, broadcast):
        self.path = Path(path)
        self.token = secrets.token_urlsafe(32)
        self.broadcast = broadcast
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notifications-db")
        self._queue = queue.Queue(maxsize=512)
        self._loop = None
        self._wake = asyncio.Event()
        self._worker = None
        self._push_task = None
        self._push_version = 0
        self._closed = False

    def start(self):
        if self._worker is None:
            self._loop = asyncio.get_running_loop()
            self._worker = self._loop.create_task(self._run(), name="notifications-writer")

    def publish(self, event: dict) -> bool:
        if self._closed:
            notification_error("event rejected during shutdown")
            return False
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            notification_error("queue full; event not saved")
            return False
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)
        return True

    async def _run(self):
        while True:
            self._wake.clear()
            while not self._queue.empty():
                event = self._queue.get_nowait()
                try:
                    await self.apply(event)
                except Exception as exc:
                    notification_error(f"save failed: {type(exc).__name__}")
                finally:
                    self._queue.task_done()
            if self._closed:
                return
            await self._wake.wait()

    async def close(self):
        self._closed = True
        self._wake.set()
        if self._worker:
            try:
                await asyncio.wait_for(self._worker, 5)
            except TimeoutError:
                notification_error("shutdown timed out; pending events may not be saved")
        if self._push_task:
            self._push_task.cancel()
            await asyncio.gather(self._push_task, return_exceptions=True)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
                data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                read_revision INTEGER NOT NULL DEFAULT 0, read_at REAL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notification_meta (
                id INTEGER PRIMARY KEY, version INTEGER NOT NULL,
                cleared_through_id INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO notification_meta (id, version) VALUES (1, 0);
        """)
        if "cleared_through_id" not in {row["name"] for row in db.execute("PRAGMA table_info(notification_meta)")}:
            db.execute("ALTER TABLE notification_meta ADD COLUMN cleared_through_id INTEGER NOT NULL DEFAULT 0")
        return db

    @staticmethod
    def _state(db):
        meta = db.execute("SELECT version,cleared_through_id FROM notification_meta WHERE id=1").fetchone()
        return {
            "version": meta["version"],
            "cleared_through_id": meta["cleared_through_id"],
            "latest_id": db.execute("SELECT COALESCE(MAX(id),0) FROM notifications").fetchone()[0],
            "unread": db.execute("SELECT COUNT(*) FROM notifications WHERE revision>read_revision AND id>?",
                                 (meta["cleared_through_id"],)).fetchone()[0],
        }

    @staticmethod
    def _item(row):
        return {**json.loads(row["data"]), "id": row["id"], "revision": row["revision"],
                "unread": row["revision"] > row["read_revision"], "updated_at": row["updated_at"]}

    @staticmethod
    def _validate(event):
        if not isinstance(event, dict) or event.get("kind") not in {"signal", "verification"}:
            raise ValueError("invalid notification kind")
        for field in ("event_key", "session_id", "origin"):
            if not isinstance(event.get(field), str) or not 0 < len(event[field]) <= 256:
                raise ValueError(f"invalid {field}")
        allowed = {"event_key", "kind", "origin", "session_id", "occurred_at", "candle_timestamp_ms",
                   "context", "signal", "finding", "webhook", "telegram"}
        result = safe_value({k: v for k, v in event.items() if k in allowed})
        for channel in ("webhook", "telegram"):
            if channel in result:
                delivery = result[channel]
                if not isinstance(delivery, dict) or delivery.get("status") not in {
                    "sent", "failed", "unknown", "disabled", "not_requested", "configuration_error",
                }:
                    raise ValueError("invalid delivery status")
                result[channel] = {k: v for k, v in delivery.items() if k in {
                    "status", "http_status", "receiver_status", "error_type", "attempts",
                }}
        if result["kind"] == "signal" and "webhook" not in result and "telegram" not in result:
            raise ValueError("signal must have a completed channel result")
        return result

    def _operation(self, operation, **kwargs):
        with closing(self._connect()) as db, db:
            changed = False
            result = {}
            if operation == "apply":
                event = self._validate(kwargs["event"])
                row = db.execute("SELECT * FROM notifications WHERE event_key=?", (event["event_key"],)).fetchone()
                now = time.time()
                if row:
                    old = json.loads(row["data"])
                    # Event identity/context are immutable. Late channel results only patch that channel.
                    merged = {**old, **{k: event[k] for k in ("webhook", "telegram") if k in event}}
                    if merged != old:
                        db.execute("UPDATE notifications SET data=?,revision=revision+1,updated_at=? WHERE id=?",
                                   (json.dumps(merged, ensure_ascii=False), now, row["id"]))
                        changed = True
                else:
                    db.execute("INSERT INTO notifications(event_key,data,created_at,updated_at) VALUES (?,?,?,?)",
                               (event["event_key"], json.dumps(event, ensure_ascii=False), now, now))
                    changed = True
            elif operation == "list":
                rows = db.execute("""SELECT * FROM notifications WHERE id<?
                                     AND id>(SELECT cleared_through_id FROM notification_meta WHERE id=1)
                                     ORDER BY id DESC LIMIT ?""",
                                  (kwargs.get("before") or 9223372036854775807, kwargs["limit"] + 1)).fetchall()
                items = rows[:kwargs["limit"]]
                result = {"items": [self._item(row) for row in items],
                          "next_cursor": items[-1]["id"] if len(rows) > len(items) else None}
            elif operation == "detail":
                row = db.execute("SELECT * FROM notifications WHERE id=?", (kwargs["id"],)).fetchone()
                if row is None:
                    raise KeyError(kwargs["id"])
                result = {"item": self._item(row)}
            elif operation == "read":
                cursor = db.execute("""UPDATE notifications SET read_revision=MIN(revision,?),read_at=?
                                       WHERE id=? AND read_revision<MIN(revision,?)""",
                                    (kwargs["revision"], time.time(), kwargs["id"], kwargs["revision"]))
                changed = cursor.rowcount > 0
            elif operation == "read_all":
                cursor = db.execute("""UPDATE notifications SET read_revision=revision,read_at=? WHERE read_revision<revision
                                       AND id>(SELECT cleared_through_id FROM notification_meta WHERE id=1)""",
                                    (time.time(),))
                changed = cursor.rowcount > 0
            elif operation == "clear":
                db.execute("BEGIN IMMEDIATE")
                state = self._state(db)
                through_id = min(kwargs["through_id"], state["latest_id"])
                # Preserve the records; only advance the list's visibility boundary.
                if through_id > state["cleared_through_id"]:
                    if db.execute("""SELECT 1 FROM notifications WHERE id>? AND id<=?
                                     AND read_revision<revision LIMIT 1""",
                                  (state["cleared_through_id"], through_id)).fetchone():
                        raise ValueError("New results arrived. Mark all as read before clearing the list.")
                    db.execute("UPDATE notification_meta SET cleared_through_id=? WHERE id=1", (through_id,))
                    changed = True
            if changed:
                db.execute("UPDATE notification_meta SET version=version+1 WHERE id=1")
            return {**result, **self._state(db), "changed": changed}

    async def query(self, operation, **kwargs):
        loop = asyncio.get_running_loop()
        from functools import partial
        result = await loop.run_in_executor(self._executor, partial(self._operation, operation, **kwargs))
        if result.pop("changed"):
            self._push_version = result["version"]
            if self._push_task is None or self._push_task.done():
                self._push_task = loop.create_task(self._push())
        return result

    async def apply(self, event):
        return await self.query("apply", event=event)

    async def _push(self):
        while True:
            version = self._push_version
            try:
                await asyncio.wait_for(self.broadcast({"type": "notifications", "version": version}), 2)
            except Exception as exc:
                notification_error(f"broadcast failed: {type(exc).__name__}")
            if version == self._push_version:
                return


class ReadRevision(BaseModel):
    revision: int = Field(ge=1)


class ClearList(BaseModel):
    through_id: int = Field(ge=0)


def build_notification_router(service: NotificationService, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/api/notifications")
    async def listing(limit: int = Query(30, ge=1, le=100), before: int | None = Query(None, ge=1)):
        return await service.query("list", limit=limit, before=before)

    @router.post("/api/notifications/read-all")
    async def read_all():
        return await service.query("read_all")

    @router.post("/api/notifications/clear")
    async def clear(body: ClearList):
        try:
            return await service.query("clear", through_id=body.through_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/api/notifications/{notification_id}")
    async def detail(notification_id: int):
        try:
            return await service.query("detail", id=notification_id)
        except KeyError:
            raise HTTPException(404, "Notification not found") from None

    @router.post("/api/notifications/{notification_id}/read")
    async def read(notification_id: int, body: ReadRevision):
        return await service.query("read", id=notification_id, revision=body.revision)

    @router.post("/internal/notifications")
    async def ingest(request: Request):
        if (not request.client or request.client.host not in {"127.0.0.1", "::1"}
                or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {service.token}")):
            raise HTTPException(403, "Forbidden")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                raise HTTPException(413, "Notification too large")
        try:
            event = json.loads(raw)
            service._validate(event)
            if event["origin"] != "primary" or event["kind"] != "signal" or registry.get(event["session_id"]) is None:
                raise ValueError("invalid producer")
            return await service.apply(event)
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid notification") from None

    return router
