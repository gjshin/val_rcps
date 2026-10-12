"""화면 색은 valuation/ui_theme.py 한곳에서 정한다 — 단추 색 설정·화면 CSS·계산표가 같은 값을 쓴다."""
import re
from pathlib import Path

from valuation.ui_theme import DEFAULT, THEMES, css_vars, theme

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_config_matches_theme():
    text = (ROOT / '.streamlit' / 'config.toml').read_text(encoding='utf-8')
    block = text.split('[theme]', 1)[1].split('\n[', 1)[0]                 # 파이썬 3.9 에서도 돌게 직접 읽는다
    cfg = dict(re.findall(r'^(\w+)\s*=\s*"([^"]*)"', block, flags=re.M))
    t = THEMES[DEFAULT]
    assert cfg['primaryColor'].upper() == t['accent'].upper()
    assert cfg['backgroundColor'].upper() == t['bg'].upper() and cfg['textColor'].upper() == t['ink'].upper()


def test_themes_have_the_same_tokens():
    keys = set(THEMES[DEFAULT])
    assert all(set(t) == keys for t in THEMES.values())


def test_screens_take_colors_from_theme_only():
    import v2_workspace
    css = v2_workspace.CSS.split(css_vars(), 1)[1]
    assert not re.findall(r'#[0-9a-fA-F]{6}\b', css)                    # 화면 CSS 에 색 값을 직접 적지 않는다
    page = (ROOT / 'valuation' / 'calculation_grid.html').read_text(encoding='utf-8')
    style = page.split('</style>', 1)[0]
    assert set(re.findall(r'#[0-9a-fA-F]{6,8}\b', style)) <= {'#18223830'}   # 그림자만
    from valuation.calculation_view import worksheet_html
    assert css_vars() in worksheet_html({'mode': 'nodes'}) and '__THEME__' not in worksheet_html({'mode': 'nodes'})


def test_theme_ignores_unknown_names():
    assert theme('없는이름') == THEMES[DEFAULT] and theme('teal')['accent'] == THEMES['teal']['accent']
