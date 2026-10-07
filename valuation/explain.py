"""Read-only calculation evidence from the same run used for the result and export."""
import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict
from . import legacy as L

VERSION = '2026.10.06-v2.2'
KINDS = {'hold': '계속 보유', 'conv': '전환', 'put': '상환청구', 'call': '콜 행사',
         'mat': '만기상환', 'auto': '만기 자동전환', 'ipo': '상장 강제전환'}


# 메모가 «현재 조건의 기록» 인지 가리는 식별값. 계약 조항을 보고 내린 판단(콜 방법·우선순위·분리·검토 주제)은
# 시장자료(주가·변동성·금리곡선·평가기준일)를 뺀 모든 입력을 본다 — 분기마다 시장자료만 바뀌면 메모가 그대로
# 현재 조건이고, 계약 입력이 하나라도 바뀌면 다시 확인한다. 평가 결과 숫자를 보고 내린 판단(BDT 검토·최초 인식
# 원인 점검·시장자료 적정성)은 모든 입력을 본다. 항목을 주제마다 골라 적으면 빠뜨린 입력이 생긴다.
_MARKET = ['S0', 'sig', 'div_y', 'rf_curve', 'cr_curve', 'cr_curve_b', 'rate_mode', 'd_base']
# 판단과 무관한 기록 칸 — 주가 조회 출처·종목코드·원주가 등
_RECORDS = {'s0_src', 's0_date', 's0_raw', 's0_adj', 's0_splits', 'ticker', 'cr_src', 'tranche', 'unmod_note',
            'scen_md5', 'rvol_how', 'split_base_why', 'd1_reason'}


def memo_fields(topic, values=None):
    """그 메모의 식별값에 넣을 입력 — None 이면 모든 입력, ['__all_but_market__'] 이면 시장자료·기록을 뺀 모든 입력."""
    if topic in ('split_put', 'split_call', 'split_conv') and values is not None:
        # 분리 판단의 출발 금액을 자동값(100 + 별개 콜 가치)으로 두면 그 콜 가치가 시장자료로 바뀐다.
        auto_with_call = float(values.get('split_base_in', -1) or -1) <= 0 and L.split_call_separate(L.Terms(**{
            k: v for k, v in values.items() if k in L.Terms.__dataclass_fields__}))
        # 분리 판단은 행사금액·상각후원가를 정하는 계약 입력 전부를 본다(일정표·산식 공제 포함). 시장자료는 자동 출발 금액일 때만.
        return None if auto_with_call else ['__all_but_market__']
    # 평가 결과 숫자를 보고 내리는 판단은 모든 입력을 본다 — BDT 검토(주가÷전환가액·보장수익률·금리·변동성 민감도),
    # 최초 인식 차이(모형값 전체), 시장자료 자체를 판단하는 주제.
    if topic in ('bdt', 'fair_value_inputs') or topic.startswith('day1'):
        return None
    # 그 밖의 판단(콜 방법·우선순위·전환 대응·검토 주제)은 시장자료와 출처 기록을 뺀 모든 입력을 본다.
    return ['__all_but_market__']


def memo_key(case, topic):
    """Conservative dependency signature; metadata and other memos do not reprice."""
    values = {**asdict(L.Terms()), **case.effective()}
    fields = memo_fields(topic, values)
    if fields is None:
        fields = sorted(values)
    elif fields == ['__all_but_market__']:
        fields = sorted(k for k in values if k not in _MARKET and k not in _RECORDS)
    inputs = {k: values.get(k) for k in fields}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


_V21_GROUPS = {
    'call_method': ['k_method', 'k_w', 'k_lock', 'k_hold', 'k_conv_resp', 'issuer_call', 'model', 'd_base'],
    'conv_resp': ['k_conv_resp', 'cv_s', 'cv_e', 'k_s', 'k_e', 'k_lock', 'k_hold', 'd_base'],
    'priority': ['pc_order', 'p_s', 'p_e', 'k_s', 'k_e', 'd_base'],
    'rcps_equity': ['inst', 'view', 'conv_class', 'issuer_call', 'mat_mode'],
}


