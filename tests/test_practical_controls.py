"""Independent dated cash-flow oracles and fail-closed review/evidence tests."""
import copy
import datetime as dt
import hashlib
import io
import json
import math
import statistics
import subprocess
import sys
import zipfile
import pytest
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest
from test_v2_workflow import synthetic, ROOT
from test_engagement_evidence import sample
from valuation.case import Case
from valuation import controls, legacy
from valuation.market_data import query_spec, make_pack, validate_pack, digest, parse_price_table
from valuation.bridge import apply_volatility
from valuation.cashflows import calculate_cashflows
from valuation.service import calculate, refresh_run, export_bundle, export_final_bundle
from valuation.evidence import evidence_cards, judgment_record


def prices(asof='2025-01-01', n=30):
    end = dt.date.fromisoformat(asof)
    return [[(end-dt.timedelta(days=n-1-i)).isoformat(), 100*math.exp(.015*math.sin(i)+.002*i)] for i in range(n)]


def pack():
    query = query_spec(False, ['TEST,Example'], 30, '2025-01-01')
    return make_pack([['Example', prices()]], query, tdays=250, drop=False, pick='median', source='Synthetic source')


def event(date, **kwargs):
    row = dict(date=date, dividend_due=0., dividend_paid=0., redemption_fraction=0., payment_date='',
               profit_limit=1000., cash_limit=1000., reset_price=0., penalty_rate=0., penalty_start='')
    row.update(kwargs)
    return row


def scenario(**kwargs):
    row = dict(name='Synthetic partial redemptions', rationale='Independent dated cash-flow oracle',
               end_date='2028-01-01', terminal='redeem', opening_unpaid=0., opening_paid_dividends=0.,
               arrears_rate=0., conversion_arrears='forfeit', redemption_rate=.07, deduct_paid_dividends=True,
               redemption_arrears='included', extension_conversion=False, extension_redemption=False,
               schedule=[event('2028-01-01', redemption_fraction=1., payment_date='2028-01-01')])
    row.update(kwargs)
    return row


def prepared_case():
    case = sample()
    case.contract.update(p_s=0., p_e=24.)
    return case


def years(date):
    return (dt.date.fromisoformat(date)-dt.date(2026,1,1)).days/365


def test_volatility_independent_sample_std_and_roundtrip():
    p = pack()
    returns = [math.log(b[1]/a[1]) for a,b in zip(prices(), prices()[1:])]
    assert p['sigma'] == pytest.approx(statistics.stdev(returns)*math.sqrt(250), rel=1e-12)
    validate_pack(json.loads(json.dumps(p)))
    case = synthetic()
    case.market.pop('S0')  # Partial input preparation may receive volatility.
    case = apply_volatility(case, p)
    assert case.market_evidence['sig'] == p
    assert Case.from_dict(case.to_dict()).market_evidence == case.market_evidence
    p['series'][0][1][0][1] += 10
    assert case.market_evidence['sig']['data_sha256'] == digest(case.market_evidence['sig']['series'])


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(sigma=p['sigma']+.01),
    lambda p: p['series'][0][1][0].__setitem__(1, 0.),
    lambda p: p['series'][0][1][-1].__setitem__(0, '2025-01-02'),
    lambda p: p['series'][0][1][1].__setitem__(0, p['series'][0][1][0][0]),
    lambda p: p['opt'].update(asof='2025-01-02'),
    lambda p: p['opt'].update(tdays=0),
    lambda p: p['query']['peers'].append({'code':'MISSING', 'name':'Missing'}),
    lambda p: p['series'][0][1].pop(),
    lambda p: p.update(data_sha256='wrong'),
])
def test_bad_volatility_evidence_rejected(mutation):
    p = pack(); mutation(p)
    with pytest.raises(ValueError):
        validate_pack(p)


def test_changed_query_and_old_packages_cannot_apply():
    p = pack()
    query = query_spec(False, ['OTHER,Example'], 30, '2025-01-01')
    with pytest.raises(ValueError, match='바뀌'):
        validate_pack(p, current_query=query)
    with pytest.raises(ValueError):
        apply_volatility(synthetic(), dict(kind='vol_pack',version=1,sigma=.2,opt={'asof':'2025-01-01'}))
    query = query_spec(False, ['TEST,Example'], 30, '2025-01-10')
    with pytest.raises(ValueError, match='7일'):
        make_pack([['Example', prices()]], query, tdays=250, drop=False, pick='median', source='Test')


