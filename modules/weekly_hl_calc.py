import numpy as np
import pandas as pd

from modules.request_security import request_security, read_ohlcv_i32_f32_le


# ============================================================
# 5. 2주 전 주봉 high / low
# ============================================================

def get_weekly_high_low(
    data_path: str,
    *,
    ago: int,
    session_offset_hours: int = 9,
    lookahead_on: bool = False
) -> tuple[list, list]:
    """
    5분봉 OHLCV DataFrame 기준으로
    마지막 5분봉 시점에 보이는 '2주 전 주봉' 의 high / low 값을 반환한다.

    개념적으로:
        request.security(timeframe="1W", high[2]) 와 동일한 효과를 노린다.
    """
    df_5m: pd.DataFrame = read_ohlcv_i32_f32_le(data_path)
    # 정렬 및 중복 제거
    df_5m = df_5m[~df_5m.index.duplicated(keep="last")].sort_index()

    # 주의:
    #   lookahead_off 가 이미 1바 뒤로 밀어주므로,
    #   여기서 htf_func 에서 1칸만 shift 하면 결과적으로 2주 전이 된다.
    def weekly_high_func(df_w: pd.DataFrame) -> pd.Series:
        shift_value = ago
        return df_w["high"].shift(shift_value)

    def weekly_low_func(df_w: pd.DataFrame) -> pd.Series:
        shift_value = ago
        return df_w["low"].shift(shift_value)

    weekly_high = request_security(
        df_5m,
        timeframe="1W",
        htf_func=weekly_high_func,
        lookahead_on=lookahead_on,
        session_offset_hours=session_offset_hours,
    )

    weekly_low = request_security(
        df_5m,
        timeframe="1W",
        htf_func=weekly_low_func,
        lookahead_on=lookahead_on,
        session_offset_hours=session_offset_hours,
    )

    # 5) NaN -> 0.0 변환 후 list[float] 로 변환
    list_weekly_high = np.nan_to_num(weekly_high.values, nan=0.0).astype(float).tolist()
    list_weekly_low = np.nan_to_num(weekly_low.values, nan=0.0).astype(float).tolist()

    return list_weekly_high, list_weekly_low
