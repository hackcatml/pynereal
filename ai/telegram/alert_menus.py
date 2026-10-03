"""Requester-bound Manual Alert menus, without model inference."""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import math
import re
import secrets
import time
from decimal import Decimal, DecimalException, localcontext

from ai.scripts.manual_alert_tool import ManualAlertToolError, ManualAlertTools
from data_service.config import sanitize_manual_alert_templates
from data_service.manual_alerts import build_manual_alert_payload, send_manual_alert_payload, validate_manual_alert_template
from .commands import session_label
from .price import _positive_number
from .session_control import SessionCommandError


def _trigger_snapshot(trigger: dict) -> dict:
    return {
        "id": str(trigger.get("id") or ""), "price": trigger.get("price"),
        "title": str((trigger.get("template") or {}).get("title") or "Manual Alert"),
        "revision": hashlib.sha256(json.dumps(trigger, sort_keys=True).encode()).hexdigest(),
    }


def _active_alerts(control, notice: str = "") -> dict:
    alerts = []
    for sid, session in control.registry.sessions.items():
        triggers = [item for item in session.spec.manual_alert_triggers if item.get("enabled")]
        if triggers:
            target = control.snapshot(sid)
            alerts.extend({"target": target, "trigger": _trigger_snapshot(item)} for item in triggers)
    return {"stage": "alerts", "alerts": alerts, "page": 0, "notice": notice}


def _alert_label(item: dict) -> str:
    target, trigger = item["target"], item["trigger"]
    return (f"{target['exchange'].upper()} {target['symbol']} {target['timeframe']} | "
            f"{trigger['title']} | Price: {trigger['price']}")


def _trigger_price(text: str) -> float:
    text = text.strip()
    error = "Enter a price or calculation using numbers and +, -, *, /, parentheses (up to 64 characters)."
    if len(text) > 64 or not re.fullmatch(r"[0-9. +*/()\t-]+", text):
        raise ValueError(error)

    def calculate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Decimal(ast.get_source_segment(text, node))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = calculate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = calculate(node.left), calculate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ValueError("Cannot divide by zero.")
            return left / right
        raise ValueError(error)

    try:
        with localcontext() as context:
            context.prec = 64
            # Preserve plain decimal input, including leading zeros, without evaluating code.
            value = (Decimal(text) if re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", text)
                     else calculate(ast.parse(text, mode="eval").body))
            price = float(value)
    except (SyntaxError, DecimalException, OverflowError):
        raise ValueError(error) from None
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Price must be a finite positive number.")
    return price


async def _send_alert(session, target: dict, request: dict) -> dict:
    template = request["draft"]
    try:
        validate_manual_alert_template(template, session.spec)
    except ValueError as exc:
        raise SessionCommandError(str(exc)) from None
    _, market, traded_at = session.feed.raw_trade_cursor()
    market, traded_at = _positive_number(market), _positive_number(traded_at)
    if (target["collector"] != "running" or traded_at is None
            or not -5 <= time.time() - traded_at / 1000 <= 60):
        market = None
    if market is None:
        raise SessionCommandError("Current market price is unavailable or stale. Nothing was sent. Check the feed and try again.")
    bar_time = session.feed.last_bar_time()
    if bar_time is None and any("{{time}}" in str(template.get(key) or "") for key in ("message", "ai")):
        raise SessionCommandError("Current candle time is unavailable. Nothing was sent. Check the feed and try again.")
    payload = build_manual_alert_payload(template=template, spec=session.spec, price=market,
                                         market=market, time=bar_time)
    result = await asyncio.to_thread(
        send_manual_alert_payload, spec=session.spec, script_title=session._manual_alert_script_title(),
        payload=payload, notify=session.notifications.publish if session.notifications else None,
    )
    dispatched = await session.dispatch_manual_alert_ai_instruction(payload, result, mode="send")
    webhook, telegram = result.get("webhook", {}), result.get("telegram", {})
    notice = session_label(target) + f"\nTemplate: {template['title']}\nPrice: {market}"
    notice += "\nWebhook: " + ("sent" if webhook.get("sent") else "failed or unconfirmed")
    notice += "\nTelegram: " + ("sent" if telegram.get("sent") else "failed" if telegram.get("error") else "not configured")
    if payload.get("ai_instruction"):
        notice += "\nAI instruction: " + ("dispatched" if dispatched else "not run")
    if not webhook.get("sent"):
        notice += "\nNot retried. Check the receiver before sending again."
    return {"stage": "home", "notice": notice}


