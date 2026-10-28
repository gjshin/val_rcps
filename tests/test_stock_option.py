"""주식선택권(주식결제형) — 격자·블랙-숄즈·비용 배분·입력 검사·조서·화면.

숫자는 모두 가상의 값이다 (실제 회사 자료 아님).
"""
import copy
import io
import math
import shutil
import zipfile

import pytest
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

import v2_workspace
from valuation import stock_option as SO
from valuation import legacy as L
from valuation import stock_option_workpaper as SOW
from valuation.case import Case, inspect_case
from valuation.evidence import TOPICS, applicable
from valuation.service import calculate, export_bundle, CaseError
from test_v2_workflow import ROOT


def case(**over):
    rows = over.pop('so_rows', [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0)])
    contract = dict(inst='SO', tranche='가상', d_issue='2026-01-01', d_mat='2031-01-01', K0=100.0, so_settle=0,
                    so_fy_end='12-31', so_rows=rows, so_mod=0, so_mod_rows=[], so_mkt=0)
    market = dict(S0=100.0, sig=0.3, div_y=0.0, rf_curve=[[1, 0.03], [5, 0.03]], y_type='spot', cmp_rf=1)
    method = dict(d_base='2026-01-01', so_ex=0, so_mult=2.0, so_exit=0.0, grid_days=7.0, gap_m=1.0, so_prev_liab=-1.0,
                  so_paid=0.0, so_nv=0.0)
    for key, value in over.items():
        for group in (contract, market, method):
            if key in group:
                group[key] = value
                break
        else:
            raise KeyError(key)
    return Case(name='가상 주식선택권', contract=contract, market=market, method=method)


def fv(c):
    return calculate(c).raw['rows'][0]['fv']


def test_black_scholes_formula():
    # 교과서 값 — S = K = 100, T = 1, r = 5%, σ = 20%, 배당 없음 → 10.4506
    assert SO.black_scholes(100, 100, 1, 0.05, 0, 0.2) == pytest.approx(10.4506, abs=1e-4)
    assert SO.black_scholes(100, 120, 0, 0.05, 0, 0.2) == 0.0


def test_european_lattice_converges_to_black_scholes():
    run = calculate(case())
    r = run.raw['rows'][0]
    assert r['fv'] == pytest.approx(r['bs_end'], rel=SO.BS_TOL)
    assert abs(r['fv'] / r['bs_end'] - 1) < 0.002          # 주 단위 격자면 0.2% 안쪽
    assert run.raw['integrity'] == []
    check = next(c for c in run.summary['checks'] if c['name'].startswith('블랙-숄즈'))
    assert check['passed']


def test_no_dividend_optimal_exercise_equals_european_and_dividend_makes_it_worth_more():
    assert fv(case(so_ex=1)) == pytest.approx(fv(case(so_ex=0)), rel=1e-12)
    eu, am = fv(case(so_ex=0, div_y=0.05)), fv(case(so_ex=1, div_y=0.05))
    assert am > eu


def test_exercise_multiple_and_exit_rate_lower_the_value():
    best = fv(case(so_ex=1))
    multiple = fv(case(so_ex=2, so_mult=1.5))
    assert multiple < best
    assert fv(case(so_ex=2, so_mult=3.0)) > multiple           # 배수가 클수록 늦게 행사
    assert fv(case(so_ex=1, so_exit=0.1)) < best
    run = calculate(case(so_ex=2, so_mult=1.5, so_exit=0.1))
    r = run.raw['rows'][0]
    assert r['life'] < r['t_end']                              # 조기행사·퇴사로 기대존속기간이 짧다
    assert 'so_early_exercise' not in {i.code for i in run.issues}


def test_no_exercise_before_exercise_start():
    run = calculate(case(so_ex=1, div_y=0.08, so_rows=[dict(name='1차', vest='2027-01-01', start='2028-07-01',
                                                             end='', qty=10.0, keep=1.0)]))
    r = run.raw['rows'][0]
    assert r['ex_step'] > 0
    assert not any(any(g) for g in r['exercise'][:r['ex_step']])
    assert any(any(g) for g in r['exercise'][r['ex_step']:-1])  # 배당이 크면 행사 시작 뒤 조기행사가 생긴다


def test_node_trace_reconciles_every_node():
    run = calculate(case(so_ex=2, so_mult=1.6, so_exit=0.05))
    r = run.raw['rows'][0]
    worst = max(abs(SO.node_trace(run.terms, r, i, j)['reconciliation'])
                for i in range(0, r['n'] + 1, 5) for j in range(0, i + 1, 3))
    assert worst < 1e-9
    assert SO.node_trace(run.terms, r, r['n'], 0)['terminal']
    with pytest.raises(ValueError):
        SO.node_trace(run.terms, r, 3, 4)


# ── 시장조건·비가득조건 (문단 21, 21A) — 격자 안에서 판정한다 ─────────────────────

def _gap_call(S, K, H, T, r, q, s):
    """만기 주가가 H 이상일 때만 S − K 를 받는 갭 옵션 — 가득일 = 만기인 «가득일 판정» 의 닫힌 식."""
    d1 = (math.log(S/H) + (r - q + s*s/2)*T)/(s*math.sqrt(T))
    return S*math.exp(-q*T)*SO._ncdf(d1) - K*math.exp(-r*T)*SO._ncdf(d1 - s*math.sqrt(T))


def _up_in_call(S, K, H, T, r, q, s):
    """연속 관찰 상향 진입 콜 (H > K) — 만기까지 한 번이라도 H 에 닿으면 보통 콜이 된다."""
    lam, st = (r - q + s*s/2)/(s*s), s*math.sqrt(T)
    y = math.log(H*H/(S*K))/st + lam*st
    x1, y1 = math.log(S/H)/st + lam*st, math.log(H/S)/st + lam*st
    N = SO._ncdf
    return (S*math.exp(-q*T)*N(x1) - K*math.exp(-r*T)*N(x1 - st)
            - S*math.exp(-q*T)*(H/S)**(2*lam)*(N(-y) - N(-y1))
            + K*math.exp(-r*T)*(H/S)**(2*lam - 2)*(N(-y + st) - N(-y1 + st)))


def _target_row(H, vest='2028-01-01', end='2028-01-01'):
    return [dict(name='1차', vest=vest, end=end, qty=1.0, keep=1.0, target=H)]


@pytest.mark.parametrize('H', [110.0, 130.0, 170.0])
def test_market_condition_at_vest_matches_the_gap_option(H):
    # 가득일 = 행사 종료일이면 «가득일 주가 ≥ 목표» 는 갭 옵션이다. 목표가 걸친 노드를 비율로 나눠 계단 오차를 줄인다.
    run = calculate(case(so_rows=_target_row(H), so_mkt=1, d_mat='2028-01-01', div_y=0.01))
    r = run.raw['rows'][0]
    want = _gap_call(100.0, 100.0, H, r['t_end'], r['r_end'], 0.01, 0.3)
    assert r['fv'] == pytest.approx(want, rel=0.005)
    assert not run.raw['integrity']
    ends = [SO.node_trace(run.terms, r, r['n'], j) for j in range(r['n'] + 1)]   # 종료 노드에서 판정
    assert max(abs(t['reconciliation']) for t in ends) <= 1e-12
    assert any(0 < t['pass_share'] < 1 for t in ends)


@pytest.mark.parametrize('H', [115.0, 130.0, 160.0])
def test_market_condition_touch_matches_the_weekly_barrier_option(H):
    # «가득일까지 한 번이라도» 는 격자 노드(주 단위)에서 관찰한다 — 연속 관찰 식에 주 단위 관찰 보정을 한 값과 대조
    run = calculate(case(so_rows=_target_row(H), so_mkt=2, d_mat='2028-01-01', div_y=0.01))
    r = run.raw['rows'][0]
    shifted = H*math.exp(0.5826*0.3*math.sqrt(r['dt']))
    want = _up_in_call(100.0, 100.0, shifted, r['t_end'], r['r_end'], 0.01, 0.3)
    assert r['fv'] == pytest.approx(want, rel=0.006)