def test_strict_one_company_file_parser_does_not_drop_bad_cells():
    text = 'Date,One\n2025-01-01,"1,000"\n2025-01-02,1001\n'
    assert parse_price_table(text) == [['One', [['2025-01-01',1000.], ['2025-01-02',1001.]]]]
    for bad in [text.replace('1001', ''), text.replace('1001', 'nan'), text.replace('2025-01-02', '2025-01-01'), text.replace('2025-01-02', 'bad')]:
        with pytest.raises(ValueError):
            parse_price_table(bad)


def test_partial_dividend_deduction_payment_lag_independent_dated_pv():
    case = prepared_case()
    sc = scenario(opening_unpaid=5., opening_paid_dividends=2., arrears_rate=.1, schedule=[
        event('2027-01-01', dividend_due=4., dividend_paid=3., redemption_fraction=.4, payment_date='2027-03-12'),
        event('2028-01-01', dividend_due=4., dividend_paid=2., redemption_fraction=1., payment_date='2028-03-11')])
    result = calculate_cashflows(case, sc, intervals=2)
    cash1 = .4*(100*1.07-5)
    cash2 = .6*(100*1.07**2-7)
    expected = 3/1.08 + cash1/1.08**years('2027-03-12') + 1.2/1.08**2 + cash2/1.08**years('2028-03-11')
    assert result['value_100'] == pytest.approx(expected, rel=1e-12)
    assert result['timeline'][1]['상환 후 잔존비율'] == 0
    assert result['timeline'][1]['잔존100당 미지급배당'] == pytest.approx((5*1.1+4-3)*1.1+4-2)


def test_unpaid_dividends_added_only_when_explicitly_selected():
    case = prepared_case()
    sc = scenario(opening_unpaid=8.)
    included = calculate_cashflows(case, sc, intervals=2)
    sc['redemption_arrears'] = 'add'
    added = calculate_cashflows(case, sc, intervals=2)
    assert added['value_100'] - included['value_100'] == pytest.approx(8/1.08**2)


def test_residual_conversion_does_not_erase_claimed_unpaid_receivable():
    case = prepared_case(); case.contract.update(cv_s=24., cv_e=24.)
    sc = scenario(terminal='convert', redemption_rate=0., schedule=[
        event('2027-01-01', redemption_fraction=.5, payment_date='2028-03-11'), event('2028-01-01')])
    result = calculate_cashflows(case, sc, intervals=2)
    assert result['equity_cashflow_100'] == pytest.approx(50.)
    assert result['debt_cashflow_100'] == pytest.approx(50/1.08**years('2028-03-11'))


def test_conversion_before_scheduled_claim_small_tree_oracle():
    case = prepared_case(); case.contract.update(cv_s=12., cv_e=24.)
    sc = scenario(terminal='convert', redemption_rate=0., schedule=[
        event('2027-01-01', redemption_fraction=.5, payment_date='2027-01-01'), event('2028-01-01')])
    result = calculate_cashflows(case, sc, intervals=2)
    u=math.exp(.3); d=1/u; q=(1.05-d)/(u-d)
    expected=0.
    for stock, prob in [(100*u,q),(100*d,1-q)]:
        # At year 1: keep half in stock and receive cash 50, or convert all.
        equity, debt = (stock,0.) if stock > .5*stock+50 else (.5*stock,50.)
        expected += prob*(equity/1.05 + debt/1.08)
    assert result['value_100'] == pytest.approx(expected)


def test_cash_only_payments_exact_despite_grid_delay_and_penalty():
    case=prepared_case()
    sc=scenario(schedule=[event('2026-09-15',redemption_fraction=.4,payment_date='2026-12-24',penalty_rate=.12,penalty_start='2026-11-24'),
                          event('2028-01-01',redemption_fraction=1.,payment_date='2028-03-11')])
    first=.4*100*1.07**years('2026-09-15')*1.12**(30/365)/1.08**years('2026-12-24')
    last=.6*100*1.07**2/1.08**years('2028-03-11')
    for n in (4,24,120):
        result=calculate_cashflows(case,sc,intervals=n)
        assert result['value_100'] == pytest.approx(first+last, rel=1e-11)


