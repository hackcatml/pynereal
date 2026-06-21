from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

import ccxt.pro as ccxt

from ohlcv_io import make_ccxt_pro_client


@dataclass
class ExchangeClock:
    exchange_name: str
    sync_interval_sec: float = 30.0
    time_offset_ms: float = 0.0
    next_sync: float = 0.0
    backoff_sec: float = 5.0
    ref_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ex: object | None = None

    async def now_ms(self) -> float:
        mono = time.monotonic()
        if mono < self.next_sync:
            return time.time() * 1000 + self.time_offset_ms

        async with self.lock:
            mono = time.monotonic()
            if mono < self.next_sync:
                return time.time() * 1000 + self.time_offset_ms

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
                self.next_sync = mono + delay + jitter
                self.backoff_sec = min(delay * 2.0, 60.0)
                print(
                    f"[exchange_clock] {self.exchange_name} fetch_time error: "
                    f"{type(e).__name__}: {e}; using cached/local clock, "
                    f"retrying in {delay:g}s"
                )
                return time.time() * 1000 + self.time_offset_ms

            self.time_offset_ms = server_ms - time.time() * 1000
            jitter = random.uniform(0.0, min(2.0, self.sync_interval_sec * 0.1))
            self.next_sync = mono + self.sync_interval_sec + jitter
            self.backoff_sec = 5.0
            return time.time() * 1000 + self.time_offset_ms

    async def close(self) -> None:
        if self.ex is None:
            return
        try:
            await self.ex.close()
        except Exception:
            pass
        finally:
            self.ex = None


_CLOCKS: dict[str, ExchangeClock] = {}


def retain_exchange_clock(exchange_name: str, sync_interval_sec: float = 30.0) -> ExchangeClock:
    key = exchange_name.lower()
    clock = _CLOCKS.get(key)
    if clock is None:
        clock = ExchangeClock(exchange_name=exchange_name, sync_interval_sec=sync_interval_sec)
        _CLOCKS[key] = clock
    else:
        clock.sync_interval_sec = sync_interval_sec
    clock.ref_count += 1
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
