"""Review records and fail-closed final value workpapers; no pricing on reads.

Names are user-declared review records, not authenticated electronic signatures.
Conditional scenario values cannot silently replace or be added to a base run.
"""
import copy
import datetime as dt
import math
from dataclasses import asdict
from .case import Case, FIELDS, RIGHT_KINDS, inspect_case
from .market_data import digest

GROUPS = [
    ('issuance', '발행·주식수·기준일', ['d_issue', 'd_mat', 'd_base', 'face_total', 'issue_px', 'par']),
    ('conversion', '전환·신주인수권', ['K0', 'cv_s', 'cv_e', 'mat_mode', 'bw_pay', 'bw_detach']),
    ('dividend', '배당·이자', ['cpn', 'ipay', 'div_mode', 'div_basis']),
    ('redemption', '상환·만기금액', ['p_s', 'p_e', 'p_mode', 'p_yield', 'p_cmp', 'p_less_cpn', 'ytm', 'mat_amt']),
    ('call', '콜·발행자 상환권', ['issuer_call', 'k_w', 'k_s', 'k_e', 'k_prem', 'k_method', 'k_hold']),
    ('reset', '전환가액 조정·IPO', ['rfx_mode', 'floor', 'K_cap', 'ipo_on', 'ipo_px']),
]


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def input_key(case):
    from .service import code_fingerprint
    obj = case.to_dict()
    obj.pop('review_controls'); obj.pop('judgments')
    return digest([obj, code_fingerprint()])


def _text(record, keys):
    return all(isinstance(record.get(k), str) and record[k].strip() for k in keys)


def validate_controls(controls):
    allowed = {'coverage', 'defaults', 'market', 'verification', 'review', 'final'}
    if not isinstance(controls, dict) or set(controls) - allowed:
        raise ValueError('지원하지 않는 검토 통제 항목입니다.')
    shapes = {
        'defaults': {'reviewer', 'rationale', 'reviewed_at', 'input_key'},
        'verification': {'reviewer', 'reference', 'reviewed_at', 'key', 'values', 'absolute_tolerance', 'relative_tolerance'},
        'review': {'preparer', 'reviewer', 'rationale', 'reviewed_at', 'key'},
        'final': {'reviewer', 'confirmed_at', 'key'},
    }
    for kind, shape in shapes.items():
        if kind not in controls:
            continue
        row = controls[kind]
        if not isinstance(row, dict) or set(row) != shape:
            raise ValueError(f'{kind}: 검토 기록의 필수 항목을 확인하십시오.')
        text_keys = shape - {'values', 'absolute_tolerance', 'relative_tolerance'}
        if not _text(row, text_keys):
            raise ValueError('검토자·근거·식별값을 기록하십시오.')
        if kind == 'verification':
            if not isinstance(row['values'], dict) or not row['values']:
                raise ValueError('독립 검산값이 필요합니다.')
            for value in list(row['values'].values()) + [row['absolute_tolerance'], row['relative_tolerance']]:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('독립 검산값·허용차이는 유한한 숫자여야 합니다.')
            if row['absolute_tolerance'] < 0 or not 0 <= row['relative_tolerance'] <= .01:
                raise ValueError('절대 허용차이는 0 이상, 상대 허용차이는 0~1%로 입력하십시오.')
    for kind, extra in [('coverage', {'mode', 'clause'}), ('market', {'date'})]:
        records = controls.get(kind, {})
        if not isinstance(records, dict):
            raise ValueError('검토 기록은 항목별 객체여야 합니다.')
        shape = {'reviewer', 'rationale', 'reviewed_at', 'input_key'} | extra
        for key, row in records.items():
            if not isinstance(key, str) or not isinstance(row, dict) or set(row) != shape or not _text(row, shape):
                raise ValueError('검토 기록에 조항·근거·검토자 등 필수 항목이 필요합니다.')
            if kind == 'coverage' and row['mode'] not in {'base', 'excluded', 'assumption', 'conditional'}:
                raise ValueError('계약 반영방식을 확인하십시오.')
            if kind == 'market':
                if key not in {'S0', 'sig', 'rf_curve', 'cr_curve'}:
                    raise ValueError('지원하지 않는 시장자료 검토 항목입니다.')
                dt.date.fromisoformat(row['date'])


