from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any


_EDIT_ROOTS = (
    Path("workdir"),
    Path("modules"),
    Path("data_service/templates"),
)
_TMP_ROOT = Path("tmp")
_MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_REPLACEMENTS = 50


class FileToolError(ValueError):
    """A file operation rejected by the dashboard AI policy."""


class RestrictedFileTools:
    """Server-side file tools that enforce the dashboard AI write policy."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.edit_roots = tuple(
            (self.project_root / relative_path).resolve()
            for relative_path in _EDIT_ROOTS
        )
        self.tmp_root = (self.project_root / _TMP_ROOT).resolve()

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "edit_existing_file",
                "description": (
                    "Modify one existing UTF-8 regular file under workdir/, modules/, or "
                    "data_service/templates/ using exact text replacements. The path must be "
                    "repository-relative. All replacements are validated before the file is written."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Repository-relative path to an existing file.",
                        },
                        "replacements": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": _MAX_REPLACEMENTS,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "old_text": {"type": "string", "minLength": 1},
                                    "new_text": {"type": "string"},
                                    "expected_occurrences": {
                                        "type": "integer",
                                        "minimum": 1,
                                    },
                                },
                                "required": [
                                    "old_text",
                                    "new_text",
                                    "expected_occurrences",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["path", "replacements"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "write_tmp_file",
                "description": (
                    "Create or overwrite one UTF-8 file under the repository tmp/ directory. "
                    "The path must be relative to tmp/. Parent directories are created only "
                    "inside tmp/."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to the repository tmp/ directory.",
                        },
                        "content": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "default": False,
                        },
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        ]

    def handle_server_request(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method != "item/tool/call":
            return {}

        try:
            if not isinstance(params, dict):
                raise FileToolError("Tool call parameters must be an object")
            tool = params.get("tool")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                raise FileToolError("Tool arguments must be an object")
            if tool == "edit_existing_file":
                result = self.edit_existing_file(arguments)
            elif tool == "write_tmp_file":
                result = self.write_tmp_file(arguments)
            else:
                raise FileToolError(f"Unknown file tool: {tool!r}")
        except (FileToolError, OSError, UnicodeError) as exc:
            return self._tool_response(False, {"error": str(exc)})
        except Exception as exc:
            print(f"[ai] file tool internal error: {type(exc).__name__}: {exc}")
            return self._tool_response(False, {"error": "File tool failed unexpectedly"})

        print(f"[ai] file tool changed {result['path']}")
        return self._tool_response(True, result)

    def edit_existing_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(arguments, {"path", "replacements"}, label="arguments")
        path = self._required_string(arguments, "path")
        replacements = arguments.get("replacements")
        if not isinstance(replacements, list) or not replacements:
            raise FileToolError("replacements must be a non-empty array")
        if len(replacements) > _MAX_REPLACEMENTS:
            raise FileToolError(f"At most {_MAX_REPLACEMENTS} replacements are allowed")

        target = self._editable_file(path)
        original = self._read_regular_file(target)
        updated = original
        applied = 0
        for index, replacement in enumerate(replacements, start=1):
            if not isinstance(replacement, dict):
                raise FileToolError(f"Replacement {index} must be an object")
            self._validate_keys(
                replacement,
                {"old_text", "new_text", "expected_occurrences"},
                label=f"replacement {index}",
            )
            old_text = self._required_string(replacement, "old_text")
            new_text = replacement.get("new_text")
            expected = replacement.get("expected_occurrences")
            if not isinstance(new_text, str):
                raise FileToolError(f"Replacement {index} new_text must be a string")
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
                raise FileToolError(
                    f"Replacement {index} expected_occurrences must be a positive integer"
                )
            if old_text == new_text:
                raise FileToolError(f"Replacement {index} does not change the file")

            occurrences = updated.count(old_text)
            if occurrences != expected:
                raise FileToolError(
                    f"Replacement {index} expected {expected} occurrence(s), found {occurrences}"
                )
            updated = updated.replace(old_text, new_text)
            applied += occurrences

        updated_bytes = updated.encode("utf-8")
        if len(updated_bytes) > _MAX_FILE_BYTES:
            raise FileToolError(f"Edited file exceeds {_MAX_FILE_BYTES} bytes")

        self._write_existing_regular_file(target, updated)
        return {
            "path": target.relative_to(self.project_root).as_posix(),
            "replacements_applied": applied,
            "before_sha256": self._sha256(original.encode("utf-8")),
            "after_sha256": self._sha256(updated_bytes),
        }

    def write_tmp_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(arguments, {"path", "content", "overwrite"}, label="arguments")
        relative_path = self._required_string(arguments, "path")
        content = arguments.get("content")
        overwrite = arguments.get("overwrite", False)
        if not isinstance(content, str):
            raise FileToolError("content must be a string")
        if not isinstance(overwrite, bool):
            raise FileToolError("overwrite must be a boolean")

        content_bytes = content.encode("utf-8")
        if len(content_bytes) > _MAX_FILE_BYTES:
            raise FileToolError(f"Temporary file exceeds {_MAX_FILE_BYTES} bytes")

        target = self._tmp_file(relative_path)
        self._ensure_no_symlink_components(target.parent, allow_missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_no_symlink_components(target.parent, allow_missing=False)

        if target.exists() and not overwrite:
            raise FileToolError(f"Temporary file already exists: {relative_path}")
        if target.is_symlink():
            raise FileToolError("Symbolic links are not allowed")
        if target.exists() and not target.is_file():
            raise FileToolError("Temporary path is not a regular file")

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if not overwrite:
            flags |= os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags, 0o600)
        except FileExistsError as exc:
            raise FileToolError(f"Temporary file already exists: {relative_path}") from exc
        with os.fdopen(fd, "wb") as file:
            file.write(content_bytes)

        return {
            "path": target.relative_to(self.project_root).as_posix(),
            "bytes": len(content_bytes),
            "sha256": self._sha256(content_bytes),
        }

    def _editable_file(self, path: str) -> Path:
        relative = self._repository_relative_path(path)
        target = self.project_root / relative
        if target.is_symlink():
            raise FileToolError("Symbolic links are not allowed")
        if not target.exists():
            raise FileToolError("The edit target must already exist")
        resolved = target.resolve(strict=False)
        if not any(resolved.is_relative_to(root) for root in self.edit_roots):
            raise FileToolError(
                "Existing files may only be edited under workdir/, modules/, "
                "or data_service/templates/"
            )
        self._ensure_no_symlink_components(target, allow_missing=False)
        if not target.is_file():
            raise FileToolError("The edit target must be a regular file, not a symbolic link")
        return target

    def _tmp_file(self, path: str) -> Path:
        relative = self._relative_path(path, label="tmp path")
        target = self.tmp_root / relative
        if not target.resolve(strict=False).is_relative_to(self.tmp_root):
            raise FileToolError("Temporary path escapes tmp/")
        return target

    def _repository_relative_path(self, path: str) -> Path:
        return self._relative_path(path, label="repository path")

    @staticmethod
    def _relative_path(path: str, *, label: str) -> Path:
        if "\x00" in path:
            raise FileToolError(f"{label} may not contain null bytes")
        value = Path(path)
        if not path.strip() or value.is_absolute():
            raise FileToolError(f"{label} must be a non-empty relative path")
        if any(part in {"", ".", ".."} for part in value.parts):
            raise FileToolError(f"{label} may not contain '.', '..', or empty components")
        return value

    def _ensure_no_symlink_components(self, path: Path, *, allow_missing: bool) -> None:
        try:
            relative = path.relative_to(self.project_root)
        except ValueError as exc:
            raise FileToolError("Path escapes the repository") from exc

        current = self.project_root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise FileToolError("Symbolic links are not allowed in file tool paths")
            if not current.exists():
                if allow_missing:
                    continue
                raise FileToolError(f"Path component does not exist: {part}")

    @staticmethod
    def _required_string(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value:
            raise FileToolError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _validate_keys(arguments: dict[str, Any], allowed: set[str], *, label: str) -> None:
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            raise FileToolError(f"Unexpected {label} field(s): {', '.join(unexpected)}")

    @staticmethod
    def _read_regular_file(path: Path) -> str:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise FileToolError("The edit target must be a regular file")
            if file_stat.st_size > _MAX_FILE_BYTES:
                raise FileToolError(f"The edit target exceeds {_MAX_FILE_BYTES} bytes")
            with os.fdopen(fd, "r", encoding="utf-8", newline="") as file:
                fd = -1
                return file.read()
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _write_existing_regular_file(path: Path, content: str) -> None:
        flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise FileToolError("The edit target must remain a regular file")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as file:
                fd = -1
                file.write(content)
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _tool_response(success: bool, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": success,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ],
        }
