from __future__ import annotations

from datetime import datetime, UTC
import struct
import time
from typing import Callable, Optional
from pathlib import Path
from tempfile import TemporaryDirectory

from dateutil.relativedelta import relativedelta
from pynecore.core.exchange_policy import fetch_current_open_from_exchange
from pynecore.core.ohlcv_file import OHLCVReader, OHLCVWriter
from pynecore.types.ohlcv import OHLCV
from ohlcv_cache import import_from_ohlcv
from pynecore.cli.app import app_state
from log_utils import log_with_time


def convert_timeframe(timeframe: str, to_ms: bool = False) -> int | str:
    """
    timeframe을 분 단위 또는 밀리초로 변환

    Args:
        timeframe: 시간 단위 문자열 (예: "5m", "1h", "1d")
        to_ms: True면 밀리초로, False면 분 단위 문자열로 반환
    """
    unit = timeframe[-1]
    value = int(timeframe[:-1])

    # 먼저 분 단위로 변환
    if unit == "m":
        minutes = value
    elif unit == "h":
        minutes = value * 60
    else:  # "d"
        minutes = value * 24 * 60

    return minutes * 60 * 1000 if to_ms else str(minutes)


def download_history(provider: str, exchange: str, symbol: str, timeframe: str, since: Optional[str]) -> bool:
    # pynecore download uses timeframe as minutes in numeric format
    tf_modifier = timeframe[-1]
    tf_value = int(timeframe[:-1])

    if tf_modifier == "m":
        data_timeframe = str(tf_value)
    elif tf_modifier == "h":
        data_timeframe = str(tf_value * 60)
    else:
        data_timeframe = timeframe

    if since is None:
        today = datetime.today()
        month_ago = 1 if data_timeframe == "1" else 2
        since = (today - relativedelta(months=month_ago)).strftime("%Y-%m-%d")

    from pynecore.cli.commands.data import download, AvailableProvidersEnum, parse_date_or_days

    time_from = parse_date_or_days(since)
    time_to = parse_date_or_days("")

    try:
        download(
            provider=AvailableProvidersEnum(provider),
            symbol=f"{exchange}:{symbol}".upper(),
            timeframe=data_timeframe,
            time_from=time_from,
            time_to=time_to,
            chunk_size=None if exchange.lower() == "hyperliquid" else 100,
            list_symbols=False,
            show_info=False,
        )
        return True
    except Exception as e:
        print(f"[data_service] download failed: {e}")
        return False


def download_history_range_into_cache(
    *,
    cache_path: Path,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    time_from: datetime,
    time_to: datetime,
) -> bool:
    ok = False
    with TemporaryDirectory() as tmp_dir:
        ohlv_dir = Path(tmp_dir)
        try:
            ohlcv_path = download_history_range_to_file(
                provider=provider,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                time_from=time_from,
                time_to=time_to,
                ohlv_dir=ohlv_dir,
            )
            import_from_ohlcv(cache_path, provider, exchange, symbol, timeframe, ohlcv_path)
            ok = True
        except Exception as e:
            print(f"[data_service] download_range failed: {e}")
    return ok


def download_history_range_to_file(
    *,
    provider: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    time_from: datetime,
    time_to: datetime,
    ohlv_dir: Path,
    on_progress: Callable[[datetime], None] | None = None,
) -> Path:
    provider_module = __import__(f"pynecore.providers.{provider}", fromlist=[""])
    provider_class = getattr(
        provider_module,
        [p for p in dir(provider_module) if p.endswith("Provider")][0],
    )
    provider_instance = provider_class(
        symbol=f"{exchange}:{symbol}".upper(),
        timeframe=convert_timeframe(timeframe),
        ohlv_dir=ohlv_dir,
        config_dir=app_state.config_dir,
    )
    with provider_instance:
        provider_instance.download_ohlcv(
            time_from=time_from.replace(tzinfo=UTC),
            time_to=time_to.replace(tzinfo=UTC),
            on_progress=on_progress,
        )
    assert provider_instance.ohlcv_path is not None
    return provider_instance.ohlcv_path


