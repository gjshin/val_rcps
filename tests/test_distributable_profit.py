"""배당가능이익 상환 제약 (상환전환우선주) — 가상 수치.

· 넣지 않으면 종전과 같다 (배당이 가능하다는 전제).
· 발생연도 Y 의 이익은 Y+1 년 재원 · 우선배당 먼저 · 갚지 못한 금액은 다음 해로 · 동순위는 상환금 비율.
· 손계산 대조는 앱 계산 함수를 쓰지 않고 시험 안에서 따로 센다.
· 발행자 상환권은 재원이 있어야 행사, 제3자 매도청구권은 직접 영향 없음, 다른 상품(전환사채)에는 영향 없음.
· 수식 조서를 리브레오피스로 다시 계산해 엔진과 같은지 본다.
"""
import datetime as dt
import io
import math
import shutil
import subprocess
import zipfile
from dataclasses import asdict

import pytest
from openpyxl import load_workbook

from valuation import legacy
from valuation.case import import_legacy, inspect_case
from valuation.service import calculate, export_bundle
from valuation.xlsx_validation import compare_cells

FACE = 10_000_000_000.0          # 평가대상 발행총액 100억


def rcps(**o):
    t = legacy.Terms(inst="RCPS", d_issue="2026-01-01", d_base="2026-01-01", d_mat="2031-01-01",
                     S0=20000., K0=60000., issue_px=60000., face_total=FACE, sig=.35, par=500.,
                     cpn=.02, div_basis=0, div_mode=0, ipay=12., mat_mode=0, ytm=.05, ytm_cmp=1,
                     p_mode="accrue", p_yield=.05, p_cmp=1, p_s=12., p_e=59., p_f=0.,
                     rfx_mode=0, issuer_call=0, k_w=0., cv_s=1., cv_e=60., view="issuer", gap_m=1.,
                     rf_curve=[[1, .03], [10, .03]], cr_curve=[[1, .08], [10, .08]])
    extra = {k: o.pop(k) for k in list(o) if k in ("dp_rows", "dp_others", "dp_delay", "dp_from")}
    for k, v in o.items(): setattr(t, k, v)
    c = import_legacy(asdict(t), "가상 우선주")
    c.exercise_styles = {"p_f": "any", "cv": "any"}
    if "dp_rows" in extra: c.market["dp_rows"] = extra.pop("dp_rows")
    c.contract.update(extra)
    return c


def ea(run):
    t = run.terms
    return legacy.exercise_amounts(t, t.n, t.T/t.n), t


def test_default_unchanged_without_profits():
    a = calculate(rcps()); b = calculate(rcps(dp_rows=[]))
    assert a.summary["amounts_100"] == b.summary["amounts_100"]
    E, t = ea(a)
    assert all(E["put_val"](i) == E["put"](i) for i in E["p_dates"]) and E["red_val"] == E["red"]


def test_hand_calculation_with_pari_product():
    # 2028 년 청구 → 발생연도 2027 이익 30억 · 2028 이익 60억. 평가대상 우선배당 2% (청구한 해만).
    # 동순위 상품: 발행 2026-01-01 · 50억 · 보장수익률 0 → 상환금 50 (100 기준) · 우선배당 1% (청구한 해).
    oth = [dict(name="가상 2회차", rank="pari", issue="2026-01-01", face=5e9, yld=0.0, cmp=1,
                start="2027-01-01", end="2030-12-31", div=.01)]
    run = calculate(rcps(dp_rows=[{"fy": 2027, "amt": 3e9}, {"fy": 2028, "amt": 6e9}], dp_others=oth))
    E, t = ea(run)
    dt_ = t.T/t.n
    i = next(i for i in sorted(E["p_dates"]) if legacy.dp_step_dt(t, dt_, i).year == 2028)
    A = E["put"](i)
    RF, CR = legacy.curves(t); f = legacy.forward_rate(CR, 0.0, dt_)
    # 손계산 — 1년차: 재원 30 − 우선배당(평가대상 2 + 동순위 0.5) = 27.5 를 A : 50 으로 나눈다
    cap1 = 30.0 - 2.0 - 0.5
    p1 = min(A, cap1*A/(A + 50.0)); q1 = min(50.0, cap1*50.0/(A + 50.0))
    # 2년차: 재원 60 (우선배당 없음 — 둘 다 청구함), 남은 금액 비율
    be, bo = A - p1, 50.0 - q1
    cap2 = 60.0
    p2 = min(be, cap2*be/(be + bo))
    # 3년차: 발생연도 2029 는 넣지 않았으므로 제한 없음 — 남은 것 전부
    p3 = be - p2
    m1 = int(math.floor(1/dt_ + .5)); m2 = int(math.floor(2/dt_ + .5))
    pv = p1 + p2*math.exp(-f*m1*dt_) + p3*math.exp(-f*m2*dt_)
    assert E["put_val"](i) == pytest.approx(pv, rel=1e-12)
    assert E["put_val"](i) < A


