"""Single evaluation workflow with shared input and pricing result."""
from pathlib import Path
import runpy
import streamlit as st

ROOT = Path(__file__).parent
PAGES = ['평가 작업', '여러 회차·변동 분석']
APP_VERSION = '2026.10.06-v2.2'


def detailed(run):
    st.subheader('상세 계산 및 회계 참고표')
    st.caption('회계 참고표는 입력한 가정에 따른 초안입니다. 민감도·추가 검산은 실행 버튼을 눌렀을 때 계산합니다.')
    runpy.run_path(str(ROOT / 'legacy_app.py'), run_name='__main__', init_globals={'_WORKSPACE_RUN': run})


def main():
    st.set_page_config(page_title='복합금융상품 평가', layout='wide')
    from v2_workspace import CSS
    st.markdown(CSS, unsafe_allow_html=True)
    st.session_state._app_embedded = True
    with st.sidebar.expander('업무 선택'):
        page = st.radio('업무 선택', PAGES, key='_app_page')
    if page == PAGES[0]:
        from v2_workspace import main as workspace
        workspace()
    else:
        import engagement_ui
        engagement_ui.main()
