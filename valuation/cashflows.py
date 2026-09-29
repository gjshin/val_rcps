"""Conditional partial-redemption TF analysis, separate from the base valuation.

Redemption fractions and funding are prescribed conditional schedules, not an
optimal partial-exercise policy or a stochastic profit/cash-flow model. Cash
receivables leave the convertible units at claim and are discounted to their
actual payment dates; subsequent conversion cannot erase those receivables.
"""
import copy
import datetime as dt
import math
import numpy as np
from . import legacy
from .market_data import digest

SCOPE = ('입력한 상환비율·이익·재원이 실현되는 조건부 분석입니다. 상환청구 전에는 전환과 예정 상환을 비교하고, '
         '청구된 물량은 전환권에서 제외하여 실제 지급일까지 할인합니다. 최적 부분상환비율, 이익·주가의 공동분포, '
         '청산배분·계약위반 확률은 산출하지 않습니다. 기본 평가값에 자동 합산하거나 최종 공정가치로 확정하지 않습니다.')
FIELDS = {'name', 'rationale', 'end_date', 'terminal', 'opening_unpaid', 'opening_paid_dividends',
          'arrears_rate', 'conversion_arrears', 'redemption_rate', 'deduct_paid_dividends',
          'redemption_arrears', 'extension_conversion', 'extension_redemption', 'schedule'}
ROW_FIELDS = {'date', 'dividend_due', 'dividend_paid', 'redemption_fraction', 'payment_date',
              'profit_limit', 'cash_limit', 'reset_price', 'penalty_rate', 'penalty_start'}


def number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def validate_shape(scenario):
    if not isinstance(scenario, dict) or set(scenario) != FIELDS:
        raise ValueError('부분상환 분석의 필수 항목을 확인하십시오.')
    if any(not isinstance(scenario[k], str) or not scenario[k].strip() for k in ('name', 'rationale')):
        raise ValueError('분석 이름과 계약·일정 가정의 근거가 필요합니다.')
    if scenario['terminal'] not in {'redeem', 'convert'} or scenario['conversion_arrears'] not in {'pay', 'forfeit'} or scenario['redemption_arrears'] not in {'included', 'add'}:
        raise ValueError('종료 처리·배당 포함 여부를 확인하십시오.')
    if any(type(scenario[k]) is not bool for k in ('deduct_paid_dividends', 'extension_conversion', 'extension_redemption')):
        raise ValueError('배당 차감·연장 중 권리는 참·거짓으로 입력하십시오.')
    if any(not number(scenario[k]) for k in ('opening_unpaid', 'opening_paid_dividends', 'arrears_rate', 'redemption_rate')):
        raise ValueError('배당 잔액·가산이율·상환이율은 유한한 0 이상 숫자여야 합니다.')
    try:
        end = dt.date.fromisoformat(scenario['end_date'])
        rows = scenario['schedule']
        if not isinstance(rows, list) or not rows:
            raise ValueError('종료일을 포함한 일정을 입력하십시오.')
        previous = None
        for row in rows:
            if not isinstance(row, dict) or set(row) != ROW_FIELDS:
                raise ValueError('일정의 필수 열을 확인하십시오.')
            day = dt.date.fromisoformat(row['date'])
            if previous and day <= previous or day > end:
                raise ValueError('일정은 종료일 이내에서 중복 없이 날짜 오름차순이어야 합니다.')
            previous = day
            if any(not number(row[k]) for k in ROW_FIELDS - {'date', 'payment_date', 'penalty_start'}):
                raise ValueError('일정의 금액·비율에는 유한한 0 이상 숫자가 필요합니다.')
            if row['redemption_fraction'] > 1:
                raise ValueError('상환비율은 잔존 물량의 0~1로 입력하십시오.')
            if row['redemption_fraction']:
                payment = dt.date.fromisoformat(row['payment_date'])
                if payment < day:
                    raise ValueError('지급일이 청구일보다 빠릅니다.')
                if row['penalty_rate']:
                    start = dt.date.fromisoformat(row['penalty_start'])
                    if not day <= start <= payment:
                        raise ValueError('지연 가산 시작일은 청구일부터 지급일 사이여야 합니다.')
                elif row['penalty_start'] != '':
                    raise ValueError('지연 가산이율이 0이면 가산 시작일은 비우십시오.')
            elif row['payment_date'] != '' or row['penalty_rate'] != 0 or row['penalty_start'] != '':
                raise ValueError('상환 없는 행의 지급일·지연 가산 조건은 비우십시오.')
        if previous != end:
            raise ValueError('마지막 일정은 분석 종료일이어야 합니다.')
    except (TypeError, OverflowError) as exc:
        raise ValueError('날짜는 YYYY-MM-DD 문자열로 입력하십시오.') from exc


def scenario_key(case, scenario):
    from .service import calculation_key
    return digest([calculation_key(case), scenario])