def test_pari_window_inside_fund_year_but_off_anniversary():
    # 동순위 상품의 청구 기간이 2028-03-01~2028-06-30 뿐이어도(평가대상 청구일·1년 뒤 지급일 어디에도 걸리지 않음)
    # 2028년 재원 연도 안에 있으므로 그 해 함께 청구한다. 상환금은 그 해 청구 기간 중 평가대상 지급일에 가장 가까운 날
    # (2028-03-01) 기준 — 50 × 1.04^τ.
    oth = [dict(name="가상 3회차", rank="pari", issue="2026-01-01", face=5e9, yld=.04, cmp=1,
                start="2028-03-01", end="2028-06-30", div=.01)]
    run = calculate(rcps(dp_rows=[{"fy": 2027, "amt": 3e9}, {"fy": 2028, "amt": 6e9}], dp_others=oth))
    E, t = ea(run)
    dt_ = t.T/t.n
    i = next(i for i in sorted(E["p_dates"]) if (d := legacy.dp_step_dt(t, dt_, i)).year == 2028 and d.month < 3)
    A = E["put"](i)
    RF, CR = legacy.curves(t); f = legacy.forward_rate(CR, 0.0, dt_)
    B = 50.0*1.04**((dt.datetime(2028, 3, 1) - dt.datetime(2026, 1, 1)).days/365)
    cap1 = 30.0 - 2.0 - 0.5
    p1 = min(A, cap1*A/(A + B)); q1 = min(B, cap1*B/(A + B))
    be, bo = A - p1, B - q1
    p2 = min(be, 60.0*be/(be + bo))
    p3 = be - p2
    m1 = int(math.floor(1/dt_ + .5)); m2 = int(math.floor(2/dt_ + .5))
    assert E["put_val"](i) == pytest.approx(p1 + p2*math.exp(-f*m1*dt_) + p3*math.exp(-f*m2*dt_), rel=1e-12)


def test_issuer_call_counts_pari_principal_same_year():
    # 재원 200 − 우선배당 2 = 198. 발행자 상환금(약 106~125) 만으로는 되지만 같은 해 동순위 상환금 100 을 더하면 모자란다.
    call = dict(k_s=12., k_e=59., k_f=12., k_prem=.06, k_cmp=1, k_w=1.)
    prof = [{"fy": y, "amt": 2e10} for y in range(2026, 2031)]
    oth = [dict(name="가상 4회차", rank="pari", issue="2026-01-01", face=1e10, yld=0.0, cmp=1,
                start="2027-01-01", end="2030-12-31", div=0.0)]
    a = calculate(rcps(issuer_call=1, dp_rows=prof, **call)); Ea, _ = ea(a)
    b = calculate(rcps(issuer_call=1, dp_rows=prof, dp_others=oth, **call)); Eb, _ = ea(b)
    assert Ea["k_dates"] and all(Ea["k_on"](i) for i in Ea["k_dates"])
    assert not any(Eb["k_on"](i) for i in Eb["k_dates"])


def test_peer_whose_window_ended_is_retired():
    # 청구 기간이 2026년 안에 끝난 동순위 상품 — 평가대상 청구(2027년~)보다 먼저 상환을 마쳤다고 보므로
    # 배당도 상환금도 빼지 않는다. 다른 상품을 넣지 않은 평가와 같아야 한다.
    rows = [{"fy": y, "amt": 1.5e9} for y in range(2026, 2031)]
    oth = [dict(name="가상 5회차", rank="pari", issue="2026-01-01", face=5e9, yld=.03, cmp=1,
                start="2026-06-01", end="2026-12-31", div=.02)]
    Ea, _ = ea(calculate(rcps(dp_rows=rows, dp_others=oth))); Eb, _ = ea(calculate(rcps(dp_rows=rows)))
    assert all(Ea["put_val"](i) == Eb["put_val"](i) for i in Ea["p_dates"]) and Ea["red_val"] == Eb["red_val"]
    assert any(Ea["put_val"](i) < Ea["put"](i) for i in Ea["p_dates"])


def test_call_date_dividend_not_counted_twice():
    # 행사일 배당을 따로 주는 발행자 상환권 — 그 배당은 우선배당으로 재원에서 이미 뺐다.
    # 재원 = 우선배당 2 + 상환금 + 1 이면 (배당 2 를 한 번 더 더하지 않으므로) 행사할 수 있다.
    call = dict(issuer_call=1, k_s=12., k_e=59., k_f=12., k_prem=.06, k_cmp=1, k_w=1., k_cpn_add=1)
    E0, t = ea(calculate(rcps(**call))); dt_ = t.T/t.n
    i0 = min(E0["k_dates"]); y = legacy.dp_fund_year(t, legacy.dp_step_dt(t, dt_, i0))
    amt = (E0["call"](i0) + 2.0 + 1.0)*FACE/100
    E, _ = ea(calculate(rcps(dp_rows=[{"fy": y - 1, "amt": amt}], **call)))
    assert E["k_on"](i0)
    E2, _ = ea(calculate(rcps(dp_rows=[{"fy": y - 1, "amt": amt - 2e8}], **call)))   # 재원이 상환금보다 1 작으면 막힌다
    assert not E2["k_on"](i0)


def test_peer_issued_later_in_same_fund_year_pays_dividend_first():
    # 2028년 초 청구 · 동순위 상품은 2028-06-01 발행, 2028-07-01~12-31 청구 → 같은 재원 연도라 그 해 상환금과
    # 함께 우선배당(5e9 × 2% = 100 기준 1)도 먼저 뺀다. 재원 30 − 평가대상 2 − 1 = 27.
    oth = [dict(name="가상 6회차", rank="pari", issue="2028-06-01", face=5e9, yld=0.0, cmp=1,
                start="2028-07-01", end="2028-12-31", div=.02)]
    E, t = ea(calculate(rcps(dp_rows=[{"fy": 2027, "amt": 3e9}], dp_others=oth))); dt_ = t.T/t.n
    i = next(i for i in sorted(E["p_dates"]) if (d := legacy.dp_step_dt(t, dt_, i)).year == 2028 and d.month < 6)
    r0 = E["dp"].schedule(i, E["put"](i))["rows"][0]
    assert r0[3] == pytest.approx(27.0, rel=1e-12) and r0[5] == pytest.approx(50.0, rel=1e-12)


def test_missing_maturity_year_is_warned():
    # 만기 현금상환 — 만기(2031-01-01)는 발생연도 2030 이익을 쓴다. 상환청구 기간의 해만 넣으면 만기 해를 알린다.
    rows = [{"fy": y, "amt": 1e10} for y in range(2026, 2030)]
    run = calculate(rcps(mat_mode=1, dp_rows=rows))
    assert any("2030" in m.message and "넣지 않아" in m.message for m in run.issues if m.code == "input_check")


