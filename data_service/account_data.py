from __future__ import annotations

import asyncio
import copy
import multiprocessing
import queue
import time
from collections.abc import Callable
from concurrent.futures import Executor, ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from account_service.cache import AccountCache
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
        cache_path: Path | None = None,
        cache_ttl_seconds: float = 10.0,
        executor_factory: Callable[[], Executor] | None = None,
        positions_collector: Callable[[str], dict[str, Any]] = collect_positions_snapshot,
    ) -> None:
        self.config_path = config_path.resolve()
        self.cache_path = (
            cache_path
            if cache_path is not None
            else self.config_path.parent.parent / "data" / "cache" / "account_cache.sqlite"
        ).resolve()
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
        self._live_control_output: Any = None
        self._live_input: Any = None
        self._live_stop: Any = None
        self._live_reader: asyncio.Task[None] | None = None
        self._live_control_reader: asyncio.Task[None] | None = None
        self._history_refresh_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._positions_revision = 0
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
            if force:
                self._sync_live_snapshot(result)
            output = copy.deepcopy(result)
            output["cached"] = False
            return output

    def _sync_live_snapshot(self, snapshot: dict[str, Any]) -> None:
        input_queue = self._live_input
        process = self._live_process
        if input_queue is None or process is None or not process.is_alive():
            return
        revision = self._positions_revision + 1
        message = {
            "revision": revision,
            "snapshot": copy.deepcopy(snapshot),
        }
        try:
            input_queue.put_nowait(message)
        except queue.Full:
            try:
                input_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                input_queue.put_nowait(message)
            except queue.Full:
                return
        self._positions_revision = revision

    async def position_history(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        account: str = "",
        exchange: str = "",
        symbol: str = "",
        exact_market: bool = False,
    ) -> dict[str, Any]:
        try:
            page = await asyncio.to_thread(
                AccountCache(self.cache_path).position_history_page,
                cursor=cursor,
                limit=limit,
                account=account,
                exchange=exchange,
                symbol=symbol,
                exact_market=exact_market,
            )
        except ValueError:
            raise
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc
        return self._history_response(page)

    async def position_history_groups(self, *, exchange: str = "") -> dict[str, Any]:
        try:
            groups = await asyncio.to_thread(
                AccountCache(self.cache_path).position_history_groups,
                exchange=exchange,
            )
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc
        return self._history_group_response(groups, exchange=exchange)

    async def order_history(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        account: str = "",
        exchange: str = "",
        symbol: str = "",
        status: str = "",
        exact_market: bool = False,
    ) -> dict[str, Any]:
        try:
            page = await asyncio.to_thread(
                AccountCache(self.cache_path).order_history_page,
                cursor=cursor,
                limit=limit,
                account=account,
                exchange=exchange,
                symbol=symbol,
                status=status,
                exact_market=exact_market,
            )
        except ValueError:
            raise
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc
        return self._history_response(page)

    async def order_history_groups(self, *, exchange: str = "") -> dict[str, Any]:
        try:
            groups = await asyncio.to_thread(
                AccountCache(self.cache_path).order_history_groups,
                exchange=exchange,
            )
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc
        return self._history_group_response(groups, exchange=exchange)

    async def pnl(
        self,
        *,
        days: int = 90,
        account: str = "",
        exchange: str = "",
    ) -> dict[str, Any]:
        try:
            payload = await asyncio.to_thread(
                AccountCache(self.cache_path).pnl_summary,
                days=days,
                account=account,
                exchange=exchange,
            )
        except ValueError:
            raise
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc

        results = payload.get("results")
        results = results if isinstance(results, list) else []
        for result in results:
            if not isinstance(result, dict):
                continue
            result["exchange_logo_url"] = exchange_logo_url(
                str(result.get("exchange") or "")
            )
        accounts = {
            str(result.get("account") or "")
            for result in results
            if isinstance(result, dict) and result.get("account")
        }
        return {
            "schema_version": "1.0",
            "collected_at": str(payload.get("to") or ""),
            "read_only": True,
            "cached": True,
            "period": {
                "days": int(payload.get("days") or days),
                "from": payload.get("from"),
                "to": payload.get("to"),
            },
            "totals": payload.get("totals", []),
            "results": results,
            "summary": {
                "accounts": len(accounts),
                "rows": len(results),
                "funding_available": False,
                "borrow_interest_available": False,
            },
        }

    async def refresh_history(
        self,
        *,
        kind: str,
        account: str = "",
        exchange: str = "",
        symbol: str = "",
    ) -> dict[str, Any]:
        normalized_kind = kind.strip().lower()
        if normalized_kind not in {"order", "position"}:
            raise AccountDataError("history kind must be order or position")

        account_filter = account.strip()
        account_names: list[str] = []
        if not account_filter:
            try:
                account_names = await asyncio.to_thread(
                    AccountCache(self.cache_path).history_accounts,
                    kind=normalized_kind,
                    exchange=exchange,
                    symbol=symbol,
                )
            except Exception as exc:
                message = str(exc).replace("\n", " ").strip()
                raise AccountDataError(message[:500] or type(exc).__name__) from exc
            if not account_names:
                raise AccountDataError(
                    "no cached accounts match the selected history scope"
                )

        await self._ensure_live_stream()
        process = self._live_process
        input_queue = self._live_input
        if process is None or not process.is_alive() or input_queue is None:
            raise AccountDataError("account worker is not available")

        request_id = uuid4().hex
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._history_refresh_waiters[request_id] = waiter
        message = {
            "type": "history.refresh",
            "request_id": request_id,
            "kind": normalized_kind,
            "account": account_filter,
            "accounts": account_names,
            "exchange": exchange.strip().lower(),
            "symbol": symbol.strip(),
        }
        try:
            await asyncio.to_thread(input_queue.put, message, True, 5.0)
            result = await asyncio.wait_for(asyncio.shield(waiter), timeout=600.0)
        except (queue.Full, ValueError, OSError) as exc:
            raise AccountDataError("account worker request queue is unavailable") from exc
        except TimeoutError as exc:
            raise AccountDataError("account history refresh timed out") from exc
        finally:
            self._history_refresh_waiters.pop(request_id, None)
            if not waiter.done():
                waiter.cancel()

        if result.get("status") != "ok":
            error = result.get("error")
            if not isinstance(error, dict):
                errors = [
                    item.get("error")
                    for item in result.get("results", [])
                    if isinstance(item, dict) and isinstance(item.get("error"), dict)
                ]
                error = errors[0] if errors else {}
            message = str(error.get("message") or "account history refresh failed")
            raise AccountDataError(message[:500])
        return result

    @staticmethod
    def _history_response(page: dict[str, Any]) -> dict[str, Any]:
        results = page.get("results")
        results = results if isinstance(results, list) else []
        for result in results:
            if not isinstance(result, dict):
                continue
            exchange = str(result.get("exchange") or "")
            result["exchange_logo_url"] = exchange_logo_url(exchange)
        total = int(page.get("total") or 0)
        return {
            "schema_version": "1.0",
            "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "read_only": True,
            "cached": True,
            "results": results,
            "next_cursor": page.get("next_cursor"),
            "summary": {
                "total": total,
                "returned": len(results),
                "has_more": bool(page.get("next_cursor")),
            },
        }

    @staticmethod
    def _history_group_response(
        groups: dict[str, Any],
        *,
        exchange: str,
    ) -> dict[str, Any]:
        results = groups.get("results")
        results = results if isinstance(results, list) else []
        for result in results:
            if not isinstance(result, dict):
                continue
            exchange_id = str(result.get("exchange") or "")
            result["exchange_logo_url"] = exchange_logo_url(exchange_id)
        return {
            "schema_version": "1.0",
            "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "read_only": True,
            "cached": True,
            "group_by": "symbol" if exchange.strip() else "exchange",
            "results": results,
            "summary": {
                "groups": len(results),
                "total": int(groups.get("total") or 0),
            },
        }

    async def connect_live(self, ws: Any) -> None:
        await self.live_ws.connect(ws)
        await self._ensure_live_stream()
        if self._positions is not None:
            payload = copy.deepcopy(self._positions)
            payload["cached"] = True
            await self.live_ws.send(ws, {"type": "account.positions", "payload": payload})

    async def disconnect_live(self, ws: Any) -> None:
        await self.live_ws.disconnect(ws)

    async def start(self) -> None:
        await self._ensure_live_stream()

    async def _ensure_live_stream(self) -> None:
        async with self._live_lock:
            if self._live_process is not None and self._live_process.is_alive():
                return
            context = multiprocessing.get_context("spawn")
            self._live_output = context.Queue(maxsize=8)
            self._live_control_output = context.Queue(maxsize=8)
            self._live_input = context.Queue(maxsize=16)
            self._live_stop = context.Event()
            self._positions_revision = 0
            self._live_process = context.Process(
                target=run_positions_stream,
                args=(
                    str(self.config_path),
                    str(self.cache_path),
                    self._live_output,
                    self._live_control_output,
                    self._live_input,
                    self._live_stop,
                    copy.deepcopy(self._positions),
                ),
                name="pynereal-account-positions",
                daemon=True,
            )
            self._live_process.start()
            self._live_reader = asyncio.create_task(self._read_live_output())
            self._live_control_reader = asyncio.create_task(
                self._read_live_control_output()
            )

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
            raw_revision = payload.pop("_refresh_revision", 0)
            revision = (
                raw_revision
                if isinstance(raw_revision, int)
                and not isinstance(raw_revision, bool)
                else 0
            )
            if revision < self._positions_revision:
                continue
            payload = _add_exchange_logos(payload)
            self._positions = payload
            self._positions_at = time.monotonic()
            await self.live_ws.broadcast_json(
                {"type": "account.positions", "payload": payload}
            )

    async def _read_live_control_output(self) -> None:
        output = self._live_control_output
        if output is None:
            return
        while True:
            message = await asyncio.to_thread(output.get)
            if message is None:
                for waiter in self._history_refresh_waiters.values():
                    if not waiter.done():
                        waiter.set_exception(AccountDataError("account worker stopped"))
                return
            if not isinstance(message, dict):
                continue
            if message.get("type") != "history.refresh.result":
                continue
            request_id = str(message.get("request_id") or "")
            waiter = self._history_refresh_waiters.get(request_id)
            payload = message.get("payload")
            if waiter is None or waiter.done() or not isinstance(payload, dict):
                continue
            waiter.set_result(payload)

    async def _close_live_stream(self) -> None:
        process = self._live_process
        stop = self._live_stop
        reader = self._live_reader
        control_reader = self._live_control_reader
        output = self._live_output
        control_output = self._live_control_output
        input_queue = self._live_input
        self._live_process = None
        self._live_stop = None
        self._live_reader = None
        self._live_control_reader = None
        self._live_output = None
        self._live_control_output = None
        self._live_input = None
        self._positions_revision = 0
        waiters = list(self._history_refresh_waiters.values())
        self._history_refresh_waiters.clear()
        for waiter in waiters:
            if not waiter.done():
                waiter.set_exception(AccountDataError("account worker stopped"))
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
        if control_reader is not None and not control_reader.done():
            try:
                await asyncio.wait_for(control_reader, timeout=2)
            except TimeoutError:
                control_reader.cancel()
                await asyncio.gather(control_reader, return_exceptions=True)
        if output is not None:
            output.close()
            await asyncio.to_thread(output.join_thread)
        if control_output is not None:
            control_output.close()
            await asyncio.to_thread(control_output.join_thread)
        if input_queue is not None:
            input_queue.close()
            await asyncio.to_thread(input_queue.join_thread)

    async def close(self) -> None:
        await self._close_live_stream()
        executor = self._executor
        self._executor = None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
