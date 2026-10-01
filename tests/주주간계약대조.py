#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""주주간계약 조서가 엔진과 같은 답을 내는지 확인한다.

값 조서와 수식 조서를 `build_xlsx_sha` 한 함수에서 만들므로 자리와 차례는
갈라질 수 없다. 남는 것은 **수식이 엔진과 같은 계산을 하는가** 하나라서,
수식 조서를 실제로 풀어 엔진 값과 맞춰 본다. 값 조서 쪽도 같은 셀을 함께 본다.

    pip install formulas
    python3 tests/주주간계약대조.py            # 전부
    python3 tests/주주간계약대조.py 적격상장     # 이름으로 거르기

보는 칸 — 결과 시트의 100 기준 값(지분·풋·콜·합·총액 부채)과 원 단위 값(풋·콜 수량을 곱한
금액), 회계처리 시트의 세 관점 표(100 기준·원). 회차별 표 사례는 회차 합계 시트의 합계와
회차마다의 결과 시트를 본다. 사례는 모두 가상 수치다 (고객 자료가 아니다).

시트 이름에 한글이 있으면 formulas 가 깨져서 ASCII 로 바꾼 사본을 만들어 푼다.
"""
import sys, os, types, tempfile, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CASES = [
    ("기본", {}),
    ("콜 없음", dict(sha_call_s=0., sha_call_e=0.)),
    ("단리 보장", dict(sha_put_cmp=0, sha_call_cmp=0)),
    ("적격상장 36개월", dict(ipo_on=1, ipo_m=36., ipo_min=1200.)),
    ("적격상장 · 콜 존속",
     dict(ipo_on=1, ipo_m=36., ipo_min=1200., sha_qipo_kill=0)),
    ("무위험 할인", dict(sha_disc=0)),
    ("무위험 + 스프레드", dict(sha_disc=2, sha_spread=.04)),
    ("중간평가", dict(d_base="2026-03-31")),
    ("풋 주기 12개월", dict(sha_put_f=12.)),
    ("주가 60%", dict(S0=600.)),
    ("풋 의무자 = 발행회사", dict(sha_writer=1)),
    ("월 노드", dict(gap_m=1.0, sha_put_f=3., sha_call_f=3.)),
    # 상호소멸 — 한쪽이 행사하면 다른 쪽이 그 자리에서 소멸한다. 엔진에만 넣고
    # 수식 조서에 안 넣었던 적이 있어(조서를 풀면 다른 값이 나왔다) 여기 심는다.
    ("상호소멸", dict(sha_kill=1)),
    ("상호소멸 · 적격상장",
     dict(sha_kill=1, ipo_on=1, ipo_m=36., ipo_min=1200.)),
    ("상호소멸 · 콜 우선", dict(sha_kill=1, pc_order=1)),
    # ── SHA 점검 (2026-09-30) — 엑셀이 엔진을 따라오지 못하던 자리 ──
    # 위험중립확률에서 배당수익률이 빠져 있었다 (엔진만 뺐다).
    ("배당수익률 3%", dict(div_y=.03)),
    # 가산율 0% = 고정 행사가격. 보조 식이 0 으로 나누지 않아야 한다.
    ("가산율 0% (고정 행사가격)", dict(sha_put_yield=0., sha_call_prem=0.)),
    # 주·2주 격자에서 매월 행사 — 계약일마다 그날 이후 첫 노드 (칸 간격 반올림이 아니다)
    ("2주 격자 · 매월 행사", dict(grid_days=14., d_mat="2029-03-31", sha_put_e=48., sha_put_f=1.,
                               sha_call_f=1.)),
    ("주 격자 · 기간 중 언제든지", dict(grid_days=7., d_base="2027-03-31", d_mat="2029-03-31",
                                     sha_put_e=48., sha_put_f=0., sha_call_s=24., sha_call_e=48.,
                                     sha_call_f=0.)),
    # 가격 가산기간 — 실제 일수 (평가기준일까지 계약 개월 + 이후 실제 일수)
    ("가산기간 실제 일수 · 중간평가", dict(acc_basis=0, d_base="2026-03-31")),
    # 풋·콜 대상 주식수가 다르고 콜 기준가격이 다른 계약
    ("풋·콜 수량 다름 · 풋 권리자 관점", dict(sha_put_q=6_190_690., sha_call_q=4_643_000., sha_side=1)),
    ("콜 기준가격 다름", dict(sha_call_k=1100., sha_call_prem=.05)),
    # 같은 주식 물량의 연계 판단 — 수량이 달라 풋만·콜만 물량이 따로 남는 계약 (가상 수치)
    ("연계 · 풋 많음 · 콜 권리자 우선", dict(sha_kill=1, sha_put_q=600_000., sha_call_q=450_000., sha_link_q=450_000.,
                                    pc_order=1)),
    ("연계 · 콜 많음 · 풋 권리자 우선", dict(sha_kill=1, sha_put_q=300_000., sha_call_q=500_000., sha_link_q=300_000.)),
    ("연계 · 같은 가격 7% · 같은 기간", dict(sha_kill=1, sha_put_yield=.07, sha_call_prem=.07, sha_call_s=36.,
                                     sha_call_e=60., pc_order=1)),
    ("연계 · 풋 의무자 = 발행회사", dict(sha_kill=1, sha_writer=1)),
]
BASE = dict(inst="SHA", S0=1000., K0=1000., d_issue="2025-03-31",
            d_base="2025-03-31", d_mat="2030-03-31", gap_m=6.0, sig=0.40,
            face_total=10_000_000_000.,
            sha_put_s=36., sha_put_e=60., sha_put_f=6., sha_put_yield=.08,
            sha_put_cmp=1, sha_call_s=12., sha_call_e=36., sha_call_f=6.,
            sha_call_prem=.15, sha_call_cmp=1,
            rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
            cr_curve=[(1, .0500), (3, .0550), (5, .0600)])

# 회차별 표 — 연도별 물량·행사기간·기준가격·가산율·금리가 다른 두 회차(가상 수치).
ROWS = [
    dict(name="1차 행사분", start="2026-01-01", end="2026-12-31", style="any", price=900., rate=0.,
         put_q=30_000., call_q=30_000., rf=.021, pdisc=.061),
    dict(name="2차 행사분", start="2027-01-01", end="2027-12-31", style="any", price=1600., rate=0.,
         put_q=30_000., call_q=30_000., rf=.023, pdisc=.079, sig=.55),
]
PCASES = [
    ("회차 둘 · 고정 가격", dict(sha_rows=ROWS)),
    ("회차 하나 · 수량 다름 · 가산 6%",
     dict(sha_rows=[dict(name="단일", start="2026-06-10", end="2029-06-10", style="any", price=900., rate=.06,
                         put_q=600_000., call_q=450_000.)], d_issue="2024-06-10", d_base="2025-02-28",
          d_mat="2029-06-10", S0=960., sig=.30, grid_days=14.)),
    ("회차 둘 · 상호소멸 · 정기",
     dict(sha_rows=[dict(ROWS[0], kill=1), dict(ROWS[1], style="periodic", freq=3., kill=1, rate=.03)])),
    ("회차 하나 · 수량 다름 · 같은 주식 물량 연계",
     dict(sha_rows=[dict(ROWS[0], put_q=60_000., call_q=45_000., kill=1, link_q=45_000.)])),
]
PBASE = dict(inst="SHA", S0=1000., K0=1000., d_issue="2021-11-15", d_base="2025-09-30",
             d_mat="2027-12-31", sig=.60, gap_m=1.0, grid_days=7., sha_disc=1,
             rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
             cr_curve=[(1, .0500), (3, .0550), (5, .0600)])


def load_app():
    stub = types.ModuleType("streamlit"); stub.cache_data = lambda **k: (lambda f: f)
    sys.modules["streamlit"] = stub
    m = types.ModuleType("cbapp"); sys.modules["cbapp"] = m
    src = open(os.path.join(ROOT, "valuation", "legacy.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], os.path.join(ROOT, "valuation", "legacy.py"), "exec"), m.__dict__)
    return m.__dict__


def solve(G, t, R, path):
    """수식 조서를 만들어 풀고 {원래 시트 이름: {칸: 값}} 을 돌려준다."""
    import openpyxl, formulas
    open(path, "wb").write(G["build_xlsx_sha"](t, R, formula=True))
    wb = openpyxl.load_workbook(path)
    mp = {nm: f"S{i:02d}" for i, nm in enumerate(wb.sheetnames)}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    f = c.value
                    for o, nn in sorted(mp.items(), key=lambda x: -len(x[0])):
                        f = f.replace(f"'{o}'!", f"{nn}!").replace(f"{o}!", f"{nn}!")
                    c.value = f
    for o, nn in mp.items(): wb[o].title = nn
    wb.save(path)
    xl = formulas.ExcelModel().loads(path).finish()
    sol = xl.calculate()
    base = os.path.basename(path).upper()
    back = {v: k for k, v in mp.items()}
    out = {}
    for k, v in sol.items():
        ku = k.upper()
        if not ku.startswith(f"'[{base}]"): continue
        sh, cell = ku.split("]", 1)[1].split("'!")
        try: out.setdefault(back[sh], {})[cell] = float(v.value[0, 0])
        except Exception: pass
    return out


def main():
    G = load_app()
    import io, openpyxl
    bad = 0
    only = sys.argv[1] if len(sys.argv) > 1 else ""

    def line(nm, h, v, want, tol):
        """수식(h)·값(v)이 엔진(want)과 같은가. 원 단위 칸(won)은 상대 1e-9 — 값 조서도 반올림하지
        않은 값을 담으므로 두 조서가 같은 자리까지 맞아야 한다."""
        nonlocal bad
        rt = 1e-9 if tol == "won" else tol
        ok = (h is not None and abs(h - want) <= rt*max(1.0, abs(want))
              and v is not None and abs(float(v) - want) <= rt*max(1.0, abs(want)))
        if not ok: bad += 1
        print("   %-26s 수식 %18s · 값 %18s · 엔진 %18.4f  %s"
              % (nm, f"{h:.4f}" if h is not None else "없음",
                 f"{float(v):.4f}" if v is not None else "없음", want, "" if ok else "★"))

    def accounts(got, vw, t, R, multi):
        """회계처리 시트 — 세 관점 표의 모든 숫자 칸이 엔진(sha_accounts)과 같은가."""
        nonlocal bad
        A1 = None if multi else G["sha_accounts"](t, R)
        if multi:
            comp = {k: sum(G["sha_components_krw"](x["tm"], x["R"])[k] for x in R["rows"]) for k in
                    ("eq", "put", "call", "gpv")}
            has_call = any(x["R"]["has_call"] for x in R["rows"])
            lines = G["sha_account_lines"](t, R["rows"][0]["R"], has_call=has_call, gross=False)
            AK = {w: ([(nm, G["sha_eval"](c, comp)) for nm, c in rows], m) for w, (rows, m) in lines.items()}
        else:
            AK = G["sha_accounts"](t, R, krw=True)
        ws = vw["회계처리"]; fx = got.get("회계처리", {})
        r = 4
        for who in G["SHA_PARTIES"]:
            r += 2
            for k, (nm, vk) in enumerate(AK[who][0]):
                if not multi and A1[who][0][k][1] is None:
                    # 주식수가 달라 100 기준 순액을 싣지 않는 칸 — 두 조서 모두 숫자가 아니어야 한다
                    if isinstance(ws.cell(r, 3).value, (int, float)):
                        print(f"   회계 {who[:4]} {nm[:10]} 100 — 주식수가 다른데 100 기준 순액이 숫자로 실림 ★")
                        bad += 1
                elif not multi:
                    line(f"회계 {who[:4]} {nm[:10]} 100", fx.get(f"C{r}"), ws.cell(r, 3).value, A1[who][0][k][1], 1e-6)
                line(f"회계 {who[:4]} {nm[:10]} 원", fx.get(f"D{r}"), ws.cell(r, 4).value, vk, "won")
                r += 1
            r += 2

    for lbl, over in CASES:
        if only and only not in lbl: continue
        t = G["Terms"](**{**BASE, **over}); G["derive"](t)
        R = G["sha_portfolio"](t)
        with tempfile.TemporaryDirectory() as d:
            got = solve(G, t, R, os.path.join(d, "wb.xlsx"))
        vwb = openpyxl.load_workbook(io.BytesIO(G["build_xlsx_sha"](t, R, formula=False)))
        vw, fx = vwb["결과"], got.get("결과", {})
        gp = R["gross"]
        kp = t.K0/100
        qp, qc = G["sha_qty"](t)
        eqv = 100*t.S0/t.K0
        rows = [("지분가치", "C6", 6, 3, eqv),
                ("풋", "C7", 7, 3, R["put"]),
                ("콜", "C8", 8, 3, R["call"]),
                ("지분+풋−콜 원", "F9", 9, 6, eqv*kp*G["sha_contract_shares"](t) + R["put"]*kp*qp - R["call"]*kp*qc),
                ("총액 부채", "C15", 15, 3, gp["pv"] if gp else 0.0),
                ("풋 원", "F7", 7, 6, R["put"]*kp*qp),
                ("콜 원", "F8", 8, 6, R["call"]*kp*qc)]
        print(f"\n[{lbl}]")
        for nm, cell, vr, vc, want in rows:
            line(nm, fx.get(cell), vw.cell(vr, vc).value, want, 1e-6 if vc == 3 else "won")
        accounts(got, vwb, t, R, False)

    for lbl, over in PCASES:
        if only and only not in lbl: continue
        t = G["Terms"](**{**PBASE, **over}); G["derive"](t)
        P = G["sha_portfolio"](t)
        with tempfile.TemporaryDirectory() as d:
            got = solve(G, t, P, os.path.join(d, "wb.xlsx"))
        vwb = openpyxl.load_workbook(io.BytesIO(G["build_xlsx_sha"](t, P, formula=False)))
        print(f"\n[{lbl}] 회차 {len(P['rows'])}")
        for k, x in enumerate(P["rows"], 1):
            sh = f"{k}·결과"
            vw, fx = vwb[sh], got.get(sh, {})
            line(f"{x['name']} 풋 100", fx.get("C7"), vw.cell(7, 3).value, x["R"]["put"], 1e-6)
            line(f"{x['name']} 콜 100", fx.get("C8"), vw.cell(8, 3).value, x["R"]["call"], 1e-6)
            line(f"{x['name']} 풋 원", fx.get("F7"), vw.cell(7, 6).value, x["put_krw"], "won")
            line(f"{x['name']} 콜 원", fx.get("F8"), vw.cell(8, 6).value, x["call_krw"], "won")
        tr = 5 + len(P["rows"])
        vw, fx = vwb["회차 합계"], got.get("회차 합계", {})
        line("합계 풋 원", fx.get(f"J{tr}"), vw.cell(tr, 10).value, P["put_krw"], "won")
        line("합계 콜 원", fx.get(f"K{tr}"), vw.cell(tr, 11).value, P["call_krw"], "won")
        accounts(got, vwb, t, P, True)
    print("\n" + ("모든 항목 일치" if not bad else f"★ {bad}건 불일치"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
