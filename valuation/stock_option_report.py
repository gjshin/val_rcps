"""주식선택권 조서 — 이미 평가한 실행 결과(run)로만 만든다. 새로 계산하지 않는다.

기본 값 조서는 report.basic_workbook 이 공통 시트를 만들고 여기의 시트를 더한다.
상세 값·수식 조서는 stock_option_workpaper 가 전환사채 상세 조서와 같은 서식으로 만들고, 여기의 표를 쓴다.
"""
import math
import re

from . import stock_option as SO

# 주식선택권 평가에 쓰는 입력 — 조서의 계약조건·시장자료·평가방법 시트에 이것만 싣는다.
INPUT_KEYS = frozenset({
    'inst', 'tranche', 'd_issue', 'd_base', 'd_mat', 'K0', 'so_settle', 'so_fy_end', 'base_shares', 'dil_shares',
    'unmod_note', 'S0', 'sig', 'div_y', 'y_type', 'cmp_rf', 'ticker', 's0_src', 's0_date', 's0_raw', 's0_adj',
    's0_splits', 'so_ex', 'so_mult', 'so_exit', 'grid_days', 'gap_m', 'so_mod', 'so_prev_liab', 'so_paid', 'so_mkt', 'so_nv',
})
# 합계 줄에 더할 칸 — 평가 종류마다
SUM_COLS = {'grant': ('부여 수량', '공정가치', '보상원가'),
            'mod': ('변경 전 수량', '늘어난 수량', '당초 보상원가', '증분 공정가치', '증분 보상원가'),
            'cash': ('부여 수량', '공정가치', '가득 예상 수량의 공정가치', '주식보상부채')}
MODE_TEXT = {'grant': '부여일 평가', 'mod': '조건 변경 평가 (증분 공정가치)', 'cash': '현금결제형 결산일 재측정'}
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
            ['시장조건 판정', SO.MKT_LABELS[int(t.so_mkt)]],
            ['비가득조건 연간 중단율 (가득일까지)', t.so_nv],
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
    heads = ['회차', '가득일 (비용 배분 종료)', '행사 시작일', '행사 종료일', '부여 수량(주)', '가득 예상 비율',
             '시장조건 목표 주가(원)', '목표 도달 (측정기준일 전)']
    return [heads] + [[r['name'], r['vest'], r['start'], r['end'], r['qty'], r['keep'], SO.target(r),
                       '예' if SO.reached(r) else ''] for r in SO.rows(run.terms)]


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


def _sheet_name(base, used):
    name = re.sub(r'[\[\]:*?/\\]', '_', base)[:28]
    out, k = name, 2
    while out in used:
        out = f'{name[:25]}_{k}'
        k += 1
    used.add(out)
    return out


def detail_workbook(run, *, accounting=False, formula=False):
    """상세 조서 — 전환사채·상환전환우선주 상세 조서와 같은 서식 (stock_option_workpaper)."""
    from .stock_option_workpaper import detail_workbook as build
    return build(run, accounting=accounting, formula=formula)


def append_accounting(wb, run):
    """기본 값 조서의 회계처리 — 전환사채 기본 조서와 같은 표 (report.calculation_table)."""
    from .report import calculation_table
    calculation_table(wb, '회계처리', journal_rows(run))
