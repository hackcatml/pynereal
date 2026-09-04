from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time
import tomllib
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _copy_scripts(source: Path, destination: Path) -> None:
    def ignored(directory: str, names: list[str]) -> set[str]:
        root = Path(directory)
        return {
            name
            for name in names
            if name.startswith(".")
            or name == "__pycache__"
            or Path(name).suffix.lower() in {".pyc", ".pyo"}
            or (root / name).is_symlink()
        }

    shutil.copytree(source, destination, ignore=ignored)


def _isolated_config(repo_root: Path) -> dict[str, Any]:
    config_path = repo_root / "workdir" / "config" / "realtime_trade.toml"
    try:
        with config_path.open("rb") as handle:
            config = copy.deepcopy(tomllib.load(handle))
    except FileNotFoundError:
        config = {}
    pyne = config.setdefault("pyne", {})
    realtime = config.setdefault("realtime", {})
    webhook = config.setdefault("webhook", {})
    pyne["no_progress"] = True
    pyne["no_report"] = False
    realtime["enabled"] = False
    webhook["enabled"] = False
    webhook["telegram_notification"] = False
    webhook.pop("url", None)
    return config


def _apply_input_overrides(script_path: Path, values: dict[str, Any]) -> None:
    if not values:
        return
    import tomlkit

    settings_path = script_path.with_suffix(".toml")
    if settings_path.is_file():
        document = tomlkit.parse(settings_path.read_text(encoding="utf-8"))
    else:
        document = tomlkit.document()
        document.add("script", tomlkit.table())
    if "inputs" not in document:
        document.add("inputs", tomlkit.table())
    inputs = document["inputs"]
    for input_id, value in values.items():
        if input_id not in inputs:
            inputs.add(input_id, tomlkit.table())
        inputs[input_id]["value"] = value
    settings_path.write_text(tomlkit.dumps(document), encoding="utf-8")


def _run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    job_dir = Path(args.job_dir).resolve()
    runtime_dir = job_dir / "runtime"
    scripts_snapshot = runtime_dir / "scripts"
    data_snapshot = runtime_dir / "data.ohlcv"
    metadata_snapshot = runtime_dir / "data.toml"
    source_script = repo_root / "workdir" / "scripts" / args.script_path
    source_data = repo_root / "workdir" / "data" / args.data_path
    source_metadata = source_data.with_suffix(".toml")

    # Rich renders log records into an 80-column table when stdout is a file,
    # which hard-wraps messages long before the browser log panel boundary.
    os.environ["PYNE_NO_COLOR_LOG"] = "1"

    from pynecore.core.exchange_policy import tradingview_hides_zero_volume
    from pynecore.core.ohlcv_file import OHLCVReader, OHLCVWriter
    from pynecore.core.script_runner import ScriptRunner
    from pynecore.core.syminfo import SymInfo

    print(f"[backtest] preparing {args.script_path}", flush=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _copy_scripts(repo_root / "workdir" / "scripts", scripts_snapshot)
    script_snapshot = scripts_snapshot / args.script_path
    if not script_snapshot.is_file():
        raise FileNotFoundError(f"script snapshot was not created: {args.script_path}")
    try:
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        job = {}
    input_values = job.get("inputs")
    if not isinstance(input_values, dict):
        input_values = {}
    _apply_input_overrides(script_snapshot, input_values)

    syminfo = SymInfo.load_toml(source_metadata)
    time_from = int(args.time_from)
    time_to = int(args.time_to)
    skip_zero_volume = tradingview_hides_zero_volume(syminfo.prefix)
    with OHLCVReader(source_data) as reader:
        candles = list(
            reader.read_from(
                time_from,
                time_to,
                skip_zero_volume=skip_zero_volume,
            )
        )
    if not candles:
        raise ValueError("no OHLCV candles found in the selected range")
    with OHLCVWriter(data_snapshot, truncate=True) as writer:
        for candle in candles:
            writer.write(candle)
    shutil.copy2(source_metadata, metadata_snapshot)

    config = _isolated_config(repo_root)
    actual_from = int(candles[0].timestamp)
    actual_to = int(candles[-1].timestamp)
    print(
        "[backtest] input ready | "
        f"file={args.data_path} candles={len(candles)} "
        f"from={datetime.fromtimestamp(actual_from, UTC).isoformat()} "
        f"to={datetime.fromtimestamp(actual_to, UTC).isoformat()}",
        flush=True,
    )
    if input_values:
        print(
            "[backtest] inputs | "
            + ", ".join(f"{name}={value!r}" for name, value in input_values.items()),
            flush=True,
        )

    lib_dir = scripts_snapshot / "lib"
    lib_added = False
    if lib_dir.is_dir():
        sys.path.insert(0, str(lib_dir))
        lib_added = True
    runner: ScriptRunner | None = None
    try:
        runner = ScriptRunner(
            script_snapshot,
            iter(candles),
            syminfo,
            last_bar_index=len(candles) - 1,
            plot_path=job_dir / "plot.csv",
            strat_path=job_dir / "strategy.csv",
            trade_path=job_dir / "trades.csv",
            realtime_config=config,
            custom_inputs={},
            preload_ohlcv=candles,
        )
        (job_dir / "runtime-ready").touch()
        started = time.monotonic()

        print("[backtest] running", flush=True)
        runner.run()
        elapsed = time.monotonic() - started
        print(f"[backtest] completed in {elapsed:.3f}s", flush=True)
        return {
            "status": "completed",
            "candle_count": len(candles),
            "actual_time_from": actual_from,
            "actual_time_to": actual_to,
            "elapsed_seconds": elapsed,
        }
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
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--script-path", required=True)
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--time-from", required=True, type=int)
    parser.add_argument("--time-to", required=True, type=int)
    args = parser.parse_args()
    job_dir = Path(args.job_dir).resolve()
    result_path = job_dir / "worker_result.json"
    try:
        result = _run(args)
    except BaseException as exc:
        print(f"[backtest] failed: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        try:
            _write_json(
                result_path,
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        except OSError:
            pass
        return 1
    _write_json(result_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
