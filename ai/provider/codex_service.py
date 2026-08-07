from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, CodexConfig
from openai_codex.errors import TransportClosedError
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    AgentMessageThreadItem,
    CommandExecutionThreadItem,
    ConfigReadResponse,
    DynamicToolCallThreadItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    McpToolCallThreadItem,
    MessagePhase,
    ReasoningEffort,
    TurnCompletedNotification,
    TurnStatus,
    WebSearchThreadItem,
)
from pydantic import BaseModel, ConfigDict, Field

from ai.scripts.dynamic_tools import AIDynamicTools

DEFAULT_DEVELOPER_INSTRUCTIONS = (
    "You are the AI assistant for the PyneReal dashboard. "
    "Treat exchanges and accounts as read-only. Do not place or cancel orders, "
    "change leverage, or perform any other account state mutation. "
    "You may change session state only through the dedicated tools when the user "
    "explicitly requests setting or deleting Manual Alert price triggers. For any Manual Alert request, "
    "A server-verified automated strategy instruction configured through the ai parameter of "
    "strategy.entry or strategy.close is an explicit user request. Execute only its stated scope "
    "and use the exact session context supplied by the server. "
    "first call get_manual_alert_context to inspect the active sessions and templates. "
    "If the session, price, alert template, deletion target, or deletion scope is unclear or "
    "could match more than one option, do not call a mutation tool. Ask for a human-readable distinction "
    "such as the exchange, timeframe, or strategy name. Never ask the user to provide a "
    "session_id. Resolve the user's symbol, company or asset name, or strategy description "
    "to a session from the context and use its ID internally. If the requested alert "
    "template does not exist in that session, ask the user for both the alert title and "
    "message format, then pass the custom template fields to set_manual_alert_trigger so "
    "the template and trigger are added together. Do not tell the user to add the template "
    "through the dashboard, and never guess or invent missing values. For an explicit trigger "
    "deletion request, map the user's description to active trigger IDs and call "
    "delete_manual_alert_triggers. If the user explicitly asks to delete all triggers in one "
    "session and then set a new trigger in that same session, call set_manual_alert_trigger once "
    "with replace_existing_triggers=true instead of performing separate delete and set calls. "
    "Deleting or replacing triggers must always preserve configured alert templates. Never delete "
    "a template unless the user explicitly requests template deletion through a dedicated tool. "
    "When an asset or position request does not specify an account or exchange, run the "
    "dedicated script for every account configured in providers.toml. Never include database "
    "connection details, API keys, secrets, or other credentials in a response. Intermediate "
    "progress for asset or position lookups must contain only user-relevant information such "
    "as the actual lookup target, meaningful progress, partial failures, or retries. Do not "
    "describe internal preparation such as reading instruction files, exploring the repository, "
    "selecting a workflow, reviewing execution options, or planning how to summarize the result. "
    "In the final response, do not list the procedure; present the lookup result immediately. "
    "Only when the user explicitly asks to send the result to Telegram, complete the lookup and "
    "then call send_telegram_message with a concise plain-text result. Report successful Telegram "
    "delivery only when the tool returns success. For evaluation or analysis of a running PyneReal "
    "strategy session, call get_session_evaluation_context before interpreting the strategy. If the "
    "user names a symbol, company, asset, or strategy rather than an exact internal ID, first call the "
    "tool without session_id, resolve exactly one human-readable match, and then call it again with "
    "that ID and wait_for_ready=true. If the user explicitly names a configured account, pass the "
    "human-readable account name in the account argument; that selection must not be replaced by "
    "another account. Otherwise omit account so the server matches configured accounts from current "
    "positions and recent order history. Treat the returned confirmed bars, simulation state, plots, "
    "trades, source, logs, calculation generation, and warnings as the authoritative session evidence. "
    "Do not treat the forming bar as confirmed. Use account_match as request-time evidence, respect "
    "ambiguous or no_match status, and clearly separate simulated state from real account state. Never "
    "persist or invent a static account binding. Do not invent "
    "strategy-specific state that is not exposed by source, plots, logs, orders, or trades. When visual "
    "confirmation would materially improve the evaluation, call capture_session_chart only with the "
    "exact ready generation returned by get_session_evaluation_context. For requests to check, refresh, add, update, or "
    "remove calendar schedules, first call get_calendar_context. If the user does not name a "
    "session, research every active session. "
    "Resolve company, asset, symbol, exchange, timeframe, or strategy descriptions to the exact "
    "session IDs returned by the tool and never ask the user for a session ID. Treat "
    "https://www.saveticker.com/calendar as a discovery source for event titles, then verify dates "
    "and details with public web search or authoritative company, exchange, filing, or economic "
    "calendar sources. If SaveTicker has no relevant item, search the web directly. Never invent an "
    "event or date. Unless the user specifies a period, refresh today through 90 days ahead. Use "
    "add_calendar_event for a request to add one specific event without changing other events. A "
    "server-provided calendar-date input must call add_calendar_event exactly once after verification "
    "and must never call replace_calendar_events. For a calendar refresh or range-wide schedule check, "
    "call replace_calendar_events for every requested session, including an empty event array for a "
    "researched session with no relevant schedule. Use the same date, time, and concise title in every "
    "affected session when one event applies to multiple sessions, and report only tool-confirmed saves. "
    "A server-provided calendar event forecast request is analysis-only: research "
    "and assess the specified event and affected sessions without calling calendar mutation tools or "
    "changing any account or repository state."
)
_MAX_PERSISTED_MESSAGES = 200
_MAX_CONTEXT_MESSAGES = 12
_MAX_PERSISTED_CONTENT_CHARS = 40_000
# dashboard chat defaults when the user has not picked anything yet
_PREFERRED_DEFAULT_MODEL = "gpt-5.6-sol"
_PREFERRED_DEFAULT_EFFORT = ReasoningEffort.medium.value
_AI_PERMISSION_PROFILE = "pynereal-ai-read-only"
_CODEX_LOGIN_TIMEOUT_SECONDS = 10 * 60