def validate_scenario(case, scenario):
    from .service import _prepare
    validate_shape(scenario)
    values = case.effective()
    if any(values.get(k, 0) for k in ('k_w', 'issuer_call', 'rfx_mode', 'ipo_on', 'put_bdt')):
        raise ValueError('콜·정기 리픽싱·IPO·BDT와 부분상환 분석의 결합은 지원하지 않습니다.')
    _, tm, _, _ = _prepare(case)
    if tm.inst != 'RCPS' or tm.model != 'TF':
        raise ValueError('부분상환 일정 분석은 RCPS·TF에서 사용할 수 있습니다.')
    base, maturity, issue, end = map(dt.date.fromisoformat, (tm.d_base, tm.d_mat, tm.d_issue, scenario['end_date']))
    if end < maturity or end <= base:
        raise ValueError('분석 종료일은 계약 만기일 이후(당일 포함)여야 합니다.')
    put_start, put_end = legacy.months_to_date(issue, tm.p_s), legacy.months_to_date(issue, tm.p_e)
    for row in scenario['schedule']:
        day = dt.date.fromisoformat(row['date'])
        if day <= base:
            raise ValueError('일정은 평가기준일 이후여야 합니다. 기준일 이전 배당은 기초 잔액에 기록하십시오.')
        if row['redemption_fraction']:
            terminal_redemption = day == end == maturity and scenario['terminal'] == 'redeem' and tm.mat_mode == 1
            extension = day > maturity and scenario['extension_redemption']
            if not (put_start <= day <= put_end or terminal_redemption or extension):
                raise ValueError('상환청구일이 행사기간 밖입니다. 실제 계약기간 또는 연장 중 상환권을 확인하십시오.')
    return tm, base, issue, end


