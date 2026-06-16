from __future__ import annotations

import asyncio
import shutil
import traceback
from typing import Dict, List, Optional

from config import MAX_SESSIONS, FeedSpec, SessionSpec, save_sessions
from collector_loop import fix_missing_bars_loop, watch_trades_loop
from file_update_loop import file_update_loop
from runner_supervisor import RunnerSupervisor
from runtime import Feed, Session
from ws_manager import WSManager


class SessionLimitError(Exception):
    pass


class SessionExistsError(Exception):
    pass


class SessionNotFoundError(Exception):
    pass


class SessionRegistry:
    """Owns Feeds (one per market, shared) and Sessions (one per strategy), their
    background tasks, the runner supervisor, and the dashboard (/ws/hub) push.

    Multiple Sessions on the same (provider, exchange, symbol, timeframe) share a
    single Feed, so the same market is only collected/downloaded once."""

    def __init__(self, port: int) -> None:
        self.feeds: Dict[str, Feed] = {}
        self.sessions: Dict[str, Session] = {}
        self.hub_ws = WSManager()  # dashboard clients on /ws/hub
        self.supervisor = RunnerSupervisor(port=port, on_change=self.notify_hub)

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

    def snapshots(self) -> List[dict]:
        out = []
        for s in self.sessions.values():
            snap = s.snapshot()
            snap["runner"] = self.runner_status(s.spec.id)
            out.append(snap)
        return out

    # ------------------------------------------------------------------
    # Feed lifecycle (shared data layer)
    # ------------------------------------------------------------------
    def _start_feed_tasks(self, feed: Feed) -> None:
        spec = feed.spec
        feed.tasks = [
            asyncio.create_task(self._guard_feed(feed, "watch_trades_loop", watch_trades_loop(
                spec.exchange, spec.symbol, spec.timeframe, feed.state, feed.broadcast_bar))),
            asyncio.create_task(self._guard_feed(feed, "fix_missing_bars_loop", fix_missing_bars_loop(
                spec.exchange, spec.timeframe, feed.state))),
            asyncio.create_task(self._guard_feed(feed, "file_update_loop", file_update_loop(
                config=spec, ohlcv_path=feed.paths.ohlcv_path, toml_path=feed.paths.toml_path,
                state=feed.state, emit_event=feed.emit_event))),
        ]

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
        for t in feed.tasks:
            t.cancel()
        if feed.tasks:
            await asyncio.gather(*feed.tasks, return_exceptions=True)
        self.feeds.pop(feed.spec.id, None)
        print(f"[registry] feed torn down (idle): {feed.spec.id}")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    async def add_session(self, spec: SessionSpec, *, persist: bool = True) -> Session:
        if spec.id in self.sessions:
            raise SessionExistsError(spec.id)
        if len(self.sessions) >= MAX_SESSIONS:
            raise SessionLimitError(f"max {MAX_SESSIONS} sessions reached")
        feed = self._get_or_create_feed(spec)
        session = Session(spec, feed)
        session.on_status_change = self.notify_hub
        feed.subscribers[spec.id] = session
        self.sessions[spec.id] = session
        if persist:
            self._persist()
        await self.notify_hub()
        return session

    async def remove_session(self, session_id: str, *, persist: bool = True,
                             cleanup_output: bool = False) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self.supervisor.stop(session_id)
        feed = session.feed
        feed.subscribers.pop(session_id, None)
        del self.sessions[session_id]
        await self._teardown_feed_if_idle(feed)
        if cleanup_output:
            # Remove this session's output dir (plot.csv / script_hash.csv / runner.log).
            out_dir = session.paths.plot_path.parent
            shutil.rmtree(out_dir, ignore_errors=True)
        if persist:
            self._persist()
        await self.notify_hub()

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

    # ------------------------------------------------------------------
    # Runner control
    # ------------------------------------------------------------------
    async def start_runner(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self.supervisor.start(session.spec, session.paths)

    async def stop_runner(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self.supervisor.stop(session_id)
        await self.notify_hub()

    async def restart_runner(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self.supervisor.restart(session.spec, session.paths)

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
        # Autostart runners for sessions flagged autostart_runner (decision: boot restore).
        for s in list(self.sessions.values()):
            if s.spec.autostart_runner:
                try:
                    await self.start_runner(s.spec.id)
                except Exception as e:
                    print(f"[registry] autostart runner failed for {s.spec.id}: {e}")

    async def shutdown(self) -> None:
        await self.supervisor.shutdown()
        all_tasks = [t for feed in self.feeds.values() for t in feed.tasks]
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

    async def notify_hub(self) -> None:
        await self.hub_ws.broadcast_json({"type": "sessions", "sessions": self.snapshots()})