def test_extension_and_arrears_on_conversion():
    case=prepared_case()
    sc=scenario(end_date='2029-01-01',extension_redemption=True,opening_unpaid=5.,arrears_rate=.1,redemption_arrears='add',
                schedule=[event('2029-01-01',redemption_fraction=1.,payment_date='2029-03-01')])
    expected=(100*1.07**years('2029-01-01')+5*1.1**years('2029-01-01'))/1.08**years('2029-03-01')
    assert calculate_cashflows(case,sc,intervals=3)['value_100'] == pytest.approx(expected)
    sc=scenario(terminal='convert',opening_unpaid=5.,conversion_arrears='pay',schedule=[event('2028-01-01')])
    assert calculate_cashflows(case,sc,intervals=2)['value_100'] == pytest.approx(100+5/1.08**2)


@pytest.mark.parametrize('mutation', [
    lambda s:s['schedule'][0].update(redemption_fraction=1.1),
    lambda s:s['schedule'][0].update(payment_date='2027-12-31'),
    lambda s:s['schedule'][0].update(profit_limit=20.),
    lambda s:s['schedule'][0].update(cash_limit=20.),
    lambda s:s['schedule'][0].update(dividend_paid=1.),
    lambda s:s['schedule'][0].update(redemption_fraction=.5),
    lambda s:s.update(opening_unpaid=float('nan')),
    lambda s:s['schedule'][0].update(penalty_rate=.12,penalty_start='2029-01-01'),
])
def test_infeasible_partial_cashflows_fail_closed(mutation):
    sc=scenario();mutation(sc)
    with pytest.raises(ValueError):
        calculate_cashflows(prepared_case(),sc)


def test_cashflow_serialization_and_unsupported_rights():
    case=prepared_case();case.cashflow_scenarios=[scenario()]
    assert Case.from_dict(case.to_dict()).cashflow_scenarios == case.cashflow_scenarios
    for field in ('k_w','issuer_call','rfx_mode','ipo_on','put_bdt'):
        candidate=copy.deepcopy(case);candidate.contract[field]=1 if field!='put_bdt' else candidate.contract.get(field,0)
        if field=='put_bdt':candidate.method[field]=1
        with pytest.raises(ValueError):calculate_cashflows(candidate, scenario())
    bad=case.to_dict();bad['cashflow_scenarios'][0]['schedule'][0]['date']=123
    with pytest.raises(ValueError):Case.from_dict(bad)


def ready_run(case=None):
    case = case or synthetic()
    case.sources.update({k:'Synthetic independent reference' for k in ('contract','S0','sig','rf_curve','cr_curve')})
    for key in ('S0','sig','rf_curve','cr_curve'):
        case=controls.record_control(case,'market',key=key,reviewer='Reviewer',rationale='Source matched',date=case.method['d_base'])
    for row in controls.coverage_rows(case):
        mode='excluded' if row['right'] else 'base'
        case=controls.record_control(case,'coverage',key=row['id'],reviewer='Reviewer',rationale='Contract matched',clause='§1',mode=mode)
    if controls.default_fields(case):
        case=controls.record_control(case,'defaults',reviewer='Reviewer',rationale='Defaults reviewed')
    for card in evidence_cards(case):
        case.judgments[card['id']]=judgment_record(case,'검토 완료','Synthetic conclusion','Synthetic rationale','Reviewer','§1')
    run=calculate(case)
    # The independent synthetic oracle is auto-conversion of a non-dividend stock.
    expected={'net':case.market['S0']}
    case=controls.record_verification(run,reviewer='Reviewer',reference='Hand PV: non-dividend stock, S0',values=expected,
                                      absolute_tolerance=1e-8,relative_tolerance=1e-10)
    return refresh_run(run,case)


def test_review_final_lifecycle_exact_export_and_no_extra_engine(monkeypatch):
    run=ready_run()
    assert not controls.blockers(run,require_review=False)
    assert controls.workflow_state(run.case,run)=='분석 완료 · 검토 중'
    with pytest.raises(ValueError):export_final_bundle(run)
    with pytest.raises(ValueError):controls.approve_review(run,'Same','Same','Review')
    case=controls.approve_review(run,'Preparer','Reviewer','All issues reviewed')
    run=refresh_run(run,case)
    assert controls.workflow_state(case,run)=='검토 완료'
    case=controls.finalize(run);run=refresh_run(run,case)
    assert controls.is_final(run)
    monkeypatch.setattr(legacy,'decompose',lambda *_: (_ for _ in ()).throw(AssertionError('Pricing on export')))
    blob=export_final_bundle(run)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert json.loads(z.read('result.json'))['status']=='reviewed_final_values'
        for name,sha in json.loads(z.read('manifest.json')).items():
            assert hashlib.sha256(z.read(name)).hexdigest()==sha
        wb=load_workbook(io.BytesIO(z.read('final_values.xlsx')))
        assert wb['확정상태']['B1'].value=='최종 값 조서'
        assert wb['독립검산대사']['C2'].value==40
    # Importing a final case preserves the same review state on a fresh calculation.
    imported=Case.from_dict(case.to_dict())
    assert imported.review_controls==case.review_controls


