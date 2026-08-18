from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import ssl
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    import certifi
except ModuleNotFoundError:  # pragma: no cover - provider extras install certifi
    certifi = None

from account_service.history import reconstruct_position_history_from_fills


MAX_IMPORT_FILES = 32
MAX_IMPORT_FILE_BYTES = 32 * 1024 * 1024
MAX_IMPORT_TOTAL_BYTES = 96 * 1024 * 1024
DEFAULT_TIMEZONE = "UTC+09:00"
TIMEZONE_CONFIRMATION_WARNING = "Confirm the source timezone before importing."


class HistoryCsvError(ValueError):
    pass


def _clean(value: Any) -> str:
    return str("" if value is None else value).replace("\ufeff", "").strip()


def _decimal(value: Any) -> Decimal | None:
    text = _clean(value).replace(",", "")
    if not text:
        return None
    match = re.match(r"^[\t ]*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", text)
    if match is None:
        return None
    try:
        result = Decimal(match.group(1))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _number_text(value: Any, default: str | None = None) -> str | None:
    parsed = _decimal(value)
    if parsed is None:
        return default
    return format(parsed, "f")


def _sum_decimal(*values: Any) -> Decimal | None:
    parsed = [_decimal(value) for value in values]
    if any(value is None for value in parsed):
        return None
    return sum((value for value in parsed if value is not None), Decimal(0))


def _timezone_name(offset_minutes: int) -> str:
    sign = "+" if offset_minutes >= 0 else "-"
    total = abs(offset_minutes)
    return f"UTC{sign}{total // 60:02d}:{total % 60:02d}"


def timezone_options() -> list[str]:
    return [_timezone_name(offset * 60) for offset in range(-12, 15)]


def _timezone_offset(value: str) -> int:
    match = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", _clean(value).upper())
    if match is None:
        raise HistoryCsvError(f"invalid source timezone: {value}")
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if hours > 14 or minutes > 59:
        raise HistoryCsvError(f"invalid source timezone: {value}")
    total = hours * 60 + minutes
    return total if match.group(1) == "+" else -total


def _infer_timezone(
    name: str,
    metadata_rows: list[list[str]],
    exchange: str,
) -> tuple[str, bool]:
    filename_match = re.search(
        r"UTC\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?",
        name,
        flags=re.IGNORECASE,
    )
    if filename_match is not None:
        sign = 1 if filename_match.group(1) == "+" else -1
        minutes = int(filename_match.group(2)) * 60 + int(filename_match.group(3) or 0)
        return _timezone_name(sign * minutes), False

    metadata = " ".join(",".join(row) for row in metadata_rows)
    okx_match = re.search(r'"userTimeZone"\s*:\s*"?([+-]?\d{1,2})', metadata)
    if okx_match is not None:
        return _timezone_name(int(okx_match.group(1)) * 60), False

    return DEFAULT_TIMEZONE, exchange in {"bitget", "hyperliquid"}


def _infer_source_account_uid(
    metadata_rows: list[list[str]],
    exchange: str,
) -> str:
    if exchange != "okx":
        return ""
    for row in metadata_rows:
        for value in row:
            match = re.fullmatch(r"UID\s*:\s*(\d+)", _clean(value), flags=re.IGNORECASE)
            if match is not None:
                return match.group(1)
    return ""


def _parse_timestamp(value: Any, source_timezone: str) -> tuple[str, int]:
    text = _clean(value)
    if not text:
        raise HistoryCsvError("timestamp is empty")
    parsed: datetime | None = None
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%m/%d/%Y - %H:%M:%S",
        "%m/%d/%Y - %H:%M:%S.%f",
    ):
        try:
            parsed = datetime.strptime(text, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HistoryCsvError(f"unsupported timestamp: {text}") from exc
    if parsed.tzinfo is None:
        offset = _timezone_offset(source_timezone)
        parsed = parsed.replace(tzinfo=timezone(timedelta(minutes=offset)))
    utc_value = parsed.astimezone(UTC)
    iso = utc_value.isoformat().replace("+00:00", "Z")
    return iso, int(utc_value.timestamp() * 1000)


def _stable_id(prefix: str, *values: Any) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _linear_symbol(value: Any, settlement_hint: str = "") -> str:
    raw = _clean(value).upper().replace(" ", "")
    if not raw:
        return ""
    if "/" in raw:
        return raw if ":" in raw else f"{raw}:{raw.rsplit('/', 1)[1]}"
    for quote in tuple(filter(None, (settlement_hint.upper(), "USDT", "USDC", "BUSD", "USD"))):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}/{quote}:{quote}"
    return raw


def _okx_symbol(value: Any, instrument: Any = "") -> str:
    raw = _clean(value).upper()
    parts = raw.split("-")
    instrument_type = _clean(instrument).upper()
    if len(parts) < 2:
        return raw
    base, quote = parts[0], parts[1]
    if instrument_type in {"SWAP", "FUTURES"} or "SWAP" in parts:
        suffix = [part for part in parts[2:] if part != "SWAP"]
        settlement = quote
        contract = settlement + (f"-{'-'.join(suffix)}" if suffix else "")
        return f"{base}/{quote}:{contract}"
    return f"{base}/{quote}"


