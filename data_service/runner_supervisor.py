from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from config import SessionSpec
from runtime import SessionPaths

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_MAIN = REPO_ROOT / "runner_service" / "main.py"
VERIFICATION_CONNECT_TIMEOUT_SECONDS = 30.0


@dataclass
class RunnerProcessHandle:
    session_id: str
    role: str
    process: asyncio.subprocess.Process
    status: str          # starting | stopped | crashed  (process-level)
    user_stopped: bool   # True when stop/restart asked for it (crash vs intentional)
    log_path: Path
    log_fh: object
    monitor_task: Optional[asyncio.Task] = None


class RunnerSupervisor:
    """Spawns / stops one runner_service subprocess per session and tracks
    process-level liveness. The 'running' vs 'starting' distinction (whether the
    runner's websocket actually connected) is resolved by the registry, which
    also knows runner_count per session."""

    def __init__(self, port: int, on_change: Callable[[], Awaitable[None]]) -> None:
        # Runner connects back to the hub locally regardless of bind host.
        self.port = port
        self._on_change = on_change
        self.handles: Dict[str, RunnerProcessHandle] = {}
        self.verification_handles: Dict[str, RunnerProcessHandle] = {}
        self._verification_targets: Dict[str, tuple[SessionSpec, SessionPaths]] = {}
        self._verification_desired: set[str] = set()
        self._verification_recovery_tasks: Dict[str, asyncio.Task] = {}
        self._verification_recovery_attempts: Dict[str, int] = {}
        self._verification_recovery_reasons: Dict[str, str] = {}
        self._verification_recovery_at: Dict[str, float] = {}
        self._verification_recovery_spawning: set[str] = set()
        self._verification_connect_timeout_tasks: Dict[str, asyncio.Task] = {}
        self._shutting_down = False
        self.verification_enabled = (
            os.environ.get("PYNEREAL_VERIFICATION_RUNNER", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        )

    async def _changed(self) -> None:
        try:
            await self._on_change()
        except Exception:
            pass

    def _ws_url(self, session_id: str) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/{session_id}"

    def is_active(self, session_id: str) -> bool:
        h = self.handles.get(session_id)
        return bool(h and h.process.returncode is None)

    def status(self, session_id: str) -> str:
        h = self.handles.get(session_id)
        if h is None:
            return "stopped"
        if h.process.returncode is None:
            return "starting"  # process up; registry upgrades to "running" if connected
        return h.status

    def verification_process_status(self, session_id: str) -> str:
        if not self.verification_enabled:
            return "disabled"
        recovery = self._verification_recovery_tasks.get(session_id)
        if recovery is not None and not recovery.done():
            return "recovering"
        handle = self.verification_handles.get(session_id)
        if handle is None:
            return "stopped"
        if handle.process.returncode is None:
            return "starting"
        return handle.status

    def verification_recovery_info(self, session_id: str) -> dict:
        return {
            "attempt": int(self._verification_recovery_attempts.get(session_id, 0)),
            "reason": self._verification_recovery_reasons.get(session_id),
            "next_retry_at": self._verification_recovery_at.get(session_id),
        }

    async def start(self, spec: SessionSpec, paths: SessionPaths) -> None:
        if not self.is_active(spec.id):
            # Reap any finished handle / log file before re-spawning.
            await self._cleanup_handle(spec.id, role="primary")
            await self._spawn(spec, paths, role="primary")

        if self.verification_enabled:
            self._verification_targets[spec.id] = (spec, paths)
            self._verification_desired.add(spec.id)
        if self.verification_enabled and not self._is_verification_active(spec.id):
            try:
                await self._cleanup_handle(spec.id, role="verification")
                await self._spawn(
                    spec,
                    self._verification_paths(paths),
                    role="verification",
                )
            except Exception as error:
                print(
                    f"[supervisor] verification runner {spec.id} failed to start: "
                    f"{type(error).__name__}: {error}"
                )
                self.request_verification_recovery(
                    spec.id,
                    reason=f"initial start failed: {type(error).__name__}: {error}",
                )

    async def _spawn(
        self,
        spec: SessionSpec,
        paths: SessionPaths,
        *,
        role: str,
    ) -> None:
        handles = self.handles if role == "primary" else self.verification_handles

        paths.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(paths.log_path, "ab", buffering=0)

        args = [
            sys.executable, "-u", str(RUNNER_MAIN),
            "--session-id", spec.id,
            "--role", role,
            "--data-service-ws", self._ws_url(spec.id),
            "--provider", spec.provider,
            "--exchange", spec.exchange,
            "--symbol", spec.symbol,
            "--timeframe", spec.timeframe,
            "--script-name", spec.script_name,
            "--plot-path", str(paths.plot_path),
            "--hash-path", str(paths.hash_path),
            "--webhook-enabled", _bool_arg(spec.webhook.get("enabled")),
            "--telegram-enabled", _bool_arg(spec.webhook.get("telegram_notification")),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=log_fh, stderr=log_fh, cwd=str(REPO_ROOT),
            )
        except Exception:
            log_fh.close()
            raise
        handle = RunnerProcessHandle(
            session_id=spec.id, role=role, process=proc, status="starting",
            user_stopped=False, log_path=paths.log_path, log_fh=log_fh,
        )
        handles[spec.id] = handle
        handle.monitor_task = asyncio.create_task(self._monitor(handle))
        if role == "verification":
            self._start_verification_connect_timeout(handle)
        print(
            f"[supervisor] started {role} runner for {spec.id} "
            f"(pid={proc.pid})"
        )
        await self._changed()

    @staticmethod
    def _verification_paths(paths: SessionPaths) -> SessionPaths:
        return SessionPaths(
            plot_path=paths.plot_path.with_name("verification_plot.csv"),
            hash_path=paths.hash_path.with_name("verification_script_hash.csv"),
            log_path=paths.log_path.with_name("verification_runner.log"),
        )

    def _is_verification_active(self, session_id: str) -> bool:
        handle = self.verification_handles.get(session_id)
        return bool(handle and handle.process.returncode is None)

    async def _monitor(self, handle: RunnerProcessHandle) -> None:
        rc = await handle.process.wait()
        if (
            handle.role == "verification"
            and self.verification_handles.get(handle.session_id) is handle
        ):
            self._cancel_verification_connect_timeout(handle.session_id)
        unexpected_exit = (
            not handle.user_stopped
            and (handle.role == "verification" or rc != 0)
        )
        if not unexpected_exit:
            handle.status = "stopped"
        else:
            handle.status = "crashed"
            print(
                f"[supervisor] {handle.role} runner {handle.session_id} "
                f"crashed (exit={rc})"
            )
        try:
            handle.log_fh.close()
        except Exception:
            pass
        if (
            unexpected_exit
            and handle.role == "verification"
            and handle.session_id in self._verification_desired
            and not self._shutting_down
        ):
            scheduled = self.request_verification_recovery(
                handle.session_id,
                reason=f"process exited with code {rc}",
            )
            if not scheduled:
                recovery = self._verification_recovery_tasks.get(handle.session_id)
                if recovery is not None and not recovery.done():
                    def retry_after_current(_task: asyncio.Task) -> None:
                        current = self.verification_handles.get(handle.session_id)
                        if current is handle and current.process.returncode is not None:
                            self.request_verification_recovery(
                                handle.session_id,
                                reason=f"process exited with code {rc}",
                            )

                    recovery.add_done_callback(retry_after_current)
        await self._changed()

    def _start_verification_connect_timeout(
        self,
        handle: RunnerProcessHandle,
    ) -> None:
        self._cancel_verification_connect_timeout(handle.session_id)
        self._verification_connect_timeout_tasks[handle.session_id] = asyncio.create_task(
            self._verification_connect_timeout(handle),
            name=f"verification-connect-timeout:{handle.session_id}",
        )

    def _cancel_verification_connect_timeout(self, session_id: str) -> None:
        task = self._verification_connect_timeout_tasks.pop(session_id, None)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _verification_connect_timeout(
        self,
        handle: RunnerProcessHandle,
    ) -> None:
        try:
            await asyncio.sleep(VERIFICATION_CONNECT_TIMEOUT_SECONDS)
            if (
                self.verification_handles.get(handle.session_id) is handle
                and handle.process.returncode is None
                and handle.session_id in self._verification_desired
            ):
                self.request_verification_recovery(
                    handle.session_id,
                    reason="verification runner connection timed out",
                )
        except asyncio.CancelledError:
            raise
        finally:
            if (
                self._verification_connect_timeout_tasks.get(handle.session_id)
                is asyncio.current_task()
            ):
                self._verification_connect_timeout_tasks.pop(handle.session_id, None)

    @staticmethod
    def _verification_recovery_delay(attempt: int) -> float:
        return float((2, 5, 10, 20, 30, 60)[min(max(attempt - 1, 0), 5)])

    def request_verification_recovery(
        self,
        session_id: str,
        *,
        reason: str,
        immediate: bool = False,
    ) -> bool:
        if (
            not self.verification_enabled
            or self._shutting_down
            or session_id not in self._verification_desired
            or session_id not in self._verification_targets
        ):
            return False
        current = self._verification_recovery_tasks.get(session_id)
        if current is not None and not current.done():
            if not immediate:
                return False
            current.cancel()

        attempt = self._verification_recovery_attempts.get(session_id, 0) + 1
        delay = 0.0 if immediate else self._verification_recovery_delay(attempt)
        self._verification_recovery_attempts[session_id] = attempt
        self._verification_recovery_reasons[session_id] = reason
        self._verification_recovery_at[session_id] = time.time() + delay
        task = asyncio.create_task(
            self._recover_verification(session_id, delay),
            name=f"verification-recovery:{session_id}",
        )
        self._verification_recovery_tasks[session_id] = task
        asyncio.create_task(self._changed())
        return True

    async def _recover_verification(self, session_id: str, delay: float) -> None:
        current_task = asyncio.current_task()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if (
                self._shutting_down
                or session_id not in self._verification_desired
                or not self.is_active(session_id)
            ):
                return
            spec, paths = self._verification_targets[session_id]
            self._verification_recovery_spawning.add(session_id)
            await self._stop_handle(session_id, role="verification")
            await self._cleanup_handle(session_id, role="verification")
            await self._spawn(
                spec,
                self._verification_paths(paths),
                role="verification",
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(
                f"[supervisor] verification runner {session_id} recovery failed: "
                f"{type(error).__name__}: {error}"
            )
            if session_id in self._verification_desired and not self._shutting_down:
                self._verification_recovery_tasks.pop(session_id, None)
                self.request_verification_recovery(
                    session_id,
                    reason=f"restart failed: {type(error).__name__}: {error}",
                )
        finally:
            self._verification_recovery_spawning.discard(session_id)
            if self._verification_recovery_tasks.get(session_id) is current_task:
                self._verification_recovery_tasks.pop(session_id, None)
                self._verification_recovery_at.pop(session_id, None)
            await self._changed()

    def mark_verification_connected(self, session_id: str) -> None:
        self._cancel_verification_connect_timeout(session_id)
        task = self._verification_recovery_tasks.get(session_id)
        if (
            task is not None
            and not task.done()
            and session_id not in self._verification_recovery_spawning
        ):
            task.cancel()
            self._verification_recovery_tasks.pop(session_id, None)
            self._verification_recovery_at.pop(session_id, None)

    def mark_verification_disconnected(self, session_id: str) -> None:
        handle = self.verification_handles.get(session_id)
        if (
            handle is None
            or handle.process.returncode is not None
            or session_id not in self._verification_desired
        ):
            return
        # The runner's reconnect loop keeps its in-memory strategy state. Only
        # replace the process if it cannot restore the local websocket channel.
        self._start_verification_connect_timeout(handle)

    def mark_verification_ready(self, session_id: str) -> None:
        self.mark_verification_connected(session_id)
        self._verification_recovery_attempts.pop(session_id, None)
        self._verification_recovery_reasons.pop(session_id, None)

    async def restart_verification(
        self,
        spec: SessionSpec,
        paths: SessionPaths,
        *,
        reason: str = "manual restart",
    ) -> None:
        if not self.verification_enabled:
            raise RuntimeError("verification runner is disabled")
        if not self.is_active(spec.id):
            raise RuntimeError("primary runner is not running")
        self._verification_targets[spec.id] = (spec, paths)
        self._verification_desired.add(spec.id)
        self.request_verification_recovery(
            spec.id,
            reason=reason,
            immediate=True,
        )

    async def stop(self, session_id: str) -> None:
        self._verification_desired.discard(session_id)
        recovery = self._verification_recovery_tasks.pop(session_id, None)
        if recovery is not None and not recovery.done():
            recovery.cancel()
            await asyncio.gather(recovery, return_exceptions=True)
        self._verification_recovery_at.pop(session_id, None)
        self._verification_recovery_attempts.pop(session_id, None)
        self._verification_recovery_reasons.pop(session_id, None)
        self._verification_recovery_spawning.discard(session_id)
        self._cancel_verification_connect_timeout(session_id)
        self._verification_targets.pop(session_id, None)
        await asyncio.gather(
            self._stop_handle(session_id, role="verification"),
            self._stop_handle(session_id, role="primary"),
        )

    async def _stop_handle(self, session_id: str, *, role: str) -> None:
        handles = self.handles if role == "primary" else self.verification_handles
        handle = handles.get(session_id)
        if handle is None:
            return
        handle.user_stopped = True
        proc = handle.process
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except Exception:
                    pass
        # Let the monitor task finish updating status.
        if handle.monitor_task is not None:
            try:
                await handle.monitor_task
            except Exception:
                pass
        print(f"[supervisor] stopped {role} runner for {session_id}")

    async def restart(self, spec: SessionSpec, paths: SessionPaths) -> None:
        await self.stop(spec.id)
        await self.start(spec, paths)

    async def _cleanup_handle(self, session_id: str, *, role: str) -> None:
        handles = self.handles if role == "primary" else self.verification_handles
        handle = handles.pop(session_id, None)
        if handle is None:
            return
        if handle.monitor_task is not None and not handle.monitor_task.done():
            handle.monitor_task.cancel()
        try:
            handle.log_fh.close()
        except Exception:
            pass

    async def shutdown(self) -> None:
        self._shutting_down = True
        session_ids = set(self.handles) | set(self.verification_handles)
        for sid in list(session_ids):
            await self.stop(sid)
        recovery_tasks = list(self._verification_recovery_tasks.values())
        self._verification_recovery_tasks.clear()
        for task in recovery_tasks:
            task.cancel()
        if recovery_tasks:
            await asyncio.gather(*recovery_tasks, return_exceptions=True)
        connect_tasks = list(self._verification_connect_timeout_tasks.values())
        self._verification_connect_timeout_tasks.clear()
        for task in connect_tasks:
            task.cancel()
        if connect_tasks:
            await asyncio.gather(*connect_tasks, return_exceptions=True)


def _bool_arg(v) -> str:
    return "true" if bool(v) else "false"
