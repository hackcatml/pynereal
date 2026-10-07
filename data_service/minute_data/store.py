from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

from .core import valid_bar


class MinuteStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS candles (
                    market TEXT NOT NULL, ts INTEGER NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    source TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (market, ts)
                );
                CREATE TABLE IF NOT EXISTS coverage (
                    market TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
                    checked_at REAL NOT NULL,
                    PRIMARY KEY (market, start, end)
                );
                CREATE TABLE IF NOT EXISTS status (
                    market TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def write(self, market: str, rows: list, source: str, missing_only: bool = False) -> int:
        accepted = [bar for row in rows if (bar := valid_bar(row)) is not None]
        now = time.time()
        with self.connect() as db:
            db.executemany("""
                INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, ts) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume,
                    source=excluded.source, updated_at=excluded.updated_at
                WHERE (excluded.source NOT IN ('trades', 'archive_no_trade') AND NOT ?)
                   OR candles.source = 'trades'
                   OR (candles.source = 'archive_no_trade'
                       AND excluded.source NOT IN ('trades', 'archive_no_trade'))
            """, [(market, *row, source, now, missing_only) for row in accepted])
        return len(accepted)

    def mark_checked(self, market: str, start: int, end: int) -> None:
        # Empty successful REST windows are coverage, not fabricated zero-volume bars.
        with self.connect() as db:
            db.execute("DELETE FROM coverage WHERE market=? AND checked_at<=?", (market, time.time()-3600))
            overlaps = db.execute("SELECT start,end FROM coverage WHERE market=? AND end>=? AND start<=?",
                                  (market, start, end)).fetchall()
            if overlaps:
                start = min(start, min(r[0] for r in overlaps))
                end = max(end, max(r[1] for r in overlaps))
                db.execute("DELETE FROM coverage WHERE market=? AND end>=? AND start<=?", (market, start, end))
            db.execute("INSERT OR REPLACE INTO coverage VALUES (?,?,?,?)", (market, start, end, time.time()))

    def gaps(self, market: str, start: int, end: int) -> list[tuple[int, int]]:
        with self.connect() as db:
            spans = db.execute("""
                SELECT ts,ts+60 FROM candles WHERE market=? AND ts>=? AND ts<? AND source!='trades'
                UNION ALL SELECT start,end FROM coverage WHERE market=? AND end>? AND start<? AND checked_at>?
                ORDER BY 1
            """, (market, start, end, market, start, end, time.time()-3600)).fetchall()
        result, cursor = [], start
        for a, b in spans:
            a, b = max(a, start), min(b, end)
            if a > cursor:
                result.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < end:
            result.append((cursor, end))
        return result

    def read(self, market: str, start: int, end: int) -> list:
        with self.connect() as db:
            return db.execute("SELECT ts,open,high,low,close,volume,source FROM candles WHERE market=? AND ts>=? AND ts<? ORDER BY ts",
                              (market, start, end)).fetchall()

    def bounds(self, market: str):
        with self.connect() as db:
            return db.execute("SELECT MIN(ts), MAX(ts) FROM candles WHERE market=? AND source!='trades'", (market,)).fetchone()

    def set_status(self, market: str, **values) -> None:
        with self.connect() as db:
            old = db.execute("SELECT value FROM status WHERE market=?", (market,)).fetchone()
            value = json.loads(old[0]) if old else {}
            value.update(values, updated_at=time.time())
            db.execute("INSERT OR REPLACE INTO status VALUES (?,?)", (market, json.dumps(value)))

    def read_primary(self, path: Path, market, start: int, end: int, limit: int = 1000) -> list:
        if not path.exists():
            return []
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            return db.execute("""
                SELECT ts,open,high,low,close,volume FROM bars
                WHERE provider='ccxt' AND exchange=? AND symbol=? AND timeframe='1m' AND ts>=? AND ts<?
                ORDER BY ts LIMIT ?
            """, (market.exchange, market.symbol, start, end, limit)).fetchall()
        finally:
            db.close()
