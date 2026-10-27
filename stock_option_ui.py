"""주식선택권 화면 — 입력 · 평가결과 · 계산내역.

화면을 그릴 때는 계산하지 않는다. 평가 단추를 눌러 만든 실행 결과(run)만 보여 준다.
계산은 valuation/stock_option.py 에 있다.
"""

import datetime as dt
import math

import pandas as pd
import streamlit as st

import workspace_app as W
from ui_format import dataframe
from valuation import stock_option as SO
from valuation.presentation import label

COLS = [('name', '회차'), ('vest', '가득일'), ('start', '행사 시작일'), ('end', '행사 종료일'), ('qty', '부여 수량(주)'),
        ('keep', '가득 예상 비율(%)')]


def _day(v):
    try:
        return dt.date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def _blank(v):
    return v is None or v == '' or v is pd.NaT or (isinstance(v, float) and pd.isna(v))


def _iso(v):
    return '' if _blank(v) else v.isoformat()[:10] if hasattr(v, 'isoformat') else str(v)


def rows_editor(edited):
    """회차표 — 가득일이 다른 물량(단계 가득)은 한 줄씩."""
    rev = st.session_state.get('revision', 0)
    st.session_state.setdefault('_rendered_fields', set()).add('so_rows')
    old = list(edited.get('so_rows') or [])
    shown = old or [dict(name='1차', vest='', start='', end='', qty=None, keep=1.0)]
    frame = pd.DataFrame([{'회차': r.get('name', ''), '가득일': _day(r.get('vest')),
                           '행사 시작일': _day(r.get('start')), '행사 종료일': _day(r.get('end')),
                           '부여 수량(주)': r.get('qty'),
                           '가득 예상 비율(%)': None if r.get('keep') is None else float(r['keep'])*100}
                          for r in shown], columns=[t for _, t in COLS])
    for col in ('가득일', '행사 시작일', '행사 종료일'):
        frame[col] = pd.to_datetime(frame[col])      # 빈 날짜는 빈칸으로 보인다 (글자 «None» 이 아니라)
    table = st.data_editor(
        frame, num_rows='dynamic', hide_index=True, width='stretch', key=f'so_rows_{rev}',
        column_config={
            '회차': st.column_config.TextColumn(help='예: 1차 · 2차. 이름이 겹치지 않게 넣습니다.'),
            '가득일': st.column_config.DateColumn(format='YYYY-MM-DD',
                                               help='근무조건이 끝나는 날. 보상원가를 부여일부터 이날까지 나눕니다.'),
            '행사 시작일': st.column_config.DateColumn(format='YYYY-MM-DD',
                                                 help='비우면 가득일. 근무조건은 끝났지만 행사가 늦게 열리면 그 날을 넣습니다.'),
            '행사 종료일': st.column_config.DateColumn(format='YYYY-MM-DD',
                                                 help='비우면 위의 «행사기간 종료일» 을 씁니다.'),
            '부여 수량(주)': st.column_config.NumberColumn(min_value=0., format='%,.0f'),
            '가득 예상 비율(%)': st.column_config.NumberColumn(
                min_value=0., max_value=100., format='%.2f',
                help='가득기간 중 퇴사 등으로 가득되지 않을 수량을 뺀 비율. 공정가치가 아니라 수량에만 반영합니다 (문단 19·20).'),
        })
    new = []
    for rec in table.to_dict('records'):
        if all(_blank(v) for v in rec.values()):
            continue
        qty, keep = rec['부여 수량(주)'], rec['가득 예상 비율(%)']
        new.append(dict(name=str(rec['회차'] or '').strip() or f'{len(new)+1}차',
                        vest=_iso(rec['가득일']), start=_iso(rec['행사 시작일']), end=_iso(rec['행사 종료일']),
                        qty=None if _blank(qty) else float(qty),
                        keep=1.0 if _blank(keep) else float(keep)/100))
    if new != old and (new or old):
        edited['so_rows'] = new


