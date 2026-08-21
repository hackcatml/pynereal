from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import ccxt

from ai.scripts.asset import (
    ExchangeAccount,
    build_exchange,
    redact_error,
    secret_values,
)


TRANSFER_INITIAL_DAYS = {
    "binance": 180,
    "bitget": 90,
    "bybit": 90,
    "okx": 90,
    "hyperliquid": 365,
}
TRANSFER_REFRESH_OVERLAP_MS = 24 * 60 * 60 * 1000
TRANSFER_CACHE_TTL_SECONDS = 30 * 60

_BINANCE_ROUTES = {
    "MAIN_UMFUTURE": ("spot", "swap"),
    "MAIN_MARGIN": ("spot", "margin"),
    "MAIN_FUNDING": ("spot", "funding"),
    "UMFUTURE_MAIN": ("swap", "spot"),
    "UMFUTURE_MARGIN": ("swap", "margin"),
    "UMFUTURE_FUNDING": ("swap", "funding"),
    "MARGIN_MAIN": ("margin", "spot"),
    "MARGIN_UMFUTURE": ("margin", "swap"),
    "MARGIN_FUNDING": ("margin", "funding"),
    "FUNDING_MAIN": ("funding", "spot"),
    "FUNDING_UMFUTURE": ("funding", "swap"),
    "FUNDING_MARGIN": ("funding", "margin"),
}
_BINANCE_ACCOUNT_TYPES = {
    "SPOT": "spot",
    "USDT_FUTURE": "swap",
    "USDT_FUTURES": "swap",
    "MARGIN": "margin",
    "FUNDING": "funding",
}
_BITGET_ACCOUNT_TYPES = {
    "spot": "spot",
    "p2p": "funding",
    "usdt_futures": "swap",
    "usdc_futures": "swap",
    "coin_futures": "swap",
    "crossed_margin": "margin",
    "isolated_margin": "margin",
    "uta": "swap",
    "unified": "swap",
}
_BITGET_FROM_TYPES = {
    "spot": "spot",
    "swap": "usdt_futures",
    "margin": "crossed_margin",
    "funding": "p2p",
}
_BYBIT_ACCOUNT_TYPES = {
    "SPOT": "spot",
    "UNIFIED": "swap",
    "CONTRACT": "swap",
    "OPTION": "swap",
    "FUND": "funding",
    "INVESTMENT": "funding",
}
_OKX_ACCOUNT_TYPES = {
    "6": "funding",
    "18": "spot",
}
_OKX_ACCOUNT_TRANSFER_TYPES = {"20", "21", "22", "23"}
_NETWORK_ERRORS = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.ExchangeNotAvailable,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _iso_time(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    try:
        milliseconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return utc_now()
    if abs(milliseconds) < 10_000_000_000:
        milliseconds *= 1000
    try:
        return (
            datetime.fromtimestamp(milliseconds / 1000, UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OSError, OverflowError, ValueError):
        return utc_now()


def _decimal_text(value: Any) -> str:
    try:
        amount = abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return "0"
    if not amount.is_finite():
        return "0"
    text = format(amount, "f")
    normalized = text.rstrip("0").rstrip(".") if "." in text else text
    return normalized or "0"


def _rows(response: Any, *keys: str) -> list[dict[str, Any]]:
    value = response
    for _ in range(4):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return []
        candidate = next(
            (
                value.get(key)
                for key in (*keys, "data", "result", "rows", "list")
                if isinstance(value.get(key), (dict, list))
            ),
            None,
        )
        if candidate is None:
            return []
        value = candidate
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _stable_id(prefix: str, values: list[Any]) -> str:
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"), default=str)
    return f"{prefix}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:32]}"


def _record(
    account: str,
    exchange: str,
    transfer_id: Any,
    *,
    asset: Any,
    amount: Any,
    occurred_at: Any,
    from_type: Any,
    to_type: Any,
    status: Any = "success",
    direction: str = "internal",
    from_account: Any = "",
    to_account: Any = "",
    client_id: Any = "",
    source: str = "native",
    transfer_kind: str = "wallet",
    from_account_label: Any = "",
    to_account_label: Any = "",
    info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    occurred = _iso_time(occurred_at)
    currency = str(asset or "").strip().upper() or "UNKNOWN"
    normalized_amount = _decimal_text(amount)
    identifier = str(transfer_id or "").strip() or _stable_id(
        exchange,
        [
            account,
            occurred,
            currency,
            normalized_amount,
            from_type,
            to_type,
            from_account,
            to_account,
        ],
    )
    payload = {
        "id": identifier,
        "client_id": str(client_id or ""),
        "timestamp": int(datetime.fromisoformat(occurred.replace("Z", "+00:00")).timestamp() * 1000),
        "datetime": occurred,
        "currency": currency,
        "amount": normalized_amount,
        "direction": direction,
        "from_account": str(from_account or account),
        "to_account": str(to_account or account),
        "from_account_label": str(from_account_label or ""),
        "to_account_label": str(to_account_label or ""),
        "from_account_type": str(from_type or "").strip().lower(),
        "to_account_type": str(to_type or "").strip().lower(),
        "transfer_kind": "account" if transfer_kind == "account" else "wallet",
        "status": str(status or "unknown").strip().lower(),
        "source": source,
    }
    if info:
        payload["info"] = info
    return {
        "account": account,
        "exchange": exchange,
        "transfer_id": identifier,
        "occurred_at": occurred,
        "asset": currency,
        "amount": normalized_amount,
        "direction": direction,
        "status": payload["status"],
        "source": source,
        "payload": payload,
    }


def _request(callback: Callable[[], Any]) -> Any:
    for attempt in range(2):
        try:
            return callback()
        except _NETWORK_ERRORS:
            if attempt:
                raise
            time.sleep(1.0)
    raise RuntimeError("unreachable")


def _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (
            str(record.get("account") or ""),
            str(record.get("exchange") or ""),
            str(record.get("transfer_id") or ""),
        )
        deduplicated[key] = record
    return sorted(
        deduplicated.values(),
        key=lambda item: str(item.get("occurred_at") or ""),
        reverse=True,
    )


def _append_warning(warnings: list[str], exchange: str, exc: Exception, secrets: list[str]) -> None:
    message = redact_error(exc, secrets).replace("\n", " ").strip()
    warning = f"{exchange} transfer history is partial: {message[:300] or type(exc).__name__}"
    if warning not in warnings:
        warnings.append(warning)


def _collect_binance(
    exchange: ccxt.Exchange,
    account: ExchangeAccount,
    since_ms: int,
    until_ms: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    secrets = secret_values(account.config)
    for transfer_type, (source, destination) in _BINANCE_ROUTES.items():
        try:
            page = 1
            while page <= 20:
                response = _request(lambda page=page, transfer_type=transfer_type: (
                    exchange.sapiGetAssetTransfer({
                        "type": transfer_type,
                        "startTime": since_ms,
                        "endTime": until_ms,
                        "current": page,
                        "size": 100,
                    })
                ))
                items = _rows(response, "rows")
                for item in items:
                    records.append(_record(
                        account.name,
                        "binance",
                        item.get("tranId"),
                        asset=item.get("asset"),
                        amount=item.get("amount"),
                        occurred_at=item.get("timestamp"),
                        from_type=source,
                        to_type=destination,
                        status=item.get("status"),
                        info=item,
                    ))
                total = int(response.get("total") or 0) if isinstance(response, dict) else 0
                if len(items) < 100 or page * 100 >= total:
                    break
                page += 1
        except Exception as exc:
            _append_warning(warnings, "Binance", exc, secrets)
            break

    if account.name not in {"binance", "binance_main"}:
        return records
    try:
        window_start = since_ms
        window_ms = 29 * 24 * 60 * 60 * 1000
        while window_start <= until_ms:
            window_end = min(until_ms, window_start + window_ms)
            page = 1
            while page <= 20:
                response = _request(lambda page=page, start=window_start, end=window_end: (
                    exchange.sapiGetSubAccountUniversalTransfer({
                        "startTime": start,
                        "endTime": end,
                        "page": page,
                        "limit": 500,
                    })
                ))
                items = _rows(response, "result")
                for item in items:
                    from_email = str(item.get("fromEmail") or account.name)
                    to_email = str(item.get("toEmail") or account.name)
                    from_type = _BINANCE_ACCOUNT_TYPES.get(
                        str(item.get("fromAccountType") or "").upper(),
                        str(item.get("fromAccountType") or "").lower(),
                    )
                    to_type = _BINANCE_ACCOUNT_TYPES.get(
                        str(item.get("toAccountType") or "").upper(),
                        str(item.get("toAccountType") or "").lower(),
                    )
                    direction = (
                        "out"
                        if from_email == account.name and to_email != account.name
                        else (
                            "in"
                            if to_email == account.name and from_email != account.name
                            else "internal"
                        )
                    )
                    records.append(_record(
                        account.name,
                        "binance",
                        item.get("tranId"),
                        asset=item.get("asset"),
                        amount=item.get("qty") or item.get("amount"),
                        occurred_at=item.get("time") or item.get("timestamp"),
                        from_type=from_type,
                        to_type=to_type,
                        status=item.get("status"),
                        direction=direction,
                        from_account=from_email,
                        to_account=to_email,
                        transfer_kind="account",
                        client_id=item.get("clientTranId"),
                        info=item,
                    ))
                if len(items) < 500:
                    break
                page += 1
            window_start = window_end + 1
    except Exception as exc:
        _append_warning(warnings, "Binance account transfer", exc, secrets)
    return records


def _collect_bitget(
    exchange: ccxt.Exchange,
    account: ExchangeAccount,
    since_ms: int,
    until_ms: int,
    asset_hints: list[str],
    account_type_hints: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    secrets = secret_values(account.config)
    currencies = list(dict.fromkeys([
        *[str(value).upper() for value in asset_hints if value],
        "USDT",
        "USDC",
    ]))[:20]
    source_types = list(dict.fromkeys(
        _BITGET_FROM_TYPES[value]
        for value in account_type_hints
        if value in _BITGET_FROM_TYPES
    )) or ["spot", "usdt_futures", "crossed_margin", "p2p"]
    try:
        for currency in currencies:
            for source_type in source_types:
                older_than = ""
                for _ in range(10):
                    params: dict[str, Any] = {
                        "coin": currency,
                        "fromType": source_type,
                        "startTime": since_ms,
                        "endTime": until_ms,
                        "limit": 500,
                    }
                    if older_than:
                        params["idLessThan"] = older_than
                    response = _request(
                        lambda params=params: exchange.privateSpotGetV2SpotAccountTransferRecords(params)
                    )
                    items = _rows(response, "data")
                    for item in items:
                        records.append(_record(
                            account.name,
                            "bitget",
                            item.get("transferId") or item.get("newTransferId"),
                            asset=item.get("coin"),
                            amount=item.get("size") or item.get("amount"),
                            occurred_at=item.get("ts") or item.get("createdTime"),
                            from_type=_BITGET_ACCOUNT_TYPES.get(
                                str(item.get("fromType") or "").lower(),
                                item.get("fromType"),
                            ),
                            to_type=_BITGET_ACCOUNT_TYPES.get(
                                str(item.get("toType") or "").lower(),
                                item.get("toType"),
                            ),
                            status=item.get("status"),
                            client_id=item.get("clientOid"),
                            info=item,
                        ))
                    if len(items) < 500:
                        break
                    older_than = str(items[-1].get("transferId") or "")
                    if not older_than:
                        break
    except Exception as exc:
        _append_warning(warnings, "Bitget", exc, secrets)

    for role, direction in (("initiator", "out"), ("receiver", "in")):
        try:
            older_than = ""
            for _ in range(10):
                params = {
                    "role": role,
                    "startTime": since_ms,
                    "endTime": until_ms,
                    "limit": 100,
                }
                if older_than:
                    params["idLessThan"] = older_than
                response = _request(lambda params=params: exchange.request(
                    "v2/spot/account/sub-main-trans-record",
                    ["private", "spot"],
                    "GET",
                    params,
                ))
                items = _rows(response, "list")
                for item in items:
                    records.append(_record(
                        account.name,
                        "bitget",
                        item.get("newTransferId") or item.get("transferId"),
                        asset=item.get("coin"),
                        amount=item.get("size") or item.get("amount"),
                        occurred_at=item.get("ts") or item.get("createdTime"),
                        from_type=_BITGET_ACCOUNT_TYPES.get(
                            str(item.get("fromType") or "").lower(),
                            item.get("fromType"),
                        ),
                        to_type=_BITGET_ACCOUNT_TYPES.get(
                            str(item.get("toType") or "").lower(),
                            item.get("toType"),
                        ),
                        status=item.get("status"),
                        direction=direction,
                        from_account=item.get("fromUserId"),
                        to_account=item.get("toUserId"),
                        transfer_kind="account",
                        client_id=item.get("clientOid"),
                        info=item,
                    ))
                next_older_than = str(
                    items[-1].get("transferId") or items[-1].get("newTransferId") or ""
                ) if items else ""
                if (
                    len(items) < 100
                    or not next_older_than
                    or next_older_than == older_than
                ):
                    break
                older_than = next_older_than
        except Exception as exc:
            _append_warning(warnings, "Bitget account transfer", exc, secrets)
            break
    return records


def _bybit_pages(
    exchange: ccxt.Exchange,
    method: Callable[[dict[str, Any]], Any],
    since_ms: int,
    until_ms: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    window_ms = 7 * 24 * 60 * 60 * 1000
    start = since_ms
    while start <= until_ms:
        end = min(until_ms, start + window_ms - 1000)
        cursor = ""
        for _ in range(20):
            params: dict[str, Any] = {
                "startTime": start,
                "endTime": end,
                "limit": 50,
            }
            if cursor:
                params["cursor"] = cursor
            response = _request(lambda params=params: method(params))
            items = _rows(response, "list")
            records.extend(items)
            result = response.get("result", {}) if isinstance(response, dict) else {}
            next_cursor = str(result.get("nextPageCursor") or "") if isinstance(result, dict) else ""
            if len(items) < 50 or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        start = end + 1000
    return records


def _collect_bybit(
    exchange: ccxt.Exchange,
    account: ExchangeAccount,
    since_ms: int,
    until_ms: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    secrets = secret_values(account.config)
    own_uid = str(account.config.get("uid") or "")
    if not own_uid:
        try:
            response = _request(lambda: exchange.privateGetV5UserQueryApi({}))
            data = response.get("result", {}) if isinstance(response, dict) else {}
            own_uid = str(data.get("userID") or data.get("userId") or "")
        except Exception:
            own_uid = ""
    try:
        for item in _bybit_pages(
            exchange,
            exchange.privateGetV5AssetTransferQueryInterTransferList,
            since_ms,
            until_ms,
        ):
            records.append(_record(
                account.name,
                "bybit",
                item.get("transferId"),
                asset=item.get("coin"),
                amount=item.get("amount"),
                occurred_at=item.get("timestamp"),
                from_type=_BYBIT_ACCOUNT_TYPES.get(
                    str(item.get("fromAccountType") or "").upper(),
                    item.get("fromAccountType"),
                ),
                to_type=_BYBIT_ACCOUNT_TYPES.get(
                    str(item.get("toAccountType") or "").upper(),
                    item.get("toAccountType"),
                ),
                status=item.get("status"),
                info=item,
            ))
    except Exception as exc:
        _append_warning(warnings, "Bybit", exc, secrets)
    try:
        for item in _bybit_pages(
            exchange,
            exchange.privateGetV5AssetTransferQueryUniversalTransferList,
            since_ms,
            until_ms,
        ):
            from_uid = str(item.get("fromMemberId") or "")
            to_uid = str(item.get("toMemberId") or "")
            direction = "out" if own_uid and from_uid == own_uid else (
                "in" if own_uid and to_uid == own_uid else "internal"
            )
            records.append(_record(
                account.name,
                "bybit",
                item.get("transferId"),
                asset=item.get("coin"),
                amount=item.get("amount"),
                occurred_at=item.get("timestamp"),
                from_type=_BYBIT_ACCOUNT_TYPES.get(
                    str(item.get("fromAccountType") or "").upper(),
                    item.get("fromAccountType"),
                ),
                to_type=_BYBIT_ACCOUNT_TYPES.get(
                    str(item.get("toAccountType") or "").upper(),
                    item.get("toAccountType"),
                ),
                status=item.get("status"),
                direction=direction,
                from_account=from_uid,
                to_account=to_uid,
                transfer_kind="account",
                info=item,
            ))
    except Exception as exc:
        _append_warning(warnings, "Bybit account transfer", exc, secrets)
    return records


def _okx_bill_pages(
    method: Callable[[dict[str, Any]], Any],
    since_ms: int,
    until_ms: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    after = ""
    for _ in range(20):
        params: dict[str, Any] = {
            "type": "1",
            "begin": since_ms,
            "end": until_ms,
            "limit": 100,
        }
        if after:
            params["after"] = after
        response = _request(lambda params=params: method(params))
        items = _rows(response, "data")
        records.extend(items)
        next_after = str(items[-1].get("billId") or "") if items else ""
        if len(items) < 100 or not next_after or next_after == after:
            break
        after = next_after
    return records


def _okx_asset_bill_pages(
    method: Callable[[dict[str, Any]], Any],
    since_ms: int,
    until_ms: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    after = str(until_ms + 1)
    before = str(max(0, since_ms - 1))
    for page in range(20):
        params: dict[str, Any] = {
            "after": after,
            "before": before,
            "limit": 100,
            "pagingType": "1",
        }
        response = _request(lambda params=params: method(params))
        items = _rows(response, "data")
        records.extend(
            item
            for item in items
            if str(item.get("type") or "") in _OKX_ACCOUNT_TRANSFER_TYPES
        )
        timestamps = [
            int(item["ts"])
            for item in items
            if str(item.get("ts") or "").isdigit()
        ]
        next_after = str(min(timestamps)) if timestamps else ""
        if (
            len(items) < 100
            or not next_after
            or next_after == after
            or int(next_after) <= since_ms
        ):
            break
        after = next_after
        if page < 19:
            time.sleep(1.05)
    return records


def _okx_account_transfer_route(
    account: ExchangeAccount,
    bill_type: str,
) -> tuple[str, str, str]:
    if bill_type == "20":
        return account.name, "sub_account", "out"
    if bill_type == "21":
        return "sub_account", account.name, "in"
    if bill_type == "22":
        return account.name, "main_account", "out"
    return "main_account", account.name, "in"


def _collect_okx(
    exchange: ccxt.Exchange,
    account: ExchangeAccount,
    since_ms: int,
    until_ms: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    secrets = secret_values(account.config)
    bills: list[dict[str, Any]] = []
    successes = 0
    for label, method in (
        ("recent", exchange.privateGetAccountBills),
        ("archive", exchange.privateGetAccountBillsArchive),
    ):
        try:
            bills.extend(_okx_bill_pages(method, since_ms, until_ms))
            successes += 1
        except Exception as exc:
            _append_warning(warnings, f"OKX {label}", exc, secrets)
    if not successes and warnings:
        return records
    for item in bills:
        source = _OKX_ACCOUNT_TYPES.get(str(item.get("from") or ""), item.get("from"))
        destination = _OKX_ACCOUNT_TYPES.get(str(item.get("to") or ""), item.get("to"))
        amount = item.get("sz") or item.get("balChg")
        records.append(_record(
            account.name,
            "okx",
            item.get("billId") or item.get("transId"),
            asset=item.get("ccy"),
            amount=amount,
            occurred_at=item.get("ts"),
            from_type=source,
            to_type=destination,
            status="success",
            from_account=item.get("fromSubAcct") or account.name,
            to_account=item.get("toSubAcct") or item.get("subAcct") or account.name,
            client_id=item.get("clientId"),
            info=item,
        ))

    try:
        asset_bills = _okx_asset_bill_pages(
            exchange.privateGetAssetBillsHistory,
            since_ms,
            until_ms,
        )
    except Exception as exc:
        _append_warning(warnings, "OKX account transfer", exc, secrets)
        return records
    for item in asset_bills:
        bill_type = str(item.get("type") or "")
        from_account, to_account, direction = _okx_account_transfer_route(
            account,
            bill_type,
        )
        records.append(_record(
            account.name,
            "okx",
            f"asset:{item.get('billId')}",
            asset=item.get("ccy"),
            amount=item.get("balChg"),
            occurred_at=item.get("ts"),
            from_type="funding",
            to_type="funding",
            status="success",
            direction=direction,
            from_account=from_account,
            to_account=to_account,
            transfer_kind="account",
            client_id=item.get("clientId"),
            info=item,
        ))
    return records


def _collect_hyperliquid(
    exchange: ccxt.Exchange,
    account: ExchangeAccount,
    since_ms: int,
    until_ms: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    address = str(
        account.config.get("walletAddress")
        or getattr(exchange, "walletAddress", "")
        or ""
    ).strip().lower()
    if not address:
        warnings.append("Hyperliquid transfer history is partial: walletAddress is missing")
        return []
    secrets = secret_values(account.config)
    try:
        response = _request(lambda: exchange.publicPostInfo({
            "type": "userNonFundingLedgerUpdates",
            "user": address,
            "startTime": since_ms,
            "endTime": until_ms,
        }))
    except Exception as exc:
        _append_warning(warnings, "Hyperliquid", exc, secrets)
        return []
    records: list[dict[str, Any]] = []
    for item in _rows(response):
        delta = item.get("delta")
        if not isinstance(delta, dict):
            continue
        kind = str(delta.get("type") or "")
        if kind not in {
            "internalTransfer",
            "subAccountTransfer",
            "spotTransfer",
            "accountClassTransfer",
        }:
            continue
        sender = str(delta.get("user") or address).lower()
        recipient = str(delta.get("destination") or address).lower()
        direction = "out" if sender == address and recipient != address else (
            "in" if recipient == address and sender != address else "internal"
        )
        if kind == "accountClassTransfer":
            to_perp = bool(delta.get("toPerp"))
            source = "spot" if to_perp else "swap"
            destination = "swap" if to_perp else "spot"
            asset = "USDC"
            amount = delta.get("usdc")
            direction = "internal"
        elif kind == "spotTransfer":
            source = "spot"
            destination = "spot"
            asset = delta.get("token")
            amount = delta.get("amount")
        else:
            source = "swap"
            destination = "swap"
            asset = "USDC"
            amount = delta.get("usdc")
        records.append(_record(
            account.name,
            "hyperliquid",
            _stable_id(
                "hyperliquid",
                [item.get("hash"), item.get("time"), kind, delta],
            ),
            asset=asset,
            amount=amount,
            occurred_at=item.get("time"),
            from_type=source,
            to_type=destination,
            status="success",
            direction=direction,
            from_account=sender,
            to_account=recipient,
            transfer_kind="account" if kind == "subAccountTransfer" else "wallet",
            info=item,
        ))
    return records


def collect_transfer_history(
    account: ExchangeAccount,
    *,
    since_ms: int,
    until_ms: int,
    asset_hints: list[str] | None = None,
    account_type_hints: list[str] | None = None,
    timeout_ms: int = 30_000,
) -> dict[str, Any]:
    exchange_id = account.exchange_id
    collector = {
        "binance": _collect_binance,
        "bitget": _collect_bitget,
        "bybit": _collect_bybit,
        "okx": _collect_okx,
        "hyperliquid": _collect_hyperliquid,
    }.get(exchange_id)
    if collector is None:
        raise ValueError(f"transfer history is not supported for {exchange_id}")
    exchange = build_exchange(exchange_id, account.config, timeout_ms, None)
    warnings: list[str] = []
    try:
        if exchange_id == "bitget":
            records = collector(
                exchange,
                account,
                since_ms,
                until_ms,
                list(asset_hints or []),
                list(account_type_hints or []),
                warnings,
            )
        else:
            records = collector(
                exchange,
                account,
                since_ms,
                until_ms,
                warnings,
            )
    finally:
        try:
            exchange.close()
        except Exception:
            pass
    records = _deduplicate(records)
    return {
        "account": account.name,
        "exchange": exchange_id,
        "records": records,
        "warnings": warnings,
        "partial": bool(warnings),
        "since_ms": since_ms,
        "until_ms": until_ms,
    }


def records_from_transfer_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    account = str(result.get("account") or "").strip()
    target_account = str(result.get("target_account") or account).strip() or account
    exchange = str(result.get("exchange") or "").strip().lower()
    occurred_at = result.get("completed_at") or utc_now()
    asset = result.get("asset")
    amount = result.get("amount")
    records: list[dict[str, Any]] = []
    for index, step in enumerate(result.get("steps") or []):
        if not isinstance(step, dict) or step.get("action") not in {
            "transfer",
            "account_transfer",
            "redeem",
        }:
            continue
        identifier = (
            step.get("transaction_id")
            or step.get("redeem_id")
            or _stable_id(
                f"local-{exchange}",
                [account, target_account, occurred_at, asset, amount, index],
            )
        )
        is_account_transfer = step.get("action") == "account_transfer"
        source_record = _record(
            account,
            exchange,
            identifier,
            asset=asset,
            amount=amount,
            occurred_at=occurred_at,
            from_type=step.get("source"),
            to_type=step.get("destination"),
            status=step.get("status") or result.get("status"),
            direction="out" if is_account_transfer else "internal",
            from_account=account,
            to_account=target_account,
            source="local",
            transfer_kind="account" if is_account_transfer else "wallet",
            from_account_label=step.get("source_account"),
            to_account_label=step.get("target_account"),
            info=step,
        )
        records.append(source_record)
        if is_account_transfer and target_account != account:
            records.append(_record(
                target_account,
                exchange,
                identifier,
                asset=asset,
                amount=amount,
                occurred_at=occurred_at,
                from_type=step.get("source"),
                to_type=step.get("destination"),
                status=step.get("status") or result.get("status"),
                direction="in",
                from_account=account,
                to_account=target_account,
                source="local",
                transfer_kind="account",
                from_account_label=step.get("source_account"),
                to_account_label=step.get("target_account"),
                info=step,
            ))
    return _deduplicate(records)