def _hyperliquid_symbol(value: Any) -> tuple[str, str]:
    raw = _clean(value)
    if not raw:
        return "", "default"
    display_match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", raw)
    if display_match is not None:
        coin = display_match.group(1).strip().upper()
        dex_name = display_match.group(2).strip().lower() or "default"
        return f"{dex_name.upper()}-{coin}/USDC:USDC", dex_name
    if "/" in raw:
        return raw.upper(), "spot"
    if ":" in raw:
        dex, coin = raw.split(":", 1)
        dex_name = dex.strip().lower() or "default"
        return f"{dex_name.upper()}-{coin.strip().upper()}/USDC:USDC", dex_name
    return f"{raw.upper()}/USDC:USDC", "default"


def canonicalize_hyperliquid_symbol(
    symbol: str,
    aliases: dict[tuple[str, str], tuple[str, str]],
) -> tuple[str, str]:
    normalized = _clean(symbol).upper()
    if not normalized:
        return "", "default"
    if "/" in normalized and ":" not in normalized:
        return normalized, "spot"
    base = normalized.split("/", 1)[0]
    if "-" not in base:
        return normalized, "default"
    dex_name, coin = base.split("-", 1)
    return aliases.get(
        (dex_name.lower(), coin),
        (normalized, dex_name.lower()),
    )


def canonicalize_hyperliquid_import_batch(
    batch: dict[str, Any],
    account: str,
    aliases: dict[tuple[str, str], tuple[str, str]],
) -> None:
    for category in ("orders", "fills", "positions"):
        records = batch.get(category)
        if not isinstance(records, list):
            continue
        for record in records:
            if (
                not isinstance(record, dict)
                or record.get("account") != account
                or record.get("exchange") != "hyperliquid"
            ):
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            symbol, scope = canonicalize_hyperliquid_symbol(
                str(payload.get("symbol") or ""),
                aliases,
            )
            payload["symbol"] = symbol
            record["market_scope"] = scope
            if category == "positions":
                record["dex"] = scope

    events = batch.get("pnl_events")
    if not isinstance(events, list):
        return
    for record in events:
        if (
            not isinstance(record, dict)
            or record.get("account") != account
            or record.get("exchange") != "hyperliquid"
        ):
            continue
        symbol, _ = canonicalize_hyperliquid_symbol(
            str(record.get("symbol") or ""),
            aliases,
        )
        record["symbol"] = symbol


def _rows_from_bytes(content: bytes) -> tuple[list[list[str]], str]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HistoryCsvError("CSV must be UTF-8 or UTF-8 BOM") from exc
    rows = [
        [_clean(cell) for cell in row]
        for row in csv.reader(io.StringIO(text, newline=""))
        if any(_clean(cell) for cell in row)
    ]
    if not rows:
        raise HistoryCsvError("CSV is empty")
    return rows, text


_SIGNATURES: list[tuple[str, str, set[str]]] = [
    ("binance", "position_history", {"Symbol", "Position Side", "Closing PNL", "Opened", "Closed"}),
    ("binance", "order_history", {"Uid", "Order No", "Update Time", "Executed Amount"}),
    ("binance", "trade_history", {"Trade ID", "Order ID", "Realized Profit", "Quantity"}),
    ("binance", "transaction_history", {"Transaction ID", "Type", "Amount", "Asset"}),
    ("bitget", "position_history", {"Futures", "Opening time", "Position Pnl", "Closed time"}),
    (
        "bitget",
        "order_history",
        {"Order ID", "Direction", "Futures", "Transaction type", "NetProfits"},
    ),
    (
        "okx",
        "position_history",
        {"Position Create Time", "Position Update Time", "Business Line", "Instrument Name"},
    ),
    ("okx", "order_history", {"Order ID", "Order Time", "Instrument", "Order Amount", "Bot ID"}),
    (
        "okx",
        "trade_details",
        {"Order ID", "Trade ID", "Trade Time", "Filled Amount", "taker/maker"},
    ),
    ("hyperliquid", "trade_history", {"time", "coin", "dir", "px", "sz", "closedPnl"}),
    ("hyperliquid", "funding_history", {"time", "coin", "sz", "side", "payment", "rate"}),
]


def _detect_format(rows: list[list[str]]) -> tuple[str, str, int]:
    for header_index, row in enumerate(rows[:4]):
        fields = set(row)
        for exchange, file_type, signature in _SIGNATURES:
            if signature.issubset(fields):
                return exchange, file_type, header_index
    raise HistoryCsvError("unsupported account history CSV format")


def _dict_rows(rows: list[list[str]], header_index: int) -> list[dict[str, str]]:
    header = rows[header_index]
    results: list[dict[str, str]] = []
    for values in rows[header_index + 1:]:
        if not any(values):
            continue
        padded = [*values, *([""] * max(0, len(header) - len(values)))]
        results.append(dict(zip(header, padded, strict=False)))
    return results