async def template_command(control, request: dict) -> dict:
    if control is None:
        raise SessionCommandError("Session access is unavailable. Retry /alerts.")
    if request["operation"] == "sessions":
        return {"stage": "sessions", "sessions": control.sessions(), "page": 0,
                "mode": request.get("mode", "template")}
    if request["operation"] == "list":
        return _active_alerts(control)
    try:
        target = await control.execute(request["target"], "status")
    except SessionCommandError:
        raise SessionCommandError("The session changed or was removed. Use /alerts again.") from None
    if request["operation"] == "delete_trigger":
        selected = request["trigger"]
        triggers = control.registry.get(target["session_id"]).spec.manual_alert_triggers
        current = next((item for item in triggers if str(item.get("id") or "") == selected["id"]), None)
        if current is None or not current.get("enabled"):
            return _active_alerts(control, "This alert is no longer active (already triggered or cancelled). Nothing was cancelled.")
        if _trigger_snapshot(current) != selected:
            return _active_alerts(control, "This alert changed. Nothing was cancelled. Select it again to review the current values.")
        removed = await control.registry.delete_manual_alert_triggers({target["session_id"]: {selected["id"]}})
        notice = ("Alert cancelled: " + _alert_label(request) + "\nTemplates are unchanged." if removed else
                  "This alert is no longer active. Nothing was cancelled.")
        return _active_alerts(control, notice)
    current = control.registry.get(target["session_id"]).spec.manual_alert_templates
    if request["operation"] == "templates":
        return {"stage": "templates", "target": target, "templates": copy.deepcopy(current), "page": 0,
                "mode": request.get("mode", "template")}
    if request["operation"] in {"set_trigger", "send_alert"}:
        index = request["index"]
        if (type(index) is not int or not 0 <= index < len(current)
                or current[index] != request["draft"]):
            raise SessionCommandError("The selected template changed or was removed. No alert was set or sent. Use /alerts again.")
        if request["operation"] == "send_alert":
            return await _send_alert(control.registry.get(target["session_id"]), target, request)
        tools = ManualAlertTools(control.registry)
        try:
            arguments = tools._validate_set_arguments({
                "session_id": target["session_id"], "template_index": index, "price": request["price"],
            })
            result = await tools.bridge._execute_on_loop("set_trigger", arguments)
        except ManualAlertToolError as exc:
            raise SessionCommandError(str(exc)) from None
        if not result.get("set"):
            raise RuntimeError("Manual Alert trigger persistence could not be confirmed")
        notice = ("Alert set: " if result["created"] else "Alert already active (no duplicate added): ")
        notice += (session_label(target) + f"\nTemplate: {result['template_title']}\nPrice: {result['price']}"
                   "\nOther alerts and templates are unchanged. No direct alert was sent.")
        return _active_alerts(control, notice)
    if request["operation"] != "save":
        raise SessionCommandError("Unsupported template operation.")
    if current != request["templates"]:
        raise SessionCommandError("Templates changed while you were editing. Nothing was saved. Use /alerts again.")
    draft = request["draft"]
    for key, limit in (("title", 100), ("message", 5000), ("ai", 4000)):
        value = draft.get(key, "")
        if not isinstance(value, str) or len(value) > limit or (key != "ai" and not value.strip()):
            raise SessionCommandError(f"Invalid template {key}.")
    index = request["index"]
    if index is not None and (type(index) is not int or not 0 <= index < len(current)):
        raise SessionCommandError("The template no longer exists. Use /alerts again.")
    if index is None and len(current) >= 50:
        raise SessionCommandError("A session supports at most 50 templates.")
    after = sanitize_manual_alert_templates([draft])[0]
    try:
        validate_manual_alert_template(after, control.registry.get(target["session_id"]).spec)
    except ValueError as exc:
        raise SessionCommandError(str(exc)) from None
    if any(i != index and item["title"] == after["title"] for i, item in enumerate(current)):
        raise SessionCommandError("A template with this title already exists. Nothing was saved.")
    templates = copy.deepcopy(current)
    if index is None:
        templates.append(after)
    else:
        templates[index] = after
    if templates != current:
        saved = await control.registry.update_manual_alert_templates(target["session_id"], templates)
        if saved != templates:
            raise RuntimeError("Template persistence could not be confirmed")
    return {"stage": "saved", "target": target, "title": after["title"]}


