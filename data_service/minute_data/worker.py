from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# data_service also supports script-style imports in its existing IO helpers.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ccxt.async_support as ccxt
import httpx

from ohlcv_io import make_ccxt_pro_client
from schedule_utils import seconds_until_bar_boundary_guard_end
from .archives import MAX_ARCHIVE_BYTES, archive_plan, prepare_archive
from .core import LIVE_PREFIX, Market, MinuteAccumulator, log_error, valid_bar
from .store import MinuteStore


async def wait_safe(minimum_remaining: float = 0) -> float:
    """Conservatively protect every minute boundary, including all strategy TFs."""
    while True:
        now = time.time()
        delay = seconds_until_bar_boundary_guard_end(now)
        remaining = 50 - now % 60
        if not delay and remaining > minimum_remaining:
            return remaining
        await asyncio.sleep(delay if delay else 60 - now % 60 + 10.05)


class Worker:
    def __init__(self, path: Path, primary_path: Path):
        self.store = MinuteStore(path)
        self.primary_path = primary_path
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="minute-storage")
        self.markets = {}
        self.clients = {}
        self.locks = {}
        self.http = httpx.AsyncClient(timeout=15, follow_redirects=True)
        self.closing = threading.Event()

    async def io(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, func, *args)

    async def guarded_io(self, func, *args):
        def execute():
            # Recheck in the IO thread: earlier queued parsing may cross a boundary.
            while not self.closing.is_set():
                now = time.time()
                delay = seconds_until_bar_boundary_guard_end(now)
                if not delay and 50 - now % 60 > 0.5:
                    return func(*args)
                self.closing.wait(delay if delay else 60 - now % 60 + 10.05)
        return await self.io(execute)

    async def status(self, key, **values):
        await wait_safe(0.25)
        await self.guarded_io(lambda: self.store.set_status(key, **values))

    async def request(self, exchange, factory):
        lock = self.locks.setdefault(exchange, asyncio.Lock())
        async with lock:
            remaining = await wait_safe(2)
            # Includes CCXT throttling: a request cannot start after the guard begins.
            async with asyncio.timeout(min(15, remaining - 0.25)):
                result = await factory()
            await asyncio.sleep(0.25)
        await wait_safe(0.25)
        return result

    async def client(self, market):
        key = (market.exchange, market.market_type)
        if key not in self.clients:
            client = make_ccxt_pro_client(ccxt, market.exchange, market_type=market.market_type, symbol=market.symbol)
            client.enableRateLimit = True
            client.timeout = 10000
            self.clients[key] = client
        client = self.clients[key]
        if not client.markets:
            await self.request(market.exchange, lambda: client.load_markets())
        return client

    async def write(self, market, rows, source, *, missing_only=False):
        for offset in range(0, len(rows), 1000):
            await wait_safe(0.5)
            await self.guarded_io(self.store.write, market.key, rows[offset:offset + 1000], source, missing_only)

    async def configure(self, configs):
        for key in list(self.markets):
            if key not in configs or self.markets[key].config != configs[key]:
                collector = self.markets.pop(key)
                await collector.close()
        for key, config in configs.items():
            if key not in self.markets:
                collector = Collector(self, config)
                self.markets[key] = collector
                collector.start()

    async def publish_live(self):
        # Chart snapshots do not wait for the guarded disk/REST path.
        while True:
            now = time.time()
            for collector in self.markets.values():
                snapshot = collector.live_snapshot(now)
                if snapshot is not None:
                    print(LIVE_PREFIX + json.dumps(snapshot, separators=(",", ":")), flush=True)
            await asyncio.sleep(0.25)

    async def close(self):
        self.closing.set()
        await asyncio.gather(*(c.close() for c in self.markets.values()))
        self.markets.clear()
        await asyncio.gather(*(c.close() for c in self.clients.values()), return_exceptions=True)
        await self.http.aclose()
        self.executor.shutdown(wait=True, cancel_futures=True)


