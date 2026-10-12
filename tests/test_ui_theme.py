"""화면 색은 valuation/ui_theme.py 한곳에서 정한다 — 단추 색 설정·화면 CSS·계산표가 같은 값을 쓴다."""
import re
import tomllib
from pathlib import Path

from valuation.ui_theme import DEFAULT, THEMES, css_vars, theme

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_config_matches_theme():
    cfg = tomllib.loads((ROOT / '.streamlit' / 'config.toml').read_text(encoding='utf-8'))['theme']
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


def test_theme_can_be_switched_by_environment(monkeypatch):
    monkeypatch.setenv('VALUATION_THEME', 'teal')
    assert theme()['accent'] == THEMES['teal']['accent']
    monkeypatch.setenv('VALUATION_THEME', '없는이름')
    assert theme() == THEMES[DEFAULT]
