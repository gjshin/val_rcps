#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전환사채 평가 — Streamlit

실행
    pip install streamlit numpy pandas matplotlib openpyxl yfinance
    streamlit run cb_app.py

금액은 전자등록금액 100 기준이다.
"""
from __future__ import annotations
import math, json, io, re, calendar, datetime as dt
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd
import streamlit as st

# ══════════════════════════════════════════════════════════
# 1. 인풋
# ══════════════════════════════════════════════════════════
@dataclass
class Terms:
    S0: float = 853.0
    K0: float = 853.0             # 평가기준일 현재 전환(행사)가액
    # 상향 재조정의 상한. 계약은 「조정 후 전환가액은 최초 전환가액을 초과할 수
    # 없다」고 정하므로 상한은 **최초** 전환가액이지 현재 전환가액이 아니다.
    # 이미 하향 조정된 상품을 결산 평가할 때 둘이 갈린다. 음수면 K0 를 쓴다.
    K_cap: float = -1.0
    d_issue: str = "2025-03-31"     # 발행일
    d_base: str = "2025-03-31"      # 평가기준일
    d_mat: str = "2030-03-31"       # 만기일
    # ── 상품 스위치 ─────────────────────────────────────────
    # CB 는 액면 100, RCPS 는 1주 발행가 100 을 기준으로 잰다. 전환가치가
    # 100·S/K 로 같아서 격자는 그대로 쓰고, 다른 것은 만기와 콜의 성격뿐이다.
    inst: str = "CB"                # "CB" 전환사채 / "RCPS" 상환전환우선주 / "BW" 신주인수권부사채
    # BW — 신주인수권을 행사할 때 무엇으로 대금을 내는가가 계약을 가른다.
    #   대용납입: 사채를 권면액만큼 납입에 갈음한다. 사채가 소멸하고 주식을 받으므로
    #             전환사채와 수학적으로 같다. 격자를 그대로 쓴다.
    #   현금납입: 현금을 따로 내고 사채는 남는다. 사채와 신주인수권을 따로 재어 더한다.
    bw_pay: int = 0                 # BW 행사대금 0 현금납입 / 1 사채 대용납입
    bw_detach: int = 0              # BW 신주인수권 0 비분리형 / 1 분리형
    # ── 주주간계약 (SHA) ───────────────────────────────────
    # 사채가 없다. 투자자가 이미 가진 «지분»에 풋과 콜이 붙어 있을 뿐이라
    # 순차 차감이 아니라 두 옵션을 따로 잰다. 금액 기준은 투자원금 100 이고
    # 지분가치는 100 × 주가 ÷ 주당 인수가액이다.
    sha_put_s: float = 36.0         # 풋 행사 시작 (발행일 기준 개월)
    sha_put_e: float = 60.0         # 풋 행사 종료
    sha_put_f: float = 3.0          # 풋 행사 주기 (개월)
    sha_put_yield: float = 0.08     # 풋 보장수익률 (연)
    sha_put_cmp: int = 1            # 풋 보장수익률 복리 횟수 (0 이면 단리)
    sha_call_s: float = 0.0         # 콜 행사 시작. 시작 > 종료면 콜이 없다
    sha_call_e: float = 0.0
    sha_call_f: float = 3.0
    sha_call_prem: float = 0.10     # 콜 행사금액 가산율 (연)
    sha_call_cmp: int = 1
    sha_writer: int = 0             # 풋 의무자 0 최대주주 / 1 발행회사 / 2 연대
    sha_disc: int = 1               # 풋 할인 0 무위험 / 1 위험 곡선 / 2 무위험+스프레드
    sha_spread: float = 0.03        # 위 2 를 골랐을 때 더할 스프레드
    sha_qipo_kill: int = 1          # 적격상장 시 0 풋만 소멸 / 1 풋·콜 모두 소멸
    mat_mode: int = 0               # RCPS 존속기간 만료 시 0 보통주 자동전환 / 1 상환
    issuer_call: int = 0            # RCPS 콜 — 0 없음 / 1 발행자 상환권 / 2 제3자 지정 매도청구권
    div_mode: int = 0               # RCPS 우선배당 0 상환가액에 가산(전체 부채) / 1 재량(부채 현금흐름 제외)
    bs_target: float = 100.0        # 발행가 역산(Backsolve) 목표 — 발행가 100 기준
    # 역산 목표를 무엇에 맞출 것인가.
    #   0 본체 — 매도청구권을 뺀 사채·우선주 본체(B2)를 발행가에 맞춘다.
    #            매도청구권에 별도 대가가 오갔거나, 발행가가 본체만의 대가일 때.
    #   1 순액 — 「본체 − 매도청구권」 을 발행가에 맞춘다. 투자자는 돈을 내면서
    #            매도청구권을 함께 써 주었으므로, 실제로 받은 것은 그 차감 후
    #            순액이다. 발행가가 패키지 전체의 대가일 때가 이쪽이다.
    # 매도청구권이 유의적일 때만 값이 갈린다. validate() 가 그 자리를 짚는다.
    bs_net: int = 0                 # 0 본체 / 1 매도청구권 차감 순액
    prev_deriv: float = -1.0        # 전기말 파생상품부채 장부금액 (100 기준). 음수면 없음
    prev_host: float = -1.0         # 전기말 주계약(부채) 장부금액 (100 기준). 음수면 없음
    issue_cost: float = 0.0         # 발행 거래원가 (원). 1032 문단 38 로 요소별 배분
    eir_issue: float = -1.0         # 발행일 유효이자율 (연, 이산복리). 음수면 없음
    cur_periods: int = 0            # 당기 이자 회차 수. 0 이면 12 ÷ 지급주기
    settle_amt: float = -1.0        # 상환·재매입 지급대가 (100 기준). 음수면 없음
    # ── IPO 조항 (책 [사례 5-5]) ─────────────────────────────
    ipo_on: int = 0                 # 상장 조항을 격자에 넣는가
    ipo_m: float = 24.0             # 예상 상장 시점 (발행일 기준 개월)
    ipo_px: float = 0.0             # 공모가액 (원)
    ipo_mult: float = 0.70          # 공모가 배수 — 조정후 전환가격 = 공모가 × 배수
    ipo_min: float = 0.0            # 최소공모가격 (원). 주가가 이 밑이면 상장 무산
    ipo_conv: int = 1               # 상장하면 보통주로 강제전환하는가
    gap_m: float = 1.0              # 노드 간격 (개월)
    T: float = 5.0                  # 잔존기간 — derive() 가 채운다
    n: int = 60                     # 노드 수 — derive() 가 채운다
    elapsed_m: float = 0.0          # 발행일 → 평가기준일 경과 개월
    cpn: float = 0.0            # 표면이자율
    ipay: float = 3.0           # 이자 지급주기 (개월)
    ytm: float = 0.0            # 만기보장수익률
    ytm_cmp: int = 4            # 만기보장수익률 복리 횟수 (분기)
    cv_s: float = 12.0          # 전환 시작 (개월)
    cv_e: float = 59.0
    rfx_mode: int = 2           # 0 없음 / 1 하향만 / 2 하향+상향
    rfx_cyc: float = 7.0
    floor: float = 598.0
    par: float = 500.0
    carry: int = 0              # 0 상태확장 / 1 경로가중치 / 2 확률가중평균 / 3 특정노드선택
    p_s: float = 24.0
    p_e: float = 57.0
    p_f: float = 3.0
    p_rate: float = 100.0
    k_s: float = 12.0
    k_e: float = 24.0
    k_f: float = 3.0
    k_prem: float = 0.01
    k_cmp: int = 4
    k_w: float = 0.30
    k_lock: float = 25.0
    k_third: int = 1              # 매도청구권을 제3자에게 지정할 수 있는가
    k_transfer: int = 0           # 매도청구권을 사채와 독립적으로 양도할 수 있는가
    # 조기상환청구권과 매도청구권이 **같은 노드에서 함께 열릴 때** 누구의 권리가
    # 먼저 작동하는지는 계약이 정한다. 수식이 정하는 것이 아니다.
    #   0 투자자 풋 우선 — 통지한 조기상환은 매도청구로 막지 못한다.
    #                    한국 사모 CB 의 매도청구권은 사채 **일부를 매수**하는
    #                    권리이지 상환이 아니라는 읽기다.  V = MAX(전환, 풋, MIN(보유, 콜))
    #   1 발행자 콜 우선 — 콜이 유효하게 행사되면 투자자는 전환으로만 대응한다.
    #                    미국식 callable convertible 의 표준 처리다 (Hull).
    #                    V = MAX(전환, MIN(MAX(보유, 풋), 콜))
    # 두 금액이 다르고 행사기간이 겹칠 때만 값이 갈린다. validate() 가 그 자리를 짚는다.
    pc_order: int = 0             # 0 투자자 풋 우선 / 1 발행자 콜 우선
    # 보통주 배당수익률. 위험중립 드리프트에서 빠진다 — 배당을 받지 못하는
    # 전환권·신주인수권은 그만큼 값이 낮아진다. 비상장 성장기업은 0 이 보통이나
    # 상장 배당기업의 CB·BW 를 재려면 반드시 넣어야 한다 (0 이면 과대평가).
    div_y: float = 0.0            # 배당수익률 δ (연, 연속복리)
    # 주주간계약에서 한쪽이 행사하면 계약이 끝나 다른 쪽 권리도 소멸하는가.
    #   0 독립 — 두 권리를 각각 따로 잰다. 개별 옵션의 공정가치를 각 당사자
    #            입장에서 재는 목적이면 이쪽이다.
    #   1 상호소멸 — 투자자가 풋을 행사하면 최대주주 콜이, 최대주주가 콜을
    #            행사하면 투자자 풋이 그 자리에서 사라진다. 두 권리가 경제적으로
    #            독립이 아니므로 한 격자에서 함께 풀어야 한다.
    sha_kill: int = 0             # 0 독립 / 1 상호소멸
    p_lost_int: int = 0           # 조기상환 행사금액이 상실이자 보상 수준인가
    fvpl_whole: int = 0           # 복합계약 전체를 당기손익-공정가치로 지정했는가
    k_method: int = 0
    p_sep: int = 1                  # 조기상환권 1 분리 / 0 주계약에 포함
    k_sep: int = 1                  # 1 별도 금융상품 / 0 복합내재파생에 포함            # 0 유무가치비교 / 1 혼합할인율 / 2 지분·부채 분리
    sig: float = 0.4130
    model: str = "TF"
    conv_class: str = "equity"   # equity 전환권 자본 / liability 전환권 파생상품부채
    cmp_rf: int = 2              # 무위험 복리 횟수 (국고채 반기)
    cmp_cr: int = 4              # 위험 복리 횟수 (회사채 분기)
    p_mode: str = "fixed"        # fixed 고정률 / accrue 보장수익률 복리
    p_yield: float = 0.0         # 조기상환 보장수익률
    p_cmp: int = 4               # 조기상환 보장수익률 복리 횟수
    face_total: float = 25_000_000_000.0   # 전자등록총액 (원)
    ticker: str = ""              # 종목코드·티커 (주가·변동성 조회용, 비상장이면 빈칸)
    s0_src: str = ""              # 평가기준일 주가의 출처 ("야후 085660.KQ 2024-06-28 종가"). 빈칸 = 직접 입력
    rate_mode: str = "direct"      # direct 직접 · pick 등급 하나 · rating 두 등급 보간
    cr_src: str = ""               # 위험 곡선을 어디서 가져왔는지 (조서에 적는다)     # direct 곡선 직접 / rating 등급 보간
    rt_a: str = "BBB+"            # 인풋 곡선 A 등급
    rt_b: str = "BBB-"            # 인풋 곡선 B 등급
    rt_tgt: str = "BBB0"          # 평가대상 등급
    cr_curve_b: list = field(default_factory=list)
    put_bdt: int = 0              # 조기상환권 0 격자(확정) / 1 BDT 금리격자
    bdt_sig: float = 0.20         # BDT 단기이자율 변동성 (로그정규, 연)
    bdt_base: int = 0             # 0 위험 곡선 직접 / 1 무위험 + 확정 스프레드
    y_type: str = "par"           # par 만기수익률 / spot 현물이자율
    rf_curve: list = field(default_factory=list)   # [(만기, 연이율)]
    cr_curve: list = field(default_factory=list)


def accrue_rate(t_year: float, g: float, c: float, m: int) -> float:
    """상환할증금률. 미지급 보장수익률을 매 회차 적립해 굴린 연금의 미래가치다.

        Σ_{k=1..mt} ((g−c)/m)·(1+g/m)^(mt−k)  =  (g−c)/g · ((1+g/m)^(mt) − 1)

    표면이자율 c 를 빼는 것은 그만큼 이미 현금으로 지급했기 때문이다.
    c 가 0 이면 (1+g/m)^(mt) − 1 로 줄어 종전 산식과 같아진다.

    보장수익률이 표면이자율보다 낮으면 산식이 음수가 된다. 그러나 할증금은
    수익률을 채워 주려고 **더** 얹는 돈이라 음수가 될 수 없다 — 이미 지급한
    이자를 만기에 되돌려 받는 계약은 없다. 그래서 0 에서 끊는다. 그런
    입력은 애초에 잘못이므로 validate() 가 따로 경고한다.

    복리 횟수 m 이 0 이면 **단리**다. 「발행가에 연 X% 단리를 가산」이라고 쓴
    계약이 적지 않다. 그때 할증금은 (g − c)·t 로, 이미 지급한 이자만 빼면 된다.
    """
    if t_year <= 0: return 0.0
    m = int(m)
    if m <= 0: return max(0.0, (g - c)*t_year)      # 단리
    if g <= 1e-12: return max(0.0, (g - c)*t_year)   # g → 0 극한
    return max(0.0, (g - c)/g * ((1 + g/m)**(m*t_year) - 1))


def xl_prem(g: str, c: str, m: str, yr: str) -> str:
    """상환할증금률의 엑셀 식. 엔진의 accrue_rate 와 같은 갈래를 탄다.

    복리 횟수 셀이 0 이면 단리 (g − c)·t 다. 거짓 갈래도 파서가 훑고 지나가므로
    나눗셈에 MAX(1, m) 을 씌워 0 으로 나누는 일이 없게 한다.
    """
    mm = f"MAX(1,{m})"
    return (f"IF({m}<=0,MAX(0,({g}-{c})*{yr}),"
            f"MAX(0,({g}-{c})/{g}*((1+{g}/{mm})^({mm}*{yr})-1)))")


def step_mapper(tm: "Terms", n: int, dt_: float):
    """계약상 월(발행일 기준)을 노드 번호로 바꾸는 두 함수를 만든다.

    스텝을 반올림으로 잡으면 계약일 **전**의 노드에서 행사가 열려 옵션이
    과대평가된다. 그래서 노드의 실제 날짜를 계약일과 직접 견준다.

        lo(m)  계약일 **이후** 첫 노드   — 행사기간 시작에 쓴다
        hi(m)  계약일 **이전** 마지막 노드 — 행사기간 종료에 쓴다

    노드가 하나도 조건을 만족하지 않으면 lo 는 n+1, hi 는 −1 을 돌려주어
    그 구간이 비어 있음을 알린다.
    """
    di = dt.date.fromisoformat(tm.d_issue)
    db = dt.date.fromisoformat(tm.d_base)
    day = dt_*365                                   # 한 스텝의 일수
    nd = [db + dt.timedelta(days=round(i*day)) for i in range(n+1)]
    # 노드는 한 달을 30.4일로 잡아 놓으므로 달력 기준일과 하루이틀 어긋난다.
    # 그만큼은 같은 날로 본다. 노드 하나를 통째로 앞당길 만큼은 못 된다.
    tol = dt.timedelta(days=min(5, max(1, int(day//4))))

    def cd(m):                                      # 발행일 + m 개월
        k = int(math.floor(m)); fr = m - k
        d = _add_months(di, k)
        return d + dt.timedelta(days=round(fr*30.4375)) if fr else d

    def lo(m):
        c = cd(m) - tol
        return next((i for i, x in enumerate(nd) if x >= c), n+1)

    def hi(m):
        c = cd(m) + tol
        return next((i for i in range(n, -1, -1) if nd[i] <= c), -1)
    return lo, hi


def pay_offset(tm: "Terms", st_lo) -> int:
    """평가기준일 뒤 첫 이자·배당 지급 노드.

    지급일은 계약(발행일) 기준으로 확정된다. 평가기준일에서 다시 세면 결산
    평가에서 지급일이 통째로 밀려 지급 횟수가 하나 사라진다. 리픽싱의 첫
    조정 스텝(``rfx_off``)과 같은 방식으로 발행일에서 센다.
    """
    if tm.ipay <= 0: return 1
    return st_lo(tm.ipay*(math.floor(tm.elapsed_m/tm.ipay) + 1))


def _add_months(d: dt.date, k: int) -> dt.date:
    """d 에서 k 개월 뒤 같은 날. 그 달에 그 날이 없으면 말일로 맞춘다."""
    y, mo = d.year + (d.month - 1 + k)//12, (d.month - 1 + k) % 12 + 1
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def months_between(d1: dt.date, d2: dt.date) -> float:
    """개월 단위 경과기간.

    꽉 찬 개월을 세고, 남는 일수는 **그 구간의 실제 한 달 길이**로 나눈다.
    말일까지 남은 날수로 나누면 안 된다 — 그러면 12월 23일에서 31일까지 8일이
    0.89개월로 부풀어 결산일 평가가 통째로 밀린다.
    """
    if d2 <= d1: return 0.0
    m = (d2.year - d1.year)*12 + (d2.month - d1.month)
    if d2.day < d1.day: m -= 1
    same, nxt = _add_months(d1, m), _add_months(d1, m + 1)
    return m + (d2 - same).days/max(1, (nxt - same).days)


# 지원하지 않는 조합. 세 경로 — 사이드바(잠금 캡션) · validate()(경고) · derive()(되돌림) — 가
# **같은 문구**를 쓴다. 화면만 막고 엔진이 조용히 다른 값을 내는 일이 없게 하기 위해서다.
COMPAT_GS_KMETHOD = ("**GS 에서는 유무가치비교법만 지원합니다.** 옵션차익혼합할인법은 "
                     "노드의 지분·부채 분해 위에 정의된 산식이라 TF 전용입니다 "
                     "(한공회 4.4.3). GS 로 재려면 신용위험 처리를 TF 로 바꾸십시오.")
COMPAT_PSEP = ("조기상환권을 분리하지 않는 선택은 전환권을 **자본**으로 두고 매도청구권을 "
               "**별도 금융상품**으로 볼 때만 고를 수 있습니다. 매도청구권을 내재파생으로 "
               "묶으면 복수의 내재파생을 하나의 복합내재파생으로 다루므로 (문단 B4.3.4) "
               "조기상환권도 함께 분리됩니다. 전환권이 부채여도 같은 이유로 묶음에 들어갑니다.")
COMPAT_BDT = ("BDT 금리격자는 전환권을 **자본**으로 두고 **TF** 를 쓸 때만 켤 수 있습니다. "
              "자본이면 전환권대가가 잔여라 부채요소만 바꿔도 배분이 성립하지만, 부채이면 "
              "복합내재파생을 전체로서 재야 해서 전체 가치까지 함께 손봐야 합니다.")


# 알려진 한계 — (제목, 설명, 실려야 하는 곳). 화면 검산 탭 · 조서 99_모형검증 · README 「한계」 가
# 모두 이 표에서 나온다. 시험(기능목록.py)이 README 에 제목이 있는지 확인한다.
MODEL_LIMITS = (
    ('복합내재파생이 음수',
     '발행자 상환권이 전환권보다 크면 부채 갈래 묶음이 음수 (대신증권 −9.0050)',
     ('화면 배분표 캡션', 'docs/사례_대신증권_RCPS.md', '조서 회계처리 시트')),
    ('배당가능이익·상환재원 제약 미반영',
     '계약상 상환일에 즉시 상환된다고 본다',
     ('UNMODELLED_NOTE (조서 표지)', 'README', 'docs/입력안내_RCPS.md')),
    ('IPO 는 가정 비교이지 PWERM 이 아니다',
     '상장 시점·공모가는 확률분포가 아니라 가정',
     ('UNMODELLED_NOTE', 'README', '화면 IPO 캡션')),
    ('자기신용위험 변동분(5.7.7) 분해 안 함',
     '전체 FVPL 지정 후속측정에서 OCI 몫을 나누지 않는다',
     ('UNMODELLED_NOTE', 'validate() 경고', '조서 상각표 자리 FVPL_NOTE', 'docs/의사결정규칙.md §13')),
    ('리픽싱 근사법(경로가중 등)은 근사',
     'E[1/K] ≠ 1/E[K]. 상태확장이 정확법',
     ('화면 조정일 처리 캡션', '조서 가정 시트 경고', 'docs/의사결정규칙.md')),
    ('리픽싱 기준가 = 노드 주가 (VWAP 아님)',
     '계약의 가중평균가 대신 노드 주가',
     ('UNMODELLED_NOTE', 'docs/입력안내_RCPS.md')),
    ('사전통지기간 미반영',
     '행사일 = 노드일',
     ('docs/의사결정규칙.md §7',)),
    ('BW: 신주인수권증권 자체에 대한 콜 미지원',
     '콜은 사채에 대한 권리로만 잰다',
     ('validate() 경고', 'docs/입력안내_BW.md')),
    ('BW: 부분행사·다단계 행사가액 미지원',
     '단일 행사·단일 행사가액',
     ('docs/입력안내_BW.md',)),
    ('SHA: 상대방 신용은 할인율로만',
     '부도 손실률·회수율 구조 없음',
     ('화면 풋 할인율 캡션', 'docs/입력안내_주주간계약.md')),
    ('SHA: Drag/Tag/ROFR·다단계 strike 미지원',
     '',
     ('docs/입력안내_주주간계약.md',)),
    ('역산이 목표를 정확히 못 맞힐 수 있다',
     '격자 값의 계단 (0.19 등)',
     ('화면 역산 경고', 'docs/의사결정규칙.md §10-1')),
    ('자동전환·상장 강제전환 RCPS 의 전환권이 음수',
     '존속기간 만료 시 자동전환·상장 시 강제전환은 권리가 아니라 의무다. 주가가 낮으면 B2 < B1 이라 「전환권 = B2 − B1」 이 음수다 (분기전수·조합시험이 limit 로 허용)',
     ('화면', '조서')),
    ('강제전환 할인율 효과로 매도청구권이 음수',
     'TF·GS 는 지분을 무위험(전환확률 가중)으로 할인한다. 콜이 전환을 강제하면 부채가 지분으로 바뀌어 할인이 가벼워지고 전체 가치가 오를 수 있다 → 유무가치비교법 매도청구권 < 0. RCPS 발행자 상환권은 ca_debt=max(0,·) 로 막는다',
     ('화면', '조서', 'README')),
    ('잔여 주계약 ≤ 0 이면 상각표 없음',
     '발행가 100 과 공정가치가 크게 다르면(Day-1 차이) 부채 분류의 잔여 주계약이 0 이하다. 유효이자율이 정의되지 않으므로 상각표를 만들지 않고 그 사실을 적는다 (4단계 F-05)',
     ('화면', '조서')),
    ('비분리형 BW 는 신주인수권 조기행사를 상태로 갖지 않는다',
     '자식 노드의 매도청구는 신주인수권이 살아 있다고 보고 정해진다. 부모에서 투자자가 먼저 행사하면 그 콜은 사채만 비싸게 사는 셈이라 콜 있는 격자가 없는 격자보다 커질 수 있다 (매도청구권 < 0). 두 상태 격자로 고치는 것은 산식 변경이라 별도 승인 대상',
     ('화면', '조서', 'README')),
)


HOST_NONPOS_NOTE = ("**잔여 주계약이 0 이하라 상각표를 만들지 않습니다.** 전체 가치가 발행가 100 과 "
                    "크게 달라 파생을 뺀 잔여가 남지 않는 자리입니다 — 최초 인식 시점의 공정가치와 "
                    "거래가격의 차이(Day-1 차이, 제1109호 문단 B5.1.2A)를 먼저 정리하셔야 합니다. "
                    "유효이자율이 정의되지 않으므로 상각표·이자비용 대신 이 문구가 조서에 실립니다.")


def compat(tm: Terms):
    """지원하지 않는 조합을 찾는다. [(필드, 되돌릴 값, 사유)].

    derive() 가 이 목록대로 되돌리고 ``tm.forced_notes`` 에 남긴다. validate() 가 그것을
    경고로 올리고, 사이드바는 같은 사유로 칸을 잠근다. 주주간계약·BW 전용 강제는
    derive() 안에 따로 있다 — 그것은 «상품에 없는 스위치» 라 사용자가 고른 것이 아니다.
    """
    out = []
    if tm.model == "GS" and int(tm.k_method):
        out.append(("k_method", 0, COMPAT_GS_KMETHOD))
    if int(tm.p_sep) == 0 and not (tm.conv_class == "equity" and int(tm.k_sep) != 0):
        out.append(("p_sep", 1, COMPAT_PSEP))
    if int(tm.put_bdt) and not (tm.conv_class == "equity" and tm.model == "TF"):
        out.append(("put_bdt", 0, COMPAT_BDT))
    return out


def derive(tm: Terms) -> Terms:
    """날짜에서 경과기간·잔존기간·노드 수를 계산해 채운다.

    지원하지 않는 조합(compat)은 여기서 되돌린다 — 화면·validate·직접 호출이 같은 값을
    내야 하기 때문이다. 되돌린 내역은 ``forced_notes`` 에 남는다 (두 번째 호출은 이미
    되돌린 뒤라 비어 있으므로 덮어쓰지 않는다).
    """
    _f = compat(tm)
    if _f:
        tm.forced_notes = _f
        for k, v, _ in _f: setattr(tm, k, v)
    di = dt.date.fromisoformat(tm.d_issue)
    db = dt.date.fromisoformat(tm.d_base)
    dm = dt.date.fromisoformat(tm.d_mat)
    tm.elapsed_m = max(0.0, months_between(di, db))
    tm.T = max(1e-6, (dm-db).days/365)
    gap = max(0.25, tm.gap_m)
    tm.n = max(4, int(round(tm.T*12/gap)))
    if is_sha(tm):
        # 주주간계약에는 사채가 없다. 사채·우선주 전용 스위치를 모두 끈다.
        # 지분가치는 100 × 주가 ÷ 주당 인수가액이라 리픽싱도 없다.
        tm.mat_mode = 1; tm.issuer_call = 0; tm.div_mode = 0
        tm.rfx_mode = 0; tm.cpn = 0.0; tm.ytm = 0.0
        tm.k_w = 0.0; tm.k_method = 0; tm.k_lock = 0.0
        tm.p_sep = 1; tm.k_sep = 1
        tm.put_bdt = 0
        tm.ipo_conv = 0          # 강제전환이 아니라 풋·콜이 소멸하는 사건이다
        tm.carry = 1             # 리픽싱이 없어 상태확장이 뜻을 잃는다
        return tm
    if is_bw(tm):
        # 신주인수권부사채는 우선주가 아니므로 RCPS 전용 스위치를 모두 끈다.
        tm.mat_mode = 1          # 만기에 자동전환되는 갈래가 없다
        tm.issuer_call = 0       # 발행자 상환권·제3자 지정은 RCPS 전용 스위치다
        tm.div_mode = 0
        if int(tm.bw_pay) == 1:
            # 대용납입 — 사채를 권면액만큼 납입에 갈음한다. 사채가 소멸하므로
            # 분리·비분리 구분이 격자에 남기는 흔적이 없다.
            tm.bw_detach = 0
    if is_rcps(tm):
        ic = int(tm.issuer_call)
        if ic == 2:
            # 제3자 지정 매도청구권 — 발행회사가 **지정하는 제3자**가 인수인이
            # 가진 우선주를 사 가는 권리다. 거래상대방이 발행회사가 아니므로
            # 내재파생이 아니라 **별도의 금융상품**이고 (문단 4.3.1), 기초자산이
            # 전환권까지 붙은 우선주라 전체 격자에서 잰다 — CB 의 매도청구권과
            # 같은 길이다. 한도·의무보유·평가방법을 사용자가 정한다.
            tm.k_third = 1; tm.k_transfer = 0
            tm.k_sep = 1
        elif ic == 1:
            # 발행자 상환권 — 거래상대방이 그대로인 내재파생이라 격자 안에서
            # MIN(보유, 상환가액) 으로 누르고, 전체(100%)에 걸린다. 상환청구권과
            # 하나의 복합내재파생으로 묶는다 (문단 B4.3.4).
            tm.k_w = 1.0
            tm.k_method = 0; tm.k_lock = 0.0
            tm.k_third = 0; tm.k_transfer = 0
            tm.k_sep = 0
        else:
            # 콜이 없다. 상환청구권 하나뿐이라 분리 여부(p_sep)가 산다.
            tm.k_w = 0.0
            tm.k_method = 0; tm.k_lock = 0.0
            tm.k_third = 0; tm.k_transfer = 0
            tm.k_sep = 1
    return tm


def is_rcps(tm: Terms) -> bool:
    return (tm.inst or "CB").upper() == "RCPS"


def is_bw(tm: Terms) -> bool:
    return (tm.inst or "CB").upper() == "BW"


def bw_cash(tm: Terms) -> bool:
    """신주인수권 행사대금을 **현금으로** 내는 BW 인가.

    현금납입이면 행사해도 사채가 남는다. 그래서 격자가 갈린다 — 사채와
    신주인수권을 따로 재어 더한다. 대용납입이면 사채를 권면액만큼 납입에
    갈음해 사채가 소멸하므로 전환사채와 완전히 같은 계산이 된다.
    """
    return is_bw(tm) and int(tm.bw_pay) == 0


def is_sha(tm: Terms) -> bool:
    """주주간계약인가. 사채가 없어 격자와 조서가 통째로 갈린다."""
    return (tm.inst or "CB").upper() == "SHA"


def bw_alive(tm: Terms) -> bool:
    """사채가 소멸해도 신주인수권이 살아남는가 (분리형).

    분리형이면 신주인수권증권이 따로 유통되므로 사채를 조기상환받아도
    신주인수권은 행사기간 끝까지 남는다. 비분리형이면 사채에 붙어 있어
    사채가 소멸할 때 함께 소멸한다 — 상환 직전에 행사할 기회는 있다.
    """
    return bw_cash(tm) and int(tm.bw_detach) == 1


# 주주간계약의 행사 판정 허용오차. 사채 격자의 TOL(1e-9) 보다 촘촘하다 —
# 지분가치 격자에는 리픽싱 동점이 없어 잡음만 걸러 내면 된다. 엔진 settle() 과
# 수식 조서가 **같은 값**을 봐야 조서가 엔진을 따라온다.
SHA_SETTLE_TOL = 1e-12


def node_decide(cv, pv, kv, hold, kfirst):
    """한 노드에서 **누가 이기는가**. 「무엇을 받는가」는 상품이 정한다.

    전환사채·상환전환우선주와 신주인수권부사채 두 갈래가 각자 이 사슬을 복제해
    가지고 있었다. 그래서 한쪽을 고치면 다른 쪽에 반영이 안 되는 회귀가 두 번
    났다 — 뿌리 노드 예외와, 매도청구 시 신주인수권이 사라지는 결함이다.
    이제 판정은 여기 한 곳에만 있다.

    부르는 쪽이 자기 계약을 후보로 번역해서 넘긴다.

        전환사채·우선주   node_decide(전환가치, 조기상환, 매도청구, 보유)
        BW 분리형         node_decide(-inf,     조기상환, 매도청구, 사채)
        BW 비분리형       node_decide(-inf,     조기상환+신주인수권,
                                      매도청구+신주인수권, 보유+신주인수권)

    전환이 없는 갈래는 ``cv=-inf``, 행사기간 밖이라 콜이 없으면 ``kv=+inf`` 다.
    난수 40만 건과 동점 조합으로 옛 세 사슬과 같은 답을 내는 것을 확인했고,
    그 대조는 ``tests/손계산대조.py`` 에 회귀로 남아 있다.

    ``kfirst`` 가 참이면 발행자 매도청구가 먼저다 (``Terms.pc_order``).

        투자자 풋 우선 :  MAX(전환, 풋, MIN(보유, 콜))
        발행자 콜 우선 :  MAX(전환, MIN(MAX(보유, 풋), 콜))

    **동점 규칙이 값보다 정산확률에 크게 영향을 준다.** 전환과 콜은 허용오차만큼
    앞설 때만 이기고(``+TOL``), 풋과 보유는 동점이면 이긴다(``−TOL``). 리픽싱이
    주가로 재설정되는 날에는 전환가치가 정확히 100 이 되어 조기상환금액과 동점이
    되는데, 정해 두지 않으면 부동소수 잡음이 갈라 놓는다.

    돌려주는 것은 ``"conv"`` · ``"put"`` · ``"call"`` · ``"hold"`` 넷 중 하나다.
    """
    if kfirst:
        # 콜이 없을 때 투자자가 고를 값. 콜은 이것을 눌러 내리는 쪽으로만 쓴다.
        inv = max(hold, pv)
        if cv >= min(inv, kv) + TOL: return "conv"
        if inv > kv + TOL:           return "call"
        if pv >= hold - TOL:         return "put"
        return "hold"
    inner = min(hold, kv)
    if cv >= max(pv, inner) + TOL: return "conv"
    if pv >= inner - TOL:          return "put"
    if hold <= kv + TOL:           return "hold"
    return "call"


def xl_decide(cv, pv, kv, hold, kfirst, tolx,
              names=("전환", "상환P", "상환C", "보유")):
    """``node_decide`` 와 같은 결정을 엑셀 IF 중첩으로 쓴다.

    인자는 숫자가 아니라 **셀 주소 문자열**이다 (``"C12"``, ``"MAX(D5,E5)"`` 처럼
    식이어도 된다). 전환이 없는 갈래는 ``cv=None`` 으로 부르면 그 가지를 빼고,
    ``names`` 로 라벨을 갈아 끼운다.

    파이썬과 엑셀이 같은 판정을 하도록 **한 곳에서** 만든다. 예전에는 같은 패턴을
    트랜치 TF·GS·부채요소·신주인수권부사채 트랜치에 손으로 네 벌 썼다.
    """
    _c, _p, _k, _h = names
    if kfirst:
        inv = f"MAX({hold},{pv})"
        out = (f'IF({inv}>{kv}+{tolx},"{_k}",'
               f'IF({pv}>={hold}-{tolx},"{_p}","{_h}"))')
        if cv is not None:
            out = f'IF({cv}>=MIN({inv},{kv})+{tolx},"{_c}",{out})'
        return out
    inner = f"MIN({hold},{kv})"
    out = (f'IF({pv}>={inner}-{tolx},"{_p}",'
           f'IF({hold}<={kv}+{tolx},"{_h}","{_k}"))')
    if cv is not None:
        out = f'IF({cv}>=MAX({pv},{inner})+{tolx},"{_c}",{out})'
    return out


def xl_pick(dec, pv, kv, hold, cv=None, names=("전환", "상환P", "상환C", "보유")):
    """결정 셀(``dec``)을 읽어 그 노드의 값을 고르는 엑셀 식.

    ``node_decide`` 뒤에 오는 값 배정과 같은 자리다. 결정과 값을 두 번 따로
    쓰면 어긋나므로 여기서 함께 만든다.
    """
    _c, _p, _k, _h = names
    out = f'IF({dec}="{_p}",{pv},IF({dec}="{_k}",{kv},{hold}))'
    if cv is not None:
        out = f'IF({dec}="{_c}",{cv},{out})'
    return out


def called_conv(cv, pv, kv, hold, kfirst) -> bool:
    """그 전환이 **매도청구를 당해 전환으로 대응한** 것인가.

    콜이 없었다면(``kv=inf``) 투자자가 전환을 고르지 않았을 자리다. 값은 같지만
    (어느 쪽이든 지분 = 전환가치, 부채 = 0) 정산 분포에서는 뜻이 다르다 —
    「투자자가 스스로 전환했다」와 「발행자가 불러서 어쩔 수 없이 전환했다」는
    기대만기 해석이 갈린다.
    """
    return (node_decide(cv, pv, kv, hold, kfirst) == "conv"
            and node_decide(cv, pv, math.inf, hold, kfirst) != "conv")


def fvpl_on(tm: Terms) -> bool:
    """복합계약 **전체**를 당기손익-공정가치로 지정한 갈래인가.

    지정하면 내재파생을 떼지 않고(문단 4.3.3(3)) 전체를 하나의 금융부채로 재므로
    배분표가 한 줄이 되고 유효이자율 상각표를 만들지 않는다.

    **전환권이 자본이면 지정할 수 없다.** 문단 4.2.2 는 지정을 **금융부채**에만
    허용하는데 자본요소는 금융부채가 아니기 때문이다. 그 설정에서는 여기서 거짓을
    돌려주어 배분표 형태가 조용히 바뀌지 않게 하고, ``validate()`` 의 경고가 그대로
    서 있게 한다 — 잘못 고른 것을 앱이 대신 정당화해 주면 안 된다.

    주주간계약은 사채가 없어 복합계약이 아니므로 해당이 없다.
    """
    return bool(int(tm.fvpl_whole) and not is_sha(tm)
                and tm.conv_class != "equity")


def issuer_redeem(tm: Terms) -> bool:
    """콜을 **발행자 상환권 방식**으로 재는가.

    RCPS 라도 제3자 지정 매도청구권이면 거래상대방이 달라 별도의 금융상품이고,
    기초자산이 전환권 붙은 우선주라 **전체 격자**에서 잰다 — CB 와 같은 길이다.
    발행자 상환권만 부채 격자에서 재고(문단 31) 트리·조서도 다르게 그린다.
    """
    return is_rcps(tm) and int(tm.issuer_call) == 1


def auto_conv(tm: Terms) -> bool:
    """존속기간 만료 시 보통주로 자동전환되는 RCPS 인가."""
    return is_rcps(tm) and int(tm.mat_mode) == 0


def eff_cpn(tm: Terms) -> float:
    """계산에 쓰는 정기 지급률.

    CB 의 표면이자는 채무라 늘 들어간다. RCPS 의 우선배당은 계약에 따라 갈린다
    (1032 AG37) — 미지급분을 상환가액에 가산하면 전체가 부채고 배당은 이자비용
    (그대로 쓴다). 배당이 발행자 재량이고 상환가액과 무관하면 배당은 자본요소의
    이익분배라 부채 현금흐름에서 빼고, 상환가액 산식도 배당을 차감하지 않는다(0).
    """
    return 0.0 if (is_rcps(tm) and int(tm.div_mode) == 1) else tm.cpn


SCOPE_NOTE = (
    "적용범위 — 주가를 관찰하거나 역산할 수 있는 **단일 종류**의 전환사채·"
    "신주인수권부사채·상환전환우선주와, 그 지분에 붙은 주주간계약의 풋·콜을 "
    "이항격자로 잰다.")
UNMODELLED_NOTE = (
    "반영하지 않은 것 — 배당가능이익·순자본비율 등 상환재원 제약에 따른 실제 상환 "
    "지연, 기간에 따라 달라지는 배당률(step-up), 조기상환 청구 "
    "시차(지급기일 60일 전~30일 전), 종류주식 간 배분(OPM)·복수 투자라운드·"
    "청산우선권·IPO 확률(PWERM), 당기손익-공정가치 지정 금융부채의 자기신용위험 "
    "변동분 분리(제1109호 문단 5.7.7). 상장 시점과 공모가액은 확률분포가 아니라 "
    "가정으로 넣는다.")

# 복합계약 전체를 당기손익-공정가치로 지정했을 때 상각표 자리에 남기는 글.
# 값 조서와 수식 조서가 같은 문안을 쓴다 — 두 조서가 다른 말을 하면 안 된다.
FVPL_NOTE = (
    "복합계약 전체를 당기손익-공정가치 측정 금융부채로 지정했으므로 "
    "유효이자율 상각표를 만들지 않는다.",
    "상각표는 「상각후원가로 측정하는 주계약」이 있어야 성립한다. 전체를 "
    "공정가치로 재면 그 주계약이 없다 — 내재파생을 분리하지 않아(제1109호 문단 "
    "4.3.3(3)) 배분표가 한 줄이고, 후속측정은 상각이 아니라 전체를 매 결산 "
    "공정가치로 다시 재는 것이다.",
    "이자비용도 유효이자율로 굴린 금액이 아니라 공정가치 변동에 녹아 든다. "
    "표면이자를 따로 표시한다면 계약상 지급액을 그대로 쓴다.",
    "거래원가는 얹을 자리가 없어 전액 즉시 비용이다 (문단 5.1.1). 「배분표」 "
    "시트의 거래원가 표가 그렇게 되어 있다.",
    "자기신용위험 변동분은 기타포괄손익으로 표시해야 한다 (문단 5.7.7). "
    "이 조서는 그 분해를 하지 않으므로 직접 나누어야 한다.",
    "매도청구권이 제3자에게 지정·양도될 수 있으면 복합계약의 일부가 아니라 "
    "별도의 금융상품이라(문단 4.3.1) 이 지정 밖에 남고, 파생상품자산으로 따로 "
    "인식한다. 「배분표」 시트에 그 줄이 있다.")


def k_cap(tm: Terms) -> float:
    """상향 재조정의 상한 — **최초** 전환가액이다.

    계약은 「조정 후 전환가액은 최초 전환가액을 초과할 수 없다」고 정한다. 이미
    하향 조정된 상품을 결산 평가하면 현재 전환가액(``K0``)과 이 상한이 갈린다.
    비워 두면(음수) 최초 인식 평가로 보아 ``K0`` 를 그대로 쓴다.
    """
    return tm.K_cap if tm.K_cap > 0 else tm.K0


def lbl(tm: Terms) -> dict:
    """상품에 따라 갈리는 이름. 화면·조서가 모두 여기서 가져간다."""
    if is_rcps(tm):
        # 제3자 지정 콜은 계약서에서도 「매도청구권」이라 부른다. 발행자 상환권과
        # 성격이 다르므로 이름을 바꾸지 않는다.
        _th = int(tm.issuer_call) == 2
        return dict(inst="상환전환우선주", short="RCPS", face="발행가", cpn="우선배당률",
                    ipay="배당 지급주기", put="상환청구권",
                    call=("매도청구권" if _th else "발행자 상환권"),
                    host="우선주부채 (옵션 없는 부채)", liab="부채요소 (우선주 + 상환청구권)",
                    red="존속기간 만료 시 상환금액", ytm="만료 시 상환 보장수익률",
                    bond="상환전환우선주부채",
                    callamt=("매도청구금액" if _th else "발행자 상환가액"),
                    unit="1주 발행가 100 기준")
    if is_sha(tm):
        return dict(inst="주주간계약", short="SHA", face="투자원금",
                    cpn="—", ipay="—", put="투자자 풋옵션", call="최대주주 콜옵션",
                    host="—", liab="—", red="—", ytm="풋 보장수익률",
                    bond="주주간계약", callamt="콜 행사금액",
                    unit="투자원금 100 기준")
    if is_bw(tm):
        # 신주인수권부사채. 사채는 CB 와 같고 갈리는 것은 지분요소의 이름과
        # 행사대금 납입 방식이다. 대용납입이면 사채가 소멸해 전환사채와 같고,
        # 현금납입이면 사채가 남아 사채와 신주인수권을 따로 재어 더한다.
        return dict(inst="신주인수권부사채", short="BW", face="액면", cpn="표면이자율",
                    ipay="이자 지급주기", put="조기상환청구권", call="매도청구권",
                    host="주계약 (옵션 없는 사채)", liab="부채요소 (사채 + 조기상환권)",
                    red="만기상환금액", ytm="만기보장수익률", bond="신주인수권부사채",
                    callamt="매도청구금액", unit="전자등록금액 100 기준")
    return dict(inst="전환사채", short="CB", face="액면", cpn="표면이자율",
                ipay="이자 지급주기", put="조기상환청구권", call="매도청구권",
                host="주계약 (옵션 없는 사채)", liab="부채요소 (사채 + 조기상환권)",
                red="만기상환금액", ytm="만기보장수익률", bond="전환사채",
                callamt="매도청구금액", unit="전자등록금액 100 기준")


# ══════════════════════════════════════════════════════════
# 2. 이자율 곡선
# ══════════════════════════════════════════════════════════
def _lin(pts, t):
    if not pts: return None
    if t <= pts[0][0]: return pts[0][1]
    if t >= pts[-1][0]: return pts[-1][1]
    for i in range(1, len(pts)):
        if t <= pts[i][0]:
            (x0, y0), (x1, y1) = pts[i-1], pts[i]
            return y0 + (y1-y0)*(t-x0)/(x1-x0)


def bootstrap_df(par_pts, Tmax, m=1):
    """만기수익률 곡선 → 할인계수.

    par_pts 는 [(만기, 연 만기수익률)] 이고 m 은 연간 이표 횟수다.
    선형보간으로 이표 시점마다 수익률을 만든 뒤 앞에서부터 순차로 푼다.
        1 = c·(DF1 + … + DFk) + DFk        c = 해당 만기 수익률 ÷ m
    """
    N = max(1, int(math.ceil(Tmax*m)))
    out, acc = [(0.0, 1.0)], 0.0
    for k in range(1, N+1):
        t = k/m
        c = _lin(par_pts, t)/m
        df = (1 - c*acc)/(1 + c)
        acc += df
        out.append((t, df))
    return out


def make_curve(par_pts, Tmax, m=1):
    """할인계수에서 연속복리 현물이자율 함수를 만든다."""
    dfs = bootstrap_df(par_pts, Tmax, m)
    spot = [(t, -math.log(df)/t) for t, df in dfs if t > 0]
    if not spot: return lambda t: 0.0
    spot = [(1e-6, spot[0][1])] + spot
    return lambda t: spot[0][1] if t <= 0 else _lin(spot, t)


def spot_from_zero(pts, m=1):
    """이미 현물이자율(이산)로 받은 경우 — 연속복리로 바꾼다.

    m 은 그 현물이자율의 복리 횟수다. 국고채는 연복리로 고시되는 경우가 많지만
    회사채 제로커브는 분기복리인 경우가 있다. 복리 횟수를 무시하고 ln(1+r) 로만
    환산하면 할인계수와 위험중립확률이 어긋난다 (책 3.7.4.4 오류 사례).

        연속 = m · ln(1 + r/m)        m=1 이면 ln(1+r) 로 되돌아온다
    """
    m = max(1, int(m))
    cont = [(t, m*math.log(1 + r/m)) for t, r in pts]
    return lambda t: cont[0][1] if t <= 0 else _lin(cont, t)


def forward_rate(F, t0, t1):
    """구간 선도이자율.  f = [r(t1)·t1 − r(t0)·t0] ÷ (t1 − t0)"""
    return (F(t1)*t1 - F(t0)*t0)/(t1-t0)


RATINGS = ["AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-",
           "BBB+", "BBB0", "BBB-", "BB+", "BB0", "BB-",
           "B+", "B0", "B-", "CCC+", "CCC0", "CCC-", "CC", "C", "D"]


def rating_idx(r: str) -> int:
    """등급을 노치 번호로. AAA=0 에서 한 단계씩 내려간다."""
    r = (r or "").strip().upper().replace(" ", "")
    if r in RATINGS: return RATINGS.index(r)
    for alt in (r+"0", r.replace("0", "")):
        if alt in RATINGS: return RATINGS.index(alt)
    return -1


# 긴 등급부터 맞춰야 'BBB-' 를 'BB' 로 잘못 집지 않는다. 앞뒤로 다른 알파벳이
# 붙으면 등급이 아니다 — 그래야 'CD(91일)' 을 등급 C 로 집지 않는다.
_RATING_PAT = re.compile(
    r"(?<![A-Z])(?:" + "|".join(sorted((re.escape(r) for r in RATINGS),
                                       key=len, reverse=True)) + r")(?![A-Z])")


def rating_in(text) -> str:
    """문자열 안에서 신용등급을 찾는다. 못 찾으면 None.

    고시표의 줄 이름은 '회사채 I(공모사채) / 무보증 / BBB0' 처럼 등급이 뒤에
    붙는다. 그 줄이 어느 등급인지 알아야 화면에서 **표에 실제로 있는 등급만**
    고르게 할 수 있다.
    """
    t = (text or "").upper().replace(" ", "")
    m = _RATING_PAT.search(t)
    if m: return m.group(0)
    # 'AA' · 'BBB' 처럼 0 을 안 붙인 표기. 뒤에 숫자가 붙으면 'CP(A2)' 처럼
    # 다른 척도의 기호이므로 등급으로 보지 않는다.
    m = re.search(r"(?<![A-Z])(AAA|AA|A|BBB|BB|B|CCC|CC|C|D)(?![A-Z0-9+\-])", t)
    if m:
        c = m.group(1)
        return c if c in RATINGS else (c+"0" if c+"0" in RATINGS else None)
    return None


def blend_curves(pts_a, pts_b, ra, rb, rt):
    """두 등급 곡선을 노치 거리로 선형보간해 대상 등급 곡선을 만든다.

    대상 등급이 두 등급 사이면 내삽, 밖이면 같은 기울기로 외삽한다.
    """
    ia, ib, it = rating_idx(ra), rating_idx(rb), rating_idx(rt)
    if not pts_a: return pts_b or []
    if not pts_b or ia < 0 or ib < 0 or it < 0 or ia == ib: return pts_a
    w = (it-ia)/(ib-ia)
    ts = sorted({t for t, _ in pts_a} | {t for t, _ in pts_b})
    out = []
    for t in ts:
        ya, yb = _lin(pts_a, t), _lin(pts_b, t)
        if ya is None or yb is None: continue
        out.append((t, ya + (yb-ya)*w))
    return out


def to_cont(r, m):
    """이산복리(연 m회) → 연속복리.  국고채 반기(2), 회사채 분기(4)가 관행이다."""
    return m*math.log(1 + r/m) if (m and m > 0) else r


def credit_curve(tm: Terms):
    """위험 곡선. 등급 보간 방식이면 두 등급 곡선을 섞는다."""
    if tm.rate_mode == "rating" and len(tm.cr_curve) >= 2 and len(tm.cr_curve_b) >= 2:
        return blend_curves(tm.cr_curve, tm.cr_curve_b, tm.rt_a, tm.rt_b, tm.rt_tgt)
    return tm.cr_curve


def lattice_ud(tm: Terms, dt_: float):
    """CRR 격자의 상승·하락계수. σ 가 0 이면 격자 자체가 서지 않는다.

    ``u = exp(σ√Δt)``, ``d = 1/u`` 이므로 σ = 0 이면 ``u = d = 1`` 이 되어
    위험중립가중치의 분모가 0 이 된다. 예전에는 그 자리에서 ZeroDivisionError
    가 났다 — 화면은 σ 를 막고 있었지만 엔진을 직접 부르면 그대로 터졌다.

    변동성이 없는 계약은 격자로 풀 이유가 없다. 주가가 확정이면 전환할지 말지도
    확정이라 현금흐름을 할인하면 끝이다. 그래서 여기서는 값을 지어내지 않고
    왜 안 되는지를 말한다.

    σ 가 0 은 아니지만 지나치게 작아도 격자가 드리프트를 담지 못한다.
    ``d < exp((r−δ)Δt) < u`` 여야 하는데 σ√Δt 가 |(r−δ)Δt| 보다 작으면 q 가
    0~1 을 벗어난다. 그쪽은 이미 ``qbad`` 가 전 구간을 재어 화면·조서에
    알리므로 여기서 막지 않는다 — 어느 구간이 어긋났는지가 더 쓸모 있다.
    """
    if not (tm.sig > 0) or tm.sig*math.sqrt(max(dt_, 0.0)) <= 1e-12:
        raise ValueError(
            "변동성 σ 가 0 이라 이항격자를 만들 수 없습니다 (u = d = 1). "
            "주가가 확정이면 전환 여부도 확정이므로 격자가 아니라 현금흐름 "
            "할인으로 재야 합니다. σ 를 0 보다 크게 넣으십시오.")
    u = math.exp(tm.sig*math.sqrt(dt_))
    return u, 1/u


def curves(tm: Terms):
    """무위험·위험 모두 만기수익률 곡선을 부트스트래핑해 쓴다."""
    cc = credit_curve(tm)
    if len(tm.rf_curve) >= 2 and len(cc) >= 2:
        if tm.y_type == "spot":
            # 현물이자율은 고시된 복리 횟수로 연속환산한다 (책 3.7.4.4)
            return (spot_from_zero(tm.rf_curve, tm.cmp_rf),
                    spot_from_zero(cc, tm.cmp_cr))
        return (make_curve(tm.rf_curve, tm.T, tm.cmp_rf),
                make_curve(cc, tm.T, tm.cmp_cr))
    # 곡선이 아직 없을 때의 임시값 — 화면이 경고를 띄운다
    return (lambda t: to_cont(0.028, tm.cmp_rf)), (lambda t: to_cont(0.07, tm.cmp_cr))


# ══════════════════════════════════════════════════════════
# 3. 격자 엔진
# ══════════════════════════════════════════════════════════
TOL = 1e-9      # 동점 판정 허용오차. 값이 100 근처라 1e-9 은 잡음보다 크고 실질 차이보다 작다


def engine(tm: Terms, conv=True, put=True, call=False, conv_start=None):
    RF, CR = curves(tm)
    n, T = int(tm.n), tm.T
    dt_ = T/n
    mper = n/(T*12)
    el = tm.elapsed_m                       # 발행일 → 평가기준일 경과 개월
    # 계약상 개월 → 노드 번호. 시작은 계약일 이후 첫 노드, 종료는 이전 마지막 노드.
    st_lo, st_hi = step_mapper(tm, n, dt_)
    ey = el/12                               # 경과 연수
    u, d = lattice_ud(tm, dt_)
    fwd = lambda F, i: (F((i+1)*dt_)*(i+1)*dt_ - F(i*dt_)*i*dt_)/dt_
    # 배당수익률은 위험중립 드리프트에서 빠진다. 배당은 주주에게 가고 전환 전
    # 투자자는 받지 못하므로, 같은 주가라도 전환권 가치가 그만큼 낮아진다.
    qi = lambda i: (math.exp((fwd(RF, i) - tm.div_y)*dt_) - d)/(u - d)
    # 위험중립가중치는 구간마다 선도이자율로 다시 계산된다. 첫 구간만 보고
    # 넘어가면 뒤쪽 곡선이 가파를 때 q 가 0~1 을 벗어난 채로 계산이 끝난다.
    # 여기서 전 구간을 미리 재어 화면·조서가 함께 보게 한다.
    qs = [qi(i) for i in range(n)]
    qbad = [(i, qs[i]) for i in range(n) if not (0.0 < qs[i] < 1.0)]
    cs = tm.cv_s if conv_start is None else conv_start

    def in_set(i, a, b, fr):
        lo, hi = st_lo(a), st_hi(b)
        if i < max(lo, 0) or i > hi: return False
        per = max(1, int(round(fr*mper)))
        return (i-lo) % per == 0
    rfx_per = max(1, int(round(tm.rfx_cyc*mper)))
    # 다음 조정일은 발행일 + (지난 회차 + 1) × 주기 다. 그 날 이후 첫 노드가 시작이다.
    rfx_off = (st_lo(tm.rfx_cyc*(math.floor(el/tm.rfx_cyc) + 1))
               if tm.rfx_cyc > 0 else 1)
    is_rfx = lambda i: (tm.rfx_mode > 0 and i > 0 and i >= rfx_off
                        and (i-rfx_off) % rfx_per == 0)
    pay_per = max(1, int(round(tm.ipay*mper)))
    pay_off = pay_offset(tm, st_lo)
    is_pay = lambda i: (eff_cpn(tm) > 0 and i > 0 and i >= pay_off
                        and (i-pay_off) % pay_per == 0)
    cpn_amt = 100*eff_cpn(tm)*tm.ipay/12
    red = 100*(1 + accrue_rate(T + ey, tm.ytm, eff_cpn(tm), tm.ytm_cmp))
    S = lambda i, j: tm.S0 * u**j * d**(i-j)
    kcap = k_cap(tm)
    clip = lambda s: min(max(s, tm.floor, tm.par), kcap)
    def put_amt(i):
        """행사금액은 발행일부터 붙는다. 경과분을 더해 계산한다."""
        if tm.p_mode == "accrue":
            return 100*(1 + accrue_rate(i*dt_ + ey, tm.p_yield, eff_cpn(tm), tm.p_cmp))
        return tm.p_rate
    put_a = lambda i: put_amt(i) if (put and in_set(i, tm.p_s, tm.p_e, tm.p_f)) else 0.0
    # kstrike 는 콜 스위치와 무관한 행사금액이다. 행사기간이 아니면 None.
    # call_a 는 call=False 면 항상 inf 라 제3자 콜옵션 평가에 쓸 수 없다.
    kstrike = lambda i: (100*(1 + accrue_rate(i*dt_ + ey, tm.k_prem, eff_cpn(tm),
                                             tm.k_cmp))
                         if in_set(i, tm.k_s, tm.k_e, tm.k_f) else None)
    call_a = lambda i: (kstrike(i) if (call and in_set(i, tm.k_s, tm.k_e, tm.k_f))
                        else math.inf)
    conv_ok = lambda i: conv and st_lo(cs) <= i <= st_hi(tm.cv_e)
    # ── BW 현금납입 ──
    # 신주인수권을 현금으로 행사하면 사채가 그대로 남는다. 그래서 「사채를 내주고
    # 주식을 받는」 전환 갈래가 없다. 대신 지분요소가 신주인수권 하나가 되어
    #     신주인수권 행사가치 = 100 × 주가/행사가격 − 100 = 전환가치 − 100
    # 이 되고 (권면액 100 만큼 현금을 내고 그 값어치 주식을 받는다), 부채요소는
    # 전환권 없는 사채가 조기상환청구권·매도청구권만 달고 남는다.
    # 대용납입이면 사채가 권면액만큼 소멸하므로 전환사채와 완전히 같아 이 갈래를
    # 타지 않는다. 부채요소만 재는 격자(conv=False)도 마찬가지다.
    bwc = bw_cash(tm) and conv
    bwd = bwc and int(tm.bw_detach) == 1     # 분리형 — 사채가 소멸해도 남는다
    # ── IPO (책 [사례 5-5]) ──
    # 상장은 특정 스텝에서 조건부로 전환가격을 자르는 사건이다. 상장 성공 여부는
    # 그 노드의 주가로 판정한다 — 최소공모가격에 못 미치면 상장 자체가 무산되므로
    # 조정도 없다. 강제전환은 만기 자동전환과 같은 규칙으로 전환권이 있는 격자에서만
    # 탄다 (전환권을 뺀 부채는 주식이 될 수 없다).
    ipo_i = (st_lo(tm.ipo_m) if (int(tm.ipo_on) and tm.ipo_px > 0) else -1)
    ipo_k = tm.ipo_px*tm.ipo_mult
    ipo_hit = lambda i, j: (i == ipo_i and 0 < i <= n and S(i, j) > tm.ipo_min)
    ipo_adj = lambda i, j, k: (clip(min(k, ipo_k)) if ipo_hit(i, j) else k)
    exact = ((tm.rfx_mode > 0 or ipo_i > 0) and tm.carry == 0)

    def child_k(i, K, s):
        """스텝 i 의 전환가격 K 에서 주가 s 인 자식(스텝 i+1)의 전환가격.

        상태확장(exact) 격자의 노드 열쇠가 이 값으로 만들어지므로, 격자를 세우는
        rec() 와 뒤에서 확률을 걷는 두 루프가 **반드시 같은 함수**를 써야 한다.
        한쪽만 상장 조정을 빠뜨리면 열쇠가 어긋나 그 가지의 확률이 조용히 사라진다.
        """
        if tm.rfx_mode == 0: k2 = K
        elif not is_rfx(i+1): k2 = K
        else: k2 = clip(s) if tm.rfx_mode == 2 else clip(min(K, s))
        # 자식이 상장 스텝이고 그 주가가 최소공모가격을 넘으면 자른다.
        if i+1 == ipo_i and s > tm.ipo_min and ipo_k > 0:
            k2 = clip(min(k2, ipo_k))
        return k2

    # 도달확률. 위험중립가중치 q 가 구간마다 다르므로 이항계수 한 방에 셀 수
    # 없다. COMBIN(i,j)·q^j·(1−q)^(i−j) 는 q 가 모든 구간에서 같을 때만 맞고,
    # 그러면 정규화된 이월 가중치에서 q 가 통째로 약분돼 「확률가중」이 아니라
    # 「경로 수 가중」이 된다. 앞에서부터 한 칸씩 쌓아야 한다.
    #     P(i,j) = P(i−1,j−1)·q(i−1) + P(i−1,j)·(1−q(i−1))
    # 평탄한 곡선이면 이항식과 정확히 같은 값이 나온다 (책 예제가 그렇다).
    Preach = [[1.0]]
    for i in range(1, n+1):
        q_ = qi(i-1)
        Preach.append([(Preach[i-1][j-1]*q_ if j-1 >= 0 else 0.0)
                       + (Preach[i-1][j]*(1-q_) if j <= i-1 else 0.0)
                       for j in range(i+1)])

    Kg = None
    if not exact:
        Kg = [[tm.K0]]
        for i in range(1, n+1):
            q = qi(i-1)
            row = []
            for j in range(i+1):
                # 선행 전환가액을 고르는 방법은 **조정일이든 아니든 같아야 한다.**
                # 비조정일만 두 선행값을 가중평균하고 조정일에는 하나만 집으면
                # 같은 격자 안에서 처리가 갈려, 하향만 조정에서 전환가액이 높게
                # 잡히고 값이 낮아진다. 먼저 이월값을 정한 뒤 조정을 얹는다.
                up = Kg[i-1][j-1] if j-1 >= 0 else None
                dn = Kg[i-1][j] if j <= i-1 else None
                if up is None: prev = dn
                elif dn is None: prev = up
                elif tm.carry == 3: prev = dn
                elif tm.carry == 2: prev = up*q + dn*(1-q)
                else:
                    wu, wd = Preach[i-1][j-1]*q, Preach[i-1][j]*(1-q)
                    prev = (up*wu + dn*wd)/(wu+wd) if wu+wd > 0 else dn
                if tm.rfx_mode == 0:
                    kv = prev                        # 주기 조정이 없으면 그대로 이월
                elif is_rfx(i):
                    kv = clip(S(i, j) if tm.rfx_mode == 2 else min(prev, S(i, j)))
                else:
                    kv = prev
                # 상장하면 공모가 × 배수로 자른다. 낮아질 때만 조정된다.
                row.append(ipo_adj(i, j, kv))
            Kg.append(row)

    memo = {}
    def rec(i, j, K):
        key = (i, j, round(K, 6)) if exact else (i, j)
        if key in memo: return memo[key]
        if i == n:
            KK = K if exact else Kg[n][j]
            if bwc:
                # 사채는 만기상환(또는 그날 열려 있는 조기상환)으로 끝나고,
                # 신주인수권은 내가격이면 행사한다. 둘은 서로를 막지 않는다.
                cv = 100*S(n, j)/KK if conv_ok(n) else 0.0
                wv = max(cv - 100, 0.0) if conv_ok(n) else 0.0
                cm = cpn_amt if is_pay(n) else 0.0
                pv = put_a(n)
                cash = max(pv, red) + cm
                o = dict(E=wv, B=cash, V=wv+cash, P=0.0, wx=1.0 if wv > 0 else 0.0,
                         kind=("put" if pv > red + TOL else "mat"),
                         hold=red, cv=cv, K=KK)
                memo[key] = o
                return o
            if conv and auto_conv(tm):
                # 존속기간이 끝나면 보통주가 된다. 전환기간 밖이어도, 내가격이
                # 아니어도 그렇다 — 상법이 우선주의 존속기간 만료를 그렇게 정해
                # 두었다. 상환은 상환청구기간 안에서만 일어나므로 만기에는
                # 현금 갈래가 없다. 받는 것은 주식이라 그날 배당은 없다.
                # 전환권을 뺀 부채 격자(conv=False)는 이 갈래를 타지 않는다 —
                # 전환권이 없는 부채는 보통주가 될 수 없으니 상환가액으로 끝난다.
                # 그래서 부채요소는 「상환받는다」는 전제로 재고, 자동전환은
                # 전환권의 한 갈래로 전체 가치에만 들어간다.
                cv = 100*S(n, j)/KK
                # 상환청구기간이 만기까지 열려 있으면 주식 대신 상환을 고를 수 있다.
                # 동점 처리는 아래 일반 만기 노드와 같다 — 주식은 TOL 만큼 앞설 때만.
                pv = put_a(n)
                cash = (pv + (cpn_amt if is_pay(n) else 0.0)) if pv > 0 else 0.0
                if cv >= cash + TOL:
                    o = dict(E=cv, B=0.0, V=cv, P=1.0, kind="auto", hold=cv, cv=cv, K=KK)
                else:
                    o = dict(E=0.0, B=cash, V=cash, P=0.0, kind="put", hold=cv, cv=cv, K=KK)
                memo[key] = o
                return o
            cv = 100*S(n, j)/KK if conv_ok(n) else 0.0
            pv = put_a(n)
            # 만기에도 이자 지급일이면 이자를 함께 받는다 (책 5-7 만기 현금흐름).
            # 전환을 택하면 주식을 받으므로 중간 노드와 같이 이자는 사라진다.
            cm = cpn_amt if is_pay(n) else 0.0
            cash = max(pv, red) + cm
            # 리픽싱이 주가로 재설정되는 날에는 전환가치가 정확히 100 이 되어
            # 상환금액과 동점이 된다. 부동소수 잡음으로 갈리지 않게 전환은
            # TOL 만큼 앞설 때만 이긴다. 동점이면 현금(부채)이다.
            if cv >= max(cash, 0.0) + TOL and cv > 0:
                o = dict(E=cv, B=0.0, V=cv, P=1.0, kind="conv", hold=red, cv=cv, K=KK)
            else:
                o = dict(E=0.0, B=cash, V=cash, P=0.0, kind="mat",
                         hold=red, cv=cv, K=KK)
        elif conv and int(tm.ipo_conv) and ipo_hit(i, j):
            # 상장하면 보통주가 된다. 그 자리에서 주식으로 끝난다 — 상환청구권도
            # 발행자 상환권도 함께 사라진다. 받는 것은 주식이라 그날 배당은 없다.
            KK = K if exact else Kg[i][j]
            cv = 100*S(i, j)/KK
            o = dict(E=cv, B=0.0, V=cv, P=1.0, kind="ipo", hold=cv, cv=cv, K=KK)
            memo[key] = o
            return o
        else:
            KU = child_k(i, K, S(i, j)*u) if exact else Kg[i+1][j+1]
            KD = child_k(i, K, S(i, j)*d) if exact else Kg[i+1][j]
            ku = (i+1, j+1, round(KU, 6)) if exact else (i+1, j+1)
            kd = (i+1, j,   round(KD, 6)) if exact else (i+1, j)
            a, b = rec(i+1, j+1, KU), rec(i+1, j, KD)
            q = qi(i); c = cpn_amt if is_pay(i) else 0.0
            fr, fc = fwd(RF, i), fwd(CR, i)
            E = (q*a["E"] + (1-q)*b["E"]) * math.exp(-fr*dt_)
            B = (q*a["B"] + (1-q)*b["B"]) * math.exp(-fc*dt_) + c
            # GS — 자식 노드의 전환확률로 각각 할인한다 (순환참조가 생기지 않는다)
            ya = a["P"]*fr + (1-a["P"])*fc
            yb = b["P"]*fr + (1-b["P"])*fc
            Vc = q*a["V"]*math.exp(-ya*dt_) + (1-q)*b["V"]*math.exp(-yb*dt_) + c
            pr = q*a["P"] + (1-q)*b["P"]
            KK = K if exact else Kg[i][j]
            cv = 100*S(i, j)/KK if conv_ok(i) else 0.0
            pv, kv = put_a(i), call_a(i)
            if bwc:
                # 신주인수권은 행사해도 사채가 남으므로 사채 결정과 별개다.
                # 미국형이라 「지금 행사」와 「계속 보유」 중 큰 쪽을 고른다.
                wv = max(cv - 100, 0.0) if conv_ok(i) else 0.0
                En = max(E, wv)
                _wx = 1.0 if (wv > 0 and wv >= E - TOL) else 0.0
                # forced 는 «매도청구를 당해 전환으로 대응했는가» 다. 신주인수권부
                # 사채의 사채 결정에는 전환이 들어가지 않아 늘 거짓이다.
                ex = dict(hold=E+B, cv=cv, K=KK, pv=pv, kv=kv, Vc=E+B,
                          up=ku, dn=kd, wv=wv, forced=False)
                _kf = int(tm.pc_order) == 1
                if bwd:
                    # 분리형 — 신주인수권증권이 따로 유통되므로 사채를 상환받아도
                    # 남는다. 두 결정이 서로를 건드리지 않으므로 **사채만** 넘긴다.
                    # 전환은 이 결정에 들어가지 않아 -inf 다.
                    _kd = node_decide(-math.inf, pv, kv, B, _kf)
                    _bv = {"put": pv, "call": kv, "hold": B}[_kd]
                    o = dict(E=En, B=_bv, V=En+_bv, P=0.0, kind=_kd, wx=_wx, **ex)
                else:
                    # 비분리형 — 사채가 소멸하면 미행사 신주인수권도 소멸한다.
                    # 다만 **상환 직전에 행사할 기회는 남아 있다** (통지기간).
                    # 그 읽기는 조기상환과 매도청구에 똑같이 적용해야 한다.
                    # 한쪽만 인정하면 같은 격자가 두 계약을 읽는 셈이 된다.
                    #
                    # 매도청구도 마찬가지다. 발행자가 사채를 매수해 가면 투자자는
                    # 그 직전에 신주인수권을 행사해 wv 를 챙기므로 실제로 받는 값은
                    # kv 가 아니라 kv + wv 다. 그러면 **콜 행사 판단 자체도** 그
                    # 금액과 견줘야 한다 — 발행자는 투자자 가치를 눌러 내리는
                    # 쪽으로만 콜하기 때문이다. wv 가 0 이면 예전 식과 같아진다.
                    #
                    # 사채와 신주인수권을 **합쳐서** 견주므로 세 후보 모두 신주인수권
                    # 몫을 얹어 넘긴다. 보유만 En(행사와 계속보유 중 큰 쪽)이고
                    # 상환 두 갈래는 wv(그 자리 행사가치)다 — 사채가 소멸하는
                    # 순간에는 계속보유라는 선택지가 없기 때문이다.
                    holdT, putT, callT = B + En, pv + wv, kv + wv
                    _cx = 1.0 if wv > 0 else 0.0
                    _kd = node_decide(-math.inf, putT, callT, holdT, _kf)
                    if _kd == "hold":
                        o = dict(E=En, B=B, V=holdT, P=0.0, kind="hold", wx=_wx, **ex)
                    else:
                        _bv, _vt = ((pv, putT) if _kd == "put" else (kv, callT))
                        o = dict(E=wv, B=_bv, V=_vt, P=0.0, kind=_kd, wx=_cx, **ex)
                memo[key] = o
                return o
            hold = E + B
            # 풋과 콜이 **같은 노드에서 함께 열릴 때** 누가 먼저 움직이는지는
            # 계약이 정한다 (pc_order). 조기상환금액과 매도청구금액이 다르고
            # 행사기간이 겹치는 자리에서만 값이 갈린다.
            #   투자자 풋 우선 :  MAX(전환, 풋, MIN(보유, 콜))
            #   발행자 콜 우선 :  MAX(전환, MIN(MAX(보유, 풋), 콜))
            # GS 도 같은 우선순위를 따라야 한다 — 한 격자에서 두 모형이 다른
            # 계약을 읽으면 안 된다.
            _kfirst = int(tm.pc_order) == 1
            Vg = (max(cv, min(max(Vc, pv), kv)) if _kfirst
                  else max(cv, pv, min(Vc, kv)))
            # GS 의 전환확률은 GS 자신의 판단을 따른다. TF 와 다른 갈래를 고를 수 있다.
            # GS 도 같은 순서다. 현금이 동점이면 전환확률 0 이다.
            if abs(Vg - pv) < TOL or (kv < math.inf and abs(Vg - kv) < TOL): Pg = 0.0
            elif cv > 0 and abs(Vg - cv) < TOL:                              Pg = 1.0
            else:                                                            Pg = pr
            # up·dn 은 자식 노드 키다. 만기 노드에는 없어 자식 없음의 표시가 된다.
            ex = dict(hold=hold, cv=cv, K=KK, pv=pv, kv=kv, Vc=Vc, up=ku, dn=kd,
                      forced=False)
            # 동점 처리는 위 만기 노드와 같다. 전환은 TOL 만큼 앞설 때만 이긴다.
            # 평가기준일(i=0)도 예외가 아니다. 그날 행사할 수 있고 행사가 유리하면
            # 공정가치는 행사가치 이상이어야 한다 — 계속보유로 눌러 두면 값이
            # 과소계상되고, 같은 판단을 하는 GS·수식 조서와도 어긋난다. 그날
            # 행사할 수 없는 권리는 conv_ok·put_a·call_a 가 이미 막는다.
            _kd = node_decide(cv, pv, kv, hold, _kfirst)
            if   _kd == "conv": _e, _b = cv, 0.0
            elif _kd == "put":  _e, _b = 0.0, pv
            elif _kd == "call": _e, _b = 0.0, kv
            else:               _e, _b = E, B
            # 매도청구를 당해 전환으로 대응한 자리인지 함께 적어 둔다. 가치는
            # 자발적 전환과 같지만 정산 분포에서는 갈라 세야 한다.
            ex["forced"] = (_kd == "conv"
                            and node_decide(cv, pv, math.inf, hold, _kfirst) != "conv")
            o = dict(E=_e, B=_b, V=Vg, P=Pg, kind=_kd, **ex)
        memo[key] = o
        return o

    r0 = rec(0, 0, tm.K0)

    # 정산 유형 분포
    #
    # **만기 층까지 걷는다.** 예전에는 `range(n)` 이라 만기 층을 아예 방문하지 않고
    # 살아남은 확률을 통째로 `mat` 에 넣었다. 그러면 존속기간 만료 시 자동전환하는
    # 우선주에서 「만기에 주식이 된 몫」과 「만기에 상환받은 몫」이 한 덩어리가
    # 된다 — 상환청구기간이 만기까지 열린 계약에서는 만기 노드가 실제로 둘로
    # 갈린다. 이제 만기 노드도 자기 kind 로 흡수되고, 남는 `mat` 은 「만기까지
    # 살아남아 현금으로 끝난 확률」만 담는다.
    #
    # `conv_called` 는 그 전환 중 **매도청구를 당해 전환으로 대응한** 몫이다.
    # `conv` 에서 빼지 않고 겹쳐 센다 — 「전환 중 그만큼」이라는 뜻이라 합계가
    # 그대로 1 이다.
    dist = dict(conv=0.0, put=0.0, call=0.0, mat=0.0, tc=0.0, tp=0.0, tk=0.0,
                conv_called=0.0, tcc=0.0)
    layer = {((0, 0, round(tm.K0, 6)) if exact else (0, 0)): (1.0, tm.K0, 0)}
    for i in range(n+1):
        nxt, q = {}, (qi(i) if i < n else 0.0)
        for key, (p_, K, j) in layer.items():
            o = memo.get(key)
            if o is None: continue
            # 평가기준일에 즉시 행사되면 그 유형이 100% 다. i>0 예외를 두면
            # 루트 행사가 정산 분포에서 사라진다.
            if o["kind"] != "hold":
                if o["kind"] in ("conv", "auto", "ipo"):
                    dist["conv"] += p_; dist["tc"] += p_*i
                    if o.get("forced"):
                        dist["conv_called"] += p_; dist["tcc"] += p_*i
                elif o["kind"] == "put": dist["put"] += p_; dist["tp"] += p_*i
                elif o["kind"] == "call": dist["call"] += p_; dist["tk"] += p_*i
                elif o["kind"] == "mat": dist["mat"] += p_
                continue
            if i == n:
                # 만기에 «보유» 는 없다. 여기 오면 kind 배선이 빠진 것이다.
                dist["mat"] += p_; continue
            KU = child_k(i, K, S(i, j)*u) if exact else Kg[i+1][j+1]
            KD = child_k(i, K, S(i, j)*d) if exact else Kg[i+1][j]
            for kk, pp, jj, KK in (
                ((i+1, j+1, round(KU, 6)) if exact else (i+1, j+1), p_*q, j+1, KU),
                ((i+1, j, round(KD, 6)) if exact else (i+1, j), p_*(1-q), j, KD)):
                if kk in nxt:
                    a0, b0, c0 = nxt[kk]; nxt[kk] = (a0+pp, b0, c0)
                else:
                    nxt[kk] = (pp, KK, jj)
        layer = nxt

    # 신주인수권 행사확률. 사채 정산 분포와 걷는 길이 다르다 — 분리형이면 사채를
    # 상환받아도 신주인수권이 남고, 비분리형이면 사채가 소멸할 때 함께 소멸한다.
    # 그래서 한 번 더 걷는다. 사채 쪽 분포(dist)는 그대로 둔다.
    if bwc:
        wp = wt = 0.0
        lay = {((0, 0, round(tm.K0, 6)) if exact else (0, 0)): (1.0, tm.K0, 0)}
        for i in range(n):
            nxt, q = {}, qi(i)
            for key, (p_, K, j) in lay.items():
                o = memo.get(key)
                if o is None: continue
                if i > 0:
                    if o.get("wx", 0.0) > 0:
                        wp += p_; wt += p_*i; continue
                    if (not bwd) and o["kind"] in ("put", "call"):
                        continue          # 사채와 함께 소멸한다 — 행사하지 못한다
                KU = child_k(i, K, S(i, j)*u) if exact else Kg[i+1][j+1]
                KD = child_k(i, K, S(i, j)*d) if exact else Kg[i+1][j]
                for kk, pp, jj, KK in (
                    ((i+1, j+1, round(KU, 6)) if exact else (i+1, j+1), p_*q, j+1, KU),
                    ((i+1, j, round(KD, 6)) if exact else (i+1, j), p_*(1-q), j, KD)):
                    if kk in nxt:
                        a0, b0, c0 = nxt[kk]; nxt[kk] = (a0+pp, b0, c0)
                    else:
                        nxt[kk] = (pp, KK, jj)
            lay = nxt
        for key, (p_, K, j) in lay.items():
            o = memo.get(key)
            if o is not None and o.get("wx", 0.0) > 0:
                wp += p_; wt += p_*n
        dist["wex"], dist["tw"] = wp, wt

    root = (0, 0, round(tm.K0, 6)) if exact else (0, 0)
    return dict(TF=r0["E"]+r0["B"], E=r0["E"], B=r0["B"], GS=r0["V"], P=r0["P"],
                q=qi(0), qs=qs, qbad=qbad, qmin=min(qs), qmax=max(qs),
                u=u, d=d, dt=dt_, mper=mper, n=n, memo=memo, Kg=Kg,
                exact=exact, S=S, host=100*math.exp(-CR(T)*T), dist=dist,
                root=root, qi=qi, fwdRF=lambda i: fwd(RF, i),
                fwdCR=lambda i: fwd(CR, i), kstrike=kstrike)


def pick(res, model): return res["GS"] if model == "GS" else res["TF"]


# ══════════════════════════════════════════════════════════
# 3-B. 주주간계약 — 지분에 붙은 풋과 콜
# ══════════════════════════════════════════════════════════
def sha_engine(tm: Terms):
    """주주간계약을 지분가치 격자에서 잰다.

    사채가 없다. 투자자가 **이미 가진 지분**에 풋(투자자가 최대주주·발행회사에
    되팔 권리)과 콜(최대주주가 되사 갈 권리)이 붙어 있을 뿐이다. 그래서
    「B0 → B1 → B2」 같은 순차 차감이 아니라 두 옵션을 **따로** 잰다.

    보유자가 서로 다르기 때문이다 — 풋은 투자자가, 콜은 최대주주가 고른다.
    한 격자에서 함께 최적화하면 두 사람이 한 사람인 것처럼 재게 된다. 실무에서
    풋과 콜을 각각 평가해 각자의 재무제표에 총액으로 싣는 것도 같은 이유다.

    금액 기준은 **투자원금 100** 이다.

        지분가치(t) = 100 × 주가(t) ÷ 주당 인수가액
        풋 행사가치 = MAX(풋 행사금액 − 지분가치, 0)
        콜 행사가치 = MAX(지분가치 − 콜 행사금액, 0)

    풋 행사금액은 투자원금에 보장수익률을 붙인 값이라 사채의 조기상환금액과
    같은 산식(``accrue_rate``)을 쓴다.

    적격상장(Q-IPO)이 이루어지면 투자자가 시장에서 팔 수 있으므로 풋이
    소멸한다. 그 노드의 주가가 최소 기준을 넘는지로 성공을 판정한다 — CB·RCPS
    의 IPO 조항과 같은 기계다. 콜도 함께 소멸하는지는 계약마다 다르므로
    ``sha_qipo_kill`` 로 고른다.

    할인율이 갈린다. 풋은 **현금을 받을 권리**라 의무자의 신용위험이 붙고,
    콜은 **주식을 받을 권리**라 인도 위험이 사실상 없어 무위험으로 재는 것이
    보통이다. 풋 쪽만 세 갈래로 고르게 했다.
    """
    derive(tm)
    RF, CR = curves(tm)
    n, T = int(tm.n), tm.T
    dt_ = T/n
    mper = n/(T*12)
    ey = tm.elapsed_m/12                     # 발행일 → 평가기준일 경과 연수
    st_lo, st_hi = step_mapper(tm, n, dt_)
    u, d = lattice_ud(tm, dt_)
    fwd = lambda F, i: (F((i+1)*dt_)*(i+1)*dt_ - F(i*dt_)*i*dt_)/dt_
    # 지분가치 격자도 사채 격자와 같은 드리프트를 쓴다.
    qi = lambda i: (math.exp((fwd(RF, i) - tm.div_y)*dt_) - d)/(u - d)
    # 위험중립가중치는 구간마다 다시 계산된다. 첫 구간만 보면 뒤쪽 곡선이
    # 가파를 때 q 가 0~1 을 벗어난 채로 계산이 끝난다 — 사채 격자와 같다.
    qs = [qi(i) for i in range(n)]
    qbad = [(i, qs[i]) for i in range(n) if not (0.0 < qs[i] < 1.0)]
    rf = lambda i: fwd(RF, i)
    if int(tm.sha_disc) == 0:   pdisc = rf
    elif int(tm.sha_disc) == 2: pdisc = lambda i: fwd(RF, i) + tm.sha_spread
    else:                       pdisc = lambda i: fwd(CR, i)

    def in_set(i, a, b, fr):
        lo, hi = st_lo(a), st_hi(b)
        if i < max(lo, 0) or i > hi: return False
        per = max(1, int(round(fr*mper)))
        return (i-lo) % per == 0

    S = lambda i, j: tm.S0 * u**j * d**(i-j)
    eq = lambda i, j: 100*S(i, j)/tm.K0                      # 지분가치
    pk = lambda i: 100*(1 + accrue_rate(i*dt_ + ey, tm.sha_put_yield, 0.0,
                                        tm.sha_put_cmp))
    ck = lambda i: 100*(1 + accrue_rate(i*dt_ + ey, tm.sha_call_prem, 0.0,
                                        tm.sha_call_cmp))
    p_on = lambda i: in_set(i, tm.sha_put_s, tm.sha_put_e, tm.sha_put_f)
    # 종료가 0 이하면 콜이 없는 계약이다. 한 시점만 열리는 계약(시작 = 종료)은
    # 그대로 살린다.
    c_on = lambda i: (tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
                      and in_set(i, tm.sha_call_s, tm.sha_call_e, tm.sha_call_f))
    # 적격상장 — 그 스텝의 주가가 최소 기준을 넘으면 성공이다.
    qi_step = st_lo(tm.ipo_m) if (int(tm.ipo_on) and tm.ipo_m > 0) else -1
    qipo = lambda i, j: (i == qi_step and 0 < i <= n and S(i, j) > tm.ipo_min)

    # 뒤에서부터 한 열씩. 두 격자를 나란히 굴린다.
    P = [[0.0]*(n+1) for _ in range(n+1)]    # 투자자 풋
    C = [[0.0]*(n+1) for _ in range(n+1)]    # 최대주주 콜
    # 한쪽 행사가 다른 쪽을 소멸시키는 계약인가. 그렇다면 두 권리는 경제적으로
    # 독립이 아니다 — 따로 재면 「풋은 콜이 살아 있다고 보고, 콜은 풋이 살아
    # 있다고 보는」 공존할 수 없는 두 미래를 각각 값에 넣게 된다.
    #
    # 다행히 두 행사구역은 상태공간에서 대체로 겹치지 않는다. 투자자는 지분가치가
    # **낮을 때** 풋을 행사하고 최대주주는 **높을 때** 콜을 행사한다. 그래서 한
    # 번의 후진 계산으로 풀린다 — 각 노드에서 누가 행사하는지 정하고, 행사가
    # 일어나면 상대방 격자를 그 자리에서 0 으로 흡수한다. 두 할인율(풋은 의무자
    # 신용, 콜은 무위험)은 각자 자기 격자에 그대로 남는다.
    #
    # 두 구역이 겹치는 자리(풋 행사금액 > 콜 행사금액)에서만 누가 먼저인지가
    # 문제가 되는데, 그건 전환사채와 같은 계약 문제라 pc_order 를 따른다.
    _kill = int(tm.sha_kill) == 1
    _pfirst = int(tm.pc_order) == 0
    KIND = [["hold"]*(n+1) for _ in range(n+1)]

    def settle(i, j, pe, ce, pc, cc):
        """그 노드에서 누가 행사하는가. 상호소멸일 때만 부른다."""
        _p = pe > SHA_SETTLE_TOL and pe >= pc - SHA_SETTLE_TOL
        _c = ce > SHA_SETTLE_TOL and ce >= cc - SHA_SETTLE_TOL
        if _p and _c: _p, _c = _pfirst, not _pfirst
        if _p:  return pe, 0.0, "put"
        if _c:  return 0.0, ce, "call"
        return pc, cc, "hold"

    for j in range(n+1):
        pe = max(pk(n) - eq(n, j), 0.0) if p_on(n) else 0.0
        ce = max(eq(n, j) - ck(n), 0.0) if c_on(n) else 0.0
        if _kill: P[n][j], C[n][j], KIND[n][j] = settle(n, j, pe, ce, 0.0, 0.0)
        else:     P[n][j], C[n][j] = pe, ce
        if qipo(n, j):
            P[n][j] = 0.0; KIND[n][j] = "qipo"
            if int(tm.sha_qipo_kill): C[n][j] = 0.0
    for i in range(n-1, -1, -1):
        q = qi(i)
        for j in range(i+1):
            pc = (q*P[i+1][j+1] + (1-q)*P[i+1][j]) * math.exp(-pdisc(i)*dt_)
            cc = (q*C[i+1][j+1] + (1-q)*C[i+1][j]) * math.exp(-rf(i)*dt_)
            pe = max(pk(i) - eq(i, j), 0.0) if p_on(i) else 0.0
            ce = max(eq(i, j) - ck(i), 0.0) if c_on(i) else 0.0
            if _kill:
                P[i][j], C[i][j], KIND[i][j] = settle(i, j, pe, ce, pc, cc)
            else:
                P[i][j] = max(pc, pe)
                C[i][j] = max(cc, ce)
            if qipo(i, j):
                P[i][j] = 0.0; KIND[i][j] = "qipo"
                if int(tm.sha_qipo_kill): C[i][j] = 0.0

    # 행사·소멸 확률. 앞에서부터 한 칸씩 굴리며 흡수한다.
    def walk(V, ex_on, ex_val, kill_it, mine=None):
        """행사·소멸 분포. ``mine`` 은 상호소멸일 때 이 격자의 행사 이름이다.

        상호소멸 계약에서는 **상대방이 먼저 행사해 내 권리가 사라지는** 갈래가
        따로 있다. 그 자리를 「상대방 행사로 소멸」로 흡수하지 않으면 계속보유로
        새어 나가 분포가 1 을 넘거나 모자란다.
        """
        prob = dict(ex=0.0, tex=0.0, qipo=0.0, expire=0.0, counter=0.0)
        lay = {0: 1.0}
        for i in range(n+1):
            nxt = {}
            q = qi(i) if i < n else 0.0
            for j, p_ in lay.items():
                if qipo(i, j) and kill_it:
                    prob["qipo"] += p_; continue
                if mine is not None and KIND[i][j] not in ("hold", "qipo"):
                    if KIND[i][j] == mine:
                        prob["ex"] += p_; prob["tex"] += p_*i
                    else:
                        prob["counter"] += p_
                    continue
                # 평가기준일(i=0)도 예외가 아니다. 그날 행사가 최적이면 그
                # 유형이 100% 다 — i>0 예외를 두면 루트 행사가 분포에서 사라진다.
                if mine is None and ex_on(i) and ex_val(i, j) > SHA_SETTLE_TOL \
                        and abs(V[i][j] - ex_val(i, j)) < SHA_SETTLE_TOL:
                    prob["ex"] += p_; prob["tex"] += p_*i; continue
                if i == n:
                    prob["expire"] += p_; continue
                nxt[j+1] = nxt.get(j+1, 0.0) + p_*q
                nxt[j] = nxt.get(j, 0.0) + p_*(1-q)
            lay = nxt
        return prob

    pw = walk(P, p_on, lambda i, j: max(pk(i) - eq(i, j), 0.0), True,
              mine=("put" if _kill else None))
    cw = walk(C, c_on, lambda i, j: max(eq(i, j) - ck(i), 0.0),
              bool(int(tm.sha_qipo_kill)), mine=("call" if _kill else None))

    # 1032 문단 23 — 발행회사가 자기지분상품을 매입할 의무를 지면 **옵션
    # 공정가치가 아니라** 상환금액의 현재가치를 총액으로 부채에 싣는다.
    # 상환금액은 가장 이른 행사 가능일의 행사금액으로 잡는다.
    first_p = next((i for i in range(n+1) if p_on(i)), None)
    if first_p is None:
        gross = None
    else:
        # 격자와 같은 할인이어야 조서가 한 사슬로 이어진다. 구간 선도이자율을
        # 첫 행사일까지 곱한다 — 곡선에서 뽑은 현물할인계수와 같은 값이다.
        df = 1.0
        for i in range(first_p):
            df *= math.exp(-pdisc(i)*dt_)
        gross = dict(step=first_p, t=first_p*dt_, strike=pk(first_p),
                     pv=pk(first_p)*df, df=df)
    return dict(put=P[0][0], call=C[0][0], P=P, C=C, S=S, eq=eq, KIND=KIND,
                kill=_kill,
                pk=pk, ck=ck, p_on=p_on, c_on=c_on, qipo=qipo,
                qi_step=qi_step, n=n, dt=dt_, mper=mper, u=u, d=d,
                q=qi(0), qs=qs, qbad=qbad, qmin=min(qs), qmax=max(qs),
                qi=qi, rf=rf, pdisc=pdisc, gross=gross,
                dist_put=pw, dist_call=cw)


def sha_backsolve(tm: Terms, target: float = None):
    """투자원금으로 지분가치를 역산한다.

    비상장 대상회사는 관측 주가가 없다. 「지분 + 풋 − 콜 = 투자원금」 이라고
    놓고 그 등식을 만족하는 주가를 이분법으로 찾는다. 투자자가 낸 돈이 곧
    받은 것의 공정가치라는 발행 시점의 전제다.
    """
    target = 100.0 if target is None else target
    t2 = Terms(**asdict(tm))

    def f(S):
        t2.S0 = S
        r = sha_engine(t2)
        return 100*S/t2.K0 + r["put"] - r["call"]

    lo, hi = tm.K0*0.02, tm.K0*5.0
    flo, fhi = f(lo), f(hi)
    if flo >= target: return lo, flo, -1
    if fhi <= target: return hi, fhi, -1
    for k in range(80):
        mid = 0.5*(lo+hi); fm = f(mid)
        if abs(fm - target) < 1e-7 or hi-lo < 1e-6*max(1.0, mid):
            return mid, fm, k+1
        if fm < target: lo = mid
        else: hi = mid
    return mid, fm, 80


def call_third_party(tm: Terms, full, method: int) -> float:
    """제3자 지정 가능 콜옵션 — 옵션차익혼합할인법.

    한국공인회계사회 『K-IFRS 실무사례와 해설 11 복합금융상품』 4.4.3 과
    부속예제 [사례 4-4] 의 산식이다. 발행자가 지정한 제3자에게 넘어갈 수 있는
    콜옵션은 기준서 1109 문단 4.3.1 상 별도의 금융상품이고, 기초자산이
    전환사채인 복합옵션(an option on an option)이므로 격자 안에서
    MIN(계속보유, 콜금액) 으로 누르는 발행자 콜옵션과 다르게 평가한다.

    기초자산은 ``콜과 그 부속조항(의무보유 등)을 포함하지 않은 전환사채`` 다.
    ``decompose`` 가 넘기는 ``full = engine(tm, call=False)`` 이 정확히 그것이라
    새 격자를 만들지 않고 그 memo 를 한 번 더 역진한다.

    method 1  혼합할인율      — 값 하나를 자식의 구성비율로 섞은 할인율로 할인
    method 2  지분·부채 분리   — 페이오프를 구성비율로 쪼개 각각 Rf·Rd 로 할인
    """
    memo, dt_ = full["memo"], full["dt"]
    qi, fRF, fCR = full["qi"], full["fwdRF"], full["fwdCR"]
    kstrike = full["kstrike"]
    cache = {}

    def w(o):
        """구성비율 — 노드 가치 중 지분 몫."""
        v = o["E"] + o["B"]
        return o["E"]/v if v > 1e-12 else 0.0

    def rec(key, i):
        if key in cache: return cache[key]
        o = memo[key]
        K = kstrike(i)
        pay = max(o["E"] + o["B"] - K, 0.0) if K is not None else 0.0
        if "up" not in o:                       # 만기 — 자식이 없다
            ww = w(o)
            r = (pay, pay*ww, pay*(1-ww))
        else:
            q, ou, od = qi(i), memo[o["up"]], memo[o["dn"]]
            cu, eu, bu = rec(o["up"], i+1)
            cd, ed, bd = rec(o["dn"], i+1)
            if method == 1:
                yu = w(ou)*fRF(i) + (1-w(ou))*fCR(i)
                yd = w(od)*fRF(i) + (1-w(od))*fCR(i)
                cont = q*cu*math.exp(-yu*dt_) + (1-q)*cd*math.exp(-yd*dt_)
                r = (max(pay, cont), 0.0, 0.0)
            else:
                he = (q*eu + (1-q)*ed) * math.exp(-fRF(i)*dt_)
                hb = (q*bu + (1-q)*bd) * math.exp(-fCR(i)*dt_)
                if pay >= he + hb:
                    ww = w(o); r = (pay, pay*ww, pay*(1-ww))
                else:
                    r = (he + hb, he, hb)
        cache[key] = r
        return r

    return rec(full["root"], 0)[0]


K_METHODS = {0: "유무가치비교법", 1: "옵션차익혼합할인법 · 혼합할인율",
             2: "옵션차익혼합할인법 · 지분·부채 분리"}



def sha_accounts(tm: Terms, R):
    """주주간계약을 세 당사자의 재무제표로 옮긴다.

    같은 계약인데 실리는 것이 완전히 다르다.

    **발행회사** — 계약 당사자가 최대주주뿐이면 발행회사 재무제표에는 아무것도
    실리지 않는다. 발행회사가 풋 의무자면 이야기가 뒤바뀐다. 자기지분상품을
    매입할 의무이므로 기업회계기준서 제1032호 문단 23 이 걸린다.

    > 자기지분상품을 매입해야 하는 의무가 포함된 계약의 경우, 그 의무에 대하여
    > 인식하는 금융부채는 **상환금액의 현재가치**이다.

    옵션 공정가치가 아니라 **총액**이다. 상대 계정은 자본이다. 조건부 결제라도
    문단 25 에 따라 원칙적으로 금융부채다 — 결제 요구 조항이 진성이 아니거나
    발행자가 청산되는 경우에만 결제되는 것이 아니라면 그렇다.

    **최대주주** — 자기지분상품이 아니라 남의 주식이므로 문단 23 이 걸리지
    않는다. 매도한 풋은 파생상품부채, 매수한 콜은 파생상품자산이고 둘 다
    공정가치로 재평가한다.

    **투자자** — 매수한 풋은 파생상품자산, 매도한 콜은 파생상품부채다. 보유
    지분은 별도의 지분상품이다. 상계 요건을 못 채우면 총액으로 표시한다.
    """
    put_, call_ = R["put"], R["call"]
    g = R["gross"]
    w = int(tm.sha_writer)
    has_call = tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
    gpv = (g["pv"] if g else 0.0)
    out = {}

    if w == 0:
        out["발행회사"] = ([("인식할 것이 없다 — 계약 당사자가 아니다", 0.0)],
            "풋 의무자가 최대주주뿐이므로 발행회사의 자기지분상품 매입의무가 "
            "아니다. 발행회사 재무제표에는 실리지 않는다. 다만 주주간계약의 "
            "존재와 조건은 특수관계자 거래·우발상황 주석에서 다룰 수 있다.")
    else:
        out["발행회사"] = ([
            ("금융부채 — 자기지분상품 매입의무 (상환금액의 현재가치)", gpv),
            ("자본 (기타자본 차감)", -gpv),
            ("합계", 0.0)],
            "기업회계기준서 제1032호 문단 23 — 자기지분상품을 매입해야 하는 의무는 "
            "**옵션 공정가치가 아니라 상환금액의 현재가치**를 총액으로 부채에 싣고 "
            "같은 금액을 자본에서 뺀다. "
            + (f"첫 행사 가능일의 행사금액 {g['strike']:,.4f} 를 그날까지 할인한 "
               f"{gpv:,.4f} 이다. " if g else "")
            + "이후 부채는 유효이자율법으로 상환금액까지 늘려 가고, 그 증가액은 "
              "이자비용이다. 풋이 행사되지 않고 소멸하면 부채를 제거하고 자본으로 "
              "되돌린다 (문단 23 후단). 조건부 결제조항이라도 결제 요구가 진성이 "
              "아니거나 발행자 청산 시에만 결제되는 경우가 아니면 금융부채다 "
              "(문단 25)."
            + ("  연대의무를 고르셨습니다 — 하나의 의무를 두 사람이 지는 것이므로 "
               "계약상 1차 의무자 기준으로 **한 곳에서만** 인식하십시오."
               if w == 2 else ""))

    if w in (0, 2):
        rows = [("파생상품부채 — 매도한 풋옵션", put_)]
        if has_call: rows.append(("파생상품자산 — 매수한 콜옵션", -call_))
        rows.append(("합계 (순액)", sum(v for _, v in rows)))
        note = ("최대주주에게 대상회사 주식은 **자기지분상품이 아니므로** 문단 23 이 "
                "걸리지 않는다. 매도한 풋은 파생상품부채, 매수한 콜은 파생상품자산이고 "
                "매기 공정가치로 재평가해 당기손익에 반영한다. 상계 요건(제1032호 "
                "문단 42)을 못 채우면 재무상태표에는 총액으로 표시한다.")
    else:
        rows = [("인식할 것이 없다 — 풋 의무자가 발행회사다", 0.0)]
        note = ("풋 의무자를 발행회사로 두셨습니다. 최대주주가 콜만 가지고 있다면 "
                "그 콜은 파생상품자산입니다.")
        if has_call:
            rows = [("파생상품자산 — 매수한 콜옵션", -call_), ("합계", -call_)]
    out["최대주주"] = (rows, note)

    rows = [("지분상품 — 보유 주식 (공정가치)", 100*tm.S0/tm.K0),
            ("파생상품자산 — 매수한 풋옵션", put_)]
    if has_call: rows.append(("파생상품부채 — 매도한 콜옵션", -call_))
    rows.append(("합계", sum(v for _, v in rows)))
    out["투자자"] = (rows, (
        "투자자는 주식과 파생을 따로 인식한다. 풋은 파생상품자산, 매도한 콜은 "
        "파생상품부채이고 둘 다 당기손익-공정가치다. 보유 주식은 지분상품이라 "
        "당기손익-공정가치 또는 (선택 시) 기타포괄손익-공정가치로 잰다 — "
        "제1109호 문단 4.1.4·5.7.5. "
        "주식과 풋이 하나의 거래로 묶여 **실질적으로 원리금 회수**만 남는다면 "
        "전체를 하나의 금융상품(대여금 성격)으로 볼 여지가 있다. 그때는 지분이 "
        "아니라 상각후원가·당기손익-공정가치 금융자산이 되므로, 이 표를 쓰기 전에 "
        "계약의 실질을 먼저 판단하십시오."))
    return out


def sha_validate(tm: Terms):
    """주주간계약 인풋에서 계약과 어긋나는 것을 잡는다."""
    w = []
    if tm.sha_put_s > tm.sha_put_e:
        w.append("풋 행사 시작이 종료보다 늦습니다. 풋이 없는 것으로 계산됩니다.")
    horizon = tm.T*12 + tm.elapsed_m + 0.5
    if tm.sha_put_e > horizon:
        w.append(f"풋 행사 종료({tm.sha_put_e:.0f}개월)가 격자 끝({horizon:.0f}개월)을 "
                 "넘습니다. 만기일을 계약의 마지막 행사 가능일 뒤로 두십시오.")
    if 0 < tm.sha_call_e <= horizon + 1e9 and tm.sha_call_e > horizon:
        w.append(f"콜 행사 종료({tm.sha_call_e:.0f}개월)가 격자 끝을 넘습니다.")
    if int(tm.ipo_on) and tm.ipo_min <= 0:
        w.append("적격상장을 켜셨는데 **적격 판정 최소 주가가 0** 입니다. 어떤 "
                 "경로에서도 상장이 성공한 것으로 잡혀 풋이 통째로 사라집니다.")
    if int(tm.sha_writer) in (1, 2):
        w.append("풋 의무자를 **발행회사**로 두셨습니다. 자기지분상품 매입의무이므로 "
                 "발행회사 재무제표에는 옵션 공정가치가 아니라 **상환금액의 현재가치를 "
                 "총액으로** 싣습니다 (1032 문단 23). 회계처리 탭에서 두 값을 나란히 "
                 "보시고, 어느 것을 인식하는지 조서에 밝히십시오.")
    if int(tm.sha_disc) == 0 and tm.sha_put_yield > 0:
        w.append("풋을 **무위험**으로 할인하고 있습니다. 풋은 현금을 받을 권리라 "
                 "의무자의 신용위험이 붙습니다 — 최대주주 개인이 의무자면 그 신용을 "
                 "반영하지 않은 값은 과대평가입니다. 할인율 칸을 확인하십시오.")
    try:
        _RF, _CR = curves(tm)
        if _CR(tm.T) - _RF(tm.T) < 0:
            w.append("위험 곡선이 무위험 곡선보다 낮습니다. 두 곡선을 바꿔 "
                     "넣으셨는지 확인하십시오.")
    except Exception:
        pass
    return w


# ══════════════════════════════════════════════════════════
# 3-1. BDT 금리격자 — 조기상환권 전용
# ══════════════════════════════════════════════════════════
# 전환을 끄면 격자가 주가와 무관해져 스텝마다 값이 하나뿐이다. 즉 지금
# 조기상환권은 불확실성이 없는 확정 계산이고 옵션의 시간가치가 없다.
# 금리를 확률변수로 두면 그 시간가치가 생긴다. 조기상환권은 주가와 무관한
# 순수 금리·신용 상품이라, 주가 격자를 건드리지 않고 여기서만 따로 잰다.


def bdt_tree(spot, T: float, n: int, sig: float):
    """현물이자율 곡선에 맞춘 BDT 단기이자율 격자.

        r(i, j) = a_i · exp(2·σ·j·√Δt)          j 는 상승 횟수 (0..i)

    로그정규라 이자율이 음수가 되지 않는다. a_i 는 (i+1)Δt 만기 무이표채를
    정확히 재현하도록 역산한다 — 그래서 옵션이 없는 사채는 곡선을 그대로
    되돌려 준다. 위험중립확률은 BDT 관행대로 0.5 다.

    σ 가 0 이면 a_i 가 구간 선도이자율이 되어 확정 격자와 완전히 같아진다.
    이 성질을 검사에서 쓴다.
    """
    dt_ = T/max(1, n)
    sq = math.sqrt(dt_)
    P = [math.exp(-spot(k*dt_)*k*dt_) for k in range(n+1)]   # 시장 할인계수
    r, base, Q = [], [], [[1.0]]                             # Q 는 도달가격
    for i in range(n):
        mul = [math.exp(2*sig*j*sq) for j in range(i+1)]
        def price(a):
            return sum(Q[i][j]*math.exp(-a*mul[j]*dt_) for j in range(i+1))
        lo, hi = 1e-10, 5.0                    # 연 500% 까지 잡으면 넉넉하다
        for _ in range(200):                   # price 는 a 에 대해 감소한다
            mid = (lo+hi)/2
            if price(mid) > P[i+1]: lo = mid
            else: hi = mid
        a = (lo+hi)/2
        base.append(a)
        r.append([a*x for x in mul])
        nq = [0.0]*(i+2)
        for j in range(i+1):
            d = 0.5*Q[i][j]*math.exp(-r[i][j]*dt_)
            nq[j+1] += d; nq[j] += d
        Q.append(nq)
    return r, base


def bdt_parts(tm: Terms):
    """BDT 격자에서 쓸 재료를 한곳에서 만든다. 조서도 이것을 그대로 쓴다.

    기준 곡선은 두 가지로 고를 수 있다.

    * 0 위험 곡선 직접 — 단기이자율이 곧 위험이자율이다. σ 가 위험이자율
      전체의 변동성이라 신용스프레드 변동성까지 안고 간다. 옵션 없는 사채가
      격자의 주계약과 정확히 같아져 검산이 쉽다.
    * 1 무위험 + 확정 스프레드 — 국고채에 σ 를 태우고 구간 선도 스프레드를
      확정으로 얹는다. σ 를 국고채에서 관측한 값으로 쓸 수 있지만,
      스프레드가 금리와 무관하다고 본 것이므로 그 한계를 조서에 적어야 한다.
    """
    derive(tm)
    RF, CR = curves(tm)
    n, T = int(tm.n), tm.T
    dt_ = T/n
    ey = tm.elapsed_m/12
    mper = n/(T*12)
    st_lo, st_hi = step_mapper(tm, n, dt_)
    if tm.bdt_base == 0:
        rt, ab = bdt_tree(CR, T, n, tm.bdt_sig)
        add = [0.0]*n
    else:
        rt, ab = bdt_tree(RF, T, n, tm.bdt_sig)
        add = [forward_rate(CR, i*dt_, (i+1)*dt_) - forward_rate(RF, i*dt_, (i+1)*dt_)
               for i in range(n)]
    red = 100*(1 + accrue_rate(T + ey, tm.ytm, eff_cpn(tm), tm.ytm_cmp))
    cpn_amt = 100*eff_cpn(tm)*tm.ipay/12
    pay_per = max(1, int(round(tm.ipay*mper)))
    pay_off = pay_offset(tm, st_lo)
    is_pay = lambda i: (eff_cpn(tm) > 0 and i > 0 and i >= pay_off
                        and (i-pay_off) % pay_per == 0)
    p_lo, p_hi = st_lo(tm.p_s), st_hi(tm.p_e)
    p_per = max(1, int(round(tm.p_f*mper)))
    in_put = lambda i: (max(p_lo, 0) <= i <= p_hi and (i-p_lo) % p_per == 0)
    put_a = lambda i: ((100*(1 + accrue_rate(i*dt_ + ey, tm.p_yield, eff_cpn(tm), tm.p_cmp))
                        if tm.p_mode == "accrue" else tm.p_rate)
                       if in_put(i) else 0.0)
    # 캘리브레이션 검산 재료 — 도달가격 Q 와 시장 할인계수.
    # Σ_j Q(k,j) 가 시장 할인계수와 같아야 한다. 이것이 무차익거래 조건이고,
    # 기준금리 a 를 그 조건에 맞춰 역산한 것이다. 조서에서 눈으로 확인하도록
    # 격자와 함께 내보낸다.
    base = CR if tm.bdt_base == 0 else RF
    mkt = [math.exp(-base(k*dt_)*k*dt_) for k in range(n+1)]
    Q = [[1.0]]
    for i in range(n):
        nq = [0.0]*(i+2)
        for j in range(i+1):
            d = 0.5*Q[i][j]*math.exp(-rt[i][j]*dt_)
            nq[j+1] += d; nq[j] += d
        Q.append(nq)
    return dict(r=rt, a=ab, add=add, n=n, T=T, dt=dt_, red=red, cpn=cpn_amt,
                is_pay=is_pay, in_put=in_put, put_a=put_a, Q=Q, mkt=mkt,
                base_nm=("위험 곡선" if tm.bdt_base == 0 else "무위험 곡선"))


def bdt_grid(tm: Terms, put: bool):
    """BDT 격자에서 사채를 역진하고 전 노드 값을 돌려준다.

    만기 노드와 중간 노드의 판정을 격자 엔진과 같은 순서로 맞춘다 —
    만기는 MAX(조기상환금액, 만기상환금액) + 쿠폰, 중간은 MAX(계속보유,
    조기상환금액) 이다. 조서가 표를 그릴 때 이 격자를 그대로 쓴다.
    """
    B = bdt_parts(tm)
    n, dt_ = B["n"], B["dt"]
    cm = B["cpn"] if B["is_pay"](n) else 0.0
    V = [[0.0]*(i+1) for i in range(n+1)]
    for j in range(n+1):
        V[n][j] = max(B["put_a"](n) if put else 0.0, B["red"]) + cm
    for i in range(n-1, -1, -1):
        c = B["cpn"] if B["is_pay"](i) else 0.0
        for j in range(i+1):
            d = math.exp(-(B["r"][i][j] + B["add"][i])*dt_)
            h = (0.5*V[i+1][j+1] + 0.5*V[i+1][j])*d + c
            if put and B["in_put"](i): h = max(h, B["put_a"](i))
            V[i][j] = h
    return B, V


def bond_bdt(tm: Terms, put: bool) -> float:
    """BDT 격자에서 잰 사채 가치 (t=0)."""
    return bdt_grid(tm, put)[1][0][0]


def put_bdt_on(tm: Terms) -> bool:
    """BDT 를 실제로 쓸 조건인가.

    전환권을 자본으로 두고 TF 를 쓸 때만 연다. 자본이면 전환권대가가 잔여라
    부채요소만 바꿔도 배분이 그대로 성립하지만, 부채로 두면 복합내재파생을
    전체로서 재야 해서 전체 가치(주가 격자)까지 같이 손봐야 하기 때문이다.
    """
    return bool(tm.put_bdt) and tm.conv_class == "equity" and tm.model == "TF" \
        and tm.p_s <= tm.p_e


def decompose(tm: Terms):
    derive(tm)
    # RCPS 도 같은 길을 간다. derive 가 k_w(=발행자 상환권 유무)·k_sep·k_lock 을
    # 고정해 두었으므로 「B0 → +상환청구권 → +전환권 → −발행자상환권」 순차 차감이
    # CB 의 유무가치비교법(100%)과 정확히 같은 계산이 된다.
    full = engine(tm, call=False)
    b0 = pick(engine(tm, conv=False, put=False, call=False), tm.model)
    b1 = pick(engine(tm, conv=False, put=True, call=False), tm.model)
    # 조기상환권을 BDT 로 재면 부채요소만 갈아 끼운다. 전환권이 자본이면
    # 전환권대가가 잔여라 배분이 그대로 성립하고 합계도 100 을 지킨다.
    # 전체 가치(b2)는 주가 격자 그대로 두므로, 그 차이는 잔여가 흡수한다.
    if put_bdt_on(tm):
        b1 = bond_bdt(tm, True)
    b2 = pick(full, tm.model)
    # 행사 가능한 시점이 하나도 없으면 매도청구권은 없다.
    ks = full["kstrike"]
    has_call = tm.k_w > 0 and any(ks(i) is not None for i in range(full["n"]+1))
    full["has_call"] = has_call          # 화면이 무엇을 「공정가치」로 부를지 가른다
    if not has_call:
        ca = 0.0
    elif tm.k_method:
        # 제3자 지정 가능 콜옵션 — 기초자산은 콜·의무보유를 뺀 full 그대로다
        ca = tm.k_w*call_third_party(tm, full, tm.k_method)
    else:
        # 의무보유는 전환을 늦추기만 한다. 전환 시작보다 이르면 아무 제약이 아니다.
        cs = max(tm.cv_s, tm.k_lock)
        b3 = pick(engine(tm, conv=True, put=True, call=True, conv_start=cs), tm.model)
        ca = tm.k_w*(b2-b3)
    # RCPS 의 발행자 상환권은 자본요소가 아닌 파생이라 **부채요소 안에서** 잰다
    # (1032 문단 31·32 — 비자본 파생 특성은 부채요소 장부금액에 포함). 전체 격자에서
    # 잰 콜(ca)은 전환 상승분을 자르는 값이라 부채에서 빼면 부채가 과소, 자본이
    # 과대가 되고, 풋보다 커지면 복합내재파생이 음수가 되기도 한다. 그래서 전환권을
    # 자본으로 볼 때의 배분에는 부채 격자(전환권 없음)에서 잰 콜을 쓴다. 전환권이
    # 부채면 전환권·풋·콜을 전체 격자에서 묶어 재므로 ca 그대로다.
    if issuer_redeem(tm) and has_call:
        _b1p = pick(engine(tm, conv=False, put=True, call=False), tm.model)
        _b1c = pick(engine(tm, conv=False, put=True, call=True), tm.model)
        full["ca_debt"] = max(0.0, _b1p - _b1c)
        resid = 100 - b1 + full["ca_debt"]
    else:
        resid = 100 - b1 + ca      # 전환권이 자본일 때의 잔여 (전환권대가)
    return full, b0, b1, b2, ca, resid

def ipo_scenarios(tm: Terms, months=None):
    """상장 시점을 바꿔 가며 값을 낸다.

    예상 상장 시점은 **가정**이다. 한 값만 내면 그 가정이 조서에서 사라지므로,
    여러 시점과 「상장 없음」을 나란히 실어 감사인이 폭을 보게 한다.
    """
    if not (is_rcps(tm) and tm.ipo_on and tm.ipo_px > 0): return []
    base = [float(tm.ipo_m)] + [float(tm.ipo_m)+12, float(tm.ipo_m)+24]
    months = months if months is not None else sorted({m for m in base
                                                       if 0 < m < tm.T*12 + tm.elapsed_m})
    out = []
    for m in list(months) + [None]:
        t2 = Terms(**asdict(tm))
        if m is None: t2.ipo_on = 0
        else: t2.ipo_m = m
        derive(t2)
        full, b0, b1, b2, ca, conv = decompose(t2)
        out.append(("상장 없음" if m is None else f"{m:,.0f}개월 상장",
                    b2, b2-ca, conv, (b2 - (out[0][1] if out else b2))))
    return out


def backsolve(tm: Terms, target: float = None, lo: float = None, hi: float = None):
    """발행가로 주가를 역산한다 (책 [사례 5-1] TF모형 with Backsolve).

    비상장 발행회사는 관측 주가가 없다. 대신 「이 조건으로 발행된 값이 곧
    공정가치다」 라고 놓고, 전체 가치(B2)가 발행가(100)가 되는 주가를 격자에서
    찾는다. 전체 가치는 주가에 단조증가하므로 이분법으로 충분하다.

    **무엇을 발행가에 맞출 것인가**는 계약이 정한다 (``bs_net``). 매도청구권은
    격자 밖에서 따로 재어 차감하는 파생상품자산이라, 본체(B2)만 100 에 맞추면
    투자자가 실제로 받은 순액은 100 에 못 미친다 — 돈을 내면서 매도청구권까지
    써 주었기 때문이다. 발행가가 패키지 전체의 대가라면 「본체 − 매도청구권」 을
    100 에 맞춰야 하고, 그러면 역산 주가가 올라간다.

    반대로 매도청구권에 별도 대가가 오갔거나 최초인식 차이(Day-1)가 따로 있다면
    본체 기준이 맞다. 그래서 고르게 두고 기본값은 현행(본체)을 지킨다.

    돌려주는 것은 (주가, 그 주가에서의 목표값, 반복 횟수). 격자가 목표에 못 닿으면
    가장 가까운 끝값을 돌려주고 반복 횟수를 −1 로 표시한다.
    """
    target = tm.bs_target if target is None else target
    t2 = Terms(**asdict(tm))
    _net = int(tm.bs_net) == 1 and tm.k_w > 0

    def f(S):
        t2.S0 = S
        if _net:
            # 매도청구권까지 재려면 분해가 필요하다. 본체보다 느리지만 이분법
            # 스무 번 남짓이라 견딜 만하다.
            _f, _b0, _b1, _b2, _ca, _cv = decompose(t2)
            return _b2 - _ca
        return pick(engine(t2, call=issuer_redeem(t2)), t2.model)
    lo = lo if lo is not None else tm.K0*0.02
    hi = hi if hi is not None else tm.K0*5.0
    flo, fhi = f(lo), f(hi)
    if flo >= target: return lo, flo, -1
    if fhi <= target: return hi, fhi, -1
    for k in range(80):
        mid = 0.5*(lo+hi); fm = f(mid)
        if abs(fm - target) < 1e-7 or hi-lo < 1e-6*max(1.0, mid): return mid, fm, k+1
        if fm < target: lo = mid
        else: hi = mid
    return mid, fm, 80


# 분리 판단에서 "행사가격이 상각후원가와 거의 같다" 로 볼 문턱.
# 기준서는 "거의 같다" 라고만 하고 수치를 주지 않는다. 실무에서 널리 쓰는
# 10% 를 기본으로 두되, 문턱 근처면 그 사실 자체를 알린다.
SPLIT_TOL = 0.10


def _close_test(strike, amort):
    """행사가격과 상각후원가가 '거의 같은가' — 문단 B4.3.5(5)(가)."""
    gap = abs(strike - amort)/max(abs(amort), 1e-9)
    return gap, gap <= SPLIT_TOL


def split_test(tm: Terms, full, b0, b1, b2, ca, rows_eir):
    """조기상환권·매도청구권을 주계약과 분리해야 하는지 계약 조항으로 판단한다.

    판단 순서가 정해져 있다. 문단 B4.3.5 말미가 못박는다 — "기업회계기준서
    제1032호에 따라 전환채무상품의 자본요소를 분리하기 **전에** 내재된
    콜옵션이나 풋옵션이 주채무계약과 밀접하게 관련되어 있는지를 판단한다."

    돌려주는 것은 옵션마다 결론·근거·평가방법·지표를 담은 사전이다. 화면과
    조서가 같은 것을 쓰므로 둘이 어긋날 수 없다.
    """
    n, dt_ = int(tm.n), tm.T/int(tm.n)
    ey = tm.elapsed_m/12
    liab = tm.conv_class != "equity"

    # 문단 B4.3.5(5)(가) 의 「채무상품의 상각후원가」는 **주계약**의 상각후원가다.
    # 내재파생을 떼어 낸 뒤 남는 사채, 곧 B0 에서 출발하는 상각표다.
    #
    # 넘겨받은 rows_eir 은 **실제로 인식한 배분액**에서 출발한다 — 조기상환권을
    # 분리하지 않기로 고르면 부채요소(B1)에서 시작한다. 그것을 그대로 쓰면
    # 「분리하지 않기로 했더니 상각후원가가 올라가 행사금액과 가까워지고, 그래서
    # 분리하지 않아도 된다」는 순환이 생긴다. 판정은 설정과 무관해야 하므로
    # 여기서는 늘 B0 기준 상각표를 다시 만들어 쓴다.
    try:
        _rows_host = eir_table(tm, b0)[1]
    except Exception:
        _rows_host = rows_eir

    def amort_at(t_year):
        """그 시점 **주계약**의 상각후원가. 분리 여부 설정과 무관하다."""
        return next((en for _, tt_, _b, _i, _c, en in _rows_host
                     if tt_ >= t_year - 1e-9), b0)

    out = {}

    # ── 조기상환청구권 ────────────────────────────────────────
    if tm.p_s > tm.p_e or tm.T <= 0:
        out["put"] = dict(있음=False, 결론="해당 없음",
                          이유=["계약에 조기상환청구권이 없습니다."],
                          근거=[], 평가="—", 지표={})
    else:
        pv = (100*(1 + accrue_rate(tm.p_s/12, tm.p_yield, eff_cpn(tm), tm.p_cmp))
              if tm.p_mode == "accrue" else tm.p_rate)
        bv = amort_at(max(0.0, (tm.p_s - tm.elapsed_m)/12))
        gap, close = _close_test(pv, bv)
        why, cite = [], []
        if tm.fvpl_whole:
            res = "분리하지 않음"
            why.append("복합계약 전체를 당기손익-공정가치로 측정하므로 분리 요건이 "
                       "성립하지 않습니다.")
            cite.append("1109 문단 4.3.3(3)")
        elif liab:
            res = "묶어서 분리"
            why.append("전환권이 파생상품부채이므로 조기상환권을 따로 떼지 않고 "
                       "전환권과 하나의 복합내재파생상품으로 묶어 전체로서 "
                       "측정합니다. 전환하거나 상환받거나 둘 중 하나라 서로 "
                       "배타적이어서, 따로 재어 더하면 총액이 부풀려집니다.")
            cite.append("1109 문단 B4.3.4")
        elif tm.p_lost_int:
            res = "분리하지 않음"
            why.append("행사가격이 잔여기간 상실이자의 현재가치를 보상하는 "
                       "수준이므로 주계약과 밀접하게 관련되어 있습니다.")
            cite.append("1109 문단 B4.3.5(5)(나)")
        elif close:
            res = "분리하지 않을 여지"
            why.append(f"첫 조기상환일 행사금액 {pv:,.2f} 와 같은 시점 주계약 "
                       f"상각후원가 {bv:,.2f} 의 차이가 {gap*100:.1f}% 로 "
                       "거의 같습니다. 밀접하게 관련되어 있다고 볼 여지가 "
                       "있습니다.")
            cite.append("1109 문단 B4.3.5(5)(가)")
        else:
            res = "분리"
            why.append(f"첫 조기상환일 행사금액 {pv:,.2f} 와 같은 시점 주계약 "
                       f"상각후원가 {bv:,.2f} 의 차이가 {gap*100:.1f}% 로 "
                       "거의 같지 않습니다.")
            why.append("같은 조건의 별도 금융상품이 파생상품의 정의를 충족하고, "
                       "복합계약 전체를 당기손익-공정가치로 측정하지 않습니다.")
            cite += ["1109 문단 B4.3.5(5)", "문단 4.3.3"]
        val = ("순차 차감 — 조기상환권만 얹은 값에서 옵션 없는 사채를 뺍니다 "
               f"(B1 − B0 = {b1-b0:,.4f}). 전환권과 대체 관계라 따로 재어 "
               "더하면 총액이 부풀려집니다."
               if res == "분리" else
               "묶음 전체를 공정가치로 측정합니다 (B2 − B0)." if res == "묶어서 분리"
               else "분리하지 않으므로 주계약에 포함해 상각후원가로 측정합니다.")
        out["put"] = dict(있음=True, 결론=res, 이유=why, 근거=cite, 평가=val,
                          지표={"첫 조기상환일 행사금액": pv,
                                "같은 시점 상각후원가": bv,
                                "차이": gap})

    # ── 매도청구권 ────────────────────────────────────────────
    ks = full["kstrike"]
    first_k = next((i for i in range(n+1) if ks(i) is not None), None)
    if tm.k_w <= 0 or first_k is None:
        out["call"] = dict(있음=False, 결론="해당 없음",
                           이유=["계약에 매도청구권이 없거나 행사 가능한 시점이 "
                                 "없습니다."], 근거=[], 평가="—", 지표={})
    else:
        kv = ks(first_k)
        kb = amort_at(first_k*dt_)
        kgap, kclose = _close_test(kv, kb)
        why, cite = [], []
        if tm.k_third or tm.k_transfer:
            res = "별도의 금융상품"
            why.append("계약상 " + ("발행회사가 제3자를 지정할 수 있어 거래상대방이 "
                                  "달라질 수 있습니다. " if tm.k_third else "")
                       + ("사채와 독립적으로 양도할 수 있습니다. "
                          if tm.k_transfer else "")
                       + "내재파생상품이 아니라 별도의 금융상품이므로 분리 요건을 "
                         "따질 것 없이 처음부터 별개의 파생상품으로 인식합니다.")
            cite.append("1109 문단 4.3.1 마지막 문장")
        elif tm.fvpl_whole:
            res = "분리하지 않음"
            why.append("복합계약 전체를 당기손익-공정가치로 측정하므로 분리 요건이 "
                       "성립하지 않습니다.")
            cite.append("1109 문단 4.3.3(3)")
        elif liab:
            res = "묶어서 분리"
            why.append("발행회사만 행사할 수 있어 거래상대방이 그대로이므로 내재파생"
                       "상품이고, 전환권이 파생상품부채이므로 전환권·조기상환권과 "
                       "하나의 복합내재파생상품으로 묶어 전체로서 측정합니다. "
                       "세 권리는 상호배타적·상호의존적입니다 — 전환하거나 상환받거나 "
                       "매도청구를 당하거나 셋 중 하나로만 끝나므로, 따로 재어 더하면 "
                       "일어날 수 없는 조합까지 값에 넣게 됩니다.")
            cite.append("1109 문단 B4.3.4")
        elif kclose:
            res = "분리하지 않을 여지"
            why.append(f"첫 매도청구일 매매대금 {kv:,.2f} 와 같은 시점 주계약 "
                       f"상각후원가 {kb:,.2f} 의 차이가 {kgap*100:.1f}% 로 "
                       "거의 같습니다.")
            cite.append("1109 문단 B4.3.5(5)(가)")
        else:
            res = "분리"
            why.append(f"발행회사만 행사할 수 있어 내재파생상품이고, 첫 매도청구일 "
                       f"매매대금 {kv:,.2f} 와 같은 시점 주계약 상각후원가 "
                       f"{kb:,.2f} 의 차이가 {kgap*100:.1f}% 로 거의 같지 "
                       "않습니다.")
            cite += ["1109 문단 4.3.1", "문단 B4.3.5(5)"]
        if res == "별도의 금융상품":
            val = ("기초자산이 전환사채 전체인 미국형 복합옵션이므로 "
                   "**옵션차익혼합할인법**이 개념적으로 정합합니다. 다만 계약에 "
                   "의무보유 조건이 있으면 그 효과를 값에 넣으려고 "
                   "유무가치비교법을 쓰기도 합니다 — 재는 대상이 다르므로 "
                   "고른 방법을 조서에 밝히십시오."
                   if tm.k_lock > tm.cv_s else
                   "기초자산이 전환사채 전체인 미국형 복합옵션이므로 "
                   "**옵션차익혼합할인법**이 개념적으로 정합합니다.")
        elif res in ("분리", "묶어서 분리"):
            val = ("순차 차감 — 콜을 넣고 뺀 차액입니다 "
                   f"(적용값 {ca:,.4f}). 의무보유로 잃는 전환권 가치까지 값에 "
                   "들어가므로, 계약에 의무보유가 없으면 그만큼 과대해집니다."
                   if tm.k_lock > tm.cv_s else
                   f"순차 차감 — 콜을 넣고 뺀 차액입니다 (적용값 {ca:,.4f}).")
        else:
            val = "분리하지 않으므로 주계약에 포함해 상각후원가로 측정합니다."
        out["call"] = dict(있음=True, 결론=res, 이유=why, 근거=cite, 평가=val,
                           지표={"첫 매도청구일 매매대금": kv,
                                 "같은 시점 상각후원가": kb,
                                 "차이": kgap,
                                 "제3자 지정 가능": bool(tm.k_third),
                                 "독립 양도 가능": bool(tm.k_transfer)})

    # ── 신주인수권 (BW) ──────────────────────────────────────
    if is_bw(tm):
        why, cite = [], []
        if int(tm.bw_pay) == 1:
            res = "복합금융상품의 자본요소" if not liab else "복합내재파생상품"
            why.append("신주인수권을 행사할 때 사채를 권면액만큼 납입에 갈음하므로 "
                       "(대용납입) 사채가 소멸하고 주식을 받습니다. 전환사채의 "
                       "전환권과 경제적 실질이 같아 격자도 같은 것을 씁니다.")
            cite.append("1032 문단 28~32")
        elif int(tm.bw_detach) == 1:
            res = "별도의 금융상품" if liab else "복합금융상품의 자본요소"
            why.append("분리형이라 신주인수권증권이 사채와 독립적으로 양도됩니다. "
                       "내재파생상품이 아니므로 분리 요건(밀접한 관련성)을 따질 것 "
                       "없이 처음부터 따로 인식합니다. 다만 사채와 **함께 발행된** "
                       "복합금융상품이므로 발행금액 배분은 부채요소를 먼저 공정가치로 "
                       "정하고 나머지를 신주인수권에 두는 방법을 그대로 씁니다.")
            cite += ["1109 문단 4.3.1 마지막 문장", "1032 문단 28~32"]
        else:
            res = "복합금융상품의 자본요소" if not liab else "복합내재파생상품"
            why.append("비분리형이라 신주인수권이 사채에 붙어 있습니다. 행사대금을 "
                       "현금으로 내므로 사채는 행사 뒤에도 남고, 조기상환을 받으면 "
                       "미행사 신주인수권도 함께 소멸합니다.")
            cite.append("1032 문단 28~32")
        if liab:
            why.append("행사가격 조정(리픽싱) 등으로 「확정 수량의 주식을 확정 금액의 "
                       "현금과 교환」하는 조건을 충족하지 못하면 자본이 아니라 "
                       "파생상품부채입니다.")
            cite.append("1032 문단 16(2)(나) · 문단 AG27")
        else:
            why.append("확정 수량의 주식을 확정 금액의 현금과 교환하므로 지분상품의 "
                       "정의를 충족합니다.")
            cite.append("1032 문단 16(2)(나)")
        val = ("행사대금을 사채로 갈음하므로 전환사채와 같은 격자에서 재고, "
               "부채요소를 먼저 정한 뒤 나머지를 신주인수권대가로 둡니다."
               if int(tm.bw_pay) == 1 else
               "행사해도 사채가 남으므로 사채와 신주인수권을 따로 재어 더합니다. "
               "신주인수권 행사가치는 «주식가치 − 권면액» 입니다."
               + ("" if int(tm.bw_detach) == 1 else
                  " 비분리형이라 사채가 소멸하는 노드에서 미행사분이 함께 "
                  "사라지는 것까지 격자에 넣었습니다."))
        out["warrant"] = dict(있음=True, 결론=res, 이유=why, 근거=cite, 평가=val,
                              지표={"행사대금 납입": ("사채 대용납입"
                                                 if int(tm.bw_pay) == 1 else "현금"),
                                    "신주인수권증권": ("분리형"
                                                  if int(tm.bw_detach) == 1
                                                  else "비분리형"),
                                    "신주인수권 공정가치": b2 - b1,
                                    "신주인수권대가 (잔여)": 100 - b1})
        out["warrant"]["설정일치"] = True

    # 화면에서 고른 회계 처리와 판정이 어긋나면 알린다
    want = 1 if out["call"]["결론"] == "별도의 금융상품" else 0
    out["call"]["설정일치"] = (not out["call"]["있음"]) or (tm.k_sep == want)
    # 조기상환권도 같다. 다만 스위치가 살아 있을 때만 본다 — 매도청구권을
    # 내재파생으로 묶거나 전환권이 부채면 조기상환권은 묶음에 딸려 분리된다.
    _live = tm.conv_class == "equity" and tm.k_sep != 0
    _res = out["put"]["결론"]
    if not out["put"]["있음"] or not _live or _res == "분리하지 않을 여지":
        # 「여지」는 어느 쪽으로도 갈 수 있다. 어긋났다고 하지 않는다.
        out["put"]["설정일치"] = True
    else:
        out["put"]["설정일치"] = (int(tm.p_sep) ==
                                (1 if _res in ("분리", "묶어서 분리") else 0))
    out["put"]["스위치"] = _live
    return out


def split_memo(sp) -> str:
    """분리 판단을 조서에 옮길 글로 편다. 화면과 조서가 같은 문안을 쓴다."""
    out = []
    for key, nm in (("warrant", "신주인수권"), ("put", "조기상환청구권"),
                    ("call", "매도청구권")):
        d = sp.get(key)
        if d is None: continue
        if not d["있음"]:
            out.append(f"[{nm}] {d['이유'][0]}"); continue
        out.append(f"[{nm}] 결론 — {d['결론']}\n"
                   + "\n".join("  · " + x for x in d["이유"])
                   + (f"\n  근거 — {' · '.join(d['근거'])}" if d["근거"] else "")
                   + "\n  평가방법 — " + d["평가"].replace("**", ""))
    return "\n\n".join(out)


RIGHT_COLS = ["권리", "권리자", "행사기간", "행사조건", "페이오프", "우선순위"]


def rights_table(tm: Terms):
    """계약상 권리를 한 표로 편다 — 격자 로직의 설계도다.

    모델을 짜기 전에 「누가 · 언제 · 무슨 조건으로 · 얼마를 받고 · 누가 먼저」를
    적어 두면 노드 의사결정이 그 표를 옮긴 것이 된다. 감사인에게도 이 표 하나가
    계약서 몇 장보다 빠르다. 화면과 조서가 같은 표를 쓴다.
    """
    L = lbl(tm)
    rows = []
    _mo = lambda a, b, f=None: (f"{a:,.0f} ~ {b:,.0f}개월"
                                + (f" · {f:,.0f}개월마다" if f else ""))
    if is_sha(tm):
        _pk = 100*(1 + accrue_rate(tm.sha_put_s/12, tm.sha_put_yield, 0.0,
                                   tm.sha_put_cmp))
        rows.append(("투자자 풋옵션", "투자자",
                     _mo(tm.sha_put_s, tm.sha_put_e, tm.sha_put_f),
                     ("적격상장하면 소멸" if int(tm.ipo_on) else "—"),
                     f"MAX(행사금액 − 지분가치, 0) · 첫날 {_pk:,.2f}",
                     "—"))
        if tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e:
            rows.append(("최대주주 콜옵션", "최대주주",
                         _mo(tm.sha_call_s, tm.sha_call_e, tm.sha_call_f),
                         ("적격상장하면 소멸" if (int(tm.ipo_on)
                                            and int(tm.sha_qipo_kill)) else "—"),
                         "MAX(지분가치 − 행사금액, 0)", "—"))
        rows.append(("풋 의무자", ["최대주주", "발행회사", "최대주주 · 발행회사 연대"]
                     [int(tm.sha_writer)], "—", "—",
                     ("상환금액의 현재가치를 총액으로 (1032 문단 23)"
                      if int(tm.sha_writer) else "옵션 공정가치"), "—"))
        return rows

    # 전환권 (BW 는 신주인수권)
    _rfx = (["조정 없음", "하향만 조정", "하향+상향 조정"][int(tm.rfx_mode)]
            + (f" · {tm.rfx_cyc:,.0f}개월 주기 · 하한 {max(tm.floor, tm.par):,.0f}원"
               if tm.rfx_mode > 0 else ""))
    rows.append((inst_text(tm, "전환권"), "투자자",
                 _mo(tm.cv_s, tm.cv_e), _rfx,
                 ("100 × 주가 ÷ 행사가격 − 100 (현금납입)" if bw_cash(tm)
                  else "100 × 주가 ÷ 전환가격"),
                 "—"))
    if auto_conv(tm):
        rows.append(("존속기간 만료 시 자동전환", "—", f"{tm.T*12+tm.elapsed_m:,.0f}개월",
                     "상법상 우선주 존속기간 만료", "100 × 주가 ÷ 전환가격", "—"))
    # 조기상환청구권
    _po = ("투자자 우선" if int(tm.pc_order) == 0 else "발행자 콜에 밀림")
    if tm.p_s <= tm.p_e:
        _amt = ("100 × (1 + 보장수익률 복리)" if tm.p_mode == "accrue"
                else f"{tm.p_rate:,.2f} 고정")
        rows.append((L["put"], "투자자", _mo(tm.p_s, tm.p_e, tm.p_f), "—", _amt,
                     (_po if tm.k_w > 0 else "—")))
    # 매도청구권 (RCPS 발행자 상환권 포함)
    if tm.k_w > 0 and tm.k_s <= tm.k_e:
        _who = ("발행회사" if not tm.k_third else "발행회사 또는 지정하는 제3자")
        _cond = []
        if tm.k_w < 1.0: _cond.append(f"한도 {tm.k_w:.0%}")
        if tm.k_lock > tm.cv_s: _cond.append(f"의무보유 {tm.k_lock:,.0f}개월")
        rows.append((L["call"], _who, _mo(tm.k_s, tm.k_e, tm.k_f),
                     (" · ".join(_cond) if _cond else "—"),
                     "100 × (1 + 프리미엄 복리)",
                     ("발행자 우선" if int(tm.pc_order) == 1 else "투자자 풋에 밀림")))
    return rows


def rights_memo(tm: Terms) -> str:
    """권리 표를 조서 문안으로. 화면과 조서가 같은 글을 쓴다."""
    rows = rights_table(tm)
    if not rows: return ""
    return "\n".join(
        f"[{r[0]}] 권리자 {r[1]} · 행사 {r[2]} · 조건 {r[3]} · 페이오프 {r[4]}"
        + (f" · 우선순위 {r[5]}" if r[5] != "—" else "")
        for r in rows)


def allocate(tm: Terms, full, b0, b1, b2, ca):
    """최초 인식 배분.  전환권 분류에 따라 무엇을 잔여로 두는지가 뒤바뀐다.

    매도청구권을 어떻게 볼지는 ``k_sep`` 이 정한다.

    * 1 별도 금융상품 — 제3자 지정이 가능하면 거래상대방이 달라지므로 내재파생이
      아니라 별도의 금융상품이다 (기준서 1109 문단 4.3.1 마지막 문장).
      파생상품자산으로 따로 세운다.
    * 0 복합내재파생에 포함 — 발행회사만 행사할 수 있으면 거래상대방이 그대로라
      내재파생상품이다. 전환권·조기상환권과 하나의 복합내재파생상품으로 묶는다
      (문단 B4.3.4). 콜은 발행자에게 유리하므로 묶음을 그만큼 줄인다.

    어느 쪽이든 주계약과 전환권대가는 같다. 파생을 총액으로 볼지 순액으로 볼지가
    다를 뿐이다.
    """
    sep = tm.k_sep != 0
    # 조기상환권을 분리하지 않는 선택은 **전환권이 자본이고 매도청구권이 별도
    # 금융상품일 때**만 살아 있다. 매도청구권을 내재파생으로 묶으면 문단 B4.3.4
    # 가 복수의 내재파생을 하나의 복합내재파생으로 다루라고 하므로 조기상환권도
    # 그 묶음에 딸려 분리된다. 전환권이 부채면 애초에 묶음으로 재므로 마찬가지다.
    psep = not (tm.conv_class == "equity" and sep and int(tm.p_sep) == 0)
    # 자본 갈래에서 부채요소를 줄이는 콜 — RCPS 는 부채 격자에서 잰 값 (문단 31)
    cad = full.get("ca_debt", ca) if is_rcps(tm) else ca
    # 매도청구권 줄은 값이 0 이 아니면 싣는다. 음수(강제전환 할인율 효과 — 모형 성질)라고
    # 빼 버리면 잔여 계산에는 들어가 있어 합이 100 에서 어긋난다 (조합시험이 잡음).
    if fvpl_on(tm):
        # 복합계약 **전체**를 당기손익-공정가치로 지정했다 (문단 4.2.2·4.3.3(3)).
        # 내재파생을 떼지 않으므로 부채 갈래의 「주계약 + 복합내재파생」 두 줄을
        # 하나로 접는다. 새로 계산할 값이 없다 — 두 줄의 합이 곧 전체 공정가치다.
        #
        #   주계약(잔여) + 복합내재파생 = (100 + ca) − (b2 − b0) + (b2 − b0)
        #                              = 100 + ca      ← 콜이 별도 금융상품일 때
        #                              = 100           ← 콜을 묶었을 때
        #
        # 매도청구권은 전체 지정 **밖**에 남는다. 제3자에게 지정·양도될 수 있으면
        # 거래상대방이 달라 복합계약의 일부가 아니라 별도의 금융상품이기 때문이다
        # (문단 4.3.1). 지정은 그 계약을 건드리지 못한다.
        whole = (100 + ca) if sep else 100.0
        rows = [("복합계약 전체 · 당기손익-공정가치 측정 금융부채", whole)]
        if sep and (abs(ca) > 1e-12 or not is_rcps(tm)):
            rows.append(("매도청구권 · 파생상품자산", -ca))
        note = ("복합계약 **전체**를 당기손익-공정가치 측정 금융부채로 지정했으므로 "
                "내재파생상품을 분리하지 않고 한 줄로 인식합니다 (기업회계기준서 "
                "제1109호 문단 4.2.2 · 4.3.3(3)). 요소별 배분(주계약 · 파생상품부채 · "
                "전환권대가)을 하지 않으므로 **유효이자율 상각표도 만들지 않습니다** — "
                "후속측정은 전체를 공정가치로 다시 재는 것이고, 상각후원가가 없습니다. "
                "거래원가는 자산·부채에 얹지 못하고 전액 즉시 비용입니다 (문단 5.1.1)."
                + ("" if (is_rcps(tm) and ca <= 1e-12) else
                   " 매도청구권은 제3자 지정이 가능해 **별도의 금융상품**이므로 이 "
                   "지정 밖에 남고, 파생상품자산으로 따로 인식합니다 (문단 4.3.1). "
                   f"복합계약에 배분된 금액은 {whole:,.2f} 입니다."
                   if sep else
                   " 매도청구권은 발행회사만 행사할 수 있어 거래상대방이 그대로이므로 "
                   "내재파생상품이고, 전체 지정 안에 함께 들어갑니다 (문단 4.3.1).")
                + " 후속측정에서 **자기신용위험 변동분은 기타포괄손익**으로 표시해야 "
                  "합니다 (문단 5.7.7) — 이 조서는 그 분해를 하지 않습니다.")
    elif tm.conv_class == "liability":
        # 전환권이 파생상품부채 — 내재파생을 공정가치로 두고 주계약을 잔여로.
        # 전환권과 조기상환권은 상호의존적이라 하나의 복합내재파생상품으로 묶어
        # 전체로서(as a whole) 측정한다 (문단 B4.3.4).
        deriv = (b2 - b0) if sep else (b2 - ca - b0)
        host_acc = (100 + ca) - (b2 - b0)      # 어느 쪽이든 같다
        rows = [("주계약 (잔여)", host_acc),
                ("복합내재파생상품 · 파생상품부채", deriv)]
        if sep and (abs(ca) > 1e-12 or not is_rcps(tm)):
            rows.append(("매도청구권 · 파생상품자산", -ca))
        note = ("전환권이 파생상품부채이므로 전환권과 조기상환권을 하나의 "
                "복합내재파생상품으로 묶어 공정가치로 측정하고 주계약을 잔여로 둡니다 "
                "(기준서 1109 문단 B4.3.4). "
                + ("" if (is_rcps(tm) and ca <= 1e-12) else
                   "매도청구권은 제3자 지정이 가능해 별도의 금융상품이므로 "
                   "이 묶음에 넣지 않습니다 (문단 4.3.1). 전환사채에 배분된 금액은 "
                   f"{100+ca:,.2f} 입니다."
                   if sep else
                   "매도청구권은 발행회사만 행사할 수 있어 거래상대방이 그대로이므로 "
                   "내재파생상품이고, 같은 묶음에 넣어 순액으로 측정합니다 (문단 4.3.1).")
                + f" 이론적 주계약가치는 {b0:,.2f} 입니다.")
    elif not psep:
        # 조기상환권이 주계약과 밀접하게 관련되어 분리하지 않는다. 부채요소를
        # 통째로 상각후원가로 두고, 파생상품부채를 세우지 않는다.
        rows = [("부채요소 (사채 + 조기상환권)", b1)]
        if abs(cad) > 1e-12 or not is_rcps(tm):
            rows.append(("매도청구권 · 파생상품자산", -cad))
        rows.append(("전환권대가 · 자본", 100-b1+cad))
        note = ("기업회계기준서 제1032호 문단 31 — 부채요소를 먼저 정하고 나머지를 자본에 "
                "배분합니다. 최초 인식에는 손익이 생기지 않습니다. "
                "조기상환청구권은 주계약과 밀접하게 관련되어 분리하지 않으므로 "
                "(제1109호 문단 4.3.3·B4.3.5(5)(가)) 부채요소에 포함해 상각후원가로 "
                f"측정합니다. 분리했다면 파생상품부채로 세웠을 금액은 {b1-b0:,.2f} "
                "입니다 — 분리하지 않으므로 인식하지 않고, 유효이자율에 녹아 듭니다.")
    else:
        # 계약에 조기상환청구권이 없으면 0 짜리 줄을 남기지 않는다 — 있는 것처럼
        # 보이면 조서를 읽는 사람이 헷갈린다.
        _hasput = tm.p_s <= tm.p_e and abs(b1 - b0) > 1e-9
        rows = [("주계약 (옵션 없는 사채)", b0)]
        if _hasput or not sep:
            rows.append(("조기상환청구권 · 파생상품부채",
                         (b1-b0) if sep else (b1-b0-cad)))
        if sep and (abs(cad) > 1e-12 or not is_rcps(tm)):
            rows.append(("매도청구권 · 파생상품자산", -cad))
        if not sep:
            rows[1] = ("복합내재파생상품 · 파생상품부채", b1-b0-cad)
        rows.append(("전환권대가 · 자본", 100-b1+cad))
        note = ("기업회계기준서 제1032호 문단 31 — 부채요소를 먼저 정하고 나머지를 자본에 배분합니다. "
                "최초 인식에는 손익이 생기지 않습니다."
                + ("" if sep else
                   " 매도청구권은 발행회사만 행사할 수 있어 내재파생상품이므로 "
                   "조기상환권과 하나로 묶어 순액으로 봅니다 (문단 4.3.1 · B4.3.4)."))
    rows.append(("합계", sum(v for _, v in rows)))
    if tm.elapsed_m > 0.01:
        note += ("  ※ 이 배분은 **최초 인식**용입니다. 평가기준일이 발행일보다 뒤이므로 "
                 "결산 회계처리에는 그대로 쓰지 마십시오. 결산일에 필요한 것은 파생상품의 "
                 "공정가치뿐이고, 주계약은 발행일 배분액을 유효이자율로 상각한 장부금액입니다.")
    return rows, note


def acc_host(tm: Terms, full, b0, b1, b2, ca):
    """상각표가 출발해야 하는 금액 — 실제로 인식한 주계약이다.

    자본 분류면 이론적 주계약(b0)이 그대로 인식되지만, 부채 분류면 잔여로
    떨어진 금액이 인식된다. 상각후원가는 최초 인식액에서 출발해야 하므로
    이론값이 아니라 배분액으로 유효이자율을 역산한다.

    복합계약 전체를 당기손익-공정가치로 지정했으면 **상각할 대상이 없다.** 그때는
    ``None`` 을 돌려주고, 부르는 쪽이 상각표를 만들지 않는다. 0 을 돌려주면
    「상각후원가가 0 인 사채」라는 없는 물건이 조서에 실린다.
    """
    if fvpl_on(tm): return None
    rows = allocate(tm, full, b0, b1, b2, ca)[0]
    # 거래원가 중 부채요소 몫은 부채에서 차감하므로 상각도 그만큼 낮은 데서 출발한다.
    host = rows[0][1] - cost_host(tm, rows)
    # 발행가 100 과 공정가치가 크게 다르면(최초 인식 차이) 부채 분류의 잔여 주계약이
    # 0 이하로 떨어진다. 유효이자율이 정의되지 않으므로 상각표를 만들지 않는다 —
    # 만들면 이분법이 상한에 걸린 엉터리 표가 조서에 실린다. 화면이 그 사실을 적는다.
    if host <= 0: return None
    return host


def eir_or_none(tm: Terms, full, b0, b1, b2, ca):
    """조서에 실을 상각표. 전체 지정이면 ``None`` — 상각할 대상이 없다.

    조서 두 개가 같은 판단을 하도록 한 자리에 모아 둔다.
    """
    host = acc_host(tm, full, b0, b1, b2, ca)
    return None if host is None else eir_table(tm, host)


def model_checks(tm: Terms, full, b0, b1, b2, ca, eir=None):
    """조서와 화면이 함께 싣는 검산 표. [(항목, 값, 판정, 설명)].

    판정은 넷 — 적합 · 확인 필요 · 한계(모형 성질이라 결함이 아님) · 해당 없음.
    «설명» 이 아니라 격자를 실제로 훑어 센 결과만 적는다. 시험(분기전수·조합시험)이
    같은 불변식을 쓴다 — 조서에 실린 검산과 시험이 어긋나면 그것이 결함이다.
    """
    out = []
    bw = bw_cash(tm)
    mis = ill = 0; neg = 0.0
    for o in full["memo"].values():
        kd = o["kind"]
        if kd in ("conv", "auto", "ipo") and abs(o["B"]) > 1e-9: mis += 1
        if (not bw) and kd in ("put", "call", "mat") and abs(o["E"]) > 1e-9: mis += 1
        if kd == "put" and o.get("pv", 0.0) <= 0: ill += 1
        if kd == "call" and o.get("kv", math.inf) == math.inf: ill += 1
        if kd in ("conv", "auto", "ipo") and o.get("cv", 0.0) <= 0: ill += 1
        neg = min(neg, o["E"], o["B"])
    N = len(full["memo"])
    out.append(("결정 ↔ 지분·부채 배정", f"어긋난 노드 {mis} / {N:,}", "적합" if (mis == 0 and neg >= -1e-9) else "확인 필요",
                "전환이면 부채 0, 상환이면 지분 0. 음수 노드 없음" if neg >= -1e-9 else f"음수 노드 있음 (최소 {neg:,.4f})"))
    out.append(("행사 불가능한 자리의 결정", f"어긋난 노드 {ill} / {N:,}", "적합" if ill == 0 else "확인 필요",
                "계약일 이후 첫 노드부터 주기마다만 열린다"))
    rt = full["memo"][full["root"]]
    ok_root = abs(full["TF"] - (rt["E"] + rt["B"])) < 1e-9 and abs(full["GS"] - rt["V"]) < 1e-9
    out.append(("뿌리 노드 = 결과 (두 모형)", f"TF {full['TF']:,.4f} · GS {full['GS']:,.4f}", "적합" if ok_root else "확인 필요",
                "지분+부채 = TF, V = GS. V ≠ 지분+부채 는 결함이 아니다"))
    out.append(("위험중립가중치 q", f"[{full['qmin']:.4f}, {full['qmax']:.4f}]", "적합" if not full["qbad"] else "확인 필요",
                "전 구간 (0, 1) 안" if not full["qbad"] else f"벗어난 구간 {len(full['qbad'])}개 — 화면은 계산을 멈춘다"))
    cad = full.get("ca_debt", ca) if is_rcps(tm) else ca
    forced_conv = auto_conv(tm) or (is_rcps(tm) and int(tm.ipo_on) and int(tm.ipo_conv))
    bdt = put_bdt_on(tm)
    pv_, cv_ = b1 - b0, b2 - b1
    rights = []
    if bdt: rights.append(("조기상환청구권", pv_, "한계", "BDT 부채요소는 다른 모형이라 B0 와 견주지 않는다"))
    else:   rights.append(("조기상환청구권", pv_, "적합" if pv_ >= -1e-7 else "확인 필요", ""))
    if bdt: rights.append(("전환권", cv_, "한계", "BDT 부채요소는 다른 모형이라 B2 와 견주지 않는다"))
    elif cv_ < -1e-7 and forced_conv: rights.append(("전환권", cv_, "한계", "자동전환·강제전환은 권리가 아니라 의무 — 음수 허용"))
    else:   rights.append(("전환권", cv_, "적합" if cv_ >= -1e-7 else "확인 필요", ""))
    if cad < -1e-7:
        r3 = engine(tm, conv=True, put=True, call=True, conv_start=max(tm.cv_s, tm.k_lock))
        fc = r3["dist"].get("conv_called", 0.0)
        rights.append(("매도청구권", cad, "한계" if fc > 0 else "확인 필요",
                       f"강제전환 확률 {fc:.4f} — 콜이 전환을 강제하면 할인이 가벼워져 값이 오른다 (모형 성질)" if fc > 0 else ""))
    else:
        rights.append(("매도청구권", cad, "적합", ""))
    for nm, v, vd, why in rights:
        out.append((f"권리 값 ≥ 0 · {nm}", f"{v:,.4f}", vd, why))
    D = full["dist"]; ds = D["conv"] + D["put"] + D["call"] + D["mat"]
    out.append(("정산 분포 합", f"{ds:.10f}", "적합" if abs(ds - 1) <= 1e-9 else "확인 필요", "전환 + 조기상환 + 매도청구 + 만기 = 1"))
    rows, _ = allocate(tm, full, b0, b1, b2, ca)
    tot = sum(v for _, v in rows[:-1])
    out.append(("배분표 합 = 100", f"{tot:.10f}", "적합" if abs(tot - 100) <= 1e-9 else "확인 필요", "발행대가를 요소에 남김없이 배분"))
    dr = 100.0 + sum(-v for _, v in rows[:-1] if v < 0); cr = sum(v for _, v in rows[:-1] if v > 0)
    out.append(("분개 차대 균형", f"차 {dr:,.4f} = 대 {cr:,.4f}", "적합" if abs(dr - cr) <= 1e-9 else "확인 필요",
                "현금 + 파생상품자산 = 부채·자본 요소"))
    if tm.issue_cost and tm.issue_cost > 0:
        parts, cost = cost_split(tm, rows)
        sm = sum(c for _, _, c, _ in parts)
        out.append(("거래원가 배분 합 = 원가", f"{sm:.6f} = {cost:.6f}", "적합" if abs(sm - cost) <= 1e-9 else "확인 필요", "1032 문단 38 비례 배분"))
    if eir is None:
        out.append(("상각표 기말 = 상환금액", "상각표 없음", "해당 없음",
                    "복합계약 전체 FVPL 지정" if fvpl_on(tm) else "잔여 주계약 ≤ 0 (Day-1 차이)"))
    else:
        r_, arows, red_, _ = eir
        end = arows[-1][5]
        out.append(("상각표 기말 = 상환금액", f"{end:,.6f} = {red_:,.6f}", "적합" if abs(end - red_) <= 1e-6 else "확인 필요",
                    f"유효이자율 {r_:.4%}"))
    notes = getattr(tm, "forced_notes", [])
    out.append(("되돌린 설정 (지원하지 않는 조합)", f"{len(notes)}건", "해당 없음" if not notes else "확인 필요",
                "; ".join(f"{k} → {v}" for k, v, _ in notes) if notes else "없음"))
    return out


def sha_checks(tm: Terms, R):
    """주주간계약 조서의 검산 표. 형식은 model_checks 와 같다."""
    out = []
    n = R["n"]
    out.append(("풋 ≥ 0", f"{R['put']:,.4f}", "적합" if R["put"] >= -1e-7 else "확인 필요", ""))
    out.append(("콜 ≥ 0", f"{R['call']:,.4f}", "적합" if R["call"] >= -1e-7 else "확인 필요", ""))
    pmax = max(R["pk"](i) for i in range(n + 1))
    out.append(("풋 ≤ 최대 행사금액", f"{R['put']:,.4f} ≤ {pmax:,.4f}", "적합" if R["put"] <= pmax + 1e-7 else "확인 필요", "지분 ≥ 0 이므로"))
    out.append(("위험중립가중치 q", f"[{R['qmin']:.4f}, {R['qmax']:.4f}]", "적합" if not R["qbad"] else "확인 필요", ""))
    if int(tm.sha_kill):
        co = sum(1 for i in range(n + 1) for j in range(i + 1)
                 if R["KIND"][i][j] in ("put", "call") and R["P"][i][j] > 1e-12 and R["C"][i][j] > 1e-12)
        out.append(("상호소멸 — 행사 노드에 두 권리가 함께 남지 않음", f"{co}", "적합" if co == 0 else "확인 필요", ""))
    else:
        out.append(("상호소멸", "끔", "해당 없음", "두 권리를 독립으로 잰다"))
    out.append(("적격상장 스텝", f"{R['qi_step']}", "해당 없음" if R["qi_step"] < 0 else "적합",
                "" if R["qi_step"] < 0 else f"주가 > {tm.ipo_min:,.0f} 인 노드에서 풋 소멸" + (" · 콜도 소멸" if int(tm.sha_qipo_kill) else "")))
    return out


def write_check_sheets(wb, tm: Terms, checks, after="결과"):
    """조서에 «검산요약» 과 «99_모형검증» 두 장을 더한다.

    검산요약은 이 계약을 실제로 훑은 결과이고, 99_모형검증은 모형 자체의 알려진 한계와
    검증 프로그램(시험 목록·매트릭스 요약)이다. 값 조서·수식 조서·주주간계약 조서가
    같은 함수를 부른다.
    """
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    thin = Side(style="thin", color=RPT["hair"])
    def put(ws, r, c, v, *, bold=False, size=10, color=None, fill=None, wrap=False, border=False):
        cl = ws.cell(row=r, column=c, value=xlfn(v))
        cl.font = Font(name="맑은 고딕", size=size, bold=bold, color=color or RPT["ink"])
        if fill: cl.fill = PatternFill("solid", fgColor=fill)
        cl.alignment = Alignment(vertical="top", wrap_text=wrap)
        if border: cl.border = Border(top=thin, bottom=thin, left=thin, right=thin)
        return cl
    tone = {"적합": RPT["green"], "확인 필요": RPT["red"], "한계": RPT["amber"], "해당 없음": RPT["grey"]}

    C = wb.create_sheet("검산요약"); C.sheet_view.showGridLines = False
    for col, w in (("A", 2), ("B", 38), ("C", 30), ("D", 11), ("E", 70)): C.column_dimensions[col].width = w
    put(C, 2, 2, "검산요약 — 이 격자를 실제로 훑은 결과", bold=True, size=14)
    put(C, 3, 2, "«설명» 이 아니라 셈한 결과다. 판정이 «확인 필요» 인 줄이 하나라도 있으면 값을 쓰기 전에 원인을 찾아야 한다. "
        "«한계» 는 모형 성질이라 결함이 아니다 — 99_모형검증 시트에 이유가 있다.", size=9, color=RPT["grey"], wrap=True)
    C.merge_cells(start_row=3, start_column=2, end_row=3, end_column=5); C.row_dimensions[3].height = 30
    for i, h in enumerate(["항목", "값", "판정", "설명"]):
        put(C, 5, 2 + i, h, bold=True, fill=RPT["band"], border=True)
    r = 6
    for nm, val, vd, why in checks:
        put(C, r, 2, nm, border=True); put(C, r, 3, val, border=True)
        put(C, r, 4, vd, bold=True, color=tone.get(vd, RPT["ink"]), border=True)
        put(C, r, 5, why, size=9, color=RPT["grey"], border=True, wrap=True); r += 1
    bad = [nm for nm, _, vd, _ in checks if vd == "확인 필요"]
    put(C, r + 1, 2, ("모든 항목 적합" if not bad else "확인 필요 " + str(len(bad)) + "건: " + ", ".join(bad)),
        bold=True, color=(RPT["green"] if not bad else RPT["red"]))
    C.sheet_properties.tabColor = RPT["green"] if not bad else RPT["red"]
    if after in wb.sheetnames:
        wb.move_sheet("검산요약", offset=wb.sheetnames.index(after) + 1 - wb.sheetnames.index("검산요약"))

    V = wb.create_sheet("99_모형검증"); V.sheet_view.showGridLines = False
    for col, w in (("A", 2), ("B", 40), ("C", 90), ("D", 30)): V.column_dimensions[col].width = w
    put(V, 2, 2, "99_모형검증 — 모형의 알려진 한계와 검증 프로그램", bold=True, size=14)
    put(V, 3, 2, "이 조서를 낸 앱이 무엇을 못 하는지와, 그 앱이 어떤 시험을 통과했는지를 적는다. "
        "한계 표는 화면 검산 탭·README 「한계」 와 같은 원본(MODEL_LIMITS)에서 나온다.", size=9, color=RPT["grey"], wrap=True)
    V.merge_cells(start_row=3, start_column=2, end_row=3, end_column=4); V.row_dimensions[3].height = 30
    r = 5
    put(V, r, 2, "1. 알려진 한계", bold=True, fill=RPT["band"]); put(V, r, 3, "", fill=RPT["band"]); put(V, r, 4, "", fill=RPT["band"]); r += 1
    for i, h in enumerate(["한계", "설명", "실리는 곳"]): put(V, r, 2 + i, h, bold=True, border=True)
    r += 1
    for nm, why, where in MODEL_LIMITS:
        put(V, r, 2, nm, border=True, wrap=True); put(V, r, 3, why, size=9, border=True, wrap=True)
        put(V, r, 4, " · ".join(where), size=9, color=RPT["grey"], border=True, wrap=True); r += 1
    r += 1
    put(V, r, 2, "2. 검증 프로그램 (tests/)", bold=True, fill=RPT["band"]); put(V, r, 3, "", fill=RPT["band"]); put(V, r, 4, "", fill=RPT["band"]); r += 1
    for i, h in enumerate(["시험", "무엇을 보나", "명령"]): put(V, r, 2 + i, h, bold=True, border=True)
    r += 1
    for nm, what, cmd in VERIFY_SUITES:
        put(V, r, 2, nm, border=True); put(V, r, 3, what, size=9, border=True, wrap=True)
        put(V, r, 4, cmd, size=9, color=RPT["grey"], border=True); r += 1
    r += 1
    put(V, r, 2, "3. 기능 매트릭스 요약", bold=True, fill=RPT["band"]); put(V, r, 3, "", fill=RPT["band"]); put(V, r, 4, "", fill=RPT["band"]); r += 1
    try:
        _mx = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "검증매트릭스.json"), encoding="utf-8"))
        _rows = _mx["rows"] if isinstance(_mx, dict) else _mx
        _sum = {}
        for x in _rows: _sum[(x["product"], x["status"])] = _sum.get((x["product"], x["status"]), 0) + 1
        prods = sorted({x["product"] for x in _rows}, key=lambda p: ["CB", "RCPS", "BW", "SHA", "ALL"].index(p) if p in ("CB", "RCPS", "BW", "SHA", "ALL") else 9)
        for i, h in enumerate(["상품", "PASS · BLOCK · LIMIT · FAIL · NOT_TESTED", "기준"]): put(V, r, 2 + i, h, bold=True, border=True)
        r += 1
        for pr in prods:
            g = lambda st_: _sum.get((pr, st_), 0)
            put(V, r, 2, pr, border=True)
            put(V, r, 3, f"{g('PASS')} · {g('EXPECTED_BLOCK')} · {g('KNOWN_LIMITATION')} · {g('FAIL')} · {g('NOT_TESTED')}", border=True)
            put(V, r, 4, (_mx.get("head", "") if isinstance(_mx, dict) else ""), size=9, color=RPT["grey"], border=True); r += 1
    except Exception as _e:
        put(V, r, 2, "매트릭스 파일이 없다 — python3 tests/기능목록.py 로 만든다", size=9, color=RPT["grey"]); r += 1
    r += 1
    put(V, r, 2, "4. 기준선 (docs/검증기준선.md)", bold=True, fill=RPT["band"]); put(V, r, 3, "", fill=RPT["band"]); put(V, r, 4, "", fill=RPT["band"]); r += 1
    put(V, r, 2, "기본 계약 (carry=1)", border=True); put(V, r, 3, "주계약 37.5208 · 부채요소 73.1837 · 전체 114.8781 · 매도청구권 12.2404", border=True); r += 1
    put(V, r, 2, "허용오차", border=True); put(V, r, 3, "이자율·확률 1e-12 · 금액(100 기준) 1e-6 · 조서 대 엔진 1e-4 · 역산 0.25 — docs/골든값과_허용오차.md", size=9, border=True, wrap=True); r += 1
    V.sheet_properties.tabColor = RPT["sub"]
    return C, V


# 조서 99_모형검증 시트와 run_all.py 가 같은 목록을 쓴다
VERIFY_SUITES = (
    ("손계산대조", "계약에서 손으로 센 기대값 대 엔진 (독립)", "python3 tests/손계산대조.py"),
    ("오라클", "app.py 를 보지 않고 쓴 닫힌 식 대 엔진 (독립)", "python3 tests/오라클.py"),
    ("분기전수", "선택 가능한 모든 갈래 × 불변식", "python3 tests/분기전수.py"),
    ("조합시험", "pairwise · 지정 3-way · seed 무작위 · 단조성", "python3 tests/조합시험.py"),
    ("배선대조", "수식 조서 머리 행이 가정 시트를 바르게 참조하나", "python3 tests/배선대조.py"),
    ("값조서대조", "값 조서 대 엔진", "python3 tests/값조서대조.py"),
    ("주주간계약대조", "주주간계약 수식·값 조서 대 엔진", "python3 tests/주주간계약대조.py"),
    ("조서대조", "수식 조서를 실제로 풀어 엔진과 대조", "python3 tests/조서대조.py"),
    ("설정전수대조", "설정을 하나씩 흔들어 조서가 따라오나", "python3 tests/설정전수대조.py"),
    ("리포트대조", "변동성·이자율 부속 리포트 대 엔진", "python3 tests/리포트대조.py"),
    ("기능목록", "기능 명세와 커버리지 매트릭스", "python3 tests/기능목록.py --strict"),
)


def cost_split(tm: Terms, rows):
    """거래원가를 요소별로 배분한다 (1032 문단 38).

    > 복합금융상품 발행과 관련된 거래원가는 **배분된 발행금액에 비례하여**
    > 부채요소와 자본요소로 배분한다.

    비율의 분모는 복합금융상품에 배분된 금액이다. 매도청구권 자산은 별도의
    금융상품이라(문단 4.3.1) 복합금융상품이 아니므로 분모에서 뺀다.

    배분한 몫의 처리는 그 요소에 적용하는 회계원칙을 따른다.

    * 주계약·부채요소 — 상각후원가라 부채에서 차감하고 유효이자율에 녹인다
    * 파생상품부채 — 당기손익-공정가치라 거래원가를 얹을 자리가 없어 즉시 비용
      (제1109호 문단 5.1.1)
    * 전환권대가 — 자본에서 직접 차감

    복합계약 **전체**를 당기손익-공정가치로 지정했으면 얹을 자리가 아예 없어
    **전액 즉시 비용**이다 (제1109호 문단 5.1.1). 요소별 배분 자체가 없어진다.

    돌려주는 것은 [(항목, 배분액, 몫, 처리)] 와 합계다.
    """
    _fv = fvpl_on(tm)
    cost = max(0.0, float(tm.issue_cost or 0.0))/max(1e-9, tm.face_total)*100
    base = [(k, v) for k, v in rows[:-1] if v > 0]
    tot = sum(v for _, v in base) or 1.0
    out = []
    for k, v in base:
        how = ("자본에서 차감" if "자본" in k else
               "즉시 비용 (당기손익-공정가치)" if (_fv or "파생상품부채" in k) else
               "부채에서 차감 — 유효이자율에 반영")
        out.append((k, v, cost*v/tot, how))
    return out, cost


def cost_host(tm: Terms, rows) -> float:
    """주계약(부채요소)에 배분된 거래원가 몫. 상각표가 여기서 출발한다.

    라벨을 다시 해석하지 않고 ``cost_split`` 이 정한 **처리**를 따른다 — 한 곳에서만
    판단해야 갈래가 늘어도 어긋나지 않는다. 전체 지정이면 전액이 즉시 비용이라
    여기 걸리는 줄이 없어 0 이다.
    """
    return sum(c for _, _, c, how in cost_split(tm, rows)[0]
               if how.startswith("부채에서 차감"))


def allocate_full(tm: Terms, rows):
    """100 기준 배분에 전액 기준 금액을 붙인다."""
    f = tm.face_total
    return [(k, v, v/100*f) for k, v in rows]


def pay_index(tm: Terms, t_year: float) -> int:
    """상각 회차의 연수를 계약상 지급 회차 번호로 되짚는다.

    t 는 평가기준일 기준이므로 경과분을 더해 발행일 기준으로 옮긴 뒤
    지급주기로 나눈다. 지급일이 발행일 + m×주기 이므로 m 이 나온다.
    """
    per = max(1e-6, tm.ipay/12)
    return max(1, int(round((t_year + tm.elapsed_m/12)/per)))


def eir_table(tm: Terms, host):
    c = 100*eff_cpn(tm)*tm.ipay/12
    per = max(1e-6, tm.ipay/12)
    red = 100*(1 + accrue_rate(tm.T + tm.elapsed_m/12, tm.ytm, eff_cpn(tm), tm.ytm_cmp))
    # 지급일은 계약상 일정이므로 **발행일**부터 센다. 평가기준일이 발행일보다
    # 뒤이면 첫 회차만 짧아지고 나머지는 온전한 한 주기다. 평가기준일에서
    # 세면 지급일이 계약과 어긋나 이자비용이 회차마다 밀린다.
    # 마지막은 만기다. 남는 조각이 주기의 10% 미만이면 앞 회차에 붙여
    # 하루짜리 회차를 만들지 않는다.
    ey_ = tm.elapsed_m/12
    ts, k = [], 1
    while k*per - ey_ < tm.T - per*0.1:
        t_ = k*per - ey_
        if t_ > per*0.1: ts.append(t_)
        k += 1
    ts.append(tm.T)
    nper = len(ts)
    def pv(r):
        return (sum(c*(1+r)**(-t) for t in ts[:-1])
                + (c + red)*(1+r)**(-tm.T))
    # 상한은 넉넉히 잡되 고정하지 않는다 — 만기 한두 달 앞의 중간평가는 연 환산 유효이자율이
    # 수백 % 를 넘을 수 있고, 상한에 걸리면 상각표가 엉뚱한 곳에서 끝난다. 상한에서도
    # 현재가치가 장부금액을 넘으면 상한을 네 배씩 올린다 (1e4 = 연 1,000,000%).
    lo, hi = -0.99, 5.0
    while pv(hi) > host and hi < 1e7: hi *= 4
    for _ in range(200):
        m = (lo+hi)/2
        if pv(m) > host: lo = m
        else: hi = m
    r = (lo+hi)/2
    rows, bv, prev = [], host, 0.0
    for k, t in enumerate(ts, 1):
        it = bv*((1+r)**(t-prev) - 1); end = bv + it - c
        rows.append((k, t, bv, it, c, end)); bv, prev = end, t
    return r, rows, red, nper


def pc_overlap(tm: Terms):
    """조기상환청구권과 매도청구권이 **같은 노드에서 함께 열리는** 스텝을 찾는다.

    격자 안에서 두 권리가 부딪히는 자리다. 어느 쪽이 먼저인지는 계약이 정하는데
    (``pc_order``), 두 행사금액이 같으면 어느 쪽을 골라도 답이 같다. **조기상환금액이
    매도청구금액보다 큰 스텝이 하나라도 있을 때만** 값이 갈린다 — 그 자리를 짚어
    준다.

    돌려주는 것은 [(스텝, 발행일 기준 개월, 조기상환금액, 매도청구금액)] 이다.
    """
    n = max(1, int(tm.n)); dt_ = tm.T/n
    mper = n/(tm.T*12); ey = tm.elapsed_m/12
    lo, hi = step_mapper(tm, n, dt_)

    def opened(i, a, b, fr):
        s0, s1 = lo(a), hi(b)
        if i < max(s0, 0) or i > s1: return False
        per = max(1, int(round(fr*mper)))
        return (i - s0) % per == 0

    out = []
    for i in range(n+1):
        if not (opened(i, tm.p_s, tm.p_e, tm.p_f)
                and opened(i, tm.k_s, tm.k_e, tm.k_f)):
            continue
        t_ = i*dt_ + ey
        pv = (100*(1 + accrue_rate(t_, tm.p_yield, eff_cpn(tm), tm.p_cmp))
              if tm.p_mode == "accrue" else tm.p_rate)
        kv = 100*(1 + accrue_rate(t_, tm.k_prem, eff_cpn(tm), tm.k_cmp))
        out.append((i, t_*12, pv, kv))
    return out


def validate(tm: Terms):
    derive(tm)
    w = [f"설정을 되돌렸습니다 ({k} → {v}). " + msg.replace("**", "")
         for k, v, msg in getattr(tm, "forced_notes", [])]
    if tm.cv_s >= tm.cv_e: w.append("전환 시작이 종료보다 늦거나 같습니다.")
    horizon = tm.T*12 + tm.elapsed_m + 0.5      # 발행일 기준 총 개월
    if tm.cv_e > horizon: w.append(f"전환 종료({tm.cv_e:.0f}개월)가 만기({horizon:.0f}개월)를 넘습니다.")
    if tm.p_s > tm.p_e: w.append("조기상환 시작이 종료보다 늦습니다.")
    if is_rcps(tm):
        _lo, _hi = step_mapper(tm, int(tm.n), tm.T/int(tm.n))
        if tm.p_s > tm.p_e or _lo(tm.p_s) > _hi(tm.p_e):
            # 「발행자만 상환권 = 자본」으로 단정하면 안 된다. 1032 AG25·AG26 은
            # 상환의무가 없는 우선주의 분류를 **다른 권리를 종합해** 판단하라고 하고,
            # AG26 이 직접 말하는 것은 「배당이 발행자 재량이면 자본」이다. 비재량
            # 현금배당이 부채가 되는지는 금융부채 정의(문단 11)와 계약의 실질에서
            # 나오는 결론이지 AG26 의 문언이 아니다.
            w.append("**투자자 상환청구권이 확인되지 않습니다.** 발행회사에게만 있는 "
                     "상환권은 그 자체로 금융부채를 발생시키지 않습니다 "
                     "(기준서 1032 AG25·AG26). 다만 배당의 지급재량, 조건부 현금결제, "
                     "전환 시 교부할 주식 수 등 다른 계약조건을 함께 보아야 하므로 "
                     "**이 사실만으로 자본 분류를 확정할 수 없습니다.** 자본으로 "
                     "결론이 나면 이 앱의 부채요소 → 잔여 배분은 그 계약에 맞지 않으니 "
                     "배분표를 조서에 그대로 옮기지 마십시오. 상환청구 기간을 "
                     "확인하십시오.")
        if int(tm.issuer_call) == 2 and tm.k_w <= 0:
            w.append("**제3자 지정 매도청구권의 행사 한도가 0%** 입니다. 계약서의 "
                     "콜옵션 대상주식 비율(예: 총 발행금액의 20%)을 넣으십시오. "
                     "0 이면 콜이 없는 것과 같습니다.")
        if int(tm.div_mode) == 1 and tm.cpn > 0:
            w.append(f"우선배당 {tm.cpn:.2%} 를 발행자 재량으로 두어 부채 계산에서 뺐습니다. "
                     "계약이 「미지급 배당을 상환가액에 가산」이면 첫 번째 갈래로 바꾸십시오 "
                     "(1032 AG37).")
    if is_bw(tm):
        if int(tm.bw_pay) == 0 and tm.cv_e > horizon - 0.5 + 1e-9 and int(tm.bw_detach) == 0:
            w.append("비분리형인데 신주인수권 행사기간이 사채 만기까지 걸쳐 있습니다. "
                     "만기에 사채가 소멸하면 신주인수권도 함께 소멸하므로, 계약서의 "
                     "행사 종료일이 만기 **전**인지 확인하십시오.")
        if int(tm.bw_pay) == 0 and tm.ytm > 0:
            w.append(f"현금납입형이라 신주인수권을 행사해도 사채가 남습니다. 그래서 "
                     f"만기보장수익률 {tm.ytm:.2%} 가 붙은 상환금액이 **모든 경로에서** "
                     "지급됩니다 — 대용납입형과 달리 상환할증금이 사라지지 않으므로 "
                     "부채요소가 그만큼 큽니다. 계약서의 납입 방식을 확인하십시오.")
        if int(tm.bw_pay) == 1 and int(tm.bw_detach) == 1:
            w.append("대용납입형에는 분리·비분리 구분이 격자에 남기는 흔적이 없어 "
                     "비분리형으로 계산했습니다 — 사채를 납입에 갈음하므로 사채와 "
                     "신주인수권이 함께 소멸합니다.")
        if int(tm.bw_pay) == 0 and tm.k_w > 0:
            w.append("**매도청구권을 «사채»에 대한 권리로 재고 있습니다.** 현금납입형 "
                     "BW 의 콜옵션이 계약서상 «신주인수권증권»을 되사는 권리라면 "
                     "기초자산이 달라 이 값이 맞지 않습니다 — 그때는 매도청구 한도를 "
                     "0 으로 두고 신주인수권증권 매도청구권을 별도로 평가해 조서에 "
                     "붙이십시오. 계약서의 콜옵션 대상이 무엇인지 확인하십시오.")
        if tm.rfx_mode > 0 and tm.conv_class == "equity":
            w.append("행사가격 조정(리픽싱)이 있는데 신주인수권을 **자본**으로 두었습니다. "
                     "발행할 주식 수가 확정되지 않으면 「확정 수량 ↔ 확정 금액」 요건을 "
                     "충족하지 못해 파생상품부채가 됩니다 (1032 문단 16(2)(나)). "
                     "모든 주주에게 동등하게 적용되는 희석방지조항이라 자본으로 본다면 "
                     "그 근거를 조서에 남기십시오.")
    if int(tm.fvpl_whole) and not is_sha(tm):
        if tm.conv_class == "equity":
            w.append("**복합계약 전체를 당기손익-공정가치로 지정**하셨는데 전환권을 "
                     "**자본**으로 두었습니다. 자본요소가 있는 복합금융상품은 전체를 "
                     "당기손익-공정가치로 지정할 수 없습니다 — 제1109호 문단 4.2.2 는 "
                     "**금융부채**에만 지정을 허용하고, 자본요소는 금융부채가 아닙니다. "
                     "전환권 회계 분류를 파생상품부채로 바꾸시거나 지정을 해제하십시오.")
        elif fvpl_on(tm):
            w.append("복합계약 전체를 당기손익-공정가치로 지정하셨습니다. 배분표가 "
                     "**한 줄**이 되고 유효이자율 **상각표를 만들지 않습니다** — "
                     "상각후원가로 남는 주계약이 없기 때문입니다. 거래원가는 전액 "
                     "즉시 비용입니다 (문단 5.1.1). 후속측정에서 **자기신용위험 "
                     "변동분은 기타포괄손익**으로 표시해야 하는데(문단 5.7.7) 이 앱은 "
                     "그 분해를 하지 않습니다 — 신용스프레드를 전기말 값으로 고정해 "
                     "다시 재고 그 차이를 직접 나누셔야 합니다.")
    if tm.k_s > tm.k_e: w.append("매도청구 시작이 종료보다 늦습니다.")
    # 두 권리가 같은 노드에서 부딪히는 자리. 행사금액이 같으면 어느 우선순위든
    # 답이 같으므로, 조기상환금액이 더 큰 스텝이 있을 때만 알린다.
    if tm.k_w > 0 and not is_sha(tm):
        _ov = [x for x in pc_overlap(tm) if x[2] > x[3] + 1e-9]
        if _ov:
            _i, _m, _pv, _kv = _ov[0]
            w.append(
                f"**조기상환청구권과 매도청구권이 {len(_ov)}개 노드에서 함께 열리고, "
                f"그 자리의 조기상환금액이 매도청구금액보다 큽니다.** 처음 부딪히는 "
                f"곳은 발행일 기준 {_m:,.0f}개월(스텝 {_i})이고 조기상환 {_pv:,.4f} 대 "
                f"매도청구 {_kv:,.4f} 입니다. 이럴 때는 **누가 먼저 행사하는가**에 "
                "따라 값이 갈립니다 — 지금 설정은 "
                + ("「발행자 매도청구 우선」" if int(tm.pc_order) == 1
                   else "「투자자 조기상환 우선」")
                + " 입니다. 계약서의 통지기간과 우선순위 조항을 확인하시고, 고른 "
                  "근거를 조서에 남기십시오. (매도청구권 칸에서 바꿉니다)")
    if tm.k_lock < tm.k_e and not issuer_redeem(tm) and tm.k_w > 0:
        w.append("의무보유 전환지연이 매도청구 종료보다 이릅니다. 콜이 실효화될 수 있습니다.")
    # 할증금 산식은 보장수익률에서 표면이자율을 뺀다. 보장이 더 낮으면 음수가
    # 되어 상환금액이 액면 밑으로 내려간다. 0 에서 끊고는 있지만 입력 자체가
    # 계약과 맞지 않으므로 알려 준다.
    if tm.ytm > 0 and tm.ytm < eff_cpn(tm) - 1e-9:
        w.append(f"만기보장수익률({tm.ytm:.2%})이 표면이자율({eff_cpn(tm):.2%})보다 낮습니다. "
                 "상환할증금이 음수가 되어 0 으로 끊었습니다. 계약서를 확인하십시오.")
    if tm.p_mode == "accrue" and tm.p_yield > 0 and tm.p_yield < eff_cpn(tm) - 1e-9:
        w.append(f"조기상환 보장수익률({tm.p_yield:.2%})이 표면이자율({eff_cpn(tm):.2%})보다 "
                 "낮습니다. 조기상환금액이 액면 밑으로 내려갑니다.")
    if tm.k_w > 0 and tm.k_prem > 0 and tm.k_prem < eff_cpn(tm) - 1e-9:
        w.append(f"매도청구 프리미엄({tm.k_prem:.2%})이 표면이자율({eff_cpn(tm):.2%})보다 "
                 "낮습니다. 매도청구금액이 액면 밑으로 내려갑니다.")
    # 신용스프레드가 너무 얇으면 곡선을 잘못 골랐다는 신호다. 실제로 고시표에서
    # 「특수채·공사채 AAA」 줄을 눌러 국고채와 사실상 같은 곡선이 들어간 일이
    # 있었다. 주계약이 부풀고 전환권대가가 그만큼 깎인다.
    try:
        _RF, _CR = curves(tm)
        _sp = _CR(tm.T) - _RF(tm.T)
        if _sp < 0:
            w.append(f"위험 곡선이 무위험 곡선보다 **낮습니다** (잔존만기 "
                     f"{tm.T:.2f}년에서 {_sp*100:+.2f}%p). 두 곡선을 바꿔 "
                     "넣으셨는지 확인하십시오.")
        elif _sp < 0.01:
            w.append(f"신용스프레드가 잔존만기 {tm.T:.2f}년에서 "
                     f"**{_sp*10000:.0f}bp** 밖에 안 됩니다"
                     + (f" (위험 곡선 출처 — {tm.cr_src})" if tm.cr_src else "")
                     + ". 국채·특수채·공사채 줄을 고르셨을 수 있습니다. "
                       "발행회사 신용등급의 회사채 곡선인지 확인하십시오 — "
                       "곡선이 낮으면 주계약이 부풀고 전환권대가가 그만큼 "
                       "깎입니다.")
    except Exception:
        pass
    if put_bdt_on(tm):
        if tm.bdt_sig <= 0:
            w.append("BDT 변동성이 0 입니다. 금리 고정 격자와 같은 값이 나옵니다.")
        elif tm.bdt_sig > 1.0:
            w.append(f"BDT 변동성이 {tm.bdt_sig:.0%} 입니다. 로그정규 변동성이라 "
                     "보통 10~30% 를 씁니다. 단위를 확인하십시오.")
        try:
            _g = pick(engine(tm, conv=False, put=True, call=False), tm.model)
            _b = bond_bdt(tm, True)
            if _b < _g - 1e-6:
                w.append(f"BDT 부채요소({_b:,.2f})가 금리 고정 격자({_g:,.2f})보다 "
                         "작습니다. 옵션가치는 음수가 될 수 없으므로 캘리브레이션을 "
                         "확인해야 합니다.")
        except Exception:
            pass
    if tm.floor > tm.K0: w.append("최저 조정가액이 최초 전환가액보다 큽니다.")
    if tm.par > tm.floor: w.append("액면가가 최저 조정가액보다 큽니다. 액면가가 하한으로 작동합니다.")
    if tm.rfx_mode > 0 and round(tm.rfx_cyc*tm.n/(tm.T*12)) < 1:
        w.append("조정 주기가 노드 간격보다 짧습니다. 노드를 늘리십시오.")
    if round(tm.p_f*tm.n/(tm.T*12)) < 1: w.append("조기상환 주기가 노드 간격보다 짧습니다.")
    if tm.k_w > 0 and round(tm.k_f*tm.n/(tm.T*12)) < 1:
        w.append("매도청구 주기가 노드 간격보다 짧습니다.")
    if tm.sig <= 0.01: w.append("변동성이 지나치게 낮습니다.")
    if tm.sig > 2: w.append("변동성이 200%를 넘습니다. 단위를 확인하십시오.")
    if tm.carry == 0 and tm.rfx_mode > 0 and tm.n > 120:
        w.append("상태확장은 노드 120개까지 권장합니다. 근사 방법을 고르거나 노드를 줄이십시오.")
    if tm.p_mode == "accrue" and tm.p_yield <= 0:
        w.append("조기상환 보장수익률이 0입니다. 복리 방식을 쓸 이유가 없습니다.")
    # 위험 곡선이 무위험보다 낮으면 두 곡선을 바꿔 넣은 것이다. 그대로 두면
    # 조기상환권과 전환권이 뒤집힌 값으로 나온다.
    if tm.rf_curve and tm.cr_curve:
        try:
            RF, CR = curves(tm)
            bad = [x for x in (0.25, 0.5, 1, 2, 3, 5, 7, 10)
                   if x <= tm.T + 1e-9 and CR(x) < RF(x) - 1e-9]
            if bad:
                w.append(f"신용스프레드가 음수인 구간이 있습니다 "
                         f"({bad[0]:g}년 무위험 {RF(bad[0]):.2%} > 위험 {CR(bad[0]):.2%}). "
                         f"두 곡선을 바꿔 넣지 않았는지 확인하십시오.")
        except Exception:
            pass
    return w


# ══════════════════════════════════════════════════════════
# 4. 주가·변동성
# ══════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False)
def korean_font():
    """그림에 쓸 한글 글꼴 이름. 없으면 None.

    이름만 보고 고르면 글꼴이 있어도 한글 글리프가 없어 네모로 나온다.
    matplotlib 에 딸린 FT2Font 로 '가'(U+AC00) 가 실제로 있는지 확인한다.
    """
    try:
        from matplotlib import font_manager as fm
        from matplotlib.ft2font import FT2Font
    except Exception:
        return None
    pref = ("NanumGothic", "NanumBarunGothic", "NanumSquare", "Malgun Gothic",
            "AppleGothic", "Apple SD Gothic Neo", "Noto Sans CJK KR",
            "Noto Sans KR", "Source Han Sans KR", "UnDotum", "Baekmuk Gulim",
            "WenQuanYi Zen Hei", "Droid Sans Fallback")

    def has_hangul(path):
        try:
            return 0xAC00 in FT2Font(path).get_charmap()
        except Exception:
            return False

    byname = {}
    for f in fm.fontManager.ttflist:
        byname.setdefault(f.name, f.fname)
    for nm in pref:
        if nm in byname and has_hangul(byname[nm]):
            return nm
    for nm, path in sorted(byname.items()):
        if has_hangul(path):
            return nm
    return None


def use_korean_font():
    """그림을 그리기 전에 부른다. 글꼴을 찾았으면 이름, 못 찾았으면 None."""
    nm = korean_font()
    try:
        import matplotlib
        matplotlib.rcParams["axes.unicode_minus"] = False   # 음수 부호도 네모가 된다
        if nm:
            matplotlib.rcParams["font.family"] = nm
    except Exception:
        pass
    return nm


def _yf_symbols(code: str, market: str):
    """국내 6자리 코드면 고른 시장을 먼저, 그다음 다른 시장. 해외 티커는 그대로."""
    if not code.isdigit():
        return [code]
    sufs = [f".{market}"] if market else []
    sufs += [x for x in (".KQ", ".KS") if x not in sufs]
    return [code + suf for suf in sufs]


def pick_close(rows, on_date: str):
    """[(날짜, 종가)] 에서 평가기준일 **이하** 마지막 거래일의 종가를 고른다.

    평가기준일이 휴장이면(주말·공휴일·거래정지) 직전 거래일이다. 기준일 뒤의 값은 절대 쓰지
    않는다 — 결산일 이후 주가로 재면 안 된다. 없으면 None.
    """
    cand = [(d, v) for d, v in rows if d <= on_date and v > 0]
    return cand[-1] if cand else None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_close(code: str, market: str, on_date: str):
    """평가기준일(또는 직전 거래일)의 **종가**를 야후에서 받는다. (거래일, 종가, 심볼).

    변동성용 fetch_prices 와 달리 auto_adjust=False — 평가일의 주가는 그날 실제로 거래된
    값이어야지, 그 뒤의 증자·분할을 소급 반영한 수정주가가 아니다.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance 가 설치되어 있지 않습니다.") from None
    d2 = dt.date.fromisoformat(on_date)
    d1 = d2 - dt.timedelta(days=21)
    errs = []
    for sym in _yf_symbols(code, market):
        try:
            df = yf.download(sym, start=d1, end=d2 + dt.timedelta(days=1),
                             progress=False, auto_adjust=False, threads=False)
            if df is None or df.empty:
                errs.append(f"{sym} 자료 없음"); continue
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df = df.droplevel(1, axis=1)
            col = "Close" if "Close" in df.columns else df.columns[0]
            rows = [(i.strftime("%Y-%m-%d"), float(v)) for i, v in df[col].dropna().items()]
            hit = pick_close(rows, on_date)
            if hit: return hit[0], hit[1], sym
            errs.append(f"{sym} {on_date} 이전 거래일 없음")
        except Exception as e:
            errs.append(f"{sym} {e}")
    raise RuntimeError(" / ".join(errs) or "자료를 찾지 못했습니다")


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_prices(code: str, days: int, market: str, end: str = None):
    """야후 파이낸스에서 수정주가를 받는다.

    auto_adjust=True 라 유상증자·액면분할·배당이 반영된 종가가 온다.
    국내 종목은 코스닥 .KQ, 코스피 .KS 를 차례로 시도한다.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError(
            "yfinance 가 설치되어 있지 않습니다. requirements.txt 에 "
            "yfinance>=0.2.40,<0.2.58 을 넣고 앱을 다시 시작하십시오. "
            "그동안은 아래에서 주가 파일을 넣으시면 됩니다.") from None
    d2 = dt.date.fromisoformat(end) if end else dt.date.today()
    d1 = d2 - dt.timedelta(days=int(days*1.7)+30)
    # 고른 시장을 먼저 보되 비면 다른 시장도 해 본다. 이전 상장이나 시장 이관이
    # 있으면 접미사가 어긋나는데, 화면에서는 "자료 없음" 으로만 보여 원인을 못 찾는다.
    errs = []
    for sym in _yf_symbols(code, market):
        try:
            df = yf.download(sym, start=d1, end=d2+dt.timedelta(days=1),
                             progress=False, auto_adjust=True, threads=False)
            if df is None or df.empty:
                errs.append(f"{sym} 자료 없음"); continue
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df = df.droplevel(1, axis=1)
            col = "Close" if "Close" in df.columns else df.columns[0]
            sr = df[col].dropna()
            rows = [(i.strftime("%Y-%m-%d"), float(v)) for i, v in sr.items() if v > 0]
            if len(rows) >= 10:
                return rows[-days:], f"야후 {sym} · 수정주가"
            errs.append(f"{sym} {len(rows)}개")
        except Exception as e:
            errs.append(f"{sym} {e}")
    # 무엇을 해 봤고 왜 안 됐는지 밝힌다. 옛 yfinance 는 야후가 API 를 바꾸면
    # 예외 없이 빈 표를 돌려주므로, 판 번호가 있어야 원인을 가릴 수 있다.
    raise RuntimeError((" / ".join(errs) or "자료를 찾지 못했습니다")
                       + f"  (yfinance {getattr(yf, '__version__', '?')})")


def vol_from(prices, tdays=250, drop_outlier=True):
    px = [p for _, p in prices]
    if len(px) < 10: return None
    r = np.diff(np.log(px))
    removed, lo, hi = 0, None, None
    if drop_outlier:
        M = float(np.median(r)); mad = float(np.median(np.abs(r-M)))*1.4826
        lo, hi = M-2.5*mad, M+2.5*mad   # 사례 5-2 와 같은 배수
        keep = (r >= lo) & (r <= hi); removed = int((~keep).sum()); r = r[keep]
    sd = float(np.std(r, ddof=1))
    return dict(daily=sd, annual=sd*math.sqrt(tdays), n=len(px)-1,
                removed=removed, lo=lo, hi=hi)


def read_upload(name: str, data: bytes) -> str:
    """업로드 파일을 텍스트로 편다.

    엑셀은 시트를 탭 구분 텍스트로 바꾼다. csv·txt 는 한글 인코딩을 차례로
    시도한다. 증권사·거래소에서 내려받은 파일은 대개 CP949 다.
    """
    low = name.lower()
    if low.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        out = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            cells = ["" if v is None else
                     (v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v))
                     for v in row]
            if any(cells): out.append("\t".join(cells))
        wb.close()
        return "\n".join(out)
    if low.endswith(".xls"):
        try:
            import xlrd                              # 옛 형식은 xlrd 만 읽는다
        except ImportError:
            raise ValueError(
                "옛 형식(.xls)을 읽으려면 xlrd 가 필요합니다. requirements.txt 에 "
                "xlrd>=2.0 을 넣고 앱을 다시 시작하시거나, 엑셀에서 .xlsx 로 "
                "저장해 다시 넣으십시오.") from None
        bk = xlrd.open_workbook(file_contents=data)
        sh = bk.sheet_by_index(0)
        out = []
        for r in range(sh.nrows):
            cells = []
            for c in range(sh.ncols):
                v, ty = sh.cell_value(r, c), sh.cell_type(r, c)
                if ty == xlrd.XL_CELL_DATE:
                    y, mo, d = xlrd.xldate_as_tuple(v, bk.datemode)[:3]
                    v = f"{y:04d}-{mo:02d}-{d:02d}"
                elif isinstance(v, float) and v == int(v) and abs(v) < 1e15:
                    v = int(v)
                cells.append("" if v is None else str(v))
            if any(cells): out.append("\t".join(cells))
        return "\n".join(out)
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            txt = data.decode(enc)
            if "�" not in txt: return txt
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore")


def parse_prices(txt: str):
    out, close_idx, start = [], -1, 0
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    import re
    def cut(l):
        l = re.sub(r'"([^"]*)"', lambda m: m.group(1).replace(",", ""), l)
        return [x.strip() for x in re.split(r"[\t,;]|\s{2,}", l) if x.strip()]
    isdate = lambda x: bool(re.match(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$", x.strip()))
    for i, l in enumerate(lines[:3]):
        c = cut(l)
        k = [j for j, x in enumerate(c)
             if re.match(r"^(종가|현재가|close|adj\s*close)$", x.strip(), re.I)]
        if k: close_idx, start = k[0], i+1; break
        if any(re.search(r"[가-힣A-Za-z]{2,}", x) for x in c) and not any(isdate(x) for x in c):
            start = i+1
    for l in lines[start:]:
        c = cut(l)
        if not c: continue
        di = next((j for j, x in enumerate(c) if isdate(x)), -1)
        close = None
        if 0 <= close_idx < len(c):
            try: close = float(c[close_idx].replace(",", "").replace("원", ""))
            except Exception: close = None
        if close is None or close <= 0:
            for k in range(di+1 if di >= 0 else 0, len(c)):
                if isdate(c[k]): continue
                try:
                    v = float(c[k].replace(",", "").replace("원", ""))
                    if v > 0: close = v; break
                except Exception: pass
        if close is None or close <= 0: continue
        d = c[di].replace(".", "-").replace("/", "-") if di >= 0 else ""
        if d:
            p = d.split("-"); d = f"{p[0]}-{int(p[1]):02d}-{int(p[2]):02d}"
        out.append((d, close))
    if len(out) >= 2 and out[0][0] and out[-1][0] and out[0][0] > out[-1][0]:
        out.reverse()
    return out


def parse_prices_multi(txt: str):
    """여러 종목의 종가가 한 파일에 들어 있을 때 열마다 갈라 읽는다.

    첫 줄이 머리글이고 첫 열이 일자, 나머지 열이 종목별 종가인 형태를 본다.
    야후·거래소·증권사에서 여러 종목을 한 번에 내려받으면 대개 이 꼴이다.
    """
    import re
    lines = [l for l in (x.rstrip() for x in txt.splitlines()) if l.strip()]
    if not lines: return []

    def cut(l):
        l = re.sub(r'"([^"]*)"', lambda m: m.group(1).replace(",", ""), l)
        return [x.strip() for x in re.split(r"[\t,;]|\s{2,}", l)]

    isdate = lambda x: bool(re.match(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$", x.strip()))
    hdr = cut(lines[0])
    body = [cut(l) for l in lines[1:]]
    body = [c for c in body if c and isdate(c[0])]
    if len(body) < 10 or len(hdr) < 3: return []
    out = []
    for j in range(1, len(hdr)):
        nm = hdr[j].strip() or f"열{j}"
        rows = []
        for c in body:
            if j >= len(c): continue
            try:
                v = float(c[j].replace(",", "").replace("원", ""))
            except Exception:
                continue
            if v <= 0: continue
            d = c[0].replace(".", "-").replace("/", "-").split("-")
            rows.append((f"{d[0]}-{int(d[1]):02d}-{int(d[2]):02d}", v))
        if len(rows) >= 10:
            if rows[0][0] > rows[-1][0]: rows.reverse()
            out.append((nm, rows))
    return out


def parse_yields(txt: str, unit: str = "auto"):
    """만기별 수익률을 읽는다.

    받는 형태
        만기(년)  수익률(%)                 →  2열
        만기일  만기(개월)  수익률(%)        →  3열 (한국은행·금투협 표를 그대로 붙여넣는 형태)
    unit 이 auto 면 만기 값이 40을 넘는 항목이 있을 때 개월로 본다.
    """
    import re
    rows = []
    isdate = lambda x: bool(re.match(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$", x.strip()))
    for line in txt.splitlines():
        c = [x for x in re.split(r"[\t,;]|\s{2,}|\s", line.strip()) if x]
        if not c: continue
        c = [x.replace("%", "").replace(",", "") for x in c]
        c = [x for x in c if not isdate(x)]          # 날짜 열은 버린다
        nums = []
        for x in c:
            try: nums.append(float(x))
            except ValueError: pass
        if len(nums) >= 2:
            rows.append((nums[-2], nums[-1]))        # 뒤에서 둘 = 만기, 수익률
    if not rows: return []
    mx = max(r[0] for r in rows)
    months = (unit == "month") or (unit == "auto" and mx > 40)
    out = [((m/12 if months else m), y/100) for m, y in rows if m > 0]
    return sorted(out)


KIS_TENORS = {"3월": 3, "6월": 6, "9월": 9, "1년": 12, "1년6월": 18, "2년": 24,
              "2년6월": 30, "3년": 36, "4년": 48, "5년": 60, "7년": 84,
              "10년": 120, "15년": 180, "20년": 240, "30년": 360, "50년": 600}


def read_kisnet(name: str, data: bytes):
    """KIS-Net 채권시가평가 기준수익률 표를 읽는다.

    첫 시트가 ``종류 · 종류명 · 신용등급 · 고시기관 · 3월 · 6월 · … · 50년`` 이고
    금리는 % 단위, 값이 없으면 ``-`` 다. 국채 한 줄과 회사채 등급별 여러 줄이
    한 표에 같이 있으므로, 어느 줄을 무위험으로 쓰고 어느 줄을 위험으로 쓸지는
    화면에서 고른다.

    돌려주는 것은 ``[(라벨, [(만기(년), 수익률), …]), …]`` 이다.
    """
    ext = name.lower().rsplit(".", 1)[-1]
    if ext == "xls":
        try:
            import xlrd                              # 옛 형식은 xlrd 만 읽는다
        except ImportError:
            raise ValueError(
                "옛 형식(.xls)을 읽으려면 xlrd 가 필요합니다. requirements.txt 에 "
                "xlrd>=2.0 을 넣고 앱을 다시 시작하시거나, 엑셀에서 .xlsx 로 "
                "저장해 다시 넣으십시오.") from None
        sh = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
        grid = [[sh.cell_value(r, c) for c in range(sh.ncols)]
                for r in range(sh.nrows)]
    else:
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(data), data_only=True).worksheets[0]
        grid = [[c if c is not None else "" for c in row]
                for row in ws.iter_rows(values_only=True)]
    if not grid: raise ValueError("빈 파일입니다.")

    # 머리 행 — 만기 이름이 가장 많이 걸리는 줄
    hi, cols = -1, {}
    for i, row in enumerate(grid[:10]):
        got = {j: KIS_TENORS[str(v).strip()]
               for j, v in enumerate(row) if str(v).strip() in KIS_TENORS}
        if len(got) > len(cols): hi, cols = i, got
    if len(cols) < 3:
        raise ValueError("만기 열(3월·6월·1년 …)을 찾지 못했습니다. "
                         "KIS-Net 기준수익률 표의 첫 시트인지 확인하십시오.")

    out = []
    for row in grid[hi+1:]:
        head = [str(row[j]).strip() for j in range(min(3, len(row)))]
        head = [h for h in head if h and h != "-"]
        if not head: continue
        pts = []
        for j, mth in sorted(cols.items(), key=lambda x: x[1]):
            if j >= len(row): continue
            try: y = float(str(row[j]).replace(",", "").replace("%", ""))
            except ValueError: continue
            if y > 0: pts.append((mth/12, y/100))
        if len(pts) >= 2:
            out.append((" · ".join(head[:3]).replace("*", ""), pts))
    if not out: raise ValueError("수익률 행을 찾지 못했습니다.")
    return out


def curve_text(pts):
    """곡선을 화면 입력 형식(개월 · 수익률%)으로 되돌린다."""
    return "\n".join(f"{round(t*12):d}\t{y*100:.3f}%" for t, y in pts)


# ══════════════════════════════════════════════════════════
# 4-2. 금리변동성 — BDT 의 σ 를 시계열에서 뽑는다
# ══════════════════════════════════════════════════════════
# 할인율은 이미 등급보간(blend_curves) → 만기보간(_lin) 두 번을 거친다.
# 변동성도 같은 자료·같은 보간에서 나와야 조서가 하나로 이어진다.
#
# 순서가 중요하다. **보간을 먼저 하고 변동성을 나중에** 구한다. 변동성은
# 선형 함수가 아니라 등급별 σ 를 보간하면 값이 달라진다 — 내삽이면 2% 안쪽이지만
# 외삽하면 9% 가까이 벌어진다.

TENOR_MO = dict(KIS_TENORS)
TENOR_MO.update({"1개월": 1, "3개월": 3, "6개월": 6, "9개월": 9,
                 "1년6개월": 18, "2년6개월": 30, "18월": 18, "30월": 30})


def _tenor_years(label) -> float:
    """만기 머리글을 연 단위로. '3년' · '1년6개월' · '0.25' · '36'(개월) 을 받는다."""
    s = str(label).strip()
    if not s: return None
    if s in TENOR_MO: return TENOR_MO[s]/12
    m = re.match(r"^\s*(\d+)\s*년\s*(?:(\d+)\s*개?월)?\s*$", s)
    if m: return int(m.group(1)) + (int(m.group(2) or 0))/12
    m = re.match(r"^\s*(\d+)\s*개?월\s*$", s)
    if m: return int(m.group(1))/12
    try:
        v = float(s.replace(",", ""))
    except Exception:
        return None
    # 12 보다 크면 개월로 본다. 만기 곡선에 12년 이상은 드물다.
    return v/12 if v > 12 else (v if v > 0 else None)


def parse_rate_panel(txt: str):
    """일자 × (등급·만기) 표를 읽는다. 머리 줄이 여러 개여도 된다.

    금투협 시계열은 열마다 '회사채 I(공모사채) /무보증 / BBB0' 같은 등급명과
    '5년' 같은 만기가 **서로 다른 머리 줄**에 있다. 그래서 열별로 머리 줄을
    모두 모아 등급과 만기를 찾는다. 한 파일에 등급이 여럿이어도 갈라 읽고,
    등급 표기가 없으면 만기만 읽어 종전의 일자 × 만기 표와 같아진다.

    반환은 ([(등급, 만기(년), 열번호)], [(일자, [값…])]) 이고, 등급·만기는
    못 찾으면 None 이다. 수익률은 % 단위 그대로다.
    """
    lines = [l for l in (x.rstrip() for x in txt.splitlines()) if l.strip()]
    if not lines: return [], []

    def cut(l):
        l = re.sub(r'"([^"]*)"', lambda m: m.group(1).replace(",", ""), l)
        return [x.strip() for x in re.split(r"[\t,;]|\s{2,}", l)]

    isdate = lambda x: bool(re.match(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$", x.strip()))
    heads, body = [], []
    for l in lines:
        c = cut(l)
        if c and isdate(c[0]):
            body.append(c)
        elif not body and len(c) >= 2:
            heads.append(c)                          # 자료가 나오기 전은 다 머리다
    if not heads or len(body) < 10: return [], []
    ncol = max([len(h) for h in heads] + [len(b) for b in body])
    cols = []
    for j in range(1, ncol):
        cells = [h[j] for h in heads if j < len(h)]
        rt = next((r for r in (rating_in(c) for c in cells) if r), None)
        tn = next((y for y in (_tenor_years(c) for c in cells) if y), None)
        if rt or tn: cols.append((rt, tn, j))
    if not cols: return [], []
    rows = []
    for c in body:
        vals = []
        for _, _, j in cols:
            try:
                vals.append(float(c[j].replace(",", "").replace("%", "")))
            except Exception:
                vals.append(None)
        if any(v is not None and v > 0 for v in vals):
            d = re.split(r"[-./]", c[0].strip())
            rows.append((f"{int(d[0]):04d}-{int(d[1]):02d}-{int(d[2]):02d}", vals))
    if len(rows) >= 2 and rows[0][0] > rows[-1][0]: rows.reverse()
    return cols, rows


def panel_series(cols, rows, T: float):
    """패널을 등급별 고정만기 시계열로 접는다.

    한 등급에 만기가 여럿이면 그날 곡선에서 잔존만기 T 지점을 뽑고, 하나뿐이면
    그 만기를 그대로 쓴다 — 고시표가 5년 한 열만 주는 일이 흔하다. 만기를
    먼저 맞추고 변동성은 나중에 계산해야 만기 이동 효과가 σ 에 섞이지 않는다.

    반환은 ({등급: [(일자, 금리)]}, {등급: [만기(년)…]}) 이다.
    """
    byr = {}
    for k, (rt, tn, _) in enumerate(cols):
        if tn is not None: byr.setdefault(rt, []).append((tn, k))
    ser, tens = {}, {}
    for rt, items in byr.items():
        items.sort()
        out = []
        for d, vals in rows:
            pts = [(tn, vals[k]) for tn, k in items
                   if vals[k] is not None and vals[k] > 0]
            if not pts: continue
            y = _lin(pts, T)
            if y and y > 0: out.append((d, y))
        if len(out) >= 10:
            ser[rt] = out
            tens[rt] = [tn for tn, _ in items]
    return ser, tens


def cm_series(tenors, rows, T: float):
    """고정만기 시계열 — 매일 그날 곡선에서 잔존만기 T 지점을 뽑는다.

    과거로 갈 때 만기를 함께 늘리면 금리 변동이 아니라 만기 이동 효과가
    변동성에 섞인다. 국고채 지표금리를 만기 고정으로 고시하는 것과 같은 이유다.
    """
    out = []
    for d, vals in rows:
        pts = [(t, v) for t, v in zip(tenors, vals) if v is not None and v > 0]
        if len(pts) < 2: continue
        pts.sort()
        y = _lin(pts, T)
        if y and y > 0: out.append((d, y))
    return out


def blend_series(sa, sb, ra: str, rb: str, rt: str):
    """두 등급의 고정만기 시계열을 노치 거리로 섞는다.

    blend_curves 와 같은 가중치를 쓴다. 날짜가 둘 다 있는 날만 남긴다.
    """
    ia, ib, it = rating_idx(ra), rating_idx(rb), rating_idx(rt)
    if not sb: return list(sa)
    if not sa: return list(sb)
    if ia < 0 or ib < 0 or it < 0 or ia == ib: return list(sa)
    w = (it-ia)/(ib-ia)
    db = dict(sb)
    return [(d, y + (db[d]-y)*w) for d, y in sa if d in db]


def rate_vol(series, tdays=250, drop=True):
    """금리 시계열의 변동성. 상대(로그정규)와 절대(정규)를 함께 준다.

    BDT 의 σ 는 **상대** 변동성이다. 실무에서 bp 로 말하는 절대 변동성을
    그대로 넣으면 크게 어긋나므로 둘을 나란히 보여 준다.

        상대 ≈ 절대 ÷ 평균금리
    """
    v = vol_from(series, tdays, drop)
    if not v: return None
    lv = np.array([y for _, y in series], dtype=float)
    lg = np.diff(np.log(lv))
    dif = np.diff(lv)
    # 상대 변동성이 채택분으로만 계산되므로 절대 변동성도 같은 날만 쓴다.
    # 전체로 계산하면 이상치를 뺀 상대값과 견줄 수 없고, 리포트 수식과도 어긋난다.
    if drop and v.get("lo") is not None:
        keep = (lg >= v["lo"]) & (lg <= v["hi"])
        dif = dif[keep]
    v = dict(v)
    v["abs_daily"] = float(np.std(dif, ddof=1)) if len(dif) > 1 else 0.0
    v["abs_annual"] = v["abs_daily"]*math.sqrt(tdays)
    v["mean"] = float(np.mean(lv))
    v["min"] = float(np.min(lv))
    v["neg"] = int((lv <= 0).sum())
    return v


# ══════════════════════════════════════════════════════════
# 4-1. 리포트 공통 서식
# ══════════════════════════════════════════════════════════
# 조서와 같은 손맛으로 보이도록 색·글꼴·번호서식을 한곳에 모았다.
RPT = dict(
    ink="1F3864", sub="44618C", grey="7F7F7F", amber="9A7200",
    green="1F6B44", red="A6301F", band="EFF3F8", light="F7F9FC",
    tint="E4EBF5", hair="D6DCE5", warm="FFF7E6",
)

# 잔여 주계약이 0 이하일 때 상각표 자리에 싣는 문구 — 화면 HOST_NONPOS_NOTE 와 같은 뜻
HOST_NONPOS_XL = (
    "잔여 주계약이 0 이하라 상각표를 만들지 않는다.",
    "전체 가치가 발행가 100 과 크게 달라 파생을 뺀 잔여가 남지 않는다 — 최초 인식 시점의 "
    "공정가치와 거래가격의 차이(Day-1 차이, 제1109호 문단 B5.1.2A)를 먼저 정리해야 한다.",
    "유효이자율이 정의되지 않으므로 상각표·이자비용 대신 이 문구를 싣는다. 배분표는 그대로다.",
)
R_N0, R_N2, R_N4, R_N6 = "#,##0", "#,##0.00", "#,##0.0000", "0.000000"
R_P2, R_P4 = "0.00%", "0.0000%"
R_YMD = "yyyy-mm-dd"


# 엑셀 2010 이후에 들어온 함수는 xlsx 파일 안에 «_xlfn.» 접두사를 달고 저장돼야 한다.
# openpyxl 은 문자열을 그대로 쓰므로 접두사가 없으면 엑셀이 #NAME? 을 보이다가 사용자가
# 셀에서 엔터를 쳐야 다시 파싱한다. 조서를 여는 사람마다 겪을 일이라 여기서 붙인다.
XLFN = ("STDEV.S", "STDEV.P", "VAR.S", "VAR.P", "PERCENTILE.INC", "PERCENTILE.EXC",
        "QUARTILE.INC", "QUARTILE.EXC", "NORM.S.DIST", "NORM.DIST", "NORM.S.INV", "NORM.INV",
        "IFS", "MAXIFS", "MINIFS", "CONCAT", "TEXTJOIN", "XLOOKUP", "FORECAST.LINEAR",
        "COVARIANCE.S", "COVARIANCE.P", "MODE.SNGL", "RANK.EQ", "RANK.AVG")
_XLFN_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(f) for f in XLFN) + r")\s*\(")


def xlfn(v):
    """수식 문자열이면 2010+ 함수 이름 앞에 _xlfn. 을 붙인다. 이미 붙어 있으면 그대로."""
    if not (isinstance(v, str) and v.startswith("=")): return v
    return _XLFN_RE.sub(lambda m: "_xlfn." + m.group(1) + "(", v)


def report_kit(wb, font="맑은 고딕"):
    """리포트 한 권에 쓸 서식 도구를 만든다.

    put   — 셀 하나. 값·수식·서식·색을 한 번에 준다.
    head  — 표지 제목줄
    sec   — 구역 머리 (색 띠)
    cols  — 표 머리줄
    note  — 회색 주석
    """
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as gl

    thin = Side(style="thin", color=RPT["hair"])
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    def put(ws, r, c, v, *, fmt=None, bold=False, size=10, color=None,
            fill=None, align=None, border=False, wrap=False, italic=False):
        cl = ws.cell(r, c, xlfn(v))
        cl.font = Font(name=font, size=size, bold=bold, italic=italic,
                       color=color or RPT["ink"])
        if fmt: cl.number_format = fmt
        if fill: cl.fill = PatternFill("solid", fgColor=fill)
        if align or wrap:
            cl.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
        if border: cl.border = box
        return cl

    def head(ws, r, txt, sub=None, span=8):
        put(ws, r, 2, txt, bold=True, size=16, color=RPT["ink"])
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=1+span)
        if sub:
            put(ws, r+1, 2, sub, size=9.5, color=RPT["grey"], wrap=True)
            ws.merge_cells(start_row=r+1, start_column=2, end_row=r+1, end_column=1+span)
            ws.row_dimensions[r+1].height = 28
        ws.row_dimensions[r].height = 24

    def sec(ws, r, txt, span=8, tone=None):
        for c in range(2, 2+span):
            put(ws, r, c, txt if c == 2 else None, bold=(c == 2), size=10.5,
                color=RPT["ink"], fill=tone or RPT["band"])
        ws.row_dimensions[r].height = 20

    def cols(ws, r, names, widths=None, start=2):
        for i, nm in enumerate(names):
            put(ws, r, start+i, nm, bold=True, size=9, fill=RPT["tint"],
                align="center", border=True, wrap=True)
        if widths:
            for i, w in enumerate(widths):
                ws.column_dimensions[gl(start+i)].width = w
        ws.row_dimensions[r].height = 26

    def note(ws, r, txt, span=8, tone=None):
        put(ws, r, 2, txt, size=9, color=tone or RPT["grey"], wrap=True)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=1+span)
        ws.row_dimensions[r].height = max(16, 14*(1+len(txt)//95))

    def sheet(name, tab=None, widths=None, freeze=None, landscape=False):
        ws = wb.create_sheet(name)
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = tab or RPT["sub"]
        ws.column_dimensions["A"].width = 2.2
        if widths:
            for i, w in enumerate(widths):
                ws.column_dimensions[gl(2+i)].width = w
        if freeze: ws.freeze_panes = freeze
        ws.page_setup.orientation = "landscape" if landscape else "portrait"
        ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        return ws

    return dict(put=put, head=head, sec=sec, cols=cols, note=note, sheet=sheet, gl=gl)


def _brackets(pts, t):
    """t 를 감싸는 입력곡선 두 점의 번호. 범위 밖이면 (i, i) 로 한 점만 준다."""
    if not pts: return (0, 0)
    if t <= pts[0][0]: return (0, 0)
    if t >= pts[-1][0]: return (len(pts)-1, len(pts)-1)
    for i in range(1, len(pts)):
        if t <= pts[i][0]: return (i-1, i)
    return (len(pts)-1, len(pts)-1)


def lerp_formula(t, pts, col, row0, sh=None):
    """엑셀에서 선형보간. 입력 셀을 가리키므로 노란 셀을 고치면 따라 움직인다.

    col 은 값이 든 열 문자, row0 은 첫 점의 행, sh 는 그 표가 있는 시트다.
    범위 밖이면 끝점을 그대로 쓴다 — 앱의 _lin 과 같다.
    """
    q = f"'{sh}'!" if sh else ""
    i, j = _brackets(pts, t)
    if i == j: return f"={q}${col}${row0+i}"
    x0, x1 = pts[i][0], pts[j][0]
    return (f"={q}${col}${row0+i}+({q}${col}${row0+j}-{q}${col}${row0+i})"
            f"*({t:.12g}-{x0:.12g})/({x1:.12g}-{x0:.12g})")


def build_xlsx_vol(series, tdays=250, drop=True, mad_k=2.5, pick="median",
                   applied=None, asof=None, kind="stock", how=None,
                   wb=None, prefix=""):
    """변동성 산출내역. 계산이 전부 수식으로 들어간다.

    series 는 [(이름, [(일자, 종가), …]), …] 다. 하나면 대상회사만,
    여럿이면 비상장 평가에서 쓰는 피어 묶음이다.
    pick 은 여러 회사를 하나로 줄이는 방법 — median · mean · max · min.

    ``wb`` 를 주면 그 조서 안에 시트를 더하고, 산출된 연 변동성이 앉은 셀 주소를
    돌려준다. 조서의 가정 시트가 그 셀을 참조하면 종가를 고칠 때 σ 가 따라 움직인다.
    ``prefix`` 는 시트 이름 앞에 붙는다 (조서에 이미 「표지」가 있을 수 있다).
    """
    from openpyxl import Workbook
    _own = wb is None
    if _own:
        wb = Workbook(); wb.remove(wb.active)
    K = report_kit(wb)
    put, head, sec, cols, note, sheet = (K[x] for x in
        ("put", "head", "sec", "cols", "note", "sheet"))
    series = [(nm, px) for nm, px in series if px and len(px) >= 10]
    if not series: raise ValueError("종가가 10개 이상인 계열이 하나도 없습니다.")
    many = len(series) > 1
    YEL = "FFF9DB"
    # 금리 계열이면 절대(정규) 변동성을 한 줄 더 낸다. 실무에서 bp 로 말하는
    # 그 값이고, BDT 가 쓰는 상대(로그정규) 변동성과 헷갈리기 쉬워 나란히 둔다.
    _rate = (kind == "rate")
    UNIT = "금리 (%)" if _rate else "종가"
    RET = "로그변화율" if _rate else "로그수익률"
    PICKS = {"median": "중앙값", "mean": "단순평균", "max": "최댓값", "min": "최솟값"}

    # 회사별 시트의 행 자리. 한곳에서 정해 두고 수식이 이 이름만 쓴다.
    R_TD, R_MK, R_DR = 5, 6, 7          # 거래일수 · MAD 배수 · 이상치 제거
    R_NP, R_NR, R_MD = 8, 9, 10         # 종가 수 · 수익률 수 · 중앙값
    R_MA, R_LO, R_HI = 11, 12, 13       # MAD · 하한 · 상한
    R_EX, R_SD, R_AN = 14, 15, 16       # 제외 · 일 변동성 · 연 변동성
    R_AD, R_AA, R_MN = 17, 18, 19       # 절대 일·연 변동성 · 평균 (금리만)
    HDR = 22 if _rate else 19           # 표 머리
    R0 = HDR + 1                        # 첫 자료행

    P = prefix
    names = [f"{P}{i:02d} {_vsafe(nm)}"[:31] for i, (nm, _) in enumerate(series, 1)]

    # ── 표지 ──
    C = sheet(f"{P}표지", tab=RPT["ink"], widths=[24, 20, 18, 18, 16, 16, 16, 16])
    head(C, 2, ("금리변동성 산출내역" if _rate else "변동성 산출내역"),
         ("BDT 금리격자에 넣을 단기이자율 변동성 σ 를 금리의 로그변화율로 구한 "
          "내역이다. " if _rate else
          "전환사채 평가에 쓸 주가변동성을 로그수익률의 표본표준편차로 구한 "
          "내역이다. ")
         + "노란 셀만 입력이고 나머지는 수식이라, 거래일수나 이상치 배수를 "
           "바꾸면 표 전체가 다시 계산된다.")
    r = 5
    sec(C, r, "산출 요약"); r += 1
    cols(C, r, ["항목", "내용"], [26, 62]); r += 1
    for k, v in ([("평가기준일", (asof or dt.date.today()).isoformat()),
                 ("대상 계열", f"{len(series)}개 — " + " · ".join(nm for nm, _ in series)),
                 ("수익률", f"일별 {RET}  ln({UNIT} ÷ 직전 {UNIT})"),
                 ("이상치 처리", (f"중앙값 절대편차(MAD) × {mad_k:g} 밖을 제외"
                                if drop else "제외하지 않음")),
                 ("표준편차", "표본표준편차 STDEV.S — 자유도 n−1"),
                 ("연환산", f"일 변동성 × √{tdays:g}"),
                  ("종합 방법", PICKS.get(pick, pick) if many else "단일 계열")]
                 + ([("산출 경위", how)] if how else [])
                 + ([("변동성 종류", "상대(로그정규) — BDT 의 σ 다. 절대(정규) "
                                  "변동성도 함께 내되 모형에는 상대를 쓴다")]
                    if _rate else [])):
        put(C, r, 2, k, bold=True, border=True, fill=RPT["light"])
        put(C, r, 3, v, border=True, wrap=True)
        C.merge_cells(start_row=r, start_column=3, end_row=r, end_column=8)
        r += 1
    r += 1
    sec(C, r, "결과"); r += 1
    cols(C, r, ["구분", "연 변동성"], [26, 18]); r += 1
    res = r
    put(C, r, 2, "종합", bold=True, border=True, fill=RPT["warm"])
    put(C, r, 3, (f"='{P}종합'!$C$6" if many else f"='{names[0]}'!$C${R_AN}"),
        fmt=R_P2, bold=True, border=True, fill=RPT["warm"], align="right")
    r += 1
    if applied is not None:
        put(C, r, 2, "앱에 적용한 값", bold=True, border=True)
        put(C, r, 3, applied, fmt=R_P2, border=True, align="right", color=RPT["amber"])
        put(C, r+1, 2, "차이", bold=True, border=True)
        put(C, r+1, 3, f"=C{r}-C{res}", fmt=R_P4, border=True, align="right")
        r += 2
    r += 1
    note(C, r, "주황색 숫자는 앱이 넣은 값이고 노란 셀은 바꿔도 되는 입력이다. "
               + ("금리는 평가 기준 곡선과 같은 등급·같은 잔존만기로 맞춘 "
                  "고정만기 시계열이어야 한다 — 매일 만기가 줄어드는 특정 종목의 "
                  "금리를 쓰면 만기 효과가 변동성으로 잡힌다."
                  if _rate else
                  "종가는 수정주가여야 한다 — 유상증자·액면분할·배당이 반영되지 "
                  "않은 종가를 쓰면 그날 하루가 통째로 이상치가 된다."))

    # ── 회사별 시트 ──
    for (nm, px), sn in zip(series, names):
        W = sheet(sn, widths=[15, 13, 14, 13, 9, 14], freeze=f"B{R0}")
        head(W, 2, f"{nm} — 일별 {RET}", span=6)
        n = len(px); last = R0 + n - 1
        D1, DN = f"$D${R0+1}", f"$D${last}"
        sec(W, 4, "입력과 결과", span=6)
        lab = [(R_TD, "연 거래일수", tdays, R_N0, True),
               (R_MK, "MAD 배수", mad_k, "0.0#", True),
               (R_DR, "이상치 제거 (1/0)", 1 if drop else 0, R_N0, True),
               (R_NP, "관측 종가", f"=COUNT($C${R0}:$C${last})", R_N0, False),
               (R_NR, "수익률", f"=COUNT({D1}:{DN})", R_N0, False),
               (R_MD, "중앙값", f"=MEDIAN({D1}:{DN})", R_N6, False),
               (R_MA, "MAD (×1.4826)", f"=MEDIAN($E${R0+1}:$E${last})*1.4826",
                R_N6, False),
               (R_LO, "정상범위 하한", f"=$C${R_MD}-$C${R_MK}*$C${R_MA}", R_N6, False),
               (R_HI, "정상범위 상한", f"=$C${R_MD}+$C${R_MK}*$C${R_MA}", R_N6, False),
               (R_EX, "제외 개수",
                f"=COUNT({D1}:{DN})-COUNT($G${R0+1}:$G${last})", R_N0, False),
               (R_SD, "일 변동성", f"=_xlfn.STDEV.S($G${R0+1}:$G${last})", R_P4, False),
               (R_AN, "연 변동성", f"=$C${R_SD}*SQRT($C${R_TD})", R_P2, False)]
        if _rate:
            lab += [(R_AD, "절대 일 변동성 (%p)",
                     f"=_xlfn.STDEV.S($H${R0+1}:$H${last})", R_N4, False),
                    (R_AA, "절대 연 변동성 (%p)",
                     f"=$C${R_AD}*SQRT($C${R_TD})", R_N4, False),
                    (R_MN, "평균 금리 (%)",
                     f"=AVERAGE($C${R0}:$C${last})", R_N4, False)]
        for rr, k, v, fm, inp in lab:
            fin = (rr == R_AN)
            put(W, rr, 2, k, bold=True, border=True,
                fill=(YEL if inp else (RPT["warm"] if fin else RPT["light"])))
            put(W, rr, 3, v, fmt=fm, border=True, align="right", bold=fin,
                fill=(YEL if inp else (RPT["warm"] if fin else None)))
        note(W, (R_MN if _rate else R_AN)+1,
             "MAD 는 중앙값 절대편차에 1.4826 을 곱해 정규분포의 표준편차와 눈금을 "
             "맞춘 값이다. 중앙값과 MAD 는 제외 전 전체 " + RET + " 로 구하고, "
             "표준편차만 채택분으로 구한다."
             + ("  절대 변동성은 로그가 아니라 금리 차이(%p)의 표준편차다. "
                "상대 ≈ 절대 ÷ 평균금리 로 환산된다 — BDT 에는 **상대**를 넣는다."
                if _rate else ""), span=(7 if _rate else 6))
        _hd = ["일자", UNIT, RET, "|편차|", "채택", f"채택 {RET}"]
        _wd = [15, 13, 14, 13, 9, 14]
        if _rate: _hd, _wd = _hd + ["채택 변화분"], _wd + [13]
        cols(W, HDR, _hd, _wd)
        for i, (d, v) in enumerate(px):
            rr = R0 + i
            put(W, rr, 2, (dt.date.fromisoformat(d) if d else None),
                fmt=R_YMD, border=True, align="center")
            put(W, rr, 3, v, fmt=R_N2, border=True, align="right")
            if i == 0: continue
            put(W, rr, 4, f"=LN(C{rr}/C{rr-1})", fmt=R_N6, border=True, align="right")
            put(W, rr, 5, f"=ABS(D{rr}-$C${R_MD})", fmt=R_N6, border=True, align="right")
            put(W, rr, 6, f"=IF($C${R_DR}=0,1,"
                          f"IF(AND(D{rr}>=$C${R_LO},D{rr}<=$C${R_HI}),1,0))",
                fmt=R_N0, border=True, align="center")
            put(W, rr, 7, f'=IF(F{rr}=1,D{rr},"")', fmt=R_N6, border=True, align="right")
            if _rate:
                # 절대 변동성용 — 로그가 아니라 금리 차이(%p) 다
                put(W, rr, 8, f'=IF(F{rr}=1,C{rr}-C{rr-1},"")', fmt=R_N4,
                    border=True, align="right")

    # ── 종합 ──
    if many:
        S = sheet(f"{P}종합", tab=RPT["green"], widths=[8, 26, 18, 14, 12])
        head(S, 2, "피어 종합",
             "비상장이라 대상회사 주가가 없을 때, 유사기업의 변동성을 모아 하나로 줄인다.",
             span=5)
        fn = {"median": "MEDIAN", "mean": "AVERAGE",
              "max": "MAX", "min": "MIN"}.get(pick, "MEDIAN")
        r1, r2 = 9, 9 + len(series) - 1
        put(S, 5, 2, "종합 방법", bold=True, border=True, fill=YEL)
        put(S, 5, 3, PICKS.get(pick, pick), border=True, fill=YEL)
        put(S, 6, 2, "적용 변동성", bold=True, border=True, fill=RPT["warm"])
        put(S, 6, 3, f"={fn}($E${r1}:$E${r2})", fmt=R_P2, bold=True,
            border=True, align="right", fill=RPT["warm"])
        cols(S, 8, ["번호", "회사", "시트", "연 변동성", "수익률"], [8, 26, 18, 14, 12])
        for i, ((nm, px), sn) in enumerate(zip(series, names)):
            rr = r1 + i
            put(S, rr, 2, i+1, fmt=R_N0, border=True, align="center")
            put(S, rr, 3, nm, border=True)
            put(S, rr, 4, sn, border=True, size=9, color=RPT["grey"])
            put(S, rr, 5, f"='{sn}'!$C${R_AN}", fmt=R_P2, border=True, align="right")
            put(S, rr, 6, f"='{sn}'!$C${R_NR}", fmt=R_N0, border=True, align="right")
        note(S, r2+2, "중앙값은 한 회사의 급등락에 덜 흔들린다. 평균을 쓰려면 왜 그 "
                      "회사들이 대상회사와 같은 위험을 진다고 보는지 조서에 남긴다. "
                      "업종·규모·상장기간이 크게 다른 회사는 빼는 편이 낫다.", span=5)
    # 조서 안에 심은 경우에는 결과 셀 주소를 돌려준다. 가정 시트가 이 셀을 본다.
    if not _own: return f"'{P}표지'!$C${res}"
    return _save(wb)


def _vsafe(s):
    """시트 이름에 쓸 수 없는 글자를 턴다."""
    out = "".join(c for c in str(s) if c not in r'[]:*?/\\')
    return out.strip() or "계열"


def polish_wb(wb):
    """조서·리포트 마무리. 탭 색으로 갈래를 나누고 인쇄를 폭 맞춤으로 둔다.

    시트가 스무 장을 넘으면 탭 이름만으로는 어디가 어딘지 안 보인다.
    입력(노랑) · 트리(회색) · 결과(초록) · 회계(빨강)로 갈라 놓는다.
    """
    tone = {"해설": RPT["ink"], "가정": RPT["amber"], "도달확률": "9AA4AE",
            "결과": RPT["green"], "회계처리": RPT["red"],
            "상각표": RPT["sub"], "이자율곡선": RPT["sub"], "표지": RPT["ink"]}
    for ws in wb.worksheets:
        nm = ws.title
        if ws.sheet_properties.tabColor is None:
            ws.sheet_properties.tabColor = tone.get(
                nm, "B7C0CC" if nm[:2].isdigit() else RPT["sub"])
        try:
            ws.page_setup.orientation = "landscape"
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.sheet_properties.pageSetUpPr.fitToPage = True
            ws.print_options.horizontalCentered = True
            ws.oddHeader.left.text = nm
            ws.oddHeader.left.size = 9
            ws.oddHeader.left.color = "7F7F7F"
            ws.oddFooter.right.text = "&P / &N"
            ws.oddFooter.right.size = 9
        except Exception:
            pass
    if wb.worksheets: wb.active = 0
    return wb


def _save(wb):
    polish_wb(wb)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def build_xlsx_rate(tm: Terms, sig_how: str = "", wb=None, prefix=""):
    """선도이자율 산출내역.

    만기수익률 곡선 → 선형보간 → 부트스트래핑 → 연속복리 현물 → 구간 선도.
    노란 셀을 고치면 끝까지 따라 움직인다.

    ``wb`` 를 주면 그 조서 안에 시트를 더하고 ``(시트이름, 첫 자료행)`` 을 돌려준다.
    트리 시트의 11·12행이 그 표의 G·J 열을 참조하면 이자율까지 살아 있는 조서가 된다.
    """
    from openpyxl import Workbook
    _own = wb is None
    if _own:
        wb = Workbook(); wb.remove(wb.active)
    K = report_kit(wb)
    put, head, sec, cols, note, sheet = (K[x] for x in
        ("put", "head", "sec", "cols", "note", "sheet"))
    YEL = "FFF9DB"
    derive(tm)
    n, T = int(tm.n), tm.T
    dt_ = T/n
    cc = credit_curve(tm)
    if len(tm.rf_curve) < 2 or len(cc) < 2:
        raise ValueError("무위험·위험 곡선을 각각 두 점 이상 넣어야 합니다.")
    spot_in = (tm.y_type == "spot")
    P = prefix
    IN = f"{P}입력곡선"
    R0IN = 8                                   # 입력곡선 첫 자료행
    LEG = [("무위험", tm.rf_curve, int(tm.cmp_rf), "B", "C"),
           ("위험", cc, int(tm.cmp_cr), "E", "F")]

    # ── 표지 ──
    C = sheet(f"{P}표지", tab=RPT["ink"], widths=[26, 22, 18, 18, 16, 16, 16, 16])
    head(C, 2, "이자율 산출내역",
         "만기수익률 곡선에서 할인계수를 순차로 풀고(부트스트래핑), 연속복리 "
         "현물이자율로 바꾼 뒤, 격자 한 구간의 선도이자율을 뽑는 과정이다. "
         "조서 트리 시트 11·12행에 값으로 들어가는 숫자가 여기서 나온다.")
    r = 5
    sec(C, r, "방법"); r += 1
    cols(C, r, ["단계", "내용"], [26, 64]); r += 1
    steps = ([("1. 입력", "현물이자율(제로커브)을 고시된 그대로 받는다"),
              ("2. 연속환산", "연속 = m · ln(1 + r ÷ m).  복리 횟수 m 을 무시하고 "
                            "ln(1+r) 로만 바꾸면 할인계수와 위험중립확률이 어긋난다"),
              ("3. 보간", "고시 만기 사이는 직선으로 잇는다"),
              ("4. 선도", "f(t₀,t₁) = [ r(t₁)·t₁ − r(t₀)·t₀ ] ÷ (t₁ − t₀)")]
             if spot_in else
             [("1. 입력", "만기수익률(YTM) 곡선. 고시 만기 사이는 직선으로 잇는다"),
              ("2. 부트스트래핑", "1 = c·(DF₁+…+DF_k) + DF_k 를 앞에서부터 순차로 푼다. "
                               "c 는 그 만기 수익률 ÷ 연 이표 횟수"),
              ("3. 현물", "연속복리 현물  r(t) = −ln(DF) ÷ t"),
              ("4. 선도", "f(t₀,t₁) = [ r(t₁)·t₁ − r(t₀)·t₀ ] ÷ (t₁ − t₀)")])
    for k, v in steps:
        put(C, r, 2, k, bold=True, border=True, fill=RPT["light"])
        put(C, r, 3, v, border=True, wrap=True)
        C.merge_cells(start_row=r, start_column=3, end_row=r, end_column=8)
        C.row_dimensions[r].height = 30
        r += 1
    r += 1
    sec(C, r, "설정"); r += 1
    cols(C, r, ["항목", "값"], [26, 24]); r += 1
    for k, v in ([("입력 유형", "현물이자율(제로커브)" if spot_in else "만기수익률(YTM)"),
                 ("무위험 복리 횟수 (연)", int(tm.cmp_rf)),
                 ("위험 복리 횟수 (연)", int(tm.cmp_cr)),
                 ("위험 곡선 방식", {"pick": "표에서 등급 하나",
                                  "rating": "두 등급 보간"}.get(tm.rate_mode,
                                                          "직접 입력")),
                 ("위험 곡선 출처", tm.cr_src or "직접 입력"),
                 ("평가기준일", tm.d_base), ("만기일", tm.d_mat),
                 ("잔존기간 T (년)", round(T, 8)), ("노드 수 n", n),
                 ("한 구간 Δt (년)", round(dt_, 8)),
                 ("주가 변동성 σ", tm.sig)]
                + ([("BDT 단기이자율 변동성 σ", tm.bdt_sig),
                    ("BDT σ 산출방식", sig_how or "직접 입력 (산출근거 없음)"),
                    ("BDT 기준 곡선", "위험 곡선에 직접"
                     if tm.bdt_base == 0 else "무위험 + 확정 스프레드")]
                   if put_bdt_on(tm) else [])):
        put(C, r, 2, k, bold=True, border=True, fill=RPT["light"])
        put(C, r, 3, v, border=True,
            fmt=(R_P2 if k in ("주가 변동성 σ", "BDT 단기이자율 변동성 σ") else None),
            align=None if isinstance(v, str) else "right", wrap=True)
        r += 1
    r += 1
    note(C, r, "노란 셀만 입력이다. 만기와 수익률을 고치면 부트스트래핑부터 선도까지 "
               "전부 다시 계산된다. 다만 만기 칸을 늘리거나 줄이려면 앱에서 곡선을 "
               "바꿔 리포트를 다시 만들어야 한다 — 표의 길이는 구조라 수식으로 늘지 않는다.")
    r += 1
    note(C, r, "주가 변동성 σ 는 이자율 산출에 쓰이지 않는다. 선도이자율 시트의 "
               "위험중립가중치 q = [exp((f − δ)·Δt) − d] ÷ (u − d) 에서 u·d 를 만드는 "
               "데만 쓴다 — q 가 이자율과 주가를 잇는 자리라 여기 함께 적어 둔다. "
               "보통주 배당수익률 δ 는 그 드리프트에서만 빠지고 할인율에는 닿지 않는다.")
    r += 1
    if put_bdt_on(tm):
        note(C, r, "BDT 단기이자율 변동성 σ 는 조기상환권을 재는 금리격자에만 쓴다. "
                   "산출방식 줄이 「직접 입력」이면 근거가 조서에 없다는 뜻이다 — "
                   "앱 조서 탭의 「금리변동성 산출내역」 리포트를 함께 철하십시오.")
        r += 1
    note(C, r, "위험 곡선 출처는 앱의 이자율 칸에서 고른 그대로다. 두 등급 보간이면 "
               "아래 입력곡선 시트의 위험 열이 이미 섞인 곡선이고, 등급 하나를 "
               "고르셨으면 고시표의 그 줄이 그대로 들어간다.")

    # ── 입력곡선 ──
    I = sheet(IN, widths=[12, 16, 15, 5, 12, 16, 15])
    head(I, 2, "입력 곡선", "고시된 그대로 적는다. 이 두 표가 리포트 전체의 뿌리다.",
         span=7)
    cols(I, R0IN-1, ["무위험 만기", "수익률", "연속환산", "",
                     "위험 만기", "수익률", "연속환산"],
         [12, 16, 15, 5, 12, 16, 15])
    for (lbl, pts, cmp_, mcol, ycol) in LEG:
        for i, (mt, y) in enumerate(pts):
            rr = R0IN + i
            put(I, rr, 2 if mcol == "B" else 5, mt, fmt="0.####",
                border=True, align="right", fill=YEL)
            put(I, rr, 3 if mcol == "B" else 6, y, fmt=R_P4,
                border=True, align="right", fill=YEL)
            put(I, rr, 4 if mcol == "B" else 7,
                f"={cmp_}*LN(1+{ycol}{rr}/{cmp_})", fmt=R_P4,
                border=True, align="right")
    endr = R0IN + max(len(tm.rf_curve), len(cc)) + 1
    note(I, endr, "연속환산 열은 참고다. 만기수익률을 넣었다면 실제 계산은 다음 두 "
                  "시트의 부트스트래핑에서 하고, 현물이자율을 넣었다면 이 열이 곧 "
                  "쓰이는 값이다.", span=7)

    # ── 곡선별 산출 ──
    made = {}
    for (lbl, pts, cmp_, mcol, ycol) in LEG:
        sn = f"{P}{lbl} 산출"
        W = sheet(sn, widths=[8, 13, 15, 13, 15, 15, 15], freeze="B9")
        R0 = 9
        if spot_in:
            head(W, 2, f"{lbl} — 현물이자율 연속환산", span=6)
            put(W, 5, 2, "연속 = m · ln(1 + r ÷ m)", bold=True, color=RPT["sub"])
            put(W, 6, 2, f"m = {cmp_}  (책 3.7.4.4)", color=RPT["grey"], size=9)
            cols(W, R0-1, ["번호", "만기 t", "고시 수익률", "연속복리 현물"],
                 [8, 13, 16, 16])
            for i, (mt, y) in enumerate(pts):
                rr = R0 + i
                put(W, rr, 2, i+1, fmt=R_N0, border=True, align="center")
                put(W, rr, 3, f"='{IN}'!${mcol}${R0IN+i}", fmt="0.####",
                    border=True, align="right")
                put(W, rr, 4, f"='{IN}'!${ycol}${R0IN+i}", fmt=R_P4,
                    border=True, align="right")
                put(W, rr, 5, f"={cmp_}*LN(1+D{rr}/{cmp_})", fmt=R_P4,
                    border=True, align="right")
            # 보간에 쓸 표 — (만기, 현물) 이 C·E 열에 있다
            grid = [(mt, None) for mt, _ in pts]
            made[lbl] = dict(sh=sn, r0=R0, tcol="C", rcol="E", pts=grid)
            note(W, R0+len(pts)+1, "고시 만기 사이는 다음 시트에서 직선으로 잇는다.",
                 span=6)
        else:
            head(W, 2, f"{lbl} — 부트스트래핑", span=7)
            put(W, 5, 2, "1 = c · (DF₁ + … + DF_k) + DF_k", bold=True, color=RPT["sub"])
            put(W, 6, 2, f"c = 그 만기 수익률 ÷ {cmp_}   (연 {cmp_}회 이표 가정) · "
                         f"현물 = −ln(DF) ÷ t", color=RPT["grey"], size=9)
            cols(W, R0-1, ["k", "만기 t", "보간 수익률", "c", "누적 DF", "DF",
                           "현물 (연속)"], [8, 13, 15, 13, 15, 15, 15])
            N = max(1, int(math.ceil(T*cmp_)))
            for k in range(1, N+1):
                rr, t_ = R0 + k - 1, k/cmp_
                put(W, rr, 2, k, fmt=R_N0, border=True, align="center")
                put(W, rr, 3, round(t_, 12), fmt="0.0000", border=True, align="right")
                put(W, rr, 4, lerp_formula(t_, pts, ycol, R0IN, IN), fmt=R_P4,
                    border=True, align="right")
                put(W, rr, 5, f"=D{rr}/{cmp_}", fmt=R_N6, border=True, align="right")
                put(W, rr, 6, ("=0" if k == 1 else f"=F{rr-1}+G{rr-1}"),
                    fmt=R_N6, border=True, align="right")
                put(W, rr, 7, f"=(1-E{rr}*F{rr})/(1+E{rr})", fmt=R_N6,
                    border=True, align="right")
                put(W, rr, 8, f"=-LN(G{rr})/C{rr}", fmt=R_P4, border=True, align="right")
            # 보간에 쓸 표 — 만기는 C, 현물은 H 열
            made[lbl] = dict(sh=sn, r0=R0, tcol="C", rcol="H",
                             pts=[(k/cmp_, None) for k in range(1, N+1)])
            note(W, R0+N+1, "DF 는 앞 회차 결과를 이어 받는다. 첫 줄의 누적 DF 가 0 인 "
                            "것은 그 앞에 이표가 없기 때문이다.", span=7)

    # ── 선도이자율 ──
    def spot_ref(leg, t):
        """산출 시트의 현물 표에서 t 의 값을 뽑는 수식."""
        d = made[leg]
        pts = [(x, 0.0) for x, _ in d["pts"]]
        return lerp_formula(t, pts, d["rcol"], d["r0"], d["sh"])

    FS = f"{P}선도이자율"
    F = sheet(FS, tab=RPT["green"],
              widths=[8, 13, 13, 14, 14, 14, 14, 14, 14, 14, 12],
              freeze="B10", landscape=True)
    head(F, 2, "구간 선도이자율",
         "격자 한 칸을 건너갈 때 쓰는 이자율이다. 조서 트리 시트의 11행(무위험)과 "
         "12행(위험)에 이 값이 그대로 들어간다.", span=11)
    put(F, 5, 2, "f(t₀,t₁) = [ r(t₁)·t₁ − r(t₀)·t₀ ] ÷ (t₁ − t₀)",
        bold=True, color=RPT["sub"])
    put(F, 6, 2, "q = [ exp(f_무위험 · Δt) − d ] ÷ (u − d),   u = exp(σ√Δt),  d = 1/u",
        color=RPT["grey"], size=9)
    put(F, 7, 2, "Δt", bold=True, border=True, fill=RPT["light"])
    put(F, 7, 3, round(dt_, 12), fmt="0.00000000", border=True, align="right")
    put(F, 7, 4, "σ", bold=True, border=True, fill=YEL)
    put(F, 7, 5, tm.sig, fmt=R_P2, border=True, align="right", fill=YEL)
    put(F, 7, 6, "u", bold=True, border=True, fill=RPT["light"])
    put(F, 7, 7, "=EXP($E$7*SQRT($C$7))", fmt=R_N6, border=True, align="right")
    put(F, 7, 8, "d", bold=True, border=True, fill=RPT["light"])
    put(F, 7, 9, "=1/$G$7", fmt=R_N6, border=True, align="right")
    cols(F, 9, ["스텝", "t₀", "t₁", "무위험 r(t₀)", "무위험 r(t₁)", "무위험 선도",
                "위험 r(t₀)", "위험 r(t₁)", "위험 선도", "스프레드", "q"],
         [8, 13, 13, 14, 14, 14, 14, 14, 14, 14, 12])
    for i in range(n):
        rr = 10 + i
        t0, t1 = i*dt_, (i+1)*dt_
        put(F, rr, 2, i, fmt=R_N0, border=True, align="center")
        put(F, rr, 3, round(t0, 12), fmt="0.000000", border=True, align="right")
        put(F, rr, 4, round(t1, 12), fmt="0.000000", border=True, align="right")
        put(F, rr, 5, spot_ref("무위험", t0), fmt=R_P4, border=True, align="right")
        put(F, rr, 6, spot_ref("무위험", t1), fmt=R_P4, border=True, align="right")
        put(F, rr, 7, f"=(F{rr}*D{rr}-E{rr}*C{rr})/(D{rr}-C{rr})", fmt=R_P4,
            border=True, align="right", bold=True)
        put(F, rr, 8, spot_ref("위험", t0), fmt=R_P4, border=True, align="right")
        put(F, rr, 9, spot_ref("위험", t1), fmt=R_P4, border=True, align="right")
        put(F, rr, 10, f"=(I{rr}*D{rr}-H{rr}*C{rr})/(D{rr}-C{rr})", fmt=R_P4,
            border=True, align="right", bold=True)
        put(F, rr, 11, f"=J{rr}-G{rr}", fmt=R_P4, border=True, align="right")
        put(F, rr, 12, f"=(EXP(G{rr}*$C$7)-$I$7)/($G$7-$I$7)", fmt=R_N4,
            border=True, align="right")
    note(F, 10+n+1, "스프레드가 음수인 줄이 있으면 두 곡선을 바꿔 넣은 것이다. "
                    "q 가 0 과 1 밖으로 나가면 변동성이 너무 낮거나 노드가 너무 성긴 "
                    "것이다 — 격자가 무차익 조건을 못 맞춘다.", span=11)
    # 조서 안에 심은 경우 — 트리 11·12행이 참조할 시트 이름과 첫 자료행
    if not _own: return (FS, 10)
    return _save(wb)


# ══════════════════════════════════════════════════════════
# 5. 엑셀 조서
# ══════════════════════════════════════════════════════════
# RCPS 조서의 용어. 긴 것부터 바꿔야 「조기상환청구권」이 「조기상환」에 먹히지 않는다.
# 수식 셀(=로 시작)은 건드리지 않는다 — 의사결정 문자열("상환P" 등)이 들어 있다.
# 앞·뒤 두 토막 사이에 콜 관련 낱말이 낀다. 제3자 지정 매도청구권을 가진 RCPS 는
# 계약서도 그것을 「매도청구권」이라 부르므로 그 토막만 빼고 바꾼다.
_RCPS_HEAD = [
    ("전환사채", "상환전환우선주"), ("조기상환청구권", "상환청구권"), ("조기상환권", "상환청구권"),
    ("조기상환", "상환청구"),
]
_RCPS_CALL = [
    ("매도청구권자산", "발행자 상환권 가치"),
    ("매도청구 한도", "발행자 상환권 있음(1) / 없음(0)"),
    ("매도청구권", "발행자 상환권"), ("매도청구", "발행자 상환"),
]
_RCPS_TAIL = [
    ("표면이자율", "우선배당률"), ("표면이자", "우선배당"), ("이자 지급주기", "배당 지급주기"),
    ("이자 지급", "배당 지급"), ("지급이자", "지급배당"), ("쿠폰", "우선배당"),
    ("만기보장수익률", "만료 시 상환 보장수익률"),
    ("만기보장 복리", "만료 시 상환 보장 복리"),
    ("만기상환금액", "만료 시 상환금액"), ("만기상환", "만료 시 상환"),
    ("옵션 없는 사채", "옵션 없는 우선주부채"),
    ("순수사채", "순수 우선주부채"), ("순수 사채", "순수 우선주부채"),
    ("없는 사채", "없는 우선주부채"),
    ("사채와 독립적으로", "우선주와 독립적으로"),
    ("사채 + ", "우선주 + "), ("사채요소", "우선주부채"),
    ("전자등록금액", "발행가"), ("전자등록총액", "발행총액"),
    ("액면 100", "발행가 100"), ("트랜치", "격자"),
]
_RCPS_WORDS = _RCPS_HEAD + _RCPS_CALL + _RCPS_TAIL

# BW 는 지분요소의 이름만 갈린다. 사채 쪽 낱말은 CB 와 같다. 긴 낱말을 먼저
# 두어야 「전환권대가」가 「신주인수권대가」로 한 번에 바뀐다.
_BW_WORDS = [
    ("전환사채", "신주인수권부사채"),
    ("전환권대가", "신주인수권대가"), ("전환권조정", "신주인수권조정"),
    ("전환권", "신주인수권"),
    ("전환가격", "행사가격"), ("전환가치", "행사가치"), ("전환비율", "행사비율"),
    ("전환청구기간", "행사기간"), ("전환기간", "행사기간"),
    ("전환청구", "신주인수권 행사"), ("전환확률", "행사확률"),
    ("전환", "행사"),
]

# 의사결정 트리가 쓰는 낱말. 값 조서는 이것을 글자로 담고 수식 조서는 수식이
# 만들어 내므로, 글자만 바꾸면 두 조서가 어긋난다. 그래서 건드리지 않는다.
_DECISIONS = {"전환", "자동전환", "상장전환", "보유", "상환P", "상환C", "행사"}


def inst_words(tm: Terms):
    """이 계약에 적용할 치환 목록.

    제3자 지정 매도청구권(``issuer_call == 2``)이 있으면 콜은 발행자 상환권이
    아니라 매도청구권이므로 그 낱말을 건드리지 않는다. 트랜치도 실제로 한도가
    있는 콜이라 「격자」로 바꾸면 뜻이 사라진다.
    """
    if is_bw(tm):
        return _BW_WORDS
    if not is_rcps(tm):
        return []
    if int(tm.issuer_call) == 2:
        return _RCPS_HEAD + [x for x in _RCPS_TAIL if x[0] != "트랜치"]
    return _RCPS_WORDS


def relabel_inst(wb, tm: Terms):
    """조서의 글자 셀을 그 상품의 용어로 바꾼다. 값·수식은 그대로다."""
    words = inst_words(tm)
    if not words: return
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if not isinstance(v, str) or v.startswith("="): continue
                if v.strip() in _DECISIONS: continue
                for a, b in words:
                    if a in v: v = v.replace(a, b)
                if v != c.value: c.value = v


def _stamp(tm: Terms, kind: str = "") -> str:
    """조서를 만든 시점의 인풋 지문.

    조서는 트리·배분·상각표가 한 계약에서 나와야 성립한다. 만든 뒤 인풋이
    바뀌면 예전 파일은 더 이상 그 계약의 조서가 아니므로 내주지 않는다.
    """
    import hashlib
    d = {k: v for k, v in asdict(tm).items()}
    return hashlib.md5(
        (kind + json.dumps(d, sort_keys=True, default=str)).encode()).hexdigest()


def inst_text(tm: Terms, text: str) -> str:
    """화면 문장을 상품 용어로. CB 면 그대로다."""
    if not isinstance(text, str): return text
    for a, b in inst_words(tm):
        if a in text: text = text.replace(a, b)
    return text


def amort_year(tm: Terms):
    """전기말 장부금액을 **발행일 유효이자율**로 당기만큼 굴린다.

    이 앱의 상각표는 평가기준일 배분액에서 출발하므로 최초 인식 평가에만 맞는다.
    결산 평가에서 필요한 것은 발행일에 정한 유효이자율로 굴려 온 장부금액이라,
    그 둘(전기말 장부금액·발행일 유효이자율)을 받아 당기 회차를 다시 만든다.

    돌려주는 것은 [(회차, 기초, 유효이자, 지급이자, 기말)] 와 기말 장부금액이다.
    """
    if tm.eir_issue < 0 or tm.prev_host < 0: return [], None
    r = float(tm.eir_issue)
    per = max(1e-6, tm.ipay/12)
    c = 100*eff_cpn(tm)*tm.ipay/12
    k = int(tm.cur_periods) or max(1, int(round(12/max(1e-6, tm.ipay))))
    rows, bv = [], float(tm.prev_host)
    for i in range(1, k+1):
        it = bv*((1+r)**per - 1); end = bv + it - c
        rows.append((i, bv, it, c, end)); bv = end
    return rows, bv


def settle_split(tm: Terms, liab_fv: float, liab_bv: float):
    """상환·재매입 대가의 배분 (1032 문단 AG33·AG34).

    > 지급한 대가와 거래원가를 **발행 시점에 배분한 방법과 일관되게** 부채요소와
    > 자본요소에 배분한다. … 부채요소에 관련된 손익은 **당기손익**, 자본요소와
    > 관련된 대가는 **자본**으로 인식한다.

    발행 시점과 일관된 방법이란 「부채요소를 먼저 공정가치로 정하고 나머지를
    자본에 배분」이다 (문단 31·32). 그래서 대가 중 부채 몫은 상환일 부채요소의
    공정가치이고, 나머지가 자본 몫이다. 부채 장부금액과 부채 몫의 차이가
    상환손익이다 — 장부금액이 더 크면 이익이다.
    """
    if tm.settle_amt < 0: return None
    pay = float(tm.settle_amt)
    eq = pay - liab_fv
    return dict(pay=pay, liab_fv=liab_fv, eq=eq, liab_bv=liab_bv,
                pl=liab_bv - liab_fv)


def remeasure(tm: Terms, rows):
    """기말 재평가. 배분표에서 파생상품부채 줄을 모아 당기 공정가치를 잡고
    전기말 장부금액과 견준다. 부채가 늘면 발행회사에는 평가손실이다.

    주계약은 여기서 다루지 않는다 — 상각후원가 장부금액은 발행일의 유효이자율로
    굴린 값이어야 하는데, 이 앱의 상각표는 평가기준일 배분액에서 출발하므로
    최초 인식 평가에만 맞는다. 전기 장부금액은 참고로 함께 보인다.

    복합계약 **전체**를 당기손익-공정가치로 지정했으면 재평가 대상이 파생상품부채가
    아니라 **그 한 줄 전부**다. 상각후원가로 남는 주계약이 없으므로 「파생만 다시
    잰다」는 위 단서도 그때는 해당이 없다.
    """
    _fv = fvpl_on(tm)
    fv_liab = sum(v for k, v in rows[:-1]
                  if ("당기손익-공정가치" in k if _fv else "파생상품부채" in k))
    fv_asset = sum(-v for k, v in rows[:-1] if "파생상품자산" in k)
    has = tm.prev_deriv is not None and tm.prev_deriv >= 0
    pl = (fv_liab - tm.prev_deriv) if has else None      # + 이면 부채 증가 = 손실
    return dict(fv_liab=fv_liab, fv_asset=fv_asset, has=has,
                prev=(tm.prev_deriv if has else None), pl=pl,
                prev_host=(tm.prev_host if (has and tm.prev_host >= 0) else None))


def attach_reports(wb, tm, px=None, rate=None, rate_how="", ir=True):
    """산출내역을 조서 **안에** 시트로 붙인다.

    따로 내려받아 철하는 대신 한 권으로 묶으면, 감사인이 종가나 고시 수익률을
    고쳤을 때 그 결과가 어디까지 번지는지 같은 파일 안에서 따라갈 수 있다.

    돌려주는 것은 (변동성 결과 셀, 금리변동성 결과 셀, (선도이자율 시트, 첫 행)) 다.
    수식 조서는 이 주소를 가정 시트와 트리 11·12행에 꽂아 살아 있는 사슬을 만든다.
    붙이지 못한 자리는 None 이라 종전처럼 값으로 들어간다.
    """
    volref = rvolref = irref = None

    def _agg(series, o, fn=vol_from):
        """산출내역이 낼 연 변동성. 적용값과 같을 때만 트리를 여기에 잇는다."""
        vs = [fn(pxs, o.get("tdays", 250), o.get("drop", True)) for _, pxs in series]
        ann = sorted(v["annual"] for v in vs if v)
        if not ann: return None
        if len(ann) == 1: return ann[0]
        k = o.get("pick", "median")
        if k == "mean": return sum(ann)/len(ann)
        if k == "max": return ann[-1]
        if k == "min": return ann[0]
        return ann[len(ann)//2] if len(ann) % 2 else (ann[len(ann)//2-1]+ann[len(ann)//2])/2

    if px:
        try:
            o = px[1] or {}
            volref = build_xlsx_vol(
                px[0], tdays=o.get("tdays", 250), drop=o.get("drop", True),
                pick=o.get("pick", "median"), applied=tm.sig,
                asof=dt.date.fromisoformat(tm.d_base), kind="stock",
                wb=wb, prefix="σ ")
            # 산출값과 적용값이 다르면 잇지 않는다. 이으면 조서가 화면과 다른
            # σ 로 다시 계산되어 「값 조서 = 수식 조서」가 깨진다. 그 경우에도
            # 시트는 남으므로 산출근거와 차이는 조서에 그대로 보인다.
            _a = _agg(px[0], o)
            if _a is None or abs(_a - tm.sig) > 5e-5: volref = None
        except Exception:
            volref = None
    if rate and put_bdt_on(tm):
        try:
            o = rate[1] or {}
            rvolref = build_xlsx_vol(
                rate[0], tdays=o.get("tdays", 250), drop=o.get("drop", True),
                applied=tm.bdt_sig, asof=dt.date.fromisoformat(tm.d_base),
                kind="rate", how=rate_how, wb=wb, prefix="σr ")
            _a = _agg(rate[0], o, rate_vol)
            if _a is None or abs(_a - tm.bdt_sig) > 5e-5: rvolref = None
        except Exception:
            rvolref = None
    if ir:
        try:
            irref = build_xlsx_rate(tm, rate_how, wb=wb, prefix="IR ")
        except Exception:
            irref = None
    return volref, rvolref, irref


def build_xlsx(tm: Terms, full, b0, b1, b2, ca, conv, eir, attach=None):
    """트리 하나에 시트 하나. 엑셀 트리모델과 같은 구조로 내보낸다."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as gl

    F = "KoPub돋움체 Medium"
    NAVY, SUB, LIGHT, BAND, RFXC = "1F3864", "44618C", "DCE6F1", "F2F5F8", "FCE4D6"
    RED, GREEN, GREY, AMB = "C00000", "006100", "6B7480", "BF8F00"
    thin = Side(style="thin", color="BFC7D0")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    N2, N0, P2, N4, N6 = '#,##0.00', '#,##0', '0.00%', '0.0000', '0.000000'
    DATE = 'yyyy-mm-dd'
    n = tm.n; dt_ = tm.T/n; mper = n/(tm.T*12); R0 = 20
    # 조기상환권을 분리하지 않는 선택이 실제로 살아 있는가. allocate 와 같은
    # 조건이어야 가정 시트·회계처리 표가 배분표와 어긋나지 않는다.
    _nosep = (tm.conv_class == "equity" and tm.k_sep != 0 and int(tm.p_sep) == 0)
    # 계약상 개월 → 평가기준일 기준 스텝. 엔진과 같아야 한다 (경과분을 뺀다).
    stp_lo, stp_hi = step_mapper(tm, n, dt_)
    per_ = lambda mth: max(1, int(round(mth*mper)))   # 주기는 뺄 것이 없다
    RF, CR = curves(tm)
    wb = Workbook(); wb.remove(wb.active)
    # 산출내역은 조서를 다 만든 뒤 뒤쪽에 붙인다 (아래 _tail 에서).

    def put(ws, r, c, v, *, bold=False, color="000000", fill=None, fmt=None,
            size=10, align=None, border=False):
        cl = ws.cell(row=r, column=c, value=xlfn(v))
        cl.font = Font(name=F, size=size, bold=bold, color=color)
        if fill: cl.fill = PatternFill("solid", fgColor=fill)
        if fmt: cl.number_format = fmt
        cl.alignment = Alignment(horizontal=align or "general", vertical="center")
        if border: cl.border = BOX
        return cl
    def title(ws, r, t, span=8):
        put(ws, r, 2, t, bold=True, color="FFFFFF", fill=NAVY, size=13)
        for c in range(3, 2+span): ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=NAVY)
        ws.row_dimensions[r].height = 22
    def sec(ws, r, t, span=8):
        put(ws, r, 2, t, bold=True, color="FFFFFF", fill=SUB, size=10)
        for c in range(3, 2+span): ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=SUB)

    # ── 노드 값 색인 (r = 하락 횟수) ──
    # CB 는 콜 없는 격자를 보이고 매도청구권은 B3 에서만 뺀다 (트랜치 방식).
    # RCPS 의 발행자 상환권은 전체에 걸리는 내재파생이라, 트리는 실제 상품
    # 그대로 — 상환C 가 찍히는 격자 — 를 보이는 편이 읽는 사람에게 맞다.
    fullv = (engine(tm, call=True) if (issuer_redeem(tm) and ca > 0)
             else full)
    idx = {}
    for k, v in fullv["memo"].items():
        i, j = k[0], k[1]
        if (i, j) not in idx: idx[(i, j)] = v
    node = lambda i, r: idx.get((i, i-r))
    S = fullv["S"]
    # 다음 조정일은 평가기준일부터 (주기 − 경과분) 뒤다. 엔진과 같은 오프셋이다.
    rfx_per = max(1, int(round(tm.rfx_cyc*mper)))
    rfx_off = (stp_lo(tm.rfx_cyc*(math.floor(tm.elapsed_m/tm.rfx_cyc) + 1))
               if tm.rfx_cyc > 0 else 1)
    is_rfx = lambda i: (tm.rfx_mode > 0 and i > 0 and i >= rfx_off
                        and (i-rfx_off) % rfx_per == 0)
    REFIXC = {i for i in range(1, n+1) if is_rfx(i)}
    cpn_amt = 100*eff_cpn(tm)*tm.ipay/12
    ey = tm.elapsed_m/12                     # 경과 연수 — 행사금액은 발행일부터 붙는다
    red = 100*(1 + accrue_rate(tm.T + ey, tm.ytm, eff_cpn(tm), tm.ytm_cmp))
    def in_set(i, a, b, fr):
        lo, hi = stp_lo(a), stp_hi(b)
        return lo <= i <= hi and (i-lo) % per_(fr) == 0
    def put_amt(i):
        if not in_set(i, tm.p_s, tm.p_e, tm.p_f): return 0.0
        if tm.p_mode == "accrue":
            return 100*(1 + accrue_rate(i*dt_ + ey, tm.p_yield, eff_cpn(tm), tm.p_cmp))
        return tm.p_rate
    def call_amt(i, on=True):
        if not on or not in_set(i, tm.k_s, tm.k_e, tm.k_f): return 999999
        return 100*(1 + accrue_rate(i*dt_ + ey, tm.k_prem, eff_cpn(tm), tm.k_cmp))

    HEAD = ["Date", "time-step", "Flag(전환)", "Flag(조기상환)", "Flag(매도청구)",
            "Flag(리픽싱)", "조기상환금액", "매도청구금액", "쿠폰", "만기상환",
            "무위험 선도이자율", "위험 선도이자율", "σ", "u", "d", "q", "1−q"]

    def newsheet(name, ttl, note, refs, call_on=True):
        W = wb.create_sheet(name); W.sheet_view.showGridLines = False
        W.column_dimensions["B"].width = 17
        for i in range(n+1): W.column_dimensions[gl(3+i)].width = 9
        for r, nm in enumerate(HEAD, start=1):
            put(W, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
        d0 = dt.date.fromisoformat(tm.d_base)
        for i in range(n+1):
            g = lambda r, v, fm=None, col="000000": put(W, r, 3+i, v, fmt=fm,
                                                        align="center", size=8, color=col)
            g(1, d0 + dt.timedelta(days=round(i*dt_*365)), DATE, GREY)
            g(2, i, N0)
            g(3, 1 if (stp_lo(tm.cv_s) <= i <= stp_hi(tm.cv_e)
                       or (auto_conv(tm) and i == n)) else 0, N0)
            g(4, 1 if in_set(i, tm.p_s, tm.p_e, tm.p_f) else 0, N0)
            g(5, 1 if (call_on and in_set(i, tm.k_s, tm.k_e, tm.k_f)) else 0, N0)
            g(6, 1 if i in REFIXC else 0, N0, RED)
            g(7, round(put_amt(i), 4), N2)
            g(8, round(call_amt(i, call_on), 4), N2)
            g(9, round(cpn_amt if (eff_cpn(tm) > 0 and i > 0
                                   and i % per_(tm.ipay) == 0) else 0.0, 4), N2)
            g(10, round(red if i == n else 0.0, 4), N2)
            if i < n:
                g(11, forward_rate(RF, i*dt_, (i+1)*dt_), P2)
                g(12, forward_rate(CR, i*dt_, (i+1)*dt_), P2)
            g(13, tm.sig, P2); g(14, full["u"], N4); g(15, full["d"], N4)
            g(16, full["q"], N4); g(17, 1-full["q"], N4)
        title(W, 18, ttl, span=min(n+1, 14))
        put(W, 19, 2, "r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
        for i in range(n+1):
            put(W, 19, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                fill=(RFXC if i in REFIXC else LIGHT), border=True)
        for r in range(n+1):
            put(W, R0+r, 2, r, bold=True, size=8, fmt=N0, align="center", fill=LIGHT, border=True)
        put(W, R0+n+2, 2, note, color=GREY, size=9)
        put(W, R0+n+3, 2, "참조: " + refs, color=GREEN, size=9)
        put(W, R0+n+4, 2, "r 은 하락 횟수. 위로 갈수록 주가가 높다.", color=GREY, size=9)
        W.freeze_panes = "C20"
        return W

    def fill_tree(W, fn, fmt=N2, txt=False):
        for i in range(n+1):
            for r in range(i+1):
                v = fn(i, r)
                if v is None: continue
                put(W, R0+r, 3+i, v, fmt=(None if txt else fmt), size=8,
                    align=("center" if txt else "right"))

    # ── 가정 ──
    A = wb.create_sheet("가정"); A.sheet_view.showGridLines = False
    for cc, w in (("B", 32), ("C", 15), ("D", 13), ("E", 13), ("F", 52)):
        A.column_dimensions[cc].width = w
    title(A, 2, "전환사채 평가 조서", span=5)
    put(A, 3, 2, "생성 " + dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        + "   ·   금액은 전자등록금액 100 기준", color=GREY, size=9)
    # 조서를 받은 사람이 무엇을 재고 무엇을 안 쟀는지 표지에서 알아야 한다.
    put(A, 4, 2, SCOPE_NOTE.replace("**", ""), color=GREY, size=9)
    put(A, 5, 2, UNMODELLED_NOTE, color=AMB, size=9)
    blocks = [("1. 모형", [("신용위험 처리", tm.model, None),
        ("조정일 아닌 시점", ["상태확장(정확)", "경로가중치", "확률가중평균", "특정노드선택"][tm.carry], None),
        ("전환권 회계 분류", "파생상품부채" if tm.conv_class == "liability" else "자본", None)]),
      ("2. 계약조건", [("발행일", tm.d_issue, None), ("평가기준일", tm.d_base, None),
        ("만기일", tm.d_mat, None), ("경과기간 (개월)", tm.elapsed_m, N2),
        ("평가기준일 주가", tm.S0, N2), ("현재 전환가액", tm.K0, N2),
        ("잔존기간 (년)", tm.T, N4), ("노드 수", tm.n, N0), ("Δt", dt_, N4),
        ("표면이자율", tm.cpn, P2)]
        + ([("우선배당 처리", ("발행자 재량 — 부채 현금흐름에서 제외 (1032 AG37)"
                              if int(tm.div_mode) == 1 else
                              "미지급분을 상환가액에 가산 — 전체 부채 · 배당은 이자비용"), None)]
           if is_rcps(tm) else [])
        + [("이자 지급주기 (개월)", tm.ipay, N0),
        ("만기보장수익률", tm.ytm, P2),
        ("보장 복리", ("단리" if int(tm.ytm_cmp) <= 0 else f"연 {int(tm.ytm_cmp)}회 복리"), None),
        ("만기상환금액", red, N2)]),
      ("3. 전환가액 조정", [("조정 방식", ["조정 없음", "하향만", "하향+상향"][tm.rfx_mode], None),
        ("조정 주기 (개월)", tm.rfx_cyc, N0), ("최저 조정가액", tm.floor, N2), ("액면가", tm.par, N2)]
        + ([("IPO 조항", "반영" if tm.ipo_on and tm.ipo_px > 0 else "없음", None)]
           + ([("예상 상장 시점 (개월)", tm.ipo_m, N0),
               ("공모가액", tm.ipo_px, N2), ("공모가 배수", tm.ipo_mult, P2),
               ("조정후 전환가격", tm.ipo_px*tm.ipo_mult, N2),
               ("최소공모가격", tm.ipo_min, N2),
               ("상장 시 강제전환", "예" if tm.ipo_conv else "아니오", None)]
              if (tm.ipo_on and tm.ipo_px > 0) else [])
           if is_rcps(tm) else [])),
      ("4. 옵션", [("전환 시작 / 종료 (개월)", tm.cv_s, N0), ("　", tm.cv_e, N0),
        ("조기상환 시작 / 종료 / 주기", tm.p_s, N0), ("　", tm.p_e, N0), ("　 ", tm.p_f, N0),
        ("조기상환 행사금액 산정", "보장수익률 복리" if tm.p_mode == "accrue" else "고정률", None),
        ("조기상환권 회계 처리",
         ("분리하지 않음 · 부채요소에 포함" if _nosep else "분리 · 파생상품부채"),
         None),
        ("매도청구 시작 / 종료 / 주기", tm.k_s, N0), ("　  ", tm.k_e, N0), ("　   ", tm.k_f, N0),
        ("매도청구 프리미엄", tm.k_prem, P2),
        ("매도청구 복리 횟수 (연)", tm.k_cmp, N0), ("매도청구 한도", tm.k_w, P2),
        ("의무보유 전환지연 (개월)", tm.k_lock, N0),
        ("매도청구권 평가방법", K_METHODS[tm.k_method], None),
        ("풋·콜 우선순위", ("발행자 콜 우선 — 콜을 당하면 전환으로만 대응한다" if int(tm.pc_order) == 1 else "투자자 풋 우선 — 통지한 조기상환을 매도청구로 막지 못한다"), None),
        ("매도청구권 회계 처리",
         "별도 금융상품" if tm.k_sep else "복합내재파생에 포함", None)]),
      ("5. 시장 인풋", [("주가 출처", (tm.s0_src or "직접 입력"), None), ("변동성 σ", tm.sig, P2),
                     ("보통주 배당수익률 δ", tm.div_y, P2)]
        + ([("조기상환권 평가", "BDT 금리격자", None),
            ("BDT 단기이자율 변동성 σ", tm.bdt_sig, P2),
            ("BDT 기준 곡선", ("위험 곡선에 직접" if tm.bdt_base == 0
                            else "무위험 + 확정 스프레드"), None)]
           if put_bdt_on(tm) else [("조기상환권 평가", "금리 고정 격자", None)])
        + [
        ("무위험 이표 (연 회)", tm.cmp_rf, N0), ("위험 이표 (연 회)", tm.cmp_cr, N0),
        ("이자율 입력", ("만기수익률 곡선" if tm.y_type == "par"
                     else f"현물이자율 곡선 (무위험 {tm.cmp_rf}회·위험 {tm.cmp_cr}회 복리)"), None),
        (f"{tm.T:.2f}년 무위험 (연속)", RF(tm.T), P2),
        (f"{tm.T:.2f}년 위험 (연속)", CR(tm.T), P2)]),
      ("6. 격자 파라미터", [("상승계수 u", full["u"], N4), ("하락계수 d", full["d"], N4),
        ("위험중립가중치 q · 첫 구간", full["q"], N4),
        # q 는 구간마다 다시 계산된다. 하나만 실으면 뒤쪽이 깨진 것을 조서에서
        # 알 수 없다.
        ("위험중립가중치 q · 최소", full["qmin"], N4),
        ("위험중립가중치 q · 최대", full["qmax"], N4)])]
    r = 7                       # 3~5행은 생성시각·적용범위·미반영 조건이다
    for ttl, items in blocks:
        sec(A, r, ttl, span=5); r += 1
        for nm, v, fm in items:
            put(A, r, 2, nm, border=True)
            put(A, r, 3, v, color=(RED if fm in (None, N0, N2, P2, N4) else "000000"),
                fmt=fm, align="right", border=True)
            r += 1
        r += 1
    put(A, r, 2, "빨강은 입력값입니다. 트리 시트는 계산 결과를 값으로 담았습니다.",
        color=GREY, size=9)

    # ── 트리 시트 ──
    T01 = newsheet("01 주가", "① 주가트리  S = S0 × u^(스텝−r) × d^r",
        "위로 갈수록 상승이 많은 경로다.", "가정")
    fill_tree(T01, lambda i, r: round(S(i, i-r), 2), N2)

    T02 = newsheet("02 전환가격", "② 전환가격트리  조정일이면 주가를 자르고, 아니면 이어받는다",
        "조정일 열은 주황색이다.", "01 · 가정")
    if full["exact"]:
        put(T02, R0+n+6, 2, "상태확장을 골랐으므로 한 노드에 전환가격이 여럿일 수 있어 "
            "삼각형으로 펴지지 않는다. 대표값을 표시했다.", color=RED, size=9)
    fill_tree(T02, lambda i, r: (round(node(i, r)["K"], 2) if node(i, r) else None), N2)

    T03 = newsheet("03 전환비율", "③ 전환비율트리  100 ÷ 전환가격",
        "리픽싱으로 전환가격이 내려가면 받는 주식 수가 늘어난다.", "02")
    fill_tree(T03, lambda i, r: (round(100/node(i, r)["K"], 4) if node(i, r) else None), N4)

    T04 = newsheet("04 전환가치", "④ 전환가치트리  주가 × 전환비율",
        "전환청구기간 밖이면 0이다.", "01 · 03")
    fill_tree(T04, lambda i, r: (round(node(i, r)["cv"], 2) if node(i, r) else None))

    # 앱에서 고른 모형의 트리만 만든다. 값 조서의 GS 시트는 memo 에서 값을 직접
    # 받으므로 TF 트리를 참조하지 않는다. 그래서 서로 독립적으로 넣고 뺄 수 있다.
    # 현금납입 BW 는 지분과 부채가 애초에 갈라져 있어 GS 가 TF 와 같은 값을 낸다.
    # 쓸모없는 GS 시트를 넣지 않고 TF 자리에 신주인수권·사채 트리를 담는다.
    _bwc = bw_cash(tm)
    _tf = tm.model != "GS" or _bwc
    if _tf:
        T05 = newsheet("05 지분가치",
            ("⑤ 신주인수권가치트리  행사가치 − 100" if _bwc else
             "⑤ 지분가치트리  주식으로 받게 될 부분"),
            ("권면액 100 만큼 현금을 내고 그 값어치 주식을 받는다. 지금 행사와 계속 보유 "
             "중 큰 쪽이고, 계속 보유는 다음 열 값을 무위험이자율로 할인한 값이다."
             if _bwc else
             "전환하면 전환가치, 상환하면 0, 보유하면 다음 열 값을 무위험이자율로 할인한 값이다."),
            "04 · 08 · 다음 열 05")
        fill_tree(T05, lambda i, r: (round(node(i, r)["E"], 2) if node(i, r) else None))

        T06 = newsheet("06 부채가치",
            ("⑥ 사채가치트리  신주인수권과 무관하게 남는 사채" if _bwc else
             "⑥ 부채가치트리  현금으로 받게 될 부분"),
            ("조기상환금액과 계속보유를 견주고, 매도청구가 걸리면 그 금액에서 잘린다."
             if _bwc else
             "전환하면 0, 상환하면 그 금액, 보유하면 다음 열 값을 위험 선도이자율로 할인한 값이다."),
            "08 · 다음 열 06")
        fill_tree(T06, lambda i, r: (round(node(i, r)["B"], 2) if node(i, r) else None))

        T07 = newsheet("07 보유가치", "⑦ 보유가치트리  지금 행사하지 않을 때의 값",
            ("신주인수권은 무위험, 사채는 위험 선도이자율로 따로 할인해 더한다."
             if _bwc else
             "지분은 무위험, 부채는 위험 선도이자율로 따로 할인해 더한다. 이것이 TF 모형이다."),
            "다음 열 05 · 06")
        fill_tree(T07, lambda i, r: (round(node(i, r)["hold"], 2) if node(i, r) else None))

        T08 = newsheet("08 의사결정",
            ("⑧ 의사결정트리  상환P · 상환C · 보유 — 사채가 어떻게 끝나는가" if _bwc else
             "⑧ 의사결정트리  전환 · 상환P · 상환C · 보유"),
            ("신주인수권 행사 여부는 ⑤ 를 보라 — 그 칸이 «행사가치 − 100» 과 같으면 "
             "그 노드에서 행사한다." if _bwc else
             "위쪽은 전환, 아래쪽은 상환이 몰린다. 매도청구는 중간 띠에 나타난다."), "04 · 07")
        lab = {"conv": "전환", "put": "상환P", "call": "상환C", "hold": "보유",
               "mat": "만기상환", "auto": "자동전환", "ipo": "상장전환"}
        fill_tree(T08, lambda i, r: (lab.get(node(i, r)["kind"], "") if node(i, r) else None), txt=True)

        T09 = newsheet("09 금융상품가치",
            ("⑨ 금융상품가치트리 = 신주인수권 + 사채" if _bwc else
             "⑨ 금융상품가치트리 = 지분가치 + 부채가치"),
            "네 갈래 중 최적을 고른 뒤의 값이다. 07과 비교하면 어디서 행사가 일어났는지 보인다.",
            "05 · 06")
        fill_tree(T09, lambda i, r: (round(node(i, r)["E"]+node(i, r)["B"], 2) if node(i, r) else None))
    else:
        T10 = newsheet("05 GS 전환확률", "⑤ [GS] 전환확률트리  전환 1 · 현금 0 · 보유면 다음 두 칸의 평균",
            "이 확률로 할인율을 섞는다. 자식에서 가져오므로 순환참조가 없다.", "07 · 다음 열 05")
        fill_tree(T10, lambda i, r: (round(node(i, r)["P"], 4) if node(i, r) else None), N4)

        T11 = newsheet("06 GS 할인율", "⑥ [GS] 위험조정할인율트리  y = 확률 × 무위험 + (1−확률) × 위험",
            "위쪽은 무위험에, 아래쪽은 위험이자율에 가깝다.", "05")
        def gs_rate(i, r):
            o = node(i, r)
            if not o or i >= n: return None
            fr = forward_rate(RF, i*dt_, (i+1)*dt_); fc = forward_rate(CR, i*dt_, (i+1)*dt_)
            return o["P"]*fr + (1-o["P"])*fc
        fill_tree(T11, gs_rate, P2)

        T12 = newsheet("07 GS 금융상품가치", "⑦ [GS] 금융상품가치트리",
            "네 갈래 중 최적을 고른 뒤의 값이다.", "04 · 06")
        fill_tree(T12, lambda i, r: (round(node(i, r)["V"], 2) if node(i, r) else None))

    # ── BDT (조기상환권을 금리격자로 잴 때만) ──
    if put_bdt_on(tm):
        BP, BV = bdt_grid(tm, True)
        _, BV0 = bdt_grid(tm, False)
        d0 = dt.date.fromisoformat(tm.d_base)
        for nm, ttl, note, grid, rate in (
            ("BDT 단기이자율", "BDT 단기이자율격자  r(i,j) = a · exp(2σ·j·√Δt)",
             "로그정규라 이자율이 음수가 되지 않는다. j 는 상승 횟수이고 클수록 "
             "금리가 높다 — 주가 트리와 달리 위로 갈수록 낮다. 기준금리 a 는 곡선을 "
             "정확히 되돌리도록 역산한 값이다.", None, True),
            ("BDT 부채요소", "BDT 부채요소  전환 없는 사채 + 조기상환권",
             "MAX(조기상환금액, 계속보유) 를 고른다. 계속보유는 다음 두 칸을 0.5 씩 "
             "섞어 그 칸의 단기이자율로 할인한 값이다.", BV, False),
            ("BDT 주계약", "BDT 주계약  옵션이 없는 사채",
             "조기상환권을 빼고 같은 격자로 굴린 값이다. 곡선을 정확히 되돌리므로 "
             "⑩ 주계약과 같아야 한다 — 캘리브레이션 검산이다.", BV0, False)):
            W = wb.create_sheet(nm); W.sheet_view.showGridLines = False
            W.column_dimensions["B"].width = 18
            for i in range(n+1): W.column_dimensions[gl(3+i)].width = 9
            for r, h in enumerate(["Date", "time-step", "Flag(조기상환)", "조기상환금액",
                                   "쿠폰", "만기상환", "기준금리 a", "확정 스프레드"],
                                  start=1):
                put(W, r, 2, h, bold=True, size=8, fill=LIGHT, border=True)
            for i in range(n+1):
                g = lambda r, v, fm=None: put(W, r, 3+i, v, fmt=fm,
                                              align="center", size=8)
                g(1, d0 + dt.timedelta(days=round(i*dt_*365)), DATE)
                g(2, i, N0)
                g(3, 1 if BP["in_put"](i) else 0, N0)
                g(4, round(BP["put_a"](i), 4), N2)
                g(5, round(BP["cpn"] if BP["is_pay"](i) else 0.0, 4), N2)
                g(6, round(BP["red"] if i == n else 0.0, 4), N2)
                if i < n:
                    g(7, BP["a"][i], P2); g(8, BP["add"][i], P2)
            title(W, 10, ttl, span=min(n+1, 14))
            put(W, 11, 2, note, color=GREY, size=9)
            put(W, 12, 2, "j ＼ 스텝", bold=True, size=8, fill=LIGHT,
                border=True, align="center")
            for i in range(n+1):
                put(W, 12, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                    fill=LIGHT, border=True)
            for j in range(n+1):
                put(W, 13+j, 2, j, bold=True, size=8, fmt=N0, align="center",
                    fill=LIGHT, border=True)
            for i in range(n+1):
                if rate and i == n: continue          # 만기에는 다음 구간이 없다
                for j in range(i+1):
                    v = BP["r"][i][j] if rate else grid[i][j]
                    put(W, 13+j, 3+i, round(v, 8), fmt=(P2 if rate else N2),
                        size=8, align="right")
            if not rate:
                put(W, 13+n+2, 2, "t = 0", bold=True)
                put(W, 13+n+2, 3, round(grid[0][0], 6), bold=True, fmt=N2,
                    align="right")
            else:
                # 캘리브레이션 검산 — 도달가격을 더하면 시장 무이표채 가격이다.
                # 기준금리 a 를 이 조건에 맞춰 역산했으므로 0 이 나와야 한다.
                QR = 13+n+3
                sec(W, QR-1, f"캘리브레이션 검산 — 격자가 {BP['base_nm']}을 되돌리는가")
                put(W, QR, 2, "j ＼ 스텝", bold=True, size=8, fill=LIGHT,
                    border=True, align="center")
                for i in range(n+1):
                    put(W, QR, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                        fill=LIGHT, border=True)
                for j in range(n+1):
                    put(W, QR+1+j, 2, j, bold=True, size=8, fmt=N0,
                        align="center", fill=LIGHT, border=True)
                for i in range(n+1):
                    for j in range(len(BP["Q"][i])):
                        put(W, QR+1+j, 3+i, round(BP["Q"][i][j], 10), fmt=N6,
                            size=8, align="right")
                for k, (nm, fn) in enumerate((
                        ("모형 무이표채  Σ Q", lambda i: sum(BP["Q"][i])),
                        ("시장 할인계수", lambda i: BP["mkt"][i]),
                        ("차이", lambda i: sum(BP["Q"][i]) - BP["mkt"][i]))):
                    r2 = QR+n+2+k
                    put(W, r2, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
                    for i in range(n+1):
                        put(W, r2, 3+i, round(fn(i), 12), fmt=N6, size=8,
                            align="right", bold=(k == 2))
                put(W, QR+n+5, 2,
                    "도달가격 Q(i,j) 는 그 칸에 이르는 경로의 확률을 그 경로의 "
                    "할인율로 할인해 더한 값이다. 스텝별로 모두 더하면 그 만기의 "
                    "무이표채 가격이 되고, 그것이 시장 할인계수와 같아야 한다 — "
                    "무차익거래 조건이다. 기준금리 a 를 이 조건에 맞춰 이분법으로 "
                    "역산했으므로 차이가 0 이다.", color=GREY, size=9)
            W.freeze_panes = "C13"

    # ── 이자율곡선 ──
    C = wb.create_sheet("이자율곡선"); C.sheet_view.showGridLines = False
    for cc, w in (("B", 12), ("C", 14), ("D", 14), ("E", 14), ("F", 14), ("G", 14)):
        C.column_dimensions[cc].width = w
    title(C, 2, "기간별 이자율", span=6)
    put(C, 3, 2, "선도이자율  f(t, t+Δt) = [ r(t+Δt)×(t+Δt) − r(t)×t ] ÷ Δt", color=GREY, size=9)
    for i, h in enumerate(["시점 (년)", "무위험 현물", "무위험 선도", "위험 현물", "위험 선도", "스프레드"]):
        put(C, 5, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    for k in range(9):
        t_ = tm.T*k/8; i = min(n-1, round(t_/dt_))
        fr = forward_rate(RF, i*dt_, (i+1)*dt_); fc = forward_rate(CR, i*dt_, (i+1)*dt_)
        for j2, v in enumerate([t_, RF(t_), fr, CR(t_), fc, fc-fr]):
            put(C, 6+k, 2+j2, v, fmt=(N2 if j2 == 0 else P2), align="right", border=True)
    if tm.y_type == "par" and len(tm.cr_curve) >= 2:
        sec(C, 16, "부트스트래핑 — 위험 곡선", span=6)
        for i, h in enumerate(["만기 (년)", "만기수익률", "할인계수", "현물 (연속)"]):
            put(C, 17, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for k, (t_, df) in enumerate([x for x in bootstrap_df(tm.cr_curve, tm.T, tm.cmp_cr) if x[0] > 0]):
            for j2, v in enumerate([t_, _lin(tm.cr_curve, t_), df, -math.log(df)/t_]):
                put(C, 18+k, 2+j2, v,
                    fmt=(N2 if j2 == 0 else (N6 if j2 == 2 else P2)), align="right", border=True)

    # ── 결과 ──
    R = wb.create_sheet("결과"); R.sheet_view.showGridLines = False
    for cc, w in (("B", 36), ("C", 14), ("D", 14), ("E", 12), ("F", 46)):
        R.column_dimensions[cc].width = w
    title(R, 2, "평가결과", span=5)
    sec(R, 4, "1. 순차 차감", span=5)
    for i, h in enumerate(["단계", "가치", "차액", "해당 옵션"]):
        put(R, 5, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    steps = [("B0  옵션 없는 사채", b0, None, "—"),
             ("B1  조기상환권 추가", b1, b1-b0, "조기상환청구권"),
             ("B2  전환권 추가", b2, b2-b1, "전환권"),
             ("B3  매도청구권 반영", b2-ca, -ca, f"매도청구권 ({tm.k_w*100:.0f}% 한도)")]
    for i, (k, v, dv, nm) in enumerate(steps):
        last = (i == 3); fl = BAND if last else None
        put(R, 6+i, 2, k, bold=last, fill=fl, border=True)
        put(R, 6+i, 3, v, bold=last, fill=fl, fmt=N2, align="right", border=True)
        put(R, 6+i, 4, dv if dv is not None else "", bold=last, fill=fl, fmt=N2,
            align="right", border=True)
        put(R, 6+i, 5, nm, bold=last, fill=fl, border=True)
    _sc = ipo_scenarios(tm)
    if _sc:
        rr2 = 36
        sec(R, rr2, "4. 상장 시점 가정", span=5)
        for i, h in enumerate(["가정", "전체 (B2)", "발행자 상환권 반영 (B3)", "전환권대가"]):
            put(R, rr2+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, (nm, v, v3, cvv, _d) in enumerate(_sc):
            put(R, rr2+2+i, 2, nm, border=True, bold=(i == 0),
                fill=(BAND if i == 0 else None))
            for j2, x in enumerate([v, v3, cvv]):
                put(R, rr2+2+i, 3+j2, x, fmt=N2, align="right", border=True,
                    bold=(i == 0), fill=(BAND if i == 0 else None))
        put(R, rr2+2+len(_sc), 2, "예상 상장 시점은 가정이다. 첫 줄이 조서에 쓴 가정이고 "
            "나머지는 폭을 보이려고 함께 싣는다. 상장 성공 여부는 그 노드의 주가가 "
            "최소공모가격을 넘는지로 판정한다 (책 [사례 5-5]).", color=GREY, size=9)
    if issuer_redeem(tm) and ca > 0:
        put(R, 10, 2, f"발행자 상환권을 전환권 없는 부채 격자에서 재면 {full.get('ca_debt', 0.0):,.4f} 다. "
            "부채요소·전환권대가 배분에는 이 값을 쓴다 (1032 문단 31 — 비자본 파생 특성은 "
            "부채요소에 포함). 위 B3 의 차액은 전체 격자에서 전환 상승분을 자른 크기다.",
            color=GREY, size=9)
    # 앱에서 고른 모형만 싣는다.
    sec(R, 11, "2. 신용위험 처리 — "
        + ("지분·부채 분리 — 현금납입 BW 는 TF 와 GS 가 같은 값을 낸다" if _bwc else
           "TF · 값을 쪼갠다" if _tf else "GS · 할인율을 섞는다"),
        span=5)
    for i, h in enumerate(["모형", "전체", "지분", "부채", "전환확률"]):
        put(R, 12, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    if _tf:
        put(R, 13, 2, "TF · 값을 쪼갠다", border=True)
        for j2, v in enumerate([fullv["TF"], fullv["E"], fullv["B"]]):
            put(R, 13, 3+j2, v, fmt=N2, align="right", border=True)
        put(R, 13, 6, "", border=True)
    else:
        put(R, 13, 2, "GS · 할인율을 섞는다", border=True)
        for j2, v in enumerate([fullv["GS"], fullv["GS"]*fullv["P"], fullv["GS"]*(1-fullv["P"])]):
            put(R, 13, 3+j2, v, fmt=N2, align="right", border=True)
        put(R, 13, 6, fullv["P"], fmt=N4, align="right", border=True)
    if fullv is not full:
        put(R, 14, 2, "트리 시트와 위 표는 발행자 상환권이 걸린 실제 격자다 (B3). "
            "B2 는 상환권을 뺀 참고값이다.", color=GREY, size=9)
    put(R, 15, 2, "앱에서 고른 방법만 싣습니다. 다른 방법의 값은 이 조서에 없습니다.",
        color=GREY, size=9)
    sec(R, 17, "3. 검산", span=5)
    imm = 100*tm.S0/tm.K0
    al = allocate(tm, full, b0, b1, b2, ca)[0]
    ck = [("위험중립가중치 q · 첫 구간", full["q"], 0 < full["q"] < 1),
          ("위험중립가중치 q · 최소", full["qmin"], not full["qbad"]),
          ("위험중립가중치 q · 최대", full["qmax"], not full["qbad"]),
          ("상승계수 u", full["u"], full["u"] > 1),
          ("전체 ≥ 순수사채가치", b2-full["host"], b2 >= full["host"]-1e-6),
          ("전체 ≥ 즉시 전환가치", b2-imm, not (tm.cv_s <= 0 and b2 < imm-1e-6)),
          ("배분 합계 = 100", al[-1][1], abs(al[-1][1]-100) < 0.01)]
    for i, (k, v, ok) in enumerate(ck):
        put(R, 18+i, 2, k, border=True)
        put(R, 18+i, 3, v, fmt=N4, align="right", border=True)
        put(R, 18+i, 4, "적합" if ok else "확인 필요",
            color=(GREEN if ok else RED), align="center", border=True)

    # ── 회계처리 ──
    E = wb.create_sheet("회계처리"); E.sheet_view.showGridLines = False
    for cc, w in (("B", 34), ("C", 14), ("D", 14), ("E", 18), ("F", 18), ("G", 30)):
        E.column_dimensions[cc].width = w
    title(E, 2, "회계처리", span=6)
    put(E, 3, 2, ("복합계약 **전체**를 당기손익-공정가치 측정 금융부채로 지정했으므로 "
                  "내재파생상품을 분리하지 않고 한 줄로 인식한다 (제1109호 문단 4.2.2 · "
                  "4.3.3(3)). 요소별 배분도 유효이자율 상각도 없다. 매도청구권은 제3자에게 "
                  "이전될 수 있어 별도의 금융상품이라(문단 4.3.1) 이 지정 밖에 남는다."
                  if fvpl_on(tm) else
                  "기업회계기준서 제1032호 문단 31·32 — 부채요소를 먼저 정하고 나머지를 자본에 배분한다. "
                  "매도청구권은 제3자에게 이전될 수 있어 별도의 금융상품이다 (제1109호 문단 4.3.1, "
                  "회계기준원 질의회신 2022-I-KQA006, 금융위 2022.5.3 감독지침). "
                  "전환권이 부채이면 전환권과 조기상환권은 상호의존적이므로 하나의 복합내재파생상품으로 "
                  "전체로서 측정한다 (제1109호 문단 B4.3.4)."),
        color=GREY, size=9)
    if tm.elapsed_m > 0.01:
        put(E, 4, 2, "※ 평가기준일이 발행일보다 뒤입니다. 아래 배분은 최초 인식용이므로 "
            "결산 회계처리에 그대로 쓰지 마십시오. 결산일에 쓰는 것은 파생상품 공정가치뿐이고, "
            "주계약은 발행일 배분액을 유효이자율로 상각한 장부금액입니다.", color=RED, size=9)
    sec(E, 5, "1. 최초 인식 배분", span=5)
    fac = tm.face_total
    for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
        put(E, 6, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    for i, (k, v) in enumerate(al[:-1]):
        put(E, 7+i, 2, k, border=True)
        put(E, 7+i, 3, v, fmt=N2, align="right", border=True)
        put(E, 7+i, 4, v/100*fac, fmt=N0, align="right", border=True)
    rr = 7+len(al)-1
    put(E, rr, 2, "합계", bold=True, fill=BAND, border=True)
    put(E, rr, 3, al[-1][1], bold=True, fill=BAND, fmt=N2, align="right", border=True)
    put(E, rr, 4, al[-1][1]/100*fac, bold=True, fill=BAND, fmt=N0, align="right", border=True)
    put(E, rr+1, 2, f"전자등록총액 {fac:,.0f}원 기준으로 환산했습니다.", color=GREY, size=9)
    sec(E, rr+3, "2. 분개", span=5)
    for i, h in enumerate(["계정", "차변 (100)", "대변 (100)", "차변 (원)", "대변 (원)"]):
        put(E, rr+4, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    # 분개는 배분표(al)를 그대로 뒤집어 만든다. 따로 계산하면 두 표가 어긋난다.
    # 음수 항목(매도청구권 자산)만 차변으로, 나머지는 대변으로 간다.
    je = [("현금", 100.0, None)]
    for k, v in al[:-1]:
        nm = k.split(" · ")[0]
        if v < 0: je.append((f"파생상품자산 ({nm})", -v, None))
        else:     je.append((f"　{nm}", None, v))
    for i, (k, dr, cr) in enumerate(je):
        put(E, rr+5+i, 2, k, size=9, border=True)
        put(E, rr+5+i, 3, dr if dr is not None else "", fmt=N2, align="right", border=True)
        put(E, rr+5+i, 4, cr if cr is not None else "", fmt=N2, align="right", border=True)
        put(E, rr+5+i, 5, dr/100*fac if dr is not None else "", fmt=N0, align="right", border=True)
        put(E, rr+5+i, 6, cr/100*fac if cr is not None else "", fmt=N0, align="right", border=True)
    tr = rr+5+len(je)
    sd = sum(x for _, x, _ in je if x); sc = sum(x for _, _, x in je if x)
    put(E, tr, 2, "합계", bold=True, fill=BAND, border=True)
    for j2, v2 in enumerate([sd, sc, sd/100*fac, sc/100*fac]):
        put(E, tr, 3+j2, v2, bold=True, fill=BAND, fmt=(N2 if j2 < 2 else N0),
            align="right", border=True)
    put(E, tr+2, 2, "최초 인식에는 어떠한 손익도 생기지 않는다. 차변과 대변 합계가 일치해야 한다.",
        color=GREY, size=9)
    put(E, tr+3, 2, "전환권 분류: " + ("파생상품부채 — 주계약을 잔여로"
        if tm.conv_class == "liability" else "자본 — 전환권대가를 잔여로"), color=GREY, size=9)
    # (아래 tr2 가 거래원가 블록 유무에 따라 다음 절의 시작 행을 정한다)
    tr2 = tr+3
    if tm.issue_cost > 0:
        _cs, _c100 = cost_split(tm, al)
        rc = tr+5
        sec(E, rc, "거래원가 배분 (1032 문단 38)", span=6)
        for i, h in enumerate(["요소", "배분액 (100)", "거래원가 몫 (100)", "몫 (원)", "처리"]):
            put(E, rc+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, (k, v, c, how) in enumerate(_cs):
            put(E, rc+2+i, 2, k, border=True, size=9)
            put(E, rc+2+i, 3, v, fmt=N2, align="right", border=True)
            put(E, rc+2+i, 4, c, fmt=N4, align="right", border=True)
            put(E, rc+2+i, 5, c/100*fac, fmt=N0, align="right", border=True)
            put(E, rc+2+i, 6, how, border=True, size=9)
        rt2 = rc+2+len(_cs)
        put(E, rt2, 2, "합계", bold=True, fill=BAND, border=True)
        put(E, rt2, 4, _c100, bold=True, fill=BAND, fmt=N4, align="right", border=True)
        put(E, rt2, 5, _c100/100*fac, bold=True, fill=BAND, fmt=N0, align="right", border=True)
        put(E, rt2+1, 2, "배분된 발행금액에 비례해 나눈다. 매도청구권 자산은 별도의 금융상품이라 "
            "(문단 4.3.1) 분모에서 뺐다. 주계약 몫은 부채에서 차감해 유효이자율에 녹이고, "
            "파생상품부채 몫은 당기손익-공정가치라 즉시 비용, 자본 몫은 자본에서 직접 뺀다.",
            color=GREY, size=9)
        tr2 = rt2+3
    _rm = remeasure(tm, al)
    if _rm["has"]:
        rq = tr2+2
        sec(E, rq, "3. 기말 재평가 — 파생상품부채", span=5)
        for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
            put(E, rq+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        _pl = _rm["pl"]
        rows_rm = [("전기말 장부금액 (입력)", _rm["prev"]),
                   ("당기말 공정가치 (배분표의 파생상품부채)", _rm["fv_liab"]),
                   (("평가손실 — 부채 증가" if _pl >= 0 else "평가이익 — 부채 감소"), abs(_pl))]
        if _rm["prev_host"] is not None:
            rows_rm.append(("주계약 전기말 장부금액 (참고 · 재평가 대상 아님)", _rm["prev_host"]))
        for i, (k, v) in enumerate(rows_rm):
            put(E, rq+2+i, 2, k, border=True, bold=(i == 2), fill=(BAND if i == 2 else None))
            put(E, rq+2+i, 3, v, fmt=N4, align="right", border=True, bold=(i == 2),
                fill=(BAND if i == 2 else None), color=(AMB if i == 0 else "000000"))
            put(E, rq+2+i, 4, v/100*fac, fmt=N0, align="right", border=True, bold=(i == 2),
                fill=(BAND if i == 2 else None))
        rj = rq+2+len(rows_rm)+1
        put(E, rj, 2, ("차) 파생상품평가손실 / 대) 파생상품부채" if _pl >= 0
                       else "차) 파생상품부채 / 대) 파생상품평가이익"), bold=True, size=9)
        put(E, rj, 3, abs(_pl), fmt=N4, align="right", bold=True)
        put(E, rj, 4, abs(_pl)/100*fac, fmt=N0, align="right", bold=True)
        put(E, rj+1, 2, "주계약은 발행일 유효이자율로 상각한 장부금액을 쓴다. 이 조서의 상각표는 "
            "평가기준일 배분액에서 출발하므로 최초 인식 평가에만 맞는다.", color=GREY, size=9)
        tr2 = rj+3
    # ── 당기 이자비용 — 발행일 유효이자율 ──
    _ar, _end = amort_year(tm)
    if _ar:
        ra = tr2+2
        sec(E, ra, "당기 이자비용 — 발행일 유효이자율", span=6)
        for i, h in enumerate(["회차", "기초", "유효이자", "지급이자", "기말"]):
            put(E, ra+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, row in enumerate(_ar):
            for j2, v in enumerate(row):
                put(E, ra+2+i, 2+j2, v, fmt=(N0 if j2 == 0 else N4),
                    align=("center" if j2 == 0 else "right"), border=True, size=9)
        rz = ra+2+len(_ar)
        put(E, rz, 2, "합계", bold=True, fill=BAND, border=True)
        for j2, v in enumerate([sum(x[2] for x in _ar), sum(x[3] for x in _ar)]):
            put(E, rz, 4+j2, v, bold=True, fill=BAND, fmt=N4, align="right", border=True)
        put(E, rz+1, 2, f"전기말 {tm.prev_host:,.4f} → 당기말 {_end:,.4f} · "
            f"유효이자율 {tm.eir_issue:.4%} · {len(_ar)}회차. 상각표 시트는 평가기준일 "
            "배분액에서 출발하므로 결산에는 이 표를 쓴다.", color=GREY, size=9)
        tr2 = rz+3
    # ── 상환·재매입 대가 배분 (AG33·AG34) ──
    _hbv = (_end if _ar else (tm.prev_host if tm.prev_host >= 0 else al[0][1]))
    _ss = settle_split(tm, b1, _hbv)
    if _ss:
        rs = tr2+2
        sec(E, rs, "상환·재매입 대가 배분 (1032 문단 AG33·AG34)", span=5)
        for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
            put(E, rs+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        _pl = _ss["pl"]
        for i, (k, v) in enumerate([
                ("지급대가", _ss["pay"]),
                ("부채 몫 — 상환일 부채요소 공정가치", _ss["liab_fv"]),
                ("자본 몫 — 잔여", _ss["eq"]),
                ("부채 장부금액", _ss["liab_bv"]),
                (("상환이익 (당기손익)" if _pl >= 0 else "상환손실 (당기손익)"), abs(_pl))]):
            r = rs+2+i
            put(E, r, 2, k, border=True, bold=(i == 4), fill=(BAND if i == 4 else None))
            put(E, r, 3, v, fmt=N4, align="right", border=True, bold=(i == 4),
                fill=(BAND if i == 4 else None))
            put(E, r, 4, v/100*fac, fmt=N0, align="right", border=True, bold=(i == 4),
                fill=(BAND if i == 4 else None))
        put(E, rs+8, 2, "발행 시점과 일관되게 — 부채요소를 먼저 공정가치로 정하고 나머지를 "
            "자본에 배분한다 (문단 31·32). 부채요소 관련 손익은 당기손익, 자본요소 관련 "
            "대가는 자본이다 (문단 AG34). 부채 몫은 **상환일**에 다시 잰 값이어야 하므로 "
            "평가기준일을 상환일로 맞추고 그날 곡선을 넣어야 한다.", color=GREY, size=9)

    # ── 상각표 ──
    # 전체를 당기손익-공정가치로 지정했으면 상각할 주계약이 없다. 빈 표를 싣는
    # 대신 **왜 없는지**를 시트 하나로 남긴다 — 조서를 받은 사람이 「상각표가
    # 빠졌다」고 읽으면 안 된다.
    M = wb.create_sheet("상각표"); M.sheet_view.showGridLines = False
    for cc, w in (("B", 10), ("C", 13), ("D", 12), ("E", 16), ("F", 14),
                  ("G", 14), ("H", 16)):
        M.column_dimensions[cc].width = w
    if eir is None:
        title(M, 2, "주계약 상각표 — 만들지 않는다", span=7)
        M.column_dimensions["B"].width = 110
        for _i, _tx in enumerate(FVPL_NOTE if fvpl_on(tm) else HOST_NONPOS_XL):
            put(M, 4+_i, 2, _tx, color=(RED if _i == 0 else GREY),
                bold=(_i == 0), size=(10 if _i == 0 else 9))
            M.cell(row=4+_i, column=2).alignment = Alignment(wrap_text=True,
                                                             vertical="top")
        SP = split_test(tm, full, b0, b1, b2, ca, [])
    else:
        r_eir, rows_eir, redm, nper = eir
        title(M, 2, "주계약 상각표", span=7)
        put(M, 3, 2, "지급일은 계약상 일정이므로 발행일부터 센다. 회차 수는 노드가 아니라 "
            "이자 지급주기를 따른다.", color=GREY, size=9)
        sec(M, 4, "유효이자율 역산", span=7)
        for i, (k, v, fm) in enumerate([("주계약 (인식액, 거래원가 차감 후)"
                                        if tm.issue_cost > 0 else "주계약 (인식액)",
                                        rows_eir[0][2] if rows_eir else b0, N2),
                                        ("만기상환금액", redm, N2),
                                        ("표면이자 (회당)", cpn_amt, N2),
                                        ("상각 횟수", nper, N0)]):
            put(M, 5+i, 2, k, border=True)
            put(M, 5+i, 3, v, fmt=fm, align="right", border=True)
        put(M, 9, 2, "유효이자율 (연, 이산복리)", bold=True, fill=BAND, border=True)
        put(M, 9, 3, r_eir, bold=True, fill=BAND, fmt=P2, align="right", border=True)
        sec(M, 11, "상각 내역", span=7)
        for i, h in enumerate(["회차", "지급일", "경과연수", "기초", "이자비용",
                               "지급이자", "기말"]):
            put(M, 12, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        _di = dt.date.fromisoformat(tm.d_issue); _dm = dt.date.fromisoformat(tm.d_mat)
        for i, row in enumerate(rows_eir):
            last = (i == len(rows_eir)-1); fl = BAND if last else None
            # 마지막은 만기일, 나머지는 발행일 + 회차 × 지급주기다.
            pd_ = (_dm if last else
                   _add_months(_di, int(round(pay_index(tm, row[1])*tm.ipay))))
            put(M, 13+i, 2, row[0], bold=last, fill=fl, fmt=N0, align="right", border=True)
            put(M, 13+i, 3, pd_, bold=last, fill=fl, fmt=DATE, align="right", border=True)
            for j2, v in enumerate(row[1:], start=4):
                put(M, 13+i, j2, v, bold=last, fill=fl, fmt=N2, align="right", border=True)
        # 화면과 같은 함수가 만든 문안이라 둘이 어긋날 수 없다.
        SP = split_test(tm, full, b0, b1, b2, ca, eir[1])

    # ── 분리 판단 ──
    J = wb.create_sheet("분리 판단"); J.sheet_view.showGridLines = False
    J.column_dimensions["B"].width = 24; J.column_dimensions["C"].width = 92
    title(J, 2, "내재파생상품 분리 판단", span=2)
    put(J, 3, 2, "판단 순서 — 기업회계기준서 제1109호 문단 B4.3.5 말미는 제1032호에 "
                 "따라 전환채무상품의 자본요소를 분리하기 전에 내재된 콜옵션이나 "
                 "풋옵션이 주채무계약과 밀접하게 관련되어 있는지를 판단하라고 정한다.",
        color=GREY, size=9)
    # 격자 의사결정의 설계도. 계약을 표로 편 뒤에 분리 판단이 온다.
    for _c, _w in zip("BCDEFG", (20, 16, 22, 26, 34, 20)):
        J.column_dimensions[_c].width = _w
    _r = 5
    sec(J, _r, "계약상 권리 — 격자 의사결정의 설계도", span=6); _r += 1
    for _i, _c in enumerate(RIGHT_COLS):
        put(J, _r, 2+_i, _c, bold=True, fill=LIGHT, align="center", border=True, size=9)
    _r += 1
    for _row in rights_table(tm):
        for _i, _v in enumerate(_row):
            put(J, _r, 2+_i, _v, border=True, size=9, bold=(_i == 0))
        _r += 1
    put(J, _r, 2, "조기상환청구권과 매도청구권이 같은 노드에서 함께 열릴 때 누가 먼저 "
                  "움직이는지는 계약이 정한다 — 수식이 정하는 것이 아니다. 이 조서는 "
                  + ("「발행자 매도청구 우선」" if int(tm.pc_order) == 1
                     else "「투자자 조기상환 우선」")
                  + " 으로 계산했다. 두 행사금액이 다르고 행사기간이 겹치는 자리에서만 "
                    "값이 갈린다.", color=GREY, size=9); _r += 2
    for _k, _nm in (("put", "조기상환청구권"), ("call", "매도청구권")):
        _d = SP[_k]
        sec(J, _r, _nm, span=2); _r += 1
        put(J, _r, 2, "결론", bold=True, border=True)
        put(J, _r, 3, _d["결론"], bold=True, border=True); _r += 1
        for _i, _x in enumerate(_d["이유"]):
            put(J, _r, 2, "판단 근거" if _i == 0 else "", border=True)
            put(J, _r, 3, _x, border=True); _r += 1
        put(J, _r, 2, "기준서", border=True)
        put(J, _r, 3, " · ".join(_d["근거"]) or "—", border=True); _r += 1
        put(J, _r, 2, "평가방법", border=True)
        put(J, _r, 3, _d["평가"].replace("**", ""), border=True); _r += 1
        for _a, _v in _d["지표"].items():
            put(J, _r, 2, _a, border=True)
            put(J, _r, 3, (f"{_v*100:.1f}%" if _a == "차이" else
                           ("예" if _v is True else "아니오" if _v is False
                            else f"{_v:,.4f}")), border=True); _r += 1
        _r += 1
    # 판정과 실제 회계처리 설정이 어긋나면 이 조서 안에서 「분리 판단」 시트와
    # 「회계처리」 시트가 서로 다른 말을 하게 된다. 그 사실을 여기 적어 둔다.
    _mis = [(_nm, SP[_k]) for _k, _nm in
            (("put", "조기상환청구권"), ("call", "매도청구권"))
            if not SP[_k].get("설정일치", True)]
    sec(J, _r, "판정과 회계처리 설정이 맞는가", span=6); _r += 1
    if _mis:
        for _nm, _d in _mis:
            _set = ((("분리 · 파생상품부채" if int(tm.p_sep) else "분리하지 않음 · 부채요소에 포함")
                     if _nm == "조기상환청구권" else
                     ("별도 금융상품" if tm.k_sep else "복합내재파생에 포함")))
            put(J, _r, 2, f"★ {_nm}", bold=True, color=RED, border=True)
            put(J, _r, 3, f"판정은 「{_d['결론']}」 인데 이 조서의 회계처리 설정은 "
                          f"「{_set}」 이다. 배분표와 분개가 위 판정과 다르게 나온다 — "
                          "어느 쪽이 계약에 맞는지 정하고 근거를 남겨야 한다.",
                color=RED, border=True); _r += 1
    else:
        put(J, _r, 2, "일치", bold=True, border=True)
        put(J, _r, 3, "위 판정과 이 조서의 회계처리 설정이 같다. 배분표·분개·상각표가 "
                      "판정대로 만들어졌다.", border=True); _r += 1
    _r += 1
    put(J, _r, 2, "이 시트는 앱의 「분리 판단」 화면과 같은 함수가 만든다. 계약 조항 "
                  "확인 항목을 **앱에서** 바꾸면 결론과 문안이 함께 바뀐다. "
                  "이 시트는 그 결과를 옮겨 적은 것이라 수식이 없다.  판정에 쓰는 "
                  "상각후원가는 문단 B4.3.5(5)(가) 대로 **주계약(B0)** 기준이라 "
                  "분리 여부 설정과 무관하다.",
        color=GREY, size=9)
    for _row in J.iter_rows(min_row=5, max_row=_r, min_col=3, max_col=3):
        for _c in _row: _c.alignment = Alignment(wrap_text=True, vertical="top")

    # ── 해설 ──
    write_check_sheets(wb, tm, model_checks(tm, full, b0, b1, b2, ca, eir))

    H = wb.create_sheet("해설", 0); H.sheet_view.showGridLines = False
    H.column_dimensions["B"].width = 22; H.column_dimensions["C"].width = 96
    title(H, 2, "이 조서를 읽는 법", span=2)
    ex = [("시트 순서", ""),
      ("구조", "트리 하나가 시트 하나다. 가정 → 01 주가 → … → 12 GS → 결과 → 검산요약 → 회계처리."),
      ("따라가기", "시트 탭을 왼쪽부터 차례로 누르면 계산이 쌓이는 순서 그대로다."),
      ("", ""),
      ("머리 17행은 모두 같다", ""),
      ("1행 Date", "평가기준일 + 스텝 × Δt. 계약상 행사일과 대조해 보는 자리다."),
      ("1~2행", "날짜와 스텝 번호"),
      ("3~6행", "Flag — 전환 · 조기상환 · 매도청구 · 리픽싱이 가능한 열에 1이 뜬다"),
      ("7~10행", "조기상환금액 · 매도청구금액 · 쿠폰 · 만기상환"),
      ("11~12행", "무위험 선도이자율과 위험 선도이자율"),
      ("13~17행", "σ · u · d · q · 1−q"),
      ("", ""),
      ("행은 하락 횟수다", ""),
      ("r = 0", "한 번도 안 내린 경로. 맨 위이고 주가가 가장 높다."),
      ("r = 스텝", "계속 내린 경로. 맨 아래다."),
      ("빈칸", "그 시점에 존재하지 않는 노드다."),
      ("", ""),
      ("계산 순서", ""),
      ("만기부터", "만기에는 미래가 없어 전환가치·조기상환·만기상환만 비교하면 끝난다."),
      ("한 칸씩 왼쪽", "07 보유가치가 다음 열의 05·06을 가져와 할인한다."),
      ("그다음", "09 금융상품가치가 최적을 고르고, 08 의사결정이 이름을 붙이고, 05·06이 확정된다."),
      ("순환이 아닌 이유", "05·06은 같은 열의 08을 보지만, 07은 다음 열의 05·06을 본다."),
      ("", "오른쪽 열이 먼저 확정되고 왼쪽으로 오므로 고리가 닫히지 않는다."),
      ("", ""),
      ("TF와 GS", ""),
      ("TF", "05~09. 값을 지분과 부채로 쪼개 각각 다른 이자율로 할인한다."),
      ("GS", "10~12. 값은 하나로 두고 전환확률로 할인율을 섞는다."),
      ("비교", "09와 12의 같은 칸을 비교하면 두 모형의 차이가 그 노드에서 얼마인지 보인다."),
      ("", ""),
      ("노드에서 무엇을 고르는가", ""),
      ("투자자 권리", "전환 · 조기상환청구 · 보유. 셋 중 자기에게 가장 유리한 것을 고른다."),
      ("발행자 권리", "매도청구. 투자자 가치를 눌러 내리는 쪽으로만 쓴다."),
      ("적용한 식", "MAX(전환, MIN(MAX(보유, 조기상환), 매도청구))　— 발행자 매도청구 우선" if int(tm.pc_order) == 1 else "MAX(전환, 조기상환, MIN(보유, 매도청구))　— 투자자 조기상환 우선"),
      ("우선순위", "두 권리가 같은 노드에서 함께 열릴 때 누가 먼저 움직이는지는 "
              "계약이 정한다 — 수식이 정하는 것이 아니다. 두 행사금액이 다르고 "
              "행사기간이 겹치는 자리에서만 값이 갈린다. 「분리 판단」 시트의 "
              "「계약상 권리」 표에 고른 근거를 남겨야 한다."),
      ("동점 처리", "전환은 허용오차(1e-9)만큼 앞설 때만 이긴다. 동점이면 현금(상환)이다. "
              "리픽싱이 주가로 재설정되는 날에는 전환가치가 정확히 100 이 되어 "
              "조기상환금액과 동점이 되는데, 정해 두지 않으면 부동소수 잡음이 갈라 놓는다."),
      ("만기 노드", "매도청구는 없다. 전환가치와 현금(MAX(조기상환금액, 만기상환금액) + 이자) "
              "둘만 견준다."),
      ("두 모형이 한 노드에", "한 노드가 **두 모형을 함께 담는다** — 지분·부채 두 줄은 "
              "TF(Tsiveriotis–Fernandes) 이고, 금융상품가치 한 줄은 GS(Goldman Sachs) 다. "
              "그래서 «금융상품가치 ≠ 지분 + 부채» 인 것이 정상이다. 결함이 아니라 두 "
              "모형이 같은 계약을 다르게 재는 것이다 — 지분+부채는 TF 결과와, "
              "금융상품가치는 GS 결과와 각각 정확히 맞는다."),
      ("결정은 한 곳에서", "전환사채·우선주와 신주인수권부사채가 **같은 판정 함수**를 "
              "쓴다. 상품마다 다른 것은 「이겼을 때 무엇을 받는가」뿐이다 — 예전에는 "
              "판정까지 복제되어 있어 한쪽만 고쳐지는 일이 있었다."),
      ("", ""),
      ("이 파일의 성격", ""),
      ("값 조서", "평가앱이 계산한 결과를 값으로 담았다. 수식이 아니므로 셀을 바꿔도 다시 계산되지 않는다."),
      ("재계산", "인풋을 바꾸려면 앱에서 다시 계산한 뒤 조서를 새로 내려받으면 된다.")]
    r = 4
    for a2, b3 in ex:
        if a2 and not b3: sec(H, r, a2, span=2)
        elif a2:
            put(H, r, 2, a2, bold=True, size=9); put(H, r, 3, b3, size=9)
        r += 1
    for i in range(4, r):
        H.cell(row=i, column=3).alignment = Alignment(horizontal="left", vertical="center")

    _attached = []
    if attach:
        _pre = list(wb.sheetnames)
        attach_reports(wb, tm, **attach)
        _attached = [x for x in wb.sheetnames if x not in _pre]
    if _attached:
        # 산출내역은 조서를 다 읽은 뒤에 보는 부록이라 뒤로 보낸다.
        _rest = [w for w in wb._sheets if w.title not in _attached]
        _tail = [w for w in wb._sheets if w.title in _attached]
        wb._sheets = _rest + _tail
    polish_wb(wb)
    relabel_inst(wb, tm)
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue()


def build_xlsx_formula(tm: Terms, full, b0, b1, b2, ca, conv, eir, attach=None):
    """수식 조서 — 트리를 살아 있는 수식으로 내보낸다.
    가정 시트의 노란 셀을 바꾸면 엑셀 안에서 다시 계산된다.
    재결합 격자가 필요하므로 근사 방법에서만 만들 수 있다."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as gl

    F = "KoPub돋움체 Medium"
    NAVY, SUB, LIGHT, BAND, RFXC = "1F3864", "44618C", "DCE6F1", "F2F5F8", "FCE4D6"
    RED, GREEN, GREY, AMB = "C00000", "006100", "6B7480", "BF8F00"
    thin = Side(style="thin", color="BFC7D0")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    N2, N0, P2, N4, N6 = '#,##0.00', '#,##0', '0.00%', '0.0000', '0.000000'
    DATE = 'yyyy-mm-dd'
    n = tm.n; dt_ = tm.T/n; mper = n/(tm.T*12); R0 = 20
    # 조기상환권을 분리하지 않는 선택이 실제로 살아 있는가. allocate 와 같은
    # 조건이어야 가정 시트·회계처리 표가 배분표와 어긋나지 않는다.
    _nosep = (tm.conv_class == "equity" and tm.k_sep != 0 and int(tm.p_sep) == 0)
    el = tm.elapsed_m
    # 트랜치 이름은 계약의 매도청구 한도에서 나온다. 30/70 으로 굳혀 두면
    # 한도가 다른 사채에서 시트 이름이 계약과 어긋난다.
    if issuer_redeem(tm):
        KW, KW0 = "발행자 상환권 반영", "발행자 상환권 없음(참고)"
    else:
        KW = f"{tm.k_w*100:,.0f}%"
        KW0 = f"{(1-tm.k_w)*100:,.0f}%"
    stp_lo, stp_hi = step_mapper(tm, n, dt_)
    RF, CR = curves(tm)
    # 다음 조정일은 평가기준일부터 (주기 − 경과분) 뒤다. 엔진과 같은 오프셋이다.
    rfx_per = max(1, int(round(tm.rfx_cyc*mper)))
    rfx_off = (stp_lo(tm.rfx_cyc*(math.floor(tm.elapsed_m/tm.rfx_cyc) + 1))
               if tm.rfx_cyc > 0 else 1)
    is_rfx = lambda i: (tm.rfx_mode > 0 and i > 0 and i >= rfx_off
                        and (i-rfx_off) % rfx_per == 0)
    REFIXSET = {i for i in range(1, n+1) if is_rfx(i)}
    wb = Workbook(); wb.remove(wb.active)
    # 산출내역을 **먼저** 붙여야 트리 11·12행과 가정 시트가 그 셀을 참조할 수 있다.
    _volref = _rvolref = _irref = None
    _attached = []
    if attach:
        _pre = list(wb.sheetnames)
        _volref, _rvolref, _irref = attach_reports(wb, tm, **attach)
        _attached = [x for x in wb.sheetnames if x not in _pre]

    def put(ws, r, c, v, *, bold=False, color="000000", fill=None, fmt=None,
            size=10, align=None, border=False):
        cl = ws.cell(row=r, column=c, value=xlfn(v))
        cl.font = Font(name=F, size=size, bold=bold, color=color)
        if fill: cl.fill = PatternFill("solid", fgColor=fill)
        if fmt: cl.number_format = fmt
        cl.alignment = Alignment(horizontal=align or "general", vertical="center")
        if border: cl.border = BOX
        return cl

    def title(ws, r, t, span=8):
        put(ws, r, 2, t, bold=True, color="FFFFFF", fill=NAVY, size=13)
        for c in range(3, 2+span):
            ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=NAVY)
        ws.row_dimensions[r].height = 22

    def sec(ws, r, t, span=8):
        put(ws, r, 2, t, bold=True, color="FFFFFF", fill=SUB, size=10)
        for c in range(3, 2+span):
            ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=SUB)

    # ── 가정 ─────────────────────────────────────────────
    A = wb.create_sheet("가정"); A.sheet_view.showGridLines = False
    for cc, w in (("B", 30), ("C", 16), ("D", 46)): A.column_dimensions[cc].width = w
    title(A, 1, "전환사채 평가 조서 — 수식 포함", span=3)
    put(A, 2, 2, "노란 셀을 바꾸면 모든 트리 시트가 다시 계산된다.", color=GREY, size=9)
    spec = [
        # 진짜 날짜로 넣는다. 각 트리 머리 1행이 이 셀로 스텝 날짜를 계산한다.
        ("발행일", "d_issue", dt.date.fromisoformat(tm.d_issue), DATE, True),
        ("평가기준일", "d_base", dt.date.fromisoformat(tm.d_base), DATE, True),
        ("만기일", "d_mat", dt.date.fromisoformat(tm.d_mat), DATE, True),
        ("경과기간 (개월)", "elm", tm.elapsed_m, N2, True),
        ("평가기준일 주가", "S0", tm.S0, N2, True),
        ("주가 출처", "s0src", (tm.s0_src or "직접 입력"), None, True),
        ("현재 전환가액", "K0", tm.K0, N2, True),
        ("잔존기간 T (년)", "T", tm.T, N4, True),
        ("노드 수 n", "n", n, N0, True),
        ("Δt", "dt", "@=C{T}/C{n}", N4, False),
        ("표면이자율", "cpn", tm.cpn, P2, True),
        ("이자 지급주기 (스텝)", "ipay", max(1, int(round(tm.ipay*mper))), N0, True),
        # 지급일은 계약(발행일) 기준이다. 평가기준일에서 다시 세면 결산 평가에서
        # 지급일이 밀린다 — 첫 조정 스텝(roff)과 같은 방식으로 잡는다.
        ("첫 지급 스텝", "payoff", pay_offset(tm, stp_lo), N0, True),
        ("이자 지급주기 (개월)", "ipaym", tm.ipay, N2, True),
        ("만기보장수익률", "ytm", tm.ytm, P2, True),
        ("만기보장 복리 횟수", "ycm", tm.ytm_cmp, N0, True),
        ("만기상환금액", "red",
         # 할증금은 음수가 될 수 없다. 엔진의 accrue_rate 와 같이 0 에서 끊는다.
         "@=IF(C{ytm}<=0,100*(1+MAX(0,(C{ytm}-C{cpn})*(C{T}+C{elm}/12))),"
         "100*(1+IF(C{ycm}<=0,MAX(0,(C{ytm}-C{cpn})*(C{T}+C{elm}/12)),"
         "MAX(0,(C{ytm}-C{cpn})/C{ytm}*"
         "((1+C{ytm}/MAX(1,C{ycm}))^(MAX(1,C{ycm})*(C{T}+C{elm}/12))-1)))))", N2, False),
        ("최저 조정가액", "flr", tm.floor, N2, True),
        ("액면가", "par", tm.par, N2, True),
        # 상향 재조정의 상한은 **최초** 전환가액이다. 이미 하향 조정된 상품을
        # 결산 평가하면 현재 전환가액과 갈리므로 따로 받는다.
        ("리픽싱 상한 (최초 전환가액)", "cap", k_cap(tm), N2, True),
        ("리픽싱 주기 (스텝)", "cyc", max(1, int(round(tm.rfx_cyc*mper))), N0, True),
        ("첫 조정 스텝", "roff", rfx_off, N0, True),
        ("전환 시작 (스텝)", "cvs", stp_lo(tm.cv_s), N0, True),
        ("전환 종료 (스텝)", "cve", stp_hi(tm.cv_e), N0, True),
        ("조기상환 시작 (스텝)", "pst", stp_lo(tm.p_s), N0, True),
        ("조기상환 종료 (스텝)", "pen", stp_hi(tm.p_e), N0, True),
        ("조기상환 주기 (스텝)", "frq", max(1, int(round(tm.p_f*mper))), N0, True),
        ("조기상환 행사금액", "prate", tm.p_rate, N2, True),
        ("조기상환 보장수익률", "pyld", tm.p_yield, P2, True),
        ("보장 복리 (연 회)", "pcmp", tm.p_cmp, N0, True),
        ("매도청구 시작 (스텝)", "kst", stp_lo(tm.k_s), N0, True),
        ("매도청구 종료 (스텝)", "ken", stp_hi(tm.k_e), N0, True),
        ("매도청구 주기 (스텝)", "kfrq", max(1, int(round(tm.k_f*mper))), N0, True),
        ("매도청구 프리미엄", "prem", tm.k_prem, P2, True),
        ("매도청구 복리 횟수 (연)", "kcmp", tm.k_cmp, N0, True),
        ("매도청구 한도", "cw", tm.k_w, P2, True),
        # 계약 우선순위는 트리 구조를 정한다. 엑셀에서 바꿔도 수식이 따라오지
        # 않으므로 흰 셀(입력 아님)로 두고 앱에서 고른 것을 적어만 둔다.
        ("풋·콜 우선순위", "pcord",
         ("발행자 콜 우선 — 콜을 당하면 전환으로만 대응한다" if int(tm.pc_order) == 1 else "투자자 풋 우선 — 통지한 조기상환을 매도청구로 막지 못한다"), None, False),
        (f"{KW} 전환 시작 (스텝)", "cv30", stp_lo(max(tm.cv_s, tm.k_lock)), N0,
         tm.k_method == 0),
        ("변동성 σ", "sig",
         (f"={_volref}" if _volref else tm.sig), P2, not _volref),
        # 배당수익률은 드리프트에서만 빠진다 — 할인율에는 손대지 않는다.
        ("보통주 배당수익률 δ", "divy", tm.div_y, P2, True),
        ("상승계수 u", "u", "@=EXP(C{sig}*SQRT(C{dt}))", N4, False),
        ("하락계수 d", "dd", "@=1/C{u}", N4, False),
        ("위험중립가중치 q", "q",
         "@=(EXP((C{rfc}-C{divy})*C{dt})-C{dd})/(C{u}-C{dd})", N4, False),
        ("1 − q", "q1", "@=1-C{q}", N4, False),
        ("리픽싱 반영 (1/0)", "rfx", 1 if tm.rfx_mode > 0 else 0, N0, True),
        ("상향 조정 (1/0)", "up", 1 if tm.rfx_mode == 2 else 0, N0, True),
        ("조정일 처리 (1/2/3)", "mth", max(1, tm.carry), N0, True),
        ("전환권 분류 (1 자본 / 0 부채)", "eqcls", 1 if tm.conv_class == "equity" else 0, N0, True),
        ("상품 (0 CB / 1 RCPS)", "inst", 1 if is_rcps(tm) else 0, N0, False),
        # 존속기간 만료 시 보통주 자동전환. 만기 노드 수식이 모두 이 셀을 보므로
        # 엑셀에서 바꿔도 따라온다. CB 는 0 이다.
        ("존속기간 만료 시 자동전환 (1/0)", "auto", 1 if auto_conv(tm) else 0, N0, True),
        # IPO 조항 (책 [사례 5-5]). 상장 스텝은 격자 구조가 아니라 조건이라
        # 엑셀에서 바꿔도 트리가 따라온다.
        ("IPO 반영 (1/0)", "ipoon",
         1 if (is_rcps(tm) and tm.ipo_on and tm.ipo_px > 0) else 0, N0, True),
        ("상장 스텝", "ipos", stp_lo(tm.ipo_m), N0, True),
        ("공모가액", "ipopx", tm.ipo_px, N2, True),
        ("공모가 배수", "ipomul", tm.ipo_mult, P2, True),
        ("조정후 전환가격", "ipok", "@=C{ipopx}*C{ipomul}", N2, False),
        ("최소공모가격", "ipomin", tm.ipo_min, N2, True),
        ("상장 시 강제전환 (1/0)", "ipocv", int(tm.ipo_conv), N0, True),
        ("매도청구권 평가방법 (0 유무가치 / 1 혼합할인율 / 2 지분·부채 분리)",
         "kmeth", tm.k_method, N0, False),
        ("조기상환권 처리 (1 분리 / 0 부채요소에 포함)", "psep", int(tm.p_sep), N0, True),
        # 전체 지정이면 배분표가 한 줄이 되고 상각표를 만들지 않는다. 트리는
        # 그대로다 — 평가가 아니라 **인식**을 바꾸는 스위치다.
        ("복합계약 전체 당기손익-공정가치 지정", "fvpl",
         ("지정 — 배분표 한 줄 · 상각표 없음 · 거래원가 즉시 비용"
          if fvpl_on(tm) else "지정하지 않음 — 요소별 배분"), None, False),
        ("매도청구권 처리 (1 별도 금융상품 / 0 내재파생 포함)", "ksep", tm.k_sep, N0, True),
        ("신용위험 처리 (0 TF / 1 GS)", "mdl", 1 if tm.model == "GS" else 0, N0, False),
        ("조기상환권 (0 금리고정 / 1 BDT)", "pbdt", 1 if put_bdt_on(tm) else 0, N0, False),
        ("BDT 변동성 σ", "bsig",
         (f"={_rvolref}" if _rvolref else tm.bdt_sig), P2, not _rvolref),
        ("BDT 기준 (0 위험곡선 / 1 무위험+스프레드)", "bbase", tm.bdt_base, N0, False),
        ("전자등록총액 (원)", "face", tm.face_total, N0, True),
        # 기말 재평가. 음수면 「없음」이다 — 발행 시점 평가.
        ("발행 거래원가 (원)", "cost", tm.issue_cost, N0, True),
        ("전기말 파생상품부채 장부금액 (음수 = 없음)", "pdrv", tm.prev_deriv, N4, True),
        ("전기말 주계약 장부금액 (음수 = 없음)", "phst", tm.prev_host, N4, True),
        ("무위험 (연속, 평탄)", "rfc", RF(tm.T), P2, False)]
    if is_rcps(tm):
        # 계산에 쓰는 배당률은 「계약 배당률 × (재량이면 0)」 이다. 엑셀에서 처리
        # 스위치를 바꾸면 모든 트리와 상환가액 산식이 따라온다.
        _i = next(i for i, x in enumerate(spec) if x[1] == "cpn")
        spec[_i:_i+1] = [
            ("표면이자율 (계약)", "cpnc", tm.cpn, P2, True),
            ("우선배당 처리 (0 상환가액 가산 / 1 재량)", "dmode", int(tm.div_mode), N0, True),
            ("표면이자율 (계산에 쓰는 값)", "cpn", "@=IF(C{dmode}=1,0,C{cpnc})", P2, False)]
    ROWN = {key: 3+i for i, (_, key, _, _, _) in enumerate(spec)}
    K = {key: f"가정!$C${r}" for key, r in ROWN.items()}
    for i, (nm, key, v, fm, inp) in enumerate(spec):
        r = 3+i
        put(A, r, 2, nm, border=True)
        val = v.lstrip("@").format(**ROWN) if (isinstance(v, str) and v.startswith("@")) else v
        put(A, r, 3, val, color=(RED if inp else "000000"),
            fill=("FDF6DD" if inp else None), fmt=fm, align="right", border=True)
    nb = 3+len(spec)+1
    put(A, nb, 2, "노란 셀이 입력값이다. 스텝은 평가기준일 기준이며, "
        "행사금액은 발행일부터 붙는다.", color=GREY, size=9)
    put(A, nb+1, 2, "선도이자율은 부트스트래핑 결과라 각 트리 시트 11·12행에 값으로 들어 있다.",
        color=AMB, size=9)
    # 흰 셀 가운데 「방법을 고르는」 것들은 트리 구조 자체를 정하므로 엑셀에서
    # 바꿔도 따라오지 않는다. 그 사실을 여기서 못박아 둔다.
    put(A, nb+2, 2, "흰 셀 가운데 평가방법·신용위험 처리·BDT 관련 줄은 "
        "**앱에서 고른 값**이다. 이 조서에는 고른 방법의 트리만 들어 있어서 "
        "여기서 숫자를 바꿔도 트리가 따라오지 않는다. 방법을 바꾸려면 앱에서 "
        "바꾸고 조서를 다시 만들어야 한다.", color=RED, size=9)
    # 조서를 받은 사람이 무엇을 재고 무엇을 안 쟀는지 알아야 한다.
    put(A, nb+4, 2, SCOPE_NOTE.replace("**", ""), color=GREY, size=9)
    put(A, nb+5, 2, UNMODELLED_NOTE, color=AMB, size=9)
    if put_bdt_on(tm):
        put(A, nb+3, 2, "BDT 변동성 σ 도 마찬가지다. BDT 격자의 기준금리 a 는 "
            "「σ 가 지금 값일 때 시장 금리곡선을 맞추도록」 역산한 값이라 조서에 "
            "숫자로 박혀 있다. 여기서 σ 만 바꾸면 격자는 움직이는데 a 는 그대로라 "
            "시장 곡선과 어긋난다 — 「BDT 단기이자율」 시트의 캘리브레이션 검산이 "
            "그때 틀어진다. σ 를 바꾸려면 앱에서 바꾸십시오.", color=RED, size=9)

    HEAD = ["Date", "time-step", "Flag(전환)", "Flag(조기상환)", "Flag(매도청구)",
            "Flag(리픽싱)", "조기상환금액", "매도청구금액", "쿠폰", "만기상환",
            "무위험 선도이자율", "위험 선도이자율", "σ", "u", "d", "q", "1−q"]
    ey = el/12

    def newsheet(name, ttl, note, refs, call_on=True, conv_cell=None):
        W = wb.create_sheet(name); W.sheet_view.showGridLines = False
        W.column_dimensions["B"].width = 17
        for i in range(n+1): W.column_dimensions[gl(3+i)].width = 9
        cvs = conv_cell or K["cvs"]
        for r, nm in enumerate(HEAD, start=1):
            put(W, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            g = lambda r, v, fm=None, col="000000": put(W, r, 3+i, v, fmt=fm,
                                                        align="center", size=8, color=col)
            # 머리는 모두 2행(스텝)을 참조한다. 2행 자신도 직전 열 + 1 이라,
            # 맨 앞 열의 0 하나에서 모든 열이 줄줄이 정해진다.
            st = f"{L}$2"                            # 이 열의 스텝
            yr = f"({st}*{K['dt']}+{K['elm']}/12)"   # 발행일부터 흐른 연수
            g(1, f"={K['d_base']}+{st}*{K['dt']}*365", DATE, GREY)
            g(2, (0 if i == 0 else f"={Lp}$2+1"), N0)
            g(3, f"=IF(OR(AND({st}>={cvs},{st}<={K['cve']}),"
                 f"AND({K['auto']}=1,{st}={K['n']})),1,0)", N0)
            g(4, f"=IF(AND({st}>={K['pst']},{st}<={K['pen']},"
                 f"MOD({st}-{K['pst']},{K['frq']})=0),1,0)", N0)
            g(5, (f"=IF(AND({st}>={K['kst']},{st}<={K['ken']},"
                  f"MOD({st}-{K['kst']},{K['kfrq']})=0),1,0)" if call_on else 0), N0)
            g(6, f"=IF(AND({st}>0,{st}>={K['roff']},"
                 f"MOD({st}-{K['roff']},{K['cyc']})=0),1,0)", N0, RED)
            # 상환할증금 = (g−c)/g × ((1+g/m)^(m·t) − 1).  g 가 0 이면 (g−c)·t
            g(7, f"=IF({L}$4=1,IF({K['pyld']}>0,"
                 f"100*(1+{xl_prem(K['pyld'], K['cpn'], K['pcmp'], yr)}),"
                 f"{K['prate']}),0)", N2)
            g(8, f"=IF({L}$5=1,IF({K['prem']}>0,"
                 f"100*(1+{xl_prem(K['prem'], K['cpn'], K['kcmp'], yr)}),"
                 f"100*(1+MAX(0,-{K['cpn']}*{yr}))),999999)", N2)
            g(9, f"=IF(AND({st}>0,{st}>={K['payoff']},"
                 f"MOD({st}-{K['payoff']},{K['ipay']})=0),"
                 f"100*{K['cpn']}*{K['ipaym']}/12,0)", N2)
            g(10, f"=IF({st}={K['n']},{K['red']},0)", N2)
            if i < n:
                # 이자율 산출내역을 함께 실었으면 그 표를 가리킨다. 고시 수익률을
                # 고치면 부트스트래핑 → 선도 → 트리까지 한 파일 안에서 따라온다.
                if _irref:
                    g(11, f"='{_irref[0]}'!$G${_irref[1]+i}", P2)
                    g(12, f"='{_irref[0]}'!$J${_irref[1]+i}", P2)
                else:
                    g(11, forward_rate(RF, i*dt_, (i+1)*dt_), P2, AMB)
                    g(12, forward_rate(CR, i*dt_, (i+1)*dt_), P2, AMB)
                g(16, f"=(EXP(({L}$11-{K['divy']})*{K['dt']})-{L}$15)"
                      f"/({L}$14-{L}$15)", N4)
                g(17, f"=1-{L}$16", N4)
            else:
                g(16, f"={K['q']}", N4); g(17, f"={K['q1']}", N4)
            g(13, f"={K['sig']}", P2); g(14, f"={K['u']}", N4); g(15, f"={K['dd']}", N4)
        title(W, 18, ttl, span=min(n+1, 14))
        put(W, 19, 2, "r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
        for i in range(n+1):
            put(W, 19, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                fill=(RFXC if i in REFIXSET else LIGHT), border=True)
        for r in range(n+1):
            put(W, R0+r, 2, r, bold=True, size=8, fmt=N0, align="center",
                fill=LIGHT, border=True)
        put(W, R0+n+2, 2, note, color=GREY, size=9)
        put(W, R0+n+3, 2, "참조: " + refs, color=GREEN, size=9)
        put(W, R0+n+4, 2, "r 은 하락 횟수. 11·12행 선도이자율만 값이다.", color=AMB, size=9)
        W.freeze_panes = "C20"
        return W

    def fill(W, fn, fmt=N2, txt=False):
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None; Ln = gl(4+i) if i < n else None
            for r in range(i+1):
                v = fn(i, r, L, Lp, Ln)
                if v is None: continue
                put(W, R0+r, 3+i, v, fmt=(None if txt else fmt), size=8,
                    align=("center" if txt else "right"))

    Q = lambda nm: f"'{nm}'"
    TOLX = repr(TOL)                # 엔진과 같은 동점 허용오차를 수식에도 쓴다
    S1, S2, S3, S4 = "01 주가", "02 전환가격", "03 전환비율", "04 전환가치"
    S5, S6, S7 = "05 지분가치", "06 부채가치", "07 보유가치"
    S8, S9, S10 = "08 금융상품가치", "09 의사결정", "10 주계약가치"
    S11, S12, S13, S14 = "11 GS 전환확률", "12 GS 할인율", "13 GS 보유가치", "14 GS 금융상품가치"
    S15 = "15 발행자 상환권 반영" if issuer_redeem(tm) else f"15 {KW} 트랜치"
    S16 = "16 부채요소"
    _rcps_call = issuer_redeem(tm) and tm.k_w > 0
    # 자본 배분에서 부채요소를 줄이는 콜 — 발행자 상환권은 결과 C26 (부채 격자),
    # CB 와 제3자 지정 매도청구권은 C22 (전체 격자)
    CAE = "C26" if issuer_redeem(tm) else "C22"
    S17, S18 = "17 구성비율", "18 혼합할인율"
    S19, S20 = "19 콜 페이오프", "20 매도청구권가치"
    S21, S22 = "21 방법2 지분보유", "22 방법2 부채보유"
    S23, S24 = "23 방법2 지분몫", "24 방법2 부채몫"

    # ── 01 주가 ──
    W = newsheet(S1, "① 주가트리",
                 "맨 위는 직전 열 맨 위 × u, 나머지는 직전 열 한 칸 위 × d.", "가정")
    fill(W, lambda i, r, L, Lp, Ln: (f"={K['S0']}" if i == 0 else
         (f"={Lp}{R0}*{L}$14" if r == 0 else f"={Lp}{R0+r-1}*{L}$15")), N2)

    # ── 도달확률 ──
    W2 = wb.create_sheet("도달확률"); W2.sheet_view.showGridLines = False
    W2.column_dimensions["B"].width = 12
    title(W2, 2, "도달확률  P(i,r) = P(i−1,r)×q + P(i−1,r−1)×(1−q)",
          span=min(n+1, 12))
    put(W2, 3, 2, "앞 열에서 한 칸씩 쌓아 온다. q 는 **직전 열**의 위험중립가중치이고 "
        "(① 16행), 이 격자는 구간마다 q 가 다르므로 이항계수 한 방으로 셀 수 없다 — "
        "COMBIN(i,r)×q^(i−r)×(1−q)^r 는 q 가 모든 구간에서 같을 때만 맞는다. "
        "r 은 하락 횟수이고, 위로 가면 r 이 그대로(q), 아래로 가면 r 이 하나 는다(1−q).",
        color=GREY, size=9)
    put(W2, 4, 2, "r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
    for i in range(n+1):
        L = gl(3+i)
        W2.column_dimensions[L].width = 9
        # 스텝도 직전 열 + 1 이다. 맨 앞의 0 하나가 전체를 정한다.
        put(W2, 4, 3+i, (0 if i == 0 else f"={gl(2+i)}$4+1"),
            bold=True, size=8, fmt=N0, align="center", fill=LIGHT, border=True)
    for r in range(n+1):
        put(W2, 5+r, 2, (0 if r == 0 else f"=$B{4+r}+1"),
            bold=True, size=8, fmt=N0, align="center", fill=LIGHT, border=True)
        for i in range(r, n+1):
            L, Lp = gl(3+i), gl(2+i)
            # q 는 직전 열의 것이다 — 열 i−1 의 q 가 구간 [i−1, i] 를 지배한다.
            qq = f"{Q(S1)}!{Lp}$16"
            if i == 0:
                v = 1
            elif r == 0:
                v = f"={Lp}5*{qq}"                       # 계속 상승뿐
            else:
                # 위 칸은 상승(r 유지), 그 위 칸은 하락(r 하나 증가)으로 온다.
                # r = i 면 {Lp}{5+r} 이 비어 있어 0 으로 읽힌다 — 그게 맞다.
                v = f"={Lp}{5+r}*{qq}+{Lp}{5+r-1}*(1-{qq})"
            put(W2, 5+r, 3+i, v, fmt='0.000000', size=8, align="right")
    W2.freeze_panes = "C5"

    # ── 02 전환가격 ──
    W = newsheet(S2, "② 전환가격트리",
                 "조정일 열은 주황색이다. 처리 방법은 가정에서 고른다.", f"{S1} · 도달확률")
    def kf(i, r, L, Lp, Ln):
        if i == 0: return f"={K['K0']}"
        up = f"{Lp}{R0+r}" if r <= i-1 else None
        dn = f"{Lp}{R0+r-1}" if r-1 >= 0 else None
        uP = f"도달확률!{Lp}{5+r}" if up else None
        dP = f"도달확률!{Lp}{5+r-1}" if dn else None
        if up is None: carry = dn
        elif dn is None: carry = up
        else:
            # q 는 직전 구간의 것이다. 엔진도 qi(i-1) 을 쓴다.
            m1 = (f"({up}*{uP}*{Lp}$16+{dn}*{dP}*{Lp}$17)"
                  f"/({uP}*{Lp}$16+{dP}*{Lp}$17)")
            m2 = f"({up}*{Lp}$16+{dn}*{Lp}$17)"
            carry = f"IF({K['mth']}=1,{m1},IF({K['mth']}=2,{m2},{dn}))"
        carry = carry or K['K0']
        # 조정일에도 **같은 이월값**을 쓴다. 비조정일만 가중평균하고 조정일에는
        # 선행 노드 하나만 집으면 같은 격자 안에서 처리가 갈린다 (엔진과 동일).
        base = (f"IF({K['up']}=1,{Q(S1)}!{L}{R0+r},"
                f"MIN({carry},{Q(S1)}!{L}{R0+r}))")
        clip = f"MIN(MAX({base},{K['flr']},{K['par']}),{K['cap']})"
        # 주기 조정이 없으면 이월만 한다 (IPO 조정은 그 위에 걸린다).
        nrm = f"IF({K['rfx']}=0,{carry},IF({L}$6=1,{clip},{carry}))"
        # 상장 스텝이고 그 주가가 최소공모가격을 넘으면 공모가 × 배수로 자른다.
        # 낮아질 때만 조정되고, 최저 조정가액·액면가 하한이 그대로 걸린다.
        hit = (f"AND({K['ipoon']}=1,{L}$2={K['ipos']},"
               f"{Q(S1)}!{L}{R0+r}>{K['ipomin']})")
        return (f"=IF({hit},MIN(MAX(MIN({nrm},{K['ipok']}),{K['flr']},{K['par']}),"
                f"{K['cap']}),{nrm})")
    fill(W, kf, N2)

    W = newsheet(S3, "③ 전환비율트리  100 ÷ 전환가격", "받게 될 주식 수다.", S2)
    fill(W, lambda i, r, L, Lp, Ln: f"=100/{Q(S2)}!{L}{R0+r}", N4)

    W = newsheet(S4, "④ 전환가치트리  주가 × 전환비율",
                 "전환청구기간 밖이면 0이다.", f"{S1} · {S3}")
    fill(W, lambda i, r, L, Lp, Ln:
         f"=IF({L}$3=1,{Q(S1)}!{L}{R0+r}*{Q(S3)}!{L}{R0+r},0)")

    # 앱에서 고른 것만 만든다. 쓰이지 않는 트리는 아예 넣지 않는다.
    #   GS 시트     — 신용위험 처리가 GS 일 때
    #   ⑮ 트랜치    — 매도청구권을 유무가치비교법으로 잴 때
    #   ⑰~⑳       — 옵션차익 · 혼합할인율
    #   ⑰⑲㉑~㉔   — 옵션차익 · 지분·부채 분리
    # 풋과 콜이 같은 노드에서 함께 열릴 때의 계약 우선순위. 엔진과 같은 갈래를
    # 타야 한다 — 콜이 사는 곳(⑮ 트랜치 · ⑯c)에서만 값이 갈린다.
    _kfirst = int(tm.pc_order) == 1
    # 현금납입 BW 는 지분(신주인수권)과 부채(사채)가 애초에 갈라져 있어 GS 가
    # TF 와 같은 값을 낸다. 조서에 쓸모없는 GS 블록을 넣지 않는다.
    _bwc = bw_cash(tm)
    _gs = tm.model == "GS" and not _bwc
    _hascall = tm.k_w > 0
    _km = tm.k_method
    _need15 = _hascall and _km == 0
    _need1 = _hascall and _km == 1
    _need2 = _hascall and _km == 2

    # ⑤~⑨ 는 TF 트리다. GS 를 골랐어도 옵션차익법(방법 1·2)의 기초자산이라
    # 그때는 남긴다. GS + 유무가치비교법이면 쓰이지 않으므로 만들지 않는다.
    _needTF = (not _gs) or _need1 or _need2
    _bdt = put_bdt_on(tm)      # 조기상환권을 BDT 로 재는가
    if _bwc:
        # ── 현금납입 BW ──
        # 신주인수권을 행사해도 사채가 남으므로 「사채를 내주고 주식을 받는」
        # 전환 갈래가 없다. ⑤ 는 신주인수권, ⑥ 은 사채가 되고, 둘을 더한 것이
        # ⑧ 이다. 분리형이면 두 결정이 서로를 건드리지 않아 ⑤ 가 ⑨ 를 참조조차
        # 하지 않는다. 비분리형이면 사채가 소멸할 때 미행사분이 함께 사라지므로
        # ⑨ 가 둘을 묶어 판단한다.
        _dt = K["dt"]
        WV = lambda L, r: f"MAX({Q(S4)}!{L}{R0+r}-100,0)"
        CE = lambda L, r, Ln: (f"({Q(S5)}!{Ln}{R0+r}*{L}$16+{Q(S5)}!{Ln}{R0+r+1}"
                               f"*{L}$17)*EXP(-{L}$11*{_dt})")
        CB = lambda L, r, Ln: (f"({Q(S6)}!{Ln}{R0+r}*{L}$16+{Q(S6)}!{Ln}{R0+r+1}"
                               f"*{L}$17)*EXP(-{L}$12*{_dt})+{L}$9")
        _det = int(tm.bw_detach) == 1
        DEC = lambda L, r: f"{Q(S9)}!{L}{R0+r}"

        W = newsheet(S5, "⑤ 신주인수권가치트리",
                     "행사가치는 «행사가치트리 − 100» 이다 — 권면액 100 만큼 현금을 내고 "
                     "그 값어치 주식을 받는다. 지금 행사와 계속 보유 중 큰 쪽을 고르고, "
                     "계속 보유는 다음 열을 무위험이자율로 할인한다."
                     + ("  분리형이라 사채가 소멸해도 남는다."
                        if _det else
                        "  비분리형이라 사채가 소멸하는 노드에서는 그 자리의 행사가치로 끝난다."),
                     f"{S4} · 다음 열 {S5}" + ("" if _det else f" · {S9}"), call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
            f"={WV(L, r)}" if i == n else
            f"=MAX({WV(L, r)},{CE(L, r, Ln)})" if _det else
            # 사채가 소멸하는 노드(상환P·상환C)에서는 통지기간 안에 행사할 기회가
            # 남아 있어 그 자리의 행사가치를 챙긴다. 조기상환과 매도청구가 같다.
            f'=IF(OR({DEC(L, r)}="상환P",{DEC(L, r)}="상환C"),{WV(L, r)},'
            f"MAX({WV(L, r)},{CE(L, r, Ln)}))"))

        W = newsheet(S6, "⑥ 사채가치트리",
                     "신주인수권과 무관하게 남는 사채다. 조기상환금액과 계속보유를 견주고, "
                     "매도청구가 걸리면 그 금액에서 잘린다.",
                     f"다음 열 {S6}" + (f" · {S9}" if not _det else ""), call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
            f"=MAX({L}$7,{L}$10)+{L}$9" if i == n else
            f"=MAX({L}$7,MIN({CB(L, r, Ln)},{L}$8))" if _det else
            f'=IF({DEC(L, r)}="상환P",{L}$7,IF({DEC(L, r)}="상환C",{L}$8,'
            f"{CB(L, r, Ln)}))"))

        W = newsheet(S7, "⑦ 보유가치트리",
                     "신주인수권은 무위험, 사채는 위험 선도이자율로 따로 할인해 더한다.",
                     f"다음 열 {S5} · {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
            f"={L}$10" if i == n else f"={CE(L, r, Ln)}+{CB(L, r, Ln)}"))

        W = newsheet(S8, "⑧ 금융상품가치트리 = 신주인수권 + 사채",
                     f"{KW0} 트랜치 — 매도청구권이 걸리지 않는다. 콜은 ⑮에서만 반영한다.",
                     f"{S5} · {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln:
             f"={Q(S5)}!{L}{R0+r}+{Q(S6)}!{L}{R0+r}")

        W = newsheet(S9, "⑨ 의사결정트리  상환P · 상환C · 보유",
                     "**사채**가 어떻게 끝나는지다. 신주인수권 행사 여부는 ⑤ 를 보라 — "
                     "그 칸이 «행사가치 − 100» 과 같으면 그 노드에서 행사한다."
                     + ("  분리형이라 사채가 소멸해도 신주인수권은 남으므로 두 판단이 "
                        "서로를 건드리지 않는다."
                        if _det else
                        "  비분리형이라 사채가 소멸하면 신주인수권도 소멸한다. 다만 "
                        "통지기간 안에 행사할 기회는 남으므로 조기상환금액에도 "
                        "매도청구금액에도 그 자리의 행사가치를 더해 견준다."),
                     f"다음 열 {S5} · {S6}", call_on=False)
        if _det:
            _D = lambda L, r, Ln: (
                f'IF({L}$7>=MIN({CB(L, r, Ln)},{L}$8)-{TOLX},"상환P",'
                f'IF({CB(L, r, Ln)}<={L}$8+{TOLX},"보유","상환C"))')
        else:
            # 매도청구금액에도 행사기회를 더한다 — 발행자가 사채를 매수해 가도
            # 투자자는 그 직전에 신주인수권을 행사해 그 값을 챙기므로, 콜이
            # 투자자 가치를 눌러 내리는지는 «매도청구금액 + 행사가치» 와 견줘야
            # 안다. 조기상환 쪽 «+ 행사가치» 와 같은 읽기다.
            _D = lambda L, r, Ln: (
                f'IF({L}$7+{WV(L, r)}>=MIN({CB(L, r, Ln)}+{CE(L, r, Ln)},'
                f'{L}$8+{WV(L, r)})-{TOLX},"상환P",'
                f'IF({CB(L, r, Ln)}+{CE(L, r, Ln)}<={L}$8+{WV(L, r)}+{TOLX},'
                f'"보유","상환C"))')
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({L}$7>{L}$10+{TOLX},"상환P","만기상환")' if i == n else
            f"={_D(L, r, Ln)}"), txt=True)

    elif _needTF:
        W = newsheet(S5, "⑤ 지분가치트리",
                     "전환이면 전환가치, 상환이면 0, 보유면 다음 열을 무위험이자율로 할인.",
                     f"{S4} · {S9} · 다음 열 {S5}", call_on=False)
        # 만기 노드에서 자동전환(가정 auto=1)이면 주식을 받는다. 상환청구가 만기에
        # 열려 있으면 그 금액(+배당)과 견줘 큰 쪽이다. CASH 는 그 상환 갈래다.
        AU = K["auto"]
        CASH = lambda L: f"IF({L}$7>0,{L}$7+{L}$9,0)"
        _CV = lambda L, r: (f'OR({Q(S9)}!{L}{R0+r}="전환",{Q(S9)}!{L}{R0+r}="자동전환",'
                            f'{Q(S9)}!{L}{R0+r}="상장전환")')
        # 상장 스텝에서 주가가 최소공모가격을 넘으면 그 자리에서 주식이 된다.
        _IPO = lambda L, r: (f"AND({K['ipoon']}=1,{K['ipocv']}=1,{L}$2={K['ipos']},"
                             f"{L}$2>0,{Q(S1)}!{L}{R0+r}>{K['ipomin']})")
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({_CV(L, r)},{Q(S4)}!{L}{R0+r},0)' if i == n else
            f'=IF({_CV(L, r)},{Q(S4)}!{L}{R0+r},'
            f'IF(OR({Q(S9)}!{L}{R0+r}="상환P",{Q(S9)}!{L}{R0+r}="상환C"),0,'
            f'({Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17)*EXP(-{L}$11*{K["dt"]})))'))

        W = newsheet(S6, "⑥ 부채가치트리",
                     "전환이면 0, 상환이면 그 금액, 보유면 다음 열을 위험 선도이자율로 할인.",
                     f"{S9} · 다음 열 {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({_CV(L, r)},0,IF({AU}=1,{L}$7+{L}$9,MAX({L}$7,{L}$10)+{L}$9))' if i == n else
            f'=IF({Q(S9)}!{L}{R0+r}="상환P",{L}$7,IF({Q(S9)}!{L}{R0+r}="상환C",{L}$8,'
            f'IF({_CV(L, r)},0,'
            f'({Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17)*EXP(-{L}$12*{K["dt"]})+{L}$9)))'))

        W = newsheet(S7, "⑦ 보유가치트리",
                     "지분은 무위험, 부채는 위험 선도이자율로 따로 할인해 더한다. 이것이 TF다.",
                     f"다음 열 {S5} · {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({AU}=1,{Q(S4)}!{L}{R0+r},{L}$10+{L}$9)" if i == n else
             f"=({Q(S5)}!{Ln}{R0+r}*{L}$16+{Q(S5)}!{Ln}{R0+r+1}*{L}$17)"
             f"*EXP(-{L}$11*{K['dt']})"
             f"+({Q(S6)}!{Ln}{R0+r}*{L}$16+{Q(S6)}!{Ln}{R0+r+1}*{L}$17)"
             f"*EXP(-{L}$12*{K['dt']})+{L}$9"))

        W = newsheet(S8, "⑧ 금융상품가치트리",
                     f"{KW0} 트랜치 — 매도청구권이 걸리지 않는다. 콜은 ⑮에서만 반영한다.",
                     f"{S4} · {S7}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({AU}=1,MAX({Q(S4)}!{L}{R0+r},{CASH(L)}),"
             f"MAX({Q(S7)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7+{L}$9))" if i == n else
             f"=IF({_IPO(L, r)},{Q(S4)}!{L}{R0+r},"
             f"MAX({Q(S7)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7))"))

        W = newsheet(S9, "⑨ 의사결정트리",
                     f"전환가치·조기상환금액·보유가치를 직접 견준다. {KW0} 트랜치라 "
                     "상환C 는 없다. 리픽싱 조정일에는 전환가치가 정확히 100 이 되어 상환금액과 동점이 되므로 "
                     "전환은 허용오차만큼 앞설 때만 이긴다. 동점이면 현금이다.",
                     f"{S4} · {S7}", call_on=False)
        _DEC = lambda L, r: (
             f'IF({Q(S4)}!{L}{R0+r}>=MAX({L}$7,{Q(S7)}!{L}{R0+r})+{TOLX},"전환",'
             f'IF({L}$7>={Q(S7)}!{L}{R0+r}-{TOLX},"상환P","보유"))')
        fill(W, lambda i, r, L, Lp, Ln: (
             f'=IF({AU}=1,IF({Q(S4)}!{L}{R0+r}>={CASH(L)}+{TOLX},"자동전환","상환P"),'
             f'{_DEC(L, r)})' if i == n else
             f'=IF({_IPO(L, r)},"상장전환",{_DEC(L, r)})'), txt=True)

    W = newsheet(S10, "⑩ 주계약가치트리  옵션이 전혀 없는 순수 사채",
                 "주가와 무관하므로 같은 열의 값이 모두 같다.", "가정")
    fill(W, lambda i, r, L, Lp, Ln: (f"={L}$10+{L}$9" if i == n else
         f"=({Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17)*EXP(-{L}$12*{K['dt']})"
         f"+{L}$9+{L}$10"))

    if _gs:
        W = newsheet(S11, "⑪ [GS] 전환확률트리",
                     "전환 1, 현금 0, 보유면 다음 두 칸의 평균.", f"{S14} · 다음 열 {S11}")
        # 현금(상환P·상환C)이 동점이면 0 이다. 전환은 허용오차만큼 앞설 때만 1 이다.
        _cash = lambda L, r: (f'OR(ABS({Q(S14)}!{L}{R0+r}-{L}$7)<{TOLX},'
                              f'ABS({Q(S14)}!{L}{R0+r}-{L}$8)<{TOLX})')
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({_cash(L, r)},0,'
            f'IF(ABS({Q(S14)}!{L}{R0+r}-{Q(S4)}!{L}{R0+r})<{TOLX},1,'
            + ('0))' if i == n else
               f'{Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17))')), N4)

        W = newsheet(S12, "⑫ [GS] 위험조정할인율트리",
                     "이 칸을 직전 시점으로 할인할 때 쓰는 이자율이다. "
                     "전환확률로 무위험과 위험을 섞되 직전 구간의 선도이자율을 쓴다.", S11)
        fill(W, lambda i, r, L, Lp, Ln:
             (f"={Q(S11)}!{L}{R0+r}*{Lp}$11+(1-{Q(S11)}!{L}{R0+r})*{Lp}$12"
              if i > 0 else "=0"), P2)

        W = newsheet(S13, "⑬ [GS] 보유가치트리",
                     "다음 두 칸을 각 칸의 할인율로 따로 할인한다.", f"다음 열 {S12} · {S14}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({K['auto']}=1,{Q(S4)}!{L}{R0+r},{L}$10+{L}$9)" if i == n else
             f"={Q(S14)}!{Ln}{R0+r}*{L}$16*EXP(-{Q(S12)}!{Ln}{R0+r}*{K['dt']})"
             f"+{Q(S14)}!{Ln}{R0+r+1}*{L}$17*EXP(-{Q(S12)}!{Ln}{R0+r+1}*{K['dt']})+{L}$9"))

        W = newsheet(S14, "⑭ [GS] 금융상품가치트리",
                     "⑧과 같은 칸을 비교하면 모형 차이가 보인다.", f"{S4} · {S13}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({K['auto']}=1,MAX({Q(S4)}!{L}{R0+r},IF({L}$7>0,{L}$7+{L}$9,0)),"
             f"MAX({Q(S13)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7+{L}$9))" if i == n else
             f"=IF(AND({K['ipoon']}=1,{K['ipocv']}=1,{L}$2={K['ipos']},{L}$2>0,"
             f"{Q(S1)}!{L}{R0+r}>{K['ipomin']}),{Q(S4)}!{L}{R0+r},"
             f"MAX({Q(S13)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7))"))

    if _need15:
        # ── 15 트랜치 ──
        # 고른 모형의 블록만 담는다. GS 블록은 TF 다섯 블록을 참조하지 않으므로
        # 어느 쪽을 골라도 나머지 절반은 만들 필요가 없다.
        W = newsheet(S15, f"⑮ {KW} 트랜치  매도청구권이 걸리는 부분",
                     f"의무보유 때문에 전환 시작이 늦다. {KW0}와의 차이가 "
                     "매도청구권의 가치다. "
                     + ("전환가치 뒤에 GS 네 블록을 둔다. 고른 모형이 GS 이기 때문이다."
                        if _gs else
                        "전환가치 뒤에 TF 다섯 블록을 둔다. 고른 모형이 TF 이기 때문이다."),
                     "가정", conv_cell=K["cv30"])
        HH = n+3
        _bs = {"row": R0, "i": 0}
        def blk(t):
            _bs["row"] += HH
            row = _bs["row"]
            sec(W, row-1, f"{'가나다라마바'[_bs['i']]}  {t}", span=min(n+1, 14))
            _bs["i"] += 1
            put(W, row, 2, "r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
            for i in range(n+1):
                put(W, row, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                    fill=LIGHT, border=True)
            for r in range(n+1):
                put(W, row+1+r, 2, r, bold=True, size=8, fmt=N0, align="center",
                    fill=LIGHT, border=True)
            return row+1

        # 전환가치는 두 모형이 함께 쓴다.
        c1 = blk("전환가치")
        for i in range(n+1):
            L = gl(3+i)
            for r in range(i+1):
                put(W, c1+1+r, 3+i,
                    f"=IF({L}$3=1,{Q(S1)}!{L}{R0+r}*{Q(S3)}!{L}{R0+r},0)",
                    fmt=N2, size=8, align="right")

        if _bwc:
            # 현금납입 BW 의 트랜치. ⑤~⑨ 와 같은 구조인데 매도청구권이 살아 있고
            # (L$5 · L$8), 의무보유 때문에 행사 시작이 늦다 (L$3).
            c2 = blk("신주인수권가치")
            c3 = blk("사채가치")
            c4 = blk("보유가치")
            c5 = blk("금융상품가치")
            c6 = blk("의사결정")
            last = c5
            _det = int(tm.bw_detach) == 1
            for i in range(n+1):
                L = gl(3+i); Ln = gl(4+i) if i < n else None
                for r in range(i+1):
                    p = lambda base, v, fm=N2, tx=False: put(W, base+1+r, 3+i, v,
                            fmt=(None if tx else fm), size=8,
                            align=("center" if tx else "right"))
                    wv = f"MAX({L}{c1+1+r}-100,0)"
                    if i == n:
                        p(c2, f"={wv}")
                        p(c3, f"=MAX({L}$7,{L}$10)+{L}$9")
                        p(c4, f"={L}$10")
                        p(c5, f"={L}{c2+1+r}+{L}{c3+1+r}")
                        p(c6, f'=IF({L}$7>{L}$10+{TOLX},"상환P","만기상환")', tx=True)
                        continue
                    ce = (f"({Ln}{c2+1+r}*{L}$16+{Ln}{c2+2+r}*{L}$17)"
                          f"*EXP(-{L}$11*{K['dt']})")
                    cb = (f"({Ln}{c3+1+r}*{L}$16+{Ln}{c3+2+r}*{L}$17)"
                          f"*EXP(-{L}$12*{K['dt']})+{L}$9")
                    p(c4, f"={ce}+{cb}")
                    p(c5, f"={L}{c2+1+r}+{L}{c3+1+r}")
                    # 사채 쪽 결정은 전환사채와 같은 규칙이라 xl_decide 가 만든다.
                    # 전환은 이 결정에 들어가지 않으므로 cv=None 이다.
                    #   분리형   — 사채만 견준다 (신주인수권은 따로 산다)
                    #   비분리형 — 사채가 소멸하면 신주인수권도 소멸하되 상환 직전
                    #              행사할 기회는 남으므로 세 후보에 행사가치를 얹는다
                    _bwn = ("전환", "상환P", "상환C", "보유")
                    if _det:
                        _dec = xl_decide(None, f"{L}$7", f"{L}$8", cb, _kfirst,
                                         TOLX, _bwn)
                        p(c6, f"={_dec}", tx=True)
                        p(c2, f"=MAX({wv},{ce})")
                        p(c3, "=" + xl_pick(f"{L}{c6+1+r}", f"{L}$7", f"{L}$8", cb,
                                            names=_bwn))
                    else:
                        _dec = xl_decide(None, f"{L}$7+{wv}", f"{L}$8+{wv}",
                                         f"{cb}+{ce}", _kfirst, TOLX, _bwn)
                        p(c6, f"={_dec}", tx=True)
                        p(c2, f'=IF(OR({L}{c6+1+r}="상환P",{L}{c6+1+r}="상환C"),{wv},'
                              f"MAX({wv},{ce}))")
                        p(c3, "=" + xl_pick(f"{L}{c6+1+r}", f"{L}$7", f"{L}$8", cb,
                                            names=_bwn))
        elif not _gs:
            c2 = blk("지분가치")
            c3 = blk("부채가치")
            c4 = blk("보유가치")
            c5 = blk("금융상품가치")
            c6 = blk("의사결정")
            last = c5
            for i in range(n+1):
                L = gl(3+i); Ln = gl(4+i) if i < n else None
                for r in range(i+1):
                    p = lambda base, v, fm=N2, tx=False: put(W, base+1+r, 3+i, v,
                            fmt=(None if tx else fm), size=8,
                            align=("center" if tx else "right"))
                    if i == n:
                        _AU, _CS = K["auto"], f"IF({L}$7>0,{L}$7+{L}$9,0)"
                        p(c5, f"=IF({_AU}=1,MAX({L}{c1+1+r},{_CS}),"
                              f"MAX({L}{c1+1+r},MAX({L}$7,{L}$10)+{L}$9))")
                        p(c6, f'=IF({_AU}=1,IF({L}{c1+1+r}>={_CS}+{TOLX},"자동전환","상환P"),'
                              f'IF({L}{c1+1+r}>=MAX({L}$7,{L}$10)+{L}$9+{TOLX},"전환",'
                              f'IF({L}$7>={L}$10-{TOLX},"상환P","만기상환")))', tx=True)
                        p(c2, f'=IF(OR({L}{c6+1+r}="전환",{L}{c6+1+r}="자동전환"),{L}{c1+1+r},0)')
                        p(c3, f'=IF(OR({L}{c6+1+r}="전환",{L}{c6+1+r}="자동전환"),0,'
                              f'IF({_AU}=1,{L}$7+{L}$9,MAX({L}$7,{L}$10)+{L}$9))')
                        p(c4, f"=IF({_AU}=1,{L}{c1+1+r},{L}$10+{L}$9)")
                        continue
                    e = f"({Ln}{c2+1+r}*{L}$16+{Ln}{c2+2+r}*{L}$17)*EXP(-{L}$11*{K['dt']})"
                    b = f"({Ln}{c3+1+r}*{L}$16+{Ln}{c3+2+r}*{L}$17)*EXP(-{L}$12*{K['dt']})"
                    _ip = (f"AND({K['ipoon']}=1,{K['ipocv']}=1,{L}$2={K['ipos']},"
                           f"{L}$2>0,{Q(S1)}!{L}{R0+r}>{K['ipomin']})")
                    _cvx = f'OR({L}{c6+1+r}="전환",{L}{c6+1+r}="상장전환")'
                    p(c4, f"={e}+{b}+{L}$9")
                    # 계약 우선순위 (pc_order) 에 따라 두 갈래다. 엔진과 같다.
                    #   투자자 풋 우선 : MAX(전환, 풋, MIN(보유, 콜))
                    #   발행자 콜 우선 : MAX(전환, MIN(MAX(보유, 풋), 콜))
                    # 결정은 xl_decide 가 만든다 — 엔진의 node_decide 와 같은 식이다.
                    _cvC, _pvC, _kvC = f"{L}{c1+1+r}", f"{L}$7", f"{L}$8"
                    _hdC = f"{L}{c4+1+r}"
                    _dec = xl_decide(_cvC, _pvC, _kvC, _hdC, _kfirst, TOLX)
                    if _kfirst:
                        _iv = f"MAX({_hdC},{_pvC})"      # 콜이 없을 때 투자자 선택값
                        _val = f"MAX({_cvC},MIN({_iv},{_kvC}))"
                    else:
                        _val = f"MAX(MIN({_hdC},{_kvC}),{_cvC},{_pvC})"
                    p(c5, f"=IF({_ip},{_cvC},"
                          f"IF({L}$5=1,{_val},MAX({_hdC},{_cvC},{_pvC})))")
                    p(c6, f'=IF({_ip},"상장전환",{_dec})', tx=True)
                    p(c2, f'=IF({_cvx},{L}{c1+1+r},'
                          f'IF(OR({L}{c6+1+r}="상환P",{L}{c6+1+r}="상환C"),0,{e}))')
                    p(c3, f'=IF({L}{c6+1+r}="상환P",{L}$7,IF({L}{c6+1+r}="상환C",{L}$8,'
                          f'IF({_cvx},0,{b}+{L}$9)))')
        else:
            # ⑪~⑭ 와 같은 순서지만 트랜치 자신의 헤더와 전환가치를 쓴다.
            c7 = blk("[GS] 전환확률")
            c8 = blk("[GS] 위험조정할인율")
            c9 = blk("[GS] 보유가치")
            c10 = blk("[GS] 금융상품가치")
            last = c10
            for i in range(n+1):
                L = gl(3+i); Lp = gl(2+i) if i > 0 else None; Ln = gl(4+i) if i < n else None
                for r in range(i+1):
                    p = lambda base, v, fm=N2: put(W, base+1+r, 3+i, v, fmt=fm,
                                                   size=8, align="right")
                    if i == n:
                        # 만기에는 TF 와 GS 가 같다.
                        _AU, _CS = K["auto"], f"IF({L}$7>0,{L}$7+{L}$9,0)"
                        p(c9, f"=IF({_AU}=1,{L}{c1+1+r},{L}$10+{L}$9)")
                        p(c10, f"=IF({_AU}=1,MAX({L}{c1+1+r},{_CS}),"
                               f"MAX({L}{c1+1+r},MAX({L}$7,{L}$10)+{L}$9))")
                        p(c7, f"=IF({L}{c10+1+r}={L}{c1+1+r},1,0)", N4)
                    else:
                        p(c9, f"={Ln}{c10+1+r}*{L}$16*EXP(-{Ln}{c8+1+r}*{K['dt']})"
                              f"+{Ln}{c10+2+r}*{L}$17*EXP(-{Ln}{c8+2+r}*{K['dt']})+{L}$9")
                        # ⑮ TF 와 같은 식을 GS 의 보유가치로 부른다. 한 격자에서
                        # 두 모형이 다른 계약을 읽으면 안 된다.
                        _gcall = (f"MAX({L}{c1+1+r},"
                                  f"MIN(MAX({L}{c9+1+r},{L}$7),{L}$8))"
                                  if _kfirst else
                                  f"MAX(MIN({L}{c9+1+r},{L}$8),{L}{c1+1+r},{L}$7)")
                        p(c10, f"=IF(AND({K['ipoon']}=1,{K['ipocv']}=1,{L}$2={K['ipos']},"
                               f"{L}$2>0,{Q(S1)}!{L}{R0+r}>{K['ipomin']}),{L}{c1+1+r},"
                               f"IF({L}$5=1,{_gcall},"
                               f"MAX({L}{c9+1+r},{L}{c1+1+r},{L}$7)))")
                        p(c7, f"=IF({L}{c10+1+r}={L}{c1+1+r},1,"
                              f"IF(OR({L}{c10+1+r}={L}$7,{L}{c10+1+r}={L}$8),0,"
                              f"{Ln}{c7+1+r}*{L}$16+{Ln}{c7+2+r}*{L}$17))", N4)
                    p(c8, (f"={L}{c7+1+r}*{Lp}$11+(1-{L}{c7+1+r})*{Lp}$12"
                           if i > 0 else "=0"), P2)

        # 마지막으로 놓인 블록 아래에 둔다. TF 는 의사결정(바)이 값 블록(마) 뒤에
        # 오므로 last 를 그대로 쓰면 그 위에 겹쳐 쓰게 된다.
        RT = _bs["row"] + n + 3
        sec(W, RT, "결과", span=6)
        put(W, RT+1, 2, f"{KW} 트랜치 금융상품가치 · {tm.model} (t=0)", bold=True)
        put(W, RT+1, 3, f"=C{last+1}", bold=True, fmt=N2, align="right")

    # ── 16 부채요소 ──
    D = wb.create_sheet(S16); D.sheet_view.showGridLines = False
    D.column_dimensions["B"].width = 20
    for i in range(n+1): D.column_dimensions[gl(3+i)].width = 9
    title(D, 2, "⑯ 부채요소  전환권이 없는 사채에 조기상환권만 붙인 값", span=min(n+1, 14))
    put(D, 3, 2, "전환이 없으면 주가와 무관하므로 한 줄로 끝난다. "
        "만기부터 왼쪽으로 오며 MAX(조기상환금액, 계속보유)를 고른다.", color=GREY, size=9)
    for r, nm in enumerate(["Date", "time-step", "Flag(조기상환)", "조기상환금액", "쿠폰",
                            "만기상환", "위험 선도이자율", "부채요소"], start=4):
        put(D, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
    for i in range(n+1):
        L = gl(3+i); Lp = gl(2+i) if i > 0 else None; Ln = gl(4+i) if i < n else None
        g = lambda r, v, fm=None, col="000000": put(D, r, 3+i, v, fmt=fm,
                                                    align="center", size=8, color=col)
        st = f"{L}$5"
        yr = f"({st}*{K['dt']}+{K['elm']}/12)"
        g(4, f"={K['d_base']}+{st}*{K['dt']}*365", DATE, GREY)
        g(5, (0 if i == 0 else f"={Lp}$5+1"), N0)
        g(6, f"=IF(AND({st}>={K['pst']},{st}<={K['pen']},"
             f"MOD({st}-{K['pst']},{K['frq']})=0),1,0)", N0)
        g(7, f"=IF({L}$6=1,IF({K['pyld']}>0,"
             f"100*(1+{xl_prem(K['pyld'], K['cpn'], K['pcmp'], yr)}),"
             f"{K['prate']}),0)", N2)
        g(8, f"=IF(AND({st}>0,{st}>={K['payoff']},"
             f"MOD({st}-{K['payoff']},{K['ipay']})=0),"
             f"100*{K['cpn']}*{K['ipaym']}/12,0)", N2)
        g(9, f"=IF({st}={K['n']},{K['red']},0)", N2)
        if i < n: g(10, forward_rate(CR, i*dt_, (i+1)*dt_), P2, AMB)
        g(11, (f"=MAX({L}$7,{L}$9)+{L}$8" if i == n else
               f"=MAX({L}$7,{Ln}11*EXP(-{L}$10*{K['dt']})+{L}$8)"), N2)
    put(D, 13, 2, "부채요소 (t=0)", bold=True)
    put(D, 13, 3, "=C11", bold=True, fmt=N2, align="right")

    # ── 16c 부채요소 (발행자 상환권 포함) — RCPS 만 ──
    # 1032 문단 31: 자본요소가 아닌 파생(콜)은 부채요소에 포함한다. 그래서 전환권
    # 없는 부채 격자에 발행자 상환권을 걸어 재고, 그 차이를 자본 배분에 쓴다.
    S16C = "16c 부채요소 (발행자 상환권)"
    if _rcps_call:
        Dc = wb.create_sheet(S16C); Dc.sheet_view.showGridLines = False
        Dc.column_dimensions["B"].width = 22
        for i in range(n+1): Dc.column_dimensions[gl(3+i)].width = 9
        title(Dc, 2, "⑯c 부채요소 + 발행자 상환권  전환권 없는 부채에 상환청구권과 발행자 상환권을 함께 붙인 값",
              span=min(n+1, 14))
        put(Dc, 3, 2, "MAX(상환청구금액, MIN(계속보유, 발행자 상환가액)). 전환권이 없으니 발행자가 "
            "되사올 이유가 거의 없어 ⑯ 과 비슷하다 — 그 차이가 부채 격자에서 잰 발행자 상환권이다.",
            color=GREY, size=9)
        for r, nm in enumerate(["Date", "time-step", "Flag(조기상환)", "조기상환금액", "쿠폰",
                                "만기상환", "위험 선도이자율", "Flag(매도청구)", "매도청구금액",
                                "부채요소 (콜 포함)"], start=4):
            put(Dc, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None; Ln = gl(4+i) if i < n else None
            g = lambda r, v, fm=None, col="000000": put(Dc, r, 3+i, v, fmt=fm,
                                                        align="center", size=8, color=col)
            st = f"{L}$5"
            yr = f"({st}*{K['dt']}+{K['elm']}/12)"
            g(4, f"={K['d_base']}+{st}*{K['dt']}*365", DATE, GREY)
            g(5, (0 if i == 0 else f"={Lp}$5+1"), N0)
            g(6, f"=IF(AND({st}>={K['pst']},{st}<={K['pen']},"
                 f"MOD({st}-{K['pst']},{K['frq']})=0),1,0)", N0)
            g(7, f"=IF({L}$6=1,IF({K['pyld']}>0,"
                 f"100*(1+{xl_prem(K['pyld'], K['cpn'], K['pcmp'], yr)}),"
                 f"{K['prate']}),0)", N2)
            g(8, f"=IF(AND({st}>0,{st}>={K['payoff']},"
                 f"MOD({st}-{K['payoff']},{K['ipay']})=0),"
                 f"100*{K['cpn']}*{K['ipaym']}/12,0)", N2)
            g(9, f"=IF({st}={K['n']},{K['red']},0)", N2)
            if i < n: g(10, forward_rate(CR, i*dt_, (i+1)*dt_), P2, AMB)
            g(11, f"=IF(AND({st}>={K['kst']},{st}<={K['ken']},"
                  f"MOD({st}-{K['kst']},{K['kfrq']})=0),1,0)", N0)
            g(12, f"=IF({L}$11=1,IF({K['prem']}>0,"
                  f"100*(1+{xl_prem(K['prem'], K['cpn'], K['kcmp'], yr)}),"
                  f"100*(1+MAX(0,-{K['cpn']}*{yr}))),999999)", N2)
            _cont = f"{Ln}13*EXP(-{L}$10*{K['dt']})+{L}$8"
            # 전환이 없는 갈래라 xl_decide 의 cv=None 과 같은 모양이다.
            g(13, (f"=MAX({L}$7,{L}$9)+{L}$8" if i == n else
                   (f"=MIN(MAX({_cont},{L}$7),{L}$12)" if _kfirst else
                    f"=MAX({L}$7,MIN({_cont},{L}$12))")), N2)
        put(Dc, 15, 2, "부채요소 + 발행자 상환권 (t=0)", bold=True)
        put(Dc, 15, 3, "=C13", bold=True, fmt=N2, align="right")

    if _bdt:
        # ── BDT 단기이자율 · BDT 부채요소 ──
        # 기준금리 a_i 는 곡선에 맞추려고 역산한 값이라 수식으로 펼 수 없다.
        # 선도이자율과 같은 자리다. 격자와 역진은 전부 수식이다.
        BP, BV = bdt_grid(tm, True)
        SB1, SB2 = "BDT 단기이자율", "BDT 부채요소"
        RB = 14                                  # 표 첫 자료행 (j = 0)

        def bhead(W, ttl, note):
            W.sheet_view.showGridLines = False
            W.column_dimensions["B"].width = 20
            for i in range(n+1): W.column_dimensions[gl(3+i)].width = 9
            title(W, 2, ttl, span=min(n+1, 14))
            put(W, 3, 2, note, color=GREY, size=9)
            for r, nm in enumerate(["Date", "time-step", "Flag(조기상환)", "조기상환금액",
                                    "쿠폰", "만기상환", "기준금리 a", "확정 스프레드"],
                                   start=4):
                put(W, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
            for i in range(n+1):
                L = gl(3+i); Lp = gl(2+i) if i > 0 else None
                g = lambda r, v, fm=None, col="000000": put(W, r, 3+i, v, fmt=fm,
                                                            align="center", size=8, color=col)
                st = f"{L}$5"
                yr = f"({st}*{K['dt']}+{K['elm']}/12)"
                g(4, f"={K['d_base']}+{st}*{K['dt']}*365", DATE, GREY)
                g(5, (0 if i == 0 else f"={Lp}$5+1"), N0)
                g(6, f"=IF(AND({st}>={K['pst']},{st}<={K['pen']},"
                     f"MOD({st}-{K['pst']},{K['frq']})=0),1,0)", N0)
                g(7, f"=IF({L}$6=1,IF({K['pyld']}>0,"
                     f"100*(1+{xl_prem(K['pyld'], K['cpn'], K['pcmp'], yr)}),"
                     f"{K['prate']}),0)", N2)
                g(8, f"=IF(AND({st}>0,{st}>={K['payoff']},"
                     f"MOD({st}-{K['payoff']},{K['ipay']})=0),"
                     f"100*{K['cpn']}*{K['ipaym']}/12,0)", N2)
                g(9, f"=IF({st}={K['n']},{K['red']},0)", N2)
                if i < n:
                    g(10, BP["a"][i], P2, AMB)
                    g(11, BP["add"][i], P2, AMB)
            put(W, RB-1, 2, "j ＼ 스텝", bold=True, size=8, fill=LIGHT,
                border=True, align="center")
            for i in range(n+1):
                put(W, RB-1, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                    fill=LIGHT, border=True)
            for j in range(n+1):
                put(W, RB+j, 2, j, bold=True, size=8, fmt=N0, align="center",
                    fill=LIGHT, border=True)
            W.freeze_panes = f"C{RB}"

        W = wb.create_sheet(SB1)
        bhead(W, "BDT 단기이자율격자  r(i,j) = a · exp(2σ·j·√Δt)",
              "로그정규라 이자율이 음수가 되지 않는다. j 는 상승 횟수이고 클수록 "
              "금리가 높다 — 주가 트리와 달리 위로 갈수록 낮다. 기준금리 a 는 곡선을 "
              "정확히 되돌리도록 역산한 값이라 주황색이다. σ 를 바꾸시려면 앱에서 "
              "조서를 다시 만드셔야 한다.")
        for i in range(n):
            L = gl(3+i)
            for j in range(i+1):
                put(W, RB+j, 3+i,
                    f"={L}$10*EXP(2*{K['bsig']}*$B{RB+j}*SQRT({K['dt']}))",
                    fmt=P2, size=8, align="right")

        # ── 캘리브레이션 검산 ──
        # 기준금리 a 만 값이라 조서만 보면 근거 없는 상수처럼 보인다. 그래서
        # 도달가격 Q 를 수식으로 쌓아 보인다. Σ_j Q(k,j) 가 시장 할인계수와
        # 같아야 하고, a 를 바로 그 조건에 맞춰 역산했다 — 무차익거래 조건이다.
        QR = RB+n+3
        sec(W, QR-2, f"캘리브레이션 검산 — 격자가 {BP['base_nm']}을 되돌리는가",
            span=min(n+1, 14))
        put(W, QR-1, 2, "j ＼ 스텝", bold=True, size=8, fill=LIGHT,
            border=True, align="center")
        for i in range(n+1):
            put(W, QR-1, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                fill=LIGHT, border=True)
        for j in range(n+1):
            put(W, QR+j, 2, j, bold=True, size=8, fmt=N0, align="center",
                fill=LIGHT, border=True)
        for i in range(n+1):
            L, Lp = gl(3+i), (gl(2+i) if i > 0 else None)
            for j in range(i+1):
                if i == 0:
                    v = 1
                else:
                    # 아래에서 올라온 몫 + 위에서 내려온 몫. 삼각형 밖은 비어
                    # 있으므로 엑셀에서 0 으로 읽힌다.
                    up = (f"0.5*{Lp}{QR+j-1}*EXP(-{Lp}{RB+j-1}*{K['dt']})"
                          if j > 0 else "")
                    dn = (f"0.5*{Lp}{QR+j}*EXP(-{Lp}{RB+j}*{K['dt']})"
                          if j <= i-1 else "")
                    v = "=" + "+".join(x for x in (up, dn) if x)
                put(W, QR+j, 3+i, v, fmt=N6, size=8, align="right")
        for k2, nm in enumerate(("모형 무이표채  Σ Q", "시장 할인계수", "차이")):
            r2 = QR+n+1+k2
            put(W, r2, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
            for i in range(n+1):
                L = gl(3+i)
                v = (f"=SUM({L}{QR}:{L}{QR+n})" if k2 == 0 else
                     (BP["mkt"][i] if k2 == 1 else f"={L}{r2-2}-{L}{r2-1}"))
                put(W, r2, 3+i, v, fmt=N6, size=8, align="right",
                    bold=(k2 == 2), color=(AMB if k2 == 1 else "000000"))
        put(W, QR+n+5, 2,
            "도달가격 Q(i,j) 는 그 칸에 이르는 경로의 확률을 그 경로의 할인율로 "
            "할인해 더한 값이다. 스텝별로 모두 더하면 그 만기의 무이표채 가격이 "
            "되고, 그것이 시장 할인계수(주황)와 같아야 한다 — 무차익거래 조건이다. "
            "기준금리 a 를 이 조건에 맞춰 이분법으로 역산했으므로 차이가 0 이다. "
            "σ 를 바꾸면 a 도 함께 바뀌어야 하므로 앱에서 조서를 다시 만드셔야 "
            "한다 — 이 시트에서 σ 만 바꾸면 차이가 0 에서 벗어난다.", color=GREY, size=9)

        W = wb.create_sheet(SB2)
        bhead(W, "BDT 부채요소  전환 없는 사채 + 조기상환권",
              "MAX(조기상환금액, 계속보유) 를 고른다. 계속보유는 다음 두 칸을 "
              "0.5 씩 섞어 그 칸의 단기이자율로 할인한 값이다. 확정 격자와의 차이가 "
              "곧 금리에서 나오는 옵션의 시간가치다.")
        for i in range(n, -1, -1):
            L = gl(3+i); Ln = gl(4+i) if i < n else None
            for j in range(i+1):
                if i == n:
                    v = f"=MAX({L}$7,{L}$9)+{L}$8"
                else:
                    d = f"EXP(-('{SB1}'!{L}{RB+j}+{L}$11)*{K['dt']})"
                    v = (f"=MAX({L}$7,(0.5*{Ln}{RB+j+1}+0.5*{Ln}{RB+j})*{d}+{L}$8)")
                put(W, RB+j, 3+i, v, fmt=N2, size=8, align="right")
        put(W, RB+n+2, 2, "부채요소 (t=0)", bold=True)
        put(W, RB+n+2, 3, f"=C{RB}", bold=True, fmt=N2, align="right")
        put(W, RB+n+3, 2, "금리 고정 격자 (⑯)", bold=True)
        put(W, RB+n+3, 3, f"={Q(S16)}!C13", fmt=N2, align="right")
        put(W, RB+n+4, 2, "차이 = 금리에서 나온 옵션가치", bold=True)
        put(W, RB+n+4, 3, f"=C{RB+n+2}-C{RB+n+3}", bold=True, fmt=N2, align="right")

    if _need1 or _need2:
        # ── 17~20 옵션차익혼합할인법 ──
        # 제3자 지정 가능 콜옵션은 전환사채를 기초자산으로 하는 복합옵션이다.
        # 기초자산은 콜과 부속조항(의무보유)을 뺀 ⑧ 이다 (책 4.4.3, 부속예제 4-4).
        W = newsheet(S17, "⑰ 구성비율트리  지분 몫 ÷ 전환사채 가치",
                     "노드 가치 중 주식에서 온 몫의 비율이다.", f"{S5} · {S8}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln:
             f"=IF({Q(S8)}!{L}{R0+r}=0,0,{Q(S5)}!{L}{R0+r}/{Q(S8)}!{L}{R0+r})", N4)

        if _need1:                      # ⑱·⑳ 은 혼합할인율(방법 1) 전용이다
            W = newsheet(S18, "⑱ 혼합할인율트리  구성비율 × 무위험 + (1−구성비율) × 위험",
                         "이 칸을 직전 시점으로 할인할 때 쓰는 이자율이다. "
                         "지분 몫에는 무위험, 채권 몫에는 위험 선도이자율을 섞는다.",
                         S17, call_on=False)
            fill(W, lambda i, r, L, Lp, Ln:
                 (f"={Q(S17)}!{L}{R0+r}*{Lp}$11+(1-{Q(S17)}!{L}{R0+r})*{Lp}$12"
                  if i > 0 else "=0"), P2)

        W = newsheet(S19, "⑲ 콜 페이오프트리  MAX(전환사채 가치 − 매도청구금액, 0)",
                     "매도청구 행사기간에만 값이 생긴다. 기초자산은 ⑧ 이다.", S8)
        fill(W, lambda i, r, L, Lp, Ln:
             f"=IF({L}$5=1,MAX({Q(S8)}!{L}{R0+r}-{L}$8,0),0)")

        if _need1:
            W = newsheet(S20, "⑳ 매도청구권가치트리  미국형 복합옵션",
                         "자식의 구성비율로 섞은 할인율로 자식을 각각 할인한다.",
                         f"{S18} · {S19} · 다음 열 {S20}")
            fill(W, lambda i, r, L, Lp, Ln: (f"={Q(S19)}!{L}{R0+r}" if i == n else
                 f"=MAX({Q(S19)}!{L}{R0+r},"
                 f"{Ln}{R0+r}*{L}$16*EXP(-{Q(S18)}!{Ln}{R0+r}*{K['dt']})"
                 f"+{Ln}{R0+r+1}*{L}$17*EXP(-{Q(S18)}!{Ln}{R0+r+1}*{K['dt']}))"))
            put(W, R0+n+3, 2, "매도청구권 (한도 반영 전, t=0)", bold=True)
            put(W, R0+n+3, 3, f"=C{R0}", bold=True, fmt=N2, align="right")

    if _need2:
        # ── 21~24 옵션차익혼합할인법 · 방법 2 (지분·부채 분리) ──
        # 값 하나를 섞은 할인율로 할인하는 대신, 콜옵션 가치를 지분 몫과 부채 몫으로
        # 쪼개 각각 무위험·위험 선도이자율로 할인한다 (책 4.4.3, 부속예제 4-4 방법2).
        W = newsheet(S21, "㉑ [방법2] 지분 몫 보유가치",
                     "다음 두 칸의 지분 몫을 무위험 선도이자율로 할인한다.",
                     f"다음 열 {S23}")
        fill(W, lambda i, r, L, Lp, Ln: ("=0" if i == n else
             f"=({Q(S23)}!{Ln}{R0+r}*{L}$16+{Q(S23)}!{Ln}{R0+r+1}*{L}$17)"
             f"*EXP(-{L}$11*{K['dt']})"), N4)

        W = newsheet(S22, "㉒ [방법2] 부채 몫 보유가치",
                     "다음 두 칸의 부채 몫을 위험 선도이자율로 할인한다.",
                     f"다음 열 {S24}")
        fill(W, lambda i, r, L, Lp, Ln: ("=0" if i == n else
             f"=({Q(S24)}!{Ln}{R0+r}*{L}$16+{Q(S24)}!{Ln}{R0+r+1}*{L}$17)"
             f"*EXP(-{L}$12*{K['dt']})"), N4)

        ex2 = (lambda L, r: f"{Q(S19)}!{L}{R0+r}>={Q(S21)}!{L}{R0+r}+{Q(S22)}!{L}{R0+r}")
        W = newsheet(S23, "㉓ [방법2] 매도청구권 · 지분 몫",
                     "행사하면 페이오프의 지분 몫, 아니면 보유가치의 지분 몫이다. "
                     "만기에는 보유가치가 0 이라 언제나 페이오프를 쪼갠다.",
                     f"{S17} · {S19} · {S21} · {S22}")
        fill(W, lambda i, r, L, Lp, Ln:
             f"=IF({ex2(L, r)},{Q(S19)}!{L}{R0+r}*{Q(S17)}!{L}{R0+r},"
             f"{Q(S21)}!{L}{R0+r})", N4)

        W = newsheet(S24, "㉔ [방법2] 매도청구권 · 부채 몫",
                     "행사 판단은 ㉓ 과 같다. 지분 몫의 나머지가 부채 몫이다.",
                     f"{S17} · {S19} · {S21} · {S22}")
        fill(W, lambda i, r, L, Lp, Ln:
             f"=IF({ex2(L, r)},{Q(S19)}!{L}{R0+r}*(1-{Q(S17)}!{L}{R0+r}),"
             f"{Q(S22)}!{L}{R0+r})", N4)
        put(W, R0+n+3, 2, "매도청구권 · 방법2 (한도 반영 전, t=0)", bold=True)
        put(W, R0+n+3, 3, f"={Q(S23)}!C{R0}+C{R0}", bold=True, fmt=N2, align="right")

    # ── 결과 ──
    R = wb.create_sheet("결과"); R.sheet_view.showGridLines = False
    for cc, w in (("B", 36), ("C", 14), ("D", 16), ("E", 12), ("F", 42)):
        R.column_dimensions[cc].width = w
    title(R, 2, "평가결과", span=5)
    put(R, 3, 2, "모든 값이 앞의 트리 시트에서 수식으로 넘어온다.", color=GREY, size=9)
    sec(R, 5, "1. 트랜치", span=5)
    # 주계약과 부채요소에는 전환이 없어 TF 와 GS 가 항상 같다. 모형 선택이
    # 갈라지는 곳은 트랜치 둘뿐이므로 여기서 한 번만 고른다.
    # 앱에서 고른 것만 값이 든다. 행 자리는 그대로 두어 아래 참조가 깨지지 않게 한다.
    B_ = ""
    _t70 = f"={Q(S14)}!C{R0}" if _gs else f"={Q(S8)}!C{R0}"
    _t30 = f"={Q(S15)}!C{RT+1}" if _need15 else B_
    for i, (nm, fx) in enumerate([
            (f"{KW0} 트랜치 · TF", B_ if _gs else _t70),
            (f"{KW0} 트랜치 · GS", _t70 if _gs else B_),
            (f"{KW} 트랜치 · TF", B_ if _gs else _t30),
            (f"{KW} 트랜치 · GS", _t30 if _gs else B_),
            (f"적용 · {KW0} 트랜치", _t70),
            (f"적용 · {KW} 트랜치", _t30),
            ("가중 평균", f"=(1-{K['cw']})*C10+{K['cw']}*C11" if _need15 else B_)]):
        bold = (i >= 4)
        put(R, 6+i, 2, nm, bold=bold, border=True)
        put(R, 6+i, 3, fx, bold=bold, fmt=N2, align="right", border=True)
    sec(R, 14, "2. 구성요소", span=5)
    put(R, 15, 3, "100 기준", bold=True, fill=LIGHT, align="center", border=True)
    put(R, 15, 4, "전액 기준 (원)", bold=True, fill=LIGHT, align="center", border=True)
    put(R, 15, 5, "공시", bold=True, fill=LIGHT, align="center", border=True)
    items = [("주계약", f"={Q(S10)}!C{R0}"),
             ("부채요소 (사채 + 조기상환권)",
              f"='BDT 부채요소'!C{14+n+2}" if _bdt else f"={Q(S16)}!C13"),
             ("조기상환청구권", "=C17-C16"),
             ("매도청구권 · 유무가치비교법",
              f"={K['cw']}*(C10-C11)" if _need15 else B_),
             ("매도청구권 · 옵션차익 · 혼합할인율",
              f"={K['cw']}*{Q(S20)}!C{R0+n+3}" if _need1 else B_),
             ("매도청구권 · 옵션차익 · 지분·부채 분리",
              f"={K['cw']}*{Q(S24)}!C{R0+n+3}" if _need2 else B_),
             ("매도청구권자산 (적용값)", f"=C{19+_km}" if _hascall else "=0"),
             ("전환권대가 (자본일 때)", f'=IF({K["eqcls"]}=1,100-C17+{CAE},"")'),
             ("복합내재파생상품 (부채일 때)", f'=IF({K["eqcls"]}=0,C10-C16,"")'),
             ("주계약 잔여 (부채일 때)", f'=IF({K["eqcls"]}=0,100+C22-C24,"")')]
    if issuer_redeem(tm):
        # 부채 격자에서 잰 발행자 상환권. 자본 배분(전환권대가·회계처리)이 이 값을 쓴다.
        items.append(("매도청구권 · 부채 격자 기준 (자본 배분용)",
                      f"=MAX(0,{Q(S16)}!C13-'{S16C}'!C15)" if _rcps_call else "=0"))
    for i, (nm, fx) in enumerate(items):
        r = 16+i
        put(R, r, 2, nm, bold=True, border=True)
        put(R, r, 3, fx, bold=True, fmt=N2, align="right", border=True)
        put(R, r, 4, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
    # 마팅게일 검산식. SUMPRODUCT 로 마지막 열의 도달확률 × 주가를 모두 더한 뒤
    # 구간 선도이자율의 누적으로 할인하고 S0 으로 나눈다. 1 이 나와야 한다.
    _LN = gl(3+n)
    _GRW = "*".join(f"EXP({Q(S1)}!{gl(3+i)}$11*{K['dt']})"
                    for i in range(n)) or "1"
    _MG = (f"=SUMPRODUCT(도달확률!{_LN}5:{_LN}{5+n},"
           f"'01 주가'!{_LN}{R0}:{_LN}{R0+n})/({_GRW})/{K['S0']}")
    sec(R, 27, "3. 검산", span=5)
    for i, (nm, fx, jd) in enumerate([
            # q 는 구간마다 선도이자율로 다시 계산된다 (트리 머리 16행). 첫
            # 구간만 실으면 뒤쪽 구간이 0~1 을 벗어난 것을 조서에서 알 수 없다.
            # 값은 최소를, 판정은 최소·최대를 함께 본다.
            ("위험중립가중치 q · 전 구간 최소 (판정은 최대까지)",
             f"=MIN({Q(S1)}!{gl(3)}$16:{gl(2+n)}$16)",
             f'=IF(AND(C28>0,MAX({Q(S1)}!{gl(3)}$16:{gl(2+n)}$16)<1),'
             '"적합","확인 필요")'),
            # 합계 = 1 은 재귀식이든 이항식이든 자동으로 성립해서 틀려도 통과한다.
            # 대신 마팅게일을 본다 — 마지막 열의 도달확률로 잰 주가 기댓값을
            # 무위험으로 할인하면 평가기준일 주가가 나와야 한다. q·u·d·선도
            # 이자율·도달확률이 하나라도 어긋나면 이 값이 틀어진다.
            ("주가 마팅게일 (기댓값 ÷ 성장) ÷ S0", _MG,
             '=IF(ABS(C29-1)<0.000001,"적합","확인 필요")'),
            ("전체 ≥ 주계약", "=C10-C16", '=IF(C30>=0,"적합","확인 필요")'),
            ("배분 합계 = 100",
             f'=IF({K["ksep"]}=1,IF({K["eqcls"]}=1,C16+C18-{CAE}+C23,C25+C24-C22),'
             f'IF({K["eqcls"]}=1,C16+C18-{CAE}+C23,C25+C24-C22))',
             '=IF(ABS(C31-100)<0.01,"적합","확인 필요")'),
            # 상태확장 격자는 재결합하지 않아 엑셀 트리 한 장으로 옮길 수 없다.
            # 앱이 상태확장으로 계산했다면 이 조서는 근사값이므로 그 사실을 밝힌다.
            ("앱 계산값 · 상태확장 격자", (b2 if tm.carry == 0 else ""),
             '=IF(C32="","해당 없음 (앱과 조서가 같은 방법)",'
             'IF(ABS(C32-C10)<0.01,"적합","★ 조서는 근사값 — 아래 설명"))'),
            # 상각표의 유효이자율은 앱이 역산해 **값으로** 박은 것이라, 배분을
            # 움직이는 인풋이 하나라도 바뀌면 트리와 어긋난다. 그 사실이 상각표
            # 안에만 있으면 놓치기 쉬워 결과 시트에도 끌어올린다.
            ("상각표가 이 조서와 같은 계약인가", "=상각표!D21",
             '=IF(ABS(C33)<0.0001,"적합","★ 상각표가 예전 인풋이다 — 앱에서 다시 만드십시오")')]):
        put(R, 28+i, 2, nm, border=True)
        put(R, 28+i, 3, fx, fmt=N4, align="right", border=True,
            color=(AMB if i == 4 else "000000"))
        put(R, 28+i, 5, jd, align="center", border=True)
    if tm.carry == 0:
        put(R, 34, 2,
            "★ 앱은 상태확장 격자로 계산했다. 조정일마다 전환가액이 갈라져 같은 칸에 "
            "여러 값이 존재하므로 엑셀 트리 한 장으로는 옮길 수 없다. 이 조서는 "
            "경로가중치 근사로 다시 계산한 값이다. 위 두 숫자의 차이가 근사 오차이며, "
            "정확한 값은 앱 계산값(주황)이다.", color=RED, size=9)
    put(R, 35, 2, "이 조서에는 앱에서 고른 방법만 들어 있습니다. 다른 신용위험 처리나 "
        "다른 매도청구권 평가방법의 값은 이 조서에 없습니다.", color=GREY, size=9)
    put(R, 36, 2, "주황색 숫자만 값이다. 선도이자율은 부트스트래핑 결과라 엑셀에서 재현하지 않는다.",
        color=AMB, size=9)

    # ── 이자율곡선 ──
    # 각 트리 11·12행의 선도이자율이 어디서 왔는지 남긴다. 부트스트래핑은
    # 엑셀에서 재현하지 않으므로 여기서도 값이다.
    C = wb.create_sheet("이자율곡선"); C.sheet_view.showGridLines = False
    for cc, w in (("B", 12), ("C", 14), ("D", 14), ("E", 14), ("F", 14), ("G", 14)):
        C.column_dimensions[cc].width = w
    title(C, 2, "기간별 이자율", span=6)
    put(C, 3, 2, "선도이자율  f(t, t+Δt) = [ r(t+Δt)×(t+Δt) − r(t)×t ] ÷ Δt   "
        "· 각 트리 시트 11·12행이 이 값이다.", color=GREY, size=9)
    for i, h in enumerate(["시점 (년)", "무위험 현물", "무위험 선도",
                           "위험 현물", "위험 선도", "스프레드"]):
        put(C, 5, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    for k in range(9):
        t_ = tm.T*k/8; i = min(n-1, round(t_/dt_))
        fr = forward_rate(RF, i*dt_, (i+1)*dt_); fc = forward_rate(CR, i*dt_, (i+1)*dt_)
        for j2, v in enumerate([t_, RF(t_), fr, CR(t_), fc, fc-fr]):
            put(C, 6+k, 2+j2, v, fmt=(N2 if j2 == 0 else P2), align="right",
                border=True, color=(None if j2 == 0 else AMB))
    rr = 16
    if tm.y_type == "par" and len(tm.cr_curve) >= 2:
        sec(C, rr, "부트스트래핑 — 위험 곡선", span=6)
        for i, h in enumerate(["만기 (년)", "만기수익률", "할인계수", "현물 (연속)"]):
            put(C, rr+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        bs = [x for x in bootstrap_df(tm.cr_curve, tm.T, tm.cmp_cr) if x[0] > 0]
        for k, (t_, df) in enumerate(bs):
            for j2, v in enumerate([t_, _lin(tm.cr_curve, t_), df, -math.log(df)/t_]):
                put(C, rr+2+k, 2+j2, v,
                    fmt=(N2 if j2 == 0 else (N6 if j2 == 2 else P2)),
                    align="right", border=True)
        rr = rr+3+len(bs)
    put(C, rr, 2, "주황색은 값이다. 곡선을 바꾸려면 앱에서 조서를 다시 만들어야 한다.",
        color=AMB, size=9)

    # ── 상각표 ──
    # 유효이자율만 역산 결과(값)이고, 나머지는 살아 있는 수식이다.
    # 전체를 당기손익-공정가치로 지정했으면 상각할 주계약이 없다. 빈 표 대신
    # **왜 없는지**를 남긴다 — 값 조서와 같은 문안(FVPL_NOTE)을 쓴다.
    M = wb.create_sheet("상각표"); M.sheet_view.showGridLines = False
    for cc, w in (("B", 10), ("C", 13), ("D", 12), ("E", 16), ("F", 14),
                  ("G", 14), ("H", 16)):
        M.column_dimensions[cc].width = w
    if eir is None:
        title(M, 2, "주계약 상각표 — 만들지 않는다", span=7)
        M.column_dimensions["B"].width = 110
        for _i, _tx in enumerate(FVPL_NOTE if fvpl_on(tm) else HOST_NONPOS_XL):
            put(M, 4+_i, 2, _tx, color=(RED if _i == 0 else GREY),
                bold=(_i == 0), size=(10 if _i == 0 else 9))
            M.cell(row=4+_i, column=2).alignment = Alignment(wrap_text=True,
                                                             vertical="top")
    else:
        r_eir, rows_eir, redm, nper = eir
        title(M, 2, "주계약 상각표", span=7)
        put(M, 3, 2, "주계약(옵션 없는 사채)을 유효이자율법으로 상각한다. "
            "기말 잔액이 만기상환금액과 맞아떨어져야 한다. "
            "지급일은 계약상 일정이므로 발행일부터 센다. 평가기준일이 발행일보다 뒤이면 "
            "첫 회차만 짧고 나머지는 온전한 한 주기다. 회차 수는 노드가 아니라 "
            "이자 지급주기를 따른다.", color=GREY, size=9)
        sec(M, 5, "유효이자율 역산", span=6)
        for i, (k, fx, fm, val) in enumerate([
                # 부채로 분류하면 잔여로 떨어진 금액이 인식액이다. 이론값(C16)이 아니다.
                ("주계약 (인식액, 거래원가 차감 후)",
                 f'=IF({K["eqcls"]}=1,IF(AND({K["ksep"]}=1,{K["psep"]}=0),결과!C17,'
                 f'결과!C16),결과!C25)-회계처리!D32-회계처리!D33', N2, None),
                ("만기상환금액", f"={K['red']}", N2, None),
                ("표면이자 (회당)", f"=100*{K['cpn']}*{K['ipaym']}/12", N2, None),
                ("상각 횟수", None, N0, nper)]):
            put(M, 6+i, 2, k, border=True)
            put(M, 6+i, 3, fx if fx else val, fmt=fm, align="right", border=True)
        put(M, 10, 2, "유효이자율 (연, 이산복리)", bold=True, fill=BAND, border=True)
        put(M, 10, 3, r_eir, bold=True, fill=BAND, fmt=P2, align="right",
            border=True, color=AMB)
        put(M, 11, 2, "주황색은 역산 결과라 값이다. 아래 표는 이 이자율로 도는 수식이다.",
            color=AMB, size=9)
        sec(M, 13, "상각 내역", span=7)
        for i, h in enumerate(["회차", "지급일", "경과연수", "기초", "이자비용",
                               "지급이자", "기말"]):
            put(M, 14, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, row in enumerate(rows_eir):
            r = 15+i; last = (i == len(rows_eir)-1); fl = BAND if last else None
            prev = r-1
            put(M, r, 2, row[0], fmt=N0, align="right", border=True, bold=last, fill=fl)
            # 계약상 지급일이다. 마지막은 만기일, 나머지는 발행일에 달을 더한다.
            pdf = (f"={K['d_mat']}" if last else
                   f"=EDATE({K['d_issue']},{int(round(pay_index(tm, row[1])*tm.ipay))})")
            put(M, r, 3, pdf, fmt=DATE, align="right", border=True, bold=last, fill=fl)
            put(M, r, 4, row[1], fmt=N2, align="right", border=True, bold=last, fill=fl)
            put(M, r, 5, ("=$C$6" if i == 0 else f"=H{prev}"), fmt=N2, align="right",
                border=True, bold=last, fill=fl)
            # 이자비용 = 기초 × (1+r)^기간 − 기초.  회차마다 기간이 달라 이렇게 쓴다.
            gap = f"(D{r}" + ("" if i == 0 else f"-D{prev}") + ")"
            put(M, r, 6, f"=E{r}*((1+$C$10)^{gap}-1)", fmt=N2, align="right",
                border=True, bold=last, fill=fl)
            put(M, r, 7, "=$C$8", fmt=N2, align="right", border=True, bold=last, fill=fl)
            put(M, r, 8, f"=E{r}+F{r}-G{r}", fmt=N2, align="right",
                border=True, bold=last, fill=fl)
        lr = 15+len(rows_eir)
        put(M, lr, 2, "검산 · 기말 잔액 = 만기상환금액", bold=True, border=True)
        put(M, lr, 4, f"=H{lr-1}-$C$7", fmt=N2, align="right", border=True)
        put(M, lr, 6, f'=IF(ABS(H{lr-1}-$C$7)<0.01,"적합","확인 필요")',
            align="center", border=True)
        # 유효이자율은 값이라, 가정을 고쳐 인식액이 움직이면 표가 닫히지 않는다.
        put(M, lr+1, 2, "검산 · 인식액이 역산 당시와 같은가", bold=True, border=True)
        put(M, lr+1, 4, f"=C6-{rows_eir[0][2] if rows_eir else 0!r}", fmt=N2,
            align="right", border=True)
        put(M, lr+1, 6, f'=IF(ABS(C6-{rows_eir[0][2] if rows_eir else 0!r})<0.0001,'
            f'"적합","앱에서 다시 만드십시오")', align="center", border=True)

    # ── 회계처리 ──
    # 복합계약 전체를 당기손익-공정가치로 지정했는가. 이 시트가 통째로 갈리므로
    # 머리글보다 먼저 정한다.
    _FVROW = fvpl_on(tm)
    E = wb.create_sheet("회계처리"); E.sheet_view.showGridLines = False
    for cc, w in (("B", 34), ("C", 14), ("D", 14), ("E", 18), ("F", 18)):
        E.column_dimensions[cc].width = w
    title(E, 2, "회계처리", span=5)
    put(E, 3, 2, ("복합계약 **전체**를 당기손익-공정가치 측정 금융부채로 지정했으므로 "
                  "내재파생상품을 분리하지 않고 한 줄로 인식한다 (제1109호 문단 4.2.2 · "
                  "4.3.3(3)). 요소별 배분도 유효이자율 상각도 없다. 매도청구권은 제3자에게 "
                  "이전될 수 있어 별도의 금융상품이라(문단 4.3.1) 이 지정 밖에 남는다."
                  if _FVROW else
                  "기업회계기준서 제1032호 문단 31·32 — 부채요소를 먼저 정하고 나머지를 자본에 배분한다. "
                  "매도청구권은 제3자에게 이전될 수 있어 별도의 금융상품이다 (제1109호 문단 4.3.1, "
                  "회계기준원 질의회신 2022-I-KQA006, 금융위 2022.5.3 감독지침). "
                  "전환권이 부채이면 전환권과 조기상환권은 상호의존적이므로 하나의 복합내재파생상품으로 "
                  "전체로서 측정한다 (제1109호 문단 B4.3.4)."),
        color=GREY, size=9)
    if tm.elapsed_m > 0.01:
        put(E, 4, 2, "※ 평가기준일이 발행일보다 뒤입니다. 아래 배분은 최초 인식용이므로 "
            "결산 회계처리에 그대로 쓰지 마십시오. 결산일에 쓰는 것은 파생상품 공정가치뿐이고, "
            "주계약은 발행일 배분액을 유효이자율로 상각한 장부금액입니다.", color=RED, size=9)
    sec(E, 5, "1. 최초 인식 배분", span=5)
    for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
        put(E, 6, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    # 매도청구권을 별도 금융상품으로 볼지 내재파생에 넣을지에 따라 표가 갈린다.
    # 넣는 쪽이면 파생 줄에서 콜을 빼고 자산 줄을 비운다. 합계는 어느 쪽이든 100.
    KS = K["ksep"]
    # 조기상환권을 분리하지 않는 갈래. 전환권이 자본이고 매도청구권이 별도
    # 금융상품일 때만 살아 있다 — 매도청구권을 내재파생으로 묶으면 문단
    # B4.3.4 가 하나의 복합내재파생으로 다루라고 해서 조기상환권도 딸려 간다.
    NS = f'AND({K["eqcls"]}=1,{KS}=1,{K["psep"]}=0)'
    _HOST = f'=IF({K["eqcls"]}=1,IF({NS},"",결과!C16),결과!C25)'
    _PUT = f'=IF(OR({K["eqcls"]}=0,{KS}=0,{NS}),"",결과!C18)'
    _LIAB = f'=IF({NS},결과!C17,"")'
    # 전환권이 부채면 전환권+조기상환권 묶음, 자본이면서 콜을 내재파생으로
    # 넣었으면 조기상환권+매도청구권 묶음이다. 어느 쪽이든 순액 한 줄이다.
    _CMP = (f'=IF({K["eqcls"]}=0,IF({KS}=1,결과!C24,결과!C24-결과!C22),'
            f'IF({KS}=0,결과!C18-결과!{CAE},""))')
    _CALL = f'=IF({KS}=1,IF({K["eqcls"]}=1,-결과!{CAE},-결과!C22),"")'
    _EQ = f'=IF({K["eqcls"]}=1,결과!C23,"")'
    # 복합계약 **전체**를 당기손익-공정가치로 지정하면 요소별 줄이 한 줄로 접힌다.
    # 새로 계산할 값이 없다 — 부채 갈래의 「주계약 + 복합내재파생」 합이 그대로
    # 전체 공정가치이고, 그것은 100 (콜을 묶었을 때) 또는 100 + 매도청구권
    # (콜이 별도 금융상품일 때) 이다.
    #
    # 줄을 새로 만들지 않고 **첫 줄을 그 자리로 쓴다.** 이 시트는 「주계약과
    # 부채요소는 서로 배타적이다」라는 규칙으로 이미 돌아가고 있어서, 배타적인
    # 갈래를 하나 더하는 것이 자연스럽다. 행 번호가 밀리지 않는 이점도 크다 —
    # 분개·거래원가·기말 재평가가 모두 이 표의 행을 이름으로 가리킨다.
    # 전체 지정은 **구조**를 정하는 선택이라 엑셀에서 셀을 바꿔도 따라올 수 없다
    # (평가방법·신용위험 처리와 같은 부류다). 그래서 수식으로 갈래를 만들지 않고
    # 앱에서 고른 쪽만 싣는다. 나머지 줄은 비워 둔다 — 한 줄과 요소별 줄이 함께
    # 서면 합계가 두 배가 된다.
    #
    # 전체 지정은 전환권이 **부채**일 때만 성립하므로(문단 4.2.2) 매도청구권은
    # 부채 갈래와 같은 칸(결과!C22)을 본다.
    if _FVROW:
        _HOST = f'=IF({KS}=1,100+결과!C22,100)'
        _LIAB = _PUT = _CMP = _EQ = ""
    _HOSTNM = ("복합계약 전체 · 당기손익-공정가치 측정 금융부채" if _FVROW else "주계약")
    al2 = [(_HOSTNM, _HOST),
           ("부채요소 (사채 + 조기상환권)", _LIAB),
           ("조기상환청구권 · 파생상품부채", _PUT),
           ("복합내재파생상품 · 파생상품부채", _CMP),
           ("매도청구권 · 파생상품자산", _CALL),
           ("전환권대가 · 자본", _EQ)]
    for i, (nm, fx) in enumerate(al2):
        r = 7+i
        put(E, r, 2, nm, border=True)
        put(E, r, 3, fx, fmt=N2, align="right", border=True)
        put(E, r, 4, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
    put(E, 13, 2, "합계", bold=True, fill=BAND, border=True)
    put(E, 13, 3, "=SUM(C7:C12)", bold=True, fill=BAND, fmt=N2, align="right", border=True)
    put(E, 13, 4, "=SUM(D7:D12)", bold=True, fill=BAND, fmt=N0, align="right", border=True)
    put(E, 14, 2, ("복합계약 전체를 당기손익-공정가치로 지정했으므로 첫 줄 하나만 찬다. "
                   "요소별 배분을 하지 않으므로 아래 줄들은 비어 있고, 유효이자율 "
                   "상각표도 만들지 않는다 (제1109호 문단 4.2.2 · 4.3.3(3)). "
                   "매도청구권은 별도의 금융상품이라 이 지정 밖에 남는다 (문단 4.3.1)."
                   if _FVROW else
                   "주계약과 부채요소는 서로 배타적이다 — 조기상환권을 분리하면 위 줄이, "
                   "분리하지 않으면 아래 줄이 찬다."), color=GREY, size=9)
    sec(E, 16, "2. 분개", span=5)
    for i, h in enumerate(["계정", "차변 (100)", "대변 (100)", "차변 (원)", "대변 (원)"]):
        put(E, 17, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    je2 = [("현금", "=100", None),
           ("파생상품자산 (매도청구권)", f'=IF({KS}=1,IF({K["eqcls"]}=1,결과!{CAE},결과!C22),"")', None),
           (("　당기손익-공정가치 측정 금융부채 (복합계약 전체)" if _FVROW
             else "　전환사채 (주계약)"), None, _HOST),
           ("　전환사채 (부채요소)", None, _LIAB),
           ("　파생상품부채 (조기상환청구권)", None, _PUT),
           ("　파생상품부채 (복합내재파생상품)", None, _CMP),
           ("　전환권대가 (자본)", None, _EQ)]
    for i, (nm, dr, cr) in enumerate(je2):
        r = 18+i
        put(E, r, 2, nm, size=9, border=True)
        put(E, r, 3, dr if dr else "", fmt=N2, align="right", border=True)
        put(E, r, 4, cr if cr else "", fmt=N2, align="right", border=True)
        put(E, r, 5, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
        put(E, r, 6, f'=IF(ISNUMBER(D{r}),D{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
    put(E, 25, 2, "합계", bold=True, fill=BAND, border=True)
    for j2, col in enumerate("CDEF"):
        put(E, 25, 3+j2, f"=SUM({col}18:{col}24)", bold=True, fill=BAND,
            fmt=(N2 if j2 < 2 else N0), align="right", border=True)
    put(E, 26, 2, "차변과 대변이 일치해야 한다", bold=True, border=True)
    put(E, 26, 3, '=IF(ABS(C25-D25)<0.01,"적합","오류")', align="center", border=True)
    put(E, 28, 2, "최초 인식에는 어떠한 손익도 생기지 않는다.", color=GREY, size=9)
    # ── 거래원가 배분 (1032 문단 38) ── 가정의 거래원가 셀을 바꾸면 따라온다
    sec(E, 30, "3. 거래원가 배분 (1032 문단 38)", span=6)
    for i, h in enumerate(["요소", "배분액 (100)", "거래원가 몫 (100)", "몫 (원)", "처리"]):
        put(E, 31, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    _C100 = f'{K["cost"]}/{K["face"]}*100'
    # 분모는 복합금융상품에 배분된 양수 줄의 합이다 (매도청구권 자산은 별도 금융상품).
    _BASE = "SUMIF(C7:C12,\">0\")"
    _CROWS = [((_HOSTNM, "C7", "즉시 비용 (당기손익-공정가치)") if _FVROW
               else ("주계약", "C7", "부채에서 차감 — 유효이자율에 반영")),
              ("부채요소 (사채 + 조기상환권)", "C8", "부채에서 차감 — 유효이자율에 반영"),
              ("조기상환청구권 · 파생상품부채", "C9", "즉시 비용 (당기손익-공정가치)"),
              ("복합내재파생상품 · 파생상품부채", "C10", "즉시 비용 (당기손익-공정가치)"),
              ("전환권대가 · 자본", "C12", "자본에서 차감")]
    for i, (nm, cell, how) in enumerate(_CROWS):
        r = 32+i
        put(E, r, 2, nm, border=True, size=9)
        put(E, r, 3, f'=IF(ISNUMBER({cell}),{cell},"")', fmt=N2, align="right", border=True)
        put(E, r, 4, f'=IF(AND(ISNUMBER({cell}),{cell}>0),{_C100}*{cell}/{_BASE},0)',
            fmt=N4, align="right", border=True)
        put(E, r, 5, f'=C{r}/100*{K["face"]}'.replace(f"C{r}", f"D{r}"),
            fmt=N0, align="right", border=True)
        put(E, r, 6, how, border=True, size=9)
    put(E, 37, 2, "합계", bold=True, fill=BAND, border=True)
    put(E, 37, 4, "=SUM(D32:D36)", bold=True, fill=BAND, fmt=N4, align="right", border=True)
    put(E, 37, 5, "=SUM(E32:E36)", bold=True, fill=BAND, fmt=N0, align="right", border=True)
    put(E, 38, 2, ("전체를 당기손익-공정가치로 지정했으므로 거래원가를 얹을 자리가 없어 "
                   "전액 즉시 비용이다 (제1109호 문단 5.1.1). 유효이자율에 녹이는 몫이 "
                   "없고 상각표도 없다."
                   if _FVROW else
                   "배분된 발행금액에 비례해 나눈다. 매도청구권 자산은 별도의 금융상품이라 "
                   "(문단 4.3.1) 분모에서 뺐다. 상각표는 주계약에서 이 몫을 뺀 금액에서 "
                   "출발한다."), color=GREY, size=9)

    # ── 기말 재평가 — 전기말 장부금액이 있을 때만 값이 찬다 (수식은 늘 산다) ──
    RM = 40                                   # 기말 재평가 절 첫 행
    # 전체 지정이면 재평가 대상이 파생상품부채가 아니라 복합계약 한 줄 전부다.
    sec(E, RM, ("4. 기말 재평가 — 복합계약 전체 (당기손익-공정가치)" if _FVROW
                else "4. 기말 재평가 — 파생상품부채"), span=5)
    for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
        put(E, RM+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    _FVL = ("C7" if _FVROW else
            f'IF({K["eqcls"]}=1,IF({NS},0,IF({KS}=1,결과!C18,결과!C18-결과!{CAE})),'
            f'IF({KS}=1,결과!C24,결과!C24-결과!C22))')
    _HAS = f'{K["pdrv"]}>=0'
    for i, (k, fx) in enumerate([
            ("전기말 장부금액 (가정)", f'=IF({_HAS},{K["pdrv"]},"")'),
            (("당기말 공정가치 (배분표의 복합계약 전체)" if _FVROW
              else "당기말 공정가치 (배분표의 파생상품부채)"), f"={_FVL}"),
            ("평가손익 (+ 손실 · − 이익)", f'=IF({_HAS},C{RM+3}-C{RM+2},"")'),
            ("주계약 전기말 장부금액 (참고 · 재평가 대상 아님)",
             f'=IF(AND({_HAS},{K["phst"]}>=0),{K["phst"]},"")')]):
        r = RM+2+i
        put(E, r, 2, k, border=True, bold=(i == 2), fill=(BAND if i == 2 else None))
        put(E, r, 3, fx, fmt=N4, align="right", border=True, bold=(i == 2),
            fill=(BAND if i == 2 else None))
        put(E, r, 4, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")', fmt=N0,
            align="right", border=True, bold=(i == 2), fill=(BAND if i == 2 else None))
    _PL, _PLD = f"C{RM+4}", f"D{RM+4}"
    _DRCR = (('"차) 금융부채평가손실 / 대) 당기손익-공정가치 측정 금융부채"',
              '"차) 당기손익-공정가치 측정 금융부채 / 대) 금융부채평가이익"')
             if _FVROW else
             ('"차) 파생상품평가손실 / 대) 파생상품부채"',
              '"차) 파생상품부채 / 대) 파생상품평가이익"'))
    put(E, RM+7, 2, f'=IF(NOT(ISNUMBER({_PL})),"전기말 장부금액이 없어 재평가 없음 (발행 시점 평가)",'
                    f'IF({_PL}>=0,{_DRCR[0]},{_DRCR[1]}))', bold=True, size=9)
    put(E, RM+7, 3, f'=IF(ISNUMBER({_PL}),ABS({_PL}),"")', fmt=N4, align="right", bold=True)
    put(E, RM+7, 4, f'=IF(ISNUMBER({_PLD}),ABS({_PLD}),"")', fmt=N0, align="right", bold=True)
    put(E, RM+8, 2, ("상각후원가로 남는 주계약이 없어 유효이자율 이자비용이 없다. "
                     "자기신용위험 변동분은 기타포괄손익으로 표시해야 하는데(제1109호 "
                     "문단 5.7.7) 이 조서는 그 분해를 하지 않는다 — 직접 나누어야 한다."
                     if _FVROW else
                     "주계약은 발행일 유효이자율로 상각한 장부금액을 쓴다. 이 조서의 "
                     "상각표는 평가기준일 배분액에서 출발하므로 최초 인식 평가에만 "
                     "맞는다."), color=GREY, size=9)

    # ── 분리 판단 ──
    # 화면과 같은 함수가 만든 문안이라 둘이 어긋날 수 없다.
    SP = split_test(tm, full, b0, b1, b2, ca, [] if eir is None else eir[1])
    J = wb.create_sheet("분리 판단"); J.sheet_view.showGridLines = False
    J.column_dimensions["B"].width = 24; J.column_dimensions["C"].width = 92
    title(J, 2, "내재파생상품 분리 판단", span=2)
    put(J, 3, 2, "판단 순서 — 기업회계기준서 제1109호 문단 B4.3.5 말미는 제1032호에 "
                 "따라 전환채무상품의 자본요소를 분리하기 전에 내재된 콜옵션이나 "
                 "풋옵션이 주채무계약과 밀접하게 관련되어 있는지를 판단하라고 정한다.",
        color=GREY, size=9)
    # 격자 의사결정의 설계도. 계약을 표로 편 뒤에 분리 판단이 온다.
    for _c, _w in zip("BCDEFG", (20, 16, 22, 26, 34, 20)):
        J.column_dimensions[_c].width = _w
    _r = 5
    sec(J, _r, "계약상 권리 — 격자 의사결정의 설계도", span=6); _r += 1
    for _i, _c in enumerate(RIGHT_COLS):
        put(J, _r, 2+_i, _c, bold=True, fill=LIGHT, align="center", border=True, size=9)
    _r += 1
    for _row in rights_table(tm):
        for _i, _v in enumerate(_row):
            put(J, _r, 2+_i, _v, border=True, size=9, bold=(_i == 0))
        _r += 1
    put(J, _r, 2, "조기상환청구권과 매도청구권이 같은 노드에서 함께 열릴 때 누가 먼저 "
                  "움직이는지는 계약이 정한다 — 수식이 정하는 것이 아니다. 이 조서는 "
                  + ("「발행자 매도청구 우선」" if int(tm.pc_order) == 1
                     else "「투자자 조기상환 우선」")
                  + " 으로 계산했다. 두 행사금액이 다르고 행사기간이 겹치는 자리에서만 "
                    "값이 갈린다.", color=GREY, size=9); _r += 2
    for _k, _nm in (("put", "조기상환청구권"), ("call", "매도청구권")):
        _d = SP[_k]
        sec(J, _r, _nm, span=2); _r += 1
        put(J, _r, 2, "결론", bold=True, border=True)
        put(J, _r, 3, _d["결론"], bold=True, border=True); _r += 1
        for _i, _x in enumerate(_d["이유"]):
            put(J, _r, 2, "판단 근거" if _i == 0 else "", border=True)
            put(J, _r, 3, _x, border=True); _r += 1
        put(J, _r, 2, "기준서", border=True)
        put(J, _r, 3, " · ".join(_d["근거"]) or "—", border=True); _r += 1
        put(J, _r, 2, "평가방법", border=True)
        put(J, _r, 3, _d["평가"].replace("**", ""), border=True); _r += 1
        for _a, _v in _d["지표"].items():
            put(J, _r, 2, _a, border=True)
            put(J, _r, 3, (f"{_v*100:.1f}%" if _a == "차이" else
                           ("예" if _v is True else "아니오" if _v is False
                            else f"{_v:,.4f}")), border=True); _r += 1
        _r += 1
    # 판정과 실제 회계처리 설정이 어긋나면 이 조서 안에서 「분리 판단」 시트와
    # 「회계처리」 시트가 서로 다른 말을 하게 된다. 그 사실을 여기 적어 둔다.
    _mis = [(_nm, SP[_k]) for _k, _nm in
            (("put", "조기상환청구권"), ("call", "매도청구권"))
            if not SP[_k].get("설정일치", True)]
    sec(J, _r, "판정과 회계처리 설정이 맞는가", span=6); _r += 1
    if _mis:
        for _nm, _d in _mis:
            _set = ((("분리 · 파생상품부채" if int(tm.p_sep) else "분리하지 않음 · 부채요소에 포함")
                     if _nm == "조기상환청구권" else
                     ("별도 금융상품" if tm.k_sep else "복합내재파생에 포함")))
            put(J, _r, 2, f"★ {_nm}", bold=True, color=RED, border=True)
            put(J, _r, 3, f"판정은 「{_d['결론']}」 인데 이 조서의 회계처리 설정은 "
                          f"「{_set}」 이다. 배분표와 분개가 위 판정과 다르게 나온다 — "
                          "어느 쪽이 계약에 맞는지 정하고 근거를 남겨야 한다.",
                color=RED, border=True); _r += 1
    else:
        put(J, _r, 2, "일치", bold=True, border=True)
        put(J, _r, 3, "위 판정과 이 조서의 회계처리 설정이 같다. 배분표·분개·상각표가 "
                      "판정대로 만들어졌다.", border=True); _r += 1
    _r += 1
    put(J, _r, 2, "이 시트는 앱의 「분리 판단」 화면과 같은 함수가 만든다. 계약 조항 "
                  "확인 항목을 **앱에서** 바꾸면 결론과 문안이 함께 바뀐다. "
                  "이 시트는 그 결과를 옮겨 적은 것이라 수식이 없다.  판정에 쓰는 "
                  "상각후원가는 문단 B4.3.5(5)(가) 대로 **주계약(B0)** 기준이라 "
                  "분리 여부 설정과 무관하다.",
        color=GREY, size=9)
    for _row in J.iter_rows(min_row=5, max_row=_r, min_col=3, max_col=3):
        for _c in _row: _c.alignment = Alignment(wrap_text=True, vertical="top")

    # ── 해설 ──
    write_check_sheets(wb, tm, model_checks(tm, full, b0, b1, b2, ca, eir))

    H = wb.create_sheet("해설", 0); H.sheet_view.showGridLines = False
    H.column_dimensions["B"].width = 22; H.column_dimensions["C"].width = 96
    title(H, 2, "수식 조서 사용 안내", span=2)
    ex = [("성격", ""),
      ("살아 있는 수식", "가정 시트의 노란 셀을 바꾸면 모든 트리가 다시 계산된다."),
      ("값으로 들어간 것", "각 시트 11·12행의 선도이자율. 부트스트래핑 결과라 엑셀에서 재현하기 어렵다."),
      ("머리와 스텝", "머리 1·3~10행은 모두 2행(스텝)을 참조한다. 2행 자신도 직전 열 + 1 이라 "
                    "맨 앞 열의 0 하나에서 모든 열이 정해진다. 날짜도 스텝에서 나온다."),
      ("바꿀 수 없는 것", "노드 수와 리픽싱 주기는 격자 구조를 정하므로 앱에서 다시 만들어야 한다."),
      ("옮길 수 없는 것", "상태확장 격자는 재결합하지 않아 엑셀 트리 한 장으로 표현할 수 없다. "
                     "앱에서 상태확장을 골랐다면 이 조서는 경로가중치 근사이고, "
                     "결과 시트 3번 검산 마지막 줄에 앱 계산값과의 차이가 나온다."),
      ("", ""),
      ("시트 순서", ""),
      # 시트는 앱에서 고른 방법만 만든다. 안내도 실제로 만들어진 시트만
      # 적어야 한다 — 없는 시트를 가리키면 조서를 읽는 사람이 헤맨다.
      ("흐름", " → ".join(["가정"] + [x for x in wb.sheetnames
                                    if x not in ("가정", "해설")])),
      ("담긴 것", "앱에서 고른 방법의 트리만 들어 있다. 고르지 않은 방법 "
                "(다른 신용위험 처리, 다른 매도청구권 평가방법)의 시트는 "
                "아예 만들지 않는다."),
      ("도달확률", "02 전환가격이 경로가중치 방법을 쓸 때 참조한다. "
                 "앞 열에서 한 칸씩 쌓는 재귀식이다 — 구간마다 q 가 달라 "
                 "이항계수 한 방으로는 셀 수 없다."),
      ("16 부채요소", "전환이 없으면 주가와 무관해 한 줄로 끝난다. 결과 시트가 이 값을 쓴다."),
      ("", ""),
      ("05~09가 순환처럼 보이는 이유", ""),
      ("사실", "05·06은 같은 열의 09를 보지만, 07은 다음 열의 05·06을 본다."),
      ("결과", "오른쪽 열이 먼저 확정되고 왼쪽으로 오므로 고리가 닫히지 않는다."),
      ("확인", "아무 칸에서 F2를 누르면 참조 테두리가 오른쪽이나 같은 열에만 생긴다."),
      ("", ""),
      ("노드에서 무엇을 고르는가", ""),
      ("투자자 권리", "전환 · 조기상환청구 · 보유. 셋 중 자기에게 가장 유리한 것을 고른다."),
      ("발행자 권리", "매도청구. 투자자 가치를 눌러 내리는 쪽으로만 쓴다."),
      ("적용한 식", "MAX(전환, MIN(MAX(보유, 조기상환), 매도청구))　— 발행자 매도청구 우선" if int(tm.pc_order) == 1 else "MAX(전환, 조기상환, MIN(보유, 매도청구))　— 투자자 조기상환 우선"),
      ("우선순위", "두 권리가 같은 노드에서 함께 열릴 때 누가 먼저 움직이는지는 "
              "계약이 정한다 — 수식이 정하는 것이 아니다. 두 행사금액이 다르고 "
              "행사기간이 겹치는 자리에서만 값이 갈린다. 「분리 판단」 시트의 "
              "「계약상 권리」 표에 고른 근거를 남겨야 한다."),
      ("만기 노드", "매도청구는 없다. 전환가치와 현금(MAX(조기상환금액, 만기상환금액) + 이자) "
              "둘만 견준다."),
      ("두 모형이 한 노드에", "한 노드가 **두 모형을 함께 담는다** — 지분·부채 두 줄은 "
              "TF(Tsiveriotis–Fernandes) 이고, 금융상품가치 한 줄은 GS(Goldman Sachs) 다. "
              "그래서 «금융상품가치 ≠ 지분 + 부채» 인 것이 정상이다. 결함이 아니라 두 "
              "모형이 같은 계약을 다르게 재는 것이다 — 지분+부채는 TF 결과와, "
              "금융상품가치는 GS 결과와 각각 정확히 맞는다."),
      ("결정은 한 곳에서", "전환사채·우선주와 신주인수권부사채가 **같은 판정 함수**를 "
              "쓴다. 상품마다 다른 것은 「이겼을 때 무엇을 받는가」뿐이다 — 예전에는 "
              "판정까지 복제되어 있어 한쪽만 고쳐지는 일이 있었다."),
      ("", ""),
      ("동점을 어떻게 깨는가", ""),
      ("언제 생기나", "리픽싱이 주가로 재설정되는 날에는 전환가액 = 주가이므로 "
                  "전환가치가 정확히 100 이 된다. 같은 날 조기상환금액도 100 이면 값이 같다."),
      ("규칙", "전환은 허용오차(1e-9)만큼 앞설 때만 이긴다. 동점이면 현금(상환)이다. "
             "지분으로 보면 무위험이자율로 할인되어 값이 올라가므로 현금 쪽이 보수적이다."),
      ("왜 정해야 하나", "정해 두지 않으면 부동소수 잡음이 갈라 놓는다. "
                    "TF 는 지분과 부채를 다른 이자율로 할인하므로 한 노드의 판정이 "
                    "전체 값을 몇 포인트씩 움직인다."),
      ("", ""),
      ("주의", ""),
      ("상태확장", "수식 조서는 재결합 격자에서만 만들 수 있다. 앱이 경로가중치로 대체한다."),
      ("매도청구권", "이 조서는 "
                    + ["유무가치비교법(⑮ 트랜치 두 개의 차이)",
                       "옵션차익 · 혼합할인율(⑰~⑳)",
                       "옵션차익 · 지분·부채 분리(⑰⑲㉑~㉔)"][tm.k_method]
                    + " 으로 잰다. 가정 시트의 평가방법 줄은 그 사실을 적어 둔 "
                      "것이지 고르는 칸이 아니다 — 다른 방법의 트리는 이 조서에 "
                      "없다."),
      ("신용위험 처리", ("이 조서는 TF 로 잰다. " if tm.model == "TF"
                       else "이 조서는 GS 로 잰다. ")
                     + "고르지 않은 쪽의 트리는 만들지 않았으므로 가정 시트의 "
                       "TF/GS 줄을 바꿔도 값이 따라오지 않는다. 주계약과 "
                       "부채요소는 전환이 없어 두 모형이 같다."),
      ("이자율곡선", "각 트리 11·12행 선도이자율의 출처다. 부트스트래핑 표까지 남긴다."),
      ("상각표", "유효이자율만 역산 결과라 값이고, 상각 내역은 수식이다. "
                "기말 잔액이 만기상환금액과 맞는지 마지막 줄에서 검산한다."),
      ("검산", "결과 시트 3번과 상각표 마지막 줄을 먼저 보고 모두 적합인지 확인한다.")]
    r = 4
    for a2, b3 in ex:
        if a2 and not b3: sec(H, r, a2, span=2)
        elif a2:
            put(H, r, 2, a2, bold=True, size=9); put(H, r, 3, b3, size=9)
        r += 1
    for i in range(4, r):
        H.cell(row=i, column=3).alignment = Alignment(horizontal="left", vertical="center")

    if _attached:
        # 산출내역은 조서를 다 읽은 뒤에 보는 부록이라 뒤로 보낸다.
        _rest = [w for w in wb._sheets if w.title not in _attached]
        _tail = [w for w in wb._sheets if w.title in _attached]
        wb._sheets = _rest + _tail
    polish_wb(wb)
    relabel_inst(wb, tm)
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue()



def build_xlsx_sha(tm: Terms, R, formula: bool = False, attach=None):
    """주주간계약 조서. 값 조서와 수식 조서를 **한 함수**에서 만든다.

    두 조서가 같은 답을 내야 한다는 요구가 있어, 자리와 차례를 따로 적어 두면
    언젠가 어긋난다. 그래서 칸마다 「값」과 「수식」을 나란히 주고 스위치 하나로
    고른다 — 구조가 하나뿐이니 갈라질 자리가 없다.

    시트는 여덟 장이다.
        해설 · 가정 · 01 주가 · 02 지분가치 · 03 풋가치 · 04 콜가치 ·
        결과 · 회계처리
    """
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter as gl

    derive(tm)
    n, dt_ = int(tm.n), tm.T/int(tm.n)
    mper = n/(tm.T*12)
    R0 = 17                                   # 트리 첫 자료행
    N2, N0, P2, N4, N6 = '#,##0.00', '#,##0', '0.00%', '0.0000', '0.000000'
    DATE = 'yyyy-mm-dd'
    YEL = "FFF6D8"                            # 수식 조서의 입력 셀
    st_lo, st_hi = step_mapper(tm, n, dt_)
    per_ = lambda fr: max(1, int(round(fr*mper)))

    wb = Workbook(); wb.remove(wb.active)
    _volref = _rvolref = _irref = None
    if attach:
        _volref, _rvolref, _irref = attach_reports(wb, tm, **attach)
        _order = list(wb.sheetnames)
    K = report_kit(wb)
    put, head, sec, cols, note, sheet = (K[x] for x in
        ("put", "head", "sec", "cols", "note", "sheet"))
    Q = lambda nm: f"'{nm}'"
    V = lambda val, fx: (fx if formula else val)

    S1, S2, S3, S4 = "01 주가", "02 지분가치", "03 풋가치", "04 콜가치"

    # ── 해설 ──
    H = sheet("해설", widths=[22, 96])
    head(H, 2, "주주간계약 평가 조서 — 읽는 법",
         "사채가 없는 계약이다. 투자자가 이미 가진 지분에 풋과 콜이 붙어 있을 뿐이라 "
         "순차 차감이 아니라 두 옵션을 따로 잰다.", span=2)
    _rows = [
        ("무엇을 재나", "투자자 풋옵션(지분을 되팔 권리)과 최대주주 콜옵션(지분을 "
                     "사 갈 권리)을 각각 미국형으로 잰다. 기초자산은 대상회사 "
                     "지분이고 금액 기준은 투자원금 100 이다."),
        ("왜 따로 재나", "풋은 투자자가, 콜은 최대주주가 고른다. 보유자가 다르므로 "
                      "한 격자에서 함께 최적화하면 두 사람을 한 사람으로 만드는 "
                      "셈이 된다. 각자의 재무제표에 총액으로 싣는 것도 같은 이유다."),
        ("지분가치", "100 × 주가 ÷ 주당 인수가액. 평가기준일 주가가 인수가액과 "
                  "같으면 100 이다."),
        ("풋 행사가치", "MAX(풋 행사금액 − 지분가치, 0). 행사금액은 투자원금에 "
                     "보장수익률을 복리로 붙인 값이다."),
        ("콜 행사가치", "MAX(지분가치 − 콜 행사금액, 0)."),
        ("적격상장", "그 노드의 주가가 최소 기준을 넘으면 상장이 이루어진 것으로 "
                  "본다. 투자자가 시장에서 팔 수 있게 되므로 풋이 소멸한다. "
                  "콜도 함께 끝나는지는 계약에서 고른다."),
        ("할인율", "풋은 현금을 받을 권리라 의무자의 신용위험이 붙는다 (8행). "
                 "콜은 주식을 받을 권리라 인도 위험이 사실상 없어 무위험으로 "
                 "잰다 (9행)."),
        ("총액 부채", "발행회사가 풋 의무자면 기준서 1032 문단 23 에 따라 옵션 "
                   "공정가치가 아니라 **상환금액의 현재가치**를 총액으로 싣는다. "
                   "결과 시트에 함께 낸다."),
        ("이 파일", "값 조서는 계산 결과를 담은 스냅샷이고, 수식 조서는 노란 셀을 "
                 "바꾸면 엑셀 안에서 다시 계산된다. 둘은 같은 자리에 같은 값을 "
                 "낸다."),
    ]
    cols(H, 5, ["항목", "설명"], widths=[22, 96])
    for i, (a, b) in enumerate(_rows):
        put(H, 6+i, 2, a, bold=True, size=9, border=True)
        put(H, 6+i, 3, b, size=9, border=True, wrap=True)
        H.row_dimensions[6+i].height = max(16, 14*(1 + len(b)//80))

    # ── 가정 ──
    A = sheet("가정", widths=[34, 18, 14, 60])
    head(A, 2, "가정", ("노란 셀을 바꾸면 모든 트리가 다시 계산된다."
                      if formula else
                      "앱이 계산한 값을 그대로 담았다."), span=4)
    K_ = {}

    def kv(r, name, val, fmt=None, memo="", key=None, yellow=True):
        put(A, r, 2, name, bold=True, size=9.5, border=True)
        put(A, r, 3, val, fmt=fmt, size=9.5, align="right", border=True,
            fill=(YEL if (formula and yellow) else None))
        if memo: put(A, r, 4, memo, size=9, color=RPT["grey"], wrap=True)
        if key: K_[key] = f"{Q('가정')}!$C${r}"

    sec(A, 4, "1. 대상 지분", span=4)
    kv(5, "평가기준일 주가 (원)", tm.S0, N2, "", "S0")
    kv(6, "주당 인수가액 (원)", tm.K0, N2, "지분가치 = 100 × 주가 ÷ 이 값", "K0")
    kv(7, "평가기준일", dt.date.fromisoformat(tm.d_base), DATE, "", "d_base")
    kv(8, "투자일 → 평가기준일 경과 (개월)", tm.elapsed_m, N2, "", "elm")
    kv(9, "노드 수", n, N0, "", "n")
    kv(10, "Δt (년)", dt_, N6, "", "dt")
    kv(11, "변동성 σ (연)", tm.sig, P2, "", "sig")
    kv(12, "u = EXP(σ√Δt)", V(R["u"], f"=EXP({K_['sig']}*SQRT({K_['dt']}))"),
       N4, "", "u", yellow=False)
    kv(13, "d = 1 ÷ u", V(R["d"], f"=1/{K_['u']}"), N4, "", "dd", yellow=False)
    kv(14, "q (첫 구간)", R["q"], N4, "각 구간의 q 는 트리 13행에서 다시 잰다", "q",
       yellow=False)
    kv(15, "1 − q", 1-R["q"], N4, "", "q1", yellow=False)

    sec(A, 17, "2. 투자자 풋옵션", span=4)
    kv(18, "행사 시작 (스텝)", st_lo(tm.sha_put_s), N0,
       f"계약 {tm.sha_put_s:,.0f}개월", "pst")
    kv(19, "행사 종료 (스텝)", st_hi(tm.sha_put_e), N0,
       f"계약 {tm.sha_put_e:,.0f}개월", "pen")
    kv(20, "행사 주기 (스텝)", per_(tm.sha_put_f), N0,
       f"계약 {tm.sha_put_f:,.0f}개월", "pfrq")
    kv(21, "보장수익률 (연)", tm.sha_put_yield, P2, "", "pyld")
    kv(22, "보장 복리 횟수", tm.sha_put_cmp, N0, "0 이면 단리", "pcmp")

    sec(A, 24, "3. 최대주주 콜옵션", span=4)
    _hascall = tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
    kv(25, "콜 있음 (1) / 없음 (0)", 1 if _hascall else 0, N0, "", "con")
    kv(26, "행사 시작 (스텝)", st_lo(tm.sha_call_s), N0,
       f"계약 {tm.sha_call_s:,.0f}개월", "kst")
    kv(27, "행사 종료 (스텝)", st_hi(tm.sha_call_e), N0,
       f"계약 {tm.sha_call_e:,.0f}개월", "ken")
    kv(28, "행사 주기 (스텝)", per_(tm.sha_call_f), N0,
       f"계약 {tm.sha_call_f:,.0f}개월", "kfrq")
    kv(29, "행사금액 가산율 (연)", tm.sha_call_prem, P2, "", "cprem")
    kv(30, "가산 복리 횟수", tm.sha_call_cmp, N0, "0 이면 단리", "ccmp")

    sec(A, 32, "4. 적격상장 · 할인", span=4)
    kv(33, "적격상장 조항 (1 반영)", int(tm.ipo_on), N0, "", "ipoon")
    kv(34, "적격상장 스텝", R["qi_step"], N0,
       f"계약 {tm.ipo_m:,.0f}개월", "ipos")
    kv(35, "적격 판정 최소 주가 (원)", tm.ipo_min, N2,
       "그 노드 주가가 이 값을 넘으면 상장 성공", "ipomin")
    kv(36, "상장 시 콜도 소멸 (1)", int(tm.sha_qipo_kill), N0, "", "qkill")
    kv(37, "한쪽 행사 시 상대 권리 (0 존속 / 1 소멸)", int(tm.sha_kill), N0,
       ("한 격자에서 함께 푼다 — 행사확률 합이 1 이다" if int(tm.sha_kill)
        else "두 권리를 따로 잰다 — 행사확률 합이 1 을 넘을 수 있다"),
       yellow=False)
    kv(38, "풋 할인 기준", ["무위험", "위험 곡선", "무위험 + 스프레드"][int(tm.sha_disc)],
       None, "선도이자율은 트리 8행에 값으로 들어 있다", yellow=False)
    kv(39, "풋 의무자", ["최대주주", "발행회사", "최대주주 · 발행회사 연대"][int(tm.sha_writer)],
       None, "발행회사면 1032 문단 23 총액 부채", yellow=False)
    kv(40, "투자원금 총액 (원)", tm.face_total, N0, "", "face")
    note(A, 42, "스텝은 계약상 개월을 노드 번호로 옮긴 것이다. 시작은 계약일 "
         "이후 첫 노드, 종료는 그 이전 마지막 노드다 — 계약일 전에 행사가 열리지 "
         "않게 한다.", span=4)

    # ── 트리 시트 ──
    HEAD = ["Date", "time-step", "Flag(풋)", "Flag(콜)", "적격상장 스텝",
            "풋 행사금액", "콜 행사금액", "풋 선도할인율", "무위험 선도이자율",
            "σ", "u", "d", "q", "1−q"]

    def newsheet(name, ttl, memo, refs):
        W = sheet(name, widths=[15] + [9]*(n+1))
        for r, nm in enumerate(HEAD, start=1):
            put(W, r, 2, nm, bold=True, size=8, fill=RPT["tint"], border=True)
        d0 = dt.date.fromisoformat(tm.d_base)
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            g = lambda r, v, fm=None, col=None: put(W, r, 3+i, v, fmt=fm,
                                                    align="center", size=8, color=col)
            stp = f"{L}$2"
            yr = f"({stp}*{K_['dt']}+{K_['elm']}/12)"
            g(1, V(d0 + dt.timedelta(days=round(i*dt_*365)),
                   f"={K_['d_base']}+{stp}*{K_['dt']}*365"), DATE, RPT["grey"])
            g(2, V(i, (0 if i == 0 else f"={Lp}$2+1")), N0)
            _pf = (1 if (st_lo(tm.sha_put_s) <= i <= st_hi(tm.sha_put_e)
                         and (i-st_lo(tm.sha_put_s)) % per_(tm.sha_put_f) == 0) else 0)
            g(3, V(_pf, f"=IF(AND({stp}>={K_['pst']},{stp}<={K_['pen']},"
                        f"MOD({stp}-{K_['pst']},{K_['pfrq']})=0),1,0)"), N0)
            _cf = (1 if (_hascall and st_lo(tm.sha_call_s) <= i <= st_hi(tm.sha_call_e)
                         and (i-st_lo(tm.sha_call_s)) % per_(tm.sha_call_f) == 0) else 0)
            g(4, V(_cf, f"=IF(AND({K_['con']}=1,{stp}>={K_['kst']},{stp}<={K_['ken']},"
                        f"MOD({stp}-{K_['kst']},{K_['kfrq']})=0),1,0)"), N0)
            _qf = 1 if (int(tm.ipo_on) and i == R["qi_step"] and i > 0) else 0
            g(5, V(_qf, f"=IF(AND({K_['ipoon']}=1,{stp}={K_['ipos']},{stp}>0),1,0)"),
              N0, RPT["grey"])
            g(6, V(round(R["pk"](i), 6) if _pf else 0.0,
                   f"=IF({L}$3=1,100*(1+"
                   + xl_prem(K_['pyld'], "0", K_['pcmp'], yr) + "),0)"), N2)
            g(7, V(round(R["ck"](i), 6) if _cf else 0.0,
                   f"=IF({L}$4=1,100*(1+"
                   + xl_prem(K_['cprem'], "0", K_['ccmp'], yr) + "),0)"), N2)
            if i < n:
                g(8, R["pdisc"](i), P2)
                g(9, R["rf"](i), P2)
                g(13, V(R["qi"](i),
                        f"=(EXP({L}$9*{K_['dt']})-{L}$12)/({L}$11-{L}$12)"), N4)
                g(14, V(1-R["qi"](i), f"=1-{L}$13"), N4)
            else:
                g(13, V(R["q"], f"={K_['q']}"), N4)
                g(14, V(1-R["q"], f"={K_['q1']}"), N4)
            g(10, V(tm.sig, f"={K_['sig']}"), P2)
            g(11, V(R["u"], f"={K_['u']}"), N4)
            g(12, V(R["d"], f"={K_['dd']}"), N4)
        put(W, 15, 2, ttl, bold=True, size=11)
        put(W, 16, 2, "r ＼ 스텝", bold=True, size=8, fill=RPT["tint"],
            border=True, align="center")
        for i in range(n+1):
            put(W, 16, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                fill=RPT["tint"], border=True)
        for r in range(n+1):
            put(W, R0+r, 2, r, bold=True, size=8, fmt=N0, align="center",
                fill=RPT["tint"], border=True)
        put(W, R0+n+2, 2, memo, size=9, color=RPT["grey"])
        put(W, R0+n+3, 2, "참조: " + refs, size=9, color=RPT["green"])
        put(W, R0+n+4, 2, "r 은 하락 횟수. 위로 갈수록 주가가 높다."
            + ("  8·9행 선도이자율만 값이다." if formula else ""),
            size=9, color=RPT["grey"])
        W.freeze_panes = "C17"
        return W

    def fill(W, fn, fmt=N2):
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            Ln = gl(4+i) if i < n else None
            for r in range(i+1):
                put(W, R0+r, 3+i, fn(i, r, L, Lp, Ln), fmt=fmt, size=8, align="right")

    W = newsheet(S1, "① 주가트리  S = S0 × u^(스텝−r) × d^r",
                 "맨 위는 직전 열 맨 위 × u, 나머지는 직전 열 한 칸 위 × d.", "가정")
    fill(W, lambda i, r, L, Lp, Ln: V(
        round(R["S"](i, i-r), 4),
        f"={K_['S0']}" if i == 0 else
        (f"={Lp}{R0}*{L}$11" if r == 0 else f"={Lp}{R0+r-1}*{L}$12")))

    W = newsheet(S2, "② 지분가치트리  100 × 주가 ÷ 주당 인수가액",
                 "투자원금 100 과 견줄 수 있게 옮긴 값이다.", f"{S1} · 가정")
    fill(W, lambda i, r, L, Lp, Ln: V(
        round(R["eq"](i, i-r), 6), f"=100*{Q(S1)}!{L}{R0+r}/{K_['K0']}"))

    _QI = lambda L, r: f"AND({L}$5=1,{Q(S1)}!{L}{R0+r}>{K_['ipomin']})"
    _PEX = lambda L, r: f"IF({L}$3=1,MAX({L}$6-{Q(S2)}!{L}{R0+r},0),0)"
    _CEX = lambda L, r: f"IF({L}$4=1,MAX({Q(S2)}!{L}{R0+r}-{L}$7,0),0)"

    # 상호소멸 계약이면 한 노드에서 **누가 먼저 행사하는가**를 정하고, 진 쪽은
    # 그 자리에서 0 이 된다. 엔진 settle() 과 같은 판정을 엑셀로 옮긴다.
    #
    # 엔진의 허용오차가 TOL(1e-9) 이 아니라 1e-12 라 여기서도 같은 값을 쓴다 —
    # 두 곳이 다른 값을 쓰면 조서가 엔진을 못 따라온다.
    _kill = R.get("kill", False)
    _TS = repr(SHA_SETTLE_TOL)
    _pfx = int(tm.pc_order) == 0            # 겹치면 투자자 풋이 먼저인가
    _pc = lambda L, r, Ln: (f"({Q(S3)}!{Ln}{R0+r}*{L}$13+{Q(S3)}!{Ln}{R0+r+1}*{L}$14)"
                            f"*EXP(-{L}$8*{K_['dt']})")
    _cc = lambda L, r, Ln: (f"({Q(S4)}!{Ln}{R0+r}*{L}$13+{Q(S4)}!{Ln}{R0+r+1}*{L}$14)"
                            f"*EXP(-{L}$9*{K_['dt']})")

    def _wins(L, r, Ln):
        """(풋이 이기는 조건, 콜이 이기는 조건). 둘 다면 우선순위가 가른다."""
        pe, ce = _PEX(L, r), _CEX(L, r)
        pc = "0" if Ln is None else _pc(L, r, Ln)
        cc = "0" if Ln is None else _cc(L, r, Ln)
        pw = f"AND({pe}>{_TS},{pe}>={pc}-{_TS})"
        cw = f"AND({ce}>{_TS},{ce}>={cc}-{_TS})"
        if _pfx:  return pw, f"AND({cw},NOT({pw}))"
        return f"AND({pw},NOT({cw}))", cw

    W = newsheet(S3, "③ 투자자 풋가치트리",
                 ("한 노드에서 누가 먼저 행사하는지를 먼저 정한다 — 최대주주가 "
                  "콜을 행사하면 계약이 끝나 **풋이 그 자리에서 소멸**한다 "
                  "(가정 37행). 아무도 행사하지 않으면 다음 열을 **풋 선도할인율**"
                  "(8행)로 할인한다."
                  if _kill else
                  "지금 행사(행사금액 − 지분가치)와 계속 보유 중 큰 쪽. 계속 보유는 "
                  "다음 열을 **풋 선도할인율**(8행)로 할인한다.")
                 + "  적격상장 노드에서는 0 이다 — 시장에서 팔 수 있게 되어 풋이 "
                   "소멸한다.",
                 f"{S2} · 다음 열 {S3}" + (f" · {S4}" if _kill else ""))

    def _pfx_fx(i, r, L, Lp, Ln):
        _cont = "0" if i == n else _pc(L, r, Ln)
        if not _kill:
            body = (_PEX(L, r) if i == n else f"MAX({_PEX(L, r)},{_cont})")
        else:
            pw, cw = _wins(L, r, None if i == n else Ln)
            body = f'IF({pw},{_PEX(L, r)},IF({cw},0,{_cont}))'
        return V(round(R["P"][i][i-r], 6), f"=IF({_QI(L, r)},0,{body})")
    fill(W, _pfx_fx)

    W = newsheet(S4, "④ 최대주주 콜가치트리",
                 ("한 노드에서 투자자가 풋을 행사하면 계약이 끝나 **콜이 그 자리에서 "
                  "소멸**한다 (가정 37행). 아무도 행사하지 않으면 다음 열을 "
                  "**무위험**(9행)으로 할인한다."
                  if _kill else
                  "지금 행사(지분가치 − 행사금액)와 계속 보유 중 큰 쪽. 주식을 받을 "
                  "권리라 **무위험**(9행)으로 할인한다.")
                 + "  적격상장 시 소멸 여부는 가정에서 고른다.",
                 f"{S2} · 다음 열 {S4}" + (f" · {S3}" if _kill else ""))

    def _cfx_fx(i, r, L, Lp, Ln):
        _cont = "0" if i == n else _cc(L, r, Ln)
        if not _kill:
            body = (_CEX(L, r) if i == n else f"MAX({_CEX(L, r)},{_cont})")
        else:
            pw, cw = _wins(L, r, None if i == n else Ln)
            body = f'IF({cw},{_CEX(L, r)},IF({pw},0,{_cont}))'
        return V(round(R["C"][i][i-r], 6),
                 f"=IF(AND({_QI(L, r)},{K_['qkill']}=1),0,{body})")
    fill(W, _cfx_fx)

    # ── 결과 ──
    RS = sheet("결과", widths=[42, 16, 18, 46], tab=RPT["green"])
    head(RS, 2, "평가결과",
         ("모든 값이 앞의 트리에서 수식으로 넘어온다." if formula else
          "앱이 계산한 값을 그대로 담았다."), span=4)
    _g = R["gross"]
    _fp = _g["step"] if _g else 0
    _LF = gl(3+_fp)
    _dfx = "*".join(f"EXP(-{Q(S3)}!{gl(3+i)}$8*{K_['dt']})" for i in range(_fp)) or "1"
    sec(RS, 4, "1. 옵션가치 (투자원금 100 기준)", span=4)
    cols(RS, 5, ["항목", "100 기준", "전액 기준 (원)", "설명"],
         widths=[42, 16, 18, 46])
    _eqv = 100*tm.S0/tm.K0
    _items = [
        ("지분가치 (평가기준일)", _eqv, f"={Q(S2)}!C{R0}",
         "100 × 주가 ÷ 주당 인수가액"),
        ("투자자 풋옵션", R["put"], f"={Q(S3)}!C{R0}",
         "지분을 보장수익률로 되팔 권리"),
        ("최대주주 콜옵션", R["call"], f"={Q(S4)}!C{R0}",
         "지분을 사 갈 권리. 콜이 없으면 0"),
        ("지분 + 풋 − 콜", _eqv + R["put"] - R["call"], "=C6+C7-C8",
         "투자자가 쥔 것의 합. 투자 시점이면 투자원금과 견준다"),
    ]
    for i, (nm, val, fx, memo) in enumerate(_items):
        r = 6+i
        put(RS, r, 2, nm, bold=True, border=True)
        put(RS, r, 3, V(round(val, 6), fx), fmt=N4, align="right", bold=True,
            border=True)
        put(RS, r, 4, V(round(val*tm.face_total/100, 0),
                        f"=C{r}/100*{K_['face']}"), fmt=N0, align="right", border=True)
        put(RS, r, 5, memo, size=9, color=RPT["grey"], wrap=True)

    sec(RS, 11, "2. 발행회사가 풋 의무자일 때 — 1032 문단 23 총액 부채", span=4)
    _rows2 = [
        ("첫 행사 가능일 (스텝)", V(_fp, f"={Q(S3)}!{_LF}$2"), N0,
         "가장 이른 행사 가능 시점"),
        ("그날 행사금액", V(round(_g["strike"], 6) if _g else 0.0,
                       f"={Q(S3)}!{_LF}$6"), N4, "상환금액"),
        ("할인계수", V(round(_g["df"], 8) if _g else 1.0, f"={_dfx}"), N6,
         "격자와 같은 구간 선도이자율로 할인"),
        ("금융부채 (상환금액의 현재가치)", V(round(_g["pv"], 6) if _g else 0.0,
                                "=C13*C14"), N4,
         "옵션 공정가치가 아니라 **총액**이다"),
        ("참고 — 풋옵션 공정가치", V(round(R["put"], 6), "=C7"), N4,
         "최대주주가 의무자면 이쪽을 쓴다"),
    ]
    for i, (nm, fx, fm, memo) in enumerate(_rows2):
        r = 12+i
        put(RS, r, 2, nm, bold=True, border=True)
        put(RS, r, 3, fx, fmt=fm, align="right", bold=True, border=True)
        put(RS, r, 4, memo, size=9, color=RPT["grey"], wrap=True)

    sec(RS, 18, "3. 검산", span=4)
    _LN = gl(3+n)
    _grw = "*".join(f"EXP({Q(S1)}!{gl(3+i)}$9*{K_['dt']})" for i in range(n)) or "1"
    _chk = [
        ("위험중립가중치 q", V(round(R["q"], 6), f"={Q(S1)}!C$13"), N4,
         '=IF(AND(C19>0,C19<1),"적합","확인 필요")'),
        ("풋 − 지금 행사했을 때의 값 (≥ 0)",
         V(round(R["put"] - (max(R["pk"](0) - 100*tm.S0/tm.K0, 0.0)
                             if R["p_on"](0) else 0.0), 6),
           f'=C7-IF({Q(S3)}!C$3=1,MAX({Q(S3)}!C$6-C6,0),0)'), N4,
         '=IF(C20>=-0.000001,"적합","확인 필요")'),
        ("풋·콜이 모두 0 이상",
         V(round(min(R["put"], R["call"]), 6), "=MIN(C7,C8)"), N4,
         '=IF(C21>=-0.000001,"적합","확인 필요")'),
    ]
    for i, (nm, fx, fm, jd) in enumerate(_chk):
        r = 19+i
        put(RS, r, 2, nm, bold=True, border=True)
        put(RS, r, 3, fx, fmt=fm, align="right", border=True)
        put(RS, r, 4, jd, align="center", border=True)
    note(RS, 23, "적격상장을 켜면 풋이 그 노드에서 소멸하므로 값이 뚝 떨어진다. "
         "최소 주가 기준이 계약의 「적격상장」 정의와 맞는지 반드시 확인하라.", span=4)

    # ── 회계처리 ──
    AC = sheet("회계처리", widths=[46, 16, 18, 60], tab=RPT["red"])
    head(AC, 2, "회계처리 — 세 관점",
         "같은 계약인데 실리는 것이 완전히 다르다. 누가 풋 의무자인지가 가른다.",
         span=4)
    acc = sha_accounts(tm, R)
    r = 4
    for who in ("발행회사", "최대주주", "투자자"):
        rows, memo = acc[who]
        sec(AC, r, who, span=4); r += 1
        cols(AC, r, ["항목", "100 기준", "전액 기준 (원)", ""], widths=[46, 16, 18, 60])
        r += 1
        for nm, v in rows:
            put(AC, r, 2, nm, bold=nm.startswith("합계"), border=True)
            put(AC, r, 3, v, fmt=N4, align="right", border=True)
            put(AC, r, 4, v*tm.face_total/100, fmt=N0, align="right", border=True)
            r += 1
        note(AC, r, memo, span=4); r += 2

    write_check_sheets(wb, tm, sha_checks(tm, R), after="회계처리")

    if attach:
        # 산출내역은 뒤로 보낸다. 앞 여섯 장이 조서의 본문이다.
        for nm in _order:
            wb.move_sheet(nm, offset=len(wb.sheetnames))
    polish_wb(wb)
    return _save(wb)


# ══════════════════════════════════════════════════════════
# 6. 화면
# ══════════════════════════════════════════════════════════
st.set_page_config(page_title="복합금융상품 평가 — CB · BW · RCPS", layout="wide")
st.markdown("""<style>
.block-container{padding-top:2.2rem;max-width:1250px}
h1{font-size:1.7rem !important;letter-spacing:-.02em}
[data-testid="stMetricValue"]{font-size:1.9rem}
</style>""", unsafe_allow_html=True)

HLP_CMP = ("0 이면 **단리**입니다 — 「발행가에 연 X% 단리를 가산」 계약이 적지 않습니다. "
           "1 연복리 · 2 반기 · 4 분기.")
# 제목은 상품에 따라 갈리는데 상품은 사이드바에서 정해진다. 사이드바가 아래에서
# 그려지므로 자리만 잡아 두고 값이 정해진 뒤에 채운다 — 그러지 않으면 시나리오를
# 불러온 그 순간의 제목이 한 박자 늦는다.
_HEAD = st.empty()

if "tm" not in st.session_state: st.session_state.tm = Terms()
if "prices" not in st.session_state: st.session_state.prices = []
if "peers" not in st.session_state: st.session_state.peers = []
if "rate_series" not in st.session_state: st.session_state.rate_series = []

with st.sidebar:
    st.subheader("계약조건")

    up = st.file_uploader("시나리오 불러오기", type=["json"], key="scen")
    # 업로더는 지운 뒤에도 같은 파일을 계속 돌려준다. 실행마다 다시 읽으면
    # 그 뒤에 손으로 바꾼 값이 매번 되돌아가므로 한 번만 읽는다.
    _sid = (up.name, up.size) if up is not None else None
    if up is not None and st.session_state.get("scen_id") != _sid:
        try:
            up.seek(0)
            o = json.load(up)
            st.session_state.tm = Terms(**{k: v for k, v in o.items()
                                           if k in Terms.__dataclass_fields__})
            # 이자율 곡선은 아래 텍스트 칸이 실행마다 t.rf_curve·t.cr_curve 를
            # 통째로 덮어쓴다. 시나리오의 곡선을 그 칸에 직접 써 넣지 않으면
            # 화면 기본값(국고채·회사채 예시)으로 계산되어 버린다.
            _tm = st.session_state.tm
            def _fmt(pts):
                return "\n".join(f"{float(x):g}\t{float(y)*100:.4f}%" for x, y in pts)
            if _tm.rf_curve: st.session_state.rf_txt = _fmt(_tm.rf_curve)
            if _tm.cr_curve:
                st.session_state.cr_txt = _fmt(_tm.cr_curve)
                st.session_state.ca_txt = _fmt(_tm.cr_curve)
            if _tm.cr_curve_b: st.session_state.cb_txt = _fmt(_tm.cr_curve_b)
            st.session_state.scen_id = _sid
            st.success("불러왔습니다. 이자율 곡선도 함께 채웠습니다 (만기 단위 = 년).")
            st.rerun()
        except Exception as ex:
            st.error(f"읽지 못했습니다 — {ex}")
    t = st.session_state.tm

    with st.expander("모형", expanded=True):
        _INSTS = ["CB", "BW", "RCPS", "SHA"]
        t.inst = st.selectbox(
            "상품", _INSTS,
            index=_INSTS.index(t.inst if t.inst in _INSTS else "CB"),
            format_func=lambda x: {"CB": "전환사채 (CB) · 액면 100 기준",
                                   "BW": "신주인수권부사채 (BW) · 액면 100 기준",
                                   "RCPS": "상환전환우선주 (RCPS) · 1주 발행가 100 기준",
                                   "SHA": "주주간계약 (SHA) · 투자원금 100 기준"}[x],
            help="격자·이자율·변동성·조서 기계는 같습니다. RCPS 를 고르면 우선배당·"
                 "존속기간 만료 처리·발행자 상환권·발행가 역산이 열리고, 매도청구권의 "
                 "트랜치·제3자 지정·세 평가방법은 닫힙니다. BW 를 고르면 행사대금 "
                 "납입 방식과 신주인수권증권의 분리 여부가 열립니다. SHA 는 사채가 "
                 "없어 화면이 통째로 갈립니다 — 지분에 붙은 풋과 콜만 잽니다.")
        if is_bw(t):
            # 행사대금을 무엇으로 내는가가 격자를 가른다. 대용납입이면 사채가
            # 소멸해 전환사채와 같아지고, 현금납입이면 사채가 남아 따로 잰다.
            t.bw_pay = st.selectbox(
                "신주인수권 행사대금", [0, 1], index=int(t.bw_pay),
                format_func=lambda i: ["현금납입 — 현금을 내고 사채는 남는다",
                                       "사채 대용납입 — 사채를 권면액만큼 납입에 갈음"][i],
                help="대용납입이면 사채가 소멸하고 주식을 받으므로 전환사채와 "
                     "수학적으로 같습니다. 현금납입이면 사채와 신주인수권을 따로 "
                     "재어 더합니다.")
            if int(t.bw_pay) == 0:
                t.bw_detach = st.selectbox(
                    "신주인수권증권", [0, 1], index=int(t.bw_detach),
                    format_func=lambda i: ["비분리형 — 사채가 소멸하면 함께 소멸",
                                           "분리형 — 사채와 따로 유통"][i],
                    help="분리형이면 사채를 조기상환받아도 신주인수권이 행사기간 "
                         "끝까지 남습니다. 비분리형이면 사채가 소멸할 때 미행사분이 "
                         "함께 사라집니다 — 상환 직전에 행사할 기회는 있습니다.")
        L = lbl(t)
        if is_rcps(t):
            st.caption("모든 금액은 **1주 발행가 = 100** 기준입니다. 전환가치는 "
                       "100 × 주가 ÷ 전환가격 — 전환가격이 발행가와 같으면 1 : 1 전환입니다.")
        if bw_cash(t):
            st.caption("현금납입형이라 **사채 + 신주인수권**으로 나누어 잽니다. "
                       "신주인수권 행사가치는 100 × 주가 ÷ 행사가격 − 100 — "
                       "권면액 100 만큼 현금을 내고 그 값어치 주식을 받습니다. "
                       "지분과 부채가 애초에 갈라져 있어 TF 와 GS 가 같은 값을 냅니다.")
        if is_sha(t):
            # 사채가 없어 신용위험을 값에 쪼개 넣을 자리가 없고, 리픽싱도 없다.
            # 전환권 자체가 없으므로 회계 분류도 여기서 정하지 않는다.
            st.caption("주주간계약은 사채가 없어 **신용위험 처리(TF·GS)** 와 "
                       "**리픽싱 처리** 칸이 없습니다. 풋의 신용위험은 아래 "
                       "「의무자 · 할인율」 에서 정합니다.")
        else:
          t.model = st.selectbox("신용위험 처리", ["TF", "GS"],
                               index=0 if t.model == "TF" else 1,
                               format_func=lambda x: "TF · 값을 쪼갠다" if x == "TF" else "GS · 할인율을 섞는다")
          t.carry = st.selectbox("조정일 아닌 시점", [0, 1, 2, 3], index=t.carry,
                               format_func=lambda i: ["상태확장 (정확)", "경로가중치",
                                                      "확률가중평균", "특정노드선택"][i])
          t.conv_class = st.selectbox(inst_text(t, "전환권 회계 분류"),
                                    ["equity", "liability"],
                                    index=0 if t.conv_class == "equity" else 1,
                                    format_func=lambda x: inst_text(
                                        t, "자본 · 전환권대가를 잔여로") if x == "equity"
                                    else "파생상품부채 · 주계약을 잔여로")
          st.caption("분류에 따라 무엇을 공정가치로 재고 무엇을 잔여로 두는지가 뒤바뀝니다.")

    with st.expander("날짜 · 기간", expanded=True):
        c1, c2 = st.columns(2)
        t.d_issue = c1.date_input("발행일", value=dt.date.fromisoformat(t.d_issue)).isoformat()
        t.d_mat = c2.date_input("만기일", value=dt.date.fromisoformat(t.d_mat)).isoformat()
        t.d_base = st.date_input("평가기준일", value=dt.date.fromisoformat(t.d_base),
                                 help="발행일과 같으면 최초 인식, 뒤면 후속 측정입니다.").isoformat()
        gaps = {"월": 1.0, "2주": 12/26, "주": 12/52}
        gname = min(gaps, key=lambda k: abs(gaps[k]-t.gap_m))
        gname = st.selectbox("노드 간격", list(gaps), index=list(gaps).index(gname))
        t.gap_m = gaps[gname]
        derive(t)
        c3, c4, c5 = st.columns(3)
        c3.metric("경과", f"{t.elapsed_m:.1f}개월")
        c4.metric("잔존", f"{t.T:.2f}년")
        c5.metric("노드", f"{t.n}")
        st.caption("행사 시점은 아래에서 **발행일 기준 개월**로 넣으십시오. "
                   "앱이 평가기준일 기준으로 옮기고, 행사금액도 발행일부터 복리로 붙입니다.")

    with st.expander("기본", expanded=True):
        _SHA = is_sha(t)
        # 상장사면 평가기준일(또는 직전 거래일) 종가를 받아 넣는다. 비상장이면 빈칸으로 두고
        # 손으로 넣거나 아래 역산을 쓴다. 출처는 조서 가정 시트에 같이 실린다.
        tk1, tk2, tk3 = st.columns([2, 1, 2])
        t.ticker = tk1.text_input("종목코드 · 티커", value=t.ticker,
                                  help="국내는 6자리 숫자, 해외는 티커. 비상장이면 비워 두십시오.").strip()
        _mkt = tk2.selectbox("시장", ["KQ", "KS", ""], index=0,
                             format_func=lambda x: {"KQ": "코스닥", "KS": "코스피", "": "해외"}[x],
                             key="s0_mkt")
        if tk3.button("평가기준일 종가 불러오기", use_container_width=True,
                      disabled=not t.ticker, key="btn_s0"):
            with st.spinner("받는 중"):
                try:
                    _dd, _px, _sym = fetch_close(t.ticker, _mkt, t.d_base)
                    t.S0 = float(_px)
                    t.s0_src = f"야후 {_sym} {_dd} 종가"
                    st.rerun()
                except Exception as ex:
                    st.warning(f"받지 못했습니다 — {ex}. 주가를 직접 넣으십시오.")
        _s0_before = float(t.S0)
        t.S0 = st.number_input("평가기준일 주가 (원)", value=float(t.S0), step=1.0,
                               help=("비상장이면 지분가치 평가액 ÷ 주식수를 넣거나, 아래에서 "
                                     "투자원금으로 역산하십시오." if _SHA else
                                     "비상장이면 별도 지분평가액 ÷ 주식수를 넣거나, 아래에서 "
                                     "발행가로 역산하십시오."))
        if abs(t.S0 - _s0_before) > 1e-9:
            t.s0_src = ""                      # 손으로 고쳤다 — 출처는 더 이상 야후가 아니다
        if t.s0_src:
            st.caption(f"출처 · {t.s0_src}")
            if t.s0_src.split()[-2] != t.d_base:
                st.caption(f"평가기준일 {t.d_base} 은 휴장일이라 직전 거래일 종가입니다.")
        # 발행가 역산 (책 5-1). 비상장 발행회사는 관측 주가가 없으니 「발행된 값이
        # 곧 공정가치」로 놓고 전체 가치가 발행가가 되는 주가를 격자에서 찾는다.
        bc1, bc2 = st.columns([1, 1])
        t.bs_target = bc1.number_input(f"역산 목표 ({L['face']} 100 기준)",
                                       value=float(t.bs_target), step=1.0,
                                       help="발행 시점 평가면 100. 할인·할증 발행이면 그 값.")
        # 무엇을 목표에 맞출 것인가. 매도청구권은 격자 밖에서 따로 재어 차감하는
        # 파생상품자산이라, 본체만 맞추면 투자자가 실제로 받은 순액은 목표에
        # 못 미친다 — 돈을 내면서 매도청구권까지 써 주었기 때문이다.
        if not _SHA and t.k_w > 0:
            t.bs_net = int(st.selectbox(
                "역산 목표를 무엇에 맞추나", [0, 1], index=int(t.bs_net),
                format_func=lambda x: (
                    f"본체 — {L['call']}을 빼기 전 값" if x == 0
                    else f"순액 — 본체 − {L['call']}"),
                help="발행가가 패키지 전체의 대가라면 「순액」 입니다. 투자자는 돈을 "
                     "내면서 매도청구권까지 써 주었으므로, 실제로 받은 것은 그 차감 "
                     "후 순액이기 때문입니다. 매도청구권에 별도 대가가 오갔거나 "
                     "최초인식 차이를 따로 보고 있다면 「본체」 입니다. 매도청구권이 "
                     "클수록 두 답이 벌어집니다."))
        if bc2.button(("투자원금으로 지분 역산" if _SHA else "발행가로 주가 역산"),
                      use_container_width=True,
                      help=("«지분가치 + 풋 − 콜» 이 목표와 같아지는 주가를 이분법으로 "
                            "찾아 위 칸에 넣습니다." if _SHA else
                            "전체 가치(B2, 발행자 상환권이 있으면 B3)가 목표와 같아지는 "
                            "주가를 이분법으로 찾아 위 칸에 넣습니다 (책 [사례 5-1])。")):
            with st.spinner("격자를 되풀이 계산합니다"):
                _S, _v, _k = (sha_backsolve(t, t.bs_target) if _SHA else backsolve(t))
            if _k < 0 and _SHA:
                st.error(f"격자가 목표에 닿지 않습니다 (주가 {_S:,.0f}원에서 {_v:,.2f}). "
                         "**풋 자체가 투자원금보다 클 수 있습니다** — 보장수익률이 붙은 "
                         "풋은 주가가 아무리 낮아도 행사금액의 현재가치만큼 값이 남기 "
                         "때문입니다. 그때는 역산이 성립하지 않으므로 지분가치를 직접 "
                         "넣으시고, 풋 할인율에 의무자의 신용을 반영하셨는지 보십시오.")
            elif _k < 0:
                st.error(f"격자가 목표에 닿지 않습니다 (주가 {_S:,.0f}원에서 {_v:,.2f}). "
                         "전환가액·변동성·상환 조건을 확인하십시오.")
            else:
                t.S0 = float(_S)
                # 격자 값은 주가에 대해 연속이 아니다. 어느 노드의 결정이
                # 뒤집히는 자리에서 계단처럼 튀므로 이분법이 목표를 정확히
                # 맞히지 못할 수 있다. 얼마나 못 맞혔는지는 말해 주어야 한다.
                if abs(_v - t.bs_target) > 0.05:
                    st.warning(
                        f"역산이 목표 {t.bs_target:,.2f} 에 **{_v:,.4f}** 로 "
                        f"멈췄습니다 (차이 {_v - t.bs_target:+,.4f}). 격자 값은 "
                        "주가에 대해 연속이 아니라 어느 노드의 결정이 뒤집히는 "
                        "자리에서 계단처럼 뜁니다 — 이분법이 그 계단을 넘을 수 "
                        "없어서 생기는 한계이지 오류가 아닙니다. 노드를 촘촘히 "
                        "하면 계단이 잘아집니다.")
                _tg = ("순액" if (not _SHA and t.k_w > 0 and int(t.bs_net))
                       else "본체")
                st.session_state.px_src = (
                    f"발행가 역산 (목표 {t.bs_target:,.2f})" if _SHA else
                    f"발행가 역산 (목표 {t.bs_target:,.2f} · {_tg} 기준)")
                st.rerun()
        if (st.session_state.get("px_src") or "").startswith("발행가 역산"):
            st.success(f"주가 {t.S0:,.2f}원 — {st.session_state.px_src}. 조서에 "
                       "그대로 적힙니다. 기말 재평가에는 쓰지 마십시오 — 그때는 "
                       "발행가가 기준이 아닙니다.")
        t.K0 = st.number_input(
            ("주당 인수가액 (원)" if _SHA else inst_text(t, "현재 전환가액 (원)")),
            value=float(t.K0), step=1.0,
            help=("투자자가 1주에 낸 금액입니다. 지분가치 = 100 × 주가 ÷ 이 값 이라, "
                  "평가기준일 주가가 이 값과 같으면 지분가치가 100 입니다." if _SHA else
                  "리픽싱이 이미 일어났으면 조정된 값을 넣으십시오."))
        if _SHA:
            t.face_total = st.number_input(
                "투자원금 총액 (원)", value=float(t.face_total), step=1e8,
                format="%.0f", help="화면과 조서의 전액 기준 금액을 계산합니다.")
            st.caption("주주간계약에는 사채가 없어 표면이자·만기상환금액·거래원가 칸이 "
                       "없습니다. 아래 「주주간계약」 칸에서 풋과 콜을 넣으십시오.")
        # 주주간계약에는 사채가 없다. 표면이자·만기상환·거래원가 칸을 뺀다.
        if not _SHA:
            t.cpn = st.number_input(f"{L['cpn']} (%)", value=t.cpn*100, step=0.5,
                                    help=("확정 배당률을 표면이자처럼 현금흐름으로 봅니다. "
                                          "배당가능이익이 없어 지급 가능성이 없다고 보시면 0."
                                          if is_rcps(t) else None))/100
            t.ipay = st.number_input(f"{L['ipay']} (개월)", value=float(t.ipay), step=1.0)
            if is_rcps(t):
                t.div_mode = st.selectbox(
                    "우선배당의 성격", [0, 1], index=int(t.div_mode),
                    format_func=lambda x: ("미지급분을 상환가액에 가산 — 전체 부채 · 배당은 이자비용"
                                           if x == 0 else
                                           "발행자 재량 · 상환가액과 무관 — 부채 현금흐름에서 제외"),
                    help="기준서 1032 AG37. 지급되지 않은 배당을 상환금액에 가산하면 금융상품 "
                         "전체가 부채이고 배당은 이자비용입니다 (보장수익률 − 배당률 산식이 이 "
                         "경우). 배당이 발행자 재량이고 상환가액과 무관하면 배당은 자본요소의 "
                         "이익분배라 부채 계산에서 뺍니다.")
                if t.div_mode == 1 and t.cpn > 0:
                    st.caption(f"배당률 {t.cpn:.2%} 는 조서에 계약 조건으로 남고, 격자와 상환가액 "
                               "산식에는 **0** 으로 들어갑니다. 회계처리에서 배당은 이익잉여금의 "
                               "처분입니다.")
                t.mat_mode = st.selectbox(
                    "존속기간 만료 시", [0, 1], index=int(t.mat_mode),
                    format_func=lambda x: ("보통주로 자동전환" if x == 0
                                           else f"{L['face']}(+보장수익률)로 상환"),
                    help="상법상 우선주 존속기간이 끝나면 보통주가 되는 계약이 표준입니다. "
                         "만료 시 현금 상환을 받는 계약이면 두 번째를 고르십시오.")
                if t.mat_mode == 0:
                    st.caption("자동전환은 **전환권이 있는 격자**에서만 탑니다. 전환권을 뺀 "
                               "부채요소는 보통주가 될 수 없으니 아래 보장수익률로 상환받는 "
                               "것으로 잽니다 — 그래서 이 값이 여전히 필요합니다.")
            t.ytm = st.number_input(f"{L['ytm']} (%)", value=t.ytm*100, step=0.1, format="%.4f")/100
            t.ytm_cmp = int(st.number_input("보장 복리 횟수 (연)", value=int(t.ytm_cmp),
                                            step=1, min_value=0, max_value=12,
                                            help="공시 상환율이 분기복리면 4, 반기면 2. " + HLP_CMP))
            t.face_total = st.number_input(
                ("발행총액 (원)" if is_rcps(t) else "전자등록총액 (원)"),
                value=float(t.face_total), step=1e8, format="%.0f",
                help="회계처리 탭의 전액 기준 금액을 계산합니다.")
            t.issue_cost = st.number_input(
                "발행 거래원가 (원)", value=float(t.issue_cost), step=1e6, min_value=0.0,
                format="%.0f",
                help="주관수수료·등록비 등. 기업회계기준서 제1032호 문단 38 에 따라 "
                     "배분된 발행금액에 비례하여 요소별로 나눕니다. 부채요소 몫은 "
                     "부채에서 차감해 유효이자율에 녹이고, 파생상품부채 몫은 당기손익-"
                     "공정가치라 즉시 비용, 자본요소 몫은 자본에서 직접 뺍니다.")
            st.caption(f"{L['red']} = {100*(1+accrue_rate(t.T+t.elapsed_m/12, t.ytm, eff_cpn(t), t.ytm_cmp)):,.4f}   "
                       + ("계약서의 상환가액 산식과 대조하십시오." if is_rcps(t)
                          else "공시 만기상환율과 대조하십시오."))

    # 주주간계약에는 사채가 없다. 전환·조기상환·매도청구·상각 관련 칸은 뜻이
    # 없으므로 통째로 빼고, 대신 아래 「주주간계약」 칸을 연다.
    if not is_sha(t):
        with st.expander(inst_text(t, "전환 · 조정")):
            st.caption("모두 **발행일 기준 개월**입니다. 계약서 그대로 넣으십시오.")
            t.cv_s = st.number_input(inst_text(t, "전환 시작 (개월)"),
                                     value=float(t.cv_s), step=1.0)
            t.cv_e = st.number_input(inst_text(t, "전환 종료 (개월)"),
                                     value=float(t.cv_e), step=1.0)
            t.rfx_mode = st.selectbox("조정 방식", [2, 1, 0], index=[2, 1, 0].index(t.rfx_mode),
                                      format_func=lambda i: ["조정 없음", "하향만", "하향 + 상향"][i])
            t.rfx_cyc = st.number_input("조정 주기 (개월)", value=float(t.rfx_cyc), step=1.0)
            t.floor = st.number_input("최저 조정가액 (원)", value=float(t.floor), step=1.0)
            # 상향 재조정의 상한은 계약상 **최초** 전환가액이다. 현재 전환가액으로
            # 상한을 겸하면 이미 하향된 상품이 계약상 회복 한도까지 못 올라간다.
            _cap_on = st.checkbox(
                "최초 전환가액이 현재 전환가액과 다르다 (이미 조정되었다)",
                value=(t.K_cap > 0), key="capon",
                help="계약은 「조정 후 전환가액은 최초 전환가액을 초과할 수 없다」고 "
                     "정합니다. 하향 조정된 뒤에 평가한다면 상향 재조정의 상한은 "
                     "여전히 최초 전환가액입니다.")
            if _cap_on:
                t.K_cap = st.number_input(
                    "최초 전환가액 (원) — 상향 조정 상한", value=float(k_cap(t)),
                    step=1.0, min_value=0.0)
                if t.K_cap < t.K0:
                    st.warning("상한이 현재 전환가액보다 낮습니다. 계약서를 다시 "
                               "확인하십시오.")
            else:
                t.K_cap = -1.0
                st.caption(f"상향 조정 상한을 현재 전환가액 {t.K0:,.0f}원으로 둡니다.")
            t.par = st.number_input(("액면가 (원)" if not is_rcps(t) else "액면가 (원) — 조정 하한"),
                                    value=float(t.par), step=100.0)
            if is_rcps(t):
                st.divider()
                st.markdown("**IPO 조항**")
                t.ipo_on = int(st.checkbox(
                    "상장 조항을 격자에 넣는다", value=bool(t.ipo_on),
                    help="국내 RCPS 계약에 거의 빠짐없이 들어갑니다 — 상장 시 보통주 "
                         "자동전환과 공모가 연동 전환가격 조정. 책 [사례 5-5] 의 산식을 씁니다."))
                if t.ipo_on:
                    t.ipo_m = st.number_input("예상 상장 시점 (개월)", value=float(t.ipo_m),
                                              step=1.0, min_value=1.0,
                                              help="발행일 기준입니다. **가정**이므로 여러 시점을 "
                                                   "돌려 조서에 나란히 싣는 편이 정직합니다.")
                    t.ipo_px = st.number_input("공모가액 (원)", value=float(t.ipo_px), step=100.0,
                                               min_value=0.0)
                    t.ipo_mult = st.number_input("공모가 배수 (%)", value=t.ipo_mult*100,
                                                 step=5.0, min_value=1.0)/100
                    t.ipo_min = st.number_input("최소공모가격 (원)", value=float(t.ipo_min),
                                                step=100.0, min_value=0.0,
                                                help="그 시점 주가가 이 값에 못 미치면 **상장 자체가 "
                                                     "무산**된 것으로 봅니다. 격자가 노드마다 주가를 "
                                                     "가지고 있으므로 상장 확률을 따로 넣지 않습니다.")
                    t.ipo_conv = int(st.checkbox("상장하면 보통주로 강제전환", value=bool(t.ipo_conv),
                                                 help="상장 요건상 우선주를 남겨 둘 수 없어 대부분 "
                                                      "강제전환입니다. 그 자리에서 주식으로 끝나므로 "
                                                      "상환청구권도 발행자 상환권도 함께 사라집니다."))
                    if t.ipo_px > 0:
                        st.caption(f"조정후 전환가격 = {t.ipo_px:,.0f} × {t.ipo_mult:.0%} = "
                                   f"**{t.ipo_px*t.ipo_mult:,.0f}원** (지금 전환가액 {t.K0:,.0f}원보다 "
                                   + ("낮아 조정됩니다" if t.ipo_px*t.ipo_mult < t.K0 else "높아 조정되지 않습니다")
                                   + "). 최저 조정가액·액면가 하한이 그대로 걸립니다.")
                    else:
                        st.warning("공모가액이 0 이라 IPO 조항이 작동하지 않습니다.")

        with st.expander(L["put"]):
            t.p_s = st.number_input("시작 (개월)", value=float(t.p_s), step=1.0, key="ps")
            t.p_e = st.number_input("종료 (개월)", value=float(t.p_e), step=1.0, key="pe")
            t.p_f = st.number_input("주기 (개월)", value=float(t.p_f), step=1.0, key="pf")
            t.p_mode = st.selectbox("행사금액 산정", ["fixed", "accrue"],
                                    index=0 if t.p_mode == "fixed" else 1,
                                    format_func=lambda x: "고정률" if x == "fixed" else "보장수익률 복리")
            if t.p_mode == "fixed":
                t.p_rate = st.number_input("행사금액 (%)", value=float(t.p_rate), step=1.0)
            else:
                t.p_yield = st.number_input(f"{'상환' if is_rcps(t) else '조기상환'} 보장수익률 (%)",
                                            value=t.p_yield*100, step=0.5)/100
                t.p_cmp = int(st.number_input("복리 횟수 (연)", value=int(t.p_cmp), step=1,
                                              min_value=0, help=HLP_CMP))
                st.caption("행사금액 = 100 × (1 + 실효수익률)^경과연수")

            st.divider()
            st.markdown("**회계 처리**")
            _psok = (t.conv_class == "equity" and t.k_sep != 0)
            t.p_sep = 1 if st.selectbox(
                inst_text(t, "조기상환권 처리"), [1, 0], index=0 if int(t.p_sep) else 1,
                format_func=lambda x: ("분리 · 파생상품부채" if x
                                       else "분리하지 않음 · 부채요소에 포함"),
                disabled=not _psok,
                help="행사금액이 상각후원가와 거의 같으면 주채무계약과 밀접하게 "
                     "관련되어 분리하지 않습니다 (기준서 1109 문단 B4.3.5(5)(가)). "
                     "「분리 판단」 탭이 계약 조항으로 이 결론을 내 줍니다.") else 0
            if not _psok:
                st.caption(inst_text(t, COMPAT_PSEP))
                t.p_sep = 1
            elif int(t.p_sep) == 0:
                st.caption("부채요소(사채 + 조기상환권)를 통째로 상각후원가로 둡니다. "
                           "파생상품부채를 세우지 않고, 상각표도 부채요소에서 "
                           "출발합니다. 전환권대가는 어느 쪽이든 같습니다.")

            st.divider()
            st.markdown("**평가 방법**")
            _ok = (t.conv_class == "equity" and t.model == "TF" and t.p_s <= t.p_e)
            t.put_bdt = int(st.checkbox(
                "BDT 금리격자로 평가", value=bool(t.put_bdt), disabled=not _ok,
                help="전환을 끄면 격자가 주가와 무관해져 조기상환권이 확정 계산이 "
                     "됩니다. 금리를 확률변수로 두면 옵션의 시간가치가 생깁니다."))
            if not _ok:
                st.caption(COMPAT_BDT)
                if t.conv_class != "equity" or t.model != "TF": t.put_bdt = 0
            elif t.put_bdt:
                # 키를 두지 않는다. 키가 있으면 위젯이 저장해 둔 값이 value 를
                # 이겨서, 아래 「이 변동성 적용」 도 시나리오 불러오기도 화면에
                # 반영되지 않는다. 주가 변동성 칸도 같은 이유로 키가 없다.
                t.bdt_sig = st.number_input("단기이자율 변동성 (%)",
                                            value=t.bdt_sig*100, step=1.0,
                                            min_value=0.0)/100
                t.bdt_base = st.selectbox(
                    "기준 곡선", [0, 1], index=int(t.bdt_base),
                    format_func=lambda x: ("위험 곡선에 직접" if x == 0
                                           else "무위험 + 확정 스프레드"))
                if t.bdt_base == 0:
                    st.caption("단기이자율이 곧 위험이자율입니다. 변동성이 신용스프레드 "
                               "변동까지 안고 갑니다. 옵션 없는 사채가 격자의 주계약과 "
                               "정확히 같아져 검산이 쉽습니다.")
                else:
                    st.caption("국고채에 변동성을 태우고 구간 선도 스프레드를 확정으로 "
                               "얹습니다. 변동성을 국고채에서 관측한 값으로 쓸 수 있지만, "
                               "**스프레드가 금리와 무관하다고 본 것**이므로 그 한계를 "
                               "조서에 적으십시오.")
                st.caption("로그정규 변동성입니다. 실무에서는 10~30% 를 씁니다. "
                           "0 이면 지금과 같은 값이 나옵니다.")

                st.markdown("**시계열로 σ 산출**")
                st.caption("할인율은 이미 등급보간 → 만기보간을 거칩니다. 변동성도 같은 "
                           "자료·같은 보간에서 나와야 조서가 하나로 이어집니다.")
                # 위험 곡선에서 고른 방식을 그대로 첫 값으로 둔다. 사용자가 여기서
                # 따로 고르면 그 선택이 남는다 (위젯 키가 있으므로 한 번만 정한다).
                if "rvmode" not in st.session_state:
                    st.session_state.rvmode = {"pick": "pick", "rating": "blend"}.get(
                        t.rate_mode, "single")
                # 위험 곡선과 같은 세 갈래다. 이름도 같게 두어야 조서에서 「어느
                # 등급 시계열을 썼나」가 곡선 쪽과 한눈에 대조된다.
                _RVM = ["pick", "single", "blend"]
                _RVNM = {"pick": "표에서 등급 하나 고르기",
                         "single": "일자 · 금리 두 열 파일",
                         "blend": "두 등급 곡선으로 보간"}
                _rmode = st.radio("자료", _RVM, key="rvmode",
                                  format_func=lambda x: _RVNM[x],
                                  help="위험 곡선을 **표에서 등급 하나**로 고르셨으면 "
                                       "여기도 같은 등급을 고르십시오. 곡선과 변동성이 "
                                       "다른 등급에서 나오면 조서가 갈라집니다.")
                _rt = int(st.number_input("연 거래일수", value=250, step=5,
                                          min_value=30, key="rvtd"))
                _rdrop = st.checkbox("이상치 제거 (MAD 2.5배)", value=True, key="rvdrop")
                _ser, _how = None, ""
                _RVX = ["xls", "xlsx", "xlsm", "csv", "txt", "tsv"]
                if _rmode == "single":
                    _f1 = st.file_uploader("금리 시계열 (일자 · 금리)", type=_RVX,
                                           key="rv1")
                    if _f1 is not None:
                        try:
                            _ser = parse_prices(read_upload(_f1.name, _f1.getvalue()))
                            _how = f"{_f1.name} · 단일 시계열"
                        except Exception as ex:
                            st.error(str(ex))
                else:
                    st.caption("첫 열이 일자이고 머리 줄에 **등급과 만기**가 있으면 "
                               "한 파일에 등급이 여럿이어도 갈라 읽습니다. 등급을 "
                               "따로 받으셨으면 두 번째 칸에 넣으십시오.")
                    _fa = st.file_uploader("금리 시계열", type=_RVX, key="rva")
                    _fb = st.file_uploader("두 번째 파일 (선택)", type=_RVX, key="rvb")
                    _pool, _tens, _seen = {}, {}, False
                    for _f in (_fa, _fb):
                        if _f is None: continue
                        _seen = True
                        try:
                            _c, _r = parse_rate_panel(read_upload(_f.name, _f.getvalue()))
                            _s2, _t2 = panel_series(_c, _r, t.T)
                            _pool.update(_s2); _tens.update(_t2)
                        except Exception as ex:
                            st.error(f"{_f.name} — {ex}")
                    if _seen and not _pool:
                        st.error("등급이나 만기를 찾지 못했습니다. 머리 줄에 "
                                 "`… / BBB0` 같은 등급이나 `5년` 같은 만기가 "
                                 "있어야 합니다. 자료는 10줄 이상이어야 합니다.")
                    elif _pool:
                        # 고를 수 있는 것은 **파일에 실제로 있는 등급**뿐이다.
                        # 고시표에 없는 등급을 곡선으로 고르면 붙일 자료가 없다.
                        _opt = sorted(_pool, key=lambda r: (r is None, rating_idx(r)))
                        _nm = lambda r: (r or "등급 미상") + (
                            f" ({'·'.join(f'{x:g}년' for x in _tens.get(r, []))})"
                            if _tens.get(r) else "")
                        st.success("찾은 등급 — " + " · ".join(_nm(r) for r in _opt))
                        if _rmode == "pick":
                            # 표의 한 등급을 그대로 쓴다. 보간이 없으니 조서에
                            # 적을 것도 「그 등급 · 고정만기」 한 줄뿐이다.
                            _dg = t.rt_tgt if t.rt_tgt in _opt else _opt[0]
                            _rp = st.selectbox("σ 를 뽑을 등급", _opt,
                                               index=_opt.index(_dg),
                                               format_func=_nm, key="rvgp")
                            _ser = _pool[_rp]
                            _how = f"{_nm(_rp)} 단일 · 고정만기 {t.T:.2f}년"
                            if t.rt_tgt and _rp and _rp != t.rt_tgt:
                                st.warning(f"위험 곡선의 평가대상은 **{t.rt_tgt}** 인데 "
                                           f"σ 는 **{_rp}** 에서 뽑고 있습니다. 등급이 "
                                           "다르면 조서에 그 이유를 적으십시오 — 인접 "
                                           "등급끼리는 변동성이 거의 같아 실무에서 "
                                           "흔히 하는 대체이지만, 근거는 남아야 합니다.")
                        else:
                            g1, g2 = st.columns(2)
                            _ia = _opt.index(t.rt_a) if t.rt_a in _opt else 0
                            _ra = g1.selectbox("곡선 A", _opt, index=_ia,
                                               format_func=_nm, key="rvga")
                            _rest = [x for x in _opt if x != _ra]
                            _ib = (_rest.index(t.rt_b) + 1) if t.rt_b in _rest else 0
                            _rb = g2.selectbox("곡선 B", [None] + _rest, index=_ib,
                                               format_func=lambda r: "(없음)" if r is None
                                               else _nm(r), key="rvgb")
                            _rtg = st.selectbox(
                                "평가대상", RATINGS, key="rvgt",
                                index=(RATINGS.index(t.rt_tgt)
                                       if t.rt_tgt in RATINGS else 8),
                                help="두 곡선 사이면 내삽, 밖이면 같은 기울기로 외삽합니다.")
                            if _rb is None or _ra is None:
                                _ser = _pool[_ra]
                                _how = f"{_nm(_ra)} 단일 · 고정만기 {t.T:.2f}년"
                                st.info("곡선 B 가 없어 보간 없이 **곡선 A** 를 "
                                        "그대로 씁니다. 처음부터 등급 하나만 쓰실 "
                                        "거라면 위에서 **표에서 등급 하나 고르기** 를 "
                                        "고르시는 편이 짧습니다.")
                            else:
                                _ser = blend_series(_pool[_ra], _pool[_rb],
                                                    _ra, _rb, _rtg)
                                _ja, _jb = rating_idx(_ra), rating_idx(_rb)
                                _w = (((rating_idx(_rtg)-_ja)/(_jb-_ja))
                                      if _ja != _jb else 0.0)
                                _how = (f"{_ra}·{_rb} → {_rtg} (가중치 {_w:.2f}) · "
                                        f"고정만기 {t.T:.2f}년")
                                st.caption(f"가중치 — {_ra} {1-_w:.0%} · "
                                           f"{_rb} {_w:.0%}")
                                if not 0 <= _w <= 1:
                                    st.warning(
                                        f"평가대상 **{_rtg}** 가 두 곡선 **밖**입니다 "
                                        f"(가중치 {_w:.2f}). 등급 간 스프레드는 아래로 "
                                        "갈수록 가속해서 벌어지므로, 직선으로 뻗는 "
                                        "외삽은 금리를 낮게 잡습니다. 평가대상을 "
                                        "사이에 끼우는 등급을 받아 오시는 편이 "
                                        "낫습니다.")
                        _tn = _tens.get(_rp if _rmode == "pick" else _ra) or []
                        if len(_tn) == 1 and abs(_tn[0] - t.T) > 0.5:
                            st.warning(f"파일의 만기가 **{_tn[0]:g}년** 한 열뿐인데 "
                                       f"잔존만기는 **{t.T:.2f}년** 입니다. 만기 보간을 "
                                       "할 수 없으므로 그 차이를 조서에 적으십시오.")
                if _ser and len(_ser) >= 10:
                    _v = rate_vol(_ser, _rt, _rdrop)
                    if _v:
                        st.session_state.rate_series = _ser
                        st.session_state.rate_how = _how
                        st.session_state.rate_opt = dict(tdays=_rt, drop=_rdrop)
                        st.metric("상대 변동성 (BDT 의 σ)", f"{_v['annual']*100:.2f}%",
                                  f"절대 {_v['abs_annual']:.3f}%p")
                        st.caption(f"평균 금리 {_v['mean']:.3f}% · 관측 {_v['n']}개 · "
                                   f"{_ser[0][0]} ~ {_ser[-1][0]}"
                                   + (f" · 이상치 {_v['removed']}개 제거" if _v['removed'] else "")
                                   + f"  ·  절대 ÷ 평균 = {_v['abs_annual']/max(_v['mean'],1e-9)*100:.2f}%")
                        if _v["neg"]:
                            st.error(f"0 이하인 금리가 {_v['neg']}개 있습니다. 로그를 쓸 수 "
                                     "없어 그 구간이 빠집니다.")
                        if _v["min"] < 1.0:
                            st.warning(f"최저 금리가 {_v['min']:.3f}% 입니다. 저금리 구간에서는 "
                                       "작은 변동도 로그로는 크게 잡혀 σ 가 부풀어 오릅니다.")
                        _exp = 0 if t.bdt_base else 1
                        st.caption("기준 곡선이 **"
                                   + ("무위험 + 확정 스프레드" if t.bdt_base else "위험 곡선 직접")
                                   + "** 이므로 "
                                   + ("국고채" if t.bdt_base else "회사채(평가대상 등급)")
                                   + " 시계열을 쓰셔야 맞습니다.")
                        # 시계열의 금리 수준과 격자가 쓰는 곡선의 수준을 견준다.
                        # 8% 회사채로 변동성을 재고 2.5% 공사채 곡선에 태우면
                        # 서로 다른 채권을 섞은 것이다.
                        try:
                            _RFc, _CRc = curves(t)
                            _lvl = (_RFc(t.T) if t.bdt_base else _CRc(t.T))*100
                            _nmc = "무위험 곡선" if t.bdt_base else "위험 곡선"
                            if _lvl > 0.05 and abs(_v["mean"] - _lvl)/_lvl > 0.5:
                                st.error(
                                    f"시계열의 평균 금리는 **{_v['mean']:.2f}%** 인데 "
                                    f"격자가 쓰는 {_nmc} 은 잔존 {t.T:.2f}년에서 "
                                    f"**{_lvl:.2f}%** 입니다. 서로 다른 채권입니다 — "
                                    "변동성을 어느 등급에서 뽑았는지, 이자율 칸의 "
                                    "곡선을 어느 줄로 골랐는지 둘 다 확인하십시오. "
                                    "상대변동성이라 수준 자체가 값에 들어가지는 "
                                    "않지만, 근거가 갈리면 조서가 서지 않습니다.")
                            else:
                                st.caption(f"시계열 평균 {_v['mean']:.2f}% · "
                                           f"{_nmc} {t.T:.2f}년 {_lvl:.2f}% — 수준이 "
                                           "비슷합니다.")
                        except Exception:
                            pass
                        if abs(_v["annual"] - t.bdt_sig) > 5e-5:
                            st.warning(f"아직 **적용하지 않았습니다**. 지금 조서에 "
                                       f"들어가는 값은 **{t.bdt_sig*100:.2f}%** "
                                       "입니다. 아래 단추를 누르셔야 산출한 값이 "
                                       "BDT 격자에 들어갑니다.")
                        if st.button("이 변동성 적용", use_container_width=True,
                                     type="primary", key="rvapply"):
                            t.bdt_sig = _v["annual"]
                            st.rerun()

        with st.expander(L["call"]):
          if is_rcps(t):
            # 콜은 두 갈래다. **발행자 상환권**은 발행회사가 우선주를 되사는 조항이라
            # 거래상대방이 그대로고(문단 4.3.1) 내재파생이므로, 격자 안에서
            # MIN(보유, 상환가액) 으로 눌러 전체에 걸린다. **제3자 지정 매도청구권**은
            # 발행회사가 지정한 제3자가 인수인의 우선주를 사 가는 권리라 거래상대방이
            # 달라져 별도의 금융상품이고, CB 의 매도청구권과 같은 길을 간다 —
            # 한도·의무보유·세 평가방법이 그대로 살아난다.
            t.issuer_call = st.selectbox(
                "콜옵션", [0, 1, 2], index=int(t.issuer_call),
                format_func=lambda i: ["없음 — 상환권은 투자자만",
                                       "발행회사의 상환권 (전체에 걸림)",
                                       "제3자 지정 매도청구권 (한도 %)"][i],
                help="**발행회사의 상환권**은 회사가 우선주를 되사 가는 조항입니다. "
                     "거래상대방이 그대로라 내재파생이고, 상환청구권과 하나의 복합내재파생으로 "
                     "묶습니다 (기준서 1109 문단 B4.3.4).\n\n"
                     "**제3자 지정 매도청구권**은 발행회사가 **지정하는 제3자**가 인수인에게서 "
                     "우선주를 사 가는 권리입니다. 거래상대방이 달라지므로 **별도의 금융상품**이고 "
                     "(문단 4.3.1), 발행회사는 이를 **파생상품자산**으로 따로 인식합니다. "
                     "실무 계약에서는 총 발행금액의 10~20% 한도로 자주 붙습니다.")
            if t.issuer_call == 1:
                t.k_s = st.number_input("시작 (개월)", value=float(t.k_s), step=1.0, key="ks")
                t.k_e = st.number_input("종료 (개월)", value=float(t.k_e), step=1.0, key="ke")
                t.k_f = st.number_input("주기 (개월)", value=float(t.k_f), step=1.0, key="kf")
                t.k_prem = st.number_input("상환 보장수익률 (연 %)", value=t.k_prem*100, step=0.5,
                                           help="발행자 상환가액 = 100 × (1 + 보장수익률 복리)^경과연수 "
                                                "− 기지급배당. 상환청구권과 같은 산식입니다.")/100
                t.k_cmp = int(st.number_input("복리 횟수 (연)", 0, 12, int(t.k_cmp), 1,
                                              help=HLP_CMP))
                st.caption("상환청구권과 하나의 **복합내재파생상품**으로 묶어 순액으로 봅니다 "
                           "(기준서 1109 문단 B4.3.4). 전환권을 자본으로 두면 부채요소는 "
                           "「우선주 + 상환청구권 − 발행자 상환권」입니다.")
            elif t.issuer_call == 2:
                t.k_s = st.number_input("시작 (개월)", value=float(t.k_s), step=1.0, key="ks")
                t.k_e = st.number_input("종료 (개월)", value=float(t.k_e), step=1.0, key="ke")
                t.k_f = st.number_input("주기 (개월)", value=float(t.k_f), step=1.0, key="kf")
                t.k_prem = st.number_input(
                    "매수대금 보장수익률 (연 %)", value=t.k_prem*100, step=0.5,
                    help="매매대금 = 인수대금 × (1 + 보장수익률 복리)^경과연수. "
                         "계약서의 회차별 매도청구권 행사금액(%) 표와 대조하십시오.")/100
                t.k_cmp = int(st.number_input("복리 횟수 (연)", 0, 12, int(t.k_cmp), 1,
                                              help="공시 행사금액표가 분기복리면 4. " + HLP_CMP))
                # 콜 갈래를 바꾸면 derive 가 k_w 를 0(없음)이나 1(발행자 상환권)로
                # 눌러 놓는다. 그 값을 그대로 보이면 한도가 0% 로 뜨므로, 처음
                # 열릴 때는 실무에서 흔한 20% 를 채워 둔다. 계약서 값으로 고치면 된다.
                t.k_w = st.number_input(
                    "행사 한도 (%)", value=(t.k_w*100 if 0 < t.k_w < 1 else 20.0),
                    step=5.0,
                    help="콜옵션 대상주식이 총 발행금액에서 차지하는 비율입니다. "
                         "실무 계약은 10~20% 가 흔합니다. 계약서의 「콜옵션 대상주식」 "
                         "조항을 그대로 넣으십시오.")/100
                t.k_lock = st.number_input(
                    "의무보유 전환지연 (개월)", value=float(t.k_lock), step=1.0,
                    help="인수인이 콜옵션 대상주식을 **미전환 상태로 보유**해야 하는 기간입니다. "
                         "매도청구 종료일까지 두는 계약이 많습니다. "
                         "**유무가치비교법에서만** 값에 들어갑니다.")
                # 옵션차익혼합할인법은 노드의 지분·부채 분해 위에 정의된 산식이라 TF
                # 전용이다 (한공회 4.4.3). GS 를 고르면 기초상품만 GS 이고 콜은 TF 라
                # 표시와 계산이 어긋나므로 조합 자체를 막는다.
                _gs_blk = (t.model == "GS")
                if _gs_blk and t.k_method:
                    t.k_method = 0
                t.k_method = st.selectbox("평가방법", [0, 1, 2],
                                          index=[0, 1, 2].index(t.k_method),
                                          format_func=lambda i: K_METHODS[i], key="kmeth_rcps",
                                          disabled=_gs_blk)
                if _gs_blk:
                    st.caption(COMPAT_GS_KMETHOD)
                if t.k_method:
                    st.warning("의무보유는 지금 고른 평가방법에서 **값을 움직이지 않습니다.** "
                               "옵션차익법은 기초자산을 「콜과 그 부속조항을 뺀 우선주」로 "
                               "보기 때문입니다. 의무보유 효과까지 넣으시려면 "
                               "**유무가치비교법**을 고르십시오.")
                st.caption("거래상대방이 발행회사가 아니라 제3자이므로 **별도의 금융상품**입니다 "
                           "(기준서 1109 문단 4.3.1). 회계처리 탭에서 **파생상품자산**으로 "
                           "따로 세우고, 상환청구권·전환권 묶음에는 넣지 않습니다.")
            else:
                st.caption("상환권은 투자자만 가집니다.")
          else:
              t.k_s = st.number_input("시작 (개월)", value=float(t.k_s), step=1.0, key="ks")
              t.k_e = st.number_input("종료 (개월)", value=float(t.k_e), step=1.0, key="ke")
              t.k_f = st.number_input("주기 (개월)", value=float(t.k_f), step=1.0, key="kf")
              t.k_prem = st.number_input("프리미엄 (연 %)", value=t.k_prem*100, step=0.5)/100
              t.k_cmp = int(st.number_input("복리 횟수 (연)", 0, 12, int(t.k_cmp), 1,
                                            help="분기복리 4 · 반기 2 · 연 1. " + HLP_CMP
                                                 + " 계약서의 매수대금 표와 맞는지 확인하십시오."))
              t.k_w = st.number_input("행사 한도 (%)", value=t.k_w*100, step=5.0)/100
              t.k_lock = st.number_input(
                  "의무보유 전환지연 (개월)", value=float(t.k_lock), step=1.0,
                  help="매도청구 기간 동안 그 부분을 전환하지 못하게 하는 조건입니다. "
                       "**유무가치비교법에서만** 값에 들어갑니다.")
              if t.k_method != 0:
                  st.warning("의무보유는 지금 고른 평가방법에서 **값을 움직이지 "
                             "않습니다.** 옵션차익법은 기초자산을 「콜과 그 부속조항을 "
                             "뺀 전환사채」로 보기 때문입니다 (책 4.4.3). 의무보유 "
                             "효과까지 콜 값에 넣으시려면 **유무가치비교법**을 "
                             "고르십시오.")
              t.k_sep = 1 if st.selectbox(
                  "회계 처리", ["별도 금융상품", "복합내재파생에 포함"],
                  index=0 if t.k_sep else 1,
                  help="발행회사가 지정하는 제3자가 살 수 있으면 거래상대방이 달라지므로 "
                       "별도의 금융상품입니다 (기준서 1109 문단 4.3.1). 발행회사만 "
                       "행사할 수 있으면 내재파생상품이라 전환권·조기상환권과 하나로 "
                       "묶습니다 (문단 B4.3.4). 주계약과 전환권대가는 어느 쪽이든 같고 "
                       "파생을 총액으로 볼지 순액으로 볼지가 다릅니다."
                  ) == "별도 금융상품" else 0
              # 옵션차익혼합할인법은 노드의 지분·부채 분해 위에 정의된 산식이라 TF
              # 전용이다 (한공회 4.4.3). GS 를 고르면 기초상품만 GS 이고 콜은 TF 라
              # 표시와 계산이 어긋나므로 조합 자체를 막는다.
              _gs_blk = (t.model == "GS")
              if _gs_blk and t.k_method:
                  t.k_method = 0
              t.k_method = st.selectbox("평가방법", [0, 1, 2],
                                        index=[0, 1, 2].index(t.k_method),
                                        format_func=lambda i: K_METHODS[i],
                                        disabled=_gs_blk)
              if _gs_blk:
                  st.caption(COMPAT_GS_KMETHOD)
              if t.k_method:
                  st.caption("발행회사가 **지정하는 제3자**도 행사할 수 있는 콜옵션은 별도의 "
                             "금융상품이고 기초자산이 전환사채인 복합옵션입니다 "
                             "(기준서 1109 문단 4.3.1). 기초자산에서 **의무보유는 빠집니다.**")
              else:
                  st.caption("콜을 넣고 뺀 두 평가액의 차이로 봅니다. 의무보유 효과가 콜 값에 "
                             "포함됩니다.")

          # 풋과 콜이 같은 노드에서 함께 열릴 때 누가 먼저인가. 계약이 정하는
          # 것이지 수식이 정하는 것이 아니다. 콜이 없으면 물을 것도 없다.
          if t.k_w > 0:
              t.pc_order = int(st.selectbox(
                  "조기상환청구권과 겹칠 때", [0, 1], index=int(t.pc_order),
                  format_func=lambda i: ["투자자 조기상환 우선", "발행자 매도청구 우선"][i],
                  help="같은 날 두 권리가 모두 열릴 때의 계약상 우선순위입니다.\n\n"
                       "**투자자 조기상환 우선** — 통지한 조기상환을 매도청구로 막지 "
                       "못합니다. 한국 사모 전환사채의 매도청구권은 사채 «일부를 "
                       "매수»하는 권리이지 상환이 아니라는 읽기입니다.\n\n"
                       "**발행자 매도청구 우선** — 매도청구가 유효하게 행사되면 "
                       "투자자는 전환으로만 대응할 수 있습니다. 미국식 callable "
                       "convertible 의 표준 처리입니다.\n\n"
                       "두 행사금액이 다르고 행사기간이 겹칠 때만 값이 갈립니다. "
                       "겹치지 않으면 어느 쪽을 고르셔도 같은 답이 나옵니다."))
              st.caption("계약서에 「이미 통지된 조기상환청구는 매도청구로 "
                         "번복할 수 없다」 같은 조항이 있으면 첫 번째입니다. "
                         "**계약 우선순위가 수식보다 먼저입니다** — 고른 근거를 "
                         "조서에 남기십시오.")

        with st.expander("기말 재평가 · 전기 장부금액"):
            st.caption("평가기준일이 발행일보다 뒤인 **결산 평가**라면 전기말 장부금액을 넣으십시오. "
                       "회계처리 탭에 당기 평가손익과 분개가 나옵니다. 발행 시점 평가면 비워 두십시오.")
            _has_prev = st.checkbox("전기말 장부금액이 있다", value=(t.prev_deriv >= 0))
            if _has_prev:
                t.prev_deriv = st.number_input("전기말 파생상품부채 장부금액 (100 기준)",
                                               value=max(0.0, float(t.prev_deriv)), step=0.01,
                                               format="%.4f",
                                               help="전환권이 부채면 복합내재파생상품, 자본이면 "
                                                    "분리한 상환청구권(·발행자 상환권) 파생상품부채.")
                t.prev_host = st.number_input("전기말 주계약(부채) 장부금액 (100 기준)",
                                              value=max(0.0, float(t.prev_host)), step=0.01,
                                              format="%.4f",
                                              help="상각후원가 장부금액. 당기 상각표의 기초와 대조합니다.")
                _e = st.number_input("발행일 유효이자율 (%)",
                                     value=(t.eir_issue*100 if t.eir_issue >= 0 else 0.0),
                                     step=0.1, min_value=0.0, format="%.4f",
                                     help="**발행 시점 조서**의 상각표에서 역산한 값입니다. "
                                          "이 앱의 상각표는 평가기준일 배분액에서 출발해 최초 "
                                          "인식에만 맞으므로, 결산 평가에서는 이 값을 넣어야 "
                                          "당기 이자비용이 나옵니다.")
                t.eir_issue = _e/100 if _e > 0 else -1.0
                t.cur_periods = int(st.number_input(
                    "당기 이자 회차 수", value=int(t.cur_periods), step=1, min_value=0,
                    help=f"0 이면 1년치({max(1, int(round(12/max(1e-6, t.ipay))))}회)로 봅니다."))
            else:
                t.prev_deriv = -1.0; t.prev_host = -1.0; t.eir_issue = -1.0
            _sa = st.number_input(
                "상환·재매입 지급대가 (100 기준)",
                value=(t.settle_amt if t.settle_amt >= 0 else 0.0), step=1.0, min_value=0.0,
                format="%.4f",
                help="만기 전에 상환하거나 되사는 경우입니다. 0 이면 표시하지 않습니다. "
                     "대가를 부채·자본에 배분해 상환손익을 냅니다 (1032 문단 AG33·AG34).")
            t.settle_amt = _sa if _sa > 0 else -1.0

    else:
        with st.expander("주주간계약 — 풋 · 콜", expanded=True):
            st.caption("모두 **투자일(발행일) 기준 개월**입니다. 계약서 그대로 넣으십시오. "
                       "금액 기준은 투자원금 100 입니다.")
            st.markdown("**투자자 풋옵션** — 보유 지분을 되팔 권리")
            q1, q2, q3 = st.columns(3)
            t.sha_put_s = q1.number_input("시작 (개월)", value=float(t.sha_put_s),
                                          step=1.0, key="shaps")
            t.sha_put_e = q2.number_input("종료 (개월)", value=float(t.sha_put_e),
                                          step=1.0, key="shape")
            t.sha_put_f = q3.number_input("주기 (개월)", value=float(t.sha_put_f),
                                          step=1.0, key="shapf")
            t.sha_put_yield = st.number_input(
                "풋 보장수익률 (%)", value=t.sha_put_yield*100, step=0.5,
                format="%.4f",
                help="행사금액 = 투자원금 × (1 + 보장수익률 복리). 계약서의 「연 복리 "
                     "X% 를 가산한 금액」 조항이 여기입니다.")/100
            t.sha_put_cmp = int(st.number_input(
                "풋 보장 복리 횟수 (연)", value=int(t.sha_put_cmp), step=1,
                min_value=0, max_value=12, help=HLP_CMP))
            st.caption(f"첫 행사일({t.sha_put_s:,.0f}개월) 행사금액 = "
                       f"**{100*(1+accrue_rate(t.sha_put_s/12, t.sha_put_yield, 0.0, t.sha_put_cmp)):,.4f}**"
                       "　(투자원금 100 기준)")
            st.divider()
            st.markdown("**최대주주 콜옵션** — 투자자 지분을 사 갈 권리")
            st.caption("시작이 종료보다 크면 콜이 없는 계약입니다 (둘 다 0 이면 없음).")
            r1, r2, r3 = st.columns(3)
            t.sha_call_s = r1.number_input("시작 (개월)", value=float(t.sha_call_s),
                                           step=1.0, key="shacs")
            t.sha_call_e = r2.number_input("종료 (개월)", value=float(t.sha_call_e),
                                           step=1.0, key="shace")
            t.sha_call_f = r3.number_input("주기 (개월)", value=float(t.sha_call_f),
                                           step=1.0, key="shacf")
            t.sha_call_prem = st.number_input(
                "콜 행사금액 가산율 (%)", value=t.sha_call_prem*100, step=0.5,
                format="%.4f",
                help="행사금액 = 투자원금 × (1 + 가산율 복리).")/100
            t.sha_call_cmp = int(st.number_input(
                "콜 가산 복리 횟수 (연)", value=int(t.sha_call_cmp), step=1,
                min_value=0, max_value=12, help=HLP_CMP))

        # 두 권리가 서로를 소멸시키는가. 따로 재면 공존할 수 없는 두 미래를
        # 각각 값에 넣게 된다 — 행사확률 합이 1 을 넘는 것으로 드러난다.
        t.sha_kill = int(st.selectbox(
            "한쪽이 행사하면 다른 쪽은", [0, 1], index=int(t.sha_kill),
            format_func=lambda x: ("그대로 남는다 — 두 권리를 따로 잰다" if x == 0
                                   else "함께 소멸한다 — 한 격자에서 함께 푼다"),
            help="계약서에 「풋 행사로 주식이 이전되면 콜은 소멸한다」 같은 조항이 "
                 "있으면 두 권리는 경제적으로 독립이 아닙니다. 따로 재면 「풋은 콜이 "
                 "살아 있다고 보고, 콜은 풋이 살아 있다고 보는」 공존할 수 없는 두 "
                 "미래를 각각 값에 넣게 됩니다. 「검산」 탭의 행사확률 합이 1 을 "
                 "넘으면 그 증거입니다."))

        with st.expander("적격상장(Q-IPO) 연계", expanded=True):
            t.ipo_on = int(st.checkbox("적격상장 조항을 격자에 넣는다",
                                       value=bool(t.ipo_on)))
            if t.ipo_on:
                t.ipo_m = st.number_input("적격상장 기한 (개월)", value=float(t.ipo_m),
                                          step=1.0)
                t.ipo_min = st.number_input(
                    "적격 판정 최소 주가 (원)", value=float(t.ipo_min), step=100.0,
                    help="그 시점 주가가 이 값을 넘으면 적격상장이 이루어진 것으로 "
                         "봅니다. 계약의 「적격상장」 정의(공모가·시가총액 기준)를 "
                         "주당으로 환산해 넣으십시오.")
                t.sha_qipo_kill = int(st.selectbox(
                    "적격상장이 되면", [0, 1], index=int(t.sha_qipo_kill),
                    format_func=lambda x: ("풋만 소멸 — 콜은 남는다" if x == 0
                                           else "풋·콜 모두 소멸"),
                    help="적격상장하면 투자자가 시장에서 팔 수 있으므로 풋이 소멸하는 "
                         "것이 보통입니다. 콜도 함께 끝나는지는 계약마다 다릅니다."))
                st.caption("상장 성공은 **그 노드의 주가**가 최소 주가를 넘는지로 "
                           "판정합니다. 상장 확률을 따로 넣지 않습니다 — 확률은 격자가 "
                           "이미 담고 있습니다.")

        with st.expander("의무자 · 할인율", expanded=True):
            t.sha_writer = int(st.selectbox(
                "풋 의무자 (누가 사 주는가)", [0, 1, 2], index=int(t.sha_writer),
                format_func=lambda x: ["최대주주 (발행회사는 당사자가 아니다)",
                                       "발행회사 (자기지분상품 매입의무)",
                                       "최대주주 · 발행회사 연대"][x],
                help="발행회사가 의무자면 자기지분상품을 매입할 의무라 기준서 1032 "
                     "문단 23 이 걸립니다 — 옵션 공정가치가 아니라 **상환금액의 "
                     "현재가치를 총액으로** 금융부채에 싣고 자본에서 뺍니다."))
            t.sha_disc = int(st.selectbox(
                "풋 할인율", [0, 1, 2], index=int(t.sha_disc),
                format_func=lambda x: ["무위험 곡선",
                                       "위험 곡선 (아래 이자율 칸의 위험 곡선)",
                                       "무위험 + 스프레드"][x],
                help="풋은 **현금을 받을 권리**라 의무자의 신용위험이 붙습니다. "
                     "콜은 주식을 받을 권리라 인도 위험이 사실상 없어 늘 무위험으로 "
                     "잽니다."))
            if t.sha_disc == 2:
                t.sha_spread = st.number_input(
                    "스프레드 (%)", value=t.sha_spread*100, step=0.1,
                    format="%.4f")/100

    with st.expander("변동성", expanded=True):
        c1, c2 = st.columns([2, 1])
        code = c1.text_input("종목코드 · 티커", value=(t.ticker or "057680"),
                             help="국내는 6자리 숫자, 해외는 티커. 「기본」의 종목코드를 따라옵니다.",
                             key="vol_code")
        mkt = c2.selectbox("시장", ["KQ", "KS", ""],
                           index=["KQ", "KS", ""].index(st.session_state.get("s0_mkt", "KQ")),
                           format_func=lambda x: {"KQ": "코스닥", "KS": "코스피", "": "해외"}[x],
                           key="vol_mkt")
        c3, c4 = st.columns(2)
        pdays = int(c3.number_input("조회 일수", value=250, step=10, min_value=30))
        tdays = int(c4.number_input(
            "연 거래일수", value=250, step=5,
            help="1년에 며칠 거래하나입니다. 국내 증시는 약 245~250일입니다. "
                 "**받아온 자료가 며칠치인가(조회 일수)와 다릅니다.** 여기에 "
                 "관측 개수를 넣으면 연환산이 어긋납니다."))
        if not 200 <= tdays <= 300:
            st.warning(f"연 거래일수가 **{tdays}일** 입니다. 국내 증시는 약 "
                       "245~250일입니다. 이 칸은 「1년에 며칠 거래하나」이지 "
                       "「몇 일치를 받아왔나」가 아닙니다 — 그건 위의 **조회 "
                       f"일수** 칸입니다. 지금 값이면 σ 가 √({tdays}÷250) = "
                       f"{(tdays/250)**0.5:.3f} 배로 나옵니다.")
        st.caption("야후 파이낸스 수정주가를 씁니다. 유상증자·액면분할·배당이 반영된 종가입니다.")
        # 평가기준일까지의 주가로 변동성을 잰다. 기준일 뒤의 값이 섞이면 결산일 평가가 아니다.
        asof = st.date_input("조회 종료일", value=dt.date.fromisoformat(t.d_base),
                             help="기본은 평가기준일입니다. 기준일 뒤 주가로 변동성을 재면 안 됩니다.")
        drop = st.checkbox("이상치 제거 (중앙값 절대편차 2.5배)", value=True,
                           help="MAD × 1.4826 × 2.5 밖의 일간수익률을 뺍니다. "
                                "책 사례 5-2 와 같은 배수입니다.")
        if st.button("주가 수집", use_container_width=True, type="secondary"):
            with st.spinner("받는 중"):
                try:
                    px, src = fetch_prices(code.strip(), pdays, mkt, asof.isoformat())
                    st.session_state.prices = px
                    st.session_state.px_src = src
                    st.success(f"{src} · {len(px)}개 · {px[0][0]} ~ {px[-1][0]}")
                except Exception as ex:
                    st.error(f"받지 못했습니다 — {ex}\n\n"
                             "종목코드와 시장을 확인하시거나 아래에서 파일을 넣으십시오.")
        pf = st.file_uploader("주가 파일 (엑셀 · csv · txt)",
                              type=["xlsx", "xlsm", "xls", "csv", "txt", "tsv"], key="pxf")
        if pf is not None:
            try:
                rows = parse_prices(read_upload(pf.name, pf.getvalue()))
            except Exception as ex:
                st.error(str(ex))
            else:
                if len(rows) >= 10:
                    st.session_state.prices = rows
                    st.session_state.px_src = pf.name
                    st.success(f"{pf.name} · {len(rows)}개")
                else:
                    st.error(f"종가를 {len(rows)}개밖에 찾지 못했습니다. "
                             "머리글에 '종가' 또는 'Close' 가 있는지 확인하십시오.")
        if st.session_state.prices:
            v = vol_from(st.session_state.prices, tdays, drop)
            if v:
                st.metric("연 변동성", f"{v['annual']*100:.2f}%",
                          f"일 {v['daily']*100:.2f}%")
                st.caption(f"{st.session_state.get('px_src','')} · 수익률 {v['n']}개"
                           + (f" · 이상치 {v['removed']}개 제거 "
                              f"(정상범위 {v['lo']*100:.2f}% ~ {v['hi']*100:.2f}%)"
                              if v['removed'] else ""))
                if abs(v["annual"] - t.sig) > 5e-5:
                    st.warning(f"아직 **적용하지 않았습니다**. 지금 조서에 들어가는 "
                               f"값은 **{t.sig*100:.2f}%** 입니다. 아래 단추를 "
                               "누르셔야 산출한 값이 계산에 들어갑니다.")
                if st.button("이 변동성 적용", use_container_width=True, type="primary"):
                    t.sig = v["annual"]
                    st.rerun()

        st.divider()
        st.markdown("**비상장 — 피어로 산출**")
        st.caption("대상회사 주가가 없으면 유사기업 여럿의 변동성을 모아 씁니다. "
                   "업종·규모·상장기간이 비슷한 회사를 고르고, 왜 골랐는지 조서에 남기십시오.")
        ptxt = st.text_area("피어 목록 — 한 줄에 하나, `코드` 또는 `코드,이름`",
                            value=st.session_state.get("peer_txt", ""),
                            height=90, placeholder="122870,와이지엔터\n035900,JYP\n041510,SM")
        st.session_state.peer_txt = ptxt
        pc1, pc2 = st.columns(2)
        pmkt = pc1.selectbox("피어 시장", ["KQ", "KS", ""], index=0, key="pmkt",
                             format_func=lambda x: {"KQ": "코스닥", "KS": "코스피",
                                                    "": "해외"}[x])
        vpick = pc2.selectbox("종합 방법", ["median", "mean", "max", "min"],
                             format_func=lambda x: {"median": "중앙값", "mean": "단순평균",
                                                    "max": "최댓값", "min": "최솟값"}[x])
        if st.button("피어 주가 수집", use_container_width=True):
            got, fail = [], []
            with st.spinner("받는 중"):
                for line in ptxt.splitlines():
                    line = line.strip()
                    if not line: continue
                    parts = [x.strip() for x in line.replace("\t", ",").split(",")]
                    code = parts[0]
                    nm = parts[1] if len(parts) > 1 and parts[1] else code
                    try:
                        rows, _src = fetch_prices(code, pdays, pmkt, asof.isoformat())
                        got.append((nm, rows))
                    except Exception as ex:
                        fail.append(f"{nm} — {ex}")
            st.session_state.peers = got
            if got: st.success(f"{len(got)}개 수집 · " + " · ".join(n for n, _ in got))
            if fail: st.error("못 받은 것: " + " / ".join(fail))
        mf = st.file_uploader("여러 종목 종가 파일 (첫 열 일자, 나머지 열 종목)",
                              type=["xlsx", "xlsm", "csv", "txt", "tsv"], key="mpxf")
        if mf is not None:
            try:
                got = parse_prices_multi(read_upload(mf.name, mf.getvalue()))
            except Exception as ex:
                st.error(str(ex))
            else:
                if got:
                    st.session_state.peers = got
                    st.success(f"{mf.name} · {len(got)}개 — "
                               + " · ".join(n for n, _ in got))
                else:
                    st.error("종목별 종가를 찾지 못했습니다. 첫 줄이 머리글이고 "
                             "첫 열이 일자인지 확인하십시오.")
        peers = st.session_state.get("peers") or []
        if peers:
            pv = [(nm, vol_from(px, tdays, drop)) for nm, px in peers]
            pv = [(nm, x) for nm, x in pv if x]
            if pv:
                ann = sorted(x["annual"] for _, x in pv)
                agg = {"median": (ann[len(ann)//2] if len(ann) % 2
                                  else (ann[len(ann)//2-1]+ann[len(ann)//2])/2),
                       "mean": sum(ann)/len(ann), "max": ann[-1], "min": ann[0]}[vpick]
                st.dataframe(pd.DataFrame(
                    [[nm, x["annual"], x["n"], x["removed"]] for nm, x in pv],
                    columns=["회사", "연 변동성", "수익률", "제외"]).style.format(
                    {"연 변동성": "{:.2%}"}), use_container_width=True, hide_index=True)
                st.metric("피어 종합", f"{agg*100:.2f}%",
                          {"median": "중앙값", "mean": "단순평균",
                           "max": "최댓값", "min": "최솟값"}[vpick])
                if st.button("피어 종합 적용", use_container_width=True, type="primary"):
                    t.sig = agg
        st.session_state.vol_opt = dict(tdays=tdays, drop=drop, pick=vpick,
                                        asof=asof.isoformat())
        t.sig = st.number_input("변동성 (%)", value=t.sig*100, step=0.5)/100
        # 배당수익률은 위험중립 드리프트에서 빠진다. 배당은 주주에게 가고
        # 전환 전 투자자는 받지 못하므로 전환권·신주인수권이 그만큼 싸진다.
        # 비상장 성장기업은 0 이 보통이지만 상장 배당기업이면 넣어야 한다.
        t.div_y = st.number_input(
            "보통주 배당수익률 δ (%)", value=t.div_y*100, step=0.1,
            min_value=0.0,
            help="배당은 주주에게 가고 전환 전 투자자는 받지 못합니다. "
                 "0 으로 두면 전환권·신주인수권을 그만큼 과대평가합니다. "
                 "위험중립 드리프트에서만 빠지고 할인율에는 닿지 않습니다.")/100

    with st.expander("이자율", expanded=True):
        st.caption("무위험·위험 모두 만기수익률(YTM) 곡선을 넣습니다. "
                   "앱이 선형보간 → 부트스트래핑 → 선도이자율 순으로 처리합니다.")
        t.y_type = st.selectbox("입력 유형", ["par", "spot"],
                                index=0 if t.y_type == "par" else 1,
                                format_func=lambda x: "만기수익률 (YTM)" if x == "par"
                                else "현물이자율 (zero rate)")
        if t.y_type == "spot":
            st.caption("아래 **이표 횟수**를 고시된 곡선의 **복리 횟수**로 맞추십시오. "
                       "회사채 제로커브가 분기복리인데 연복리로 두면 할인계수가 어긋납니다.")
        unit = st.selectbox("만기 단위", ["auto", "month", "year"], index=0,
                            format_func=lambda x: {"auto": "자동 인식", "month": "개월",
                                                   "year": "년"}[x])
        cc1, cc2 = st.columns(2)
        t.cmp_rf = int(cc1.number_input("무위험 이표 (연 회)", value=int(t.cmp_rf),
                                        step=1, min_value=1, max_value=12))
        t.cmp_cr = int(cc2.number_input("위험 이표 (연 회)", value=int(t.cmp_cr),
                                        step=1, min_value=1, max_value=12))
        st.caption("국고채는 6개월 이표(2회), 회사채는 3개월 이표(4회)가 발행 관행입니다. "
                   "부트스트래핑에서 현금흐름 시점을 잡는 데 쓰입니다.")
        # ── KIS-Net 기준수익률 표에서 바로 채우기 ──
        kf = st.file_uploader("KIS-Net 기준수익률 표 (선택)", type=["xls", "xlsx"],
                              key="kisnet",
                              help="채권시가평가 기준수익률 표를 그대로 올리면 "
                                   "무위험·위험 곡선을 골라 아래 칸에 채웁니다.")
        if kf is not None:
            try:
                _rows = read_kisnet(kf.name, kf.getvalue())
            except Exception as ex:
                st.error(f"읽지 못했습니다 — {ex}")
            else:
                _lbl = [x[0] for x in _rows]

                def _first(*cands):
                    """앞에서부터 걸리는 첫 줄. 0 번이 정답일 수 있어 None 으로 가른다."""
                    for ws in cands:
                        for i, L in enumerate(_lbl):
                            if all(w in L for w in ws): return i
                    return 0

                st.success(f"{len(_rows)}개 곡선을 찾았습니다.")
                k1, k2 = st.columns(2)
                _ri = k1.selectbox(
                    "무위험 곡선 (국공채)", range(len(_rows)),
                    index=_first(("국채", "국고채권"), ("국채",)),
                    format_func=lambda i: _lbl[i])
                _ci = k2.selectbox(
                    "위험 곡선 (신용위험 반영)", range(len(_rows)),
                    index=_first(("회사채 II", "무보증"), ("회사채", "무보증"), ("회사채",)),
                    format_func=lambda i: _lbl[i])
                st.caption("사모 CB 는 **회사채 II(사모사채)** 줄이 성격에 가깝습니다. "
                           "무등급이면 추정 등급을 고르고 근거를 조서에 남기십시오.")
                if st.button("이 곡선 적용", use_container_width=True, type="primary"):
                    st.session_state.rf_txt = curve_text(_rows[_ri][1])
                    st.session_state.cr_txt = curve_text(_rows[_ci][1])
                    st.session_state.kis_src = (_lbl[_ri], _lbl[_ci])
                    st.rerun()
                # 등급 보간 칸이 같은 표에서 곡선을 고를 수 있도록 남긴다
                st.session_state.kis_rows = _rows
        if st.session_state.get("kis_src"):
            st.caption("적용된 곡선 — 무위험 **%s** · 위험 **%s**"
                       % st.session_state["kis_src"])

        # 두 곡선 모두 같은 형식이다 — 만기(개월) 다음에 수익률(%).
        # 날짜 열이 앞에 붙어 있어도 그대로 읽는다.
        st.caption("형식은 두 곡선이 같습니다 — 한 줄에 **만기 · 수익률**. "
                   "고시표를 날짜 열까지 통째로 붙여 넣어도 날짜는 알아서 버립니다.")
        if "rf_txt" not in st.session_state:
            st.session_state.rf_txt = ("3\t2.40%\n6\t2.38%\n12\t2.25%\n"
                                       "24\t2.33%\n36\t2.34%\n60\t2.50%")
        if "cr_txt" not in st.session_state:
            st.session_state.cr_txt = ("12\t5.10%\n24\t5.70%\n36\t6.20%\n"
                                       "48\t6.65%\n60\t7.05%")
        rf_txt = st.text_area("무위험 곡선 (국공채 YTM)", key="rf_txt", height=130)
        t.rf_curve = parse_yields(rf_txt, unit)
        _MODES = ["pick", "direct", "rating"]
        _MODE_NM = {"pick": "표에서 등급 하나 고르기",
                    "direct": "YTM 직접 입력",
                    "rating": "두 등급 곡선으로 보간"}
        if t.rate_mode not in _MODES: t.rate_mode = "direct"
        t.rate_mode = st.selectbox("위험 곡선", _MODES,
                                   index=_MODES.index(t.rate_mode),
                                   format_func=lambda x: _MODE_NM[x],
                                   help="고시표를 올리셨으면 **등급 하나 고르기**가 "
                                        "가장 짧습니다. 평가대상 등급이 표에 없을 "
                                        "때만 두 등급 보간을 쓰십시오.")
        _kr = st.session_state.get("kis_rows") or []
        _kg = [(i, rating_in(L)) for i, (L, _) in enumerate(_kr)]
        _kg = [(i, g) for i, g in _kg if g]
        _pick = sorted({g for _, g in _kg}, key=rating_idx)

        if t.rate_mode == "pick":
            # 고시표의 한 줄을 그대로 위험 곡선으로 쓴다. 텍스트 칸을 거치지
            # 않으므로 "고른 등급과 실제로 쓰인 곡선이 다른" 사고가 없다.
            if not _kr:
                st.info("고시표를 아직 올리지 않으셨습니다. 위에서 KIS-Net 표를 "
                        "올리시거나, **YTM 직접 입력**으로 바꾸십시오. 그동안은 "
                        "아래 직접 입력 칸을 씁니다.")
                cr_txt = st.text_area("위험 곡선 (등급별 회사채 YTM)",
                                      key="cr_txt", height=130)
                t.cr_curve = parse_yields(cr_txt, unit); t.cr_curve_b = []
                t.cr_src = "직접 입력"
            else:
                _idx = [i for i, _ in _kg] or list(range(len(_kr)))
                _prev = next((i for i in _idx if _kr[i][0] == t.cr_src), _idx[0])
                _pi = st.selectbox("위험 곡선으로 쓸 줄", _idx,
                                   index=_idx.index(_prev),
                                   format_func=lambda i: _kr[i][0])
                t.cr_curve = list(_kr[_pi][1]); t.cr_curve_b = []
                t.cr_src = _kr[_pi][0]
                _g = rating_in(t.cr_src)
                if _g: t.rt_tgt = _g
                st.success(f"**{t.cr_src}** 을 그대로 씁니다 — "
                           + " · ".join(f"{int(round(m*12))}월 {y*100:.2f}%"
                                        for m, y in t.cr_curve[:6])
                           + (" …" if len(t.cr_curve) > 6 else ""))
                st.caption("표의 만기가 그대로 들어갑니다. 아래 직접 입력 칸은 이 "
                           "방식에서는 쓰이지 않습니다.")
        elif t.rate_mode == "direct":
            cr_txt = st.text_area("위험 곡선 (등급별 회사채 YTM)", key="cr_txt", height=130)
            t.cr_curve = parse_yields(cr_txt, unit)
            t.cr_curve_b = []
            t.cr_src = "직접 입력"
        else:
            # 표를 올리셨으면 **그 표에 있는 등급만** 고르게 한다. 고시표에 없는
            # 등급을 곡선으로 고르면 붙여 넣을 자료가 없다 — 무보증 공모사채는
            # 대개 BBB- 까지만 고시한다.
            _opts = _pick or RATINGS
            _at = lambda r, d: _opts.index(r) if r in _opts else min(d, len(_opts)-1)
            r1, r2, r3 = st.columns(3)
            # 표가 없으면 종전 기본값(BBB+ · BBB-), 있으면 가장 위·아래 등급
            t.rt_a = r1.selectbox("곡선 A", _opts, index=_at(t.rt_a, 0 if _pick else 7))
            t.rt_b = r2.selectbox("곡선 B", _opts,
                                  index=_at(t.rt_b, len(_opts)-1 if _pick else 9))
            t.rt_tgt = r3.selectbox(
                "평가대상", RATINGS,
                index=RATINGS.index(t.rt_tgt) if t.rt_tgt in RATINGS else 8,
                help="곡선 두 개 사이면 내삽, 밖이면 같은 기울기로 외삽합니다. "
                     "평가대상은 표에 없어도 고를 수 있습니다.")
            if _pick:
                st.caption("올리신 표에 있는 등급 — **" + " · ".join(_pick)
                           + "**. 두 곡선은 이 안에서만 고를 수 있습니다.")
                if st.button("고른 두 등급으로 채우기", use_container_width=True):
                    _byg = {}
                    for i, g in _kg: _byg.setdefault(g, i)
                    st.session_state.ca_txt = curve_text(_kr[_byg[t.rt_a]][1])
                    st.session_state.cb_txt = curve_text(_kr[_byg[t.rt_b]][1])
                    st.rerun()
            if "ca_txt" not in st.session_state:
                st.session_state.ca_txt = "12\t4.60%\n36\t5.40%\n60\t6.10%"
            if "cb_txt" not in st.session_state:
                st.session_state.cb_txt = "12\t5.80%\n36\t7.20%\n60\t8.30%"
            ca_txt = st.text_area(f"{t.rt_a} 곡선", key="ca_txt", height=100)
            cb_txt = st.text_area(f"{t.rt_b} 곡선", key="cb_txt", height=100)
            t.cr_curve = parse_yields(ca_txt, unit)
            t.cr_curve_b = parse_yields(cb_txt, unit)
            t.cr_src = f"{t.rt_a}·{t.rt_b} 두 등급 보간 → {t.rt_tgt}"
            ia, ib, it2 = rating_idx(t.rt_a), rating_idx(t.rt_b), rating_idx(t.rt_tgt)
            if ia >= 0 and ib >= 0 and it2 >= 0 and ia != ib:
                w = (it2-ia)/(ib-ia)
                if not 0 <= w <= 1:
                    st.warning(f"평가대상 **{t.rt_tgt}** 가 두 곡선 **밖**입니다 "
                               f"(가중치 {w:.2f}). 등급 간 스프레드는 아래로 갈수록 "
                               "가속해서 벌어지므로, 직선으로 뻗는 외삽은 금리를 "
                               "낮게 잡습니다. 평가대상을 사이에 끼우는 등급을 "
                               "받아 오시는 편이 낫습니다.")
                st.caption(f"가중치 — {t.rt_a} {1-w:.0%} · {t.rt_b} {w:.0%}"
                           + ("   (등급 범위 밖이라 외삽합니다)" if not 0 <= w <= 1 else ""))
        cc = credit_curve(t)
        if len(t.rf_curve) < 2 or len(cc) < 2:
            st.error(f"읽힌 줄 — 무위험 {len(t.rf_curve)}개, 위험 {len(cc)}개. "
                     "만기가 다른 값이 각각 두 개 이상 필요합니다.")
        else:
            RFq, CRq = curves(t)
            st.success(f"부트스트래핑 완료 · {t.T:.2f}년 무위험 {math.exp(RFq(t.T))-1:.2%} "
                       f"위험 {math.exp(CRq(t.T))-1:.2%} "
                       f"(스프레드 {math.exp(CRq(t.T))-math.exp(RFq(t.T)):.2%})")

    st.download_button("시나리오 저장",
                       json.dumps(asdict(t), ensure_ascii=False, indent=2).encode(),
                       f"{lbl(t)['short']}평가_시나리오_{dt.date.today()}.json",
                       "application/json",
                       use_container_width=True)

# ── 계산 ──
t = st.session_state.tm
warn = validate(t)
if warn:
    st.warning("확인이 필요합니다\n\n" + "\n".join(f"- {w}" for w in warn))

if t.sig <= 0:
    st.error("변동성이 0 이라 상승계수와 하락계수가 같아집니다. 격자가 성립하지 "
             "않으므로 계산을 멈춥니다. 「주가·변동성」 탭에서 σ 를 산출해 "
             "적용하십시오.")
    st.stop()

# ── 주주간계약은 사채가 없어 화면이 통째로 갈린다 ──
if is_sha(t):
    LB = lbl(t)
    _sw = sha_validate(t)
    if _sw:
        st.warning("확인이 필요합니다\n\n" + "\n".join(f"- {w}" for w in _sw))
    with st.spinner("계산 중"):
        R = sha_engine(t)
    if R["qbad"]:
        _i, _q = R["qbad"][0]
        st.error(
            f"위험중립가중치가 범위를 벗어났습니다 — 어긋난 구간 {len(R['qbad'])}개, "
            f"전체 범위 [{R['qmin']:.4f}, {R['qmax']:.4f}].\n\n"
            f"처음 어긋난 곳은 **스텝 {_i}** (투자일부터 "
            f"{(_i*R['dt']*12 + t.elapsed_m):.1f}개월, q = {_q:.4f}) 입니다.\n\n"
            "그 구간의 선도이자율이 변동성에 비해 가파릅니다. 변동성을 올리거나 "
            "노드 수를 늘리거나, 이자율 곡선을 확인하십시오.")
        st.stop()
    _eqv = 100*t.S0/t.K0
    _hascall = t.sha_call_e > 0 and t.sha_call_s <= t.sha_call_e
    _F = t.face_total/100.0
    with _HEAD.container():
        st.title("주주간계약 평가")
        st.caption("투자자가 이미 가진 지분에 붙은 **풋**과 **콜**을 이항격자에서 "
                   "각각 잽니다. 사채가 없으므로 순차 차감이 아닙니다. 금액은 "
                   "투자원금 100 기준입니다.")
    m1, m2, m3 = st.columns(3)
    m1.metric("투자자 풋옵션", f"{R['put']:,.2f}",
              help="지분을 보장수익률로 되팔 권리")
    m2.metric("최대주주 콜옵션", f"{R['call']:,.2f}",
              help="지분을 사 갈 권리. 콜이 없으면 0")
    m3.metric("지분가치", f"{_eqv:,.2f}", help="100 × 주가 ÷ 주당 인수가액")

    stabs = st.tabs(["구성요소", "회계처리 — 세 관점", "행사 분포",
                     "이자율곡선", "주가·변동성", "민감도", "검산", "조서"])

    with stabs[0]:
        _crow = ([["－ 최대주주 콜옵션", -R["call"], -R["call"]*_F,
                   "MAX(지분가치 − 행사금액, 0) 을 미국형으로"]]
                 if _hascall else [])
        st.dataframe(pd.DataFrame([
            ["지분가치 (평가기준일)", _eqv, _eqv*_F, "100 × 주가 ÷ 주당 인수가액"],
            ["＋ 투자자 풋옵션", R["put"], R["put"]*_F,
             "MAX(행사금액 − 지분가치, 0) 을 미국형으로"]]
            + _crow
            + [["＝ 투자자가 쥔 것", _eqv + R["put"] - R["call"],
                (_eqv + R["put"] - R["call"])*_F,
                "투자 시점 평가면 투자원금(100)과 견줍니다"]],
            columns=["항목", "100 기준", "전액 기준 (원)", "설명"]).style.format(
            {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
            use_container_width=True, hide_index=True)
        st.caption("풋과 콜을 **따로** 잽니다. 풋은 투자자가, 콜은 최대주주가 고르므로 "
                   "한 격자에서 함께 최적화하면 두 사람을 한 사람으로 만드는 셈이 "
                   "됩니다. 각자의 재무제표에 총액으로 싣는 것도 같은 이유입니다. "
                   "위 표의 «투자자가 쥔 것» 은 세 값을 더해 본 참고치이지 하나의 "
                   "금융상품 가치가 아닙니다.")
        _pk0 = 100*(1 + accrue_rate(t.sha_put_s/12, t.sha_put_yield, 0.0, t.sha_put_cmp))
        _pk1 = 100*(1 + accrue_rate(t.sha_put_e/12, t.sha_put_yield, 0.0, t.sha_put_cmp))
        _rows = [["풋 행사기간", f"{t.sha_put_s:,.0f} ~ {t.sha_put_e:,.0f}개월 · "
                              f"{t.sha_put_f:,.0f}개월마다"],
                 ["풋 행사금액", f"{_pk0:,.4f} (첫날) ~ {_pk1:,.4f} (마지막날)"]]
        if _hascall:
            _ck0 = 100*(1 + accrue_rate(t.sha_call_s/12, t.sha_call_prem, 0.0,
                                        t.sha_call_cmp))
            _ck1 = 100*(1 + accrue_rate(t.sha_call_e/12, t.sha_call_prem, 0.0,
                                        t.sha_call_cmp))
            _rows += [["콜 행사기간", f"{t.sha_call_s:,.0f} ~ {t.sha_call_e:,.0f}개월 · "
                                   f"{t.sha_call_f:,.0f}개월마다"],
                      ["콜 행사금액", f"{_ck0:,.4f} ~ {_ck1:,.4f}"]]
        else:
            _rows.append(["콜옵션", "없음 (시작 > 종료)"])
        _rows.append(["풋 할인율", ["무위험 곡선", "위험 곡선",
                                 f"무위험 + {t.sha_spread:.2%}"][int(t.sha_disc)]])
        if int(t.ipo_on):
            _rows.append(["적격상장", f"{t.ipo_m:,.0f}개월 · 최소 주가 {t.ipo_min:,.0f}원 · "
                                   + ("풋·콜 모두 소멸" if int(t.sha_qipo_kill)
                                      else "풋만 소멸")])
        st.dataframe(pd.DataFrame(_rows, columns=["계약 조건", "내용"]),
                     use_container_width=True, hide_index=True)

    with stabs[1]:
        st.write("같은 계약인데 세 사람의 재무제표에 실리는 것이 완전히 다릅니다. "
                 "**누가 풋 의무자인가**가 가릅니다.")
        acc = sha_accounts(t, R)
        for who in ("발행회사", "최대주주", "투자자"):
            rows, memo = acc[who]
            st.markdown(f"### {who}")
            st.dataframe(pd.DataFrame(
                [[k, v, v*_F] for k, v in rows],
                columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
                {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            st.caption(memo)
        _g = R["gross"]
        if _g:
            st.divider()
            st.markdown("### 옵션 공정가치 대 총액 부채 — 얼마나 다른가")
            st.dataframe(pd.DataFrame([
                ["풋옵션 공정가치 (파생상품부채)", R["put"], R["put"]*_F,
                 "최대주주가 의무자일 때"],
                ["상환금액의 현재가치 (금융부채 총액)", _g["pv"], _g["pv"]*_F,
                 "발행회사가 의무자일 때 — 1032 문단 23"],
                ["차이", _g["pv"] - R["put"], (_g["pv"] - R["put"])*_F,
                 "같은 조항인데 이만큼 갈립니다"]],
                columns=["항목", "100 기준", "전액 기준 (원)", "언제"]).style.format(
                {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            st.info(f"첫 행사 가능일({_g['step']}스텝 · {_g['t']:,.2f}년)의 행사금액 "
                    f"**{_g['strike']:,.4f}** 를 그날까지 할인한 값이 "
                    f"**{_g['pv']:,.4f}** 입니다. 기준서 1032 문단 23 은 "
                    "자기지분상품을 매입할 의무에 대해 **상환금액의 현재가치**를 "
                    "부채로 인식하라고 합니다 — 옵션이 내가격일 확률과 무관합니다. "
                    "그래서 옵션 공정가치보다 훨씬 큽니다.")
            st.markdown("**발행회사가 의무자일 때의 분개**")
            st.code(f"[최초 인식]\n"
                    f"차) 자본 (기타자본)               {_g['pv']:>12,.4f}\n"
                    f"    대) 금융부채 (주식매입의무)      {_g['pv']:>12,.4f}\n\n"
                    f"[매기]\n"
                    f"차) 이자비용                     유효이자율 × 장부금액\n"
                    f"    대) 금융부채                   같은 금액\n\n"
                    f"[풋이 행사되지 않고 소멸]\n"
                    f"차) 금융부채                     그때 장부금액\n"
                    f"    대) 자본 (기타자본)             같은 금액\n"
                    f"※ 문단 23 후단 — 소멸하면 자본으로 되돌린다. 손익이 없다.",
                    language=None)

    with stabs[2]:
        st.write("위험중립확률로 잰 행사·소멸 분포입니다. **실제 행사 예측이 아닙니다** "
                 "— 격자의 확률은 기준일 주가와 변동성이 정한 것입니다.")
        _dp, _dc = R["dist_put"], R["dist_call"]
        def _tab(dd, nm, other):
            _ct = dd.get("counter", 0.0)
            tot = dd["ex"] + dd["qipo"] + dd["expire"] + _ct or 1
            rows = [[f"{nm} 행사", dd["ex"]/tot,
                     dd["tex"]/dd["ex"]/R["mper"] if dd["ex"] else None]]
            if _ct > 1e-12:
                rows.append([f"{other} 행사로 소멸", _ct/tot, None])
            rows += [["적격상장으로 소멸", dd["qipo"]/tot, t.ipo_m if t.ipo_on else None],
                     ["행사기간 만료", dd["expire"]/tot, t.T*12 + t.elapsed_m]]
            return rows
        st.markdown("#### 투자자 풋옵션")
        st.dataframe(pd.DataFrame(_tab(_dp, "풋", "콜"),
            columns=["유형", "비중", "평균 시점(개월)"]).style.format(
            {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
            use_container_width=True, hide_index=True)
        if _hascall:
            st.markdown("#### 최대주주 콜옵션")
            st.dataframe(pd.DataFrame(_tab(_dc, "콜", "풋"),
                columns=["유형", "비중", "평균 시점(개월)"]).style.format(
                {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
                use_container_width=True, hide_index=True)
        if _hascall and not int(t.sha_kill):
            _sum = R["dist_put"]["ex"] + R["dist_call"]["ex"]
            if _sum > 1.0 + 1e-9:
                st.warning(
                    f"풋 행사확률 {R['dist_put']['ex']:.1%} 과 콜 행사확률 "
                    f"{R['dist_call']['ex']:.1%} 을 더하면 **{_sum:.1%}** 입니다. "
                    "두 권리를 따로 재고 있어서, 「풋은 콜이 살아 있다고 보고 콜은 "
                    "풋이 살아 있다고 보는」 공존할 수 없는 두 미래가 각각 값에 "
                    "들어 있다는 뜻입니다. 계약서에 한쪽 행사로 다른 쪽이 소멸한다는 "
                    "조항이 있으면 콜옵션 칸에서 「함께 소멸한다」로 바꾸십시오.")
        st.caption("풋 행사확률이 높다는 것은 그만큼 투자자의 하방이 막혀 있다는 "
                   "뜻입니다. 발행회사가 의무자라면 그 계약은 지분이 아니라 사실상 "
                   "부채이므로, 회계처리 탭의 총액 부채를 함께 보십시오.")

    with stabs[6]:
        st.write("격자가 제대로 섰는지 봅니다.")
        _mart = sum(math.comb(R["n"], j) * R["q"]**j * (1-R["q"])**(R["n"]-j)
                    * R["S"](R["n"], j) for j in range(R["n"]+1))
        _grw = 1.0
        for i in range(R["n"]): _grw *= math.exp(R["rf"](i)*R["dt"])
        _pex0 = (max(R["pk"](0) - _eqv, 0.0) if R["p_on"](0) else 0.0)
        _cex0 = (max(_eqv - R["ck"](0), 0.0) if R["c_on"](0) else 0.0)
        st.dataframe(pd.DataFrame([
            ["위험중립가중치 q · 첫 구간", f"{R['q']:.6f}",
             "적합" if 0 < R["q"] < 1 else "확인 필요"],
            # q 는 구간마다 다시 계산된다. 첫 구간만 실으면 뒤쪽이 깨진 것을
            # 조서에서 알 수 없다.
            ["위험중립가중치 q · 전 구간 범위",
             f"[{R['qmin']:.6f}, {R['qmax']:.6f}]  (구간 {R['n']}개)",
             "적합" if not R["qbad"] else "확인 필요"],
            ["풋 ≥ 즉시 행사가치", f"{R['put'] - _pex0:+.6f}",
             "적합" if R["put"] - _pex0 >= -1e-9 else "확인 필요"],
            ["콜 ≥ 즉시 행사가치", f"{R['call'] - _cex0:+.6f}",
             "적합" if R["call"] - _cex0 >= -1e-9 else "확인 필요"],
            ["풋 ≤ 행사금액 현재가치", f"{R['put']:.6f} ≤ "
             + (f"{R['gross']['pv']:.6f}" if R["gross"] else "—"),
             ("적합" if (not R["gross"] or R["put"] <= R["gross"]["pv"] + 1e-6)
              else "확인 필요")],
            ["풋·콜 모두 0 이상", f"{min(R['put'], R['call']):.6f}",
             "적합" if min(R["put"], R["call"]) >= -1e-9 else "확인 필요"]],
            columns=["검산", "값", "판정"]), use_container_width=True, hide_index=True)
        st.caption("«풋 ≤ 행사금액 현재가치» 는 풋이 아무리 깊은 내가격이라도 "
                   "행사금액을 넘을 수 없다는 상한입니다. 이 줄이 어긋나면 할인율이나 "
                   "행사금액 산식을 보십시오.")

    with stabs[5]:
        st.write("주가와 변동성, 그리고 풋 보장수익률을 흔들어 봅니다.")
        _sc = [0.6, 0.8, 1.0, 1.2, 1.5]
        _rows = []
        for m in _sc:
            t2 = Terms(**asdict(t)); t2.S0 = t.S0*m; derive(t2)
            r2 = sha_engine(t2)
            _rows.append([f"주가 ×{m:.1f}", 100*t2.S0/t2.K0, r2["put"], r2["call"]])
        for sg in (max(0.05, t.sig-0.15), t.sig, t.sig+0.15):
            t2 = Terms(**asdict(t)); t2.sig = sg; derive(t2)
            r2 = sha_engine(t2)
            _rows.append([f"σ {sg:.1%}", _eqv, r2["put"], r2["call"]])
        for gy in (max(0.0, t.sha_put_yield-0.04), t.sha_put_yield,
                   t.sha_put_yield+0.04):
            t2 = Terms(**asdict(t)); t2.sha_put_yield = gy; derive(t2)
            r2 = sha_engine(t2)
            _rows.append([f"풋 보장 {gy:.1%}", _eqv, r2["put"], r2["call"]])
        st.dataframe(pd.DataFrame(_rows,
            columns=["가정", "지분가치", "풋", "콜"]).style.format(
            {"지분가치": "{:,.2f}", "풋": "{:,.4f}", "콜": "{:,.4f}"}),
            use_container_width=True, hide_index=True)
        st.caption("풋은 주가가 내려갈수록, 보장수익률이 올라갈수록 커집니다. "
                   "변동성에는 둔합니다 — 이미 깊은 내가격이면 시간가치가 얼마 "
                   "남지 않기 때문입니다.")

    with stabs[3]:
        st.write("사이드바 **이자율** 칸에서 넣은 곡선입니다. 풋은 아래에서 고른 "
                 "할인율로, 콜은 무위험으로 잽니다.")
        _RF, _CR = curves(t)
        _tt = [i*R["dt"] for i in range(R["n"]+1)]
        st.dataframe(pd.DataFrame(
            [[f"{x:.3f}", _RF(x), _CR(x),
              R["rf"](i) if i < R["n"] else None,
              R["pdisc"](i) if i < R["n"] else None]
             for i, x in enumerate(_tt)],
            columns=["잔존(년)", "무위험 현물", "위험 현물",
                     "무위험 선도", "풋 할인 선도"]).style.format(
            {"무위험 현물": "{:.4%}", "위험 현물": "{:.4%}",
             "무위험 선도": "{:.4%}", "풋 할인 선도": "{:.4%}"}, na_rep="—"),
            use_container_width=True, hide_index=True, height=320)

    with stabs[4]:
        st.write("사이드바 **변동성** 칸에서 산출한 값입니다. 비상장 대상회사면 "
                 "유사기업(피어) 변동성을 쓰고 그 근거를 조서에 남기십시오.")
        st.metric("적용 변동성 σ", f"{t.sig:.2%}")
        _pv2 = st.session_state.get("prices") or []
        if _pv2:
            _vv2 = vol_from(_pv2, (st.session_state.get("vol_opt") or {}).get("tdays", 250),
                            (st.session_state.get("vol_opt") or {}).get("drop", True))
            if _vv2:
                st.caption(f"산출값 {_vv2['annual']:.2%} · 관측 {len(_pv2)}건. "
                           "사이드바에서 「이 변동성 적용」을 누르셔야 위 값이 바뀝니다.")

    with stabs[7]:
        st.write("가정 · 주가 · 지분가치 · 풋 · 콜 트리와 결과 · 회계처리로 이루어진 "
                 "조서를 만듭니다. 값 조서와 수식 조서가 **같은 함수**에서 나오므로 "
                 "자리와 차례가 갈라지지 않습니다.")
        skind = st.radio("조서 형식", ["값", "수식"], horizontal=True, key="sha_kind",
                         format_func=lambda x: "값 조서 — 계산 결과 스냅샷"
                         if x == "값" else "수식 조서 — 엑셀에서 다시 계산됨")
        if skind == "값":
            st.caption("앱이 계산한 값을 그대로 담습니다. 제출용 조서에 적합합니다.")
        else:
            st.caption("가정 시트의 노란 셀을 바꾸면 엑셀 안에서 트리가 다시 "
                       "계산됩니다. 선도이자율만 값으로 들어갑니다.")
        if st.button("조서 만들기", type="primary", use_container_width=True,
                     key="sha_build"):
            try:
                with st.spinner("엑셀 작성 중"):
                    _px = st.session_state.get("peers") or (
                        [(st.session_state.get("px_src") or "대상회사",
                          st.session_state.prices)]
                        if st.session_state.get("prices") else None)
                    _att = dict(px=(_px, st.session_state.get("vol_opt")) if _px else None,
                                rate=None, rate_how="",
                                ir=bool(len(t.rf_curve) >= 2
                                        and len(credit_curve(t)) >= 2))
                    data = build_xlsx_sha(t, R, formula=(skind == "수식"),
                                          attach=_att)
                    fn = f"주주간계약평가조서_{skind}_{dt.date.today()}.xlsx"
                st.session_state.report = (fn, data, _stamp(t, skind))
            except ModuleNotFoundError:
                st.error("openpyxl 이 없습니다.  pip install openpyxl  을 실행하십시오.")
            except Exception as ex:
                st.error(f"조서를 만들지 못했습니다 — {ex}")
        rep = st.session_state.get("report")
        if rep and len(rep) == 3 and rep[2] != _stamp(t, skind):
            st.warning("**조서를 만든 뒤 인풋이 바뀌었습니다.** 예전 파일은 지웠으니 "
                       "「조서 만들기」를 다시 누르십시오.")
            st.session_state.pop("report", None)
            rep = None
        if rep:
            fn, data = rep[0], rep[1]
            st.download_button(f"{fn} 내려받기  ({len(data)/1024:,.0f} KB)", data, fn,
                               "application/vnd.openxmlformats-officedocument."
                               "spreadsheetml.sheet",
                               type="primary", key="sha_dl", use_container_width=True)
        st.divider()
        st.caption("**변동성 산출내역은 이 조서 안에 함께 들어갑니다.** 주주간계약은 "
                   "부트스트래핑한 선도이자율을 트리 8·9행에 값으로 담습니다 — 사채 "
                   "현금흐름이 없어 곡선을 다시 풀 자리가 없기 때문입니다.")
    st.stop()

with st.spinner("계산 중"):
    full, b0, b1, b2, ca, conv = decompose(t)

# 첫 구간만 보면 뒤쪽 선도이자율이 튈 때 q 가 0~1 을 벗어난 채로 계산이 끝난다.
# 전 구간을 보고, 어긋난 스텝을 짚어 준다.
if full["qbad"]:
    _i, _q = full["qbad"][0]
    _fr = full["fwdRF"](_i)
    st.error(
        f"위험중립가중치가 범위를 벗어났습니다 — 어긋난 구간 {len(full['qbad'])}개, "
        f"전체 범위 [{full['qmin']:.4f}, {full['qmax']:.4f}].\n\n"
        f"처음 어긋난 곳은 **스텝 {_i}** (발행일부터 "
        f"{(_i*full['dt']*12 + t.elapsed_m):.1f}개월, 구간 선도이자율 "
        f"{math.exp(_fr)-1:.2%}, q = {_q:.4f}) 입니다.\n\n"
        "그 구간의 선도이자율이 변동성에 비해 가파릅니다. 변동성을 올리거나 "
        "노드 수를 늘리거나, 이자율 곡선을 확인하십시오.")
    st.stop()

eq = full["GS"]*full["P"] if t.model == "GS" else full["E"]
dv = full["GS"]*(1-full["P"]) if t.model == "GS" else full["B"]

c1, c2, c3 = st.columns([2, 1, 1])
LB = lbl(t)
with _HEAD.container():
    st.title(f"{LB['inst']} 평가")
    st.caption("계약조건과 시장자료를 넣으면 이항격자로 옵션을 분리해 계산하고 조서를 "
               f"엑셀로 내보냅니다. 금액은 {LB['unit']}입니다.")
# 무엇을 「공정가치」로 부를지는 콜의 성격이 정한다. B2 는 콜을 뺀 값이라,
# 발행자 상환권이 붙은 계약에서 그대로 「공정가치」라고 쓰면 상환권 값만큼
# 과대표시된다. 제3자 지정 콜은 별도의 금융상품이라 기초상품과 나눠 적는다.
_b3 = b2 - ca
if not full.get("has_call"):
    c1.metric(f"{LB['inst']} 공정가치 · {t.model}", f"{b2:,.2f}",
              help="콜 조항이 없어 B2 가 그대로 공정가치입니다.")
elif issuer_redeem(t):
    c1.metric(f"{LB['inst']} 공정가치 · {LB['call']} 반영 · {t.model}", f"{_b3:,.2f}",
              help=f"{LB['call']}까지 반영한 B3 입니다. 미반영 B2 는 {b2:,.2f}, "
                   f"{LB['call']} 은 {ca:,.2f} 입니다.")
else:
    c1.metric(f"{LB['inst']} 기초상품 · {LB['call']} 미반영 (B2) · {t.model}",
              f"{b2:,.2f}",
              help=f"{LB['call']} 은 거래상대방이 제3자라 **별도의 금융상품**입니다 "
                   f"(기준서 1109 문단 4.3.1) — 파생상품자산 {ca:,.2f} 로 따로 "
                   f"세웁니다. 차감한 순액 B3 는 {_b3:,.2f} 입니다.")
c2.metric("지분가치", f"{eq:,.2f}", help="주식으로 받게 될 부분")
c3.metric("부채가치", f"{dv:,.2f}", help="현금으로 받게 될 부분")

tabs = st.tabs(["구성요소", "회계처리", "분리 판단", "이자율곡선", "주가·변동성",
                "의사결정", "상각표", "민감도", "검산", "조서"])

with tabs[0]:
    df = pd.DataFrame([
        [f"B0  {LB['host'].split(' (')[0]} — 옵션 없음", b0, None, "—"],
        [f"B1  {LB['put']} 추가", b1, b1-b0, LB["put"]],
        [inst_text(t, "B2  전환권 추가"), b2, b2-b1,
         inst_text(t, "전환권")
         + (" (존속기간 만료 시 자동전환 포함)" if auto_conv(t) else "")
         + (" — 행사해도 사채가 남는다" if bw_cash(t) else "")],
        [f"B3  {LB['call']} 반영", b2-ca, -ca,
         (f"{LB['call']} (전체에 걸림)" if issuer_redeem(t)
          else f"{LB['call']} ({t.k_w*100:.0f}% 한도)")]],
        columns=["단계", "가치", "차액", "해당 옵션"])
    st.dataframe(df.style.format({"가치": "{:,.2f}", "차액": "{:+,.2f}"}, na_rep="—"),
                 use_container_width=True, hide_index=True)
    _sc = ipo_scenarios(t)
    if _sc:
        st.markdown("### 상장 시점 가정")
        st.dataframe(pd.DataFrame(
            [[nm, v, v3, cvv, d] for nm, v, v3, cvv, d in _sc],
            columns=["가정", "전체 (B2)", "발행자 상환권 반영 (B3)", "전환권대가", "기준 대비"]
            ).style.format({"전체 (B2)": "{:,.4f}", "발행자 상환권 반영 (B3)": "{:,.4f}",
                            "전환권대가": "{:,.4f}", "기준 대비": "{:+,.4f}"}),
            use_container_width=True, hide_index=True)
        st.caption("예상 상장 시점은 **가정**입니다. 한 값만 싣지 말고 이 표를 조서에 함께 "
                   "넣으십시오 — 감사인이 반드시 묻는 질문의 답이 그 안에 있습니다. "
                   "상장 성공 여부는 그 노드의 주가가 최소공모가격을 넘는지로 판정하므로 "
                   "상장 확률을 따로 넣지 않습니다 (책 [사례 5-5]).")
    if issuer_redeem(t) and ca > 0:
        st.info(f"발행자 상환권을 **전환권 없는 부채 격자**에서 재면 **{full.get('ca_debt', 0.0):,.4f}** "
                f"입니다. 부채요소·전환권대가 배분은 이 값을 씁니다 — 기준서 1032 문단 31 은 "
                "자본요소가 아닌 파생(콜)을 **부채요소 안에** 넣으라고 합니다. 위 B3 의 차액 "
                f"{ca:,.4f} 은 전체 격자에서 전환 상승분을 자른 크기이고, 전환권을 **부채**로 "
                "보면 그쪽을 씁니다 (복합내재파생을 전체로 재므로).")
    if ca < -1e-9:
        st.warning(
            f"**{LB['call']} 값이 음수({ca:,.4f})입니다.** 콜을 넣었더니 전체가 오히려 "
            "커졌다는 뜻이라 계약으로는 설명되지 않습니다. TF 모형에서 콜이 전환을 "
            "앞당기면 그 노드가 통째로 지분이 되어 **무위험이자율로 할인**되기 때문에 "
            "생기는 현상이고, 격자가 성글수록 크게 나타납니다. **노드 간격을 1개월로 "
            "줄여** 보시고, 그래도 음수면 콜의 행사금액·행사기간이 계약과 맞는지 "
            "확인하십시오. 이 값은 배분표에 그대로 들어가므로, 고치지 않으면 "
            "파생상품자산이 음수로 실립니다."
            + ("　발행자 상환권을 부채 격자에서 잰 값(1032 문단 31)은 0 에서 "
               "끊으므로 자본 배분은 영향을 받지 않습니다." if issuer_redeem(t) else ""))
    st.caption("옵션은 서로 대체 관계라 각각 따로 평가해 더하면 총액이 부풀려집니다. "
               "하나씩 얹으며 차액을 보면 합계가 항상 맞습니다.")
    if bw_cash(t):
        # 현금납입형은 지분(신주인수권)과 부채(사채)가 애초에 갈라져 있다.
        # 지분이 될 확률로 할인율을 섞을 자리가 없어 GS 가 TF 와 같은 값이 된다.
        st.dataframe(pd.DataFrame([
            ["신주인수권 (지분요소)", full["E"], "무위험이자율"],
            ["사채 (부채요소)", full["B"], "위험이자율"],
            ["합계", full["TF"], "—"]],
            columns=["요소", "가치", "할인율"]).style.format({"가치": "{:,.2f}"}),
            use_container_width=True, hide_index=True)
        st.caption("현금납입형은 행사해도 사채가 남으므로 지분과 부채가 처음부터 "
                   "갈라져 있습니다. 지분이 될 확률로 할인율을 섞을 자리가 없어 "
                   "**TF 와 GS 가 같은 값**을 냅니다 — 신용위험 처리를 무엇으로 "
                   "고르셔도 결과가 바뀌지 않습니다.")
    else:
        st.dataframe(pd.DataFrame([
            ["TF · 값을 쪼갠다", full["TF"], full["E"], full["B"], None],
            ["GS · 할인율을 섞는다", full["GS"], full["GS"]*full["P"], full["GS"]*(1-full["P"]), full["P"]],
            ["차이", full["TF"]-full["GS"], None, None, None]],
            columns=["모형", "전체", "지분", "부채", inst_text(t, "전환확률")]).style.format(
            {"전체": "{:,.2f}", "지분": "{:,.2f}", "부채": "{:,.2f}",
             inst_text(t, "전환확률"): "{:.4f}"}, na_rep=""),
            use_container_width=True, hide_index=True)
        st.caption(inst_text(t, "전환확률이 0과 1 사이 중간이면 두 모형이 갈립니다. "
                             "한쪽으로 몰리면 사실상 같은 값이 나옵니다."
                             if 0.15 < full["P"] < 0.85 else
                             "전환확률이 한쪽으로 몰려 두 모형이 사실상 같은 값을 냅니다."))

with tabs[1]:
    alloc_rows, alloc_note = allocate(t, full, b0, b1, b2, ca)
    af = allocate_full(t, alloc_rows)
    st.dataframe(pd.DataFrame(af, columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
        {"100 기준": "{:,.2f}", "전액 기준 (원)": "{:,.0f}"}),
        use_container_width=True, hide_index=True)
    st.caption(f"{'발행총액' if is_rcps(t) else '전자등록총액'} {t.face_total:,.0f}원 "
               "기준으로 환산했습니다.")
    st.caption(alloc_note)
    if t.issue_cost > 0:
        _cs, _c100 = cost_split(t, alloc_rows)
        _F = t.face_total/100
        st.markdown("### 거래원가 배분")
        st.dataframe(pd.DataFrame(
            [[inst_text(t, k), v, c, c*_F, how] for k, v, c, how in _cs]
            + [["합계", sum(v for _, v, _, _ in _cs), _c100, _c100*_F, ""]],
            columns=["요소", "배분액 (100)", "거래원가 몫 (100)", "몫 (원)", "처리"]
            ).style.format({"배분액 (100)": "{:,.2f}", "거래원가 몫 (100)": "{:,.4f}",
                            "몫 (원)": "{:,.0f}"}),
            use_container_width=True, hide_index=True)
        if fvpl_on(t):
            st.caption("복합계약 전체를 당기손익-공정가치로 지정했으므로 거래원가를 얹을 "
                       "자리가 없어 **전액 즉시 비용**입니다 (제1109호 문단 5.1.1). "
                       "요소별 배분도, 유효이자율에 녹이는 몫도 없습니다.")
        else:
            st.caption("기업회계기준서 제1032호 문단 38 — 복합금융상품 발행과 관련된 거래원가는 "
                       "**배분된 발행금액에 비례하여** 부채요소와 자본요소로 배분합니다. "
                       "매도청구권 자산은 별도의 금융상품이라(문단 4.3.1) 분모에서 뺐습니다. "
                       + (f"주계약은 거래원가를 뺀 **{_ahc:,.4f}** 에서 상각을 시작하므로 "
                          "유효이자율이 그만큼 높아집니다."
                          if (_ahc := acc_host(t, full, b0, b1, b2, ca)) is not None
                          else "잔여 주계약이 0 이하라 상각표가 없습니다 (아래 상각표 탭)."))
    # 분개는 배분표를 그대로 뒤집는다 — 조서와 같은 규칙. 음수 줄(자산)만
    # 차변으로 가고 나머지는 대변이다. 따로 쓰면 두 표가 어긋난다.
    _je = [("현금", 100.0, None)]
    for _k, _v in alloc_rows[:-1]:
        _nm = inst_text(t, _k.split(" · ")[0])
        if _v < 0: _je.append((f"파생상품자산 ({_nm})", -_v, None))
        else:      _je.append((f"    {_nm}", None, _v))
    _w = max(len(k) for k, _, _ in _je) + 2
    _ln = [f"차) {k:<{_w}} {dr:>12,.4f}" if dr is not None else
           f"    대) {k.strip():<{_w-4}} {cr:>12,.4f}" for k, dr, cr in _je]
    _sd = sum(dr for _, dr, _ in _je if dr); _sc = sum(cr for _, _, cr in _je if cr)
    # 전체 지정이면 상각후원가로 남는 주계약이 없어 유효이자율 이자비용이 없다.
    # 전체를 공정가치로 다시 재고 그 변동을 손익으로 보낸다.
    if fvpl_on(t):
        _post = ("차) 금융부채평가손익            복합계약 전체를 공정가치로 재측정\n"
                 "    대) 당기손익-공정가치 측정 금융부채\n"
                 "※ 상각후원가로 남는 주계약이 없어 유효이자율 이자비용이 없습니다.\n"
                 "※ 자기신용위험 변동분은 기타포괄손익으로 표시합니다 "
                 "(제1109호 문단 5.7.7).\n"
                 "   이 앱은 그 분해를 하지 않으므로 직접 나누어야 합니다.")
    else:
        _post = (
            inst_text(t, "차) 이자비용                   주계약 × 유효이자율\n"
                         "    대) 전환사채 (주계약)\n")
            + ("차) 파생상품평가손익            매 결산 공정가치로 재측정\n"
               "    대) 파생상품부채\n"
               "※ 전환권이 부채이므로 주가가 오르면 평가손실이 납니다."
               if t.conv_class == "liability" else
               ("차) 파생상품평가손익            분리한 파생상품부채를 공정가치로 재측정\n"
                "    대) 파생상품부채\n"
                if any("파생상품부채" in k for k, _ in alloc_rows[:-1]) else "")
               + "※ 전환권대가는 자본이므로 후속 재측정이 없습니다."))
    je = ("[최초 인식]\n" + "\n".join(_ln)
          + f"\n{'합계':<{_w+4}} 차변 {_sd:,.4f} = 대변 {_sc:,.4f}\n\n[후속 결산]\n"
          + _post)
    st.code(je, language=None)

    # ── 기말 재평가 ──
    _rm = remeasure(t, alloc_rows)
    # 전체 지정이면 재평가 대상이 파생상품부채가 아니라 복합계약 전체다.
    _FVL = "복합계약 전체 (당기손익-공정가치)" if fvpl_on(t) else "파생상품부채"
    st.markdown("### 기말 재평가")
    if not _rm["has"]:
        st.caption("결산 평가라면 사이드바 **기말 재평가 · 전기 장부금액** 에 전기말 장부금액을 "
                   "넣으십시오. 당기 공정가치와의 차이가 평가손익으로, 분개와 함께 나옵니다. "
                   f"지금 {_FVL} 공정가치는 **{_rm['fv_liab']:,.4f}** 입니다.")
    else:
        _F = t.face_total/100
        _pl = _rm["pl"]
        st.dataframe(pd.DataFrame([
            [f"{_FVL} · 전기말 장부금액", _rm["prev"], _rm["prev"]*_F],
            [f"{_FVL} · 당기말 공정가치", _rm["fv_liab"], _rm["fv_liab"]*_F],
            [("평가손실 (부채 증가)" if _pl >= 0 else "평가이익 (부채 감소)"), abs(_pl), abs(_pl)*_F]]
            + ([["주계약 · 전기말 장부금액 (참고)", _rm["prev_host"], _rm["prev_host"]*_F]]
               if _rm["prev_host"] is not None else []),
            columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
            {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
            use_container_width=True, hide_index=True)
        if _pl >= 0:
            st.code(f"차) 파생상품평가손실            {_pl:>12,.4f}\n"
                    f"    대) 파생상품부채                {_pl:>12,.4f}", language=None)
        else:
            st.code(f"차) 파생상품부채                {-_pl:>12,.4f}\n"
                    f"    대) 파생상품평가이익            {-_pl:>12,.4f}", language=None)
        st.caption("공정가치는 이 화면의 배분표에서 파생상품부채 줄을 모은 값입니다 — 전환권이 "
                   "부채면 복합내재파생상품, 자본이면 분리한 상환·매도청구권 파생상품부채입니다. "
                   "**주계약은 여기서 재평가하지 않습니다.** 상각후원가는 발행일 유효이자율로 "
                   "굴린 장부금액이어야 하는데 이 앱의 상각표는 평가기준일 배분액에서 출발하므로 "
                   "최초 인식에만 맞습니다. 발행 시점 조서의 상각표 그 회차 기말 금액을 쓰십시오.")
        if (st.session_state.get("px_src") or "").startswith("발행가 역산"):
            st.error("주가가 **발행가 역산**값입니다. 기말 재평가에서는 발행가가 기준이 아니므로 "
                     "평가기준일 주가를 직접 넣으십시오.")

    # ── 당기 이자비용 — 발행일 유효이자율로 굴린다 ──
    _ar, _end = amort_year(t)
    if _ar:
        _F = t.face_total/100
        st.markdown("### 당기 이자비용 — 발행일 유효이자율")
        st.dataframe(pd.DataFrame(
            [[i, bv, it, c, end] for i, bv, it, c, end in _ar],
            columns=["회차", "기초", "유효이자", "지급이자", "기말"]).style.format(
            {"기초": "{:,.4f}", "유효이자": "{:,.4f}", "지급이자": "{:,.4f}", "기말": "{:,.4f}"}),
            use_container_width=True, hide_index=True)
        _ti = sum(x[2] for x in _ar); _tc = sum(x[3] for x in _ar)
        st.code(inst_text(t,
            f"차) 이자비용                    {_ti:>12,.4f}\n"
            + (f"    대) 현금 (표면이자)             {_tc:>12,.4f}\n" if _tc > 0 else "")
            + f"    대) 전환사채 (주계약)            {_ti-_tc:>12,.4f}"), language=None)
        st.caption(f"전기말 {t.prev_host:,.4f} → 당기말 **{_end:,.4f}** "
                   f"(전액 {_end*_F:,.0f}원). 유효이자율 {t.eir_issue:.4%} · {len(_ar)}회차. "
                   "이 앱의 상각표(다음 탭)는 평가기준일 배분액에서 출발하므로 결산에는 "
                   "이 표를 쓰십시오.")

    # ── 전환·상환 시 분개 ──
    _fvl = remeasure(t, alloc_rows)["fv_liab"]
    _host_bv = _end if _ar else (t.prev_host if t.prev_host >= 0 else alloc_rows[0][1])
    with st.expander(inst_text(t, "전환·상환 시 분개")):
        if bw_cash(t):
            # 현금납입형은 행사해도 사채가 남는다. 사채를 제거하는 갈래가 없고,
            # 들어온 현금과 자본요소만 자본으로 넘어간다.
            st.markdown("**신주인수권이 행사될 때** — 기업회계기준서 제1032호 문단 AG32")
            st.caption("행사대금을 현금으로 받으므로 **사채는 그대로 남는다.** 자본으로 "
                       "넘어가는 것은 받은 현금과 신주인수권대가(또는 그때까지 재평가한 "
                       "파생상품부채)뿐이고, 행사에 따라 인식할 손익은 없다. 사채는 "
                       "만기까지 상각후원가로 굴러간다.")
            st.code(
                f"차) 현금 (행사대금)               {100.0:>12,.4f}\n"
                + (f"차) 파생상품부채 (신주인수권)      {_fvl:>12,.4f}\n" if _fvl > 1e-9
                   else f"차) 신주인수권대가 (자본)         {conv:>12,.4f}\n"
                        if t.conv_class == "equity" else "")
                + f"    대) 자본금 + 주식발행초과금       "
                  f"{100.0 + (max(0.0, _fvl) if _fvl > 1e-9 else (conv if t.conv_class == 'equity' else 0)):>12,.4f}\n"
                + "※ 신주인수권부사채(주계약)는 분개에 나오지 않는다 — 행사해도 소멸하지 "
                  "않는다.", language=None)
        else:
            st.markdown("**전환될 때** — 기업회계기준서 제1032호 문단 AG32")
            st.caption("발행자는 부채를 제거하고 자본으로 인식한다. 최초 인식시점의 자본요소는 "
                       "자본의 다른 항목으로 대체될 수 있지만 계속 자본으로 유지된다. "
                       "**전환에 따라 인식할 손익은 없다.**")
            st.code(inst_text(t,
                f"차) 전환사채 (주계약)             {_host_bv:>12,.4f}\n"
                + (f"차) 파생상품부채                 {_fvl:>12,.4f}\n" if _fvl > 1e-9 else "")
                + (f"차) 전환권대가 (자본)            {conv:>12,.4f}\n"
                   if t.conv_class == "equity" else "")
                + f"    대) 자본금 + 주식발행초과금       "
                  f"{_host_bv + max(0.0, _fvl) + (conv if t.conv_class == 'equity' else 0):>12,.4f}"),
                language=None)
        st.markdown("**상환·재매입될 때** — 문단 AG33 · AG34")
        st.caption("지급한 대가를 발행 시점과 **일관된 방법**으로 부채요소와 자본요소에 "
                   "배분한다. 부채요소에 관련된 손익은 당기손익, 자본요소와 관련된 대가는 "
                   "자본으로 인식한다. 발행 시점의 방법이 「부채요소를 먼저 공정가치로 정하고 "
                   "나머지를 자본에」이므로, 대가 중 부채 몫은 **상환일 부채요소 공정가치**이고 "
                   "나머지가 자본 몫이다.")
        _ss = settle_split(t, b1, _host_bv)
        if _ss is None:
            st.info("사이드바 **기말 재평가 · 전기 장부금액 → 상환·재매입 지급대가** 에 "
                    "지급액을 넣으면 배분표와 분개가 나옵니다.")
        else:
            st.dataframe(pd.DataFrame([
                ["지급대가", _ss["pay"], _ss["pay"]*t.face_total/100],
                ["부채 몫 — 상환일 부채요소 공정가치", _ss["liab_fv"], _ss["liab_fv"]*t.face_total/100],
                ["자본 몫 — 잔여", _ss["eq"], _ss["eq"]*t.face_total/100],
                ["부채 장부금액", _ss["liab_bv"], _ss["liab_bv"]*t.face_total/100],
                [("상환이익 (장부 > 부채 몫)" if _ss["pl"] >= 0 else "상환손실"),
                 abs(_ss["pl"]), abs(_ss["pl"])*t.face_total/100]],
                columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
                {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            _pl = _ss["pl"]
            st.code(inst_text(t,
                f"차) 전환사채 (주계약)             {_ss['liab_bv']:>12,.4f}\n"
                f"차) 자본 (전환권대가 등)          {_ss['eq']:>12,.4f}\n"
                + (f"차) 상환손실                    {-_pl:>12,.4f}\n" if _pl < 0 else "")
                + f"    대) 현금                       {_ss['pay']:>12,.4f}\n"
                + (f"    대) 상환이익                   {_pl:>12,.4f}" if _pl > 0 else "")),
                language=None)
            st.caption("부채 몫은 상환일에 다시 잰 부채요소 공정가치입니다 — 지금 화면의 "
                       f"부채요소 **{b1:,.4f}** 를 씁니다. 평가기준일을 상환일로 맞추고 "
                       "그날 곡선을 넣으셔야 맞습니다.")

with tabs[2]:
    if is_bw(t):
        st.write("**신주인수권**이 별도의 금융상품인지 복합금융상품의 자본요소인지, "
                 "그리고 조기상환청구권·매도청구권을 주계약과 분리해야 하는지를 "
                 "계약 조항에 근거해 판단합니다. 아래 문안을 그대로 조서에 "
                 "옮기실 수 있습니다.")
    else:
        st.write(inst_text(t, f"{LB['put']}과 {LB['call']}을 **주계약과 분리해야 "
                              "하는지**를 계약 조항에 근거해 판단하고, 분리한다면 어떤 "
                              "방법으로 재는지까지 정리합니다. 아래 문안을 그대로 조서에 "
                              "옮기실 수 있습니다."))
    st.caption("판단 순서가 정해져 있습니다 — 문단 B4.3.5 말미가 "
               "\"제1032호에 따라 전환채무상품의 자본요소를 분리하기 전에 "
               "내재된 콜옵션이나 풋옵션이 주채무계약과 밀접하게 관련되어 "
               "있는지를 판단한다\" 고 못박습니다.")

    # 격자 로직의 설계도. 모델을 짜기 전에 이 표부터 채워야 노드 의사결정이
    # 계약을 옮긴 것이 된다.
    st.markdown("**계약상 권리** — 이 표가 격자 의사결정의 설계도입니다")
    st.dataframe(pd.DataFrame(rights_table(t), columns=RIGHT_COLS),
                 use_container_width=True, hide_index=True)
    _ov = [x for x in pc_overlap(t) if x[2] > x[3] + 1e-9] if t.k_w > 0 else []
    if _ov:
        st.caption(f"조기상환청구권과 매도청구권이 **{len(_ov)}개 노드에서 함께 열리고** "
                   f"그 자리의 조기상환금액이 더 큽니다 (첫 자리 "
                   f"{_ov[0][1]:,.0f}개월 · {_ov[0][2]:,.4f} 대 {_ov[0][3]:,.4f}). "
                   "**우선순위가 값을 가릅니다** — 아래 비교표를 보십시오.")
    elif t.k_w > 0 and pc_overlap(t):
        st.caption("두 권리가 함께 열리는 노드가 있으나 매도청구금액이 늘 크거나 같아 "
                   "**어느 우선순위를 고르셔도 같은 답**이 나옵니다.")
    st.divider()
    st.markdown("**계약 조항 확인** — 사이드바에 없는 사실만 여기서 받습니다")
    f1, f2 = st.columns(2)
    # RCPS 는 사이드바의 「콜옵션」 갈래가 이 둘을 정한다 (derive 가 덮어쓴다).
    # 눌러도 소용없는 칸을 살려 두면 판단이 어긋난 것처럼 보이므로 잠근다.
    _lk = is_rcps(t)
    t.k_third = 1 if f1.checkbox(
        inst_text(t, "매도청구권을 제3자에게 지정할 수 있다"), value=bool(t.k_third),
        disabled=_lk,
        help=("사이드바 「콜옵션」에서 **제3자 지정 매도청구권**을 고르시면 켜집니다."
              if _lk else
              "공시에 \"발행회사 및 발행회사가 지정하는 자\" 로 적혀 있으면 "
              "해당합니다. 거래상대방이 달라질 수 있어 내재파생상품이 아니라 "
              "별도의 금융상품입니다 (문단 4.3.1 마지막 문장).")) else 0
    t.k_transfer = 1 if f2.checkbox(
        inst_text(t, "매도청구권을 사채와 독립적으로 양도할 수 있다"),
        value=bool(t.k_transfer), disabled=_lk,
        help=("RCPS 는 사이드바 「콜옵션」 갈래가 정합니다." if _lk else
              "같은 문단의 다른 갈래입니다. 둘 중 하나만 해당해도 별도의 "
              "금융상품입니다.")) else 0
    f3, f4 = st.columns(2)
    t.p_lost_int = 1 if f3.checkbox(
        inst_text(t, "조기상환 행사금액이 상실이자 보상 수준이다"), value=bool(t.p_lost_int),
        help="잔여기간에 못 받게 된 이자의 현재가치를 보상하는 수준이면 주계약과 "
             "밀접하게 관련되어 있어 분리하지 않습니다 (문단 B4.3.5(5)(나)). "
             "국내 사모 CB 는 대개 해당하지 않습니다.") else 0
    t.fvpl_whole = 1 if f4.checkbox(
        "복합계약 전체를 당기손익-공정가치로 지정했다", value=bool(t.fvpl_whole),
        help="전체를 공정가치로 재면 내재파생을 따로 뗄 이유가 없습니다 "
             "(문단 4.3.3(3)). 고르면 **배분표가 한 줄**이 되고 유효이자율 "
             "**상각표를 만들지 않습니다** — 상각후원가로 남는 주계약이 없기 "
             "때문입니다. 거래원가는 전액 즉시 비용입니다. 전환권이 자본이면 "
             "지정할 수 없어(문단 4.2.2) 형태가 바뀌지 않고 경고만 뜹니다. "
             "실무에서 드뭅니다.") else 0

    # 상각표는 아래 탭에서 만들어지므로 여기서 따로 부른다. 판단이 쓰는 것은
    # 실제로 인식한 배분액에서 상각한 장부금액이다.
    # 전체 지정이면 인식한 주계약이 없어 상각표를 만들 수 없다. split_test 는
    # 판정에 쓸 상각표를 어차피 **B0 기준으로 다시 만들므로**(순환을 끊은 자리)
    # 빈 목록을 넘겨도 판정이 흔들리지 않는다.
    _ah = acc_host(t, full, b0, b1, b2, ca)
    _sp = split_test(t, full, b0, b1, b2, ca,
                     [] if _ah is None else eir_table(t, _ah)[1])
    st.divider()

    _items = ([("warrant", "신주인수권")] if is_bw(t) else []) + \
             [("put", LB["put"]), ("call", LB["call"])]
    for _key, _nm in _items:
        _d = _sp.get(_key)
        if _d is None: continue
        st.markdown(f"### {_nm}")
        if not _d["있음"]:
            st.info(inst_text(t, _d["이유"][0])); continue
        _box = (st.success if _d["결론"] in ("분리", "별도의 금융상품", "묶어서 분리")
                else st.warning)
        _box(inst_text(t, f"**{_d['결론']}**　—　" + " ".join(_d["이유"])))
        if _d["근거"]:
            st.caption("근거 · " + " · ".join(_d["근거"]))
        if _d["지표"]:
            st.dataframe(pd.DataFrame(
                [[k2, (f"{v2*100:.1f}%" if k2 == "차이" else
                       "예" if v2 is True else "아니오" if v2 is False
                       else v2 if isinstance(v2, str) else f"{v2:,.4f}")]
                 for k2, v2 in _d["지표"].items()],
                columns=["항목", "값"]), use_container_width=True, hide_index=True)
        st.markdown("**평가방법** — " + inst_text(t, _d["평가"]))

    if not _sp["put"]["설정일치"]:
        st.error(inst_text(t,
                 "사이드바의 **조기상환청구권 → 회계 처리** 설정이 위 판정과 "
                 f"어긋납니다. 판정은 **{_sp['put']['결론']}** 인데 설정은 "
                 + ("분리 · 파생상품부채" if int(t.p_sep) else "분리하지 않음")
                 + " 입니다. 배분표와 분개가 판정과 다르게 나오므로 사이드바에서 "
                   "맞추십시오."))
    elif (_sp["put"].get("스위치") and _sp["put"]["결론"] == "분리하지 않을 여지"):
        st.info(inst_text(t,
                "조기상환권은 **어느 쪽도 설명할 수 있는** 자리입니다. 지금 설정은 "
                + ("**분리 · 파생상품부채**" if int(t.p_sep)
                   else "**분리하지 않음 · 부채요소에 포함**")
                + " 입니다. 사이드바 **조기상환청구권 → 회계 처리** 에서 바꿀 수 "
                  "있고, 어느 쪽을 골랐는지와 그 이유를 조서에 적으십시오. "
                  "전환권대가는 어느 쪽이든 같고, 갈리는 것은 부채 표시와 "
                  "후속측정입니다 — 분리하면 파생상품부채를 매기 공정가치로 "
                  "재평가하고, 분리하지 않으면 부채요소를 상각후원가로 굴립니다."))
    if not _sp["call"]["설정일치"]:
        st.error(inst_text(t,
                 "사이드바의 **매도청구권 → 회계 처리** 설정이 위 판정과 "
                 f"어긋납니다. 판정은 **{_sp['call']['결론']}** 인데 설정은 "
                 + ("별도 금융상품" if t.k_sep else "복합내재파생에 포함")
                 + " 입니다. 배분표와 분개가 판정과 다르게 나오므로 사이드바에서 "
                   "맞추십시오."))

    st.divider()
    st.markdown("## 평가방법 — 어떻게 잴 것인가")

    # ── 조기상환권 : 확정 계산으로 충분한가, 금리모형이 필요한가 ──
    st.markdown(inst_text(t, "### 조기상환청구권 — 금리모형(BDT)을 켤 것인가"))
    st.caption(inst_text(t, "전환을 끄면 격자가 주가와 무관해져 스텝마다 값이 하나뿐입니다. "
               "즉 지금 조기상환권은 **미리 내다보고 액면이 더 크면 행사한다**는 "
               "확정 계산이고, 옵션의 시간가치가 들어 있지 않습니다. "
               "행사가 뻔하면 그래도 맞는 답이 나오지만, 애매하면 값을 0 에 "
               "가깝게 잡습니다. 그 자리가 금리모형이 필요한 자리입니다."))
    if t.p_s <= t.p_e and t.T > 0:
        _r0 = engine(t, conv=False, put=False, call=False)
        _dtx = t.T/int(t.n)
        _lo2, _hi2 = step_mapper(t, int(t.n), _dtx)
        _mp = int(t.n)/(t.T*12)
        _pr = max(1, int(round(t.p_f*_mp)))
        _s2, _e2 = _lo2(t.p_s), _hi2(t.p_e)
        _at2 = {}
        for _k3, _v3 in _r0["memo"].items():
            _at2.setdefault(_k3[0], _v3)
        _rows2, _rat2 = [], []
        for _i3 in range(max(_s2, 0), _e2+1):
            if (_i3-_s2) % _pr or _i3 not in _at2: continue
            _hold = _at2[_i3]["E"] + _at2[_i3]["B"]
            _amt = (100*(1 + accrue_rate(_i3*_dtx + t.elapsed_m/12, t.p_yield,
                                         eff_cpn(t), t.p_cmp))
                    if t.p_mode == "accrue" else t.p_rate)
            _rat2.append(_amt/max(_hold, 1e-9))
            _rows2.append([_i3, round(t.elapsed_m + _i3*_dtx*12), _amt, _hold,
                           _rat2[-1]])
        if _rows2:
            _atm2 = sum(1 for x in _rat2 if 0.97 <= x <= 1.03)
            _otm2 = sum(1 for x in _rat2 if x < 0.97)
            st.dataframe(pd.DataFrame(
                _rows2, columns=["스텝", "발행 후 개월", "행사금액", "계속보유가치",
                                 "행사금액 ÷ 계속보유"]).style.format(
                {"행사금액": "{:,.2f}", "계속보유가치": "{:,.2f}",
                 "행사금액 ÷ 계속보유": "{:.3f}"}),
                use_container_width=True, hide_index=True, height=240)
            st.caption("마지막 열이 **1 보다 크면** 그 날 상환받는 편이 낫다는 뜻입니다. "
                       "행사가 확정적이라 금리를 흔들어도 판단이 안 바뀝니다. "
                       "**1 근처(0.97~1.03)이거나 1보다 작으면** 금리에 따라 판단이 "
                       "갈리므로 확정 계산이 값을 적게 잡습니다.")
            if _otm2 == len(_rat2):
                st.error(f"모든 행사일이 **외가격**입니다 (비율 최대 {max(_rat2):.3f}). "
                         f"지금 모델은 조기상환권을 {b1-b0:,.2f} 로 계산하는데, "
                         "외가격 옵션에도 시간가치가 있습니다. **금리모형 없이는 값을 "
                         "0 에 가깝게 잡습니다.** 사이드바에서 BDT 를 켜십시오.")
            elif _atm2 + _otm2 > 0:
                st.warning(f"등가격 근처가 {_atm2}회, 외가격이 {_otm2}회 있습니다 "
                           f"(비율 {min(_rat2):.3f} ~ {max(_rat2):.3f}). 행사 여부가 "
                           "금리에 따라 갈릴 수 있으므로 BDT 를 켜서 차이를 "
                           "확인하고 그 판단을 조서에 남기십시오.")
            else:
                st.success(f"모든 행사일에서 행사금액이 계속보유가치보다 큽니다 "
                           f"(비율 {min(_rat2):.3f} ~ {max(_rat2):.3f}). 행사가 "
                           "확정적이라 금리를 확률변수로 두어도 판단이 바뀌지 "
                           "않습니다. **확정 격자로 충분합니다.**")
        st.caption(inst_text(t, "현재 설정 — 조기상환권을 "
                   + ("**BDT 금리격자**로 잽니다." if put_bdt_on(t) else
                      "**금리 고정 격자**로 잽니다.")
                   + ("" if put_bdt_on(t) else
                      "  BDT 는 전환권이 자본이고 TF 일 때만 켤 수 있습니다.")))
    else:
        st.info(inst_text(t, "조기상환청구권이 없어 판단할 것이 없습니다."))

    # ── 매도청구권 : 세 방법을 나란히 ──
    st.markdown(inst_text(t, "### 매도청구권 — 세 방법 중 무엇으로 잴 것인가"))
    if t.k_w > 0:
        _mv = []
        for _km, _lb in ((0, "유무가치비교법"), (1, "옵션차익 · 혼합할인율"),
                         (2, "옵션차익 · 지분·부채 분리")):
            _tk = Terms(**asdict(t)); _tk.k_method = _km; derive(_tk)
            _mv.append([_lb, decompose(_tk)[4], "◀ 적용" if t.k_method == _km else ""])
        st.dataframe(pd.DataFrame(_mv, columns=["방법", "값", "　"]).style.format(
            {"값": "{:,.4f}"}), use_container_width=True, hide_index=True)
        st.caption(inst_text(t, "**어느 쪽이 옳다기보다 재는 대상이 다릅니다.** 유무가치비교법은 "
                   "콜을 넣고 뺀 차액이라 **의무보유로 잃는 전환권 가치까지** 값에 "
                   "들어갑니다. 옵션차익혼합할인법은 전환사채를 기초자산으로 하는 "
                   "콜옵션 자체만 잽니다. 보고서를 검토하실 때도 어느 방법을 썼는지 "
                   "먼저 확인하셔야 합니다."))
        st.info(inst_text(t, "판정에 따른 권고 — " + _sp["call"]["평가"].replace("**", "")))

        # 계약 우선순위가 값을 얼마나 바꾸는가. 겹치는 노드가 없거나 매도청구금액이
        # 늘 크면 두 갈래가 같은 답을 내므로 표가 한 줄로 겹친다 — 그것도 정보다.
        st.markdown(inst_text(t, "#### 조기상환청구권과 겹칠 때 — 누가 먼저인가"))
        _pv2 = []
        for _po, _lb in ((0, "투자자 조기상환 우선"), (1, "발행자 매도청구 우선")):
            _tp = Terms(**asdict(t)); _tp.pc_order = _po; derive(_tp)
            _f2, _b02, _b12, _b22, _ca2, _cv2 = decompose(_tp)
            _pv2.append([_lb, _ca2, _cv2, "◀ 적용" if int(t.pc_order) == _po else ""])
        st.dataframe(pd.DataFrame(
            _pv2, columns=["우선순위", inst_text(t, "매도청구권"),
                           inst_text(t, "전환권대가"), "　"]).style.format(
            {inst_text(t, "매도청구권"): "{:,.4f}",
             inst_text(t, "전환권대가"): "{:,.4f}"}),
            use_container_width=True, hide_index=True)
        _d2 = abs(_pv2[0][1] - _pv2[1][1])
        if _d2 > 1e-6:
            st.warning(inst_text(t,
                f"**우선순위에 따라 매도청구권이 {_d2:,.4f} 만큼 갈립니다.** "
                "수식이 정하는 것이 아니라 **계약이 정하는 것**입니다 — 계약서의 "
                "통지기간과 「이미 통지된 조기상환청구를 매도청구로 번복할 수 있는가」 "
                "조항을 확인하시고, 고른 근거를 조서에 남기십시오. 사이드바 "
                "매도청구권 칸에서 바꿉니다."))
        else:
            st.caption(inst_text(t,
                "두 우선순위가 같은 답을 냅니다 — 행사기간이 겹치지 않거나, 겹치는 "
                "자리에서 매도청구금액이 조기상환금액보다 크거나 같기 때문입니다. "
                "그래도 계약서의 우선순위 조항은 조서에 적어 두십시오."))
    else:
        st.info(inst_text(t, "매도청구권이 없어 판단할 것이 없습니다."))

    # ── 금리 민감도로 본 금리모형 실익 ──
    with st.expander("금리모형이 값을 얼마나 바꾸는가 — 민감도로 본 실익"):
        RFc, CRc = curves(t)

        def _bump(**kw):
            tt = Terms(**asdict(t))
            for k2, v2 in kw.items(): setattr(tt, k2, v2)
            derive(tt); return pick(engine(tt, call=False), t.model)
        # BDT 는 금리 "수준" 을 확률변수로 둔다. 두 곡선을 함께 흔들어야 노출을
        # 제대로 잰다 — 무위험만 흔들면 스프레드와 상쇄되어 크게 과소하게 잡힌다.
        _par2 = lambda d: dict(rf_curve=[(x, y+d) for x, y in t.rf_curve],
                               cr_curve=[(x, y+d) for x, y in t.cr_curve],
                               cr_curve_b=[(x, y+d) for x, y in t.cr_curve_b])
        _dl = (_bump(**_par2(0.01)) - _bump(**_par2(-0.01)))/2
        _ds = (_bump(cr_curve=[(x, y+0.01) for x, y in t.cr_curve],
                     cr_curve_b=[(x, y+0.01) for x, y in t.cr_curve_b])
               - _bump(cr_curve=[(x, y-0.01) for x, y in t.cr_curve],
                       cr_curve_b=[(x, y-0.01) for x, y in t.cr_curve_b]))/2
        _dv = (_bump(sig=t.sig+0.10) - _bump(sig=max(0.01, t.sig-0.10)))/2
        _ratio = abs(_dl)/max(abs(_dv), 1e-9)
        _spr = CRc(t.T) - RFc(t.T)
        _share = _spr/CRc(t.T) if CRc(t.T) > 1e-9 else 0.0
        st.dataframe(pd.DataFrame([
            ["금리 수준 ±1%p (두 곡선 평행)", f"{_dl:+,.4f}",
             f"{abs(_dl)/max(b2,1e-9)*100:.2f}%"],
            ["신용스프레드 ±1%p (위험 곡선만)", f"{_ds:+,.4f}",
             f"{abs(_ds)/max(b2,1e-9)*100:.2f}%"],
            ["변동성 ±10%p", f"{_dv:+,.4f}", f"{abs(_dv)/max(b2,1e-9)*100:.2f}%"],
            ["금리 수준 ÷ 주가 민감도", f"{_ratio:.3f}", ""],
            [f"{t.T:.2f}년 신용스프레드", f"{_spr*100:.2f}%p",
             f"할인율 중 {_share*100:.0f}%"]],
            columns=["항목", "값", "비중"]), use_container_width=True,
            hide_index=True)
        if _ratio < 0.2 or _share > 0.7:
            st.success("금리를 확률변수로 둘 실익이 작습니다. 신용스프레드가 값을 "
                       "지배하므로 금리 고정 격자로 충분합니다.")
        else:
            st.warning("금리 수준 민감도가 무시할 수준이 아닙니다. BDT 적용 여부를 "
                       "검토하고 그 판단을 조서에 남기십시오.")
        st.caption("두 곡선을 함께 흔드는 이유 — 부채 부분은 위험이자율로 할인되므로 "
                   "무위험 곡선만 흔들면 스프레드 변화와 상쇄되어 노출이 최대 수십 배 "
                   "과소하게 잡힙니다.")

    # ── 회계처리 ──
    st.divider()
    st.markdown("## 회계처리 — 판정대로 배분하면")
    _rows_al, _note_al = allocate(t, full, b0, b1, b2, ca)
    st.dataframe(pd.DataFrame(
        [[k2, v2, fv2] for k2, v2, fv2 in allocate_full(t, _rows_al)],
        columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
        {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
        use_container_width=True, hide_index=True)
    st.caption(_note_al)
    st.caption("분개는 **회계처리** 탭에 있습니다. 여기서는 판정이 배분에 어떻게 "
               "닿는지만 보입니다.")

    st.divider()
    st.markdown("**조서에 옮길 문안**")
    st.code(inst_text(t, split_memo(_sp)), language=None)
    st.caption("판단 순서·근거 문단·지표가 함께 들어 있습니다. 결론만 적는 것과 "
               "달리 감사인이 다시 물을 여지를 줄입니다.")

with tabs[3]:
    RF, CR = curves(t)
    dt_ = t.T/t.n
    rows = []
    for k in range(9):
        tt = t.T*k/8
        i = min(t.n-1, round(tt/dt_))
        fr = forward_rate(RF, i*dt_, (i+1)*dt_)
        fc = forward_rate(CR, i*dt_, (i+1)*dt_)
        rows.append([tt, RF(tt), fr, CR(tt), fc, fc-fr])
    cdf = pd.DataFrame(rows, columns=["시점(년)", "무위험 현물", "무위험 선도",
                                      "위험 현물", "위험 선도", "스프레드"])
    st.dataframe(cdf.style.format({"시점(년)": "{:.2f}", "무위험 현물": "{:.2%}",
                                   "무위험 선도": "{:.2%}", "위험 현물": "{:.2%}",
                                   "위험 선도": "{:.2%}", "스프레드": "{:.2%}"}),
                 use_container_width=True, hide_index=True)
    st.line_chart(cdf.set_index("시점(년)")[["무위험 현물", "위험 현물", "위험 선도"]])
    if len(t.cr_curve) >= 2 and t.y_type == "par":
        st.markdown("**부트스트래핑 과정**")
        dfs = bootstrap_df(t.cr_curve, t.T, t.cmp_cr)
        bt = pd.DataFrame([[d[0], _lin(t.cr_curve, d[0]), d[1], -math.log(d[1])/d[0]]
                           for d in dfs if d[0] > 0],
                          columns=["만기(년)", "만기수익률", "할인계수", "현물이자율(연속)"])
        st.dataframe(bt.style.format({"만기(년)": "{:.2f}", "만기수익률": "{:.2%}",
                                      "할인계수": "{:.6f}", "현물이자율(연속)": "{:.4%}"}),
                     use_container_width=True, hide_index=True, height=260)
        st.caption("각 이표 시점마다 1 = 이자 × 앞선 할인계수 합 + 그 시점 할인계수 를 풀어 "
                   "할인계수를 앞에서부터 순차로 구합니다. 현물이자율은 −LN(할인계수) ÷ 만기입니다.")
    step_df = math.exp(-sum(forward_rate(CR, i*dt_, (i+1)*dt_)*dt_ for i in range(t.n)))
    ok = abs(step_df - math.exp(-CR(t.T)*t.T)) < 1e-8
    st.caption(f"검산 — 스텝별 선도이자율을 {t.n}번 곱한 값 {step_df:.8f} 과 "
               f"만기 현물 할인계수 {math.exp(-CR(t.T)*t.T):.8f} 가 "
               + ("일치합니다." if ok else "어긋납니다. 곡선 입력을 확인하십시오."))
    st.caption("선도이자율  f(t, t+Δt) = [ r(t+Δt)×(t+Δt) − r(t)×t ] ÷ Δt. "
               "격자의 한 스텝 할인이 이 값을 씁니다.")

with tabs[4]:
    if not st.session_state.prices:
        st.info("왼쪽 변동성 칸에서 주가를 수집하거나 파일을 넣으십시오. "
                "국내 6자리 종목은 한국거래소를, 실패하면 야후 파이낸스를 씁니다.")
    else:
        px = st.session_state.prices
        pxdf = pd.DataFrame(px, columns=["날짜", "종가"])
        c1, c2, c3 = st.columns(3)
        c1.metric("종가 개수", f"{len(px):,}")
        c2.metric("기간", f"{px[0][0]} ~ {px[-1][0]}")
        c3.metric("마지막 종가", f"{px[-1][1]:,.0f}")
        st.caption("출처 " + st.session_state.get("px_src", ""))
        if pxdf["날짜"].iloc[0]:
            st.line_chart(pxdf.set_index("날짜")["종가"])
        v = vol_from(px, 250, True); v0 = vol_from(px, 250, False)
        st.dataframe(pd.DataFrame([
            ["이상치 제거", v["annual"], v["daily"], v["n"]-v["removed"], v["removed"]],
            ["이상치 포함", v0["annual"], v0["daily"], v0["n"], 0]],
            columns=["구분", "연 변동성", "일 변동성", "관측", "제거"]).style.format(
            {"연 변동성": "{:.2%}", "일 변동성": "{:.2%}"}),
            use_container_width=True, hide_index=True)
        st.caption(f"정상범위 {v['lo']*100:.2f}% ~ {v['hi']*100:.2f}% — "
                   "일별 로그수익률의 중앙값에서 중앙값 절대편차의 3배를 벗어난 값을 뺍니다.")
        st.markdown("**최근 10일**")
        st.dataframe(pxdf.tail(10).iloc[::-1].style.format({"종가": "{:,.0f}"}),
                     use_container_width=True, hide_index=True)

with tabs[5]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    # 한글 글꼴이 없으면 글자가 네모로 나온다. 찾으면 쓰고, 없으면 영문으로 그린다.
    _kf = use_korean_font()
    _L = (dict(x="스텝", y="주가 수준",
               lg=["전환", "조기상환", "매도청구", "보유", "만기상환"]) if _kf else
          dict(x="step", y="stock level",
               lg=["Convert", "Put", "Call", "Hold", "Redeem"]))
    n = t.n
    idx = {}
    for k, v in full["memo"].items():
        kk = (k[0], k[1])
        if kk not in idx: idx[kk] = v
    code_map = {"conv": 1, "auto": 1, "ipo": 1, "put": 2, "call": 3, "hold": 4, "mat": 5}
    grid = np.full((n+1, n+1), np.nan)
    for (i, j), o in idx.items():
        if j <= i: grid[n-j, i] = code_map[o["kind"]]
    cmap = ListedColormap(["#1b6b5a", "#7a4b1e", "#a3312a", "#e4e8ec", "#9aa4ae"])
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.imshow(grid, aspect="auto", cmap=cmap, vmin=0.5, vmax=5.5, interpolation="nearest")
    ax.set_xlabel(_L["x"]); ax.set_ylabel(_L["y"])
    ax.set_yticks([]); ax.spines[["top", "right"]].set_visible(False)
    ax.legend(handles=[Patch(facecolor=c, label=l) for c, l in
                       zip(["#1b6b5a", "#7a4b1e", "#a3312a", "#e4e8ec", "#9aa4ae"],
                           _L["lg"])],
              loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=5, frameon=False)
    st.pyplot(fig, use_container_width=True)
    if not _kf:
        st.caption("한글 글꼴이 없어 그림만 영문으로 그렸습니다. 표와 설명은 그대로입니다. "
                   "한글로 보시려면 저장소에 `packages.txt` 를 만들어 `fonts-nanum` "
                   "한 줄을 넣으십시오. 다만 apt 설치가 실패하면 앱이 아예 뜨지 "
                   "않으므로, 넣으신 뒤 재시작이 되는지 확인하십시오.")
    D = full["dist"]; tot = D["conv"]+D["put"]+D["call"]+D["mat"] or 1
    if bw_cash(t):
        # 사채가 어떻게 끝나는지와 신주인수권을 행사하는지는 다른 사건이다.
        # 분리형이면 사채를 상환받아도 신주인수권이 남아 둘이 겹칠 수 있다.
        _w, _tw = D.get("wex", 0.0), D.get("tw", 0.0)
        st.dataframe(pd.DataFrame([
            [LB["put"], D["put"]/tot, D["tp"]/D["put"]/full["mper"] if D["put"] else None],
            [LB["call"], D["call"]/tot, D["tk"]/D["call"]/full["mper"] if D["call"] else None],
            ["만기 상환", D["mat"]/tot, t.T*12],
            ["— 신주인수권 행사 (사채와 별개)", _w,
             _tw/_w/full["mper"] if _w else None]],
            columns=["유형", "비중", "평균 시점(개월)"]).style.format(
            {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
            use_container_width=True, hide_index=True)
        st.caption("위 세 줄은 **사채**가 어떻게 끝나는지의 분포이고 합이 100% 입니다. "
                   "마지막 줄은 **신주인수권**을 행사할 확률로, 행사해도 사채가 남으므로 "
                   "위 분포와 따로 셉니다"
                   + (" — 분리형이라 사채를 상환받아도 신주인수권은 남습니다."
                      if int(t.bw_detach) == 1 else
                      " — 비분리형이라 사채가 소멸하는 순간 미행사분은 사라집니다.")
                   + " 기준일 주가에서 잰 위험중립확률이라 실제 행사 예측이 아닙니다.")
    else:
        # 「그중 매도청구 대응」은 전환에서 빼지 않고 겹쳐 센다. 들여쓴 줄이라
        # 합계에 들어가지 않는다 — 위 세 줄과 만기 줄만 더해 100% 다.
        _cc = D.get("conv_called", 0.0)
        _rows = [["전환", D["conv"]/tot,
                  D["tc"]/D["conv"]/full["mper"] if D["conv"] else None]]
        if _cc > 1e-12:
            _rows.append(["　— 그중 매도청구 대응 전환", _cc/tot,
                          D["tcc"]/_cc/full["mper"]])
        _rows += [
            [LB["put"], D["put"]/tot, D["tp"]/D["put"]/full["mper"] if D["put"] else None],
            [LB["call"], D["call"]/tot, D["tk"]/D["call"]/full["mper"] if D["call"] else None],
            ["만기 상환", D["mat"]/tot, t.T*12]]
        st.dataframe(pd.DataFrame(_rows,
            columns=["유형", "비중", "평균 시점(개월)"]).style.format(
            {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
            use_container_width=True, hide_index=True)
        st.caption("거의 모든 경로가 만기 전에 끝나면 기대만기가 계약만기보다 짧다는 뜻이고, "
                   "장기 할인율의 영향이 줄어듭니다."
                   + ("  만기에 존속기간이 만료되어 **보통주로 자동전환**되는 몫은 "
                      "「전환」에 들어갑니다 — 「만기 상환」 줄은 그때 상환청구권을 "
                      "골라 현금으로 끝난 몫입니다." if auto_conv(t) else ""))
        if _cc > 1e-12:
            st.caption(f"**「그중 매도청구 대응 전환」은 전환 {D['conv']/tot:.1%} 안에 "
                       f"들어 있는 몫**이라 합계에 두 번 세지 않습니다. 발행자가 "
                       "매도청구하지 않았다면 그 노드에서 투자자는 전환하지 않았을 "
                       "것입니다 — 콜이 상방을 눌러 전환을 앞당긴 자리입니다. "
                       "이 비중이 높으면 **매도청구권이 기대만기를 짧게 만들고 "
                       "있다**는 뜻이므로, 계약서의 매도청구 조건을 다시 보십시오.")

with tabs[6]:
  _ah6 = acc_host(t, full, b0, b1, b2, ca)
  if _ah6 is None and not fvpl_on(t):
    st.warning(HOST_NONPOS_NOTE)
  elif _ah6 is None:
    st.info("**복합계약 전체를 당기손익-공정가치로 지정**하셨으므로 유효이자율 "
            "상각표를 만들지 않습니다.\n\n"
            "상각표는 「상각후원가로 측정하는 주계약」이 있어야 성립합니다. 전체를 "
            "공정가치로 재면 그 주계약이 없습니다 — 배분표가 한 줄이고, 후속측정은 "
            "상각이 아니라 **전체를 매 결산 공정가치로 다시 재는 것**입니다. "
            "이자비용도 유효이자율로 굴린 금액이 아니라 공정가치 변동에 녹아 듭니다.\n\n"
            "표면이자를 따로 표시하실 거라면 계약상 지급액을 그대로 쓰시고, "
            "자기신용위험 변동분은 기타포괄손익으로 나누셔야 합니다 (제1109호 "
            "문단 5.7.7). 이 앱은 그 분해를 하지 않습니다.")
    st.caption("지정을 해제하시면 요소별 배분과 상각표가 다시 나옵니다. "
               "전환권을 **자본**으로 두셨다면 애초에 지정할 수 없어(문단 4.2.2) "
               "이 화면이 뜨지 않습니다.")
  else:
    r_eir, rows_eir, red, nper = eir_table(t, _ah6)
    st.dataframe(pd.DataFrame([
        ["주계약 (옵션 없는 사채)", f"{b0:,.2f}"], ["만기상환금액", f"{red:,.2f}"],
        ["표면이자 (회당)", f"{100*eff_cpn(t)*t.ipay/12:,.2f}"], ["상각 횟수", f"{nper}회"],
        ["유효이자율 (연, 이산복리)", f"{r_eir:.2%}"]], columns=["항목", "값"]),
        use_container_width=True, hide_index=True)
    amdf = pd.DataFrame(rows_eir, columns=["회차", "경과연수", "기초 장부금액",
                                           "이자비용", "지급이자", "기말 장부금액"])
    st.dataframe(amdf.style.format({"경과연수": "{:.2f}", "기초 장부금액": "{:,.2f}",
                                    "이자비용": "{:,.2f}", "지급이자": "{:,.2f}",
                                    "기말 장부금액": "{:,.2f}"}),
                 use_container_width=True, hide_index=True, height=320)
    st.caption("기말 장부금액이 만기에 상환금액과 일치해야 합니다.")

with tabs[7]:
    rows = []
    for dvv in (-0.15, -0.075, 0.0, 0.075, 0.15):
        tt = Terms(**asdict(t)); tt.sig = max(0.01, t.sig+dvv)
        rows.append([tt.sig, pick(engine(tt, call=False), t.model)])
    base = rows[2][1]
    st.dataframe(pd.DataFrame([[r[0], r[1], r[1]-base] for r in rows],
                              columns=["변동성", "전체 가치", "변화"]).style.format(
        {"변동성": "{:.1%}", "전체 가치": "{:,.2f}", "변화": "{:+,.2f}"}),
        use_container_width=True, hide_index=True)
    rows2 = []
    for dvv in (-0.05, -0.025, 0.0, 0.025, 0.05):
        tt = Terms(**asdict(t))
        tt.cr_curve = [(x, y+dvv) for x, y in t.cr_curve]
        tt.cr_curve_b = [(x, y+dvv) for x, y in t.cr_curve_b]
        rows2.append([dvv, pick(engine(tt, call=False), t.model)])
    base2 = rows2[2][1]
    st.dataframe(pd.DataFrame([[r[0], r[1], r[1]-base2] for r in rows2],
                              columns=["할인율 조정", "전체 가치", "변화"]).style.format(
        {"할인율 조정": "{:+.1%}", "전체 가치": "{:,.2f}", "변화": "{:+,.2f}"}),
        use_container_width=True, hide_index=True)
    st.caption("구조에 따라 값을 지배하는 인풋이 다릅니다. "
               "변동성 영향이 거의 없다면 민감도 공시 대상은 할인율이어야 합니다.")

with tabs[8]:
    st.write("**계산이 성립하는지**만 봅니다. 무엇을 분리하고 어떻게 잴지는 "
             "「분리 판단」 탭으로 옮겼습니다.")
    imm = 100*t.S0/t.K0
    tot_al = allocate(t, full, b0, b1, b2, ca)[0][-1][1]
    checks = [("위험중립가중치 q · 첫 구간", f"{full['q']:.4f}", 0 < full["q"] < 1),
              ("위험중립가중치 q · 전 구간 범위",
               f"[{full['qmin']:.4f}, {full['qmax']:.4f}]  (구간 {full['n']}개)",
               not full["qbad"]),
              ("상승계수 u", f"{full['u']:.4f}", full["u"] > 1),
              ("전체 가치 ≥ 순수사채가치", f"{b2:,.2f} ≥ {full['host']:,.2f}",
               b2 >= full["host"]-1e-6),
              ("전체 가치 ≥ 즉시 전환가치", f"{b2:,.2f} ≥ {imm:,.2f}",
               not (t.cv_s <= 0 and b2 < imm-1e-6)),
              ("배분 합계 = 100", f"{tot_al:,.2f}", abs(tot_al-100) < 0.01)]
    st.dataframe(pd.DataFrame([[k, v, "적합" if ok else "확인 필요"] for k, v, ok in checks],
                              columns=["항목", "값", "판정"]),
                 use_container_width=True, hide_index=True)
    st.caption("위험중립가중치가 0과 1을 벗어나면 변동성이나 노드 수 설정이 잘못된 것입니다. "
               "구간마다 선도이자율로 다시 계산되므로 **첫 구간만 보아서는 안 됩니다** — "
               "전 구간 범위를 함께 싣습니다.")

    # ── 남이 이 격자를 검토한다면 ──
    # 격자 모델 리뷰에서 실제로 묻는 것들. 앱이 답할 수 있는 것은 채워 두고,
    # 계약을 읽어야 답하는 것만 사용자에게 남긴다.
    with st.expander("이 격자를 남이 검토한다면 — 물어야 할 것들"):
        _ovq = pc_overlap(t) if t.k_w > 0 else []
        _ovd = [x for x in _ovq if x[2] > x[3] + 1e-9]
        # 지분·부채로 갈랐을 때 어느 쪽도 음수가 되면 안 된다. 음수가 나오면
        # 노드 결정과 D/E 재분류가 어긋났다는 뜻이다.
        _neg = min([min(o["E"], o["B"]) for o in full["memo"].values()] or [0.0])
        _flags = sum(1 for i in range(full["n"]+1)
                     if full["kstrike"](i) is not None)
        # ── 여기부터는 «설명» 이 아니라 실제로 재는 검산이다 ──
        # 결정과 지분·부채 배정이 어긋난 노드. 전환이면 부채가 0, 상환이면 지분이
        # 0 이어야 한다. 신주인수권부사채는 사채와 신주인수권이 따로라 상환
        # 노드에도 지분이 남으므로 그 상품은 빼고 센다.
        _bw = bw_cash(t)
        _mis = _ill = 0
        for _o in full["memo"].values():
            _kd = _o["kind"]
            if _kd in ("conv", "auto", "ipo") and abs(_o["B"]) > 1e-9: _mis += 1
            if (not _bw) and _kd in ("put", "call", "mat") and abs(_o["E"]) > 1e-9:
                _mis += 1
            # 행사할 수 없는 자리에서 그 결정이 났는가
            if _kd == "put" and _o.get("pv", 0.0) <= 0: _ill += 1
            if _kd == "call" and _o.get("kv", math.inf) == math.inf: _ill += 1
            if _kd in ("conv", "auto", "ipo") and _o.get("cv", 0.0) <= 0: _ill += 1
        # 순차 차감으로 잰 권리 값이 음수는 아닌가 (풋·전환·매도청구권)
        _rt = full["memo"][full["root"]]
        _neg2 = [nm for nm, v in (("조기상환청구권", b1-b0), ("전환권", b2-b1),
                                  (LB["call"], ca)) if v < -1e-9]
        _q = [
            ("풋과 콜이 동시에 가능한 스텝은 어디인가",
             ("없다 — 두 행사기간이 겹치지 않는다" if not _ovq else
              f"{len(_ovq)}개 노드. 첫 자리는 발행일 기준 {_ovq[0][1]:,.0f}개월"
              f"(스텝 {_ovq[0][0]})")),
            ("그때 누가 먼저 결정하는가",
             ("해당 없음" if not _ovq else
              ("발행자 매도청구 우선" if int(t.pc_order) == 1
               else "투자자 조기상환 우선"))),
            ("계약서상 그 우선순위가 맞는가",
             ("해당 없음" if not _ovq else
              ("**확인 필요** — 값이 갈리는 자리가 "
               f"{len(_ovd)}개 있다 (첫 자리 조기상환 {_ovd[0][2]:,.4f} 대 "
               f"매도청구 {_ovd[0][3]:,.4f})" if _ovd else
               "겹치지만 매도청구금액이 늘 크거나 같아 값이 갈리지 않는다"))),
            ("매도청구를 당하면 전환권이 남는가",
             ("해당 없음 — 매도청구권이 없다" if t.k_w <= 0 else
              "남는다. 격자가 전환가치와 매도청구금액을 함께 견준다 — "
              "매도청구가 걸려도 전환이 더 크면 전환을 고른다")),
            ("조기상환을 고른 뒤 매도청구가 또 작동하지는 않는가",
             "않는다. 한 노드에서 한 갈래만 고르고 그 자리에서 계약이 끝난다"),
            ("최종 상태가 지분·부채로 맞게 갈렸는가",
             (f"**어긋난 노드 {_mis}개** · 음수 노드 없음 — 전환이면 (전환가치, 0), "
              "상환이면 (0, 상환금액), 보유면 각각 다른 이자율로 할인한 값이다. "
              f"{len(full['memo']):,}개 노드를 전부 확인했다"
              if (_mis == 0 and _neg >= -1e-9) else
              f"**확인 필요** — 결정과 지분·부채가 어긋난 노드 {_mis}개"
              + (f", 음수가 나온 노드도 있다 (최소 {_neg:,.4f})"
                 if _neg < -1e-9 else ""))),
            ("행사할 수 없는 자리에서 권리가 작동하지는 않는가",
             (f"작동하지 않는다 — {len(full['memo']):,}개 노드를 전부 확인했고 "
              f"어긋난 곳이 없다. 계약일 이후 첫 노드부터 주기마다만 열리고, "
              f"매도청구가 열리는 노드는 {_flags}개다"
              if _ill == 0 else f"**확인 필요** — 어긋난 노드 {_ill}개")),
            ("지분·부채 분해가 모형과 맞는가",
             (f"맞는다. 한 노드가 두 모형을 함께 담는다 — **지분+부채는 TF**"
              f"({_rt['E']+_rt['B']:,.4f} = 결과 {full['TF']:,.4f}), "
              f"**V 는 GS**({_rt['V']:,.4f} = 결과 {full['GS']:,.4f}). "
              "V ≠ 지분+부채 인 것은 결함이 아니라 두 모형이 다른 답을 낸다는 뜻이다"
              if (abs(full["TF"] - (_rt["E"]+_rt["B"])) < 1e-9
                  and abs(full["GS"] - _rt["V"]) < 1e-9)
              else "**확인 필요** — 뿌리 노드가 결과와 어긋난다")),
            ("권리 값이 음수는 아닌가",
             (f"모두 0 이상 — 조기상환청구권 {b1-b0:,.4f} · 전환권 {b2-b1:,.4f} · "
              f"{LB['call']} {ca:,.4f}. 권리를 더하면 값이 올라가고 발행자 권리를 "
              "빼면 내려가야 한다"
              if not _neg2 else
              f"**확인 필요** — 음수인 권리: {', '.join(_neg2)}")),
        ]
        st.dataframe(pd.DataFrame(_q, columns=["질문", "답"]),
                     use_container_width=True, hide_index=True)
        if _ovd:
            st.warning("세 번째 줄이 **확인 필요**입니다. 계약서의 통지기간과 "
                       "우선순위 조항을 보시고, 「분리 판단」 탭의 비교표에서 두 "
                       "갈래의 값 차이를 확인하십시오.")
        st.caption("계약을 읽어야 답할 수 있는 것은 세 번째 줄 하나입니다. "
                   "나머지는 앱이 **격자를 실제로 훑어** 답합니다 — 「어긋난 노드 "
                   "0개」는 서술이 아니라 셈한 결과입니다.")
        st.markdown("**검산 표 — 조서 「검산요약」 시트와 같은 표**")
        _mc = model_checks(t, full, b0, b1, b2, ca, eir_or_none(t, full, b0, b1, b2, ca))
        st.dataframe(pd.DataFrame(_mc, columns=["항목", "값", "판정", "설명"]),
                     use_container_width=True, hide_index=True)
        _mcbad = [nm for nm, _, vd, _ in _mc if vd == "확인 필요"]
        if _mcbad: st.error("확인 필요: " + ", ".join(_mcbad))
        with st.expander("모형의 알려진 한계 — 조서 「99_모형검증」 시트와 같은 표"):
            st.dataframe(pd.DataFrame([(a, b) for a, b, _ in MODEL_LIMITS], columns=["한계", "설명"]),
                         use_container_width=True, hide_index=True)

        # ── 극단 시험 — 격자를 두 번 더 돌리므로 눌렀을 때만 ──
        st.markdown("**극단에서 값이 붙는가**")
        st.caption("주가를 아주 낮추면 전환권이 무가치해져 **전체 = 사채 + 조기상환권** "
                   "이어야 하고, 아주 높이면 **전체 ÷ 전환가치 = 1** 로 붙어야 합니다. "
                   "격자를 두 번 더 돌리므로 누르셨을 때만 잽니다.")
        if st.button("극단 두 곳에서 재 본다", key="btn_extreme"):
            with st.spinner("격자를 두 번 더 돌립니다"):
                _lo = Terms(**asdict(t)); _lo.S0 = t.K0*0.001; derive(_lo)
                _hi = Terms(**asdict(t)); _hi.S0 = t.K0*100.0; derive(_hi)
                _fl, _l0, _l1, _l2, _lc, _ = decompose(_lo)
                _fh, _h0, _h1, _h2, _hc, _ = decompose(_hi)
            _cvh = 100*_hi.S0/_hi.K0
            _gap1, _gap2 = abs(_l2 - _l1), abs(_h2/_cvh - 1.0)
            st.dataframe(pd.DataFrame([
                [f"주가 {_lo.S0:,.2f}원 (인수가액의 0.1%)",
                 f"전체 {_l2:,.4f}", f"사채+조기상환권 {_l1:,.4f}",
                 ("붙는다" if _gap1 < 1e-4 else f"차이 {_gap1:,.4f} — 확인 필요")],
                [f"주가 {_hi.S0:,.0f}원 (인수가액의 100배)",
                 f"전체 ÷ 전환가치 {_h2/_cvh:,.6f}", "1.000000",
                 ("붙는다" if _gap2 < 1e-4 else f"차이 {_gap2:,.6f} — 확인 필요")]],
                columns=["극단", "잰 값", "가야 할 곳", "판정"]),
                use_container_width=True, hide_index=True)
            st.caption("붙지 않으면 전환가치·상환금액 배선이나 리픽싱 하한을 "
                       "의심하십시오. 매도청구권은 이 시험에 넣지 않습니다 — "
                       "한도·의무보유가 걸려 있어 극단에서도 단순한 값으로 "
                       "붙지 않습니다.")

    st.markdown("**신용스프레드가 발행조건과 맞는가**")
    st.caption(inst_text(t, "발행일에는 투자자가 100 을 내고 사채 + 조기상환권 + 전환권을 삽니다. "
               "그러니 전체 가치가 100 이어야 합니다. 크게 벗어나면 인풋이 발행조건과 "
               "어긋난 것이고, 대개 위험할인율(신용스프레드) 추정이 원인입니다. "
               "**전체가 100 이 되는 할인율을 역산해** 넣으신 값과 견줍니다."))
    if t.elapsed_m > 0.01:
        st.info(f"평가기준일이 발행일보다 {t.elapsed_m:.1f}개월 뒤입니다. 그 사이 주가와 "
                "신용도가 바뀌었으므로 전체가 100 을 벗어나는 것이 정상입니다. "
                f"현재 전체 {b2:,.2f}. 역산은 발행일 평가에서만 돌립니다.")
    elif len(t.cr_curve) < 2:
        st.info("위험 곡선을 두 점 이상 넣으셔야 역산할 수 있습니다.")
    else:
        _lv = [y for _, y in t.cr_curve]
        _sh = lambda d: [(x, y+d) for x, y in t.cr_curve]

        def _tot(d):
            tt = Terms(**asdict(t)); tt.cr_curve = _sh(d)
            derive(tt); return decompose(tt)[3]
        try:
            _a, _b = -min(_lv)+1e-4, 0.60          # 곡선을 평행이동할 폭
            if _tot(_b) > 100:
                # 스프레드를 아무리 올려도 못 내려간다 — 사채요소가 아니라
                # 전환조건이 값을 떠받치고 있다는 뜻이다.
                _flr = _tot(_b)
                st.warning(inst_text(t,
                    f"신용스프레드를 60%p 올려도 전체가 {_flr:,.2f} 아래로 "
                    f"내려가지 않습니다 (현재 {b2:,.2f}). 사채요소를 거의 0 으로 "
                    "만들어도 그만큼이 남는다는 뜻이므로, **원인은 신용이 아니라 "
                    "전환조건**입니다. 주가 ÷ 전환가액 "
                    f"{t.S0/max(t.K0,1e-9):.2f}, 변동성 {t.sig:.1%}, "
                    f"리픽싱 {'있음' if t.rfx_mode else '없음'} 을 먼저 보십시오. "
                    "메자닌은 투자자에게 유리하게 발행되는 경우가 많아 실제로 "
                    "100 을 넘기도 합니다 — 그러면 그 사실을 조서에 적으면 됩니다."))
            elif _tot(_a) < 100:
                st.warning(
                    f"스프레드를 0 까지 낮춰도 전체가 100 에 못 미칩니다 "
                    f"(현재 {b2:,.2f}). 만기보장수익률이나 행사조건이 빠지지 "
                    "않았는지 확인하십시오.")
            else:
                for _ in range(36):
                    _m = (_a+_b)/2
                    if _tot(_m) > 100: _a = _m
                    else: _b = _m
                _d = (_a+_b)/2
                _t2 = Terms(**asdict(t)); _t2.cr_curve = _sh(_d)
                derive(_t2)
                _f2, _z0, _z1, _z2, _zc, _zv = decompose(_t2)
                _RF2, _CR2 = curves(_t2)
                st.dataframe(pd.DataFrame([
                    ["넣으신 위험이자율 (잔존만기)", f"{CRc(t.T)*100:.2f}%",
                     f"전체 {b2:,.2f}"],
                    ["역산된 위험이자율", f"{_CR2(t.T)*100:.2f}%", "전체 100.00"],
                    ["차이 (곡선 평행이동)", f"{_d*100:+.2f}%p", ""],
                    ["역산 상태의 조기상환권", f"{_z1-_z0:,.2f}",
                     f"현재 {b1-b0:,.2f}"],
                    ["역산 상태의 전환권대가", f"{_zv:,.2f}", f"현재 {conv:,.2f}"]],
                    columns=["항목", "값", "참고"]),
                    use_container_width=True, hide_index=True)
                if abs(b2-100) < 1.0:
                    st.success("전체가 발행가액과 거의 맞습니다. 인풋이 발행조건과 "
                               "정합적입니다.")
                elif b2 > 100:
                    st.warning(
                        f"전체가 발행가액보다 {b2-100:,.2f} 큽니다. 그만큼을 "
                        "투자자가 공짜로 받은 셈이라 발행조건과 맞지 않습니다. "
                        f"**신용스프레드를 {_d*100:.2f}%p 낮게 잡았을 소지가 큽니다.** "
                        "위 조기상환권 진단에서 등가격이 잡혔다면, 역산값을 쓰면 "
                        "내가격으로 돌아서 등가격 문제 자체가 사라지는지 먼저 "
                        "확인하십시오.")
                else:
                    st.warning(
                        f"전체가 발행가액보다 {100-b2:,.2f} 작습니다. 스프레드를 "
                        "높게 잡았거나, 리픽싱·조기상환 같은 조건이 빠졌을 수 "
                        "있습니다.")
        except Exception as _ex:
            st.info(f"역산하지 못했습니다 — {_ex}")
        st.caption(inst_text(t, "역산값을 그대로 쓰라는 뜻은 아닙니다. 시장에서 관측한 등급 "
                   "수익률을 쓰는 것이 원칙이고, 역산은 **인풋이 발행조건과 얼마나 "
                   "떨어져 있는지 재는 자**입니다. 괴리가 크면 등급 추정이나 "
                   "만기보장수익률 입력을 다시 보십시오."))

with tabs[9]:
    st.write("가정 · 트리 시트 · 이자율곡선 · 결과 · 회계처리 · 상각표로 이루어진 조서를 만듭니다. "
             "트리 하나가 시트 하나이고, 모든 시트의 머리 17행이 같은 형태입니다.")
    kind = st.radio("조서 형식", ["값", "수식"], horizontal=True,
                    format_func=lambda x: "값 조서 — 계산 결과 스냅샷"
                    if x == "값" else "수식 조서 — 엑셀에서 다시 계산됨")
    if kind == "값":
        st.caption("앱이 계산한 값을 그대로 담습니다. 셀을 바꿔도 다시 계산되지 않으므로 "
                   "제출용 조서에 적합합니다. 상태확장을 포함한 모든 설정에서 만들 수 있습니다.")
    else:
        st.caption("가정 시트의 노란 셀을 바꾸면 엑셀 안에서 트리가 다시 계산됩니다. "
                   "선도이자율만 값으로 들어갑니다. 노드 수와 리픽싱 주기는 격자 구조라 바꿀 수 없습니다.")
        if t.carry == 0 and t.rfx_mode > 0:
            st.warning("상태확장은 한 노드에 전환가격이 여럿이라 수식으로 펼 수 없습니다. "
                       "경로가중치로 대체해 만듭니다. 값이 조금 달라집니다.")
    # 산출해 놓고 적용하지 않은 변동성이 있으면 여기서 막아 세운다. 조서를
    # 만드는 자리가 마지막 관문이라, 사이드바 경고를 놓쳐도 여기서는 보인다.
    _unap = []
    _pv = st.session_state.get("prices") or []
    if _pv:
        _vv = vol_from(_pv, (st.session_state.get("vol_opt") or {}).get("tdays", 250),
                       (st.session_state.get("vol_opt") or {}).get("drop", True))
        if _vv and abs(_vv["annual"] - t.sig) > 5e-5:
            _unap.append(f"주가 변동성 — 산출 **{_vv['annual']*100:.2f}%** / "
                         f"조서에 들어가는 값 **{t.sig*100:.2f}%**")
    _rv = st.session_state.get("rate_series") or []
    if _rv and put_bdt_on(t):
        _ro = st.session_state.get("rate_opt") or {}
        _vr = rate_vol(_rv, _ro.get("tdays", 250), _ro.get("drop", True))
        if _vr and abs(_vr["annual"] - t.bdt_sig) > 5e-5:
            _unap.append(f"BDT 단기이자율 변동성 — 산출 **{_vr['annual']*100:.2f}%** / "
                         f"조서에 들어가는 값 **{t.bdt_sig*100:.2f}%**")
    if _unap:
        st.warning("**산출해 놓고 적용하지 않은 변동성이 있습니다.**\n\n"
                   + "\n".join(f"- {x}" for x in _unap)
                   + "\n\n왼쪽 칸의 「이 변동성 적용」을 누르셔야 조서에 들어갑니다. "
                     "지금 만들면 위의 **조서에 들어가는 값**으로 계산됩니다.")

    c1, c2 = st.columns([1, 2])
    if c1.button("조서 만들기", type="primary", use_container_width=True):
        try:
            with st.spinner("엑셀 작성 중"):
                # 산출내역을 조서 안에 함께 싣는다. 수식 조서에서는 종가·고시
                # 수익률이 트리까지 이어져, 한 파일 안에서 인풋을 흔들 수 있다.
                _px = st.session_state.get("peers") or (
                    [(st.session_state.get("px_src") or "대상회사",
                      st.session_state.prices)]
                    if st.session_state.get("prices") else None)
                _rt = ([(st.session_state.get("rate_src") or "금리",
                         st.session_state.rate_series)]
                       if st.session_state.get("rate_series") else None)
                _att = dict(px=(_px, st.session_state.get("vol_opt")) if _px else None,
                            rate=(_rt, st.session_state.get("rate_opt")) if _rt else None,
                            rate_how=st.session_state.get("rate_how", ""),
                            ir=bool(len(t.rf_curve) >= 2 and len(credit_curve(t)) >= 2))
                if kind == "값":
                    data = build_xlsx(t, full, b0, b1, b2, ca, conv,
                                      eir_or_none(t, full, b0, b1, b2, ca),
                                      attach=_att)
                    fn = f"{LB['short']}평가조서_값_{dt.date.today()}.xlsx"
                else:
                    tf = Terms(**asdict(t))
                    if tf.carry == 0 and tf.rfx_mode > 0: tf.carry = 1
                    ff, f0, f1, f2, fca, fconv = decompose(tf)
                    data = build_xlsx_formula(tf, ff, f0, f1, f2, fca, fconv,
                                              eir_or_none(tf, ff, f0, f1, f2, fca),
                                              attach=_att)
                    fn = f"{LB['short']}평가조서_수식_{dt.date.today()}.xlsx"
            st.session_state.report = (fn, data, _stamp(t, kind))
        except ModuleNotFoundError:
            st.error("openpyxl 이 없습니다.  pip install openpyxl  을 실행하고 다시 시도하십시오.")
        except Exception as ex:
            st.error(f"조서를 만들지 못했습니다 — {ex}")

    rep = st.session_state.get("report")
    if rep and len(rep) == 3 and rep[2] != _stamp(t, kind):
        # 조서를 만든 뒤 인풋이 바뀌었다. 예전 파일을 그대로 내주면 트리와
        # 상각표가 서로 다른 계약으로 계산된 조서가 손에 남는다.
        st.warning("**조서를 만든 뒤 인풋이 바뀌었습니다.** 예전 파일은 지웠으니 "
                   "「조서 만들기」를 다시 누르십시오. 그대로 두면 트리와 상각표가 "
                   "서로 다른 계약으로 계산된 조서가 나갑니다.")
        st.session_state.pop("report", None)
        rep = None
    if rep:
        fn, data = rep[0], rep[1]
        st.download_button(f"{fn} 내려받기  ({len(data)/1024:,.0f} KB)", data, fn,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary", key="dl_report", use_container_width=True)
        st.caption("버튼이 보이지 않거나 눌러도 반응이 없으면 브라우저의 팝업·다운로드 차단을 확인하십시오.")

    st.divider()
    st.caption(
        "**산출내역은 이 조서 안에 함께 들어갑니다.** 따로 내려받아 철하실 것이 "
        "없습니다. 조서 뒤쪽에 「σ 표지 · σ 회사별」(변동성), 「σr …」(금리변동성), "
        "「IR 표지 · IR 입력곡선 · IR 곡선별 산출 · IR 선도이자율」(이자율) 시트가 "
        "붙습니다. 수식 조서에서는 트리 11·12행이 「IR 선도이자율」 표를 참조하므로, "
        "고시 수익률을 고치면 부트스트래핑 → 선도이자율 → 트리 → 배분까지 한 파일 "
        "안에서 따라 움직입니다. 변동성은 산출값과 적용값이 같을 때만 이어 붙입니다 "
        "— 다르면 값으로 두어 값 조서와 수식 조서가 갈라지지 않게 합니다.")