def _position_record(
    *,
    exchange: str,
    market_scope: str,
    symbol: str,
    side: str,
    opened_at: str,
    closed_at: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    position_key = _stable_id(
        "csv-position",
        exchange,
        market_scope,
        symbol,
        side,
        opened_at,
        closed_at,
    )
    return {
        "exchange": exchange,
        "market_scope": market_scope,
        "position_key": position_key,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "source": f"csv:{exchange}",
        "payload": {"symbol": symbol, "side": side, **payload},
    }


def _order_record(
    exchange: str,
    market_scope: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "market_scope": market_scope,
        "source": f"csv:{exchange}",
        "payload": payload,
    }


def _fill_record(
    exchange: str,
    market_scope: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "market_scope": market_scope,
        "source": f"csv:{exchange}",
        "payload": payload,
    }


def _event_record(
    exchange: str,
    event_id: str,
    event_type: str,
    symbol: str,
    occurred_at: str,
    amount: str,
    currency: str,
    component: str,
) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "event_id": event_id,
        "event_type": event_type,
        "symbol": symbol,
        "occurred_at": occurred_at,
        "payload": {
            "amount": amount,
            "currency": currency,
            "component": component,
            "count_in_pnl": True,
            "canonical_source": f"csv:{exchange}",
        },
    }


def _parse_binance(
    file_type: str,
    rows: list[dict[str, str]],
    source_timezone: str,
) -> dict[str, list[dict[str, Any]]]:
    result = {"orders": [], "fills": [], "positions": [], "pnl_events": []}
    for index, row in enumerate(rows, start=1):
        if file_type == "position_history":
            opened_at, _ = _parse_timestamp(row.get("Opened"), source_timezone)
            closed_at, _ = _parse_timestamp(row.get("Closed"), source_timezone)
            symbol = _linear_symbol(row.get("Symbol"), "USDT")
            side = _clean(row.get("Position Side")).lower()
            gross = _number_text(row.get("Closing PNL"))
            payload = {
                "quantity": _number_text(row.get("Closed Vol.")),
                "contracts": _number_text(row.get("Closed Vol.")),
                "entry_price": _number_text(row.get("Entry Price")),
                "exit_price": _number_text(row.get("Avg. Close Price")),
                "realized_pnl": gross,
                "realized_pnl_breakdown": {
                    "gross_pnl": gross,
                    "fees": None,
                    "net_pnl": gross,
                    "currency": symbol.rsplit(":", 1)[-1],
                    "complete": False,
                },
                "margin_mode": _clean(row.get("Margin Mode")).lower(),
                "opened_at_known": True,
                "native_id": None,
                "source_row": index,
            }
            result["positions"].append(_position_record(
                exchange="binance",
                market_scope="usd_m",
                symbol=symbol,
                side=side,
                opened_at=opened_at,
                closed_at=closed_at,
                payload=payload,
            ))
        elif file_type == "order_history":
            created_at, created_ms = _parse_timestamp(row.get("Time"), source_timezone)
            updated_at, updated_ms = _parse_timestamp(
                row.get("Update Time") or row.get("Time"),
                source_timezone,
            )
            symbol = _linear_symbol(row.get("Symbol"), "USDT")
            amount = _number_text(row.get("Amount"))
            filled = _number_text(row.get("Executed Amount"))
            remaining = None
            amount_decimal = _decimal(amount)
            filled_decimal = _decimal(filled)
            if amount_decimal is not None and filled_decimal is not None:
                remaining = format(amount_decimal - filled_decimal, "f")
            result["orders"].append(_order_record("binance", "usd_m", {
                "id": _clean(row.get("Order No")),
                "client_order_id": "",
                "symbol": symbol,
                "type": _clean(row.get("Type")).lower(),
                "side": _clean(row.get("Side")).lower(),
                "status": _clean(row.get("Status")).lower(),
                "price": _number_text(row.get("Price")),
                "average_price": _number_text(row.get("Average Price")),
                "trigger_price": _number_text(row.get("Stop Price")),
                "amount": amount,
                "filled": filled,
                "remaining": remaining,
                "cost": _number_text(row.get("Executed Quote Amount")),
                "timestamp": created_ms,
                "datetime": created_at,
                "updated_timestamp": updated_ms,
                "source_row": index,
            }))
        elif file_type == "trade_history":
            occurred_at, occurred_ms = _parse_timestamp(row.get("Time"), source_timezone)
            symbol = _linear_symbol(row.get("Symbol"), "USDT")
            fee_text = _number_text(row.get("Fee"), "0") or "0"
            fee_currency_match = re.search(r"([A-Za-z]+)$", _clean(row.get("Fee")))
            fee_currency = fee_currency_match.group(1).upper() if fee_currency_match else "USDT"
            result["fills"].append(_fill_record("binance", "usd_m", {
                "id": _clean(row.get("Trade ID")),
                "order_id": _clean(row.get("Order ID")),
                "symbol": symbol,
                "side": _clean(row.get("Side")).lower(),
                "price": _number_text(row.get("Price")),
                "amount": _number_text(row.get("Quantity")),
                "cost": _number_text(row.get("Amount")),
                "timestamp": occurred_ms,
                "datetime": occurred_at,
                "fee": {"cost": fee_text, "currency": fee_currency},
                "fees": [],
                "position_side": "BOTH",
                "realized_pnl": _number_text(row.get("Realized Profit"), "0"),
                "source_row": index,
            }))
        elif file_type == "transaction_history":
            event_type = _clean(row.get("Type")).upper()
            if event_type != "FUNDING_FEE":
                continue
            occurred_at, _ = _parse_timestamp(row.get("Time"), source_timezone)
            asset = _clean(row.get("Asset")).upper()
            symbol = _linear_symbol(row.get("Symbol"), asset)
            event_id = _clean(row.get("Transaction ID")) or _stable_id(
                "binance-funding",
                occurred_at,
                symbol,
                row.get("Amount"),
            )
            result["pnl_events"].append(_event_record(
                "binance",
                event_id,
                "funding",
                symbol,
                occurred_at,
                _number_text(row.get("Amount"), "0") or "0",
                asset or "USDT",
                "funding",
            ))
    return result


