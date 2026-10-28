"""주식선택권 값 조서 — 이미 평가한 실행 결과(run)로만 만든다. 새로 계산하지 않는다.

기본 값 조서는 report.basic_workbook 이 공통 시트를 만들고 여기의 시트를 더한다.
상세 값 조서는 회차마다 이항격자 전체(옵션 1주당 가치·주가·행사 노드)를 싣는다.
조건 변경이면 회차마다 변경 전·후 격자를, 현금결제형이면 결산일 격자를 싣는다.
"""
import math
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import stock_option as SO

# 주식선택권 평가에 쓰는 입력 — 조서의 계약조건·시장자료·평가방법 시트에 이것만 싣는다.
INPUT_KEYS = frozenset({
    'inst', 'tranche', 'd_issue', 'd_base', 'd_mat', 'K0', 'so_settle', 'so_fy_end', 'base_shares', 'dil_shares',
    'unmod_note', 'S0', 'sig', 'div_y', 'y_type', 'cmp_rf', 'ticker', 's0_src', 's0_date', 's0_raw', 's0_adj',
    's0_splits', 'so_ex', 'so_mult', 'so_exit', 'grid_days', 'gap_m', 'so_mod', 'so_prev_liab',
})
# 합계 줄에 더할 칸 — 평가 종류마다
SUM_COLS = {'grant': ('부여 수량', '공정가치', '보상원가'),
            'mod': ('변경 전 수량', '늘어난 수량', '당초 보상원가', '증분 공정가치', '증분 보상원가'),
            'cash': ('부여 수량', '공정가치', '가득 예상 수량의 공정가치', '주식보상부채')}
MODE_TEXT = {'grant': '부여일 평가', 'mod': '조건 변경 평가 (증분 공정가치)', 'cash': '현금결제형 결산일 재측정'}
NAVY = PatternFill('solid', fgColor='17365D')
GO = PatternFill('solid', fgColor='FCE4D6')        # 행사 노드
HEAD = Font(bold=True, color='FFFFFF')
DRAFT_NOTE = '입력 가정에 따른 초안입니다. 계약별 회계처리는 별도로 검토하십시오.'


def summary_rows(run):
    """평가요약 시트의 줄."""
    t, s, raw = run.terms, run.summary, run.raw
    tot, ps, a = s['amounts_total'], s['amounts_per_share'], s['amounts_100']
    from .service import AMOUNT_LABELS
    md = raw['mode']
    rows = [['평가 결과', '총액(원)', '1주당 금액(원)', '측정기준일 주가 100 기준'],
            ['건명', run.case.name], ['평가 종류', MODE_TEXT[md]], ['측정기준일', t.d_base],
            ['상품·차수', '주식선택권', t.tranche],
            ['결제 방식', SO.SETTLE_LABELS[int(t.so_settle)]], ['부여일', t.d_issue],
            ['행사가격(원)', t.K0 if md != 'mod' else f'{t.K0:,.2f} (변경 전 — 회차별 변경 후 값은 «조건 변경» 시트)'],
            ['측정기준일 주가(원)', t.S0], ['부여 수량(주)', raw['qty']],
            ['평가모형', '이항격자 (CRR) — 회차마다 따로 · 블랙-숄즈 대조'],
            ['조기행사 가정', SO.EX_LABELS[int(t.so_ex)] + (f' · 행사 배수 {t.so_mult:g}배' if int(t.so_ex) == 2 else '')],
            ['가득 뒤 연간 퇴사율', t.so_exit],
            ['산출물 범위', '입력 조건에 따른 계산 결과']]
    for key in SO.AMOUNT_KEYS[md]:
        rows.append([AMOUNT_LABELS[key], tot[key], ps[key], a[key]])
    if md == 'mod':
        rows.append(['당초 보상원가(원) — 부여일 공정가치 기준', raw['grant_cost']])
    if md == 'cash':
        rows += [['전기말 주식보상부채(원)', raw['prev_liab']], ['당기 비용(원) = 당기말 부채 − 전기말 부채', raw['expense']]]
    rows += [['계산 구간 수 (가장 긴 회차)', max(r['n'] for r in raw['rows'])],
             ['계산시각(UTC)', s['calculated_at']], ['기록 갱신시각(UTC)', s['generated_at']],
             ['계산 소요시간(초)', s['calculation_seconds']],
             ['입력 식별값', s['case_sha256']], ['계산 코드 식별값', s['code_sha256']],
             ['검토메모', run.case.notes]]
    return rows


