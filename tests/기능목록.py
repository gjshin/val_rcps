#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""지원 분기 명세와 커버리지 장부 — 모형 검증 프로그램 2단계.

세 가지를 한다.

1. **분기 명세** — `Terms` 의 열거형 필드마다 「어떤 값이 무슨 뜻이고 어느 상품에
   열리는가」를 적은 MANIFEST. 사람이 쓴다. 코드가 아니라 계약을 기준으로 적는다.

2. **누락 감시** — `Terms` 의 열거형 필드와 화면(`app.py`)의 선택칸을 기계로 뽑아
   MANIFEST 와 대조한다. 코드에 선택지가 생겼는데 명세에 없으면 **여기서 실패**한다.
   새 기능을 더하고 이 시험을 안 고치면 통과하지 못한다 — 그게 목적이다.

3. **커버리지 장부** — 기존 시험 파일들의 케이스를 실제로 `derive()` 까지 돌려
   각 케이스가 어떤 (상품, 필드, 값) 을 밟는지 기록한다. 그래서 「이 분기를 어느
   시험이 덮는가」가 사람의 기억이 아니라 **케이스에서 계산된 사실**이 된다.
   결과는 `tests/검증매트릭스.json` 이다.

    python3 tests/기능목록.py            # 매트릭스를 만들고 누락·미검증을 보고한다
    python3 tests/기능목록.py --strict   # NOT_TESTED 가 있으면 실패 (7단계 완료 기준)