def _bitget_future_parts(value: Any) -> tuple[str, str, str]:
    text = _clean(value)
    chunks = text.split(maxsplit=1)
    symbol = _linear_symbol(chunks[0] if chunks else "", "USDT")
    side = ""
    margin_mode = ""
    if len(chunks) > 1:
        details = chunks[1].split("·")
        side = _clean(details[0]).lower()
        margin_mode = _clean(details[1] if len(details) > 1 else "").lower()
    return symbol, side, margin_mode


def _bitget_status(value: Any) -> str:
    status = _clean(value).lower()
    return {
        "fully executed": "closed",
        "partially executed": "open",
        "cancelled": "canceled",
        "canceled": "canceled",
    }.get(status, status)


def _parse_bitget(
    file_type: str,
    rows: list[dict[str, str]],
    source_timezone: str,
) -> dict[str, list[dict[str, Any]]]:
    result = {"orders": [], "fills": [], "positions": [], "pnl_events": []}
    for index, row in enumerate(rows, start=1):
        if file_type == "position_history":
            opened_at, _ = _parse_timestamp(row.get("Opening time"), source_timezone)
            closed_at, _ = _parse_timestamp(row.get("Closed time"), source_timezone)
            symbol, side, margin_mode = _bitget_future_parts(row.get("Futures"))
            gross = _decimal(row.get("Realized PnL"))
            net = _decimal(row.get("Position Pnl"))
            funding = _decimal(row.get("Fees"))
            fees = (
                gross + funding - net
                if gross is not None and funding is not None and net is not None
                else None
            )
            currency_match = re.search(r"([A-Za-z]+)$", _clean(row.get("Position Pnl")))
            currency = currency_match.group(1).upper() if currency_match else "USDT"
            result["positions"].append(_position_record(
                exchange="bitget",
                market_scope="USDT-FUTURES",
                symbol=symbol,
                side=side,
                opened_at=opened_at,
                closed_at=closed_at,
                payload={
                    "quantity": _number_text(row.get("Closed amount")),
                    "contracts": _number_text(row.get("Closed amount")),
                    "entry_price": _number_text(row.get("Average entry price")),
                    "exit_price": _number_text(row.get("Average closing price")),
                    "realized_pnl": format(net, "f") if net is not None else None,
                    "realized_pnl_breakdown": {
                        "gross_pnl": format(gross, "f") if gross is not None else None,
                        "fees": format(fees, "f") if fees is not None else None,
                        "net_pnl": format(net, "f") if net is not None else None,
                        "currency": currency,
                        "complete": all(
                            value is not None
                            for value in (gross, net, funding, fees)
                        ),
                        "funding": (
                            format(funding, "f") if funding is not None else None
                        ),
                        "funding_source": "position",
                        "funding_or_other": _number_text(row.get("Fees")),
                        "opening_fee": _number_text(row.get("Opening fee")),
                        "closing_fee": _number_text(row.get("Closing fee")),
                    },
                    "margin_mode": margin_mode,
                    "opened_at_known": True,
                    "native_id": None,
                    "source_row": index,
                },
            ))
        else:
            created_at, created_ms = _parse_timestamp(row.get("Date"), source_timezone)
            settlement = _clean(row.get("Coin")).upper() or "USDT"
            symbol = _linear_symbol(row.get("Futures"), settlement)
            amount = _number_text(row.get("Order amount"))
            filled = _number_text(row.get("Executed"))
            remaining = None
            amount_decimal = _decimal(amount)
            filled_decimal = _decimal(filled)
            if amount_decimal is not None and filled_decimal is not None:
                remaining = format(amount_decimal - filled_decimal, "f")
            result["orders"].append(_order_record("bitget", "USDT-FUTURES", {
                "id": _clean(row.get("Order ID")),
                "client_order_id": "",
                "symbol": symbol,
                "type": _clean(row.get("Transaction type")).lower(),
                "side": _clean(row.get("Direction")).lower(),
                "status": _bitget_status(row.get("Status")),
                "time_in_force": _clean(row.get("order source")),
                "price": _number_text(row.get("Price")),
                "average_price": _number_text(row.get("Average Price")),
                "amount": amount,
                "filled": filled,
                "remaining": remaining,
                "cost": _number_text(row.get("Trading volume")),
                "timestamp": created_ms,
                "datetime": created_at,
                "updated_timestamp": created_ms,
                "source_row": index,
            }))
    return result


def _okx_scope(value: Any) -> str:
    return {
        "SWAP": "swap",
        "FUTURES": "futures",
        "SPOT": "spot",
        "MARGIN": "spot",
        "OPTION": "option",
    }.get(_clean(value).upper(), _clean(value).lower())


