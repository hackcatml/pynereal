from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, CodexConfig, Sandbox
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

DEFAULT_DEVELOPER_INSTRUCTIONS = (
    "너는 PyneReal 대시보드의 AI 어시스턴트야. "
    "조회만 수행하고 주문, 취소, 레버리지 변경 등 계정 상태를 변경하는 작업과 파일 수정은 하지 마. "
    "계정이나 거래소를 지정하지 않은 자산 또는 포지션 조회 요청은 providers.toml에 등록된 "
    "모든 계정을 대상으로 전용 스크립트를 실행해. "
    "DB 접속정보와 API key, secret 등 비밀값은 응답에 포함하지 마. "
    "자산 또는 포지션 조회의 중간 진행 설명에는 실제 조회 대상, 조회 진행, 부분 실패처럼 "
    "사용자에게 의미 있는 상태만 포함해. 지침 파일 확인, 저장소 탐색, 워크플로 선택, 실행 옵션 "
    "검토 같은 내부 준비 과정이나 결과를 나중에 정리하겠다는 설명은 출력하지 마. "
    "최종 답변도 작업 절차를 나열하지 말고 조회 결과부터 바로 제시해."
)
_MAX_PERSISTED_MESSAGES = 200
_MAX_CONTEXT_MESSAGES = 12
_MAX_PERSISTED_CONTENT_CHARS = 40_000


@dataclass(frozen=True)
class CodexStreamEvent:
    event: str
    payload: dict[str, Any]


class CodexService:
    """Own one long-running Codex app-server and its dashboard chat threads."""

    def __init__(
        self,
        project_root: Path,
        developer_instructions: str = DEFAULT_DEVELOPER_INSTRUCTIONS,
        timeout_seconds: float = 180,
        chat_state_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.developer_instructions = developer_instructions
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
        self._warm_thread_task: asyncio.Task[AsyncThread | None] | None = None
        self._chat_messages: list[dict[str, Any]] = []
        self._chat_conversation_id: str | None = None
        self._load_chat_state()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._codex is not None:
                return

            started_at = time.perf_counter()
            codex_bin = shutil.which("codex")
            codex = AsyncCodex(CodexConfig(
                codex_bin=codex_bin,
                cwd=str(self.project_root),
                config_overrides=(
                    "sandbox_workspace_write.network_access=true",
                    'web_search="live"',
                ),
            ))
            try:
                await codex.__aenter__()
                account = await codex.account()
                if account.account is None:
                    raise RuntimeError("Codex is not authenticated; run `codex login` first")
            except Exception:
                await codex.close()
                raise

            self._codex = codex
            self._warm_thread_task = asyncio.create_task(self._prepare_warm_thread(codex))
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            print(
                "[ai] Codex app-server started with the current local login "
                f"in {elapsed_ms:.0f}ms"
            )

    async def close(self) -> None:
        async with self._lifecycle_lock:
            codex = self._codex
            self._codex = None
            warm_thread_task = self._warm_thread_task
            self._warm_thread_task = None
            self._conversations.clear()
            self._turn_locks.clear()
            if warm_thread_task is not None:
                warm_thread_task.cancel()
                await asyncio.gather(warm_thread_task, return_exceptions=True)
            if codex is not None:
                await codex.close()
                print("[ai] Codex app-server stopped")

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

    async def stream_shared_chat(
        self,
        message: str,
        *,
        client_history: list[dict[str, Any]] | None = None,
        client_conversation_id: str | None = None,
    ) -> AsyncIterator[CodexStreamEvent]:
        async with self._shared_chat_lock:
            history, conversation_id = await self._begin_shared_chat(
                message,
                client_history=client_history,
                client_conversation_id=client_conversation_id,
            )
            try:
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
                    yield event
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._append_chat_message(
                    "assistant",
                    f"AI call failed: {e}",
                    error=True,
                )
                raise

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
        tmp.write_text(
            json.dumps(self._chat_state_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.chat_state_path)

    def _chat_state_payload(self) -> dict[str, Any]:
        return {
            "conversation_id": self._chat_conversation_id,
            "messages": [dict(item) for item in self._chat_messages],
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
            lines.append("서버에 저장된 이전 대화:")
            for item in history:
                role = "사용자" if item["role"] == "user" else "AI"
                lines.append(f"[{role}] {item['content']}")
            lines.append("")
        lines.append(f"[현재 사용자 질문] {message}")
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
        return await codex.thread_start(
            approval_mode=ApprovalMode.deny_all,
            cwd=str(self.project_root),
            developer_instructions=self.developer_instructions,
            ephemeral=True,
            sandbox=Sandbox.workspace_write,
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
