#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""시나리오 파일 하나로 평가를 돌려 조서 묶음을 만든다 — 앱 화면을 거치지 않는다.

계산은 app.py 의 엔진을 그대로 쓴다. 화면에서 「조서 만들기」를 누른 것과 같은 파일이 나온다.

    python3 tools/run_valuation.py 시나리오.json --out 출력폴더
    python3 tools/run_valuation.py 시나리오.json --out 출력폴더 --set p_s_date=2035-04-09 --sens 민감도.json

* 시나리오 — 앱의 「시나리오 저장」 파일과 같은 형식(``Terms`` 칸 이름). 없는 칸은 앱 기본값이다
* ``--set 칸=값`` — 시나리오 위에 덮어쓴다. 여러 번 줄 수 있다. 행사 시점은 날짜로도 준다:
  ``cv_s_date`` ``cv_e_date`` ``p_s_date`` ``p_e_date`` ``k_s_date`` ``k_e_date`` ``k_lock_date``
  ``ipo_m_date`` (발행일 기준 개월로 바꾼다). ``p_none=1`` · ``k_none=1`` 은 그 권리를 없앤다
* ``--sens 파일`` — ``[{"label": "…", "set": {칸: 값}}]`` 목록. 각 줄을 따로 돌려 민감도 표를 만든다
* ``--vol 패키지.json`` — 변동성 전용 앱(vol_app.py)이 내려준 변동성 패키지. σ 를 그 값으로 두고
  (``--set sig=`` 가 있으면 그쪽이 이긴다) 조서에 변동성 산출내역 시트를 싣는다
* ``--no-formula`` — 수식 조서를 만들지 않는다 (주 단위 노드면 파일이 크고 엑셀이 느리게 연다)
* ``--no-check`` — 수식 조서 자체 검산(노드를 줄여 엑셀 수식을 실제로 푼다)을 건너뛴다

만드는 것 (출력폴더):
  <이름>_값조서.xlsx · <이름>_수식조서.xlsx · <이름>_시나리오.json · <이름>_결과요약.md
