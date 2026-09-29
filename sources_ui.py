"""근거 원문 펼침창 — 판정 줄 옆 [원문]."""
import streamlit as st
from valuation import sources


def show(topic, text='원문', keys=None):
    """topic(또는 keys)의 근거 원문을 팝오버로 보여 준다. 누를 때만 그린다."""
    keys = keys or sources.refs(topic)
    if not keys:
        return
    with st.popover(f'{text} · {len(keys)}건'):
        choice = st.radio('근거', keys, format_func=sources.label, horizontal=True,
                          key=f'_src_{topic}_{"_".join(keys)}', label_visibility='collapsed')
        for row in sources.lookup(choice):
            st.markdown(f"**{row['title']}**" + (f" — {row['location']}" if row['kind'] == '한공회 실무사례' else ''))
            st.text(row['text'])
            if row['url']:
                st.caption(f"출처: {row['url']} · {row['source']}")
            else:
                st.caption(f"출처: {row['source']} (발췌 · 외부 배포 금지)")
