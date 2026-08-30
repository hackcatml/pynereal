from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any


_TRADE_INTENT_TYPES = {"trade_entry", "trade_close"}


def intent_key(intent: Any) -> str:
    return json.dumps(
        intent,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def intent_identity(intent: Any) -> Any:
    if not isinstance(intent, dict) or intent.get("type") not in _TRADE_INTENT_TYPES:
        return intent
    return {
        "type": intent.get("type"),
        "time": intent.get("time"),
        "size": intent.get("size"),
        "id": intent.get("id") or "",
        "exit_id": intent.get("exit_id") or "",
        "comment": intent.get("comment") or "",
        "occurrence_index": intent.get("occurrence_index", 0),
    }


def intent_identity_key(intent: Any) -> str:
    return intent_key(intent_identity(intent))


def index_intents(intents: list[Any]) -> dict[str, list[Any]]:
    indexed: dict[str, list[Any]] = defaultdict(list)
    for intent in intents:
        indexed[intent_identity_key(intent)].append(intent)
    return dict(indexed)


def expand_intent_difference(
    difference: Counter[str],
    indexed: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for encoded, count in sorted(difference.items()):
        values = indexed.get(encoded) or []
        for value in values[:count]:
            expanded.append(value if isinstance(value, dict) else {"raw": value})
        if len(values) >= count:
            continue
        try:
            fallback = json.loads(encoded)
        except json.JSONDecodeError:
            fallback = {"raw": encoded}
        if not isinstance(fallback, dict):
            fallback = {"raw": fallback}
        expanded.extend([fallback] * (count - len(values)))
    return expanded


def intent_detail_differences(
    primary_index: dict[str, list[Any]],
    finalized_index: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for identity_key in sorted(primary_index.keys() & finalized_index.keys()):
        primary_values = primary_index[identity_key]
        finalized_values = finalized_index[identity_key]
        for primary, finalized in zip(primary_values, finalized_values):
            if primary == finalized:
                continue
            if not isinstance(primary, dict) or not isinstance(finalized, dict):
                continue
            fields = {}
            for field in sorted(primary.keys() | finalized.keys()):
                primary_value = primary.get(field)
                finalized_value = finalized.get(field)
                if primary_value != finalized_value:
                    fields[field] = {
                        "primary": primary_value,
                        "finalized": finalized_value,
                    }
            if fields:
                differences.append({
                    "identity": intent_identity(primary),
                    "fields": fields,
                })
    return differences


def candle_difference(
    primary: Any,
    finalized: Any,
) -> dict[str, dict[str, Any]]:
    fields = ("timestamp_ms", "open", "high", "low", "close", "volume")
    if not isinstance(primary, (list, tuple)):
        primary = []
    if not isinstance(finalized, (list, tuple)):
        finalized = []
    difference: dict[str, dict[str, Any]] = {}
    for index, field in enumerate(fields):
        primary_value = primary[index] if index < len(primary) else None
        finalized_value = finalized[index] if index < len(finalized) else None
        if primary_value != finalized_value:
            difference[field] = {
                "primary": primary_value,
                "finalized": finalized_value,
            }
    return difference


def compare_results(
    primary: dict[str, Any],
    finalized: dict[str, Any],
    *,
    generation_id: str,
    timestamp_ms: int,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    primary_intents = list(primary.get("intents") or [])
    finalized_intents = list(finalized.get("intents") or [])
    primary_index = index_intents(primary_intents)
    finalized_index = index_intents(finalized_intents)
    primary_counter = Counter({key: len(values) for key, values in primary_index.items()})
    finalized_counter = Counter({key: len(values) for key, values in finalized_index.items()})
    detail_differences = intent_detail_differences(primary_index, finalized_index)
    primary_plot = primary.get("plot_values") or {}
    finalized_plot = finalized.get("plot_values") or {}
    primary_hashes = primary.get("source_hashes") or {}
    finalized_hashes = finalized.get("source_hashes") or {}
    primary_bar = primary.get("confirmed_bar")
    finalized_bar = finalized.get("confirmed_bar")
    bar_difference = candle_difference(primary_bar, finalized_bar)
    fingerprint = (
        generation_id,
        timestamp_ms,
        tuple(sorted(primary_counter.items())),
        tuple(sorted(finalized_counter.items())),
        tuple(sorted(intent_key(item) for item in primary_intents)),
        tuple(sorted(intent_key(item) for item in finalized_intents)),
        intent_key(primary_plot),
        intent_key(finalized_plot),
        intent_key(primary_hashes),
        intent_key(finalized_hashes),
        intent_key(primary_bar),
        intent_key(finalized_bar),
        primary.get("result_status"),
        primary.get("reason"),
    )
    missing_from_primary = finalized_counter - primary_counter
    primary_only = primary_counter - finalized_counter
    primary_result_available = primary.get("result_status") != "not_calculated"
    comparison = {
        "event": "verification_result_comparison",
        "generation_id": generation_id,
        "candle_timestamp_ms": timestamp_ms,
        "matched": (
            primary_result_available
            and not missing_from_primary
            and not primary_only
            and not detail_differences
            and primary_plot == finalized_plot
            and primary_hashes == finalized_hashes
            and not bar_difference
        ),
        "missing_from_primary": expand_intent_difference(
            missing_from_primary,
            finalized_index,
        ),
        "primary_only": expand_intent_difference(primary_only, primary_index),
        "signal_intents_matched": not missing_from_primary and not primary_only,
        "intent_details_matched": not detail_differences,
        "intent_detail_differences": detail_differences,
        "plot_values_matched": primary_plot == finalized_plot,
        "primary_plot_values": primary_plot,
        "finalized_plot_values": finalized_plot,
        "source_hashes_matched": primary_hashes == finalized_hashes,
        "primary_source_hashes": primary_hashes,
        "finalized_source_hashes": finalized_hashes,
        "confirmed_bars_matched": not bar_difference,
        "primary_confirmed_bar": primary_bar,
        "finalized_confirmed_bar": finalized_bar,
        "confirmed_bar_difference": bar_difference,
        "primary_result_status": primary.get("result_status") or "calculated",
        "primary_result_reason": primary.get("reason"),
        "primary_result_available": primary_result_available,
        "supplemental_delivery_enabled": False,
    }
    return fingerprint, comparison