@pytest.mark.parametrize('change', [
    lambda c:c.sources.update(S0='Changed source'),
    lambda c:c.market.update(S0=41.),
    lambda c:c.review_controls['verification'].update(reference='Changed independent reference'),
    lambda c:c.judgments['classification'].update(rationale='Changed conclusion'),
    lambda c:c.review_controls['coverage']['conversion'].update(rationale='Changed mapping'),
    lambda c:c.cashflow_scenarios.append(scenario()),
])
def test_final_record_invalidated_by_any_material_change(change):
    run=ready_run();run=refresh_run(run,controls.approve_review(run,'Preparer','Reviewer','Reviewed'))
    run=refresh_run(run,controls.finalize(run))
    case=Case.from_dict(run.case.to_dict());change(case)
    changed=calculate(case) if case.market['S0']!=run.case.market['S0'] else refresh_run(run,case)
    assert not controls.is_final(changed)
    with pytest.raises(ValueError):export_final_bundle(changed)


def test_unresolved_and_conditional_rights_block_final_but_allow_draft():
    case=synthetic();case.additional_rights=[dict(kind='distributable_profit',clause='§3',treatment='unresolved',rationale='',assumption_fields=[])]
    run=ready_run(case)
    assert any(r['code'].startswith('unresolved:') for r in controls.blockers(run))
    assert export_bundle(run)
    row=next(r for r in controls.coverage_rows(run.case) if r['right'])
    case=controls.record_control(run.case,'coverage',key=row['id'],reviewer='Reviewer',rationale='Conditional schedule',clause='§3',mode='conditional')
    run=refresh_run(run,case)
    assert any(r['code'].startswith('conditional:') for r in controls.blockers(run))
    with pytest.raises(ValueError):controls.finalize(run)


def test_bad_independent_reconciliation_blocks_review():
    run=ready_run()
    bad=controls.record_verification(run,reviewer='Reviewer',reference='Wrong model',values={'net':30.},absolute_tolerance=.01,relative_tolerance=.001)
    run=refresh_run(run,bad)
    assert any(r['code']=='verification' for r in controls.blockers(run))
    with pytest.raises(ValueError):controls.approve_review(run,'A','B','Done')


def test_stored_volatility_must_match_effective_input():
    case=apply_volatility(synthetic(),pack());case.market['sig']+=.01
    run=calculate(case)
    assert any(r['code']=='volatility_evidence' for r in controls.blockers(run))


def test_modified_result_cannot_be_final_exported():
    run=ready_run();run=refresh_run(run,controls.approve_review(run,'Preparer','Reviewer','Reviewed'))
    run=refresh_run(run,controls.finalize(run))
    run.summary['amounts_total']['net']+=100
    with pytest.raises(ValueError,match='일치'):
        export_final_bundle(run)


def test_ui_preserves_default_provenance_and_blocks_final():
    case=synthetic();case.contract.pop('ipay');case.imported_defaults=['p_cmp']
    app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=30)
    app.session_state.case=case;app.run()
    app.run()
    assert 'ipay' in app.session_state.case.imported_defaults
    assert 'p_cmp' in app.session_state.case.imported_defaults
    app.radio(key='_workflow_stage').set_value('평가·분석').run()
    next(b for b in app.button if b.label=='현재 입력으로 평가').click().run()
    app.radio(key='_workflow_stage').set_value('조서 출력').run()
    assert not app.exception
    assert next(b for b in app.button if b.label=='현재 결과 최종 확정').disabled


