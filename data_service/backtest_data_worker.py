from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _record_download_source(
    metadata_path: Path,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> None:
    text = metadata_path.read_text(encoding="utf-8").rstrip()
    if "[pynereal_download]" in text:
        return
    metadata_path.write_text(
        f'{text}\n\n[pynereal_download]\n'
        f'exchange = "{exchange.lower()}"\n'
        f'symbol = "{symbol.upper()}"\n'
        f'timeframe = "{timeframe}"\n',
        encoding="utf-8",
    )


def _install_data_pair(source: Path, target: Path) -> None:
    token = uuid.uuid4().hex[:12]
    temporary_data = target.with_name(f".{target.name}.{token}.tmp")
    target_metadata = target.with_suffix(".toml")
    temporary_metadata = target_metadata.with_name(f".{target_metadata.name}.{token}.tmp")
    try:
        shutil.copy2(source, temporary_data)
        shutil.copy2(source.with_suffix(".toml"), temporary_metadata)
        os.replace(temporary_metadata, target_metadata)
        os.replace(temporary_data, target)
    finally:
        temporary_data.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)


def _first_available_ohlcv_timestamp(
    provider: Any,
    *,
    now: datetime | None = None,
) -> datetime:
    client = provider._client
    client.load_markets()
    market = client.market(provider.symbol)
    raw_starts = [
        market.get("created"),
        *((market.get("info") or {}).get(key) for key in (
            "onboardDate",
            "launchTime",
            "listTime",
            "openTime",
            "contTdSwTime",
        )),
    ]
    starts: list[int] = []
    for value in raw_starts:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            continue
        if timestamp <= 0:
            continue
        starts.append(timestamp * 1000 if timestamp < 100_000_000_000 else timestamp)
    if starts:
        return datetime.fromtimestamp(min(starts) / 1000, UTC)

    current = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    interval_ms = int(client.parse_timeframe(provider.xchg_timeframe) * 1000)
    latest_success: tuple[int, int] | None = None
    failed_days: int | None = None

    def probe(days: int) -> tuple[int, int] | None:
        candidate = current - timedelta(days=days)
        candidate_ms = int(candidate.timestamp() * 1000)
        rows = client.fetch_ohlcv(
            provider.symbol,
            timeframe=provider.xchg_timeframe,
            since=candidate_ms,
            limit=200,
        )
        timestamps = [int(row[0]) for row in rows if row and row[0] is not None]
        if not timestamps:
            return None
        return candidate_ms, min(timestamps)

    days = 1
    while days <= 365 * 20:
        result = probe(days)
        if result is None:
            failed_days = days
            break
        candidate_ms, first_ms = result
        latest_success = (days, first_ms)
        if first_ms > candidate_ms + interval_ms * 2:
            return datetime.fromtimestamp(first_ms / 1000, UTC)
        days *= 2

    if latest_success is None:
        raise RuntimeError("the exchange did not return any OHLCV history")

    if failed_days is not None:
        successful_days = latest_success[0]
        while failed_days - successful_days > 1:
            middle_days = (successful_days + failed_days) // 2
            result = probe(middle_days)
            if result is None:
                failed_days = middle_days
                continue
            candidate_ms, first_ms = result
            successful_days = middle_days
            latest_success = (middle_days, first_ms)
            if first_ms > candidate_ms + interval_ms * 2:
                return datetime.fromtimestamp(first_ms / 1000, UTC)

    return datetime.fromtimestamp(latest_success[1] / 1000, UTC)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    data_root = (repo_root / "workdir" / "data").resolve()
    config_root = (repo_root / "workdir" / "config").resolve()

    from pynecore.cli.commands.data import (
        AvailableProvidersEnum,
        download,
        parse_date_or_days,
    )
    from pynecore.providers.ccxt import CCXTProvider

    provider = AvailableProvidersEnum.CCXT
    target = f"{args.exchange}:{args.symbol}".upper()
    canonical_output_path = CCXTProvider.get_ohlcv_path(
        target,
        args.timeframe,
        data_root,
    ).resolve()
    output_path = Path(args.data_path).resolve() if args.data_path else canonical_output_path
    try:
        output_path.relative_to(data_root)
    except ValueError as exc:
        raise ValueError("OHLCV output must stay inside the repository data directory") from exc
    if args.action == "update":
        if not output_path.is_file() or not output_path.with_suffix(".toml").is_file():
            raise FileNotFoundError("selected OHLCV data pair was not found")
        time_from: datetime | str = "continue"
    else:
        if output_path.exists() or output_path.with_suffix(".toml").exists():
            raise FileExistsError("OHLCV data already exists; select it and use Update")
        if args.history_since:
            time_from = parse_date_or_days(args.history_since)
        else:
            discovery = CCXTProvider(
                symbol=target,
                timeframe=args.timeframe,
                ohlv_dir=data_root,
            )
            try:
                time_from = _first_available_ohlcv_timestamp(discovery)
            finally:
                close = getattr(discovery._client, "close", None)
                if callable(close):
                    close()

    from pynecore.cli.app import app_state

    with tempfile.TemporaryDirectory(
        prefix="pynereal-backtest-data-",
        dir=str(Path(args.result_path).resolve().parent),
    ) as temporary:
        staging_workdir = Path(temporary) / "workdir"
        staging_data = staging_workdir / "data"
        staging_config = staging_workdir / "config"
        staging_data.mkdir(parents=True)
        staging_config.mkdir(parents=True)
        shutil.copy2(config_root / "providers.toml", staging_config / "providers.toml")
        app_state.workdir = staging_workdir
        staging_output = CCXTProvider.get_ohlcv_path(
            target,
            args.timeframe,
            staging_data,
        ).resolve()
        if args.action == "update":
            shutil.copy2(output_path, staging_output)
            shutil.copy2(output_path.with_suffix(".toml"), staging_output.with_suffix(".toml"))
        download(
            provider=provider,
            symbol=target,
            list_symbols=False,
            timeframe=args.timeframe,
            time_from=time_from,
            time_to=datetime.now(UTC).replace(second=0, microsecond=0),
            show_info=False,
            force_save_info=False,
            truncate=False,
            chunk_size=None if args.exchange.lower() == "hyperliquid" else 100,
        )
        if not staging_output.is_file() or not staging_output.with_suffix(".toml").is_file():
            raise RuntimeError("download completed without an OHLCV data pair")
        _record_download_source(
            staging_output.with_suffix(".toml"),
            exchange=args.exchange,
            symbol=args.symbol,
            timeframe=args.timeframe,
        )
        _install_data_pair(staging_output, output_path)
    return {
        "status": "completed",
        "data_path": output_path.relative_to(data_root).as_posix(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--action", choices=("download", "update"), required=True)
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--history-since", default="")
    parser.add_argument("--data-path", default="")
    args = parser.parse_args()
    result_path = Path(args.result_path).resolve()
    try:
        result = _run(args)
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        result = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        try:
            _write_result(result_path, result)
        except OSError:
            pass
        return 1
    _write_result(result_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
