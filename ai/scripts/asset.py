#!/usr/bin/env python3
"""Collect normalized, read-only exchange balances through CCXT."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

import ccxt

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "workdir" / "config" / "providers.toml"
SCHEMA_VERSION = "1.2"
_BALANCE_FIELDS = ("free", "used", "total", "debt")
DEFAULT_ACCOUNT_TYPES = ("spot", "swap", "margin", "funding", "earn")
_ACCOUNT_CREDENTIAL_FIELDS = {
    "apiKey",
    "privateKey",
    "password",
    "secret",
    "token",
    "uid",
    "walletAddress",
}
_SENSITIVE_PATTERN = re.compile(
    r"(?i)(api[-_ ]?key|secret|password|passphrase|signature|token)"
    r"(\s*[=:]\s*|[\"']\s*:\s*[\"'])([^&\s,}\"']+)"
)


@dataclass(frozen=True)
class ExchangeAccount:
    name: str
    exchange_id: str
    config: dict[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def read_provider_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"provider config not found: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError("provider config must contain TOML tables")
    return data


def ccxt_root_config(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("ccxt")
    return value if isinstance(value, dict) else {}


def is_account_table(value: Any) -> bool:
    return isinstance(value, dict) and any(
        key in value for key in _ACCOUNT_CREDENTIAL_FIELDS
    )


def configured_exchanges(data: dict[str, Any]) -> list[str]:
    root = ccxt_root_config(data)
    supported = set(ccxt.exchanges)
    names = {
        str(key).lower()
        for key, value in root.items()
        if isinstance(value, dict) and str(key).lower() in supported
    }
    for key, value in data.items():
        exchange_id = key.split(".", 1)[1].lower() if key.startswith("ccxt.") else ""
        if exchange_id in supported and isinstance(value, dict):
            names.add(exchange_id)
    return sorted(names)


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def exchange_config(data: dict[str, Any], exchange_id: str) -> dict[str, Any]:
    root = ccxt_root_config(data)
    supported = set(ccxt.exchanges)
    base = {
        key: value
        for key, value in root.items()
        if not (isinstance(value, dict) and str(key).lower() in supported)
    }
    nested = root.get(exchange_id)
    if not isinstance(nested, dict):
        nested = {}
    else:
        nested = {
            key: value for key, value in nested.items() if not is_account_table(value)
        }
    legacy = data.get(f"ccxt.{exchange_id}")
    if not isinstance(legacy, dict):
        legacy = {}
    return merge_dicts(merge_dicts(base, nested), legacy)


def configured_accounts(data: dict[str, Any]) -> list[ExchangeAccount]:
    accounts: list[ExchangeAccount] = []
    names: set[str] = set()
    root = ccxt_root_config(data)

    for exchange_id in configured_exchanges(data):
        exchange_table = root.get(exchange_id)
        exchange_table = exchange_table if isinstance(exchange_table, dict) else {}
        nested_accounts = [
            (str(raw_name), raw_config)
            for raw_name, raw_config in exchange_table.items()
            if is_account_table(raw_config)
        ]
        has_direct_credentials = any(
            key in exchange_table for key in _ACCOUNT_CREDENTIAL_FIELDS
        )
        legacy = data.get(f"ccxt.{exchange_id}")
        has_legacy_table = isinstance(legacy, dict)

        if not nested_accounts or has_direct_credentials or has_legacy_table:
            accounts.append(
                ExchangeAccount(exchange_id, exchange_id, exchange_config(data, exchange_id))
            )
            names.add(exchange_id)

        base = {
            key: value
            for key, value in exchange_config(data, exchange_id).items()
            if key not in _ACCOUNT_CREDENTIAL_FIELDS
        }
        for raw_name, raw_config in nested_accounts:
            suffix = raw_name.strip().lower()
            name = f"{exchange_id}_{suffix}"
            if not suffix or not re.fullmatch(r"[a-z0-9_-]+", suffix):
                raise ValueError(
                    f"invalid nested ccxt account name: {exchange_id}.{raw_name}"
                )
            if name in names:
                raise ValueError(f"duplicate ccxt account name: {name}")
            accounts.append(
                ExchangeAccount(name, exchange_id, merge_dicts(base, raw_config))
            )
            names.add(name)

    profile_root = data.get("ccxt_accounts", {})
    if not isinstance(profile_root, dict):
        raise ValueError("[ccxt_accounts] must contain account tables")

    supported = set(ccxt.exchanges)
    for raw_name, raw_config in profile_root.items():
        name = str(raw_name).strip().lower()
        if not name or not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError(f"invalid ccxt account name: {raw_name!r}")
        if name in names:
            raise ValueError(f"duplicate ccxt account name: {name}")
        if not isinstance(raw_config, dict):
            raise ValueError(f"[ccxt_accounts.{name}] must be a TOML table")

        exchange_id = str(raw_config.get("exchange", "")).strip().lower()
        if exchange_id not in supported:
            raise ValueError(
                f"[ccxt_accounts.{name}] has unsupported exchange: {exchange_id or '<missing>'}"
            )

        # Profiles inherit exchange options, but credentials must be declared per account.
        base = {
            key: value
            for key, value in exchange_config(data, exchange_id).items()
            if key not in _ACCOUNT_CREDENTIAL_FIELDS
        }
        override = {key: value for key, value in raw_config.items() if key != "exchange"}
        accounts.append(ExchangeAccount(name, exchange_id, merge_dicts(base, override)))
        names.add(name)
    return accounts


def select_accounts(
    data: dict[str, Any],
    requested_accounts: list[str] | None,
    requested_exchanges: list[str] | None,
) -> list[ExchangeAccount]:
    accounts = configured_accounts(data)
    supported = set(ccxt.exchanges)
    exchange_filters = list(
        dict.fromkeys(
            value.strip().lower()
            for value in (requested_exchanges or [])
            if value.strip()
        )
    )
    unsupported = [exchange_id for exchange_id in exchange_filters if exchange_id not in supported]
    if unsupported:
        raise ValueError(f"unsupported CCXT exchange: {', '.join(unsupported)}")

    if requested_accounts:
        account_names = list(
            dict.fromkeys(value.strip().lower() for value in requested_accounts if value.strip())
        )
        account_map = {account.name: account for account in accounts}
        missing = [name for name in account_names if name not in account_map]
        if missing:
            available = ", ".join(account_map) or "none"
            raise ValueError(
                f"unknown ccxt account: {', '.join(missing)}; available accounts: {available}"
            )
        selected = [account_map[name] for name in account_names]
        if exchange_filters:
            selected = [
                account for account in selected if account.exchange_id in exchange_filters
            ]
            if not selected:
                raise ValueError("selected accounts do not match the requested exchanges")
        return selected

    if not exchange_filters:
        return accounts

    selected = [account for account in accounts if account.exchange_id in exchange_filters]
    selected_exchanges = {account.exchange_id for account in selected}
    for exchange_id in exchange_filters:
        if exchange_id not in selected_exchanges:
            selected.append(
                ExchangeAccount(exchange_id, exchange_id, exchange_config(data, exchange_id))
            )
    return selected


def redact_error(exc: Exception, secrets: list[str]) -> str:
    text = str(exc).replace("\n", " ").strip()
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        text = text.replace(secret, "***")
    text = _SENSITIVE_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    return text[:500] or type(exc).__name__


def secret_values(config: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("apiKey", "secret", "password", "privateKey", "token"):
        value = config.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return values


def build_exchange(
    exchange_id: str,
    config: dict[str, Any],
    timeout_ms: int,
    account_type: str | None,
) -> ccxt.Exchange:
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None or exchange_id not in ccxt.exchanges:
        raise ValueError(f"unsupported CCXT exchange: {exchange_id}")

    client_config = merge_dicts(
        {"enableRateLimit": True, "timeout": timeout_ms},
        config,
    )
    sandbox = bool(client_config.pop("isTestnet", False) or client_config.pop("sandbox", False))
    if account_type:
        options = client_config.get("options")
        options = dict(options) if isinstance(options, dict) else {}
        options["defaultType"] = account_type
        client_config["options"] = options

    exchange = exchange_class(client_config)
    try:
        if sandbox:
            exchange.set_sandbox_mode(True)
        if exchange_id == "hyperliquid":
            if not exchange.walletAddress:
                raise ccxt.ArgumentsRequired(
                    "hyperliquid requires walletAddress in [ccxt.hyperliquid]"
                )
        else:
            exchange.check_required_credentials()
    except Exception:
        try:
            exchange.close()
        except Exception:
            pass
        raise
    return exchange


def number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def asset_codes(balance: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for field in _BALANCE_FIELDS:
        values = balance.get(field)
        if isinstance(values, dict):
            codes.update(str(code).upper() for code in values)
    for code, value in balance.items():
        if isinstance(value, dict) and any(field in value for field in _BALANCE_FIELDS):
            codes.add(str(code).upper())
    return codes


def normalize_assets(
    balance: dict[str, Any],
    currencies: set[str],
    include_zero: bool,
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for code in sorted(asset_codes(balance)):
        if currencies and code not in currencies:
            continue
        direct = balance.get(code)
        direct = direct if isinstance(direct, dict) else {}
        asset: dict[str, Any] = {"currency": code}
        values: list[int | float] = []
        for field in _BALANCE_FIELDS:
            field_map = balance.get(field)
            raw_value = field_map.get(code) if isinstance(field_map, dict) else direct.get(field)
            value = number_or_none(raw_value)
            asset[field] = value
            if value is not None:
                values.append(value)
        if include_zero or any(value != 0 for value in values):
            assets.append(asset)
    return assets


def add_balance_amount(
    balance: dict[str, Any],
    code: Any,
    *,
    free: Any = None,
    used: Any = None,
    total: Any = None,
    debt: Any = None,
) -> None:
    currency = str(code or "").strip().upper()
    if not currency:
        return
    account = balance.setdefault(currency, {})
    for field, raw_value in (
        ("free", free),
        ("used", used),
        ("total", total),
        ("debt", debt),
    ):
        value = number_or_none(raw_value)
        if value is None:
            continue
        previous = number_or_none(account.get(field)) or 0
        account[field] = previous + value


def retry_read(
    exchange: ccxt.Exchange,
    operation: str,
    callback: Callable[[], Any],
    attempts: int,
    secrets: list[str],
) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            return callback()
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
                f"[asset] {exchange.id} {operation} failed ({attempt}/{attempts}): "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}; retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError(f"{operation} retry loop ended unexpectedly")


def fetch_standard_balance(
    exchange: ccxt.Exchange,
    account_type: str,
    attempts: int,
    secrets: list[str],
) -> dict[str, Any]:
    params: dict[str, Any] = {"type": account_type}
    if account_type == "margin":
        params["marginMode"] = "cross"
    return retry_read(
        exchange,
        f"fetch_balance({account_type})",
        lambda: exchange.fetch_balance(params),
        attempts,
        secrets,
    )


def fetch_hyperliquid_balance(
    exchange: ccxt.Exchange,
    account_type: str,
    attempts: int,
    secrets: list[str],
) -> dict[str, Any]:
    params = {
        "type": account_type,
        "enableUnifiedMargin": False,
    }
    return retry_read(
        exchange,
        f"fetch_balance({account_type})",
        lambda: exchange.fetch_balance(params),
        attempts,
        secrets,
    )


def fetch_bitget_funding_balance(
    exchange: ccxt.Exchange,
    attempts: int,
    secrets: list[str],
) -> dict[str, Any]:
    response = retry_read(
        exchange,
        "fetch_funding_assets",
        lambda: exchange.privateSpotGetV2AccountFundingAssets({}),
        attempts,
        secrets,
    )
    rows = response.get("data", []) if isinstance(response, dict) else []
    if not isinstance(rows, list):
        raise TypeError("Bitget funding assets returned invalid data")
    balance: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        free = number_or_none(row.get("available"))
        used = number_or_none(row.get("frozen"))
        total = None
        if free is not None or used is not None:
            total = (free or 0) + (used or 0)
        add_balance_amount(
            balance,
            row.get("coin"),
            free=free,
            used=used,
            total=total,
        )
    return balance


def fetch_bitget_earn_balance(
    exchange: ccxt.Exchange,
    attempts: int,
    secrets: list[str],
) -> dict[str, Any]:
    response = retry_read(
        exchange,
        "fetch_earn_assets",
        lambda: exchange.privateEarnGetV2EarnAccountAssets({}),
        attempts,
        secrets,
    )
    rows = response.get("data", []) if isinstance(response, dict) else []
    if not isinstance(rows, list):
        raise TypeError("Bitget earn assets returned invalid data")
    balance: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        amount = number_or_none(row.get("amount"))
        add_balance_amount(
            balance,
            row.get("coin"),
            used=amount,
            total=amount,
        )
    return balance


def fetch_binance_earn_rows(
    exchange: ccxt.Exchange,
    method_name: str,
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]]:
    method = getattr(exchange, method_name)
    rows: list[dict[str, Any]] = []
    current = 1
    page_size = 100
    while True:
        response = retry_read(
            exchange,
            method_name,
            lambda: method({"current": current, "size": page_size}),
            attempts,
            secrets,
        )
        page = response.get("rows", []) if isinstance(response, dict) else []
        if not isinstance(page, list):
            raise TypeError(f"Binance {method_name} returned invalid rows")
        rows.extend(row for row in page if isinstance(row, dict))
        total = number_or_none(response.get("total")) if isinstance(response, dict) else None
        if len(page) < page_size or (total is not None and current * page_size >= total):
            break
        current += 1
    return rows


def fetch_binance_earn_balance(
    exchange: ccxt.Exchange,
    attempts: int,
    secrets: list[str],
) -> dict[str, Any]:
    flexible = fetch_binance_earn_rows(
        exchange,
        "sapiGetSimpleEarnFlexiblePosition",
        attempts,
        secrets,
    )
    locked = fetch_binance_earn_rows(
        exchange,
        "sapiGetSimpleEarnLockedPosition",
        attempts,
        secrets,
    )
    balance: dict[str, Any] = {}
    for row in flexible:
        amount = number_or_none(row.get("totalAmount"))
        add_balance_amount(balance, row.get("asset"), used=amount, total=amount)
    for row in locked:
        amount = number_or_none(row.get("amount"))
        add_balance_amount(balance, row.get("asset"), used=amount, total=amount)
    return balance


def supports_account_type(exchange_id: str, account_type: str) -> bool:
    if exchange_id == "hyperliquid":
        return account_type in {"spot", "swap"}
    if account_type == "earn":
        return exchange_id in {"binance", "bitget"}
    return True


def unavailable_account_type_error(
    exchange_id: str,
    account_type: str,
    exc: Exception,
) -> bool:
    if exchange_id != "bitget":
        return False
    message = str(exc).lower()
    if account_type == "margin":
        return "50021" in message and "margin trading account does not exist" in message
    if account_type in {"funding", "earn"}:
        return "40068" in message and "disable subaccount access" in message
    return False


def fetch_account_type_balance(
    exchange: ccxt.Exchange,
    exchange_id: str,
    account_type: str,
    attempts: int,
    secrets: list[str],
) -> tuple[dict[str, Any], str]:
    if exchange_id == "hyperliquid" and account_type in {"spot", "swap"}:
        return (
            fetch_hyperliquid_balance(exchange, account_type, attempts, secrets),
            "ccxt.fetch_balance",
        )
    if account_type == "earn":
        if exchange_id == "binance":
            return (
                fetch_binance_earn_balance(exchange, attempts, secrets),
                "binance.simple_earn",
            )
        if exchange_id == "bitget":
            return fetch_bitget_earn_balance(exchange, attempts, secrets), "bitget.earn"
        raise ccxt.NotSupported(f"{exchange_id} earn assets are not supported")
    if account_type == "funding" and exchange_id == "bitget":
        return fetch_bitget_funding_balance(exchange, attempts, secrets), "bitget.funding"
    return (
        fetch_standard_balance(exchange, account_type, attempts, secrets),
        "ccxt.fetch_balance",
    )


def account_type_result(
    account_name: str,
    exchange_id: str,
    account_type: str,
    *,
    status: str,
    source: str | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    if source is None:
        if account_type == "earn" and exchange_id == "binance":
            source = "binance.simple_earn"
        elif account_type == "earn" and exchange_id == "bitget":
            source = "bitget.earn"
        elif account_type == "funding" and exchange_id == "bitget":
            source = "bitget.funding"
        else:
            source = "ccxt.fetch_balance"
    result: dict[str, Any] = {
        "account": account_name,
        "exchange": exchange_id,
        "account_type": account_type,
        "source": source,
        "assets": [],
        "asset_count": 0,
        "status": status,
    }
    if error is not None:
        result["error"] = error
    return result


def collect_account_type(
    account_name: str,
    exchange_id: str,
    account_type: str,
    exchange: ccxt.Exchange,
    secrets: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    eprint(
        f"[asset] collecting account={account_name} exchange={exchange_id} "
        f"account_type={account_type}"
    )
    try:
        balance, source = fetch_account_type_balance(
            exchange,
            exchange_id,
            account_type,
            args.attempts,
            secrets,
        )
        assets = normalize_assets(balance, args.currencies, args.include_zero)
        return {
            "account": account_name,
            "exchange": exchange_id,
            "account_type": account_type,
            "source": source,
            "exchange_timestamp": balance.get("timestamp"),
            "exchange_datetime": balance.get("datetime"),
            "assets": assets,
            "asset_count": len(assets),
            "status": "ok",
        }
    except Exception as exc:
        message = redact_error(exc, secrets)
        if unavailable_account_type_error(exchange_id, account_type, exc):
            eprint(
                f"[asset] {account_name}/{exchange_id}/{account_type} unavailable: "
                f"{message}"
            )
            return account_type_result(
                account_name,
                exchange_id,
                account_type,
                status="unavailable",
                error={"type": type(exc).__name__, "message": message},
            )
        eprint(
            f"[asset] {account_name}/{exchange_id}/{account_type} failed: "
            f"{type(exc).__name__}: {message}"
        )
        return account_type_result(
            account_name,
            exchange_id,
            account_type,
            status="error",
            error={"type": type(exc).__name__, "message": message},
        )


def collect_account(
    account: ExchangeAccount,
    account_types: list[str],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    secrets = secret_values(account.config)
    supported = [
        account_type
        for account_type in account_types
        if supports_account_type(account.exchange_id, account_type)
    ]
    unsupported = set(account_types) - set(supported)
    exchange: ccxt.Exchange | None = None
    build_error: Exception | None = None
    if supported:
        try:
            exchange = build_exchange(
                account.exchange_id,
                account.config,
                args.timeout_ms,
                None,
            )
        except Exception as exc:
            build_error = exc

    results: list[dict[str, Any]] = []
    try:
        for account_type in account_types:
            if account_type in unsupported:
                results.append(
                    account_type_result(
                        account.name,
                        account.exchange_id,
                        account_type,
                        status="unsupported",
                        error={
                            "type": "NotSupported",
                            "message": (
                                f"{account.exchange_id} does not expose {account_type} assets"
                            ),
                        },
                    )
                )
                continue
            if build_error is not None:
                message = redact_error(build_error, secrets)
                results.append(
                    account_type_result(
                        account.name,
                        account.exchange_id,
                        account_type,
                        status="error",
                        error={"type": type(build_error).__name__, "message": message},
                    )
                )
                continue
            if exchange is None:
                raise RuntimeError("exchange client was not initialized")
            results.append(
                collect_account_type(
                    account.name,
                    account.exchange_id,
                    account_type,
                    exchange,
                    secrets,
                    args,
                )
            )
        return results
    finally:
        if exchange is not None:
            try:
                exchange.close()
            except Exception:
                pass


def remove_binance_earn_receipts(results: list[dict[str, Any]]) -> None:
    by_account: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if result.get("exchange") == "binance":
            by_account.setdefault(str(result.get("account")), []).append(result)

    for account_results in by_account.values():
        earn_codes = {
            str(asset.get("currency"))
            for result in account_results
            if result.get("account_type") == "earn" and result.get("status") == "ok"
            for asset in result.get("assets", [])
            if isinstance(asset, dict)
        }
        if not earn_codes:
            continue
        for result in account_results:
            if result.get("account_type") != "spot" or result.get("status") != "ok":
                continue
            assets = result.get("assets", [])
            if not isinstance(assets, list):
                continue
            excluded = [
                str(asset.get("currency"))
                for asset in assets
                if isinstance(asset, dict)
                and str(asset.get("currency", "")).startswith("LD")
                and str(asset.get("currency", ""))[2:] in earn_codes
            ]
            if excluded:
                result["assets"] = [
                    asset
                    for asset in assets
                    if not isinstance(asset, dict)
                    or str(asset.get("currency")) not in excluded
                ]
                result["asset_count"] = len(result["assets"])
                result["excluded_receipt_assets"] = excluded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read normalized exchange balances from providers.toml via CCXT."
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
            "Only query this balance type. Repeat as needed; defaults to spot, swap, "
            "margin, funding, and earn."
        ),
    )
    parser.add_argument(
        "--currency",
        action="append",
        dest="currencies_raw",
        help="Only return this currency. Repeat for multiple currencies.",
    )
    parser.add_argument("--include-zero", action="store_true", help="Include zero balances.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be greater than zero")
    if args.attempts <= 0:
        parser.error("--attempts must be greater than zero")
    args.currencies = {
        value.strip().upper() for value in (args.currencies_raw or []) if value.strip()
    }
    if args.account_types:
        args.account_types = list(
            dict.fromkeys(value.strip().lower() for value in args.account_types if value.strip())
        )
    return args


def print_fatal_result(error_type: str, message: str) -> None:
    output = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "source": "exchange.account_assets",
        "read_only": True,
        "results": [],
        "summary": {
            "requested": 0,
            "succeeded": 0,
            "failed": 1,
            "unsupported": 0,
            "unavailable": 0,
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
        eprint(f"[asset] config error: {type(exc).__name__}: {message}")
        print_fatal_result(type(exc).__name__, message)
        return 2

    try:
        accounts = select_accounts(data, args.accounts, args.exchanges)
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:500]
        eprint(f"[asset] account config error: {type(exc).__name__}: {message}")
        print_fatal_result(type(exc).__name__, message)
        return 2
    if not accounts:
        eprint(
            "[asset] no account selected; pass --account/--exchange or configure "
            "[ccxt.<exchange>] or [ccxt_accounts.<name>]"
        )
        print_fatal_result("ConfigurationError", "no exchange account selected or configured")
        return 2

    account_types = args.account_types or list(DEFAULT_ACCOUNT_TYPES)
    results = [
        result
        for account in accounts
        for result in collect_account(account, account_types, args)
    ]
    if "earn" in account_types:
        remove_binance_earn_receipts(results)
    failed = sum(result["status"] == "error" for result in results)
    unsupported = sum(result["status"] == "unsupported" for result in results)
    unavailable = sum(result["status"] == "unavailable" for result in results)
    output = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "source": "exchange.account_assets",
        "read_only": True,
        "results": results,
        "summary": {
            "requested": len(results),
            "succeeded": len(results) - failed - unsupported - unavailable,
            "failed": failed,
            "unsupported": unsupported,
            "unavailable": unavailable,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
