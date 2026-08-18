from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

import ccxt.pro as ccxt

from ohlcv_io import make_ccxt_pro_client
from log_utils import log_with_time
from schedule_utils import seconds_until_post_bar_task_window


_DEFAULT_SYNC_INTERVAL_SECONDS = 10 * 60


@dataclass
class ExchangeClock:
    exchange_name: str
    sync_interval_sec: float = _DEFAULT_SYNC_INTERVAL_SECONDS
    time_offset_ms: float = 0.0
    backoff_sec: float = 5.0
    ref_count: int = 0
    ex: object | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    sync_task: asyncio.Task[None] | None = field(default=None, repr=False)

    async def now_ms(self) -> float:
        return time.time() * 1000 + self.time_offset_ms

    def start(self) -> None:
        if self.sync_task is not None and not self.sync_task.done():
            return
        self.stop_event.clear()
        self.sync_task = asyncio.create_task(
            self._sync_loop(),
            name=f"exchange-clock-{self.exchange_name.lower()}",
        )

    async def _sync_loop(self) -> None:
        delay = seconds_until_post_bar_task_window()
        while not self.stop_event.is_set():
            if await self._wait_until_stopped(delay):
                return

            started = time.monotonic()
            next_interval = await self._sync_once()
            elapsed = time.monotonic() - started
            if await self._wait_until_stopped(max(0.0, next_interval - elapsed)):
                return
            delay = seconds_until_post_bar_task_window()

    async def _sync_once(self) -> float:
        if self.ex is None:
            self.ex = make_ccxt_pro_client(ccxt, self.exchange_name)

        try:
            server_ms = await self.ex.fetch_time()
            if server_ms is None:
                raise RuntimeError("fetch_time returned None")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            delay = self.backoff_sec
            jitter = random.uniform(0.0, min(1.0, delay * 0.2))
            self.backoff_sec = min(delay * 2.0, 60.0)
            await self._close_exchange()
            log_with_time(
                f"[exchange_clock] {self.exchange_name} fetch_time error: "
                f"{type(e).__name__}: {e}; using cached/local clock, "
                f"retrying in {delay:g}s"
            )
            return delay + jitter

        self.time_offset_ms = server_ms - time.time() * 1000
        self.backoff_sec = 5.0
        return self.sync_interval_sec + random.uniform(
            0.0,
            min(2.0, self.sync_interval_sec * 0.1),
        )

    async def _wait_until_stopped(self, delay: float) -> bool:
        if delay <= 0.0:
            return self.stop_event.is_set()
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    async def close(self) -> None:
        self.stop_event.set()
        task = self.sync_task
        self.sync_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._close_exchange()

    async def _close_exchange(self) -> None:
        if self.ex is None:
            return
        try:
            await self.ex.close()
        except Exception:
            pass
        finally:
            self.ex = None


_CLOCKS: dict[str, ExchangeClock] = {}


def retain_exchange_clock(
    exchange_name: str,
    sync_interval_sec: float = _DEFAULT_SYNC_INTERVAL_SECONDS,
) -> ExchangeClock:
    key = exchange_name.lower()
    clock = _CLOCKS.get(key)
    if clock is None:
        clock = ExchangeClock(exchange_name=exchange_name, sync_interval_sec=sync_interval_sec)
        _CLOCKS[key] = clock
    else:
        clock.sync_interval_sec = sync_interval_sec
    clock.ref_count += 1
    clock.start()
    return clock


async def release_exchange_clock(exchange_name: str) -> None:
    key = exchange_name.lower()
    clock = _CLOCKS.get(key)
    if clock is None:
        return
    clock.ref_count -= 1
    if clock.ref_count > 0:
        return
    _CLOCKS.pop(key, None)
    await clock.close()