def test_missing_year_for_carried_balance_is_warned():
    # 만기(2031-01-01) 현금상환 · 발생연도 2030 이익 0 → 전액이 2032년 재원(발생연도 2031, 넣지 않음)으로 넘어간다.
    rows = [{"fy": y, "amt": 1e10} for y in range(2026, 2030)] + [{"fy": 2030, "amt": 0.0}]
    run = calculate(rcps(mat_mode=1, dp_rows=rows))
    msg = [m.message for m in run.issues if m.code == "input_check" and "넣지 않아" in m.message]
    assert msg and "2031" in msg[0] and "2030" not in msg[0].split("년의")[0]


def test_fractional_compounding_mode_is_rejected():
    t = legacy.Terms(inst="RCPS"); t.dp_rows = [{"fy": 2027, "amt": 1e9}]
    t.dp_others = [dict(name="가상", rank="pari", issue="2026-01-01", face=1e9, start="2027-01-01",
                        end="2028-01-01", cmp=0.5)]
    assert any("복리 방식" in m for m in legacy.dp_issues(t))


def test_fractional_fiscal_year_is_rejected():
    t = legacy.Terms(inst="RCPS"); t.dp_rows = [{"fy": 2027.9, "amt": 1e9}]
    assert any("발생연도와 금액" in m for m in legacy.dp_issues(t)) and not legacy.dp_profits(t)


def test_malformed_numbers_report_instead_of_crash():
    # 숫자가 아닌 칸이 있어도 점검이 멈추지 않고 오류 문장을 낸다 (고치기 전에는 숫자 변환 오류로 멈췄다).
    bad = rcps(dp_rows=[{"fy": "이천이십칠", "amt": "많음"}],
               dp_others=[dict(name="가상", rank="pari", issue="2026-01-01", face=1e9, start="2027-01-01",
                               end="2028-01-01", cmp="연복리")])
    msgs = [i.message for i in inspect_case(bad) if i.severity == "error"]
    assert any("발생연도와 금액" in m for m in msgs) and any("복리 방식" in m for m in msgs)
    t = legacy.Terms(inst="RCPS"); t.dp_rows = [{"fy": 2027, "amt": 1e9}]; t.dp_delay = "빠름"
    assert any("가산율을 숫자로" in m for m in legacy.dp_issues(t))
    assert not legacy.dp_active(legacy.Terms(inst="RCPS", dp_rows=[{"fy": "x", "amt": "y"}]))


def test_dividends_first_and_zero_capacity_carries_forward():
    # 2028 년 재원(발생연도 2027) 이 우선배당(2)보다 작으면 그 해 상환은 0 이고 전액이 다음 해로 넘어간다.
    run = calculate(rcps(dp_rows=[{"fy": 2027, "amt": 1e8}], dp_delay=.04))
    E, t = ea(run); dt_ = t.T/t.n
    i = next(i for i in sorted(E["p_dates"]) if legacy.dp_step_dt(t, dt_, i).year == 2028)
    sc = E["dp"].schedule(i, E["put"](i))
    assert sc["rows"][0][3] == 0.0 and sc["rows"][0][6] == 0.0          # 재원 0 · 지급 0
    assert sc["rows"][1][6] == pytest.approx(E["put"](i)*1.04, rel=1e-12)  # 다음 해 가산율 붙여 전액
    assert any("우선배당보다 작습니다" in m.message for m in run.issues if m.code == "input_check")


def test_fiscal_year_mapping_and_unlisted_years_unlimited():
    # 발생연도 2030 만 넣으면 2031 년 청구분만 제한된다 — 이 평가의 청구는 2027~2030 년이라 모두 제한 없음.
    run = calculate(rcps(dp_rows=[{"fy": 2030, "amt": 0.0}]))
    E, t = ea(run)
    assert all(E["put_val"](i) == pytest.approx(E["put"](i), rel=1e-15) for i in E["p_dates"])
    assert any("넣지 않아" in m.message for m in run.issues if m.code == "input_check")


def test_fund_start_date_moves_year_boundary():
    # 재원 사용 시작일 4월 1일 — 2028년 2월 청구는 아직 2027년 재원(발생연도 2026 이익)을 쓴다.
    # 발생연도 2026 이익 0 → 그 해 지급 0. 1월 1일(기본)이면 2028년 재원(발생연도 2027, 넣지 않음 → 제한 없음).
    rows = [{"fy": 2026, "amt": 0.0}]
    a = calculate(rcps(dp_rows=rows, dp_from="04-01")); b = calculate(rcps(dp_rows=rows))
    Ea, t = ea(a); Eb, _ = ea(b); dt_ = t.T/t.n
    feb = [i for i in sorted(Ea["p_dates"]) if (d := legacy.dp_step_dt(t, dt_, i)).year == 2028 and d.month < 4]
    may = [i for i in sorted(Ea["p_dates"]) if (d := legacy.dp_step_dt(t, dt_, i)).year == 2028 and d.month >= 4]
    assert feb and may
    for i in feb:
        assert legacy.dp_fund_year(t, legacy.dp_step_dt(t, dt_, i)) == 2027
        assert Ea["dp"].schedule(i, Ea["put"](i))["rows"][0][6] == 0.0
        assert Ea["put_val"](i) < Ea["put"](i) and Eb["put_val"](i) == Eb["put"](i)
    for i in may:
        assert Ea["put_val"](i) == pytest.approx(Ea["put"](i), rel=1e-15)
    bad = [x.message for x in inspect_case(rcps(dp_rows=rows, dp_from="13-40")) if x.severity == "error"]
    assert bad