def editor(edited, case, draft_errors):
    """입력 화면. 저장은 workspace_app._commit 이 한다."""
    st.subheader('부여 조건')
    st.caption('주식결제형 주식선택권은 부여일 현재 공정가치로 한 번 재고 다시 재지 않습니다 (기업회계기준서 제1102호 '
               '문단 11). 측정기준일에는 보통 부여일을 넣습니다.')
    W.fields(['tranche', 'so_settle', 'd_issue', 'd_base', 'd_mat', 'K0'], edited, case)
    st.subheader('가득 회차')
    st.caption('한 번에 모두 가득되면 한 줄, 2년 뒤 50%·3년 뒤 50% 처럼 나눠 가득되면 회차마다 한 줄을 넣습니다. '
               '회차마다 따로 평가하고 따로 비용을 나눕니다.')
    rows_editor(edited)
    st.subheader('행사 가정')
    st.caption('가득 뒤 언제 행사할지는 평가자가 정하는 추정입니다 (문단 B5·B16~B19). 과거 행사 실적·유사 기업 자료를 '
               '근거로 고르고 «출처·평가가정» 에 기록하십시오.')
    W.fields(['so_ex', 'so_exit'], edited, case)
    if edited.get('so_ex') == 2:
        W.field('so_mult', edited, case)
    st.caption('가득 뒤 연간 퇴사율 — 가득된 뒤 퇴사하면 그때 내가격이면 바로 행사하고, 아니면 소멸한다고 봅니다. '
               '가득 전 퇴사는 위 «가득 예상 비율» 에 넣습니다.')
    st.subheader('시장자료 및 계산방법')
    W.fields(['S0', 'sig', 'div_y'], edited, case)
    W.grid_editor(edited, case, end_word='회차의 행사 종료일')
    W.fields(['y_type', 'cmp_rf'], edited, case)
    st.caption('비율은 % 단위입니다. 신용위험이 없는 지분상품이라 무위험 금리곡선만 씁니다.')
    W.curve_editor('rf_curve', edited)
    st.subheader('회계 처리')
    W.field('so_fy_end', edited, case)
    st.caption('보상원가를 부여일부터 가득일까지 일수로 나눠 결산일마다 비용으로 인식합니다.')
    with st.expander('희석·모형 밖 조건 메모'):
        W.fields(['base_shares', 'dil_shares', 'unmod_note'], edited, case)
        st.caption('희석 효과·성과조건 등 이 모형이 반영하지 않는 조건을 기록합니다. 평가금액에는 영향이 없습니다.')


def cost_tables(run):
    """회계연도별 비용 — (결산일 × 회차) 표와 결산일별 분개."""
    sched = pd.DataFrame(run.summary['so_schedule'])
    pivot = sched.pivot_table(index='결산일', columns='회차', values='당기 비용', aggfunc='sum', sort=False).fillna(0.0)
    pivot = pivot[[r['회차'] for r in run.summary['so_rows'] if r['회차'] in pivot.columns]]
    pivot['합계'] = pivot.sum(axis=1)
    pivot['누적'] = pivot['합계'].cumsum()
    pivot = pivot.reset_index()
    journal = [{'결산일': r['결산일'], '차변': '주식보상비용', '대변': '주식선택권 (자본조정)', '금액': r['합계']}
               for r in pivot.to_dict('records') if abs(r['합계']) > 0.005]
    return pivot, journal


