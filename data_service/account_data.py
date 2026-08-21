from __future__ import annotations

import asyncio
import copy
import multiprocessing
import queue
import shutil
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import Executor, ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from account_service.cache import AccountCache
from account_service.csv_import import (
    MAX_IMPORT_FILE_BYTES,
    MAX_IMPORT_FILES,
    MAX_IMPORT_TOTAL_BYTES,
    TIMEZONE_CONFIRMATION_WARNING,
    HistoryCsvError,
    build_history_import_batch,
    preview_history_files,
)
from account_service.live_positions import run_positions_stream
from account_service.positions import collect_positions_snapshot
from account_service.transfers import (
    TRANSFER_CACHE_TTL_SECONDS,
    records_from_transfer_result,
)
from ai.scripts.asset import (
    ExchangeAccount,
    build_exchange,
    configured_accounts,
    read_provider_config,
)
from tv_logos import exchange_logo_url
from ws_manager import WSManager


class AccountDataError(RuntimeError):
    pass


def _fetch_okx_account_uid(account: ExchangeAccount) -> str:
    configured_uid = str(account.config.get("uid") or "").strip()
    if configured_uid:
        return configured_uid
    exchange = build_exchange("okx", account.config, 10_000, None)
    try:
        response = exchange.privateGetAccountConfig({})
        rows = response.get("data") if isinstance(response, dict) else None
        data = rows[0] if isinstance(rows, list) and rows else {}
        return str(data.get("uid") or "").strip() if isinstance(data, dict) else ""
    except Exception:
        return ""
    finally:
        try:
            exchange.close()
        except Exception:
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
        self._import_executor: Executor | None = None
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
        self._history_import_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._transfer_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._transfer_refresh_locks: dict[str, asyncio.Lock] = {}
        self._history_import_previews: dict[str, dict[str, Any]] = {}
        self._history_import_jobs: dict[str, dict[str, Any]] = {}
        self._history_import_tasks: dict[str, asyncio.Task[None]] = {}
        self._history_import_root: Path | None = None
        self._okx_account_uid_cache: dict[str, str] = {}
        self._okx_account_uid_lock = asyncio.Lock()
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

    def _ensure_import_executor(self) -> Executor:
        if self._import_executor is None:
            self._import_executor = self._executor_factory()
        return self._import_executor

    async def _okx_account_uids(
        self,
        accounts: list[ExchangeAccount],
    ) -> dict[str, str]:
        okx_accounts = [account for account in accounts if account.exchange_id == "okx"]
        async with self._okx_account_uid_lock:
            missing = [
                account
                for account in okx_accounts
                if account.name not in self._okx_account_uid_cache
            ]
            if missing:
                resolved = await asyncio.gather(*(
                    asyncio.to_thread(_fetch_okx_account_uid, account)
                    for account in missing
                ))
                self._okx_account_uid_cache.update(
                    (account.name, uid)
                    for account, uid in zip(missing, resolved, strict=True)
                )
            return {
                account.name: self._okx_account_uid_cache.get(account.name, "")
                for account in okx_accounts
                if self._okx_account_uid_cache.get(account.name)
            }

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

    async def transfer_history(
        self,
        *,
        account: str,
        exchange: str,
        cursor: str | None = None,
        limit: int = 50,
        force: bool = False,
        assets: list[str] | None = None,
        account_types: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_account = account.strip()
        normalized_exchange = exchange.strip().lower()
        if not normalized_account or not normalized_exchange:
            raise AccountDataError("transfer account and exchange are required")
        asset_hints = list(dict.fromkeys(
            str(value).strip().upper()
            for value in (assets or [])
            if str(value).strip()
        ))[:20]
        type_hints = list(dict.fromkeys(
            str(value).strip().lower()
            for value in (account_types or [])
            if str(value).strip()
        ))[:10]

        lock = self._transfer_refresh_locks.setdefault(
            f"{normalized_exchange}:{normalized_account}",
            asyncio.Lock(),
        )
        refresh_error = ""
        async with lock:
            state = await asyncio.to_thread(
                AccountCache(self.cache_path).transfer_sync_state,
                normalized_account,
                normalized_exchange,
            )
            last_success = str(state.get("last_success_at") or "")
            stale = True
            if last_success:
                try:
                    age = datetime.now(UTC).timestamp() - datetime.fromisoformat(
                        last_success.replace("Z", "+00:00")
                    ).timestamp()
                    stale = age >= TRANSFER_CACHE_TTL_SECONDS
                except ValueError:
                    stale = True
            if force or stale:
                try:
                    await self._refresh_transfers(
                        normalized_account,
                        normalized_exchange,
                        asset_hints,
                        type_hints,
                    )
                except AccountDataError as exc:
                    refresh_error = str(exc)

        try:
            page, state = await asyncio.gather(
                asyncio.to_thread(
                    AccountCache(self.cache_path).transfer_history_page,
                    account=normalized_account,
                    exchange=normalized_exchange,
                    cursor=cursor,
                    limit=limit,
                ),
                asyncio.to_thread(
                    AccountCache(self.cache_path).transfer_sync_state,
                    normalized_account,
                    normalized_exchange,
                ),
            )
        except ValueError:
            raise
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc
        results = page.get("results") if isinstance(page, dict) else []
        results = results if isinstance(results, list) else []
        if refresh_error and not results:
            raise AccountDataError(refresh_error)
        if refresh_error:
            state = {**state, "status": "error", "last_error": refresh_error}
        return {
            "schema_version": "1.0",
            "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "read_only": True,
            "cached": True,
            "account": normalized_account,
            "exchange": normalized_exchange,
            "results": results,
            "next_cursor": page.get("next_cursor"),
            "sync": state,
            "summary": {
                "total": int(page.get("total") or 0),
                "returned": len(results),
                "has_more": bool(page.get("next_cursor")),
            },
        }

    async def _refresh_transfers(
        self,
        account: str,
        exchange: str,
        assets: list[str],
        account_types: list[str],
    ) -> dict[str, Any]:
        await self._ensure_live_stream()
        process = self._live_process
        input_queue = self._live_input
        if process is None or not process.is_alive() or input_queue is None:
            raise AccountDataError("account worker is not available")
        request_id = uuid4().hex
        waiter = asyncio.get_running_loop().create_future()
        self._transfer_waiters[request_id] = waiter
        try:
            await asyncio.to_thread(input_queue.put, {
                "type": "transfer.refresh",
                "request_id": request_id,
                "account": account,
                "exchange": exchange,
                "assets": assets,
                "account_types": account_types,
            }, True, 5.0)
            result = await asyncio.wait_for(asyncio.shield(waiter), timeout=300.0)
        except (queue.Full, ValueError, OSError) as exc:
            raise AccountDataError("account worker request queue is unavailable") from exc
        except TimeoutError as exc:
            raise AccountDataError("transfer history refresh timed out") from exc
        finally:
            self._transfer_waiters.pop(request_id, None)
            if not waiter.done():
                waiter.cancel()
        if result.get("status") == "error":
            error = result.get("error")
            error = error if isinstance(error, dict) else {}
            raise AccountDataError(
                str(error.get("message") or "transfer history refresh failed")[:500]
            )
        return result

    async def record_transfer_result(self, result: dict[str, Any]) -> None:
        records = records_from_transfer_result(result)
        if not records:
            return
        await self._ensure_live_stream()
        process = self._live_process
        input_queue = self._live_input
        if process is None or not process.is_alive() or input_queue is None:
            raise AccountDataError("account worker is not available")
        request_id = uuid4().hex
        waiter = asyncio.get_running_loop().create_future()
        self._transfer_waiters[request_id] = waiter
        try:
            await asyncio.to_thread(input_queue.put, {
                "type": "transfer.record",
                "request_id": request_id,
                "records": records,
                "account": str(result.get("account") or ""),
                "exchange": str(result.get("exchange") or ""),
            }, True, 5.0)
            response = await asyncio.wait_for(asyncio.shield(waiter), timeout=30.0)
            if response.get("status") == "error":
                error = response.get("error")
                error = error if isinstance(error, dict) else {}
                raise AccountDataError(
                    str(error.get("message") or "transfer history write failed")[:500]
                )
        finally:
            self._transfer_waiters.pop(request_id, None)
            if not waiter.done():
                waiter.cancel()

    async def pnl(
        self,
        *,
        days: int | None = 90,
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
        funding_available = any(
            isinstance(result, dict) and result.get("funding") is not None
            for result in results
        )
        borrow_interest_available = any(
            isinstance(result, dict) and result.get("borrow_interest") is not None
            for result in results
        )
        return {
            "schema_version": "1.0",
            "collected_at": str(payload.get("to") or ""),
            "read_only": True,
            "cached": True,
            "period": {
                "days": payload.get("days"),
                "from": payload.get("from"),
                "to": payload.get("to"),
            },
            "totals": payload.get("totals", []),
            "results": results,
            "summary": {
                "accounts": len(accounts),
                "rows": len(results),
                "funding_available": funding_available,
                "borrow_interest_available": borrow_interest_available,
            },
        }

    def _cleanup_history_import_state(self) -> None:
        now = time.monotonic()
        expired_previews = [
            preview_id
            for preview_id, preview in self._history_import_previews.items()
            if float(preview.get("expires_monotonic") or 0.0) <= now
        ]
        for preview_id in expired_previews:
            preview = self._history_import_previews.pop(preview_id, None)
            if preview is not None:
                shutil.rmtree(str(preview.get("directory") or ""), ignore_errors=True)

        expired_jobs = [
            job_id
            for job_id, job in self._history_import_jobs.items()
            if job.get("status") in {"completed", "failed"}
            and now - float(job.get("finished_monotonic") or now) > 24 * 60 * 60
        ]
        for job_id in expired_jobs:
            self._history_import_jobs.pop(job_id, None)

    @staticmethod
    def _public_import_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if key not in {"finished_monotonic"}
        }

    async def preview_history_import(
        self,
        uploads: list[tuple[str, bytes]],
    ) -> dict[str, Any]:
        self._cleanup_history_import_state()
        if not uploads or len(uploads) > MAX_IMPORT_FILES:
            raise AccountDataError(
                f"select between 1 and {MAX_IMPORT_FILES} CSV files"
            )
        total_size = sum(len(content) for _, content in uploads)
        if total_size > MAX_IMPORT_TOTAL_BYTES:
            raise AccountDataError(
                f"combined CSV size exceeds {MAX_IMPORT_TOTAL_BYTES // (1024 * 1024)} MB"
            )

        if self._history_import_root is None:
            self._history_import_root = Path(
                tempfile.mkdtemp(prefix="pynereal-history-import-")
            )
        preview_id = uuid4().hex
        directory = self._history_import_root / preview_id
        directory.mkdir(parents=True, exist_ok=False)
        specs: list[dict[str, Any]] = []
        try:
            for original_name, content in uploads:
                name = Path(str(original_name or "")).name.strip()
                if not name or Path(name).suffix.lower() != ".csv":
                    raise AccountDataError("only CSV files can be imported")
                if len(content) > MAX_IMPORT_FILE_BYTES:
                    raise AccountDataError(
                        f"CSV exceeds {MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MB: {name}"
                    )
                file_id = uuid4().hex
                path = directory / f"{file_id}.csv"
                await asyncio.to_thread(path.write_bytes, content)
                specs.append({"file_id": file_id, "name": name, "path": str(path)})

            loop = asyncio.get_running_loop()
            preview = await loop.run_in_executor(
                self._ensure_import_executor(),
                preview_history_files,
                specs,
            )
            provider_data = await asyncio.to_thread(
                read_provider_config,
                self.config_path,
            )
            accounts = configured_accounts(provider_data)
            okx_account_uids = await self._okx_account_uids(accounts) if any(
                str(item.get("exchange") or "") == "okx"
                for item in preview.get("files", [])
                if isinstance(item, dict)
            ) else {}
            candidates_by_exchange: dict[str, list[str]] = {}
            for account in accounts:
                candidates_by_exchange.setdefault(account.exchange_id, []).append(
                    account.name
                )

            preview_files = preview.get("files")
            preview_files = preview_files if isinstance(preview_files, list) else []
            stored_files: dict[str, dict[str, Any]] = {}
            specs_by_id = {str(item["file_id"]): item for item in specs}
            for file_preview in preview_files:
                file_id = str(file_preview.get("file_id") or "")
                exchange_id = str(file_preview.get("exchange") or "")
                candidates = sorted(candidates_by_exchange.get(exchange_id, []))
                file_preview["account_candidates"] = candidates
                source_uid = str(file_preview.get("source_account_uid") or "")
                uid_matches = [
                    account_name
                    for account_name in candidates
                    if source_uid and okx_account_uids.get(account_name) == source_uid
                ]
                file_preview["suggested_account"] = uid_matches[0] if len(
                    uid_matches
                ) == 1 else candidates[0] if len(candidates) == 1 else ""
                stored_files[file_id] = {
                    **copy.deepcopy(file_preview),
                    "path": specs_by_id[file_id]["path"],
                }
            if len(stored_files) != len(specs):
                raise AccountDataError("not every selected file could be previewed")

            expires_at = datetime.now(UTC) + timedelta(minutes=30)
            self._history_import_previews[preview_id] = {
                "preview_id": preview_id,
                "directory": str(directory),
                "files": stored_files,
                "timezone_options": preview.get("timezone_options", []),
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                "expires_monotonic": time.monotonic() + 30 * 60,
            }
            return {
                "preview_id": preview_id,
                "expires_at": self._history_import_previews[preview_id]["expires_at"],
                "files": preview_files,
                "timezone_options": preview.get("timezone_options", []),
            }
        except AccountDataError:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        except (HistoryCsvError, ValueError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise AccountDataError(str(exc)[:500]) from exc
        except Exception as exc:
            shutil.rmtree(directory, ignore_errors=True)
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc

    async def remove_history_import_preview_file(
        self,
        preview_id: str,
        file_id: str,
    ) -> dict[str, Any]:
        self._cleanup_history_import_state()
        preview = self._history_import_previews.get(str(preview_id or "").strip())
        if preview is None:
            raise AccountDataError("history import preview expired or was not found")
        files = preview.get("files")
        files = files if isinstance(files, dict) else {}
        file_preview = files.pop(str(file_id or "").strip(), None)
        if not isinstance(file_preview, dict):
            raise AccountDataError("history import preview file was not found")
        path = Path(str(file_preview.get("path") or ""))
        if path.is_file():
            await asyncio.to_thread(path.unlink)
        if not files:
            self._history_import_previews.pop(str(preview_id).strip(), None)
            await asyncio.to_thread(
                shutil.rmtree,
                str(preview.get("directory") or ""),
                True,
            )
        return {"ok": True, "remaining": len(files)}

    async def commit_history_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._cleanup_history_import_state()
        preview_id = str(payload.get("preview_id") or "").strip()
        preview = self._history_import_previews.get(preview_id)
        if preview is None:
            raise AccountDataError("history import preview expired or was not found")
        selections = payload.get("files")
        selections = selections if isinstance(selections, list) else []
        selection_map = {
            str(item.get("file_id") or ""): item
            for item in selections
            if isinstance(item, dict) and item.get("file_id")
        }
        preview_files = preview["files"]
        if set(selection_map) != set(preview_files):
            raise AccountDataError("confirm the account and timezone for every CSV file")

        batch_id = uuid4().hex
        specs: list[dict[str, Any]] = []
        for file_id, file_preview in preview_files.items():
            selection = selection_map[file_id]
            account_name = str(selection.get("account") or "").strip()
            if account_name not in file_preview.get("account_candidates", []):
                raise AccountDataError(
                    f"select a configured {file_preview['exchange']} account for "
                    f"{file_preview['name']}"
                )
            source_timezone = str(selection.get("source_timezone") or "").strip()
            if not source_timezone:
                raise AccountDataError(f"select the source timezone for {file_preview['name']}")
            if selection.get("timezone_confirmed") is not True:
                raise AccountDataError(
                    f"confirm the source timezone for {file_preview['name']}"
                )
            specs.append({
                "file_id": file_id,
                "name": file_preview["name"],
                "path": file_preview["path"],
                "account": account_name,
                "source_timezone": source_timezone,
                "timezone_confirmed": True,
                "import_id": uuid4().hex,
                "batch_id": batch_id,
            })

        loop = asyncio.get_running_loop()
        try:
            batch = await loop.run_in_executor(
                self._ensure_import_executor(),
                build_history_import_batch,
                specs,
            )
        except (HistoryCsvError, ValueError) as exc:
            raise AccountDataError(str(exc)[:500]) from exc
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc

        job_id = uuid4().hex
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = {
            "job_id": job_id,
            "batch_id": batch_id,
            "kind": "import",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "file_count": len(specs),
            "summary": None,
            "imports": [],
            "error": None,
        }
        self._history_import_jobs[job_id] = job
        task = asyncio.create_task(self._run_history_import_job(job_id, batch))
        self._history_import_tasks[job_id] = task
        task.add_done_callback(lambda _task, key=job_id: self._history_import_tasks.pop(key, None))
        return self._public_import_job(job)

    async def _run_history_import_job(
        self,
        job_id: str,
        batch: dict[str, Any],
    ) -> None:
        job = self._history_import_jobs[job_id]
        job["status"] = "running"
        job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        request_id = uuid4().hex
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._history_import_waiters[request_id] = waiter
        try:
            await self._ensure_live_stream()
            process = self._live_process
            input_queue = self._live_input
            if process is None or not process.is_alive() or input_queue is None:
                raise AccountDataError("account worker is not available")
            await asyncio.to_thread(input_queue.put, {
                "type": "history.import",
                "request_id": request_id,
                "batch": batch,
            }, True, 10.0)
            result = await asyncio.wait_for(asyncio.shield(waiter), timeout=600.0)
            if result.get("status") != "ok":
                error = result.get("error")
                error = error if isinstance(error, dict) else {}
                raise AccountDataError(
                    str(error.get("message") or "history import failed")[:500]
                )
            job["status"] = "completed"
            job["summary"] = result.get("summary")
            job["imports"] = result.get("imports", [])
        except asyncio.CancelledError:
            job["status"] = "failed"
            job["error"] = "history import was cancelled"
            raise
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc).replace("\n", " ")[:500]
        finally:
            self._history_import_waiters.pop(request_id, None)
            if not waiter.done():
                waiter.cancel()
            job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            job["finished_monotonic"] = time.monotonic()

    def history_import_job(self, job_id: str) -> dict[str, Any]:
        self._cleanup_history_import_state()
        job = self._history_import_jobs.get(job_id)
        if job is None:
            raise AccountDataError("history import job was not found")
        return self._public_import_job(job)

    async def retry_history_import_enrichment(
        self,
        import_id: str,
    ) -> dict[str, Any]:
        try:
            manifest = await asyncio.to_thread(
                AccountCache(self.cache_path).csv_import,
                import_id,
            )
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc
        if not isinstance(manifest, dict) or manifest.get("exchange") != "hyperliquid":
            raise AccountDataError("Hyperliquid history import was not found")

        job_id = uuid4().hex
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = {
            "job_id": job_id,
            "batch_id": str(manifest.get("batch_id") or ""),
            "import_id": import_id,
            "kind": "enrichment",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "file_count": 1,
            "summary": None,
            "imports": [],
            "error": None,
        }
        self._history_import_jobs[job_id] = job
        task = asyncio.create_task(
            self._run_history_enrichment_job(job_id, import_id)
        )
        self._history_import_tasks[job_id] = task
        task.add_done_callback(
            lambda _task, key=job_id: self._history_import_tasks.pop(key, None)
        )
        return self._public_import_job(job)

    async def _run_history_enrichment_job(
        self,
        job_id: str,
        import_id: str,
    ) -> None:
        job = self._history_import_jobs[job_id]
        job["status"] = "running"
        job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        request_id = uuid4().hex
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._history_import_waiters[request_id] = waiter
        try:
            await self._ensure_live_stream()
            process = self._live_process
            input_queue = self._live_input
            if process is None or not process.is_alive() or input_queue is None:
                raise AccountDataError("account worker is not available")
            await asyncio.to_thread(input_queue.put, {
                "type": "history.enrichment",
                "request_id": request_id,
                "import_id": import_id,
            }, True, 10.0)
            result = await asyncio.wait_for(asyncio.shield(waiter), timeout=600.0)
            if result.get("status") != "ok":
                error = result.get("error")
                error = error if isinstance(error, dict) else {}
                raise AccountDataError(
                    str(error.get("message") or "history enrichment failed")[:500]
                )
            job["status"] = "completed"
            job["summary"] = result.get("summary")
            job["imports"] = result.get("imports", [])
        except asyncio.CancelledError:
            job["status"] = "failed"
            job["error"] = "history enrichment was cancelled"
            raise
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc).replace("\n", " ")[:500]
        finally:
            self._history_import_waiters.pop(request_id, None)
            if not waiter.done():
                waiter.cancel()
            job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            job["finished_monotonic"] = time.monotonic()

    async def history_imports(self, *, limit: int = 100) -> dict[str, Any]:
        try:
            payload = await asyncio.to_thread(
                AccountCache(self.cache_path).csv_imports,
                limit=limit,
            )
        except ValueError:
            raise
        except Exception as exc:
            message = str(exc).replace("\n", " ").strip()
            raise AccountDataError(message[:500] or type(exc).__name__) from exc
        for result in payload.get("results", []):
            if isinstance(result, dict):
                warnings = result.get("warnings")
                if isinstance(warnings, list):
                    result["warnings"] = [
                        warning
                        for warning in warnings
                        if warning != TIMEZONE_CONFIRMATION_WARNING
                    ]
                result["exchange_logo_url"] = exchange_logo_url(
                    str(result.get("exchange") or "")
                )
                result["enrichment_retry_available"] = (
                    result.get("exchange") == "hyperliquid"
                    and result.get("status") == "partial"
                )
        self._cleanup_history_import_state()
        payload["active_jobs"] = [
            self._public_import_job(job)
            for job in self._history_import_jobs.values()
            if job.get("status") in {"queued", "running"}
        ]
        return payload

    async def delete_history_import(self, import_id: str) -> dict[str, Any]:
        normalized_id = import_id.strip()
        if not normalized_id:
            raise AccountDataError("CSV import was not found")
        self._cleanup_history_import_state()
        if any(
            job.get("status") in {"queued", "running"}
            and job.get("import_id") == normalized_id
            for job in self._history_import_jobs.values()
        ):
            raise AccountDataError("CSV import enrichment is still running")
        request_id = uuid4().hex
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._history_import_waiters[request_id] = waiter
        try:
            await self._ensure_live_stream()
            process = self._live_process
            input_queue = self._live_input
            if process is None or not process.is_alive() or input_queue is None:
                raise AccountDataError("account worker is not available")
            await asyncio.to_thread(input_queue.put, {
                "type": "history.import.delete",
                "request_id": request_id,
                "import_id": normalized_id,
            }, True, 10.0)
            result = await asyncio.wait_for(asyncio.shield(waiter), timeout=30.0)
            if result.get("status") == "error":
                error = result.get("error")
                error = error if isinstance(error, dict) else {}
                raise AccountDataError(
                    str(error.get("message") or "CSV import deletion failed")[:500]
                )
            return result
        finally:
            self._history_import_waiters.pop(request_id, None)
            if not waiter.done():
                waiter.cancel()

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
                waiters = [
                    *self._history_refresh_waiters.values(),
                    *self._history_import_waiters.values(),
                    *self._transfer_waiters.values(),
                ]
                for waiter in waiters:
                    if not waiter.done():
                        waiter.set_exception(AccountDataError("account worker stopped"))
                return
            if not isinstance(message, dict):
                continue
            message_type = message.get("type")
            if message_type == "history.refresh.result":
                waiter_map = self._history_refresh_waiters
            elif message_type in {
                "history.import.result",
                "history.enrichment.result",
                "history.import.delete.result",
            }:
                waiter_map = self._history_import_waiters
            elif message_type in {
                "transfer.refresh.result",
                "transfer.record.result",
            }:
                waiter_map = self._transfer_waiters
            else:
                continue
            request_id = str(message.get("request_id") or "")
            waiter = waiter_map.get(request_id)
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
        waiters = [
            *self._history_refresh_waiters.values(),
            *self._history_import_waiters.values(),
            *self._transfer_waiters.values(),
        ]
        self._history_refresh_waiters.clear()
        self._history_import_waiters.clear()
        self._transfer_waiters.clear()
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
        import_tasks = list(self._history_import_tasks.values())
        self._history_import_tasks.clear()
        for task in import_tasks:
            task.cancel()
        if import_tasks:
            await asyncio.gather(*import_tasks, return_exceptions=True)
        await self._close_live_stream()
        executor = self._executor
        self._executor = None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
        import_executor = self._import_executor
        self._import_executor = None
        if import_executor is not None:
            await asyncio.to_thread(
                import_executor.shutdown,
                wait=True,
                cancel_futures=True,
            )
        if self._history_import_root is not None:
            await asyncio.to_thread(
                shutil.rmtree,
                self._history_import_root,
                True,
            )
            self._history_import_root = None
        self._history_import_previews.clear()
