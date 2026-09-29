"""Review-only UI. Never invokes a valuation engine on page rendering."""
import datetime as dt
import pandas as pd
import streamlit as st
from valuation import controls
from valuation.presentation import label, display_value
from valuation.legacy import Terms
from dataclasses import asdict

MODES = {'base': '기본 모형 입력', 'excluded': '해당 없음·평가 제외', 'assumption': '입력 가정으로 근사', 'conditional': '조건부 분석만 수행'}


def input_review(case, pending):
    from workspace_app import save_case
    rev = st.session_state.get('revision', 0)
    with st.expander('계약 반영표 — 조항과 적용값 확인'):
        st.caption('저장 입력과 계산 적용값이 조항에 맞는지 확인하고 제외·근사 근거를 기록하십시오. 별도 권리는 기본 모형에 직접 반영되었다고 표시할 수 없습니다.')
        from valuation.service import calculation_key
        run = st.session_state.get('run')
        applied = run.summary['applied_terms'] if run and run.summary['calculation_key'] == calculation_key(case) else None
        rows = controls.coverage_rows(case, applied)
        table = pd.DataFrame([{'항목': r['title'], '저장 입력': r['inputs'], '계산 적용값': r['applied_inputs'], '상태': r['status'],
            '조항·원문 위치': r['record'].get('clause', r['clause']), '반영방식': MODES.get(r['record'].get('mode'), MODES['base'] if not r['right'] else MODES['conditional']),
            '판단근거': r['record'].get('rationale', ''), '이번에 확인': False} for r in rows])
        with st.form(f'coverage_{rev}'):
            edited = st.data_editor(table, hide_index=True, disabled=['항목', '저장 입력', '계산 적용값', '상태'],
                column_config={'반영방식': st.column_config.SelectboxColumn(options=list(MODES.values())), '이번에 확인': st.column_config.CheckboxColumn()})
            reviewer = st.text_input('계약 대조자')
            if st.form_submit_button('선택한 계약조건 확인 기록', disabled=pending):
                try:
                    candidate = case
                    for item, row in zip(rows, edited.to_dict('records')):
                        if row['이번에 확인']:
                            candidate = controls.record_control(candidate, 'coverage', key=item['id'], reviewer=reviewer,
                                rationale=row['판단근거'], clause=row['조항·원문 위치'], mode=next(k for k,v in MODES.items() if v == row['반영방식']))
                    save_case(candidate)
                except (ValueError, TypeError, StopIteration) as exc:
                    st.error(str(exc))
    with st.expander('기본값·비적용 설정 확인'):
        defaults = controls.default_fields(case)
        values = {**asdict(Terms()), **case.effective()}
        if defaults:
            st.dataframe(pd.DataFrame([{'항목': label(k), '적용값': display_value(k, values[k], values['d_issue'])} for k in defaults]), hide_index=True)
            record = case.review_controls.get('defaults', {})
            st.caption('현재 입력 확인 완료' if record.get('input_key') == controls.input_key(case) else '기본값을 그대로 저장해도 확인 기록이 자동으로 생기지 않습니다.')
            with st.form(f'default_review_{rev}'):
                reviewer = st.text_input('설정 확인자')
                rationale = st.text_area('기본값의 적정성·비적용 근거')
                checked = st.checkbox('위 설정과 누락된 계약조건이 없는지 확인했습니다')
                if st.form_submit_button('기본값 확인 기록', disabled=pending):
                    try:
                        if not checked:
                            raise ValueError('설정 목록을 확인한 후 체크하십시오.')
                        save_case(controls.record_control(case, 'defaults', reviewer=reviewer, rationale=rationale))
                    except ValueError as exc:
                        st.error(str(exc))
        else:
            st.caption('자동으로 보충된 기본값이 없습니다.')
    with st.expander('시장자료 기준일 확인'):
        st.caption('각 입력값의 출처는 「자료 출처·검토메모」에 기록합니다. 기준일로 환산한 자료라면 환산 근거도 남기십시오.')
        with st.form(f'market_review_{rev}'):
            records = case.review_controls.get('market', {})
            date_text = st.text_input('검토한 시장자료 기준일(YYYY-MM-DD)', case.sources.get('market_date', ''))
            selected = st.multiselect('이번에 확인한 항목', ['S0', 'sig', 'rf_curve', 'cr_curve'], format_func=label)
            reviewer = st.text_input('시장자료 확인자')
            rationale = st.text_area('원자료 대조·기준일 조정·피어 선정 근거')
            if st.form_submit_button('시장자료 확인 기록', disabled=pending):
                try:
                    dt.date.fromisoformat(date_text)
                    if date_text != case.effective().get('d_base'):
                        raise ValueError('평가기준일과 다릅니다. 기준일에 맞게 자료를 조정한 후 확인하십시오.')
                    candidate = case
                    for key in selected:
                        candidate = controls.record_control(candidate, 'market', key=key, reviewer=reviewer, rationale=rationale, date=date_text)
                    save_case(candidate)
                except ValueError as exc:
                    st.error(str(exc))
        st.dataframe(pd.DataFrame([{'항목': label(k), '기록 기준일': records.get(k, {}).get('date', ''),
            '상태': '확인 완료' if records.get(k, {}).get('input_key') == controls.input_key(case) else '확인 필요'} for k in ['S0', 'sig', 'rf_curve', 'cr_curve']]), hide_index=True)


