from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from scripting_history import (
    ScriptingHistoryError,
    ScriptingHistoryStore,
    ScriptingRevisionNotFoundError,
)


class ScriptingWorkspaceError(Exception):
    pass


class ScriptingPathError(ScriptingWorkspaceError):
    pass


class ScriptingFileNotFoundError(ScriptingWorkspaceError):
    pass


class ScriptingFileTypeError(ScriptingWorkspaceError):
    pass


class ScriptingFileEncodingError(ScriptingWorkspaceError):
    pass


class ScriptingFileTooLargeError(ScriptingWorkspaceError):
    pass


class ScriptingNoteError(ScriptingWorkspaceError):
    pass


class ScriptingConflictError(ScriptingWorkspaceError):
    def __init__(self, current_revision: str) -> None:
        super().__init__("script changed after it was loaded")
        self.current_revision = current_revision


class ScriptingWorkspace:
    _MAX_FILE_BYTES = 2 * 1024 * 1024
    _MAX_NOTE_CHARS = 240
    _SUPPORTED_TYPES = {
        ".py": "python",
        ".md": "markdown",
    }
    _EXCLUDED_NAMES = {"__pycache__"}

    def __init__(
        self,
        scripts_root: Path,
        history_store: ScriptingHistoryStore | None = None,
    ) -> None:
        root = scripts_root.absolute()
        if root.is_symlink():
            raise ScriptingPathError("scripts root must not be a symbolic link")
        self.root = root.resolve(strict=False)
        self.history_store = history_store
        self._write_lock = threading.RLock()

    def tree_payload(self) -> dict[str, Any]:
        if not self.root.exists():
            return {"root": "workdir/scripts", "entries": []}
        if not self.root.is_dir():
            raise ScriptingPathError("scripts root is not a directory")
        try:
            entries = self._scan_directory(self.root)
        except OSError as exc:
            raise ScriptingWorkspaceError("failed to read scripts directory") from exc
        return {
            "root": "workdir/scripts",
            "entries": entries,
        }

    def read_file(self, relative_path: str) -> dict[str, Any]:
        path, normalized = self._resolve_file_path(relative_path)
        if not path.exists() or not path.is_file():
            raise ScriptingFileNotFoundError(f"script file not found: {normalized}")

        content_bytes, content = self._read_content(path, normalized)
        payload = {
            "path": normalized,
            "name": path.name,
            "language": self._SUPPORTED_TYPES[path.suffix.lower()],
            "content": content,
            "size": len(content_bytes),
            "revision": hashlib.sha256(content_bytes).hexdigest(),
        }
        if self.history_store is not None:
            try:
                history_revision = self.history_store.observe(
                    normalized,
                    content_bytes,
                )
                payload["history_revision"] = history_revision
                payload["history_revision_id"] = int(history_revision["id"])
                payload["note"] = history_revision.get("note")
            except ScriptingHistoryError as exc:
                raise ScriptingWorkspaceError("failed to record script history") from exc
        return payload

    def save_file(
        self,
        relative_path: str,
        content: str,
        base_revision: str,
        *,
        source: str = "manual",
        note: str | None = None,
        restored_from_id: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ScriptingFileEncodingError("script content must be a UTF-8 string")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self._MAX_FILE_BYTES:
            raise ScriptingFileTooLargeError("script file exceeds the 2 MB limit")
        if not isinstance(base_revision, str) or len(base_revision) != 64:
            raise ScriptingConflictError("")
        normalized_note = self._normalize_note(note)
        if self.history_store is None:
            raise ScriptingWorkspaceError("script history is not configured")

        with self._write_lock:
            path, normalized = self._resolve_file_path(relative_path)
            if not path.exists() or not path.is_file():
                raise ScriptingFileNotFoundError(f"script file not found: {normalized}")
            current_bytes, _ = self._read_content(path, normalized)
            current_revision = hashlib.sha256(current_bytes).hexdigest()
            try:
                current_history_revision = self.history_store.observe(
                    normalized,
                    current_bytes,
                )
            except ScriptingHistoryError as exc:
                raise ScriptingWorkspaceError("failed to reconcile script history") from exc
            if current_revision != base_revision:
                raise ScriptingConflictError(current_revision)
            if current_bytes == content_bytes:
                note_saved = False
                if (
                    note is not None
                    and current_history_revision.get("note") != normalized_note
                ):
                    try:
                        self.history_store.update_revision_note(
                            normalized,
                            int(current_history_revision["id"]),
                            normalized_note,
                        )
                    except ScriptingHistoryError as exc:
                        raise ScriptingWorkspaceError(
                            "failed to update version note"
                        ) from exc
                    note_saved = True
                return self.read_file(normalized) | {
                    "ok": True,
                    "saved": False,
                    "note_saved": note_saved,
                    "apply_state": "next_warmup",
                }

            try:
                pending = self.history_store.prepare_revision(
                    normalized,
                    content_bytes,
                    source=source,
                    note=normalized_note,
                    restored_from_id=restored_from_id,
                )
            except ScriptingHistoryError as exc:
                raise ScriptingWorkspaceError("failed to prepare script history") from exc

            try:
                latest_bytes, _ = self._read_content(path, normalized)
                latest_revision = hashlib.sha256(latest_bytes).hexdigest()
                if latest_revision != base_revision:
                    self.history_store.mark_failed(int(pending["id"]))
                    self.history_store.observe(normalized, latest_bytes)
                    raise ScriptingConflictError(latest_revision)
                self._atomic_replace(path, content_bytes)
            except ScriptingConflictError:
                raise
            except Exception as exc:
                try:
                    self.history_store.mark_failed(int(pending["id"]))
                except ScriptingHistoryError:
                    pass
                if isinstance(exc, ScriptingWorkspaceError):
                    raise
                raise ScriptingWorkspaceError(
                    f"failed to save script file: {normalized}"
                ) from exc

            try:
                self.history_store.mark_committed(int(pending["id"]))
            except ScriptingHistoryError:
                # A later read reconciles a pending row whose checksum matches disk.
                try:
                    self.history_store.observe(normalized, content_bytes)
                except ScriptingHistoryError as exc:
                    raise ScriptingWorkspaceError(
                        "script was saved but its history could not be finalized"
                    ) from exc

            return self.read_file(normalized) | {
                "ok": True,
                "saved": True,
                "note_saved": False,
                "apply_state": "next_warmup",
                "revision_id": int(pending["id"]),
            }

    def history_payload(self, relative_path: str, limit: int = 100) -> dict[str, Any]:
        current = self.read_file(relative_path)
        if self.history_store is None:
            raise ScriptingWorkspaceError("script history is not configured")
        try:
            revisions = self.history_store.list_revisions(current["path"], limit)
        except ScriptingHistoryError as exc:
            raise ScriptingWorkspaceError("failed to read script history") from exc
        return {
            "path": current["path"],
            "current_revision": current["revision"],
            "revisions": revisions,
        }

    def revision_payload(self, relative_path: str, revision_id: int) -> dict[str, Any]:
        path, normalized = self._resolve_file_path(relative_path)
        if not path.exists() or not path.is_file():
            raise ScriptingFileNotFoundError(f"script file not found: {normalized}")
        if self.history_store is None:
            raise ScriptingWorkspaceError("script history is not configured")
        try:
            return {"path": normalized} | self.history_store.get_revision(
                normalized,
                revision_id,
            )
        except ScriptingRevisionNotFoundError:
            raise
        except ScriptingHistoryError as exc:
            raise ScriptingWorkspaceError("failed to read script revision") from exc

    def diff_payload(self, relative_path: str, revision_id: int) -> dict[str, Any]:
        current = self.read_file(relative_path)
        selected = self.revision_payload(relative_path, revision_id)
        diff = "".join(
            difflib.unified_diff(
                selected["content"].splitlines(keepends=True),
                current["content"].splitlines(keepends=True),
                fromfile=f"revision-{revision_id}",
                tofile=current["path"],
            )
        )
        return {
            "path": current["path"],
            "revision_id": int(revision_id),
            "selected_revision": selected["revision"],
            "current_revision": current["revision"],
            "diff": diff,
            "changed": selected["revision"] != current["revision"],
        }

    def restore_file(
        self,
        relative_path: str,
        revision_id: int,
        base_revision: str,
    ) -> dict[str, Any]:
        selected = self.revision_payload(relative_path, revision_id)
        return self.save_file(
            relative_path,
            selected["content"],
            base_revision,
            source="restore",
            note=selected.get("note"),
            restored_from_id=int(revision_id),
        )

    def _read_content(self, path: Path, normalized: str) -> tuple[bytes, str]:
        try:
            content_bytes = path.read_bytes()
        except OSError as exc:
            raise ScriptingWorkspaceError(
                f"failed to read script file: {normalized}"
            ) from exc
        if len(content_bytes) > self._MAX_FILE_BYTES:
            raise ScriptingFileTooLargeError("script file exceeds the 2 MB limit")
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScriptingFileEncodingError(
                f"script file is not valid UTF-8: {normalized}"
            ) from exc
        return content_bytes, content

    @classmethod
    def _normalize_note(cls, note: str | None) -> str | None:
        if note is None:
            return None
        if not isinstance(note, str):
            raise ScriptingNoteError("version note must be a string")
        normalized = " ".join(note.split())
        if len(normalized) > cls._MAX_NOTE_CHARS:
            raise ScriptingNoteError(
                f"version note exceeds the {cls._MAX_NOTE_CHARS} character limit"
            )
        return normalized or None

    @staticmethod
    def _atomic_replace(path: Path, content: bytes) -> None:
        mode = path.stat().st_mode & 0o777
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                os.fchmod(temporary_file.fileno(), mode)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _resolve_file_path(self, relative_path: str) -> tuple[Path, str]:
        parts = self._validate_relative_path(relative_path)
        candidate = self.root
        for part in parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise ScriptingPathError(
                    "symbolic links are not available in the scripting workspace"
                )

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ScriptingPathError(
                "script path must be inside workdir/scripts"
            ) from exc

        if resolved.suffix.lower() not in self._SUPPORTED_TYPES:
            raise ScriptingFileTypeError("only .py and .md files are supported")
        return resolved, "/".join(parts)

    def _validate_relative_path(self, relative_path: str) -> tuple[str, ...]:
        if not isinstance(relative_path, str) or not relative_path:
            raise ScriptingPathError("script path is required")
        if "\x00" in relative_path or "\\" in relative_path:
            raise ScriptingPathError("script path is invalid")
        if relative_path.startswith("/") or Path(relative_path).is_absolute():
            raise ScriptingPathError("absolute script paths are not allowed")

        parts = tuple(relative_path.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise ScriptingPathError("script path must be normalized")
        if any(self._is_excluded_name(part) for part in parts):
            raise ScriptingPathError("hidden and generated paths are not available")
        return parts

    def _scan_directory(self, directory: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        visible_children = [
            child
            for child in directory.iterdir()
            if not child.is_symlink() and not self._is_excluded_name(child.name)
        ]
        children = sorted(
            visible_children,
            key=lambda child: (
                not child.is_dir(),
                child.name.casefold(),
                child.name,
            ),
        )
        for child in children:
            relative = child.relative_to(self.root).as_posix()
            if child.is_dir():
                entries.append({
                    "type": "directory",
                    "name": child.name,
                    "path": relative,
                    "children": self._scan_directory(child),
                })
                continue
            language = self._SUPPORTED_TYPES.get(child.suffix.lower())
            if language is None or not child.is_file():
                continue
            entries.append({
                "type": "file",
                "name": child.name,
                "path": relative,
                "language": language,
                "size": child.stat().st_size,
            })
        return entries

    def _is_excluded_name(self, name: str) -> bool:
        return name.startswith(".") or name in self._EXCLUDED_NAMES
