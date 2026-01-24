from __future__ import annotations

from datetime import datetime, UTC
from typing import Optional
from pathlib import Path
from tempfile import TemporaryDirectory

from dateutil.relativedelta import relativedelta
from pynecore.core.ohlcv_file import OHLCVReader, OHLCVWriter
from pynecore.types.ohlcv import OHLCV
from ohlcv_cache import import_from_ohlcv
from pynecore.cli.app import app_state


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
            chunk_size=100,
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
                    on_progress=None,
                )
            assert provider_instance.ohlcv_path is not None
            import_from_ohlcv(cache_path, provider, exchange, symbol, timeframe, provider_instance.ohlcv_path)
            ok = True
        except Exception as e:
            print(f"[data_service] download_range failed: {e}")
    return ok


def fix_last_open_if_needed(ohlcv_path: str) -> float:
    fixed_candle_open_price = 0.0
    need_fix = False
    open_price, high_price, low_price, close_price, vol, prev_close_price = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    with OHLCVReader(ohlcv_path) as reader:
        size = reader.size
        last = reader.read(size - 1)
        prev = reader.read(size - 2)
        open_price = last.open
        high_price = last.high
        low_price = last.low
        close_price = last.close
        vol = last.volume
        prev_close_price = prev.close
        if open_price != prev_close_price:
            # print(f"Open price is different from previous close price. Fixing...\n"
            #       f"prev close: {prev_close_price}, open: {open_price}")
            need_fix = True
        reader.close()

    if need_fix:
        with OHLCVWriter(ohlcv_path) as writer:
            writer.overwrite(timestamp=writer.end_timestamp,
                             candle=OHLCV(timestamp=writer.end_timestamp, open=prev_close_price,
                                          high=high_price,
                                          low=low_price, close=close_price, volume=vol))
            fixed_candle_open_price = prev_close_price
            # print("Candle open price fixing done")
            writer.close()

    return fixed_candle_open_price


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
    import ccxt
    # Create ccxt client
    client = getattr(ccxt, exchange)(config={})

    # Read current last candle timestamp
    with OHLCVReader(ohlcv_path) as reader:
        size = reader.size
        last_candle = reader.read(size - 1)
        last_timestamp_sec = last_candle.timestamp
        interval = reader.interval
        reader.close()

    # Fetch candles from exchange and update the ohlcv file
    try:
        res = client.fetch_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            since=last_timestamp_sec * 1000 - interval * 1000,  # Convert to milliseconds
            limit=None
        )

        if not res or len(res) == 0:
            print(f"[fetch_and_update_ohlcv_data] No data received from exchange")
            return None

        update_ohlcv_data(ohlcv_path, res)
        return res

    except Exception as e:
        print(f"[fetch_and_update_ohlcv_data] Error fetching OHLCV: {e}")
        return None