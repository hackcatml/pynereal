from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx


CMC_DATA_API_URL = (
    "https://api.coinmarketcap.com/data-api/v3/exchange/market-pairs/latest"
)
CMC_LOGO_URL_TEMPLATE = (
    "https://s2.coinmarketcap.com/static/img/coins/64x64/{cmc_id}.png"
)
CMC_EXCHANGE_SLUGS = {
    "binance": "binance",
    "bitget": "bitget",
    "bybit": "bybit",
    "okx": "okx",
    "hyperliquid": "hyperliquid",
}
CATALOG_TTL_SECONDS = 24 * 60 * 60
CATALOG_CHECK_SECONDS = 30 * 60
MAX_LOGO_BYTES = 2 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_COMMODITY_SYMBOLS = {
    "BRENT", "BZ", "CL", "COPPER", "GOLD", "NATGAS", "OIL",
    "PALLADIUM", "PLATINUM", "SILVER", "WTI", "XAG", "XAU", "XPD", "XPT",
}
_COMMODITY_TERMS = (
    "brent oil",
    "copper",
    "crude oil",
    "gold",
    "natural gas",
    "palladium",
    "platinum",
    "silver",
)
_ETF_SYMBOLS = {
    "BITO", "CSOPSAMSUNG2L", "CSOPSKHYNIX2L", "DIA", "DRAM", "EWT", "EWJ",
    "EWY", "EWZ", "GDX", "IBIT", "IWM", "KODEX200", "KORU", "MUU", "MVLL",
    "QQQ", "SMH", "SNXX", "SOXL", "SOXS", "SPY", "SQQQ", "TBT", "TMF",
    "TQQQ", "TZA", "URNM", "UVXY", "XBI", "XLE",
}
_ETF_TERMS = (
    " etf",
    "exchange traded fund",
    "leveraged product",
    "proshares ultrapro",
    "proshares ultrashort",
)
_INDEX_SYMBOLS = {
    "DJI", "HK50", "JP225", "KR200", "NAS100", "NIKKEI", "SP500", "US100",
    "US30", "US500",
}
_INDEX_TERMS = (
    "dow jones index",
    "hang seng index",
    "index futures",
    "korea 200 index",
    "nasdaq 100 index",
    "nikkei 225 index",
    "s&p 500 index",
)
_FOREX_SYMBOLS = {
    "AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "KRW", "MXN", "NZD",
}


def _classify_asset(base_symbol: str, name: str, slug: str) -> str:
    symbol = base_symbol.strip().upper()
    normalized_name = " ".join(name.strip().lower().split())
    normalized_slug = slug.strip().lower()
    if symbol in _COMMODITY_SYMBOLS or any(
        term in normalized_name for term in _COMMODITY_TERMS
    ):
        return "commodities"
    if symbol in _ETF_SYMBOLS or any(term in normalized_name for term in _ETF_TERMS):
        return "etfs"
    if symbol in _INDEX_SYMBOLS or any(term in normalized_name for term in _INDEX_TERMS):
        return "other"
    if symbol in _FOREX_SYMBOLS:
        return "other"
    if normalized_name.endswith("(derivatives)") or normalized_slug.endswith("-derivatives"):
        return "stocks"
    return "crypto"


class CmcLogoCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = threading.Lock()
        self._symbols: dict[str, dict[str, int]] = {}
        self._asset_classes: dict[str, dict[str, str]] = {}
        self._updated_at: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        exchanges = payload.get("exchanges") if isinstance(payload, dict) else None
        if not isinstance(exchanges, dict):
            return
        for exchange_id in CMC_EXCHANGE_SLUGS:
            item = exchanges.get(exchange_id)
            if not isinstance(item, dict):
                continue
            raw_symbols = item.get("symbols")
            if not isinstance(raw_symbols, dict):
                continue
            symbols: dict[str, int] = {}
            for symbol, value in raw_symbols.items():
                try:
                    cmc_id = int(value)
                except (TypeError, ValueError):
                    continue
                normalized = str(symbol).strip().upper()
                if normalized and cmc_id > 0:
                    symbols[normalized] = cmc_id
            if symbols:
                self._symbols[exchange_id] = symbols
                raw_asset_classes = item.get("asset_classes")
                if isinstance(raw_asset_classes, dict):
                    asset_classes = {
                        str(symbol).strip().upper(): str(asset_class).strip().lower()
                        for symbol, asset_class in raw_asset_classes.items()
                        if str(asset_class).strip().lower()
                        in {"stocks", "etfs", "commodities", "other"}
                    }
                    self._asset_classes[exchange_id] = asset_classes
                try:
                    self._updated_at[exchange_id] = float(item.get("updated_at") or 0)
                except (TypeError, ValueError):
                    self._updated_at[exchange_id] = 0.0

    @staticmethod
    def _fetch_exchange(slug: str) -> tuple[dict[str, int], dict[str, str]]:
        start = 1
        limit = 1000
        rows: list[dict[str, Any]] = []
        total = 0
        with httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "PyneReal/Watchlist",
            },
        ) as client:
            while True:
                response = client.get(CMC_DATA_API_URL, params={
                    "slug": slug,
                    "start": start,
                    "limit": limit,
                    "category": "perpetual",
                    "centerType": "all",
                    "sort": "cmc_rank_advanced",
                })
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                page = data.get("marketPairs") if isinstance(data, dict) else None
                if not isinstance(page, list):
                    raise RuntimeError(f"invalid CoinMarketCap response for {slug}")
                rows.extend(item for item in page if isinstance(item, dict))
                try:
                    total = int(data.get("numMarketPairs") or len(rows))
                except (TypeError, ValueError):
                    total = len(rows)
                if not page or len(rows) >= total:
                    break
                start += len(page)

        symbols: dict[str, int] = {}
        asset_classes: dict[str, str] = {}
        ambiguous: set[str] = set()
        for row in rows:
            symbol = str(row.get("baseSymbol") or "").strip().upper()
            try:
                cmc_id = int(row.get("baseCurrencyId"))
            except (TypeError, ValueError):
                continue
            if not symbol or cmc_id <= 0 or symbol in ambiguous:
                continue
            previous = symbols.get(symbol)
            if previous is not None and previous != cmc_id:
                symbols.pop(symbol, None)
                asset_classes.pop(symbol, None)
                ambiguous.add(symbol)
                continue
            symbols[symbol] = cmc_id
            asset_class = _classify_asset(
                symbol,
                str(row.get("baseCurrencyName") or ""),
                str(row.get("baseCurrencySlug") or ""),
            )
            if asset_class != "crypto":
                asset_classes[symbol] = asset_class
        if not symbols:
            raise RuntimeError(f"empty CoinMarketCap catalog for {slug}")
        return symbols, asset_classes

    def refresh_if_stale(self) -> list[str]:
        with self._lock:
            now = time.time()
            stale = [
                exchange_id
                for exchange_id in CMC_EXCHANGE_SLUGS
                if (
                    now - self._updated_at.get(exchange_id, 0.0) >= CATALOG_TTL_SECONDS
                    or exchange_id not in self._asset_classes
                )
            ]
            if not stale:
                return []
            refreshed: dict[str, tuple[dict[str, int], dict[str, str]]] = {}
            failures: list[str] = []
            with ThreadPoolExecutor(
                max_workers=min(3, len(stale)),
                thread_name_prefix="cmc-logo",
            ) as executor:
                futures = {
                    executor.submit(
                        self._fetch_exchange,
                        CMC_EXCHANGE_SLUGS[exchange_id],
                    ): exchange_id
                    for exchange_id in stale
                }
                for future in as_completed(futures):
                    exchange_id = futures[future]
                    try:
                        refreshed[exchange_id] = future.result()
                    except Exception as exc:
                        failures.append(
                            f"{exchange_id}: {type(exc).__name__}: {exc}"
                        )
            if refreshed:
                refreshed_at = time.time()
                for exchange_id, (symbols, asset_classes) in refreshed.items():
                    self._symbols[exchange_id] = symbols
                    self._asset_classes[exchange_id] = asset_classes
                    self._updated_at[exchange_id] = refreshed_at
                self._write()
            return failures

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "exchanges": {
                exchange_id: {
                    "updated_at": self._updated_at.get(exchange_id, 0.0),
                    "symbols": self._symbols.get(exchange_id, {}),
                    "asset_classes": self._asset_classes.get(exchange_id, {}),
                }
                for exchange_id in CMC_EXCHANGE_SLUGS
                if self._symbols.get(exchange_id)
            },
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _symbol_candidates(exchange_id: str, base: str) -> tuple[str, ...]:
        normalized = base.strip().upper()
        candidates = [normalized] if normalized else []
        if exchange_id == "hyperliquid":
            for separator in (":", "-"):
                if separator in normalized:
                    candidate = normalized.rsplit(separator, 1)[-1].strip()
                    if candidate and candidate not in candidates:
                        candidates.append(candidate)
        return tuple(candidates)

    def resolve_id(self, exchange_id: str, base: str) -> int | None:
        exchange_id = exchange_id.strip().lower()
        with self._lock:
            sources = [self._symbols.get("binance", {})]
            if exchange_id != "binance":
                sources.append(self._symbols.get(exchange_id, {}))
            for candidate in self._symbol_candidates(exchange_id, base):
                for symbols in sources:
                    cmc_id = symbols.get(candidate)
                    if cmc_id is not None:
                        return cmc_id
        return None

    def resolve_url(self, exchange_id: str, base: str) -> str:
        cmc_id = self.resolve_id(exchange_id, base)
        return f"/api/watchlist/logos/{cmc_id}.png" if cmc_id is not None else ""

    def resolve_asset_class(self, exchange_id: str, base: str) -> str:
        exchange_id = exchange_id.strip().lower()
        with self._lock:
            sources = [self._asset_classes.get(exchange_id, {})]
            if exchange_id != "binance":
                sources.append(self._asset_classes.get("binance", {}))
            for candidate in self._symbol_candidates(exchange_id, base):
                for asset_classes in sources:
                    asset_class = asset_classes.get(candidate)
                    if asset_class is not None:
                        return asset_class
        return "crypto"


