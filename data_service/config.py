from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from pynecore.cli.app import app_state


@dataclass(frozen=True)
class DataServiceConfig:
    pyne_section: dict
    realtime_section: dict
    webhook_section: dict

    provider: str
    exchange: str
    symbol: str
    timeframe: str
    host: str
    port: int


def load_config() -> DataServiceConfig:
    with open(app_state.config_dir / "realtime_trade.toml", "rb") as f:
        cfg = tomllib.load(f)

    realtime = cfg.get("realtime", {})
    pyne = cfg.get("pyne", {})
    webhook = cfg.get("webhook", {})

    if pyne.get("no_logo", False):
        os.environ["PYNE_NO_LOGO"] = "True"
        os.environ["PYNE_QUIET"] = "True"

    provider = realtime.get("provider", "")
    exchange = realtime.get("exchange", "")
    symbol = realtime.get("symbol", "")
    timeframe = realtime.get("timeframe", "")
    data_service_addr = realtime.get("data_service_addr", "")
    data_service_host = data_service_addr.split(":")[0] if data_service_addr else "0.0.0.0"
    data_service_port = int(data_service_addr.split(":")[1]) if data_service_addr else 9001

    if not provider or not exchange or not symbol or not timeframe:
        raise RuntimeError("Missing provider/exchange/symbol/timeframe in realtime_trade.toml")

    return DataServiceConfig(
        pyne_section=pyne, realtime_section=realtime, webhook_section=webhook,
        provider=provider, exchange=exchange, symbol=symbol, timeframe=timeframe,
        host=data_service_host, port=data_service_port
    )