def _memo_key_v21(case, topic):
    """2026.10.06 판(v2.1~2.2)이 저장한 식별값 — 그 판에서 저장한 메모를 공연히 «재확인 필요» 로 만들지 않는다."""
    values = {**asdict(L.Terms()), **case.effective()}
    groups = _V21_GROUPS
    # 이전 판이 보지 않던 항목을 지금 보면, 그 항목이 바뀌었는지 이전 식별값으로는 알 수 없다 — 다시 확인하게 한다.
    new = memo_fields(topic, values)
    if topic in groups and (new is None or not set(new) <= set(groups[topic])):
        return None
    try:
        inputs = {k: values[k] for k in groups.get(topic, sorted(values))}
        return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    except (KeyError, TypeError):
        return None


def memo_status(case, topic):
    if not case.memos.get(topic):
        return '미작성'
    old = case.memo_context.get(topic)
    if not old:
        return '작성 당시 조건 미확인'
    current = old == memo_key(case, topic) or old == _memo_key_v21(case, topic)
    return '현재 조건의 기록' if current else '이전 조건의 기록 · 재확인 필요'


def put_diagnostic(t):
    if L.is_sha(t) or t.p_s > t.p_e or t.conv_class != 'equity':
        return []
    r = L.engine(t, conv=False, put=False, call=False)
    ea = L.exercise_amounts(t, t.n, t.T/t.n)
    rows = []
    pay = L.pay_steps(t, t.n, t.T/t.n)
    for i, month in sorted(ea['p_dates'].items()):
        o = r['memo'].get((i, 0))
        if o is None or not ea['p_on'](i):
            continue
        value = ea['put_val'](i)
        coupon = 100*L.eff_cpn(t)*t.ipay/12*pay.get(i, 0)
        if i == t.n or t.p_cpn_add:
            value += coupon
        hold = o['E']+o['B']
        rows.append({'스텝': i, '발행 후 개월': month, '계약 청구액': ea['put'](i),
                     '지급 제약·이자 반영 청구가치': value, '계속보유가치': hold,
                     '비율': value/hold if abs(hold) > 1e-12 else None})
    return rows


def dp_missing_years(t):
    if not L.dp_active(t):
        return []
    ea = L.exercise_amounts(t, t.n, t.T/t.n)
    plan = ea['dp']
    missing = set()
    claims = [(i, ea['put'](i), 'put') for i in ea['p_dates']] + [(t.n, ea['red'], None)]
    for i, amount, kind in claims:
        year = L.dp_fund_year(t, plan.claim_dt(i, kind))
        for k, _, _, cap, before, _, paid, _ in plan.schedule(i, amount, kind)['rows']:
            if cap >= L.DP_INF/10 and before > 1e-12 and paid > 1e-12:
                missing.add(year+k-1)
    if L.issuer_redeem(t):
        profits = L.dp_profits(t)
        for i in ea['k_dates']:
            year = L.dp_fund_year(t, plan.claim_dt(i, 'call'))-1
            if year not in profits:
                missing.add(year)
    return sorted(missing)


def export_blockers(run):
    blockers = []
    missing = run.summary.get('dp_missing_years', [])
    if missing and not run.case.sources.get('dp_missing_assumption', '').strip():
        blockers.append('배당가능이익을 넣지 않은 발생연도 ' + ', '.join(map(str, missing)) +
                        '년의 재원이 실제 지급에 쓰였습니다. «입력 → 계약·평가 입력 → 상환청구권 → 배당가능이익에 따른 '
                        '상환 제약» 에서 그 해의 배당가능이익을 표에 넣거나, 표 아래 «넣지 않은 발생연도의 재원 가정·근거» '
                        '를 적고 저장하십시오.')
    for row in run.case.additional_rights:
        if row.get('treatment') == 'unresolved':
            blockers.append('직접 반영되지 않은 계약조건이 미해결입니다: ' + str(row.get('clause', row.get('kind'))))
    return blockers


