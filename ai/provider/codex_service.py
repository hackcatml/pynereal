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
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, CodexConfig
from openai_codex.errors import TransportClosedError
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    AgentMessageThreadItem,
    CommandExecutionThreadItem,
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

from ai.scripts.dynamic_tools import AIDynamicTools

DEFAULT_DEVELOPER_INSTRUCTIONS = (
    "You are the AI assistant for the PyneReal dashboard. "
    "Treat exchanges and accounts as read-only. Do not place or cancel orders, "
    "change leverage, or perform any other account state mutation. "
    "You may change session state only through the dedicated tools when the user "
    "explicitly requests setting or deleting Manual Alert price triggers. For any Manual Alert request, "
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
    "delivery only when the tool returns success."
)
_MAX_PERSISTED_MESSAGES = 200
_MAX_CONTEXT_MESSAGES = 12
_MAX_PERSISTED_CONTENT_CHARS = 40_000
_AI_PERMISSION_PROFILE = "pynereal-ai-read-only"
_CODEX_LOGIN_TIMEOUT_SECONDS = 10 * 60


@dataclass(frozen=True)
class CodexStreamEvent:
    event: str
    payload: dict[str, Any]


_CHAT_STREAM_END = object()
ChatStateCallback = Callable[[], Awaitable[None]]


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
        developer_instructions: str = DEFAULT_DEVELOPER_INSTRUCTIONS,
        timeout_seconds: float = 180,
        chat_state_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.dynamic_tools = AIDynamicTools(
            self.project_root,
            session_registry=session_registry,
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
        self._lifecycle_lock = asyncio.Lock()
        self._conversations_lock = asyncio.Lock()
        self._shared_chat_lock = asyncio.Lock()
        self._chat_state_lock = asyncio.Lock()
        self._conversations: dict[str, AsyncThread] = {}
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._shared_chat_tasks: set[asyncio.Task[None]] = set()
        self._pending_shared_chats = 0
        self._warm_thread_task: asyncio.Task[AsyncThread | None] | None = None
        self._chat_messages: list[dict[str, Any]] = []
        self._chat_conversation_id: str | None = None
        self._load_chat_state()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._codex is not None:
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
                await self._ensure_authenticated(codex)
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

    async def _ensure_authenticated(self, codex: AsyncCodex) -> None:
        account = await codex.account()
        if account.account is not None:
            return

        if not self._interactive_terminal_available():
            raise RuntimeError(
                "Codex is not authenticated and interactive login is unavailable; "
                "start data_service/main.py from a terminal or run `codex login` first"
            )

        print("[ai] Codex login is required. Starting device-code login...")
        login = await codex.login_chatgpt_device_code()
        print(f"[ai] Open {login.verification_url}")
        print(f"[ai] Enter code: {login.user_code}")
        print("[ai] Waiting for Codex login to complete...")
        try:
            async with asyncio.timeout(_CODEX_LOGIN_TIMEOUT_SECONDS):
                await login.wait()
        except TimeoutError:
            await login.cancel()
            raise RuntimeError("Codex interactive login timed out") from None
        except asyncio.CancelledError:
            await login.cancel()
            raise

        account = await codex.account(refresh_token=True)
        if account.account is None:
            raise RuntimeError("Codex login completed without an authenticated account")
        print("[ai] Codex login completed")

    @staticmethod
    def _interactive_terminal_available() -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

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
            ):
                yield event

    def start_shared_chat(
        self,
        message: str,
        *,
        client_history: list[dict[str, Any]] | None = None,
        client_conversation_id: str | None = None,
        on_state_changed: ChatStateCallback | None = None,
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
            ),
            name=f"codex-shared-chat-{run.id}",
        )
        self._shared_chat_tasks.add(task)
        task.add_done_callback(self._shared_chat_tasks.discard)
        return run

    async def stream_shared_chat(
        self,
        message: str,
        *,
        client_history: list[dict[str, Any]] | None = None,
        client_conversation_id: str | None = None,
        on_state_changed: ChatStateCallback | None = None,
    ) -> AsyncIterator[CodexStreamEvent]:
        run = self.start_shared_chat(
            message,
            client_history=client_history,
            client_conversation_id=client_conversation_id,
            on_state_changed=on_state_changed,
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
    ) -> AsyncIterator[CodexStreamEvent]:
        turn = None
        first_event_ms: float | None = None
        first_delta_ms: float | None = None
        try:
            turn_start_requested_at = time.perf_counter()
            turn = await thread.turn(
                prompt,
                approval_mode=ApprovalMode.deny_all,
                effort=ReasoningEffort.xhigh,
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
