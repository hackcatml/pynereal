#!/usr/bin/env python3
"""Collect normalized, read-only exchange positions through CCXT."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import certifi
import ccxt
from websockets.sync.client import connect

from asset import (
    DEFAULT_CONFIG,
    build_exchange,
    eprint,
    number_or_none,
    read_provider_config,
    redact_error,
    select_accounts,
    secret_values,
    utc_now,
)


SCHEMA_VERSION = "1.5"
DEFAULT_ACCOUNT_TYPE = "swap"

BINANCE_ALL_POSITION_SCOPES = (
    (
        "usd_m",
        {"type": "swap", "subType": "linear", "useV2": True},
    ),
    (
        "coin_m",
        {"type": "delivery", "subType": "inverse"},
    ),
)
BITGET_ALL_POSITION_SCOPES = (
    "USDT-FUTURES",
    "USDC-FUTURES",
    "COIN-FUTURES",
)
BYBIT_TRANSACTION_MAX_RANGE_MS = 7 * 24 * 60 * 60 * 1000
BYBIT_BREAKDOWN_MAX_AGE_MS = 31 * 24 * 60 * 60 * 1000
BINANCE_TRADE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
BINANCE_POSITION_TRADE_WINDOWS = 14


def normalized_side(position: dict[str, Any], contracts: int | float | None) -> str | None:
    side = position.get("side")
    if isinstance(side, str) and side.lower() in {"long", "short"}:
        return side.lower()
    if contracts is not None and contracts < 0:
        return "short"
    return None


def _sum_present_numbers(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    values = [number_or_none(source.get(key)) for key in keys]
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _pnl_breakdown(
    gross_pnl: Decimal,
    fee_cost: Decimal,
    net_pnl: Decimal,
    *,
    funding: Decimal,
    funding_source: str,
    currency: str | None,
) -> dict[str, Any]:
    return {
        "gross_pnl": float(gross_pnl),
        "fees": float(fee_cost),
        "funding": float(funding),
        "funding_source": funding_source,
        "net_pnl": float(net_pnl),
        "currency": currency,
        "complete": True,
    }


def normalize_realized_pnl(position: dict[str, Any]) -> tuple[float | None, dict[str, Any] | None]:
    """Normalize the current position's realized PnL without estimating missing data."""

    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    ccxt_net = number_or_none(position.get("realizedPnl"))
    gross_pnl: float | None = None
    fee_cost: float | None = None
    funding: float | None = None
    funding_source: str | None = None
    net_pnl = ccxt_net
    complete = False

    # OKX: realizedPnl = pnl + settledPnl + fee + fundingFee + liqPenalty.
    if "instId" in info and "realizedPnl" in info:
        net_pnl = number_or_none(info.get("realizedPnl"))
        gross_pnl = _sum_present_numbers(info, ("pnl", "settledPnl"))
        funding = number_or_none(info.get("fundingFee"))
        if net_pnl is not None and gross_pnl is not None:
            fee_cost = gross_pnl + (funding or 0.0) - net_pnl
            funding_source = "position" if funding is not None else None
            complete = True

    # Bitget classic: achievedProfits excludes transaction and funding fees.
    elif "achievedProfits" in info:
        gross_pnl = _sum_present_numbers(info, ("achievedProfits", "cashDividend"))
        funding = number_or_none(info.get("totalFee")) or 0.0
        fee_cost = number_or_none(info.get("deductedFee")) or 0.0
        if gross_pnl is not None:
            net_pnl = gross_pnl + funding - fee_cost
            funding_source = "position"
            complete = True

    # Bitget UTA exposes signed fee/funding components alongside current net PnL.
    elif "curRealisedPnl" in info and any(
        key in info for key in ("openFeeTotal", "closeFeeTotal", "totalFunding")
    ):
        net_pnl = number_or_none(info.get("curRealisedPnl"))
        signed_trading_fees = _sum_present_numbers(
            info,
            ("openFeeTotal", "closeFeeTotal"),
        )
        funding = number_or_none(info.get("totalFunding")) or 0.0
        if net_pnl is not None and signed_trading_fees is not None:
            fee_cost = -signed_trading_fees
            gross_pnl = net_pnl + fee_cost - funding
            funding_source = "position"
            complete = True

    # Bybit's curRealisedPnl is already net of trading and funding fees for
    # the current holding position. A separate transaction-log lookup adds
    # the fee breakdown when available.
    elif "curRealisedPnl" in info:
        net_pnl = number_or_none(info.get("curRealisedPnl"))

    if net_pnl is None:
        return None, None
    return net_pnl, {
        "gross_pnl": gross_pnl,
        "fees": fee_cost,
        "funding": funding,
        "funding_source": funding_source,
        "net_pnl": net_pnl,
        "complete": complete,
    }


