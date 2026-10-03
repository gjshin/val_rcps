"""Human-facing field names and lossless display conversions; no pricing code."""
import datetime as dt
from .legacy import months_to_date, date_to_months

LABELS = {
    'inst': '상품 유형', 'd_issue': '실제 발행일', 'd_base': '평가기준일', 'd_mat': '계약상 만기일',
    'S0': '기초자산 주당가치(원)', 'K0': '현재 전환가액(원)', 'K_cap': '전환가액 상한(원, -1은 현재 전환가액)',
    'issue_px': '1주당 발행가(원)', 'par': '1주당 액면가(원)', 'face_total': '평가대상 발행금액·투자원금(원)',
    'sig': '주가 변동성(연, %)', 'cpn': '표면이자·우선배당률(연, %)', 'div_y': '보통주 배당수익률(연, %)',
    'mat_mode': '만기 처리', 'div_mode': '우선배당 처리', 'div_basis': '우선배당률 기준', 'issuer_call': 'RCPS 콜 권리',
    'cv_s': '전환·신주인수권 행사 시작일', 'cv_e': '전환·신주인수권 행사 종료일',
    'p_s': '투자자 상환청구 시작일', 'p_e': '투자자 상환청구 종료일', 'p_f': '상환청구 주기(개월)',
    'p_mode': '상환청구금액 산정', 'p_rate': '상환청구금액(원금 대비 %)', 'p_yield': '상환청구 보장수익률(연, %)',
    'p_cmp': '상환청구 수익률 복리 횟수(연, 0은 단리)', 'p_cpn_add': '상환청구 행사일 이자 별도 지급',
    'k_s': '콜 행사 시작일', 'k_e': '콜 행사 종료일', 'k_f': '콜 행사 주기(개월)',
    'k_prem': '콜 행사가액 가산율(연, %)', 'k_cmp': '콜 가산율 복리 횟수(연, 0은 단리)',
    'k_w': '콜 대상 비율(%)', 'k_lock': '콜 대상 의무보유 종료일',
    'k_lock_w': '의무보유 물량 비율(%, 비우면 콜 대상 비율과 같음)', 'k_hold': '콜 대상물량 의무보유',
    'k_lock_put': '의무보유 중 상환청구 제한', 'k_third': '콜 행사자에 제3자 지정 가능',
    'k_transfer': '콜 독립 양도 가능', 'k_kind': '제3자 콜 유형', 'k_basis': '콜 평가방법 선택 근거',
    'k_method': '콜 평가방법', 'k_split': '콜 행사가액 분해방법', 'k_cpn_add': '콜 행사일 이자 별도 지급',
    'pc_order': '풋·콜 동시 행사 시 우선권', 'k_conv_resp': '매도청구 통지 뒤 전환 대응', 'rfx_mode': '정기 전환가액 조정', 'rfx_cyc': '전환가액 조정 주기(개월)',
    'rfx_first': '최초 전환가액 조정일 (비우면 발행일 + 주기)',
    'floor': '최저 조정가액(원)', 'rfx_round': '조정 후 전환가액 원 단위 미만 처리', 'carry': '전환가액 경로 처리',
    'ipay': '이자 지급 주기(개월)', 'ytm': '만기보장수익률(연, %)', 'ytm_cmp': '만기수익률 복리 횟수(연, 0은 단리)',
    'mat_amt': '만기상환금액(원금 대비 %, -1은 산식)', 'acc_basis': '보장수익률 경과기간 기준',
    'p_sched': '상환청구 회차별 금액표', 'k_sched': '콜 회차별 금액표',
    'p_less_cpn': '상환청구금액의 기지급 이자 공제', 'k_less_cpn': '콜 금액의 기지급 이자 공제',
    'm_less_cpn': '만기상환금액의 기지급 이자 공제',
    'model': '평가모형', 'view': '평가 관점', 'gap_m': '계산 간격(개월)', 'grid_days': '일수 기준 계산 간격',
    'conv_class': '전환권 회계분류 가정', 'emb_approach': '내재파생 분리 정책 (회계정책)',
    'p_sep': '조기상환권 처리', 'k_sep': '콜 별도 금융상품 가정',
    'p_lost_int': '상환청구금액이 상실이자 보상 수준', 'fvpl_whole': '전체 당기손익 공정가치 지정 가정',
    'split_tol': '풋 분리 판단 비교기준 — 회계정책(기본 10%)', 'split_base_in': '풋 분리 판단 출발 금액(0 이하면 자동)',
    'split_base_why': '풋 분리 판단 출발 금액 근거',
    'put_bdt': '상환청구권에 BDT 금리격자 적용', 'bdt_sig': 'BDT 금리 변동성(연, %)', 'bdt_base': 'BDT 금리곡선 방식',
    'rf_curve': '무위험 금리곡선', 'cr_curve': '위험 금리곡선', 'cr_curve_b': '보간용 두 번째 위험 금리곡선',
    'cmp_rf': '무위험 금리 복리 횟수(연)', 'cmp_cr': '위험 금리 복리 횟수(연)', 'y_type': '금리 자료 유형',
    'rate_mode': '위험 금리곡선 입력 방식', 'rt_a': '위험 곡선 A 신용등급', 'rt_b': '위험 곡선 B 신용등급',
    'rt_tgt': '평가대상 신용등급', 'cr_src': '위험 금리 출처 기록',
    'ticker': '종목코드', 's0_src': '주당가치 출처 기록', 's0_date': '사용 주가의 실제 거래일',
    's0_raw': '원주가(원, -1은 미기록)', 's0_adj': '수정주가(원, -1은 미기록)', 's0_splits': '주식분할·병합 이력',
    'rvol_rating': '금리 변동성 자료의 신용등급', 'rvol_tenor': '금리 변동성 자료의 만기(년)', 'rvol_how': '금리 변동성 산출근거',
    'ipo_on': 'IPO 시점 가정 적용', 'ipo_m': '예상 IPO일', 'ipo_px': '예상 공모가액(원)', 'ipo_mult': '공모가 대비 전환가액 비율(%)',
    'ipo_min': '상장 최소 공모가격(원)', 'ipo_conv': 'IPO 시 보통주 강제전환',
    'bw_pay': 'BW 행사대금 납입 방식', 'bw_detach': 'BW 분리형 여부',
    'sha_put_s': '주주간계약 풋 행사 시작일', 'sha_put_e': '주주간계약 풋 행사 종료일', 'sha_put_f': '주주간계약 풋 주기(개월)',
    'sha_put_yield': '주주간계약 풋 가격 가산율(연, %, 0은 고정 가격)', 'sha_put_cmp': '주주간계약 풋 가산 복리 횟수(연, 0은 단리)',
    'sha_call_s': '주주간계약 콜 행사 시작일', 'sha_call_e': '주주간계약 콜 행사 종료일', 'sha_call_f': '주주간계약 콜 주기(개월)',
    'sha_call_prem': '주주간계약 콜 가격 가산율(연, %, 0은 고정 가격)', 'sha_call_cmp': '주주간계약 콜 가산 복리 횟수(연, 0은 단리)',
    'sha_call_k': '콜 주당 기준가격(원, -1은 풋과 같음)', 'sha_put_q': '풋 대상 주식수(-1은 계산기준금액 ÷ 기준가격)',
    'sha_call_q': '콜 대상 주식수(-1은 계산기준금액 ÷ 기준가격)', 'sha_side': '순액을 보는 관점',
    'sha_rows': '주주간계약 회차별 표',
    'dp_rows': '연도별 추정 배당가능이익 (발생연도 기준)', 'dp_others': '같은 배당가능이익을 쓰는 다른 상품',
    'dp_delay': '넘긴 상환금 연 가산율', 'dp_from': '재원 사용 시작일 (월-일)',
    'sha_writer': '풋 행사 시 주식매수 의무자', 'sha_disc': '주주간계약 풋 할인 방식', 'sha_spread': '주주간계약 신용스프레드(%p)',
    'sha_qipo_kill': '적격상장 시 소멸 권리', 'sha_kill': '한쪽 행사 시 같은 주식의 상대 권리',
    'sha_link_q': '같은 주식에 붙은 풋·콜 물량(주, -1은 미입력)',
    'sha_hold_q': '평가기준일 보유주식수(주, -1은 미입력 — 넣으면 회차 합계를 점검)',
    'sha_ipo_kind': '상장 조항의 종료 조건',
    'bs_target': '역산 목표(원금 100 기준)', 'bs_net': '역산 목표금액 기준',
    'prev_hold': '투자자 전기 장부금액(원금 100 기준, -1은 없음)',
    'd1_pl': '최초 인식 차이 처리', 'd1_reason': '최초 인식 차이를 당기손익으로 처리한 근거',
    'prev_host': '주계약 전기 장부금액(원금 100 기준, -1은 없음)',
    'prev_deriv': '파생상품 전기 장부금액(원금 100 기준, -1은 없음)',
    'issue_cost': '발행 거래원가(원)', 'eir_issue': '발행일 유효이자율(연, %, -100은 미입력)',
    'cur_periods': '당기 이자 회차 수(0은 연간)', 'settle_amt': '상환·재매입 지급대가(원금 100 기준, -1은 없음)',
    'tranche': '대상 회차', 'unmod_note': '모형 미반영 권리·근거 메모',
    'base_shares': '기존 보통주식수', 'dil_shares': '전환으로 증가하는 보통주식수',
    'additional_rights': '별도 계약조건', 'assumptions': '평가가정', 'defaults': '미입력 선택항목',
    'imported_defaults': '기존 파일의 보충값', 'applied_terms': '실제 적용 조건', 'result': '평가 결과', 'dates': '평가 관련 날짜',
    'notes': '검토메모', 'market_date': '시장자료 기준일',
    'sources': '자료 출처', 'contract': '계약서',
}
PERCENT = frozenset({'split_tol', 'sig', 'cpn', 'div_y', 'p_yield', 'k_prem', 'k_w', 'k_lock_w', 'ytm', 'bdt_sig',
                     'ipo_mult', 'sha_put_yield', 'sha_call_prem', 'sha_spread', 'eir_issue'})
