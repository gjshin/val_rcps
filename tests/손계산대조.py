#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""손으로 센 기대값에 엔진을 맞춰 본다.

다른 대조 스크립트는 「엔진 = 엑셀 조서」를 본다. 둘이 **같이** 틀리면 통과한다.
실제로 이표 지급일이 그랬다 — 엔진과 조서가 같은 방식으로 평가기준일에서 세고
있어서 회귀 테스트가 통과했다.

그래서 이 파일의 기대값은 엔진 결과가 아니라 **계약에서 손으로 센 값**이다.
엔진을 고쳤다고 여기 숫자를 갱신하면 안 된다. 숫자가 틀렸다고 생각되면 계약과
손계산을 먼저 다시 보아야 한다.

    python3 tests/손계산대조.py
"""
import sys, os, types, math, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_app():
    stub = types.ModuleType("streamlit"); stub.cache_data = lambda **k: (lambda f: f)
    sys.modules["streamlit"] = stub
    m = types.ModuleType("cbapp"); sys.modules["cbapp"] = m
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], "app.py", "exec"), m.__dict__)
    return m.__dict__


G = load_app()
Terms, engine, derive = G["Terms"], G["engine"], G["derive"]
FAIL = []


def chk(tag, got, want, tol=1e-4):
    ok = got is not None and abs(got - want) < tol
    print("  %-52s %14s  기대 %12.4f  %s"
          % (tag, f"{got:.4f}" if got is not None else "없음", want,
             "OK" if ok else "★"))
    if not ok: FAIL.append(tag)


def chk_bool(tag, got, want=True):
    ok = bool(got) is bool(want)
    print("  %-52s %14s  기대 %12s  %s"
          % (tag, str(bool(got)), str(bool(want)), "OK" if ok else "★"))
    if not ok: FAIL.append(tag)


# ══════════════════════════════════════════════════════════
def test_coupon_schedule_after_elapsed_months():
    """이표 지급일은 발행일 기준이다.

    발행 2025-01-01 · 평가 2025-02-01 · 만기 2026-01-01, 분기 이표 연 12%,
    무위험·신용 이자율 0%. 계약상 남은 지급일은 2025-04-01·07-01·10-01·2026-01-01
    네 번이고 한 번에 3.0 씩이다. 만기에 원금 100 을 받으므로

        100 + 3.0 × 4 = 112.0000

    평가기준일에서 다시 세면 지급일이 한 번 사라져 109 가 된다.
    """
    print("\n[1] 이표 지급일 — 발행일 기준")
    t = Terms(inst="CB", d_issue="2025-01-01", d_base="2025-02-01",
              d_mat="2026-01-01", cpn=.12, ipay=3., ytm=0., ytm_cmp=0,
              gap_m=1., sig=.20, rfx_mode=0, cv_s=1., cv_e=11.,
              p_s=99., p_e=0., K0=1000., S0=100., par=100., floor=100.,
              rf_curve=[(1, 0.0), (5, 0.0)], cr_curve=[(1, 0.0), (5, 0.0)])
    derive(t)
    r = engine(t, conv=False, put=False, call=False)
    chk("B0 옵션 없는 사채 (이표 3.0 × 4회 + 원금 100)", r["TF"], 112.0)


def test_root_immediate_put():
    """평가기준일에 행사할 수 있고 유리하면 그 값이 공정가치다.

    조기상환 행사금액을 고정률 150 으로 두고 평가기준일(발행 12개월 뒤)에
    청구기간이 열려 있게 한다. 계속보유가치가 얼마든 공정가치는 150 이상이고,
    풋이 최적이므로 정확히 150 이다. TF 와 GS 가 같은 답을 내야 한다.
    """
    print("\n[2] 루트 즉시 행사 — TF 와 GS 가 같아야 한다")
    t = Terms(inst="CB", d_issue="2024-01-01", d_base="2025-01-01",
              d_mat="2027-01-01", cpn=0., ipay=3., ytm=0., ytm_cmp=0,
              gap_m=1., sig=.20, rfx_mode=0, cv_s=99., cv_e=0.,
              p_s=6., p_e=36., p_f=3., p_mode="fixed", p_rate=150.,
              K0=1000., S0=100., par=100., floor=100.,
              rf_curve=[(1, 0.0), (5, 0.0)], cr_curve=[(1, .10), (5, .10)])
    derive(t)
    r = engine(t, conv=False, put=True, call=False)
    root = r["memo"][r["root"]]
    chk("풋 행사금액 (계약)", root["pv"], 150.0)
    chk("TF", r["TF"], 150.0)
    chk("GS", r["GS"], 150.0)
    chk_bool("루트 정산 유형이 'put'", root["kind"] == "put")
    chk("정산 분포 · 상환 확률", r["dist"]["put"], 1.0)


def test_all_step_risk_neutral_probabilities():
    """구간마다 계산되는 위험중립가중치를 전부 본다.

    1년 2% · 2년 40% 곡선이면 뒤쪽 구간의 선도이자율이 튀어 q 가 1 을 넘는다.
    첫 구간만 보면 0.4999 로 멀쩡해 보인다. 엔진이 어긋난 구간을 짚어야 한다.
    """
    print("\n[3] 위험중립가중치 — 전 구간 검증")
    t = Terms(inst="CB", d_issue="2025-01-01", d_base="2025-01-01",
              d_mat="2027-01-01", cpn=0., ytm=0., ytm_cmp=0, gap_m=1.,
              sig=.20, rfx_mode=0, cv_s=1., cv_e=23., p_s=99., p_e=0.,
              K0=1000., S0=1000., par=100., floor=100.,
              rf_curve=[(1, .02), (2, .40)], cr_curve=[(1, .02), (2, .40)])
    derive(t)
    r = engine(t, conv=True, put=False, call=False)
    chk_bool("첫 구간 q 는 정상 범위 (그래서 첫 구간만 보면 안 된다)",
             0 < r["q"] < 1)
    chk_bool("어긋난 구간을 잡아낸다", len(r["qbad"]) > 0)
    chk_bool("전 구간 최대 q 가 1 을 넘는다", r["qmax"] > 1.0)
    chk_bool("qmin·qmax 가 qs 와 맞는다",
             abs(r["qmin"] - min(r["qs"])) < 1e-12
             and abs(r["qmax"] - max(r["qs"])) < 1e-12)


def test_current_k_and_original_cap():
    """상향 재조정의 상한은 **최초** 전환가액이다.

    최초 100 에서 이미 70 으로 하향 조정된 상품을 결산 평가한다. 계약은 조정
    후 전환가액이 최초 전환가액(100)을 넘지 못한다고 정하므로, 주가가 오르면
    전환가액이 100 까지 회복할 수 있어야 한다. 현재 전환가액 70 으로 상한을
    겸하면 70 에서 막힌다.
    """
    print("\n[4] 현재 전환가액과 최초 전환가액 상한 분리")
    t = Terms(inst="CB", d_issue="2024-01-01", d_base="2025-01-01",
              d_mat="2027-01-01", cpn=0., ytm=0., ytm_cmp=0, gap_m=1.,
              sig=.25, rfx_mode=2, rfx_cyc=3., carry=2,
              cv_s=1., cv_e=35., p_s=99., p_e=0.,
              K0=70., K_cap=100., floor=50., par=1., S0=90.,
              rf_curve=[(1, 0.)], cr_curve=[(1, .05)])
    derive(t)
    r = engine(t, conv=True, put=False, call=False)
    ks = [k for row in r["Kg"] for k in row]
    chk("격자 최대 전환가액 (상한 = 최초 전환가액)", max(ks), 100.0)
    chk_bool("현재 전환가액 70 을 넘는 노드가 있다", max(ks) > 70.0)

    # 상한을 비우면(음수) 종전처럼 현재 전환가액이 상한이다 — 하위호환
    t2 = Terms(**{**{k: getattr(t, k) for k in Terms.__dataclass_fields__},
                  "K_cap": -1.0})
    derive(t2)
    ks2 = [k for row in engine(t2, conv=True, put=False, call=False)["Kg"]
           for k in row]
    chk("상한을 비우면 현재 전환가액이 상한 (하위호환)", max(ks2), 70.0)


def test_refix_weighted_average_on_reset_date():
    """조정일에도 비조정일과 같은 방법으로 선행 전환가액을 이월한다.

    하향만 조정 + 확률가중평균 이월. 조정일에 선행 노드 하나만 집으면 전환가액이
    높게 잡혀 값이 낮아진다. 조정일 노드의 전환가액은 두 선행값을 q 로 가중한
    값에서 나와야 한다.
    """
    print("\n[5] 하향만 리픽싱 — 조정일 이월 방법 일관성")
    t = Terms(inst="CB", d_issue="2025-01-01", d_base="2025-01-01",
              d_mat="2028-01-01", cpn=0., ytm=0., ytm_cmp=0, gap_m=1.,
              sig=.30, rfx_mode=1, rfx_cyc=3., carry=2,
              cv_s=1., cv_e=35., p_s=99., p_e=0.,
              K0=100., floor=50., par=1., S0=100.,
              rf_curve=[(1, 0.)], cr_curve=[(1, .05)])
    derive(t)
    r = engine(t, conv=True, put=False, call=False)
    Kg, S = r["Kg"], r["S"]
    hits = 0
    for i in range(2, min(r["n"], 12)+1):
        q = r["qi"](i-1)
        for j in range(1, i):
            up, dn = Kg[i-1][j-1], Kg[i-1][j]
            if abs(up - dn) < 1e-9: continue
            prev = up*q + dn*(1-q)
            want = min(max(min(prev, S(i, j)), t.floor, t.par), t.K0)
            got = Kg[i][j]
            # 조정일이 아니면 그대로 이월이라 prev 와 같다. 어느 쪽이든
            # **두 선행값의 가중평균에서** 나와야 하고, dn 하나에서 나오면 안 된다.
            if abs(got - prev) < 1e-9 or abs(got - want) < 1e-9:
                if abs(got - dn) > 1e-9: hits += 1
            else:
                FAIL.append(f"노드({i},{j}) 이월값이 가중평균에서 나오지 않음")
                print(f"  ★ 노드({i},{j}) K={got:.4f} "
                      f"(선행 {up:.4f}/{dn:.4f}, 가중 {prev:.4f}, 조정후 {want:.4f})")
    chk_bool(f"선행 노드 하나만 집지 않는다 (확인 노드 {hits}개)", hits > 0)


def main():
    print("손계산 기대값 대조 — 기대값은 계약에서 센 값이다. 갱신하지 말 것.")
    test_coupon_schedule_after_elapsed_months()
    test_root_immediate_put()
    test_all_step_risk_neutral_probabilities()
    test_current_k_and_original_cap()
    test_refix_weighted_average_on_reset_date()
    print()
    if FAIL:
        print(f"★ 어긋남 {len(FAIL)}건")
        for f in FAIL: print("   -", f)
        return 1
    print("모두 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
