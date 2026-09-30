#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""수식 조서의 머리 행이 가정 시트의 **올바른 항목**을 참조하는지 본다.

값이 맞는지는 `조서대조.py` 와 `설정전수대조.py` 가 본다. 이쪽은 배선만 본다.
기본값이 우연히 같으면(매도청구 주기 3개월 = 조기상환 주기 3개월) 값 대조는
통과하면서도 엉뚱한 항목을 참조하고 있을 수 있다. 그런 것을 잡는다.

각 트리 시트 머리 행의 수식에서 `가정!$C$n` 참조를 뽑아 가정 시트의 항목
이름으로 되돌린 뒤, 그 행이 참조해야 할 항목 집합과 맞춰본다.

또 값 조서·수식 조서·엔진 셋이 같은 행사 시점을 쓰는지 경과기간을 바꿔가며 본다.
엑셀을 풀지 않으므로 몇 초면 끝난다.

    python3 tests/배선대조.py
"""
import sys, os, re, io, types, warnings, datetime as dt
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 머리 행 -> 참조해야 하는 가정 항목 (키 이름)
# 행사일·지급일은 계약서의 날짜를 앱이 노드에 배정한 목록이다(00 격자 공통 20·27행, 9행).
# 그래서 Flag(조기상환)은 의무보유 시작(pst·pt30)만, Flag(매도청구)는 가정 칸을 보지 않는다.
WANT = {
    3:  {"cvs", "cve", "cv30", "auto", "n"},                # Flag(전환) — ⑮ 는 cv30
    4:  {"pst", "pt30"},                       # Flag(조기상환) — 행사일 목록 + 의무보유로 늦춰진 시작
    5:  set(),                                 # Flag(매도청구) — 행사일 목록만 본다
    6:  {"roff", "cyc"},                       # Flag(리픽싱)
    # 조기상환금액 — 계약 행사월(20행) → 경과연수(accb·elm·remm·T) → 할증률(pyld·cpn·pcmp)
    # → 기지급 공제(pless·ipaym) → 금액(pmode·prate) + 행사일 이자(pcadd). 기간 중 언제든지면
    # 행사월이 노드 개월(elm·remm·n)이다.
    7:  {"pyld", "cpn", "pcmp", "elm", "prate", "pmode", "accb", "remm", "T", "n",
         "pcadd", "ipaym", "pless"},
    8:  {"prem", "cpn", "kcmp", "elm", "kless", "accb", "remm", "T", "n",
         "kcadd", "ipaym"},                     # 매도청구금액 — kless 가 지급분 공제 방식을 고른다
    9:  {"cpn", "ipaym"},                      # 쿠폰 — 지급일은 목록, 금액은 표면이자율로
    10: {"n", "red"},                          # 만기상환
}
ROWNAME = {3: "Flag(전환)", 4: "Flag(조기상환)", 5: "Flag(매도청구)",
           6: "Flag(리픽싱)", 7: "조기상환금액", 8: "매도청구금액",
           9: "쿠폰", 10: "만기상환"}


def load_app():
    stub = types.ModuleType("streamlit"); stub.cache_data = lambda **k: (lambda f: f)
    sys.modules["streamlit"] = stub
    m = types.ModuleType("cbapp"); sys.modules["cbapp"] = m
    src = open(os.path.join(ROOT, "valuation", "legacy.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], os.path.join(ROOT, "valuation", "legacy.py"), "exec"), m.__dict__)
    return m.__dict__


def keymap(wb):
    """가정 시트 행 번호 -> 키 이름. 수식 조서가 만들 때 쓴 표(wb.ROWN)를 그대로 뒤집는다."""
    return {r: k for k, r in wb.ROWN.items()}


def formula_wb(G, t, full, b0, b1, b2, ca, conv):
    return G["build_xlsx_formula"](t, full, b0, b1, b2, ca, conv, G["eir_table"](t, b0),
                                   as_workbook=True)


def terms(G, **kw):
    t = G["Terms"]()
    t.rf_curve = [(1, .0226), (3, .0240), (5, .0252)]
    t.cr_curve = [(1, .1409), (3, .1740), (5, .1905)]
    for k, v in kw.items(): setattr(t, k, v)
    G["derive"](t)
    return t


def main():
    G = load_app()
    import openpyxl
    bad = 0

    print("1. 머리 행이 가정 시트의 올바른 항목을 참조하는가")
    # 기본값이 서로 다르도록 일부러 어긋나게 준다
    t = terms(G, p_f=6., k_f=1., k_e=36., ipay=6., cpn=.03, ytm=.07, rfx_cyc=5.,
              gap_m=6.)
    full, b0, b1, b2, ca, conv = G["decompose"](t)
    wb = formula_wb(G, t, full, b0, b1, b2, ca, conv)
    km = keymap(wb)
    # ⑯ 부채요소는 머리 행 구성이 다르므로 뺀다. ⑮ 30% 트랜치의 전환·조기상환 Flag 는
    # 의무보유를 반영한 cv30 · pt30 을 쓰는 것이 맞다.
    trees = [s for s in wb.sheetnames
             if re.match(r"^\d\d ", s) and not s.startswith("16 ")]
    # 행사금액(7·8행)은 00 격자 공통의 계산 과정 행에서 한 단계씩 계산한다 — 그 행들도 함께 본다.
    STEPS = {7: [20, 21, 22, 23, 24, 25, 26], 8: [27, 28, 29, 30, 31, 32, 33]}
    for row, want in WANT.items():
        seen = set()
        for nm in trees:
            ws = wb[nm]
            rows = [row] + (STEPS.get(row, []) if nm == "00 격자 공통" else [])
            for rr in rows:
                for c in range(3, 3+t.n+1):
                    v = ws.cell(rr, c).value
                    if isinstance(v, str) and v.startswith("="):
                        for mm in re.finditer(r"가정!\$C\$(\d+)", v):
                            seen.add(km.get(int(mm.group(1)), "?%s" % mm.group(1)))
        extra, miss = seen - want, want - seen
        ok = not extra and not miss
        if not ok: bad += 1
        print("   %-14s %-34s %s" % (ROWNAME[row], " · ".join(sorted(seen)),
                                     "OK" if ok else
                                     "★ 잘못 참조 %s / 빠짐 %s" % (sorted(extra), sorted(miss))))

    print("\n2. 엔진 · 값 조서 · 수식 조서가 같은 행사일을 쓰는가")
    # 계약서의 행사일(시작일부터 주기마다)을 «계약일 이후 첫 노드» 에 둔다 — 여기서는 앱의
    # 목록을 보지 않고 날짜를 직접 세어 배정한다. 같은 날의 두 권리는 같은 노드에 와야 한다.
    d0 = dt.date.fromisoformat(G["Terms"]().d_issue)
    for el in (0, 3, 6, 12, 18, 24, 36):
        t = terms(G, d_base=(d0+dt.timedelta(days=int(el*30.4375))).isoformat())
        n = t.n; mper = n/(t.T*12)
        lo, hi = G["step_mapper"](t, n, t.T/n)
        gapm = t.rem_m/n
        def dates(s_, e_, f_):
            if s_ > e_: return set()
            if f_ < 1.5*gapm: return set(range(max(lo(s_), 0), min(hi(e_), n)+1))
            out, k = set(), 0
            while s_ + k*f_ <= e_ + 1e-6:
                m = s_ + k*f_; k += 1
                if m < t.elapsed_m - 1e-6: continue
                if lo(m) <= n: out.add(lo(m))
            return out
        per = max(1, int(round(t.rfx_cyc*mper)))
        off = int(round((t.rfx_cyc - t.elapsed_m % t.rfx_cyc)*mper)) if t.rfx_cyc > 0 else 1
        eng = {
            "리픽싱": {i for i in range(1, n+1) if i >= off and (i-off) % per == 0},
            "조기상환": dates(t.p_s, t.p_e, t.p_f),
            "매도청구": dates(t.k_s, t.k_e, t.k_f),
        }
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        wv = openpyxl.load_workbook(io.BytesIO(
            G["build_xlsx"](t, full, b0, b1, b2, ca, conv, G["eir_table"](t, b0))))["01 주가"]
        val = {"리픽싱": {i for i in range(1, n+1) if wv.cell(6, 3+i).value == 1},
               "조기상환": {i for i in range(0, n+1) if wv.cell(4, 3+i).value == 1},
               "매도청구": {i for i in range(0, n+1) if wv.cell(5, 3+i).value == 1}}
        # 수식 조서 — 리픽싱은 가정 값으로 같은 식을 세우고, 행사일은 00 격자 공통의 행사월 행을 본다
        wbf = formula_wb(G, t, full, b0, b1, b2, ca, conv)
        wf, wc = wbf["가정"], wbf["00 격자 공통"]
        A = {}
        for r in range(3, 120):
            nm2 = wf.cell(r, 2).value
            if nm2: A[nm2] = wf.cell(r, 3).value
        fm = {
            "리픽싱": {i for i in range(1, n+1)
                    if i >= A["첫 조정 스텝"] and (i-A["첫 조정 스텝"]) % A["리픽싱 주기 (스텝)"] == 0},
            "조기상환": {i for i in range(0, n+1) if wc.cell(20, 3+i).value not in (None, "")},
            "매도청구": {i for i in range(0, n+1) if wc.cell(27, 3+i).value not in (None, "")},
        }
        marks = []
        for k in ("리픽싱", "조기상환", "매도청구"):
            ok = eng[k] == val[k] == fm[k]
            if not ok: bad += 1
            marks.append("%s %s" % (k, "OK" if ok else "★"))
        print("   경과 %2.0f개월  n=%2d  %s" % (t.elapsed_m, n, " · ".join(marks)))

    print("\n2-2. 같은 계약일의 풋·콜은 같은 노드에 온다 (주 격자)")
    t = terms(G, d_issue="2026-07-10", d_base="2026-07-10", d_mat="2031-07-10", grid_days=7.,
              p_s=24., p_e=57., p_f=3., k_s=12., k_e=24., k_f=3., k_w=.5)
    EA = G["exercise_amounts"](t, t.n, t.T/t.n)
    kp = {m: i for i, m in EA["p_dates"].items()}; kk = {m: i for i, m in EA["k_dates"].items()}
    same = [m for m in kp if m in kk]
    ok = bool(same) and all(kp[m] == kk[m] for m in same)
    if not ok: bad += 1
    print("   겹치는 계약일 %s → 노드 %s  %s" % (same, [(kp[m], kk[m]) for m in same], "OK" if ok else "★"))

    print("\n3. 값 조서의 금액 행이 계약일 기준 산식과 같은가")
    for lbl, kw in (("무이표", {}),
                    ("표면 2% · 보장 7% 복리", dict(cpn=.02, ytm=.07,
                                                 p_mode="accrue", p_yield=.07)),
                    ("중간평가 12개월", dict(d_base="2026-03-31", cpn=.02,
                                        p_mode="accrue", p_yield=.07)),
                    ("중간평가 18개월 · 반기", dict(d_base="2026-09-30", cpn=.03,
                                             ipay=6., ytm=.07, p_mode="accrue",
                                             p_yield=.07, k_prem=.05))):
        t = terms(G, **kw)
        n = t.n; dt_ = t.T/n; ey = t.elapsed_m/12
        lo, hi = G["step_mapper"](t, n, dt_)
        gapm = t.rem_m/n
        ar = G["accrue_rate"]
        # 행사금액의 경과연수 — 계약은 «개월» 로 센다 (acc_basis=1). 행사일 노드의 금액은
        # 노드가 아니라 **계약일** 의 경과기간으로 센다.
        yr = (lambda m: m/12) if int(t.acc_basis) else \
            (lambda m: (m - t.elapsed_m)/t.rem_m*t.T + ey)
        mat_yr = (t.elapsed_m + t.rem_m)/12 if int(t.acc_basis) else t.T + ey
        def when(s_, e_, f_):
            if s_ > e_: return {}
            if f_ < 1.5*gapm:
                return {i: t.elapsed_m + i*t.rem_m/n for i in range(max(lo(s_), 0), min(hi(e_), n)+1)}
            out, k = {}, 0
            while s_ + k*f_ <= e_ + 1e-6:
                m = s_ + k*f_; k += 1
                if m >= t.elapsed_m - 1e-6 and lo(m) <= n and lo(m) not in out: out[lo(m)] = m
            return out
        pw, kw_ = when(t.p_s, t.p_e, t.p_f), when(t.k_s, t.k_e, t.k_f)
        pays = {}
        k = int(t.elapsed_m // t.ipay) + 1
        while k*t.ipay <= t.elapsed_m + t.rem_m + 1e-6:
            i = min(lo(k*t.ipay), n); pays[i] = pays.get(i, 0) + (1 if i >= 1 else 0); k += 1
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        W = openpyxl.load_workbook(io.BytesIO(
            G["build_xlsx"](t, full, b0, b1, b2, ca, conv,
                            G["eir_table"](t, b0))))["01 주가"]
        worst = [("", 0.0)]
        for i in range(n+1):
            want = (0.0 if i not in pw else
                    (100*(1+ar(yr(pw[i]), t.p_yield, t.cpn, t.p_cmp))
                     if t.p_mode == "accrue" else t.p_rate))
            got = W.cell(7, 3+i).value or 0.0
            if abs(got-want) > worst[0][1]: worst = [("조기상환금액", abs(got-want))]
            want = (999999 if i not in kw_ else 100*(1+ar(yr(kw_[i]), t.k_prem, t.cpn, t.k_cmp)))
            got = W.cell(8, 3+i).value or 0.0
            if abs(got-want) > worst[0][1]: worst = [("매도청구금액", abs(got-want))]
            want = 100*t.cpn*t.ipay/12*pays.get(i, 0) if t.cpn > 0 else 0.0
            got = W.cell(9, 3+i).value or 0.0
            if abs(got-want) > worst[0][1]: worst = [("쿠폰", abs(got-want))]
            want = 100*(1+ar(mat_yr, t.ytm, t.cpn, t.ytm_cmp)) if i == n else 0.0
            got = W.cell(10, 3+i).value or 0.0
            if abs(got-want) > worst[0][1]: worst = [("만기상환", abs(got-want))]
        nm2, gap = worst[0]
        ok = gap < 1e-3
        if not ok: bad += 1
        print("   %-22s n=%2d  최대 차이 %s %.6f  %s"
              % (lbl, n, nm2 or "—", gap, "OK" if ok else "★"))

    print("\n3-1. 이자 지급 횟수는 격자 간격과 무관하다 (5년 · 분기 이표 = 20회)")
    for gd in (0., 14., 7.):
        t = terms(G, d_issue="2026-01-01", d_base="2026-01-01", d_mat="2031-01-01",
                  cpn=.04, ipay=3., grid_days=gd)
        cnt = sum(G["pay_steps"](t, t.n, t.T/t.n).values())
        ok = cnt == 20
        if not ok: bad += 1
        print("   %-6s n=%3d  %d회  %s" % ({0.: "월", 14.: "2주", 7.: "주"}[gd], t.n, cnt, "OK" if ok else "★"))

    print("\n0. 경과기간 계산")
    # 꽉 찬 개월을 세고 남는 일수는 그 구간의 실제 한 달 길이로 나눈다.
    # 말일까지 남은 날수로 나누면 12월 23일→31일 8일이 0.89개월로 부풀어
    # 결산일 평가가 통째로 밀린다.
    import datetime as _dt
    _D = _dt.date.fromisoformat
    for a, b, want in (("2025-05-23", "2025-12-31", 7.2581),
                       ("2025-05-23", "2025-12-20", 6.9000),
                       ("2025-05-23", "2025-06-30", 1.2333),
                       ("2025-03-31", "2025-12-31", 9.0000),
                       ("2025-11-30", "2025-12-31", 1.0323),
                       ("2025-01-31", "2025-02-28", 1.0000),
                       ("2025-05-23", "2026-05-23", 12.0000),
                       ("2025-05-23", "2025-05-23", 0.0000)):
        got = G["months_between"](_D(a), _D(b))
        ok = abs(got - want) < 1e-4
        if not ok: bad += 1
        print("   %s → %s  %8.4f 개월  %s"
              % (a, b, got, "OK" if ok else "★ %.4f 기대" % want))

    print("\n3-2. 행사 시작이 계약일보다 앞서지 않는가")
    # 스텝을 반올림으로 잡으면 계약일 전 노드에서 행사가 열려 옵션이 과대평가된다.
    # 노드 날짜를 계약일과 직접 견주되, 달력 근사만큼(최대 5일)은 같은 날로 본다.
    _d0 = _dt.date(2025, 5, 23)
    for gap, glab in ((1., "월"), (12/26, "2주"), (12/52, "주")):
        worst, over = 0, 0
        for k in range(0, 400, 14):
            t = terms(G, d_issue="2025-05-23", d_mat="2029-05-23", gap_m=gap,
                      d_base=(_d0 + _dt.timedelta(days=k)).isoformat())
            if t.T <= 0.2: continue
            lo, _ = G["step_mapper"](t, t.n, t.T/t.n)
            day = t.T/t.n*365
            for mth in (12., 30.):
                i = lo(mth)
                if i > t.n: continue
                nd = _D(t.d_base) + _dt.timedelta(days=round(i*day))
                d = (nd - G["_add_months"](_d0, int(mth))).days
                worst = min(worst, d)
                if d < -5: over += 1
        ok = over == 0
        if not ok: bad += 1
        print("   %-4s 노드  최대 앞당김 %3d일 · 허용오차 밖 %d건  %s"
              % (glab, worst, over, "OK" if ok else "★"))

    print("\n4. 전환가격을 바꾸는 조항이 없으면 전환가격 트리와 그 입력 줄을 싣지 않는가")
    for rfx, lbl in ((0, "리픽싱 없음"), (2, "하향·상향")):
        t = terms(G, rfx_mode=rfx, rfx_cyc=7., gap_m=3.)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        wbf = formula_wb(G, t, full, b0, b1, b2, ca, conv)
        names = set(wbf.sheetnames)
        labels = {wbf["가정"].cell(r, 2).value for r in range(1, 150)}
        has = {"02 전환가격", "03 전환비율"} <= names
        ok = (not has and "최저 조정가액" not in labels) if rfx == 0 else (has and "최저 조정가액" in labels)
        if not ok: bad += 1
        print("   %-10s 02·03 트리 %s · 최저 조정가액 줄 %s  %s"
              % (lbl, "있음" if has else "없음", "있음" if "최저 조정가액" in labels else "없음",
                 "OK" if ok else "★"))
    # 옛 파일의 상태확장(carry=0)은 경로가중치(1)로 계산한다
    t = terms(G, carry=0, rfx_mode=2, rfx_cyc=7., gap_m=3.)
    ok = t.carry == 1
    if not ok: bad += 1
    print("   상태확장(0) 입력 → carry %d  %s" % (t.carry, "OK" if ok else "★"))

    print("\n" + ("배선 이상 없음" if bad == 0 else "★ %d건" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
