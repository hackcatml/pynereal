from __future__ import annotations

from pathlib import Path
from typing import Tuple

from pynecore.cli.app import app_state


def _tf_key(timeframe: str) -> str:
    tf_modifier = timeframe[-1]
    tf_value = int(timeframe[:-1])
    if tf_modifier == "h":
        return str(tf_value * 60)
    elif tf_modifier == "m":
        return str(tf_value)
    else:
        return timeframe


def make_ohlcv_paths(provider: str, exchange: str, symbol: str, timeframe: str) -> Tuple[Path, Path]:
    base = (
        f"{provider}_{exchange.upper()}_"
        f"{symbol.upper().replace('/', ':').replace(':', '_')}_{_tf_key(timeframe)}"
    )
    return app_state.data_dir / f"{base}.ohlcv", app_state.data_dir / f"{base}.toml"


def runtime_output_dir(session_id: str) -> Path:
    """Per-session output directory holding plot.csv / script_hash.csv / runner.log."""
    return app_state.output_dir / "realtime" / session_id


def make_cache_path() -> Path:
    return app_state.data_dir / "cache" / "ohlcv_cache.sqlite"
