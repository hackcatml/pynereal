from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
import time
import tomllib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import ccxt


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AI_SCRIPTS = _PROJECT_ROOT / "ai" / "scripts"
if str(_AI_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_AI_SCRIPTS))

from asset import eprint, redact_error, secret_values, utc_now  # noqa: E402

from account_service.history import (  # noqa: E402
    bitget_history_item_matches_scope,
    bitget_position_matches_scope,
    normalize_fill,
    normalize_order,
)
from data_service.schedule_utils import (  # noqa: E402
    seconds_until_manual_refresh_guard_end,
    seconds_until_post_bar_task_window,
)


INITIAL_HISTORY_DAYS = 90
CURSOR_OVERLAP_SECONDS = 24 * 60 * 60
BACKFILL_INTERVAL_SECONDS = 30 * 60
MAX_PAGINATION_CALLS = 5
MAX_HISTORY_RECORDS = 1000
MAX_FUNDING_RECORDS = 1000
MAX_FUNDING_PAGINATION_CALLS = 10
BITGET_SPOT_ORDER_PAGE_LIMIT = 100
BINANCE_HISTORY_WINDOW_SAFETY_MS = 5 * 60 * 1000
BINANCE_ORDER_CURSOR_STREAM = "orders_90d"
OKX_ARCHIVE_PAGINATION_CALLS = 10
OKX_ORDER_CURSOR_STREAM = "orders_archive"
POSITION_PAGE_LIMIT = 100
POSITION_MIN_WINDOW_MS = 60 * 60 * 1000
_MANUAL_REFRESH_TIMING: ContextVar[bool] = ContextVar(
    "account_manual_refresh_timing",
    default=False,
)
_IMMEDIATE_HISTORY_TIMING: ContextVar[bool] = ContextVar(
    "account_immediate_history_timing",
    default=False,
)


@contextmanager
def manual_history_refresh_timing() -> Iterator[None]:
    token = _MANUAL_REFRESH_TIMING.set(True)
    try:
        yield
    finally:
        _MANUAL_REFRESH_TIMING.reset(token)


@contextmanager
def immediate_history_refresh_timing() -> Iterator[None]:
    token = _IMMEDIATE_HISTORY_TIMING.set(True)
    try:
        yield
    finally:
        _IMMEDIATE_HISTORY_TIMING.reset(token)


@dataclass(frozen=True)
class HistoryScope:
    name: str
    params: dict[str, Any]
    position_params: dict[str, Any] | None = None
    requires_symbols: bool = False


def _history_scopes(exchange_id: str, config: dict[str, Any]) -> list[HistoryScope]:
    if exchange_id == "binance":
        return [
            HistoryScope("spot", {"type": "spot"}, requires_symbols=True),
            HistoryScope(
                "usd_m",
                {"type": "future", "subType": "linear"},
                requires_symbols=True,
            ),
            HistoryScope(
                "coin_m",
                {"type": "delivery", "subType": "inverse"},
                requires_symbols=True,
            ),
        ]
    if exchange_id == "bitget":
        options = config.get("options")
        options = options if isinstance(options, dict) else {}
        uta = bool(config.get("uta") or options.get("uta"))
        if uta:
            return [HistoryScope("uta", {"uta": True}, {"uta": True})]
        return [
            HistoryScope("spot", {"type": "spot"}),
            HistoryScope(
                "USDT-FUTURES",
                {
                    "type": "swap",
                    "subType": "linear",
                    "productType": "USDT-FUTURES",
                },
                {"productType": "USDT-FUTURES"},
            ),
            HistoryScope(
                "USDC-FUTURES",
                {
                    "type": "swap",
                    "subType": "linear",
                    "productType": "USDC-FUTURES",
                },
                {"productType": "USDC-FUTURES"},
            ),
            HistoryScope(
                "COIN-FUTURES",
                {
                    "type": "swap",
                    "subType": "inverse",
                    "productType": "COIN-FUTURES",
                },
                {"productType": "COIN-FUTURES"},
            ),
        ]
    if exchange_id == "okx":
        return [
            HistoryScope("spot", {"type": "spot"}),
            HistoryScope(
                "swap",
                {"type": "swap"},
                {"instType": "SWAP"},
            ),
            HistoryScope(
                "futures",
                {"type": "future"},
                {"instType": "FUTURES"},
            ),
        ]
    if exchange_id == "bybit":
        return [
            HistoryScope("spot", {"type": "spot"}),
            HistoryScope(
                "linear",
                {"type": "swap", "subType": "linear"},
                {"subType": "linear"},
            ),
            HistoryScope(
                "inverse",
                {"type": "swap", "subType": "inverse"},
                {"subType": "inverse"},
            ),
        ]
    if exchange_id == "hyperliquid":
        return [HistoryScope("default", {})]
    return [HistoryScope("default", {})]


def _hyperliquid_dex(symbol: str) -> str | None:
    base = symbol.split("/", 1)[0].strip()
    if "-" not in base:
        return None
    prefix = base.split("-", 1)[0].strip().lower()
    return prefix or None


def hyperliquid_dexes(symbols: set[str]) -> list[str]:
    return sorted({dex for symbol in symbols if (dex := _hyperliquid_dex(symbol))})