def _ohlcv_float(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


def _preserve_latest_closed_bar(
    rest_bars: list,
    local_closed_bar: OHLCV | None,
) -> list:
    """Protect only the latest closed local candle from a stale REST value."""
    if not rest_bars or local_closed_bar is None:
        return rest_bars

    local_timestamp = int(local_closed_bar.timestamp)
    local_volume = _ohlcv_float(float(local_closed_bar.volume))
    merged: list = []
    for rest in rest_bars:
        preserve_local = (
            isinstance(rest, (list, tuple))
            and len(rest) >= 6
            and int(rest[0] / 1000) == local_timestamp
            and _ohlcv_float(float(rest[5])) < local_volume
        )
        if preserve_local:
            merged.append([
                local_timestamp * 1000,
                float(local_closed_bar.open),
                float(local_closed_bar.high),
                float(local_closed_bar.low),
                float(local_closed_bar.close),
                float(local_closed_bar.volume),
            ])
        else:
            merged.append(rest)

    return merged


def _filter_invalid_ccxt_markets(markets: list) -> list:
    """Drop ccxt-normalized markets that have no id/symbol.

    OKX sometimes lists preopen instruments with an empty instId, which ccxt's
    safe_string() normalizes to None. Such markets make keysort(markets_by_id)
    in set_markets() compare None with str and raise TypeError, so the whole
    load_markets() fails (breaking both REST OHLCV fetch and watch_trades).
    Unfixed upstream as of ccxt 4.5.57, so filter them out here.
    """
    return [
        market for market in markets
        if market and market.get("id") and market.get("symbol")
    ]


def _infer_market_type_from_symbol(symbol: str) -> str:
    value = (symbol or "").upper()
    if ":" not in value:
        return "spot"
    settle = value.rsplit(":", 1)[-1]
    if settle in {"USDT", "USDC"}:
        return "linear"
    return "inverse"


def _make_ccxt_config(exchange: str, market_type: str = "", symbol: str = "") -> dict:
    if exchange.lower() != "binance":
        return {}

    resolved_market_type = (market_type or "").strip().lower()
    if resolved_market_type not in {"spot", "linear", "inverse"}:
        resolved_market_type = _infer_market_type_from_symbol(symbol) if symbol else ""

    if resolved_market_type == "spot":
        return {"options": {"defaultType": "spot", "fetchMarkets": {"types": ["spot"]}}}
    if resolved_market_type == "linear":
        return {"options": {"defaultType": "future", "fetchMarkets": {"types": ["linear"]}}}
    if resolved_market_type == "inverse":
        return {"options": {"defaultType": "delivery", "fetchMarkets": {"types": ["inverse"]}}}

    return {"options": {"defaultType": "future"}}


def make_ccxt_client(ccxt_module, exchange: str, market_type: str = "", symbol: str = ""):
    """Create a sync ccxt client.

    For OKX, wrap the exchange class so fetch_markets() drops invalid markets
    right before load_markets() passes them to set_markets(). Other exchanges
    are created as-is.
    """
    exchange_class = getattr(ccxt_module, exchange)
    config = _make_ccxt_config(exchange, market_type, symbol)
    if exchange.lower() != "okx":
        return exchange_class(config=config)

    class SafeOKX(exchange_class):
        def fetch_markets(self, params={}):
            return _filter_invalid_ccxt_markets(super().fetch_markets(params))

    return SafeOKX(config=config)


def make_ccxt_pro_client(ccxt_module, exchange: str, market_type: str = "", symbol: str = ""):
    """ccxt.pro (async) counterpart of make_ccxt_client().

    Kept separate because fetch_markets() is a coroutine in ccxt.pro and needs
    an async override: a sync override would hand a plain list to ccxt's
    internal await, and using this helper on sync ccxt would return a
    coroutine to sync callers.
    """
    exchange_class = getattr(ccxt_module, exchange)
    config = _make_ccxt_config(exchange, market_type, symbol)
    if exchange.lower() != "okx":
        return exchange_class(config=config)

    class SafeOKXPro(exchange_class):
        async def fetch_markets(self, params={}):
            markets = await super().fetch_markets(params)
            return _filter_invalid_ccxt_markets(markets)

    return SafeOKXPro(config=config)


def fetch_ohlcv_data(
    exchange: str,
    symbol: str,
    timeframe: str,
    since: int,
    limit: int | None = None,
    market_type: str = "",
) -> list | None:
    import ccxt

    client = make_ccxt_client(ccxt, exchange, market_type=market_type, symbol=symbol)
    return client.fetch_ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        since=since,
        limit=limit,
    )


