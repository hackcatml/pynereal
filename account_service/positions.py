from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AI_SCRIPTS = _PROJECT_ROOT / "ai" / "scripts"
if str(_AI_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_AI_SCRIPTS))

from asset import configured_accounts, read_provider_config, utc_now  # noqa: E402
from position import collect_one  # noqa: E402


SCHEMA_VERSION = "1.0"


def _collect_positions_snapshot(
    config_path: str,
    *,
    account_names: tuple[str, ...] = (),
    exchange_id: str = "",
    symbols: tuple[str, ...] = (),
    account_type: str = "swap",
    all_derivative_scopes: bool = True,
) -> dict[str, Any]:
    data = read_provider_config(Path(config_path))
    accounts = configured_accounts(data)
    selected_names = set(account_names)
    if selected_names:
        accounts = [account for account in accounts if account.name in selected_names]
    if exchange_id:
        accounts = [account for account in accounts if account.exchange_id == exchange_id]
    if not accounts:
        return {
            "schema_version": SCHEMA_VERSION,
            "collected_at": utc_now(),
            "source": "exchange.positions",
            "read_only": True,
            "results": [],
            "summary": {
                "accounts": 0,
                "succeeded": 0,
                "failed": 0,
                "open_positions": 0,
            },
        }

    args = argparse.Namespace(
        timeout_ms=30_000,
        attempts=2,
        symbols=list(symbols),
        dex=None,
        include_closed=False,
        all_derivative_scopes=all_derivative_scopes,
    )
    bitget_accounts = [account for account in accounts if account.exchange_id == "bitget"]
    account_groups = ([bitget_accounts] if bitget_accounts else []) + [
        [account] for account in accounts if account.exchange_id != "bitget"
    ]

    def collect_group(group: list[Any]) -> list[dict[str, Any]]:
        return [
            collect_one(
                account.name,
                account.exchange_id,
                account_type,
                account.config,
                args,
                log_progress=False,
            )
            for account in group
        ]

    with ThreadPoolExecutor(
        max_workers=min(4, len(account_groups)),
        thread_name_prefix="account-position",
    ) as executor:
        results_by_account = {
            result["account"]: result
            for group_results in executor.map(collect_group, account_groups)
            for result in group_results
        }
        results = [results_by_account[account.name] for account in accounts]

    succeeded = sum(result.get("status") == "ok" for result in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "source": "exchange.positions",
        "read_only": True,
        "results": results,
        "summary": {
            "accounts": len(accounts),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "open_positions": sum(
                int(result.get("position_count") or 0)
                for result in results
            ),
        },
    }


def collect_positions_snapshot(config_path: str) -> dict[str, Any]:
    """Collect every configured account without exposing credentials to the parent."""

    return _collect_positions_snapshot(config_path)


def collect_positions_snapshot_scope(
    config_path: str,
    exchange_id: str,
    account_names: tuple[str, ...],
    symbol: str,
    account_type: str,
) -> dict[str, Any]:
    """Collect current positions only for one session's account candidates."""

    return _collect_positions_snapshot(
        config_path,
        account_names=account_names,
        exchange_id=exchange_id,
        symbols=(symbol,),
        account_type=account_type,
        all_derivative_scopes=False,
    )
