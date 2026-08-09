from __future__ import annotations

import inspect
import math
from dataclasses import asdict, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from pynecore.core.strategy_stats import calculate_strategy_statistics
from pynecore.types.na import NA


SCHEMA_VERSION = "1.2"
MAX_OPEN_TRADES = 500
MAX_CLOSED_TRADES = 200
MAX_ACTIVE_ORDERS = 500
MAX_EQUITY_SAMPLES = 400
MAX_RECENT_EQUITY = 100


def _json_value(value: Any) -> Any:
    if isinstance(value, NA) or value is inspect.Parameter.empty:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    raw_value = getattr(value, "value", None)
    if isinstance(raw_value, (str, bool, int, float)):
        return _json_value(raw_value)
    return str(value)


def _selected_fields(value: Any, names: Iterable[str]) -> dict[str, Any]:
    return {
        name: _json_value(getattr(value, name, None))
        for name in names
    }


def _trade_payload(trade: Any) -> dict[str, Any]:
    return _selected_fields(trade, (
        "size",
        "sign",
        "entry_id",
        "entry_bar_index",
        "entry_time",
        "entry_price",
        "entry_comment",
        "entry_equity",
        "exit_id",
        "exit_bar_index",
        "exit_time",
        "exit_price",
        "exit_comment",
        "exit_equity",
        "commission",
        "profit",
        "profit_percent",
        "max_drawdown",
        "max_drawdown_percent",
        "max_runup",
        "max_runup_percent",
        "cum_profit",
        "cum_profit_percent",
        "cum_max_drawdown",
        "cum_max_runup",
    ))


def _order_payload(order: Any, bucket: str) -> dict[str, Any]:
    payload = _selected_fields(order, (
        "order_id",
        "exit_id",
        "size",
        "sign",
        "limit",
        "stop",
        "trail_price",
        "trail_offset",
        "trail_triggered",
        "profit_ticks",
        "loss_ticks",
        "trail_points_ticks",
        "is_market_order",
        "cancelled",
        "bar_index",
        "oca_name",
        "oca_type",
        "comment",
    ))
    payload["bucket"] = bucket
    return payload


def _active_orders(position: Any) -> tuple[list[dict[str, Any]], bool]:
    pending: list[dict[str, Any]] = []
    seen: set[int] = set()
    for bucket, values in (
        ("market", getattr(position, "market_orders", {})),
        ("entry", getattr(position, "entry_orders", {})),
        ("exit", getattr(position, "exit_orders", {})),
    ):
        if not isinstance(values, dict):
            continue
        for order in values.values():
            identity = id(order)
            if identity in seen:
                continue
            seen.add(identity)
            pending.append(_order_payload(order, bucket))
            if len(pending) >= MAX_ACTIVE_ORDERS:
                return pending, True
    return pending, False


def _input_payload(runner: Any) -> list[dict[str, Any]]:
    script = runner.script
    try:
        parameters = inspect.signature(runner.script_module.main).parameters
    except (TypeError, ValueError):
        parameters = {}

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_name, metadata in getattr(script, "inputs", {}).items():
        if raw_name is None:
            continue
        name = str(raw_name).removesuffix("__global__")
        if name in seen:
            continue
        seen.add(name)
        parameter = parameters.get(name)
        resolved = parameter.default if parameter is not None else getattr(metadata, "defval", None)
        if resolved is inspect.Parameter.empty:
            resolved = getattr(metadata, "defval", None)

        if is_dataclass(metadata):
            metadata_payload = {
                field.name: _json_value(getattr(metadata, field.name))
                for field in fields(metadata)
                if field.name != "id"
            }
        else:
            metadata_payload = {}
        result.append({
            "name": name,
            "value": _json_value(resolved),
            "metadata": metadata_payload,
        })
    return result