def test_market_condition_bounds():
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0)]
    base = fv(case(so_ex=1, div_y=0.02, so_rows=rows))
    vals = {}
    for mkt in (1, 2):
        for H in (1.0, 130.0, 1e6):
            vals[mkt, H] = fv(case(so_ex=1, div_y=0.02, so_mkt=mkt, so_rows=[{**rows[0], 'target': H}]))
    assert vals[1, 1.0] == pytest.approx(base, rel=1e-12) and vals[2, 1.0] == pytest.approx(base, rel=1e-12)
    assert vals[1, 1e6] == 0 and vals[2, 1e6] == 0
    assert 0 < vals[1, 130.0] < vals[2, 130.0] < base        # 한 번이라도 닿으면 되는 쪽이 더 쉽다


def test_market_condition_after_measurement_keeps_the_trace_and_life():
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0, target=130.0)]
    for mkt in (1, 2):
        run = calculate(case(so_ex=2, so_mult=1.8, so_exit=0.05, so_nv=0.05, div_y=0.01, so_mkt=mkt, so_rows=rows,
                             grid_days=14.0))
        r = run.raw['rows'][0]
        free = calculate(case(so_ex=2, so_mult=1.8, so_exit=0.05, div_y=0.01, so_rows=[{**rows[0], 'target': None}],
                              grid_days=14.0)).raw['rows'][0]
        assert r['life'] == pytest.approx(free['life'], rel=1e-12)       # 기대존속기간은 가득된다고 보고 잰다
        worst = max(abs(SO.node_trace(run.terms, r, i, j)['reconciliation'])
                    for i in range(r['n']) for j in range(i + 1))
        assert worst <= 1e-10
        if mkt == 2:
            w = max(abs(SO.node_trace(run.terms, r, i, j).get('wait_reconciliation', 0.0))
                    for i in range(r['vest_step'] + 1) for j in range(i + 1))
            assert w <= 1e-10
        checks = {c['name']: c['passed'] for c in SO.checks(run.terms, run.raw)}
        assert checks['시장조건·비가득조건 (공정가치 ≤ 조건 없는 블랙-숄즈)'] and '블랙-숄즈 수렴 (유럽형)' not in checks
        assert not run.raw['integrity']
    trace = SO.node_trace(run.terms, run.raw['rows'][0], 0, 0)
    assert '비가득조건' in trace['rule']


@pytest.mark.parametrize('mkt', [0, 2])
def test_non_vesting_condition_scales_the_value_by_the_survival_to_vesting(mkt):
    # 비가득조건(문단 21A) — 가득일까지 구간마다 «그만두지 않을 확률» 을 곱한다
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0, target=130.0 if mkt else None)]
    plain = calculate(case(so_ex=1, div_y=0.02, so_mkt=mkt, so_rows=rows)).raw['rows'][0]
    r = calculate(case(so_ex=1, div_y=0.02, so_mkt=mkt, so_nv=0.1, so_rows=rows)).raw['rows'][0]
    assert r['nv_step'] == pytest.approx(1 - 0.9**r['dt'], rel=1e-14)
    assert r['fv'] == pytest.approx(plain['fv']*(1 - r['nv_step'])**r['vest_step'], rel=1e-12)


def test_modification_values_both_sides_with_the_market_condition():
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=0.9, target=130.0)]
    r = calculate(mod_case([dict(name='1차', K=80.0, grant_fv=20.0)], rows=rows, so_mkt=1)).raw['rows'][0]
    before = fv(case(d_base='2027-01-01', so_mkt=1, so_rows=rows))
    after = fv(case(d_base='2027-01-01', so_mkt=1, so_rows=rows, K0=80.0))
    assert r['fv_before'] == pytest.approx(before, rel=1e-12) and r['fv'] == pytest.approx(after, rel=1e-12)
    assert r['target'] == 130.0 and r['mkt'] == 1


def test_condition_inputs_reach_the_workpapers_and_review():
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0, target=130.0)]
    run = calculate(case(so_mkt=2, so_nv=0.05, so_rows=rows, grid_days=14.0))
    codes = {i.code for i in run.issues}
    assert {'so_market', 'so_nonvesting'} <= codes
    wb = _workbook(run, detail=True)
    A = wb['가정']
    assert A[_input(wb, '시장조건 판정')].value == SO.MKT_LABELS[2]
    assert A[_input(wb, '비가득조건 연간 중단율 (가득일까지)')].value == 0.05
    assert A[_cell(A, '회차', '1차', '시장조건 목표 주가(원)')].value == 130.0
    lattice = next(wb[n] for n in wb.sheetnames if n.startswith('격자 '))
    assert any(c.value and str(c.value).startswith('③ 목표 주가 미도달 상태 가치트리')
               for row in lattice.iter_rows() for c in row)
    basic = _workbook(run)
    summary = {r[0]: r[1] for r in basic['평가요약'].iter_rows(values_only=True) if r and r[0]}
    assert summary['시장조건 판정'] == SO.MKT_LABELS[2] and summary['비가득조건 연간 중단율 (가득일까지)'] == 0.05


def test_cost_schedule_is_day_proportional_and_stops_after_vesting():
    rows = [dict(name='1차', vest='2027-07-01', end='', qty=1000.0, keep=0.9),
            dict(name='2차', vest='2029-01-01', end='', qty=2000.0, keep=0.8)]
    run = calculate(case(so_rows=rows, so_ex=1))
    raw = run.raw
    one = raw['rows'][0]
    assert one['cost'] == pytest.approx(one['fv'] * 1000 * 0.9)
    s1 = [x for x in raw['schedule'] if x['name'] == '1차']
    assert [x['fy_end'] for x in s1] == ['2026-12-31', '2027-12-31']   # 가득된 뒤 결산일에는 줄이 없다
    days = (L.dt.date(2026, 12, 31) - L.dt.date(2026, 1, 1)).days
    span = (L.dt.date(2027, 7, 1) - L.dt.date(2026, 1, 1)).days
    assert s1[0]['share'] == pytest.approx(days / span)
    assert sum(x['expense'] for x in raw['schedule']) == pytest.approx(raw['cost'])
    assert run.summary['amounts_total']['so_cost'] == pytest.approx(raw['cost'])
    assert run.summary['amounts_per_share']['so_fair'] == pytest.approx(raw['fair'] / 3000)
    # 결산일이 3월 말인 회사
    run = calculate(case(so_rows=rows, so_fy_end='03-31'))
    assert run.raw['schedule'][0]['fy_end'] == '2026-03-31'


