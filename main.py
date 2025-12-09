from typing import List, Dict, Any, Optional, Iterator

import ccxt.pro as ccxt

import asyncio
import os
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta

from appendable_iter import AppendableIterable
from pynecore.core.ohlcv_file import OHLCVWriter, OHLCVReader
from pynecore.core.script_runner import ScriptRunner
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV
from pynecore.cli.app import app_state


# -------------------------------------------------------------
# region Download OHLCV Data
# -------------------------------------------------------------
def get_ohlcv_data(provider: str, symbol: str, timeframe: str, since: str | None) -> bool:
    today = datetime.today()
    month_ago = 1 if timeframe == "1" else 2    # 1분봉의 경우 1달치 그외는 2달치 데이터 다운로드

    past = (today - relativedelta(months=month_ago)).strftime("%Y-%m-%d")
    if timeframe != "1" and since is not None:
        past = since

    from pynecore.cli.commands.data import download, AvailableProvidersEnum, parse_date_or_days
    time_from = parse_date_or_days(past)
    time_to = parse_date_or_days("")
    try:
        download(provider=AvailableProvidersEnum(provider), symbol=symbol, timeframe=timeframe, time_from=time_from,
             time_to=time_to, chunk_size=100, list_symbols=False, show_info=False)
    except Exception as e:
        print(f"Failed to download {timeframe} ohlcv data: {e}")
        return False
    return True


# -------------------------------------------------------------
# region Update OHLCV Data
# -------------------------------------------------------------
def update_ohlcv_data(filepath: str, candle_datas: list) -> int:
    """
    update the ohlcv file with [confirmed bar, new bar]
    """
    incremental_size: int = 0
    last_timestamp: int = 0
    last_open_price: float = 0.0

    with OHLCVReader(filepath) as reader:
        last_timestamp = reader.end_timestamp
        last_open_price = reader.read(reader.size - 1).open
        reader.close()

    with OHLCVWriter(filepath) as writer:
        for candle_data in candle_datas:
            timestamp = int(candle_data[0] / 1000)
            open_price = candle_data[1]
            # 파일에 기록되어 있는 마지막 캔들의 open 과 덮어쓰려고 하는 open 값이 다르면 파일에 기록되어 있는 open 을 사용
            if (timestamp == last_timestamp) and (open_price != last_open_price):
                open_price = last_open_price
            high_price = candle_data[2]
            low_price = candle_data[3]
            close_price = candle_data[4]
            vol = candle_data[5]
            original_size = writer.size

            writer.seek_to_timestamp(timestamp)
            writer.truncate()
            writer.write(
                OHLCV(timestamp=timestamp, open=open_price, high=high_price, low=low_price, close=close_price,
                      volume=vol))
            incremental_size = incremental_size + writer.size - original_size
            # print(f'[{datetime.now().strftime("%y-%m-%d %H:%M:%S")}] Candle updated: {candle_data}')
        writer.close()

    return incremental_size


