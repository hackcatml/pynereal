from __future__ import annotations

import os
import json
import secrets
import sqlite3
import time
from pathlib import Path

from .transport import PENDING_TEXT, message_chunks
from .attachments import attachment_descriptor


COMMAND_HELP = (
    "Command:\n"
    "/model selects model and effort\n"
    "/new starts a new chat\n"
    "/end ends the conversation\n"
    "/cancel cancels work"
)
AI_MODE_NOTICE = "AI mode on. Send text, images or scripts.\nChanges require your approval.\n\n" + COMMAND_HELP


class TelegramStore:
    """All methods run on the service's dedicated single-thread executor."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.db: sqlite3.Connection | None = None
        self.bot_id = 0
        self._lock_file = None

    def open(self, bot_id: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self.path.with_suffix(".lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._lock_file.write(b"0")
                self._lock_file.flush()
                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError("Another Telegram AI receiver owns this database") from None
        self.bot_id = bot_id
        try:
            self.db = sqlite3.connect(self.path, timeout=5)
            os.chmod(self.path, 0o600)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS offsets (bot INTEGER PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS received (
                    bot INTEGER NOT NULL, id INTEGER NOT NULL, PRIMARY KEY (bot, id));
                CREATE TABLE IF NOT EXISTS chats (
                    bot INTEGER, chat INTEGER, actor INTEGER, expires REAL NOT NULL,
                    PRIMARY KEY (bot, chat, actor));
                CREATE TABLE IF NOT EXISTS jobs (
                    bot INTEGER, id INTEGER, chat INTEGER, actor INTEGER,
                    prompt TEXT NOT NULL, state TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (bot, id));
                CREATE INDEX IF NOT EXISTS telegram_job_state ON jobs(bot, state, id);
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY, bot INTEGER NOT NULL, chat INTEGER NOT NULL,
                    text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
                    message_id INTEGER);
                CREATE INDEX IF NOT EXISTS telegram_outbox_state ON outbox(bot, state, id);
                CREATE TABLE IF NOT EXISTS proposals (
                    nonce TEXT PRIMARY KEY, bot INTEGER, chat INTEGER, actor INTEGER, job INTEGER,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, guard TEXT NOT NULL,
                    state TEXT NOT NULL, expires REAL NOT NULL, outbox_id INTEGER,
                    result TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS telegram_proposal_state ON proposals(bot,state);
                CREATE TABLE IF NOT EXISTS attachments (
                    bot INTEGER, job INTEGER, chat INTEGER, actor INTEGER, created REAL,
                    name TEXT, kind TEXT, mime TEXT, data BLOB, PRIMARY KEY(bot,job));
                CREATE TABLE IF NOT EXISTS model_menus (
                    nonce TEXT PRIMARY KEY, bot INTEGER, chat INTEGER, actor INTEGER,
                    catalog TEXT NOT NULL, selected INTEGER, stage TEXT NOT NULL,
                    expires REAL NOT NULL, outbox_id INTEGER NOT NULL);
            """)
            with self.db:
                chat_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(chats)")}
                for name in ("model", "effort"):
                    if name not in chat_columns:
                        self.db.execute(f"ALTER TABLE chats ADD COLUMN {name} TEXT")
                if "history_after" not in chat_columns:
                    self.db.execute("ALTER TABLE chats ADD COLUMN history_after INTEGER NOT NULL DEFAULT 0")
                self.db.execute("DELETE FROM model_menus WHERE bot=?", (bot_id,))
                columns = {row["name"] for row in self.db.execute("PRAGMA table_info(outbox)")}
                for name in ("job_id", "replace_id"):
                    if name not in columns:
                        self.db.execute(f"ALTER TABLE outbox ADD COLUMN {name} INTEGER")
                for name, definition in {
                    "kind": "TEXT NOT NULL DEFAULT 'text'", "data": "BLOB", "filename": "TEXT",
                    "mime": "TEXT", "markup": "TEXT", "request_id": "INTEGER", "requires_id": "INTEGER",
                }.items():
                    if name not in columns:
                        self.db.execute(f"ALTER TABLE outbox ADD COLUMN {name} {definition}")
                job_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)")}
                if "input" not in job_columns:
                    self.db.execute("ALTER TABLE jobs ADD COLUMN input TEXT NOT NULL DEFAULT '{}'")
                self.db.execute("CREATE INDEX IF NOT EXISTS telegram_outbox_job ON outbox(bot, job_id)")
                self.db.execute("UPDATE proposals SET state='expired',payload='{}',guard='{}' "
                                "WHERE bot=? AND state IN ('draft','pending','queued')", (bot_id,))
                uncertain = self.db.execute("SELECT chat,outbox_id FROM proposals WHERE bot=? AND state='executing'", (bot_id,)).fetchall()
                self.db.execute("UPDATE proposals SET state='unknown' WHERE bot=? AND state='executing'", (bot_id,))
                for row in uncertain:
                    self._reply(row["chat"], "Server restarted during an approved change. Its outcome is unknown. "
                                "Check the current file/settings before requesting it again. It was not replayed.",
                                replace_id=row["outbox_id"])
                self.db.execute("UPDATE outbox SET state='failed',data=NULL WHERE bot=? AND state='held'", (bot_id,))
                self.db.execute("UPDATE outbox SET state='failed' WHERE bot=? AND state='pending' AND markup IS NOT NULL", (bot_id,))
                self._prune(time.time())
                interrupted = self.db.execute(
                    "SELECT id,chat FROM jobs WHERE bot=? AND state IN ('queued','running')",
                    (bot_id,),
                ).fetchall()
                self.db.execute(
                    "UPDATE jobs SET state='interrupted' WHERE bot=? AND state IN ('queued','running')",
                    (bot_id,),
                )
                self.db.execute("UPDATE chats SET expires=0 WHERE bot=?", (bot_id,))
                for row in interrupted:
                    self._complete_reply(row, "Server restarted. The unfinished request was not replayed. Use /ai to request it again.")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def offset(self) -> int:
        row = self.db.execute("SELECT value FROM offsets WHERE bot=?", (self.bot_id,)).fetchone()
        return row[0] if row else 0

    def _reply(self, chat: int, text: str, *, job_id: int | None = None, replace_id: int | None = None) -> None:
        chunks = [PENDING_TEXT] if job_id is not None else message_chunks(text)
        self.db.executemany(
            "INSERT INTO outbox(bot,chat,text,job_id,replace_id) VALUES (?,?,?,?,?)",
            [(self.bot_id, chat, chunk, job_id, replace_id if index == 0 else None)
             for index, chunk in enumerate(chunks)],
        )

    def _complete_reply(self, job, text: str) -> None:
        placeholder = self.db.execute(
            "SELECT id FROM outbox WHERE bot=? AND job_id=? ORDER BY id LIMIT 1",
            (self.bot_id, job["id"]),
        ).fetchone()
        self._reply(job["chat"], text, replace_id=placeholder["id"] if placeholder else None)

    def preferences(self, chat: int, actor: int) -> dict:
        row = self.db.execute("SELECT model,effort FROM chats WHERE bot=? AND chat=? AND actor=?",
                              (self.bot_id, chat, actor)).fetchone()
        return dict(row) if row else {"model": None, "effort": None}

    def _menu_message(self, chat: int, text: str, buttons: list, *, replace_id=None) -> int:
        return self.db.execute("INSERT INTO outbox(bot,chat,text,markup,replace_id) VALUES (?,?,?,?,?)",
                               (self.bot_id, chat, "\U0001f916 " + text,
                                json.dumps({"inline_keyboard": buttons}), replace_id)).lastrowid

    def _model_menu(self, chat: int, actor: int, catalog: dict, now: float) -> None:
        current = self.preferences(chat, actor)
        model = current["model"] or catalog["model"]
        effort = current["effort"] or catalog["effort"]
        self.db.execute("DELETE FROM model_menus WHERE bot=? AND chat=? AND actor=?", (self.bot_id, chat, actor))
        nonce = secrets.token_urlsafe(18)
        buttons = [[{"text": item["label"] + (" (current)" if item["value"] == model else ""),
                     "callback_data": f"tm:m:{nonce}:{index}"}]
                   for index, item in enumerate(catalog["options"])]
        buttons.append([{"text": "Cancel", "callback_data": f"tm:c:{nonce}:0"}])
        item_id = self._menu_message(chat, f"Model: {model}\nEffort: {effort}\n\nSelect a model, then effort.", buttons)
        self.db.execute("INSERT INTO model_menus(nonce,bot,chat,actor,catalog,stage,expires,outbox_id) "
                        "VALUES (?,?,?,?,?,'model',?,?)",
                        (nonce, self.bot_id, chat, actor, json.dumps(catalog), now + 600, item_id))

    def _model_callback(self, query: dict, parts: list[str], now: float) -> str:
        row = self.db.execute("SELECT m.*,o.message_id FROM model_menus m JOIN outbox o ON o.id=m.outbox_id "
                              "WHERE m.bot=? AND m.nonce=?", (self.bot_id, parts[2])).fetchone()
        message = query.get("message") or {}
        if row is None or row["expires"] <= now:
            return "This selection expired. Use /model again."
        if (row["chat"] != message.get("chat", {}).get("id") or row["actor"] != query.get("from", {}).get("id")
                or row["message_id"] is None or row["message_id"] != message.get("message_id")):
            return "Only the requester can use the current selection buttons."
        if parts[1] == "c":
            self.db.execute("DELETE FROM model_menus WHERE nonce=?", (row["nonce"],))
            self._reply(row["chat"], "Selection cancelled. Settings unchanged.", replace_id=row["outbox_id"])
            return "Cancelled."
        if not parts[3].isascii() or not parts[3].isdecimal():
            return "Invalid selection. Use /model again."
        index = int(parts[3])
        catalog = json.loads(row["catalog"])
        if parts[1] == "m" and row["stage"] == "model" and index < len(catalog["options"]):
            selected = catalog["options"][index]
            choices = [{"text": effort, "callback_data": f"tm:e:{row['nonce']}:{i}"}
                       for i, effort in enumerate(selected["efforts"])]
            buttons = [choices[i:i + 2] for i in range(0, len(choices), 2)]
            buttons.append([{"text": "Cancel", "callback_data": f"tm:c:{row['nonce']}:0"}])
            item_id = self._menu_message(row["chat"], f"Model: {selected['value']}\n\nSelect reasoning effort.",
                                         buttons, replace_id=row["outbox_id"])
            self.db.execute("UPDATE model_menus SET stage='effort',selected=?,outbox_id=? WHERE nonce=?",
                            (index, item_id, row["nonce"]))
            return "Select effort to save."
        if parts[1] == "e" and row["stage"] == "effort":
            selected = catalog["options"][row["selected"]]
            if index < len(selected["efforts"]):
                model, effort = selected["value"], selected["efforts"][index]
                self.db.execute("INSERT INTO chats(bot,chat,actor,expires,model,effort) VALUES (?,?,?,0,?,?) "
                                "ON CONFLICT(bot,chat,actor) DO UPDATE SET model=excluded.model,effort=excluded.effort",
                                (self.bot_id, row["chat"], row["actor"], model, effort))
                self.db.execute("DELETE FROM model_menus WHERE nonce=?", (row["nonce"],))
                self._reply(row["chat"], f"Model: {model}\nEffort: {effort}\n\nSaved for your next requests in this chat.",
                            replace_id=row["outbox_id"])
                return "Saved. Applies to your next requests."
        return "Invalid or already handled selection. Use /model again."

    def accept(
        self, update_id: int, message: dict | None, *, now: float,
        idle: int, username: str, ai_enabled: bool, model_catalog: dict | None = None,
    ) -> bool:
        """Commit authorization-filtered input and offset together; return cancel flag."""
        with self.db:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO received(bot,id) VALUES (?,?)", (self.bot_id, update_id),
            ).rowcount
            self.db.execute(
                "INSERT INTO offsets(bot,value) VALUES (?,?) ON CONFLICT(bot) DO UPDATE SET value=MAX(value,excluded.value)",
                (self.bot_id, update_id + 1),
            )
            if not inserted or message is None:
                return False
            chat, actor = message["chat"]["id"], message["from"]["id"]
            self._prune(now)
            text = str(message.get("text") or message.get("caption") or "").strip()
            parts = text.split(maxsplit=1)
            head = parts[0] if parts else ""
            argument = parts[1] if len(parts) > 1 else ""
            command, _, target = head.partition("@")
            if head.startswith("/") and target and target.lower() != username.lower():
                return False
            if command == "/model":
                if not ai_enabled:
                    self._reply(chat, "AI is disabled or unavailable on this server.")
                elif argument:
                    self._reply(chat, "Use /model without arguments, then select the model and effort buttons.")
                elif model_catalog is None:
                    self._reply(chat, "Model options are unavailable. Please retry /model.")
                else:
                    self._model_menu(chat, actor, model_catalog, now)
                return False
            if command == "/new" and (not ai_enabled or argument):
                self._reply(chat, "AI is disabled or unavailable on this server." if not ai_enabled else
                            "Use /new without text, then send your request.")
                return False
            if command in ("/end", "/cancel", "/new"):
                self.db.execute("DELETE FROM model_menus WHERE bot=? AND chat=? AND actor=?", (self.bot_id, chat, actor))
                cancelled = self.db.execute(
                    "SELECT id,chat FROM jobs WHERE bot=? AND chat=? AND actor=? AND state IN ('queued','running')",
                    (self.bot_id, chat, actor),
                ).fetchall()
                self.db.execute(
                    "UPDATE jobs SET state='cancelled' WHERE bot=? AND chat=? AND actor=? AND state IN ('queued','running')",
                    (self.bot_id, chat, actor),
                )
                for job in cancelled:
                    self._complete_reply(job, "Request cancelled.")
                self.db.execute("UPDATE proposals SET state='cancelled',payload='{}',guard='{}' "
                                "WHERE bot=? AND chat=? AND actor=? AND state IN ('draft','pending','queued')",
                                (self.bot_id, chat, actor))
                self.db.execute("UPDATE outbox SET state='failed',data=NULL WHERE bot=? AND state='held' "
                                "AND request_id IN (SELECT id FROM jobs WHERE bot=? AND chat=? AND actor=?)",
                                (self.bot_id, self.bot_id, chat, actor))
                if command == "/end":
                    self.db.execute("UPDATE chats SET expires=0 WHERE bot=? AND chat=? AND actor=?", (self.bot_id, chat, actor))
                elif command == "/new":
                    self.db.execute("INSERT INTO chats(bot,chat,actor,expires,history_after) VALUES (?,?,?,?,?) "
                                    "ON CONFLICT(bot,chat,actor) DO UPDATE SET expires=excluded.expires,history_after=excluded.history_after",
                                    (self.bot_id, chat, actor, now + idle, update_id))
                notice = {"/end": "AI mode ended.", "/cancel": "Requests cancelled.",
                          "/new": "New chat started. AI mode on. Model and effort unchanged."}[command]
                self._reply(chat, notice
                            + " Approved actions already started may finish; completed actions are not undone.")
                return True
            if text.startswith("/") and command != "/ai":
                if command in ("/start", "/help"):
                    notice = "Use /ai to start. Send text, images or scripts.\nChanges require your approval.\n\n" + COMMAND_HELP
                    if message["chat"].get("type") in {"group", "supergroup"}:
                        notice += (f"\n\nIn groups, use /ai@{username} <request> for reliable delivery. "
                                   "Plain messages require Privacy Mode disabled or the bot to be an admin. "
                                   "Replies are visible to everyone in this group.")
                    self._reply(chat, notice)
                return False
            mode = self.db.execute(
                "SELECT expires FROM chats WHERE bot=? AND chat=? AND actor=?", (self.bot_id, chat, actor),
            ).fetchone()
            if command != "/ai" and (not mode or mode[0] <= now):
                if mode and mode[0] > 0:
                    self.db.execute("UPDATE chats SET expires=0 WHERE bot=? AND chat=? AND actor=?", (self.bot_id, chat, actor))
                    self._reply(chat, "AI mode expired. Use /ai to start again.")
                return False
            if not ai_enabled:
                self._reply(chat, "AI is disabled or unavailable on this server.")
                return False
            self.db.execute(
                "INSERT INTO chats(bot,chat,actor,expires) VALUES (?,?,?,?) ON CONFLICT(bot,chat,actor) DO UPDATE SET expires=excluded.expires",
                (self.bot_id, chat, actor, now + idle),
            )
            if command == "/ai":
                text = argument.strip()
                if not text and not (message.get("photo") or message.get("document")):
                    self._reply(chat, AI_MODE_NOTICE)
                    return False
            try:
                attachment = attachment_descriptor(message)
            except ValueError as exc:
                self._reply(chat, str(exc))
                return False
            if not text:
                if attachment is None:
                    self._reply(chat, "Send text, a supported image, or a script attachment.")
                    return False
                text = "Please inspect the attached file. Do not change anything without an explicit request."
            if len(text) > 12000:
                self._reply(chat, "Request is too long (maximum 12000 characters).")
                return False
            pending = self.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE bot=? AND state IN ('queued','running')", (self.bot_id,),
            ).fetchone()[0]
            if pending >= 10:
                self._reply(chat, "Request queue is full. Retry after an answer or use /cancel.")
                return False
            reply = message.get("reply_to_message") or {}
            metadata = {"attachment": attachment, "settings": self.preferences(chat, actor)}
            if type(reply.get("message_id")) is int:
                # Only retain the ID. The quoted Telegram body is never session evidence.
                metadata["reply_id"] = reply["message_id"]
            self.db.execute(
                "INSERT INTO jobs(bot,id,chat,actor,prompt,state,input) VALUES (?,?,?,?,?,'queued',?)",
                (self.bot_id, update_id, chat, actor, text, json.dumps(metadata)),
            )
            self._reply(chat, "", job_id=update_id)
            return False

    def claim(self) -> dict | None:
        with self.db:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE bot=? AND state='queued' ORDER BY id LIMIT 1", (self.bot_id,),
            ).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE jobs SET state='running' WHERE bot=? AND id=?", (self.bot_id, row["id"]))
            return dict(row)

    def _history_after(self, chat: int, actor: int) -> int:
        row = self.db.execute("SELECT history_after FROM chats WHERE bot=? AND chat=? AND actor=?",
                              (self.bot_id, chat, actor)).fetchone()
        return row[0] if row else 0

    def history(self, chat: int, actor: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT prompt,answer FROM jobs WHERE bot=? AND chat=? AND actor=? AND state='done' AND id>? ORDER BY id DESC LIMIT 10",
            (self.bot_id, chat, actor, self._history_after(chat, actor)),
        ).fetchall()
        result = []
        for row in reversed(rows):
            result.extend([
                {"role": "user", "content": row["prompt"]},
                {"role": "assistant", "content": row["answer"]},
            ])
        return result

    def finish(self, job: dict, answer: str, state: str = "done") -> None:
        with self.db:
            changed = self.db.execute(
                "UPDATE jobs SET state=?,answer=? WHERE bot=? AND id=? AND state='running'",
                (state, answer, self.bot_id, job["id"]),
            ).rowcount
            if changed:
                self._complete_reply(job, answer)
                if state == "done":
                    self.db.execute("UPDATE proposals SET state='pending',expires=? WHERE bot=? AND job=? AND state='draft'",
                                    (time.time() + 600, self.bot_id, job["id"]))
                    self.db.execute("UPDATE outbox SET state='pending' WHERE bot=? AND request_id=? AND state='held'",
                                    (self.bot_id, job["id"]))
                else:
                    self.db.execute("UPDATE proposals SET state='cancelled',payload='{}',guard='{}' WHERE bot=? AND job=? AND state='draft'",
                                    (self.bot_id, job["id"]))
                    self.db.execute("UPDATE outbox SET state='failed',data=NULL WHERE bot=? AND request_id=? AND state='held'",
                                    (self.bot_id, job["id"]))

    def next_message(self) -> dict | None:
        row = self.db.execute(
            "SELECT o.*, p.message_id AS target_message_id, r.state AS required_state, "
            "a.state AS proposal_state,a.expires AS proposal_expires, "
            "m.stage AS menu_stage,m.expires AS menu_expires,j.state AS request_state FROM outbox o "
            "LEFT JOIN outbox p ON p.id=o.replace_id AND p.bot=o.bot "
            "LEFT JOIN outbox r ON r.id=o.requires_id AND r.bot=o.bot "
            "LEFT JOIN proposals a ON a.outbox_id=o.id AND a.bot=o.bot "
            "LEFT JOIN model_menus m ON m.outbox_id=o.id AND m.bot=o.bot "
            "LEFT JOIN jobs j ON j.id=o.request_id AND j.bot=o.bot "
            "WHERE o.bot=? AND o.state='pending' ORDER BY o.id LIMIT 1", (self.bot_id,),
        ).fetchone()
        return dict(row) if row else None

    def replace_unavailable(self, item_id: int) -> None:
        with self.db:
            self.db.execute(
                "UPDATE outbox SET replace_id=NULL WHERE bot=? AND id=?", (self.bot_id, item_id),
            )

    def delivered(self, item_id: int, message_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE outbox SET state='sent',message_id=?,data=NULL WHERE bot=? AND id=?", (message_id, self.bot_id, item_id))

    def delivery_failed(self, item_id: int, due: float, permanent: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE outbox SET attempts=attempts+1,due=?,state=? WHERE bot=? AND id=?",
                (due, "failed" if permanent else "pending", self.bot_id, item_id),
            )
            if permanent:
                self.db.execute("UPDATE outbox SET data=NULL WHERE bot=? AND id=?", (self.bot_id, item_id))

    def _prune(self, now: float) -> None:
        self.db.execute("DELETE FROM model_menus WHERE expires<?", (now,))
        self.db.execute("DELETE FROM attachments WHERE created<?", (now - 86400,))
        self.db.execute("UPDATE proposals SET state='expired',payload='{}',guard='{}' "
                        "WHERE bot=? AND expires<? AND state IN ('pending','queued')", (self.bot_id, now))
        self.db.execute("UPDATE proposals SET payload='{}',guard='{}' WHERE bot=? AND expires<? "
                        "AND state NOT IN ('executing','queued')", (self.bot_id, now - 86400))

    def _running(self, job: dict) -> None:
        row = self.db.execute("SELECT state FROM jobs WHERE bot=? AND id=? AND chat=? AND actor=?",
                              (self.bot_id, job["id"], job["chat"], job["actor"])).fetchone()
        if row is None or row[0] != "running":
            raise ValueError("The request is no longer running")

    def save_attachment(self, job: dict, item: dict) -> None:
        with self.db:
            self._running(job)
            self.db.execute("INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?,?,?,?)",
                            (self.bot_id, job["id"], job["chat"], job["actor"], time.time(),
                             item["name"], item["kind"], item["mime"], item["data"]))
            self.db.execute("DELETE FROM attachments WHERE bot=? AND chat=? AND actor=? AND job NOT IN "
                            "(SELECT job FROM attachments WHERE bot=? AND chat=? AND actor=? ORDER BY job DESC LIMIT 10)",
                            (self.bot_id, job["chat"], job["actor"], self.bot_id, job["chat"], job["actor"]))

    def attachments(self, job: dict, attachment_id: int | None = None):
        rows = self.db.execute("SELECT * FROM attachments WHERE bot=? AND chat=? AND actor=? AND created>? "
                               "AND job<=? AND job>? ORDER BY job DESC LIMIT 10",
                               (self.bot_id, job["chat"], job["actor"], time.time() - 86400, job["id"],
                                self._history_after(job["chat"], job["actor"]))).fetchall()
        if attachment_id is not None:
            for row in rows:
                if row["job"] == attachment_id:
                    return dict(row)
            raise ValueError("Attachment not found in this user's recent requests")
        return [{"id": row["job"], "name": row["name"], "kind": row["kind"]} for row in rows]

    def queue_media(self, job: dict, *, kind: str, data: bytes, filename: str, mime: str, caption: str,
                    immediate: bool = False) -> int:
        with self.db:
            if immediate and kind != "photo":
                raise ValueError("Only read-only chart photos may be sent before the answer")
            item_id = self._insert_media(job, kind=kind, data=data, filename=filename, mime=mime, caption=caption)
            if immediate:
                self.db.execute("UPDATE outbox SET state='pending' WHERE id=?", (item_id,))
            return item_id

    def _insert_media(self, job: dict, *, kind: str, data: bytes, filename: str, mime: str, caption: str) -> int:
        self._running(job)
        count = self.db.execute("SELECT COUNT(*) FROM outbox WHERE bot=? AND request_id=? AND kind!='text'",
                                (self.bot_id, job["id"])).fetchone()[0]
        if count >= 8 or len(data) > 5 * 1024 * 1024 or kind not in {"photo", "document"}:
            raise ValueError("Outgoing attachment limit exceeded")
        return self.db.execute("INSERT INTO outbox(bot,chat,text,state,request_id,kind,data,filename,mime) "
                               "VALUES (?,?,?,'held',?,?,?,?,?)",
                               (self.bot_id, job["chat"], caption[:900], job["id"], kind, data, filename, mime)).lastrowid

    def propose(self, job: dict, kind: str, payload: dict, guard: dict, preview: str, review: bytes | None = None) -> dict:
        with self.db:
            self._running(job)
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            existing = self.db.execute("SELECT nonce FROM proposals WHERE bot=? AND job=? AND kind=? AND payload=? AND state='draft'",
                                       (self.bot_id, job["id"], kind, encoded)).fetchone()
            if existing:
                return {"proposal_id": existing[0], "state": "awaiting_approval", "applied": False}
            count = self.db.execute("SELECT COUNT(*) FROM proposals WHERE bot=? AND job=?", (self.bot_id, job["id"])).fetchone()[0]
            if count >= 4 or len(encoded.encode()) > 1024 * 1024:
                raise ValueError("Proposal limit exceeded")
            nonce = secrets.token_urlsafe(18)
            review_id = None
            if review:
                review_id = self._insert_media(job, kind="document", data=review,
                    filename="changes.html" if kind == "script" else "changes.txt",
                    mime="text/html" if kind == "script" else "text/plain",
                    caption="Review the complete changes before approving.")
            if len(preview) > 1700:
                preview = preview.splitlines()[0][:200] + "\nReview the attached complete changes before approving."
            text = "\U0001f916 " + preview + "\n\nNot applied. Only the requester can approve within 10 minutes."
            markup = {"inline_keyboard": [[
                {"text": "Save" if kind == "script" else "Apply", "callback_data": f"ta:y:{nonce}"},
                {"text": "Cancel", "callback_data": f"ta:n:{nonce}"},
            ]]}
            item_id = self.db.execute("INSERT INTO outbox(bot,chat,text,state,markup,request_id,requires_id) "
                                     "VALUES (?,?,?,'held',?,?,?)",
                                     (self.bot_id, job["chat"], text, json.dumps(markup), job["id"], review_id)).lastrowid
            self.db.execute("INSERT INTO proposals(nonce,bot,chat,actor,job,kind,payload,guard,state,expires,outbox_id) "
                            "VALUES (?,?,?,?,?,?,?,?,'draft',?,?)",
                            (nonce, self.bot_id, job["chat"], job["actor"], job["id"], kind, encoded,
                             json.dumps(guard, ensure_ascii=False), time.time() + 600, item_id))
            return {"proposal_id": nonce, "state": "awaiting_approval", "applied": False}

    def reply_context(self, job: dict, message_id: int) -> dict | None:
        row = self.db.execute("SELECT j.prompt,j.answer FROM outbox o LEFT JOIN outbox p ON p.id=o.replace_id "
                              "JOIN jobs j ON j.bot=o.bot AND j.id=COALESCE(o.job_id,o.request_id,p.job_id) "
                              "WHERE o.bot=? AND o.chat=? AND o.message_id=? AND j.actor=? AND j.state='done' AND j.id>? "
                              "ORDER BY o.id DESC LIMIT 1",
                              (self.bot_id, job["chat"], message_id, job["actor"],
                               self._history_after(job["chat"], job["actor"]))).fetchone()
        return {"question": row["prompt"], "answer": row["answer"][:12000]} if row else None

    def accept_callback(self, update_id: int, query: dict | None, now: float) -> str:
        with self.db:
            inserted = self.db.execute("INSERT OR IGNORE INTO received(bot,id) VALUES (?,?)", (self.bot_id, update_id)).rowcount
            self.db.execute("INSERT INTO offsets VALUES (?,?) ON CONFLICT(bot) DO UPDATE SET value=MAX(value,excluded.value)",
                            (self.bot_id, update_id + 1))
            if not inserted or query is None:
                return "This approval is unavailable."
            self._prune(now)
            parts = str(query.get("data") or "").split(":")
            if len(parts) == 4 and parts[0] == "tm":
                return self._model_callback(query, parts, now)
            if len(parts) != 3 or parts[0] != "ta" or parts[1] not in {"y", "n"}:
                return "Unknown action."
            message = query.get("message") or {}
            row = self.db.execute("SELECT p.*,o.message_id FROM proposals p JOIN outbox o ON o.id=p.outbox_id "
                                  "WHERE p.bot=? AND p.nonce=?", (self.bot_id, parts[2])).fetchone()
            if (row is None or row["chat"] != message.get("chat", {}).get("id")
                    or row["actor"] != query.get("from", {}).get("id")
                    or row["message_id"] is None or row["message_id"] != message.get("message_id")):
                return "Only the original requester can use this approval."
            if row["state"] != "pending":
                if row["state"] in {"executing", "unknown"}:
                    return "This change started already. Check its result and the current settings before requesting it again."
                return "Already handled, cancelled or expired. Request a new proposal if needed."
            if parts[1] == "n":
                self.db.execute("UPDATE proposals SET state='cancelled',payload='{}',guard='{}' WHERE nonce=?", (row["nonce"],))
                self._reply(row["chat"], "Change cancelled. Nothing was applied.", replace_id=row["outbox_id"])
                return "Cancelled."
            self.db.execute("UPDATE proposals SET state='queued' WHERE nonce=?", (row["nonce"],))
            return "Approved. Applying the change."

    def claim_action(self) -> dict | None:
        with self.db:
            self._prune(time.time())
            row = self.db.execute("SELECT * FROM proposals WHERE bot=? AND state='queued' ORDER BY rowid LIMIT 1", (self.bot_id,)).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE proposals SET state='executing' WHERE nonce=?", (row["nonce"],))
            return {**dict(row), "payload": json.loads(row["payload"]), "guard": json.loads(row["guard"])}

    def finish_action(self, nonce: str, state: str, result: str) -> None:
        with self.db:
            row = self.db.execute("SELECT * FROM proposals WHERE bot=? AND nonce=? AND state='executing'", (self.bot_id, nonce)).fetchone()
            if row is None:
                return
            self.db.execute("UPDATE proposals SET state=?,result=?,payload='{}',guard='{}' WHERE nonce=?", (state, result, nonce))
            self._reply(row["chat"], result, replace_id=row["outbox_id"])
            self.db.execute("UPDATE jobs SET answer=answer || ? WHERE bot=? AND id=?",
                            ("\n[Approval result] " + result, self.bot_id, row["job"]))