def _strategy_config(script: Any) -> dict[str, Any]:
    return _selected_fields(script, (
        "title",
        "shorttitle",
        "script_type",
        "overlay",
        "pyramiding",
        "calc_on_order_fills",
        "calc_on_every_tick",
        "max_bars_back",
        "calc_bars_count",
        "backtest_fill_limits_assumption",
        "default_qty_type",
        "default_qty_value",
        "initial_capital",
        "currency",
        "slippage",
        "commission_type",
        "commission_value",
        "process_orders_on_close",
        "close_entries_rule",
        "margin_long",
        "margin_short",
        "risk_free_rate",
        "use_bar_magnifier",
        "fill_orders_on_standard_ohlc",
    ))


def _risk_payload(position: Any) -> dict[str, Any]:
    return _selected_fields(position, (
        "max_drawdown",
        "max_runup",
        "max_equity",
        "min_equity",
        "risk_allowed_direction",
        "risk_max_cons_loss_days",
        "risk_max_drawdown_value",
        "risk_max_drawdown_type",
        "risk_max_intraday_filled_orders",
        "risk_max_intraday_loss_value",
        "risk_max_intraday_loss_type",
        "risk_max_position_size",
        "risk_cons_loss_days",
        "risk_intraday_filled_orders",
        "risk_intraday_start_equity",
        "risk_halt_trading",
    ))


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _numbers_match(actual: Any, expected: float | None) -> bool:
    actual_number = _finite_number(actual)
    if actual_number is None or expected is None:
        return actual_number is None and expected is None
    return math.isclose(
        actual_number,
        expected,
        rel_tol=1e-9,
        abs_tol=max(1e-9, abs(expected) * 1e-9),
    )


