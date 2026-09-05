from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable

from pynecore.core.ohlcv_file import OHLCVReader
from pynecore.core.syminfo import SymInfo
from pynecore.providers.ccxt import CCXTProvider

from scripting_validation import ScriptingValidator
from scripting_backtest_summary import read_strategy_summary


_ACTIVE_STATUSES = {"queued", "preparing", "running", "stopping"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
_BACKTEST_DATA_EXCHANGES = ("binance", "bitget", "bybit", "okx", "hyperliquid")
_DOWNLOAD_SOURCE_SECTION = "pynereal_download"
_MAX_CONCURRENT_BACKTESTS = 10
_MAX_BACKTEST_VARIANTS = 1000


class ScriptingBacktestError(Exception):
    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(slots=True)
class BacktestJob:
    id: str
    script_path: str
    script_revision: str
    data_path: str
    time_from: int
    time_to: int
    status: str
    created_at: int
    run_key: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    started_at: int | None = None
    finished_at: int | None = None
    pid: int | None = None
    exit_code: int | None = None
    error: str | None = None
    actual_time_from: int | None = None
    actual_time_to: int | None = None
    summary: dict[str, Any] | None = None
    stop_requested: bool = False
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    monitor_task: asyncio.Task[None] | None = field(default=None, repr=False)


class ScriptingBacktestManager:
    def __init__(
        self,
        repo_root: Path,
        *,
        registry: Any = None,
    ) -> None:
        self.repo_root = repo_root.resolve(strict=False)
        self.scripts_root = (self.repo_root / "workdir" / "scripts").resolve(strict=False)
        self.data_root = (self.repo_root / "workdir" / "data").resolve(strict=False)
        self.output_root = (
            self.repo_root / "workdir" / "output" / "backtests"
        ).resolve(strict=False)
        self.registry = registry
        self.validator = ScriptingValidator(self.scripts_root, self.repo_root)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="pynereal-backtest-io",
        )
        self._jobs: dict[str, BacktestJob] = {}
        self._lock = asyncio.Lock()
        self._data_process: asyncio.subprocess.Process | None = None
        self._closed = False
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._recover_jobs()

    async def run_io(
        self,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if self._closed:
            raise RuntimeError("backtest manager is closed")
        callback = partial(function, *args, **kwargs)
        return await asyncio.get_running_loop().run_in_executor(
            self._executor,
            callback,
        )

    async def data_payload(self, script_path: str) -> dict[str, Any]:
        normalized = self._normalize_relative_path(script_path, suffix=".py")
        entries = await self.run_io(self._scan_data_files)
        for entry in entries:
            path, _ = self._resolve_under(
                self.data_root,
                entry["path"],
                suffix=".ohlcv",
            )
            entry["delete_blocked"] = self._is_live_data_path(path)
        recommended = self._recommended_data_path(normalized, entries)
        return {
            "script_path": normalized,
            "recommended_data_path": recommended,
            "supported_exchanges": list(_BACKTEST_DATA_EXCHANGES),
            "data": entries,
        }

    async def start(
        self,
        *,
        script_path: str,
        base_revision: str,
        data_path: str,
        time_from: int,
        time_to: int,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        input_values = (
            {name: [value] for name, value in inputs.items()}
            if inputs is not None
            else None
        )
        jobs = await self.start_variants(
            script_path=script_path,
            base_revision=base_revision,
            data_path=data_path,
            time_from=time_from,
            time_to=time_to,
            input_values=input_values,
        )
        return jobs[0]

    async def input_payload(
        self,
        *,
        script_path: str,
        base_revision: str,
        data_path: str,
    ) -> dict[str, Any]:
        normalized_script = self._normalize_relative_path(script_path, suffix=".py")
        request = await self.run_io(
            self._preflight_input_request,
            normalized_script,
            base_revision,
            data_path,
        )
        result = await self.run_io(
            self._inspect_input_metadata,
            request["script_path"],
            request["data_path"],
        )
        return {
            "script_path": request["script_path"],
            "script_revision": request["script_revision"],
            "data_path": request["data_path"],
            "max_concurrent": _MAX_CONCURRENT_BACKTESTS,
            "max_variants": _MAX_BACKTEST_VARIANTS,
            "inputs": result,
        }

    async def start_variants(
        self,
        *,
        script_path: str,
        base_revision: str,
        data_path: str,
        time_from: int,
        time_to: int,
        input_values: dict[str, list[Any]] | None,
    ) -> list[dict[str, Any]]:
        normalized_script = self._normalize_relative_path(script_path, suffix=".py")
        preflight = await self.run_io(
            self._preflight,
            normalized_script,
            base_revision,
            data_path,
            time_from,
            time_to,
        )
        metadata = await self.run_io(
            self._inspect_input_metadata,
            preflight["script_path"],
            preflight["data_path"],
        )
        variants = self._expand_input_variants(metadata, input_values)

        async with self._lock:
            if self._closed:
                raise ScriptingBacktestError(
                    "backtest service is shutting down",
                    code="service_closed",
                    status_code=503,
                )
            if self._data_process is not None and self._data_process.returncode is None:
                raise ScriptingBacktestError(
                    "OHLCV data is being updated",
                    code="data_sync_busy",
                    status_code=409,
                )

            prepared: list[BacktestJob] = []
            run_keys: set[str] = set()
            for variant in variants:
                run_key = self._run_key(preflight, variant)
                if run_key in run_keys or self._active_job_by_run_key(run_key) is not None:
                    raise ScriptingBacktestError(
                        "an identical backtest is already queued or running",
                        code="backtest_duplicate",
                        status_code=409,
                    )
                run_keys.add(run_key)
                job_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:10]
                job = BacktestJob(
                    id=job_id,
                    run_key=run_key,
                    script_path=preflight["script_path"],
                    script_revision=preflight["script_revision"],
                    data_path=preflight["data_path"],
                    time_from=preflight["time_from"],
                    time_to=preflight["time_to"],
                    status="queued",
                    created_at=int(time.time() * 1000),
                    inputs=variant,
                )
                prepared.append(job)

            for job in prepared:
                self._jobs[job.id] = job
                await self.run_io(self._prepare_job_files, job)
            await self._fill_backtest_slots_locked()
            return [self._payload(job) for job in prepared]

    async def _fill_backtest_slots_locked(self) -> None:
        while self._active_process_count() < _MAX_CONCURRENT_BACKTESTS:
            queued = next(
                (job for job in self._jobs.values() if job.status == "queued"),
                None,
            )
            if queued is None:
                return
            await self._launch_job_locked(queued)

    async def _launch_job_locked(self, job: BacktestJob) -> None:
        job.status = "preparing"
        await self.run_io(self._write_job, job)
        log_file = self._log_path(job.id).open("ab", buffering=0)
        worker = Path(__file__).with_name("backtest_worker.py")
        command = [
            sys.executable,
            "-u",
            str(worker),
            "--repo-root",
            str(self.repo_root),
            "--job-dir",
            str(self._job_dir(job.id)),
            "--script-path",
            job.script_path,
            "--data-path",
            job.data_path,
            "--time-from",
            str(job.time_from),
            "--time-to",
            str(job.time_to),
        ]
        process_options: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": log_file,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if os.name == "posix":
            process_options["start_new_session"] = True
        elif os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                **process_options,
            )
        except Exception as exc:
            job.status = "failed"
            job.finished_at = int(time.time() * 1000)
            job.error = f"failed to start backtest worker: {type(exc).__name__}: {exc}"
            await self.run_io(self._write_job, job)
            return
        finally:
            log_file.close()

        job.process = process
        job.pid = process.pid
        job.started_at = int(time.time() * 1000)
        await self.run_io(self._write_job, job)
        job.monitor_task = asyncio.create_task(
            self._monitor(job),
            name=f"backtest-monitor-{job.id}",
        )

    async def status(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(job_id)
        await self._promote_running(job)
        return self._payload(job)

    async def jobs(self, script_path: str | None = None) -> list[dict[str, Any]]:
        normalized = (
            self._normalize_relative_path(script_path, suffix=".py")
            if script_path
            else None
        )
        jobs = sorted(
            (
                job
                for job in self._jobs.values()
                if normalized is None or job.script_path == normalized
            ),
            key=lambda item: item.created_at,
            reverse=True,
        )
        for job in jobs:
            await self._promote_running(job)
        return [self._payload(job) for job in jobs]

    async def latest(self, script_path: str | None = None) -> dict[str, Any] | None:
        normalized = (
            self._normalize_relative_path(script_path, suffix=".py")
            if script_path
            else None
        )
        jobs = [
            job
            for job in self._jobs.values()
            if normalized is None or job.script_path == normalized
        ]
        if not jobs:
            return None
        job = max(jobs, key=lambda item: item.created_at)
        await self._promote_running(job)
        return self._payload(job)

    async def read_log(
        self,
        job_id: str,
        *,
        offset: int = 0,
        max_bytes: int = 128 * 1024,
    ) -> dict[str, Any]:
        self._get_job(job_id)
        safe_offset = max(0, int(offset))
        safe_max = min(max(1, int(max_bytes)), 512 * 1024)
        return await self.run_io(self._read_log_file, job_id, safe_offset, safe_max)

    async def stop(self, job_id: str) -> dict[str, Any]:
        async with self._lock:
            job = self._get_job(job_id)
            if job.status in _TERMINAL_STATUSES:
                return self._payload(job)
            if job.status == "queued":
                job.stop_requested = True
                job.status = "cancelled"
                job.finished_at = int(time.time() * 1000)
                await self.run_io(self._write_job, job)
                return self._payload(job)
            job.stop_requested = True
            job.status = "stopping"
            await self.run_io(self._write_job, job)
            await self._terminate_job(job)
            await self._fill_backtest_slots_locked()
            return self._payload(job)

    async def delete_results(self, script_path: str) -> dict[str, Any]:
        normalized = self._normalize_relative_path(script_path, suffix=".py")
        async with self._lock:
            targets = [
                job
                for job in self._jobs.values()
                if job.script_path == normalized
            ]
            if any(job.status in _ACTIVE_STATUSES for job in targets):
                raise ScriptingBacktestError(
                    "stop the running backtest before deleting its results",
                    code="backtest_active",
                    status_code=409,
                )
            job_ids = [job.id for job in targets]
            await self.run_io(self._delete_job_directories, job_ids)
            for job_id in job_ids:
                self._jobs.pop(job_id, None)
        return {"ok": True, "deleted": len(job_ids), "script_path": normalized}

    async def sync_data(
        self,
        *,
        action: str,
        data_path: str = "",
        exchange: str = "",
        symbol: str = "",
        timeframe: str = "",
        history_since: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        process: asyncio.subprocess.Process | None = None
        sync_dir: Path | None = None
        request: dict[str, str]
        async with self._lock:
            if self._closed:
                raise ScriptingBacktestError(
                    "backtest service is shutting down",
                    code="service_closed",
                    status_code=503,
                )
            if self._active_job() is not None:
                raise ScriptingBacktestError(
                    "stop the running backtest before changing OHLCV data",
                    code="backtest_busy",
                    status_code=409,
                )
            if self._data_process is not None and self._data_process.returncode is None:
                raise ScriptingBacktestError(
                    "another OHLCV data operation is already running",
                    code="data_sync_busy",
                    status_code=409,
                )
            request = await self.run_io(
                self._preflight_data_sync,
                action,
                data_path,
                exchange,
                symbol,
                timeframe,
                history_since,
                file_name,
            )
            if request.get("managed_live") == "true":
                return {
                    "ok": True,
                    "data_path": request["data_path"],
                    "message": "An active session already keeps this data current.",
                }

            sync_dir = self.output_root / f".data-sync-{uuid.uuid4().hex[:12]}"
            sync_dir.mkdir(parents=True, exist_ok=False)
            result_path = sync_dir / "result.json"
            worker = Path(__file__).with_name("backtest_data_worker.py")
            command = [
                sys.executable,
                "-u",
                str(worker),
                "--repo-root",
                str(self.repo_root),
                "--result-path",
                str(result_path),
                "--action",
                request["action"],
                "--exchange",
                request["exchange"],
                "--symbol",
                request["symbol"],
                "--timeframe",
                request["timeframe"],
            ]
            if request.get("history_since"):
                command.extend(["--history-since", request["history_since"]])
            if request.get("absolute_data_path"):
                command.extend(["--data-path", request["absolute_data_path"]])
            process_options: dict[str, Any] = {
                "cwd": str(self.repo_root),
                "stdin": asyncio.subprocess.DEVNULL,
                "stdout": asyncio.subprocess.DEVNULL,
                "stderr": asyncio.subprocess.DEVNULL,
            }
            if os.name == "posix":
                process_options["start_new_session"] = True
            elif os.name == "nt":
                process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    **process_options,
                )
            except Exception as exc:
                shutil.rmtree(sync_dir, ignore_errors=True)
                raise ScriptingBacktestError(
                    f"failed to start OHLCV data worker: {type(exc).__name__}: {exc}",
                    code="data_sync_start_failed",
                    status_code=500,
                ) from exc
            self._data_process = process

        try:
            exit_code = await process.wait()
            result = await self.run_io(self._read_json_file, sync_dir / "result.json")
        except asyncio.CancelledError:
            await self._terminate_process(process)
            raise
        finally:
            async with self._lock:
                if self._data_process is process:
                    self._data_process = None
            if self._closed:
                shutil.rmtree(sync_dir, ignore_errors=True)
            else:
                await self.run_io(shutil.rmtree, sync_dir, True)

        if exit_code != 0 or result.get("status") != "completed":
            raise ScriptingBacktestError(
                str(result.get("error") or f"OHLCV data worker exited with status {exit_code}")[:1000],
                code="data_sync_failed",
                status_code=500,
            )
        return {
            "ok": True,
            "data_path": str(result.get("data_path") or request["data_path"]),
            "message": "OHLCV data updated." if request["action"] == "update" else "OHLCV data downloaded.",
        }

    async def delete_data(self, data_path: str) -> dict[str, Any]:
        async with self._lock:
            if self._closed:
                raise ScriptingBacktestError(
                    "backtest service is shutting down",
                    code="service_closed",
                    status_code=503,
                )
            if self._active_job() is not None:
                raise ScriptingBacktestError(
                    "stop the running backtest before deleting OHLCV data",
                    code="backtest_busy",
                    status_code=409,
                )
            if self._data_process is not None and self._data_process.returncode is None:
                raise ScriptingBacktestError(
                    "another OHLCV data operation is already running",
                    code="data_sync_busy",
                    status_code=409,
                )

            path, normalized = self._resolve_under(
                self.data_root,
                data_path,
                suffix=".ohlcv",
            )
            if not path.is_file():
                raise ScriptingBacktestError(
                    "OHLCV data file was not found",
                    code="data_not_found",
                    status_code=404,
                )
            if self._is_live_data_path(path):
                raise ScriptingBacktestError(
                    "remove every session using this OHLCV data before deleting it",
                    code="data_in_use",
                    status_code=409,
                )

            try:
                self._delete_data_pair(path)
            except OSError as exc:
                raise ScriptingBacktestError(
                    f"OHLCV data could not be deleted: {exc}",
                    code="data_delete_failed",
                    status_code=500,
                ) from exc
        return {"ok": True, "data_path": normalized}

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            for job in list(self._jobs.values()):
                if job.status not in _ACTIVE_STATUSES:
                    continue
                if job.status == "queued":
                    job.status = "interrupted"
                    job.finished_at = int(time.time() * 1000)
                    job.error = "data service stopped before the backtest started"
                    await self.run_io(self._write_job, job)
                    continue
                job.stop_requested = True
                job.status = "stopping"
                await self.run_io(self._write_job, job)
                await self._terminate_job(job)
            if self._data_process is not None and self._data_process.returncode is None:
                await self._terminate_process(self._data_process)
                self._data_process = None
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def _monitor(self, job: BacktestJob) -> None:
        process = job.process
        if process is None:
            return
        exit_code = await process.wait()
        job.exit_code = exit_code
        job.finished_at = int(time.time() * 1000)
        result = await self.run_io(self._read_worker_result, job.id)
        if job.stop_requested:
            job.status = "cancelled"
        elif exit_code == 0 and result.get("status") == "completed":
            job.status = "completed"
            job.summary = await self.run_io(
                read_strategy_summary,
                self._job_dir(job.id) / "strategy.csv",
            )
        else:
            job.status = "failed"
            job.error = str(
                result.get("error")
                or f"backtest worker exited with status {exit_code}"
            )[:1000]
        job.actual_time_from = self._optional_int(result.get("actual_time_from"))
        job.actual_time_to = self._optional_int(result.get("actual_time_to"))
        job.process = None
        await self.run_io(self._delete_runtime_directory, job.id)
        await self.run_io(self._write_job, job)
        if not job.stop_requested:
            async with self._lock:
                if not self._closed:
                    await self._fill_backtest_slots_locked()

    async def _promote_running(self, job: BacktestJob) -> None:
        if job.status != "preparing":
            return
        if not self._ready_path(job.id).exists():
            return
        job.status = "running"
        await self.run_io(self._write_job, job)

    async def _terminate_job(self, job: BacktestJob) -> None:
        process = job.process
        if process is None or process.returncode is not None:
            if job.monitor_task is not None:
                await asyncio.gather(job.monitor_task, return_exceptions=True)
            return
        self._send_terminate(process)
        try:
            if job.monitor_task is not None:
                await asyncio.wait_for(asyncio.shield(job.monitor_task), timeout=5.0)
            else:
                await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._send_kill(process)
            if job.monitor_task is not None:
                await asyncio.gather(job.monitor_task, return_exceptions=True)
            else:
                await process.wait()

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        self._send_terminate(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._send_kill(process)
            await process.wait()

    @staticmethod
    def _send_terminate(process: asyncio.subprocess.Process) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    @staticmethod
    def _send_kill(process: asyncio.subprocess.Process) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass

    def _preflight_input_request(
        self,
        script_path: str,
        base_revision: str,
        data_path: str,
    ) -> dict[str, str]:
        script_file, normalized_script = self._resolve_under(
            self.scripts_root,
            script_path,
            suffix=".py",
        )
        if not script_file.is_file():
            raise ScriptingBacktestError(
                "script file was not found",
                code="script_not_found",
                status_code=404,
            )
        revision = hashlib.sha256(script_file.read_bytes()).hexdigest()
        if not isinstance(base_revision, str) or revision != base_revision:
            raise ScriptingBacktestError(
                "script changed after it was loaded",
                code="revision_conflict",
                status_code=409,
            )
        data_file, normalized_data = self._resolve_under(
            self.data_root,
            data_path,
            suffix=".ohlcv",
        )
        if not data_file.is_file():
            raise ScriptingBacktestError(
                "OHLCV data file was not found",
                code="data_not_found",
                status_code=404,
            )
        if not data_file.with_suffix(".toml").is_file():
            raise ScriptingBacktestError(
                "symbol metadata file was not found",
                code="symbol_metadata_not_found",
            )
        return {
            "script_path": normalized_script,
            "script_revision": revision,
            "data_path": normalized_data,
        }

    def _inspect_input_metadata(
        self,
        script_path: str,
        data_path: str,
    ) -> list[dict[str, Any]]:
        work_dir = self.output_root / f".input-inspect-{uuid.uuid4().hex[:12]}"
        work_dir.mkdir(parents=True, exist_ok=False)
        result_path = work_dir / "result.json"
        command = [
            sys.executable,
            "-u",
            str(Path(__file__).with_name("backtest_input_worker.py")),
            "--repo-root",
            str(self.repo_root),
            "--script-path",
            script_path,
            "--data-path",
            data_path,
            "--result-path",
            str(result_path),
        ]
        try:
            try:
                process = subprocess.run(
                    command,
                    cwd=self.repo_root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ScriptingBacktestError(
                    "script inputs could not be read before the timeout",
                    code="input_inspection_timeout",
                    status_code=500,
                ) from exc
            result = self._read_json_file(result_path)
            if process.returncode != 0 or result.get("error"):
                detail = str(result.get("error") or process.stdout or "unknown error")
                raise ScriptingBacktestError(
                    f"script inputs could not be read: {detail[:1000]}",
                    code="input_inspection_failed",
                    status_code=500,
                )
            raw_inputs = result.get("inputs")
            if not isinstance(raw_inputs, list):
                raise ScriptingBacktestError(
                    "script input metadata is invalid",
                    code="input_metadata_invalid",
                    status_code=500,
                )
            return [item for item in raw_inputs if isinstance(item, dict)]
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _expand_input_variants(
        self,
        metadata: list[dict[str, Any]],
        requested: dict[str, list[Any]] | None,
    ) -> list[dict[str, Any]]:
        if requested is not None and not isinstance(requested, dict):
            raise ScriptingBacktestError(
                "input_values must be an object of value lists",
                code="invalid_input_values",
            )
        requested = requested or {}
        descriptors = {
            str(item.get("id")): item
            for item in metadata
            if isinstance(item.get("id"), str) and item.get("id")
        }
        unknown = sorted(set(requested) - set(descriptors))
        if unknown:
            raise ScriptingBacktestError(
                f"unknown script input: {unknown[0]}",
                code="unknown_input",
            )

        names: list[str] = []
        value_sets: list[list[Any]] = []
        variant_count = 1
        for name, descriptor in descriptors.items():
            raw_values = requested.get(name, [descriptor.get("value")])
            if not isinstance(raw_values, list) or not raw_values:
                raise ScriptingBacktestError(
                    f"{name} must have at least one value",
                    code="invalid_input_values",
                )
            values: list[Any] = []
            seen: set[str] = set()
            for raw_value in raw_values:
                value = self._validate_input_value(descriptor, raw_value)
                marker = json.dumps(value, sort_keys=True, ensure_ascii=False)
                if marker in seen:
                    continue
                seen.add(marker)
                values.append(value)
            names.append(name)
            value_sets.append(values)
            variant_count *= len(values)
            if variant_count > _MAX_BACKTEST_VARIANTS:
                raise ScriptingBacktestError(
                    f"input combinations exceed the {_MAX_BACKTEST_VARIANTS} run limit",
                    code="too_many_variants",
                )

        if not names:
            return [{}]
        return [
            dict(zip(names, values, strict=True))
            for values in itertools.product(*value_sets)
        ]

    @staticmethod
    def _validate_input_value(descriptor: dict[str, Any], value: Any) -> Any:
        name = str(descriptor.get("id") or "input")
        input_type = str(descriptor.get("input_type") or "").lower()
        if input_type == "bool":
            if not isinstance(value, bool):
                raise ScriptingBacktestError(f"{name} must be true or false", code="invalid_input_value")
            normalized = value
        elif input_type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ScriptingBacktestError(f"{name} must be an integer", code="invalid_input_value")
            normalized = value
        elif input_type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ScriptingBacktestError(f"{name} must be a number", code="invalid_input_value")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ScriptingBacktestError(
                    f"{name} must be a finite number",
                    code="invalid_input_value",
                )
        elif input_type in {"string", "source", "color", "enum"}:
            if not isinstance(value, str):
                raise ScriptingBacktestError(f"{name} must be text", code="invalid_input_value")
            normalized = value
        else:
            if not isinstance(value, (bool, int, float, str)):
                raise ScriptingBacktestError(
                    f"{name} has an unsupported value",
                    code="invalid_input_value",
                )
            normalized = value

        options = descriptor.get("options")
        if isinstance(options, list) and options and normalized not in options:
            raise ScriptingBacktestError(
                f"{name} must be one of its declared options",
                code="invalid_input_value",
            )
        if isinstance(normalized, (int, float)) and not isinstance(normalized, bool):
            minimum = descriptor.get("minval")
            maximum = descriptor.get("maxval")
            if isinstance(minimum, (int, float)) and normalized < minimum:
                raise ScriptingBacktestError(
                    f"{name} must be at least {minimum}",
                    code="invalid_input_value",
                )
            if isinstance(maximum, (int, float)) and normalized > maximum:
                raise ScriptingBacktestError(
                    f"{name} must be at most {maximum}",
                    code="invalid_input_value",
                )
        return normalized

    @staticmethod
    def _run_key(preflight: dict[str, Any], inputs: dict[str, Any]) -> str:
        payload = {
            "script_path": preflight["script_path"],
            "script_revision": preflight["script_revision"],
            "data_path": preflight["data_path"],
            "time_from": preflight["time_from"],
            "time_to": preflight["time_to"],
            "inputs": inputs,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def _preflight(
        self,
        script_path: str,
        base_revision: str,
        data_path: str,
        time_from: int,
        time_to: int,
    ) -> dict[str, Any]:
        script_file, normalized_script = self._resolve_under(
            self.scripts_root,
            script_path,
            suffix=".py",
        )
        if not script_file.is_file():
            raise ScriptingBacktestError(
                "script file was not found",
                code="script_not_found",
                status_code=404,
            )
        content_bytes = script_file.read_bytes()
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScriptingBacktestError(
                "script must be UTF-8 encoded",
                code="script_encoding",
            ) from exc
        revision = hashlib.sha256(content_bytes).hexdigest()
        if not isinstance(base_revision, str) or revision != base_revision:
            raise ScriptingBacktestError(
                "script changed after it was loaded",
                code="revision_conflict",
                status_code=409,
            )
        validation = self.validator.validate(normalized_script, content)
        if (
            validation.get("script_kind") not in {"strategy", "indicator"}
            or not validation.get("runnable")
        ):
            raise ScriptingBacktestError(
                "only a runnable strategy or indicator can be backtested",
                code="script_not_runnable",
            )

        data_file, normalized_data = self._resolve_under(
            self.data_root,
            data_path,
            suffix=".ohlcv",
        )
        if not data_file.is_file():
            raise ScriptingBacktestError(
                "OHLCV data file was not found",
                code="data_not_found",
                status_code=404,
            )
        if not data_file.with_suffix(".toml").is_file():
            raise ScriptingBacktestError(
                "symbol metadata file was not found",
                code="symbol_metadata_not_found",
            )
        entry = self._data_entry(data_file)
        requested_from = int(time_from)
        requested_to = int(time_to)
        if requested_from > requested_to:
            raise ScriptingBacktestError(
                "Date from must not be after Date to",
                code="invalid_time_range",
            )
        if (
            requested_from < entry["start_timestamp"]
            or requested_to > entry["latest_confirmed_timestamp"]
        ):
            raise ScriptingBacktestError(
                "selected dates must be inside the confirmed OHLCV range",
                code="time_range_outside_data",
            )
        with OHLCVReader(data_file) as reader:
            if reader.get_size(requested_from, requested_to) <= 0:
                raise ScriptingBacktestError(
                    "no OHLCV candles exist in the selected range",
                    code="empty_time_range",
                )
        return {
            "script_path": normalized_script,
            "script_revision": revision,
            "data_path": normalized_data,
            "time_from": requested_from,
            "time_to": requested_to,
        }

    def _preflight_data_sync(
        self,
        action: str,
        data_path: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        history_since: str,
        file_name: str,
    ) -> dict[str, str]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "update":
            path, normalized_path = self._resolve_under(
                self.data_root,
                data_path,
                suffix=".ohlcv",
            )
            if not path.is_file() or not path.with_suffix(".toml").is_file():
                raise ScriptingBacktestError(
                    "OHLCV data file was not found",
                    code="data_not_found",
                    status_code=404,
                )
            source = self._ccxt_source_for_data(path)
            if source is None:
                raise ScriptingBacktestError(
                    "this OHLCV file does not have a supported download source",
                    code="data_source_unavailable",
                )
            if self._is_live_data_path(path):
                return {
                    **source,
                    "action": normalized_action,
                    "data_path": normalized_path,
                    "absolute_data_path": str(path),
                    "managed_live": "true",
                }
            return {
                **source,
                "action": normalized_action,
                "data_path": normalized_path,
                "absolute_data_path": str(path),
            }
        if normalized_action != "download":
            raise ScriptingBacktestError("invalid OHLCV data action", code="invalid_data_action")

        normalized_exchange = str(exchange or "").strip().lower()
        if normalized_exchange not in _BACKTEST_DATA_EXCHANGES:
            raise ScriptingBacktestError(
                "select a supported exchange",
                code="invalid_exchange",
            )
        normalized_symbol = str(symbol or "").strip().upper()
        if not re.fullmatch(
            r"[A-Z0-9][A-Z0-9._-]*/[A-Z0-9][A-Z0-9._-]*(?::[A-Z0-9][A-Z0-9._-]*)?",
            normalized_symbol,
        ):
            raise ScriptingBacktestError(
                "enter a symbol such as BTC/USDT:USDT",
                code="invalid_symbol",
            )
        normalized_timeframe = self._normalize_download_timeframe(timeframe)
        normalized_history = self._normalize_history_since(history_since)
        target = f"{normalized_exchange}:{normalized_symbol}".upper()
        canonical_path = CCXTProvider.get_ohlcv_path(
            target,
            normalized_timeframe,
            self.data_root,
        ).resolve(strict=False)
        output_path = self._download_output_path(file_name, canonical_path)
        if output_path.exists() or output_path.with_suffix(".toml").exists():
            raise ScriptingBacktestError(
                "OHLCV data already exists; select it and use Update",
                code="data_exists",
                status_code=409,
            )
        return {
            "action": normalized_action,
            "exchange": normalized_exchange,
            "symbol": normalized_symbol,
            "timeframe": normalized_timeframe,
            "history_since": normalized_history,
            "data_path": output_path.relative_to(self.data_root).as_posix(),
            "absolute_data_path": str(output_path),
        }

    def _ccxt_source_for_data(
        self,
        path: Path,
        *,
        syminfo: SymInfo | None = None,
    ) -> dict[str, str] | None:
        if syminfo is None:
            try:
                syminfo = SymInfo.load_toml(path.with_suffix(".toml"))
            except (OSError, ValueError, TypeError):
                return None
        period = str(syminfo.period or "").strip()
        stored = self._stored_download_source(path.with_suffix(".toml"))
        if stored is not None and stored["timeframe"] == period:
            return stored
        if not path.stem.lower().startswith("ccxt_"):
            return None
        prefix = path.stem[5:]
        exchange = str(syminfo.prefix or "").strip().lower()
        exchange_prefix = f"{exchange}_"
        if (
            exchange not in _BACKTEST_DATA_EXCHANGES
            or not period
            or not prefix.lower().startswith(exchange_prefix)
        ):
            return None
        remainder = prefix[len(exchange_prefix):]
        marker = f"_{period}"
        positions = [
            match.start()
            for match in re.finditer(re.escape(marker), remainder, flags=re.IGNORECASE)
            if match.end() == len(remainder) or remainder[match.end()] == "_"
        ]
        for position in reversed(positions):
            market_parts = remainder[:position].split("_")
            if len(market_parts) == 2:
                base, quote = market_parts
                symbol = f"{base}/{quote}"
            elif len(market_parts) >= 3:
                base = "_".join(market_parts[:-2])
                quote, settle = market_parts[-2:]
                symbol = f"{base}/{quote}:{settle}"
            else:
                continue
            target = f"{exchange}:{symbol}".upper()
            expected = CCXTProvider.get_ohlcv_path(
                target,
                period,
                self.data_root,
            ).resolve(strict=False)
            actual_stem = path.resolve(strict=False).stem
            if actual_stem != expected.stem and not actual_stem.startswith(f"{expected.stem}_"):
                continue
            return {
                "exchange": exchange,
                "symbol": symbol.upper(),
                "timeframe": period,
            }
        return None

    @staticmethod
    def _stored_download_source(path: Path) -> dict[str, str] | None:
        try:
            with path.open("rb") as file:
                source = tomllib.load(file).get(_DOWNLOAD_SOURCE_SECTION)
        except (OSError, tomllib.TOMLDecodeError):
            return None
        if not isinstance(source, dict):
            return None
        exchange = str(source.get("exchange") or "").strip().lower()
        symbol = str(source.get("symbol") or "").strip().upper()
        timeframe = str(source.get("timeframe") or "").strip().upper()
        if (
            exchange not in _BACKTEST_DATA_EXCHANGES
            or not re.fullmatch(
                r"[A-Z0-9][A-Z0-9._-]*/[A-Z0-9][A-Z0-9._-]*(?::[A-Z0-9][A-Z0-9._-]*)?",
                symbol,
            )
            or not timeframe
        ):
            return None
        try:
            CCXTProvider.to_exchange_timeframe(timeframe)
        except ValueError:
            return None
        return {"exchange": exchange, "symbol": symbol, "timeframe": timeframe}

    def _download_output_path(self, value: str, canonical_path: Path) -> Path:
        raw = str(value or "").strip()
        if not raw:
            return canonical_path
        if raw.lower().endswith(".ohlcv"):
            raw = raw[:-6]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", raw):
            raise ScriptingBacktestError(
                "file name may contain only letters, numbers, dots, dashes, and underscores",
                code="invalid_data_file_name",
            )
        output_path = (self.data_root / f"{raw}.ohlcv").resolve(strict=False)
        try:
            output_path.relative_to(self.data_root)
        except ValueError as exc:
            raise ScriptingBacktestError(
                "file name must stay inside the data directory",
                code="invalid_data_file_name",
            ) from exc
        return output_path

    @staticmethod
    def _normalize_download_timeframe(value: str) -> str:
        raw = str(value or "").strip()
        if re.fullmatch(r"[1-9][0-9]*(?:m|h|d|w)", raw):
            return CCXTProvider.to_tradingview_timeframe(raw)
        normalized = raw.upper()
        if re.fullmatch(r"[1-9][0-9]*(?:D|W|M)?", normalized):
            try:
                CCXTProvider.to_exchange_timeframe(normalized)
            except ValueError as exc:
                raise ScriptingBacktestError(
                    str(exc),
                    code="invalid_timeframe",
                ) from exc
            return normalized
        raise ScriptingBacktestError(
            "enter a timeframe such as 5m, 1h, or 1D",
            code="invalid_timeframe",
        )

    @staticmethod
    def _normalize_history_since(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScriptingBacktestError(
                "history since must be an ISO date or timestamp",
                code="invalid_history_since",
            ) from exc
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        if parsed >= datetime.now(UTC).replace(tzinfo=None):
            raise ScriptingBacktestError(
                "history since must be earlier than now",
                code="invalid_history_since",
            )
        return parsed.isoformat(sep=" ", timespec="seconds")

    def _is_live_data_path(self, path: Path) -> bool:
        if self.registry is None:
            return False
        target = path.resolve(strict=False)
        return any(
            session.ohlcv_path.resolve(strict=False) == target
            for session in self.registry.sessions.values()
        )

    @staticmethod
    def _delete_data_pair(path: Path) -> None:
        metadata_path = path.with_suffix(".toml")
        paths = [path]
        if metadata_path.is_file():
            paths.append(metadata_path)

        staged: list[tuple[Path, Path]] = []
        token = uuid.uuid4().hex
        try:
            for source in paths:
                temporary = source.with_name(f".{source.name}.delete-{token}")
                os.replace(source, temporary)
                staged.append((source, temporary))
        except OSError:
            for source, temporary in reversed(staged):
                if temporary.exists():
                    os.replace(temporary, source)
            raise

        for _, temporary in staged:
            temporary.unlink(missing_ok=True)

    def _scan_data_files(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not self.data_root.exists():
            return entries
        for path in sorted(self.data_root.rglob("*.ohlcv")):
            relative_parts = path.relative_to(self.data_root).parts
            if (
                path.is_symlink()
                or any(part.startswith(".") for part in relative_parts)
                or not path.with_suffix(".toml").is_file()
            ):
                continue
            try:
                entries.append(self._data_entry(path))
            except (OSError, ValueError, TypeError):
                continue
        return entries

    def _data_entry(self, path: Path) -> dict[str, Any]:
        syminfo = SymInfo.load_toml(path.with_suffix(".toml"))
        with OHLCVReader(path) as reader:
            start = reader.start_timestamp
            end = reader.end_timestamp
            interval = reader.interval
            size = reader.size
        if start is None or end is None or interval is None or size < 2:
            raise ValueError("OHLCV data does not contain enough candles")
        latest_confirmed = end if end + interval <= int(time.time()) else end - interval
        if latest_confirmed < start:
            raise ValueError("OHLCV data does not contain a confirmed candle")
        relative = path.relative_to(self.data_root).as_posix()
        entry = {
            "path": relative,
            "provider": str(syminfo.prefix or ""),
            "symbol": str(syminfo.ticker or path.stem),
            "description": str(syminfo.description or ""),
            "timeframe": str(syminfo.period or ""),
            "start_timestamp": int(start),
            "end_timestamp": int(end),
            "latest_confirmed_timestamp": int(latest_confirmed),
            "interval_seconds": int(interval),
            "candle_count": int(size),
        }
        source = self._ccxt_source_for_data(path, syminfo=syminfo)
        if source is not None:
            try:
                input_timeframe = CCXTProvider.to_exchange_timeframe(source["timeframe"])
            except ValueError:
                pass
            else:
                entry["download_source"] = {
                    **source,
                    "input_timeframe": input_timeframe,
                }
        return entry

    def _recommended_data_path(
        self,
        script_path: str,
        entries: list[dict[str, Any]],
    ) -> str | None:
        if self.registry is None:
            return None
        available = {entry["path"] for entry in entries}
        matches: set[str] = set()
        for session in self.registry.sessions.values():
            if str(session.spec.script_name or "").replace("\\", "/") != script_path:
                continue
            try:
                relative = session.ohlcv_path.resolve(strict=False).relative_to(
                    self.data_root
                ).as_posix()
            except (AttributeError, ValueError):
                continue
            if relative in available:
                matches.add(relative)
        return next(iter(matches)) if len(matches) == 1 else None

    def _normalize_relative_path(self, value: str, *, suffix: str) -> str:
        _, normalized = self._resolve_under(
            self.scripts_root if suffix == ".py" else self.data_root,
            value,
            suffix=suffix,
        )
        return normalized

    @staticmethod
    def _resolve_under(root: Path, value: str, *, suffix: str) -> tuple[Path, str]:
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise ScriptingBacktestError("invalid path", code="invalid_path")
        path = Path(value)
        if path.is_absolute() or path.suffix.lower() != suffix:
            raise ScriptingBacktestError("invalid path", code="invalid_path")
        parts = path.parts
        if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
            raise ScriptingBacktestError("invalid path", code="invalid_path")
        candidate = root
        for part in parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise ScriptingBacktestError(
                    "symbolic links are not available",
                    code="invalid_path",
                )
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ScriptingBacktestError("invalid path", code="invalid_path") from exc
        return resolved, "/".join(parts)

    def _recover_jobs(self) -> None:
        for metadata_path in self.output_root.glob("*/job.json"):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                inputs = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
                job = BacktestJob(
                    id=str(data["id"]),
                    run_key=str(data.get("run_key") or ""),
                    script_path=str(data["script_path"]),
                    script_revision=str(data["script_revision"]),
                    data_path=str(data["data_path"]),
                    time_from=int(data["time_from"]),
                    time_to=int(data["time_to"]),
                    status=str(data["status"]),
                    created_at=int(data["created_at"]),
                    inputs=inputs,
                    started_at=self._optional_int(data.get("started_at")),
                    finished_at=self._optional_int(data.get("finished_at")),
                    pid=self._optional_int(data.get("pid")),
                    exit_code=self._optional_int(data.get("exit_code")),
                    error=data.get("error"),
                    actual_time_from=self._optional_int(data.get("actual_time_from")),
                    actual_time_to=self._optional_int(data.get("actual_time_to")),
                    summary=data.get("summary") if isinstance(data.get("summary"), dict) else None,
                )
                if not job.run_key:
                    job.run_key = self._run_key(
                        {
                            "script_path": job.script_path,
                            "script_revision": job.script_revision,
                            "data_path": job.data_path,
                            "time_from": job.time_from,
                            "time_to": job.time_to,
                        },
                        job.inputs,
                    )
                if job.status in _ACTIVE_STATUSES:
                    job.status = "interrupted"
                    job.finished_at = int(time.time() * 1000)
                    job.error = "data service stopped before the backtest finished"
                    self._write_job(job)
                elif job.status == "completed" and job.summary is None:
                    job.summary = read_strategy_summary(
                        metadata_path.parent / "strategy.csv"
                    )
                    self._write_job(job)
                self._jobs[job.id] = job
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue

    def _prepare_job_files(self, job: BacktestJob) -> None:
        job_dir = self._job_dir(job.id)
        job_dir.mkdir(parents=True, exist_ok=False)
        self._log_path(job.id).touch()
        self._write_job(job)

    def _write_job(self, job: BacktestJob) -> None:
        payload = self._payload(job)
        path = self._job_path(job.id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _read_worker_result(self, job_id: str) -> dict[str, Any]:
        path = self._job_dir(job_id) / "worker_result.json"
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _delete_job_directories(self, job_ids: list[str]) -> None:
        for job_id in job_ids:
            shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    def _delete_runtime_directory(self, job_id: str) -> None:
        shutil.rmtree(self._job_dir(job_id) / "runtime", ignore_errors=True)

    def _read_log_file(self, job_id: str, offset: int, max_bytes: int) -> dict[str, Any]:
        path = self._log_path(job_id)
        if not path.exists():
            return {"offset": offset, "next_offset": offset, "text": "", "eof": True}
        size = path.stat().st_size
        start = min(offset, size)
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(max_bytes)
        next_offset = start + len(data)
        return {
            "offset": start,
            "next_offset": next_offset,
            "text": data.decode("utf-8", errors="replace"),
            "eof": next_offset >= size,
        }

    def _active_job(self, script_path: str | None = None) -> BacktestJob | None:
        return next(
            (
                job
                for job in self._jobs.values()
                if job.status in _ACTIVE_STATUSES
                and (script_path is None or job.script_path == script_path)
            ),
            None,
        )

    def _active_job_by_run_key(self, run_key: str) -> BacktestJob | None:
        return next(
            (
                job
                for job in self._jobs.values()
                if job.status in _ACTIVE_STATUSES and job.run_key == run_key
            ),
            None,
        )

    def _active_process_count(self) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.status in {"preparing", "running", "stopping"}
            and job.process is not None
            and job.process.returncode is None
        )

    def _get_job(self, job_id: str) -> BacktestJob:
        job = self._jobs.get(str(job_id))
        if job is None:
            raise ScriptingBacktestError(
                "backtest job was not found",
                code="job_not_found",
                status_code=404,
            )
        return job

    def _payload(self, job: BacktestJob) -> dict[str, Any]:
        job_dir = self._job_dir(job.id)
        return {
            "id": job.id,
            "run_key": job.run_key,
            "script_path": job.script_path,
            "script_revision": job.script_revision,
            "data_path": job.data_path,
            "time_from": job.time_from,
            "time_to": job.time_to,
            "actual_time_from": job.actual_time_from,
            "actual_time_to": job.actual_time_to,
            "status": job.status,
            "inputs": job.inputs,
            "queue_position": self._queue_position(job),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "pid": job.pid,
            "exit_code": job.exit_code,
            "error": job.error,
            "summary": job.summary,
            "artifacts": {
                "plot": (job_dir / "plot.csv").is_file(),
                "strategy": (job_dir / "strategy.csv").is_file(),
                "trades": (job_dir / "trades.csv").is_file(),
            },
        }

    def _queue_position(self, job: BacktestJob) -> int | None:
        if job.status != "queued":
            return None
        queued = [item for item in self._jobs.values() if item.status == "queued"]
        queued.sort(key=lambda item: item.created_at)
        try:
            return queued.index(job) + 1
        except ValueError:
            return None

    def _job_dir(self, job_id: str) -> Path:
        return self.output_root / job_id

    def _job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _log_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "backtest.log"

    def _ready_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "runtime-ready"

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