def configured_session_symbols(config_path: str, exchange_id: str) -> set[str]:
    config_dir = Path(config_path).resolve().parent
    symbols: set[str] = set()

    def add_sessions(sessions: Any) -> None:
        if not isinstance(sessions, list):
            return
        for session in sessions:
            if not isinstance(session, dict):
                continue
            if str(session.get("exchange") or "").lower() != exchange_id:
                continue
            symbol = str(session.get("symbol") or "").strip().upper()
            if symbol:
                symbols.add(symbol)

    sessions_path = config_dir / "sessions.json"
    try:
        payload = json.loads(sessions_path.read_text(encoding="utf-8"))
        add_sessions(payload.get("sessions", []) if isinstance(payload, dict) else [])
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        pass
    if symbols:
        return symbols

    realtime_path = config_dir / "realtime_trade.toml"
    try:
        with realtime_path.open("rb") as handle:
            payload = tomllib.load(handle)
        add_sessions(payload.get("session", []))
        realtime = payload.get("realtime")
        if isinstance(realtime, dict):
            add_sessions([realtime])
    except (FileNotFoundError, OSError, ValueError, tomllib.TOMLDecodeError):
        pass
    return symbols


def account_history_symbols(
    config_path: str,
    exchange_id: str,
    current_positions: list[dict[str, Any]],
    cached_symbols: set[str],
) -> set[str]:
    symbols = configured_session_symbols(config_path, exchange_id)
    symbols.update(str(symbol).upper() for symbol in cached_symbols if symbol)
    symbols.update(
        str(position.get("symbol") or "").strip().upper()
        for position in current_positions
        if isinstance(position, dict) and position.get("symbol")
    )
    return {symbol for symbol in symbols if symbol}


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
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _iso_timestamp(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, UTC).isoformat().replace(
            "+00:00",
            "Z",
        )
    except (OverflowError, OSError, ValueError):
        return None