def test_issuer_call_needs_capacity_third_party_does_not():
    call = dict(k_s=12., k_e=59., k_f=12., k_prem=.06, k_cmp=1, k_w=1.)
    prof = [{"fy": y, "amt": 1e9} for y in range(2026, 2031)]
    a = calculate(rcps(issuer_call=1, dp_rows=prof, **call))
    E, _ = ea(a)
    assert E["k_dates"] and not any(E["k_on"](i) for i in E["k_dates"])       # 재원 10 < 상환금 100 이상
    assert any("발행자 상환권은 재원이 부족한" in m.message for m in a.issues if m.code == "input_check")
    b = calculate(rcps(issuer_call=2, dp_rows=prof, **call))
    E2, _ = ea(b)
    assert all(E2["k_on"](i) for i in E2["k_dates"])                          # 제3자는 직접 제한 없음
    assert any("제3자 지정 매도청구권은" in m.message for m in b.issues if m.code == "input_check")


def test_other_products_unaffected():
    t = legacy.Terms(inst="CB"); t.dp_rows = [{"fy": 2026, "amt": 0.0}]
    assert not legacy.dp_active(t) and legacy.dp_issues(t) == []


def test_input_errors_block():
    bad = rcps(dp_rows=[{"fy": 2027, "amt": 1e9}, {"fy": 2027, "amt": 2e9}, {"fy": 2028, "amt": -1.0}],
               dp_others=[dict(name="날짜 없음", rank="pari", face=1e9)])
    msgs = [i.message for i in inspect_case(bad) if i.severity == "error"]
    assert any("두 번" in m for m in msgs) and any("0 이상" in m for m in msgs) and any("YYYY-MM-DD" in m for m in msgs)


def test_value_workbooks_carry_schedule_sheet():
    run = calculate(rcps(dp_rows=[{"fy": 2027, "amt": 3e9}]))
    for detail in (False, True):
        z = zipfile.ZipFile(io.BytesIO(export_bundle(run, detail=detail, accounting=True)))
        assert "00 배당가능이익 상환" in load_workbook(io.BytesIO(z.read("value_review.xlsx"))).sheetnames


@pytest.mark.skipif(not (shutil.which("libreoffice") or shutil.which("soffice")), reason="LibreOffice 없음")
@pytest.mark.parametrize("call,frm,win", [(0, "01-01", None), (1, "01-01", None), (0, "04-01", None),
                                          (1, "04-01", ("2028-05-01", "2028-08-31"))])
def test_formula_workbook_matches_engine(tmp_path, call, frm, win):
    kw = dict(issuer_call=1, k_s=12., k_e=59., k_f=12., k_prem=.06, k_cmp=1, k_w=1.) if call else {}
    if call and win: kw["k_cpn_add"] = 1
    oth = [dict(name="가상 2회차", rank="pari", issue="2026-06-01", face=5e9, yld=.04, cmp=0,
                start=(win or ("2028-01-01",))[0], end=(win or (None, "2030-06-30"))[1], div=.01)]
    run = calculate(rcps(dp_rows=[{"fy": 2027, "amt": 3e9}, {"fy": 2028, "amt": 2.5e10}, {"fy": 2029, "amt": 4e9}],
                         dp_others=oth, dp_delay=.03, dp_from=frm, **kw))
    t, R = run.terms, run.raw
    data = zipfile.ZipFile(io.BytesIO(export_bundle(run, formula=True, detail=True, accounting=True))).read("formula_review.xlsx")
    (tmp_path/"s.xlsx").write_bytes(data); (tmp_path/"o").mkdir()
    subprocess.run([shutil.which("libreoffice") or shutil.which("soffice"),
                    "-env:UserInstallation=" + (tmp_path/"p").as_uri(), "--headless", "--convert-to", "xlsx",
                    "--outdir", str(tmp_path/"o"), str(tmp_path/"s.xlsx")], capture_output=True, timeout=1800)
    wb = load_workbook(tmp_path/"o"/"s.xlsx", data_only=True)
    exp = legacy.formula_key_cells(t, R, legacy.eir_or_none(t, R["full"], R["b0"], R["b1"], R["b2"], R["ca"]))
    rows = compare_cells(wb, exp)
    assert all(r[4] == "일치" for r in rows), rows
    E, _ = ea(run)
    ws = wb["00 격자 공통"]
    for i in range(t.n + 1):
        if E["p_on"](i):
            assert ws.cell(7, 3+i).value == pytest.approx(E["put_val"](i), rel=1e-9, abs=1e-9), i
        if call:
            assert int(ws.cell(5, 3+i).value or 0) == (1 if E["k_on"](i) else 0), i
    assert ws.cell(10, 3+t.n).value == pytest.approx(E["red_val"], rel=1e-9)


def test_screen_keeps_inputs_and_matches_engine():
    # 입력 화면을 거쳐도 연도별 이익·다른 상품 표가 그대로 남고, 화면 평가 금액이 엔진과 같다.
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    oth = [dict(name="가상 2회차", rank="pari", issue="2026-01-01", face=5e9, yld=0.0, cmp=1,
                start="2027-01-01", end="2030-12-31", div=.01)]
    c = rcps(dp_rows=[{"fy": 2027, "amt": 3e9}, {"fy": 2028, "amt": 6e9}], dp_others=oth)
    app = AppTest.from_file(str(Path(__file__).parent.parent/"app.py"), default_timeout=300)
    app.session_state.case = c; app.run()
    assert not app.exception
    app.radio(key="_workflow_stage").set_value("평가·분석").run()
    next(b for b in app.button if b.label == "현재 입력으로 평가").click().run()
    assert not app.exception
    case = app.session_state.case
    assert case.market["dp_rows"] == [{"fy": 2027, "amt": 3e9}, {"fy": 2028, "amt": 6e9}]
    assert case.contract["dp_others"][0]["rank"] == "pari"
    assert app.session_state.run.summary["amounts_total"] == calculate(case).summary["amounts_total"]
    assert any("배당가능이익 반영" in x.label for x in app.expander)


