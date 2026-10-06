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
            config[name] = st.column_config.NumberColumn(format='%.2f')
    config.update(kwargs.pop('column_config', {}) or {})
    return st.dataframe(data, column_config=config, **kwargs)
