from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Protocol


ONE_MINUTE_SLOTS = (10, 15, 20, 25, 30)
FIVE_MINUTE_SLOTS = (15, 35, 55, 75, 95, 115, 135, 150)
STANDARD_SLOT_INTERVAL_SECONDS = 20


class SchedulableSession(Protocol):
    id: str
    timeframe: str
    prerun_mode: str
    prerun_offset_seconds: int | None


@dataclass(frozen=True)
class PrerunAssignment:
    session_id: str
    offset_seconds: int
    duplicate: bool


@lru_cache(maxsize=None)
def timeframe_seconds(timeframe: str) -> int:
    value = str(timeframe or "").strip()
    if len(value) < 2 or not value[:-1].isdigit():
        return 60
    number = int(value[:-1])
    unit = value[-1]
    if unit == "M":
        return number * 30 * 86400
    multiplier = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }.get(unit.lower())
    return number * multiplier if multiplier is not None else 60


@lru_cache(maxsize=None)
def scheduling_window(timeframe: str) -> tuple[int, int, int] | None:
    value = str(timeframe or "").strip()
    if value == "1m":
        return ONE_MINUTE_SLOTS[0], ONE_MINUTE_SLOTS[-1], 5
    if value == "5m":
        return FIVE_MINUTE_SLOTS[0], FIVE_MINUTE_SLOTS[-1], STANDARD_SLOT_INTERVAL_SECONDS

    duration = timeframe_seconds(value)
    if duration > 5 * 60:
        return 15, duration - 5 * 60, STANDARD_SLOT_INTERVAL_SECONDS
    return None


@lru_cache(maxsize=None)
def candidate_slots(timeframe: str) -> tuple[int, ...]:
    value = str(timeframe or "").strip()
    if value == "1m":
        return ONE_MINUTE_SLOTS
    if value == "5m":
        return FIVE_MINUTE_SLOTS

    window = scheduling_window(value)
    if window is None:
        return ()
    start, end, interval = window
    slots = list(range(start, end + 1, interval))
    if slots[-1] != end:
        slots.append(end)
    return tuple(slots)


def default_offset_seconds(timeframe: str) -> int:
    slots = candidate_slots(timeframe)
    if slots:
        return slots[0]
    return max(1, timeframe_seconds(timeframe) // 2)


def validate_prerun_schedule(
    timeframe: str,
    mode: object,
    offset_seconds: object = None,
) -> tuple[str, int | None]:
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode not in {"auto", "custom"}:
        raise ValueError("pre-run timing must be auto or custom")
    if normalized_mode == "auto":
        return "auto", None

    window = scheduling_window(timeframe)
    if window is None:
        raise ValueError(
            "custom warm-up timing is supported for 1m, 5m, and longer sessions"
        )
    try:
        offset = int(offset_seconds)
    except (TypeError, ValueError):
        raise ValueError("custom warm-up time must be expressed in whole seconds")
    minimum, maximum, _ = window
    if offset < minimum or offset > maximum:
        raise ValueError(
            f"custom warm-up time for {timeframe} must be between "
            f"{minimum} and {maximum} seconds"
        )
    return "custom", offset


def _nearest_distance(slot: int, occupied: list[int]) -> int:
    if not occupied:
        return 10_000_000
    index = bisect_left(occupied, slot)
    distances = []
    if index < len(occupied):
        distances.append(abs(occupied[index] - slot))
    if index > 0:
        distances.append(abs(slot - occupied[index - 1]))
    return min(distances)


def _shorter_timeframe_starts(
    duration: int,
    sessions: list[SchedulableSession],
    assignments: dict[str, PrerunAssignment],
) -> list[int]:
    starts: set[int] = set()
    for session in sessions:
        interval = timeframe_seconds(session.timeframe)
        if interval >= duration:
            continue
        offset = assignments[session.id].offset_seconds
        starts.update(range(offset, duration, interval))
    return sorted(starts)


def assign_prerun_offsets(
    sessions: Iterable[SchedulableSession],
) -> dict[str, PrerunAssignment]:
    ordered = list(sessions)
    assignments: dict[str, PrerunAssignment] = {}
    schedulable: dict[int, list[SchedulableSession]] = {}

    for session in ordered:
        if candidate_slots(session.timeframe):
            duration = timeframe_seconds(session.timeframe)
            schedulable.setdefault(duration, []).append(session)

    shorter_sessions: list[SchedulableSession] = []
    for duration in sorted(schedulable):
        group = schedulable[duration]
        slots = candidate_slots(group[0].timeframe)
        counts: Counter[int] = Counter()

        for session in group:
            if session.prerun_mode != "custom" or session.prerun_offset_seconds is None:
                continue
            offset = int(session.prerun_offset_seconds)
            counts[offset] += 1
            assignments[session.id] = PrerunAssignment(
                session_id=session.id,
                offset_seconds=offset,
                duplicate=counts[offset] > 1,
            )

        occupied = _shorter_timeframe_starts(
            duration,
            shorter_sessions,
            assignments,
        )
        occupied_set = set(occupied)
        for session in group:
            if session.id in assignments:
                continue
            offset = min(
                slots,
                key=lambda slot: (
                    counts[slot],
                    slot in occupied_set,
                    -_nearest_distance(slot, occupied),
                    slot,
                ),
            )
            counts[offset] += 1
            assignments[session.id] = PrerunAssignment(
                session_id=session.id,
                offset_seconds=offset,
                duplicate=counts[offset] > 1,
            )
        shorter_sessions.extend(group)

    for session in ordered:
        if session.id in assignments:
            continue
        offset = default_offset_seconds(session.timeframe)
        assignments[session.id] = PrerunAssignment(
            session_id=session.id,
            offset_seconds=offset,
            duplicate=False,
        )

    return assignments