def row_table(run):
    rows = run.summary['so_rows']
    cols = list(rows[0])
    tot = ['합계'] + [''] * (len(cols) - 1)
    for k in SUM_COLS[run.raw['mode']]:
        tot[cols.index(k)] = sum(r[k] for r in rows)
    return [cols] + [[r[k] for k in cols] for r in rows] + [tot]


def mod_input_table(run):
    heads = ['회차'] + [t for k, t in (('K', '변경 후 행사가격(원)'), ('vest', '변경 후 가득일'), ('start', '변경 후 행사 시작일'),
                                     ('end', '변경 후 행사 종료일'), ('qty', '변경 후 수량(주)'), ('grant_fv', '부여일 1주당 공정가치(원)'))]
    keys = ['name', 'K', 'vest', 'start', 'end', 'qty', 'grant_fv']
    return [heads] + [[m.get(k, '변경 없음' if k not in ('name', 'grant_fv') else '') for k in keys]
                      for m in (run.terms.so_mod_rows or [])]


def input_rows_table(run):
    heads = ['회차', '가득일 (비용 배분 종료)', '행사 시작일', '행사 종료일', '부여 수량(주)', '가득 예상 비율']
    return [heads] + [[r['name'], r['vest'], r['start'], r['end'], r['qty'], r['keep']] for r in SO.rows(run.terms)]


def bs_table(run):
    """블랙-숄즈 대조 — 조기행사·퇴사가 없을 때의 값. 격자값이 이보다 크면 이상이다."""
    t = run.terms
    S, q, sig = float(t.S0), float(t.div_y), float(t.sig)
    out = [['회차', '만기 구분', '기간(년)', '무위험 현물금리(연속)', 'd1', 'd2', 'N(d1)', 'N(d2)',
            '블랙-숄즈 1주당(원)', '격자 1주당(원)', '격자 − 블랙-숄즈']]
    for name, r in lattices(run).items():
        K = float(r.get('K') or t.K0)
        for kind, T in (('행사 종료일', r['t_end']), ('기대존속기간', r['life'])):
            rate = r['r_end'] if kind == '행사 종료일' else None
            if rate is None:
                rate = _rate(run, T)
            bs = SO.black_scholes(S, K, T, rate, q, sig)
            if T <= 0:
                # 평가일에 바로 행사 — 남은 기간이 없어 d1·d2 가 정의되지 않는다 (값 = 내재가치)
                out.append([name, kind, T, rate, None, None, None, None, bs, r['fv'], r['fv'] - bs])
                continue
            v = sig*math.sqrt(T)
            d1 = (math.log(S/K) + (rate - q + 0.5*sig*sig)*T)/v
            d2 = d1 - v
            out.append([name, kind, T, rate, d1, d2, SO._ncdf(d1), SO._ncdf(d2), bs, r['fv'], r['fv'] - bs])
    return out


def lattices(run):
    """조서에 싣는 격자 {이름: 격자} — 조건 변경이면 회차마다 변경 후·변경 전."""
    if run.raw.get('mode') == 'mod':
        out = {}
        for a, b in zip(run.raw['rows'], run.raw['before_rows']):
            out[f"{a['name']} 변경 후"] = a
            out[f"{b['name']} 변경 전"] = b
        return out
    return {r['name']: r for r in run.raw['rows']}


