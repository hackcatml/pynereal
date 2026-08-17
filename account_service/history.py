from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
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


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


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
    info = trade.get("info")
    info = info if isinstance(info, dict) else {}
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
    realized_pnl = trade.get("realizedPnl")
    if realized_pnl is None:
        realized_pnl = info.get("realizedPnl")
    if realized_pnl is None:
        realized_pnl = info.get("closedPnl")
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
        "position_side": str(
            trade.get("positionSide") or info.get("positionSide") or ""
        ).upper() or None,
        "realized_pnl": _number(realized_pnl),
    }


def bitget_position_market_scope(position: dict[str, Any]) -> str | None:
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    product_type = str(
        info.get("productType") or position.get("productType") or ""
    ).upper()
    if product_type in {"USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES"}:
        return product_type

    symbol_scope = bitget_symbol_market_scope(position.get("symbol"))
    if symbol_scope not in {None, "spot"}:
        return symbol_scope

    margin_coin = str(info.get("marginCoin") or "").upper()
    if margin_coin == "USDT":
        return "USDT-FUTURES"
    if margin_coin == "USDC":
        return "USDC-FUTURES"
    if margin_coin:
        return "COIN-FUTURES"
    return None


def bitget_symbol_market_scope(symbol: Any) -> str | None:
    value = str(symbol or "").strip()
    if not value:
        return None
    if ":" not in value:
        return "spot"
    settlement = value.rsplit(":", 1)[1].split("-", 1)[0].upper()
    if settlement == "USDT":
        return "USDT-FUTURES"
    if settlement == "USDC":
        return "USDC-FUTURES"
    return "COIN-FUTURES" if settlement else None


def bitget_history_item_matches_scope(
    payload: dict[str, Any],
    market_scope: str,
) -> bool:
    if market_scope == "uta":
        return True
    actual_scope = bitget_symbol_market_scope(payload.get("symbol"))
    return actual_scope is None or actual_scope == market_scope


def bitget_position_matches_scope(
    position: dict[str, Any],
    market_scope: str,
) -> bool:
    if market_scope == "uta":
        return True
    actual_scope = bitget_position_market_scope(position)
    return actual_scope is None or actual_scope == market_scope


def okx_history_market_scope(payload: dict[str, Any]) -> str | None:
    info = payload.get("info")
    info = info if isinstance(info, dict) else {}
    instrument_type = str(
        info.get("instType") or payload.get("instType") or ""
    ).upper()
    scope_by_type = {
        "SPOT": "spot",
        "MARGIN": "spot",
        "SWAP": "swap",
        "FUTURES": "futures",
        "OPTION": "option",
    }
    if instrument_type in scope_by_type:
        return scope_by_type[instrument_type]

    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        return None
    if ":" not in symbol:
        return "spot"
    contract = symbol.rsplit(":", 1)[1]
    parts = contract.split("-")
    if len(parts) >= 3 and parts[-1].upper() in {"C", "P"}:
        return "option"
    return "futures" if len(parts) >= 2 else "swap"


def _normalized_fee(payload: dict[str, Any]) -> tuple[Decimal, str | None]:
    fee = payload.get("fee")
    if isinstance(fee, dict):
        cost = _decimal(fee.get("cost"))
        if cost is not None:
            return cost, str(fee.get("currency") or "").upper() or None
    fees = payload.get("fees")
    if not isinstance(fees, list):
        return Decimal(0), None
    total = Decimal(0)
    currencies: set[str] = set()
    for item in fees:
        if not isinstance(item, dict):
            continue
        cost = _decimal(item.get("cost"))
        if cost is not None:
            total += cost
        currency = str(item.get("currency") or "").upper()
        if currency:
            currencies.add(currency)
    return total, next(iter(currencies)) if len(currencies) == 1 else None


def hyperliquid_market_scope(symbol: str) -> str:
    if "/" in symbol and ":" not in symbol:
        return "spot"
    base = symbol.split("/", 1)[0].strip()
    if "-" not in base:
        return "default"
    return base.split("-", 1)[0].strip().lower() or "default"


