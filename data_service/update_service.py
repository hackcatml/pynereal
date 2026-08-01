from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


class UpdateServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def read_update_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def write_update_state(path: Path, **values: Any) -> None:
    state = read_update_state(path)
    state.update(values)
    state["updated_at"] = int(time.time())
    _write_json_atomic(path, state)


class UpdateService:
    _CONFIRM_TTL_SECONDS = 120

    def __init__(
        self,
        repo_root: Path,
        registry: Any,
        port: int,
        ai_enabled: Callable[[], bool],
        request_shutdown: Callable[[], None],
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.registry = registry
        self.port = port
        self.ai_enabled = ai_enabled
        self.request_shutdown = request_shutdown
        self.state_path = self.repo_root / "workdir" / "config" / "update_state.json"
        self.request_path = self.repo_root / "workdir" / "config" / "update_request.json"
        self._lock = asyncio.Lock()
        self._confirmation: dict[str, Any] | None = None

    async def status(self) -> dict[str, Any]:
        repo = await asyncio.to_thread(self._repo_info)
        self._update_in_progress()
        return {
            **repo,
            "update": read_update_state(self.state_path),
        }

    async def check(self) -> dict[str, Any]:
        async with self._lock:
            if self._update_in_progress():
                raise UpdateServiceError("an update is already in progress", 409)
            try:
                result = await asyncio.to_thread(self._check_sync, True)
            except (OSError, subprocess.SubprocessError) as exc:
                raise UpdateServiceError(f"update check failed: {exc}", 503) from exc

            self._confirmation = None
            if result["available"] and result["can_update"]:
                token = secrets.token_urlsafe(24)
                self._confirmation = {
                    "token": token,
                    "head": result["commit"],
                    "target": result["target_commit"],
                    "expires_at": time.monotonic() + self._CONFIRM_TTL_SECONDS,
                }
                result["confirmation_token"] = token
            return result

    async def start(self, confirmation_token: str) -> dict[str, Any]:
        async with self._lock:
            confirmation = self._confirmation
            self._confirmation = None
            if (
                not confirmation
                or not secrets.compare_digest(
                    str(confirmation.get("token", "")), confirmation_token
                )
                or time.monotonic() > float(confirmation.get("expires_at", 0))
            ):
                raise UpdateServiceError("update confirmation expired; check again", 409)
            if self._update_in_progress():
                raise UpdateServiceError("an update is already in progress", 409)

            check = await asyncio.to_thread(self._check_sync, False)
            if not check["available"]:
                raise UpdateServiceError("no update is available", 409)
            if not check["can_update"]:
                raise UpdateServiceError(check["blocked_reason"], 409)
            if (
                check["commit"] != confirmation["head"]
                or check["target_commit"] != confirmation["target"]
            ):
                raise UpdateServiceError("repository changed; check for updates again", 409)

            active_runner_ids = [
                session_id
                for session_id in self.registry.sessions
                if self.registry.supervisor.is_active(session_id)
            ]
            request = {
                "repo_root": str(self.repo_root),
                "parent_pid": os.getpid(),
                "python": sys.executable,
                "data_service_main": str(self.repo_root / "data_service" / "main.py"),
                "port": self.port,
                "branch": check["branch"],
                "from_commit": check["commit"],
                "target_commit": check["target_commit"],
                "active_runner_ids": active_runner_ids,
                "ai_enabled": bool(self.ai_enabled()),
            }
            _write_json_atomic(self.request_path, request)
            write_update_state(
                self.state_path,
                status="stopping",
                branch=check["branch"],
                from_commit=check["commit"],
                target_commit=check["target_commit"],
                message="Stopping runners and data service",
                error="",
                owner_pid=os.getpid(),
            )

            asyncio.get_running_loop().call_later(0.35, self.request_shutdown)
            return {
                "ok": True,
                "active_runners": len(active_runner_ids),
                "target_commit": check["target_commit"],
            }

    def _update_in_progress(self) -> bool:
        state = read_update_state(self.state_path)
        if state.get("status") not in {
            "stopping",
            "updating",
            "restarting",
            "resuming_runners",
        }:
            return False
        try:
            owner_pid = int(state.get("owner_pid", 0))
            if owner_pid <= 0:
                return True
            os.kill(owner_pid, 0)
            return True
        except (ProcessLookupError, ValueError, TypeError):
            write_update_state(
                self.state_path,
                status="failed",
                message="The update process stopped unexpectedly.",
                error="update process is no longer running",
            )
            return False
        except PermissionError:
            return True

    def pending_restart(self) -> bool:
        state = read_update_state(self.state_path)
        try:
            owner_pid = int(state.get("owner_pid", 0))
        except (TypeError, ValueError):
            return False
        return state.get("status") == "restarting" and owner_pid == os.getpid()

    def apply_and_restart(self) -> None:
        request = read_update_state(self.request_path)
        update_error = ""
        if not request:
            update_error = "update request data is missing"
            write_update_state(
                self.state_path,
                status="failed",
                message="Update request data is missing.",
                error=update_error,
            )
        else:
            try:
                write_update_state(
                    self.state_path,
                    status="updating",
                    message="Running git pull",
                )
                output = self._git("pull", "--ff-only", timeout=180)
                if output:
                    print(f"[update] {output}")
                write_update_state(
                    self.state_path,
                    status="updating",
                    message="Clearing Python caches",
                )
                removed = self._remove_bytecode_caches()
                print(f"[update] removed {removed} __pycache__ directories")
            except Exception as exc:
                update_error = str(exc)
                print(f"[update] update failed: {exc}", file=sys.stderr)

        write_update_state(
            self.state_path,
            status="restarting",
            message="Restarting data service",
            error=update_error,
        )
        env = os.environ.copy()
        env["PYNEREAL_UPDATE_AI_ENABLED"] = "1" if request.get("ai_enabled") else "0"
        python = str(request.get("python") or sys.executable)
        main_path = str(
            request.get("data_service_main")
            or self.repo_root / "data_service" / "main.py"
        )
        try:
            os.execve(python, [python, "-u", main_path], env)
        except OSError as exc:
            write_update_state(
                self.state_path,
                status="failed",
                message="Data service could not be restarted.",
                error=f"{update_error}; {exc}" if update_error else str(exc),
            )
            raise

    async def finish_restart(self, timeout: float = 900) -> None:
        request = read_update_state(self.request_path)
        state = read_update_state(self.state_path)
        pending = {
            str(item)
            for item in request.get("active_runner_ids", [])
            if isinstance(item, str) and item
        }
        update_error = str(state.get("error") or "")
        write_update_state(
            self.state_path,
            status="resuming_runners",
            message="Waiting for market data and resuming runners",
        )
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            for session_id in list(pending):
                session = self.registry.get(session_id)
                if session is None:
                    continue
                if self.registry.supervisor.is_active(session_id):
                    pending.remove(session_id)
                    continue
                if not session.feed.history_ready():
                    continue
                try:
                    await self.registry.start_runner(session_id)
                except Exception:
                    continue
                pending.remove(session_id)
            if pending:
                await asyncio.sleep(1)

        commit = self._git("rev-parse", "--short=7", "HEAD")
        if update_error:
            write_update_state(
                self.state_path,
                status="failed",
                message="Update failed; the previous version was restarted.",
                error=update_error,
                commit=commit,
                pending_runner_ids=sorted(pending),
            )
        elif pending:
            write_update_state(
                self.state_path,
                status="failed",
                message="Updated, but some runners could not be resumed.",
                error=f"runners not resumed: {', '.join(sorted(pending))}",
                commit=commit,
                pending_runner_ids=sorted(pending),
            )
        else:
            write_update_state(
                self.state_path,
                status="completed",
                message="Update completed.",
                error="",
                commit=commit,
                pending_runner_ids=[],
            )

    def _remove_bytecode_caches(self) -> int:
        removed = 0
        for root, dirs, _ in os.walk(self.repo_root, topdown=True):
            for name in [item for item in dirs if item == "__pycache__"]:
                shutil.rmtree(Path(root) / name, ignore_errors=True)
                dirs.remove(name)
                removed += 1
        return removed

    def _git(self, *args: str, timeout: float = 60) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise subprocess.SubprocessError(detail or f"git {' '.join(args)} failed")
        return completed.stdout.strip()

    def _repo_info(self, ref: str = "HEAD") -> dict[str, Any]:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        commit = self._git("rev-parse", "--short=7", ref)
        version = ""
        if branch == "main":
            try:
                version = self._git(
                    "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", ref
                )
            except subprocess.SubprocessError:
                pass
        display = f"{version} · {commit}" if version else commit
        return {
            "branch": branch,
            "commit": commit,
            "version": version,
            "display": display,
        }

    def _check_sync(self, fetch: bool) -> dict[str, Any]:
        info = self._repo_info()
        try:
            upstream = self._git(
                "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
            )
        except subprocess.SubprocessError:
            return {
                **info,
                "upstream": "",
                "available": False,
                "can_update": False,
                "blocked_reason": "current branch has no tracking branch",
                "target_commit": info["commit"],
                "target_version": info["version"],
            }

        if fetch:
            self._git("fetch", "--quiet", timeout=120)

        counts = self._git("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        ahead_text, behind_text = counts.split()
        ahead, behind = int(ahead_text), int(behind_text)
        target_commit = self._git("rev-parse", "--short=7", "@{upstream}")
        target_version = ""
        if info["branch"] == "main":
            try:
                target_version = self._git(
                    "describe",
                    "--tags",
                    "--abbrev=0",
                    "--match",
                    "v[0-9]*",
                    "@{upstream}",
                )
            except subprocess.SubprocessError:
                pass

        available = behind > 0
        blocked_reason = ""
        if available and ahead > 0:
            blocked_reason = "local and remote branches have diverged; update manually"
        elif available:
            incoming = set(
                filter(None, self._git("diff", "--name-only", "HEAD..@{upstream}").splitlines())
            )
            local = set(filter(None, self._git("diff", "--name-only", "HEAD").splitlines()))
            local.update(
                filter(
                    None,
                    self._git("ls-files", "--others", "--exclude-standard").splitlines(),
                )
            )
            conflicts = sorted(incoming & local)
            if conflicts:
                preview = ", ".join(conflicts[:3])
                if len(conflicts) > 3:
                    preview += f" and {len(conflicts) - 3} more"
                blocked_reason = f"local changes overlap update files: {preview}"

        return {
            **info,
            "upstream": upstream,
            "available": available,
            "can_update": available and not blocked_reason,
            "blocked_reason": blocked_reason,
            "target_commit": target_commit,
            "target_version": target_version,
            "ahead": ahead,
            "behind": behind,
        }
