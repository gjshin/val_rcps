"""주주간계약 같은 주식 물량 연계 판단 — 수식 조서를 LibreOffice 로 실제 재계산해 앱 값과 견준다 (가상 수치).

LibreOffice 가 없으면 건너뛴다(결과에 «건너뜀» 으로 남는다). 금리 산출(IR) 시트를 붙여 트리의 선도이자율이
그 시트를 수식으로 따르는 경로까지 본다.
"""
import shutil

import pytest

from valuation.legacy import Terms, derive, sha_portfolio, build_xlsx_sha, sha_qty, sha_contract_shares
from valuation.xlsx_validation import inspect_workbook, recalculate_and_compare

RF = [(1, .0226), (3, .0240), (5, .0252)]
CR = [(1, .1409), (3, .1740), (5, .1905)]
CASES = {
    "같은 주식 450 · 풋만 150 · 콜 권리자 우선 · 위험 곡선 할인":
        dict(sha_put_q=600_000., sha_call_q=450_000., sha_link_q=450_000., sha_kill=1, pc_order=1, sha_disc=1),
    "같은 주식 300 · 콜만 200 · 풋 의무자 대상회사 · 무위험+스프레드":
        dict(sha_put_q=300_000., sha_call_q=500_000., sha_link_q=300_000., sha_kill=1, sha_writer=1, sha_disc=2,
             sha_spread=.02),
    "상대 권리 존속": dict(sha_put_q=600_000., sha_call_q=450_000., sha_kill=0),
}


def _case(o):
    t = Terms(inst="SHA", K0=10000., S0=8000., face_total=6e9, sha_put_yield=.07, sha_call_prem=.07,
              sha_put_s=24., sha_put_e=60., sha_call_s=24., sha_call_e=60., gap_m=6., rf_curve=RF, cr_curve=CR, **o)
    derive(t)
    return t, sha_portfolio(t)


@pytest.mark.parametrize("name", list(CASES))
def test_formula_workbook_structure(name):
    t, R = _case(CASES[name])
    data = build_xlsx_sha(t, R, formula=True, attach=dict(ir=True))
    check = inspect_workbook(data)
    assert not check["errors"], check["errors"]
    assert check["max_formula_length"] <= 8192


@pytest.mark.skipif(not (shutil.which("libreoffice") or shutil.which("soffice")), reason="LibreOffice 없음")
@pytest.mark.parametrize("name", list(CASES))
def test_formula_workbook_recalculates_to_app(name):
    t, R = _case(CASES[name])
    data = build_xlsx_sha(t, R, formula=True, attach=dict(ir=True))
    kp = t.K0/100
    exp = [("풋 원", "결과", "F7", R["put_krw"]), ("콜 원", "결과", "F8", R["call_krw"]),
           ("풋 100", "결과", "C7", R["put"]), ("콜 100", "결과", "C8", R["call"]),
           ("계약 대상 주식 원", "결과", "F6", 100*t.S0/t.K0*kp*sha_contract_shares(t))]
    for k, b in enumerate(R["blocks"]):
        exp.append((f"물량 {b['name']} 풋", "결과", f"C{22+k}", b["R"]["put"] if b["qp"] > 0 else 0.0))
    out = recalculate_and_compare(data, expected=exp)
    assert all(r[4] == "일치" for r in out["comparison"]), out["comparison"]
