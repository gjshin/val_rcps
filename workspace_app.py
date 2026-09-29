"""Professional workflow. Rendering and editing never invoke a pricing engine."""
import datetime as dt
import hashlib
import io
import json
import zipfile
from dataclasses import asdict
from typing import get_type_hints
import pandas as pd
import streamlit as st
from valuation.case import Case, SCHEMA, RIGHT_KINDS, FIELDS, REQUIRED, RCPS_REQUIRED, section_for, import_legacy, inspect_case, compare_cases
from valuation.legacy import Terms, months_to_date
from valuation.presentation import CHOICES, PERCENT, EVENT_DATES, label, display_value, event_months, issue_rows
from valuation.service import AMOUNT_LABELS, calculate, calculation_key, refresh_run, export_bundle
from valuation.analysis import sensitivity

TYPES = get_type_hints(Terms)
DEFAULTS = asdict(Terms())
COUNTS = {'sha_put_cmp', 'sha_call_cmp', 'cur_periods', 'ytm_cmp', 'k_cmp', 'cmp_rf', 'cmp_cr', 'p_cmp'}


def read_case(raw, name):
    obj = json.loads(raw)
    case = Case.from_dict(obj) if isinstance(obj, dict) and obj.get('schema') == SCHEMA else import_legacy(obj, name)
    malformed = [i for i in inspect_case(case) if i.severity == 'error' and i.code in
                 {'type', 'unknown_field', 'wrong_section', 'assumption_shape', 'assumption_field', 'right_shape', 'right_kind', 'right_text', 'source_type'}]
    if malformed:
        raise ValueError(' / '.join(f'{label(i.field)}: {i.message}' for i in malformed))
    return case


def install_case(case):
    st.session_state.case = case
    st.session_state.revision = st.session_state.get('revision', 0) + 1
    for key in ['run', 'bundle', 'analysis', 'bundle_key', '_input_pending']:
        st.session_state.pop(key, None)


def save_case(case):
    if st.session_state.case.to_dict() == case.to_dict():
        return
    run = st.session_state.get('run')
    st.session_state.case = case
    st.session_state.pop('bundle', None)
    if run and not any(i.severity == 'error' for i in inspect_case(case)):
        if run.summary['calculation_key'] == calculation_key(case):
            st.session_state.run = refresh_run(run, case)
    st.session_state.revision = st.session_state.get('revision', 0) + 1
    st.rerun()


