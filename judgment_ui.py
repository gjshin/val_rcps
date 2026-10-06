"""판단·근거 탭 — 분리 판정·권리표·우선순위·행사 진단·BDT·콜 방법. 추가 검토 항목은 출처·평가가정 화면에 있다.

원칙
- 한 줄 판정 + 숫자 + [원문]. 긴 설명은 펼침 안에 둔다.
- 격자를 다시 도는 비교는 버튼을 눌렀을 때만 계산하고 계산키로 보관한다.
- 앱 판정은 초안이다. 평가자가 동의·수정과 근거를 적으면 평가파일과 조서에 남는다.
"""
import copy
import json
from dataclasses import asdict

import pandas as pd
import streamlit as st

from valuation import legacy as L
from valuation import sources
from sources_ui import show as source

DECISIONS = ['앱 판정에 동의', '앱 판정과 다르게 판단', '해당 없음']


def _key(t):
    return json.dumps(asdict(t), sort_keys=True, default=str)


def _saved(name, t):
    got = st.session_state.get(name)
    return got[1] if got and got[0] == _key(t) else None


def memo(topic, run, title='평가자 판단'):
    """판정 줄 아래 평가자 판단 한 줄. 임베디드(평가파일)일 때만 저장한다."""
    if run is None:
        return
    case = st.session_state.get('case')
    if case is None:
        return
    old = case.memos.get(topic, {})
    rev = st.session_state.get('revision', 0)
    c1, c2, c3 = st.columns([2, 5, 1])
    decision = c1.selectbox(title, DECISIONS, index=DECISIONS.index(old['decision']) if old.get('decision') in DECISIONS else 0,
                            key=f'memo_d_{topic}_{rev}', label_visibility='collapsed')
    reason = c2.text_input('근거', value=old.get('reason', ''), key=f'memo_r_{topic}_{rev}',
                           placeholder='근거 한 줄 (계약 조항·기준서 문단)', label_visibility='collapsed')
    if c3.button('저장', key=f'memo_s_{topic}_{rev}', disabled=not reason.strip()):
        from valuation.case import Case
        from workspace_app import save_case
        candidate = Case.from_dict(case.to_dict())
        candidate.memos[topic] = dict(decision=decision, reason=reason.strip())
        from valuation.explain import memo_key
        candidate.memo_context[topic] = memo_key(candidate, topic)
        save_case(candidate)
    if old:
        from valuation.explain import memo_status
        status = memo_status(case, topic)
        if status != '현재 조건의 기록':
            st.warning(status + ' — 내용을 확인한 뒤 저장하면 현재 조건과 연결됩니다.')
        st.caption(f"{status} — {old['decision']} · {old['reason']}")


def _fmt(v):
    return (f'{v*100:.1f}%' if isinstance(v, float) and abs(v) < 1.5 else
            '예' if v is True else '아니오' if v is False else v if isinstance(v, str) else f'{v:,.4f}')


def _headline(nm, d):
    ind = d['지표']
    nums = ''
    if '첫 조기상환일 행사금액' in ind:
        nums = (f" · 행사금액 {ind['첫 조기상환일 행사금액']:,.2f} / 상각후원가 "
                f"{ind['같은 시점 상각후원가']:,.2f} · 차이 {ind['차이']*100:.1f}% (비교기준 {ind.get('비교기준 (회계정책)', L.SPLIT_TOL):.0%} · 회계정책)")
    return f"**{nm} — {d['결론']}**{nums}"


