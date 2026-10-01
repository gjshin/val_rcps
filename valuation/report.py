"""Compact values-only workpaper built entirely from an existing evaluation."""
import io
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from .case import FIELDS, compare_cases, RIGHT_KINDS
from .presentation import label, display_value, issue_rows


def basic_workbook(run, previous=None, *, as_workbook=False):
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
    result_rows = [['평가 결과', '총액(원)', '1주당 가치(원, RCPS·SHA 단일 계약)', '원금 100 기준' if tm.inst != 'SHA' else '계산기준금액 100 기준'],
                   ['건명', run.case.name], ['평가기준일', tm.d_base], ['상품·회차', tm.inst, tm.tranche],
                   [('계산기준금액(원) — 주당 기준가격 × 대상 주식수' if tm.inst == 'SHA' else '평가대상 발행금액·투자원금(원)'), tm.face_total],
                   ['평가모형', tm.model], ['산출물 범위', '입력 조건에 따른 계산 결과']]
    for key, value in summary['amounts_100'].items():
        result_rows.append([AMOUNT_LABELS[key], summary['amounts_total'][key],
                            (summary['amounts_per_share'] or {}).get(key), value])
    result_rows += [['계산 구간 수', tm.n], ['평균 간격(일)', summary['grid']['average_days']],
                   ['계산시각(UTC)', summary['calculated_at']], ['기록 갱신시각(UTC)', summary['generated_at']],
                   ['계산 소요시간(초)', summary['calculation_seconds']],
                   ['입력 식별값', summary['case_sha256']], ['계산 코드 식별값', summary['code_sha256']],
                   ['검토메모', run.case.notes]]
    sheet('평가요약', result_rows)
    if summary.get('sha_rows'):
        # 주주간계약 회차별 표 — 회차마다 따로 잰 풋·콜과 합계 (미행사 물량은 다음 회차로 넘기지 않는다).
        cols = list(summary['sha_rows'][0])
        tot = ['합계'] + [''] * (len(cols) - 1)
        for k in ('풋 수량', '콜 수량', '같은 주식 물량 (연계 판단)', '풋 전액', '콜 전액'):
            if k in cols:
                tot[cols.index(k)] = sum(r[k] for r in summary['sha_rows'])
        sheet('회차별 결과', [cols] + [[r[k] for k in cols] for r in summary['sha_rows']] + [tot])
        if summary.get('sha_recon'):
            rc = list(summary['sha_recon'][0])
            sheet('수량 대사', [rc] + [[r[k] for k in rc] for r in summary['sha_recon']])
        from .legacy import SHA_ROW_KEYS, sha_row_defaults
        heads = dict(name='평가 구분', start='행사 시작일', end='행사 종료일', style='행사 방식', freq='주기(개월)',
                     price='주당 기준가격(원)', rate='가격 가산율(연)', acc_from='가격 가산 기산일(비우면 계약일)',
                     put_q='풋 수량', call_q='콜 수량', kill='한쪽 행사 시 상대 권리 소멸(1)',
                     link_q='같은 주식 물량(비우면 수량이 같을 때 전부)', status='평가 대상 상태', side='확정된 거래',
                     deal_px='확정 주당 매매대금(원)', settle='결제 예정일', cond_basis='조건부 평가 가정',
                     cond_note='추가 조건 내용', pool='같은 주식 묶음', pool_cap='묶음 공통 한도(주)', call_start='콜 시작일(비우면 풋과 같음)',
                     call_end='콜 종료일', call_price='콜 기준가격(원)', call_rate='콜 가산율(연)',
                     sig='회차 변동성(비우면 공통)', rf='회차 무위험 금리', pdisc='회차 풋 할인율', price_note='가격 산식 기록')
        heads['perf'] = '실적 연동 산식 (매출·차감·영업손익·기준·배수·주식수·연도)'
        _cell = lambda v: (' · '.join(f'{k}={v[k]}' for k in v) if isinstance(v, dict) else v)
        sheet('회차별 입력', [[heads.get(k, k) for k in SHA_ROW_KEYS]] +
              [[_cell(sha_row_defaults(r)[k]) for k in SHA_ROW_KEYS] for r in summary['applied_terms']['sha_rows']])
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
            rows.append([label(key), display_value(key, run.case.facts().get(key), tm.d_issue, tm.inst),
                         display_value(key, value, tm.d_issue, tm.inst), assumptions.get(key, {}).get('rationale', ''),
                         run.case.sources.get(key, ''), '기본값 적용' if key in run.case.imported_defaults or key in summary['engine_defaults'] else ''])
        sheet(name, rows)
    sheet('행사방식', [['권리·조정 항목', '계약상 방식', '계산 반영']] +
          [[label(k) if k != 'cv' else '전환·신주인수권', {'any':'기간 중 언제든지', 'periodic':'정기 행사·조정', 'single':'특정일에만 행사'}[v],
            '행사기간 내 모든 계산시점에 적용' if v == 'any' else '계약상 주기 또는 특정일 적용'] for k,v in run.case.exercise_styles.items()])
    sheet('계약검토안', [['문서', '조항·쪽수', '원문 발췌', '해석 초안', '후속 작업', '연결 입력']] +
          [[r['document'], r['clause'], r['quote'], r['interpretation'], r['action'], ', '.join(label(k) for k in r['fields'])] for r in run.case.contract_review.get('findings', [])])
    if run.case.contract_review.get('open_items'):
        sheet('추가확인자료', [['미확인 사항']] + [[x] for x in run.case.contract_review['open_items']])
    curves = [['금리곡선', '만기(년)', '원본 연이율(%)', '가정 적용 연이율(%)', '출처']]
    for key in ['rf_curve', 'cr_curve', 'cr_curve_b']:
        original = dict(run.case.facts().get(key, []))
        applied = dict(summary['applied_terms'][key])
        for tenor in sorted(original.keys() | applied.keys()):
            curves.append([label(key), tenor, original[tenor] * 100 if tenor in original else None,
                           applied[tenor] * 100 if tenor in applied else None, run.case.sources.get(key, '')])
    sheet('금리곡선', curves)
    sheet('평가가정', [['변경 항목', '적용 가정', '근거']] +
          [[label(r['field']), display_value(r['field'], r['value'], tm.d_issue, tm.inst), r['rationale']] for r in run.case.assumptions])
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
              [[label(r['field']), display_value(r['field'], r['previous'], inst=tm.inst), display_value(r['field'], r['current'], inst=tm.inst)]
               for r in compare_cases(previous, run.case)])
    if as_workbook:
        return wb
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


