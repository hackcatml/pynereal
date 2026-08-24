from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
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
    _PYTHON_DEPENDENCY_FILES = frozenset({
        "pyproject.toml",
        "requirements-runtime.txt",
    })
    _HOST_TOOL_FILES = frozenset({"setup.sh"})
    _DEPENDENCY_FILES = _PYTHON_DEPENDENCY_FILES | _HOST_TOOL_FILES

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
        self._live_update_task: asyncio.Task[None] | None = None

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
                    "branch": result["branch"],
                    "head_sha": result["commit_sha"],
                    "target_sha": result["target_sha"],
                    "changed_files": tuple(result["changed_files"]),
                    "restart_required": result["restart_required"],
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

            try:
                check = await asyncio.to_thread(
                    self._validate_confirmed_target,
                    str(confirmation["branch"]),
                    str(confirmation["head_sha"]),
                    str(confirmation["target_sha"]),
                    tuple(confirmation["changed_files"]),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise UpdateServiceError(f"update validation failed: {exc}", 409) from exc

            restart_required = bool(confirmation["restart_required"])
            if check["restart_required"] != restart_required:
                raise UpdateServiceError("update contents changed; check again", 409)

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
                "from_sha": check["commit_sha"],
                "target_commit": check["target_commit"],
                "target_sha": check["target_sha"],
                "changed_files": check["changed_files"],
                "restart_required": restart_required,
                "active_runner_ids": active_runner_ids,
                "ai_enabled": bool(self.ai_enabled()),
            }
            _write_json_atomic(self.request_path, request)
            if not restart_required:
                write_update_state(
                    self.state_path,
                    status="updating",
                    branch=check["branch"],
                    from_commit=check["commit"],
                    target_commit=check["target_commit"],
                    target_sha=check["target_sha"],
                    restart_required=False,
                    message="Applying frontend update",
                    error="",
                    owner_pid=os.getpid(),
                )
                self._live_update_task = asyncio.create_task(
                    self._apply_live_update(request)
                )
                return {
                    "ok": True,
                    "active_runners": len(active_runner_ids),
                    "target_commit": check["target_commit"],
                    "restart_required": False,
                }

            write_update_state(
                self.state_path,
                status="stopping",
                branch=check["branch"],
                from_commit=check["commit"],
                target_commit=check["target_commit"],
                target_sha=check["target_sha"],
                restart_required=True,
                message="Stopping runners and data service",
                error="",
                owner_pid=os.getpid(),
            )

            asyncio.get_running_loop().call_later(0.35, self.request_shutdown)
            return {
                "ok": True,
                "active_runners": len(active_runner_ids),
                "target_commit": check["target_commit"],
                "restart_required": True,
            }

    async def _apply_live_update(self, request: dict[str, Any]) -> None:
        try:
            output = await asyncio.to_thread(
                self._merge_target,
                str(request["target_sha"]),
            )
            if output:
                print(f"[update] {output}")
            commit = await asyncio.to_thread(
                self._git, "rev-parse", "--short=7", "HEAD"
            )
            write_update_state(
                self.state_path,
                status="completed",
                message="Update completed.",
                error="",
                commit=commit,
                restart_required=False,
                pending_runner_ids=[],
            )
        except Exception as exc:
            print(f"[update] frontend update failed: {exc}", file=sys.stderr)
            write_update_state(
                self.state_path,
                status="failed",
                message="Frontend update failed.",
                error=str(exc),
                restart_required=False,
            )
        finally:
            self._live_update_task = None

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
                if self._dependency_sync_required(request.get("changed_files", [])):
                    write_update_state(
                        self.state_path,
                        status="updating",
                        message="Installing dependencies",
                    )
                    self._install_target_dependencies(str(request["target_sha"]))
                    request["dependencies_synced"] = True
                    _write_json_atomic(self.request_path, request)
                write_update_state(
                    self.state_path,
                    status="updating",
                    message="Applying confirmed update",
                )
                output = self._merge_target(str(request["target_sha"]))
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
        self._exec_data_service(request, update_error)

    def _exec_data_service(
        self,
        request: dict[str, Any],
        update_error: str = "",
    ) -> None:
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

    def sync_dependencies_after_legacy_update(self) -> bool:
        """Handle the first update performed by an older updater implementation."""
        if not self.pending_restart():
            return False
        request = read_update_state(self.request_path)
        changed_files = request.get("changed_files", [])
        if request.get("dependencies_synced") or not self._dependency_sync_required(
            changed_files
        ):
            return False
        python_dependencies_changed = self._python_dependency_sync_required(
            changed_files
        )
        try:
            write_update_state(
                self.state_path,
                status="restarting",
                message="Installing dependencies",
            )
            if python_dependencies_changed:
                self._install_current_dependencies()
            else:
                self._install_ai_host_tools()
            request["dependencies_synced"] = True
            _write_json_atomic(self.request_path, request)
            write_update_state(
                self.state_path,
                status="restarting",
                message=(
                    "Restarting data service"
                    if python_dependencies_changed
                    else "Starting data service"
                ),
            )
        except Exception as exc:
            write_update_state(
                self.state_path,
                status="failed",
                message="Dependency installation failed.",
                error=str(exc),
            )
            raise
        return python_dependencies_changed

    def restart_after_legacy_dependency_sync(self) -> None:
        request = read_update_state(self.request_path)
        self._exec_data_service(request)

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

    @classmethod
    def _dependency_sync_required(cls, changed_files: Any) -> bool:
        return any(str(path) in cls._DEPENDENCY_FILES for path in changed_files)

    @classmethod
    def _python_dependency_sync_required(cls, changed_files: Any) -> bool:
        return any(
            str(path) in cls._PYTHON_DEPENDENCY_FILES for path in changed_files
        )

    @staticmethod
    def _runtime_requirements(text: str) -> list[str]:
        return [
            line
            for raw_line in text.splitlines()
            if (line := raw_line.strip()) and not line.startswith("#")
        ]

    @staticmethod
    def _project_requirements(text: str) -> list[str]:
        project = tomllib.loads(text).get("project", {})
        optional = project.get("optional-dependencies", {})
        return [str(item) for item in optional.get("all", [])]

    @staticmethod
    def _deduplicate_requirements(requirements: list[str]) -> list[str]:
        return list(dict.fromkeys(requirements))

    def _install_requirements(self, requirements: list[str]) -> None:
        requirements = self._deduplicate_requirements(requirements)
        if not requirements:
            return
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt") as handle:
            handle.write("\n".join(requirements) + "\n")
            handle.flush()
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", handle.name],
                cwd=self.repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        if completed.stdout.strip():
            print(f"[update] {completed.stdout.strip()}")
        if completed.returncode != 0:
            detail = completed.stdout.strip()
            if len(detail) > 4000:
                detail = detail[-4000:]
            raise subprocess.SubprocessError(
                detail
                or f"Python dependency installation failed with exit code {completed.returncode}"
            )

    def _install_ai_host_tools(self) -> None:
        pass

    def _install_target_dependencies(self, target_sha: str) -> None:
        requirements = self._project_requirements(
            self._git("show", f"{target_sha}:pyproject.toml")
        )
        requirements.extend(
            self._runtime_requirements(
                self._git("show", f"{target_sha}:requirements-runtime.txt")
            )
        )
        self._install_requirements(requirements)
        self._install_ai_host_tools()

    def _install_current_dependencies(self) -> None:
        requirements = self._project_requirements(
            (self.repo_root / "pyproject.toml").read_text(encoding="utf-8")
        )
        requirements.extend(
            self._runtime_requirements(
                (self.repo_root / "requirements-runtime.txt").read_text(encoding="utf-8")
            )
        )
        self._install_requirements(requirements)
        self._install_ai_host_tools()

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

    def _merge_target(self, target_sha: str) -> str:
        if not target_sha:
            raise subprocess.SubprocessError("confirmed target commit is missing")
        return self._git("merge", "--ff-only", target_sha, timeout=180)

    def _repo_info(self, ref: str = "HEAD") -> dict[str, Any]:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        commit_sha = self._git("rev-parse", ref)
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
            "commit_sha": commit_sha,
            "version": version,
            "display": display,
        }

    def _changed_files(self, start_sha: str, target_sha: str) -> list[str]:
        return sorted(
            filter(
                None,
                self._git("diff", "--name-only", f"{start_sha}..{target_sha}").splitlines(),
            )
        )

    @staticmethod
    def _restart_required(changed_files: list[str] | tuple[str, ...]) -> bool:
        return any(
            not path.startswith("data_service/templates/")
            for path in changed_files
        )

    def _local_conflicts(self, changed_files: list[str] | tuple[str, ...]) -> list[str]:
        local = set(filter(None, self._git("diff", "--name-only", "HEAD").splitlines()))
        local.update(
            filter(
                None,
                self._git("ls-files", "--others", "--exclude-standard").splitlines(),
            )
        )
        return sorted(set(changed_files) & local)

    @staticmethod
    def _conflict_message(conflicts: list[str]) -> str:
        preview = ", ".join(conflicts[:3])
        if len(conflicts) > 3:
            preview += f" and {len(conflicts) - 3} more"
        return f"local changes overlap update files: {preview}"

    def _validate_confirmed_target(
        self,
        branch: str,
        head_sha: str,
        target_sha: str,
        expected_changed_files: tuple[str, ...],
    ) -> dict[str, Any]:
        current = self._repo_info()
        if current["branch"] != branch or current["commit_sha"] != head_sha:
            raise subprocess.SubprocessError("repository changed; check for updates again")
        self._git("cat-file", "-e", f"{target_sha}^{{commit}}")
        self._git("merge-base", "--is-ancestor", head_sha, target_sha)
        changed_files = self._changed_files(head_sha, target_sha)
        if tuple(changed_files) != expected_changed_files:
            raise subprocess.SubprocessError("update contents changed; check again")
        conflicts = self._local_conflicts(changed_files)
        if conflicts:
            raise subprocess.SubprocessError(self._conflict_message(conflicts))
        return {
            **current,
            "target_sha": target_sha,
            "target_commit": self._git("rev-parse", "--short=7", target_sha),
            "changed_files": changed_files,
            "restart_required": self._restart_required(changed_files),
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
                "target_sha": info["commit_sha"],
                "target_version": info["version"],
                "changed_files": [],
                "restart_required": False,
            }

        if fetch:
            self._git("fetch", "--quiet", timeout=120)

        counts = self._git("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        ahead_text, behind_text = counts.split()
        ahead, behind = int(ahead_text), int(behind_text)
        target_sha = self._git("rev-parse", "@{upstream}")
        target_commit = self._git("rev-parse", "--short=7", target_sha)
        target_version = ""
        if info["branch"] == "main":
            try:
                target_version = self._git(
                    "describe",
                    "--tags",
                    "--abbrev=0",
                    "--match",
                    "v[0-9]*",
                    target_sha,
                )
            except subprocess.SubprocessError:
                pass

        available = behind > 0
        blocked_reason = ""
        changed_files: list[str] = []
        if available and ahead > 0:
            blocked_reason = "local and remote branches have diverged; update manually"
        elif available:
            changed_files = self._changed_files(info["commit_sha"], target_sha)
            conflicts = self._local_conflicts(changed_files)
            if conflicts:
                blocked_reason = self._conflict_message(conflicts)

        return {
            **info,
            "upstream": upstream,
            "available": available,
            "can_update": available and not blocked_reason,
            "blocked_reason": blocked_reason,
            "target_commit": target_commit,
            "target_sha": target_sha,
            "target_version": target_version,
            "changed_files": changed_files,
            "restart_required": self._restart_required(changed_files),
            "ahead": ahead,
            "behind": behind,
        }
