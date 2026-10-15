"""공시 사례 — 공개 공시(전자공시)만으로 만든 연습용 평가파일이 앱에서 그대로 열리고 계산되는지.

각 파일의 검토메모 마지막 줄에 «이 파일 그대로 앱에서 계산한 값» 을 적어 두었다. 계산 엔진이
바뀌어 값이 달라지면 그 줄이 낡으므로, 메모의 금액(백만원)과 지금 계산이 같은지 본다.
"""
import re

import pytest
from streamlit.testing.v1 import AppTest

import v2_workspace
import workspace_app as W
from valuation.service import calculate
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