@pytest.mark.parametrize('over, words', [
    (dict(so_settle=2), '지원하지 않는 선택값'),
    (dict(so_settle=1, so_mod=1, d_base='2026-12-31'), '조건 변경 평가를 따로 하지 않습니다'),
    (dict(so_mod=1, d_base='2026-01-01', so_mod_rows=[dict(name='1차', grant_fv=10.0)]), '부여일보다 뒤'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[dict(name='1차', K=80.0)]), '부여일 1주당 공정가치'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[]), '조건 변경표에 없는 회차'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[dict(name='1차', grant_fv=10.0), dict(name='9차', grant_fv=1.0)]),
     '회차표에 없습니다'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[dict(name='1차', qty=900.0, grant_fv=10.0)]), '수량이 줄었습니다'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[dict(name='1차', K=0.0, grant_fv=10.0)]), '행사가격은 0보다'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[dict(name='1차', vest='2026-06-30', grant_fv=10.0)]),
     '조건 변경일보다 빠릅니다'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[dict(name='1차', price=1.0, grant_fv=10.0)]), '조건 변경표의 형식'),
    (dict(so_mod=1, d_base='2027-01-01', so_mod_rows=[dict(name='1차', end='2051-01-01', grant_fv=10.0)]),
     '1차 (변경 후): 격자가'),
    (dict(grid_days=1.0), '계산 한도'),
    (dict(so_settle=1, d_base='2026-12-31', so_paid=-1.0), '결제액은 0 이상'),
    (dict(so_paid=100.0), '현금결제형에서만'),
    (dict(so_ex=2, so_mult=1.0), '행사 배수'),
    (dict(so_exit=1.0), '퇴사율'),
    (dict(so_fy_end='12/31'), '결산일'),
    (dict(so_rows=[]), '한 줄 이상'),
    (dict(so_rows=[dict(name='1차', vest='2025-06-01', qty=1.0)]), '부여일보다 빠릅니다'),
    (dict(so_rows=[dict(name='1차', vest='2028-01-01', start='2027-01-01', qty=1.0)]), '가득일보다 빠릅니다'),
    (dict(so_rows=[dict(name='1차', vest='2028-01-01', end='2032-01-01', qty=1.0)]), '행사기간 종료일'),
    (dict(so_rows=[dict(name='1차', vest='2028-01-01', qty=0.0)]), '부여 수량'),
    (dict(so_rows=[dict(name='1차', vest='2028-01-01', qty=1.0, keep=0.0)]), '가득 예상 비율'),
    (dict(so_rows=[dict(name='A', vest='2028-01-01', qty=1.0), dict(name='A', vest='2029-01-01', qty=1.0)]), '겹칩니다'),
    (dict(so_rows=[dict(name='1차', vest='2028-01-01', qty=1.0, price=3)]), '형식'),
    (dict(so_exit=0.1, so_rows=[dict(name='1차', vest='2027-01-01', start='2028-01-01', qty=1.0)]), '행사 시작일이 가득일보다 늦은'),
    (dict(so_mkt=3), '지원하지 않는 선택값'),
    (dict(so_nv=1.0), '비가득조건 연간 중단율'),
    (dict(so_nv=-0.1), '비가득조건 연간 중단율'),
    (dict(so_mkt=1), '목표 주가를 넣은 회차가 없습니다'),
    (dict(so_mkt=1, so_rows=[dict(name='1차', vest='2028-01-01', qty=1.0, target=0.0)]), '목표 주가는 0보다'),
    (dict(so_rows=[dict(name='1차', vest='2028-01-01', qty=1.0, target=130.0)]), '«시장조건 판정» 이 «없음»'),
    (dict(so_mkt=2, d_base='2028-06-30', d_issue='2026-01-01',
          so_rows=[dict(name='1차', vest='2028-01-01', qty=1.0, target=130.0)]), '시장조건이 이미 판정됐습니다'),
    (dict(so_mkt=1, so_mod=1, d_base='2027-01-01', so_rows=[dict(name='1차', vest='2028-01-01', qty=1.0, target=130.0)],
          so_mod_rows=[dict(name='1차', vest='2027-01-01', grant_fv=10.0)]), '변경 후 가득일이 조건 변경일 이전'),
])
def test_input_checks_block_calculation(over, words):
    c = case(**over)
    errors = [i for i in inspect_case(c) if i.severity == 'error']
    assert any(words in i.message for i in errors), [i.message for i in errors]
    with pytest.raises(CaseError):
        calculate(c)


def test_review_prompts_follow_the_standard():
    codes = {i.code for i in calculate(case(so_ex=0)).issues}
    assert {'so_early_exercise', 'so_vesting', 'so_dividend'} <= codes
    codes = {i.code for i in calculate(case(d_base='2026-06-30', so_ex=2, so_mult=2.0, div_y=0.01,
                                            so_rows=[dict(name='1차', vest='2028-01-01', qty=1.0, keep=0.9)])).issues}
    assert {'so_measure_date', 'so_multiple'} <= codes and 'so_vesting' not in codes
    assert 'normalized' not in codes                     # 사채 전용 칸을 끈 것은 알릴 일이 아니다


def test_only_stock_option_judgment_topics_apply():
    values = case().effective()
    ids = {t['id'] for t in TOPICS if applicable(t, values)}
    assert ids == {'so_volatility', 'so_early_exercise', 'so_vesting', 'so_conditions', 'so_modification', 'so_settlement',
                   'fair_value_inputs', 'method_change'}
    cb = {**values, 'inst': 'CB', 'view': 'issuer'}
    assert not any(applicable(t, cb) for t in TOPICS if t['trigger'] == 'so')


def _label_row(ws, label, col=2):
    """B 열에서 항목 이름이 label 인 첫 줄."""
    return next(r for r in range(1, ws.max_row + 1) if ws.cell(r, col).value == label)


def _table_rows(ws, first_head, need=None):
    """B 열 머리가 first_head 이고 (need 가 있으면) 그 머리를 가진 표 — (머리 목록, [줄 번호])."""
    for h in range(1, ws.max_row + 1):
        if ws.cell(h, 2).value != first_head:
            continue
        heads = [ws.cell(h, c).value for c in range(2, ws.max_column + 1)]
        heads = heads[:heads.index(None)] if None in heads else heads
        if need is None or need in heads:
            break
    rows = []
    r = h + 1
    while ws.cell(r, 2).value not in (None, ''):
        rows.append(r)
        r += 1
    return heads, rows


def _cell(ws, first_head, row_label, head):
    """표(first_head)에서 B 열이 row_label 인 줄의 head 칸 좌표."""
    heads, rows = _table_rows(ws, first_head, head)
    r = next(x for x in rows if str(ws.cell(x, 2).value) == str(row_label))
    return f"{chr(ord('B') + heads.index(head))}{r}"


def _input(wb, label):
    """가정 시트 «1. 평가 입력값» 의 칸."""
    return f"C{_label_row(wb['가정'], label)}"


def _amount_rows(ws):
    """결과 시트 «1. 평가 금액» — {항목: (총액, 1주당, 주가 100 기준)}."""
    heads, rows = _table_rows(ws, '항목')
    return {ws.cell(r, 2).value: tuple(ws.cell(r, c).value for c in (3, 4, 5)) for r in rows}


def _records(ws, first_head, need=None):
    heads, rows = _table_rows(ws, first_head, need)
    return [dict(zip(heads, (ws.cell(r, 2 + k).value for k in range(len(heads))))) for r in rows]


def _workbook(run, **kw):
    with zipfile.ZipFile(io.BytesIO(export_bundle(run, **kw))) as z:
        return load_workbook(io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith('.xlsx')))))