def fix_last_open_if_needed(
    ohlcv_path: str,
    exchange: str = "",
    symbol: str = "",
    timeframe: str = "",
    market_type: str = "",
) -> tuple[float, bool]:
    """Resolve and persist the forming candle's authoritative open.

    The returned target remains valid even when the file already had that open.
    A fake candle can be replaced by a later live candle after this function
    returns, so the caller must retain and reapply the target until run_ready.
    """
    retry_delays = (1.0, 2.0)
    max_attempts = len(retry_delays) + 1
    open_price, high_price, low_price, close_price, vol, prev_close_price = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    last_timestamp, interval = 0, 0
    with OHLCVReader(ohlcv_path) as reader:
        size = reader.size
        last = reader.read(size - 1)
        prev = reader.read(size - 2)
        interval = reader.interval
        last_timestamp = last.timestamp
        open_price = last.open
        high_price = last.high
        low_price = last.low
        close_price = last.close
        vol = last.volume
        prev_close_price = prev.close
        reader.close()

    if fetch_current_open_from_exchange(exchange):
        # OKX, Binance, HYPERLIQUID 의 경우 이전 봉 종가 != 현재 봉 시가 이므로 fetch 로 현재 봉 값을 가져와야 함.
        # fetch 에서 에러가 발생할 경우 현재 봉 시가 fix 가 안되므로 retry 필요함 (현재 봉 시가는 현재 봉이 confirmed 되면 계산에 쓰이므로 fix 되어야 함)
        target_open_price = None
        for attempt in range(1, max_attempts + 1):
            try:
                res = fetch_ohlcv_data(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    since=(last_timestamp - interval) * 1000,
                    limit=3,
                    market_type=market_type,
                )
            except Exception as e:
                if attempt >= max_attempts:
                    log_with_time(
                        f"[fix_last_open_if_needed] Error fetching current open: {e}; "
                        f"failed after {attempt}/{max_attempts}"
                    )
                    return 0.0, False
                delay = retry_delays[attempt - 1]
                log_with_time(
                    f"[fix_last_open_if_needed] Error fetching current open: {e}; "
                    f"retrying in {delay:g}s ({attempt}/{max_attempts})"
                )
                time.sleep(delay)
                continue

            for bar in res or []:
                if int(bar[0] / 1000) == last_timestamp:
                    # fetch 로 받은 bar open 데이터를 float32 타입으로 변경하여 저장
                    target_open_price = _ohlcv_float(bar[1])
                    break
            if target_open_price is not None:
                break

            if attempt >= max_attempts:
                return 0.0, False
            delay = retry_delays[attempt - 1]
            log_with_time(
                "[fix_last_open_if_needed] current open not found in fetched OHLCV; "
                f"retrying in {delay:g}s ({attempt}/{max_attempts})"
            )
            time.sleep(delay)

        if target_open_price is None:
            return 0.0, False
    else:
        # Bitget and Bybit use previous-close continuity. Keep the legacy fallback
        # for any provider not covered by the fetch-current-open policy.
        target_open_price = prev_close_price

    corrected_high_price = max(high_price, target_open_price)
    corrected_low_price = min(low_price, target_open_price)
    changed = (
        open_price != target_open_price
        or high_price != corrected_high_price
        or low_price != corrected_low_price
    )
    if changed:
        with OHLCVWriter(ohlcv_path) as writer:
            writer.overwrite(timestamp=writer.end_timestamp,
                             candle=OHLCV(timestamp=writer.end_timestamp, open=target_open_price,
                                          high=corrected_high_price,
                                          low=corrected_low_price, close=close_price, volume=vol))
            # print("Candle open price fixing done")
            writer.close()

    return target_open_price, changed


