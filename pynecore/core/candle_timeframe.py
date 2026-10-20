"""UTC exchange-candle boundaries, separate from Pine timeframe semantics."""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class CandleTimeframe:
    count: int
    unit: str
    seconds: int  # Nominal duration only; use shift/start for calendar boundaries.

    @property
    def calendar(self) -> bool:
        return self.unit in ("w", "M")

    def start(self, timestamp: int | float) -> int:
        if self.unit == "M":
            dt = datetime.fromtimestamp(timestamp, UTC)
            month = (dt.year * 12 + dt.month - 1) // self.count * self.count
            year, month = divmod(month, 12)
            return int(datetime(year, month + 1, 1, tzinfo=UTC).timestamp())
        anchor = 4 * 86400 if self.unit == "w" else 0  # Monday, 1970-01-05
        return int((timestamp - anchor) // self.seconds) * self.seconds + anchor

    def shift(self, timestamp: int | float, steps: int = 1) -> int:
        if self.unit != "M":
            return int(timestamp) + steps * self.seconds
        dt = datetime.fromtimestamp(timestamp, UTC)
        month = dt.year * 12 + dt.month - 1 + steps * self.count
        year, month = divmod(month, 12)
        return int(datetime(year, month + 1, 1, tzinfo=UTC).timestamp())

    def start_ms(self, timestamp: int | float) -> int:
        return self.start(timestamp / 1000) * 1000

    def shift_ms(self, timestamp: int | float, steps: int = 1) -> int:
        return self.shift(timestamp / 1000, steps) * 1000

    def missing(self, previous: int, following: int) -> int:
        if self.unit == "M":
            a = datetime.fromtimestamp(previous, UTC)
            b = datetime.fromtimestamp(following, UTC)
            return max(0, ((b.year - a.year) * 12 + b.month - a.month) // self.count - 1)
        return max(0, (following - previous) // self.seconds - 1)


@lru_cache(maxsize=128)
def candle_timeframe(value: str) -> CandleTimeframe:
    """Parse CCXT intervals; uppercase M means months, lowercase m minutes."""
    value = str(value).strip()
    if len(value) < 2 or not value[:-1].isdigit() or int(value[:-1]) <= 0:
        raise ValueError(f"Invalid candle timeframe: {value}")
    unit = value[-1] if value[-1] == "M" else value[-1].lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "M": 30 * 86400}
    if unit not in multipliers:
        raise ValueError(f"Invalid candle timeframe: {value}")
    count = int(value[:-1])
    return CandleTimeframe(count, unit, count * multipliers[unit])