def _rate(run, T):
    horizon = max(r['t_end'] for r in lattices(run).values())
    return SO.rate_fn(run.terms, horizon)(max(T, 1e-6))


def schedule_table(run):
    rows = run.summary['so_schedule']
    cols = list(rows[0])
    return [cols] + [[r[k] for k in cols] for r in rows]


def journal_rows(run):
    """결산일별 분개 — (차) 주식보상비용 / (대) 주식선택권(자본조정). 현금결제형은 주식보상부채."""
    if run.raw['mode'] == 'cash':
        raw, d = run.raw, run.terms.d_base
        v = raw['expense']
        rows = [[DRAFT_NOTE], ['결산일', '차변 계정', '차변 금액(원)', '대변 계정', '대변 금액(원)']]
        rows.append([d, '주식보상비용', v, '주식보상부채', v] if v >= 0 else [d, '주식보상부채', -v, '주식보상비용 (환입)', -v])
        rows += [[], ['전기말 주식보상부채', raw['prev_liab']], ['당기말 주식보상부채', raw['liab']],
                 ['재측정', '결제될 때까지 매 결산일과 결제일에 공정가치로 다시 재고, 변동액을 당기손익으로 인식합니다 (문단 30).'],
                 ['결제 시', '(차) 주식보상부채 (대) 현금 — 행사일의 주가 − 행사가격 차액']]
        return rows
    by_end = {}
    for r in run.summary['so_schedule']:
        by_end[r['결산일']] = by_end.get(r['결산일'], 0.0) + r['당기 비용']
    rows = [[DRAFT_NOTE], ['결산일', '차변 계정', '차변 금액(원)', '대변 계정', '대변 금액(원)']]
    rows += [[d, '주식보상비용', v, '주식선택권 (자본조정)', v] for d, v in by_end.items() if abs(v) > 0.005]
    rows += [[], ['행사 시', '현금 (행사가격 × 행사 수량) + 주식선택권', '', '자본금 · 주식발행초과금', ''],
             ['소멸 시', '가득된 뒤 행사되지 않고 소멸해도 인식한 비용은 환입하지 않습니다 (문단 23). 자본계정 사이의 대체는 할 수 있습니다.'],
             ['재추정', '결산일마다 가득 예상 수량을 다시 추정하고, 누적 비용을 새 추정에 맞춥니다 (문단 20).'],
             ['일반기업회계기준', '중소기업은 행사할 때까지 회계처리를 하지 않을 수 있습니다 (제31장 문단 31.8).']]
    return rows


