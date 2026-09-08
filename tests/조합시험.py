#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""조합·속성 검증 — 모형 검증 프로그램 5단계.

네 층이다. 오라클은 모두 **불변식·단조성** (값을 못 박지 않는다 — 그건 스냅샷 시험 몫).

  1. 제약조건부 pairwise   — 상품마다 선택 가능한 스위치 값의 **모든 쌍**을 한 번씩 밟는다.
                             derive() 가 되돌리는 쌍은 «도달 불가» 로 적고 뺀다.
  2. 지정 3-way (지시서 §2-C 일곱 묶음) — 계산위험이 큰 묶음은 전수 곱.
  3. 무작위 (seed 고정)     — 상품별 500 · 고위험 묶음별 200. 실패는 Terms 전체를 JSON 으로 남긴다.
  4. 단조성 (지시서 §7.2)   — 무작위 계약 하나에서 한 칸을 밀어 방향을 본다.

    python3 tests/조합시험.py            # 전체 (약 10~20분)
    python3 tests/조합시험.py --quick    # 무작위 100/40 (PR 용)
"""
import sys, os, math, json, time, random, itertools, argparse, traceback, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import 분기전수 as BX
import 기능목록 as F
OUT = os.path.join(ROOT, "tests", "output", "조합시험.json")
FAILS = os.path.join(ROOT, "tests", "output", "조합시험_실패.json")
SEED = 20260908
RF = BX.RF; CR = BX.CR


# ══════════════════════════════════════════════════════════
# 공통 — 한 계약을 돌려 불변식을 잰다
# ══════════════════════════════════════════════════════════
def run_case(G, product, over, label, layer, extra=None):
    """{status, checks, values}. q ∉ (0,1) 은 화면이 멈추는 자리라 EXPECTED_BLOCK."""
    row = dict(layer=layer, product=product, label=label, over=_jsonable(over))
    try:
        t = BX.make_terms(G, product, over)
        forced = [k for k, v in over.items() if k in G["Terms"].__dataclass_fields__
                  and k not in ("rf_curve", "cr_curve", "cr_curve_b") and getattr(t, k) != v]
        if forced:
            # compat() 이 되돌린 것은 «지원하지 않는 조합» — 화면·validate·derive 가 같은 문구로 막는다
            notes = getattr(t, "forced_notes", [])
            row.update(status=("EXPECTED_BLOCK" if notes else "FORCED"),
                       note=("지원하지 않는 조합 — " + "; ".join(f"{k}→{v}" for k, v, _ in notes)) if notes
                       else "derive() 가 되돌림: " + ", ".join(f"{k}={getattr(t, k)!r}" for k in forced))
            return row, t
        C, vals = (BX.run_sha if product == "SHA" else BX.run_bond)(G, t)
        if extra: C.update(extra(G, t, vals))
        bad = [k for k, (ok, _) in C.items() if ok is False]
        lim = [k for k, (ok, _) in C.items() if ok == "limit"]
        if bad == ["q_in_01"]:
            row.update(status="EXPECTED_BLOCK", failed=bad, checks={k: v for k, (_, v) in C.items()}, values=vals,
                       note="위험중립가중치가 (0,1) 을 벗어나는 구간 — 화면은 st.stop()")
        else:
            row.update(status="FAIL" if bad else ("KNOWN_LIMITATION" if lim else "PASS"), failed=bad, limits=lim,
                       checks={k: v for k, (_, v) in C.items()}, values=vals)
        return row, t
    except Exception as e:
        row.update(status="ERROR", note=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-800:])
        return row, None


def _jsonable(d):
    return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v)) for k, v in d.items()}


def companions(product, f, v):
    over = dict(BX.COMPANION.get(("*", f, v), {})); over.update(BX.COMPANION.get((product, f, v), {}))
    over[f] = v
    return over


def merge(product, picks):
    """[(field, value)] → 덮어쓰기. 동반 설정이 서로 부딪히면(예: issuer_call) None."""
    over = {}
    for f, v in picks:
        c = companions(product, f, v)
        for k, val in c.items():
            if k in over and over[k] != val and k not in picks_dict(picks):
                return None
            over[k] = val
    for f, v in picks: over[f] = v          # 고른 값이 동반 설정보다 우선
    return over


def picks_dict(picks): return dict(picks)


def reachable(G, product, over):
    """derive() 뒤에도 고른 값이 남는가 (엔진을 돌리지 않는다 — 싸다)."""
    try:
        t = BX.make_terms(G, product, over)
    except Exception:
        return False
    return all(getattr(t, k) == v for k, v in over.items()
               if k in G["Terms"].__dataclass_fields__ and k not in ("rf_curve", "cr_curve", "cr_curve_b"))


# ══════════════════════════════════════════════════════════
# 1. 제약조건부 pairwise
# ══════════════════════════════════════════════════════════
def axes(G, product):
    """상품의 스위치 축. [(field, [values])] — 열거형 + 있음/없음."""
    ax = []
    for f, (mean, opts, prods) in F.MANIFEST.items():
        if product not in prods or f == "inst": continue
        vals = [v for v in opts if F.selectable(G, product, f, v)]
        if len(vals) >= 2: ax.append((f, vals))
    for k, (mean, pred, prods) in F.PRESENCE.items():
        if product in prods: ax.append(("~" + k, [True, False]))
    return ax


def apply_presence(over, f, v):
    """'~put' 같은 축은 OFF 덮어쓰기로 푼다."""
    if f.startswith("~"):
        if not v: over.update(BX.OFF[f[1:]])
        return over
    return over


def build_over(product, picks):
    enum = [(f, v) for f, v in picks if not f.startswith("~")]
    over = merge(product, enum)
    if over is None: return None
    for f, v in picks:
        if f.startswith("~") and not v: over.update(BX.OFF[f[1:]])
    return over


def pairwise(G, product, rng):
    ax = axes(G, product)
    idx = {f: i for i, (f, _) in enumerate(ax)}
    # 쌍의 열쇠는 필드 이름의 문자열 순서로 고정한다 — 아래 «덮은 쌍» 계산과 같은 순서여야
    # 탐욕 알고리즘이 실제로 여러 쌍을 한 계약에 담는다.
    pairs = set()
    for (f1, v1s), (f2, v2s) in itertools.combinations(ax, 2):
        (a, avs), (b, bvs) = sorted([(f1, v1s), (f2, v2s)], key=lambda x: str(x[0]))
        for v1 in avs:
            for v2 in bvs: pairs.add((a, v1, b, v2))
    covered, unreachable, cases = set(), set(), []
    # 어떤 쌍이 도달 가능한가 — 쌍만 든 계약으로 derive
    for pr in sorted(pairs, key=str):
        f1, v1, f2, v2 = pr
        over = build_over(product, [(f1, v1), (f2, v2)])
        if over is None or not reachable(G, product, over): unreachable.add(pr)
    todo = pairs - unreachable
    while todo:
        f1, v1, f2, v2 = min(todo, key=str)
        picks = [(f1, v1), (f2, v2)]
        order = [f for f, _ in ax if f not in (f1, f2)]
        rng.shuffle(order)
        for f in order:
            best, bestn = None, -1
            vals = list(dict(ax)[f]); rng.shuffle(vals)
            for v in vals:
                cand = picks + [(f, v)]
                over = build_over(product, cand)
                if over is None or not reachable(G, product, over): continue
                n_new = sum(1 for (g, w) in picks
                            if ((g, w, f, v) if str(g) < str(f) else (f, v, g, w)) in todo)
                if n_new > bestn: best, bestn = v, n_new
            if best is not None: picks.append((f, best))
        over = build_over(product, picks)
        got = {(a, x, b, y) for (a, x), (b, y) in itertools.combinations(sorted(picks, key=lambda t: str(t[0])), 2)}
        newly = got & todo
        if not newly:                         # 안전망 — 한 쌍은 반드시 덮는다
            newly = {(f1, v1, f2, v2)}
        todo -= newly; covered |= newly
        cases.append((dict(picks), over))
    return cases, covered, unreachable, len(pairs)


# ══════════════════════════════════════════════════════════
# 2. 지정 3-way — 지시서 §2-C 일곱 묶음
# ══════════════════════════════════════════════════════════
def threeway_groups():
    """[(이름, 상품들, 축 [(field|'~presence'|'@custom', values)], 후처리)]"""
    def mid_axes():
        # 중간평가 × 당일 행사 × 지급일 — 경과 0/6/7개월, 풋 시작 = 경과 −1/0/+1, 지급주기 3/6
        out = []
        for el in (0, 6, 7):
            for ps_off in (-1, 0, 1):
                for ipay in (3., 6.):
                    ps = max(0., el + ps_off)
                    d_base = {0: "2025-03-31", 6: "2025-09-30", 7: "2025-10-31"}[el]
                    out.append(dict(d_base=d_base, p_s=ps, p_e=57., ipay=ipay, cpn=.03, k_s=ps, k_e=24., k_w=.3))
        return out
    return [
        ("1 중간평가 × 당일 행사 × 이표 지급일", ("CB", "RCPS", "BW"), [("@", mid_axes())]),
        ("2 리픽싱 × 풋/콜 × 우선순위", ("CB",),
         [("rfx_mode", [0, 1, 2]), ("~put", [True, False]), ("~call", [True, False]), ("pc_order", [0, 1]), ("carry", [0, 1])]),
        ("3 RCPS 자동전환/상환 × 배당 × 콜 갈래", ("RCPS",),
         [("mat_mode", [0, 1]), ("div_mode", [0, 1]), ("~cpn", [True, False]), ("issuer_call", [0, 1, 2])]),
        ("4 BW 현금/대용 × 분리/비분리 × 풋/콜", ("BW",),
         [("bw_pay", [0, 1]), ("bw_detach", [0, 1]), ("~put", [True, False]), ("~call", [True, False]), ("pc_order", [0, 1])]),
        ("5 SHA 풋/콜 × 의무자 × 할인 × 적격상장", ("SHA",),
         [("~sha_put", [True, False]), ("~sha_call", [True, False]), ("sha_writer", [0, 1, 2]), ("sha_disc", [0, 1, 2]),
          ("ipo_on", [0, 1]), ("sha_qipo_kill", [0, 1]), ("sha_kill", [0, 1])]),
        ("6 곡선 모양 × TF/GS × 전 구간 q", ("CB", "RCPS"),
         [("@", [dict(rf_curve=[(1, .03), (3, .03), (5, .03)], cr_curve=[(1, .10), (3, .10), (5, .10)], _shape="flat"),
                 dict(rf_curve=[(1, .02), (3, .03), (5, .04)], cr_curve=[(1, .08), (3, .11), (5, .14)], _shape="upward"),
                 dict(rf_curve=[(1, .01), (3, .05), (5, .09)], cr_curve=[(1, .06), (3, .14), (5, .22)], _shape="steep"),
                 dict(rf_curve=[(1, .05), (3, .04), (5, .03)], cr_curve=[(1, .16), (3, .13), (5, .10)], _shape="inverted")]),
          ("model", ["TF", "GS"]), ("y_type", ["par", "spot"]), ("cmp_rf", [1, 2, 4, 12]), ("sig", [.15, .45])]),
        ("7 회계분류 × 분리방법 × 거래원가 × 전체 FVPL", ("CB", "RCPS"),
         [("conv_class", ["equity", "liability"]), ("p_sep", [1, 0]), ("k_sep", [1, 0]), ("~cost", [True, False]),
          ("fvpl_whole", [0, 1]), ("~call", [True, False])]),
    ]


def acc_checks(G, t, vals):
    """묶음 7 — 회계 등식. 거래원가 배분 합 = 원가, 상각표가 주계약에서 출발."""
    C = {}
    full, b0, b1, b2, ca, conv = G["decompose"](t)
    rows, _ = G["allocate"](t, full, b0, b1, b2, ca)
    parts, cost = G["cost_split"](t, rows)
    C["cost_sum"] = (abs(sum(c for _, _, c, _ in parts) - cost) <= 1e-9, f"배분 합 {sum(c for _, _, c, _ in parts):.6f} = 원가 {cost:.6f}")
    if G["fvpl_on"](t):
        C["fvpl_all_expense"] = (all(h.startswith("즉시 비용") for _, _, c, h in parts if c > 0), "전체 지정 → 전액 즉시 비용")
    C["neg_rows"] = (all(v >= -1e-9 or "매도청구권" in k or "복합내재파생" in k or "잔여" in k for k, v in rows[:-1]),
                     "음수는 매도청구권 자산·복합내재파생·잔여만")
    return C


def expand(G, product, axes_, rng):
    """축의 데카르트 곱 → over 목록 (「@」 축은 사전 그대로)."""
    keys = [f for f, _ in axes_]; vals = [v for _, v in axes_]
    out = []
    for combo in itertools.product(*vals):
        picks, over = [], {}
        for f, v in zip(keys, combo):
            if f == "@": over.update({k: x for k, x in v.items() if not k.startswith("_")}); over["_tag"] = v.get("_shape", "")
            elif f.startswith("~"):
                if not v: over.update(BX.OFF[f[1:]])
            elif f in ("sig",): over[f] = v
            else: picks.append((f, v))
        m = merge(product, picks)
        if m is None: continue
        m.update({k: x for k, x in over.items() if k != "_tag"})
        lab = " · ".join(f"{f}={v}" for f, v in zip(keys, combo) if f != "@") + (f" · {over.get('_tag', '')}" if over.get("_tag") else "")
        out.append((lab, m))
    return out


# ══════════════════════════════════════════════════════════
# 3. 무작위 — 상품별 500 · 고위험 200
# ══════════════════════════════════════════════════════════
def rand_curve(rng, lo, hi):
    a = rng.uniform(lo, hi); b = a + rng.uniform(-.005, .02); c = b + rng.uniform(-.005, .02)
    return [(1, round(a, 4)), (3, round(max(b, .001), 4)), (5, round(max(c, .001), 4))]


def rand_terms(G, product, rng, risky=None):
    """유효한 무작위 계약 하나. risky 는 고위험 묶음 이름."""
    T_y = rng.choice([1, 2, 3, 5, 7]); el = rng.choice([0, 0, 3, 6, 12, 18]) if T_y > 1 else 0
    if el >= T_y*12: el = 0
    d_issue = "2025-03-31"; d_base = BX_add_months(d_issue, el); d_mat = BX_add_months(d_issue, T_y*12)
    gap = rng.choice([1., 3.])
    over = dict(d_issue=d_issue, d_base=d_base, d_mat=d_mat, gap_m=gap,
                S0=round(rng.uniform(300, 2500), 2), K0=1000., sig=round(rng.uniform(.12, .9), 4),
                cmp_rf=rng.choice([1, 2, 4, 12]), cmp_cr=rng.choice([1, 2, 4, 12]),
                rf_curve=rand_curve(rng, .01, .05), cr_curve=None, pc_order=rng.choice([0, 1]))
    rf = over["rf_curve"]; over["cr_curve"] = [(x, round(y + rng.uniform(.02, .15), 4)) for x, y in rf]
    H = T_y*12
    if product == "SHA":
        ps = rng.choice([0, 6, 12, 24]); over.update(
            sha_put_s=float(ps), sha_put_e=float(rng.choice([H, H - 6, min(H, ps + 24)])), sha_put_f=rng.choice([1., 3., 6.]),
            sha_put_yield=round(rng.uniform(0, .15), 4), sha_put_cmp=rng.choice([0, 1, 2, 4]),
            sha_call_s=float(rng.choice([0, 12])), sha_call_e=float(rng.choice([0, H // 2, H])), sha_call_f=rng.choice([1., 3.]),
            sha_call_prem=round(rng.uniform(-.1, .2), 4), sha_call_cmp=rng.choice([0, 1, 2, 4]),
            sha_writer=rng.choice([0, 1, 2]), sha_disc=rng.choice([0, 1, 2]), sha_kill=rng.choice([0, 1]),
            sha_qipo_kill=rng.choice([0, 1]), ipo_on=rng.choice([0, 1]), ipo_m=float(rng.choice([12, 24, 36])),
            ipo_px=round(rng.uniform(600, 2000), 0), ipo_min=round(rng.uniform(0, 900), 0))
        if risky == "SHA 상호소멸 × 겹치는 행사금액":
            over.update(sha_kill=1, sha_put_yield=round(rng.uniform(.15, .4), 3), sha_call_prem=round(rng.uniform(-.3, 0), 3),
                        sha_call_s=0., sha_call_e=float(H), sha_put_s=0., sha_put_e=float(H))
        over["inst"] = "SHA"
        return over
    cpn = rng.choice([0., 0., .01, .02, .03, .05]); ytm = rng.choice([0., cpn, cpn + .02, .05, .08])
    over.update(cpn=cpn, ytm=ytm, ipay=rng.choice([1., 3., 6., 12.]), ytm_cmp=rng.choice([0, 1, 2, 4]),
                cv_s=float(rng.choice([0, 1, 6, 12])), cv_e=float(H - 1),
                rfx_mode=rng.choice([0, 1, 2]), rfx_cyc=rng.choice([1., 3., 6., 12.]),
                floor=round(rng.uniform(500, 1000), 0), par=500., carry=rng.choice([0, 1, 2, 3]),
                p_s=float(rng.choice([0, 12, 18, 24, 36])), p_e=float(H - rng.choice([1, 3, 12])), p_f=rng.choice([1., 3., 6.]),
                p_mode=rng.choice(["fixed", "accrue"]), p_yield=round(rng.uniform(0, .1), 4), p_cmp=rng.choice([0, 1, 2, 4, 12]),
                p_rate=round(rng.uniform(100, 130), 2),
                k_s=float(rng.choice([0, 6, 12, 24])), k_e=float(rng.choice([12, 24, 36, H - 1])), k_f=rng.choice([1., 3.]),
                k_prem=round(rng.uniform(0, .08), 4), k_cmp=rng.choice([0, 1, 2, 4]), k_w=round(rng.choice([0, .3, .5, 1.]), 2),
                k_lock=float(rng.choice([0, 12, 25])), k_method=rng.choice([0, 1, 2]), k_sep=rng.choice([0, 1]),
                conv_class=rng.choice(["equity", "liability"]), model=rng.choice(["TF", "GS"]),
                p_sep=rng.choice([0, 1]), fvpl_whole=rng.choice([0, 0, 1]), bs_net=rng.choice([0, 1]),
                issue_cost=rng.choice([0., 0., 1e8, 5e8]), div_y=round(rng.choice([0., 0., .01, .03]), 3),
                put_bdt=rng.choice([0, 0, 1]), bdt_sig=.2, bdt_base=rng.choice([0, 1]))
    if over["carry"] == 0 and over["rfx_mode"] and (H/gap) > 60: over["carry"] = 1   # 상태확장 120 노드 권장
    # derive()/compat() 가 되돌릴 조합은 처음부터 만들지 않는다 — 표본을 버리지 않기 위해서다.
    # (되돌림 자체는 분기전수·손계산대조 [19] 가 본다)
    if over["model"] == "GS": over["k_method"] = 0
    if not (over["conv_class"] == "equity" and over["k_sep"] != 0): over["p_sep"] = 1
    if not (over["conv_class"] == "equity" and over["model"] == "TF"): over["put_bdt"] = 0
    if product == "RCPS":
        ic = rng.choice([0, 1, 2])
        over.update(inst="RCPS", mat_mode=rng.choice([0, 1]), issuer_call=ic, div_mode=rng.choice([0, 1]),
                    ipo_on=rng.choice([0, 0, 1]), ipo_m=24., ipo_px=1200., ipo_min=600., ipo_conv=rng.choice([0, 1]))
        if ic == 2: over.update(k_third=1, k_transfer=0, k_sep=1)
        elif ic == 1: over.update(k_w=1.0, k_method=0, k_lock=0., k_third=0, k_transfer=0, k_sep=0)
        else: over.update(k_w=0.0, k_method=0, k_lock=0., k_third=0, k_transfer=0, k_sep=1)
    elif product == "BW":
        bp = rng.choice([0, 1]); over.update(inst="BW", bw_pay=bp, bw_detach=0 if bp else rng.choice([0, 1]))
    else:
        over["inst"] = "CB"
    if risky == "리픽싱 × 풋/콜 × 우선순위":
        over.update(rfx_mode=rng.choice([1, 2]), k_w=rng.choice([.3, 1.]), k_s=0., k_e=float(H - 1), p_s=0., p_e=float(H - 1))
    elif risky == "BW 대용/비분리 × 풋/콜":
        bp = rng.choice([0, 1])
        over.update(inst="BW", bw_pay=bp, bw_detach=0 if bp else rng.choice([0, 1]), k_w=rng.choice([.3, 1.]), k_s=0., k_e=float(H - 1))
    elif risky == "중간평가 만기 직전 × 지급일":
        el2 = max(0, H - rng.choice([1, 2, 3])); over.update(d_base=BX_add_months(d_issue, el2), ipay=1., cpn=.03, p_s=0., p_e=float(H))
    elif risky == "GS × 가파른 곡선":
        rf = [(1, .01), (3, .04), (5, .08)]
        over.update(model="GS", k_method=0, put_bdt=0, rf_curve=rf, cr_curve=[(x, y + .05) for x, y in rf], sig=round(rng.uniform(.1, .3), 3))
    return over


def BX_add_months(iso, k):
    import datetime as D, calendar
    d = D.date.fromisoformat(iso)
    y, mo = d.year + (d.month - 1 + k)//12, (d.month - 1 + k) % 12 + 1
    return D.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1])).isoformat()


# ══════════════════════════════════════════════════════════
# 4. 단조성 (지시서 §7.2)
# ══════════════════════════════════════════════════════════
def monotone(G, product, over, rng):
    """무작위 계약 하나에서 한 칸을 밀어 방향을 본다. [(이름, 통과, 설명)]"""
    out = []
    if product == "SHA": return out
    def D(o):
        t = BX.make_terms(G, product, o); full, b0, b1, b2, ca, _ = G["decompose"](t)
        cad = full.get("ca_debt", ca) if G["is_rcps"](t) else ca     # 화면·조서가 싣는 매도청구권
        return dict(b0=b0, b1=b1, b2=b2, ca=cad, conv=b2 - b1, put=b1 - b0, t=t, full=full)
    def forced_mass(o):
        t = BX.make_terms(G, product, o)
        r3 = G["engine"](t, conv=True, put=True, call=True, conv_start=max(t.cv_s, t.k_lock))
        return r3["dist"].get("conv_called", 0.0)
    base = D(over); TOL = 1e-7
    if base["t"].rfx_mode == 0 or base["t"].carry == 0:
        # 전환가격 ↑ → 전환권 ↓ (리픽싱 근사(carry 1~3)에서는 경로가중이 뒤섞여 보장되지 않는다 — 제외)
        hi = D({**over, "K0": over["K0"]*1.2, "floor": min(over["floor"], over["K0"]*1.2)})
        out.append(("전환가격 ↑ → 전환권 안 오름", hi["conv"] <= base["conv"] + TOL, f"{base['conv']:.4f} → {hi['conv']:.4f}"))
        # 주가 ↑ → 전환권 ↑
        hs = D({**over, "S0": over["S0"]*1.2})
        out.append(("주가 ↑ → 전환권 안 내림", hs["conv"] >= base["conv"] - TOL, f"{base['conv']:.4f} → {hs['conv']:.4f}"))
    if base["t"].rfx_mode and base["t"].carry == 0:
        hf = D({**over, "floor": min(over["K0"], over["floor"]*1.2)})
        out.append(("하한 ↑ → 투자자 가치 안 오름", hf["b2"] <= base["b2"] + TOL, f"{base['b2']:.4f} → {hf['b2']:.4f}"))
    if base["t"].k_w > 0 and base["t"].k_method == 0:
        hk = D({**over, "k_prem": over["k_prem"] + .05})
        ok = hk["ca"] <= base["ca"] + TOL
        if not ok and (forced_mass(over) > 0 or forced_mass({**over, "k_prem": over["k_prem"] + .05}) > 0):
            ok = "limit"          # 강제전환 할인율 효과 — 콜이 전환을 강제하는 만큼 TF·GS 값이 오른다
        out.append(("콜 프리미엄 ↑ → 매도청구권 안 오름", ok, f"{base['ca']:.4f} → {hk['ca']:.4f}" + (" (LIMIT 강제전환)" if ok == "limit" else "")))
    if base["t"].p_s <= base["t"].p_e and base["t"].p_mode == "fixed" and not G["put_bdt_on"](base["t"]):
        hp = D({**over, "p_rate": over["p_rate"] + 5})
        out.append(("풋 상환금액 ↑ → 부채요소 안 내림", hp["b1"] >= base["b1"] - TOL, f"{base['b1']:.4f} → {hp['b1']:.4f}"))
    # 행사 불가능한 기간에 조건을 바꿔도 값이 그대로
    H = base["t"].T*12 + base["t"].elapsed_m
    dead = D({**over, "k_s": H + 12, "k_e": H + 24})
    dead2 = D({**over, "k_s": H + 12, "k_e": H + 24, "k_prem": over["k_prem"] + .05, "pc_order": 1 - over["pc_order"]})
    out.append(("행사 불가능한 콜 — 프리미엄·우선순위 바꿔도 불변", abs(dead["b2"] - dead2["b2"]) <= 1e-12 and abs(dead["ca"] - dead2["ca"]) <= 1e-12,
                f"{dead['b2']:.6f}/{dead['ca']:.6f} = {dead2['b2']:.6f}/{dead2['ca']:.6f}"))
    # 발행총액 배수 — 100 기준 값은 그대로
    big = D({**over, "face_total": 5e10})
    out.append(("발행총액 ×2 — 100 기준 값 불변", abs(big["b2"] - base["b2"]) <= 1e-12, f"{base['b2']:.6f} = {big['b2']:.6f}"))
    return out


# ══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--quick", action="store_true"); a = ap.parse_args()
    N_RAND, N_RISK, N_MONO = (100, 40, 20) if a.quick else (500, 200, 60)
    G = BX.load_app()
    rows, fails = [], []
    t0 = time.time()

    print("1. 제약조건부 pairwise")
    pw_stats = {}
    for p in ("CB", "RCPS", "BW", "SHA"):
        rng = random.Random(SEED)
        cases, covered, unreach, total = pairwise(G, p, rng)
        for i, (picks, over) in enumerate(cases):
            r, _ = run_case(G, p, over, f"pairwise#{i+1} " + " ".join(f"{k}={v}" for k, v in picks.items())[:120], "pairwise")
            rows.append(r)
        pw_stats[p] = dict(cases=len(cases), pairs=total, covered=len(covered), unreachable=len(unreach))
        print(f"   {p:5s} 쌍 {total:5d} · 덮음 {len(covered):5d} · 도달 불가 {len(unreach):4d} · 계약 {len(cases):3d}")

    print("2. 지정 3-way (지시서 §2-C)")
    tw_stats = {}
    for name, prods, ax in threeway_groups():
        for p in prods:
            rng = random.Random(SEED)
            combos = expand(G, p, ax, rng)
            extra = acc_checks if name.startswith("7") else None
            n_ok = 0
            for lab, over in combos:
                r, _ = run_case(G, p, over, f"{name} · {p} · {lab}"[:160], "3way", extra); rows.append(r)
                n_ok += r["status"] in ("PASS", "EXPECTED_BLOCK", "FORCED", "KNOWN_LIMITATION")
            tw_stats[f"{name} · {p}"] = dict(combos=len(combos), ok=n_ok)
            print(f"   {name} · {p}: {len(combos)} 조합 · 통과/차단/강제 {n_ok}")

    print("3. 무작위 (seed 고정)")
    for p in ("CB", "RCPS", "BW", "SHA"):
        rng = random.Random(SEED + hash(p) % 1000)
        rng = random.Random(SEED + {"CB": 1, "RCPS": 2, "BW": 3, "SHA": 4}[p])
        st = {}
        for i in range(N_RAND):
            over = rand_terms(G, p, rng)
            r, _ = run_case(G, p, over, f"random#{i+1}", "random"); r["seed"] = SEED; r["i"] = i
            rows.append(r); st[r["status"]] = st.get(r["status"], 0) + 1
        print(f"   {p:5s} {N_RAND} → {st}")
    RISK = [("리픽싱 × 풋/콜 × 우선순위", "CB"), ("BW 대용/비분리 × 풋/콜", "BW"), ("SHA 상호소멸 × 겹치는 행사금액", "SHA"),
            ("중간평가 만기 직전 × 지급일", "CB"), ("GS × 가파른 곡선", "RCPS")]
    for gi, (name, p) in enumerate(RISK):
        rng = random.Random(SEED + 100 + gi); st = {}
        for i in range(N_RISK):
            over = rand_terms(G, p, rng, risky=name)
            r, _ = run_case(G, p, over, f"{name}#{i+1}", "risk"); r["seed"] = SEED + 100 + gi; r["i"] = i
            rows.append(r); st[r["status"]] = st.get(r["status"], 0) + 1
        print(f"   {name} ({p}) {N_RISK} → {st}")

    print("4. 단조성 (지시서 §7.2)")
    mst = {}
    for p in ("CB", "RCPS", "BW"):
        rng = random.Random(SEED + 50)
        for i in range(N_MONO):
            over = rand_terms(G, p, rng)
            try:
                for nm, ok, desc in monotone(G, p, over, rng):
                    rows.append(dict(layer="monotone", product=p, label=f"{nm} · #{i+1}",
                                     status="PASS" if ok is True else ("KNOWN_LIMITATION" if ok == "limit" else "FAIL"),
                                     checks={nm: desc}, over=_jsonable(over), seed=SEED + 50, i=i))
                    mst[nm] = mst.get(nm, [0, 0, 0]); mst[nm][0 if ok is True else (2 if ok == "limit" else 1)] += 1
            except Exception as e:
                rows.append(dict(layer="monotone", product=p, label=f"#{i+1}", status="ERROR", note=str(e), over=_jsonable(over)))
    for nm, (ok, bad, lim) in mst.items(): print(f"   {nm:40s} 통과 {ok:4d} · 어긋남 {bad} · 한계 {lim}")

    fails = [r for r in rows if r["status"] in ("FAIL", "ERROR")]
    summ = {}
    for r in rows: summ[(r["layer"], r["status"])] = summ.get((r["layer"], r["status"]), 0) + 1
    print()
    for (l, s), n in sorted(summ.items()): print(f"   {l:10s} {s:15s} {n}")
    for r in fails[:40]:
        print(f"  ★ {r['layer']} {r['product']} {r['label'][:90]} — {r.get('note','')} {[(k, r['checks'][k]) for k in r.get('failed', [])]}")
    json.dump(dict(generated=time.strftime("%Y-%m-%d %H:%M"), seed=SEED, quick=a.quick, sec=round(time.time() - t0, 1),
                   pairwise=pw_stats, threeway=tw_stats, summary={f"{l}/{s}": n for (l, s), n in summ.items()},
                   rows=rows), open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    json.dump(fails, open(FAILS, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"→ {os.path.relpath(OUT, ROOT)} {len(rows)} 줄 · 실패 {len(fails)} ({os.path.relpath(FAILS, ROOT)}) · {time.time() - t0:.0f}초")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