JUDGMENT_SHEETS = {'해설', '분리 판단', '검산요약', '99_모형검증'}


def calculation_sheets_only(data, *, accounting=False, judgment=False):
    """Drop GPT-era sign-off sheets; keep judgment·check sheets when asked.

    Fail closed if a retained formula depends on an omitted sheet.
    """
    is_workbook = isinstance(data, Workbook)
    wb = data if is_workbook else load_workbook(io.BytesIO(data))
    unwanted = {'V2_검토기록', 'V2_계약과가정',
                'V2_추가권리', '판단근거', '확정상태', '계약반영표', '독립검산대사', '시장자료확인',
                '기본값확인', '계약검토안', '추가확인자료', '별도계약조건', '평가자확인'}
    if not judgment:
        unwanted |= JUDGMENT_SHEETS
    if not accounting:
        unwanted |= {'회계처리', '상각표'}
    removed = unwanted & set(wb.sheetnames)
    for name in removed:
        del wb[name]
    for ws in wb:
        for cell in ws._cells.values():
            if cell.data_type == 'f' and any(f"'{name}'!" in cell.value or f'{name}!' in cell.value for name in removed):
                raise ValueError('상세 조서 수식이 제외된 시트를 참조합니다: ' + ws.title + '!' + cell.coordinate)
    if accounting:
        for name in ('회계처리', '상각표'):
            if name in wb:
                wb[name]['B1'] = '입력 가정에 따른 초안입니다. 계약별 회계처리는 별도로 검토하십시오.'
                wb[name]['B1'].font = Font(bold=True, color='9C0006')
    if is_workbook:
        return wb
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def append_controls(data, run, *, final=False):
    from .controls import coverage_rows, blockers, verification_rows, workflow_state, default_fields, input_key
    from .market_data import digest
    wb = load_workbook(io.BytesIO(data))
    records = run.case.review_controls
    first = wb['평가요약'] if '평가요약' in wb.sheetnames else wb.worksheets[0]
    status_row = next((row for row in first if row[0].value == '산출물 구분'), None)
    if status_row:
        status_row[1].value = '최종 값 조서' if final else '검토용 조서'
    else:
        first.append(['산출물 구분', '최종 값 조서' if final else '검토용 조서'])
    tables = {
        '확정상태': [['산출물 구분', '최종 값 조서' if final else '검토용 조서'],
            ['검토 진행상태', workflow_state(run.case, run)], ['범위', '기본 모형 결과. 조건부 분석값은 합산하지 않음.'],
            ['서명 구분', '사용자가 기록한 이름. 본인인증·전자서명 아님.'],
            ['작성자', records.get('review', {}).get('preparer', '')], ['검토자', records.get('review', {}).get('reviewer', '')],
            ['검토결론', records.get('review', {}).get('rationale', '')],
            ['확정시각', records.get('final', {}).get('confirmed_at', '') if final else ''],
            ['최종 확정 식별값', records.get('final', {}).get('key', '') if final else ''],
            ['남은 확인사항', '\n'.join(r['message'] for r in blockers(run))]],
        '계약반영표': [['항목', '저장 입력', '계산 적용값', '상태', '조항', '반영방식', '근거', '확인자']] +
            [[r['title'], r['inputs'], r['applied_inputs'], r['status'], r['record'].get('clause', r['clause']), r['record'].get('mode', ''),
              r['record'].get('rationale', ''), r['record'].get('reviewer', '')] for r in coverage_rows(run.case, run.summary['applied_terms'])],
        '독립검산대사': [['항목', '앱 결과(100당)', '독립 검산값(100당)', '차이', '허용차이', '일치 여부']] +
            [[r['item'], r['calculated'], r['reference'], r['difference'], r['tolerance'], r['passed']] for r in verification_rows(run)] +
            [['계산서·허용차이 근거', records.get('verification', {}).get('reference', '')], ['확인자', records.get('verification', {}).get('reviewer', '')]],
        '시장자료확인': [['항목', '기준일', '확인자', '검토근거', '입력 식별값']] +
            [[label(k), r['date'], r['reviewer'], r['rationale'], r['input_key']] for k,r in records.get('market', {}).items()],
        '기본값확인': [['검토 상태', '확인 완료' if records.get('defaults', {}).get('input_key') == input_key(run.case) else '확인 필요' if default_fields(run.case) else '보충값 없음'],
            ['확인자', records.get('defaults', {}).get('reviewer', '')], ['확인시각', records.get('defaults', {}).get('reviewed_at', '')],
            ['확인 근거', records.get('defaults', {}).get('rationale', '')], ['항목', '적용값']] +
            [[label(k), display_value(k, run.summary['applied_terms'][k], run.terms.d_issue, run.terms.inst)] for k in default_fields(run.case)],
    }
    tables['행사방식'] = [['권리·조정 항목', '계약상 방식', '계산 적용 주기(개월)']] + [
        [label(k) if k != 'cv' else '전환·신주인수권', {'any':'기간 중 언제든지', 'periodic':'정기 행사·조정', 'single':'특정일에만 행사'}[v],
         run.summary['applied_terms'].get(k, '행사기간 적용')] for k,v in run.case.exercise_styles.items()]
    tables['계약검토안'] = [['문서', '조항·쪽수', '원문 발췌', '해석 초안', '후속 작업', '연결 입력']] + [
        [r['document'],r['clause'],r['quote'],r['interpretation'],r['action'], ', '.join(label(k) for k in r['fields'])] for r in run.case.contract_review.get('findings', [])]
    if run.case.contract_review.get('open_items'):
        tables['추가확인자료'] = [['미확인 사항']] + [[x] for x in run.case.contract_review['open_items']]
    if run.case.market_evidence.get('sig'):
        pack = run.case.market_evidence['sig']
        tables['변동성원자료'] = ([['원본 데이터 식별값', pack['data_sha256']], ['조회 조건', str(pack['query'])],
            ['원본 파일 식별값', pack['query'].get('file_sha256', '')], ['자료 취득시각', pack['retrieved_at']],
            ['계산 옵션', str(pack['opt'])], ['적용 변동성', pack['sigma']], ['회사', '날짜', '수정종가']] +
            [[name, date, price] for name, prices in pack['series'] for date, price in prices])
    if run.case.cashflow_scenarios:
        from .cashflows import SCOPE
        tables['부분상환분석입력'] = ([['분석명', '근거', '입력 전체', '입력 식별값', '범위']] +
            [[s['name'], s['rationale'], str(s), digest(s), SCOPE] for s in run.case.cashflow_scenarios])
    for name, rows in tables.items():
        if name in wb.sheetnames:
            del wb[name]
        ws = wb.create_sheet(name, 0 if final and name == '확정상태' else len(wb.sheetnames))
        for row in rows:
            ws.append(row)
        ws.freeze_panes = 'A2'
        for cell in ws[1]:
            cell.font = Font(color='FFFFFF', bold=True)
            cell.fill = PatternFill('solid', fgColor='17365D')
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 38
        for row in ws:
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if isinstance(cell.value, str):
                    cell.data_type = 's'
    out = io.BytesIO(); wb.save(out)
    return out.getvalue()