class _CodexModelInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    model: str
    display_name: str = Field(alias="displayName")
    description: str = ""
    hidden: bool = False
    is_default: bool = Field(default=False, alias="isDefault")
    supported_reasoning_efforts: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="supportedReasoningEfforts",
    )


class _CodexModelListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    data: list[_CodexModelInfo]


@dataclass(frozen=True)
class CodexStreamEvent:
    event: str
    payload: dict[str, Any]


_CHAT_STREAM_END = object()
ChatStateCallback = Callable[[], Awaitable[None]]
CodexAuthenticationResult = Literal["ready", "login_completed", "disabled"]


class CodexChatRun:
    """One server-owned chat turn with an optional live SSE subscriber."""

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:8]
        self._events: asyncio.Queue[CodexStreamEvent | object] = asyncio.Queue()
        self._subscribed = True

    def publish(self, event: CodexStreamEvent) -> None:
        if self._subscribed:
            self._events.put_nowait(event)

    def finish(self) -> None:
        if self._subscribed:
            self._events.put_nowait(_CHAT_STREAM_END)

    async def events(self) -> AsyncIterator[CodexStreamEvent]:
        try:
            while True:
                event = await self._events.get()
                if event is _CHAT_STREAM_END:
                    return
                yield event
        finally:
            self._subscribed = False
            while not self._events.empty():
                self._events.get_nowait()