def test_non_finite_peer_rates_block():
    for v in ("nan", "inf", float("nan")):
        t = legacy.Terms(inst="RCPS"); t.dp_rows = [{"fy": 2027, "amt": 1e9}]
        t.dp_others = [dict(name="가상", rank="pari", issue="2026-01-01", face=1e9, yld=v, div=v,
                            start="2027-01-01", end="2028-01-01", cmp=1)]
        msgs = legacy.dp_issues(t)
        assert any("상환 보장수익률" in m for m in msgs) and any("우선배당률" in m for m in msgs), v


def test_screen_survives_unreadable_uploaded_values():
    # 불러온 파일에 읽을 수 없는 값이 있어도 입력 화면이 멈추지 않고, 비워 둔 칸을 알린다.
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    c = rcps(dp_rows=[{"fy": "이천이십칠", "amt": "많음"}, {"fy": 2027.9, "amt": 1e9}, 42], dp_from="13-40",
             dp_others=[dict(name="가상", rank="pari", issue="날짜아님", face="큼", yld="nan", div="높음",
                             start="2027-01-01", end="2028-01-01", cmp="연복리")])
    app = AppTest.from_file(str(Path(__file__).parent.parent/"app.py"), default_timeout=300)
    app.session_state.case = c; app.run()
    for _ in range(2):                       # 한 번 더 그려도 원래 값과 안내가 남는다 (고치기 전에는 멈췄다)
        assert not app.exception
        assert any("읽을 수 없는 값" in w.value for w in app.warning)
        k = app.session_state.case
        assert 42 in k.market["dp_rows"]
        assert sorted(str(r["fy"]) for r in k.market["dp_rows"] if isinstance(r, dict)) == ["2027.9", "이천이십칠"]
        assert k.contract["dp_from"] == "13-40"
        o = k.contract["dp_others"][0]
        assert (o["issue"], o["face"], o["yld"], o["div"], o["cmp"]) == ("날짜아님", "큼", "nan", "높음", "연복리")
        assert any(i.severity == "error" for i in inspect_case(k))
        app.run()


def test_decimal_string_compounding_mode_calculates():
    base = dict(name="가상", rank="pari", issue="2026-01-01", face=5e9, yld=.03, start="2027-01-01",
                end="2030-12-31", div=.01)
    rows = [{"fy": 2027, "amt": 3e9}]
    a = calculate(rcps(dp_rows=rows, dp_others=[dict(base, cmp="1.0")]))
    b = calculate(rcps(dp_rows=rows, dp_others=[dict(base, cmp=1)]))
    assert a.summary["amounts_100"] == b.summary["amounts_100"]


def test_screen_lets_user_fix_invalid_carry_rate_with_zero():
    # 읽을 수 없는 가산율은 칸을 비워 보여 준다 — 비운 채면 원래 값을 두고, 0 을 넣으면 0 으로 고쳐진다.
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    c = rcps(dp_rows=[{"fy": 2027, "amt": 3e9}], dp_delay="빠름", dp_from="13-40")
    app = AppTest.from_file(str(Path(__file__).parent.parent/"app.py"), default_timeout=300)
    app.session_state.case = c; app.run()
    assert not app.exception
    k = app.session_state.case
    assert k.contract["dp_delay"] == "빠름" and k.contract["dp_from"] == "13-40"
    g = next(x for x in app.number_input if "가산율" in x.label)
    m = next(x for x in app.number_input if "재원 사용 시작 (월)" in x.label)
    d = next(x for x in app.number_input if "재원 사용 시작 (일)" in x.label)
    assert g.value is None and m.value is None and d.value is None
    g.set_value(0.0); m.set_value(1); d.set_value(1); app.run()
    assert not app.exception
    k = app.session_state.case
    assert k.contract["dp_delay"] == 0.0 and k.contract["dp_from"] == "01-01"


def test_missing_issuer_call_year_is_warned():
    # 상환청구권 없음 · 발행자 상환권만 — 행사일(2027~2030년)에 쓰는 해의 이익을 넣지 않았으면 알린다.
    call = dict(issuer_call=1, k_s=12., k_e=59., k_f=12., k_prem=.06, k_cmp=1, k_w=1., p_s=0., p_e=0.)
    run = calculate(rcps(dp_rows=[{"fy": 2025, "amt": 1e10}], **call))
    msg = [m.message for m in run.issues if m.code == "input_check" and "넣지 않아" in m.message]
    assert msg and "2026" in msg[0] and "발행자 상환권" in msg[0]


def test_profit_years_beyond_schedule_horizon_are_rejected():
    t = legacy.Terms(inst="RCPS", d_base="2026-01-01"); t.dp_rows = [{"fy": 2026 + legacy.DP_KMAX, "amt": 0.0}]
    assert any("까지 넣을 수 있습니다" in m for m in legacy.dp_issues(t))
    t.dp_rows = [{"fy": 2026 + legacy.DP_KMAX - 2, "amt": 0.0}]
    assert not any("까지 넣을 수 있습니다" in m for m in legacy.dp_issues(t))


def test_screen_keeps_unknown_peer_fields():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    c = rcps(dp_rows=[{"fy": 2027, "amt": 3e9}],
             dp_others=[dict(name="가상", rank="pari", issue="2026-01-01", face=5e9, yield_=.05, cmp=1,
                             start="2027-01-01", end="2030-12-31", div=.01)])
    app = AppTest.from_file(str(Path(__file__).parent.parent/"app.py"), default_timeout=300)
    app.session_state.case = c; app.run(); app.run()
    assert not app.exception
    k = app.session_state.case
    assert k.contract["dp_others"][0]["yield_"] == .05
    assert any("알 수 없는 칸" in i.message for i in inspect_case(k) if i.severity == "error")
    assert any("알 수 없는 칸" in w.value for w in app.warning)


