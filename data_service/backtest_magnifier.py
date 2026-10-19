"""Read shared minute candles only inside the isolated backtest worker."""
import sqlite3
from pathlib import Path

from pynecore.core.bar_magnifier import fixed_seconds
from pynecore.types.ohlcv import OHLCV

if __package__:
    from .minute_data.core import Market
else:
    from minute_data.core import Market


def attach_shared_minutes(runner, data_root: Path, data_info: dict, start: int, end: int):
    exchange = str(data_info.get('exchange') or '').lower()
    symbol = str(data_info.get('symbol') or '').upper()
    if not exchange or '/' not in symbol or exchange != runner.syminfo.prefix.lower():
        raise ValueError('Bar Magnifier requires a known exchange and symbol for the OHLCV data')
    market = Market(exchange, symbol)
    shared = data_root / 'cache' / 'minute_candles.sqlite'
    primary = data_root / 'cache' / 'ohlcv_cache.sqlite'
    if not shared.is_file() and not primary.is_file():
        raise ValueError('Bar Magnifier requires collected 1-minute data; no minute database found')
    if fixed_seconds(runner.syminfo.period) <= 60:
        raise ValueError('Collected 1-minute data requires a chart timeframe above 1 minute')

    def rows(path, query, params):
        if not path.is_file():
            return
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            # One ordered query establishes a consistent read snapshot for this source.
            for row in db.execute(query, params):
                yield row
        finally:
            db.close()

    def candles():
        shared_rows = rows(shared, '''
            SELECT ts,open,high,low,close,volume FROM candles
            WHERE market=? AND ts>=? AND ts<? AND source!='trades' ORDER BY ts
        ''', (market.key, start, end))
        primary_rows = rows(primary, '''
            SELECT ts,open,high,low,close,volume FROM bars
            WHERE provider='ccxt' AND exchange=? AND symbol=? AND timeframe='1m'
            AND ts>=? AND ts<? ORDER BY ts
        ''', (exchange, symbol, start, end))
        try:
            a, b = next(shared_rows, None), next(primary_rows, None)
            while a is not None or b is not None:
                if a is not None and (b is None or a[0] <= b[0]):
                    selected = a
                    if b is not None and a[0] == b[0]:
                        b = next(primary_rows, None)
                    a = next(shared_rows, None)
                else:
                    selected = b
                    b = next(primary_rows, None)
                yield OHLCV(*selected)
        finally:
            shared_rows.close()
            primary_rows.close()

    runner.set_bar_magnifier(candles(), source_seconds=60, source=f'shared-minute:{market.key}')
