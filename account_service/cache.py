from __future__ import annotations

import base64
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from account_service.history import (
    bitget_history_item_matches_scope,
    bitget_position_market_scope,
    hyperliquid_market_scope,
    okx_history_market_scope,
    reconstruct_position_history_from_fills,
)


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


def _encode_cursor(values: list[Any]) -> str:
    encoded = base64.urlsafe_b64encode(_json(values).encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _decode_cursor(value: str | None, expected_size: int) -> list[Any] | None:
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}")
        items = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid history cursor") from exc
    if not isinstance(items, list) or len(items) != expected_size:
        raise ValueError("invalid history cursor")
    return items


def _page_limit(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _payload_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


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
        self._repair_bitget_history_scopes(connection)
        self._repair_bitget_position_history(connection)
        self._repair_okx_history_scopes(connection)
        self._repair_hyperliquid_history_scopes(connection)
        self._rebuild_binance_position_history(connection)
        self._rebuild_hyperliquid_position_history(connection)
        self._repair_replaced_derived_position_history(connection)
        self._repair_okx_intermediate_position_history(connection)
        connection.commit()
        return connection

    def _read_connection(self) -> sqlite3.Connection | None:
        if not self.path.exists():
            return None
        connection = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro",
            uri=True,
            timeout=_BUSY_TIMEOUT_SECONDS,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA query_only = ON")
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
                source TEXT NOT NULL DEFAULT 'live',
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
                source TEXT NOT NULL DEFAULT 'live',
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
            CREATE INDEX IF NOT EXISTS idx_position_history_exchange_symbol
                ON position_history (exchange, symbol, closed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_orders_updated
                ON orders (updated_at DESC, account);
            CREATE INDEX IF NOT EXISTS idx_orders_sort
                ON orders (COALESCE(updated_at, created_at, '') DESC);
            CREATE INDEX IF NOT EXISTS idx_orders_exchange_symbol
                ON orders (exchange, symbol, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_fills_occurred
                ON fills (occurred_at DESC, account);
            CREATE INDEX IF NOT EXISTS idx_pnl_events_occurred
                ON pnl_events (occurred_at DESC, account);
            """
        )
        AccountCache._ensure_column(
            connection,
            "orders",
            "source",
            "TEXT NOT NULL DEFAULT 'live'",
        )
        AccountCache._ensure_column(
            connection,
            "fills",
            "source",
            "TEXT NOT NULL DEFAULT 'live'",
        )
        connection.commit()

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _repair_bitget_position_history(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT id, account, symbol, side, opened_at, closed_at,
                   market_scope, payload_json
            FROM position_history
            WHERE exchange = 'bitget' AND source = 'native'
            ORDER BY id
            """
        ).fetchall()
        grouped: dict[tuple[str, str, str, str, str], list[sqlite3.Row]] = {}
        for row in rows:
            key = (
                str(row["account"]),
                str(row["symbol"]),
                str(row["side"]),
                str(row["opened_at"]),
                str(row["closed_at"]),
            )
            grouped.setdefault(key, []).append(row)
        for duplicates in grouped.values():
            if len(duplicates) < 2:
                continue
            payload = _payload_object(duplicates[0]["payload_json"])
            payload.setdefault("symbol", duplicates[0]["symbol"])
            expected_scope = bitget_position_market_scope(payload)
            if expected_scope is None:
                continue
            preferred = next(
                (
                    row
                    for row in duplicates
                    if str(row["market_scope"]) == expected_scope
                ),
                None,
            )
            if preferred is None:
                continue
            connection.executemany(
                "DELETE FROM position_history WHERE id = ?",
                [
                    (int(row["id"]),)
                    for row in duplicates
                    if int(row["id"]) != int(preferred["id"])
                ],
            )

    @staticmethod
    def _repair_bitget_history_scopes(connection: sqlite3.Connection) -> None:
        cursor_keys: list[tuple[str, str, str]] = []
        for table, stream in (("orders", "orders"), ("fills", "fills")):
            rows = connection.execute(
                f"""
                SELECT DISTINCT account
                FROM {table}
                WHERE exchange = 'bitget' AND market_scope = 'spot'
                  AND instr(symbol, ':') > 0
                """
            ).fetchall()
            if not rows:
                continue
            connection.execute(
                f"""
                DELETE FROM {table}
                WHERE exchange = 'bitget' AND market_scope = 'spot'
                  AND instr(symbol, ':') > 0
                """
            )
            cursor_keys.extend(
                (str(row["account"]), stream, "spot") for row in rows
            )
        connection.executemany(
            """
            DELETE FROM sync_cursors
            WHERE account = ? AND exchange = 'bitget'
              AND stream = ? AND market_scope = ?
            """,
            cursor_keys,
        )

    @staticmethod
    def _repair_hyperliquid_history_scopes(
        connection: sqlite3.Connection,
    ) -> None:
        for table, identity_column in (
            ("orders", "order_id"),
            ("fills", "trade_id"),
        ):
            rows = connection.execute(
                f"""
                SELECT rowid AS storage_id, account, symbol,
                       {identity_column} AS identity, market_scope, source
                FROM {table}
                WHERE exchange = 'hyperliquid'
                ORDER BY rowid
                """
            ).fetchall()
            grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
            for row in rows:
                key = (
                    str(row["account"]),
                    str(row["symbol"]),
                    str(row["identity"]),
                )
                grouped.setdefault(key, []).append(row)

            for duplicates in grouped.values():
                canonical_scope = hyperliquid_market_scope(
                    str(duplicates[0]["symbol"]),
                )
                preferred = min(
                    duplicates,
                    key=lambda row: (
                        str(row["source"]) != "live",
                        str(row["market_scope"]) != canonical_scope,
                        -int(row["storage_id"]),
                    ),
                )
                connection.executemany(
                    f"DELETE FROM {table} WHERE rowid = ?",
                    [
                        (int(row["storage_id"]),)
                        for row in duplicates
                        if int(row["storage_id"])
                        != int(preferred["storage_id"])
                    ],
                )
                if str(preferred["market_scope"]) != canonical_scope:
                    connection.execute(
                        f"UPDATE {table} SET market_scope = ? WHERE rowid = ?",
                        (canonical_scope, int(preferred["storage_id"])),
                    )

    @staticmethod
    def _repair_okx_history_scopes(connection: sqlite3.Connection) -> None:
        for table, identity_column in (
            ("orders", "order_id"),
            ("fills", "trade_id"),
        ):
            rows = connection.execute(
                f"""
                SELECT rowid AS storage_id, account, symbol,
                       {identity_column} AS identity, market_scope, source
                FROM {table}
                WHERE exchange = 'okx'
                ORDER BY rowid
                """
            ).fetchall()
            grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
            for row in rows:
                key = (
                    str(row["account"]),
                    str(row["symbol"]),
                    str(row["identity"]),
                )
                grouped.setdefault(key, []).append(row)

            for duplicates in grouped.values():
                canonical_scope = okx_history_market_scope({
                    "symbol": duplicates[0]["symbol"],
                })
                if canonical_scope is None:
                    continue
                preferred = min(
                    duplicates,
                    key=lambda row: (
                        str(row["source"]) != "live",
                        str(row["market_scope"]) != canonical_scope,
                        -int(row["storage_id"]),
                    ),
                )
                connection.executemany(
                    f"DELETE FROM {table} WHERE rowid = ?",
                    [
                        (int(row["storage_id"]),)
                        for row in duplicates
                        if int(row["storage_id"])
                        != int(preferred["storage_id"])
                    ],
                )
                if str(preferred["market_scope"]) != canonical_scope:
                    connection.execute(
                        f"UPDATE {table} SET market_scope = ? WHERE rowid = ?",
                        (canonical_scope, int(preferred["storage_id"])),
                    )

    @staticmethod
    def _repair_replaced_derived_position_history(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            DELETE FROM position_history
            WHERE source = 'derived'
              AND EXISTS (
                  SELECT 1
                  FROM position_history AS authoritative
                  WHERE authoritative.id != position_history.id
                    AND authoritative.source IN ('native', 'trades')
                    AND authoritative.account = position_history.account
                    AND authoritative.exchange = position_history.exchange
                    AND authoritative.symbol = position_history.symbol
                    AND (
                        LOWER(authoritative.side) = LOWER(position_history.side)
                        OR LOWER(authoritative.side) IN ('', 'net')
                        OR LOWER(position_history.side) IN ('', 'net')
                    )
                    AND ABS(
                        (julianday(authoritative.opened_at)
                         - julianday(position_history.opened_at)) * 86400.0
                    ) <= 1.0
              )
            """
        )

    @staticmethod
    def _repair_okx_intermediate_position_history(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            DELETE FROM position_history
            WHERE exchange = 'okx' AND source = 'native'
              AND EXISTS (
                  SELECT 1
                  FROM position_history AS final
                  WHERE final.id != position_history.id
                    AND final.exchange = 'okx'
                    AND final.source = 'native'
                    AND final.account = position_history.account
                    AND final.market_scope = position_history.market_scope
                    AND final.symbol = position_history.symbol
                    AND LOWER(final.side) = LOWER(position_history.side)
                    AND final.opened_at = position_history.opened_at
                    AND final.closed_at > position_history.closed_at
              )
            """
        )

    @staticmethod
    def _rebuild_fill_position_history(
        connection: sqlite3.Connection,
        exchange: str,
        groups: set[tuple[str, str, str]] | None = None,
    ) -> None:
        if groups is None:
            groups = {
                (str(row["account"]), str(row["market_scope"]), str(row["symbol"]))
                for row in connection.execute(
                    """
                    SELECT DISTINCT account, market_scope, symbol
                    FROM fills
                    WHERE exchange = ? AND symbol != ''
                    """,
                    (exchange,),
                )
            }
        for account, market_scope, symbol in groups:
            rows = []
            for row in connection.execute(
                """
                SELECT trade_id, occurred_at, payload_json
                FROM fills
                WHERE account = ? AND exchange = ?
                  AND market_scope = ? AND symbol = ?
                ORDER BY occurred_at, trade_id
                """,
                (account, exchange, market_scope, symbol),
            ):
                rows.append({
                    "trade_id": str(row["trade_id"]),
                    "occurred_at": str(row["occurred_at"] or ""),
                    "payload": _payload_object(row["payload_json"]),
                })
            rebuilt = reconstruct_position_history_from_fills(
                account,
                exchange,
                market_scope,
                symbol,
                rows,
                infer_linear_pnl=(
                    exchange == "hyperliquid"
                    or (exchange == "binance" and market_scope == "usd_m")
                ),
            )
            if exchange == "hyperliquid":
                connection.execute(
                    """
                    DELETE FROM position_history
                    WHERE account = ? AND exchange = 'hyperliquid'
                      AND symbol = ? AND source = 'trades'
                    """,
                    (account, symbol),
                )
            else:
                connection.execute(
                    """
                    DELETE FROM position_history
                    WHERE account = ? AND exchange = ?
                      AND market_scope = ? AND symbol = ? AND source = 'trades'
                    """,
                    (account, exchange, market_scope, symbol),
                )
            for record in rebuilt:
                payload = record["payload"]
                connection.execute(
                    """
                    INSERT INTO position_history (
                        account, exchange, position_key, market_scope, dex,
                        symbol, side, opened_at, closed_at, source, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'trades', ?)
                    ON CONFLICT(account, position_key, opened_at, closed_at)
                    DO UPDATE SET payload_json = excluded.payload_json,
                                  source = excluded.source
                    """,
                    (
                        account,
                        exchange,
                        record["position_key"],
                        market_scope,
                        market_scope if exchange == "hyperliquid" else "",
                        symbol,
                        str(payload.get("side") or ""),
                        record["opened_at"],
                        record["closed_at"],
                        _json(payload),
                    ),
                )
                if exchange == "hyperliquid":
                    connection.execute(
                        """
                        DELETE FROM position_history
                        WHERE account = ? AND exchange = 'hyperliquid'
                          AND symbol = ? AND source = 'derived'
                          AND opened_at <= ? AND closed_at >= ?
                        """,
                        (
                            account,
                            symbol,
                            record["closed_at"],
                            record["opened_at"],
                        ),
                    )
                else:
                    connection.execute(
                        """
                        DELETE FROM position_history
                        WHERE account = ? AND exchange = ?
                          AND market_scope = ? AND symbol = ?
                          AND source = 'derived'
                          AND opened_at <= ? AND closed_at >= ?
                        """,
                        (
                            account,
                            exchange,
                            market_scope,
                            symbol,
                            record["closed_at"],
                            record["opened_at"],
                        ),
                    )

    @staticmethod
    def _rebuild_binance_position_history(
        connection: sqlite3.Connection,
        groups: set[tuple[str, str, str]] | None = None,
    ) -> None:
        AccountCache._rebuild_fill_position_history(
            connection,
            "binance",
            groups,
        )

    @staticmethod
    def _rebuild_hyperliquid_position_history(
        connection: sqlite3.Connection,
        groups: set[tuple[str, str, str]] | None = None,
    ) -> None:
        AccountCache._rebuild_fill_position_history(
            connection,
            "hyperliquid",
            groups,
        )

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
                    completed_from_fills = connection.execute(
                        """
                        SELECT 1 FROM position_history
                        WHERE account = ? AND exchange = ?
                          AND (? = 'hyperliquid' OR market_scope = ?)
                          AND symbol = ? AND source = 'trades'
                          AND opened_at <= ? AND closed_at >= ?
                        LIMIT 1
                        """,
                        (
                            row["account"],
                            row["exchange"],
                            row["exchange"],
                            row["market_scope"],
                            row["symbol"],
                            observed_at,
                            row["opened_at"],
                        ),
                    ).fetchone()
                    if completed_from_fills is None:
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
        positions: list[dict[str, Any]] | None = None,
        cursors: list[dict[str, Any]] | None = None,
    ) -> None:
        positions = positions or []
        cursors = cursors or []
        if not orders and not fills and not positions and not cursors:
            return
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        touched_binance_fills: set[tuple[str, str, str]] = set()
        touched_hyperliquid_fills: set[tuple[str, str, str]] = set()
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
                if exchange == "hyperliquid":
                    market_scope = hyperliquid_market_scope(symbol)
                elif exchange == "okx":
                    market_scope = (
                        okx_history_market_scope(payload) or market_scope
                    )
                elif (
                    exchange == "bitget"
                    and not bitget_history_item_matches_scope(
                        payload,
                        market_scope,
                    )
                ):
                    continue
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
                        created_at, updated_at, source, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        account, exchange, market_scope, symbol, order_id
                    ) DO UPDATE SET
                        client_order_id = excluded.client_order_id,
                        status = excluded.status,
                        side = excluded.side,
                        order_type = CASE
                            WHEN excluded.order_type != ''
                            THEN excluded.order_type
                            ELSE orders.order_type
                        END,
                        created_at = COALESCE(orders.created_at, excluded.created_at),
                        updated_at = excluded.updated_at,
                        source = CASE
                            WHEN excluded.source = 'live' THEN 'live'
                            ELSE orders.source
                        END,
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
                        str(record.get("source") or "live"),
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
                if exchange == "hyperliquid":
                    market_scope = hyperliquid_market_scope(symbol)
                elif exchange == "okx":
                    market_scope = (
                        okx_history_market_scope(payload) or market_scope
                    )
                elif (
                    exchange == "bitget"
                    and not bitget_history_item_matches_scope(
                        payload,
                        market_scope,
                    )
                ):
                    continue
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
                        symbol, side, occurred_at, source, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        account, exchange, market_scope, symbol, trade_id
                    ) DO UPDATE SET
                        order_id = excluded.order_id,
                        side = excluded.side,
                        occurred_at = excluded.occurred_at,
                        source = CASE
                            WHEN excluded.source = 'live' THEN 'live'
                            ELSE fills.source
                        END,
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
                        str(record.get("source") or "live"),
                        _json(payload),
                    ),
                )
                if exchange == "binance":
                    touched_binance_fills.add((account, market_scope, symbol))
                elif exchange == "hyperliquid":
                    touched_hyperliquid_fills.add(
                        (account, market_scope, symbol),
                    )

            for record in positions:
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                account = str(record.get("account") or "").strip()
                exchange = str(record.get("exchange") or "").strip()
                position_key = str(record.get("position_key") or "").strip()
                opened_at = str(record.get("opened_at") or "").strip()
                closed_at = str(record.get("closed_at") or "").strip()
                symbol = str(payload.get("symbol") or "")
                side = str(payload.get("side") or "")
                if not all((account, exchange, position_key, opened_at, closed_at, symbol)):
                    continue
                connection.execute(
                    """
                    INSERT INTO position_history (
                        account, exchange, position_key, market_scope, dex,
                        symbol, side, opened_at, closed_at, source, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account, position_key, opened_at, closed_at)
                    DO UPDATE SET
                        exchange = excluded.exchange,
                        market_scope = excluded.market_scope,
                        dex = excluded.dex,
                        symbol = excluded.symbol,
                        side = excluded.side,
                        source = excluded.source,
                        payload_json = excluded.payload_json
                    """,
                    (
                        account,
                        exchange,
                        position_key,
                        str(record.get("market_scope") or ""),
                        str(record.get("dex") or ""),
                        symbol,
                        side,
                        opened_at,
                        closed_at,
                        str(record.get("source") or "native"),
                        _json(payload),
                    ),
                )

            if positions:
                self._repair_replaced_derived_position_history(connection)
                self._repair_okx_intermediate_position_history(connection)

            for record in cursors:
                account = str(record.get("account") or "").strip()
                exchange = str(record.get("exchange") or "").strip()
                stream = str(record.get("stream") or "").strip()
                market_scope = str(record.get("market_scope") or "")
                updated_at = str(record.get("updated_at") or "").strip()
                if not all((account, exchange, stream, updated_at)):
                    continue
                connection.execute(
                    """
                    INSERT INTO sync_cursors (
                        account, exchange, stream, market_scope, cursor, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account, exchange, stream, market_scope)
                    DO UPDATE SET
                        cursor = excluded.cursor,
                        updated_at = excluded.updated_at
                    """,
                    (
                        account,
                        exchange,
                        stream,
                        market_scope,
                        str(record.get("cursor") or ""),
                        updated_at,
                    ),
                )
            if touched_binance_fills:
                self._rebuild_binance_position_history(
                    connection,
                    touched_binance_fills,
                )
            if touched_hyperliquid_fills:
                self._rebuild_hyperliquid_position_history(
                    connection,
                    touched_hyperliquid_fills,
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

    def sync_cursors(self) -> dict[tuple[str, str, str, str], str]:
        connection = self._connect()
        return {
            (
                str(row["account"]),
                str(row["exchange"]),
                str(row["stream"]),
                str(row["market_scope"]),
            ): str(row["cursor"] or "")
            for row in connection.execute("SELECT * FROM sync_cursors")
        }

    def known_history_symbols(self) -> dict[str, set[str]]:
        connection = self._connect()
        symbols: dict[str, set[str]] = {}
        for table in ("current_positions", "position_history", "orders", "fills"):
            for row in connection.execute(
                f"SELECT DISTINCT exchange, symbol FROM {table} WHERE symbol != ''"
            ):
                symbols.setdefault(str(row["exchange"]), set()).add(str(row["symbol"]))
        return symbols

    def _history_groups(
        self,
        *,
        table: str,
        timestamp_expression: str,
        exchange: str = "",
    ) -> dict[str, Any]:
        if table not in {"position_history", "orders"}:
            raise ValueError(f"unsupported history table: {table}")

        normalized_exchange = exchange.strip().lower()
        connection = self._read_connection()
        if connection is None:
            return {"results": [], "total": 0}
        try:
            if normalized_exchange:
                rows = connection.execute(
                    f"""
                    SELECT MIN(exchange) AS exchange, MIN(symbol) AS symbol,
                           COUNT(*) AS record_count,
                           COUNT(DISTINCT account) AS account_count,
                           MAX({timestamp_expression}) AS latest_at
                    FROM {table}
                    WHERE LOWER(exchange) = ? AND symbol != ''
                    GROUP BY LOWER(symbol)
                    ORDER BY latest_at DESC, LOWER(symbol)
                    """,
                    (normalized_exchange,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT MIN(exchange) AS exchange, '' AS symbol,
                           COUNT(*) AS record_count,
                           COUNT(DISTINCT account) AS account_count,
                           MAX({timestamp_expression}) AS latest_at
                    FROM {table}
                    WHERE exchange != ''
                    GROUP BY LOWER(exchange)
                    ORDER BY latest_at DESC, LOWER(exchange)
                    """
                ).fetchall()
        finally:
            connection.close()

        results = [dict(row) for row in rows]
        return {
            "results": results,
            "total": sum(int(row["record_count"] or 0) for row in rows),
        }

    def position_history_groups(self, *, exchange: str = "") -> dict[str, Any]:
        return self._history_groups(
            table="position_history",
            timestamp_expression="closed_at",
            exchange=exchange,
        )

    def order_history_groups(self, *, exchange: str = "") -> dict[str, Any]:
        return self._history_groups(
            table="orders",
            timestamp_expression="COALESCE(updated_at, created_at, '')",
            exchange=exchange,
        )

    def history_accounts(
        self,
        *,
        kind: str,
        exchange: str = "",
        symbol: str = "",
    ) -> list[str]:
        table = {
            "order": "orders",
            "position": "position_history",
        }.get(kind)
        if table is None:
            raise ValueError(f"unsupported history kind: {kind}")

        filters: list[str] = []
        params: list[str] = []
        for column, value in (("exchange", exchange), ("symbol", symbol)):
            normalized = value.strip().lower()
            if normalized:
                filters.append(f"LOWER({column}) = ?")
                params.append(normalized)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""

        connection = self._read_connection()
        if connection is None:
            return []
        try:
            rows = connection.execute(
                f"""
                SELECT DISTINCT account
                FROM {table}
                {where}
                ORDER BY LOWER(account)
                """,
                params,
            ).fetchall()
        finally:
            connection.close()
        return [str(row["account"]) for row in rows if row["account"]]

    def position_history_page(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        account: str = "",
        exchange: str = "",
        symbol: str = "",
        exact_market: bool = False,
    ) -> dict[str, Any]:
        page_size = _page_limit(limit)
        decoded_cursor = _decode_cursor(cursor, 2)
        filters: list[str] = []
        filter_params: list[Any] = []
        for column, value in (
            ("account", account),
            ("exchange", exchange),
            ("symbol", symbol),
        ):
            normalized = value.strip().lower()
            if normalized:
                if exact_market and column in {"exchange", "symbol"}:
                    filters.append(f"LOWER({column}) = ?")
                    filter_params.append(normalized)
                else:
                    filters.append(f"LOWER({column}) LIKE ?")
                    filter_params.append(f"%{normalized}%")

        connection = self._read_connection()
        if connection is None:
            return {"results": [], "next_cursor": None, "total": 0}
        try:
            filter_sql = f" WHERE {' AND '.join(filters)}" if filters else ""
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM position_history{filter_sql}",
                filter_params,
            ).fetchone()[0])

            conditions = list(filters)
            params = list(filter_params)
            if decoded_cursor is not None:
                closed_at, row_id = decoded_cursor
                if not isinstance(closed_at, str) or not isinstance(row_id, int):
                    raise ValueError("invalid history cursor")
                conditions.append("(closed_at < ? OR (closed_at = ? AND id < ?))")
                params.extend((closed_at, closed_at, row_id))
            where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = connection.execute(
                f"""
                SELECT id, account, exchange, market_scope, dex, symbol, side,
                       opened_at, closed_at, source, payload_json,
                       EXISTS (
                           SELECT 1
                           FROM current_positions AS active
                           WHERE active.account = position_history.account
                             AND active.exchange = position_history.exchange
                             AND (
                                 active.market_scope = position_history.market_scope
                                 OR active.market_scope = ''
                                 OR position_history.market_scope = ''
                             )
                             AND active.symbol = position_history.symbol
                             AND ABS(
                                 (julianday(active.opened_at)
                                  - julianday(position_history.opened_at)) * 86400.0
                             ) <= 2.0
                       ) AS remains_open
                FROM position_history
                {where_sql}
                ORDER BY closed_at DESC, id DESC
                LIMIT ?
                """,
                [*params, page_size + 1],
            ).fetchall()
        finally:
            connection.close()

        has_more = len(rows) > page_size
        page_rows = rows[:page_size]
        results = []
        for row in page_rows:
            position = _payload_object(row["payload_json"])
            opened_at = (
                None
                if position.get("opened_at_known") is False
                else row["opened_at"]
            )
            results.append({
                "id": int(row["id"]),
                "account": row["account"],
                "exchange": row["exchange"],
                "market_scope": row["market_scope"],
                "dex": row["dex"],
                "symbol": row["symbol"],
                "side": row["side"],
                "opened_at": opened_at,
                "closed_at": row["closed_at"],
                "close_status": (
                    "partially_closed" if row["remains_open"] else "fully_closed"
                ),
                "source": row["source"],
                "position": position,
            })
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor([last["closed_at"], int(last["id"])])
        return {"results": results, "next_cursor": next_cursor, "total": total}

    def order_history_page(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        account: str = "",
        exchange: str = "",
        symbol: str = "",
        status: str = "",
        exact_market: bool = False,
    ) -> dict[str, Any]:
        page_size = _page_limit(limit)
        decoded_cursor = _decode_cursor(cursor, 2)
        filters: list[str] = []
        filter_params: list[Any] = []
        for column, value in (
            ("account", account),
            ("exchange", exchange),
            ("symbol", symbol),
        ):
            normalized = value.strip().lower()
            if normalized:
                if exact_market and column in {"exchange", "symbol"}:
                    filters.append(f"LOWER({column}) = ?")
                    filter_params.append(normalized)
                else:
                    filters.append(f"LOWER({column}) LIKE ?")
                    filter_params.append(f"%{normalized}%")
        normalized_status = status.strip().lower()
        if normalized_status:
            filters.append("LOWER(status) = ?")
            filter_params.append(normalized_status)

        connection = self._read_connection()
        if connection is None:
            return {"results": [], "next_cursor": None, "total": 0}
        sort_expression = "COALESCE(updated_at, created_at, '')"
        try:
            filter_sql = f" WHERE {' AND '.join(filters)}" if filters else ""
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM orders{filter_sql}",
                filter_params,
            ).fetchone()[0])

            conditions = list(filters)
            params = list(filter_params)
            if decoded_cursor is not None:
                sort_at, row_id = decoded_cursor
                if not isinstance(sort_at, str) or not isinstance(row_id, int):
                    raise ValueError("invalid history cursor")
                conditions.append(
                    f"({sort_expression} < ? OR "
                    f"({sort_expression} = ? AND rowid < ?))"
                )
                params.extend((sort_at, sort_at, row_id))
            where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = connection.execute(
                f"""
                SELECT rowid AS row_id, account, exchange, order_id,
                       client_order_id, market_scope, symbol, status, side,
                       order_type, created_at, updated_at, source,
                       {sort_expression} AS sort_at, payload_json
                FROM orders
                {where_sql}
                ORDER BY sort_at DESC, row_id DESC
                LIMIT ?
                """,
                [*params, page_size + 1],
            ).fetchall()
        finally:
            connection.close()

        has_more = len(rows) > page_size
        page_rows = rows[:page_size]
        results = [
            {
                "account": row["account"],
                "exchange": row["exchange"],
                "order_id": row["order_id"],
                "client_order_id": row["client_order_id"],
                "market_scope": row["market_scope"],
                "symbol": row["symbol"],
                "status": row["status"],
                "side": row["side"],
                "order_type": row["order_type"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "source": row["source"],
                "order": _payload_object(row["payload_json"]),
            }
            for row in page_rows
        ]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor([last["sort_at"], int(last["row_id"])])
        return {"results": results, "next_cursor": next_cursor, "total": total}

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