def _parse_okx(
    file_type: str,
    rows: list[dict[str, str]],
    source_timezone: str,
) -> dict[str, list[dict[str, Any]]]:
    result = {"orders": [], "fills": [], "positions": [], "pnl_events": []}
    duplicate_trade_ids: dict[tuple[str, str], int] = {}
    if file_type == "trade_details":
        for row in rows:
            key = (_clean(row.get("Symbol")), _clean(row.get("Trade ID")))
            duplicate_trade_ids[key] = duplicate_trade_ids.get(key, 0) + 1
    for index, row in enumerate(rows, start=1):
        if file_type == "position_history":
            opened_at, _ = _parse_timestamp(row.get("Position Create Time"), source_timezone)
            closed_at, _ = _parse_timestamp(row.get("Position Update Time"), source_timezone)
            scope = _okx_scope(row.get("Business Line"))
            symbol = _okx_symbol(row.get("Instrument Name"), row.get("Business Line"))
            gross = _decimal(row.get("Pnl"))
            fee = _decimal(row.get("Fee"))
            funding = _decimal(row.get("Funding Fee"))
            liquidation = _decimal(row.get("Liquidation Clearance Fee"))
            net = _sum_decimal(gross, fee, funding, liquidation)
            fee_cost = (
                gross + funding - net
                if gross is not None and funding is not None and net is not None
                else None
            )
            currency = _clean(row.get("Margin Currency")).upper() or "USDT"
            result["positions"].append(_position_record(
                exchange="okx",
                market_scope=scope,
                symbol=symbol,
                side=_clean(row.get("Direction")).lower(),
                opened_at=opened_at,
                closed_at=closed_at,
                payload={
                    "quantity": _number_text(row.get("Total Close Quantity")),
                    "contracts": _number_text(row.get("Total Close Quantity")),
                    "entry_price": _number_text(row.get("Average Open Price")),
                    "exit_price": _number_text(row.get("Average Close Price")),
                    "realized_pnl": format(net, "f") if net is not None else None,
                    "realized_pnl_breakdown": {
                        "gross_pnl": format(gross, "f") if gross is not None else None,
                        "fees": format(fee_cost, "f") if fee_cost is not None else None,
                        "net_pnl": format(net, "f") if net is not None else None,
                        "currency": currency,
                        "complete": net is not None,
                        "funding_source": "position",
                        "trading_fee": format(fee, "f") if fee is not None else None,
                        "funding": format(funding, "f") if funding is not None else None,
                        "liquidation_fee": (
                            format(liquidation, "f") if liquidation is not None else None
                        ),
                    },
                    "leverage": _number_text(row.get("Leverage")),
                    "margin_mode": _clean(row.get("Margin Mode")).lower(),
                    "opened_at_known": True,
                    "native_id": None,
                    "source_row": index,
                },
            ))
        elif file_type == "order_history":
            created_at, created_ms = _parse_timestamp(row.get("Order Time"), source_timezone)
            scope = _okx_scope(row.get("Instrument"))
            symbol = _okx_symbol(row.get("Symbol"), row.get("Instrument"))
            amount = _number_text(row.get("Order Amount"))
            filled = _number_text(row.get("Filled Amount"))
            remaining = None
            amount_decimal = _decimal(amount)
            filled_decimal = _decimal(filled)
            if amount_decimal is not None and filled_decimal is not None:
                remaining = format(amount_decimal - filled_decimal, "f")
            result["orders"].append(_order_record("okx", scope, {
                "id": _clean(row.get("Order ID")),
                "client_order_id": "",
                "symbol": symbol,
                "type": _clean(row.get("Order Type")).lower(),
                "side": _clean(row.get("Side")).lower(),
                "status": _clean(row.get("Status")).lower(),
                "price": _number_text(row.get("Order Price")),
                "average_price": _number_text(row.get("Avg. Filled Price")),
                "amount": amount,
                "filled": filled,
                "remaining": remaining,
                "timestamp": created_ms,
                "datetime": created_at,
                "updated_timestamp": created_ms,
                "source_row": index,
            }))
        else:
            occurred_at, occurred_ms = _parse_timestamp(row.get("Trade Time"), source_timezone)
            scope = _okx_scope(row.get("Instrument"))
            symbol = _okx_symbol(row.get("Symbol"), row.get("Instrument"))
            raw_trade_id = _clean(row.get("Trade ID"))
            duplicate_key = (_clean(row.get("Symbol")), raw_trade_id)
            trade_id = raw_trade_id
            if duplicate_trade_ids.get(duplicate_key, 0) > 1:
                leg_id = _stable_id(
                    "leg",
                    row.get("Filled Amount"),
                    row.get("Filled Amount Unit"),
                    row.get("Filled Price"),
                    row.get("Fee Unit"),
                ).split(":", 1)[1]
                trade_id = f"{raw_trade_id}:{leg_id}"
            signed_fee = _decimal(row.get("Fee"))
            fee_cost = -signed_fee if signed_fee is not None else Decimal(0)
            result["fills"].append(_fill_record("okx", scope, {
                "id": trade_id,
                "order_id": _clean(row.get("Order ID")),
                "symbol": symbol,
                "side": None,
                "price": _number_text(row.get("Filled Price")),
                "amount": _number_text(row.get("Filled Amount")),
                "cost": _number_text(row.get("Trading Volume")),
                "timestamp": occurred_ms,
                "datetime": occurred_at,
                "fee": {
                    "cost": format(fee_cost, "f"),
                    "currency": _clean(row.get("Fee Unit")).upper(),
                },
                "fees": [],
                "taker_or_maker": _clean(row.get("taker/maker")).lower(),
                "source_row": index,
            }))
    return result


