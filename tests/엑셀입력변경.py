"""엑셀에서 입력을 바꾼 뒤 다시 계산하면 앱과 같은 값이 나오는가.

조서대조.py 는 «앱에서 입력을 바꿔 조서를 새로 만든 뒤» 견준다. 이 시험은 반대 방향이다 —
조서를 한 번 만든 뒤 가정 시트의 노란 칸(주가 · 변동성 · 표면이자율 · 보장수익률 등)을
**엑셀 안에서** 바꾸고 다시 계산한 값을, 같은 입력으로 앱이 새로 계산한 값과 견준다.
수식 조서의 칸 하나라도 값으로 굳어 있으면(입력을 따라오지 않으면) 여기서 걸린다.

견주는 칸은 재계산 검사 도구와 같은 목록이다 (legacy.formula_key_cells — 결과 시트의
전체·주계약·부채요소·조기상환청구권·매도청구권·전환권대가와 상각표 유효이자율).

재계산은 기본으로 formulas(파이썬 계산기)로 한다. ``--libreoffice`` 를 주면 LibreOffice(Calc)로
다시 계산한다 — 둘 다 Microsoft Excel 이 아니므로 실제 Excel 에서의 확인은 따로 해야 한다.

    python3 tests/엑셀입력변경.py                   # 전부 (formulas)
    python3 tests/엑셀입력변경.py 부채               # 이름에 «부채» 가 든 경우만
    python3 tests/엑셀입력변경.py --libreoffice      # LibreOffice 로 재계산
"""
import copy
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_spec = importlib.util.spec_from_file_location("josu", os.path.join(ROOT, "tests", "조서대조.py"))
J = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(J)

# (이름, 계약 설정, 엑셀에서 바꿀 칸 {가정 시트 B열 라벨: (Terms 항목, 새 값)})
CASES = [
    ("CB · 주가 +10% · 변동성 +5%p", {},
     {"평가기준일 주가": ("S0", 853*1.1), "변동성 σ": ("sig", 0.413 + 0.05)}),
    # 전환권이 부채면 주계약이 «100 − 파생상품» 의 잔여라 주가가 바뀌면 상각표 유효이자율도 바뀐다
    ("CB 전환권 부채 · 주가 −15%", {"conv_class": "liability"},
     {"평가기준일 주가": ("S0", 853*0.85)}),
    ("CB 표면 2% → 3% · 만기보장 5% → 6%", {"cpn": 0.02, "ytm": 0.05},
     {"표면이자율": ("cpn", 0.03), "만기보장수익률": ("ytm", 0.06)}),
    # 조기상환금액을 보장수익률로 붙이는 방식(accrue)이어야 그 칸이 계산에 쓰인다
    ("CB 조기상환 보장 4% → 7%", {"p_mode": "accrue", "p_yield": 0.04},
     {"조기상환 보장수익률": ("p_yield", 0.07)}),
    ("RCPS · 주가 +20% · 우선배당 1% → 2%", {"inst": "RCPS", "cpn": 0.01},
     {"평가기준일 주가": ("S0", 853*1.2), "우선배당률 (계약)": ("cpn", 0.02)}),
    ("BW 현금납입 · 변동성 +10%p", {"inst": "BW", "bw_pay": 1},
     {"변동성 σ": ("sig", 0.513)}),
    ("GS · 주가 −10%", {"model": "GS"},
     {"평가기준일 주가": ("S0", 853*0.9)}),
    # 옵션차익법 — 엑셀에서 주가·변동성을 바꾸면 ⑲ 즉시행사가치 · ⑲a 존속 · ⑲b 판단 · ㉑~㉔ 가 따라와야 한다
    ("옵션차익 성분분리 · 전환확률 · 주가 +10% · 변동성 +5%p", {"k_method": 2, "k_split": 1},
     {"평가기준일 주가": ("S0", 853*1.1), "변동성 σ": ("sig", 0.413 + 0.05)}),
    ("옵션차익 혼합할인 · 의무보유 없음 · 주가 −10%", {"k_method": 1, "k_split": 0, "k_hold": 0},
     {"평가기준일 주가": ("S0", 853*0.9)}),
]


