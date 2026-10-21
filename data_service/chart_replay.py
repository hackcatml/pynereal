"""WebSocket-owned replay workers, isolated from the live runner lifecycle."""
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
from fastapi import APIRouter, WebSocket, WebSocketDisconnect


class ChartReplayService:
    def __init__(self, registry, repo_root: Path):
        self.registry = registry
        self.repo_root = repo_root
        self.workers = set()
        self.connections = set()
        self.closed = False

    async def stop(self, process):
        if process.returncode is None:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        self.workers.discard(process)

    async def close(self):
        self.closed = True
        for ws in list(self.connections):
            try:
                await ws.close(code=1001)
            except Exception:
                pass
        for process in list(self.workers):
            await self.stop(process)

    async def connect(self, ws: WebSocket, session_id: str):
        # Match the caller's origin when browsers send one; allow non-browser clients.
        origin = ws.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != ws.headers.get('host'):
                await ws.close(code=1008)
                return
        await ws.accept()
        if self.closed or len(self.connections) >= 4:
            await ws.send_json({'type': 'error', 'error': 'Replay capacity reached (4). Close another replay first.'})
            await ws.close()
            return
        self.connections.add(ws)
        process = None
        output_task = None
        temp = tempfile.TemporaryDirectory(prefix='pynereal-replay-')
        root = Path(temp.name)
        log = None
        ready = False
        pending = False
        try:
            session = self.registry.get(session_id)
            if session is None:
                raise ValueError('Session not found')
            scripts = (self.repo_root / 'workdir' / 'scripts').resolve()
            script = (scripts / session.spec.script_name).resolve()
            if not session.spec.script_name or not script.is_relative_to(scripts) or not script.is_file():
                raise ValueError('Assign an existing strategy to this session first')
            if not session.feed.history_ready_event.is_set():
                raise ValueError('Session history is still loading')
            start = await asyncio.wait_for(ws.receive_json(), 30)
            if start.get('type') != 'start' or type(start.get('time')) is not int:
                raise ValueError('A replay start time in UTC seconds is required')
            request = {
                'root': str(root), 'scripts': str(scripts), 'script': str(script),
                'data': str(session.feed.paths.ohlcv_path.resolve()),
                'metadata': str(session.feed.paths.toml_path.resolve()),
                'time': start['time'], 'now': int(time.time()),
                'market': {'exchange': session.spec.exchange, 'symbol': session.spec.symbol,
                           'timeframe': session.spec.timeframe},
            }
            (root / 'request.json').write_text(json.dumps(request), encoding='utf-8')
            log = (root / 'worker.log').open('wb')
            with anyio.CancelScope(shield=True):
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'data_service.chart_replay_worker', str(root / 'request.json'),
                    cwd=self.repo_root, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=log, start_new_session=os.name == 'posix', limit=8 * 1024 * 1024)
                self.workers.add(process)

            async def output():
                nonlocal ready, pending
                try:
                    while line := await process.stdout.readline():
                        message = json.loads(line)
                        if message.get('type') == 'ready':
                            ready = True
                            pending = False
                            message['seek_supported'] = True
                        if message.get('type') == 'frame':
                            pending = False
                        if message.get('type') == 'ready':
                            await ws.send_json(message)
                        else:
                            await ws.send_text(line.decode('utf-8'))
                        if message.get('type') == 'error':
                            await ws.close(code=1011)
                            return
                    code = await process.wait()
                    if code:
                        await ws.send_json({'type': 'error', 'error': f'Replay worker exited ({code})'})
                    await ws.close()
                except Exception:
                    try:
                        await ws.send_json({'type': 'error', 'error': 'Replay worker connection failed'})
                        await ws.close(code=1011)
                    except Exception:
                        pass

            output_task = asyncio.create_task(output())
            while True:
                command = await asyncio.wait_for(ws.receive_json(), 120)
                kind = command.get('type')
                if kind == 'ping':
                    await ws.send_json({'type': 'pong'})
                elif kind == 'step' and ready and not pending and process.returncode is None:
                    pending = True
                    process.stdin.write(b'{"type":"step"}\n')
                    await process.stdin.drain()
                elif kind == 'seek' and ready and not pending and process.returncode is None:
                    if type(command.get('time')) is not int:
                        raise ValueError('A replay seek time in UTC seconds is required')
                    pending = True
                    ready = False
                    process.stdin.write((json.dumps({'type': 'seek', 'time': command['time']}) + '\n').encode())
                    await process.stdin.drain()
                elif kind == 'stop':
                    break
        except (WebSocketDisconnect, TimeoutError):
            pass
        except Exception as exc:
            try:
                await ws.send_json({'type': 'error', 'error': str(exc)})
            except Exception:
                pass
        finally:
            # A tab close cancels the ASGI scope; cleanup must still reap its worker.
            with anyio.CancelScope(shield=True):
                if output_task:
                    output_task.cancel()
                    await asyncio.gather(output_task, return_exceptions=True)
                if process:
                    await self.stop(process)
                if log:
                    log.close()
                await asyncio.to_thread(temp.cleanup)
                self.connections.discard(ws)
                try:
                    await ws.close()
                except Exception:
                    pass


def build_chart_replay_router(service: ChartReplayService):
    router = APIRouter()

    @router.websocket('/ws/replay/{session_id}')
    async def replay(ws: WebSocket, session_id: str):
        await service.connect(ws, session_id)

    return router
