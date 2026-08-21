from __future__ import annotations

import base64
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
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
_POSITION_LIFECYCLE_OPEN_TOLERANCE_SECONDS = 2.0
_POSITION_UPDATE_TOLERANCE_SECONDS = 1.0


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


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _iso_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _epoch_timestamp(value: Any) -> float | None:
    parsed = _iso_timestamp(value)
    if parsed is not None:
        return parsed
    number = _decimal(value)
    if number is None:
        return None
    seconds = float(number)
    if abs(seconds) > 10_000_000_000:
        seconds /= 1000.0
    return seconds


def _position_sides_match(left: Any, right: Any) -> bool:
    left_side = str(left or "").strip().lower()
    right_side = str(right or "").strip().lower()
    return (
        left_side == right_side
        or left_side in {"", "net"}
        or right_side in {"", "net"}
    )


def _position_scopes_match(left: Any, right: Any) -> bool:
    left_scope = str(left or "").strip().lower()
    right_scope = str(right or "").strip().lower()
    return not left_scope or not right_scope or left_scope == right_scope


def _fill_amount_and_cost(payload: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    amount = _decimal(payload.get("amount"))
    if amount is None:
        return None
    cost = _decimal(payload.get("cost"))
    if cost is None:
        price = _decimal(payload.get("price"))
        if price is None:
            return None
        cost = price * amount
    return amount, cost


def _decimal_close(left: Decimal, right: Decimal, relative: Decimal) -> bool:
    tolerance = max(Decimal("0.000000001"), abs(left) * relative)
    return abs(left - right) <= tolerance


def _settlement_currency(symbol: str, payload: dict[str, Any]) -> str:
    breakdown = payload.get("realized_pnl_breakdown")
    if isinstance(breakdown, dict):
        currency = str(breakdown.get("currency") or "").strip().upper()
        if currency:
            return currency
    normalized = symbol.strip().upper()
    if ":" in normalized:
        settlement = normalized.rsplit(":", 1)[1].split("-", 1)[0]
        if settlement:
            return settlement
    if "/" in normalized:
        quote = normalized.split("/", 1)[1].split(":", 1)[0].split("-", 1)[0]
        if quote:
            return quote
    return "UNKNOWN"


def _new_pnl_bucket(
    account: str,
    exchange: str,
    currency: str,
) -> dict[str, Any]:
    return {
        "account": account,
        "exchange": exchange,
        "currency": currency,
        "realized_pnl": Decimal(0),
        "unrealized_pnl": Decimal(0),
        "gross_realized_pnl": Decimal(0),
        "fees": Decimal(0),
        "funding": Decimal(0),
        "borrow_interest": Decimal(0),
        "closed_positions": 0,
        "open_positions": 0,
        "realized_complete": True,
        "unrealized_complete": True,
        "fee_breakdown_complete": True,
        "funding_available": False,
        "borrow_interest_available": False,
    }


def _serialize_pnl_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    realized = bucket["realized_pnl"]
    unrealized = bucket["unrealized_pnl"]
    return {
        "account": bucket["account"],
        "exchange": bucket["exchange"],
        "currency": bucket["currency"],
        "realized_pnl": float(realized),
        "unrealized_pnl": float(unrealized),
        "net_pnl": float(realized + unrealized),
        "gross_realized_pnl": (
            float(bucket["gross_realized_pnl"])
            if bucket["fee_breakdown_complete"]
            else None
        ),
        "fees": (
            float(bucket["fees"])
            if bucket["fee_breakdown_complete"]
            else None
        ),
        "closed_positions": bucket["closed_positions"],
        "open_positions": bucket["open_positions"],
        "complete": (
            bucket["realized_complete"]
            and bucket["unrealized_complete"]
        ),
        "fee_breakdown_complete": bucket["fee_breakdown_complete"],
        "funding": (
            float(bucket["funding"])
            if bucket["funding_available"]
            else None
        ),
        "borrow_interest": (
            float(bucket["borrow_interest"])
            if bucket["borrow_interest_available"]
            else None
        ),
    }


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
        self._repair_hyperliquid_csv_fill_overlaps(connection)
        self._rebuild_binance_position_history(connection)
        self._rebuild_hyperliquid_position_history(connection)
        self._repair_replaced_derived_position_history(connection)
        self._repair_okx_intermediate_position_history(connection)
        self._repair_okx_csv_position_pnl(connection)
        self._repair_bitget_csv_position_pnl(connection)
        self._repair_csv_position_history(connection)
        self._allocate_position_funding(connection)
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

            CREATE TABLE IF NOT EXISTS transfer_history (
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                transfer_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                asset TEXT NOT NULL DEFAULT '',
                amount TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT 'internal',
                status TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'native',
                payload_json TEXT NOT NULL,
                PRIMARY KEY (account, exchange, transfer_id)
            );

            CREATE TABLE IF NOT EXISTS transfer_sync_status (
                account TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                status TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                last_success_at TEXT,
                last_error TEXT,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
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

            CREATE TABLE IF NOT EXISTS csv_imports (
                import_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                original_name TEXT NOT NULL,
                account TEXT NOT NULL,
                exchange TEXT NOT NULL,
                file_type TEXT NOT NULL,
                market_scope TEXT NOT NULL DEFAULT '',
                source_timezone TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                first_occurred_at TEXT,
                last_occurred_at TEXT,
                status TEXT NOT NULL,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                imported_at TEXT NOT NULL,
                UNIQUE (account, exchange, file_type, file_hash)
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
            CREATE INDEX IF NOT EXISTS idx_transfer_history_occurred
                ON transfer_history (account, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_csv_imports_imported
                ON csv_imports (imported_at DESC);
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
    def _repair_hyperliquid_csv_fill_overlaps(
        connection: sqlite3.Connection,
    ) -> set[tuple[str, str, str]]:
        rows = connection.execute(
            """
            SELECT rowid AS storage_id, account, market_scope, symbol,
                   side, occurred_at, source, payload_json
            FROM fills
            WHERE exchange = 'hyperliquid'
            ORDER BY occurred_at, rowid
            """
        ).fetchall()
        csv_groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
        native_rows: list[dict[str, Any]] = []
        for row in rows:
            occurred = _iso_timestamp(row["occurred_at"])
            payload = _payload_object(row["payload_json"])
            totals = _fill_amount_and_cost(payload)
            if occurred is None or totals is None:
                continue
            item = {
                "storage_id": int(row["storage_id"]),
                "account": str(row["account"]),
                "market_scope": str(row["market_scope"]),
                "symbol": str(row["symbol"]),
                "side": str(row["side"]),
                "occurred": occurred,
                "payload": payload,
                "amount": totals[0],
                "cost": totals[1],
            }
            if str(row["source"]).startswith("csv:"):
                key = (
                    item["account"],
                    item["symbol"],
                    item["side"],
                    int(occurred),
                )
                csv_groups.setdefault(key, []).append(item)
            else:
                native_rows.append(item)

        consumed_native: set[int] = set()
        aliases: dict[tuple[str, str], tuple[str, str]] = {}
        deleted_native: list[int] = []
        affected: set[tuple[str, str, str]] = set()
        for (account, csv_symbol, side, occurred_second), csv_rows in csv_groups.items():
            csv_amount = sum(
                (item["amount"] for item in csv_rows),
                Decimal(0),
            )
            csv_cost = sum(
                (item["cost"] for item in csv_rows),
                Decimal(0),
            )
            candidates: dict[str, list[dict[str, Any]]] = {}
            for item in native_rows:
                if (
                    item["storage_id"] in consumed_native
                    or item["account"] != account
                    or item["side"] != side
                    or abs(item["occurred"] - occurred_second) > 2.0
                ):
                    continue
                candidates.setdefault(item["symbol"], []).append(item)

            matches: list[tuple[str, list[dict[str, Any]]]] = []
            for candidate_symbol, candidate_rows in candidates.items():
                native_amount = sum(
                    (item["amount"] for item in candidate_rows),
                    Decimal(0),
                )
                native_cost = sum(
                    (item["cost"] for item in candidate_rows),
                    Decimal(0),
                )
                if (
                    _decimal_close(csv_amount, native_amount, Decimal("0.00000001"))
                    and _decimal_close(csv_cost, native_cost, Decimal("0.000002"))
                ):
                    matches.append((candidate_symbol, candidate_rows))
            if len(matches) != 1:
                continue

            canonical_symbol, matched_rows = matches[0]
            canonical_scope = hyperliquid_market_scope(canonical_symbol)
            aliases[(account, csv_symbol)] = (
                canonical_scope,
                canonical_symbol,
            )
            affected.add((account, canonical_scope, canonical_symbol))
            affected.update(
                (account, item["market_scope"], item["symbol"])
                for item in csv_rows
            )
            for item in matched_rows:
                consumed_native.add(item["storage_id"])
                deleted_native.append(item["storage_id"])

        if deleted_native:
            connection.executemany(
                "DELETE FROM fills WHERE rowid = ?",
                [(storage_id,) for storage_id in deleted_native],
            )

        for (account, source_symbol), (market_scope, symbol) in aliases.items():
            matching = connection.execute(
                """
                SELECT rowid AS storage_id, market_scope, symbol, payload_json
                FROM fills
                WHERE account = ? AND exchange = 'hyperliquid'
                  AND source LIKE 'csv:%' AND symbol = ?
                """,
                (account, source_symbol),
            ).fetchall()
            for row in matching:
                payload = _payload_object(row["payload_json"])
                payload["symbol"] = symbol
                connection.execute(
                    """
                    UPDATE fills
                    SET market_scope = ?, symbol = ?, payload_json = ?
                    WHERE rowid = ?
                    """,
                    (market_scope, symbol, _json(payload), int(row["storage_id"])),
                )
                affected.add((account, str(row["market_scope"]), source_symbol))
                affected.add((account, market_scope, symbol))

        for account, _, symbol in affected:
            connection.execute(
                """
                DELETE FROM position_history
                WHERE account = ? AND exchange = 'hyperliquid'
                  AND symbol = ? AND source = 'trades'
                """,
                (account, symbol),
            )
        return affected

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
        derived_rows = connection.execute(
            """
            SELECT id, account, exchange, market_scope, dex, symbol, side,
                   opened_at, closed_at, payload_json
            FROM position_history
            WHERE source = 'derived'
            ORDER BY id
            """
        ).fetchall()
        for derived in derived_rows:
            candidates = connection.execute(
                """
                SELECT id, source, market_scope, dex, side, closed_at
                FROM position_history
                WHERE id != ? AND account = ? AND exchange = ? AND symbol = ?
                  AND source IN ('native', 'trades')
                  AND ABS(
                      (julianday(opened_at) - julianday(?)) * 86400.0
                  ) <= ?
                ORDER BY closed_at, id
                """,
                (
                    int(derived["id"]),
                    str(derived["account"]),
                    str(derived["exchange"]),
                    str(derived["symbol"]),
                    str(derived["opened_at"]),
                    _POSITION_LIFECYCLE_OPEN_TOLERANCE_SECONDS,
                ),
            ).fetchall()
            candidates = [
                candidate
                for candidate in candidates
                if _position_sides_match(candidate["side"], derived["side"])
                and _position_scopes_match(
                    candidate["market_scope"],
                    derived["market_scope"],
                )
                and _position_scopes_match(candidate["dex"], derived["dex"])
            ]
            if not candidates:
                continue

            # Fill-reconstructed histories represent a complete flat-to-flat
            # lifecycle and remain authoritative over an observed disappearance.
            if any(candidate["source"] == "trades" for candidate in candidates):
                connection.execute(
                    "DELETE FROM position_history WHERE id = ?",
                    (int(derived["id"]),),
                )
                continue

            payload = _payload_object(derived["payload_json"])
            last_active_update = _epoch_timestamp(
                payload.get("last_update_timestamp")
            )
            derived_closed_at = _iso_timestamp(derived["closed_at"])
            intermediate_ids: list[int] = []
            authoritative_native = False
            for candidate in candidates:
                native_closed_at = _iso_timestamp(candidate["closed_at"])
                if native_closed_at is None:
                    authoritative_native = True
                    continue
                if (
                    last_active_update is not None
                    and native_closed_at
                    <= last_active_update + _POSITION_UPDATE_TOLERANCE_SECONDS
                ):
                    intermediate_ids.append(int(candidate["id"]))
                    continue
                if (
                    derived_closed_at is not None
                    and abs(native_closed_at - derived_closed_at)
                    <= _POSITION_LIFECYCLE_OPEN_TOLERANCE_SECONDS
                ):
                    authoritative_native = True
                    continue
                # Without evidence that the native row was already reflected
                # while the position remained open, preserve exchange history.
                authoritative_native = True

            if intermediate_ids:
                connection.executemany(
                    "DELETE FROM position_history WHERE id = ?",
                    [(row_id,) for row_id in intermediate_ids],
                )
            if authoritative_native:
                connection.execute(
                    "DELETE FROM position_history WHERE id = ?",
                    (int(derived["id"]),),
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
    def _repair_csv_position_history(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM position_history
            WHERE source NOT LIKE 'csv:%'
              AND EXISTS (
                  SELECT 1
                  FROM position_history AS csv_row
                  WHERE csv_row.source LIKE 'csv:%'
                    AND csv_row.account = position_history.account
                    AND csv_row.exchange = position_history.exchange
                    AND csv_row.market_scope = position_history.market_scope
                    AND csv_row.symbol = position_history.symbol
                    AND (
                        LOWER(csv_row.side) = LOWER(position_history.side)
                        OR (
                            position_history.exchange = 'okx'
                            AND (
                                LOWER(csv_row.side) IN ('', 'net')
                                OR LOWER(position_history.side) IN ('', 'net')
                            )
                        )
                    )
                    AND ABS(
                        (julianday(csv_row.opened_at)
                         - julianday(position_history.opened_at)) * 86400.0
                    ) <= 2.0
                    AND ABS(
                        (julianday(csv_row.closed_at)
                         - julianday(position_history.closed_at)) * 86400.0
                    ) <= 2.0
              )
            """
        )

    @staticmethod
    def _repair_okx_csv_position_pnl(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT id, payload_json
            FROM position_history
            WHERE exchange = 'okx' AND source = 'csv:okx'
            """
        ).fetchall()
        for row in rows:
            payload = _payload_object(row["payload_json"])
            breakdown = payload.get("realized_pnl_breakdown")
            if not isinstance(breakdown, dict):
                continue
            gross = _decimal(breakdown.get("gross_pnl"))
            trading_fee = _decimal(breakdown.get("trading_fee"))
            funding = _decimal(breakdown.get("funding"))
            liquidation_fee = _decimal(breakdown.get("liquidation_fee"))
            if any(
                value is None
                for value in (gross, trading_fee, funding, liquidation_fee)
            ):
                continue
            net = gross + trading_fee + funding + liquidation_fee
            fees = gross + funding - net
            net_text = format(net, "f")
            fees_text = format(fees, "f")
            if (
                payload.get("realized_pnl") == net_text
                and breakdown.get("fees") == fees_text
                and breakdown.get("net_pnl") == net_text
                and breakdown.get("funding_source") == "position"
                and breakdown.get("complete") is True
            ):
                continue
            payload["realized_pnl"] = net_text
            breakdown.update({
                "gross_pnl": format(gross, "f"),
                "fees": fees_text,
                "net_pnl": net_text,
                "funding_source": "position",
                "complete": True,
            })
            connection.execute(
                "UPDATE position_history SET payload_json = ? WHERE id = ?",
                (_json(payload), row["id"]),
            )

    @staticmethod
    def _repair_bitget_csv_position_pnl(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT id, payload_json
            FROM position_history
            WHERE exchange = 'bitget' AND source = 'csv:bitget'
            """
        ).fetchall()
        for row in rows:
            payload = _payload_object(row["payload_json"])
            breakdown = payload.get("realized_pnl_breakdown")
            if not isinstance(breakdown, dict):
                continue
            gross = _decimal(breakdown.get("gross_pnl"))
            net = _decimal(breakdown.get("net_pnl"))
            funding = _decimal(breakdown.get("funding_or_other"))
            if gross is None or net is None or funding is None:
                continue
            fees = gross + funding - net
            payload["realized_pnl"] = format(net, "f")
            breakdown.update({
                "fees": format(fees, "f"),
                "funding": format(funding, "f"),
                "funding_source": "position",
                "complete": True,
            })
            connection.execute(
                "UPDATE position_history SET payload_json = ? WHERE id = ?",
                (_json(payload), row["id"]),
            )

    @staticmethod
    def _allocate_position_funding(connection: sqlite3.Connection) -> None:
        position_rows = connection.execute(
            """
            SELECT id, account, exchange, symbol, side, opened_at, closed_at,
                   payload_json
            FROM position_history
            WHERE exchange IN ('binance', 'hyperliquid')
            """
        ).fetchall()
        event_rows = connection.execute(
            """
            SELECT account, exchange, symbol, occurred_at, payload_json
            FROM pnl_events
            WHERE exchange IN ('binance', 'hyperliquid')
            """
        ).fetchall()
        if not position_rows or not event_rows:
            return

        positions: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in position_rows:
            opened = _iso_timestamp(row["opened_at"])
            closed = _iso_timestamp(row["closed_at"])
            if opened is None or closed is None:
                continue
            payload = _payload_object(row["payload_json"])
            quantity = _decimal(payload.get("quantity"))
            if quantity is None or quantity <= 0:
                quantity = _decimal(payload.get("contracts")) or Decimal(1)
            item = {
                "id": int(row["id"]),
                "account": str(row["account"]),
                "exchange": str(row["exchange"]),
                "symbol": str(row["symbol"]),
                "side": str(row["side"]).lower(),
                "opened": opened,
                "closed": closed,
                "quantity": abs(quantity),
                "payload": payload,
            }
            key = (item["account"], item["exchange"], item["symbol"])
            positions.setdefault(key, []).append(item)

        allocations: dict[int, Decimal] = {}
        estimated: set[int] = set()
        covered_groups: set[tuple[str, str, str]] = set()
        for row in event_rows:
            payload = _payload_object(row["payload_json"])
            if str(payload.get("component") or "").lower() != "funding":
                continue
            amount = _decimal(payload.get("amount"))
            occurred = _iso_timestamp(row["occurred_at"])
            if amount is None or occurred is None:
                continue
            key = (
                str(row["account"]),
                str(row["exchange"]),
                str(row["symbol"]),
            )
            candidates = positions.get(key, [])
            if not candidates:
                continue
            covered_groups.add(key)
            event_side = str(payload.get("side") or "").lower()
            if event_side in {"long", "short"}:
                candidates = [item for item in candidates if item["side"] == event_side]

            event_time = datetime.fromtimestamp(occurred, UTC)
            is_daily_hyperliquid = (
                key[1] == "hyperliquid"
                and event_time.hour == 0
                and event_time.minute == 0
                and event_time.second == 0
            )
            weighted: list[tuple[dict[str, Any], Decimal]] = []
            if is_daily_hyperliquid:
                window_end = occurred + 86400.0
                for item in candidates:
                    overlap = min(item["closed"], window_end) - max(
                        item["opened"], occurred
                    )
                    if overlap > 0:
                        weighted.append(
                            (item, item["quantity"] * Decimal(str(overlap)))
                        )
            else:
                weighted = [
                    (item, item["quantity"])
                    for item in candidates
                    if item["opened"] <= occurred <= item["closed"]
                ]
            total_weight = sum((weight for _, weight in weighted), Decimal(0))
            if total_weight <= 0:
                continue
            for item, weight in weighted:
                position_id = int(item["id"])
                allocations[position_id] = (
                    allocations.get(position_id, Decimal(0))
                    + amount * weight / total_weight
                )
                if is_daily_hyperliquid:
                    estimated.add(position_id)

        for key in covered_groups:
            for item in positions.get(key, []):
                payload = item["payload"]
                breakdown = payload.get("realized_pnl_breakdown")
                if not isinstance(breakdown, dict):
                    continue
                funding = allocations.get(int(item["id"]), Decimal(0))
                gross = _decimal(breakdown.get("gross_pnl"))
                fees = _decimal(breakdown.get("fees"))
                trading_net = _decimal(breakdown.get("trading_net_pnl"))
                if trading_net is None and gross is not None and fees is not None:
                    trading_net = gross - fees
                if trading_net is None:
                    current_net = _decimal(payload.get("realized_pnl"))
                    previous_funding = (
                        _decimal(breakdown.get("funding"))
                        if breakdown.get("funding_source") == "ledger"
                        else Decimal(0)
                    )
                    if current_net is None or previous_funding is None:
                        continue
                    trading_net = current_net - previous_funding
                net = trading_net + funding
                funding_text = format(funding, "f")
                net_text = format(net, "f")
                payload["realized_pnl"] = net_text
                breakdown.update({
                    "funding": funding_text,
                    "funding_source": "ledger",
                    "funding_allocation": (
                        "estimated" if int(item["id"]) in estimated else "exact"
                    ),
                    "trading_net_pnl": format(trading_net, "f"),
                    "net_pnl": net_text,
                })
                connection.execute(
                    "UPDATE position_history SET payload_json = ? WHERE id = ?",
                    (_json(payload), int(item["id"])),
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
        if groups is None:
            groups = {
                (str(row["account"]), str(row["market_scope"]), str(row["symbol"]))
                for row in connection.execute(
                    """
                    SELECT DISTINCT account, market_scope, symbol
                    FROM fills
                    WHERE exchange = 'hyperliquid'
                      AND market_scope != 'spot' AND symbol != ''
                    """
                )
            }
        else:
            groups = {group for group in groups if group[1] != "spot"}
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
        derived_history_changed = False
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
                        derived_history_changed = True
                    connection.execute(
                        "DELETE FROM current_positions WHERE account = ? AND position_key = ?",
                        (account, key),
                    )
            if derived_history_changed:
                self._repair_replaced_derived_position_history(connection)
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
        pnl_events: list[dict[str, Any]] | None = None,
        imports: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        positions = positions or []
        cursors = cursors or []
        pnl_events = pnl_events or []
        imports = imports or []
        if not orders and not fills and not positions and not cursors and not pnl_events and not imports:
            return None
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        touched_binance_fills: set[tuple[str, str, str]] = set()
        touched_hyperliquid_fills: set[tuple[str, str, str]] = set()
        hyperliquid_fills_changed = False
        try:
            accepted_imports: set[str] = set()
            import_results: list[dict[str, Any]] = []
            pending_import_keys: dict[tuple[str, str, str, str], str] = {}
            for manifest in imports:
                import_id = str(manifest.get("import_id") or "").strip()
                account = str(manifest.get("account") or "").strip()
                exchange = str(manifest.get("exchange") or "").strip()
                file_type = str(manifest.get("file_type") or "").strip()
                file_hash = str(manifest.get("file_hash") or "").strip()
                if not all((import_id, account, exchange, file_type, file_hash)):
                    continue
                import_key = (account, exchange, file_type, file_hash)
                pending_import_id = pending_import_keys.get(import_key)
                if pending_import_id is not None:
                    import_results.append({
                        "import_id": import_id,
                        "status": "already_imported",
                        "existing_import_id": pending_import_id,
                        "name": str(manifest.get("original_name") or ""),
                    })
                    continue
                existing = connection.execute(
                    """
                    SELECT import_id
                    FROM csv_imports
                    WHERE account = ? AND exchange = ?
                      AND file_type = ? AND file_hash = ?
                    """,
                    (account, exchange, file_type, file_hash),
                ).fetchone()
                previous_import_id = ""
                if existing is not None:
                    previous_import_id = str(existing["import_id"])
                    connection.execute(
                        "DELETE FROM csv_imports WHERE import_id = ?",
                        (previous_import_id,),
                    )
                accepted_imports.add(import_id)
                pending_import_keys[import_key] = import_id
                import_results.append({
                    "import_id": import_id,
                    "status": "imported",
                    "reprocessed": bool(previous_import_id),
                    "name": str(manifest.get("original_name") or ""),
                })

            def record_allowed(record: dict[str, Any]) -> bool:
                import_id = str(record.get("import_id") or "").strip()
                return not imports or not import_id or import_id in accepted_imports

            for record in orders:
                if not record_allowed(record):
                    continue
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
                        source = excluded.source,
                        payload_json = excluded.payload_json
                    WHERE (
                            orders.updated_at IS NULL
                            AND excluded.updated_at IS NOT NULL
                        )
                        OR excluded.updated_at > orders.updated_at
                        OR (
                            excluded.updated_at = orders.updated_at
                            AND excluded.source LIKE 'csv:%'
                        )
                        OR (
                            orders.updated_at IS NULL
                            AND excluded.updated_at IS NULL
                            AND excluded.source LIKE 'csv:%'
                        )
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
                if not record_allowed(record):
                    continue
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
                        source = excluded.source,
                        payload_json = excluded.payload_json
                    WHERE fills.source NOT LIKE 'csv:%'
                       OR excluded.source LIKE 'csv:%'
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
                    hyperliquid_fills_changed = True
                    if market_scope != "spot":
                        touched_hyperliquid_fills.add(
                            (account, market_scope, symbol),
                        )

            if hyperliquid_fills_changed:
                touched_hyperliquid_fills.update(
                    self._repair_hyperliquid_csv_fill_overlaps(connection),
                )

            for record in positions:
                if not record_allowed(record):
                    continue
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
                    WHERE position_history.source NOT LIKE 'csv:%'
                       OR excluded.source LIKE 'csv:%'
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
            for record in pnl_events:
                if not record_allowed(record):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                account = str(record.get("account") or "").strip()
                exchange = str(record.get("exchange") or "").strip()
                event_id = str(record.get("event_id") or "").strip()
                event_type = str(record.get("event_type") or "").strip()
                if not all((account, exchange, event_id, event_type)):
                    continue
                symbol = str(record.get("symbol") or "")
                occurred_at = str(record.get("occurred_at") or "") or None
                amount = _decimal(payload.get("amount"))
                duplicate_ids: list[str] = []
                if amount is not None:
                    if occurred_at is None:
                        existing_events = connection.execute(
                            """
                            SELECT event_id, payload_json
                            FROM pnl_events
                            WHERE account = ? AND exchange = ? AND event_type = ?
                              AND symbol = ? AND occurred_at IS NULL
                            """,
                            (account, exchange, event_type, symbol),
                        ).fetchall()
                    else:
                        existing_events = connection.execute(
                            """
                            SELECT event_id, payload_json
                            FROM pnl_events
                            WHERE account = ? AND exchange = ? AND event_type = ?
                              AND symbol = ?
                              AND ABS(
                                  (julianday(occurred_at) - julianday(?))
                                  * 86400000.0
                              ) <= 1.0
                            """,
                            (account, exchange, event_type, symbol, occurred_at),
                        ).fetchall()
                    duplicate_ids = [
                        str(existing["event_id"])
                        for existing in existing_events
                        if str(existing["event_id"]) != event_id
                        and _decimal(
                            _payload_object(existing["payload_json"]).get("amount")
                        ) == amount
                    ]
                canonical_source = str(payload.get("canonical_source") or "")
                existing_event = connection.execute(
                    """
                    SELECT payload_json
                    FROM pnl_events
                    WHERE account = ? AND exchange = ?
                      AND event_id = ? AND event_type = ?
                    """,
                    (account, exchange, event_id, event_type),
                ).fetchone()
                if existing_event is not None:
                    existing_source = str(
                        _payload_object(existing_event["payload_json"]).get(
                            "canonical_source"
                        ) or ""
                    )
                    if (
                        existing_source.startswith("csv:")
                        and not canonical_source.startswith("csv:")
                    ):
                        continue
                if duplicate_ids and not canonical_source.startswith("csv:"):
                    continue
                for duplicate_id in duplicate_ids:
                    connection.execute(
                        """
                        DELETE FROM pnl_events
                        WHERE account = ? AND exchange = ?
                          AND event_id = ? AND event_type = ?
                        """,
                        (account, exchange, duplicate_id, event_type),
                    )
                connection.execute(
                    """
                    INSERT INTO pnl_events (
                        account, exchange, event_id, event_type,
                        symbol, occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account, exchange, event_id, event_type)
                    DO UPDATE SET
                        symbol = excluded.symbol,
                        occurred_at = excluded.occurred_at,
                        payload_json = excluded.payload_json
                    """,
                    (
                        account,
                        exchange,
                        event_id,
                        event_type,
                        symbol,
                        occurred_at,
                        _json(payload),
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
            if positions or touched_binance_fills or touched_hyperliquid_fills:
                self._repair_csv_position_history(connection)
            if imports or pnl_events:
                self._repair_okx_csv_position_pnl(connection)
                self._repair_bitget_csv_position_pnl(connection)
                self._allocate_position_funding(connection)

            imported_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            for manifest in imports:
                import_id = str(manifest.get("import_id") or "").strip()
                if import_id not in accepted_imports:
                    continue
                warnings = manifest.get("warnings")
                warnings = warnings if isinstance(warnings, list) else []
                connection.execute(
                    """
                    INSERT INTO csv_imports (
                        import_id, batch_id, file_hash, original_name,
                        account, exchange, file_type, market_scope,
                        source_timezone, row_count, first_occurred_at,
                        last_occurred_at, status, warnings_json, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_id,
                        str(manifest.get("batch_id") or import_id),
                        str(manifest.get("file_hash") or ""),
                        str(manifest.get("original_name") or ""),
                        str(manifest.get("account") or ""),
                        str(manifest.get("exchange") or ""),
                        str(manifest.get("file_type") or ""),
                        str(manifest.get("market_scope") or ""),
                        str(manifest.get("source_timezone") or ""),
                        int(manifest.get("row_count") or 0),
                        manifest.get("first_occurred_at"),
                        manifest.get("last_occurred_at"),
                        (
                            "partial"
                            if str(manifest.get("status") or "") == "partial"
                            else "imported"
                        ),
                        _json(warnings),
                        imported_at,
                    ),
                )
            connection.commit()
            return {
                "status": "ok",
                "imports": import_results,
                "summary": {
                    "imported": sum(item["status"] == "imported" for item in import_results),
                    "already_imported": sum(
                        item["status"] == "already_imported"
                        for item in import_results
                    ),
                    "orders": sum(record_allowed(record) for record in orders),
                    "fills": sum(record_allowed(record) for record in fills),
                    "positions": sum(record_allowed(record) for record in positions),
                    "pnl_events": sum(record_allowed(record) for record in pnl_events),
                },
            }
        except BaseException:
            connection.rollback()
            raise

    def apply_transfer_batch(
        self,
        transfers: list[dict[str, Any]],
        *,
        account: str,
        exchange: str,
        cursor: str | None = None,
        warnings: list[str] | None = None,
        status: str = "ok",
        error: str = "",
        update_sync: bool = True,
    ) -> dict[str, Any]:
        normalized_account = account.strip()
        normalized_exchange = exchange.strip().lower()
        if not normalized_account or not normalized_exchange:
            raise ValueError("transfer account and exchange are required")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            stored = 0
            for record in transfers:
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                record_account = str(record.get("account") or "").strip()
                record_exchange = str(record.get("exchange") or "").strip().lower()
                transfer_id = str(record.get("transfer_id") or "").strip()
                occurred_at = str(record.get("occurred_at") or "").strip()
                if not all((record_account, record_exchange, transfer_id, occurred_at)):
                    continue
                if str(record.get("source") or "native") == "native":
                    if self._native_transfer_has_local_account_record(
                        connection,
                        record,
                        account=record_account,
                        exchange=record_exchange,
                    ):
                        continue
                    self._remove_legacy_okx_transfer_key(
                        connection,
                        record,
                        account=record_account,
                        exchange=record_exchange,
                    )
                    self._remove_matching_local_transfer(
                        connection,
                        record,
                        account=record_account,
                        exchange=record_exchange,
                    )
                connection.execute(
                    """
                    INSERT INTO transfer_history (
                        account, exchange, transfer_id, occurred_at, asset,
                        amount, direction, status, source, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account, exchange, transfer_id) DO UPDATE SET
                        occurred_at = excluded.occurred_at,
                        asset = excluded.asset,
                        amount = excluded.amount,
                        direction = excluded.direction,
                        status = excluded.status,
                        source = excluded.source,
                        payload_json = excluded.payload_json
                    WHERE transfer_history.source != 'native'
                       OR excluded.source = 'native'
                    """,
                    (
                        record_account,
                        record_exchange,
                        transfer_id,
                        occurred_at,
                        str(record.get("asset") or ""),
                        str(record.get("amount") or ""),
                        str(record.get("direction") or "internal"),
                        str(record.get("status") or ""),
                        str(record.get("source") or "native"),
                        _json(payload),
                    ),
                )
                stored += 1
            if cursor is not None:
                connection.execute(
                    """
                    INSERT INTO sync_cursors (
                        account, exchange, stream, market_scope, cursor, updated_at
                    ) VALUES (?, ?, 'transfers', '', ?, ?)
                    ON CONFLICT(account, exchange, stream, market_scope)
                    DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at
                    """,
                    (
                        normalized_account,
                        normalized_exchange,
                        str(cursor),
                        now,
                    ),
                )
            if update_sync:
                previous = connection.execute(
                    """
                    SELECT last_success_at
                    FROM transfer_sync_status
                    WHERE account = ?
                    """,
                    (normalized_account,),
                ).fetchone()
                last_success = (
                    now
                    if status in {"ok", "partial"}
                    else (
                        str(previous["last_success_at"])
                        if previous and previous["last_success_at"]
                        else None
                    )
                )
                connection.execute(
                    """
                    INSERT INTO transfer_sync_status (
                        account, exchange, status, last_attempt_at, last_success_at,
                        last_error, warnings_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account) DO UPDATE SET
                        exchange = excluded.exchange,
                        status = excluded.status,
                        last_attempt_at = excluded.last_attempt_at,
                        last_success_at = excluded.last_success_at,
                        last_error = excluded.last_error,
                        warnings_json = excluded.warnings_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        normalized_account,
                        normalized_exchange,
                        status,
                        now,
                        last_success,
                        error or None,
                        _json(warnings or []),
                        now,
                    ),
                )
            connection.commit()
            return {"status": status, "stored": stored}
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _native_transfer_has_local_account_record(
        connection: sqlite3.Connection,
        record: dict[str, Any],
        *,
        account: str,
        exchange: str,
    ) -> bool:
        payload = record.get("payload")
        if not isinstance(payload, dict) or payload.get("transfer_kind") != "account":
            return False
        occurred_at = str(record.get("occurred_at") or "")
        try:
            occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        start = (occurred - timedelta(minutes=5)).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        )
        end = (occurred + timedelta(minutes=5)).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        )
        candidates = connection.execute(
            """
            SELECT asset, amount, direction, payload_json
            FROM transfer_history
            WHERE account = ? AND exchange = ? AND source = 'local'
              AND occurred_at BETWEEN ? AND ?
            """,
            (account, exchange, start, end),
        ).fetchall()
        for candidate in candidates:
            candidate_payload = _payload_object(candidate["payload_json"])
            if candidate_payload.get("transfer_kind") != "account":
                continue
            try:
                same_amount = Decimal(str(candidate["amount"])) == Decimal(
                    str(record.get("amount") or "0")
                )
            except (InvalidOperation, TypeError, ValueError):
                same_amount = str(candidate["amount"]) == str(record.get("amount") or "")
            if (
                str(candidate["asset"]) == str(record.get("asset") or "")
                and same_amount
                and str(candidate["direction"]) == str(record.get("direction") or "")
            ):
                return True
        return False

    @staticmethod
    def _remove_legacy_okx_transfer_key(
        connection: sqlite3.Connection,
        record: dict[str, Any],
        *,
        account: str,
        exchange: str,
    ) -> None:
        if exchange != "okx":
            return
        payload = record.get("payload")
        info = payload.get("info") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            return
        bill_id = str(info.get("billId") or "")
        legacy_id = str(info.get("transId") or "")
        if not bill_id or not legacy_id or bill_id == legacy_id:
            return
        row = connection.execute(
            """
            SELECT payload_json
            FROM transfer_history
            WHERE account = ? AND exchange = 'okx' AND transfer_id = ?
              AND source = 'native'
            """,
            (account, legacy_id),
        ).fetchone()
        if row is None:
            return
        legacy_payload = _payload_object(row["payload_json"])
        legacy_info = legacy_payload.get("info")
        if isinstance(legacy_info, dict) and str(legacy_info.get("billId") or "") == bill_id:
            connection.execute(
                """
                DELETE FROM transfer_history
                WHERE account = ? AND exchange = 'okx' AND transfer_id = ?
                  AND source = 'native'
                """,
                (account, legacy_id),
            )

    @staticmethod
    def _remove_matching_local_transfer(
        connection: sqlite3.Connection,
        record: dict[str, Any],
        *,
        account: str,
        exchange: str,
    ) -> None:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        occurred_at = str(record.get("occurred_at") or "")
        try:
            occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            return
        start = (occurred - timedelta(minutes=5)).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        )
        end = (occurred + timedelta(minutes=5)).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        )
        candidates = connection.execute(
            """
            SELECT transfer_id, asset, amount, direction, payload_json
            FROM transfer_history
            WHERE account = ? AND exchange = ? AND source = 'local'
              AND occurred_at BETWEEN ? AND ?
            """,
            (account, exchange, start, end),
        ).fetchall()

        def matches(candidate: sqlite3.Row) -> bool:
            candidate_payload = _payload_object(candidate["payload_json"])
            try:
                same_amount = Decimal(str(candidate["amount"])) == Decimal(
                    str(record.get("amount") or "0")
                )
            except (InvalidOperation, TypeError, ValueError):
                same_amount = str(candidate["amount"]) == str(record.get("amount") or "")
            return (
                str(candidate["asset"]) == str(record.get("asset") or "")
                and same_amount
                and str(candidate["direction"]) == str(record.get("direction") or "")
                and str(candidate_payload.get("from_account_type") or "")
                == str(payload.get("from_account_type") or "")
                and str(candidate_payload.get("to_account_type") or "")
                == str(payload.get("to_account_type") or "")
            )

        matching_ids = [str(row["transfer_id"]) for row in candidates if matches(row)]
        if len(matching_ids) == 1 and matching_ids[0] != str(record.get("transfer_id") or ""):
            connection.execute(
                """
                DELETE FROM transfer_history
                WHERE account = ? AND exchange = ? AND transfer_id = ? AND source = 'local'
                """,
                (account, exchange, matching_ids[0]),
            )

    def transfer_sync_state(self, account: str, exchange: str) -> dict[str, Any]:
        connection = self._read_connection()
        if connection is None:
            return {}
        try:
            row = connection.execute(
                """
                SELECT status, last_attempt_at, last_success_at, last_error,
                       warnings_json, updated_at
                FROM transfer_sync_status
                WHERE account = ? AND exchange = ?
                """,
                (account.strip(), exchange.strip().lower()),
            ).fetchone()
            cursor = connection.execute(
                """
                SELECT cursor, updated_at
                FROM sync_cursors
                WHERE account = ? AND exchange = ?
                  AND stream = 'transfers' AND market_scope = ''
                """,
                (account.strip(), exchange.strip().lower()),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return {}
            raise
        finally:
            connection.close()
        if row is None:
            return {}
        try:
            warnings = json.loads(str(row["warnings_json"] or "[]"))
        except json.JSONDecodeError:
            warnings = []
        return {
            "status": str(row["status"] or ""),
            "last_attempt_at": row["last_attempt_at"],
            "last_success_at": row["last_success_at"],
            "last_error": row["last_error"],
            "warnings": warnings if isinstance(warnings, list) else [],
            "cursor": str(cursor["cursor"] or "") if cursor else "",
            "cursor_updated_at": cursor["updated_at"] if cursor else None,
        }

    def transfer_history_page(
        self,
        *,
        account: str,
        exchange: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        normalized_limit = _page_limit(limit)
        decoded_cursor = _decode_cursor(cursor, 2)
        connection = self._read_connection()
        if connection is None:
            return {"results": [], "next_cursor": None, "total": 0}
        clauses = ["account = ?", "exchange = ?"]
        parameters: list[Any] = [account.strip(), exchange.strip().lower()]
        if decoded_cursor is not None:
            occurred_at, transfer_id = decoded_cursor
            if not isinstance(occurred_at, str) or not isinstance(transfer_id, str):
                connection.close()
                raise ValueError("invalid history cursor")
            clauses.append("(occurred_at < ? OR (occurred_at = ? AND transfer_id < ?))")
            parameters.extend([occurred_at, occurred_at, transfer_id])
        where = " AND ".join(clauses)
        try:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM transfer_history WHERE account = ? AND exchange = ?",
                (account.strip(), exchange.strip().lower()),
            ).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT account, exchange, transfer_id, occurred_at, asset,
                       amount, direction, status, source, payload_json
                FROM transfer_history
                WHERE {where}
                ORDER BY occurred_at DESC, transfer_id DESC
                LIMIT ?
                """,
                [*parameters, normalized_limit + 1],
            ).fetchall()
            has_more = len(rows) > normalized_limit
            selected = rows[:normalized_limit]
            results: list[dict[str, Any]] = []
            for row in selected:
                payload = _payload_object(row["payload_json"])
                payload.update({
                    "account": str(row["account"]),
                    "exchange": str(row["exchange"]),
                    "id": str(row["transfer_id"]),
                    "datetime": str(row["occurred_at"]),
                    "currency": str(row["asset"]),
                    "amount": str(row["amount"]),
                    "direction": str(row["direction"]),
                    "status": str(row["status"]),
                    "source": str(row["source"]),
                })
                results.append(payload)
            self._match_okx_transfer_accounts(connection, results)
            self._match_transfer_account_ids(connection, results)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return {"results": [], "next_cursor": None, "total": 0}
            raise
        finally:
            connection.close()
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = _encode_cursor([
                str(last["occurred_at"]),
                str(last["transfer_id"]),
            ])
        return {"results": results, "next_cursor": next_cursor, "total": total}

    @staticmethod
    def _match_okx_transfer_accounts(
        connection: sqlite3.Connection,
        results: list[dict[str, Any]],
    ) -> None:
        counterpart_types = {"20": "23", "21": "22", "22": "21", "23": "20"}
        account_fields = {
            "20": "to_account",
            "21": "from_account",
            "22": "to_account",
            "23": "from_account",
        }
        generic_accounts = {"main_account", "sub_account"}
        for payload in results:
            if (
                payload.get("exchange") != "okx"
                or payload.get("transfer_kind") != "account"
            ):
                continue
            info = payload.get("info")
            bill_type = str(info.get("type") or "") if isinstance(info, dict) else ""
            field = account_fields.get(bill_type)
            if field is None or str(payload.get(field) or "") not in generic_accounts:
                continue
            occurred = _iso_timestamp(payload.get("datetime"))
            if occurred is None:
                continue
            start = datetime.fromtimestamp(occurred - 2, UTC).isoformat().replace(
                "+00:00", "Z"
            )
            end = datetime.fromtimestamp(occurred + 2, UTC).isoformat().replace(
                "+00:00", "Z"
            )
            candidates = connection.execute(
                """
                SELECT account, amount, payload_json
                FROM transfer_history
                WHERE exchange = 'okx' AND account != ? AND asset = ?
                  AND occurred_at BETWEEN ? AND ?
                """,
                (
                    str(payload.get("account") or ""),
                    str(payload.get("currency") or ""),
                    start,
                    end,
                ),
            ).fetchall()
            matches: list[str] = []
            for candidate in candidates:
                candidate_payload = _payload_object(candidate["payload_json"])
                candidate_info = candidate_payload.get("info")
                candidate_type = (
                    str(candidate_info.get("type") or "")
                    if isinstance(candidate_info, dict)
                    else ""
                )
                if candidate_type != counterpart_types[bill_type]:
                    continue
                try:
                    same_amount = Decimal(str(candidate["amount"])) == Decimal(
                        str(payload.get("amount") or "0")
                    )
                except (InvalidOperation, TypeError, ValueError):
                    same_amount = str(candidate["amount"]) == str(
                        payload.get("amount") or ""
                    )
                if same_amount:
                    matches.append(str(candidate["account"]))
            if len(matches) == 1:
                payload[field] = matches[0]

    @staticmethod
    def _match_transfer_account_ids(
        connection: sqlite3.Connection,
        results: list[dict[str, Any]],
    ) -> None:
        exchanges = {
            str(payload.get("exchange") or "")
            for payload in results
            if payload.get("exchange") in {
                "binance",
                "bitget",
                "bybit",
                "hyperliquid",
            }
        }
        for exchange in exchanges:
            rows = connection.execute(
                """
                SELECT account, direction, payload_json
                FROM transfer_history
                WHERE exchange = ? AND direction IN ('in', 'out')
                """,
                (exchange,),
            ).fetchall()
            accounts_by_id: dict[str, set[str]] = {}
            for row in rows:
                payload = _payload_object(row["payload_json"])
                direction = str(row["direction"] or "")
                field = "from_account" if direction == "out" else "to_account"
                external_id = str(payload.get(field) or "").strip()
                account_name = str(row["account"] or "").strip()
                if external_id and account_name:
                    accounts_by_id.setdefault(external_id, set()).add(account_name)
            resolved = {
                external_id: next(iter(account_names))
                for external_id, account_names in accounts_by_id.items()
                if len(account_names) == 1
            }
            for payload in results:
                if payload.get("exchange") != exchange:
                    continue
                for field in ("from_account", "to_account"):
                    external_id = str(payload.get(field) or "").strip()
                    if external_id in resolved:
                        payload[field] = resolved[external_id]

    def rows(self, table: str) -> list[dict[str, Any]]:
        if table not in {
            "current_positions",
            "position_history",
            "orders",
            "fills",
            "pnl_events",
            "transfer_history",
            "transfer_sync_status",
            "account_sync_status",
            "csv_imports",
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

    def pnl_summary(
        self,
        *,
        days: int | None = 90,
        account: str = "",
        exchange: str = "",
    ) -> dict[str, Any]:
        if days is not None and (
            isinstance(days, bool) or not 1 <= days <= 365
        ):
            raise ValueError("days must be between 1 and 365")

        now = datetime.now(UTC)
        since_text = (
            (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
            if days is not None
            else None
        )
        filters: list[str] = []
        params: list[str] = []
        for column, value in (("account", account), ("exchange", exchange)):
            normalized = value.strip().lower()
            if normalized:
                filters.append(f"LOWER({column}) = ?")
                params.append(normalized)
        common_filter = f" AND {' AND '.join(filters)}" if filters else ""

        connection = self._read_connection()
        if connection is None:
            return {
                "from": since_text,
                "to": now.isoformat().replace("+00:00", "Z"),
                "days": days,
                "results": [],
                "totals": [],
            }
        try:
            if since_text is None:
                history_rows = connection.execute(
                    f"""
                    SELECT account, exchange, symbol, payload_json
                    FROM position_history
                    WHERE 1 = 1{common_filter}
                    """,
                    params,
                ).fetchall()
                event_rows = connection.execute(
                    f"""
                    SELECT account, exchange, symbol, occurred_at, payload_json
                    FROM pnl_events
                    WHERE 1 = 1{common_filter}
                    """,
                    params,
                ).fetchall()
            else:
                history_rows = connection.execute(
                    f"""
                    SELECT account, exchange, symbol, payload_json
                    FROM position_history
                    WHERE closed_at >= ?{common_filter}
                    """,
                    [since_text, *params],
                ).fetchall()
                event_rows = connection.execute(
                    f"""
                    SELECT account, exchange, symbol, occurred_at, payload_json
                    FROM pnl_events
                    WHERE occurred_at >= ?{common_filter}
                    """,
                    [since_text, *params],
                ).fetchall()
            current_rows = connection.execute(
                f"""
                SELECT account, exchange, symbol, payload_json
                FROM current_positions
                WHERE 1 = 1{common_filter}
                """,
                params,
            ).fetchall()
        finally:
            connection.close()

        buckets: dict[tuple[str, str, str], dict[str, Any]] = {}

        def bucket_for(
            row: sqlite3.Row,
            payload: dict[str, Any],
            currency_override: str = "",
        ) -> dict[str, Any]:
            account_name = str(row["account"])
            exchange_id = str(row["exchange"])
            currency = currency_override or _settlement_currency(
                str(row["symbol"]),
                payload,
            )
            key = (account_name, exchange_id, currency)
            return buckets.setdefault(
                key,
                _new_pnl_bucket(account_name, exchange_id, currency),
            )

        for row in history_rows:
            payload = _payload_object(row["payload_json"])
            bucket = bucket_for(row, payload)
            bucket["closed_positions"] += 1
            breakdown = payload.get("realized_pnl_breakdown")
            breakdown = breakdown if isinstance(breakdown, dict) else {}
            realized = _decimal(payload.get("realized_pnl"))
            if realized is None:
                bucket["realized_complete"] = False
            else:
                allocated_funding = _decimal(breakdown.get("funding"))
                if (
                    breakdown.get("funding_source") == "ledger"
                    and allocated_funding is not None
                ):
                    realized -= allocated_funding
                bucket["realized_pnl"] += realized

            gross = _decimal(breakdown.get("gross_pnl"))
            fees = _decimal(breakdown.get("fees"))
            if breakdown.get("complete") is True and gross is not None and fees is not None:
                bucket["gross_realized_pnl"] += gross
                bucket["fees"] += fees
            else:
                bucket["fee_breakdown_complete"] = False
            position_funding = _decimal(breakdown.get("funding"))
            if (
                breakdown.get("funding_source") == "position"
                and position_funding is not None
            ):
                bucket["funding"] += position_funding
                bucket["funding_available"] = True

        for row in event_rows:
            payload = _payload_object(row["payload_json"])
            if payload.get("count_in_pnl") is not True:
                continue
            amount = _decimal(payload.get("amount"))
            currency = str(payload.get("currency") or "").strip().upper()
            component = str(payload.get("component") or "").strip().lower()
            if amount is None or not currency:
                continue
            bucket = bucket_for(row, payload, currency)
            bucket["realized_pnl"] += amount
            if component == "funding":
                bucket["funding"] += amount
                bucket["funding_available"] = True
            elif component == "borrow_interest":
                bucket["borrow_interest"] += amount
                bucket["borrow_interest_available"] = True
            elif component == "trading_fee":
                bucket["fees"] += -amount

        for row in current_rows:
            payload = _payload_object(row["payload_json"])
            bucket = bucket_for(row, payload)
            bucket["open_positions"] += 1
            unrealized = _decimal(payload.get("unrealized_pnl"))
            if unrealized is None:
                bucket["unrealized_complete"] = False
            else:
                bucket["unrealized_pnl"] += unrealized

        totals: dict[str, dict[str, Any]] = {}
        for bucket in buckets.values():
            currency = str(bucket["currency"])
            total = totals.setdefault(
                currency,
                _new_pnl_bucket("", "", currency),
            )
            for field in (
                "realized_pnl",
                "unrealized_pnl",
                "gross_realized_pnl",
                "fees",
                "funding",
                "borrow_interest",
            ):
                total[field] += bucket[field]
            for field in ("closed_positions", "open_positions"):
                total[field] += bucket[field]
            for field in (
                "realized_complete",
                "unrealized_complete",
                "fee_breakdown_complete",
            ):
                total[field] = total[field] and bucket[field]
            for field in ("funding_available", "borrow_interest_available"):
                total[field] = total[field] or bucket[field]

        ordered_buckets = sorted(
            buckets.values(),
            key=lambda item: (
                item["exchange"].lower(),
                item["account"].lower(),
                item["currency"],
            ),
        )
        ordered_totals = sorted(totals.values(), key=lambda item: item["currency"])
        return {
            "from": since_text,
            "to": now.isoformat().replace("+00:00", "Z"),
            "days": days,
            "results": [_serialize_pnl_bucket(item) for item in ordered_buckets],
            "totals": [_serialize_pnl_bucket(item) for item in ordered_totals],
        }

    def csv_imports(self, *, limit: int = 100) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        connection = self._read_connection()
        if connection is None:
            return {"results": [], "total": 0}
        try:
            total = int(connection.execute("SELECT COUNT(*) FROM csv_imports").fetchone()[0])
            rows = connection.execute(
                """
                SELECT import_id, batch_id, original_name, account, exchange,
                       file_type, market_scope, source_timezone, row_count,
                       first_occurred_at, last_occurred_at, status,
                       warnings_json, imported_at
                FROM csv_imports
                ORDER BY imported_at DESC, original_name
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        results = []
        for row in rows:
            item = dict(row)
            try:
                warnings = json.loads(str(item.pop("warnings_json") or "[]"))
            except json.JSONDecodeError:
                warnings = []
            item["warnings"] = warnings if isinstance(warnings, list) else []
            results.append(item)
        return {"results": results, "total": total}

    def csv_import(self, import_id: str) -> dict[str, Any] | None:
        normalized_id = import_id.strip()
        if not normalized_id:
            return None
        connection = self._read_connection()
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM csv_imports WHERE import_id = ?",
                (normalized_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        try:
            warnings = json.loads(str(result.pop("warnings_json") or "[]"))
        except json.JSONDecodeError:
            warnings = []
        result["warnings"] = warnings if isinstance(warnings, list) else []
        return result

    def delete_csv_import(self, import_id: str) -> dict[str, Any]:
        normalized_id = import_id.strip()
        if not normalized_id:
            raise ValueError("CSV import was not found")
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT original_name FROM csv_imports WHERE import_id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise ValueError("CSV import was not found")
            connection.execute(
                "DELETE FROM csv_imports WHERE import_id = ?",
                (normalized_id,),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {
            "status": "deleted",
            "import_id": normalized_id,
            "original_name": str(row["original_name"] or ""),
        }

    def update_csv_import_status(
        self,
        import_id: str,
        *,
        status: str,
        warnings: list[str],
    ) -> None:
        if status not in {"imported", "partial"}:
            raise ValueError("unsupported CSV import status")
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                """
                UPDATE csv_imports
                SET status = ?, warnings_json = ?
                WHERE import_id = ?
                """,
                (status, _json(warnings), import_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("CSV import was not found")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

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
