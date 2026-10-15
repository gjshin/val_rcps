"""사용 안내 — 문서와 앱 화면이 어긋나지 않게 지킨다.

안내 글의 «화면 문구»는 사용자가 화면에서 그대로 찾는 글자다. 단추 이름을 바꾸면
안내도 함께 고쳐야 하므로, 안내에 적힌 문구가 앱 소스에 실제로 있는지 본다.
"""
import re

from streamlit.testing.v1 import AppTest

import guide_ui
from test_v2_workflow import ROOT

APP_SOURCES = ['application.py', 'v2_workspace.py', 'workspace_app.py', 'market_tools_ui.py',
               'vol_app.py', 'engagement_ui.py', 'judgment_ui.py', 'guide_ui.py',
               'valuation/presentation.py', 'valuation/explain.py', 'valuation/workpaper_v2.py',
               'valuation/calculation_grid.html', 'valuation/calculation_view.py', 'valuation/service.py']


def _source():
    return '\n'.join((ROOT / p).read_text(encoding='utf-8') for p in APP_SOURCES)


def test_guide_has_steps_and_every_picture_exists():
    title, intro, pages = guide_ui.load()
    assert title and len(pages) >= 10
    names = [name for name, _ in pages]
    assert len(set(names)) == len(names)
    pictures = [p for _, lines in pages for p in guide_ui.images(lines)]
    assert len(pictures) >= 10
    for alt, path in pictures:
        assert alt.strip(), path
        assert path.is_file(), path
        assert path.stat().st_size < 400_000, f'{path.name} 이 너무 큽니다'


def test_guide_screen_words_exist_in_the_app():
    text = guide_ui.GUIDE.read_text(encoding='utf-8')
    source = _source()
    quoted = re.findall(r'«([^»]+)»', text)
    assert len(quoted) >= 60
    # 화면 문구가 아닌 인용(예시 숫자, 파일 이름, 조서 안의 문장)만 뺀다.
    examples = {'60억 원', '이렇게', '건명_평가기준일_평가입력.json'}
    missing = []
    for phrase in quoted:
        if phrase in examples:
            continue
        # «확인할 사항 … · 계산 점검» 처럼 숫자가 들어가는 자리는 … 로 적는다.
        parts = [p.strip() for p in re.split(r'…|→', phrase) if p.strip()]
        if not all(p in source for p in parts):
            missing.append(phrase)
    assert not missing, missing


def test_guide_page_opens_from_the_menu_and_moves_between_steps():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=120)
    app.run()
    menu = app.sidebar.radio(key='_app_page')
    assert '사용 안내' in menu.options
    menu.set_value('사용 안내').run()
    assert not app.exception, app.exception
    _, _, pages = guide_ui.load()
    toc = app.sidebar.radio(key='_guide_page')
    assert list(toc.options) == [name for name, _ in pages]
    assert app.header[0].value == pages[0][0]
    next(b for b in app.button if b.label.endswith(' →')).click().run()
    assert not app.exception, app.exception
    assert app.header[0].value == pages[1][0]
    assert any(b.label.startswith('← ') for b in app.button)


def test_start_screen_points_to_the_guide():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=120)
    app.run()
    assert any('사용 안내' in c.value for c in app.caption)
