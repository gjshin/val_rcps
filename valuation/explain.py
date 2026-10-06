"""Read-only calculation evidence from the same run used for the result and export."""
import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict
from . import legacy as L

VERSION = '2026.10.06-v2.1-preview'
KINDS = {'hold': '계속 보유', 'conv': '전환', 'put': '상환청구', 'call': '콜 행사',
         'mat': '만기상환', 'auto': '만기 자동전환', 'ipo': '상장 강제전환'}


def memo_key(case, topic):
    """Conservative dependency signature; metadata and other memos do not reprice."""
    values = {**asdict(L.Terms()), **case.effective()}
    groups = {
        'call_method': ['k_method', 'k_w', 'k_lock', 'k_hold', 'k_conv_resp', 'issuer_call', 'model', 'd_base'],
        'conv_resp': ['k_conv_resp', 'cv_s', 'cv_e', 'k_s', 'k_e', 'k_lock', 'k_hold', 'd_base'],
        'priority': ['pc_order', 'p_s', 'p_e', 'k_s', 'k_e', 'd_base'],
        'rcps_equity': ['inst', 'view', 'conv_class', 'issuer_call', 'mat_mode'],
    }
    inputs = {k: values[k] for k in groups.get(topic, sorted(values))}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def memo_status(case, topic):
    if not case.memos.get(topic):
        return '미작성'
    old = case.memo_context.get(topic)
    if not old:
        return '작성 당시 조건 미확인'
    return '현재 조건의 기록' if old == memo_key(case, topic) else '이전 조건의 기록 · 재확인 필요'


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
        blockers.append('배당가능이익 미입력 발생연도 ' + ', '.join(map(str, missing)) +
                        '년: 연도별 재원을 보완하거나, 미입력 연도를 제한 없이 상환하는 가정과 근거를 입력하십시오.')
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
