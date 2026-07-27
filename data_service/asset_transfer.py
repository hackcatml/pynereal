from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import ccxt

from ai.scripts.asset import (
    ExchangeAccount,
    build_exchange,
    configured_accounts,
    fetch_account_type_balance,
    fetch_binance_earn_rows,
    normalize_assets,
    read_provider_config,
    redact_error,
    retry_read,
    secret_values,
)


_SOURCES = {
    "binance": {"spot", "swap", "margin", "funding", "earn"},
    "bitget": {"spot", "swap", "margin", "funding", "earn"},
    "bybit": {"spot", "swap", "margin", "funding"},
    "okx": {"spot", "funding"},
    "hyperliquid": {"spot", "swap"},
}
_DESTINATIONS = {
    "binance": {
        "spot": ("swap", "margin", "funding"),
        "swap": ("spot", "margin", "funding"),
        "margin": ("spot", "swap", "funding"),
        "funding": ("spot", "swap", "margin"),
        "earn": ("spot", "swap", "margin"),
    },
    "bitget": {
        "spot": ("swap", "margin", "funding"),
        "swap": ("spot", "margin", "funding"),
        "margin": ("spot", "swap", "funding"),
        "funding": ("spot", "swap", "margin"),
        "earn": ("spot", "swap", "margin", "funding"),
    },
    "bybit": {
        "spot": ("funding",),
        "swap": ("funding",),
        "margin": ("funding",),
        "funding": ("spot",),
    },
    "okx": {
        "spot": ("funding",),
        "funding": ("spot",),
    },
    "hyperliquid": {
        "spot": ("swap",),
        "swap": ("spot",),
    },
}
_BINANCE_TRANSFER_TYPES = {
    ("spot", "swap"): "MAIN_UMFUTURE",
    ("spot", "margin"): "MAIN_MARGIN",
    ("spot", "funding"): "MAIN_FUNDING",
    ("swap", "spot"): "UMFUTURE_MAIN",
    ("swap", "margin"): "UMFUTURE_MARGIN",
    ("swap", "funding"): "UMFUTURE_FUNDING",
    ("margin", "spot"): "MARGIN_MAIN",
    ("margin", "swap"): "MARGIN_UMFUTURE",
    ("margin", "funding"): "MARGIN_FUNDING",
    ("funding", "spot"): "FUNDING_MAIN",
    ("funding", "swap"): "FUNDING_UMFUTURE",
    ("funding", "margin"): "FUNDING_MARGIN",
}
_BITGET_ACCOUNT_TYPES = {
    "spot": "spot",
    "swap": "usdt_futures",
    "margin": "crossed_margin",
    "funding": "p2p",
}
_OKX_ACCOUNT_TYPES = {
    "spot": "18",
    "funding": "6",
}
_BYBIT_ACCOUNT_TYPES = {
    "spot": "UNIFIED",
    "swap": "UNIFIED",
    "margin": "UNIFIED",
    "funding": "FUND",
}
_BINANCE_SUB_ACCOUNT_TYPES = {
    "spot": "SPOT",
    "swap": "USDT_FUTURE",
    "margin": "MARGIN",
}
_ASSET_PATTERN = re.compile(r"[A-Z0-9]{1,20}")
_HYPERLIQUID_ADDRESS_PATTERN = re.compile(r"0x[a-f0-9]{40}")
_ACCOUNT_HIERARCHY_TTL_SECONDS = 30 * 60


@dataclass(frozen=True)
class TransferAccountIdentity:
    key: str
    label: str
    role: str
    group_id: str
    external_id: str | None
    account: ExchangeAccount | None = field(default=None, repr=False, compare=False)


@dataclass
class TransferAccountHierarchy:
    exchange_id: str
    accounts: dict[str, TransferAccountIdentity]
    masters: dict[str, str]

    def identity(self, account_name: str) -> TransferAccountIdentity | None:
        return self.accounts.get(str(account_name or "").strip().lower())

    def master(self, identity: TransferAccountIdentity) -> TransferAccountIdentity | None:
        key = self.masters.get(identity.group_id)
        return self.accounts.get(key) if key else None

    def related(self, identity: TransferAccountIdentity) -> list[TransferAccountIdentity]:
        return [
            item
            for item in self.accounts.values()
            if item.group_id == identity.group_id
        ]


class AssetTransferError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AssetTransferError(f"{field} must be a valid number") from exc
    if not parsed.is_finite():
        raise AssetTransferError(f"{field} must be a finite number")
    return parsed


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _asset_code(value: Any) -> str:
    asset = str(value or "").strip().upper()
    if not _ASSET_PATTERN.fullmatch(asset):
        raise AssetTransferError("asset must be a valid exchange asset code")
    return asset


def _account_by_name(
    config_path: Path,
    account_name: str,
    exchange_id: str,
) -> ExchangeAccount:
    requested = str(account_name or "").strip().lower()
    accounts = configured_accounts(read_provider_config(config_path))
    account = next((item for item in accounts if item.name == requested), None)
    if account is None:
        raise AssetTransferError("configured exchange account was not found", status_code=404)
    if account.exchange_id != exchange_id:
        raise AssetTransferError("account does not belong to the requested exchange")
    return account


def _response_rows(response: Any, *keys: str) -> list[dict[str, Any]]:
    value = response
    search_keys = (*keys, "data", "rows", "list", "subAccounts")
    for _ in range(3):
        if not isinstance(value, dict):
            break
        next_value = None
        for key in search_keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                value = candidate
                next_value = None
                break
            if isinstance(candidate, dict) and next_value is None:
                next_value = candidate
        else:
            if next_value is not None:
                value = next_value
                continue
        if isinstance(value, list) or next_value is None:
            break
        value = next_value
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _external_id(value: Any) -> str:
    return str(value or "").strip()


def _integer_account_id(value: str | None, exchange_label: str) -> int:
    text = _external_id(value)
    if not text.isdigit():
        raise AssetTransferError(f"{exchange_label} account UID discovery is incomplete")
    return int(text)


def _is_main_identifier(parent_id: str, user_id: str) -> bool:
    return not parent_id or parent_id == "0" or parent_id == user_id


def _account_label(account: ExchangeAccount) -> str:
    return account.name


