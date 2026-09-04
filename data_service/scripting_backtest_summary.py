from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    result = _number(value)
    return int(result) if result is not None else None


def read_strategy_summary(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = [str(field or "").strip() for field in reader.fieldnames or []]
            amount_field = next(
                (field for field in fields if field.startswith("All ") and field != "All %"),
                None,
            )
            if not amount_field:
                return None
            rows = {
                str(row.get("Metric") or "").strip(): row
                for row in reader
                if str(row.get("Metric") or "").strip()
            }
    except (OSError, csv.Error):
        return None

    def amount(metric: str) -> float | None:
        return _number((rows.get(metric) or {}).get(amount_field))

    def percent(metric: str) -> float | None:
        return _number((rows.get(metric) or {}).get("All %"))

    return {
        "currency": amount_field.removeprefix("All ").strip(),
        "net_profit": amount("Net profit"),
        "net_profit_percent": percent("Net profit"),
        "max_drawdown": amount("Max equity drawdown"),
        "max_drawdown_percent": percent("Max equity drawdown"),
        "total_trades": _integer((rows.get("Total trades") or {}).get(amount_field)),
        "open_trades": _integer((rows.get("Total open trades") or {}).get(amount_field)),
        "win_rate": percent("Percent profitable"),
        "profit_factor": amount("Profit factor"),
        "commission": amount("Commission paid"),
        "buy_hold_return": amount("Buy & hold return"),
        "buy_hold_return_percent": percent("Buy & hold return"),
        "sharpe_ratio": amount("Sharpe ratio"),
    }
