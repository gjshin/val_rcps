"""Bounded arithmetic and contract-review prompts; no automatic accounting opinion."""
import copy
import math
from . import legacy
from .case import Issue


def review_issues(case, tm, raw):
    out = []
    def add(code, field, fact, impact, action):
        out.append(Issue('review', code, field, fact, impact, action))
    market_date = case.sources.get('market_date')
    if not market_date or market_date != tm.d_base:
        add('market_date', 'market_date', f"시장자료 기준일: {market_date or '미기록'} / 평가기준일: {tm.d_base}",
            '전기 또는 다른 시점의 자료가 남아 있을 수 있습니다.', '주당가치·변동성·금리의 기준일을 확인하고, 시점 차이가 있으면 근거를 기록하십시오.')
    if tm.s0_date and tm.s0_date != tm.d_base:
        add('price_date', 's0_date', f'사용 주가 거래일은 {tm.s0_date}입니다.', '평가기준일과 다릅니다.', '휴장 등 직전 거래일 사용 사유를 확인하십시오.')
    for field in (['rf_curve'] if legacy.is_so(tm) else ['rf_curve', 'cr_curve']):
        curve = getattr(tm, field)
        example = legacy.EXAMPLE_RF if field == 'rf_curve' else legacy.EXAMPLE_CR
        if legacy.curve_is_example(curve, example):
            add('example_curve', field, '입력 금리가 앱의 예시 곡선과 같습니다.',
                '예시값이 적용됐을 수 있습니다.', '평가기준일 금리자료와 대조하십시오.')
        if curve and tm.T > curve[-1][0]:
            add('curve_coverage', field, f'입력 곡선 최장만기 {curve[-1][0]:g}년보다 잔존기간 {tm.T:.3f}년이 깁니다.',
                '입력 자료 바깥 구간의 금리를 연장 적용합니다.', '장기 금리자료를 확보하거나 곡선 연장 적용의 근거를 기록하십시오.')
    if tm.rfx_mode and tm.carry:
        add('refixing_approximation', 'carry', '전환가액 경로를 근사하는 방법을 적용했습니다.',
            '상태별 경로를 모두 보존한 값과 차이가 날 수 있습니다.', '평가방법 선택 근거와 필요한 비교분석을 기록하십시오.')
    if tm.put_bdt:
        add('bdt_scope', 'put_bdt', '상환청구권 포함 부채요소에 BDT 금리격자를 적용했습니다.',
            '전체가치는 주가 격자에서 계산하므로 구성요소의 차이에 두 모형이 함께 영향을 줍니다.',
            '금리곡선·금리 변동성 근거와 분해 목적을 검토하십시오. 수치 임계값으로 적용 적합성을 판정하지 않습니다.')
    if tm.ipo_on:
        add('ipo_assumption', 'ipo_m', '입력한 IPO 시점·가격을 시나리오로 적용했습니다.',
            '상장 발생확률을 직접 추정하는 모형은 아닙니다.', '시점·가격 가정의 근거 및 미상장 시나리오를 검토하십시오.')
    if tm.unmod_note:
        add('unmodeled', 'unmod_note', tm.unmod_note, '기재된 권리가 계산에 반영되지 않았을 수 있습니다.', '기본 계산 결과를 출력한 뒤 해당 조건의 영향을 별도로 검토하십시오.')
    if tm.dil_shares > 0:
        ratio = f' / 기존 보통주 {tm.base_shares:,.0f}주 = {tm.dil_shares/tm.base_shares:.2%} 증가' if tm.base_shares > 0 else ''
        add('dilution', 'dil_shares', f"{'행사' if legacy.is_so(tm) else '전환'} 증가 주식수 {tm.dil_shares:,.0f}주{ratio}.",
            f"이 주식수만으로 {'행사' if legacy.is_so(tm) else '전환'}에 따른 자본구조·희석을 재계산하지 않습니다.",
            f"기초자산 가치와 {'행사가격' if legacy.is_so(tm) else '전환가액'}에 반영된 희석 범위를 대사하십시오.")
    pack = case.market_evidence.get('sig')
    if pack and pack.get('listed') and pack['series']:
        name, prices = pack['series'][0]
        last = prices[-1][1]
        ratio = tm.S0/last
        if abs(ratio-1) > .05:
            add('price_basis', 'S0', f'입력 주가 {tm.S0:,.2f}원 / {name} 변동성 자료의 마지막 주가 {last:,.2f}원 = {ratio:.4f}배.',
                '주가와 수정주가의 기준이 다를 수 있습니다.', '거래일, 분할·병합·배당 조정 및 전환가액의 기준을 대조하십시오.')
    if tm.rfx_mode and tm.S0 < tm.K0*.8:
        add('low_price_refixing', 'rfx_mode', f'주가가 전환가액의 {tm.S0/tm.K0:.2%}이며 리픽싱이 켜져 있습니다.',
            '첫 조정일부터 전환가액이 하락할 수 있습니다.', '현재 전환가액과 다음 조정일, 조정 하한을 확인하십시오.')
    if legacy.is_so(tm):
        so_review(tm, raw, add)
        return out
    if legacy.is_sha(tm):
        return out
    full = raw['full']
    negative_spread = [i for i in range(tm.n) if full['fwdCR'](i) < full['fwdRF'](i) - 1e-10]
    if negative_spread:
        add('negative_spread', 'cr_curve', f'위험 선도금리가 무위험 선도금리보다 낮은 구간이 {len(negative_spread)}개입니다.',
            '해당 구간의 신용스프레드가 음수입니다.', '두 곡선의 대상·기준일·복리 기준 및 변환 결과를 대조하십시오.')
    if tm.rfx_mode and tm.floor > tm.K0:
        add('refixing_floor', 'floor', '최저 조정가액이 현재 전환가액보다 큽니다.',
            '조정 시 적용 하한에 영향을 줍니다.', '최초·현재 전환가액과 하한 조항을 대조하십시오.')
    if legacy.is_rcps(tm) and tm.div_mode == 1 and tm.cpn:
        add('dividend', 'div_mode', '입력 우선배당률을 재량배당으로 처리했습니다.',
            '부채 현금흐름에 해당 배당을 넣지 않았습니다.', '미지급 배당 가산·누적 여부를 계약서와 대조하십시오.')
    if legacy.is_bw(tm) and tm.bw_pay == 0 and tm.k_w:
        add('bw_call', 'k_w', '현금납입형 BW의 콜은 사채를 대상으로 계산합니다.',
            '신주인수권증권을 되사는 계약이면 평가대상이 다릅니다.', '콜 대상증권을 확인하고 다른 대상이면 별도 모형으로 평가하십시오.')
    if tm.k_w:
        if not full.get('has_call'):
            add('inactive_call', 'k_w', '입력한 콜이 계산 격자에서 행사되지 않습니다.',
                '콜 차감액이 0으로 계산됩니다.', '행사기간·금액표·평가기준일을 확인하십시오.')
        if tm.p_s <= tm.p_e and max(tm.p_s, tm.k_s) <= min(tm.p_e, tm.k_e):
            add('priority', 'pc_order', '풋·콜 행사기간이 겹칩니다.', '행사 우선순위에 따라 가치가 달라질 수 있습니다.',
                '계약의 통지·행사 우선순위를 확인하십시오.')
        add('lock', 'k_hold', '콜 대상 의무보유 설정을 계산에 적용했습니다.',
            '콜 대상물량의 전환·상환 가능 시점에 영향을 줍니다.', '설정값만으로 계약에 의무보유가 있다고 추정하지 않습니다. 실제 제한기간과 권리를 대조하십시오.')
    periods = [('p_f', tm.p_s <= tm.p_e), ('k_f', tm.k_w > 0), ('rfx_cyc', tm.rfx_mode > 0)]
    for field, active in periods:
        if active and getattr(tm, field) < tm.T * 12 / tm.n:
            add('grid_resolution', field, '행사·조정 주기가 계산 간격보다 짧습니다.',
                '행사·조정일을 충분히 구분하지 못할 수 있습니다.', '계산 간격을 줄여 결과를 비교하십시오.')
    for field, active in [('ytm', True), ('p_yield', tm.p_mode == 'accrue'), ('k_prem', tm.k_w > 0)]:
        if active and 0 < getattr(tm, field) < legacy.eff_cpn(tm):
            add('yield_coupon', field, '보장수익률·가산율이 적용 이자·배당률보다 낮습니다.',
                '기지급액 공제 및 상환할증금 하한 처리에 영향을 줍니다.', '계약상 산식과 회차별 지급금액을 확인하십시오.')
    return out


