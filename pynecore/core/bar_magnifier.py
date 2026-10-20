"""Ordered lower-timeframe candles for historical broker fills only."""
from collections import Counter
from hashlib import sha256
from math import isclose, isfinite
from struct import pack

from pynecore.lib.timeframe import in_seconds


def fixed_seconds(period: str) -> int:
    if str(period).upper().endswith('M'):
        raise ValueError('Bar Magnifier does not support calendar-month candles')
    return int(in_seconds(str(period)))


class BarMagnifier:
    """Consume one ordered stream without changing the strategy's chart candles.

    A partial or inconsistent window is never used for fills. Such windows use
    the ordinary chart-bar emulator and are accounted for in the final report.
    """

    def __init__(self, candles, *, chart_seconds: int, source_seconds: int, mintick: float,
                 source: str = ''):
        if not 0 < source_seconds < chart_seconds or chart_seconds % source_seconds:
            raise ValueError('Bar Magnifier requires a smaller, evenly dividing timeframe')
        self.chart_seconds = chart_seconds
        self.source_seconds = source_seconds
        self.mintick = mintick
        self.source = source
        self._candles = iter(candles)
        self._next = None
        self._last = None
        self._parent = None
        self._digest = sha256()
        self.counts = Counter()
        self.examples = []
        try:
            self._advance()
        except BaseException:
            self.close()
            raise

    def _advance(self):
        self._next = next(self._candles, None)
        if self._next is None:
            return
        bar = self._next
        if (not isfinite(bar.timestamp) or int(bar.timestamp) != bar.timestamp or
                (self._last is not None and bar.timestamp <= self._last)):
            raise ValueError('Bar Magnifier timestamps must be finite, unique, and increasing seconds')
        self._last = bar.timestamp

    def _fallback(self, candle, reason):
        self.counts[reason] += 1
        if len(self.examples) < 10:
            self.examples.append({'timestamp': candle.timestamp, 'reason': reason})
        return None

    def window(self, candle):
        start = candle.timestamp
        end = start + self.chart_seconds
        if self._parent is not None and start < self._parent + self.chart_seconds:
            raise ValueError('Bar Magnifier chart candles must not overlap')
        self._parent = start
        self.counts['chart_bars'] += 1
        while self._next is not None and self._next.timestamp < start:
            self._advance()
        bars = []
        while self._next is not None and self._next.timestamp < end:
            bars.append(self._next)
            self._advance()
        if (len(bars) != self.chart_seconds // self.source_seconds or
                any(b.timestamp != start + i * self.source_seconds for i, b in enumerate(bars))):
            return self._fallback(candle, 'missing_or_partial')
        for bar in bars:
            values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if (not all(isfinite(v) for v in values) or bar.volume < 0 or
                    bar.high < max(bar.open, bar.close, bar.low) or
                    bar.low > min(bar.open, bar.close)):
                return self._fallback(candle, 'invalid_ohlcv')
        actual = (bars[0].open, max(b.high for b in bars), min(b.low for b in bars), bars[-1].close)
        expected = (candle.open, candle.high, candle.low, candle.close)
        if not all(isclose(a, b, rel_tol=1e-10, abs_tol=self.mintick * 1e-4)
                   for a, b in zip(actual, expected)):
            return self._fallback(candle, 'ohlc_mismatch')
        for bar in bars:
            self._digest.update(pack('<q5d', int(bar.timestamp), bar.open, bar.high,
                                     bar.low, bar.close, bar.volume))
        self.counts['magnified_bars'] += 1
        self.counts['sub_bars'] += len(bars)
        return bars

    def report(self):
        return {'source': self.source, 'source_seconds': self.source_seconds,
                'chart_seconds': self.chart_seconds, **dict(self.counts),
                'magnified_bars': self.counts['magnified_bars'],
                'fallback_bars': self.counts['chart_bars'] - self.counts['magnified_bars'],
                'used_data_sha256': self._digest.hexdigest(), 'examples': list(self.examples)}

    def close(self):
        close = getattr(self._candles, 'close', None)
        if close is not None:
            close()


def attach_file(runner, path, start: int, end: int):
    """Attach a local OHLCV source, retaining zero-volume but not missing candles."""
    from pathlib import Path
    from .ohlcv_file import OHLCVReader
    from .syminfo import SymInfo

    path = Path(path)
    info = SymInfo.load_toml(path.with_suffix('.toml'))
    primary = runner.syminfo
    if any(str(getattr(info, key)).upper() != str(getattr(primary, key)).upper()
           for key in ('prefix', 'ticker', 'currency', 'basecurrency')):
        raise ValueError('Bar Magnifier source must match the chart exchange and symbol')
    seconds = fixed_seconds(info.period)

    def candles():
        with OHLCVReader(path) as reader:
            if reader.interval != seconds:
                raise ValueError('Bar Magnifier file interval does not match its symbol metadata')
            yield from reader.read_from(start, end - 1, skip_zero_volume=False)

    runner.set_bar_magnifier(candles(), source_seconds=seconds, source=str(path))