def _private_call_with_retry(
    exchange: ccxt.Exchange,
    call: Any,
    params: dict[str, Any],
    attempts: int,
    secrets: list[str],
    label: str,
) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            return call(params)
        except (
            ccxt.ArgumentsRequired,
            ccxt.AuthenticationError,
            ccxt.BadRequest,
            ccxt.NotSupported,
            ccxt.PermissionDenied,
        ):
            raise
        except ccxt.BaseError as exc:
            if attempt >= attempts:
                raise
            delay = min(2 ** (attempt - 1), 4)
            eprint(
                f"[position] {exchange.id} {label} failed ({attempt}/{attempts}): "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}; "
                f"retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError(f"{label} retry loop ended unexpectedly")


def _binance_position_cycle(
    trades: list[dict[str, Any]],
    position: dict[str, Any],
    normalized: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]] | None:
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    position_side = str(info.get("positionSide") or "BOTH").upper()
    side = str(normalized.get("side") or "").lower()
    contracts = _decimal_or_none(normalized.get("contracts"))
    if side not in {"long", "short"} or contracts in {None, Decimal(0)}:
        return None

    current = abs(contracts) if side == "long" else -abs(contracts)
    relevant: list[tuple[int, int, str, Decimal, dict[str, Any]]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        trade_position_side = str(trade.get("positionSide") or "BOTH").upper()
        if trade_position_side != position_side:
            continue
        timestamp = _integer_or_none(trade.get("time"))
        quantity = _decimal_or_none(trade.get("qty"))
        trade_side = str(trade.get("side") or "").upper()
        if timestamp is None or quantity in {None, Decimal(0)}:
            continue
        if trade_side not in {"BUY", "SELL"}:
            continue
        trade_id = _integer_or_none(trade.get("id")) or 0
        relevant.append((timestamp, trade_id, trade_side, quantity, trade))

    tolerance = max(abs(current) * Decimal("1e-12"), Decimal("1e-12"))
    cycle: list[dict[str, Any]] = []
    for timestamp, _, trade_side, quantity, trade in sorted(relevant, reverse=True):
        cycle.append(trade)
        delta = quantity if trade_side == "BUY" else -quantity
        before = current - delta
        if abs(before) <= tolerance:
            cycle.reverse()
            return timestamp, cycle
        # A single fill that flips direction contains PnL from two position
        # lifecycles and cannot be split reliably from Income History.
        if before * current < 0:
            return None
        current = before
    return None


def _binance_position_start_time(
    trades: list[dict[str, Any]],
    position: dict[str, Any],
    normalized: dict[str, Any],
) -> int | None:
    cycle = _binance_position_cycle(trades, position, normalized)
    return cycle[0] if cycle is not None else None


def enrich_binance_realized_pnl(
    exchange: ccxt.Exchange,
    position: dict[str, Any],
    normalized: dict[str, Any],
    market_scope: str,
    attempts: int,
    secrets: list[str],
) -> None:
    if not is_open_position(normalized):
        return
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    raw_symbol = str(info.get("symbol") or "").upper()
    if not raw_symbol:
        return
    if market_scope == "coin_m":
        trades_call = exchange.dapiPrivateGetUserTrades
        income_call = exchange.dapiPrivateGetIncome
    else:
        trades_call = exchange.fapiPrivateGetUserTrades
        income_call = exchange.fapiPrivateGetIncome

    trades: list[dict[str, Any]] = []
    end_time: int | None = None
    cycle = None
    for _ in range(BINANCE_POSITION_TRADE_WINDOWS):
        params: dict[str, Any] = {"symbol": raw_symbol, "limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time
        batch = _private_call_with_retry(
            exchange,
            trades_call,
            params,
            attempts,
            secrets,
            f"{market_scope} user trades",
        )
        if not isinstance(batch, list):
            return
        trades.extend(trade for trade in batch if isinstance(trade, dict))
        cycle = _binance_position_cycle(trades, position, normalized)
        if cycle is not None:
            break
        timestamps = [
            timestamp
            for trade in batch
            if isinstance(trade, dict)
            if (timestamp := _integer_or_none(trade.get("time"))) is not None
        ]
        if timestamps:
            end_time = min(timestamps) - 1
        elif end_time is None:
            position_time = _integer_or_none(normalized.get("timestamp"))
            if position_time is None:
                return
            end_time = min(
                int(time.time() * 1000),
                position_time + BINANCE_TRADE_WINDOW_MS - 1,
            )
        else:
            end_time -= BINANCE_TRADE_WINDOW_MS
    if cycle is None:
        return
    start_time, cycle_trades = cycle

    # Trade rows provide exact cycle-specific realized PnL and commissions.
    # Income History is needed only for funding, which is not a trade event.
    gross_pnl = Decimal(0)
    trading_fees = Decimal(0)
    assets: set[str] = set()
    for trade in cycle_trades:
        gross_pnl += _decimal_or_none(trade.get("realizedPnl")) or Decimal(0)
        trading_fees += _decimal_or_none(trade.get("commission")) or Decimal(0)
        commission_asset = str(trade.get("commissionAsset") or "").upper()
        if commission_asset:
            assets.add(commission_asset)

    unified_symbol = str(normalized.get("symbol") or "")
    if ":" in unified_symbol:
        settlement = unified_symbol.rsplit(":", 1)[1].split("-", 1)[0].upper()
        if settlement:
            assets.add(settlement)

    # Binance Income timestamps are rounded to the second while trade times
    # include milliseconds, so include the whole opening second.
    income_start = start_time // 1000 * 1000
    incomes = _private_call_with_retry(
        exchange,
        income_call,
        {
            "symbol": raw_symbol,
            "incomeType": "FUNDING_FEE",
            "startTime": income_start,
            "limit": 1000,
        },
        attempts,
        secrets,
        f"{market_scope} income history",
    )
    if not isinstance(incomes, list) or len(incomes) >= 1000:
        return

    funding = Decimal(0)
    for income in incomes:
        if not isinstance(income, dict):
            continue
        value = _decimal_or_none(income.get("income"))
        timestamp = _integer_or_none(income.get("time"))
        if (
            str(income.get("incomeType") or "").upper() != "FUNDING_FEE"
            or value is None
            or timestamp is None
            or timestamp < income_start
        ):
            continue
        funding += value
        asset = str(income.get("asset") or "").upper()
        if asset:
            assets.add(asset)
    if len(assets) > 1:
        return

    fee_cost = trading_fees - funding
    net_pnl = gross_pnl - fee_cost
    normalized["realized_pnl"] = float(net_pnl)
    normalized["realized_pnl_breakdown"] = _pnl_breakdown(
        gross_pnl,
        trading_fees,
        net_pnl,
        funding=funding,
        funding_source="income",
        currency=next(iter(assets), None),
    )


def _bybit_transaction_pages(
    exchange: ccxt.Exchange,
    category: str,
    start_time: int,
    end_time: int,
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    window_start = start_time
    while window_start <= end_time:
        window_end = min(window_start + BYBIT_TRANSACTION_MAX_RANGE_MS - 1, end_time)
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "accountType": "UNIFIED",
                "category": category,
                "startTime": window_start,
                "endTime": window_end,
                "limit": 50,
            }
            if cursor:
                params["cursor"] = cursor
            response = _private_call_with_retry(
                exchange,
                exchange.privateGetV5AccountTransactionLog,
                params,
                attempts,
                secrets,
                "transaction log",
            )
            result = response.get("result") if isinstance(response, dict) else None
            if not isinstance(result, dict):
                return None
            page = result.get("list")
            if not isinstance(page, list):
                return None
            rows.extend(item for item in page if isinstance(item, dict))
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        window_start = window_end + 1
    return rows


def enrich_bybit_realized_pnl(
    exchange: ccxt.Exchange,
    position: dict[str, Any],
    normalized: dict[str, Any],
    attempts: int,
    secrets: list[str],
) -> None:
    net_value = _decimal_or_none(normalized.get("realized_pnl"))
    if net_value is None or not is_open_position(normalized):
        return
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    raw_symbol = str(info.get("symbol") or "").upper()
    category = str(info.get("category") or "linear").lower()
    start_time = _integer_or_none(info.get("openTime"))
    if start_time is None or start_time <= 0:
        start_time = _integer_or_none(position.get("timestamp"))
    end_time = exchange.milliseconds()
    if (
        not raw_symbol
        or category not in {"linear", "inverse", "option"}
        or start_time is None
        or start_time <= 0
        or end_time - start_time > BYBIT_BREAKDOWN_MAX_AGE_MS
    ):
        return

    try:
        transactions = _bybit_transaction_pages(
            exchange,
            category,
            start_time,
            end_time,
            attempts,
            secrets,
        )
    except (ccxt.NotSupported, ccxt.PermissionDenied, ccxt.BadRequest):
        return
    if transactions is None:
        return

    gross_pnl = Decimal(0)
    trading_fees = Decimal(0)
    funding = Decimal(0)
    currencies: set[str] = set()
    for transaction in transactions:
        if str(transaction.get("symbol") or "").upper() != raw_symbol:
            continue
        cash_flow = _decimal_or_none(transaction.get("cashFlow")) or Decimal(0)
        fee = _decimal_or_none(transaction.get("fee")) or Decimal(0)
        funding_value = _decimal_or_none(transaction.get("funding")) or Decimal(0)
        gross_pnl += cash_flow
        trading_fees += fee
        funding += funding_value
        currency = str(transaction.get("currency") or "").upper()
        if currency:
            currencies.add(currency)

    calculated_net = gross_pnl + funding - trading_fees
    tolerance = max(abs(net_value) * Decimal("1e-8"), Decimal("1e-8"))
    if len(currencies) > 1 or abs(calculated_net - net_value) > tolerance:
        return
    normalized["realized_pnl_breakdown"] = _pnl_breakdown(
        gross_pnl,
        trading_fees,
        net_value,
        funding=funding,
        funding_source="transaction_log",
        currency=next(iter(currencies), None),
    )


def normalize_position(position: dict[str, Any]) -> dict[str, Any]:
    contracts = number_or_none(position.get("contracts"))
    contract_size = number_or_none(position.get("contractSize"))
    quantity = contracts
    if contracts is not None and contract_size is not None:
        quantity = contracts * contract_size
    realized_pnl, realized_pnl_breakdown = normalize_realized_pnl(position)
    return {
        "symbol": str(position.get("symbol") or ""),
        "side": normalized_side(position, contracts),
        "quantity": quantity,
        "contracts": contracts,
        "contract_size": contract_size,
        "notional": number_or_none(position.get("notional")),
        "leverage": number_or_none(position.get("leverage")),
        "entry_price": number_or_none(position.get("entryPrice")),
        "mark_price": number_or_none(position.get("markPrice")),
        "liquidation_price": number_or_none(position.get("liquidationPrice")),
        "collateral": number_or_none(position.get("collateral")),
        "initial_margin": number_or_none(position.get("initialMargin")),
        "maintenance_margin": number_or_none(position.get("maintenanceMargin")),
        "margin_ratio": number_or_none(position.get("marginRatio")),
        "margin_mode": position.get("marginMode"),
        "hedged": position.get("hedged") if isinstance(position.get("hedged"), bool) else None,
        "unrealized_pnl": number_or_none(position.get("unrealizedPnl")),
        "realized_pnl": realized_pnl,
        "realized_pnl_breakdown": realized_pnl_breakdown,
        "percentage": number_or_none(position.get("percentage")),
        "stop_loss_price": number_or_none(position.get("stopLossPrice")),
        "take_profit_price": number_or_none(position.get("takeProfitPrice")),
        "timestamp": position.get("timestamp"),
        "datetime": position.get("datetime"),
        "last_update_timestamp": position.get("lastUpdateTimestamp"),
    }


def normalize_hyperliquid_position(position: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_position(position)
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    entry = info.get("position")
    entry = entry if isinstance(entry, dict) else {}
    if normalized.get("mark_price") is None:
        notional = number_or_none(normalized.get("notional"))
        quantity = number_or_none(normalized.get("quantity"))
        if notional is not None and quantity not in {None, 0}:
            normalized["mark_price"] = abs(notional / quantity)
    return_on_equity = number_or_none(entry.get("returnOnEquity"))
    if return_on_equity is not None:
        normalized["percentage"] = round(return_on_equity * 100, 12)
        return normalized

    unrealized_pnl = number_or_none(normalized.get("unrealized_pnl"))
    initial_margin = number_or_none(normalized.get("initial_margin"))
    normalized["percentage"] = (
        round(unrealized_pnl / initial_margin * 100, 12)
        if unrealized_pnl is not None and initial_margin not in {None, 0}
        else None
    )
    return normalized


def is_open_position(position: dict[str, Any]) -> bool:
    for key in ("contracts", "quantity", "notional"):
        value = number_or_none(position.get(key))
        if value is not None and value != 0:
            return True
    return False


def fetch_positions_with_retry(
    exchange: ccxt.Exchange,
    symbols: list[str] | None,
    params: dict[str, Any],
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]]:
    for attempt in range(1, attempts + 1):
        try:
            positions = exchange.fetch_positions(symbols, params)
            if not isinstance(positions, list):
                raise TypeError("CCXT fetch_positions() returned a non-list result")
            return [item for item in positions if isinstance(item, dict)]
        except (
            ccxt.ArgumentsRequired,
            ccxt.AuthenticationError,
            ccxt.BadRequest,
            ccxt.NotSupported,
            ccxt.PermissionDenied,
        ):
            raise
        except ccxt.BaseError as exc:
            if attempt >= attempts:
                raise
            delay = min(2 ** (attempt - 1), 4)
            eprint(
                f"[position] {exchange.id} fetch failed ({attempt}/{attempts}): "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}; retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError("position retry loop ended unexpectedly")


def binance_position_scopes(
    account_type: str,
    all_derivative_scopes: bool,
) -> list[tuple[str, dict[str, Any]]]:
    if all_derivative_scopes or account_type == "all":
        return [(name, dict(params)) for name, params in BINANCE_ALL_POSITION_SCOPES]
    if account_type in {"coin", "coin-m", "coin_m", "delivery", "inverse"}:
        return [("coin_m", {"type": "delivery", "subType": "inverse"})]
    return [
        (
            "usd_m",
            {"type": account_type, "subType": "linear", "useV2": True},
        )
    ]


def binance_position_matches_scope(
    position: dict[str, Any],
    market_scope: str,
) -> bool:
    """Validate a Binance derivative scope from its CCXT unified symbol."""

    symbol = str(position.get("symbol") or "")
    if ":" not in symbol or "/" not in symbol:
        return True
    market_pair, settlement = symbol.rsplit(":", 1)
    base, quote = market_pair.split("/", 1)
    settlement = settlement.split("-", 1)[0]
    base = base.upper()
    quote = quote.upper()
    settlement = settlement.upper()
    if market_scope == "usd_m":
        return settlement == quote
    if market_scope == "coin_m":
        return settlement == base
    return True


def bitget_position_scopes(
    account_type: str,
    all_derivative_scopes: bool,
) -> list[str]:
    if all_derivative_scopes or account_type == "all":
        return list(BITGET_ALL_POSITION_SCOPES)
    product_types = {
        "coin": "COIN-FUTURES",
        "coin-futures": "COIN-FUTURES",
        "coin_m": "COIN-FUTURES",
        "delivery": "COIN-FUTURES",
        "inverse": "COIN-FUTURES",
        "linear": "USDT-FUTURES",
        "swap": "USDT-FUTURES",
        "future": "USDT-FUTURES",
        "usdc": "USDC-FUTURES",
        "usdc-futures": "USDC-FUTURES",
        "usdt": "USDT-FUTURES",
        "usdt-futures": "USDT-FUTURES",
    }
    product_type = product_types.get(account_type)
    if product_type is None:
        raise ValueError(f"unsupported Bitget position account type: {account_type}")
    return [product_type]


def fetch_bitget_positions_with_retry(
    exchange: ccxt.Exchange,
    account_name: str,
    product_type: str,
    symbols: list[str] | None,
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]]:
    for attempt in range(1, attempts + 1):
        try:
            exchange.load_markets()
            response = exchange.privateMixGetV2MixPositionAllPosition(
                {"productType": product_type}
            )
            raw_positions = response.get("data") if isinstance(response, dict) else None
            if not isinstance(raw_positions, list):
                raise TypeError("Bitget all-position endpoint returned invalid data")
            positions = [
                exchange.parse_position(item)
                for item in raw_positions
                if isinstance(item, dict)
            ]
            if symbols:
                requested = set(symbols)
                positions = [
                    position
                    for position in positions
                    if position.get("symbol") in requested
                ]
            return positions
        except (
            ccxt.ArgumentsRequired,
            ccxt.AuthenticationError,
            ccxt.BadRequest,
            ccxt.NotSupported,
            ccxt.PermissionDenied,
        ):
            raise
        except ccxt.BaseError as exc:
            if attempt >= attempts:
                raise
            delay = min(2 ** (attempt - 1), 4)
            eprint(
                f"[position] {account_name}/bitget {product_type} fetch failed "
                f"({attempt}/{attempts}): {type(exc).__name__}: "
                f"{redact_error(exc, secrets)}; retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError("Bitget position retry loop ended unexpectedly")


def hyperliquid_ws_url(exchange: ccxt.Exchange) -> str:
    api = exchange.urls.get("api", {})
    public_url = api.get("public") if isinstance(api, dict) else None
    if not isinstance(public_url, str):
        raise ValueError("Hyperliquid public API URL is not configured")
    public_url = public_url.replace("{hostname}", str(exchange.hostname))
    if public_url.startswith("https://"):
        return f"wss://{public_url.removeprefix('https://').rstrip('/')}/ws"
    if public_url.startswith("http://"):
        return f"ws://{public_url.removeprefix('http://').rstrip('/')}/ws"
    raise ValueError(f"unsupported Hyperliquid public API URL: {public_url}")


def parse_hyperliquid_dex_states(value: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = (
            item
            for item in value
            if isinstance(item, list) and len(item) == 2
        )
    else:
        raise TypeError("Hyperliquid clearinghouseStates has an invalid type")

    states: list[tuple[str, dict[str, Any]]] = []
    for raw_dex, state in items:
        if not isinstance(state, dict):
            continue
        dex = str(raw_dex or "").strip().lower() or "default"
        states.append((dex, state))
    if not states:
        raise ValueError("Hyperliquid returned no DEX clearinghouse states")
    return states


def fetch_hyperliquid_all_dex_states(
    exchange: ccxt.Exchange,
    attempts: int,
    secrets: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    wallet_address, _ = exchange.handle_public_address("fetchPositions", {})
    wallet_address = str(wallet_address or "").strip()
    if not wallet_address:
        raise ccxt.ArgumentsRequired("Hyperliquid walletAddress is required")
    request = {
        "method": "subscribe",
        "subscription": {
            "type": "allDexsClearinghouseState",
            "user": wallet_address,
        },
    }
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    for attempt in range(1, attempts + 1):
        try:
            with connect(
                hyperliquid_ws_url(exchange),
                ssl=ssl_context,
                open_timeout=10,
                close_timeout=5,
            ) as websocket:
                websocket.send(json.dumps(request))
                deadline = time.monotonic() + min(exchange.timeout / 1000, 30)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Hyperliquid all-DEX position snapshot timed out")
                    message = json.loads(websocket.recv(timeout=remaining))
                    if not isinstance(message, dict):
                        continue
                    if message.get("channel") != "allDexsClearinghouseState":
                        continue
                    data = message.get("data")
                    if not isinstance(data, dict):
                        raise TypeError("Hyperliquid all-DEX snapshot has invalid data")
                    return parse_hyperliquid_dex_states(data.get("clearinghouseStates"))
        except Exception as exc:
            if attempt >= attempts:
                raise
            delay = min(2 ** (attempt - 1), 4)
            eprint(
                f"[position] hyperliquid all-DEX snapshot failed ({attempt}/{attempts}): "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}; "
                f"retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError("Hyperliquid all-DEX snapshot retry loop ended unexpectedly")


def configure_hyperliquid_dex_markets(
    exchange: ccxt.Exchange,
    dexes: list[str],
) -> None:
    fetch_markets = exchange.options.get("fetchMarkets")
    fetch_markets = dict(fetch_markets) if isinstance(fetch_markets, dict) else {}
    fetch_markets["types"] = ["swap"] + (["hip3"] if dexes else [])
    hip3 = fetch_markets.get("hip3")
    hip3 = dict(hip3) if isinstance(hip3, dict) else {}
    hip3["dexes"] = dexes
    fetch_markets["hip3"] = hip3
    exchange.options["fetchMarkets"] = fetch_markets


def load_hyperliquid_position_markets(
    exchange: ccxt.Exchange,
    dexes: list[str],
    attempts: int,
    secrets: list[str],
) -> None:
    configure_hyperliquid_dex_markets(exchange, dexes)
    for attempt in range(1, attempts + 1):
        try:
            exchange.load_markets()
            return
        except ccxt.BaseError as exc:
            if attempt >= attempts:
                raise
            delay = min(2 ** (attempt - 1), 4)
            eprint(
                f"[position] hyperliquid market metadata failed ({attempt}/{attempts}): "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}; "
                f"retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError("Hyperliquid market metadata retry loop ended unexpectedly")


def fetch_hyperliquid_positions(
    exchange: ccxt.Exchange,
    args: argparse.Namespace,
    secrets: list[str],
) -> tuple[list[dict[str, Any]], list[str], str]:
    if not args.dex and not args.symbols:
        states = fetch_hyperliquid_all_dex_states(exchange, args.attempts, secrets)
        active_dexes = [
            dex
            for dex, state in states
            if dex != "default" and state.get("assetPositions")
        ]
        if any(state.get("assetPositions") for _, state in states):
            load_hyperliquid_position_markets(
                exchange,
                active_dexes,
                args.attempts,
                secrets,
            )
        positions: list[dict[str, Any]] = []
        for dex, state in states:
            raw_positions = state.get("assetPositions", [])
            if not isinstance(raw_positions, list):
                raise TypeError(f"Hyperliquid {dex} assetPositions has an invalid type")
            for raw_position in raw_positions:
                if not isinstance(raw_position, dict):
                    continue
                normalized = normalize_hyperliquid_position(
                    exchange.parse_position(raw_position)
                )
                normalized["dex"] = dex
                positions.append(normalized)
        return (
            positions,
            [dex for dex, _ in states],
            "hyperliquid.all_dexs_clearinghouse_state",
        )

    dexes: list[str | None] = [args.dex] if args.dex else [None]

    positions: list[dict[str, Any]] = []
    queried_dexes: list[str] = []
    for dex in dexes:
        params = {"dex": dex} if dex else {}
        fetched = fetch_positions_with_retry(
            exchange,
            args.symbols or None,
            params,
            args.attempts,
            secrets,
        )
        dex_name = dex or "default"
        queried_dexes.append(dex_name)
        for position in fetched:
            normalized = normalize_hyperliquid_position(position)
            normalized["dex"] = dex_name
            positions.append(normalized)
    return positions, queried_dexes, "ccxt.fetch_positions"


def collect_one(
    account_name: str,
    exchange_id: str,
    account_type: str,
    config: dict[str, Any],
    args: argparse.Namespace,
    *,
    log_progress: bool = True,
) -> dict[str, Any]:
    secrets = secret_values(config)
    exchange: ccxt.Exchange | None = None
    try:
        exchange = build_exchange(exchange_id, config, args.timeout_ms, account_type)
        if not exchange.has.get("fetchPositions"):
            raise ccxt.NotSupported(f"{exchange_id} does not support fetchPositions")
        if exchange_id == "hyperliquid":
            normalized, queried_dexes, source = fetch_hyperliquid_positions(
                exchange,
                args,
                secrets,
            )
            queried_scopes = ["all_dexs"] if not args.dex and not args.symbols else []
        elif exchange_id == "binance":
            normalized = []
            queried_dexes = []
            queried_scopes = []
            scopes = binance_position_scopes(
                account_type,
                args.all_derivative_scopes,
            )
            for scope, params in scopes:
                positions = fetch_positions_with_retry(
                    exchange,
                    args.symbols or None,
                    params,
                    args.attempts,
                    secrets,
                )
                queried_scopes.append(scope)
                for position in positions:
                    item = normalize_position(position)
                    item["market_scope"] = scope
                    if binance_position_matches_scope(item, scope):
                        try:
                            enrich_binance_realized_pnl(
                                exchange,
                                position,
                                item,
                                scope,
                                args.attempts,
                                secrets,
                            )
                        except Exception as exc:
                            eprint(
                                f"[position] binance {scope} realized PnL unavailable: "
                                f"{type(exc).__name__}: {redact_error(exc, secrets)}"
                            )
                        normalized.append(item)
            source = "ccxt.fetch_positions"
        elif exchange_id == "bitget":
            normalized = []
            queried_dexes = []
            queried_scopes = []
            product_types = bitget_position_scopes(
                account_type,
                args.all_derivative_scopes,
            )
            for product_type in product_types:
                positions = fetch_bitget_positions_with_retry(
                    exchange,
                    account_name,
                    product_type,
                    args.symbols or None,
                    args.attempts,
                    secrets,
                )
                queried_scopes.append(product_type)
                for position in positions:
                    item = normalize_position(position)
                    item["market_scope"] = product_type
                    normalized.append(item)
            source = "bitget.all_position"
        else:
            params: dict[str, Any] = {"type": account_type}
            positions = fetch_positions_with_retry(
                exchange,
                args.symbols or None,
                params,
                args.attempts,
                secrets,
            )
            normalized = []
            for position in positions:
                item = normalize_position(position)
                if exchange_id == "bybit":
                    try:
                        enrich_bybit_realized_pnl(
                            exchange,
                            position,
                            item,
                            args.attempts,
                            secrets,
                        )
                    except Exception as exc:
                        eprint(
                            f"[position] bybit realized PnL breakdown unavailable: "
                            f"{type(exc).__name__}: {redact_error(exc, secrets)}"
                        )
                normalized.append(item)
            queried_dexes = []
            queried_scopes = [account_type]
            source = "ccxt.fetch_positions"
        if not args.include_closed:
            normalized = [position for position in normalized if is_open_position(position)]
        normalized.sort(
            key=lambda item: (
                str(item.get("dex")),
                str(item.get("symbol")),
                str(item.get("side")),
            )
        )
        return {
            "account": account_name,
            "exchange": exchange_id,
            "account_type": account_type,
            "dex": (
                args.dex or ("all" if not args.symbols else None)
                if exchange_id == "hyperliquid"
                else None
            ),
            "queried_dexes": queried_dexes if exchange_id == "hyperliquid" else None,
            "queried_scopes": queried_scopes,
            "source": source,
            "positions": normalized,
            "position_count": len(normalized),
            "status": "ok",
        }
    except Exception as exc:
        message = redact_error(exc, secrets)
        eprint(
            f"[position] {account_name}/{exchange_id}/{account_type} failed: "
            f"{type(exc).__name__}: {message}"
        )
        return {
            "account": account_name,
            "exchange": exchange_id,
            "account_type": account_type,
            "dex": args.dex if exchange_id == "hyperliquid" else None,
            "queried_scopes": [],
            "source": (
                "hyperliquid.all_dexs_clearinghouse_state"
                if exchange_id == "hyperliquid" and not args.dex and not args.symbols
                else "ccxt.fetch_positions"
            ),
            "positions": [],
            "position_count": 0,
            "status": "error",
            "error": {"type": type(exc).__name__, "message": message},
        }
    finally:
        if exchange is not None:
            try:
                exchange.close()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read normalized exchange positions from providers.toml via CCXT."
    )
    parser.add_argument(
        "--account",
        "--profile",
        action="append",
        dest="accounts",
        help="Configured ccxt account name. Repeat for multiple accounts.",
    )
    parser.add_argument(
        "--exchange",
        action="append",
        dest="exchanges",
        help="CCXT exchange id. Selects every configured account for that exchange.",
    )
    parser.add_argument(
        "--account-type",
        action="append",
        dest="account_types",
        help=(
            "Limit the derivative scope, for example swap, usdc, or delivery. "
            "By default all supported scopes are queried."
        ),
    )
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="CCXT unified symbol. Repeat to filter multiple symbols.",
    )
    parser.add_argument(
        "--dex",
        help="Hyperliquid HIP-3 perp DEX name, for example xyz.",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include zero-size position records returned by the exchange.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be greater than zero")
    if args.attempts <= 0:
        parser.error("--attempts must be greater than zero")
    args.all_derivative_scopes = not bool(args.account_types)
    args.account_types = list(
        dict.fromkeys(
            value.strip().lower()
            for value in (args.account_types or [DEFAULT_ACCOUNT_TYPE])
            if value.strip()
        )
    )
    args.symbols = list(
        dict.fromkeys(value.strip() for value in (args.symbols or []) if value.strip())
    )
    args.dex = args.dex.strip().lower() if isinstance(args.dex, str) and args.dex.strip() else None
    return args


def print_fatal_result(error_type: str, message: str) -> None:
    output = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "source": "exchange.positions",
        "read_only": True,
        "results": [],
        "summary": {
            "requested": 0,
            "succeeded": 0,
            "failed": 1,
            "open_positions": 0,
        },
        "error": {"type": error_type, "message": message},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))


def main() -> int:
    args = parse_args()
    try:
        data = read_provider_config(args.config.resolve())
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:500]
        eprint(f"[position] config error: {type(exc).__name__}: {message}")
        print_fatal_result(type(exc).__name__, message)
        return 2

    try:
        accounts = select_accounts(data, args.accounts, args.exchanges)
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:500]
        eprint(f"[position] account config error: {type(exc).__name__}: {message}")
        print_fatal_result(type(exc).__name__, message)
        return 2
    if not accounts:
        eprint(
            "[position] no account selected; pass --account/--exchange or configure "
            "[ccxt.<exchange>] or [ccxt_accounts.<name>]"
        )
        print_fatal_result("ConfigurationError", "no exchange account selected or configured")
        return 2

    results = [
        collect_one(
            account.name,
            account.exchange_id,
            account_type,
            account.config,
            args,
        )
        for account in accounts
        for account_type in args.account_types
    ]
    failed = sum(result["status"] != "ok" for result in results)
    output = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "source": "exchange.positions",
        "read_only": True,
        "results": results,
        "summary": {
            "requested": len(results),
            "succeeded": len(results) - failed,
            "failed": failed,
            "open_positions": sum(result["position_count"] for result in results),
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
