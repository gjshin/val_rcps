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


def apply_styles(values, styles):
    validate_styles(styles)
    result = copy.deepcopy(values)
    # Match derive(), including its minimum of four intervals. A nominal gap
    # alone is insufficient for short remaining lives with the minimum grid.
    if any(v == 'any' and k in FREQUENCIES for k, v in styles.items()):
        try:
            years = (dt.date.fromisoformat(values['d_mat']) - dt.date.fromisoformat(values['d_base'])).days / 365
            gap = max(.25, float(values['gap_m']))
            if years > 0:
                interval = years * 12 / max(4, round(years * 12 / gap))
                for key in FREQUENCIES:
                    if styles.get(key) == 'any':
                        # Legacy rfx_any() uses the nominal gap for labels.
                        result[key] = min(interval, gap) if key == 'rfx_cyc' else interval
        except (KeyError, TypeError, ValueError):
            pass  # Incomplete drafts remain editable; inspect_case blocks pricing.
    if styles.get('cv') == 'single' and 'cv_s' in result:
        result['cv_e'] = result['cv_s']
    return result