def test_volatility_ui_query_change_invalidates_apply(monkeypatch):
    monkeypatch.setattr(legacy,'fetch_prices',lambda *a: (prices(), 'Synthetic adjusted close'))
    app=AppTest.from_file(str(ROOT/'vol_app.py'),default_timeout=30)
    app.session_state.case=synthetic();app.session_state['_app_embedded']=True
    app.run()
    next(t for t in app.text_area if t.label.startswith('피어 목록')).set_value('TEST,Example')
    next(n for n in app.number_input if n.label=='조회 일수 (거래일)').set_value(30)
    app.run()
    next(b for b in app.button if b.label=='주가 받기').click().run()
    assert not app.exception
    assert any(b.label=='이 변동성을 현재 평가에 적용' for b in app.button)
    next(d for d in app.date_input if d.label.startswith('조회 종료일')).set_value(dt.date(2025,1,2)).run()
    assert not any(b.label=='이 변동성을 현재 평가에 적용' for b in app.button)
    assert any('바뀌' in w.value for w in app.warning)


def test_final_cli_refuses_draft_without_writing(tmp_path):
    path=tmp_path/'draft.json';output=tmp_path/'final.zip'
    path.write_text(json.dumps(synthetic().to_dict()))
    done=subprocess.run([sys.executable,str(ROOT/'tools/run_case.py'),str(path),'--final','--out',str(output)],capture_output=True,text=True)
    assert done.returncode==2
    assert not output.exists()


def test_yahoo_ingestion_rejects_invalid_close_without_silently_filtering(monkeypatch):
    import types
    import pandas as pd
    fake = types.ModuleType('yfinance')
    frame = pd.DataFrame({'Close':[100.+i for i in range(30)]},index=pd.date_range('2024-12-03',periods=30))
    fake.download=lambda *a,**kw: frame
    fake.__version__='synthetic'
    monkeypatch.setitem(sys.modules,'yfinance',fake)
    rows,source=legacy.fetch_prices('TEST',30,'','2025-01-01')
    assert len(rows)==30 and 'TEST' in source
    for bad in (0.,float('nan'),-1.):
        frame.iloc[5,0]=bad
        with pytest.raises(RuntimeError,match='누락'):
            legacy.fetch_prices('TEST',30,'','2025-01-01')
    frame=frame.rename(columns={'Close':'Open'})
    with pytest.raises(RuntimeError,match='Close'):
        legacy.fetch_prices('TEST',30,'','2025-01-01')


def test_final_case_roundtrip_and_cli_positive(tmp_path):
    run=ready_run();run=refresh_run(run,controls.approve_review(run,'Preparer','Reviewer','Reviewed'))
    run=refresh_run(run,controls.finalize(run))
    case=Case.from_dict(json.loads(json.dumps(run.case.to_dict())))
    reloaded=calculate(case)
    assert controls.is_final(reloaded)
    path=tmp_path/'case.json';output=tmp_path/'final.zip'
    path.write_text(json.dumps(case.to_dict()))
    done=subprocess.run([sys.executable,str(ROOT/'tools/run_case.py'),str(path),'--final','--out',str(output)],capture_output=True,text=True)
    assert done.returncode==0,done.stderr
    with zipfile.ZipFile(output) as z:
        assert json.loads(z.read('result.json'))['status']=='reviewed_final_values'


def test_cashflow_cli_matches_independent_pv(tmp_path):
    case=prepared_case();case.cashflow_scenarios=[scenario()]
    path=tmp_path/'case.json';output=tmp_path/'cashflow.json'
    path.write_text(json.dumps(case.to_dict()))
    done=subprocess.run([sys.executable,str(ROOT/'tools/run_engagement.py'),str(path),'--cashflow',case.cashflow_scenarios[0]['name'],'--out',str(output)],capture_output=True,text=True)
    assert done.returncode==0,done.stderr
    result=json.loads(output.read_text())
    assert result['value_100']==pytest.approx(100*1.07**2/1.08**2)


def test_conversion_window_uses_calendar_dates_across_leap_year():
    case=prepared_case()
    case.contract.update(d_issue='2027-01-01',d_mat='2029-01-01',cv_s=24.,cv_e=24.,p_e=24.)
    case.method['d_base']='2027-01-01'
    sc=scenario(end_date='2029-01-01',redemption_rate=0.,schedule=[event('2029-01-01',redemption_fraction=1.,payment_date='2029-01-01')])
    T=731/365;h=T/2;u=math.exp(.3*math.sqrt(h));d=1/u;q=(1.05**h-d)/(u-d)
    expected=0.
    for j,prob in [(0,(1-q)**2),(1,2*q*(1-q)),(2,q*q)]:
        S=100*u**j*d**(2-j)
        expected+=prob*(S/1.05**T if S>100 else 100/1.08**T)
    assert calculate_cashflows(case,sc,intervals=2)['value_100']==pytest.approx(expected)