def result_review(case, run, current, pending):
    from workspace_app import save_case
    st.subheader('검토 및 최종 확정')
    st.write('진행 상태: ' + controls.workflow_state(case, run if current else None))
    st.caption('최종 확정은 기록된 검토절차의 완료를 뜻합니다. 입력한 이름은 본인인증·전자서명이 아니며, 앱이 회계 판단이나 평가의견을 보증하지 않습니다.')
    if not current or pending:
        st.info('입력을 저장하고 현재 입력으로 평가하면 검토·확정 절차를 진행할 수 있습니다.')
        return
    remaining = controls.blockers(run)
    if remaining:
        st.dataframe(pd.DataFrame([{'남은 확인사항': row['message']} for row in remaining]), hide_index=True)
    with st.expander('독립 검산값과 대사'):
        st.caption('이 앱에서 복사한 값을 검산값으로 사용하지 마십시오. 별도 계산서·외부 모형의 산식과 범위를 확인하고 원금 100 기준 값을 입력하십시오.')
        rev = st.session_state.get('revision', 0)
        with st.form(f'independent_{rev}'):
            values = {k: st.number_input('독립 검산: ' + label(k) + ' (원금 100 기준)', value=None, format='%.8f') for k in (['put', 'call'] if run.terms.inst == 'SHA' else ['net'])}
            absolute = st.number_input('절대 허용차이(원금 100 기준)', value=.000001, min_value=0., format='%.8f')
            relative = st.number_input('상대 허용차이(%)', value=.001, min_value=0., max_value=1., format='%.6f')
            reference = st.text_area('독립 계산서 위치·산식·허용차이 근거')
            reviewer = st.text_input('독립 검산 확인자')
            if st.form_submit_button('검산 대사 기록'):
                try:
                    save_case(controls.record_verification(run, reviewer=reviewer, reference=reference, values=values,
                                                          absolute_tolerance=absolute, relative_tolerance=relative/100))
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))
        st.dataframe(pd.DataFrame(controls.verification_rows(run)).rename(columns={'item':'항목', 'calculated':'앱 결과', 'reference':'독립 검산값', 'difference':'차이', 'tolerance':'허용차이', 'passed':'일치'}), hide_index=True)
    with st.expander('검토 완료 기록'):
        st.caption('계약 반영표·판단 근거·독립 검산을 완료한 후 작성자와 다른 검토자가 확인합니다. 평가 결과 탭의 모든 확인사항과 조서의 적용입력·정규화 내역도 검토하십시오.')
        with st.form(f'approval_{st.session_state.get("revision", 0)}'):
            preparer = st.text_input('작성자')
            reviewer = st.text_input('최종 검토자')
            rationale = st.text_area('검토결론·경고사항 처리·잔여 제한사항')
            if st.form_submit_button('검토 완료 기록', disabled=bool(controls.blockers(run, require_review=False))):
                try:
                    save_case(controls.approve_review(run, preparer, reviewer, rationale))
                except ValueError as exc:
                    st.error(str(exc))
    if st.button('현재 결과 최종 확정', disabled=bool(remaining) or controls.is_final(run)):
        try:
            save_case(controls.finalize(run))
        except ValueError as exc:
            st.error(str(exc))
    if controls.is_final(run):
        st.success('이 입력과 검토기록에 대한 값 조서를 확정했습니다. 입력·출처·코드·검토기록이 바뀌면 다시 확인해야 합니다.')
        if st.button('최종 값 조서 생성'):
            from valuation.service import export_final_bundle
            try:
                st.session_state.final_bundle = export_final_bundle(run)
                st.session_state.final_bundle_key = case.fingerprint()
            except ValueError as exc:
                st.error(str(exc))
        if st.session_state.get('final_bundle_key') == case.fingerprint():
            st.download_button('최종 값 조서 묶음 저장', st.session_state.final_bundle, '최종평가조서.zip', 'application/zip')