EVENT_DATES = frozenset({'cv_s', 'cv_e', 'p_s', 'p_e', 'k_s', 'k_e', 'k_lock', 'ipo_m', 'rfx_first',
                        'sha_put_s', 'sha_put_e', 'sha_call_s', 'sha_call_e'})
CHOICES = {
    'model': {'TF': 'TF', 'GS': 'GS'}, 'view': {'holder': '투자자', 'issuer': '발행자'},
    'mat_mode': {0: '보통주 자동전환', 1: '현금상환'},
    'div_mode': {0: '상환가액에 가산', 1: '재량배당으로 부채 현금흐름에서 제외'},
    'div_basis': {0: '발행가 기준', 1: '액면가 기준'},
    'issuer_call': {0: '없음', 1: '발행자 상환권', 2: '제3자 지정 매도청구권'},
    'rfx_mode': {0: '정기 조정 없음', 1: '하향 조정', 2: '하향·상향 조정'},
    'carry': {1: '경로가중치 근사', 2: '확률가중평균 근사', 3: '특정노드 선택 근사'},
    'rfx_round': {0: '처리 없음 (계산값 그대로)', 1: '원 단위 미만 절상', 2: '원 단위 미만 절사'},
    'p_mode': {'fixed': '고정 상환율', 'accrue': '보장수익률 누적'},
    'conv_class': {'equity': '자본', 'liability': '파생상품부채'},
    'emb_approach': {1: '접근법 1 — 얽힌 권리를 먼저 묶고 판단 (기본)', 2: '접근법 2 — 권리마다 판단한 뒤 분리 대상끼리 묶기'},
    'p_sep': {1: '분리 — 파생상품으로 따로 인식 (얽힌 권리와 묶음)', 0: '주계약에 포함 (분리하지 않음)'},
    'pc_order': {0: '투자자 상환청구 우선', 1: '발행자 콜 우선'},
    'k_conv_resp': {1: '전환할 수 있다 (전환이 먼저)', 0: '전환할 수 없다 (매도청구가 먼저)'},
    'k_method': {0: '콜 유무 가치 비교', 1: '옵션차익 혼합할인율', 2: '옵션차익 성분 분리할인 (주식결제·현금결제)'},
    'k_split': {0: '가치 구성비율', 1: '전환확률'}, 'k_kind': {0: '제3자 지정 가능', 1: '제3자 사전 특정'},
    'bw_pay': {0: '현금납입', 1: '사채 대용납입'}, 'bw_detach': {0: '비분리형', 1: '분리형'},
    'acc_basis': {0: '실제 일수 / 365', 1: '계약상 개월 / 12'},
    'y_type': {'par': '만기수익률', 'spot': '현물이자율'},
    'rate_mode': {'direct': '직접 입력', 'rating': '두 신용등급 사이 보간', 'pick': '기존 직접입력'},
    'bdt_base': {0: '위험 금리곡선', 1: '무위험 금리 + 확정 스프레드'},
    'sha_writer': {0: '콜 권리자(상대 주주)', 1: '발행회사', 2: '상대 주주·발행회사 연대'},
    'sha_side': {0: '콜 권리자 관점 (콜 − 풋)', 1: '풋 권리자 관점 (풋 − 콜)'},
    'sha_disc': {0: '무위험 금리', 1: '위험 금리', 2: '무위험 금리 + 스프레드'},
    'sha_qipo_kill': {0: '풋만 소멸', 1: '풋·콜 모두 소멸'},
    'sha_kill': {0: '존속 (각자 판단)', 1: '소멸 (같은 주식 물량은 연계 판단)'},
    'sha_ipo_kind': {1: '실제 상장 완료 시 종료 (상장일·상장 가정일 — 주가와 무관)', 0: '그 시점 주가가 기준을 넘으면 상장으로 봄 (주가 기준)'},
    'bs_net': {0: '콜 차감 전', 1: '콜 차감 후'},
    'd1_pl': {0: '이연 (기본 · 1109 B5.1.2A(2))', 1: '당기손익 (관측 가능한 시장자료만 사용 · B5.1.2A(1))'},
}
for _key in ('p_less_cpn', 'k_less_cpn', 'm_less_cpn'):
    CHOICES[_key] = {0: '공제 없음', 1: '이자·배당의 미래가치 공제', 2: '기지급 명목금액 공제'}


