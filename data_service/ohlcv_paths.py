from __future__ import annotations

from pathlib import Path
from typing import Tuple

from pynecore.cli.app import app_state


def make_ohlcv_paths(provider: str, exchange: str, symbol: str, timeframe: str) -> Tuple[Path, Path]:
    tf_modifier = timeframe[-1]
    tf_value = int(timeframe[:-1])

    if tf_modifier == "h":
        tf_key = str(tf_value * 60)
    elif tf_modifier == "m":
        tf_key = str(tf_value)
    else:
        tf_key = timeframe

    base = f"{provider}_{exchange.upper()}_{symbol.upper().replace('/', ':').replace(':', '_')}_{tf_key}"
    return app_state.data_dir / f"{base}.ohlcv", app_state.data_dir / f"{base}.toml"
