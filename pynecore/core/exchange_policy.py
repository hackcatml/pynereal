from __future__ import annotations


_ZERO_VOLUME_HIDDEN_EXCHANGES = {
    "OKX",
    "BINANCE",
    "BINANCEUSDM",
    "BINANCECOINM",
}

_FETCH_CURRENT_OPEN_EXCHANGES = _ZERO_VOLUME_HIDDEN_EXCHANGES | {
    "HYPERLIQUID",
}


def normalize_exchange_name(exchange: str | None) -> str:
    return (exchange or "").upper()


def tradingview_hides_zero_volume(exchange: str | None) -> bool:
    return normalize_exchange_name(exchange) in _ZERO_VOLUME_HIDDEN_EXCHANGES


def fetch_current_open_from_exchange(exchange: str | None) -> bool:
    return normalize_exchange_name(exchange) in _FETCH_CURRENT_OPEN_EXCHANGES