# 주주간계약 — 가정 시트의 노란 칸을 바꾸면 트리 · 결과(100 기준 · 원) · 회차 합계 · 회계처리가 따라와야
# 한다. 회차가 여럿이면 회차마다 가정 시트가 있어 같은 칸을 모든 회차에서 바꾼다. 가상 수치다.
SHA_BASE = dict(inst="SHA", S0=1000., K0=1000., d_issue="2025-03-31", d_base="2025-03-31",
                d_mat="2030-03-31", gap_m=6.0, sig=.40, face_total=1e10, sha_put_s=36., sha_put_e=60.,
                sha_put_f=6., sha_put_yield=.08, sha_call_s=12., sha_call_e=36., sha_call_f=6.,
                sha_call_prem=.15, rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                cr_curve=[(1, .0500), (3, .0550), (5, .0600)])
SHA_ROWS = [dict(name="1차", start="2026-01-01", end="2026-12-31", style="any", price=900., rate=0.,
                 put_q=30000., call_q=30000., rf=.021, pdisc=.061),
            dict(name="2차", start="2027-01-01", end="2027-12-31", style="any", price=1600., rate=.03,
                 put_q=30000., call_q=20000.)]
SHA_CASES = [
    ("SHA · 주가 +10% · 변동성 +5%p · 배당 0 → 2%", {},
     {"평가기준일 주가 (원)": ("S0", 1100.), "변동성 σ (연)": ("sig", .45),
      "보통주 배당수익률 (연, 연속)": ("div_y", .02)}),
    ("SHA · 풋 가산 8% → 0% (고정 가격) · 수량 변경", {"sha_put_q": 1e7, "sha_call_q": 8e6},
     {"풋 가격 가산율 (연)": ("sha_put_yield", 0.), "콜 대상 주식수": ("sha_call_q", 6e6)}),
    ("SHA · 가산기간 계약 개월 → 실제 일수 · 중간평가", {"d_base": "2026-03-31"},
     {"가격 가산기간 (1 계약 개월 ÷ 12 / 0 실제 일수)": ("acc_basis", 0)}),
    ("SHA 회차 둘 · 주가 −10% · 변동성 +10%p", {"sha_rows": SHA_ROWS, "d_issue": "2021-11-15",
                                          "d_base": "2025-09-30", "d_mat": "2027-12-31", "grid_days": 14.},
     {"평가기준일 주가 (원)": ("S0", 900.), "변동성 σ (연)": ("sig", .50)}),
]


def sha_want(G, t):
    """앱이 그 입력으로 새로 만든 값 조서의 숫자 칸 — 결과(회차별)·회차 합계·회계처리."""
    import io, openpyxl
    P = G["sha_portfolio"](t)
    wb = openpyxl.load_workbook(io.BytesIO(G["build_xlsx_sha"](t, P, formula=False)))
    out = []
    for ws in wb.worksheets:
        nm = ws.title
        if not (nm.endswith("결과") or nm in ("회차 합계", "회계처리")): continue
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool) and c.column >= 3:
                    lab = ws.cell(c.row, 2).value
                    out.append((f"{nm} {str(lab)[:14]} {c.coordinate}", nm, c.coordinate, float(c.value)))
    return out, P


