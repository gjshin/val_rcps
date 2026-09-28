"""Bounded arithmetic and contract-review prompts; no automatic accounting opinion."""
import math
from . import legacy
from .case import Issue


def review_issues(case, tm, raw):
    out = []
    def add(code, field, fact, impact, action):
        out.append(Issue('review', code, field, fact, impact, action))
    add('judgement_scope', 'model', '계약 해석·회계분류·모형 적합성은 자동 검증하지 않습니다.',
        '산술 검산 통과가 평가 결론의 적정성을 의미하지 않습니다.', '계약조건 및 선택한 모형의 적용 근거를 별도로 검토하십시오.')
    market_date = case.sources.get('market_date')
    if not market_date or market_date != tm.d_base:
        add('market_date', 'market_date', f"시장자료 기준일: {market_date or '미기록'} / 평가기준일: {tm.d_base}",
            '전기 또는 다른 시점의 자료가 남아 있을 수 있습니다.', '주당가치·변동성·금리의 기준일을 확인하고, 시점 차이가 있으면 근거를 기록하십시오.')
    if tm.s0_date and tm.s0_date != tm.d_base:
        add('price_date', 's0_date', f'사용 주가 거래일은 {tm.s0_date}입니다.', '평가기준일과 다릅니다.', '휴장 등 직전 거래일 사용 사유를 확인하십시오.')
    for field in ['rf_curve', 'cr_curve']:
        curve = getattr(tm, field)
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
        add('unmodeled', 'unmod_note', tm.unmod_note, '기재된 권리가 계산에 반영되지 않았을 수 있습니다.', '별도 계약조건에서 처리방식·영향·근거를 기록하십시오.')
    if tm.dil_shares > 0:
        add('dilution', 'dil_shares', f'전환 증가 주식수 {tm.dil_shares:,.0f}주가 기록되어 있습니다.',
            '이 주식수만으로 전환에 따른 자본구조·희석을 재계산하지 않습니다.', '기초자산 가치와 전환가액에 반영된 희석 범위를 대사하십시오.')
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


def arithmetic_checks(tm, raw, amounts):
    full = raw if legacy.is_sha(tm) else raw['full']
    checks = [dict(name='유한한 평가금액', passed=all(math.isfinite(v) for v in amounts.values()),
                   detail='NaN·무한대가 없는지 확인'),
              dict(name='위험중립확률 범위', passed=not full.get('qbad'),
                   detail=f"최소 {full['qmin']:.8f}, 최대 {full['qmax']:.8f}; 0 < 확률 < 1")]
    if not legacy.is_sha(tm):
        delta = amounts['net'] - (amounts['host_reference'] + amounts['put_increment'] +
                                  amounts['conversion_increment'] - amounts['call_deduction'])
        checks.append(dict(name='구성요소 합계 대사', passed=abs(delta) < 1e-8,
                           detail=f'차이 {delta:.10f} (원금 100 기준). 동일 실행 결과의 산술 대사이며 독립 모형 검증은 아님.'))
    return checks
