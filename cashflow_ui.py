import datetime as dt
import json
import pandas as pd
import streamlit as st
from valuation.case import Case
from valuation.cashflows import SCOPE, calculate_cashflows, validate_scenario, scenario_key

COLUMNS = {'date': '발생·청구일', 'dividend_due': '배당발생(잔존100당)', 'dividend_paid': '배당지급(잔존100당)',
    'redemption_fraction': '잔존물량 중 상환비율(0~1)', 'payment_date': '실제 지급일',
    'profit_limit': '이익 한도(최초100당)', 'cash_limit': '현금 한도(최초100당)', 'reset_price': '조정 전환가액(원, 0=유지)',
    'penalty_rate': '지연 가산 연이율(0.12=12%)', 'penalty_start': '지연 가산 시작일'}


def main(case):
    from workspace_app import save_case
    st.info(SCOPE)
    st.write('배당은 사건일 현재 잔존 물량의 원금 100당, 이익·현금 한도는 최초 원금 100당입니다. 예: 잔존물량의 절반 상환은 0.5를 입력합니다. 청구 후 지급까지 전환권이 유지되는 계약에는 이 분석을 사용할 수 없습니다.')
    st.caption('기준일에 이미 상환청구된 미수금은 이 계산의 기초 물량에 포함하지 마십시오. 잔존 RCPS와 별도로 평가·대사해야 합니다.')
    st.caption('상환금액 = 최초 발행가 기준 100 × (1+상환이율)^발행일부터 청구일까지 연수 − 기지급배당 + 별도 가산할 미지급배당. 실제일수/365 연복리입니다. 7% 약정에 배당이 포함되면 미지급배당을 다시 더하지 마십시오.')
    selected = st.selectbox('부분상환 분석 선택', ['새 분석']+[s['name'] for s in case.cashflow_scenarios])
    saved = next((s for s in case.cashflow_scenarios if s['name'] == selected), {})
    prefix = str(st.session_state.get('revision', 0)) + selected
    with st.form('partial_' + prefix):
        name = st.text_input('부분상환 분석 이름', saved.get('name', ''))
        rationale = st.text_area('조항·배당·이익·지급일·연장 가정의 근거', saved.get('rationale', ''))
        a, b = st.columns(2)
        end = a.date_input('전환·상환청구 분석 종료일', value=dt.date.fromisoformat(saved.get('end_date', case.contract.get('d_mat', dt.date.today().isoformat()))))
        terminal = b.selectbox('잔존 물량 종료 처리', ['redeem', 'convert'], index=['redeem', 'convert'].index(saved.get('terminal', 'redeem')), format_func=lambda x: {'redeem':'전량 상환청구', 'convert':'자동 전환'}[x])
        opening = a.number_input('기초 미지급배당(잔존100당)', min_value=0., value=float(saved.get('opening_unpaid', 0)))
        paid = b.number_input('기초 기지급배당 누적(잔존100당)', min_value=0., value=float(saved.get('opening_paid_dividends', 0)))
        arrears = a.number_input('미지급배당 가산 연이율(%)', min_value=0., value=float(saved.get('arrears_rate', 0))*100)
        redemption = b.number_input('상환 연복리 이율(%)', min_value=0., value=float(saved.get('redemption_rate', case.effective().get('p_yield', 0)))*100)
        policy = a.selectbox('전환 시 미지급배당', ['forfeit', 'pay'], index=['forfeit', 'pay'].index(saved.get('conversion_arrears', 'forfeit')), format_func=lambda x: {'forfeit':'소멸', 'pay':'전환 시 현금 지급'}[x])
        add = b.selectbox('상환 시 미지급배당', ['included', 'add'], index=['included', 'add'].index(saved.get('redemption_arrears', 'included')), format_func=lambda x: {'included':'약정 상환금에 포함 — 다시 가산하지 않음', 'add':'약정 상환금에 추가 지급'}[x])
        deduct = st.checkbox('상환금액에서 기지급배당 차감', value=saved.get('deduct_paid_dividends', True))
        cv_extension = st.checkbox('만기 연장 기간에도 전환 가능', value=saved.get('extension_conversion', False))
        put_extension = st.checkbox('만기 연장 기간에도 상환청구 가능', value=saved.get('extension_redemption', False))
        st.caption('날짜: YYYY-MM-DD. 상환 없는 행의 지급일·가산 시작일은 빈칸, 숫자는 0. 모든 행의 날짜가 달라야 하며 마지막 행은 종료일입니다. 상환 전액 청구 후 지급일은 종료일보다 늦을 수 있습니다. 한도는 지급일에 그 지급건에 배분할 금액을 입력합니다.')
        edited = st.data_editor(pd.DataFrame(saved.get('schedule', []), columns=COLUMNS).rename(columns=COLUMNS), num_rows='dynamic', hide_index=True, key='partial_grid_'+prefix)
        submitted = st.form_submit_button('부분상환 일정 저장')
    if submitted:
        try:
            rows = edited.rename(columns={v:k for k,v in COLUMNS.items()}).to_dict('records')
            for row in rows:
                for key in COLUMNS:
                    row[key] = ('' if pd.isna(row[key]) else str(row[key])) if key in {'date','payment_date','penalty_start'} else float(row[key])
            scenario = dict(name=name, rationale=rationale, end_date=end.isoformat(), terminal=terminal, opening_unpaid=opening,
                opening_paid_dividends=paid, arrears_rate=arrears/100, conversion_arrears=policy, redemption_rate=redemption/100,
                deduct_paid_dividends=deduct, redemption_arrears=add, extension_conversion=cv_extension, extension_redemption=put_extension, schedule=rows)
            validate_scenario(case, scenario)
            candidate = Case.from_dict(case.to_dict())
            candidate.cashflow_scenarios = [s for s in candidate.cashflow_scenarios if s['name'] != name] + [scenario]
            save_case(candidate)
        except (ValueError, TypeError, KeyError) as exc:
            st.error(str(exc))
    if saved:
        if st.button('이 부분상환 일정 삭제'):
            candidate = Case.from_dict(case.to_dict())
            candidate.cashflow_scenarios = [s for s in candidate.cashflow_scenarios if s['name'] != saved['name']]
            save_case(candidate)
        if st.button('부분상환·지급시차 분석 실행', type='primary'):
            try:
                with st.spinner('상환청구·지급시차·잔존 전환권을 계산하고 있습니다.'):
                    st.session_state.partial_result = calculate_cashflows(case, saved)
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))
        result = st.session_state.get('partial_result')
        if result and result['key'] == scenario_key(case, saved):
            st.metric('입력 일정에 따른 조건부 가치(원)', f"{result['value_total']:,.0f}")
            st.caption(f"1주당 {result['value_per_share']:,.2f}원 · 격자 {result['intervals']}구간 · 사건일 최대 이연 {result['max_grid_delay_days']:.2f}일")
            if result['max_grid_delay_days'] > 1:
                st.warning('행사·전환 비교에는 격자 이연이 있습니다. 간격을 줄인 결과와 대조하십시오. 고정 현금흐름 할인은 실제 지급일을 사용합니다.')
            if result['curve_extrapolation']:
                st.warning('마지막 지급일이 입력 신용금리곡선 만기 밖입니다. 장기 금리 연장 가정을 검토하십시오.')
            st.dataframe(pd.DataFrame(result['timeline']), hide_index=True)
            st.download_button('부분상환 분석 입력·결과 저장', json.dumps(result, ensure_ascii=False, indent=2), '부분상환분석.json', 'application/json')
            st.download_button('부분상환 현금흐름 대사표 저장', pd.DataFrame(result['timeline']).to_csv(index=False).encode('utf-8-sig'), '부분상환현금흐름.csv', 'text/csv')
