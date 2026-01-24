from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from pynecore.core.ohlcv_file import OHLCVReader, OHLCVWriter
from pynecore.types.ohlcv import OHLCV


def init_cache(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bars (
                provider TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (provider, exchange, symbol, timeframe, ts)
            )
            """
        )


def cache_has_data(
    db_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM bars
            WHERE provider = ? AND exchange = ? AND symbol = ? AND timeframe = ?
            LIMIT 1
            """,
            (provider, exchange, symbol, timeframe),
        ).fetchone()
        return row is not None


def get_last_ts(
    db_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> int | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT MAX(ts) FROM bars
            WHERE provider = ? AND exchange = ? AND symbol = ? AND timeframe = ?
            """,
            (provider, exchange, symbol, timeframe),
        ).fetchone()
        return row[0] if row and row[0] is not None else None


def get_min_ts(
    db_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> int | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT MIN(ts) FROM bars
            WHERE provider = ? AND exchange = ? AND symbol = ? AND timeframe = ?
            """,
            (provider, exchange, symbol, timeframe),
        ).fetchone()
        return row[0] if row and row[0] is not None else None


def upsert_bars(
    db_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    bars: Iterable[Sequence[float]],
) -> None:
    rows = [
        (
            provider,
            exchange,
            symbol,
            timeframe,
            int(bar[0]),
            float(bar[1]),
            float(bar[2]),
            float(bar[3]),
            float(bar[4]),
            float(bar[5]),
        )
        for bar in bars
    ]
    if not rows:
        return
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO bars (provider, exchange, symbol, timeframe, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, exchange, symbol, timeframe, ts)
            DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            rows,
        )


def import_from_ohlcv(
    db_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    ohlcv_path: Path,
    batch_size: int = 2000,
) -> None:
    if not ohlcv_path.exists():
        return
    with OHLCVReader(ohlcv_path) as reader:
        size = reader.size
        batch = []
        for idx in range(size):
            bar = reader.read(idx)
            batch.append(
                (
                    bar.timestamp,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                )
            )
            if len(batch) >= batch_size:
                upsert_bars(db_path, provider, exchange, symbol, timeframe, batch)
                batch.clear()
        if batch:
            upsert_bars(db_path, provider, exchange, symbol, timeframe, batch)
        reader.close()


def export_to_ohlcv(
    db_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    ohlcv_path: Path,
) -> None:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts, open, high, low, close, volume
            FROM bars
            WHERE provider = ? AND exchange = ? AND symbol = ? AND timeframe = ?
            ORDER BY ts
            """,
            (provider, exchange, symbol, timeframe),
        )
        with OHLCVWriter(ohlcv_path, truncate=True) as writer:
            for ts, open_p, high, low, close, volume in rows:
                writer.write(
                    OHLCV(
                        timestamp=int(ts),
                        open=float(open_p),
                        high=float(high),
                        low=float(low),
                        close=float(close),
                        volume=float(volume),
                    )
                )
            writer.close()


def export_to_ohlcv_since(
    db_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    ohlcv_path: Path,
    start_ts: int,
) -> None:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts, open, high, low, close, volume
            FROM bars
            WHERE provider = ? AND exchange = ? AND symbol = ? AND timeframe = ? AND ts >= ?
            ORDER BY ts
            """,
            (provider, exchange, symbol, timeframe, start_ts),
        )
        with OHLCVWriter(ohlcv_path, truncate=True) as writer:
            for ts, open_p, high, low, close, volume in rows:
                writer.write(
                    OHLCV(
                        timestamp=int(ts),
                        open=float(open_p),
                        high=float(high),
                        low=float(low),
                        close=float(close),
                        volume=float(volume),
                    )
                )
            writer.close()
