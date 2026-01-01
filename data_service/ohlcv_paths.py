from __future__ import annotations

from pathlib import Path
from typing import Tuple

from config import DataServiceConfig
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


def make_plot_path(cfg: DataServiceConfig) -> Path:
    script_name = cfg.realtime_section.get("script_name", "")
    script_stem = Path(script_name).stem
    plot_path = app_state.output_dir / f"{script_stem}.csv"
    return plot_path