from __future__ import annotations

import asyncio
import copy
import math
import queue
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import ccxt
import ccxt.pro as ccxtpro


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AI_SCRIPTS = _PROJECT_ROOT / "ai" / "scripts"
if str(_AI_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_AI_SCRIPTS))

from asset import (  # noqa: E402
    ExchangeAccount,
    configured_accounts,
    merge_dicts,
    read_provider_config,
    redact_error,
    secret_values,
    utc_now,
)
from position import (  # noqa: E402
    binance_position_matches_scope,
    is_open_position,
    normalize_hyperliquid_position,
    normalize_position,
)

from account_service.cache import AccountCache  # noqa: E402
from account_service.history import normalize_fill, normalize_order  # noqa: E402
from account_service.positions import collect_positions_snapshot  # noqa: E402
from data_service.schedule_utils import (  # noqa: E402
    seconds_until_post_bar_task_window,
)


EMIT_INTERVAL_SECONDS = 0.25
PERSIST_INTERVAL_SECONDS = 1.0
RECONCILE_INTERVAL_SECONDS = 300.0


def _position_key(position: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(position.get("market_scope") or ""),
        str(position.get("dex") or ""),
        str(position.get("symbol") or ""),
        str(position.get("side") or ""),
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class LivePositionState:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = copy.deepcopy(snapshot)
        self.dirty = asyncio.Event()
        self.persist_dirty = asyncio.Event()
        self.sequence = 0

    def replace(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = copy.deepcopy(snapshot)
        self.mark_dirty()

    def mark_dirty(self) -> None:
        self.sequence += 1
        self.dirty.set()
        self.persist_dirty.set()

    def result(self, account_name: str) -> dict[str, Any] | None:
        for result in self.snapshot.get("results", []):
            if isinstance(result, dict) and result.get("account") == account_name:
                return result
        return None

    def positions(self, account_name: str) -> list[dict[str, Any]]:
        result = self.result(account_name)
        if result is None:
            return []
        positions = result.get("positions")
        return positions if isinstance(positions, list) else []

    def merge_positions(
        self,
        account_name: str,
        raw_positions: list[dict[str, Any]],
        *,
        exchange_id: str,
        market_scope: str | None = None,
        dex: str | None = None,
    ) -> None:
        result = self.result(account_name)
        if result is None:
            return
        previous_positions = copy.deepcopy(self.positions(account_name))
        previous_stream_status = result.get("stream_status")
        current = {
            _position_key(position): position
            for position in self.positions(account_name)
            if isinstance(position, dict)
        }
        for raw_position in raw_positions:
            if not isinstance(raw_position, dict):
                continue
            normalized = (
                normalize_hyperliquid_position(raw_position)
                if exchange_id == "hyperliquid"
                else normalize_position(raw_position)
            )
            if market_scope:
                normalized["market_scope"] = market_scope
            if dex:
                normalized["dex"] = dex
            key = _position_key(normalized)
            previous = current.get(key)
            if isinstance(previous, dict):
                previous_breakdown = previous.get("realized_pnl_breakdown")
                new_breakdown = normalized.get("realized_pnl_breakdown")
                previous_net = _number(previous.get("realized_pnl"))
                new_net = _number(normalized.get("realized_pnl"))
                if new_net is None and previous_net is not None:
                    normalized["realized_pnl"] = previous_net
                    normalized["realized_pnl_breakdown"] = copy.deepcopy(previous_breakdown)
                elif (
                    new_net is not None
                    and previous_net is not None
                    and math.isclose(new_net, previous_net, rel_tol=1e-10, abs_tol=1e-10)
                    and isinstance(previous_breakdown, dict)
                    and not (
                        isinstance(new_breakdown, dict)
                        and new_breakdown.get("complete") is True
                    )
                ):
                    normalized["realized_pnl_breakdown"] = copy.deepcopy(previous_breakdown)
            if (
                exchange_id == "binance"
                and market_scope
                and not binance_position_matches_scope(normalized, market_scope)
            ):
                current.pop(key, None)
                continue
            if is_open_position(normalized):
                current[key] = normalized
            else:
                current.pop(key, None)
        positions = sorted(
            current.values(),
            key=lambda item: (
                str(item.get("market_scope") or item.get("dex") or ""),
                str(item.get("symbol") or ""),
                str(item.get("side") or ""),
            ),
        )
        result["positions"] = positions
        result["position_count"] = len(positions)
        result["stream_status"] = "live"
        if positions != previous_positions or previous_stream_status != "live":
            self.mark_dirty()

    def apply_mark(
        self,
        account_name: str,
        symbol: str,
        mark_price: float,
        market: dict[str, Any] | None,
    ) -> None:
        changed = False
        for position in self.positions(account_name):
            if not isinstance(position, dict) or position.get("symbol") != symbol:
                continue
            position["mark_price"] = mark_price
            pnl = _calculate_unrealized_pnl(position, mark_price, market)
            if pnl is not None:
                position["unrealized_pnl"] = pnl
                margin_basis = _position_margin_basis(position, market)
                position["percentage"] = (
                    pnl / margin_basis * 100
                    if margin_basis not in {None, 0}
                    else None
                )
            changed = True
        if changed:
            self.mark_dirty()

    def payload(self) -> dict[str, Any]:
        payload = copy.deepcopy(self.snapshot)
        results = payload.get("results", [])
        successful = [
            result
            for result in results
            if isinstance(result, dict) and result.get("status") == "ok"
        ]
        payload["collected_at"] = utc_now()
        payload["cached"] = False
        payload["live"] = True
        payload["sequence"] = self.sequence
        payload["summary"] = {
            "accounts": len(results),
            "succeeded": len(successful),
            "failed": len(results) - len(successful),
            "open_positions": sum(
                len(result.get("positions", []))
                for result in successful
                if isinstance(result.get("positions", []), list)
            ),
        }
        return payload


class AccountHistoryBuffer:
    def __init__(self) -> None:
        self.dirty = asyncio.Event()
        self._orders: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        self._fills: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    def add_orders(
        self,
        account: str,
        exchange: str,
        orders: list[dict[str, Any]],
        market_scope: str,
    ) -> None:
        changed = False
        for raw_order in orders:
            if not isinstance(raw_order, dict):
                continue
            payload = normalize_order(raw_order)
            key = (
                account,
                exchange,
                market_scope,
                str(payload.get("symbol") or ""),
                str(payload.get("id") or ""),
            )
            previous = self._orders.get(key)
            if previous is not None:
                previous_timestamp = _number(
                    previous["payload"].get("updated_timestamp")
                )
                timestamp = _number(payload.get("updated_timestamp"))
                if (
                    previous_timestamp is not None
                    and timestamp is not None
                    and timestamp < previous_timestamp
                ):
                    continue
            self._orders[key] = {
                "account": account,
                "exchange": exchange,
                "market_scope": market_scope,
                "payload": payload,
            }
            changed = True
        if changed:
            self.dirty.set()

    def add_fills(
        self,
        account: str,
        exchange: str,
        fills: list[dict[str, Any]],
        market_scope: str,
    ) -> None:
        changed = False
        for raw_fill in fills:
            if not isinstance(raw_fill, dict):
                continue
            payload = normalize_fill(raw_fill)
            key = (
                account,
                exchange,
                market_scope,
                str(payload.get("symbol") or ""),
                str(payload.get("id") or ""),
            )
            self._fills[key] = {
                "account": account,
                "exchange": exchange,
                "market_scope": market_scope,
                "payload": payload,
            }
            changed = True
        if changed:
            self.dirty.set()

    def drain(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        orders = list(self._orders.values())
        fills = list(self._fills.values())
        self._orders.clear()
        self._fills.clear()
        return orders, fills

    def restore(
        self,
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
    ) -> None:
        for record in orders:
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            key = (
                str(record.get("account") or ""),
                str(record.get("exchange") or ""),
                str(record.get("market_scope") or ""),
                str(payload.get("symbol") or ""),
                str(payload.get("id") or ""),
            )
            self._orders.setdefault(key, record)
        for record in fills:
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            key = (
                str(record.get("account") or ""),
                str(record.get("exchange") or ""),
                str(record.get("market_scope") or ""),
                str(payload.get("symbol") or ""),
                str(payload.get("id") or ""),
            )
            self._fills.setdefault(key, record)
        if orders or fills:
            self.dirty.set()


def _calculate_unrealized_pnl(
    position: dict[str, Any],
    mark_price: float,
    market: dict[str, Any] | None,
) -> float | None:
    entry_price = _number(position.get("entry_price"))
    contracts = _number(position.get("contracts"))
    contract_size = _number(position.get("contract_size")) or 1.0
    if entry_price in {None, 0} or contracts in {None, 0} or mark_price <= 0:
        return None
    side = str(position.get("side") or "").lower()
    if side not in {"long", "short"}:
        return None
    direction = 1.0 if side == "long" else -1.0
    quantity = abs(contracts) * contract_size
    if market and market.get("inverse"):
        return direction * quantity * (1.0 / entry_price - 1.0 / mark_price)
    return direction * quantity * (mark_price - entry_price)


def _position_margin_basis(
    position: dict[str, Any],
    market: dict[str, Any] | None,
) -> float | None:
    for key in ("initial_margin", "collateral"):
        value = _number(position.get(key))
        if value is not None and value > 0:
            return value
    entry_price = _number(position.get("entry_price"))
    contracts = _number(position.get("contracts"))
    leverage = _number(position.get("leverage"))
    contract_size = _number(position.get("contract_size")) or 1.0
    if (
        entry_price in {None, 0}
        or contracts in {None, 0}
        or leverage in {None, 0}
    ):
        return None
    quantity = abs(contracts) * contract_size
    entry_notional = (
        quantity / entry_price
        if market and market.get("inverse")
        else quantity * entry_price
    )
    return entry_notional / leverage


def _build_live_exchange(account: ExchangeAccount) -> Any:
    exchange_class = getattr(ccxtpro, account.exchange_id, None)
    if exchange_class is None:
        raise ValueError(f"unsupported CCXT Pro exchange: {account.exchange_id}")
    config = merge_dicts(
        {"enableRateLimit": True, "timeout": 30_000},
        account.config,
    )
    sandbox = bool(config.pop("isTestnet", False) or config.pop("sandbox", False))
    options = config.get("options")
    options = dict(options) if isinstance(options, dict) else {}
    options.setdefault("defaultType", "swap")
    if account.exchange_id == "binance":
        watch_positions = options.get("watchPositions")
        watch_positions = (
            dict(watch_positions) if isinstance(watch_positions, dict) else {}
        )
        # Our REST snapshot is authoritative. CCXT Pro 4.5.58 drops subType
        # while building Binance's COIN-M WS snapshot and can load USD-M twice.
        watch_positions["fetchPositionsSnapshot"] = False
        watch_positions["awaitPositionsSnapshot"] = False
        options["watchPositions"] = watch_positions
    config["options"] = options
    exchange = exchange_class(config)
    if sandbox:
        exchange.set_sandbox_mode(True)
    if account.exchange_id == "hyperliquid":
        if not exchange.walletAddress:
            raise ccxt.ArgumentsRequired("hyperliquid walletAddress is required")
    else:
        exchange.check_required_credentials()
    return exchange


def _subscriptions(
    exchange_id: str,
    positions: list[dict[str, Any]],
) -> list[tuple[list[str] | None, dict[str, Any], str | None, str | None]]:
    if exchange_id == "binance":
        return [
            (None, {"type": "future", "subType": "linear"}, "usd_m", None),
            (None, {"type": "delivery", "subType": "inverse"}, "coin_m", None),
        ]
    if exchange_id == "bitget":
        grouped: dict[str, list[str]] = {}
        for position in positions:
            scope = str(position.get("market_scope") or "USDT-FUTURES")
            symbol = str(position.get("symbol") or "")
            if symbol:
                grouped.setdefault(scope, []).append(symbol)
        if not grouped:
            return [(None, {}, "USDT-FUTURES", None)]
        return [
            (sorted(set(symbols)), {}, scope, None)
            for scope, symbols in grouped.items()
        ]
    if exchange_id == "hyperliquid":
        grouped_dexes: dict[str, list[str]] = {}
        for position in positions:
            dex = str(position.get("dex") or "default")
            symbol = str(position.get("symbol") or "")
            if symbol:
                grouped_dexes.setdefault(dex, []).append(symbol)
        if not grouped_dexes:
            return [(None, {}, None, "default")]
        return [
            (sorted(set(symbols)), {}, None, dex)
            for dex, symbols in grouped_dexes.items()
        ]
    return [(None, {}, None, None)]


def _history_subscriptions(account: ExchangeAccount) -> list[tuple[dict[str, Any], str]]:
    if account.exchange_id == "binance":
        return [
            ({"type": "future", "subType": "linear"}, "usd_m"),
            ({"type": "delivery", "subType": "inverse"}, "coin_m"),
        ]
    if account.exchange_id == "bitget":
        options = account.config.get("options")
        options = options if isinstance(options, dict) else {}
        if bool(account.config.get("uta") or options.get("uta")):
            return [({"uta": True}, "uta")]
        return [
            (
                {
                    "type": "swap",
                    "subType": "linear",
                    "productType": "USDT-FUTURES",
                },
                "USDT-FUTURES",
            ),
            (
                {
                    "type": "swap",
                    "subType": "linear",
                    "productType": "USDC-FUTURES",
                },
                "USDC-FUTURES",
            ),
            (
                {
                    "type": "swap",
                    "subType": "inverse",
                    "productType": "COIN-FUTURES",
                },
                "COIN-FUTURES",
            ),
        ]
    return [({}, "")]


async def _watch_private_orders(
    exchange: Any,
    account: ExchangeAccount,
    history: AccountHistoryBuffer,
    params: dict[str, Any],
    market_scope: str,
    stop: asyncio.Event,
) -> None:
    delay = 1.0
    secrets = secret_values(account.config)
    while not stop.is_set():
        try:
            orders = await exchange.watch_orders(None, params=params)
            if isinstance(orders, list):
                history.add_orders(
                    account.name,
                    account.exchange_id,
                    orders,
                    market_scope,
                )
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except (
            ccxt.ArgumentsRequired,
            ccxt.AuthenticationError,
            ccxt.BadRequest,
            ccxt.NotSupported,
            ccxt.PermissionDenied,
        ) as exc:
            print(
                f"[account] {account.name} order stream unavailable: "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}",
                file=sys.stderr,
                flush=True,
            )
            return
        except Exception as exc:
            print(
                f"[account] {account.name} order stream error: "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}",
                file=sys.stderr,
                flush=True,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 30.0)


async def _watch_private_fills(
    exchange: Any,
    account: ExchangeAccount,
    history: AccountHistoryBuffer,
    params: dict[str, Any],
    market_scope: str,
    stop: asyncio.Event,
) -> None:
    delay = 1.0
    secrets = secret_values(account.config)
    while not stop.is_set():
        try:
            fills = await exchange.watch_my_trades(None, params=params)
            if isinstance(fills, list):
                history.add_fills(
                    account.name,
                    account.exchange_id,
                    fills,
                    market_scope,
                )
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except (
            ccxt.ArgumentsRequired,
            ccxt.AuthenticationError,
            ccxt.BadRequest,
            ccxt.NotSupported,
            ccxt.PermissionDenied,
        ) as exc:
            print(
                f"[account] {account.name} fill stream unavailable: "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}",
                file=sys.stderr,
                flush=True,
            )
            return
        except Exception as exc:
            print(
                f"[account] {account.name} fill stream error: "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}",
                file=sys.stderr,
                flush=True,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 30.0)


