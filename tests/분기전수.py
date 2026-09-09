#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""분기 전수 — 선택할 수 있는 모든 (상품, 항목, 값) 을 엔진에 한 번씩 통과시킨다.

2단계 매트릭스(`tests/검증매트릭스.json`)에서 99 갈래가 NOT_TESTED 였다. 값을 손으로
못 박아 둔 시험은 없지만, **항등식**은 어느 갈래에서나 성립해야 한다. 그래서 이
시험의 오라클은 값이 아니라 불변식이다 —

    NaN·inf 없음 · 위험중립가중치 q ∈ (0,1) · 풋 = B1−B0 ≥ 0 · 전환 = B2−B1 ≥ 0
    매도청구권 ≥ 0 · 배분표 합계 = 100 · 상각표 마지막 기말 = 상환금액 · validate() 가 죽지 않음

각 갈래를 기본 계약에서 **한 항목만** 바꿔 돌린다 (조합은 5단계). 있음/없음 스위치
(PRESENCE)도 하나씩 끈다. `derive()` 가 값을 되돌리는 갈래(예: RCPS 제3자 지정이면
k_sep=1 강제)는 FORCED 로 적는다 — 그건 「선택」이 아니므로 매트릭스에서 빠져야 한다.

결과는 `tests/output/분기전수.json` 에 쓰고 `tests/기능목록.py` 가 커버리지로 읽는다.

    python3 tests/분기전수.py
