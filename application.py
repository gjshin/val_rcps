"""Single evaluation workflow with shared input and pricing result."""
from pathlib import Path
import runpy
import streamlit as st

ROOT = Path(__file__).parent
PAGES = ['평가 작업', '여러 회차·변동 분석']
APP_VERSION = '2026.09.29-unified.1'


def detailed(run):
    st.subheader('상세 계산 및 회계 참고표')
    st.caption('회계 참고표는 입력한 분류·측정 가정에 따른 계산입니다. 「검토조서」에 계약별 결론과 근거를 기록하십시오. 민감도·검산 등은 선택 시 추가 계산이 발생합니다.')
    runpy.run_path(str(ROOT / 'legacy_app.py'), run_name='__main__', init_globals={'_WORKSPACE_RUN': run})


def main():
    st.set_page_config(page_title='복합금융상품 평가', layout='wide')
    st.session_state._app_embedded = True
    st.sidebar.caption('화면 버전 ' + APP_VERSION)
    page = st.sidebar.radio('업무 선택', PAGES, key='_app_page')
    if page == PAGES[0]:
        from workspace_app import main as workspace
        workspace()
    else:
        import engagement_ui
        engagement_ui.main()