def test_value_workpapers_carry_the_run_values():
    run = calculate(v2_workspace.sample_so_case())
    basic = _workbook(run, accounting=True)
    for name in ('평가요약', '회차별 공정가치', '회차표 입력', '블랙-숄즈 대조', '비용 배분', '회계처리', '금리곡선'):
        assert name in basic.sheetnames, name
    assert '행사방식' not in basic.sheetnames
    rows = list(basic['회차별 공정가치'].iter_rows(values_only=True))
    head = rows[0]
    # 엑셀 파일은 숫자를 유효숫자 16자리까지만 적는다 — 마지막 자리까지 같기를 요구하지 않는다
    for r, x in zip(rows[1:-1], run.summary['so_rows']):
        assert r[head.index('1주당 공정가치')] == pytest.approx(x['1주당 공정가치'], rel=1e-12)
    assert rows[-1][head.index('보상원가')] == pytest.approx(run.summary['amounts_total']['so_cost'])
    labels = [r[0] for r in basic['계약조건'].iter_rows(values_only=True)]
    assert '행사가격(원)' in labels and '현재 전환가액(원)' not in labels and '표면이자·우선배당률(연, %)' not in labels
    journal = [r for r in basic['회계처리'].iter_rows(values_only=True) if r and r[1] == '주식보상비용']
    assert sum(r[2] for r in journal) == pytest.approx(run.summary['amounts_total']['so_cost'])
    detail = _workbook(run, detail=True)
    # 상세 조서는 전환사채 상세 조서와 같은 구성 — 해설 · 가정 · 격자 · 결과
    assert detail.sheetnames[:3] == ['00_안내', '해설', '가정']
    for name in ('결과', '비용 배분', '금리곡선'):
        assert name in detail.sheetnames, name
    assert '평가요약' not in detail.sheetnames and '평가 입력값' not in detail.sheetnames
    lattices = [n for n in detail.sheetnames if n.startswith('격자 ')]
    assert len(lattices) == 3
    ws, r = detail[lattices[0]], run.raw['rows'][0]
    found = [c.value for row in ws.iter_rows() for c in row
             if isinstance(c.value, float) and abs(c.value - r['values'][0][0]) <= 1e-12*abs(r['values'][0][0])]
    assert found                                          # 격자 시점 0의 가치 = 회차 1주당 공정가치
    assert r['values'][0][0] == r['fv']
    assert detail['가정'][_input(detail, '측정기준일 주가(원)')].value == run.terms.S0
    assert detail['가정'][_input(detail, '변동성 σ (연)')].value == run.terms.sig
    lay = SOW.layout(r['n'])
    assert ws.cell(lay['value'], 3).value == r['fv'] and ws.cell(lay['stock'], 3).value == run.terms.S0
    assert ws['B1'].value == '날짜' and ws.cell(lay['stock'] - 1, 2).value == '하락 횟수 r ＼ 스텝'
    res = _amount_rows(detail['결과'])
    from valuation.service import AMOUNT_LABELS
    assert res[AMOUNT_LABELS['so_cost']][0] == pytest.approx(run.summary['amounts_total']['so_cost'])
    # 서식 — 전환사채 상세 조서와 같은 글꼴·제목 띠·항목 칸 색
    assert detail['가정']['B2'].font.name == 'KoPub돋움체 Medium' and detail['가정']['B2'].fill.fgColor.rgb.endswith('1F3864')
    assert ws['B3'].fill.fgColor.rgb.endswith('DCE6F1')


def test_screens_open_evaluate_and_show_calculations():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180)
    app.run()
    next(b for b in app.button if b.label == '주식선택권 합성사례로 시작하기').click().run()
    assert not app.exception, app.exception
    assert app.session_state.case.contract['inst'] == 'SO'
    assert '가득 회차' in [h.value for h in app.subheader]
    app.session_state['_workflow_stage'] = '평가·분석'
    app.run()
    next(b for b in app.button if b.label == '현재 입력으로 평가').click().run()
    assert not app.exception, app.exception
    assert [h.value for h in app.subheader][:2] == ['회차별 공정가치', '회계연도별 보상원가 배분']
    assert any(m.label.startswith('1주당 공정가치') for m in app.metric)
    for topic in ('노드 계산표', '노드 스케줄', '부트스트래핑', '비용 배분'):
        app.session_state['_workflow_stage'] = '계산내역'
        app.session_state['_calc_topic'] = topic
        app.run()
        assert not app.exception, (topic, app.exception)
    app.session_state['_workflow_stage'] = '조서 출력'
    app.run()
    choice = next(r for r in app.radio if r.label == '조서 구성')
    assert choice.options == ['기본 값 조서', '상세 계산 값 조서', '상세 계산 수식 조서']
    app.session_state['_workflow_stage'] = '입력·시장자료'
    app.session_state['_input_area'] = '주가·변동성·금리 자료'
    app.run()
    tools = next(s for s in app.selectbox if s.label == '시장자료 도구')
    assert tools.options == ['변동성 산출', '기준일 주가 조회', '금리곡선 불러오기']


def test_screens_take_market_and_non_vesting_conditions():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180)
    app.run()
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0, target=130.0)]
    for mkt in (1, 2):
        app.session_state['case'] = case(so_mkt=mkt, so_nv=0.05, so_rows=rows, grid_days=14.0)
        app.session_state['_workflow_stage'] = '입력·시장자료'
        app.run()
        assert not app.exception, app.exception
        assert '시장조건·비가득조건' in [h.value for h in app.subheader]
        assert next(s for s in app.selectbox if s.label == '시장조건 판정').value == mkt
        assert any(n.label.startswith('비가득조건 연간 중단율') for n in app.number_input)
        app.session_state['_workflow_stage'] = '평가·분석'
        app.run()
        next(b for b in app.button if b.label == '현재 입력으로 평가').click().run()
        assert not app.exception, app.exception
        run = app.session_state.run
        r = run.raw['rows'][0]
        assert r['mkt'] == mkt and r['nv_step'] > 0
        app.session_state['_workflow_stage'] = '계산내역'
        for topic in ('노드 계산표', '노드 스케줄'):
            app.session_state['_calc_topic'] = topic
            app.run()
            assert not app.exception, (mkt, topic, app.exception)


def test_new_stock_option_case_from_the_sidebar():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180)
    app.run()
    app.text_input[0].set_value('가상 부여 건')
    next(s for s in app.selectbox if s.label == '평가 상품').set_value('SO')
    next(b for b in app.button if b.label == '빈 입력안 만들기').click().run()
    assert not app.exception, app.exception
    c = app.session_state.case
    assert c.contract['inst'] == 'SO' and c.method['so_ex'] == 1
    missing = {i.field for i in inspect_case(c) if i.code == 'required'}
    assert {'S0', 'K0', 'sig'} <= missing


def test_exercise_at_measurement_date_still_exports():
    # 부여일 = 가득일 = 측정기준일이고 주가가 이미 행사 배수를 넘으면 뿌리에서 행사한다 — 기대존속기간 0년.
    # 블랙-숄즈 대조표가 0으로 나누지 않고 내재가치를 싣는다 (자동 검토 지적).
    c = case(S0=250.0, K0=100.0, so_ex=2, so_mult=2.0,
             so_rows=[dict(name='1차', vest='2026-01-01', end='', qty=10.0, keep=1.0)])
    run = calculate(c)
    r = run.raw['rows'][0]
    assert r['life'] == 0 and r['fv'] == pytest.approx(150.0)
    assert run.raw['schedule'][0]['share'] == 1.0            # 가득기간이 없으면 첫 결산일에 전액
    wb = _workbook(run, accounting=True)
    rows = list(wb['블랙-숄즈 대조'].iter_rows(values_only=True))
    life_row = next(x for x in rows if x[1] == '기대존속기간')
    assert life_row[4] is None and life_row[8] == pytest.approx(150.0)
    wb = _workbook(run, detail=True, accounting=True)               # 상세 조서는 결과 시트의 «3. 블랙-숄즈 대조»
    life = next(x for x in _records(wb['결과'], '회차', '만기 구분') if x['만기 구분'] == '기대존속기간')
    assert life['d1'] is None and life['블랙-숄즈 1주당(원)'] == pytest.approx(150.0)


# ── 2단계 ① 조건 변경 · ② 현금결제형 ─────────────────────────────────────────

ONE = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=0.9)]


def mod_case(mods, rows=ONE, **over):
    return case(so_mod=1, d_base='2027-01-01', so_rows=rows, so_mod_rows=mods, **over)