def test_fund_year_uses_rounded_node_date():
    # 2026-03-01 ~ 2028-03-01 월 격자 — 13번째 시점은 2027-03-31 23시 무렵이지만 노드 날짜는 2027-04-01 이다.
    # 재원 사용 시작일 4월 1일이면 그날은 2027년 재원(발생연도 2026)을 쓴다.
    run = calculate(rcps(d_issue="2026-03-01", d_base="2026-03-01", d_mat="2028-03-01", p_e=23., cv_e=24.,
                         dp_rows=[{"fy": 2026, "amt": 0.0}], dp_from="04-01"))
    t = run.terms; n = t.n; dt_ = t.T/n
    nd = legacy.node_dates(t, n, dt_)
    hits = [i for i in range(n + 1) if nd[i] == dt.date(2027, 4, 1)]
    assert hits
    for i in hits:
        assert legacy.dp_step_dt(t, dt_, i).date() == nd[i]
        assert legacy.dp_fund_year(t, legacy.dp_step_dt(t, dt_, i)) == 2027
    assert all(legacy.dp_step_dt(t, dt_, i).date() == nd[i] for i in range(n + 1))


def test_screen_keeps_invalid_cells_when_other_rows_are_deleted():
    # 다른 줄을 지워도(줄 수가 바뀌어도) 남은 줄의 읽을 수 없는 칸·모르는 칸은 원래 값이 그대로 남는다.
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    ok = dict(name="정상", rank="pari", issue="2026-01-01", face=5e9, yld=.03, cmp=1, start="2027-01-01",
              end="2030-12-31", div=.01)
    badrow = dict(ok, name="가상", yld="nan", cmp="연복리", yield_=.05)
    c = rcps(dp_rows=[{"fy": 2026, "amt": 1e9}, {"fy": "이천이십칠", "amt": 3e9}], dp_others=[ok, badrow])
    app = AppTest.from_file(str(Path(__file__).parent.parent/"app.py"), default_timeout=300)
    app.session_state.case = c; app.run()
    rev = app.session_state["revision"] if "revision" in app.session_state else 0
    app.session_state[f"dp_others_{rev}"] = {"edited_rows": {}, "added_rows": [], "deleted_rows": [0]}
    app.session_state[f"dp_rows_{rev}"] = {"edited_rows": {}, "added_rows": [], "deleted_rows": [0]}
    app.run()
    assert not app.exception
    k = app.session_state.case
    assert k.market["dp_rows"] == [{"fy": "이천이십칠", "amt": 3e9}]
    o = k.contract["dp_others"]
    assert len(o) == 1 and (o[0]["yld"], o[0]["cmp"], o[0]["yield_"]) == ("nan", "연복리", .05)


def test_tie_day_rounds_like_node_dates():
    # 2026-10-01 부터 1년 월 격자 — 6번째 시점은 딱 182.5일. 노드 날짜(짝수 쪽 반올림)는 2027-04-01 이다.
    run = calculate(rcps(d_issue="2026-10-01", d_base="2026-10-01", d_mat="2027-10-01", p_s=1., p_e=11., cv_e=12.,
                         dp_rows=[{"fy": 2026, "amt": 0.0}], dp_from="04-02"))
    t = run.terms; n = t.n; dt_ = t.T/n
    nd = legacy.node_dates(t, n, dt_)
    assert all(legacy.dp_step_dt(t, dt_, i).date() == nd[i] for i in range(n + 1))
    i6 = next(i for i in range(n + 1) if nd[i] == dt.date(2027, 4, 1))
    assert legacy.dp_fund_year(t, legacy.dp_step_dt(t, dt_, i6)) == 2026


def _recalc_formula(run, tmp_path):
    data = zipfile.ZipFile(io.BytesIO(export_bundle(run, formula=True, detail=True, accounting=True))).read("formula_review.xlsx")
    (tmp_path/"s.xlsx").write_bytes(data); (tmp_path/"o").mkdir()
    subprocess.run([shutil.which("libreoffice") or shutil.which("soffice"),
                    "-env:UserInstallation=" + (tmp_path/"p").as_uri(), "--headless", "--convert-to", "xlsx",
                    "--outdir", str(tmp_path/"o"), str(tmp_path/"s.xlsx")], capture_output=True, timeout=1800)
    return data, load_workbook(tmp_path/"o"/"s.xlsx", data_only=True)