LABELS.setdefault('input', '입력 점검')


# 주주간계약 화면에서만 바꿔 부르는 이름 — 사채가 없으므로 전환가액·발행일·발행금액이라 부르지 않는다.
SHA_LABELS = {
    'd_issue': '계약일(가격 가산 기산일)', 'K0': '주당 기준가격(원)',
    'face_total': '계산기준금액(원) — 주당 기준가격 × 대상 주식수', 'd_mat': '평가 종료일(마지막 행사 가능일)',
    'S0': '보통주 1주당 가치(원) — 평가기준일 주가',
    'pc_order': '동시 행사 우선권 (같은 날 풋·콜을 모두 행사하려 할 때)',
    'sha_kill': '행사 후 소멸하는 권리 — 한쪽이 행사하면 같은 주식의 상대 권리는',
    'sha_link_q': '같은 주식에 붙은 풋·콜 물량(주) — 한쪽 행사로 함께 끝나는 물량',
    'sha_writer': '풋 매수 의무자 (풋이 행사되면 주식을 사 주는 쪽)',
    'sha_put_q': '풋 대상 주식수(주, -1은 계산기준금액 ÷ 기준가격)', 'sha_call_q': '콜 대상 주식수(주, -1은 계산기준금액 ÷ 기준가격)',
}
# 주주간계약 화면의 선택지 — 사채의 «투자자 상환청구 · 발행자 콜» 이 아니라 권리자로 부른다.
SHA_CHOICES = {
    'pc_order': {0: '풋 권리자 우선', 1: '콜 권리자 우선'},
    'sha_kill': {0: '그대로 남는다 — 각 권리자가 자기 권리만 보고 판단', 1: '함께 끝난다 — 같은 주식 물량은 소멸 권리까지 보고 판단'},
    'sha_writer': {0: '콜 권리자(상대 주주)', 1: '대상회사(발행회사)', 2: '상대 주주·대상회사 연대'},
}


