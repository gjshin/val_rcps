"""Display-only rounding. Never round the values used by the valuation."""
import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler


def dataframe(data, **kwargs):
    if isinstance(data, Styler):
        return st.dataframe(data, **kwargs)
    frame = data.data if isinstance(data, Styler) else data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    config = {}
    for name in frame.columns:
        if pd.api.types.is_float_dtype(frame[name]):
            # 원화 총액처럼 큰 금액은 원 단위 정수, 그 밖(원금 100 기준·비율·확률)은 소수 넷째 자리 — 모두 천 단위 쉼표.
            big = frame[name].abs().max(skipna=True)
            config[name] = st.column_config.NumberColumn(format='%,.0f' if pd.notna(big) and big >= 1e6 else '%,.4f')
    config.update(kwargs.pop('column_config', {}) or {})
    return st.dataframe(data, column_config=config, **kwargs)