고객 자료가 들어 있으므로 출력폴더는 저장소 밖에 둔다.
"""
import sys, os, io, re, json, argparse, types, warnings, zipfile, datetime as dt
from dataclasses import asdict
from xml.etree import ElementTree as ET

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE_KEYS = {"cv_s_date": "cv_s", "cv_e_date": "cv_e", "p_s_date": "p_s", "p_e_date": "p_e",
             "k_s_date": "k_s", "k_e_date": "k_e", "k_lock_date": "k_lock", "ipo_m_date": "ipo_m"}
XL_MAX = 8192                       # 엑셀 한 셀 수식 한도 — 넘으면 「일부 내용에 문제」


def load_app():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from valuation import legacy
    return vars(legacy)



def _num(v):
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ("true", "false"): return s.lower() == "true"
        try: return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
        except ValueError: return s
    return v


def apply_set(G, t, kv: dict):
    """덮어쓰기. 날짜 키는 발행일 기준 개월로 옮긴다 — 발행일을 먼저 반영한다."""
    kv = dict(kv)
    for k in ("d_issue", "d_base", "d_mat"):
        if k in kv: setattr(t, k, str(kv.pop(k)))
    for k, v in kv.items():
        if k in DATE_KEYS:
            setattr(t, DATE_KEYS[k], G["date_to_months"](t.d_issue, dt.date.fromisoformat(str(v))))
        elif k == "p_none":
            if _num(v): t.p_s, t.p_e = 99*12.0, 0.0
        elif k == "k_none":
            if _num(v): t.k_s, t.k_e = 99*12.0, 0.0
        elif k in G["Terms"].__dataclass_fields__:
            setattr(t, k, _num(v))
        else:
            raise SystemExit(f"모르는 칸: {k}")


def make_terms(G, scen: dict, over: dict):
    from valuation.bridge import legacy_term_values
    T = G["Terms"]
    t = T(**legacy_term_values(scen))
    apply_set(G, t, over)
    G["derive"](t)
    return t


def run(G, t):
    full, b0, b1, b2, ca, conv = G["decompose"](t)
    return dict(full=full, b0=b0, b1=b1, b2=b2, ca=ca, conv=conv, net=b2 - ca)


def scan(data: bytes) -> list:
    """엑셀이 「일부 내용에 문제」로 복구할 자리를 찾는다 — XML · 수식 길이 · 시트 이름."""
    probs = []
    z = zipfile.ZipFile(io.BytesIO(data))
    for nm in z.namelist():
        if nm.endswith((".xml", ".rels")):
            x = z.read(nm)
            try: ET.fromstring(x)
            except Exception as e: probs.append(f"XML 깨짐 {nm}: {e}"); continue
            if nm.startswith("xl/worksheets/"):
                for f in re.findall(rb"<f>(.*?)</f>", x):
                    import html
                    n = len(html.unescape(f.decode("utf-8", "replace")))
                    if n > XL_MAX: probs.append(f"{nm} 수식 {n:,}자 > {XL_MAX:,}"); break
    wbx = z.read("xl/workbook.xml").decode("utf-8", "replace")
    for s in re.findall(r'<sheet [^>]*name="([^"]+)"', wbx):
        import html
        s = html.unescape(s)
        if len(s) > 31 or re.search(r"[\[\]:*?/\\]", s): probs.append(f"시트 이름 {s!r}")
    return probs


def self_check(G, t) -> list:
    """이 시나리오의 수식 조서를 노드를 줄여(6개월 간격) 실제로 풀어 엔진과 맞춘다.

    tests/조서대조.py 와 같은 방법이다. 트리 수식의 모양은 노드 수와 무관하므로, 이 시나리오의
    스위치 조합에서 수식이 엔진과 같은 답을 내는지 몇 초~1분 안에 확인된다.
    """
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import importlib, tempfile
    jd = importlib.import_module("조서대조")
    over = {k: v for k, v in asdict(t).items()
            if k not in ("T", "n", "elapsed_m", "rem_m", "scen_md5", "gap_m")}
    over["_gap"] = 6.0
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "wb.xlsx")
        eng, res, acc = jd.build(G, over, path)
        got = jd.solve(path, (res, acc))
    out = []
    want = eng["b2"]; have = got[res].get("C10")
    out.append(("적용 트랜치 (결과!C10)", have, want))
    if eng.get("holder"):
        out.append(("투자자 순포지션 (회계처리!C10)", got[acc].get("C10"), eng["b2"] - eng["ca"]))
    elif eng.get("fv_only"):
        out.append(("공정가치 전용 · 전체 (회계처리!C10)", got[acc].get("C10"), eng["b2"]))
    else:
        out.append(("배분 합계 (회계처리!C13)", got[acc].get("C13"), 100.0))
    return out


def fmt(x, d=4):
    return "—" if x is None else f"{x:,.{d}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario"); ap.add_argument("--out", required=True)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--sens"); ap.add_argument("--name"); ap.add_argument("--vol")
    ap.add_argument("--no-formula", action="store_true"); ap.add_argument("--no-check", action="store_true")
    a = ap.parse_args()
    G = load_app()
    scen = json.load(open(a.scenario, encoding="utf-8"))
    over = dict(s.split("=", 1) for s in a.set)
    pack = None
    if a.vol:
        pack = json.load(open(a.vol, encoding="utf-8"))
        if pack.get("kind") != "vol_pack": raise SystemExit("변동성 패키지가 아닙니다.")
        over.setdefault("sig", pack["sigma"])
    t = make_terms(G, scen, over)
    if pack:
        if pack.get('opt', {}).get('asof') != t.d_base:
            raise SystemExit('변동성 산출 기준일과 평가기준일이 다릅니다.')
        if pack.get('version') == 2:
            from valuation.market_data import validate_pack
            try:
                validate_pack(pack, asof=t.d_base)
            except (ValueError, TypeError, KeyError) as exc:
                raise SystemExit(str(exc)) from exc
        else:
            print('주의: 이전 변동성 패키지는 취득조건·전체 피어의 성공 여부를 검증할 수 없습니다. 새 변동성 화면에서 다시 산출하십시오.', file=sys.stderr)
    if G["is_sha"](t): raise SystemExit("주주간계약은 아직 이 도구에서 돌리지 않습니다 — 앱을 쓰십시오.")
    if len(t.rf_curve) < 2 or len(G["credit_curve"](t)) < 2: raise SystemExit("이자율 곡선이 없습니다.")
    if not a.no_formula and t.carry == 0 and t.rfx_mode > 0:
        raise SystemExit('상태확장 리픽싱은 같은 방법의 수식 조서를 지원하지 않습니다. --no-formula로 값 조서를 만들거나, 입력의 계산방법을 명시적으로 변경한 뒤 다시 실행하십시오.')
    os.makedirs(a.out, exist_ok=True)
    L = G["lbl"](t)
    name = a.name or f"{L['short']}평가{G['tranche_tag'](t)}_{t.d_base}"
    R = run(G, t)
    eir = G["eir_or_none"](t, R["full"], R["b0"], R["b1"], R["b2"], R["ca"])
    # 변동성 패키지가 있으면 산출내역 시트를 싣는다. 적용 σ 가 패키지 종합값과 다르면 앱과 같이
    # 시트만 남기고 트리에 잇지 않는다(build_xlsx 가 판단한다).
    px = ([(nm, [(d, float(v)) for d, v in rows]) for nm, rows in pack["series"]], pack["opt"]) \
        if pack else None
    att = dict(px=px, rate=None, rate_how="", ir=True)
    files, checks = [], []

    data = G["build_xlsx"](t, R["full"], R["b0"], R["b1"], R["b2"], R["ca"], R["conv"], eir, attach=att)
    p = os.path.join(a.out, f"{name}_값조서.xlsx"); open(p, "wb").write(data); files.append(p)
    checks.append(("값 조서 손상 검사", scan(data)))
    if not a.no_formula:
        tf = G["Terms"](**asdict(t))
        F = run(G, tf)
        data = G["build_xlsx_formula"](tf, F["full"], F["b0"], F["b1"], F["b2"], F["ca"], F["conv"],
                                       G["eir_or_none"](tf, F["full"], F["b0"], F["b1"], F["b2"], F["ca"]),
                                       attach=att)
        p = os.path.join(a.out, f"{name}_수식조서.xlsx"); open(p, "wb").write(data); files.append(p)
        checks.append(("수식 조서 손상 검사", scan(data)))
    sj = {**asdict(t), "_schema": G["SCHEMA_VER"],
          "_meta": {**G["run_stamp"](t), "scen_md5": G["scen_stamp"](asdict(t))}}
    p = os.path.join(a.out, f"{name}_시나리오.json")
    json.dump(sj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2); files.append(p)

    sc = None if a.no_check or a.no_formula else self_check(G, t)

    sens = []
    if a.sens:
        for row in json.load(open(a.sens, encoding="utf-8")):
            ts = make_terms(G, scen, {**over, **row.get("set", {})})
            rs = run(G, ts)
            sens.append((row["label"], rs))

    warn = G["validate"](t) or []
    md = [f"# 평가 결과 요약 — {name}", "",
          f"* 상품 {t.inst} · 평가기준일 {t.d_base} · 발행일 {t.d_issue} · 만기 {t.d_mat} · "
          f"잔존 {t.T:.2f}년 · 노드 {t.n}개 ({t.gap_m:g}개월 간격)",
          f"* 평가 관점 — {G['view_text'](t)}",
          f"* 주가 {t.S0:,.2f} · 전환가 {t.K0:,.2f} · 변동성 {t.sig:.2%} · 신용위험 처리 {t.model}",
          f"* 발행총액 {t.face_total:,.0f}원 (100 기준 금액 × {t.face_total/100:,.0f})",
          (f"* 변동성 패키지 — {pack.get('source','')} · {pack['made_at']} · 종합 {pack['sigma']:.2%} · "
           + " · ".join(f"{r[0]} {r[1]:.2%}" for r in pack.get("per_company", []))
           + ("" if abs(pack["sigma"] - t.sig) < 5e-5 else f" · **적용 σ {t.sig:.2%} 와 다름**"))
          if pack else "* 변동성 — 시나리오 값 (산출내역 시트 없음)", "",
          "## 구성요소 (100 기준)", "", "| 항목 | 100 기준 | 전액 (원) |", "|---|---:|---:|"]
    rows = [(L["host"].split(" (")[0] + " (옵션 없음)", R["b0"]), (L["put"], R["b1"] - R["b0"]),
            ("전환권", R["b2"] - R["b1"]), ("매도청구권 (차감)", -R["ca"]),
            ("합계 = 순포지션", R["net"])]
    md += [f"| {k} | {v:,.4f} | {v/100*t.face_total:,.0f} |" for k, v in rows]
    if sens:
        md += ["", "## 민감도", "", "| 경우 | 합계 (100 기준) | 전액 (원) |", "|---|---:|---:|"]
        md += [f"| {lb} | {rs['net']:,.4f} | {rs['net']/100*t.face_total:,.0f} |" for lb, rs in sens]
    if warn:
        md += ["", "## 앱의 확인 사항 (validate)", ""] + [f"- {w.replace('**', '')}" for w in warn]
    md += ["", "## 검사", ""]
    for nm, pr in checks:
        md.append(f"- {nm} — " + ("문제 없음" if not pr else "문제 " + str(len(pr)) + "건: " + "; ".join(pr[:5])))
    if sc is not None:
        ok = all(h is not None and abs(h - w) < 1e-4 for _, h, w in sc)
        md.append("- 수식 조서 자체 검산 (6개월 간격으로 엑셀 수식을 풀어 엔진과 대조) — "
                  + ("일치" if ok else "불일치") + " · "
                  + " · ".join(f"{n} 조서 {fmt(h)} / 엔진 {fmt(w)}" for n, h, w in sc))
    md += ["", "## 파일", ""] + [f"- {os.path.basename(f)}" for f in files]
    p = os.path.join(a.out, f"{name}_결과요약.md")
    open(p, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