상태값 — PASS · EXPECTED_BLOCK · KNOWN_LIMITATION · FAIL · NOT_TESTED.
"""
import sys, os, json, re, io, json, types, warnings, argparse, itertools, datetime
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "검증매트릭스.json")


def load_app():
    stub = types.ModuleType("streamlit"); stub.cache_data = lambda **k: (lambda f: f)
    sys.modules["streamlit"] = stub
    m = types.ModuleType("cbapp"); sys.modules["cbapp"] = m
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], "app.py", "exec"), m.__dict__)
    return m.__dict__, src


# ══════════════════════════════════════════════════════════
# 1. 분기 명세 — 사람이 계약을 보고 쓴다
# ══════════════════════════════════════════════════════════
# 필드 → (뜻, {값: 라벨}, 열리는 상품). 상품이 None 이면 넷 다.
# 값 라벨은 화면 문구가 아니라 **계약상 뜻**이다. 화면이 바뀌어도 여기는 안 바뀐다.
P_ALL = ("CB", "RCPS", "BW", "SHA")
P_BOND = ("CB", "RCPS", "BW")          # 사채·우선주 — 주주간계약은 격자가 다르다
MANIFEST = {
    # ── 상품 ──
    "inst":        ("상품", {"CB": "전환사채", "RCPS": "상환전환우선주",
                            "BW": "신주인수권부사채", "SHA": "주주간계약"}, P_ALL),
    # ── 모형·분류 ──
    "model":       ("신용위험 처리", {"TF": "지분·부채 분리 할인 (TF)",
                                   "GS": "전환확률 가중 할인 (GS)"}, P_BOND),
    "conv_class":  ("전환권 회계 분류", {"equity": "자본", "liability": "파생상품부채"}, P_BOND),
    "carry":       ("리픽싱 조정일 처리", {0: "상태확장 (정확)", 1: "경로가중 근사",
                                     2: "위험중립확률 가중 근사", 3: "특정노드 선택 근사"}, P_BOND),
    "rfx_mode":    ("리픽싱 방식", {0: "없음", 1: "하향만", 2: "하향+상향"}, P_BOND),
    # ── 투자자 풋 ──
    "p_mode":      ("조기상환 행사금액 산정", {"fixed": "고정 금액", "accrue": "보장수익률 복리 누적"}, P_BOND),
    "p_cmp":       ("조기상환 보장 복리 횟수", {0: "단리", 1: "연 1회", 2: "연 2회", 4: "연 4회", 12: "월"}, P_BOND),
    "p_sep":       ("조기상환권 회계", {1: "분리 · 파생상품부채", 0: "분리하지 않음 · 부채요소에 포함"}, P_BOND),
    "p_lost_int":  ("행사금액이 상실이자 보상 수준", {0: "아니다", 1: "그렇다 (B4.3.5(5)(나))"}, P_BOND),
    # ── 발행자 콜 ──
    "k_method":    ("매도청구권 평가방법", {0: "유무가치비교", 1: "옵션차익 · GS식 전환가중확률할인",
                                      2: "옵션차익 · TF식 지분-채권 분리할인"}, ("CB", "BW", "RCPS")),
    "k_sep":       ("매도청구권 회계", {1: "별도 금융상품", 0: "복합내재파생에 포함"}, ("CB", "BW", "RCPS")),
    "k_less_cpn":  ("매도청구금액 산식", {1: "보장수익률 복리 − 기 지급 이자·배당", 0: "순수 복리 (차감 없음)"}, ("CB", "BW", "RCPS")),
    "k_split":     ("옵션차익법 지분·채권 구분 기준", {0: "비례균등차감법 — 가치 구성비율", 1: "한공회 본문 4.3.3 — GS 전환확률"}, ("CB", "BW", "RCPS")),
    "k_hold":      ("콜 대상물량 의무보유", {1: "있음 — 의무보유 기간 동안 존속", 0: "없음 — 전환·조기상환으로 콜도 소멸"}, ("CB", "BW", "RCPS")),
    "k_lock_put":  ("의무보유가 조기상환청구도 막는가", {1: "막는다 — 계약 정의", 0: "전환만 막는다"}, ("CB", "BW", "RCPS")),
    "k_kind":      ("콜옵션 유형", {0: "제3자 지정 가능 (파생상품자산)", 1: "제3자 기특정 (주주간 분배)"}, ("CB", "BW", "RCPS")),
    "k_third":     ("제3자 지정 가능", {0: "발행회사만", 1: "제3자 지정 가능"}, ("CB", "BW", "RCPS")),
    "k_transfer":  ("사채와 독립 양도 가능", {0: "아니다", 1: "그렇다"}, ("CB", "BW", "RCPS")),
    "k_cmp":       ("매도청구 프리미엄 복리 횟수", {0: "단리", 1: "연 1회", 2: "연 2회", 4: "연 4회"}, ("CB", "BW", "RCPS")),
    "pc_order":    ("풋·콜 같은 노드 우선순위", {0: "투자자 풋 우선", 1: "발행자 콜 우선"}, P_ALL),
    # ── 만기·이자 ──
    "ytm_cmp":     ("만기보장수익률 복리 횟수", {0: "단리", 1: "연 1회", 2: "연 2회", 4: "연 4회"}, P_BOND),
    "cmp_rf":      ("무위험 복리 횟수", {1: "연 1회", 2: "연 2회", 4: "연 4회", 12: "월"}, P_ALL),
    "cmp_cr":      ("위험 복리 횟수", {1: "연 1회", 2: "연 2회", 4: "연 4회", 12: "월"}, P_ALL),
    "y_type":      ("금리 입력 유형", {"par": "만기수익률 (par)", "spot": "현물이자율"}, P_ALL),
    "rate_mode":   ("위험 곡선 출처", {"direct": "직접 입력", "pick": "등급 하나",
                                   "rating": "두 등급 보간"}, P_ALL),
    # ── BDT ──
    "put_bdt":     ("조기상환권 금리격자", {0: "확정 격자", 1: "BDT 금리격자"}, ("CB", "RCPS", "BW")),
    "bdt_base":    ("BDT 기준 곡선", {0: "위험 곡선 직접", 1: "무위험 + 확정 스프레드"}, ("CB", "RCPS", "BW")),
    # ── RCPS ──
    "mat_mode":    ("존속기간 만료 시", {0: "보통주 자동전환", 1: "상환"}, ("RCPS",)),
    "issuer_call": ("발행자 측 권리", {0: "없음", 1: "발행자 상환권", 2: "제3자 지정 매도청구권"}, ("RCPS",)),
    "div_mode":    ("우선배당 성격", {0: "미지급분 상환가액 가산 (부채)", 1: "발행자 재량 (제외)"}, ("RCPS",)),
    "ipo_on":      ("적격상장 조항", {0: "없음", 1: "격자에 넣음"}, ("RCPS", "SHA")),
    "ipo_conv":    ("상장 시 강제전환", {0: "리픽싱만", 1: "보통주로 강제전환"}, ("RCPS",)),
    # ── BW ──
    "bw_pay":      ("행사대금 납입", {0: "현금납입", 1: "사채 대용납입"}, ("BW",)),
    "bw_detach":   ("신주인수권 분리성", {0: "비분리형", 1: "분리형"}, ("BW",)),
    # ── 회계 ──
    "fvpl_whole":  ("복합계약 전체 FVPL 지정", {0: "지정하지 않음", 1: "지정"}, P_BOND),
    "bs_net":      ("역산 목표", {0: "본체 B2", 1: "매도청구권 차감 순액"}, P_BOND),
    # ── 주주간계약 ──
    "sha_writer":  ("풋 의무자", {0: "최대주주", 1: "발행회사", 2: "연대"}, ("SHA",)),
    "sha_disc":    ("풋 할인", {0: "무위험", 1: "위험 곡선", 2: "무위험 + 스프레드"}, ("SHA",)),
    "sha_qipo_kill": ("적격상장 시 콜", {0: "풋만 소멸", 1: "풋·콜 모두 소멸"}, ("SHA",)),
    "sha_kill":    ("한쪽 행사 시 상대 권리", {0: "존속 (독립)", 1: "소멸 (상호소멸)"}, ("SHA",)),
    "sha_put_cmp": ("풋 보장 복리", {0: "단리", 1: "연 1회", 2: "연 2회", 4: "연 4회"}, ("SHA",)),
    "sha_call_cmp": ("콜 가산 복리", {0: "단리", 1: "연 1회", 2: "연 2회", 4: "연 4회"}, ("SHA",)),
}

# 값이 아니라 **있고 없음**으로 갈리는 권리. 필드 하나로 못 잡아 술어로 적는다.
PRESENCE = {
    "put":  ("조기상환청구권", lambda t: t.p_s <= t.p_e, P_BOND),
    "call": ("매도청구권", lambda t: t.k_w > 0 and t.k_s <= t.k_e, ("CB", "BW", "RCPS")),
    "cpn":  ("표면이자·우선배당", lambda t: t.cpn > 0, P_BOND),
    "ytm":  ("만기보장수익률", lambda t: t.ytm > 0, P_BOND),
    "mid":  ("중간평가 (경과기간 > 0)", lambda t: t.elapsed_m > 0.01, P_ALL),
    "cost": ("거래원가", lambda t: t.issue_cost > 0, P_BOND),
    "curve": ("기간구조 곡선 (만기점 2개 이상)", lambda t: len(t.rf_curve) >= 2, P_ALL),
    "sha_put": ("주주간계약 풋", lambda t: t.sha_put_s <= t.sha_put_e, ("SHA",)),
    "sha_call": ("주주간계약 콜", lambda t: t.sha_call_e > 0 and t.sha_call_s <= t.sha_call_e, ("SHA",)),
}

# derive() 가 강제하는 호환 제약. 데카르트 곱에서 있을 수 없는 조합을 뺀다.
# (필드, 값) 이 이 상품에서 **선택 가능한가**. 강제되는 값은 「선택」이 아니다.
def selectable(G, product, field, value):
    # 상품 필드는 그 상품 값 하나만 「선택」이다 — CB 행에 RCPS 값이 있을 수 없다
    if field == "inst": return value == product
    if product == "SHA":
        forced = dict(mat_mode=1, issuer_call=0, div_mode=0, rfx_mode=0, k_method=0,
                      p_sep=1, k_sep=1, put_bdt=0, ipo_conv=0, carry=1)
        if field in forced: return forced[field] == value
    if product == "BW":
        forced = dict(mat_mode=1, issuer_call=0, div_mode=0)
        if field in forced: return forced[field] == value
    if product == "RCPS" and field == "k_transfer":
        # 세 콜 갈래(issuer_call 0·1·2) 모두 k_transfer=0 으로 되돌린다 — 「독립 양도」는
        # 우선주에 없다. 분기전수.py 가 FORCED 로 잡아 준 것 (사이드바 체크박스는 남아 있다)
        return value == 0
    if product == "RCPS" and field in ("k_sep", "k_third", "k_method"):
        # RCPS 콜 갈래(issuer_call)가 정한다 — 세 값 다 어느 갈래에선가 나온다
        return True
    if product == "CB" and field in ("mat_mode", "issuer_call", "div_mode", "ipo_conv",
                                     "bw_pay", "bw_detach"):
        return False
    return True


# 하드 차단(EXPECTED_BLOCK) — 앱이 계산하지 않고 멈추는 것.
BLOCKS = [
    ("σ ≤ 0", "변동성 0 은 격자가 서지 않는다 (u=d=1). 엔진 lattice_ud() 가 ValueError, 화면 st.stop()",
     "engine+ui", "tests/손계산대조.py::test_dividend_yield_and_zero_vol"),
    ("q ∉ [0,1] 인 구간", "선도이자율이 변동성에 비해 가파른 구간. 전 구간 qbad 를 재어 화면 st.stop(). 엔진은 qbad 목록을 돌려준다",
     "ui", "tests/손계산대조.py::test_all_step_risk_neutral_probabilities"),
    ("GS + 매도청구권 평가방법 1·2", "compat() 이 k_method=0 으로 되돌린다. 사이드바 잠금 캡션·validate 경고·derive 되돌림이 같은 문구 (COMPAT_GS_KMETHOD)",
     "engine+ui+validate", "tests/손계산대조.py::test_unsupported_combos_agree"),
    ("전환권 부채 + 조기상환권 미분리", "compat() 이 p_sep=1 로 되돌린다 (COMPAT_PSEP). 값은 전부터 같았고 이제 경고가 붙는다",
     "engine+ui+validate", "tests/손계산대조.py::test_unsupported_combos_agree"),
    ("GS 또는 전환권 부채 + BDT", "compat() 이 put_bdt=0 으로 되돌린다 (COMPAT_BDT). 값은 전부터 같았고 이제 경고가 붙는다",
     "engine+ui+validate", "tests/손계산대조.py::test_unsupported_combos_agree"),
]

# 계산은 허용하되 한계가 화면·문서·조서에 같은 뜻으로 표시되어야 하는 것.
# 표시 위치 셋은 6단계에서 검사한다. 여기서는 목록과 어디에 있어야 하는지만.
# 알려진 한계는 app.py 의 MODEL_LIMITS 가 원본이다 — build_matrix 가 G 에서 읽는다.
# 화면·조서는 그 표를 그대로 싣고, README 「한계」 에 제목이 있는지를 여기서 확인한다.
LIMITS = []


# ══════════════════════════════════════════════════════════
# 2. 누락 감시 — 코드에서 뽑아 명세와 대조
# ══════════════════════════════════════════════════════════
def terms_enum_fields(G):
    """Terms 필드 중 열거형으로 보이는 것 — int/str 이면서 부동소수 입력이 아닌 것."""
    T = G["Terms"]
    out = {}
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    body = src.split("class Terms")[1].split("\ndef ")[0]
    for f, fld in T.__dataclass_fields__.items():
        ty = fld.type if isinstance(fld.type, str) else getattr(fld.type, "__name__", str(fld.type))
        if ty not in ("int", "str"): continue
        # 문자열이라도 날짜·출처·등급 같은 자유 입력은 열거형이 아니다
        # 자유 문자열 — 갈래가 아니다 (날짜·등급 이름·주가 출처·종목코드)
        if f in ("d_issue", "d_base", "d_mat", "cr_src", "rt_a", "rt_b", "rt_tgt", "ticker", "s0_src",
                 "rvol_rating", "rvol_how", "k_basis"): continue
        # int 지만 개수·횟수인 것 (열거형이 아니다)
        if f in ("n", "cur_periods"): continue
        out[f] = ty
    return out


def ui_enum_fields(src):
    """app.py 의 `t.<field> = ... st.selectbox(...)` / checkbox 에서 필드를 뽑는다."""
    out = {}
    # 신용등급 칸(rt_a·rt_b·rt_tgt)은 어느 곡선을 보간할지 고르는 것이지 계산
    # 갈래가 아니다 — 갈래는 rate_mode 다. 등급 하나하나를 분기로 세지 않는다.
    NOT_BRANCH = {"rt_a", "rt_b", "rt_tgt"}
    for m in re.finditer(r"t\.([a-z_0-9]+)\s*=\s*(?:int\()?(?:1 if )?(?:\w+\.)?(selectbox|checkbox|radio)\(",
                         src):
        if m.group(1) in NOT_BRANCH: continue
        out.setdefault(m.group(1), m.group(2))
    return out


def check_manifest(G, src):
    """명세 누락을 찾는다. 하나라도 있으면 목록을 돌려준다."""
    missing = []
    te = terms_enum_fields(G)
    for f in te:
        if f not in MANIFEST:
            missing.append(f"Terms 열거형 필드 `{f}`({te[f]}) 가 MANIFEST 에 없다")
    ue = ui_enum_fields(src)
    for f in ue:
        if f not in MANIFEST and f not in ("ipo_on",):   # ipo_on 은 MANIFEST 에 있다
            if f in MANIFEST: continue
            missing.append(f"화면 선택칸 `t.{f}` ({ue[f]}) 가 MANIFEST 에 없다")
    for f in MANIFEST:
        if f not in G["Terms"].__dataclass_fields__:
            missing.append(f"MANIFEST 의 `{f}` 가 Terms 에 없다 — 명세가 오래됐다")
    return missing


# ══════════════════════════════════════════════════════════
# 3. 커버리지 — 기존 시험 케이스를 실제로 derive() 해서 밟는 분기를 센다
# ══════════════════════════════════════════════════════════
def _exec_cases(path):
    """시험 파일을 모듈처럼 실행해 CASES/BASE 를 가져온다 — 시험은 돌리지 않는다.

    `__name__` 을 "_probe" 로 주면 `if __name__ == "__main__"` 갈래가 잠겨 함수
    정의와 상수만 남는다. 앞부분만 잘라 쓰면 load_app 이 CASES 보다 앞에 있는
    파일에서 CASES 를 놓친다.
    """
    src = open(path, encoding="utf-8").read()
    ns = {"__file__": path, "__name__": "_probe"}
    try:
        exec(compile(src, path, "exec"), ns)
    except SystemExit:
        pass
    except Exception as e:
        return None, f"{os.path.basename(path)}: 케이스 정의를 읽지 못함 — {e}"
    if "CASES" not in ns:
        return None, f"{os.path.basename(path)}: CASES 가 없다"
    return ns, None


def _terms_from(G, base, over):
    t = G["Terms"](**{k: v for k, v in base.items() if k in G["Terms"].__dataclass_fields__})
    for k, v in over.items():
        if k.startswith("_"): continue
        if k in G["Terms"].__dataclass_fields__: setattr(t, k, v)
    if "_gap" in over: t.gap_m = over["_gap"]
    if not t.rf_curve: t.rf_curve = [(1, .0226), (3, .0240), (5, .0252)]
    if not t.cr_curve: t.cr_curve = [(1, .1409), (3, .1740), (5, .1905)]
    G["derive"](t)
    return t


def collect_coverage(G):
    """{(product, field, value): [(test_file, case_label, oracle)]} 와 PRESENCE 도."""
    cov, pres = {}, {}
    def hit(t, tf, label, oracle):
        p = t.inst
        for f in MANIFEST:
            v = getattr(t, f)
            if not selectable(G, p, f, v): continue
            cov.setdefault((p, f, v), []).append((tf, label, oracle))
        for k, (_, pred, prods) in PRESENCE.items():
            if p in prods:
                pres.setdefault((p, k, bool(pred(t))), []).append((tf, label, oracle))

    # 값 조서 · 수식 조서 · 설정 전수 — 오라클은 「엔진 = 조서」 (같이 틀리면 통과)
    ORACLE_SNAP = "engine_vs_workbook"
    tf = "tests/값조서대조.py"
    ns, err = _exec_cases(os.path.join(ROOT, tf))
    if ns:
        for cls, km, md, ks, bsg, over in ns["CASES"]:
            base = dict(conv_class=cls, k_method=km, model=md, k_sep=ks)
            if bsg is not None: base.update(put_bdt=1, bdt_sig=bsg)
            t = _terms_from(G, base, over)
            hit(t, tf, f"{cls}·{km}·{md}·{ks}" + (f"·bdt{bsg}" if bsg is not None else "")
                + ("·" + ",".join(f"{k}={v}" for k, v in over.items() if not isinstance(v, list))[:40] if over else ""),
                ORACLE_SNAP)
    for tf in ("tests/조서대조.py", "tests/설정전수대조.py"):
        ns, err = _exec_cases(os.path.join(ROOT, tf))
        if not ns: print("  !", err); continue
        for row in ns["CASES"]:
            label, over = row[0], row[1]
            t = _terms_from(G, {}, over)
            hit(t, tf, label, "engine_vs_formula_workbook" if "조서대조" in tf else "engine_vs_workbook_sweep")
    tf = "tests/주주간계약대조.py"
    ns, err = _exec_cases(os.path.join(ROOT, tf))
    if ns:
        for label, over in ns["CASES"]:
            t = _terms_from(G, ns["BASE"], over)
            hit(t, tf, label, "engine_vs_formula_workbook")

    # 손계산 — 오라클이 독립이다. 함수마다 어떤 분기를 밟는지 손으로 적는다.
    # (함수 안에서 Terms 를 여러 개 만들어 기계 추출이 어렵다)
    HAND = {
        "test_coupon_schedule_after_elapsed_months": [("CB", "mid", True), ("CB", "cpn", True)],
        "test_root_immediate_put": [("CB", "mid", True), ("CB", "put", True)],
        "test_all_step_risk_neutral_probabilities": [("CB", "curve", True)],
        "test_current_k_and_original_cap": [("CB", "rfx_mode", 2)],
        "test_refix_weighted_average_on_reset_date": [("CB", "carry", 1)],
        "test_bw_inherits_engine_fixes": [("BW", "bw_pay", 0), ("BW", "bw_pay", 1),
                                          ("BW", "bw_detach", 0), ("BW", "bw_detach", 1)],
        "test_sha_root_immediate_put": [("SHA", "mid", True), ("SHA", "sha_put", True)],
        "test_split_metric_independent_of_setting": [("CB", "p_sep", 1), ("CB", "p_sep", 0)],
        "test_put_separation_flows_to_accounting": [("CB", "p_sep", 1), ("CB", "p_sep", 0)],
        "test_bw_root_and_call_keep_warrant": [("BW", "bw_detach", 0), ("BW", "bw_detach", 1),
                                               ("BW", "call", True), ("BW", "put", True)],
        "test_sha_mutual_kill_probabilities_sum_to_one": [("SHA", "sha_kill", 0), ("SHA", "sha_kill", 1),
                                                          ("SHA", "sha_call", True)],
        "test_dividend_yield_and_zero_vol": [("CB", "inst", "CB")],
        "test_backsolve_net_target": [("CB", "bs_net", 0), ("CB", "bs_net", 1)],
        "test_fvpl_whole_flows_to_accounting": [("CB", "fvpl_whole", 1), ("CB", "fvpl_whole", 0),
                                                ("CB", "conv_class", "liability"), ("CB", "cost", True)],
        "test_decision_table": [("CB", "pc_order", 0), ("CB", "pc_order", 1)],
        "test_decision_matches_old_chains": [("CB", "pc_order", 0), ("CB", "pc_order", 1),
                                             ("BW", "pc_order", 0), ("BW", "pc_order", 1)],
        "test_maturity_layer_in_distribution": [("RCPS", "mat_mode", 0), ("RCPS", "put", True),
                                                ("CB", "pc_order", 1)],
        "test_sha_boundaries": [("SHA", "sha_kill", 1), ("SHA", "pc_order", 0), ("SHA", "pc_order", 1),
                                ("SHA", "sha_writer", 0), ("SHA", "sha_writer", 1), ("SHA", "sha_writer", 2),
                                ("SHA", "sha_call_cmp", 0), ("SHA", "sha_call", True)],
        "test_unsupported_combos_agree": [("CB", "model", "GS"), ("CB", "k_method", 0), ("CB", "p_sep", 1),
                                          ("CB", "put_bdt", 0), ("CB", "conv_class", "liability"), ("CB", "k_sep", 0)],
        "test_ipo_branch_keeps_probability_mass": [("RCPS", "ipo_on", 1), ("RCPS", "ipo_conv", 1),
                                                   ("RCPS", "ipo_conv", 0), ("RCPS", "carry", 0),
                                                   ("RCPS", "rfx_mode", 0)],
        # 날짜↔개월 변환과 종가 고르기 — 격자 값이 아니라 입력 경로의 시험. step_mapper 를 밟는다.
        "test_date_month_roundtrip": [("CB", "mid", True)],
        "test_pick_close": [("CB", "inst", "CB")],
        "test_acc_mode_fv_only": [("CB", "mid", True), ("SHA", "mid", True)],
        "test_call_split_text": [("CB", "k_split", 0), ("CB", "k_split", 1), ("RCPS", "k_split", 1), ("BW", "k_split", 1),
                                 ("CB", "k_method", 1), ("CB", "k_method", 2), ("CB", "k_kind", 0), ("CB", "k_kind", 1),
                                 ("RCPS", "k_kind", 1), ("BW", "k_kind", 1),
                                 ("CB", "k_hold", 0), ("CB", "k_hold", 1),
                                 ("RCPS", "k_hold", 0), ("BW", "k_hold", 0)],
        "test_call_strike_switch": [("CB", "k_less_cpn", 0), ("CB", "k_less_cpn", 1),
                                    ("RCPS", "k_less_cpn", 0), ("BW", "k_less_cpn", 0), ("CB", "call", True)],
        "test_eir_expected_maturity": [("CB", "p_sep", 0), ("CB", "p_sep", 1), ("CB", "k_sep", 1)],
        "test_bdt_review_gates": [("CB", "conv_class", "equity"), ("CB", "conv_class", "liability"),
                                  ("CB", "put_bdt", 1), ("CB", "put_bdt", 0), ("CB", "put", True), ("CB", "put", False)],
    }
    tf = "tests/손계산대조.py"
    src = open(os.path.join(ROOT, tf), encoding="utf-8").read()
    have = set(re.findall(r"^def (test_\w+)", src, re.M))
    for fn, tags in HAND.items():
        if fn not in have: continue
        for p, f, v in tags:
            key = (p, f, v)
            (pres if f in PRESENCE else cov).setdefault(key, []).append((tf, fn, "hand_calc_independent"))
    unlisted = sorted(have - set(HAND))

    # 독립 오라클 (tests/오라클.py) — 앞부분 식이 app.py 를 보지 않는다. 손으로 적은 태그.
    ORACLE = {
        "test_discounting": [("CB", "cpn", True), ("CB", "cpn", False), ("CB", "cmp_cr", 4), ("CB", "cmp_cr", 2),
                             ("CB", "cmp_rf", 2), ("CB", "put", False), ("CB", "call", False), ("CB", "curve", True)],
        "test_crr": [("CB", "cmp_rf", 2), ("CB", "curve", True)],
        "test_accrue": [("CB", "ytm_cmp", 0), ("CB", "ytm_cmp", 1), ("CB", "ytm_cmp", 2), ("CB", "ytm_cmp", 4),
                        ("CB", "p_cmp", 0), ("CB", "p_cmp", 1), ("CB", "p_cmp", 2), ("CB", "p_cmp", 4)],
        "test_node_rule": [("CB", "pc_order", 0), ("CB", "pc_order", 1)],
        "test_refix": [("CB", "rfx_mode", 1), ("CB", "rfx_mode", 2), ("CB", "carry", 1)],
        "test_one_step_tree": [("CB", "cmp_rf", 2), ("CB", "cmp_cr", 4), ("CB", "conv_class", "equity")],
        "test_bw_cash": [("BW", "bw_pay", 0), ("BW", "bw_detach", 1)],
        "test_sha_european": [("SHA", "sha_disc", 0), ("SHA", "sha_put_cmp", 1), ("SHA", "sha_call_cmp", 1),
                              ("SHA", "sha_put", True), ("SHA", "sha_call", True)],
        "test_date_boundaries": [("CB", "mid", True), ("CB", "mid", False)],
        "test_sequential_identities": [("CB", "carry", 0), ("CB", "k_sep", 1), ("CB", "model", "TF")],
    }
    tf = "tests/오라클.py"
    src = open(os.path.join(ROOT, tf), encoding="utf-8").read()
    have_o = set(re.findall(r"^def (test_\w+)", src, re.M))
    for fn, tags in ORACLE.items():
        if fn not in have_o: continue
        for p, f, v in tags:
            key = (p, f, v)
            (pres if f in PRESENCE else cov).setdefault(key, []).append((tf, fn, "hand_calc_independent"))
    unlisted += sorted(f"오라클::{x}" for x in have_o - set(ORACLE))

    # 분기 전수 — 오라클은 불변식 (NaN 없음 · q ∈ (0,1) · 권리값 ≥ 0 · 배분 합계 100 ·
    # 상각표 기말 = 상환금액 · 분포 합 1). 값을 못 박지는 않지만 모든 선택 갈래를 한 번씩 밟는다.
    tf = "tests/분기전수.py"
    bx = os.path.join(ROOT, "tests", "output", "분기전수.json")
    if os.path.exists(bx):
        for r in json.load(open(bx, encoding="utf-8"))["rows"]:
            if r["status"] != "PASS": continue
            if r.get("presence"):
                pres.setdefault((r["product"], r["presence"], bool(r["value"])), []).append(
                    (tf, r["label"], "invariants"))
            else:
                cov.setdefault((r["product"], r["field"], r["value"]), []).append(
                    (tf, r["label"], "invariants"))
    else:
        print("  ! tests/output/분기전수.json 이 없다 — python3 tests/분기전수.py 를 먼저 돌릴 것")
    return cov, pres, unlisted


# ══════════════════════════════════════════════════════════
# 4. 매트릭스
# ══════════════════════════════════════════════════════════
def build_matrix(G, cov, pres):
    rows = []
    def cid(p, f, v):
        return f"{p}-{f}-{str(v).upper()}".replace(".", "_")
    for f, (mean, opts, prods) in MANIFEST.items():
        for p in prods:
            for v, lab in opts.items():
                if not selectable(G, p, f, v): continue
                tests = cov.get((p, f, v), [])
                indep = any(o == "hand_calc_independent" for _, _, o in tests)
                rows.append(dict(
                    case_id=cid(p, f, v), product=p, branch=f, value=v, label=lab, meaning=mean,
                    kind="enum",
                    status="PASS" if tests else "NOT_TESTED",
                    oracle=("hand_calc_independent" if indep else
                            (tests[0][2] if tests else None)),
                    independent_oracle=indep,
                    covered_by=[f"{tf}::{lb}" for tf, lb, _ in tests][:12],
                    n_tests=len(tests)))
    for k, (mean, _, prods) in PRESENCE.items():
        for p in prods:
            for v in (True, False):
                tests = pres.get((p, k, v), [])
                indep = any(o == "hand_calc_independent" for _, _, o in tests)
                rows.append(dict(
                    case_id=cid(p, k, "ON" if v else "OFF"), product=p, branch=k, value=v,
                    label=("있음" if v else "없음"), meaning=mean, kind="presence",
                    status="PASS" if tests else "NOT_TESTED",
                    oracle=("hand_calc_independent" if indep else (tests[0][2] if tests else None)),
                    independent_oracle=indep,
                    covered_by=[f"{tf}::{lb}" for tf, lb, _ in tests][:12], n_tests=len(tests)))
    for i, (nm, why, where, tf) in enumerate(BLOCKS, 1):
        rows.append(dict(case_id=f"BLOCK-{i:02d}", product="ALL", branch=nm, value=None, label=why,
                         meaning="지원하지 않는 조합", kind="block",
                         status=("EXPECTED_BLOCK" if where != "ui-only" else "FAIL"),
                         oracle="block_behaviour", independent_oracle=False,
                         covered_by=([tf] if tf else []), n_tests=(1 if tf else 0),
                         note=("화면만 막고 엔진·validate 는 계산한다 — 세 경로가 같아야 한다 (지시서 §6.3)"
                               if where == "ui-only" else "")))
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    for i, (nm, why, where) in enumerate(G["MODEL_LIMITS"], 1):
        shown = nm in readme
        rows.append(dict(case_id=f"LIMIT-{i:02d}", product="ALL", branch=nm, value=None, label=why,
                         meaning="계산은 하되 한계를 표시", kind="limitation",
                         status="KNOWN_LIMITATION" if shown else "FAIL", oracle="display_consistency",
                         independent_oracle=False, covered_by=(["README.md 「한계」", "app.py MODEL_LIMITS → 화면 검산 탭 · 조서 99_모형검증"] if shown else []),
                         n_tests=(1 if shown else 0),
                         must_appear_in=list(where), display_checked=shown,
                         note=("" if shown else "README 「한계」 에 이 제목이 없다")))
    return rows


def summarize(rows):
    prods = ("CB", "RCPS", "BW", "SHA", "ALL")
    st = ("PASS", "EXPECTED_BLOCK", "KNOWN_LIMITATION", "FAIL", "NOT_TESTED")
    tab = {p: {s: 0 for s in st} for p in prods}
    for r in rows: tab[r["product"]][r["status"]] += 1
    return tab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="NOT_TESTED·FAIL 이 있으면 실패")
    a = ap.parse_args()
    G, src = load_app()

    print("1. 명세 누락 감시")
    miss = check_manifest(G, src)
    for m in miss: print("   ★", m)
    print("   " + ("누락 없음" if not miss else f"누락 {len(miss)}건"))

    print("\n2. 기존 시험 케이스에서 커버리지 계산")
    cov, pres, unlisted = collect_coverage(G)
    print(f"   열거형 분기 (상품,필드,값) 밟힌 조합 {len(cov)} · 권리 유무 조합 {len(pres)}")
    if unlisted:
        print(f"   ★ 손계산 시험 함수 중 태그가 없는 것 {len(unlisted)}: {', '.join(unlisted)}")

    print("\n3. 매트릭스")
    rows = build_matrix(G, cov, pres)
    tab = summarize(rows)
    print("   %-6s %6s %6s %8s %8s %6s %8s" % ("상품", "전체", "PASS", "BLOCK", "LIMIT", "FAIL", "미검증"))
    for p, d in tab.items():
        tot = sum(d.values())
        print("   %-6s %6d %6d %8d %8d %6d %8d" % (p, tot, d["PASS"], d["EXPECTED_BLOCK"],
                                                    d["KNOWN_LIMITATION"], d["FAIL"], d["NOT_TESTED"]))
    nt = [r for r in rows if r["status"] == "NOT_TESTED"]
    if nt:
        print(f"\n   미검증 분기 {len(nt)}:")
        for r in nt: print(f"     {r['case_id']:32s} {r['meaning']} = {r['label']}")
    fl = [r for r in rows if r["status"] == "FAIL"]
    if fl:
        print(f"\n   FAIL {len(fl)}:")
        for r in fl: print(f"     {r['case_id']:12s} {r['branch']} — {r.get('note','')}")
    indep = sum(1 for r in rows if r.get("independent_oracle"))
    print(f"\n   독립 오라클이 붙은 분기 {indep} / 열거형+권리 {sum(1 for r in rows if r['kind'] in ('enum','presence'))}")

    # 워크트리에서는 .git 이 파일이라 git 에게 묻는다 (실패해도 매트릭스는 만든다)
    try:
        import subprocess
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        head = ""
    out = dict(generated=datetime.datetime.now().isoformat(timespec="seconds"),
               head=head, manifest_missing=miss, unlisted_hand_tests=unlisted,
               summary=tab, cases=rows)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    # 조서 99_모형검증 시트가 같은 함수로 읽는다. 한때 «rows» 키를 읽어 KeyError 를 삼키고
    # 「매트릭스 파일이 없다」로 찍혔다 — 쓰는 쪽과 읽는 쪽을 여기서 맞춰 본다.
    _ms = G["matrix_summary"](OUT)
    if _ms is None or not any(p == "CB" for p, _ in _ms["rows"]):
        print("   ★ 조서가 매트릭스를 읽지 못한다 (matrix_summary)"); FAIL.append("matrix_summary")
    else:
        print(f"   조서 99_모형검증 매트릭스 요약 — {len(_ms['rows'])} 상품 · {_ms['n']} 행 (matrix_summary)")
    print(f"\n   → {os.path.relpath(OUT, ROOT)} ({len(rows)} 행)")

    bad = bool(miss) or bool(unlisted)
    if a.strict: bad = bad or bool(nt) or bool(fl)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
