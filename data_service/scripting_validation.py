from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any


VALIDATOR_VERSION = 2
_MAX_SYNTAX_ERRORS = 50

_SCRIPT_DECORATORS = {"strategy", "indicator", "library"}
_INDICATOR_STRATEGY_CALLS = {"entry", "close", "close_all", "exit", "order"}


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _diagnostic(
    *,
    severity: str,
    code: str,
    message: str,
    relative_path: str,
    node: ast.AST | None = None,
    line: int | None = None,
    column: int | None = None,
) -> dict[str, Any]:
    resolved_line = line if line is not None else getattr(node, "lineno", 1)
    resolved_column = column if column is not None else getattr(node, "col_offset", 0) + 1
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "file": relative_path,
        "line": max(int(resolved_line or 1), 1),
        "column": max(int(resolved_column or 1), 1),
    }


class ScriptingValidator:
    def __init__(self, scripts_root: Path, project_root: Path) -> None:
        self.scripts_root = scripts_root.resolve(strict=False)
        self.project_root = project_root.resolve(strict=False)

    def validate(self, relative_path: str, content: str) -> dict[str, Any]:
        diagnostics: list[dict[str, Any]] = []
        script_kind = "module"
        runnable = False

        try:
            tree = ast.parse(content, filename=relative_path, mode="exec")
        except SyntaxError:
            diagnostics.extend(self._syntax_diagnostics(relative_path, content))
            return self._result(relative_path, script_kind, runnable, diagnostics)

        try:
            compile(tree, relative_path, "exec", dont_inherit=True)
        except SyntaxError as exc:
            diagnostics.append(
                _diagnostic(
                    severity="error",
                    code="python.compile",
                    message=exc.msg or "Python could not compile this file.",
                    relative_path=relative_path,
                    line=exc.lineno,
                    column=exc.offset,
                )
            )

        declarations = self._script_declarations(tree)
        if declarations:
            script_kind = declarations[0][0]
            runnable = len(declarations) == 1 and declarations[0][1].name == "main"
            if len(declarations) > 1:
                diagnostics.append(
                    _diagnostic(
                        severity="error",
                        code="pyne.multiple_declarations",
                        message=(
                            "Only one script strategy, indicator, or library "
                            "declaration is allowed."
                        ),
                        relative_path=relative_path,
                        node=declarations[1][1],
                    )
                )
                runnable = False
            for kind, function in declarations:
                if function.name == "main":
                    continue
                diagnostics.append(
                    _diagnostic(
                        severity="error",
                        code="pyne.main_required",
                        message=f"@script.{kind} must decorate a module-level main function.",
                        relative_path=relative_path,
                        node=function,
                    )
                )
                runnable = False

        diagnostics.extend(self._import_diagnostics(tree, relative_path))
        if script_kind == "indicator":
            diagnostics.extend(self._indicator_call_diagnostics(tree, relative_path))

        diagnostics.sort(
            key=lambda item: (
                int(item["line"]),
                int(item["column"]),
                0 if item["severity"] == "error" else 1,
                str(item["code"]),
            )
        )
        return self._result(relative_path, script_kind, runnable, diagnostics)

    @staticmethod
    def _syntax_diagnostics(
        relative_path: str,
        content: str,
    ) -> list[dict[str, Any]]:
        lines = content.splitlines()
        if not lines:
            lines = [""]
        working_lines = list(lines)
        diagnostics: list[dict[str, Any]] = []
        seen: set[tuple[int, int, str]] = set()
        replaced_lines: set[int] = set()

        for _ in range(min(len(lines) + 1, _MAX_SYNTAX_ERRORS)):
            try:
                ast.parse("\n".join(working_lines), filename=relative_path, mode="exec")
                break
            except SyntaxError as exc:
                line = max(min(int(exc.lineno or 1), len(working_lines)), 1)
                column = max(int(exc.offset or 1), 1)
                message = exc.msg or "Invalid Python syntax."
                key = (line, column, message)
                if key not in seen:
                    seen.add(key)
                    diagnostics.append(
                        _diagnostic(
                            severity="error",
                            code="python.syntax",
                            message=message,
                            relative_path=relative_path,
                            line=line,
                            column=column,
                        )
                    )

                line_index = line - 1
                if line_index in replaced_lines:
                    break
                replaced_lines.add(line_index)
                working_lines[line_index] = ScriptingValidator._syntax_replacement_line(
                    working_lines,
                    line_index,
                    message,
                )

        diagnostics.sort(
            key=lambda item: (
                int(item["line"]),
                int(item["column"]),
                str(item["message"]),
            )
        )
        return diagnostics

    @staticmethod
    def _syntax_replacement_line(
        lines: list[str],
        line_index: int,
        message: str,
    ) -> str:
        original = lines[line_index]
        indentation = original[: len(original) - len(original.lstrip())]
        stripped = original.lstrip()
        block_prefixes = (
            "async def ",
            "async for ",
            "async with ",
            "def ",
            "class ",
            "if ",
            "elif ",
            "else",
            "for ",
            "while ",
            "try",
            "except",
            "finally",
            "with ",
            "match ",
            "case ",
        )
        for following in lines[line_index + 1 :]:
            if not following.strip() or following.lstrip().startswith("#"):
                continue
            following_indent = len(following) - len(following.lstrip())
            if stripped.startswith(block_prefixes) and following_indent > len(indentation):
                return f"{indentation}if True:"
            break
        if message.startswith("expected an indented block"):
            return f"{indentation}    pass"
        return f"{indentation}pass"

    @staticmethod
    def _script_declarations(
        tree: ast.Module,
    ) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
        declarations: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                dotted = _dotted_name(target)
                parts = dotted.split(".")
                if len(parts) < 2 or parts[-2] != "script" or parts[-1] not in _SCRIPT_DECORATORS:
                    continue
                declarations.append((parts[-1], node))
        return declarations

    def _import_diagnostics(
        self,
        tree: ast.Module,
        relative_path: str,
    ) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = []
        current_file = self.scripts_root / relative_path
        seen: set[tuple[int, str]] = set()
        for node in ast.walk(tree):
            modules: list[tuple[str, int]] = []
            if isinstance(node, ast.Import):
                modules.extend((alias.name, 0) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append((node.module or "", int(node.level or 0)))
            else:
                continue
            for module, level in modules:
                key = (level, module)
                if key in seen:
                    continue
                seen.add(key)
                if self._module_exists(current_file, module, level):
                    continue
                display_name = f"{'.' * level}{module}" or "."
                diagnostics.append(
                    _diagnostic(
                        severity="warning",
                        code="python.import_not_found",
                        message=(
                            "Import could not be resolved without executing code: "
                            f"{display_name}"
                        ),
                        relative_path=relative_path,
                        node=node,
                    )
                )
        return diagnostics

    def _module_exists(self, current_file: Path, module: str, level: int) -> bool:
        if level:
            base = current_file.parent
            for _ in range(level - 1):
                base = base.parent
            return self._module_path_exists(base, module)

        if not module:
            return False
        top_level = module.split(".", 1)[0]
        if top_level in sys.stdlib_module_names:
            return True
        for base in (current_file.parent, self.scripts_root, self.project_root):
            if self._module_path_exists(base, module):
                return True
        try:
            return importlib.util.find_spec(top_level) is not None
        except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
            return False

    @staticmethod
    def _module_path_exists(base: Path, module: str) -> bool:
        if not module:
            return (base / "__init__.py").is_file()
        module_path = base.joinpath(*module.split("."))
        return module_path.with_suffix(".py").is_file() or (
            module_path.is_dir() and (module_path / "__init__.py").is_file()
        )

    @staticmethod
    def _indicator_call_diagnostics(
        tree: ast.Module,
        relative_path: str,
    ) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted_name(node.func)
            parts = dotted.split(".")
            if (
                len(parts) < 2
                or parts[-2] != "strategy"
                or parts[-1] not in _INDICATOR_STRATEGY_CALLS
            ):
                continue
            diagnostics.append(
                _diagnostic(
                    severity="error",
                    code="pyne.indicator_strategy_call",
                    message=f"@script.indicator cannot call strategy.{parts[-1]}().",
                    relative_path=relative_path,
                    node=node,
                )
            )
        return diagnostics

    @staticmethod
    def _result(
        relative_path: str,
        script_kind: str,
        runnable: bool,
        diagnostics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        error_count = sum(item["severity"] == "error" for item in diagnostics)
        warning_count = sum(item["severity"] == "warning" for item in diagnostics)
        status = "error" if error_count else "warning" if warning_count else "passed"
        return {
            "path": relative_path,
            "validator_version": VALIDATOR_VERSION,
            "status": status,
            "script_kind": script_kind,
            "runnable": bool(runnable and not error_count),
            "summary": {
                "errors": error_count,
                "warnings": warning_count,
            },
            "diagnostics": diagnostics,
        }