def coverage_rows(case, applied=None):
    from .legacy import Terms
    from .presentation import label, display_value
    values = {**asdict(Terms()), **case.effective()}
    groups = GROUPS if values['inst'] != 'SHA' else [
        ('sha', '주주간계약 풋·콜', ['sha_put_s', 'sha_put_e', 'sha_put_yield', 'sha_call_s', 'sha_call_e', 'sha_call_prem', 'sha_writer', 'sha_disc'])]
    rows = []
    for key, title, fields in groups:
        rows.append(dict(id=key, title=title, inputs=' / '.join(f'{label(k)}: {display_value(k, values[k], values["d_issue"])}' for k in fields),
                         applied_inputs=' / '.join(f'{label(k)}: {display_value(k, applied[k], applied["d_issue"])}' for k in fields) if applied else '평가 실행 후 표시',
                         clause='', capability='기본 모형의 입력값. 계약과의 일치·비적용 여부를 확인하십시오.', right=None))
    for right in case.additional_rights:
        key = 'right:' + digest([right['kind'], right['clause']])[:20]
        rows.append(dict(id=key, title=RIGHT_KINDS[right['kind']], inputs=', '.join(right['assumption_fields']) or '기본 모형에 직접 계산되지 않음',
                         applied_inputs='별도 권리의 직접 계산 없음. 연결한 가정의 반영값·처리 근거 확인',
                         clause=right['clause'], capability='별도 조건. 제외의 중요성 또는 근사의 적정성을 검토하십시오.', right=right))
    current = input_key(case)
    for row in rows:
        record = case.review_controls.get('coverage', {}).get(row['id'], {})
        row['record'] = record
        row['current'] = bool(record) and record.get('input_key') == current
        row['status'] = '확인 완료' if row['current'] else '변경 후 재확인' if record else '미확인'
    return rows


def record_control(case, kind, *, reviewer, rationale, key=None, **extra):
    result = Case.from_dict(case.to_dict())
    row = dict(reviewer=reviewer.strip(), rationale=rationale.strip(), reviewed_at=now(), input_key=input_key(case), **extra)
    if kind in {'coverage', 'market'}:
        if kind == 'coverage':
            item = next((r for r in coverage_rows(case) if r['id'] == key), None)
            if not item:
                raise ValueError('계약 반영표의 항목을 선택하십시오.')
            if item['right'] and extra.get('mode') == 'base':
                raise ValueError('별도 권리는 기본 모형에 직접 반영되었다고 표시할 수 없습니다.')
        result.review_controls.setdefault(kind, {})[key] = row
    elif kind == 'defaults':
        result.review_controls[kind] = row
    else:
        raise ValueError('지원하지 않는 검토 기록입니다.')
    validate_controls(result.review_controls)
    return result


def default_fields(case):
    return sorted((FIELDS - set(case.effective())) | set(case.imported_defaults))


def result_key(run):
    from .evidence import source_version
    return digest([input_key(run.case), run.case.judgments, source_version(), run.summary['calculation_key'],
                   run.summary['amounts_100'], run.summary['checks'], run.summary['normalizations']])


def approval_key(run):
    records = copy.deepcopy(run.case.review_controls)
    records.pop('review', None); records.pop('final', None)
    return digest([result_key(run), records])


def final_key(run):
    return digest([approval_key(run), run.case.review_controls.get('review')])


def verification_rows(run):
    record = run.case.review_controls.get('verification', {})
    keys = ['put', 'call'] if run.terms.inst == 'SHA' else ['net']
    values = record.get('values', {})
    result = []
    for key in keys:
        actual = run.summary['amounts_100'][key]
        ref = values.get(key)
        tolerance = max(record.get('absolute_tolerance', 0), abs(ref or 0)*record.get('relative_tolerance', 0))
        result.append(dict(item=key, calculated=actual, reference=ref, difference=actual-ref if ref is not None else None,
                           tolerance=tolerance, passed=ref is not None and abs(actual-ref) <= tolerance))
    return result


def record_verification(run, *, reviewer, reference, values, absolute_tolerance, relative_tolerance):
    case = Case.from_dict(run.case.to_dict())
    expected = {'put', 'call'} if run.terms.inst == 'SHA' else {'net'}
    if set(values) != expected:
        raise ValueError('현재 결과와 같은 범위의 독립 검산값(원금 100 기준)을 입력하십시오.')
    case.review_controls['verification'] = dict(reviewer=reviewer.strip(), reference=reference.strip(), values=values,
        absolute_tolerance=absolute_tolerance, relative_tolerance=relative_tolerance, reviewed_at=now(), key=result_key(run))
    validate_controls(case.review_controls)
    return case