def test_repricing_measures_the_increment_on_the_modification_date():
    run = calculate(mod_case([dict(name='1차', K=80.0, grant_fv=20.0)]))
    r = run.raw['rows'][0]
    # 변경 전·후 값 = 같은 날·같은 시장자료로 따로 잰 부여일 평가 값 (문단 27, 부록 나43⑴)
    before = fv(case(d_base='2027-01-01', so_rows=ONE))
    after = fv(case(d_base='2027-01-01', so_rows=ONE, K0=80.0))
    assert r['fv_before'] == pytest.approx(before, rel=1e-12) and r['fv'] == pytest.approx(after, rel=1e-12)
    assert r['K'] == 80.0 and r['K_before'] == 100.0
    assert r['incr'] == pytest.approx(after - before) and r['incr'] > 0
    assert r['grant_cost'] == pytest.approx(20.0*1000*0.9)            # 당초 보상원가는 그대로 인식한다
    assert r['fair'] == pytest.approx(r['incr']*1000) and r['cost'] == pytest.approx(r['incr']*1000*0.9)
    total = run.summary['amounts_total']
    assert list(total) == ['so_incr_fair', 'so_incr_cost'] and total['so_incr_cost'] == pytest.approx(r['cost'])
    assert run.raw['integrity'] == []
    sched = run.raw['schedule']
    assert [(x['fy_end'], x['part']) for x in sched] == [
        ('2026-12-31', '당초'), ('2027-12-31', '당초'), ('2028-12-31', '당초'), ('2027-12-31', '증분'), ('2028-12-31', '증분')]
    inc = [x for x in sched if x['part'] == '증분']
    assert inc[0]['share'] == pytest.approx(364/365)                    # 증분은 변경일부터 가득일까지
    assert sum(x['expense'] for x in sched) == pytest.approx(r['grant_cost'] + r['cost'])
    assert 'so_mod_grant_fv' in {i.code for i in run.issues} and 'so_measure_date' not in {i.code for i in run.issues}
    check = next(c for c in run.summary['checks'] if c['name'].startswith('증분 공정가치'))
    assert check['passed']


def test_unfavourable_change_adds_nothing():
    run = calculate(mod_case([dict(name='1차', K=120.0, grant_fv=20.0)]))
    r = run.raw['rows'][0]
    assert r['fv'] < r['fv_before'] and r['incr'] == 0 and r['cost'] == 0
    assert all(x['expense'] == 0 for x in run.raw['schedule'] if x['part'] == '증분')   # 줄은 있고 금액은 0
    assert 'so_mod_no_increment' in {i.code for i in run.issues}


def test_added_options_are_valued_at_the_modified_value():
    run = calculate(mod_case([dict(name='1차', qty=1200.0, grant_fv=20.0)]))
    r = run.raw['rows'][0]
    assert r['incr'] == pytest.approx(0.0, abs=1e-12)                 # 다른 조건은 그대로
    assert r['qty_before'] == 1000 and r['qty_added'] == 200
    assert r['fair'] == pytest.approx(r['fv']*200) and r['cost'] == pytest.approx(r['fv']*200*0.9)
    assert run.raw['qty'] == 1000
    assert 'so_mod_no_increment' not in {i.code for i in run.issues}


def test_shorter_vesting_catches_up_without_rewriting_past_years():
    rows = [dict(name='1차', vest='2029-01-01', end='', qty=1000.0, keep=1.0)]
    run = calculate(mod_case([dict(name='1차', vest='2027-06-30', grant_fv=20.0)], rows=rows))
    first = [x for x in run.raw['schedule'] if x['part'] == '당초']
    span = (L.dt.date(2029, 1, 1) - L.dt.date(2026, 1, 1)).days
    assert first[0]['fy_end'] == '2026-12-31' and first[0]['share'] == pytest.approx(364/span)   # 지난 결산은 당초 추정
    assert first[1]['fy_end'] == '2027-12-31' and first[1]['cum'] == pytest.approx(20000.0)    # 줄인 가득일로 따라잡음
    assert len(first) == 2
    assert run.raw['rows'][0]['start'] == '2027-06-30'                # 가득일부터 행사하던 회차는 새 가득일부터
    late = [dict(name='1차', vest='2028-01-01', start='2028-07-01', end='', qty=1000.0, keep=1.0)]
    run = calculate(mod_case([dict(name='1차', vest='2027-06-30', grant_fv=20.0)], rows=late))
    assert run.raw['rows'][0]['start'] == '2028-07-01'                # 따로 정한 행사 시작일은 그대로
    assert 'so_mod_vest_shorter' in {i.code for i in run.issues}


def test_longer_vesting_keeps_the_original_cost_on_the_original_date():
    run = calculate(mod_case([dict(name='1차', K=80.0, vest='2029-01-01', grant_fv=20.0)]))
    first = [x for x in run.raw['schedule'] if x['part'] == '당초']
    inc = [x for x in run.raw['schedule'] if x['part'] == '증분']
    assert first[-1]['fy_end'] == '2028-12-31' and first[-2]['share'] == pytest.approx(729/730)   # 당초 가득일 2028-01-01
    assert inc[-1]['fy_end'] == '2029-12-31' and inc[0]['share'] == pytest.approx(364/(365 + 366))
    assert 'so_mod_vest_longer' in {i.code for i in run.issues}


def test_extending_the_exercise_period_past_the_original_expiry():
    # 회차 행사 종료일 빈칸 = 행사기간 종료일 2031-01-01. 연장한 종료일은 그보다 늦어도 되고, 변경 전은 그대로다 (자동 검토 지적)
    run = calculate(mod_case([dict(name='1차', end='2032-01-01', grant_fv=20.0)]))
    r = run.raw['rows'][0]
    assert r['end_before'] == '2031-01-01' and r['end'] == '2032-01-01'
    before = fv(case(d_base='2027-01-01', so_rows=ONE))
    after = fv(case(d_base='2027-01-01', d_mat='2032-01-01', so_rows=ONE))
    assert r['fv_before'] == pytest.approx(before, rel=1e-12) and r['fv'] == pytest.approx(after, rel=1e-12)
    assert r['incr'] > 0 and run.raw['integrity'] == []


def test_increment_after_vesting_is_recognised_at_once():
    rows = [dict(name='1차', vest='2026-06-30', end='', qty=1000.0, keep=1.0)]
    run = calculate(mod_case([dict(name='1차', K=80.0, grant_fv=20.0)], rows=rows))
    inc = [x for x in run.raw['schedule'] if x['part'] == '증분']
    assert len(inc) == 1 and inc[0]['fy_end'] == '2027-12-31' and inc[0]['share'] == 1.0
    assert inc[0]['expense'] == pytest.approx(run.raw['rows'][0]['cost'])
    first = [x for x in run.raw['schedule'] if x['part'] == '당초']
    assert len(first) == 1 and first[0]['fy_end'] == '2026-12-31'


def cash_case(d_base='2026-12-31', **over):
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=0.9),
            dict(name='2차', vest='2026-06-30', end='', qty=500.0, keep=1.0)]
    return case(so_settle=1, d_base=d_base, so_rows=rows, **over)


def test_cash_settled_liability_is_remeasured_at_year_end():
    run = calculate(cash_case(so_prev_liab=5000.0))
    one, two = run.raw['rows']
    assert one['service'] == pytest.approx(364/730) and two['service'] == 1.0
    assert one['fv'] == pytest.approx(fv(case(d_base='2026-12-31', so_rows=ONE)), rel=1e-12)
    assert one['liab'] == pytest.approx(one['fv']*1000*0.9*364/730)
    assert run.raw['liab'] == pytest.approx(one['liab'] + two['liab'])
    assert run.raw['expense'] == pytest.approx(run.raw['liab'] - 5000.0)
    assert run.summary['so_cash']['당기 비용'] == pytest.approx(run.raw['expense'])
    assert list(run.summary['amounts_total']) == ['so_fair', 'so_liab']
    assert run.raw['integrity'] == []
    codes = {i.code for i in run.issues}
    assert 'so_cash_prev' not in codes and 'so_cash_date' not in codes and 'so_measure_date' not in codes
    codes = {i.code for i in calculate(cash_case(d_base='2026-11-30')).issues}
    assert {'so_cash_prev', 'so_cash_date'} <= codes
    # 당기 중 결제 — 부채 증감만 보면 결제한 4,000원만큼 비용이 줄어든다 (자동 검토 지적)
    paid = calculate(cash_case(so_prev_liab=5000.0, so_paid=4000.0))
    assert paid.raw['expense'] == pytest.approx(run.raw['expense'] + 4000.0)
    assert paid.summary['so_cash']['당기 현금 결제액'] == 4000.0
    wb = _workbook(paid, accounting=True)
    rows = list(wb['회계처리'].iter_rows(values_only=True))
    assert ('당기 중', '주식보상부채', 4000.0, '현금', 4000.0) in [r[:5] for r in rows]
    # 당기 중 모두 결제 — 남은 수량 0, 부채 0, 비용 = 결제액 − 전기말 부채 (자동 검토 지적)
    done = cash_case(so_prev_liab=10000.0, so_paid=12000.0)
    for r in done.contract['so_rows']:
        r['qty'] = 0.0
    gone = calculate(done)
    assert gone.raw['liab'] == 0 and gone.raw['expense'] == pytest.approx(2000.0)
    assert gone.summary['amounts_per_share'] == {'so_fair': 0.0, 'so_liab': 0.0}
    assert '부채 재측정' in _workbook(gone, accounting=True).sheetnames
    detail = _workbook(gone, detail=True, accounting=True)
    assert any(c.value == '2. 회차별 공정가치와 주식보상부채' for row in detail['결과'].iter_rows() for c in row)
    assert any('부여 수량' in i.message for i in inspect_case(case(so_rows=[dict(name='1차', vest='2028-01-01', qty=0.0)])))