def calculate_cashflows(case, scenario, intervals=None):
    tm, base, issue, end = validate_scenario(case, scenario)
    T = (end-base).days/365
    n = max(1, math.ceil(T*12/tm.gap_m)) if intervals is None else intervals
    if type(n) is not int or not 1 <= n <= 1200:
        raise ValueError('계산 구간 수는 1~1,200의 정수여야 합니다.')
    step = T/n
    tm = copy.deepcopy(tm); tm.T = T; tm.n = n
    RF, CR = legacy.curves(tm)
    up, down = legacy.lattice_ud(tm, step)
    rf = np.array([legacy.forward_rate(RF, i*step, (i+1)*step) for i in range(n)])
    cr = np.array([legacy.forward_rate(CR, i*step, (i+1)*step) for i in range(n)])
    q = (np.exp((rf-tm.div_y)*step)-down)/(up-down)
    if np.any(q <= 0) or np.any(q >= 1):
        raise ValueError('위험중립확률이 (0, 1)을 벗어납니다. 금리·변동성·격자를 확인하십시오.')
    def discount(t):
        return math.exp(-CR(t)*t)
    events, timeline = {}, []
    fraction, unpaid, paid = 1., scenario['opening_unpaid'], scenario['opening_paid_dividends']
    previous, K = base, tm.K0
    for row in scenario['schedule']:
        day = dt.date.fromisoformat(row['date'])
        t = (day-base).days/365
        i = min(n, max(1, math.ceil(t/step-1e-10)))
        if i in events:
            raise ValueError('서로 다른 사건일이 같은 격자에 놓입니다. 계산 간격을 줄이십시오.')
        if fraction <= 1e-12 and any(row[k] for k in ('dividend_due', 'dividend_paid', 'redemption_fraction', 'reset_price')):
            raise ValueError('전량 상환청구 이후에는 배당·상환·전환가액 변경을 입력할 수 없습니다.')
        unpaid = unpaid*(1+scenario['arrears_rate'])**((day-previous).days/365) + row['dividend_due']
        if row['dividend_paid'] > unpaid + 1e-9:
            raise ValueError('지급배당이 누적 미지급배당과 당기 발생액을 초과합니다.')
        unpaid = max(0., unpaid-row['dividend_paid'])
        paid += row['dividend_paid']
        K = row['reset_price'] or K
        before = fraction
        redeemed = before*row['redemption_fraction']
        fraction = max(0., before-redeemed)
        unit = 100*(1+scenario['redemption_rate'])**((day-issue).days/365)
        deduction = paid if scenario['deduct_paid_dividends'] else 0.
        unit -= deduction
        if scenario['redemption_arrears'] == 'add':
            unit += unpaid
        if redeemed and unit < 0:
            raise ValueError('기지급배당 차감 후 상환금액이 음수입니다. 상환 약정과 배당 차감을 확인하십시오.')
        cash = max(0., unit)*redeemed
        claim_pv = 0.
        if redeemed:
            payment = dt.date.fromisoformat(row['payment_date'])
            if row['penalty_rate']:
                start = dt.date.fromisoformat(row['penalty_start'])
                cash *= (1+row['penalty_rate'])**((payment-start).days/365)
            if cash > min(row['profit_limit'], row['cash_limit']) + 1e-9:
                raise ValueError('지급일까지 배분 가능한 이익·현금이 예정 상환액보다 작습니다. 상환비율 또는 지급일을 수정하십시오.')
            claim_pv = cash*discount((payment-base).days/365)/discount(i*step)
        # Date-exact discount adjustment preserves cash-only analytical PVs.
        dividend_at_node = before*row['dividend_paid']*discount(t)/discount(i*step)
        events[i] = dict(before=before, after=fraction, unpaid=unpaid, K=K, claim=claim_pv,
                         dividend=dividend_at_node, date=day, t=t)
        timeline.append({'청구·발생일': row['date'], '지급일': row['payment_date'],
                         '상환 전 잔존비율': before, '최초 물량 대비 상환비율': redeemed, '상환 후 잔존비율': fraction,
                         '잔존100당 미지급배당': unpaid, '잔존100당 기지급배당 누적': paid,
                         '잔존100당 배당 차감': deduction, '최초100당 지급배당': before*row['dividend_paid'],
                         '최초100당 상환 현금': cash, '전환가액(원)': K, '격자 이연(일)': (i*step-t)*365})
        previous = day
    if scenario['terminal'] == 'redeem' and fraction > 1e-10:
        raise ValueError('종료 시 상환을 선택하면 마지막까지 잔존 물량이 전부 상환청구되어야 합니다.')
    # Deterministic state path for the units that have not yet been claimed.
    states = []
    f, unpaid, K, tprev = 1., scenario['opening_unpaid'], tm.K0, 0.
    for i in range(n+1):
        event = events.get(i)
        if event:
            f, unpaid, K, tprev = event['after'], event['unpaid'], event['K'], event['t']
        states.append((f, unpaid*(1+scenario['arrears_rate'])**max(0., i*step-tprev), K))
    maturity = dt.date.fromisoformat(tm.d_mat)
    cv_start = (legacy.months_to_date(issue, tm.cv_s)-base).days/365
    cv_end = (legacy.months_to_date(issue, tm.cv_e)-base).days/365
    def conversion_open(i):
        ordinary = cv_start-1e-9 <= i*step <= cv_end+1e-9
        extended = scenario['extension_conversion'] and tm.cv_s <= tm.cv_e and i*step > (maturity-base).days/365
        return ordinary or extended
    E, B = np.zeros(n+1), np.zeros(n+1)
    for i in range(n, -1, -1):
        if i < n:
            E = (q[i]*E[1:] + (1-q[i])*E[:-1])*math.exp(-rf[i]*step)
            B = (q[i]*B[1:] + (1-q[i])*B[:-1])*math.exp(-cr[i]*step)
        f, arrears, K = states[i]
        stock = tm.S0*up**np.arange(i+1)*down**(i-np.arange(i+1))
        event = events.get(i)
        if i == n and scenario['terminal'] == 'convert':
            E = f*100*stock/K
            B = np.full(i+1, f*arrears if scenario['conversion_arrears'] == 'pay' else 0.)
        if event:
            B += event['claim']
            # Choice occurs before the scheduled claim, after the dividend.
            f, arrears, K = event['before'], event['unpaid'], event['K']
        if conversion_open(i):
            cv = f*100*stock/K
            cv_cash = f*arrears if scenario['conversion_arrears'] == 'pay' else 0.
            # A rounding-sized tie must not switch the TF equity/debt branch:
            # their pre-event discount rates differ even at the same payoff.
            tolerance = 1e-12*np.maximum(1., np.maximum(np.abs(cv+cv_cash), np.abs(E+B)))
            exercise = cv + cv_cash > E + B + tolerance
            E = np.where(exercise, cv, E)
            B = np.where(exercise, cv_cash, B)
        if event:
            B += event['dividend']
    value = float(E[0]+B[0])
    if not math.isfinite(value):
        raise ValueError('유한한 분석값을 산출하지 못했습니다.')
    last_payment = max([end] + [dt.date.fromisoformat(r['payment_date']) for r in scenario['schedule'] if r['payment_date']])
    return dict(name=scenario['name'], value_100=value, value_total=value*tm.face_total/100,
                value_per_share=value*tm.issue_px/100, equity_cashflow_100=float(E[0]), debt_cashflow_100=float(B[0]),
                scenario=copy.deepcopy(scenario), timeline=timeline, key=scenario_key(case, scenario), intervals=n,
                max_grid_delay_days=max(r['격자 이연(일)'] for r in timeline), scope=SCOPE,
                explanation='발생·지급배당은 그 시점 잔존 물량의 원금 100당입니다. 이익·현금 한도는 최초 원금 100당, 해당 회차·지급건 배분액입니다. 상환이율은 실제일수/365 연복리이며 지연 가산은 명시한 기간에만 적용합니다. 원래 배당·상환 일정은 사용하지 않습니다.',
                curve_extrapolation=last_payment > base + dt.timedelta(days=round(max(t for t, _ in legacy.credit_curve(tm))*365)))
