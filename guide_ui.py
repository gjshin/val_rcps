"""사용 안내 — docs/사용안내.md 를 앱 안에서 단계별로 보여 준다.

안내 글은 문서 한 곳에만 둔다(저장소에서 읽어도, 앱에서 읽어도 같은 글).
«## » 제목마다 한 쪽으로 나누고, 그림 줄(![설명](guide/그림.png))은 st.image 로 바꾼다.
"""

from pathlib import Path
import re

import streamlit as st

DOCS = Path(__file__).parent / 'docs'
GUIDE = DOCS / '사용안내.md'
IMAGE = re.compile(r'^!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)\s*$')


def load(path=GUIDE):
    """(문서 제목, 머리말, [(쪽 제목, 본문)]) — 본문은 줄 목록."""
    title, intro, pages = '', [], []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('# ') and not title:
            title = line[2:].strip()
        elif line.startswith('## '):
            pages.append((line[3:].strip(), []))
        elif pages:
            pages[-1][1].append(line)
        else:
            intro.append(line)
    return title, intro, pages


def images(lines):
    """본문에 실린 그림 (설명, 파일 경로) — 시험이 파일이 있는지 본다."""
    return [(m['alt'], DOCS / m['src']) for m in map(IMAGE.match, lines) if m]


def render(lines):
    buffer = []

    def flush():
        text = '\n'.join(buffer).strip()
        if text:
            st.markdown(text)
        buffer.clear()

    for line in lines:
        m = IMAGE.match(line)
        if m:
            flush()
            path = DOCS / m['src']
            if path.is_file():
                st.image(str(path), caption=m['alt'], width='stretch')
            else:  # 그림이 빠져도 안내 글은 읽을 수 있게
                st.caption(f"(그림 없음: {m['alt']})")
        else:
            buffer.append(line)
    flush()


def main():
    title, intro, pages = load()
    names = [name for name, _ in pages]
    if st.session_state.get('_guide_page') not in names:
        st.session_state['_guide_page'] = names[0]
    st.sidebar.radio('안내 목차', names, key='_guide_page')
    st.title(title)
    index = names.index(st.session_state['_guide_page'])
    if index == 0:  # 머리말은 첫 쪽에만
        render(intro)
    st.header(names[index])
    render(pages[index][1])

    def go(step):
        st.session_state['_guide_page'] = names[index + step]

    left, right = st.columns(2)
    if index > 0:
        left.button('← ' + names[index - 1], on_click=go, args=(-1,), width='stretch')
    if index < len(names) - 1:
        right.button(names[index + 1] + ' →', on_click=go, args=(1,), type='primary', width='stretch')
