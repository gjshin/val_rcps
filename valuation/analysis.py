"""Explicit, bounded sensitivity requests. Never called by normal rendering."""
from .case import Case, section_for
from .service import calculate, calculation_key


def sensitivity(run, variable, change):
    if variable not in {'S0', 'sig', 'rf_curve', 'cr_curve'}:
        raise ValueError('지원하지 않는 민감도 변수입니다.')
    if not 0 < change <= (50 if variable == 'S0' else 10):
        raise ValueError('주당가치 변화율은 0~50%, 변동성·금리 변화폭은 0~10%p 범위로 입력하십시오.')
    output = []
    for direction in [-1, 0, 1]:
        if direction == 0:
            selected = run
        else:
            case = Case.from_dict(run.case.to_dict())
            value = case.effective()[variable]
            adjusted = (value * (1 + direction * change / 100) if variable == 'S0' else
                        [[m, y + direction * change / 100] for m, y in value] if variable.endswith('curve') else
                        value + direction * change / 100)
            case.assumptions = [r for r in case.assumptions if r['field'] != variable]
            case.assumptions.append(dict(field=variable, value=adjusted, rationale='사용자가 실행한 민감도 분석'))
            selected = calculate(case)
        output.append({'변화': direction * change, **selected.summary['amounts_total']})
    return {'calculation_key': run.summary['calculation_key'], 'variable': variable, 'change': change, 'rows': output}