def sha_main(G, only, use_lo):
    import openpyxl
    bad = 0
    for lbl, over, change in SHA_CASES:
        if only and only not in lbl: continue
        t0 = G["Terms"](**{**SHA_BASE, **over}); G["derive"](t0)
        P0 = G["sha_portfolio"](t0)
        x = G["build_xlsx_sha"](t0, P0, formula=True)
        t1 = copy.deepcopy(t0)
        for k, (attr, v) in change.items():
            setattr(t1, attr, v)
            if t1.sha_rows and attr in ("sha_put_yield",):   # 회차 표면 가산율은 회차 칸이다
                pass
        G["derive"](t1)
        want, _ = sha_want(G, t1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "wb.xlsx")
            open(path, "wb").write(x)
            wb = openpyxl.load_workbook(path)
            for ws in wb.worksheets:
                if not ws.title.endswith("가정"): continue
                rows = {c.value: c.row for (c,) in ws.iter_rows(min_col=2, max_col=2) if isinstance(c.value, str)}
                for k, (_, v) in change.items():
                    if k not in rows:
                        print(f"\n[{lbl}]  ★ {ws.title} 시트에 없는 칸: {k}"); bad += 1; continue
                    ws.cell(row=rows[k], column=3).value = v
            if use_lo:
                wb.save(path)
                mp = {s_: s_ for s_ in wb.sheetnames}
                need = {}
                for _, sh, cell, _ in want: need.setdefault(sh, []).append(cell)
                got = solve_libreoffice(path, need)
            else:
                mp = {nm: f"S{i:02d}" for i, nm in enumerate(wb.sheetnames)}
                for ws in wb.worksheets:
                    for row in ws.iter_rows():
                        for c in row:
                            if isinstance(c.value, str) and c.value.startswith("="):
                                f = c.value
                                for o, nn in sorted(mp.items(), key=lambda z: -len(z[0])):
                                    f = f.replace(f"'{o}'!", f"{nn}!").replace(f"{o}!", f"{nn}!")
                                c.value = f
                for o, nn in mp.items(): wb[o].title = nn
                wb.save(path)
                got = J.solve(path, tuple(sorted({mp[s_] for _, s_, _, _ in want if s_ in mp})))
        # 값 조서는 반올림하지 않은 값을 담는다 — 원 칸도 수식 조서와 같은 자리까지 맞아야 한다
        nb = 0
        print(f"\n[{lbl}]  엑셀에서 바꾼 칸: " + ", ".join(f"{k} → {v:g}" for k, (_, v) in change.items()))
        for name, sh, cell, w in want:
            h = got.get(mp.get(sh, ""), {}).get(cell)
            ok = h is not None and abs(h - w) <= 1e-9*max(1.0, abs(w))
            if not ok:
                nb += 1
                print("   %-40s 엑셀 %18s · 앱 %18.6f  ★" % (name, f"{h:.6f}" if h is not None else "없음", w))
        print(f"   칸 {len(want)}개 대조 · 불일치 {nb}")
        bad += nb
    return bad


def terms(G, over):
    t = G["Terms"](); t.rf_curve = [(1, .0226), (3, .0240), (5, .0252)]
    t.cr_curve = [(1, .1409), (3, .1740), (5, .1905)]
    t.gap_m = 6.0
    for k, v in over.items(): setattr(t, k, v)
    G["derive"](t)
    return t


def app_values(G, t):
    """앱이 그 입력으로 새로 계산한 값 — 수식 조서가 따라와야 할 답."""
    full, b0, b1, b2, ca, conv = G["decompose"](t)
    eir = G["eir_or_none"](t, full, b0, b1, b2, ca)
    raw = dict(full=full, b0=b0, b1=b1, b2=b2, ca=ca, conv=conv)
    return G["formula_key_cells"](t, raw, eir), (full, b0, b1, b2, ca, conv, eir)


def solve_libreoffice(path, sheets_cells):
    """LibreOffice 로 다시 계산해 {시트: {칸: 값}} — 값이 없는 수식 칸은 불러올 때 계산된다."""
    import shutil, subprocess
    import openpyxl
    from pathlib import Path
    exe = os.environ.get("VALUATION_SOFFICE") or shutil.which("libreoffice") or shutil.which("soffice")
    if not exe:
        raise SystemExit("LibreOffice 가 없습니다 — --libreoffice 없이 돌리십시오.")
    base = Path(path).parent; out = base/"lo"; out.mkdir(exist_ok=True)
    r = subprocess.run([exe, "-env:UserInstallation=" + (base/"profile").as_uri(), "--headless",
                        "--convert-to", "xlsx", "--outdir", str(out), str(path)],
                       capture_output=True, text=True, timeout=1800)
    tgt = out/Path(path).name
    if r.returncode or not tgt.exists():
        raise SystemExit("LibreOffice 재계산 실패: " + (r.stderr or r.stdout)[-800:])
    wb = openpyxl.load_workbook(tgt, data_only=True)
    got = {}
    for sh, cells in sheets_cells.items():
        got[sh] = {}
        for c in cells:
            v = wb[sh][c].value if sh in wb.sheetnames else None
            if isinstance(v, (int, float)) and not isinstance(v, bool): got[sh][c] = float(v)
    return got