def _parse_hyperliquid(
    file_type: str,
    rows: list[dict[str, str]],
    source_timezone: str,
) -> dict[str, list[dict[str, Any]]]:
    result = {"orders": [], "fills": [], "positions": [], "pnl_events": []}
    for index, row in enumerate(rows, start=1):
        occurred_at, occurred_ms = _parse_timestamp(row.get("time"), source_timezone)
        symbol, scope = _hyperliquid_symbol(row.get("coin"))
        if file_type == "funding_history":
            event_id = _stable_id(
                "hyperliquid-funding",
                occurred_at,
                row.get("coin"),
                row.get("payment"),
                row.get("rate"),
            )
            event = _event_record(
                "hyperliquid",
                event_id,
                "funding",
                symbol,
                occurred_at,
                _number_text(row.get("payment"), "0") or "0",
                "USDC",
                "funding",
            )
            event["payload"].update({
                "side": _clean(row.get("side")).lower(),
                "size": _number_text(row.get("sz")),
                "rate": _number_text(row.get("rate")),
            })
            result["pnl_events"].append(event)
            continue

        direction = _clean(row.get("dir"))
        side = {
            "Open Long": "buy",
            "Close Long": "sell",
            "Open Short": "sell",
            "Close Short": "buy",
            "Buy": "buy",
            "Sell": "sell",
        }.get(direction, direction.lower())
        position_side = (
            "LONG" if "Long" in direction
            else "SHORT" if "Short" in direction
            else None
        )
        fee = _decimal(row.get("fee")) or Decimal(0)
        closed_net = _decimal(row.get("closedPnl")) or Decimal(0)
        gross = closed_net + fee
        trade_id = _stable_id(
            "hyperliquid-fill",
            occurred_at,
            row.get("coin"),
            direction,
            row.get("px"),
            row.get("sz"),
        )
        result["fills"].append(_fill_record("hyperliquid", scope, {
            "id": trade_id,
            "order_id": "",
            "symbol": symbol,
            "side": side,
            "price": _number_text(row.get("px")),
            "amount": _number_text(row.get("sz")),
            "cost": _number_text(row.get("ntl")),
            "timestamp": occurred_ms,
            "datetime": occurred_at,
            "fee": {"cost": format(fee, "f"), "currency": "USDC"},
            "fees": [],
            "position_side": position_side,
            "realized_pnl": format(gross, "f"),
            "closed_pnl_net": format(closed_net, "f"),
            "direction": direction,
            "source_row": index,
        }))
    return result


def _parse_file(spec: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(spec.get("path") or ""))
    name = _clean(spec.get("name")) or path.name
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HistoryCsvError(f"cannot read upload: {name}") from exc
    if len(content) > MAX_IMPORT_FILE_BYTES:
        raise HistoryCsvError(f"CSV exceeds {MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MB: {name}")
    rows, _ = _rows_from_bytes(content)
    exchange, file_type, header_index = _detect_format(rows)
    inferred_timezone, timezone_required = _infer_timezone(
        name,
        rows[:header_index],
        exchange,
    )
    source_account_uid = _infer_source_account_uid(rows[:header_index], exchange)
    source_timezone = _clean(spec.get("source_timezone")) or inferred_timezone
    _timezone_offset(source_timezone)
    data_rows = _dict_rows(rows, header_index)
    if not data_rows:
        raise HistoryCsvError(f"CSV has no data rows: {name}")
    if exchange == "binance":
        records = _parse_binance(file_type, data_rows, source_timezone)
    elif exchange == "bitget":
        records = _parse_bitget(file_type, data_rows, source_timezone)
    elif exchange == "okx":
        records = _parse_okx(file_type, data_rows, source_timezone)
    else:
        records = _parse_hyperliquid(file_type, data_rows, source_timezone)

    timestamps: list[str] = []
    for record in records["orders"]:
        value = record["payload"].get("datetime")
        if value:
            timestamps.append(str(value))
    for record in records["fills"]:
        value = record["payload"].get("datetime")
        if value:
            timestamps.append(str(value))
    for record in records["positions"]:
        timestamps.extend((record["opened_at"], record["closed_at"]))
    for record in records["pnl_events"]:
        if record.get("occurred_at"):
            timestamps.append(str(record["occurred_at"]))

    scopes = {
        str(record.get("market_scope") or "")
        for category in ("orders", "fills", "positions")
        for record in records[category]
        if record.get("market_scope")
    }
    warnings: list[str] = []
    if timezone_required and spec.get("timezone_confirmed") is not True:
        warnings.append(TIMEZONE_CONFIRMATION_WARNING)
    return {
        "file_id": _clean(spec.get("file_id")) or hashlib.sha256(content).hexdigest()[:16],
        "name": name,
        "file_hash": hashlib.sha256(content).hexdigest(),
        "exchange": exchange,
        "file_type": file_type,
        "market_scope": next(iter(scopes)) if len(scopes) == 1 else "mixed" if scopes else "",
        "source_timezone": source_timezone,
        "source_account_uid": source_account_uid,
        "timezone_requires_confirmation": timezone_required,
        "row_count": len(data_rows),
        "first_occurred_at": min(timestamps) if timestamps else None,
        "last_occurred_at": max(timestamps) if timestamps else None,
        "warnings": warnings,
        "records": records,
    }