class Collector:
    def __init__(self, worker: Worker, config: dict):
        self.worker = worker
        self.config = config
        self.market = Market(config["exchange"], config["symbol"], config["market_type"])
        self.accumulator = MinuteAccumulator()
        self.tasks = []
        self.market_info = None
        self.mirror_cursor = config["start"]
        self.last_refresh = int(time.time() // 60) * 60
        self.last_live_rows = []

    def start(self):
        self.tasks = [asyncio.create_task(self.history()), asyncio.create_task(self.refresh()),
                      asyncio.create_task(self.flush())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def receive(self, message):
        if message.get("source_id") != self.config["source_id"] or not self.market_info:
            return
        info = self.market_info
        contract = bool(info.get("contract"))
        inverse = bool(info.get("inverse"))
        # OKX public trades are contract quantities; its REST candles are base volume.
        factor = float(info.get("contractSize") or 1) if contract and (self.market.exchange == "okx" or inverse) else 1
        self.accumulator.add(message["rows"], time.time(), factor, inverse)

    def live_snapshot(self, now):
        cutoff = int(now // 60) * 60 - 60
        rows = [list(row) for ts, row in sorted(self.accumulator.bars.items()) if cutoff <= ts <= cutoff + 60]
        if rows == self.last_live_rows:
            return None
        self.last_live_rows = rows
        return {"market": self.market.key, "source_id": self.config["source_id"],
                "rows": rows, "updated_at": now}

    async def error(self, exc):
        log_error(self.market.key, exc)
        await self.worker.status(self.market.key, error=f"{type(exc).__name__}: {exc}")

    async def ready_client(self):
        client = await self.worker.client(self.market)
        self.market_info = client.market(self.market.symbol)
        return client

    async def rest_range(self, start: int, end: int):
        client = await self.ready_client()
        # Reserve two rows for endpoint inclusivity, using each history API's cap.
        page_size = {"binance": 1000, "bybit": 1000, "hyperliquid": 5000}.get(self.market.exchange, 200)
        while start < end:
            finish = min(start + (page_size - 2) * 60, end)
            # Bitget's recent endpoint excludes startTime; keep the storage range unchanged.
            since = (start - 60) * 1000 if self.market.exchange == "bitget" else start * 1000
            # Bitget rounds endTime down; subtracting 1ms excludes the last minute.
            until = finish * 1000 if self.market.exchange == "bitget" else finish * 1000 - 1
            rows = await self.worker.request(self.market.exchange, lambda: client.fetch_ohlcv(
                self.market.symbol, "1m", since, page_size, {"until": until}))
            normalized = []
            for row in rows:
                if start * 1000 <= row[0] < finish * 1000:
                    bar = valid_bar([int(row[0] // 1000), *row[1:6]])
                    if bar is None:
                        raise ValueError("exchange returned an invalid minute candle")
                    normalized.append(bar)
            if rows and not normalized:
                raise ValueError("exchange returned candles outside the requested range")
            await self.worker.write(self.market, normalized, "rest")
            # Absence is not permanent proof of completeness: empty intervals expire
            # and are checked again by the hourly gap sweep.
            present = {row[0] for row in normalized}
            missing_start = None
            for ts in range(start, finish + 60, 60):
                if ts < finish and ts not in present:
                    if missing_start is None:
                        missing_start = ts
                elif missing_start is not None:
                    await self.worker.guarded_io(self.worker.store.mark_checked, self.market.key, missing_start, ts)
                    missing_start = None
            start = finish

    async def download(self, url):
        async with self.worker.http.stream("GET", url) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            chunks, total = [], 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError("minute archive exceeds download size limit")
                chunks.append(chunk)
            return b"".join(chunks)

    async def bootstrap(self, end: int | None = None, *, use_archives: bool = True):
        end = min(end or int(time.time() // 60) * 60, int(time.time() // 60) * 60)
        start = self.config["start"]
        if self.market.exchange == "hyperliquid":
            start = max(start, int(time.time() // 60) * 60 - 4999 * 60)
        await self.worker.status(self.market.key, requested_start=self.config["start"],
                                 fetchable_start=start, state="backfilling", error=None)
        gaps = await self.worker.io(self.worker.store.gaps, self.market.key, start, end)
        if gaps or not self.config["reuse_primary"]:
            await self.ready_client()
        archives = archive_plan(self.market, self.market_info, start, end) if gaps and use_archives else []
        for archive in archives:
            if not any(a < archive.end and b > archive.start for a, b in gaps):
                continue
            try:
                data = await self.worker.request(self.market.exchange, lambda: self.download(archive.url))
                if data is None:
                    continue
                await wait_safe(5)
                previous = []
                if self.market.exchange == "bitget":
                    first = max(start, archive.start)
                    previous = await self.worker.io(self.worker.store.read, self.market.key, first - 60, first)
                rows, no_trade = await self.worker.io(prepare_archive, data, self.market, archive, start, end,
                                                      previous[0] if previous else None)
                await self.worker.write(self.market, rows, "archive", missing_only=True)
                await self.worker.write(self.market, no_trade, "archive_no_trade", missing_only=True)
            except Exception as exc:
                # Missing/unpublished files are repaired through the same REST path.
                log_error(self.market.key, exc)
        gaps = await self.worker.io(self.worker.store.gaps, self.market.key, start, end)
        for a, b in gaps:
            await self.rest_range(a, b)
        low, high = await self.worker.io(self.worker.store.bounds, self.market.key)
        await self.worker.status(self.market.key, state="live", error=None,
                                 available_start=low, available_end=high,
                                 limited_history=start > self.config["start"] or low is None or low > self.config["start"])

    async def mirror(self, start: int, end: int):
        found = False
        while start < end:
            rows = await self.worker.io(self.worker.store.read_primary, self.worker.primary_path, self.market, start, end)
            if not rows:
                break
            found = True
            await self.worker.write(self.market, rows, "session")
            start = int(rows[-1][0]) + 60
        return start if found else None

    async def history(self):
        prefix_ready = False
        while True:
            try:
                if self.config["reuse_primary"]:
                    # Read the existing 1m session's store, without another download/WS.
                    # It may contain older history than the session's configured start.
                    end = int(time.time() // 60) * 60
                    cursor = await self.mirror(self.mirror_cursor, end)
                    if cursor is not None:
                        self.mirror_cursor = max(self.config["start"], cursor - 600)
                    if not prefix_ready and self.config["start"] < self.config["primary_start"]:
                        await self.bootstrap(self.config["primary_start"])
                    prefix_ready = True
                    await self.worker.status(self.market.key, state="shared_1m_session",
                                             requested_start=self.config["start"])
                    await asyncio.sleep(60)
                else:
                    await self.bootstrap(use_archives=not prefix_ready)
                    prefix_ready = True
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.error(exc)
                await asyncio.sleep(60)

    async def refresh(self):
        while True:
            now = time.time()
            await asyncio.sleep((self.market.refresh_offset - now % 60) % 60)
            try:
                end = int(time.time() // 60) * 60
                if self.config["reuse_primary"]:
                    await self.mirror(max(self.config["start"], end - 600), end)
                else:
                    # Catch up beyond ten bars after outages, without an endless trade buffer.
                    start = min(end - 600, self.last_refresh - 60) if self.last_refresh else end - 600
                    start = max(self.config["start"], start)
                    if self.market.exchange == "hyperliquid":
                        start = max(start, end - 4999 * 60)
                    await self.rest_range(start, end)
                self.last_refresh = end
                await self.worker.status(self.market.key, last_refresh=end, error=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.error(exc)
            await asyncio.sleep(1)

    async def flush(self):
        while True:
            await asyncio.sleep(1)
            await wait_safe(0.5)
            rows = self.accumulator.take_dirty()
            try:
                await self.worker.write(self.market, rows, "trades")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The next REST refresh replaces any lost provisional write.
                await self.error(exc)


async def run(args):
    if hasattr(os, "nice"):
        try:
            os.nice(5)
        except OSError:
            pass
    worker = Worker(Path(args.db), Path(args.primary_db))
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    reader = asyncio.StreamReader(limit=8 * 1024 * 1024)
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)

    async def commands():
        while line := await reader.readline():
            message = json.loads(line)
            if message["type"] == "configure":
                await worker.configure(message["markets"])
            elif message["type"] == "trades":
                collector = worker.markets.get(message["market"])
                if collector:
                    collector.receive(message)
        stopped.set()

    async def parent_watch():
        while not stopped.is_set():
            if os.getppid() != args.parent_pid:
                stopped.set()
                return
            await asyncio.sleep(1)

    command_task = asyncio.create_task(commands())

    def commands_finished(task):
        if not task.cancelled() and task.exception() is not None:
            log_error("control", task.exception())
        stopped.set()

    command_task.add_done_callback(commands_finished)
    watch_task = asyncio.create_task(parent_watch())
    publish_task = asyncio.create_task(worker.publish_live())
    publish_task.add_done_callback(commands_finished)
    try:
        await stopped.wait()
    finally:
        command_task.cancel()
        watch_task.cancel()
        publish_task.cancel()
        await asyncio.gather(command_task, watch_task, publish_task, return_exceptions=True)
        transport.close()
        await worker.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--primary-db", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