def result_panel(run, case, current, pending):
    s, t = run.summary, run.terms
    tot, ps = s['amounts_total'], s['amounts_per_share']
    st.markdown(
        '<div class="v2-hero"><div class="eyebrow">총 보상원가 · 가득 예상 수량 기준</div>'
        f'<div class="amount">{tot["so_cost"]:,.0f} <span class="unit">원</span></div></div>',
        unsafe_allow_html=True)
    from v2_workspace import goto
    st.button('계산표·수식 보기 →', on_click=goto, args=('계산내역', '노드 계산표'), type='primary')
    c1, c2, c3 = st.columns(3)
    c1.metric('1주당 공정가치 (가중평균, 원)', f"{ps['so_fair']:,.2f}",
              help='회차별 1주당 공정가치를 부여 수량으로 가중평균한 값입니다.')
    c2.metric('부여 수량 전체의 공정가치 (원)', f"{tot['so_fair']:,.0f}")
    c3.metric('부여 수량 (주)', f"{run.raw['qty']:,.0f}")
    st.caption(f"주가 대비 {s['amounts_100']['so_fair']:.2f}% · 행사가격 {t.K0:,.0f}원 · 부여일 주가 {t.S0:,.0f}원 · "
               f"변동성 {t.sig:.2%} · 조기행사 가정: {SO.EX_LABELS[int(t.so_ex)]}")
    st.subheader('회차별 공정가치')
    frame = pd.DataFrame(s['so_rows'])           # 합계는 위의 지표 칸에 있다 (표에 합계 줄을 넣으면 빈칸이 생긴다)
    dataframe(frame.style.format({'부여 수량': '{:,.0f}', '가득 예상 비율': '{:.2%}', '구간 수': '{:,.0f}',
                                  '1주당 공정가치': '{:,.2f}', '블랙-숄즈(행사 종료일)': '{:,.2f}',
                                  '기대존속기간(년)': '{:.3f}', '공정가치': '{:,.0f}', '보상원가': '{:,.0f}'}, na_rep=''),
              hide_index=True, width='stretch')
    st.caption('보상원가 = 1주당 공정가치 × 부여 수량 × 가득 예상 비율. 블랙-숄즈 값은 조기행사·퇴사가 없을 때의 '
               '대조값입니다 — 조기행사를 반영하면 격자값이 이보다 작거나 같습니다.')
    st.subheader('회계연도별 보상원가 배분')
    pivot, journal = cost_tables(run)
    dataframe(pivot.style.format({c: '{:,.0f}' for c in pivot.columns if c != '결산일'}),
              hide_index=True, width='stretch')
    st.caption('부여일부터 회차별 가득일까지 일수로 나눴습니다. 결산일마다 가득 예상 비율을 다시 추정하고, 바뀌면 '
               '그때까지의 누적 비용을 새 추정에 맞춥니다 (문단 20). 가득된 뒤 행사되지 않고 소멸해도 인식한 비용은 '
               '되돌리지 않습니다 (문단 23).')
    with st.expander('분개 (초안)'):
        dataframe(pd.DataFrame(journal).style.format({'금액': '{:,.0f}'}), hide_index=True, width='stretch')
        st.caption('행사 시: (차) 현금(행사가격 × 행사 수량) + 주식선택권 (대) 자본금 · 주식발행초과금. '
                   '일반기업회계기준을 적용하는 비상장 중소기업은 행사할 때까지 회계처리를 하지 않을 수 있습니다 '
                   '(일반기업회계기준 제31장 문단 31.8).')
    notices = [i for i in run.issues if i.code not in
               {'source', 'engine_defaults', 'legacy_defaults', 'judgement_scope', 'market_date', 'coverage_review'}]
    from valuation.explain import export_blockers
    from v2_workspace import check_rows
    from valuation.presentation import issue_rows
    blocked = export_blockers(run)
    for message in blocked:
        st.warning(message)
    with st.expander(f'확인할 사항 {len(notices)}건 · 계산 점검', expanded=bool(blocked)):
        if notices:
            dataframe(pd.DataFrame(issue_rows(notices)), hide_index=True, width='stretch')
        else:
            st.write('이 실행에서 표시할 수치 경고가 없습니다.')
        dataframe(pd.DataFrame(check_rows(s['checks'])), hide_index=True, width='stretch')
    with st.expander('민감도 · 기준 평가 유지'):
        variable = st.selectbox('민감도 변수', ['S0', 'sig', 'rf_curve'], format_func=lambda k: label(k, 'SO'),
                                key='so_sens_var')
        magnitude = st.number_input('변화폭(주가는 %, 변동성·금리는 %p)', min_value=0.01,
                                    max_value=50.0 if variable == 'S0' else 10.0,
                                    value=10.0 if variable == 'S0' else 1.0, key='so_sens_mag')
        if st.button('민감도 계산', disabled=not current or pending, key='so_sens_btn'):
            from valuation.analysis import sensitivity
            try:
                with st.spinner('기준 입력을 보존하고 감소·증가 조건을 계산합니다.'):
                    st.session_state.analysis = sensitivity(run, variable, magnitude)
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))
        a = st.session_state.get('analysis')
        if a and a['calculation_key'] == s['calculation_key'] and current:
            from valuation.service import AMOUNT_LABELS
            dataframe(pd.DataFrame(a['rows']).rename(columns=AMOUNT_LABELS), hide_index=True, width='stretch')
            st.caption(f"계산된 변수 {label(a['variable'], 'SO')} · 변화폭 ±{a['change']:.2f}. 기준 평가와 입력은 유지됩니다.")
    with st.expander('평가 정보'):
        from valuation.explain import VERSION
        st.write({'실행번호': s['run_id'], '문서 개정': s['document_id'], '버전': VERSION,
                  '계산시각': s['calculated_at'], '입력 식별값': s['case_sha256']})