def split_section(t, full, b0, b1, b2, ca, LB, run):
    ah = L.acc_host(t, full, b0, b1, b2, ca)
    sp = L.split_test(t, full, b0, b1, b2, ca, [] if ah is None else L.eir_table(t, ah)[1])
    st.markdown('#### 분리 판정 (앱 판정 · 초안)')
    st.caption('분리 정책 — ' + ('접근법 1 (얽힌 권리를 먼저 묶고 판단)' if L.emb_policy(t) == 1 else
                                 '접근법 2 (권리마다 판단한 뒤 분리 대상끼리 묶기)')
               + ' · 한공회 실무사례 30~32쪽. 풋·콜이 주계약과 밀접한지를 전환권 분리 전에 판단합니다 (1109 B4.3.5 말미).'
               + (' 평가기준일이 발행일보다 뒤이므로 다시 판정하지 않고 최초 인식 때의 결론을 이어 씁니다 (1109 B4.3.11).'
                  if t.elapsed_m > 0.01 else ''))
    items = ([('warrant', '신주인수권', 'embedded')] if L.is_bw(t) else []) + \
            [('put', LB['put'], 'split_put'), ('call', LB['call'], 'third_party_call' if t.k_third else 'split_call')]
    for key, nm, topic in items:
        d = sp.get(key)
        if d is None:
            continue
        if not d['있음']:
            st.caption(f"{nm} — 해당 없음 ({L.inst_text(t, d['이유'][0])})")
            continue
        box = st.success if d['결론'] in ('분리', '별도의 금융상품', '묶어서 분리') else st.info
        box(L.inst_text(t, _headline(nm, d)))
        c1, c2 = st.columns([5, 1])
        with c1.expander('판정 이유 · 평가방법'):
            for why in d['이유']:
                st.write('· ' + L.inst_text(t, why))
            if d['근거']:
                st.caption('근거 · ' + ' · '.join(d['근거']))
            st.write('평가방법 — ' + L.inst_text(t, d['평가']))
            if d['지표'] and d.get('회차'):
                # 행사일마다 견준 표 — 조서 «분리 판단» 시트의 행사일별 표와 같은 값이다.
                st.dataframe(pd.DataFrame([[round(m, 2), max(0.0, (m - t.elapsed_m)/12), pv, bv, f'{g*100:.1f}%']
                                           for m, pv, bv, g in d['회차']], columns=L.SPLIT_DATE_COLS),
                             hide_index=True, use_container_width=True)
                st.caption('행사일마다 행사금액과 같은 시점 상각후원가를 견주고, 가장 큰 차이로 판정합니다. '
                           '조서 «분리 판단» 시트의 행사일별 표와 같은 값입니다.')
        with c2:
            source(topic)
        memo(f'split_{key}', run)
    if not sp['put']['설정일치']:
        st.error(f"설정 불일치 — 판정 「{sp['put']['결론']}」, 입력 「"
                 + ('분리하지 않음' if L.put_in_host(t) else '분리') + "」. 입력의 「분해방법·기간 기준」에서 맞추십시오.")
    if not sp['call']['설정일치']:
        st.error(f"설정 불일치 — 판정 「{sp['call']['결론']}」, 입력 「"
                 + ('별도 금융상품' if t.k_sep else '복합내재파생에 포함') + "」. 입력의 「분해방법·기간 기준」에서 맞추십시오.")
    return sp


def facts_section(t, run):
    st.markdown('#### 계약 조항 확인')
    rows = [['매도청구권을 제3자에게 지정할 수 있다', t.k_third, '1109 4.3.1'],
            ['매도청구권을 독립적으로 양도할 수 있다', t.k_transfer, '1109 4.3.1'],
            ['조기상환 행사금액이 상실이자 보상 수준이다', t.p_lost_int, '1109 B4.3.5(5)(나)'],
            ['복합계약 전체를 당기손익-공정가치로 지정했다', t.fvpl_whole, '1109 4.3.5']]
    st.dataframe(pd.DataFrame([[a, '예' if b else '아니오', c] for a, b, c in rows],
                              columns=['조항', '입력', '근거']), hide_index=True, use_container_width=True)
    if run is not None:
        st.caption('바꾸려면 「입력·시장자료 → 분해방법·기간 기준」 또는 콜 권리의 상세 조건에서 수정합니다.')


def put_exercise_section(t):
    """조기상환 행사 진단 — 옵션 없는 격자 한 장(가벼움)으로 행사금액과 계속보유가치를 견준다."""
    if not (t.p_s <= t.p_e and t.T > 0) or t.conv_class != 'equity':
        return
    from valuation.explain import put_diagnostic
    rows = put_diagnostic(t)
    ratios = [r['비율'] for r in rows if r['비율'] is not None]
    if not ratios:
        return
    st.markdown('#### 조기상환 행사 진단')
    st.write(f'지급 제약·이자 반영 청구가치 ÷ 계속보유가치 {min(ratios):.3f} ~ {max(ratios):.3f}')
    st.caption('계약 청구액과 실제 지급 일정의 현재가치를 구분합니다. 원금 100 기준이며 단독 채권의 행사 유인을 보는 참고표입니다.')
    if L.dp_active(t):
        st.info('배당가능이익으로 지급이 지연되는 가치를 반영했습니다. 현재 지원범위에서는 배당가능이익 제약과 BDT를 함께 적용할 수 없습니다.')
    elif min(ratios) <= 1.03:
        st.info('금리에 따라 행사 여부가 달라질 수 있습니다. 지원범위와 금리모형 적용 근거를 확인하십시오.')
    with st.expander('행사일별 표'):
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    source('put_exercise')


