import datetime as dt
import json
import pandas as pd
import streamlit as st
from valuation.case import Case
from valuation.contract_analysis import calculate_schedule, scenario_key, SCOPE

COLUMNS = {'date': '사건일', 'dividend_due': '배당 발생(100당)', 'dividend_paid': '배당 지급(100당)',
           'redemption_allowed': '상환 허용', 'redemption_amount': '상환 원금·할증(100당)',
           'profit_limit': '배당지급 후 가용이익(100당)', 'cash_limit': '배당지급 후 가용재원(100당)', 'reset_price': '새 전환가액(원, 0=유지)'}


def main():
    st.title('별도 계약조건의 영향 분석')
    case = st.session_state.get('case')
    if case is None:
        st.info('평가 작업에서 평가파일을 먼저 열어 주십시오.'); return
    st.info(SCOPE)
    st.write('전체 배당·상환 일정을 입력하고, 미지급배당의 이자·연장 종료일·사건별 전환가액을 지정합니다. 금액은 원금 100당이며 가용재원은 이 회차에 배분되는 금액입니다.')
    st.caption('기존 배당·상환 일정은 아래 표로 대체합니다. 현재 평가의 전환 행사기간은 유지합니다. 연장 기간의 전환권은 별도 확인하십시오. 상환청구일과 지급일이 다르면 이 분석으로 두 날짜 사이의 권리 소멸을 표현할 수 없습니다.')
    options = ['새 일정']+[s['name'] for s in case.contract_scenarios]
    chosen = st.selectbox('저장한 분석', options)
    saved = next((s for s in case.contract_scenarios if s['name'] == chosen), {})
    end_default = saved.get('end_date', case.contract.get('d_mat', dt.date.today().isoformat()))
    prefix = str(st.session_state.get('revision', 0)) + '_' + chosen
    with st.form('contract_schedule_' + prefix):
        name = st.text_input('분석 이름', saved.get('name', ''))
        rationale = st.text_area('계약 조항·일정 가정의 근거', saved.get('rationale', ''))
        end = st.date_input('분석 종료일(연장 시 연장 종료일)', value=dt.date.fromisoformat(end_default))
        terminal = st.selectbox('종료 시 처리', ['redeem', 'convert'], index=['redeem', 'convert'].index(saved.get('terminal', 'redeem')), format_func=lambda x: {'redeem':'전액 상환', 'convert':'보통주 전환'}[x])
        opening = st.number_input('기준일 미지급 배당 잔액(원금 100당)', min_value=0., value=float(saved.get('opening_unpaid', 0)))
        rate = st.number_input('미지급 배당 가산 연이율(%, 연복리)', min_value=0., value=float(saved.get('arrears_rate', 0))*100)
        policy = st.selectbox('전환 시 미지급 배당', ['forfeit', 'pay'], index=['forfeit', 'pay'].index(saved.get('conversion_arrears', 'forfeit')), format_func=lambda x: {'forfeit':'소멸', 'pay':'현금 지급'}[x])
        table = pd.DataFrame(saved.get('schedule', []), columns=COLUMNS).rename(columns=COLUMNS)
        edited = st.data_editor(table, num_rows='dynamic', column_config={'사건일': st.column_config.TextColumn(help='YYYY-MM-DD'), '상환 허용': st.column_config.CheckboxColumn(default=False)}, key='schedule_grid_' + prefix)
        submitted = st.form_submit_button('일정 저장')
    if submitted:
        from valuation.contract_analysis import validate_scenario
        try:
            rows = edited.rename(columns={v:k for k,v in COLUMNS.items()}).to_dict('records')
            for row in rows:
                for key in COLUMNS:
                    if key not in {'date', 'redemption_allowed'}:
                        row[key] = float(row[key])
            scenario = dict(name=name, rationale=rationale, end_date=end.isoformat(), terminal=terminal,
                            opening_unpaid=opening, arrears_rate=rate/100, conversion_arrears=policy, schedule=rows)
            validate_scenario(case, scenario)
            candidate = Case.from_dict(case.to_dict())
            candidate.contract_scenarios = [s for s in candidate.contract_scenarios if s['name'] != name]+[scenario]
            from workspace_app import save_case
            save_case(candidate)
        except (ValueError, TypeError, KeyError) as exc:
            st.error(str(exc))
    if saved:
        if st.button('선택한 일정 삭제'):
            candidate = Case.from_dict(case.to_dict())
            candidate.contract_scenarios = [s for s in candidate.contract_scenarios if s['name'] != saved['name']]
            from workspace_app import save_case
            save_case(candidate)
        if st.button('저장한 일정으로 분석'):
            try:
                with st.spinner('계약조건의 영향을 계산하고 있습니다.'):
                    st.session_state._scenario_result = calculate_schedule(case, saved)
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))
        result = st.session_state.get('_scenario_result')
        if result and result['key'] == scenario_key(case, saved):
            st.metric('입력 일정이 실현될 경우의 조건부 가치(원)', f"{result['value_total']:,.0f}")
            st.caption(f"1주당 {result['value_per_share']:,.2f}원 · 격자 {result['intervals']}구간 · 사건일 최대 이연 {result['max_grid_delay_days']:.2f}일")
            st.dataframe(pd.DataFrame(result['timeline']), hide_index=True)
            st.download_button('분석 입력·결과 저장', json.dumps(result, ensure_ascii=False, indent=2), '계약조건분석.json', 'application/json')
            csv = pd.DataFrame(result['timeline']).to_csv(index=False).encode('utf-8-sig')
            st.download_button('현금흐름 대사표 저장', csv, '계약조건현금흐름.csv', 'text/csv')
