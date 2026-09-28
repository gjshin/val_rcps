"""Single entry point; only the selected tool is executed."""
from pathlib import Path
import runpy
from dataclasses import asdict
import streamlit as st

ROOT = Path(__file__).parent
PAGES = ['평가 작업', '판단 근거', '계약조건 분석', '여러 회차·변동 분석', '상세 기능', '변동성 산출']


def detailed():
    from valuation.legacy import Terms
    from valuation.bridge import changes_from_terms, apply_changes
    from valuation.presentation import label, display_value
    from workspace_app import save_case
    st.subheader('기존 상세 기능')
    st.caption('금리곡선·등급 보간 / 주가·변동성 / 주가 역산 / 구성요소 / 회계 참고표 / 분리 검토 / 행사 분포 / 상각표 / 민감도 / 검산 / 상세 조서')
    case = st.session_state.get('case')
    if case is None:
        st.info('평가 작업에서 평가파일을 먼저 열어 주십시오.'); return
    if st.session_state.get('_bridge_case') != case.fingerprint():
        st.info('현재 평가를 상세 기능에 가져오면 같은 입력으로 역산·금리·회계 참고표·상세 분석을 사용할 수 있습니다.')
        if st.button('현재 평가를 상세 기능에 가져오기'):
            for key in st.session_state.get('_bridge_legacy_keys', []):
                st.session_state.pop(key, None)
            st.session_state.tm = Terms(**case.effective())
            st.session_state.blank = set()
            for src, target in [('rf_curve', 'rf_txt'), ('cr_curve', 'cr_txt'), ('cr_curve', 'ca_txt'), ('cr_curve_b', 'cb_txt')]:
                st.session_state[target] = '\n'.join(f'{x:g}\t{y*100:.10g}%' for x, y in getattr(st.session_state.tm, src))
            st.session_state._bridge_baseline = asdict(st.session_state.tm)
            st.session_state._bridge_case = case.fingerprint()
            st.rerun()
        return
    st.caption('상세 기능은 선택한 항목만 엽니다. 변경한 입력은 아래 반영 버튼으로 평가 작업에 전달합니다.')
    changes = changes_from_terms(case, st.session_state.tm, st.session_state._bridge_baseline)
    with st.expander(f'평가 작업에 반영할 변경 {len(changes)}개', expanded=bool(changes)):
        if changes:
            st.dataframe([{'항목': label(k), '기존 적용값': display_value(k, st.session_state._bridge_baseline.get(k)), '새 적용값': display_value(k, v)} for k,v in sorted(changes.items())], hide_index=True)
            if st.button('이 변경을 현재 평가에 반영'):
                try:
                    candidate = apply_changes(case, changes)
                    st.session_state._bridge_baseline = asdict(st.session_state.tm)
                    st.session_state._bridge_case = candidate.fingerprint()
                    save_case(candidate)
                except ValueError as exc:
                    st.error(str(exc))
    protected = set(st.session_state) - set(st.session_state.get('_bridge_legacy_keys', []))
    protected |= {k for k in st.session_state if k.startswith('_bridge') or k.startswith('_app')}
    st.session_state._bridge_protected = protected
    try:
        runpy.run_path(str(ROOT / 'legacy_app.py'), run_name='__main__')
    finally:
        st.session_state._bridge_legacy_keys = list(set(st.session_state) - protected)


def main():
    st.set_page_config(page_title='복합금융상품 평가', layout='wide')
    st.session_state._app_embedded = True
    page = st.sidebar.radio('업무 선택', PAGES, key='_app_page')
    if st.session_state.get('_app_last_page') == '상세 기능' and page != '상세 기능':
        for key in ['_legacy_price', '_legacy_price_key', '_legacy_authorized', '_legacy_warn', '_legacy_warn_key']:
            st.session_state.pop(key, None)
    st.session_state._app_last_page = page
    # Preserve the data owned by the app when Streamlit cleans up hidden widgets.
    if page == PAGES[0]:
        from workspace_app import main as workspace
        workspace()
    elif page == PAGES[1]:
        import evidence_ui
        evidence_ui.main()
    elif page == PAGES[2]:
        import scenario_ui
        scenario_ui.main()
    elif page == PAGES[3]:
        import engagement_ui
        engagement_ui.main()
    elif page == PAGES[4]:
        detailed()
    else:
        runpy.run_path(str(ROOT / 'vol_app.py'), run_name='__main__')