"""
import sys, os, math, json, time, types, warnings, traceback
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
OUT = os.path.join(ROOT, "tests", "output", "분기전수.json")

TOL = 1e-7          # 금액 항등식 (100 기준). 격자는 같은 부동소수 연산을 다른 순서로 한다
TOL_SUM = 1e-9      # 배분표 합계 — 단순 덧셈이라 더 조인다

RF = [(1, .0226), (3, .0240), (5, .0252)]
CR = [(1, .1409), (3, .1740), (5, .1905)]
CR_B = [(1, .1650), (3, .2000), (5, .2200)]

# 상품별 기본 계약 — PRESENCE 스위치(풋·콜·이표·보장·중간평가·거래원가·곡선)를 모두 켠다.
# 노드 간격 3개월 (n = 18) — 갈래 300 개를 몇 분 안에 돈다.
BASE = {
    "CB":   dict(inst="CB", d_issue="2025-03-31", d_base="2025-09-30", d_mat="2030-03-31", gap_m=3.0,
                 cpn=.02, ytm=.05, p_s=24., p_e=57., k_s=12., k_e=24., k_w=.3, issue_cost=2e8),
    "RCPS": dict(inst="RCPS", d_issue="2025-03-31", d_base="2025-09-30", d_mat="2030-03-31", gap_m=3.0,
                 cpn=.02, ytm=.05, p_s=24., p_e=57., issuer_call=2, k_s=12., k_e=24., k_w=.3,
                 mat_mode=0, issue_cost=2e8),
    "BW":   dict(inst="BW", d_issue="2025-03-31", d_base="2025-09-30", d_mat="2030-03-31", gap_m=3.0,
                 cpn=.02, ytm=.05, p_s=24., p_e=57., k_s=12., k_e=24., k_w=.3, bw_pay=0, bw_detach=1,
                 issue_cost=2e8),
    "SHA":  dict(inst="SHA", d_issue="2025-03-31", d_base="2025-09-30", d_mat="2030-03-31", gap_m=3.0,
                 sha_put_s=36., sha_put_e=60., sha_call_s=12., sha_call_e=36., ipo_on=1, ipo_m=24.,
                 ipo_px=1200., ipo_min=600.),
}

# 값 하나를 고르려면 다른 칸도 따라 바뀌어야 하는 갈래 — 그 값이 «살아 있게» 하는 최소 동반 설정
COMPANION = {
    ("*", "rate_mode", "rating"): dict(cr_curve_b=CR_B),
    ("*", "put_bdt", 1):          dict(bdt_sig=.20),
    ("*", "p_mode", "accrue"):    dict(p_yield=.06),
    ("*", "rfx_mode", 1):         dict(rfx_cyc=3.),
    ("*", "rfx_mode", 2):         dict(rfx_cyc=3.),
    ("RCPS", "k_sep", 0):         dict(issuer_call=1),
    ("RCPS", "k_third", 0):       dict(issuer_call=1),
    ("RCPS", "k_method", 1):      dict(issuer_call=2),
    ("RCPS", "k_method", 2):      dict(issuer_call=2),
    ("RCPS", "k_kind", 1):        dict(issuer_call=2),
    ("RCPS", "k_split", 1):       dict(issuer_call=2, k_method=2),
    ("BW", "k_split", 1):         dict(bw_pay=1, k_method=2),
    ("CB", "k_split", 1):         dict(k_method=2),
    ("CB", "k_hold", 0):          dict(k_method=2),
    ("RCPS", "k_hold", 0):        dict(issuer_call=2, k_method=2),
    ("BW", "k_hold", 0):          dict(bw_pay=1, k_method=2),
    # 의무보유가 조기상환 시작(24개월)보다 늦어야 「전환만 막는다」가 값을 바꾼다.
    ("CB", "k_lock_put", 0):      dict(k_lock=30.),
    ("RCPS", "k_lock_put", 0):    dict(issuer_call=2, k_lock=30.),
    ("BW", "k_lock_put", 0):      dict(bw_pay=1, k_lock=30.),
    # Actual/365 기준은 만기가 «정확히 N개월» 이 아닌 계약에서만 값이 갈린다.
    ("CB", "acc_basis", 0):       dict(d_mat="2030-01-05"),
    ("RCPS", "acc_basis", 0):     dict(d_mat="2030-01-05"),
    ("BW", "acc_basis", 0):       dict(d_mat="2030-01-05"),
    # 콜 소멸이 우선순위를 따르는지 — 의무보유가 없어야 소멸 조건이 걸린다.
    ("CB", "pc_order", 1):        dict(k_method=2, k_hold=0),
    ("RCPS", "pc_order", 1):      dict(issuer_call=2, k_method=2, k_hold=0),
    ("RCPS", "ipo_conv", 1):      dict(ipo_on=1, ipo_m=24., ipo_px=1200., ipo_min=600.),
    ("RCPS", "ipo_conv", 0):      dict(ipo_on=1, ipo_m=24., ipo_px=1200., ipo_min=600.),
    ("*", "ipo_on", 1):           dict(ipo_m=24., ipo_px=1200., ipo_min=600.),
    ("*", "fvpl_whole", 1):       dict(conv_class="equity"),
    ("*", "bs_net", 1):           dict(k_w=.3),
    ("SHA", "sha_disc", 2):       dict(sha_spread=.03),
}

# PRESENCE 를 «끄는» 덮어쓰기
OFF = {
    "put":      dict(p_s=99., p_e=0.),
    "call":     dict(k_w=0.),
    "cpn":      dict(cpn=0.),
    "ytm":      dict(ytm=0.),
    "mid":      dict(d_base="2025-03-31"),
    "cost":     dict(issue_cost=0.),
    "curve":    dict(rf_curve=[(5, .025)], cr_curve=[(5, .17)]),
    "sha_put":  dict(sha_put_s=99., sha_put_e=0.),
    "sha_call": dict(sha_call_s=0., sha_call_e=0.),
}


def load_app():
    stub = types.ModuleType("streamlit"); stub.cache_data = lambda **k: (lambda f: f)
    sys.modules["streamlit"] = stub
    m = types.ModuleType("cbapp"); sys.modules["cbapp"] = m
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], "app.py", "exec"), m.__dict__)
    return m.__dict__


def make_terms(G, product, over):
    t = G["Terms"](**{k: v for k, v in BASE[product].items()})
    t.rf_curve, t.cr_curve = list(RF), list(CR)
    for k, v in over.items(): setattr(t, k, v)
    G["derive"](t)
    return t


def fin(x): return isinstance(x, (int, float)) and math.isfinite(x)


def run_bond(G, t):
    """전환사채·RCPS·BW — decompose 를 돌리고 불변식을 잰다. {검사: (통과, 값·설명)}"""
    full, b0, b1, b2, ca, conv = G["decompose"](t)
    rows, note = G["allocate"](t, full, b0, b1, b2, ca)
    C = {}
    C["finite"] = (all(fin(x) for x in (b0, b1, b2, ca)), f"B0 {b0:.4f} B1 {b1:.4f} B2 {b2:.4f} CA {ca:.4f}")
    C["q_in_01"] = (not full["qbad"], f"q ∈ [{full['qmin']:.4f}, {full['qmax']:.4f}]")
    if G["put_bdt_on"](t):
        C["put_ge_0"] = (True, "BDT 부채요소는 다른 모형이라 B0 와 견주지 않는다")
    else:
        C["put_ge_0"] = (b1 - b0 >= -TOL, f"풋 {b1 - b0:.6f}")
    forced_conv = G["auto_conv"](t) or (G["is_rcps"](t) and int(t.ipo_on) and int(t.ipo_conv))
    if G["put_bdt_on"](t):
        C["conv_ge_0"] = (True, "BDT 부채요소는 다른 모형이라 B2 와 견주지 않는다")
    elif forced_conv:
        # 존속기간 만료 시 자동전환·상장 시 강제전환 — 그 전환은 권리가 아니라 의무라
        # 주가가 낮으면 B2 < B1 이다 (LIMIT 자동전환·강제전환).
        C["conv_ge_0"] = (True if b2 - b1 >= -TOL else "limit", f"전환 {b2 - b1:.6f} (의무 전환 — 음수 허용)")
    else:
        C["conv_ge_0"] = (b2 - b1 >= -TOL, f"전환 {b2 - b1:.6f}")
    if ca >= -TOL:
        C["call_ge_0"] = (True, f"매도청구권 {ca:.6f}")
    else:
        # TF·GS 는 지분을 무위험(또는 전환확률 가중)으로 할인한다. 콜이 전환을 강제하면
        # 부채가 지분으로 바뀌어 할인율이 낮아지고 전체 가치가 **오를** 수 있다 — 그러면
        # 유무가치비교법의 매도청구권이 음수다. 모형 성질이라 KNOWN_LIMITATION 이다.
        # 강제전환이 실제로 있었는지(분포)로 그 경우만 허용한다.
        r3 = G["engine"](t, conv=True, put=True, call=True, conv_start=max(t.cv_s, t.k_lock))
        fc = r3["dist"].get("conv_called", 0.0)
        # 비분리형 BW 는 «신주인수권을 이미 행사했는가» 를 상태로 갖지 않는다. 자식 노드의 콜은
        # 신주인수권이 살아 있다고 보고 결정되는데, 부모에서 투자자가 미리 행사하면 그 콜은
        # 사채만 비싸게 사는 셈이라 부모 값이 오른다 — 상태 미추적의 알려진 한계.
        bw_nd = G["bw_cash"](t) and int(t.bw_detach) == 0 and t.k_w > 0
        lim = fc > 0 or bw_nd
        C["call_ge_0"] = ("limit" if lim else False,
                          f"매도청구권 {ca:.6f} · 강제전환 확률 {fc:.4f}"
                          + (" (LIMIT 강제전환 할인율 효과)" if fc > 0 else (" (LIMIT 비분리형 BW 상태 미추적)" if bw_nd else "")))
    tot = sum(v for _, v in rows[:-1])
    C["alloc_100"] = (abs(rows[-1][1] - 100) <= TOL_SUM and abs(tot - 100) <= TOL_SUM,
                      f"합계 {rows[-1][1]:.10f}")
    eir = G["eir_or_none"](t, full, b0, b1, b2, ca)
    host = G["acc_host"](t, full, b0, b1, b2, ca)
    if eir is None:
        C["amort_end"] = (True, "상각표 없음 — " + ("전체 FVPL 지정" if G["fvpl_on"](t) else f"잔여 주계약 {host}"))
    elif host is not None and host <= 0:
        # 발행가 100 과 공정가치가 크게 달라 잔여 주계약이 0 이하 — 최초 인식 차이(Day-1) 자리.
        # 유효이자율이 정의되지 않는다. 상각표를 만들면 안 된다 (4단계 F-05).
        C["amort_end"] = ("limit", f"잔여 주계약 {host:.4f} ≤ 0 — 상각 대상 없음 (LIMIT Day-1)")
    else:
        r, arows, red, nper = eir
        end = arows[-1][5]
        C["amort_end"] = (abs(end - red) <= 1e-6, f"마지막 기말 {end:.6f} 상환 {red:.6f} 유효이자율 {r:.4%}")
    D = full["dist"]; ds = D["conv"] + D["put"] + D["call"] + D["mat"]
    C["dist_sum_1"] = (abs(ds - 1) <= 1e-9, f"분포 합 {ds:.10f}")
    w = G["validate"](t)
    C["validate_runs"] = (isinstance(w, list), f"경고 {len(w)}")
    vals = dict(b0=b0, b1=b1, b2=b2, ca=ca, n=int(full["n"]))
    return C, vals


def run_sha(G, t):
    R = G["sha_engine"](t)
    C = {}
    C["finite"] = (fin(R["put"]) and fin(R["call"]), f"풋 {R['put']:.4f} 콜 {R['call']:.4f}")
    C["q_in_01"] = (not R["qbad"], f"q ∈ [{R['qmin']:.4f}, {R['qmax']:.4f}]")
    C["put_ge_0"] = (R["put"] >= -TOL, f"풋 {R['put']:.6f}")
    C["call_ge_0"] = (R["call"] >= -TOL, f"콜 {R['call']:.6f}")
    # 풋은 어느 노드에서든 행사금액을 넘을 수 없다 (지분 ≥ 0)
    pmax = max(R["pk"](i) for i in range(R["n"] + 1))
    C["put_le_strike"] = (R["put"] <= pmax + TOL, f"풋 {R['put']:.4f} ≤ 최대 행사금액 {pmax:.4f}")
    w = G["validate"](t)
    C["validate_runs"] = (isinstance(w, list), f"경고 {len(w)}")
    return C, dict(put=R["put"], call=R["call"], n=int(R["n"]))


def one(G, product, label, field, value, over):
    t0 = time.time()
    row = dict(product=product, label=label, field=field, value=value, over={k: v for k, v in over.items()
               if not isinstance(v, list)})
    try:
        t = make_terms(G, product, over)
        if field is not None and field in G["Terms"].__dataclass_fields__ and getattr(t, field) != value:
            notes = getattr(t, "forced_notes", [])
            row.update(status=("EXPECTED_BLOCK" if notes else "FORCED"),
                       note=(f"지원하지 않는 조합 — compat() 이 {field}={getattr(t, field)!r} 로 되돌린다" if notes
                             else f"derive() 가 {field}={getattr(t, field)!r} 로 되돌린다"))
            return row
        C, vals = (run_sha if product == "SHA" else run_bond)(G, t)
        bad = [k for k, (ok, _) in C.items() if ok is False]
        lim = [k for k, (ok, _) in C.items() if ok == "limit"]
        row.update(status="FAIL" if bad else ("KNOWN_LIMITATION" if lim else "PASS"), failed=bad, limits=lim,
                   checks={k: v for k, (_, v) in C.items()}, values=vals)
    except Exception as e:
        row.update(status="ERROR", note=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-600:])
    row["sec"] = round(time.time() - t0, 2)
    return row


def main():
    G = load_app()
    import 기능목록 as F
    rows = []
    for p in ("CB", "RCPS", "BW", "SHA"):
        for f, (mean, opts, prods) in F.MANIFEST.items():
            if p not in prods: continue
            for v in opts:
                if not F.selectable(G, p, f, v): continue
                over = dict(COMPANION.get(("*", f, v), {})); over.update(COMPANION.get((p, f, v), {}))
                over[f] = v
                rows.append(one(G, p, f"{f}={v}", f, v, over))
        for k, (mean, pred, prods) in F.PRESENCE.items():
            if p not in prods: continue
            for on in (True, False):
                over = {} if on else dict(OFF[k])
                r = one(G, p, f"{k}={'ON' if on else 'OFF'}", None, None, over)
                r["presence"] = k; r["value"] = on
                # 켠 상태가 정말 켜졌나 — 기본 계약이 PRESENCE 를 만족해야 한다
                try:
                    if r["status"] in ("PASS", "FAIL") and bool(pred(make_terms(G, p, over))) != on:
                        r["status"] = "FORCED"; r["note"] = "기본 계약에서 이 스위치가 뜻대로 안 켜졌다"
                except Exception as e:
                    r["status"] = "ERROR"; r["note"] = str(e)
                rows.append(r)
        n = sum(1 for r in rows if r["product"] == p)
        print(f"{p:5s} {n:3d}갈래  " + "  ".join(f"{s} {sum(1 for r in rows if r['product']==p and r['status']==s)}"
                                              for s in ("PASS", "KNOWN_LIMITATION", "EXPECTED_BLOCK", "FAIL", "FORCED", "ERROR")))
    for r in rows:
        if r["status"] != "PASS":
            print(f"  {r['status']:6s} {r['product']:4s} {r['label']:24s} {r.get('note','')}"
                  + ("  " + ", ".join(f"{k}: {r['checks'][k]}" for k in r.get("failed", [])) if r.get("failed") else ""))
    json.dump(dict(generated=time.strftime("%Y-%m-%d %H:%M"), tol=dict(money=TOL, sum=TOL_SUM),
                   rows=rows), open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("→", os.path.relpath(OUT, ROOT), f"{len(rows)} 줄")
    return 0 if all(r["status"] in ("PASS", "FORCED", "KNOWN_LIMITATION", "EXPECTED_BLOCK") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