def _companion_warnings(files: list[dict[str, Any]]) -> None:
    types_by_exchange: dict[str, set[str]] = {}
    for item in files:
        types_by_exchange.setdefault(item["exchange"], set()).add(item["file_type"])
    for item in files:
        available = types_by_exchange[item["exchange"]]
        warning = None
        if item["exchange"] == "binance" and "trade_history" not in available:
            warning = "Binance Trade History is required for fee-complete PnL."
        elif item["exchange"] == "hyperliquid" and "funding_history" not in available:
            warning = "Hyperliquid Funding History is missing; funding PnL will be incomplete."
        if warning and warning not in item["warnings"]:
            item["warnings"].append(warning)


def preview_history_files(specs: list[dict[str, Any]]) -> dict[str, Any]:
    if not specs or len(specs) > MAX_IMPORT_FILES:
        raise HistoryCsvError(f"select between 1 and {MAX_IMPORT_FILES} CSV files")
    files = [_parse_file(spec) for spec in specs]
    _companion_warnings(files)
    return {
        "files": [
            {key: value for key, value in item.items() if key != "records"}
            for item in files
        ],
        "timezone_options": timezone_options(),
    }


def _iso_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _enrich_binance_positions(files: list[dict[str, Any]]) -> None:
    fills_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    total_fees: dict[tuple[str, str], Decimal] = {}
    fee_event_targets: dict[tuple[str, str], dict[str, Any]] = {}
    positions: list[dict[str, Any]] = []
    for item in files:
        account = str(item["account"])
        for fill in item["records"]["fills"]:
            if fill["exchange"] != "binance":
                continue
            symbol = str(fill["payload"].get("symbol") or "")
            fills_by_group.setdefault((account, symbol), []).append({
                "trade_id": str(fill["payload"].get("id") or ""),
                "occurred_at": str(fill["payload"].get("datetime") or ""),
                "payload": fill["payload"],
            })
            fee = fill["payload"].get("fee")
            fee = fee if isinstance(fee, dict) else {}
            fee_cost = _decimal(fee.get("cost"))
            fee_currency = _clean(fee.get("currency")).upper() or "USDT"
            if fee_cost is not None:
                fee_key = (account, fee_currency)
                total_fees[fee_key] = total_fees.get(fee_key, Decimal(0)) + fee_cost
                fee_event_targets.setdefault(fee_key, item)
        positions.extend(
            position
            for position in item["records"]["positions"]
            if position["exchange"] == "binance"
        )
    for (account, symbol), fills in fills_by_group.items():
        rebuilt = reconstruct_position_history_from_fills(
            account,
            "binance",
            "usd_m",
            symbol,
            fills,
            infer_linear_pnl=True,
        )
        for position in positions:
            if str(position["payload"].get("symbol") or "") != symbol:
                continue
            match = next((
                candidate
                for candidate in rebuilt
                if (
                    abs(
                        _iso_seconds(candidate["opened_at"])
                        - _iso_seconds(position["opened_at"])
                    ) <= 2
                    and abs(
                        _iso_seconds(candidate["closed_at"])
                        - _iso_seconds(position["closed_at"])
                    ) <= 2
                )
            ), None)
            if match is None:
                continue
            rebuilt_payload = match["payload"]
            position["payload"]["realized_pnl"] = rebuilt_payload.get("realized_pnl")
            position["payload"]["realized_pnl_breakdown"] = rebuilt_payload.get(
                "realized_pnl_breakdown"
            )

    allocated_fees: dict[tuple[str, str], Decimal] = {}
    for position in positions:
        payload = position.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        breakdown = payload.get("realized_pnl_breakdown")
        breakdown = breakdown if isinstance(breakdown, dict) else {}
        fee_cost = _decimal(breakdown.get("fees"))
        currency = _clean(breakdown.get("currency")).upper()
        account = str(position.get("account") or "")
        if fee_cost is not None and currency:
            key = (account, currency)
            allocated_fees[key] = allocated_fees.get(key, Decimal(0)) + fee_cost

    for key, total_fee in total_fees.items():
        residual_fee = total_fee - allocated_fees.get(key, Decimal(0))
        if abs(residual_fee) <= Decimal("0.000000000001"):
            continue
        account, currency = key
        item = fee_event_targets[key]
        import_id = str(item["import_id"])
        event = _event_record(
            "binance",
            _stable_id("binance-unallocated-fee", item["file_hash"], account, currency),
            "trading_fee",
            "",
            item["last_occurred_at"],
            format(-residual_fee, "f"),
            currency,
            "trading_fee",
        )
        event["account"] = account
        event["import_id"] = import_id
        event["payload"]["import"] = {
            "import_id": import_id,
            "file_type": item["file_type"],
            "source_timezone": item["source_timezone"],
        }
        item["records"]["pnl_events"].append(event)


