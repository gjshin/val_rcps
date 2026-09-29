"""Lossless, explicit transfer between detailed tools and the current case."""
from dataclasses import asdict
from .case import Case, FIELDS, section_for, inspect_case


def legacy_term_values(payload):
    """Reject versioned cases before legacy filtering can erase their inputs."""
    from .legacy import Terms
    if not isinstance(payload, dict):
        raise ValueError('계약 입력이 담긴 JSON 객체가 필요합니다.')
    if 'schema' in payload or any(k in payload for k in ('contract', 'market', 'method', 'cases')):
        raise ValueError('새 평가·여러 회차 파일은 평가 작업 또는 여러 회차·변동 분석에서 여십시오. '
                         '상세 계산은 평가·분석에서 현재 결과를 공유합니다. 명령행에서는 tools/run_case.py 또는 tools/run_engagement.py를 사용하십시오.')
    values = {k: v for k, v in payload.items() if k in Terms.__dataclass_fields__}
    if not values:
        raise ValueError('기존 시나리오의 계약 입력을 찾지 못했습니다.')
    return values


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
            assumptions[key]['rationale'] += '\n평가 입력 도구에서 적용값 수정 — 기존 판단근거 재확인 필요.'
        else:
            getattr(result, section_for(key))[key] = value
    result.imported_defaults = [k for k in result.imported_defaults if k not in changes]
    issues = [i for i in inspect_case(result) if i.severity == 'error'] if validate else []
    if issues:
        raise ValueError(' / '.join(i.message for i in issues))
    return result


def apply_volatility(case, pack):
    import copy
    from .market_data import validate_pack
    if case.effective().get('d_base') and pack.get('opt', {}).get('asof') != case.effective().get('d_base'):
        raise ValueError('변동성 산출 기준일과 평가기준일이 다릅니다. 기준일을 맞춘 후 적용하십시오.')
    validate_pack(pack, asof=case.effective().get('d_base'))
    # Other required fields may still be blank during input preparation.
    candidate = apply_changes(case, {'sig': pack['sigma']}, validate=False)
    candidate.market_evidence['sig'] = copy.deepcopy(pack)
    candidate.sources['sig'] = f"변동성 도구 · {pack.get('source', '')} · {pack['opt']} · 종목별 {pack.get('per_company', [])}"
    return candidate
