#!/usr/bin/env python3
"""Collect normalized, read-only exchange positions through CCXT."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
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


SCHEMA_VERSION = "1.4"
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


def normalized_side(position: dict[str, Any], contracts: int | float | None) -> str | None:
    side = position.get("side")
    if isinstance(side, str) and side.lower() in {"long", "short"}:
        return side.lower()
    if contracts is not None and contracts < 0:
        return "short"
    return None


def normalize_position(position: dict[str, Any]) -> dict[str, Any]:
    contracts = number_or_none(position.get("contracts"))
    contract_size = number_or_none(position.get("contractSize"))
    quantity = contracts
    if contracts is not None and contract_size is not None:
        quantity = contracts * contract_size
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
        "realized_pnl": number_or_none(position.get("realizedPnl")),
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
                f"[position] bitget {product_type} fetch failed "
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
) -> dict[str, Any]:
    secrets = secret_values(config)
    exchange: ccxt.Exchange | None = None
    eprint(
        f"[position] collecting account={account_name} exchange={exchange_id} "
        f"account_type={account_type}"
    )
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
            normalized = [normalize_position(position) for position in positions]
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