def call_section(t, full, b2, sp, run):
    if t.k_w <= 0:
        return
    st.markdown('#### 매도청구권 평가방법')
    st.caption(L.inst_text(t, L.call_type_note(t)).replace('**', ''))
    c1, c2 = st.columns([5, 1])
    with c2:
        source('call_method')
    if c1.button('콜 평가방법 비교', key='jd_call_btn'):
        with st.spinner('방법별로 다시 계산합니다.'):
            st.session_state.jd_call = (_key(t), (*L.call_compare(t, full, b2), L.wow_trace(t, full, b2)))
    got = _saved('jd_call', t)
    if got:
        cmp_rows, rec, tr = got
        base = next((v for nm, _, v, _ in cmp_rows if nm.startswith('유무가치비교법 (')), None)
        st.dataframe(pd.DataFrame([[nm, sp_, v, (v - base) if base else 0., '◀ 적용' if on else '']
                                   for nm, sp_, v, on in cmp_rows],
                                  columns=['방법', '지분·채권 구분', '값', '유무가치 대비', '']).style.format(
            {'값': '{:,.4f}', '유무가치 대비': '{:+,.4f}'}), hide_index=True, use_container_width=True)
        st.caption(L.call_compare_note(t))
        if tr:
            # 유무가치비교법 차액의 구성 — 음수여도 0 으로 덮지 않고 원인을 나눠 보인다 (조서 결과 시트와 같은 표)
            st.dataframe(pd.DataFrame(L.wow_trace_rows(tr), columns=['유무가치비교법 차액의 구성', '값']).style.format(
                {'값': '{:,.4f}'}), hide_index=True, use_container_width=True)
            (st.warning if (tr['A'] < 0 or tr['A0'] < 0) else st.caption)(L.wow_trace_note(tr))
        if rec:
            # 조서 결과 시트와 같은 분해 — 마지막 줄이 ① + ② 이자 실제 차이다.
            _dA = rec['유무가치비교법 (적용 계약)'] - rec['옵션차익법 (적용 산식·적용 설정)']
            st.dataframe(pd.DataFrame([[k, v] for k, v in rec.items()]
                                      + [['차이 (유무가치 − 옵션차익) = ① + ②', _dA]],
                                      columns=['차이 분해', '값']).style.format({'값': '{:,.4f}'}),
                         hide_index=True, use_container_width=True)
            st.caption(L.CALL_REC_NOTE)
    with st.expander('같은 날 겹치는 사건의 처리 (세 평가방법 공통)'):
        st.table(pd.DataFrame(L.event_order_rows(t), columns=['사건', '처리']))
    with st.expander('옵션차익법 계산 단계'):
        st.markdown(L.inst_text(t, L.CALL_HOWTO))
    st.caption('앱 권고 — ' + L.inst_text(t, sp['call']['평가']).replace('**', ''))
    memo('call_method', run, '방법 선택 근거')


def priority_section(t, ca, conv, run):
    if t.k_w <= 0 or not L.pc_overlap(t):
        return
    ov = [x for x in L.pc_overlap(t) if x[2] > x[3] + 1e-9]
    st.markdown('#### 풋·콜 우선순위')
    if ov:
        st.warning(f'두 권리가 {len(ov)}개 시점에서 함께 열리고 그 자리 조기상환금액이 더 큽니다 '
                   f'(첫 자리 {ov[0][1]:,.0f}개월 · {ov[0][2]:,.2f} 대 {ov[0][3]:,.2f}). 우선순위에 따라 값이 달라질 수 있습니다.')
    else:
        st.caption('함께 열리는 시점이 있으나 매도청구금액이 늘 크거나 같아 우선순위가 값을 바꾸지 않습니다.')
    c1, c2 = st.columns([5, 1])
    with c2:
        source('priority')
    if c1.button('우선순위 비교', key='jd_pc_btn'):
        with st.spinner('두 우선순위로 다시 계산합니다.'):
            st.session_state.jd_pc = (_key(t), L.pc_compare(t))
    got = _saved('jd_pc', t)
    if got is not None:
        rows = got or [['투자자 조기상환 우선', ca, conv, int(t.pc_order) == 0], ['발행자 매도청구 우선', ca, conv, int(t.pc_order) == 1]]
        st.dataframe(pd.DataFrame([[a, b, c, '◀ 적용' if on else ''] for a, b, c, on in rows],
                                  columns=['우선순위', '매도청구권', '전환권대가', '']).style.format(
            {'매도청구권': '{:,.4f}', '전환권대가': '{:,.4f}'}), hide_index=True, use_container_width=True)
    memo('priority', run, '우선순위 조항')


