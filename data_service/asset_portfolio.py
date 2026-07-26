from __future__ import annotations

import argparse
import asyncio
import copy
import math
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ccxt

from ai.scripts.asset import (
    DEFAULT_ACCOUNT_TYPES,
    ExchangeAccount,
    apply_cached_markets,
    collect_account,
    configured_accounts,
    number_or_none,
    read_provider_config,
    remove_binance_earn_receipts,
)


_LOCAL_QUOTES = {
    "bithumb": "KRW",
    "coinone": "KRW",
    "korbit": "KRW",
    "upbit": "KRW",
}
_USD_QUOTES = {
    "BUSD",
    "DAI",
    "FDUSD",
    "PYUSD",
    "TUSD",
    "USD",
    "USDC",
    "USDP",
    "USDT",
}
_SPOT_ONLY_EXCHANGES = {"bithumb", "coinone", "korbit", "upbit"}
_UNIFIED_TRADING_EXCHANGES = {"bybit", "okx"}
_MARKET_REFRESH_INTERVAL_SECONDS = 30 * 60


class AssetPortfolioError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _finite(value: Any, default: float = 0.0) -> float:
    parsed = number_or_none(value)
    return float(parsed) if parsed is not None else default


def _quote_currency(exchange_id: str) -> str:
    if exchange_id == "hyperliquid":
        return "USDC"
    return _LOCAL_QUOTES.get(exchange_id, "USDT")


def _asset_amount(asset: dict[str, Any]) -> float:
    total = number_or_none(asset.get("total"))
    if total is None:
        total = _finite(asset.get("free")) + _finite(asset.get("used"))
    return float(total) - _finite(asset.get("debt"))


