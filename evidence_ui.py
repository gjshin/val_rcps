"""Source lookup and explicit reviewer records; no model execution."""
import streamlit as st
from valuation.case import Case
from valuation.evidence import evidence_cards, judgment_record, BOOK_TITLE, BOOK_SHA256
from valuation.presentation import label, display_value


def main():
    st.title('판단 근거와 검토기록')
    case = st.session_state.get('case')
    if case is None:
        st.info('평가파일을 열면 해당 조건에 필요한 근거와 검토기록을 함께 볼 수 있습니다.')
        case = Case('근거 찾아보기')
    st.caption('기준서 → 실무사례 → 적용 시 확인할 내용 순서로 검토하십시오. 발췌문은 문단 전체를 대신하지 않습니다.')
    all_topics = st.checkbox('다른 상품·관점의 주제도 표시', value='case' not in st.session_state)
    cards = evidence_cards(case, include_all=all_topics)
    query = st.text_input('주제 검색', placeholder='상환, 배당, BDT, 전환권, 제3자 콜 등')
    cards = [c for c in cards if not query or query.lower() in str(c).lower()]
    for card in cards:
        with st.expander(f"{card['title']} · {card['status']}"):
            st.write(card['explanation'])
            if card['inputs']:
                st.caption('현재 입력: ' + ' / '.join(f'{label(k)}: {display_value(k, v)}' for k, v in card['inputs'].items()))
            for src in card['sources']:
                st.markdown(f"**{src['kind']} — {src['title']}**")
                st.caption(src['location'])
                if src['quote']:
                    st.markdown('> ' + src['quote'])
                if src.get('url'):
                    st.markdown(f"[해당 자료 열기]({src['url']})")
                if src.get('official_url'):
                    st.markdown(f"[한국회계기준원 시행 기준서]({src['official_url']})")
                st.caption(src['note'] + ' · 대조일 ' + src['checked_on'])
            st.write('계약과 대조할 사항')
            for question in card['questions']:
                st.write('• ' + question)
            st.info(card['limitation'])
            if 'case' in st.session_state:
                record = card['review']
                if card['review_stale']:
                    st.warning('검토 당시와 평가 입력 또는 근거 버전이 다릅니다. 기존 결론을 재확인하십시오.')
                with st.form('judgment_' + card['id'] + '_' + str(st.session_state.get('revision', 0))):
                    status = st.selectbox('검토 상태', ['검토 중', '검토 완료', '해당 없음'], index=['검토 중', '검토 완료', '해당 없음'].index(record.get('status', '검토 중')))
                    clause = st.text_input('계약서 조항·쪽수', record.get('contract_clause', ''))
                    conclusion = st.text_area('이 계약에 대한 결론', record.get('conclusion', ''))
                    rationale = st.text_area('적용 이유·예외 검토', record.get('rationale', ''))
                    reviewer = st.text_input('검토자', record.get('reviewer', ''))
                    if st.form_submit_button('검토기록 저장'):
                        try:
                            candidate = Case.from_dict(case.to_dict())
                            candidate.judgments[card['id']] = judgment_record(case, status, conclusion, rationale, reviewer, clause)
                            from workspace_app import save_case
                            save_case(candidate)
                        except ValueError as exc:
                            st.error(str(exc))
    with st.expander('실무사례 PDF에서 해당 쪽 바로 확인'):
        st.caption(BOOK_TITLE + '. 올린 원본은 저장소에 배포하지 않습니다.')
        upload = st.file_uploader('보유한 Series 11 PDF', type='pdf', key='source_pdf')
        if upload:
            import hashlib
            if hashlib.sha256(upload.getvalue()).hexdigest() != BOOK_SHA256:
                st.warning('발췌 시 확인한 파일과 다릅니다. 판본과 쪽수를 다시 확인하십시오.')
            pages = sorted({s['pdf_page'] for c in cards for s in c['sources'] if 'pdf_page' in s})
            if pages:
                page = st.selectbox('PDF 쪽수', pages, format_func=lambda n: f'PDF {n}쪽 / 책 {n-20}쪽')
                if st.button('선택한 한 쪽 열기'):
                    import io
                    from pypdf import PdfReader, PdfWriter
                    try:
                        reader = PdfReader(io.BytesIO(upload.getvalue()))
                        writer = PdfWriter()
                        writer.add_page(reader.pages[page-1])
                        out = io.BytesIO(); writer.write(out)
                        st.download_button('선택 쪽 PDF 저장', out.getvalue(), f'Series11_PDF_{page}.pdf', 'application/pdf')
                    except Exception as exc:
                        st.error(f'PDF 쪽을 읽지 못했습니다: {exc}')