DRAFT_NOTE = '입력 가정에 따른 초안입니다. 계약별 회계처리는 별도로 검토하십시오.'


INFO_SHEET = '조서 정보'


def info_sheet(wb, run, *, formula=False):
    """몇 줄짜리 기록을 한 장에 모은다 — 계산 정보 · 자료 출처 · 행사방식 · 확인할 사항.

    숫자가 맞는지 보는 확인(산술검산·모형검증·엑셀 재계산 대조)은 조서에 싣지 않는다 —
    앱이 평가할 때 돌리고, 걸리면 조서를 만들지 않는다.
    """
    s, t = run.summary, run.terms
    for old in ('계산정보', '확인사항', '산술검산', '출처기록', '행사방식', '수식대사', '변동성조회조건', INFO_SHEET):
        if old in wb:
            del wb[old]
    ws = wb.create_sheet(INFO_SHEET)
    ws.sheet_view.showGridLines = False
    for col, w in zip('ABCDE', (34, 60, 22, 22, 16)):
        ws.column_dimensions[col].width = w
    NAVY = PatternFill('solid', fgColor='17365D')
    r = [1]

    def title(text):
        c = ws.cell(r[0], 1, text); c.font = Font(bold=True, size=12, color='17365D'); r[0] += 1

    def head(cols):
        for j, h in enumerate(cols, 1):
            c = ws.cell(r[0], j, h); c.font = Font(bold=True, color='FFFFFF'); c.fill = NAVY
        r[0] += 1

    def row(vals):
        for j, v in enumerate(vals, 1):
            c = ws.cell(r[0], j, str(v) if isinstance(v, (dict, list, tuple)) else v)
            c.alignment = Alignment(vertical='top', wrap_text=True)
            if isinstance(v, str):
                c.data_type = 's'          # 입력·출처 문자열은 절대 엑셀 수식으로 읽히지 않는다
            elif isinstance(v, (int, float)):
                c.number_format = '#,##0.000000'
        r[0] += 1
        return r[0] - 1

    title('계산 정보'); head(['항목', '내용'])
    for k, v in [['건명', run.case.name], ['평가기준일', t.d_base], ['평가모형', t.model], ['구간 수', t.n],
                 ['간격 설정', f'{t.grid_days:g}일 기준' if t.grid_days else f'{t.gap_m:g}개월'],
                 ['평균 간격(일)', s['grid']['average_days']], ['입력 식별값', s['case_sha256']],
                 ['계산 코드 식별값', s['code_sha256']]]:
        row([k, v])
    r[0] += 1
    if run.case.sources:
        title('자료 출처'); head(['입력항목', '출처'])
        for k, v in run.case.sources.items():
            row([label(k), v])
        r[0] += 1
    pack = run.case.market_evidence.get('sig')
    if pack:
        title('변동성 조회 조건'); head(['항목', '내용'])
        for k, v in [['조회 조건', pack['query']], ['산출 옵션', pack['opt']], ['적용 변동성', pack['sigma']],
                     ['자료 출처', pack['source']], ['취득시각', pack['retrieved_at']]] + \
                [['제외·확인사항', x] for x in pack['warnings']]:
            row([k, v])
        r[0] += 1
    if run.case.exercise_styles:
        title('행사방식'); head(['항목', '방식'])
        for k, v in run.case.exercise_styles.items():
            row([label(k) if k != 'cv' else '전환·신주인수권',
                 {'any': '기간 중 언제든지', 'single': '특정일', 'periodic': '정기 행사'}[v]])
        r[0] += 1
    skip = {'source', 'legacy_defaults', 'engine_defaults', 'judgement_scope', 'market_date'}
    warnings = [i for i in run.issues if i.code not in skip]
    title('확인할 사항'); head(['항목', '확인할 사항', '수치 영향', '확인 방법'])
    for i in warnings:
        row([label(i.field), i.message, i.impact, i.action])
    if not warnings:
        row(['—', '없음'])
    r[0] += 1
    # 엑셀로 다시 계산한 값이 앱과 같은지는 앱이 조서를 만들 때 확인한다 — 조서에는 싣지 않는다.
    return ws


