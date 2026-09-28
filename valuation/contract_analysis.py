"""Conditional RCPS cash-flow schedules on a TF stock lattice.

The schedules are explicit analyst assumptions, NOT a stochastic model of
distributable profits, funding, corporate actions, or extension probability.
The original engine is untouched. Unsupported contractual interactions fail
closed; these results do not replace the base run or its accounting allocation.
"""
import copy
import datetime as dt
import hashlib
import json
import math
import numpy as np
from . import legacy

SCOPE = ('입력 일정이 실현된다는 조건의 TF 분석. 이익·현금·기업행위의 확률과 주가의 공동분포, '
         '부분상환·콜옵션·정기 리픽싱·청산배분은 포함하지 않습니다. 기본 평가값을 대체하거나 자동 합산하지 않습니다.')
FIELDS = {'name', 'rationale', 'end_date', 'terminal', 'opening_unpaid', 'arrears_rate', 'conversion_arrears', 'schedule'}
ROW_FIELDS = {'date', 'dividend_due', 'dividend_paid', 'redemption_allowed', 'redemption_amount', 'profit_limit', 'cash_limit', 'reset_price'}


def number(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(label + '에 유효한 0 이상 숫자를 입력하십시오.')


def scenario_key(case, scenario):
    from .service import calculation_key
    return hashlib.sha256(json.dumps([calculation_key(case), scenario], sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_scenario(case, scenario):
    from .service import _prepare
    values = case.effective()
    if any(values.get(k, 0) for k in ['k_w', 'issuer_call', 'rfx_mode', 'ipo_on', 'put_bdt']):
        raise ValueError('콜·정기 리픽싱·IPO·BDT가 입력되어 있습니다. 이 조건의 일정 분석 결합을 지원하지 않습니다.')
    _, tm, _, _ = _prepare(case)
    if not isinstance(scenario, dict) or set(scenario) != FIELDS:
        raise ValueError('계약조건 분석의 필수 항목을 확인하십시오.')
    if not isinstance(scenario['name'], str) or not scenario['name'].strip() or not isinstance(scenario['rationale'], str) or not scenario['rationale'].strip():
        raise ValueError('시나리오 이름과 계약·일정 가정의 근거가 필요합니다.')
    if tm.inst != 'RCPS' or tm.model != 'TF':
        raise ValueError('이 일정 분석은 RCPS·TF 조건을 지원합니다.')
    if tm.k_w or tm.issuer_call or tm.rfx_mode or tm.ipo_on or tm.put_bdt:
        raise ValueError('콜·정기 리픽싱·IPO·BDT와 일정 분석의 결합은 지원하지 않습니다. 실제 계약에 해당 권리가 있다면 이 분석만으로 평가를 확정할 수 없습니다.')
    if scenario['terminal'] not in {'redeem', 'convert'} or scenario['conversion_arrears'] not in {'pay', 'forfeit'}:
        raise ValueError('종료 시 처리와 전환 시 미지급배당 처리를 확인하십시오.')
    for key in ['opening_unpaid', 'arrears_rate']:
        number(scenario[key], key)
    base = dt.date.fromisoformat(tm.d_base)
    end = dt.date.fromisoformat(scenario['end_date'])
    if end < dt.date.fromisoformat(tm.d_mat) or end <= base:
        raise ValueError('분석 종료일은 계약상 만기일 이후(당일 포함)이고 평가기준일보다 뒤여야 합니다.')
    rows = scenario['schedule']
    if not isinstance(rows, list) or not rows:
        raise ValueError('종료일을 포함한 사건·현금흐름 일정을 입력하십시오.')
    previous = base
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_FIELDS:
            raise ValueError('일정표의 날짜·배당·상환·가용재원·전환가액 열을 확인하십시오.')
        date = dt.date.fromisoformat(row['date'])
        if not previous < date <= end:
            raise ValueError('일정은 기준일 이후부터 종료일까지 중복 없이 날짜 오름차순이어야 합니다.')
        previous = date
        if type(row['redemption_allowed']) is not bool:
            raise ValueError('상환 허용 여부는 참·거짓으로 입력하십시오.')
        for key in ROW_FIELDS - {'date', 'redemption_allowed'}:
            number(row[key], key)
        if row['redemption_allowed'] and row['redemption_amount'] <= 0:
            raise ValueError('상환 허용일에는 상환금액이 필요합니다.')
    if previous != end:
        raise ValueError('일정의 마지막 행이 분석 종료일이어야 합니다.')
    return tm, base, end


def calculate_schedule(case, scenario, intervals=None):
    tm, base, end = validate_scenario(case, scenario)
    T = (end-base).days/365
    if intervals is not None and (type(intervals) is not int or intervals <= 0):
        raise ValueError('계산 구간 수에는 양의 정수가 필요합니다.')
    n = int(intervals) if intervals is not None else max(1, math.ceil(T*12/tm.gap_m))
    if not 1 <= n <= 1200:
        raise ValueError('일정 분석은 1~1,200 구간을 지원합니다.')
    tm = copy.deepcopy(tm); tm.T = T; tm.n = n
    RF, CR = legacy.curves(tm)
    step = T/n
    up, down = legacy.lattice_ud(tm, step)
    rf = np.array([legacy.forward_rate(RF, i*step, (i+1)*step) for i in range(n)])
    cr = np.array([legacy.forward_rate(CR, i*step, (i+1)*step) for i in range(n)])
    q = (np.exp((rf-tm.div_y)*step)-down)/(up-down)
    if np.any(q <= 0) or np.any(q >= 1):
        raise ValueError('일정 분석의 위험중립확률이 (0, 1)을 벗어납니다. 격자·금리·변동성을 확인하십시오.')
    events = {}
    timeline = []
    K = tm.K0
    arrears = float(scenario['opening_unpaid'])
    prev = base
    for row in scenario['schedule']:
        date = dt.date.fromisoformat(row['date'])
        i = min(n, max(1, math.ceil((date-base).days/(365*step)-1e-10)))
        if i in events:
            raise ValueError('둘 이상의 사건일이 같은 격자에 놓입니다. 계산 간격을 줄이십시오.')
        arrears *= (1+scenario['arrears_rate'])**((date-prev).days/365)
        arrears += row['dividend_due']
        if row['dividend_paid'] > arrears+1e-9:
            raise ValueError('배당 지급액이 당기 발생액과 미지급 누적액을 초과합니다.')
        arrears = max(0, arrears-row['dividend_paid'])
        if row['reset_price'] > 0:
            K = row['reset_price']
        payoff = row['redemption_amount'] + arrears
        feasible = row['redemption_allowed'] and min(row['profit_limit'], row['cash_limit'])+1e-9 >= payoff
        if i == n and scenario['terminal'] == 'redeem' and not feasible:
            raise ValueError('종료일 전액 상환에 필요한 이익·현금 또는 상환 허용 조건이 부족합니다. 기간 연장·회수금액을 별도로 검토하십시오.')
        events[i] = dict(row, arrears=arrears, K=K, feasible=feasible, payoff=payoff)
        timeline.append({'계약·가정일': row['date'], '격자 적용일': (base+dt.timedelta(days=round(i*step*365))).isoformat(),
                         '최대 시차(일)': i*step*365-(date-base).days, '원금100당 배당발생': row['dividend_due'],
                         '원금100당 배당지급': row['dividend_paid'], '원금100당 미지급누적': arrears,
                         '상환 허용': row['redemption_allowed'], '전액 상환재원 충족': feasible,
                         '원금100당 상환금액(미지급 포함)': payoff, '조정 후 전환가액(원)': K})
        prev = date
    # States are deterministic schedules conditional on survival. At every
    # event the paid dividend belongs to holders before exercise; conversion
    # then either forfeits or settles remaining arrears. No cash double count.
    prices = np.full(n+1, tm.K0, dtype=float)
    unpaid = np.zeros(n+1)
    last_k, last_unpaid, last_day = tm.K0, scenario['opening_unpaid'], 0.
    for i in range(n+1):
        if i in events:
            last_k, last_unpaid, last_day = events[i]['K'], events[i]['arrears'], (dt.date.fromisoformat(events[i]['date'])-base).days
        prices[i] = last_k
        unpaid[i] = last_unpaid*(1+scenario['arrears_rate'])**max(0, (i*step*365-last_day)/365)
        if i in events:
            unpaid[i] = events[i]['arrears']
    terminal = events[n]
    S = tm.S0 * up**np.arange(n+1) * down**(n-np.arange(n+1))
    cv = 100*S/prices[n]
    cv_cash = unpaid[n] if scenario['conversion_arrears'] == 'pay' else 0.
    issue = dt.date.fromisoformat(tm.d_issue)
    def conversion_open(i):
        absolute_m = ((base-issue).days/365 + i*step)*12
        return tm.cv_s-1e-9 <= absolute_m <= tm.cv_e+1e-9
    if scenario['terminal'] == 'convert':
        E = cv; B = np.full(n+1, cv_cash)
    else:
        exercise = (cv+cv_cash > terminal['payoff']) if conversion_open(n) else np.zeros(n+1, bool)
        E = np.where(exercise, cv, 0.)
        B = np.where(exercise, cv_cash, terminal['payoff'])
    B = B + terminal['dividend_paid']
    for i in range(n-1, -1, -1):
        E = (q[i]*E[1:]+(1-q[i])*E[:-1])*math.exp(-rf[i]*step)
        B = (q[i]*B[1:]+(1-q[i])*B[:-1])*math.exp(-cr[i]*step)
        row = events.get(i)
        if row and row['feasible']:
            exercise = row['payoff'] > E+B
            E = np.where(exercise, 0., E); B = np.where(exercise, row['payoff'], B)
        if conversion_open(i):
            S = tm.S0*up**np.arange(i+1)*down**(i-np.arange(i+1))
            cv = 100*S/prices[i]
            cash = unpaid[i] if scenario['conversion_arrears'] == 'pay' else 0.
            exercise = cv+cash > E+B
            E = np.where(exercise, cv, E); B = np.where(exercise, cash, B)
        if row:
            B += row['dividend_paid']
    value = float(E[0]+B[0])
    if not math.isfinite(value):
        raise ValueError('유한한 일정 분석값을 계산하지 못했습니다.')
    return dict(name=scenario['name'], value_100=value, value_total=value*tm.face_total/100,
                value_per_share=value*tm.issue_px/100, equity_cashflow_100=float(E[0]),
                debt_cashflow_100=float(B[0]), scenario=copy.deepcopy(scenario), timeline=timeline,
                intervals=n, key=scenario_key(case, scenario), scope=SCOPE,
                explanation='기존 배당·상환 일정은 이 분석에서 사용하지 않습니다. 입력한 전체 일정이 이를 대체합니다. 전환 행사기간은 현재 평가 입력을 따릅니다. 연장 중 전환 가능 여부도 별도 확인하십시오.',
                max_grid_delay_days=max(r['최대 시차(일)'] for r in timeline))