async def _watch_private_positions(
    exchange: Any,
    account: ExchangeAccount,
    state: LivePositionState,
    symbols: list[str] | None,
    params: dict[str, Any],
    market_scope: str | None,
    dex: str | None,
    stop: asyncio.Event,
) -> None:
    delay = 1.0
    secrets = secret_values(account.config)
    while not stop.is_set():
        try:
            positions = await exchange.watch_positions(symbols, params=params)
            if isinstance(positions, list):
                state.merge_positions(
                    account.name,
                    positions,
                    exchange_id=account.exchange_id,
                    market_scope=market_scope,
                    dex=dex,
                )
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"[account] {account.name} position stream error: "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}",
                file=sys.stderr,
                flush=True,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 30.0)


def _ticker_mark(ticker: dict[str, Any]) -> float | None:
    for key in ("mark", "markPrice"):
        value = _number(ticker.get(key))
        if value is not None and value > 0:
            return value
    info = ticker.get("info")
    if isinstance(info, dict):
        for key in ("markPrice", "markPx", "mark_price", "lastPr", "lastPrice"):
            value = _number(info.get(key))
            if value is not None and value > 0:
                return value
    for key in ("last", "close"):
        value = _number(ticker.get(key))
        if value is not None and value > 0:
            return value
    return None


