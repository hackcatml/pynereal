from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime


SUPPORTED_EXCHANGES = {"binance", "bitget", "okx", "bybit", "hyperliquid"}
LIVE_PREFIX = "minute-chart:"


@dataclass(frozen=True)
class Market:
    exchange: str
    symbol: str
    market_type: str = ""

    def __post_init__(self):
        symbol = self.symbol.upper()
        kind = self.market_type
        if not kind:
            if ":" not in symbol:
                kind = "spot"
            else:
                kind = "inverse" if symbol.rsplit(":", 1)[1] == symbol.split("/")[0] else "linear"
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "exchange", self.exchange.lower())
        object.__setattr__(self, "market_type", kind)

    @property
    def key(self) -> str:
        return "|".join((self.exchange, self.market_type, self.symbol))

    @property
    def refresh_offset(self) -> float:
        return 15 + int(hashlib.sha256(self.key.encode()).hexdigest()[:8], 16) % 2000 / 100


def valid_bar(row) -> list | None:
    try:
        ts = int(row[0])
        values = [float(v) for v in row[1:6]]
        if len(values) != 5 or ts <= 0 or ts % 60:
            return None
        o, h, low, c, volume = values
        if not all(math.isfinite(v) for v in values):
            return None
        if min(o, h, low, c) <= 0 or volume < 0 or h < max(o, low, c) or low > min(o, c):
            return None
        return [ts, *values]
    except (ValueError, TypeError, IndexError, OverflowError):
        return None


class MinuteAccumulator:
    """Bounded provisional candles, independent of primary trade buffers."""

    def __init__(self):
        self.bars: dict[int, list] = {}
        self.times: dict[int, list[float]] = {}
        self.seen: OrderedDict[tuple, None] = OrderedDict()
        self.dirty: set[int] = set()

    def add(self, trades: list, now: float, factor: float = 1.0, inverse: bool = False) -> None:
        cutoff = int(now // 60) * 60 - 10 * 60
        for row in trades:
            try:
                trade_id, timestamp, price, amount = row
                timestamp, price, amount = float(timestamp), float(price), float(amount)
                if not all(math.isfinite(v) for v in (timestamp, price, amount)) or price <= 0 or amount < 0:
                    continue
                ts = int(timestamp / 1000) // 60 * 60
                if ts < cutoff or ts > now + 60:
                    continue
                key = ("id", trade_id) if trade_id else ("values", timestamp, price, amount)
                if key in self.seen:
                    continue
                self.seen[key] = None
                if len(self.seen) > 20000:
                    self.seen.popitem(last=False)
                volume = amount * factor / price if inverse else amount * factor
                bar = self.bars.get(ts)
                if bar is None:
                    self.bars[ts] = [ts, price, price, price, price, volume]
                    self.times[ts] = [timestamp, timestamp]
                else:
                    first, last = self.times[ts]
                    if timestamp < first:
                        bar[1], self.times[ts][0] = price, timestamp
                    if timestamp >= last:
                        bar[4], self.times[ts][1] = price, timestamp
                    bar[2], bar[3] = max(bar[2], price), min(bar[3], price)
                    bar[5] += volume
                self.dirty.add(ts)
            except (ValueError, TypeError, OverflowError):
                continue
        for ts in list(self.bars):
            if ts < cutoff:
                self.bars.pop(ts)
                self.times.pop(ts)
                self.dirty.discard(ts)

    def take_dirty(self) -> list[list]:
        result = [list(self.bars[ts]) for ts in sorted(self.dirty)]
        self.dirty.clear()
        return result


def log_error(market: str, exc: BaseException) -> None:
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}][minute-data] {market}: {type(exc).__name__}: {exc}", flush=True)
