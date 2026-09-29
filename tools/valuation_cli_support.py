#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy import helpers kept for compatibility tests.

The runnable CLI lives in tools/run_case.py. Its --check-formulas verifies the
actual export. self_check here is the older reduced-grid regression helper.
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
            if k not in ("T", "n", "elapsed_m", "rem_m", "scen_md5", "gap_m", "grid_days")}
    over["_gap"] = 6.0
    over["grid_days"] = 0.
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