async def _watch_mark(
    exchange: Any,
    account: ExchangeAccount,
    state: LivePositionState,
    symbol: str,
    stop: asyncio.Event,
) -> None:
    delay = 1.0
    secrets = secret_values(account.config)
    while not stop.is_set():
        try:
            if exchange.has.get("watchMarkPrice"):
                ticker = await exchange.watch_mark_price(symbol)
            else:
                ticker = await exchange.watch_ticker(symbol)
            mark_price = _ticker_mark(ticker if isinstance(ticker, dict) else {})
            if mark_price is not None:
                market = exchange.market(symbol) if symbol in exchange.markets else None
                state.apply_mark(account.name, symbol, mark_price, market)
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"[account] {account.name} {symbol} mark stream error: "
                f"{type(exc).__name__}: {redact_error(exc, secrets)}",
                file=sys.stderr,
                flush=True,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 30.0)


async def _ticker_supervisor(
    exchange: Any,
    account: ExchangeAccount,
    state: LivePositionState,
    stop: asyncio.Event,
) -> None:
    tasks: dict[str, asyncio.Task[None]] = {}
    try:
        while not stop.is_set():
            symbols = {
                str(position.get("symbol") or "")
                for position in state.positions(account.name)
                if isinstance(position, dict) and position.get("symbol")
            }
            for symbol in symbols - tasks.keys():
                tasks[symbol] = asyncio.create_task(
                    _watch_mark(exchange, account, state, symbol, stop)
                )
            for symbol in set(tasks) - symbols:
                tasks.pop(symbol).cancel()
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