@pytest.mark.skipif(not (shutil.which("libreoffice") or shutil.which("soffice")), reason="LibreOffice 없음")
def test_formula_workbook_tie_day_and_text_names(tmp_path):
    # 딱 반일인 시점이 있는 격자에서도 수식 조서가 엔진과 같고, «=» 로 시작하는 상품 이름은 글자로 남는다.
    oth = [dict(name="=1+1", rank="pari", issue="2026-10-01", face=5e9, yld=.03, cmp=1,
                start="2027-03-01", end="2027-09-30", div=.01)]
    run = calculate(rcps(d_issue="2026-10-01", d_base="2026-10-01", d_mat="2027-10-01", p_s=1., p_e=11., cv_e=12.,
                         dp_rows=[{"fy": 2026, "amt": 2e9}], dp_from="04-02", dp_others=oth))
    t, R = run.terms, run.raw
    data, wb = _recalc_formula(run, tmp_path)
    exp = legacy.formula_key_cells(t, R, legacy.eir_or_none(t, R["full"], R["b0"], R["b1"], R["b2"], R["ca"]))
    assert all(r[4] == "일치" for r in compare_cells(wb, exp))
    E, _ = ea(run)
    ws = wb["00 격자 공통"]
    for i in range(t.n + 1):
        if E["p_on"](i):
            assert ws.cell(7, 3+i).value == pytest.approx(E["put_val"](i), rel=1e-9, abs=1e-9), i
    raw = load_workbook(io.BytesIO(data))["00 배당가능이익 상환"]
    cells = [c for row in raw.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=1+1")]
    assert len(cells) > 1 and all(c.data_type == "s" for c in cells)          # 이름 칸과 이름이 든 행 제목 모두
    vz = zipfile.ZipFile(io.BytesIO(export_bundle(run, detail=True, accounting=True)))
    vs = load_workbook(io.BytesIO(vz.read("value_review.xlsx")))["00 배당가능이익 상환"]
    vcells = [c for row in vs.iter_rows() for c in row if c.value == "=1+1"]
    assert vcells and all(c.data_type == "s" for c in vcells)


def _grid3(**o):
    return rcps(d_mat="2029-01-01", p_s=12., p_e=35., cv_e=36., dp_from="04-03",
                dp_rows=[{"fy": y, "amt": 1e8} for y in range(2026, 2030)], **o)


def test_carried_payment_waits_for_fund_year_start():
    # 지급 시점이 그 재원 연도 시작일(4월 3일) 전날에 걸리면 시작일 이후 첫 시점으로 미룬다.
    run = calculate(_grid3()); E, t = ea(run); dt_ = t.T/t.n
    moved = 0
    for i in E["p_dates"]:
        y0 = legacy.dp_fund_year(t, legacy.dp_step_dt(t, dt_, i))
        for k, m, at, *_ in E["dp"].schedule(i, E["put"](i))["rows"]:
            if k > 0:
                assert at >= legacy.dp_fund_start(t, y0 + k), (i, k, at)
                moved += m != i + legacy.dp_year_step(k, dt_)
    assert moved


def test_maturity_year_warned_even_with_auto_conversion():
    # 만기 자동전환이어도 부채요소는 만기 현금상환 일정을 쓴다 — 만기에 쓰는 해(발생연도 2030)를 넣지 않았으면 알린다.
    run = calculate(rcps(p_s=0., p_e=0., dp_rows=[{"fy": 2026, "amt": 1e10}]))
    msg = [m.message for m in run.issues if m.code == "input_check" and "넣지 않아" in m.message]
    assert msg and "2030" in msg[0]


@pytest.mark.skipif(not (shutil.which("libreoffice") or shutil.which("soffice")), reason="LibreOffice 없음")
def test_formula_workbook_moved_payment_steps(tmp_path):
    run = calculate(_grid3(dp_delay=.02))
    t, R = run.terms, run.raw
    data, wb = _recalc_formula(run, tmp_path)
    exp = legacy.formula_key_cells(t, R, legacy.eir_or_none(t, R["full"], R["b0"], R["b1"], R["b2"], R["ca"]))
    assert all(r[4] == "일치" for r in compare_cells(wb, exp))
    E, _ = ea(run)
    ws = wb["00 격자 공통"]
    for i in range(t.n + 1):
        if E["p_on"](i):
            assert ws.cell(7, 3+i).value == pytest.approx(E["put_val"](i), rel=1e-9, abs=1e-9), i
    assert ws.cell(10, 3+t.n).value == pytest.approx(E["red_val"], rel=1e-9)


def test_unpaid_at_maturity_lost_option():
    # 만기(2031-01-01) 현금상환 · 2030년까지 이익 0. 연장(기본)이면 2032년에 전액, «받지 못함» 이면 0.
    rows = [{"fy": y, "amt": 0.0} for y in range(2026, 2031)]
    Ea, _ = ea(calculate(rcps(mat_mode=1, p_s=0., p_e=0., dp_rows=rows)))
    b = calculate(rcps(mat_mode=1, p_s=0., p_e=0., dp_rows=rows, dp_unpaid="lost")); Eb, _ = ea(b)
    assert Ea["red_val"] > 0 and Eb["red_val"] == 0.0
    assert any("받지 못하는 것으로" in m.message for m in b.issues if m.code == "input_check")
    # 만기 전에 갚은 몫은 그대로 — 2028년 청구, 재원 일부만 있으면 만기 전 지급분만 남는다
    rows2 = [{"fy": y, "amt": 2e9} for y in range(2026, 2031)]
    Ec, t = ea(calculate(rcps(dp_rows=rows2, dp_unpaid="lost"))); dt_ = t.T/t.n
    for i in Ec["p_dates"]:
        sc = Ec["dp"].schedule(i, Ec["put"](i))
        assert sc["pv"] == pytest.approx(sum(r[6]*r[7] for r in sc["rows"] if r[1] <= t.n), rel=1e-12)
    assert legacy.dp_issues(legacy.Terms(inst="RCPS", dp_rows=rows, dp_unpaid="convert"))


def test_auto_conversion_with_extension_is_flagged():
    rows = [{"fy": y, "amt": 1e9} for y in range(2026, 2031)]
    run = calculate(rcps(dp_rows=rows))           # 만기 자동전환(기본) · 연장(기본) · 만기 뒤 지급 있음
    assert any("만기 자동전환 조건인데" in m.message for m in run.issues if m.code == "input_check")


@pytest.mark.skipif(not (shutil.which("libreoffice") or shutil.which("soffice")), reason="LibreOffice 없음")
def test_formula_workbook_unpaid_lost(tmp_path):
    oth = [dict(name="가상 2회차", rank="pari", issue="2026-06-01", face=5e9, yld=.04, cmp=0,
                start="2028-01-01", end="2030-06-30", div=.01)]
    run = calculate(rcps(mat_mode=1, dp_rows=[{"fy": y, "amt": 1.5e9} for y in range(2026, 2031)],
                         dp_others=oth, dp_delay=.03, dp_unpaid="lost"))
    t, R = run.terms, run.raw
    data, wb = _recalc_formula(run, tmp_path)
    exp = legacy.formula_key_cells(t, R, legacy.eir_or_none(t, R["full"], R["b0"], R["b1"], R["b2"], R["ca"]))
    assert all(r[4] == "일치" for r in compare_cells(wb, exp))
    E, _ = ea(run)
    ws = wb["00 격자 공통"]
    for i in range(t.n + 1):
        if E["p_on"](i):
            assert ws.cell(7, 3+i).value == pytest.approx(E["put_val"](i), rel=1e-9, abs=1e-9), i
    assert ws.cell(10, 3+t.n).value == pytest.approx(E["red_val"], rel=1e-9, abs=1e-9)


def test_screen_unpaid_option():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).parent.parent/"app.py"), default_timeout=300)
    app.session_state.case = rcps(dp_rows=[{"fy": 2027, "amt": 3e9}]); app.run()
    r = next(x for x in app.radio if x.label == "만기까지 갚지 못한 금액")
    assert r.value == "extend"
    r.set_value("lost").run()
    assert not app.exception and app.session_state.case.contract["dp_unpaid"] == "lost"