def _pick_row(run, key):
    names = [r['name'] for r in run.raw['rows']]
    if st.session_state.get(key) not in names:
        st.session_state[key] = names[0]
    name = st.selectbox('회차', names, key=key)
    return next(r for r in run.raw['rows'] if r['name'] == name)


def node_table(run, row, start, end):
    """시점 start~end 의 노드 — 칸마다 «옵션가치 / 주가», 행사 노드는 ●."""
    cols = {}
    for i in range(start, end + 1):
        cols[f"{i}"] = [f"{'● ' if row['exercise'][i][j] else ''}{row['values'][i][j]:,.2f} / {row['prices'][i][j]:,.0f}"
                        if j <= i else '' for j in range(end + 1)]
    frame = pd.DataFrame(cols)
    frame.insert(0, '자리', range(end + 1))
    return frame


def calculation_panel(run):
    topics = ['노드 계산표', '노드 따라가기', '금리·확률', '비용 배분']
    if st.session_state.get('_calc_topic') not in topics:
        st.session_state['_calc_topic'] = topics[0]
    topic = st.radio('계산내역 선택', topics, horizontal=True, key='_calc_topic')
    t = run.terms
    if topic == '비용 배분':
        pivot, journal = cost_tables(run)
        dataframe(pd.DataFrame(run.summary['so_schedule']).style.format(
            {'누적 배분율': '{:.4%}', '당기 비용': '{:,.0f}', '누적 비용': '{:,.0f}'}), hide_index=True, width='stretch')
        st.caption('누적 배분율 = (결산일 − 부여일) ÷ (가득일 − 부여일), 1을 넘지 않음. 당기 비용 = 누적 비용 − 전기 누적 비용.')
        return
    row = _pick_row(run, '_so_calc_row')
    st.caption(f"{row['name']} · 가득일 {row['vest']} · 행사 시작일 {row['start']} · 행사 종료일 {row['end']} · {row['n']}구간 · "
               f"구간 {row['dt']*365:.2f}일 · 상승 배수 u = {row['u']:.6f} · 하락 배수 d = {row['d']:.6f} · "
               f"행사 가능 시점 {row['ex_step']}부터")
    if topic == '노드 계산표':
        n = row['n']
        spans = list(range(0, n + 1, 16))
        start = st.selectbox('계산표 시점 구간', spans, format_func=lambda i: f'{i}–{min(i+15, n)} 시점',
                             key='_so_span_' + run.summary['run_id'] + row['name'])
        end = min(start + 15, n)
        table = node_table(run, row, start, end)
        dataframe(table, hide_index=True, width='stretch',
                  column_config={c: st.column_config.TextColumn(width='medium') for c in table.columns if c != '자리'})
        st.caption('칸마다 «옵션 1주당 가치 / 주가». ● 는 그 노드에서 행사한다는 뜻입니다. 자리 0이 가장 높은 주가입니다. '
                   '열 이름은 시점(구간 번호)입니다.')
    elif topic == '노드 따라가기':
        c1, c2 = st.columns(2)
        i = c1.number_input('시점', min_value=0, max_value=row['n'], value=0, step=1, key='_so_trace_i')
        j = c2.number_input('자리 (0 = 가장 높은 주가)', min_value=0, max_value=int(i), value=0, step=1,
                            key='_so_trace_j')
        tr = SO.node_trace(t, row, int(i), min(int(j), int(i)))
        st.write(f"**{tr['date']}** · 주가 {tr['stock']:,.2f}원 · 행사가격 {tr['strike']:,.2f}원 · 판단: {tr['rule']}")
        if tr['terminal']:
            _formula(f"가치 = max({tr['stock']:,.4f} − {tr['strike']:,.4f}, 0) = {tr['value']:,.6f}")
        else:
            text = (f"계속 보유(퇴사 전) = exp(−{tr['fwd']:.6%} × {row['dt']:.6f}) × "
                    f"[{tr['p']:.8f} × {tr['up']:,.6f} + (1 − {tr['p']:.8f}) × {tr['down']:,.6f}]\n"
                    f"= {tr['disc']:.8f} × {tr['p']*tr['up'] + (1-tr['p'])*tr['down']:,.6f} = {tr['cont']:,.6f}")
            if tr['exit_step'] > 0:
                text += (f"\n퇴사 반영 = (1 − {tr['exit_step']:.8f}) × {tr['cont']:,.6f} + {tr['exit_step']:.8f} × "
                         f"max({tr['intrinsic']:,.4f}, 0) = {tr['hold']:,.6f}")
            if tr['vested']:
                text += f"\n지금 행사 = {tr['stock']:,.4f} − {tr['strike']:,.4f} = {tr['intrinsic']:,.6f}"
            text += f"\n이 노드의 가치 = {tr['value']:,.6f}  (다시 계산한 값과의 차이 {abs(tr['reconciliation']):.6f})"
            _formula(text)
            st.caption(f"위 노드: 주가 {tr['up_stock']:,.2f} · 가치 {tr['up']:,.6f} / 아래 노드: 주가 {tr['down_stock']:,.2f} · "
                       f"가치 {tr['down']:,.6f}")
    else:
        rows = [{'시점': i, '격자일': (dt.date.fromisoformat(t.d_base) + dt.timedelta(days=round(i*row['dt']*365))).isoformat(),
                 '선도 무위험 금리(연속)': row['fwd'][i], '할인계수': math.exp(-row['fwd'][i]*row['dt']),
                 '상승 확률': row['p'][i], '행사 가능': '가능' if i >= row['ex_step'] else '불가'}
                for i in range(row['n'])]
        dataframe(pd.DataFrame(rows).style.format({'선도 무위험 금리(연속)': '{:.6%}', '할인계수': '{:.8f}',
                                                    '상승 확률': '{:.8f}'}), hide_index=True, width='stretch')
        st.caption(f"상승 확률 = (e^((선도 금리 − 배당수익률 {t.div_y:.2%}) × 구간) − d) ÷ (u − d). "
                   f"무위험 금리곡선 출처: {run.case.sources.get('rf_curve', '미기록')}")


def _formula(text):
    from v2_workspace import formula_box
    formula_box(text)