def _first_integer(source: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _integer(source.get(key))
        if value is not None:
            return value
    return None


def _first_number(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(source.get(key))
        if value is not None:
            return value
    return None


def _position_pnl_breakdown(
    exchange_id: str,
    position: dict[str, Any],
) -> tuple[float | None, dict[str, Any] | None]:
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    net = _number(position.get("realizedPnl"))
    gross = None
    if exchange_id == "bitget":
        gross = _first_number(info, ("pnl", "cumRealisedPnl"))
        native_net = _first_number(info, ("netProfit",))
        if native_net is not None:
            net = native_net
        funding = _first_number(info, ("totalFunding", "totalFundingFee"))
        if gross is not None and funding is not None and net is not None:
            fees = gross + funding - net
            return net, {
                "gross_pnl": gross,
                "fees": fees,
                "funding": funding,
                "funding_source": "position",
                "net_pnl": net,
                "currency": str(info.get("marginCoin") or "").upper() or None,
                "complete": True,
            }
    elif exchange_id == "okx":
        position_pnl = _first_number(info, ("pnl",))
        settled_pnl = _first_number(info, ("settledPnl",)) or 0.0
        gross = (
            position_pnl + settled_pnl
            if position_pnl is not None
            else None
        )
        native_net = _first_number(info, ("realizedPnl",))
        if native_net is not None:
            net = native_net
        funding = _first_number(info, ("fundingFee",))
        if gross is not None and funding is not None and net is not None:
            fees = gross + funding - net
            return net, {
                "gross_pnl": gross,
                "fees": fees,
                "funding": funding,
                "funding_source": "position",
                "net_pnl": net,
                "currency": str(info.get("ccy") or "").upper() or None,
                "complete": True,
            }
    elif exchange_id == "bybit":
        native_net = _first_number(info, ("closedPnl",))
        if native_net is not None:
            net = native_net
    if net is None:
        return None, None
    if gross is None:
        return net, {
            "gross_pnl": None,
            "fees": None,
            "net_pnl": net,
            "currency": None,
            "complete": False,
        }
    return net, {
        "gross_pnl": gross,
        "fees": gross - net,
        "net_pnl": net,
        "currency": None,
        "complete": True,
    }


def normalize_historical_position(
    account_name: str,
    exchange_id: str,
    market_scope: str,
    position: dict[str, Any],
) -> dict[str, Any] | None:
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    symbol = str(position.get("symbol") or "").strip()
    side = str(position.get("side") or "").strip().lower()
    identity_side = side
    if not symbol:
        return None

    opened_ms = _integer(position.get("timestamp"))
    closed_ms = _integer(position.get("lastUpdateTimestamp"))
    if closed_ms is None:
        closed_ms = _first_integer(
            info,
            ("updatedTime", "updatedAt", "utime", "uTime", "closeTime"),
        )
    opened_known = exchange_id != "bybit" and opened_ms is not None
    if closed_ms is None:
        return None
    if opened_ms is None or not opened_known:
        opened_ms = closed_ms
    opened_at = _iso_timestamp(opened_ms)
    closed_at = _iso_timestamp(closed_ms)
    if opened_at is None or closed_at is None:
        return None

    contracts = _number(position.get("contracts"))
    identity_contracts = contracts
    if exchange_id == "okx":
        direction = str(info.get("direction") or "").strip().lower()
        if side in {"", "net"} and direction in {"long", "short"}:
            side = direction
        if contracts is None:
            contracts = _first_number(info, ("closeTotalPos",))
    contract_size = _number(position.get("contractSize"))
    quantity = contracts
    if contracts is not None and contract_size is not None:
        quantity = contracts * contract_size
    realized_pnl, realized_breakdown = _position_pnl_breakdown(
        exchange_id,
        position,
    )
    native_id = str(position.get("id") or "").strip()
    if not native_id:
        native_id = str(
            _first_integer(
                info,
                ("positionId", "posId", "orderId"),
            )
            or ""
        )
    identity = [
        account_name,
        exchange_id,
        market_scope,
        native_id,
        symbol,
        identity_side,
        opened_at,
        closed_at,
        position.get("entryPrice"),
        position.get("lastPrice"),
        identity_contracts,
    ]
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    payload = {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "contracts": contracts,
        "contract_size": contract_size,
        "entry_price": _number(position.get("entryPrice")),
        "exit_price": _number(position.get("lastPrice")),
        "realized_pnl": realized_pnl,
        "realized_pnl_breakdown": realized_breakdown,
        "leverage": _number(position.get("leverage")),
        "margin_mode": position.get("marginMode"),
        "opened_at_known": opened_known,
        "native_id": native_id or None,
    }
    return {
        "account": account_name,
        "exchange": exchange_id,
        "market_scope": market_scope,
        "position_key": f"native:{digest}",
        "opened_at": opened_at,
        "closed_at": closed_at,
        "source": "native",
        "payload": payload,
    }


def _cursor_key(
    account_name: str,
    exchange_id: str,
    stream: str,
    scope_name: str,
) -> tuple[str, str, str, str]:
    return account_name, exchange_id, stream, scope_name


def _cursor_since(
    cursors: dict[tuple[str, str, str, str], str],
    key: tuple[str, str, str, str],
    now_ms: int,
) -> int:
    initial = now_ms - INITIAL_HISTORY_DAYS * 86_400_000
    try:
        previous = int(cursors.get(key, ""))
    except (TypeError, ValueError):
        return initial
    return max(initial, previous - CURSOR_OVERLAP_SECONDS * 1000)


def _initial_history_since(exchange_id: str, now_ms: int) -> int:
    since = now_ms - INITIAL_HISTORY_DAYS * 86_400_000
    if exchange_id == "binance":
        since += BINANCE_HISTORY_WINDOW_SAFETY_MS
    return since


def _history_since(
    exchange_id: str,
    cursors: dict[tuple[str, str, str, str], str],
    key: tuple[str, str, str, str],
    now_ms: int,
) -> int:
    return max(
        _cursor_since(cursors, key, now_ms),
        _initial_history_since(exchange_id, now_ms),
    )


def _cursor_record(
    account_name: str,
    exchange_id: str,
    stream: str,
    scope_name: str,
    cursor: int,
) -> dict[str, Any]:
    return {
        "account": account_name,
        "exchange": exchange_id,
        "stream": stream,
        "market_scope": scope_name,
        "cursor": str(cursor),
        "updated_at": utc_now(),
    }


def _scope_symbols(
    exchange: Any,
    exchange_id: str,
    scope: HistoryScope,
    known_symbols: set[str],
    target_symbol: str | None = None,
) -> list[str | None]:
    if target_symbol:
        market = exchange.markets.get(target_symbol)
        if not isinstance(market, dict):
            return []
        if scope.name == "spot":
            matches = bool(market.get("spot"))
        elif scope.name == "usd_m":
            matches = bool(market.get("contract") and market.get("linear"))
        elif scope.name == "coin_m":
            matches = bool(market.get("contract") and market.get("inverse"))
        elif scope.name == "USDT-FUTURES":
            matches = bool(
                market.get("swap")
                and market.get("linear")
                and str(market.get("settle") or "").upper() == "USDT"
            )
        elif scope.name == "USDC-FUTURES":
            matches = bool(
                market.get("swap")
                and market.get("linear")
                and str(market.get("settle") or "").upper() == "USDC"
            )
        elif scope.name == "COIN-FUTURES":
            matches = bool(market.get("swap") and market.get("inverse"))
        elif scope.name == "swap":
            matches = bool(market.get("swap"))
        elif scope.name == "futures":
            matches = bool(market.get("future"))
        elif scope.name == "linear":
            matches = bool(market.get("contract") and market.get("linear"))
        elif scope.name == "inverse":
            matches = bool(market.get("contract") and market.get("inverse"))
        else:
            matches = True
        return [target_symbol] if matches else []

    if exchange_id == "hyperliquid":
        if scope.name == "default":
            return [None]
        matches = [
            symbol for symbol in known_symbols if _hyperliquid_dex(symbol) == scope.name
        ]
        return [matches[0]] if matches else []
    if not scope.requires_symbols:
        return [None]
    matched: list[str] = []
    for symbol in sorted(known_symbols):
        market = exchange.markets.get(symbol)
        if not isinstance(market, dict):
            continue
        if scope.name == "spot" and market.get("spot"):
            matched.append(symbol)
        elif scope.name == "usd_m" and market.get("linear") and market.get("contract"):
            matched.append(symbol)
        elif scope.name == "coin_m" and market.get("inverse") and market.get("contract"):
            matched.append(symbol)
    return matched


async def _wait_for_history_slot(stop: asyncio.Event) -> None:
    if _IMMEDIATE_HISTORY_TIMING.get():
        return
    delay = (
        seconds_until_manual_refresh_guard_end()
        if _MANUAL_REFRESH_TIMING.get()
        else seconds_until_post_bar_task_window()
    )
    if delay <= 0.0:
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        return
    raise asyncio.CancelledError


async def _fetch_with_retry(
    exchange: Any,
    account: Any,
    label: str,
    callback: Any,
    stop: asyncio.Event,
) -> list[dict[str, Any]]:
    for attempt in range(1, 3):
        await _wait_for_history_slot(stop)
        try:
            result = await callback()
            if not isinstance(result, list):
                raise TypeError(f"{label} returned a non-list result")
            return [item for item in result if isinstance(item, dict)]
        except asyncio.CancelledError:
            raise
        except (
            ccxt.ArgumentsRequired,
            ccxt.AuthenticationError,
            ccxt.BadRequest,
            ccxt.NotSupported,
            ccxt.PermissionDenied,
        ):
            raise
        except Exception:
            if attempt >= 2:
                raise
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
                raise asyncio.CancelledError
            except TimeoutError:
                pass
    raise RuntimeError(f"{label} retry loop ended unexpectedly")


def _rest_record(
    account: Any,
    market_scope: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "account": account.name,
        "exchange": account.exchange_id,
        "market_scope": market_scope,
        "source": "rest",
        "payload": payload,
    }


def _decimal_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return format(parsed, "f") if parsed.is_finite() else None


def _funding_amount_text(exchange_id: str, funding: dict[str, Any]) -> str | None:
    info = funding.get("info")
    info = info if isinstance(info, dict) else {}
    if exchange_id == "binance":
        raw = info.get("income")
    elif exchange_id == "hyperliquid":
        delta = info.get("delta")
        delta = delta if isinstance(delta, dict) else {}
        raw = delta.get("usdc")
    else:
        raw = None
    return _decimal_text(raw if raw is not None else funding.get("amount"))


def _normalize_funding_event(
    account: Any,
    market_scope: str,
    funding: dict[str, Any],
) -> dict[str, Any] | None:
    timestamp = _integer(funding.get("timestamp"))
    amount = _funding_amount_text(account.exchange_id, funding)
    if timestamp is None or amount is None:
        return None
    occurred_at = _iso_timestamp(timestamp)
    if occurred_at is None:
        return None
    symbol = str(funding.get("symbol") or "").strip()
    currency = str(funding.get("code") or "").strip().upper()
    if not currency:
        currency = "USDC" if account.exchange_id == "hyperliquid" else "USDT"

    info = funding.get("info")
    info = info if isinstance(info, dict) else {}
    event_id = str(funding.get("id") or "").strip()
    if account.exchange_id == "hyperliquid":
        delta = info.get("delta")
        delta = delta if isinstance(delta, dict) else {}
        identity = [
            account.name,
            occurred_at,
            symbol,
            amount,
            delta.get("fundingRate") or funding.get("rate"),
            delta.get("szi"),
        ]
        event_id = "hyperliquid-funding:" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
    elif not event_id:
        identity = [account.name, market_scope, occurred_at, symbol, amount]
        event_id = "rest-funding:" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()[:24]

    payload: dict[str, Any] = {
        "amount": amount,
        "currency": currency,
        "component": "funding",
        "count_in_pnl": True,
        "canonical_source": f"rest:{account.exchange_id}",
    }
    if account.exchange_id == "hyperliquid":
        delta = info.get("delta")
        delta = delta if isinstance(delta, dict) else {}
        size = _decimal_text(delta.get("szi"))
        payload.update({
            "side": "long" if size is not None and not size.startswith("-") else "short",
            "size": size,
            "rate": _decimal_text(delta.get("fundingRate") or funding.get("rate")),
        })
    return {
        "account": account.name,
        "exchange": account.exchange_id,
        "event_id": event_id,
        "event_type": "funding",
        "market_scope": market_scope,
        "symbol": symbol,
        "occurred_at": occurred_at,
        "source": "rest",
        "payload": payload,
    }


async def _fetch_funding_events(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    since: int,
    now_ms: int,
    stop: asyncio.Event,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    if account.exchange_id not in {"binance", "hyperliquid"}:
        return []
    if not exchange.has.get("fetchFundingHistory"):
        raise ccxt.NotSupported(
            f"{account.exchange_id} does not expose funding history"
        )

    params = dict(scope.params)
    records: dict[str, dict[str, Any]] = {}
    page_limit = 500 if account.exchange_id == "hyperliquid" else MAX_FUNDING_RECORDS
    for window_start, window_end in _stream_windows(
        account.exchange_id,
        since,
        now_ms,
    ):
        cursor = window_start
        effective_end = window_end if window_end is not None else now_ms
        for _ in range(MAX_FUNDING_PAGINATION_CALLS):
            request_params = {**params, "until": effective_end}
            page = await _fetch_with_retry(
                exchange,
                account,
                f"{scope.name} funding history",
                lambda cursor=cursor, request_params=request_params: (
                    exchange.fetch_funding_history(
                        symbol,
                        cursor,
                        page_limit,
                        dict(request_params),
                    )
                ),
                stop,
            )
            page_timestamps: list[int] = []
            for item in page:
                timestamp = _integer(item.get("timestamp"))
                if timestamp is None or timestamp < since or timestamp > now_ms:
                    continue
                page_timestamps.append(timestamp)
                normalized = _normalize_funding_event(account, scope.name, item)
                if normalized is not None:
                    records[normalized["event_id"]] = normalized
            if not page_timestamps:
                break
            next_cursor = max(page_timestamps) + 1
            if next_cursor <= cursor or next_cursor > effective_end:
                break
            cursor = next_cursor
            if len(page) < page_limit:
                break
    return list(records.values())


def _binance_unified_symbol(
    exchange: Any,
    raw_symbol: str,
    market_scope: str,
) -> str | None:
    candidates = exchange.markets_by_id.get(raw_symbol.upper())
    if isinstance(candidates, dict):
        candidates = [candidates]
    if not isinstance(candidates, list):
        return None
    for market in candidates:
        if not isinstance(market, dict) or not market.get("contract"):
            continue
        if market_scope == "usd_m" and not market.get("linear"):
            continue
        if market_scope == "coin_m" and not market.get("inverse"):
            continue
        symbol = str(market.get("symbol") or "").strip()
        if symbol:
            return symbol
    return None


def _oldest_numeric_id(rows: list[dict[str, Any]]) -> str | None:
    identifiers: list[tuple[int, str]] = []
    for row in rows:
        value = str(row.get("id") or "").strip()
        if not value:
            continue
        try:
            identifiers.append((int(value), value))
        except ValueError:
            continue
    return min(identifiers)[1] if identifiers else None


async def _fetch_bitget_spot_orders(
    exchange: Any,
    account: Any,
    symbol: str | None,
    since: int,
    stop: asyncio.Event,
) -> list[dict[str, Any]]:
    method = getattr(exchange, "fetch_canceled_and_closed_orders", None)
    if not callable(method):
        raise ccxt.NotSupported("bitget does not expose spot order history")

    orders: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    cursor: str | None = None
    for page_number in range(1, MAX_PAGINATION_CALLS + 1):
        params: dict[str, Any] = {
            "type": "spot",
            "paginate": False,
        }
        if cursor is not None:
            params["idLessThan"] = cursor
        page = await _fetch_with_retry(
            exchange,
            account,
            f"spot orders page {page_number}",
            lambda params=params: method(
                symbol,
                since,
                BITGET_SPOT_ORDER_PAGE_LIMIT,
                dict(params),
            ),
            stop,
        )
        if not page:
            break
        for order in page:
            order_id = str(order.get("id") or "").strip()
            if order_id and order_id in seen_ids:
                continue
            if order_id:
                seen_ids.add(order_id)
            orders.append(order)

        next_cursor = _oldest_numeric_id(page)
        if (
            len(page) < BITGET_SPOT_ORDER_PAGE_LIMIT
            or next_cursor is None
            or next_cursor == cursor
        ):
            break
        cursor = next_cursor
    return orders


async def _discover_binance_history_symbols(
    exchange: Any,
    account: Any,
    market_scope: str,
    since: int,
    now_ms: int,
    stop: asyncio.Event,
) -> set[str]:
    if market_scope == "coin_m":
        method = exchange.dapiPrivateGetIncome
    else:
        method = exchange.fapiPrivateGetIncome
    raw_symbols: set[str] = set()
    window_start = since
    for _ in range(MAX_PAGINATION_CALLS):
        rows = await _fetch_with_retry(
            exchange,
            account,
            f"{market_scope} realized PnL symbols",
            lambda window_start=window_start: method({
                "incomeType": "REALIZED_PNL",
                "startTime": window_start,
                "endTime": now_ms,
                "limit": 1000,
            }),
            stop,
        )
        timestamps: list[int] = []
        for row in rows:
            raw_symbol = str(row.get("symbol") or "").strip().upper()
            if raw_symbol:
                raw_symbols.add(raw_symbol)
            timestamp = _integer(row.get("time"))
            if timestamp is not None:
                timestamps.append(timestamp)
        if len(rows) < 1000 or not timestamps:
            break
        next_start = max(timestamps) + 1
        if next_start <= window_start:
            break
        window_start = next_start
    return {
        symbol
        for raw_symbol in raw_symbols
        if (
            symbol := _binance_unified_symbol(
                exchange,
                raw_symbol,
                market_scope,
            )
        )
    }


async def _fetch_orders(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    symbols: list[str | None],
    since: int,
    stop: asyncio.Event,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    historical_supported = False
    params = {
        **scope.params,
        "paginate": True,
        "paginationCalls": (
            OKX_ARCHIVE_PAGINATION_CALLS
            if account.exchange_id == "okx"
            else MAX_PAGINATION_CALLS
        ),
    }
    if account.exchange_id == "okx":
        params["method"] = "privateGetTradeOrdersHistoryArchive"
    for symbol in symbols:
        raw_orders: list[dict[str, Any]] = []
        if account.exchange_id == "bitget" and scope.name == "spot":
            historical_supported = True
            raw_orders.extend(await _fetch_bitget_spot_orders(
                exchange,
                account,
                symbol,
                since,
                stop,
            ))
        elif exchange.has.get("fetchOrders"):
            historical_supported = True
            raw_orders.extend(await _fetch_with_retry(
                exchange,
                account,
                f"{scope.name} orders",
                lambda symbol=symbol: exchange.fetch_orders(
                    symbol,
                    since,
                    MAX_HISTORY_RECORDS,
                    dict(params),
                ),
                stop,
            ))
        else:
            for capability, method_name in (
                ("fetchClosedOrders", "fetch_closed_orders"),
            ):
                if not exchange.has.get(capability):
                    continue
                historical_supported = True
                method = getattr(exchange, method_name)
                raw_orders.extend(await _fetch_with_retry(
                    exchange,
                    account,
                    f"{scope.name} {method_name}",
                    lambda method=method, symbol=symbol: method(
                        symbol,
                        since,
                        MAX_HISTORY_RECORDS,
                        dict(params),
                    ),
                    stop,
                ))
            if (
                account.exchange_id in {"bitget", "bybit", "okx"}
                and exchange.has.get("fetchCanceledOrders")
            ):
                try:
                    raw_orders.extend(await _fetch_with_retry(
                        exchange,
                        account,
                        f"{scope.name} fetch_canceled_orders",
                        lambda symbol=symbol: exchange.fetch_canceled_orders(
                            symbol,
                            since,
                            MAX_HISTORY_RECORDS,
                            dict(params),
                        ),
                        stop,
                    ))
                except (
                    ccxt.ArgumentsRequired,
                    ccxt.BadRequest,
                    ccxt.NotSupported,
                ):
                    pass
        for order in raw_orders:
            normalized = normalize_order(order)
            if (
                account.exchange_id == "bitget"
                and not bitget_history_item_matches_scope(
                    normalized,
                    scope.name,
                )
            ):
                continue
            records.append(_rest_record(account, scope.name, normalized))
    if not historical_supported:
        raise ccxt.NotSupported(
            f"{account.exchange_id} does not expose order history"
        )
    return records


def _stream_windows(
    exchange_id: str,
    since: int,
    now_ms: int,
) -> list[tuple[int, int | None]]:
    if exchange_id not in {"binance", "bybit"}:
        return [(since, None)]
    windows: list[tuple[int, int | None]] = []
    window_start = since
    window_size = 7 * 86_400_000
    while window_start < now_ms:
        window_end = min(window_start + window_size, now_ms)
        windows.append((window_start, window_end))
        window_start = window_end + 1
    return windows


def _scope_until(scope: HistoryScope, until: int | None) -> HistoryScope:
    if until is None:
        return scope
    return HistoryScope(
        scope.name,
        {**scope.params, "until": until},
        scope.position_params,
        scope.requires_symbols,
    )


async def _fetch_orders_since(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    symbols: list[str | None],
    since: int,
    now_ms: int,
    stop: asyncio.Event,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for window_start, window_end in _stream_windows(
        account.exchange_id,
        since,
        now_ms,
    ):
        records.extend(await _fetch_orders(
            exchange,
            account,
            _scope_until(scope, window_end),
            symbols,
            window_start,
            stop,
        ))
    return records


async def _fetch_fills_since(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    symbols: list[str | None],
    since: int,
    now_ms: int,
    stop: asyncio.Event,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for window_start, window_end in _stream_windows(
        account.exchange_id,
        since,
        now_ms,
    ):
        records.extend(await _fetch_fills(
            exchange,
            account,
            _scope_until(scope, window_end),
            symbols,
            window_start,
            stop,
        ))
    return records


async def _fetch_fills(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    symbols: list[str | None],
    since: int,
    stop: asyncio.Event,
) -> list[dict[str, Any]]:
    if not exchange.has.get("fetchMyTrades"):
        raise ccxt.NotSupported(
            f"{account.exchange_id} does not expose trade history"
        )
    params = {
        **scope.params,
        "paginate": True,
        "paginationCalls": MAX_PAGINATION_CALLS,
    }
    records: list[dict[str, Any]] = []
    for symbol in symbols:
        trades = await _fetch_with_retry(
            exchange,
            account,
            f"{scope.name} fills",
            lambda symbol=symbol: exchange.fetch_my_trades(
                symbol,
                since,
                MAX_HISTORY_RECORDS,
                dict(params),
            ),
            stop,
        )
        for trade in trades:
            normalized = normalize_fill(trade)
            if (
                account.exchange_id == "bitget"
                and not bitget_history_item_matches_scope(
                    normalized,
                    scope.name,
                )
            ):
                continue
            records.append(_rest_record(account, scope.name, normalized))
    return records


def _position_closed_ms(position: dict[str, Any]) -> int | None:
    value = _integer(position.get("lastUpdateTimestamp"))
    if value is not None:
        return value
    info = position.get("info")
    info = info if isinstance(info, dict) else {}
    return _first_integer(
        info,
        ("updatedTime", "updatedAt", "utime", "uTime", "closeTime"),
    )


async def _fetch_position_window(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    since: int,
    until: int,
    stop: asyncio.Event,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    params = {**(scope.position_params or {}), "until": until}
    rows = await _fetch_with_retry(
        exchange,
        account,
        f"{scope.name} position history",
        lambda: exchange.fetch_positions_history(
            [symbol] if symbol else None,
            since,
            POSITION_PAGE_LIMIT,
            params,
        ),
        stop,
    )
    if len(rows) < POSITION_PAGE_LIMIT or until - since <= POSITION_MIN_WINDOW_MS:
        return rows
    midpoint = since + (until - since) // 2
    left = await _fetch_position_window(
        exchange,
        account,
        scope,
        since,
        midpoint,
        stop,
        symbol,
    )
    right = await _fetch_position_window(
        exchange,
        account,
        scope,
        midpoint + 1,
        until,
        stop,
        symbol,
    )
    return [*left, *right]


async def _fetch_okx_position_pages(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    since: int,
    stop: asyncio.Event,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after: int | None = None
    for _ in range(MAX_PAGINATION_CALLS):
        params = dict(scope.position_params or {})
        if after is not None:
            params["after"] = str(after)
        page = await _fetch_with_retry(
            exchange,
            account,
            f"{scope.name} position history",
            lambda params=params: exchange.fetch_positions_history(
                [symbol] if symbol else None,
                None,
                POSITION_PAGE_LIMIT,
                params,
            ),
            stop,
        )
        rows.extend(page)
        timestamps = [
            timestamp
            for position in page
            if (timestamp := _position_closed_ms(position)) is not None
        ]
        if len(page) < POSITION_PAGE_LIMIT or not timestamps:
            break
        oldest = min(timestamps)
        if oldest <= since or oldest == after:
            break
        after = oldest
    return [
        position
        for position in rows
        if (_position_closed_ms(position) or 0) >= since
    ]


async def _fetch_positions(
    exchange: Any,
    account: Any,
    scope: HistoryScope,
    since: int,
    now_ms: int,
    stop: asyncio.Event,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    if scope.position_params is None or not exchange.has.get("fetchPositionsHistory"):
        return []
    if account.exchange_id == "okx":
        raw_positions = await _fetch_okx_position_pages(
            exchange,
            account,
            scope,
            since,
            stop,
            symbol,
        )
    else:
        max_window = 7 * 86_400_000 if account.exchange_id == "bybit" else 90 * 86_400_000
        raw_positions = []
        window_start = since
        while window_start < now_ms:
            window_end = min(window_start + max_window, now_ms)
            raw_positions.extend(await _fetch_position_window(
                exchange,
                account,
                scope,
                window_start,
                window_end,
                stop,
                symbol,
            ))
            window_start = window_end + 1
    records = [
        normalized
        for position in raw_positions
        if (
            account.exchange_id != "bitget"
            or bitget_position_matches_scope(position, scope.name)
        )
        if (
            normalized := normalize_historical_position(
                account.name,
                account.exchange_id,
                scope.name,
                position,
            )
        ) is not None
    ]
    return list({record["position_key"]: record for record in records}.values())


async def backfill_account_once(
    exchange: Any,
    account: Any,
    known_symbols: set[str],
    cursors: dict[tuple[str, str, str, str], str],
    stop: asyncio.Event,
    *,
    include_orders: bool = True,
    include_fills: bool = True,
    include_positions: bool = True,
    include_pnl_events: bool = True,
    target_symbol: str | None = None,
    discover_symbols: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    now_ms = int(time.time() * 1000)
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    pnl_events: list[dict[str, Any]] = []
    cursor_records: list[dict[str, Any]] = []
    scopes = _history_scopes(account.exchange_id, account.config)
    discovered_symbols: dict[str, set[str]] = {}
    full_history_scopes: set[str] = set()
    pending_discovery_keys: dict[str, tuple[str, str, str, str]] = {}
    if account.exchange_id == "binance" and discover_symbols:
        for market_scope in ("usd_m", "coin_m"):
            discovery_key = _cursor_key(
                account.name,
                account.exchange_id,
                "symbols",
                market_scope,
            )
            initial_discovery = discovery_key not in cursors
            discovery_since = _history_since(
                account.exchange_id,
                cursors,
                discovery_key,
                now_ms,
            )
            try:
                discovered_symbols[market_scope] = (
                    await _discover_binance_history_symbols(
                        exchange,
                        account,
                        market_scope,
                        discovery_since,
                        now_ms,
                        stop,
                    )
                )
                if initial_discovery:
                    full_history_scopes.add(market_scope)
                pending_discovery_keys[market_scope] = discovery_key
            except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
                pass
            except Exception as exc:
                eprint(
                    f"[account] {account.name} {market_scope} symbol discovery "
                    f"failed: {type(exc).__name__}: "
                    f"{redact_error(exc, secret_values(account.config))}"
                )
    secrets = secret_values(account.config)
    for scope in scopes:
        scope_symbols = set(known_symbols)
        scope_symbols.update(discovered_symbols.get(scope.name, set()))
        symbols = _scope_symbols(
            exchange,
            account.exchange_id,
            scope,
            scope_symbols,
            target_symbol,
        )
        if not symbols:
            if scope.name in pending_discovery_keys:
                discovery_key = pending_discovery_keys[scope.name]
                cursors[discovery_key] = str(now_ms)
                cursor_records.append(_cursor_record(
                    account.name,
                    account.exchange_id,
                    "symbols",
                    scope.name,
                    now_ms,
                ))
            continue
        scope_key = scope.name
        if scope.requires_symbols:
            symbol_targets = symbols
        else:
            symbol_targets = symbols[:1]

        collects_funding = (
            account.exchange_id == "hyperliquid"
            or (
                account.exchange_id == "binance"
                and scope.name in {"usd_m", "coin_m"}
            )
        )
        if include_pnl_events and collects_funding:
            funding_key = _cursor_key(
                account.name,
                account.exchange_id,
                "funding",
                scope.name,
            )
            funding_since = _history_since(
                account.exchange_id,
                cursors,
                funding_key,
                now_ms,
            )
            try:
                pnl_events.extend(await _fetch_funding_events(
                    exchange,
                    account,
                    scope,
                    funding_since,
                    now_ms,
                    stop,
                    target_symbol,
                ))
                cursors[funding_key] = str(now_ms)
                cursor_records.append(_cursor_record(
                    account.name,
                    account.exchange_id,
                    "funding",
                    scope.name,
                    now_ms,
                ))
            except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
                pass
            except Exception as exc:
                eprint(
                    f"[account] {account.name} {scope.name} funding backfill "
                    f"failed: {type(exc).__name__}: "
                    f"{redact_error(exc, secrets)}"
                )

        order_stream = (
            OKX_ORDER_CURSOR_STREAM
            if account.exchange_id == "okx"
            else (
                BINANCE_ORDER_CURSOR_STREAM
                if account.exchange_id == "binance"
                else "orders"
            )
        )
        order_key = _cursor_key(
            account.name,
            account.exchange_id,
            order_stream,
            scope_key,
        )
        order_since = (
            _initial_history_since(account.exchange_id, now_ms)
            if scope.name in full_history_scopes
            else _history_since(
                account.exchange_id,
                cursors,
                order_key,
                now_ms,
            )
        )
        order_history_complete = not include_orders
        if include_orders:
            try:
                orders.extend(await _fetch_orders_since(
                    exchange,
                    account,
                    scope,
                    symbol_targets,
                    order_since,
                    now_ms,
                    stop,
                ))
                cursors[order_key] = str(now_ms)
                cursor_records.append(_cursor_record(
                    account.name,
                    account.exchange_id,
                    order_stream,
                    scope_key,
                    now_ms,
                ))
                order_history_complete = True
            except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
                pass
            except Exception as exc:
                eprint(
                    f"[account] {account.name} {scope.name} order backfill failed: "
                    f"{type(exc).__name__}: {redact_error(exc, secrets)}"
                )

        fill_key = _cursor_key(
            account.name,
            account.exchange_id,
            "fills",
            scope_key,
        )
        fill_since = (
            _initial_history_since(account.exchange_id, now_ms)
            if scope.name in full_history_scopes
            else _history_since(
                account.exchange_id,
                cursors,
                fill_key,
                now_ms,
            )
        )
        fill_history_complete = not include_fills
        if include_fills:
            try:
                fills.extend(await _fetch_fills_since(
                    exchange,
                    account,
                    scope,
                    symbol_targets,
                    fill_since,
                    now_ms,
                    stop,
                ))
                cursors[fill_key] = str(now_ms)
                cursor_records.append(_cursor_record(
                    account.name,
                    account.exchange_id,
                    "fills",
                    scope_key,
                    now_ms,
                ))
                fill_history_complete = True
            except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
                pass
            except Exception as exc:
                eprint(
                    f"[account] {account.name} {scope.name} fill backfill failed: "
                    f"{type(exc).__name__}: {redact_error(exc, secrets)}"
                )

        if (
            order_history_complete
            and fill_history_complete
            and scope.name in pending_discovery_keys
        ):
            discovery_key = pending_discovery_keys[scope.name]
            cursors[discovery_key] = str(now_ms)
            cursor_records.append(_cursor_record(
                account.name,
                account.exchange_id,
                "symbols",
                scope.name,
                now_ms,
            ))

        if not include_positions or scope.position_params is None:
            continue
        position_key = _cursor_key(
            account.name,
            account.exchange_id,
            "positions",
            scope_key,
        )
        position_since = _cursor_since(cursors, position_key, now_ms)
        try:
            positions.extend(await _fetch_positions(
                exchange,
                account,
                scope,
                position_since,
                now_ms,
                stop,
                target_symbol,
            ))
            cursors[position_key] = str(now_ms)
            cursor_records.append(_cursor_record(
                account.name,
                account.exchange_id,
                "positions",
                scope_key,
                now_ms,
            ))
        except (ccxt.ArgumentsRequired, ccxt.BadRequest, ccxt.NotSupported):
            pass
        except Exception as exc:
            eprint(
                f"[account] {account.name} {scope.name} position backfill failed: "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}"
            )

    return {
        "orders": orders,
        "fills": fills,
        "positions": positions,
        "pnl_events": pnl_events,
        "cursors": cursor_records,
    }


async def account_history_backfill_loop(
    exchange: Any,
    account: Any,
    config_path: str,
    current_positions: Any,
    cached_symbols: set[str],
    history: Any,
    cursors: dict[tuple[str, str, str, str], str],
    semaphore: asyncio.Semaphore,
    account_lock: asyncio.Lock,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        await _wait_for_history_slot(stop)
        known_symbols = account_history_symbols(
            config_path,
            account.exchange_id,
            current_positions(),
            cached_symbols,
        )
        try:
            async with semaphore:
                async with account_lock:
                    batch = await backfill_account_once(
                        exchange,
                        account,
                        known_symbols,
                        cursors,
                        stop,
                    )
            history.add_backfill(**batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            eprint(
                f"[account] {account.name} history backfill failed: "
                f"{type(exc).__name__}: "
                f"{redact_error(exc, secret_values(account.config))}"
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=BACKFILL_INTERVAL_SECONDS)
        except TimeoutError:
            pass