# -------------------------------------------------------------
# region Ready Script Runner
# -------------------------------------------------------------
def ready_scrip_runner(script_path: Path, data_path: Path, data_toml_path: Path) -> tuple[ScriptRunner,
AppendableIterable[OHLCV]] | None:
    """
    마지막 확정봉에 script 를 돌리기전 pre-run script 를 준비하는 단계
    """
    # Get symbol info for the data
    syminfo = SymInfo.load_toml(data_toml_path)

    # Open data file
    with OHLCVReader(data_path) as reader:
        time_from = reader.start_datetime
        time_to = reader.end_datetime

        # Get the iterator
        gaps = sum(1 for ohlcv in reader if ohlcv.volume < 0)
        size = reader.get_size(int(time_from.timestamp()), int(time_to.timestamp()))
        if gaps > 0:
            size = size - gaps
        ohlcv_iter: Iterator[OHLCV] = reader.read_from(int(time_from.timestamp()), int(time_to.timestamp()))
        # 가변적인 iter 준비
        stream: AppendableIterable[OHLCV] = AppendableIterable(ohlcv_iter, feed_in_background=True)

        from pynecore.cli.app import app_state
        # Add lib directory to Python path for library imports
        lib_dir = app_state.scripts_dir / "lib"
        lib_path_added = False
        if lib_dir.exists() and lib_dir.is_dir():
            sys.path.insert(0, str(lib_dir))
            lib_path_added = True

            try:
                #################################### Module calculation ####################################
                # # bb1d / weekly high, low calculation
                # from modules.bb1d_calc import get_bb1d_lower
                # from modules.weekly_hl_calc import get_weekly_high_low
                # bb1d_lower = get_bb1d_lower(str(data_path), period=20, mult=2.0,
                #                             lookahead_on=True)
                # macro_high, macro_low = get_weekly_high_low(str(data_path), ago=2, session_offset_hours=9,
                #                                             lookahead_on=True)
                #################################### Module calculation ####################################

                # Create script runner (this is where the import happens)
                config_dir = app_state.config_dir
                with open(config_dir / "realtime_trade.toml", "rb") as f:
                    realtime_config = tomllib.load(f)
                    runner = ScriptRunner(script_path, stream, syminfo,
                                          last_bar_index=size - 1,
                                          plot_path=None, strat_path=None, trade_path=None,
                                          realtime_config=realtime_config,
                                          custom_inputs={
                                                # "bb1d_lower": bb1d_lower,
                                                # "macro_high": macro_high,
                                                # "macro_low": macro_low
                                          })
                    runner.init_step()
            finally:
                # Remove lib directory from Python path
                if lib_path_added:
                    sys.path.remove(str(lib_dir))

            # runner 및 가변적인 iter 반환
            return runner, stream


# ============================================================
# region Shared state
# ============================================================
class SharedState:
    def __init__(self) -> None:
        self.collected_trades: List[Dict[str, Any]] = []
        self.collected_bars: List[List[Any]] = []  # [ts_ms, o, h, l, c, v]
        self.lock = asyncio.Lock()
        self.last_fix_bar_ts: Optional[int] = None


# ============================================================
# region Generate collected_bars
# ============================================================
async def watch_trades_loop(
    exchange_name: str,
    symbol: str,
    timeframe: str,
    state: SharedState,
) -> None:
    exchange = getattr(ccxt, exchange_name)(config={})
    tf = timeframe
    tf_modifier = tf[-1]
    tf_multiplier = tf[0]
    if tf_modifier == "h":
        tf_multiplier = f"{int(tf_multiplier) * 60}"
    since = exchange.milliseconds() - int(tf_multiplier) * 60 * 1000

    while True:
        try:
            ws_trades = await exchange.watch_trades(symbol, since, None, {})
            async with state.lock:
                state.collected_trades.extend(ws_trades)
                generated_bars = exchange.build_ohlcvc(state.collected_trades, timeframe, since)
                collected_bars = state.collected_bars

                for bar in generated_bars:
                    bar_timestamp = bar[0]  # ms
                    last_ts = collected_bars[-1][0] if collected_bars else 0

                    if bar_timestamp == last_ts:
                        collected_bars[-1] = bar
                    elif bar_timestamp > last_ts:
                        collected_bars.append(bar)
                        state.collected_trades = exchange.filter_by_since_limit(
                            state.collected_trades, bar_timestamp
                        )
            # print(f"[WATCH TRADES LOOP] {datetime.now().strftime("%y-%m-%d %H:%M:%S")} {state.collected_bars}")

        except Exception as e:
            await exchange.close()
            exchange = getattr(ccxt, exchange_name)(config={})


# ============================================================
# region Timeframe parsing  # str 타임프레임을 ms 로 전환
# ============================================================
def parse_timeframe_to_ms(tf: str) -> int:
    unit = tf[-1]
    value = int(tf[:-1])

    if unit == "m":
        return value * 60 * 1000
    elif unit == "h":
        return value * 60 * 60 * 1000
    else:
        return value * 24 * 60 * 60 * 1000