def so_review(tm, raw, add):
    """주식선택권 확인할 사항 — 기준서가 정한 판단을 평가자가 했는지 묻는다(값을 바꾸지 않는다)."""
    from .stock_option import EX_LABELS
    if tm.d_base != tm.d_issue:
        add('so_measure_date', 'd_base', f'측정기준일({tm.d_base})이 부여일({tm.d_issue})과 다릅니다.',
            '주식결제형은 부여일 현재 공정가치로 측정하고 다시 재지 않습니다 (기업회계기준서 제1102호 문단 11).',
            '조건 변경 등 다른 측정기준일을 쓴 근거를 기록하십시오.')
    if int(tm.so_ex) == 0 and any(r['t_end'] - r['t_ex'] > 1e-9 for r in raw['rows']):
        add('so_early_exercise', 'so_ex', f"조기행사 가정: {EX_LABELS[0]}.",
            '행사 시작일과 행사 종료일 사이에 행사할 수 있는데 조기행사를 반영하지 않았습니다 (문단 B5·B16~B19).',
            '행사 기간이 짧거나 조기행사 영향이 작다는 근거를 기록하거나 최적 행사·행사 배수 가정을 고르십시오.')
    if int(tm.so_ex) == 2:
        add('so_multiple', 'so_mult', f'행사 배수 {tm.so_mult:g}배 — 주가가 행사가격의 {tm.so_mult:g}배 이상이면 행사한다고 봤습니다.',
            '행사 배수는 과거 행사 실적이나 유사 기업 자료로 정하는 추정치입니다 (문단 B17~B19).',
            '배수를 정한 근거(과거 행사 자료·업계 자료)를 기록하십시오.')
    if all(r['keep'] >= 1 for r in raw['rows']):
        add('so_vesting', 'so_rows', '가득 예상 비율이 모든 회차에서 100%입니다.',
            '가득기간 중 퇴사로 가득되지 않을 수량을 보상원가에 반영하지 않았습니다 (문단 19·20).',
            '퇴사 예상이 없다는 근거를 기록하거나, 회차표의 가득 예상 비율을 고치십시오. 매 결산일에 다시 추정합니다.')
    if tm.div_y == 0:
        add('so_dividend', 'div_y', '배당수익률 0%로 평가했습니다.',
            '기대배당은 공정가치에 영향을 줍니다 (문단 B31~B36).', '배당 정책·과거 배당 실적을 확인하고 근거를 기록하십시오.')


