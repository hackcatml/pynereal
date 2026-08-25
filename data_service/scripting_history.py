from __future__ import annotations

import hashlib
import sqlite3
import threading
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_BUSY_TIMEOUT_SECONDS = 5.0
_BUSY_TIMEOUT_MS = int(_BUSY_TIMEOUT_SECONDS * 1000)
_VALID_SOURCES = {"baseline", "manual", "ai", "external", "restore"}


class ScriptingHistoryError(Exception):
    pass


class ScriptingRevisionNotFoundError(ScriptingHistoryError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _content_revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ScriptingHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=_BUSY_TIMEOUT_SECONDS,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scripting_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    current_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );

                CREATE TABLE IF NOT EXISTS scripting_blobs (
                    revision TEXT PRIMARY KEY,
                    content BLOB NOT NULL,
                    size INTEGER NOT NULL,
                    line_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scripting_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    revision TEXT NOT NULL,
                    parent_id INTEGER,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    note TEXT,
                    restored_from_id INTEGER,
                    state TEXT NOT NULL,
                    FOREIGN KEY (file_id) REFERENCES scripting_files(id),
                    FOREIGN KEY (revision) REFERENCES scripting_blobs(revision),
                    FOREIGN KEY (parent_id) REFERENCES scripting_revisions(id),
                    FOREIGN KEY (restored_from_id) REFERENCES scripting_revisions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_scripting_revisions_file_state_id
                ON scripting_revisions(file_id, state, id DESC);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(scripting_revisions)"
                ).fetchall()
            }
            if "note" not in columns:
                connection.execute(
                    "ALTER TABLE scripting_revisions ADD COLUMN note TEXT"
                )

    @staticmethod
    def _store_blob(connection: sqlite3.Connection, content: bytes) -> str:
        revision = _content_revision(content)
        connection.execute(
            """
            INSERT OR IGNORE INTO scripting_blobs
                (revision, content, size, line_count)
            VALUES (?, ?, ?, ?)
            """,
            (
                revision,
                sqlite3.Binary(zlib.compress(content)),
                len(content),
                content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0),
            ),
        )
        return revision

    @staticmethod
    def _file_id(connection: sqlite3.Connection, relative_path: str) -> int:
        row = connection.execute(
            "SELECT id FROM scripting_files WHERE current_path = ?",
            (relative_path,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = connection.execute(
            """
            INSERT INTO scripting_files (current_path, created_at, deleted_at)
            VALUES (?, ?, NULL)
            """,
            (relative_path, _utc_now()),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _latest_committed(
        connection: sqlite3.Connection,
        file_id: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT id, revision, created_at, source, note, restored_from_id
            FROM scripting_revisions
            WHERE file_id = ? AND state = 'committed'
            ORDER BY id DESC
            LIMIT 1
            """,
            (file_id,),
        ).fetchone()

    @staticmethod
    def _revision_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "revision": str(row["revision"]),
            "created_at": str(row["created_at"]),
            "source": str(row["source"]),
            "note": str(row["note"]) if row["note"] is not None else None,
            "restored_from_id": (
                int(row["restored_from_id"])
                if row["restored_from_id"] is not None
                else None
            ),
        }

    def observe(self, relative_path: str, content: bytes) -> dict[str, Any]:
        disk_revision = _content_revision(content)
        with self._lock, self._connection() as connection:
            file_id = self._file_id(connection, relative_path)
            pending_rows = connection.execute(
                """
                SELECT id, revision
                FROM scripting_revisions
                WHERE file_id = ? AND state = 'pending'
                ORDER BY id DESC
                """,
                (file_id,),
            ).fetchall()
            matching_pending_id = next(
                (
                    int(row["id"])
                    for row in pending_rows
                    if str(row["revision"]) == disk_revision
                ),
                None,
            )
            for row in pending_rows:
                state = (
                    "committed"
                    if matching_pending_id is not None and int(row["id"]) == matching_pending_id
                    else "failed"
                )
                connection.execute(
                    "UPDATE scripting_revisions SET state = ? WHERE id = ?",
                    (state, int(row["id"])),
                )

            latest = self._latest_committed(connection, file_id)
            if latest is None or str(latest["revision"]) != disk_revision:
                self._store_blob(connection, content)
                cursor = connection.execute(
                    """
                    INSERT INTO scripting_revisions
                        (file_id, revision, parent_id, created_at, source,
                         note, restored_from_id, state)
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, 'committed')
                    """,
                    (
                        file_id,
                        disk_revision,
                        int(latest["id"]) if latest is not None else None,
                        _utc_now(),
                        "external" if latest is not None else "baseline",
                    ),
                )
                latest = connection.execute(
                    """
                    SELECT id, revision, created_at, source, note, restored_from_id
                    FROM scripting_revisions WHERE id = ?
                    """,
                    (int(cursor.lastrowid),),
                ).fetchone()
            if latest is None:
                raise ScriptingHistoryError("failed to observe script revision")
            return self._revision_payload(latest)

    def prepare_revision(
        self,
        relative_path: str,
        content: bytes,
        *,
        source: str,
        note: str | None = None,
        restored_from_id: int | None = None,
    ) -> dict[str, Any]:
        if source not in _VALID_SOURCES - {"baseline", "external"}:
            raise ScriptingHistoryError(f"invalid scripting revision source: {source}")
        with self._lock, self._connection() as connection:
            file_id = self._file_id(connection, relative_path)
            latest = self._latest_committed(connection, file_id)
            revision = self._store_blob(connection, content)
            cursor = connection.execute(
                """
                INSERT INTO scripting_revisions
                    (file_id, revision, parent_id, created_at, source,
                     note, restored_from_id, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    file_id,
                    revision,
                    int(latest["id"]) if latest is not None else None,
                    _utc_now(),
                    source,
                    note,
                    restored_from_id,
                ),
            )
            return {
                "id": int(cursor.lastrowid),
                "revision": revision,
            }

    def mark_committed(self, revision_id: int) -> None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE scripting_revisions
                SET state = 'committed'
                WHERE id = ? AND state = 'pending'
                """,
                (revision_id,),
            )
            if cursor.rowcount != 1:
                raise ScriptingHistoryError("pending script revision was not found")

    def mark_failed(self, revision_id: int) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE scripting_revisions
                SET state = 'failed'
                WHERE id = ? AND state = 'pending'
                """,
                (revision_id,),
            )

    def list_revisions(self, relative_path: str, limit: int = 100) -> list[dict[str, Any]]:
        resolved_limit = min(max(int(limit), 1), 500)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM scripting_files WHERE current_path = ?",
                (relative_path,),
            ).fetchone()
            if row is None:
                return []
            rows = connection.execute(
                """
                SELECT
                    revisions.id,
                    revisions.revision,
                    revisions.created_at,
                    revisions.source,
                    revisions.note,
                    revisions.restored_from_id,
                    blobs.size,
                    blobs.line_count
                FROM scripting_revisions AS revisions
                JOIN scripting_blobs AS blobs ON blobs.revision = revisions.revision
                WHERE revisions.file_id = ? AND revisions.state = 'committed'
                ORDER BY revisions.id DESC
                LIMIT ?
                """,
                (int(row["id"]), resolved_limit),
            ).fetchall()
            return [
                self._revision_payload(item)
                | {
                    "size": int(item["size"]),
                    "line_count": int(item["line_count"]),
                }
                for item in rows
            ]

    def update_revision_note(
        self,
        relative_path: str,
        revision_id: int,
        note: str | None,
    ) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE scripting_revisions
                SET note = ?
                WHERE id = ?
                  AND state = 'committed'
                  AND file_id = (
                      SELECT id FROM scripting_files WHERE current_path = ?
                  )
                """,
                (note, int(revision_id), relative_path),
            )
            if cursor.rowcount != 1:
                raise ScriptingRevisionNotFoundError("script revision not found")
            row = connection.execute(
                """
                SELECT id, revision, created_at, source, note, restored_from_id
                FROM scripting_revisions
                WHERE id = ?
                """,
                (int(revision_id),),
            ).fetchone()
            if row is None:
                raise ScriptingRevisionNotFoundError("script revision not found")
            return self._revision_payload(row)

    def get_revision(self, relative_path: str, revision_id: int) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    revisions.id,
                    revisions.revision,
                    revisions.created_at,
                    revisions.source,
                    revisions.note,
                    revisions.restored_from_id,
                    blobs.content,
                    blobs.size,
                    blobs.line_count
                FROM scripting_revisions AS revisions
                JOIN scripting_files AS files ON files.id = revisions.file_id
                JOIN scripting_blobs AS blobs ON blobs.revision = revisions.revision
                WHERE files.current_path = ?
                  AND revisions.id = ?
                  AND revisions.state = 'committed'
                """,
                (relative_path, int(revision_id)),
            ).fetchone()
            if row is None:
                raise ScriptingRevisionNotFoundError("script revision not found")
            try:
                content_bytes = zlib.decompress(bytes(row["content"]))
                content = content_bytes.decode("utf-8")
            except (zlib.error, UnicodeDecodeError) as exc:
                raise ScriptingHistoryError("stored script revision is invalid") from exc
            if _content_revision(content_bytes) != str(row["revision"]):
                raise ScriptingHistoryError("stored script revision checksum mismatch")
            return self._revision_payload(row) | {
                "content": content,
                "size": int(row["size"]),
                "line_count": int(row["line_count"]),
            }
