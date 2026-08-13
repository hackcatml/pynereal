from __future__ import annotations

import asyncio
import copy
import multiprocessing
import time
from collections.abc import Callable
from concurrent.futures import Executor, ProcessPoolExecutor
from pathlib import Path
from typing import Any

from account_service.live_positions import run_positions_stream
from account_service.positions import collect_positions_snapshot
from tv_logos import exchange_logo_url
from ws_manager import WSManager


class AccountDataError(RuntimeError):
    pass


def _add_exchange_logos(payload: dict[str, Any]) -> dict[str, Any]:
    for result in payload.get("results", []):
        if not isinstance(result, dict):
            continue
        exchange = str(result.get("exchange") or "")
        result["exchange_logo_url"] = exchange_logo_url(exchange)
    return payload


class AccountDataService:
    """Read-only account snapshots executed outside the data-service process."""

    def __init__(
        self,
        config_path: Path,
        *,
        cache_ttl_seconds: float = 10.0,
        executor_factory: Callable[[], Executor] | None = None,
        positions_collector: Callable[[str], dict[str, Any]] = collect_positions_snapshot,
    ) -> None:
        self.config_path = config_path.resolve()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._executor_factory = executor_factory or self._new_process_executor
        self._positions_collector = positions_collector
        self._executor: Executor | None = None
        self._positions: dict[str, Any] | None = None
        self._positions_at = 0.0
        self._positions_lock = asyncio.Lock()
        self._live_lock = asyncio.Lock()
        self._live_process: multiprocessing.Process | None = None
        self._live_output: Any = None
        self._live_stop: Any = None
        self._live_reader: asyncio.Task[None] | None = None
        self.live_ws = WSManager()

    @staticmethod
    def _new_process_executor() -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
        )

    def _ensure_executor(self) -> Executor:
        if self._executor is None:
            self._executor = self._executor_factory()
        return self._executor

    def _positions_cache_valid(self) -> bool:
        return (
            self._positions is not None
            and time.monotonic() - self._positions_at < self.cache_ttl_seconds
        )

    async def positions(self, *, force: bool = False) -> dict[str, Any]:
        requested_at = time.monotonic()
        if not force and self._positions_cache_valid():
            result = copy.deepcopy(self._positions)
            result["cached"] = True
            return result

        async with self._positions_lock:
            if (
                force
                and self._positions is not None
                and self._positions_at >= requested_at
            ):
                result = copy.deepcopy(self._positions)
                result["cached"] = True
                return result
            if not force and self._positions_cache_valid():
                result = copy.deepcopy(self._positions)
                result["cached"] = True
                return result

            loop = asyncio.get_running_loop()
            try:
                result = await loop.run_in_executor(
                    self._ensure_executor(),
                    self._positions_collector,
                    str(self.config_path),
                )
            except Exception as exc:
                message = str(exc).replace("\n", " ").strip()
                raise AccountDataError(message[:500] or type(exc).__name__) from exc
            result = _add_exchange_logos(result)
            self._positions = result
            self._positions_at = time.monotonic()
            output = copy.deepcopy(result)
            output["cached"] = False
            return output

    async def connect_live(self, ws: Any) -> None:
        await self.live_ws.connect(ws)
        await self._ensure_live_stream()
        if self._positions is not None:
            payload = copy.deepcopy(self._positions)
            payload["cached"] = True
            await self.live_ws.send(ws, {"type": "account.positions", "payload": payload})

    async def disconnect_live(self, ws: Any) -> None:
        await self.live_ws.disconnect(ws)

    async def _ensure_live_stream(self) -> None:
        async with self._live_lock:
            if self._live_process is not None and self._live_process.is_alive():
                return
            context = multiprocessing.get_context("spawn")
            self._live_output = context.Queue(maxsize=8)
            self._live_stop = context.Event()
            self._live_process = context.Process(
                target=run_positions_stream,
                args=(
                    str(self.config_path),
                    self._live_output,
                    self._live_stop,
                    copy.deepcopy(self._positions),
                ),
                name="pynereal-account-positions",
                daemon=True,
            )
            self._live_process.start()
            self._live_reader = asyncio.create_task(self._read_live_output())

    async def _read_live_output(self) -> None:
        output = self._live_output
        if output is None:
            return
        while True:
            payload = await asyncio.to_thread(output.get)
            if payload is None:
                return
            if not isinstance(payload, dict):
                continue
            payload = _add_exchange_logos(payload)
            self._positions = payload
            self._positions_at = time.monotonic()
            await self.live_ws.broadcast_json(
                {"type": "account.positions", "payload": payload}
            )

    async def _close_live_stream(self) -> None:
        process = self._live_process
        stop = self._live_stop
        reader = self._live_reader
        output = self._live_output
        self._live_process = None
        self._live_stop = None
        self._live_reader = None
        self._live_output = None
        if stop is not None:
            stop.set()
        if process is not None:
            await asyncio.to_thread(process.join, 10)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        if reader is not None and not reader.done():
            try:
                await asyncio.wait_for(reader, timeout=2)
            except TimeoutError:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
        if output is not None:
            output.close()
            await asyncio.to_thread(output.join_thread)

    async def close(self) -> None:
        await self._close_live_stream()
        executor = self._executor
        self._executor = None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