def _leap(**o):
    # 2026-04-03 발행 · 해마다 4월 3일 상환청구·발행자 상환권. 2028년은 윤년이라 계약일 2028-04-03 이
    # 하루 앞 노드(2028-04-02)에 배정된다. 재원 사용 시작일 4월 3일.
    c = rcps(d_issue="2026-04-03", d_base="2026-04-03", d_mat="2030-04-03", p_s=12., p_e=36., p_f=12.,
             cv_e=48., dp_from="04-03", dp_rows=[{"fy": 2026, "amt": 0.0}, {"fy": 2027, "amt": 1e11},
                                                 {"fy": 2028, "amt": 0.0}], **o)
    c.exercise_styles["p_f"] = "periodic"          # 해마다 정해진 날 (상시 행사가 아님)
    return c


def test_fund_year_uses_contract_claim_date():
    call = dict(issuer_call=1, k_s=12., k_e=36., k_f=12., k_prem=.06, k_cmp=1, k_w=1.)
    E, t = ea(calculate(_leap(**call))); dt_ = t.T/t.n
    i = next(i for i in E["p_dates"] if legacy.node_dates(t, t.n, dt_)[i] == dt.date(2028, 4, 2))
    assert E["dp"].claim_dt(i, "put").date() == dt.date(2028, 4, 3)
    assert legacy.dp_fund_year(t, E["dp"].claim_dt(i, "put")) == 2028
    assert E["put_val"](i) == pytest.approx(E["put"](i), rel=1e-12)    # 2028년 재원(발생연도 2027)으로 전액
    assert i in E["k_dates"] and E["k_on"](i)


@pytest.mark.skipif(not (shutil.which("libreoffice") or shutil.which("soffice")), reason="LibreOffice 없음")
def test_formula_workbook_contract_claim_date(tmp_path):
    call = dict(issuer_call=1, k_s=12., k_e=36., k_f=12., k_prem=.06, k_cmp=1, k_w=1.)
    oth = [dict(name="가상 2회차", rank="pari", issue="2026-04-03", face=5e9, yld=.04, cmp=1,
                start="2027-06-01", end="2029-06-30", div=.01)]
    run = calculate(_leap(dp_others=oth, **call))
    t, R = run.terms, run.raw
    data, wb = _recalc_formula(run, tmp_path)
    exp = legacy.formula_key_cells(t, R, legacy.eir_or_none(t, R["full"], R["b0"], R["b1"], R["b2"], R["ca"]))
    assert all(r[4] == "일치" for r in compare_cells(wb, exp))
    E, _ = ea(run)
    ws = wb["00 격자 공통"]
    for i in range(t.n + 1):
        if E["p_on"](i):
            assert ws.cell(7, 3+i).value == pytest.approx(E["put_val"](i), rel=1e-9, abs=1e-9), i
        assert int(ws.cell(5, 3+i).value or 0) == (1 if E["k_on"](i) else 0), i


def test_screen_keeps_unknown_profit_row_fields():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    c = rcps(dp_rows=[{"fy": 2027, "amt": 1e6, "currency": "USD"}])
    app = AppTest.from_file(str(Path(__file__).parent.parent/"app.py"), default_timeout=300)
    app.session_state.case = c; app.run(); app.run()
    assert not app.exception
    k = app.session_state.case
    assert k.market["dp_rows"] == [{"fy": 2027, "amt": 1e6, "currency": "USD"}]
    assert any(i.severity == "error" for i in inspect_case(k))
    assert any("알 수 없는 칸" in w.value for w in app.warning)


def test_pay_step_respects_tie_rounding():
    # 한 칸 = 183.5/6 일인 격자 — 6번째 시점은 딱 183.5일이라 짝수 쪽 184일. 시작일까지 D=184 일이면
    # 그 시점(6)에서 바로 갚을 수 있다(한 칸 더 미루지 않는다 — 고치기 전에는 7).
    dt_ = 183.5/(6*365)
    t = legacy.Terms(inst="RCPS", d_base="2026-10-01", dp_from="04-03")
    assert legacy.dp_step_days(dt_, 6) == 184
    assert legacy.dp_fund_start(t, 2027) == dt.datetime(2026, 10, 1) + dt.timedelta(days=184)
    P = legacy.DPPlan.__new__(legacy.DPPlan); P.tm, P.dt, P.n = t, dt_, 12
    assert P.pay_step(5, 2027) == 6
