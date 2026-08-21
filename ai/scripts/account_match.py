"""Match one strategy session to configured accounts using read-only evidence."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any


MAX_RECENT_MATCHED_ORDERS = 50


def canonical_symbol(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def resolve_account_hint(hint: str, account_names: list[str]) -> str:
    normalized_hint = re.sub(r"[^a-z0-9]+", "_", hint.strip().lower()).strip("_")
    for marker in ("account_", "profile_"):
        normalized_hint = normalized_hint.removeprefix(marker)
    for marker in ("_account", "_profile"):
        normalized_hint = normalized_hint.removesuffix(marker)
    normalized_names = {
        re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"): name
        for name in account_names
    }
    exact = normalized_names.get(normalized_hint)
    if exact:
        return exact

    compact_hint = normalized_hint.replace("_", "")
    compact_matches = [
        name for normalized, name in normalized_names.items()
        if normalized.replace("_", "") == compact_hint
    ]
    if len(compact_matches) == 1:
        return compact_matches[0]
    available = ", ".join(account_names) or "none"
    raise ValueError(
        f"unknown or ambiguous configured account {hint!r}; available accounts: {available}"
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _relative_distance(left: Any, right: Any) -> float | None:
    a = _number(left)
    b = _number(right)
    if a is None or b is None or a == 0 or b == 0:
        return None
    return abs(a - b) / max(abs(a), abs(b))


def _timestamp_milliseconds(value: Any) -> int | None:
    number = _number(value)
    if number is not None and number > 0:
        if number < 10_000_000_000:
            number *= 1000
        return int(number)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    return None


def _simulation_values(
    context: dict[str, Any],
) -> tuple[str, float | None, float | None, list[int]]:
    simulation = ((context.get("strategy") or {}).get("simulation") or {})
    position = simulation.get("position") or {}
    entry_times = [
        timestamp
        for trade in (simulation.get("open_trades") or [])
        if isinstance(trade, dict)
        for timestamp in [_timestamp_milliseconds(trade.get("entry_time"))]
        if timestamp is not None
    ]
    size = _number(position.get("size"))
    return (
        str(position.get("direction") or "flat").lower(),
        abs(size) if size is not None else None,
        _number(position.get("avg_price")),
        entry_times,
    )


def _account_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("results")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def match_session_account(
    context: dict[str, Any],
    positions_payload: dict[str, Any],
    orders_payload: dict[str, Any],
    position_history_payload: dict[str, Any] | None = None,
    *,
    explicit_account: str | None = None,
) -> dict[str, Any]:
    session = context.get("session") or {}
    target_exchange = str(session.get("exchange") or "").lower()
    target_symbol = canonical_symbol(session.get("symbol"))
    (
        simulation_side,
        simulation_size,
        simulation_entry,
        simulation_entry_times,
    ) = _simulation_values(context)

    candidates: dict[str, dict[str, Any]] = {}

    def candidate(account: str, exchange: str) -> dict[str, Any]:
        item = candidates.setdefault(account, {
            "account": account,
            "exchange": exchange,
            "score": 0,
            "reasons": [],
            "positions": [],
            "recent_orders": [],
            "recent_positions": [],
            "collection_status": {},
        })
        if exchange == target_exchange and "same_exchange" not in item["reasons"]:
            item["score"] += 40
            item["reasons"].append("same_exchange")
        return item

    for row in _account_rows(positions_payload):
        account = str(row.get("account") or "")
        exchange = str(row.get("exchange") or "").lower()
        if not account:
            continue
        item = candidate(account, exchange)
        item["collection_status"]["positions"] = row.get("status")
        for position in row.get("positions") or []:
            if not isinstance(position, dict):
                continue
            if canonical_symbol(position.get("symbol")) != target_symbol:
                continue
            item["positions"].append(position)
            item["score"] += 1000
            if "open_position_same_symbol" not in item["reasons"]:
                item["reasons"].append("open_position_same_symbol")

            real_side = str(position.get("side") or "").lower()
            if simulation_side in {"long", "short"} and real_side:
                if real_side == simulation_side:
                    item["score"] += 100
                    item["reasons"].append("position_side_matches_simulation")
                else:
                    item["score"] -= 150
                    item["reasons"].append("position_side_conflicts_with_simulation")

            entry_distance = _relative_distance(
                position.get("entry_price"), simulation_entry
            )
            if entry_distance is not None:
                if entry_distance <= 0.001:
                    item["score"] += 100
                    item["reasons"].append("entry_price_within_0.1_percent")
                elif entry_distance <= 0.01:
                    item["score"] += 70
                    item["reasons"].append("entry_price_within_1_percent")
                elif entry_distance <= 0.03:
                    item["score"] += 40
                    item["reasons"].append("entry_price_within_3_percent")

            quantity_distance = _relative_distance(
                abs(_number(position.get("quantity")) or 0), simulation_size
            )
            if quantity_distance is not None:
                if quantity_distance <= 0.02:
                    item["score"] += 80
                    item["reasons"].append("position_size_within_2_percent")
                elif quantity_distance <= 0.1:
                    item["score"] += 50
                    item["reasons"].append("position_size_within_10_percent")

    for row in _account_rows(orders_payload):
        account = str(row.get("account") or "")
        exchange = str(row.get("exchange") or "").lower()
        if not account:
            continue
        item = candidate(account, exchange)
        item["collection_status"]["orders"] = row.get("status")
        matching_orders = [
            order for order in (row.get("orders") or [])
            if isinstance(order, dict)
            and canonical_symbol(order.get("symbol")) == target_symbol
        ]
        if matching_orders:
            item["recent_orders"].extend(
                matching_orders[:MAX_RECENT_MATCHED_ORDERS]
            )
            item["score"] += 300 + min(len(matching_orders), 10) * 20
            item["reasons"].append("recent_orders_same_symbol")
            if any(order.get("status") == "open" for order in matching_orders):
                item["score"] += 80
                item["reasons"].append("open_order_same_symbol")

            entry_orders = [
                order for order in matching_orders
                if order.get("reduce_only") is not True
            ]
            expected_side = (
                "buy" if simulation_side == "long"
                else "sell" if simulation_side == "short"
                else None
            )
            if expected_side and any(
                str(order.get("side") or "").lower() == expected_side
                for order in entry_orders
            ):
                item["score"] += 70
                item["reasons"].append("order_side_matches_simulation")

            order_price_distances = [
                distance
                for order in entry_orders
                for distance in [_relative_distance(
                    order.get("average_price") or order.get("price"),
                    simulation_entry,
                )]
                if distance is not None
            ]
            if order_price_distances:
                closest_price = min(order_price_distances)
                if closest_price <= 0.001:
                    item["score"] += 100
                    item["reasons"].append("order_price_within_0.1_percent")
                elif closest_price <= 0.01:
                    item["score"] += 70
                    item["reasons"].append("order_price_within_1_percent")
                elif closest_price <= 0.03:
                    item["score"] += 40
                    item["reasons"].append("order_price_within_3_percent")

            order_times = [
                timestamp
                for order in entry_orders
                for timestamp in [_timestamp_milliseconds(order.get("timestamp"))]
                if timestamp is not None
            ]
            if simulation_entry_times and order_times:
                closest_time = min(
                    abs(order_time - entry_time)
                    for order_time in order_times
                    for entry_time in simulation_entry_times
                )
                if closest_time <= 2 * 60 * 1000:
                    item["score"] += 120
                    item["reasons"].append("order_time_within_2_minutes")
                elif closest_time <= 15 * 60 * 1000:
                    item["score"] += 100
                    item["reasons"].append("order_time_within_15_minutes")
                elif closest_time <= 4 * 60 * 60 * 1000:
                    item["score"] += 60
                    item["reasons"].append("order_time_within_4_hours")
                elif closest_time <= 24 * 60 * 60 * 1000:
                    item["score"] += 30
                    item["reasons"].append("order_time_within_1_day")

    for row in _account_rows(position_history_payload or {}):
        account = str(row.get("account") or "")
        exchange = str(row.get("exchange") or "").lower()
        if not account:
            continue
        item = candidate(account, exchange)
        item["collection_status"]["position_history"] = row.get("status")
        matching_positions = [
            position for position in (row.get("positions") or [])
            if isinstance(position, dict)
            and canonical_symbol(position.get("symbol")) == target_symbol
        ]
        if not matching_positions:
            continue
        item["recent_positions"].extend(matching_positions[:20])
        item["score"] += 180 + min(len(matching_positions), 5) * 20
        item["reasons"].append("position_history_same_symbol")

        historical_sides = {
            str(position.get("side") or "").lower()
            for position in matching_positions
        }
        if simulation_side in {"long", "short"} and simulation_side in historical_sides:
            item["score"] += 40
            item["reasons"].append("position_history_side_matches_simulation")

    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            int(item["score"]),
            len(item["positions"]),
            len(item["recent_orders"]),
            len(item["recent_positions"]),
            item["account"],
        ),
        reverse=True,
    )
    evidence_candidates = [
        item for item in ranked
        if item["positions"] or item["recent_orders"] or item["recent_positions"]
    ]
    selected = evidence_candidates[0] if evidence_candidates else None
    status = "no_match"
    confidence = "none"
    selection_mode = "automatic"
    if explicit_account is not None:
        selection_mode = "explicit"
        selected = candidates.get(explicit_account)
        if selected is None:
            selected = {
                "account": explicit_account,
                "exchange": None,
                "score": 0,
                "reasons": ["user_selected_account"],
                "positions": [],
                "recent_orders": [],
                "recent_positions": [],
                "collection_status": {},
            }
        elif "user_selected_account" not in selected["reasons"]:
            selected["reasons"].insert(0, "user_selected_account")
        status = "matched"
        confidence = "user_selected"
    elif selected is not None:
        second = evidence_candidates[1] if len(evidence_candidates) > 1 else None
        if second is not None and int(selected["score"]) - int(second["score"]) < 100:
            status = "ambiguous"
            confidence = "low"
            selected = None
        else:
            status = "matched"
            top_score = int(selected["score"])
            confidence = "high" if top_score >= 1000 else "medium"

    collection_errors = []
    for source, payload in (
        ("positions", positions_payload),
        ("orders", orders_payload),
        ("position_history", position_history_payload or {}),
    ):
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if isinstance(summary, dict) and int(summary.get("failed") or 0) > 0:
            collection_errors.append({
                "source": source,
                "failed": int(summary.get("failed") or 0),
                "requested": int(summary.get("requested") or 0),
            })
        if isinstance(payload, dict) and payload.get("error"):
            collection_errors.append({"source": source, "fatal": True})

    return {
        "status": status,
        "confidence": confidence,
        "selection_mode": selection_mode,
        "selected": selected,
        "candidates": evidence_candidates[:5],
        "collection": {
            "positions_collected_at": positions_payload.get("collected_at"),
            "orders_collected_at": orders_payload.get("collected_at"),
            "position_history_collected_at": (
                (position_history_payload or {}).get("collected_at")
            ),
            "errors": collection_errors,
        },
        "rule": (
            "No static account binding is used. Matching requires same-symbol current "
            "position, order, or position-history evidence from the session exchange; "
            "side, entry price, size, and order time refine ranking. An explicitly "
            "selected account always takes precedence."
        ),
    }