def field(key, edited, case, prefix='input'):
    st.session_state.setdefault('_rendered_fields', set()).add(key)
    rev = st.session_state.get('revision', 0)
    required = REQUIRED | (RCPS_REQUIRED if edited.get('inst') == 'RCPS' else set())
    value = edited.get(key, None if key in required or key in EVENT_DATES else DEFAULTS.get(key))
    widget_key, title = f'{prefix}_{key}_{rev}', label(key)
    override = next((r for r in case.assumptions if r['field'] == key), None)
    if override and prefix == 'input':
        st.caption(f"{title}: 계산에는 별도 가정 {display_value(key, override['value'], case.contract.get('d_issue'))}을 적용합니다. ‘출처·평가가정’에서 변경하십시오.")
    styles = st.session_state.get('_editing_styles')
    if styles is not None and key in {'p_f', 'k_f', 'sha_put_f', 'sha_call_f', 'rfx_cyc'} and prefix == 'input':
        selected = st.selectbox(title.replace('주기(개월)', '방식'), ['periodic', 'any'],
            index=1 if styles.get(key) == 'any' else 0,
            format_func=lambda x: '기간 중 언제든지' if x == 'any' else '정기 행사·조정', key=widget_key + '_style')
        if selected != styles.get(key, 'periodic'):
            styles[key] = selected
        if selected == 'any':
            st.caption('행사기간 내 모든 계산시점에서 행사합니다. 계산 간격 변경 시에도 유지됩니다.')
            return
    if key in CHOICES:
        options = CHOICES[key]
        keys = list(options)
        new = st.selectbox(title, keys, index=keys.index(value) if value in keys else None,
                           format_func=lambda v: options[v], key=widget_key, placeholder='선택하십시오')
    elif key in EVENT_DATES:
        issue_date = edited.get('d_issue')
        if not issue_date:
            st.caption(f'{title}: 실제 발행일을 먼저 입력하십시오.')
            return
        original_date = case.contract.get('d_issue') or issue_date
        initial = months_to_date(original_date, value) if value is not None else None
        chosen = st.date_input(title, value=initial, min_value=dt.date(1900, 1, 1), max_value=dt.date(2200, 12, 31), key=widget_key)
        if chosen and chosen < dt.date.fromisoformat(issue_date):
            st.error(f'{title}이 발행일보다 빠릅니다.'); new = None
        else:
            new = event_months(issue_date, chosen, value, original_date)
    elif key.startswith('d_'):
        try:
            initial = dt.date.fromisoformat(value) if value else None
        except ValueError:
            initial = None
        new = st.date_input(title, value=initial,
                            min_value=dt.date(1900, 1, 1), max_value=dt.date(2200, 12, 31), key=widget_key)
        new = new.isoformat() if new else None
    elif TYPES[key] is int and key not in COUNTS:
        new = int(st.checkbox(title, value=bool(value), key=widget_key))
    elif TYPES[key] in (float, int):
        scale = 100 if key in PERCENT else 1
        displayed = float(value * scale) if value is not None else None
        number = st.number_input(title, value=displayed, format='%.8f' if key in PERCENT else '%.6f', key=widget_key)
        new = value if number == displayed else number / scale if number is not None else None
        if new is not None and TYPES[key] is int:
            if float(new).is_integer():
                new = int(new)
            else:
                st.error(f'{title}: 정수를 입력하십시오.')
    elif key in {'p_sched', 'k_sched'}:
        new = st.text_area(title, value=value or '', key=widget_key,
                           help='한 줄에 날짜 또는 발행 후 개월과 원금 대비 금액(%)을 입력합니다. 예: 2028-03-31 108.5')
    else:
        new = st.text_input(title, value=value or '', key=widget_key)
    if new is None:
        edited.pop(key, None)
    else:
        edited[key] = new


def fields(keys, edited, case):
    columns = st.columns(2)
    for idx, key in enumerate(keys):
        with columns[idx % 2]:
            field(key, edited, case)


def right_period(title, start, end, edited, case, extra, errors):
    rev = st.session_state.get('revision', 0)
    original_on = edited.get(start, 99) <= edited.get(end, 0)
    on = st.checkbox(title, value=original_on, key=f'right_{start}_{rev}')
    if on:
        if not original_on:
            edited.pop(start, None); edited.pop(end, None)
        fields([start, end] + extra, edited, case)
        if start in edited and end in edited and edited[start] > edited[end]:
            errors.append(f'{title}: 행사 시작일이 종료일보다 늦습니다.')
    elif original_on or start not in edited:
        edited[start], edited[end] = 99., 0.
    return on


def curve_editor(key, edited):
    st.session_state.setdefault('_rendered_fields', set()).add(key)
    current = edited.get(key, [])
    initial = pd.DataFrame([[x, y * 100] for x, y in current], columns=['만기(년)', '연이율(%)'], dtype=float)
    st.write(label(key))
    table = st.data_editor(initial, num_rows='dynamic', key=f'{key}_{st.session_state.get("revision", 0)}', hide_index=True,
                           column_config={'만기(년)': st.column_config.NumberColumn(min_value=.001, required=True),
                                          '연이율(%)': st.column_config.NumberColumn(required=True, format='%.6f')})
    if table.values.tolist() != initial.values.tolist():
        edited[key] = [[float(x), float(y) / 100] for x, y in table.values.tolist()]


