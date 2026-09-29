from __future__ import annotations

import re
from pathlib import PurePosixPath


BOT_COMMANDS = [
    {"command": "ai", "description": "Start an AI conversation"},
    {"command": "screenshot", "description": "Choose a session or capture directly: /screenshot mrvl"},
    {"command": "assets", "description": "Select all assets or an exchange"},
    {"command": "positions", "description": "Show open positions across all configured accounts"},
    {"command": "sessions", "description": "Select a session and control runner, webhook and Telegram"},
    {"command": "pnl", "description": "Select a PnL period or use /pnl 30d"},
    {"command": "alerts", "description": "List/set Manual Alerts or create/edit session templates"},
    {"command": "model", "description": "Select model and reasoning effort"},
    {"command": "new", "description": "Start a new chat"},
    {"command": "cancel", "description": "Cancel unfinished work"},
    {"command": "end", "description": "End the AI conversation"},
    {"command": "help", "description": "Show command help"},
]


PNL_PERIODS = {"7d": 7, "30d": 30, "90d": 90, "6m": 180, "1y": 365, "all": None}


def pnl_days(argument: str) -> int | None:
    value = argument.strip().lower()
    if value not in PNL_PERIODS:
        raise ValueError("Use /pnl to select a period, or /pnl [7d, 30d, 90d, 6m, 1y, all].")
    return PNL_PERIODS[value]


def match_sessions(sessions: list[dict], query: str) -> list[dict]:
    query = query.strip().casefold()
    exact = [item for item in sessions if item["session_id"].casefold() == query]
    if exact:
        return exact
    tokens = re.findall(r"[^\W_]+", query)
    if not tokens:
        return []
    matches = []
    for item in sessions:
        symbol = item["symbol"].casefold()
        aliases = set(re.findall(r"[^\W_]+", symbol))
        aliases.update((item["exchange"].casefold(), item["timeframe"].casefold(),
                        re.sub(r"[\W_]+", "", symbol.split(":")[0])))
        if all(token in aliases for token in tokens):
            matches.append(item)
    return matches


def session_label(session: dict) -> str:
    label = f"{session['exchange'].upper()} {session['symbol']} {session['timeframe']}"
    script = PurePosixPath(session.get("script_name") or "").name
    return f"{label} | {script}" if script else label
