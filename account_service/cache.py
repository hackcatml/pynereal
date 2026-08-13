from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_BUSY_TIMEOUT_SECONDS = 30.0
_BUSY_TIMEOUT_MS = int(_BUSY_TIMEOUT_SECONDS * 1000)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _position_key(position: dict[str, Any]) -> str:
    return _json([
        str(position.get("market_scope") or ""),
        str(position.get("dex") or ""),
        str(position.get("symbol") or ""),
        str(position.get("side") or ""),
    ])


def _timestamp(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            pass
    return fallback


class AccountCache:
    """Single-writer storage owned by the account-service process."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._connection: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        self._connection = connection
        self._create_schema(connection)
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS current_positions (
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                position_key TEXT NOT NULL,
                market_scope TEXT NOT NULL DEFAULT '',
                dex TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (account, position_key)
            );

            CREATE TABLE IF NOT EXISTS position_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                position_key TEXT NOT NULL,
                market_scope TEXT NOT NULL DEFAULT '',
                dex TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE (account, position_key, opened_at, closed_at)
            );

            CREATE TABLE IF NOT EXISTS orders (
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                order_id TEXT NOT NULL,
                client_order_id TEXT,
                market_scope TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL DEFAULT '',
                order_type TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (
                    account, exchange, market_scope, symbol, order_id
                )
            );

            CREATE TABLE IF NOT EXISTS fills (
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                order_id TEXT,
                market_scope TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL DEFAULT '',
                occurred_at TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (
                    account, exchange, market_scope, symbol, trade_id
                )
            );

            CREATE TABLE IF NOT EXISTS pnl_events (
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '',
                occurred_at TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (account, exchange, event_id, event_type)
            );

            CREATE TABLE IF NOT EXISTS account_sync_status (
                account TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                stream_status TEXT NOT NULL,
                last_snapshot_at TEXT,
                last_success_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_cursors (
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                stream TEXT NOT NULL,
                market_scope TEXT NOT NULL DEFAULT '',
                cursor TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account, exchange, stream, market_scope)
            );

            CREATE INDEX IF NOT EXISTS idx_current_positions_symbol
                ON current_positions (exchange, symbol);
            CREATE INDEX IF NOT EXISTS idx_position_history_closed
                ON position_history (closed_at DESC, account);
            CREATE INDEX IF NOT EXISTS idx_orders_updated
                ON orders (updated_at DESC, account);
            CREATE INDEX IF NOT EXISTS idx_fills_occurred
                ON fills (occurred_at DESC, account);
            CREATE INDEX IF NOT EXISTS idx_pnl_events_occurred
                ON pnl_events (occurred_at DESC, account);
            """
        )
        connection.commit()

    def apply_positions_snapshot(self, payload: dict[str, Any]) -> None:
        connection = self._connect()
        observed_at = str(payload.get("collected_at") or "")
        if not observed_at:
            observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        source = str(payload.get("source") or "exchange.positions")
        results = payload.get("results")
        if not isinstance(results, list):
            return

        connection.execute("BEGIN IMMEDIATE")
        try:
            for result in results:
                if not isinstance(result, dict):
                    continue
                account = str(result.get("account") or "").strip()
                exchange = str(result.get("exchange") or "").strip()
                if not account or not exchange:
                    continue
                status = str(result.get("status") or "error")
                stream_status = str(result.get("stream_status") or status)
                error = result.get("error")
                error_message = None
                if isinstance(error, dict) and error.get("message"):
                    error_message = str(error["message"])[:500]
                connection.execute(
                    """
                    INSERT INTO account_sync_status (
                        account, exchange, stream_status, last_snapshot_at,
                        last_success_at, last_error, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account) DO UPDATE SET
                        exchange = excluded.exchange,
                        stream_status = excluded.stream_status,
                        last_snapshot_at = excluded.last_snapshot_at,
                        last_success_at = CASE
                            WHEN excluded.last_success_at IS NOT NULL
                            THEN excluded.last_success_at
                            ELSE account_sync_status.last_success_at
                        END,
                        last_error = excluded.last_error,
                        updated_at = excluded.updated_at
                    """,
                    (
                        account,
                        exchange,
                        stream_status,
                        observed_at,
                        observed_at if status == "ok" else None,
                        error_message,
                        observed_at,
                    ),
                )
                if status != "ok":
                    continue

                existing = {
                    str(row["position_key"]): row
                    for row in connection.execute(
                        "SELECT * FROM current_positions WHERE account = ?",
                        (account,),
                    )
                }
                positions = result.get("positions")
                positions = positions if isinstance(positions, list) else []
                active_keys: set[str] = set()
                for position in positions:
                    if not isinstance(position, dict):
                        continue
                    key = _position_key(position)
                    active_keys.add(key)
                    symbol = str(position.get("symbol") or "")
                    side = str(position.get("side") or "")
                    market_scope = str(position.get("market_scope") or "")
                    dex = str(position.get("dex") or "")
                    opened_at = _timestamp(
                        position.get("datetime") or position.get("timestamp"),
                        observed_at,
                    )
                    connection.execute(
                        """
                        INSERT INTO current_positions (
                            account, exchange, position_key, market_scope, dex,
                            symbol, side, opened_at, first_seen_at, last_seen_at,
                            source, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(account, position_key) DO UPDATE SET
                            exchange = excluded.exchange,
                            market_scope = excluded.market_scope,
                            dex = excluded.dex,
                            symbol = excluded.symbol,
                            side = excluded.side,
                            last_seen_at = excluded.last_seen_at,
                            source = excluded.source,
                            payload_json = excluded.payload_json
                        """,
                        (
                            account,
                            exchange,
                            key,
                            market_scope,
                            dex,
                            symbol,
                            side,
                            opened_at,
                            observed_at,
                            observed_at,
                            source,
                            _json(position),
                        ),
                    )

                for key, row in existing.items():
                    if key in active_keys:
                        continue
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO position_history (
                            account, exchange, position_key, market_scope, dex,
                            symbol, side, opened_at, closed_at, source, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'derived', ?)
                        """,
                        (
                            row["account"],
                            row["exchange"],
                            row["position_key"],
                            row["market_scope"],
                            row["dex"],
                            row["symbol"],
                            row["side"],
                            row["opened_at"],
                            observed_at,
                            row["payload_json"],
                        ),
                    )
                    connection.execute(
                        "DELETE FROM current_positions WHERE account = ? AND position_key = ?",
                        (account, key),
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def apply_history_batch(
        self,
        orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
    ) -> None:
        if not orders and not fills:
            return
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for record in orders:
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                account = str(record.get("account") or "").strip()
                exchange = str(record.get("exchange") or "").strip()
                order_id = str(payload.get("id") or "").strip()
                symbol = str(payload.get("symbol") or "")
                market_scope = str(record.get("market_scope") or "")
                if not account or not exchange or not order_id:
                    continue
                created_at = _timestamp(
                    payload.get("datetime") or payload.get("timestamp"),
                    "",
                ) or None
                updated_at = _timestamp(
                    payload.get("updated_timestamp"),
                    created_at or "",
                ) or None
                connection.execute(
                    """
                    INSERT INTO orders (
                        account, exchange, order_id, client_order_id,
                        market_scope, symbol, status, side, order_type,
                        created_at, updated_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        account, exchange, market_scope, symbol, order_id
                    ) DO UPDATE SET
                        client_order_id = excluded.client_order_id,
                        status = excluded.status,
                        side = excluded.side,
                        order_type = excluded.order_type,
                        created_at = COALESCE(orders.created_at, excluded.created_at),
                        updated_at = excluded.updated_at,
                        payload_json = excluded.payload_json
                    WHERE orders.updated_at IS NULL
                        OR excluded.updated_at IS NULL
                        OR excluded.updated_at >= orders.updated_at
                    """,
                    (
                        account,
                        exchange,
                        order_id,
                        str(payload.get("client_order_id") or "") or None,
                        market_scope,
                        symbol,
                        str(payload.get("status") or ""),
                        str(payload.get("side") or ""),
                        str(payload.get("type") or ""),
                        created_at,
                        updated_at,
                        _json(payload),
                    ),
                )

            for record in fills:
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                account = str(record.get("account") or "").strip()
                exchange = str(record.get("exchange") or "").strip()
                trade_id = str(payload.get("id") or "").strip()
                symbol = str(payload.get("symbol") or "")
                market_scope = str(record.get("market_scope") or "")
                if not account or not exchange or not trade_id:
                    continue
                occurred_at = _timestamp(
                    payload.get("datetime") or payload.get("timestamp"),
                    "",
                ) or None
                connection.execute(
                    """
                    INSERT INTO fills (
                        account, exchange, trade_id, order_id, market_scope,
                        symbol, side, occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        account, exchange, market_scope, symbol, trade_id
                    ) DO UPDATE SET
                        order_id = excluded.order_id,
                        side = excluded.side,
                        occurred_at = excluded.occurred_at,
                        payload_json = excluded.payload_json
                    """,
                    (
                        account,
                        exchange,
                        trade_id,
                        str(payload.get("order_id") or "") or None,
                        market_scope,
                        symbol,
                        str(payload.get("side") or ""),
                        occurred_at,
                        _json(payload),
                    ),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def rows(self, table: str) -> list[dict[str, Any]]:
        if table not in {
            "current_positions",
            "position_history",
            "orders",
            "fills",
            "account_sync_status",
        }:
            raise ValueError(f"unsupported account cache table: {table}")
        connection = self._connect()
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