def _aggregate_account_results(
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    holdings: dict[str, dict[str, Any]] = {}
    statuses: list[dict[str, Any]] = []

    for result in results:
        status = str(result.get("status") or "error")
        account_type = str(result.get("account_type") or "unknown")
        status_item: dict[str, Any] = {
            "account_type": account_type,
            "status": status,
        }
        error = result.get("error")
        if isinstance(error, dict) and error.get("message"):
            status_item["message"] = str(error["message"])[:300]
        statuses.append(status_item)
        if status != "ok":
            continue

        assets = result.get("assets")
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            currency = str(asset.get("currency") or "").strip().upper()
            if not currency:
                continue
            item = holdings.setdefault(
                currency,
                {
                    "currency": currency,
                    "amount": 0.0,
                    "free": 0.0,
                    "used": 0.0,
                    "debt": 0.0,
                    "account_types": set(),
                },
            )
            item["amount"] += _asset_amount(asset)
            item["free"] += _finite(asset.get("free"))
            item["used"] += _finite(asset.get("used"))
            item["debt"] += _finite(asset.get("debt"))
            item["account_types"].add(account_type)

    normalized: list[dict[str, Any]] = []
    for item in holdings.values():
        if item["amount"] <= 0:
            continue
        item["account_types"] = sorted(item["account_types"])
        normalized.append(item)
    normalized.sort(key=lambda item: (-item["amount"], item["currency"]))
    return normalized, statuses


def _portfolio_account_types(exchange_id: str) -> list[str]:
    if exchange_id in _SPOT_ONLY_EXCHANGES:
        return ["spot"]
    if exchange_id == "okx":
        return ["spot", "funding"]
    return list(DEFAULT_ACCOUNT_TYPES)


def _deduplicate_unified_trading_results(
    exchange_id: str,
    results: list[dict[str, Any]],
) -> None:
    if exchange_id not in _UNIFIED_TRADING_EXCHANGES:
        return
    primary = next(
        (
            result
            for result in results
            if result.get("account_type") in {"spot", "swap", "margin"}
            and result.get("status") == "ok"
        ),
        None,
    )
    if primary is None:
        return
    primary_type = str(primary.get("account_type"))
    for result in results:
        if result is primary:
            continue
        if (
            result.get("account_type") in {"spot", "swap", "margin"}
            and result.get("status") == "ok"
        ):
            result["status"] = "duplicate"
            result["assets"] = []
            result["asset_count"] = 0
            result["duplicate_of"] = primary_type


def _market_rank(market: dict[str, Any]) -> tuple[int, int, str]:
    if market.get("spot"):
        market_type = 0
    elif market.get("linear"):
        market_type = 1
    else:
        market_type = 2
    return (
        1 if market.get("active") is False else 0,
        market_type,
        str(market.get("symbol") or ""),
    )


def _find_market(
    markets: dict[str, dict[str, Any]],
    base: str,
    quote: str,
) -> dict[str, Any] | None:
    matches = [
        market
        for market in markets.values()
        if str(market.get("base") or "").upper() == base
        and str(market.get("quote") or "").upper() == quote
    ]
    return min(matches, key=_market_rank) if matches else None


def _ticker_price(ticker: Any) -> float | None:
    if not isinstance(ticker, dict):
        return None
    for field in ("last", "close"):
        value = number_or_none(ticker.get(field))
        if value is not None and value > 0:
            return float(value)
    bid = number_or_none(ticker.get("bid"))
    ask = number_or_none(ticker.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (float(bid) + float(ask)) / 2
    return None


def _fetch_symbol_prices(
    exchange: ccxt.Exchange,
    symbols: set[str],
    params: dict[str, Any] | None = None,
) -> dict[str, float]:
    if not symbols:
        return {}

    request_params = params or {}
    prices: dict[str, float] = {}
    if exchange.has.get("fetchTickers"):
        try:
            tickers = exchange.fetch_tickers(sorted(symbols), request_params)
            if isinstance(tickers, dict):
                for symbol, ticker in tickers.items():
                    price = _ticker_price(ticker)
                    if price is not None:
                        prices[str(symbol)] = price
        except Exception:
            pass

    missing = symbols - set(prices)
    if exchange.has.get("fetchTicker"):
        for symbol in sorted(missing):
            try:
                price = _ticker_price(exchange.fetch_ticker(symbol, request_params))
            except Exception:
                continue
            if price is not None:
                prices[symbol] = price
    return prices


def _market_context_price(market: dict[str, Any]) -> float | None:
    info = market.get("info")
    if not isinstance(info, dict):
        return None
    for field in ("midPx", "markPx", "last", "close"):
        value = number_or_none(info.get(field))
        if value is not None and value > 0:
            return float(value)
    return None


def _build_public_exchange(exchange_id: str, timeout_ms: int) -> ccxt.Exchange:
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise ValueError(f"Unsupported CCXT exchange: {exchange_id}")

    client_config: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": timeout_ms,
    }
    if exchange_id == "hyperliquid":
        client_config["options"] = {
            "fetchMarkets": {
                "types": ["spot"],
            },
        }
    return exchange_class(client_config)


def _fetch_prices(
    exchange_id: str,
    currencies: list[str],
    quote: str,
    timeout_ms: int,
    market_source: ccxt.Exchange | None = None,
) -> tuple[dict[str, float], str | None]:
    prices: dict[str, float] = {}
    unresolved = set(currencies)
    if quote in unresolved:
        prices[quote] = 1.0
        unresolved.remove(quote)
    if quote in _USD_QUOTES:
        for currency in unresolved & _USD_QUOTES:
            prices[currency] = 1.0
        unresolved -= _USD_QUOTES
    if not unresolved:
        return prices, None

    try:
        exchange = _build_public_exchange(exchange_id, timeout_ms)
    except ValueError as exc:
        return prices, str(exc)
    try:
        if market_source is not None:
            try:
                apply_cached_markets(exchange, market_source)
                markets = exchange.markets
            except Exception:
                markets = exchange.load_markets()
        else:
            markets = exchange.load_markets()
        direct_markets: dict[str, dict[str, Any]] = {}
        bridge_markets: dict[str, dict[str, Any]] = {}
        quote_bridge = None
        if quote not in _USD_QUOTES:
            quote_bridge = _find_market(markets, "USDT", quote)

        for currency in unresolved:
            direct = _find_market(markets, currency, quote)
            if direct is not None:
                direct_markets[currency] = direct
                continue
            if quote_bridge is not None:
                bridge = _find_market(markets, currency, "USDT")
                if bridge is not None:
                    bridge_markets[currency] = bridge

        symbols = {
            str(market["symbol"])
            for market in (*direct_markets.values(), *bridge_markets.values())
        }
        if quote_bridge is not None and bridge_markets:
            symbols.add(str(quote_bridge["symbol"]))
        ticker_prices: dict[str, float] = {}
        if exchange_id == "hyperliquid":
            for market in direct_markets.values():
                price = _market_context_price(market)
                if price is not None:
                    ticker_prices[str(market["symbol"])] = price
        missing_symbols = symbols - set(ticker_prices)
        ticker_prices.update(_fetch_symbol_prices(
            exchange,
            missing_symbols,
            {"type": "spot"} if exchange_id == "hyperliquid" else None,
        ))

        for currency, market in direct_markets.items():
            price = ticker_prices.get(str(market["symbol"]))
            if price is not None:
                prices[currency] = price

        bridge_quote_price = (
            ticker_prices.get(str(quote_bridge["symbol"]))
            if quote_bridge is not None
            else None
        )
        if bridge_quote_price is not None:
            for currency, market in bridge_markets.items():
                bridge_price = ticker_prices.get(str(market["symbol"]))
                if bridge_price is not None:
                    prices[currency] = bridge_price * bridge_quote_price
        return prices, None
    except Exception as exc:
        message = str(exc).replace("\n", " ").strip()
        return prices, (message[:300] or type(exc).__name__)
    finally:
        try:
            exchange.close()
        except Exception:
            pass


def _value_portfolio(
    account: str,
    exchange_id: str,
    results: list[dict[str, Any]],
    timeout_ms: int,
    *,
    prices: dict[str, float] | None = None,
    price_error: str | None = None,
) -> dict[str, Any]:
    holdings, statuses = _aggregate_account_results(results)
    quote = _quote_currency(exchange_id)
    if prices is None:
        prices, price_error = _fetch_prices(
            exchange_id,
            [item["currency"] for item in holdings],
            quote,
            timeout_ms,
        )

    assets: list[dict[str, Any]] = []
    total_value = 0.0
    for holding in holdings:
        price = prices.get(holding["currency"])
        value = holding["amount"] * price if price is not None else None
        if value is not None and math.isfinite(value):
            total_value += value
        assets.append({
            **holding,
            "price": price,
            "value": value,
            "priced": value is not None,
        })

    for asset in assets:
        value = asset["value"]
        asset["weight"] = (
            value / total_value * 100
            if value is not None and total_value > 0
            else None
        )
    assets.sort(
        key=lambda item: (
            item["value"] is None,
            -(item["value"] or 0),
            item["currency"],
        )
    )
    account_type_breakdown = []
    for result in results:
        if result.get("status") != "ok":
            continue
        type_holdings, _ = _aggregate_account_results([result])
        type_total = 0.0
        unpriced_assets: list[str] = []
        for holding in type_holdings:
            price = prices.get(holding["currency"])
            if price is None:
                unpriced_assets.append(holding["currency"])
                continue
            value = holding["amount"] * price
            if math.isfinite(value):
                type_total += value
        account_type_breakdown.append({
            "account_type": str(result.get("account_type") or "unknown"),
            "total_value": type_total,
            "weight": type_total / total_value * 100 if total_value > 0 else 0.0,
            "asset_count": len(type_holdings),
            "unpriced_assets": unpriced_assets,
        })

    succeeded = sum(item["status"] == "ok" for item in statuses)
    failed = sum(item["status"] == "error" for item in statuses)
    warnings = []
    if price_error:
        warnings.append(f"Price lookup: {price_error}")
    if failed:
        warnings.append(f"{failed} account type request(s) failed")
    return {
        "account": account,
        "exchange": exchange_id,
        "quote_currency": quote,
        "total_value": total_value,
        "priced_asset_count": sum(item["priced"] for item in assets),
        "unpriced_asset_count": sum(not item["priced"] for item in assets),
        "assets": assets,
        "account_type_breakdown": account_type_breakdown,
        "account_type_statuses": statuses,
        "warnings": warnings,
        "status": "ok" if succeeded else ("error" if failed else "empty"),
    }


def _collect_exchange_portfolios(
    exchange_id: str,
    accounts: list[ExchangeAccount],
    args: argparse.Namespace,
    timeout_ms: int,
    market_source: ccxt.Exchange | None = None,
) -> list[dict[str, Any]]:
    raw_results = [
        result
        for account in accounts
        for result in collect_account(
            account,
            _portfolio_account_types(exchange_id),
            args,
            market_source,
        )
    ]
    remove_binance_earn_receipts(raw_results)
    results_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in raw_results:
        results_by_account[str(result.get("account") or "")].append(result)
    for account in accounts:
        _deduplicate_unified_trading_results(
            exchange_id,
            results_by_account.get(account.name, []),
        )

    currencies: set[str] = set()
    for account in accounts:
        holdings, _ = _aggregate_account_results(
            results_by_account.get(account.name, [])
        )
        currencies.update(item["currency"] for item in holdings)
    prices, price_error = _fetch_prices(
        exchange_id,
        sorted(currencies),
        _quote_currency(exchange_id),
        timeout_ms,
        market_source,
    )
    return [
        _value_portfolio(
            account.name,
            exchange_id,
            results_by_account.get(account.name, []),
            timeout_ms,
            prices=prices,
            price_error=price_error,
        )
        for account in accounts
    ]


class _AssetMarketCache:
    def __init__(self, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms
        self._lock = threading.RLock()
        self._sources: dict[str, ccxt.Exchange] = {}

    def get(self, exchange_id: str) -> ccxt.Exchange | None:
        with self._lock:
            return self._sources.get(exchange_id)

    def refresh(self, exchange_id: str) -> None:
        source = _build_public_exchange(exchange_id, self.timeout_ms)
        try:
            source.load_markets()
        except Exception:
            try:
                source.close()
            except Exception:
                pass
            raise

        with self._lock:
            previous = self._sources.get(exchange_id)
            self._sources[exchange_id] = source
        if previous is not None:
            try:
                previous.close()
            except Exception:
                pass

    def retain(self, exchange_ids: set[str]) -> None:
        with self._lock:
            removed = [
                self._sources.pop(exchange_id)
                for exchange_id in set(self._sources) - exchange_ids
            ]
        for source in removed:
            try:
                source.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            sources = list(self._sources.values())
            self._sources.clear()
        for source in sources:
            try:
                source.close()
            except Exception:
                pass


class AssetPortfolioService:
    def __init__(
        self,
        config_path: Path,
        *,
        cache_ttl_seconds: float = 30.0,
        timeout_ms: int = 30_000,
        attempts: int = 3,
        market_refresh_interval_seconds: float = _MARKET_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        self.config_path = config_path
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_ms = timeout_ms
        self.attempts = attempts
        self.market_refresh_interval_seconds = market_refresh_interval_seconds
        self._lock = asyncio.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._market_cache = _AssetMarketCache(timeout_ms)
        self._market_ready = asyncio.Event()
        self._market_stop = asyncio.Event()
        self._market_refresh_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._market_refresh_task is not None:
            return
        self._market_stop.clear()
        self._market_ready.clear()
        self._market_refresh_task = asyncio.create_task(
            self._market_refresh_loop(),
            name="asset-market-cache",
        )

    async def close(self) -> None:
        task = self._market_refresh_task
        if task is not None:
            self._market_stop.set()
            await task
            self._market_refresh_task = None
        await asyncio.to_thread(self._market_cache.close)

    def _configured_exchange_ids(self) -> set[str]:
        data = read_provider_config(self.config_path)
        return {
            account.exchange_id
            for account in configured_accounts(data)
        }

    async def _refresh_market_cache(self) -> None:
        exchange_ids = await asyncio.to_thread(self._configured_exchange_ids)
        self._market_cache.retain(exchange_ids)
        if not exchange_ids:
            return

        ordered_ids = sorted(exchange_ids)
        results = await asyncio.gather(*(
            asyncio.to_thread(self._market_cache.refresh, exchange_id)
            for exchange_id in ordered_ids
        ), return_exceptions=True)
        for exchange_id, result in zip(ordered_ids, results):
            if isinstance(result, Exception):
                message = str(result).replace("\n", " ").strip()
                print(
                    f"[asset] market cache refresh failed exchange={exchange_id}: "
                    f"{message[:300] or type(result).__name__}"
                )

    async def _market_refresh_loop(self) -> None:
        first_refresh = True
        while not self._market_stop.is_set():
            try:
                await self._refresh_market_cache()
            except Exception as exc:
                message = str(exc).replace("\n", " ").strip()
                print(
                    f"[asset] market cache refresh failed: "
                    f"{message[:300] or type(exc).__name__}"
                )
            finally:
                if first_refresh:
                    self._market_ready.set()
                    first_refresh = False
            try:
                await asyncio.wait_for(
                    self._market_stop.wait(),
                    timeout=self.market_refresh_interval_seconds,
                )
            except TimeoutError:
                pass

    async def _wait_for_market_cache(self) -> None:
        if self._market_refresh_task is None:
            await self.start()
        await self._market_ready.wait()

    def _cache_valid(self) -> bool:
        return (
            self._cached is not None
            and time.monotonic() - self._cached_at < self.cache_ttl_seconds
        )

    async def invalidate(self) -> None:
        async with self._lock:
            self._cached = None
            self._cached_at = 0.0

    async def snapshot(self, *, force: bool = False) -> dict[str, Any]:
        requested_at = time.monotonic()
        if not force and self._cache_valid():
            result = copy.deepcopy(self._cached)
            result["cached"] = True
            return result

        async with self._lock:
            if (
                force
                and self._cached is not None
                and self._cached_at >= requested_at
            ):
                result = copy.deepcopy(self._cached)
                result["cached"] = True
                return result
            if not force and self._cache_valid():
                result = copy.deepcopy(self._cached)
                result["cached"] = True
                return result
            await self._wait_for_market_cache()
            try:
                result = await asyncio.to_thread(self._collect_snapshot)
            except Exception as exc:
                message = str(exc).replace("\n", " ").strip()
                raise AssetPortfolioError(message[:500] or type(exc).__name__) from exc
            self._cached = result
            self._cached_at = time.monotonic()
            output = copy.deepcopy(result)
            output["cached"] = False
            return output

    def _collect_snapshot(self) -> dict[str, Any]:
        data = read_provider_config(self.config_path)
        accounts = configured_accounts(data)
        if not accounts:
            return {
                "collected_at": _utc_now(),
                "read_only": True,
                "portfolios": [],
                "totals_by_quote": [],
                "summary": {
                    "accounts": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "priced_assets": 0,
                    "unpriced_assets": 0,
                },
            }

        args = argparse.Namespace(
            timeout_ms=self.timeout_ms,
            attempts=self.attempts,
            currencies=set(),
            include_zero=False,
        )
        accounts_by_exchange: dict[str, list[ExchangeAccount]] = defaultdict(list)
        for account in accounts:
            accounts_by_exchange[account.exchange_id].append(account)
        portfolios_by_account: dict[str, dict[str, Any]] = {}
        worker_count = min(4, len(accounts_by_exchange))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="asset-exchange",
        ) as executor:
            futures = {
                exchange_id: executor.submit(
                    _collect_exchange_portfolios,
                    exchange_id,
                    exchange_accounts,
                    args,
                    self.timeout_ms,
                    self._market_cache.get(exchange_id),
                )
                for exchange_id, exchange_accounts in accounts_by_exchange.items()
            }
            for future in futures.values():
                for portfolio in future.result():
                    portfolios_by_account[portfolio["account"]] = portfolio
        portfolios = [portfolios_by_account[account.name] for account in accounts]
        totals: dict[str, float] = defaultdict(float)
        for portfolio in portfolios:
            totals[portfolio["quote_currency"]] += portfolio["total_value"]
        totals_by_quote = [
            {"currency": currency, "value": value}
            for currency, value in sorted(totals.items())
        ]
        return {
            "collected_at": _utc_now(),
            "read_only": True,
            "portfolios": portfolios,
            "totals_by_quote": totals_by_quote,
            "summary": {
                "accounts": len(portfolios),
                "succeeded": sum(item["status"] == "ok" for item in portfolios),
                "failed": sum(item["status"] == "error" for item in portfolios),
                "priced_assets": sum(item["priced_asset_count"] for item in portfolios),
                "unpriced_assets": sum(item["unpriced_asset_count"] for item in portfolios),
            },
        }
