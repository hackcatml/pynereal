from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any
from urllib.parse import quote

from data_service.evaluation_context import (
    capture_session_evaluation_seed,
    collect_session_evaluation_context,
)

from .account_match import match_session_account, resolve_account_hint
from .asset import configured_accounts, read_provider_config


_CONTEXT_TOOL_NAME = "get_session_evaluation_context"
_CAPTURE_TOOL_NAME = "capture_session_chart"
_MAX_CONFIRMED_BARS = 2_000
_DEFAULT_CONFIRMED_BARS = 500
_READY_WAIT_SECONDS = 480.0
_BRIDGE_TIMEOUT_SECONDS = 500.0
_CAPTURE_TIMEOUT_SECONDS = 40.0
_ACCOUNT_COLLECTOR_TIMEOUT_SECONDS = 180.0


class SessionEvaluationToolError(ValueError):
    pass


class SessionEvaluationBridge:
    def __init__(self, project_root: Path, registry: Any) -> None:
        self._project_root = project_root.resolve()
        self._registry = registry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread_id = threading.get_ident()

    def unbind_loop(self) -> None:
        self._loop = None
        self._loop_thread_id = None

    def execute(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise SessionEvaluationToolError("Session evaluation service is not running")
        if threading.get_ident() == self._loop_thread_id:
            raise SessionEvaluationToolError(
                "Session evaluation tool cannot block the data-service event loop"
            )
        future = asyncio.run_coroutine_threadsafe(
            self._execute_on_loop(operation, arguments),
            loop,
        )
        try:
            return future.result(timeout=_BRIDGE_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            future.cancel()
            raise SessionEvaluationToolError("Session evaluation timed out") from None

    async def _execute_on_loop(
        self,
        operation: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "context":
            return await self._context(arguments)
        if operation == "capture":
            return await self._capture(arguments)
        raise SessionEvaluationToolError(f"Unknown evaluation operation: {operation}")

    def _session_summaries(self) -> list[dict[str, Any]]:
        result = []
        for session in self._registry.sessions.values():
            result.append({
                "session_id": session.spec.id,
                "exchange": session.spec.exchange,
                "symbol": session.spec.symbol,
                "market_type": session.spec.market_type,
                "timeframe": session.spec.timeframe,
                "strategy_name": str(
                    session.chart_info.get("script_title")
                    or session.spec.script_name
                    or ""
                ),
                "script_name": session.spec.script_name,
                "runner_connected": session.runner_count > 0,
                "runner_ready": session.runner_ready,
                "calculation": session.calculation_state_payload(),
            })
        return result

    async def _context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = arguments.get("session_id")
        if session_id is None:
            return {
                "session_count": len(self._registry.sessions),
                "sessions": self._session_summaries(),
                "instruction": (
                    "Resolve the user's description to exactly one session_id, then call "
                    "get_session_evaluation_context again with that ID."
                ),
            }
        session = self._registry.get(session_id)
        if session is None:
            raise SessionEvaluationToolError(f"Unknown active session: {session_id}")

        wait_for_ready = bool(arguments.get("wait_for_ready", True))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _READY_WAIT_SECONDS
        if wait_for_ready and not session.calculation_ready():
            ready = await session.wait_for_calculation_ready(
                max(0.0, deadline - loop.time())
            )
            if not ready and session.runner_count > 0:
                raise SessionEvaluationToolError(
                    "Session calculation did not become ready before the wait limit"
                )

        context: dict[str, Any] | None = None
        context_stable = False
        for _ in range(3):
            generation_id = session.calculation_generation_id
            seed = capture_session_evaluation_seed(session)
            context = await asyncio.to_thread(
                collect_session_evaluation_context,
                seed,
                generation_id=generation_id,
                confirmed_bar_limit=int(arguments["confirmed_bar_limit"]),
                include_recent_logs=bool(arguments["include_recent_logs"]),
            )
            if generation_id == session.calculation_generation_id:
                if wait_for_ready and not session.calculation_ready():
                    warning = (
                        "The runner did not produce a ready strategy snapshot before the wait limit."
                        if session.runner_count > 0
                        else "The runner is stopped; the returned calculation state is not current."
                    )
                    context["warnings"].append(warning)
                context_stable = True
                break
            if wait_for_ready:
                remaining = max(0.0, deadline - loop.time())
                if remaining <= 0:
                    raise SessionEvaluationToolError(
                        "Session calculation kept changing until the readiness wait expired"
                    )
                ready = await session.wait_for_calculation_ready(remaining)
                if not ready and session.runner_count > 0:
                    raise SessionEvaluationToolError(
                        "Session calculation did not become ready after its generation changed"
                    )
        if context is None or not context_stable:
            raise SessionEvaluationToolError(
                "Session calculation changed repeatedly while collecting evaluation context"
            )

        (
            account_match,
            positions_payload,
            orders_payload,
            explicit_account,
        ) = await self._collect_account_match(
            context,
            account_hint=arguments.get("account"),
        )

        if (
            wait_for_ready
            and context["calculation"].get("generation_id")
            != session.calculation_generation_id
        ):
            ready = await session.wait_for_calculation_ready(
                max(0.0, deadline - loop.time())
            )
            if not ready:
                raise SessionEvaluationToolError(
                    "Session calculation changed during account lookup and did not become ready"
                )
            generation_id = session.calculation_generation_id
            seed = capture_session_evaluation_seed(session)
            refreshed = await asyncio.to_thread(
                collect_session_evaluation_context,
                seed,
                generation_id=generation_id,
                confirmed_bar_limit=int(arguments["confirmed_bar_limit"]),
                include_recent_logs=bool(arguments["include_recent_logs"]),
            )
            if (
                generation_id != session.calculation_generation_id
                or not session.calculation_ready()
            ):
                raise SessionEvaluationToolError(
                    "Session calculation changed repeatedly during account lookup"
                )
            refreshed["account_match"] = match_session_account(
                refreshed,
                positions_payload,
                orders_payload,
                explicit_account=explicit_account,
            )
            context = refreshed
        else:
            context["account_match"] = account_match

        account_match = context["account_match"]
        if account_match["status"] == "ambiguous":
            context["warnings"].append(
                "Multiple configured accounts have similarly strong matching evidence."
            )
        elif account_match["status"] == "no_match":
            context["warnings"].append(
                "No configured account had a same-symbol position or recent order history."
            )
        if account_match["collection"]["errors"]:
            context["warnings"].append(
                "Some account position or order-history collectors were incomplete."
            )
        return context

    async def _collect_account_match(
        self,
        context: dict[str, Any],
        *,
        account_hint: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None]:
        explicit_account: str | None = None
        if account_hint:
            config_path = self._project_root / "workdir" / "config" / "providers.toml"
            try:
                data = await asyncio.to_thread(read_provider_config, config_path)
                names = [account.name for account in configured_accounts(data)]
                explicit_account = resolve_account_hint(account_hint, names)
            except Exception as exc:
                raise SessionEvaluationToolError(str(exc)) from None

        account_args = ["--account", explicit_account] if explicit_account else []
        session = context.get("session") or {}
        symbol = str(session.get("symbol") or "")
        market_type = str(session.get("market_type") or "").lower()
        if market_type not in {"spot", "linear", "inverse"}:
            market_type = "linear" if ":" in symbol else "spot"

        positions_task = asyncio.create_task(self._run_account_collector(
            "position.py",
            account_args,
        ))
        orders_task = asyncio.create_task(self._run_account_collector(
            "order_history.py",
            ["--symbol", symbol, "--market-type", market_type, *account_args],
        ))
        positions_payload, orders_payload = await asyncio.gather(
            positions_task,
            orders_task,
        )
        result = match_session_account(
            context,
            positions_payload,
            orders_payload,
            explicit_account=explicit_account,
        )
        return result, positions_payload, orders_payload, explicit_account

    async def _run_account_collector(
        self,
        script_name: str,
        arguments: list[str],
    ) -> dict[str, Any]:
        script_path = self._project_root / "ai" / "scripts" / script_name
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            *arguments,
            cwd=str(self._project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=_ACCOUNT_COLLECTOR_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return {
                "results": [],
                "summary": {"requested": 0, "succeeded": 0, "failed": 1},
                "error": {"type": "TimeoutError"},
            }
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {
                "results": [],
                "summary": {"requested": 0, "succeeded": 0, "failed": 1},
                "error": {"type": "InvalidCollectorOutput"},
            }
        return payload if isinstance(payload, dict) else {
            "results": [],
            "summary": {"requested": 0, "succeeded": 0, "failed": 1},
            "error": {"type": "InvalidCollectorOutput"},
        }

    async def _capture(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = arguments["session_id"]
        generation_id = arguments["generation_id"]
        session = self._registry.get(session_id)
        if session is None:
            raise SessionEvaluationToolError(f"Unknown active session: {session_id}")
        if generation_id != session.calculation_generation_id or not session.calculation_ready():
            raise SessionEvaluationToolError(
                "The requested calculation generation is no longer ready; collect context again"
            )

        capture_path = await asyncio.to_thread(
            self._capture_chart,
            session_id,
            int(arguments["width"]),
            int(arguments["height"]),
        )
        if generation_id != session.calculation_generation_id or not session.calculation_ready():
            capture_path.unlink(missing_ok=True)
            raise SessionEvaluationToolError(
                "The calculation generation changed while the chart was being captured"
            )
        return {
            "session_id": session_id,
            "generation_id": generation_id,
            "width": int(arguments["width"]),
            "height": int(arguments["height"]),
            "image_path": str(capture_path),
        }

    @staticmethod
    def _browser_executable() -> str | None:
        candidates = [
            os.environ.get("PYNEREAL_CHROME_PATH"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(candidate)
        return None

    def _capture_chart(self, session_id: str, width: int, height: int) -> Path:
        browser = self._browser_executable()
        if browser is None:
            raise SessionEvaluationToolError(
                "No supported Chrome or Chromium executable is available for chart capture"
            )
        output_dir = self._project_root / "tmp" / "session-evaluation"
        output_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for old_path in output_dir.glob("*.png"):
            try:
                if now - old_path.stat().st_mtime > 24 * 60 * 60:
                    old_path.unlink()
            except OSError:
                pass
        safe_name = "".join(char if char.isalnum() else "_" for char in session_id)[:80]
        output_path = output_dir / f"{safe_name}-{int(now * 1000)}.png"
        port = int(self._registry.supervisor.port)
        url = f"http://127.0.0.1:{port}/s/{quote(session_id, safe='')}"
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--no-first-run",
            f"--window-size={width},{height}",
            "--virtual-time-budget=5000",
            f"--screenshot={output_path}",
            url,
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            output_path.unlink(missing_ok=True)
            raise SessionEvaluationToolError(
                f"Chart capture failed (browser exit {completed.returncode})"
            )
        return output_path


class SessionEvaluationTools:
    def __init__(self, project_root: Path, registry: Any) -> None:
        self.bridge = SessionEvaluationBridge(project_root, registry)

    @property
    def names(self) -> set[str]:
        return {_CONTEXT_TOOL_NAME, _CAPTURE_TOOL_NAME}

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": _CONTEXT_TOOL_NAME,
                "description": (
                    "Collect one generation-consistent, read-only evaluation context for an "
                    "active PyneReal strategy session. Call without session_id first when the "
                    "user names a symbol, company, asset, or strategy instead of an exact internal "
                    "ID. Then resolve exactly one returned session and call again with its ID. "
                    "When wait_for_ready is true, an in-progress pre-run is allowed to finish before "
                    "market bars, simulation state, trades, plots, source, and logs are returned. "
                    "The exact-session call also reads configured account positions and recent "
                    "orders. Pass account only when the user explicitly names one; otherwise the "
                    "tool returns a deterministic evidence-based account match."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Exact active session ID returned by this tool's session list.",
                        },
                        "wait_for_ready": {
                            "type": "boolean",
                            "default": True,
                        },
                        "confirmed_bar_limit": {
                            "type": "integer",
                            "minimum": 50,
                            "maximum": _MAX_CONFIRMED_BARS,
                            "default": _DEFAULT_CONFIRMED_BARS,
                        },
                        "include_recent_logs": {
                            "type": "boolean",
                            "default": True,
                        },
                        "account": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Human-readable configured account name explicitly supplied by "
                                "the user, for example 'bitget sub2'. Omit it to match across all "
                                "configured accounts from positions and recent order history."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": _CAPTURE_TOOL_NAME,
                "description": (
                    "Capture the local chart for a ready session context as a PNG image. Use only "
                    "the exact session_id and generation_id returned by "
                    "get_session_evaluation_context. The server validates the local URL and rejects "
                    "stale generations."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "minLength": 1},
                        "generation_id": {"type": "string", "minLength": 1},
                        "width": {
                            "type": "integer",
                            "minimum": 800,
                            "maximum": 1920,
                            "default": 1440,
                        },
                        "height": {
                            "type": "integer",
                            "minimum": 600,
                            "maximum": 1400,
                            "default": 1000,
                        },
                    },
                    "required": ["session_id", "generation_id"],
                    "additionalProperties": False,
                },
            },
        ]

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.bridge.bind_loop(loop)

    def unbind_loop(self) -> None:
        self.bridge.unbind_loop()

    def handle_server_request(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method != "item/tool/call" or not isinstance(params, dict):
            return {}
        tool = params.get("tool")
        if tool not in self.names:
            return {}
        try:
            arguments = self._validate(tool, params.get("arguments"))
            operation = "context" if tool == _CONTEXT_TOOL_NAME else "capture"
            result = self.bridge.execute(operation, arguments)
            if operation == "capture":
                image_path = Path(result.pop("image_path"))
                image_url = "data:image/png;base64," + base64.b64encode(
                    image_path.read_bytes()
                ).decode("ascii")
                return {
                    "success": True,
                    "contentItems": [
                        {"type": "inputText", "text": json.dumps(result, ensure_ascii=False)},
                        {"type": "inputImage", "imageUrl": image_url},
                    ],
                }
            return self._tool_response(True, result)
        except SessionEvaluationToolError as exc:
            return self._tool_response(False, {"error": str(exc)})
        except Exception as exc:
            print(f"[ai] Session evaluation tool failed: {type(exc).__name__}")
            return self._tool_response(
                False,
                {"error": f"Session evaluation failed ({type(exc).__name__})"},
            )

    @staticmethod
    def _validate(tool: str, raw_arguments: Any) -> dict[str, Any]:
        if not isinstance(raw_arguments, dict):
            raise SessionEvaluationToolError("Tool arguments must be an object")
        if tool == _CONTEXT_TOOL_NAME:
            allowed = {
                "session_id",
                "wait_for_ready",
                "confirmed_bar_limit",
                "include_recent_logs",
                "account",
            }
            unexpected = sorted(set(raw_arguments) - allowed)
            if unexpected:
                raise SessionEvaluationToolError(
                    f"Unexpected argument field(s): {', '.join(unexpected)}"
                )
            session_id = raw_arguments.get("session_id")
            if session_id is not None and (
                not isinstance(session_id, str) or not session_id.strip()
            ):
                raise SessionEvaluationToolError("session_id must be a non-empty string")
            account = raw_arguments.get("account")
            if account is not None and (
                not isinstance(account, str) or not account.strip()
            ):
                raise SessionEvaluationToolError("account must be a non-empty string")
            limit = raw_arguments.get("confirmed_bar_limit", _DEFAULT_CONFIRMED_BARS)
            if not isinstance(limit, int) or isinstance(limit, bool):
                raise SessionEvaluationToolError("confirmed_bar_limit must be an integer")
            if not 50 <= limit <= _MAX_CONFIRMED_BARS:
                raise SessionEvaluationToolError(
                    f"confirmed_bar_limit must be between 50 and {_MAX_CONFIRMED_BARS}"
                )
            return {
                "session_id": session_id.strip() if isinstance(session_id, str) else None,
                "wait_for_ready": bool(raw_arguments.get("wait_for_ready", True)),
                "confirmed_bar_limit": limit,
                "include_recent_logs": bool(raw_arguments.get("include_recent_logs", True)),
                "account": account.strip() if isinstance(account, str) else None,
            }

        allowed = {"session_id", "generation_id", "width", "height"}
        unexpected = sorted(set(raw_arguments) - allowed)
        if unexpected:
            raise SessionEvaluationToolError(
                f"Unexpected argument field(s): {', '.join(unexpected)}"
            )
        for field in ("session_id", "generation_id"):
            if not isinstance(raw_arguments.get(field), str) or not raw_arguments[field].strip():
                raise SessionEvaluationToolError(f"{field} must be a non-empty string")
        width = raw_arguments.get("width", 1440)
        height = raw_arguments.get("height", 1000)
        if not isinstance(width, int) or isinstance(width, bool) or not 800 <= width <= 1920:
            raise SessionEvaluationToolError("width must be an integer between 800 and 1920")
        if not isinstance(height, int) or isinstance(height, bool) or not 600 <= height <= 1400:
            raise SessionEvaluationToolError("height must be an integer between 600 and 1400")
        return {
            "session_id": raw_arguments["session_id"].strip(),
            "generation_id": raw_arguments["generation_id"].strip(),
            "width": width,
            "height": height,
        }

    @staticmethod
    def _tool_response(success: bool, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": success,
            "contentItems": [{
                "type": "inputText",
                "text": json.dumps(payload, ensure_ascii=False),
            }],
        }