def _wallet_assets(
    balance: dict[str, Any],
    *,
    allowed_assets: set[str] | None = None,
    unsupported_note: str | None = None,
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for item in normalize_assets(balance, set(), False):
        asset = str(item.get("currency") or "").upper()
        available = _decimal(item.get("free") or 0, "available balance")
        if available <= 0:
            continue
        transferable = allowed_assets is None or asset in allowed_assets
        assets.append({
            "key": f"wallet:{asset}",
            "asset": asset,
            "available": _amount_text(available),
            "source_kind": "wallet",
            "transferable": transferable,
            "note": None if transferable else unsupported_note,
        })
    return sorted(
        assets,
        key=lambda item: (not item["transferable"], item["asset"]),
    )


def _wallet_available(balance: dict[str, Any], asset: str) -> Decimal:
    return next(
        (
            _decimal(item.get("free") or 0, "available balance")
            for item in normalize_assets(balance, {asset}, True)
            if item.get("currency") == asset
        ),
        Decimal(0),
    )


def _binance_earn_positions(
    exchange: ccxt.Exchange,
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]]:
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
    positions: list[dict[str, Any]] = []
    for row in flexible:
        available = _decimal(row.get("totalAmount") or 0, "Earn balance")
        product_id = str(row.get("productId") or "").strip()
        asset = str(row.get("asset") or "").strip().upper()
        if available <= 0 or not product_id or not _ASSET_PATTERN.fullmatch(asset):
            continue
        can_redeem = row.get("canRedeem") is not False
        positions.append({
            "key": f"flexible:{product_id}",
            "asset": asset,
            "available": _amount_text(available),
            "source_kind": "flexible",
            "product_id": product_id,
            "transferable": can_redeem,
            "note": None if can_redeem else "This Flexible Earn product cannot be redeemed now.",
        })
    for row in locked:
        available = _decimal(row.get("amount") or 0, "Locked Earn balance")
        position_id = str(row.get("positionId") or "").strip()
        asset = str(row.get("asset") or "").strip().upper()
        if available <= 0 or not position_id or not _ASSET_PATTERN.fullmatch(asset):
            continue
        positions.append({
            "key": f"locked:{position_id}",
            "asset": asset,
            "available": _amount_text(available),
            "source_kind": "locked",
            "position_id": position_id,
            "transferable": False,
            "note": "Locked Earn redemption is not supported by this transfer screen.",
        })
    return sorted(
        positions,
        key=lambda item: (
            not item["transferable"],
            item["asset"],
            item["key"],
        ),
    )


