
# 100% AI Generated request.security mocking

from typing import Callable

import numpy as np
import pandas as pd

# ============================================================
# 1. OHLCV 바이너리 입출력
#    포맷: little-endian int32 timestamp(sec) + float32×5 (open..volume)
# ============================================================

def read_ohlcv_i32_f32_le(
    path: str,
    timezone: str = "Asia/Seoul",
    unit: str = "s",
) -> pd.DataFrame:
    """
    리틀엔디언 int32 + float32×5 OHLCV 바이너리 파일을 읽어서 DataFrame으로 반환한다.

    각 레코드:
        int32  timestamp (epoch, unit 초)
        float32 open, high, low, close, volume
    """
    dtype = np.dtype([
        ("ts",   "<i4"),
        ("open", "<f4"),
        ("high", "<f4"),
        ("low",  "<f4"),
        ("close","<f4"),
        ("volume","<f4"),
    ])

    arr = np.memmap(path, dtype=dtype, mode="r")
    if arr.size == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # 타임스탬프를 tz-aware DateTimeIndex로 변환
    idx = pd.to_datetime(
        arr["ts"].astype(np.int64),
        unit=unit,
        utc=True
    ).tz_convert(timezone)

    df = pd.DataFrame(
        {
            "open":   arr["open"].astype(np.float64),
            "high":   arr["high"].astype(np.float64),
            "low":    arr["low"].astype(np.float64),
            "close":  arr["close"].astype(np.float64),
            "volume": arr["volume"].astype(np.float64),
        },
        index=idx,
    )
    df.index.name = "time"
    return df


# ============================================================
# 2. 타임프레임 유틸과 리샘플
# ============================================================

def timeframe_to_rule(tf: str) -> str:
    """
    PineScript 스타일의 timeframe 문자열을 pandas resample rule 로 변환한다.

    예:
        "1D"  -> "1D"
        "1W"  -> "W-MON"   (월요일 시작 주봉)
        "60"  -> "60T"     (60분)
        "5"   -> "5T"      (5분)
        "1H"  -> "1H"
    """
    tf = str(tf).strip().upper()

    if tf.endswith("D"):
        n = int(tf[:-1] or 1)
        return f"{n}D"

    if tf.endswith("W"):
        n = int(tf[:-1] or 1)
        return "W-MON" if n == 1 else f"{n}W-MON"

    if tf.endswith("H"):
        n = int(tf[:-1] or 1)
        return f"{n}H"

    # 나머지는 모두 분 단위로 처리 ("5" -> 5분)
    n = int(tf)
    return f"{n}min"


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    표준 OHLCV 방식으로 리샘플한다.
    open  -> first
    high  -> max
    low   -> min
    close -> last
    volume-> sum
    """
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    cols = [c for c in df.columns if c in agg]
    return df[cols].resample(rule, label="left", closed="left").agg(agg)


# ============================================================
# 3. request_security 에뮬레이터
# ============================================================

def request_security(
    df_ltf: pd.DataFrame,
    timeframe: str,
    htf_func: Callable[[pd.DataFrame], pd.Series],
    *,
    lookahead_on: bool = False,
    session_offset_hours: int = 9,
) -> pd.Series:
    """
    PineScript 의 request.security 를 파이썬/pandas 로 흉내낸 함수.

    인자:
        df_ltf:
            저타임프레임(예: 5분봉) OHLCV DataFrame.
            tz-aware DateTimeIndex 이어야 한다.

        timeframe:
            "1D", "1W", "60" (분), "5" 등.

        htf_func:
            고타임프레임 DataFrame(예: 일봉, 주봉)을 받아
            Series 를 반환하는 함수.
            예: lambda df_d: df_d["close"]    (일봉 종가)
                lambda df_w: df_w["high"].shift(2)  (2주 전 주봉 고점)

        lookahead_on:
            False:
                HTF 바가 확정된 이후에야 값이 보인다. (Pine 기본)
            True:
                현재 진행 중인 HTF 바의 값도 실시간으로 LTF 에 보인다.

        session_offset_hours:
            일봉/주봉 경계를 언제로 맞출지 결정하는 오프셋.
            한국 시간 09:00 기준이면 9로 설정.

    반환:
        LTF 인덱스에 맞춰진 Series.
    """
    if df_ltf.empty:
        return pd.Series(index=df_ltf.index, dtype="float64")

    if not isinstance(df_ltf.index, pd.DatetimeIndex):
        raise ValueError("df_ltf must have a DateTimeIndex")

    # 1. 타임프레임 rule 계산
    rule = timeframe_to_rule(timeframe)

    # 2. 세션 오프셋 적용 (예: 09:00 기준으로 일/주봉 시작)
    shift = pd.Timedelta(hours=session_offset_hours)
    df_shifted = df_ltf.copy()
    df_shifted.index = df_shifted.index - shift

    # 3. 고타임프레임 OHLCV 생성
    df_htf = resample_ohlcv(df_shifted, rule)

    # 4. 사용자 정의 계산 수행 (예: 일봉 BB, 주봉 high, low 등)
    htf_series = htf_func(df_htf)
    if not isinstance(htf_series, pd.Series):
        raise ValueError("htf_func must return a pandas Series")

    # 5. lookahead_off 이면 "확정된 바"만 보이게 한 칸 시프트
    if not lookahead_on:
        htf_series = htf_series.shift(1)

    # 6. merge_asof 로 LTF 타임스탬프에 가장 가까운 과거 HTF 값 매핑
    #    (주봉 W-MON 같은 anchored freq 도 안전하게 처리)
    # LTF 쪽 준비
    ltf_frame = pd.DataFrame(index=df_shifted.index).sort_index()
    ltf_reset = ltf_frame.reset_index()
    ltf_time_col = ltf_reset.columns[0]

    # HTF 쪽 준비
    htf_clean = htf_series.dropna().sort_index()
    if htf_clean.empty:
        # 계산 가능한 HTF 값이 하나도 없으면 전부 NaN
        out = pd.Series(index=df_ltf.index, dtype="float64")
        return out

    htf_reset = htf_clean.to_frame("val").reset_index()
    htf_time_col = htf_reset.columns[0]

    # 시간 기준 정렬
    ltf_reset = ltf_reset.sort_values(ltf_time_col)
    htf_reset = htf_reset.sort_values(htf_time_col)

    merged = pd.merge_asof(
        ltf_reset,
        htf_reset,
        left_on=ltf_time_col,
        right_on=htf_time_col,
        direction="backward",
    )

    out = pd.Series(merged["val"].values, index=ltf_frame.index)

    # 7. 인덱스를 원래 LTF 인덱스로 복원
    out.index = df_ltf.index
    return out
