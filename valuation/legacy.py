#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전환사채 평가 — Streamlit

실행
    pip install streamlit numpy pandas matplotlib openpyxl yfinance
    streamlit run cb_app.py

금액은 전자등록금액 100 기준이다.
"""
from __future__ import annotations
import math, json, io, os, re, calendar, datetime as dt
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

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
    # 콜 주당 기준가격(원). −1 이면 풋과 같은 주당 기준가격(K0)이다. 콜 행사금액은
    # 이 가격에 콜 가산율을 붙인 값이고, 100 기준으로는 100 × (이 가격 ÷ K0) 에서 출발한다.
    sha_call_k: float = -1.0
    # 풋·콜 대상 주식수. −1 이면 «계산기준금액 ÷ 주당 기준가격» (종전 투자원금 100 기준과 같다).
    sha_put_q: float = -1.0
    sha_call_q: float = -1.0
    # 같은 주식에 붙은 풋·콜 물량 (한쪽 행사로 상대 권리가 함께 끝나는 물량, sha_kill 1 일 때).
    # −1 이면 입력하지 않음 — 두 수량이 같을 때만 그 수량 전부로 본다. 수량이 다르면 반드시 넣는다
    # (수량만 보고 자동으로 연결하지 않는다). 나머지 풋·콜 물량은 상대 권리 없이 따로 잰다.
    sha_link_q: float = -1.0
    # 평가기준일 현재 실제 보유주식수 (회차별 표) — 넣으면 평가하는 회차들의 계약 대상 주식 합이 이를 넘지 않는지 본다.
    # −1 이면 입력하지 않음 (점검하지 않는다 — 자동으로 가정하지 않는다).
    sha_hold_q: float = -1.0
    # 주주간계약 상장 조항의 종료 조건 — 1 실제 상장 완료(상장일·상장 가정일에 주가와 무관하게 종료) /
    # 0 그 시점 주가가 기준을 넘으면 상장으로 봄(주가 기준) / −1 고르지 않음(상장 조항을 넣으면 막는다).
    # 전환사채·상환전환우선주의 상장 조항에는 쓰지 않는다.
    sha_ipo_kind: int = -1
    # 순액을 누구 입장에서 보나 — 0 콜 권리자(풋이 행사되면 주식을 사 주는 쪽) · 콜 − 풋
    #                           1 풋 권리자(주식 보유자) · 풋 − 콜
    sha_side: int = 0
    # 회차별 표 — 계약·연도·물량별로 행사기간·주당 기준가격·가산율·풋·콜 수량을 한 줄씩.
    # 비어 있으면 위의 풋·콜 칸 한 벌(단일 계약)을 쓴다. 줄의 모양은 SHA_ROW_KEYS.
    sha_rows: list = field(default_factory=list)
    mat_mode: int = 0               # RCPS 존속기간 만료 시 0 보통주 자동전환 / 1 상환
    issuer_call: int = 0            # RCPS 콜 — 0 없음 / 1 발행자 상환권 / 2 제3자 지정 매도청구권
    # 배당가능이익 상환 제약 (RCPS) — 비어 있으면 배당이 가능하다는 전제(제한 없음)다.
    #   dp_rows    [{"fy": 발생연도, "amt": 그해 결산 기준 배당가능이익(원)}] — 다음 해 상환·배당 재원
    #   dp_others  같은 재원을 쓰는 다른 상품. 기본은 평가대상이 선순위(다른 상품은 평가대상 뒤) —
    #              «pari»(동순위)로 고른 상품만 상환청구 금액 비율로 나눈다. 줄의 모양은 DP_OTHER_KEYS.
    #   dp_delay   갚지 못해 다음 해로 넘긴 상환금에 붙는 연 가산율 (계약에 없으면 0)
    dp_rows: list = field(default_factory=list)
    dp_others: list = field(default_factory=list)
    dp_delay: float = 0.0
    #   dp_from    재원 사용 시작일 (월-일). 이 날 전의 청구·지급은 그 전해 재원을 쓴다 — 결산 확정(정기주주총회)
    #              전에는 직전 연도 이익을 쓸 수 없다고 볼 때 «04-01» 처럼 넣는다. 기본 01-01 은 1월 1일부터 쓴다.
    dp_from: str = "01-01"
    div_mode: int = 0               # RCPS 우선배당 0 상환가액에 가산(전체 부채) / 1 재량(부채 현금흐름 제외)
    # 우선배당률의 기준. 격자는 1주 발행가를 100 으로 재므로 배당률도 발행가 기준이어야
    # 한다. 계약이 「1주당 **액면가액** 기준 연 1%」 라고 쓰면 발행가 기준으로는
    # 1% × 액면가 ÷ 발행가 다 — 액면 500원 · 발행가 59,390원이면 연 0.0084%.
    # 환산은 eff_cpn() 한 곳에서만 한다. cpn 칸에는 계약서 숫자를 그대로 둔다.
    div_basis: int = 0              # 0 발행가 기준 / 1 액면가 기준
    issue_px: float = 0.0           # 1주당 발행가 (원) — 액면 기준 환산에 쓴다
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
    grid_days: float = 0.0          # 0: 기존 월 격자, 7/14: 일수 기준 등간격 격자
    T: float = 5.0                  # 잔존기간 — derive() 가 채운다
    n: int = 60                     # 노드 수 — derive() 가 채운다
    elapsed_m: float = 0.0          # 발행일 → 평가기준일 경과 개월
    rem_m: float = 60.0             # 평가기준일 → 만기 «계약상» 개월 — derive() 가 채운다
    # 행사금액(조기상환·매도청구·만기)의 경과기간을 무엇으로 세는가.
    # 1 계약 개월 ÷ 12 — 계약서가 「36개월 보장수익률」이라 쓰면 그 36개월이다 (기본)
    # 0 Actual/365 — 할인기간과 같은 잣대. 종전 동작이라 옛 값을 재현할 때 쓴다
    acc_basis: int = 1
    # 계약서의 «회차별 행사금액표» 를 그대로 넣는 자리. 한 줄에 「날짜 또는 개월 · 금액(%)」.
    # 넣으면 산식보다 «우선» 하고, 행사 가능 시점도 이 표를 따른다. 비우면 산식대로다.
    p_sched: str = ""             # 조기상환 회차별 표
    k_sched: str = ""             # 매도청구 회차별 표
    mat_amt: float = -1.0         # 만기상환금액(%). 음수면 산식으로 계산
    cpn: float = 0.0            # 표면이자율
    ipay: float = 3.0           # 이자 지급주기 (개월)
    ytm: float = 0.0            # 만기보장수익률
    ytm_cmp: int = 4            # 만기보장수익률 복리 횟수 (분기)
    cv_s: float = 12.0          # 전환 시작 (개월)
    cv_e: float = 59.0
    rfx_mode: int = 2           # 0 없음 / 1 하향만 / 2 하향+상향
    rfx_cyc: float = 7.0
    # 최초 조정일 (발행 후 개월). 0 이면 주기와 같다 — 첫 조정 = 발행일 + 주기.
    # 「발행 후 12개월 되는 날 최초 조정, 이후 매 7개월」 처럼 첫 조정만 따로 정한 계약은
    # 여기에 12 를 넣는다 (12 · 19 · 26 · 33 …). 주기를 12 로 바꾸면 이후 조정일이 틀린다.
    rfx_first: float = 0.0
    floor: float = 598.0
    par: float = 500.0
    # 조정 후 전환가격의 원 단위 미만 처리 — 계약서 문구대로. 0 처리 없음 / 1 절상 / 2 절사.
    # 주가로 새로 정한 가격(정기 조정)과 공모가 × 배수(상장 조정)에 걸고, 그다음 하한·상한을 건다.
    rfx_round: int = 0
    carry: int = 1              # 1 경로가중치 / 2 확률가중평균 / 3 특정노드선택 (상태확장은 없앴다)
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
    # 의무보유 물량 비율 (발행총액 대비). 음수면 콜 대상비율(k_w)과 같다 — 콜 대상 전부가 묶인다.
    # 「콜 한도 70% · 미전환 의무보유 30%」 처럼 다르면 콜 대상 70% 가운데 30% 만 묶이고
    # 나머지 40% 는 처음부터 전환·조기상환할 수 있다 (lock_share · call_mix).
    k_lock_w: float = -1.0
    k_split: int = 0              # 옵션차익혼합할인법의 행사가 분해 — 0 부속예제(가치 구성비율 E/(E+B)) / 1 한공회 본문 4.3.3(GS 전환확률)
    k_kind: int = 0               # 콜옵션 유형 — 0 제3자 지정 «가능» 콜(발행자 보유 · 파생상품자산) / 1 제3자 사전 «기특정» 콜(본문 4.5.4 접근법 2-2 · 주주간 분배)
    k_basis: str = ""             # 평가기법 선택 근거 (한공회 4.6.2 문서화) — 조서 문안에 실린다
    # 콜 대상물량 의무보유. 대상비율은 k_w, **기간은 k_lock**, 막는 권리는 전환과
    # (k_lock_put 이면) 조기상환청구다. 1 이면 그 기간 동안 물량이 살아 있어 콜을 언제든
    # 행사할 수 있고, 0 이면 투자자가 전환·조기상환으로 사채를 소멸시킬 때 콜도 사라진다.
    # 유무가치비교법과 옵션차익법이 **같은 기간·같은 권리**를 본다.
    k_hold: int = 1
    k_lock_put: int = 1           # 의무보유 기간에 조기상환청구도 막는가 — 1 막는다(계약 정의) / 0 전환만 막는다
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
    pc_order: int = 0             # 0 투자자 풋 우선 / 1 발행자 콜 우선 — 조기상환과 매도청구 사이만 정한다
    k_conv_resp: int = 1          # 매도청구 통지 뒤 전환 대응 — 1 전환할 수 있다(전환이 콜보다 먼저) / 0 없다(콜이 전환보다 먼저)
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
    # 풋 분리 판단 — 「행사금액이 상각후원가와 거의 같은가」(1109 B4.3.5(5)(가)).
    # 기준서는 수치를 정하지 않는다. 비교기준은 평가자가 정하고(기본 10%), 상각 출발
    # 금액은 자동값(발행금액 100, 별개 콜을 함께 샀으면 + 콜 가치)을 쓰되 실제 회계상
    # 배분액이 다르면 평가자가 넣는다(0 이하면 자동값). 근거는 split_base_why 에 적는다.
    split_tol: float = 0.10
    split_base_in: float = -1.0
    split_base_why: str = ""
    fvpl_whole: int = 0           # 복합계약 전체를 당기손익-공정가치로 지정했는가
    k_method: int = 0
    # 복수의 내재파생상품을 어떤 순서로 묶는가 — 회계정책이다 (한공회 실무사례 30~32쪽).
    # 1 = 접근법 1: 서로 얽힌 권리를 먼저 묶고, 묶음이 주계약과 밀접한지 판단한다.
    # 2 = 접근법 2: 권리마다 분리 여부를 판단한 뒤, 분리 대상끼리 묶는다.
    emb_approach: int = 1
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
    # 행사일이 이자지급일과 겹칠 때 그날 이자를 «따로» 받는가. 계약이 정하는 것이지
    # 앱이 정할 일이 아니다. 0 = 행사금액만 받는다(종전 동작) · 1 = 행사금액 + 그날 이자.
    # k_less_cpn 과는 다른 물음이다 — 그것은 «산식이 기지급분을 빼는가» 이고,
    # 이것은 «행사하는 날의 이자를 위에 더 얹는가» 다.
    p_cpn_add: int = 0            # 조기상환 행사일 이자 별도지급
    k_cpn_add: int = 0            # 매도청구 행사일 이자 별도지급
    face_total: float = 25_000_000_000.0   # 전자등록총액 (원)
    ticker: str = ""              # 종목코드·티커 (주가·변동성 조회용, 비상장이면 빈칸)
    # 불러온 원본 시나리오 JSON 의 지문. 계산에 쓰이지 않으므로 _stamp 은 이 값을 뺀다 —
    # 그래야 「원본 지문」과 「계산 지문」을 나란히 적을 수 있다.
    scen_md5: str = ""
    # ── 평가 관점 ─────────────────────────────────────────────
    # 공정가치는 누가 들고 있든 같다(제1113호 — 시장참여자의 교환가격). 갈리는 것은
    # 회계 단위다. 발행자는 부채·자본을 가르고 요소별로 배분한다(1032·1109 4.3.3).
    # 투자자는 주계약이 금융자산이라 내재파생을 분리하지 않고 전체를 하나로 잰다(4.3.2).
    view: str = "issuer"          # "issuer" 발행자 / "holder" 투자자
    prev_hold: float = -1.0       # 투자자 전기말 장부금액(= 전기말 공정가치, 순액 · 100 기준). 음수면 없음
    d1_pl: int = 0                # 최초 인식 차이(발행자·투자자) — 0 이연(기본) / 1 당기손익(관측 가능한 시장자료만 사용, 1109 B5.1.2A(1))
    d1_reason: str = ""           # 당기손익을 고른 근거 (d1_pl=1 이면 필수) — 계산에 쓰지 않는다
    # ── 표시·기록 전용 (계산에 쓰지 않는다) ──────────────────────
    tranche: str = ""             # 회차 표시 — 분할납입이면 회차마다 따로 평가해 합산한다
    unmod_note: str = ""          # 이 계약에서 평가에 반영하지 않은 권리와 그 이유 (조서 표지)
    base_shares: float = 0.0      # 평가기준일 보통주식수 — 희석 경고용
    dil_shares: float = 0.0       # 전환 시 늘어나는 보통주식수 — 희석 경고용
    s0_src: str = ""              # 평가기준일 주가의 출처 ("야후 085660.KQ 2024-06-28 종가"). 빈칸 = 직접 입력
    # 조회 결과를 그대로 남긴다 — 「무엇을 요청했고 무엇을 받았는가」가 조서에 있어야
    # 나중에 분할·병합을 의심할 때 되짚을 수 있다. 빈칸이면 직접 입력이다.
    s0_date: str = ""             # 실제로 쓴 거래일 (평가기준일이 휴장이면 직전 거래일)
    s0_raw: float = -1.0          # 그날의 «원주가» (auto_adjust=False)
    s0_adj: float = -1.0          # 그날의 «수정주가» (auto_adjust=True). 둘이 다르면 조정사건이 있었다
    s0_splits: str = ""           # 야후가 기록한 분할 이력. "" 미조회 · "없음" 기록 없음 · 그 외 목록
    rate_mode: str = "direct"      # direct 직접 입력 · rating 두 등급 보간
                                   # (옛 "pick" 은 derive() 가 direct 로 옮긴다)
    cr_src: str = ""               # 위험 곡선을 어디서 가져왔는지 (조서에 적는다)     # direct 곡선 직접 / rating 등급 보간
    rt_a: str = "BBB+"            # 인풋 곡선 A 등급
    rt_b: str = "BBB-"            # 인풋 곡선 B 등급
    rt_tgt: str = "BBB0"          # 평가대상 등급
    cr_curve_b: list = field(default_factory=list)
    rvol_rating: str = ""         # BDT σ 를 뽑은 시계열의 등급 (빈칸 = 직접 입력·단일 파일)
    rvol_tenor: float = -1.0      # 그 시계열의 만기 (년). 만기 보간이면 잔존만기, 한 열이면 그 만기. 음수 = 모름
    rvol_how: str = ""            # σ 산출 근거 한 줄 (조서 가정 시트에 적는다)
    # 행사금액에서 이미 지급한 이자·배당을 어떻게 빼는가 — 계약 문언이 정한다.
    #   1 이자를 붙여 공제 — 「보장수익률이 연 X% 가 되도록」. 받은 이자를 보장수익률로
    #     굴린 금액을 뺀다. 투자자 수익률이 정확히 보장수익률이다 (종전 동작 · 기본)
    #   2 받은 금액만 공제 — 「연복리 X% 를 적용한 금액에서 지급된 배당금을 공제」.
    #     행사일까지 도래한 지급 회차의 명목 합계만 뺀다
    #   0 공제하지 않음 — 순수 복리 (차바이오텍 매도청구 101.5084%)
    # 옛 시나리오의 k_less_cpn 은 1/0 이라 뜻이 그대로다.
    k_less_cpn: int = 1           # 매도청구금액
    p_less_cpn: int = 1           # 조기상환금액
    m_less_cpn: int = 1           # 만기상환금액
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


# 이미 지급한 이자·배당을 행사금액에서 빼는 방식. 값은 Terms 의 *_less_cpn.
DED_LBL = {1: "이자를 붙여 공제", 2: "받은 금액만 공제", 0: "공제하지 않음"}
DED_TXT = {
    1: "보장수익률 복리 − 기 지급 이자·배당을 보장수익률로 굴린 금액 (투자자 수익률 = 보장수익률)",
    2: "보장수익률 복리 − 기 지급 이자·배당의 명목 합계 (「지급된 배당금을 공제」 문언)",
    0: "보장수익률 순수 복리 (지급분 차감 없음)"}
DED_HELP = ("계약서가 이미 준 이자·배당을 어떻게 빼는지 고릅니다.\n\n"
            "**이자를 붙여 공제** — 「보장수익률이 연 X% 가 되도록」. 받은 이자를 보장수익률로 "
            "다시 굴렸다고 보고 뺍니다. 투자자 수익률이 정확히 보장수익률입니다 (전환사채 "
            "발행조건의 통상적 의미).\n\n"
            "**받은 금액만 공제** — 「연복리 X% 를 적용한 금액에서 지급된 배당금을 공제」. "
            "행사일까지 받은 이자·배당의 합계만 뺍니다. RCPS 계약서에 흔합니다.\n\n"
            "**공제하지 않음** — 순수 복리. 이자·배당은 따로 받습니다.")


def paid_count(mo: float, ipay: float) -> int:
    """발행일부터 ``mo`` 개월까지 도래한 이자·배당 지급 회차 수.

    지급일은 발행일 + ipay, + 2·ipay, … 다 (pay_offset 과 같은 기준). 행사일 당일의
    지급분도 센다 — 그날 지급된 것이다.
    """
    if ipay <= 0 or mo <= 0: return 0
    return int(math.floor(mo/ipay + 1e-9))


def ded_prem(t_year: float, g: float, c: float, m: int, ded: int = 1,
             mo: float = 0.0, ipay: float = 0.0) -> float:
    """상환할증금률 — 이미 지급한 이자·배당을 빼는 방식(ded)에 따라.

        1  이자를 붙여 공제   accrue_rate(t, g, c, m)                    (종전)
        2  받은 금액만 공제   accrue_rate(t, g, 0, m) − c·ipay/12·지급회차
        0  공제하지 않음     accrue_rate(t, g, 0, m)

    어느 방식이든 할증금은 0 밑으로 내려가지 않는다 (accrue_rate 와 같은 이유).
    """
    ded = int(ded)
    if ded == 1: return accrue_rate(t_year, g, c, m)
    base = accrue_rate(t_year, g, 0.0, m)
    if ded == 2: return max(0.0, base - c*ipay/12*paid_count(mo, ipay))
    return base


# 회차별 역산 보장수익률이 이보다 벌어지면 한 수익률로 만든 표가 아니다 — 오타를 의심한다
SCHED_Y_TOL = 0.005


def implied_yield(prem: float, t_year: float, c: float, m: int):
    """할증금률에서 **보장수익률을 되찾는다** — ``accrue_rate`` 의 역함수.

    계약이 회차별 금액을 확정 숫자로 주면 보장수익률은 화면에 적을 자리가 없다. 그러나
    그 표가 계약서의 몇 %와 맞는지는 확인해야 한다 — 「연 5% 분기복리」라고 쓰인 계약의
    표에서 4.2% 가 나오면 표를 잘못 옮긴 것이다. 그래서 역산해 보여 준다.

    ``accrue_rate`` 는 ``g ≤ c`` 에서 0 으로 끊기므로 **``g ≥ c`` 구간에서만** 단조증가한다.
    그 구간에서 이분법으로 찾는다 (BDT 기준금리·발행가 역산과 같은 방식). 단리(``m=0``)는
    닫힌 해가 있다.

    돌려주는 것 — 연 보장수익률, 또는 되찾을 수 없으면 ``None`` (할증금이 0 이하이거나
    기간이 0 이면 어떤 수익률도 그 금액을 설명하지 못한다).
    """
    if t_year <= 0 or prem <= 1e-12: return None
    m = int(m)
    if m <= 0: return prem/t_year + c                  # 단리 — (g − c)·t = prem
    lo, hi = max(c, 0.0), max(c, 0.0) + 2.0
    if accrue_rate(t_year, hi, c, m) < prem: return None   # 연 200% 로도 못 미친다
    for _ in range(200):
        mid = (lo + hi)/2
        if accrue_rate(t_year, mid, c, m) < prem: lo = mid
        else: hi = mid
    return (lo + hi)/2


def ded_implied(prem: float, t_year: float, c: float, m: int, ded: int = 1,
                mo: float = 0.0, ipay: float = 0.0):
    """``ded_prem`` 의 역함수 — 할증금률에서 보장수익률을 되찾는다.

    「받은 금액만 공제」는 명목 지급분을 되돌려 더하면 순수 복리가 된다.
    """
    ded = int(ded)
    if ded == 1: return implied_yield(prem, t_year, c, m)
    if ded == 2: prem = prem + c*ipay/12*paid_count(mo, ipay)
    return implied_yield(prem, t_year, 0.0, m)


def sched_lock_note(rows: list, c: float, m: int, ded: int = 1, ipay: float = 0.0) -> list:
    """행사금액표가 있을 때 산식 칸 아래에 붙일 캡션 — 문구 목록.

    계산에 쓰이지 않는 칸이 열려 있으면 이용자는 그 값이 쓰인다고 오해한다. 칸은
    화면에서 잠그고, 대신 **그 표가 암시하는 보장수익률**을 적어 계약서와 대조하게 한다.
    """
    out = ["**행사금액표가 정합니다** — 이 칸은 계산에 쓰지 않습니다. "
           "표를 지우면 다시 열립니다."]
    got = sched_yield(rows, c, m, ded, ipay)
    if got is None:
        out.append("표에 할증금이 없어(금액 ≤ 100) 보장수익률을 역산할 수 없습니다.")
    else:
        rep_, lo, hi = got
        out.append(f"이 표가 암시하는 보장수익률 **연 {rep_*100:,.2f}%** "
                   f"(회차별 {lo*100:,.2f}~{hi*100:,.2f}%). 계약서 수치와 대조하십시오."
                   + ("  회차별로 크게 갈립니다 — 표를 잘못 옮겼는지 확인하십시오."
                      if hi - lo > SCHED_Y_TOL else ""))
    return out


def sched_yield(rows: list, c: float, m: int, ded: int = 1, ipay: float = 0.0):
    """행사금액표가 **암시하는 보장수익률** — (대표값, 최소, 최대) 또는 None.

    회차마다 역산한다. 한 보장수익률로 만든 표라면 회차별 값이 거의 같고, 벌어지면
    표를 잘못 옮겼거나 계약이 회차마다 다른 수익률을 쓴 것이다 — 값싼 검산이다.

    대표값은 **마지막 회차**다. 기간이 길어 소수점 반올림에 가장 둔감하다. 기간은
    계약 개월÷12 로 센다 — 표는 계약서의 회차표이므로 계약이 세는 방식을 따른다.
    """
    got = [(mo, ded_implied(v/100 - 1, mo/12, c, m, ded, mo, ipay)) for mo, v in rows if mo > 0]
    got = [(mo, g) for mo, g in got if g is not None]
    if not got: return None
    ys = [g for _, g in got]
    return (got[-1][1], min(ys), max(ys))


def xl_prem(g: str, c: str, m: str, yr: str) -> str:
    """상환할증금률의 엑셀 식. 엔진의 accrue_rate 와 같은 갈래를 탄다.

    복리 횟수 셀이 0 이면 단리 (g − c)·t 다. 보장수익률이 0 이면 엔진처럼 g → 0 극한 (g − c)·t 다
    (예전에는 g 로 나눠 0% 계약의 보조 행이 #DIV/0! 이었다). 거짓 갈래도 파서가 훑고 지나가므로
    나눗셈에 MAX(1, m) · MAX(1E-12, g) 를 씌워 0 으로 나누는 일이 없게 한다.
    """
    mm = f"MAX(1,{m})"
    return (f"IF(OR({m}<=0,{g}<=1E-12),MAX(0,({g}-{c})*{yr}),"
            f"MAX(0,({g}-{c})/MAX(1E-12,{g})*((1+{g}/{mm})^({mm}*{yr})-1)))")


def xl_ded_prem(g: str, c: str, m: str, yr: str, ded: str, mo: str, ipaym: str) -> str:
    """``ded_prem`` 의 엑셀 식. 공제 방식 셀(ded)이 1 / 2 / 0 을 고른다.

    지급 회차는 INT(개월 ÷ 지급주기) — paid_count 와 같이 행사일 당일 지급분까지 센다.
    """
    cnt = f"INT({mo}/MAX(1E-9,{ipaym})+1E-9)"
    return (f"IF({ded}=1,{xl_prem(g, c, m, yr)},"
            f"MAX(0,{xl_prem(g, '0', m, yr)}-IF({ded}=2,{c}*{ipaym}/12*{cnt},0)))")


def node_dates(tm: "Terms", n: int, dt_: float) -> list:
    """노드 날짜 — 평가기준일 + 스텝 × 구간 일수(반올림). 조서 트리 1행 날짜와 같다."""
    db = dt.date.fromisoformat(tm.d_base)
    day = dt_*365                                   # 한 스텝의 일수
    return [db + dt.timedelta(days=round(i*day)) for i in range(n+1)]


def date_tol_days(dt_: float) -> int:
    """계약일과 «같은 날» 로 보는 노드의 허용 일수 — 노드 간격의 4분의 1, 최소 1일 · 최대 5일.

    노드는 한 달을 30.4일로 잡아 놓으므로 달력 기준일과 하루이틀 어긋난다. 그만큼은 같은
    날로 본다. 노드 하나를 통째로 앞당길 만큼은 못 된다. 월 간격 5일 · 2주 간격 3일 · 주 간격 1일.
    """
    return min(5, max(1, int(dt_*365//4)))


# 행사일을 노드에 배정하는 규칙 — 화면·값 조서·수식 조서가 같은 문장을 쓴다.
EXDATE_RULE = ("계약일마다 «계약일 이후 첫 노드» 에 배정합니다. 다만 노드 날짜는 한 달을 30.4일로 "
               "잡아 달력과 하루이틀 어긋나므로, 노드가 계약일보다 허용 일수 안에서 앞서면 같은 날로 "
               "보고 그 노드를 씁니다. 허용 일수는 노드 간격의 4분의 1(최소 1일 · 최대 5일 — 월 간격 "
               "5일, 2주 간격 3일, 주 간격 1일)입니다. 행사기간이 끝나는 날은 거꾸로 «계약일 뒤 허용 "
               "일수 안의 마지막 노드» 까지 엽니다. 행사금액은 노드 날짜가 아니라 계약일의 경과기간으로 "
               "계산하고, 할인은 노드 날짜로 합니다.")


def step_mapper(tm: "Terms", n: int, dt_: float):
    """계약상 월(발행일 기준)을 노드 번호로 바꾸는 두 함수를 만든다.

    스텝을 반올림으로 잡으면 계약일 **전**의 노드에서 행사가 열려 옵션이
    과대평가된다. 그래서 노드의 실제 날짜를 계약일과 직접 견준다.

        lo(m)  계약일 **이후** 첫 노드   — 행사일·행사기간 시작에 쓴다
        hi(m)  계약일 **이전** 마지막 노드 — 행사기간 종료에 쓴다

    «이후 · 이전» 은 허용 일수(date_tol_days)만큼 너그럽다 — 노드가 계약일보다 그 안에서
    앞서면(lo) 또는 뒤지면(hi) 같은 날로 본다. 규칙 문장은 EXDATE_RULE 이다.

    노드가 하나도 조건을 만족하지 않으면 lo 는 n+1, hi 는 −1 을 돌려주어
    그 구간이 비어 있음을 알린다.
    """
    di = dt.date.fromisoformat(tm.d_issue)
    nd = node_dates(tm, n, dt_)
    tol = dt.timedelta(days=date_tol_days(dt_))

    def cd(m):                                      # 발행일 + m 개월
        return months_to_date(di, m)

    def lo(m):
        c = cd(m) - tol
        return next((i for i, x in enumerate(nd) if x >= c), n+1)

    def hi(m):
        c = cd(m) + tol
        return next((i for i in range(n, -1, -1) if nd[i] <= c), -1)
    return lo, hi


EXDATE_COLS = ["권리", "구분", "계약일", "적용 노드", "노드 날짜", "차이 (일)", "비고"]


def exercise_date_rows(tm: "Terms") -> list:
    """계약상 행사일과 실제로 쓴 노드의 대조표 — 화면 · 값 조서 · 수식 조서가 같은 표를 쓴다.

    줄 = (권리, 구분, 계약일, 적용 노드, 노드 날짜, 차이 일수, 비고). 차이 = 노드 날짜 − 계약일.
    음수면 노드가 계약일보다 앞선다(허용 일수 안이라 같은 날로 본 것). 기간 중 언제든지 행사하는
    권리는 기간의 첫 노드와 마지막 노드만 싣는다. 격자가 성겨 앞 회차와 같은 노드에 떨어진 회차는
    «격자에서 빠짐» 으로 적는다. 주주간계약은 풋·콜 두 권리를 같은 규칙으로 싣는다.
    """
    n = max(1, int(tm.n)); dt_ = tm.T/n
    nd = node_dates(tm, n, dt_)
    tol = date_tol_days(dt_)
    di = dt.date.fromisoformat(tm.d_issue)
    lo, hi = step_mapper(tm, n, dt_)
    EA = exercise_amounts(tm, n, dt_)
    el = float(tm.elapsed_m)
    rows = []

    def put_row(right, kind, m, i, side="lo"):
        cd = months_to_date(di, m)
        if i is None or not (0 <= i <= n):
            rows.append((right, kind, cd.isoformat(), None, "—", None, "격자 밖 — 이 권리는 격자에서 열리지 않는다"))
            return
        dd = (nd[i] - cd).days
        if m < el - 1e-6 and side == "lo":
            note = "평가기준일 전에 이미 시작 — 첫 노드(평가기준일)부터 연다"
        elif dd == 0:
            note = "같은 날"
        elif side == "lo":
            note = (f"노드가 {-dd}일 앞섬 — 허용 {tol}일 안이라 같은 날로 봄" if dd < 0 else
                    f"계약일 뒤 첫 노드 ({dd}일 뒤)")
        else:
            note = (f"노드가 {dd}일 늦음 — 허용 {tol}일 안이라 같은 날로 봄" if dd > 0 else
                    f"계약일 앞 마지막 노드 ({-dd}일 앞)")
        rows.append((right, kind, cd.isoformat(), i, nd[i].isoformat(), dd, note))

    def window(right, s_, e_):
        if s_ > e_: return
        i0, i1 = max(lo(s_), 0), min(hi(e_), n)
        if i0 > i1:
            put_row(right, "기간 시작", s_, None); return
        put_row(right, "기간 시작", s_, i0, "lo")
        put_row(right, "기간 종료", e_, i1, "hi")

    def dated(right, dates, drop, cont, s_, e_, rows_, f=None):
        if cont:
            window(right, s_, e_); return
        ms = [m for m, _ in rows_] if rows_ else []
        if not ms and s_ <= e_:
            f = float(f if f is not None else (tm.p_f if right.startswith("조기") else tm.k_f))
            k = 0
            while f > 0 and s_ + k*f <= e_ + 1e-6:
                ms.append(s_ + k*f); k += 1
        past = [m for m in ms if m < el - 1e-6]
        if past:
            rows.append((right, f"지난 회차 {len(past)}개", months_to_date(di, past[-1]).isoformat() + " 까지",
                         None, "—", None, "평가기준일 전에 지나 격자에 없다"))
        k = 0
        for i, m in sorted(dates.items(), key=lambda x: x[1]):
            k += 1
            put_row(right, f"{k}회차", m, i, "lo")
        for m in drop:
            cd = months_to_date(di, m)
            rows.append((right, "격자에서 빠짐", cd.isoformat(), None, "—", None,
                         "노드가 성겨 앞 회차와 같은 노드에 떨어졌다 — 노드를 촘촘히 하면 담긴다"))

    if is_sha(tm):
        for right, s_, e_, f_, on in (("풋", tm.sha_put_s, tm.sha_put_e, tm.sha_put_f,
                                       tm.sha_put_s <= tm.sha_put_e),
                                      ("콜", tm.sha_call_s, tm.sha_call_e, tm.sha_call_f,
                                       tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e)):
            if not on: continue
            d_, dr_, c_ = exercise_dates(tm, n, dt_, float(s_), float(e_), float(f_))
            dated(right, d_, dr_, c_, float(s_), float(e_), [], f=float(f_))
        return rows
    # 전환권(신주인수권) — 기간 중 언제든지
    if tm.cv_s <= tm.cv_e:
        window("신주인수권 행사" if is_bw(tm) else "전환권", float(tm.cv_s), float(tm.cv_e))
    if tm.p_s <= tm.p_e or EA["p_rows"]:
        dated("조기상환청구권", EA["p_dates"], EA["p_drop"], EA["p_cont"], float(tm.p_s), float(tm.p_e), EA["p_rows"])
    _kname = "발행자 상환권" if issuer_redeem(tm) else "매도청구권"
    if tm.k_w > 0 and (tm.k_s <= tm.k_e or EA["k_rows"]):
        dated(_kname, EA["k_dates"], EA["k_drop"], EA["k_cont"], float(tm.k_s), float(tm.k_e), EA["k_rows"])
        # 의무보유 종료일도 계약일이다 — 매도청구와 같은 노드 규칙으로 배정한다 (lock_end_step).
        _L = lock_end_step(tm, n, dt_)
        if _L >= 0 and tm.k_lock > el + 1e-6:
            cd = months_to_date(di, float(tm.k_lock))
            dd = (nd[_L] - cd).days
            rows.append(("의무보유", "종료 (이 노드까지 전환" + ("·조기상환" if int(tm.k_lock_put) else "")
                         + " 제한)", cd.isoformat(), _L, nd[_L].isoformat(), dd,
                         ("같은 계약일의 마지막 매도청구 노드까지 묶음" if _L > hi(float(tm.k_lock)) else
                          "같은 날" if dd == 0 else
                          f"계약일 앞 마지막 노드 ({-dd}일 앞)" if dd < 0 else
                          f"노드가 {dd}일 늦음 — 허용 {tol}일 안이라 같은 날로 봄")))
    return rows


def exdate_head(tm: "Terms") -> str:
    """대조표 위에 붙이는 한 줄 — 노드 간격과 허용 일수."""
    n = max(1, int(tm.n)); dt_ = tm.T/n
    return (f"노드 간격 {dt_*365:,.1f}일 · 허용 {date_tol_days(dt_)}일 · 평가기준일 {tm.d_base} "
            f"(노드 0). 차이 = 노드 날짜 − 계약일.")


def pay_steps(tm: "Terms", n: int, dt_: float) -> dict:
    """이자·배당 지급일 ``{스텝: 회수}``.

    지급일은 계약이 정한다 — 발행일 + 지급주기 × k. 날짜마다 행사일과 같은 규칙
    (계약일 이후 첫 노드 · 허용 일수 안에서 앞선 노드는 같은 날 — EXDATE_RULE)으로 배정하므로 격자 간격이 달라도 지급 **횟수** 는 계약대로다.
    스텝 수를 주기로 세면(MOD) 격자에 따라 한 번 더 또는 덜 지급한다(5년 분기 이표를
    2주 격자로 세면 21회). 격자가 지급주기보다 성겨 두 회차가 한 노드에 모이면 그
    노드에서 회수만큼 지급한다. 평가기준일 당일 이전 회차는 이미 지급했다.
    """
    if tm.ipay <= 0 or eff_cpn(tm) <= 0: return {}
    lo, _ = step_mapper(tm, n, dt_)
    end = tm.elapsed_m + float(getattr(tm, "rem_m", 0.0) or tm.T*12)
    out, k = {}, math.floor(tm.elapsed_m/tm.ipay + 1e-9) + 1
    while k*tm.ipay <= end + 1e-6:
        i = min(lo(k*tm.ipay), n)
        if i >= 1: out[i] = out.get(i, 0) + 1
        k += 1
    return out


def pay_offset(tm: "Terms", st_lo) -> int:
    """평가기준일 뒤 첫 이자·배당 지급 노드.

    지급일은 계약(발행일) 기준으로 확정된다. 평가기준일에서 다시 세면 결산
    평가에서 지급일이 통째로 밀려 지급 횟수가 하나 사라진다. 리픽싱의 첫
    조정일(``refix_steps``)과 같이 발행일에서 센다.
    """
    if tm.ipay <= 0: return 1
    return st_lo(tm.ipay*(math.floor(tm.elapsed_m/tm.ipay) + 1))


def months_to_date(d_issue, m: float) -> dt.date:
    """발행일 기준 m 개월 → 날짜. 꽉 찬 달은 달력으로, 남는 소수 달은 30.4375일로 센다.

    step_mapper 가 계약 개월을 날짜로 옮길 때 쓰는 **유일한** 식이다. 사이드바의 날짜
    입력도 이 식의 역함수(date_to_months)로 개월을 만들므로, 날짜로 넣든 개월로 넣든 같은
    노드에 떨어진다.
    """
    di = dt.date.fromisoformat(d_issue) if isinstance(d_issue, str) else d_issue
    k = int(math.floor(m)); fr = m - k
    d = _add_months(di, k)
    return d + dt.timedelta(days=round(fr*30.4375)) if fr else d


def date_to_months(d_issue, d) -> float:
    """날짜 → 발행일 기준 개월. months_to_date 의 역함수 — 왕복하면 같은 날이 나온다.

    꽉 찬 달 k 는 _add_months(발행일, k) ≤ d 인 최대 정수, 남는 날수는 30.4375 로 나눈다.
    정수 달이면 소수 없이 딱 떨어진다.
    """
    di = dt.date.fromisoformat(d_issue) if isinstance(d_issue, str) else d_issue
    d = dt.date.fromisoformat(d) if isinstance(d, str) else d
    if d <= di: return 0.0
    k = (d.year - di.year)*12 + (d.month - di.month)
    while _add_months(di, k) > d: k -= 1
    while _add_months(di, k + 1) <= d: k += 1
    rem = (d - _add_months(di, k)).days
    return float(k) if rem == 0 else round(k + rem/30.4375, 6)


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
COMPAT_KKIND = ("**제3자 기특정 콜옵션은 별도의 금융상품입니다.** 발행 시 제3자가 정해져 있어 거래상대방이 "
                "발행자가 아니므로 내재파생에 넣을 수 없습니다 (문단 4.3.1). 회계 처리를 «별도 금융상품» 으로 되돌렸습니다.")
COMPAT_KHOLDER = ("**제3자 사전 특정 콜은 제3자가 행사하는 콜입니다.** «제3자 지정 불가» 와 함께 둘 수 없어 "
                  "콜 권리자를 «발행 시 정해진 제3자» 로 맞췄습니다. 발행회사만 행사하는 콜이면 콜 권리자를 "
                  "«발행회사 본인만» 으로 고르십시오.")
COMPAT_PSEP = ("조기상환권 처리를 «주계약에 포함(분리하지 않음)» 으로 둘 수 없습니다. 분리 정책이 "
               "**접근법 1**(서로 얽힌 권리를 먼저 묶고 판단 — 한공회 실무사례 30~31쪽)이라, 전환권이 부채이거나 "
               "매도청구권을 내재파생에 포함하면 조기상환권은 그 파생상품과 **묶어서 하나의 복합내재파생상품**"
               "으로 주계약에서 떼어 냅니다 (문단 B4.3.4) — 조기상환권 처리를 «분리» 로 두었습니다. "
               "조기상환권을 따로 판단하려면 분리 정책을 **접근법 2**(권리마다 판단한 뒤 분리 대상끼리 "
               "묶기 — 30~32쪽)로 바꾸십시오.")
COMPAT_BDT = ("BDT 금리격자는 전환권을 **자본**으로 두고 **TF** 를 쓸 때만 켤 수 있습니다. "
              "자본이면 전환권대가가 잔여라 부채요소만 바꿔도 배분이 성립하지만, 부채이면 "
              "복합내재파생을 전체로서 재야 해서 전체 가치까지 함께 손봐야 합니다.")


# 알려진 한계 — (제목, 설명, 실려야 하는 곳). 화면 검산 탭 · 조서 99_모형검증 · README 「한계」 가
# 모두 이 표에서 나온다. 시험(기능목록.py)이 README 에 제목이 있는지 확인한다.
# 매도청구권 평가체계 버전. 1 = PR #13 이전(유무가치비교법 기본, 행사가 분해 없음),
# 2 = 옵션차익·TF식 + 본문 4.3.3 전환확률 분해, 의무보유 세 값,
# 3 = 지금(매도청구 통지 뒤 전환 대응 k_conv_resp 를 따로 받고, 세 평가방법이 같은 사건 규칙 —
# 상장·만기일·행사일 이자 — 을 쓴다). 시나리오 JSON 에 「_schema」로 적어 두고, 옛 파일을 열면
# 그 사실을 알려 준다.
SCHEMA_VER = 3

# 기준선 — 화면·조서 99_모형검증·tests/run_all.py 가 «같은 원본» 을 본다.
# 종전에는 조서에 숫자를 직접 박아 두어, 값이 움직인 뒤에도 옛 숫자가 실려 나갔다.
BASELINE_BASE = dict(주계약=37.5208, 부채요소=73.1837, 전체=116.3743, 매도청구권=13.5251)
BASELINE_TEXT = " · ".join(f"{k} {v:,.4f}" for k, v in BASELINE_BASE.items())

MODEL_LIMITS = (
    ('복합내재파생이 음수',
     '발행자 상환권이 전환권보다 크면 부채 갈래 묶음이 음수 (대신증권 −9.0050)',
     ('화면 배분표 캡션', 'docs/사례_대신증권_RCPS.md', '조서 회계처리 시트')),
    ('의무보유 물량은 한 번에 풀린다',
     '콜 한도와 의무보유 물량이 다른 계약(「콜 한도 70% · 미전환 의무보유 30%」)은 콜 대상을 묶인 몫과 '
     '묶이지 않은 몫으로 나눠 따로 잰다(k_lock_w). 「30% → 20% → 10% 단계적 해제」처럼 물량이 시간에 '
     '따라 갈리는 계약과, 콜 대상 밖 물량의 의무보유는 담지 못한다. 기간(k_lock)과 제한 '
     '권리(k_lock_put)는 표현된다',
     ('화면 매도청구권 캡션', 'README 「한계」', 'docs/의사결정규칙.md §29')),
    ('부분 매도청구는 뿌리에서 비율로만 반영',
     '뿌리값에 k_w 를 곱한다. 행사금액이 물량에 비례하고 한도가 전체 기간에 하나뿐이면 '
     '발행자는 이득이 나는 순간 한도를 다 쓰는 것이 언제나 최선이므로 이 처리는 근사가 '
     '아니라 정확하다. 담지 못하는 것은 기간별 세부한도가 붙은 계약(「전체 30%인데 매년 '
     '10%씩만」)이다 — 남은 한도가 상태변수가 되어 상태확장이 필요하다',
     ('화면 매도청구권 캡션', 'README 「한계」', 'docs/의사결정규칙.md §29')),
    ('배당가능이익 상환 제약은 연도별 고정 추정치로만 반영',
     '넣지 않으면 계약상 상환일에 즉시 상환된다고 본다. 넣으면 발생연도별 고정값으로 청구 시점마다 지급 일정을 '
     '정한다 — 주가와 이익의 연동, 해마다 한도만큼만 나눠 청구하는 전략, 동순위 상품의 개별 판단(그 재원 연도 안에 '
     '청구 기간이 하루라도 있으면 그 해 함께 청구하고, 평가대상 청구 전에 청구 기간이 끝난 상품은 그 전에 상환을 마쳤다고 본다)은 반영하지 않는다. 넘긴 상환금은 청구일부터 1년 단위의 가장 가까운 계산 시점에 갚고, 만기 '
     '뒤 지급분은 마지막 구간의 위험 선도이자율로 할인한다. 배당 부족(이익 < 우선배당)은 따로 반영하지 않는다',
     ('UNMODELLED_NOTE (조서 표지)', 'README', 'docs/입력안내_RCPS.md')),
    ('전환 희석 미반영',
     '기초주가를 받은 그대로 쓴다. 전환으로 늘어나는 주식수와 사라지는 부채를 주가에 되먹이지 '
     '않는다 — 희석 반영 주당가치를 밖에서 산정해 넣어야 한다. 보통주식수·전환 시 증가 '
     '주식수를 넣으면 비율이 10% 를 넘을 때 경고한다',
     ('validate() 경고', '화면 기초주가 칸', 'README', 'docs/입력안내_RCPS.md')),
    ('회사 행위로 조정되는 전환가액(희석방지 조항) 미반영',
     '저가 신주발행·무상증자·합병처럼 회사의 결정으로 일어나는 조정은 주가 격자로 표현할 수 '
     '없다. 주가만 보는 리픽싱(정기 · 언제든지)과 IPO 공모가 연동만 반영한다',
     ('화면 리픽싱 도움말', '「평가에 반영하지 않은 권리」 칸', 'README')),
    ('IPO 는 가정 비교이지 PWERM 이 아니다',
     '상장 시점·공모가는 확률분포가 아니라 가정',
     ('UNMODELLED_NOTE', 'README', '화면 IPO 캡션')),
    ('자기신용위험 변동분(5.7.7) 분해 안 함',
     '전체 FVPL 지정 후속측정에서 OCI 몫을 나누지 않는다',
     ('UNMODELLED_NOTE', 'validate() 경고', '조서 상각표 자리 FVPL_NOTE', 'docs/의사결정규칙.md §13')),
    ('리픽싱 근사법(경로가중 등)은 근사',
     'E[1/K] ≠ 1/E[K]. 노드마다 전환가액을 여럿 들고 가는 정확법(상태확장)은 값 조서와 수식 조서를 '
     '같게 만들 수 없어 앱에서 뺐다 — 근사 방법 세 가지 중 하나를 쓴다',
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
    ('SHA: Drag/Tag/ROFR 미지원',
     '동반매도·우선매수권은 모형에 넣지 않는다. 연도·물량·가격이 다른 회차는 회차별 표로 한 줄씩 넣는다',
     ('docs/입력안내_주주간계약.md',)),
    ('SHA: 상대 권리 소멸은 같은 회차 안에서만',
     '회차 사이의 소멸·우선순위·미행사 물량 이월, 행사일부터 대금 지급일까지의 시차는 반영하지 않는다. 같은 주식 물량은 두 권리자가 소멸 권리까지 보고 행사를 판단하고(연계 판단), 풋만·콜만 남는 물량은 따로 평가해 더한다. 수량이 다르면 같은 주식 물량을 입력해야 하며, 풋 의무자가 연대인 계약의 연계 판단은 지원하지 않는다',
     ('화면 «이 모델이 다루지 않는 계약 조건»', 'docs/입력안내_주주간계약.md')),
    ('SHA: 실적 연동 행사가격은 고정값으로',
     '추정 재무수치로 산식의 가격을 고정해 평가한다 — 미래 실적의 불확실성은 확률로 반영하지 않으며 매출·손실률 민감도만 제공한다',
     ('화면 실적 연동 행사가격 계산', 'docs/의사결정규칙.md §45')),
    ('SHA: 회차를 넘는 공통 한도 잔량은 지원하지 않음',
     '같은 보유주식·공통 한도를 쓰는 회차 묶음은 합계가 한도 안일 때만 계산한다 — 한 회차의 행사가 다른 회차의 잔량을 줄이는 경로 의존 계약은 막는다',
     ('화면 회차 표 «같은 주식 묶음»', 'docs/입력안내_주주간계약.md')),
    ('SHA: 조건부 물량은 고른 가정 하나만 반영',
     '추가 조건부 물량은 조건 충족·미충족 가정 중 사용자가 고른 것만 평가금액에 넣는다 — 충족 가능성을 확률로 반영하지 않는다',
     ('결과 화면 «조건 충족 전·후 차이»', 'docs/입력안내_주주간계약.md')),
    ('역산이 목표를 정확히 못 맞힐 수 있다',
     '격자 값의 계단 (0.19 등)',
     ('화면 역산 경고', 'docs/의사결정규칙.md §10-1')),
    ('자동전환·상장 강제전환 RCPS 의 전환권이 음수',
     '존속기간 만료 시 자동전환·상장 시 강제전환은 권리가 아니라 의무다. 주가가 낮으면 B2 < B1 이라 「전환권 = B2 − B1」 이 음수다 (분기전수·조합시험이 limit 로 허용)',
     ('화면', '조서')),
    ('강제전환 할인율 효과로 매도청구권이 음수',
     'TF·GS 는 지분을 무위험(전환확률 가중)으로 할인한다. 콜이 전환을 강제하면 부채가 지분으로 바뀌어 할인이 가벼워지고 전체 가치가 오를 수 있다 → 유무가치비교법 매도청구권 < 0. 값을 0 으로 덮지 않고 결과 시트 «유무가치비교법 차액의 구성» 에 행사 판정 효과와 할인 방식 효과로 나눠 싣는다. RCPS 발행자 상환권의 부채요소 배분에 쓰는 값(전환권 없는 부채 격자)에는 전환이 없어 이 효과가 생기지 않는다',
     ('화면', '조서', 'README')),
    ('잔여 주계약 ≤ 0 이면 상각표 없음',
     '발행가 100 과 공정가치가 크게 다르면(Day-1 차이) 부채 분류의 잔여 주계약이 0 이하다. 유효이자율이 정의되지 않으므로 상각표를 만들지 않고 그 사실을 적는다 (4단계 F-05)',
     ('화면', '조서')),
    ('주가 basis 검증은 내부 신호뿐 — 분할 이력은 참고',
     '분할·병합·무상증자 여부는 원주가 대 수정주가의 배수와 전환가 계열의 정합성으로만 판정한다. '
     '야후의 분할 이력은 한국 종목 커버리지가 고르지 않고 특히 무상증자를 split 으로 기록하지 '
     '않는 경우가 많아 근거로 쓰지 않는다 — 「기록 없음」이 「사건 없음」이 아니다. '
     '조정사건이 의심되면 공시로 확인해야 한다',
     ('화면 주가 캡션', 'README 「한계」', 'docs/의사결정규칙.md §30·§31')),
    ('주가와 금리를 함께 흔드는 결합격자 미지원',
     '주가 이항격자와 BDT 금리격자를 곱한 2요인 격자를 만들지 않는다. 전환권이 파생상품부채인 '
     'CB 는 주계약과 내재파생 전체를 하나의 복합내재파생으로 재므로 조기상환권을 따로 잴 이유가 '
     '없고, 전환권이 자본이라 조기상환권만 부채요소에 남는 경우에만 BDT 를 쓴다. 두 위험의 '
     '상관을 반영해야 하는 계약은 고도화 대상이다',
     ('README 「한계」', 'docs/의사결정규칙.md §20')),
    ('비분리형 BW 는 신주인수권 조기행사를 상태로 갖지 않는다',
     '자식 노드의 매도청구는 신주인수권이 살아 있다고 보고 정해진다. 부모에서 투자자가 먼저 행사하면 그 콜은 사채만 비싸게 사는 셈이라 콜 있는 격자가 없는 격자보다 커질 수 있다 (매도청구권 < 0). 두 상태 격자로 고치는 것은 산식 변경이라 별도 승인 대상',
     ('화면', '조서', 'README')),
)


HOST_NONPOS_NOTE = ("**잔여 주계약이 0 이하라 상각표를 만들지 않습니다.** 전체 가치가 발행가 100 과 "
                    "크게 달라 파생을 뺀 잔여가 남지 않는 자리입니다 — 최초 인식 시점의 공정가치와 "
                    "거래가격의 차이(Day-1 차이, 제1109호 문단 B5.1.2A)를 먼저 정리하셔야 합니다. "
                    "유효이자율이 정의되지 않으므로 상각표·이자비용 대신 이 문구가 조서에 실립니다.")


COMPAT_DIVBASIS = ("우선배당률을 **액면가 기준**으로 고르셨지만 액면가나 1주당 발행가가 "
                   "비어 있어 환산할 수 없습니다. **발행가 기준**으로 되돌려 계산했습니다 — "
                   "두 값을 넣으십시오.")


def emb_policy(tm: Terms) -> int:
    """복수 내재파생의 분리 정책 — 1 접근법 1(묶고 판단) / 2 접근법 2(각각 판단 후 묶기)."""
    return 2 if int(getattr(tm, "emb_approach", 1)) == 2 else 1


def psep_free(tm: Terms) -> bool:
    """조기상환권 처리(분리 / 주계약에 포함)를 평가자가 고를 수 있는 자리인가.

    접근법 1 은 서로 얽힌 권리를 먼저 묶는다. 전환권이 부채이거나 발행회사만 행사하는
    매도청구권이 내재파생이면 조기상환권은 그 묶음에 딸려 가므로 고를 것이 없다
    (문단 B4.3.4). 접근법 2 는 권리마다 따로 판단하므로 언제나 고를 수 있다.
    """
    if emb_policy(tm) == 2: return True
    return tm.conv_class == "equity" and int(tm.k_sep) != 0


def put_in_host(tm: Terms) -> bool:
    """조기상환권을 주계약에 남기는가(분리하지 않음). 배분·상각표·조서·화면이 같은 판단을 쓴다."""
    return int(tm.p_sep) == 0 and psep_free(tm) and not fvpl_on(tm)


def compat(tm: Terms):
    """지원하지 않는 조합을 찾는다. [(필드, 되돌릴 값, 사유)].

    derive() 가 이 목록대로 되돌리고 ``tm.forced_notes`` 에 남긴다. validate() 가 그것을
    경고로 올리고, 사이드바는 같은 사유로 칸을 잠근다. 주주간계약·BW 전용 강제는
    derive() 안에 따로 있다 — 그것은 «상품에 없는 스위치» 라 사용자가 고른 것이 아니다.
    """
    out = []
    if tm.model == "GS" and int(tm.k_method):
        out.append(("k_method", 0, COMPAT_GS_KMETHOD))
    if int(tm.p_sep) == 0 and not psep_free(tm):
        out.append(("p_sep", 1, COMPAT_PSEP))
    if int(tm.put_bdt) and not (tm.conv_class == "equity" and tm.model == "TF"):
        out.append(("put_bdt", 0, COMPAT_BDT))
    if int(getattr(tm, "k_kind", 0)) == 1 and int(tm.k_sep) == 0:
        out.append(("k_sep", 1, COMPAT_KKIND))
    if int(getattr(tm, "k_kind", 0)) == 1 and not int(tm.k_third) and not is_rcps(tm):
        out.append(("k_third", 1, COMPAT_KHOLDER))
    if (is_rcps(tm) and int(getattr(tm, "div_basis", 0)) == 1
            and not (float(getattr(tm, "issue_px", 0.0)) > 0 and tm.par > 0)):
        out.append(("div_basis", 0, COMPAT_DIVBASIS))
    return out


def derive(tm: Terms) -> Terms:
    """날짜에서 경과기간·잔존기간·노드 수를 계산해 채운다.

    지원하지 않는 조합(compat)은 여기서 되돌린다 — 화면·validate·직접 호출이 같은 값을
    내야 하기 때문이다. 되돌린 내역은 ``forced_notes`` 에 남는다 (두 번째 호출은 이미
    되돌린 뒤라 비어 있으므로 덮어쓰지 않는다).
    """
    # 「표에서 등급 하나 고르기」는 없앴다. 고시표를 올려 「이 곡선 적용」 을 누르면
    # 그 곡선이 직접 입력 칸에 들어오므로 같은 일을 두 번 묻던 갈래였다. 옛 시나리오는
    # cr_curve 를 그대로 들고 있어 직접 입력으로 열면 **같은 곡선·같은 값**이다.
    if tm.rate_mode == "pick": tm.rate_mode = "direct"
    # 상태확장(carry=0)은 없앴다. 옛 평가파일이 들고 있으면 경로가중치로 계산한다.
    if int(tm.carry) not in (1, 2, 3): tm.carry = 1
    _f = compat(tm)
    if _f:
        tm.forced_notes = _f
        for k, v, _ in _f: setattr(tm, k, v)
    di = dt.date.fromisoformat(tm.d_issue)
    db = dt.date.fromisoformat(tm.d_base)
    dm = dt.date.fromisoformat(tm.d_mat)
    tm.elapsed_m = max(0.0, months_between(di, db))
    # 잔존기간은 두 가지로 잰다. **할인**은 Actual/365 (tm.T) 로, **행사금액 산정**은
    # 계약이 세는 개월수(tm.rem_m) 로 한다 — 계약서가 「36개월 보장수익률」이라고
    # 쓰면 그 36개월이지 1,096일÷365 = 3.0027년이 아니다.
    tm.rem_m = max(0.0, months_between(db, dm))
    tm.T = max(1e-6, (dm-db).days/365)
    gap = max(0.25, tm.gap_m)
    tm.n = max(4, int(round(tm.T*365/tm.grid_days if tm.grid_days > 0 else tm.T*12/gap)))
    if is_sha(tm):
        # 주주간계약에는 사채가 없다. 사채·우선주 전용 스위치를 모두 끈다.
        # 지분가치는 100 × 주가 ÷ 주당 인수가액이라 리픽싱도 없다.
        tm.mat_mode = 1; tm.issuer_call = 0; tm.div_mode = 0; tm.div_basis = 0
        tm.view = "issuer"       # 주주간계약은 회계처리 화면에 세 관점이 따로 있다
        tm.rfx_mode = 0; tm.cpn = 0.0; tm.ytm = 0.0
        tm.k_w = 0.0; tm.k_method = 0; tm.k_lock = 0.0
        tm.p_sep = 1; tm.k_sep = 1
        tm.put_bdt = 0
        tm.k_kind = 0; tm.k_split = 0; tm.k_hold = 1
        tm.ipo_conv = 0          # 강제전환이 아니라 풋·콜이 소멸하는 사건이다
        return tm
    if is_bw(tm):
        # 신주인수권부사채는 우선주가 아니므로 RCPS 전용 스위치를 모두 끈다.
        tm.mat_mode = 1          # 만기에 자동전환되는 갈래가 없다
        tm.issuer_call = 0       # 발행자 상환권·제3자 지정은 RCPS 전용 스위치다
        tm.div_mode = 0
        tm.div_basis = 0         # 사채의 표면이자는 권면(= 100) 기준이다
        if int(tm.bw_pay) == 1:
            # 대용납입 — 사채를 권면액만큼 납입에 갈음한다. 사채가 소멸하므로
            # 분리·비분리 구분이 격자에 남기는 흔적이 없다.
            tm.bw_detach = 0
        else:
            # 현금납입형은 노드의 전환확률이 0 (사채와 워런트가 따로 간다)이라 본문식 분해가
            # 행사가를 전부 채권으로 보내 버린다. 부속예제 방식만 쓴다.
            tm.k_split = 0
    if is_rcps(tm):
        ic = int(tm.issuer_call)
        if ic == 2:
            # 제3자 지정 매도청구권 — 발행회사가 **지정하는 제3자**가 인수인이
            # 가진 우선주를 사 가는 권리다. 거래상대방이 발행회사가 아니므로
            # 내재파생이 아니라 **별도의 금융상품**이고 (문단 4.3.1), 기초자산이
            # 전환권까지 붙은 우선주라 전체 격자에서 잰다 — CB 의 매도청구권과
            # 같은 길이다. 한도·의무보유·평가방법을 사용자가 정한다.
            tm.k_third = 1; tm.k_transfer = 0
            tm.k_sep = 1                      # k_kind(지정 가능/기특정)는 사용자 값 그대로
        elif ic == 1:
            # 발행자 상환권 — 거래상대방이 그대로인 내재파생이라 격자 안에서
            # MIN(보유, 상환가액) 으로 누르고, 전체(100%)에 걸린다. 상환청구권과
            # 하나의 복합내재파생으로 묶는다 (문단 B4.3.4).
            tm.k_w = 1.0
            tm.k_method = 0; tm.k_lock = 0.0
            tm.k_third = 0; tm.k_transfer = 0
            tm.k_sep = 0; tm.k_kind = 0; tm.k_hold = 1
        else:
            # 콜이 없다. 상환청구권 하나뿐이라 분리 여부(p_sep)가 산다.
            tm.k_w = 0.0
            tm.k_method = 0; tm.k_lock = 0.0
            tm.k_third = 0; tm.k_transfer = 0
            tm.k_sep = 1; tm.k_kind = 0; tm.k_hold = 1
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


def sha_call_ratio(tm: Terms) -> float:
    """콜 주당 기준가격 ÷ 풋 주당 기준가격(K0). 콜 기준가격을 따로 넣지 않았으면 1."""
    kc = float(getattr(tm, "sha_call_k", -1.0))
    return kc/tm.K0 if kc > 0 and tm.K0 > 0 else 1.0


def sha_qty(tm: Terms):
    """(풋 대상 주식수, 콜 대상 주식수). 넣지 않았으면 계산기준금액 ÷ 주당 기준가격."""
    base = tm.face_total/tm.K0 if tm.K0 > 0 else 0.0
    qp, qc = float(getattr(tm, "sha_put_q", -1.0)), float(getattr(tm, "sha_call_q", -1.0))
    return (qp if qp >= 0 else base), (qc if qc >= 0 else base)


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


def node_decide(cv, pv, kv, hold, kfirst, cresp=True):
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

    ``kfirst`` 가 참이면 발행자 매도청구가 조기상환보다 먼저다 (``Terms.pc_order``). 전환과 매도청구
    사이는 따로 정한다 — ``cresp`` 가 참이면 매도청구 통지를 받아도 전환할 수 있다
    (``Terms.k_conv_resp``, 기본). 네 조합이다.

        풋 우선 · 전환 대응 가능 :  MAX(전환, 풋, MIN(보유, 콜))
        콜 우선 · 전환 대응 가능 :  MAX(전환, MIN(MAX(보유, 풋), 콜))
        풋 우선 · 전환 대응 불가 :  MAX(풋, MIN(MAX(전환, 보유), 콜))
        콜 우선 · 전환 대응 불가 :  MIN(MAX(전환, 풋, 보유), 콜)

    풋 우선이면 전환 대응 여부가 값을 바꾸지 않는다 — 투자자가 그 노드에서 먼저 움직이므로
    매도청구 전에 전환할 수 있다. 앞 두 줄이 종전 식 그대로다.

    **동점 규칙이 값보다 정산확률에 크게 영향을 준다.** 전환과 콜은 허용오차만큼
    앞설 때만 이기고(``+허용오차``), 풋과 보유는 동점이면 이긴다(``−허용오차``). 리픽싱이
    주가로 재설정되는 날에는 전환가치가 정확히 100 이 되어 조기상환금액과 동점이
    되는데, 정해 두지 않으면 부동소수 잡음이 갈라 놓는다. 허용오차는 견주는 두 금액의
    크기에 비례한다(``tie_tol`` — 1,000 이하 1e-9, 그 위로 금액 × 1e-12).

    돌려주는 것은 ``"conv"`` · ``"put"`` · ``"call"`` · ``"hold"`` 넷 중 하나다.
    """
    if kfirst and not cresp:
        # 콜이 풋보다도 전환보다도 먼저다 — MIN(MAX(전환, 풋, 보유), 콜)
        top = max(cv, pv, hold)
        if top > kv + tie_tol(top, kv):     return "call"
        rest = max(pv, hold)
        if cv >= rest + tie_tol(cv, rest):  return "conv"
        if pv >= hold - tie_tol(pv, hold):  return "put"
        return "hold"
    if kfirst:
        # 콜이 없을 때 투자자가 고를 값. 콜은 이것을 눌러 내리는 쪽으로만 쓴다.
        inv = max(hold, pv)
        lo = min(inv, kv)
        if cv >= lo + tie_tol(cv, lo):      return "conv"
        if inv > kv + tie_tol(inv, kv):     return "call"
        if pv >= hold - tie_tol(pv, hold):  return "put"
        return "hold"
    if not cresp:
        # 풋은 콜보다 먼저지만 전환은 콜에 밀린다 — MAX(풋, MIN(MAX(전환, 보유), 콜))
        rest = max(cv, hold)
        lo = min(rest, kv)
        if pv >= lo - tie_tol(pv, lo):      return "put"
        if rest > kv + tie_tol(rest, kv):   return "call"
        if cv >= hold + tie_tol(cv, hold):  return "conv"
        return "hold"
    inner = min(hold, kv)
    rival = max(pv, inner)
    if cv >= rival + tie_tol(cv, rival):    return "conv"
    if pv >= inner - tie_tol(pv, inner):    return "put"
    if hold <= kv + tie_tol(hold, kv):      return "hold"
    return "call"


def xl_tol(x, y):
    """``tie_tol`` 과 같은 허용오차를 엑셀 식으로 — MAX(1e-9, 1e-12 × MAX(|x|, |y|)).

    열리지 않은 매도청구는 엑셀에서 999999 로 적힌다(엔진은 무한대). 그 칸과 견주는 비교는
    어느 허용오차로도 결과가 같다 — 999999 에 붙는 몫은 1e-6 이다.
    """
    return f"MAX({TOL!r},{TOL_REL!r}*MAX(ABS({x}),ABS({y})))"


def xl_decide(cv, pv, kv, hold, kfirst,
              names=("전환", "상환P", "상환C", "보유"), cresp=True, popen=None):
    """``node_decide`` 와 같은 결정을 엑셀 IF 중첩으로 쓴다.

    인자는 숫자가 아니라 **셀 주소 문자열**이다 (``"C12"``, ``"MAX(D5,E5)"`` 처럼
    식이어도 된다). 전환이 없는 갈래는 ``cv=None`` 으로 부르면 그 가지를 빼고,
    ``names`` 로 라벨을 갈아 끼운다.

    파이썬과 엑셀이 같은 판정을 하도록 **한 곳에서** 만든다. 예전에는 같은 패턴을
    트랜치 TF·GS·부채요소·신주인수권부사채 트랜치에 손으로 네 벌 썼다.
    ``cresp`` 는 ``node_decide`` 와 같다 — 거짓이면 매도청구 통지 뒤 전환할 수 없는 계약이다.
    전환이 없는 갈래(``cv=None``)에서는 값이 갈리지 않는다.
    ``popen`` 은 «그 자리에 상환청구가 열려 있다» 는 조건식이다(예: ``"D$7>0"``). 엔진은 열리지 않은
    상환청구를 −무한대로 넘기므로, 엑셀은 상환청구가 이기는 조건에 이 식을 AND 로 건다.
    """
    _c, _p, _k, _h = names
    _P = (lambda cond: f"AND({popen},{cond})") if popen else (lambda cond: cond)
    if cv is not None and not cresp:
        if kfirst:
            # MIN(MAX(전환, 풋, 보유), 콜) — 콜이 풋보다도 전환보다도 먼저다
            top = f"MAX({cv},{pv},{hold})"
            rest = f"MAX({pv},{hold})"
            return (f'IF({top}>{kv}+{xl_tol(top, kv)},"{_k}",'
                    f'IF({cv}>={rest}+{xl_tol(cv, rest)},"{_c}",'
                    f'IF({_P(f"{pv}>={hold}-{xl_tol(pv, hold)}")},"{_p}","{_h}")))')
        # MAX(풋, MIN(MAX(전환, 보유), 콜)) — 풋은 콜보다 먼저, 전환은 콜에 밀린다
        rest = f"MAX({cv},{hold})"
        lo = f"MIN({rest},{kv})"
        return (f'IF({_P(f"{pv}>={lo}-{xl_tol(pv, lo)}")},"{_p}",'
                f'IF({rest}>{kv}+{xl_tol(rest, kv)},"{_k}",'
                f'IF({cv}>={hold}+{xl_tol(cv, hold)},"{_c}","{_h}")))')
    if kfirst:
        inv = f"MAX({hold},{pv})"
        lo = f"MIN({inv},{kv})"
        out = (f'IF({inv}>{kv}+{xl_tol(inv, kv)},"{_k}",'
               f'IF({_P(f"{pv}>={hold}-{xl_tol(pv, hold)}")},"{_p}","{_h}"))')
        if cv is not None:
            out = f'IF({cv}>={lo}+{xl_tol(cv, lo)},"{_c}",{out})'
        return out
    inner = f"MIN({hold},{kv})"
    rival = f"MAX({pv},{inner})"
    out = (f'IF({_P(f"{pv}>={inner}-{xl_tol(pv, inner)}")},"{_p}",'
           f'IF({hold}<={kv}+{xl_tol(hold, kv)},"{_h}","{_k}"))')
    if cv is not None:
        out = f'IF({cv}>={rival}+{xl_tol(cv, rival)},"{_c}",{out})'
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


def xl_value(cv, pv, kv, hold, kfirst, cresp=True):
    """콜이 걸린 노드의 값 — ``node_decide`` 의 네 조합과 같은 MAX·MIN 식 (엔진 GS 의 ``Vg``).

    풋 우선 · 전환 대응 가능 :  MAX(MIN(보유, 콜), 전환, 풋)
    콜 우선 · 전환 대응 가능 :  MAX(전환, MIN(MAX(보유, 풋), 콜))
    풋 우선 · 전환 대응 불가 :  MAX(풋, MIN(MAX(전환, 보유), 콜))
    콜 우선 · 전환 대응 불가 :  MIN(MAX(전환, 풋, 보유), 콜)
    앞 두 줄이 종전 식 그대로다.
    """
    if cresp:
        return (f"MAX({cv},MIN(MAX({hold},{pv}),{kv}))" if kfirst
                else f"MAX(MIN({hold},{kv}),{cv},{pv})")
    return (f"MIN(MAX({cv},{pv},{hold}),{kv})" if kfirst
            else f"MAX({pv},MIN(MAX({cv},{hold}),{kv}))")


# 유효이자율 이분법의 반복 횟수(엑셀). 엔진은 200회를 돈다. 64회면 구간 폭이 (상한+0.99)/2^64 —
# 상한 5 에서 3E-19, 가장 넓힌 상한(약 2천만)에서도 1E-12 라 표시 자릿수에서 엔진과 같은 값이다.
# 한 회에 한 줄이다.
XL_EIR_STEPS = 64


def xl_eir_solver(put, ws, top, host, pv_of, fmts, color):
    """엔진 ``eir_table`` 과 **같은 절차**(상한 넓히기 → 이분법)를 셀 수식으로 쓴다.

    ``pv_of(x)`` 는 이자율 칸 주소 x 를 받아 «그 이자율로 할인한 현재가치» 식을 돌려준다.
    ``host`` 는 상각 출발 금액 칸이다. 돌려주는 것은 (결과 칸 주소, 다음 빈 행). 결과 칸이
    유효이자율이고, 상각표가 이 칸을 본다 — 가정 시트의 입력을 바꾸면 이자율도 다시 구해진다.
    식끼리 한쪽으로만 참조하므로 순환참조가 없다(현재가치 식은 상각표의 이자·잔액을 보지 않는다).
    """
    N2_, N6_ = fmts
    put(ws, top, 2, "유효이자율 계산 과정 — 앱과 같은 이분법 (현재가치 = 상각 출발 금액이 되는 이자율)",
        bold=True)
    put(ws, top+1, 2, "상한을 5 에서 시작해, 그 이자율의 현재가치가 출발 금액보다 크면 4배씩 넓힌다. 하한은 −99%. "
                      f"구간의 가운데 이자율로 현재가치를 구해 출발 금액보다 크면 하한을, 작거나 같으면 상한을 "
                      f"옮긴다 — {XL_EIR_STEPS}회.", color=color, size=9)
    r = top + 2
    put(ws, r, 2, "상한 · 시작", border=True, size=9)
    put(ws, r, 3, 5.0, fmt=N6_, align="right", border=True)
    for k in range(1, 12):
        prev = f"C{r+k-1}"
        put(ws, r+k, 2, f"상한 · 넓히기 {k}", border=True, size=9)
        put(ws, r+k, 3, f"=IF(AND({pv_of(prev)}>{host},{prev}<10000000),{prev}*4,{prev})",
            fmt=N6_, align="right", border=True)
    hi0 = f"C{r+11}"
    h = r + 13
    for j, nm in enumerate(["반복", "하한", "상한", "가운데 이자율", "그 이자율의 현재가치"]):
        put(ws, h, 2+j, nm, bold=True, align="center", border=True, size=9)
    for i in range(XL_EIR_STEPS):
        rr = h + 1 + i
        put(ws, rr, 2, i+1, align="right", border=True, size=9)
        if i == 0:
            put(ws, rr, 3, -0.99, fmt=N6_, align="right", border=True, size=9)
            put(ws, rr, 4, f"={hi0}", fmt=N6_, align="right", border=True, size=9)
        else:
            put(ws, rr, 3, f"=IF(F{rr-1}>{host},E{rr-1},C{rr-1})", fmt=N6_, align="right", border=True, size=9)
            put(ws, rr, 4, f"=IF(F{rr-1}>{host},D{rr-1},E{rr-1})", fmt=N6_, align="right", border=True, size=9)
        put(ws, rr, 5, f"=(C{rr}+D{rr})/2", fmt=N6_, align="right", border=True, size=9)
        put(ws, rr, 6, f"={pv_of(f'E{rr}')}", fmt=N2_, align="right", border=True, size=9)
    last = h + XL_EIR_STEPS
    res = last + 1
    put(ws, res, 2, "유효이자율 (마지막 구간의 가운데)", bold=True, border=True)
    put(ws, res, 3, f"=(IF(F{last}>{host},E{last},C{last})+IF(F{last}>{host},D{last},E{last}))/2",
        bold=True, fmt="0.0000%", align="right", border=True)
    return f"$C${res}", res + 2


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


def acc_mode(tm: Terms) -> str:
    """이 평가로 어떤 회계처리를 만들 수 있는가.

    * ``"initial"`` — 발행 시점 평가(경과 0). 최초 인식 배분·분개·상각표를 만든다.
    * ``"subsequent"`` — 발행일 뒤 평가인데 전기말 장부금액이 들어왔다. 배분표는 최초 인식용
      참고이고 기말 재평가·당기 이자비용이 결산 회계처리다.
    * ``"fv_only"`` — 발행일 뒤 평가인데 전기말 장부금액이 **없다**. 결산일에 필요한 것은
      파생상품 공정가치뿐이고, 주계약은 발행일 배분액을 최초 유효이자율로 상각한 장부금액이라
      이 평가만으로는 못 만든다. 그래서 배분표·분개·상각표를 만들지 않고 공정가치만 싣는다 —
      최초 인식 숫자를 결산 분개로 옮겨 적는 사고를 막기 위해서다.

    화면·값 조서·수식 조서가 같은 판단을 하도록 한 자리에 둔다 (fvpl_on 과 같은 이유).
    """
    if is_sha(tm): return "initial"
    if tm.elapsed_m <= 0.01: return "initial"
    if tm.prev_deriv is not None and tm.prev_deriv >= 0: return "subsequent"
    if tm.prev_host is not None and tm.prev_host >= 0: return "subsequent"
    return "fv_only"


FV_ONLY_XL = (
    "공정가치 산출 전용 — 평가기준일이 발행일보다 뒤인데 전기말 장부금액이 없다.",
    "결산일의 회계처리에 필요한 것은 파생상품(또는 복합내재파생상품)의 공정가치와, 발행일 배분액을 "
    "최초 유효이자율로 상각한 주계약 장부금액이다. 이 평가는 앞의 것만 준다. 최초 인식 배분표·분개·"
    "상각표를 이 시점 값으로 만들면 결산 분개에 잘못 옮겨 적기 쉬워 만들지 않는다.",
    "사이드바 「기말 재평가 · 전기 장부금액」에 발행일 배분액·최초 유효이자율·전기말 장부금액을 넣으면 "
    "기말 재평가와 당기 이자비용이 나온다. 발행 시점 평가라면 평가기준일을 발행일로 두면 된다.",
)
FV_ONLY_NOTE = ("**공정가치 산출 전용입니다.** 평가기준일이 발행일보다 뒤인데 전기말 장부금액이 없습니다. "
                "결산일에 필요한 것은 파생상품 공정가치와 «발행일 배분액을 최초 유효이자율로 상각한» "
                "주계약 장부금액인데, 이 평가는 앞의 것만 줍니다. 최초 인식 배분표·분개·상각표를 지금 "
                "시점 값으로 만들면 결산 분개에 잘못 옮겨 적기 쉬워 만들지 않습니다. 사이드바 "
                "**기말 재평가 · 전기 장부금액**에 발행일 배분액·최초 유효이자율·전기말 장부금액을 "
                "넣으면 기말 재평가와 당기 이자비용이 나옵니다.")


def fv_only_rows(tm: Terms, full, b0, b1, b2, ca):
    """공정가치 전용일 때 조서·화면에 싣는 표 — [(항목, 100 기준)]. 배분표와 같은 함수에서 나온다."""
    rows, _ = allocate(tm, full, b0, b1, b2, ca)
    rm = remeasure(tm, rows)
    out = [("전체 (적용 물량 기준)", b2), ("부채요소 (사채 + 조기상환권)", b1), ("주계약 (옵션 없는 사채 · 참고)", b0)]
    out.append((("복합계약 전체 · 당기손익-공정가치" if fvpl_on(tm) else
                 "파생상품부채 공정가치 (결산 재측정 대상)"), rm["fv_liab"]))
    if rm["fv_asset"] > 1e-12: out.append(("파생상품자산 (매도청구권) 공정가치", rm["fv_asset"]))
    # 기특정 콜은 발행자 자산이 아니라 재평가 대상이 아니다. 값만 참고로 싣는다.
    out += alloc_extra(tm, ca)
    if tm.conv_class == "equity" and not is_bw(tm):
        out.append(("전환권대가 (자본 · 재측정 없음 · 참고)", 100 - b1 + (ca if ca > 0 else 0.0)))
    return out


def holder_on(tm: Terms) -> bool:
    """투자자 관점으로 회계처리를 만드는가. 주주간계약은 자기 화면에 세 관점이 따로 있다."""
    return (not is_sha(tm)) and str(getattr(tm, "view", "issuer")) == "holder"


def holder_call_sep(tm: Terms, ca: float) -> bool:
    """투자자에게 매도청구권이 **따로 떨어진 파생상품부채**인가.

    거래상대방이 발행회사가 아닌 제3자이거나(지정 가능 · 기특정) 사채와 따로 양도되면
    투자자가 써 준 별도의 옵션이다(4.3.1 과 같은 이유). 발행자 상환권처럼 거래상대방이
    그대로이면 계약의 일부라 복합계약 전체의 공정가치 안에 녹는다.
    """
    return abs(ca) > 1e-12 and int(tm.k_sep) != 0 and not issuer_redeem(tm)


def holder_class(tm: Terms):
    """투자자의 분류 — (결론, 근거 문장)."""
    if bw_cash(tm) and int(tm.bw_detach) == 1:
        return ("사채와 신주인수권증권을 따로 인식한다",
                "분리형 BW 는 두 증권을 따로 양도할 수 있어 각각 별개의 금융자산이다. "
                "신주인수권증권은 파생상품이라 당기손익-공정가치로 측정한다(제1109호 문단 4.1.4). "
                "사채는 사업모형과 계약상 현금흐름 특성에 따라 상각후원가·기타포괄손익-공정가치·"
                "당기손익-공정가치 가운데 하나다(문단 4.1.1~4.1.4) — 상각후원가로 분류하면 장부금액은 "
                "이 공정가치가 아니다.")
    if is_rcps(tm) and tm.p_s > tm.p_e:
        return ("지분상품 — 당기손익-공정가치 (기타포괄손익-공정가치 선택 가능)",
                "투자자에게 상환청구권이 없어 발행회사가 자본으로 분류할 수 있는 우선주다. 발행회사 "
                "입장의 지분상품이면 투자자는 당기손익-공정가치로 측정하되, 단기매매가 아니면 최초 "
                "인식 때 기타포괄손익-공정가치를 취소불가능하게 선택할 수 있다(제1109호 문단 5.7.5). "
                "발행회사가 부채로 분류하는 조건이 따로 있으면 채무상품이라 당기손익-공정가치다.")
    return ("복합계약 전체 — 당기손익-공정가치",
            "주계약이 제1109호 적용범위의 금융자산이므로 내재파생상품을 분리하지 않고 복합계약 "
            "전체를 분류한다(제1109호 문단 4.3.2). "
            + ("전환권 때문에" if not is_bw(tm) else "신주인수권 때문에")
            + " 계약상 현금흐름이 원금과 이자의 지급만으로 구성되지 않으므로(문단 4.1.2⑵·B4.1.14) "
              "당기손익-공정가치로 측정한다(문단 4.1.4).")


HOLDER_NOTE = (
    "공정가치는 누가 들고 있든 같습니다(제1113호 — 시장참여자 사이의 교환가격). 발행자 관점과 "
    "값이 같고, 갈리는 것은 **회계 단위와 분개**입니다. 투자자는 내재파생을 떼지 않고 전체를 "
    "하나로 잽니다(제1109호 문단 4.3.2).")
HOLDER_DAY1 = (
    "최초 인식 차이(거래가격 100 − 공정가치) — 공정가치가 활성시장 공시가격이나 관측 가능한 "
    "시장자료만으로 산정된 경우에만 당기손익, 그 밖에는 이연한다(제1109호 문단 B5.1.2A). 비상장 "
    "증권은 보통 이연이다. 모형을 거래가격에 보정하면 차이가 생기지 않는다(제1113호 문단 64).")


def day1_label(tm: Terms, d: float) -> str:
    """최초 인식 차이 분개 계정 — 이연(기본) / 당기손익(근거 입력 시)."""
    if int(getattr(tm, "d1_pl", 0)) == 1:
        return ("금융자산평가이익 — 최초 인식 차이 (문단 B5.1.2A(1))" if d > 0 else
                "금융자산평가손실 — 최초 인식 차이 (문단 B5.1.2A(1))")
    return "최초 인식 차이 — 이연 (문단 B5.1.2A(2))"


# 발행자 최초 인식 차이를 당기손익으로 고른 경우 배분표에 붙는 줄. 음수(차변)가 손실이다.
DAY1_LOSS = "최초 인식 손실 · 당기손익 (1109 B5.1.2A(1))"
DAY1_GAIN = "최초 인식 이익 · 당기손익 (1109 B5.1.2A(1))"
ISSUER_DAY1 = (
    "발행회사 최초 인식 차이 — 회계기준원 질의회신 2019-I-KQA018. 공정가치가 거래가격과 다르면 "
    "주계약과 전환권 파생을 각각 공정가치로 재고(주계약 = 전체 공정가치 − 파생), 차이는 먼저 금융상품이 "
    "아닌 것의 대가인지 본 뒤 제1109호 문단 B5.1.2A 에 따라 처리한다 — 활성시장 공시가격이나 관측 "
    "가능한 시장자료만 쓴 평가면 당기손익, 그 밖에는 이연. 거래가격이 공정가치라면 먼저 평가를 "
    "거래가격에 보정한다(제1113호 문단 64) — 그러면 차이가 생기지 않는다.")


def issuer_day1(tm: Terms, b0, b1, b2, ca):
    """발행자 최초 인식 대사 — 모형 순평가금액 · 거래가격 100 · 차이 · 주계약 공정가치.

    발행일 평가(경과 0)의 발행자 관점에서만 뜻이 있다. 발행자가 인식하는 금융상품의 공정가치
    합계(순평가금액)는 콜 차감 전 가치에서 발행자가 가진 콜을 뺀 값이다 — 제3자 기특정 콜은
    발행자 것이 아니라 빼지 않는다.

    * 전환권이 부채(복합계약) — 차이를 이연하면 주계약 공정가치에서 빼 둔 금액이 최초 장부금액이고
      (종전 배분과 같은 숫자), 당기손익이면 주계약이 공정가치 그대로다. 전체 지정이면 그 한 줄이다.
    * 전환권이 자본(복합금융상품) — 차이는 잔여인 자본요소에 흡수된다 (1032 문단 31).
    """
    if is_sha(tm) or holder_on(tm) or tm.elapsed_m > 0.01: return None
    kk = int(getattr(tm, "k_kind", 0)) == 1
    net = b2 - (0.0 if kk else ca)
    diff = net - 100.0
    hybrid = tm.conv_class == "liability"
    if fvpl_on(tm):
        host_fv = net + (ca if (int(tm.k_sep) != 0 and not kk) else 0.0)
    else:
        host_fv = b1 if put_in_host(tm) else b0
    pl = hybrid and int(getattr(tm, "d1_pl", 0)) == 1 and abs(diff) > 1e-12
    return dict(net=net, price=100.0, diff=diff, hybrid=hybrid, host_fv=host_fv, pl=pl, kk=kk, ca=ca,
                whole=fvpl_on(tm),
                host_book=((host_fv if pl else host_fv - diff) if hybrid else None),
                mode=(("당기손익" if pl else "이연") if hybrid else "자본요소에 흡수"))


def issuer_day1_note(d1) -> str:
    """배분 문안에 붙는 한 문단 — 화면·값 조서·수식 조서가 같이 쓴다."""
    d = d1["diff"]
    out = f"최초 인식 차이 {d:+,.4f} (순평가금액 {d1['net']:,.4f} − 거래가격 100). "
    if d1["kk"] and d1["ca"] > 1e-12:
        out += (f"제3자 기특정 콜 {d1['ca']:,.4f} 이 이 차이에 들어 있습니다 — 금융상품이 아닌 것의 "
                "대가(주주간 분배 등)인지 먼저 판단하십시오. ")
    if d1["pl"]:
        return out + ("관측 가능한 시장자료만 쓴 평가라는 근거에 따라 **당기손익**으로 인식하고, 주계약을 "
                      f"공정가치 {d1['host_fv']:,.4f} 로 둡니다 (1109 B5.1.2A(1), 회계기준원 2019-I-KQA018).")
    return out + ("관측할 수 없는 투입변수를 쓴 평가라 **이연**합니다 (1109 B5.1.2A(2)) — 주계약 공정가치 "
                  f"{d1['host_fv']:,.4f} 에서 차이를 빼 최초 장부금액 {d1['host_book']:,.4f} 로 두고, "
                  "유효이자율로 기간에 걸쳐 인식합니다. 거래가격이 공정가치라면 먼저 평가를 거래가격에 "
                  "보정하십시오 (1113 문단 64) — 차이가 생기지 않습니다.")


def issuer_day1_rows(d1):
    """최초 인식 대사표 — [(항목, 100 기준)]. 배분표와 따로 싣는다(합계에 넣지 않는다)."""
    rows = [("콜 차감 후 순평가금액 (발행자가 인식하는 금융상품의 공정가치)", d1["net"]),
            ("거래가격 (받은 현금)", 100.0),
            ("최초 인식 차이 (순평가금액 − 거래가격)", d1["diff"])]
    if d1["hybrid"]:
        _nm = "복합계약 전체" if d1["whole"] else "주계약"
        rows += [(f"{_nm} 공정가치", d1["host_fv"]),
                 (("최초 인식 차이 · 당기손익 인식" if d1["pl"] else
                   f"최초 미인식 차이 ({_nm}에서 차감 — 유효이자율로 나눠 인식)"),
                  (0.0 if d1["pl"] else -d1["diff"])),
                 (f"{_nm} 최초 장부금액", d1["host_book"])]
    return rows


def issuer_day1_cases(d1) -> list:
    """최초 인식 차이의 세 가지 구분 — [(구분, 처리, 이 평가)]. 화면·두 조서 공통.

    차이를 어디에 두는지는 지분요소의 분류와 평가 투입변수의 관측 가능성이 정한다. 세 갈래를
    나란히 적고 이 평가가 어느 갈래인지 표시한다 — 하나만 적으면 다른 갈래를 검토했는지 알 수 없다.
    """
    hy, pl = bool(d1["hybrid"]), bool(d1["pl"])
    return [("① 전환권(지분요소)이 자본 — 복합금융상품",
             "차이는 잔여인 자본요소에 흡수된다. 최초 인식 손익이 생기지 않는다 (1032 문단 31).",
             "◀ 이 평가" if not hy else ""),
            ("② 부채(복합계약) · 관측 가능한 시장자료만 쓴 평가",
             "차이를 당기손익으로 인식하고 주계약을 공정가치로 둔다 (1109 문단 B5.1.2A(1)).",
             "◀ 이 평가" if (hy and pl) else ""),
            ("③ 부채(복합계약) · 관측할 수 없는 투입변수를 쓴 평가",
             "차이를 이연해 주계약 장부금액에서 빼고 유효이자율로 기간에 걸쳐 인식한다 (1109 문단 B5.1.2A(2)).",
             "◀ 이 평가" if (hy and not pl) else "")]


def alloc_journal(rows):
    """배분표를 분개로 뒤집는다 — [(계정, 차변, 대변)]. 화면·값 조서가 같이 쓴다.

    음수 줄은 차변이다 — 매도청구권 자산과 최초 인식 손실. 나머지는 대변이다.
    """
    je = [("현금", 100.0, None)]
    for k, v in rows[:-1]:
        nm = k.split(" · ")[0]
        if nm.startswith("최초 인식"):
            je.append(((f"{nm} (당기손익)", -v, None) if v < 0 else (f"　{nm} (당기손익)", None, v)))
        elif v < 0:
            je.append((f"파생상품자산 ({nm})", -v, None))
        else:
            je.append((f"　{nm}", None, v))
    return je


HOLDER_GROUP = (
    "투자자가 발행회사의 지배기업이면 연결재무제표에서는 내부거래로 제거됩니다. 별도재무제표·"
    "관계기업 투자는 이 증권이 발행회사 입장의 지분상품인지에 따라 적용 기준서가 갈리므로 따로 "
    "검토하십시오.")


def holder_rows(tm: Terms, full, b0, b1, b2, ca) -> dict:
    """투자자 관점 — 재무상태표 줄 · 참고 분해 · 분개. 화면·값 조서·수식 조서가 같이 쓴다.

    투자자가 실제로 들고 있는 것은 **B2 − 매도청구권**(화면의 B3)이다. 매도청구권은 투자자가
    써 준 권리라 부호가 발행자와 반대다.
    """
    L = lbl(tm)
    net = b2 - ca
    sep = holder_call_sep(tm, ca)
    split_bw = bw_cash(tm) and int(tm.bw_detach) == 1
    ttl, basis = holder_class(tm)
    pos = [("투자자 순포지션 (자산 − 부채)", net)]
    if split_bw:
        pos += [("사채 · 금융자산 (분류는 사업모형에 따른다)", b1),
                ("신주인수권증권 · 파생상품자산 (당기손익-공정가치)", b2 - b1)]
    else:
        pos += [("복합계약 전체 · 당기손익-공정가치측정 금융자산", b2 if sep else net)]
    if sep:
        pos += [(f"{L['call']} — 투자자가 써 준 권리 · 파생상품부채 (당기손익-공정가치)", ca)]
    conv_nm = inst_text(tm, "전환권") if not is_bw(tm) else "신주인수권"
    parts = [(L["host"].split(" (")[0] + " (옵션 없음)", b0), (L["put"], b1 - b0),
             (conv_nm, b2 - b1)]
    if abs(ca) > 1e-12: parts.append((L["call"] + " (투자자 부담 — 차감)", -ca))
    parts.append(("합계 = 투자자 순포지션", net))

    J = []                                   # (차변/대변, 계정, 100 기준)
    if tm.elapsed_m <= 0.01:
        mode = "initial"
        if split_bw:
            J += [("차변", "금융자산 — 사채", b1), ("차변", "파생상품자산 — 신주인수권증권", b2 - b1)]
        else:
            J += [("차변", "당기손익-공정가치측정금융자산", b2 if sep else net)]
        J += [("대변", "현금 (거래가격)", 100.0)]
        if sep: J += [("대변", f"파생상품부채 — {L['call']}", ca)]
        d = net - 100.0
        if abs(d) > 1e-12:
            J += [("대변" if d > 0 else "차변", day1_label(tm, d), abs(d))]
        c100 = (tm.issue_cost/tm.face_total*100) if tm.issue_cost and tm.face_total else 0.0
        if c100 > 0 and not split_bw:
            J += [("차변", "지급수수료 (당기비용 · 문단 5.1.1)", c100), ("대변", "현금 (거래원가)", c100)]
    elif float(getattr(tm, "prev_hold", -1.0)) >= 0:
        mode = "subsequent"
        d = net - float(tm.prev_hold)
        if d >= 0:
            J += [("차변", "당기손익-공정가치측정금융자산 (순액)", d), ("대변", "금융자산평가이익", d)]
        else:
            J += [("차변", "금융자산평가손실", -d), ("대변", "당기손익-공정가치측정금융자산 (순액)", -d)]
    else:
        mode = "fv"
    return dict(title=ttl, basis=basis, pos=pos, parts=parts, journal=J, mode=mode, sep=sep,
                net=net)


def write_holder_sheet(wb, tm: Terms, h: dict, put, sec, title, N4, N0, LIGHT, RED, GREY):
    """「회계처리」 시트를 투자자 관점으로 바꿔 그린다. 값 조서·수식 조서가 같이 쓴다.

    첫 표의 첫 줄(C10)이 투자자 순포지션이다 — 조서대조가 엔진의 B2 − 매도청구권과 대조한다.
    """
    from openpyxl.styles import Alignment
    _ei = wb.sheetnames.index("회계처리"); wb.remove(wb["회계처리"])
    E = wb.create_sheet("회계처리", _ei); E.sheet_view.showGridLines = False
    for cc, w in (("B", 58), ("C", 16), ("D", 18)): E.column_dimensions[cc].width = w
    title(E, 2, "회계처리 — 투자자 관점", span=3)
    for _i, (_tx, _col, _b) in enumerate(((HOLDER_NOTE.replace("**", ""), RED, True),
                                          ("분류 — " + h["title"], "000000", True),
                                          (h["basis"], GREY, False))):
        put(E, 4+_i, 2, _tx, color=_col, bold=_b, size=(10 if _b else 9))
        E.merge_cells(start_row=4+_i, start_column=2, end_row=4+_i, end_column=4)
        E.cell(row=4+_i, column=2).alignment = Alignment(wrap_text=True, vertical="top")
        E.row_dimensions[4+_i].height = 30 if _i != 1 else 15
    sec(E, 8, "평가기준일 공정가치 — 재무상태표", span=3)
    for i, hh in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
        put(E, 9, 2+i, hh, bold=True, fill=LIGHT, align="center", border=True, size=9)
    r = 10
    for k, v in h["pos"]:
        put(E, r, 2, k, border=True, bold=(r == 10))
        put(E, r, 3, v, fmt=N4, align="right", border=True)
        put(E, r, 4, v/100*tm.face_total, fmt=N0, align="right", border=True)
        r += 1
    r += 1
    sec(E, r, "참고 — 구성요소 분해 (회계 단위가 아니다)", span=3); r += 1
    for k, v in h["parts"]:
        put(E, r, 2, k, border=True, bold=k.startswith("합계"))
        put(E, r, 3, v, fmt=N4, align="right", border=True)
        put(E, r, 4, v/100*tm.face_total, fmt=N0, align="right", border=True)
        r += 1
    r += 1
    sec(E, r, "분개 — " + holder_mode_text(h), span=3); r += 1
    if h["journal"]:
        for i, hh in enumerate(["계정", "100 기준", "전액 기준 (원)"]):
            put(E, r, 2+i, hh, bold=True, fill=LIGHT, align="center", border=True, size=9)
        r += 1
        for side, acct, v in h["journal"]:
            put(E, r, 2, ("(차) " if side == "차변" else "　　(대) ") + acct, border=True)
            put(E, r, 3, v, fmt=N4, align="right", border=True)
            put(E, r, 4, v/100*tm.face_total, fmt=N0, align="right", border=True)
            r += 1
    r += 1
    for _tx in ((HOLDER_DAY1,) if h["mode"] == "initial" else ()) + (
            "이 조서의 「분리 판단」 시트는 발행자 관점의 판단이다 — 투자자는 문단 4.3.2 에 따라 "
            "내재파생상품을 분리하지 않는다.", HOLDER_GROUP):
        put(E, r, 2, _tx, color=GREY, size=9)
        E.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        E.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
        E.row_dimensions[r].height = 30
        r += 1
    return E


def view_text(tm: Terms) -> str:
    return ("투자자 — 보유 금융자산 · 복합계약 전체 공정가치 (1109 문단 4.3.2)" if holder_on(tm)
            else "발행자 — 부채·자본 분류와 요소별 배분 (1032 · 1109 문단 4.3.3)")


def holder_mode_text(h: dict) -> str:
    return {"initial": "최초 인식 — 거래가격을 치르고 공정가치로 인식한다",
            "subsequent": "후속 측정 — 전기말 공정가치에서 당기말 공정가치로 재측정해 차이를 당기손익에",
            "fv": ("후속 측정 — 장부금액은 곧 이 공정가치다. 전기말 장부금액(공정가치)을 넣으면 "
                   "평가손익 분개가 나온다")}[h["mode"]]


def auto_conv(tm: Terms) -> bool:
    """존속기간 만료 시 보통주로 자동전환되는 RCPS 인가."""
    return is_rcps(tm) and int(tm.mat_mode) == 0


def cpn_basis_rate(tm: Terms) -> float:
    """계약 배당률을 **발행가 100 기준**으로 옮긴 값 (재량 여부는 보지 않는다).

    격자는 1주 발행가를 100 으로 잰다. 계약이 「1주당 액면가액 기준 연 1%」 면 발행가
    기준으로는 1% × 액면가 ÷ 발행가 다. 계약서 숫자를 그대로 넣으면 배당이 발행가 ÷
    액면가 배(59,390 ÷ 500 = 119배) 부풀고, 상환금액 산식이 지급분을 빼므로 상환금액까지
    내려간다. RCPS 에만 있다 — 사채의 표면이자는 권면(= 100) 기준이다.
    """
    if (is_rcps(tm) and int(getattr(tm, "div_basis", 0)) == 1
            and float(getattr(tm, "issue_px", 0.0)) > 0 and tm.par > 0):
        return tm.cpn*tm.par/float(tm.issue_px)
    return tm.cpn


def eff_cpn(tm: Terms) -> float:
    """계산에 쓰는 정기 지급률 (발행가 100 기준).

    CB 의 표면이자는 채무라 늘 들어간다. RCPS 의 우선배당은 계약에 따라 갈린다
    (1032 AG37) — 미지급분을 상환가액에 가산하면 전체가 부채고 배당은 이자비용
    (그대로 쓴다). 배당이 발행자 재량이고 상환가액과 무관하면 배당은 자본요소의
    이익분배라 부채 현금흐름에서 빼고, 상환가액 산식도 배당을 차감하지 않는다(0).
    액면 기준 배당률은 여기서 **한 번만** 발행가 기준으로 옮긴다 (cpn_basis_rate).
    """
    return 0.0 if (is_rcps(tm) and int(tm.div_mode) == 1) else cpn_basis_rate(tm)


def div_basis_text(tm: Terms) -> str:
    """조서·조건표에 적는 우선배당률 기준 — 환산 전·후를 함께."""
    if int(getattr(tm, "div_basis", 0)) == 1 and float(getattr(tm, "issue_px", 0.0)) > 0 and tm.par > 0:
        return (f"액면 {tm.par:,.0f}원 기준 연 {tm.cpn:.2%} "
                f"(발행가 {tm.issue_px:,.0f}원 환산 연 {cpn_basis_rate(tm):.4%})")
    return f"발행가 기준 연 {tm.cpn:.2%}"


DIL_WARN = 0.10      # 전환 시 늘어나는 주식이 보통주의 이 비율을 넘으면 경고한다


def dil_msg(tm: Terms) -> str:
    """희석 경고 — 화면·validate 가 같은 문구를 쓴다."""
    r = tm.dil_shares/tm.base_shares
    return (f"**전환 시 보통주가 {r:.1%} 늘어납니다** ({tm.dil_shares:,.0f}주 ÷ "
            f"{tm.base_shares:,.0f}주). 기초주가가 희석을 반영한 주당가치인지 확인하십시오 — "
            "이 도구는 희석을 스스로 반영하지 않습니다. 발행가가 주가보다 훨씬 높은 "
            "증권이면 전환 시 부채가 사라지는 효과가 더 커서 희석 반영 주가가 오히려 "
            "높아질 수 있습니다.")


def tranche_tag(tm: Terms) -> str:
    """파일 이름에 붙이는 회차 표시 — 「1차 납입분 84,189주」 → 「_1차납입분84189주」."""
    tg = re.sub(r"[^0-9A-Za-z가-힣]", "", str(getattr(tm, "tranche", "") or ""))
    return f"_{tg[:30]}" if tg else ""


def unmod_text(tm: Terms) -> str:
    """「이 계약에서 반영하지 않은 권리」 한 줄. 비어 있으면 "" — 절을 그리지 않는다."""
    lines = [x.strip() for x in str(getattr(tm, "unmod_note", "") or "").splitlines() if x.strip()]
    return ("이 계약에서 반영하지 않은 권리 — " + " / ".join(lines)) if lines else ""


def ded_suffix(tm: Terms, which: str) -> str:
    """권리 표의 페이오프 뒤에 붙이는 공제 방식 — 지급률이 0 이면 붙이지 않는다."""
    if eff_cpn(tm) <= 0: return ""
    return {1: " − 기 지급분(보장수익률로 굴린 금액)", 2: " − 기 지급분(명목 합계)",
            0: " (지급분 공제 없음)"}[ded_of(tm, which)]


def node_gap_m(tm: Terms) -> float:
    """«기간 중 언제든지» 에 쓰는 노드 간격(개월). 월 격자면 입력한 간격(gap_m), 일수 격자(주·2주)면
    실제 한 칸의 길이다 — 월 간격을 쓰면 주 격자에서 한 달에 한 번만 열린다. 날짜가 비면 gap_m."""
    days = float(getattr(tm, "grid_days", 0.0) or 0.0)
    if days <= 0:
        return float(tm.gap_m)
    # derive() 와 같은 구간 수에서 센다 — valuation/exercise.py 의 node_interval_months 와 같은 식이다.
    try:
        years = (dt.date.fromisoformat(tm.d_mat) - dt.date.fromisoformat(tm.d_base)).days/365
    except (TypeError, ValueError):
        return float(tm.gap_m)
    if years <= 0:
        return float(tm.gap_m)
    return years*12/max(4, round(years*365/days))


def conv_resp(tm: Terms) -> bool:
    """매도청구 통지를 받은 투자자가 전환으로 대응할 수 있는 계약인가 (``Terms.k_conv_resp``).

    풋·콜 우선순위(``pc_order``)와 따로 정한다 — 우선순위는 조기상환과 매도청구 사이만 정하고,
    이 선택은 전환과 매도청구 사이를 정한다. 기본 1(대응할 수 있다)이 종전 격자(유무가치비교법)의
    읽기다. 현금납입 신주인수권부사채는 행사해도 사채가 남아 이 선택이 뜻을 갖지 않는다.
    """
    return int(getattr(tm, "k_conv_resp", 1)) == 1


def pc_order_text(tm: Terms) -> str:
    """풋·콜 우선순위 한 줄 — 화면·두 조서가 같은 문장을 쓴다. 조기상환과 매도청구 사이만 말한다."""
    return ("발행자 콜 우선 — 조기상환과 매도청구가 같은 노드에서 함께 열리면 매도청구가 먼저다"
            if int(tm.pc_order) == 1 else
            "투자자 풋 우선 — 통지한 조기상환을 매도청구로 막지 못한다")


def conv_resp_text(tm: Terms) -> str:
    """매도청구 통지 뒤 전환 대응 한 줄 (``k_conv_resp``)."""
    if bw_cash(tm):
        return "해당 없음 — 현금납입 신주인수권은 행사해도 사채가 남는다"
    return ("전환할 수 있다 — 매도청구 통지를 받아도 전환해 매도청구를 피할 수 있다 (전환이 매도청구보다 먼저)"
            if conv_resp(tm) else
            "전환할 수 없다 — 매도청구가 통지되면 그 물량은 전환하지 못한다 (매도청구가 전환보다 먼저)")


def decide_formula_text(tm: Terms) -> str:
    """콜이 걸린 노드의 식을 말로 — node_decide 의 네 조합 중 이 계약의 것."""
    kf, cr = int(tm.pc_order) == 1, conv_resp(tm)
    return {(False, True): "MAX(전환, 조기상환, MIN(보유, 매도청구))",
            (True, True): "MAX(전환, MIN(MAX(보유, 조기상환), 매도청구))",
            (False, False): "MAX(조기상환, MIN(MAX(전환, 보유), 매도청구))",
            (True, False): "MIN(MAX(전환, 조기상환, 보유), 매도청구)"}[(kf, cr)]


def priority_label(tm: Terms) -> str:
    """적용한 식 옆에 붙이는 짧은 이름 — 두 선택을 함께 적는다."""
    return (("동시 행사 시 발행자 매도청구 우선" if int(tm.pc_order) == 1 else "동시 행사 시 투자자 조기상환 우선")
            + " · 매도청구 통지 뒤 전환 " + ("가능" if conv_resp(tm) else "불가"))


PRIORITY_NOTE = ("두 권리가 같은 시점에 함께 행사될 수 있을 때 어느 쪽이 먼저인지는 계약이 정합니다. 조기상환과 "
                 "매도청구 사이는 「풋·콜 우선순위」가, 전환과 매도청구 사이는 「매도청구 통지 뒤 전환 대응」이 따로 "
                 "정합니다 — 우선순위 하나가 전환권까지 바꾸지 않습니다. 두 행사금액이 다르고 행사기간이 겹치는 "
                 "시점에서만 결과가 달라집니다. 적용한 선택과 근거는 「분리 판단」 시트의 「계약상 권리」 표에 남깁니다.")


def event_order_rows(tm: Terms) -> list:
    """같은 날 겹치는 사건과 매도청구의 선후 — [(사건, 처리)]. 세 평가방법이 같은 표를 따른다.

    화면(매도청구권 칸)·값 조서·수식 조서가 이 표를 그대로 싣는다. 입력으로 정하는 것은 앞의
    두 줄뿐이고, 나머지는 앱이 모든 방법에 같게 적용하는 규칙이다.
    """
    if tm.k_w <= 0 or is_sha(tm):
        return []
    rows = [("조기상환 ↔ 매도청구 (같은 노드)", pc_order_text(tm)),
            ("전환 ↔ 매도청구 (매도청구 통지 뒤)", conv_resp_text(tm))]
    if is_rcps(tm) and int(tm.ipo_on) and int(tm.ipo_conv):
        rows.append(("상장 강제전환 ↔ 매도청구", "상장 강제전환이 먼저 — 그 자리에서 보통주가 되어 콜이 살 대상이 사라진다"))
    rows.append(("만기 ↔ 매도청구", "만기상환·전환(자동전환 포함)이 먼저 — 만기일에는 매도청구를 반영하지 않는다"))
    if int(tm.k_hold) == 1 and tm.k_lock > 0:
        rows.append(("의무보유 기간 (발행 후 " + f"{tm.k_lock:,.0f}개월까지)",
                     "콜 대상물량은 " + ("전환·조기상환청구를" if int(tm.k_lock_put) else "전환을")
                     + " 할 수 없다 — 그 기간에는 매도청구 통지에 그 권리로 대응하지 못하고, 사채가 소멸하지 않아 콜이 이어진다"))
    rows.append(("이 입력으로 나타내지 못하는 계약",
                 "통지기간의 일부에만 전환을 허용하는 계약, 발행회사 콜과 지정 제3자 콜의 우선순위를 달리 정한 "
                 "계약, 의무보유가 조기상환만 막는 계약은 반영하지 않는다 — 「평가에 반영하지 않은 권리」에 적는다"))
    return rows


def rfx_any(tm: Terms) -> bool:
    """리픽싱을 «언제든지» (매 노드) 하는 계약인가 — 주기를 노드 간격 이하로 두었다."""
    return float(tm.rfx_cyc) <= node_gap_m(tm) + 1e-9


K_ROUND_EPS = 1e-9   # 원 단위 처리의 경계 허용오차 — 1,447.0000000001 을 1,448 로 올리지 않는다. 엑셀도 같은 값.


def k_round(tm: Terms, x: float) -> float:
    """조정 후 전환가격의 원 단위 미만 처리 (계약서 문구). 0 그대로 · 1 절상 · 2 절사."""
    m = int(getattr(tm, "rfx_round", 0))
    if m == 1: return float(math.ceil(x - K_ROUND_EPS))
    if m == 2: return float(math.floor(x + K_ROUND_EPS))
    return x


def xl_k_round(x: str, mode_ref: str) -> str:
    """k_round 의 엑셀 식 — 가정 시트의 처리 칸(mode_ref)을 본다."""
    return (f"IF({mode_ref}=1,CEILING({x}-{K_ROUND_EPS:.0E},1),"
            f"IF({mode_ref}=2,FLOOR({x}+{K_ROUND_EPS:.0E},1),{x}))")


def refix_steps(tm: Terms, n: int, dt_: float) -> dict:
    """정기 전환가액 조정일 {스텝: 계약 조정월(발행일부터 개월)} — 엔진 · 값 조서 · 수식 조서가 함께 쓴다.

    조정일은 발행일부터 주기마다 돌아오는 계약일이고, 날마다 «그날 이후 첫 노드» 에 배정한다
    (허용 일수 안에서 앞선 노드는 같은 날 — EXDATE_RULE). 평가기준일 전에 지난 조정일과 평가기준일
    노드(스텝 0 — 현재 전환가액에 이미 반영)는 싣지 않는다. 격자가 성겨 두 조정일이 한 노드에
    떨어지면 한 번만 조정한다. «언제든지» (주기 ≤ 노드 간격)면 스텝 1 부터 모든 노드다.

    한때 «주기 × 월당 노드 수» 를 반올림한 칸 간격으로 열어, 주·2주 격자에서 매월 조정이 4·2노드
    마다(28일) 일어나며 5년에 다섯 번 더 조정하고 날짜가 최대 140일 밀렸다. 월 격자에서도 주기가
    노드 간격의 배수가 아니면(예: 6개월 노드 · 7개월 주기) 매 노드 조정으로 바뀌어 있었다.
    """
    if int(tm.rfx_mode) == 0 or tm.rfx_cyc <= 0:
        return {}
    st_lo, _ = step_mapper(tm, n, dt_)
    first = rfx_first_m(tm)
    if rfx_any(tm):
        # 언제든지 — 최초 조정일을 따로 정했으면 그날 이후 노드부터다.
        i0 = max(1, st_lo(first)) if float(getattr(tm, "rfx_first", 0.0) or 0.0) > 0 else 1
        return {i: None for i in range(i0, n+1)}
    rem_m = float(getattr(tm, "rem_m", 0.0) or tm.T*12)
    end = tm.elapsed_m + rem_m + 1e-6
    # 조정일 = 최초 조정일 + 주기 × k (k = 0, 1, 2, …). 평가기준일 당일 이전 조정일은 이미
    # 현재 전환가액에 반영되어 있다.
    k = max(0, math.floor((tm.elapsed_m - first)/tm.rfx_cyc) + 1) if tm.elapsed_m >= first - 1e-9 else 0
    out = {}
    while first + tm.rfx_cyc*k <= end:
        m = first + tm.rfx_cyc*k
        i = st_lo(m)
        if m > tm.elapsed_m + 1e-9 and 1 <= i <= n and i not in out:
            out[i] = m
        k += 1
    return out


def rfx_first_m(tm: Terms) -> float:
    """최초 리픽싱 조정일 (발행 후 개월). 따로 넣지 않았으면 주기와 같다."""
    f = float(getattr(tm, "rfx_first", 0.0) or 0.0)
    return f if f > 0 else float(tm.rfx_cyc)


def rfx_cycle_text(tm: Terms) -> str:
    _f = float(getattr(tm, "rfx_first", 0.0) or 0.0)
    if rfx_any(tm):
        return "언제든지 (매 노드 조정)" + (f" · 발행 후 {_f:,.4g}개월부터" if _f > 0 else "")
    return (f"최초 발행 후 {_f:,.4g}개월 · 이후 {tm.rfx_cyc:,.4g}개월 주기" if _f > 0
            else f"{tm.rfx_cyc:,.0f}개월 주기")


def ded_of(tm: Terms, which: str) -> int:
    """행사금액의 지급분 공제 방식 — which 는 "p" 조기상환 / "k" 매도청구 / "m" 만기."""
    return int(getattr(tm, f"{which}_less_cpn", 1))


def yr_of_month(tm: Terms, mo: float) -> float:
    """발행일부터 mo 개월 — 행사금액 산식에 넣는 경과연수.

    acc_basis 1 이면 계약 개월 ÷ 12, 0 이면 종전(Actual/365, rem_m 개월 = T 년)이다.
    exercise_amounts · 만기상환금액 · 화면 캡션이 모두 이 잣대를 쓴다.
    """
    if int(getattr(tm, "acc_basis", 1)): return mo/12
    rem_m = float(getattr(tm, "rem_m", 0.0) or tm.T*12)
    return ((mo - tm.elapsed_m)/max(1e-9, rem_m))*tm.T + tm.elapsed_m/12


def mat_years(tm: Terms):
    """만기상환금액 산식의 (경과연수, 발행일 기준 개월). exercise_amounts 와 같은 잣대."""
    mo = tm.elapsed_m + float(getattr(tm, "rem_m", 0.0) or tm.T*12)
    return yr_of_month(tm, mo), mo


def mat_red_formula(tm: Terms) -> float:
    """만기상환금액의 **산식값** (직접 입력 금액은 보지 않는다). 화면·검증·엔진이 함께 쓴다."""
    yrs, mo = mat_years(tm)
    return 100*(1 + ded_prem(yrs, tm.ytm, eff_cpn(tm), tm.ytm_cmp, ded_of(tm, "m"),
                             mo, tm.ipay))


def mat_implied(tm: Terms, amt: float):
    """직접 넣은 만기상환금액이 암시하는 보장수익률 — 공제 방식까지 같은 잣대로."""
    yrs, mo = mat_years(tm)
    return ded_implied(amt/100 - 1, yrs, eff_cpn(tm), tm.ytm_cmp, ded_of(tm, "m"), mo, tm.ipay)


SCOPE_NOTE = (
    "적용범위 — 주가를 관찰하거나 역산할 수 있는 **단일 종류**의 전환사채·"
    "신주인수권부사채·상환전환우선주와, 그 지분에 붙은 주주간계약의 풋·콜을 "
    "이항격자로 잰다.")
UNMODELLED_NOTE = (
    "반영하지 않은 것 — 순자본비율 등 상환재원 제약에 따른 실제 상환 지연(배당가능이익은 "
    "연도별 추정치를 넣었을 때만 반영), 기간에 따라 달라지는 배당률(step-up), 조기상환 청구 "
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


def parse_sched(txt: str, tm: Terms) -> list:
    """계약서의 «회차별 행사금액표» 를 [(발행일부터 개월, 금액%)] 로 읽는다.

    한 줄에 한 회차. 앞은 **날짜**(2026-12-05) 또는 **개월수**(6), 뒤는 금액이다.
    구분은 탭·쉼표·공백 아무거나. ``%`` 와 천단위 쉼표는 떼어 낸다. 빈 줄과 ``#`` 로
    시작하는 줄은 건너뛴다. 공시 표를 그대로 붙여 넣을 수 있게 하려는 것이다.

    읽지 못한 줄은 버리지 않고 ``(None, 원문)`` 으로 남긴다 — validate() 가 줄 번호와
    함께 알려 주어야 사용자가 어디가 잘못됐는지 안다.
    """
    if not (txt or "").strip(): return []
    try:
        di = dt.date.fromisoformat(tm.d_issue)
    except Exception:
        di = None
    out = []
    for ln in txt.splitlines():
        t = ln.strip()
        if not t or t.startswith("#"): continue
        parts = [x for x in re.split(r"[\t,;|]+|\s{2,}|\s+", t) if x]
        if len(parts) < 2: out.append((None, t)); continue
        a, b = parts[0], parts[-1]
        try:
            amt = float(b.replace("%", "").replace(",", "").strip())
        except ValueError:
            out.append((None, t)); continue
        mo = None
        if re.fullmatch(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", a) and di is not None:
            try:
                d = dt.date.fromisoformat(a.replace(".", "-").replace("/", "-"))
                mo = months_between(di, d)
            except Exception:
                mo = None
        else:
            try: mo = float(a.replace("개월", "").replace(",", "").strip())
            except ValueError: mo = None
        out.append((mo, amt) if mo is not None else (None, t))
    return out


def sched_rows(txt: str, tm: Terms) -> list:
    """parse_sched 결과 중 **읽힌 줄만** 개월 순으로."""
    return sorted([(m, v) for m, v in parse_sched(txt, tm) if m is not None],
                  key=lambda x: x[0])


def sched_at(rows: list, month: float, tol: float = 0.5):
    """그 개월에 해당하는 표의 금액. 없으면 None — 부르는 쪽이 산식으로 넘어간다."""
    best, bd = None, tol
    for m, v in rows:
        d = abs(m - month)
        if d <= bd: best, bd = v, d
    return best


def sched_steps(rows: list, cmonth, n: int) -> dict:
    """계약서의 **한 회차를 격자의 한 스텝에** 배정한다 — ``{스텝: (개월, 금액)}``.

    계약이 「2026-12-05 하루」라고 정한 조기상환을 격자가 여러 노드에서 열면, 특정일
    권리가 **기간 권리로 부풀어** 값이 커진다. 그래서 회차마다 가장 가까운 스텝 «하나» 만
    고른다. 거리가 같으면 **앞선 스텝**을 고른다 — 규칙을 정해 두어야 같은 계약이 항상
    같은 격자를 만든다.

    노드가 회차보다 성기면 두 회차가 한 스텝에 몰린다. 그때는 **그 스텝에 더 가까운
    회차**가 이기고(거리가 같으면 앞선 회차), 밀려난 회차는 격자에서 사라진다 —
    ``validate()`` 가 그 사실을 경고한다. 노드를 늘려야 담긴다.
    """
    out = {}
    for mo, v in rows:
        i = min(range(n + 1), key=lambda k: (abs(cmonth(k) - mo), k))
        d = abs(cmonth(i) - mo)
        if i in out and (abs(cmonth(i) - out[i][0]), out[i][0]) <= (d, mo): continue
        out[i] = (mo, v)
    return out


def exercise_dates(tm: Terms, n: int, dt_: float, s: float, e: float, f: float, rows=None):
    """행사일 목록 {스텝: 계약 행사월(발행일부터 개월)} · 격자에서 빠진 회차 · 상시행사 여부.

    조기상환·매도청구·주주간계약 풋·콜이 모두 이 함수 «하나» 로 행사일을 정한다.
    계약서의 행사일(표가 있으면 표의 회차, 없으면 시작일부터 주기마다)을 하나씩 만들고,
    날짜마다 **계약일 이후 첫 노드**(허용 일수 안에서 앞선 노드는 같은 날 — EXDATE_RULE)에
    배정한다. 기간 중 언제든지(주기가 격자 한 칸의 1.5배보다 짧다)면 기간 안의 모든 노드가
    행사일이고, 그 노드의 계약 행사월은 평가기준일까지의 개월 + 이후 구간을 잔존 개월로
    균등하게 나눈 값이다(``cmonth``). 평가기준일 전에 지난 회차는 싣지 않는다.
    """
    rem_m = float(getattr(tm, "rem_m", 0.0) or tm.T*12)
    cmonth = lambda i: tm.elapsed_m + i*rem_m/max(1, n)
    st_lo, st_hi = step_mapper(tm, n, dt_)
    gapm = rem_m/max(1, n)
    out, dropped = {}, []
    if rows:
        ms = [m for m, _ in rows]
    elif s > e:
        return out, dropped, False
    elif f < gapm*1.5:
        for i in range(max(st_lo(s), 0), min(st_hi(e), n) + 1):
            out[i] = cmonth(i)
        return out, dropped, True
    else:
        ms, k = [], 0
        while s + k*f <= e + 1e-6:
            ms.append(s + k*f); k += 1
    for m in ms:
        if m < tm.elapsed_m - 1e-6:          # 평가기준일 전에 지난 회차
            continue
        i = st_lo(m)
        if i > n:
            continue
        if i in out:                          # 격자가 성겨 두 회차가 한 노드에 — 앞 회차가 남는다
            dropped.append(m); continue
        out[i] = m
    return out, dropped, False


# ── 배당가능이익 상환 제약 (상환전환우선주) ──
# 상환주식은 회사의 이익으로 상환한다(상법 제345조 — 배당가능이익은 제462조). 연도별 추정 배당가능이익을
# 넣으면 상환청구 시점마다 «실제로 받는 현금 일정» 을 정하고 그 현재가치를 상환청구 금액 자리에 쓴다.
#   · 발생연도 Y 의 배당가능이익은 Y+1 년의 우선배당·상환 재원이다 (결산 확정 뒤 쓴다).
#   · 우선배당을 먼저 뺀다 — 청구한 해의 평가대상 우선배당과, 아직 청구하지 않은 동순위 상품의 우선배당.
#   · 평가대상이 선순위가 기본이다(다른 상품은 평가대상 뒤라 영향 없음). 동순위로 고른 상품은 그 해
#     상환청구가 열려 있으면 함께 청구한다고 보고(조건이 비슷한 투자자는 같은 판단을 한다), 남은 상환금
#     비율로 나눈다 — 상환금은 사용자가 넣은 발행일·발행총액·보장수익률로 앱이 계산한다.
#   · 갚지 못한 금액은 다음 해 같은 날(청구일 + 1년, 가장 가까운 계산 시점)로 넘기고 dp_delay 를 붙인다.
#   · 할인은 그 기간의 위험 선도이자율(트리 12행)이다. 만기 뒤 지급분은 마지막 구간의 선도이자율로 잇는다.
#   · 넣지 않은 해는 제한이 없다(배당이 가능하다는 전제). 아무 해도 넣지 않으면 종전 계산과 같다.
# 배당가능이익은 주가와 무관한 고정값이라 청구 시점마다 지급 일정이 하나로 정해진다 — 격자 구조를 그대로
# 쓰고, 수식 조서(00 배당가능이익 상환)도 같은 식으로 다시 계산한다.
DP_OTHER_KEYS = ("name", "rank", "issue", "face", "yld", "cmp", "start", "end", "div")
DP_RANKS = {"senior": "평가대상이 선순위 (이 상품은 평가대상 뒤에 상환)",
            "pari": "동순위 (같은 해 상환청구 금액 비율로 나눔)"}
DP_INF = 1e300
DP_KMAX = 60


def dp_profits(tm: Terms) -> dict:
    """{발생연도: 배당가능이익(원)} — 비운 줄은 빼고 읽는다."""
    out = {}
    for r in getattr(tm, "dp_rows", None) or []:
        if not isinstance(r, dict) or r.get("fy") in (None, "") or r.get("amt") in (None, ""):
            continue
        try:
            fy = float(r["fy"])
            if not fy.is_integer(): continue  # 소수 연도 — dp_issues 가 오류 문장을 낸다
            out[int(fy)] = float(r["amt"])
        except (TypeError, ValueError, OverflowError):
            continue                      # 숫자가 아닌 줄 — dp_issues 가 오류 문장을 낸다
    return out


def dp_active(tm: Terms) -> bool:
    return is_rcps(tm) and bool(dp_profits(tm))


def dp_others(tm: Terms) -> list:
    """동순위로 고른 다른 상품 — 날짜는 datetime, 금액·율은 실수. 선순위 가정 상품은 평가에 쓰지 않는다."""
    out = []
    for r in getattr(tm, "dp_others", None) or []:
        if not isinstance(r, dict) or str(r.get("rank") or "senior") != "pari":
            continue
        out.append(dict(name=str(r.get("name") or "다른 상품"),
                        issue=dt.datetime.fromisoformat(str(r["issue"])),
                        face=float(r["face"]), yld=float(r.get("yld") or 0.0), cmp=int(float(r.get("cmp", 1))),
                        start=dt.datetime.fromisoformat(str(r["start"])),
                        end=dt.datetime.fromisoformat(str(r["end"])), div=float(r.get("div") or 0.0)))
    return out


def dp_other_issues(tm: Terms) -> list:
    """다른 상품 표에서 계산을 막아야 하는 입력 — 문장 목록."""
    out = []
    for k, r in enumerate(getattr(tm, "dp_others", None) or [], 1):
        if not isinstance(r, dict):
            out.append(f"다른 상품 {k}번째 줄의 형식이 올바르지 않습니다."); continue
        nm = r.get("name") or f"{k}번째 상품"
        if set(r) - set(DP_OTHER_KEYS):
            out.append(f"{nm}: 알 수 없는 칸이 있습니다 ({', '.join(sorted(set(r) - set(DP_OTHER_KEYS)))}).")
        if str(r.get("rank") or "senior") not in DP_RANKS:
            out.append(f"{nm}: 순위는 «평가대상이 선순위» 또는 «동순위» 가운데 고르십시오."); continue
        if str(r.get("rank") or "senior") != "pari":
            continue                      # 선순위 가정 — 평가대상 뒤라 다른 칸이 필요 없다
        try:
            a, b, c = (dt.date.fromisoformat(str(r[x])) for x in ("issue", "start", "end"))
        except (KeyError, TypeError, ValueError):
            out.append(f"{nm}: 발행일·상환청구 시작일·종료일을 YYYY-MM-DD 로 넣으십시오."); continue
        if not (a <= b <= c):
            out.append(f"{nm}: 발행일 ≤ 상환청구 시작일 ≤ 종료일이어야 합니다.")
        try:
            v = float(r["face"])
            if not (v > 0 and math.isfinite(v)):
                out.append(f"{nm}: 발행총액(원)을 0 보다 크게 넣으십시오.")
        except (KeyError, TypeError, ValueError):
            out.append(f"{nm}: 발행총액(원)을 넣으십시오.")
        for x, lab in (("yld", "상환 보장수익률"), ("div", "우선배당률")):
            try:
                v = float(r.get(x) or 0.0)
                if not (v >= 0 and math.isfinite(v)): out.append(f"{nm}: {lab}은 0 이상의 유한한 값이어야 합니다.")
            except (TypeError, ValueError):
                out.append(f"{nm}: {lab}을 숫자로 넣으십시오.")
        try:
            ok = float(r.get("cmp", 1)) in (0.0, 1.0)     # 0.5 를 0 으로 자르지 않는다
        except (TypeError, ValueError):
            ok = False
        if not ok:
            out.append(f"{nm}: 복리 방식은 1(연복리) 또는 0(단리)입니다.")
    return out


def dp_issues(tm: Terms) -> list:
    """배당가능이익 입력 가운데 계산을 막아야 하는 것 — 문장 목록."""
    if not is_rcps(tm):
        return []
    out, seen = [], set()
    for k, r in enumerate(getattr(tm, "dp_rows", None) or [], 1):
        if not isinstance(r, dict) or set(r) - {"fy", "amt"}:
            out.append(f"배당가능이익 {k}번째 줄의 형식이 올바르지 않습니다."); continue
        if r.get("fy") in (None, "") and r.get("amt") in (None, ""):
            continue
        try:
            fy_ = float(r["fy"]); amt = float(r["amt"])
            if not fy_.is_integer(): raise ValueError
            fy = int(fy_)
        except (KeyError, TypeError, ValueError, OverflowError):
            out.append(f"배당가능이익 {k}번째 줄 — 발생연도와 금액(원)을 함께 넣으십시오."); continue
        if fy in seen: out.append(f"배당가능이익 {fy}년이 두 번 있습니다.")
        seen.add(fy)
        if amt < 0 or not math.isfinite(amt): out.append(f"배당가능이익 {fy}년은 0 이상의 금액이어야 합니다.")
    try:
        _g = float(getattr(tm, "dp_delay", 0.0) or 0.0)
        if not (_g >= 0 and math.isfinite(_g)):
            out.append("이월 상환금 가산율은 0 이상이어야 합니다.")
    except (TypeError, ValueError):
        out.append("이월 상환금 가산율을 숫자로 넣으십시오.")
    if dp_from_md(tm) is None:
        out.append("재원 사용 시작일은 «월-일»(예: 04-01) 형식이어야 합니다 (02-29 는 쓸 수 없습니다).")
    if seen and dp_from_md(tm) is not None:
        try:
            lim = dp_fund_year(tm, dt.datetime.fromisoformat(str(tm.d_base))) + DP_KMAX - 2
            if max(seen) > lim:
                out.append(f"배당가능이익 발생연도는 {lim}년까지 넣을 수 있습니다 — 지급 일정은 평가기준일부터 "
                           f"{DP_KMAX}년까지만 따라갑니다.")
        except (TypeError, ValueError):
            pass
    out += dp_other_issues(tm)
    if seen and int(getattr(tm, "put_bdt", 0) or 0):
        out.append("배당가능이익 반영은 금리 이항모형(상환청구권 금리모형)과 함께 쓸 수 없습니다 — 한쪽을 끄십시오.")
    return out


def dp_warnings(tm: Terms) -> list:
    """배당가능이익 제약을 켰을 때 평가자가 알아야 할 것 — 계산은 막지 않는다."""
    if not dp_active(tm) or dp_issues(tm):
        return []
    derive(tm)
    n = int(tm.n); dt_ = tm.T/n
    P = dp_profits(tm)
    EA = exercise_amounts(tm, n, dt_)
    w = []
    # 청구 시점(만기에 현금상환이면 만기도)마다 지급 일정을 따라가며, 넣지 않은 해(제한 없음)에 갚는다고 본
    # 금액이 있으면 그 발생연도를 알린다 — 넘긴 금액을 갚는 뒤 해도 포함한다.
    claims = [(i, EA["put"](i)) for i in sorted(EA["p_dates"])]
    if int(getattr(tm, "mat_mode", 0)) == 1:
        claims.append((n, EA["red"]))
    DP = EA["dp"]; miss = set()
    for i, amt in claims:
        y0 = dp_fund_year(tm, dp_step_dt(tm, dt_, i))
        for k, m, at, cap, be, bo, pe, df in DP.schedule(i, amt)["rows"]:
            if cap >= DP_INF/10 and be > 1e-12:
                miss.add(y0 + k - 1)
    if issuer_redeem(tm):                    # 발행자 상환권 행사일 — 넣지 않은 해는 제한 없이 행사할 수 있다고 본다
        for i in EA["k_dates"]:
            y = dp_fund_year(tm, dp_step_dt(tm, dt_, i)) - 1
            if y not in P: miss.add(y)
    miss = sorted(miss)
    if miss:
        w.append("상환청구·만기상환(다음 해로 넘긴 금액 포함)·발행자 상환권에 쓰는 해 중 발생연도 " + ", ".join(str(y) for y in miss) + "년의 배당가능이익을 넣지 않아 "
                 "그 다음 해에는 제한 없이 상환된다고 봤습니다(기본 전제). 추정치가 있으면 넣으십시오.")
    face = float(tm.face_total); de = 100*dp_div_rate(tm)
    low = [y for y, a in sorted(P.items()) if a*100/face < de - 1e-9]
    if low:
        w.append("발생연도 " + ", ".join(str(y) for y in low) + "년 배당가능이익이 평가대상 우선배당보다 작습니다 — "
                 "평가는 우선배당을 계약대로 받는다고 보고 상환 재원만 줄입니다(배당 부족은 따로 반영하지 않음).")
    snr = [str(r.get("name") or "다른 상품") for r in (getattr(tm, "dp_others", None) or [])
           if isinstance(r, dict) and str(r.get("rank") or "senior") != "pari"]
    if snr:
        w.append("평가대상이 선순위라고 보아 " + ", ".join(snr) + " 은(는) 평가대상 상환 재원에서 빼지 않았습니다.")
    if issuer_redeem(tm):
        _all = set(EA["k_dates"]); _ok = {i for i in _all if EA["k_on"](i)}
        if _all - _ok:
            w.append(f"발행자 상환권은 재원이 부족한 {len(_all - _ok)}개 행사일(전체 {len(_all)}개)에서 행사할 수 없는 "
                     "것으로 봤습니다 — 회사가 상환할 때도 배당가능이익이 필요합니다.")
    elif int(getattr(tm, "issuer_call", 0)) == 2:
        w.append("제3자 지정 매도청구권은 제3자가 자기 돈으로 사므로 배당가능이익 제한을 직접 받지 않습니다 — "
                 "상환청구 가치가 바뀌어 행사 판단만 달라집니다.")
    return w


def dp_from_md(tm: Terms):
    """재원 사용 시작일 (월, 일). 형식이 틀리면 None — dp_issues 가 막는다."""
    try:
        m, d = (int(x) for x in str(getattr(tm, "dp_from", "01-01") or "01-01").split("-"))
        dt.date(2001, m, d)                    # 윤년이 아닌 해에 있는 날만 (02-29 는 받지 않는다)
        return m, d
    except (TypeError, ValueError):
        return None


def dp_fund_year(tm: Terms, at) -> int:
    """그 날짜에 쓰는 재원 연도 Y (발생연도 Y−1 의 배당가능이익). 재원 사용 시작일 전이면 한 해 앞이다."""
    m, d = dp_from_md(tm) or (1, 1)
    return at.year if (at.month, at.day) >= (m, d) else at.year - 1


def dp_fund_start(tm: Terms, year: int) -> dt.datetime:
    """재원 연도 year 가 시작하는 날 (재원 사용 시작일)."""
    m, d = dp_from_md(tm) or (1, 1)
    return dt.datetime(year, m, d)


def dp_other_claim(tm: Terms, o: dict, year: int, at: dt.datetime):
    """동순위 상품 o 가 재원 연도 year 에 상환청구하는 날 — 그 해 안에 청구 기간(발행 뒤)이 없으면 None.

    그 해 청구 기간 가운데 평가대상 지급일(at)에 가장 가까운 날로 본다(상환금 가산 계산에 쓴다).
    """
    lo = max(o["start"], o["issue"], dp_fund_start(tm, year))
    hi = min(o["end"], dp_fund_start(tm, year + 1) - dt.timedelta(days=1))
    if lo > hi:
        return None
    return min(max(at, lo), hi)


def dp_div_rate(tm: Terms) -> float:
    """평가대상 우선배당률(발행가 기준, 연) — 재량 배당이어도 지급하면 재원을 쓰므로 계약 배당률을 쓴다."""
    return cpn_basis_rate(tm)


def dp_step_dt(tm: Terms, dt_: float, i: int) -> dt.datetime:
    """스텝 i 의 날짜 — 평가기준일 + 스텝 × Δt × 365일을 날 단위로 반올림한다(node_dates 와 같은 날).

    재원 연도 경계(재원 사용 시작일)를 하루 안쪽 시각 차이로 넘나들지 않게 한다. 딱 반일 때는 짝수 쪽으로
    (파이썬 round). 수식 조서도 같은 규칙의 식을 쓴다.
    """
    y = i*(dt_*365)                                   # node_dates 와 같은 식
    f = math.floor(y)
    d = f + (f % 2) if abs(y - f - 0.5) < 1e-9 else math.floor(y + 0.5)   # 딱 반이면 짝수 쪽 (파이썬 round 와 같다)
    return dt.datetime.fromisoformat(tm.d_base) + dt.timedelta(days=d)


def dp_year_step(k: int, dt_: float) -> int:
    """청구일 + k 년에 가장 가까운 스텝 수 — 엑셀 ROUND(k/Δt, 0) 과 같다(0.5 는 올림)."""
    return int(math.floor(k/dt_ + 0.5))


def dp_grow(face: float, yld: float, cmp: int, issue: dt.datetime, at: dt.datetime) -> float:
    """다른 상품의 상환금(원) = 발행총액 × 보장수익률 가산 (연복리 또는 단리, 실제 경과일 ÷ 365)."""
    tau = max(0.0, (at - issue).total_seconds()/86400/365)
    return face*((1 + yld)**tau if int(cmp) == 1 else (1 + yld*tau))


class DPPlan:
    """배당가능이익 상환 일정을 스텝마다 만든다 — 엔진·값 조서·수식 조서가 같은 계산을 본다."""

    def __init__(self, tm: Terms, n: int, dt_: float):
        self.tm, self.n, self.dt = tm, n, dt_
        self.P = dp_profits(tm)
        self.face = float(tm.face_total)
        self.div_e = 100*dp_div_rate(tm)
        self.g = float(getattr(tm, "dp_delay", 0.0) or 0.0)
        self.others = dp_others(tm)
        RF, CR = curves(tm)
        f = [forward_rate(CR, j*dt_, (j+1)*dt_) for j in range(n)]
        self.cum = [0.0]
        for j in range(n): self.cum.append(self.cum[-1] + f[j]*dt_)
        self.flast = f[-1] if f else 0.0
        mx = max(self.P) if self.P else 0
        y0 = dp_fund_year(tm, dp_step_dt(tm, dt_, 0))
        self.K = max(0, min(DP_KMAX, mx + 2 - y0))        # 이 뒤 해는 넣지 않았으므로 제한이 없다

    def cum_at(self, m: int) -> float:
        return self.cum[m] if m <= self.n else self.cum[self.n] + (m - self.n)*self.flast*self.dt

    def cap(self, year: int, ded: float) -> float:
        """year 년에 쓸 수 있는 재원(평가대상 100 기준) — 넣지 않은 해(발생연도 year−1)는 무한대."""
        p = self.P.get(year - 1)
        if p is None: return DP_INF
        return max(0.0, p*100/self.face - ded)

    def other_div(self, o, year: int) -> float:
        """동순위 상품의 그 재원 연도 우선배당 — 그 해 안에(다음 해 시작일 전) 발행되면 그 해 배당을 먼저 뺀다."""
        return o["div"]*o["face"]*100/self.face if o["issue"] < dp_fund_start(self.tm, year + 1) else 0.0

    def live(self, y0: int) -> list:
        """재원 연도 y0 에 아직 남은 동순위 상품 — 청구 기간이 y0 전에 끝났으면 그 전에 청구해 상환을 마쳤다고 본다."""
        fs = dp_fund_start(self.tm, y0)
        return [o for o in self.others if o["end"] >= fs]

    def schedule(self, i: int, amount: float) -> dict:
        """스텝 i 에 상환청구하면 받는 현금 일정 — {pv, rows=[(k, 스텝, 날짜, 재원, 평가대상 잔액, 동순위 잔액 합, 지급, 할인계수)]}."""
        t0 = dp_step_dt(self.tm, self.dt, i)
        y0 = dp_fund_year(self.tm, t0)          # 청구 시점의 재원 연도 — 넘긴 금액은 y0+1, y0+2 … 해의 재원으로 갚는다
        oth = self.live(y0)
        be = float(amount); bo = [0.0]*len(oth); joined = [False]*len(oth)
        pv, rows = 0.0, []
        for k in range(self.K + 1):
            m = i + dp_year_step(k, self.dt)
            at = dp_step_dt(self.tm, self.dt, m)
            ded = self.div_e if k == 0 else 0.0
            for x, o in enumerate(oth):
                if not joined[x]:
                    ded += self.other_div(o, y0 + k)
                    c_ = dp_other_claim(self.tm, o, y0 + k, at)
                    if c_ is not None:
                        joined[x] = True
                        bo[x] = dp_grow(o["face"], o["yld"], o["cmp"], o["issue"], c_)*100/self.face
            cap = self.cap(y0 + k, ded)
            tot = be + sum(bo)
            if cap >= DP_INF/10:
                pe, po = be, list(bo)
            else:
                pe = min(be, cap*be/tot) if tot > 0 else 0.0
                po = [min(b, cap*b/tot) if tot > 0 else 0.0 for b in bo]
            df = math.exp(-(self.cum_at(m) - self.cum_at(i)))
            rows.append((k, m, at, cap, be, sum(bo), pe, df))
            pv += pe*df
            be = (be - pe)*(1 + self.g)
            bo = [(b - p_)*(1 + self.g) for b, p_ in zip(bo, po)]
        return dict(pv=pv, rows=rows, left=be)

    def call_ok(self, i: int, amount: float, share: float) -> bool:
        """발행자 상환권 — 그 해 재원(우선배당을 뺀 뒤)이 상환할 금액 × 한도와 같은 해 동순위 상환금을 함께
        갚을 수 있을 때만 행사한다(비율로 나누면 평가대상 몫이 전액이 되는 조건)."""
        t0 = dp_step_dt(self.tm, self.dt, i)
        y0 = dp_fund_year(self.tm, t0)
        oth = self.live(y0)
        ded = self.div_e + sum(self.other_div(o, y0) for o in oth)
        bo = 0.0
        for o in oth:
            c_ = dp_other_claim(self.tm, o, y0, t0)
            if c_ is not None:
                bo += dp_grow(o["face"], o["yld"], o["cmp"], o["issue"], c_)*100/self.face
        return self.cap(y0, ded) >= amount*share + bo - 1e-9


def dp_value_sheet(wb, tm: Terms):
    """값 조서에 «00 배당가능이익 상환» 시트를 더한다 — 입력, 연도별 재원, 청구 시점별 실제 지급 현재가치.

    수식 조서는 같은 이름의 시트를 수식으로 만든다(build_xlsx_formula). 숫자는 엔진과 같은 DPPlan 에서 나온다.
    """
    from openpyxl.styles import Font, PatternFill, Alignment
    if not dp_active(tm):
        return
    derive(tm)
    n = int(tm.n); dt_ = tm.T/n
    EA = exercise_amounts(tm, n, dt_)
    DP = EA["dp"]
    name = "00 배당가능이익 상환"
    if name in wb.sheetnames: del wb[name]
    W = wb.create_sheet(name); W.sheet_view.showGridLines = False
    hdr = Font(bold=True, color="FFFFFF"); fill = PatternFill("solid", fgColor="2F4B66")
    bold = Font(bold=True); grey = Font(color="6B7480", size=9)
    for col, w in zip("BCDEFGHI", (30, 16, 18, 18, 16, 14, 16, 16)):
        W.column_dimensions[col].width = w
    r = 2
    W.cell(r, 2, "배당가능이익 상환 — 청구 시점마다 실제로 받는 현금의 현재가치").font = Font(bold=True, size=13); r += 1
    W.cell(r, 2, "발생연도 Y 의 배당가능이익은 Y+1 년(재원 사용 시작일부터)의 재원이다. 우선배당을 먼저 빼고, 평가대상이 선순위(기본)이며 동순위 "
                 "상품과는 남은 상환금 비율로 나눈다. 갚지 못한 금액은 다음 해(청구일 + 1년에 가장 가까운 계산 시점)로 넘긴다. "
                 "넣지 않은 해는 제한이 없다. 할인은 위험 선도이자율(만기 뒤는 마지막 구간 값).").font = grey
    r += 2
    def table(head, rows, fmts):
        nonlocal r
        for c, h in enumerate(head):
            cl = W.cell(r, 2+c, h); cl.font = hdr; cl.fill = fill; cl.alignment = Alignment(wrap_text=True)
        r += 1
        for row in rows:
            for c, v in enumerate(row):
                cl = W.cell(r, 2+c, v)
                if isinstance(v, str): cl.data_type = 's'    # 입력 문자열(상품 이름 등)은 엑셀 수식으로 읽히지 않는다
                if fmts[c]: cl.number_format = fmts[c]
            r += 1
        r += 1
    W.cell(r, 2, "입력").font = bold; r += 1
    _fm, _fd = dp_from_md(tm) or (1, 1)
    table(["항목", "값"], [["평가대상 발행총액 (원)", float(tm.face_total)],
                          ["평가대상 우선배당률 (발행가 기준, 연)", dp_div_rate(tm)],
                          ["넘긴 상환금 연 가산율", float(getattr(tm, "dp_delay", 0.0) or 0.0)],
                          ["재원 사용 시작일 (이 날 전은 그 전해 재원)", f"매년 {_fm}월 {_fd}일"]], [None, "#,##0.####"])
    face = float(tm.face_total)
    table(["발생연도", "배당가능이익 (원)", "재원으로 쓰는 기간", "100 기준", "우선배당 뺀 뒤 (100 기준, 청구한 해)"],
          [[fy, a, f"{fy+1}-{_fm:02d}-{_fd:02d} ~ {fy+2}-{_fm:02d}-{_fd:02d} 전날", a*100/face,
            max(0.0, a*100/face - 100*dp_div_rate(tm))] for fy, a in sorted(DP.P.items())],
          ["0", "#,##0", "0", "#,##0.0000", "#,##0.0000"])
    if DP.others:
        table(["동순위 상품", "발행일", "발행총액 (원)", "상환 보장수익률", "복리", "상환청구 시작일", "상환청구 종료일", "우선배당률"],
              [[o["name"], o["issue"].date(), o["face"], o["yld"], "연복리" if o["cmp"] == 1 else "단리",
                o["start"].date(), o["end"].date(), o["div"]] for o in DP.others],
              [None, "yyyy-mm-dd", "#,##0", "0.00%", None, "yyyy-mm-dd", "yyyy-mm-dd", "0.00%"])
    snr = [str(x.get("name")) for x in (getattr(tm, "dp_others", None) or [])
           if isinstance(x, dict) and str(x.get("rank") or "senior") != "pari"]
    if snr:
        W.cell(r, 2, "평가대상이 선순위라고 보아 재원에서 빼지 않은 상품: " + ", ".join(snr)).font = grey; r += 2
    W.cell(r, 2, "청구 시점별 상환청구 가치 (100 기준)").font = bold; r += 1
    rows = []
    for i in sorted(EA["p_dates"]):
        sc = DP.schedule(i, EA["put"](i))
        last = max((x[2] for x in sc["rows"] if x[6] > 1e-12), default=None)
        rows.append([i, dp_step_dt(tm, dt_, i).date(), EA["put"](i), sc["pv"],
                     sc["pv"]/EA["put"](i) if EA["put"](i) else None, last.date() if last else None])
    table(["스텝", "청구일", "계약 상환금", "실제 지급 현재가치", "비율", "마지막 지급일"], rows,
          ["0", "yyyy-mm-dd", "#,##0.0000", "#,##0.0000", "0.00%", "yyyy-mm-dd"])
    sc = DP.schedule(n, EA["red"])
    W.cell(r, 2, "만기상환 (존속기간 만료 시 상환) — 만기 노드에서 청구한 것과 같은 일정").font = bold; r += 1
    table(["만기일", "계약 만기상환금액", "실제 지급 현재가치"], [[dp_step_dt(tm, dt_, n).date(), EA["red"], sc["pv"]]],
          ["yyyy-mm-dd", "#,##0.0000", "#,##0.0000"])
    if issuer_redeem(tm):
        blk = sorted(i for i in EA["k_dates"] if not EA["k_on"](i))
        W.cell(r, 2, f"발행자 상환권 — 재원이 부족해 행사할 수 없는 행사일 {len(blk)}개 / 전체 {len(EA['k_dates'])}개").font = bold; r += 1
        if blk:
            table(["스텝", "날짜"], [[i, dp_step_dt(tm, dt_, i).date()] for i in blk], ["0", "yyyy-mm-dd"])


def exercise_amounts(tm: Terms, n: int, dt_: float) -> dict:
    """조기상환·매도청구·만기 **행사금액** 을 한 곳에서 만든다.

    같은 산식이 엔진·BDT 갈래·값 조서·EIR 표·조건표 다섯 군데에 흩어져 있었다.
    흩어지면 하나를 고칠 때 나머지가 남아 **같은 금액을 두 값으로 계산한다** —
    실제로 EIR 표(``eir_expect``)는 계약 개월수를, 격자는 Actual/365 를 쓰고 있었다.

    돌려주는 것
      ``cmonth(i)``  스텝 i 의 발행일부터 경과 **개월** — 계약이 세는 방식
      ``cyear(i)``   행사금액 산정에 쓰는 경과 **연수**
      ``put(i)``     조기상환 행사금액 (행사 가능 여부는 보지 않는다 — 부르는 쪽이 판단)
      ``call(i)``    매도청구 행사금액
      ``red``        만기상환금액
      ``put_at_month(m)`` · ``call_at_month(m)``  스텝이 아니라 **개월**로 묻는 입구.
                     EIR 표·조건표처럼 격자가 없는 자리가 쓴다
      ``p_rows`` · ``k_rows``  계약서에서 읽은 회차별 표 (없으면 빈 목록)
      ``tol``        표를 노드에 붙여 읽는 허용 개월
      ``p_on(i)`` · ``k_on(i)``  표가 정한 행사 가능 시점. 표가 없으면 **None** —
                     부르는 쪽이 종전대로 시작·종료·주기를 쓴다

    계약서가 회차별 금액을 확정 숫자로 준 경우(공시에 표로 실린다) **그 표가 산식보다
    앞선다.** 산식은 표가 없는 회차에만 쓰인다.
    """
    rem_m = float(getattr(tm, "rem_m", 0.0) or tm.T*12)
    cmonth = lambda i: tm.elapsed_m + i*rem_m/max(1, n)
    # 계약은 개월로 센다 — 「발행일로부터 6개월이 되는 날」의 조기상환율은 0.5년으로
    # 계산한 값이다. 할인기간(Actual/365)을 그대로 쓰면 회차마다 조금씩 어긋나
    # 조서가 공시 표와 맞지 않는다. acc_basis=0 이면 종전(Actual/365)이다.
    yr_of = lambda mo: yr_of_month(tm, mo)
    cyear = lambda i: yr_of(cmonth(i))

    # 계약서가 회차별 금액을 «확정 숫자» 로 준 경우 그 표가 산식보다 앞선다.
    # 노드 간격이 회차 간격과 어긋날 수 있으므로 반 개월까지 붙여 읽는다.
    p_rows = sched_rows(getattr(tm, "p_sched", ""), tm)
    k_rows = sched_rows(getattr(tm, "k_sched", ""), tm)
    tol = max(0.5, 0.5*rem_m/max(1, n))
    # ── 행사일 목록 — 모든 계산이 이 목록 «하나» 를 쓴다 ──
    # 계약서의 행사일(표가 있으면 표의 회차, 없으면 시작일부터 주기마다)을 하나씩
    # 만들고, 날짜마다 **계약일 이후 첫 노드**(허용 일수 안에서 앞선 노드는 같은 날 — EXDATE_RULE)
    # 에 배정한다. 시작·종료를 서로 다른
    # 규칙(이후 첫 노드 / 이전 마지막 노드)으로 잡으면 같은 날의 풋과 콜이 다른
    # 노드로 갈라져 우선순위가 작동할 자리가 사라진다. 행사금액은 옮겨진 노드가
    # 아니라 **계약일의 경과기간** 으로 계산한다 — 공시 표의 확정 금액과 같아진다.
    # 기간 중 언제든지(주기가 격자 간격 수준)면 창 안의 모든 노드가 행사일이다.
    p_dates, p_drop, p_cont = exercise_dates(tm, n, dt_, tm.p_s, tm.p_e, tm.p_f, p_rows)
    k_dates, k_drop, k_cont = exercise_dates(tm, n, dt_, tm.k_s, tm.k_e, tm.k_f, k_rows)
    _pt, _kt = dict(p_rows), dict(k_rows)
    # 표가 준 확정 금액 {스텝: (개월, 금액)} — 00 행사금액표가 그대로 싣는다.
    p_steps = {i: (m, _pt[m]) for i, m in p_dates.items() if m in _pt}
    k_steps = {i: (m, _kt[m]) for i, m in k_dates.items() if m in _kt}

    # 이미 지급한 이자·배당을 빼는 방식은 권리마다 계약이 정한다 (ded_prem).
    _c = eff_cpn(tm)

    def _formula_put(mo):
        if tm.p_mode == "accrue":
            return 100*(1 + ded_prem(yr_of(mo), tm.p_yield, _c, tm.p_cmp,
                                     ded_of(tm, "p"), mo, tm.ipay))
        return float(tm.p_rate)

    def _formula_call(mo):
        return 100*(1 + ded_prem(yr_of(mo), tm.k_prem, _c, tm.k_cmp,
                                 ded_of(tm, "k"), mo, tm.ipay))

    def put_at_month(mo):
        # 개월로 묻는 자리(상각표의 기대만기)는 표를 개월로 찾는다 — 스텝이 없다.
        hit = sched_at(p_rows, mo, tol)
        return float(hit) if hit is not None else _formula_put(mo)

    def call_at_month(mo):
        hit = sched_at(k_rows, mo, tol)
        return float(hit) if hit is not None else _formula_call(mo)

    # 행사일 노드면 그 계약일의 금액, 아니면(참고로 묻는 자리) 노드 개월의 산식.
    put = lambda i: (float(p_steps[i][1]) if i in p_steps else
                     _formula_put(p_dates.get(i, cmonth(i))))
    call = lambda i: (float(k_steps[i][1]) if i in k_steps else
                      _formula_call(k_dates.get(i, cmonth(i))))

    # 만기상환금액도 계약이 확정 숫자를 주면 그것을 쓴다 (제9회 106.4302%).
    _ma = float(getattr(tm, "mat_amt", -1.0))
    red = (_ma if _ma > 0 else mat_red_formula(tm))
    # 행사 가능 시점도 위 목록이 정한다. 의무보유 등으로 시작이 늦춰지면 부르는 쪽이
    # 스텝으로 한 번 더 거른다.
    p_on = lambda i: i in p_dates
    k_on = lambda i: i in k_dates
    # 배당가능이익 상환 제약 — 평가에 쓰는 상환청구 가치(put_val)와 발행자 상환권의 행사 가능 여부.
    # put 은 계약 금액 그대로 둔다(분리 판단·행사금액표·상각표의 기대만기는 계약 금액을 본다).
    put_val, DP, red_val = put, None, red
    if dp_active(tm):
        DP = DPPlan(tm, n, dt_)
        _dpv = {i: DP.schedule(i, put(i))["pv"] for i in p_dates}
        put_val = lambda i: _dpv[i] if i in _dpv else put(i)
        # 존속기간 만료 시 상환도 이익으로 한다 — 만기 노드에서 청구한 것과 같은 일정의 현재가치.
        red_val = DP.schedule(n, red)["pv"]
        if issuer_redeem(tm):
            # 행사일에 따로 주는 배당(가산분)은 그 해 우선배당으로 재원에서 이미 뺐다 — 상환원금만 비교한다
            _kok = {i for i in k_dates if DP.call_ok(i, call(i), float(tm.k_w))}
            k_on = lambda i: i in _kok
    return dict(cmonth=cmonth, cyear=cyear, put=put, call=call, put_val=put_val, dp=DP, red_val=red_val,
                put_at_month=put_at_month, call_at_month=call_at_month,
                p_rows=p_rows, k_rows=k_rows, tol=tol, red=red,
                p_on=p_on, k_on=k_on, p_steps=p_steps, k_steps=k_steps,
                p_dates=p_dates, k_dates=k_dates, p_drop=p_drop, k_drop=k_drop,
                p_cont=p_cont, k_cont=k_cont)


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
        return dict(inst="주주간계약", short="SHA", face="주당 기준가격",
                    cpn="—", ipay="—", put="풋옵션 (주식 보유자)", call="콜옵션 (상대방)",
                    host="—", liab="—", red="—", ytm="풋 보장수익률",
                    bond="주주간계약", callamt="콜 행사금액",
                    unit="주당 기준가격 100 기준")
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


def boot_times(par_pts, Tmax, m=1):
    """부트스트래핑할 만기 — 이표 시점(k/m) «과» 입력 곡선의 만기.

    이표 시점만 풀면 이표 주기와 어긋난 입력 만기(반기 이표 국고채의 3M·9M 등)가
    계산에서 빠진다. 입력 만기를 모두 넣어야 입력 곡선이 그대로 재현된다.
    돌려주는 것은 [(t, 종류)] — 종류 'grid' 이표 시점 · 'zero' 첫 이표 전 만기 ·
    'stub' 이표 시점 사이 만기.
    """
    N = max(1, int(math.ceil(Tmax*m)))
    on = lambda t: abs(t*m - round(t*m)) < 1e-9
    ts = {round(k/m, 12): "grid" for k in range(1, N+1)}
    for t, _ in par_pts:
        if 0 < t < N/m and not on(t):
            ts[round(t, 12)] = "zero" if t < 1/m else "stub"
    return sorted(ts.items())


def bootstrap_df(par_pts, Tmax, m=1):
    """만기수익률 곡선 → 할인계수.

    par_pts 는 [(만기, 연 만기수익률)] 이고 m 은 연간 이표 횟수다.
    선형보간으로 만기마다 수익률을 만든 뒤 앞에서부터 순차로 푼다.
      이표 시점 k/m   1 = c·(DF₁ + … + DF_k) + DF_k          c = 그 만기 수익률 ÷ m
      첫 이표 전 만기  DF = (1 + y/m)^(−m·t)                    이표 없이 만기에 한 번
      이표 시점 사이   1 = c·f·DF(t₁) + c·(DF(t₂)+…) + (1+c)·DF(t)
                       이표는 만기에서 1/m 씩 거꾸로 센다. 맨 앞(t₁)은 짧은 첫 이표로
                       기간 비율 f = t₁·m 만큼만 준다. 앞 시점의 DF 는 이미 푼 점들의
                       연속복리 현물을 직선으로 이어 읽는다.
    이표 시점의 DF 는 이표 시점끼리만으로 풀므로, 입력이 모두 이표 시점에 있으면
    종전과 같은 값이다.
    """
    out, acc, known = [(0.0, 1.0)], 0.0, []

    def df_at(tq):
        sp = _lin(known, tq)
        return math.exp(-sp*tq)
    for t, kind in boot_times(par_pts, Tmax, m):
        y = _lin(par_pts, t); c = y/m
        if kind == "grid":
            df = (1 - c*acc)/(1 + c)
            acc += df
        elif kind == "zero":
            df = (1 + c)**(-m*t)
        else:
            cps, tj = [], t - 1/m
            while tj > 1e-9:
                cps.append(tj); tj -= 1/m
            first = cps[-1]                       # 가장 이른 이표 — 짧은 첫 이표
            pv = c*first*m*df_at(first) + c*sum(df_at(x) for x in cps[:-1])
            df = (1 - pv)/(1 + c)
        out.append((t, df))
        known.append((t, -math.log(df)/t))
        known.sort()
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


def rating_blend_kind(tm) -> str:
    """두 등급으로 대상 등급 곡선을 만드는 방식 — 두 등급 사이면 「보간」, 밖이면 「외삽」."""
    ia, ib, it = rating_idx(tm.rt_a), rating_idx(tm.rt_b), rating_idx(tm.rt_tgt)
    if min(ia, ib, it) < 0 or ia == ib:
        return "보간"
    return "보간" if min(ia, ib) <= it <= max(ia, ib) else "외삽"


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
TOL = 1e-9      # 동점 판정 허용오차의 바닥값. 값이 100 근처라 1e-9 은 잡음보다 크고 실질 차이보다 작다
TOL_REL = 1e-12  # 금액이 1,000 을 넘으면 허용오차 = 금액 × 1e-12 (tie_tol)


def tie_tol(x, y):
    """두 금액을 동점으로 볼 허용오차 — 큰 쪽 금액의 1e-12 배, 최소 TOL(1e-9).

    금액이 1,000 이하이면 TOL 그대로다(액면 100 기준 격자의 거의 모든 노드). 주가가 극단적으로
    오른 끝자락 노드처럼 금액이 아주 크면 반올림 오차도 금액에 비례해 커진다. 허용오차가 고정이면
    그런 노드에서는 규칙(전환·콜은 앞설 때만 이기고 풋·보유는 동점이면 이긴다)이 아니라 반올림
    오차가 판정을 정하고, 엔진과 엑셀의 판정 표시가 갈린다. 한쪽이 무한대(열리지 않은 권리)이면
    어떤 유한한 허용오차로도 결과가 같으므로 TOL 을 쓴다. 엑셀은 같은 값을 xl_tol 로 쓴다.
    """
    m = abs(x); k = abs(y)
    if k > m: m = k
    if m == math.inf: return TOL
    t = TOL_REL * m
    return t if t > TOL else TOL


def engine(tm: Terms, conv=True, put=True, call=False, conv_start=None,
           put_start=None, lock_m=None):
    RF, CR = curves(tm)
    n, T = int(tm.n), tm.T
    dt_ = T/n
    mper = n/(T*12)
    el = tm.elapsed_m                       # 발행일 → 평가기준일 경과 개월
    # 계약상 개월 → 노드 번호. 시작은 계약일 이후 첫 노드, 종료는 이전 마지막 노드
    # (둘 다 허용 일수만큼 너그럽다 — EXDATE_RULE).
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
    # 의무보유는 전환뿐 아니라 조기상환청구도 막는다. 부르는 쪽이 시작을 미뤄 준다.
    ps = tm.p_s if put_start is None else put_start
    # 의무보유가 걸린 마지막 노드 (lock_end_step). 그 노드까지는 전환이 막히고, 조기상환청구도
    # 막는 계약(k_lock_put)이면 조기상환도 막힌다. 시작 개월(cs·ps)만으로는 종료일과 같은 날의
    # 마지막 매도청구 노드를 묶지 못한다.
    _LK = -1 if lock_m is None else lock_end_step(tm, n, dt_, lock_m)
    _lkput = int(getattr(tm, "k_lock_put", 1)) == 1

    # 조정일은 계약일마다 그날 이후 첫 노드다 (refix_steps — 조서와 같은 목록).
    _RFX = refix_steps(tm, n, dt_)
    is_rfx = lambda i: i in _RFX
    # 지급일은 계약일 목록에서 온다 (pay_steps) — 격자 간격과 무관하게 횟수가 같다.
    _pays = pay_steps(tm, n, dt_)
    cpn_amt = 100*eff_cpn(tm)*tm.ipay/12
    cpn_at = lambda i: cpn_amt*_pays.get(i, 0)
    # 행사금액은 발행일부터 붙는다. 산식은 exercise_amounts 하나에서 나온다.
    EA = exercise_amounts(tm, n, dt_)
    red = EA["red_val"]          # 배당가능이익 제약이 있으면 실제 지급 일정의 현재가치
    S = lambda i, j: tm.S0 * u**j * d**(i-j)
    kcap = k_cap(tm)
    clip = lambda s: min(max(s, tm.floor, tm.par), kcap)
    rnd = lambda s: k_round(tm, s)
    put_amt = EA["put_val"]          # 배당가능이익 제약이 있으면 실제 지급 일정의 현재가치
    # 계약서의 회차별 표를 넣었으면 그 회차가 곧 행사일이다. 의무보유(ps)는 그때도
    # 앞쪽 회차를 막는다 — 표에 있는 날이라도 묶여 있으면 청구할 수 없다.
    # 의무보유는 «스텝» 으로 견준다. 개월에 반 노드 허용오차를 두면 조서(스텝 비교)와
    # 경계에서 갈려, 의무보유가 걸린 트랜치에서 매도청구권 값이 어긋난다.
    _pin = lambda i: EA["p_on"](i) and i >= st_lo(ps) and not (_lkput and i <= _LK)
    _kin = EA["k_on"]
    put_a = lambda i: put_amt(i) if (put and _pin(i)) else 0.0
    # kstrike 는 콜 스위치와 무관한 행사금액이다. 행사기간이 아니면 None.
    # call_a 는 call=False 면 항상 inf 라 제3자 콜옵션 평가에 쓸 수 없다.
    kstrike = lambda i: (EA["call"](i) if _kin(i) else None)
    call_a = lambda i: (kstrike(i) if (call and _kin(i)) else math.inf)
    # 콜을 행사당할 때 투자자가 실제로 받는 금액 — 매도청구금액 + (별도 지급 계약이면) 그날 이자.
    # 격자 안의 매도청구(kv += c)와 수식 조서 8행(매도청구금액 + 이자)이 이미 이 금액을 쓴다.
    # 만기 노드는 더하지 않는다 — 만기에는 마지막 이자를 «쿠폰» 으로 따로 받기 때문이다.
    _kadd = int(getattr(tm, "k_cpn_add", 0)) == 1
    kcash = lambda i: (None if kstrike(i) is None else
                       kstrike(i) + (cpn_at(i) if (_kadd and i < n) else 0.0))
    conv_ok = lambda i: conv and st_lo(cs) <= i <= st_hi(tm.cv_e) and i > _LK
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
    ipo_k = rnd(tm.ipo_px*tm.ipo_mult)
    ipo_hit = lambda i, j: (i == ipo_i and 0 < i <= n and S(i, j) > tm.ipo_min)
    ipo_adj = lambda i, j, k: (clip(min(k, ipo_k)) if ipo_hit(i, j) else k)

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
                # 원 단위 처리는 주가로 새로 정한 가격에만 건다 — 이월값(경로 가중 평균)은 그대로 둔다.
                kv = clip(rnd(S(i, j)) if tm.rfx_mode == 2 else min(prev, rnd(S(i, j))))
            else:
                kv = prev
            # 상장하면 공모가 × 배수로 자른다. 낮아질 때만 조정된다.
            row.append(ipo_adj(i, j, kv))
        Kg.append(row)

    memo = {}
    def rec(i, j, K):
        key = (i, j)
        if key in memo: return memo[key]
        if i == n:
            KK = Kg[n][j]
            if bwc:
                # 사채는 만기상환(또는 그날 열려 있는 조기상환)으로 끝나고,
                # 신주인수권은 내가격이면 행사한다. 둘은 서로를 막지 않는다.
                cv = 100*S(n, j)/KK if conv_ok(n) else 0.0
                wv = max(cv - 100, 0.0) if conv_ok(n) else 0.0
                cm = cpn_at(n)
                pv = put_a(n)
                cash = max(pv, red) + cm
                o = dict(E=wv, B=cash, V=wv+cash, P=0.0, wx=1.0 if wv > 0 else 0.0,
                         kind=("put" if pv > red + tie_tol(pv, red) else "mat"),
                         hold=red, cv=cv, K=KK, pv=pv)
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
                # 동점 처리는 아래 일반 만기 노드와 같다 — 주식은 허용오차만큼 앞설 때만.
                pv = put_a(n)
                cash = (pv + cpn_at(n)) if pv > 0 else 0.0
                # 상환청구가 열려 있지 않으면 고를 것이 없다 — 주가가 0 에 가까워도 주식이 된다.
                # (종전에는 전환가치가 허용오차보다 작은 자리를 «상환청구» 로 적어, 상환청구권이
                # 없는 계약에서 행사 불가능한 자리의 결정으로 잡혔다.)
                if pv <= 0 or cv >= cash + tie_tol(cv, cash):
                    o = dict(E=cv, B=0.0, V=cv, P=1.0, kind="auto", hold=cv, cv=cv, K=KK)
                else:
                    o = dict(E=0.0, B=cash, V=cash, P=0.0, kind="put", hold=cv, cv=cv, K=KK, pv=pv)
                memo[key] = o
                return o
            cv = 100*S(n, j)/KK if conv_ok(n) else 0.0
            pv = put_a(n)
            # 만기에도 이자 지급일이면 이자를 함께 받는다 (책 5-7 만기 현금흐름).
            # 전환을 택하면 주식을 받으므로 중간 노드와 같이 이자는 사라진다.
            cm = cpn_at(n)
            cash = max(pv, red) + cm
            # 리픽싱이 주가로 재설정되는 날에는 전환가치가 정확히 100 이 되어
            # 상환금액과 동점이 된다. 부동소수 잡음으로 갈리지 않게 전환은
            # 허용오차(tie_tol)만큼 앞설 때만 이긴다. 동점이면 현금(부채)이다.
            _c0 = max(cash, 0.0)
            if cv >= _c0 + tie_tol(cv, _c0) and cv > 0:
                o = dict(E=cv, B=0.0, V=cv, P=1.0, kind="conv", hold=red, cv=cv, K=KK)
            else:
                o = dict(E=0.0, B=cash, V=cash, P=0.0, kind="mat",
                         hold=red, cv=cv, K=KK)
        elif conv and int(tm.ipo_conv) and ipo_hit(i, j):
            # 상장하면 보통주가 된다. 그 자리에서 주식으로 끝난다 — 상환청구권도
            # 발행자 상환권도 함께 사라진다. 받는 것은 주식이라 그날 배당은 없다.
            KK = Kg[i][j]
            cv = 100*S(i, j)/KK
            o = dict(E=cv, B=0.0, V=cv, P=1.0, kind="ipo", hold=cv, cv=cv, K=KK)
            memo[key] = o
            return o
        else:
            KU = Kg[i+1][j+1]
            KD = Kg[i+1][j]
            ku = (i+1, j+1)
            kd = (i+1, j)
            a, b = rec(i+1, j+1, KU), rec(i+1, j, KD)
            q = qi(i); c = cpn_at(i)
            fr, fc = fwd(RF, i), fwd(CR, i)
            E = (q*a["E"] + (1-q)*b["E"]) * math.exp(-fr*dt_)
            B = (q*a["B"] + (1-q)*b["B"]) * math.exp(-fc*dt_) + c
            # GS — 자식 노드의 전환확률로 각각 할인한다 (순환참조가 생기지 않는다)
            ya = a["P"]*fr + (1-a["P"])*fc
            yb = b["P"]*fr + (1-b["P"])*fc
            Vc = q*a["V"]*math.exp(-ya*dt_) + (1-q)*b["V"]*math.exp(-yb*dt_) + c
            pr = q*a["P"] + (1-q)*b["P"]
            KK = Kg[i][j]
            cv = 100*S(i, j)/KK if conv_ok(i) else 0.0
            pv, kv = put_a(i), call_a(i)
            # 행사일이 이자지급일과 겹칠 때 그날 이자를 «따로» 받는 계약이 있다. 계약이
            # 정하는 것이라 스위치로 받는다 (기본은 받지 않는다 — 행사금액에 이미
            # 들어 있다고 본다). 만기 노드는 여기 오지 않는다 — 만기에는 상환이든
            # 조기상환이든 마지막 이자를 받는 것이 관행이라 위에서 늘 더한다.
            if c > 0:
                if int(getattr(tm, "p_cpn_add", 0)) and pv > 0: pv += c
                if int(getattr(tm, "k_cpn_add", 0)) and kv < math.inf: kv += c
            if bwc:
                # 신주인수권은 행사해도 사채가 남으므로 사채 결정과 별개다.
                # 미국형이라 「지금 행사」와 「계속 보유」 중 큰 쪽을 고른다.
                wv = max(cv - 100, 0.0) if conv_ok(i) else 0.0
                En = max(E, wv)
                _wx = 1.0 if (wv > 0 and wv >= E - tie_tol(wv, E)) else 0.0
                # forced 는 «매도청구를 당해 전환으로 대응했는가» 다. 신주인수권부
                # 사채의 사채 결정에는 전환이 들어가지 않아 늘 거짓이다.
                ex = dict(hold=E+B, cv=cv, K=KK, pv=pv, kv=kv, Vc=E+B,
                          up=ku, dn=kd, wv=wv, forced=False)
                _kf = int(tm.pc_order) == 1
                if bwd:
                    # 분리형 — 신주인수권증권이 따로 유통되므로 사채를 상환받아도
                    # 남는다. 두 결정이 서로를 건드리지 않으므로 **사채만** 넘긴다.
                    # 전환은 이 결정에 들어가지 않아 -inf 다.
                    # 그 자리에 열리지 않은 조기상환(금액 0)은 후보가 아니다 — 매도청구의 무한대와 같다.
                    _kd = node_decide(-math.inf, pv if pv > 0 else -math.inf, kv, B, _kf)
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
                    holdT, putT, callT = B + En, (pv + wv) if pv > 0 else -math.inf, kv + wv
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
            _cresp = conv_resp(tm)
            # 네 조합 모두 «콜을 당하지 않았을 때의 최선» 과 «콜을 당했을 때 받는 것» 가운데 작은 쪽이다.
            # 콜을 당하면 콜금액, 또는 먼저 설 수 있는 권리(전환 대응 가능이면 전환, 풋 우선이면 풋).
            Vg = min(max(cv, pv, Vc), max(kv, cv if _cresp else -math.inf,
                                          pv if not _kfirst else -math.inf))
            # GS 의 전환확률은 GS 자신의 판단을 따른다. TF 와 다른 갈래를 고를 수 있다.
            # GS 도 같은 순서다. 현금이 동점이면 전환확률 0 이다. 열리지 않은 상환청구(금액 0)는 현금 갈래가
            # 아니다 — 아래 결정과 같은 읽기(_pvd)다. 0 과 견주면 값이 0 에 가까운 자리의 전환확률이 0 이 된다.
            _pvd = pv if pv > 0 else -math.inf
            if (abs(Vg - _pvd) < tie_tol(Vg, _pvd)
                    or (kv < math.inf and abs(Vg - kv) < tie_tol(Vg, kv))):   Pg = 0.0
            elif cv > 0 and abs(Vg - cv) < tie_tol(Vg, cv):                  Pg = 1.0
            else:                                                            Pg = pr
            # up·dn 은 자식 노드 키다. 만기 노드에는 없어 자식 없음의 표시가 된다.
            ex = dict(hold=hold, cv=cv, K=KK, pv=pv, kv=kv, Vc=Vc, up=ku, dn=kd,
                      forced=False)
            # 동점 처리는 위 만기 노드와 같다. 전환은 허용오차만큼 앞설 때만 이긴다.
            # 평가기준일(i=0)도 예외가 아니다. 그날 행사할 수 있고 행사가 유리하면
            # 공정가치는 행사가치 이상이어야 한다 — 계속보유로 눌러 두면 값이
            # 과소계상되고, 같은 판단을 하는 GS·수식 조서와도 어긋난다. 그날
            # 행사할 수 없는 권리는 conv_ok·put_a·call_a 가 이미 막는다.
            # 그 자리에 열리지 않은 상환청구(금액 0)는 결정 후보가 아니다 — 매도청구가 열리지 않으면
            # 무한대로 넘기는 것과 같은 읽기다. 0 으로 넘기면 «풋은 동점이면 이긴다» 규칙 때문에 주가가
            # 0 에 가까운 자리(전환가치·보유가치가 허용오차 안)에서 상환청구가 골라져, 상환청구권이 없는
            # 계약에 «행사 불가능한 자리의 결정» 이 생긴다. 가치는 그 자리 값이 1e-9 수준이라 거의 같다.
            _kd = node_decide(cv, _pvd, kv, hold, _kfirst, _cresp)
            if   _kd == "conv": _e, _b = cv, 0.0
            elif _kd == "put":  _e, _b = 0.0, pv
            elif _kd == "call": _e, _b = 0.0, kv
            else:               _e, _b = E, B
            # 매도청구를 당해 전환으로 대응한 자리인지 함께 적어 둔다. 가치는
            # 자발적 전환과 같지만 정산 분포에서는 갈라 세야 한다.
            ex["forced"] = (_kd == "conv"
                            and node_decide(cv, _pvd, math.inf, hold, _kfirst, _cresp) != "conv")
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
    layer = {(0, 0): (1.0, tm.K0, 0)}
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
            KU = Kg[i+1][j+1]
            KD = Kg[i+1][j]
            for kk, pp, jj, KK in (
                ((i+1, j+1), p_*q, j+1, KU),
                ((i+1, j), p_*(1-q), j, KD)):
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
        lay = {(0, 0): (1.0, tm.K0, 0)}
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
                KU = Kg[i+1][j+1]
                KD = Kg[i+1][j]
                for kk, pp, jj, KK in (
                    ((i+1, j+1), p_*q, j+1, KU),
                    ((i+1, j), p_*(1-q), j, KD)):
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

    root = (0, 0)
    return dict(TF=r0["E"]+r0["B"], E=r0["E"], B=r0["B"], GS=r0["V"], P=r0["P"],
                q=qi(0), qs=qs, qbad=qbad, qmin=min(qs), qmax=max(qs),
                u=u, d=d, dt=dt_, mper=mper, n=n, memo=memo, Kg=Kg,
                exact=False, S=S, host=100*math.exp(-CR(T)*T), dist=dist,
                root=root, qi=qi, fwdRF=lambda i: fwd(RF, i),
                fwdCR=lambda i: fwd(CR, i), kstrike=kstrike, kcash=kcash,
                st_lo=st_lo, st_hi=st_hi)


def pick(res, model): return res["GS"] if model == "GS" else res["TF"]


# ══════════════════════════════════════════════════════════
# 3-B. 주주간계약 — 지분에 붙은 풋과 콜
# ══════════════════════════════════════════════════════════
def sha_integrity(tm: Terms, R: dict) -> list:
    if R.get("deal"):
        return []           # 확정 거래 — 격자가 없다
    return _sha_integrity(tm, R)


def _sha_integrity(tm: Terms, R: dict) -> list:
    """주주간계약 평가가 고장 나지 않았는지 — 앱이 평가할 때 보고, 걸리면 조서를 만들지 않는다.

    위험중립가중치 q 가 0 과 1 사이인가, 풋이 지금 행사했을 때의 값보다 작지 않은가,
    풋·콜이 음수가 아닌가.
    """
    bad = []
    if R.get("qbad") or not (0 < R.get("q", 0.5) < 1):
        bad.append("위험중립가중치 q 가 0 과 1 사이를 벗어남")
    imm = max(R["pk"](0) - 100*tm.S0/tm.K0, 0.0) if R["p_on"](0) else 0.0
    # 같은 주식 물량(연계)의 풋은 상대가 먼저 콜을 행사하면 즉시행사값보다 작을 수 있다 — 따로 잰 물량만 본다.
    for b in R.get("blocks") or [dict(kind="ind", R=R)]:
        if b["kind"] in ("ind", "put") and b["R"]["put"] < imm - 1e-6:
            bad.append("풋 가치가 지금 행사했을 때의 값보다 작음"); break
    if min(R["put"], R["call"]) < -1e-6 and int(getattr(tm, "sha_writer", 0)) == 0:
        bad.append("풋·콜 가치가 음수")
    return bad


def sha_link_split(tm: Terms):
    """(같은 주식에 붙은 물량, 풋만 있는 물량, 콜만 있는 물량).

    상호소멸(sha_kill 1)이 아니면 겹치는 물량은 없다 — 두 권리를 따로 잰다. 상호소멸이면 같은 주식에
    붙은 물량을 입력받는다(sha_link_q). 넣지 않았으면 풋·콜 수량이 같을 때만 그 수량 전부로 보고,
    다르면 계산을 막는다 — 수량만 보고 자동으로 잇지 않는다.
    """
    qp, qc = sha_qty(tm)
    # 행사기간이 없는 권리는 수량이 적혀 있어도 없는 권리다 — 연계할 상대가 없다.
    _has_put = tm.sha_put_s <= tm.sha_put_e
    _has_call = tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
    if not int(getattr(tm, "sha_kill", 0)) or qp <= 0 or qc <= 0 or not (_has_put and _has_call):
        return 0.0, qp, qc
    lq = float(getattr(tm, "sha_link_q", -1.0))
    if lq < 0:
        if abs(qp - qc) > 1e-9:
            raise ValueError(f"풋 {qp:,.0f}주 · 콜 {qc:,.0f}주로 수량이 다릅니다 — 한쪽 행사로 함께 끝나는 "
                             "«같은 주식에 붙은 물량» 을 입력하십시오. 나머지 물량은 엔진이 따로 잽니다.")
        lq = qp
    if lq > min(qp, qc) + 1e-9:
        raise ValueError(f"같은 주식에 붙은 물량 {lq:,.0f}주가 풋 {qp:,.0f}주 · 콜 {qc:,.0f}주 가운데 "
                         "작은 쪽보다 큽니다.")
    return lq, qp - lq, qc - lq


def sha_engine(tm: Terms):
    """주주간계약 평가 — 계약조건대로 물량을 나눠 잰다 (평가방법을 고르는 단계는 없다).

    상호소멸 계약이면 같은 주식에 붙은 물량은 한 격자에서 두 당사자의 행사 판단을 함께 풀고
    (_sha_lattice 의 settle), 풋만·콜만 있는 물량은 상대 권리 없이 따로 잰다. 상호소멸이 아니면
    두 권리를 따로 잰다. 돌려주는 ``put`` · ``call`` 은 **풋 1주 · 콜 1주당 100 기준** 값이다 —
    물량 가중 평균이라 «100 기준 × 기준가격 ÷ 100 × 주식수» 가 그대로 원 단위 총액이다. 물량별 격자와
    값은 ``blocks`` 에 있다 (조서가 쓴다). 트리(P · C · KIND)는 같은 주식 물량의 격자(없으면 따로 잰 격자)다.
    """
    lq, qpo, qco = sha_link_split(tm)
    qp, qc = sha_qty(tm)
    kill = int(getattr(tm, "sha_kill", 0)) == 1 and lq > 0
    if kill and int(tm.sha_writer) == 2:
        # 지원하지 않는 조건 — 단순화한 값을 정상 결과처럼 내지 않는다 (화면·입력 검사와 같은 문장)
        raise ValueError(" / ".join(sha_link_issues(1, qp, qc, lq, 2)))
    if kill:
        RL = _sha_lattice(tm)
        if qpo > 1e-9 or qco > 1e-9:
            t0 = Terms(**asdict(tm)); t0.sha_kill = 0
            RI = _sha_lattice(t0)
        else:
            RI = None
        R = dict(RL)
        blocks = [dict(kind="link", name="같은 주식 (풋·콜 연계)", R=RL, qp=lq, qc=lq)]
        if qpo > 1e-9: blocks.append(dict(kind="put", name="풋만 있는 물량", R=RI, qp=qpo, qc=0.0))
        if qco > 1e-9: blocks.append(dict(kind="call", name="콜만 있는 물량", R=RI, qp=0.0, qc=qco))
    else:
        t0 = tm
        if int(getattr(tm, "sha_kill", 0)) == 1:
            t0 = Terms(**asdict(tm)); t0.sha_kill = 0      # 한쪽 권리뿐이라 연계할 상대가 없다
        R = _sha_lattice(t0)
        blocks = [dict(kind="ind", name="풋·콜 따로", R=R, qp=qp, qc=qc)]
    # 풋 1주 · 콜 1주당 100 기준 (물량 가중)
    R["put"] = (sum(b["R"]["put"]*b["qp"] for b in blocks)/qp) if qp > 0 else blocks[0]["R"]["put"]
    R["call"] = (sum(b["R"]["call"]*b["qc"] for b in blocks)/qc) if qc > 0 else blocks[0]["R"]["call"]
    def _mix(key, qkey, tot):
        out = {}
        for b in blocks:
            if b[qkey] <= 0: continue
            for k, v in b["R"][key].items():
                out[k] = out.get(k, 0.0) + v*b[qkey]/tot
        return out or dict(blocks[0]["R"][key])
    if kill and len(blocks) > 1:
        R["dist_put"] = _mix("dist_put", "qp", qp)
        R["dist_call"] = _mix("dist_call", "qc", qc)
    R.update(blocks=blocks, link_q=lq, put_only_q=qpo, call_only_q=qco, linked=kill)
    return R


def _sha_lattice(tm: Terms):
    """주주간계약을 지분가치 격자에서 잰다 — 한 물량(같은 계약조건)의 격자.

    사채가 없다. 주식 보유자가 **이미 가진 지분**에 풋(보유자가 상대방에게 사 달라고
    요구할 권리)과 콜(상대방이 팔라고 요구할 권리)이 붙어 있을 뿐이다. 그래서
    「B0 → B1 → B2」 같은 순차 차감이 아니라 두 옵션을 **따로** 잰다.

    보유자가 서로 다르기 때문이다 — 풋은 주식 보유자가, 콜은 상대방이 고른다.
    한 격자에서 함께 최적화하면 두 사람이 한 사람인 것처럼 재게 된다. 실무에서
    풋과 콜을 각각 평가해 각자의 재무제표에 총액으로 싣는 것도 같은 이유다.

    금액 기준은 **주당 기준가격 100** 이다 (원 단위 = 결과 × 기준가격 ÷ 100 × 주식수).

        지분가치(t) = 100 × 주가(t) ÷ 주당 인수가액
        풋 행사가치 = MAX(풋 행사금액 − 지분가치, 0)
        콜 행사가치 = MAX(지분가치 − 콜 행사금액, 0)

    풋 행사금액은 기준가격에 가산율을 붙인 값이라 사채의 조기상환금액과
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

    # 행사일은 전환사채의 조기상환·매도청구와 «같은 목록» 규칙으로 정한다(exercise_dates) —
    # 정기 행사는 계약일마다 그날 이후 첫 노드, 기간 중 언제든지는 기간 안의 모든 노드.
    # 한때 「주기(개월) × 월당 노드 수」 를 반올림한 칸 간격으로 열어, 주 격자에서 매월 행사가
    # 4노드마다(= 28일마다) 열리며 계약일에서 조금씩 밀렸다.
    _has_put = tm.sha_put_s <= tm.sha_put_e
    # 종료가 0 이하면 콜이 없는 계약이다. 한 시점만 열리는 계약(시작 = 종료)은 그대로 살린다.
    _has_call = tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
    p_dates, p_drop, p_cont = (exercise_dates(tm, n, dt_, tm.sha_put_s, tm.sha_put_e, tm.sha_put_f)
                               if _has_put else ({}, [], False))
    c_dates, c_drop, c_cont = (exercise_dates(tm, n, dt_, tm.sha_call_s, tm.sha_call_e, tm.sha_call_f)
                               if _has_call else ({}, [], False))
    rem_m = float(getattr(tm, "rem_m", 0.0) or tm.T*12)
    cmonth = lambda i: tm.elapsed_m + i*rem_m/max(1, n)

    S = lambda i, j: tm.S0 * u**j * d**(i-j)
    eq = lambda i, j: 100*S(i, j)/tm.K0                      # 지분가치
    # 행사금액은 노드 날짜가 아니라 **계약 행사월** 의 경과기간으로 붙인다 — 경과기간의 잣대는
    # 가정 「행사금액 경과기간」(acc_basis) 이고 전환사채와 같은 함수(yr_of_month)다.
    kc = sha_call_ratio(tm)                                  # 콜 기준가격 ÷ 풋 기준가격
    pmo = lambda i: p_dates.get(i, cmonth(i))
    cmo = lambda i: c_dates.get(i, cmonth(i))
    # 가격 가산 경과기간 — 계약 개월 ÷ 12 (acc_basis 1) 또는 가산 기산일부터 행사일까지 실제 일수 ÷ 365
    # (0). 행사일은 정기 행사면 계약일, 기간 중 언제든지면 그 노드의 날짜다.
    _di = dt.date.fromisoformat(tm.d_issue)
    _nd = node_dates(tm, n, dt_)
    _acc = int(getattr(tm, "acc_basis", 1))
    pday = lambda i: (months_to_date(_di, p_dates[i]) if (i in p_dates and not p_cont) else _nd[i])
    cday = lambda i: (months_to_date(_di, c_dates[i]) if (i in c_dates and not c_cont) else _nd[i])
    pyr = lambda i: (pmo(i)/12 if _acc else (pday(i) - _di).days/365)
    cyr = lambda i: (cmo(i)/12 if _acc else (cday(i) - _di).days/365)
    pk = lambda i: 100*(1 + accrue_rate(pyr(i), tm.sha_put_yield, 0.0, tm.sha_put_cmp))
    ck = lambda i: 100*kc*(1 + accrue_rate(cyr(i), tm.sha_call_prem, 0.0, tm.sha_call_cmp))
    p_on = lambda i: i in p_dates
    c_on = lambda i: i in c_dates
    # 적격상장 — 그 스텝의 주가가 최소 기준을 넘으면 성공이다.
    qi_step = st_lo(tm.ipo_m) if (int(tm.ipo_on) and tm.ipo_m > 0) else -1
    # 상장 종료 — 실제 상장(사건)이면 그 노드에서 주가와 무관하게, 주가 기준이면 그 노드 주가가 기준을 넘을 때만
    _ipo_event = int(getattr(tm, "sha_ipo_kind", -1)) == 1
    qipo = lambda i, j: (i == qi_step and 0 < i <= n and (_ipo_event or S(i, j) > tm.ipo_min))

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
    # 콜 권리자가 풋 의무자 본인인가 (sha_writer 0). 그러면 같은 주식을 두 당사자가 서로 사고팔
    # 권리를 가진 «하나로 묶인 계약» 이다 — 콜을 행사하면 자기가 지던 풋 의무도 함께 끝난다.
    _same = int(tm.sha_writer) == 0
    KIND = [["hold"]*(n+1) for _ in range(n+1)]
    # 적격상장으로 덮어쓰기 전의 행사 판단 — 조서 «행사 판단» 시트가 그대로 싣는다.
    DEC = [["hold"]*(n+1) for _ in range(n+1)]
    _T = SHA_SETTLE_TOL

    def settle(i, j, pc, cc):
        """그 노드에서 누가 행사하는가 — 상호소멸(같은 물량의 상대 권리가 함께 끝나는) 계약에서만 부른다.

        각 당사자는 **자기에게 걸린 권리·의무 전체**로 지금 행사와 계속 보유를 견준다.
          풋 권리자(주식 보유자 · 콜 의무자)  행사 → 풋 행사금액 − 지분가치 / 보유 → 풋 − 콜 (계속보유)
          콜 권리자 = 풋 의무자              행사 → 지분가치 − 콜 행사금액 / 보유 → 콜 − 풋 (계속보유)
          콜 권리자 ≠ 풋 의무자 (발행회사가 풋 의무자) — 콜 권리자에게는 콜뿐이다: 보유 → 콜
        행사로 상대 권리·의무가 함께 사라지므로, 자기 옵션만 보면 외가격이어도 행사가 나을 수 있다
        (예: 지금 콜로 −20 에 끝내기 vs 기다렸다 상대 풋에 −27). «행사가치 > 0» 조건은 쓰지 않는다 —
        지금 행사가 이익이거나, 계속 보유가 손해일 때 그보다 나으면 행사한다.

        둘 다 행사하려 하면 계약의 우선권(pc_order)이 가른다. 거래는 행사한 쪽의 가격 X 로 이뤄지고
        표시금액은 그 거래에서 누가 이득을 보는지로 나눈다 — 풋 = MAX(X − 지분가치, 0)(풋 권리자의 몫,
        의무자 신용으로 할인), 콜 = MAX(지분가치 − X, 0)(상대의 몫, 무위험). 순액(풋 − 콜)은 거래 손익
        X − 지분가치 그대로다. 콜 권리자가 다른 당사자면 각자의 상대방별로 — 풋 = X − 지분가치(풋 행사),
        콜 = 지분가치 − X(콜 행사)."""
        e = eq(i, j)
        hp, hc = pc - cc, cc - pc
        _p = _c = False
        if p_on(i):
            v = pk(i) - e
            _p = v >= hp - _T and (v > _T or hp < -_T)
        if c_on(i):
            v = e - ck(i)
            if _same:
                _c = v >= hc - _T and (v > _T or hc < -_T)
            else:
                _c = v > _T and v >= cc - _T
        if _p and _c: _p, _c = _pfirst, not _pfirst
        if _p:
            x = pk(i)
            return ((max(x - e, 0.0), max(e - x, 0.0), "put") if _same else (x - e, 0.0, "put"))
        if _c:
            x = ck(i)
            return ((max(x - e, 0.0), max(e - x, 0.0), "call") if _same else (0.0, e - x, "call"))
        return pc, cc, "hold"

    for j in range(n+1):
        pe = max(pk(n) - eq(n, j), 0.0) if p_on(n) else 0.0
        ce = max(eq(n, j) - ck(n), 0.0) if c_on(n) else 0.0
        if _kill:
            P[n][j], C[n][j], KIND[n][j] = settle(n, j, 0.0, 0.0)
            DEC[n][j] = KIND[n][j]
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
                P[i][j], C[i][j], KIND[i][j] = settle(i, j, pc, cc)
                DEC[i][j] = KIND[i][j]
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
    return dict(put=P[0][0], call=C[0][0], P=P, C=C, S=S, eq=eq, KIND=KIND, DEC=DEC,
                kill=_kill, same=_same,
                pk=pk, ck=ck, p_on=p_on, c_on=c_on, qipo=qipo,
                qi_step=qi_step, n=n, dt=dt_, mper=mper, u=u, d=d,
                q=qi(0), qs=qs, qbad=qbad, qmin=min(qs), qmax=max(qs),
                qi=qi, rf=rf, pdisc=pdisc, gross=gross,
                dist_put=pw, dist_call=cw,
                p_dates=p_dates, c_dates=c_dates, p_drop=p_drop, c_drop=c_drop,
                p_cont=p_cont, c_cont=c_cont, pmo=pmo, cmo=cmo, cmonth=cmonth, kc=kc,
                pday=pday, cday=cday, pyr=pyr, cyr=cyr,
                has_put=_has_put, has_call=_has_call)


# 회차별 표 한 줄의 모양. 필수 — name · start · end · style · price · put_q · call_q.
# 나머지는 비우면(None · "") 공통 설정이나 풋 쪽 값을 쓴다.
# 통지·결제 시차 — 구현하지 않는다. 조서 가정 시트에 이 한 줄을 싣는다.
SHA_NO_LAG = ("통지일부터 주식 이전 및 대금 지급일까지의 시차는 반영하지 않고, 행사일에 거래가 완료되는 것으로 "
              "가정하였다.")

SHA_ROW_KEYS = ("name", "start", "end", "style", "freq", "price", "rate", "acc_from",
                "put_q", "call_q", "kill", "call_start", "call_end", "call_price", "call_rate",
                "sig", "rf", "pdisc", "price_note", "link_q",
                # 평가 대상의 상태 — open 미행사 / agreed 행사·매매 확정·미결제 / settled 결제 완료 / cond 추가 조건부
                "status", "side", "deal_px", "settle", "cond_basis", "cond_note",
                # 같은 보유주식·공통 한도를 쓰는 회차 묶음과 그 묶음의 한도(주)
                "pool", "pool_cap",
                # 실적 연동 행사가격의 산식 입력 (dict) — 있으면 주당 기준가격을 이 산식으로 계산한다
                "perf")
SHA_PERF_KEYS = ("rev", "ded", "op", "thr", "hi", "lo", "sh", "fy", "ey", "kind")
SHA_ROW_STATUS = {"open": "미행사", "agreed": "행사·매매 확정 (미결제)", "settled": "결제 완료",
                  "cond": "추가 조건부"}
SHA_COND_BASIS = {"met": "조건 충족 가정 (평가에 반영)", "unmet": "조건 미충족 가정 (평가에서 제외)"}
SHA_ROW_STYLES = {"any": "기간 중 언제든지", "periodic": "정기 (주기마다)", "single": "특정일 1회"}
SHA_ROW_MAX_N = 1200          # 회차 격자 한도 — service._prepare 의 계산 한도와 같다


def sha_row_issue_text(name, message: str) -> str:
    """회차 검사 문장 — 회차 이름으로 시작하지 않으면 앞에 붙인다."""
    name = str(name)
    return message if message.startswith(name) else f"{name}: {message}"


def sha_row_defaults(row: dict) -> dict:
    """비운 칸을 채운 회차 줄. 저장 파일의 모양은 바꾸지 않는다(읽을 때만 채운다)."""
    r = {k: None for k in SHA_ROW_KEYS}
    r.update({k: v for k, v in (row or {}).items() if k in SHA_ROW_KEYS})
    r["name"] = str(r["name"] or "").strip() or "회차"
    # 비운 칸만 기본값(언제든지)이다. 알 수 없는 값은 그대로 두어 sha_row_issues 가 막는다 —
    # 오타를 «언제든지» 로 읽으면 정기·1회 권리가 모든 노드에서 열려 값이 부풀려진다.
    r["style"] = r["style"] or "any"
    for k in ("freq", "price", "rate", "put_q", "call_q", "call_price", "call_rate", "sig", "rf", "pdisc", "link_q",
              "deal_px", "pool_cap"):
        v = r[k]
        r[k] = None if v in (None, "") else float(v)
    r["rate"] = r["rate"] or 0.0
    r["put_q"] = r["put_q"] or 0.0
    r["call_q"] = r["call_q"] or 0.0
    r["kill"] = int(bool(r["kill"]))
    for k in ("start", "end", "acc_from", "call_start", "call_end", "price_note", "side", "settle", "cond_basis",
              "cond_note", "pool"):
        r[k] = str(r[k] or "").strip()
    r["status"] = str(r["status"] or "open").strip() or "open"
    r["perf"] = r["perf"] if isinstance(r["perf"], dict) and r["perf"] else None
    return r


def sha_perf_calc(pf: dict):
    """실적 연동 산식 입력 → (주당 행사가격, 적용 배수, 영업손실률). 계약에 없는 하한은 두지 않는다.

    영업손실률 = MAX(0, −영업손익 ÷ 매출액) — 영업손익을 부호 그대로 받는다(손실은 음수). 손실률이 기준을
    **초과**해야 «초과 시 배수» 다 (정확히 같으면 «이하 시 배수»). 비교는 소수 12자리에서 반올림해 부동소수
    잡음으로 경계가 뒤집히지 않게 한다.
    """
    rev, ded, op = float(pf["rev"]), float(pf.get("ded") or 0.0), float(pf["op"])
    thr, hi, lo, sh = float(pf["thr"]), float(pf["hi"]), float(pf["lo"]), float(pf["sh"])
    if not rev > 0:
        raise ValueError("실적 연동 행사가격 — 매출액을 0 보다 크게 입력하십시오.")
    loss = max(0.0, -op/rev)
    px, mult = sha_perf_price(rev, ded, loss, thr, hi, lo, sh)
    return px, mult, loss


def sha_perf_sensitivity(tm: Terms, name: str, rev_mults=(0.8, 0.9, 1.0, 1.1, 1.2)) -> list:
    """실적 연동 회차 하나의 매출·손실률 민감도 — [(매출 배율, 손실률, 적용 배수, 주당 행사가격, 풋 원, 콜 원)].

    손실률은 입력값 · 기준과 정확히 같음 · 기준 + 0.1%p 세 경우. 확률을 붙이지 않는다 — 결과는 시나리오별
    금액일 뿐 평가금액에 반영되지 않는다. 다른 입력(주가·변동성·금리)은 그대로다.
    """
    raw = next((x for x in tm.sha_rows or [] if sha_row_defaults(x)["name"] == name), None)
    if raw is None or not sha_row_defaults(raw)["perf"]:
        raise ValueError(f"«{name}» 회차에 실적 연동 산식이 없습니다.")
    pf0 = dict(sha_row_defaults(raw)["perf"])
    rev0, thr = float(pf0["rev"]), float(pf0["thr"])
    loss0 = max(0.0, -float(pf0["op"])/rev0)
    out = []
    for m in rev_mults:
        for lab, loss in (("입력값", loss0), ("기준과 같음", thr), ("기준 + 0.1%p", thr + 0.001)):
            pf = dict(pf0, rev=rev0*m, op=-loss*rev0*m)
            t = sha_row_terms(tm, dict(raw, perf=pf))
            R = sha_engine(t)
            qp, qc = sha_qty(t)
            px, mult, _ = sha_perf_calc(pf)
            out.append((m, lab, loss, mult, px, R["put"]/100*t.K0*qp, R["call"]/100*t.K0*qc))
    return out


def sha_perf_price(revenue: float, deduct: float, loss_rate: float, threshold: float,
                   mult_over: float, mult_else: float, shares: float):
    """실적 연동 주당 행사가격 = (매출액 − 차감액) × 적용 배수 ÷ 발행주식 총수 → (가격, 배수).

    영업손실률이 기준을 **초과**하면 ``mult_over``, 아니면(같거나 낮으면) ``mult_else``. 추정 재무수치로
    계산한 값을 회차 표에 **고정해** 쓴다 — 미래 실적의 불확실성은 반영하지 않는다.
    """
    if not shares > 0:
        raise ValueError("발행주식 총수를 0 보다 크게 입력하십시오.")
    mult = mult_over if round(loss_rate, 12) > round(threshold, 12) else mult_else
    return (revenue - deduct)*mult/shares, mult


SHA_IPO_KIND_MSG = ("상장 조항을 넣었으면 종료 조건을 고르십시오 — «실제 상장 완료 시 종료»(상장일·상장 가정일에 주가와 "
                    "무관하게 권리가 끝남) 또는 «그 시점 주가가 기준을 넘으면 상장으로 봄». 주가가 올랐다는 이유만으로 "
                    "실제 상장 조건을 충족한 것으로 보지 않습니다.")


def sha_ipo_issues(tm: Terms) -> list:
    if not int(getattr(tm, "ipo_on", 0)):
        return []
    if int(getattr(tm, "sha_ipo_kind", -1)) not in (0, 1):
        return [SHA_IPO_KIND_MSG]
    # 상장일이 평가기준일 이전(같은 날 포함)이면 격자의 첫 노드(0)에 떨어지는데, 상장 종료는 노드 1 부터
    # 본다 — 이미 끝난 권리를 살아 있는 것으로 재게 된다. 계산하지 않고 알린다.
    if tm.ipo_m > 0:
        try:
            _ld = months_to_date(tm.d_issue, tm.ipo_m)
            if _ld <= dt.date.fromisoformat(tm.d_base):
                return [f"상장 시점({_ld.isoformat()})이 평가기준일({tm.d_base}) 이전입니다 — "
                        + ("실제 상장으로 권리가 이미 끝났으면 그 회차를 «결제 완료» 로 두거나 평가 대상에서 빼십시오."
                           if int(tm.sha_ipo_kind) == 1 else
                           "그 시점의 주가 기준 충족 여부는 이미 정해졌습니다. 충족했으면 권리가 끝난 것이므로 "
                           "그 회차를 «결제 완료» 로 두거나 평가 대상에서 빼고, 충족하지 못했으면 상장 조항을 끄십시오.")]
        except (TypeError, ValueError):
            pass
    return []


def sha_link_issues(kill, qp, qc, lq, writer) -> list:
    """같은 주식에 붙은 풋·콜(상호소멸)의 계산을 막는 입력 — 문장 목록. 단일 계약·회차가 같이 쓴다."""
    out = []
    if not kill or qp <= 0 or qc <= 0:
        if lq is not None and lq > 0 and not kill:
            out.append("«같은 주식에 붙은 물량» 을 넣었는데 «한쪽 행사 시 상대 권리» 가 «존속» 입니다 — 같은 주식이면 "
                       "한쪽 행사로 그 주식의 상대 권리가 끝납니다. 계약서의 소멸 조항을 확인하고 «소멸» 로 바꾸거나, "
                       "다른 주식이면 이 칸을 비우십시오.")
        return out
    if writer == 2:
        out.append("풋 의무자가 «상대 주주·발행회사 연대» 인 계약의 풋·콜 연계(상호소멸)는 지원하지 않습니다 — "
                   "콜 권리자가 풋 의무 가운데 얼마를 지는지 정할 수 없어, 콜 행사로 없어지는 의무를 행사 판단에 넣을 "
                   "수 없습니다. 계약상 1차 의무자를 확인해 «콜 권리자» 또는 «발행회사» 로 입력하십시오.")
    if lq is None or lq < 0:
        if abs(qp - qc) > 1e-9:
            out.append(f"풋 {qp:,.0f}주 · 콜 {qc:,.0f}주로 수량이 다릅니다 — 한쪽 행사로 함께 끝나는 «같은 주식에 붙은 "
                       "물량» 을 입력하십시오. 나머지 풋·콜 물량은 엔진이 상대 권리 없이 따로 잽니다 (줄을 나눌 필요 없음).")
    elif lq > min(qp, qc) + 1e-9:
        out.append(f"같은 주식에 붙은 물량 {lq:,.0f}주가 풋 {qp:,.0f}주 · 콜 {qc:,.0f}주 가운데 작은 쪽보다 큽니다.")
    return out


def sha_contract_issues(tm: Terms) -> list:
    """주주간계약에서 계산을 막는 입력 — 문장 목록. 회차별 표가 있으면 sha_row_issues 가 회차마다 본다."""
    if tm.sha_rows:
        return [sha_row_issue_text(k, m) for k, m in sha_row_issues(tm)]
    qp, qc = sha_qty(tm)
    return sha_ipo_issues(tm) + sha_link_issues(int(tm.sha_kill), qp, qc, float(getattr(tm, "sha_link_q", -1.0)),
                                                int(tm.sha_writer))


def sha_row_issues(tm: Terms) -> list:
    """회차별 표에서 계산을 막아야 하는 입력 — (회차 이름, 문장). 비어 있으면 계산할 수 있다.

    회차 이름은 표의 «평가 구분» 칸이다 (비우면 «N회차»). 문장이 회차 이름으로 시작하면 그대로,
    아니면 «회차 이름: 문장» 으로 보인다 (sha_row_issue_text).
    """
    out = []
    try:
        db = dt.date.fromisoformat(tm.d_base)
    except (TypeError, ValueError):
        return [(0, "평가기준일을 먼저 입력하십시오.")]
    names = set()
    out += [("상장 조항", m) for m in sha_ipo_issues(tm)]
    for k, raw in enumerate(tm.sha_rows or [], 1):
        if not isinstance(raw, dict):
            out.append((k, "회차 줄의 형식이 올바르지 않습니다.")); continue
        r = sha_row_defaults(raw)
        k = str((raw or {}).get("name") or "").strip() or f"{k}회차"
        if r["name"] in names:
            out.append((k, f"회차 이름 «{r['name']}» 이 겹칩니다 — 회차마다 다른 이름을 적으십시오."))
        names.add(r["name"])
        st = r["status"]
        if st not in SHA_ROW_STATUS:
            out.append((k, f"평가 대상 상태 «{st}» 를 알 수 없습니다 — 미행사 · 행사·매매 확정(미결제) · 결제 완료 · "
                           "추가 조건부 가운데 고르십시오.")); continue
        if min(r["put_q"], r["call_q"]) < 0:
            out.append((k, "수량은 0 이상이어야 합니다.")); continue
        if st == "settled":
            # 결제가 끝난 물량 — 남은 선택권이 없다. 수량 대사에만 싣는다.
            continue
        if st == "agreed":
            # 행사·매매가 확정되고 결제만 남은 물량 — 새 선택권을 주지 않고 확정 이전·대금만 잰다.
            if r["side"] not in ("put", "call"):
                out.append((k, f"{k}: 확정된 거래가 풋 행사(주식 보유자가 상대에게 판다)인지 콜 행사(상대가 산다)인지 "
                               "고르십시오.")); continue
            if (r["put_q"] if r["side"] == "put" else r["call_q"]) <= 0:
                out.append((k, f"{k}: 확정된 {'풋' if r['side'] == 'put' else '콜'} 물량(주)을 0 보다 크게 입력하십시오."))
            if not (r["deal_px"] or 0) > 0:
                out.append((k, f"{k}의 확정 주당 매매대금(원)을 입력하십시오."))
            try:
                dsx = dt.date.fromisoformat(r["settle"])
            except (TypeError, ValueError):
                out.append((k, f"{k}의 결제 예정일을 YYYY-MM-DD 로 입력하십시오.")); continue
            if dsx <= db:
                out.append((k, f"{k}: 결제 예정일({dsx})이 평가기준일({db}) 이전입니다 — 이미 결제됐으면 «결제 완료» 로 두십시오."))
            continue
        if st == "cond" and r["cond_basis"] not in SHA_COND_BASIS:
            # 근거 없는 행사확률을 넣지 않는다 — 조건 충족 가정·미충족 가정 가운데 사용자가 고른다.
            out.append((k, f"{k}: 추가 조건이 있는 물량을 어떤 가정으로 평가할지 고르십시오 — 조건 충족 가정(평가에 반영) "
                           "또는 조건 미충족 가정(평가에서 제외). 두 경우의 차이는 결과에 함께 싣습니다."))
        if r["style"] not in SHA_ROW_STYLES:
            out.append((k, f"행사 방식 «{r['style']}» 을 알 수 없습니다 — 기간 중 언제든지(any) · 정기(periodic) · "
                           "특정일 1회(single) 가운데 고르십시오."))
            continue
        try:
            ds = dt.date.fromisoformat(r["start"]); de = dt.date.fromisoformat(r["end"])
            cs = dt.date.fromisoformat(r["call_start"] or r["start"])
            ce = dt.date.fromisoformat(r["call_end"] or r["end"])
            da = dt.date.fromisoformat(r["acc_from"] or tm.d_issue)
        except (TypeError, ValueError):
            out.append((k, "행사 시작일·종료일(·가격 가산 기산일)을 YYYY-MM-DD 로 입력하십시오.")); continue
        if ds > de or cs > ce:
            out.append((k, "행사 시작일이 종료일보다 늦습니다."))
        if r["put_q"] <= 0 and r["call_q"] <= 0:
            out.append((k, "풋 수량과 콜 수량이 모두 0 입니다 — 평가할 권리가 없습니다."))
        if r["perf"]:
            # 실적 연동 — 주당 기준가격은 산식이 정한다. 산식 입력이 모자라면 막는다.
            _miss = [nm for key, nm in (("rev", "매출액"), ("op", "영업손익"), ("thr", "기준 손실률"), ("hi", "초과 시 배수"),
                                        ("lo", "이하 시 배수"), ("sh", "계약상 발행주식 총수"))
                     if r["perf"].get(key) in (None, "")]
            if _miss:
                out.append((k, f"{k}의 실적 연동 행사가격 산식에서 {' · '.join(_miss)}을(를) 입력하십시오."))
            else:
                try:
                    _px = sha_perf_calc(r["perf"])[0]
                    if not _px > 0:
                        out.append((k, f"{k}: 실적 연동 산식의 주당 행사가격이 {_px:,.2f}원입니다 — 매출액·차감액을 "
                                       "확인하십시오 (계약에 없는 하한은 두지 않습니다)."))
                except (ValueError, TypeError, ZeroDivisionError) as ex:
                    out.append((k, f"{k}: {ex}"))
        elif r["price"] is None:
            out.append((k, f"{k}의 주당 기준가격을 입력하십시오."))
        elif not r["price"] > 0:
            out.append((k, f"{k}의 주당 기준가격을 0 보다 크게 입력하십시오."))
        if r["call_price"] is not None and r["call_price"] <= 0:
            out.append((k, "콜 주당 기준가격은 비우거나 0 보다 크게 입력하십시오."))
        last = max(de if r["put_q"] > 0 else dt.date.min, ce if r["call_q"] > 0 else dt.date.min)
        if last < db:
            out.append((k, f"행사기간이 평가기준일({db}) 전에 끝났습니다 — 이미 행사·소멸했는지 확인하고, "
                           "남은 권리가 없으면 줄을 지우십시오. 행사됐다고 앱이 가정하지 않습니다."))
        if da > min(ds, cs):
            out.append((k, "가격 가산 기산일이 행사 시작일보다 늦습니다."))
        if da > db:
            out.append((k, "가격 가산 기산일이 평가기준일보다 늦습니다 — 계약일(기산일)은 평가기준일 이전이어야 합니다."))
        if r["style"] == "periodic" and not (r["freq"] or 0) > 0:
            out.append((k, "정기 행사면 주기(개월)를 0 보다 크게 입력하십시오."))
        if not out or out[-1][0] != k:
            # 회차마다 격자를 따로 세우므로 계산 한도(1,200구간)도 회차마다 본다.
            try:
                _n = sha_row_terms(tm, raw).n
                if _n > SHA_ROW_MAX_N:
                    out.append((k, f"이 회차의 격자가 {_n:,}구간으로 계산 한도({SHA_ROW_MAX_N:,}구간)를 넘습니다 — "
                                   "행사 종료일을 확인하거나 계산 간격을 늘리십시오."))
            except (ValueError, TypeError) as ex:
                out.append((k, f"회차 조건을 읽지 못했습니다 — {ex}"))
        for m in sha_link_issues(r["kill"], r["put_q"], r["call_q"],
                                 -1.0 if r["link_q"] is None else r["link_q"], int(tm.sha_writer)):
            out.append((k, m))
        if r["sig"] is not None and not r["sig"] > 0:
            out.append((k, "회차 변동성은 비우거나 0 보다 크게 입력하십시오."))
    if not out:
        out += sha_pool_issues(tm)
    return out


def sha_row_shares(tm: Terms, raw: dict) -> float:
    """회차가 붙잡고 있는 보유주식 수 — 결제 완료는 0, 확정·미결제는 그 거래 물량, 나머지는 계약 대상 주식."""
    r = sha_row_defaults(raw)
    if r["status"] == "settled":
        return 0.0
    if r["status"] == "agreed":
        return float(r["put_q"] if r["side"] == "put" else r["call_q"])
    return sha_contract_shares(sha_row_terms(tm, raw))


def sha_pool_issues(tm: Terms) -> list:
    """같은 보유주식·공통 한도를 쓰는 회차들이 보유주식·한도를 넘지 않는가 — (이름, 문장).

    회차는 따로 잰다. 같은 주식을 여러 회차가 나눠 쓰고 한 회차의 행사가 다른 회차의 잔량을 줄이는 계약은 행사
    경로마다 잔량이 달라져 이 격자로 정확히 풀 수 없다 — 그래서 합계가 한도 안에 있을 때만 계산한다(어느 경로에서도
    잔량이 모자라지 않는다). 넘으면 막고, 계약 해석에 따라 회차별 물량을 나눠 넣도록 안내한다. 묶음·보유주식을
    넣지 않으면 점검하지 않는다 — 자동으로 같은 주식이라고 가정하지 않는다.
    """
    out, pools = [], {}
    rows = tm.sha_rows or []
    for raw in rows:
        r = sha_row_defaults(raw)
        if r["status"] == "settled" or not r["pool"]:
            continue
        p = pools.setdefault(r["pool"], dict(q=0.0, caps=set(), names=[]))
        p["q"] += sha_row_shares(tm, raw)
        p["names"].append(r["name"])
        if r["pool_cap"] is not None:
            p["caps"].add(float(r["pool_cap"]))
    for nm, p in pools.items():
        if len(p["caps"]) > 1:
            out.append((nm, f"같은 주식 묶음 «{nm}» 의 공통 한도가 회차마다 다릅니다 ({', '.join(f'{c:,.0f}' for c in sorted(p['caps']))}주) "
                            "— 한 값으로 맞추십시오."))
        elif p["caps"] and p["q"] > min(p["caps"]) + 1e-9:
            out.append((nm, f"같은 주식 묶음 «{nm}» 의 회차({' · '.join(p['names'])}) 물량 합 {p['q']:,.0f}주가 공통 한도 "
                            f"{min(p['caps']):,.0f}주를 넘습니다 — 한 회차의 행사가 다른 회차의 잔량을 줄이는 계약은 행사 "
                            "경로마다 잔량이 달라 지원하지 않습니다. 계약 해석에 따라 회차별 물량을 한도 안으로 나눠 넣으십시오."))
    hold = float(getattr(tm, "sha_hold_q", -1.0))
    if hold >= 0:
        tot = sum(sha_row_shares(tm, raw) for raw in rows)
        if tot > hold + 1e-9:
            out.append(("보유주식", f"평가하는 회차들의 대상 주식 합 {tot:,.0f}주가 평가기준일 보유주식 {hold:,.0f}주를 넘습니다 "
                                 "— 같은 주식을 두 회차에서 세고 있지 않은지, 이미 행사·이전한 물량이 «결제 완료» 로 "
                                 "되어 있는지 확인하십시오."))
    return out


def sha_row_terms(tm: Terms, raw: dict) -> Terms:
    """회차 한 줄 → 그 회차만 재는 Terms. 격자는 평가기준일부터 그 회차의 마지막 행사일까지다.

    주당 기준가격을 K0 로, 가격 가산 기산일을 d_issue 로 두면 엔진(sha_engine)을 그대로 쓴다 —
    지분가치 = 100 × 주가 ÷ 기준가격, 행사금액 = 100 × (1 + 가산율 누적). 결과 × 기준가격 ÷ 100
    이 1주당 금액(원)이다.
    """
    r = sha_row_defaults(raw)
    t = Terms(**asdict(tm))
    t.sha_rows = []
    t.tranche = r["name"]
    d0 = r["acc_from"] or tm.d_issue
    t.d_issue = d0
    ps, pe = r["start"], r["end"]
    cs, ce = r["call_start"] or ps, r["call_end"] or pe
    if r["style"] == "single":
        pe, ce = ps, cs
    f = 0.0 if r["style"] == "any" else float(r["freq"] or 12.0)
    if r["put_q"] > 0:
        t.sha_put_s, t.sha_put_e = date_to_months(d0, ps), date_to_months(d0, pe)
    else:
        t.sha_put_s, t.sha_put_e = 99.0, 0.0
    if r["call_q"] > 0:
        t.sha_call_s, t.sha_call_e = date_to_months(d0, cs), date_to_months(d0, ce)
        # 종료 0 은 «콜 없음» 표지다 — 기산일 당일 1회만 여는 콜은 아주 작은 양수로 살린다.
        if t.sha_call_e <= 0: t.sha_call_e = 1e-9
    else:
        t.sha_call_s, t.sha_call_e = 0.0, 0.0
    t.sha_put_f = t.sha_call_f = f
    t.K0 = float(r["price"]) if not r["perf"] else sha_perf_calc(r["perf"])[0]
    t._perf = r["perf"]
    t.sha_call_k = float(r["call_price"]) if r["call_price"] else -1.0
    t.sha_put_yield = float(r["rate"])
    t.sha_call_prem = float(r["call_rate"]) if r["call_rate"] is not None else float(r["rate"])
    t.sha_put_q, t.sha_call_q = float(r["put_q"]), float(r["call_q"])
    t.face_total = t.K0*max(t.sha_put_q, t.sha_call_q)
    t.sha_kill = r["kill"]
    t.sha_link_q = -1.0 if r["link_q"] is None else float(r["link_q"])
    t._price_note = r["price_note"]          # 실적 연동 가격이면 산식 기록 (조서 가정 시트에 싣는다)
    ends = ([pe] if r["put_q"] > 0 else []) + ([ce] if r["call_q"] > 0 else [])
    t.d_mat = max(ends)
    if int(tm.ipo_on) and tm.ipo_m > 0:
        # 적격상장 기한은 공통 계약일 기준 개월로 저장돼 있다 — 이 회차의 기산일 기준으로 옮긴다.
        t.ipo_m = date_to_months(d0, months_to_date(tm.d_issue, tm.ipo_m))
    if r["sig"] is not None: t.sig = float(r["sig"])
    if r["rf"] is not None:
        t.rf_curve = [(0.25, float(r["rf"])), (30.0, float(r["rf"]))]
    if r["pdisc"] is not None:
        t.sha_disc = 1; t.rate_mode = "direct"
        t.cr_curve = [(0.25, float(r["pdisc"])), (30.0, float(r["pdisc"]))]
    derive(t)
    return t


def sha_row_window(t: Terms) -> str:
    """회차 행사기간 한 줄 — 합계표·화면이 쓴다."""
    parts = []
    if t.sha_put_s <= t.sha_put_e:
        parts.append(f"풋 {months_to_date(t.d_issue, t.sha_put_s)}~{months_to_date(t.d_issue, t.sha_put_e)}")
    if t.sha_call_e > 0 and t.sha_call_s <= t.sha_call_e:
        parts.append(f"콜 {months_to_date(t.d_issue, t.sha_call_s)}~{months_to_date(t.d_issue, t.sha_call_e)}")
    return " · ".join(parts)


def sha_portfolio(tm: Terms):
    """주주간계약 평가의 입구. 회차별 표가 없으면 단일 계약 그대로(엔진 결과에 원 단위를 붙인다).

    회차가 있으면 회차마다 따로 잰다 — 연도별 물량·행사기간·행사가격이 다른 계약을 한 물량으로
    합쳐 모든 기간에 행사할 수 있게 재지 않는다. 연도별 미행사 물량을 다음 회차로 넘기지 않는다.
    """
    derive(tm)
    if not tm.sha_rows:
        bad = sha_contract_issues(tm)
        if bad:
            raise ValueError(" / ".join(bad))
        R = sha_engine(tm)
        qp, qc = sha_qty(tm)
        ck = sha_components_krw(tm, R)
        R.update(portfolio=False, put_krw=ck["put"], call_krw=ck["call"],
                 base_amount=tm.K0*sha_contract_shares(tm))
        return R
    bad = sha_row_issues(tm)
    if bad:
        raise ValueError(" / ".join(sha_row_issue_text(k, m) for k, m in bad))
    rows, excluded = [], []
    for raw in tm.sha_rows:
        r = sha_row_defaults(raw)
        if r["status"] == "settled":
            # 결제가 끝난 물량 — 남은 선택권이 없으므로 평가하지 않는다. 수량 대사에만 싣는다.
            excluded.append(dict(name=r["name"], status="settled", qp=r["put_q"], qc=r["call_q"]))
            continue
        if r["status"] == "agreed":
            t, R = sha_deal(tm, raw)
        else:
            t = sha_row_terms(tm, raw)
            if t.n > SHA_ROW_MAX_N:
                raise ValueError(f"{t.tranche}: 격자 {t.n:,}구간 — 계산 한도 {SHA_ROW_MAX_N:,}구간을 넘습니다.")
            R = sha_engine(t)
        qp, qc = sha_qty(t)
        ps, cs = R["put"]/100*t.K0, R["call"]/100*t.K0
        # 추가 조건부 물량 — 사용자가 고른 가정만 금액에 반영한다. 조건 충족 시 금액은 차이 분석으로 함께 싣는다.
        incl = 0.0 if (r["status"] == "cond" and r["cond_basis"] == "unmet") else 1.0
        rows.append(dict(name=t.tranche, tm=t, R=R, qp=qp, qc=qc, K=t.K0, status=r["status"], incl=incl,
                         cond_basis=r["cond_basis"], cond_note=r["cond_note"], pool=r["pool"],
                         put_ps=ps, call_ps=cs, put_krw=incl*ps*qp, call_krw=incl*cs*qc,
                         put_krw_met=ps*qp, call_krw_met=cs*qc,
                         window=(sha_deal_window(r) if r["status"] == "agreed" else sha_row_window(t))))
    if not rows:
        raise ValueError("평가할 회차가 없습니다 — 모든 회차가 «결제 완료» 입니다. 남은 선택권이 없으면 평가 대상이 아닙니다.")
    put_k = sum(x["put_krw"] for x in rows); call_k = sum(x["call_krw"] for x in rows)
    base = sum(x["incl"]*x["K"]*sha_contract_shares(x["tm"]) for x in rows)
    _lat = [x for x in rows if not x["R"].get("deal")]
    qbad = [q for x in _lat for q in x["R"]["qbad"]]
    return dict(portfolio=True, rows=rows, excluded=excluded, put_krw=put_k, call_krw=call_k, base_amount=base,
                put=(put_k*100/base if base else 0.0), call=(call_k*100/base if base else 0.0),
                qbad=qbad, qmin=min((x["R"]["qmin"] for x in _lat), default=0.5),
                qmax=max((x["R"]["qmax"] for x in _lat), default=0.5),
                n=max(x["R"]["n"] for x in rows), kill=any(x["R"]["kill"] for x in rows),
                recon=sha_quantity_recon(tm, rows, excluded))


def sha_deal_window(r: dict) -> str:
    return (f"{'풋' if r['side'] == 'put' else '콜'} 행사 확정 · 결제 {r['settle']} · 주당 {float(r['deal_px']):,.2f}원")


def sha_deal(tm: Terms, raw: dict):
    """행사·매매가 확정되고 결제만 남은 회차 — (Terms, 결과). 새 선택권을 주지 않는다.

    주식 보유자가 결제일에 주식을 넘기고 확정 매매대금을 받는다(선도 거래). 1주당 금액(100 기준 = 매매대금 100):
      풋 행사 확정  풋 = 100 × 대금 할인계수(풋 할인율) − 주식 현재가치
      콜 행사 확정  콜 = 주식 현재가치 − 100 × 대금 할인계수(무위험)
    주식 현재가치 = 100 × 주가 ÷ 매매대금 × EXP(−배당수익률 × 결제까지 연수). 음수도 그대로 둔다(0 으로 자르지 않는다).
    대금의 신용위험은 미행사 풋과 같은 할인율을 쓴다 — 대금을 지급하는 쪽의 신용이 그 할인율과 맞는지 확인하십시오.
    """
    r = sha_row_defaults(raw)
    t = Terms(**asdict(tm))
    t.sha_rows = []
    t.tranche = r["name"]
    t.d_mat = r["settle"]
    t.K0 = float(r["deal_px"])
    t.sha_put_q, t.sha_call_q = ((float(r["put_q"]), 0.0) if r["side"] == "put" else (0.0, float(r["call_q"])))
    # 거래는 확정 — 행사기간·소멸·연계 판단이 없다
    t.sha_put_s, t.sha_put_e = (0.0, 0.0) if r["side"] == "put" else (99.0, 0.0)
    t.sha_call_s, t.sha_call_e = (0.0, 1e-9) if r["side"] == "call" else (0.0, 0.0)
    t.sha_kill = 0; t.sha_link_q = -1.0
    t.face_total = t.K0*max(t.sha_put_q, t.sha_call_q)
    if r["sig"] is not None: t.sig = float(r["sig"])
    if r["rf"] is not None:
        t.rf_curve = [(0.25, float(r["rf"])), (30.0, float(r["rf"]))]
    if r["pdisc"] is not None:
        # 회차 할인율을 직접 넣었으면 등급 보간을 끈다 — 켜 두면 curves() 가 이 곡선을 공통 등급 곡선과
        # 섞어 확정 거래의 결제대금을 입력한 율이 아닌 섞인 율로 할인한다 (미행사 회차와 같은 처리).
        t.cr_curve = [(0.25, float(r["pdisc"])), (30.0, float(r["pdisc"]))]; t.sha_disc = 1; t.rate_mode = "direct"
    derive(t)
    T = max(0.0, (dt.date.fromisoformat(r["settle"]) - dt.date.fromisoformat(tm.d_base)).days/365)
    RF, CR = curves(t)
    zr = RF(T) if T > 0 else 0.0
    zp = (zr if int(t.sha_disc) == 0 else (zr + t.sha_spread) if int(t.sha_disc) == 2 else (CR(T) if T > 0 else 0.0))
    dfr, dfp = math.exp(-zr*T), math.exp(-zp*T)
    sh = 100*t.S0/t.K0*math.exp(-t.div_y*T)
    put = 100*dfp - sh if r["side"] == "put" else 0.0
    call = sh - 100*dfr if r["side"] == "call" else 0.0
    R = dict(deal=True, side=r["side"], put=put, call=call, T=T, zr=zr, zp=zp, dfr=dfr, dfp=dfp, share_pv=sh,
             settle=r["settle"], qbad=[], qmin=0.5, qmax=0.5, n=0, kill=False, linked=False,
             has_put=r["side"] == "put", has_call=r["side"] == "call", gross=None, blocks=[],
             p_dates={}, c_dates={})
    return t, R


def sha_quantity_recon(tm: Terms, rows, excluded) -> list:
    """수량 대사 — [(구분, 회차, 상태, 풋 주식수, 콜 주식수, 계약 대상 주식, 평가 반영)]. 맨 끝에 보유주식 대사."""
    out = []
    for x in rows:
        out.append((x["name"], SHA_ROW_STATUS.get(x["status"], x["status"])
                    + (f" · {SHA_COND_BASIS[x['cond_basis']]}" if x["status"] == "cond" and x["cond_basis"] in SHA_COND_BASIS else ""),
                    x["qp"], x["qc"], sha_contract_shares(x["tm"]) if not x["R"].get("deal") else max(x["qp"], x["qc"]),
                    "반영" if x["incl"] else "제외 (가정)", x.get("pool") or ""))
    for e in excluded:
        out.append((e["name"], SHA_ROW_STATUS["settled"], e["qp"], e["qc"], 0.0, "제외 (남은 선택권 없음)", ""))
    return out



def sha_portfolio_integrity(tm: Terms, P) -> list:
    """회차마다 sha_integrity — 회차 이름을 붙여 돌려준다."""
    if not P.get("portfolio"):
        return sha_integrity(tm, P)
    return [f"{x['name']}: {m}" for x in P["rows"] for m in sha_integrity(x["tm"], x["R"])]


def sha_backsolve(tm: Terms, target: float = None):
    """투자원금으로 지분가치를 역산한다.

    비상장 대상회사는 관측 주가가 없다. 「지분 + 풋 − 콜 = 투자원금」 이라고
    놓고 그 등식을 만족하는 주가를 이분법으로 찾는다. 투자자가 낸 돈이 곧
    받은 것의 공정가치라는 발행 시점의 전제다.
    """
    target = 100.0 if target is None else target
    t2 = Terms(**asdict(tm))
    # 풋·콜 수량이 다를 수 있다 — 100 기준 값을 그냥 더하지 않고 원 단위로 더한 뒤 계약 대상 주식으로 나눈다.
    # 수량이 같으면 «지분 + 풋 − 콜» (100 기준) 과 같다.
    qp, qc = sha_qty(tm); cs = sha_contract_shares(tm)

    def f(S):
        t2.S0 = S
        r = sha_engine(t2)
        if cs <= 0:
            return 100*S/t2.K0 + r["put"] - r["call"]
        return (100*S/t2.K0*cs + r["put"]*qp - r["call"]*qc)/cs

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


def lock_delay(tm: Terms, lock=None):
    """의무보유가 미루는 «전환 시작 · 조기상환 시작». 유무가치비교법의 With 격자용.

    계약 정의는 「콜 대상비율 물량을 의무보유 기간 동안 **전환 및 조기상환청구가
    불가능한 상태로** 보유」다. 전환만 미루면 그 물량이 조기상환으로 빠져나갈 수 있어
    콜이 살 대상이 사라진다 — 계약과 다르다. ``tm.k_lock_put`` 이 0 이면 전환만 막는
    계약이므로 조기상환 시작은 그대로 둔다.
    """
    lk = tm.k_lock if lock is None else lock
    if not int(getattr(tm, "k_hold", 1)): lk = 0.0   # 의무보유 없음 — 두 방법 모두 제약 없음
    cs = max(tm.cv_s, lk)
    ps = max(tm.p_s, lk) if int(getattr(tm, "k_lock_put", 1)) else tm.p_s
    return cs, ps


def lock_end_step(tm: Terms, n: int, dt_: float, lock=None) -> int:
    """의무보유가 걸려 있는 마지막 노드. 의무보유가 없으면 −1.

    의무보유 종료일도 계약일이다. 행사일과 같은 규칙으로 노드에 배정한다 —
    종료일 이전 마지막 노드(hi)까지 묶이고, **종료일 이전 계약일의 매도청구가 배정된
    노드**도 묶인 채로 본다. 매도청구일은 «그날 이후 첫 노드» 로 가므로, 노드가 계약일과
    어긋나면 같은 날의 마지막 매도청구(예: 78번)가 의무보유 종료(77번) 뒤로 밀려 그날
    투자자가 먼저 전환해 버리는 일이 생겼다. 같은 계약일은 같은 노드에서 함께 처리한다.
    엔진(유무가치비교법 With 격자 · 옵션차익법) · 값 조서 · 수식 조서가 이 값 하나를 쓴다.
    """
    lk = tm.k_lock if lock is None else lock
    if not int(getattr(tm, "k_hold", 1)) or lk <= 0 or tm.k_w <= 0:
        return -1
    kd = exercise_amounts(tm, n, dt_)["k_dates"]
    # 의무보유는 매도청구에 딸린 약정이다. 격자 안에 남은 매도청구일이 없으면 지킬 매도청구가 없으므로
    # 종료 노드를 따로 묶지 않는다 (시작 지연 lock_delay 만 남는다 — 종전과 같다).
    if not kd:
        return -1
    _, hi = step_mapper(tm, n, dt_)
    L = hi(lk)
    for i, m in kd.items():
        if m is not None and m <= lk + 1e-9:
            L = max(L, i)
    return min(L, n)


def lock_share(tm: Terms) -> float:
    """콜 대상 물량 가운데 의무보유로 묶인 물량 (발행총액 대비). 의무보유가 없으면 0.

    의무보유 비율을 따로 넣지 않았으면(음수) 콜 대상 전부다 — 종전과 같다. 콜 한도보다 크게 넣어도
    콜 대상 밖의 물량은 콜 값에 닿지 않으므로 콜 한도에서 자른다 (validate 가 알린다).
    """
    if not int(getattr(tm, "k_hold", 1)) or tm.k_w <= 0:
        return 0.0
    lw = float(getattr(tm, "k_lock_w", -1.0))
    return float(tm.k_w) if lw < 0 else min(lw, float(tm.k_w))


def call_mix(tm: Terms, unit_locked, unit_free) -> float:
    """매도청구권 = 의무보유 물량 × 묶인 1단위 값 + (콜 한도 − 의무보유 물량) × 묶이지 않은 1단위 값.

    콜 한도는 한 번만 걸린다 — 두 몫을 더하면 콜 대상 전체다. ``unit_*`` 는 값을 돌려주는 함수라
    한쪽 몫이 0 이면 그 격자를 만들지 않는다 (의무보유 비율 = 콜 한도면 종전 계산 그대로)."""
    w, L = float(tm.k_w), lock_share(tm)
    v = 0.0
    if L > 1e-12:
        v += L*unit_locked()
    if w - L > 1e-12:
        v += (w - L)*unit_free()
    return v


# 기초 사채가 그 자리에서 정산되어 사라지는 결정들. 의무보유가 없을 때 콜도 함께 소멸한다.


def call_third_party(tm: Terms, full, method: int, nodes=None) -> float:
    """제3자 지정 가능 콜옵션 — 옵션차익혼합할인법.

    한국공인회계사회 『K-IFRS 실무사례와 해설 연구보고서 시리즈 11 복합금융상품』
    4.3.3·4.3.4·4.4.3 의 산식이다. 발행자가 지정한 제3자에게 넘어갈 수 있는
    콜옵션은 기준서 1109 문단 4.3.1 상 별도의 금융상품이고, 기초자산이
    전환사채인 복합옵션(an option on an option)이므로 격자 안에서
    MIN(계속보유, 콜금액) 으로 누르는 발행자 콜옵션과 다르게 평가한다.
    발행자 콜은 행사하면 사채가 소멸해 기초자산이 남지 않으므로 복합옵션 구조가
    성립하지 않고, 그래서 4.3.2 의 유무가치비교법을 쓴다.

    기초자산은 ``콜과 그 부속조항(의무보유 등)을 포함하지 않은 전환사채`` 다.
    ``decompose`` 가 넘기는 ``full = engine(tm, call=False)`` 이 정확히 그것이라
    새 격자를 만들지 않고 그 memo 를 한 번 더 역진한다.

    본문 3.2 (p.50) 가 「혼합할인율 이항모형」을 **모형군의 이름**으로 정의한다 — 노드의
    페이오프 성격에 따라 위험할인율과 무위험할인율을 «구분하여» 적용하는 방식 전체를 말하고,
    그 안에 **TF & Hull(지분-채권 현금가중할인)** 과 **GS(전환가중확률할인)** 가 나란히 있다.
    그래서 두 갈래를 모두 둔다.

    method 1  GS 식 전환가중확률할인 — 값 하나를 자식의 «지분 성격 비중» 으로 섞은 할인율로
              할인한다. 비중은 늘 [0, 1] 이다.
    method 2  TF 식 지분-채권 분리할인 — 페이오프를 지분 몫·채권 몫으로 쪼개 각각 Rf·Rd 로
              할인한다. 두 몫은 부호가 갈릴 수 있고(long-short), 합은 언제나 페이오프다.

    지분·채권 구분 기준 ``tm.k_split``:
      0  비례균등차감법 — 지분·부채 가치 구성비율 w = E/(E+B). 행사가를 페이오프에 비례해
         균등 차감하는 것과 같다. 국내 실무서·부속예제가 쓰는 방식이고 **한공회 본문에서
         도출되지는 않는다** (w = P 는 전환·상환 두 시나리오의 현가가 같을 때만 성립한다).
      1  한공회 본문 4.3.3 — 「행사가를 분해하기 위해서는 … 행사 확률인 위험중립확률을 구해야
         한다. 이 단계에서 GS 모형의 전환확률을 활용할 수 있다」. 지분 몫 = E − P·K,
         채권 몫 = B − (1−P)·K. 각 몫은 «확률 × (그 시나리오 현가 − 행사가)» 라 상환
         시나리오에서 음수가 될 수 있다 — 콜이 주식으로 갈 시나리오에서만 이득이라는 뜻이다.

    의무보유 ``tm.k_hold`` — 기초자산에서는 빼고(4.4.2·4.4.3 문언) **옵션 계약층에서만**
    반영한다. 기간은 ``tm.k_lock``, 막는 권리는 전환과 (``tm.k_lock_put`` 이면) 조기상환
    청구다 — 유무가치비교법이 보는 것과 같다. 투자자의 제한 자체를 별도의 가치요소로 콜에
    더하지는 않고,
    의무보유가 끝난 자리에서 콜이 소멸하는지는 ``tm.pc_order`` 를 따른다 — 「발행자 콜
    우선」이면 투자자의 전환·조기상환이 매도청구에 밀리므로 콜이 소멸하지 않고 행사된다.
    한 격자에서 두 층이 다른 계약을 읽으면 안 된다.

    그 제한으로 콜 대상 전환사채가 행사기간 동안 존속하여 콜의 행사 가능성이 유지되는
    효과만 콜 계약가치에 담는다. 0 이면 기초 사채가 소멸하는 노드에서 콜도 함께 사라진다.

    ``nodes`` 에 사전을 넘기면 노드마다 ``(콜 가치, 지분 몫, 채권 몫)`` 을 채워 준다 —
    불변식 시험이 뿌리뿐 아니라 «모든» 노드를 볼 수 있게 하려는 것이다.
    """
    memo, dt_ = full["memo"], full["dt"]
    qi, fRF, fCR = full["qi"], full["fwdRF"], full["fwdCR"]
    # 행사가 = 콜을 행사할 때 치르는 금액. 행사일 이자를 따로 지급하는 계약이면 그 이자까지다
    # (격자 안 매도청구·수식 조서와 같은 금액 — kcash). 예전에는 매도청구금액만 써서 별도 지급
    # 스위치를 켜도 옵션차익법 값이 바뀌지 않았다.
    kstrike = full.get("kcash", full["kstrike"])
    n_last = int(full["n"])
    ksplit = int(getattr(tm, "k_split", 0)) == 1
    khold = int(getattr(tm, "k_hold", 1)) == 1
    # 의무보유가 살아 있는 마지막 스텝. 없으면 -1 이라 첫 노드부터 소멸 조건이 걸린다.
    lock_end = lock_end_step(tm, n_last, dt_) if khold else -1
    # 의무보유가 조기상환청구까지 막는가. 0 이면 전환만 막으므로 조기상환 노드에서는
    # 의무보유 기간 안이라도 사채가 사라지고 콜도 함께 사라진다.
    lkput = int(getattr(tm, "k_lock_put", 1)) == 1
    # 같은 노드에서 투자자의 전환·조기상환과 발행자의 매도청구가 함께 열릴 때 누가
    # 먼저 움직이는지는 계약이 정한다 (pc_order) — 기초 격자가 이미 쓰는 스위치다.
    # 「발행자 콜 우선」이면 투자자가 사채를 없애기 전에 콜이 행사되므로, 의무보유가
    # 끝난 뒤라도 그 자리에서 콜이 소멸하지 않고 행사된다. 자동전환·상장전환·만기는
    # 투자자의 «선택» 이 아니라 밀릴 수 없으므로 그대로 소멸한다.
    kfirst = int(getattr(tm, "pc_order", 0)) == 1
    # 매도청구 통지 뒤 전환할 수 있는가 (k_conv_resp) — 격자 안 매도청구(node_decide)와 같은 스위치다.
    cresp = conv_resp(tm)
    # 현금납입 BW 는 신주인수권을 행사해도 사채가 남는다 — 전환으로 콜을 피하는 갈래가 없다.
    bwc = bw_cash(tm)
    cache = {}

    def w(o):
        """가치 구성비율 — 노드 가치 중 지분 몫. 늘 [0, 1]."""
        v = o["E"] + o["B"]
        return o["E"]/v if v > 1e-12 else 0.0

    def gamma(o):
        """방법 1 의 지분 성격 비중. 두 기준 모두 [0, 1] 이라 할인율이 Rf~Rd 를 벗어나지 않는다."""
        return o.get("P", 0.0) if ksplit else w(o)

    def split(o, i, pay):
        """행사 페이오프의 (지분 몫, 채권 몫). 합은 언제나 pay 다."""
        K = kstrike(i)
        if pay <= 0 or K is None: return 0.0, 0.0
        if not ksplit:
            ww = w(o); return pay*ww, pay*(1-ww)
        P = o.get("P", 0.0)
        return o["E"] - P*K, o["B"] - (1-P)*K

    def rec(key, i):
        if key in cache: return cache[key]
        o = memo[key]
        K = kstrike(i)
        pay = max(o["E"] + o["B"] - K, 0.0) if K is not None else 0.0
        # 이 노드에서 투자자가 스스로 할 수 있는 것 — 의무보유는 전환을(그리고 k_lock_put 이면
        # 조기상환도) 막는다. 의무보유 기간 안이면 그 물량이 묶여 있어 콜이 존속한다.
        _kd = o.get("kind")
        _locked = i <= lock_end
        can_c = (not bwc) and (not _locked) and o.get("cv", 0.0) > 0
        can_p = (not _locked or not lkput) and o.get("pv", 0.0) > 0
        # 콜을 행사당하는 순간 투자자가 대신 고를 수 있으면 콜이 살 사채가 없다 — 행사해도 0 이다.
        #   전환: 매도청구 통지 뒤 전환할 수 있는 계약(k_conv_resp)이고 전환가치가 행사가를 넘을 때
        #   조기상환: 풋이 콜보다 먼저인 계약(pc_order 0)이고 조기상환금액이 행사가 이상일 때
        # 격자 안 매도청구(node_decide)의 동점 규칙과 같다 — 전환은 앞설 때만, 풋은 동점이면 이긴다.
        if pay > 0 and K is not None:
            _cv, _pv = o.get("cv", 0.0), o.get("pv", 0.0)
            _resp = ((cresp and can_c and _cv >= K + tie_tol(_cv, K)) or
                     (not kfirst and can_p and _pv >= K - tie_tol(_pv, K)))
            ex = 0.0 if _resp else pay
        else:
            ex = 0.0
        # 투자자가 스스로 전환·조기상환해 사채가 이 노드에서 끝나는 자리. 그 권리가 콜보다 먼저면
        # (전환 대응 가능 · 풋 우선) 콜도 함께 사라지고, 콜이 먼저면 콜은 지금 행사될 수 있다.
        _free = ((_kd == "conv" and can_c) or (_kd == "put" and can_p)) and "up" in o
        if _free:
            _first = (_kd == "conv" and cresp) or (_kd == "put" and not kfirst)
            if not _first and ex > 0:
                e_, b_ = split(o, i, ex); r = (ex, e_, b_)     # 콜이 먼저 행사된다
            else:
                r = (0.0, 0.0, 0.0)
        elif "up" not in o and i < n_last:      # 중도 소멸 — 상장 강제전환 (자식이 없다)
            # 상장하면 그 자리에서 보통주가 된다. 콜이 살 사채가 없으므로 콜도 소멸한다 —
            # 유무가치비교법 격자도 상장 노드에서 콜보다 강제전환이 먼저다. 예전에는 자식이
            # 없는 노드를 모두 만기로 보아 상장 노드에 콜 행사이익을 남겼다.
            r = (0.0, 0.0, 0.0)
        elif "up" not in o:                     # 만기 — 자식이 없다
            # 만기일에는 만기상환·전환(자동전환 포함)이 매도청구보다 먼저다 — 그날 사채가 상환·전환되어
            # 콜이 살 대상이 남지 않는다. 유무가치비교법 격자도 만기 노드에서 매도청구를 보지 않는다
            # (MAX(전환, 상환) 만 고른다). 예전에는 이 방법만 만기에 «만기 가치 − 행사가» 를 남겨
            # 행사기간이 만기까지 열린 계약에서 두 방법이 다른 계약을 읽었다.
            r = (0.0, 0.0, 0.0)
        else:
            q, ou, od = qi(i), memo[o["up"]], memo[o["dn"]]
            cu, eu, bu = rec(o["up"], i+1)
            cd, ed, bd = rec(o["dn"], i+1)
            if method == 1:
                yu = gamma(ou)*fRF(i) + (1-gamma(ou))*fCR(i)
                yd = gamma(od)*fRF(i) + (1-gamma(od))*fCR(i)
                cont = q*cu*math.exp(-yu*dt_) + (1-q)*cd*math.exp(-yd*dt_)
                r = (max(ex, cont), 0.0, 0.0)
            else:
                he = (q*eu + (1-q)*ed) * math.exp(-fRF(i)*dt_)
                hb = (q*bu + (1-q)*bd) * math.exp(-fCR(i)*dt_)
                if ex > 0 and ex >= he + hb:
                    e_, b_ = split(o, i, ex); r = (ex, e_, b_)
                else:
                    r = (he + hb, he, hb)
        cache[key] = r
        return r

    v = rec(full["root"], 0)[0]
    if nodes is not None: nodes.update(cache)
    return v


CALL_HOWTO = """\
| 단계 | 하는 일 | 조서 시트 |
|---|---|---|
| ① | **기초자산 격자** — 콜도 의무보유도 없는 전환사채. 노드마다 지분 조각·채권 조각·전환확률이 나온다 | 04 · 05 · 06 · 08 · 11 |
| ② | **회차별 매도청구 행사금액** — 계약서의 행사금액표와 대조한다 | 트리 8행 |
| ③ | **노드별 즉시행사가치** = (지분 + 채권) − 행사금액, 음수면 0. 콜 통지에 투자자가 전환·조기상환으로 대응할 수 있는 자리(계약의 우선순위를 따른다)는 0 | 19 |
| ④ | **콜이 이어지는가** — 상장 강제전환·만기, 또는 투자자가 콜보다 먼저 사채를 끝내는 자리에서는 소멸 | 19a |
| ⑤ | **그 이득의 성격을 가른다** — 전환확률(⑪) 또는 가치 구성비율(⑰) | 11 · 11b · 17 · 17a · 17b |
| ⑥ | **뒤에서 앞으로 되짚는다** — 「지금 행사」와 「계속 보유」 중 큰 쪽. 계속 보유는 지분 성격에 무위험, 채권 성격에 위험 선도이자율. 성분 분리할인은 두 성분의 합으로 한 번만 판단한다 | 18 · 19b · 20 · 21~24 |
| ⑦ | **뿌리 값 × 콜 대상비율** | 결과 |

본문 3.2 는 「노드의 페이오프 성격에 따라 위험할인율 또는 무위험할인율이 **구분되어 적용**됨으로 인해
**혼합할인율 이항모형**으로 알려져 있다」고 하며, 그 안에 **TF & Hull(지분-채권 현금가중할인)** 과
**GS(전환가중확률할인)** 를 나란히 둡니다. 앱의 두 갈래가 그것입니다."""


# 기특정 콜에서 «본문이 하나로 결론짓지 않는다» 는 사실을 화면·조서에 똑같이 싣는다.
# 4.5.1 은 「주주간 분배로 회계처리 되는 경우가 있다」이지 「항상 그렇다」가 아니고,
# 바로 뒤에 4.5.2 접근법 1 · 4.5.3 접근법 2-1 · 4.5.4 접근법 2-2 를 나란히 둔다.
KKIND_CHOICE = ("본문 4.5 는 기특정 콜에 **세 접근법**을 나란히 둡니다 — 4.5.2 접근법 1(발행자 "
                "콜과 같이 유무가치비교법), 4.5.3 접근법 2-1(이연지정), 4.5.4 접근법 2-2. "
                "4.5.1 도 「주주간 분배로 **회계처리 되는 경우가 있다**」고 쓰지 「항상 그렇다」고 "
                "하지 않습니다. **이 앱은 접근법 2-2 를 채택**했습니다. 접근법 1 로 가려면 "
                "평가방법을 「유무가치비교법」으로 두십시오. 접근법 2-1 은 다루지 않습니다.")


def call_type_note(tm: Terms) -> str:
    """콜 유형별 한공회 실무 접근 — 화면에 서너 줄로 요약해 싣는다."""
    if is_sha(tm) or tm.k_w <= 0: return ""
    if issuer_redeem(tm) or not tm.k_third:
        return ("**발행자 콜** — 한공회 기본 접근은 **유무가치비교법**입니다 (4.3.2). 콜조항을 넣은 "
                "값과 뺀 값의 차이로 봅니다. 행사하면 사채가 소멸해 기초자산이 남지 않으므로 "
                "복합옵션 구조가 성립하지 않고, 거래상대방도 그대로라 내재파생이어서 따로 자산으로 "
                "세우지 않습니다.")
    if int(tm.k_kind) == 1:
        return ("**제3자 사전 기특정 콜** — 값은 지정 가능 콜과 같은 격자에서 나옵니다 (4.5.4 접근법 "
                "2-2). 발행 시 제3자가 이미 정해져 있으므로 이 접근법에서는 발행회사가 옵션 "
                "당사자가 아니라고 보아 파생상품자산을 인식하지 않고 최초 인식 시 **주주간 분배**로 "
                "봅니다. " + KKIND_CHOICE + " 선택한 방법과 근거를 일관되게 문서화하십시오 (4.6).")
    return ("**제3자 지정 가능 콜** — 거래상대방이 달라 **별도의 금융상품**이고 (기준서 1109 문단 "
            "4.3.1), 전환사채를 먼저 평가한 뒤 그 전환사채를 기초자산으로 하는 **복합옵션**으로 "
            "잽니다 (4.3.4 · 4.4.3). 유무가치비교법도 쓸 수 있으나 재는 대상이 달라 값이 갈립니다 — "
            "선택한 평가방법과 근거를 일관되게 문서화하십시오 (4.6).")


def call_compare(tm: Terms, full, b2):
    """매도청구권을 방법별로 나란히 잰다 — 화면·값 조서·수식 조서가 같은 함수를 쓴다.

    옵션차익 네 조합은 이미 만든 ``full`` 의 memo 를 한 번 더 역진할 뿐이라 싸다.
    유무가치비교법만 격자를 두 장 더 만든다 (의무보유 있는 것과 뺀 것).

    돌려주는 것 ``(rows, rec)``
      rows  [(방법, 지분·채권 구분 기준, 값, 적용 여부)]
      rec   적용값과 유무가치비교법의 차이를 두 조각으로 나눈 정합 분해.
            한공회 4.1.1 이 「두 방법은 개념적으로 결과가 동일하여야 하나 세부적인
            구현방법에서 … 차이가 종종 발생한다」고 하므로, 그 차이를 설명해 둔다.
    """
    if is_sha(tm) or tm.k_w <= 0: return [], {}
    ks = full["kstrike"]
    if not any(ks(i) is not None for i in range(int(full["n"])+1)): return [], {}

    def opt(method, split, hold):
        t2 = Terms(**asdict(tm)); t2.k_split = split; t2.k_hold = hold
        t0 = Terms(**asdict(t2)); t0.k_hold = 0
        if not hold:
            return tm.k_w * call_third_party(t0, full, method)
        return call_mix(tm, lambda: call_third_party(t2, full, method),
                        lambda: call_third_party(t0, full, method))

    def _wunit(lock):
        cs, ps = lock_delay(tm, lock)
        return b2 - pick(engine(tm, conv=True, put=True, call=True, conv_start=cs,
                                put_start=ps, lock_m=lock), tm.model)

    def wow(lock):                      # 유무가치비교법 — 콜을 넣고 뺀 차액
        if lock <= 0:
            return tm.k_w * _wunit(0.0)
        return call_mix(tm, lambda: _wunit(lock), lambda: _wunit(0.0))

    km, kspl, khl = int(tm.k_method), int(tm.k_split), int(tm.k_hold)
    A, A0 = wow(tm.k_lock), wow(0.0)
    rows = [("유무가치비교법 (4.3.2)", "해당 없음 — 격자에서 직접", A, km == 0)]
    if tm.k_lock > tm.cv_s:
        rows.append(("유무가치비교법 · 의무보유 뺀 값 (참고)", "해당 없음", A0, False))
    for m in (1, 2):
        mn = K_METHODS_SHORT[m]
        for sp in (1, 0):
            rows.append((mn, K_SPLITS[sp].split(" —")[0], opt(m, sp, khl),
                         km == m and kspl == sp))
    rec = {}
    if km:                              # 적용값이 옵션차익법일 때만 분해가 뜻을 가진다
        O = opt(km, kspl, khl)          # 적용 산식·적용 설정 (의무보유 설정 그대로)
        B = opt(km, kspl, 0)            # 정산 시 콜 소멸 (의무보유 없음)
        # 두 조각의 합은 언제나 «유무가치 − 옵션차익(적용)» 이다. 의무보유가 없으면 A = A0,
        # O = B 라 ② 가 0 이 된다 — 예전에는 ② 에 «의무보유 있음» 값을 써서 의무보유를 끈
        # 설정에서 합계가 실제 차이와 맞지 않았다.
        rec = {"유무가치비교법 (적용 계약)": A,
               "옵션차익법 (적용 산식·적용 설정)": O,
               "① 방법론 차이 (둘 다 의무보유 없음)": A0 - B,
               "② 의무보유가 두 방법에 다르게 들어가는 부분": (A - A0) - (O - B)}
        if khl:
            rec["참고 · 유무가치법의 의무보유 효과 (의무보유 있음 − 없음)"] = A - A0
            rec["참고 · 옵션차익법의 의무보유 효과 (콜 행사기회 보전)"] = O - B
    return rows, rec


def wow_trace(tm: Terms, full, b2):
    """유무가치비교법 차액이 어디서 오는가 — 관련 약정(의무보유) · 행사 판정 · 할인 방식으로 나눈다.

    한공회 4.3.2 의 차액 «콜이 없을 때 − 콜이 있을 때» 는 음수가 될 수 있다. 음수를 0 으로 덮지 않고
    원인을 셀 수 있게 세 조각으로 나눈다 (합이 언제나 적용값이다).

      의무보유 효과 = A − A0          (A 적용 계약, A0 의무보유를 뺀 콜만의 차액)
      A0 = X + Y — 두 격자(콜 없음 · 콜 있음, 의무보유 없음)를 노드마다 견주어
        X  행사 판정 효과 — 노드에서 콜 때문에 달라진 값을 위험 선도이자율로만 할인해 더한 값. 노드 종류로
           다시 나눈다: 콜이 행사된 노드(≥ 0) · 콜 통지에 투자자가 전환으로 대응한 노드(강제전환, ≥ 0) ·
           콜이 계속보유가치를 낮춰 투자자가 먼저 전환·조기상환한 노드(투자자 대응, ≤ 0 이 보통).
        Y  할인 방식 효과 — 같은 변화가 주식결제분(TF: 무위험 할인)이나 전환확률(GS: 혼합 할인)을 바꿔
           생긴 할인 차이. 콜이 강제전환을 앞당겨 현금결제분이 주식결제분으로 바뀌면 노드 값은 줄어도
           앞 노드에서 덜 할인되어 Y 가 음수가 된다.

    노드 D = X 조각 + 위험할인 전파 + Y 조각 은 항등식이라 X + Y 는 A0 와 부동소수 오차 안에서 같다.
    """
    if is_sha(tm) or tm.k_w <= 0: return {}
    ks = full["kstrike"]
    if not any(ks(i) is not None for i in range(int(full["n"])+1)): return {}
    cs, ps = lock_delay(tm, 0.0)
    w = engine(tm, conv=True, put=True, call=True, conv_start=cs, put_start=ps)
    mo, mw = full["memo"], w["memo"]
    dt_ = w["dt"]
    gs = tm.model == "GS" and not bw_cash(tm)
    val = (lambda o: o["V"]) if gs else (lambda o: o["E"] + o["B"])

    def disc(o, fr, fc):
        """자식 한 노드를 부모로 끌어올 때의 할인값 — 모형의 규칙 그대로."""
        if gs:
            y = o.get("P", 0.0)*fr + (1 - o.get("P", 0.0))*fc
            return o["V"]*math.exp(-y*dt_)
        return o["E"]*math.exp(-fr*dt_) + o["B"]*math.exp(-fc*dt_)

    Wt = {w["root"]: 1.0}; Y = 0.0; nf = nc = 0
    Xg = {"call": 0.0, "forced": 0.0, "resp": 0.0}
    grp = lambda b: ("call" if b.get("kind") == "call" else "forced" if b.get("forced") else "resp")
    for key in sorted(mw.keys()):
        if key not in Wt or key not in mo: continue
        a, b = mo[key], mw[key]; i = key[0]
        D = val(a) - val(b)
        if b.get("forced"): nf += 1
        if b.get("kind") == "call": nc += 1
        if "up" not in a or "up" not in b:
            Xg[grp(b)] += Wt[key]*D; continue
        q, fr, fc = w["qi"](i), w["fwdRF"](i), w["fwdCR"](i)
        comp = cont = 0.0
        for ch, p in ((b["up"], q), (b["dn"], 1 - q)):
            ao, bo = mo[ch], mw[ch]
            dvo, dvw = disc(ao, fr, fc), disc(bo, fr, fc)
            base_o, base_w = val(ao)*math.exp(-fc*dt_), val(bo)*math.exp(-fc*dt_)
            comp += p*((dvo - base_o) - (dvw - base_w))
            cont += p*(base_o - base_w)
            Wt[ch] = Wt.get(ch, 0.0) + Wt[key]*p*math.exp(-fc*dt_)
        Xg[grp(b)] += Wt[key]*(D - cont - comp); Y += Wt[key]*comp
    X = sum(Xg.values())
    A0 = tm.k_w*(b2 - pick(w, tm.model))
    lk = tm.k_lock if int(tm.k_hold) else 0.0
    cs1, ps1 = lock_delay(tm, lk)
    _locked = ((cs1, ps1) != lock_delay(tm, 0.0)
               or lock_end_step(tm, int(full["n"]), full["dt"], lk) >= 0)
    # 의무보유 물량과 콜 한도가 다르면 묶인 몫과 묶이지 않은 몫(= A0 의 1단위)을 나눠 더한다 (call_mix).
    A = (A0 if not _locked else
         call_mix(tm, lambda: b2 - pick(engine(tm, conv=True, put=True, call=True, conv_start=cs1,
                                               put_start=ps1, lock_m=lk), tm.model),
                  lambda: b2 - pick(w, tm.model)))
    # 참고 — 의무보유가 콜과 «별개» 약정이라면(콜이 없어도 적용) 콜만의 값은 의무보유를 둔 채 콜만 넣고 뺀
    # 차액이다. 앱은 딸린 약정으로 읽는다(4.4.2 · 4.4.3 «콜과 그 부속조항»). 두 읽기의 차이가 이 줄과 A 의 차이다.
    A_sep = (None if not _locked else
             A - lock_share(tm)*(b2 - pick(engine(tm, conv=True, put=True, call=False, conv_start=cs1, put_start=ps1,
                                          lock_m=lk),
                                   tm.model)))
    return {"A": A, "A0": A0, "lock": A - A0, "A_sep": A_sep, "X": tm.k_w*X, "Y": tm.k_w*Y,
            "X_call": tm.k_w*Xg["call"], "X_forced": tm.k_w*Xg["forced"], "X_resp": tm.k_w*Xg["resp"],
            "forced": nf, "called": nc, "model": "GS" if gs else "TF"}


def wow_trace_rows(tr: dict) -> list:
    """wow_trace 를 표 줄로 — [(항목, 값)]. 화면과 두 조서가 같은 줄을 쓴다."""
    if not tr: return []
    disc = ("전환확률로 섞은 할인율(GS)" if tr["model"] == "GS"
            else "주식결제분 무위험 · 현금결제분 위험 할인(TF)")
    rows = [("유무가치비교법 (적용 계약)", tr["A"]),
            ("  관련 약정 효과 — 의무보유 (적용 계약 − 콜만)", tr["lock"]),
            ("  콜만의 차액 (의무보유 없음)", tr["A0"]),
            ("    행사 판정 효과 — 노드마다 달라진 값을 위험이자율로만 할인", tr["X"]),
            (f"      콜이 행사된 노드 ({tr['called']}개)", tr["X_call"]),
            (f"      콜 통지에 투자자가 전환으로 대응한 노드 ({tr['forced']}개)", tr["X_forced"]),
            ("      콜 때문에 투자자가 먼저 전환·조기상환한 노드 (투자자 대응)", tr["X_resp"]),
            (f"    할인 방식 효과 — {disc}", tr["Y"])]
    if tr.get("A_sep") is not None:
        rows.append(("참고 · 의무보유가 콜과 별개 약정이라면 — 의무보유를 둔 채 콜만 넣고 뺀 차액", tr["A_sep"]))
    return rows


def wow_trace_note(tr: dict) -> str:
    """음수일 때 원인 문장. 음수를 0 으로 바꾸지 않고 그대로 쓴다."""
    if not tr: return ""
    out = ("콜이 직접 누른 값(콜 행사 · 강제전환 노드)은 0 이상이고, 투자자 대응 노드는 콜이 계속보유가치를 낮춰 "
           "투자자가 먼저 전환·조기상환해 되찾은 값이라 보통 음수다. 할인 방식 효과는 TF·GS 가 노드 값의 성격에 따라 "
           "할인율을 달리 쓰기 때문에 생긴다. 조각의 합이 적용값이다.")
    if tr.get("A_sep") is not None:
        out += (" 의무보유는 매도청구에 딸린 약정으로 읽는다(한공회 4.4.2 · 4.4.3 «콜과 그 부속조항») — 그래서 적용값에 "
                "의무보유로 투자자가 전환·조기상환을 못 하게 된 효과(관련 약정 효과)가 들어간다. 계약상 의무보유가 콜과 "
                "별개 약정이면 참고 줄이 콜만의 값이고, 의무보유 효과는 전환사채 쪽 평가에 들어가야 한다(이 앱의 범위 밖).")
    neg = [nm for nm, v in (("적용 계약", tr["A"]), ("콜만 · 의무보유 없음", tr["A0"])) if v < 0]
    if neg:
        why = []
        if tr["Y"] < 0:
            why.append("할인 방식 효과 " + f"{tr['Y']:+,.4f}" + " — 콜이 강제전환을 앞당겨 현금결제분(위험 할인)이 "
                       "주식결제분(무위험 할인)으로 바뀌면 노드 값은 줄어도 앞 노드에서 덜 할인되어 평가기준일 가치가 커진다")
        if tr["X_resp"] < 0:
            why.append("투자자 대응 " + f"{tr['X_resp']:+,.4f}" + " — 콜 행사일 앞에서 투자자가 먼저 전환·조기상환해 "
                       "콜이 누를 값을 되찾는다")
        if tr["lock"] < 0:
            why.append("관련 약정 효과 " + f"{tr['lock']:+,.4f}" + " — 의무보유 설정과 계약을 다시 확인하십시오")
        out = ("유무가치비교법 차액이 음수다(" + " · ".join(neg) + ") — 값을 0 으로 바꾸지 않고 그대로 쓴다. 원인: "
               + ("; ".join(why) if why else "행사금액표·행사기간 입력을 확인하십시오")
               + f". 콜이 직접 누른 값은 {tr['X_call'] + tr['X_forced']:+,.4f} 이다. " + out)
    return out


def call_compare_note(tm: Terms) -> str:
    """방법별 비교표 아래에 싣는 적용 범위 — 화면과 두 조서가 같은 문장을 쓴다."""
    out = ("옵션차익 두 방법의 기초자산은 매도청구 조항을 뺀 전환사채를 TF 격자(주식결제분 + 현금결제분)로 "
           "잰 값이다. 세 방법 모두 같은 우선순위·전환 대응·의무보유·만기 규칙을 따른다(같은 날 사건 표).")
    if tm.model == "GS":
        out += (" 이 평가의 전환사채 모형은 GS 라 옵션차익법을 적용할 수 없다 — 표의 옵션차익 값은 같은 계약을 "
                "TF 격자로 잰 참고값이고, 적용값은 유무가치비교법이다.")
    return out


# 방법별 차이 분해의 설명 — 화면과 두 조서가 같은 문장을 쓴다.
CALL_REC_NOTE = ("한공회 4.1.1 — 「유무가치비교법과 옵션차익혼합할인법은 개념적으로 그 결과가 동일하여야 "
                 "하나 세부적인 구현방법에서 … 그 차이가 종종 발생한다」. ①은 두 방법이 같은 계약(의무보유 "
                 "없음)을 잴 때 남는 구현 차이다. ②는 의무보유가 두 방법에 다르게 들어가는 부분이다 — "
                 "유무가치비교법은 콜을 넣고 뺀 차액이라 의무보유로 투자자가 전환·조기상환을 못 하게 된 "
                 "효과까지 값에 들어가고, 옵션차익법은 그 제한 자체를 가치요소로 더하지 않고 콜 대상물량이 "
                 "행사기간 동안 존속해 행사 가능성이 유지되는 효과만 담는다. 의무보유가 없으면 ②는 0 이다. "
                 "① + ② 는 언제나 두 방법의 실제 차이와 같다.")


# 화면·조서 이름. 옵션차익 두 방법은 «콜을 어떻게 할인하는가» 의 이름이다 — 기초 전환사채의
# 평가모형(TF/GS)과 다른 선택이다. 본문 3.2 의 두 갈래(GS 전환가중확률할인 · TF & Hull 지분-채권
# 현금가중할인)를 괄호에 적는다.
K_METHODS = {0: "유무가치비교법",
             1: "옵션차익 혼합할인율법 (콜 가치 하나를 노드별 혼합할인율로 할인 · 본문 3.2 전환가중확률할인)",
             2: "옵션차익 주식결제·현금결제 성분 분리할인법 (본문 3.2 TF & Hull 지분-채권 할인)"}
K_METHODS_SHORT = {0: "유무가치비교법", 1: "옵션차익 · 혼합할인율", 2: "옵션차익 · 성분 분리할인"}
K_SPLITS = {0: "비례균등차감법 — 지분·부채 가치 구성비율 (타 실무서·부속예제)",
            1: "한공회 본문 4.3.3 — GS 전환확률"}
K_HOLDS = {1: "의무보유 있음 — 콜 대상물량이 의무보유 기간 동안 존속",
           0: "의무보유 없음 — 투자자의 전환·조기상환으로 콜도 소멸"}
K_KINDS = {0: "제3자 지정 가능 콜 (발행자 보유 · 파생상품자산)",
           1: "제3자 사전 기특정 콜 (4.5.4 접근법 2-2 · 주주간 분배)"}

# 콜 권리자 — 전환사채·신주인수권부사채 콜의 첫 선택. 계약서의 «발행회사», «발행회사 또는 발행회사가
# 지정하는 자», «○○(최대주주 등)» 문구를 그대로 고른다. 저장은 종전 두 칸(k_third · k_kind)이다.
CALL_HOLDERS = {0: "발행회사 본인만 — 발행자 콜 (내재파생상품)",
                1: "발행회사 또는 발행회사가 지정하는 제3자 — 지정 가능 콜 (별도 금융상품)",
                2: "발행 시 정해진 제3자 — 사전 특정 콜 (4.5.4 접근법 2-2)"}


def call_holder(tm) -> int:
    """콜 권리자 0 발행회사 본인만 / 1 제3자 지정 가능 / 2 제3자 사전 특정. ``tm`` 은 Terms 나 dict."""
    g = (lambda k, d: tm.get(k, d)) if isinstance(tm, dict) else (lambda k, d: getattr(tm, k, d))
    if int(g("k_kind", 0)) == 1: return 2
    return 1 if int(g("k_third", 1)) else 0


def call_alloc_note(tm, whole: bool = False) -> str:
    """회계처리 시트 첫 문단의 매도청구권 문장 — 콜 권리자와 회계처리 설정을 따른다 (두 조서 공통).

    종전에는 콜이 없거나 발행회사만 행사하는 콜에도 «제3자에게 이전될 수 있어 별도의 금융상품» 이라고 적었다.
    """
    if tm.k_w <= 0 or is_sha(tm): return ""
    h, sep = call_holder(tm), int(tm.k_sep) == 1
    if h == 2:
        why = ("발행 시 정해진 제3자의 매도청구권은 이 접근법(한공회 실무사례 4.5.4 접근법 2-2)에서 발행회사가 "
               "옵션 당사자가 아니라고 보아 자산으로 인식하지 않는다")
    elif h == 1:
        why = ("매도청구권은 발행회사가 제3자를 지정할 수 있어 거래상대방이 달라지므로 별도의 금융상품이다 "
               "(제1109호 문단 4.3.1, 회계기준원 질의회신 2022-I-KQA006, 금융위 2022.5.3 감독지침)")
    elif sep:
        why = ("발행회사만 행사하는 매도청구권은 내재파생상품이지만 이 조서는 이용자 설정에 따라 별도 금융상품으로 "
               "처리했다 — 분리 판단 시트의 «판정과 설정 비교» 를 확인한다")
    else:
        why = ("발행회사만 행사하는 매도청구권은 거래상대방이 그대로인 내재파생상품이라 전환권·조기상환권과 하나의 "
               "복합내재파생상품으로 묶는다 (제1109호 문단 B4.3.4)")
    if whole:
        return why + (" — 이 지정 밖에 남는다." if (sep and h != 2) else
                      " — 발행회사의 자산이 아니다." if h == 2 else " — 이 지정 안에 포함된다.")
    return why + ". "


def call_holder_fields(h: int, model: str = "TF") -> dict:
    """콜 권리자를 고르면 함께 정해지는 값 — 저장 칸과 **기본** 평가방법·회계처리.

    발행회사 본인만: 거래상대방이 그대로라 내재파생 (복합내재파생에 포함, 문단 B4.3.4) · 유무가치비교법 (4.3.2).
    제3자(지정 가능 · 사전 특정): 별도 금융상품 (문단 4.3.1) · TF 면 옵션차익 성분 분리할인 + 전환확률 분해.
    평가방법·회계처리는 기본값일 뿐이라 화면에서 바꿀 수 있다 (바꾸면 근거를 남긴다).
    """
    h = int(h)
    third = h in (1, 2)
    return dict(k_third=1 if third else 0, k_kind=1 if h == 2 else 0,
                k_sep=1 if third else 0,
                k_method=(2 if (third and model == "TF") else 0), k_split=1)


def call_method_text(tm: Terms) -> str:
    """조서에 적는 매도청구권 평가방법 문안 — 한공회 연구보고서 시리즈 11 문단을 단다."""
    if tm.k_w <= 0 or is_sha(tm): return "매도청구권 없음"
    kind = ("제3자 사전 기특정 콜 — 본문 4.5 의 세 접근법 중 4.5.4 «접근법 2-2» 를 채택 "
            "(최초 인식 시 주주간 분배). 4.5.2 «접근법 1»(발행자 콜과 같이 유무가치비교법)을 "
            "따르려면 평가방법을 유무가치비교법으로 둘 것; 4.5.3 «접근법 2-1»(이연지정)은 다루지 않음"
            if int(tm.k_kind) == 1 else "제3자 지정 가능 콜 — 접근법 2 제3자 가상지정관점 (4.4.3)")
    if int(tm.k_method) == 0:
        how = "유무가치비교법 — 콜 조항 유무의 가치 비교 (4.3.2), 의무보유·전환억제 효과 포함"
    else:
        how = ("옵션차익혼합할인법 · "
               + ("GS식 전환가중확률할인" if int(tm.k_method) == 1 else "TF식 지분-채권 분리할인")
               + " (3.2 「혼합할인율 이항모형」) — 기초자산은 콜·부속조항을 뺀 전환사채(4.4.2·4.4.3), "
               + "지분·채권 구분 기준은 "
               + ("본문 4.3.3 의 GS 전환확률" if int(tm.k_split) == 1
                  else "지분·부채 가치 구성비율(비례균등차감법 — 타 실무서, 본문에서 도출되는 방식은 아님)")
               + (f"; 콜 대상물량은 의무보유로 {tm.k_lock:,.0f}개월까지 존속한다고 본다"
                  + ("(전환·조기상환청구 모두 제한)" if int(tm.k_lock_put) else "(전환만 제한)")
                  + " — 제한 자체를 별도의 가치요소로 더하지는 않고, 그 제한으로 콜의 행사 "
                    "가능성이 유지되는 효과만 담는다" if int(tm.k_hold) == 1
                  else "; 콜 대상물량에 의무보유가 없어 투자자의 전환·조기상환으로 콜도 소멸한다고 본다")
               + ("; 다만 발행자 매도청구가 우선하는 계약이라 그 자리에서도 콜은 소멸하지 않고 "
                  "행사된다" if int(tm.pc_order) == 1 else ""))
    base = f" · 선택 근거: {tm.k_basis.strip()}" if (tm.k_basis or "").strip() else ""
    # 한공회 본문이 유형별로 「기본적인 접근법」을 지정한다 — 고른 방법이 그것과 같은지 밝힌다.
    # 발행자만 행사하는 콜은 행사하면 사채가 소멸해 기초자산이 남지 않는다 — 복합옵션
    # 구조가 성립하지 않으므로 본문 4.3.2 가 유무가치비교법을 기본으로 둔다.
    std = ("유무가치비교법 (4.3.2)" if (issuer_redeem(tm) or not tm.k_third)
           else "옵션차익혼합할인법 (4.3.4·4.4.3)")
    now = "유무가치비교법" if int(tm.k_method) == 0 else "옵션차익혼합할인법"
    same = (" — 본문 기본 접근법과 같다" if now in std else
            " — **본문 기본 접근법과 다르다.** 4.6.2 대로 선택 근거를 남길 것")
    return (f"{kind} · {how}{base} · 본문 기본 접근법: {std}{same}"
            " — 한공회 『K-IFRS 실무사례와 해설 연구보고서 시리즈 11』 4.6 대로 일관 적용")



def call_method_rows(tm: Terms) -> list:
    """매도청구권 평가방법을 항목별로 나눈다 — [(항목, 내용)]. 한 칸에 여러 가정을 몰지 않는다."""
    if tm.k_w <= 0 or is_sha(tm):
        return [("매도청구권", "없음")]
    km = int(tm.k_method)
    rows = [("콜 유형", "제3자 사전 기특정 콜 — 최초 인식 시 주주간 분배(한공회 실무사례 4.5.4 접근법 2-2)"
             if int(tm.k_kind) == 1 else
             ("발행회사 또는 지정 제3자의 매수청구권 — 제3자 가상지정관점(한공회 실무사례 4.4.3 접근법 2)"
              if tm.k_third else "발행회사의 매수청구권"))]
    if km == 0:
        rows += [("평가방법", "유무가치비교법 — 매도청구 조항이 있을 때와 없을 때의 가치 차이 (한공회 실무사례 4.3.2)"),
                 ("기초자산", "해당 없음 — 매도청구 대상 물량과 비대상 물량을 같은 격자에서 각각 계산"),
                 ("할인·행사가격 배분", "해당 없음 — 각 격자가 선택한 모형(TF/GS)으로 할인")]
    else:
        _wt = ("GS 전환확률(⑪)" if int(tm.k_split) == 1 else "가치 구성비율(⑰ = 주식결제분 ÷ 전환사채 가치)")
        rows += [("평가방법", K_METHODS[km] + " — 한공회 실무사례 3.2 · 4.4.3"),
                 ("할인 방식", ("콜 가치 하나를 다음 시점 두 노드에서 각각 «비중 × 무위험 + (1 − 비중) × 위험» "
                               f"선도이자율로 할인한 뒤 위험중립확률(q, 1 − q)로 가중한다. 비중은 {_wt} 이다 — "
                               "주가 상승확률 q 와 다른 값이다" if km == 1 else
                               "콜 가치를 주식결제 성분과 현금결제 성분으로 나눠 각각 무위험·위험 선도이자율로 "
                               "할인한다. 행사·보유 판단은 두 성분의 합으로 한 번만 한다(성분마다 따로 고르지 않는다)")),
                 ("기초자산", "매도청구 조항을 뺀 전환사채 가치 — TF 격자(주식결제분 + 현금결제분)로 잰다 "
                              "(한공회 실무사례 4.4.2 · 4.4.3). 전환사채 평가모형이 GS 이면 이 방법을 쓸 수 없다 — "
                              "노드의 지분·부채 분해가 필요하다"),
                 ("할인·행사가격 배분", ("매도청구 행사가격을 GS 전환확률로 주식결제·현금결제 성분에 배분 (한공회 실무사례 4.3.3)"
                                    if int(tm.k_split) == 1 else
                                    "매도청구 행사가격을 주식결제분·현금결제분 가치 구성비율로 배분 (비례균등차감법)"))]
    rows.append(("적용 수량", f"발행총액의 {tm.k_w:.0%} (매도청구 한도)"))
    if int(tm.k_hold) == 1 and tm.k_lock > 0:
        rows.append(("전환제한", f"콜 대상 물량은 발행 후 {tm.k_lock:,.0f}개월까지 "
                     + ("전환·조기상환청구가 제한됨" if int(tm.k_lock_put) else "전환이 제한됨")
                     + (" — 유무가치비교법은 그 효과를 가치에 포함" if km == 0 else
                        " — 제한 자체를 별도 가치로 더하지 않고, 콜 행사 가능성이 유지되는 효과만 반영")))
    else:
        rows.append(("전환제한", "없음 — 투자자가 전환·조기상환하면 그 물량의 콜도 소멸"))
    rows += [("같은 날 사건 · " + a, b) for a, b in event_order_rows(tm)]
    std = ("유무가치비교법 (4.3.2)" if (issuer_redeem(tm) or not tm.k_third)
           else "옵션차익법 (4.3.4 · 4.4.3)")
    now = "유무가치비교법" if km == 0 else "옵션차익법"
    rows.append(("한공회 실무사례의 기본 접근법", std + (" — 적용한 방법과 같음" if now in std else
                                                    " — 적용한 방법과 다름. 선택 근거를 남기십시오 (4.6.2)")))
    return rows


def sha_account_lines(tm: Terms, R, has_call=None, gross=None):
    """주주간계약 회계 참고표의 줄 — (항목, 구성) 목록. 구성은 {성분: 계수} 다.

    성분은 네 가지다 — ``eq`` 보유 주식 공정가치 · ``put`` 풋옵션 · ``call`` 콜옵션 ·
    ``gpv`` 풋 행사금액의 현재가치(1032 문단 23 총액 부채). 값 조서는 성분 값을 넣어 숫자로,
    수식 조서는 결과 시트의 성분 칸을 넣어 **수식으로** 만든다 — 엑셀에서 주가·변동성을 바꾸면
    회계 참고표도 따라 움직인다(예전에는 숫자로 굳어 남았다).
    """
    g = R.get("gross") if gross is None else (gross or None)
    w = int(tm.sha_writer)
    if has_call is None:
        has_call = tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
    out = {}
    if w == 0:
        out["발행회사"] = ([("인식할 것이 없다 — 계약 당사자가 아니다", {})],
            "풋 의무자가 대상회사가 아니라 다른 주주이므로 발행회사의 자기지분상품 매입의무가 "
            "아니다. 발행회사 재무제표에는 실리지 않는다. 다만 주주간계약의 "
            "존재와 조건은 특수관계자 거래·우발상황 주석에서 다룰 수 있다.")
    else:
        out["발행회사"] = ([
            ("금융부채 — 자기지분상품 매입의무 (상환금액의 현재가치)", {"gpv": 1}),
            ("자본 차감 (기타자본) — 같은 금액", {"gpv": 1})],
            "기업회계기준서 제1032호 문단 23 — 자기지분상품을 매입해야 하는 의무는 "
            "**옵션 공정가치가 아니라 상환금액의 현재가치**를 총액으로 부채에 싣고 "
            "같은 금액을 자본에서 뺀다. "
            + (f"첫 행사 가능일의 행사금액 {g['strike']:,.4f} 를 그날까지 할인한 "
               f"{g['pv']:,.4f} 이다 (100 기준). " if g else "")
            + "이후 부채는 유효이자율법으로 상환금액까지 늘려 가고, 그 증가액은 "
              "이자비용이다. 풋이 행사되지 않고 소멸하면 부채를 제거하고 자본으로 "
              "되돌린다 (문단 23 후단). 조건부 결제조항이라도 결제 요구가 진성이 "
              "아니거나 발행자 청산 시에만 결제되는 경우가 아니면 금융부채다 "
              "(문단 25)."
            + ("  연대의무를 고르셨습니다 — 하나의 의무를 두 사람이 지는 것이므로 "
               "계약상 1차 의무자 기준으로 **한 곳에서만** 인식하십시오."
               if w == 2 else ""))

    # 금액은 모두 양수로 싣는다 — 자산이든 부채든 그 계정의 금액이다. 순액 줄만 «무엇 − 무엇» 을 이름에 적는다.
    if w in (0, 2):
        rows = [("파생상품부채 — 매도한 풋옵션", {"put": 1})]
        if has_call:
            rows += [("파생상품자산 — 매수한 콜옵션", {"call": 1}),
                     ("순액 = 부채 − 자산 (참고 · 상계 표시 아님)", {"put": 1, "call": -1})]
        note = ("콜 권리자(풋이 행사되면 주식을 사 주는 쪽)에게 대상회사 주식은 **자기지분상품이 "
                "아니므로** 문단 23 이 걸리지 않는다. 매도한 풋은 파생상품부채, 매수한 콜은 "
                "파생상품자산이고 매기 공정가치로 재평가해 당기손익에 반영한다. 상계 요건(제1032호 "
                "문단 42)을 못 채우면 재무상태표에는 총액으로 표시한다.")
    else:
        rows = [("인식할 것이 없다 — 풋 의무자가 발행회사다", {})]
        note = ("풋 의무자를 발행회사로 두셨습니다. 상대 주주가 콜만 가지고 있다면 "
                "그 콜은 파생상품자산입니다.")
        if has_call:
            rows = [("파생상품자산 — 매수한 콜옵션", {"call": 1})]
    out["콜 권리자"] = (rows, note)

    rows = [("지분상품 — 계약 대상 주식 (공정가치 · 보유 주식 전체 아님)", {"eq": 1}),
            ("파생상품자산 — 매수한 풋옵션", {"put": 1})]
    if has_call: rows.append(("파생상품부채 — 매도한 콜옵션", {"call": 1}))
    rows.append(("순액 = 자산 − 부채 (참고)", {"eq": 1, "put": 1, "call": (-1 if has_call else 0)}))
    out["풋 권리자"] = (rows, (
        "지분상품 줄은 이 계약의 풋·콜이 걸린 주식(같은 주식에 붙은 물량은 한 번)만의 공정가치다 — "
        "풋 권리자가 그 밖에 가진 주식은 들어 있지 않다. "
        "풋 권리자(주식 보유자)는 주식과 파생을 따로 인식한다. 풋은 파생상품자산, 매도한 콜은 "
        "파생상품부채이고 둘 다 당기손익-공정가치다. 보유 주식은 지분상품이라 "
        "당기손익-공정가치 또는 (선택 시) 기타포괄손익-공정가치로 잰다 — "
        "제1109호 문단 4.1.4·5.7.5. "
        "주식과 풋이 하나의 거래로 묶여 **실질적으로 원리금 회수**만 남는다면 "
        "전체를 하나의 금융상품(대여금 성격)으로 볼 여지가 있다. 그때는 지분이 "
        "아니라 상각후원가·당기손익-공정가치 금융자산이 되므로, 이 표를 쓰기 전에 "
        "계약의 실질을 먼저 판단하십시오."))
    return out


SHA_PARTIES = ("발행회사", "콜 권리자", "풋 권리자")


def sha_components(tm: Terms, R) -> dict:
    """회계 참고표 성분의 100 기준 값."""
    g = R.get("gross")
    return {"eq": 100*tm.S0/tm.K0, "put": R["put"], "call": R["call"],
            "gpv": (g["pv"] if g else 0.0)}


def sha_components_krw(tm: Terms, R) -> dict:
    """회계 참고표 성분의 원 단위 값 — 풋·지분·총액 부채는 풋 수량, 콜은 콜 수량으로 곱한다."""
    qp, qc = sha_qty(tm)
    c = sha_components(tm, R)
    # 조서와 같은 순서로 곱한다 — (100 기준 ÷ 100 × 기준가격) × 주식수
    f = lambda v, q: v/100*tm.K0*q
    return {"eq": f(c["eq"], sha_contract_shares(tm)), "put": f(c["put"], qp), "call": f(c["call"], qc),
            "gpv": f(c["gpv"], qp)}


def sha_entry_comp_krw(e: dict) -> dict:
    """회차 하나의 회계 성분(원) — 추가 조건부 물량을 미충족 가정으로 뺐으면 0."""
    c = sha_components_krw(e["tm"], e["R"])
    w = float(e.get("incl", 1.0))
    return {k: w*v for k, v in c.items()}


def sha_contract_shares(tm: Terms) -> float:
    """계약 대상 주식수 — 풋·콜 대상 주식을 합하되 같은 주식에 붙은 물량은 한 번만 센다.

    평가 의뢰인이 가진 주식 전체가 아니라 «이 계약이 걸린» 주식이다. 상호소멸이 아니고 같은 주식
    물량을 넣지 않았으면 종전처럼 두 수량 가운데 큰 쪽(콜 대상이 풋 대상에 포함된다고 본다)이다.
    """
    qp, qc = sha_qty(tm)
    # 행사기간이 없는 권리의 수량은 세지 않는다 — 화면에서 권리를 끄고 수량을 남겨 두어도 계약 대상이 아니다.
    _has_put = tm.sha_put_s <= tm.sha_put_e
    _has_call = tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
    if not (_has_put and _has_call):
        return (qp if _has_put else 0.0) + (qc if _has_call else 0.0)
    lq = float(getattr(tm, "sha_link_q", -1.0))
    if int(getattr(tm, "sha_kill", 0)) == 1:
        try:
            lq = sha_link_split(tm)[0]
        except ValueError:
            lq = min(qp, qc)
    elif lq < 0:
        lq = min(qp, qc)
    return qp + qc - min(lq, qp, qc)


def sha_eval(comb: dict, vals: dict) -> float:
    return float(sum(v*vals[k] for k, v in comb.items()))


def sha_formula(comb: dict, refs: dict) -> str:
    """구성 → 엑셀 식. 성분이 없으면 0."""
    if not comb: return "=0"
    parts = []
    for k, v in comb.items():
        if v == 0: continue
        sgn = "-" if v < 0 else "+"
        mag = "" if abs(v) == 1 else f"{abs(v):g}*"
        parts.append(f"{sgn}{mag}{refs[k]}")
    txt = "".join(parts).lstrip("+")
    return "=" + (txt or "0")


def sha_accounts(tm: Terms, R, krw: bool = False):
    """주주간계약을 세 당사자의 재무제표로 옮긴다 — {당사자: ([(항목, 값)], 설명)}.

    같은 계약인데 실리는 것이 완전히 다르다. **누가 풋 의무자인가**가 가른다.

    **발행회사** — 풋 의무자가 아니면 아무것도 실리지 않는다. 발행회사가 풋 의무자면
    자기지분상품을 매입할 의무이므로 기업회계기준서 제1032호 문단 23 이 걸린다 —
    옵션 공정가치가 아니라 **상환금액의 현재가치**를 총액으로 부채에 싣는다.

    **콜 권리자**(풋이 행사되면 주식을 사 주는 쪽) — 자기지분상품이 아니라 남의 주식이므로
    문단 23 이 걸리지 않는다. 매도한 풋은 파생상품부채, 매수한 콜은 파생상품자산이다.

    **풋 권리자**(주식 보유자) — 매수한 풋은 파생상품자산, 매도한 콜은 파생상품부채다.

    ``krw`` 가 참이면 원 단위(풋 수량·콜 수량을 곱한 값), 아니면 100 기준이다.
    """
    vals = sha_components_krw(tm, R) if krw else sha_components(tm, R)
    # 100 기준 순액은 주식수가 같을 때만 뜻이 있다 — 풋·콜·계약 대상 주식수가 다르면 원 단위로만 더한다.
    ok = krw or sha_same_qty(tm)
    return {who: ([(nm, (sha_eval(c, vals) if (ok or not nm.startswith("순액")) else None)) for nm, c in rows], memo)
            for who, (rows, memo) in sha_account_lines(tm, R).items()}


def sha_same_qty(tm: Terms) -> bool:
    """풋 · 콜 · 계약 대상 주식수가 모두 같은가 — 100 기준 값을 그대로 더해도 되는가."""
    qp, qc = sha_qty(tm)
    has_call = tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e
    if not has_call: qc = qp
    return abs(qp - qc) < 1e-9 and abs(sha_contract_shares(tm) - qp) < 1e-9


def sha_validate(tm: Terms):
    """주주간계약 인풋에서 계약과 어긋나는 것을 잡는다."""
    w = []
    if tm.sha_rows:
        # 회차별 표 — 계산을 막는 입력은 sha_row_issues(평가 입력 오류)가 잡는다. 여기서는 확인할 점만.
        rows = [sha_row_defaults(r) for r in tm.sha_rows]
        if any(r["put_q"] > 0 and r["call_q"] > 0 and not r["kill"] for r in rows):
            w.append("한쪽 행사 뒤에도 상대 권리가 **남는** 계약으로 잽니다. 같은 주식에 붙은 풋·콜이고 한쪽 행사로 상대 "
                     "권리가 끝나는 계약이면 그 회차의 «한쪽 행사 시 상대 권리 소멸» 을 켜고 같은 주식 물량을 넣으십시오 — "
                     "계약서의 소멸·우선순위 조항을 확인하십시오.")
        if len({(r["start"], r["end"]) for r in rows}) < len(rows):
            w.append("행사기간이 같은 회차가 둘 이상입니다. 같은 물량을 두 번 넣지 않았는지 확인하십시오.")
        if int(tm.sha_writer) in (1, 2):
            w.append("풋 행사 시 주식매수 의무자를 **발행회사**로 두셨습니다. 발행회사 재무제표에는 옵션 공정가치가 "
                     "아니라 **상환금액의 현재가치를 총액으로** 싣습니다 (1032 문단 23).")
        if int(tm.sha_disc) == 0 and not all(r["pdisc"] is not None for r in rows):
            w.append("풋을 **무위험**으로 할인하는 회차가 있습니다. 풋은 현금을 받을 권리라 의무자의 신용위험이 "
                     "붙습니다 — 의무자의 신용을 반영했는지 확인하십시오.")
        if any(r["price_note"] for r in rows):
            w.append("실적 연동 행사가격을 추정 재무수치로 계산해 **고정**했습니다 — 미래 실적의 불확실성은 "
                     "반영하지 않았습니다. 산식의 재무제표 연도(보통 행사연도의 직전 연도)를 확인하십시오.")
        return w
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
                 "의무자의 신용위험이 붙습니다 — 상대 주주(개인·회사)가 의무자면 그 신용을 "
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
    EA = exercise_amounts(tm, n, dt_)
    red = EA["red"]
    cpn_amt = 100*eff_cpn(tm)*tm.ipay/12
    _pays = pay_steps(tm, n, dt_)
    is_pay = lambda i: i in _pays
    cpn_at = lambda i: cpn_amt*_pays.get(i, 0)
    # 행사일은 주가 격자와 같은 목록에서 온다 (exercise_amounts).
    in_put = EA["p_on"]
    put_a = lambda i: (EA["put"](i) if in_put(i) else 0.0)
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
                is_pay=is_pay, cpn_at=cpn_at, in_put=in_put, put_a=put_a, Q=Q, mkt=mkt,
                base_nm=("위험 곡선" if tm.bdt_base == 0 else "무위험 곡선"))


def bdt_grid(tm: Terms, put: bool):
    """BDT 격자에서 사채를 역진하고 전 노드 값을 돌려준다.

    만기 노드와 중간 노드의 판정을 격자 엔진과 같은 순서로 맞춘다 —
    만기는 MAX(조기상환금액, 만기상환금액) + 쿠폰, 중간은 MAX(계속보유,
    조기상환금액) 이다. 조서가 표를 그릴 때 이 격자를 그대로 쓴다.
    """
    B = bdt_parts(tm)
    n, dt_ = B["n"], B["dt"]
    cm = B["cpn_at"](n)
    V = [[0.0]*(i+1) for i in range(n+1)]
    for j in range(n+1):
        V[n][j] = max(B["put_a"](n) if put else 0.0, B["red"]) + cm
    # 행사일 이자 별도지급 — 주가 격자(engine)와 같은 스위치다. 그날이 지급일이면
    # 조기상환금액 위에 그날 이자를 얹어 계속보유(이자 포함)와 견준다.
    _add = int(getattr(tm, "p_cpn_add", 0))
    for i in range(n-1, -1, -1):
        c = B["cpn_at"](i)
        for j in range(i+1):
            d = math.exp(-(B["r"][i][j] + B["add"][i])*dt_)
            h = (0.5*V[i+1][j+1] + 0.5*V[i+1][j])*d + c
            if put and B["in_put"](i):
                pv = B["put_a"](i)
                h = max(h, pv + (c if (_add and pv > 0) else 0.0))
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
    return bool(tm.put_bdt) and put_bdt_avail(tm)


def put_bdt_avail(tm: Terms) -> bool:
    """BDT 를 **켤 수 있는 자리인가** (켜져 있는지와 별개다).

    같은 조건이 사이드바·검토 화면·put_bdt_on 세 군데에 손으로 적혀 있었다. 그래서 화면이
    사이드바의 잠금을 모르고 「BDT 를 켜십시오」라고 권했다 — 켤 수 없는 자리에서.
    """
    return (tm.conv_class == "equity" and tm.model == "TF" and tm.p_s <= tm.p_e)


def put_bdt_block(tm: Terms) -> str:
    """켤 수 없는 이유. 켤 수 있으면 빈 문자열이다."""
    if tm.p_s > tm.p_e: return "조기상환청구권이 없다"
    if tm.conv_class != "equity": return "전환권이 파생상품부채다"
    if tm.model != "TF": return "신용위험 처리가 GS 다"
    return ""


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
        _tf = Terms(**asdict(tm)); _tf.k_hold = 0
        ca = call_mix(tm, lambda: call_third_party(tm, full, tm.k_method),
                      lambda: call_third_party(_tf, full, tm.k_method))
    else:
        # 의무보유는 전환과 조기상환청구를 함께 늦춘다. 시작보다 이르면 아무 제약이 아니다.
        def _unit(lk):
            cs, ps = lock_delay(tm, lk)
            return b2 - pick(engine(tm, conv=True, put=True, call=True, conv_start=cs,
                                    put_start=ps, lock_m=lk), tm.model)
        ca = call_mix(tm, lambda: _unit(tm.k_lock), lambda: _unit(0.0))
    # RCPS 의 발행자 상환권은 자본요소가 아닌 파생이라 **부채요소 안에서** 잰다
    # (1032 문단 31·32 — 비자본 파생 특성은 부채요소 장부금액에 포함). 전체 격자에서
    # 잰 콜(ca)은 전환 상승분을 자르는 값이라 부채에서 빼면 부채가 과소, 자본이
    # 과대가 되고, 풋보다 커지면 복합내재파생이 음수가 되기도 한다. 그래서 전환권을
    # 자본으로 볼 때의 배분에는 부채 격자(전환권 없음)에서 잰 콜을 쓴다. 전환권이
    # 부채면 전환권·풋·콜을 전체 격자에서 묶어 재므로 ca 그대로다.
    if issuer_redeem(tm) and has_call:
        _b1p = pick(engine(tm, conv=False, put=True, call=False), tm.model)
        _b1c = pick(engine(tm, conv=False, put=True, call=True), tm.model)
        # 전환권이 없는 격자라 지분 몫이 없고 콜은 부채를 누르기만 한다 — 차액은 0 이상이다. max 는 부동소수
        # 잡음만 걷는다(유무가치 음수를 덮는 자리가 아니다 — 적용 콜 값 ca 는 음수여도 그대로 쓴다).
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


# 분리 판단에서 "행사가격이 상각후원가와 거의 같다" 로 볼 문턱의 기본값.
# 기준서는 "거의 같다" 라고만 하고 수치를 주지 않는다 — 기준서가 정한 수치가 아니라
# 실무에서 쓰는 10% 를 기본으로 두고, 평가자가 split_tol 로 바꾼다.
SPLIT_TOL = 0.10


def split_tol(tm: "Terms | None" = None) -> float:
    """평가자가 정한 비교기준 (기본 10%)."""
    v = float(getattr(tm, "split_tol", SPLIT_TOL)) if tm is not None else SPLIT_TOL
    return v if v > 0 else SPLIT_TOL


def _close_test(strike, amort, tol=SPLIT_TOL):
    """행사가격과 상각후원가가 '거의 같은가' — 문단 B4.3.5(5)(가)."""
    gap = abs(strike - amort)/max(abs(amort), 1e-9)
    return gap, gap <= tol


def split_call_separate(tm: Terms) -> bool:
    """매도청구권이 전환사채와 **별개의 금융상품** 으로 발행회사가 보유하는가.

    제3자 지정·독립 양도가 가능하면 발행회사가 파생상품자산으로 따로 인식한다
    (1109 문단 4.3.1 마지막 문장). 발행 시 제3자가 이미 정해진 콜(k_kind=1)은
    발행회사가 옵션 당사자가 아니므로 해당하지 않는다.
    """
    return (tm.k_w > 0 and (bool(tm.k_third) or bool(tm.k_transfer))
            and int(getattr(tm, "k_kind", 0)) == 0 and not issuer_redeem(tm))


def split_base(tm: Terms, ca: float) -> float:
    """분리 판단용 상각후원가의 출발점 — **자본요소를 분리하기 전** 주계약.

    1109 문단 B4.3.5(5) 말미: 전환채무상품의 자본요소를 분리하기 «전에» 내재 콜·풋이
    주계약과 밀접하게 관련되어 있는지 판단한다. 한공회 실무사례 28쪽 각주는 이 금액이
    실제 회계처리(자본요소를 뗀 뒤)의 상각후원가와 다르다고 짚고, 159쪽 사례는 순발행가액
    가운데 **전환사채에 배분된 거래가격** 에서 출발한다.

    발행금액 100 에서 출발하되, 발행회사가 별개의 콜옵션을 함께 사들였다면(제3자 지정
    가능 콜) 그 대가만큼 전환사채에 더 배분된다 — 159쪽의 CU1,200 이 그 경우다.
    실제 회계상 배분액이 다르면 평가자가 split_base_in 으로 넣는다 (0 이하면 자동값).
    """
    if float(getattr(tm, "split_base_in", -1.0)) > 0:
        return float(tm.split_base_in)
    return split_base_auto(tm, ca)


def split_base_auto(tm: Terms, ca: float) -> float:
    """앱이 정하는 출발 금액 — 발행금액 100 (+ 발행회사가 함께 산 별개 콜의 가치)."""
    return 100.0 + (float(ca) if split_call_separate(tm) else 0.0)


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

    # 문단 B4.3.5(5)(가) 의 상각후원가는 **자본요소를 분리하기 전** 주계약의 것이다
    # (문단 B4.3.5(5) 말미). 전환사채에 배분된 거래가격(split_base)에서 출발해
    # 계약만기까지 굴린 상각표를 쓴다. 옵션을 모두 뗀 사채(B0)에서 출발하면 자본요소를
    # 뗀 «뒤» 의 금액이라 행사금액과의 차이가 부풀어 분리 쪽으로 기운다.
    # 실제로 인식한 배분액(rows_eir)도 쓰지 않는다 — 분리 여부 설정에 따라 출발점이
    # 바뀌면 「분리하지 않기로 했더니 가까워져 분리하지 않아도 된다」는 순환이 생긴다.
    base0 = split_base(tm, ca)
    tol = split_tol(tm)
    _base_by = ("평가자 입력" + (f" — {tm.split_base_why.strip()}" if str(tm.split_base_why).strip() else "")
                if float(getattr(tm, "split_base_in", -1.0)) > 0 else
                ("앱 자동값 — 발행금액 100 + 발행회사가 함께 산 별개 콜의 가치"
                 if split_call_separate(tm) else "앱 자동값 — 발행금액 100"))
    try:
        _rows_host = eir_table(tm, base0)[1]
    except Exception:
        _rows_host = rows_eir

    def amort_at(t_year):
        """그 시점 분리 판단용 상각후원가. 분리 여부 설정과 무관하다."""
        return next((en for _, tt_, _b, _i, _c, en in _rows_host
                     if tt_ >= t_year - 1e-9), base0)
    _EA = exercise_amounts(tm, n, dt_)

    out = {}

    # ── 조기상환청구권 ────────────────────────────────────────
    if tm.p_s > tm.p_e or tm.T <= 0:
        out["put"] = dict(있음=False, 결론="해당 없음",
                          이유=["계약에 조기상환청구권이 없습니다."],
                          근거=[], 평가="—", 지표={})
    else:
        # 행사일마다 견준다 — 「매 상환권 행사시점의 행사금액이 그 시점의 상각후원가와
        # 거의 같을 정도」여야 비분리다 (실무사례 28쪽). 첫 행사일 하나만 보면 뒤로 갈수록
        # 벌어지는 계약을 놓친다.
        _pm = sorted(_EA["p_dates"].values()) or [tm.p_s]
        _chk = []
        for _m in _pm:
            _pv = _EA["put_at_month"](_m)
            _bv = amort_at(max(0.0, (_m - tm.elapsed_m)/12))
            _chk.append((_m, _pv, _bv, _close_test(_pv, _bv)[0]))
        _, pv, bv, gap = _chk[0]
        _worst = max(_chk, key=lambda x: x[3])
        close = _worst[3] <= tol
        _wtxt = (f" 행사일 {len(_chk)}회 가운데 차이가 가장 큰 회차는 발행 후 "
                 f"{_worst[0]:g}개월({_worst[3]*100:.1f}%)입니다." if len(_chk) > 1 else "")
        # 비교 금액의 출발점 — 전환권이 자본이면 기준서가 정한다(자본요소 분리 전). 부채면
        # 명문 규정이 없어 그 규정을 회계정책으로 준용한다(접근법 2 · 실무사례 32쪽).
        _from = ("전환권을 떼기 전 금액" if liab else "자본요소 분리 전 금액")
        _cmp = (f"첫 조기상환일 행사금액 {pv:,.2f} 와 같은 시점 상각후원가 "
                f"{bv:,.2f}({_from} {base0:,.2f} 에서 출발) 의 차이가 {gap*100:.1f}% 입니다.{_wtxt}")
        # 접근법 1 이면 얽힌 권리를 먼저 묶는다 — 전환권이 부채이거나 발행회사만 행사하는
        # 매도청구권이 내재파생이면 조기상환권은 따로 판단하지 않는다.
        _bund = emb_policy(tm) == 1 and (liab or (int(tm.k_sep) == 0 and tm.k_w > 0))
        why, cite = [], []
        if tm.fvpl_whole:
            res = "분리하지 않음"
            why.append("복합계약 전체를 당기손익-공정가치로 측정하므로 분리 요건이 "
                       "성립하지 않습니다.")
            cite.append("1109 문단 4.3.3(3)")
        elif _bund and liab:
            res = "묶어서 분리"
            why.append("분리 정책 접근법 1(서로 얽힌 권리를 먼저 묶고 판단 — 한공회 실무사례 "
                       "30~31쪽)을 적용합니다. 전환권이 파생상품부채이므로 조기상환권을 따로 "
                       "판단하지 않고 전환권과 하나의 복합내재파생상품으로 묶습니다. 묶음에는 "
                       "주가위험이 들어 있어 주계약과 밀접하지 않으므로 전체로서 분리합니다. "
                       "전환하거나 상환받거나 둘 중 하나라 서로 배타적이어서, 따로 재어 더하면 "
                       "총액이 부풀려집니다.")
            cite += ["1109 문단 B4.3.4", "실무사례 30~31쪽"]
        elif _bund:
            res = "묶어서 분리"
            why.append("분리 정책 접근법 1(한공회 실무사례 30~31쪽)을 적용합니다. 발행회사만 "
                       "행사하는 매도청구권이 내재파생이라 조기상환권과 먼저 하나로 묶어 봅니다 "
                       "(B4.3.4). 이 앱은 그 묶음을 주계약에서 분리한 것으로 계산합니다. "
                       f"(참고) 조기상환권만 보면 — {_cmp}")
            cite += ["1109 문단 B4.3.4", "실무사례 30~31쪽"]
        elif tm.p_lost_int:
            res = "분리하지 않음"
            why.append("행사가격이 잔여기간 상실이자의 현재가치를 보상하는 "
                       "수준이므로 주계약과 밀접하게 관련되어 있습니다.")
            cite.append("1109 문단 B4.3.5(5)(나)")
        elif close:
            res = "분리하지 않을 여지"
            why.append(f"{_cmp} 모든 행사일에서 이용자 설정 비교기준({tol*100:g}%) 이내이므로 "
                       "밀접하게 관련되어 있다고 볼 여지가 있습니다. 비교기준은 기준서가 정한 "
                       "수치가 아닙니다 (실무사례 28쪽).")
            cite.append("1109 문단 B4.3.5(5)(가)")
        else:
            res = "분리"
            why.append(f"{_cmp} 이용자 설정 비교기준({tol*100:g}%)을 초과함 — 기준을 넘는 행사일이 있습니다. "
                       "비교기준은 기준서가 정한 수치가 아닙니다 (실무사례 28쪽).")
            why.append("같은 조건의 별도 금융상품이 파생상품의 정의를 충족하고, "
                       "복합계약 전체를 당기손익-공정가치로 측정하지 않습니다.")
            cite += ["1109 문단 B4.3.5(5)", "문단 4.3.3"]
        if emb_policy(tm) == 2 and not tm.fvpl_whole and (liab or int(tm.k_sep) == 0):
            # 접근법 2 — 권리마다 따로 판단한다. 부채 분류의 비교 금액은 준용이라 밝힌다.
            why.insert(0, "분리 정책 접근법 2(권리마다 분리 여부를 판단한 뒤 분리 대상끼리 묶기 — "
                          "한공회 실무사례 30~32쪽)를 적용해 조기상환권을 따로 판단합니다."
                          + (" 전환권이 파생상품부채일 때 비교할 상각후원가는 기준서가 정하지 않아, "
                             "전환권이 자본일 때의 규정(자본요소를 분리하기 전 금액에서 출발 — B4.3.5(5) "
                             "말미)을 회계정책으로 준용합니다 (실무사례 32쪽)." if liab else ""))
            cite.append("실무사례 30~32쪽")
            if res == "분리" and liab:
                why.append("분리 대상이므로 전환권과 얽힌 두 권리를 하나의 복합내재파생상품으로 "
                           "묶어 측정합니다 (B4.3.4).")
        val = ("묶음 전체를 공정가치로 측정합니다 (B2 − B0)." if (res == "묶어서 분리" or (res == "분리" and liab))
               else "순차 차감 — 조기상환권만 얹은 값에서 옵션 없는 사채를 뺍니다 "
               f"(B1 − B0 = {b1-b0:,.4f}). 전환권과 대체 관계라 따로 재어 "
               "더하면 총액이 부풀려집니다."
               if res == "분리" else
               "분리하지 않으므로 주계약에 포함해 상각후원가로 측정합니다."
               + (f" 전환권만 파생상품부채로 둡니다 (B2 − B1 = {b2-b1:,.4f})." if liab else ""))
        out["put"] = dict(있음=True, 결론=res, 이유=why, 근거=cite, 평가=val,
                          지표={"첫 조기상환일 행사금액": pv,
                                "같은 시점 상각후원가": bv,
                                "차이": gap,
                                "상각 출발 금액 (자본요소 분리 전)": base0,
                                "가장 큰 차이": _worst[3],
                                "가장 큰 차이 · 발행 후 개월": _worst[0],
                                "비교기준 (회계정책)": tol,
                                "출발 금액 근거": _base_by},
                          회차=_chk)

    # ── 매도청구권 ────────────────────────────────────────────
    ks = full["kstrike"]
    first_k = next((i for i in range(n+1) if ks(i) is not None), None)
    if tm.k_w <= 0 or first_k is None:
        out["call"] = dict(있음=False, 결론="해당 없음",
                           이유=["계약에 매도청구권이 없거나 행사 가능한 시점이 "
                                 "없습니다."], 근거=[], 평가="—", 지표={})
    else:
        kv = ks(first_k)
        kb = amort_at(max(0.0, (_EA["k_dates"].get(first_k, tm.elapsed_m + first_k*dt_*12)
                                - tm.elapsed_m)/12))
        kgap, kclose = _close_test(kv, kb, tol)
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
        elif liab and emb_policy(tm) == 1:
            res = "묶어서 분리"
            why.append("분리 정책 접근법 1(한공회 실무사례 30~31쪽)을 적용합니다. 발행회사만 "
                       "행사할 수 있어 거래상대방이 그대로이므로 내재파생"
                       "상품이고, 전환권이 파생상품부채이므로 전환권·조기상환권과 "
                       "하나의 복합내재파생상품으로 묶어 전체로서 측정합니다. "
                       "세 권리는 상호배타적·상호의존적입니다 — 전환하거나 상환받거나 "
                       "매도청구를 당하거나 셋 중 하나로만 끝나므로, 따로 재어 더하면 "
                       "일어날 수 없는 조합까지 값에 넣게 됩니다.")
            cite += ["1109 문단 B4.3.4", "실무사례 30~31쪽"]
        elif kclose:
            res = "분리하지 않을 여지"
            why.append(f"첫 매도청구일 매매대금 {kv:,.2f} 와 같은 시점 주계약 "
                       f"상각후원가 {kb:,.2f} 의 차이가 {kgap*100:.1f}% 로 "
                       "거의 같습니다. 다만 이 앱은 발행회사만 행사하는 매도청구권을 주계약에 "
                       "남기는 처리를 지원하지 않아 분리한 것으로 계산합니다 — 주계약에 둔다고 "
                       "판단하면 그 차이를 조서에 따로 적으십시오.")
            cite.append("1109 문단 B4.3.5(5)(가)")
        else:
            res = "분리"
            why.append(f"발행회사만 행사할 수 있어 내재파생상품이고, 첫 매도청구일 "
                       f"매매대금 {kv:,.2f} 와 같은 시점 주계약 상각후원가 "
                       f"{kb:,.2f} 의 차이가 {kgap*100:.1f}% 로 거의 같지 "
                       "않습니다.")
            cite += ["1109 문단 4.3.1", "문단 B4.3.5(5)"]
        if (emb_policy(tm) == 2 and not (tm.k_third or tm.k_transfer) and not tm.fvpl_whole
                and res in ("분리", "분리하지 않을 여지")):
            why.insert(0, "분리 정책 접근법 2(권리마다 판단한 뒤 분리 대상끼리 묶기 — 한공회 실무사례 "
                          "30~32쪽)에 따라 매도청구권을 따로 판단합니다.")
            cite.append("실무사례 30~32쪽")
            if liab:
                why.append("분리 대상이면 전환권과 얽혀 있어(매도청구에 전환으로 대응한다) 하나의 "
                           "복합내재파생상품으로 묶습니다 (B4.3.4).")
        if res == "별도의 금융상품" and int(getattr(tm, "k_kind", 0)) == 1:
            # 발행 시 제3자가 이미 정해져 있으면 발행자가 콜을 보유하지 않는다.
            val = ("발행 시 제3자가 특정되어 있습니다. **이 앱이 채택한 본문 4.5.4 접근법 2-2** "
                   "에서는 발행회사가 옵션 당사자가 아니라고 보아, 값은 지정 가능 콜과 같은 "
                   "격자에서 나오되 발행회사 측면에서는 금융상품이 아니라 **주주간 분배**이므로 "
                   "파생상품자산을 인식하지 않고 최초 인식 시 그 가치를 측정해 둡니다. "
                   "회계처리 탭의 배분표에 합계 밖 참고 줄로 실립니다.\n\n" + KKIND_CHOICE)
        elif res == "별도의 금융상품":
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
            why.append("신주인수권 행사 시 사채로 대용납입하여 사채가 소멸하므로, "
                       "전환사채의 전환과 같은 계산 구조를 적용합니다.")
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
    _live = psep_free(tm)
    _res = out["put"]["결론"]
    if not out["put"]["있음"] or not _live or _res == "분리하지 않을 여지":
        # 「여지」는 어느 쪽으로도 갈 수 있다. 어긋났다고 하지 않는다.
        out["put"]["설정일치"] = True
    else:
        out["put"]["설정일치"] = (int(tm.p_sep) ==
                                (1 if _res in ("분리", "묶어서 분리") else 0))
    out["put"]["스위치"] = _live
    # 후속 평가일 — 분리 여부는 최초로 계약당사자가 된 날 한 번 판단하고, 계약조건이 바뀌어
    # 현금흐름이 유의적으로 수정되지 않는 한 다시 판단하지 않는다 (1109 문단 B4.3.11). 이
    # 평가의 숫자(평가기준일 기준)로 다시 판정하면 결론이 흔들려 설정을 바꾸라는 신호가 되므로,
    # 판정 대신 최초 결론(설정)을 이어 적용한다고 적는다.
    if tm.elapsed_m > 0.01 and not tm.fvpl_whole:
        for key, nm in (("put", "조기상환청구권"), ("call", "매도청구권")):
            d = out.get(key)
            if not d or not d.get("있음") or d["결론"] == "별도의 금융상품":
                continue
            _set = (("주계약에 포함 (분리하지 않음)" if put_in_host(tm) else "분리")
                    if key == "put" else "분리")
            d.update(결론="최초 인식 판단 이어 적용",
                     이유=[B4311_NOTE + f" 이 평가는 {nm}을 «{_set}» 으로 이어 적용합니다."],
                     근거=["1109 문단 B4.3.11", "실무사례 32쪽"], 지표={}, 설정일치=True)
            d.pop("회차", None)
    return out


# 기대만기(첫 조기상환 가능일)로 상각할 때 그날 행사되지 않으면 — 세 화면·조서가 같은 문장을 쓴다.
EXPECT_B546 = ("첫 조기상환일에 행사되지 않으면 남은 현금흐름(다음 조기상환일 또는 만기)을 다시 추정해 최초 "
               "유효이자율로 할인한 금액으로 장부금액을 조정하고, 그 차이를 당기손익으로 인식한다 (1109 문단 B5.4.6). "
               "이 상각표는 첫 조기상환일까지만 보여 준다 — 그 뒤의 조정은 결산 평가에서 따로 한다.")


# 분리 판단의 «검토용 수치» 는 판단에만 쓴다 — 분개·상각표의 장부금액이 아니다.
SPLIT_NUM_NOTE = ("아래 금액은 분리 여부를 판단하려고 잰 값이다. 분개와 상각표의 장부금액(「회계처리」 "
                  "시트의 최초 장부금액)과 다르다.")


def split_policy_rows(tm: "Terms", key: str) -> list:
    """분리 판단 한 권리의 «적용 회계정책» 과 «선택한 처리» — [(항목, 내용)]. 두 조서·화면 공통.

    결론(판정)은 앱이 계약과 수치로 낸 초안이고, 선택한 처리는 이 조서의 회계처리 설정이다.
    둘이 다르면 시트 아래 «판정과 회계처리 설정이 맞는가» 에 적힌다.
    """
    pol = (("접근법 1 — 얽힌 권리를 먼저 묶고 판단" if emb_policy(tm) == 1 else
            "접근법 2 — 권리마다 판단한 뒤 분리 대상끼리 묶기") + " (실무사례 30~32쪽)")
    if key == "put":
        pol += f" · 비교기준 {split_tol(tm)*100:g}% (회계정책 — 기준서가 정한 수치가 아님)"
    if fvpl_on(tm):
        cho = "복합계약 전체를 당기손익-공정가치로 지정 — 분리하지 않음"
    elif key == "put":
        if put_in_host(tm):
            cho = "주계약에 포함 (분리하지 않음) — 상각후원가로 측정"
        elif tm.conv_class != "equity":
            cho = "전환권과 묶어 분리 — 복합내재파생상품 (파생상품부채)"
        elif int(tm.k_sep) == 0 and tm.k_w > 0:
            cho = "매도청구권과 묶어 분리 — 복합내재파생상품"
        else:
            cho = "분리 — 파생상품부채"
    else:
        cho = ("별도 금융상품 — 파생상품자산으로 따로 인식" if int(tm.k_sep) else
               "복합내재파생상품에 포함")
    return [("적용 회계정책", pol), ("선택한 처리 (이 조서의 회계처리)", cho)]


def split_compare(tm: "Terms", key: str, d: dict) -> str:
    """분리 판단 한 권리의 «수치 판정 · 이용자 설정 · 일치 여부» 한 줄. 두 조서 공통.

    「분리하지 않을 여지」 는 수치상 주계약과 밀접하다고 볼 여지가 있다는 뜻이다. 그때 분리를
    골랐으면 어긋났다고 단정하지 않되 «검토 필요» 로 적는다 — 밀접한 내재파생은 분리하지 않는다.
    """
    res = d.get("결론", "")
    cho = dict(split_policy_rows(tm, key))["선택한 처리 (이 조서의 회계처리)"]
    if fvpl_on(tm) or res == "최초 인식 판단 이어 적용":
        ok = "해당 없음 — " + ("복합계약 전체 지정" if fvpl_on(tm) else "최초 인식 때의 결론을 이어 적용")
    elif key == "put":
        host = put_in_host(tm)
        if res in ("분리", "묶어서 분리"):
            ok = "검토 필요 — 수치 판정과 이용자 설정이 다름" if host else "일치"
        elif res == "분리하지 않음":
            ok = "일치" if host else "검토 필요 — 수치 판정과 이용자 설정이 다름"
        else:
            ok = "일치" if host else "검토 필요 — 수치상 분리하지 않을 여지가 있으나 분리를 선택함"
    elif res == "분리하지 않을 여지":
        # 매도청구권은 어느 설정(별도 금융상품 · 복합내재파생에 포함)이든 분리해 처리한다 — 주계약에 남기는
        # 처리를 지원하지 않으므로 수치 판정이 회계처리에 반영되지 않았다고 적는다.
        ok = "검토 필요 — 수치상 분리하지 않을 여지가 있으나 이 조서는 분리해 처리함 (주계약에 남기는 처리는 지원하지 않음)"
    else:
        ok = "일치" if d.get("설정일치", True) else "검토 필요 — 수치 판정과 이용자 설정이 다름"
    return f"수치 판정 «{res}» · 이용자 설정 «{cho}» → {ok}"


# 분리 판단 «행사일별 비교표» 의 열 — 화면·값 조서·수식 조서가 같은 머리를 쓴다.
SPLIT_DATE_COLS = ["발행 후 개월", "행사 시점 (년)", "행사금액", "상각후원가", "차이"]

B4311_NOTE = ("분리 여부는 최초로 계약당사자가 된 날 판단하고, 계약조건이 바뀌어 현금흐름이 유의적으로 "
              "수정되지 않는 한 다시 판단하지 않습니다 (1109 문단 B4.3.11). 평가기준일이 발행일보다 "
              "뒤이므로 이 평가의 숫자로 다시 판정하지 않고 최초 인식 때의 결론을 이어 적용합니다 — "
              "최초 인식 조서의 분리 판단을 함께 보관하십시오.")


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


RIGHT_COLS = ["권리", "권리자", "행사기간", "행사조건", "행사 시 지급액", "우선순위"]


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
        rows.append(("풋옵션", "풋 권리자 (주식 보유자)",
                     _mo(tm.sha_put_s, tm.sha_put_e, tm.sha_put_f),
                     ("적격상장하면 소멸" if int(tm.ipo_on) else "—"),
                     f"MAX(행사금액 − 지분가치, 0) · 첫날 {_pk:,.2f}",
                     "—"))
        if tm.sha_call_e > 0 and tm.sha_call_s <= tm.sha_call_e:
            rows.append(("콜옵션", "콜 권리자 (상대방)",
                         _mo(tm.sha_call_s, tm.sha_call_e, tm.sha_call_f),
                         ("적격상장하면 소멸" if (int(tm.ipo_on)
                                            and int(tm.sha_qipo_kill)) else "—"),
                         "MAX(지분가치 − 행사금액, 0)", "—"))
        rows.append(("풋 행사 시 주식매수 의무자", ["콜 권리자 (상대 주주)", "발행회사", "상대 주주 · 발행회사 연대"]
                     [int(tm.sha_writer)], "—", "—",
                     ("상환금액의 현재가치를 총액으로 (1032 문단 23)"
                      if int(tm.sha_writer) else "옵션 공정가치"), "—"))
        return rows

    # 전환권 (BW 는 신주인수권)
    _rfx = (["조정 없음", "하향만 조정", "하향+상향 조정"][int(tm.rfx_mode)]
            + (f" · {rfx_cycle_text(tm)} · 하한 {max(tm.floor, tm.par):,.0f}원"
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
        _amt = ("100 × (1 + 보장수익률 복리)" + ded_suffix(tm, "p") if tm.p_mode == "accrue"
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
                     "100 × (1 + 프리미엄 복리)" + ded_suffix(tm, "k"),
                     ("동시 행사 시 발행자 매도청구 우선" if int(tm.pc_order) == 1 else "동시 행사 시 투자자 상환청구권 우선")))
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
    # 제3자 사전 기특정 콜 — 발행 시 제3자가 이미 정해져 있어 발행자가 콜을 보유하지
    # 않는다. 발행자 측면에서는 금융상품이 아니라 **주주간 분배**이므로 (본문 4.5.1)
    # 파생상품자산을 세우지 않고, 받은 현금 100 을 복합금융상품 요소에 전부 배분한다.
    # 콜의 공정가치는 유형과 무관하게 같으므로 참고 줄로 합계 밖에 적는다.
    kk = int(getattr(tm, "k_kind", 0)) == 1
    # 조기상환권을 분리하지 않는 선택이 살아 있는지는 분리 정책이 정한다 (put_in_host).
    # 접근법 1 이면 전환권이 부채이거나 매도청구권을 내재파생으로 묶을 때 조기상환권이
    # 그 묶음에 딸려 분리된다(문단 B4.3.4). 접근법 2 면 조기상환권을 따로 판단한다.
    psep = not put_in_host(tm)
    # 자본 갈래에서 부채요소를 줄이는 콜 — RCPS 는 부채 격자에서 잰 값 (문단 31)
    cad = full.get("ca_debt", ca) if is_rcps(tm) else ca
    _ca, _cad = (0.0, 0.0) if kk else (ca, cad)     # 배분에 실제로 들어가는 금액
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
        whole = (100 + _ca) if sep else 100.0
        rows = [("복합계약 전체 · 당기손익-공정가치 측정 금융부채", whole)]
        if sep and not kk and (abs(ca) > 1e-12 or not is_rcps(tm)):
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
    elif tm.conv_class == "liability" and not psep:
        # 분리 정책 접근법 2 — 조기상환권을 따로 판단했더니 주계약과 밀접해 남긴다.
        # 분리하는 것은 전환권뿐이다(콜을 내재파생으로 두면 전환권과 얽혀 함께 묶인다).
        # 전환권 가치는 «사채 + 조기상환권»(B1) 위에 얹힌 몫이다 — B2 − B1.
        deriv = (b2 - b1) if sep else (b2 - ca - b1)
        host_acc = (100 + _ca) - (b2 - b1)
        rows = [("주계약 (사채 + 조기상환권)", host_acc),
                (("전환권 · 파생상품부채" if sep else
                  "복합내재파생상품 (전환권 + 매도청구권) · 파생상품부채"), deriv)]
        if sep and not kk and (abs(ca) > 1e-12 or not is_rcps(tm)):
            rows.append(("매도청구권 · 파생상품자산", -ca))
        note = ("분리 정책 **접근법 2**(권리마다 분리 여부를 판단한 뒤 분리 대상끼리 묶기 — 한공회 "
                "실무사례 30~32쪽)를 적용했습니다. 조기상환청구권은 주계약과 밀접하게 관련되어 "
                "분리하지 않고 주계약에 포함해 상각후원가로 측정합니다(제1109호 문단 4.3.3·"
                "B4.3.5(5)(가)). 전환권만 파생상품부채로 분리합니다."
                + ("" if sep else
                   " 매도청구권은 발행회사만 행사할 수 있는 내재파생이고 전환권과 얽혀 있어 "
                   "함께 하나의 복합내재파생상품으로 묶습니다 (문단 B4.3.4).")
                + f" 주계약(사채 + 조기상환권)의 이론가치는 {b1:,.2f} 입니다.")
    elif tm.conv_class == "liability":
        # 전환권이 파생상품부채 — 내재파생을 공정가치로 두고 주계약을 잔여로.
        # 전환권과 조기상환권은 상호의존적이라 하나의 복합내재파생상품으로 묶어
        # 전체로서(as a whole) 측정한다 (문단 B4.3.4).
        deriv = (b2 - b0) if sep else (b2 - ca - b0)
        host_acc = (100 + _ca) - (b2 - b0)      # 어느 쪽이든 같다
        rows = [("주계약 (잔여)", host_acc),
                ("복합내재파생상품 · 파생상품부채", deriv)]
        if sep and not kk and (abs(ca) > 1e-12 or not is_rcps(tm)):
            rows.append(("매도청구권 · 파생상품자산", -ca))
        note = (("분리 정책 **접근법 1**(서로 얽힌 권리를 먼저 묶고 판단 — 한공회 실무사례 "
                 "30~31쪽)에 따라, 전환권이 파생상품부채이므로 전환권과 조기상환권을 하나의 "
                 if emb_policy(tm) == 1 else
                 "분리 정책 **접근법 2**(권리마다 판단한 뒤 분리 대상끼리 묶기 — 30~32쪽)에서 "
                 "조기상환청구권도 분리 대상으로 판단했으므로, 전환권과 얽힌 두 권리를 하나의 ")
                + "복합내재파생상품으로 묶어 공정가치로 측정하고 주계약을 잔여로 둡니다 "
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
        if not kk and (abs(cad) > 1e-12 or not is_rcps(tm)):
            # 콜을 내재파생으로 둔 채 접근법 2 로 조기상환권만 남기면 콜은 따로 분리된다.
            rows.append((("매도청구권 · 파생상품자산" if sep else
                          "매도청구권 (내재파생 · 분리) · 파생상품자산"), -cad))
        rows.append(("전환권대가 · 자본", 100-b1+_cad))
        note = ("기업회계기준서 제1032호 문단 31 — 부채요소를 먼저 정하고 나머지를 자본에 "
                "배분합니다. 최초 인식에는 손익이 생기지 않습니다. "
                "조기상환청구권은 주계약과 밀접하게 관련되어 분리하지 않으므로 "
                "(제1109호 문단 4.3.3·B4.3.5(5)(가)) 부채요소에 포함해 상각후원가로 "
                f"측정합니다. 분리했다면 파생상품부채로 세웠을 금액은 {b1-b0:,.2f} "
                "입니다 — 분리하지 않으므로 인식하지 않고, 유효이자율에 녹아 듭니다."
                + ("" if sep else
                   " 분리 정책 **접근법 2**(권리마다 판단 — 한공회 실무사례 30~32쪽)에 따라 "
                   "발행회사만 행사하는 매도청구권은 따로 판단해 분리합니다. 이 앱은 그 콜을 "
                   "주계약에 남기는 처리는 지원하지 않습니다."))
    else:
        # 계약에 조기상환청구권이 없으면 0 짜리 줄을 남기지 않는다 — 있는 것처럼
        # 보이면 조서를 읽는 사람이 헷갈린다.
        _hasput = tm.p_s <= tm.p_e and abs(b1 - b0) > 1e-9
        rows = [("주계약 (옵션 없는 사채)", b0)]
        if _hasput or not sep:
            rows.append(("조기상환청구권 · 파생상품부채",
                         (b1-b0) if sep else (b1-b0-cad)))
        if sep and not kk and (abs(cad) > 1e-12 or not is_rcps(tm)):
            rows.append(("매도청구권 · 파생상품자산", -cad))
        if not sep:
            rows[1] = ("복합내재파생상품 · 파생상품부채", b1-b0-cad)
        rows.append(("전환권대가 · 자본", 100-b1+_cad))
        note = ("기업회계기준서 제1032호 문단 31 — 부채요소를 먼저 정하고 나머지를 자본에 배분합니다. "
                "최초 인식에는 손익이 생기지 않습니다."
                + ("" if sep else
                   (" 매도청구권은 발행회사만 행사할 수 있어 내재파생상품이므로 분리 정책 "
                    "**접근법 1**(한공회 실무사례 30~31쪽)에 따라 조기상환권과 하나로 묶어 "
                    "순액으로 봅니다 (문단 4.3.1 · B4.3.4)." if emb_policy(tm) == 1 else
                    " 분리 정책 **접근법 2**(30~32쪽)로 조기상환권과 매도청구권을 각각 판단해 "
                    "둘 다 분리 대상이므로, 서로 얽힌 두 권리를 하나로 묶어 순액으로 봅니다 "
                    "(문단 4.3.1 · B4.3.4).")))
    # 발행자 최초 인식 차이 — 당기손익을 골랐으면 주계약(전체 지정이면 그 한 줄)을
    # 공정가치로 올리고 차이를 손익 줄로 적는다. 이연(기본)이면 배분이 그대로다 —
    # 차이가 주계약 장부금액에서 빠져 유효이자율로 기간에 걸쳐 인식된다.
    d1 = issuer_day1(tm, b0, b1, b2, ca)
    if d1 and d1["pl"]:
        rows[0] = (rows[0][0], rows[0][1] + d1["diff"])
        rows.append(((DAY1_LOSS if d1["diff"] > 0 else DAY1_GAIN), -d1["diff"]))
    if d1 and d1["hybrid"] and abs(d1["diff"]) >= 0.005:
        note += ("  ※ " + issuer_day1_note(d1))
    rows.append(("합계", sum(v for _, v in rows)))
    if kk:
        note += ("  ※ 매도청구권이 **제3자 사전 기특정 콜**입니다. **채택한 접근법은 한공회 "
                 "연구보고서 시리즈 11 문단 4.5.4 «접근법 2-2»** 이며, 그 접근법에서는 발행회사가 "
                 "옵션 당사자가 아니라고 보아 파생상품자산을 인식하지 않고 받은 대가 100 을 "
                 "복합금융상품 요소에 전부 배분합니다. 옵션을 받은 제3자(최대주주 등)와 투자자 "
                 f"사이의 거래이며, 그 공정가치 {ca:,.4f} 는 참고로만 적습니다 — 주석 공시 "
                 "대상인지 별도로 판단하십시오. 본문 4.5 에는 4.5.2 «접근법 1»(발행자 콜과 같이 "
                 "유무가치비교법)과 4.5.3 «접근법 2-1» 도 있고, 4.5.1 은 주주간 분배로 «회계처리 "
                 "되는 경우가 있다»고 씁니다 — 접근법 1 을 따르려면 평가방법을 유무가치비교법으로 "
                 "두십시오.")
    if tm.elapsed_m > 0.01:
        note += ("  ※ 이 배분은 **최초 인식**용입니다. 평가기준일이 발행일보다 뒤이므로 "
                 "결산 회계처리에는 그대로 쓰지 마십시오. 결산일에 필요한 것은 파생상품의 "
                 "공정가치뿐이고, 주계약은 발행일 배분액을 유효이자율로 상각한 장부금액입니다.")
    return rows, note


NOTE_KKIND = ("제3자 기특정 콜옵션 {v} — 채택한 접근법은 한공회 연구보고서 시리즈 11 문단 "
              "4.5.4 «접근법 2-2» 다. 그 접근법에서는 발행회사가 옵션 당사자가 아니라고 보아 "
              "금융상품을 인식하지 않는다. 옵션을 받은 제3자(최대주주 등)와 투자자 사이의 "
              "거래이며 발행회사 측면에서는 주주간 분배다. 위 분개에 차변으로 넣지 않는다 — "
              "주석 공시 대상인지 별도로 판단할 것. 본문 4.5 에는 4.5.2 «접근법 1»(유무가치 "
              "비교법)과 4.5.3 «접근법 2-1» 도 있다.")


def alloc_extra(tm: Terms, ca):
    """배분표 **합계 밖**에 적는 참고 줄. 기특정 콜의 주주간 분배 금액이다.

    합계에 넣으면 100 이 되지 않고, 차변에 넣으면 분개 대차가 깨진다. 그래서
    합계 다음 줄에 «참고» 로만 싣는다. 화면·값 조서·수식 조서가 같이 부른다.
    """
    if int(getattr(tm, "k_kind", 0)) != 1 or is_sha(tm) or tm.k_w <= 0:
        return []
    return [("제3자 기특정 콜옵션 · 주주간 분배 (참고 · 발행자 자산 아님)", ca)]


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


def formula_key_cells(tm: Terms, raw: dict, eir=None) -> list:
    """수식 조서를 다시 계산했을 때 앱과 같아야 하는 칸 — [(항목, 시트, 칸, 앱 값)].

    재계산 검사(``xlsx_validation.recalculate_and_compare``)와 엑셀 입력 변경 시험이 같은
    목록을 본다. 주주간계약 조서는 구조가 달라 빈 목록이다.
    """
    if is_sha(tm): return []
    b0, b1, b2, ca = raw["b0"], raw["b1"], raw["b2"], raw["ca"]
    out = [("전체 (적용 물량)", "결과", "C10", b2), ("주계약", "결과", "C16", b0),
           ("부채요소 (사채 + 조기상환권)", "결과", "C17", b1), ("조기상환청구권", "결과", "C18", b1 - b0),
           ("매도청구권 (적용값)", "결과", "C22", ca)]
    if tm.conv_class == "equity":
        out.append(("전환권대가", "결과", "C23", raw["conv"]))
    if eir is not None:
        out.append(("상각표 유효이자율", "상각표", "C10", eir[0]))
    return out


def eir_or_none(tm: Terms, full, b0, b1, b2, ca):
    """조서에 실을 상각표. 전체 지정이면 ``None`` — 상각할 대상이 없다.

    조서 두 개가 같은 판단을 하도록 한 자리에 모아 둔다.
    """
    if acc_mode(tm) == "fv_only": return None      # 공정가치 전용 — 상각표를 만들지 않는다
    if holder_on(tm): return None                  # 투자자 — 전체를 공정가치로 잰다
    host = acc_host(tm, full, b0, b1, b2, ca)
    return None if host is None else eir_table(tm, host, eir_expect(tm))


BDT_GAP_TOL = 0.05      # 종전 참고값. 판단에 사용하지 않음.
BDT_RATIO_TOL = 0.2     # 종전 참고값. 판단에 사용하지 않음.
BDT_SPREAD_TOL = 0.7    # 종전 참고값. 판단에 사용하지 않음.


def rate_signals(tm: Terms):
    """금리를 확률변수로 둘 실익 — 격자를 네 번 더 돌려 잰다.

    금리 «수준» ±1%p (무위험·위험 곡선 평행), 신용스프레드 ±1%p (위험 곡선만),
    변동성 ±10%p 의 세 민감도와, 잔존만기 시점의 스프레드 비중. 화면 «분리 판단» 의
    expander 와 조서 «검산요약» 이 같은 값을 쓴다. 매도청구권은 빼고(call=False) 잰다.
    """
    base = pick(engine(tm, call=False), tm.model)

    def _bump(**kw):
        tt = Terms(**asdict(tm))
        for k2, v2 in kw.items(): setattr(tt, k2, v2)
        derive(tt); return pick(engine(tt, call=False), tt.model)
    _par = lambda d: dict(rf_curve=[(x, y+d) for x, y in tm.rf_curve],
                          cr_curve=[(x, y+d) for x, y in tm.cr_curve],
                          cr_curve_b=[(x, y+d) for x, y in tm.cr_curve_b])
    dl = (_bump(**_par(0.01)) - _bump(**_par(-0.01)))/2
    ds = (_bump(cr_curve=[(x, y+0.01) for x, y in tm.cr_curve],
                cr_curve_b=[(x, y+0.01) for x, y in tm.cr_curve_b])
          - _bump(cr_curve=[(x, y-0.01) for x, y in tm.cr_curve],
                  cr_curve_b=[(x, y-0.01) for x, y in tm.cr_curve_b]))/2
    dv = (_bump(sig=tm.sig+0.10) - _bump(sig=max(0.01, tm.sig-0.10)))/2
    RFc, CRc = curves(tm)
    spr = CRc(tm.T) - RFc(tm.T)
    share = spr/CRc(tm.T) if CRc(tm.T) > 1e-9 else 0.0
    return dict(base=base, dl=dl, ds=ds, dv=dv, ratio=abs(dl)/max(abs(dv), 1e-9),
                spr=spr, share=share)


def bdt_review(tm: Terms, full, b0, b1, b2, ca, sig=None):
    """Document observations without turning qualitative guidance into gates.

    Series 11 3.3.1.4/3.3.2.2 gives professional considerations, not mandatory
    5pp/20%/70% cutoffs or a necessary out-of-the-money condition. Historical
    dictionary keys are retained for workbook callers; None means human review.
    """
    if is_sha(tm):
        return None
    _, CRc = curves(tm)
    rd = math.exp(CRc(tm.T))-1
    gy = tm.ytm
    if float(getattr(tm, 'mat_amt', -1)) > 0:
        implied = mat_implied(tm, float(tm.mat_amt))
        if implied is not None: gy = implied
    m = int(tm.ytm_cmp)
    g = (1+gy/m)**m-1 if m > 0 else gy
    gap = rd-g
    mny = tm.S0/tm.K0 if tm.K0 > 0 else float('inf')
    D = full['dist']
    rows = [
        (1, '분류·이 앱의 계산 범위', '자본' if tm.conv_class == 'equity' else '파생상품부채', None,
         '회계단위와 모형의 지원 범위를 구분한다. 이 앱의 단독 BDT 지원 제한이 다른 금리모형의 필요성까지 부정하지 않는다.'),
        (2, '전환가액 대비 주가', f'주가 ÷ 전환가액 {mny:,.3f}', None,
         '내·외가격은 영향 분석의 참고사항이며 BDT 적용의 필수 관문이 아니다.'),
        (3, '보장수익률과 위험할인율', f'보장 {g*100:.2f}% · 할인 {rd*100:.2f}% · 격차 {gap*100:+.2f}%p', None,
         '금리 기간구조·신용등급·시간가치의 중요성을 함께 검토한다. 책 87쪽은 일률적인 수치 경계를 제시하지 않는다.'),
    ]
    if sig is not None:
        rows.append((4, '민감도 관측값', f"금리 ±1%p {sig['dl']:+,.4f} · 변동성 ±10%p {sig['dv']:+,.4f} · 비율 {sig['ratio']:.3f} · 스프레드 비중 {sig['share']:.1%}", None,
                     '서로 다른 크기의 충격을 비교한 참고값이다. 이 비율만으로 금리 시간가치가 중요하지 않다고 결론내리지 않는다.'))
    dist = None
    if put_bdt_on(tm):
        t0 = Terms(**asdict(tm)); t0.put_bdt = 0; derive(t0)
        _, _, other, _, _, _ = decompose(t0)
        dist = dict(bdt=b1, tf=other, diff=b1-other)
    result = 'BDT 적용 중 — 적합성 판단 필요' if dist else '평가자 판단 필요'
    reason = '실무사례집 3.3.1.4의 신용등급·금리 차이·시간가치 중요성을 검토하고 적용 또는 미적용 사유를 기록하십시오.'
    if tm.p_s > tm.p_e:
        result = '이 분석의 조기상환권 없음'
        reason = '상환권만을 대상으로 한 이 검토의 범위입니다. 다른 옵션의 금리위험까지 없다는 결론은 아닙니다.'
    text = (f'검토 초안: 보장수익률 {g:.2%}, 위험할인율 {rd:.2%}, 차이 {gap*100:+.2f}%p, '
            f'주가/전환가액 {mny:.3f}. 신용등급, 금리 기간구조 및 시간가치의 중요성을 검토하여 모형 선택 사유를 추가한다. '
            '이 초안은 검토 완료 또는 BDT 미적용 결론을 뜻하지 않는다.')
    if dist:
        text += (f" BDT와 TF 부채요소 차이는 {dist['diff']:+,.4f}(원금100 기준)이다. "
                 '두 모형의 금리 가정 차이가 잔여 전환권에 미치는 영향을 검토한다(책 89쪽).')
    return dict(관문=rows, 결론=result, 사유=reason, 문안=text, 왜곡=dist,
                지표=dict(rd=rd, g=g, gap=gap, mny=mny, conv_share=D['conv'],
                          put_share=D['put'], pv=b1-b0, cv=b2-b1))


# 계산 무결성 점검 — 하나라도 «확인 필요» 면 계산이 고장 난 것이라 조서를 만들지 않는다
# (service.export_bundle). 나머지 줄(우선순위·되돌린 설정 등)은 검토자가 판단할 사항이라
# 「확인할 사항」으로 간다.
HARD_CHECKS = frozenset({
    "결정 ↔ 지분·부채 배정", "행사 불가능한 자리의 결정", "뿌리 노드 = 결과 (두 모형)",
    "위험중립가중치 q", "정산 분포 합", "배분표 합 = 100", "분개 차대 균형",
    "거래원가 배분 합 = 원가", "상각표 기말 = 상환금액",
    "BDT 캘리브레이션 |ΣQ − 시장할인계수| 최대",
    "투자자 순포지션 = 전체 − 매도청구권", "투자자 분개 대차"})


def model_checks(tm: Terms, full, b0, b1, b2, ca, eir=None, light=False):
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
                "계약일에 배정한 노드에서만 열린다 (계약일 이후 첫 노드 · 허용 일수 안에서 앞선 노드는 같은 날)"))
    rt = full["memo"][full["root"]]
    ok_root = abs(full["TF"] - (rt["E"] + rt["B"])) < 1e-9 and abs(full["GS"] - rt["V"]) < 1e-9
    out.append(("뿌리 노드 = 결과 (두 모형)", f"TF {full['TF']:,.4f} · GS {full['GS']:,.4f}", "적합" if ok_root else "확인 필요",
                "지분+부채 = TF, V = GS. V ≠ 지분+부채 는 결함이 아니다"))
    out.append(("위험중립가중치 q", f"[{full['qmin']:.4f}, {full['qmax']:.4f}]", "적합" if not full["qbad"] else "확인 필요",
                "전 구간 (0, 1) 안" if not full["qbad"] else f"벗어난 구간 {len(full['qbad'])}개 — 화면은 계산을 멈춘다"))
    if holder_on(tm):
        # 투자자 관점 — 순포지션과 분개 대차가 맞는지. 배분표 검산은 발행자 대조용으로 남는다.
        _h = holder_rows(tm, full, b0, b1, b2, ca)
        _pos = _h["pos"][0][1]
        out.append(("투자자 순포지션 = 전체 − 매도청구권", f"{_pos:,.4f} = {b2:,.4f} − {ca:,.4f}",
                    "적합" if abs(_pos - (b2 - ca)) < 1e-9 else "확인 필요", "제1109호 문단 4.3.2 · 화면 B3"))
        _dr = sum(v for sd, _, v in _h["journal"] if sd == "차변")
        _cr = sum(v for sd, _, v in _h["journal"] if sd == "대변")
        out.append(("투자자 분개 대차", f"차변 {_dr:,.4f} · 대변 {_cr:,.4f}",
                    "적합" if abs(_dr - _cr) < 1e-9 else "확인 필요",
                    holder_mode_text(_h)))
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
    if cad < -1e-7 and light:
        rights.append(("매도청구권", cad, "한계", ""))
    elif cad < -1e-7:
        _cs3, _ps3 = lock_delay(tm)
        r3 = engine(tm, conv=True, put=True, call=True, conv_start=_cs3, put_start=_ps3, lock_m=tm.k_lock)
        fc = r3["dist"].get("conv_called", 0.0)
        rights.append(("매도청구권", cad, "한계" if fc > 0 else "확인 필요",
                       f"강제전환 확률 {fc:.4f} — 콜이 전환을 강제하면 할인이 가벼워져 값이 오른다 (모형 성질)" if fc > 0 else ""))
    else:
        rights.append(("매도청구권", cad, "적합", ""))
    for nm, v, vd, why in rights:
        out.append((f"권리 값 ≥ 0 · {nm}", f"{v:,.4f}", vd, why))
    # 어느 방법·어느 구분 기준으로 쟀는지 — 조서를 읽는 사람이 가장 먼저 확인할 것이다.
    if tm.k_w > 0 and not is_sha(tm):
        _std = ("유무가치비교법" if (issuer_redeem(tm) or not tm.k_third)
                else "옵션차익혼합할인법")
        _now = "유무가치비교법" if int(tm.k_method) == 0 else "옵션차익혼합할인법"
        out.append(("매도청구권 평가체계 버전", f"v{SCHEMA_VER}", "적합",
                    "시나리오 JSON 의 «_schema» 와 같다 — 옛 파일은 저장된 대로 열리고 "
                    "화면이 그 사실을 알려 준다"))
        out.append(("매도청구권 적용 방법 · 구분 기준",
                    K_METHODS[int(tm.k_method)]
                    + (" · " + K_SPLITS[int(tm.k_split)].split(" —")[0] if tm.k_method else "")
                    + (" · 의무보유 "
                       + (f"{tm.k_lock:,.0f}개월"
                          + ("(전환·조기상환)" if int(tm.k_lock_put) else "(전환만)")
                          if int(tm.k_hold) else "없음") if tm.k_method else ""),
                    "적합" if _now == _std else "한계",
                    ("본문 기본 접근법과 같다 (4.3.2 · 4.3.4)" if _now == _std else
                     f"본문 기본 접근법은 {_std} 이다 — 4.6.2 대로 선택 근거를 남길 것")))
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
                    "투자자 관점 — 복합계약 전체를 공정가치로 측정" if holder_on(tm) else
                    "복합계약 전체 FVPL 지정" if fvpl_on(tm) else
                    "후속평가 · 공정가치 산출 전용 (전기말 장부금액 없음)" if acc_mode(tm) == "fv_only" else
                    "잔여 주계약 ≤ 0 (Day-1 차이)"))
    else:
        r_, arows, red_, _ = eir
        end = arows[-1][5]
        out.append(("상각표 기말 = 상환금액", f"{end:,.6f} = {red_:,.6f}", "적합" if abs(end - red_) <= 1e-6 else "확인 필요",
                    f"유효이자율 {r_:.4%}" + (" · 기대만기 = 첫 조기상환 가능일 (조기상환권 비분리)"
                                          if eir_expect(tm) is not None else "")))
    # 우선순위 검산은 «실제로 열린 행사 노드» 로 센다. 날짜·주기만 견주면 계약서
    # 행사금액표를 넣은 계약에서 겹침을 놓친다. 겹치는 자리가 있으면 그 수를 적고,
    # 값이 갈릴 수 있는 자리(조기상환금액 > 매도청구금액)가 있으면 격자를 두 번 돌려
    # 두 값과 차이를 싣는다.
    # light — 조서 관문(service)이 부를 때는 격자를 더 돌리는 비교를 건너뛴다. 무결성 점검이
    # 아니라 검토자가 판단할 비교라서 판단·근거 탭이 버튼으로 따로 보여 준다.
    _ovall = (pc_overlap(tm) if (tm.k_w > 0 and not is_sha(tm) and not light) else [])
    _ovbig = [x for x in _ovall if x[2] > x[3] + 1e-9]
    pcc = None if light else pc_compare(tm)
    if light:
        pass
    elif pcc is None:
        out.append(("풋·콜 우선순위 · 겹치는 행사노드",
                    f"{len(_ovall)}개 (금액이 갈리는 자리 0개)", "해당 없음",
                    ("두 권리가 함께 열리는 노드는 있으나 그 자리의 조기상환금액이 "
                     "매도청구금액을 넘지 않는다. 그때는 어느 쪽이 먼저 움직여도 같은 값이다"
                     if _ovall else "두 권리가 함께 열리는 노드가 없다")))
    else:
        dpc = abs(pcc[0][1] - pcc[1][1])
        _p0 = next((v for lb, v, _, _ in pcc if lb.startswith("투자자")), None)
        _p1 = next((v for lb, v, _, _ in pcc if lb.startswith("발행자")), None)
        out.append(("풋·콜 우선순위 · 겹치는 행사노드",
                    f"{len(_ovall)}개 (금액이 갈리는 자리 {len(_ovbig)}개)",
                    "적합" if dpc <= 1e-6 else "한계",
                    (f"처음 갈리는 곳은 발행일 기준 {_ovbig[0][1]:,.0f}개월(스텝 {_ovbig[0][0]}) — "
                     f"조기상환 {_ovbig[0][2]:,.4f} 대 매도청구 {_ovbig[0][3]:,.4f}"
                     if _ovbig else "격자를 두 번 돌려 견주었다")))
        out.append(("매도청구권 · 우선순위별 차이",
                    f"{dpc:,.4f}", "적합" if dpc <= 1e-6 else "한계",
                    (f"투자자 우선 {_p0:,.4f} · 발행자 우선 {_p1:,.4f}. "
                     "계약의 통지기간·번복 조항이 정한다 — 결과 시트에 두 값을 나란히 "
                     "실었다. 고른 근거를 조서에 적을 것" if dpc > 1e-6 else
                     "격자를 두 번 돌린 결과가 같다")))
    if put_bdt_on(tm):
        bp = bdt_parts(tm)
        dmax = max(abs(sum(bp["Q"][k]) - bp["mkt"][k]) for k in range(int(bp["n"]) + 1))
        out.append(("BDT 캘리브레이션 |ΣQ − 시장할인계수| 최대", ("< 1E-09" if dmax < 1e-9 else f"{dmax:.2E}"),
                    "적합" if dmax < 1e-6 else "확인 필요",
                    "기준금리 a 를 이분법으로 역산한 결과 — 수식 조서에서 σ·곡선을 바꾸면 벗어난다"))
    notes = getattr(tm, "forced_notes", [])
    out.append(("되돌린 설정 (지원하지 않는 조합)", f"{len(notes)}건", "해당 없음" if not notes else "확인 필요",
                "; ".join(f"{k} → {v}" for k, v, _ in notes) if notes else "없음"))
    # 재현 기록 한 줄 — 이 조서가 어느 판의 앱에서 나왔는지 검산요약에서 바로 보인다.
    _m = run_stamp(tm)
    out.append(("재현 기록 · 앱 버전 · 입력 식별값",
                f"{_m['app_sha12'] or '?'} · {_m['terms_md5'][:8]}",
                "적합" if _m["app_sha12"] else "확인 필요",
                (f"계산 코드 버전 {_m['git_head']} · 가정 시트에 전체 기록" if _m["git_head"]
                 else "계산 코드 버전(git)을 확인하지 못했다 — 가정 시트에 나머지 기록") ))
    return out


def sha_checks(tm: Terms, R):
    """주주간계약 조서의 검산 표. 형식은 model_checks 와 같다."""
    out = []
    n = R["n"]
    if R.get("linked") and int(tm.sha_writer) != 0:
        # 풋 의무자가 콜 권리자가 아니면, 풋 권리자는 상대 콜을 끝내려고 손해 보는 풋도 행사할 수 있다 —
        # 그 물량의 풋 표시금액은 음수일 수 있다 (풋 권리자의 순액).
        out.append(("풋 ≥ 0", f"{R['put']:,.4f}", "해당 없음",
                    "같은 주식 물량 · 풋 의무자 ≠ 콜 권리자 — 콜을 끝내려는 행사가 있어 음수일 수 있다"))
    else:
        out.append(("풋 ≥ 0", f"{R['put']:,.4f}", "적합" if R["put"] >= -1e-7 else "확인 필요", ""))
    out.append(("콜 ≥ 0", f"{R['call']:,.4f}", "적합" if R["call"] >= -1e-7 else "확인 필요", ""))
    pmax = max(R["pk"](i) for i in range(n + 1))
    out.append(("풋 ≤ 최대 행사금액", f"{R['put']:,.4f} ≤ {pmax:,.4f}", "적합" if R["put"] <= pmax + 1e-7 else "확인 필요", "지분 ≥ 0 이므로"))
    out.append(("위험중립가중치 q", f"[{R['qmin']:.4f}, {R['qmax']:.4f}]", "적합" if not R["qbad"] else "확인 필요", ""))
    if int(tm.sha_kill):
        co = sum(1 for i in range(n + 1) for j in range(i + 1)
                 if R["KIND"][i][j] in ("put", "call") and R["P"][i][j] > 1e-12 and R["C"][i][j] > 1e-12)
        out.append(("상대 권리 소멸 — 행사 노드에 두 권리가 함께 남지 않음", f"{co}", "적합" if co == 0 else "확인 필요", ""))
    else:
        out.append(("상대 권리 소멸", "없음 (존속)", "해당 없음", "각 권리자가 자기 권리만 보고 판단한다"))
    out.append(("적격상장 스텝", f"{R['qi_step']}", "해당 없음" if R["qi_step"] < 0 else "적합",
                "" if R["qi_step"] < 0 else f"주가 > {tm.ipo_min:,.0f} 인 노드에서 풋 소멸" + (" · 콜도 소멸" if int(tm.sha_qipo_kill) else "")))
    # 재현 기록 한 줄 — model_checks 와 같은 형식이다.
    _m = run_stamp(tm)
    out.append(("재현 기록 · 앱 버전 · 입력 식별값",
                f"{_m['app_sha12'] or '?'} · {_m['terms_md5'][:8]}",
                "적합" if _m["app_sha12"] else "확인 필요",
                (f"계산 코드 버전 {_m['git_head']} · 해설 시트에 전체 기록" if _m["git_head"]
                 else "계산 코드 버전(git)을 확인하지 못했다 — 해설 시트에 나머지 기록")))
    return out


def matrix_summary(path=None):
    """tests/검증매트릭스.json 의 상품별 판정 개수. 없으면 None.

    기능목록.py 가 ``cases`` 키로 쓴다 — 예전 이름 ``rows`` 도 받는다. 한때 조서가 ``rows`` 만 읽어
    KeyError 를 조용히 삼키고 「매트릭스 파일이 없다」로 찍혔다. 시험(기능목록)이 이 함수를 같이 부른다.
    """
    # 시험 하네스는 이 파일을 exec 로 읽어 __file__ 이 없다 — 그때는 작업 폴더(저장소 루트)
    _here = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if "__file__" in globals() else os.getcwd()
    path = path or os.path.join(_here, "tests", "검증매트릭스.json")
    try:
        mx = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    rows = (mx.get("cases") or mx.get("rows") or []) if isinstance(mx, dict) else mx
    if not rows: return None
    order = ["CB", "RCPS", "BW", "SHA", "ALL"]
    prods = sorted({x["product"] for x in rows}, key=lambda p: order.index(p) if p in order else 9)
    out = []
    for pr in prods:
        cnt = {k: 0 for k in ("PASS", "EXPECTED_BLOCK", "KNOWN_LIMITATION", "FAIL", "NOT_TESTED")}
        for x in rows:
            if x["product"] == pr and x["status"] in cnt: cnt[x["status"]] += 1
        out.append((pr, cnt))
    return dict(rows=out, head=(mx.get("head", "") if isinstance(mx, dict) else ""), n=len(rows))


def write_call_rows(R, r, tm: Terms, full, b2, put, sec, fmt4, pct, grey):
    """결과 시트 «매도청구권 방법별 · 정합 분해» — 값이다. 두 조서가 같은 함수를 부른다.

    한공회 4.6.2 가 「선택한 평가기법이 적합하다는 판단 근거 등을 제시하고 관련 내용을
    문서화할 필요가 있다」고 하므로, 유형·기본 접근법·적용 방법·대체방법 값·차이·
    선택 근거를 한자리에 모은다.
    """
    cmp_, rec = call_compare(tm, full, b2)
    if not cmp_: return r
    sec(R, r, "4. 매도청구권 — 방법별 값과 선택 근거 (앱에서 생성 당시 계산한 참고값 · 4.6.2 문서화)", span=6); r += 1
    put(R, r, 2, "이 표의 숫자는 앱에서 생성 당시 계산한 참고값이다 — 수식 조서에서 입력을 바꿔도 다시 계산되지 않는다. "
                 "적용한 방법의 값은 위 2. 구성요소에서 수식으로 따라온다.", color=grey, size=9); r += 1
    put(R, r, 2, call_type_note(tm).replace("**", ""), color=grey, size=9)
    R.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
    R.row_dimensions[r].height = 42; r += 2
    base = next((v for nm, _, v, _ in cmp_ if nm.startswith("유무가치비교법 (")), None)
    for i, h in enumerate(["방법", "지분·채권 구분 기준", "값 (앱에서 생성 당시 계산한 참고값)", "유무가치 대비", "차이율", "적용"]):
        put(R, r, 2+i, h, bold=True, border=True, size=9)
    r += 1
    for nm, sp, v, on in cmp_:
        put(R, r, 2, nm, border=True, size=9); put(R, r, 3, sp, border=True, size=9)
        put(R, r, 4, v, fmt=fmt4, align="right", border=True)
        put(R, r, 5, (v - base) if base else "", fmt=fmt4, align="right", border=True)
        put(R, r, 6, ((v - base)/base if base else ""), fmt=pct, align="right", border=True)
        put(R, r, 7, "◀ 적용" if on else "", border=True, size=9); r += 1
    put(R, r, 2, call_compare_note(tm), color=grey, size=9)
    R.merge_cells(start_row=r, start_column=2, end_row=r, end_column=7)
    R.row_dimensions[r].height = 30; r += 2
    # 유무가치비교법 차액의 구성 — 관련 약정 · 행사 판정 · 할인 방식 (음수여도 0 으로 덮지 않는다)
    _tr = wow_trace(tm, full, b2)
    if _tr:
        put(R, r, 2, "유무가치비교법 차액의 구성 — 관련 약정 · 행사 판정 · 할인 방식", bold=True); r += 1
        for k, v in wow_trace_rows(_tr):
            put(R, r, 2, k, border=True, size=9)
            put(R, r, 4, v, fmt=fmt4, align="right", border=True); r += 1
        put(R, r, 2, wow_trace_note(_tr), color=grey, size=9)
        R.merge_cells(start_row=r, start_column=2, end_row=r, end_column=7)
        R.row_dimensions[r].height = 58; r += 2
    if rec:
        put(R, r, 2, "유무가치비교법과의 차이 — 어디에서 오는가", bold=True); r += 1
        for k, v in rec.items():
            put(R, r, 2, k, border=True, size=9)
            put(R, r, 4, v, fmt=fmt4, align="right", border=True); r += 1
        _d = rec["유무가치비교법 (적용 계약)"] - rec["옵션차익법 (적용 산식·적용 설정)"]
        put(R, r, 2, "차이 (유무가치 − 옵션차익) = ① + ②", bold=True, border=True, size=9)
        put(R, r, 4, _d, bold=True, fmt=fmt4, align="right", border=True); r += 2
        put(R, r, 2, CALL_REC_NOTE, color=grey, size=9)
        R.merge_cells(start_row=r, start_column=2, end_row=r, end_column=7)
        R.row_dimensions[r].height = 58; r += 2
    for _lb, _tx in call_method_rows(tm):
        put(R, r, 2, _lb, bold=True, border=True, size=9)
        put(R, r, 3, _tx, border=True, size=9)
        R.merge_cells(start_row=r, start_column=3, end_row=r, end_column=7); r += 1
    put(R, r, 2, "평가기법 선택 근거 (4.6.2)", bold=True, border=True, size=9)
    put(R, r, 3, (tm.k_basis.strip() or "— 사이드바 「평가기법 선택 근거」에 적으면 여기에 실린다"),
        border=True, size=9)
    R.merge_cells(start_row=r, start_column=3, end_row=r, end_column=7)
    return r + 2


def write_exdate_rows(J, r, tm: Terms, put, sec, grey, light):
    """«행사일 대조» 표 — 계약상 행사일과 실제로 쓴 노드(값). 두 조서의 분리 판단 시트 공통.

    화면(격자모형 검토)과 같은 표·같은 규칙 문장(EXDATE_RULE)이다. 돌려주는 것은 다음 빈 행.
    """
    rows = exercise_date_rows(tm)
    if not rows: return r
    sec(J, r, "행사일 대조 — 계약일과 실제로 쓴 노드", span=7); r += 1
    put(J, r, 2, exdate_head(tm), color=grey, size=9); r += 1
    for i, c in enumerate(EXDATE_COLS):
        put(J, r, 2+i, c, bold=True, fill=light, align="center", border=True, size=9)
    r += 1
    for row in rows:
        for i, v in enumerate(row):
            put(J, r, 2+i, ("—" if v is None else v), border=True, size=9,
                align=("right" if i in (3, 5) else None))
        r += 1
    put(J, r, 2, "규칙 — " + EXDATE_RULE, color=grey, size=9); r += 1
    if (J.column_dimensions["H"].width or 0) < 46:
        J.column_dimensions["H"].width = 46
    return r + 1


def write_pc_rows(R, r, tm: Terms, put, sec, fmt4, grey):
    """결과 시트 «우선순위별 매도청구권» — 값이다(트리 자체가 한 우선순위로 만들어지므로). 두 조서 공통.

    풋·콜 우선순위 두 값과, 값이 갈릴 자리가 있으면 매도청구 통지 뒤 전환 대응 두 값을 싣는다.
    """
    pcc = pc_compare(tm)
    sec(R, r, "3. 풋·콜 우선순위별 매도청구권 (앱에서 생성 당시 계산한 참고값 · 계약이 정한다)", span=5); r += 1
    if pcc is None:
        put(R, r, 2, "겹치는 노드에서 조기상환금액이 매도청구금액보다 큰 자리가 없어 두 우선순위가 같은 답이다.",
            color=grey, size=9)
        r += 1
    else:
        for i, h in enumerate(["우선순위", "매도청구권", "전환권대가", "적용"]):
            put(R, r, 2+i, h, bold=True, border=True, size=9)
        r += 1
        for lb, ca2, cv2, on in pcc:
            put(R, r, 2, lb, border=True); put(R, r, 3, ca2, fmt=fmt4, align="right", border=True)
            put(R, r, 4, cv2, fmt=fmt4, align="right", border=True)
            put(R, r, 5, "◀ 적용" if on else "", border=True); r += 1
        put(R, r, 2, "두 값이 다르면 어느 쪽이 맞는지는 계약의 통지기간·「통지된 조기상환청구를 매도청구로 번복할 수 "
                     "있는가」 조항이 정한다. 고른 근거를 조서에 적는다.", color=grey, size=9)
        r += 1
    # 매도청구 통지 뒤 전환 대응 — 전환청구기간과 겹치고 의무보유로 막히지 않는 행사일이 있을 때만
    ccc = cresp_compare(tm)
    if ccc is not None:
        r += 1
        _ov = cresp_overlap(tm)
        put(R, r, 2, f"매도청구 통지 뒤 전환 대응별 매도청구권 — 행사일 {len(_ov)}회가 전환청구기간과 겹치고 "
                     "의무보유로 막히지 않는다", bold=True); r += 1
        for i, h in enumerate(["통지 뒤 전환", "매도청구권", "전환권대가", "적용"]):
            put(R, r, 2+i, h, bold=True, border=True, size=9)
        r += 1
        for lb, ca2, cv2, on in ccc:
            put(R, r, 2, lb, border=True); put(R, r, 3, ca2, fmt=fmt4, align="right", border=True)
            put(R, r, 4, cv2, fmt=fmt4, align="right", border=True)
            put(R, r, 5, "◀ 적용" if on else "", border=True); r += 1
        put(R, r, 2, "어느 쪽이 맞는지는 매도청구 조항(통지를 받은 사채의 전환청구를 막는가)이 정한다. 세 평가방법이 "
                     "같은 선택을 따른다. 고른 근거를 조서에 적는다.", color=grey, size=9)
        r += 1
    return r

def write_check_sheets(wb, tm: Terms, checks, after="결과", review=None, xl=None):
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
    r = 6; nxl = 0
    for nm, val, vd, why in checks:
        # xl 이 있으면(수식 조서) 그 줄의 값·판정·설명을 다른 시트를 참조하는 수식으로 쓴다.
        # 파이썬 판정(vd)은 탭 색과 요약 줄에 그대로 쓴다 — 수식이 같은 답을 내는지는
        # 검산수식대조가 formulas 로 풀어 확인한다.
        fx = (xl or {}).get(nm)
        put(C, r, 2, nm, border=True); put(C, r, 3, (fx[0] if fx and fx[0] else val), border=True)
        put(C, r, 4, (fx[1] if fx and fx[1] else vd), bold=True, color=tone.get(vd, RPT["ink"]), border=True)
        put(C, r, 5, (fx[2] if fx and fx[2] else why), size=9, color=RPT["grey"], border=True, wrap=True)
        if fx: nxl += 1
        r += 1
    bad = [nm for nm, _, vd, _ in checks if vd == "확인 필요"]
    put(C, r + 1, 2, ("모든 항목 적합" if not bad else "확인 필요 " + str(len(bad)) + "건: " + ", ".join(bad)),
        bold=True, color=(RPT["green"] if not bad else RPT["red"]))
    if xl is not None:
        put(C, r + 2, 2, f'=IF(COUNTIF(D6:D{r-1},"확인 필요")=0,"수식 판정 — 모든 항목 적합",'
                         f'"수식 판정 — 확인 필요 "&COUNTIF(D6:D{r-1},"확인 필요")&"건")', bold=True)
        put(C, r + 3, 2, f"위 {nxl}줄의 값·판정은 다른 시트의 셀을 참조하는 **수식**이다 — 가정 시트를 바꾸면 따라온다. "
            "나머지 줄(정산 분포 합 · 우선순위별 차이 · 되돌린 설정 · «한계» 판정)은 앱 계산값이다. "
            "값 조서의 같은 줄과 문자열이 같아야 하며 검산수식대조가 그것을 확인한다.",
            size=9, color=RPT["grey"], wrap=True)
        C.merge_cells(start_row=r + 3, start_column=2, end_row=r + 3, end_column=5); C.row_dimensions[r + 3].height = 30
        r += 2
    C.sheet_properties.tabColor = RPT["green"] if not bad else RPT["red"]
    if review is not None:
        r += 3
        put(C, r, 2, "이자율모형(BDT) 적용 검토 — 『K-IFRS 실무사례와 해설 11』 3.3.1.4 · 3.3.2.2", bold=True, size=12); r += 1
        put(C, r, 2, "책 87쪽의 정성적 고려사항과 아래 관측값을 대조한다. 수치만으로 적용·미적용을 결정하지 않는다. 평가자의 결론과 근거를 별도로 기록한다.",
            size=9, color=RPT["grey"], wrap=True)
        C.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5); C.row_dimensions[r].height = 30; r += 2
        for i, h in enumerate(["검토사항", "값", "상태", "설명"]):
            put(C, r, 2 + i, h, bold=True, fill=RPT["band"], border=True)
        r += 1
        for no, q, v, ok, why in review["관문"]:
            put(C, r, 2, f"{no}. {q}", border=True); put(C, r, 3, v, border=True, wrap=True)
            put(C, r, 4, "판단 필요" if ok is None else "예" if ok else "아니오", bold=True, border=True,
                color=RPT["amber"])
            put(C, r, 5, why, size=9, color=RPT["grey"], border=True, wrap=True); r += 1
        r += 1
        _warn = review["결론"].startswith("이자율모형 적용을") or "닫힌 채" in review["결론"]
        put(C, r, 2, "결론 · " + review["결론"], bold=True, color=(RPT["amber"] if _warn else RPT["green"])); r += 1
        put(C, r, 2, review["사유"], size=9, color=RPT["grey"], wrap=True)
        C.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5); r += 1
        if review["왜곡"]:
            d = review["왜곡"]
            put(C, r, 2, "모형 간 차이 (3.3.2.2)", bold=True, border=True)
            put(C, r, 3, f"BDT 부채요소 {d['bdt']:,.4f} − TF 부채요소 {d['tf']:,.4f} = {d['diff']:+,.4f} → 전환권에서 빠진다",
                border=True, wrap=True)
            C.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5); r += 1
        r += 1
        put(C, r, 2, "조서 문안", bold=True); r += 1
        put(C, r, 2, review["문안"], wrap=True)
        C.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5); C.row_dimensions[r].height = 75
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
    _ms = matrix_summary()
    if _ms is None:
        put(V, r, 2, "매트릭스 파일이 없다 — python3 tests/기능목록.py 로 만든다", size=9, color=RPT["grey"]); r += 1
    else:
        for i, h in enumerate(["상품", "PASS · BLOCK · LIMIT · FAIL · NOT_TESTED", "기준"]): put(V, r, 2 + i, h, bold=True, border=True)
        r += 1
        for pr, cnt in _ms["rows"]:
            put(V, r, 2, pr, border=True)
            put(V, r, 3, " · ".join(str(cnt[k]) for k in ("PASS", "EXPECTED_BLOCK", "KNOWN_LIMITATION", "FAIL", "NOT_TESTED")), border=True)
            put(V, r, 4, _ms["head"], size=9, color=RPT["grey"], border=True); r += 1
    r += 1
    put(V, r, 2, "4. 기준선 (docs/검증기준선.md)", bold=True, fill=RPT["band"]); put(V, r, 3, "", fill=RPT["band"]); put(V, r, 4, "", fill=RPT["band"]); r += 1
    put(V, r, 2, "기본 계약 (carry=1)", border=True)
    put(V, r, 3, BASELINE_TEXT, border=True); r += 1
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
    # 최초 인식 손익 줄은 배분된 발행금액이 아니라 손익이므로 분모와 배분 대상에서 뺀다.
    base = [(k, v) for k, v in rows[:-1] if v > 0 and not k.startswith("최초 인식")]
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


def eir_expect(tm: Terms):
    """조기상환권을 분리하지 않을 때 상각표가 써야 하는 **기대만기** — (연수, 상환금액, 개월) 또는 None.

    조기상환권을 분리하지 않으면(put_in_host — 분리 정책·전환권 분류·콜 처리로 정해진다) 주계약
    (사채 + 조기상환권)을 통째로 상각후원가로 둔다. 그때 계약만기 현금흐름으로 유효이자율을 구하면 첫 조기상환 가능일의
    행사금액과 장부금액이 크게 벌어지고 이자비용·부채가 과소계상된다 — 기준서가 분리하지 않아도 되는
    경우로 든 «행사가격 ≈ 상각후원가»(B4.3.5(5)(가))와 어긋난다. 적합한 처리는 첫 조기상환 가능일을
    기대만기로, 그 시점 행사금액을 기대만기 현금흐름으로 두는 것이다.

    첫 조기상환 가능일이 평가기준일 이전이면 그 뒤의 첫 조기상환 가능일(주기 단위)이다. 그마저 평가기준일과
    같은 날이면(잔여 0) 상각할 기간이 없어 None — 계약만기로 둔다.
    분리 판단의 10% 검토는 옵션 없는 주계약(B0)이 대상이라 이 함수를 쓰지 않는다.
    """
    if fvpl_on(tm) or is_sha(tm) or is_bw(tm): return None
    if not put_in_host(tm): return None
    if tm.p_s > tm.p_e: return None
    # 계약서의 회차별 행사금액표를 넣으면 «행사 가능 시점도 표가 정한다» (exercise_amounts).
    # 그때 시작·주기로 걸으면 표에 없는 달을 첫 행사일로 잡아 산식으로 되돌아간다.
    _rows = sched_rows(getattr(tm, "p_sched", ""), tm)
    if _rows:
        _hit = [mo for mo, _ in _rows
                if mo >= tm.elapsed_m - 1e-9 and mo >= tm.p_s - 1e-9]
        if not _hit: return None
        m = float(_hit[0])
    else:
        m = float(tm.p_s)
        step = max(float(tm.p_f), 1e-6)
        while m < tm.elapsed_m - 1e-9 and m <= tm.p_e + 1e-9: m += step
        if m > tm.p_e + 1e-9: return None
    t_exp = (m - tm.elapsed_m)/12
    if t_exp < 0.01 or t_exp > tm.T - 1e-9: return None
    # 격자와 같은 산식에서 가져온다. 스텝이 아니라 «개월» 로 묻는다.
    amt = exercise_amounts(tm, 1, 0.0)["put_at_month"](m)
    return (t_exp, amt, m)


def eir_table(tm: Terms, host, expect=None):
    """유효이자율 상각표. ``expect`` = eir_expect() — 있으면 그 연수·금액이 만기 대신 선다."""
    c = 100*eff_cpn(tm)*tm.ipay/12
    per = max(1e-6, tm.ipay/12)
    red = exercise_amounts(tm, max(1, int(tm.n)), tm.T/max(1, int(tm.n)))["red"]
    hz = tm.T
    if expect is not None:
        hz, red = expect[0], expect[1]
    # 지급일은 계약상 일정이므로 **발행일**부터 센다. 평가기준일이 발행일보다
    # 뒤이면 첫 회차만 짧아지고 나머지는 온전한 한 주기다. 평가기준일에서
    # 세면 지급일이 계약과 어긋나 이자비용이 회차마다 밀린다.
    # 마지막은 만기다. 남는 조각이 주기의 10% 미만이면 앞 회차에 붙여
    # 하루짜리 회차를 만들지 않는다.
    ey_ = tm.elapsed_m/12
    ts, k = [], 1
    while k*per - ey_ < hz - per*0.1:
        t_ = k*per - ey_
        if t_ > per*0.1: ts.append(t_)
        k += 1
    ts.append(hz)
    nper = len(ts)
    # 기대만기(첫 조기상환 가능일)에 끝나는 상각표면 마지막 회차의 이자는 계약을 따른다 — «행사일 이자를
    # 따로 준다» 가 아니면(행사금액에 포함) 그날 이자를 따로 받지 않는다. 격자의 조기상환 갈래(_pcx)와 같다.
    # 종전에는 늘 이자를 더해, 그 설정에서 상각표의 마지막 현금흐름이 격자보다 이자 한 회분 많았다.
    cl = c if (expect is None or int(getattr(tm, "p_cpn_add", 0))) else 0.0
    def pv(r):
        return (sum(c*(1+r)**(-t) for t in ts[:-1])
                + (cl + red)*(1+r)**(-hz))
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
        ck = cl if k == len(ts) else c
        it = bv*((1+r)**(t-prev) - 1); end = bv + it - ck
        rows.append((k, t, bv, it, ck, end)); bv, prev = end, t
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
    # 금액과 열림 판정을 «엔진과 같은 곳» 에서 가져온다. 계약서의 회차별 행사금액표를
    # 넣으면 표가 산식을 이기고 행사 가능 시점도 표가 정하므로, 여기서 산식으로 다시
    # 계산하면 겹침 판정이 격자와 어긋난다.
    EA = exercise_amounts(tm, n, dt_)

    p_in, k_in = EA["p_on"], EA["k_on"]

    out = []
    for i in range(n+1):
        if not (p_in(i) and k_in(i)): continue
        out.append((i, EA["cmonth"](i), EA["put"](i), EA["call"](i)))
    return out


def cresp_overlap(tm: Terms) -> list:
    """매도청구 통지 뒤 전환 대응(k_conv_resp)이 값을 가를 수 있는 행사일 — 발행일부터 개월, 오름차순.

    매도청구가 열린 노드 가운데 전환청구기간 안이고 의무보유로 막히지 않은 자리다(만기 노드 제외 —
    만기일에는 매도청구를 반영하지 않는다). 현금납입 BW 는 전환으로 피하는 갈래가 없어 빈 목록이다.
    """
    if is_sha(tm) or tm.k_w <= 0 or bw_cash(tm) or tm.k_s > tm.k_e: return []
    try:
        n = max(1, int(tm.n)); ea = exercise_amounts(tm, n, tm.T/n)
        lk = float(tm.k_lock) if int(tm.k_hold) else -1.0
        return sorted(m for i, m in ea["k_dates"].items()
                      if i < n and tm.cv_s - 1e-9 <= m <= tm.cv_e + 1e-9 and m > lk + 1e-9)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return []


def cresp_compare(tm: Terms):
    """매도청구 통지 뒤 전환 대응 둘(k_conv_resp 1 · 0)로 재 본 매도청구권·전환권대가 — pc_compare 와 같은 모양.

    값이 갈릴 자리(cresp_overlap)가 없으면 None. 어느 쪽이 맞는지는 계약(통지 뒤 전환청구 제한 조항)이
    정하므로 두 값을 나란히 싣고 고른 근거를 적는다. 화면·값 조서·수식 조서가 같은 함수를 쓴다.
    """
    if not cresp_overlap(tm): return None
    out = []
    for cr, lb in ((1, "통지 뒤 전환할 수 있다"), (0, "통지 뒤 전환할 수 없다")):
        tp = Terms(**asdict(tm)); tp.k_conv_resp = cr; derive(tp)
        _, _, _, _, ca2, cv2 = decompose(tp)
        out.append((lb, ca2, cv2, conv_resp(tm) == bool(cr)))
    return out


def pc_compare(tm: Terms):
    """풋·콜 우선순위(pc_order) 둘로 재 본 매도청구권·전환권대가 — [(라벨, 매도청구권, 전환권대가, 적용)].

    겹치는 노드에서 조기상환금액이 매도청구금액보다 큰 자리가 없으면 두 갈래가 같은 답이라 None.
    있으면 격자를 두 번 더 돌린다. 어느 쪽이 맞는지는 수식이 아니라 계약(통지기간·번복 조항)이
    정하므로 조서에 두 값을 나란히 싣고 고른 근거를 적는다. 화면·값 조서·수식 조서가 같은 함수.
    """
    if is_sha(tm) or tm.k_w <= 0: return None
    if not [x for x in pc_overlap(tm) if x[2] > x[3] + 1e-9]: return None
    out = []
    for po, lb in ((0, "투자자 조기상환 우선"), (1, "발행자 매도청구 우선")):
        tp = Terms(**asdict(tm)); tp.pc_order = po; derive(tp)
        _, _, _, _, ca2, cv2 = decompose(tp)
        out.append((lb, ca2, cv2, int(tm.pc_order) == po))
    return out


# 분할·병합·무상증자에서 흔한 배수. 이 근처면 corporate action 을 강하게 의심한다.
CA_RATIOS = (2, 3, 4, 5, 10, 20, 100)


def px_trace(tm: Terms) -> list:
    """주가 조회 기록 다섯 줄 — ``[(항목, 문구)]``. 화면과 세 조서가 같은 것을 쓴다.

    조서를 받은 사람이 **무엇을 요청했고 무엇을 받았는지** 알아야 분할·병합을 의심할 때
    되짚을 수 있다. 직접 입력이면 조회 자체가 없었으니 그렇게 적는다.
    """
    src = (tm.s0_src or "").strip()
    if not src:
        return [("주가 출처", "직접 입력 — 야후 조회 없음"),
                ("요청 평가기준일", tm.d_base),
                ("실제 사용 거래일", "해당 없음 (직접 입력)"),
                ("원주가 · 수정주가", "해당 없음 (직접 입력)"),
                ("분할 기록", "조회하지 않음")]
    dd = tm.s0_date or "?"
    day = dd + ("" if dd == tm.d_base else "  (평가기준일이 휴장이라 직전 거래일)")
    if tm.s0_raw > 0:
        px = f"원주가 {tm.s0_raw:,.2f} · 수정주가 " + (
            f"{tm.s0_adj:,.2f}" if tm.s0_adj > 0 else "받지 못함")
        if tm.s0_adj > 0 and abs(tm.s0_adj - tm.s0_raw) > 0.005 * max(1.0, tm.s0_raw):
            px += "  ← 다르다. 조회일 뒤 조정사건 가능"
    else:
        px = "기록 없음 (이전 판에서 만든 값이거나 손으로 고친 값)"
    sp = tm.s0_splits or "조회하지 못함 — 확인 못 함"
    if tm.s0_splits == "없음":
        sp = "없음 — 다만 한국 종목은 무상증자가 야후에 기록되지 않는 경우가 많다"
    return [("주가 출처", src), ("요청 평가기준일", tm.d_base),
            ("실제 사용 거래일", day), ("원주가 · 수정주가", px), ("분할 기록", sp)]


def basis_check(tm: Terms, px_last: float = None) -> list:
    """주가와 전환가가 **같은 기준(basis)** 위에 있는지 본다. 네트워크를 쓰지 않는다.

    분할·병합·무상증자가 있으면 「분할 전 전환가액」과 「분할 후 주가」가 섞여 값이
    배수만큼 틀어진다. 그런데 앱은 지금까지 이것을 한 번도 보지 않았다 — ``validate()``
    안에 ``tm.S0`` 가 아예 등장하지 않았다.

    야후의 분할 이력은 판정 근거로 쓰지 않는다. 한국 종목은 커버리지가 고르지 않고,
    특히 **무상증자를 split 으로 기록하지 않는 경우가 많다** — 「기록이 없다」가
    「사건이 없었다」를 뜻하지 않는다. 대신 앱이 이미 가진 두 신호로 판정한다.

      ① 평가기준일 종가(S0, 원주가)와 변동성 시계열의 마지막 값(수정주가)의 배수.
         앱은 S0 를 auto_adjust=False 로, 시계열을 True 로 받는다 — 설계상 의도지만
         조회 구간 안에 조정사건이 있으면 두 값이 배수만큼 벌어진다.
      ② 전환가 계열(K0 · floor · par · K_cap)이 서로 정합적인가.
      ③ 주가와 전환가의 비가 상식 범위인가.

    돌려주는 것 — 경고 문구 목록. 계산을 막지는 않는다.
    """
    out = []

    def near(x):
        """1 이 아닌 흔한 배수(또는 그 역수)에 가까운가."""
        for r in CA_RATIOS:
            if abs(x - r) < 0.03*r or abs(x - 1.0/r) < 0.03/r: return r
        return None

    if px_last and px_last > 0 and tm.S0 > 0:
        rt = tm.S0/px_last
        hit = near(rt)
        # 흔한 배수면 크기와 무관하게, 아니면 5% 를 넘을 때만 알린다 — 하루 등락으로
        # 매번 경고가 뜨면 정작 중요한 신호를 흘려 보내게 된다.
        if hit or abs(rt - 1.0) > 0.05:
            msg = (f"평가기준일 주가 {tm.S0:,.0f}원 과 변동성 시계열의 마지막 값 "
                   f"{px_last:,.0f}원 이 {rt:,.4f}배 차이 납니다. ")
            if hit:
                out.append(msg + f"**{hit}배 안팎이라 분할·병합·무상증자를 의심해야 합니다.** "
                           "주가는 원주가(조정 전), 변동성 시계열은 수정주가라 조회 구간에 "
                           "조정사건이 있으면 이렇게 벌어집니다. 전환가액이 어느 기준인지 "
                           "계약서와 대조하십시오 — 기준이 섞이면 값이 배수만큼 틀어집니다.")
            else:
                out.append(msg + "시계열의 마지막 거래일이 평가기준일과 다르거나 "
                           "조정사건이 있었을 수 있습니다.")
    # 전환가 계열
    if tm.K_cap > 0 and tm.K0 > 0:
        rt = tm.K_cap/tm.K0
        if near(rt) and abs(rt - 1.0) > 0.01:
            out.append(f"최초 전환가액 {tm.K_cap:,.0f}원 이 현재 전환가액 {tm.K0:,.0f}원 의 "
                       f"{rt:,.2f}배입니다 — 리픽싱만으로 보기에는 배수가 정연합니다. "
                       "분할·병합 뒤 한쪽만 조정하지 않았는지 확인하십시오.")
    # 리픽싱이 없으면 하한이 애초에 작동하지 않고, 하한 = 액면가면 계약상 하한이 아니라
    # 상법상 액면미달발행 금지에서 오는 법정 하한이다 — 둘 다 비율을 따질 일이 아니다.
    if tm.K0 > 0 and tm.floor > 0 and tm.rfx_mode > 0 and tm.floor > tm.par + 1e-9:
        rt = tm.floor/tm.K0
        if rt < 0.3 or rt > 1.0:
            out.append(f"최저 조정가액이 현재 전환가액의 {rt:,.1%} 입니다. 계약상 하한은 "
                       "보통 70~80% 입니다 — 두 값이 같은 기준인지 확인하십시오.")
    if tm.S0 > 0 and tm.K0 > 0:
        rt = tm.S0/tm.K0
        if rt > 20 or rt < 0.05:
            out.append(f"주가가 전환가액의 {rt:,.1f}배입니다. 분할·병합 뒤 한쪽만 조정한 "
                       "값이 아닌지 확인하십시오.")
    return out


def validate(tm: Terms, px_last: float = None):
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
    # 만기일에 열린 매도청구 — 세 평가방법 모두 만기상환·전환을 먼저 본다(같은 날 사건 표). 알린다.
    if tm.k_w > 0 and not is_sha(tm) and tm.k_s <= tm.k_e:
        try:
            _n = max(1, int(tm.n)); _ea = exercise_amounts(tm, _n, tm.T/_n)
            _kmat = _n in _ea["k_dates"]
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            _kmat = False
        # 전환과 매도청구가 같은 노드에서 함께 열리고 의무보유로 막히지 않는 자리 — 그 자리에서는
        # «매도청구 통지 뒤 전환할 수 있는가»(k_conv_resp)가 값을 크게 가른다. 계약 판단이라 알린다.
        _ovc = cresp_overlap(tm)
        if _ovc:
            w.append(f"매도청구 행사일 {len(_ovc)}회가 전환청구기간과 겹치고 의무보유로 막히지 않습니다 (첫 회차 "
                     f"발행 후 {_ovc[0]:,.1f}개월). 이 자리에서는 **매도청구 통지를 받은 투자자가 전환으로 피할 수 "
                     "있는지**에 따라 세 평가방법의 값이 모두 크게 갈립니다 — 지금 설정은 「"
                     + ("전환할 수 있다" if conv_resp(tm) else "전환할 수 없다")
                     + "」입니다. 계약서의 매도청구 조항(통지 뒤 전환청구 제한 여부)을 확인하시고 근거를 조서에 "
                       "남기십시오. (매도청구권 칸에서 바꿉니다)")
        if _kmat:
            w.append("매도청구 행사기간이 **만기일까지** 열려 있습니다. 만기일에는 만기상환·전환(자동전환 포함)이 "
                     "매도청구보다 먼저라 세 평가방법 모두 만기일의 매도청구를 반영하지 않습니다. 계약서가 "
                     "만기일 매도청구를 따로 정하면 「평가에 반영하지 않은 권리」에 적으십시오.")
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
    if tm.k_w > 0 and not is_sha(tm) and not tm.k_third and int(tm.k_method):
        w.append("매도청구권을 **발행회사만** 행사할 수 있다고 두셨는데 옵션차익혼합할인법을 "
                 "고르셨습니다. 발행자 콜은 행사하면 사채가 소멸해 기초자산이 남지 않으므로 "
                 "복합옵션 구조가 성립하지 않습니다 — 본문 4.3.2 는 **유무가치비교법**을 기본 "
                 "접근법으로 둡니다. (매도청구권 칸에서 바꿉니다)")
    if tm.k_lock < tm.k_e and not issuer_redeem(tm) and tm.k_w > 0 and int(tm.k_hold):
        w.append("의무보유가 매도청구 종료보다 먼저 끝납니다. 그 뒤 노드에서는 투자자가 "
                 "전환·조기상환으로 대상물량을 소멸시킬 수 있어 콜이 실효화될 수 있습니다.")
    # 계약서에 의무보유가 있는데 스위치를 끄면 옵션차익법이 «콜 대상물량이 중간에 사라질 수
    # 있다» 고 보아 값이 크게 낮아진다. 반대로 없는데 켜 두면 크게 높아진다. 둘 다 경고한다.
    if tm.k_w > 0 and not is_sha(tm):
        if tm.k_lock > tm.cv_s and int(tm.k_hold) == 0:
            w.append("계약에 의무보유가 있는데 「콜 대상물량 의무보유 있음」을 꺼 두셨습니다. "
                     "두 평가방법 모두 콜 대상물량을 투자자가 중도에 소멸시킬 수 있다고 보아 "
                     "값이 낮아집니다. (매도청구권 칸에서 바꿉니다)")
        if tm.k_lock > tm.p_s and int(tm.k_hold) == 1 and not int(tm.k_lock_put):
            w.append("의무보유 기간에 **조기상환청구는 허용**한다고 두셨습니다. 계약서가 "
                     "대상물량의 조기상환도 막는다면 「이 기간에 조기상환청구도 막는다」를 "
                     "켜십시오. 두 평가방법의 값이 모두 달라집니다. (매도청구권 칸에서 바꿉니다)")
        if tm.k_lock <= tm.cv_s and int(tm.k_hold) == 1:
            w.append("계약에 의무보유가 없는데 「콜 대상물량 의무보유 있음」을 켜 두셨습니다. "
                     "콜 행사기회가 보장된다고 보아 값이 높아집니다. "
                     "계약서의 처분·전환 제한 조항을 확인하십시오. (매도청구권 칸에서 바꿉니다)")
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
        # 곡선과 σ 시계열의 근거 대조 — 등급·만기가 다르면 조서가 서지 않는다
        if tm.rvol_rating and tm.rt_tgt and tm.rate_mode != "direct" and tm.rvol_rating != tm.rt_tgt:
            w.append(f"위험 곡선의 평가대상은 **{tm.rt_tgt}** 인데 BDT 변동성은 **{tm.rvol_rating}** "
                     "시계열에서 뽑았습니다. 등급이 다르면 곡선과 변동성의 근거가 갈립니다 — 조서 가정 "
                     "시트에 두 출처가 나란히 실리니 이유를 적으십시오.")
        if tm.rvol_tenor > 0 and abs(tm.rvol_tenor - tm.T) > 0.5:
            w.append(f"BDT 변동성 시계열의 만기는 **{tm.rvol_tenor:g}년** 인데 잔존만기는 "
                     f"**{tm.T:.2f}년** 입니다. 만기가 다른 금리의 변동성이므로 그 차이를 조서에 적으십시오.")
        if tm.rate_mode == "direct" and tm.rvol_rating:
            w.append(f"위험 곡선은 **직접 입력**이고 BDT 변동성은 **{tm.rvol_rating}** 시계열입니다. "
                     "직접 넣은 곡선이 어느 등급·어느 날 고시인지 조서 가정 시트에 적어야 두 근거가 맞물립니다.")
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
    # 행사금액표 — 못 읽은 줄은 조용히 버리면 안 된다. 어느 줄인지 알려 준다.
    for _nm, _tx in (("조기상환", getattr(tm, "p_sched", "")),
                     ("매도청구", getattr(tm, "k_sched", ""))):
        _bad = [i+1 for i, (m, _) in enumerate(parse_sched(_tx, tm)) if m is None]
        if _bad:
            w.append(f"{_nm} 행사금액표에서 읽지 못한 줄이 있습니다 — "
                     f"{', '.join(str(x) for x in _bad[:8])}번째 줄. "
                     "한 줄에 「날짜 또는 개월 · 금액(%)」 두 값이어야 합니다.")
        _rows = sched_rows(_tx, tm)
        if _rows and _rows[-1][0] > tm.elapsed_m + tm.rem_m + 0.5:
            w.append(f"{_nm} 행사금액표의 마지막 회차가 만기보다 뒤입니다 — "
                     "발행일 기준 개월인지 확인하십시오.")
    if float(getattr(tm, "mat_amt", -1.0)) > 0 and tm.ytm > 0:
        _f = mat_red_formula(tm)
        if abs(_f - float(tm.mat_amt)) > 0.05:
            w.append(f"만기상환금액을 직접 넣으셨습니다 ({tm.mat_amt:,.4f}%). "
                     f"보장수익률 산식으로는 {_f:,.4f}% 입니다 — 계약서와 대조하십시오.")
    w += basis_check(tm, px_last)
    if not is_sha(tm) or tm.rf_curve:
        w += [x.replace("**", "") for x in curve_notes(tm)]
    if holder_on(tm) and (tm.prev_deriv >= 0 or tm.prev_host >= 0) and float(tm.prev_hold) < 0:
        w.append("투자자 관점인데 발행자의 전기말 장부금액(파생상품부채·주계약)이 들어 있습니다. "
                 "투자자 분개에는 쓰이지 않습니다 — 「전기말 장부금액(공정가치)」 칸에 전기말 "
                 "순포지션 공정가치를 넣으십시오.")
    if tm.base_shares > 0 and tm.dil_shares/tm.base_shares > DIL_WARN:
        w.append(dil_msg(tm).replace("**", ""))
    if tm.floor > tm.K0: w.append("최저 조정가액이 최초 전환가액보다 큽니다.")
    if tm.par > tm.floor: w.append("액면가가 최저 조정가액보다 큽니다. 액면가가 하한으로 작동합니다.")
    if tm.rfx_mode > 0 and round(tm.rfx_cyc*tm.n/(tm.T*12)) < 1:
        w.append("조정 주기가 노드 간격보다 짧습니다. 노드를 늘리십시오.")
    if round(tm.p_f*tm.n/(tm.T*12)) < 1: w.append("조기상환 주기가 노드 간격보다 짧습니다.")
    if tm.k_w > 0 and round(tm.k_f*tm.n/(tm.T*12)) < 1:
        w.append("매도청구 주기가 노드 간격보다 짧습니다.")
    if tm.sig <= 0.01: w.append("변동성이 지나치게 낮습니다.")
    if tm.sig > 2: w.append("변동성이 200%를 넘습니다. 단위를 확인하십시오.")
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
    w += dp_warnings(tm)
    return w


# ══════════════════════════════════════════════════════════
# 4. 주가·변동성
# ══════════════════════════════════════════════════════════
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


def fetch_close(code: str, market: str, on_date: str):
    """평가기준일(또는 직전 거래일)의 **종가**를 야후에서 받는다. (거래일, 종가, 심볼).

    변동성용 fetch_prices 와 달리 auto_adjust=False — 평가일의 주가는 그날 실제로 거래된
    값이어야지, 그 뒤의 증자·분할을 소급 반영한 수정주가가 아니다.

    **수정주가도 함께 받아 둔다.** 둘이 다르면 조회일 뒤에 조정사건이 있었다는 뜻이고,
    그때 전환가액이 어느 기준인지 확인해야 한다. 돌려주는 것은
    ``(거래일, 원주가, 심볼, 수정주가)`` — 수정주가를 못 받으면 ``None`` 이다.
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
            if hit:
                adj = None
                try:
                    da = yf.download(sym, start=d1, end=d2 + dt.timedelta(days=1),
                                     progress=False, auto_adjust=True, threads=False)
                    if da is not None and not da.empty:
                        if hasattr(da.columns, "nlevels") and da.columns.nlevels > 1:
                            da = da.droplevel(1, axis=1)
                        ca = "Close" if "Close" in da.columns else da.columns[0]
                        ar = [(i.strftime("%Y-%m-%d"), float(v)) for i, v in da[ca].dropna().items()]
                        ah = pick_close(ar, on_date)
                        if ah and ah[0] == hit[0]: adj = ah[1]
                except Exception:
                    adj = None           # 수정주가는 «참고» 다 — 못 받아도 원주가로 진행한다
                return hit[0], hit[1], sym, adj
            errs.append(f"{sym} {on_date} 이전 거래일 없음")
        except Exception as e:
            errs.append(f"{sym} {e}")
    raise RuntimeError(" / ".join(errs) or "자료를 찾지 못했습니다")


def fetch_splits(code: str, market: str, d1: str, d2: str):
    """야후가 기록한 **분할·병합 이력**. 판정 근거가 아니라 «참고» 다.

    돌려주는 것 — ``None`` 이면 **조회하지 못했다(모른다)**, ``[]`` 면 **기록이 없다**,
    그 외는 ``[(날짜, 배수)]``. 이 셋을 구분하는 것이 요점이다.

    한국 종목은 커버리지가 고르지 않다. 액면분할·병합은 대체로 잡히지만 **무상증자는
    split 으로 기록되지 않는 경우가 많다.** 그래서 「기록이 없다」를 「사건이 없었다」로
    읽으면 안 된다 — 화면·조서에 그 사실을 함께 적는다. 실제 판정은 basis_check() 가
    네트워크 없이 한다.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    for sym in _yf_symbols(code, market):
        try:
            sp = yf.Ticker(sym).splits
            if sp is None: continue
            out = [(i.strftime("%Y-%m-%d"), float(v)) for i, v in sp.items()
                   if d1 <= i.strftime("%Y-%m-%d") <= d2 and abs(float(v) - 1.0) > 1e-9]
            return out
        except Exception:
            continue
    return None


def fetch_prices(code: str, days: int, market: str, end: str = None, *, allow_missing=False, return_exclusions=False):
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
            if "Close" not in df.columns:
                raise ValueError('수정종가 Close 열이 없습니다.')
            sr = df['Close'].tail(days)
            excluded = []
            if allow_missing:
                missing = sr.isna()
                excluded = [dict(name=code, date=i.strftime('%Y-%m-%d'), reason='주가 누락') for i in sr.index[missing]]
                sr = sr[~missing]
            if any(not math.isfinite(float(v)) or float(v) <= 0 for v in sr):
                raise ValueError('조회 기간에 누락·0·음수 주가가 있습니다. 원자료를 확인하십시오.')
            rows = [(i.strftime("%Y-%m-%d"), float(v)) for i, v in sr.items()]
            if len(rows) >= 10:
                result = (rows[-days:], f"야후 {sym} · 수정주가")
                return (*result, excluded) if return_exclusions else result
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


# 화면 이자율 칸의 예시 곡선. 이대로 조서가 나가면 평가기준일 곡선이 아니다.
EXAMPLE_RF = [(0.25, .024), (0.5, .0238), (1.0, .0225), (2.0, .0233), (3.0, .0234), (5.0, .025)]
EXAMPLE_CR = [(1.0, .051), (2.0, .057), (3.0, .062), (4.0, .0665), (5.0, .0705)]


# 첫 실행에 비워 두는 칸 — 예시 회사의 숫자(주가·전환가 853원, 2025-03-31, σ 41.30% …)다.
# 지급주기·복리 횟수 같은 관행값은 넣지 않는다. 시나리오를 불러오면 모두 채워진다.
BLANK_FIELDS = ("d_issue", "d_mat", "d_base", "S0", "K0", "cpn", "ytm", "face_total",
                "cv_s", "cv_e", "rfx_cyc", "floor", "par", "p_s", "p_e", "p_rate", "p_yield",
                "k_s", "k_e", "k_prem", "k_w", "sig")

CURVE_TOL = 0.05        # 년 — 곡선 끝과 잔존기간이 이만큼(약 18일) 안이면 보외로 보지 않는다


def curve_is_example(pts, ex) -> bool:
    return (len(pts) == len(ex)
            and all(abs(a - c) < 1e-9 and abs(b - d) < 1e-9 for (a, b), (c, d) in zip(pts, ex)))


def curve_notes(tm) -> list:
    """이자율 곡선 경고 — 예시 곡선이 남아 있는가 · 곡선이 잔존기간까지 닿는가.

    곡선의 마지막 만기 뒤는 **마지막 수익률로 평평하게 연장**한다(보외 — _lin · lerp_formula).
    오류는 아니지만 잔존기간이 그보다 길면 그 뒤 구간의 할인율이 전부 한 값이 된다.
    """
    out = []
    for nm, pts, ex in (("무위험", tm.rf_curve, EXAMPLE_RF), ("위험", credit_curve(tm), EXAMPLE_CR)):
        if not pts: continue
        if curve_is_example(pts, ex):
            out.append(f"**{nm} 곡선이 화면의 예시 곡선 그대로입니다.** 평가기준일({tm.d_base})의 "
                       "고시 곡선으로 바꾸십시오 — KIS-Net 기준수익률 표를 올리고 「이 곡선 적용」을 "
                       "누르면 고시된 모든 만기가 들어갑니다.")
        last = max(x for x, _ in pts)
        if last < tm.T - CURVE_TOL:
            out.append(f"{nm} 곡선이 **{last:g}년**까지만 있습니다. 잔존기간 {tm.T:.2f}년 가운데 "
                       f"{last:g}년 뒤({tm.T - last:.2f}년)는 마지막 수익률 {dict(pts)[last]*100:.3f}% 로 "
                       "평평하게 연장합니다(보외). 고시표의 그 뒤 만기(7년·10년 등)까지 넣으십시오.")
    return out


def _brackets(pts, t):
    """t 를 감싸는 입력곡선 두 점의 번호. 범위 밖이면 (i, i) 로 한 점만 준다."""
    if not pts: return (0, 0)
    if t <= pts[0][0]: return (0, 0)
    if t >= pts[-1][0]: return (len(pts)-1, len(pts)-1)
    for i in range(1, len(pts)):
        if t <= pts[i][0]: return (i-1, i)
    return (len(pts)-1, len(pts)-1)


def lerp_formula(t, pts, col, row0, sh=None, tref=None, xcol=None, xsh=None):
    """엑셀에서 선형보간. 입력 셀을 가리키므로 노란 셀을 고치면 따라 움직인다.

    col 은 값이 든 열 문자, row0 은 첫 점의 행, sh 는 그 표가 있는 시트다.
    범위 밖이면 끝점을 그대로 쓴다 — 앱의 _lin 과 같다.

    ``tref`` 를 주면 보간 시점을 숫자 대신 **그 셀**에서 읽는다(같은 행의 t 열). ``xcol`` 을
    주면 두 만기도 숫자 대신 그 표의 만기 열에서 읽는다 — 시점·만기 칸을 고치면 보간값이
    따라온다. 어느 두 점 사이인지는 구조라 숫자 t 로 정한다.
    """
    q = f"'{sh}'!" if sh else ""
    i, j = _brackets(pts, t)
    if i == j: return f"={q}${col}${row0+i}"
    x0, x1 = pts[i][0], pts[j][0]
    tt = tref if tref else f"{t:.12g}"
    if xcol:
        qx = f"'{xsh or sh}'!" if (xsh or sh) else ""
        X0, X1 = f"{qx}${xcol}${row0+i}", f"{qx}${xcol}${row0+j}"
    else:
        X0, X1 = f"{x0:.12g}", f"{x1:.12g}"
    return (f"={q}${col}${row0+i}+({q}${col}${row0+j}-{q}${col}${row0+i})"
            f"*({tt}-{X0})/({X1}-{X0})")


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
    YEL = INPUT_FILL                          # 입력 칸 — 모든 조서가 같은 노란색
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
    if _rate:
        r += 1
        note(C, r, "이 시트는 BDT 금리변동성 σ 의 산출 근거다. 여기서 값을 바꿔도 조서의 BDT 격자는 "
                   "따라오지 않는다 — BDT 기준금리 a 가 지금 σ 와 금리곡선에 맞춰 앱에서 역산한 고정값이라, "
                   "σ 만 바뀌면 격자가 금리곡선과 어긋난다. σ 를 바꾸려면 앱에서 다시 평가해 조서를 새로 만든다.")

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
               (R_LO, "통계적 제외기준 하한", f"=$C${R_MD}-$C${R_MK}*$C${R_MA}", R_N6, False),
               (R_HI, "통계적 제외기준 상한", f"=$C${R_MD}+$C${R_MK}*$C${R_MA}", R_N6, False),
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
    YEL = INPUT_FILL                          # 입력 칸 — 모든 조서가 같은 노란색
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
         + ("주주간계약 조서 트리 시트의 9행(무위험)과 8행(풋 할인)이 이 숫자를 수식으로 가리킨다."
            if is_sha(tm) else "조서 트리 시트 11·12행에 들어가는 숫자가 여기서 나온다."))
    r = 5
    sec(C, r, "방법"); r += 1
    cols(C, r, ["단계", "내용"], [26, 64]); r += 1
    steps = ([("1. 입력", "현물이자율(제로커브) — 앱에 입력한 적용 금리곡선"),
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
                                  "rating": f"두 등급 {rating_blend_kind(tm)} ({tm.rt_a} · {tm.rt_b} → {tm.rt_tgt})"}.get(tm.rate_mode,
                                                          "직접 입력")),
                 ("위험 곡선 출처", tm.cr_src or "직접 입력"),
                 ("평가기준일", tm.d_base), ("만기일", tm.d_mat),
                 ("잔존기간 T (년)", round(T, 8)), ("노드 수 n", n),
                 ("무위험 곡선 마지막 만기 (년)", max(x for x, _ in tm.rf_curve)),
                 ("위험 곡선 마지막 만기 (년)", max(x for x, _ in cc)),
                 ("한 구간 Δt (년)", round(dt_, 8)),
                 ("주가 변동성 σ", tm.sig)]
                + ([("BDT 단기이자율 변동성 σ", tm.bdt_sig),
                    ("BDT σ 산출방식", sig_how or "직접 입력 — 이 조서에 금리변동성 산출내역이 포함되어 있지 않습니다"),
                    ("BDT 기준 곡선", "위험 곡선에 직접"
                     if tm.bdt_base == 0 else "무위험 + 확정 스프레드")]
                   if put_bdt_on(tm) else [])):
        put(C, r, 2, k, bold=True, border=True, fill=RPT["light"])
        put(C, r, 3, v, border=True,
            fmt=(R_P2 if k in ("주가 변동성 σ", "BDT 단기이자율 변동성 σ") else None),
            align=None if isinstance(v, str) else "right", wrap=True)
        r += 1
    r += 1
    for _cn in curve_notes(tm):
        note(C, r, _cn.replace("**", "")); r += 1
    if put_bdt_on(tm):
        note(C, r, "BDT 금리격자를 쓰는 조서라 입력곡선의 만기·수익률은 입력칸(노란색)이 아니다. "
                   "BDT 기준금리 a 가 이 곡선에 맞춰 앱에서 역산한 고정값이어서, 곡선을 이 파일에서 고치면 "
                   "주가 격자의 선도이자율만 따라오고 a 는 그대로라 결과가 틀어진다. 곡선을 바꾸려면 앱에서 "
                   "다시 평가해 조서를 새로 만든다.")
    else:
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
        note(C, r, "BDT 단기이자율 변동성 σ 는 조기상환청구권을 평가하는 금리격자에만 쓴다(전환권·매도청구권 "
                   "가치는 주가 격자에서 계산한다). 산출방식 줄이 「직접 입력」이면 이 조서에 금리변동성 산출내역이 "
                   "포함되어 있지 않습니다 — 앱 조서 탭의 「금리변동성 산출내역」 리포트를 함께 철하십시오.")
        r += 1
        note(C, r, "BDT 단기이자율과 이 시트의 선도이자율은 다르다. 선도이자율은 주가 격자의 한 구간에 쓰는 "
                   "확정 금리다. BDT 단기이자율 r(i,j) = a_i · exp(2σ·j·√Δt) 는 같은 시점에서도 노드마다 다른 "
                   "금리이고, 시점마다 기준금리 a_i 를 정해 격자 전체로 할인한 값(도달가격의 합)이 "
                   + ("위험 곡선의 할인계수와 같아지게 맞춘다." if tm.bdt_base == 0 else
                      "무위험 곡선의 할인계수와 같아지게 맞추고, 할인할 때 구간 선도 신용스프레드를 확정으로 얹는다.")
                   + " 그래서 할인계수는 곡선과 같지만 한 노드의 단기이자율은 "
                   "선도이자율과 같지 않다. a_i 는 앱이 역산한 고정값이다 — 곡선이나 σ 를 바꾸려면 앱에서 다시 평가한다.")
        r += 1
    note(C, r, "위험 곡선 출처는 앱의 이자율 칸에서 고른 그대로다. 두 등급으로 대상 등급을 만들었으면 "
               "(대상 등급이 두 등급 사이면 보간, 밖이면 외삽) 아래 입력곡선 시트의 위험 열이 그렇게 만든 "
               "적용 금리곡선이고, 등급 하나를 고르셨으면 고시표의 그 줄이 그대로 들어간다.")
    r += 1
    note(C, r, "1년 미만 만기의 수익률은 현금흐름이 한 번인 할인채 수익률로 보아 할인계수를 구했다. "
               "국내 채권의 관행적 복할인(끝 구간 단리)과의 차이는 3개월·3% 기준 가격의 약 0.003% 이다.")

    # ── 입력곡선 ──
    # 무위험은 B·C·D, 위험은 E·F·G 열이다 — 산출 시트들이 이 열(LEG 의 만기·수익률 열)을 가리킨다.
    I = sheet(IN, widths=[12, 16, 15, 12, 16, 15])
    head(I, 2, "입력 곡선", ("적용 금리곡선 — 앱에 입력한 곡선" + (" (위험 곡선은 등급 조정 후 적용 금리곡선)"
                                                         if tm.rate_mode == "rating" else "")
                             + ". 이후 모든 이자율 계산의 출발점이다."),
         span=6)
    # 표 제목은 그 표의 열 바로 위에 — 무위험 B~D, 위험 E~G (산출 시트가 가리키는 열과 같다).
    put(I, R0IN-2, 2, "무위험 곡선", bold=True, size=9.5, color=RPT["sub"])
    I.merge_cells(start_row=R0IN-2, start_column=2, end_row=R0IN-2, end_column=4)
    put(I, R0IN-2, 5, "위험 곡선" + (" (등급 조정 후)" if tm.rate_mode == "rating" else ""),
        bold=True, size=9.5, color=RPT["sub"])
    I.merge_cells(start_row=R0IN-2, start_column=5, end_row=R0IN-2, end_column=7)
    cols(I, R0IN-1, ["무위험 만기", "수익률", "연속환산",
                     "위험 만기", "수익률", "연속환산"],
         [12, 16, 15, 12, 16, 15])
    # BDT 금리격자를 쓰면 기준금리 a 가 이 곡선에 맞춰 앱에서 역산한 고정값이다. 곡선을
    # 엑셀에서 고치면 주가 격자의 선도이자율은 따라오지만 a 는 그대로라 두 격자가 서로 다른
    # 곡선을 쓰게 된다 — 그래서 입력칸(노란색)으로 열지 않는다.
    _lock = put_bdt_on(tm)
    _yf = None if _lock else YEL
    for (lbl, pts, cmp_, mcol, ycol) in LEG:
        for i, (mt, y) in enumerate(pts):
            rr = R0IN + i
            put(I, rr, 2 if mcol == "B" else 5, mt, fmt="0.####",
                border=True, align="right", fill=_yf)
            put(I, rr, 3 if mcol == "B" else 6, y, fmt=R_P4,
                border=True, align="right", fill=_yf)
            put(I, rr, 4 if mcol == "B" else 7,
                f"={cmp_}*LN(1+{ycol}{rr}/{cmp_})", fmt=R_P4,
                border=True, align="right")
    endr = R0IN + max(len(tm.rf_curve), len(cc)) + 1
    note(I, endr, "연속환산 열은 참고다. 만기수익률을 넣었다면 실제 계산은 다음 두 "
                  "시트의 부트스트래핑에서 하고, 현물이자율을 넣었다면 이 열이 곧 "
                  "쓰이는 값이다.", span=6)
    if _lock:
        I.protection.sheet = True                  # 곡선을 엑셀에서 고치지 못하게 — 기준금리 a 가 이 곡선에 맞춘 고정값이다
        note(I, endr+1, "BDT 금리격자를 쓰는 조서라 이 곡선은 입력칸이 아니다 — BDT 기준금리 a 가 이 곡선과 "
                        "BDT 금리변동성 σ 에 맞춰 앱에서 역산한 고정값이므로, 곡선을 바꾸려면 앱에서 다시 "
                        "평가해 조서를 새로 만든다.", span=6)

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
                           "현물 (연속)", "비고"], [8, 13, 15, 13, 15, 15, 15, 30])
            _bt = boot_times(pts, T, cmp_)
            N = len(_bt)
            _last = max(x for x, _ in pts)
            _prev_grid, _k = None, 0
            for j, (t_, kind) in enumerate(_bt):
                rr = R0 + j
                if kind == "grid":
                    _k += 1
                    put(W, rr, 2, _k, fmt=R_N0, border=True, align="center")
                    # 만기 = k ÷ 이표 횟수 — 수식으로 두어 보간 수익률이 이 칸을 읽는다
                    put(W, rr, 3, f"=B{rr}/{cmp_}", fmt="0.0000", border=True, align="right")
                else:
                    put(W, rr, 2, "·", border=True, align="center")
                    put(W, rr, 3, t_, fmt="0.0000", border=True, align="right")
                put(W, rr, 4, lerp_formula(t_, pts, ycol, R0IN, IN, tref=f"C{rr}",
                                           xcol=mcol), fmt=R_P4, border=True, align="right")
                put(W, rr, 5, f"=D{rr}/{cmp_}", fmt=R_N6, border=True, align="right")
                if kind == "grid":
                    put(W, rr, 6, ("=0" if _prev_grid is None else f"=F{_prev_grid}+G{_prev_grid}"),
                        fmt=R_N6, border=True, align="right")
                    put(W, rr, 7, f"=(1-E{rr}*F{rr})/(1+E{rr})", fmt=R_N6,
                        border=True, align="right")
                    _prev_grid = rr
                    _nt = ""
                elif kind == "zero":
                    put(W, rr, 7, f"=(1+E{rr})^(-{cmp_}*C{rr})", fmt=R_N6,
                        border=True, align="right")
                    _nt = "첫 이표 전 만기 — 이표 없이 만기에 한 번 받는다"
                else:
                    # 이표는 만기에서 1/m 씩 거꾸로 센다. 앞 시점의 DF 는 이미 푼 줄의 현물을 잇는다.
                    cps, tj = [], t_ - 1/cmp_
                    while tj > 1e-9:
                        cps.append(tj); tj -= 1/cmp_
                    prev = [(x, 0.0) for x, _ in _bt[:j]]
                    dfq = lambda tq: (f"EXP(-({lerp_formula(tq, prev, 'H', R0)[1:]})*{tq:.12g})")
                    first = cps[-1]
                    terms = [f"E{rr}*{first*cmp_:.12g}*{dfq(first)}"] + [f"E{rr}*{dfq(x)}" for x in cps[:-1]]
                    put(W, rr, 7, f"=(1-({'+'.join(terms)}))/(1+E{rr})", fmt=R_N6,
                        border=True, align="right")
                    _nt = f"이표 시점 사이 만기 — 첫 이표({first:.2f}년)는 기간 비율 {first*cmp_:.2f} 만큼"
                put(W, rr, 8, f"=-LN(G{rr})/C{rr}", fmt=R_P4, border=True, align="right")
                if t_ > _last + 1e-9:
                    _nt = f"외삽 — 마지막 만기 {_last:g}년 수익률로 연장"
                if _nt:
                    put(W, rr, 9, _nt, color=(RPT["red"] if "보외" in _nt else RPT["grey"]),
                        size=9, border=True)
            # 보간에 쓸 표 — 만기는 C, 현물은 H 열
            made[lbl] = dict(sh=sn, r0=R0, tcol="C", rcol="H",
                             pts=[(t_, None) for t_, _ in _bt])
            note(W, R0+N+1, "DF 는 앞 이표 시점의 결과를 이어 받는다(누적 DF). 이표 주기와 어긋난 "
                            "입력 만기(예: 반기 이표 곡선의 3M·9M)도 한 줄씩 풀어 곡선에 넣는다 — "
                            "비고 열에 방법을 적었다. 입력 곡선의 마지막 만기 뒤는 마지막 수익률로 "
                            "평평하게 연장한다(외삽).", span=8)

    # ── 선도이자율 ──
    def spot_ref(leg, t, tref=None):
        """산출 시트의 현물 표에서 t 의 값을 뽑는 수식. tref 셀에서 시점을 읽는다."""
        d = made[leg]
        pts = [(x, 0.0) for x, _ in d["pts"]]
        return lerp_formula(t, pts, d["rcol"], d["r0"], d["sh"], tref=tref, xcol=d["tcol"])

    FS = f"{P}선도이자율"
    F = sheet(FS, tab=RPT["green"],
              widths=[8, 13, 13, 14, 14, 14, 14, 14, 14, 14, 12],
              freeze="B10", landscape=True)
    head(F, 2, "구간 선도이자율",
         "격자 한 칸을 건너갈 때 쓰는 이자율이다. "
         + ("주주간계약 조서 트리 시트의 9행(무위험)과 8행(풋 할인 — 가정의 풋 할인 기준에 따라 무위험 · 위험 · "
            "무위험 + 스프레드)이 이 값을 가리킨다." if is_sha(tm) else
            "조서 트리 시트의 11행(무위험)과 12행(위험)에 이 값이 그대로 들어간다."), span=11)
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
                "위험 r(t₀)", "위험 r(t₁)", "위험 선도", "스프레드", "q", "비고"],
         [8, 13, 13, 14, 14, 14, 14, 14, 14, 14, 12, 30])
    _lastc = min(max(x for x, _ in tm.rf_curve), max(x for x, _ in cc))
    for i in range(n):
        rr = 10 + i
        t0, t1 = i*dt_, (i+1)*dt_
        put(F, rr, 2, i, fmt=R_N0, border=True, align="center")
        # t₀ = 스텝 × Δt, t₁ = t₀ + Δt — 현물 네 열이 이 두 칸을 읽는다
        put(F, rr, 3, f"=B{rr}*$C$7", fmt="0.000000", border=True, align="right")
        put(F, rr, 4, f"=C{rr}+$C$7", fmt="0.000000", border=True, align="right")
        put(F, rr, 5, spot_ref("무위험", t0, f"C{rr}"), fmt=R_P4, border=True, align="right")
        put(F, rr, 6, spot_ref("무위험", t1, f"D{rr}"), fmt=R_P4, border=True, align="right")
        put(F, rr, 7, f"=(F{rr}*D{rr}-E{rr}*C{rr})/(D{rr}-C{rr})", fmt=R_P4,
            border=True, align="right", bold=True)
        put(F, rr, 8, spot_ref("위험", t0, f"C{rr}"), fmt=R_P4, border=True, align="right")
        put(F, rr, 9, spot_ref("위험", t1, f"D{rr}"), fmt=R_P4, border=True, align="right")
        put(F, rr, 10, f"=(I{rr}*D{rr}-H{rr}*C{rr})/(D{rr}-C{rr})", fmt=R_P4,
            border=True, align="right", bold=True)
        put(F, rr, 11, f"=J{rr}-G{rr}", fmt=R_P4, border=True, align="right")
        put(F, rr, 12, f"=(EXP(G{rr}*$C$7)-$I$7)/($G$7-$I$7)", fmt=R_N4,
            border=True, align="right")
        if t1 > _lastc + 1e-9:
            put(F, rr, 13, f"외삽 — 곡선 마지막 만기 {_lastc:g}년 뒤", color=RPT["red"],
                size=9, border=True)
    note(F, 10+n+1, "t₀·t₁ 은 Δt 로 계산되고 현물 네 열은 그 두 칸을 읽는다. 곡선의 마지막 만기 "
                    "뒤는 마지막 수익률로 평평하게 연장하므로(외삽) 선도이자율이 한 값으로 "
                    "고정된다 — 비고 열에 표시한다. "
                    "스프레드(위험 − 무위험)가 음수인 줄이 있으면 두 곡선의 구분·기준일·보간 방법을 확인하십시오. "
                    "위험중립 상승확률 q 가 0 과 1 사이를 벗어나면 이 격자로 계산할 수 없다 — 앱이 평가 단계에서 막는다.", span=11)
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
    ("조기상환청구", "상환청구"), ("조기상환 청구", "상환청구"), ("조기상환", "상환청구"),
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
    ("옵션 없는 사채", "옵션 없는 우선주부채"), ("옵션 제외 사채", "옵션 제외 우선주부채"),
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
    ("전환주식수", "인수주식수"),
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


# 계산 시트마다 「이 결과를 어디에 쓰는가」 — 시트 상단 설명에 붙인다 (계산 코드용 이름).
USED_BY = {
    "01 주가": "04 전환가치 · 02 전환가격(조정일) · 도달확률",
    "02a 이월 전환가격": "02 전환가격",
    "02b 정기 조정": "02 전환가격",
    "02 전환가격": "03 전환비율 · 다음 시점 02a",
    "03 전환비율": "04 전환가치",
    "04 전환가치": "09 의사결정 · 05 지분가치 · 08 금융상품가치 (GS: 14)",
    "05 지분가치": "앞 시점의 07 보유가치 · 08 금융상품가치",
    "06 부채가치": "앞 시점의 07 보유가치 · 08 금융상품가치",
    "07 보유가치": "09 의사결정 · 08 금융상품가치",
    "08 금융상품가치": "결과 시트(콜 반영 전 가치) · 19 콜 즉시행사가치",
    "09 의사결정": "05 지분가치 · 06 부채가치",
    "10 주계약가치": "결과 시트(주계약)",
    "11 GS 전환확률": "12 GS 할인율 · 11b",
    "11 전환확률": "12 전환확률 · 할인율 · 11b · 17a · 17b",
    "12 GS 할인율": "13 GS 보유가치", "12 전환확률 · 할인율": "13 전환확률 · 보유가치",
    "13 GS 보유가치": "14 GS 금융상품가치", "13 전환확률 · 보유가치": "14 전환확률 · 금융상품가치",
    "14 GS 금융상품가치": "결과 시트(콜 반영 전 가치) · 앞 시점의 11",
    "14 전환확률 · 금융상품가치": "앞 시점의 11 전환확률",
    "16 부채요소": "결과 시트(부채요소)",
    "19 콜 페이오프": "17a · 17b · 19b · 20 · 23 · 24",
    "19a 콜 존속": "19b 행사 판단 · 20 매도청구권가치",
    "19b 행사 판단": "23 · 24",
    "17a 행사 지분몫": "23 방법2 지분몫", "17b 행사 채권몫": "24 방법2 부채몫",
    "21 방법2 지분보유": "23 방법2 지분몫", "22 방법2 부채보유": "24 방법2 부채몫",
    "23 방법2 지분몫": "결과 시트(매도청구권) · 앞 시점의 21",
    "24 방법2 부채몫": "결과 시트(매도청구권) · 앞 시점의 22",
}


def used_by(name: str) -> str:
    u = USED_BY.get(name) or ("결과 시트(콜 반영 후 가치)" if name.startswith("15 ") else "")
    return f"  ▶ 사용처: {u}" if u else ""


def sheet_display_names(tm: Terms) -> dict:
    """계산 코드가 쓰는 시트 이름 → 조서에 보이는 이름.

    코드 안에서는 짧은 옛 이름(05 지분가치 …)을 그대로 쓰고, 조서를 다 만든 뒤
    rename_sheets 가 시트 이름·수식 참조·설명 문구를 한꺼번에 바꾼다 — 계산용 이름과
    표시용 이름을 나눠 수식이 깨지지 않게 한다. TF 의 주식결제분·현금결제분은 회계상
    자본요소·부채요소와 금액이 다를 수 있어 회계 명칭을 쓰지 않는다.
    """
    bw, rc = is_bw(tm), is_rcps(tm)
    inst = "BW" if bw else "RCPS" if rc else "CB"
    unit = "발행가 100당" if rc else "액면 100당"
    cv = "행사" if bw else "전환"
    host = "우선주부채" if rc else "사채"
    putw = "상환청구권" if rc else "풋"
    callw = "발행자 상환권" if issuer_redeem(tm) else "콜"
    m = {
        "03 전환비율": f"03 {'인수' if bw else '전환'}주식수 ({unit})",
        "04 전환가치": f"04 즉시{cv}가치",
        "05 지분가치": "05 주식결제분 가치",
        "06 부채가치": "06 현금결제분 가치",
        "07 보유가치": "07 계속보유가치",
        "08 금융상품가치": f"08 {callw} 반영 전 {inst} 가치",
        "10 주계약가치": f"10 옵션 제외 {host}가치",
        "11b 채권확률 (1 − 전환확률)": f"11b 비{cv}확률 (1 − {cv}확률)",
        "16 부채요소": f"16 {'신주인수권' if bw else '전환권'} 제외 {host}가치 ({putw} 포함)",
        "16c 부채요소 (발행자 상환권)": f"16c {cv}권 제외 · 발행자 상환권 포함",
        "17a 행사 지분몫": "17a 콜 행사 시 주식결제 성분",
        "17b 행사 채권몫": "17b 콜 행사 시 현금결제 성분",
        "19 콜 페이오프": "19 콜 즉시행사가치",
        "19a 콜 존속": "19a 콜 존속 여부",
        "19b 행사 판단": "19b 콜 행사·보유 판단",
        "21 방법2 지분보유": "21 콜 계속보유 · 주식결제 성분",
        "22 방법2 부채보유": "22 콜 계속보유 · 현금결제 성분",
        "23 방법2 지분몫": "23 콜 최종가치 · 주식결제 성분",
        "24 방법2 부채몫": "24 콜 최종가치 · 현금결제 성분",
        # 의무보유가 없는 콜 대상 몫 (콜 한도 ≠ 의무보유 비율일 때만 생긴다)
        "15n 무보유 트랜치": "15n 콜 대상 · 의무보유 없음",
        "19n 콜 페이오프 무보유": "19n 콜 즉시행사가치 · 무보유",
        "17an 행사 지분몫 무보유": "17an 행사 주식결제 · 무보유",
        "17bn 행사 채권몫 무보유": "17bn 행사 현금결제 · 무보유",
        "19an 콜 존속 무보유": "19an 콜 존속 · 의무보유 없음",
        "20n 매도청구권 무보유": "20n 매도청구권 · 의무보유 없음",
        "19bn 행사 판단 무보유": "19bn 행사 판단 · 의무보유 없음",
        "21n 지분보유 무보유": "21n 계속보유 주식 · 무보유",
        "22n 부채보유 무보유": "22n 계속보유 현금 · 무보유",
        "23n 지분몫 무보유": "23n 최종가치 주식 · 무보유",
        "24n 부채몫 무보유": "24n 최종가치 현금 · 무보유",
        "IR 표지": "IR 이자율 산출요약",
        "σ 표지": "σ 주가변동성 산출요약",
        "σr 표지": "σr 금리변동성 산출요약",
    }
    if bw_cash(tm):
        # 현금납입 BW — 05·06 은 주식결제·현금결제 구분이 아니라 신주인수권과 사채 자체다.
        m["05 지분가치"], m["06 부채가치"] = "05 신주인수권가치", "06 사채가치"
    kw = f"{tm.k_w:.0%}" if tm.k_w > 0 else ""
    if kw and not issuer_redeem(tm):
        m[f"15 {kw} 트랜치"] = f"15 매도청구 대상 {kw}"
    return m


def rename_sheets(wb, mapping: dict, words=()):
    """시트 이름을 바꾸고, 그 이름을 쓰는 수식·글자·하이퍼링크를 함께 고친다. 값은 그대로다.

    words — 상품 용어 치환(BW 의 전환→행사 등). 설명 문구는 relabel_inst 가 바꾸므로
    시트 이름에도 같은 치환을 해야 「02 행사가격」 같은 참조 문구와 탭 이름이 맞는다.
    """
    mapping = dict(mapping)
    if words:
        for nm in wb.sheetnames:
            cur = mapping.get(nm, nm)
            for a, b in words:
                cur = cur.replace(a, b)
            mapping[nm] = cur
    mapping = {o: nn for o, nn in mapping.items() if o in wb.sheetnames and o != nn}
    for o, nn in mapping.items():
        assert len(nn) <= 31 and not any(ch in nn for ch in r":\/?*[]"), nn
        wb[o].title = nn
    order = sorted(mapping, key=len, reverse=True)
    for ws in wb.worksheets:
        for c in list(ws._cells.values()):
            v = c.value
            if isinstance(v, str):
                nv = v
                for o in order:
                    if o in nv:
                        nv = nv.replace(o, mapping[o])
                if not nv.startswith("="):
                    nv = nv.replace("**", "")      # 화면용 굵은 글씨 기호는 엑셀에 찍지 않는다
                if nv != v:
                    c.value = nv
            if c.hyperlink is not None and c.hyperlink.location:
                loc = c.hyperlink.location
                for o in order:
                    loc = loc.replace(o, mapping[o])
                c.hyperlink.location = loc
    return wb


# 상품 이름을 바꾸면 안 되는 말 — 세 상품을 나란히 적은 적용범위 문장, 기준서 용어.
# 엑셀에서 고쳐도 되는 입력 칸의 색. 가정 시트·산출내역·주주간계약 조서가 같은 색을 써야
# 해설의 «노란 칸» 이 한 가지 뜻이 된다.
INPUT_FILL = "FDF6DD"
_RELABEL_KEEP = ("전환사채·신주인수권부사채·상환전환우선주", "전환채무상품", "상환전환우선주")
# 신주인수권부사채 조서에서 전환사채와 **견주는** 말. 바꾸면 「신주인수권부사채와 같은」이 되어 뜻이 없다.
_BW_KEEP = ("전환사채의 전환과 같은", "전환사채와 같은")
# 받침에 따라 모양이 바뀌는 조사. 뒤에 띄어쓰기·문장부호·줄끝이 올 때만 조사로 본다.
_JOSA = (("은", "는"), ("이", "가"), ("을", "를"), ("과", "와"), ("으로", "로"))
_JOSA_RE = re.compile(r"(은|는|이|가|을|를|과|와|으로|로)(?=[\s,.·)」』—:;]|$)")


def _batchim(ch: str):
    """한글 마지막 글자의 받침 — (있는가, ㄹ 받침인가). 한글이 아니면 None."""
    code = ord(ch) - 0xAC00
    if not 0 <= code < 11172:
        return None
    jong = code % 28
    return jong != 0, jong == 8


def fix_josa(text: str, word: str) -> str:
    """word 바로 뒤의 조사를 word 의 받침에 맞춘다 (행사은 → 행사는, 상환청구을 → 상환청구를)."""
    b = _batchim(word[-1]) if word else None
    if b is None or word not in text:
        return text
    has, rieul = b
    out, i = [], 0
    while True:
        j = text.find(word, i)
        if j < 0:
            out.append(text[i:]); break
        k = j + len(word)
        out.append(text[i:k])
        m = _JOSA_RE.match(text, k)
        if m:
            got = m.group(1)
            for with_b, without_b in _JOSA:
                if got in (with_b, without_b):
                    if with_b == "으로":
                        right = "로" if (not has or rieul) else "으로"
                    else:
                        right = with_b if has else without_b
                    out.append(right); k = m.end(); break
        i = k
    return "".join(out)


def relabel_text(v: str, words) -> str:
    """한 글자 칸을 상품 용어로 바꾼다 — 지켜야 할 말은 가려 두고, 바꾼 말 뒤 조사를 고친다."""
    keep = {}
    for n_, w in enumerate(_RELABEL_KEEP + (_BW_KEEP if ("전환사채", "신주인수권부사채") in words else ())):
        if w in v:
            tok = f"\x00{n_}\x00"; keep[tok] = w; v = v.replace(w, tok)
    used = []
    for a, b in words:
        if a in v:
            v = v.replace(a, b); used.append(b)
    for b in used:
        v = fix_josa(v, b)
    for tok, w in keep.items():
        v = v.replace(tok, w)
    return v


def relabel_inst(wb, tm: Terms):
    """조서의 글자 셀을 그 상품의 용어로 바꾼다. 값·수식은 그대로다."""
    words = inst_words(tm)
    if not words: return
    for ws in wb.worksheets:
        for c in list(ws._cells.values()):
            v = c.value
            if not isinstance(v, str) or v.startswith("="): continue
            if v.strip() in _DECISIONS: continue
            nv = relabel_text(v, words)
            if nv != c.value: c.value = nv


def _stamp(tm: Terms, kind: str = "") -> str:
    """조서를 만든 시점의 인풋 지문.

    조서는 트리·배분·상각표가 한 계약에서 나와야 성립한다. 만든 뒤 인풋이
    바뀌면 예전 파일은 더 이상 그 계약의 조서가 아니므로 내주지 않는다.
    """
    import hashlib
    # 원본 시나리오 지문은 «계산에 쓰이지 않는 기록» 이라 뺀다. 넣으면 같은 계약을
    # 파일에서 열었는지 손으로 넣었는지에 따라 계산 지문이 갈린다.
    d = {k: v for k, v in asdict(tm).items() if k != "scen_md5"}
    return hashlib.md5(
        (kind + json.dumps(d, sort_keys=True, default=str)).encode()).hexdigest()


def scen_stamp(obj: dict) -> str:
    """저장·불러온 **원본 시나리오 JSON** 의 지문. ``_meta`` 는 빼고 잰다."""
    import hashlib
    d = {k: v for k, v in (obj or {}).items() if not str(k).startswith("_")}
    return hashlib.md5(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


_RUN_ENV: dict = {}


def _run_env() -> dict:
    """app.py 의 해시와 git HEAD. 한 번만 재고 못 재면 빈 문자열이다.

    ``tests/run_all.py`` 가 검증 결과에 찍는 것과 **같은 방식**으로 만든다 —
    조서에 실린 해시와 검증 보고서의 해시를 눈으로 대조할 수 있어야 한다.
    """
    if _RUN_ENV: return _RUN_ENV
    import hashlib, subprocess
    pth = globals().get("__file__") or ""
    if not pth or not os.path.exists(pth):
        for c in (os.path.join(os.getcwd(), "valuation", "legacy.py"), "valuation/legacy.py"):
            if os.path.exists(c): pth = c; break
    sha = head = ""
    try:
        sha = hashlib.sha256(open(pth, "rb").read()).hexdigest()[:12]
    except Exception:
        pass
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=(os.path.dirname(os.path.abspath(pth)) or "."),
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    _RUN_ENV.update(app_sha12=sha, git_head=head)
    return dict(_RUN_ENV)


def run_stamp(tm: Terms, kind: str = "") -> dict:
    """이 조서를 **다시 만들 수 있게** 하는 최소 기록.

    조서에는 지금까지 생성시각과 출처 문자열뿐이었다. 몇 달 뒤 같은 계약을 다시 재서
    값이 다르면 앱이 바뀐 것인지 인풋이 바뀐 것인지 가릴 방법이 없었다. 그래서 앱 해시 ·
    git HEAD · 평가체계 버전 · 인풋 지문을 함께 남긴다.

    ``terms_md5`` 는 **계산에 실제로 쓴 Terms** 의 지문이다 (``_stamp`` 과 같은 함수).
    수식 조서는 조정일 처리를 바꾼 사본으로 트리를 만들므로 그 사본을 넘겨야 한다.
    """
    e = _run_env()
    _n = max(1, int(tm.n))
    return dict(app_sha12=e["app_sha12"], git_head=e["git_head"], schema=SCHEMA_VER,
                terms_md5=_stamp(tm, kind), scen_md5=(tm.scen_md5 or ""),
                grid_days=float(tm.grid_days), gap_req=float(tm.grid_days*12/365 if tm.grid_days else tm.gap_m), gap_act=float(tm.rem_m)/_n,
                dt_yr=float(tm.T)/_n, d_base=tm.d_base,
                s0_date=(tm.s0_date or ""), px_src=(tm.s0_src or "직접 입력"),
                cr_src=(tm.cr_src or "직접 입력"), sig=float(tm.sig), n=int(tm.n),
                gap_m=float(tm.gap_m), model=str(tm.model), carry=int(tm.carry),
                rfx_mode=int(tm.rfx_mode), acc_basis=int(getattr(tm, "acc_basis", 1)),
                k_method=int(tm.k_method),
                made_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"))


def stamp_rows(tm: Terms, kind: str = "") -> list:
    """``run_stamp`` 를 조서에 실을 ``[(항목, 문구)]`` 로 바꾼다. 세 조서가 같이 쓴다."""
    m = run_stamp(tm, kind)
    return ([("회차", tm.tranche)] if getattr(tm, "tranche", "") else []) + [
            ("생성시각", m["made_at"]),
            ("앱 버전 식별값 (app.py 앞 12자리)", m["app_sha12"] or "확인 못 함"),
            ("계산 코드 버전 (git)", m["git_head"] or "확인 못 함"),
            ("평가체계 버전", f"{m['schema']}"),
            # 두 지문은 다른 것을 가리킨다. 다른 것이 정상이다 — 원본을 열면
            # derive() 가 경과기간·노드 수를 채우고 compat 가 지원하지 않는 조합을
            # 되돌리므로, 실제로 계산에 들어간 Terms 는 파일과 같지 않다.
            ("입력파일 식별값 (불러온 원본)",
             (m["scen_md5"] or "해당 없음 — 화면에서 직접 입력")),
            ("계산 입력 식별값 (적용값 기준)", m["terms_md5"]),
            ("노드 — 요청 간격 · 실제",
             f"요청 {m['gap_req']:g}개월 · 노드 {m['n']}개 · "
             f"실제 간격 {m['gap_act']:,.3f}개월 · Δt {m['dt_yr']:,.4f}년"),
            ("평가기준일 · 주가 거래일", f"{m['d_base']} · {m['s0_date'] or '해당 없음'}"),
            ("주가 출처", m["px_src"]),
            ("위험 곡선 출처", m["cr_src"]),
            ("변동성 σ", f"{m['sig']:.4f}")] + (_sha_stamp(tm) if getattr(tm, "inst", "") == "SHA" else [
            ("신용위험 처리 · 전환가격 조정 · 조정일 아닌 시점 처리 · 행사금액 경과기간",
             f"{m['model']} · "
             + {0: '조정 없음', 1: '하향만', 2: '하향·상향'}.get(int(m['rfx_mode']), str(m['rfx_mode'])) + " · "
             + ({1: '경로가중 근사', 2: '위험중립확률 가중 근사', 3: '특정노드 선택 근사'}.get(int(m['carry']), str(m['carry']))
                if int(m['rfx_mode']) else '해당 없음') + " · "
             f"{'계약 개월÷12' if m['acc_basis'] else 'Actual/365'}"),
            ("매도청구권 평가방법", K_METHODS[int(m["k_method"])]),
            ("평가 관점", view_text(tm))])


def _sha_stamp(tm: Terms) -> list:
    """주주간계약 조서의 기록 행 — 사채가 없으니 신용위험 모형·전환가격·매도청구권 방법 대신 계약조건을 적는다."""
    return [("풋 할인 · 가격 가산 경과기간",
             ["무위험 곡선", "위험 곡선", f"무위험 + {tm.sha_spread:.2%}"][int(tm.sha_disc)] + " · "
             + ("계약 개월÷12" if int(getattr(tm, "acc_basis", 1)) else "실제 일수÷365")),
            ("한쪽 행사 시 같은 물량의 상대 권리 · 동시 행사 우선권",
             ("소멸 (같은 주식 물량은 연계 판단)" if int(tm.sha_kill) else "존속 (각자 판단)") + " · "
             + ("풋 권리자 우선" if int(tm.pc_order) == 0 else "콜 권리자 우선")),
            ("풋 매수 의무자", ["콜 권리자(상대 주주)", "대상회사(발행회사)", "상대 주주 · 대상회사 연대"][int(tm.sha_writer)])]


def inst_text(tm: Terms, text: str) -> str:
    """화면 문장을 상품 용어로. CB 면 그대로다."""
    if not isinstance(text, str): return text
    # 조서 글자 칸과 같은 치환(지킬 말 · 조사 고침)을 쓴다 — 따로 바꾸면 「전환사채의 전환과 같은」 이
    # 「신주인수권부사채의 행사과 같은」 이 된다.
    words = inst_words(tm)
    return relabel_text(text, words) if words else text


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
                # 화면에서 고른 조회 종료일 (피어로 산출했으면 피어 칸의 날짜)
                asof=dt.date.fromisoformat(o.get("asof") or tm.d_base), kind="stock",
                wb=wb, prefix="σ ")
            # 산출값과 적용값이 다르면 잇지 않는다. 이으면 조서가 화면과 다른
            # σ 로 다시 계산되어 「값 조서 = 수식 조서」가 깨진다. 그 경우에도
            # 시트는 남으므로 산출근거와 차이는 조서에 그대로 보인다.
            _a = _agg(px[0], o)
            if _a is None or abs(_a - tm.sig) > 1e-12: volref = None
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
            if _a is None or abs(_a - tm.bdt_sig) > 1e-12: rvolref = None
        except Exception:
            rvolref = None
    if ir:
        try:
            irref = build_xlsx_rate(tm, rate_how, wb=wb, prefix="IR ")
        except Exception:
            irref = None
    return volref, rvolref, irref


def build_xlsx(tm: Terms, full, b0, b1, b2, ca, conv, eir, attach=None, *, as_workbook=False, include_review=True):
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
    _nosep = put_in_host(tm)
    # 계약상 개월 → 평가기준일 기준 스텝. 엔진과 같아야 한다 (경과분을 뺀다).
    stp_lo, stp_hi = step_mapper(tm, n, dt_)
    per_ = lambda mth: max(1, int(round(mth*mper)))   # 주기는 뺄 것이 없다
    RF, CR = curves(tm)
    wb = Workbook(); wb.remove(wb.active)
    # 산출내역은 조서를 다 만든 뒤 뒤쪽에 붙인다 (아래 _tail 에서).

    _styles = {}
    def put(ws, r, c, v, *, bold=False, color="000000", fill=None, fmt=None,
            size=10, align=None, border=False):
        cl = ws.cell(row=r, column=c, value=xlfn(v))
        key = (bold, color, fill, fmt, size, align, border)
        if key in _styles:
            cl._style = _styles[key]
        else:
            cl.font = Font(name=F, size=size, bold=bold, color=color)
            if fill: cl.fill = PatternFill("solid", fgColor=fill)
            if fmt: cl.number_format = fmt
            cl.alignment = Alignment(horizontal=align or "general", vertical="center")
            if border: cl.border = BOX
            _styles[key] = cl._style
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
    # 조정일은 엔진과 같은 목록이다 (refix_steps — 계약일마다 그날 이후 첫 노드).
    REFIXC = set(refix_steps(tm, n, dt_))
    cpn_amt = 100*eff_cpn(tm)*tm.ipay/12
    ey = tm.elapsed_m/12                     # 경과 연수 — 행사금액은 발행일부터 붙는다
    EA = exercise_amounts(tm, n, dt_)        # 엔진과 같은 산식에서 나온다
    red = EA["red_val"]          # 엔진과 같은 금액 (배당가능이익 제약이 있으면 현재가치)
    # 행사일은 엔진·수식 조서와 같은 목록에서 온다 (exercise_amounts).
    _pin, _kin = EA["p_on"], EA["k_on"]
    # 행사일이 이자지급일과 겹칠 때 그날 이자를 따로 받는가 (엔진과 같은 스위치).
    # 만기 스텝은 더하지 않는다 — 만기에는 «쿠폰» 열이 따로 더해지기 때문이다.
    _pays = pay_steps(tm, n, dt_)
    _cpn_at = lambda i: cpn_amt*_pays.get(i, 0)
    _pcx = lambda i: (_cpn_at(i) if (i < n and int(getattr(tm, "p_cpn_add", 0))) else 0.0)
    _kcx = lambda i: (_cpn_at(i) if (i < n and int(getattr(tm, "k_cpn_add", 0))) else 0.0)
    def put_amt(i):
        # 배당가능이익 제약이 있으면 엔진과 같은 «실제 지급 일정의 현재가치» 다 (00 배당가능이익 상환).
        return (EA["put_val"](i) + _pcx(i)) if _pin(i) else 0.0
    def call_amt(i, on=True):
        return (EA["call"](i) + _kcx(i)) if (on and _kin(i)) else 999999

    HEAD = ["날짜", "스텝(노드 번호)", "전환 가능 (1=예)", "조기상환 가능 (1=예)", "매도청구 가능 (1=예)",
            "전환가격 조정일 (1=예)", "조기상환금액", "매도청구금액", "쿠폰", "만기상환금액",
            "무위험 선도이자율", "위험 선도이자율", "주가변동성 σ", "상승계수 u", "하락계수 d",
            "위험중립 상승확률 q", "하락확률 1−q"]

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
            g(4, 1 if _pin(i) else 0, N0)
            g(5, 1 if (call_on and _kin(i)) else 0, N0)
            g(6, 1 if i in REFIXC else 0, N0, RED)
            g(7, round(put_amt(i), 4), N2)
            g(8, round(call_amt(i, call_on), 4), N2)
            g(9, round(_cpn_at(i), 4), N2)
            g(10, round(red if i == n else 0.0, 4), N2)
            if i < n:
                g(11, forward_rate(RF, i*dt_, (i+1)*dt_), P2)
                g(12, forward_rate(CR, i*dt_, (i+1)*dt_), P2)
            g(13, tm.sig, P2); g(14, full["u"], N4); g(15, full["d"], N4)
            g(16, full["q"], N4); g(17, 1-full["q"], N4)
        title(W, 18, ttl, span=min(n+1, 14))
        put(W, 18, 3+min(n+1, 14), note + used_by(name), color=GREY, size=9)
        put(W, 19, 2, "하락 횟수 r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
        for i in range(n+1):
            put(W, 19, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                fill=(RFXC if i in REFIXC else LIGHT), border=True)
        for r in range(n+1):
            put(W, R0+r, 2, r, bold=True, size=8, fmt=N0, align="center", fill=LIGHT, border=True)
        put(W, R0+n+2, 2, note, color=GREY, size=9)
        put(W, R0+n+3, 2, "이 시트가 참조하는 시트: " + refs, color=GREEN, size=9)
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
    _SRW = stamp_rows(tm, "값")     # 재현 기록 — 아래 「0. 재현 기록」 절에 싣는다
    # 조서를 받은 사람이 무엇을 재고 무엇을 안 쟀는지 표지에서 알아야 한다.
    put(A, 4, 2, SCOPE_NOTE.replace("**", ""), color=GREY, size=9)
    put(A, 5, 2, UNMODELLED_NOTE, color=AMB, size=9)
    # 이 계약에서 평가에서 뺀 권리 — 비어 있으면 그리지 않는다 (종전 조서와 같다)
    if unmod_text(tm): put(A, 6, 2, unmod_text(tm), color=AMB, size=9)
    # 개월을 날짜와 함께 싣는다 — 계약서(날짜)와 조서(개월)를 서로 대조할 수 있게
    def _md(m, on=True):
        try:
            if on and 0 <= m < 1200:
                return f"{m:,.1f} ({months_to_date(tm.d_issue, m).isoformat()})"
        except Exception:
            pass
        return f"{m:,.1f}"
    # 「0. 재현 기록」 — 이 조서를 다시 만들려면 무엇이 같아야 하는가.
    blocks = [("0. 재현 기록", [(_l, _v, None) for _l, _v in _SRW]),
      ("1. 모형", [("신용위험 처리", tm.model, None),
        ("조정일 아닌 시점", ({1: "경로가중치", 2: "확률가중평균", 3: "특정노드선택"}.get(int(tm.carry), "경로가중치")
                           if tm.rfx_mode > 0 else "해당 없음 (전환가액 조정 없음)"), None),
        ("전환권 회계 분류", "파생상품부채" if tm.conv_class == "liability" else "자본", None),
        ("평가 관점", view_text(tm), None)]),
      ("2. 계약조건", [("발행일", tm.d_issue, None), ("평가기준일", tm.d_base, None),
        ("만기일", tm.d_mat, None), ("경과기간 (개월)", tm.elapsed_m, N2),
        ("평가기준일 주가", tm.S0, N2), ("현재 전환가액", tm.K0, N2),
        ("잔존기간 (년)", tm.T, N4), ("잔존기간 (계약상 개월)", tm.rem_m, N2),
        ("행사금액 경과기간", ("계약 개월 ÷ 12" if int(getattr(tm, "acc_basis", 1))
                              else "Actual/365 (할인기간과 같은 잣대)"), None),
        ("노드 수", tm.n, N0), ("Δt", dt_, N4),
        ("표면이자율", tm.cpn, P2)]
        + ([("우선배당률 기준", div_basis_text(tm), None),
            ("우선배당률 (계산에 쓰는 값 · 발행가 기준)", eff_cpn(tm), '0.0000%')]
           if is_rcps(tm) else [])
        + ([("우선배당 처리", ("발행자 재량 — 부채 현금흐름에서 제외 (1032 AG37)"
                              if int(tm.div_mode) == 1 else
                              "미지급분을 상환가액에 가산 — 전체 부채 · 배당은 이자비용"), None)]
           if is_rcps(tm) else [])
        + [("이자 지급주기 (개월)", tm.ipay, N0),
        ("만기보장수익률", tm.ytm, P2),
        ("보장 복리", ("단리" if int(tm.ytm_cmp) <= 0 else f"연 {int(tm.ytm_cmp)}회 복리"), None),
        ("만기상환금액 지급분 공제", DED_TXT[ded_of(tm, "m")], None),
        ("만기상환금액", EA["red"], N2)]
        + ([("만기상환 가치 — 배당가능이익 지급 일정의 현재가치 (트리 10행)", red, N2)] if dp_active(tm) else [])),
      # 조정 조항이 없으면 주기·최저 조정가액은 계약에 없는 값(앱 기본값)이라 싣지 않는다.
      ("3. 전환가액 조정", ([("조정 방식", ["조정 없음", "하향만", "하향+상향"][tm.rfx_mode], None),
        ("조정 주기 (개월)", tm.rfx_cyc, N2),
        ("최초 조정일 (발행 후 개월)", rfx_first_m(tm), N2), ("조정 시점", rfx_cycle_text(tm), None),
        ("최저 조정가액", tm.floor, N2), ("액면가", tm.par, N2)] if tm.rfx_mode > 0 else
        [("조정 방식", "정기 조정 없음 — 전환가액이 만기까지 그대로다", None)])
        + ([("IPO 조항", "반영" if tm.ipo_on and tm.ipo_px > 0 else "없음", None)]
           + ([("예상 상장 시점 (개월)", tm.ipo_m, N0),
               ("공모가액", tm.ipo_px, N2), ("공모가 배수", tm.ipo_mult, P2),
               ("조정후 전환가격", k_round(tm, tm.ipo_px*tm.ipo_mult), N2),
               ("최소공모가격", tm.ipo_min, N2),
               ("상장 시 강제전환", "예" if tm.ipo_conv else "아니오", None)]
              if (tm.ipo_on and tm.ipo_px > 0) else [])
           if is_rcps(tm) else [])),
      ("4. 옵션", [("전환 시작 / 종료 (개월)", _md(tm.cv_s), None), ("　", _md(tm.cv_e), None),
        ("조기상환 시작 / 종료 / 주기", _md(tm.p_s, tm.p_s <= tm.p_e), None), ("　", _md(tm.p_e, tm.p_s <= tm.p_e), None), ("　 ", tm.p_f, N0),
        ("조기상환 행사금액 산정", "보장수익률 복리" if tm.p_mode == "accrue" else "고정률", None),
        ("조기상환금액 지급분 공제", (DED_TXT[ded_of(tm, "p")] if tm.p_mode == "accrue"
                                  else "해당 없음 (고정률)"), None),
        ("내재파생 분리 정책",
         ("접근법 1 — 얽힌 권리를 먼저 묶고 판단" if emb_policy(tm) == 1 else
          "접근법 2 — 권리마다 판단한 뒤 분리 대상끼리 묶기"), None),
        ("조기상환권 회계 처리",
         ("주계약에 포함 (분리하지 않음)" if _nosep else "분리 · 파생상품부채"),
         None),
        ("매도청구 시작 / 종료 / 주기", _md(tm.k_s, tm.k_s <= tm.k_e), None), ("　  ", _md(tm.k_e, tm.k_s <= tm.k_e), None), ("　   ", tm.k_f, N0),
        ("매도청구금액 지급분 공제", DED_TXT[ded_of(tm, "k")], None),
        ("매도청구 프리미엄", tm.k_prem, P2),
        ("매도청구 복리 횟수 (연)", tm.k_cmp, N0), ("매도청구 한도", tm.k_w, P2),
        ("의무보유 (개월)", _md(tm.k_lock), None),
        ("의무보유 물량 (콜 대상 안)", (f"{lock_share(tm):.4g} — 콜 대상 {tm.k_w:.4g} 가운데 나머지는 의무보유 없음"
                                    if lock_share(tm) < tm.k_w - 1e-12 else "콜 대상 전부"), None),
        ("매도청구권 평가방법", K_METHODS[tm.k_method], None),
        ("지분·채권 구분 기준", (K_SPLITS[int(tm.k_split)] if tm.k_method else "해당 없음 (유무가치비교법)"), None),
        ("콜 대상물량 의무보유", (K_HOLDS[int(tm.k_hold)] if tm.k_method else "유무가치비교법은 격자에서 직접 반영"), None),
        ("콜 권리자", CALL_HOLDERS[call_holder(tm)], None),
        ("콜옵션 유형", (K_KINDS[int(tm.k_kind)] if call_holder(tm) else "해당 없음 — 발행회사 본인만 행사"), None),
        ("평가기법", " · ".join(v for k, v in call_method_rows(tm)[:2]) + " — 상세는 결과 시트", None),
        ("풋·콜 우선순위 (조기상환과 매도청구 사이)", pc_order_text(tm), None),
        ("매도청구 통지 뒤 전환 대응", conv_resp_text(tm), None),
        ("매도청구권 회계 처리",
         "별도 금융상품" if tm.k_sep else "복합내재파생에 포함", None)]),
      ("5. 시장 인풋", [(_l, _v, None) for _l, _v in px_trace(tm)] + [
        ("위험 곡선 출처", (tm.cr_src or "직접 입력") + (f" · 평가대상 {tm.rt_tgt}" if tm.rate_mode != "direct" and tm.rt_tgt else ""), None),
        ("BDT σ 출처", (tm.rvol_how or "직접 입력") if put_bdt_on(tm) else "해당 없음 (BDT 미적용)", None),
        ("변동성 σ", tm.sig, P2),
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
        "위로 갈수록 주가 상승 횟수가 많은 경로다.", "가정")
    fill_tree(T01, lambda i, r: round(S(i, i-r), 2), N2)

    # 전환가격을 바꾸는 조항(리픽싱·상장 조정)이 없으면 전환가격은 모든 노드에서 같다 —
    # 02·03 트리를 싣지 않는다 (수식 조서와 같은 기준).
    _kconst = tm.rfx_mode == 0 and not (is_rcps(tm) and tm.ipo_on and tm.ipo_px > 0)
    if not _kconst:
        T02 = newsheet("02 전환가격", "② 전환가격트리  조정일에는 조정 규칙으로 새 전환가격을 정하고, 아니면 직전 값을 이어받는다",
            "조정일 열은 주황색이다.", "01 · 가정")
        fill_tree(T02, lambda i, r: (round(node(i, r)["K"], 2) if node(i, r) else None), N2)

        T03 = newsheet("03 전환비율", "③ 전환주식수트리  (액면 100당) 100 ÷ 전환가격",
            "액면 100 당 받을 주식 수(100 ÷ 전환가격)다. 전환가격이 내려가면 늘어난다.", "02")
        fill_tree(T03, lambda i, r: (round(100/node(i, r)["K"], 4) if node(i, r) else None), N4)

    T04 = newsheet("04 전환가치", "④ 즉시전환가치트리  주가 × 전환주식수",
        ("전환청구기간 밖이면 0이다. 전환가격이 바뀌는 조항이 없어 100 × 주가 ÷ 현재 전환가액이다."
         if _kconst else "전환청구기간 밖이면 0이다."), ("01 · 가정" if _kconst else "01 · 03"))
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
             "⑤ 주식결제분 가치트리  전환해 주식으로 받게 될 부분"),
            ("권면액 100 만큼 현금을 내고 그 가치의 주식을 받는다. 즉시 행사와 계속보유 가운데 큰 값이고, "
             "계속보유가치는 다음 시점 값을 무위험 이자율로 할인한 값이다."
             if _bwc else
             "전환하면 전환가치, 상환하면 0, 계속보유하면 다음 시점 값을 무위험 이자율로 할인한 값이다."),
            "04 · 09 · 다음 열 05")
        fill_tree(T05, lambda i, r: (round(node(i, r)["E"], 2) if node(i, r) else None))

        T06 = newsheet("06 부채가치",
            ("⑥ 사채가치트리  신주인수권 행사와 관계없이 남는 사채" if _bwc else
             "⑥ 현금결제분 가치트리  상환·이자로 현금을 받게 될 부분"),
            ("조기상환금액과 계속보유가치를 비교하고, 매도청구가 행사되면 매도청구금액으로 제한된다."
             if _bwc else
             "전환하면 0, 상환하면 그 금액, 계속보유하면 다음 시점 값을 위험 선도이자율로 할인한 값이다."),
            "09 · 다음 열 06")
        fill_tree(T06, lambda i, r: (round(node(i, r)["B"], 2) if node(i, r) else None))

        T07 = newsheet("07 보유가치", "⑦ 계속보유가치트리  이 시점에 행사하지 않을 때의 값",
            ("신주인수권은 무위험, 사채는 위험 선도이자율로 따로 할인해 더한다."
             if _bwc else
             "주식결제분은 무위험, 현금결제분은 위험 선도이자율로 따로 할인해 더한다 (TF 모형)."),
            "다음 열 05 · 06")
        fill_tree(T07, lambda i, r: (round(node(i, r)["hold"], 2) if node(i, r) else None))

        # 시트 이름·순서는 수식 조서와 같다 — 08 금융상품가치, 09 의사결정.
        T08 = newsheet("09 의사결정",
            ("⑨ 의사결정트리  상환P · 상환C · 보유 — 사채가 어떻게 끝나는가" if _bwc else
             "⑨ 의사결정트리  전환 · 상환P · 상환C · 보유"),
            ("신주인수권 행사 여부는 ⑤ 를 보라 — 그 칸이 «행사가치 − 100» 과 같으면 "
             "그 노드에서 행사한다." if _bwc else
             "주가가 높은 위쪽은 전환, 낮은 아래쪽은 상환이 많다. 매도청구는 그 사이 노드에 나타난다."), "04 · 07")
        lab = {"conv": "전환", "put": "상환P", "call": "상환C", "hold": "보유",
               "mat": "만기상환", "auto": "자동전환", "ipo": "상장전환"}
        fill_tree(T08, lambda i, r: (lab.get(node(i, r)["kind"], "") if node(i, r) else None), txt=True)

        T09 = newsheet("08 금융상품가치",
            ("⑧ 콜 반영 전 가치트리 = 신주인수권 + 사채" if _bwc else
             "⑧ 콜 반영 전 가치트리 = 주식결제분 + 현금결제분"),
            "전환·조기상환·매도청구·계속보유 가운데 선택한 결과의 값이다. 07 과 비교하면 어느 노드에서 행사가 일어났는지 보인다.",
            "05 · 06")
        fill_tree(T09, lambda i, r: (round(node(i, r)["E"]+node(i, r)["B"], 2) if node(i, r) else None))
        wb.move_sheet("08 금융상품가치", offset=wb.sheetnames.index("09 의사결정") - wb.sheetnames.index("08 금융상품가치"))
    else:
        T10 = newsheet("05 GS 전환확률", "⑤ [GS] 전환확률트리  전환 1 · 상환 0 · 계속보유면 다음 시점 전환확률의 위험중립 가중평균",
            "이 확률로 무위험·위험 이자율을 가중한 할인율을 만든다. 다음 시점의 계산 결과를 참조하므로 순환참조가 발생하지 않는다.", "07 · 다음 열 05")
        fill_tree(T10, lambda i, r: (round(node(i, r)["P"], 4) if node(i, r) else None), N4)

        T11 = newsheet("06 GS 할인율", "⑥ [GS] 위험조정할인율트리  y = 확률 × 무위험 + (1−확률) × 위험",
            "전환 가능성이 높은 위쪽 노드는 무위험 이자율에, 아래쪽 노드는 위험 이자율에 가깝다.", "05")
        def gs_rate(i, r):
            o = node(i, r)
            if not o or i >= n: return None
            fr = forward_rate(RF, i*dt_, (i+1)*dt_); fc = forward_rate(CR, i*dt_, (i+1)*dt_)
            return o["P"]*fr + (1-o["P"])*fc
        fill_tree(T11, gs_rate, P2)

        T12 = newsheet("07 GS 금융상품가치", "⑦ [GS] 금융상품가치트리",
            "전환·조기상환·매도청구·계속보유 가운데 선택한 결과의 값이다.", "04 · 06")
        fill_tree(T12, lambda i, r: (round(node(i, r)["V"], 2) if node(i, r) else None))

    # ── BDT (조기상환권을 금리격자로 잴 때만) ──
    if put_bdt_on(tm):
        BP, BV = bdt_grid(tm, True)
        d0 = dt.date.fromisoformat(tm.d_base)
        for nm, ttl, note, grid, rate in (
            ("BDT 단기이자율", "BDT 단기이자율격자  r(i,j) = a · exp(2σ·j·√Δt)",
             "로그정규 분포를 따르므로 단기이자율이 음수가 되지 않는다. j 는 금리 상승 횟수이고 클수록 금리가 높다(주가 트리와 달리 표의 아래쪽이 높은 금리). 기준금리 a 는 이 격자로 계산한 할인계수가 적용 금리곡선의 할인계수와 같아지도록 앱이 역산한 값이다.", None, True),
            ("BDT 부채요소", "BDT 부채요소  전환권 제외 사채 + 조기상환청구권 (금리격자)",
             "MAX(조기상환금액, 계속보유) 를 고른다. 계속보유는 다음 두 칸을 0.5 씩 "
             "가중해 그 노드의 단기이자율로 할인한 값이다.", BV, False),):
            W = wb.create_sheet(nm); W.sheet_view.showGridLines = False
            W.column_dimensions["B"].width = 18
            for i in range(n+1): W.column_dimensions[gl(3+i)].width = 9
            for r, h in enumerate(["날짜", "스텝(노드 번호)", "조기상환 가능 (1=예)", "조기상환금액",
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
                g(5, round(BP["cpn_at"](i), 4), N2)
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
            W.freeze_panes = "C13"

    # ── 이자율곡선 ──
    # IR 시트(입력곡선 → 부트스트래핑 → 선도)를 함께 실으면 같은 내용이라 싣지 않는다.
    _ir_will = bool(attach and attach.get("ir", True) and len(tm.rf_curve) >= 2
                    and len(credit_curve(tm)) >= 2)
    if not _ir_will:
        _cc = credit_curve(tm)      # 실제 적용한 위험 곡선 (두 등급 보간·외삽 반영)
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
        if tm.y_type == "par" and len(_cc) >= 2:
            sec(C, 16, "부트스트래핑 — 위험 곡선", span=6)
            for i, h in enumerate(["만기 (년)", "만기수익률", "할인계수", "현물 (연속)"]):
                put(C, 17, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
            for k, (t_, df) in enumerate([x for x in bootstrap_df(_cc, tm.T, tm.cmp_cr) if x[0] > 0]):
                for j2, v in enumerate([t_, _lin(_cc, t_), df, -math.log(df)/t_]):
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
    steps = [("B0  옵션 없는 사채 — 주계약 공정가치", b0, None, "—"),
             ("B1  조기상환권 추가 — 조기상환권을 포함한 부채요소", b1, b1-b0, "조기상환청구권 (증분가치)"),
             ("B2  전환권 추가 — 매도청구권 반영 전 전환사채 공정가치", b2, b2-b1, "전환권"),
             ("B3  매도청구권 반영 — 매도청구권 반영 후 순평가금액", b2-ca, -ca, f"매도청구권 ({tm.k_w*100:.0f}% 한도)")]
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
            "부채요소에 포함). 위 B3 의 차액은 전체 격자에서 발행자 상환권이 전환에 따른 가치 상승을 제한한 크기다.",
            color=GREY, size=9)
    # 앱에서 고른 모형만 싣는다.
    sec(R, 11, "2. 신용위험 처리 — "
        + ("지분·부채 분리 — 현금납입 BW 는 TF 와 GS 가 같은 값을 낸다" if _bwc else
           "TF · 주식결제분은 무위험, 현금결제분은 위험 이자율로 할인" if _tf else
           "GS · 전환확률로 무위험·위험 이자율을 가중한 할인율"),
        span=5)
    for i, h in enumerate(["모형", "전체", "주식결제분", "현금결제분", "전환확률"]):
        put(R, 12, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    if _tf:
        put(R, 13, 2, "TF", border=True)
        for j2, v in enumerate([fullv["TF"], fullv["E"], fullv["B"]]):
            put(R, 13, 3+j2, v, fmt=N2, align="right", border=True)
        put(R, 13, 6, "", border=True)
    else:
        put(R, 13, 2, "GS (전체 × 전환확률로 나눔)", border=True)
        for j2, v in enumerate([fullv["GS"], fullv["GS"]*fullv["P"], fullv["GS"]*(1-fullv["P"])]):
            put(R, 13, 3+j2, v, fmt=N2, align="right", border=True)
        put(R, 13, 6, fullv["P"], fmt=N4, align="right", border=True)
    if fullv is not full:
        put(R, 14, 2, "트리 시트와 위 표는 발행자 상환권이 걸린 실제 격자다 (B3). "
            "B2 는 상환권을 뺀 참고값이다.", color=GREY, size=9)
    put(R, 15, 2, "앱에서 고른 방법만 싣습니다. 다른 방법의 값은 이 조서에 없습니다.",
        color=GREY, size=9)
    # 숫자가 맞는지 보는 확인(q 범위·하한·배분 합계 등)은 앱이 평가할 때 수행한다 —
    # 이상이 있으면 조서를 만들지 않는다. 조서에는 싣지 않는다.
    al = allocate(tm, full, b0, b1, b2, ca)[0]
    _rk = 17
    _rk = write_pc_rows(R, _rk, tm, put, sec, N4, GREY)
    write_call_rows(R, _rk + 1, tm, full, b2, put, sec, N4, P2, GREY)

    # ── 회계처리 ──
    E = wb.create_sheet("회계처리"); E.sheet_view.showGridLines = False
    for cc, w in (("B", 34), ("C", 14), ("D", 14), ("E", 18), ("F", 18), ("G", 30)):
        E.column_dimensions[cc].width = w
    title(E, 2, "회계처리", span=6)
    put(E, 3, 2, ("복합계약 **전체**를 당기손익-공정가치 측정 금융부채로 지정했으므로 "
                  "내재파생상품을 분리하지 않고 한 줄로 인식한다 (제1109호 문단 4.2.2 · "
                  "4.3.3(3)). 요소별 배분도 유효이자율 상각도 없다. " + call_alloc_note(tm, whole=True)
                  if fvpl_on(tm) else
                  "기업회계기준서 제1032호 문단 31·32 — 부채요소를 먼저 정하고 나머지를 자본에 배분한다. "
                  + call_alloc_note(tm) +
                  "전환권이 부채이면 전환권과 조기상환권은 상호의존적이므로 하나의 복합내재파생상품으로 "
                  "전체로서 측정한다 (제1109호 문단 B4.3.4)."),
        color=GREY, size=9)
    if tm.elapsed_m > 0.01:
        put(E, 4, 2, "※ 평가기준일이 발행일보다 뒤입니다. 아래 배분은 최초 인식용이므로 "
            "결산 회계처리에 그대로 쓰지 마십시오. 결산일에 쓰는 것은 파생상품 공정가치뿐이고, "
            "주계약은 발행일 배분액을 유효이자율로 상각한 장부금액입니다.", color=RED, size=9)
    sec(E, 5, "1. 최초 인식 배분", span=5)
    fac = tm.face_total
    for i, h in enumerate(["항목", "최초 장부금액 (100 기준)", "전액 기준 (원)"]):
        put(E, 6, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    for i, (k, v) in enumerate(al[:-1]):
        put(E, 7+i, 2, k, border=True)
        put(E, 7+i, 3, v, fmt=N2, align="right", border=True)
        put(E, 7+i, 4, v/100*fac, fmt=N0, align="right", border=True)
    rr = 7+len(al)-1
    put(E, rr, 2, "합계", bold=True, fill=BAND, border=True)
    put(E, rr, 3, al[-1][1], bold=True, fill=BAND, fmt=N2, align="right", border=True)
    put(E, rr, 4, al[-1][1]/100*fac, bold=True, fill=BAND, fmt=N0, align="right", border=True)
    for _j, (_nm, _v) in enumerate(alloc_extra(tm, ca)):
        # 합계에 넣으면 100 이 되지 않고 차변에 넣으면 대차가 깨진다 — 합계 밖 참고 줄이다.
        put(E, rr+1+_j, 2, _nm, color=GREY, size=9, border=True)
        put(E, rr+1+_j, 3, _v, fmt=N2, align="right", color=GREY, border=True)
        put(E, rr+1+_j, 4, _v/100*fac, fmt=N0, align="right", color=GREY, border=True)
    rr += len(alloc_extra(tm, ca))
    put(E, rr+1, 2, f"전자등록총액 {fac:,.0f}원 기준으로 환산했습니다.", color=GREY, size=9)
    sec(E, rr+3, "2. 분개", span=5)
    for i, h in enumerate(["계정", "차변 (100)", "대변 (100)", "차변 (원)", "대변 (원)"]):
        put(E, rr+4, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    # 분개는 배분표(al)를 그대로 뒤집어 만든다. 따로 계산하면 두 표가 어긋난다.
    # 음수 항목(매도청구권 자산·최초 인식 손실)만 차변으로, 나머지는 대변으로 간다.
    je = alloc_journal(al)
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
    # 기특정 콜의 참고 문구는 «줄» 이 아니라 이 문장에 이어 붙인다 — 아래 행들이 밀리면
    # 거래원가·기말 재평가 블록의 시작 행이 어긋난다.
    put(E, tr+2, 2, "부채·자본 구성요소를 나누는 배분 자체로는 손익이 생기지 않는다. 거래원가와, 거래가격이 "
        "공정가치와 다를 때의 차이(최초 인식 손익)는 별도 검토 대상이다."
        + "".join("  ※ " + NOTE_KKIND.format(v=f"{_v:,.4f}") for _, _v in alloc_extra(tm, ca)),
        color=GREY, size=9)
    put(E, tr+3, 2, "전환권 분류: " + ("파생상품부채 — 주계약을 잔여로"
        if tm.conv_class == "liability" else "자본 — 전환권대가를 잔여로"), color=GREY, size=9)
    # (아래 tr2 가 거래원가 블록 유무에 따라 다음 절의 시작 행을 정한다)
    tr2 = tr+3
    if tm.issue_cost > 0:
        _cs, _c100 = cost_split(tm, al)
        rc = tr+5
        sec(E, rc, "거래원가 배분 (1032 문단 38)", span=6)
        for i, h in enumerate(["요소", "배분액 (100)", "거래원가 배분액 (100)", "거래원가 배분액 (원)", "처리"]):
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
        tr2 = rs+10
    # ── 최초 인식 대사 — 발행자 (회계기준원 2019-I-KQA018) ──
    # 수식 조서의 같은 절과 같은 줄·같은 값이다 (issuer_day1_rows).
    _d1x = issuer_day1(tm, b0, b1, b2, ca)
    if _d1x:
        rd = tr2+2
        sec(E, rd, "최초 인식 대사 — 발행자 (회계기준원 2019-I-KQA018)", span=5)
        for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
            put(E, rd+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, (k, v) in enumerate(issuer_day1_rows(_d1x)):
            _b = k.startswith("최초 인식 차이 (")
            put(E, rd+2+i, 2, k, border=True, bold=_b, fill=(BAND if _b else None))
            put(E, rd+2+i, 3, v, fmt=N4, align="right", border=True, bold=_b, fill=(BAND if _b else None))
            put(E, rd+2+i, 4, v/100*fac, fmt=N0, align="right", border=True, bold=_b,
                fill=(BAND if _b else None))
        put(E, rd+2+len(issuer_day1_rows(_d1x)), 2,
            (issuer_day1_note(_d1x) if _d1x["hybrid"] else
             "전환권이 자본이므로 차이는 잔여인 자본요소(전환권대가)에 흡수된다 (1032 문단 31). "
             "최초 인식 손익은 생기지 않는다.").replace("**", ""), color=GREY, size=9)
        _rc = rd+2+len(issuer_day1_rows(_d1x))+2
        for i, h in enumerate(["최초 인식 차이의 세 가지 구분", "처리", "이 평가"]):
            put(E, _rc, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, (k, v, on) in enumerate(issuer_day1_cases(_d1x)):
            put(E, _rc+1+i, 2, k, border=True, bold=bool(on), size=9)
            put(E, _rc+1+i, 3, v, border=True, size=9)
            put(E, _rc+1+i, 4, on, border=True, bold=True, size=9)

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
        for _i, _tx in enumerate(FVPL_NOTE if fvpl_on(tm) else
                                 FV_ONLY_XL if acc_mode(tm) == "fv_only" else HOST_NONPOS_XL):
            put(M, 4+_i, 2, _tx, color=(RED if _i == 0 else GREY),
                bold=(_i == 0), size=(10 if _i == 0 else 9))
            M.cell(row=4+_i, column=2).alignment = Alignment(wrap_text=True,
                                                             vertical="top")
        SP = split_test(tm, full, b0, b1, b2, ca, [])
    else:
        r_eir, rows_eir, redm, nper = eir
        title(M, 2, "주계약 상각표", span=7)
        put(M, 3, 2, "지급일은 계약상 일정이므로 발행일부터 센다. 회차 수는 노드가 아니라 "
            "이자 지급주기를 따른다."
            + ("  ※ 조기상환권을 분리하지 않으므로 기대만기 = 첫 조기상환 가능일, 만기 현금흐름 = 그 시점 "
               "행사금액이다. 계약만기로 굴리면 첫 조기상환일의 행사금액과 장부금액이 벌어져 이자비용·부채가 "
               "과소계상된다 (B4.3.5(5)(가)). " + EXPECT_B546 if eir_expect(tm) is not None else ""),
            color=GREY, size=9)
        sec(M, 4, "유효이자율 역산", span=7)
        for i, (k, v, fm) in enumerate([("주계약 (인식액, 거래원가 차감 후)"
                                        if tm.issue_cost > 0 else "주계약 (인식액)",
                                        rows_eir[0][2] if rows_eir else b0, N2),
                                        (("기대만기 상환금액 (첫 조기상환 가능일 행사금액)"
                                          if eir_expect(tm) is not None else "만기상환금액"), redm, N2),
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
        _exv = eir_expect(tm)
        if _exv is not None: _dm = months_to_date(_di, _exv[2])     # 기대만기 = 첫 조기상환 가능일
        for i, row in enumerate(rows_eir):
            last = (i == len(rows_eir)-1); fl = BAND if last else None
            # 마지막은 만기일(기대만기), 나머지는 발행일 + 회차 × 지급주기다.
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
    _r = write_exdate_rows(J, _r, tm, put, sec, GREY, LIGHT)
    for _k, _nm in (("put", "조기상환청구권"), ("call", "매도청구권")):
        _d = SP[_k]
        sec(J, _r, _nm, span=2); _r += 1
        put(J, _r, 2, "결론", bold=True, border=True)
        put(J, _r, 3, _d["결론"], bold=True, border=True); _r += 1
        # 적용 회계정책 · 선택한 처리 — 판정(결론)과 이 조서가 실제로 한 처리를 나눠 적는다.
        if _d.get("있음", True):
            for _a, _v in split_policy_rows(tm, _k):
                put(J, _r, 2, _a, border=True); put(J, _r, 3, _v, border=True); _r += 1
            _cmpx = split_compare(tm, _k, _d)
            put(J, _r, 2, "판정과 설정 비교", bold=True, border=True)
            put(J, _r, 3, _cmpx, bold=True, border=True,
                color=(RED if "검토 필요" in _cmpx else "000000")); _r += 1
        for _i, _x in enumerate(_d["이유"]):
            put(J, _r, 2, "판단 근거" if _i == 0 else "", border=True)
            put(J, _r, 3, _x, border=True); _r += 1
        put(J, _r, 2, "기준서", border=True)
        put(J, _r, 3, " · ".join(_d["근거"]) or "—", border=True); _r += 1
        put(J, _r, 2, "평가방법", border=True)
        put(J, _r, 3, _d["평가"].replace("**", ""), border=True); _r += 1
        if _d["지표"]:
            put(J, _r, 2, "분리 검토용 수치", bold=True, border=True)
            put(J, _r, 3, SPLIT_NUM_NOTE, color=GREY, size=9, border=True); _r += 1
        for _a, _v in _d["지표"].items():
            put(J, _r, 2, _a, border=True)
            put(J, _r, 3, (f"{_v*100:.1f}%" if _a in ("차이", "가장 큰 차이") else
                           f"{_v*100:g}%" if _a.startswith("비교기준") else
                           _v if isinstance(_v, str) else
                           ("예" if _v is True else "아니오" if _v is False
                            else f"{_v:,.4f}")), border=True); _r += 1
        if _k == "put" and _d["지표"] and _d.get("회차"):
            # 행사일마다 견준 표 — 수식 조서의 «행사일별 비교표» 와 같은 열·같은 값이다.
            _r += 1
            sec(J, _r, "B4.3.5(5)(가) 행사일별 행사금액과 상각후원가 비교 (값 — 수식 조서와 같은 표)", span=6); _r += 1
            for _j, _h in enumerate(SPLIT_DATE_COLS):
                put(J, _r, 2+_j, _h, bold=True, fill=LIGHT, align="center", border=True, size=9)
            _r += 1
            for _m, _pv, _bv, _g in _d["회차"]:
                for _j, (_v, _f) in enumerate(((round(_m, 6), N2), (max(0.0, (_m - tm.elapsed_m)/12), N4),
                                                (_pv, N4), (_bv, N4), (_g, P2))):
                    put(J, _r, 2+_j, _v, fmt=_f, align="right", border=True,
                        color=(RED if _j == 0 else "000000"))
                _r += 1
            put(J, _r, 2, "차이 = |행사금액 − 상각후원가| ÷ 상각후원가. 가장 큰 차이로 판정한다 — "
                          "첫 행사일 하나만 보면 뒤로 갈수록 벌어지는 계약을 놓친다 (실무사례 28쪽).",
                color=GREY, size=9); _r += 1
        _r += 1
    # 판정과 실제 회계처리 설정이 어긋나면 이 조서 안에서 「분리 판단」 시트와
    # 「회계처리」 시트가 서로 다른 말을 하게 된다. 그 사실을 여기 적어 둔다.
    _mis = [(_nm, SP[_k]) for _k, _nm in
            (("put", "조기상환청구권"), ("call", "매도청구권"))
            if not SP[_k].get("설정일치", True)]
    sec(J, _r, "판정과 회계처리 설정이 맞는가", span=6); _r += 1
    if _mis:
        for _nm, _d in _mis:
            _set = ((("주계약에 포함 (분리하지 않음)" if put_in_host(tm) else "분리 · 파생상품부채")
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
                  "상각후원가는 문단 B4.3.5(5) 말미대로 **자본요소를 분리하기 전** "
                  "금액에서 출발하므로 분리 여부 설정과 무관하다. 비교기준(평가자 설정)은 기준서가 정한 "
                  "수치가 아니며, 이 판정은 수치 비교 결과이고 분리 여부는 계약 조건과 함께 평가자가 판단한다.",
        color=GREY, size=9)
    for _row in J.iter_rows(min_row=5, max_row=_r, min_col=3, max_col=3):
        for _c in _row: _c.alignment = Alignment(wrap_text=True, vertical="top")

    # ── 해설 ──
    if holder_on(tm):
        # 투자자 관점 — 배분·상각표 대신 공정가치 측정과 투자자 분개. 화면과 같은 함수.
        write_holder_sheet(wb, tm, holder_rows(tm, full, b0, b1, b2, ca),
                           put, sec, title, N4, N0, LIGHT, RED, GREY)
    elif acc_mode(tm) == "fv_only":
        # 최초 인식 배분·분개·거래원가 표를 지우고 공정가치만 남긴다 — 세 경로(화면·값·수식)가
        # acc_mode 하나로 같은 판단을 한다. 결산 분개에 최초 인식 숫자가 옮겨 가는 것을 막는다.
        _ei = wb.sheetnames.index("회계처리"); wb.remove(wb["회계처리"])
        E = wb.create_sheet("회계처리", _ei); E.sheet_view.showGridLines = False
        for cc, w in (("B", 46), ("C", 16), ("D", 18)): E.column_dimensions[cc].width = w
        title(E, 2, "회계처리 — 공정가치 산출 전용", span=3)
        for _i, _tx in enumerate(FV_ONLY_XL):
            put(E, 4+_i, 2, _tx, color=(RED if _i == 0 else GREY), bold=(_i == 0), size=(10 if _i == 0 else 9))
            E.merge_cells(start_row=4+_i, start_column=2, end_row=4+_i, end_column=4)
            E.cell(row=4+_i, column=2).alignment = Alignment(wrap_text=True, vertical="top")
            E.row_dimensions[4+_i].height = 15 if _i == 0 else 42
        sec(E, 8, "평가기준일 공정가치", span=3)
        for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
            put(E, 9, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, (k, v) in enumerate(fv_only_rows(tm, full, b0, b1, b2, ca)):
            put(E, 10+i, 2, k, border=True)
            put(E, 10+i, 3, v, fmt=N4, align="right", border=True)
            put(E, 10+i, 4, v/100*tm.face_total, fmt=N0, align="right", border=True)

    H = wb.create_sheet("해설", 0); H.sheet_view.showGridLines = False
    H.column_dimensions["B"].width = 22; H.column_dimensions["C"].width = 96
    title(H, 2, "이 조서를 읽는 법", span=2)
    _tf = tm.model == "TF"
    _flow = ("가정 → 01 주가 → " + ("" if _kconst else "02 전환가격 → 03 전환비율 → ")
             + "04 전환가치 → " + ("05 지분가치 → 06 부채가치 → 07 보유가치 → 08 금융상품가치 → 09 의사결정"
                                  if _tf else "05 GS 전환확률 → 06 GS 할인율 → 07 GS 금융상품가치")
             + " → 결과 → 회계처리 → 상각표 → 분리 판단")
    ex = [("시트 순서", ""),
      ("구조", "계산 단계 하나가 시트 하나입니다. " + _flow + "."),
      ("따라가기", "시트 탭을 왼쪽부터 차례로 보면 계산이 쌓이는 순서와 같습니다."),
      ("", ""),
      ("각 계산 시트의 공통조건 1~17행", ""),
      ("1행 날짜", "평가기준일 + 스텝 × 구간 길이(Δt). 계약상 행사일과 대조하는 자리입니다."),
      ("2행 스텝", "평가기준일부터 센 노드 번호입니다."),
      ("3~6행 행사 가능 표시", "전환 · 조기상환 · 매도청구 · 전환가격 조정이 가능한 시점에 1 을 표시합니다."),
      ("7~10행 금액", "조기상환금액 · 매도청구금액 · 이자 · 만기상환금액 (액면 100 기준)."),
      ("11~12행 선도이자율", "무위험 선도이자율과 위험 선도이자율 (한 구간, 연속복리)."),
      ("13~17행 격자 변수", "주가변동성 σ · 상승계수 u · 하락계수 d · 위험중립 상승확률 q · 하락확률 1−q."),
      ("", ""),
      ("행 번호 r 은 하락 횟수", ""),
      ("r = 0", "한 번도 하락하지 않은 경로 — 맨 위, 주가가 가장 높습니다."),
      ("r = 스텝", "매번 하락한 경로 — 맨 아래입니다."),
      ("빈칸", "그 시점에는 존재하지 않는 노드입니다."),
      ("", ""),
      ("계산 순서", ""),
      ("만기부터", "만기 시점에는 이후가 없으므로 전환가치·조기상환금액·만기상환금액만 비교합니다."),
      ("한 시점씩 앞으로", ("07 보유가치가 다음 시점의 05 지분가치·06 부채가치를 할인해 계속보유가치를 구합니다."
                        if _tf else "07 GS 금융상품가치가 다음 시점 값을 06 GS 할인율로 할인해 계속보유가치를 구합니다.")),
      ("그다음", ("09 의사결정이 전환·상환·계속보유 중 하나를 고르고, 그 결정에 따라 05·06 이 정해지며 "
                "08 금융상품가치 = 05 + 06 입니다." if _tf else
                "07 GS 금융상품가치가 전환·상환·계속보유 중 큰 값을 고르고, 05 GS 전환확률이 그 결정을 기록합니다.")),
      ("순환참조가 없는 이유", "각 칸은 같은 시점의 값과 다음 시점의 계산 결과만 참조하므로 순환참조가 발생하지 않습니다."),
      ("", ""),
      ("TF 와 GS", ""),
      ("TF (Tsiveriotis–Fernandes)", "가치를 주식결제분(전환해 주식으로 받을 부분)과 현금결제분(상환·이자로 받을 부분)으로 "
              "나누어, 주식결제분은 무위험 이자율, 현금결제분은 위험 이자율로 할인합니다. "
              "TF 계산 시트에서는 08 금융상품가치 = 05 지분가치 + 06 부채가치가 모든 노드에서 성립합니다."),
      ("GS (Goldman Sachs)", "가치는 하나로 두고, 각 노드의 전환확률로 무위험·위험 이자율을 가중한 할인율로 할인합니다."),
      ("이 조서", f"이 조서는 {tm.model} 로 계산했습니다. 다른 모형의 계산 시트는 싣지 않습니다."),
      ("회계 금액과의 관계", "TF 의 주식결제분·현금결제분은 모형의 할인 구분이며, 회계상 자본요소·부채요소 배분액과 "
              "금액이 다를 수 있습니다. 회계 배분은 결과·회계처리 시트를 보십시오."),
      ("", ""),
      ("노드에서 무엇을 고르는가", ""),
      ("투자자 권리", "전환 · 조기상환청구 · 계속보유 가운데 투자자에게 가장 유리한 것을 고릅니다."),
      ("발행자 권리", ("발행자 상환권 — 발행회사가 정한 금액으로 상환할 수 있는 권리. 행사되면 투자자가 받는 금액이 "
                     "발행자 상환금액으로 제한됩니다." if issuer_redeem(tm) else
                     "매도청구(발행회사 또는 지정 제3자의 매수청구). 행사되면 투자자가 받는 금액이 매도청구금액으로 제한됩니다.")),
      ("적용한 식", decide_formula_text(tm) + "　— " + priority_label(tm)),
      ("우선순위", PRIORITY_NOTE),
      ("같은 가치일 때의 선택 기준", "전환가치가 상환 금액보다 허용오차 넘게 클 때만 전환을 고르고, 같으면 상환을 고릅니다. "
              "허용오차는 1e-9(10억분의 1)이고, 견주는 금액이 1,000 을 넘으면 그 금액의 1e-12 배로 커집니다 — "
              "주가가 아주 높은 노드에서도 반올림 오차가 아니라 이 규칙이 판정을 정하게 하려는 것입니다. "
              "전환가격이 그날 주가로 조정되는 날에는 전환가치가 정확히 100 이 되어 "
              "조기상환금액과 같아질 수 있으므로 기준을 미리 정해 둡니다."),
      ("만기 시점", "매도청구는 없습니다. 전환가치와 현금(MAX(조기상환금액, 만기상환금액) + 이자) 두 가지만 비교합니다."),
      ("", ""),
      ("이 파일의 성격", ""),
      ("값 조서", "앱이 계산한 결과를 값으로 담았습니다. 수식이 아니므로 칸을 바꿔도 다시 계산되지 않습니다."),
      ("다시 계산", "입력을 바꾸려면 앱에서 다시 계산한 뒤 조서를 새로 내려받습니다.")]
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
    rename_sheets(wb, sheet_display_names(tm), _BW_WORDS if is_bw(tm) else ())
    polish_wb(wb)
    relabel_inst(wb, tm)
    if as_workbook: return wb
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue()


def build_xlsx_formula(tm: Terms, full, b0, b1, b2, ca, conv, eir, attach=None, *, as_workbook=False, include_review=True):
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
    _nosep = put_in_host(tm)
    el = tm.elapsed_m
    # 트랜치 이름은 계약의 매도청구 한도에서 나온다. 30/70 으로 굳혀 두면
    # 한도가 다른 사채에서 시트 이름이 계약과 어긋난다.
    if issuer_redeem(tm):
        KW, KW0 = "발행자 상환권 반영", "발행자 상환권 없음(참고)"
    else:
        KW = f"{tm.k_w*100:,.0f}%"
        KW0 = f"{(1-tm.k_w)*100:,.0f}%"
    # 물량 표기 — 콜이 걸리지 않는 쪽과 걸리는 쪽. 발행자 상환권(RCPS)은 물량이 아니라 격자 둘이다.
    _NONCALL, _CALLED = (("발행자 상환권을 뺀 참고 격자", "발행자 상환권을 반영한 격자") if issuer_redeem(tm) else
                         (f"매도청구 대상이 아닌 {KW0} 물량", f"매도청구 대상 {KW} 물량"))
    stp_lo, stp_hi = step_mapper(tm, n, dt_)
    # 의무보유가 미루는 두 시작점 — 엔진의 lock_delay 와 같은 값을 쓴다.
    _lk_cs, _lk_ps = lock_delay(tm)
    # 의무보유가 걸린 마지막 노드 — 엔진과 같은 값 (lock_end_step). 같은 계약일의 마지막
    # 매도청구 노드까지 묶이므로, 전환·조기상환 시작은 그 다음 노드보다 이를 수 없다.
    _LKEND = lock_end_step(tm, n, dt_)
    # 콜 한도와 의무보유 비율이 다르면 의무보유가 없는 몫을 따로 잰다 (엔진 call_mix 와 같은 나눔).
    _LKW = lock_share(tm)
    _split_lock = (tm.k_w > 0 and not issuer_redeem(tm) and int(tm.k_hold) == 1
                   and _LKW < tm.k_w - 1e-12)
    _lk_cs_st = max(stp_lo(_lk_cs), _LKEND + 1)
    _lk_ps_st = max(stp_lo(_lk_ps), _LKEND + 1) if int(tm.k_lock_put) else stp_lo(_lk_ps)
    RF, CR = curves(tm)
    # 조정일은 엔진과 같은 목록이다 (refix_steps). 계약일을 노드에 배정한 결과라 00 격자 공통
    # 6행이 «00 계약일 목록» 에서 찾아 온다 — 행사일(20·27행)과 같다. 날짜를 바꾸려면 앱에서 조서를 다시 만든다.
    _RFXD = refix_steps(tm, n, dt_)
    REFIXSET = set(_RFXD)
    # 전환가격을 바꾸는 조항은 정기 조정(리픽싱)과 상장(IPO) 조정 둘이다. 둘 다 없으면
    # 전환가격은 모든 노드에서 현재 전환가액 그대로라 02·03 트리와 그 입력 줄을 싣지 않는다.
    _ipo_on = bool(is_rcps(tm) and tm.ipo_on and tm.ipo_px > 0)
    _kconst = tm.rfx_mode == 0 and not _ipo_on
    wb = Workbook(); wb.remove(wb.active)
    # 산출내역을 **먼저** 붙여야 트리 11·12행과 가정 시트가 그 셀을 참조할 수 있다.
    _volref = _rvolref = _irref = None
    _attached = []
    if attach:
        _pre = list(wb.sheetnames)
        _volref, _rvolref, _irref = attach_reports(wb, tm, **attach)
        _attached = [x for x in wb.sheetnames if x not in _pre]

    _styles = {}
    def put(ws, r, c, v, *, bold=False, color="000000", fill=None, fmt=None,
            size=10, align=None, border=False):
        cl = ws.cell(row=r, column=c, value=xlfn(v))
        key = (bold, color, fill, fmt, size, align, border)
        if key in _styles:
            cl._style = _styles[key]
        else:
            cl.font = Font(name=F, size=size, bold=bold, color=color)
            if fill: cl.fill = PatternFill("solid", fgColor=fill)
            if fmt: cl.number_format = fmt
            cl.alignment = Alignment(horizontal=align or "general", vertical="center")
            if border: cl.border = BOX
            _styles[key] = cl._style
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
    put(A, 2, 2, "노란 칸을 바꾸면 그 값을 쓰는 계산 시트가 다시 계산됩니다. 흰 칸(앱이 정한 값)과 주황색 칸(고정 산출값)은 "
                 "앱에서 바꾸고 조서를 새로 만들어야 합니다 — 해설 시트의 「재계산 범위」 참고.", color=GREY, size=9)
    spec = [
        # 진짜 날짜로 넣는다. 각 트리 머리 1행이 이 셀로 스텝 날짜를 계산한다.
        ("발행일", "d_issue", dt.date.fromisoformat(tm.d_issue), DATE, True),
        ("평가기준일", "d_base", dt.date.fromisoformat(tm.d_base), DATE, True),
        ("만기일", "d_mat", dt.date.fromisoformat(tm.d_mat), DATE, True),
        ("경과기간 (개월)", "elm", tm.elapsed_m, N2, True),
        ("평가기준일 주가", "S0", tm.S0, N2, True),
        ("주가 출처", "s0src", (tm.s0_src or "직접 입력"), None, True),
        ("위험 곡선 출처", "crsrc", (tm.cr_src or "직접 입력") + (f" · 평가대상 {tm.rt_tgt}" if tm.rate_mode != "direct" and tm.rt_tgt else ""), None, True),
        ("BDT σ 출처", "rvhow", ((tm.rvol_how or "직접 입력") if put_bdt_on(tm) else "해당 없음 (BDT 미적용)"), None, True),
        ("평가 관점 (앱에서 고른 값)", "view", view_text(tm), None, False),
        ("현재 전환가액", "K0", tm.K0, N2, True),
        ("잔존기간 T (년)", "T", tm.T, N4, True),
        # 할인은 Actual/365(T), 행사금액 산정은 계약 개월수다. 두 잣대를 따로 둔다.
        ("잔존기간 (계약상 개월)", "remm", tm.rem_m, N2, True),
        ("행사금액 경과기간 (1 계약 개월÷12 / 0 Actual/365)", "accb",
         int(getattr(tm, "acc_basis", 1)), N0, True),
        # 계약서의 회차별 표를 넣었으면 「00 행사금액표」 시트가 산식보다 앞선다.
        ("조기상환 행사금액표 사용 (1/0)", "psch",
         1 if sched_rows(getattr(tm, "p_sched", ""), tm) else 0, N0, False),
        ("매도청구 행사금액표 사용 (1/0)", "ksch",
         1 if sched_rows(getattr(tm, "k_sched", ""), tm) else 0, N0, False),
        ("노드 수 n", "n", n, N0, True),
        ("Δt", "dt", "@=C{T}/C{n}", N4, False),
        ("표면이자율", "cpn", tm.cpn, P2, True),
        ("이자 지급주기 (스텝)", "ipay", max(1, int(round(tm.ipay*mper))), N0, True),
        # 지급일은 계약(발행일) 기준이다. 평가기준일에서 다시 세면 결산 평가에서
        # 지급일이 밀린다 — 발행일부터 센 계약 지급일로 잡는다.
        ("첫 지급 스텝", "payoff", pay_offset(tm, stp_lo), N0, True),
        ("이자 지급주기 (개월) — 지급일은 앱이 계약일로 정함", "ipaym", tm.ipay, N2, False),
        ("만기보장수익률", "ytm", tm.ytm, P2, True),
        ("만기보장 복리 횟수", "ycm", tm.ytm_cmp, N0, True),
        ("만기상환금액 직접 입력 (%, 음수면 산식)", "matx",
         float(getattr(tm, "mat_amt", -1.0)), N4, True),
        ("만기상환금액", "red",
         # 할증금은 음수가 될 수 없다. 엔진의 accrue_rate 와 같이 0 에서 끊는다.
         # 만기까지의 경과연수도 가정 시트의 «행사금액 경과기간» 을 따른다.
         # 이미 지급한 이자·배당을 빼는 방식은 「만기 지급분 공제」 셀이 고른다 (xl_ded_prem).
         "@=IF(C{matx}>0,C{matx},IF(C{ytm}<=0,100*(1+MAX(0,(C{ytm}-C{cpn})*IF(C{accb}=1,(C{elm}+C{remm})/12,C{T}+C{elm}/12))),"
         "100*(1+" + xl_ded_prem("C{ytm}", "C{cpn}", "C{ycm}",
                                "IF(C{accb}=1,(C{elm}+C{remm})/12,C{T}+C{elm}/12)",
                                "C{mless}", "(C{elm}+C{remm})", "C{ipaym}") + ")))", N2, False),
        ("만기 지급분 공제 (1 이자 붙여 / 2 받은 금액만 / 0 안 뺌)", "mless",
         ded_of(tm, "m"), N0, True),
        ("최저 조정가액", "flr", tm.floor, N2, True),
        ("조정 후 전환가격 원 단위 미만 (0 처리 없음 / 1 절상 / 2 절사)", "rround", int(getattr(tm, "rfx_round", 0)), N0, True),
        ("액면가", "par", tm.par, N2, True),
        # 상향 재조정의 상한은 **최초** 전환가액이다. 이미 하향 조정된 상품을
        # 결산 평가하면 현재 전환가액과 갈리므로 따로 받는다.
        ("리픽싱 상한 (최초 전환가액)", "cap", k_cap(tm), N2, True),
        ("리픽싱 조정일 (앱이 계약일을 노드에 배정)", "rfxd",
         ("기간 중 언제든지 (모든 노드)" if rfx_any(tm) else
          (f"{rfx_cycle_text(tm)} · 격자에 {len(_RFXD)}회 · 첫 조정 "
           + (months_to_date(tm.d_issue, min(v for v in _RFXD.values())).isoformat() if _RFXD else "없음"))),
         None, False),
        ("전환 시작 (스텝)", "cvs", stp_lo(tm.cv_s), N0, True),
        ("전환 종료 (스텝)", "cve", stp_hi(tm.cv_e), N0, True),
        ("조기상환 시작 (스텝) — 행사일은 앱이 계약일로 정함", "pst", stp_lo(tm.p_s), N0, False),
        ("조기상환 종료 (스텝)", "pen", stp_hi(tm.p_e), N0, True),
        ("조기상환 주기 (스텝)", "frq", max(1, int(round(tm.p_f*mper))), N0, True),
        ("조기상환 행사금액", "prate", tm.p_rate, N2, True),
        # 행사일이 이자지급일과 겹칠 때 그날 이자를 «따로» 받는가. 계약이 정한다.
        ("조기상환 행사일 이자 별도지급 (1/0)", "pcadd",
         int(getattr(tm, "p_cpn_add", 0)), N0, True),
        ("매도청구 행사일 이자 별도지급 (1/0)", "kcadd",
         int(getattr(tm, "k_cpn_add", 0)), N0, True),
        ("조기상환 산식 (1 보장수익률 복리 / 0 확정 금액)", "pmode", (1 if tm.p_mode == "accrue" else 0), N0, True),
        ("조기상환 시작 (발행일 기준 개월) — 행사일은 앱이 계약일로 정함", "psm", tm.p_s, N0, False),
        ("조기상환 보장수익률", "pyld", tm.p_yield, P2, True),
        ("보장 복리 (연 회)", "pcmp", tm.p_cmp, N0, True),
        ("조기상환 지급분 공제 (1 이자 붙여 / 2 받은 금액만 / 0 안 뺌)", "pless",
         ded_of(tm, "p"), N0, True),
        ("매도청구 시작 (스텝)", "kst", stp_lo(tm.k_s), N0, True),
        ("매도청구 종료 (스텝)", "ken", stp_hi(tm.k_e), N0, True),
        ("매도청구 주기 (스텝)", "kfrq", max(1, int(round(tm.k_f*mper))), N0, True),
        ("매도청구 프리미엄", "prem", tm.k_prem, P2, True),
        ("매도청구 복리 횟수 (연)", "kcmp", tm.k_cmp, N0, True),
        ("매도청구 지급분 공제 (1 이자 붙여 / 2 받은 금액만 / 0 안 뺌)", "kless", ded_of(tm, "k"), N0, True),
        ("매도청구 한도", "cw", tm.k_w, P2, True),
        # 콜 대상 가운데 의무보유로 묶인 물량 — 콜 한도와 같으면 콜 대상 전부다 (lock_share).
        ("의무보유 물량 (콜 대상 안)", "lkw", lock_share(tm), P2, False),
        # 계약 우선순위는 트리 구조를 정한다. 엑셀에서 바꿔도 수식이 따라오지
        # 않으므로 흰 셀(입력 아님)로 두고 앱에서 고른 것을 적어만 둔다.
        ("풋·콜 우선순위 (조기상환과 매도청구 사이)", "pcord", pc_order_text(tm), None, False),
        # 전환과 매도청구 사이는 따로 정한다 — 우선순위 하나가 전환권까지 바꾸지 않게.
        ("매도청구 통지 뒤 전환 대응", "kresp", conv_resp_text(tm), None, False),
        (f"{KW} 전환 시작 (스텝)", "cv30", _lk_cs_st, N0, tm.k_method == 0),
        # 의무보유는 조기상환청구도 막는다 (k_lock_put). 유무가치비교법의 With 격자가 본다.
        (f"{KW} 조기상환 시작 (스텝)", "pt30", _lk_ps_st, N0, tm.k_method == 0),
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
        ("상품 (0 CB / 1 RCPS / 2 BW)", "inst", 1 if is_rcps(tm) else 2 if is_bw(tm) else 0, N0, False),
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
        ("조정후 전환가격", "ipok", "@=" + xl_k_round("C{ipopx}*C{ipomul}", "C{rround}"), N2, False),
        ("최소공모가격", "ipomin", tm.ipo_min, N2, True),
        ("상장 시 강제전환 (1/0)", "ipocv", int(tm.ipo_conv), N0, True),
        ("매도청구권 평가방법 (0 유무가치 / 1 GS식 전환가중확률할인 / 2 TF식 지분-채권 분리할인)",
         "kmeth", tm.k_method, N0, False),
        ("지분·채권 구분 기준 (0 비례균등차감법 / 1 본문 4.3.3 GS 전환확률)",
         "ksplit", int(tm.k_split), N0, False),
        ("콜 권리자 (앱에서 고른 값)", "kwho", CALL_HOLDERS[call_holder(tm)], None, False),
        ("콜옵션 유형 (0 제3자 지정 가능 / 1 제3자 기특정)", "kkind", int(tm.k_kind), N0, False),
        ("콜 대상물량 의무보유 (1 있음 / 0 없음)", "khold", int(tm.k_hold), N0, False),
        # 의무보유가 살아 있는 마지막 스텝. 없으면 -1 이라 첫 노드부터 소멸 조건이 걸린다.
        ("의무보유 만료 (스텝)", "lockend", _LKEND, N0, False),
        ("의무보유가 조기상환청구도 막음 (1/0)", "lkput", int(tm.k_lock_put), N0, False),
        # 복수 내재파생을 묶는 순서 — 회계정책(한공회 실무사례 30~32쪽). 앱에서 고른 값이다.
        ("내재파생 분리 정책 (1 접근법 1 / 2 접근법 2)", "embap", emb_policy(tm), N0, False),
        ("조기상환권 처리 (1 분리 / 0 주계약에 포함)", "psep", int(tm.p_sep), N0, True),
        # 배분표·상각표가 보는 계산값 — 분리 정책이 막는 조합이면 «주계약에 포함» 을 골라도 0 이다.
        ("조기상환권을 주계약에 포함 (계산값)", "phost",
         "@=IF(AND(C{psep}=0,OR(C{embap}=2,AND(C{eqcls}=1,C{ksep}=1))),1,0)", N0, False),
        ("조기상환 행사금액이 상실이자 보상 수준 (1/0)", "plost", int(tm.p_lost_int), N0, True),
        # 분리 판단의 판정 수식이 이 칸을 본다 — 바꾸면 판정이 따라온다.
        ("풋 분리 판단 비교기준 (평가자 설정 · 기준서가 정한 수치 아님)", "stol", split_tol(tm), P2, True),
        # 전체 지정이면 배분표가 한 줄이 되고 상각표를 만들지 않는다. 트리는
        # 그대로다 — 평가가 아니라 **인식**을 바꾸는 스위치다.
        ("복합계약 전체 당기손익-공정가치 지정", "fvpl",
         ("지정 — 배분표 한 줄 · 상각표 없음 · 거래원가 즉시 비용"
          if fvpl_on(tm) else "지정하지 않음 — 요소별 배분"), None, False),
        ("매도청구권 처리 (1 별도 금융상품 / 0 내재파생 포함)", "ksep", tm.k_sep, N0, True),
        ("신용위험 처리 (0 TF / 1 GS)", "mdl", 1 if tm.model == "GS" else 0, N0, False),
        ("조기상환권 (0 금리고정 / 1 BDT)", "pbdt", 1 if put_bdt_on(tm) else 0, N0, False),
        # 기준금리 a 가 이 σ 와 금리곡선에 맞춰 앱에서 역산한 고정값이라, σ 를 엑셀에서 바꾸면
        # 격자가 곡선과 어긋난다. 그래서 입력칸(노란색)으로 열지 않고 산출내역에도 잇지 않는다.
        ("BDT 변동성 σ (앱에서만 변경)", "bsig", tm.bdt_sig, P2, False),
        ("BDT 기준 (0 위험곡선 / 1 무위험+스프레드)", "bbase", tm.bdt_base, N0, False),
        ("전자등록총액 (원)", "face", tm.face_total, N0, True),
        # 기말 재평가. 음수면 「없음」이다 — 발행 시점 평가.
        ("발행 거래원가 (원)", "cost", tm.issue_cost, N0, True),
        # 발행자 최초 인식 차이 — 1 이면 당기손익(배분표 마지막 줄), 0 이면 이연(주계약에서 차감).
        # 전환권이 부채이고 발행일 평가일 때만 1 이 될 수 있다. 앱에서 고른 값이다.
        ("최초 인식 차이 처리 (0 이연 / 1 당기손익)", "d1pl",
         (1 if (issuer_day1(tm, b0, b1, b2, ca) or {}).get("pl") else 0), N0, False),
        ("전기말 파생상품부채 장부금액 (음수 = 없음)", "pdrv", tm.prev_deriv, N4, True),
        ("전기말 주계약 장부금액 (음수 = 없음)", "phst", tm.prev_host, N4, True),
        ("무위험 (연속, 평탄)", "rfc", RF(tm.T), P2, False)]
    if is_rcps(tm):
        # 계산에 쓰는 배당률은 「계약 배당률 × (재량이면 0)」 이다. 엑셀에서 처리
        # 스위치를 바꾸면 모든 트리와 상환가액 산식이 따라온다.
        _i = next(i for i, x in enumerate(spec) if x[1] == "cpn")
        # 액면 기준 배당률은 발행가 기준으로 옮긴다 — 1주 발행가가 100 이다.
        spec[_i:_i+1] = [
            ("표면이자율 (계약)", "cpnc", tm.cpn, P2, True),
            ("배당률 기준 (0 발행가 / 1 액면가)", "dbas", int(getattr(tm, "div_basis", 0)), N0, True),
            ("1주당 발행가 (원)", "ipx", float(getattr(tm, "issue_px", 0.0)), N2, True),
            ("우선배당 처리 (0 상환가액 가산 / 1 재량)", "dmode", int(tm.div_mode), N0, True),
            ("표면이자율 (계산에 쓰는 값)", "cpn",
             "@=IF(C{dmode}=1,0,C{cpnc}*IF(AND(C{dbas}=1,C{ipx}>0,C{par}>0),C{par}/MAX(1E-9,C{ipx}),1))",
             '0.0000%', False)]
    # 이 계약에 없는 조항의 입력 줄은 싣지 않는다 — 계산에 쓰이지 않는 기본값(최저
    # 조정가액 598 등)이 가정 시트에 남으면 계약 조건처럼 읽힌다. 빠진 줄을 참조하는 식이
    # 남아 있으면 K 에서 KeyError 가 나므로 조서가 조용히 틀어지지 않는다.
    _unused = {"ipay", "payoff"}           # 지급일은 계약일 목록(pay_steps)이 정한다
    if _kconst:
        _unused |= {"flr", "cap", "rfxd", "rfx", "up", "mth", "rround"}
        if not is_rcps(tm):
            _unused.add("par")
    if not _ipo_on:
        _unused |= {"ipoon", "ipos", "ipopx", "ipomul", "ipok", "ipomin", "ipocv"}
    # 행사기간의 끝·주기는 이제 00 격자 공통의 행사일 목록이 정한다 — 스텝 숫자 대신 계약서의
    # 말(날짜·주기·회차)로 한 줄씩 적는다. 식이 쓰는 시작 스텝(cvs·pst …)은 그대로 둔다.
    _unused |= {"pen", "frq", "kst", "ken", "kfrq", "ksch"}
    if not _split_lock:
        _unused.add("lkw")                 # 의무보유 물량 = 콜 한도 — 따로 적을 것이 없다
    _EA0 = exercise_amounts(tm, n, dt_)
    _dd = lambda m: months_to_date(tm.d_issue, m).isoformat()

    def _sched_txt(s_, e_, f_, dates, rows):
        if s_ > e_: return "없음"
        if rows: return f"계약서 회차표 {len(rows)}회 (00 행사금액표)"
        body = f"{_dd(s_)} ~ {_dd(e_)}"
        cnt = len(dates)
        return (body + " · 기간 중 언제든지" if _EA0["p_cont" if dates is _EA0["p_dates"] else "k_cont"]
                else body + f" · {f_:g}개월마다 {cnt}회")
    spec = [x for x in spec if x[1] not in _unused]
    _extra = [("전환청구기간", "txcv",
               (f"{_dd(tm.cv_s)} ~ {_dd(tm.cv_e)}" if tm.cv_s <= tm.cv_e else "없음"), None, False),
              ("조기상환 행사일", "txput",
               _sched_txt(tm.p_s, tm.p_e, tm.p_f, _EA0["p_dates"], _EA0["p_rows"]), None, False)]
    if tm.k_w > 0:
        _extra.append(("매도청구 행사일", "txcall",
                       _sched_txt(tm.k_s, tm.k_e, tm.k_f, _EA0["k_dates"], _EA0["k_rows"]), None, False))
    if _FP0 := pay_steps(tm, n, dt_):
        _extra.append(("이자·배당 지급", "txpay",
                       f"{tm.ipay:g}개월마다 · 평가기준일 뒤 {sum(_FP0.values())}회", None, False))
    spec += _extra
    # ── 구역 — 대상·기준일 → 계약 조건 → 시장자료 → 평가방법 → 격자 → 회계 입력 ──
    _SEC = [("1. 평가 대상 · 기준일", {"d_issue", "d_base", "d_mat", "elm", "T", "remm", "view", "face", "inst"}),
            ("2. 계약 조건 — 이자 · 만기", {"cpn", "cpnc", "dbas", "ipx", "dmode", "ipaym", "txpay", "ytm", "ycm",
                                        "matx", "red", "mless", "accb", "auto"}),
            ("3. 계약 조건 — 전환", {"K0", "txcv", "cvs", "cve", "flr", "rround", "par", "cap", "rfxd", "rfx", "up", "mth",
                                 "ipoon", "ipos", "ipopx", "ipomul", "ipok", "ipomin", "ipocv"}),
            ("4. 계약 조건 — 조기상환 (투자자 풋)", {"txput", "pst", "prate", "pcadd", "pmode", "psm", "pyld", "pcmp",
                                              "pless", "psch"}),
            ("5. 계약 조건 — 매도청구 (콜)", {"kwho", "txcall", "prem", "kcmp", "kless", "cw", "kcadd", "pcord", "kresp", "cv30", "pt30",
                                        "khold", "lockend", "lkput", "lkw"}),
            ("6. 시장자료", {"S0", "s0src", "sig", "divy", "crsrc", "rfc", "bsig", "rvhow", "bbase"}),
            ("7. 평가방법 (앱에서 고른 값 — 여기서 바꿔도 트리가 따라오지 않는다)",
             {"mdl", "eqcls", "kmeth", "ksplit", "kkind", "embap", "psep", "phost", "plost", "stol", "fvpl",
              "ksep", "pbdt"}),
            ("8. 격자 (계산용)", {"n", "dt", "u", "dd", "q", "q1"}),
            ("9. 회계 입력", {"cost", "d1pl", "pdrv", "phst"})]
    if tm.k_w <= 0:
        _SEC[4] = ("5. 매도청구 (콜) — 계약에 없음 · 결과 시트 식이 참조하는 자리만 남겼다", _SEC[4][1])
    _order = {}
    for _si, (_, _keys) in enumerate(_SEC):
        for _k in _keys: _order[_k] = _si
    _spec2 = []
    for _si, (_st, _) in enumerate(_SEC):
        _rows = [x for x in spec if _order.get(x[1], 6) == _si]
        if _rows:
            _spec2.append((_st, None, None, None, False))
            _spec2 += _rows
    spec = _spec2
    ROWN = {key: 3+i for i, (_, key, _, _, _) in enumerate(spec) if key}
    wb.ROWN = dict(ROWN)          # 시험(배선대조)이 가정 행 → 항목을 읽는다. 파일에는 저장되지 않는다.
    K = {key: f"가정!$C${r}" for key, r in ROWN.items()}
    # 코드 칸의 뜻 — D열에 말로 적는다. 스텝 칸은 그 노드의 날짜를 적는다.
    _MEAN = {
        "accb": {1: "계약 개월 ÷ 12", 0: "실제 일수 ÷ 365"},
        "mless": DED_LBL, "pless": DED_LBL, "kless": DED_LBL,
        "pmode": {1: "보장수익률 복리", 0: "확정 금액"},
        "pcadd": {1: "행사일 이자를 따로 준다", 0: "행사금액에 포함"},
        "kcadd": {1: "행사일 이자를 따로 준다", 0: "행사금액에 포함"},
        "psch": {1: "계약서 회차표 사용", 0: "산식 사용"},
        "auto": {1: "만료 시 보통주 자동전환", 0: "없음"},
        "rfx": {1: "있음", 0: "없음"}, "up": {1: "하향·상향", 0: "하향만"},
        "mth": {1: "경로가중치", 2: "확률가중평균", 3: "특정노드 선택"},
        "rround": {0: "처리 없음", 1: "원 단위 미만 절상", 2: "원 단위 미만 절사"},
        "eqcls": {1: "자본", 0: "파생상품부채"}, "inst": {0: "CB", 1: "RCPS", 2: "BW"},
        "kmeth": {0: "콜 유무 가치 비교", 1: "옵션차익 혼합할인율", 2: "옵션차익 성분 분리할인 (주식결제·현금결제)"},
        "ksplit": {0: "가치 구성비율", 1: "전환확률 (본문 4.3.3)"},
        "kkind": {0: "제3자 지정 가능", 1: "제3자 사전 특정"},
        "khold": {1: "있음", 0: "없음"}, "lkput": {1: "막는다", 0: "전환만 막는다"},
        "psep": {1: "분리 (얽힌 권리와 묶어 파생상품)", 0: "주계약에 포함"}, "plost": {1: "예", 0: "아니오"},
        "embap": {1: "접근법 1 — 얽힌 권리를 먼저 묶고 판단", 2: "접근법 2 — 권리마다 판단한 뒤 분리 대상끼리 묶기"},
        "phost": {1: "주계약에 포함", 0: "분리"},
        "d1pl": {0: "이연 (주계약 장부금액에서 차감)", 1: "당기손익 (관측 가능한 시장자료만 사용)"},
        "ksep": {1: "별도 금융상품", 0: "복합내재파생에 포함"},
        "mdl": {0: "TF", 1: "GS"}, "pbdt": {0: "금리 고정 격자", 1: "BDT 금리격자"},
        "bbase": {0: "위험 곡선에 직접", 1: "무위험 + 확정 스프레드"},
        "ipoon": {1: "있음", 0: "없음"}, "ipocv": {1: "강제전환", 0: "없음"},
        "dbas": {0: "발행가 기준", 1: "액면가 기준"}, "dmode": {0: "상환가액에 가산", 1: "재량배당"}}
    _STEPS = {"cvs", "cve", "pst", "cv30", "pt30", "lockend", "ipos"}
    _d0 = dt.date.fromisoformat(tm.d_base)
    # 상장 스텝에서 주가가 최소공모가격을 넘으면 그 자리에서 주식이 된다. IPO 조항이 없으면
    # 그 갈래를 식에서 아예 뺀다 (값은 같다).
    _ipo_hit = lambda L, r: (f"AND({K['ipoon']}=1,{K['ipocv']}=1,{L}$2={K['ipos']},"
                             f"{L}$2>0,'01 주가'!{L}{R0+r}>{K['ipomin']})")
    _ipo_if = lambda L, r, yes, no: (f"IF({_ipo_hit(L, r)},{yes},{no})" if _ipo_on else no)
    # 보장수익률이 0 인 매도청구 갈래에서만 쓴다 — 어느 공제 방식이든 할증금은 0 이다.
    _KC = f"IF({K['kless']}=1,{K['cpn']},0)"
    # 발행일부터 센 계약 개월. 「받은 금액만 공제」가 지급 회차를 여기서 센다.
    _MO = lambda st: f"({K['elm']}+{st}*{K['remm']}/{K['n']})"
    for i, (nm, key, v, fm, inp) in enumerate(spec):
        r = 3+i
        if key is None:                              # 구역 제목
            put(A, r, 2, nm, bold=True, color=NAVY, fill=LIGHT)
            put(A, r, 3, None, fill=LIGHT); put(A, r, 4, None, fill=LIGHT)
            continue
        # 이름 뒤의 코드 설명 괄호(「(1/0)」 「(1 분리 / 0 …)」)는 D열의 뜻으로 옮겼으니 뗀다.
        _nm = nm.split(" (")[0] if (key in _MEAN and " (" in nm) else nm
        put(A, r, 2, _nm, border=True)
        val = v.lstrip("@").format(**ROWN) if (isinstance(v, str) and v.startswith("@")) else v
        put(A, r, 3, val, color=(RED if inp else "000000"),
            fill=(INPUT_FILL if inp else None), fmt=fm, align="right", border=True)
        if key in _MEAN and isinstance(v, (int, float)):
            put(A, r, 4, _MEAN[key].get(int(v), ""), color=GREY, size=9)
        elif key == "prate" and tm.p_mode == "accrue":
            put(A, r, 4, "보장수익률 산식을 쓰므로 이 칸은 쓰지 않는다", color=GREY, size=9)
        elif key in _STEPS and isinstance(v, (int, float)) and 0 <= v <= n:
            put(A, r, 4, (_d0 + dt.timedelta(days=round(v*dt_*365))).isoformat() + " 노드",
                color=GREY, size=9)
    nb = 3+len(spec)+1
    put(A, nb, 2, "노란 셀이 입력값이다. 스텝은 평가기준일부터 센 노드 번호이고 D열에 그 노드의 날짜를 "
        "적었다. 행사금액은 발행일부터 붙는다.", color=GREY, size=9)
    put(A, nb+1, 2, ("선도이자율은 IR 시트에서 수식으로 계산된다 — 입력곡선을 고치면 따라온다." if _irref
                     else "선도이자율은 부트스트래핑 결과라 각 트리 시트 11·12행에 값으로 들어 있다."),
        color=AMB, size=9)
    # 흰 셀 가운데 「방법을 고르는」 것들은 트리 구조 자체를 정하므로 엑셀에서
    # 바꿔도 따라오지 않는다. 그 사실을 여기서 못박아 둔다.
    put(A, nb+2, 2, "흰 셀 가운데 평가방법·신용위험 처리·BDT 관련 줄은 "
        "앱에서 고른 값이다. 이 조서에는 고른 방법의 트리만 들어 있어서 "
        "여기서 숫자를 바꿔도 트리가 따라오지 않는다. 방법을 바꾸려면 앱에서 "
        "바꾸고 조서를 다시 만들어야 한다.", color=RED, size=9)
    # 조서를 받은 사람이 무엇을 재고 무엇을 안 쟀는지 알아야 한다.
    put(A, nb+4, 2, SCOPE_NOTE.replace("**", ""), color=GREY, size=9)
    put(A, nb+5, 2, UNMODELLED_NOTE, color=AMB, size=9)
    if unmod_text(tm): put(A, nb+6, 2, unmod_text(tm), color=AMB, size=9)
    # 주가 조회 기록과 재현 기록. spec «밖» 이라 ROWN 행 번호를 밀지 않는다 —
    # 수식 배선은 그대로다. 지문은 트리를 만든 Terms(tm) 로 뜬다 — 수식 조서는
    # 조정일 처리를 바꾼 사본으로 계산하므로 화면 Terms 와 다를 수 있다.
    # 위 구역에 이미 있는 줄(주가 출처·위험 곡선 출처·변동성·평가 관점)은 다시 싣지 않는다.
    _shown = {"주가 출처", "위험 곡선 출처", "변동성 σ", "평가 관점", "평가기준일 · 주가 거래일",
              "매도청구권 평가방법"}
    _px = [x for x in px_trace(tm) if x[0] not in _shown]
    _st = [x for x in stamp_rows(tm, "수식") if x[0] not in _shown]
    put(A, nb+7, 2, "주가 조회 기록", bold=True, size=9)
    for _i, (_l, _v) in enumerate(_px):
        put(A, nb+8+_i, 2, _l, color=GREY, size=9)
        put(A, nb+8+_i, 3, _v, color=GREY, size=9)
    _r0 = nb+8+len(_px)+1
    put(A, _r0, 2, "재현 기록 — 이 조서를 다시 만들려면 무엇이 같아야 하는가", bold=True, size=9)
    for _i, (_l, _v) in enumerate(_st):
        put(A, _r0+1+_i, 2, _l, color=GREY, size=9)
        put(A, _r0+1+_i, 3, _v, color=GREY, size=9)
    if put_bdt_on(tm):
        put(A, nb+3, 2, "BDT 금리변동성 σ 도 마찬가지다. BDT 격자의 기준금리 a 는 「σ 가 지금 값일 때 "
            "적용 금리곡선과 할인계수가 같아지도록」 앱이 역산한 고정 산출값이다. 이 파일에서 σ 만 바꾸면 "
            "격자는 움직이지만 a 는 그대로라 결과가 적용 금리곡선과 맞지 않는다. σ 를 바꾸려면 앱에서 바꾸십시오.",
            color=RED, size=9)
        # 이 칸을 엑셀에서 바꾸면 기준금리 a 와 어긋난다 — 바뀌면 옆 칸에 바로 경고가 뜬다.
        _rb = ROWN["bsig"]
        put(A, _rb, 4, f'=IF(ABS(C{_rb}-{tm.bdt_sig!r})>1E-12,"※ 앱에서 정한 값({tm.bdt_sig:.2%})과 다릅니다 — 기준금리 a 와 '
                       f'맞지 않으므로 앱에서 다시 평가하십시오","앱에서 정한 값 그대로 (기준금리 a 와 맞음)")', color=RED, size=9)

    HEAD = ["날짜", "스텝(노드 번호)", "전환 가능 (1=예)", "조기상환 가능 (1=예)", "매도청구 가능 (1=예)",
            "전환가격 조정일 (1=예)", "조기상환금액", "매도청구금액", "쿠폰", "만기상환금액",
            "무위험 선도이자율", "위험 선도이자율", "주가변동성 σ", "상승계수 u", "하락계수 d",
            "위험중립 상승확률 q", "하락확률 1−q"]
    ey = el/12

    SSC = "00 행사금액표"          # 계약서의 회차별 표 — 있으면 트리가 여기를 먼저 본다

    # ── 00 행사금액표 ──
    # 계약서가 회차별 금액을 확정 숫자로 준 경우 그 표를 시트로 싣고 트리가 참조한다.
    # 스텝 번호로 찾게 두어 노드 간격이 회차 간격과 어긋나도 어긋난 채로 맞는다 —
    # 어느 스텝이 어느 회차인지는 엔진이 이미 정했고, 그 결과를 그대로 적는다.
    # 엔진이 배정한 «회차 = 스텝» 을 그대로 옮긴다. 여기서 다시 고르면 엑셀과 격자가
    # 다른 회차를 열어 값이 갈린다.
    _EA = exercise_amounts(tm, n, dt_)
    _srow = {}
    for i in sorted(set(_EA["p_steps"]) | set(_EA["k_steps"])):
        _pv = _EA["p_steps"].get(i)
        _kv = _EA["k_steps"].get(i)
        _srow[i] = ((_pv or _kv)[0],
                    (_pv[1] if _pv else None), (_kv[1] if _kv else None))
    if _srow:
        SC = wb.create_sheet(SSC); SC.sheet_view.showGridLines = False
        for cc, w_ in (("B", 10), ("C", 18), ("D", 16), ("E", 16)): SC.column_dimensions[cc].width = w_
        title(SC, 1, "00 행사금액표 — 계약서의 회차별 금액", span=4)
        put(SC, 2, 2, "계약이 확정 숫자를 준 회차다. 트리의 4·5행(행사 가능)과 7·8행(금액)이 "
            "이 표를 «먼저» 보고, 없는 회차만 보장수익률 산식으로 간다.", color=GREY, size=9)
        for j, h in enumerate(["스텝", "발행일부터 개월", "조기상환 (%)", "매도청구 (%)"]):
            put(SC, 3, 2+j, h, bold=True, size=8, fill=LIGHT, border=True)
        for r_, (i, (mo, pv, kv)) in enumerate(sorted(_srow.items()), start=4):
            put(SC, r_, 2, i, fmt=N0, align="center", border=True)
            put(SC, r_, 3, round(mo, 4), fmt=N2, align="right", border=True)
            put(SC, r_, 4, (round(pv, 6) if pv is not None else None), fmt=N4,
                align="right", border=True, color=RED)
            put(SC, r_, 5, (round(kv, 6) if kv is not None else None), fmt=N4,
                align="right", border=True, color=RED)
        SC.sheet_properties.tabColor = RFXC
    _SD = f"'{SSC}'!$B$4:$D${3+len(_srow)}" if _srow else None
    _SE = f"'{SSC}'!$B$4:$E${3+len(_srow)}" if _srow else None
    # 표에서 그 스텝의 금액을 꺼낸다. 빈 칸(그 회차에 그 권리가 없음)이면 0 이 나오므로
    # 값이 있는지는 MATCH 가 아니라 «0 보다 큰가» 로 본다.
    _VL = (lambda st, rng, col: f"VLOOKUP({st},{rng},{col},FALSE)") if _srow else None
    # 표에 없는 스텝의 VLOOKUP 은 #N/A 다. AND 는 인자를 모두 계산하므로 그 오류가
    # 번진다 — IFERROR 로 0 에 떨어뜨려 「표에 있고 그 권리의 금액이 있다」를 한 식으로.
    _pv0 = (lambda st: f"IFERROR({_VL(st, _SD, 3)},0)") if _srow else None
    _kv0 = (lambda st: f"IFERROR({_VL(st, _SE, 4)},0)") if _srow else None

    # ── 행사 가능 여부와 행사금액을 만드는 «한 곳» ──
    # 트리 4·7·5·8행, 16 부채요소, 16c 부채요소, BDT 부채요소가 모두 이것을 부른다.
    # 시트마다 따로 계산하면 계약서 표를 넣은 계약에서 시트마다 다른 금액이 나온다.
    # 행사 가능 표시는 00 격자 공통의 «계약 행사월» 행이 정한다. 그 칸에 숫자가 있으면
    # 그 노드가 계약서의 행사일이다(엔진의 exercise_amounts 가 배정한 그대로).
    # 의무보유로 시작이 늦춰진 트랜치는 자기 시작 스텝(pst_)으로 한 번 더 거른다.
    def x_pflag(st, pst_=None):
        pst_ = pst_ or K["pst"]
        L_ = st.split("$")[0]
        return f"IF(AND(ISNUMBER({COMQ}!{L_}${CROW['pmo']}),{st}>={pst_}),1,0)"

    def x_kflag(st):
        L_ = st.split("$")[0]
        if DPON and issuer_redeem(tm):
            # 발행자 상환권 — 그 해 재원이 상환할 금액 × 한도 이상일 때만 (엔진 DPPlan.call_ok 와 같다)
            return f"IF(AND(ISNUMBER({COMQ}!{L_}${CROW['kmo']}),{DPQ}!{L_}${_DPR['kok']}=1),1,0)"
        return f"IF(ISNUMBER({COMQ}!{L_}${CROW['kmo']}),1,0)"

    # 행사일 이자 — 그날이 이자지급일이고 스위치가 켜져 있으면 행사금액 위에 더 얹는다.
    # 만기 스텝은 빼 둔다. 만기 행은 「쿠폰」 열을 따로 더하므로 두 번 세게 된다.
    # 지급일은 계약일 목록에서 온다 (pay_steps) — 엔진과 같은 노드, 같은 회수.
    _FP = pay_steps(tm, n, dt_)

    def x_cpn(i):
        """스텝 i 에 지급하는 이자·배당 식 — 00 격자 공통 9행을 가리킨다 (계약일 목록에서 센 회수 × 이자).

        지급일이 하나도 없는 계약이면 0 이다."""
        if not _FP: return "0"
        return f"{COMQ}!{gl(3+i)}$9"

    def _cadd(i, key):
        if i >= n or not _FP: return "0"
        return f"IF({K[key]}=1,{x_cpn(i)},0)"

    # 개월로 묻는 자리(상각표의 기대만기 · 분리 판단의 첫 조기상환일)도 표를 먼저 본다.
    _SC = f"'{SSC}'!$C$4:$D${3+len(_srow)}" if _srow else None

    # 발행일부터 mo 개월의 경과연수 — 엔진의 yr_of_month 와 같은 잣대 (가정 「행사금액 경과기간」).
    _xyr = lambda mo: (f"IF({K['accb']}=1,({mo})/12,"
                       f"(({mo})-{K['elm']})/MAX(1E-9,{K['remm']})*{K['T']}+{K['elm']}/12)")

    def x_pamt_month(mo):
        f_ = (f"IF({K['pmode']}=1,"
              f"100*(1+{xl_ded_prem(K['pyld'], K['cpn'], K['pcmp'], '(' + _xyr(mo) + ')', K['pless'], '(' + mo + ')', K['ipaym'])}),"
              f"{K['prate']})")
        if not _srow: return f_
        v = f"IFERROR(VLOOKUP(ROUND({mo},4),{_SC},2,FALSE),0)"
        return f"IF(AND({K['psch']}=1,{v}>0),{v},{f_})"

    # ── 00 격자 공통 — 모든 트리의 머리 17행을 «한 번만» 계산한다 ──
    # 트리 시트는 이 시트를 짧게 참조한다. 행사금액은 아래 보조 행(20~31행)에서
    # 경과기간 → 할증률 → 기지급 공제 → 금액 순서로 한 단계씩 계산한다.
    # 셀 주소는 트리와 같다(C열 = 스텝 0). 트리의 E7 은 이 시트의 E7 과 같은 자리다.
    COM = "00 격자 공통"
    COMQ = f"'{COM}'"
    # 행사금액 계산 과정 — 조기상환(20~26)과 매도청구(27~33)를 따로 편다. 두 권리는
    # 행사일이 다를 수 있으므로 경과기간도 각자의 계약일에서 센다.
    CROW = dict(pmo=20, pyr=21, p1=22, p0=23, ppaid=24, pap=25, pamt=26,
                kmo=27, kyr=28, k1=29, k0=30, kpaid=31, kap=32, kamt=33)
    # ── 00 배당가능이익 상환 — 배당가능이익을 넣었을 때만 만든다 (엔진 DPPlan 과 같은 식) ──
    DPON = dp_active(tm)
    DPS = "00 배당가능이익 상환"; DPQ = f"'{DPS}'"
    if DPON:
        _DPP = DPPlan(tm, n, dt_)
        _DPY = sorted(_DPP.P.items())
        _DPO = _DPP.others
        _dp_r_prof = 10                                   # 배당가능이익 표 첫 줄
        _dp_r_oth = _dp_r_prof + len(_DPY) + 1            # 다른 상품 표 머리줄
        _dp_sb = _dp_r_oth + len(_DPO) + 3                # 스텝 구역 첫 줄 (스텝)
        _DPR = dict(st=_dp_sb, date=_dp_sb+1, cum=_dp_sb+2, amt=_dp_sb+3, pv=_dp_sb+4, kok=_dp_sb+5)
        _dp_blk = 11 + 3*len(_DPO)                        # k 한 해에 쓰는 줄 수 (머리줄 포함)
        _dp_k0 = _dp_sb + 7
        _dp_red0 = _dp_k0 + (_DPP.K + 1)*_dp_blk + 4      # 만기상환 일정 구역
        _DPRED = f"{DPQ}!$C${_dp_red0 - 1}"
    _common = {}
    # ── 00 계약일 목록 — 계약서의 날짜를 노드에 배정하는 과정을 수식으로 편다 ──
    # 정기 조정일 · 이자 지급일 · 조기상환일 · 매도청구일을 한 줄씩 적고 «계약일 이후 첫 노드»
    # (허용 일수 안에서 앞선 노드는 같은 날 — EXDATE_RULE) 를 COUNTIF 로 센다. 00 격자 공통의
    # 6행(조정일) · 9행(지급 회수) · 20·27행(행사월)이 이 목록을 본다. 종전에는 앱이 배정한 결과를
    # 숫자(빨간색)로 넣어 배정이 맞는지 엑셀에서 따라갈 수 없었다. 계약일과 노드 수는 앱이 정한다.
    DATES = "00 계약일 목록"
    DQ = f"'{DATES}'"
    DL = {}                       # 권리 → (첫 행, 끝 행)
    _dl = lambda key, col: f"{DQ}!${col}${DL[key][0]}:${col}${DL[key][1]}"

    def make_dates():
        D = wb.create_sheet(DATES); D.sheet_view.showGridLines = False
        for cc, w_ in (("B", 16), ("C", 9), ("D", 14), ("E", 13), ("F", 16), ("G", 12), ("H", 58)):
            D.column_dimensions[cc].width = w_
        title(D, 1, "00 계약일 목록 — 계약서의 날짜를 노드에 배정하는 과정", span=7)
        put(D, 2, 2, "계약일마다 «계약일 이후 첫 노드» 에 배정한다. 노드 날짜가 계약일보다 허용 일수 안에서 "
                     "앞서면 같은 날로 본다. 노드 번호 = 노드 날짜가 «계약일 − 허용 일수» 보다 이른 노드의 개수 "
                     "(COUNTIF). 00 격자 공통 6행(조정일)·9행(지급 회수)·20·27행(행사월)이 이 표를 본다. "
                     "계약일과 노드 날짜는 앱이 적은 값이다 — 바꾸려면 앱에서 조서를 다시 만든다.",
            color=GREY, size=9)
        put(D, 4, 2, "노드 번호", bold=True, size=8, fill=LIGHT, border=True)
        put(D, 5, 2, "노드 날짜", bold=True, size=8, fill=LIGHT, border=True)
        # 노드 날짜는 엔진의 node_dates 를 그대로 적는다(앱이 정한 값). 엑셀 ROUND 는 0.5 를 올리고 파이썬 round 는
        # 짝수로 맞춰, 수식으로 다시 만들면 구간 일수가 정확히 반일인 노드에서 날짜가 하루 갈려 배정이 달라진다.
        _ndv = node_dates(tm, n, dt_)
        for i in range(n+1):
            L_ = gl(3+i); D.column_dimensions[L_].width = max(D.column_dimensions[L_].width or 0, 11)
            put(D, 4, 3+i, i, fmt=N0, align="center", size=8)
            put(D, 5, 3+i, _ndv[i], fmt=DATE, align="center", size=8)
        NROW = f"$C$5:${gl(3+n)}$5"
        put(D, 6, 2, "허용 일수", bold=True, size=8, fill=LIGHT, border=True)
        put(D, 6, 3, f"=MIN(5,MAX(1,INT({K['dt']}*365/4)))", fmt=N0, align="center", size=8)
        TOL = "$C$6"
        r0 = 8
        for j, h in enumerate(["권리", "회차", "계약 개월 (발행일부터)", "계약일", "계약일 이후 첫 노드",
                               "적용 노드", "적용 규칙 (−1 = 이 격자에서 쓰지 않음)"]):
            put(D, r0, 2+j, h, bold=True, size=8, fill=LIGHT, border=True)
        r = r0 + 1
        rem_m = float(getattr(tm, "rem_m", 0.0) or tm.T*12)
        end = el + rem_m
        lists = []
        if not _kconst and int(tm.rfx_mode) and tm.rfx_cyc > 0 and not rfx_any(tm):
            first = rfx_first_m(tm)
            k = max(0, math.floor((el - first)/tm.rfx_cyc) + 1) if el >= first - 1e-9 else 0
            ms = []
            while first + tm.rfx_cyc*k <= end + 1e-6:
                ms.append(first + tm.rfx_cyc*k); k += 1
            lists.append(("rfx", "전환가격 조정일", ms,
                          lambda rr: f"=IF(AND(D{rr}>{K['elm']}+1E-9,F{rr}>=1,F{rr}<={K['n']}),F{rr},-1)",
                          "평가기준일 뒤 · 노드 1~n 만. 두 조정일이 한 노드에 오면 한 번 조정한다"))
        if _FP:
            k = math.floor(el/tm.ipay + 1e-9) + 1
            ms = []
            while k*tm.ipay <= end + 1e-6:
                ms.append(k*tm.ipay); k += 1
            lists.append(("pay", inst_text(tm, "이자 지급일"), ms,
                          lambda rr: f"=IF(F{rr}>=1,MIN(F{rr},{K['n']}),-1)",
                          "노드 1 부터 · 만기 뒤 지급일은 만기 노드. 두 지급일이 한 노드에 오면 그 노드에서 회수만큼 지급"))
        for key, nm, cont, rows_, s_, e_, f_, on in (
                ("put", "조기상환일", _EA["p_cont"], _EA["p_rows"], tm.p_s, tm.p_e, tm.p_f, bool(_EA["p_dates"])),
                ("call", "매도청구일", _EA["k_cont"], _EA["k_rows"], tm.k_s, tm.k_e, tm.k_f, bool(_EA["k_dates"]))):
            if cont or not on: continue
            if rows_:
                ms = [m for m, _ in rows_]
            else:
                ms, k = [], 0
                while s_ + k*f_ <= e_ + 1e-6:
                    ms.append(s_ + k*f_); k += 1
            lists.append((key, inst_text(tm, nm), ms,
                          lambda rr: f"=IF(OR(D{rr}<{K['elm']}-1E-6,F{rr}>{K['n']}),-1,F{rr})",
                          "평가기준일 전에 지난 회차 · 만기 뒤는 쓰지 않음. 두 회차가 한 노드에 오면 앞 회차"))
        for key, nm, ms, gf, rule in lists:
            a = r
            for j, m in enumerate(ms):
                put(D, r, 2, nm if j == 0 else "", size=8, border=True)
                put(D, r, 3, j+1, fmt=N0, align="center", size=8, border=True)
                put(D, r, 4, round(m, 6), fmt=N2, align="right", size=8, border=True)
                put(D, r, 5, months_to_date(tm.d_issue, m), fmt=DATE, align="center", size=8, border=True)
                put(D, r, 6, f'=COUNTIF({NROW},"<"&(E{r}-{TOL}))', fmt=N0, align="center", size=8, border=True)
                put(D, r, 7, gf(r), fmt=N0, align="center", size=8, border=True, bold=True)
                if j == 0: put(D, r, 8, rule, color=GREY, size=8)
                r += 1
            DL[key] = (a, r-1)
            r += 1
        D.freeze_panes = "C9"
        return D

    def head_formulas(L, Lp, i, cvs, pst, call_on):
        """트리 머리 17행의 식(이 시트 기준). 00 격자 공통과 같은 식이면 참조로 바꾼다."""
        st = f"{L}$2"
        f = {1: f"={K['d_base']}+{st}*{K['dt']}*365",
             2: (0 if i == 0 else f"={Lp}$2+1"),
             3: (f"=IF(OR(AND({st}>={cvs},{st}<={K['cve']}),"
                 f"AND({K['auto']}=1,{st}={K['n']})),1,0)"),
             # 표를 넣으면 «표가 정한 회차만» 열린다 (00 행사금액표).
             4: "=" + x_pflag(st, pst),
             5: (0 if not call_on else "=" + x_kflag(st)),
             # 조정일 표시 — 00 계약일 목록에서 이 노드에 배정된 조정일이 있으면 1 (refix_steps 와 같은 규칙).
             # 조항이 없으면 늘 0, «언제든지» 조정이면 계약일이 없어 앱이 정한 노드다.
             6: (0 if _kconst else
                 f"=IF(COUNTIF({_dl('rfx', 'G')},{st})>0,1,0)" if "rfx" in DL else
                 (1 if i in REFIXSET else 0)),
             # 금액은 00 격자 공통의 보조 행에서 계산한 값을, 이 시트의 행사 가능 표시로 켠다.
             7: (f"=IF({L}$4=1,{DPQ}!{L}${_DPR['pv']}+{_cadd(i, 'pcadd')},0)" if DPON else
                 f"=IF({L}$4=1,{COMQ}!{L}${CROW['pamt']},0)"),
             8: f"=IF({L}$5=1,{COMQ}!{L}${CROW['kamt']},999999)",
             9: (f"=COUNTIF({_dl('pay', 'G')},{st})*100*{K['cpn']}*{K['ipaym']}/12" if "pay" in DL else "=0"),
             10: f"=IF({st}={K['n']},{(_DPRED if DPON else K['red'])},0)",
             13: f"={K['sig']}", 14: f"={K['u']}", 15: f"={K['dd']}"}
        if i < n:
            # 이자율 산출내역을 함께 실었으면 그 표를 가리킨다.
            if _irref:
                f[11] = f"='{_irref[0]}'!$G${_irref[1]+i}"
                f[12] = f"='{_irref[0]}'!$J${_irref[1]+i}"
            else:
                f[11] = forward_rate(RF, i*dt_, (i+1)*dt_)
                f[12] = forward_rate(CR, i*dt_, (i+1)*dt_)
            f[16] = f"=(EXP(({L}$11-{K['divy']})*{K['dt']})-{L}$15)/({L}$14-{L}$15)"
            f[17] = f"=1-{L}$16"
        else:
            f[16] = f"={K['q']}"; f[17] = f"={K['q1']}"
        return f

    HFMT = {1: (DATE, GREY), 2: (N0, None), 3: (N0, None), 4: (N0, None), 5: (N0, None),
            6: (N0, RED), 7: (N2, None), 8: (N2, None), 9: (N2, None), 10: (N2, None),
            11: (P2, None), 12: (P2, None), 13: (P2, None), 14: (N4, None), 15: (N4, None),
            16: (N4, None), 17: (N4, None)}

    def make_common():
        W = wb.create_sheet(COM); W.sheet_view.showGridLines = False
        make_dates()
        W.column_dimensions["B"].width = 30
        for i in range(n+1): W.column_dimensions[gl(3+i)].width = 9
        title(W, 18, "00 격자 공통 — 모든 트리가 함께 쓰는 날짜·행사일·금액·금리·확률", span=min(n+1, 14))
        put(W, 19, 2, "행사금액 계산 과정", bold=True, size=8, fill=LIGHT, border=True)
        rows = dict(enumerate(HEAD, start=1))
        rows.update({CROW['pmo']: "조기상환 계약 행사월 (발행일부터)", CROW['pyr']: "조기상환 경과연수",
                     CROW['p1']: "조기상환 할증률 ① 이자 붙여 공제", CROW['p0']: "조기상환 할증률 (공제 전)",
                     CROW['ppaid']: "조기상환일까지 기지급 이자 (명목)", CROW['pap']: "조기상환 적용 할증률",
                     CROW['pamt']: "조기상환금액",
                     CROW['kmo']: "매도청구 계약 행사월 (발행일부터)", CROW['kyr']: "매도청구 경과연수",
                     CROW['k1']: "매도청구 할증률 ① 이자 붙여 공제", CROW['k0']: "매도청구 할증률 (공제 전)",
                     CROW['kpaid']: "매도청구일까지 기지급 이자 (명목)", CROW['kap']: "매도청구 적용 할증률",
                     CROW['kamt']: "매도청구금액"})
        for r, nm in rows.items():
            put(W, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
        _yr = _xyr
        _paid = lambda mo: f"{K['cpn']}*{K['ipaym']}/12*INT({mo}/MAX(1E-9,{K['ipaym']})+1E-9)"
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            st = f"{L}$2"
            c = lambda r: f"{L}${r}"
            for r, v in head_formulas(L, Lp, i, K["cvs"], K["pst"], True).items():
                fm, col = HFMT[r]
                _common[(r, i)] = v
                if r in (7, 8):                      # 이 시트 안에서는 시트 이름 없이
                    v = v.replace(f"{COMQ}!", "")
                put(W, r, 3+i, v, fmt=fm, align="center", size=8,
                    color=(col or (AMB if r in (11, 12) and not _irref else "000000")))
            # ── 행사금액 계산 과정 — 행사일인 노드에만 편다 ──
            # 계약 행사월은 계약서의 날짜(발행일부터 개월)다. 노드는 그 날 이후 첫
            # 노드이므로 노드 날짜와 며칠 다를 수 있다 — 할인은 노드 날짜로, 금액은
            # 계약일로 한다. 기간 중 언제든지 행사하는 권리는 노드 개월이 곧 행사월이다.
            # 계약일이 따로 있는 권리(정기 행사)는 모든 열에 행사월 식을 둔다 — 00 계약일 목록에서 이 노드에
            # 배정된 회차의 계약 개월이고, 없으면 빈칸이라 그 열의 금액 행도 빈칸이다 (행사 가능 표시가 0).
            for key, mrow, dates, cont, steps, kind in (
                    ("put", CROW['pmo'], _EA["p_dates"], _EA["p_cont"], _EA["p_steps"], "p"),
                    ("call", CROW['kmo'], _EA["k_dates"], _EA["k_cont"], _EA["k_steps"], "k")):
                live = (not cont) and key in DL
                if not (i in dates or live): continue
                if cont or key not in DL:
                    mo_v = f"={_MO(st)}"
                else:
                    mo_v = (f'=IFERROR(INDEX({_dl(key, "D")},MATCH({st},{_dl(key, "G")},0)),"")')
                put(W, mrow, 3+i, mo_v, fmt=N2, align="center", size=8)
                mo = c(mrow)
                g = (lambda x: f'=IF(ISNUMBER({mo}),{x},"")') if live else (lambda x: "=" + x)
                if kind == "p":
                    put(W, CROW['pyr'], 3+i, g(_yr(mo)), fmt=N4, align="center", size=8)
                    yr = c(CROW['pyr'])
                    put(W, CROW['p1'], 3+i, g(xl_prem(K['pyld'], K['cpn'], K['pcmp'], yr)), fmt=N6, align="center", size=8)
                    put(W, CROW['p0'], 3+i, g(xl_prem(K['pyld'], '0', K['pcmp'], yr)), fmt=N6, align="center", size=8)
                    put(W, CROW['ppaid'], 3+i, g(_paid(mo)), fmt=N6, align="center", size=8)
                    put(W, CROW['pap'], 3+i, g(f"IF({K['pless']}=1,{c(CROW['p1'])},"
                                               f"MAX(0,{c(CROW['p0'])}-IF({K['pless']}=2,{c(CROW['ppaid'])},0)))"),
                        fmt=N6, align="center", size=8)
                    pf = f"IF({K['pmode']}=1,100*(1+{c(CROW['pap'])}),{K['prate']})"
                    if i in steps:
                        pf = _pv0(st)
                    put(W, CROW['pamt'], 3+i, g(f"({pf}+{_cadd(i, 'pcadd')})"), fmt=N4, align="center", size=8)
                else:
                    put(W, CROW['kyr'], 3+i, g(_yr(mo)), fmt=N4, align="center", size=8)
                    yr = c(CROW['kyr'])
                    put(W, CROW['k1'], 3+i, g(xl_prem(K['prem'], K['cpn'], K['kcmp'], yr)), fmt=N6, align="center", size=8)
                    put(W, CROW['k0'], 3+i, g(xl_prem(K['prem'], '0', K['kcmp'], yr)), fmt=N6, align="center", size=8)
                    put(W, CROW['kpaid'], 3+i, g(_paid(mo)), fmt=N6, align="center", size=8)
                    put(W, CROW['kap'], 3+i, g(f"IF({K['kless']}=1,{c(CROW['k1'])},"
                                               f"MAX(0,{c(CROW['k0'])}-IF({K['kless']}=2,{c(CROW['kpaid'])},0)))"),
                        fmt=N6, align="center", size=8)
                    kf = (f"IF({K['prem']}>0,100*(1+{c(CROW['kap'])}),"
                          f"100*(1+MAX(0,-{_KC}*{yr})))")
                    if i in steps:
                        kf = _kv0(st)
                    put(W, CROW['kamt'], 3+i, g(f"({kf}+{_cadd(i, 'kcadd')})"), fmt=N4, align="center", size=8)
        put(W, 35, 2, "트리 시트의 1~17행은 이 시트를 가리킨다. 트리마다 행사 시작일이 다를 때"
            "(매도청구 대상 물량 등)만 그 시트가 행사 가능 표시(3~5행)를 따로 계산한다. "
            "금액(7·8행)은 늘 이 시트 26·33행에서 가져와 그 시트의 행사 가능 표시로 켠다. "
            "조정일(6행)·지급 회수(9행)·행사월(20·27행)은 «00 계약일 목록» 에서 계약일을 노드에 배정한 "
            "식으로 온다 — 계약일을 바꾸려면 앱에서 조서를 다시 만든다.", color=GREY, size=9)
        put(W, 36, 2, "상환할증률 = (보장수익률 − 차감률) ÷ 보장수익률 × ((1 + 보장수익률/m)^(m·t) − 1). "
            "복리 횟수 m 이 0 이면 (보장수익률 − 차감률) × t.", color=GREY, size=9)
        W.freeze_panes = "C3"
        W.sheet_properties.tabColor = RFXC
        if DPON:
            make_dp()
        return W

    def make_dp():
        """배당가능이익 상환 일정 — 청구 시점(열)마다 받는 현금과 그 현재가치. 엔진 DPPlan.schedule 과 같은 식."""
        W = wb.create_sheet(DPS); W.sheet_view.showGridLines = False
        W.column_dimensions["B"].width = 34
        for i in range(n+1): W.column_dimensions[gl(3+i)].width = 11
        title(W, 2, "00 배당가능이익 상환 — 청구 시점마다 실제로 받는 현금과 그 현재가치", span=min(n+1, 12))
        put(W, 3, 2, "발생연도 Y 의 배당가능이익은 Y+1 년(재원 사용 시작일부터)의 재원이다. 우선배당을 먼저 빼고, 동순위 상품과 남은 상환금 비율로 "
            "나누며, 갚지 못한 금액은 다음 해(청구일 + 1년에 가장 가까운 계산 시점)로 넘긴다. 넣지 않은 해는 제한이 없다. "
            "할인은 00 격자 공통 12행(위험 선도이자율)이고 만기 뒤는 마지막 구간 값으로 잇는다.", color=GREY, size=9)
        _in = lambda r, c, v, fm=None: put(W, r, c, v, fmt=fm, fill=INPUT_FILL, border=True, align="right")
        put(W, 5, 2, "평가대상 발행총액 (원)", bold=True, border=True)
        put(W, 5, 3, f"={K['face']}", fmt=N0, border=True, align="right")
        put(W, 6, 2, "평가대상 우선배당률 (발행가 기준, 연)", bold=True, border=True); _in(6, 3, dp_div_rate(tm), P2)
        put(W, 7, 2, "넘긴 상환금 연 가산율", bold=True, border=True); _in(7, 3, float(getattr(tm, "dp_delay", 0.0) or 0.0), P2)
        _fm, _fd = dp_from_md(tm) or (1, 1)
        put(W, 8, 2, "재원 사용 시작일 (월 · 일) — 이 날 전은 그 전해 재원", bold=True, border=True)
        _in(8, 3, _fm, "0"); _in(8, 4, _fd, "0")
        FY = lambda x: f"(YEAR({x})-IF({x}<DATE(YEAR({x}),$C$8,$D$8),1,0))"
        put(W, 9, 2, "발생연도", bold=True, fill=LIGHT, border=True); put(W, 9, 3, "배당가능이익 (원)", bold=True, fill=LIGHT, border=True)
        for k, (fy, amt) in enumerate(_DPY):
            _in(_dp_r_prof + k, 2, fy, "0"); _in(_dp_r_prof + k, 3, amt, N0)
        rng = f"$B${_dp_r_prof}:$C${_dp_r_prof + len(_DPY) - 1}"
        hd = ["동순위 상품", "발행일", "발행총액 (원)", "상환 보장수익률", "복리 (1 연복리 / 0 단리)",
              "상환청구 시작일", "상환청구 종료일", "우선배당률 (연)"]
        for c_, h in enumerate(hd):
            put(W, _dp_r_oth, 2+c_, h, bold=True, fill=LIGHT, border=True)
        if not _DPO:
            put(W, _dp_r_oth + 1, 2, "없음 — 다른 상품은 평가대상이 선순위라 평가대상 재원에서 빼지 않는다", color=GREY, size=9)
        orow = {}
        for x, o in enumerate(_DPO):
            r = _dp_r_oth + 1 + x; orow[x] = r
            _nm = put(W, r, 2, "", border=True); _nm.value = o["name"]; _nm.data_type = 's'   # 수식으로 읽히지 않게
            _in(r, 3, o["issue"].date(), DATE); _in(r, 4, o["face"], N0); _in(r, 5, o["yld"], P2)
            _in(r, 6, int(o["cmp"]), "0"); _in(r, 7, o["start"].date(), DATE); _in(r, 8, o["end"].date(), DATE)
            _in(r, 9, o["div"], P2)
        FACE, RATE, G = "$C$5", "$C$6", "$C$7"
        R = _DPR
        lab = {R["st"]: "청구 스텝", R["date"]: "청구일", R["cum"]: "누적 위험 할인 Σ f·Δt",
               R["amt"]: "상환청구 계약금액 (행사일 이자 제외)", R["pv"]: "상환청구 가치 — 실제 지급 일정의 현재가치"}
        if issuer_redeem(tm): lab[R["kok"]] = "발행자 상환 가능 (그 해 재원 ≥ 상환금 × 한도 + 같은 해 동순위 상환금, 1=예)"
        for r, t_ in lab.items():
            put(W, r, 2, t_, bold=True, fill=LIGHT, border=True)
        cumrng = f"$C${R['cum']}:${gl(3+n)}${R['cum']}"
        flast = f"{COMQ}!${gl(3+n-1)}$12"
        _dv = lambda x, yr, y0: (f"IF(AND($C${orow[x]}<DATE({yr}+1,$C$8,$D$8),$H${orow[x]}>=DATE({y0},$C$8,$D$8)),"
                                 f"$I${orow[x]}*$D${orow[x]}*100/{FACE},0)")
        _live = lambda x, y0: f"$H${orow[x]}>=DATE({y0},$C$8,$D$8)"
        nx = len(_DPO)

        def ndate(m):
            """스텝 m 의 날짜 — 평가기준일 + 반올림(m × Δt × 365일), 딱 반이면 짝수 쪽 (엔진 dp_step_dt 와 같다)."""
            y = f"(({m})*({K['dt']}*365))"
            return (f"({K['d_base']}+IF(ABS({y}-INT({y})-0.5)<1E-9,INT({y})+MOD(INT({y}),2),ROUND({y},0)))")
        # 동순위 상품의 그 재원 연도 안 청구 기간 [lo, hi] — 비어 있으면(lo > hi) 그 해 청구하지 않는다
        _lo = lambda x, yr: f"MAX($G${orow[x]},$C${orow[x]},DATE({yr},$C$8,$D$8))"
        _hi = lambda x, yr: f"MIN($H${orow[x]},DATE({yr}+1,$C$8,$D$8)-1)"

        def rows_of(b):
            rr = dict(m=b, date=b+1, yr=b+2, P=b+3)
            for x in range(nx): rr[("J", x)] = b+4+x
            rr["ded"] = b+4+nx; rr["cap"] = rr["ded"]+1
            for x in range(nx): rr[("B", x)] = rr["cap"]+1+x
            rr["be"] = rr["cap"]+1+nx; rr["tot"] = rr["be"]+1; rr["pe"] = rr["tot"]+1
            for x in range(nx): rr[("Q", x)] = rr["pe"]+1+x
            rr["df"] = rr["pe"]+1+nx
            return rr

        def chain(L, cst, cdate, ccum, amt, base, labels):
            """한 청구 시점(열 L)의 지급 일정. 돌려주는 것은 «지급 × 할인계수» 식 목록."""
            terms, pr = [], None
            y0c = f"{L}${rows_of(base)['yr']}"          # 청구 시점의 재원 연도 (0년 블록)
            for k in range(_DPP.K + 1):
                b = base + k*_dp_blk
                rr = rows_of(b)
                if labels:
                    put(W, b-1, 2, f"[{k}년 뒤 지급]", bold=True, color="FFFFFF", fill=SUB, size=9)
                    names = {rr["m"]: "지급 스텝", rr["date"]: "지급일", rr["yr"]: "재원 연도",
                             rr["P"]: "배당가능이익 (100 기준, 넣지 않은 해 = 제한 없음)",
                             rr["ded"]: "우선배당 차감 (100 기준)", rr["cap"]: "쓸 수 있는 재원 (100 기준)",
                             rr["be"]: "평가대상 남은 상환금", rr["tot"]: "남은 상환금 합계 (동순위 포함)",
                             rr["pe"]: "평가대상 지급", rr["df"]: "할인계수 (청구일 → 지급일)"}
                    for x, o in enumerate(_DPO):
                        names[rr[("J", x)]] = f"{o['name']} 상환청구 (1=예)"
                        names[rr[("B", x)]] = f"{o['name']} 남은 상환금"
                        names[rr[("Q", x)]] = f"{o['name']} 지급"
                    for r_, t_ in names.items():
                        put(W, r_, 2, t_, bold=True, fill=LIGHT, border=True, size=8)
                q = lambda key: f"{L}${rr[key]}"
                qp = lambda key: f"{L}${pr[key]}"
                cidx = W[f"{L}1"].column
                g_ = lambda r_, v, fm=N4: put(W, r_, cidx, v, fmt=fm, align="center", size=8)
                g_(rr["m"], f"={cst}+ROUND({k}/{K['dt']},0)", N0)
                g_(rr["date"], "=" + ndate(q('m')), DATE)
                g_(rr["yr"], f"={FY(cdate)}+{k}", "0")
                g_(rr["P"], f"=IFERROR(VLOOKUP({q('yr')}-1,{rng},2,FALSE)*100/{FACE},1E+300)")
                for x in range(nx):
                    o_ = orow[x]
                    cond = f"AND({_live(x, y0c)},{_lo(x, q('yr'))}<={_hi(x, q('yr'))})"
                    g_(rr[("J", x)], (f"=IF({cond},1,0)" if k == 0 else f"=IF(OR({qp(('J', x))}=1,{cond}),1,0)"), "0")
                ded = (f"100*{RATE}" if k == 0 else "0") + "".join(
                    f"+IF({('0' if k == 0 else qp(('J', x)))}=0,{_dv(x, q('yr'), y0c)},0)" for x in range(nx))
                g_(rr["ded"], "=" + ded)
                g_(rr["cap"], f"=IF({q('P')}>=1E+299,1E+300,MAX(0,{q('P')}-{q('ded')}))")
                for x in range(nx):
                    o_ = orow[x]
                    cl = f"MIN(MAX({q('date')},{_lo(x, q('yr'))}),{_hi(x, q('yr'))})"
                    tau = f"MAX(0,({cl}-$C${o_})/365)"
                    grow = f"$D${o_}*IF($F${o_}=1,(1+$E${o_})^{tau},1+$E${o_}*{tau})*100/{FACE}"
                    g_(rr[("B", x)], (f"=IF({q(('J', x))}=1,{grow},0)" if k == 0 else
                                      f"=IF({qp(('J', x))}=1,({qp(('B', x))}-{qp(('Q', x))})*(1+{G}),"
                                      f"IF({q(('J', x))}=1,{grow},0))"))
                g_(rr["be"], (f"={amt}" if k == 0 else f"=({qp('be')}-{qp('pe')})*(1+{G})"))
                g_(rr["tot"], "=" + "+".join([q("be")] + [q(("B", x)) for x in range(nx)]))
                share = lambda bal: (f"=IF({q('cap')}>=1E+299,{bal},IF({q('tot')}>0,MIN({bal},{q('cap')}*{bal}/{q('tot')}),0))")
                g_(rr["pe"], share(q("be")))
                for x in range(nx):
                    g_(rr[("Q", x)], share(q(("B", x))))
                cm = (f"IF({q('m')}<={n},INDEX({cumrng},1,{q('m')}+1),"
                      f"INDEX({cumrng},1,{n+1})+({q('m')}-{n})*{flast}*{K['dt']})")
                g_(rr["df"], f"=EXP(-({cm}-{ccum}))", N6)
                terms.append(f"{q('pe')}*{q('df')}")
                pr = rr
            return terms

        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            c = lambda r: f"{L}${r}"
            put(W, R["st"], 3+i, f"={COMQ}!{L}$2", fmt=N0, align="center", size=8)
            put(W, R["date"], 3+i, "=" + ndate(c(R["st"])), fmt=DATE, align="center", size=8)
            put(W, R["cum"], 3+i, (0 if i == 0 else f"={Lp}${R['cum']}+{COMQ}!{Lp}$12*{K['dt']}"), fmt=N6, align="center", size=8)
            pa = f"{COMQ}!{L}${CROW['pamt']}"
            put(W, R["amt"], 3+i, f"=IF(ISNUMBER({pa}),{pa}-({_cadd(i, 'pcadd')}),0)", fmt=N4, align="center", size=8)
            terms = chain(L, c(R["st"]), c(R["date"]), c(R["cum"]), c(R["amt"]), _dp_k0, i == 0)
            put(W, R["pv"], 3+i, "=" + "+".join(terms), fmt=N4, align="center", size=8)
            if issuer_redeem(tm):
                ka = f"{COMQ}!{L}${CROW['kamt']}"
                kp = f"({ka}-({_cadd(i, 'kcadd')}))"     # 행사일 배당 가산분은 우선배당으로 이미 뺐다
                p0 = f"IFERROR(VLOOKUP({FY(c(R['date']))}-1,{rng},2,FALSE)*100/{FACE},1E+300)"
                d0 = f"100*{RATE}" + "".join(f"+{_dv(x, FY(c(R['date'])), FY(c(R['date'])))}" for x in range(nx))
                b0 = "".join(f"+{L}${rows_of(_dp_k0)[('B', x)]}" for x in range(nx))   # 0년 블록의 동순위 남은 상환금
                put(W, R["kok"], 3+i, f"=IF(ISNUMBER({ka}),IF(OR({p0}>=1E+299,MAX(0,{p0}-({d0}))>={kp}*{K['cw']}{b0}-1E-9),1,0),0)",
                    fmt=N0, align="center", size=8)
        # ── 만기상환 — 존속기간 만료 시 상환도 이익으로 한다. 만기 노드에서 청구한 것과 같은 일정 (C열) ──
        LN = gl(3+n)
        sec(W, _dp_red0 - 3, "만기상환 일정 — 만기 노드에서 상환하는 금액(만기상환금액)의 실제 지급 일정", span=6)
        put(W, _dp_red0 - 2, 2, "만기상환금액 (계약)", bold=True, fill=LIGHT, border=True)
        put(W, _dp_red0 - 2, 3, f"={K['red']}", fmt=N4, align="center", size=8)
        put(W, _dp_red0 - 1 - 0, 2, "만기상환 가치 — 실제 지급 일정의 현재가치", bold=True, fill=LIGHT, border=True)
        rterms = chain("C", str(n), ndate(str(n)), f"{LN}${R['cum']}", f"$C${_dp_red0 - 2}", _dp_red0 + 1, True)
        put(W, _dp_red0 - 1, 3, "=" + "+".join(rterms), fmt=N4, align="center", size=8)
        W.freeze_panes = f"C{R['st']+1}"
        W.sheet_properties.tabColor = RFXC
        return W

    def newsheet(name, ttl, note, refs, call_on=True, conv_cell=None,
                 put_cell=None):
        if COM not in wb.sheetnames:
            make_common()
        W = wb.create_sheet(name); W.sheet_view.showGridLines = False
        W.column_dimensions["B"].width = 17
        for i in range(n+1): W.column_dimensions[gl(3+i)].width = 9
        cvs = conv_cell or K["cvs"]
        pst = put_cell or K["pst"]
        for r, nm in enumerate(HEAD, start=1):
            put(W, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            own = head_formulas(L, Lp, i, cvs, pst, call_on)
            same = {r: own[r] == _common[(r, i)] for r in own}
            for r, v in own.items():
                fm, col = HFMT[r]
                # 00 격자 공통과 같은 식이면 짧은 참조로 — 7·8행은 이 시트의 행사 가능
                # 표시(4·5행)가 공통과 같을 때만 참조할 수 있다.
                if same[r] and (r != 7 or same[4]) and (r != 8 or same[5]):
                    v = f"={COMQ}!{L}{r}"
                put(W, r, 3+i, v, fmt=fm, align="center", size=8, color=(col or "000000"))
        title(W, 18, ttl, span=min(n+1, 14))
        # 이 시트가 무엇을 계산하는지 한 줄로 — 제목 바로 오른쪽에 (틀 고정 영역 안)
        put(W, 18, 3+min(n+1, 14), note + used_by(name), color=GREY, size=9)
        put(W, 19, 2, "하락 횟수 r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
        for i in range(n+1):
            put(W, 19, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                fill=(RFXC if i in REFIXSET else LIGHT), border=True)
        for r in range(n+1):
            put(W, R0+r, 2, r, bold=True, size=8, fmt=N0, align="center",
                fill=LIGHT, border=True)
        put(W, R0+n+2, 2, note, color=GREY, size=9)
        put(W, R0+n+3, 2, "이 시트가 참조하는 시트: " + refs, color=GREEN, size=9)
        put(W, R0+n+4, 2, ("r 은 하락 횟수. 11·12행 선도이자율은 IR 선도이자율 시트에서 온다." if _irref
                           else "r 은 하락 횟수. 11·12행 선도이자율만 값이다."), color=AMB, size=9)
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
    # 동점 허용오차는 엔진과 같은 식(xl_tol — 견주는 두 금액 크기에 비례)을 수식에도 쓴다.
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
    S11B = "11b 채권확률 (1 − 전환확률)"
    S17, S18 = "17 구성비율", "18 혼합할인율"
    S17A, S17B = "17a 행사 지분몫", "17b 행사 채권몫"
    S19, S20 = "19 콜 페이오프", "20 매도청구권가치"
    S19A, S19B = "19a 콜 존속", "19b 행사 판단"
    S21, S22 = "21 방법2 지분보유", "22 방법2 부채보유"
    S23, S24 = "23 방법2 지분몫", "24 방법2 부채몫"
    # 콜 한도와 의무보유 비율이 다르면(콜 70% · 의무보유 30%) 의무보유가 없는 몫을 따로 잰다 —
    # 엔진 call_mix 와 같은 나눔. 같으면 종전 시트 그대로다.
    S15B, S19AN, S20N = "15n 무보유 트랜치", "19an 콜 존속 무보유", "20n 매도청구권 무보유"
    S19BN, S21N, S22N = "19bn 행사 판단 무보유", "21n 지분보유 무보유", "22n 부채보유 무보유"
    S23N, S24N = "23n 지분몫 무보유", "24n 부채몫 무보유"
    S19N, S17AN, S17BN = "19n 콜 페이오프 무보유", "17an 행사 지분몫 무보유", "17bn 행사 채권몫 무보유"
    _CALLED_FREE = f"매도청구 대상 중 의무보유 없는 {(tm.k_w - _LKW)*100:,.4g}% 물량"

    # ── 01 주가 ──
    W = newsheet(S1, "① 주가트리",
                 "맨 위 노드는 직전 시점 맨 위 × u, 나머지는 직전 시점 한 칸 위 노드 × d.", "가정")
    fill(W, lambda i, r, L, Lp, Ln: (f"={K['S0']}" if i == 0 else
         (f"={Lp}{R0}*{L}$14" if r == 0 else f"={Lp}{R0+r-1}*{L}$15")), N2)

    # ── 도달확률 ── 02a 이월 전환가격의 경로가중치가 쓴다. 전환가격이 바뀌지 않으면 필요 없다.
    if not _kconst:
        W2 = wb.create_sheet("도달확률"); W2.sheet_view.showGridLines = False
        W2.column_dimensions["B"].width = 12
        title(W2, 2, "도달확률  P(i,r) = P(i−1,r)×q + P(i−1,r−1)×(1−q)",
              span=min(n+1, 12))
        put(W2, 3, 2, "구간별 상승확률이 달라 시점별 도달확률을 앞 시점부터 순차적으로 계산한다. q 는 **직전 시점**의 "
            "위험중립 상승확률(① 16행)이다. 이항계수 식 COMBIN(i,r)×q^(i−r)×(1−q)^r 은 q 가 모든 구간에서 같을 "
            "때만 쓸 수 있다. r 은 하락 횟수이고, 상승하면 r 이 그대로(q), 하락하면 r 이 하나 늘어난다(1−q).",
            color=GREY, size=9)
        put(W2, 4, 2, "하락 횟수 r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
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
    # 전환가격을 바꾸는 조항(정기 조정 · 상장 조정)이 없으면 전환가격은 모든 노드에서
    # 현재 전환가액 그대로다. 그때는 02·03 트리를 싣지 않고 04 가 가정의 전환가액을 쓴다.
    # 조항이 있으면 단계별로 나눠 계산한다(값은 한 장으로 쓸 때와 같다).
    #   02a 이월 전환가격 — 직전 열 두 노드에서 이어받은 값 (조정일이 아닐 때의 값)
    #   02b 정기 조정 반영 — 조정일이면 주가로 자르고 하한·상한을 건다 (상장 조정이 있을 때만 따로)
    #   02 전환가격        — 상장 스텝이면 공모가 × 배수로 한 번 더 자른다
    S2A, S2B = "02a 이월 전환가격", "02b 정기 조정 반영"
    # 상장 조정이 없으면 02 가 곧 02b(정기 조정 반영)라 한 장으로 둔다.

    def bf(i, r, L, Lp, Ln):
        if i == 0: return f"={K['K0']}"
        carry = f"{Q(S2A)}!{L}{R0+r}"
        # 조정일에도 **같은 이월값**을 쓴다 (엔진과 동일).
        _px = xl_k_round(f"{Q(S1)}!{L}{R0+r}", K['rround'])
        base = (f"IF({K['up']}=1,{_px},"
                f"MIN({carry},{_px}))")
        clip = f"MIN(MAX({base},{K['flr']},{K['par']}),{K['cap']})"
        # 주기 조정이 없으면 이월만 한다 (IPO 조정은 02 에서 그 위에 걸린다).
        return f"=IF({K['rfx']}=0,{carry},IF({L}$6=1,{clip},{carry}))"

    if not _kconst:
        if _ipo_on:
            W = newsheet(S2, "② 전환가격트리  = 상장 조정 ∘ 02b 정기 조정 ∘ 02a 이월",
                         "상장 스텝에서 그 노드 주가가 최소공모가격을 넘으면 기존 전환가격과 공모가격 × 조정비율 중 낮은 금액을 적용한다 "
                         "(최저 조정가액·액면가 이상, 상한 이하). 아니면 02b 와 같다. 조정일 열은 주황색이다.", f"{S2B} · {S1}")
            def kf(i, r, L, Lp, Ln):
                if i == 0: return f"={K['K0']}"
                nrm = f"{Q(S2B)}!{L}{R0+r}"
                # 상장 스텝이고 그 주가가 최소공모가격을 넘으면 공모가 × 배수로 자른다.
                # 낮아질 때만 조정되고, 최저 조정가액·액면가 하한이 그대로 걸린다.
                hit = (f"AND({K['ipoon']}=1,{L}$2={K['ipos']},"
                       f"{Q(S1)}!{L}{R0+r}>{K['ipomin']})")
                return (f"=IF({hit},MIN(MAX(MIN({nrm},{K['ipok']}),{K['flr']},{K['par']}),"
                        f"{K['cap']}),{nrm})")
            fill(W, kf, N2)
        else:
            W = newsheet(S2, "② 전환가격트리  = 정기 조정 ∘ 02a 이월",
                         "조정일에는 그날 주가를 기준으로 새 전환가격을 정하되 최저 조정가액·액면가 이상, 상한 이하로 제한한다. "
                         "상향 조정을 허용하면 주가 그대로, 하향만이면 MIN(직전 전환가격, 주가). 조정일이 아니면 02a 와 같다. "
                         "조정일 열은 주황색이다.", f"{S2A} · {S1}")
            fill(W, bf, N2)

        W = newsheet(S2A, "②a 이월 전환가격  직전 열 두 노드에서 이어받는다",
                     "조정일이 아니면 이 값이 그대로 전환가격이다. 처리 방법(가정의 조정일 처리): "
                     "1 경로가중치(도달확률로 가중) · 2 확률가중평균(q로 가중) · 3 특정노드 선택(아래 노드).",
                     f"직전 열 {S2} · 도달확률")
        def cf(i, r, L, Lp, Ln):
            if i == 0: return f"={K['K0']}"
            up = f"{Q(S2)}!{Lp}{R0+r}" if r <= i-1 else None
            dn = f"{Q(S2)}!{Lp}{R0+r-1}" if r-1 >= 0 else None
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
            return "=" + (carry or K['K0'])
        fill(W, cf, N2)

        if _ipo_on:
            W = newsheet(S2B, "②b 정기 조정 반영  조정일에는 주가 기준 새 전환가격, 하한·상한 적용",
                         "리픽싱이 없거나 조정일이 아니면 02a 와 같다. 상향 조정(가정)이면 주가 그대로, "
                         "하향만이면 MIN(직전 전환가격, 주가). 그다음 최저 조정가액·액면가 이상, 상한 이하로 제한한다.",
                         f"{S2A} · {S1}")
            fill(W, bf, N2)

        W = newsheet(S3, "③ 전환주식수트리  (액면 100당) 100 ÷ 전환가격", "액면 100 당 받을 주식 수(100 ÷ 전환가격)다.", S2)
        fill(W, lambda i, r, L, Lp, Ln: f"=100/{Q(S2)}!{L}{R0+r}", N4)

    # 전환가치 = 주가 × 전환비율. 전환가격이 바뀌지 않으면 100 × 주가 ÷ 현재 전환가액.
    _CVX = ((lambda L, r: f"{Q(S1)}!{L}{R0+r}*100/{K['K0']}") if _kconst else
            (lambda L, r: f"{Q(S1)}!{L}{R0+r}*{Q(S3)}!{L}{R0+r}"))
    W = newsheet(S4, "④ 즉시전환가치트리  주가 × 전환주식수",
                 ("전환청구기간 밖이면 0이다. 전환가격이 바뀌는 조항이 없어 전환비율은 "
                  "100 ÷ 현재 전환가액(가정)으로 모든 노드가 같다." if _kconst else
                  "전환청구기간 밖이면 0이다."),
                 (f"{S1} · 가정" if _kconst else f"{S1} · {S3}"))
    fill(W, lambda i, r, L, Lp, Ln:
         f"=IF({L}$3=1,{_CVX(L, r)},0)")

    # 앱에서 고른 것만 만든다. 쓰이지 않는 트리는 아예 넣지 않는다.
    #   GS 시트     — 신용위험 처리가 GS 일 때
    #   ⑮ 트랜치    — 매도청구권을 유무가치비교법으로 잴 때
    #   ⑰~⑳       — 옵션차익 · 혼합할인율
    #   ⑰⑲㉑~㉔   — 옵션차익 · 지분·부채 분리
    # 풋과 콜이 같은 노드에서 함께 열릴 때의 계약 우선순위. 엔진과 같은 갈래를
    # 타야 한다 — 콜이 사는 곳(⑮ 트랜치 · ⑯c · ⑲~㉔)에서만 값이 갈린다.
    _kfirst = int(tm.pc_order) == 1
    # 매도청구 통지 뒤 전환으로 대응할 수 있는가 (k_conv_resp) — 풋·콜 우선순위와 따로 정한다.
    _cresp = conv_resp(tm)
    # 현금납입 BW 는 지분(신주인수권)과 부채(사채)가 애초에 갈라져 있어 GS 가
    # TF 와 같은 값을 낸다. 조서에 쓸모없는 GS 블록을 넣지 않는다.
    _bwc = bw_cash(tm)
    _gs = tm.model == "GS" and not _bwc
    _hascall = tm.k_w > 0
    _km = tm.k_method
    _need15 = _hascall and _km == 0
    _need1 = _hascall and _km == 1
    _need2 = _hascall and _km == 2
    # 지분·채권 구분 기준이 본문 4.3.3(GS 전환확률)이면 옵션차익법이 ⑪ 을 참조한다.
    # ⑪ 은 ⑫⑬⑭ 와 서로를 참조하므로 TF 를 골랐어도 네 시트를 함께 만든다.
    _ksplit = int(getattr(tm, "k_split", 0)) == 1 and (_need1 or _need2)
    # TF 평가에서 ⑪~⑭ 는 GS 모형이 아니라 콜 행사가액을 지분·채권 몫으로 나눌 **전환확률**
    # 을 구하는 보조 계산이다. 이름에 GS 를 붙이면 TF 조서에 GS 결과가 섞인 것으로 읽힌다.
    if not _gs:
        S11, S12, S13, S14 = ("11 전환확률", "12 전환확률 · 할인율",
                              "13 전환확률 · 보유가치", "14 전환확률 · 금융상품가치")

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
                     "그 가치의 주식을 받는다. 즉시 행사와 계속보유 가운데 큰 값이고, "
                     "계속보유가치는 다음 시점 값을 무위험 이자율로 할인한다."
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
                     "신주인수권 행사와 관계없이 남는 사채의 가치다. 조기상환금액과 계속보유가치를 비교하고, "
                     "매도청구가 행사되면 매도청구금액으로 제한된다.",
                     f"다음 열 {S6}" + (f" · {S9}" if not _det else ""), call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
            f"=MAX({L}$7,{L}$10)+{L}$9" if i == n else
            f"=MAX({L}$7,MIN({CB(L, r, Ln)},{L}$8))" if _det else
            f'=IF({DEC(L, r)}="상환P",{L}$7,IF({DEC(L, r)}="상환C",{L}$8,'
            f"{CB(L, r, Ln)}))"))

        W = newsheet(S7, "⑦ 계속보유가치트리",
                     "신주인수권은 무위험, 사채는 위험 선도이자율로 따로 할인해 더한다.",
                     f"다음 열 {S5} · {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
            f"={L}$10" if i == n else f"={CE(L, r, Ln)}+{CB(L, r, Ln)}"))

        W = newsheet(S8, "⑧ 콜 반영 전 가치트리 = 신주인수권 + 사채",
                     f"{_NONCALL}의 가치다(콜 없음). 콜은 ⑮ 시트({_CALLED})에서만 반영한다.",
                     f"{S5} · {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln:
             f"={Q(S5)}!{L}{R0+r}+{Q(S6)}!{L}{R0+r}")

        W = newsheet(S9, "⑨ 의사결정트리  상환P · 상환C · 보유",
                     "사채가 어떻게 끝나는지(조기상환·매도청구·계속보유)를 표시한다. 신주인수권 행사 여부는 ⑤ 에서 "
                     "확인한다 — 그 칸이 «행사가치 − 100» 과 같으면 그 노드에서 행사한 것이다."
                     + ("  분리형이라 사채가 소멸해도 신주인수권은 남으므로 두 판단이 "
                        "서로를 건드리지 않는다."
                        if _det else
                        "  비분리형이라 사채가 소멸하면 신주인수권도 소멸한다. 다만 "
                        "통지기간 안에 행사할 기회는 남으므로 조기상환금액에도 "
                        "매도청구금액에도 그 자리의 행사가치를 더해 견준다."),
                     f"다음 열 {S5} · {S6}", call_on=False)
        if _det:
            _D = lambda L, r, Ln: xl_decide(None, f"{L}$7", f"{L}$8", CB(L, r, Ln), False, popen=f"{L}$7>0")
        else:
            # 매도청구금액에도 행사기회를 더한다 — 발행자가 사채를 매수해 가도
            # 투자자는 그 직전에 신주인수권을 행사해 그 값을 챙기므로, 콜이
            # 투자자 가치를 눌러 내리는지는 «매도청구금액 + 행사가치» 와 견줘야
            # 안다. 조기상환 쪽 «+ 행사가치» 와 같은 읽기다.
            _D = lambda L, r, Ln: xl_decide(None, f"{L}$7+{WV(L, r)}", f"{L}$8+{WV(L, r)}",
                                            f"{CB(L, r, Ln)}+{CE(L, r, Ln)}", False, popen=f"{L}$7>0")
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({L}$7>{L}$10+{xl_tol(f"{L}$7", f"{L}$10")},"상환P","만기상환")' if i == n else
            f"={_D(L, r, Ln)}"), txt=True)

    elif _needTF:
        W = newsheet(S5, "⑤ 지분가치트리",
                     "전환하면 전환가치, 상환하면 0, 계속보유하면 다음 시점 값을 무위험 이자율로 할인한 값이다.",
                     f"{S4} · {S9} · 다음 열 {S5}", call_on=False)
        # 만기 노드에서 자동전환(가정 auto=1)이면 주식을 받는다. 상환청구가 만기에
        # 열려 있으면 그 금액(+배당)과 견줘 큰 쪽이다. CASH 는 그 상환 갈래다.
        AU = K["auto"]
        CASH = lambda L: f"IF({L}$7>0,{L}$7+{L}$9,0)"
        _CV = lambda L, r: (f'OR({Q(S9)}!{L}{R0+r}="전환",{Q(S9)}!{L}{R0+r}="자동전환",'
                            f'{Q(S9)}!{L}{R0+r}="상장전환")')
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({_CV(L, r)},{Q(S4)}!{L}{R0+r},0)' if i == n else
            f'=IF({_CV(L, r)},{Q(S4)}!{L}{R0+r},'
            f'IF(OR({Q(S9)}!{L}{R0+r}="상환P",{Q(S9)}!{L}{R0+r}="상환C"),0,'
            f'({Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17)*EXP(-{L}$11*{K["dt"]})))'))

        W = newsheet(S6, "⑥ 부채가치트리",
                     "전환하면 0, 상환하면 그 금액, 계속보유하면 다음 시점 값을 위험 선도이자율로 할인한 값이다.",
                     f"{S9} · 다음 열 {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({_CV(L, r)},0,IF({AU}=1,{L}$7+{L}$9,MAX({L}$7,{L}$10)+{L}$9))' if i == n else
            f'=IF({Q(S9)}!{L}{R0+r}="상환P",{L}$7,IF({Q(S9)}!{L}{R0+r}="상환C",{L}$8,'
            f'IF({_CV(L, r)},0,'
            f'({Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17)*EXP(-{L}$12*{K["dt"]})+{L}$9)))'))

        W = newsheet(S7, "⑦ 계속보유가치트리",
                     "주식결제분은 무위험, 현금결제분은 위험 선도이자율로 따로 할인해 더한다 (TF 모형).",
                     f"다음 열 {S5} · {S6}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({AU}=1,{Q(S4)}!{L}{R0+r},{L}$10+{L}$9)" if i == n else
             f"=({Q(S5)}!{Ln}{R0+r}*{L}$16+{Q(S5)}!{Ln}{R0+r+1}*{L}$17)"
             f"*EXP(-{L}$11*{K['dt']})"
             f"+({Q(S6)}!{Ln}{R0+r}*{L}$16+{Q(S6)}!{Ln}{R0+r+1}*{L}$17)"
             f"*EXP(-{L}$12*{K['dt']})+{L}$9"))

        W = newsheet(S8, "⑧ 금융상품가치트리",
                     f"{_NONCALL}의 가치다(콜 없음). 콜은 ⑮ 시트({_CALLED})에서만 반영한다.",
                     f"{S4} · {S7}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({AU}=1,MAX({Q(S4)}!{L}{R0+r},{CASH(L)}),"
             f"MAX({Q(S7)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7+{L}$9))" if i == n else
             "=" + _ipo_if(L, r, f"{Q(S4)}!{L}{R0+r}",
                           f"MAX({Q(S7)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7)")))

        W = newsheet(S9, "⑨ 의사결정트리",
                     f"전환가치·조기상환금액·계속보유가치를 비교한다. {_NONCALL}라 "
                     "상환C(매도청구)는 없다. 전환가치가 상환 금액보다 허용오차(1e-9, 금액이 1,000 을 넘으면 "
                     "금액 × 1e-12) 넘게 클 때만 전환을 고르고, 같으면 상환을 고른다(전환가격 조정일에는 "
                     "전환가치가 정확히 100 이 되어 같아질 수 있다).",
                     f"{S4} · {S7}", call_on=False)
        # 콜이 없는 트리라 xl_decide(매도청구 = 무한대) 와 같은 식이다.
        _cv9 = lambda L, r: f"{Q(S4)}!{L}{R0+r}"
        _hd9 = lambda L, r: f"{Q(S7)}!{L}{R0+r}"
        _DEC = lambda L, r: (
             f'IF({_cv9(L, r)}>=MAX({L}$7,{_hd9(L, r)})+{xl_tol(_cv9(L, r), f"MAX({L}$7,{_hd9(L, r)})")},"전환",'
             f'IF(AND({L}$7>0,{L}$7>={_hd9(L, r)}-{xl_tol(f"{L}$7", _hd9(L, r))}),"상환P","보유"))')
        # 만기 — 엔진과 같다: 전환가치가 현금(MAX(조기상환금액, 만기상환금액) + 이자)을 허용오차만큼
        # 앞서면 전환, 아니면 «만기상환». 값 조서와 같은 표시다(예전에는 여기서도 중간 노드 식을 써
        # 조기상환이 닫힌 만기 노드를 «보유» 로 적었다 — 값은 같고 표시만 달랐다).
        _cm9 = lambda L: f"MAX({L}$7,{L}$10)+{L}$9"
        _MAT = lambda L, r: (f'IF({_cv9(L, r)}>={_cm9(L)}+{xl_tol(_cv9(L, r), _cm9(L))},'
                             f'"전환","만기상환")')
        fill(W, lambda i, r, L, Lp, Ln: (
             f'=IF({AU}=1,IF(OR({L}$7<=0,{_cv9(L, r)}>={CASH(L)}+{xl_tol(_cv9(L, r), CASH(L))}),"자동전환","상환P"),'
             f'{_MAT(L, r)})' if i == n else
             "=" + _ipo_if(L, r, '"상장전환"', _DEC(L, r))), txt=True)

    W = newsheet(S10, "⑩ 옵션 제외 사채가치트리  전환·조기상환·매도청구가 없는 사채",
                 "주가와 관계없으므로 같은 시점의 값이 모두 같다.", "가정")
    fill(W, lambda i, r, L, Lp, Ln: (f"={L}$10+{L}$9" if i == n else
         f"=({Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17)*EXP(-{L}$12*{K['dt']})"
         f"+{L}$9+{L}$10"))

    if _gs or _ksplit:
        W = newsheet(S11, "⑪ [GS] 전환확률트리",
                     ("전환하면 1, 상환(현금)이면 0, 계속보유하면 다음 시점의 상승·하락 상태별 전환확률을 위험중립확률(q, 1−q)로 가중평균한다." if _gs else
                      "전환하면 1, 상환(현금)이면 0, 계속보유하면 다음 시점의 상승·하락 상태별 전환확률을 위험중립확률(q, 1−q)로 "
                      "가중평균한다. TF 평가에서는 매도청구 행사가격을 주식결제·현금결제 성분으로 배분하는 데에만 쓴다(한공회 실무사례 4.3.3) — ⑪~⑭ 는 그 확률을 구하는 보조 계산이다."),
                     f"{S14} · 다음 열 {S11}")
        # 현금(상환P·상환C)이 동점이면 0 이다. 전환은 허용오차만큼 앞설 때만 1 이다.
        _v14 = lambda L, r: f"{Q(S14)}!{L}{R0+r}"
        # 열리지 않은 상환청구(금액 0)는 현금 갈래가 아니다 — 엔진의 _pvd 와 같은 읽기.
        _cash = lambda L, r: (f'OR(AND({L}$7>0,ABS({_v14(L, r)}-{L}$7)<{xl_tol(_v14(L, r), f"{L}$7")}),'
                              f'ABS({_v14(L, r)}-{L}$8)<{xl_tol(_v14(L, r), f"{L}$8")})')
        fill(W, lambda i, r, L, Lp, Ln: (
            f'=IF({_cash(L, r)},0,'
            f'IF(ABS({_v14(L, r)}-{Q(S4)}!{L}{R0+r})<{xl_tol(_v14(L, r), f"{Q(S4)}!{L}{R0+r}")},1,'
            + ('0))' if i == n else
               f'{Ln}{R0+r}*{L}$16+{Ln}{R0+r+1}*{L}$17))')), N4)

        W = newsheet(S11B, "⑪b 비전환확률트리  1 − 전환확률",
                     "GS 모형에서 전환하지 않고 현금(상환·만기상환)으로 끝날 위험중립확률이다 (한공회 실무사례 4.3.3). "
                     "⑪ 과 더하면 1 이다. 부도확률이나 실제 상환 가능성의 예측치가 아니다.", S11, call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: f"=1-{Q(S11)}!{L}{R0+r}", N4)

        W = newsheet(S12, "⑫ [GS] 위험조정할인율트리",
                     "이 칸을 직전 시점으로 할인할 때 쓰는 이자율이다. "
                     "전환확률로 무위험·위험 선도이자율을 가중하되, 직전 구간의 선도이자율을 쓴다.", S11)
        fill(W, lambda i, r, L, Lp, Ln:
             (f"={Q(S11)}!{L}{R0+r}*{Lp}$11+(1-{Q(S11)}!{L}{R0+r})*{Lp}$12"
              if i > 0 else "=0"), P2)

        W = newsheet(S13, "⑬ [GS] 보유가치트리",
                     "다음 시점의 상승·하락 노드 값을 각 노드의 할인율로 할인해 위험중립확률로 가중한다.", f"다음 열 {S12} · {S14}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({K['auto']}=1,{Q(S4)}!{L}{R0+r},{L}$10+{L}$9)" if i == n else
             f"={Q(S14)}!{Ln}{R0+r}*{L}$16*EXP(-{Q(S12)}!{Ln}{R0+r}*{K['dt']})"
             f"+{Q(S14)}!{Ln}{R0+r+1}*{L}$17*EXP(-{Q(S12)}!{Ln}{R0+r+1}*{K['dt']})+{L}$9"))

        W = newsheet(S14, "⑭ [GS] 금융상품가치트리",
                     "전환·상환·계속보유 가운데 선택한 결과의 값이다.", f"{S4} · {S13}", call_on=False)
        fill(W, lambda i, r, L, Lp, Ln: (
             f"=IF({K['auto']}=1,MAX({Q(S4)}!{L}{R0+r},IF({L}$7>0,{L}$7+{L}$9,0)),"
             f"MAX({Q(S13)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7+{L}$9))" if i == n else
             "=" + _ipo_if(L, r, f"{Q(S4)}!{L}{R0+r}",
                           f"MAX({Q(S13)}!{L}{R0+r},{Q(S4)}!{L}{R0+r},{L}$7)")))

    if _need15:
        def _build15(_SN, _mk, _lab, _locked):
            """⑮ 트랜치 한 장 — 콜을 넣은 격자. _locked 면 의무보유가 전환·조기상환 시작을 늦춘다.

            콜 한도와 의무보유 비율이 다르면(콜 70% · 의무보유 30%) 의무보유가 없는 몫을
            ⑮b 로 한 장 더 만든다 — 엔진의 call_mix 와 같은 나눔이다."""
            # ── 15 트랜치 ──
            # 고른 모형의 블록만 담는다. GS 블록은 TF 다섯 블록을 참조하지 않으므로
            # 어느 쪽을 골라도 나머지 절반은 만들 필요가 없다.
            W = newsheet(_SN, f"{_mk} {_lab}  콜이 반영되는 부분",
                         (f"콜 대상 물량은 의무보유로 전환{'·조기상환' if tm.k_lock_put else ''} 시작이 늦다. "
                          if _locked else "콜 대상 물량 가운데 의무보유가 없는 몫 — 전환·조기상환이 처음부터 열려 있다. ")
                         + f"{_NONCALL}{'와' if issuer_redeem(tm) else '과'}의 가치 차이가 콜의 가치다(유무가치비교법). "
                         + ("전환가치 다음에 GS 계산 블록(전환확률·할인율·계속보유·가치)을 둔다 — 이 조서의 모형이 GS 다."
                            if _gs else
                            "전환가치 다음에 TF 계산 블록(주식결제분·현금결제분·계속보유·가치·의사결정)을 둔다 — 이 조서의 모형이 TF 다."),
                         "가정", conv_cell=(K["cv30"] if _locked else None),
                         put_cell=(K["pt30"] if _locked else None))
            HH = n+3
            _bs = {"row": R0, "i": 0}
            def blk(t):
                _bs["row"] += HH
                row = _bs["row"]
                sec(W, row-1, f"{'가나다라마바'[_bs['i']]}  {t}", span=min(n+1, 14))
                _bs["i"] += 1
                put(W, row, 2, "하락 횟수 r ＼ 스텝", bold=True, size=8, fill=LIGHT, border=True, align="center")
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
                        f"=IF({L}$3=1,{_CVX(L, r)},0)",
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
                            p(c6, f'=IF({L}$7>{L}$10+{xl_tol(f"{L}$7", f"{L}$10")},"상환P","만기상환")', tx=True)
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
                            _dec = xl_decide(None, f"{L}$7", f"{L}$8", cb, _kfirst, _bwn, popen=f"{L}$7>0")
                            p(c6, f"={_dec}", tx=True)
                            p(c2, f"=MAX({wv},{ce})")
                            p(c3, "=" + xl_pick(f"{L}{c6+1+r}", f"{L}$7", f"{L}$8", cb,
                                                names=_bwn))
                        else:
                            _dec = xl_decide(None, f"{L}$7+{wv}", f"{L}$8+{wv}",
                                             f"{cb}+{ce}", _kfirst, _bwn, popen=f"{L}$7>0")
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
                            _cvm, _cmm = f"{L}{c1+1+r}", f"MAX({L}$7,{L}$10)+{L}$9"
                            p(c6, f'=IF({_AU}=1,IF(OR({L}$7<=0,{_cvm}>={_CS}+{xl_tol(_cvm, _CS)}),"자동전환","상환P"),'
                                  f'IF({_cvm}>={_cmm}+{xl_tol(_cvm, _cmm)},"전환",'
                                  f'IF({L}$7>={L}$10-{xl_tol(f"{L}$7", f"{L}$10")},"상환P","만기상환")))', tx=True)
                            p(c2, f'=IF(OR({L}{c6+1+r}="전환",{L}{c6+1+r}="자동전환"),{L}{c1+1+r},0)')
                            p(c3, f'=IF(OR({L}{c6+1+r}="전환",{L}{c6+1+r}="자동전환"),0,'
                                  f'IF({_AU}=1,{L}$7+{L}$9,MAX({L}$7,{L}$10)+{L}$9))')
                            p(c4, f"=IF({_AU}=1,{L}{c1+1+r},{L}$10+{L}$9)")
                            continue
                        e = f"({Ln}{c2+1+r}*{L}$16+{Ln}{c2+2+r}*{L}$17)*EXP(-{L}$11*{K['dt']})"
                        b = f"({Ln}{c3+1+r}*{L}$16+{Ln}{c3+2+r}*{L}$17)*EXP(-{L}$12*{K['dt']})"
                        _cvx = f'OR({L}{c6+1+r}="전환",{L}{c6+1+r}="상장전환")'
                        p(c4, f"={e}+{b}+{L}$9")
                        # 계약 우선순위 (pc_order · k_conv_resp) 에 따라 네 갈래다. 엔진과 같다 —
                        # 값은 xl_value, 결정은 xl_decide 가 만든다 (엔진의 node_decide 와 같은 식).
                        _cvC, _pvC, _kvC = f"{L}{c1+1+r}", f"{L}$7", f"{L}$8"
                        _hdC = f"{L}{c4+1+r}"
                        _dec = xl_decide(_cvC, _pvC, _kvC, _hdC, _kfirst, cresp=_cresp, popen=f"{_pvC}>0")
                        _val = xl_value(_cvC, _pvC, _kvC, _hdC, _kfirst, _cresp)
                        p(c5, "=" + _ipo_if(L, r, _cvC,
                                            f"IF({L}$5=1,{_val},MAX({_hdC},{_cvC},{_pvC}))"))
                        p(c6, "=" + _ipo_if(L, r, '"상장전환"', _dec), tx=True)
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
                # 현금(상환P·상환C)이 동점이면 전환확률 0 이다 — ⑪ 과 같은 규칙이고
                # 엔진(Pg)과도 같다. 종전에는 전환을 먼저 보고 등호로만 견주어서,
                # 전환가치와 상환금액이 같아지는 자리(의무보유로 전환 시작과 조기상환
                # 시작이 갈릴 때 생긴다)에서 GS 30% 트랜치가 엔진과 어긋났다.
                _gcash = lambda L, r: (f"OR(AND({L}$7>0,ABS({L}{c10+1+r}-{L}$7)<{xl_tol(f'{L}{c10+1+r}', f'{L}$7')}),"
                                       f"ABS({L}{c10+1+r}-{L}$8)<{xl_tol(f'{L}{c10+1+r}', f'{L}$8')})")
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
                            p(c7, f"=IF({_gcash(L, r)},0,"
                                  f"IF(ABS({L}{c10+1+r}-{L}{c1+1+r})<{xl_tol(f'{L}{c10+1+r}', f'{L}{c1+1+r}')},1,0))", N4)
                        else:
                            p(c9, f"={Ln}{c10+1+r}*{L}$16*EXP(-{Ln}{c8+1+r}*{K['dt']})"
                                  f"+{Ln}{c10+2+r}*{L}$17*EXP(-{Ln}{c8+2+r}*{K['dt']})+{L}$9")
                            # ⑮ TF 와 같은 식을 GS 의 보유가치로 부른다. 한 격자에서
                            # 두 모형이 다른 계약을 읽으면 안 된다.
                            _gcall = xl_value(f"{L}{c1+1+r}", f"{L}$7", f"{L}$8",
                                              f"{L}{c9+1+r}", _kfirst, _cresp)
                            p(c10, "=" + _ipo_if(L, r, f"{L}{c1+1+r}",
                                                 f"IF({L}$5=1,{_gcall},"
                                                 f"MAX({L}{c9+1+r},{L}{c1+1+r},{L}$7))"))
                            p(c7, f"=IF({_gcash(L, r)},0,"
                                  f"IF(ABS({L}{c10+1+r}-{L}{c1+1+r})<{xl_tol(f'{L}{c10+1+r}', f'{L}{c1+1+r}')},1,"
                                  f"{Ln}{c7+1+r}*{L}$16+{Ln}{c7+2+r}*{L}$17))", N4)
                        p(c8, (f"={L}{c7+1+r}*{Lp}$11+(1-{L}{c7+1+r})*{Lp}$12"
                               if i > 0 else "=0"), P2)

            # 마지막으로 놓인 블록 아래에 둔다. TF 는 의사결정(바)이 값 블록(마) 뒤에
            # 오므로 last 를 그대로 쓰면 그 위에 겹쳐 쓰게 된다.
            RT = _bs["row"] + n + 3
            sec(W, RT, "결과", span=6)
            put(W, RT+1, 2, f"{_lab} 가치 · {tm.model} (t=0)", bold=True)
            put(W, RT+1, 3, f"=C{last+1}", bold=True, fmt=N2, align="right")
            return RT
        RT = _build15(S15, "⑮", _CALLED, True)
        RTb = (_build15(S15B, "⑮b", _CALLED_FREE, False) if _split_lock else RT)

    # ── 16 부채요소 ──
    D = wb.create_sheet(S16); D.sheet_view.showGridLines = False
    D.column_dimensions["B"].width = 20
    for i in range(n+1): D.column_dimensions[gl(3+i)].width = 9
    title(D, 2, "⑯ 부채요소  전환권이 없는 사채에 조기상환권만 붙인 값", span=min(n+1, 14))
    put(D, 3, 2, "전환이 없으면 주가와 무관하므로 한 줄로 끝난다. "
        "만기부터 왼쪽으로 오며 MAX(조기상환금액, 계속보유)를 고른다.", color=GREY, size=9)
    for r, nm in enumerate(["날짜", "스텝(노드 번호)", "조기상환 가능 (1=예)", "조기상환금액", "쿠폰",
                            "만기상환", "위험 선도이자율", "부채요소"], start=4):
        put(D, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
    for i in range(n+1):
        L = gl(3+i); Lp = gl(2+i) if i > 0 else None; Ln = gl(4+i) if i < n else None
        g = lambda r, v, fm=None, col="000000": put(D, r, 3+i, v, fmt=fm,
                                                    align="center", size=8, color=col)
        st = f"{L}$5"
        yr = (f"IF({K['accb']}=1,({K['elm']}+{st}*{K['remm']}/{K['n']})/12,"
              f"{st}*{K['dt']}+{K['elm']}/12)")
        g(4, f"={K['d_base']}+{st}*{K['dt']}*365", DATE, GREY)
        g(5, (0 if i == 0 else f"={Lp}$5+1"), N0)
        # 트리와 «같은 함수» 로 만든다. 여기서 다시 계산하면 계약서 표를 넣은 계약에서
        # 트리와 부채요소가 다른 금액을 쓴다 — 엑셀에서 재계산할 때만 드러나는 어긋남이다.
        g(6, "=" + x_pflag(st), N0)
        g(7, (f"=IF({L}$6=1,{DPQ}!{L}${_DPR['pv']}+{_cadd(i, 'pcadd')},0)" if DPON else
               f"=IF({L}$6=1,{COMQ}!{L}${CROW['pamt']},0)"), N2)
        g(8, f"={x_cpn(i)}", N2)
        g(9, f"=IF({st}={K['n']},{(_DPRED if DPON else K['red'])},0)", N2)
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
        title(Dc, 2, "⑯c 전환권 제외 우선주부채 + 발행자 상환권  상환청구권과 발행자 상환권을 함께 반영한 값",
              span=min(n+1, 14))
        put(Dc, 3, 2, "MAX(상환청구금액, MIN(계속보유가치, 발행자 상환금액)). 전환권이 없으면 발행자가 "
            "상환할 유인이 작아 ⑯ 과 비슷하다 — ⑯ 과의 차이가 전환권을 제외한 격자에서 계산한 발행자 상환권 가치다.",
            color=GREY, size=9)
        for r, nm in enumerate(["날짜", "스텝(노드 번호)", "조기상환 가능 (1=예)", "조기상환금액", "쿠폰",
                                "만기상환", "위험 선도이자율", "매도청구 가능 (1=예)", "매도청구금액",
                                "부채요소 (콜 포함)"], start=4):
            put(Dc, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None; Ln = gl(4+i) if i < n else None
            g = lambda r, v, fm=None, col="000000": put(Dc, r, 3+i, v, fmt=fm,
                                                        align="center", size=8, color=col)
            st = f"{L}$5"
            yr = (f"IF({K['accb']}=1,({K['elm']}+{st}*{K['remm']}/{K['n']})/12,"
                  f"{st}*{K['dt']}+{K['elm']}/12)")
            g(4, f"={K['d_base']}+{st}*{K['dt']}*365", DATE, GREY)
            g(5, (0 if i == 0 else f"={Lp}$5+1"), N0)
            g(6, "=" + x_pflag(st), N0)
            g(7, (f"=IF({L}$6=1,{DPQ}!{L}${_DPR['pv']}+{_cadd(i, 'pcadd')},0)" if DPON else
               f"=IF({L}$6=1,{COMQ}!{L}${CROW['pamt']},0)"), N2)
            g(8, f"={x_cpn(i)}", N2)
            g(9, f"=IF({st}={K['n']},{(_DPRED if DPON else K['red'])},0)", N2)
            if i < n: g(10, forward_rate(CR, i*dt_, (i+1)*dt_), P2, AMB)
            g(11, "=" + x_kflag(st), N0)
            g(12, f"=IF({L}$11=1,{COMQ}!{L}${CROW['kamt']},999999)", N2)
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
            for r, nm in enumerate(["날짜", "스텝(노드 번호)", "조기상환 가능 (1=예)", "조기상환금액",
                                    "쿠폰", "만기상환", "기준금리 a", "확정 스프레드"],
                                   start=4):
                put(W, r, 2, nm, bold=True, size=8, fill=LIGHT, border=True)
            for i in range(n+1):
                L = gl(3+i); Lp = gl(2+i) if i > 0 else None
                g = lambda r, v, fm=None, col="000000": put(W, r, 3+i, v, fmt=fm,
                                                            align="center", size=8, color=col)
                st = f"{L}$5"
                yr = (f"IF({K['accb']}=1,({K['elm']}+{st}*{K['remm']}/{K['n']})/12,"
                      f"{st}*{K['dt']}+{K['elm']}/12)")
                g(4, f"={K['d_base']}+{st}*{K['dt']}*365", DATE, GREY)
                g(5, (0 if i == 0 else f"={Lp}$5+1"), N0)
                g(6, "=" + x_pflag(st), N0)
                g(7, (f"=IF({L}$6=1,{DPQ}!{L}${_DPR['pv']}+{_cadd(i, 'pcadd')},0)" if DPON else
               f"=IF({L}$6=1,{COMQ}!{L}${CROW['pamt']},0)"), N2)
                g(8, f"={x_cpn(i)}", N2)
                g(9, f"=IF({st}={K['n']},{(_DPRED if DPON else K['red'])},0)", N2)
                if i < n:
                    g(10, BP["a"][i], P2, AMB)
                    # 확정 스프레드 = 위험 선도 − 무위험 선도. 트리 11·12행이 그 값이다.
                    g(11, f"=IF({K['bbase']}=1,{Q(S1)}!{L}$12-{Q(S1)}!{L}$11,0)", P2)
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
        # 기준금리 a 는 금리곡선·금리변동성에 맞춰 앱이 역산한 고정값이다 — 엑셀에서 손으로 고치지 못하게
        # 시트를 보호한다(암호 없음). 바꾸려면 앱에서 곡선·변동성을 바꿔 다시 평가한다.
        W.protection.sheet = True
        bhead(W, "BDT 단기이자율격자  r(i,j) = a · exp(2σ·j·√Δt)",
              "로그정규 분포를 따르므로 단기이자율이 음수가 되지 않는다. j 는 금리 상승 횟수이고 클수록 금리가 높다(주가 트리와 달리 표의 아래쪽이 높은 금리). 기준금리 a 는 이 격자로 계산한 할인계수가 적용 금리곡선의 할인계수와 같아지도록 앱이 역산한 값이다. 역산한 값이라 주황색이고, 손으로 고치지 못하게 이 시트는 보호되어 있다 — BDT 변동성 σ 나 금리곡선을 바꾸려면 앱에서 "
              "다시 계산해 조서를 새로 만든다.")
        for i in range(n):
            L = gl(3+i)
            for j in range(i+1):
                put(W, RB+j, 3+i,
                    f"={L}$10*EXP(2*{K['bsig']}*$B{RB+j}*SQRT({K['dt']}))",
                    fmt=P2, size=8, align="right")

        # 기준금리 a 가 곡선을 맞추는지(도달가격 Σ Q = 할인계수)는 앱이 평가할 때 확인한다.

        W = wb.create_sheet(SB2)
        bhead(W, "BDT 부채요소  전환권 제외 사채 + 조기상환청구권 (금리격자)",
              "MAX(조기상환금액, 계속보유) 를 고른다. 계속보유는 다음 두 칸을 "
              "0.5 씩 가중해 그 노드의 단기이자율로 할인한 값이다. 확정금리 격자(⑯)와의 차이는 "
              "금리변동성 반영에 따른 평가금액 차이다 — 현금흐름·행사조건이 같을 때만 이렇게 읽는다.")
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
        put(W, RB+n+3, 2, "확정금리 격자 (⑯)", bold=True)
        put(W, RB+n+3, 3, f"={Q(S16)}!C13", fmt=N2, align="right")
        put(W, RB+n+4, 2, "차이 = 금리변동성 반영에 따른 평가금액 차이", bold=True)
        put(W, RB+n+4, 3, f"=C{RB+n+2}-C{RB+n+3}", bold=True, fmt=N2, align="right")

    if _need1 or _need2:
        # ── 17~20 옵션차익혼합할인법 ──
        # 제3자 지정 가능 콜옵션은 전환사채를 기초자산으로 하는 복합옵션이다.
        # 기초자산은 콜과 부속조항(의무보유)을 뺀 ⑧ 이다 (책 4.4.3, 부속예제 4-4).
        # 전환확률(본문 4.3.3)로 나누는 방법 2 는 구성비율을 쓰지 않는다 — 참고용 시트를 싣지 않는다.
        if _need1 or not _ksplit:
            W = newsheet(S17, "⑰ 구성비율트리  지분 몫 ÷ 전환사채 가치",
                         "노드 가치 중 주식에서 온 몫의 비율이다. **비례균등차감법**(지분·채권 구분 "
                         "기준 0)을 고를 때 쓰는 비중이고, 본문 4.3.3(기준 1)을 고르면 대신 ⑪ "
                         "전환확률을 쓴다."
                         + ("  지금 설정은 본문 4.3.3 이라 이 시트는 참고용이다." if _ksplit else ""),
                         f"{S5} · {S8}", call_on=False)
            fill(W, lambda i, r, L, Lp, Ln:
                 f"=IF({Q(S8)}!{L}{R0+r}=0,0,{Q(S5)}!{L}{R0+r}/{Q(S8)}!{L}{R0+r})", N4)

        if _need1:                      # ⑱·⑳ 은 GS식 전환가중확률할인(방법 1) 전용이다
            _wsh = S11 if _ksplit else S17
            W = newsheet(S18, "⑱ 혼합할인율트리  비중 × 무위험 + (1−비중) × 위험",
                         "이 칸을 직전 시점으로 할인할 때 쓰는 이자율이다. 지분 성격에는 무위험, "
                         "채권 성격에는 위험 선도이자율을 섞는다. 비중은 "
                         + ("**⑪ GS 전환확률**(본문 4.3.3)" if _ksplit
                            else "**⑰ 가치 구성비율**(비례균등차감법)")
                         + " 이다. 비중이 언제나 0~1 이라 할인율이 무위험과 위험 사이를 벗어나지 "
                           "않는다.",
                         _wsh, call_on=False)
            fill(W, lambda i, r, L, Lp, Ln:
                 (f"={Q(_wsh)}!{L}{R0+r}*{Lp}$11+(1-{Q(_wsh)}!{L}{R0+r})*{Lp}$12"
                  if i > 0 else "=0"), P2)

        # ── ⑲ · ⑲a · (⑲b) — 콜이 어떻게 되는지를 셀로 편다 (엔진 call_third_party 와 같은 규칙) ──
        # 같은 조건을 모든 수식에 되풀이하지 않도록 «즉시행사가치(⑲)» · «존속 여부(⑲a)» ·
        # «행사·보유 판단(⑲b, 성분 분리할인)» 을 한 번씩 계산하고, ⑳·㉓·㉔ 는 그 칸만 본다.
        # 우선순위(pc_order)와 전환 대응(k_conv_resp)은 트리 구조를 정하는 값이라 여기서 굳힌다
        # (가정 시트 pcord · kresp). 의무보유 만료·조기상환 제한은 셀을 가리켜 엑셀에서 바꿔도 따라온다.
        _cv4 = lambda L, r: f"{Q(S4)}!{L}{R0+r}"
        _dec9 = lambda L, r: f"{Q(S9)}!{L}{R0+r}"
        _canc = lambda L, r: f"AND({L}$2>{K['lockend']},{_cv4(L, r)}>0)"
        _canp = lambda L, r: f"AND(OR({L}$2>{K['lockend']},{K['lkput']}=0),{L}$7>0)"
        _cresp_x = _cresp and not _bwc     # 현금납입 BW 는 행사해도 사채가 남아 전환으로 피할 수 없다

        def _resp(L, r, lk=None):
            """콜을 행사당하는 순간 투자자가 대신 고르는 것 — 전환(대응 가능 계약) · 조기상환(풋 우선).
            lk 는 의무보유가 걸린 마지막 스텝 셀 (의무보유가 없는 몫은 −1)."""
            lk = K['lockend'] if lk is None else lk
            canc = f"AND({L}$2>{lk},{_cv4(L, r)}>0)"
            canp = f"AND(OR({L}$2>{lk},{K['lkput']}=0),{L}$7>0)"
            parts = []
            if _cresp_x:
                parts.append(f"AND({canc},{_cv4(L, r)}>={L}$8+{xl_tol(_cv4(L, r), f'{L}$8')})")
            if not _kfirst:
                parts.append(f"AND({canp},{L}$7>={L}$8-{xl_tol(f'{L}$7', f'{L}$8')})")
            return parts[0] if len(parts) == 1 else (f"OR({','.join(parts)})" if parts else "")

        _r19 = ["매도청구 행사기간에만 값이 있다. 기초자산은 ⑧(콜 반영 전 전환사채 가치)이다."]
        _alts = []
        if _cresp_x:
            _alts.append("전환가치가 매도청구금액을 넘으면 전환한다(통지 뒤 전환 가능 계약)")
        if not _kfirst:
            _alts.append("조기상환금액이 매도청구금액 이상이면 조기상환한다(투자자 풋 우선 계약)")
        if _alts:
            _r19.append("콜을 행사당하는 순간 투자자가 " + " · ".join(_alts)
                        + " — 그러면 콜이 살 사채가 없어 0 이다. 의무보유 기간에는 그 권리를 쓰지 못해 대응할 수 없다.")
        else:
            _r19.append("이 계약에서는 투자자가 콜 통지에 전환·조기상환으로 대응할 수 없다(콜이 두 권리보다 먼저).")
        _r19.append("만기일에는 만기상환·전환이 매도청구보다 먼저라 0 이다.")

        # 존속 여부 — 소멸 · 지금만 · 존속 · 만기. 투자자가 이 노드에서 스스로 전환·조기상환해 사채를
        # 끝내는 자리(⑨)에서, 그 권리가 콜보다 먼저면 콜도 사라지고(소멸), 콜이 먼저면 지금 행사하지
        # 않으면 사라진다(지금만). 의무보유 기간 안이면 투자자가 그 권리를 쓰지 못하므로 콜이 이어진다.
        _cfate = "소멸" if _cresp_x else "지금만"
        _pfate = "지금만" if _kfirst else "소멸"
        _n19a = ("소멸 — 상장 강제전환이거나, 투자자가 콜보다 먼저 "
                 + ("전환·조기상환" if (_cresp_x and not _kfirst) else "전환" if _cresp_x
                    else "조기상환" if not _kfirst else "움직일 수 있는 권리 없이")
                 + "해 사채가 없어진다. 지금만 — 투자자가 이 노드에서 사채를 끝내지만 콜이 먼저라, 지금 "
                   "행사하지 않으면 사라진다. 존속 — 콜이 다음 시점으로 이어진다(의무보유 기간 안의 "
                   "전환·조기상환 노드 포함 — 그 권리를 쓰지 못한다). 만기 — 만기일에는 만기상환·전환이 "
                   "먼저라 콜을 반영하지 않는다.")
        def _build_opt_a(S19x, SA, SV, lk, tag):
            """⑲ 즉시행사가치 · ⑲a 존속 여부 · (방법 1 이면) ⑳ 콜 가치 — lk 는 의무보유가 걸린 마지막 스텝 셀.

            콜 한도와 의무보유 비율이 다르면 의무보유가 없는 몫(lk = −1)을 한 벌 더 만든다
            — 엔진의 call_mix 와 같은 나눔이다. 의무보유는 투자자가 콜 통지에 전환·조기상환으로
            대응하는 것(⑲)과 스스로 사채를 끝내는 것(⑲a)을 함께 막으므로 두 시트 모두 몫마다 다르다."""
            W = newsheet(S19x, "⑲ 콜 즉시행사가치트리  지금 행사하면 얻는 총이익 MAX(⑧ − 매도청구금액, 0)" + tag,
                         " ".join(_r19), f"{S8} · {S4} · {S9}")

            def _v19(i, r, L):
                if i == n:
                    return "=0"
                body = f"MAX({Q(S8)}!{L}{R0+r}-{L}$8,0)"
                rs = _resp(L, r, lk)
                return (f"=IF(AND({L}$5=1,NOT({rs})),{body},0)" if rs else f"=IF({L}$5=1,{body},0)")
            fill(W, lambda i, r, L, Lp, Ln: _v19(i, r, L))
            x19 = lambda L, r: f"{Q(S19x)}!{L}{R0+r}"
            canc = lambda L, r: f"AND({L}$2>{lk},{_cv4(L, r)}>0)"
            canp = lambda L, r: f"AND(OR({L}$2>{lk},{K['lkput']}=0),{L}$7>0)"
            W = newsheet(SA, "⑲a 콜 존속 여부  소멸 · 지금만 · 존속 · 만기" + tag, _n19a, f"{S9} · {S4}")

            def _v19a(i, r, L):
                if i == n:
                    return '="만기"'
                d = _dec9(L, r)
                out = f'IF(AND({d}="상환P",{canp(L, r)}),"{_pfate}","존속")'
                if not _bwc:
                    out = f'IF(AND({d}="전환",{canc(L, r)}),"{_cfate}",{out})'
                return f'=IF({d}="상장전환","소멸",{out})'
            fill(W, lambda i, r, L, Lp, Ln: _v19a(i, r, L), txt=True)
            st = lambda L, r: f"{Q(SA)}!{L}{R0+r}"

            if _need1:
                W = newsheet(SV, "⑳ 매도청구권가치트리  미국형 복합옵션" + tag,
                             "⑲a 가 소멸·만기면 0, 지금만이면 ⑲, 존속이면 MAX(⑲, 계속보유가치)다. 계속보유가치는 "
                             "다음 시점 두 노드의 콜 가치를 각 노드의 혼합할인율(⑱)로 할인해 위험중립확률(q, 1−q)로 "
                             "가중한다 — 두 할인계수를 평균금리 하나로 바꾸지 않는다.",
                             f"{S18} · {S19x} · {SA} · 다음 열 {SV}")
                fill(W, lambda i, r, L, Lp, Ln: ("=0" if i == n else
                     f'=IF({st(L, r)}="소멸",0,IF({st(L, r)}="지금만",{x19(L, r)},'
                     f"MAX({x19(L, r)},"
                     f"{Ln}{R0+r}*{L}$16*EXP(-{Q(S18)}!{Ln}{R0+r}*{K['dt']})"
                     f"+{Ln}{R0+r+1}*{L}$17*EXP(-{Q(S18)}!{Ln}{R0+r+1}*{K['dt']}))))"))
                put(W, R0+n+3, 2, "매도청구권 (한도 반영 전, t=0)", bold=True)
                put(W, R0+n+3, 3, f"=C{R0}", bold=True, fmt=N2, align="right")
            return st, x19

        _st, _x19 = _build_opt_a(S19, S19A, S20, K['lockend'], "")
        _stN, _x19N = (_build_opt_a(S19N, S19AN, S20N, "-1", "  · 의무보유 없는 몫") if _split_lock
                       else (None, None))

    if _need2 and _ksplit:
        # ── 17a·17b 행사가를 GS 전환확률로 분해 (본문 4.3.3) ──
        # 기초자산의 지분(⑤)·채권(⑥)은 이미 나뉘어 있으나 행사가(8행)는 나뉘어 있지 않다.
        # asset-or-nothing 콜의 위험중립확률이 GS 전환확률(⑪)이고 cash-or-nothing 풋의
        # 그것이 1−⑪(⑪b)이므로 행사가를 그 확률로 나눈다. ⑲ 이 0 보다 클 때만 싣는다 — 그래야
        # 두 성분의 합이 언제나 ⑲ 다. 한 성분은 음수일 수 있다(«확률 × (그 시나리오 현가 − 행사가)»).
        def _build_17ab(_SA17, _SB17, _S19x, x19, tag):
            """행사가를 GS 전환확률로 나눈 두 성분 — ⑲(몫마다 다르다)이 0 보다 클 때만 값이 있다."""
            W = newsheet(_SA17, "⑰a 콜 행사 시 주식결제 성분  ⑤ − ⑪ × 매도청구금액" + tag,
                         "콜을 즉시 행사할 때의 총이익(⑲) 가운데 GS 전환확률에 따라 배분한 주식결제 성분이다. "
                         "⑲ 이 0 보다 클 때만 값이 있고(그 밖은 0), ⑰b 와 더하면 ⑲ 가 된다. 분해 계산상 성분이라 "
                         "한 성분이 음수일 수 있으며 — 콜이 주식으로 갈 시나리오에서만 이득이라는 뜻 — 실제 현금 "
                         "수령액이나 독립 옵션가격이 아니다.",
                         f"{S5} · {S11} · {_S19x}")
            fill(W, lambda i, r, L, Lp, Ln:
                 f"=IF({x19(L, r)}>0,{Q(S5)}!{L}{R0+r}-{Q(S11)}!{L}{R0+r}*{L}$8,0)", N4)

            W = newsheet(_SB17, "⑰b 콜 행사 시 현금결제 성분  ⑥ − (1−⑪) × 매도청구금액" + tag,
                         "콜을 즉시 행사할 때의 총이익(⑲) 가운데 현금결제 성분이다. ⑲ 이 0 보다 클 때만 값이 "
                         "있고, ⑰a 와 더하면 ⑲ 가 된다. 한 성분이 음수일 수 있다.",
                         f"{S6} · {S11B} · {_S19x}")
            fill(W, lambda i, r, L, Lp, Ln:
                 f"=IF({x19(L, r)}>0,{Q(S6)}!{L}{R0+r}-{Q(S11B)}!{L}{R0+r}*{L}$8,0)", N4)

        _build_17ab(S17A, S17B, S19, _x19, "")
        if _split_lock:
            _build_17ab(S17AN, S17BN, S19N, _x19N, "  · 의무보유 없는 몫")

    if _need2:
        # ── 21~24 옵션차익혼합할인법 · 방법 2 (지분·부채 분리) ──
        # 값 하나를 섞은 할인율로 할인하는 대신, 콜옵션 가치를 지분 몫과 부채 몫으로
        # 쪼개 각각 무위험·위험 선도이자율로 할인한다 (책 4.4.3, 부속예제 4-4 방법2).
        def _build_opt_b(_S21, _S22, _S19B, _S23, _S24, st, x19, _SA17, _SB17, _S19x, tag):
            """방법 2 (주식결제·현금결제 분리) 의 ㉑~㉔ — st·x19 는 그 몫의 존속 여부(⑲a)·즉시행사가치(⑲)."""
            W = newsheet(_S21, "㉑ 콜 계속보유가치 · 주식결제 성분 (옵션차익법 · 주식결제·현금결제 분리)" + tag,
                         "다음 시점 두 노드의 ㉓(주식결제 성분)을 위험중립확률로 가중해 무위험 선도이자율로 할인한다.",
                         f"다음 열 {_S23}")
            fill(W, lambda i, r, L, Lp, Ln: ("=0" if i == n else
                 f"=({Q(_S23)}!{Ln}{R0+r}*{L}$16+{Q(_S23)}!{Ln}{R0+r+1}*{L}$17)"
                 f"*EXP(-{L}$11*{K['dt']})"), N4)

            W = newsheet(_S22, "㉒ 콜 계속보유가치 · 현금결제 성분 (옵션차익법 · 주식결제·현금결제 분리)" + tag,
                         "다음 시점 두 노드의 ㉔(현금결제 성분)을 위험중립확률로 가중해 위험 선도이자율로 할인한다.",
                         f"다음 열 {_S24}")
            fill(W, lambda i, r, L, Lp, Ln: ("=0" if i == n else
                 f"=({Q(_S24)}!{Ln}{R0+r}*{L}$16+{Q(_S24)}!{Ln}{R0+r+1}*{L}$17)"
                 f"*EXP(-{L}$12*{K['dt']})"), N4)

            # 행사·보유 판단은 한 번만 한다 — ⑲ 과 ㉑ + ㉒ 를 견준다. 성분마다 따로 유리한 쪽을 고르지
            # 않는다(성분별 MAX 없음). 행사이득이 0 이면 행사하지 않는다 — 계속보유 성분이 음수일 수
            # 있어 «0 >= 음수» 로 잘못 행사 판정이 나는 것을 막는다.
            W = newsheet(_S19B, "⑲b 콜 행사·보유 판단  ⑲ 과 ㉑ + ㉒ 를 한 번 견준다" + tag,
                         "행사 — 즉시행사가치(⑲)가 0 보다 크고 계속보유가치(㉑ + ㉒) 이상이다. 보유 — 그 밖의 "
                         "존속 노드. 소멸 — 존속 여부(⑲a)가 소멸이거나, 지금만인데 행사이익이 없다. 만기 — "
                         "만기일에는 콜을 반영하지 않는다. 주식결제·현금결제 성분마다 따로 고르지 않는다 — "
                         "여기서 정한 판단을 ㉓·㉔ 가 함께 따른다.",
                         f"{_S19x} · {_S19B} · {_S21} · {_S22}")
            fill(W, lambda i, r, L, Lp, Ln: ('="만기"' if i == n else
                 f'=IF({st(L, r)}="소멸","소멸",IF({st(L, r)}="지금만",IF({x19(L, r)}>0,"행사","소멸"),'
                 f'IF(AND({x19(L, r)}>0,{x19(L, r)}>={Q(_S21)}!{L}{R0+r}+{Q(_S22)}!{L}{R0+r}),"행사","보유")))'),
                 txt=True)
            _dx = lambda L, r: f"{Q(_S19B)}!{L}{R0+r}"
            _eq = ((lambda L, r: f"{Q(_SA17)}!{L}{R0+r}") if _ksplit else
                   (lambda L, r: f"{x19(L, r)}*{Q(S17)}!{L}{R0+r}"))
            _db = ((lambda L, r: f"{Q(_SB17)}!{L}{R0+r}") if _ksplit else
                   (lambda L, r: f"{x19(L, r)}*(1-{Q(S17)}!{L}{R0+r})"))
            _src = (f"{_SA17} · {_SB17}" if _ksplit else S17) + f" · {_S19x} · {_S19B} · {_S21} · {_S22}"
            _how = ("⑰a(GS 전환확률로 배분한 주식결제 성분)" if _ksplit else "⑲ × ⑰(가치 구성비율)")
            W = newsheet(_S23, "㉓ 콜 최종가치 · 주식결제 성분 (옵션차익법 · 주식결제·현금결제 분리)" + tag,
                         f"⑲b 가 행사면 {_how}, 보유면 ㉑, 소멸·만기면 0 이다.", _src)
            fill(W, lambda i, r, L, Lp, Ln:
                 f'=IF({_dx(L, r)}="행사",{_eq(L, r)},IF({_dx(L, r)}="보유",{Q(_S21)}!{L}{R0+r},0))', N4)

            W = newsheet(_S24, "㉔ 콜 최종가치 · 현금결제 성분 (옵션차익법 · 주식결제·현금결제 분리)" + tag,
                         "판단은 ⑲b 하나를 ㉓ 과 함께 따른다. "
                         + ("행사면 ⑰b(현금결제 성분), 보유면 ㉒, 소멸·만기면 0 이다." if _ksplit else
                            "행사면 ⑲ × (1 − ⑰), 보유면 ㉒, 소멸·만기면 0 이다."),
                         _src)
            fill(W, lambda i, r, L, Lp, Ln:
                 f'=IF({_dx(L, r)}="행사",{_db(L, r)},IF({_dx(L, r)}="보유",{Q(_S22)}!{L}{R0+r},0))', N4)
            put(W, R0+n+3, 2, "매도청구권 · 옵션차익법 주식결제·현금결제 분리 (한도 반영 전, t=0)", bold=True)
            put(W, R0+n+3, 3, f"={Q(_S23)}!C{R0}+C{R0}", bold=True, fmt=N2, align="right")

        _build_opt_b(S21, S22, S19B, S23, S24, _st, _x19, S17A, S17B, S19, "")
        if _split_lock:
            _build_opt_b(S21N, S22N, S19BN, S23N, S24N, _stN, _x19N, S17AN, S17BN, S19N, "  · 의무보유 없는 몫")

    # ── 결과 ──
    R = wb.create_sheet("결과"); R.sheet_view.showGridLines = False
    for cc, w in (("B", 36), ("C", 14), ("D", 16), ("E", 12), ("F", 42)):
        R.column_dimensions[cc].width = w
    title(R, 2, "평가결과", span=5)
    put(R, 3, 2, "모든 값이 앞의 트리 시트에서 수식으로 넘어온다.", color=GREY, size=9)
    sec(R, 5, "1. 물량별 가치 (액면 100 기준)", span=5)
    # 주계약과 부채요소에는 전환이 없어 TF 와 GS 가 항상 같다. 모형 선택이
    # 갈라지는 곳은 트랜치 둘뿐이므로 여기서 한 번만 고른다.
    # 앱에서 고른 것만 값이 든다. 행 자리는 그대로 두어 아래 참조가 깨지지 않게 한다.
    B_ = ""
    _t70 = f"={Q(S14)}!C{R0}" if _gs else f"={Q(S8)}!C{R0}"
    _t30 = f"={Q(S15)}!C{RT+1}" if _need15 else B_
    # 유무가치비교법일 때만 콜이 걸리는 몫과 안 걸리는 몫(트랜치)으로 나눈다. 다른 방법이면
    # 08 은 콜을 반영하기 전 전환사채 «전체» 의 값이다 — 트랜치라고 부르면 대상을 오해한다.
    _N0 = _NONCALL if _need15 else "전환사채 전체 · 매도청구권 반영 전 공정가치"
    _N1 = _CALLED if _need15 else "— (물량 구분 없음)"
    for i, (nm, fx) in enumerate([
            (f"{_N0} · TF", B_ if _gs else _t70),
            (f"{_N0} · GS", _t70 if _gs else B_),
            (f"{_N1} · TF", B_ if _gs else _t30),
            (f"{_N1} · GS", _t30 if _gs else B_),
            (f"적용 · {_N0}", _t70),
            (f"적용 · {_N1}", _t30),
            ("가중 평균", (f"=(1-{K['cw']})*C10+{K['lkw']}*C11+({K['cw']}-{K['lkw']})*C13" if _split_lock
                          else f"=(1-{K['cw']})*C10+{K['cw']}*C11") if _need15 else B_)]):
        bold = (i >= 4)
        put(R, 6+i, 2, nm, bold=bold, border=True)
        put(R, 6+i, 3, fx, bold=bold, fmt=N2, align="right", border=True)
    if _need15 and _split_lock:
        # 의무보유가 없는 콜 대상 몫 — ⑮n 시트 (전환·조기상환이 처음부터 열려 있다)
        put(R, 13, 2, f"적용 · {_CALLED_FREE}", bold=True, border=True)
        put(R, 13, 3, f"={Q(S15B)}!C{RTb+1}", bold=True, fmt=N2, align="right", border=True)
    sec(R, 14, "2. 구성요소", span=5)
    put(R, 15, 3, "100 기준", bold=True, fill=LIGHT, align="center", border=True)
    put(R, 15, 4, "전액 기준 (원)", bold=True, fill=LIGHT, align="center", border=True)
    put(R, 15, 5, "공시", bold=True, fill=LIGHT, align="center", border=True)
    items = [("주계약 공정가치 (옵션 없는 사채)", f"={Q(S10)}!C{R0}"),
             ("부채요소 (사채 + 조기상환권) — 조기상환권을 포함한 공정가치",
              f"='BDT 부채요소'!C{14+n+2}" if _bdt else f"={Q(S16)}!C13"),
             ("조기상환청구권 — 조기상환권의 증분가치 (C17 − C16)", "=C17-C16"),
             # 의무보유 물량 × 묶인 1단위 + (콜 한도 − 의무보유 물량) × 묶이지 않은 1단위 (call_mix)
             ("매도청구권 · 유무가치비교법",
              ((f"={K['lkw']}*(C10-C11)+({K['cw']}-{K['lkw']})*(C10-C13)" if _split_lock
                else f"={K['cw']}*(C10-C11)") if _need15 else B_)),
             ("매도청구권 · 옵션차익 · 혼합할인율",
              ((f"={K['lkw']}*{Q(S20)}!C{R0+n+3}+({K['cw']}-{K['lkw']})*{Q(S20N)}!C{R0+n+3}" if _split_lock
                else f"={K['cw']}*{Q(S20)}!C{R0+n+3}") if _need1 else B_)),
             ("매도청구권 · 옵션차익 · 지분·부채 분리",
              ((f"={K['lkw']}*{Q(S24)}!C{R0+n+3}+({K['cw']}-{K['lkw']})*{Q(S24N)}!C{R0+n+3}" if _split_lock
                else f"={K['cw']}*{Q(S24)}!C{R0+n+3}") if _need2 else B_)),
             ("매도청구권자산 (적용값)", f"=C{19+_km}" if _hascall else "=0"),
             # 제3자 기특정 콜은 발행자가 자산으로 인식하지 않으므로 배분에서 빠진다
             # (본문 4.5.1 주주간 분배). 잔여인 전환권대가·주계약이 그만큼 작아진다.
             ("전환권대가 — 자본 배분액 (자본일 때)",
              f'=IF({K["eqcls"]}=1,100-C17+IF({K["kkind"]}=1,0,{CAE}),"")'),
             # 조기상환권을 주계약에 남기면(분리 정책 접근법 2) 파생은 «사채 + 조기상환권»
             # 위에 얹힌 전환권 몫이다 — C10 − C17. 아니면 옵션 없는 사채 위의 묶음 — C10 − C16.
             (("전환권 파생상품 (부채일 때 · 조기상환권은 주계약)" if put_in_host(tm)
               else "복합내재파생상품 (부채일 때)"),
              f'=IF({K["eqcls"]}=0,C10-IF({K["phost"]}=1,C17,C16),"")'),
             (("주계약 잔여 (부채일 때 · 조기상환권 포함)" if put_in_host(tm)
               else "주계약 잔여 (부채일 때)"),
              f'=IF({K["eqcls"]}=0,100+IF({K["kkind"]}=1,0,C22)-C24,"")')]
    if issuer_redeem(tm):
        # 부채 격자에서 잰 발행자 상환권. 자본 배분(전환권대가·회계처리)이 이 값을 쓴다.
        items.append(("매도청구권 · 부채 격자 기준 (자본 배분용)",
                      f"=MAX(0,{Q(S16)}!C13-'{S16C}'!C15)" if _rcps_call else "=0"))
    # 발행자 최초 인식 대사 — 발행일 평가에서만 싣는다 (issuer_day1). 회계처리 시트의 배분·
    # 대사표가 이 두 칸을 본다.
    _d1x = issuer_day1(tm, b0, b1, b2, ca)
    _R_NET = _R_D1 = None
    if _d1x:
        _R_NET = 16 + len(items)
        _R_D1 = _R_NET + 1
        items += [("콜 차감 후 순평가금액 (발행자가 인식하는 금융상품)",
                   f'=C10-IF({K["kkind"]}=1,0,C22)'),
                  ("최초 인식 차이 (순평가금액 − 거래가격 100)", f"=C{_R_NET}-100")]
    for i, (nm, fx) in enumerate(items):
        r = 16+i
        put(R, r, 2, nm, bold=True, border=True)
        put(R, r, 3, fx, bold=True, fmt=N2, align="right", border=True)
        put(R, r, 4, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
    # 숫자가 맞는지 보는 확인(배분 합계·상각표 조건 등)은 앱이 평가할 때 수행하고 조서에는
    # 싣지 않는다 — 이상이 있으면 조서를 만들지 않는다(service).
    put(R, 31, 2, "이 조서에는 앱에서 고른 방법만 들어 있습니다. 다른 신용위험 처리나 "
        "다른 매도청구권 평가방법의 값은 이 조서에 없습니다.", color=GREY, size=9)
    put(R, 32, 2, ("선도이자율은 IR 시트에서, 유효이자율은 상각표·분리 판단 시트에서 수식으로 계산된다. "
                   + ("값으로 들어간 산출값은 BDT 기준금리(앱이 역산한 값)뿐이다." if _bdt else
                      "값으로 들어간 산출값은 없다.")
                   if _irref else
                   "주황색 숫자만 값이다. 선도이자율은 부트스트래핑 결과를 값으로 넣었다."),
        color=AMB, size=9)
    _rk2 = write_pc_rows(R, 38, tm, put, sec, N4, GREY)
    write_call_rows(R, _rk2 + 1, tm, full, b2, put, sec, N4, P2, GREY)

    # ── 이자율곡선 ──
    # IR 시트를 함께 실었으면 선도이자율이 그 시트에서 수식으로 오므로 이 표는 싣지 않는다.
    if not _irref:
        _cc = credit_curve(tm)      # 실제 적용한 위험 곡선 (두 등급 보간·외삽 반영)
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
        if tm.y_type == "par" and len(_cc) >= 2:
            sec(C, rr, "부트스트래핑 — 위험 곡선", span=6)
            for i, h in enumerate(["만기 (년)", "만기수익률", "할인계수", "현물 (연속)"]):
                put(C, rr+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
            bs = [x for x in bootstrap_df(_cc, tm.T, tm.cmp_cr) if x[0] > 0]
            for k, (t_, df) in enumerate(bs):
                for j2, v in enumerate([t_, _lin(_cc, t_), df, -math.log(df)/t_]):
                    put(C, rr+2+k, 2+j2, v,
                        fmt=(N2 if j2 == 0 else (N6 if j2 == 2 else P2)),
                        align="right", border=True)
            rr = rr+3+len(bs)
        put(C, rr, 2, "주황색은 값이다. 곡선을 바꾸려면 앱에서 조서를 다시 만들어야 한다.",
            color=AMB, size=9)

    # ── 상각표 ──
    # 유효이자율도 표 아래 «계산 과정»(xl_eir_solver)의 수식이다. 회차 수와 경과연수만 앱이 정한 값이다.
    # 전체를 당기손익-공정가치로 지정했으면 상각할 주계약이 없다. 빈 표 대신
    # **왜 없는지**를 남긴다 — 값 조서와 같은 문안(FVPL_NOTE)을 쓴다.
    M = wb.create_sheet("상각표"); M.sheet_view.showGridLines = False
    for cc, w in (("B", 10), ("C", 13), ("D", 12), ("E", 16), ("F", 14),
                  ("G", 14), ("H", 16)):
        M.column_dimensions[cc].width = w
    if eir is None:
        title(M, 2, "주계약 상각표 — 만들지 않는다", span=7)
        M.column_dimensions["B"].width = 110
        for _i, _tx in enumerate(FVPL_NOTE if fvpl_on(tm) else
                                 FV_ONLY_XL if acc_mode(tm) == "fv_only" else HOST_NONPOS_XL):
            put(M, 4+_i, 2, _tx, color=(RED if _i == 0 else GREY),
                bold=(_i == 0), size=(10 if _i == 0 else 9))
            M.cell(row=4+_i, column=2).alignment = Alignment(wrap_text=True,
                                                             vertical="top")
    else:
        r_eir, rows_eir, redm, nper = eir
        _exf = eir_expect(tm)
        title(M, 2, "주계약 상각표", span=7)
        put(M, 3, 2, "주계약(옵션 없는 사채)을 유효이자율법으로 상각한다. "
            "기말 잔액이 만기상환금액과 맞아떨어져야 한다. "
            + ("※ 조기상환권을 분리하지 않으므로 기대만기 = 첫 조기상환 가능일, 만기 현금흐름 = 그 시점 "
               "행사금액이다 (B4.3.5(5)(가)). " + EXPECT_B546 + " " if _exf is not None else "")
            + "지급일은 계약상 일정이므로 발행일부터 센다. 평가기준일이 발행일보다 뒤이면 "
            "첫 회차만 짧고 나머지는 온전한 한 주기다. 회차 수는 노드가 아니라 "
            "이자 지급주기를 따른다.", color=GREY, size=9)
        sec(M, 5, "유효이자율 역산", span=6)
        for i, (k, fx, fm, val) in enumerate([
                # 실제로 인식한 금액 — 회계처리 배분표의 주계약(7행)·부채요소(8행) 가운데 찬 쪽이다.
                # 부채로 분류하면 잔여(최초 인식 차이를 이연하면 공정가치에서 뺀 금액)이고,
                # 이론값(결과 C16)이 아니다. 배분표를 보므로 분류·분리 정책·차이 처리가 함께 따라온다.
                ("주계약 (인식액, 거래원가 차감 후)",
                 '=SUM(회계처리!C7:C8)-회계처리!D32-회계처리!D33', N2, None),
                # 조기상환권 비분리면 기대만기(첫 조기상환 가능일)의 행사금액이 만기 현금흐름이다.
                # 그 개월(psm 또는 그 뒤 첫 주기)이 가정의 psm 과 같으면 수식, 아니면 값.
                ((("기대만기 상환금액 (첫 조기상환 가능일 행사금액)",
                   (("=" + x_pamt_month(K['psm']))
                    if abs(_exf[2] - tm.p_s) < 1e-9 else None), N2, _exf[1])
                  if _exf is not None else ("만기상환금액", f"={K['red']}", N2, None))),
                ("표면이자 (회당)", f"=100*{K['cpn']}*{K['ipaym']}/12", N2, None),
                ("상각 횟수", None, N0, nper)]):
            put(M, 6+i, 2, k, border=True)
            put(M, 6+i, 3, fx if fx else val, fmt=fm, align="right", border=True)
        put(M, 10, 2, "유효이자율 (연, 이산복리)", bold=True, fill=BAND, border=True)
        # 값은 표 아래 «계산 과정» 에서 수식으로 구한다 (xl_eir_solver) — 아래에서 채운다.
        put(M, 11, 2, "유효이자율도 수식이다 — 표 아래 계산 과정(앱과 같은 이분법)에서 구한다. "
            "가정 시트의 입력을 바꾸면 이자율과 상각표가 함께 다시 계산된다.", color=GREY, size=9)
        sec(M, 13, "상각 내역", span=7)
        for i, h in enumerate(["회차", "지급일", "경과연수", "기초", "이자비용",
                               "지급이자", "기말"]):
            put(M, 14, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, row in enumerate(rows_eir):
            r = 15+i; last = (i == len(rows_eir)-1); fl = BAND if last else None
            prev = r-1
            put(M, r, 2, row[0], fmt=N0, align="right", border=True, bold=last, fill=fl)
            # 계약상 지급일이다. 마지막은 만기일, 나머지는 발행일에 달을 더한다.
            pdf = ((f"={K['d_mat']}" if _exf is None else f"=EDATE({K['d_issue']},{int(round(_exf[2]))})") if last else
                   f"=EDATE({K['d_issue']},{int(round(pay_index(tm, row[1])*tm.ipay))})")
            put(M, r, 3, pdf, fmt=DATE, align="right", border=True, bold=last, fill=fl)
            put(M, r, 4, row[1], fmt=N2, align="right", border=True, bold=last, fill=fl)
            put(M, r, 5, ("=$C$6" if i == 0 else f"=H{prev}"), fmt=N2, align="right",
                border=True, bold=last, fill=fl)
            # 이자비용 = 기초 × (1+r)^기간 − 기초.  회차마다 기간이 달라 이렇게 쓴다.
            gap = f"(D{r}" + ("" if i == 0 else f"-D{prev}") + ")"
            put(M, r, 6, f"=E{r}*((1+$C$10)^{gap}-1)", fmt=N2, align="right",
                border=True, bold=last, fill=fl)
            # 기대만기(조기상환일)에 끝나면 마지막 회차 이자는 «행사일 이자 별도지급» 칸을 따른다 (eir_table 과 같다).
            put(M, r, 7, (f"=IF({K['pcadd']}=1,$C$8,0)" if (last and _exf is not None) else "=$C$8"),
                fmt=N2, align="right", border=True, bold=last, fill=fl)
            put(M, r, 8, f"=E{r}+F{r}-G{r}", fmt=N2, align="right",
                border=True, bold=last, fill=fl)
        # 기말 잔액이 만기상환금액과 맞는지는 앱이 평가할 때 확인한다 — 조서에 싣지 않는다.
        # 유효이자율 — 현재가치 = 이표(G열)를 각 경과연수(D열)로 할인 + 상환금액(C7)을 마지막 연수로 할인.
        _r1, _rN = 15, 15 + len(rows_eir) - 1
        _pv = (lambda x: f"(SUMPRODUCT($G${_r1}:$G${_rN},(1+{x})^(-$D${_r1}:$D${_rN}))"
                         f"+$C$7*(1+{x})^(-$D${_rN}))")
        _eir_at, _ = xl_eir_solver(put, M, _rN + 3, "$C$6", _pv, (N2, N6), GREY)
        put(M, 10, 3, f"={_eir_at}", bold=True, fill=BAND, fmt=P2, align="right", border=True)

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
                  "4.3.3(3)). 요소별 배분도 유효이자율 상각도 없다. " + call_alloc_note(tm, whole=True)
                  if _FVROW else
                  "기업회계기준서 제1032호 문단 31·32 — 부채요소를 먼저 정하고 나머지를 자본에 배분한다. "
                  + call_alloc_note(tm) +
                  "전환권이 부채이면 전환권과 조기상환권은 상호의존적이므로 하나의 복합내재파생상품으로 "
                  "전체로서 측정한다 (제1109호 문단 B4.3.4)."),
        color=GREY, size=9)
    if tm.elapsed_m > 0.01:
        put(E, 4, 2, "※ 평가기준일이 발행일보다 뒤입니다. 아래 배분은 최초 인식용이므로 "
            "결산 회계처리에 그대로 쓰지 마십시오. 결산일에 쓰는 것은 파생상품 공정가치뿐이고, "
            "주계약은 발행일 배분액을 유효이자율로 상각한 장부금액입니다.", color=RED, size=9)
    sec(E, 5, "1. 최초 인식 배분", span=5)
    for i, h in enumerate(["항목", "최초 장부금액 (100 기준)", "전액 기준 (원)"]):
        put(E, 6, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    # 매도청구권을 별도 금융상품으로 볼지 내재파생에 넣을지에 따라 표가 갈린다.
    # 넣는 쪽이면 파생 줄에서 콜을 빼고 자산 줄을 비운다. 합계는 어느 쪽이든 100.
    KS = K["ksep"]
    # 조기상환권을 주계약(부채요소)에 남기는 갈래 — 가정의 「조기상환권을 주계약에 포함」
    # 칸(분리 정책·전환권 분류·콜 처리로 정해지는 계산값)이 정한다. 전환권이 자본이면
    # 부채요소 줄이 찬다. 콜을 내재파생으로 둔 채 남기면(접근법 2) 콜은 따로 분리된다.
    NS = f'AND({K["eqcls"]}=1,{K["phost"]}=1)'
    # 최초 인식 차이를 당기손익으로 고른 복합계약 — 주계약이 공정가치 그대로다 (2019-I-KQA018).
    _D1 = (f'결과!C{_R_D1}' if _R_D1 else "0")
    _HFV = f'IF({K["phost"]}=1,결과!C17,결과!C16)'
    _HOST = f'=IF({K["eqcls"]}=1,IF({NS},"",결과!C16),IF({K["d1pl"]}=1,{_HFV},결과!C25))'
    _PUT = f'=IF(OR({K["eqcls"]}=0,{KS}=0,{NS}),"",결과!C18)'
    _LIAB = f'=IF({NS},결과!C17,"")'
    # 전환권이 부채면 전환권(+조기상환권) 묶음, 자본이면서 콜을 내재파생으로
    # 넣었으면 조기상환권+매도청구권 묶음이다. 어느 쪽이든 순액 한 줄이다.
    _CMP = (f'=IF({K["eqcls"]}=0,IF({KS}=1,결과!C24,결과!C24-결과!C22),'
            f'IF(AND({KS}=0,NOT({NS})),결과!C18-결과!{CAE},""))')
    # 기특정 콜이면 발행자가 자산을 인식하지 않는다 (본문 4.5.1) — 줄이 비어 있다.
    _CALL = (f'=IF({K["kkind"]}=1,"",IF(OR({KS}=1,{NS}),'
             f'IF({K["eqcls"]}=1,-결과!{CAE},-결과!C22),""))')
    # 마지막 줄은 전환권이 자본이면 전환권대가, 부채면 최초 인식 손익(당기손익을 고른 때만)이다.
    # 두 경우가 함께 설 수 없으므로 한 줄을 나눠 쓴다 — 행 번호가 밀리지 않는다.
    _EQ = f'=IF({K["eqcls"]}=1,결과!C23,IF({K["d1pl"]}=1,-{_D1},""))'
    # 수식 안의 글자는 relabel_inst 가 건드리지 않으므로 상품 용어를 여기서 넣는다
    # (신주인수권부사채면 「신주인수권대가」).
    _EQW = relabel_text("전환권대가", inst_words(tm))
    _EQNM = (f'=IF({K["eqcls"]}=1,"{_EQW} · 자본",IF({K["d1pl"]}=1,'
             f'IF({_D1}>0,"{DAY1_LOSS}","{DAY1_GAIN}"),"최초 인식 손익 · 당기손익 (고르지 않음)"))')
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
        # 전체의 공정가치 — 별도 금융상품인 콜은 전체 밖이므로 순평가금액에 다시 더한다.
        _WFV = (f'결과!C{_R_NET}+IF(AND({KS}=1,{K["kkind"]}<>1),결과!C22,0)' if _R_NET else "0")
        _HOST = (f'=IF({K["d1pl"]}=1,{_WFV},IF(AND({KS}=1,{K["kkind"]}<>1),100+결과!C22,100))')
        _LIAB = _PUT = _CMP = ""
        _EQ = f'=IF({K["d1pl"]}=1,-{_D1},"")'
    _ph_liab = put_in_host(tm) and tm.conv_class == "liability"
    _HOSTNM = ("복합계약 전체 · 당기손익-공정가치 측정 금융부채" if _FVROW else
               "주계약 (사채 + 조기상환권)" if _ph_liab else "주계약")
    _CMPNM = (("전환권 · 파생상품부채" if tm.k_sep != 0 else
               "복합내재파생상품 (전환권 + 매도청구권) · 파생상품부채")
              if _ph_liab else "복합내재파생상품 · 파생상품부채")
    _CALLNM = ("매도청구권 (내재파생 · 분리) · 파생상품자산"
               if (tm.k_sep == 0 and put_in_host(tm) and tm.conv_class == "equity")
               else "매도청구권 · 파생상품자산")
    al2 = [(_HOSTNM, _HOST),
           ("부채요소 (사채 + 조기상환권)", _LIAB),
           ("조기상환청구권 · 파생상품부채", _PUT),
           (_CMPNM, _CMP),
           (_CALLNM, _CALL),
           (_EQNM, _EQ)]
    for i, (nm, fx) in enumerate(al2):
        r = 7+i
        put(E, r, 2, nm, border=True)
        put(E, r, 3, fx, fmt=N2, align="right", border=True)
        put(E, r, 4, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
    put(E, 13, 2, "합계", bold=True, fill=BAND, border=True)
    put(E, 13, 3, "=SUM(C7:C12)", bold=True, fill=BAND, fmt=N2, align="right", border=True)
    put(E, 13, 4, "=SUM(D7:D12)", bold=True, fill=BAND, fmt=N0, align="right", border=True)
    if int(tm.k_kind) == 1 and tm.k_w > 0:
        # 합계에 넣으면 100 이 되지 않고 차변에 넣으면 대차가 깨진다 — 합계 밖 참고 줄이다.
        put(E, 15, 2, "제3자 기특정 콜옵션 · 주주간 분배 (참고 · 발행자 자산 아님)",
            color=GREY, size=9, border=True)
        put(E, 15, 3, f"=결과!{CAE}", fmt=N2, align="right", color=GREY, border=True)
        put(E, 15, 4, f'=C15/100*{K["face"]}', fmt=N0, align="right", color=GREY, border=True)
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
    # 마지막 줄 — 전환권대가(대변) 또는 최초 인식 손실(차변)·이익(대변).
    _JDR = f'=IF(AND({K["eqcls"]}=0,{K["d1pl"]}=1,{_D1}>0),{_D1},"")'
    _JCR = f'=IF({K["eqcls"]}=1,결과!C23,IF(AND({K["d1pl"]}=1,{_D1}<0),-{_D1},""))'
    if _FVROW:
        _JCR = f'=IF(AND({K["d1pl"]}=1,{_D1}<0),-{_D1},"")'
    _JNM = (f'=IF({K["eqcls"]}=1,"　{_EQW} (자본)",IF({K["d1pl"]}=1,'
            f'IF({_D1}>0,"최초 인식 손실 (당기손익)","　최초 인식 이익 (당기손익)"),'
            f'"　최초 인식 손익 (고르지 않음)"))')
    # 매도청구권이 음수(콜이 전환을 강제해 값이 오르는 모형 성질)면 자산이 아니라 부채다 — 배분표와
    # 값 조서(alloc_journal)처럼 대변으로 간다. 종전에는 음수를 차변 자산에 그대로 실어 부호가 섞였다.
    _CX = f'IF({K["kkind"]}=1,"",IF(OR({KS}=1,{NS}),IF({K["eqcls"]}=1,결과!{CAE},결과!C22),""))'
    je2 = [("현금", "=100", None),
           (f'=IF(AND(ISNUMBER({_CX}),N({_CX})<0),"　파생상품부채 (매도청구권)","파생상품자산 (매도청구권)")',
            f'=IF(AND(ISNUMBER({_CX}),N({_CX})>0),{_CX},"")',
            f'=IF(AND(ISNUMBER({_CX}),N({_CX})<0),-{_CX},"")'),
           (("　당기손익-공정가치 측정 금융부채 (복합계약 전체)" if _FVROW
             else "　전환사채 (주계약 · 조기상환권 포함)" if _ph_liab else "　전환사채 (주계약)"),
            None, _HOST),
           ("　전환사채 (부채요소)", None, _LIAB),
           ("　파생상품부채 (조기상환청구권)", None, _PUT),
           (("　파생상품부채 (전환권)" if (_ph_liab and tm.k_sep != 0) else
             "　파생상품부채 (복합내재파생상품)"), None, _CMP),
           (_JNM, _JDR, _JCR)]
    for i, (nm, dr, cr) in enumerate(je2):
        r = 18+i
        put(E, r, 2, nm, size=9, border=True)
        put(E, r, 3, dr if dr else "", fmt=N2, align="right", border=True)
        put(E, r, 4, cr if cr else "", fmt=N2, align="right", border=True)
        put(E, r, 5, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
        put(E, r, 6, f'=IF(ISNUMBER(D{r}),D{r}/100*{K["face"]},"")',
            fmt=N0, align="right", border=True)
    if int(tm.k_kind) == 1 and tm.k_w > 0:
        # 27행에는 아래 일반 문구가 선다 — 같은 칸에 쓰면 덮어써 사라진다. 한 줄 아래에 둔다.
        put(E, 28, 2, f'="{NOTE_KKIND.format(v="")[:-1]}"&" (금액 "&TEXT(결과!{CAE},"#,##0.0000")&")"',
            color=GREY, size=9)
    put(E, 25, 2, "합계", bold=True, fill=BAND, border=True)
    for j2, col in enumerate("CDEF"):
        put(E, 25, 3+j2, f"=SUM({col}18:{col}24)", bold=True, fill=BAND,
            fmt=(N2 if j2 < 2 else N0), align="right", border=True)
    put(E, 27, 2, ("부채·자본 구성요소를 나누는 배분 자체로는 손익이 생기지 않는다. 거래가격이 "
                   "공정가치와 다를 때의 차이(최초 인식 차이)는 아래 5번 대사표에 따로 적는다 — 이연하면 "
                   "주계약 장부금액에서 빠지고, 당기손익을 고르면 배분표 마지막 줄에 선다."
                   if _d1x else
                   "부채·자본 구성요소를 나누는 배분 자체로는 손익이 생기지 않는다. 거래원가와, 거래가격이 "
                   "공정가치와 다를 때의 차이(최초 인식 손익)는 별도 검토 대상이다."), color=GREY, size=9)
    # ── 거래원가 배분 (1032 문단 38) ── 가정의 거래원가 셀을 바꾸면 따라온다
    sec(E, 30, "3. 거래원가 배분 (1032 문단 38)", span=6)
    for i, h in enumerate(["요소", "배분액 (100)", "거래원가 배분액 (100)", "거래원가 배분액 (원)", "처리"]):
        put(E, 31, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
    _C100 = f'{K["cost"]}/{K["face"]}*100'
    # 분모는 복합금융상품에 배분된 양수 줄의 합이다 (매도청구권 자산은 별도 금융상품).
    # 마지막 줄은 전환권이 자본일 때만 배분액이다 — 부채면 최초 인식 손익이라 뺀다.
    _EQOK = f'{K["eqcls"]}=1'
    _BASE = f'(SUMIF(C7:C11,">0")+IF(AND({_EQOK},ISNUMBER(C12)),MAX(0,C12),0))'
    _CROWS = [((_HOSTNM, "C7", "즉시 비용 (당기손익-공정가치)") if _FVROW
               else ((_HOSTNM, "C7", "부채에서 차감 — 유효이자율에 반영"))),
              ("부채요소 (사채 + 조기상환권)", "C8", "부채에서 차감 — 유효이자율에 반영"),
              ("조기상환청구권 · 파생상품부채", "C9", "즉시 비용 (당기손익-공정가치)"),
              (_CMPNM, "C10", "즉시 비용 (당기손익-공정가치)"),
              ("전환권대가 · 자본", "C12", "자본에서 차감")]
    for i, (nm, cell, how) in enumerate(_CROWS):
        r = 32+i
        _ok = (f'AND({_EQOK},ISNUMBER({cell}),{cell}>0)' if cell == "C12"
               else f'AND(ISNUMBER({cell}),{cell}>0)')
        put(E, r, 2, nm, border=True, size=9)
        put(E, r, 3, (f'=IF(AND({_EQOK},ISNUMBER({cell})),{cell},"")' if cell == "C12"
                      else f'=IF(ISNUMBER({cell}),{cell},"")'), fmt=N2, align="right", border=True)
        put(E, r, 4, f'=IF({_ok},{_C100}*{cell}/{_BASE},0)',
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
                   "(문단 4.3.1) 분모에서 뺐다. 상각표는 주계약에서 이 배분액을 뺀 금액에서 "
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

    # ── 최초 인식 대사 — 발행자 (회계기준원 2019-I-KQA018) ──
    # 값 조서의 같은 절과 같은 줄이다 (issuer_day1_rows). 값은 결과 시트에서 수식으로 온다.
    if _d1x:
        RD = RM + 10
        sec(E, RD, "5. 최초 인식 대사 — 발행자 (회계기준원 2019-I-KQA018)", span=5)
        for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
            put(E, RD+1, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        _nm0 = [k for k, _ in issuer_day1_rows(_d1x)]
        _fx0 = [f"=결과!C{_R_NET}", "=100", f"=결과!C{_R_D1}"]
        if _d1x["hybrid"]:
            _fx0 += [(f"={_WFV}" if _FVROW else f"={_HFV}"),
                     f'=IF({K["d1pl"]}=1,0,-결과!C{_R_D1})',
                     f"=C{RD+5}+C{RD+6}"]
        for i, (k, fx) in enumerate(zip(_nm0, _fx0)):
            r = RD+2+i
            _b = k.startswith("최초 인식 차이 (")
            put(E, r, 2, k, border=True, bold=_b, fill=(BAND if _b else None))
            put(E, r, 3, fx, fmt=N4, align="right", border=True, bold=_b, fill=(BAND if _b else None))
            put(E, r, 4, f'=IF(ISNUMBER(C{r}),C{r}/100*{K["face"]},"")', fmt=N0, align="right",
                border=True, bold=_b, fill=(BAND if _b else None))
        put(E, RD+2+len(_nm0), 2,
            (ISSUER_DAY1 + (" 이 조서는 «당기손익» 을 골랐다 — 주계약이 공정가치 그대로이고 차이가 배분표 "
                            "마지막 줄에 선다." if _d1x["pl"] else
                            " 이 조서는 «이연» 이다 — 주계약 장부금액이 공정가치에서 차이를 뺀 금액이고, "
                            "유효이자율로 기간에 걸쳐 인식된다.")
             if _d1x["hybrid"] else
             "전환권이 자본이므로 차이는 잔여인 자본요소(전환권대가)에 흡수된다 (1032 문단 31). "
             "최초 인식 손익은 생기지 않는다."), color=GREY, size=9)
        _rc = RD+2+len(_nm0)+2
        for i, h in enumerate(["최초 인식 차이의 세 가지 구분", "처리", "이 평가"]):
            put(E, _rc, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, (k, v, on) in enumerate(issuer_day1_cases(_d1x)):
            put(E, _rc+1+i, 2, k, border=True, bold=bool(on), size=9)
            put(E, _rc+1+i, 3, v, border=True, size=9)
            put(E, _rc+1+i, 4, on, border=True, bold=True, size=9)

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
    _r = write_exdate_rows(J, _r, tm, put, sec, GREY, LIGHT)
    for _k, _nm in (("put", "조기상환청구권"), ("call", "매도청구권")):
        _d = SP[_k]
        sec(J, _r, _nm, span=2); _r += 1
        put(J, _r, 2, "결론", bold=True, border=True)
        put(J, _r, 3, _d["결론"], bold=True, border=True); _r += 1
        # 적용 회계정책 · 선택한 처리 — 판정(결론)과 이 조서가 실제로 한 처리를 나눠 적는다.
        _r38 = _r - 1
        _rcmp = None
        if _d.get("있음", True):
            for _a, _v in split_policy_rows(tm, _k):
                put(J, _r, 2, _a, border=True); put(J, _r, 3, _v, border=True); _r += 1
            _cmpx = split_compare(tm, _k, _d)
            put(J, _r, 2, "판정과 설정 비교", bold=True, border=True)
            put(J, _r, 3, _cmpx, bold=True, border=True,
                color=(RED if "검토 필요" in _cmpx else "000000"))
            _rcmp = _r; _r += 1
        for _i, _x in enumerate(_d["이유"]):
            put(J, _r, 2, "판단 근거" if _i == 0 else "", border=True)
            put(J, _r, 3, _x, border=True); _r += 1
        put(J, _r, 2, "기준서", border=True)
        put(J, _r, 3, " · ".join(_d["근거"]) or "—", border=True); _r += 1
        put(J, _r, 2, "평가방법", border=True)
        put(J, _r, 3, _d["평가"].replace("**", ""), border=True); _r += 1
        if _d["지표"]:
            put(J, _r, 2, "분리 검토용 수치", bold=True, border=True)
            put(J, _r, 3, SPLIT_NUM_NOTE + " 이 칸들은 앱에서 생성 당시 계산한 참고값이다 — 가정 시트를 바꾸면 아래 «행사금액과 상각후원가 비교 — 수식» 표가 따라온다.", color=GREY, size=9, border=True); _r += 1
        for _a, _v in _d["지표"].items():
            put(J, _r, 2, _a, border=True)
            put(J, _r, 3, (f"{_v*100:.1f}%" if _a in ("차이", "가장 큰 차이") else
                           f"{_v*100:g}%" if _a.startswith("비교기준") else
                           _v if isinstance(_v, str) else
                           ("예" if _v is True else "아니오" if _v is False
                            else f"{_v:,.4f}")), border=True); _r += 1
        if _k == "put" and _d["지표"] and tm.p_s <= tm.p_e:
            # ── B4.3.5(5)(가) 10% 검토를 수식으로 — 위 문자열과 같은 값이어야 한다 (검산수식대조) ──
            # 상각후원가는 split_test 와 같이 **자본요소를 분리하기 전** 금액(전환사채에 배분된
            # 거래가격)을 계약만기까지 굴린 표에서 «t ≥ 행사 시점» 인 첫 회차의 기말이다.
            # 유효이자율도 수식(xl_eir_solver — 엔진과 같은 이분법)이다. 행사일마다 견주고 가장 큰 차이로 판정한다.
            _e0r, _e0rows, _, _e0n = eir_table(tm, split_base(tm, ca))
            _b0x = (repr(float(tm.split_base_in)) if float(getattr(tm, "split_base_in", -1.0)) > 0
                    else f"100+결과!{CAE}" if split_call_separate(tm) else "100")
            _pms = sorted(exercise_amounts(tm, n, dt_)["p_dates"].values()) or [tm.p_s]
            _r += 1
            sec(J, _r, "B4.3.5(5)(가) 행사금액과 상각후원가 비교 — 수식 (가정 시트의 비교기준을 바꾸면 판정이 따라온다)", span=6); _r += 1
            _rs = _r + 8                               # 미니 상각표 첫 자료행
            _re = _rs + len(_e0rows) - 1
            _rpv, _rbv, _rt, _rg, _rv, _rr = _r, _r+1, _r+2, _r+3, _r+4, _r+6
            put(J, _rpv, 2, "첫 조기상환일 행사금액", border=True)
            put(J, _rpv, 3, "=" + x_pamt_month(K['psm']),
                fmt=N4, align="right", border=True)
            put(J, _rbv, 2, ("같은 시점 상각후원가 (전환권 분리 전 · 준용)" if tm.conv_class != "equity"
                             else "같은 시점 상각후원가 (자본요소 분리 전)"), border=True)
            _idx = f'(COUNTIF($C${_rs}:$C${_re},"<"&(C{_rt}-0.000000001))+1)'
            put(J, _rbv, 3, f"=IF({_idx}>{_e0n},{_b0x},INDEX($G${_rs}:$G${_re},{_idx}))",
                fmt=N4, align="right", border=True)
            put(J, _rt, 2, "첫 조기상환 시점 (평가기준일부터, 년)", border=True)
            put(J, _rt, 3, f"=MAX(0,({K['psm']}-{K['elm']})/12)", fmt=N4, align="right", border=True)
            _ws = _re + 3                              # 행사일별 비교표 첫 자료행
            _we = _ws + len(_pms) - 1
            put(J, _rg, 2, "차이 (모든 행사일 중 가장 큰 값)", border=True)
            put(J, _rg, 3, f"=MAX($F${_ws}:$F${_we})", fmt=P2, align="right", border=True)
            put(J, _rg, 4, f"=ABS(C{_rpv}-C{_rbv})/MAX(ABS(C{_rbv}),0.000000001)", fmt=P2, align="right", border=True)
            put(J, _rg, 5, "← 첫 조기상환일", color=GREY, size=9)
            put(J, _rv, 2, "판정 (수식)", bold=True, border=True)
            # 전체 지정은 구조를 정하는 선택이라 앱에서 고른 값을 박는다(가정의 fvpl 칸은 글자다).
            # 접근법 1 이면 전환권이 부채이거나 발행회사 콜이 내재파생일 때 묶어서 분리한다.
            put(J, _rv, 3, (f'=IF({1 if tm.fvpl_whole else 0}=1,"분리하지 않음",'
                            f'IF(AND({K["embap"]}=1,OR({K["eqcls"]}=0,AND({K["ksep"]}=0,{K["cw"]}>0))),"묶어서 분리",'
                            f'IF({K["plost"]}=1,"분리하지 않음",IF(C{_rg}<={K["stol"]},"분리하지 않을 여지","분리"))))'),
                bold=True, border=True)
            # 위의 «결론» 과 «판정과 설정 비교» 를 이 판정 수식에 잇는다 — 가정 시트의 비교기준이나
            # 조기상환권 처리를 바꾸면 결론·일치 여부가 함께 바뀐다 (값 조서와 같은 글자).
            if not fvpl_on(tm):
                put(J, _r38, 3, f"=C{_rv}", bold=True, border=True)
                if _rcmp:
                    # 이용자 설정 글자도 가정 시트의 분류·매도청구권 처리 칸을 보고 정한다 (split_policy_rows 와 같은 순서).
                    _w = inst_words(tm)
                    _sep = (f'IF({K["eqcls"]}=0,"{relabel_text("전환권과 묶어 분리 — 복합내재파생상품 (파생상품부채)", _w)}",'
                            f'IF(AND({K["ksep"]}=0,{K["cw"]}>0),"매도청구권과 묶어 분리 — 복합내재파생상품",'
                            f'"분리 — 파생상품부채"))')
                    _h = K["phost"]
                    _dif = "검토 필요 — 수치 판정과 이용자 설정이 다름"
                    put(J, _rcmp, 3,
                        (f'="수치 판정 «"&C{_rv}&"» · 이용자 설정 «"&IF({_h}=1,"주계약에 포함 (분리하지 않음) — 상각후원가로 측정",{_sep})&"» → "&'
                         f'IF(OR(C{_rv}="분리",C{_rv}="묶어서 분리"),IF({_h}=1,"{_dif}","일치"),'
                         f'IF(C{_rv}="분리하지 않음",IF({_h}=1,"일치","{_dif}"),'
                         f'IF({_h}=1,"일치","검토 필요 — 수치상 분리하지 않을 여지가 있으나 분리를 선택함")))'),
                        bold=True, border=True)
            put(J, _rr, 2, "유효이자율 (자본요소 분리 전 · 계약만기)", border=True)
            put(J, _rr+1, 2, "자본요소 분리 전 상각표 (분리 판단용)", bold=True)
            for _j, _h in enumerate(["회차", "연수", "기초", "이자", "지급", "기말"]):
                put(J, _rr+1, 2+_j, _h if _j else "분리 판단용 상각표 · 회차", bold=True,
                    fill=LIGHT, align="center", border=True, size=9)
            for _j, _row in enumerate(_e0rows):
                _rw = _rs + _j
                put(J, _rw, 2, _row[0], fmt=N0, align="right", border=True)
                put(J, _rw, 3, _row[1], fmt=N4, align="right", border=True)
                put(J, _rw, 4, (f"={_b0x}" if _j == 0 else f"=G{_rw-1}"), fmt=N4, align="right", border=True)
                _gap = f"(C{_rw}" + ("" if _j == 0 else f"-C{_rw-1}") + ")"
                put(J, _rw, 5, f"=D{_rw}*((1+$C${_rr})^{_gap}-1)", fmt=N4, align="right", border=True)
                put(J, _rw, 6, f"=100*{K['cpn']}*{K['ipaym']}/12", fmt=N4, align="right", border=True)
                put(J, _rw, 7, f"=D{_rw}+E{_rw}-F{_rw}", fmt=N4, align="right", border=True)
            for _j, _h in enumerate(SPLIT_DATE_COLS):
                put(J, _ws-1, 2+_j, _h, bold=True, fill=LIGHT, align="center", border=True, size=9)
            for _j, _m in enumerate(_pms):
                _rw = _ws + _j
                put(J, _rw, 2, round(_m, 6), fmt=N2, align="right", border=True, color=RED)
                put(J, _rw, 3, f"=MAX(0,(B{_rw}-{K['elm']})/12)", fmt=N4, align="right", border=True)
                put(J, _rw, 4, "=" + x_pamt_month(f"B{_rw}"), fmt=N4, align="right", border=True)
                _ix = f'(COUNTIF($C${_rs}:$C${_re},"<"&(C{_rw}-0.000000001))+1)'
                put(J, _rw, 5, f"=IF({_ix}>{_e0n},{_b0x},INDEX($G${_rs}:$G${_re},{_ix}))",
                    fmt=N4, align="right", border=True)
                put(J, _rw, 6, f"=ABS(D{_rw}-E{_rw})/MAX(ABS(E{_rw}),0.000000001)", fmt=P2,
                    align="right", border=True)
            # 유효이자율 — 이 상각표의 지급(F열)을 연수(C열)로 할인 + 계약만기 상환금액을 마지막 연수로 할인.
            _pv = (lambda x: f"(SUMPRODUCT($F${_rs}:$F${_re},(1+{x})^(-$C${_rs}:$C${_re}))"
                             f"+{K['red']}*(1+{x})^(-$C${_re}))")
            _eir_at, _nx = xl_eir_solver(put, J, _we + 2, f"$D${_rs}", _pv, (N4, N6), GREY)
            put(J, _rr, 3, f"={_eir_at}", fmt=P2, align="right", border=True)
            _r = _nx - 1
        _r += 1
    # 판정과 실제 회계처리 설정이 어긋나면 이 조서 안에서 「분리 판단」 시트와
    # 「회계처리」 시트가 서로 다른 말을 하게 된다. 그 사실을 여기 적어 둔다.
    _mis = [(_nm, SP[_k]) for _k, _nm in
            (("put", "조기상환청구권"), ("call", "매도청구권"))
            if not SP[_k].get("설정일치", True)]
    sec(J, _r, "판정과 회계처리 설정이 맞는가", span=6); _r += 1
    if _mis:
        for _nm, _d in _mis:
            _set = ((("주계약에 포함 (분리하지 않음)" if put_in_host(tm) else "분리 · 파생상품부채")
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
                  "결론·근거 문장은 그 결과를 옮겨 적은 것이고, 조기상환권의 행사금액·상각후원가 비교(행사일별 "
                  "표 · 가장 큰 차이 · 판정)는 가정 시트를 참조하는 **수식**으로도 계산한다 — 비교기준은 가정 시트의 "
                  "회계정책 칸이며 기준서가 정한 수치가 아니다(실무사례 28쪽). 판정에 쓰는 상각후원가는 문단 B4.3.5(5) "
                  "말미대로 **자본요소를 분리하기 전** 금액(전환사채에 배분된 거래가격)에서 출발하므로 분리 여부 설정과 "
                  "무관하다. 전환권이 부채면 그 규정을 회계정책으로 준용해 전환권을 떼기 전 금액에서 출발한다. "
                  "이 판정은 수치 비교 결과이며, 분리 여부는 계약 조건과 함께 평가자가 판단한다. 평가기준일이 발행일보다 "
                  "뒤이면 다시 판정하지 않고 최초 인식 때의 결론을 이어 적용한다 (1109 문단 B4.3.11).",
        color=GREY, size=9)
    for _row in J.iter_rows(min_row=5, max_row=_r, min_col=3, max_col=3):
        for _c in _row: _c.alignment = Alignment(wrap_text=True, vertical="top")

    # ── 해설 ──
    if holder_on(tm):
        # 투자자 관점 — 배분·상각표 대신 공정가치 측정과 투자자 분개. 화면과 같은 함수.
        write_holder_sheet(wb, tm, holder_rows(tm, full, b0, b1, b2, ca),
                           put, sec, title, N4, N0, LIGHT, RED, GREY)
    elif acc_mode(tm) == "fv_only":
        # 최초 인식 배분·분개·거래원가 표를 지우고 공정가치만 남긴다 — 세 경로(화면·값·수식)가
        # acc_mode 하나로 같은 판단을 한다. 결산 분개에 최초 인식 숫자가 옮겨 가는 것을 막는다.
        _ei = wb.sheetnames.index("회계처리"); wb.remove(wb["회계처리"])
        E = wb.create_sheet("회계처리", _ei); E.sheet_view.showGridLines = False
        for cc, w in (("B", 46), ("C", 16), ("D", 18)): E.column_dimensions[cc].width = w
        title(E, 2, "회계처리 — 공정가치 산출 전용", span=3)
        for _i, _tx in enumerate(FV_ONLY_XL):
            put(E, 4+_i, 2, _tx, color=(RED if _i == 0 else GREY), bold=(_i == 0), size=(10 if _i == 0 else 9))
            E.merge_cells(start_row=4+_i, start_column=2, end_row=4+_i, end_column=4)
            E.cell(row=4+_i, column=2).alignment = Alignment(wrap_text=True, vertical="top")
            E.row_dimensions[4+_i].height = 15 if _i == 0 else 42
        sec(E, 8, "평가기준일 공정가치", span=3)
        for i, h in enumerate(["항목", "100 기준", "전액 기준 (원)"]):
            put(E, 9, 2+i, h, bold=True, fill=LIGHT, align="center", border=True, size=9)
        for i, (k, v) in enumerate(fv_only_rows(tm, full, b0, b1, b2, ca)):
            put(E, 10+i, 2, k, border=True)
            put(E, 10+i, 3, v, fmt=N4, align="right", border=True)
            put(E, 10+i, 4, v/100*tm.face_total, fmt=N0, align="right", border=True)
    H = wb.create_sheet("해설", 0); H.sheet_view.showGridLines = False
    H.column_dimensions["B"].width = 22; H.column_dimensions["C"].width = 96
    title(H, 2, "수식 조서 사용 안내", span=2)
    _tf = tm.model == "TF"
    _aux = _tf and int(getattr(tm, "k_split", 0)) == 1 and tm.k_w > 0 and tm.k_method in (1, 2)
    ex = [("재계산 범위", ""),
      ("입력 변경에 따라 재계산되는 항목", "가정 시트의 노란 칸(계약 금액·비율, 주가, 주가변동성"
                     + ("" if _bdt else ", 금리곡선 수익률") + " 등)을 "
                     "바꾸면 그 값을 쓰는 모든 계산 시트가 엑셀 안에서 다시 계산됩니다."
                     + (" BDT 금리격자를 쓰는 조서라 금리곡선 수익률과 BDT 금리변동성 σ 는 노란 칸이 아닙니다 — "
                        "BDT 기준금리 a 가 그 두 값에 맞춰 앱에서 역산한 고정값이기 때문입니다." if _bdt else "")),
      ("앱에서 다시 만들어야 하는 항목", "노드 수·계산 간격, 행사일·이자 지급일(계약 날짜를 노드에 배정한 결과), "
                     "전환가격 조정 주기, 평가모형(TF/GS)·매도청구권 평가방법, 금리곡선의 만기 칸 수"
                     + (", 금리곡선 수익률, BDT 금리변동성 σ" if _bdt else "")
                     + ". 이 파일에서 바꾸면 결과가 따라오지 않거나 틀린 값이 나오므로 앱에서 바꾸고 조서를 새로 만듭니다."),
      ("고정 산출값 (주황색)", (("앱이 역산해 값으로 넣은 것 — "
                          + ", ".join(([] if not _bdt else ["BDT 기준금리 a"])
                                      + ([] if _irref else ["00 격자 공통 11·12행의 선도이자율(부트스트래핑 결과)"]))
                          + ". 관련 입력을 이 파일에서 바꾸면 고정 산출값은 따라오지 않으므로 앱에서 다시 계산합니다. "
                          if (_bdt or not _irref) else "없습니다. ")
                         + ("선도이자율은 IR 시트(입력곡선 → 부트스트래핑 → 현물 → 선도)에서 수식으로 계산되어 "
                            "00 격자 공통 11·12행으로 옵니다. " if _irref else "")
                         + "상각표와 분리 판단의 유효이자율도 수식(앱과 같은 이분법)이라 입력을 바꾸면 따라옵니다.")),
      ("계약일과 노드 배정", "전환가격 조정일·이자 지급일·조기상환일·매도청구일은 «00 계약일 목록» 시트에 계약일로 "
                     "적혀 있고, 각 계약일이 어느 노드에 배정되는지는 그 시트의 수식(COUNTIF)이 정합니다. 00 격자 공통 "
                     "6행(조정일)·9행(지급 회수)·20·27행(행사월)은 그 목록을 찾아 옵니다 (아래 «행사일 대조» 표와 규칙). "
                     "계약일 자체는 앱이 계약서에서 적은 값이라 바꾸려면 앱에서 조서를 다시 만듭니다. 같은 날의 권리는 "
                     "같은 노드에 배정됩니다. 행사금액은 노드 날짜가 아니라 계약일의 경과기간으로 계산합니다. "
                     "그래서 가정 시트의 이자 지급주기·조기상환 시작은 입력칸(노란색)이 아닙니다."),
      ("각 계산 시트의 공통조건 1~17행", "날짜·스텝·행사 가능 표시·행사금액·이자·만기상환금액·선도이자율·σ·u·d·q 는 "
                   "「00 격자 공통」에서 한 번 계산하고, 각 계산 시트의 1~17행은 그 칸을 참조합니다. "
                   "행사 시작일이 다른 시트(매도청구 대상 물량 등)만 행사 가능 표시(3~5행)를 따로 계산합니다."),
      ("행사금액 계산 과정", "00 격자 공통 20~26행(조기상환)·27~33행(매도청구) — 계약 행사월 → "
                          "경과연수 → 할증률 → 기지급 이자 공제 → 행사금액 순서입니다. 행사일인 칸에만 값이 있습니다. "
                          "각 계산 시트 7·8행은 이 금액을 그 시트의 행사 가능 표시(4·5행)에 따라 적용합니다."),
      ("", ""),
      ("시트 순서", ""),
      # 시트는 앱에서 고른 방법만 만든다. 안내도 실제로 만들어진 시트만
      # 적어야 한다 — 없는 시트를 가리키면 조서를 읽는 사람이 헤맨다.
      ("흐름", " → ".join(["가정"] + [x for x in wb.sheetnames
                                    if x not in ("가정", "해설")])),
      ("담긴 것", "앱에서 고른 평가모형과 매도청구권 평가방법의 계산 시트만 싣습니다."
                + (" 11~14 전환확률 시트는 TF 평가에서 매도청구 행사가격을 주식결제·현금결제 성분으로 "
                   "배분하는 데에만 쓰는 보조 계산입니다(한공회 실무사례 4.3.3)." if _aux else "")),
      ("전환가격", ("전환가격을 바꾸는 조항(리픽싱·상장 조정)이 없어 02·03 시트를 싣지 않았습니다. "
                  "04 전환가치 = 100 × 주가 ÷ 가정의 현재 전환가액입니다." if _kconst else
                  "도달확률 시트는 02a 이월 전환가격이 경로가중치 방법을 쓸 때 참조합니다. "
                  "구간별 상승확률이 달라 시점별 도달확률을 앞 시점부터 순차적으로 계산합니다.")),
      ("16 부채요소", "전환권이 없으면 가치가 주가와 무관하므로 한 줄로 계산합니다. 결과 시트가 이 값을 씁니다. "
                    "회계상 부채요소 배분액과 금액이 다를 수 있습니다."),
      ("", ""),
      ("계산 방향", ""),
      ("앞으로 (왼쪽 → 오른쪽)", ("01 주가 → 04 전환가치" if _kconst else
                                "01 주가 → 도달확률 → 02a 이월 → " + ("02b 정기 조정 → " if _ipo_on else "")
                                + "02 전환가격 → 03 전환비율 → 04 전환가치")
                               + ". 평가기준일에서 만기 쪽으로 계산합니다. 각 칸은 같은 열이나 왼쪽 열만 참조합니다."),
      ("뒤로 (오른쪽 → 왼쪽)", ("만기 열에서 시작해 07 보유가치(다음 시점의 05·06 할인) → 09 의사결정(04·07 과 7·8행 비교) → "
                             "05 지분가치·06 부채가치(결정에 따라 받는 금액) → 08 금융상품가치 순서입니다. "
                             if _tf else
                             "만기 열에서 시작해 13 GS 보유가치(다음 시점 값을 12 GS 할인율로 할인) → "
                             "14 GS 금융상품가치(전환·상환·계속보유 중 선택) → 11 GS 전환확률 순서입니다. ")
                             + "10 주계약가치·16 부채요소도 같은 방향입니다. 각 칸은 같은 열이나 오른쪽 열만 참조합니다."),
      ("노드 식", ("계속보유가치 = (다음 시점 상승 노드 × q + 하락 노드 × (1 − q)) × 할인계수. 주식결제분은 무위험 "
                  "선도이자율, 현금결제분은 위험 선도이자율로 할인하고 그 구간의 이자(9행)를 더합니다."
                  if _tf else
                  "계속보유가치 = 다음 시점 상승 노드 × q × 상승 노드 할인계수 + 하락 노드 × (1 − q) × 하락 노드 할인계수. "
                  "할인율은 각 노드의 전환확률로 무위험·위험 선도이자율을 가중한 값입니다.")),
      ("순환참조가 없는 이유", ("05·06 은 같은 시점의 09 의사결정을 참조하고, 07 보유가치는 다음 시점의 05·06 을 참조합니다. "
                            if _tf else "") + "다음 시점의 계산 결과를 참조하므로 순환참조가 발생하지 않습니다."),
      ("확인 방법", "아무 칸에서 F2 를 누르면 참조 테두리가 같은 열이나 오른쪽 열에만 생깁니다."),
      ("", ""),
      ("노드에서 무엇을 고르는가", ""),
      ("투자자 권리", "전환 · 조기상환청구 · 계속보유 가운데 투자자에게 가장 유리한 것을 고릅니다."),
      ("발행자 권리", ("발행자 상환권 — 발행회사가 정한 금액으로 상환할 수 있는 권리. 행사되면 투자자가 받는 금액이 "
                     "발행자 상환금액으로 제한됩니다." if issuer_redeem(tm) else
                     "매도청구(발행회사 또는 지정 제3자의 매수청구). 행사되면 투자자가 받는 금액이 매도청구금액으로 제한됩니다.")),
      ("적용한 식", decide_formula_text(tm) + "　— " + priority_label(tm)),
      ("우선순위", PRIORITY_NOTE),
      ("만기 시점", "매도청구는 없습니다. 전환가치와 현금(MAX(조기상환금액, 만기상환금액) + 이자) 두 가지만 비교합니다."),
      ] + ([("TF 시트의 합계 관계", "TF 계산 시트에서는 08 금융상품가치 = 05 지분가치 + 06 부채가치가 모든 노드에서 "
                                "성립합니다. 주식결제분·현금결제분은 모형의 할인 구분이며 회계상 자본요소·부채요소 "
                                "배분액과 금액이 다를 수 있습니다.")] if _tf else []) + [
      ("", ""),
      ("전환·상환·계속보유 가치가 같은 경우의 선택 기준", ""),
      ("언제 생기나", "전환가격이 그날 주가로 조정되는 날에는 전환가치가 정확히 100 이 됩니다. "
                  "같은 날 조기상환금액도 100 이면 두 가치가 같습니다."),
      ("기준", "전환가치가 상환 금액보다 허용오차 넘게 클 때만 전환을 고르고, 같으면 상환을 고릅니다. "
              "허용오차는 1e-9(10억분의 1)이고, 견주는 금액이 1,000 을 넘으면 그 금액의 1e-12 배입니다 "
              "(수식: MAX(1E-9, 1E-12 × 두 금액 중 큰 쪽)). 엔진과 같은 식입니다."),
      ("정해 두는 이유", "기준이 없으면 계산 오차가 선택을 가르고, TF 는 주식결제분과 현금결제분을 다른 이자율로 "
                    "할인하므로 한 노드의 선택이 전체 가치를 움직입니다."),
      ("", ""),
      ("유의사항", ""),
      ("매도청구권 평가방법", "이 조서는 「"
                    + ["유무가치비교법 — 매도청구 대상 물량과 비대상 물량의 가치 차이",
                       "옵션차익법 · 혼합할인율 — 매도청구 행사가격을 {sp}로 배분하고 혼합할인율로 할인",
                       "옵션차익법 · 주식결제·현금결제 분리 — 매도청구 행사가격을 {sp}로 배분하고 성분별로 할인"][tm.k_method]
                      .format(sp=("GS 전환확률(⑪)" if int(tm.k_split) == 1
                                  else "가치 구성비율(⑰ = 주식결제분 ÷ 콜 반영 전 CB 가치)"))
                    + "」 방법으로 계산했습니다. 가정 시트의 평가방법 줄은 적용한 방법을 적어 둔 것이며 선택 칸이 아닙니다."),
      ("신용위험 처리", ("이 조서는 TF 로 계산했습니다. " if _tf else "이 조서는 GS 로 계산했습니다. ")
                     + "가정 시트의 TF/GS 줄을 바꿔도 결과가 따라오지 않습니다. 10 주계약가치와 16 부채요소는 "
                       "전환이 없어 두 모형의 결과가 같습니다."),
      ("상각표", "유효이자율과 상각 내역 모두 수식입니다. 유효이자율은 표 아래 «계산 과정» 에서 앱과 같은 "
                "이분법으로 구합니다. 회차 수와 경과연수는 계약 지급일에서 앱이 정한 값입니다.")]
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
    rename_sheets(wb, sheet_display_names(tm), _BW_WORDS if is_bw(tm) else ())
    polish_wb(wb)
    relabel_inst(wb, tm)
    if as_workbook: return wb
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue()



def build_xlsx_sha(tm: Terms, R, formula: bool = False, attach=None, *, as_workbook=False):
    """주주간계약 조서. 값 조서와 수식 조서를 **한 함수**에서 만든다.

    두 조서가 같은 답을 내야 한다는 요구가 있어, 자리와 차례를 따로 적어 두면
    언젠가 어긋난다. 그래서 칸마다 「값」과 「수식」을 나란히 주고 스위치 하나로
    고른다 — 구조가 하나뿐이니 갈라질 자리가 없다.

    회차가 하나면(단일 계약) 시트는
        해설 · 가정 · 01 주가 · 02 보통주 가치 · 03 풋가치 · 04 콜가치 · 결과 · 회계처리
    회차가 여럿이면 회차마다 「1·가정 … 1·결과」 한 벌씩 만들고, 「회차 합계」 가 각 회차
    결과 시트의 금액을 수식으로 모아 더한다. 회계처리는 합계를 본다.
    """
    from openpyxl import Workbook

    wb = Workbook(); wb.remove(wb.active)
    _links = (None, None, None)
    if attach:
        # 산출 시트의 결과 셀 — 수식 조서는 가정 σ 와 트리 8·9행 선도이자율을 여기에 잇는다.
        _links = attach_reports(wb, tm, **attach)
        _order = list(wb.sheetnames)
    K = report_kit(wb)
    multi = bool(R.get("portfolio"))
    entries = R["rows"] if multi else [dict(name=(tm.tranche or "단일 계약"), tm=tm, R=R)]
    _sha_intro(wb, K, tm, formula, multi, entries)
    refs = []
    for k, e in enumerate(entries, 1):
        if e["R"].get("deal"):
            # 행사·매매가 확정되고 결제만 남은 회차 — 선택권 트리 없이 확정 거래만 잰다
            refs.append(_sha_deal_block(wb, K, e["tm"], e["R"], formula, f"{k}·" if multi else "", e["name"]))
            continue
        refs.append(_sha_block(wb, K, e["tm"], e["R"], formula, f"{k}·" if multi else "", e["name"],
                               links=_sha_links(tm, e["tm"], _links) if formula else None))
    tot = _sha_total(wb, K, tm, entries, refs, formula, recon=R.get("recon")) if multi else None
    _sha_accounting(wb, K, tm, R, entries, refs, tot, formula)
    if attach:
        # 산출내역은 뒤로 보낸다. 앞의 시트가 조서의 본문이다.
        for nm in _order:
            wb.move_sheet(nm, offset=len(wb.sheetnames))
    polish_wb(wb)
    return wb if as_workbook else _save(wb)


def _sha_intro(wb, K, tm, formula, multi, entries):
    put, head, cols, sheet = (K[x] for x in ("put", "head", "cols", "sheet"))
    H = sheet("해설", widths=[22, 96])
    head(H, 2, "주주간계약 평가 조서 — 읽는 법",
         "사채가 없는 계약이다. 주식 보유자가 이미 가진 지분에 풋과 콜이 붙어 있을 뿐이라 순차 차감이 "
         "아니다. 한쪽 행사로 같은 주식의 상대 권리가 끝나는 물량은 두 권리자의 행사 판단을 함께 풀고, "
         "그렇지 않은 물량은 두 권리를 따로 잰다 — 계약조건(가정 시트)이 정한다.", span=2)
    _rows = [
        ("무엇을 재나", "풋(주식 보유자가 상대방에게 주식을 사 달라고 요구할 권리)과 콜(상대방이 주식 "
                     "보유자에게 주식을 팔라고 요구할 권리)을 미국형으로 잰다. 계약서의 "
                     "「매수청구권·매도청구권」 명칭이 아니라 **누가 누구에게 어떤 거래를 요구하는가**로 "
                     "풋·콜을 가른다."),
        ("금액 단위", "트리는 주당 기준가격 100 기준으로 계산하고, 결과 시트에서 1주당 금액(원)과 "
                   "대상 주식수를 곱해 원 단위로 바꾼다. 풋과 콜의 대상 주식수가 다르면 각각 곱한다."),
        ("보통주 가치", "100 × 주가 ÷ 풋 주당 기준가격. 주당 기준가격은 행사가격 계산의 출발 금액이지 "
                  "주식의 주당가치가 아니다 — 주식의 가치는 평가기준일 주가다."),
        ("같은 주식 물량", "한쪽 행사로 상대 권리가 함께 끝나는 물량은 «05 행사 판단» 시트가 노드마다 "
                      "풋 권리자 · 콜 권리자가 각각 «지금 행사 (끝나는 상대 권리 포함)» 와 «계속 보유» 를 "
                      "비교해 풋 · 콜 · 보유를 정한다. 둘 다 행사하려 하면 동시 행사 우선권을 따른다. "
                      "풋만 · 콜만 남는 물량은 상대 권리 없이 따로 잰다 (03b · 04b). 결과 시트 3 이 "
                      "물량별 금액과 합계를 싣는다."),
        ("거래 시점", SHA_NO_LAG),
        ("풋 행사 시 가치", "MAX(풋 행사금액 − 보통주 가치, 0). 행사금액 = 100 × (1 + 가격 가산율의 누적) — "
                     "가산율 0% 이면 고정 행사가격이다."),
        ("콜 행사 시 가치", "MAX(보통주 가치 − 콜 행사금액, 0). 콜 행사금액 = 100 × (콜 기준가격 ÷ 풋 기준가격) "
                     "× (1 + 콜 가산율의 누적)."),
        ("행사일", "계약상 행사일은 그날 이후 첫 노드에 배정한다. 기간 중 "
                "언제든지면 기간 안의 모든 노드에서 행사할 수 있다. 가정 시트 아래 «행사일 대조» "
                "표가 계약일과 실제로 쓴 노드를 나란히 싣는다."),
        ("가격 가산기간", "노드 날짜가 아니라 계약 행사월(가격 가산 기산일부터 개월)로 센다. «계약 개월 ÷ "
                     "12» 또는 «평가기준일까지 개월 ÷ 12 + 이후 실제 일수 ÷ 365» — 가정 시트에서 고른다."),
        ("적격상장", "그 노드의 주가가 최소 기준을 넘으면 상장이 이루어진 것으로 본다. 풋이 소멸하고, "
                  "콜도 함께 끝나는지는 가정에서 고른다."),
        ("할인율", "풋은 현금을 받을 권리라 대금을 지급할 의무자의 신용위험이 붙는다 (트리 8행). 콜은 주식을 "
                 "받을 권리라 무위험으로 잰다 (9행). 위험중립확률은 무위험 선도이자율에서 배당수익률을 뺀 드리프트로 "
                 "잰다 (13행). 풋 할인 곡선은 사용자가 고른 것이다 — 계약상 실제 대금 지급 의무자의 신용을 대표하는지 "
                 "근거를 남기십시오. 앱은 모회사 신용등급을 자회사 의무에 자동으로 쓰지 않으며, 매도 대상 주식에 설정된 "
                 "담보(근질권)를 이유로 할인율을 바꾸거나 금액을 차감하지 않는다. 한계: 같은 주식 물량의 행사 판단은 "
                 "이 할인율로 할인한 보유가치를 비교하는 범위에서만 상대방 신용을 반영한다."),
        ("총액 부채", "발행회사가 풋 의무자면 기준서 1032 문단 23 에 따라 옵션 공정가치가 아니라 "
                   "**상환금액의 현재가치**를 총액으로 싣는다. 결과 시트에 함께 낸다."),
        ("이 파일", ("수식 조서다 — 노란 칸(주가·변동성·배당수익률·가산율·수량 등)을 바꾸면 트리·결과·"
                   + ("회차 합계·" if multi else "") + "회계처리까지 다시 계산된다. 흰 칸은 앱이 정한 "
                   "구조 설정이다 — 행사일 배정·노드 수·상대 권리 소멸 여부·풋 매수 의무자·동시 행사 "
                   "우선권·풋 할인 기준을 바꾸려면 앱에서 조서를 다시 만든다. 금리 산출(IR)·변동성 "
                   "산출(σ) 시트를 붙였으면 트리의 선도이자율과 변동성이 그 시트를 따른다.")
                  if formula else
                  "값 조서다 — 앱이 계산한 값을 담은 스냅샷이다. 수식 조서와 같은 자리에 같은 값을 낸다."),
    ]
    if multi:
        _rows.insert(1, ("회차", f"{len(entries)}개 회차를 따로 잰다 — 회차마다 행사기간·기준가격·가산율·"
                              "수량(과 필요하면 금리·변동성)이 다르다. 연도별 미행사 물량을 다음 회차로 "
                              "넘기지 않는다. 「회차 합계」 시트가 각 회차 결과를 더한다."))
    cols(H, 5, ["항목", "설명"], widths=[22, 96])
    for i, (a, b) in enumerate(_rows):
        put(H, 6+i, 2, a, bold=True, size=9, border=True)
        put(H, 6+i, 3, b, size=9, border=True, wrap=True)
        H.row_dimensions[6+i].height = max(16, 14*(1 + len(b)//80))
    _hr = 6 + len(_rows) + 1
    put(H, _hr, 2, "재현 기록", bold=True, size=9.5)
    for i, (_l, _v) in enumerate(stamp_rows(tm, "수식" if formula else "값")):
        put(H, _hr+1+i, 2, _l, size=9, border=True)
        put(H, _hr+1+i, 3, _v, size=9, border=True)


def _sha_links(top: Terms, tm: Terms, links):
    """산출 시트(σ · IR)를 이 회차에 이어도 되는가 — 회차의 σ · 곡선 · 격자가 산출 시트와 같을 때만.

    회차별 금리·변동성을 따로 넣은 회차는 산출 시트와 값이 달라 잇지 않는다 (값으로 남는다).
    """
    vol, _rv, ir = links or (None, None, None)
    derive(top); derive(tm)
    same_sig = abs(float(tm.sig) - float(top.sig)) < 1e-12
    same_ir = (int(tm.n) == int(top.n) and abs(tm.T - top.T) < 1e-12 and list(tm.rf_curve) == list(top.rf_curve)
               and list(credit_curve(tm)) == list(credit_curve(top)) and tm.y_type == top.y_type
               and int(tm.cmp_rf) == int(top.cmp_rf) and int(tm.cmp_cr) == int(top.cmp_cr))
    return dict(vol=(vol if same_sig else None), ir=(ir if same_ir else None))


def _sha_block(wb, K, tm, R, formula, pre, name, links=None):
    """한 회차의 가정 · 01~04 트리 · 결과 시트. 돌려주는 것은 결과 시트 칸 주소(합계·회계가 쓴다)."""
    from openpyxl.utils import get_column_letter as gl

    derive(tm)
    n, dt_ = int(tm.n), tm.T/int(tm.n)
    R0 = 24                                   # 트리 첫 자료행
    N2, N0, P2, N4, N6 = '#,##0.00', '#,##0', '0.00%', '0.0000', '0.000000'
    DATE = 'yyyy-mm-dd'
    YEL = INPUT_FILL                          # 입력 칸 — 모든 조서가 같은 노란색
    put, head, sec, cols, note, sheet = (K[x] for x in
        ("put", "head", "sec", "cols", "note", "sheet"))
    Q = lambda nm: f"'{nm}'"
    V = lambda val, fx: (fx if formula else val)
    SA, S1, S2, S3, S4, SR = (pre + x for x in ("가정", "01 주가", "02 보통주 가치", "03 풋가치",
                                                "04 콜가치", "결과"))
    S5, S3B, S4B = (pre + x for x in ("05 행사 판단", "03b 풋 (풋만)", "04b 콜 (콜만)"))
    qp, qc = sha_qty(tm)
    kck = tm.K0*R.get("kc", 1.0)
    _price_note = getattr(tm, "_price_note", "")
    _nd = node_dates(tm, n, dt_)
    _dd = lambda m: months_to_date(tm.d_issue, m).isoformat()

    def _sched_txt(s_, e_, f_, dates, cont, on):
        if not on: return "없음"
        body = f"{_dd(s_)} ~ {_dd(e_)}"
        if cont: return body + f" · 기간 중 언제든지 (노드 {len(dates)}개)"
        if abs(e_ - s_) < 1e-9: return body.split(" ~ ")[0] + " 1회"
        return body + f" · {f_:g}개월마다 · 격자에 {len(dates)}회"

    # ── 가정 ──
    A = sheet(SA, widths=[36, 18, 14, 60])
    head(A, 2, "가정" + (f" — {name}" if pre else ""),
         ("노란 칸 = 엑셀에서 고쳐도 되는 입력 — 바꾸면 그 값을 쓰는 트리·결과가 다시 계산된다. 흰 칸 = 앱이 정한 "
          "구조 설정(행사일 · 노드 수 · 상대 권리 소멸 · 같은 주식 물량의 판단 구조 · 풋 매수 의무자 · 우선권 · 할인 기준) — "
          "바꾸려면 앱에서 조서를 다시 만든다."
          if formula else "앱이 계산한 값을 그대로 담았다."), span=4)
    K_ = {}

    def kv(r, nm, val, fmt=None, memo="", key=None, yellow=True):
        put(A, r, 2, nm, bold=True, size=9.5, border=True)
        put(A, r, 3, val, fmt=fmt, size=9.5, align="right", border=True,
            fill=(YEL if (formula and yellow) else None))
        if memo: put(A, r, 4, memo, size=9, color=RPT["grey"], wrap=True)
        if key: K_[key] = f"{Q(SA)}!$C${r}"

    sec(A, 4, "1. 대상 지분 · 격자", span=4)
    kv(5, "평가기준일 주가 (원)", tm.S0, N2,
       " · ".join(f"{_l} {_v}" for _l, _v in px_trace(tm)), "S0")
    kv(6, "풋 주당 기준가격 (원)", tm.K0, N2,
       _price_note or "보통주 가치 = 100 × 주가 ÷ 이 값 · 풋 행사금액의 출발점", "K0")
    kv(7, "평가기준일", dt.date.fromisoformat(tm.d_base), DATE, "", "d_base", yellow=False)
    kv(8, "가격 가산 기산일 → 평가기준일 (계약 개월)", tm.elapsed_m, N4,
       f"기산일 {tm.d_issue}", "elm", yellow=False)
    kv(9, "평가기준일 → 격자 끝 (계약 개월)", tm.rem_m, N4, f"격자 끝 {tm.d_mat}", "remm", yellow=False)
    kv(10, "평가기준일 → 격자 끝 (년, 실제 일수 ÷ 365)", tm.T, N6, "", "T", yellow=False)
    kv(11, "노드 수", n, N0, "", "n", yellow=False)
    kv(12, "Δt (년)", dt_, N6, "", "dt", yellow=False)
    _volref = (links or {}).get("vol"); _irref = (links or {}).get("ir")
    kv(13, "변동성 σ (연)", (f"={_volref}" if _volref else tm.sig), P2,
       ("변동성 산출 시트의 적용값을 따른다 — 종가를 고치면 여기까지 따라온다" if _volref else ""),
       "sig", yellow=not _volref)
    kv(14, "보통주 배당수익률 (연, 연속)", tm.div_y, P2, "위험중립 드리프트에서 뺀다", "divy")
    kv(15, "u = EXP(σ√Δt)", V(R["u"], f"=EXP({K_['sig']}*SQRT({K_['dt']}))"),
       N4, "", "u", yellow=False)
    kv(16, "d = 1 ÷ u", V(R["d"], f"=1/{K_['u']}"), N4, "", "dd", yellow=False)
    kv(17, "q (첫 구간)", R["q"], N4, "각 구간의 q 는 트리 13행에서 다시 잰다 (마지막 열만 이 값)", "q",
       yellow=False)
    kv(18, "1 − q", 1-R["q"], N4, "", "q1", yellow=False)
    kv(19, "가격 가산 기산일", dt.date.fromisoformat(tm.d_issue), DATE,
       "실제 일수 기준이면 이날부터 행사일까지 일수 ÷ 365", "d0", yellow=False)

    sec(A, 20, "2. 풋 — 주식 보유자가 상대방에게 주식을 사 달라고 요구할 권리", span=4)
    kv(21, "풋 행사일", _sched_txt(tm.sha_put_s, tm.sha_put_e, tm.sha_put_f, R["p_dates"],
                                  R["p_cont"], R["has_put"]), None, "앱이 노드에 배정했다", yellow=False)
    kv(22, "풋 가격 가산율 (연)", tm.sha_put_yield, P2, "0% 이면 고정 행사가격", "pyld")
    kv(23, "풋 가산 복리 횟수 (연)", tm.sha_put_cmp, N0, "0 이면 단리", "pcmp")
    kv(24, "풋 대상 주식수", qp, N0, "", "qp")
    if int(tm.sha_disc) == 2:
        kv(25, "풋 할인 스프레드 (연)", tm.sha_spread, P2, "풋 할인 선도이자율 = 무위험 선도 + 이 값 (트리 8행)", "spr")

    sec(A, 26, "3. 콜 — 상대방이 주식 보유자에게 주식을 팔라고 요구할 권리", span=4)
    kv(27, "콜 행사일", _sched_txt(tm.sha_call_s, tm.sha_call_e, tm.sha_call_f, R["c_dates"],
                                  R["c_cont"], R["has_call"]), None, "앱이 노드에 배정했다", yellow=False)
    kv(28, "콜 주당 기준가격 (원)", kck, N2, "콜 행사금액의 출발점", "kck")
    kv(29, "콜 가격 가산율 (연)", tm.sha_call_prem, P2, "0% 이면 고정 행사가격", "cprem")
    kv(30, "콜 가산 복리 횟수 (연)", tm.sha_call_cmp, N0, "0 이면 단리", "ccmp")
    kv(31, "콜 대상 주식수", qc, N0, "", "qc")

    sec(A, 33, "4. 적격상장 · 상대 권리 소멸 · 할인 · 가산기간 · 우선권", span=4)
    _ipk = int(getattr(tm, "sha_ipo_kind", -1))
    kv(34, "상장 조항 (1 반영)", int(tm.ipo_on), N0,
       ("" if not int(tm.ipo_on) else "종료 조건: 실제 상장 완료 — 상장 스텝에서 주가와 무관하게 종료" if _ipk == 1
        else "종료 조건: 그 시점 주가가 기준을 넘으면 상장으로 봄 (주가 기준)"), "ipoon")
    kv(35, "상장 스텝", R["qi_step"], N0,
       (f"계약 {_dd(tm.ipo_m)}" if int(tm.ipo_on) else "반영하지 않음"), "ipos", yellow=False)
    kv(36, "주가 기준 최소 주가 (원)", tm.ipo_min, N2,
       ("실제 상장 기준이라 쓰지 않는다" if _ipk == 1 else "그 노드 주가가 이 값을 넘으면 상장 성공"), "ipomin",
       yellow=(_ipk != 1))
    kv(37, "상장 시 콜도 소멸 (1)", int(tm.sha_qipo_kill), N0, "", "qkill")
    kv(38, "한쪽 행사 시 같은 물량의 상대 권리 (0 존속 / 1 소멸)", int(tm.sha_kill), N0,
       ("같은 주식에 붙은 물량은 두 당사자의 행사 판단을 한 격자에서 함께 푼다 (⑤ 행사 판단)"
        if R.get("linked") else "두 권리를 따로 잰다 — 행사확률 합이 1 을 넘을 수 있다"), yellow=False)
    kv(39, "풋 할인 기준", ["무위험", "위험 곡선", "무위험 + 스프레드"][int(tm.sha_disc)],
       None, ("선도이자율은 IR 선도이자율 시트에서 수식으로 온다 (트리 8 · 9행)" if _irref
              else "선도이자율은 트리 8 · 9행에 앱이 계산한 값으로 들어 있다"), yellow=False)
    kv(40, "풋 행사 시 주식매수 의무자", ["콜 권리자(상대 주주)", "발행회사", "상대 주주 · 발행회사 연대"][int(tm.sha_writer)],
       None, "발행회사면 1032 문단 23 총액 부채", yellow=False)
    kv(41, "가격 가산기간 (1 계약 개월 ÷ 12 / 0 실제 일수)", int(getattr(tm, "acc_basis", 1)), N0,
       "1 이면 기산일부터 계약 행사월 ÷ 12, 0 이면 기산일부터 행사일까지 실제 일수 ÷ 365", "accb")
    _lq = float(R.get("link_q", 0.0) or 0.0)
    _lin = float(getattr(tm, "sha_link_q", -1.0))
    _qov = _lq if R.get("linked") else (_lin if _lin >= 0 else min(qp, qc))
    kv(42, "같은 주식에 붙은 풋·콜 물량 (주)", V(_qov, (_qov if (R.get("linked") or _lin >= 0)
                                                    else f"=MIN({K_['qp']},{K_['qc']})")), N0,
       ("한쪽 행사로 함께 끝나는 물량 — 나머지 풋·콜 물량은 상대 권리 없이 따로 잰다 (③b · ④b)"
        if R.get("linked") else "계약 대상 주식수(결과 6행)를 셀 때 한 번만 센다 — 평가는 두 권리를 따로 한다"),
       "qov", yellow=bool(R.get("linked") or _lin >= 0))
    kv(43, "동시 행사 우선권 (0 풋 권리자 우선 / 1 콜 권리자 우선)", int(tm.pc_order), N0,
       ("같은 노드에서 두 권리자가 모두 행사하려 할 때 — ⑤ 행사 판단 수식의 구조라 앱에서 바꾼다"
        if R.get("linked") else "같은 주식 물량의 연계 판단이 없어 쓰이지 않는다"), yellow=False)
    note(A, 44, SHA_NO_LAG, span=4)
    note(A, 45, EXDATE_RULE, span=4)
    # ── 행사일 대조 — 계약일과 실제로 쓴 노드 ──
    _xr = exercise_date_rows(tm)
    r = 47
    if _xr:
        sec(A, r, "5. 행사일 대조 — 계약상 행사일과 적용 노드", span=4); r += 1
        for c_, h in enumerate(EXDATE_COLS):
            put(A, r, 2+c_, h, bold=True, size=8.5, fill=RPT["tint"], border=True)
        r += 1
        for row in _xr:
            for c_, v in enumerate(row):
                put(A, r, 2+c_, v, size=8.5, border=True)
            r += 1
    # ── 실적 연동 행사가격 — 산식 입력이 있으면 주당 기준가격(6행)이 이 계산을 따른다 ──
    _pf = getattr(tm, "_perf", None)
    if _pf:
        _px, _mult, _loss = sha_perf_calc(_pf)
        r += 1
        sec(A, r, "6. 실적 연동 행사가격 — 주당 행사가격 = (매출액 − 차감액) × 적용 배수 ÷ 계약상 발행주식 총수", span=4); r += 1
        kv(r, "행사연도 · 기준 실적연도 · 실적 구분",
           f"{_pf.get('ey') or '?'} · {_pf.get('fy') or '?'} · {_pf.get('kind') or '구분 미입력'}", None,
           "추정 실적이면 하나의 추정값으로 고정한 단순화다 — 매출·손실률 민감도는 앱에서 본다", yellow=False); r += 1
        kv(r, "매출액 (원)", float(_pf["rev"]), N0, "", "prev"); r += 1
        kv(r, "차감액 (원)", float(_pf.get("ded") or 0.0), N0, "계약 원문의 차감 조건", "pded"); r += 1
        kv(r, "영업손익 (원, 손실은 음수)", float(_pf["op"]), N0, "부호 그대로 — 손실률은 아래에서 계산", "pop"); r += 1
        kv(r, "영업손실률", V(_loss, f"=MAX(0,-{K_['pop']}/{K_['prev']})"), P2, "MAX(0, −영업손익 ÷ 매출액)", "ploss",
           yellow=False); r += 1
        kv(r, "기준 손실률", float(_pf["thr"]), P2, "이 값을 «초과» 해야 초과 시 배수 (같으면 이하 시 배수)", "pthr"); r += 1
        kv(r, "초과 시 배수", float(_pf["hi"]), N4, "", "phi"); r += 1
        kv(r, "이하 시 배수", float(_pf["lo"]), N4, "", "plo"); r += 1
        kv(r, "적용 배수", V(_mult, f"=IF(ROUND({K_['ploss']},12)>ROUND({K_['pthr']},12),{K_['phi']},{K_['plo']})"), N4,
           "", "pmult", yellow=False); r += 1
        kv(r, "계약상 발행주식 총수 (주)", float(_pf["sh"]), N0, "계약 산식의 분모 — 평가에 쓰는 희석 주식수와 다르다", "psh"); r += 1
        kv(r, "주당 행사가격 (원)", V(_px, f"=({K_['prev']}-{K_['pded']})*{K_['pmult']}/{K_['psh']}"), N2,
           "계약에 없는 하한은 두지 않는다 — 가정 6행(주당 기준가격)이 이 값을 따른다", "ppx", yellow=False); r += 1
        put(A, 6, 3, V(_px, f"={K_['ppx']}"), fmt=N2, size=9.5, align="right", border=True)
        put(A, 6, 4, "실적 연동 산식 (아래 6 절) — 산식 입력을 바꾸면 따라온다", size=9, color=RPT["grey"], wrap=True)

    # ── 트리 시트 ──
    HEAD = ["날짜", "스텝(노드 번호)", "풋 행사 가능 (1=예)", "콜 행사 가능 (1=예)", "적격상장 스텝",
            "풋 행사금액", "콜 행사금액", "풋 선도할인율", "무위험 선도이자율",
            "주가변동성 σ", "상승계수 u", "하락계수 d", "위험중립 상승확률 q", "하락확률 1−q",
            "풋 계약 행사월 (기산일부터)", "풋 행사일", "풋 가산 경과연수",
            "콜 계약 행사월 (기산일부터)", "콜 행사일", "콜 가산 경과연수"]
    # 가산 경과연수 — 계약 개월 ÷ 12 또는 기산일부터 행사일까지 실제 일수 ÷ 365 (엔진 pyr·cyr 와 같다)
    _xyr = lambda mo, day: f"IF({K_['accb']}=1,({mo})/12,(({day})-{K_['d0']})/365)"
    _MO = lambda st: f"({K_['elm']}+{st}*{K_['remm']}/{K_['n']})"

    def newsheet(nm, ttl, memo, refs):
        W = sheet(nm, widths=[15] + [9]*(n+1))
        for r_, h in enumerate(HEAD, start=1):
            put(W, r_, 2, h, bold=True, size=8, fill=RPT["tint"], border=True)
        d0 = dt.date.fromisoformat(tm.d_base)
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            g = lambda r_, v, fm=None, col=None: put(W, r_, 3+i, v, fmt=fm,
                                                     align="center", size=8, color=col)
            stp = f"{L}$2"
            # 노드 날짜는 앱이 정한 값이다 (행사일 배정과 같은 날짜) — 엑셀 ROUND 는 반올림 방식이 달라
            # 0.5일에서 하루가 갈린다. 두 조서 모두 같은 날짜 값을 싣는다.
            g(1, _nd[i], DATE, RPT["grey"])
            g(2, V(i, (0 if i == 0 else f"={Lp}$2+1")), N0)
            # 행사 가능 표시는 15·17행(계약 행사월)에 숫자가 있는가로 정한다 — 앱이 배정한 행사일.
            g(3, V(1 if i in R["p_dates"] else 0, f"=IF(ISNUMBER({L}$15),1,0)"), N0)
            g(4, V(1 if i in R["c_dates"] else 0, f"=IF(ISNUMBER({L}$18),1,0)"), N0)
            _qf = 1 if (int(tm.ipo_on) and i == R["qi_step"] and i > 0) else 0
            g(5, V(_qf, f"=IF(AND({K_['ipoon']}=1,{stp}={K_['ipos']},{stp}>0),1,0)"),
              N0, RPT["grey"])
            g(6, V(round(R["pk"](i), 6) if i in R["p_dates"] else 0.0,
                   f"=IF({L}$3=1,100*(1+" + xl_prem(K_['pyld'], "0", K_['pcmp'], f"{L}$17") + "),0)"), N2)
            g(7, V(round(R["ck"](i), 6) if i in R["c_dates"] else 0.0,
                   f"=IF({L}$4=1,100*{K_['kck']}/{K_['K0']}*(1+"
                   + xl_prem(K_['cprem'], "0", K_['ccmp'], f"{L}$20") + "),0)"), N2)
            if i < n:
                if _irref:
                    # IR 선도이자율 시트 — G 무위험 · J 위험 (사채 조서 트리 11 · 12행과 같은 열)
                    _g, _j = f"'{_irref[0]}'!$G${_irref[1]+i}", f"'{_irref[0]}'!$J${_irref[1]+i}"
                    g(8, V(R["pdisc"](i), "=" + [_g, _j, f"{_g}+{K_.get('spr', 0)}"][int(tm.sha_disc)]), P2)
                    g(9, V(R["rf"](i), "=" + _g), P2)
                else:
                    g(8, R["pdisc"](i), P2, (RPT["amber"] if formula else None))
                    g(9, R["rf"](i), P2, (RPT["amber"] if formula else None))
                g(13, V(R["qi"](i),
                        f"=(EXP(({L}$9-{K_['divy']})*{K_['dt']})-{L}$12)/({L}$11-{L}$12)"), N4)
                g(14, V(1-R["qi"](i), f"=1-{L}$13"), N4)
            else:
                g(13, V(R["q"], f"={K_['q']}"), N4)
                g(14, V(1-R["q"], f"={K_['q1']}"), N4)
            g(10, V(tm.sig, f"={K_['sig']}"), P2)
            g(11, V(R["u"], f"={K_['u']}"), N4)
            g(12, V(R["d"], f"={K_['dd']}"), N4)
            # 계약 행사월·행사일 — 정기 행사는 계약일(빨간 숫자, 앱이 배정), 언제든지는 노드의 개월·날짜.
            for (rm, rd, ry, dates, cont, yrf, dayf) in (
                    (15, 16, 17, R["p_dates"], R["p_cont"], R["pyr"], R["pday"]),
                    (18, 19, 20, R["c_dates"], R["c_cont"], R["cyr"], R["cday"])):
                if i in dates:
                    g(rm, (V(round(dates[i], 6), "=" + _MO(stp)) if cont else round(dates[i], 6)),
                      N4, (None if cont else RPT["red"]))
                    g(rd, (V(dayf(i), f"={L}$1") if cont else dayf(i)), DATE, (None if cont else RPT["red"]))
                    g(ry, V(round(yrf(i), 10), "=" + _xyr(f"{L}${rm}", f"{L}${rd}")), N6)
        put(W, R0-2, 2, ttl, bold=True, size=11)
        put(W, R0-1, 2, "하락 횟수 r ＼ 스텝", bold=True, size=8, fill=RPT["tint"],
            border=True, align="center")
        for i in range(n+1):
            put(W, R0-1, 3+i, i, bold=True, size=8, fmt=N0, align="center",
                fill=RPT["tint"], border=True)
        for r_ in range(n+1):
            put(W, R0+r_, 2, r_, bold=True, size=8, fmt=N0, align="center",
                fill=RPT["tint"], border=True)
        put(W, R0+n+2, 2, memo, size=9, color=RPT["grey"])
        put(W, R0+n+3, 2, "이 시트가 참조하는 시트: " + refs, size=9, color=RPT["green"])
        put(W, R0+n+4, 2, "r 은 하락 횟수. 위로 갈수록 주가가 높다."
            + ("  8·9행 선도이자율과 15·16·18·19행 계약 행사월·행사일(빨간 숫자)은 앱이 정한 값이다." if formula else ""),
            size=9, color=RPT["grey"])
        W.freeze_panes = f"C{R0}"
        return W

    def fill(W, fn, fmt=N2):
        for i in range(n+1):
            L = gl(3+i); Lp = gl(2+i) if i > 0 else None
            Ln = gl(4+i) if i < n else None
            for r_ in range(i+1):
                put(W, R0+r_, 3+i, fn(i, r_, L, Lp, Ln), fmt=fmt, size=8, align="right")

    W = newsheet(S1, "① 주가트리  S = S0 × u^(스텝−r) × d^r",
                 "맨 위 노드는 직전 시점 맨 위 × u, 나머지는 직전 시점 한 칸 위 노드 × d.", SA)
    fill(W, lambda i, r_, L, Lp, Ln: V(
        round(R["S"](i, i-r_), 4),
        f"={K_['S0']}" if i == 0 else
        (f"={Lp}{R0}*{L}$11" if r_ == 0 else f"={Lp}{R0+r_-1}*{L}$12")))

    W = newsheet(S2, "② 보통주 가치 트리  100 × 주가 ÷ 풋 주당 기준가격 (보통주 1주당 가치를 기준가격 100 으로 환산)",
                 "풋·콜 행사금액(100 기준)과 견줄 수 있게 옮긴 값이다.", f"{S1} · {SA}")
    fill(W, lambda i, r_, L, Lp, Ln: V(
        round(R["eq"](i, i-r_), 6), f"=100*{Q(S1)}!{L}{R0+r_}/{K_['K0']}"))

    _QI = ((lambda L, r_: f"{L}$5=1") if int(getattr(tm, "sha_ipo_kind", -1)) == 1 else
           (lambda L, r_: f"AND({L}$5=1,{Q(S1)}!{L}{R0+r_}>{K_['ipomin']})"))
    _PEX = lambda L, r_: f"IF({L}$3=1,MAX({L}$6-{Q(S2)}!{L}{R0+r_},0),0)"
    _CEX = lambda L, r_: f"IF({L}$4=1,MAX({Q(S2)}!{L}{R0+r_}-{L}$7,0),0)"
    _EQ = lambda L, r_: f"{Q(S2)}!{L}{R0+r_}"
    _TS = repr(SHA_SETTLE_TOL)
    _blocks = R.get("blocks") or [dict(kind="ind", R=R, qp=qp, qc=qc)]
    _linked = bool(R.get("linked"))
    _same = int(tm.sha_writer) == 0
    _pfx = int(tm.pc_order) == 0            # 둘 다 행사하려 하면 풋 권리자가 먼저인가
    _RI = next((b["R"] for b in _blocks if b["kind"] in ("put", "call")), None)
    _cont = lambda SX, L, r_, Ln, rr: (f"({Q(SX)}!{Ln}{R0+r_}*{L}$13+{Q(SX)}!{Ln}{R0+r_+1}*{L}$14)"
                                       f"*EXP(-{L}${rr}*{K_['dt']})")

    def _indep(SX, kind, title, memo, Rv):
        """상대 권리 없이 따로 재는 풋(kind="put") 또는 콜 트리 — 지금 행사와 계속 보유 중 큰 쪽."""
        W = newsheet(SX, title, memo, f"{S2} · 다음 열 {SX}")
        ex = _PEX if kind == "put" else _CEX
        rr = 8 if kind == "put" else 9
        tree = Rv["P"] if kind == "put" else Rv["C"]

        def fx(i, r_, L, Lp, Ln):
            body = ex(L, r_) if i == n else f"MAX({ex(L, r_)},{_cont(SX, L, r_, Ln, rr)})"
            gate = _QI(L, r_) if kind == "put" else f"AND({_QI(L, r_)},{K_['qkill']}=1)"
            return V(round(tree[i][i-r_], 6), f"=IF({gate},0,{body})")
        fill(W, fx)

    if not _linked:
        _indep(S3, "put", "③ 풋가치트리 (주식 보유자의 풋)",
               "지금 행사(행사금액 − 보통주 가치)와 계속 보유 중 큰 쪽. 계속 보유는 다음 열을 **풋 선도할인율**(8행)로 "
               "할인한다 — 풋은 의무자에게서 현금을 받을 권리라 의무자의 신용위험이 붙는다. 적격상장 노드에서는 0 이다.",
               R)
        _indep(S4, "call", "④ 콜가치트리 (상대방의 콜)",
               "지금 행사(보통주 가치 − 행사금액)와 계속 보유 중 큰 쪽. 주식을 받을 권리라 **무위험**(9행)으로 할인한다. "
               "적격상장 시 소멸 여부는 가정에서 고른다.", R)
    else:
        # ── 같은 주식에 붙은 물량 — 두 당사자가 자기에게 걸린 권리·의무 전체로 행사와 보유를 견준다 ──
        _pc = lambda L, r_, Ln: "0" if Ln is None else _cont(S3, L, r_, Ln, 8)
        _cc = lambda L, r_, Ln: "0" if Ln is None else _cont(S4, L, r_, Ln, 9)

        def _dec(i, r_, L, Lp, Ln):
            Ln = None if i == n else Ln
            pc, cc, e = _pc(L, r_, Ln), _cc(L, r_, Ln), _EQ(L, r_)
            hp, hc = f"({pc}-{cc})", f"({cc}-{pc})"
            pv, cv = f"({L}$6-{e})", f"({e}-{L}$7)"
            pw = f"AND({L}$3=1,{pv}>={hp}-{_TS},OR({pv}>{_TS},{hp}<-{_TS}))"
            cw = (f"AND({L}$4=1,{cv}>={hc}-{_TS},OR({cv}>{_TS},{hc}<-{_TS}))" if _same else
                  f"AND({L}$4=1,{cv}>{_TS},{cv}>={cc}-{_TS})")
            if _pfx:  pw2, cw2 = pw, f"AND({cw},NOT({pw}))"
            else:     pw2, cw2 = f"AND({pw},NOT({cw}))", cw
            lab = {"put": "풋", "call": "콜", "hold": "보유"}
            return V(lab.get(R["DEC"][i][i-r_], "보유"), f'=IF({pw2},"풋",IF({cw2},"콜","보유"))')
        W = newsheet(S5, "⑤ 행사 판단 (같은 주식 물량) — 풋 · 콜 · 보유",
                     ("풋 권리자(주식 보유자 · 콜 의무자)는 «풋 행사금액 − 보통주 가치» 를 계속 보유의 «풋 − 콜» 과, "
                      + ("콜 권리자(= 풋 의무자)는 «보통주 가치 − 콜 행사금액» 을 계속 보유의 «콜 − 풋» 과 견준다 — "
                         "행사하면 상대 권리·의무가 함께 끝나므로 자기 옵션만 보면 외가격이어도 행사가 나을 수 있다. "
                         if _same else
                         "콜 권리자(풋 의무자가 아닌 다른 당사자)는 «보통주 가치 − 콜 행사금액» 을 자기 콜의 계속 보유와 견준다. ")
                      + "지금 행사가 이익이거나 계속 보유가 손해일 때 그보다 나으면 행사한다. 둘 다 행사하려 하면 "
                      + ("풋 권리자" if _pfx else "콜 권리자") + " 우선 (가정 우선권)."),
                     f"{S2} · 다음 열 {S3} · {S4}")
        fill(W, _dec)
        _D = lambda L, r_: f"{Q(S5)}!{L}{R0+r_}"
        W = newsheet(S3, "③ 풋가치트리 (같은 주식 물량 · 풋 권리자가 받는 몫)",
                     ("⑤ 가 풋이면 풋 행사금액, 콜이면 콜 행사금액에 주식이 넘어간다. 그 거래에서 풋 권리자가 얻는 몫 "
                      "MAX(거래가격 − 보통주 가치, 0) 이 풋이다 — 풋 의무자의 신용위험이 붙으므로 계속 보유는 **풋 선도할인율**(8행)로 "
                      "할인한다." if _same else
                      "⑤ 가 풋이면 풋 행사금액 − 보통주 가치(풋 의무자와의 거래), 콜이면 풋이 소멸해 0, 보유면 다음 열을 **풋 "
                      "선도할인율**(8행)로 할인한다.") + "  적격상장 노드에서는 0 이다.",
                     f"{S5} · {S2} · 다음 열 {S3}")

        def _pv(i, r_, L, Lp, Ln):
            e, d = _EQ(L, r_), _D(L, r_)
            hold = "0" if i == n else _cont(S3, L, r_, Ln, 8)
            if _same:
                body = f'IF({d}="풋",MAX({L}$6-{e},0),IF({d}="콜",MAX({L}$7-{e},0),{hold}))'
            else:
                body = f'IF({d}="풋",{L}$6-{e},IF({d}="콜",0,{hold}))'
            return V(round(R["P"][i][i-r_], 6), f"=IF({_QI(L, r_)},0,{body})")
        fill(W, _pv)
        W = newsheet(S4, "④ 콜가치트리 (같은 주식 물량 · 상대방이 받는 몫)",
                     ("같은 거래에서 상대방이 얻는 몫 MAX(보통주 가치 − 거래가격, 0) 이 콜이다 — 주식을 받는 쪽이라 계속 "
                      "보유는 **무위험**(9행)으로 할인한다. 풋 − 콜 = 거래가격 − 보통주 가치(풋 권리자의 순손익)." if _same else
                      "⑤ 가 콜이면 보통주 가치 − 콜 행사금액, 풋이면 콜이 소멸해 0, 보유면 다음 열을 **무위험**(9행)으로 할인한다.")
                     + "  적격상장 시 소멸 여부는 가정에서 고른다.",
                     f"{S5} · {S2} · 다음 열 {S4}")

        def _cv(i, r_, L, Lp, Ln):
            e, d = _EQ(L, r_), _D(L, r_)
            hold = "0" if i == n else _cont(S4, L, r_, Ln, 9)
            if _same:
                body = f'IF({d}="풋",MAX({e}-{L}$6,0),IF({d}="콜",MAX({e}-{L}$7,0),{hold}))'
            else:
                body = f'IF({d}="풋",0,IF({d}="콜",{e}-{L}$7,{hold}))'
            return V(round(R["C"][i][i-r_], 6), f"=IF(AND({_QI(L, r_)},{K_['qkill']}=1),0,{body})")
        fill(W, _cv)
        # ── 같은 주식에 붙지 않은 나머지 물량 — 상대 권리 없이 따로 ──
        if any(b["kind"] == "put" for b in _blocks):
            _indep(S3B, "put", "③b 풋가치트리 (풋만 있는 물량)",
                   "콜이 붙지 않은 풋 물량이다. 지금 행사와 계속 보유 중 큰 쪽 — 계속 보유는 **풋 선도할인율**(8행).", _RI)
        if any(b["kind"] == "call" for b in _blocks):
            _indep(S4B, "call", "④b 콜가치트리 (콜만 있는 물량)",
                   "풋이 붙지 않은 콜 물량이다. 지금 행사와 계속 보유 중 큰 쪽 — 계속 보유는 **무위험**(9행).", _RI)
        # 읽는 차례 — 03 풋 · 04 콜 다음에 05 행사 판단 (만든 차례는 판단이 먼저다)
        wb.move_sheet(S5, offset=wb.sheetnames.index(S4) - wb.sheetnames.index(S5))

    # ── 결과 ──
    RS = sheet(SR, widths=[42, 14, 14, 14, 18, 44], tab=RPT["green"])
    head(RS, 2, "평가결과" + (f" — {name}" if pre else ""),
         ("모든 값이 앞의 트리에서 수식으로 넘어온다." if formula else
          "앱이 계산한 값을 그대로 담았다."), span=6)
    _g = R["gross"]
    _fp = _g["step"] if _g else 0
    _LF = gl(3+_fp)
    # 노드마다 EXP 를 곱하지 않고 합을 한 번에 — 엑셀 한 칸 수식 한도(8,192자) 때문이다
    _dfx = (f"EXP(-SUM({Q(S3)}!{gl(3)}$8:{gl(2+_fp)}$8)*{K_['dt']})" if _fp > 0 else "1")
    sec(RS, 4, "1. 옵션가치", span=6)
    cols(RS, 5, ["항목", "100 기준", "1주당 (원)", "대상 주식수", "전액 (원)", "설명"],
         widths=[42, 14, 14, 14, 18, 44])
    _eqv = 100*tm.S0/tm.K0
    _side = int(getattr(tm, "sha_side", 0))
    kp = tm.K0/100.0
    _F = lambda v, q: v/100*tm.K0*q                     # 결과 시트와 같은 순서로 곱한다
    # ── 3. 물량별 평가 (아래) — 같은 주식 물량 · 풋만 · 콜만 · 따로. 1 의 풋·콜은 이 표의 합계다 ──
    _BR0 = 22                                            # 물량별 표 첫 줄
    _brow = []
    for b in _blocks:
        k = b["kind"]
        if k == "link":
            pfx, cfx = f"={Q(S3)}!C{R0}", f"={Q(S4)}!C{R0}"
            qpf, qcf = f"={K_['qov']}", f"={K_['qov']}"
        elif k == "put":
            pfx, cfx, qpf, qcf = f"={Q(S3B)}!C{R0}", "=0", f"={K_['qp']}-{K_['qov']}", "=0"
        elif k == "call":
            pfx, cfx, qpf, qcf = "=0", f"={Q(S4B)}!C{R0}", "=0", f"={K_['qc']}-{K_['qov']}"
        else:
            pfx, cfx, qpf, qcf = f"={Q(S3)}!C{R0}", f"={Q(S4)}!C{R0}", f"={K_['qp']}", f"={K_['qc']}"
        pv_ = b["R"]["put"] if b["qp"] > 0 else 0.0
        cv_ = b["R"]["call"] if b["qc"] > 0 else 0.0
        _brow.append((b["name"], pv_, pfx, cv_, cfx, b["qp"], qpf, b["qc"], qcf))
    _BT = _BR0 + len(_brow)                              # 합계 줄
    _put_k = sum(_F(x[1], x[5]) for x in _brow)
    _call_k = sum(_F(x[3], x[7]) for x in _brow)
    _qun = sha_contract_shares(tm)
    _items = [
        ("계약 대상 주식가치 (평가기준일)", _eqv, f"={Q(S2)}!C{R0}", _qun,
         (f"{K_['qp']}+{K_['qc']}-{K_['qov']}" if (R["has_put"] and R["has_call"]) else
          K_['qp'] if R["has_put"] else K_['qc'] if R["has_call"] else "0"),
         "풋·콜 대상 주식(같은 주식에 붙은 물량은 한 번)의 가치 — 평가 의뢰인이 가진 주식 전체가 아니다"),
        ("풋 — 주식 보유자가 상대에게 사 달라고 요구 (풋 1주당 · 물량별 합계)", (_put_k*100/tm.K0/qp if qp > 0 else 0.0),
         f"=IF(E7>0,G{_BT}/E7/{K_['K0']}*100,0)", qp, K_['qp'], "주식 보유자가 되팔 권리 — 아래 3 의 물량별 합계"),
        ("콜 — 상대가 주식 보유자에게 팔라고 요구 (콜 1주당 · 물량별 합계)", (_call_k*100/tm.K0/qc if qc > 0 else 0.0),
         f"=IF(E8>0,H{_BT}/E8/{K_['K0']}*100,0)", qc, K_['qc'], "상대방이 사 갈 권리 — 아래 3 의 물량별 합계. 콜이 없으면 0"),
    ]
    _wonv = [_F(_eqv, _qun), _put_k, _call_k]
    _wonf = ["=D6*E6", f"=G{_BT}", f"=H{_BT}"]
    for i, (nm, val, fx, qv, qfx, memo) in enumerate(_items):
        r_ = 6+i
        put(RS, r_, 2, nm, bold=True, border=True)
        put(RS, r_, 3, V(val, fx), fmt=N4, align="right", bold=True, border=True)
        put(RS, r_, 4, V(val/100*tm.K0, f"=C{r_}/100*{K_['K0']}"), fmt=N2, align="right", border=True)
        put(RS, r_, 5, V(qv, f"={qfx}"), fmt=N0, align="right", border=True)
        put(RS, r_, 6, V(_wonv[i], _wonf[i]), fmt=N0, align="right", border=True)
        put(RS, r_, 7, memo, size=9, color=RPT["grey"], wrap=True)
    # 100 기준 합은 싣지 않는다 — 지분·풋·콜의 주식수가 달라 100 기준 값끼리 더하면 뜻이 없다.
    put(RS, 9, 2, "계약 대상 주식 + 풋 − 콜 (주식 보유자 쪽 합, 원)", bold=True, border=True)
    put(RS, 9, 6, V(_wonv[0] + _put_k - _call_k, "=F6+F7-F8"), fmt=N0, align="right", border=True)
    put(RS, 9, 7, "참고 합계 (원 단위만 — 주식수가 달라 100 기준으로는 더하지 않는다). 하나의 금융상품 가치가 아니다",
        size=9, color=RPT["grey"], wrap=True)
    _net = (_call_k - _put_k) if _side == 0 else (_put_k - _call_k)
    put(RS, 10, 2, ("순액 — 콜 권리자 관점 (콜 − 풋)" if _side == 0 else "순액 — 풋 권리자 관점 (풋 − 콜)"),
        bold=True, border=True)
    put(RS, 10, 6, V(_net, "=F8-F7" if _side == 0 else "=F7-F8"), fmt=N0, align="right",
        bold=True, border=True)
    put(RS, 10, 7, ("같은 주식 물량은 풋 − 콜 = 거래가격 − 보통주 가치(풋 권리자의 순손익)로 이어진다. "
                    if R.get("linked") else "")
        + "두 권리를 각자 총액으로 싣는다 — 순액은 참고값", size=9, color=RPT["grey"], wrap=True)
    sec(RS, 20, "3. 물량별 평가 — 계약조건대로 나눈 물량", span=7)
    cols(RS, 21, ["물량", "풋 (100 기준)", "콜 (100 기준)", "풋 수량", "콜 수량", "풋 (원)", "콜 (원)"],
         widths=[42, 14, 14, 14, 18, 18, 18])
    for k, (nm, pv_, pfx, cv_, cfx, qpv, qpf, qcv, qcf) in enumerate(_brow):
        r_ = _BR0 + k
        put(RS, r_, 2, nm, bold=True, border=True)
        put(RS, r_, 3, V(pv_, pfx), fmt=N4, align="right", border=True)
        put(RS, r_, 4, V(cv_, cfx), fmt=N4, align="right", border=True)
        put(RS, r_, 5, V(qpv, qpf), fmt=N0, align="right", border=True)
        put(RS, r_, 6, V(qcv, qcf), fmt=N0, align="right", border=True)
        put(RS, r_, 7, V(_F(pv_, qpv), f"=C{r_}/100*{K_['K0']}*E{r_}"), fmt=N0, align="right", border=True)
        put(RS, r_, 8, V(_F(cv_, qcv), f"=D{r_}/100*{K_['K0']}*F{r_}"), fmt=N0, align="right", border=True)
    put(RS, _BT, 2, "합계", bold=True, fill=RPT["light"], border=True)
    for c_, col in ((5, "E"), (6, "F"), (7, "G"), (8, "H")):
        vals = {5: sum(x[5] for x in _brow), 6: sum(x[7] for x in _brow), 7: _put_k, 8: _call_k}
        put(RS, _BT, c_, V(vals[c_], f"=SUM({col}{_BR0}:{col}{_BT-1})"), fmt=N0, align="right",
            bold=True, fill=RPT["light"], border=True)
    note(RS, _BT+1, ("같은 주식에 붙은 물량은 ⑤ 행사 판단으로 함께 풀고(③·④), 나머지 물량은 상대 권리 없이 따로 잰다"
                     "(③b · ④b). 1 의 풋·콜(1주당 100 기준)은 이 합계를 그 권리의 주식수로 나눈 값이다."
                     if R.get("linked") else "두 권리를 따로 잰다 — 풋은 ③, 콜은 ④ 트리의 뿌리 값이다."), span=7)

    sec(RS, 11, "2. 발행회사가 풋 의무자일 때 — 1032 문단 23 총액 부채", span=6)
    _rows2 = [
        ("첫 행사 가능일 (스텝)", V(_fp, f"={Q(S3)}!{_LF}$2"), N0, None, "가장 이른 행사 가능 시점"),
        ("그날 행사금액", V(_g["strike"] if _g else 0.0, f"={Q(S3)}!{_LF}$6"), N4, None,
         "상환금액 (100 기준)"),
        ("할인계수", V(_g["df"] if _g else 1.0, f"={_dfx}"), N6, None,
         "격자와 같은 구간 선도이자율로 할인"),
        ("금융부채 (상환금액의 현재가치)", V(_g["pv"] if _g else 0.0, "=C13*C14"), N4,
         (_g["pv"] if _g else 0.0), "옵션 공정가치가 아니라 **총액**이다"),
        ("참고 — 풋옵션 공정가치", V(R["put"], "=C7"), N4, R["put"],
         "콜 권리자가 의무자면 이쪽을 쓴다"),
    ]
    for i, (nm, fx, fm, v100, memo) in enumerate(_rows2):
        r_ = 12+i
        put(RS, r_, 2, nm, bold=True, border=True)
        put(RS, r_, 3, fx, fmt=fm, align="right", bold=True, border=True)
        if v100 is not None:
            put(RS, r_, 6, V(v100/100*tm.K0*qp, f"=C{r_}/100*{K_['K0']}*{K_['qp']}"),
                fmt=N0, align="right", border=True)
        put(RS, r_, 7, memo, size=9, color=RPT["grey"], wrap=True)
    # q 범위·풋 하한·옵션 ≥ 0 확인은 앱이 평가할 때 한다(sha_integrity) — 조서에 싣지 않는다.
    note(RS, 18, "적격상장을 켜면 풋이 그 노드에서 소멸하므로 값이 뚝 떨어진다. "
         "최소 주가 기준이 계약의 「적격상장」 정의와 맞는지 반드시 확인하라.", span=6)
    q = Q(SR)
    return dict(sheet=SR, eq=f"{q}!$C$6", put=f"{q}!$C$7", call=f"{q}!$C$8", gpv=f"{q}!$C$15",
                eq_krw=f"{q}!$F$6", put_krw=f"{q}!$F$7", call_krw=f"{q}!$F$8", gpv_krw=f"{q}!$F$15",
                put_ps=f"{q}!$D$7", call_ps=f"{q}!$D$8", qp=f"{q}!$E$7", qc=f"{q}!$E$8",
                K0=K_['K0'], kck=K_['kck'], pyld=K_['pyld'], cprem=K_['cprem'])


def _sha_deal_block(wb, K, tm, R, formula, pre, name):
    """행사·매매가 확정되고 결제만 남은 회차의 결과 시트 — 새 선택권 없이 확정 이전·대금을 잰다.

    결과 표의 칸 자리(6~8행 · 15행)는 선택권 회차의 결과 시트와 같다 — 회차 합계·회계처리가 같은 주소를 본다.
    """
    put, head, sec, cols, note, sheet = (K[x] for x in ("put", "head", "sec", "cols", "note", "sheet"))
    N2, N0, P2, N4, N6 = '#,##0.00', '#,##0', '0.00%', '0.0000', '0.000000'
    DATE = 'yyyy-mm-dd'
    YEL = INPUT_FILL                          # 입력 칸 — 모든 조서가 같은 노란색
    V = lambda val, fx: (fx if formula else val)
    SR = pre + "결과"
    W = sheet(SR, widths=[40, 16, 14, 14, 18, 52])
    _side = R["side"]
    head(W, 2, "확정 거래 — " + name,
         ("행사·매매가 확정되고 결제만 남은 물량이다. 새로운 행사 선택권을 주지 않는다 — 결제일에 주식을 넘기고 확정 "
          "매매대금을 받는 거래(선도)를 잰다. 음수도 0 으로 자르지 않는다."), span=6)
    cols(W, 5, ["항목", "100 기준 (대금 100)", "1주당 (원)", "주식수", "전액 (원)", "설명"])
    kv = lambda r, nm, val, fmt=None, memo="", yellow=True: (
        put(W, r, 2, nm, bold=True, size=9.5, border=True),
        put(W, r, 3, val, fmt=fmt, size=9.5, align="right", border=True, fill=(YEL if (formula and yellow) else None)),
        memo and put(W, r, 4, memo, size=9, color=RPT["grey"], wrap=True))
    sec(W, 19, "확정 거래 조건", span=5)
    kv(20, "평가기준일 주가 (원)", tm.S0, N2)
    kv(21, "확정 주당 매매대금 (원)", tm.K0, N2, "계약·합의서의 확정 금액")
    kv(22, "거래", ("풋 행사 확정 — 주식 보유자가 상대에게 판다" if _side == "put" else
                   "콜 행사 확정 — 상대가 주식 보유자에게서 산다"), None, "구조 — 앱에서 바꾼다", yellow=False)
    kv(23, "평가기준일", dt.date.fromisoformat(tm.d_base), DATE, "", yellow=False)
    kv(24, "결제 예정일", dt.date.fromisoformat(R["settle"]), DATE)
    kv(25, "결제까지 연수 (실제 일수 ÷ 365)", V(R["T"], "=MAX(0,(C24-C23)/365)"), N6, "", yellow=False)
    kv(26, "무위험 현물이자율 (연속)", R["zr"], N6, "앱이 무위험 곡선에서 결제일까지 보간한 값", yellow=False)
    kv(27, "대금 할인 현물이자율 (연속)", R["zp"], N6, "풋 할인 기준과 같은 곡선 — 대금 지급 의무자의 신용과 맞는지 확인", yellow=False)
    kv(28, "보통주 배당수익률 (연, 연속)", tm.div_y, P2)
    kv(29, "대금 할인계수 (대금 할인 현물)", V(R["dfp"], "=EXP(-C27*C25)"), N6, "", yellow=False)
    kv(30, "대금 할인계수 (무위험)", V(R["dfr"], "=EXP(-C26*C25)"), N6, "", yellow=False)
    kv(31, "주식 현재가치 (100 기준)", V(R["share_pv"], "=100*C20/C21*EXP(-C28*C25)"), N6,
       "결제일에 넘길 주식의 오늘 가치", yellow=False)
    kv(32, "거래 물량 (주)", (tm.sha_put_q if _side == "put" else tm.sha_call_q), N0)
    qp, qc = sha_qty(tm)
    eq = 100*tm.S0/tm.K0
    rows_ = [
        (6, "계약 대상 주식가치 (평가기준일)", eq, "=100*C20/C21", "=C32", qp + qc,
         "결제 때까지 주식 보유자가 가진 주식"),
        (7, "풋 — 확정 거래 (주식 보유자가 받는 몫)", R["put"],
         ("=100*C29-C31" if _side == "put" else "=0"), ("=C32" if _side == "put" else "=0"), qp,
         "100 × 대금 할인계수 − 주식 현재가치"),
        (8, "콜 — 확정 거래 (상대가 받는 몫)", R["call"],
         ("=C31-100*C30" if _side == "call" else "=0"), ("=C32" if _side == "call" else "=0"), qc,
         "주식 현재가치 − 100 × 대금 할인계수"),
    ]
    for r_, nm, val, fx, qfx, qv, memo in rows_:
        put(W, r_, 2, nm, bold=True, border=True)
        put(W, r_, 3, V(val, fx), fmt=N4, align="right", bold=True, border=True)
        put(W, r_, 4, V(val/100*tm.K0, f"=C{r_}/100*$C$21"), fmt=N2, align="right", border=True)
        put(W, r_, 5, V(qv, qfx), fmt=N0, align="right", border=True)
        put(W, r_, 6, V(val/100*tm.K0*qv, f"=D{r_}*E{r_}"), fmt=N0, align="right", border=True)
        put(W, r_, 7, memo, size=9, color=RPT["grey"], wrap=True)
    put(W, 15, 2, "총액 부채 (해당 없음 — 확정 거래)", size=9, border=True)
    put(W, 15, 3, 0.0, fmt=N4, align="right", border=True)
    put(W, 15, 6, 0.0, fmt=N0, align="right", border=True)
    note(W, 10, SHA_NO_LAG.replace("행사일에", "결제 예정일에"), span=6)
    q = f"'{SR}'"
    return dict(sheet=SR, eq=f"{q}!$C$6", put=f"{q}!$C$7", call=f"{q}!$C$8", gpv=f"{q}!$C$15",
                eq_krw=f"{q}!$F$6", put_krw=f"{q}!$F$7", call_krw=f"{q}!$F$8", gpv_krw=f"{q}!$F$15",
                put_ps=f"{q}!$D$7", call_ps=f"{q}!$D$8", qp=f"{q}!$E$7", qc=f"{q}!$E$8",
                K0=f"{q}!$C$21", kck=f"{q}!$C$21", pyld=None, cprem=None)


def _sha_total(wb, K, tm, entries, refs, formula, recon=None):
    """회차 합계 — 각 회차 결과 시트의 금액을 수식으로 모아 더한다."""
    put, head, sec, cols, note, sheet = (K[x] for x in ("put", "head", "sec", "cols", "note", "sheet"))
    N0, N2, P2 = '#,##0', '#,##0.00', '0.00%'
    V = lambda val, fx: (fx if formula else val)
    T = sheet("회차 합계", widths=[26, 30, 13, 9, 12, 12, 12, 12, 16, 16, 16, 9, 22, 16, 16], tab=RPT["green"])
    head(T, 2, "회차 합계", "회차마다 따로 잰 풋·콜을 더한다. 연도별 미행사 물량은 다음 회차로 넘기지 않는다.",
         span=11)
    hdr = ["회차", "행사기간", "주당 기준가격", "가산율", "풋 수량", "콜 수량", "풋 1주당", "콜 1주당",
           "풋 전액 (원)", "콜 전액 (원)", "순액 (원)"]
    _side = int(getattr(tm, "sha_side", 0))
    hdr[-1] = "순액 (원) = 콜 − 풋" if _side == 0 else "순액 (원) = 풋 − 콜"
    # 반영(1/0) — 추가 조건부 물량을 조건 충족 가정으로 평가에 넣었는가. 조건 충족 시 금액은 차이 분석으로 남긴다.
    hdr += ["반영 (1/0)", "평가 대상 상태", "조건 충족 시 풋 (원)", "조건 충족 시 콜 (원)"]
    cols(T, 4, hdr)
    r = 5
    for e, f in zip(entries, refs):
        t, R_ = e["tm"], e["R"]
        qp, qc = sha_qty(t)
        pk, ck = R_["put"]/100*t.K0, R_["call"]/100*t.K0
        put(T, r, 2, e["name"], border=True)
        put(T, r, 3, e.get("window", ""), border=True, size=9)
        put(T, r, 4, V(t.K0, f"={f['K0']}"), fmt=N2, align="right", border=True)
        put(T, r, 5, (V(t.sha_put_yield, f"={f['pyld']}") if f.get("pyld") else "—"), fmt=P2, align="right",
            border=True)
        _inc = float(e.get("incl", 1.0))
        put(T, r, 13, _inc, fmt=N0, align="center", border=True,
            fill=(INPUT_FILL if (formula and e.get("status") == "cond") else None))
        put(T, r, 14, SHA_ROW_STATUS.get(e.get("status", "open"), "") + (
            " · " + SHA_COND_BASIS.get(e.get("cond_basis"), "") if e.get("status") == "cond" else ""), size=9, border=True)
        put(T, r, 15, V(pk*qp, f"={f['put_krw']}"), fmt=N0, align="right", border=True)
        put(T, r, 16, V(ck*qc, f"={f['call_krw']}"), fmt=N0, align="right", border=True)
        put(T, r, 6, V(qp, f"={f['qp']}"), fmt=N0, align="right", border=True)
        put(T, r, 7, V(qc, f"={f['qc']}"), fmt=N0, align="right", border=True)
        put(T, r, 8, V(pk, f"={f['put_ps']}"), fmt=N2, align="right", border=True)
        put(T, r, 9, V(ck, f"={f['call_ps']}"), fmt=N2, align="right", border=True)
        put(T, r, 10, V(_inc*pk*qp, f"=M{r}*O{r}"), fmt=N0, align="right", border=True)
        put(T, r, 11, V(_inc*ck*qc, f"=M{r}*P{r}"), fmt=N0, align="right", border=True)
        put(T, r, 12, V(_inc*((ck*qc - pk*qp) if _side == 0 else (pk*qp - ck*qc)),
                        f"=K{r}-J{r}" if _side == 0 else f"=J{r}-K{r}"),
            fmt=N0, align="right", border=True)
        r += 1
    first, last = 5, r-1
    put(T, r, 2, "합계", bold=True, border=True)
    for c_, L in ((6, "F"), (7, "G"), (10, "J"), (11, "K"), (12, "L")):
        vals = [T.cell(rr, c_).value for rr in range(first, last+1)]
        tot = sum(v for v in vals if isinstance(v, (int, float))) if not formula else None
        put(T, r, c_, V(tot, f"=SUM({L}{first}:{L}{last})"), fmt=N0, align="right", bold=True, border=True)
    tr = r
    note(T, r+2, ("순액은 콜 권리자 관점(콜 − 풋)" if _side == 0 else "순액은 풋 권리자 관점(풋 − 콜)")
         + "이다. 두 권리는 보유자가 달라 각자 총액으로 싣는다 — 순액은 참고값이다. «반영» 이 0 인 회차는 추가 조건을 "
           "충족하지 않는다고 가정한 물량이다 — 조건 충족 시 금액(O·P 열)은 차이 분석일 뿐 평가금액에 들어 있지 않다.",
         span=15)
    # 회계처리가 쓰는 성분 합계(원) — 지분·풋·콜·총액 부채
    put(T, r+4, 2, "회계 참고표 성분 (원)", bold=True, size=9.5)
    comp = {}
    for k_, (key, nm) in enumerate((("eq_krw", "계약 대상 주식 공정가치 (보유 주식 전체 아님)"), ("put_krw", "풋옵션"),
                                    ("call_krw", "콜옵션"), ("gpv_krw", "풋 행사금액 현재가치 (총액 부채)"))):
        rr = r+5+k_
        put(T, rr, 2, nm, border=True, size=9)
        val = sum(float(e.get("incl", 1.0))*sha_components_krw(e["tm"], e["R"])[key[:-4]] for e in entries)
        put(T, rr, 3, V(val, "=" + "+".join(f"$M${5+i}*{f[key]}" for i, f in enumerate(refs))), fmt=N0,
            align="right", border=True)
        comp[key[:-4]] = f"'회차 합계'!$C${rr}"
    if recon:
        # 평가 대상 수량과 제외 수량의 대사 — 결제 완료 물량도 여기에 남는다
        r0 = r + 11
        put(T, r0, 2, "수량 대사 — 평가 대상과 제외 물량", bold=True, size=9.5)
        cols(T, r0+1, ["회차", "평가 대상 상태", "풋 주식수", "콜 주식수", "계약 대상 주식", "평가 반영", "같은 주식 묶음"])
        for i, row in enumerate(recon):
            for c_, v in enumerate(row):
                put(T, r0+2+i, 2+c_, v, fmt=(N0 if isinstance(v, float) else None), size=9, border=True,
                    align=("right" if isinstance(v, float) else None))
        _h = float(getattr(tm, "sha_hold_q", -1.0))
        if _h >= 0:
            rr = r0 + 2 + len(recon)
            put(T, rr, 2, "평가기준일 보유주식", bold=True, size=9, border=True)
            put(T, rr, 6, _h, fmt=N0, align="right", size=9, border=True)
            put(T, rr+1, 2, "이 계약 밖 보유주식 (보유 − 계약 대상 합)", bold=True, size=9, border=True)
            put(T, rr+1, 6, V(_h - sum(x[4] for x in recon), f"=F{rr}-SUM(F{r0+2}:F{r0+1+len(recon)})"), fmt=N0,
                align="right", size=9, border=True)
    return dict(row=tr, comp=comp)


def _sha_accounting(wb, K, tm, R, entries, refs, tot, formula):
    """회계처리 — 세 당사자. 수식 조서는 결과(회차 합계) 칸을 가리키는 식이다."""
    put, head, sec, cols, note, sheet = (K[x] for x in ("put", "head", "sec", "cols", "note", "sheet"))
    N4, N0 = '0.0000', '#,##0'
    multi = tot is not None
    AC = sheet("회계처리", widths=[50, 16, 20, 56], tab=RPT["red"])
    head(AC, 2, "회계처리 — 세 관점",
         "같은 계약인데 실리는 것이 완전히 다르다. 누가 풋 의무자인지가 가른다."
         + (" 수식 조서에서는 결과 시트를 가리키는 식이라 주가·변동성을 바꾸면 함께 움직인다."
            if formula else ""), span=4)
    has_call = any(e["R"].get("has_call") for e in entries)
    g0 = entries[0]["R"].get("gross") if len(entries) == 1 else None
    lines = sha_account_lines(entries[0]["tm"] if len(entries) == 1 else tm, entries[0]["R"],
                              has_call=has_call, gross=(g0 if not multi else False))
    if multi:
        vals_k = {k: sum(sha_entry_comp_krw(e)[k] for e in entries)
                  for k in ("eq", "put", "call", "gpv")}
        refs_k = tot["comp"]
        vals_1, refs_1 = None, None
    else:
        t0, R0_ = entries[0]["tm"], entries[0]["R"]
        vals_1, vals_k = sha_components(t0, R0_), sha_components_krw(t0, R0_)
        f = refs[0]
        refs_1 = {k: f[k] for k in ("eq", "put", "call", "gpv")}
        refs_k = {k: f[k + "_krw"] for k in ("eq", "put", "call", "gpv")}
    r = 4
    for who in SHA_PARTIES:
        rows, memo = lines[who]
        sec(AC, r, who, span=4); r += 1
        cols(AC, r, ["항목", "100 기준" if not multi else "", "전액 (원)", ""], widths=[50, 16, 20, 56])
        r += 1
        for nm, comb in rows:
            put(AC, r, 2, nm, bold=nm.startswith("순액"), border=True)
            if not multi:
                if nm.startswith("순액") and not sha_same_qty(entries[0]["tm"]):
                    put(AC, r, 3, "— 주식수가 달라 원 단위로만", size=8, color=RPT["grey"], align="right", border=True)
                else:
                    put(AC, r, 3, (sha_formula(comb, refs_1) if formula else sha_eval(comb, vals_1)),
                        fmt=N4, align="right", border=True)
            put(AC, r, 4, (sha_formula(comb, refs_k) if formula else sha_eval(comb, vals_k)),
                fmt=N0, align="right", border=True)
            r += 1
        note(AC, r, memo, span=4); r += 2
    if multi:
        note(AC, r, "회차가 여럿이라 100 기준 칸은 비운다 — 회차마다 기준가격이 다르다. 총액 부채(발행회사)는 "
             "회차마다 첫 행사 가능일의 행사금액을 할인해 더한 값이다.", span=4)


# ══════════════════════════════════════════════════════════
# 6. 화면
# ══════════════════════════════════════════════════════════

# Legacy UI and regression tools retain the same public function names.
__all__ = [name for name in globals() if not name.startswith("__")]