def _bitget_savings_rows(
    exchange: ccxt.Exchange,
    period_type: str,
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    end_id = ""
    while True:
        params: dict[str, Any] = {"periodType": period_type, "limit": "100"}
        if end_id:
            params["idLessThan"] = end_id
        response = retry_read(
            exchange,
            f"fetch_{period_type}_savings_assets",
            lambda request=dict(params): exchange.privateEarnGetV2EarnSavingsAssets(
                request
            ),
            attempts,
            secrets,
        )
        data = response.get("data", {}) if isinstance(response, dict) else {}
        page = data.get("resultList", []) if isinstance(data, dict) else []
        if not isinstance(page, list):
            raise TypeError("Bitget savings assets returned invalid data")
        rows.extend(row for row in page if isinstance(row, dict))
        next_end_id = str(data.get("endId") or "") if isinstance(data, dict) else ""
        if len(page) < 100 or not next_end_id or next_end_id == end_id:
            break
        end_id = next_end_id
    return rows


def _bitget_earn_positions(
    exchange: ccxt.Exchange,
    attempts: int,
    secrets: list[str],
) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for period_type in ("flexible", "fixed"):
        for row in _bitget_savings_rows(exchange, period_type, attempts, secrets):
            available = _decimal(row.get("holdAmount") or 0, "Savings balance")
            product_id = str(row.get("productId") or "").strip()
            order_id = str(row.get("orderId") or "").strip()
            asset = str(row.get("productCoin") or "").strip().upper()
            if available <= 0 or not product_id or not _ASSET_PATTERN.fullmatch(asset):
                continue
            status = str(row.get("status") or "").strip().lower()
            transferable = period_type == "flexible" and status != "in_redemption"
            note = None
            if period_type == "fixed":
                note = "Fixed Savings redemption is not supported by this transfer screen."
            elif status == "in_redemption":
                note = "This Savings position is already being redeemed."
            positions.append({
                "key": f"{period_type}:{order_id or product_id}",
                "asset": asset,
                "available": _amount_text(available),
                "source_kind": period_type,
                "period_type": period_type,
                "product_id": product_id,
                "order_id": order_id or None,
                "transferable": transferable,
                "note": note,
            })
    return sorted(
        positions,
        key=lambda item: (
            not item["transferable"],
            item["asset"],
            item["key"],
        ),
    )


def _state_change(
    callback: Callable[[], Any],
    *,
    operation: str,
    exchange_label: str,
    secrets: list[str],
) -> dict[str, Any]:
    try:
        result = callback()
    except ccxt.NetworkError as exc:
        raise AssetTransferError(
            f"{operation} response was not received. Execution status is unknown; "
            f"check {exchange_label} balances or transfer history before trying again.",
            status_code=502,
            details={"status": "unknown"},
        ) from exc
    except ccxt.BaseError as exc:
        raise AssetTransferError(
            f"{operation} failed: {redact_error(exc, secrets)}",
            status_code=502,
            details={"status": "failed"},
        ) from exc
    except Exception as exc:
        raise AssetTransferError(
            f"{operation} response was not received. Execution status is unknown; "
            f"check {exchange_label} balances or transfer history before trying again.",
            status_code=502,
            details={"status": "unknown"},
        ) from exc
    if not isinstance(result, dict):
        raise AssetTransferError(
            f"{operation} returned an invalid response. "
            f"Check {exchange_label} before trying again.",
            status_code=502,
            details={"status": "unknown"},
        )
    return result


def _client_id() -> str:
    return f"pynereal{uuid.uuid4().hex[:24]}"


def _success_result(
    account: ExchangeAccount,
    source: str,
    destination: str,
    asset: str,
    amount: Decimal,
    steps: list[dict[str, Any]],
    *,
    target_account: str | None = None,
    target_account_label: str | None = None,
    status: str = "success",
) -> dict[str, Any]:
    return {
        "status": status,
        "account": account.name,
        "target_account": target_account or account.name,
        "target_account_label": target_account_label or target_account or account.name,
        "exchange": account.exchange_id,
        "source": source,
        "destination": destination,
        "asset": asset,
        "amount": _amount_text(amount),
        "steps": steps,
        "completed_at": _utc_now(),
    }


class AssetTransferService:
    def __init__(
        self,
        config_path: Path,
        *,
        timeout_ms: int = 30_000,
        read_attempts: int = 3,
    ) -> None:
        self.config_path = config_path
        self.timeout_ms = timeout_ms
        self.read_attempts = read_attempts
        self._locks: dict[str, asyncio.Lock] = {}
        self._hierarchy_cache: dict[
            str,
            tuple[int, float, TransferAccountHierarchy],
        ] = {}
        self._hierarchy_cache_lock = threading.Lock()

    @staticmethod
    def _validate_route(
        exchange_id: str,
        source: str,
        destination: str | None = None,
        *,
        allowed_destinations: tuple[str, ...] | None = None,
    ) -> None:
        if exchange_id not in _SOURCES:
            raise AssetTransferError("internal transfer is not supported for this exchange")
        if source not in _SOURCES[exchange_id]:
            raise AssetTransferError("unsupported source account type")
        destinations = (
            allowed_destinations
            if allowed_destinations is not None
            else _DESTINATIONS[exchange_id][source]
        )
        if destination is not None and destination not in destinations:
            raise AssetTransferError("unsupported transfer destination")

    def _config_revision(self) -> int:
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return 0

    @staticmethod
    def _standalone_hierarchy(
        exchange_id: str,
        accounts: list[ExchangeAccount],
    ) -> TransferAccountHierarchy:
        identities = {
            account.name: TransferAccountIdentity(
                key=account.name,
                label=_account_label(account),
                role="standalone",
                group_id=account.name,
                external_id=None,
                account=account,
            )
            for account in accounts
        }
        return TransferAccountHierarchy(
            exchange_id=exchange_id,
            accounts=identities,
            masters={account.name: account.name for account in accounts},
        )

    def _read_identity(
        self,
        exchange: ccxt.Exchange,
        operation: str,
        callback: Callable[[], Any],
        secrets: list[str],
    ) -> Any:
        return retry_read(
            exchange,
            operation,
            callback,
            self.read_attempts,
            secrets,
        )

    def _account_hierarchy(
        self,
        exchange_id: str,
        fallback_account: ExchangeAccount | None = None,
    ) -> TransferAccountHierarchy:
        revision = self._config_revision()
        now = time.monotonic()
        with self._hierarchy_cache_lock:
            cached = self._hierarchy_cache.get(exchange_id)
            if (
                cached is not None
                and cached[0] == revision
                and now - cached[1] < _ACCOUNT_HIERARCHY_TTL_SECONDS
            ):
                return cached[2]

        try:
            accounts = [
                account
                for account in configured_accounts(read_provider_config(self.config_path))
                if account.exchange_id == exchange_id
            ]
        except Exception:
            accounts = [fallback_account] if fallback_account is not None else []
        if fallback_account is not None and not any(
            account.name == fallback_account.name for account in accounts
        ):
            accounts.append(fallback_account)
        if not accounts:
            raise AssetTransferError("configured exchange account was not found", status_code=404)

        hierarchy = self._standalone_hierarchy(exchange_id, accounts)
        discover = getattr(self, f"_discover_{exchange_id}_hierarchy", None)
        if callable(discover):
            try:
                hierarchy = discover(accounts, hierarchy)
            except Exception:
                # Account discovery is optional for existing same-account transfers.
                hierarchy = self._standalone_hierarchy(exchange_id, accounts)
        with self._hierarchy_cache_lock:
            self._hierarchy_cache[exchange_id] = (revision, now, hierarchy)
        return hierarchy

    def _discover_bitget_hierarchy(
        self,
        accounts: list[ExchangeAccount],
        hierarchy: TransferAccountHierarchy,
    ) -> TransferAccountHierarchy:
        identities = dict(hierarchy.accounts)
        masters: dict[str, str] = {}
        for account in accounts:
            exchange = build_exchange("bitget", account.config, self.timeout_ms, None)
            secrets = secret_values(account.config)
            try:
                response = self._read_identity(
                    exchange,
                    "Bitget account info",
                    lambda: exchange.privateSpotGetV2SpotAccountInfo({}),
                    secrets,
                )
                data = response.get("data", {}) if isinstance(response, dict) else {}
                user_id = _external_id(
                    data.get("userId") if isinstance(data, dict) else None
                )
                parent_id = _external_id(
                    data.get("parentId") if isinstance(data, dict) else None
                )
                if not user_id:
                    continue
                is_main = _is_main_identifier(parent_id, user_id)
                group_id = user_id if is_main else parent_id
                identities[account.name] = TransferAccountIdentity(
                    key=account.name,
                    label=_account_label(account),
                    role="main" if is_main else "sub",
                    group_id=group_id,
                    external_id=user_id,
                    account=account,
                )
                if is_main:
                    masters[group_id] = account.name
            except Exception:
                continue
            finally:
                try:
                    exchange.close()
                except Exception:
                    pass
        return TransferAccountHierarchy("bitget", identities, masters)

    def _discover_bybit_hierarchy(
        self,
        accounts: list[ExchangeAccount],
        hierarchy: TransferAccountHierarchy,
    ) -> TransferAccountHierarchy:
        identities = dict(hierarchy.accounts)
        masters: dict[str, str] = {}
        for account in accounts:
            exchange = build_exchange("bybit", account.config, self.timeout_ms, None)
            secrets = secret_values(account.config)
            try:
                response = self._read_identity(
                    exchange,
                    "Bybit API key info",
                    lambda: exchange.privateGetV5UserQueryApi({}),
                    secrets,
                )
                data = response.get("result", {}) if isinstance(response, dict) else {}
                user_id = _external_id(
                    data.get("userID", data.get("userId"))
                    if isinstance(data, dict)
                    else None
                )
                parent_id = _external_id(
                    data.get("parentUid", data.get("parentUID"))
                    if isinstance(data, dict)
                    else None
                )
                is_master_value = data.get("isMaster") if isinstance(data, dict) else None
                is_main = (
                    is_master_value is True
                    or str(is_master_value).lower() in {"1", "true"}
                    or _is_main_identifier(parent_id, user_id)
                )
                if not user_id:
                    continue
                group_id = user_id if is_main else parent_id
                identities[account.name] = TransferAccountIdentity(
                    key=account.name,
                    label=_account_label(account),
                    role="main" if is_main else "sub",
                    group_id=group_id,
                    external_id=user_id,
                    account=account,
                )
                if is_main:
                    masters[group_id] = account.name
            except Exception:
                continue
            finally:
                try:
                    exchange.close()
                except Exception:
                    pass
        return TransferAccountHierarchy("bybit", identities, masters)

    def _discover_okx_hierarchy(
        self,
        accounts: list[ExchangeAccount],
        hierarchy: TransferAccountHierarchy,
    ) -> TransferAccountHierarchy:
        identities = dict(hierarchy.accounts)
        masters: dict[str, str] = {}
        uid_by_account: dict[str, str] = {}
        for account in accounts:
            exchange = build_exchange("okx", account.config, self.timeout_ms, None)
            secrets = secret_values(account.config)
            try:
                response = self._read_identity(
                    exchange,
                    "OKX account config",
                    lambda: exchange.privateGetAccountConfig({}),
                    secrets,
                )
                rows = _response_rows(response, "data")
                data = rows[0] if rows else {}
                user_id = _external_id(data.get("uid"))
                main_id = _external_id(data.get("mainUid")) or user_id
                if not user_id:
                    continue
                is_main = user_id == main_id
                uid_by_account[account.name] = user_id
                identities[account.name] = TransferAccountIdentity(
                    key=account.name,
                    label=_account_label(account),
                    role="main" if is_main else "sub",
                    group_id=main_id,
                    external_id=None if is_main else user_id,
                    account=account,
                )
                if is_main:
                    masters[main_id] = account.name
            except Exception:
                continue
            finally:
                try:
                    exchange.close()
                except Exception:
                    pass

        for group_id, master_key in masters.items():
            master = identities.get(master_key)
            if master is None or master.account is None:
                continue
            exchange = build_exchange("okx", master.account.config, self.timeout_ms, None)
            secrets = secret_values(master.account.config)
            try:
                response = self._read_identity(
                    exchange,
                    "OKX sub-account list",
                    lambda: exchange.privateGetUsersSubaccountList({}),
                    secrets,
                )
                names_by_uid = {
                    _external_id(row.get("uid")): _external_id(row.get("subAcct"))
                    for row in _response_rows(response, "data")
                }
                for account_name, user_id in uid_by_account.items():
                    identity = identities.get(account_name)
                    if (
                        identity is None
                        or identity.group_id != group_id
                        or identity.role != "sub"
                    ):
                        continue
                    sub_name = names_by_uid.get(user_id)
                    if sub_name:
                        identities[account_name] = TransferAccountIdentity(
                            key=identity.key,
                            label=identity.label,
                            role=identity.role,
                            group_id=identity.group_id,
                            external_id=sub_name,
                            account=identity.account,
                        )
            except Exception:
                continue
            finally:
                try:
                    exchange.close()
                except Exception:
                    pass
        return TransferAccountHierarchy("okx", identities, masters)

    def _discover_binance_hierarchy(
        self,
        accounts: list[ExchangeAccount],
        hierarchy: TransferAccountHierarchy,
    ) -> TransferAccountHierarchy:
        candidates = sorted(
            accounts,
            key=lambda account: (
                0 if account.name in {"binance", "binance_main"} else 1,
                account.name,
            ),
        )
        master_account: ExchangeAccount | None = None
        sub_rows: list[dict[str, Any]] = []
        master_exchange: ccxt.Exchange | None = None
        master_secrets: list[str] = []
        for account in candidates:
            exchange = build_exchange("binance", account.config, self.timeout_ms, None)
            secrets = secret_values(account.config)
            try:
                response = self._read_identity(
                    exchange,
                    "Binance sub-account list",
                    lambda: exchange.sapiGetSubAccountList({}),
                    secrets,
                )
                sub_rows = _response_rows(response, "subAccounts")
                master_account = account
                master_exchange = exchange
                master_secrets = secrets
                break
            except Exception:
                try:
                    exchange.close()
                except Exception:
                    pass
        if master_account is None or master_exchange is None:
            return hierarchy

        group_id = master_account.name
        identities = dict(hierarchy.accounts)
        identities[master_account.name] = TransferAccountIdentity(
            key=master_account.name,
            label=_account_label(master_account),
            role="main",
            group_id=group_id,
            external_id=None,
            account=master_account,
        )
        row_by_hint: dict[str, dict[str, Any]] = {}
        for row in sub_rows:
            for value in (row.get("email"), row.get("subUserId")):
                hint = _external_id(value).lower()
                if hint:
                    row_by_hint[hint] = row

        try:
            rows_by_account: dict[str, dict[str, Any]] = {}
            unresolved_by_api_key: dict[str, ExchangeAccount] = {}
            for account in accounts:
                if account.name == master_account.name:
                    continue
                row = None
                for key in ("email", "uid"):
                    hint = _external_id(account.config.get(key)).lower()
                    if hint and hint in row_by_hint:
                        row = row_by_hint[hint]
                        break
                if row is not None:
                    rows_by_account[account.name] = row
                    continue
                api_key = _external_id(account.config.get("apiKey"))
                if api_key:
                    unresolved_by_api_key[api_key] = account

            for candidate in sub_rows:
                if not unresolved_by_api_key:
                    break
                email = _external_id(candidate.get("email"))
                if not email:
                    continue
                try:
                    response = self._read_identity(
                        master_exchange,
                        "Binance sub-account API key list",
                        lambda email=email: master_exchange.request(
                            "sub-account/subAccountApi",
                            "sapi",
                            "GET",
                            {"email": email, "size": 100},
                        ),
                        master_secrets,
                    )
                except Exception:
                    continue
                for item in _response_rows(
                    response,
                    "subAccountApiKeyList",
                    "data",
                ):
                    api_key = _external_id(item.get("apiKey", item.get("apikey")))
                    account = unresolved_by_api_key.pop(api_key, None)
                    if account is not None:
                        rows_by_account[account.name] = candidate

            for account_name, row in rows_by_account.items():
                email = _external_id(row.get("email"))
                if not email:
                    continue
                account = next(
                    item for item in accounts if item.name == account_name
                )
                identities[account_name] = TransferAccountIdentity(
                    key=account_name,
                    label=_account_label(account),
                    role="sub",
                    group_id=group_id,
                    external_id=email,
                    account=account,
                )
        finally:
            try:
                master_exchange.close()
            except Exception:
                pass
        return TransferAccountHierarchy(
            "binance",
            identities,
            {group_id: master_account.name},
        )

    def _discover_hyperliquid_hierarchy(
        self,
        accounts: list[ExchangeAccount],
        hierarchy: TransferAccountHierarchy,
    ) -> TransferAccountHierarchy:
        identities = dict(hierarchy.accounts)
        masters: dict[str, str] = {}
        main_clients: dict[str, tuple[ccxt.Exchange, list[str]]] = {}
        try:
            for account in accounts:
                exchange = build_exchange(
                    "hyperliquid",
                    account.config,
                    self.timeout_ms,
                    None,
                )
                secrets = secret_values(account.config)
                keep_client = False
                try:
                    address = _external_id(
                        account.config.get("walletAddress")
                        or getattr(exchange, "walletAddress", None)
                    ).lower()
                    if not _HYPERLIQUID_ADDRESS_PATTERN.fullmatch(address):
                        continue
                    response = self._read_identity(
                        exchange,
                        "Hyperliquid user role",
                        lambda address=address: exchange.publicPostInfo({
                            "type": "userRole",
                            "user": address,
                        }),
                        secrets,
                    )
                    role = (
                        _external_id(response.get("role")).lower()
                        if isinstance(response, dict)
                        else ""
                    )
                    data = response.get("data", {}) if isinstance(response, dict) else {}
                    master_address = (
                        _external_id(data.get("master")).lower()
                        if isinstance(data, dict)
                        else ""
                    )
                    is_main = role != "subaccount" or not master_address
                    group_id = address if is_main else master_address
                    identities[account.name] = TransferAccountIdentity(
                        key=account.name,
                        label=_account_label(account),
                        role="main" if is_main else "sub",
                        group_id=group_id,
                        external_id=address,
                        account=account,
                    )
                    if is_main:
                        masters[group_id] = account.name
                        main_clients[group_id] = (exchange, secrets)
                        keep_client = True
                except Exception:
                    continue
                finally:
                    if not keep_client:
                        try:
                            exchange.close()
                        except Exception:
                            pass

            configured_addresses = {
                identity.external_id
                for identity in identities.values()
                if identity.external_id
            }
            for group_id, (exchange, secrets) in main_clients.items():
                try:
                    response = self._read_identity(
                        exchange,
                        "Hyperliquid sub-account list",
                        lambda group_id=group_id: exchange.publicPostInfo({
                            "type": "subAccounts",
                            "user": group_id,
                        }),
                        secrets,
                    )
                except Exception:
                    continue
                for index, row in enumerate(_response_rows(response)):
                    address = _external_id(
                        row.get("subAccountUser", row.get("user"))
                    ).lower()
                    if (
                        not _HYPERLIQUID_ADDRESS_PATTERN.fullmatch(address)
                        or address in configured_addresses
                    ):
                        continue
                    name = _external_id(row.get("name")) or f"Sub-account {index + 1}"
                    key = f"hyperliquid_sub_{address}"
                    identities[key] = TransferAccountIdentity(
                        key=key,
                        label=name,
                        role="sub",
                        group_id=group_id,
                        external_id=address,
                    )
        finally:
            for exchange, _ in main_clients.values():
                try:
                    exchange.close()
                except Exception:
                    pass
        return TransferAccountHierarchy("hyperliquid", identities, masters)

    @staticmethod
    def _cross_destinations(
        exchange_id: str,
        source: str,
        source_account: TransferAccountIdentity,
        target_account: TransferAccountIdentity,
    ) -> tuple[str, ...]:
        if (
            source_account.key == target_account.key
            or source_account.group_id != target_account.group_id
            or source_account.role not in {"main", "sub"}
            or target_account.role not in {"main", "sub"}
        ):
            return ()

        effective_source = "spot" if source == "earn" else source
        if exchange_id == "binance":
            if effective_source == "spot":
                if source_account.role == "main":
                    return ("spot", "swap", "margin")
                if target_account.role == "main":
                    return ("spot",)
                return ("spot",)
            if effective_source == "swap":
                return ("spot",)
            if effective_source == "margin":
                if target_account.role == "main":
                    return ("spot",)
                if source_account.role == "sub":
                    return ("margin",)
            return ()
        if exchange_id == "bitget":
            return (
                ("spot", "swap", "margin")
                if target_account.role == "sub"
                else ("spot", "swap", "margin", "funding")
            )
        if exchange_id == "bybit":
            return ("spot", "funding")
        if exchange_id == "okx":
            return ("spot", "funding")
        if exchange_id == "hyperliquid" and effective_source == "swap":
            # Hyperliquid's sub-account USDC transfer moves Perps collateral.
            if source_account.role == "main" or target_account.role == "main":
                return ("swap",)
        return ()

    def _target_account_options(
        self,
        hierarchy: TransferAccountHierarchy,
        source_account: TransferAccountIdentity,
        source: str,
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for target in hierarchy.related(source_account):
            if target.key == source_account.key:
                destinations = _DESTINATIONS[hierarchy.exchange_id][source]
            else:
                master = hierarchy.master(source_account)
                destinations = self._cross_destinations(
                    hierarchy.exchange_id,
                    source,
                    source_account,
                    target,
                )
                if master is None or master.account is None:
                    destinations = ()
            if not destinations:
                continue
            options.append({
                "key": target.key,
                "label": target.label,
                "role": target.role,
                "destinations": list(destinations),
            })
        return sorted(
            options,
            key=lambda item: (
                item["key"] != source_account.key,
                item["role"] != "main",
                item["label"].lower(),
            ),
        )

    def _resolve_account_route(
        self,
        exchange_id: str,
        source_account: ExchangeAccount,
        target_name: str,
        source: str,
        destination: str,
    ) -> tuple[
        TransferAccountHierarchy,
        TransferAccountIdentity,
        TransferAccountIdentity,
    ]:
        hierarchy = self._account_hierarchy(exchange_id, source_account)
        source_identity = hierarchy.identity(source_account.name)
        target_identity = hierarchy.identity(target_name)
        if source_identity is None or target_identity is None:
            raise AssetTransferError(
                "target account was not found in the configured account hierarchy",
                status_code=404,
            )
        if source_identity.key == target_identity.key:
            self._validate_route(exchange_id, source, destination)
            return hierarchy, source_identity, target_identity
        destinations = self._cross_destinations(
            exchange_id,
            source,
            source_identity,
            target_identity,
        )
        self._validate_route(
            exchange_id,
            source,
            destination,
            allowed_destinations=destinations,
        )
        master = hierarchy.master(source_identity)
        if (
            source_identity.group_id != target_identity.group_id
            or master is None
            or master.account is None
        ):
            raise AssetTransferError(
                "the selected accounts are not managed by the same configured main account"
            )
        return hierarchy, source_identity, target_identity

    async def options(
        self,
        exchange: str,
        account: str,
        source: str,
    ) -> dict[str, Any]:
        exchange_id = str(exchange or "").strip().lower()
        source_type = str(source or "").strip().lower()
        self._validate_route(exchange_id, source_type)
        try:
            return await asyncio.to_thread(
                self._options_sync,
                exchange_id,
                account,
                source_type,
            )
        except AssetTransferError:
            raise
        except Exception as exc:
            raise AssetTransferError(
                "could not load transfer options",
                status_code=502,
            ) from exc

    def _options_sync(
        self,
        exchange_id: str,
        account_name: str,
        source: str,
    ) -> dict[str, Any]:
        account = _account_by_name(self.config_path, account_name, exchange_id)
        exchange = build_exchange(exchange_id, account.config, self.timeout_ms, None)
        secrets = secret_values(account.config)
        try:
            if exchange_id == "binance" and source == "earn":
                assets = _binance_earn_positions(
                    exchange,
                    self.read_attempts,
                    secrets,
                )
            elif exchange_id == "bitget" and source == "earn":
                assets = _bitget_earn_positions(
                    exchange,
                    self.read_attempts,
                    secrets,
                )
            else:
                balance, _ = fetch_account_type_balance(
                    exchange,
                    exchange_id,
                    source,
                    self.read_attempts,
                    secrets,
                )
                if exchange_id == "hyperliquid":
                    assets = _wallet_assets(
                        balance,
                        allowed_assets={"USDC"},
                        unsupported_note=(
                            "Hyperliquid Spot/Perp internal transfer supports USDC only."
                        ),
                    )
                else:
                    assets = _wallet_assets(balance)
            hierarchy = self._account_hierarchy(exchange_id, account)
            source_identity = hierarchy.identity(account.name)
            target_accounts = (
                self._target_account_options(hierarchy, source_identity, source)
                if source_identity is not None
                else []
            )
            if not target_accounts:
                target_accounts = [{
                    "key": account.name,
                    "label": account.name,
                    "role": "standalone",
                    "destinations": list(_DESTINATIONS[exchange_id][source]),
                }]
            return {
                "account": account.name,
                "exchange": exchange_id,
                "source": source,
                "destinations": list(_DESTINATIONS[exchange_id][source]),
                "target_accounts": target_accounts,
                "assets": assets,
                "collected_at": _utc_now(),
            }
        except AssetTransferError:
            raise
        except Exception as exc:
            raise AssetTransferError(
                f"could not load transfer options: {redact_error(exc, secrets)}",
                status_code=502,
            ) from exc
        finally:
            try:
                exchange.close()
            except Exception:
                pass

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("confirm") != "TRANSFER":
            raise AssetTransferError("explicit transfer confirmation is required")
        exchange_id = str(payload.get("exchange") or "").strip().lower()
        account_name = str(payload.get("account") or "").strip().lower()
        if not account_name:
            raise AssetTransferError("account is required")
        if exchange_id not in _SOURCES:
            raise AssetTransferError("internal transfer is not supported for this exchange")
        lock_key = exchange_id
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        if lock.locked():
            raise AssetTransferError(
                "another transfer is already running for this exchange",
                status_code=409,
            )
        async with lock:
            try:
                return await asyncio.to_thread(self._execute_sync, payload)
            except AssetTransferError:
                raise
            except Exception as exc:
                raise AssetTransferError(
                    "internal transfer failed before submission",
                    status_code=502,
                ) from exc

    def _execute_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        exchange_id = str(payload.get("exchange") or "").strip().lower()
        source = str(payload.get("source") or "").strip().lower()
        destination = str(payload.get("destination") or "").strip().lower()
        self._validate_route(exchange_id, source)
        asset = _asset_code(payload.get("asset"))
        amount = _decimal(payload.get("amount"), "amount")
        if amount <= 0:
            raise AssetTransferError("amount must be greater than zero")
        if exchange_id == "hyperliquid" and asset != "USDC":
            raise AssetTransferError("Hyperliquid transfer supports USDC only")

        account = _account_by_name(
            self.config_path,
            payload.get("account"),
            exchange_id,
        )
        target_name = str(
            payload.get("target_account") or account.name
        ).strip().lower()
        hierarchy, source_identity, target_identity = self._resolve_account_route(
            exchange_id,
            account,
            target_name,
            source,
            destination,
        )
        secrets = secret_values(account.config)
        exchange: ccxt.Exchange | None = None
        try:
            exchange = build_exchange(exchange_id, account.config, self.timeout_ms, None)
            if source_identity.key != target_identity.key:
                return self._transfer_between_accounts(
                    exchange,
                    hierarchy,
                    source_identity,
                    target_identity,
                    source,
                    destination,
                    asset,
                    amount,
                    payload,
                    secrets,
                )
            if source == "earn":
                if exchange_id == "binance":
                    return self._redeem_binance_earn(
                        exchange,
                        account,
                        destination,
                        asset,
                        amount,
                        str(payload.get("product_id") or "").strip(),
                        secrets,
                    )
                return self._redeem_bitget_earn(
                    exchange,
                    account,
                    destination,
                    asset,
                    amount,
                    str(payload.get("product_id") or "").strip(),
                    str(payload.get("period_type") or "").strip().lower(),
                    secrets,
                )
            return self._transfer_wallet(
                exchange,
                account,
                source,
                destination,
                asset,
                amount,
                secrets,
            )
        except AssetTransferError:
            raise
        except Exception as exc:
            raise AssetTransferError(
                f"transfer preflight failed: {redact_error(exc, secrets)}",
                status_code=502,
                details={"status": "failed"},
            ) from exc
        finally:
            if exchange is not None:
                try:
                    exchange.close()
                except Exception:
                    pass

    def _transfer_between_accounts(
        self,
        source_exchange: ccxt.Exchange,
        hierarchy: TransferAccountHierarchy,
        source_identity: TransferAccountIdentity,
        target_identity: TransferAccountIdentity,
        source: str,
        destination: str,
        asset: str,
        amount: Decimal,
        payload: dict[str, Any],
        source_secrets: list[str],
    ) -> dict[str, Any]:
        source_account = source_identity.account
        if source_account is None:
            raise AssetTransferError("source account is not configured")
        steps: list[dict[str, Any]] = []
        effective_source = source
        if source == "earn":
            if hierarchy.exchange_id == "binance":
                redeemed = self._redeem_binance_earn(
                    source_exchange,
                    source_account,
                    "spot",
                    asset,
                    amount,
                    str(payload.get("product_id") or "").strip(),
                    source_secrets,
                )
            elif hierarchy.exchange_id == "bitget":
                redeemed = self._redeem_bitget_earn(
                    source_exchange,
                    source_account,
                    "spot",
                    asset,
                    amount,
                    str(payload.get("product_id") or "").strip(),
                    str(payload.get("period_type") or "").strip().lower(),
                    source_secrets,
                )
            else:
                raise AssetTransferError("Earn transfer is not supported for this exchange")
            steps.extend(redeemed.get("steps", []))
            effective_source = "spot"
        else:
            balance, _ = fetch_account_type_balance(
                source_exchange,
                hierarchy.exchange_id,
                source,
                self.read_attempts,
                source_secrets,
            )
            available = _wallet_available(balance, asset)
            if amount > available:
                raise AssetTransferError(
                    f"insufficient {asset} balance in {source}: "
                    f"available {_amount_text(available)}"
                )

        master = hierarchy.master(source_identity)
        if master is None or master.account is None:
            raise AssetTransferError("configured main account is required for this transfer")
        signer_exchange = source_exchange
        signer_secrets = source_secrets
        close_signer = False
        if master.account.name != source_account.name:
            signer_exchange = build_exchange(
                hierarchy.exchange_id,
                master.account.config,
                self.timeout_ms,
                None,
            )
            signer_secrets = secret_values(master.account.config)
            close_signer = True
        route_secrets = [
            *signer_secrets,
            *(
                value
                for value in (
                    source_identity.external_id,
                    target_identity.external_id,
                )
                if value
            ),
        ]
        try:
            transaction_id, transfer_status = self._submit_account_transfer(
                signer_exchange,
                hierarchy.exchange_id,
                source_identity,
                target_identity,
                effective_source,
                destination,
                asset,
                amount,
                route_secrets,
            )
        except AssetTransferError as exc:
            if steps:
                raise AssetTransferError(
                    f"Earn redemption completed, but account transfer failed. "
                    f"Redeemed funds remain in {source_identity.label} Spot.",
                    status_code=exc.status_code,
                    details={
                        "status": "partial",
                        "completed_steps": steps,
                    },
                ) from exc
            raise
        finally:
            if close_signer:
                try:
                    signer_exchange.close()
                except Exception:
                    pass

        step: dict[str, Any] = {
            "action": "account_transfer",
            "source": effective_source,
            "destination": destination,
            "source_account": source_identity.label,
            "target_account": target_identity.label,
            "status": transfer_status,
        }
        if transaction_id:
            step["transaction_id"] = transaction_id
        steps.append(step)
        return _success_result(
            source_account,
            source,
            destination,
            asset,
            amount,
            steps,
            target_account=target_identity.key,
            target_account_label=target_identity.label,
            status="pending" if transfer_status == "pending" else "success",
        )

    def _transfer_wallet(
        self,
        exchange: ccxt.Exchange,
        account: ExchangeAccount,
        source: str,
        destination: str,
        asset: str,
        amount: Decimal,
        secrets: list[str],
    ) -> dict[str, Any]:
        balance, _ = fetch_account_type_balance(
            exchange,
            account.exchange_id,
            source,
            self.read_attempts,
            secrets,
        )
        available = _wallet_available(balance, asset)
        if amount > available:
            raise AssetTransferError(
                f"insufficient {asset} balance in {source}: "
                f"available {_amount_text(available)}"
            )

        transaction_id = self._submit_wallet_transfer(
            exchange,
            account.exchange_id,
            source,
            destination,
            asset,
            amount,
            secrets,
        )
        step: dict[str, Any] = {
            "action": "transfer",
            "source": source,
            "destination": destination,
        }
        if transaction_id:
            step["transaction_id"] = transaction_id
        return _success_result(
            account,
            source,
            destination,
            asset,
            amount,
            [step],
        )

    def _submit_wallet_transfer(
        self,
        exchange: ccxt.Exchange,
        exchange_id: str,
        source: str,
        destination: str,
        asset: str,
        amount: Decimal,
        secrets: list[str],
    ) -> str | None:
        if exchange_id == "binance":
            response = _state_change(
                lambda: exchange.sapiPostAssetTransfer({
                    "type": _BINANCE_TRANSFER_TYPES[(source, destination)],
                    "asset": asset,
                    "amount": _amount_text(amount),
                }),
                operation="Binance internal transfer",
                exchange_label="Binance",
                secrets=secrets,
            )
            transaction_id = response.get("tranId")
        elif exchange_id == "bitget":
            response = _state_change(
                lambda: exchange.privateSpotPostV2SpotWalletTransfer({
                    "fromType": _BITGET_ACCOUNT_TYPES[source],
                    "toType": _BITGET_ACCOUNT_TYPES[destination],
                    "amount": _amount_text(amount),
                    "coin": asset,
                    "clientOid": _client_id(),
                }),
                operation="Bitget internal transfer",
                exchange_label="Bitget",
                secrets=secrets,
            )
            data = response.get("data", {})
            transaction_id = data.get("transferId") if isinstance(data, dict) else None
        elif exchange_id == "bybit":
            response = _state_change(
                lambda: exchange.privatePostV5AssetTransferInterTransfer({
                    "transferId": str(uuid.uuid4()),
                    "coin": asset,
                    "amount": _amount_text(amount),
                    "fromAccountType": _BYBIT_ACCOUNT_TYPES[source],
                    "toAccountType": _BYBIT_ACCOUNT_TYPES[destination],
                }),
                operation="Bybit internal transfer",
                exchange_label="Bybit",
                secrets=secrets,
            )
            data = response.get("result", {})
            transaction_id = data.get("transferId") if isinstance(data, dict) else None
        elif exchange_id == "okx":
            response = _state_change(
                lambda: exchange.privatePostAssetTransfer({
                    "ccy": asset,
                    "amt": _amount_text(amount),
                    "type": "0",
                    "from": _OKX_ACCOUNT_TYPES[source],
                    "to": _OKX_ACCOUNT_TYPES[destination],
                    "clientId": _client_id(),
                }),
                operation="OKX internal transfer",
                exchange_label="OKX",
                secrets=secrets,
            )
            rows = response.get("data", [])
            first = rows[0] if isinstance(rows, list) and rows else {}
            transaction_id = first.get("transId") if isinstance(first, dict) else None
        else:
            response = _state_change(
                lambda: exchange.transfer(
                    asset,
                    _amount_text(amount),
                    source,
                    destination,
                ),
                operation="Hyperliquid Spot/Perp transfer",
                exchange_label="Hyperliquid",
                secrets=secrets,
            )
            if response.get("status") != "ok":
                raise AssetTransferError(
                    "Hyperliquid rejected the Spot/Perp transfer",
                    status_code=502,
                    details={"status": "failed"},
                )
            transaction_id = response.get("id")
            return str(transaction_id) if transaction_id else None

        if transaction_id is None:
            raise AssetTransferError(
                f"{exchange_id} transfer response did not include a transaction ID. "
                "Check exchange balances before trying again.",
                status_code=502,
                details={"status": "unknown"},
            )
        return str(transaction_id)

    def _submit_account_transfer(
        self,
        exchange: ccxt.Exchange,
        exchange_id: str,
        source_account: TransferAccountIdentity,
        target_account: TransferAccountIdentity,
        source: str,
        destination: str,
        asset: str,
        amount: Decimal,
        secrets: list[str],
    ) -> tuple[str | None, str]:
        amount_value = _amount_text(amount)
        if exchange_id == "binance":
            params: dict[str, Any] = {
                "fromAccountType": _BINANCE_SUB_ACCOUNT_TYPES[source],
                "toAccountType": _BINANCE_SUB_ACCOUNT_TYPES[destination],
                "asset": asset,
                "amount": amount_value,
                "clientTranId": _client_id(),
            }
            if source_account.role == "sub":
                params["fromEmail"] = source_account.external_id
            if target_account.role == "sub":
                params["toEmail"] = target_account.external_id
            response = _state_change(
                lambda: exchange.sapiPostSubAccountUniversalTransfer(params),
                operation="Binance account transfer",
                exchange_label="Binance",
                secrets=secrets,
            )
            transaction_id = response.get("tranId")
        elif exchange_id == "bitget":
            if not source_account.external_id or not target_account.external_id:
                raise AssetTransferError("Bitget account UID discovery is incomplete")
            response = _state_change(
                lambda: exchange.privateSpotPostV2SpotWalletSubaccountTransfer({
                    "fromType": _BITGET_ACCOUNT_TYPES[source],
                    "toType": _BITGET_ACCOUNT_TYPES[destination],
                    "amount": amount_value,
                    "coin": asset,
                    "fromUserId": source_account.external_id,
                    "toUserId": target_account.external_id,
                    "clientOid": _client_id(),
                }),
                operation="Bitget account transfer",
                exchange_label="Bitget",
                secrets=secrets,
            )
            data = response.get("data", {})
            transaction_id = data.get("transferId") if isinstance(data, dict) else None
        elif exchange_id == "bybit":
            from_member_id = _integer_account_id(
                source_account.external_id,
                "Bybit",
            )
            to_member_id = _integer_account_id(
                target_account.external_id,
                "Bybit",
            )
            response = _state_change(
                lambda: exchange.privatePostV5AssetTransferUniversalTransfer({
                    "transferId": str(uuid.uuid4()),
                    "coin": asset,
                    "amount": amount_value,
                    "fromMemberId": from_member_id,
                    "toMemberId": to_member_id,
                    "fromAccountType": _BYBIT_ACCOUNT_TYPES[source],
                    "toAccountType": _BYBIT_ACCOUNT_TYPES[destination],
                }),
                operation="Bybit account transfer",
                exchange_label="Bybit",
                secrets=secrets,
            )
            data = response.get("result", {})
            status = _external_id(
                data.get("status") if isinstance(data, dict) else None
            ).upper()
            if status in {"FAILED", "STATUS_UNKNOWN"}:
                raise AssetTransferError(
                    f"Bybit account transfer returned {status or 'an unknown status'}. "
                    "Check Bybit transfer history before trying again.",
                    status_code=502,
                    details={"status": "unknown" if status == "STATUS_UNKNOWN" else "failed"},
                )
            transaction_id = (
                data.get("transferId") if isinstance(data, dict) else None
            )
            transfer_status = "pending" if status == "PENDING" else "success"
            if transaction_id is None:
                raise AssetTransferError(
                    "Bybit account transfer response did not include a transfer ID. "
                    "Check Bybit before trying again.",
                    status_code=502,
                    details={"status": "unknown"},
                )
            return str(transaction_id), transfer_status
        elif exchange_id == "okx":
            common = {
                "ccy": asset,
                "amt": amount_value,
                "from": _OKX_ACCOUNT_TYPES[source],
                "to": _OKX_ACCOUNT_TYPES[destination],
            }
            if source_account.role == "sub" and target_account.role == "sub":
                if not source_account.external_id or not target_account.external_id:
                    raise AssetTransferError("OKX sub-account name discovery is incomplete")
                response = _state_change(
                    lambda: exchange.privatePostAssetSubaccountTransfer({
                        **common,
                        "fromSubAccount": source_account.external_id,
                        "toSubAccount": target_account.external_id,
                    }),
                    operation="OKX sub-account transfer",
                    exchange_label="OKX",
                    secrets=secrets,
                )
            else:
                sub_account = (
                    target_account.external_id
                    if target_account.role == "sub"
                    else source_account.external_id
                )
                if not sub_account:
                    raise AssetTransferError("OKX sub-account name discovery is incomplete")
                response = _state_change(
                    lambda: exchange.privatePostAssetTransfer({
                        **common,
                        "type": "1" if source_account.role == "main" else "2",
                        "subAcct": sub_account,
                        "clientId": _client_id(),
                    }),
                    operation="OKX account transfer",
                    exchange_label="OKX",
                    secrets=secrets,
                )
            rows = _response_rows(response, "data")
            transaction_id = rows[0].get("transId") if rows else None
        else:
            if (
                source_account.role == "main"
                and target_account.role == "sub"
                and target_account.external_id
            ):
                from_account = "main"
                to_account = target_account.external_id
            elif (
                source_account.role == "sub"
                and target_account.role == "main"
                and source_account.external_id
            ):
                from_account = source_account.external_id
                to_account = "main"
            else:
                raise AssetTransferError(
                    "Hyperliquid supports account transfer only between main and sub accounts"
                )
            response = _state_change(
                lambda: exchange.transfer(
                    asset,
                    amount_value,
                    from_account,
                    to_account,
                ),
                operation="Hyperliquid account transfer",
                exchange_label="Hyperliquid",
                secrets=secrets,
            )
            status = _external_id(response.get("status")).lower()
            if status and status not in {"ok", "success"}:
                raise AssetTransferError(
                    "Hyperliquid rejected the account transfer",
                    status_code=502,
                    details={"status": "failed"},
                )
            transaction_id = response.get("id")
            return (
                str(transaction_id) if transaction_id else None,
                "success",
            )

        if transaction_id is None:
            raise AssetTransferError(
                f"{exchange_id} account transfer response did not include a transaction ID. "
                "Check exchange balances before trying again.",
                status_code=502,
                details={"status": "unknown"},
            )
        return str(transaction_id), "success"

    def _redeem_binance_earn(
        self,
        exchange: ccxt.Exchange,
        account: ExchangeAccount,
        destination: str,
        asset: str,
        amount: Decimal,
        product_id: str,
        secrets: list[str],
    ) -> dict[str, Any]:
        if not product_id:
            raise AssetTransferError("Flexible Earn product is required")
        positions = fetch_binance_earn_rows(
            exchange,
            "sapiGetSimpleEarnFlexiblePosition",
            self.read_attempts,
            secrets,
        )
        position = next(
            (
                row
                for row in positions
                if str(row.get("productId") or "") == product_id
                and str(row.get("asset") or "").upper() == asset
            ),
            None,
        )
        if position is None:
            raise AssetTransferError("Flexible Earn position was not found")
        available = _decimal(position.get("totalAmount") or 0, "Earn balance")
        if position.get("canRedeem") is False:
            raise AssetTransferError("this Flexible Earn product cannot be redeemed now")
        if amount > available:
            raise AssetTransferError(
                f"insufficient {asset} Flexible Earn balance: "
                f"available {_amount_text(available)}"
            )

        response = _state_change(
            lambda: exchange.sapiPostSimpleEarnFlexibleRedeem({
                "productId": product_id,
                "redeemAll": False,
                "amount": _amount_text(amount),
                "destAccount": "SPOT",
            }),
            operation="Binance Flexible Earn redemption",
            exchange_label="Binance",
            secrets=secrets,
        )
        if response.get("success") is not True:
            raise AssetTransferError(
                "Binance Earn redemption response was inconclusive. "
                "Check Binance before trying again.",
                status_code=502,
                details={"status": "unknown"},
            )
        steps = [{
            "action": "redeem",
            "source": "earn",
            "destination": "spot",
            "redeem_id": str(response.get("redeemId") or ""),
        }]
        self._transfer_redeemed_funds(
            exchange,
            account,
            destination,
            asset,
            amount,
            secrets,
            steps,
        )
        return _success_result(
            account,
            "earn",
            destination,
            asset,
            amount,
            steps,
        )

    def _redeem_bitget_earn(
        self,
        exchange: ccxt.Exchange,
        account: ExchangeAccount,
        destination: str,
        asset: str,
        amount: Decimal,
        product_id: str,
        period_type: str,
        secrets: list[str],
    ) -> dict[str, Any]:
        if not product_id or period_type != "flexible":
            raise AssetTransferError("Flexible Savings product is required")
        positions = _bitget_savings_rows(
            exchange,
            "flexible",
            self.read_attempts,
            secrets,
        )
        position = next(
            (
                row
                for row in positions
                if str(row.get("productId") or "") == product_id
                and str(row.get("productCoin") or "").upper() == asset
            ),
            None,
        )
        if position is None:
            raise AssetTransferError("Bitget Flexible Savings position was not found")
        available = _decimal(position.get("holdAmount") or 0, "Savings balance")
        if str(position.get("status") or "").lower() == "in_redemption":
            raise AssetTransferError("this Savings position is already being redeemed")
        if amount > available:
            raise AssetTransferError(
                f"insufficient {asset} Flexible Savings balance: "
                f"available {_amount_text(available)}"
            )

        response = _state_change(
            lambda: exchange.privateEarnPostV2EarnSavingsRedeem({
                "periodType": "flexible",
                "productId": product_id,
                "amount": _amount_text(amount),
            }),
            operation="Bitget Flexible Savings redemption",
            exchange_label="Bitget",
            secrets=secrets,
        )
        data = response.get("data", {})
        redeem_id = data.get("orderId") if isinstance(data, dict) else None
        if redeem_id is None:
            raise AssetTransferError(
                "Bitget Savings redemption response did not include an order ID. "
                "Check Bitget before trying again.",
                status_code=502,
                details={"status": "unknown"},
            )
        steps = [{
            "action": "redeem",
            "source": "earn",
            "destination": "spot",
            "redeem_id": str(redeem_id),
        }]
        self._transfer_redeemed_funds(
            exchange,
            account,
            destination,
            asset,
            amount,
            secrets,
            steps,
        )
        return _success_result(
            account,
            "earn",
            destination,
            asset,
            amount,
            steps,
        )

    def _transfer_redeemed_funds(
        self,
        exchange: ccxt.Exchange,
        account: ExchangeAccount,
        destination: str,
        asset: str,
        amount: Decimal,
        secrets: list[str],
        steps: list[dict[str, Any]],
    ) -> None:
        if destination == "spot":
            return
        try:
            transaction_id = self._submit_wallet_transfer(
                exchange,
                account.exchange_id,
                "spot",
                destination,
                asset,
                amount,
                secrets,
            )
        except AssetTransferError as exc:
            raise AssetTransferError(
                f"Earn redemption was accepted, but transfer to {destination} did not "
                f"complete. The redeemed funds should remain in Spot. {exc}",
                status_code=502,
                details={
                    "status": "partial",
                    "account": account.name,
                    "exchange": account.exchange_id,
                    "asset": asset,
                    "amount": _amount_text(amount),
                    "completed_steps": steps,
                },
            ) from exc
        step: dict[str, Any] = {
            "action": "transfer",
            "source": "spot",
            "destination": destination,
        }
        if transaction_id:
            step["transaction_id"] = transaction_id
        steps.append(step)