# ============================================================
# region Fix collected_bars at the new candle time
# ============================================================
async def collected_bars_fix_loop(
    exchange_name: str,
    timeframe: str,
    state: SharedState,
    check_interval_sec: float = 0.1,
) -> None:
    """
    별도로 돌아가는 태스크.
    주기적으로 collected_bars 를 보고, 봉 갱신 시점에 "새 봉이 있어야 하는데 없다" 라고 판단되면
    fake new candle 생성하여 collected_bars 를 보정하는 역할.
    """
    timeframe_ms = parse_timeframe_to_ms(timeframe)
    grace_ms = 0.2 * 1000  # 유예시간 0.2초
    exchange = getattr(ccxt, exchange_name)(config={})

    while True:
        await asyncio.sleep(check_interval_sec)

        try:
            now_ms = await exchange.fetch_time()    # Get exchange time in milliseconds
        except Exception as e:
            await exchange.close()
            exchange = getattr(ccxt, exchange_name)(config={})
            now_ms = datetime.now().timestamp() * 1000
        missing_ts: int | None = None

        # lock 안에서 상태 확인하고, 누락된 봉의 timestamp(missing_ts) 결정
        async with state.lock:
            bars = state.collected_bars
            if len(bars) < 2:
                continue

            last_bar_open_ts = bars[-1][0]
            expected_next_bar_ts = last_bar_open_ts + timeframe_ms

            # expected_next_bar_ts 시각이 지났는데도 그 timestamp 봉이 없으면 누락으로 판단
            if now_ms >= expected_next_bar_ts + grace_ms:
                has_next_bar = any(b[0] == expected_next_bar_ts for b in bars)
                if not has_next_bar:
                    # 이미 이 timestamp 에 대해 한 번 TV fallback 을 했으면 다시 안 함
                    if state.last_fix_bar_ts != expected_next_bar_ts:
                        missing_ts = expected_next_bar_ts

            # 누락된 봉이 없으면 다음 턴으로
            if missing_ts is None:
                continue

            prev_close = bars[-1][4]

            # 이전 봉 close 값으로 시작봉 구성
            fake_new_candle = [missing_ts, prev_close, prev_close, prev_close, prev_close, 0.01]
            bars.append(fake_new_candle)
            state.last_fix_bar_ts = missing_ts
            # print(f"[COLLECTED_BARS FIX LOOP] Applied fake new candle. {fake_new_candle}")