def update_ohlcv_data(ohlcv_path: str, candle_datas: list) -> int:
    """
    candle_datas: Expected format is [confirmed_bar, new_bar]
    """
    incremental_size = 0
    last_timestamp = 0
    last_open_price = 0.0

    with OHLCVReader(ohlcv_path) as reader:
        last_timestamp = reader.end_timestamp
        last_open_price = reader.read(reader.size - 1).open
        reader.close()

    with OHLCVWriter(ohlcv_path) as writer:
        for cd in candle_datas:
            ts_sec = int(cd[0] / 1000)
            open_price = cd[1]
            if (ts_sec == last_timestamp) and (open_price != last_open_price):
                open_price = last_open_price
            original_size = writer.size

            writer.seek_to_timestamp(ts_sec)
            writer.truncate()
            writer.write(
                OHLCV(
                    timestamp=ts_sec,
                    open=float(open_price),
                    high=float(cd[2]),
                    low=float(cd[3]),
                    close=float(cd[4]),
                    volume=float(cd[5]),
                )
            )
            incremental_size += writer.size - original_size
        writer.close()

    return incremental_size


def fetch_and_update_ohlcv_data(
    exchange: str,
    symbol: str,
    timeframe: str,
    ohlcv_path: str,
    market_type: str = "",
) -> list | None:
    """
    Fetch and update candles using fetch_ohlcv.
    Only used at the first pre_run after history download.

    :param exchange: Exchange name (e.g., "binance")
    :param symbol: Symbol (e.g., "BTC/USDT:USDT")
    :param timeframe: Timeframe (e.g., "1m", "5m")
    :param ohlcv_path: Path to OHLCV file
    :return: Updated open price of the last candle
    """
    # Read current last candle timestamp
    with OHLCVReader(ohlcv_path) as reader:
        size = reader.size
        last_candle = reader.read(size - 1)
        previous_candle = reader.read(size - 2) if size >= 2 else None
        last_timestamp_sec = last_candle.timestamp
        interval = reader.interval
        reader.close()

    # Fetch candles from exchange and update the ohlcv file
    try:
        res = fetch_ohlcv_data(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            since=last_timestamp_sec * 1000 - interval * 1000,  # Convert to milliseconds
            limit=None,
            market_type=market_type,
        )

        if not res or len(res) == 0:
            print(f"[fetch_and_update_ohlcv_data] No data received from exchange")
            return None

        merged = _preserve_latest_closed_bar(res, previous_candle)
        update_ohlcv_data(ohlcv_path, merged)
        return merged

    except Exception as e:
        log_with_time(f"[fetch_and_update_ohlcv_data] Error fetching OHLCV: {e}")
        return None


def fetch_and_update_recent_ohlcv_data(
    exchange: str,
    symbol: str,
    timeframe: str,
    ohlcv_path: str,
    current_bar_ts_ms: int,
    bar_count: int = 10,
    market_type: str = "",
) -> list | None:
    """
    Fetch and update recently closed candles before the current live candle.
    The current candle is preserved from the local OHLCV file and is not updated from REST.
    """
    current_ts_sec = int(current_bar_ts_ms / 1000)

    with OHLCVReader(ohlcv_path) as reader:
        interval = reader.interval
        last_bar = reader.read(reader.size - 1)
        previous_bar = reader.read(reader.size - 2) if reader.size >= 2 else None
        reader.close()

    if interval is None:
        return None

    if last_bar.timestamp != current_ts_sec:
        log_with_time(
            "[fetch_and_update_recent_closed_ohlcv_data] "
            f"current bar mismatch: file={last_bar.timestamp}, live={current_ts_sec}"
        )
        return None

    current_bar = [
        last_bar.timestamp * 1000,
        last_bar.open,
        last_bar.high,
        last_bar.low,
        last_bar.close,
        last_bar.volume,
    ]

    since_ms = (current_ts_sec - interval * bar_count) * 1000

    try:
        res = fetch_ohlcv_data(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            since=since_ms,
            limit=bar_count + 1,
            market_type=market_type,
        )

        if not res:
            return None

        closed_bars = [
            bar for bar in res
            if int(bar[0] / 1000) < current_ts_sec
        ][-bar_count:]

        if not closed_bars:
            return None

        merged_closed_bars = _preserve_latest_closed_bar(closed_bars, previous_bar)
        update_ohlcv_data(ohlcv_path, merged_closed_bars + [current_bar])
        # print(f"[fetch_and_update_recent_closed_ohlcv_data] Updated bars:\n{closed_bars}")
        return merged_closed_bars

    except Exception as e:
        log_with_time(f"[fetch_and_update_recent_closed_ohlcv_data] Error fetching OHLCV: {e}")
        return None
