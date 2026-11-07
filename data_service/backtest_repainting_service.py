"""Lifecycle of optional verification workers; never owns a live runner."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

import anyio

from data_service.backtest_repainting import RESULT, SNAPSHOT, write_json


class RepaintingChecks:
    def __init__(self, manager):
        self.manager = manager
        self.active = {}
        self.states = {}
        self.cancelled = set()
        self.changed = asyncio.Condition()
        self.version = 0

    async def notify(self):
        async with self.changed:
            self.version += 1
            self.changed.notify_all()

    async def status(self, job_id):
        job = self.manager._get_job(job_id)
        directory = self.manager._job_dir(job.id)
        if job.id in self.states:
            return self.states[job.id]
        if not (directory / SNAPSHOT).is_file():
            return {'status': 'unavailable', 'error': 'No verification snapshot. Run this backtest again.'}
        result = await self.manager.run_io(self._read_result, directory / RESULT)
        if result:
            return result
        return {'status': 'ready'}

    @staticmethod
    def _read_result(path):
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
            if result.get('status') in ('preparing', 'running', 'stopping'):
                return {'status': 'interrupted', 'error': 'Verification was interrupted. Run it again.'}
            return result
        except (FileNotFoundError, ValueError):
            return None

    async def start(self, job_id, start):
        from scripting_backtest import ScriptingBacktestError
        async with self.manager._lock:
            job = self.manager._get_job(job_id)
            if self.manager._closed:
                raise ScriptingBacktestError('Service is stopping', code='service_closed', status_code=503)
            if self.active:
                raise ScriptingBacktestError('A repainting check is already running', code='verification_busy', status_code=409)
            if job.status != 'completed' or (job.monitor_task and not job.monitor_task.done()):
                raise ScriptingBacktestError('Complete the backtest first', code='backtest_active', status_code=409)
            if not (self.manager._job_dir(job.id) / SNAPSHOT).is_file():
                raise ScriptingBacktestError('No verification snapshot. Run this backtest again.',
                                            code='snapshot_unavailable', status_code=409)
            begin = job.actual_time_from if job.actual_time_from is not None else job.time_from
            end = job.actual_time_to if job.actual_time_to is not None else job.time_to
            if start is not None and (type(start) is not int or not begin <= start <= end):
                raise ScriptingBacktestError('Start must be a UTC timestamp within the backtested range',
                                            code='invalid_time_range')
            directory = self.manager._job_dir(job.id)
            args = [sys.executable, '-m', 'data_service.backtest_repainting_worker', '--job-dir', str(directory)]
            if start is not None:
                args += ['--start', str(start)]
            log = (directory / 'repainting.log').open('wb')
            temporary = tempfile.TemporaryDirectory(prefix='pynereal-repainting-')
            args += ['--work-dir', temporary.name]
            process = None
            try:
                with anyio.CancelScope(shield=True):
                    self.cancelled.discard(job.id)
                    initial = {'status': 'preparing', 'start': start, 'done': 0,
                               'started_at': int(time.time())}
                    await self.manager.run_io(write_json, directory / RESULT, initial)
                    process = await asyncio.create_subprocess_exec(
                        *args, cwd=Path(__file__).resolve().parent.parent,
                        stdout=asyncio.subprocess.PIPE, stderr=log,
                        start_new_session=os.name == 'posix')
                    self.states[job.id] = initial
                    task = asyncio.create_task(self.monitor(job.id, process, temporary))
                    self.active[job.id] = (process, task)
                    await self.notify()
            except OSError as exc:
                self.states[job.id] = {'status': 'failed', 'error': 'Could not launch verification worker'}
                await self.manager.run_io(write_json, directory / RESULT, self.states[job.id])
                raise ScriptingBacktestError('Could not launch verification worker',
                                            code='verification_launch_failed', status_code=500) from exc
            finally:
                log.close()
                if process is None:
                    await self.manager.run_io(temporary.cleanup)
            return self.states[job.id]

    async def monitor(self, job_id, process, temporary):
        result_path = self.manager._job_dir(job_id) / RESULT
        try:
            async for line in process.stdout:
                message = json.loads(line)
                if message.get('status') != 'completed' and job_id not in self.cancelled:
                    self.states[job_id] = {**self.states[job_id], **message}
                    await self.notify()
            code = await process.wait()
            if job_id in self.cancelled:
                self.states[job_id] = {'status': 'cancelled'}
            elif code == 0:
                result = await self.manager.run_io(self._read_result, result_path)
                if not result or result.get('status') != 'completed':
                    raise ValueError('Verification did not produce a complete result')
                self.states[job_id] = result
            elif self.states[job_id]['status'] != 'failed':
                self.states[job_id] = {'status': 'failed', 'error': f'Verification worker exited ({code})'}
        except Exception as exc:
            self.states[job_id] = {'status': 'failed', 'error': str(exc)}
            await self.terminate(process)
        finally:
            try:
                await self.manager.run_io(write_json, result_path, self.states[job_id])
            except OSError:
                self.states[job_id] = {'status': 'failed', 'error': 'Could not save verification result'}
            finally:
                try:
                    await self.manager.run_io(temporary.cleanup)
                finally:
                    self.active.pop(job_id, None)
                    self.cancelled.discard(job_id)
                    await self.notify()

    @staticmethod
    async def terminate(process):
        if process.returncode is not None:
            return
        def send(sig):
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, sig)
                elif sig == signal.SIGTERM:
                    process.terminate()
                else:
                    process.kill()
            except ProcessLookupError:
                pass
        send(signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            send(signal.SIGKILL)
            await process.wait()

    async def stop(self, job_id):
        self.manager._get_job(job_id)
        pair = self.active.get(job_id)
        if pair:
            with anyio.CancelScope(shield=True):
                self.cancelled.add(job_id)
                self.states[job_id]['status'] = 'stopping'
                await self.notify()
                await self.terminate(pair[0])
                await asyncio.shield(pair[1])
        return await self.status(job_id)

    async def close(self):
        for job_id in list(self.active):
            await self.stop(job_id)