def input_editor(case, autosave=False):
    st.session_state._rendered_fields = set()
    styles = dict(case.exercise_styles)
    st.session_state._editing_styles = styles
    edited = case.facts().copy()
    draft_errors = []
    inst = edited['inst']
    st.subheader('평가대상 및 기준일')
    fields(['tranche', 'view', 'd_issue', 'd_base', 'd_mat', 'face_total'], edited, case)
    if inst == 'RCPS':
        fields(['issue_px', 'par', 'mat_mode', 'div_mode', 'div_basis'], edited, case)
    if inst == 'BW':
        fields(['bw_pay', 'bw_detach'], edited, case)
    st.subheader('계약조건')
    field('K0', edited, case)
    if inst == 'SHA':
        st.caption('현재 전환가액 칸에는 주당 인수가액을 입력합니다. 풋과 콜을 각각 계산합니다.')
        right_period('주주간계약 풋 있음', 'sha_put_s', 'sha_put_e', edited, case, ['sha_put_f', 'sha_put_yield', 'sha_put_cmp'], draft_errors)
        right_period('주주간계약 콜 있음', 'sha_call_s', 'sha_call_e', edited, case, ['sha_call_f', 'sha_call_prem', 'sha_call_cmp'], draft_errors)
        fields(['sha_writer', 'sha_disc', 'sha_kill'], edited, case)
        if edited.get('sha_disc') == 2:
            field('sha_spread', edited, case)
    else:
        fields(['cpn', 'ipay'], edited, case)
        if inst != 'RCPS' or edited.get('mat_mode') == 1:
            fields(['ytm', 'ytm_cmp', 'mat_amt', 'm_less_cpn'], edited, case)
        if right_period('전환·신주인수권 행사 가능', 'cv_s', 'cv_e', edited, case, [], draft_errors):
            cv_style = st.selectbox('전환·신주인수권 행사방식', ['any', 'single'],
                index=1 if styles.get('cv') == 'single' else 0,
                format_func=lambda x: '기간 중 언제든지' if x == 'any' else '특정일에만 행사', key=f'cv_style_{st.session_state.get("revision",0)}')
            if cv_style != styles.get('cv', 'any'):
                styles['cv'] = cv_style
            if cv_style == 'single':
                edited['cv_e'] = edited.get('cv_s')
                st.caption('특정일 행사는 위 행사 시작일을 적용합니다. 종료일은 시작일과 같습니다.')
            else:
                st.caption('전환권은 행사기간 내 모든 계산시점에 행사할 수 있습니다.')
        put = right_period('투자자 상환청구권 있음', 'p_s', 'p_e', edited, case, ['p_f', 'p_mode'], draft_errors)
        if put:
            fields(['p_rate'] if edited.get('p_mode') == 'fixed' else ['p_yield', 'p_cmp'], edited, case)
            with st.expander('상환청구금액의 상세 조건'):
                fields(['p_less_cpn', 'p_cpn_add', 'p_sched'], edited, case)
        if inst == 'RCPS':
            field('issuer_call', edited, case)
            call_on = bool(edited.get('issuer_call'))
        else:
            call_on = st.checkbox('콜 권리 있음', value=edited.get('k_w', 0) > 0, key=f'call_{st.session_state.get("revision", 0)}')
        if call_on:
            old_call = bool(case.facts().get('k_w', 0) or case.facts().get('issuer_call', 0))
            changed_type = inst == 'RCPS' and edited.get('issuer_call') != case.contract.get('issuer_call', 0)
            if not old_call or changed_type:
                third_party = edited.get('issuer_call') == 2 if inst == 'RCPS' else edited.get('k_third', DEFAULTS['k_third'])
                initial_method = 2 if third_party and edited.get('model', 'TF') == 'TF' else 0
                edited['k_method'], edited['k_split'] = initial_method, 1
                for key in ['k_method', 'k_split']:
                    widget_key = f'input_{key}_{st.session_state.get("revision", 0)}'
                    if widget_key in st.session_state:
                        st.session_state[widget_key] = edited[key]
            st.caption('발행자 상환권은 콜 유무 가치 비교, 제3자 콜은 TF 옵션차익 지분·부채 분리할인을 초기 설정으로 사용합니다. 기존 평가파일의 선택은 유지하며, 다른 방법을 선택한 경우 근거를 기록하십시오.')
            fields(['k_s', 'k_e', 'k_f', 'k_prem', 'k_cmp'], edited, case)
            if inst == 'RCPS' and edited.get('issuer_call') == 1:
                edited['k_w'] = 1.
                st.caption('발행자 상환권은 전체 물량에 적용합니다.')
            else:
                fields(['k_w', 'k_hold'], edited, case)
                if edited.get('k_hold'):
                    fields(['k_lock', 'k_lock_put'], edited, case)
                fields(['k_method', 'k_split'], edited, case)
            with st.expander('콜 권리의 상세 조건'):
                fields(['k_kind', 'k_third', 'k_transfer', 'k_less_cpn', 'k_cpn_add', 'k_sched', 'k_basis', 'pc_order'], edited, case)
            if edited.get('k_w', 0) <= 0:
                draft_errors.append('콜 권리가 있으면 콜 대상 비율을 0%보다 크게 입력하십시오.')
            if edited.get('k_s', 0) > edited.get('k_e', float('inf')):
                draft_errors.append('콜 행사 시작일이 종료일보다 늦습니다.')
        else:
            edited['k_w'] = 0.
        field('rfx_mode', edited, case)
        if edited.get('rfx_mode'):
            fields(['rfx_cyc', 'floor', 'K_cap', 'carry'], edited, case)
    with st.expander('IPO 조건·미반영 권리 메모'):
        field('ipo_on', edited, case)
        if edited.get('ipo_on'):
            fields(['ipo_m', 'ipo_px', 'ipo_mult', 'ipo_min'], edited, case)
            field('sha_qipo_kill' if inst == 'SHA' else 'ipo_conv', edited, case)
        fields(['unmod_note', 'base_shares', 'dil_shares'], edited, case)
    st.subheader('시장자료 및 계산방법')
    fields(['S0', 'sig', 'div_y', 'model', 'gap_m', 'y_type', 'cmp_rf', 'cmp_cr'], edited, case)
    st.caption('비율은 % 단위입니다. 계산 간격은 월 환산값이며 실제 격자는 동일 시간 간격입니다. 정확한 7일 격자는 지원하지 않습니다.')
    curve_editor('rf_curve', edited); curve_editor('cr_curve', edited)
    with st.expander('금리곡선·BDT 상세 설정'):
        field('rate_mode', edited, case)
        if edited.get('rate_mode') == 'rating':
            fields(['rt_a', 'rt_b', 'rt_tgt'], edited, case); curve_editor('cr_curve_b', edited)
        if inst != 'SHA':
            field('put_bdt', edited, case)
            if edited.get('put_bdt'):
                fields(['bdt_sig', 'bdt_base', 'rvol_rating', 'rvol_tenor', 'rvol_how'], edited, case)
    with st.expander('분해방법·기간 기준'):
        st.caption('회계분류는 사용자의 가정입니다. 구성요소 차액은 회계상 인식액과 구분하십시오.')
        fields(['acc_basis', 'conv_class', 'p_sep', 'k_sep', 'p_lost_int', 'fvpl_whole'], edited, case)
    with st.expander('후속평가·역산·기타 상세 입력'):
        st.caption('기존 모형의 전체 입력항목을 같은 평가파일에서 관리합니다. 여기서 변경한 값도 평가·분석에 직접 적용됩니다.')
        remaining = sorted(FIELDS - st.session_state._rendered_fields - {'inst'})
        selected = st.multiselect('추가로 표시할 입력항목', remaining, format_func=label, key='additional_input_fields')
        for key in selected:
            if TYPES[key] is list:
                curve_editor(key, edited)
            else:
                field(key, edited, case)
    st.session_state.pop('_editing_styles', None)
    candidate = Case.from_dict(case.to_dict())
    for group in ['contract', 'market', 'method']:
        setattr(candidate, group, {k: v for k, v in edited.items() if section_for(k) == group})
    candidate.exercise_styles = styles
    changed = {k for k in edited if k in case.facts() and edited[k] != case.facts()[k]}
    supplied = {k for k in edited if k not in case.facts() and edited[k] == DEFAULTS.get(k)}
    candidate.imported_defaults = sorted((set(case.imported_defaults) | supplied) - changed)
    pending = candidate.facts() != case.facts() or styles != case.exercise_styles or bool(draft_errors)
    for message in draft_errors:
        st.error(message)
    if autosave:
        st.session_state._input_pending = bool(draft_errors)
    if autosave and pending and not draft_errors:
        save_case(candidate)
    if pending and not autosave:
        st.info('입력 변경사항이 아직 저장되지 않았습니다. 저장 후 평가·조서를 진행하십시오.')
    if not autosave and st.button('입력 저장', type='primary', disabled=bool(draft_errors)):
        save_case(candidate)
    return pending


