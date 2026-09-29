#!/usr/bin/env python3
"""Synthetic 10-year RCPS: actual input widgets → month/week → Excel download.

python tests/acceptance_rcps_export.py --out /outside-repo/acceptance [--recalculate]
No client data. Recalculation needs LibreOffice and uses the complete exported grid.
"""
import argparse
import datetime as dt
import io
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path
from streamlit.testing.v1 import AppTest
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from synthetic_rcps_case import sample
from valuation import legacy
from valuation.case import Case
from valuation.presentation import PERCENT
from valuation.service import calculate
from valuation.xlsx_validation import inspect_workbook, recalculate_and_compare


def widget(app, group, label):
    return next(w for w in getattr(app,group) if w.label==label)


def enter_case(app, desired):
    widget(app,'text_input','평가 건명').set_value('Synthetic RCPS acceptance')
    widget(app,'button','빈 입력안 만들기').click().run()
    assert not app.exception
    facts=desired.facts()
    # First provide actual dates; exercise-date fields appear only after issue date.
    for w in app.date_input:
        match=re.match(r'input_(d_\w+)_\d+$',w.key or '')
        if match:w.set_value(dt.date.fromisoformat(facts[match[1]]))
    app.run()
    for title in ('전환·신주인수권 행사 가능','투자자 상환청구권 있음'):
        widget(app,'checkbox',title).check()
    app.run()
    widget(app,'multiselect','추가로 표시할 입력항목').set_value(['ytm','ytm_cmp']).run()
    dates=dict(cv_s='2026-04-09',cv_e='2036-04-07',p_s='2035-04-09',p_e='2036-04-06')
    for repeat in range(2):
        for group in ('number_input','selectbox','checkbox','date_input','text_input','text_area'):
            for w in getattr(app,group):
                match=re.match(r'input_(.+)_\d+$',w.key or '')
                if not match or match[1] not in facts:continue
                key=match[1];value=facts[key]
                if group=='date_input':
                    if key in dates:value=dt.date.fromisoformat(dates[key])
                    elif key.startswith('d_'):value=dt.date.fromisoformat(value)
                    else:continue
                elif group=='number_input':value=float(value*(100 if key in PERCENT else 1))
                elif group=='checkbox':value=bool(value)
                w.set_value(value)
        for w in app.selectbox:
            if w.key and w.key.endswith('_style') and 'p_f' in w.key:w.set_value('any')
        app.run()
    rev=app.session_state.revision
    for key in ('rf_curve','cr_curve'):
        app.session_state[f'{key}_{rev}']={'edited_rows':{},'deleted_rows':[],
            'added_rows':[{'만기(년)':x,'연이율(%)':y*100} for x,y in facts[key]]}
    app.run()
    assert not app.exception, app.exception
    assert not app.session_state.get('_input_pending',False)
    effective=app.session_state.case.effective()
    for key in ['d_issue','d_base','d_mat','S0','K0','issue_px','face_total','sig','par','cpn','div_basis','div_mode',
                'ipay','mat_mode','ytm','ytm_cmp','p_mode','p_yield','p_cmp','p_less_cpn','rfx_mode','issuer_call',
                'k_w','view','base_shares','dil_shares','rf_curve','cr_curve']:
        if key in ('rf_curve','cr_curve'):
            assert len(effective[key])==len(facts[key])
            assert all(math.isclose(a,b,rel_tol=1e-14) for row,expected in zip(effective[key],facts[key]) for a,b in zip(row,expected))
        else:
            assert effective[key]==facts[key],(key,effective.get(key),facts[key])
    assert app.session_state.case.exercise_styles['p_f']=='any'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--recalculate',action='store_true')
    a=parser.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    reports=[]
    for days in (0,7):
        start=time.monotonic()
        app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=900).run()
        enter_case(app,sample())
        widget(app,'selectbox','계산 간격 단위').set_value(days).run()
        app.radio(key='_input_area').set_value('주가·변동성·금리 자료').run()
        for tool in ['기준일 주가 조회','주가 역산','금리곡선 불러오기','BDT 금리 변동성','변동성 산출']:
            widget(app,'selectbox','시장자료 도구').set_value(tool).run()
            assert not app.exception
        app.radio(key='_workflow_stage').set_value('평가·분석').run()
        widget(app,'button','현재 입력으로 평가').click().run()
        assert not app.exception and 'run' in app.session_state
        run=app.session_state.run
        expected=legacy.decompose(legacy.Terms(**app.session_state.case.effective()))
        assert [run.raw[k] for k in ['b0','b1','b2','ca']]==list(expected[1:5])
        assert any('29.05%' in i.message for i in run.issues)
        widget(app,'selectbox','분석 도구').set_value('상세 계산·회계 참고표').run()
        for section in ['권리·금리 분석','이자율곡선','주가·변동성']:
            widget(app,'selectbox','상세 분석 항목').set_value(section).run()
            assert not app.exception
        app.radio(key='_workflow_stage').set_value('조서 출력').run()
        checks=[]
        for formula in (False,True):
            option='상세 계산 수식 조서' if formula else '상세 계산 값 조서'
            widget(app,'radio','조서 구성').set_value(option).run()
            widget(app,'checkbox','회계처리·분개·상각표 포함 (초안)').check().run()
            widget(app,'button','조서 생성').click().run()
            assert not app.exception and not app.error, app.error
            with zipfile.ZipFile(io.BytesIO(app.session_state.bundle)) as bundle:
                data=bundle.read('formula_review.xlsx' if formula else 'value_review.xlsx')
            path=a.out/f'{days}_{"formula" if formula else "value"}.xlsx';path.write_bytes(data)
            check=inspect_workbook(data);assert not check['errors']
            if a.recalculate and formula:check.update(recalculate_and_compare(data))
            checks.append(check)
            print(f'{days} days: {option} saved; {time.monotonic()-start:.1f}s',flush=True)
        reports.append(dict(days=days,intervals=run.terms.n,amounts=run.summary['amounts_100'],checks=checks,seconds=time.monotonic()-start))
        (a.out/'results.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
    print('PASS: month and week, identical inputs and complete-grid exports',flush=True)


if __name__=='__main__':main()
