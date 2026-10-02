#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""앱에 넣은 입력값이 조서에 정확히 실리는가 — 가상 수치.

앱 경로(평가파일 → 평가 → 조서 내려받기) 그대로 값 조서·수식 조서를 만든 뒤

1. 수식 조서 가정 시트의 칸마다 앱이 계산에 쓴 값(적용값)과 같은지 본다. 기대값은 조서 코드가 아니라
   이 파일의 표(EXPECT)가 Terms 항목에서 직접 만든다.
2. 값 조서 가정 시트의 주요 줄(날짜·주가·전환가액·변동성·노드 수)이 같은지 본다.
3. 금리곡선(IR 입력곡선)·계약일 목록(00 계약일 목록)·계약서 회차표(00 행사금액표)가 입력과 같은지 본다.
4. 입력을 하나씩 바꿔 다시 만들면 그 칸만 새 값으로 바뀌는지 본다 — 옛 값이 굳어 있으면 여기서 걸린다.
5. 입력과 적용값이 다른 항목은 앱이 «정규화» 로 알린 항목뿐인지 본다.

    python3 tests/입력반영대조.py
"""
import copy
import datetime as dt
import io
import math
import os
import sys
import zipfile
import warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from openpyxl import load_workbook                                   # noqa: E402
from valuation import legacy as L                                    # noqa: E402
from valuation.case import import_legacy                             # noqa: E402
from valuation.service import calculate, export_bundle               # noqa: E402

RF = [[1, .025], [3, .027], [5, .029]]
CR = [[1, .08], [3, .095], [5, .105]]
CASES = [
    ("전환사채 · 정기 행사 · 하향+상향 · 이자 분기", dict(
        inst="CB", d_issue="2025-07-03", d_base="2025-07-03", d_mat="2029-07-03", S0=1348., K0=1484., floor=1039.,
        cpn=.02, ipay=3., ytm=.07, ytm_cmp=4, cv_s=12., cv_e=47., rfx_mode=2, rfx_cyc=7., rfx_first=3., rfx_round=1,
        p_s=12., p_e=27., p_f=3., p_mode="accrue", p_yield=.07, p_cmp=4, p_cpn_add=1,
        k_s=12., k_e=24., k_f=3., k_prem=.1, k_cmp=12, k_w=.35, k_hold=1, k_lock=24., k_lock_put=1, k_less_cpn=2,
        k_cpn_add=1, k_third=1, k_kind=0, k_sep=1, k_method=1, k_split=1, pc_order=1,
        conv_class="liability", emb_approach=2, p_sep=0, model="TF", sig=.51, gap_m=1.,
        rf_curve=RF, cr_curve=CR, face_total=2e9)),
    ("전환사채 · 발행회사 본인만 콜 · 중간 평가", dict(
        inst="CB", d_issue="2025-01-15", d_base="2025-10-20", d_mat="2028-01-15", S0=9000., K0=10000., floor=7000.,
        cpn=.01, ipay=3., ytm=.03, ytm_cmp=4, cv_s=12., cv_e=35., rfx_mode=1, rfx_cyc=3.,
        p_s=18., p_e=33., p_f=3., p_mode="accrue", p_yield=.03, p_cmp=4,
        k_s=12., k_e=18., k_f=3., k_prem=.03, k_cmp=4, k_w=.3, k_third=0, k_kind=0, k_sep=0, k_method=0,
        conv_class="equity", model="TF", sig=.4, gap_m=1., rf_curve=RF, cr_curve=CR, face_total=5e9)),
    ("신주인수권부사채 · 대용납입 · 금리격자 · 매도청구 회차표", dict(
        inst="BW", bw_pay=1, d_issue="2026-04-29", d_base="2026-04-29", d_mat="2031-04-29", S0=5170., K0=6055.,
        cpn=0., ipay=3., ytm=0., mat_amt=100., cv_s=12., cv_e=59., rfx_mode=0, p_s=24., p_e=57., p_f=3.,
        p_mode="fixed", p_rate=100., k_s=12., k_e=24., k_f=3., k_prem=.01, k_cmp=4, k_w=.4, k_hold=1, k_lock=24.,
        k_sched="12 101.0038\n15 101.2563\n18 101.5094\n21 101.7632\n24 102.0176",
        k_third=1, k_sep=1, k_method=2, k_split=1, conv_class="equity", emb_approach=2, p_sep=1,
        put_bdt=1, bdt_sig=.1, model="TF", sig=1.0, gap_m=1., rf_curve=RF, cr_curve=CR, face_total=6e9)),
    ("상환전환우선주 · 발행자 상환권", dict(
        inst="RCPS", issue_px=10000., par=500., mat_mode=1, div_mode=0, div_basis=0, issuer_call=1,
        d_issue="2025-03-01", d_base="2025-03-01", d_mat="2030-03-01", S0=9000., K0=10000., floor=7000.,
        cpn=.02, ipay=12., ytm=.04, ytm_cmp=1, cv_s=12., cv_e=59., rfx_mode=1, rfx_cyc=6.,
        p_s=36., p_e=59., p_f=3., p_mode="accrue", p_yield=.04, p_cmp=1, k_s=24., k_e=48., k_f=3.,
        conv_class="liability", model="TF", sig=.45, gap_m=1., rf_curve=RF, cr_curve=CR, face_total=3e9)),
]

D = lambda s: dt.date.fromisoformat(s)
# 가정 시트 키 → 적용값(Terms)에서 기대값을 만드는 식. 조서 코드를 부르지 않는다(콜 권리자 이름표만 예외).
EXPECT = {
    "d_issue": lambda t: D(t.d_issue), "d_base": lambda t: D(t.d_base), "d_mat": lambda t: D(t.d_mat),
    "face": lambda t: t.face_total, "accb": lambda t: int(t.acc_basis), "ipaym": lambda t: t.ipay,
    "ytm": lambda t: t.ytm, "ycm": lambda t: t.ytm_cmp, "matx": lambda t: t.mat_amt, "mless": lambda t: int(t.m_less_cpn),
    "K0": lambda t: t.K0, "flr": lambda t: t.floor, "rround": lambda t: int(t.rfx_round), "par": lambda t: t.par,
    "rfx": lambda t: int(t.rfx_mode > 0), "up": lambda t: int(t.rfx_mode == 2), "mth": lambda t: max(1, int(t.carry)),
    "prate": lambda t: t.p_rate, "pcadd": lambda t: int(t.p_cpn_add), "pmode": lambda t: int(t.p_mode == "accrue"),
    "psm": lambda t: t.p_s, "pyld": lambda t: t.p_yield, "pcmp": lambda t: t.p_cmp, "pless": lambda t: int(t.p_less_cpn),
    "kcadd": lambda t: int(t.k_cpn_add), "prem": lambda t: t.k_prem, "kcmp": lambda t: t.k_cmp,
    "kless": lambda t: int(t.k_less_cpn), "cw": lambda t: t.k_w, "khold": lambda t: int(t.k_hold),
    "lkput": lambda t: int(t.k_lock_put), "S0": lambda t: t.S0, "sig": lambda t: t.sig, "divy": lambda t: t.div_y,
    "bsig": lambda t: t.bdt_sig, "bbase": lambda t: int(t.bdt_base), "eqcls": lambda t: int(t.conv_class == "equity"),
    "kmeth": lambda t: int(t.k_method), "ksplit": lambda t: int(t.k_split), "kkind": lambda t: int(t.k_kind),
    "embap": lambda t: int(t.emb_approach), "psep": lambda t: int(t.p_sep), "plost": lambda t: int(t.p_lost_int),
    "stol": lambda t: t.split_tol, "ksep": lambda t: int(t.k_sep), "mdl": lambda t: int(t.model == "GS"),
    "pbdt": lambda t: int(bool(t.put_bdt)), "cost": lambda t: t.issue_cost, "pdrv": lambda t: t.prev_deriv,
    "phst": lambda t: t.prev_host, "n": lambda t: t.n, "elm": lambda t: t.elapsed_m, "remm": lambda t: t.rem_m,
    "inst": lambda t: {"CB": 0, "RCPS": 1, "BW": 2}[t.inst], "kwho": lambda t: L.CALL_HOLDERS[L.call_holder(t)],
    "cpn": lambda t: t.cpn, "cpnc": lambda t: t.cpn, "dbas": lambda t: int(t.div_basis), "ipx": lambda t: t.issue_px,
    "dmode": lambda t: int(t.div_mode),
}
# 하나씩 바꿔 볼 입력 — (Terms 항목, 가정 키, 바꾸는 식)
PERTURB = [("S0", "S0", lambda v: v*1.07), ("sig", "sig", lambda v: v + .03), ("K0", "K0", lambda v: v + 11),
           ("cpn", "cpn", lambda v: v + .005), ("ytm", "ytm", lambda v: v + .01), ("floor", "flr", lambda v: v - 13),
           ("p_yield", "pyld", lambda v: v + .01), ("p_rate", "prate", lambda v: v + 1), ("k_prem", "prem", lambda v: v + .01),
           ("k_w", "cw", lambda v: round(v - .05, 4)), ("div_y", "divy", lambda v: v + .01),
           ("face_total", "face", lambda v: v*1.5), ("split_tol", "stol", lambda v: .07),
           ("rfx_round", "rround", lambda v: (int(v) + 1) % 3), ("bdt_sig", "bsig", lambda v: v + .05),
           ("p_cpn_add", "pcadd", lambda v: 1 - int(v)), ("k_cmp", "kcmp", lambda v: 2 if v != 2 else 4)]

bad = []


def ok(tag, cond, detail=""):
    print(f"   {'OK' if cond else '★'}  {tag}" + (f"  — {detail}" if (detail and not cond) else ""))
    if not cond: bad.append(tag)


def same(a, b):
    if isinstance(a, dt.datetime): a = a.date()
    if isinstance(b, dt.datetime): b = b.date()
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12)
    return a == b


def books(obj, name):
    run = calculate(import_legacy(obj, name))
    out = {}
    for f in (False, True):
        z = zipfile.ZipFile(io.BytesIO(export_bundle(run, formula=f, detail=True, accounting=True)))
        out[f] = load_workbook(io.BytesIO(z.read("formula_review.xlsx" if f else "value_review.xlsx")))
    t = run.terms
    full, b0, b1, b2, ca, conv = (run.raw[k] for k in ("full", "b0", "b1", "b2", "ca", "conv"))
    rown = L.build_xlsx_formula(copy.deepcopy(t), full, b0, b1, b2, ca, conv, None, as_workbook=True).ROWN
    return run, out[False], out[True], rown


def check_case(name, obj):
    print(f"\n[{name}]")
    run, V, F, rown = books(obj, name)
    t = run.terms
    A = F["가정"]
    # 1. 수식 조서 가정 시트
    miss = []
    for k, r in rown.items():
        if k not in EXPECT: continue
        got, want = A.cell(r, 3).value, EXPECT[k](t)
        if k == "cpn" and "cpnc" in rown: continue     # 상환전환우선주는 «계산에 쓰는 값» 이 수식이다 — 계약 배당률은 cpnc
        if not same(got, want): miss.append(f"{k}({A.cell(r, 2).value}) 조서 {got!r} · 적용값 {want!r}")
    ok(f"수식 조서 가정 시트 {sum(1 for k in rown if k in EXPECT)}칸 = 적용값", not miss, "; ".join(miss[:6]))
    # 2. 값 조서 가정 시트 주요 줄
    VA = V["가정"]
    lab = {str(VA.cell(r, 2).value).strip(): VA.cell(r, 3).value for r in range(1, VA.max_row + 1) if VA.cell(r, 2).value}
    kname = "현재 행사가액" if t.inst == "BW" else "현재 전환가액"
    want = {"발행일": t.d_issue, "평가기준일": t.d_base, "만기일": t.d_mat, "평가기준일 주가": t.S0, kname: t.K0,
            "노드 수": t.n}
    vm = [f"{k} 조서 {lab.get(k)!r} · 적용값 {v!r}" for k, v in want.items()
          if not same(lab.get(k) if not isinstance(v, str) else str(lab.get(k)), v)]
    ok("값 조서 가정 시트 날짜·주가·전환가액·노드 수 = 적용값", not vm, "; ".join(vm))
    # 3. 금리곡선 · 계약일 목록 · 회차표
    I = F["IR 입력곡선"]
    cc = L.credit_curve(t)
    cm = [i for i, (m, y) in enumerate(t.rf_curve) if not (same(I.cell(8+i, 2).value, m) and same(I.cell(8+i, 3).value, y))]
    cm += [i for i, (m, y) in enumerate(cc) if not (same(I.cell(8+i, 5).value, m) and same(I.cell(8+i, 6).value, y))]
    ok(f"이자율 입력곡선 무위험 {len(t.rf_curve)}점 · 위험 {len(cc)}점 = 입력", not cm, str(cm))
    if "00 계약일 목록" in F.sheetnames:
        DS = F["00 계약일 목록"]
        rows = [(DS.cell(r, 2).value, DS.cell(r, 4).value, DS.cell(r, 5).value) for r in range(9, DS.max_row + 1)
                if isinstance(DS.cell(r, 4).value, (int, float))]
        dm = [f"{m} → {d}" for _, m, d in rows if not same(d, L.months_to_date(t.d_issue, m))]
        ok(f"계약일 목록 {len(rows)}줄 — 계약 개월과 계약일이 맞다", bool(rows) and not dm, "; ".join(dm[:4]))
        EA = L.exercise_amounts(t, t.n, t.T/t.n)
        for key, ms_ in (("조기상환일", sorted(EA["p_dates"].values())), ("매도청구일", sorted(EA["k_dates"].values()))):
            if EA["p_cont" if key == "조기상환일" else "k_cont"] or not ms_: continue
            lst = [m for nm, m, _ in rows if nm and L.inst_text(t, key) in str(nm)]
            blk = []
            take = False
            for nm, m, _ in rows:
                if nm: take = L.inst_text(t, key) in str(nm)
                if take: blk.append(m)
            ok(f"계약일 목록의 {key} = 엔진의 행사일 (평가기준일 뒤)",
               all(any(abs(m - x) < 1e-6 for x in blk) for x in ms_ for m in [x]) if blk else False,
               f"목록 {blk[:6]} · 엔진 {ms_[:6]}")
    if "00 행사금액표" in F.sheetnames:
        S = F["00 행사금액표"]
        sched = L.sched_rows(t.k_sched, t)
        got = [(S.cell(r, 3).value, S.cell(r, 5).value) for r in range(4, S.max_row + 1) if S.cell(r, 2).value is not None]
        ok("계약서 회차표(매도청구) = 입력", [round(a, 4) for a, _ in got] == [round(m, 4) for m, _ in sched]
           and all(abs(b - v) < 1e-6 for (_, b), (_, v) in zip(got, sched)), f"조서 {got} · 입력 {sched}")
    # 5. 정규화 — 입력과 적용값이 다른 항목은 앱이 알린 것뿐이다
    eff = run.case.effective()
    diff = sorted(k for k in eff if k in L.Terms.__dataclass_fields__ and not same(eff[k], getattr(t, k))
                  and not isinstance(eff[k], list))
    noted = set(run.summary.get("normalizations", {}))
    ok("입력 ≠ 적용값인 항목은 모두 «정규화» 로 알림", set(diff) <= noted, f"알리지 않은 항목 {sorted(set(diff) - noted)}")
    # 4. 하나씩 바꿔 다시 만든다
    changed, stale = 0, []
    for fld, key, fn in PERTURB:
        if key == "cpn" and "cpnc" in rown: key = "cpnc"
        if key not in rown or key not in EXPECT: continue
        o2 = copy.deepcopy(obj); o2[fld] = fn(getattr(t, fld))
        try:
            run2, _, F2, rown2 = books(o2, name)
        except Exception as e:                         # 막히는 조합이면 건너뛴다 (입력 점검이 할 일)
            print(f"      ({fld} 바꾼 조합은 계산이 막혀 건너뜀: {str(e)[:60]})"); continue
        want2 = EXPECT[key](run2.terms)
        got2 = F2["가정"].cell(rown2[key], 3).value
        changed += 1
        if not same(got2, want2) or same(got2, EXPECT[key](t)) and not same(want2, EXPECT[key](t)):
            stale.append(f"{fld}: 조서 {got2!r} · 새 적용값 {want2!r}")
    ok(f"입력 {changed}개를 하나씩 바꾸면 그 칸이 새 값으로 바뀐다", changed > 0 and not stale, "; ".join(stale))


def main():
    for name, obj in CASES:
        check_case(name, obj)
    print()
    if bad:
        print(f"★ 어긋남 {len(bad)}건"); [print("   -", b) for b in bad]
        return 1
    print("모두 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
