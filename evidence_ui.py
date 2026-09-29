"""Contract-specific workpaper records with source excerpts."""
import streamlit as st
from valuation.case import Case
from valuation.evidence import evidence_cards, judgment_record, BOOK_TITLE, BOOK_SHA256
from valuation.presentation import label, display_value
from valuation.contract_intake import findings_for


def main():
    st.subheader('계약·회계 판단 검토조서')
    case = st.session_state.get('case') or Case('근거 찾아보기')
    st.write('계약 해석과 평가방법 선택의 근거를 남기는 조서입니다. 아래 검토주제를 선택하여 조항·관련 입력값을 대조하고, 기준서 및 실무사례를 확인한 뒤 결론을 기록하십시오.')
    st.caption('검토기록을 저장해도 계산 입력값은 바뀌지 않습니다. 결론에 따라 입력을 변경하고 다시 평가한 뒤 기록을 확정하십시오. 저장한 기록은 Excel 조서에 포함됩니다.')
    all_topics = st.checkbox('다른 상품·관점의 주제도 표시', value='case' not in st.session_state)
    cards = evidence_cards(case, include_all=all_topics)
    remaining = sum(c['status'] not in {'검토 완료', '해당 없음'} for c in cards)
    st.caption(f'관련 주제 {len(cards)}건 · 미완료 또는 재검토 {remaining}건')
    st.dataframe([{'검토주제':c['title'], '상태':c['status'], '검토자':c['review'].get('reviewer','')} for c in cards], hide_index=True)
    query = st.text_input('주제 검색', placeholder='상환, 배당, BDT, 전환권, 제3자 콜')
    cards = [c for c in cards if not query or query.lower() in str(c).lower()]
    if not cards:
        st.info('검색 조건에 해당하는 검토주제가 없습니다.')
        return
    choice = st.selectbox('검토할 주제', range(len(cards)), format_func=lambda i: cards[i]['title'] + ' · ' + cards[i]['status'])
    card = cards[choice]
    st.markdown('**검토 목적**')
    st.write(card['explanation'])
    st.markdown('**관련 입력값**')
    st.dataframe([{'항목': label(k), '현재 적용값': display_value(k, v, case.contract.get('d_issue')), '출처':case.sources.get(k,'')} for k,v in card['inputs'].items()], hide_index=True)
    linked = findings_for(case, card['id'])
    for row in linked:
        st.markdown('**계약 검토안: ' + row['document'] + ' · ' + row['clause'] + '**')
        st.text(row['quote'])
        st.write(row['interpretation'])
        st.caption('확인할 사항: ' + row['action'])
    st.markdown('**확인할 사항**')
    for item in card['questions']:
        st.write('• ' + item)
    st.info('후속 작업: ' + card['action'])
    with st.expander('기준서·실무사례 발췌 및 원문 위치'):
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
    st.caption('적용 범위: ' + card['limitation'])
    if 'case' in st.session_state:
        record = card['review']
        if card['review_stale']:
            st.warning('검토 후 입력 또는 근거자료가 변경되었습니다. 기존 결론의 유효성을 재확인하십시오.')
        with st.form('judgment_' + card['id'] + '_' + str(st.session_state.get('revision', 0))):
            status = st.selectbox('검토 상태', ['검토 중', '검토 완료', '해당 없음'], index=['검토 중', '검토 완료', '해당 없음'].index(record.get('status', '검토 중')))
            proposed_clause = '; '.join(r['document'] + ' ' + r['clause'] for r in linked)
            clause = st.text_input('계약서 조항·쪽수', record.get('contract_clause', proposed_clause))
            conclusion = st.text_area('계약별 결론 및 평가 반영방법', record.get('conclusion', ''))
            rationale = st.text_area('적용 문단·계약 해석·예외 검토', record.get('rationale', ''), help='기준서 문단, 해당 조항의 사실관계, 결론 도출 이유 및 입력 반영 여부를 기록하십시오.')
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