def calculation_table(wb, name, rows):
    if name in wb:
        del wb[name]
    ws = wb.create_sheet(name)
    for row in rows:
        ws.append([str(v) if isinstance(v, (dict, list, tuple)) else v for v in row])
    ws.freeze_panes = 'A2'
    ws.sheet_view.showGridLines = False
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='17365D')
    for cell in ws._cells.values():
        cell.alignment = Alignment(vertical='top', wrap_text=True)
        if isinstance(cell.value, str):
            cell.data_type = 's'
        elif isinstance(cell.value, (int, float)):
            cell.number_format = '#,##0.000000'
    for column in ws.columns:
        ws.column_dimensions[column[0].column_letter].width = 28
    return ws


def append_basic_accounting(wb, run):
    """Optional numeric accounting draft, without rebuilding stock lattices."""
    from . import legacy
    t, r = run.terms, run.raw
    if legacy.is_sha(t):
        # 세 당사자 — 발행회사 · 콜 권리자 · 풋 권리자. 원 단위(풋·콜 수량을 각각 곱한 값). 회차가 여럿이면 합계.
        items = r['rows'] if r.get('portfolio') else [dict(tm=t, R=r)]
        comp = {k: sum(legacy.sha_entry_comp_krw(x)[k] for x in items) for k in ('eq', 'put', 'call', 'gpv')}
        lines = legacy.sha_account_lines(items[0]['tm'] if len(items) == 1 else t, items[0]['R'],
                                         has_call=any(x['R'].get('has_call') for x in items),
                                         gross=(None if len(items) == 1 else False))
        rows = [[DRAFT_NOTE], ['당사자', '계정', '총액(원)', '설명']]
        for who in legacy.SHA_PARTIES:
            acc, memo = lines[who]
            for k, (name, c) in enumerate(acc):
                rows.append([who, name, legacy.sha_eval(c, comp), memo if k == 0 else ''])
        calculation_table(wb, '회계처리', rows)
        return
    args = [t] + [r[k] for k in ('full', 'b0', 'b1', 'b2', 'ca')]
    if legacy.holder_on(t):
        h = legacy.holder_rows(*args)
        rows = [[DRAFT_NOTE], ['계정', '100 기준', '총액(원)']]
        rows += [[k, v, v*t.face_total/100] for k,v in h['pos']]
        rows += [[], ['구분', '계정', '100 기준', '총액(원)']]
        rows += [[side, name, v, v*t.face_total/100] for side,name,v in h['journal']]
        if not h['journal']:
            rows.append(['분개 미산출', '후속평가 분개에는 투자자 전기 장부금액이 필요합니다.'])
    else:
        al = legacy.allocate(*args)[0]
        rows = [[DRAFT_NOTE], ['배분 항목', '100 기준', '총액(원)']]
        rows += [[k,v,v*t.face_total/100] for k,v in al]
        rows += [[], ['계정', '차변(100)', '대변(100)'], ['현금', 100., 0.]]
        rows += [[k, -v if v<0 else 0., v if v>=0 else 0.] for k,v in al[:-1]]
    calculation_table(wb, '회계처리', rows)
    eir = legacy.eir_or_none(*args)
    if eir is None:
        rows = [[DRAFT_NOTE], ['상각표', '현재 측정·분류 가정에서는 주계약 상각표를 산출하지 않습니다.']]
    else:
        rate, amort, redemption, periods = eir
        rows = [[DRAFT_NOTE], ['유효이자율', rate], ['상환금액(100)', redemption],
                ['회차', '기간(년)', '기초', '이자', '지급', '기말']] + list(amort)
    calculation_table(wb, '상각표', rows)