def test_mode_workpapers():
    wb = _workbook(calculate(mod_case([dict(name='1차', K=80.0, grant_fv=20.0)])), detail=True, accounting=True)
    for name in ('해설', '가정', '격자 1차 변경 후', '격자 1차 변경 전', '결과', '비용 배분', '회계처리'):
        assert name in wb.sheetnames, name
    assert wb['가정'][_cell(wb['가정'], '회차', '1차', '부여일 1주당 공정가치(원)')].value == 20.0
    assert any(c.value == '2. 회차별 조건 변경 전·후 공정가치와 증분' for row in wb['결과'].iter_rows() for c in row)
    basic = _workbook(calculate(mod_case([dict(name='1차', K=80.0, grant_fv=20.0)])), accounting=True)
    assert '조건 변경' in basic.sheetnames and '회차별 공정가치' not in basic.sheetnames
    run = calculate(cash_case(so_prev_liab=5000.0))
    wb = _workbook(run, accounting=True)
    assert '부채 재측정' in wb.sheetnames and '조건 변경' not in wb.sheetnames
    journal = [r for r in wb['회계처리'].iter_rows(values_only=True) if r and r[1] == '주식보상비용']
    assert sum(r[2] for r in journal) == pytest.approx(run.raw['expense'])


def test_screens_switch_between_grant_modification_and_cash():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180)
    app.run()
    app.session_state['case'] = mod_case([dict(name='1차', K=80.0, grant_fv=20.0)])
    app.run()
    assert not app.exception, app.exception
    assert '조건 변경' in [h.value for h in app.subheader]
    app.session_state['_workflow_stage'] = '평가·분석'
    app.run()
    next(b for b in app.button if b.label == '현재 입력으로 평가').click().run()
    assert not app.exception, app.exception
    assert [h.value for h in app.subheader][:2] == ['회차별 조건 변경 전·후 공정가치', '회계연도별 보상원가 배분']
    stale = cash_case(so_prev_liab=5000.0)
    stale.contract['so_mod'] = 1                 # 조건 변경을 켠 채 현금결제형으로 바꾼 입력 (자동 검토 지적)
    app.session_state['case'] = stale
    app.session_state['_workflow_stage'] = '입력·시장자료'
    app.run()
    assert not app.exception, app.exception
    assert '조건 변경' not in [h.value for h in app.subheader]
    labels = [n.label for n in app.number_input]
    assert '당기 현금 결제액(원)' in labels and '전기말 주식보상부채(원, -1은 없음)' in labels
    app.session_state['_workflow_stage'] = '평가·분석'
    app.run()
    next(b for b in app.button if b.label == '현재 입력으로 평가').click().run()
    assert not app.exception, app.exception
    assert app.session_state.case.contract['so_mod'] == 0
    assert [h.value for h in app.subheader][:2] == ['회차별 공정가치와 부채', '결산일 부채와 당기 비용']
    app.session_state['_workflow_stage'] = '조서 출력'
    app.run()
    next(r for r in app.radio if r.label == '조서 구성').set_value('상세 계산 수식 조서').run()
    next(b for b in app.button if b.label == '조서 생성').click().run()
    assert not app.exception, app.exception
    with zipfile.ZipFile(io.BytesIO(app.session_state.bundle)) as z:
        assert 'formula_review.xlsx' in z.namelist()

    # 결제액을 넣은 현금결제형을 주식결제형으로 바꾼 입력 (자동 검토 지적) — 숨은 결제액이 평가를 막지 않는다
    back = case(so_rows=ONE)
    back.method['so_paid'] = 4000.0
    app.session_state['case'] = back
    app.session_state['_workflow_stage'] = '입력·시장자료'
    app.run()
    assert '당기 현금 결제액(원)' not in [n.label for n in app.number_input]
    app.session_state['_workflow_stage'] = '평가·분석'
    app.run()
    next(b for b in app.button if b.label == '현재 입력으로 평가').click().run()
    assert not app.exception, app.exception
    assert app.session_state.case.method['so_paid'] == 0.0
    assert [h.value for h in app.subheader][:2] == ['회차별 공정가치', '회계연도별 보상원가 배분']

# ── 2단계 ③ 수식 조서 — 상세 값 조서와 같은 시트·같은 칸, 다시 계산하면 같은 값 ─────────────────────

LIBRE = shutil.which('libreoffice') or shutil.which('soffice')
THREE = [dict(name='1차', vest='2027-01-01', end='2029-01-01', qty=1000.0, keep=0.9),
         dict(name='2차', vest='2028-01-01', end='', qty=2000.0, keep=0.8)]
TARGETS = [{**THREE[0], 'target': 125.0}, {**THREE[1], 'target': 140.0}]
FORMULA_CASES = {
    '부여일 · 행사 배수 · 퇴사율': lambda: case(so_ex=2, so_mult=1.8, so_exit=0.05, div_y=0.01, d_mat='2029-07-01',
                                       so_rows=THREE, grid_days=14.0),
    '부여일 · 최적 행사 · 배당': lambda: case(so_ex=1, div_y=0.03, d_mat='2029-07-01', so_rows=THREE, grid_days=14.0),
    '조건 변경': lambda: mod_case([dict(name='1차', K=80.0, qty=1200.0, vest='2028-07-01', grant_fv=20.0)],
                              grid_days=14.0, so_ex=1, div_y=0.02),
    '현금결제형': lambda: cash_case(so_prev_liab=5000.0, so_paid=1500.0, grid_days=14.0, so_ex=2, so_mult=2.0),
    '현금결제형 · 모두 결제': lambda: _settled(cash_case(so_prev_liab=10000.0, so_paid=12000.0, grid_days=14.0)),
    # 가득된 선택권의 행사기간 연장 — 주가가 행사 배수를 넘어 변경 전·후 모두 바로 행사하므로 처음 증분 0
    '조건 변경 · 증분 0': lambda: mod_case([dict(name='1차', end='2032-01-01', grant_fv=20.0)], grid_days=14.0, so_ex=2,
                                       so_mult=2.0, S0=250.0,
                                       rows=[dict(name='1차', vest='2026-06-30', end='', qty=1000.0, keep=1.0)]),
    # 시장조건·비가득조건 — 목표 주가가 걸친 노드의 비율, «닿지 않은 상태» 격자, 가득 전 중단 확률까지 수식으로
    '시장조건 가득일 판정 · 비가득조건': lambda: case(so_ex=1, div_y=0.02, d_mat='2029-07-01', so_rows=TARGETS, so_mkt=1,
                                          so_nv=0.05, grid_days=14.0),
    '시장조건 한 번 도달 · 행사 배수 · 퇴사': lambda: case(so_ex=2, so_mult=1.8, so_exit=0.05, d_mat='2029-07-01',
                                             so_rows=TARGETS, so_mkt=2, grid_days=14.0),
    '시장조건 가득일 = 행사 종료일': lambda: case(d_mat='2029-07-01', so_mkt=1, grid_days=7.0,
                                       so_rows=[dict(name='1차', vest='2028-01-01', end='2028-01-01', qty=1000.0,
                                                     keep=1.0, target=120.0)]),
    '조건 변경 · 시장조건 한 번 도달 · 비가득조건': lambda: mod_case(
        [dict(name='1차', K=80.0, grant_fv=20.0)], grid_days=14.0, so_ex=1, so_mkt=2, so_nv=0.05,
        rows=[dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=0.9, target=130.0)]),
    '현금결제형 · 가득일 판정': lambda: _targeted(cash_case(so_prev_liab=5000.0, grid_days=14.0, so_mkt=1)),
}


