#!/usr/bin/env python3
"""Collect normalized, read-only recent exchange order history through CCXT."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

import ccxt

from asset import (
    DEFAULT_CONFIG,
    build_exchange,
    eprint,
    number_or_none,
    read_provider_config,
    redact_error,
    secret_values,
    select_accounts,
    utc_now,
)


SCHEMA_VERSION = "1.0"
DEFAULT_DAYS = 90
DEFAULT_LIMIT = 100


def normalize_order(order: dict[str, Any]) -> dict[str, Any]:
    fee = order.get("fee")
    fee = fee if isinstance(fee, dict) else {}
    return {
        "id": str(order.get("id") or ""),
        "client_order_id": str(order.get("clientOrderId") or ""),
        "symbol": str(order.get("symbol") or ""),
        "type": order.get("type"),
        "side": order.get("side"),
        "status": order.get("status"),
        "time_in_force": order.get("timeInForce"),
        "price": number_or_none(order.get("price")),
        "average_price": number_or_none(order.get("average")),
        "trigger_price": number_or_none(
            order.get("triggerPrice", order.get("stopPrice"))
        ),
        "amount": number_or_none(order.get("amount")),
        "filled": number_or_none(order.get("filled")),
        "remaining": number_or_none(order.get("remaining")),
        "cost": number_or_none(order.get("cost")),
        "reduce_only": (
            order.get("reduceOnly")
            if isinstance(order.get("reduceOnly"), bool)
            else None
        ),
        "timestamp": order.get("timestamp"),
        "datetime": order.get("datetime"),
        "last_trade_timestamp": order.get("lastTradeTimestamp"),
        "fee": {
            "currency": fee.get("currency"),
            "cost": number_or_none(fee.get("cost")),
        },
    }


def _fetch_with_retry(
    exchange: ccxt.Exchange,
    label: str,
    call: Callable[[], Any],
    *,
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]]:
    for attempt in range(1, attempts + 1):
        try:
            result = call()
            if not isinstance(result, list):
                raise TypeError(f"{label} returned a non-list result")
            return [item for item in result if isinstance(item, dict)]
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
                f"[order_history] {exchange.id} {label} failed "
                f"({attempt}/{attempts}): {type(exc).__name__}: "
                f"{redact_error(exc, secrets)}; retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError(f"{label} retry loop ended unexpectedly")


def _hyperliquid_dex(symbol: str) -> str | None:
    base = symbol.split("/", 1)[0].strip()
    if "-" not in base:
        return None
    prefix = base.split("-", 1)[0].strip().lower()
    return prefix or None


def _prepare_markets(exchange: ccxt.Exchange, symbol: str, attempts: int,
                     secrets: list[str]) -> None:
    if exchange.id == "hyperliquid":
        dex = _hyperliquid_dex(symbol)
        if dex:
            from position import configure_hyperliquid_dex_markets

            configure_hyperliquid_dex_markets(exchange, [dex])
    _fetch_with_retry(
        exchange,
        "load_markets",
        lambda: list(exchange.load_markets().values()),
        attempts=attempts,
        secrets=secrets,
    )


def fetch_order_history(
    exchange: ccxt.Exchange,
    symbol: str,
    since: int,
    limit: int,
    attempts: int,
    secrets: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    sources: list[str] = []
    orders: list[dict[str, Any]] = []

    if exchange.has.get("fetchOrders"):
        try:
            orders.extend(_fetch_with_retry(
                exchange,
                "fetch_orders",
                lambda: exchange.fetch_orders(symbol, since, limit),
                attempts=attempts,
                secrets=secrets,
            ))
            sources.append("ccxt.fetch_orders")
        except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
            pass
    if not sources:
        if exchange.has.get("fetchClosedOrders"):
            try:
                orders.extend(_fetch_with_retry(
                    exchange,
                    "fetch_closed_orders",
                    lambda: exchange.fetch_closed_orders(symbol, since, limit),
                    attempts=attempts,
                    secrets=secrets,
                ))
                sources.append("ccxt.fetch_closed_orders")
            except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
                pass
        if exchange.has.get("fetchOpenOrders"):
            try:
                orders.extend(_fetch_with_retry(
                    exchange,
                    "fetch_open_orders",
                    lambda: exchange.fetch_open_orders(symbol, None, limit),
                    attempts=attempts,
                    secrets=secrets,
                ))
                sources.append("ccxt.fetch_open_orders")
            except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
                pass

    if not sources:
        raise ccxt.NotSupported(
            f"{exchange.id} does not expose order history through CCXT"
        )

    deduplicated: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for raw_order in orders:
        order = normalize_order(raw_order)
        key = (order["id"], str(order["status"]), order["timestamp"])
        deduplicated[key] = order
    normalized = sorted(
        deduplicated.values(),
        key=lambda item: int(item.get("timestamp") or 0),
        reverse=True,
    )[:limit]
    return normalized, sources


def account_type_for_market(market_type: str) -> str:
    if market_type == "spot":
        return "spot"
    if market_type == "inverse":
        return "delivery"
    return "swap"


def collect_one(account_name: str, exchange_id: str, config: dict[str, Any],
                args: argparse.Namespace) -> dict[str, Any]:
    secrets = secret_values(config)
    exchange: ccxt.Exchange | None = None
    account_type = account_type_for_market(args.market_type)
    try:
        exchange = build_exchange(exchange_id, config, args.timeout_ms, account_type)
        _prepare_markets(exchange, args.symbol, args.attempts, secrets)
        if args.symbol not in exchange.markets:
            return {
                "account": account_name,
                "exchange": exchange_id,
                "account_type": account_type,
                "symbol": args.symbol,
                "orders": [],
                "order_count": 0,
                "sources": [],
                "status": "unavailable",
                "reason": "symbol_not_available",
            }
        since = int((time.time() - args.days * 86_400) * 1000)
        orders, sources = fetch_order_history(
            exchange,
            args.symbol,
            since,
            args.limit,
            args.attempts,
            secrets,
        )
        return {
            "account": account_name,
            "exchange": exchange_id,
            "account_type": account_type,
            "symbol": args.symbol,
            "orders": orders,
            "order_count": len(orders),
            "sources": sources,
            "status": "ok",
        }
    except (ccxt.NotSupported, ccxt.BadSymbol) as exc:
        return {
            "account": account_name,
            "exchange": exchange_id,
            "account_type": account_type,
            "symbol": args.symbol,
            "orders": [],
            "order_count": 0,
            "sources": [],
            "status": "unavailable",
            "reason": type(exc).__name__,
        }
    except Exception as exc:
        message = redact_error(exc, secrets)
        eprint(
            f"[order_history] {account_name}/{exchange_id} failed: "
            f"{type(exc).__name__}: {message}"
        )
        return {
            "account": account_name,
            "exchange": exchange_id,
            "account_type": account_type,
            "symbol": args.symbol,
            "orders": [],
            "order_count": 0,
            "sources": [],
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
        description="Read normalized recent order history from providers.toml via CCXT."
    )
    parser.add_argument("--symbol", required=True, help="CCXT unified session symbol.")
    parser.add_argument(
        "--market-type",
        choices=("spot", "linear", "inverse"),
        default="linear",
    )
    parser.add_argument("--account", action="append", dest="accounts")
    parser.add_argument("--exchange", action="append", dest="exchanges")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    if args.days <= 0 or args.limit <= 0:
        parser.error("--days and --limit must be greater than zero")
    if args.timeout_ms <= 0 or args.attempts <= 0:
        parser.error("--timeout-ms and --attempts must be greater than zero")
    args.symbol = args.symbol.strip()
    return args


def fatal_result(error_type: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "source": "exchange.order_history",
        "read_only": True,
        "results": [],
        "summary": {"requested": 0, "succeeded": 0, "failed": 1, "orders": 0},
        "error": {"type": error_type, "message": message},
    }


def main() -> int:
    args = parse_args()
    try:
        data = read_provider_config(args.config.resolve())
        accounts = select_accounts(data, args.accounts, args.exchanges)
        if not accounts:
            raise ValueError("no exchange account selected or configured")
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:500]
        print(json.dumps(fatal_result(type(exc).__name__, message), ensure_ascii=False,
                         indent=2, allow_nan=False))
        return 2

    results = [
        collect_one(account.name, account.exchange_id, account.config, args)
        for account in accounts
    ]
    failed = sum(result["status"] == "error" for result in results)
    succeeded = sum(result["status"] == "ok" for result in results)
    output = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "source": "exchange.order_history",
        "read_only": True,
        "query": {
            "symbol": args.symbol,
            "market_type": args.market_type,
            "days": args.days,
            "limit_per_account": args.limit,
        },
        "results": results,
        "summary": {
            "requested": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "unavailable": len(results) - succeeded - failed,
            "orders": sum(result["order_count"] for result in results),
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
