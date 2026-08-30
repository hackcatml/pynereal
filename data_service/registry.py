from __future__ import annotations

import asyncio
import sqlite3
import shutil
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

from config import MAX_SESSIONS, FeedSpec, SessionSpec, save_sessions
from collector_loop import fix_missing_bars_loop, watch_trades_loop
from file_update_loop import _to_thread_cancel_safe, file_update_loop
from verification import FinalizedCandleProbe, VerificationDeliveryService
from prerun_scheduler import assign_prerun_offsets
from runner_supervisor import RunnerSupervisor
from runtime import Feed, Session
from tv_logos import TradingViewLogoResolver
from ws_manager import WSManager


class SessionLimitError(Exception):
    pass


class SessionExistsError(Exception):
    pass


class SessionNotFoundError(Exception):
    pass


class HistoryNotReadyError(Exception):
    pass


class SessionOrderError(Exception):
    pass


class RunnerActiveError(Exception):
    pass


class VerificationRunnerUnavailableError(Exception):
    pass


class SessionRegistry:
    """Owns Feeds (one per market, shared) and Sessions (one per strategy), their
    background tasks, the runner supervisor, and the dashboard (/ws/hub) push.

    Multiple Sessions on the same (provider, exchange, symbol, timeframe) share a
    single Feed, so the same market is only collected/downloaded once."""

    def __init__(
        self,
        port: int,
        *,
        verification_delivery_path: Path | None = None,
    ) -> None:
        self.feeds: Dict[str, Feed] = {}
        self.sessions: Dict[str, Session] = {}
        self.hub_ws = WSManager()  # dashboard clients on /ws/hub
        self.supervisor = RunnerSupervisor(port=port, on_change=self.notify_hub)
        self.logo_resolver = TradingViewLogoResolver()
        self.logo_tasks: Dict[str, asyncio.Task] = {}
        self.ai_instruction_handler: Optional[
            Callable[[Session, dict], Awaitable[None]]
        ] = None
        self.strategy_evaluation_enabled = False
        self.verification_delivery = (
            VerificationDeliveryService(verification_delivery_path)
            if verification_delivery_path is not None
            else None
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get(self, session_id: str) -> Optional[Session]:
        return self.sessions.get(session_id)

    def runner_status(self, session_id: str) -> str:
        sup = self.supervisor.status(session_id)
        if sup == "starting":
            # process alive: amber while spawning/connecting/pre-running, green only
            # once the runner reports its first pre_run is done (chart plots ready).
            s = self.sessions.get(session_id)
            if s is not None and s.runner_count > 0 and s.runner_ready:
                return "running"
            return "starting"
        return sup

    def verification_status(self, session: Session) -> dict:
        enabled = self.supervisor.verification_enabled
        verification = session.verification
        if not enabled:
            return {"enabled": False, "status": "disabled"}

        primary_status = self.runner_status(session.spec.id)
        process_status = self.supervisor.verification_process_status(session.spec.id)
        recovery = self.supervisor.verification_recovery_info(session.spec.id)
        if primary_status in {"stopped", "crashed"}:
            status = "stopped"
        elif process_status == "recovering":
            status = "recovering"
        elif verification.runner_count > 0 and verification.runner_ready:
            status = "running"
        elif verification.runner_count > 0:
            status = "warming_up"
        elif verification.connection_lost and process_status == "starting":
            status = "reconnecting"
        elif process_status == "crashed":
            status = "crashed"
        elif process_status == "starting":
            status = "starting"
        else:
            status = process_status

        next_retry_at = recovery.get("next_retry_at")
        return {
            "enabled": True,
            "status": status,
            "connected": verification.runner_count > 0,
            "ready": verification.runner_ready,
            "reason": recovery.get("reason") or verification.last_error,
            "attempt": recovery.get("attempt", 0),
            "next_retry_at": (
                int(float(next_retry_at) * 1000) if next_retry_at is not None else None
            ),
            "updated_at": verification.state_updated_at,
        }

    def snapshots(self) -> List[dict]:
        out = []
        for s in self.sessions.values():
            snap = s.snapshot()
            snap["runner"] = self.runner_status(s.spec.id)
            snap["verification"] = self.verification_status(s)
            out.append(snap)
        return out

    def _rebalance_prerun_schedule(self) -> None:
        assignments = assign_prerun_offsets(
            session.spec for session in self.sessions.values()
        )
        for session_id, assignment in assignments.items():
            session = self.sessions.get(session_id)
            if session is not None:
                session.set_prerun_assignment(
                    assignment.offset_seconds,
                    assignment.duplicate,
                )
        for feed in self.feeds.values():
            offsets = [
                session.prerun_effective_offset_seconds
                for session in feed.subscribers.values()
            ]
            if offsets:
                feed.prerun_prepare_offset_seconds = min(offsets)

    def retry_missing_symbol_logos(self) -> None:
        """Retry logo resolution when a dashboard reconnects after an earlier miss."""
        for session in list(self.sessions.values()):
            if (session.logo_info.get("symbol_logo_url") or "").strip():
                continue
            task = self.logo_tasks.get(session.spec.id)
            if task is not None and not task.done():
                continue
            self._schedule_logo_resolution(session)

    def set_ai_instruction_handler(
        self,
        handler: Callable[[Session, dict], Awaitable[None]],
    ) -> None:
        self.ai_instruction_handler = handler
        for session in self.sessions.values():
            session.on_ai_instruction = handler

    def set_strategy_evaluation_enabled(self, enabled: bool) -> None:
        self.strategy_evaluation_enabled = bool(enabled)
        for session in self.sessions.values():
            session.strategy_evaluation_enabled = self.strategy_evaluation_enabled

    def _schedule_logo_resolution(self, session: Session) -> None:
        task = self.logo_tasks.pop(session.spec.id, None)
        if task is not None:
            task.cancel()
        self.logo_tasks[session.spec.id] = asyncio.create_task(self._resolve_session_logos(session))

    async def _resolve_session_logos(self, session: Session) -> None:
        session_id = session.spec.id
        try:
            info = await self.logo_resolver.resolve(session.spec.exchange, session.spec.symbol)
            if self.sessions.get(session_id) is not session:
                return
            session.logo_info.update(info)
            await self.notify_hub()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[registry] failed to resolve TradingView logos for {session_id}: {e}")
        finally:
            task = self.logo_tasks.get(session_id)
            if task is asyncio.current_task():
                self.logo_tasks.pop(session_id, None)

    # ------------------------------------------------------------------
    # Feed lifecycle (shared data layer)
    # ------------------------------------------------------------------
    async def _file_update_loop_with_recovery(
        self,
        feed: Feed,
        history_ready_event: asyncio.Event,
    ) -> None:
        delay = 1.0
        attempt = 0
        while True:
            try:
                await file_update_loop(
                    config=feed.spec,
                    ohlcv_path=feed.paths.ohlcv_path,
                    toml_path=feed.paths.toml_path,
                    state=feed.state,
                    emit_event=feed.emit_event,
                    get_prerun_offset_seconds=lambda: feed.prerun_prepare_offset_seconds,
                    history_ready_event=history_ready_event,
                )
                return
            except asyncio.CancelledError:
                raise
            except sqlite3.OperationalError as e:
                if "database is locked" not in str(e).lower() or history_ready_event.is_set():
                    raise
                attempt += 1
                previous_error = feed.collector_error
                retry_error = f"file_update_loop: {e}"
                feed.collector_error = retry_error
                print(
                    f"[feed {feed.spec.id}] SQLite cache locked during startup; "
                    f"retrying file updater in {delay:g}s (attempt {attempt})"
                )
                await self.notify_hub()
                await asyncio.sleep(delay)
                if feed.collector_error == retry_error:
                    feed.collector_error = previous_error
                    await self.notify_hub()
                delay = min(delay * 2, 30.0)

    def _start_file_update_task(
        self,
        feed: Feed,
        *,
        history_ready_event: asyncio.Event | None = None,
    ) -> asyncio.Task:
        history_ready_event = history_ready_event or feed.history_ready_event
        task = asyncio.create_task(self._guard_feed(
            feed,
            "file_update_loop",
            self._file_update_loop_with_recovery(feed, history_ready_event),
        ))
        feed.tasks["file_update_loop"] = task
        return task

    def _start_feed_tasks(self, feed: Feed) -> None:
        spec = feed.spec
        feed.tasks = {
            "watch_trades_loop": asyncio.create_task(self._guard_feed(feed, "watch_trades_loop", watch_trades_loop(
                spec.exchange, spec.symbol, spec.timeframe, feed.state, feed.broadcast_bar,
                on_trades=feed.broadcast_trades,
                market_type=spec.market_type))),
            "fix_missing_bars_loop": asyncio.create_task(self._guard_feed(feed, "fix_missing_bars_loop", fix_missing_bars_loop(
                spec.exchange, spec.timeframe, feed.state))),
        }
        self._start_file_update_task(feed)

    @staticmethod
    def _has_verification_probe_consumer(feed: Feed) -> bool:
        return any(
            session.verification.enabled and session.verification.runner_count > 0
            for session in feed.subscribers.values()
        )

    def _start_verification_probe(self, feed: Feed) -> None:
        current = feed.finalized_candle_probe
        if current is not None:
            task = feed.tasks.get(current.task_name)
            if task is not None and not task.done():
                return
            feed.tasks.pop(current.task_name, None)

        spec = feed.spec
        probe = FinalizedCandleProbe(
            exchange_name=spec.exchange,
            symbol=spec.symbol,
            timeframe=spec.timeframe,
            market_type=spec.market_type,
            on_event=feed.log_market_data_diagnostic,
            on_authoritative=feed.queue_verification_candle,
        )
        feed.seed_verification_primary_results(probe)
        feed.finalized_candle_probe = probe
        probe_name = probe.task_name
        feed.tasks[probe_name] = asyncio.create_task(
            self._guard_probe(feed, probe_name, probe.run())
        )

    async def _stop_verification_probe(self, feed: Feed) -> None:
        probe = feed.finalized_candle_probe
        feed.finalized_candle_probe = None
        feed.clear_verification_primary_results()
        if probe is None:
            return
        task = feed.tasks.pop(probe.task_name, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _sync_verification_probe(self, feed: Feed) -> None:
        async with feed._verification_probe_lock:
            if self._has_verification_probe_consumer(feed):
                self._start_verification_probe(feed)
            else:
                await self._stop_verification_probe(feed)

    async def _guard_probe(self, feed: Feed, name: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            feed.log_market_data_diagnostic({
                "event": "finalized_candle_probe_crashed",
                "source": name,
                "error_type": type(e).__name__,
                "error_message": str(e)[:500],
            })
            print(f"[feed {feed.spec.id}] {name} crashed: {e}")

    async def _guard_feed(self, feed: Feed, name: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            feed.collector_error = f"{name}: {e}"
            print(f"[feed {feed.spec.id}] {name} crashed: {e}")
            print(traceback.format_exc())
            await self.notify_hub()

    def _get_or_create_feed(self, session_spec: SessionSpec) -> Feed:
        fid = session_spec.feed_id
        feed = self.feeds.get(fid)
        if feed is None:
            feed = Feed(FeedSpec.from_session(session_spec))
            self.feeds[fid] = feed
            self._start_feed_tasks(feed)
            print(f"[registry] feed created: {fid}")
        return feed

    async def _teardown_feed_if_idle(self, feed: Feed) -> None:
        if feed.subscribers:
            return
        tasks = list(feed.tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        feed.tasks.clear()
        self.feeds.pop(feed.spec.id, None)
        print(f"[registry] feed torn down (idle): {feed.spec.id}")

    async def _restart_file_update(self, feed: Feed) -> None:
        task = feed.tasks.pop("file_update_loop", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        if (feed.collector_error or "").startswith("file_update_loop:"):
            feed.collector_error = None
        async with feed.state.lock:
            feed.state.pending_prerun_event = None

        feed.history_ready_event.clear()
        task = self._start_file_update_task(
            feed,
            history_ready_event=feed.history_ready_event,
        )
        ready_wait = asyncio.create_task(feed.history_ready_event.wait())
        try:
            done, _ = await asyncio.wait(
                {task, ready_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_wait not in done:
                raise RuntimeError(feed.collector_error or "file update stopped before history was ready")
        finally:
            if not ready_wait.done():
                ready_wait.cancel()
            await asyncio.gather(ready_wait, return_exceptions=True)
        print(f"[registry] file updater restarted: {feed.spec.id}")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    async def add_session(self, spec: SessionSpec, *, persist: bool = True) -> Session:
        if spec.id in self.sessions:
            raise SessionExistsError(spec.id)
        if len(self.sessions) >= MAX_SESSIONS:
            raise SessionLimitError(f"max {MAX_SESSIONS} sessions reached")
        feed = self._get_or_create_feed(spec)
        session = Session(
            spec,
            feed,
            strategy_evaluation_enabled=self.strategy_evaluation_enabled,
            verification_enabled=self.supervisor.verification_enabled,
            verification_delivery=self.verification_delivery,
        )
        session.on_status_change = self.notify_hub
        session.on_spec_change = self._persist_and_notify
        session.on_ai_instruction = self.ai_instruction_handler
        session.verification.on_recovery = self._request_verification_recovery
        session.verification.on_connected = self._mark_verification_connected
        session.verification.on_disconnected = self._mark_verification_disconnected
        session.verification.on_ready = self._mark_verification_ready
        feed.subscribers[spec.id] = session
        self.sessions[spec.id] = session
        self._rebalance_prerun_schedule()
        self._schedule_logo_resolution(session)
        if persist:
            self._persist()
        await self.notify_hub()
        return session

    async def remove_session(self, session_id: str, *, persist: bool = True,
                             cleanup_output: bool = False) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        logo_task = self.logo_tasks.pop(session_id, None)
        if logo_task is not None:
            logo_task.cancel()
            await asyncio.gather(logo_task, return_exceptions=True)
        await self.supervisor.stop(session_id)
        feed = session.feed
        feed.subscribers.pop(session_id, None)
        await self._sync_verification_probe(feed)
        del self.sessions[session_id]
        self._rebalance_prerun_schedule()
        await self._teardown_feed_if_idle(feed)
        if cleanup_output:
            # Remove this session's output dir (plot.csv / script_hash.csv / runner.log).
            out_dir = session.paths.plot_path.parent
            shutil.rmtree(out_dir, ignore_errors=True)
        if persist:
            self._persist()
        await self.notify_hub()

    async def reorder_sessions(self, session_ids: list[str]) -> List[dict]:
        current_ids = list(self.sessions)
        if len(session_ids) != len(current_ids) or len(set(session_ids)) != len(session_ids):
            raise SessionOrderError("session order must contain every session exactly once")
        if set(session_ids) != set(current_ids):
            raise SessionOrderError("session order contains unknown or missing session ids")

        previous_sessions = self.sessions
        self.sessions = {session_id: previous_sessions[session_id] for session_id in session_ids}
        self._rebalance_prerun_schedule()
        try:
            self._persist()
        except Exception:
            self.sessions = previous_sessions
            self._rebalance_prerun_schedule()
            raise
        await self.notify_hub()
        return self.snapshots()

    async def update_script_name(self, session_id: str, script_name: str) -> SessionSpec:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if self.supervisor.is_active(session_id) or session.runner_count > 0:
            raise RunnerActiveError("stop the runner before changing its script")
        if session.spec.script_name == script_name:
            return session.spec

        for other in self.sessions.values():
            if other is session:
                continue
            if other.spec.feed_id == session.spec.feed_id and other.spec.script_name == script_name:
                raise SessionExistsError(other.spec.id)

        previous_spec = session.spec
        next_spec = replace(previous_spec, script_name=script_name)
        session.spec = next_spec
        try:
            self._persist()
        except Exception:
            session.spec = previous_spec
            raise

        session.reconfigure_script(next_spec)
        for path in (session.paths.plot_path, session.paths.hash_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[registry] failed to clear stale strategy output {path}: {exc}")
        await session.send_to_charts({"type": "script_modified"})
        await self.notify_hub()
        return next_spec

    async def update_webhook(self, session_id: str, *, enabled: bool | None = None,
                             telegram_notification: bool | None = None,
                             url: str | None = None,
                             telegram_token: str | None = None,
                             telegram_chat_id: str | None = None) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        session.spec = session.spec.with_webhook(
            enabled=enabled, telegram_notification=telegram_notification,
            url=url, telegram_token=telegram_token, telegram_chat_id=telegram_chat_id)
        # Keep the feed subscriber ref pointing at the updated session object (same instance).
        self._persist()
        await session.push_webhook_config()
        await self.notify_hub()
        return dict(session.spec.webhook)

    async def update_prerun_schedule(
        self,
        session_id: str,
        mode: object,
        offset_seconds: object = None,
    ) -> SessionSpec:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        previous_specs = {key: value.spec for key, value in self.sessions.items()}
        session.spec = session.spec.with_prerun_schedule(mode, offset_seconds)
        self._rebalance_prerun_schedule()
        try:
            self._persist()
        except Exception:
            for key, spec in previous_specs.items():
                current = self.sessions.get(key)
                if current is not None:
                    current.spec = spec
            self._rebalance_prerun_schedule()
            raise
        await self.notify_hub()
        return session.spec

    async def update_history_since(self, session_id: str, history_since: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        feed = session.feed
        # The ohlcv file is shared per feed, so every session on this market
        # has to move to the new start together.
        targets = list(feed.subscribers.values())
        if all(s.spec.history_since == history_since for s in targets):
            return

        previous = [(s, s.spec) for s in targets]
        for s in targets:
            s.spec = s.spec.with_history_since(history_since)
        try:
            self._persist()
        except Exception:
            for s, spec in previous:
                s.spec = spec
            raise

        # Runners must not hold the ohlcv file while file_update_loop
        # regenerates it, and the strategies have to recompute over the new
        # window anyway — stop them, restart only the file updater, bring them back.
        running = [
            s for s in targets
            if self.supervisor.status(s.spec.id) in ("running", "starting")
        ]
        for s in running:
            await self.supervisor.stop(s.spec.id)

        feed.spec = replace(feed.spec, history_since=history_since)
        await self._restart_file_update(feed)

        for s in running:
            s.history_resync_pending = True
            await self.supervisor.start(s.spec, s.paths)
        await self.notify_hub()

    async def repair_data_integrity(
        self,
        session_id: str,
        repair: Callable[[], dict],
    ) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        feed = session.feed
        targets = list(feed.subscribers.values())
        running = [
            target
            for target in targets
            if self.supervisor.status(target.spec.id) in ("running", "starting")
        ]
        for target in running:
            await self.supervisor.stop(target.spec.id)

        file_task = feed.tasks.pop("file_update_loop", None)
        if file_task is not None:
            file_task.cancel()
            await asyncio.gather(file_task, return_exceptions=True)

        result: dict | None = None
        repair_error: BaseException | None = None
        try:
            result = await _to_thread_cancel_safe(repair)
        except BaseException as exc:
            repair_error = exc

        restart_error: BaseException | None = None
        try:
            await self._restart_file_update(feed)
        except BaseException as exc:
            restart_error = exc

        if restart_error is None:
            for target in running:
                target.history_resync_pending = True
                await self.supervisor.start(target.spec, target.paths)
        await self.notify_hub()

        if repair_error is not None:
            raise repair_error
        if restart_error is not None:
            raise restart_error
        return result or {}

    async def update_manual_alert_templates(self, session_id: str, templates: list[dict]) -> list[dict]:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        session.spec = session.spec.with_manual_alert_templates(templates)
        self._persist()
        return [dict(t) for t in session.spec.manual_alert_templates]

    async def update_manual_alert_triggers(self, session_id: str, triggers: object) -> list[dict]:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        session.spec = session.spec.with_manual_alert_triggers(triggers)
        session.reset_manual_alert_trigger_gate()
        self._persist()
        await session.push_manual_alert_trigger()
        await self.notify_hub()
        return [dict(t) for t in session.spec.manual_alert_triggers]

    async def update_manual_alert_configuration(
        self,
        session_id: str,
        *,
        templates: list[dict],
        triggers: object,
    ) -> dict[str, list[dict]]:
        """Persist templates and triggers together for one AI-driven alert setup."""
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        previous_spec = session.spec
        session.spec = (
            session.spec
            .with_manual_alert_templates(templates)
            .with_manual_alert_triggers(triggers)
        )
        try:
            self._persist()
        except Exception:
            session.spec = previous_spec
            raise

        session.reset_manual_alert_trigger_gate()
        await session.push_manual_alert_trigger()
        await self.notify_hub()
        return {
            "templates": [dict(t) for t in session.spec.manual_alert_templates],
            "triggers": [dict(t) for t in session.spec.manual_alert_triggers],
        }

    async def delete_manual_alert_triggers(
        self,
        selections: dict[str, set[str] | None],
    ) -> list[dict]:
        """Delete selected or all triggers from multiple sessions in one save."""
        unknown = [session_id for session_id in selections if session_id not in self.sessions]
        if unknown:
            raise SessionNotFoundError(unknown[0])

        changes: list[tuple[Session, object, list[dict]]] = []
        deleted: list[dict] = []
        for session_id, trigger_ids in selections.items():
            session = self.sessions[session_id]
            current = [dict(trigger) for trigger in session.spec.manual_alert_triggers]
            if trigger_ids is None:
                removed = current
                remaining: list[dict] = []
            else:
                removed = [
                    trigger for trigger in current
                    if str(trigger.get("id") or "") in trigger_ids
                ]
                remaining = [
                    trigger for trigger in current
                    if str(trigger.get("id") or "") not in trigger_ids
                ]
            if not removed:
                continue
            changes.append((session, session.spec, removed))
            session.spec = session.spec.with_manual_alert_triggers(remaining)

        if not changes:
            return []

        try:
            self._persist()
        except Exception:
            for session, previous_spec, _ in changes:
                session.spec = previous_spec
            raise

        for session, _, removed in changes:
            session.reset_manual_alert_trigger_gate()
            await session.push_manual_alert_trigger()
            deleted.extend({
                "session_id": session.spec.id,
                "exchange": session.spec.exchange,
                "symbol": session.spec.symbol,
                "timeframe": session.spec.timeframe,
                "trigger": dict(trigger),
            } for trigger in removed)
        await self.notify_hub()
        return deleted

    # ------------------------------------------------------------------
    # Runner control
    # ------------------------------------------------------------------
    async def start_runner(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if not session.feed.history_ready():
            raise HistoryNotReadyError(session_id)
        await self.supervisor.start(session.spec, session.paths)

    async def stop_runner(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self.supervisor.stop(session_id)
        await self._sync_verification_probe(session.feed)
        await self.notify_hub()

    async def restart_runner(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if not session.feed.history_ready():
            raise HistoryNotReadyError(session_id)
        await self.supervisor.restart(session.spec, session.paths)

    async def restart_verification_runner(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if not self.supervisor.verification_enabled:
            raise VerificationRunnerUnavailableError(
                "verification runner is disabled"
            )
        if not self.supervisor.is_active(session_id):
            raise VerificationRunnerUnavailableError(
                "start the strategy runner before restarting verification"
            )
        if not session.feed.history_ready():
            raise HistoryNotReadyError(session_id)
        await session.verification.prepare_recovery("manual restart")
        await self.supervisor.restart_verification(
            session.spec,
            session.paths,
            reason="manual restart",
        )
        await self.notify_hub()

    async def _request_verification_recovery(
        self,
        session: Session,
        reason: str,
    ) -> None:
        self.supervisor.request_verification_recovery(
            session.spec.id,
            reason=reason,
        )
        await self.notify_hub()

    async def _mark_verification_connected(self, session: Session) -> None:
        self.supervisor.mark_verification_connected(session.spec.id)
        await self._sync_verification_probe(session.feed)

    async def _mark_verification_disconnected(self, session: Session) -> None:
        self.supervisor.mark_verification_disconnected(session.spec.id)
        await self._sync_verification_probe(session.feed)

    async def _mark_verification_ready(self, session: Session) -> None:
        self.supervisor.mark_verification_ready(session.spec.id)

    # ------------------------------------------------------------------
    # Boot / shutdown
    # ------------------------------------------------------------------
    async def start_all(self, specs: List[SessionSpec]) -> None:
        for spec in specs[:MAX_SESSIONS]:
            try:
                await self.add_session(spec, persist=False)
            except Exception as e:
                print(f"[registry] failed to start session {spec.id}: {e}")
        # Initial persist is best-effort: a save failure must not crash hub boot.
        try:
            self._persist()
        except Exception as e:
            print(f"[registry] initial persist failed: {e}")

    async def shutdown(self) -> None:
        await self.supervisor.shutdown()
        if self.verification_delivery is not None:
            await self.verification_delivery.close()
        logo_tasks = list(self.logo_tasks.values())
        self.logo_tasks.clear()
        for t in logo_tasks:
            t.cancel()
        if logo_tasks:
            await asyncio.gather(*logo_tasks, return_exceptions=True)
        all_tasks = [t for feed in self.feeds.values() for t in feed.tasks.values()]
        for t in all_tasks:
            t.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Persistence + dashboard push
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        # Raises on failure so mutating API calls surface a 500 instead of
        # returning ok=true while sessions.json silently fails to update.
        save_sessions([s.spec for s in self.sessions.values()])

    async def _persist_and_notify(self) -> None:
        self._persist()
        await self.notify_hub()

    async def notify_hub(self) -> None:
        await self.hub_ws.broadcast_json({"type": "sessions", "sessions": self.snapshots()})