def cresp_section(t, ca, conv, run):
    """매도청구 통지 뒤 전환 대응 — 값이 갈릴 자리가 있을 때만 두 값을 나란히 보인다 (조서 결과 시트와 같은 표)."""
    ov = L.cresp_overlap(t)
    if t.k_w <= 0 or not ov:
        return
    st.markdown('#### 매도청구 통지 뒤 전환 대응')
    st.warning(f'매도청구 행사일 {len(ov)}회가 전환청구기간과 겹치고 의무보유로 막히지 않습니다 '
               f'(첫 회차 발행 후 {ov[0]:,.1f}개월). 통지를 받은 투자자가 전환으로 피할 수 있는지에 따라 '
               '세 평가방법의 값이 모두 갈립니다 — 계약서의 매도청구 조항을 확인하십시오.')
    c1, c2 = st.columns([5, 1])
    with c2:
        source('priority')
    if c1.button('전환 대응 비교', key='jd_cr_btn'):
        with st.spinner('두 읽기로 다시 계산합니다.'):
            st.session_state.jd_cr = (_key(t), L.cresp_compare(t))
    got = _saved('jd_cr', t)
    if got is not None:
        st.dataframe(pd.DataFrame([[a, b, c, '◀ 적용' if on else ''] for a, b, c, on in got],
                                  columns=['통지 뒤 전환', '매도청구권', '전환권대가', '']).style.format(
            {'매도청구권': '{:,.4f}', '전환권대가': '{:,.4f}'}), hide_index=True, use_container_width=True)
    memo('conv_resp', run, '매도청구 조항')


def bdt_section(t, full, b0, b1, b2, ca, run):
    if L.is_sha(t):
        return
    st.markdown('#### 이자율모형(BDT) 검토')
    c1, c2 = st.columns([5, 1])
    with c2:
        source('bdt')
    if c1.button('BDT 적용 검토', key='jd_bdt_btn'):
        with st.spinner('금리·스프레드·변동성을 흔들어 다시 계산합니다.'):
            sig = L.rate_signals(t)
            review = L.bdt_review(t, full, b0, b1, b2, ca, sig)
            compare = None
            if L.put_bdt_avail(t):
                fixed = copy.deepcopy(t); fixed.put_bdt = 0
                tf = L.pick(L.engine(fixed, conv=False, put=True, call=False), fixed.model)
                compare = (tf, L.bond_bdt(t, True))
            st.session_state.jd_bdt = (_key(t), (sig, review, compare))
    got = _saved('jd_bdt', t)
    if got:
        sig, bd, compare = got
        st.dataframe(pd.DataFrame([[n_, q_, v_, '판단 필요' if ok_ is None else '예' if ok_ else '아니오']
                                   for n_, q_, v_, ok_, _ in bd['관문']],
                                  columns=['번호', '검토사항', '값', '상태']), hide_index=True, use_container_width=True)
        st.info(f"**{bd['결론']}** — {bd['사유']}")
        st.caption(f"민감도(전체 대비) — 금리 ±1%p {abs(sig['dl'])/max(b2, 1e-9):.2%} · 스프레드 ±1%p "
                   f"{abs(sig['ds'])/max(b2, 1e-9):.2%} · 변동성 ±10%p {abs(sig['dv'])/max(b2, 1e-9):.2%}")
        if compare:
            st.caption(f'부채요소 — TF 확정금리 {compare[0]:,.4f} · BDT {compare[1]:,.4f} · 차이 {compare[1]-compare[0]:+,.4f}')
        with st.expander('조서 문안'):
            st.code(bd['문안'], language=None)
    memo('bdt', run, 'BDT 판단')


def allocation_section(t, full, b0, b1, b2, ca):
    with st.expander('판정대로 배분하면'):
        rows, note = L.allocate(t, full, b0, b1, b2, ca)
        st.dataframe(pd.DataFrame([[k, v, fv] for k, v, fv in L.allocate_full(t, rows)],
                                  columns=['항목', '100 기준', '전액 기준 (원)']).style.format(
            {'100 기준': '{:,.4f}', '전액 기준 (원)': '{:,.0f}'}), hide_index=True, use_container_width=True)
        st.caption(note)


