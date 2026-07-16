from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .file_tools import RestrictedFileTools
from .manual_alert_tool import ManualAlertTools
from .telegram_tool import TelegramMessageTool


class AIDynamicTools:
    """Route app-server dynamic tool calls to server-controlled handlers."""

    def __init__(self, project_root: Path, *, session_registry: Any) -> None:
        self.file_tools = RestrictedFileTools(project_root)
        self.manual_alert = ManualAlertTools(session_registry)
        self.telegram = TelegramMessageTool()

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [*self.file_tools.specs, *self.manual_alert.specs, self.telegram.spec]

    def bind_loop(self, loop: Any) -> None:
        self.manual_alert.bind_loop(loop)

    def unbind_loop(self) -> None:
        self.manual_alert.unbind_loop()

    def handle_server_request(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method != "item/tool/call":
            return {}
        if not isinstance(params, dict):
            return self._error_response("Tool call parameters must be an object")

        tool = params.get("tool")
        file_tool_names = {spec["name"] for spec in self.file_tools.specs}
        if tool in file_tool_names:
            return self.file_tools.handle_server_request(method, params)
        if tool in self.manual_alert.names:
            return self.manual_alert.handle_server_request(method, params)
        if tool == self.telegram.name:
            return self.telegram.handle_server_request(method, params)
        return self._error_response(f"Unknown AI tool: {tool!r}")

    @staticmethod
    def _error_response(error: str) -> dict[str, Any]:
        return {
            "success": False,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps({"error": error}, ensure_ascii=False),
                }
            ],
        }
