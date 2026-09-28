"""Compact values-only workpaper built entirely from an existing evaluation."""
import io
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from .case import FIELDS, compare_cases, RIGHT_KINDS
from .presentation import label, display_value, issue_rows


def basic_workbook(run, previous=None):
    from .service import AMOUNT_LABELS
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, rows):
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append([str(v) if isinstance(v, (list, dict, tuple)) else v for v in row])
        ws.freeze_panes = 'A2'
        ws.sheet_view.showGridLines = False
        for cell in ws[1]:
            cell.fill = PatternFill('solid', fgColor='17365D')
            cell.font = Font(color='FFFFFF', bold=True)
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 28 if len(column) < 3 else 36
        for row in ws:
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if isinstance(cell.value, str):
                    cell.data_type = 's'  # Customer-provided text is never an Excel formula.
                elif isinstance(cell.value, (float, int)):
                    cell.number_format = '#,##0.0000;[Red](#,##0.0000)'
        return ws

    tm, summary = run.terms, run.summary
    result_rows = [['평가 결과', '총액(원)', '1주당 가치(원, RCPS)', '원금 100 기준'],
                   ['건명', run.case.name], ['평가기준일', tm.d_base], ['상품·회차', tm.inst, tm.tranche],
                   ['평가대상 발행금액·투자원금(원)', tm.face_total],
                   ['평가모형', tm.model], ['산출물 범위', '계산 결과 및 검토기록. 계약·회계 판단은 평가자가 별도로 수행.']]
    for key, value in summary['amounts_100'].items():
        result_rows.append([AMOUNT_LABELS[key], summary['amounts_total'][key],
                            (summary['amounts_per_share'] or {}).get(key), value])
    result_rows += [['계산 구간 수', tm.n], ['평균 간격(일)', summary['grid']['average_days']],
                   ['계산시각(UTC)', summary['calculated_at']], ['기록 갱신시각(UTC)', summary['generated_at']],
                   ['계산 소요시간(초)', summary['calculation_seconds']],
                   ['입력 식별값', summary['case_sha256']], ['계산 코드 식별값', summary['code_sha256']],
                   ['검토메모', run.case.notes]]
    sheet('평가요약', result_rows)
    header = ['항목', '원본 입력', '적용값', '가정·선택 근거', '출처', '기본값 보충']
    assumptions = {row['field']: row for row in run.case.assumptions}
    for group, name in [('contract', '계약조건'), ('market', '시장자료'), ('method', '평가방법')]:
        rows = [header]
        from .case import section_for
        for key in sorted(FIELDS):
            if section_for(key) != group:
                continue
            value = summary['applied_terms'][key]
            # Curves have their own numeric table with explicit percent units.
            if isinstance(value, list):
                continue
            rows.append([label(key), display_value(key, run.case.facts().get(key), tm.d_issue),
                         display_value(key, value, tm.d_issue), assumptions.get(key, {}).get('rationale', ''),
                         run.case.sources.get(key, ''), '보충값 확인 필요' if key in run.case.imported_defaults or key in summary['engine_defaults'] else ''])
        sheet(name, rows)
    curves = [['금리곡선', '만기(년)', '원본 연이율(%)', '가정 적용 연이율(%)', '출처']]
    for key in ['rf_curve', 'cr_curve', 'cr_curve_b']:
        original = dict(run.case.facts().get(key, []))
        applied = dict(summary['applied_terms'][key])
        for tenor in sorted(original.keys() | applied.keys()):
            curves.append([label(key), tenor, original[tenor] * 100 if tenor in original else None,
                           applied[tenor] * 100 if tenor in applied else None, run.case.sources.get(key, '')])
    sheet('금리곡선', curves)
    sheet('평가가정', [['변경 항목', '적용 가정', '근거']] +
          [[label(r['field']), display_value(r['field'], r['value'], tm.d_issue), r['rationale']] for r in run.case.assumptions])
    sheet('산술검산', [['검사 항목', '결과', '검사 범위']] +
          [[r['name'], '통과' if r['passed'] else '차이 발생', r['detail']] for r in summary['checks']])
    rows = issue_rows(run.issues)
    sheet('평가자확인', [list(rows[0])] + [list(r.values()) for r in rows] if rows else [['확인사항 없음']])
    treatments = {'unresolved': '미해결', 'excluded': '평가에서 제외', 'scenario': '별도 가정으로 근사'}
    sheet('별도계약조건', [['권리', '계약조항', '처리방식', '판단근거', '연결 가정']] +
          [[RIGHT_KINDS[r['kind']], r['clause'], treatments[r['treatment']], r['rationale'],
            ', '.join(label(k) for k in r['assumption_fields'])] for r in run.case.additional_rights])
    sheet('출처기록', [['항목', '자료 위치·기준일·근거']] + [[label(k), v] for k, v in run.case.sources.items()])
    from .evidence import evidence_rows
    sheet('판단근거', evidence_rows(run.case))
    if run.case.contract_scenarios:
        from .contract_analysis import SCOPE
        rows = [['분석명', '계약·일정 근거', '분석 종료일', '종료 처리', '계약 일정', '적용 범위']]
        rows += [[s['name'], s['rationale'], s['end_date'], s['terminal'], s['schedule'], SCOPE] for s in run.case.contract_scenarios]
        sheet('별도분석입력', rows)
    if previous:
        sheet('전기입력비교', [['항목', '전기', '당기']] +
              [[label(r['field']), display_value(r['field'], r['previous']), display_value(r['field'], r['current'])]
               for r in compare_cases(previous, run.case)])
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def calculation_sheets_only(data):
    """Exclude historical auto-opinions from optional detailed calculation exports.

    Fail closed if a retained formula depends on an omitted sheet.
    """
    wb = load_workbook(io.BytesIO(data))
    removed = {'해설', '분리 판단', '검산요약', '99_모형검증', '회계처리', '상각표'} & set(wb.sheetnames)
    for name in removed:
        del wb[name]
    for ws in wb:
        for row in ws:
            for cell in row:
                if cell.data_type == 'f' and any(f"'{name}'!" in cell.value or f'{name}!' in cell.value for name in removed):
                    raise ValueError('상세 조서 수식이 제외된 회계·판단 시트를 참조합니다. 기본 값 조서를 사용하십시오.')
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