async def _watch_account(
    account: ExchangeAccount,
    state: LivePositionState,
    history: AccountHistoryBuffer,
    stop: asyncio.Event,
) -> None:
    exchange = None
    tasks: list[asyncio.Task[None]] = []
    secrets = secret_values(account.config)
    try:
        exchange = _build_live_exchange(account)
        await exchange.load_markets()
        if not exchange.has.get("watchPositions"):
            raise ccxt.NotSupported(f"{account.exchange_id} does not support watchPositions")
        for symbols, params, market_scope, dex in _subscriptions(
            account.exchange_id,
            state.positions(account.name),
        ):
            tasks.append(
                asyncio.create_task(
                    _watch_private_positions(
                        exchange,
                        account,
                        state,
                        symbols,
                        params,
                        market_scope,
                        dex,
                        stop,
                    )
                )
            )
        tasks.append(asyncio.create_task(_ticker_supervisor(exchange, account, state, stop)))
        if exchange.has.get("watchOrders"):
            for params, market_scope in _history_subscriptions(account):
                tasks.append(asyncio.create_task(
                    _watch_private_orders(
                        exchange,
                        account,
                        history,
                        params,
                        market_scope,
                        stop,
                    )
                ))
        if exchange.has.get("watchMyTrades"):
            for params, market_scope in _history_subscriptions(account):
                tasks.append(asyncio.create_task(
                    _watch_private_fills(
                        exchange,
                        account,
                        history,
                        params,
                        market_scope,
                        stop,
                    )
                ))
        await stop.wait()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(
            f"[account] {account.name} live stream unavailable: "
            f"{type(exc).__name__}: {redact_error(exc, secrets)}",
            file=sys.stderr,
            flush=True,
        )
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


