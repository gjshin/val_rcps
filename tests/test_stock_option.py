"""주식선택권(주식결제형) — 격자·블랙-숄즈·비용 배분·입력 검사·조서·화면.

숫자는 모두 가상의 값이다 (실제 회사 자료 아님).
"""
import io
import zipfile

import pytest
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

import v2_workspace
from valuation import stock_option as SO
from valuation import legacy as L
from valuation.case import Case, inspect_case
from valuation.evidence import TOPICS, applicable
from valuation.service import calculate, export_bundle, CaseError
from test_v2_workflow import ROOT


def case(**over):
    rows = over.pop('so_rows', [dict(name='1차', vest='2028-01-01', end='', qty=1000.0, keep=1.0)])
    contract = dict(inst='SO', tranche='가상', d_issue='2026-01-01', d_mat='2031-01-01', K0=100.0, so_settle=0,
                    so_fy_end='12-31', so_rows=rows)
    market = dict(S0=100.0, sig=0.3, div_y=0.0, rf_curve=[[1, 0.03], [5, 0.03]], y_type='spot', cmp_rf=1)
    method = dict(d_base='2026-01-01', so_ex=0, so_mult=2.0, so_exit=0.0, grid_days=7.0, gap_m=1.0)
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
    (dict(so_settle=1), '현금결제형'),
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
    assert ids == {'so_volatility', 'so_early_exercise', 'so_vesting', 'fair_value_inputs', 'method_change'}
    cb = {**values, 'inst': 'CB', 'view': 'issuer'}
    assert not any(applicable(t, cb) for t in TOPICS if t['trigger'] == 'so')


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
    for r, x in zip(rows[1:-1], run.summary['so_rows']):
        assert r[head.index('1주당 공정가치')] == x['1주당 공정가치']
    assert rows[-1][head.index('보상원가')] == pytest.approx(run.summary['amounts_total']['so_cost'])
    labels = [r[0] for r in basic['계약조건'].iter_rows(values_only=True)]
    assert '행사가격(원)' in labels and '현재 전환가액(원)' not in labels and '표면이자·우선배당률(연, %)' not in labels
    journal = [r for r in basic['회계처리'].iter_rows(values_only=True) if r and r[1] == '주식보상비용']
    assert sum(r[2] for r in journal) == pytest.approx(run.summary['amounts_total']['so_cost'])
    detail = _workbook(run, detail=True)
    lattices = [n for n in detail.sheetnames if n.startswith('격자 ')]
    assert len(lattices) == 3
    ws, r = detail[lattices[0]], run.raw['rows'][0]
    found = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, float) and c.value == r['values'][0][0]]
    assert found                                          # 격자 시점 0의 가치 = 회차 1주당 공정가치
    assert r['values'][0][0] == r['fv']
    with pytest.raises(ValueError, match='2단계'):
        export_bundle(run, detail=True, formula=True)


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
    for topic in ('노드 계산표', '노드 따라가기', '금리·확률', '비용 배분'):
        app.session_state['_workflow_stage'] = '계산내역'
        app.session_state['_calc_topic'] = topic
        app.run()
        assert not app.exception, (topic, app.exception)
    app.session_state['_workflow_stage'] = '조서 출력'
    app.run()
    choice = next(r for r in app.radio if r.label == '조서 구성')
    assert choice.options == ['기본 값 조서', '상세 계산 값 조서']
    app.session_state['_workflow_stage'] = '입력·시장자료'
    app.session_state['_input_area'] = '주가·변동성·금리 자료'
    app.run()
    tools = next(s for s in app.selectbox if s.label == '시장자료 도구')
    assert tools.options == ['변동성 산출', '기준일 주가 조회', '금리곡선 불러오기']


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
    for detail in (False, True):
        wb = _workbook(run, detail=detail, accounting=True)
        rows = list(wb['블랙-숄즈 대조'].iter_rows(values_only=True))
        life_row = next(x for x in rows if x[1] == '기대존속기간')
        assert life_row[4] is None and life_row[8] == pytest.approx(150.0)
