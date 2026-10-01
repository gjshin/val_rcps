"""The contract review handoff; original documents stay outside this app."""
import streamlit as st
from valuation.presentation import label, display_value, issue_rows
from valuation.case import inspect_case


def main(case):
    st.subheader('계약 검토 및 입력안')
    st.write('계약서와 참고자료는 업무파트너와 검토한 뒤, 확인된 조건을 평가 입력파일로 가져옵니다. 이 화면은 계약 원문을 업로드하거나 외부 AI로 전송하지 않습니다.')
    st.markdown('1. 이 대화에서 계약서·변경계약·주주간계약을 검토합니다.\n2. 조항별 해석, 입력값, 검토사항이 포함된 평가 입력파일(JSON)을 받습니다.\n3. 왼쪽 **평가파일 불러오기**에서 열고 **입력·시장자료**에서 미확인 조건과 시장자료를 보완합니다.')
    st.caption('입력파일에도 고객 정보와 조항 발췌가 포함될 수 있으므로 원문과 같은 기준으로 관리하십시오. 앱 서버 운영자와 접근권한을 확인한 환경에서 사용하십시오.')
    findings = case.contract_review.get('findings', [])
    if findings:
        st.subheader('조항별 검토안')
        for idx, row in enumerate(findings, 1):
            with st.expander(f"{idx}. {row['document']} · {row['clause']}"):
                st.write('원문 발췌'); st.text(row['quote'])
                st.write('계약 해석: ' + row['interpretation'])
                st.write('후속 작업: ' + row['action'])
                st.dataframe([{'입력항목': label(k), '계약 입력': display_value(k, case.facts().get(k), case.contract.get('d_issue'), case.facts().get('inst')), '계산 적용값': display_value(k, case.effective().get(k), case.contract.get('d_issue'), case.facts().get('inst'))} for k in row['fields']], hide_index=True)
        st.caption('검토안은 검토자의 서명이나 승인 기록이 아닙니다. 「검토조서」에서 결론과 검토자를 기록하십시오.')
    else:
        st.info('조항별 검토안이 없는 평가파일입니다. 기존 입력은 그대로 사용할 수 있습니다. 자료 출처와 추가 확인사항을 기록하십시오.')
    for item in case.contract_review.get('open_items', []):
        st.warning(item)
    if case.contract_review.get('open_items'):
        with st.form('resolve_contract_item'):
            item = st.selectbox('처리한 추가 확인사항', case.contract_review['open_items'])
            reviewer = st.text_input('확인자')
            basis = st.text_area('확인자료·처리결과 및 입력 반영 내역')
            if st.form_submit_button('확인사항 처리 기록'):
                if not reviewer.strip() or not basis.strip():
                    st.error('확인자와 처리 근거를 입력하십시오.')
                else:
                    from valuation.case import Case
                    from workspace_app import save_case
                    candidate = Case.from_dict(case.to_dict())
                    candidate.contract_review['open_items'].remove(item)
                    candidate.notes += f'\n계약 추가 확인 처리 / {reviewer}: {item}\n{basis}'
                    save_case(candidate)
    st.subheader('계약·시장자료 입력 현황')
    issues = [i for i in inspect_case(case) if i.severity == 'error']
    if issues:
        st.dataframe(issue_rows(issues), hide_index=True)
    else:
        st.success('계산에 필요한 입력 형식이 갖추어져 있습니다. 계약 해석과 시장자료 적정성은 별도로 검토하십시오.')
    with st.expander('입력항목과 출처 대조'):
        st.dataframe([{'입력항목': label(k), '현재 입력': display_value(k, v, case.contract.get('d_issue'), case.facts().get('inst')), '출처·근거': case.sources.get(k, '')} for k,v in case.facts().items()], hide_index=True)
