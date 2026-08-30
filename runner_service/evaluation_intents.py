from __future__ import annotations

import json
from collections import Counter, deque
from typing import Any


_MAX_RECORDED_TRADE_IDENTITIES = 4096
_TRADE_INTENT_TYPES = {"trade_entry", "trade_close"}
_ORDER_SIGNAL_TYPE = "order_signal"


def trade_intent_identity(intent: dict[str, Any]) -> str | None:
    intent_type = intent.get("type")
    if intent_type == _ORDER_SIGNAL_TYPE:
        identity = {
            "type": intent_type,
            "evaluation_candle_time": intent.get("evaluation_candle_time"),
            "action": intent.get("action") or "",
            "order_id": intent.get("order_id") or "",
            "exit_id": intent.get("exit_id") or "",
        }
        return json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    if intent_type not in _TRADE_INTENT_TYPES:
        return None
    identity = {
        "type": intent.get("type"),
        "time": intent.get("time"),
        "size": intent.get("size"),
        "id": intent.get("id") or "",
        "exit_id": intent.get("exit_id") or "",
        "comment": intent.get("comment") or "",
    }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)


class EvaluationIntentRecorder:
    """Collect per-candle comparison intents without changing live event delivery."""

    def __init__(self, max_trade_history: int = _MAX_RECORDED_TRADE_IDENTITIES) -> None:
        self.max_trade_history = max(1, int(max_trade_history))
        self._intents: list[dict[str, Any]] = []
        self._occurrences: Counter[str] = Counter()
        self._recorded_trade_keys: set[tuple[str, int]] = set()
        self._recorded_trade_order: deque[tuple[str, int]] = deque()

    def reset(self) -> None:
        self._intents.clear()
        self._occurrences.clear()
        self._recorded_trade_keys.clear()
        self._recorded_trade_order.clear()

    def begin_candle(self) -> None:
        self._intents.clear()
        self._occurrences.clear()

    def record(
        self,
        event: dict[str, Any],
        *,
        evaluation_candle_time: int,
        suppress_trade_replays: bool,
    ) -> bool:
        payload = dict(event)
        payload["evaluation_candle_time"] = int(evaluation_candle_time)

        identity = trade_intent_identity(payload)
        if identity is not None:
            occurrence = self._occurrences[identity]
            self._occurrences[identity] += 1
            payload["occurrence_index"] = occurrence
            replay_key = (identity, occurrence)
            if suppress_trade_replays:
                if replay_key in self._recorded_trade_keys:
                    return False
                self._recorded_trade_keys.add(replay_key)
                self._recorded_trade_order.append(replay_key)
                while len(self._recorded_trade_order) > self.max_trade_history:
                    expired = self._recorded_trade_order.popleft()
                    self._recorded_trade_keys.discard(expired)

        self._intents.append(payload)
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(intent) for intent in self._intents]
