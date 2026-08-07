from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pynecore.cli.app import app_state
from pynecore.core.csv_file import CSVReader
from pynecore.core.exchange_policy import tradingview_hides_zero_volume
from pynecore.core.ohlcv_file import OHLCVReader


SCHEMA_VERSION = "1.0"
MAX_PLOT_SERIES = 64
MAX_RECENT_EVENTS = 200
MAX_LOG_LINES = 200
MAX_LOG_CHARS = 30_000
MAX_SOURCE_CHARS = 120_000


@dataclass(frozen=True)
class EvaluationContextSeed:
    session_id: str
    provider: str
    exchange: str
    symbol: str
    market_type: str
    timeframe: str
    script_name: str
    ohlcv_path: Path
    plot_path: Path
    log_path: Path
    live_bars: list[list[Any]]
    plot_options: dict[str, dict[str, Any]]
    plotchar_history: list[dict[str, Any]]
    trades_history: list[dict[str, Any]]
    chart_info: dict[str, Any]
    calculation: dict[str, Any]
    strategy_snapshot: dict[str, Any] | None
    strategy_snapshot_generation_id: str | None


def capture_session_evaluation_seed(session: Any) -> EvaluationContextSeed:
    return EvaluationContextSeed(
        session_id=session.spec.id,
        provider=session.spec.provider,
        exchange=session.spec.exchange,
        symbol=session.spec.symbol,
        market_type=session.spec.market_type,
        timeframe=session.spec.timeframe,
        script_name=session.spec.script_name,
        ohlcv_path=Path(session.ohlcv_path),
        plot_path=Path(session.paths.plot_path),
        log_path=Path(session.paths.log_path),
        live_bars=copy.deepcopy(session.feed.state.live_bars),
        plot_options=copy.deepcopy(session.plot_options),
        plotchar_history=copy.deepcopy(session.plotchar_history[-MAX_RECENT_EVENTS:]),
        trades_history=copy.deepcopy(session.trades_history[-MAX_RECENT_EVENTS:]),
        chart_info=copy.deepcopy(session.chart_info),
        calculation=copy.deepcopy(session.calculation_state_payload()),
        strategy_snapshot=copy.deepcopy(session.strategy_snapshot),
        strategy_snapshot_generation_id=session.strategy_snapshot_generation_id,
    )


def _bar_payload(candle: Any) -> dict[str, Any]:
    return {
        "time": int(candle.timestamp),
        "open": float(candle.open),
        "high": float(candle.high),
        "low": float(candle.low),
        "close": float(candle.close),
        "volume": float(candle.volume),
    }