def node_trace(run, i=0, j=0):
    """No new pricing. Explain full's pre-call tree; never claim it is the net tree."""
    if L.is_sha(run.terms):
        raise ValueError('주주간계약은 회차별 풋·콜 계산표를 사용합니다.')
    r, t = run.raw['full'], run.terms
    key = (int(i), int(j))
    if key not in r['memo']:
        raise ValueError('이 노드는 조기 종료되어 계산되지 않았습니다. 이전 시점을 선택하십시오.')
    o = r['memo'][key]
    out = {'step': i, 'up_count': j, 'date': (dt.date.fromisoformat(t.d_base) + dt.timedelta(days=round(i*r['dt']*365))).isoformat(),
           'stock': r['S'](i, j), 'conversion_price': o.get('K'), 'node': o,
           'model': t.model, 'value': o['V'] if t.model == 'GS' else o['E']+o['B'],
           'kind': KINDS.get(o.get('kind'), o.get('kind', '—')), 'terminal': 'up' not in o,
           'eligible': {k: bool(r[fn](i)) for k,fn in [('cv','can_convert'),('pv','can_put'),('kv','can_call')]}}
    if 'up' not in o:
        return out
    a, b = r['memo'][o['up']], r['memo'][o['dn']]
    q, delta = r['qi'](i), r['dt']
    rf, cr = r['fwdRF'](i), r['fwdCR'](i)
    coupon = 100*L.eff_cpn(t)*t.ipay/12*L.pay_steps(t, t.n, delta).get(i, 0)
    equity = (q*a['E']+(1-q)*b['E'])*math.exp(-rf*delta)
    debt = (q*a['B']+(1-q)*b['B'])*math.exp(-cr*delta)+coupon
    ya, yb = a['P']*rf+(1-a['P'])*cr, b['P']*rf+(1-b['P'])*cr
    gs = q*a['V']*math.exp(-ya*delta)+(1-q)*b['V']*math.exp(-yb*delta)+coupon
    hold = gs if t.model == 'GS' else equity+debt
    out.update(q=q, delta=delta, rf=rf, cr=cr, df_rf=math.exp(-rf*delta), df_cr=math.exp(-cr*delta),
               up=a, down=b, up_key=o['up'], down_key=o['dn'], coupon=coupon,
               equity_hold=equity, debt_hold=debt, hold=hold, gs_rates=(ya,yb),
               reconciliation=hold-o.get('Vc' if t.model=='GS' else 'hold',hold))
    return out


def rate_rows(run):
    t = run.terms
    rf, cr = L.curves(t)
    rows = []
    for i in range(t.n+1):
        time = i*t.T/t.n
        rows.append({'스텝': i, '경과연수': time, '무위험 현물금리(연속)': rf(time),
                     '위험 현물금리(연속)': cr(time), '무위험 할인계수': math.exp(-rf(time)*time),
                     '위험 할인계수': math.exp(-cr(time)*time)})
    return rows


def event_rows(run):
    t = run.terms
    if L.is_sha(t):
        return run.summary.get('sha_rows') or []
    ea = L.exercise_amounts(t,t.n,t.T/t.n)
    rows=[]
    for i in sorted(set(ea['p_dates']) | set(ea['k_dates']) | {t.n}):
        month=ea['cmonth'](i)
        rows.append({'스텝': i, '격자일': (dt.date.fromisoformat(t.d_base)+dt.timedelta(days=round(i*t.T/t.n*365))).isoformat(),
                     '발행 후 개월': month, '계약 상환액': ea['put'](i) if ea['p_on'](i) else None,
                     '지급 제약 반영 상환가치': ea['put_val'](i) if ea['p_on'](i) else None,
                     '계약 콜금액': ea['call'](i) if i in ea['k_dates'] else None,
                     '콜 행사 가능': '가능' if ea['k_on'](i) and i<t.n else '불가',
                     '만기 지급가치': ea['red_val'] if i==t.n else None})
    return rows


def safe_filename(run, kind, extension):
    import re
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', run.case.name).strip(' .')[:70] or '평가'
    return f'{name}_{run.terms.inst}_{run.terms.d_base}_{kind}_{run.summary["run_id"]}.{extension}'