def _open_trade_ledger(
    position: Any,
    open_trades: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    net_size = 0.0
    gross_size = 0.0
    entry_cost = 0.0
    valid_trades: list[tuple[float, float]] = []
    for trade in open_trades:
        size = _finite_number(getattr(trade, "size", None))
        entry_price = _finite_number(getattr(trade, "entry_price", None))
        if size is None or entry_price is None:
            continue
        net_size += size
        gross_size += abs(size)
        entry_cost += abs(size) * entry_price
        valid_trades.append((size, entry_price))

    weighted_average_price = entry_cost / gross_size if gross_size else None
    current_price = _finite_number(getattr(position, "c", None))
    unrealized_pnl = (
        sum(size * (current_price - entry_price) for size, entry_price in valid_trades)
        if current_price is not None
        else None
    )
    if not open_trades:
        unrealized_pnl = 0.0

    ledger = {
        "accounting_basis": "pine_fifo_open_trade_ledger",
        "trade_count": len(open_trades),
        "valid_trade_count": len(valid_trades),
        "net_size": net_size,
        "gross_size": gross_size,
        "entry_cost": entry_cost,
        "weighted_average_price": weighted_average_price,
        "unrealized_pnl": unrealized_pnl,
    }
    checks = {
        "position_size_matches": _numbers_match(
            getattr(position, "size", None), net_size
        ),
        "entry_cost_matches": _numbers_match(
            getattr(position, "entry_summ", None), entry_cost
        ),
        "average_price_matches": _numbers_match(
            getattr(position, "avg_price", None), weighted_average_price
        ),
        "unrealized_pnl_matches": _numbers_match(
            getattr(position, "openprofit", None), unrealized_pnl
        ),
    }
    consistency_warnings: list[str] = []
    if len(valid_trades) != len(open_trades):
        consistency_warnings.append(
            "Some open trades could not be included in ledger calculations."
        )
    labels = {
        "position_size_matches": "Position size differs from the open-trade ledger.",
        "entry_cost_matches": "Entry cost differs from the open-trade ledger.",
        "average_price_matches": "Pine FIFO average price differs from the open-trade ledger.",
        "unrealized_pnl_matches": "Pine FIFO unrealized PnL differs from the open-trade ledger.",
    }
    for key, message in labels.items():
        if not checks[key]:
            consistency_warnings.append(message)
    return ledger, {**checks, "warnings": consistency_warnings}


def _position_lifecycle(position: Any, calculated_through: int | None) -> dict[str, Any]:
    size = _finite_number(getattr(position, "size", None)) or 0.0
    opened_at_ms = getattr(position, "position_open_time", None)
    opened_bar_index = getattr(position, "position_open_bar_index", None)
    if not size or not isinstance(opened_at_ms, int):
        return {
            "accounting_basis": "continuous_non_flat_position",
            "opened_at_ms": None,
            "opened_at_iso": None,
            "opened_bar_index": None,
            "holding_duration_seconds": 0,
        }

    opened_at_seconds = opened_at_ms / 1000.0
    holding_duration_seconds = None
    if isinstance(calculated_through, int):
        holding_duration_seconds = max(
            0,
            int(calculated_through - opened_at_seconds),
        )
    return {
        "accounting_basis": "continuous_non_flat_position",
        "opened_at_ms": opened_at_ms,
        "opened_at_iso": datetime.fromtimestamp(opened_at_seconds, UTC).isoformat(),
        "opened_bar_index": _json_value(opened_bar_index),
        "holding_duration_seconds": holding_duration_seconds,
    }


def _curve_with_current_value(runner: Any, position: Any) -> list[float]:
    raw_curve = getattr(runner, "equity_curve", [])
    curve = raw_curve if isinstance(raw_curve, list) else list(raw_curve)
    processed_count = max(0, int(getattr(runner, "bar_index", -1)) + 1)
    if len(curve) < processed_count:
        current = _json_value(getattr(position, "equity", None))
        if isinstance(current, (int, float)):
            return [*curve, float(current)]
    return curve


def _equity_point(runner: Any, curve: list[float], index: int) -> dict[str, Any]:
    timestamp = None
    bars = getattr(runner, "_all_ohlcv", None)
    if bars is not None and 0 <= index < len(bars):
        timestamp = int(bars[index].timestamp)
    return {
        "bar_index": index,
        "time": timestamp,
        "equity": curve[index],
    }


def _equity_curve_payload(runner: Any, curve: list[float]) -> dict[str, Any]:
    count = len(curve)
    if count <= MAX_EQUITY_SAMPLES:
        sample_indices = list(range(count))
    elif MAX_EQUITY_SAMPLES <= 1:
        sample_indices = [count - 1]
    else:
        sample_indices = sorted({
            round(index * (count - 1) / (MAX_EQUITY_SAMPLES - 1))
            for index in range(MAX_EQUITY_SAMPLES)
        })
    recent_start = max(0, count - MAX_RECENT_EQUITY)
    return {
        "point_count": count,
        "samples": [_equity_point(runner, curve, index) for index in sample_indices],
        "recent": [_equity_point(runner, curve, index) for index in range(recent_start, count)],
    }


def _request_security_payload() -> dict[str, Any]:
    try:
        from pynecore.lib.request import get_security_ctx

        context = get_security_ctx()
    except Exception:
        context = None
    if context is None:
        return {
            "enabled": False,
            "same_symbol_only": True,
            "timeframes": [],
            "expression_count": 0,
        }
    timeframe_cache = getattr(context, "_cache", {})
    expression_cache = getattr(context, "_expr_cache", {})
    return {
        "enabled": True,
        "same_symbol_only": True,
        "timeframes": sorted(str(value) for value in timeframe_cache),
        "expression_count": len(expression_cache),
    }


def _source_payload(source_hashes: dict[str, str] | None) -> dict[str, Any]:
    files = []
    for raw_path, digest in sorted((source_hashes or {}).items()):
        files.append({"name": Path(raw_path).name, "sha256": str(digest)})
    return {"files": files}


def build_strategy_snapshot(
    runner: Any,
    *,
    phase: str,
    calculated_through: int | None,
    calculation_generation_id: str,
    source_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    script = runner.script
    position = getattr(script, "position", None)
    if position is None:
        return {
            "type": "strategy_snapshot",
            "schema_version": SCHEMA_VERSION,
            "captured_at": datetime.now(UTC).isoformat(),
            "phase": phase,
            "calculated_through": calculated_through,
            "calculation_generation_id": calculation_generation_id,
            "strategy": {
                "config": _strategy_config(script),
                "inputs": _input_payload(runner),
                "source": _source_payload(source_hashes),
            },
            "simulation": None,
            "warnings": ["The loaded script does not expose strategy position state."],
        }

    open_trades_all = list(getattr(position, "open_trades", []))
    closed_trades_all = list(getattr(position, "closed_trades", []))
    open_trades = open_trades_all[-MAX_OPEN_TRADES:]
    closed_trades = closed_trades_all[-MAX_CLOSED_TRADES:]
    pending_orders, orders_truncated = _active_orders(position)
    warnings: list[str] = []
    if len(open_trades_all) > len(open_trades):
        warnings.append("Open trades were truncated in the wire snapshot.")
    if len(closed_trades_all) > len(closed_trades):
        warnings.append("Closed trades were truncated in the wire snapshot.")
    if orders_truncated:
        warnings.append("Active orders were truncated in the wire snapshot.")

    open_trade_ledger, consistency = _open_trade_ledger(position, open_trades_all)
    warnings.extend(
        f"Strategy state inconsistency: {warning}"
        for warning in consistency["warnings"]
    )

    curve = _curve_with_current_value(runner, position)
    try:
        statistics = asdict(calculate_strategy_statistics(
            position,
            float(script.initial_capital),
            curve if curve else None,
            getattr(runner, "first_price", None),
            getattr(runner, "last_price", None),
        ))
        statistics = _json_value(statistics)
    except Exception as exc:
        statistics = None
        warnings.append(f"Strategy statistics unavailable ({type(exc).__name__}).")

    size = _json_value(getattr(position, "size", 0.0))
    direction = "flat"
    if isinstance(size, (int, float)):
        direction = "long" if size > 0 else "short" if size < 0 else "flat"

    simulation = {
        "position": {
            "direction": direction,
            **_selected_fields(position, (
                "size",
                "sign",
                "avg_price",
                "aggregate_avg_price",
                "entry_equity",
                "entry_summ",
            )),
        },
        "pnl": _selected_fields(position, (
            "netprofit",
            "openprofit",
            "aggregate_openprofit",
            "grossprofit",
            "grossloss",
            "cum_profit",
            "open_commission",
            "equity",
        )),
        "trade_counts": {
            "open": len(open_trades_all),
            "closed": int(getattr(position, "closed_trades_count", len(closed_trades_all))),
            "winning": int(getattr(position, "wintrades", 0)),
            "even": int(getattr(position, "eventrades", 0)),
            "losing": int(getattr(position, "losstrades", 0)),
        },
        "open_trades": [_trade_payload(trade) for trade in open_trades],
        "open_trade_ledger": open_trade_ledger,
        "entry_open_ledger": _json_value(
            getattr(position, "_entry_open_ledger", {})
        ),
        "position_lifecycle": _position_lifecycle(position, calculated_through),
        "accounting": {
            "pine_fifo": {
                "average_price_field": "position.avg_price",
                "unrealized_pnl_field": "pnl.openprofit",
            },
            "aggregate": {
                "average_price_field": "position.aggregate_avg_price",
                "unrealized_pnl_field": "pnl.aggregate_openprofit",
                "preferred_for_live_risk": True,
                "included_in_strategy_equity": False,
            },
            "entry_bound": {
                "remaining_quantity_field": "entry_open_ledger",
                "preferred_for_entry_specific_remaining_quantity": True,
            },
        },
        "consistency": consistency,
        "recent_closed_trades": [_trade_payload(trade) for trade in closed_trades],
        "active_orders": pending_orders,
        "risk": _risk_payload(position),
        "statistics": statistics,
        "equity_curve": _equity_curve_payload(runner, curve),
        "current_bar": _selected_fields(position, ("o", "h", "l", "c")),
    }
    return {
        "type": "strategy_snapshot",
        "schema_version": SCHEMA_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "phase": phase,
        "calculated_through": calculated_through,
        "calculation_generation_id": calculation_generation_id,
        "runner_bar_index": int(getattr(runner, "bar_index", -1)),
        "strategy": {
            "config": _strategy_config(script),
            "inputs": _input_payload(runner),
            "request_security": _request_security_payload(),
            "source": _source_payload(source_hashes),
        },
        "simulation": simulation,
        "warnings": warnings,
    }