def _live_bar_payload(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 6:
        return None
    try:
        return {
            "time": int(raw[0] // 1000),
            "open": float(raw[1]),
            "high": float(raw[2]),
            "low": float(raw[3]),
            "close": float(raw[4]),
            "volume": float(raw[5]),
        }
    except (TypeError, ValueError):
        return None


def _read_market(
    seed: EvaluationContextSeed,
    *,
    confirmed_limit: int,
    latest_confirmed_bar: int | None,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    path = seed.ohlcv_path
    if not path.exists():
        return {
            "confirmed_bars": [],
            "forming_bar": None,
            "quality": {"status": "missing", "path_exists": False},
        }, ["The session OHLCV file is missing."]

    if latest_confirmed_bar is None and len(seed.live_bars) >= 2:
        previous_live_bar = _live_bar_payload(seed.live_bars[-2])
        if previous_live_bar is not None:
            latest_confirmed_bar = previous_live_bar["time"]

    skip_zero_volume = tradingview_hides_zero_volume(seed.exchange)
    confirmed: list[dict[str, Any]] = []
    forming: dict[str, Any] | None = None
    interval: int | None = None
    total_records = 0
    raw_zero_volume = 0
    scan_limit = max(confirmed_limit * 4, confirmed_limit + 100)
    try:
        with OHLCVReader(path) as reader:
            total_records = reader.size
            interval = int(reader.interval) if reader.interval is not None else None
            scanned = 0
            for index in range(reader.size - 1, -1, -1):
                candle = reader.read(index)
                if candle is None:
                    continue
                scanned += 1
                if float(candle.volume) == 0.0:
                    raw_zero_volume += 1
                    if skip_zero_volume:
                        if scanned >= scan_limit and len(confirmed) >= confirmed_limit:
                            break
                        continue
                timestamp = int(candle.timestamp)
                if latest_confirmed_bar is None or timestamp <= latest_confirmed_bar:
                    confirmed.append(_bar_payload(candle))
                    if len(confirmed) >= confirmed_limit:
                        break
                elif forming is None:
                    forming = _bar_payload(candle)
                if scanned >= scan_limit and len(confirmed) >= confirmed_limit:
                    break
            reader.close()
    except Exception as exc:
        return {
            "confirmed_bars": [],
            "forming_bar": None,
            "quality": {
                "status": "error",
                "path_exists": True,
                "error_type": type(exc).__name__,
            },
        }, [f"OHLCV data could not be read ({type(exc).__name__})."]

    confirmed.reverse()
    live_bars = seed.live_bars
    if live_bars:
        live_forming = _live_bar_payload(live_bars[-1])
        if live_forming is not None and (
            latest_confirmed_bar is None or live_forming["time"] > latest_confirmed_bar
        ):
            forming = live_forming

    duplicate_count = 0
    gap_count = 0
    previous_time: int | None = None
    for bar in confirmed:
        timestamp = bar["time"]
        if previous_time is not None:
            if timestamp == previous_time:
                duplicate_count += 1
            elif interval and timestamp - previous_time > interval:
                gap_count += max(1, (timestamp - previous_time) // interval - 1)
        previous_time = timestamp

    last_time = confirmed[-1]["time"] if confirmed else None
    if latest_confirmed_bar is not None and last_time is not None and last_time < latest_confirmed_bar:
        warnings.append("The returned OHLCV window ends before the calculation target.")
    quality = {
        "status": "ok" if confirmed else "empty",
        "path_exists": True,
        "total_file_records": total_records,
        "returned_confirmed_bars": len(confirmed),
        "first_returned_time": confirmed[0]["time"] if confirmed else None,
        "last_returned_time": last_time,
        "interval_seconds": interval,
        "duplicate_count_in_returned_window": duplicate_count,
        "gap_count_in_returned_window": gap_count,
        "zero_volume_records_seen_while_scanning": raw_zero_volume,
        "zero_volume_policy": "hidden" if skip_zero_volume else "kept",
    }
    return {
        "confirmed_bars": confirmed,
        "forming_bar": forming,
        "quality": quality,
    }, warnings


def _plot_value(value: Any, kind: str) -> float | int | None:
    if value is None or str(value) == "":
        return None
    try:
        return int(value) if kind == "bgcolor" else float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _read_plots(
    seed: EvaluationContextSeed,
    *,
    start_time: int | None,
    end_time: int | None,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    options_items = list(seed.plot_options.items())[:MAX_PLOT_SERIES]
    if not options_items or not seed.plot_path.exists() or start_time is None:
        return [], [], []
    rows = []
    warnings: list[str] = []
    try:
        with CSVReader(seed.plot_path) as reader:
            for candle in reader.read_from(start_time, end_time):
                rows.append(candle)
                if len(rows) > limit:
                    rows.pop(0)
            reader.close()
    except Exception as exc:
        return [], [], [f"Plot data could not be read ({type(exc).__name__})."]

    plots: list[dict[str, Any]] = []
    backgrounds: list[dict[str, Any]] = []
    for title, options in options_items:
        kind = str(options.get("kind") or "line")
        series = {
            "title": str(title),
            "kind": kind,
            "options": copy.deepcopy(options),
            "data": [
                {
                    "time": int(candle.timestamp),
                    "value": _plot_value(candle.extra_fields.get(title), kind),
                }
                for candle in rows
            ],
        }
        if kind == "bgcolor":
            backgrounds.append(series)
        else:
            plots.append(series)
    if len(seed.plot_options) > MAX_PLOT_SERIES:
        warnings.append("Plot series were truncated in the evaluation context.")
    return plots, backgrounds, warnings


def _read_source(seed: EvaluationContextSeed) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    source = str(seed.chart_info.get("script_source") or "")
    source_name = str(seed.chart_info.get("script_source_name") or seed.script_name or "")
    if not source and seed.script_name:
        scripts_root = app_state.scripts_dir.resolve()
        try:
            script_path = (scripts_root / seed.script_name).resolve()
            script_path.relative_to(scripts_root)
            source = script_path.read_text(encoding="utf-8")
            source_name = Path(seed.script_name).as_posix()
        except Exception as exc:
            warnings.append(f"Strategy source could not be read ({type(exc).__name__}).")
    truncated = len(source) > MAX_SOURCE_CHARS
    if truncated:
        source = source[:MAX_SOURCE_CHARS]
        warnings.append("Strategy source was truncated in the evaluation context.")
    return {
        "name": source_name,
        "content": source,
        "truncated": truncated,
    }, warnings


def _read_recent_logs(seed: EvaluationContextSeed) -> tuple[str, list[str]]:
    path = seed.log_path
    if not path.exists():
        return "", []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return "", [f"Runner logs could not be read ({type(exc).__name__})."]
    tail = "\n".join(text.splitlines()[-MAX_LOG_LINES:])
    if len(tail) > MAX_LOG_CHARS:
        tail = tail[-MAX_LOG_CHARS:]
    return tail, []


def collect_session_evaluation_context(
    seed: EvaluationContextSeed,
    *,
    generation_id: str,
    confirmed_bar_limit: int,
    include_recent_logs: bool,
) -> dict[str, Any]:
    calculation = copy.deepcopy(seed.calculation)
    strategy_snapshot = copy.deepcopy(seed.strategy_snapshot) or {}
    latest_confirmed = calculation.get("latest_confirmed_bar")
    market, warnings = _read_market(
        seed,
        confirmed_limit=confirmed_bar_limit,
        latest_confirmed_bar=latest_confirmed,
    )
    confirmed_bars = market["confirmed_bars"]
    start_time = confirmed_bars[0]["time"] if confirmed_bars else None
    plots, backgrounds, plot_warnings = _read_plots(
        seed,
        start_time=start_time,
        end_time=latest_confirmed,
        limit=confirmed_bar_limit,
    )
    warnings.extend(plot_warnings)
    source, source_warnings = _read_source(seed)
    warnings.extend(source_warnings)
    recent_logs = ""
    if include_recent_logs:
        recent_logs, log_warnings = _read_recent_logs(seed)
        warnings.extend(log_warnings)

    engine_strategy = strategy_snapshot.get("strategy") or {}
    simulation = strategy_snapshot.get("simulation")
    warnings.extend(
        str(value) for value in strategy_snapshot.get("warnings", []) if str(value).strip()
    )
    if seed.strategy_snapshot_generation_id != generation_id:
        warnings.append("Strategy snapshot generation changed during context collection.")

    script_title = str(
        seed.chart_info.get("script_title")
        or engine_strategy.get("config", {}).get("title")
        or seed.script_name
        or ""
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(UTC).isoformat(),
        "session": {
            "id": seed.session_id,
            "exchange": seed.exchange,
            "symbol": seed.symbol,
            "market_type": seed.market_type,
            "timeframe": seed.timeframe,
            "strategy_name": script_title,
            "script_path": seed.script_name,
        },
        "calculation": calculation,
        "market": market,
        "strategy": {
            "source": source,
            "source_hashes": (engine_strategy.get("source") or {}).get("files", []),
            "config": engine_strategy.get("config") or {},
            "inputs": engine_strategy.get("inputs") or [],
            "request_security": engine_strategy.get("request_security") or {},
            "simulation": simulation,
            "recent_trade_events": copy.deepcopy(seed.trades_history[-MAX_RECENT_EVENTS:]),
            "plots": plots,
            "plotchars": copy.deepcopy(seed.plotchar_history[-MAX_RECENT_EVENTS:]),
            "backgrounds": backgrounds,
            "recent_logs": recent_logs,
        },
        "warnings": list(dict.fromkeys(warnings)),
    }