def judgment_rows(run):
    """판단·근거 시트 — 앱 판정(초안) · 핵심 수치 · 평가자 판단 · 근거 문단, 그리고 원문 발췌."""
    from . import legacy, sources
    from .evidence import TOPICS, applicable
    t, r = run.terms, run.raw
    memos = run.case.memos
    head = [['구분', '항목', '앱 판정(초안)', '핵심 수치', '평가자 판단', '평가자 근거', '근거 문단']]
    rows, used = [], []

    def add(kind, item, verdict, nums, memo_key, topic):
        m = memos.get(memo_key, {})
        rows.append([kind, item, verdict, nums, m.get('decision', '미답'), m.get('reason', ''), sources.cite(topic)])
        used.append(topic)

    if not legacy.is_sha(t):
        args = (t, r['full'], r['b0'], r['b1'], r['b2'], r['ca'])
        ah = legacy.acc_host(*args)
        sp = legacy.split_test(*args, [] if ah is None else legacy.eir_table(t, ah)[1])
        for key, nm, topic in [('warrant', '신주인수권', 'embedded'), ('put', '조기상환청구권', 'split_put'),
                               ('call', '매도청구권', 'third_party_call' if t.k_third else 'split_call')]:
            d = sp.get(key)
            if not d or not d['있음']:
                continue
            ind = d['지표']
            nums = (f"행사금액 {ind['첫 조기상환일 행사금액']:,.4f} / 상각후원가 {ind['같은 시점 상각후원가']:,.4f} / 차이 {ind['차이']:.2%} (비교기준 {ind.get('비교기준 (회계정책)', legacy.SPLIT_TOL):.0%} · 회계정책)"
                    if '첫 조기상환일 행사금액' in ind else '')
            add('분리 판정', nm, legacy.inst_text(t, d['결론'] + ' — ' + ' '.join(d['이유'])), nums, f'split_{key}', topic)
        if t.k_w > 0:
            method = {0: '유무가치비교법', 1: '옵션차익 혼합할인율', 2: '옵션차익 성분 분리할인 (주식결제·현금결제)'}[int(t.k_method)]
            add('평가방법', '매도청구권 평가방법', f'적용: {method}', f"콜 {r['ca']:,.4f}", 'call_method', 'call_method')
            if legacy.pc_overlap(t):
                add('평가방법', '풋·콜 우선순위', ['투자자 조기상환 우선', '발행자 매도청구 우선'][int(t.pc_order)], '', 'priority', 'priority')
        add('평가방법', '이자율모형(BDT)', '적용' if legacy.put_bdt_on(t) else '확정금리 격자', '', 'bdt', 'bdt')
    values = run.case.effective()
    for tp in TOPICS:
        if tp['id'] in {'embedded', 'third_party_call', 'bdt'} or not applicable(tp, values):
            continue
        add('추가 검토', tp['title'], ' / '.join(tp.get('questions', [])[:3]), '', tp['id'], tp['id'])
    day1 = run.summary.get('day1')
    if day1:
        add('최초 인식', '최초 인식 차이 처리', day1['verdict'], day1['nums'], 'day1_mode', 'day1')
        rows[-1][4], rows[-1][5] = day1['mode'], (t.d1_reason or ('기본 — 관측할 수 없는 투입변수 사용' if day1['mode'] == '이연' else ''))
        if abs(day1['diff']) >= 0.005:
            for key, label_ in DAY1_TOPICS:
                add('최초 인식 · 원인 점검', label_, '', '', key, 'day1')
    # 근거는 출처(자료명·문단·쪽)만 적는다 — 기준서·실무사례 원문은 조서에 싣지 않는다.
    return head + rows