def build_history_import_batch(specs: list[dict[str, Any]]) -> dict[str, Any]:
    files = [_parse_file(spec) for spec in specs]
    _companion_warnings(files)
    for item, spec in zip(files, specs, strict=True):
        account = _clean(spec.get("account"))
        import_id = _clean(spec.get("import_id"))
        if not account or not import_id:
            raise HistoryCsvError("account and import_id are required")
        item["account"] = account
        item["import_id"] = import_id
        item["batch_id"] = _clean(spec.get("batch_id")) or import_id
        for category in ("orders", "fills", "positions", "pnl_events"):
            for record in item["records"][category]:
                record["account"] = account
                record["import_id"] = import_id
                payload = record.get("payload")
                if isinstance(payload, dict):
                    payload["import"] = {
                        "import_id": import_id,
                        "file_type": item["file_type"],
                        "source_timezone": item["source_timezone"],
                    }

    _enrich_binance_positions(files)
    batch: dict[str, Any] = {
        "orders": [],
        "fills": [],
        "positions": [],
        "pnl_events": [],
        "imports": [],
    }
    for item in files:
        for category in ("orders", "fills", "positions", "pnl_events"):
            batch[category].extend(item["records"][category])
        batch["imports"].append({
            "import_id": item["import_id"],
            "batch_id": item["batch_id"],
            "file_hash": item["file_hash"],
            "original_name": item["name"],
            "account": item["account"],
            "exchange": item["exchange"],
            "file_type": item["file_type"],
            "market_scope": item["market_scope"],
            "source_timezone": item["source_timezone"],
            "row_count": item["row_count"],
            "first_occurred_at": item["first_occurred_at"],
            "last_occurred_at": item["last_occurred_at"],
            "status": "ready",
            "warnings": item["warnings"],
        })
    return batch


def _hyperliquid_info_request(
    body: dict[str, Any],
    *,
    timeout: float,
) -> Any:
    request = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "PyneReal/1"},
        method="POST",
    )
    context = ssl.create_default_context(
        cafile=certifi.where() if certifi is not None else None,
    )
    with urllib.request.urlopen(  # noqa: S310
        request,
        timeout=timeout,
        context=context,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_hyperliquid_symbol_aliases(
    *,
    timeout: float = 30.0,
) -> dict[tuple[str, str], tuple[str, str]]:
    payload = _hyperliquid_info_request(
        {"type": "perpConciseAnnotations"},
        timeout=timeout,
    )
    if not isinstance(payload, list):
        raise HistoryCsvError(
            "Hyperliquid perpConciseAnnotations returned an invalid response"
        )
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    for item in payload:
        if not isinstance(item, list) or len(item) != 2:
            continue
        raw_coin, annotation = item
        if not isinstance(annotation, dict):
            continue
        coin_name = _clean(raw_coin)
        if ":" not in coin_name:
            continue
        dex_name, coin = coin_name.split(":", 1)
        dex_name = dex_name.strip().lower()
        coin = coin.strip().upper()
        display_name = _clean(annotation.get("displayName")).upper()
        if not dex_name or not coin or not display_name:
            continue
        canonical = (f"{dex_name.upper()}-{coin}/USDC:USDC", dex_name)
        aliases[(dex_name, display_name)] = canonical
        aliases[(dex_name, coin)] = canonical
    return aliases


def fetch_hyperliquid_historical_orders(
    wallet_address: str,
    account: str,
    import_id: str,
    *,
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    address = _clean(wallet_address)
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        raise HistoryCsvError("Hyperliquid account requires a valid walletAddress")
    payload = _hyperliquid_info_request(
        {"type": "historicalOrders", "user": address},
        timeout=timeout,
    )
    if not isinstance(payload, list):
        raise HistoryCsvError("Hyperliquid historicalOrders returned an invalid response")
    orders: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        raw = item.get("order")
        raw = raw if isinstance(raw, dict) else {}
        order_id = _clean(raw.get("oid") or item.get("oid"))
        if not order_id:
            continue
        symbol, scope = _hyperliquid_symbol(raw.get("coin"))
        created_ms = int(_decimal(raw.get("timestamp")) or 0)
        updated_ms = int(_decimal(item.get("statusTimestamp")) or created_ms)
        created_at = datetime.fromtimestamp(created_ms / 1000, UTC).isoformat().replace(
            "+00:00", "Z"
        ) if created_ms else None
        side_value = _clean(raw.get("side")).upper()
        tif = _clean(raw.get("tif"))
        order_type = _clean(raw.get("orderType") or raw.get("order_type")).lower()
        if not order_type:
            order_type = "market" if tif.lower() == "ioc" else "limit"
        side = {
            "A": "sell",
            "B": "buy",
        }.get(side_value, side_value.lower())
        amount = _decimal(raw.get("origSz") or raw.get("sz"))
        remaining = _decimal(raw.get("sz"))
        filled = amount - remaining if amount is not None and remaining is not None else None
        order_payload = {
            "id": order_id,
            "client_order_id": _clean(raw.get("cloid")),
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "status": _clean(item.get("status")).lower(),
            "time_in_force": tif or None,
            "price": _number_text(raw.get("limitPx")),
            "average_price": None,
            "amount": format(amount, "f") if amount is not None else None,
            "filled": format(filled, "f") if filled is not None else None,
            "remaining": format(remaining, "f") if remaining is not None else None,
            "timestamp": created_ms or None,
            "datetime": created_at,
            "updated_timestamp": updated_ms or None,
            "source_row": index,
            "import": {
                "import_id": import_id,
                "file_type": "historical_orders_api",
                "source_timezone": "UTC+00:00",
            },
        }
        orders.append({
            "account": account,
            "exchange": "hyperliquid",
            "market_scope": scope,
            "source": "rest:hyperliquid:historicalOrders",
            "import_id": import_id,
            "payload": order_payload,
        })
    warnings = []
    if len(payload) >= 2000:
        warnings.append(
            "Hyperliquid returned 2,000 historical orders; older orders may be missing."
        )
    return orders, warnings
