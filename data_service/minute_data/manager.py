from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta

from .core import LIVE_PREFIX, Market, SUPPORTED_EXCHANGES, log_error, valid_bar


class MinuteDataService:
    """Non-blocking tap of existing feeds; IO and aggregation run in a child."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config: dict[str, dict] = {}
        self.sources: dict[str, str] = {}
        self.pending = deque(maxlen=64)
        self.process = None
        self.task = None
        self.closed = False
        self.executor = None
        self.output_task = None
        self.live = {}
        self.primary_feeds = {}
        self.primary_snapshots = {}

    @staticmethod
    def history_start(spec) -> int:
        try:
            dt = datetime.fromisoformat(spec.history_since) if spec.history_since else datetime.now(UTC) - relativedelta(months=1 if spec.timeframe == "1m" else 2)
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except ValueError:
            dt = datetime.now(UTC) - relativedelta(months=2)
        return int(dt.timestamp()) // 60 * 60

    def configure(self, sessions) -> None:
        groups = {}
        for session in sessions:
            spec = session.spec
            market = Market(spec.exchange, spec.symbol, spec.market_type)
            if spec.provider != "ccxt" or market.exchange not in SUPPORTED_EXCHANGES:
                continue
            groups.setdefault(market, []).append(session)
        config, sources, primary_feeds = {}, {}, {}
        for market, group in groups.items():
            if all(s.spec.timeframe == "1m" for s in group):
                continue
            feeds = {s.feed.spec.id: s for s in group}
            previous = self.config.get(market.key, {}).get("source_id")
            one_minute = [s for s in group if s.spec.timeframe == "1m"]
            source = one_minute[0] if one_minute else feeds.get(previous, group[0])
            starts = [self.history_start(s.spec) for s in group]
            config[market.key] = {
                "exchange": market.exchange, "symbol": market.symbol,
                "market_type": market.market_type, "start": min(starts),
                "source_id": source.feed.spec.id, "reuse_primary": bool(one_minute),
                "primary_start": min(self.history_start(s.spec) for s in one_minute) if one_minute else None,
            }
            if not one_minute:
                sources[source.feed.spec.id] = market.key
            else:
                primary_feeds[market.key] = source.feed
        for key in list(self.live):
            if self.config.get(key) != config.get(key):
                self.live.pop(key, None)
                self.primary_snapshots.pop(key, None)
        self.primary_feeds = primary_feeds
        self.config, self.sources = config, sources
        if config and not self.closed and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self._pump(), name="minute-data")

    def offer(self, feed_id: str, trades: list) -> None:
        key = self.sources.get(feed_id)
        if key and not self.closed and trades:
            # Copy only references; no serialization, disk or network on the feed path.
            self.pending.append((key, feed_id, tuple(trades)))

    def accept_live(self, message):
        key = message["market"]
        config = self.config.get(key)
        if not config or config["reuse_primary"] or config["source_id"] != message["source_id"]:
            return
        self.live[key] = tuple({**dict(zip(("time", "open", "high", "low", "close", "volume"), row)),
                                "source": "live", "updated_at": message["updated_at"]}
                               for raw in message["rows"][-2:] if (row := valid_bar(raw)) is not None)

    def live_rows(self, key):
        now = time.time()
        cutoff = int(now // 60) * 60 - 60
        feed = self.primary_feeds.get(key)
        snapshot = getattr(feed, "latest_chart_bar", None)
        if snapshot is not None and snapshot is not self.primary_snapshots.get(key):
            self.primary_snapshots[key] = snapshot
            bar = {**snapshot["data"], "source": "live", "updated_at": now}
            rows = {row["time"]: row for row in self.live.get(key, ()) if row["time"] >= cutoff}
            rows[bar["time"]] = bar
            self.live[key] = tuple(rows[ts] for ts in sorted(rows)[-2:])
        return tuple(row for row in self.live.get(key, ()) if cutoff <= row["time"] <= cutoff + 60)

    async def _read_output(self, process):
        while line := await process.stdout.readline():
            text = line.decode(errors="replace").rstrip()
            if text.startswith(LIVE_PREFIX):
                if self.process is process:
                    self.accept_live(json.loads(text[len(LIVE_PREFIX):]))
            else:
                print(text, flush=True)

    @staticmethod
    def _encode(config, packets):
        messages = []
        if config is not None:
            messages.append({"type": "configure", "markets": config})
        for key, source, trades in packets:
            compact = [[t.get("id"), t.get("timestamp"), t.get("price"), t.get("amount")]
                       for t in trades if isinstance(t, dict)]
            messages.append({"type": "trades", "market": key, "source_id": source, "rows": compact})
        return b"".join((json.dumps(m, allow_nan=False, separators=(",", ":")) + "\n").encode() for m in messages)

    async def _start(self):
        root = Path(__file__).resolve().parents[2]
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "data_service.minute_data.worker",
            "--db", str(self.data_dir / "cache" / "minute_candles.sqlite"),
            "--primary-db", str(self.data_dir / "cache" / "ohlcv_cache.sqlite"),
            "--parent-pid", str(os.getpid()),
            cwd=str(root), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        self.output_task = asyncio.create_task(self._read_output(self.process), name="minute-chart-live")

    async def _stop(self):
        process = self.process
        if process is None:
            return

        async def reap():
            if process.stdin:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()

        cleanup = asyncio.create_task(reap())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise
        finally:
            if self.process is process and process.returncode is not None:
                self.process = None
                if self.output_task is not None:
                    self.output_task.cancel()
                    await asyncio.gather(self.output_task, return_exceptions=True)
                    self.output_task = None
                self.live.clear()
                self.primary_snapshots.clear()

    async def _pump(self):
        sent = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="minute-data-ipc")
        try:
            while not self.closed:
                if not self.config:
                    await self._stop()
                    self.pending.clear()
                    if not self.config:
                        return
                try:
                    if self.process is None or self.process.returncode is not None:
                        await self._stop()
                        await self._start()
                        sent = None
                    if self.output_task.done():
                        await self.output_task
                        raise RuntimeError("minute worker output closed")
                    current = self.config
                    update = current if sent != current else None
                    packets = [self.pending.popleft() for _ in range(min(len(self.pending), 8))]
                    if update is not None or packets:
                        payload = await asyncio.get_running_loop().run_in_executor(self.executor, self._encode, update, packets)
                        self.process.stdin.write(payload)
                        await asyncio.wait_for(self.process.stdin.drain(), 5)
                        sent = current
                    await asyncio.sleep(0.25)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log_error("worker", exc)
                    await self._stop()
                    sent = None
                    await asyncio.sleep(5)
        finally:
            await self._stop()
            self.executor.shutdown(wait=False, cancel_futures=True)

    async def close(self):
        self.closed = True
        self.sources.clear()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self._stop()
        self.pending.clear()
        self.live.clear()
        self.primary_snapshots.clear()
        self.primary_feeds.clear()
