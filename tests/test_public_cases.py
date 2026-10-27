"""공시 사례 — 공개 공시(전자공시)만으로 만든 연습용 평가파일이 앱에서 그대로 열리고 계산되는지.

각 파일의 검토메모 마지막 줄에 «이 파일 그대로 앱에서 계산한 값» 을 적어 두었다. 계산 엔진이
바뀌어 값이 달라지면 그 줄이 낡으므로, 메모의 금액(백만원)과 지금 계산이 같은지 본다.
"""
import io
import json
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
# 전환사채 사례와 주식선택권 사례는 결과의 모양이 달라 따로 본다.
SO_CASES = [p for p in CASES if json.loads(p.read_text(encoding='utf-8'))['contract'].get('inst') == 'SO']
CB_CASES = [p for p in CASES if p not in SO_CASES]


def test_folder_has_the_public_cases():
    assert len(CASES) >= 3
    assert len(v2_workspace.public_cases()) == len(CASES)


@pytest.mark.parametrize('path', CB_CASES, ids=lambda p: p.stem)
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
@pytest.mark.parametrize('path', CB_CASES, ids=lambda p: p.stem)
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


@pytest.mark.parametrize('path', SO_CASES, ids=lambda p: p.stem)
def test_stock_option_public_case_matches_its_memo(path):
    # 주식선택권 공시 사례 — 메모의 «앱 …원 · 앱 …천원» 이 지금 계산과 같고, 회사의 비용 배분 방식
    # (부여일부터 가득일까지 일수 비례)을 공시 1주당 공정가치에 적용하면 공시 당기 비용이 나온다.
    case = W.read_case(path.read_bytes(), path.stem)
    for key in ('S0', 'sig', 'rf_curve', 'K0', 'so_rows', 'contract', 'market_date'):
        assert case.sources.get(key, '').strip(), key
    assert '접수번호' in case.sources['contract']
    run = calculate(case)
    memo = case.notes.splitlines()[-1]
    assert memo.startswith('· 이 파일 그대로 앱에서 계산한 값')
    fv = float(re.search(r'1주당 공정가치 앱 ([\d,.]+)원', memo)[1].replace(',', ''))
    assert round(run.summary['amounts_per_share']['so_fair'], 1) == fv
    thousand = [float(v.replace(',', '')) for v in re.findall(r'앱 ([\d,]+)천원', memo)]
    assert round(run.summary['amounts_total']['so_cost'] / 1e3) == thousand[0]
    first = run.summary['so_schedule'][0]['결산일']
    assert round(sum(r['당기 비용'] for r in run.summary['so_schedule'] if r['결산일'] == first) / 1e3) == thousand[1]
    disclosed = re.search(r'공시 총 보상원가 ([\d,]+)천원\(= 1주당 ([\d,.]+)원 × ([\d,]+)주\) × (\d+)일/(\d+)일 = ([\d,]+)천원',
                          case.notes)
    total, fv_d, qty, num, den, expense = (float(x.replace(',', '')) for x in disclosed.groups())
    assert qty == run.raw['qty'] and abs(fv_d * qty / 1e3 - total) < 2      # 공시 1주당 금액은 소수 첫째 자리로 반올림됨
    share = run.summary['so_schedule'][0]['누적 배분율']
    assert abs(share - num / den) < 1e-12
    assert round(total * share) == expense
    for detail in (False, True):
        export_bundle(run, detail=detail, accounting=True)     # 기본·상세 값 조서가 만들어진다
