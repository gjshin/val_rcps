"""Lightweight volatility app with source-bound, reproducible evidence."""
import datetime as dt
import hashlib
import json
import pandas as pd
import streamlit as st
from valuation import legacy
from valuation.market_data import query_spec, digest, make_pack, validate_pack, aggregate, parse_price_table, select_price_window

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_prices(*args, **kwargs):
    return legacy.fetch_prices(*args, **kwargs)
agg = aggregate
PICK = {'median': '중앙값', 'mean': '단순평균', 'max': '최댓값', 'min': '최솟값'}

def main():
    if not st.session_state.get('_app_embedded'):
        st.set_page_config(page_title='주가 변동성', layout='centered')
    st.subheader('주가 변동성 산출')
    st.caption('조회 조건·원본 수정주가·취득시각을 평가파일에 함께 보관합니다.')
    listed = st.radio('대상회사', ['비상장사 — 피어로 산출', '상장사 — 대상회사 주가'], horizontal=True).startswith('상장')
    mode = st.radio('주가 자료', ['야후 파이낸스', '종가 파일'], horizontal=True)
    lines, upload = [], None
    if mode == '야후 파이낸스':
        txt = st.text_input('종목코드 · 이름') if listed else st.text_area('피어 목록 — 한 줄에 코드,이름', placeholder='377450,리파인\n030190,NICE평가정보')
        lines = [s for s in txt.splitlines() if s.strip()]
    else:
        upload = st.file_uploader('첫 열 날짜, 나머지 열 종목별 수정종가', type=['xlsx', 'csv', 'txt', 'tsv'])
        st.caption('파일의 주가가 분할·배당 등 기업행위를 반영했는지 출처에서 확인하십시오. 기준일 이후 자료는 허용하지 않습니다.')
    c1, c2 = st.columns(2)
    days = int(c1.number_input('조회 일수 (거래일)', value=250, step=10, min_value=10))
    tdays = int(c2.number_input('연 거래일수', value=250, step=5, min_value=1, max_value=366))
    case = st.session_state.get('case')
    default_date = dt.date.fromisoformat(case.effective()['d_base']) if case and case.effective().get('d_base') else dt.date.today()
    asof = st.date_input('조회 종료일 (= 평가기준일)', value=default_date)
    c3, c4 = st.columns(2)
    drop = c3.checkbox('이상치 제거 (중앙값 절대편차 2.5배)', value=True)
    pick = c4.selectbox('종합 방법', list(PICK), format_func=PICK.get, disabled=listed)
    if listed:
        pick = 'median'
    with st.expander('자료 누락 처리'):
        allow_failed = st.checkbox('못 받은 종목 빼고 계산', value=False, disabled=listed)
        allow_missing = st.checkbox('빈 날짜 빼고 계산', value=False)
        st.caption('누락을 허용하면 실제 수신한 주가만 사용합니다. 부족한 관측치, 제외 종목·날짜·사유를 산출내역에 기록합니다. 0·음수 주가와 중복·미래 날짜는 허용하지 않습니다.')
    policy = dict(allow_failed=allow_failed and not listed, allow_missing=allow_missing)
    query = None
    try:
        if mode == '야후 파이낸스' and lines:
            query = query_spec(listed, lines, days, asof.isoformat(), **policy)
        elif upload is not None:
            query = query_spec(listed, [], days, asof.isoformat(), hashlib.sha256(upload.getvalue()).hexdigest(), **policy)
    except (ValueError, TypeError) as exc:
        st.error(str(exc))
    if st.button('주가 받기' if mode == '야후 파이낸스' else '파일을 현재 조건으로 읽기', type='primary', disabled=query is None):
        # A failed new request never leaves a previous successful pack applicable.
        st.session_state.pop('vol_snapshot', None)
        try:
            with st.spinner('주가 자료를 읽고 있습니다.'):
                got, failures, exclusions = [], [], []
                if mode == '야후 파이낸스':
                    sources = []
                    for peer in query['peers']:
                        try:
                            if allow_missing:
                                rows, source, removed = fetch_prices(peer['code'], days, '', asof.isoformat(), allow_missing=True, return_exclusions=True)
                                exclusions += [dict(x, name=peer['name']) for x in removed]
                            else:
                                rows, source = fetch_prices(peer['code'], days, '', asof.isoformat())
                            got.append([peer['name'], [[str(d), float(p)] for d, p in rows]])
                            sources.append(f"{peer['code']}: {source}")
                        except Exception as exc:
                            failures.append(f"{peer['name']}: {exc}")
                            exclusions.append(dict(name=peer['name'], date='', reason='조회 실패: ' + str(exc)))
                    if failures and not policy['allow_failed']:
                        raise ValueError('일부 종목을 받지 못했습니다. 피어 전체를 확인한 뒤 다시 받으십시오. ' + ' / '.join(failures))
                    source = '야후 파이낸스 · 수정주가 · ' + ' / '.join(sources)
                else:
                    parsed, exclusions = parse_price_table(legacy.read_upload(upload.name, upload.getvalue()), allow_missing=allow_missing, return_exclusions=True)
                    got, exclusions = select_price_window(parsed, exclusions, days, asof.isoformat())
                    source = '사용자 종가 파일: ' + upload.name
                retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
                make_pack(got, query, tdays=tdays, drop=drop, pick=pick, source=source, retrieved_at=retrieved_at, exclusions=exclusions)
                st.session_state.vol_snapshot = dict(series=got, query=query, source=source, retrieved_at=retrieved_at, exclusions=exclusions)
        except (ValueError, TypeError, ArithmeticError) as exc:
            st.error(str(exc))
    snapshot = st.session_state.get('vol_snapshot')
    if snapshot:
        if query is None or digest(query) != digest(snapshot['query']):
            st.warning('조회 기준일·기간·종목 또는 파일이 바뀌었습니다. 현재 조건으로 자료를 다시 읽어야 적용할 수 있습니다.')
        else:
            try:
                pack = make_pack(**snapshot, tdays=tdays, drop=drop, pick=pick)
                validate_pack(pack, current_query=query)
                rows = [[name, sigma, n, removed, prices[0][0], prices[-1][0]]
                        for (name, sigma, n, removed), (_, prices) in zip(pack['per_company'], pack['series'])]
                st.dataframe(pd.DataFrame(rows, columns=['회사', '연 변동성', '수익률 수', '이상치 제외', '첫 일자', '끝 일자']).style.format({'연 변동성': '{:.2%}'}), hide_index=True)
                st.metric('적용 변동성 · ' + PICK[pick], f"{pack['sigma']:.2%}")
                st.caption(f"자료 취득: {snapshot['retrieved_at']} · 원본 식별값: {pack['data_sha256'][:16]}")
                for warning in pack['warnings']:
                    st.warning(warning)
                if st.session_state.get('_app_embedded') and case:
                    if st.button('이 변동성을 현재 평가에 적용'):
                        from valuation.bridge import apply_volatility
                        from workspace_app import save_case
                        save_case(apply_volatility(case, pack))
                st.download_button('변동성 패키지 (JSON) 내려받기', json.dumps(pack, ensure_ascii=False, indent=2), f'변동성패키지_{asof.isoformat()}.json', 'application/json')
                # Excel generation is explicit; changing a display option does not rebuild it.
                if st.button('산출내역 엑셀 생성'):
                    from valuation.market_data import export_volatility_workbook
                    st.session_state.vol_xlsx = export_volatility_workbook(pack)
                    st.session_state.vol_xlsx_key = digest([pack['data_sha256'], pack['opt'], pack['query_sha256'], pack['warnings']])
                if st.session_state.get('vol_xlsx_key') == digest([pack['data_sha256'], pack['opt'], pack['query_sha256'], pack['warnings']]):
                    st.download_button('산출내역 엑셀 저장', st.session_state.vol_xlsx, f'변동성산출내역_{asof.isoformat()}.xlsx')
            except (ValueError, TypeError, ArithmeticError) as exc:
                st.error(str(exc))


if __name__ == '__main__':
    main()
