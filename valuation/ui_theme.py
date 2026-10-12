"""화면 색 — 한곳에서 정한다. 화면(v2_workspace)·계산표(calculation_grid.html)는 여기 값을 CSS 변수로 받는다.

엑셀 조서의 색(노란 입력 칸·파란 입력 글씨·초록 참조 수식)은 감사 관행이라 여기서 바꾸지 않는다.
배포마다 바꿀 때는 환경변수 VALUATION_THEME 에 이름을 넣는다(예: 시안 비교). 앱의 기본 단추 색은
.streamlit/config.toml 의 primaryColor 와 같아야 한다.
"""
import os

THEMES = {
    # 지금까지 쓰던 보라
    'violet': dict(
        name='보라', ink='#182238', muted='#526176', bg='#F4F6FA', surface='#FFFFFF', line='#DCE2EE', hair='#EDF0F5',
        accent='#5145CD', accent_ink='#272152', soft='#F0EEFB', softer='#F6F5FD', select='#E5DFFF',
        nav='#F9FAFF', nav_sel='#EBE7FF', nav_sel_ink='#382777', kicker='#5145CD',
        ref='#479982', ref_soft='#E2F2EE', conv='#2852A2', conv_soft='#EFF5FF', put='#915019', put_soft='#FFF5E9'),
    # 감청 — 엑셀 조서의 파란 입력 글씨와 같은 계열. 회계 조서와 한 벌로 보이게.
    'navy': dict(
        name='감청', ink='#16233A', muted='#556274', bg='#F3F5F8', surface='#FFFFFF', line='#D9E0EA', hair='#ECEFF4',
        accent='#1F4E8C', accent_ink='#12305A', soft='#EAF0F8', softer='#F3F6FB', select='#D8E5F5',
        nav='#FBFCFD', nav_sel='#E3EBF6', nav_sel_ink='#12305A', kicker='#1F4E8C',
        ref='#2F7D5B', ref_soft='#E3F2EA', conv='#1F4E8C', conv_soft='#EAF0F8', put='#9A5B13', put_soft='#FCF2E3'),
    # 청록 — 차분한 녹청. 참조 셀은 호박색으로 구분한다.
    'teal': dict(
        name='청록', ink='#15232A', muted='#536570', bg='#F2F5F5', surface='#FFFFFF', line='#D7E1E2', hair='#EBF0F0',
        accent='#0E6B63', accent_ink='#0A3F3B', soft='#E5F2F0', softer='#F1F7F6', select='#D0EAE5',
        nav='#FAFCFC', nav_sel='#DCEFEB', nav_sel_ink='#0A3F3B', kicker='#0E6B63',
        ref='#B7791F', ref_soft='#FBF1DE', conv='#1F5F8B', conv_soft='#EAF2F8', put='#9A5B13', put_soft='#FCF2E3'),
    # 먹색·금 — 단추와 글자는 짙은 회색, 선택·꼬리표만 금색으로 짚는다.
    'graphite': dict(
        name='먹색·금', ink='#1C1F24', muted='#5E636B', bg='#F4F4F2', surface='#FFFFFF', line='#DEDDD8', hair='#EFEEEA',
        accent='#2F343B', accent_ink='#1C1F24', soft='#F1F0EC', softer='#F7F6F3', select='#EFE4C8',
        nav='#FAFAF8', nav_sel='#EFE7D3', nav_sel_ink='#5A4210', kicker='#9A7020',
        ref='#2F6F8F', ref_soft='#E2EEF4', conv='#2F6F8F', conv_soft='#E8F1F5', put='#8C5A12', put_soft='#FAF1E1'),
}
DEFAULT = 'navy'


def theme(name=None):
    """쓸 색 묶음 — 이름을 주지 않으면 환경변수 VALUATION_THEME, 없으면 기본."""
    return THEMES.get(name or os.environ.get('VALUATION_THEME') or DEFAULT, THEMES[DEFAULT])


def css_vars(name=None):
    """:root{--ink:…} — 화면 CSS 와 계산표가 var(--ink) 처럼 읽는다."""
    t = theme(name)
    return ':root{' + ''.join(f'--{k.replace("_", "-")}:{v};' for k, v in t.items() if k != 'name') + '}'