def reconstruct_position_history_from_fills(
    account: str,
    exchange: str,
    market_scope: str,
    symbol: str,
    fills: list[dict[str, Any]],
    *,
    infer_linear_pnl: bool,
) -> list[dict[str, Any]]:
    """Build completed position lifecycles from normalized fills."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in fills:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        position_side = str(payload.get("position_side") or "BOTH").upper()
        grouped.setdefault(position_side, []).append(row)

    results: list[dict[str, Any]] = []
    tolerance = Decimal("1e-12")
    for position_side, rows in grouped.items():
        current = Decimal(0)
        average = Decimal(0)
        opened_quantity = Decimal(0)
        exit_quantity = Decimal(0)
        exit_value = Decimal(0)
        gross_pnl = Decimal(0)
        fees = Decimal(0)
        fee_currencies: set[str] = set()
        gross_known = True
        opened_at = ""

        def start_cycle(
            signed_quantity: Decimal,
            price: Decimal,
            occurred_at: str,
            fee_cost: Decimal,
            fee_currency: str | None,
        ) -> None:
            nonlocal current, average, opened_quantity, exit_quantity
            nonlocal exit_value, gross_pnl, fees, gross_known, opened_at
            current = signed_quantity
            average = price
            opened_quantity = abs(signed_quantity)
            exit_quantity = Decimal(0)
            exit_value = Decimal(0)
            gross_pnl = Decimal(0)
            fees = fee_cost
            fee_currencies.clear()
            if fee_currency:
                fee_currencies.add(fee_currency)
            gross_known = True
            opened_at = occurred_at

        def finish_cycle(closed_at: str, side_sign: Decimal) -> None:
            if not opened_at or exit_quantity <= tolerance:
                return
            exit_price = exit_value / exit_quantity
            currency = (
                next(iter(fee_currencies))
                if len(fee_currencies) == 1
                else None
            )
            pnl_complete = gross_known and len(fee_currencies) <= 1
            net_pnl = gross_pnl - fees if pnl_complete else None
            identity = [
                account,
                exchange,
                market_scope,
                symbol,
                position_side,
                opened_at,
                closed_at,
            ]
            payload = {
                "symbol": symbol,
                "side": "long" if side_sign > 0 else "short",
                "quantity": float(opened_quantity),
                "contracts": float(opened_quantity),
                "contract_size": None,
                "entry_price": float(average),
                "exit_price": float(exit_price),
                "realized_pnl": float(net_pnl) if net_pnl is not None else None,
                "realized_pnl_breakdown": (
                    {
                        "gross_pnl": float(gross_pnl),
                        "fees": float(fees),
                        "net_pnl": float(net_pnl),
                        "currency": currency,
                        "complete": True,
                    }
                    if net_pnl is not None
                    else None
                ),
                "opened_at_known": True,
                "native_id": None,
                "position_side": position_side,
            }
            results.append({
                "account": account,
                "exchange": exchange,
                "market_scope": market_scope,
                "position_key": _derived_id("trades", identity),
                "opened_at": opened_at,
                "closed_at": closed_at,
                "source": "trades",
                "payload": payload,
            })

        def sort_key(row: dict[str, Any]) -> tuple[str, int | str]:
            trade_id = str(row.get("trade_id") or "")
            try:
                ordered_id: int | str = int(trade_id)
            except ValueError:
                ordered_id = trade_id
            return str(row.get("occurred_at") or ""), ordered_id

        for row in sorted(rows, key=sort_key):
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            quantity = _decimal(payload.get("amount"))
            price = _decimal(payload.get("price"))
            trade_side = str(payload.get("side") or "").lower()
            occurred_at = str(
                row.get("occurred_at") or payload.get("datetime") or ""
            )
            if (
                quantity is None
                or quantity <= 0
                or price is None
                or price <= 0
                or trade_side not in {"buy", "sell"}
                or not occurred_at
            ):
                continue
            signed = quantity if trade_side == "buy" else -quantity
            fee_cost, fee_currency = _normalized_fee(payload)
            if current == 0:
                start_cycle(signed, price, occurred_at, fee_cost, fee_currency)
                continue
            if current * signed > 0:
                new_size = abs(current) + quantity
                average = (average * abs(current) + price * quantity) / new_size
                current += signed
                opened_quantity += quantity
                fees += fee_cost
                if fee_currency:
                    fee_currencies.add(fee_currency)
                continue

            side_sign = Decimal(1) if current > 0 else Decimal(-1)
            closed_quantity = min(abs(current), quantity)
            closing_fee = fee_cost * closed_quantity / quantity
            fees += closing_fee
            if fee_currency:
                fee_currencies.add(fee_currency)
            exit_quantity += closed_quantity
            exit_value += price * closed_quantity
            native_gross = _decimal(payload.get("realized_pnl"))
            if native_gross is not None:
                gross_pnl += native_gross
            elif infer_linear_pnl:
                gross_pnl += (price - average) * closed_quantity * side_sign
            else:
                gross_known = False

            remaining_position = abs(current) - closed_quantity
            remaining_trade = quantity - closed_quantity
            if remaining_position <= tolerance:
                finish_cycle(occurred_at, side_sign)
                current = Decimal(0)
                if remaining_trade > tolerance:
                    opening_fee = fee_cost - closing_fee
                    residual = remaining_trade if signed > 0 else -remaining_trade
                    start_cycle(
                        residual,
                        price,
                        occurred_at,
                        opening_fee,
                        fee_currency,
                    )
            else:
                current = side_sign * remaining_position

    return results


def reconstruct_binance_position_history(
    account: str,
    market_scope: str,
    symbol: str,
    fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return reconstruct_position_history_from_fills(
        account,
        "binance",
        market_scope,
        symbol,
        fills,
        infer_linear_pnl=market_scope == "usd_m",
    )