def _targeted(c):
    c.contract['so_rows'][0]['target'] = 120.0      # 가득일이 결산일 뒤인 회차만
    return c


def _settled(c):
    for r in c.contract['so_rows']:
        r['qty'] = 0.0
    return c


def _xlsx(run, **kw):
    with zipfile.ZipFile(io.BytesIO(export_bundle(run, **kw))) as z:
        return z.read(next(n for n in z.namelist() if n.endswith('.xlsx')))


def _recalc(data):
    from valuation.xlsx_validation import recalculate
    return load_workbook(io.BytesIO(recalculate(data)), data_only=True)


def test_formula_workbook_has_live_lattices_and_only_estimates_open():
    from valuation.xlsx_validation import inspect_workbook
    run = calculate(FORMULA_CASES['조건 변경']())
    data = _xlsx(run, detail=True, formula=True, accounting=True)
    assert not inspect_workbook(data)['errors']
    wb = load_workbook(io.BytesIO(data))
    value = load_workbook(io.BytesIO(_xlsx(run, detail=True, accounting=True)))
    assert wb.sheetnames == value.sheetnames                       # 값 조서와 같은 시트
    ws = wb['격자 1차 변경 후']
    lay = SOW.layout(run.raw['rows'][0]['n'])
    h = lay['head']
    assert ws.cell(h['상승계수 u'], 3).value.startswith('=EXP(')
    assert all(isinstance(c.value, str) and c.value.startswith('=') for c in ws[lay['value']][2:5])
    assert ws.cell(lay['stock'], 3).value.startswith("='가정'!")
    # 금리·날짜·행사 일정은 앱이 정한 값 (엑셀에서 고치지 않는다)
    assert isinstance(ws.cell(h['무위험 선도이자율'], 3).value, float)
    assert ws.cell(h['행사 가능 (1=예)'], 3).value in (0, 1)
    editable = {(s.title, c.coordinate) for s in wb for row in s.iter_rows() for c in row
                if c.protection.locked is False}
    A = wb['가정']
    labels = ('측정기준일 주가(원)', '변동성 σ (연)', '배당수익률 (연, 연속복리)', '행사 배수 (주가 ÷ 행사가격)',
              '가득 뒤 연간 퇴사율', '비가득조건 연간 중단율 (가득일까지)')
    assert editable == ({('가정', _input(wb, x)) for x in labels} | {('가정', _cell(A, '회차', '1차', '가득 예상 비율'))} |
                        {('가정', _cell(A, '회차', '1차', '부여일 1주당 공정가치(원)'))})
    assert all(s.protection.sheet for s in wb)
    assert wb['계산대사']['C2'].value.startswith("='결과'!")


def test_formula_workbook_matches_tranche_names_like_the_engine():
    # 회차 이름 앞뒤 빈칸 — 조건 변경 계산은 떼고 맞추므로 수식 조서도 같아야 한다 (자동 검토 지적)
    rows = [dict(name=' 1차 ', vest='2028-01-01', end='', qty=1000.0, keep=0.9)]
    run = calculate(mod_case([dict(name='1차', K=80.0, grant_fv=20.0)], rows=rows, grid_days=14.0))
    wb = load_workbook(io.BytesIO(_xlsx(run, detail=True, formula=True)))
    want = _cell(wb['가정'], '회차', '1차', '부여일 1주당 공정가치(원)')
    got = wb['결과'][_cell(wb['결과'], '회차', ' 1차 ', '부여일 1주당 공정가치')].value
    assert got == f"='가정'!${want[0]}${want[1:]}"
    # 숫자 회차명 — 불러온 평가파일에서 생길 수 있다 (자동 검토 지적)
    num = [dict(name=1, vest='2028-01-01', end='', qty=1000.0, keep=0.9)]
    for c in (mod_case([dict(name=1, K=80.0, grant_fv=20.0)], rows=num, grid_days=14.0),
              case(so_rows=num, grid_days=14.0), case(so_settle=1, d_base='2026-12-31', so_rows=num, grid_days=14.0)):
        wb = load_workbook(io.BytesIO(_xlsx(calculate(c), detail=True, formula=True, accounting=True)))
        assert any(n.startswith('격자 1') for n in wb.sheetnames)


@pytest.mark.skipif(not LIBRE, reason='리브레오피스 없음')
@pytest.mark.parametrize('name', list(FORMULA_CASES))
def test_formula_workbook_recalculates_to_the_value_workbook(name):
    run = calculate(FORMULA_CASES[name]())
    value = load_workbook(io.BytesIO(_xlsx(run, detail=True, accounting=True)))
    live = _recalc(_xlsx(run, detail=True, formula=True, accounting=True))
    bad, count = [], 0
    for ws in value:
        if ws.title in ('00_안내', '계산대사'):                    # 출력 형식 안내 · 수식 대사 표는 조서마다 다르다
            continue
        other = live[ws.title]
        for row in ws.iter_rows():
            for c in row:
                a, b = c.value, other[c.coordinate].value
                if isinstance(a, (int, float)) and not isinstance(a, bool):
                    count += 1
                    if not (isinstance(b, (int, float)) and abs(a - b) <= 1e-9*max(1.0, abs(a))):
                        bad.append((ws.title, c.coordinate, a, b))
                elif a != b and not (a is None and b == ''):
                    bad.append((ws.title, c.coordinate, a, b))
    assert count > 10000 and not bad, bad[:10]
    check = list(live['계산대사'].iter_rows(min_row=2, values_only=True))
    assert check and all(abs(r[3]) <= 1e-9*max(1.0, abs(r[1])) for r in check), check


@pytest.mark.skipif(not LIBRE, reason='리브레오피스 없음')
def test_formula_workbook_follows_changed_estimates_like_the_app():
    c = FORMULA_CASES['부여일 · 행사 배수 · 퇴사율']()
    wb = load_workbook(io.BytesIO(_xlsx(calculate(c), detail=True, formula=True)))
    edits = {'측정기준일 주가(원)': 110.0, '변동성 σ (연)': 0.35, '행사 배수 (주가 ÷ 행사가격)': 2.0, '가득 뒤 연간 퇴사율': 0.03}
    for k, v in edits.items():
        wb['가정'][_input(wb, k)].value = v
    wb['가정'][_cell(wb['가정'], '회차', '2차', '가득 예상 비율')].value = 0.7
    out = io.BytesIO()
    wb.save(out)
    live = _recalc(out.getvalue())
    c2 = copy.deepcopy(c)
    c2.market.update(S0=110.0, sig=0.35)
    c2.method.update(so_mult=2.0, so_exit=0.03)
    c2.contract['so_rows'][1]['keep'] = 0.7
    app = calculate(c2)
    from valuation.service import AMOUNT_LABELS
    lab = _amount_rows(live['결과'])
    for key, want in app.summary['amounts_total'].items():
        assert lab[AMOUNT_LABELS[key]][0] == pytest.approx(want, rel=1e-10), key
        assert lab[AMOUNT_LABELS[key]][2] == pytest.approx(app.summary['amounts_100'][key], rel=1e-10), key
    sched = _records(live['비용 배분'], '결산일')
    assert [x['당기 비용'] for x in sched] == pytest.approx([x['당기 비용'] for x in app.summary['so_schedule']], rel=1e-10)