def main():
    import openpyxl
    G = J.load_app()
    use_lo = "--libreoffice" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--libreoffice"]
    only = args[0] if args else ""
    bad = 0
    for lbl, over, change in CASES:
        if only and (only not in lbl or only == "SHA"): continue
        t0 = terms(G, over)
        _, (full, b0, b1, b2, ca, conv, eir) = app_values(G, t0)
        x = G["build_xlsx_formula"](t0, full, b0, b1, b2, ca, conv, eir)
        # 같은 입력으로 앱이 새로 계산한 답
        t1 = copy.deepcopy(t0)
        for k, (attr, v) in change.items(): setattr(t1, attr, v)
        G["derive"](t1)
        want, _ = app_values(G, t1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "wb.xlsx")
            open(path, "wb").write(x)
            wb = openpyxl.load_workbook(path)
            A = wb["가정"]
            rows = {c.value: c.row for (c,) in A.iter_rows(min_col=2, max_col=2)
                    if isinstance(c.value, str)}
            miss = [k for k in change if k not in rows]
            if miss:
                print(f"\n[{lbl}]  ★ 가정 시트에 없는 칸: {miss}"); bad += 1; continue
            for k, (_, v) in change.items():
                A.cell(row=rows[k], column=3).value = v
            if use_lo:
                # LibreOffice 는 시트 이름·COMBIN 을 그대로 계산한다 — 바꾸지 않고 넘긴다
                wb.save(path)
                mp = {s_: s_ for s_ in wb.sheetnames}
                need = {}
                for _, sh, cell, _ in want: need.setdefault(sh, []).append(cell)
                got = solve_libreoffice(path, need)
            else:
                # 조서대조와 같은 방식으로 시트 이름을 짧게 바꾸고 COMBIN 을 값으로 푼다
                mp = {nm: f"S{i:02d}" for i, nm in enumerate(wb.sheetnames)}
                for ws in wb.worksheets:
                    for row in ws.iter_rows():
                        for c in row:
                            if isinstance(c.value, str) and c.value.startswith("="):
                                f = c.value
                                for o, nn in sorted(mp.items(), key=lambda z: -len(z[0])):
                                    f = f.replace(f"'{o}'!", f"{nn}!").replace(f"{o}!", f"{nn}!")
                                c.value = J._combin(f)
                for o, nn in mp.items(): wb[o].title = nn
                wb.save(path)
                got = J.solve(path, tuple(sorted({mp[s_] for _, s_, _, _ in want if s_ in mp})))
        _report(lbl, change, want, got, mp)
        bad += _count_bad(want, got, mp)
    bad += sha_main(G, only, use_lo)
    print("\n" + ("모든 항목 일치 — 엑셀에서 바꾼 입력이 결과까지 따라온다" if bad == 0
                  else f"★ {bad}건 불일치")
          + (" (LibreOffice 재계산)" if use_lo else " (formulas 재계산)"))
    return 0 if bad == 0 else 1


def _ok(h, w):
    return h is not None and abs(h - w) <= 1e-7*max(1.0, abs(w))


def _count_bad(want, got, mp):
    return sum(not _ok(got.get(mp.get(sh, ""), {}).get(cell), w) for _, sh, cell, w in want)


def _report(lbl, change, want, got, mp):
    print(f"\n[{lbl}]  엑셀에서 바꾼 칸: " + ", ".join(f"{k} → {v:g}" for k, (_, v) in change.items()))
    for name, sheet, cell, w in want:
        h = got.get(mp.get(sheet, ""), {}).get(cell)
        fmt = (lambda v: f"{v*100:.6f}%") if "유효이자율" in name else (lambda v: f"{v:.6f}")
        print("   %-24s 엑셀 %14s · 앱 %14s  %s"
              % (name, fmt(h) if h is not None else "없음", fmt(w), "" if _ok(h, w) else "★"))


if __name__ == "__main__":
    sys.exit(main())