# ============================================================
# region Script run loop
# ============================================================
async def script_run_loop(
    provider: str,
    exchange_name: str,
    symbol: str,
    timeframe: str,
    script_path: Path,
    state: SharedState
) -> None:
    """
    봉 데이터에 따라서 전략 스크립트를 실행하는 loop
    """
    tf = timeframe
    tf_modifier = tf[-1]
    tf_multiplier = tf[0]
    if tf_modifier == "h":
        tf_multiplier = f"{int(tf_multiplier) * 60}"

    # ohlcv 데이터 파일이 이미 있는 경우 파일의 첫번째 timestamp 값 얻어와서 추후 동일한 시작점에서부터 다운로드 할 수 있도록 함
    if provider == "" or exchange_name == "" or symbol == "" or tf == "":
        print("provider, exchange, symbol, timeframe is empty")
        sys.exit(1)
    data_file_name = f"{provider}_{exchange_name.upper()}_" \
                     f"{symbol.upper().replace("/", ":").replace(":", "_")}_" \
                     f"{tf_multiplier if tf_modifier == "m" or tf_modifier == "h" else tf}.ohlcv"
    toml_file_name = f"{provider}_{exchange_name.upper()}_" \
                     f"{symbol.upper().replace("/", ":").replace(":", "_")}_" \
                     f"{tf_multiplier if tf_modifier == "m" or tf_modifier == "h" else tf}.toml"
    ohlcv_file_path = app_state.data_dir / data_file_name
    toml_file_path = app_state.data_dir / toml_file_name
    start_timestamp_ohlcv_file = None
    if os.path.exists(ohlcv_file_path):
        with OHLCVReader(ohlcv_file_path) as reader:
            start_timestamp_ohlcv_file = reader.start_timestamp
            reader.close()

    # 모든 data 파일 삭제
    for file_path in [ohlcv_file_path, toml_file_path]:
        if file_path.exists():
            os.remove(file_path)

    # 분봉, 시간봉의 경우 캔들 생성후 timeframe 의 1/5 만큼 지난 시점에 캔들 데이터 무결성 검사. e.g., 5분봉의 경우 1분 지난시점, 1시간봉의 경우 12분 지난 시점
    # 그 외 일봉, 주봉 등은 1시간 지난 시점에 캔들 open 값무결성 검사
    # 분봉, 시간봉의 경우 1/2 만큼 지난 시점에 script pre-run. 그외는 12시간 지난 시점에 pre-run
    timeframe_ms = parse_timeframe_to_ms(tf)
    pre_run_script_time = timeframe_ms / 2
    fixed_candle_open_price: float = 0.0

    runner = None
    stream = None

    while True:
        await asyncio.sleep(0.1)  # 너무 자주 체크하면 비효율 → 0.1초면 충분

        async with state.lock:
            bars = state.collected_bars
            # collected_bars 의 첫번째 봉 데이터는 미완성 봉. 2번째 봉 데이터 부터는 완성된 봉. 2번째 봉 데이터 받기 시작할때 5분봉 OHLCV 데이터 받아오기
            # 3번째 봉 데이터 받기 시작할때 2번째 봉 데이터가 완성됨
            if len(bars) == 2 and not os.path.exists(ohlcv_file_path):
                with ThreadPoolExecutor() as executor:
                    data_timeframe = tf
                    if tf_modifier == "m":
                        data_timeframe = tf_multiplier
                    elif tf_modifier == "h":
                        data_timeframe = f"{int(tf_multiplier) * 60}"
                    # 기존 ohlcv 파일 있었다면 해당 파일의 첫번째 timestamp 부터 다운로드, 기존 파일 없었다면 2달치 데이터 다운로드
                    data_since = None if start_timestamp_ohlcv_file is None \
                        else datetime.fromtimestamp(start_timestamp_ohlcv_file).strftime("%Y-%m-%d")
                    future = executor.submit(get_ohlcv_data, provider=provider,
                                             symbol=f"{exchange_name}:{symbol}".upper(),
                                             timeframe=data_timeframe, since=data_since)
                    result = future.result()
                    if result:
                        print(f"Downloaded {exchange_name} {data_timeframe} OHLCV file successfully")
                    else:
                        print(f"Failed to download {data_timeframe} ohlcv data...retrying...")
                        # 모든 data 파일 삭제
                        for file_path in [ohlcv_file_path, toml_file_path]:
                            if file_path.exists():
                                os.remove(file_path)

            # Pre-run the script
            if len(bars) == 2 and datetime.now().timestamp() * 1000 >= bars[1][
                0] + pre_run_script_time and runner is None:
                # Check candle open price. 종종 직전 봉의 close 값이랑 현재봉의 open 값이 틀림
                # print(f"check candle open price at {bars[1][0]}")
                fixed_candle_open_price = 0.0
                need_fix = False
                open_price, high_price, low_price, close_price, vol, prev_close_price = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
                with OHLCVReader(ohlcv_file_path) as reader:
                    size = reader.size
                    open_price = reader.read(size - 1).open
                    high_price = reader.read(size - 1).high
                    low_price = reader.read(size - 1).low
                    close_price = reader.read(size - 1).close
                    vol = reader.read(size - 1).volume
                    prev_close_price = reader.read(size - 2).close
                    if open_price != prev_close_price:
                        # print(f"Open price is different from previous close price. Fixing...\n"
                        #       f"prev close: {prev_close_price}, open: {open_price}")
                        need_fix = True
                    reader.close()

                if need_fix:
                    with OHLCVWriter(ohlcv_file_path) as writer:
                        writer.overwrite(timestamp=writer.end_timestamp,
                                         candle=OHLCV(timestamp=writer.end_timestamp, open=prev_close_price,
                                                      high=high_price,
                                                      low=low_price, close=close_price, volume=vol))
                        fixed_candle_open_price = prev_close_price
                        # print("Candle open price fixing done")
                        writer.close()

                # Script runner 준비
                runner, stream = ready_scrip_runner(Path(script_path), Path(ohlcv_file_path),
                                                    Path(toml_file_path))
                size = runner.last_bar_index + 1
                # print("=== Pre-run 시작 (마지막 바 전까지) ===")
                for i in range(size - 1):
                    step_res = runner.step()
                    if step_res is None:
                        break
                # print("=== Pre-run 끝 ===")

            # 3번째 봉 데이터 받기 시작할때 2번째 봉 데이터가 완성되었으므로 해당 봉 데이터를 5분봉 OHLCV 데이터에 업데이트
            if len(bars) >= 3:
                # [confirmed bar, new bar] update to the ohlcv file
                bars = bars[1:]
                state.collected_bars = bars
                if fixed_candle_open_price > 0.0:
                    bars[0][1] = fixed_candle_open_price
                    # print(f"Update with Fixed candle open price: {fixed_candle_open_price}")
                incremented_size = update_ohlcv_data(ohlcv_file_path, bars)

                if incremented_size > 0:
                    #################################### Module calculation ####################################
                    # # bb1d / weekly high, low calculation
                    # from modules.bb1d_calc import get_bb1d_lower
                    # from modules.weekly_hl_calc import get_weekly_high_low
                    # bb1d_lower = get_bb1d_lower(ohlcv_file_path, period=20, mult=2.0, lookahead_on=True)
                    # macro_high, macro_low = get_weekly_high_low(ohlcv_file_path, ago=2, session_offset_hours=9,
                    #                                             lookahead_on=True)
                    #################################### Module calculation ####################################

                    # custom input update
                    runner.script.custom_inputs = {
                        # "bb1d_lower": bb1d_lower,
                        # "macro_high": macro_high,
                        # "macro_low": macro_low
                    }

                    # confirmed bar, new bar stream 에 업데이트
                    confirmed_bar_ohlcv = \
                        OHLCV(timestamp=int(bars[0][0] / 1000), open=bars[0][1],
                              high=bars[0][2],
                              low=bars[0][3], close=bars[0][4],
                              volume=bars[0][5],
                              extra_fields={})
                    new_bar_ohlcv = \
                        OHLCV(timestamp=int(bars[1][0] / 1000), open=bars[1][1],
                              high=bars[1][2],
                              low=bars[1][3], close=bars[1][4],
                              volume=bars[1][5],
                              extra_fields={})
                    stream.replace_last(confirmed_bar_ohlcv)
                    stream.append(new_bar_ohlcv)
                    stream.finish()

                    # 봉이 하나 추가되었으므로 last_bar_index 1 증가
                    runner.last_bar_index += incremented_size
                    runner.script.last_bar_index += incremented_size

                    # Calculate the last confirmed bar
                    while True:
                        step_res = runner.step()
                        if step_res is None:
                            break

                # destroy() 로 script_module 제거(필수). 제거하지 않으면 ScriptRunner 로 스크립트를 다시 로드해도 기존 봉 데이터가 사용됨
                runner.destroy()
                runner = None
                stream = None