class CodexService:
    """Own one long-running Codex app-server and its dashboard chat threads."""

    def __init__(
        self,
        project_root: Path,
        session_registry: Any,
        calendar_store: Any,
        developer_instructions: str = DEFAULT_DEVELOPER_INSTRUCTIONS,
        timeout_seconds: float = 600,
        chat_state_path: Path | None = None,
        startup_enabled: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.session_registry = session_registry
        self.dynamic_tools = AIDynamicTools(
            self.project_root,
            session_registry=session_registry,
            calendar_store=calendar_store,
        )
        self.file_tools = self.dynamic_tools.file_tools
        self.developer_instructions = (
            developer_instructions.rstrip() + " " + self._file_access_instructions()
        )
        self.timeout_seconds = timeout_seconds
        self.chat_state_path = (
            chat_state_path or self.project_root / "workdir" / "config" / "ai_chat.json"
        ).resolve()
        self._codex: AsyncCodex | None = None
        self._disabled = not startup_enabled
        self._lifecycle_lock = asyncio.Lock()
        self._conversations_lock = asyncio.Lock()
        self._models_lock = asyncio.Lock()
        self._shared_chat_lock = asyncio.Lock()
        self._strategy_instruction_locks: dict[str, asyncio.Lock] = {}
        self._chat_state_lock = asyncio.Lock()
        self._conversations: dict[str, AsyncThread] = {}
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._model_options: list[dict[str, Any]] | None = None
        self._shared_chat_tasks: set[asyncio.Task[None]] = set()
        self._pending_shared_chats = 0
        self._warm_thread_task: asyncio.Task[AsyncThread | None] | None = None
        self._chat_messages: list[dict[str, Any]] = []
        self._chat_conversation_id: str | None = None
        # model/effort picked in the dashboard; persisted so every browser
        # shares the same selection
        self._chat_model: str | None = None
        self._chat_effort: str | None = None
        self._load_chat_state()

    @property
    def enabled(self) -> bool:
        return not self._disabled

    @property
    def running(self) -> bool:
        return self._codex is not None and not self._disabled

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._codex is not None or self._disabled:
                return

            self.dynamic_tools.bind_loop(asyncio.get_running_loop())
            started_at = time.perf_counter()
            codex_bin = shutil.which("codex")
            codex: AsyncCodex | None = None
            try:
                codex = AsyncCodex(CodexConfig(
                    codex_bin=codex_bin,
                    cwd=str(self.project_root),
                    config_overrides=(
                        'web_search="live"',
                    ),
                ))
                self._install_dynamic_tool_handler(codex)
                await codex.__aenter__()
                authentication = await self._ensure_authenticated(codex)
                if authentication == "disabled":
                    await codex.close()
                    codex = None
                    self._disabled = True
                    self.dynamic_tools.unbind_loop()
                    print("[ai] Codex AI service disabled")
                    return
                if authentication == "login_completed":
                    print("[ai] Reloading Codex app-server after login...")
                    await codex.close()
                    codex = AsyncCodex(CodexConfig(
                        codex_bin=codex_bin,
                        cwd=str(self.project_root),
                        config_overrides=(
                            'web_search="live"',
                        ),
                    ))
                    self._install_dynamic_tool_handler(codex)
                    await codex.__aenter__()
                    account = await codex.account(refresh_token=True)
                    if account.account is None:
                        raise RuntimeError(
                            "Codex login was saved but the restarted app-server "
                            "did not load an authenticated account"
                        )
            except Exception:
                try:
                    if codex is not None:
                        await codex.close()
                finally:
                    self.dynamic_tools.unbind_loop()
                raise

            self._codex = codex
            self._warm_thread_task = asyncio.create_task(self._prepare_warm_thread(codex))
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            print(
                "[ai] Codex app-server started with the current local login "
                f"in {elapsed_ms:.0f}ms"
            )

    async def _ensure_authenticated(self, codex: AsyncCodex) -> CodexAuthenticationResult:
        account = await codex.account()
        if account.account is not None:
            return "ready"

        if not self._interactive_terminal_available():
            print(
                "[ai] Codex is not authenticated and no interactive terminal is available"
            )
            return "disabled"

        if not self._confirm_ai_service_use():
            return "disabled"

        print("[ai] Codex login is required. Starting device-code login...")
        login = await codex.login_chatgpt_device_code()
        print(f"[ai] Open {login.verification_url}")
        print(f"[ai] Enter code: {login.user_code}")
        print("[ai] Waiting for Codex login to complete...")
        try:
            async with asyncio.timeout(_CODEX_LOGIN_TIMEOUT_SECONDS):
                completion = await login.wait()
        except TimeoutError:
            await self._cancel_login(login)
            raise RuntimeError("Codex interactive login timed out") from None
        except asyncio.CancelledError:
            await self._cancel_login(login)
            raise

        if not completion.success:
            detail = completion.error or "unknown authentication error"
            raise RuntimeError(f"Codex interactive login failed: {detail}")
        print("[ai] Codex login completed")
        return "login_completed"

    @staticmethod
    async def _cancel_login(login: Any) -> None:
        try:
            await login.cancel()
        except Exception:
            pass

    @staticmethod
    def _interactive_terminal_available() -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    @staticmethod
    def _confirm_ai_service_use() -> bool:
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            previous_settings = termios.tcgetattr(fd)
        except (ImportError, OSError, AttributeError):
            return CodexService._confirm_ai_service_use_with_text()

        selected_yes = False
        rendered = False

        def render() -> None:
            nonlocal rendered
            if rendered:
                sys.stdout.write("\x1b[3A")
            lines = (
                "[ai] Enable Codex AI service?",
                f"  ({'●' if selected_yes else ' '}) Yes",
                f"  ({' ' if selected_yes else '●'}) No",
            )
            for line in lines:
                sys.stdout.write(f"\r\x1b[2K{line}\n")
            sys.stdout.flush()
            rendered = True

        try:
            tty.setraw(fd)
            sys.stdout.write("\x1b[?25l")
            render()
            while True:
                key = sys.stdin.read(1)
                if key == "\x1b":
                    sequence = sys.stdin.read(2)
                    if sequence in ("[A", "[B", "OA", "OB"):
                        selected_yes = not selected_yes
                        render()
                    continue
                if key in ("\r", "\n"):
                    return selected_yes
                if key.lower() == "y":
                    return True
                if key.lower() in ("n", "q") or key == "\x04":
                    return False
                if key == "\x03":
                    raise KeyboardInterrupt
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous_settings)
            # Raw-mode newlines leave the cursor mid-column; return to column 0
            # so the caller's next print starts at the line beginning.
            sys.stdout.write("\r\x1b[?25h")
            sys.stdout.flush()

    @staticmethod
    def _confirm_ai_service_use_with_text() -> bool:
        while True:
            try:
                answer = input("[ai] Enable Codex AI service? [y/N]: ").strip().lower()
            except EOFError:
                return False
            if answer in ("y", "yes"):
                return True
            if answer in ("", "n", "no"):
                return False
            print("[ai] Please enter yes or no")

    async def close(self) -> None:
        async with self._lifecycle_lock:
            codex = self._codex
            self._codex = None
            warm_thread_task = self._warm_thread_task
            self._warm_thread_task = None
            current_task = asyncio.current_task()
            shared_chat_tasks = [
                task
                for task in self._shared_chat_tasks
                if task is not current_task and not task.done()
            ]
            for task in shared_chat_tasks:
                task.cancel()
            if shared_chat_tasks:
                await asyncio.gather(*shared_chat_tasks, return_exceptions=True)
            self._conversations.clear()
            self._turn_locks.clear()
            self._strategy_instruction_locks.clear()
            if warm_thread_task is not None:
                warm_thread_task.cancel()
                await asyncio.gather(warm_thread_task, return_exceptions=True)
            try:
                if codex is not None:
                    await codex.close()
                    print("[ai] Codex app-server stopped")
            finally:
                self.dynamic_tools.unbind_loop()

    async def stream_chat(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        initial_context: str | None = None,
        history_messages: int = 0,
        model: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[CodexStreamEvent]:
        request_started_at = time.perf_counter()
        trace_id = uuid.uuid4().hex[:8]
        thread, conversation_id, is_new, thread_source = await self._conversation(conversation_id)
        prompt = initial_context if is_new and initial_context else message
        conversation_ms = (time.perf_counter() - request_started_at) * 1000
        print(
            f"[ai] chat={trace_id} conversation={'new' if is_new else 'reused'} "
            f"thread={thread_source} prepare={conversation_ms:.0f}ms "
            f"history={history_messages if is_new else 0} "
            f"prompt_chars={len(prompt)}"
        )
        yield CodexStreamEvent("conversation", {"conversation_id": conversation_id})
        async with self._turn_locks[conversation_id]:
            async for event in self._stream_turn(
                thread,
                prompt,
                trace_id=trace_id,
                request_started_at=request_started_at,
                model=model,
                effort=effort,
            ):
                yield event

    def start_shared_chat(
        self,
        message: str,
        *,
        client_history: list[dict[str, Any]] | None = None,
        client_conversation_id: str | None = None,
        on_state_changed: ChatStateCallback | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> CodexChatRun:
        run = CodexChatRun()
        self._pending_shared_chats += 1
        task = asyncio.create_task(
            self._run_shared_chat(
                run,
                message,
                client_history=client_history,
                client_conversation_id=client_conversation_id,
                on_state_changed=on_state_changed,
                model=model,
                effort=effort,
            ),
            name=f"codex-shared-chat-{run.id}",
        )
        self._shared_chat_tasks.add(task)
        task.add_done_callback(self._shared_chat_tasks.discard)
        return run

    async def handle_strategy_instruction(self, session: Any, event: dict[str, Any]) -> None:
        instruction = str(event.get("instruction") or "").strip()
        event_id = str(event.get("event_id") or "").strip()
        source = self._instruction_source(event)
        event_log_id = event_id[-8:] if source == "manual_alert" else event_id[:8]
        if not instruction or not event_id:
            return
        if self._disabled:
            print(
                f"[ai] {source} instruction skipped session={session.spec.id} "
                "reason=AI service disabled"
            )
            return

        self._pending_shared_chats += 1
        task = asyncio.create_task(
            self._run_strategy_instruction(session, event),
            name=f"codex-{source.replace('_', '-')}-{event_log_id}",
        )
        self._shared_chat_tasks.add(task)
        task.add_done_callback(self._shared_chat_tasks.discard)
        await self._notify_strategy_chat_changed()
        print(
            f"[ai] {source} instruction queued session={session.spec.id} "
            f"event={event_log_id} action={event.get('action') or 'order'}"
        )

    async def _run_strategy_instruction(self, session: Any, event: dict[str, Any]) -> None:
        conversation_id: str | None = None
        instruction = str(event.get("instruction") or "").strip()
        label = self._strategy_instruction_label(session, event)
        prefix = self._instruction_prefix(event)
        source = self._instruction_source(event)
        event_id = str(event.get("event_id") or "")
        event_log_id = event_id[-8:] if source == "manual_alert" else event_id[:8]
        session_lock = self._strategy_instruction_locks.setdefault(
            str(session.spec.id),
            asyncio.Lock(),
        )
        try:
            async with session_lock:
                await self._append_chat_message("user", f"{prefix} {label}\n{instruction}")
                await self._notify_strategy_chat_changed()

                answer = ""
                async for stream_event in self.stream_chat(
                    instruction,
                    conversation_id=None,
                    initial_context=self._build_strategy_instruction_prompt(
                        session,
                        event,
                    ),
                ):
                    if stream_event.event == "conversation":
                        conversation_id = (
                            str(stream_event.payload.get("conversation_id") or "") or None
                        )
                    elif stream_event.event == "done":
                        answer = str(stream_event.payload.get("answer") or "").strip()
                if answer:
                    await self._append_chat_message(
                        "assistant",
                        f"{prefix} {label}\n{answer}",
                    )
                print(
                    f"[ai] {source} instruction completed session={session.spec.id} "
                    f"event={event_log_id}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._append_chat_message(
                "assistant",
                f"{prefix} failed for {label}: {e}",
                error=True,
            )
            print(
                f"[ai] {source} instruction failed session={session.spec.id} "
                f"event={event_log_id}: {e}"
            )
        finally:
            if conversation_id:
                await self.reset(conversation_id)
            self._pending_shared_chats = max(0, self._pending_shared_chats - 1)
            await self._notify_strategy_chat_changed()

    async def _notify_strategy_chat_changed(self) -> None:
        try:
            await self.session_registry.hub_ws.broadcast_json({"type": "ai_chat_updated"})
        except Exception as e:
            print(f"[ai] strategy chat state notification failed: {e}")

    @staticmethod
    def _instruction_source(event: dict[str, Any]) -> str:
        return "manual_alert" if event.get("source") == "manual_alert" else "strategy"

    @classmethod
    def _instruction_prefix(cls, event: dict[str, Any]) -> str:
        return "[Manual Alert AI]" if cls._instruction_source(event) == "manual_alert" else "[Strategy AI]"

    @staticmethod
    def _strategy_instruction_label(session: Any, event: dict[str, Any]) -> str:
        if event.get("source") == "manual_alert":
            mode = "trigger" if event.get("mode") == "trigger" else "send"
            title = str(event.get("template_title") or "").strip()
            title_label = f" {title}" if title else ""
            return f"{session.spec.symbol} {session.spec.timeframe} {mode}{title_label}"
        action = str(event.get("action") or "order")
        order_id = str(event.get("order_id") or "").strip()
        order_label = f" {order_id}" if order_id else ""
        return f"{session.spec.symbol} {session.spec.timeframe} {action}{order_label}"

    @staticmethod
    def _build_strategy_instruction_prompt(session: Any, event: dict[str, Any]) -> str:
        if event.get("source") == "manual_alert":
            context = {
                "session_id": session.spec.id,
                "provider": session.spec.provider,
                "exchange": session.spec.exchange,
                "symbol": session.spec.symbol,
                "market_type": session.spec.market_type,
                "timeframe": session.spec.timeframe,
                "strategy": session.chart_info.get("script_title") or session.spec.script_name,
                "event_id": event.get("event_id"),
                "mode": event.get("mode"),
                "template_title": event.get("template_title"),
                "trigger_id": event.get("trigger_id"),
                "trigger_price": event.get("trigger_price"),
                "market_price": event.get("market_price"),
                "alert_time": event.get("time"),
                "webhook_sent": event.get("webhook_sent"),
                "telegram_sent": event.get("telegram_sent"),
                "telegram_failed": event.get("telegram_failed"),
            }
            return (
                "This is a server-verified Manual Alert AI instruction explicitly configured "
                "by the user in a Manual Alert template. The webhook was delivered successfully "
                "before this instruction was queued. Treat it as an explicit user request and "
                "execute it now. For any session state change, use only the exact session_id "
                "below; do not ask the user to identify the session and do not apply the "
                "instruction to another session. Do not broaden the requested action. If a "
                "required template or value is missing, report what is missing instead of "
                "inventing it. Respond in the same language as the instruction unless the "
                "instruction explicitly requests another language. Use that same language for "
                "any requested Telegram delivery.\n\n"
                f"Exact session and Manual Alert context:\n"
                f"{json.dumps(context, ensure_ascii=False)}\n\n"
                f"Instruction:\n{str(event.get('instruction') or '').strip()}"
            )

        context = {
            "session_id": session.spec.id,
            "provider": session.spec.provider,
            "exchange": session.spec.exchange,
            "symbol": session.spec.symbol,
            "market_type": session.spec.market_type,
            "timeframe": session.spec.timeframe,
            "strategy": session.chart_info.get("script_title") or session.spec.script_name,
            "event_id": event.get("event_id"),
            "action": event.get("action"),
            "order_id": event.get("order_id"),
            "comment": event.get("comment"),
            "bar_time": event.get("time"),
        }
        return (
            "This is a server-verified automated strategy instruction explicitly configured "
            "by the user through strategy.entry/strategy.close ai=. Treat it as an explicit "
            "user request and execute it now. For any session state change, use only the exact "
            "session_id below; do not ask the user to identify the session and do not apply the "
            "instruction to another session. Do not broaden the requested action. If a required "
            "template or value is missing, report what is missing instead of inventing it. "
            "Respond in the same language as the instruction unless the instruction explicitly "
            "requests another language. Use that same language for any requested Telegram "
            "delivery.\n\n"
            f"Exact session and fill context:\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"Instruction:\n{str(event.get('instruction') or '').strip()}"
        )

    async def stream_shared_chat(
        self,
        message: str,
        *,
        client_history: list[dict[str, Any]] | None = None,
        client_conversation_id: str | None = None,
        on_state_changed: ChatStateCallback | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[CodexStreamEvent]:
        run = self.start_shared_chat(
            message,
            client_history=client_history,
            client_conversation_id=client_conversation_id,
            on_state_changed=on_state_changed,
            model=model,
            effort=effort,
        )
        async for event in run.events():
            yield event

    async def _run_shared_chat(
        self,
        run: CodexChatRun,
        message: str,
        *,
        client_history: list[dict[str, Any]] | None,
        client_conversation_id: str | None,
        on_state_changed: ChatStateCallback | None,
        model: str | None,
        effort: str | None,
    ) -> None:
        try:
            async with self._shared_chat_lock:
                try:
                    history, conversation_id = await self._begin_shared_chat(
                        message,
                        client_history=client_history,
                        client_conversation_id=client_conversation_id,
                    )
                    await self._notify_chat_state_changed(on_state_changed)
                    async for event in self.stream_chat(
                        message,
                        conversation_id=conversation_id,
                        initial_context=self._build_initial_context(message, history),
                        history_messages=len(history),
                        model=model,
                        effort=effort,
                    ):
                        if event.event == "conversation":
                            await self._set_chat_conversation_id(
                                str(event.payload.get("conversation_id") or "") or None
                            )
                        elif event.event == "done":
                            answer = str(event.payload.get("answer") or "").strip()
                            if answer:
                                await self._append_chat_message("assistant", answer)
                        run.publish(event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    await self._append_chat_message(
                        "assistant",
                        f"AI call failed: {e}",
                        error=True,
                    )
                    run.publish(CodexStreamEvent("stream_error", {"error": str(e)}))
        finally:
            self._pending_shared_chats = max(0, self._pending_shared_chats - 1)
            await self._notify_chat_state_changed(on_state_changed)
            run.finish()

    @staticmethod
    async def _notify_chat_state_changed(callback: ChatStateCallback | None) -> None:
        if callback is None:
            return
        try:
            await callback()
        except Exception as e:
            print(f"[ai] chat state notification failed: {e}")

    async def chat_state(self) -> dict[str, Any]:
        async with self._chat_state_lock:
            return self._chat_state_payload()

    async def model_options(self) -> list[dict[str, Any]]:
        async with self._models_lock:
            if self._model_options is not None:
                return [dict(item) for item in self._model_options]
            codex = await self._get_codex()
            client = getattr(codex, "_client", None)
            if client is None:
                raise RuntimeError("Installed openai-codex SDK cannot list models")
            response = await client.request(
                "model/list",
                {"includeHidden": False},
                response_model=_CodexModelListResponse,
            )
            options = [
                {
                    "value": item.model,
                    "label": item.display_name,
                    "description": item.description,
                    "is_default": item.is_default,
                    "efforts": [
                        str(effort["reasoningEffort"])
                        for effort in item.supported_reasoning_efforts
                        if effort.get("reasoningEffort")
                    ],
                }
                for item in response.data
                if not item.hidden
                and any(
                    effort.get("reasoningEffort") == ReasoningEffort.xhigh.value
                    for effort in item.supported_reasoning_efforts
                )
            ]
            configured_model = await self._configured_model(codex)
            if configured_model:
                for option in options:
                    option["is_default"] = option["value"] == configured_model
                if configured_model not in {str(option["value"]) for option in options}:
                    options.insert(0, {
                        "value": configured_model,
                        "label": self._model_display_name(configured_model),
                        "description": "Current Codex default model.",
                        "is_default": True,
                        # supported efforts unknown for a model outside model/list;
                        # an empty list means "accept any known effort"
                        "efforts": [],
                    })
            self._model_options = options
            return [dict(item) for item in self._model_options]

    async def _configured_model(self, codex: AsyncCodex) -> str | None:
        client = getattr(codex, "_client", None)
        if client is None:
            return None
        try:
            response = await client.request(
                "config/read",
                {"cwd": str(self.project_root), "includeLayers": False},
                response_model=ConfigReadResponse,
            )
        except Exception as e:
            print(f"[ai] configured model lookup failed: {e}")
            return None
        return response.config.model

    @staticmethod
    def _model_display_name(model: str) -> str:
        parts = model.split("-")
        if len(parts) >= 2 and parts[0].lower() == "gpt":
            suffix = " ".join(part.capitalize() for part in parts[2:])
            return f"GPT-{parts[1]}" + (f" {suffix}" if suffix else "")
        return " ".join(part.upper() if len(part) <= 3 else part.capitalize() for part in parts)

    async def validate_model(self, model: str | None) -> str | None:
        if model is None:
            return None
        requested = model.strip()
        if not requested:
            return None
        options = await self.model_options()
        if requested not in {str(item["value"]) for item in options}:
            raise ValueError(f"unsupported AI model: {requested}")
        return requested

    async def chat_preferences(self) -> dict[str, str | None]:
        """Resolve the shared dashboard model/effort selection against the
        current model options, falling back to the preferred defaults."""
        options = await self.model_options()
        async with self._chat_state_lock:
            stored_model = self._chat_model
            stored_effort = self._chat_effort
        model = self._resolve_preferred_model(options, stored_model)
        effort = self._resolve_preferred_effort(options, model, stored_effort)
        return {"model": model, "effort": effort}

    async def set_chat_preferences(
        self,
        model: str | None,
        effort: str | None,
    ) -> dict[str, str | None]:
        validated_model = await self.validate_model(model)
        target_model = validated_model or (await self.chat_preferences())["model"]
        validated_effort = await self.validate_effort(effort, target_model)
        async with self._chat_state_lock:
            if validated_model:
                self._chat_model = validated_model
            if validated_effort:
                self._chat_effort = validated_effort
            self._save_chat_state()
        return await self.chat_preferences()

    @staticmethod
    def _resolve_preferred_model(
        options: list[dict[str, Any]],
        stored: str | None,
    ) -> str | None:
        values = {str(item["value"]) for item in options}
        if stored and stored in values:
            return stored
        if _PREFERRED_DEFAULT_MODEL in values:
            return _PREFERRED_DEFAULT_MODEL
        for item in options:
            if item.get("is_default"):
                return str(item["value"])
        return str(options[0]["value"]) if options else None

    @staticmethod
    def _resolve_preferred_effort(
        options: list[dict[str, Any]],
        model: str | None,
        stored: str | None,
    ) -> str | None:
        selected = next((item for item in options if str(item["value"]) == model), None)
        supported = [str(value) for value in (selected or {}).get("efforts") or []]
        # an empty supported list means the efforts are unknown; accept anything known
        if stored and (not supported or stored in supported):
            return stored
        if not supported or _PREFERRED_DEFAULT_EFFORT in supported:
            return _PREFERRED_DEFAULT_EFFORT
        return supported[-1]

    async def validate_effort(self, effort: str | None, model: str | None) -> str | None:
        if effort is None:
            return None
        requested = effort.strip().lower()
        if not requested:
            return None
        try:
            ReasoningEffort(requested)
        except ValueError:
            raise ValueError(f"unsupported reasoning effort: {requested}") from None
        options = await self.model_options()
        if model:
            selected = next((item for item in options if str(item["value"]) == model), None)
        else:
            selected = next((item for item in options if item.get("is_default")), None)
        supported = [str(value) for value in (selected or {}).get("efforts") or []]
        if supported and requested not in supported:
            raise ValueError(f"model does not support reasoning effort: {requested}")
        return requested

    async def import_chat_state(
        self,
        messages: Any,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        async with self._chat_state_lock:
            if not self._chat_messages:
                imported = self._sanitize_chat_messages(messages)
                if imported:
                    self._chat_messages = imported
                    self._chat_conversation_id = conversation_id or None
                    self._save_chat_state()
            return self._chat_state_payload()

    async def clear_shared_chat(self, conversation_id: str | None = None) -> None:
        async with self._shared_chat_lock:
            async with self._chat_state_lock:
                stored_conversation_id = self._chat_conversation_id
                self._chat_messages = []
                self._chat_conversation_id = None
                self._save_chat_state()
            for value in {conversation_id, stored_conversation_id}:
                if value:
                    await self.reset(value)

    async def reset(self, conversation_id: str) -> None:
        async with self._conversations_lock:
            self._conversations.pop(conversation_id, None)
            self._turn_locks.pop(conversation_id, None)

    async def _begin_shared_chat(
        self,
        message: str,
        *,
        client_history: list[dict[str, Any]] | None,
        client_conversation_id: str | None,
    ) -> tuple[list[dict[str, str]], str | None]:
        async with self._chat_state_lock:
            if not self._chat_messages and client_history:
                self._chat_messages = self._sanitize_chat_messages(client_history)
                self._chat_conversation_id = client_conversation_id or None
            history = [
                {"role": item["role"], "content": item["content"]}
                for item in self._chat_messages
                if not item.get("error")
            ][-_MAX_CONTEXT_MESSAGES:]
            conversation_id = self._chat_conversation_id or client_conversation_id
            self._chat_messages.append({"role": "user", "content": message})
            self._chat_messages = self._chat_messages[-_MAX_PERSISTED_MESSAGES:]
            self._save_chat_state()
            return history, conversation_id

    async def _set_chat_conversation_id(self, conversation_id: str | None) -> None:
        async with self._chat_state_lock:
            if conversation_id == self._chat_conversation_id:
                return
            self._chat_conversation_id = conversation_id
            self._save_chat_state()

    async def _append_chat_message(self, role: str, content: str, *, error: bool = False) -> None:
        entry: dict[str, Any] = {
            "role": role,
            "content": content[:_MAX_PERSISTED_CONTENT_CHARS],
        }
        if error:
            entry["error"] = True
        async with self._chat_state_lock:
            self._chat_messages.append(entry)
            self._chat_messages = self._chat_messages[-_MAX_PERSISTED_MESSAGES:]
            self._save_chat_state()

    def _load_chat_state(self) -> None:
        if not self.chat_state_path.exists():
            return
        try:
            payload = json.loads(self.chat_state_path.read_text(encoding="utf-8"))
            self._chat_messages = self._sanitize_chat_messages(payload.get("messages"))
            conversation_id = payload.get("conversation_id")
            self._chat_conversation_id = (
                conversation_id if isinstance(conversation_id, str) and conversation_id else None
            )
            model = payload.get("model")
            self._chat_model = model if isinstance(model, str) and model else None
            effort = payload.get("effort")
            self._chat_effort = effort if isinstance(effort, str) and effort else None
        except Exception as e:
            print(f"[ai] chat state load failed: {e}")

    def _save_chat_state(self) -> None:
        self.chat_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.chat_state_path.with_name(self.chat_state_path.name + ".tmp")
        payload = self._chat_state_payload()
        payload.pop("pending", None)
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.chat_state_path)

    def _chat_state_payload(self) -> dict[str, Any]:
        return {
            "conversation_id": self._chat_conversation_id,
            "messages": [dict(item) for item in self._chat_messages],
            "pending": self._pending_shared_chats > 0,
            # raw stored selection (unresolved): lets clients that reconnect
            # after missing an ai_prefs_updated broadcast catch up
            "model": self._chat_model,
            "effort": self._chat_effort,
        }

    @staticmethod
    def _sanitize_chat_messages(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        messages: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            content = content.strip()
            if not content:
                continue
            message: dict[str, Any] = {
                "role": role,
                "content": content[:_MAX_PERSISTED_CONTENT_CHARS],
            }
            if bool(item.get("error")):
                message["error"] = True
            messages.append(message)
        return messages[-_MAX_PERSISTED_MESSAGES:]

    @staticmethod
    def _build_initial_context(message: str, history: list[dict[str, str]]) -> str:
        lines: list[str] = []
        if history:
            lines.append("Previous conversation saved on the server:")
            for item in history:
                role = "User" if item["role"] == "user" else "Assistant"
                lines.append(f"[{role}] {item['content']}")
            lines.append("")
        lines.append(f"[Current user request] {message}")
        return "\n".join(lines)

    async def _get_codex(self) -> AsyncCodex:
        if self._codex is None:
            await self.start()
        if self._codex is None:
            if self._disabled:
                raise RuntimeError("Codex AI service was disabled at data-service startup")
            raise RuntimeError("Codex app-server is unavailable")
        return self._codex

    async def _conversation(
        self,
        conversation_id: str | None,
    ) -> tuple[AsyncThread, str, bool, str]:
        async with self._conversations_lock:
            if conversation_id:
                thread = self._conversations.get(conversation_id)
                if thread is not None:
                    return thread, conversation_id, False, "reused"

            codex = await self._get_codex()
            warm_thread_task = self._warm_thread_task
            thread = None
            if warm_thread_task is not None:
                thread = await asyncio.shield(warm_thread_task)
                if self._warm_thread_task is warm_thread_task:
                    self._warm_thread_task = None
            thread_source = "prewarmed"
            if thread is None:
                thread = await self._start_thread(codex)
                thread_source = "created"
            new_id = uuid.uuid4().hex
            self._conversations[new_id] = thread
            self._turn_locks[new_id] = asyncio.Lock()
            return thread, new_id, True, thread_source

    async def _prepare_warm_thread(self, codex: AsyncCodex) -> AsyncThread | None:
        started_at = time.perf_counter()
        try:
            thread = await self._start_thread(codex)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            print(f"[ai] warm thread failed after {elapsed_ms:.0f}ms: {e}")
            return None
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        print(f"[ai] warm thread ready in {elapsed_ms:.0f}ms")
        return thread

    async def _start_thread(self, codex: AsyncCodex) -> AsyncThread:
        # openai-codex 0.1.0b3 generates dynamic tool models but does not yet
        # expose dynamicTools on its high-level thread_start wrapper.
        client = getattr(codex, "_client", None)
        if client is None:
            raise RuntimeError("Installed openai-codex SDK cannot register dynamic tools")
        started = await client.thread_start({
            "approvalPolicy": "never",
            "config": self._permission_profile_config(),
            "cwd": str(self.project_root),
            "developerInstructions": self.developer_instructions,
            "dynamicTools": self.dynamic_tools.specs,
            "ephemeral": True,
        })
        return AsyncThread(codex, started.thread.id)

    def _install_dynamic_tool_handler(self, codex: AsyncCodex) -> None:
        client = getattr(codex, "_client", None)
        sync_client = getattr(client, "_sync", None)
        if sync_client is None or not hasattr(sync_client, "_approval_handler"):
            raise RuntimeError("Installed openai-codex SDK cannot handle dynamic tools")
        sync_client._approval_handler = self.dynamic_tools.handle_server_request

    def _permission_profile_config(self) -> dict[str, Any]:
        return {
            "default_permissions": _AI_PERMISSION_PROFILE,
            "permissions": {
                _AI_PERMISSION_PROFILE: {
                    "filesystem": {":root": "read"},
                    "network": {"enabled": True},
                },
            },
        }

    def _file_access_instructions(self) -> str:
        editable = ", ".join(str(path) for path in self.file_tools.edit_roots)
        return (
            "You may read files outside this repository only when required to fulfill the "
            "user's request. Modify files only when the user explicitly requests a file change. "
            f"You may modify only existing regular files under these paths: {editable}. "
            f"Create new files and directories only under {self.file_tools.tmp_root}. "
            "Use only edit_existing_file to modify an existing file and write_tmp_file to create "
            "a file under tmp. Do not write files through shell commands, apply_patch, Python "
            "scripts, or any other tool. Outside tmp, do not create, delete, rename, or move files "
            "or directories. After completing a file change, identify the paths actually changed "
            "in the final response."
        )

    @staticmethod
    def _agent_message(item: Any) -> AgentMessageThreadItem | None:
        value = item.root if hasattr(item, "root") else item
        return value if isinstance(value, AgentMessageThreadItem) else None

    @staticmethod
    def _short_work_value(value: Any, limit: int = 100) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[:limit - 3].rstrip() + "..."

    @classmethod
    def _work_status(cls, item: Any, *, completed: bool) -> str | None:
        value = item.root if hasattr(item, "root") else item
        if isinstance(value, WebSearchThreadItem):
            query = cls._short_work_value(value.query)
            if completed:
                return f'Searched the web for "{query}"' if query else "Searched the web"
            return f'Searching the web for "{query}"...' if query else "Searching the web..."
        if isinstance(value, CommandExecutionThreadItem):
            script_name = next(
                (
                    Path(token.strip("'\"")).name
                    for token in value.command.split()
                    if token.strip("'\"").endswith(".py")
                ),
                "local data lookup",
            )
            return f"Finished {script_name}" if completed else f"Running {script_name}..."
        if isinstance(value, McpToolCallThreadItem):
            tool = cls._short_work_value(value.tool, limit=60) or "MCP tool"
            return f"Completed {tool}" if completed else f"Calling {tool}..."
        if isinstance(value, DynamicToolCallThreadItem):
            tool = cls._short_work_value(value.tool, limit=60) or "tool"
            return f"Completed {tool}" if completed else f"Running {tool}..."
        return None

    async def _stream_turn(
        self,
        thread: AsyncThread,
        prompt: str,
        *,
        trace_id: str,
        request_started_at: float,
        model: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[CodexStreamEvent]:
        turn = None
        first_event_ms: float | None = None
        first_delta_ms: float | None = None
        try:
            turn_start_requested_at = time.perf_counter()
            turn = await thread.turn(
                prompt,
                approval_mode=ApprovalMode.deny_all,
                effort=ReasoningEffort(effort) if effort else ReasoningEffort.xhigh,
                model=model,
            )
            turn_start_ms = (time.perf_counter() - turn_start_requested_at) * 1000
            request_to_turn_ms = (time.perf_counter() - request_started_at) * 1000
            print(
                f"[ai] chat={trace_id} turn_started rpc={turn_start_ms:.0f}ms "
                f"request_elapsed={request_to_turn_ms:.0f}ms"
            )
            phases: dict[str, MessagePhase | None] = {}
            commentary_texts: dict[str, str] = {}
            streamed_text = ""
            final_text = ""
            stream = turn.stream()
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    async for event in stream:
                        if first_event_ms is None:
                            first_event_ms = (time.perf_counter() - request_started_at) * 1000
                            print(
                                f"[ai] chat={trace_id} first_event={first_event_ms:.0f}ms "
                                f"type={event.method}"
                            )
                        payload = event.payload
                        if isinstance(payload, ItemStartedNotification):
                            message = self._agent_message(payload.item)
                            if message is not None:
                                phases[message.id] = message.phase
                                if message.phase == MessagePhase.commentary:
                                    commentary_texts[message.id] = message.text
                            else:
                                work_status = self._work_status(payload.item, completed=False)
                                if work_status:
                                    yield CodexStreamEvent("work_status", {"text": work_status})
                            continue
                        if isinstance(payload, AgentMessageDeltaNotification):
                            phase = phases.get(payload.item_id)
                            if phase == MessagePhase.commentary:
                                commentary = commentary_texts.get(payload.item_id, "") + payload.delta
                                commentary_texts[payload.item_id] = commentary
                                if commentary.strip():
                                    yield CodexStreamEvent("status", {"text": commentary})
                                continue
                            if first_delta_ms is None:
                                first_delta_ms = (
                                    time.perf_counter() - request_started_at
                                ) * 1000
                                print(
                                    f"[ai] chat={trace_id} first_delta={first_delta_ms:.0f}ms"
                                )
                            streamed_text += payload.delta
                            yield CodexStreamEvent("delta", {"text": payload.delta})
                            continue
                        if isinstance(payload, ItemCompletedNotification):
                            message = self._agent_message(payload.item)
                            if message is not None:
                                if message.phase == MessagePhase.commentary:
                                    previous_commentary = commentary_texts.get(message.id, "")
                                    commentary_texts[message.id] = message.text
                                    if message.text.strip() and message.text != previous_commentary:
                                        yield CodexStreamEvent("status", {"text": message.text})
                                else:
                                    final_text = message.text
                            else:
                                work_status = self._work_status(payload.item, completed=True)
                                if work_status:
                                    yield CodexStreamEvent("work_status", {"text": work_status})
                            continue
                        if isinstance(payload, TurnCompletedNotification):
                            if payload.turn.status == TurnStatus.failed:
                                error = payload.turn.error
                                raise RuntimeError(
                                    error.message if error and error.message else "Codex turn failed"
                                )
                            if payload.turn.status == TurnStatus.interrupted:
                                raise RuntimeError("Codex turn was interrupted")
            except asyncio.TimeoutError:
                elapsed_ms = (time.perf_counter() - request_started_at) * 1000
                print(f"[ai] chat={trace_id} timeout total={elapsed_ms:.0f}ms")
                try:
                    await turn.interrupt()
                except Exception:
                    pass
                raise RuntimeError("Codex response timed out") from None
            except asyncio.CancelledError:
                elapsed_ms = (time.perf_counter() - request_started_at) * 1000
                print(f"[ai] chat={trace_id} cancelled total={elapsed_ms:.0f}ms")
                try:
                    await turn.interrupt()
                except Exception:
                    pass
                raise
            finally:
                await stream.aclose()
        except TransportClosedError:
            await self.close()
            raise RuntimeError("Codex app-server stopped unexpectedly; retry the request") from None

        answer = (final_text or streamed_text).strip()
        if not answer:
            raise RuntimeError("Codex returned an empty response")
        total_ms = (time.perf_counter() - request_started_at) * 1000
        first_delta_label = f"{first_delta_ms:.0f}ms" if first_delta_ms is not None else "none"
        print(
            f"[ai] chat={trace_id} completed total={total_ms:.0f}ms "
            f"first_delta={first_delta_label} answer_chars={len(answer)}"
        )
        yield CodexStreamEvent("done", {"answer": answer})
