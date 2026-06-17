from __future__ import annotations

import asyncio
import json
import re
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote


LOGO_BASE_URL = "https://s3-symbol-logo.tradingview.com/"
CRYPTO_SCAN_URL = "https://scanner.tradingview.com/crypto/scan"

_SCAN_COLUMNS = [
    "name",
    "description",
    "exchange",
    "base_currency",
    "currency",
    "base_currency_logoid",
    "currency_logoid",
    "logoid",
    "source_logoid",
]

_EXCHANGE_ALIASES = {
    "binanceusdm": "BINANCE",
    "binancecoinm": "BINANCE",
    "binance": "BINANCE",
    "bitget": "BITGET",
    "bybit": "BYBIT",
    "coinbase": "COINBASE",
    "coinbaseexchange": "COINBASE",
    "gateio": "GATEIO",
    "okx": "OKX",
}

_PROVIDER_LOGO_ALIASES = {
    "binanceusdm": "binance",
    "binancecoinm": "binance",
    "coinbaseexchange": "coinbase",
}


def _exchange_key(exchange: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (exchange or "").lower())


def tradingview_exchange_code(exchange: str) -> str:
    key = _exchange_key(exchange)
    return _EXCHANGE_ALIASES.get(key, key.upper())


def _provider_logo_slug(exchange: str) -> str:
    key = _exchange_key(exchange)
    return _PROVIDER_LOGO_ALIASES.get(key, key)


def tradingview_logo_url(logoid: str | None) -> str:
    if not logoid:
        return ""
    logoid = str(logoid).strip()
    if not logoid:
        return ""
    if logoid.startswith(("http://", "https://")):
        return logoid
    path = logoid if logoid.endswith(".svg") else f"{logoid}.svg"
    return LOGO_BASE_URL + quote(path, safe="/-_.")


def exchange_logo_url(exchange: str) -> str:
    slug = _provider_logo_slug(exchange)
    if not slug:
        return ""
    return tradingview_logo_url(f"provider/{slug}")


def _clean_symbol_part(part: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (part or "").upper())


def tradingview_perp_ticker(exchange: str, symbol: str) -> str:
    tv_exchange = tradingview_exchange_code(exchange)
    market = (symbol or "").strip().upper()
    if not tv_exchange or not market:
        return ""

    pair = market.split(":", 1)[0]
    if "/" in pair:
        base, quote_symbol = pair.split("/", 1)
        ticker = f"{_clean_symbol_part(base)}{_clean_symbol_part(quote_symbol)}"
    else:
        ticker = _clean_symbol_part(pair.removesuffix(".P"))

    if not ticker:
        return ""
    return f"{tv_exchange}:{ticker}.P"


def static_logo_info(exchange: str, symbol: str) -> dict[str, str]:
    return {
        "tv_symbol": tradingview_perp_ticker(exchange, symbol),
        "symbol_logo_url": "",
        "quote_logo_url": "",
        "exchange_logo_url": exchange_logo_url(exchange),
    }


def _value(row: list[Any], index: int) -> Any:
    return row[index] if len(row) > index else None


def _verified_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _is_cert_verify_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return True
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


def _open_scan_request(req: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=5, context=_verified_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        if not _is_cert_verify_error(e):
            raise
        with urllib.request.urlopen(
            req, timeout=5, context=ssl._create_unverified_context()
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _scan_symbol(tv_symbol: str) -> dict[str, Any]:
    payload = {
        "symbols": {"tickers": [tv_symbol], "query": {"types": []}},
        "columns": _SCAN_COLUMNS,
        "range": [0, 1],
    }
    req = urllib.request.Request(
        CRYPTO_SCAN_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/cex-screener/",
            "User-Agent": "PyneReal/1.0",
        },
        method="POST",
    )
    return _open_scan_request(req)


class TradingViewLogoResolver:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], dict[str, str]] = {}
        self._lock = asyncio.Lock()

    async def resolve(self, exchange: str, symbol: str) -> dict[str, str]:
        key = (tradingview_exchange_code(exchange), (symbol or "").upper())
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return dict(cached)

        info = static_logo_info(exchange, symbol)
        tv_symbol = info.get("tv_symbol") or ""
        if not tv_symbol:
            return info

        data = await asyncio.to_thread(_scan_symbol, tv_symbol)
        row = (data.get("data") or [{}])[0]
        values = row.get("d") or []

        if values:
            base_logoid = _value(values, 5)
            quote_logoid = _value(values, 6)
            instrument_logoid = _value(values, 7)
            source_logoid = _value(values, 8)
            primary_logoid = base_logoid or instrument_logoid or source_logoid or quote_logoid

            info.update({
                "tv_symbol": row.get("s") or tv_symbol,
                "symbol_logo_url": tradingview_logo_url(primary_logoid),
                "quote_logo_url": tradingview_logo_url(quote_logoid),
                "symbol_logo_id": str(primary_logoid or ""),
                "quote_logo_id": str(quote_logoid or ""),
            })

        async with self._lock:
            self._cache[key] = dict(info)
        return info
