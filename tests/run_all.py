#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전체 검증 스위트를 한 명령으로 돈다 — 모형 검증 프로그램 7단계.

    python3 tests/run_all.py            # 전체 (한 시간 남짓 — 조서대조·설정전수가 길다)
    python3 tests/run_all.py --quick    # 빠른 세트 (몇 분) — PR 전에
    python3 tests/run_all.py --only 손계산대조,오라클

결과는 tests/output/validation_result.json 에 남긴다 — 시험마다 종료 코드·시간·마지막
줄, 그리고 기준선(docs/검증기준선.md §3) 값. 두 번 돌려 같은지 보려면 파일을 복사해 diff.
"""
import sys, os, json, time, subprocess, argparse, hashlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "output", "validation_result.json")

# (이름, 명령, 빠른 세트에 드나, 대략 시간)
SUITES = [
    ("손계산대조",   ["python3", "tests/손계산대조.py"],          True,  "1분"),
    ("오라클",       ["python3", "tests/오라클.py"],              True,  "1분"),
    ("분기전수",     ["python3", "tests/분기전수.py"],            True,  "1분"),
    ("기능목록",     ["python3", "tests/기능목록.py", "--strict"], True,  "2분"),
    ("조합시험",     ["python3", "tests/조합시험.py", "--quick"],  True,  "3분"),
    ("검산수식대조", ["python3", "tests/검산수식대조.py", "--quick"], True, "3분"),
    ("배선대조",     ["python3", "tests/배선대조.py"],            True,  "수 초"),
    ("리포트대조",   ["python3", "tests/리포트대조.py"],          True,  "2분"),
    ("값조서대조",   ["python3", "tests/값조서대조.py"],          False, "4분"),
    ("주주간계약대조", ["python3", "tests/주주간계약대조.py"],      False, "4분"),
    ("조합시험 전체", ["python3", "tests/조합시험.py"],           False, "15분"),
    ("조서대조",     ["python3", "tests/조서대조.py"],            False, "40분"),
    ("설정전수대조", ["python3", "tests/설정전수대조.py"],        False, "30분"),
]

BASELINE = dict(주계약=37.5208, 부채요소=73.1837, 전체=114.8781, 매도청구권=13.0762)


def baseline_now():
    """기준선 계약을 지금 코드로 재어 §3 값과 견준다 (carry=1)."""
    import types, warnings; warnings.filterwarnings("ignore")
    stub = types.ModuleType("streamlit"); stub.cache_data = lambda **k: (lambda f: f)
    sys.modules["streamlit"] = stub
    m = types.ModuleType("cbapp"); sys.modules["cbapp"] = m
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], "app.py", "exec"), m.__dict__)
    G = m.__dict__
    # 검증기준선 §3-1 의 «기본 계약» 은 설정전수대조.py 의 기준 — Terms() 기본값 · carry=1 · 노드 6개월
    t = G["Terms"](carry=1, gap_m=6.0, rf_curve=[(1, .0226), (3, .0240), (5, .0252)], cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    G["derive"](t); full, b0, b1, b2, ca, _ = G["decompose"](t)
    return dict(주계약=round(b0, 4), 부채요소=round(b1, 4), 전체=round(b2, 4), 매도청구권=round(ca, 4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true"); ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    todo = [s for s in SUITES if (s[0] in only if only else (s[2] or not a.quick))]
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    src_hash = hashlib.sha256(open(os.path.join(ROOT, "app.py"), "rb").read()).hexdigest()[:12]
    print(f"검증 스위트 — HEAD {head} · app.py {src_hash} · {len(todo)} 개" + (" (빠른 세트)" if a.quick else ""))
    res = []; t0 = time.time()
    for nm, cmd, _, est in todo:
        t1 = time.time(); print(f"▶ {nm} ({est}) …", end="", flush=True)
        pr = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = [x for x in (pr.stdout + pr.stderr).strip().splitlines() if "it/s]" not in x]   # tqdm 잡음 제외
        last = out[-1] if out else ""
        sec = round(time.time() - t1, 1)
        ok = pr.returncode == 0
        print(f" {'통과' if ok else '★ 실패'} · {sec}초 · {last[:90]}")
        res.append(dict(suite=nm, cmd=" ".join(cmd), ok=ok, code=pr.returncode, sec=sec, last=last,
                        tail=out[-12:] if not ok else []))
    base = baseline_now()
    base_ok = all(abs(base[k] - v) < 5e-5 for k, v in BASELINE.items())
    print(f"기준선 {base} — {'일치' if base_ok else '★ 어긋남 (docs/검증기준선.md §3 과 대조)'}")
    mx = {}
    try:
        M = json.load(open(os.path.join(ROOT, "tests", "검증매트릭스.json"), encoding="utf-8"))
        mx = M["summary"]
    except Exception: pass
    allok = all(r["ok"] for r in res) and base_ok
    doc = dict(generated=time.strftime("%Y-%m-%d %H:%M"), head=head, app_sha256_12=src_hash, quick=a.quick,
               python=sys.version.split()[0], suites=res, baseline=base, baseline_expected=BASELINE, baseline_ok=base_ok,
               matrix=mx, all_ok=allok, sec=round(time.time() - t0, 1))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"→ {os.path.relpath(OUT, ROOT)} · {'모두 통과' if allok else '★ 실패 있음'} · {doc['sec']}초")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
