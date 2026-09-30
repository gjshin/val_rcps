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
    'k_w': '콜 대상 비율(%)', 'k_lock': '콜 대상 의무보유 종료일', 'k_hold': '콜 대상물량 의무보유',
    'k_lock_put': '의무보유 중 상환청구 제한', 'k_third': '콜 행사자에 제3자 지정 가능',
    'k_transfer': '콜 독립 양도 가능', 'k_kind': '제3자 콜 유형', 'k_basis': '콜 평가방법 선택 근거',
    'k_method': '콜 평가방법', 'k_split': '콜 행사가액 분해방법', 'k_cpn_add': '콜 행사일 이자 별도 지급',
    'pc_order': '풋·콜 동시 행사 시 우선권', 'rfx_mode': '정기 전환가액 조정', 'rfx_cyc': '전환가액 조정 주기(개월)',
    'floor': '최저 조정가액(원)', 'carry': '전환가액 경로 처리',
    'ipay': '이자 지급 주기(개월)', 'ytm': '만기보장수익률(연, %)', 'ytm_cmp': '만기수익률 복리 횟수(연, 0은 단리)',
    'mat_amt': '만기상환금액(원금 대비 %, -1은 산식)', 'acc_basis': '보장수익률 경과기간 기준',
    'p_sched': '상환청구 회차별 금액표', 'k_sched': '콜 회차별 금액표',
    'p_less_cpn': '상환청구금액의 기지급 이자 공제', 'k_less_cpn': '콜 금액의 기지급 이자 공제',
    'm_less_cpn': '만기상환금액의 기지급 이자 공제',
    'model': '평가모형', 'view': '평가 관점', 'gap_m': '계산 간격(개월)', 'grid_days': '일수 기준 계산 간격',
    'conv_class': '전환권 회계분류 가정', 'p_sep': '상환청구권 분리 가정', 'k_sep': '콜 별도 금융상품 가정',
    'p_lost_int': '상환청구금액이 상실이자 보상 수준', 'fvpl_whole': '전체 당기손익 공정가치 지정 가정',
    'split_tol': '풋 분리 판단 비교기준(기본 10%)', 'split_base_in': '풋 분리 판단 출발 금액(0 이하면 자동)',
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
    'sha_put_yield': '주주간계약 풋 보장수익률(연, %)', 'sha_put_cmp': '주주간계약 풋 복리 횟수(연, 0은 단리)',
    'sha_call_s': '주주간계약 콜 행사 시작일', 'sha_call_e': '주주간계약 콜 행사 종료일', 'sha_call_f': '주주간계약 콜 주기(개월)',
    'sha_call_prem': '주주간계약 콜 가산율(연, %)', 'sha_call_cmp': '주주간계약 콜 복리 횟수(연, 0은 단리)',
    'sha_writer': '풋 의무자', 'sha_disc': '주주간계약 풋 할인 방식', 'sha_spread': '주주간계약 신용스프레드(%p)',
    'sha_qipo_kill': '적격상장 시 소멸 권리', 'sha_kill': '주주간계약 권리의 상호소멸',
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
PERCENT = frozenset({'split_tol', 'sig', 'cpn', 'div_y', 'p_yield', 'k_prem', 'k_w', 'ytm', 'bdt_sig',
                     'ipo_mult', 'sha_put_yield', 'sha_call_prem', 'sha_spread', 'eir_issue'})
EVENT_DATES = frozenset({'cv_s', 'cv_e', 'p_s', 'p_e', 'k_s', 'k_e', 'k_lock', 'ipo_m',
                        'sha_put_s', 'sha_put_e', 'sha_call_s', 'sha_call_e'})
CHOICES = {
    'model': {'TF': 'TF', 'GS': 'GS'}, 'view': {'holder': '투자자', 'issuer': '발행자'},
    'mat_mode': {0: '보통주 자동전환', 1: '현금상환'},
    'div_mode': {0: '상환가액에 가산', 1: '재량배당으로 부채 현금흐름에서 제외'},
    'div_basis': {0: '발행가 기준', 1: '액면가 기준'},
    'issuer_call': {0: '없음', 1: '발행자 상환권', 2: '제3자 지정 매도청구권'},
    'rfx_mode': {0: '정기 조정 없음', 1: '하향 조정', 2: '하향·상향 조정'},
    'carry': {1: '경로가중치 근사', 2: '확률가중평균 근사', 3: '특정노드 선택 근사'},
    'p_mode': {'fixed': '고정 상환율', 'accrue': '보장수익률 누적'},
    'conv_class': {'equity': '자본', 'liability': '파생상품부채'},
    'pc_order': {0: '투자자 상환청구 우선', 1: '발행자 콜 우선'},
    'k_method': {0: '콜 유무 가치 비교', 1: '옵션차익 혼합할인율', 2: '옵션차익 지분·부채 분리할인'},
    'k_split': {0: '가치 구성비율', 1: '전환확률'}, 'k_kind': {0: '제3자 지정 가능', 1: '제3자 사전 특정'},
    'bw_pay': {0: '현금납입', 1: '사채 대용납입'}, 'bw_detach': {0: '비분리형', 1: '분리형'},
    'acc_basis': {0: '실제 일수 / 365', 1: '계약상 개월 / 12'},
    'y_type': {'par': '만기수익률', 'spot': '현물이자율'},
    'rate_mode': {'direct': '직접 입력', 'rating': '두 신용등급 사이 보간', 'pick': '기존 직접입력'},
    'bdt_base': {0: '위험 금리곡선', 1: '무위험 금리 + 확정 스프레드'},
    'sha_writer': {0: '최대주주', 1: '발행회사', 2: '연대'},
    'sha_disc': {0: '무위험 금리', 1: '위험 금리', 2: '무위험 금리 + 스프레드'},
    'sha_qipo_kill': {0: '풋만 소멸', 1: '풋·콜 모두 소멸'},
    'sha_kill': {0: '독립', 1: '한쪽 행사 시 다른 권리 소멸'},
    'bs_net': {0: '콜 차감 전', 1: '콜 차감 후'},
    'd1_pl': {0: '이연 (기본 · 1109 B5.1.2A(2))', 1: '당기손익 (관측 가능한 시장자료만 사용 · B5.1.2A(1))'},
}
for _key in ('p_less_cpn', 'k_less_cpn', 'm_less_cpn'):
    CHOICES[_key] = {0: '공제 없음', 1: '이자·배당의 미래가치 공제', 2: '기지급 명목금액 공제'}


LABELS.setdefault('input', '입력 점검')


def label(key):
    if key.startswith('additional_rights['):
        return '별도 계약조건 ' + str(int(key.split('[')[1][:-1]) + 1)
    return LABELS.get(key, key)


def display_value(key, value, issue_date=None):
    if value is None:
        return '미입력'
    if key in CHOICES:
        return CHOICES[key].get(value, str(value))
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