# ============================================================
# region main
# ============================================================
async def main():
    # Load realtime config
    from pynecore.cli.app import app_state
    config_dir = app_state.config_dir
    realtime_config: dict = {}
    with open(config_dir / "realtime_trade.toml", "rb") as f:
        realtime_config = tomllib.load(f)
    realtime_section: dict = realtime_config.get("realtime", {})
    pyne_section: dict = realtime_config.get("pyne", {})
    if pyne_section.get("no_logo", False):
        # pyne 커맨드 실행시 logo 출력 안되게 환경변수 설정
        os.environ['PYNE_NO_LOGO'] = "True"
        os.environ['PYNE_QUIET'] = "True"

    # 스크립트 경로 설정
    script_name = realtime_section.get("script_name", "")
    if script_name == "":
        print("script_name is empty")
        sys.exit(1)
    script_path = app_state.scripts_dir / script_name

    # config 정보 불러오기
    provider = realtime_section.get("provider", "")
    exchange_name = realtime_section.get("exchange", "")
    symbol = realtime_section.get("symbol", "")
    timeframe = realtime_section.get("timeframe", "")

    state = SharedState()

    t1 = asyncio.create_task(watch_trades_loop(exchange_name, symbol, timeframe, state))
    t2 = asyncio.create_task(collected_bars_fix_loop(exchange_name, timeframe, state))
    t3 = asyncio.create_task(script_run_loop(provider, exchange_name, symbol, timeframe, script_path, state))

    await asyncio.gather(t1, t2, t3)


if __name__ == "__main__":
    asyncio.run(main())