def evidence_editor(case, pending=False):
    rev = st.session_state.get('revision', 0)
    with st.expander('자료 출처·검토메모'):
        with st.form(f'evidence_{rev}'):
            sources = dict(case.sources)
            sources['market_date'] = st.text_input('시장자료 기준일(YYYY-MM-DD)', sources.get('market_date', ''))
            for key in ['S0', 'sig', 'rf_curve', 'cr_curve', 'contract']:
                title = '계약서 위치·조항' if key == 'contract' else f'{label(key)} 출처·산출근거'
                sources[key] = st.text_input(title, sources.get(key, ''))
            notes = st.text_area('검토메모', case.notes)
            if st.form_submit_button('출처·메모 저장', disabled=pending):
                candidate = Case.from_dict(case.to_dict())
                candidate.sources, candidate.notes = sources, notes
                save_case(candidate)
    with st.expander('계약조건과 다른 평가가정'):
        st.caption('계약 원본은 유지하고 계산에 사용할 별도 가정과 근거를 기록합니다.')
        for idx, row in enumerate(case.assumptions):
            st.write(f"{label(row['field'])}: {display_value(row['field'], row['value'], case.contract.get('d_issue'))} · {row['rationale']}")
            if st.button('이 가정 삭제', key=f'del_assumption_{idx}_{rev}', disabled=pending):
                candidate = Case.from_dict(case.to_dict()); candidate.assumptions.pop(idx); save_case(candidate)
        options = sorted(k for k in case.facts() if k != 'inst' and TYPES[k] is not list)
        if options:
            key = st.selectbox('가정으로 변경할 항목', options, format_func=label, key=f'assumption_key_{rev}')
            proposed = case.effective()
            field(key, proposed, case, prefix='assumption')
            reason = st.text_input('가정 변경 근거', key=f'assumption_reason_{rev}')
            if st.button('가정 반영', disabled=pending or not reason.strip() or key not in proposed):
                candidate = Case.from_dict(case.to_dict())
                candidate.assumptions = [r for r in candidate.assumptions if r['field'] != key]
                candidate.assumptions.append(dict(field=key, value=proposed[key], rationale=reason)); save_case(candidate)
    with st.expander('별도 검토가 필요한 계약조건'):
        st.caption('아래 권리는 등록해도 직접 계산되지 않습니다. 미해결·제외·가정에 따른 근사 중 처리방식을 남기십시오.')
        for idx, row in enumerate(case.additional_rights):
            st.write(f"{RIGHT_KINDS[row['kind']]} · {row['clause']} · {row['rationale']}")
            if st.button('이 계약조건 삭제', key=f'del_right_{idx}_{rev}', disabled=pending):
                candidate = Case.from_dict(case.to_dict()); candidate.additional_rights.pop(idx); save_case(candidate)
        kind = st.selectbox('권리 유형', list(RIGHT_KINDS), format_func=RIGHT_KINDS.get, key=f'kind_{rev}')
        clause = st.text_input('계약 조항·원문', key=f'clause_{rev}')
        choices = {'unresolved': '미해결', 'excluded': '평가에서 제외', 'scenario': '별도 가정으로 근사'}
        treatment = st.selectbox('처리방식', list(choices), format_func=choices.get, key=f'treatment_{rev}')
        reason = st.text_input('처리 근거·남은 확인사항', key=f'right_reason_{rev}')
        links = st.multiselect('연결할 평가가정', [r['field'] for r in case.assumptions], format_func=label, key=f'links_{rev}') if treatment == 'scenario' else []
        if st.button('계약조건 기록', disabled=pending or not clause.strip() or (treatment != 'unresolved' and not reason.strip()) or (treatment == 'scenario' and not links)):
            candidate = Case.from_dict(case.to_dict())
            candidate.additional_rights.append(dict(kind=kind, clause=clause, treatment=treatment, rationale=reason, assumption_fields=links)); save_case(candidate)


