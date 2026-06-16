from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from config import SessionSpec
from runtime import SessionPaths

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_MAIN = REPO_ROOT / "runner_service" / "main.py"


@dataclass
class RunnerProcessHandle:
    session_id: str
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

    async def start(self, spec: SessionSpec, paths: SessionPaths) -> None:
        if self.is_active(spec.id):
            return  # already running

        # Reap any finished handle / log file before re-spawning.
        await self._cleanup_handle(spec.id)

        paths.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(paths.log_path, "ab", buffering=0)

        args = [
            sys.executable, "-u", str(RUNNER_MAIN),
            "--session-id", spec.id,
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

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=log_fh, stderr=log_fh, cwd=str(REPO_ROOT),
        )
        handle = RunnerProcessHandle(
            session_id=spec.id, process=proc, status="starting",
            user_stopped=False, log_path=paths.log_path, log_fh=log_fh,
        )
        self.handles[spec.id] = handle
        handle.monitor_task = asyncio.create_task(self._monitor(handle))
        print(f"[supervisor] started runner for {spec.id} (pid={proc.pid})")
        await self._changed()

    async def _monitor(self, handle: RunnerProcessHandle) -> None:
        rc = await handle.process.wait()
        if handle.user_stopped or rc == 0:
            handle.status = "stopped"
        else:
            handle.status = "crashed"
            print(f"[supervisor] runner {handle.session_id} crashed (exit={rc})")
        try:
            handle.log_fh.close()
        except Exception:
            pass
        await self._changed()

    async def stop(self, session_id: str) -> None:
        handle = self.handles.get(session_id)
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
        print(f"[supervisor] stopped runner for {session_id}")

    async def restart(self, spec: SessionSpec, paths: SessionPaths) -> None:
        await self.stop(spec.id)
        await self.start(spec, paths)

    async def _cleanup_handle(self, session_id: str) -> None:
        handle = self.handles.pop(session_id, None)
        if handle is None:
            return
        if handle.monitor_task is not None and not handle.monitor_task.done():
            handle.monitor_task.cancel()
        try:
            handle.log_fh.close()
        except Exception:
            pass

    async def shutdown(self) -> None:
        for sid in list(self.handles.keys()):
            await self.stop(sid)


def _bool_arg(v) -> str:
    return "true" if bool(v) else "false"