DAY1_TOPICS = [('day1_rights', '모형이 빠뜨린 권리가 있나요?'), ('day1_inputs', '입력값이 거래 당시와 맞나요?'),
               ('day1_price', '거래가격이 공정가치가 아닐 수 있나요? (1113 B4)'),
               ('day1_nonfin', '차이가 금융상품이 아닌 다른 것의 대가인가요? (1109 B5.1.1 · 2019-I-KQA018)')]


def finish_calculation_workbook(wb, run, *, formula=False, accounting=False, judgment=False):
    """Keep reproducible inputs and numerical checks; judgment sheets on request."""
    calculation_sheets_only(wb, accounting=accounting, judgment=judgment)
    cal = run.case.calibration
    if cal:
        calculation_table(wb, '보정기록', [['항목', '내용'], ['보정일', cal['date']],
            ['보정 대상', '주가(S0)' if cal['target'] == 'S0' else '위험이자율 가산'],
            ['보정 전', cal['before']], ['보정 후', cal['after']], ['보정일 지분평가 주당가치', cal['equity_ps']],
            ['근거', cal['reason']], ['이번 평가 적용',
             f"주가 {run.terms.S0:,.4f} (출처: {run.terms.s0_src or '직접 입력'})"],
            ['이어 적용 식', '새 주가 = 보정 주가 × (이번 지분평가 주당가치 ÷ 보정일 지분평가 주당가치)'],
            ['근거 문단', 'K-IFRS 1113 문단 64 · 한공회 실무사례 3.4.2.4 보정(calibration)']])
    if judgment:
        ws = calculation_table(wb, '판단·근거', judgment_rows(run))
        ws.column_dimensions['D'].width = 40
        ws.column_dimensions['C'].width = 60
    s, t = run.summary, run.terms
    info_sheet(wb, run, formula=formula)
    pack = run.case.market_evidence.get('sig')
    # 변동성 산출내역(σ 시트)을 함께 실으면 같은 주가 자료라 따로 싣지 않는다.
    if pack and not any(n.startswith('σ ') for n in wb.sheetnames):
        calculation_table(wb, '변동성원자료', [['대상', '날짜', '수정종가']] +
            [[name,day,price] for name, prices in pack['series'] for day,price in prices])
    # Excel rejects overlong formulas. Stop with the exact cell before download.
    for ws in wb:
        for cell in ws._cells.values():
            if cell.data_type == 'f' and len(cell.value)-1 > 8192:
                raise ValueError(f'Excel 수식 길이 한도 초과: {ws.title}!{cell.coordinate} ({len(cell.value)-1:,}자)')
    from openpyxl.workbook.properties import CalcProperties
    wb.calculation = CalcProperties(calcMode='auto', fullCalcOnLoad=True)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