def blockers(run, *, require_review=True):
    from .service import calculation_key, code_fingerprint
    from .evidence import evidence_cards
    case = run.case
    out = []
    def add(code, message):
        out.append({'code': code, 'message': message})
    if run.summary.get('case_sha256') != case.fingerprint() or run.summary.get('calculation_key') != calculation_key(case) or run.summary.get('code_sha256') != code_fingerprint():
        add('stale_result', '입력·코드·결과 기록이 바뀌었습니다. 현재 입력으로 다시 평가하십시오.')
    if any(i.severity == 'error' for i in inspect_case(case)) or any(not c['passed'] for c in run.summary['checks']):
        add('input_error', '입력 오류 또는 산술 검산 차이를 해결하십시오.')
    for field in ('contract', 'S0', 'sig', 'rf_curve', 'cr_curve'):
        if not case.sources.get(field, '').strip():
            add('source:' + field, f'{field}: 원자료 위치와 산출근거를 기록하십시오.')
    current = input_key(case)
    for field in ('S0', 'sig', 'rf_curve', 'cr_curve'):
        record = case.review_controls.get('market', {}).get(field, {})
        if record.get('input_key') != current or record.get('date') != case.effective().get('d_base'):
            add('market:' + field, f'{field}: 현재 기준일 자료인지 확인하고 검토기록을 남기십시오.')
    if 'sig' in case.market_evidence:
        from .market_data import validate_pack
        try:
            pack = case.market_evidence['sig']
            validate_pack(pack, asof=case.effective().get('d_base'))
            if not math.isclose(pack['sigma'], case.effective().get('sig', -1), rel_tol=1e-12):
                raise ValueError('적용 변동성과 보관된 주가 산출값이 다릅니다.')
        except (ValueError, TypeError, KeyError) as exc:
            add('volatility_evidence', str(exc))
    if default_fields(case) and case.review_controls.get('defaults', {}).get('input_key') != current:
        add('defaults', '보충된 기본값과 비적용 설정을 확인하십시오.')
    for row in coverage_rows(case):
        rec = row['record']
        if not row['current']:
            add('coverage:' + row['id'], row['title'] + ': 계약 반영표를 확인하십시오.')
        elif rec['mode'] == 'conditional':
            add('conditional:' + row['id'], row['title'] + ': 조건부 분석은 기본 평가에 합산되지 않습니다. 별도 최종 평가 통합이 필요합니다.')
        elif row['right']:
            right = row['right']
            if right['treatment'] == 'unresolved' or rec['mode'] == 'base':
                add('unresolved:' + row['id'], row['title'] + ': 미해결 권리의 처리방식을 확정하십시오.')
            elif (right['treatment'], rec['mode']) not in {('excluded', 'excluded'), ('scenario', 'assumption')}:
                add('mismatch:' + row['id'], row['title'] + ': 별도 권리 기록과 반영표의 처리방식이 다릅니다.')
    for card in evidence_cards(case):
        if card['status'] not in {'검토 완료', '해당 없음'}:
            add('judgment:' + card['id'], card['title'] + ': 판단 근거의 검토를 완료하십시오.')
    verification = case.review_controls.get('verification', {})
    if verification.get('key') != result_key(run) or not all(r['passed'] for r in verification_rows(run)):
        add('verification', '현재 결과의 독립 검산 근거와 대사값을 기록하고 차이를 해결하십시오.')
    if require_review:
        review = case.review_controls.get('review', {})
        if review.get('key') != approval_key(run) or review.get('preparer', '').strip() == review.get('reviewer', '').strip():
            add('review', '작성자와 다른 검토자가 현재 입력·조서·남은 검토사항을 확인해야 합니다.')
    return out


def approve_review(run, preparer, reviewer, rationale):
    remaining = blockers(run, require_review=False)
    if remaining:
        raise ValueError(' / '.join(r['message'] for r in remaining))
    if not all(s.strip() for s in (preparer, reviewer, rationale)) or preparer.strip() == reviewer.strip():
        raise ValueError('작성자와 다른 검토자의 이름 및 검토결론을 입력하십시오.')
    case = Case.from_dict(run.case.to_dict())
    case.review_controls['review'] = dict(preparer=preparer.strip(), reviewer=reviewer.strip(), rationale=rationale.strip(), reviewed_at=now(), key=approval_key(run))
    case.review_controls.pop('final', None)
    return case


def finalize(run):
    remaining = blockers(run)
    if remaining:
        raise ValueError(' / '.join(r['message'] for r in remaining))
    case = Case.from_dict(run.case.to_dict())
    case.review_controls['final'] = dict(reviewer=case.review_controls['review']['reviewer'], confirmed_at=now(), key=final_key(run))
    return case


def is_final(run):
    return not blockers(run) and run.case.review_controls.get('final', {}).get('key') == final_key(run)


def workflow_state(case, run=None):
    from .service import calculation_key
    if run is None or run.summary.get('calculation_key') != calculation_key(case):
        return '입력 중'
    if case.fingerprint() != run.case.fingerprint():
        return '분석 완료 · 기록 갱신 필요'
    if is_final(run):
        return '최종 확정'
    return '검토 완료' if not blockers(run) else '분석 완료 · 검토 중'