def _table(wb, name, rows, width=24):
    ws = wb.create_sheet(name)
    for row in rows:
        ws.append([str(v) if isinstance(v, (dict, list, tuple)) else v for v in row])
    ws.freeze_panes = 'A2'
    ws.sheet_view.showGridLines = False
    for cell in ws[1]:
        cell.font, cell.fill = HEAD, NAVY
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if isinstance(cell.value, str):
                cell.data_type = 's'            # 입력 문자열은 엑셀 수식으로 읽히지 않는다
            elif isinstance(cell.value, float):
                cell.number_format = '#,##0.000000'
    for col in range(1, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    return ws


def add_basic_sheets(wb, run, sheet):
    """기본 값 조서에 더하는 시트 — report.basic_workbook 의 sheet() 로 만든다."""
    md = run.raw['mode']
    sheet({'grant': '회차별 공정가치', 'mod': '조건 변경', 'cash': '부채 재측정'}[md], row_table(run))
    sheet('회차표 입력', input_rows_table(run))
    if md == 'mod':
        sheet('조건 변경표 입력', mod_input_table(run))
    sheet('블랙-숄즈 대조', bs_table(run))
    sheet('비용 배분', schedule_table(run))


def lattice_sheet(wb, run, row, name, title=None):
    """한 회차의 격자 — 위: 시점별 금리·확률, 가운데: 옵션 1주당 가치(행사 노드 색칠), 아래: 주가."""
    import datetime as dt
    t = run.terms
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    n = row['n']
    ws.cell(1, 2, f"{title or row['name']} — 이항격자 (옵션 1주당 가치와 주가, 원)").font = Font(bold=True, size=12, color='17365D')
    params = [('행사가격(원)', float(row.get('K') or t.K0)), ('가득일', row['vest']), ('행사 시작일', row['start']), ('행사 종료일', row['end']), ('구간 수', n),
              ('구간 길이(년)', row['dt']), ('상승 배수 u', row['u']), ('하락 배수 d', row['d']),
              ('행사 가능 시점', row['ex_step']),
              ('구간 퇴사 확률', 1.0 - (1.0 - float(t.so_exit))**row['dt'] if t.so_exit else 0.0),
              ('조기행사 가정', SO.EX_LABELS[int(t.so_ex)]), ('1주당 공정가치', row['fv'])]
    for k, (a, b) in enumerate(params, start=3):
        ws.cell(k, 2, a)
        ws.cell(k, 3, b)
    top = 3 + len(params) + 1
    heads = ['시점', '격자일', '선도 무위험 금리(연속)', '상승 확률', '할인계수', '행사 가능']
    db = dt.date.fromisoformat(t.d_base)
    for k, h in enumerate(heads):
        c = ws.cell(top + k, 2, h)
        c.font, c.fill = HEAD, NAVY
    for i in range(n + 1):
        col = 3 + i
        ws.cell(top, col, i)
        ws.cell(top + 1, col, (db + dt.timedelta(days=round(i*row['dt']*365))).isoformat())
        if i < n:
            ws.cell(top + 2, col, row['fwd'][i]).number_format = '0.000000%'
            ws.cell(top + 3, col, row['p'][i]).number_format = '0.00000000'
            ws.cell(top + 4, col, math.exp(-row['fwd'][i]*row['dt'])).number_format = '0.00000000'
        ws.cell(top + 5, col, '가능' if i >= row['ex_step'] else '불가')
    blocks = [('옵션 1주당 가치 (색칠한 칸 = 그 노드에서 행사)', row['values'], True),
              ('주가 (자리 0이 가장 높은 주가)', row['prices'], False)]
    r0 = top + len(heads) + 1
    for title, grid, mark in blocks:
        c = ws.cell(r0, 2, title)
        c.font = Font(bold=True, color='17365D')
        for j in range(n + 1):
            ws.cell(r0 + 1 + j, 2, f'자리 {j}')
        for i in range(n + 1):
            col = 3 + i
            values, go = grid[i], row['exercise'][i]
            for j in range(i + 1):
                c = ws.cell(r0 + 1 + j, col, values[j])
                c.number_format = '#,##0.00'
                if mark and go[j]:
                    c.fill = GO
        r0 += n + 3
    ws.column_dimensions['A'].width = 4
    ws.column_dimensions['B'].width = 30
    for i in range(n + 1):
        ws.column_dimensions[get_column_letter(3 + i)].width = 12
    ws.freeze_panes = ws.cell(top + 1, 3)
    return ws


def _sheet_name(base, used):
    name = re.sub(r'[\[\]:*?/\\]', '_', base)[:28]
    out, k = name, 2
    while out in used:
        out = f'{name[:25]}_{k}'
        k += 1
    used.add(out)
    return out


def detail_workbook(run, *, accounting=False):
    """상세 값 조서 — 평가 당시의 값으로 고정된다."""
    from .report import basic_workbook
    wb = basic_workbook(run, as_workbook=True)
    used = set(wb.sheetnames)
    for name, row in lattices(run).items():
        lattice_sheet(wb, run, row, _sheet_name(f"격자 {name}", used), title=name)
    if accounting:
        append_accounting(wb, run)
    return wb


def append_accounting(wb, run):
    if '회계처리' in wb.sheetnames:
        del wb['회계처리']
    _table(wb, '회계처리', journal_rows(run), width=30)
