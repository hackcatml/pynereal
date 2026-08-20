from __future__ import annotations

import asyncio
import copy
import json
import math
import multiprocessing
import os
import queue
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ccxt

from cmc_logos import (
    CATALOG_CHECK_SECONDS,
    CmcLogoCatalog,
    CmcLogoImageCache,
)
from tv_logos import exchange_logo_url
from ws_manager import WSManager


WATCHLIST_EXCHANGES = ("binance", "bitget", "bybit", "okx", "hyperliquid")
WATCHLIST_REFRESH_SECONDS = 5.0
WATCHLIST_IDLE_GRACE_SECONDS = 30.0
HYPERLIQUID_HIP3_REFRESH_SECONDS = 30.0
MARKET_RELOAD_SECONDS = 30 * 60.0


@dataclass(frozen=True)
class ExchangeSpec:
    exchange_id: str
    class_name: str
    options: dict[str, Any]
    ticker_params: dict[str, Any]
    quotes: frozenset[str]


EXCHANGE_SPECS = (
    ExchangeSpec(
        "binance",
        "binanceusdm",
        {
            "defaultType": "swap",
            "defaultSubType": "linear",
            "fetchMarkets": {"types": ["linear"]},
        },
        {},
        frozenset({"USDT", "USDC"}),
    ),
    ExchangeSpec(
        "bitget",
        "bitget",
        {
            "defaultType": "swap",
            "defaultSubType": "linear",
            "fetchMarkets": {"types": ["swap"]},
        },
        {"type": "swap", "productType": "USDT-FUTURES"},
        frozenset({"USDT"}),
    ),
    ExchangeSpec(
        "bybit",
        "bybit",
        {
            "defaultType": "swap",
            "defaultSubType": "linear",
            "fetchMarkets": {"types": ["linear"]},
        },
        {"type": "swap", "subType": "linear"},
        frozenset({"USDT", "USDC"}),
    ),
    ExchangeSpec(
        "okx",
        "okx",
        {
            "defaultType": "swap",
            "fetchMarkets": {"types": ["swap"]},
        },
        {"type": "swap"},
        frozenset({"USDT", "USDC"}),
    ),
    ExchangeSpec(
        "hyperliquid",
        "hyperliquid",
        {
            "defaultType": "swap",
            "fetchMarkets": {
                "types": ["swap", "hip3"],
                "hip3": {"limit": 10, "dexes": []},
            },
        },
        {"type": "swap"},
        frozenset({"USDC"}),
    ),
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _log_error(message: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")
    print(f"[{timestamp}]{message}", file=sys.stderr, flush=True)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(source.get(key))
        if value is not None:
            return value
    return None


def _ticker_percentage(ticker: dict[str, Any], info: dict[str, Any]) -> float | None:
    percentage = _number(ticker.get("percentage"))
    if percentage is not None:
        return percentage
    for key in ("change24h", "price24hPcnt"):
        value = _number(info.get(key))
        if value is not None:
            return value * 100.0
    percentage = _number(info.get("P"))
    if percentage is not None:
        return percentage
    last = _number(ticker.get("last"))
    reference = _first_number(ticker, "open", "previousClose")
    if reference is None:
        reference = _number(info.get("prevDayPx"))
    if last is not None and reference not in (None, 0.0):
        return (last / reference - 1.0) * 100.0
    return None


def _ticker_turnover(
    exchange_id: str,
    ticker: dict[str, Any],
    info: dict[str, Any],
    market: dict[str, Any],
    last: float | None,
) -> float | None:
    if exchange_id == "okx" and market.get("swap"):
        base_volume = _number(info.get("volCcy24h"))
        if base_volume is not None and last is not None:
            return base_volume * last
    value = _number(ticker.get("quoteVolume"))
    if value is not None:
        return value
    return _first_number(
        info,
        "turnover24h",
        "quoteVolume",
        "usdtVolume",
        "dayNtlVlm",
        "volCcy24h",
        "q",
    )


def _ticker_mark(ticker: dict[str, Any], info: dict[str, Any]) -> float | None:
    value = _first_number(ticker, "mark", "markPrice")
    if value is not None:
        return value
    return _first_number(info, "markPx", "markPrice", "markPr")


def _market_for_ticker(exchange: ccxt.Exchange, ticker: dict[str, Any]) -> dict[str, Any]:
    symbol = str(ticker.get("symbol") or "")
    market = exchange.markets.get(symbol)
    if isinstance(market, dict):
        return market
    return {}


def normalize_tickers(
    exchange_id: str,
    exchange: ccxt.Exchange,
    tickers: dict[str, Any],
    quotes: frozenset[str],
    logo_catalog: CmcLogoCatalog | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    for ticker in tickers.values():
        if not isinstance(ticker, dict):
            continue
        market = _market_for_ticker(exchange, ticker)
        if market:
            if not market.get("swap") or market.get("active") is False:
                continue
            if market.get("linear") is False:
                continue
        symbol = str(ticker.get("symbol") or market.get("symbol") or "").strip()
        if not symbol:
            continue
        base = str(market.get("base") or symbol.split("/", 1)[0]).upper()
        quote = str(market.get("quote") or "").upper()
        if not quote and "/" in symbol:
            quote = symbol.split("/", 1)[1].split(":", 1)[0].upper()
        if quote not in quotes:
            continue
        info = ticker.get("info")
        info = info if isinstance(info, dict) else {}
        last = _number(ticker.get("last"))
        mark = _ticker_mark(ticker, info)
        if last is None:
            last = mark or _first_number(info, "lastPr", "last", "midPx")
        if last is None:
            continue
        timestamp = _number(ticker.get("timestamp")) or _first_number(
            info, "ts", "time", "E"
        )
        created = _number(market.get("created"))
        rows.append({
            "exchange": exchange_id,
            "exchange_logo_url": exchange_logo_url(exchange_id),
            "symbol_logo_url": (
                logo_catalog.resolve_url(exchange_id, base)
                if logo_catalog is not None
                else ""
            ),
            "symbol": symbol,
            "market_id": str(market.get("id") or info.get("symbol") or symbol),
            "base": base,
            "quote": quote,
            "category": (
                logo_catalog.resolve_asset_class(exchange_id, base)
                if logo_catalog is not None
                else "crypto"
            ),
            "last": last,
            "mark": mark,
            "change_24h": _ticker_percentage(ticker, info),
            "turnover_24h": _ticker_turnover(
                exchange_id,
                ticker,
                info,
                market,
                last,
            ),
            "updated_at": int(timestamp or now_ms),
            "is_new": bool(created and now_ms - created <= 30 * 86_400_000),
        })
    rows.sort(key=lambda row: (-(row.get("turnover_24h") or 0.0), row["symbol"]))
    return rows


class _ExchangeCollector:
    def __init__(self, spec: ExchangeSpec, logo_catalog: CmcLogoCatalog) -> None:
        self.spec = spec
        self.logo_catalog = logo_catalog
        exchange_class = getattr(ccxt, spec.class_name)
        self.exchange: ccxt.Exchange = exchange_class({
            "enableRateLimit": True,
            "timeout": 12_000,
            "options": copy.deepcopy(spec.options),
        })
        self.markets_loaded_at = 0.0
        self.hip3_rows: list[dict[str, Any]] = []
        self.hip3_loaded_at = 0.0

    def _load_markets(self) -> None:
        now = time.monotonic()
        if not self.exchange.markets or now - self.markets_loaded_at >= MARKET_RELOAD_SECONDS:
            self.exchange.load_markets(reload=bool(self.exchange.markets))
            self.markets_loaded_at = now

    def collect(self) -> list[dict[str, Any]]:
        self._load_markets()
        tickers = self.exchange.fetch_tickers(None, dict(self.spec.ticker_params))
        rows = normalize_tickers(
            self.spec.exchange_id,
            self.exchange,
            tickers if isinstance(tickers, dict) else {},
            self.spec.quotes,
            self.logo_catalog,
        )
        if self.spec.exchange_id != "hyperliquid":
            return rows

        now = time.monotonic()
        if now - self.hip3_loaded_at >= HYPERLIQUID_HIP3_REFRESH_SECONDS:
            hip3 = self.exchange.fetch_tickers(None, {"hip3": True})
            self.hip3_rows = normalize_tickers(
                self.spec.exchange_id,
                self.exchange,
                hip3 if isinstance(hip3, dict) else {},
                self.spec.quotes,
                self.logo_catalog,
            )
            self.hip3_loaded_at = now
        merged = {(row["exchange"], row["symbol"]): row for row in rows}
        merged.update({(row["exchange"], row["symbol"]): row for row in self.hip3_rows})
        return sorted(
            merged.values(),
            key=lambda row: (-(row.get("turnover_24h") or 0.0), row["symbol"]),
        )

    def close(self) -> None:
        try:
            self.exchange.close()
        except Exception:
            pass


def _replace_queue_item(output_queue: Any, payload: dict[str, Any]) -> None:
    try:
        output_queue.put_nowait(payload)
        return
    except queue.Full:
        pass
    try:
        output_queue.get_nowait()
    except queue.Empty:
        pass
    try:
        output_queue.put_nowait(payload)
    except queue.Full:
        pass


def _publish_snapshot(
    output_queue: Any,
    rows_by_exchange: dict[str, list[dict[str, Any]]],
    errors: dict[str, dict[str, str]],
    updated_exchanges: set[str],
) -> None:
    results = [
        row
        for exchange_id in WATCHLIST_EXCHANGES
        for row in rows_by_exchange.get(exchange_id, [])
    ]
    _replace_queue_item(output_queue, {
        "schema_version": "1.0",
        "collected_at": _utc_now(),
        "results": results,
        "errors": [errors[key] for key in WATCHLIST_EXCHANGES if key in errors],
        "updated_exchanges": sorted(updated_exchanges),
        "summary": {
            "exchanges": len(rows_by_exchange),
            "markets": len(results),
        },
    })


def run_watchlist_worker(
    output_queue: Any,
    stop_event: Any,
    logo_catalog_path: str,
    refresh_seconds: float = WATCHLIST_REFRESH_SECONDS,
) -> None:
    try:
        if hasattr(os, "nice"):
            try:
                os.nice(5)
            except OSError:
                pass
        logo_catalog = CmcLogoCatalog(Path(logo_catalog_path))
        for failure in logo_catalog.refresh_if_stale():
            _log_error(f"[watchlist] CMC logo refresh failed: {failure}")
        next_logo_check = time.monotonic() + CATALOG_CHECK_SECONDS
        collectors = {
            spec.exchange_id: _ExchangeCollector(spec, logo_catalog)
            for spec in EXCHANGE_SPECS
        }
        rows_by_exchange: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, dict[str, str]] = {}
        next_attempt = {exchange_id: 0.0 for exchange_id in collectors}
        failures = {exchange_id: 0 for exchange_id in collectors}
        last_logged: dict[str, tuple[str, float]] = {}
        pending_updates: set[str] = set()
        next_publish = 0.0
        with ThreadPoolExecutor(
            max_workers=len(collectors),
            thread_name_prefix="watchlist",
        ) as executor:
            in_flight: dict[Future[list[dict[str, Any]]], str] = {}
            while not stop_event.is_set():
                now = time.monotonic()
                if now >= next_logo_check:
                    for failure in logo_catalog.refresh_if_stale():
                        _log_error(f"[watchlist] CMC logo refresh failed: {failure}")
                    next_logo_check = time.monotonic() + CATALOG_CHECK_SECONDS
                active_exchanges = set(in_flight.values())
                for exchange_id, collector in collectors.items():
                    if exchange_id in active_exchanges or now < next_attempt[exchange_id]:
                        continue
                    in_flight[executor.submit(collector.collect)] = exchange_id
                if not in_flight:
                    stop_event.wait(0.1)
                    completed: set[Future[list[dict[str, Any]]]] = set()
                else:
                    completed, _ = wait(
                        tuple(in_flight),
                        timeout=0.25,
                        return_when=FIRST_COMPLETED,
                    )
                for future in completed:
                    exchange_id = in_flight.pop(future)
                    pending_updates.add(exchange_id)
                    try:
                        rows_by_exchange[exchange_id] = future.result()
                        errors.pop(exchange_id, None)
                        failures[exchange_id] = 0
                        next_attempt[exchange_id] = time.monotonic() + refresh_seconds
                    except Exception as exc:
                        failures[exchange_id] += 1
                        delay = min(refresh_seconds * (2 ** (failures[exchange_id] - 1)), 60.0)
                        next_attempt[exchange_id] = time.monotonic() + delay
                        message = str(exc).replace("\n", " ").strip()[:400]
                        errors[exchange_id] = {
                            "exchange": exchange_id,
                            "type": type(exc).__name__,
                            "message": message or type(exc).__name__,
                        }
                        previous, logged_at = last_logged.get(exchange_id, ("", 0.0))
                        if previous != message or time.monotonic() - logged_at >= 300.0:
                            _log_error(
                                f"[watchlist] {exchange_id} ticker refresh failed: "
                                f"{type(exc).__name__}: {message}"
                            )
                            last_logged[exchange_id] = (message, time.monotonic())
                now = time.monotonic()
                if pending_updates and now >= next_publish:
                    _publish_snapshot(
                        output_queue,
                        rows_by_exchange,
                        errors,
                        pending_updates,
                    )
                    pending_updates = set()
                    next_publish = now + refresh_seconds
        for collector in collectors.values():
            collector.close()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _log_error(f"[watchlist] worker stopped: {type(exc).__name__}: {exc}")
    finally:
        try:
            output_queue.put_nowait(None)
        except Exception:
            pass


class WatchlistFavoritesStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(exchange: str, symbol: str) -> tuple[str, str]:
        normalized_exchange = exchange.strip().lower()
        normalized_symbol = symbol.strip().upper()
        if normalized_exchange not in WATCHLIST_EXCHANGES:
            raise ValueError("unsupported watchlist exchange")
        if not normalized_symbol or len(normalized_symbol) > 100:
            raise ValueError("invalid watchlist symbol")
        return normalized_exchange, normalized_symbol

    def _read_unlocked(self) -> set[tuple[str, str]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (OSError, json.JSONDecodeError):
            return set()
        rows = payload.get("favorites") if isinstance(payload, dict) else []
        favorites: set[tuple[str, str]] = set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                favorites.add(self._normalize(
                    str(row.get("exchange") or ""),
                    str(row.get("symbol") or ""),
                ))
            except ValueError:
                continue
        return favorites

    def get(self) -> list[dict[str, str]]:
        with self._lock:
            values = self._read_unlocked()
        return [
            {"exchange": exchange, "symbol": symbol}
            for exchange, symbol in sorted(values)
        ]

    def set(self, exchange: str, symbol: str, favorite: bool) -> list[dict[str, str]]:
        key = self._normalize(exchange, symbol)
        with self._lock:
            values = self._read_unlocked()
            if favorite:
                values.add(key)
            else:
                values.discard(key)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": "1.0",
                "favorites": [
                    {"exchange": item[0], "symbol": item[1]}
                    for item in sorted(values)
                ],
            }
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        return [
            {"exchange": item[0], "symbol": item[1]}
            for item in sorted(values)
        ]


class WatchlistService:
    def __init__(self, favorites_path: Path, logo_cache_dir: Path) -> None:
        self.favorites = WatchlistFavoritesStore(favorites_path)
        self.logo_cache_dir = logo_cache_dir.resolve()
        self.logo_catalog_path = self.logo_cache_dir / "catalog.json"
        self.logo_images = CmcLogoImageCache(
            self.logo_cache_dir / "images",
            self.logo_catalog_path,
        )
        self._logo_download_limit = asyncio.Semaphore(4)
        self.live_ws = WSManager(on_disconnect=self._on_disconnect)
        self._clients: set[Any] = set()
        self._process_lock = asyncio.Lock()
        self._process: multiprocessing.Process | None = None
        self._output: Any = None
        self._stop: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._snapshot: dict[str, Any] | None = None

    def favorites_payload(self) -> dict[str, Any]:
        return {"schema_version": "1.0", "favorites": self.favorites.get()}

    def set_favorite(
        self,
        exchange: str,
        symbol: str,
        favorite: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "favorites": self.favorites.set(exchange, symbol, favorite),
        }

    async def broadcast_favorites(self, payload: dict[str, Any]) -> None:
        await self.live_ws.broadcast_json({
            "type": "watchlist.favorites",
            "payload": payload,
        })

    async def logo_path(self, cmc_id: int) -> Path:
        async with self._logo_download_limit:
            return await asyncio.to_thread(self.logo_images.get, cmc_id)

    async def connect_live(self, ws: Any) -> None:
        idle_task = self._idle_task
        self._idle_task = None
        if idle_task is not None:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        await self.live_ws.connect(ws)
        self._clients.add(ws)
        await self._ensure_worker()
        if self._snapshot is not None:
            await self.live_ws.send(ws, {
                "type": "watchlist.snapshot",
                "payload": copy.deepcopy(self._snapshot),
            })
        await self.live_ws.send(ws, {
            "type": "watchlist.favorites",
            "payload": self.favorites_payload(),
        })

    async def disconnect_live(self, ws: Any) -> None:
        await self.live_ws.disconnect(ws)

    async def _on_disconnect(self, ws: Any) -> None:
        self._clients.discard(ws)
        if self._clients or self._idle_task is not None:
            return
        self._idle_task = asyncio.create_task(self._stop_after_idle())

    async def _stop_after_idle(self) -> None:
        try:
            await asyncio.sleep(WATCHLIST_IDLE_GRACE_SECONDS)
            if not self._clients:
                await self._close_worker()
        except asyncio.CancelledError:
            pass
        finally:
            if self._idle_task is asyncio.current_task():
                self._idle_task = None

    async def _ensure_worker(self) -> None:
        async with self._process_lock:
            if self._process is not None and self._process.is_alive():
                return
            context = multiprocessing.get_context("spawn")
            self._output = context.Queue(maxsize=1)
            self._stop = context.Event()
            self._process = context.Process(
                target=run_watchlist_worker,
                args=(self._output, self._stop, str(self.logo_catalog_path)),
                name="pynereal-futures-watchlist",
                daemon=True,
            )
            self._process.start()
            self._reader = asyncio.create_task(self._read_output())

    def _merge_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._snapshot
        updated = {
            str(exchange)
            for exchange in payload.get("updated_exchanges", [])
            if str(exchange) in WATCHLIST_EXCHANGES
        }
        if current is None or not updated:
            return payload

        incoming_errors = {
            str(item.get("exchange")): item
            for item in payload.get("errors", [])
            if isinstance(item, dict) and item.get("exchange")
        }
        successful = updated.difference(incoming_errors)
        current_rows = current.get("results", [])
        incoming_rows = payload.get("results", [])
        rows = [
            row for row in current_rows
            if isinstance(row, dict) and str(row.get("exchange")) not in successful
        ]
        rows.extend(
            row for row in incoming_rows
            if isinstance(row, dict) and str(row.get("exchange")) in successful
        )

        errors = {
            str(item.get("exchange")): item
            for item in current.get("errors", [])
            if isinstance(item, dict) and item.get("exchange")
        }
        for exchange_id in updated:
            if exchange_id in incoming_errors:
                errors[exchange_id] = incoming_errors[exchange_id]
            else:
                errors.pop(exchange_id, None)
        merged = dict(payload)
        merged["results"] = rows
        merged["errors"] = [
            errors[key] for key in WATCHLIST_EXCHANGES if key in errors
        ]
        merged["summary"] = {
            "exchanges": len({
                str(row.get("exchange")) for row in rows if isinstance(row, dict)
            }),
            "markets": len(rows),
        }
        return merged

    async def _read_output(self) -> None:
        output = self._output
        if output is None:
            return
        while True:
            try:
                payload = await asyncio.to_thread(output.get, True, 1.0)
            except queue.Empty:
                if output is not self._output or self._process is None:
                    return
                continue
            if payload is None:
                return
            if not isinstance(payload, dict):
                continue
            self._snapshot = self._merge_snapshot(payload)
            await self.live_ws.broadcast_json({
                "type": "watchlist.snapshot",
                "payload": self._snapshot,
            })

    async def _close_worker(self) -> None:
        async with self._process_lock:
            process = self._process
            stop = self._stop
            reader = self._reader
            output = self._output
            self._process = None
            self._stop = None
            self._reader = None
            self._output = None
            if stop is not None:
                stop.set()
            if process is not None:
                await asyncio.to_thread(process.join, 10)
                if process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
            if reader is not None and not reader.done():
                try:
                    await asyncio.wait_for(reader, timeout=2)
                except TimeoutError:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
            if output is not None:
                output.close()
                await asyncio.to_thread(output.join_thread)

    async def close(self) -> None:
        idle_task = self._idle_task
        self._idle_task = None
        if idle_task is not None:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        await self._close_worker()
