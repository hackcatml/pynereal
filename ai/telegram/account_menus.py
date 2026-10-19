"""Requester-bound asset and position reports with persistent exchange buttons."""
from __future__ import annotations

import json
import secrets
import time

from .transport import formatted_message_pages
from .commands import PNL_PERIODS


_TABLES = {"assets": "asset_menus", "positions": "position_menus", "pnl": "pnl_menus"}


class AccountMenuStore:
    def _cancel_account_queries(self, view: str, chat: int, actor: int) -> None:
        rows = self.db.execute(
            "SELECT id,input FROM jobs WHERE bot=? AND chat=? AND actor=? "
            "AND kind='account' AND state IN ('queued','running')", (self.bot_id, chat, actor),
        ).fetchall()
        for row in rows:
            if json.loads(row["input"]).get("view") == view:
                self.db.execute("UPDATE jobs SET state='cancelled' WHERE bot=? AND id=?", (self.bot_id, row["id"]))
        self.db.execute(f"DELETE FROM {_TABLES[view]} WHERE bot=? AND chat=? AND actor=?", (self.bot_id, chat, actor))

    def _cancel_position_queries(self, chat: int, actor: int) -> None:
        self._cancel_account_queries("positions", chat, actor)

    @staticmethod
    def _account_buttons(view: str, nonce: str, exchanges: list[str], payload: dict) -> list:
        prefix = {"positions": "to", "assets": "te", "pnl": "tp"}[view]
        if view == "pnl":
            choices = [{"text": key.upper() if key != "all" else "All", "callback_data": f"{prefix}:{nonce}:{key}"}
                       for key in PNL_PERIODS]
            buttons = [choices[:3], choices[3:]]
        else:
            buttons = [[{"text": "All", "callback_data": f"{prefix}:{nonce}:all"}]]
            choices = [{"text": exchange.upper(), "callback_data": f"{prefix}:{nonce}:e{exchange}"}
                       for exchange in exchanges]
            buttons.extend(choices[index:index + 2] for index in range(0, len(choices), 2))
        pages, page = payload.get("pages", []), payload.get("page", 0)
        navigation = []
        if page:
            navigation.append({"text": "Previous", "callback_data": f"{prefix}:{nonce}:p{page - 1}"})
        if page + 1 < len(pages):
            navigation.append({"text": "Next", "callback_data": f"{prefix}:{nonce}:p{page + 1}"})
        if navigation:
            buttons.append(navigation)
        buttons.append([{"text": "Cancel", "callback_data": f"{prefix}:{nonce}:cancel"}])
        return buttons

    def _create_account_menu(self, view: str, chat: int, actor: int, exchanges: list[str], now: float,
                             *, partial: bool = False, job_id: int | None = None) -> None:
        nonce = secrets.token_urlsafe(18)
        payload = {"partial": partial}
        text = ("Select a PnL period. Only you can select within 10 minutes." if view == "pnl" else
                f"Select all {view} or an exchange. Only you can select within 10 minutes.")
        if partial:
            text += "\nSome account scopes are unavailable."
        item_id = self._menu_message(chat, text, self._account_buttons(view, nonce, exchanges, payload))
        if job_id is not None:
            self.db.execute("UPDATE outbox SET job_id=? WHERE bot=? AND id=?", (job_id, self.bot_id, item_id))
        if view == "pnl":
            self.db.execute("INSERT INTO pnl_menus(nonce,bot,chat,actor,expires,outbox_id,payload) VALUES (?,?,?,?,?,?,?)",
                            (nonce, self.bot_id, chat, actor, now + 600, item_id, json.dumps(payload)))
        else:
            self.db.execute(f"INSERT INTO {_TABLES[view]}(nonce,bot,chat,actor,exchanges,expires,outbox_id,payload) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (nonce, self.bot_id, chat, actor, json.dumps(exchanges), now + 600, item_id, json.dumps(payload)))

    def _render_account_menu(self, view: str, row: dict, payload: dict) -> None:
        exchanges = json.loads(row.get("exchanges", "[]"))
        pages = payload.get("pages", [])
        entities = []
        if pages:
            page = payload.get("page", 0)
            text = pages[page]["text"]
            entities = pages[page]["entities"]
            if len(pages) > 1:
                text += f"\n\nPage {page + 1}/{len(pages)}"
        else:
            text = ("Select a PnL period. Only you can select within 10 minutes." if view == "pnl" else
                    f"Select all {view} or an exchange. Only you can select within 10 minutes.")
            if payload.get("partial"):
                text += "\nSome account scopes are unavailable."
        self._menu_message(row["chat"], text, self._account_buttons(view, row["nonce"], exchanges, payload),
                           replace_id=row["outbox_id"], entities=entities)

    def _position_callback(self, update_id: int, query: dict, parts: list[str], now: float) -> str:
        return self._account_callback("positions", update_id, query, parts, now)

    def _asset_callback(self, update_id: int, query: dict, parts: list[str], now: float) -> str:
        return self._account_callback("assets", update_id, query, parts, now)

    def _pnl_callback(self, update_id: int, query: dict, parts: list[str], now: float) -> str:
        return self._account_callback("pnl", update_id, query, parts, now)

    def _account_callback(self, view: str, update_id: int, query: dict, parts: list[str], now: float) -> str:
        table = _TABLES[view]
        found = self.db.execute(
            f"SELECT m.*,o.message_id FROM {table} m JOIN outbox o ON o.id=m.outbox_id "
            "WHERE m.bot=? AND m.nonce=?", (self.bot_id, parts[1]),
        ).fetchone()
        message = query.get("message") or {}
        if found is None or found["expires"] <= now:
            return f"This selection expired. Use /{view} again."
        row = dict(found)
        if (row["chat"] != message.get("chat", {}).get("id") or row["actor"] != query.get("from", {}).get("id")
                or row["message_id"] is None or row["message_id"] != message.get("message_id")):
            return "Only the requester can use the current exchange buttons."
        choice = parts[2]
        if choice == "cancel":
            self._cancel_account_queries(view, row["chat"], row["actor"])
            noun = {"positions": "Position", "assets": "Asset", "pnl": "PnL"}[view]
            self._reply(row["chat"], f"{noun} selection cancelled.", replace_id=row["outbox_id"])
            return "Cancelled."
        payload = json.loads(row["payload"])
        if payload.get("busy"):
            return "PnL is loading. Please wait." if view == "pnl" else f"{view.capitalize()} are loading. Please wait."
        if choice.startswith("p"):
            pages = payload.get("pages", [])
            choices = {f"p{index}": index for index in range(len(pages))}
            if choice not in choices:
                return "Invalid report page."
            payload["page"] = choices[choice]
            self.db.execute(f"UPDATE {table} SET payload=? WHERE nonce=?", (json.dumps(payload), row["nonce"]))
            self._render_account_menu(view, row, payload)
            return "Page updated."
        if view == "pnl":
            if choice not in PNL_PERIODS:
                return "Invalid PnL period."
            request = {"view": view, "operation": "report", "days": PNL_PERIODS[choice], "menu_nonce": row["nonce"]}
        else:
            choices = {f"e{exchange}": exchange for exchange in json.loads(row["exchanges"])}
            if choice != "all" and choice not in choices:
                return "Invalid exchange selection."
            request = {"view": view, "operation": "report", "exchange": choices.get(choice), "menu_nonce": row["nonce"]}
        if not self._queue_direct(update_id, row["chat"], row["actor"], "account", f"/{view}", request):
            return "Request queue is full. Retry later."
        payload["busy"] = update_id
        self.db.execute(f"UPDATE {table} SET payload=? WHERE nonce=?", (json.dumps(payload), row["nonce"]))
        self.db.execute("UPDATE outbox SET job_id=? WHERE bot=? AND id=?", (update_id, self.bot_id, row["outbox_id"]))
        return "Loading PnL." if view == "pnl" else f"Loading {view}."

    def finish_position_command(self, job: dict, result: dict | None, error: str = "") -> None:
        self.finish_account_command(job, result, error)

    def finish_account_command(self, job: dict, result: dict | None, error: str = "") -> None:
        request = json.loads(job["input"])
        view = request["view"]
        table = _TABLES[view]
        with self.db:
            changed = self.db.execute(
                "UPDATE jobs SET state=?,answer=? WHERE bot=? AND id=? AND state='running'",
                ("failed" if error else "done", error or result["report"], self.bot_id, job["id"]),
            ).rowcount
            if not changed:
                return
            if request["operation"] == "menu":
                exchanges = result["exchanges"] if not error else []
                if not exchanges:
                    self._reply(job["chat"], error or result["report"], request_id=job["id"])
                    return
                self._create_account_menu(view, job["chat"], job["actor"], exchanges, time.time(),
                                          partial=not result["complete"], job_id=job["id"])
                return
            found = self.db.execute(f"SELECT * FROM {table} WHERE bot=? AND nonce=?",
                                    (self.bot_id, request["menu_nonce"])).fetchone()
            if found is None:
                return
            row = dict(found)
            payload = json.loads(row["payload"])
            if payload.get("busy") != job["id"]:
                return
            payload.pop("busy", None)
            exchanges = json.loads(row.get("exchanges", "[]"))
            if view == "positions" and not error and result["complete"]:
                if request.get("exchange") is None:
                    exchanges = result["exchanges"]
                elif not result["exchanges"]:
                    exchanges = [value for value in exchanges if value != request["exchange"]]
            if view != "pnl" and not exchanges:
                self.db.execute(f"DELETE FROM {table} WHERE nonce=?", (row["nonce"],))
                self._reply(job["chat"], error or result["report"], replace_id=row["outbox_id"])
                return
            payload["pages"] = formatted_message_pages(error or result["report"],
                                                       [] if error else result.get("entities", []))
            payload["page"] = 0
            row["exchanges"] = json.dumps(exchanges)
            if view == "pnl":
                self.db.execute(f"UPDATE {table} SET payload=? WHERE nonce=?", (json.dumps(payload), row["nonce"]))
            else:
                self.db.execute(f"UPDATE {table} SET exchanges=?,payload=? WHERE nonce=?",
                                (row["exchanges"], json.dumps(payload), row["nonce"]))
            self._render_account_menu(view, row, payload)
