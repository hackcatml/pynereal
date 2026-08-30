from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from fastapi import WebSocket

from market_data_diagnostics import log_session_diagnostic

from .delivery import VerificationDeliveryRequest, VerificationDeliveryService
from .protocol import compare_results

if TYPE_CHECKING:
    from runtime import Session


_WARMUP_TIMEOUT_SECONDS = 300.0
_MAX_CANDLE_BACKLOG = 128
_MAX_RESULTS = 128


class VerificationCoordinator:
    """Own one session's verification protocol and comparison state."""

    def __init__(
        self,
        session: Session,
        *,
        enabled: bool,
        delivery: VerificationDeliveryService | None = None,
    ) -> None:
        self.session = session
        self.enabled = enabled
        self.delivery = delivery
        self.runner_count = 0
        self.runner_ready = False
        self.generation_id = uuid.uuid4().hex
        self.initialized = False
        self.connection_lost = False
        self.source_hashes: dict[str, str] = {}
        self.last_error: str | None = None
        self.state_updated_at = datetime.now(UTC).isoformat()

        self._primary_results: dict[int, dict[str, Any]] = {}
        self._finalized_results: dict[int, dict[str, Any]] = {}
        self._primary_delivery_context: dict[int, dict[str, Any]] = {}
        self._comparison_fingerprints: set[tuple[Any, ...]] = set()
        self._candle_backlog: dict[int, dict[str, Any]] = {}
        self._protocol_lock = asyncio.Lock()
        self._last_dispatched_timestamp_ms: int | None = None
        self._latest_authoritative_timestamp_ms: int | None = None
        self._rewarm_pending = False
        self._warmup_timeout_task: asyncio.Task | None = None
        self._initial_pending_timestamp_ms: int | None = None
        self._initial_primary_pending_resolved = False

        self.on_recovery: Callable[[Session, str], Awaitable[None]] | None = None
        self.on_connected: Callable[[Session], Awaitable[None]] | None = None
        self.on_disconnected: Callable[[Session], Awaitable[None]] | None = None
        self.on_ready: Callable[[Session], Awaitable[None]] | None = None

    @property
    def rewarm_pending(self) -> bool:
        return self._rewarm_pending

    def reset_generation(self) -> None:
        self._cancel_warmup_timeout()
        self._initial_pending_timestamp_ms = None
        self._initial_primary_pending_resolved = False
        self.generation_id = uuid.uuid4().hex
        self.runner_ready = False
        self.initialized = False
        self.source_hashes.clear()
        self._last_dispatched_timestamp_ms = None
        self._latest_authoritative_timestamp_ms = None
        self._candle_backlog.clear()
        self._primary_results.clear()
        self._finalized_results.clear()
        self._primary_delivery_context.clear()
        self._comparison_fingerprints.clear()

    def reset_for_reconfigure(self) -> None:
        self._cancel_warmup_timeout()
        self._initial_pending_timestamp_ms = None
        self._initial_primary_pending_resolved = False
        self.runner_ready = False
        self._primary_results.clear()
        self._finalized_results.clear()
        self._primary_delivery_context.clear()
        self._comparison_fingerprints.clear()
        self._candle_backlog.clear()
        self._last_dispatched_timestamp_ms = None
        self._latest_authoritative_timestamp_ms = None
        self.initialized = False
        self.connection_lost = False
        self.source_hashes.clear()
        self._rewarm_pending = False
        self.last_error = None
        self.state_updated_at = datetime.now(UTC).isoformat()

    def _cancel_warmup_timeout(self) -> None:
        task = self._warmup_timeout_task
        self._warmup_timeout_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _warmup_timeout(self, generation_id: str) -> None:
        try:
            await asyncio.sleep(_WARMUP_TIMEOUT_SECONDS)
            if (
                generation_id == self.generation_id
                and self.runner_count > 0
                and not self.runner_ready
            ):
                await self.request_recovery("verification warm-up timed out")
        except asyncio.CancelledError:
            raise
        finally:
            if self._warmup_timeout_task is asyncio.current_task():
                self._warmup_timeout_task = None

    async def prepare_recovery(self, reason: str) -> None:
        self.reset_generation()
        self.connection_lost = False
        self._rewarm_pending = True
        self.last_error = reason
        self.state_updated_at = datetime.now(UTC).isoformat()
        self._log({
            "event": "verification_recovery_requested",
            "generation_id": self.generation_id,
            "reason": reason,
        })
        await self.session._notify_status()

    async def request_recovery(self, reason: str) -> None:
        await self.prepare_recovery(reason)
        if self.on_recovery is not None:
            await self.on_recovery(self.session, reason)

    def _resume_plan(
        self,
        resume: Any,
    ) -> tuple[bool, str, int | None, list[dict[str, Any]]]:
        if not self.initialized:
            return False, "verification state is not initialized", None, []
        if not isinstance(resume, dict):
            return False, "verification resume state is missing", None, []
        if str(resume.get("generation_id") or "") != self.generation_id:
            return False, "verification generation changed", None, []
        if not bool(resume.get("complete", False)):
            return False, "verification pending-result history overflowed", None, []
        source_hashes = resume.get("source_hashes") or {}
        if source_hashes != self.source_hashes:
            return False, "verification script source changed", None, []
        try:
            last_processed_ms = int(resume["last_processed_timestamp_ms"])
            expected_timestamp_ms = int(resume["pending_bar_timestamp_ms"])
        except (KeyError, TypeError, ValueError):
            return False, "verification resume cursor is missing", None, []
        if (
            self._last_dispatched_timestamp_ms is not None
            and last_processed_ms > self._last_dispatched_timestamp_ms
        ):
            return False, "verification resume cursor is ahead of dispatch", None, []

        pending = [
            payload
            for timestamp_ms, payload in sorted(self._candle_backlog.items())
            if timestamp_ms > last_processed_ms
        ]
        if (
            self._latest_authoritative_timestamp_ms is not None
            and self._latest_authoritative_timestamp_ms > last_processed_ms
            and not pending
        ):
            return False, "verification candle backlog is incomplete", None, []
        for payload in pending:
            try:
                timestamp_ms = int(payload["candle_timestamp_ms"])
                next_timestamp_ms = int(payload["confirmed_bar_and_new_bar"][1][0])
            except (KeyError, IndexError, TypeError, ValueError):
                return False, "verification candle backlog is invalid", None, []
            if timestamp_ms != expected_timestamp_ms:
                return False, "verification candle backlog has a gap", None, []
            expected_timestamp_ms = next_timestamp_ms
        return True, "", last_processed_ms, pending

    async def _ack_result(self, ws: WebSocket, event: dict[str, Any]) -> None:
        try:
            timestamp_ms = int(event["candle_timestamp_ms"])
        except (KeyError, TypeError, ValueError):
            return
        await self.session.ws_manager.send(ws, {
            "type": "verification_result_ack",
            "generation_id": self.generation_id,
            "candle_timestamp_ms": timestamp_ms,
        })

    async def _resume_runner(self, ws: WebSocket, resume: Any) -> tuple[bool, str]:
        valid, reason, last_processed_ms, pending = self._resume_plan(resume)
        if not valid or last_processed_ms is None:
            return False, reason

        async with self._protocol_lock:
            for result in resume.get("pending_results") or []:
                if not isinstance(result, dict):
                    continue
                if result.get("type") != "verification_result":
                    continue
                if str(result.get("generation_id") or "") != self.generation_id:
                    continue
                await self.record_result(result)
                await self._ack_result(ws, result)

            self._last_dispatched_timestamp_ms = last_processed_ms
            self.runner_ready = True
            self.connection_lost = False
            self.last_error = None
            self.state_updated_at = datetime.now(UTC).isoformat()
            await self.session.ws_manager.send(ws, {
                "type": "verification_resume_accepted",
                "generation_id": self.generation_id,
                "replay_count": len(pending),
            })
            for payload in pending:
                await self._dispatch_payload_locked(ws, payload)

        self._log({
            "event": "verification_runner_resumed",
            "generation_id": self.generation_id,
            "calculated_through_ms": last_processed_ms,
            "replayed_candles": len(pending),
            "pending_results": len(resume.get("pending_results") or []),
        })
        return True, ""

    async def handle_disconnect(self) -> None:
        self.runner_count -= 1
        if self.runner_count <= 0:
            self.runner_count = 0
            self.runner_ready = False
            self.connection_lost = True
            self.last_error = "verification runner connection lost"
            self.state_updated_at = datetime.now(UTC).isoformat()
            self._cancel_warmup_timeout()
        self._log({
            "event": "verification_runner_disconnected",
            "generation_id": self.generation_id,
        })
        if self.runner_count == 0:
            if self.on_disconnected is not None:
                await self.on_disconnected(self.session)
            await self.session._notify_status()

    async def handle_client_hello(
        self,
        ws: WebSocket,
        event: dict[str, Any],
    ) -> None:
        self.session.client_roles[ws] = "verification_runner"
        self.runner_count += 1
        self.runner_ready = False
        self.state_updated_at = datetime.now(UTC).isoformat()
        self._log({
            "event": "verification_runner_connected",
            "generation_id": self.generation_id,
        })
        if self.on_connected is not None:
            await self.on_connected(self.session)

        resume = event.get("verification_resume")
        resumed = False
        resume_reason = ""
        if self.session.runner_ready and resume is not None:
            resumed, resume_reason = await self._resume_runner(ws, resume)
        if resumed:
            if self.on_ready is not None:
                await self.on_ready(self.session)
            await self.session._notify_status()
            return

        reconnecting = self.connection_lost or self.initialized or resume is not None
        if reconnecting:
            resume_reason = resume_reason or "verification resume state is unavailable"
            self._log({
                "event": "verification_runner_resume_rejected",
                "generation_id": self.generation_id,
                "reason": resume_reason,
            })
            if not self._rewarm_pending:
                self.reset_generation()
                self._rewarm_pending = True
            self.last_error = resume_reason
        self.connection_lost = False
        await self.session._notify_status()
        if not self.session.runner_ready:
            return
        if (
            self._rewarm_pending
            and self.session.runner_phase in {"prerun_scheduled", "prerun_active"}
        ):
            self._log({
                "event": "verification_warmup_deferred",
                "generation_id": self.generation_id,
                "runner_phase": self.session.runner_phase,
                "next_prerun_at": self.session.next_prerun_at,
            })
            return
        self._rewarm_pending = False
        await self.send_warmup(ws)

    async def handle_message(self, ws: WebSocket, event: dict[str, Any]) -> bool:
        msg_type = event.get("type")
        if msg_type == "client_hello" and event.get("role") == "verification_runner":
            await self.handle_client_hello(ws, event)
            return True
        if msg_type == "verification_runner_ready":
            await self._handle_runner_ready(ws, event)
            return True
        if msg_type in {"primary_result", "verification_result"}:
            expected_role = (
                "runner" if msg_type == "primary_result" else "verification_runner"
            )
            if self.session.client_roles.get(ws) == expected_role:
                await self.record_result(event)
                if msg_type == "verification_result":
                    await self._ack_result(ws, event)
            return True
        if msg_type in {
            "verification_continuity_error",
            "verification_calculation_error",
        }:
            if self.session.client_roles.get(ws) != "verification_runner":
                return True
            reason = str(
                event.get("error_message") or event.get("error_type") or msg_type
            )
            self._log({
                "event": msg_type,
                "generation_id": event.get("generation_id"),
                "candle_timestamp_ms": event.get("candle_timestamp_ms"),
                "expected_timestamp_ms": event.get("expected_timestamp_ms"),
                "received_timestamp_ms": event.get("received_timestamp_ms"),
                "error_type": event.get("error_type"),
                "error_message": event.get("error_message"),
            })
            await self.request_recovery(reason)
            return True
        return False

    async def _handle_runner_ready(
        self,
        ws: WebSocket,
        event: dict[str, Any],
    ) -> None:
        if self.session.client_roles.get(ws) != "verification_runner":
            return
        if event.get("generation_id") != self.generation_id:
            return
        self.runner_ready = True
        self.initialized = True
        self.connection_lost = False
        self.source_hashes = dict(event.get("source_hashes") or {})
        self._cancel_warmup_timeout()
        self.last_error = None
        self.state_updated_at = datetime.now(UTC).isoformat()
        self._log({
            "event": "verification_runner_ready",
            "generation_id": self.generation_id,
            "calculated_through": event.get("calculated_through"),
            "pending_bar_timestamp_ms": event.get("pending_bar_timestamp_ms"),
            "source_hashes": event.get("source_hashes") or {},
        })
        try:
            calculated_through_ms = int(event["calculated_through"]) * 1000
            pending_bar_timestamp_ms = int(event["pending_bar_timestamp_ms"])
        except (KeyError, TypeError, ValueError):
            calculated_through_ms = None
            pending_bar_timestamp_ms = None
        replayed, replay_reason = await self._replay_backlog(
            ws,
            calculated_through_ms=calculated_through_ms,
            pending_bar_timestamp_ms=pending_bar_timestamp_ms,
        )
        if not replayed:
            self._log({
                "event": "verification_warmup_replay_failed",
                "generation_id": self.generation_id,
                "reason": replay_reason,
            })
            self.reset_generation()
            self.last_error = replay_reason
            await self.send_warmup(ws)
            return
        if self.on_ready is not None:
            await self.on_ready(self.session)
        await self.session._notify_status()

    async def on_primary_ready(self) -> None:
        self._rewarm_pending = False
        await self.send_warmup()

    async def on_script_info(self) -> None:
        if not self._rewarm_pending:
            return
        self._rewarm_pending = False
        await self.send_warmup()

    def on_primary_reset(self) -> None:
        self.reset_generation()
        self._rewarm_pending = True

    async def send_warmup(self, ws: WebSocket | None = None) -> None:
        if self.runner_count <= 0 or not self.session.runner_ready:
            return
        async with self.session.feed.state.lock:
            live_bars = [list(bar) for bar in self.session.feed.state.live_bars[-2:]]
        if len(live_bars) == 2:
            self._initial_pending_timestamp_ms = int(live_bars[-1][0])
            self._initial_primary_pending_resolved = False
        else:
            self._initial_pending_timestamp_ms = None
            self._initial_primary_pending_resolved = True
        payload = {
            "type": "verification_warmup",
            "ohlcv_path": str(self.session.feed.paths.ohlcv_path),
            "toml_path": str(self.session.feed.paths.toml_path),
            "confirmed_bar_and_new_bar": live_bars if len(live_bars) == 2 else None,
            "generation_id": self.generation_id,
        }
        self.runner_ready = False
        self._cancel_warmup_timeout()
        self._warmup_timeout_task = asyncio.create_task(
            self._warmup_timeout(self.generation_id),
            name=f"verification-warmup-timeout:{self.session.spec.id}",
        )
        if ws is not None:
            await self.session.ws_manager.send(ws, payload)
        else:
            await self._send_to_runners(payload)
        self._log({
            "event": "verification_warmup_dispatched",
            "generation_id": self.generation_id,
            "pending_bar_timestamp_ms": (
                int(live_bars[-1][0]) if live_bars else None
            ),
        })

    async def _send_to_runners(self, payload: dict[str, Any]) -> None:
        for ws, role in list(self.session.client_roles.items()):
            if role == "verification_runner":
                await self.session.ws_manager.send(ws, payload)

    async def _dispatch_payload_locked(
        self,
        ws: WebSocket | None,
        payload: dict[str, Any],
    ) -> None:
        timestamp_ms = int(payload["candle_timestamp_ms"])
        if (
            self._last_dispatched_timestamp_ms is not None
            and timestamp_ms <= self._last_dispatched_timestamp_ms
        ):
            return
        event = dict(payload)
        event["generation_id"] = self.generation_id
        if ws is not None:
            await self.session.ws_manager.send(ws, event)
        else:
            await self._send_to_runners(event)
        self._last_dispatched_timestamp_ms = timestamp_ms
        self._log({
            "event": "verification_candle_dispatched",
            "generation_id": self.generation_id,
            "authoritative_source": event.get("authoritative_source"),
            "candle_timestamp_ms": timestamp_ms,
            "confirmed_bar": event["confirmed_bar_and_new_bar"][0],
            "new_bar": event["confirmed_bar_and_new_bar"][1],
        })

    async def _replay_backlog(
        self,
        ws: WebSocket,
        *,
        calculated_through_ms: int | None,
        pending_bar_timestamp_ms: int | None,
    ) -> tuple[bool, str]:
        if calculated_through_ms is None or pending_bar_timestamp_ms is None:
            return False, "verification warm-up cursor is missing"
        pending = [
            payload
            for timestamp_ms, payload in sorted(self._candle_backlog.items())
            if timestamp_ms > calculated_through_ms
        ]
        expected_timestamp_ms = pending_bar_timestamp_ms
        for payload in pending:
            try:
                timestamp_ms = int(payload["candle_timestamp_ms"])
                next_timestamp_ms = int(payload["confirmed_bar_and_new_bar"][1][0])
            except (KeyError, IndexError, TypeError, ValueError):
                return False, "verification candle backlog is invalid"
            if timestamp_ms != expected_timestamp_ms:
                return False, "verification candle backlog has a gap after warm-up"
            expected_timestamp_ms = next_timestamp_ms

        async with self._protocol_lock:
            self._last_dispatched_timestamp_ms = calculated_through_ms
            for payload in pending:
                await self._dispatch_payload_locked(ws, payload)
        return True, ""

    async def handle_candle(self, payload: dict[str, Any]) -> None:
        try:
            timestamp_ms = int(payload["candle_timestamp_ms"])
        except (KeyError, TypeError, ValueError):
            return
        event = {
            "type": "verification_run_ready",
            "authoritative_source": payload.get("authoritative_source"),
            "candle_timestamp_ms": timestamp_ms,
            "confirmed_bar_and_new_bar": [
                payload.get("confirmed_bar"),
                payload.get("new_bar"),
            ],
        }
        self._candle_backlog[timestamp_ms] = event
        self._latest_authoritative_timestamp_ms = max(
            timestamp_ms,
            self._latest_authoritative_timestamp_ms or timestamp_ms,
        )
        while len(self._candle_backlog) > _MAX_CANDLE_BACKLOG:
            self._candle_backlog.pop(min(self._candle_backlog))

        if not self.runner_ready or self.runner_count <= 0:
            self._log({
                "event": "verification_candle_queued",
                "reason": "verification runner is disconnected or not ready",
                "generation_id": self.generation_id,
                "candle_timestamp_ms": timestamp_ms,
            })
            return
        async with self._protocol_lock:
            await self._dispatch_payload_locked(None, event)

    def _store_result(self, event: dict[str, Any], timestamp_ms: int) -> None:
        target = (
            self._primary_results
            if event.get("type") == "primary_result"
            else self._finalized_results
        )
        target[timestamp_ms] = dict(event)
        while len(target) > _MAX_RESULTS:
            expired_timestamp = next(iter(target))
            target.pop(expired_timestamp)
            if target is self._primary_results:
                self._primary_delivery_context.pop(expired_timestamp, None)
        if event.get("type") == "primary_result":
            self._primary_delivery_context[timestamp_ms] = {
                "webhook_config": dict(self.session.spec.webhook),
                "script_title": str(
                    self.session.chart_info.get("script_title") or ""
                ),
            }
            self.session.feed.record_verification_primary_result(event)
        self._log({
            "event": event.get("type"),
            "generation_id": event.get("generation_id"),
            "candle_timestamp_ms": timestamp_ms,
            "confirmed_bar": event.get("confirmed_bar"),
            "intents": event.get("intents") or [],
            "plot_values": event.get("plot_values"),
            "source_hashes": event.get("source_hashes") or {},
            "result_status": event.get("result_status"),
            "reason": event.get("reason"),
            "authoritative_source": event.get("authoritative_source"),
        })

    def _compare_timestamp(self, timestamp_ms: int, generation_id: str) -> None:
        primary = self._primary_results.get(timestamp_ms)
        finalized = self._finalized_results.get(timestamp_ms)
        if primary is None or finalized is None:
            return
        fingerprint, comparison = compare_results(
            primary,
            finalized,
            generation_id=generation_id,
            timestamp_ms=timestamp_ms,
        )
        if fingerprint in self._comparison_fingerprints:
            return
        self._comparison_fingerprints.add(fingerprint)
        comparison["supplemental_delivery_enabled"] = self.delivery is not None
        self._log(comparison)
        self._enqueue_supplemental_delivery(
            primary,
            finalized,
            comparison,
            timestamp_ms,
        )

    def _enqueue_supplemental_delivery(
        self,
        primary: dict[str, Any],
        finalized: dict[str, Any],
        comparison: dict[str, Any],
        timestamp_ms: int,
    ) -> None:
        if (
            self.delivery is None
            or not comparison.get("primary_result_available")
            or not comparison.get("source_hashes_matched")
            or self._rewarm_pending
            or self.connection_lost
            or not self.initialized
            or not self.runner_ready
        ):
            return
        authoritative_source = str(
            finalized.get("authoritative_source") or ""
        ).strip()
        if not authoritative_source:
            return

        context = self._primary_delivery_context.get(timestamp_ms) or {}
        toggles = primary.get("notification_toggles") or {}
        common = {
            "session_id": self.session.spec.id,
            "script_title": str(context.get("script_title") or ""),
            "exchange": self.session.spec.exchange,
            "symbol": self.session.spec.symbol,
            "timeframe": self.session.spec.timeframe,
            "candle_timestamp_ms": timestamp_ms,
            "primary_bar": comparison.get("primary_confirmed_bar"),
            "finalized_bar": comparison.get("finalized_confirmed_bar"),
            "bar_difference": dict(
                comparison.get("confirmed_bar_difference") or {}
            ),
            "authoritative_source": authoritative_source,
            "notification_toggles": {
                "webhook": bool(toggles.get("webhook")),
                "telegram": bool(toggles.get("telegram")),
            },
            "webhook_config": dict(context.get("webhook_config") or {}),
            "on_result": self._log,
        }
        for discrepancy, key in (
            ("missing", "missing_order_signals"),
            ("primary_only", "primary_only_order_signals"),
        ):
            if discrepancy == "missing":
                enabled = bool(
                    common["notification_toggles"]["webhook"]
                    or common["notification_toggles"]["telegram"]
                )
            else:
                enabled = bool(common["notification_toggles"]["telegram"])
            if not enabled:
                continue
            for signal in comparison.get(key) or []:
                self.delivery.enqueue(VerificationDeliveryRequest(
                    discrepancy=discrepancy,
                    order_signal=dict(signal),
                    **common,
                ))

    def _resolve_initial_primary_pending(
        self,
        event: dict[str, Any],
        timestamp_ms: int,
    ) -> None:
        pending_timestamp_ms = self._initial_pending_timestamp_ms
        if (
            self._initial_primary_pending_resolved
            or pending_timestamp_ms is None
            or timestamp_ms < pending_timestamp_ms
        ):
            return
        self._initial_primary_pending_resolved = True
        if timestamp_ms == pending_timestamp_ms:
            return
        if pending_timestamp_ms in self._primary_results:
            return

        synthetic = {
            "type": "primary_result",
            "generation_id": self.generation_id,
            "candle_timestamp_ms": pending_timestamp_ms,
            "intents": [],
            "plot_values": None,
            "source_hashes": event.get("source_hashes") or {},
            "result_status": "not_calculated",
            "reason": "startup_partial_bar_skipped",
        }
        self._store_result(synthetic, pending_timestamp_ms)
        self._compare_timestamp(pending_timestamp_ms, self.generation_id)

    async def record_result(self, event: dict[str, Any]) -> None:
        generation_id = str(event.get("generation_id") or "")
        if generation_id != self.generation_id:
            return
        try:
            timestamp_ms = int(event["candle_timestamp_ms"])
        except (KeyError, TypeError, ValueError):
            return
        self._store_result(event, timestamp_ms)
        if event.get("type") == "primary_result":
            self._resolve_initial_primary_pending(event, timestamp_ms)
        self._compare_timestamp(timestamp_ms, generation_id)

    def _log(self, event: dict[str, Any]) -> None:
        feed = self.session.feed.spec
        log_session_diagnostic(
            self.session.paths.plot_path.parent,
            self.session.spec.id,
            event,
            feed={
                "id": feed.id,
                "provider": feed.provider,
                "exchange": feed.exchange,
                "symbol": feed.symbol,
                "timeframe": feed.timeframe,
                "market_type": feed.market_type,
            },
        )
