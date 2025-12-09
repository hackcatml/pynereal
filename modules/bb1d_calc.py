from typing import Tuple

import numpy as np
import pandas as pd

from modules.request_security import request_security, read_ohlcv_i32_f32_le


# ============================================================
# 4. Bollinger Band 헬퍼
# ============================================================

def bb_series(
    source: pd.Series,
    period: int,
    mult: float | int,
    *,
    biased: bool = True,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    주어진 Series 에 대해 Bollinger Bands 를 계산한다.
    사용자가 준 bb(source, period, mult) 구현과 의미를 맞춘다.

    반환: (middle, upper, lower)
    """
    if period <= 0:
        raise AssertionError("Invalid period, period must be greater than 0!")
    if mult <= 0:
        raise AssertionError("Invalid multiplier, multiplier must be greater than 0!")

    roll = source.rolling(window=period)
    middle = roll.mean()
    ddof = 0 if biased else 1
    std = roll.std(ddof=ddof)

    upper = middle + mult * std
    lower = middle - mult * std
    return middle, upper, lower


def get_bb1d_lower(
    data_path: str,
    period: int = 20,
    mult: float = 2.0,
    *,
    session_offset_hours: int = 9,
    biased: bool = True,
    lookahead_on: bool = False
) -> float | list:
    """
    5분봉 OHLCV DataFrame 기준으로
    '마지막 5분봉 시점에 보이는 일봉 BB 하단값' 을 반환한다.

    Pine 기준:
        request.security(timeframe="1D", lookahead=barmerge.lookahead_off)
        로 계산한 BB.lower 를 5분봉에 붙인 다음, 마지막 값을 읽는 것과 같다.
    """
    df_5m: pd.DataFrame = read_ohlcv_i32_f32_le(data_path)
    # 정렬 및 중복 제거
    df_5m = df_5m[~df_5m.index.duplicated(keep="last")].sort_index()

    def bb1d_lower_func(df_1d: pd.DataFrame) -> pd.Series:
        _, _, lower = bb_series(df_1d["close"], period, mult, biased=biased)
        return lower

    bb1d_lower = request_security(
        df_5m,
        timeframe="1D",
        htf_func=bb1d_lower_func,
        lookahead_on=lookahead_on,
        session_offset_hours=session_offset_hours,
    )

    if bb1d_lower.dropna().empty:
        return float("nan")

    # 5) list[float] 로 변환
    list_bb1d_lower = np.nan_to_num(bb1d_lower.values, nan=0.0).astype(float).tolist()

    return list_bb1d_lower
