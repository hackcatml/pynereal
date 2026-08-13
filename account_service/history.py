from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _derived_id(prefix: str, values: list[Any]) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _fee(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "currency": value.get("currency"),
        "cost": _number(value.get("cost")),
        "rate": _number(value.get("rate")),
    }


def _updated_timestamp(order: dict[str, Any]) -> int | None:
    for value in (
        order.get("lastTradeTimestamp"),
        order.get("lastUpdateTimestamp"),
        order.get("updateTimestamp"),
    ):
        timestamp = _integer(value)
        if timestamp is not None:
            return timestamp

    info = order.get("info")
    if isinstance(info, dict):
        for key in (
            "updateTime",
            "updatedTime",
            "uTime",
            "statusTimestamp",
            "cTime",
            "timestamp",
        ):
            timestamp = _integer(info.get(key))
            if timestamp is not None:
                return timestamp
    return _integer(order.get("timestamp"))


def normalize_order(order: dict[str, Any]) -> dict[str, Any]:
    order_id = str(order.get("id") or "").strip()
    client_order_id = str(order.get("clientOrderId") or "").strip()
    timestamp = _integer(order.get("timestamp"))
    symbol = str(order.get("symbol") or "")
    if not order_id:
        order_id = (
            f"client:{client_order_id}"
            if client_order_id
            else _derived_id(
                "order",
                [
                    symbol,
                    timestamp,
                    order.get("side"),
                    order.get("type"),
                    order.get("amount"),
                    order.get("price"),
                ],
            )
        )
    return {
        "id": order_id,
        "client_order_id": client_order_id,
        "symbol": symbol,
        "type": order.get("type"),
        "side": order.get("side"),
        "status": order.get("status"),
        "time_in_force": order.get("timeInForce"),
        "price": _number(order.get("price")),
        "average_price": _number(order.get("average")),
        "trigger_price": _number(order.get("triggerPrice", order.get("stopPrice"))),
        "amount": _number(order.get("amount")),
        "filled": _number(order.get("filled")),
        "remaining": _number(order.get("remaining")),
        "cost": _number(order.get("cost")),
        "reduce_only": (
            order.get("reduceOnly")
            if isinstance(order.get("reduceOnly"), bool)
            else None
        ),
        "timestamp": timestamp,
        "datetime": order.get("datetime"),
        "updated_timestamp": _updated_timestamp(order),
        "fee": _fee(order.get("fee")),
    }


def normalize_fill(trade: dict[str, Any]) -> dict[str, Any]:
    trade_id = str(trade.get("id") or "").strip()
    order_id = str(trade.get("order") or "").strip()
    timestamp = _integer(trade.get("timestamp"))
    symbol = str(trade.get("symbol") or "")
    if not trade_id:
        trade_id = _derived_id(
            "fill",
            [
                symbol,
                order_id,
                timestamp,
                trade.get("side"),
                trade.get("price"),
                trade.get("amount"),
                trade.get("cost"),
            ],
        )
    fees = trade.get("fees")
    normalized_fees = (
        [
            normalized
            for value in fees
            if (normalized := _fee(value)) is not None
        ]
        if isinstance(fees, list)
        else []
    )
    return {
        "id": trade_id,
        "order_id": order_id,
        "symbol": symbol,
        "type": trade.get("type"),
        "side": trade.get("side"),
        "taker_or_maker": trade.get("takerOrMaker"),
        "price": _number(trade.get("price")),
        "amount": _number(trade.get("amount")),
        "cost": _number(trade.get("cost")),
        "timestamp": timestamp,
        "datetime": trade.get("datetime"),
        "fee": _fee(trade.get("fee")),
        "fees": normalized_fees,
    }
