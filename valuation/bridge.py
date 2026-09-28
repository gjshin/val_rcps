"""Lossless, explicit transfer between detailed tools and the current case."""
from dataclasses import asdict
from .case import Case, FIELDS, section_for, inspect_case


def changes_from_terms(case, terms, baseline):
    values = asdict(terms)
    return {k: values[k] for k in FIELDS if values[k] != baseline.get(k)}


def apply_changes(case, changes, *, validate=True):
    result = Case.from_dict(case.to_dict())
    assumptions = {r['field']: r for r in result.assumptions}
    for key, value in changes.items():
        if key not in FIELDS:
            raise ValueError('계산된 내부값은 계약 입력으로 반영할 수 없습니다.')
        if key in assumptions:
            assumptions[key]['value'] = value
            assumptions[key]['rationale'] += '\n상세 기능에서 적용값 수정 — 기존 판단근거 재확인 필요.'
        else:
            getattr(result, section_for(key))[key] = value
    result.imported_defaults = [k for k in result.imported_defaults if k not in changes]
    issues = [i for i in inspect_case(result) if i.severity == 'error'] if validate else []
    if issues:
        raise ValueError(' / '.join(i.message for i in issues))
    return result


def apply_volatility(case, pack):
    import math
    if pack.get('kind') != 'vol_pack' or not isinstance(pack.get('sigma'), (int, float)) or not math.isfinite(pack['sigma']) or pack['sigma'] <= 0:
        raise ValueError('유효한 변동성 산출값이 필요합니다.')
    if pack.get('opt', {}).get('asof') != case.effective().get('d_base'):
        raise ValueError('변동성 산출 기준일과 평가기준일이 다릅니다. 기준일을 맞춘 후 적용하십시오.')
    candidate = apply_changes(case, {'sig': pack['sigma']})
    candidate.sources['sig'] = f"변동성 도구 · {pack.get('source', '')} · {pack['opt']} · 종목별 {pack.get('per_company', [])}"
    return candidate
