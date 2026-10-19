"""Durable requester-bound session menus on TelegramStore's DB executor."""
from __future__ import annotations

import json
import secrets
import time

from .commands import session_label
from .session_control import SESSION_ACTIONS
from .account_reports import menu_text_entities


class SessionMenuStore:
    def _session_menu(self, job: dict, payload: dict, *, replace_id=None) -> str:
        payload = dict(payload)
        payload.pop("busy", None)
        nonce = secrets.token_urlsafe(18)
        buttons = []
        prefix = "tq" if payload.get("purpose") == "price" else "tc"
        if payload["view"] == "list":
            page, sessions = payload["page"], payload["sessions"]
            pages = max(1, (len(sessions) + 9) // 10)
            title = "Prices" if prefix == "tq" else "Sessions"
            text = f"{title} ({page + 1}/{pages})\nSelect a session. Buttons expire in 10 minutes."
            if not sessions:
                text = "No matching sessions." if payload.get("query") else "No sessions are registered."
            if payload.get("report"):
                text += "\n\n" + payload["report"]
            for index, session in enumerate(sessions[page * 10:page * 10 + 10], page * 10):
                buttons.append([{"text": session_label(session)[:120], "callback_data": f"{prefix}:{nonce}:s{index}"}])
            navigation = []
            if page > 0:
                navigation.append({"text": "Previous", "callback_data": f"{prefix}:{nonce}:p{page - 1}"})
            if page + 1 < pages:
                navigation.append({"text": "Next", "callback_data": f"{prefix}:{nonce}:p{page + 1}"})
            if navigation:
                buttons.append(navigation)
        else:
            session = payload["session"]
            text = (session_label(session) + f"\nAs of (UTC): {session['collected_at']}"
                    f"\nRunner: {session['runner_status']}"
                    f"\nCalculation: {session['calculation']} | Phase: {session['phase']}"
                    f"\nData: {'ready' if session['history_ready'] else 'preparing'} | Feed: {session['collector']}"
                    f"\nWebhook: {'ON' if session['webhook'] else 'OFF'}"
                    f"\nTelegram alerts: {'ON' if session['telegram'] else 'OFF'}")
            for field, label in (("webhook", "webhook"), ("telegram", "Telegram alerts"), ("runner", "runner")):
                desired = not session[field]
                verb = ("Start" if desired else "Stop") if field == "runner" else ("Enable" if desired else "Disable")
                action = f"{field}_{'on' if desired else 'off'}"
                buttons.append([{"text": f"{verb} {label}", "callback_data": f"tc:{nonce}:{action}"}])
            buttons.append([{"text": "Refresh", "callback_data": f"tc:{nonce}:status"},
                            {"text": "Sessions", "callback_data": f"tc:{nonce}:list"}])
            text += "\nButtons apply changes immediately. Stop does not close exchange positions."
            if payload.get("report"):
                text += "\n\n" + payload["report"]
        buttons.append([{"text": "Close", "callback_data": f"{prefix}:{nonce}:close"}])
        item_id = self._menu_message(job["chat"], text, buttons, replace_id=replace_id)
        self.db.execute("DELETE FROM session_menus WHERE bot=? AND chat=? AND actor=?",
                        (self.bot_id, job["chat"], job["actor"]))
        self.db.execute("INSERT INTO session_menus VALUES (?,?,?,?,?,?,?)",
                        (nonce, self.bot_id, job["chat"], job["actor"], json.dumps(payload), time.time() + 600, item_id))
        return text

    def _session_callback(self, update_id: int, query: dict, parts: list[str], now: float) -> str:
        row = self.db.execute("SELECT s.*,o.message_id FROM session_menus s JOIN outbox o ON o.id=s.outbox_id "
                              "WHERE s.bot=? AND s.nonce=?", (self.bot_id, parts[1])).fetchone()
        message = query.get("message") or {}
        if row is None or row["expires"] <= now:
            command = "/price" if parts[0] == "tq" else "/sessions"
            return f"This menu expired or was handled already. Use {command} again."
        if (row["chat"] != message.get("chat", {}).get("id") or row["actor"] != query.get("from", {}).get("id")
                or row["message_id"] is None or row["message_id"] != message.get("message_id")):
            return "Only the requester can use the current session buttons."
        operation = parts[2]
        payload = json.loads(row["payload"])
        is_price = payload.get("purpose") == "price"
        if parts[0] != ("tq" if is_price else "tc"):
            return "Invalid session selection."
        if operation == "close":
            self.db.execute("DELETE FROM session_menus WHERE nonce=?", (row["nonce"],))
            self._reply(row["chat"], "Price menu closed." if is_price else "Session menu closed.", replace_id=row["outbox_id"])
            return "Closed."
        if payload.get("busy"):
            return "Request is loading. Please wait."
        target = None
        if payload["view"] == "list" and operation[:1] in {"s", "p"} and operation[1:].isascii() and operation[1:].isdecimal():
            sessions = payload.get("sessions", [])
            index = operation[1:]
            if operation[:1] not in {"s", "p"} or not index.isascii() or not index.isdecimal() or len(index) > 6:
                return "Invalid session selection."
            index = int(index)
            if operation.startswith("p"):
                if index * 10 >= len(sessions):
                    return "Invalid session page."
                payload["page"] = index
                self._session_menu(dict(row), payload, replace_id=row["outbox_id"])
                return "Select a session."
            if index >= len(sessions):
                return "Invalid session selection."
            target, operation = sessions[index], "price" if is_price else "status"
        elif payload["view"] == "list":
            return "Invalid session selection."
        else:
            if operation not in {"status", "list", *SESSION_ACTIONS}:
                return "Invalid session action."
            target = payload["session"]
            if operation in SESSION_ACTIONS:
                field, desired = SESSION_ACTIONS[operation]
                if desired == target[field]:
                    return "Invalid session action. Use the displayed buttons."
        metadata = {"operation": operation, "target": target, "replace_id": row["outbox_id"], "menu_nonce": row["nonce"]}
        kind, command = ("price", "/price") if is_price else ("session", "/sessions")
        if not self._queue_direct(update_id, row["chat"], row["actor"], kind, command + " " + operation, metadata):
            return "Request queue is full. Retry later."
        payload["busy"] = update_id
        self.db.execute("UPDATE session_menus SET payload=? WHERE nonce=?", (json.dumps(payload), row["nonce"]))
        self.db.execute("UPDATE outbox SET job_id=? WHERE bot=? AND id=?", (update_id, self.bot_id, row["outbox_id"]))
        if is_price:
            return "Loading price."
        return "Updating session." if operation in SESSION_ACTIONS else "Loading session."

    def begin_session_command(self, job: dict) -> None:
        with self.db:
            self._running(job)
            request = json.loads(job["input"])
            if request.get("menu_nonce"):
                row = self.db.execute("SELECT payload,expires FROM session_menus WHERE bot=? AND nonce=?",
                                      (self.bot_id, request["menu_nonce"])).fetchone()
                if row is None or row["expires"] <= time.time() or json.loads(row["payload"]).get("busy") != job["id"]:
                    raise ValueError("The session menu is no longer active")
            if request["operation"] in SESSION_ACTIONS:
                # Once execution starts, cancellation must not interrupt process/config changes.
                self.db.execute("UPDATE jobs SET state='executing' WHERE bot=? AND id=?", (self.bot_id, job["id"]))

    def session_command_executing(self, job: dict) -> bool:
        row = self.db.execute("SELECT state FROM jobs WHERE bot=? AND id=?", (self.bot_id, job["id"])).fetchone()
        return row is not None and row[0] == "executing"

    def finish_session_command(self, job: dict, payload: dict | None, error: str = "") -> None:
        with self.db:
            changed = self.db.execute("UPDATE jobs SET state=? WHERE bot=? AND id=? AND state IN ('running','executing')",
                                      ("failed" if error else "done", self.bot_id, job["id"])).rowcount
            if not changed:
                return
            request = json.loads(job["input"])
            replace_id = request.get("replace_id")
            context = None
            if request.get("menu_nonce"):
                row = self.db.execute("SELECT * FROM session_menus WHERE bot=? AND nonce=?",
                                      (self.bot_id, request["menu_nonce"])).fetchone()
                if row is None or row["expires"] <= time.time():
                    return
                context = json.loads(row["payload"])
                if context.get("busy") != job["id"]:
                    return
                context.pop("busy", None)
            if error:
                if context is not None:
                    context["report"] = error
                    self._session_menu(job, context, replace_id=replace_id)
                else:
                    self._reply(job["chat"], error, replace_id=replace_id, request_id=job["id"], entities=menu_text_entities(error))
                answer = error
            elif payload["view"] == "price":
                answer = payload["report"]
                if context is not None:
                    context["report"] = answer
                    self._session_menu(job, context, replace_id=replace_id)
                else:
                    self._reply(job["chat"], answer, replace_id=replace_id, request_id=job["id"], entities=menu_text_entities(answer))
            else:
                answer = self._session_menu(job, payload, replace_id=replace_id)
            self.db.execute("UPDATE jobs SET answer=? WHERE bot=? AND id=?", (answer, self.bot_id, job["id"]))
