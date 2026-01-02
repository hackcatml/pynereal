from __future__ import annotations

from datetime import datetime
from typing import Optional

from dateutil.relativedelta import relativedelta
from pynecore.core.ohlcv_file import OHLCVReader, OHLCVWriter
from pynecore.types.ohlcv import OHLCV


def parse_timeframe_to_ms(tf: str) -> int:
    unit = tf[-1]
    value = int(tf[:-1])
    if unit == "m":
        return value * 60 * 1000
    if unit == "h":
        return value * 60 * 60 * 1000
    return value * 24 * 60 * 60 * 1000


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
) -> None:
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

    except Exception as e:
        print(f"[fetch_and_update_ohlcv_data] Error fetching OHLCV: {e}")
        return None