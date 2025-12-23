from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from pynecore.cli.app import app_state


@dataclass(frozen=True)
class DataServiceConfig:
    provider: str
    exchange: str
    symbol: str
    timeframe: str
    host: str = "127.0.0.1"
    port: int = 9001


def load_config() -> DataServiceConfig:
    with open(app_state.config_dir / "realtime_trade.toml", "rb") as f:
        cfg = tomllib.load(f)

    realtime = cfg.get("realtime", {})
    pyne = cfg.get("pyne", {})

    if pyne.get("no_logo", False):
        os.environ["PYNE_NO_LOGO"] = "True"
        os.environ["PYNE_QUIET"] = "True"

    provider = realtime.get("provider", "")
    exchange = realtime.get("exchange", "")
    symbol = realtime.get("symbol", "")
    timeframe = realtime.get("timeframe", "")

    if not provider or not exchange or not symbol or not timeframe:
        raise RuntimeError("Missing provider/exchange/symbol/timeframe in realtime_trade.toml")

    return DataServiceConfig(provider=provider, exchange=exchange, symbol=symbol, timeframe=timeframe)