def choices(key, inst=None):
    """선택지 — 주주간계약이면 그 상품의 이름으로."""
    if inst == 'SHA' and key in SHA_CHOICES:
        return SHA_CHOICES[key]
    return CHOICES[key]


def label(key, inst=None):
    if key.startswith('additional_rights['):
        return '별도 계약조건 ' + str(int(key.split('[')[1][:-1]) + 1)
    if inst == 'SHA' and key in SHA_LABELS:
        return SHA_LABELS[key]
    return LABELS.get(key, key)


def display_value(key, value, issue_date=None, inst=None):
    if value is None:
        return '미입력'
    if key in CHOICES:
        return choices(key, inst).get(value, str(value))
    if key in ('sha_link_q', 'sha_hold_q') and value is not None and value < 0:
        return '미입력 (풋·콜 수량이 같으면 전부)'
    if key == 'k_lock_w' and value < 0:
        return '콜 대상 비율과 같음'
    if key == 'rfx_first' and not value:
        return '주기와 같음 (발행일 + 주기)'
    if key in EVENT_DATES and issue_date:
        return months_to_date(issue_date, value).isoformat()
    if key in PERCENT:
        return f'{value * 100:,.8g}%'
    if isinstance(value, (list, dict)):
        return str(value)
    return str(value)


def event_months(issue_date, chosen_date, original_value=None, original_issue_date=None):
    """Do not alter fractional legacy offsets when their visible date is unchanged."""
    if chosen_date is None:
        return None
    if original_value is not None and issue_date == original_issue_date:
        if chosen_date == months_to_date(issue_date, original_value):
            return original_value
    return date_to_months(issue_date, chosen_date)


def issue_rows(issues):
    return [{'구분': '입력 오류' if i.severity == 'error' else '평가자 확인',
             '항목': label(i.field), '확인 내용': i.message,
             '영향': i.impact or ('평가 실행을 중단합니다.' if i.severity == 'error' else '입력·적용범위의 확인이 필요합니다.'),
             '필요한 조치': i.action or '관련 입력과 계약·자료 근거를 확인하고 기록하십시오.'} for i in issues]
