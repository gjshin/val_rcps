import json
import pandas as pd
import streamlit as st
from ui_format import dataframe
from valuation.case import Case
from valuation.engagement import Engagement, calculate_engagement, engagement_bundle, value_bridge
from valuation.service import calculation_key


def main():
    st.title('여러 회차와 전기 대비 변동')
    if '_engagement' not in st.session_state:
        st.session_state._engagement = Engagement('평가용역', '')
    engagement = st.session_state._engagement
    with st.form('engagement_info'):
        name = st.text_input('용역명', engagement.name)
        entity = st.text_input('평가대상회사', engagement.entity)
        if st.form_submit_button('용역 정보 저장'):
            if name.strip() and entity.strip():
                engagement.name, engagement.entity = name, entity
                st.rerun()
            else: st.error('용역명과 회사명을 입력하십시오.')
    upload = st.file_uploader('저장한 용역 묶음 불러오기', type='json', key='engagement_upload')
    if upload and st.button('이 묶음 열기'):
        try:
            st.session_state._engagement = Engagement.from_dict(json.loads(upload.getvalue())); st.rerun()
        except (ValueError, TypeError) as exc: st.error(str(exc))
    files = st.file_uploader('회차 평가파일 여러 개 추가', type='json', accept_multiple_files=True, key='tranche_uploads')
    if files and st.button('선택한 회차 파일 추가'):
        from workspace_app import read_case
        try:
            pending = {}
            for uploaded in files:
                imported = read_case(uploaded.getvalue(), uploaded.name.removesuffix('.json'))
                identifier = imported.contract.get('tranche') or imported.name
                if identifier in engagement.cases or identifier in pending:
                    raise ValueError(f'회차 식별값이 중복됩니다: {identifier}. 각 파일의 건명·회차를 구분하십시오.')
                pending[identifier] = imported
            engagement.cases.update(pending)
            st.rerun()
        except (ValueError, TypeError, KeyError) as exc: st.error(str(exc))
    case = st.session_state.get('case')
    if case:
        with st.form('add_tranche'):
            identifier = st.text_input('회차 식별값', case.contract.get('tranche') or case.name)
            st.caption('같은 식별값을 쓰면 저장된 회차가 현재 입력으로 갱신됩니다. 같은 계약을 중복 등록하지 마십시오.')
            if st.form_submit_button('현재 평가를 회차 목록에 저장'):
                if identifier.strip():
                    engagement.cases[identifier] = Case.from_dict(case.to_dict()); st.rerun()
    if engagement.cases:
        dataframe([{'회차': k, '건명':c.name, '상품':c.effective().get('inst'), '기준일':c.effective().get('d_base'), '원금(원)':c.effective().get('face_total')} for k,c in engagement.cases.items()], hide_index=True)
        selected = st.selectbox('회차 선택', list(engagement.cases))
        c1,c2 = st.columns(2)
        if c1.button('선택 회차를 평가 작업에서 열기'):
            from workspace_app import install_case
            install_case(Case.from_dict(engagement.cases[selected].to_dict()))
            st.success('평가 작업에서 수정할 수 있습니다. 수정 후 이 목록에 다시 저장하십시오.')
        if c2.button('선택 회차를 목록에서 제거'):
            del engagement.cases[selected]; st.rerun()
        if st.button('모든 회차 평가', disabled=not engagement.entity.strip()):
            try:
                prior = st.session_state.get('_engagement_result', {})
                with st.spinner('변경한 회차를 계산하고 있습니다.'):
                    st.session_state._engagement_result = calculate_engagement(engagement, prior.get('runs'))
                st.session_state.pop('_engagement_bundle', None)
            except (ValueError, ArithmeticError) as exc: st.error(str(exc))
        result = st.session_state.get('_engagement_result')
        if result and result['key'] == engagement.fingerprint():
            dataframe(pd.DataFrame(result['rows']), hide_index=True)
            st.info(result['scope'])
            st.write({k:f'{v:,.0f}' for k,v in result['totals'].items()})
            if st.button('회차별 조서와 총괄표 생성'):
                st.session_state._engagement_bundle = (result['key'], engagement_bundle(engagement, result))
            bundle = st.session_state.get('_engagement_bundle')
            if bundle and bundle[0] == result['key']:
                st.download_button('용역 조서 묶음 저장', bundle[1], '용역조서.zip', 'application/zip')
        elif result:
            st.warning('회차 입력이 변경되었습니다. 다시 평가해야 총괄 조서를 만들 수 있습니다.')
    st.download_button('용역 입력파일 저장', json.dumps(engagement.to_dict(), ensure_ascii=False, indent=2), '용역입력.json', 'application/json')
    st.divider()
    st.subheader('현재 평가의 전기 대비 변동 원인')
    previous = st.file_uploader('전기 평가파일', type='json', key='bridge_previous')
    if previous and case:
        from workspace_app import read_case
        try:
            prev = read_case(previous.getvalue(), previous.name)
            st.caption('최대 8회 평가합니다. 전기와 당기의 전체 입력을 같은 코드로 계산해 원인별 변동을 대사합니다.')
            if st.button('변동 원인 계산'):
                with st.spinner('변동 원인을 대사하고 있습니다.'):
                    st.session_state._value_bridge = value_bridge(prev, case)
            bridge = st.session_state.get('_value_bridge')
            if bridge and bridge['previous_key'] == calculation_key(prev) and bridge['current_key'] == calculation_key(case):
                st.info(bridge['scope'])
                dataframe(pd.DataFrame(bridge['rows']), hide_index=True)
                st.download_button('변동 분석 저장', json.dumps(bridge, ensure_ascii=False, indent=2), '전기대비변동.json', 'application/json')
        except (ValueError, ArithmeticError) as exc: st.error(str(exc))
