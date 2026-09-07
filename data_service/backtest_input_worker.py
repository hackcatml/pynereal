from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
import traceback
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _saved_input_values(script_path: Path) -> dict[str, Any]:
    settings_path = script_path.with_suffix(".toml")
    try:
        with settings_path.open("rb") as handle:
            document = tomllib.load(handle)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return {}
    raw_inputs = document.get("inputs")
    if not isinstance(raw_inputs, dict):
        return {}
    return {
        str(input_id): value["value"]
        for input_id, value in raw_inputs.items()
        if isinstance(value, dict) and "value" in value
    }


def inspect_inputs(repo_root: Path, script_path: str, data_path: str) -> dict[str, Any]:
    scripts_root = repo_root / "workdir" / "scripts"
    data_root = repo_root / "workdir" / "data"
    script_file = scripts_root / script_path
    metadata_file = (data_root / data_path).with_suffix(".toml")

    os.environ["PYNE_SAVE_SCRIPT_TOML"] = "0"

    from pynecore.core.script_runner import ScriptRunner
    from pynecore.core.syminfo import SymInfo

    runner: ScriptRunner | None = None
    lib_dir = scripts_root / "lib"
    lib_added = False
    if lib_dir.is_dir():
        sys.path.insert(0, str(lib_dir))
        lib_added = True
    try:
        runner = ScriptRunner(
            script_file,
            iter(()),
            SymInfo.load_toml(metadata_file),
            realtime_config={"realtime": {"enabled": False}},
            custom_inputs={},
            preload_ohlcv=[],
        )
        saved_values = _saved_input_values(script_file)
        result: list[dict[str, Any]] = []
        for input_id, input_data in runner.script.inputs.items():
            if not input_id:
                continue
            item = {
                field.name: _json_value(getattr(input_data, field.name))
                for field in fields(input_data)
                if field.name != "display"
            }
            item["value"] = _json_value(saved_values.get(input_id, input_data.defval))
            result.append(item)
        return {"inputs": result}
    finally:
        if runner is not None:
            runner.destroy()
        if lib_added:
            try:
                sys.path.remove(str(lib_dir))
            except ValueError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--script-path", required=True)
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--result-path", required=True)
    args = parser.parse_args()
    result_path = Path(args.result_path).resolve()
    try:
        result = inspect_inputs(
            Path(args.repo_root).resolve(),
            args.script_path,
            args.data_path,
        )
    except BaseException as exc:
        result = {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        exit_code = 1
    else:
        exit_code = 0
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