def main():
    if not st.session_state.get('_app_embedded'):
        st.set_page_config(page_title='복합금융상품 평가', layout='wide')
    st.title('복합금융상품 평가')
    st.caption('계약 검토 → 입력·시장자료 → 평가·분석 → 검토조서 → 조서 출력')
    with st.sidebar:
        st.subheader('평가파일')
        with st.expander('새 평가 만들기'):
            with st.form('new_case'):
                name = st.text_input('평가 건명')
                instrument = st.selectbox('평가 상품', ['RCPS', 'CB', 'BW', 'SHA'])
                if st.form_submit_button('빈 입력안 만들기'):
                    if name.strip():
                        contract = dict(inst=instrument, rfx_mode=0, cpn=0., cv_s=99., cv_e=0., p_s=99., p_e=0., k_w=0.,
                                        sha_put_s=99., sha_put_e=0., sha_call_s=99., sha_call_e=0.)
                        install_case(Case(name=name.strip(), contract=contract, method=dict(model='TF', view='holder', gap_m=1.)))
                    else:
                        st.error('평가 건명을 입력하십시오.')
        upload = st.file_uploader('평가파일 불러오기', type='json')
        previous_upload = st.file_uploader('전기 평가파일(선택)', type='json', key='previous_upload')
        st.caption('전기 자료를 복사한 경우 새 기준일의 주당가치·변동성·금리를 확인하십시오.')
    if upload:
        digest = hashlib.sha256(upload.getvalue()).hexdigest()
        if st.session_state.get('upload_digest') != digest:
            try:
                install_case(read_case(upload.getvalue(), upload.name.removesuffix('.json')))
                st.session_state.upload_digest = digest
            except (ValueError, TypeError) as exc:
                st.error(f'평가파일을 읽을 수 없습니다: {exc}'); st.stop()
    previous = None
    if previous_upload:
        try:
            previous = read_case(previous_upload.getvalue(), previous_upload.name)
        except (ValueError, TypeError) as exc:
            st.error(f'전기 평가파일을 읽을 수 없습니다: {exc}')
    if previous:
        with st.sidebar:
            if st.button('전기 입력을 새 평가로 복사'):
                cloned = Case.from_dict(previous.to_dict())
                cloned.name += ' — 갱신'
                cloned.notes += '\n전기 입력 복사: 평가기준일과 시장자료, 계약 변경 여부를 확인할 것.'
                install_case(cloned)
    if 'case' not in st.session_state:
        st.info('왼쪽에서 새 평가를 만들거나 기존 평가파일을 불러오십시오.'); st.stop()
    case = st.session_state.case
    st.subheader(f'{case.name} · {case.contract.get("inst", "")}')
    # One active step only: hidden analyses never execute on input changes.
    stage = st.radio('평가 진행', ['계약 검토', '입력·시장자료', '평가·분석', '검토조서', '조서 출력'],
                     index=1, horizontal=True, key='_workflow_stage')
    st.sidebar.download_button('평가파일 저장', json.dumps(case.to_dict(), ensure_ascii=False, indent=2), '평가입력.json', 'application/json')
    st.sidebar.caption('입력은 현재 세션에 반영됩니다. 종료 전 평가파일을 저장하십시오.')
    pending = st.session_state.get('_input_pending', False)
    if pending and stage != '입력·시장자료':
        st.warning('입력화면에 저장되지 않은 오류가 있습니다. 입력·시장자료에서 확인하십시오.')
    if stage == '계약 검토':
        from contract_ui import main as contract_review
        contract_review(case)
        return
    if stage == '입력·시장자료':
        area = st.radio('입력 항목', ['계약·평가 입력', '주가·변동성·금리 자료', '출처·평가가정'], horizontal=True, key='_input_area')
        if area == '계약·평가 입력':
            pending = input_editor(case, autosave=True)
        elif area == '주가·변동성·금리 자료':
            from market_tools_ui import main as market_tools
            market_tools(case)
        else:
            evidence_editor(case)
        return
    if stage == '검토조서':
        review_kind = st.radio('검토 항목', ['계약·회계 판단', '입력 적정성', '독립 검산·승인'], horizontal=True)
        if review_kind == '계약·회계 판단':
            import evidence_ui
            evidence_ui.main()
        elif review_kind == '입력 적정성':
            from review_ui import input_review
            input_review(case)
        else:
            from review_ui import result_review
            saved = st.session_state.get('run')
            valid = saved is not None and not any(i.severity == 'error' for i in inspect_case(case)) and saved.summary['calculation_key'] == calculation_key(case)
            result_review(case, saved, valid, pending)
        return
    issues = inspect_case(case)
    errors = [i for i in issues if i.severity == 'error']
    if stage == '평가·분석':
        if errors:
            st.error(f'입력 오류 {len(errors)}건을 수정해야 평가할 수 있습니다.')
            st.dataframe(pd.DataFrame(issue_rows(errors)), hide_index=True)
        if pending:
            st.warning('저장하지 않은 입력이 있습니다. 입력 저장 후 실행하십시오.')
        if st.button('현재 입력으로 평가', type='primary', disabled=bool(errors) or pending):
            try:
                with st.spinner('입력한 조건으로 평가 중입니다.'):
                    old = st.session_state.get('run')
                    if old and old.summary['calculation_key'] == calculation_key(case):
                        st.session_state.run = refresh_run(old, case)
                    else:
                        st.session_state.run = calculate(case)
                        st.session_state.pop('analysis', None)
                    st.session_state.pop('bundle', None)
            except (ValueError, ArithmeticError) as exc:
                st.error(f'평가를 완료하지 못했습니다: {exc}')
        run = st.session_state.get('run')
        current = run is not None and not errors and run.summary['calculation_key'] == calculation_key(case)
        if run:
            if not current:
                st.warning('아래는 변경 전 입력의 결과입니다. 현재 입력으로 다시 평가해야 조서를 저장할 수 있습니다.')
            st.caption(f"평가기준일 {run.terms.d_base} · {run.terms.n:,}구간 · 계산 {run.summary['calculation_seconds']:.3f}초")
            values = run.summary['amounts_total']
            keys = ['put', 'call'] if run.terms.inst == 'SHA' else ['whole_before_call', 'call_deduction', 'net']
            for col, key in zip(st.columns(len(keys)), keys):
                col.metric(AMOUNT_LABELS[key] + ' (원)', f'{values[key]:,.0f}')
                if run.summary['amounts_per_share']:
                    col.caption(f"1주당 {run.summary['amounts_per_share'][key]:,.2f}원")
            with st.expander('구성요소·원금 100 기준 상세'):
                st.caption('순차 차감에 따른 참고값입니다. 회계상 인식액을 확정한 표가 아닙니다.')
                st.dataframe(pd.DataFrame([{'항목': AMOUNT_LABELS[k], '총액(원)': values[k], '원금 100 기준': v}
                                          for k, v in run.summary['amounts_100'].items()]), hide_index=True)
            st.subheader('산술 검산')
            st.dataframe(pd.DataFrame([{'검사': r['name'], '결과': '통과' if r['passed'] else '차이 발생', '범위': r['detail']}
                                      for r in run.summary['checks']]), hide_index=True)
            st.subheader('평가자가 확인할 사항')
            from valuation.evidence import evidence_cards
            cards = evidence_cards(case)
            incomplete = sum(c['status'] not in {'검토 완료', '해당 없음'} for c in cards)
            st.caption(f'관련 판단 주제 {len(cards)}개 · 미완료·재검토 {incomplete}개. 「검토조서 → 계약·회계 판단」에서 기준서 발췌와 실무사례를 확인하고 검토기록을 남길 수 있습니다.')
            st.dataframe(pd.DataFrame(issue_rows(run.issues)), hide_index=True)
            with st.expander('추가 분석 — 선택한 변수만 계산'):
                variable = st.selectbox('민감도 변수', ['S0', 'sig', 'rf_curve', 'cr_curve'], format_func=label)
                magnitude = st.number_input('변화폭(주당가치는 %, 변동성·금리는 %p)', min_value=.01, max_value=50. if variable == 'S0' else 10., value=10. if variable == 'S0' else 1.)
                st.caption('감소·증가 조건 각 1회, 총 2회 추가 평가합니다. 금리곡선은 모든 만기의 금리를 같은 폭으로 이동합니다.')
                if st.button('민감도 계산', disabled=not current or pending):
                    try:
                        with st.spinner('민감도 2개 조건 계산 중입니다.'):
                            st.session_state.analysis = sensitivity(run, variable, magnitude)
                    except (ValueError, ArithmeticError) as exc:
                        st.error(str(exc))
                analysis = st.session_state.get('analysis')
                if analysis and analysis['calculation_key'] == run.summary['calculation_key'] and current:
                    st.write(f"계산된 변수: {label(analysis['variable'])} / 변화폭: ±{analysis['change']:g}")
                    table = pd.DataFrame(analysis['rows']).rename(columns=AMOUNT_LABELS)
                    st.dataframe(table, hide_index=True)
                    st.download_button('민감도 결과 저장', table.to_csv(index=False).encode('utf-8-sig'), '민감도.csv', 'text/csv')
        if current:
            analysis_mode = st.selectbox('분석 도구', ['결과 요약', '상세 계산·회계 참고표', '계약조건 시나리오'])
            if analysis_mode == '상세 계산·회계 참고표':
                from application import detailed
                detailed(run)
            elif analysis_mode == '계약조건 시나리오':
                import scenario_ui
                scenario_ui.main()
        if previous:
            with st.expander('전기 대비 입력 변경'):
                st.dataframe(pd.DataFrame([{'항목': label(r['field']), '전기': str(r['previous']), '당기': str(r['current'])}
                                          for r in compare_cases(previous, case)]), hide_index=True)
    if stage == '조서 출력':
        run = st.session_state.get('run')
        current = run is not None and not errors and run.summary['calculation_key'] == calculation_key(case)
        from review_ui import result_review
        result_review(case, run, current, pending)
        st.download_button('평가 입력파일 저장', json.dumps(case.to_dict(), ensure_ascii=False, indent=2), '평가입력.json', 'application/json')
        st.subheader('검토용 조서')
        st.write('기본 조서: 결과 요약, 계약조건, 시장자료, 평가가정, 산술 검산, 확인사항 및 출처 기록')
        st.caption('기본 조서는 저장된 결과로 생성합니다. 추가 평가·회계분개·자동 결론 문안은 포함하지 않습니다.')
        option = st.radio('조서 구성', ['기본 값 조서', '상세 계산 값 조서', '상세 계산 수식 조서'])
        if option != '기본 값 조서':
            st.info('상세 조서는 추가 계산과 대형 격자 생성에 시간이 걸릴 수 있습니다. 자동 회계판단·분개 시트는 제외합니다. 수식 조서의 Excel 재계산은 이 화면에서 수행하지 않습니다.')
        if st.button('조서 생성', disabled=not current or pending):
            try:
                with st.spinner('조서를 생성하고 입력·결과 기록을 묶는 중입니다.'):
                    st.session_state.bundle = export_bundle(run, formula=option == '상세 계산 수식 조서', detail=option != '기본 값 조서', previous=previous)
                    st.session_state.bundle_key = (run.case.fingerprint(), option, previous.fingerprint() if previous else None)
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))
        bundle_key = (case.fingerprint(), option, previous.fingerprint() if previous else None) if current else None
        if current and not pending and st.session_state.get('bundle_key') == bundle_key and 'bundle' in st.session_state:
            data = st.session_state.bundle
            st.download_button('평가 조서 묶음 저장', data, '평가조서.zip', 'application/zip')
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = next(n for n in z.namelist() if n.endswith('.xlsx'))
                st.download_button('Excel 조서만 저장', z.read(name), '평가조서.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == '__main__':
    main()
