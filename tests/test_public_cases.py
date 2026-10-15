"""공시 사례 — 공개 공시(전자공시)만으로 만든 연습용 평가파일이 앱에서 그대로 열리고 계산되는지.

각 파일의 검토메모 마지막 줄에 «이 파일 그대로 앱에서 계산한 값» 을 적어 두었다. 계산 엔진이
바뀌어 값이 달라지면 그 줄이 낡으므로, 메모의 금액(백만원)과 지금 계산이 같은지 본다.
"""
import io
import re
import shutil
import zipfile

import pytest
from streamlit.testing.v1 import AppTest

import v2_workspace
import workspace_app as W
import valuation.legacy as L
from valuation.service import calculate, export_bundle
from valuation.xlsx_validation import recalculate_and_compare
from test_v2_workflow import ROOT

CASES = sorted(v2_workspace.PUBLIC_CASES.glob('*.json'))


def test_folder_has_the_public_cases():
    assert len(CASES) >= 3
    assert len(v2_workspace.public_cases()) == len(CASES)


@pytest.mark.parametrize('path', CASES, ids=lambda p: p.stem)
def test_public_case_opens_calculates_and_matches_its_memo(path):
    case = W.read_case(path.read_bytes(), path.stem)
    face = case.effective()['face_total']
    for key in ('S0', 'sig', 'rf_curve', 'cr_curve', 'contract', 'market_date'):
        assert case.sources.get(key, '').strip(), key
    assert '접수번호' in case.sources['contract']
    run = calculate(case)
    a = run.summary['amounts_100']
    memo = case.notes.splitlines()[-1]
    assert memo.startswith('· 이 파일 그대로 앱에서 계산한 값')
    app = [float(v.replace(',', '')) for v in re.findall(r'앱 ([\d,]+)백만원', memo)]
    assert len(app) == 2, memo
    deriv = a['put_increment'] + (a['conversion_increment'] if '전환권+조기상환권' in memo else 0.0)
    assert round(deriv * face / 100 / 1e6) == app[0]
    assert round(a['call_deduction'] * face / 100 / 1e6) == app[1]


def test_public_case_opens_from_the_sidebar():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180)
    app.run()
    names = list(v2_workspace.public_cases())
    app.selectbox(key='_public_case').set_value(names[-1]).run()
    next(b for b in app.button if b.label == '공시 사례 열기').click().run()
    assert not app.exception, app.exception
    assert app.session_state.case.name == names[-1]


@pytest.mark.skipif(not (shutil.which('libreoffice') or shutil.which('soffice')), reason='LibreOffice 없음')
@pytest.mark.parametrize('path', CASES, ids=lambda p: p.stem)
def test_public_case_formula_workbook_recalculates_without_errors(path):
    # 평가기준일이 발행일 뒤인 결산 평가 — 격자 개월이 정수가 아니다. 수식 조서를 실제로 다시
    # 계산해 앱 값과 맞추고, 어느 시트에도 #N/A 같은 오류 칸이 없어야 한다(분리 판단 비교표 포함).
    case = W.read_case(path.read_bytes(), path.stem)
    run = calculate(case)
    data = export_bundle(run, detail=True, formula=True, accounting=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xlsx = z.read(next(n for n in z.namelist() if n.endswith('.xlsx')))
    args = (run.terms, run.raw['full'], run.raw['b0'], run.raw['b1'], run.raw['b2'], run.raw['ca'])
    expected = L.formula_key_cells(run.terms, run.raw, L.eir_or_none(*args))
    result = recalculate_and_compare(xlsx, expected=expected)
    assert result['comparison'] and all(r[4] == '일치' for r in result['comparison'])


@pytest.mark.skipif(not (shutil.which('libreoffice') or shutil.which('soffice')), reason='LibreOffice 없음')
def test_dated_call_schedule_recalculates_without_errors():
    # 매도청구 회차표를 «날짜» 로 넣으면 계약 개월이 소수 여섯째 자리 밑까지 간다(정기 행사 · 계약일
    # 목록의 개월). 언제든지 행사(녹십자웰빙)와 함께 두 길 모두에서 분리 판단 비교표가 깨지지 않아야 한다.
    path = next(p for p in CASES if p.stem.startswith('아이즈비전'))
    case = W.read_case(path.read_bytes(), path.stem)
    case.contract['k_sched'] = '2026-02-23 103.5\n2026-05-26 104.4\n2026-08-24 105.3\n2026-11-23 106.2'
    run = calculate(case)
    data = export_bundle(run, detail=True, formula=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xlsx = z.read(next(n for n in z.namelist() if n.endswith('.xlsx')))
    args = (run.terms, run.raw['full'], run.raw['b0'], run.raw['b1'], run.raw['b2'], run.raw['ca'])
    expected = L.formula_key_cells(run.terms, run.raw, L.eir_or_none(*args))
    result = recalculate_and_compare(xlsx, expected=expected)
    assert result['comparison'] and all(r[4] == '일치' for r in result['comparison'])