class CmcLogoImageCache:
    def __init__(self, directory: Path, catalog_path: Path) -> None:
        self.directory = directory.resolve()
        self.catalog_path = catalog_path.resolve()
        self._locks_guard = threading.Lock()
        self._locks: dict[int, threading.Lock] = {}
        self._catalog_mtime_ns = -1
        self._allowed_ids: set[int] = set()

    def _logo_lock(self, cmc_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(cmc_id, threading.Lock())

    def _is_allowed(self, cmc_id: int) -> bool:
        try:
            mtime_ns = self.catalog_path.stat().st_mtime_ns
        except OSError:
            return False
        with self._locks_guard:
            if mtime_ns != self._catalog_mtime_ns:
                try:
                    payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return False
                exchanges = payload.get("exchanges") if isinstance(payload, dict) else None
                allowed: set[int] = set()
                for item in exchanges.values() if isinstance(exchanges, dict) else ():
                    symbols = item.get("symbols") if isinstance(item, dict) else None
                    for value in symbols.values() if isinstance(symbols, dict) else ():
                        try:
                            value = int(value)
                        except (TypeError, ValueError):
                            continue
                        if value > 0:
                            allowed.add(value)
                self._allowed_ids = allowed
                self._catalog_mtime_ns = mtime_ns
            return cmc_id in self._allowed_ids

    def get(self, cmc_id: int) -> Path:
        if cmc_id <= 0 or cmc_id > 1_000_000_000:
            raise ValueError("invalid CoinMarketCap logo id")
        if not self._is_allowed(cmc_id):
            raise ValueError("unknown CoinMarketCap logo id")
        path = self.directory / f"{cmc_id}.png"
        if path.is_file() and path.stat().st_size > 0:
            return path
        with self._logo_lock(cmc_id):
            if path.is_file() and path.stat().st_size > 0:
                return path
            response = httpx.get(
                CMC_LOGO_URL_TEMPLATE.format(cmc_id=cmc_id),
                timeout=15.0,
                follow_redirects=True,
                headers={
                    "Accept": "image/png,image/*;q=0.8",
                    "User-Agent": "PyneReal/Watchlist",
                },
            )
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type") or "")
            data = response.content
            if (
                not content_type.lower().startswith("image/")
                or len(data) > MAX_LOGO_BYTES
                or not data.startswith(_PNG_SIGNATURE)
            ):
                raise RuntimeError("invalid CoinMarketCap logo response")
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{cmc_id}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
            return path
