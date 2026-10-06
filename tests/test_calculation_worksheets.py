import copy
import json
import math
from pathlib import Path
import pytest
from valuation import legacy as L
from valuation.service import calculate
from valuation.explain import node_trace
from valuation.calculation_view import node_values, schedule_values, bootstrap_values, worksheet_html
from test_commercial_v2 import case


@pytest.mark.parametrize('model', ['TF', 'GS'])
def test_entire_grid_uses_saved_nodes_and_real_references(model, monkeypatch):
    run=calculate(case(model=model,d_mat='2027-01-01',cv_e=12.,p_e=11.,p_s=6.))
    before=copy.deepcopy(run.raw['full']['memo'])
    monkeypatch.setattr(L,'engine',lambda *a,**k:pytest.fail('Display repriced the case'))
    view=node_values(run)
    assert len(view['nodes'])==len(before)
    for key,n in view['nodes'].items():
        tr=node_trace(run,n['i'],n['j'])
        assert key==f"{n['i']},{n['i']-n['j']}"
        assert n['value']==tr['value']
        if not n['terminal']:
            assert n['hold']==pytest.approx(tr['hold'],abs=1e-10)
            assert n['up']==list(tr['up_key']) and n['dn']==list(tr['down_key'])
            assert n['df_up']==pytest.approx(math.exp(-tr['gs_rates'][0]*view['delta']))
    assert before==run.raw['full']['memo']
    html=worksheet_html(view)
    payload=json.loads(html.split('type="application/json">')[1].split('</script>')[0])
    assert payload['nodes']['0,0']['value']==run.raw['full'][model]
    assert 'NaN' not in html


def test_segment_keeps_next_column_dependencies_and_terminal_absence():
    run=calculate(case(d_mat='2027-01-01',cv_e=12.,p_s=6.,p_e=11.))
    view=node_values(run,3,6)
    assert {n['i'] for n in view['nodes'].values()}==set(range(3,8))
    assert all(n['hold'] is None for n in node_values(run)['nodes'].values() if n['terminal'])


def test_schedule_matches_grid_dates_payments_and_probabilities(monkeypatch):
    run=calculate(case(gap_m=3.,d_mat='2027-01-01',cv_e=12.,p_s=6.,p_e=11.))
    monkeypatch.setattr(L,'engine',lambda *a,**k:pytest.fail('Schedule repriced'))
    view=schedule_values(run);t=run.terms
    assert view['steps'][0]['host']==pytest.approx(run.raw['b0'],abs=1e-9)
    assert view['steps'][0]['date']==t.d_base and view['steps'][-1]['date']==t.d_mat
    assert view['steps'][-1]['q'] is None and view['steps'][-1]['df_cr'] is None
    assert not any(s['call'] or s['call_contract'] is not None for s in view['steps'])
    pays=L.pay_steps(t,t.n,view['delta'])
    for s in view['steps']:
        assert s['coupon']==100*L.eff_cpn(t)*t.ipay/12*pays.get(s['i'],0)
        if s['i']<t.n:assert s['q']==run.raw['full']['qi'](s['i'])


@pytest.mark.parametrize('kind',['par','spot'])
def test_bootstrap_regular_short_and_stub_reconcile_to_applied_curve(kind):
    run=calculate(case(y_type=kind,cmp_rf=2,cmp_cr=4,rf_curve=[[.25,.02],[.75,.025],[1.25,.029],[7.,.034]],cr_curve=[[.1,.04],[.4,.047],[1.1,.06],[7.,.07]]))
    data=bootstrap_values(run)
    rf,cr=L.curves(run.terms)
    for c,curve in zip(data['curves'],[rf,cr]):
        for row in c['rows']:
            assert row['spot']==pytest.approx(curve(row['t']))
            if row['kind'] in ('grid','stub'):
                pv=row['c']*sum(x['weight']*x['df'] for x in row['refs'])
                assert (1-pv)/(1+row['c'])==pytest.approx(row['df'])
            assert math.exp(-row['spot']*row['t'])==pytest.approx(row['df'])
    if kind=='par':assert {'grid','zero','stub'}<={r['kind'] for r in data['curves'][0]['rows']}
    else:assert {r['kind'] for r in data['curves'][0]['rows']}=={'spot'}


def test_ui_all_worksheets_preserve_full_precision_and_same_run(monkeypatch):
    from streamlit.testing.v1 import AppTest
    run=calculate(case(S0=20000.123456789,sig=.35123456789,d_mat='2027-01-01',cv_e=12.,p_s=6.,p_e=11.))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
    app.session_state['case']=run.case;app.session_state['run']=run
    app.session_state['_workflow_stage']='계산내역'
    monkeypatch.setattr(L,'engine',lambda *a,**k:pytest.fail('UI repriced'))
    app.run()
    for topic in ('노드 계산표','노드 스케줄','부트스트래핑','구성요소'):
        app.radio(key='_calc_topic').set_value(topic).run()
        assert not app.exception
        assert app.session_state['run'].summary['run_id']==run.summary['run_id']
    app.radio(key='_workflow_stage').set_value('입력·시장자료').run()
    assert not app.exception
    assert app.session_state['case'].market['S0']==20000.123456789
    assert app.session_state['case'].market['sig']==.35123456789
