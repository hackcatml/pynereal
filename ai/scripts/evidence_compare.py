"""Build compact, source-neutral evidence comparisons for session evaluation."""

from __future__ import annotations

import copy
import math
import re
from typing import Any


PRICE_RELATIVE_TOLERANCE = 0.001
QUANTITY_SCALE_RELATIVE_TOLERANCE = 0.02
MAX_CURRENT_POSITION_EVENTS = 60
MAX_COMPARISON_EVENTS = 40
MAX_SOURCE_EXCERPTS = 3


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _relative_difference(left: Any, right: Any) -> float | None:
    a = _number(left)
    b = _number(right)
    if a is None or b is None:
        return None
    denominator = max(abs(a), abs(b))
    if denominator == 0:
        return 0.0
    return abs(a - b) / denominator


def _timestamp_seconds(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000
    return int(number)


def _timeframe_seconds(value: Any) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*([smhdwSMHDW])\s*", str(value or ""))
    if not match:
        return None
    amount = int(match.group(1))
    scale = {
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
        "w": 7 * 24 * 60 * 60,
    }[match.group(2).lower()]
    return amount * scale


def current_position_events(
    simulation: dict[str, Any] | None,
    events: list[dict[str, Any]] | None,
    *,
    limit: int = MAX_CURRENT_POSITION_EVENTS,
) -> dict[str, Any]:
    simulation = simulation if isinstance(simulation, dict) else {}
    position = simulation.get("position") or {}
    lifecycle = simulation.get("position_lifecycle") or {}
    direction = str(position.get("direction") or "flat").lower()
    opened_at_ms = lifecycle.get("opened_at_ms")
    opened_at_seconds = _timestamp_seconds(opened_at_ms)
    rows = [copy.deepcopy(row) for row in (events or []) if isinstance(row, dict)]

    if direction == "flat" or not (_number(position.get("size")) or 0):
        return {
            "status": "flat",
            "opened_at_ms": None,
            "coverage_start": None,
            "coverage_complete": True,
            "events": [],
            "truncated": False,
        }
    if opened_at_seconds is None:
        return {
            "status": "unavailable",
            "opened_at_ms": opened_at_ms,
            "coverage_start": None,
            "coverage_complete": False,
            "events": [],
            "truncated": False,
        }

    filtered = [
        row for row in rows
        if (_timestamp_seconds(row.get("time")) or 0) >= opened_at_seconds
    ]
    coverage_start = min(
        (_timestamp_seconds(row.get("time")) for row in filtered),
        default=None,
    )
    coverage_complete = coverage_start == opened_at_seconds
    truncated = len(filtered) > limit or not coverage_complete
    return {
        "status": "available" if filtered else "unavailable",
        "opened_at_ms": int(opened_at_seconds * 1000),
        "coverage_start": coverage_start,
        "coverage_complete": coverage_complete,
        "events": filtered[-limit:],
        "truncated": truncated,
    }


def _selected_account_position(context: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    account_match = context.get("account_match") or {}
    if account_match.get("status") != "matched":
        return None, str(account_match.get("status") or "unavailable")
    selected = account_match.get("selected") or {}
    positions = [
        item for item in (selected.get("positions") or [])
        if isinstance(item, dict)
    ]
    if len(positions) == 1:
        return positions[0], "matched"
    if not positions:
        return None, "no_open_position"

    simulation = ((context.get("strategy") or {}).get("simulation") or {})
    direction = str((simulation.get("position") or {}).get("direction") or "").lower()
    side_matches = [
        item for item in positions
        if str(item.get("side") or "").lower() == direction
    ]
    if len(side_matches) == 1:
        return side_matches[0], "matched"
    return None, "multiple_open_positions"


def build_evidence_summary(context: dict[str, Any]) -> dict[str, Any]:
    strategy = context.get("strategy") or {}
    simulation = strategy.get("simulation") or {}
    simulated = simulation.get("position") or {}
    simulated_direction = str(simulated.get("direction") or "flat").lower()
    simulated_size = abs(_number(simulated.get("size")) or 0.0)
    simulated_average = _number(
        simulated.get("aggregate_avg_price")
        if simulated.get("aggregate_avg_price") is not None
        else simulated.get("avg_price")
    )
    real, account_status = _selected_account_position(context)
    account_match = context.get("account_match") or {}
    selected = account_match.get("selected") or {}

    sources = {
        "strategy": {
            "direction": simulated_direction,
            "quantity": simulated_size,
            "average_price": simulated_average,
        },
        "account": {
            "status": account_status,
            "account": selected.get("account"),
            "exchange": selected.get("exchange"),
            "direction": str((real or {}).get("side") or "flat").lower(),
            "quantity": abs(_number((real or {}).get("quantity")) or 0.0),
            "average_price": _number((real or {}).get("entry_price")),
        },
    }
    differences: list[dict[str, Any]] = []
    simulated_open = simulated_direction in {"long", "short"} and simulated_size > 0
    account_open = real is not None and sources["account"]["quantity"] > 0

    if account_match.get("status") != "matched":
        return {
            "status": "unresolved",
            "sources": sources,
            "observed_differences": [],
            "diagnostic_recommended": False,
            "reason": f"account_match_{account_match.get('status') or 'unavailable'}",
        }

    if simulated_open != account_open:
        differences.append({
            "field": "position.presence",
            "strategy": simulated_open,
            "account": account_open,
            "material": True,
        })
    if simulated_open and account_open:
        real_direction = sources["account"]["direction"]
        if real_direction and real_direction != simulated_direction:
            differences.append({
                "field": "position.direction",
                "strategy": simulated_direction,
                "account": real_direction,
                "material": True,
            })

        quantity_difference = _relative_difference(
            simulated_size,
            sources["account"]["quantity"],
        )
        if quantity_difference is not None and quantity_difference > 1e-9:
            differences.append({
                "field": "position.quantity",
                "strategy": simulated_size,
                "account": sources["account"]["quantity"],
                "relative_difference": quantity_difference,
                # Absolute quantities can use different account capital. A quantity
                # difference is observable but requires event-scale comparison.
                "material": False,
            })

        average_difference = _relative_difference(
            simulated_average,
            sources["account"]["average_price"],
        )
        if average_difference is not None and average_difference > 1e-9:
            differences.append({
                "field": "position.average_price",
                "strategy": simulated_average,
                "account": sources["account"]["average_price"],
                "relative_difference": average_difference,
                "material": average_difference > PRICE_RELATIVE_TOLERANCE,
            })

    material = any(bool(item.get("material")) for item in differences)
    return {
        "status": "differences_observed" if differences else "consistent",
        "sources": sources,
        "observed_differences": differences,
        "diagnostic_recommended": material,
        "reason": "material_cross_source_difference" if material else None,
    }


def _group_strategy_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        event_type = str(row.get("type") or "")
        if event_type not in {"trade_entry", "trade_close"}:
            continue
        timestamp = _timestamp_seconds(row.get("time"))
        price = _number(row.get("price"))
        quantity = abs(_number(row.get("size")) or 0.0)
        if timestamp is None or price is None or quantity <= 0:
            continue
        action = "entry" if event_type == "trade_entry" else "reduction"
        intent = (
            str(row.get("id") or "") + "|" + str(row.get("comment") or "")
            if action == "entry"
            else str(row.get("exit_id") or row.get("comment") or "reduction")
        )
        key = (timestamp, action, intent, round(price, 12))
        item = by_key.get(key)
        if item is None:
            item = {
                "source": "strategy",
                "action": action,
                "time": timestamp,
                "price": price,
                "quantity": 0.0,
                "id": row.get("id"),
                "exit_id": row.get("exit_id"),
                "comment": row.get("comment"),
                "records": [],
            }
            by_key[key] = item
            grouped.append(item)
        item["quantity"] += quantity
        item["records"].append(copy.deepcopy(row))
    return sorted(grouped, key=lambda item: (item["time"], item["action"]))


def _account_action(order: dict[str, Any], strategy_direction: str) -> str:
    if order.get("reduce_only") is True:
        return "reduction"
    side = str(order.get("side") or "").lower()
    if strategy_direction == "long" and side == "sell":
        return "reduction"
    if strategy_direction == "short" and side == "buy":
        return "reduction"
    return "entry"


def _group_account_orders(
    rows: list[dict[str, Any]],
    strategy_direction: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        timestamp = _timestamp_seconds(row.get("timestamp") or row.get("last_trade_timestamp"))
        price = _number(row.get("average_price") or row.get("price"))
        quantity_value = (
            row.get("filled")
            if row.get("filled") is not None
            else row.get("amount")
        )
        quantity = abs(_number(quantity_value) or 0.0)
        if timestamp is None or price is None or quantity <= 0:
            continue
        normalized.append({
            "source": "account",
            "action": _account_action(row, strategy_direction),
            "time": timestamp,
            "price": price,
            "quantity": quantity,
            "side": row.get("side"),
            "order_ids": [row.get("id")],
            "records": [copy.deepcopy(row)],
        })
    normalized.sort(key=lambda item: (item["time"], item["action"]))

    grouped: list[dict[str, Any]] = []
    for item in normalized:
        previous = grouped[-1] if grouped else None
        can_merge = (
            previous is not None
            and previous["action"] == item["action"]
            and str(previous.get("side") or "") == str(item.get("side") or "")
            and item["time"] - previous["time"] <= 3
            and (_relative_difference(previous["price"], item["price"]) or 0.0) <= 0.005
        )
        if not can_merge:
            grouped.append(item)
            continue
        total = previous["quantity"] + item["quantity"]
        previous["price"] = (
            previous["price"] * previous["quantity"]
            + item["price"] * item["quantity"]
        ) / total
        previous["quantity"] = total
        previous["order_ids"].extend(item["order_ids"])
        previous["records"].extend(item["records"])
    return grouped


def _event_view(event: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "source", "action", "time", "price", "quantity", "side",
        "id", "exit_id", "comment", "order_ids",
    )
    return {key: copy.deepcopy(event.get(key)) for key in keys if event.get(key) is not None}


def _source_excerpts(
    source: dict[str, Any],
    strategy_event: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not strategy_event:
        return []
    content = str(source.get("content") or "")
    if not content:
        return []
    terms = []
    for value in (
        strategy_event.get("comment"),
        strategy_event.get("exit_id"),
        strategy_event.get("id"),
    ):
        term = str(value or "").strip()
        if len(term) >= 3 and term not in terms:
            terms.append(term)
    lines = content.splitlines()
    excerpts = []
    covered: list[tuple[int, int]] = []
    for term in terms:
        for index, line in enumerate(lines):
            if term not in line:
                continue
            start = max(0, index - 10)
            end = min(len(lines), index + 11)
            if any(start < old_end and end > old_start for old_start, old_end in covered):
                continue
            covered.append((start, end))
            excerpts.append({
                "source": source.get("name"),
                "matched_term": term,
                "start_line": start + 1,
                "end_line": end,
                "content": "\n".join(
                    f"{line_number + 1}: {lines[line_number]}"
                    for line_number in range(start, end)
                ),
            })
            if len(excerpts) >= MAX_SOURCE_EXCERPTS:
                return excerpts
    return excerpts


def compare_session_evidence(
    context: dict[str, Any],
    *,
    max_events: int = MAX_COMPARISON_EVENTS,
) -> dict[str, Any]:
    strategy = context.get("strategy") or {}
    simulation = strategy.get("simulation") or {}
    position = simulation.get("position") or {}
    event_summary = strategy.get("current_position_events") or current_position_events(
        simulation,
        strategy.get("recent_trade_events"),
    )
    strategy_rows = _group_strategy_events(event_summary.get("events") or [])[-max_events:]
    account_match = context.get("account_match") or {}
    selected = account_match.get("selected") or {}
    account_rows = _group_account_orders(
        [row for row in (selected.get("recent_orders") or []) if isinstance(row, dict)],
        str(position.get("direction") or "").lower(),
    )

    lifecycle_start = _timestamp_seconds(event_summary.get("opened_at_ms"))
    timeframe = _timeframe_seconds((context.get("session") or {}).get("timeframe"))
    match_window = max(120, min(900, timeframe or 300))
    if lifecycle_start is not None:
        account_rows = [
            row for row in account_rows
            if row["time"] >= lifecycle_start - match_window
        ]

    insufficient: list[str] = []
    if account_match.get("status") != "matched":
        insufficient.append("A single account was not matched for this session snapshot.")
    if not strategy_rows:
        insufficient.append("No current-position strategy events were available.")
    if event_summary.get("coverage_complete") is False:
        insufficient.append(
            "The retained strategy events do not cover the start of the current position."
        )
    if not account_rows:
        insufficient.append("No matching account orders were available for the position lifecycle.")
    elif (
        lifecycle_start is not None
        and min(row["time"] for row in account_rows) > lifecycle_start + match_window
    ):
        insufficient.append(
            "The available account orders do not cover the start of the current position."
        )

    used_accounts: set[int] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_strategy: list[dict[str, Any]] = []
    account_coverage_start = min((row["time"] for row in account_rows), default=None)
    for strategy_event in strategy_rows:
        candidates = [
            (index, account_event)
            for index, account_event in enumerate(account_rows)
            if index not in used_accounts
            and account_event["action"] == strategy_event["action"]
            and abs(account_event["time"] - strategy_event["time"]) <= match_window
        ]
        if not candidates:
            item = _event_view(strategy_event)
            if (
                account_coverage_start is not None
                and strategy_event["time"] < account_coverage_start - match_window
            ):
                item["coverage"] = "before_available_account_orders"
            unmatched_strategy.append(item)
            continue
        index, account_event = min(
            candidates,
            key=lambda pair: (
                abs(pair[1]["time"] - strategy_event["time"]),
                _relative_difference(pair[1]["price"], strategy_event["price"])
                if _relative_difference(pair[1]["price"], strategy_event["price"]) is not None
                else math.inf,
            ),
        )
        used_accounts.add(index)
        pairs.append((strategy_event, account_event))

    unmatched_account = [
        _event_view(row) for index, row in enumerate(account_rows)
        if index not in used_accounts
    ]
    for item in unmatched_account:
        item["classification"] = "account_only_execution"
        item["cause_confirmed"] = False
        item["possible_explanations"] = [
            "manual_or_external_account_execution",
            "missing_or_incomplete_strategy_event_history",
            "execution_outside_the_event_matching_window",
        ]
        item["confirmation_needed"] = (
            "Confirm the order origin or inspect the corresponding alert/webhook "
            "record before attributing this execution to the strategy."
        )
    baseline_pair = next(
        (
            pair for pair in pairs
            if pair[0]["action"] == "entry"
            and pair[0]["quantity"] > 0
            and pair[1]["quantity"] > 0
        ),
        None,
    )
    baseline_scale = (
        baseline_pair[1]["quantity"] / baseline_pair[0]["quantity"]
        if baseline_pair else None
    )
    if baseline_scale is None:
        insufficient.append("A matched entry was not available to establish quantity scale.")

    matched_events: list[dict[str, Any]] = []
    divergences: list[dict[str, Any]] = []
    for strategy_event, account_event in pairs:
        price_difference = _relative_difference(
            strategy_event["price"], account_event["price"]
        )
        quantity_scale = account_event["quantity"] / strategy_event["quantity"]
        scale_drift = (
            _relative_difference(quantity_scale, baseline_scale)
            if baseline_scale is not None else None
        )
        differences = []
        if price_difference is not None and price_difference > PRICE_RELATIVE_TOLERANCE:
            differences.append({
                "field": "event.price",
                "relative_difference": price_difference,
                "material": True,
            })
        if scale_drift is not None and scale_drift > QUANTITY_SCALE_RELATIVE_TOLERANCE:
            differences.append({
                "field": "event.quantity_scale",
                "relative_difference": scale_drift,
                "material": True,
            })
        item = {
            "time": strategy_event["time"],
            "action": strategy_event["action"],
            "time_difference_seconds": account_event["time"] - strategy_event["time"],
            "strategy": _event_view(strategy_event),
            "account": _event_view(account_event),
            "price_relative_difference": price_difference,
            "quantity_scale": quantity_scale,
            "quantity_scale_relative_difference": scale_drift,
            "differences": differences,
        }
        matched_events.append(item)
        if differences:
            divergences.append({
                "time": strategy_event["time"],
                "kind": "matched_event_difference",
                "comparison": item,
                "strategy_event": strategy_event,
            })

    for item in unmatched_strategy:
        if item.get("coverage") == "before_available_account_orders":
            continue
        divergences.append({
            "time": item.get("time") or 0,
            "kind": "unmatched_strategy_event",
            "strategy": item,
            "strategy_event": item,
        })
    for item in unmatched_account:
        divergences.append({
            "time": item.get("time") or 0,
            "kind": "unmatched_account_event",
            "account": item,
            "strategy_event": None,
        })
    divergences.sort(key=lambda item: (item.get("time") or 0, item["kind"]))
    first = divergences[0] if divergences else None
    public_first = None
    if first:
        public_first = {
            key: copy.deepcopy(value)
            for key, value in first.items()
            if key != "strategy_event"
        }
    observed_differences = [
        {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if key != "strategy_event"
        }
        for item in divergences
    ]
    first_is_account_only = bool(
        public_first
        and public_first.get("kind") == "unmatched_account_event"
        and (public_first.get("account") or {}).get("classification")
        == "account_only_execution"
    )

    return {
        "status": "compared" if pairs else "insufficient_evidence",
        "session": copy.deepcopy(context.get("session") or {}),
        "generation_id": (context.get("calculation") or {}).get("generation_id"),
        "account": {
            "status": account_match.get("status"),
            "account": selected.get("account"),
            "exchange": selected.get("exchange"),
        },
        "snapshot": build_evidence_summary(context),
        "comparison_scope": {
            "position_lifecycle_opened_at_ms": event_summary.get("opened_at_ms"),
            "strategy_event_count": len(strategy_rows),
            "account_event_count": len(account_rows),
            "match_window_seconds": match_window,
            "price_relative_tolerance": PRICE_RELATIVE_TOLERANCE,
            "quantity_scale_relative_tolerance": QUANTITY_SCALE_RELATIVE_TOLERANCE,
        },
        "baseline_quantity_scale": baseline_scale,
        "matched_events": matched_events,
        "observed_differences": observed_differences,
        "unmatched_strategy_events": unmatched_strategy,
        "unmatched_account_events": unmatched_account,
        "first_divergence": public_first,
        "source_excerpts": _source_excerpts(
            strategy.get("source") or {},
            first.get("strategy_event") if first else None,
        ),
        "insufficient_evidence": list(dict.fromkeys(insufficient)),
        "investigation_constraints": {
            "cause_status": "unresolved" if first_is_account_only else "not_determined",
            "strategy_source_attribution_allowed": not first_is_account_only,
            "next_required_evidence": (
                "order_origin_or_corresponding_alert_webhook_record"
                if first_is_account_only else None
            ),
            "rule": (
                "Stop strategy-side root-cause attribution when the earliest "
                "divergence is an account-only execution with unknown origin."
                if first_is_account_only else None
            ),
        },
        "interpretation_rule": (
            "This comparison reports observations and the earliest divergence only. "
            "Treat a cause as confirmed only after the event difference is reproduced "
            "or supported by source execution logic; otherwise report it as a hypothesis. "
            "An account_only_execution proves only that no strategy event was matched "
            "in the available snapshot. It may be a manual or external order and must "
            "not be attributed to the strategy, webhook, or engine without confirming "
            "the order origin."
        ),
    }


def compact_session_context(context: dict[str, Any]) -> dict[str, Any]:
    strategy = context.get("strategy") or {}
    simulation = strategy.get("simulation") or {}
    compact_simulation_keys = (
        "position", "pnl", "trade_counts", "open_trades", "open_trade_ledger",
        "entry_open_ledger", "position_lifecycle", "accounting", "consistency",
        "recent_closed_trades", "active_orders", "risk", "statistics", "current_bar",
    )
    compact_simulation = {
        key: copy.deepcopy(simulation.get(key))
        for key in compact_simulation_keys
        if key in simulation
    }
    market = context.get("market") or {}
    account_match = context.get("account_match") or {}
    selected = account_match.get("selected") or {}
    candidates = []
    for item in account_match.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append({
            "account": item.get("account"),
            "exchange": item.get("exchange"),
            "score": item.get("score"),
            "reasons": copy.deepcopy(item.get("reasons") or []),
            "position_count": len(item.get("positions") or []),
            "recent_order_count": len(item.get("recent_orders") or []),
            "recent_position_count": len(item.get("recent_positions") or []),
            "collection_status": copy.deepcopy(item.get("collection_status") or {}),
        })
    compact_account_match = {
        "status": account_match.get("status"),
        "confidence": account_match.get("confidence"),
        "selection_mode": account_match.get("selection_mode"),
        "selected": {
            "account": selected.get("account"),
            "exchange": selected.get("exchange"),
            "score": selected.get("score"),
            "reasons": copy.deepcopy(selected.get("reasons") or []),
            "positions": copy.deepcopy(selected.get("positions") or []),
            "recent_orders": copy.deepcopy((selected.get("recent_orders") or [])[:20]),
            "recent_positions": copy.deepcopy(
                (selected.get("recent_positions") or [])[:10]
            ),
            "collection_status": copy.deepcopy(selected.get("collection_status") or {}),
        } if selected else None,
        "candidates": candidates,
        "collection": copy.deepcopy(account_match.get("collection") or {}),
        "rule": account_match.get("rule"),
    }

    def compact_series(rows: Any) -> list[dict[str, Any]]:
        result = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            copy_item = {key: copy.deepcopy(value) for key, value in item.items() if key != "points"}
            copy_item["points"] = copy.deepcopy((item.get("points") or [])[-10:])
            result.append(copy_item)
        return result

    return {
        "schema_version": context.get("schema_version"),
        "collected_at": context.get("collected_at"),
        "detail_level": "compact",
        "session": copy.deepcopy(context.get("session") or {}),
        "calculation": copy.deepcopy(context.get("calculation") or {}),
        "market": {
            "confirmed_bars": copy.deepcopy((market.get("confirmed_bars") or [])[-20:]),
            "forming_bar": copy.deepcopy(market.get("forming_bar")),
            "quality": copy.deepcopy(market.get("quality") or {}),
        },
        "strategy": {
            "source": {
                "name": (strategy.get("source") or {}).get("name"),
                "available": bool((strategy.get("source") or {}).get("content")),
                "truncated": bool((strategy.get("source") or {}).get("truncated")),
            },
            "source_hashes": copy.deepcopy(strategy.get("source_hashes") or []),
            "config": copy.deepcopy(strategy.get("config") or {}),
            "inputs": copy.deepcopy(strategy.get("inputs") or []),
            "simulation": compact_simulation,
            "current_position_events": copy.deepcopy(
                strategy.get("current_position_events") or {}
            ),
            "plots": compact_series(strategy.get("plots")),
            "plotchars": copy.deepcopy((strategy.get("plotchars") or [])[-20:]),
            "backgrounds": compact_series(strategy.get("backgrounds")),
            "recent_logs": str(strategy.get("recent_logs") or "")[-4_000:],
        },
        "account_match": compact_account_match,
        "evidence_summary": copy.deepcopy(context.get("evidence_summary") or {}),
        "warnings": copy.deepcopy(context.get("warnings") or []),
    }


def comparison_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep only the same-turn evidence required by the comparison tool."""
    strategy = context.get("strategy") or {}
    simulation = strategy.get("simulation") or {}
    account_match = context.get("account_match") or {}
    selected = account_match.get("selected") or {}
    return {
        "session": copy.deepcopy(context.get("session") or {}),
        "calculation": {
            "generation_id": (context.get("calculation") or {}).get("generation_id"),
        },
        "strategy": {
            "source": copy.deepcopy(strategy.get("source") or {}),
            "simulation": {
                "position": copy.deepcopy(simulation.get("position") or {}),
                "position_lifecycle": copy.deepcopy(
                    simulation.get("position_lifecycle") or {}
                ),
            },
            "current_position_events": copy.deepcopy(
                strategy.get("current_position_events") or {}
            ),
        },
        "account_match": {
            "status": account_match.get("status"),
            "confidence": account_match.get("confidence"),
            "selection_mode": account_match.get("selection_mode"),
            "selected": {
                "account": selected.get("account"),
                "exchange": selected.get("exchange"),
                "positions": copy.deepcopy(selected.get("positions") or []),
                "recent_orders": copy.deepcopy(selected.get("recent_orders") or []),
            } if selected else None,
        },
        "evidence_summary": copy.deepcopy(context.get("evidence_summary") or {}),
    }
