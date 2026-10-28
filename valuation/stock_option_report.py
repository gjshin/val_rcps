"""주식선택권 조서 — 이미 평가한 실행 결과(run)로만 만든다. 새로 계산하지 않는다.

기본 값 조서는 report.basic_workbook 이 공통 시트를 만들고 여기의 시트를 더한다.
상세 값 조서는 회차마다 이항격자 전체(옵션 1주당 가치·주가·행사 노드)를 싣는다.
조건 변경이면 회차마다 변경 전·후 격자를, 현금결제형이면 결산일 격자를 싣는다.
상세 수식 조서는 상세 값 조서와 같은 시트·같은 칸에 엔진과 같은 순서의 수식을 넣는다.
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
    's0_splits', 'so_ex', 'so_mult', 'so_exit', 'grid_days', 'gap_m', 'so_mod', 'so_prev_liab', 'so_paid',
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
EXPENSE_LABEL = '당기 비용(원) = 당기말 부채 − 전기말 부채 + 당기 결제액'


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
        rows += [['전기말 주식보상부채(원)', raw['prev_liab']], ['당기 현금 결제액(원)', raw['paid']],
                 [EXPENSE_LABEL, raw['expense']]]
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
        # 결제액이 0이어도 줄을 둔다 — 수식 조서에서 결제액을 넣으면 이 줄이 받는다
        rows.append(['당기 중', '주식보상부채', raw['paid'], '현금', raw['paid']])
        rows += [[], ['전기말 주식보상부채', raw['prev_liab']], ['당기 현금 결제액', raw['paid']], ['당기말 주식보상부채', raw['liab']],
                 ['재측정', '결제될 때까지 매 결산일과 결제일에 공정가치로 다시 재고, 변동액을 당기손익으로 인식합니다 (문단 30).'],
                 ['결제 시', '(차) 주식보상부채 (대) 현금 — 행사일의 주가 − 행사가격 차액']]
        return rows
    by_end = {}
    for r in run.summary['so_schedule']:
        by_end[r['결산일']] = by_end.get(r['결산일'], 0.0) + r['당기 비용']
    rows = [[DRAFT_NOTE], ['결산일', '차변 계정', '차변 금액(원)', '대변 계정', '대변 금액(원)']]
    # 비용이 0인 결산일도 줄을 둔다 — 수식 조서에서 입력을 바꿔 비용이 생기면 이 줄이 받는다
    rows += [[d, '주식보상비용', v, '주식선택권 (자본조정)', v] for d, v in by_end.items()]
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
    sheet(ROWS_SHEET[md], row_table(run))
    sheet('회차표 입력', input_rows_table(run))
    if md == 'mod':
        sheet('조건 변경표 입력', mod_input_table(run))
    sheet('블랙-숄즈 대조', bs_table(run))
    sheet('비용 배분', schedule_table(run))


ROWS_SHEET = {'grant': '회차별 공정가치', 'mod': '조건 변경', 'cash': '부채 재측정'}
INPUT_SHEET = '평가 입력값'
# 평가 입력값 시트의 칸 — 수식 조서의 격자·표가 이 칸을 가리킨다
IN_ROW = {'S0': 2, 'sig': 3, 'div_y': 4, 'so_mult': 5, 'so_exit': 6, 'so_prev_liab': 7, 'so_paid': 8}
LATTICE_PARAMS = ('행사가격(원)', '가득일', '행사 시작일', '행사 종료일', '구간 수', '구간 길이(년)', '상승 배수 u',
                  '하락 배수 d', '행사 가능 시점', '구간 퇴사 확률', '조기행사 가정', '1주당 공정가치')
LATTICE_TIME = ('시점', '격자일', '선도 무위험 금리(연속)', '상승 확률', '할인계수', '행사 가능')


def lattice_layout(n):
    """격자 시트의 자리 — 값 조서와 수식 조서가 같은 자리를 쓴다."""
    param = {k: 3 + i for i, k in enumerate(LATTICE_PARAMS)}
    top = 3 + len(LATTICE_PARAMS) + 1
    time = {k: top + i for i, k in enumerate(LATTICE_TIME)}
    val = top + len(LATTICE_TIME) + 2          # 옵션 가치 첫 줄 (자리 0)
    px = val + n + 3                           # 주가 첫 줄 (자리 0)
    return dict(param=param, time=time, top=top, val=val, px=px)


def input_table(run):
    """평가 입력값 — 수식 조서에서 고칠 수 있는 시장자료·가정 (값 조서는 같은 표를 값으로 싣는다)."""
    t = run.terms
    rows = [['항목', '값', '설명'],
            ['측정기준일 주가(원)', float(t.S0), '격자의 출발 주가 · 블랙-숄즈 대조의 주가'],
            ['변동성 σ (연)', float(t.sig), '상승 배수 u = e^(σ × √구간 길이), 하락 배수 d = 1 ÷ u'],
            ['배당수익률 (연, 연속복리)', float(t.div_y), '상승 확률 = (e^((선도 금리 − 배당수익률) × 구간 길이) − d) ÷ (u − d)'],
            ['행사 배수 (주가 ÷ 행사가격)', float(t.so_mult), '«행사 배수에 도달하면 행사» 가정에서만 씁니다'],
            ['가득 뒤 연간 퇴사율', float(t.so_exit), '구간 퇴사 확률 = 1 − (1 − 퇴사율)^구간 길이']]
    if run.raw['mode'] == 'cash':
        rows += [['전기말 주식보상부채(원, -1은 없음)', float(t.so_prev_liab), '당기 비용 = 당기말 부채 − 전기말 부채(없으면 0) + 당기 결제액'],
                 ['당기 현금 결제액(원)', float(t.so_paid), '당기 중 행사되어 현금으로 지급한 금액']]
    return rows


def lattice_sheet(wb, run, row, name, title=None, formula=False):
    """한 회차의 격자 — 위: 시점별 금리·확률, 가운데: 옵션 1주당 가치(행사 노드 색칠), 아래: 주가.

    수식 조서는 같은 자리에 값 대신 수식을 넣는다 (formula_lattice). 행사 노드 색은 조건부 서식이 정한다.
    """
    import datetime as dt
    t = run.terms
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    n = row['n']
    pos = lattice_layout(n)
    ws.cell(1, 2, f"{title or row['name']} — 이항격자 (옵션 1주당 가치와 주가, 원)").font = Font(bold=True, size=12, color='17365D')
    values = dict(zip(LATTICE_PARAMS, (
        float(row.get('K') or t.K0), row['vest'], row['start'], row['end'], n, row['dt'], row['u'], row['d'],
        row['ex_step'], 1.0 - (1.0 - float(t.so_exit))**row['dt'] if t.so_exit else 0.0,
        SO.EX_LABELS[int(t.so_ex)], row['fv'])))
    for a, r in pos['param'].items():
        ws.cell(r, 2, a)
        ws.cell(r, 3, values[a])
    tr = pos['time']
    db = dt.date.fromisoformat(t.d_base)
    for h, r in tr.items():
        c = ws.cell(r, 2, h)
        c.font, c.fill = HEAD, NAVY
    for i in range(n + 1):
        col = 3 + i
        ws.cell(tr['시점'], col, i)
        ws.cell(tr['격자일'], col, (db + dt.timedelta(days=round(i*row['dt']*365))).isoformat())
        if i < n:
            ws.cell(tr['선도 무위험 금리(연속)'], col, row['fwd'][i]).number_format = '0.000000%'
            ws.cell(tr['상승 확률'], col, row['p'][i]).number_format = '0.00000000'
            ws.cell(tr['할인계수'], col, math.exp(-row['fwd'][i]*row['dt'])).number_format = '0.00000000'
        ws.cell(tr['행사 가능'], col, '가능' if i >= row['ex_step'] else '불가')
    blocks = [('옵션 1주당 가치 (색칠한 칸 = 그 노드에서 행사)', row['values'], pos['val'], True),
              ('주가 (자리 0이 가장 높은 주가)', row['prices'], pos['px'], False)]
    for head, grid, r0, mark in blocks:
        c = ws.cell(r0 - 1, 2, head)
        c.font = Font(bold=True, color='17365D')
        for j in range(n + 1):
            ws.cell(r0 + j, 2, f'자리 {j}')
        for i in range(n + 1):
            col = 3 + i
            vals, go = grid[i], row['exercise'][i]
            for j in range(i + 1):
                c = ws.cell(r0 + j, col, vals[j])
                c.number_format = '#,##0.00'
                if mark and go[j] and not formula:
                    c.fill = GO
    ws.column_dimensions['A'].width = 4
    ws.column_dimensions['B'].width = 30
    for i in range(n + 1):
        ws.column_dimensions[get_column_letter(3 + i)].width = 12
    ws.freeze_panes = ws.cell(tr['격자일'], 3)
    if formula:
        formula_lattice(ws, run, row, pos)
    return ws


def _sheet_name(base, used):
    name = re.sub(r'[\[\]:*?/\\]', '_', base)[:28]
    out, k = name, 2
    while out in used:
        out = f'{name[:25]}_{k}'
        k += 1
    used.add(out)
    return out


def detail_workbook(run, *, accounting=False, formula=False):
    """상세 조서 — 값 조서는 평가 당시의 값으로 고정되고, 수식 조서는 같은 시트·같은 칸에 수식이 들어간다.

    수식 조서를 다시 계산하면 값 조서와 같은 값이 나와야 한다 (tests/test_stock_option.py 가 리브레오피스로 대조).
    """
    from .report import basic_workbook
    wb = basic_workbook(run, as_workbook=True)
    ws = _table(wb, INPUT_SHEET, input_table(run), width=30)
    ws.column_dimensions['C'].width = 70
    wb.move_sheet(ws, offset=1 - wb.sheetnames.index(ws.title))      # 평가요약 바로 뒤
    used = set(wb.sheetnames)
    names = {}
    for name, row in lattices(run).items():
        names[name] = lattice_sheet(wb, run, row, _sheet_name(f"격자 {name}", used), title=name, formula=formula).title
    if accounting:
        append_accounting(wb, run)
    if formula:
        formula_tables(wb, run, names)
    wb.so_key_cells = key_cells(wb, run, names)
    return wb


def append_accounting(wb, run):
    if '회계처리' in wb.sheetnames:
        del wb['회계처리']
    _table(wb, '회계처리', journal_rows(run), width=30)


# ── 수식 조서 ───────────────────────────────────────────────────────────────
# 값 조서와 같은 칸에 엔진과 같은 순서의 계산식을 넣는다. 금리(선도 무위험 금리)·날짜·구간 수·행사 가능 시점은
# 앱이 정한 값으로 둔다 — 바꾸려면 앱에서 다시 평가한다. 노란 칸(평가 입력값·가득 예상 비율·부여일 공정가치)만 고친다.

def _q(name):
    return "'" + name.replace("'", "''") + "'"


def _in(key):
    return f"{_q(INPUT_SHEET)}!$B${IN_ROW[key]}"


def formula_lattice(ws, run, row, pos):
    from openpyxl.formatting.rule import FormulaRule
    t, n = run.terms, row['n']
    P, tr, v0, p0 = pos['param'], pos['time'], pos['val'], pos['px']
    K, DT, U, D, E = (f"$C${P[k]}" for k in ('행사가격(원)', '구간 길이(년)', '상승 배수 u', '하락 배수 d', '구간 퇴사 확률'))
    S0, SIG, Q, MULT, EXIT = (_in(k) for k in ('S0', 'sig', 'div_y', 'so_mult', 'so_exit'))
    ws.cell(P['상승 배수 u'], 3, f"=EXP({SIG}*SQRT({DT}))")
    ws.cell(P['하락 배수 d'], 3, f"=1/{U}")
    ws.cell(P['구간 퇴사 확률'], 3, f"=1-(1-{EXIT})^{DT}")
    ws.cell(P['1주당 공정가치'], 3, f"=C{v0}")
    fr, pr, dr = tr['선도 무위험 금리(연속)'], tr['상승 확률'], tr['할인계수']
    mode, ex_step = int(t.so_ex), int(row['ex_step'])
    L = get_column_letter
    for i in range(n + 1):
        c = L(3 + i)
        if i < n:
            ws.cell(pr, 3 + i, f"=(EXP(({c}{fr}-{Q})*{DT})-{D})/({U}-{D})")
            ws.cell(dr, 3 + i, f"=EXP(-{c}{fr}*{DT})")
        for j in range(i + 1):
            ws.cell(p0 + j, 3 + i, f"={S0}*{U}^({i - 2*j})")
            S = f"{c}{p0 + j}"
            if i == n:
                f = f"=MAX({S}-{K},0)"
            else:
                c1 = L(4 + i)
                cont = f"{c}${dr}*({c}${pr}*{c1}{v0 + j}+(1-{c}${pr})*{c1}{v0 + j + 1})"
                if i < ex_step:
                    f = f"={cont}"
                else:
                    H = f"((1-{E})*{cont}+{E}*MAX({S}-{K},0))"
                    if mode == 1:
                        f = f"=IF({S}-{K}>{H}+1E-12*MAX(1,ABS({H})),{S}-{K},{H})"
                    elif mode == 2:
                        f = f"=IF(AND({S}>={MULT}*{K}-1E-12*{K},{S}-{K}>0),{S}-{K},{H})"
                    else:
                        f = f"={H}"
            ws.cell(v0 + j, 3 + i, f)
    # 행사 노드 색 — 행사할 수 있는 시점에서 옵션 가치가 지금 행사한 값(주가 − 행사가격 > 0)과 같은 칸
    last = L(3 + n)
    rule = FormulaRule(formula=[f"AND(C${tr['행사 가능']}=\"가능\",C{p0}-{K}>0,"
                                f"ABS(C{v0}-(C{p0}-{K}))<=1E-9*MAX(1,ABS(C{v0})))"], fill=GO)
    ws.conditional_formatting.add(f"C{v0}:{last}{v0 + n}", rule)


def _rows_cols(wb, run):
    ws = wb[ROWS_SHEET[run.raw['mode']]]
    cols = {ws.cell(1, c).value: get_column_letter(c) for c in range(1, ws.max_column + 1)}
    return ws, cols


def formula_tables(wb, run, names):
    """회차표·블랙-숄즈 대조·비용 배분·평가요약·회계처리를 격자와 평가 입력값에 잇는다."""
    from .legacy import INPUT_FILL
    yellow = PatternFill('solid', fgColor=INPUT_FILL)
    t, raw, md = run.terms, run.raw, run.raw['mode']
    S0, SIG, Q = (_in(k) for k in ('S0', 'sig', 'div_y'))
    ins = wb[INPUT_SHEET]
    for r in range(2, ins.max_row + 1):
        ins.cell(r, 2).fill = yellow
    fvrow = lattice_layout(1)['param']['1주당 공정가치']

    def root(name):
        return f"{_q(names[name])}!$C${fvrow}"

    def strike(name):
        return f"{_q(names[name])}!$C${lattice_layout(1)['param']['행사가격(원)']}"

    # 회차표 입력 — 가득 예상 비율(F)은 평가자 추정이라 노란 칸
    inp = wb['회차표 입력']
    keep = {}
    for k, r in enumerate(raw['rows'], start=2):
        inp.cell(k, 6).fill = yellow
        keep[r['name']] = f"{_q('회차표 입력')}!$F${k}"
    gfv = {}
    if md == 'mod':
        mws = wb['조건 변경표 입력']
        for k in range(2, mws.max_row + 1):
            mws.cell(k, 7).fill = yellow
            gfv[str(mws.cell(k, 1).value).strip()] = f"{_q('조건 변경표 입력')}!$G${k}"
    # 블랙-숄즈 대조 — 행마다 (격자 이름, 만기 구분)
    bs = wb['블랙-숄즈 대조']
    bsrow = {}
    for r in range(2, bs.max_row + 1):
        name, kind = bs.cell(r, 1).value, bs.cell(r, 2).value
        bsrow[name, kind] = r
        K = strike(name)
        if bs.cell(r, 5).value is None:          # 남은 기간이 없다 — 내재가치
            bs.cell(r, 9, f"=MAX({S0}-{K},0)")
        else:
            bs.cell(r, 5, f"=(LN({S0}/{K})+(D{r}-{Q}+0.5*{SIG}*{SIG})*C{r})/({SIG}*SQRT(C{r}))")
            bs.cell(r, 6, f"=E{r}-{SIG}*SQRT(C{r})")
            bs.cell(r, 7, f"=NORMSDIST(E{r})")
            bs.cell(r, 8, f"=NORMSDIST(F{r})")
            bs.cell(r, 9, f"={S0}*EXP(-{Q}*C{r})*G{r}-{K}*EXP(-D{r}*C{r})*H{r}")
        bs.cell(r, 10, f"={root(name)}")
        bs.cell(r, 11, f"=J{r}-I{r}")
    # 회차표 (평가 종류별 시트)
    ws, cols = _rows_cols(wb, run)
    m = len(raw['rows'])
    at = {}
    for k, r in enumerate(raw['rows'], start=2):
        name = r['name']
        cell = lambda h, k=k: f"{cols[h]}{k}"
        at[name] = cell
        ws[cell('가득 예상 비율')] = f"={keep[name]}"
        if md == 'mod':
            ws[cell('부여일 1주당 공정가치')] = f"={gfv[name]}"
            ws[cell('변경 전 1주당 (변경일)')] = f"={root(name + ' 변경 전')}"
            ws[cell('변경 후 1주당 (변경일)')] = f"={root(name + ' 변경 후')}"
            ws[cell('1주당 증분')] = f"=MAX({cell('변경 후 1주당 (변경일)')}-{cell('변경 전 1주당 (변경일)')},0)"
            ws[cell('당초 보상원가')] = f"={cell('부여일 1주당 공정가치')}*{cell('변경 전 수량')}*{cell('가득 예상 비율')}"
            ws[cell('증분 공정가치')] = (f"={cell('1주당 증분')}*{cell('변경 전 수량')}+"
                                     f"{cell('변경 후 1주당 (변경일)')}*{cell('늘어난 수량')}")
            ws[cell('증분 보상원가')] = f"={cell('증분 공정가치')}*{cell('가득 예상 비율')}"
            continue
        ws[cell('1주당 공정가치')] = f"={root(name)}"
        ws[cell('블랙-숄즈(행사 종료일)')] = f"={_q('블랙-숄즈 대조')}!$I${bsrow[name, '행사 종료일']}"
        ws[cell('공정가치')] = f"={cell('1주당 공정가치')}*{cell('부여 수량')}"
        if md == 'cash':
            ws[cell('가득 예상 수량의 공정가치')] = f"={cell('1주당 공정가치')}*{cell('부여 수량')}*{cell('가득 예상 비율')}"
            ws[cell('주식보상부채')] = f"={cell('가득 예상 수량의 공정가치')}*{cell('근무 경과율')}"
        else:
            ws[cell('보상원가')] = f"={cell('1주당 공정가치')}*{cell('부여 수량')}*{cell('가득 예상 비율')}"
    total = {}
    for h in SUM_COLS[md]:
        ws[f"{cols[h]}{m + 2}"] = f"=SUM({cols[h]}2:{cols[h]}{m + 1})"
        total[h] = f"{_q(ws.title)}!${cols[h]}${m + 2}"
    # 비용 배분 — 누적 = 총액 × 누적 배분율, 당기 = 누적 − 직전 누적 (회차·구분마다)
    sc = wb['비용 배분']
    sh = {sc.cell(1, c).value: get_column_letter(c) for c in range(1, sc.max_column + 1)}
    prev, by_end = {}, {}
    for r, x in enumerate(raw['schedule'], start=2):
        rr = f"{_q(ws.title)}!"
        if md == 'cash':
            sc[f"{sh['주식보상부채']}{r}"] = f"={rr}{at[x['name']]('주식보상부채')}"
            continue
        src = {'grant': '보상원가', 'mod': {'당초': '당초 보상원가', '증분': '증분 보상원가'}.get(x.get('part'))}[md]
        sc[f"{sh['누적 비용']}{r}"] = f"={rr}{at[x['name']](src)}*{sh['누적 배분율']}{r}"
        key = (x['name'], x.get('part'))
        sc[f"{sh['당기 비용']}{r}"] = (f"={sh['누적 비용']}{r}-{sh['누적 비용']}{prev[key]}" if key in prev
                                     else f"={sh['누적 비용']}{r}")
        prev[key] = r
        by_end.setdefault(x['fy_end'], []).append(f"{_q('비용 배분')}!{sh['당기 비용']}{r}")
    # 평가요약
    sm = wb['평가요약']
    lab = {sm.cell(r, 1).value: r for r in range(1, sm.max_row + 1)}
    from .service import AMOUNT_LABELS
    sources = {'grant': ('공정가치', '보상원가'), 'mod': ('증분 공정가치', '증분 보상원가'),
               'cash': ('공정가치', '주식보상부채')}[md]
    qty = float(raw['qty'])
    for key, h in zip(SO.AMOUNT_KEYS[md], sources):
        r = lab[AMOUNT_LABELS[key]]
        sm.cell(r, 2, f"={total[h]}")
        sm.cell(r, 3, f"=B{r}/{qty!r}" if qty else "=0")      # 현금결제형에서 모두 결제했으면 남은 수량 0
        sm.cell(r, 4, f"=C{r}/{S0}*100")
    sm.cell(lab['측정기준일 주가(원)'], 2, f"={S0}")
    sm.cell(lab['가득 뒤 연간 퇴사율'], 2, f"={_in('so_exit')}")
    if md == 'mod':
        sm.cell(lab['당초 보상원가(원) — 부여일 공정가치 기준'], 2, f"={total['당초 보상원가']}")
    if md == 'cash':
        rp, rq, re_ = lab['전기말 주식보상부채(원)'], lab['당기 현금 결제액(원)'], lab[EXPENSE_LABEL]
        sm.cell(rp, 2, f"=MAX(0,{_in('so_prev_liab')})")
        sm.cell(rq, 2, f"={_in('so_paid')}")
        sm.cell(re_, 2, f"={total['주식보상부채']}-B{rp}+B{rq}")
    # 회계처리
    if '회계처리' not in wb.sheetnames:
        return
    jw = wb['회계처리']
    if md == 'cash':
        v = f"{_q('평가요약')}!$B${lab[EXPENSE_LABEL]}"
        jw.cell(3, 2, f'=IF({v}>=0,"주식보상비용","주식보상부채")')
        jw.cell(3, 3, f"=ABS({v})")
        jw.cell(3, 4, f'=IF({v}>=0,"주식보상부채","주식보상비용 (환입)")')
        jw.cell(3, 5, f"=ABS({v})")
        at = {jw.cell(r, 1).value: r for r in range(4, jw.max_row + 1)}
        paid = f"{_q('평가요약')}!$B${lab['당기 현금 결제액(원)']}"
        jw.cell(at['당기 중'], 3, f"={paid}")
        jw.cell(at['당기 중'], 5, f"={paid}")
        jw.cell(at['전기말 주식보상부채'], 2, f"={_q('평가요약')}!$B${lab['전기말 주식보상부채(원)']}")
        jw.cell(at['당기 현금 결제액'], 2, f"={paid}")
        jw.cell(at['당기말 주식보상부채'], 2, f"={total['주식보상부채']}")
        return
    for r in range(3, jw.max_row + 1):
        d = jw.cell(r, 1).value
        if jw.cell(r, 2).value == '주식보상비용' and d in by_end:
            jw.cell(r, 3, '=' + '+'.join(by_end[d]))
            jw.cell(r, 5, f"=C{r}")


def key_cells(wb, run, names):
    """다시 계산했을 때 앱과 같아야 하는 칸 — [(항목, 시트, 칸, 앱 값)]. 조서의 «계산대사» 시트와 시험이 쓴다."""
    from .service import AMOUNT_LABELS
    raw, s = run.raw, run.summary
    sm = wb['평가요약']
    lab = {sm.cell(r, 1).value: r for r in range(1, sm.max_row + 1)}
    out = []
    for key in SO.AMOUNT_KEYS[raw['mode']]:
        r = lab[AMOUNT_LABELS[key]]
        out += [(AMOUNT_LABELS[key] + ' (원)', '평가요약', f'B{r}', s['amounts_total'][key]),
                (AMOUNT_LABELS[key] + ' (주가 100 기준)', '평가요약', f'D{r}', s['amounts_100'][key])]
    fvrow = lattice_layout(1)['param']['1주당 공정가치']
    for name, row in lattices(run).items():
        out.append((f'{name} 1주당 공정가치', names[name], f'C{fvrow}', row['fv']))
    if raw['mode'] == 'mod':
        out.append(('당초 보상원가 (원)', '평가요약', f"B{lab['당초 보상원가(원) — 부여일 공정가치 기준']}", raw['grant_cost']))
    if raw['mode'] == 'cash':
        out.append(('당기 비용 (원)', '평가요약', f"B{lab[EXPENSE_LABEL]}", raw['expense']))
    return out