class AlertMenuStore:
    def _alert_menu(self, owner, payload: dict, *, replace_id=None) -> None:
        nonce = secrets.token_urlsafe(18)
        stage = payload["stage"]
        buttons = []

        def button(label, operation):
            return {"text": label, "callback_data": f"tl:{nonce}:{operation}"}

        text = "Manual Alerts"
        if stage in {"home", "alerts", "trigger"}:
            if payload.get("notice"):
                text += "\n" + payload["notice"][:500]
            if stage == "alerts":
                alerts, page = payload["alerts"], payload["page"]
                text += f"\nActive Manual Alerts: {len(alerts)} ({page + 1}/{max(1, (len(alerts) + 9) // 10)})"
                text += "\nSelect an alert to cancel it." if alerts else "\nNo active Manual Alert price triggers."
                for index, item in enumerate(alerts[page * 10:page * 10 + 10], page * 10):
                    label = _alert_label(item)
                    buttons.append([button(f"{index + 1}. {label[:115]}", f"a{index}")])
                navigation = []
                if page > 0:
                    navigation.append(button("Previous", f"p{page - 1}"))
                if (page + 1) * 10 < len(alerts):
                    navigation.append(button("Next", f"p{page + 1}"))
                if navigation:
                    buttons.append(navigation)
            elif stage == "trigger":
                text += "\n\n" + _alert_label(payload) + "\n\nCancel this price trigger? Its template will be kept."
                buttons.append([button("Cancel alert", "delete_trigger")])
            buttons.append([button("List", "list"), button("Set alert", "set_alert")])
            buttons.append([button("Send alert", "send_alert"), button("Set templates", "sessions")])
        elif stage in {"sessions", "templates"}:
            items = payload[stage]
            page = payload.get("page", 0)
            setting_alert = payload.get("mode") in {"trigger", "send"}
            text = ("Select a session" if stage == "sessions" else session_label(payload["target"]) +
                    ("\nSelect an alert template" if setting_alert else "\nSelect a template or create one"))
            text += f" ({page + 1}/{max(1, (len(items) + 9) // 10)})."
            if stage == "templates" and not setting_alert and len(items) < 50:
                buttons.append([button("New template", "new")])
            if not items:
                text += "\nNo sessions registered." if stage == "sessions" else "\nNo templates yet."
                if stage == "templates" and setting_alert:
                    action = "Send alert" if payload.get("mode") == "send" else "Set alert"
                    text += f" Create a template with Set templates first, then return to {action}."
                    buttons.append([button("Set templates", "sessions")])
            for index, item in enumerate(items[page * 10:page * 10 + 10], page * 10):
                label = session_label(item) if stage == "sessions" else item["title"]
                buttons.append([button(label[:120], ("s" if stage == "sessions" else "t") + str(index))])
            navigation = []
            if page > 0:
                navigation.append(button("Previous", f"p{page - 1}"))
            if (page + 1) * 10 < len(items):
                navigation.append(button("Next", f"p{page + 1}"))
            if navigation:
                buttons.append(navigation)
        else:
            draft = payload["draft"]
            text = session_label(payload["target"])
            if stage == "price":
                text += (f"\nTemplate: {draft['title']}\n\nSend the trigger price or a calculation, e.g. 252.41 * 0.996."
                         "\nUse numbers and +, -, *, /, parentheses. The result must be positive."
                         "\nReply to this prompt in groups. Use /cancel to discard.")
            elif stage in {"trigger_review", "send_review"}:
                text += f"\n\nTemplate: {draft['title']}"
                if stage == "send_review":
                    text += "\nPrice: Market price at send time"
                else:
                    if payload.get("price_expression"):
                        text += f"\nCalculation: {payload['price_expression']}"
                    text += f"\nPrice: {payload['price']}"
                for key, label in (("message", "Message"), ("ai", "AI instruction")):
                    value = draft.get(key)
                    if value:
                        limit = 650 if stage == "send_review" else 850
                        text += f"\n{label} (preview):\n" + (value[:limit] + "..." if len(value) > limit else value)
                if stage == "send_review":
                    text += ("\n\nSend this webhook now? It may place a real order."
                             "\n{{price}} and {{market}} both use the latest received trade price when sending."
                             " {{time}} uses the latest session candle's start time (Unix seconds).")
                    buttons.append([button("Send now", "send_alert")])
                else:
                    text += ("\n\nSet this price trigger? It fires when market price touches the target."
                             "\nOther alerts are kept. This does not send a direct alert.")
                    buttons.append([button("Change price", "price"), button("Set alert", "set_trigger")])
            elif stage == "review":
                text += "\n\nTemplate preview (long fields are shortened):"
                for key, label in (("title", "Title"), ("message", "Message"), ("ai", "AI instruction")):
                    value = draft.get(key) or "(none)"
                    text += f"\n{label}:\n" + (value[:850] + "..." if len(value) > 850 else value)
                text += "\n\nSave updates the template only. Existing price triggers stay unchanged. No alert is sent."
                buttons.extend([[button("Title", "title"), button("Message", "message"), button("AI instruction", "ai")],
                                [button("Save", "save")]])
            else:
                label = {"title": "title (up to 100 characters)", "message": "message (up to 5000 characters)",
                         "ai": "optional AI instruction (up to 4000 characters)"}[stage]
                text += f"\n\nSend the {label} as a text message, or reply to this prompt.\nUse /cancel to discard."
                if stage == "message":
                    text += "\nPlaceholders such as {{market}} are kept unchanged."
                if draft.get(stage):
                    value = draft[stage]
                    text += "\nCurrent value (preview):\n" + (value[:1500] + "..." if len(value) > 1500 else value)
                    buttons.append([button("Keep", "keep")])
                if stage == "ai":
                    buttons.append([button("Clear" if draft.get("ai") else "Skip", "skip")])
        buttons.append([button("Close" if stage in {"alerts", "trigger"} else "Cancel", "cancel")])
        item_id = self._menu_message(owner["chat"], text, buttons, replace_id=replace_id)
        self.db.execute("DELETE FROM alert_menus WHERE bot=? AND chat=? AND actor=?",
                        (self.bot_id, owner["chat"], owner["actor"]))
        self.db.execute("INSERT INTO alert_menus VALUES (?,?,?,?,?,?,?)",
                        (nonce, self.bot_id, owner["chat"], owner["actor"], json.dumps(payload), time.time() + 600, item_id))

    def _queue_alert(self, update_id: int, row, payload: dict, operation: str) -> str:
        request = {**payload, "operation": operation, "menu_nonce": row["nonce"]}
        if not self._queue_direct(update_id, row["chat"], row["actor"], "alert", "/alerts " + operation, request):
            return "Request queue is full. Retry later."
        self.db.execute("UPDATE alert_menus SET payload=? WHERE nonce=?",
                        (json.dumps({"stage": "busy", "job": update_id}), row["nonce"]))
        return {"save": "Saving template.", "delete_trigger": "Cancelling alert.", "set_trigger": "Setting alert.",
                "send_alert": "Sending alert.", "list": "Loading alerts."}.get(operation, "Loading sessions/templates.")

    def _alert_callback(self, update_id: int, query: dict, parts: list[str], now: float) -> str:
        row = self.db.execute("SELECT m.*,o.message_id FROM alert_menus m JOIN outbox o ON o.id=m.outbox_id "
                              "WHERE m.bot=? AND m.nonce=?", (self.bot_id, parts[1])).fetchone()
        message = query.get("message") or {}
        if row is None or row["expires"] <= now:
            return "This menu expired or was handled already. Use /alerts again."
        if (row["chat"] != message.get("chat", {}).get("id") or row["actor"] != query.get("from", {}).get("id")
                or row["message_id"] is None or row["message_id"] != message.get("message_id")):
            return "Only the requester can use the current alert buttons."
        payload = json.loads(row["payload"])
        stage, operation = payload["stage"], parts[2]
        if stage == "busy":
            return "Request already started. Use /cancel to cancel work that has not started applying changes."
        if operation == "cancel":
            self.db.execute("DELETE FROM alert_menus WHERE nonce=?", (row["nonce"],))
            self._reply(row["chat"], "Menu closed. No changes applied." if stage in {"alerts", "trigger"} else
                        "Alert/template setup cancelled. Nothing was saved.", replace_id=row["outbox_id"])
            return "Cancelled."
        if stage in {"home", "alerts", "trigger"} and operation in {"list", "sessions", "set_alert", "send_alert"}:
            if operation in {"set_alert", "send_alert"}:
                return self._queue_alert(update_id, row, {"mode": "send" if operation == "send_alert" else "trigger"}, "sessions")
            return self._queue_alert(update_id, row, {}, operation)
        if (stage == "templates" and payload.get("mode") in {"trigger", "send"} and not payload["templates"]
                and operation == "sessions"):
            return self._queue_alert(update_id, row, {}, "sessions")
        if stage == "alerts":
            if (operation[:1] not in {"a", "p"} or not operation[1:].isascii()
                    or not operation[1:].isdecimal() or len(operation) > 7):
                return "Invalid alert selection."
            index = int(operation[1:])
            if operation.startswith("p") and index * 10 < len(payload["alerts"]):
                payload["page"] = index
            elif operation.startswith("a") and index < len(payload["alerts"]):
                payload = {"stage": "trigger", **payload["alerts"][index]}
            else:
                return "Invalid alert selection."
        elif stage == "trigger":
            if operation != "delete_trigger":
                return "Invalid alert action."
            return self._queue_alert(update_id, row, payload, "delete_trigger")
        elif stage in {"sessions", "templates"}:
            if (stage == "templates" and payload.get("mode") not in {"trigger", "send"}
                    and operation == "new" and len(payload["templates"]) < 50):
                payload.update(stage="title", index=None, draft={})
            elif (operation[:1] in {"p", "s" if stage == "sessions" else "t"}
                  and operation[1:].isascii() and operation[1:].isdecimal() and len(operation) <= 7):
                index = int(operation[1:])
                items = payload[stage]
                if operation.startswith("p") and index * 10 < len(items):
                    payload["page"] = index
                elif not operation.startswith("p") and index < len(items):
                    if stage == "sessions":
                        return self._queue_alert(update_id, row, {"target": items[index],
                            "mode": payload.get("mode", "template")}, "templates")
                    payload.update(stage={"trigger": "price", "send": "send_review"}.get(payload.get("mode"), "review"),
                                   index=index, draft=dict(items[index]))
                else:
                    return "Invalid selection."
            else:
                return "Invalid selection."
        elif stage in {"trigger_review", "send_review"}:
            action = "send_alert" if stage == "send_review" else "set_trigger"
            if operation == action:
                return self._queue_alert(update_id, row, payload, action)
            if stage != "trigger_review" or operation != "price":
                return "Invalid alert action."
            payload.update(stage="price")
        elif stage == "review":
            if operation == "save":
                return self._queue_alert(update_id, row, payload, "save")
            if operation not in {"title", "message", "ai"}:
                return "Invalid template action."
            payload.update(stage=operation, editing=True)
        elif stage in {"title", "message", "ai"}:
            if operation == "skip" and stage == "ai":
                payload["draft"]["ai"] = ""
            elif operation != "keep" or not payload["draft"].get(stage):
                return "Invalid template action."
            self._advance_alert_field(payload)
        else:
            return "Invalid template action."
        if payload["stage"] == "home":
            return "Invalid menu action."
        self._alert_menu(row, payload, replace_id=row["outbox_id"])
        return {"review": "Review and save.", "alerts": "Select an alert.", "price": "Enter the price.",
                "trigger_review": "Review and set the alert.", "send_review": "Review before sending.",
                "trigger": "Review the alert before cancelling."}.get(payload["stage"], "Continue template setup.")

    @staticmethod
    def _advance_alert_field(payload: dict) -> None:
        payload["stage"] = ("review" if payload.pop("editing", False) else
                            {"title": "message", "message": "ai", "ai": "review"}[payload["stage"]])

    def _alert_text(self, message: dict, text: str, now: float) -> bool:
        row = self.db.execute("SELECT m.*,o.message_id FROM alert_menus m JOIN outbox o ON o.id=m.outbox_id "
                              "WHERE m.bot=? AND m.chat=? AND m.actor=?",
                              (self.bot_id, message["chat"]["id"], message["from"]["id"])).fetchone()
        if row is None:
            return False
        if row["expires"] <= now:
            self.db.execute("DELETE FROM alert_menus WHERE nonce=?", (row["nonce"],))
            self._reply(row["chat"], "Alert/template input expired. Nothing was saved. Use /alerts again.")
            return True
        payload = json.loads(row["payload"])
        stage = payload["stage"]
        reply = message.get("reply_to_message", {}).get("message_id")
        if row["message_id"] is None or (reply is not None and reply != row["message_id"]):
            self._reply(row["chat"], "Wait for and reply to the current /alerts prompt, or use /cancel.")
            return True
        if stage == "price":
            try:
                if not message.get("text"):
                    raise ValueError("Send the price as a text message, not an attachment.")
                payload["price"] = _trigger_price(text)
            except ValueError as exc:
                self._reply(row["chat"], str(exc))
                return True
            if re.search(r"[+*/()-]", text):
                payload["price_expression"] = text.strip()
            else:
                payload.pop("price_expression", None)
            payload["stage"] = "trigger_review"
            self._alert_menu(row, payload, replace_id=row["outbox_id"])
            return True
        if stage not in {"title", "message", "ai"}:
            self._reply(row["chat"], "Use the /alerts buttons to continue, or /cancel to discard the draft.")
            return True
        limit = {"title": 100, "message": 5000, "ai": 4000}[stage]
        if not message.get("text") or not text or len(text) > limit:
            self._reply(row["chat"], f"Send a non-empty text message of up to {limit} characters.")
            return True
        if stage == "title" and any(i != payload["index"] and item["title"] == text
                                    for i, item in enumerate(payload["templates"])):
            self._reply(row["chat"], "A template with this title already exists. Send another title.")
            return True
        payload["draft"][stage] = text
        self._advance_alert_field(payload)
        self._alert_menu(row, payload, replace_id=row["outbox_id"])
        return True

    def begin_alert_command(self, job: dict) -> None:
        with self.db:
            self._running(job)
            request = json.loads(job["input"])
            row = self.db.execute("SELECT payload,expires FROM alert_menus WHERE bot=? AND nonce=?",
                                  (self.bot_id, request["menu_nonce"])).fetchone()
            if row is None or row["expires"] <= time.time() or json.loads(row["payload"]).get("job") != job["id"]:
                raise SessionCommandError("Alert request expired or was replaced. No changes applied. Use /alerts again.")
            if request["operation"] in {"save", "delete_trigger", "set_trigger", "send_alert"}:
                self.db.execute("UPDATE jobs SET state='executing' WHERE bot=? AND id=?", (self.bot_id, job["id"]))

    def finish_alert_command(self, job: dict, payload: dict | None, error: str = "") -> None:
        with self.db:
            changed = self.db.execute("UPDATE jobs SET state=? WHERE bot=? AND id=? AND state IN ('running','executing')",
                                      ("failed" if error else "done", self.bot_id, job["id"])).rowcount
            if not changed:
                return
            request = json.loads(job["input"])
            row = self.db.execute("SELECT * FROM alert_menus WHERE bot=? AND nonce=?",
                                  (self.bot_id, request["menu_nonce"])).fetchone()
            if error and request["operation"] in {"list", "delete_trigger", "set_trigger", "send_alert"} and row:
                self._alert_menu(job, {"stage": "home", "notice": error}, replace_id=row["outbox_id"])
            elif error or payload["stage"] == "saved":
                text = error or (session_label(payload["target"]) + "\nTemplate saved: " + payload["title"]
                                 + "\nExisting price triggers are unchanged. No alert was sent.")
                self._reply(job["chat"], text, replace_id=row["outbox_id"] if row else None)
                if row:
                    self.db.execute("DELETE FROM alert_menus WHERE nonce=?", (row["nonce"],))
            elif row and row["expires"] > time.time():
                self._alert_menu(job, payload, replace_id=row["outbox_id"])
            elif payload.get("notice"):
                self._reply(job["chat"], payload["notice"])