def arithmetic_checks(tm, raw, amounts):
    if legacy.is_so(tm):
        from .stock_option import checks
        return checks(tm, raw)
    full = raw if legacy.is_sha(tm) else raw['full']
    checks = [dict(name='유한한 평가금액', passed=all(math.isfinite(v) for v in amounts.values()),
                   detail='숫자가 아닌 값·무한대가 없는지 확인'),
              dict(name='위험중립확률 범위', passed=not full.get('qbad'),
                   detail=f"최소 {full['qmin']:.8f}, 최대 {full['qmax']:.8f}; 0 < 확률 < 1")]
    if not legacy.is_sha(tm):
        delta = amounts['net'] - (amounts['host_reference'] + amounts['put_increment'] +
                                  amounts['conversion_increment'] - amounts['call_deduction'])
        checks.append(dict(name='구성요소 합계 대사', passed=abs(delta) < 1e-8,
                           detail=f'차이 {delta:.10f} (원금 100 기준). 동일 실행 결과의 산술 대사이며 독립 모형 검증은 아님.'))
    return checks


# 옛 상세 앱의 입력 경고(legacy.validate) 가운데 위 검사와 겹치는 것은 뺀다.
_DUPLICATE = ('함께 열리고', '신용스프레드가 음수', '주기가 노드 간격보다', '표면이자율', '최저 조정가액이 최초',
              '희석', '위험 곡선이 무위험 곡선보다')


def _split_sentence(text):
    """첫 문장만 '확인 내용'으로, 나머지는 '영향' 칸으로 — 표를 짧게 유지한다."""
    text = ' '.join(text.replace('**', '').split())
    for mark in ('다. ', '. '):
        cut = text.find(mark)
        if 0 < cut < len(text) - len(mark):
            return text[:cut + len(mark) - 1], text[cut + len(mark):]
    return text, ''


def input_warnings(tm, px_last=None):
    """옛 입력 경고를 확인할 사항으로 — 계산을 다시 돌지 않는 검사만(BDT 비교는 BDT 를 켰을 때만)."""
    out = []
    # 주주간계약은 사채 경고(전환기간·만기상환 등)가 뜻이 없다 — 주주간계약 경고만 싣는다.
    if legacy.is_so(tm):
        return out                       # 주식선택권 확인할 사항은 review_issues(so_review) 가 싣는다
    msgs = (legacy.sha_validate(copy.deepcopy(tm)) if legacy.is_sha(tm)
            else legacy.validate(copy.deepcopy(tm), px_last))
    for msg in msgs:
        if any(k in msg for k in _DUPLICATE):
            continue
        head, rest = _split_sentence(msg)
        out.append(Issue('review', 'input_check', 'input', head, rest, '계약서·입력값을 대조하고 필요하면 근거를 적으십시오.'))
    return out
