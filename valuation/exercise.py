"""Explicit contractual exercise styles, independent of the chosen grid."""
import copy
import datetime as dt

FREQUENCIES = {'p_f', 'k_f', 'sha_put_f', 'sha_call_f', 'rfx_cyc'}


def validate_styles(styles):
    if not isinstance(styles, dict) or set(styles) - (FREQUENCIES | {'cv'}):
        raise ValueError('행사방식의 항목을 확인하십시오.')
    for key, value in styles.items():
        allowed = {'any', 'single'} if key == 'cv' else {'any', 'periodic'}
        if not isinstance(value, str) or value not in allowed:
            raise ValueError('지원하지 않는 행사방식입니다.')


def node_interval_months(d_base, d_mat, gap_m, grid_days=0.0):
    """실제 격자 한 칸의 길이(개월) — derive() 와 같은 구간 수에서 센다(최소 4구간).

    일수 격자(grid_days > 0 — 주 7 · 2주 14)면 구간 수가 일수로 정해진다. 월 간격(gap_m)만
    보면 주 격자에서 «기간 중 언제든지» 가 한 달에 한 번만 열린다. 날짜가 비어 있으면 None.
    """
    years = (dt.date.fromisoformat(d_mat) - dt.date.fromisoformat(d_base)).days / 365
    if years <= 0:
        return None
    gap = max(.25, float(gap_m))
    days = float(grid_days or 0.0)
    n = max(4, round(years * 365 / days)) if days > 0 else max(4, round(years * 12 / gap))
    return years * 12 / n


def apply_styles(values, styles):
    validate_styles(styles)
    result = copy.deepcopy(values)
    # Match derive(), including its minimum of four intervals. A nominal gap
    # alone is insufficient for short remaining lives with the minimum grid —
    # and for day-based grids (grid_days) the interval comes from the days.
    if any(v == 'any' and k in FREQUENCIES for k, v in styles.items()):
        try:
            gap = max(.25, float(values['gap_m']))
            interval = node_interval_months(values['d_base'], values['d_mat'], gap,
                                            values.get('grid_days', 0.0))
            if interval:
                days = float(values.get('grid_days') or 0.0)
                for key in FREQUENCIES:
                    if styles.get(key) == 'any':
                        # Legacy rfx_any() uses the nominal gap for labels on month grids.
                        result[key] = (min(interval, gap) if (key == 'rfx_cyc' and days <= 0)
                                       else interval)
        except (KeyError, TypeError, ValueError):
            pass  # Incomplete drafts remain editable; inspect_case blocks pricing.
    if styles.get('cv') == 'single' and 'cv_s' in result:
        result['cv_e'] = result['cv_s']
    return result