@pytest.mark.skipif(not LIBRE, reason='리브레오피스 없음')
@pytest.mark.parametrize('name', ['시장조건 가득일 판정 · 비가득조건', '시장조건 한 번 도달 · 행사 배수 · 퇴사'])
def test_formula_workbook_follows_changed_condition_inputs_like_the_app(name):
    c = FORMULA_CASES[name]()
    wb = load_workbook(io.BytesIO(_xlsx(calculate(c), detail=True, formula=True)))
    A = wb['가정']
    A[_input(wb, '측정기준일 주가(원)')].value = 115.0
    A[_input(wb, '변동성 σ (연)')].value = 0.4
    A[_input(wb, '비가득조건 연간 중단율 (가득일까지)')].value = 0.08
    out = io.BytesIO()
    wb.save(out)
    live = _recalc(out.getvalue())
    c2 = copy.deepcopy(c)
    c2.market.update(S0=115.0, sig=0.4)
    c2.method['so_nv'] = 0.08
    app = calculate(c2)
    from valuation.service import AMOUNT_LABELS
    lab = _amount_rows(live['결과'])
    for key, want in app.summary['amounts_total'].items():
        assert lab[AMOUNT_LABELS[key]][0] == pytest.approx(want, rel=1e-10), key


@pytest.mark.skipif(not LIBRE, reason='리브레오피스 없음')
def test_formula_workbook_keeps_rows_for_amounts_that_start_at_zero():
    # 처음에 0인 증분·결제액도 비용 배분·분개에 줄이 있어, 엑셀에서 입력을 바꾸면 따라온다 (자동 검토 지적)
    c = FORMULA_CASES['조건 변경 · 증분 0']()
    assert calculate(c).raw['rows'][0]['incr'] == 0
    wb = load_workbook(io.BytesIO(_xlsx(calculate(c), detail=True, formula=True, accounting=True)))
    wb['가정'][_input(wb, '측정기준일 주가(원)')].value = 150.0     # 주가를 낮추면 바로 행사하지 않아 연장의 증분이 생긴다
    wb['가정'][_cell(wb['가정'], '회차', '1차', '부여일 1주당 공정가치(원)')].value = 10.0
    out = io.BytesIO()
    wb.save(out)
    live = _recalc(out.getvalue())
    c2 = copy.deepcopy(c)
    c2.market['S0'] = 150.0
    c2.contract['so_mod_rows'][0]['grant_fv'] = 10.0
    app = calculate(c2)
    assert app.raw['cost'] > 1000
    sched = _records(live['비용 배분'], '결산일')
    assert [x['당기 비용'] for x in sched] == pytest.approx([x['당기 비용'] for x in app.summary['so_schedule']], rel=1e-10)
    journal = {x['결산일']: x['차변 금액(원)'] for x in _records(live['회계처리'], '결산일') if x['차변 계정'] == '주식보상비용'}
    want = {}
    for x in app.summary['so_schedule']:
        want[x['결산일']] = want.get(x['결산일'], 0.0) + x['당기 비용']
    assert journal == pytest.approx(want, rel=1e-10)
    # 현금결제형 — 처음 결제액 0, 엑셀에서 3,000원을 넣는다
    c = cash_case(grid_days=14.0, so_prev_liab=5000.0)
    wb = load_workbook(io.BytesIO(_xlsx(calculate(c), detail=True, formula=True, accounting=True)))
    wb['가정'][_input(wb, '당기 현금 결제액(원)')].value = 3000.0
    out = io.BytesIO()
    wb.save(out)
    rows = {r[1]: r[1:] for r in _recalc(out.getvalue())['회계처리'].iter_rows(values_only=True) if r and r[1]}
    assert rows['당기 중'][1:5] == ('주식보상부채', 3000.0, '현금', 3000.0)
    c.method['so_paid'] = 3000.0
    assert rows[c.method['d_base']][2] == pytest.approx(calculate(c).raw['expense'], rel=1e-10)


# ── 계산표 — 전환사채·상환전환우선주와 같은 화면, 셀을 누르면 계산식과 참조 노드 ─────────────────────

@pytest.mark.parametrize('mkt', [0, 1, 2])
def test_calculation_grid_reads_the_engine_lattice(mkt, monkeypatch):
    import json
    from valuation.calculation_view import so_node_values, so_schedule_values, bootstrap_values, worksheet_html
    rows = [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0, target=130.0 if mkt else None)]
    run = calculate(case(so_ex=2, so_mult=1.8, so_exit=0.05, so_nv=0.05, div_y=0.01, so_mkt=mkt, so_rows=rows,
                         grid_days=14.0))
    r = run.raw['rows'][0]
    monkeypatch.setattr(SO, 'value_row', lambda *a, **k: pytest.fail('화면이 다시 평가했습니다'))
    view = so_node_values(run, r, 30, 61)
    assert {n['i'] for n in view['nodes'].values()} == set(range(30, 63))     # 마지막 열이 참조하는 다음 열까지
    for key, n in view['nodes'].items():
        i, down = n['i'], n['down']
        assert key == f'{i},{down}' and n['j'] == i - down
        assert n['value'] == r['values'][i][down] and n['stock'] == r['prices'][i][down]
        assert n['exercise'] == r['exercise'][i][down] and (n['kind'] == '행사') == n['exercise']
        up = view['nodes'].get(f"{n['up'][0]},{n['up'][0] - n['up'][1]}") if n['up'] else None
        dn = view['nodes'].get(f"{n['dn'][0]},{n['dn'][0] - n['dn'][1]}") if n['dn'] else None
        if up and dn:                     # 화면 계산식 C = 할인계수 × [p × V↑ + (1 − p) × V↓]
            s = view['steps'][i]
            assert (up['down'], dn['down']) == (down, down + 1)
            assert n['cont'] == pytest.approx(s['df']*(s['q']*up['value'] + (1 - s['q'])*dn['value']), rel=1e-12)
            if mkt == 2 and n['wait'] is not None and i < r['vest_step'] and n['stock'] < 130.0:
                assert n['wait'] == pytest.approx((1 - s['nv'])*s['df']*(s['q']*up['wait'] + (1 - s['q'])*dn['wait']),
                                                  rel=1e-12)
    page = worksheet_html(view)
    payload = json.loads(page.split('type="application/json">')[1].split('</script>')[0])
    assert payload['nodes']['30,0']['value'] == r['values'][30][0] and 'NaN' not in page
    if mkt == 1:
        judged = [n for n in so_node_values(run, r, r['vest_step'], r['vest_step'])['nodes'].values()
                  if n['i'] == r['vest_step']]
        assert any(0 < n['share'] < 1 for n in judged) and any(n['kind'] == '소멸 (시장조건)' for n in judged)
    sched = so_schedule_values(run, r)
    assert [s['q'] for s in sched['steps'][:-1]] == r['p'] and [s['rf'] for s in sched['steps'][:-1]] == r['fwd']
    assert sum(s['before_vest'] for s in sched['steps']) == r['vest_step']
    assert sum(not s['can_ex'] for s in sched['steps']) == r['ex_step']
    assert all(s['nv'] == (r['nv_step'] if s['i'] < r['vest_step'] else 0.0) for s in sched['steps'][:-1])
    boot = bootstrap_values(run, horizon=r['t_end'], risk_free_only=True)
    curve = SO.rate_fn(run.terms, r['t_end'])
    assert len(boot['curves']) == 1
    assert all(row['spot'] == pytest.approx(curve(row['t'])) for row in boot['curves'][0]['rows'])