TOPIC_SOURCE = {'fair_value_inputs': 'fair_value_inputs', 'classification': 'classification',
                'redemption_constraint': 'redemption_constraint', 'dividends': 'dividends', 'holder': 'holder',
                'embedded': 'embedded', 'residual': 'residual', 'third_party_call': 'third_party_call',
                'bdt': 'bdt', 'refixing': 'refixing', 'dilution_backsolve': 'dilution_backsolve',
                'method_change': 'method_change'}
# 분리 판정·BDT·콜 절(판단·근거 탭)에서 이미 다루는 주제는 추가 검토 목록에서 뺀다.
COVERED = {'embedded', 'third_party_call', 'bdt'}


DAY1_CHECKS = [dict(id='day1_rights', title='[최초 인식 차이] 모형이 빠뜨린 권리가 있나요?',
                   questions=['잔여재산 우선분배, 주주간계약 풋, 조건부 매수청구권 등']),
              dict(id='day1_inputs', title='[최초 인식 차이] 입력값이 거래 당시와 맞나요?',
                   questions=['희석 반영 여부, 할인율, 변동성']),
              dict(id='day1_price', title='[최초 인식 차이] 거래가격이 공정가치가 아닐 수 있나요?',
                   questions=['특수관계자·이해관계인 거래, 다른 권리와 묶인 거래 (1113 B4)']),
              dict(id='day1_nonfin', title='[최초 인식 차이] 차이가 금융상품이 아닌 다른 것의 대가인가요?',
                   questions=['제3자에게 준 콜·용역 대가 등 — 자산 요건을 못 채우면 비용 (1109 B5.1.1 · 질의회신 2019-I-KQA018)'])]


def _topic_rows(rows, owner):
    """주제마다 한 줄 질문 + [원문] + 평가자 판단 한 줄. owner 가 None 이면(단독 상세앱) 판단을 저장하지 않는다."""
    for tp in rows:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{tp['title']}**")
        if tp.get('questions'):
            c1.caption(' / '.join(tp['questions'][:3]))
        with c2:
            source('day1' if tp['id'].startswith('day1') else TOPIC_SOURCE.get(tp['id'], tp['id']))
        memo(tp['id'], owner)


def review_topics(case):
    """Optional notes preserve legacy records without a mandatory questionnaire."""
    from valuation.evidence import TOPICS
    titles={tp['id']: tp['title'] for tp in TOPICS}
    titles.update(rcps_equity='일반기업회계기준 RCPS', conv_resp='매도청구 통지 뒤 전환 대응')
    titles.update({key:key for key in case.memos if key not in titles})
    st.caption('필요한 주제만 기록합니다. 메모는 평가금액을 바꾸지 않으며, 조건 변경은 관련 입력에서 반영해야 합니다. 과거 기록은 보존됩니다.')
    topic=st.selectbox('메모 주제',list(titles),format_func=titles.get)
    memo(topic,case)
    if topic in TOPIC_SOURCE:
        source(TOPIC_SOURCE[topic])


def day1_checks(run):
    """최초 인식 차이가 있을 때 원인 점검 4항목 — 최초 인식 결과 바로 아래."""
    day1 = run.summary.get('day1')
    if day1 and abs(day1['diff']) >= 0.005:
        with st.expander('최초 인식 차이 — 원인 점검 4항목'):
            _topic_rows(DAY1_CHECKS, run)


def render(t, full, b0, b1, b2, ca, conv, LB, run=None):
    if L.holder_on(t):
        st.caption('투자자 관점 — 아래 분리 판정은 발행자 기준입니다. 투자자는 복합계약 전체를 당기손익-공정가치로 측정합니다 (1109 4.3.2).')
    with st.expander('계약상 권리 (격자 의사결정 설계도)'):
        st.dataframe(pd.DataFrame(L.rights_table(t), columns=L.RIGHT_COLS), hide_index=True, use_container_width=True)
    facts_section(t, run)
    sp = split_section(t, full, b0, b1, b2, ca, LB, run)
    put_exercise_section(t)
    call_section(t, full, b2, sp, run)
    priority_section(t, ca, conv, run)
    cresp_section(t, ca, conv, run)
    bdt_section(t, full, b0, b1, b2, ca, run)
    allocation_section(t, full, b0, b1, b2, ca)
    with st.expander('조서에 옮길 분리 판단 문안'):
        st.code(L.inst_text(t, L.split_memo(sp)), language=None)
    return sp
