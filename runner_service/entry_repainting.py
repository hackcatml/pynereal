"""Compare live entry fills with later historical replays, without retaining trades."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from typing import Any
from uuid import uuid4


class EntryRepaintingMonitor:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.scope_id = uuid4().hex
        self._scope: Any = None
        self._entries: dict[tuple, dict[str, Any]] = {}
        self._reported: set[tuple] = set()
        self._replayed: dict[tuple, dict[str, Any]] = {}
        self._occurrences: Counter = Counter()
        self._watched_ids: set[str] = set()
        self._watch_since: int | None = None
        self._replaying = False
        self._live_time: int | None = None
        self._hidden_fill_time: int | None = None
        self._last_entry: dict[str, Any] | None = None

    def begin_replay(self, source_hashes: dict, history_start: int) -> None:
        scope = (tuple(sorted(source_hashes.items())), history_start)
        if scope != self._scope:
            self.reset()
            self._scope = scope
        self._replaying = True
        self._live_time = None
        self._replayed.clear()
        self._occurrences.clear()

    def begin_live(self, candle_time: int, hidden_fill_time: int | None = None) -> None:
        self._replaying = False
        self._live_time = candle_time
        self._hidden_fill_time = hidden_fill_time
        self._occurrences.clear()
        self._last_entry = None

    def end_live(self) -> None:
        self._live_time = None

    def register_supplemental(self, watches: list[dict]) -> None:
        for watch in watches:
            if watch.get("scope_id") != self.scope_id:
                continue
            entry = dict(watch["entry"])
            key = (entry["entry_id"], entry["time"], entry["direction"], entry["occurrence_index"])
            self._entries.setdefault(key, entry)
            self._watched_ids.add(entry["entry_id"])
            start = entry["lifecycle_start"]
            self._watch_since = min(start, self._watch_since) if self._watch_since is not None else start

    def entry_for_order(self, signal: dict) -> dict | None:
        entry = self._last_entry
        self._last_entry = None
        if (entry is None or signal.get("action") != "entry"
                or entry["entry_id"] != signal.get("order_id")
                or entry["time"] != signal.get("time")):
            return None
        return dict(entry)

    def record_entry(self, trade: Any, position: Any) -> None:
        if self._replaying:
            if trade.entry_id not in self._watched_ids:
                return
        elif self._live_time is None:
            return
        timestamp = int(trade.entry_time // 1000)
        if self._replaying and self._watch_since is not None and timestamp < self._watch_since:
            return
        if not self._replaying and timestamp < self._live_time:
            return
        direction = "long" if trade.size > 0 else "short"
        identity = (str(trade.entry_id), timestamp, direction)
        occurrence = self._occurrences[identity]
        self._occurrences[identity] += 1
        key = (*identity, occurrence)
        entry = {
            "entry_id": identity[0], "time": timestamp,
            "price": float(trade.entry_price), "size": float(trade.size),
            "direction": direction, "occurrence_index": occurrence,
            "lifecycle_start": int((position.position_open_time or trade.entry_time) // 1000),
        }
        if self._replaying:
            # Copy at the fill callback, before partial closes mutate the trade.
            self._replayed[key] = entry
        else:
            entry["evaluation_candle_time"] = self._live_time
            entry["hidden_fill"] = timestamp == self._hidden_fill_time
            self._last_entry = entry
            self._entries.setdefault(key, entry)
            self._watched_ids.add(identity[0])
            start = entry["lifecycle_start"]
            self._watch_since = min(start, self._watch_since) if self._watch_since is not None else start

    def finish_replay(self, candles: list, calculated_through: int) -> list[dict]:
        self._replaying = False
        if not self._entries or not candles:
            return []
        matched: set[tuple] = set()
        missing: list[tuple] = []
        for key, original in self._entries.items():
            expected_time = original["time"]
            if original["hidden_fill"]:
                # A live zero-volume fake open can fill an order, whereas the replay
                # fills it on the next visible candle. That alone is not repainting.
                index = bisect_left(candles, expected_time, key=lambda bar: int(bar.timestamp))
                if index == len(candles):
                    continue
                expected_time = int(candles[index].timestamp)
            if expected_time > calculated_through:
                continue
            replay_key = (key[0], expected_time, key[2], key[3])
            if replay_key in self._replayed:
                matched.add(replay_key)
            else:
                missing.append(key)

        def group(entry):
            return entry["entry_id"], entry["direction"], entry["lifecycle_start"]

        candidates_by_group = defaultdict(list)
        for replay_key, entry in self._replayed.items():
            if replay_key not in matched and entry["time"] <= calculated_through:
                candidates_by_group[group(entry)].append(entry)
        missing_counts = Counter(group(self._entries[key]) for key in missing)
        findings = []
        for key in missing:
            original = self._entries[key]
            candidates = candidates_by_group[group(original)]
            # Several pyramided entries can share an ID. Do not invent a mapping.
            peers = missing_counts[group(original)]
            replacement = candidates[0] if len(candidates) == 1 and peers == 1 else None
            change = "moved" if replacement is not None else "disappeared"
            fingerprint = (key, change, replacement["time"] if replacement else None)
            if fingerprint in self._reported:
                continue
            self._reported.add(fingerprint)
            findings.append({
                "type": "entry_repainting_detected",
                "change": change,
                "original": dict(original),
                "replacement": dict(replacement) if replacement else None,
                "calculated_through": calculated_through,
                "source_hashes": dict(self._scope[0]),
            })
        self._replayed.clear()
        return findings