async def _emit_updates(state: LivePositionState, output_queue: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await state.dirty.wait()
        state.dirty.clear()
        try:
            await asyncio.wait_for(stop.wait(), timeout=EMIT_INTERVAL_SECONDS)
            break
        except TimeoutError:
            pass
        payload = state.payload()
        try:
            output_queue.put_nowait(payload)
        except queue.Full:
            try:
                output_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                output_queue.put_nowait(payload)
            except queue.Full:
                pass


async def _persist_updates(
    state: LivePositionState,
    cache: AccountCache,
    executor: ThreadPoolExecutor,
    stop: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        await state.persist_dirty.wait()
        state.persist_dirty.clear()
        try:
            await asyncio.wait_for(stop.wait(), timeout=PERSIST_INTERVAL_SECONDS)
            break
        except TimeoutError:
            pass
        try:
            await loop.run_in_executor(
                executor,
                cache.apply_positions_snapshot,
                state.payload(),
            )
        except Exception as exc:
            print(
                f"[account] position cache write failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )


async def _persist_history_updates(
    history: AccountHistoryBuffer,
    cache: AccountCache,
    executor: ThreadPoolExecutor,
    stop: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        await history.dirty.wait()
        try:
            await asyncio.wait_for(stop.wait(), timeout=PERSIST_INTERVAL_SECONDS)
            break
        except TimeoutError:
            pass
        history.dirty.clear()
        orders, fills = history.drain()
        try:
            await loop.run_in_executor(
                executor,
                cache.apply_history_batch,
                orders,
                fills,
            )
        except Exception as exc:
            history.restore(orders, fills)
            print(
                f"[account] history cache write failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )


async def _reconcile(
    config_path: str,
    state: LivePositionState,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=RECONCILE_INTERVAL_SECONDS)
            break
        except TimeoutError:
            pass
        post_bar_delay = seconds_until_post_bar_task_window()
        if post_bar_delay > 0.0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=post_bar_delay)
                break
            except TimeoutError:
                pass
        try:
            snapshot = await asyncio.to_thread(collect_positions_snapshot, config_path)
            state.replace(snapshot)
        except Exception as exc:
            print(
                f"[account] position reconciliation failed: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )


async def _run_positions_stream(
    config_path: str,
    cache_path: str,
    output_queue: Any,
    stop_event: Any,
    initial_snapshot: dict[str, Any] | None,
) -> None:
    snapshot = initial_snapshot or await asyncio.to_thread(
        collect_positions_snapshot,
        config_path,
    )
    state = LivePositionState(snapshot)
    state.mark_dirty()
    history = AccountHistoryBuffer()
    cache = AccountCache(Path(cache_path))
    cache_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="account-cache-writer",
    )
    data = read_provider_config(Path(config_path))
    accounts = configured_accounts(data)
    stop = asyncio.Event()

    async def watch_parent_stop() -> None:
        await asyncio.to_thread(stop_event.wait)
        stop.set()

    tasks = [
        asyncio.create_task(_watch_account(account, state, history, stop))
        for account in accounts
    ]
    tasks.extend(
        [
            asyncio.create_task(_emit_updates(state, output_queue, stop)),
            asyncio.create_task(_persist_updates(state, cache, cache_executor, stop)),
            asyncio.create_task(
                _persist_history_updates(history, cache, cache_executor, stop)
            ),
            asyncio.create_task(_reconcile(config_path, state, stop)),
            asyncio.create_task(watch_parent_stop()),
        ]
    )
    try:
        await stop.wait()
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                cache_executor,
                cache.apply_positions_snapshot,
                state.payload(),
            )
        except Exception as exc:
            print(
                f"[account] final position cache write failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        orders, fills = history.drain()
        if orders or fills:
            try:
                await loop.run_in_executor(
                    cache_executor,
                    cache.apply_history_batch,
                    orders,
                    fills,
                )
            except Exception as exc:
                print(
                    f"[account] final history cache write failed: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        await loop.run_in_executor(cache_executor, cache.close)
        cache_executor.shutdown(wait=True, cancel_futures=True)


def run_positions_stream(
    config_path: str,
    cache_path: str,
    output_queue: Any,
    stop_event: Any,
    initial_snapshot: dict[str, Any] | None = None,
) -> None:
    try:
        asyncio.run(
            _run_positions_stream(
                config_path,
                cache_path,
                output_queue,
                stop_event,
                initial_snapshot,
            )
        )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(
            f"[account] live account process stopped: {type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
    finally:
        try:
            output_queue.put_nowait(None)
        except Exception:
            pass
