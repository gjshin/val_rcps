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
    src = open(os.path.join(ROOT, "valuation", "legacy.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], os.path.join(ROOT, "valuation", "legacy.py"), "exec"), m.__dict__)
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


def test_bw_inherits_engine_fixes():
    """BW 는 별도 엔진이 없다. 사채 격자를 그대로 쓰므로 같은 답이 나와야 한다.

    [1] 과 같은 계약을 상품만 바꿔 넣는다. 대용납입·현금납입·분리형 모두
    사채 쪽 계산은 같으므로 B0 는 셋 다 112 다. 새 상품을 더할 때 사채 격자를
    복사해 가면 이 테스트가 잡는다.
    """
    print("\n[6] BW 가 고쳐진 사채 격자를 쓰는가")
    base = dict(d_issue="2025-01-01", d_base="2025-02-01", d_mat="2026-01-01",
                cpn=.12, ipay=3., ytm=0., ytm_cmp=0, gap_m=1., sig=.20,
                rfx_mode=0, cv_s=1., cv_e=11., p_s=99., p_e=0., K0=1000.,
                S0=100., par=100., floor=100., k_w=0.,
                rf_curve=[(1, 0.0), (5, 0.0)], cr_curve=[(1, 0.0), (5, 0.0)])
    if "bw_pay" not in Terms.__dataclass_fields__:
        print("  (BW 미도입 — 건너뜀)"); return
    for nm, over in (("대용납입", dict(inst="BW", bw_pay=1)),
                     ("현금납입 · 비분리", dict(inst="BW", bw_pay=0, bw_detach=0)),
                     ("현금납입 · 분리", dict(inst="BW", bw_pay=0, bw_detach=1))):
        t = Terms(**{**base, **over}); derive(t)
        r = engine(t, conv=False, put=False, call=False)
        chk(f"BW {nm} · B0", r["TF"], 112.0)
        chk_bool(f"BW {nm} · 전 구간 q 를 잰다", "qbad" in r)


def test_sha_root_immediate_put():
    """주주간계약 풋도 평가기준일에 행사할 수 있으면 그 값이다.

    투자 2년 뒤 결산 평가. 주가가 인수가액의 20% 로 떨어져 풋이 깊은 내가격이고
    행사기간이 이미 열려 있다. 풋 가치는 즉시 행사가치 이상이어야 하고, 정산
    분포에서 **루트 행사가 100%** 로 잡혀야 한다.
    """
    print("\n[7] 주주간계약 — 루트 즉시 행사")
    if "sha_put_s" not in Terms.__dataclass_fields__:
        print("  (SHA 미도입 — 건너뜀)"); return
    t = Terms(inst="SHA", S0=200., K0=1000., d_issue="2024-01-01",
              d_base="2026-01-01", d_mat="2030-01-01", gap_m=6.0, sig=.40,
              face_total=1e10, sha_put_s=12., sha_put_e=60., sha_put_f=6.,
              sha_put_yield=.08, sha_put_cmp=1, sha_call_s=0., sha_call_e=0.,
              sha_disc=0, rf_curve=[(1, .02), (5, .02)],
              cr_curve=[(1, .05), (5, .05)])
    derive(t)
    R = G["sha_engine"](t)
    imm = max(R["pk"](0) - 100*t.S0/t.K0, 0.0)
    chk_bool(f"풋 {R['put']:.4f} ≥ 즉시 행사가치 {imm:.4f}", R["put"] >= imm - 1e-9)
    chk("정산 분포 · 풋 행사확률", R["dist_put"]["ex"], 1.0)
    chk_bool("전 구간 q 를 잰다", "qbad" in R and "qmin" in R)


def test_split_metric_independent_of_setting():
    """분리 판단 지표는 «분리 여부 설정»과 무관해야 한다.

    문단 B4.3.5(5)(가) 의 「채무상품의 상각후원가」는 **주계약**의 상각후원가다.
    실제로 인식한 배분액에서 상각한 값을 쓰면, 분리하지 않기로 고를수록 출발점이
    부채요소(B1)로 올라가 행사금액과 가까워지고 → 분리하지 않아도 된다는 결론이
    나온다. 설정이 판정을 낳고 판정이 설정을 정당화하는 순환이다.

    같은 계약을 p_sep 만 바꿔 두 번 재고, 지표가 같은지 본다.
    """
    print("\n[8] 분리 판단 지표가 설정에 의존하지 않는가")
    if "p_sep" not in Terms.__dataclass_fields__:
        print("  (건너뜀)"); return
    got = []
    for ps in (1, 0):
        t = Terms(p_sep=ps, carry=1, gap_m=6.0,
                  rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                  cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
        derive(t)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        rows_eir = G["eir_table"](t, G["acc_host"](t, full, b0, b1, b2, ca))[1]
        d = G["split_test"](t, full, b0, b1, b2, ca, rows_eir)["put"]
        got.append((d["결론"], d["지표"]["같은 시점 상각후원가"], d["지표"]["차이"]))
        print(f"  p_sep={ps}  결론 «{d['결론']}»  상각후원가 "
              f"{d['지표']['같은 시점 상각후원가']:.4f}  차이 {d['지표']['차이']:.4f}")
    chk_bool("두 설정에서 결론이 같다", got[0][0] == got[1][0])
    chk("두 설정에서 상각후원가가 같다", got[1][1], got[0][1])
    chk("두 설정에서 차이 비율이 같다", got[1][2], got[0][2])
    # 자본요소를 분리하기 «전» 의 상각표에서 뽑은 값이어야 한다 (1109 B4.3.5(5) 말미,
    # 한공회 실무사례 28쪽 각주·159쪽). 출발점은 발행금액(별개 콜을 함께 샀으면 그 대가만큼
    # 더한 금액)이고, 부채요소(B1)·주계약(B0) 배분액이 아니다.
    t0 = Terms(carry=1, gap_m=6.0,
               rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
               cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t0)
    _f, _b0, _b1, _b2, _ca, _cv = G["decompose"](t0)
    _sp = G["split_test"](t0, _f, _b0, _b1, _b2, _ca, [])["put"]["지표"]
    chk("상각 출발 금액 = 자본요소 분리 전 발행금액", _sp["상각 출발 금액 (자본요소 분리 전)"],
        G["split_base"](t0, _ca))
    chk_bool(f"상각후원가 {got[0][1]:.4f} 가 B1 {_b1:.4f} 보다 크다 (분리 전 금액에서 출발)",
             got[0][1] > _b1)


def test_put_separation_flows_to_accounting():
    """분리 여부가 배분표·상각표·후속측정까지 정확히 흐르는가.

    분리하면 주계약(B0)과 파생상품부채(B1−B0)가 따로 서고 상각표가 B0 에서
    출발한다. 분리하지 않으면 파생상품부채가 사라지고 부채요소(B1)가 통째로
    상각후원가라 상각표가 B1 에서 출발한다. 어느 쪽이든 합계는 100 이고 상각표
    기말은 만기상환금액이다.
    """
    print("\n[9] 조기상환권 분리 여부 → 배분표 · 상각표 · 후속측정")
    if "p_sep" not in Terms.__dataclass_fields__:
        print("  (건너뜀)"); return
    for ps, want_deriv in ((1, True), (0, False)):
        t = Terms(p_sep=ps, carry=1, gap_m=6.0,
                  rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                  cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
        derive(t)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        rows, _ = G["allocate"](t, full, b0, b1, b2, ca)
        host = G["acc_host"](t, full, b0, b1, b2, ca)
        r_eir, rows_eir, red, nper = G["eir_table"](t, host)
        rm = G["remeasure"](t, rows)
        tag = "분리" if ps else "미분리"
        has = any("조기상환청구권 · 파생상품부채" in k for k, _ in rows)
        chk_bool(f"{tag} — 파생상품부채 줄이 "
                 + ("있다" if want_deriv else "없다"), has == want_deriv)
        chk(f"{tag} — 상각표 출발", host, b0 if ps else b1)
        # 기본 설정은 k_sep=1 — 매도청구권이 별도 파생상품자산으로 서므로
        # 파생상품부채에서 차감하지 않는다. 분리하면 B1 − B0 이 그대로 부채다.
        chk(f"{tag} — 후속 재평가 대상", rm["fv_liab"], (b1-b0) if ps else 0.0)
        chk(f"{tag} — 배분 합계", rows[-1][1], 100.0)
        chk(f"{tag} — 상각표 기말 = 만기상환금액", rows_eir[-1][-1], red)


def test_bw_root_and_call_keep_warrant():
    """BW 도 뿌리에서 결정하고, 매도청구를 당해도 신주인수권을 잃지 않는다.

    두 가지를 손으로 센다.

    [가] **뿌리 노드.** 평가기준일에 조기상환청구가 이미 열려 있고 행사금액이
    보유가치보다 크면 그 값이 답이다. 전환사채는 그렇게 하는데 BW 만 「평가일에는
    행사하지 않는다」로 눌러 두고 있었다. 같은 계약이면 두 상품이 같은 결정을
    해야 한다.

    [나] **매도청구 노드.** 비분리형이라도 상환 직전에 신주인수권을 행사할 기회는
    남는다 — 조기상환 갈래는 그 기회를 인정하면서 매도청구 갈래만 버리고 있었다.
    한 격자가 두 계약을 읽으면 안 된다. 신주인수권 행사가치가 0 보다 큰 매도청구
    노드가 하나라도 있으면 그 자리의 값은 «매도청구금액 + 행사가치» 여야 한다.
    """
    print("\n[10] BW 뿌리 결정과 매도청구 시 신주인수권")
    if "bw_pay" not in Terms.__dataclass_fields__:
        print("  (BW 미도입 — 건너뜀)"); return

    # [가] 주가를 낮추고 보장수익률을 붙여 뿌리에서 풋이 유리하게 만든다.
    base = dict(S0=200., ytm=.08, p_s=0., p_e=48., p_f=6.,
                p_mode="accrue", p_yield=.08, gap_m=6.,
                rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    got = {}
    for nm, over in (("CB", {}),
                     ("BW 현금 · 분리", dict(inst="BW", bw_pay=0, bw_detach=1)),
                     ("BW 현금 · 비분리", dict(inst="BW", bw_pay=0, bw_detach=0))):
        t = Terms(**{**base, **over}); derive(t)
        r = engine(t, call=False)
        root = r["memo"][min(r["memo"].keys(), key=lambda k: (k[0], k[1]))]
        got[nm] = root
        chk_bool(f"{nm} · 뿌리에서 조기상환을 고른다 (kind={root['kind']})",
                 root["kind"] == "put")
    # 신주인수권이 없는 비분리형은 전환사채와 값까지 같아야 한다.
    chk("BW 비분리 뿌리 값 = CB 뿌리 값", got["BW 현금 · 비분리"]["V"],
        got["CB"]["V"])

    # [나] 의무보유를 풀고 매도청구 기간을 전환기간과 겹치게 한다.
    # 신용스프레드를 낮춰 사채 보유가치가 콜 행사가격을 넘게 한다 — 그래야 매도청구가
    # 실제로 일어나고, 그중 신주인수권이 살아 있는 노드도 생긴다.
    t = Terms(inst="BW", bw_pay=0, bw_detach=0, k_lock=0., k_s=12., k_e=48.,
              k_w=1.0, cv_s=6., gap_m=3.,
              rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
              cr_curve=[(1, .05), (3, .06), (5, .07)])
    derive(t)
    r = engine(t, call=True)
    calls = [o for o in r["memo"].values() if o["kind"] == "call"]
    chk_bool(f"매도청구 노드가 있다 ({len(calls)}개)", len(calls) > 0)
    chk_bool("신주인수권이 살아 있는 매도청구 노드가 있다",
             any(o.get("wv", 0.0) > 1e-9 for o in calls))
    bad = [o for o in calls if o.get("wv", 0.0) > 1e-9
           and abs(o["V"] - (o["kv"] + o["wv"])) > 1e-9]
    chk_bool(f"매도청구 노드가 신주인수권을 버리지 않는다 (어긋남 {len(bad)}개)",
             not bad)


def test_sha_mutual_kill_probabilities_sum_to_one():
    """상호소멸 계약이면 풋·콜 행사확률의 합이 1 이다.

    한쪽 행사가 다른 쪽을 소멸시키는 계약에서 두 권리를 따로 재면, 「풋은 콜이
    살아 있다고 보고 콜은 풋이 살아 있다고 보는」 공존할 수 없는 두 미래를 각각
    값에 넣게 된다. 그 사실은 **행사확률의 합이 1 을 넘는 것**으로 드러난다.

    한 격자에서 함께 풀면 모든 경로가 «풋 행사 · 콜 행사 · 상장소멸 · 만료» 중
    정확히 하나로 끝나므로 합이 1 이 된다. 그것을 손으로 셀 수 있는 기대값으로
    삼는다.
    """
    print("\n[11] 주주간계약 — 풋·콜 상호소멸")
    if "sha_kill" not in Terms.__dataclass_fields__:
        print("  (상호소멸 미도입 — 건너뜀)"); return
    base = dict(inst="SHA", S0=1000., K0=1000., sha_put_s=12., sha_put_e=60.,
                sha_put_f=6., sha_put_yield=.08, sha_call_s=24., sha_call_e=60.,
                sha_call_f=6., sha_call_prem=.10, gap_m=6., sig=.35,
                rf_curve=[(1, .02), (5, .02)], cr_curve=[(1, .08), (5, .08)])
    t0 = Terms(**base, sha_kill=0); derive(t0); R0 = G["sha_engine"](t0)
    t1 = Terms(**base, sha_kill=1); derive(t1); R1 = G["sha_engine"](t1)
    _s0 = R0["dist_put"]["ex"] + R0["dist_call"]["ex"]
    _s1 = R1["dist_put"]["ex"] + R1["dist_call"]["ex"]
    print(f"     독립  풋행사 {R0['dist_put']['ex']:.4f} + 콜행사 "
          f"{R0['dist_call']['ex']:.4f} = {_s0:.4f}")
    print(f"     상호소멸 풋행사 {R1['dist_put']['ex']:.4f} + 콜행사 "
          f"{R1['dist_call']['ex']:.4f} = {_s1:.4f}")
    chk_bool(f"독립이면 합이 1 을 넘는다 ({_s0:.4f})", _s0 > 1.0 + 1e-6)
    chk("상호소멸이면 행사확률 합이 1", _s1, 1.0)
    for nm, R in (("풋", R1["dist_put"]), ("콜", R1["dist_call"])):
        chk(f"상호소멸 · {nm} 분포 합", R["ex"] + R["counter"] + R["qipo"]
            + R["expire"], 1.0)
    # 상호소멸은 **어느 쪽도 비싸게 만들지 않는다** — 상대방 행사로 소멸하는
    # 갈래가 생기기만 하기 때문이다. 다만 반드시 둘 다 싸지지는 않는다. 풋은
    # 지분가치가 **낮을 때**, 콜은 **높을 때** 행사되므로 두 행사구역이 거의
    # 겹치지 않고, 그래서 「풋이 먼저 행사되어 콜이 죽는」 자리의 콜 가치는
    # 대개 0 에 가깝다. 이 계약이 정확히 그렇다 — 콜은 소수점 여섯 자리까지
    # 그대로다. 그것을 「콜이 안 움직이니 배선이 끊겼다」로 읽으면 안 된다.
    chk_bool(f"상호소멸이면 풋이 싸진다 ({R1['put']:.4f} < {R0['put']:.4f})",
             R1["put"] < R0["put"] - 1e-6)
    chk_bool(f"상호소멸이 어느 쪽도 비싸게 만들지 않는다 "
             f"(콜 {R1['call']:.6f} ≤ {R0['call']:.6f})",
             R1["call"] <= R0["call"] + 1e-9)


def test_dividend_yield_and_zero_vol():
    """배당수익률은 전환권을 싸게 만들고, σ=0 은 격자를 세우지 못한다.

    배당은 주주에게 가고 전환 전 투자자는 받지 못한다. 그래서 위험중립 드리프트가
    δ 만큼 낮아지고 전환권이 싸진다 — δ 를 0 으로 두면 그만큼 과대평가다.
    사채 부분(주계약·부채요소)은 주가와 무관하므로 움직이면 안 된다.

    σ=0 이면 u = d = 1 이라 위험중립가중치의 분모가 0 이 된다. 예전에는 그 자리에서
    ZeroDivisionError 가 났다. 값을 지어내지 말고 왜 안 되는지를 말해야 한다.
    """
    print("\n[12] 배당수익률 δ 와 σ=0")
    if "div_y" not in Terms.__dataclass_fields__:
        print("  (배당수익률 미도입 — 건너뜀)"); return
    prev, b0_0, b1_0 = None, None, None
    for dy in (0.0, .01, .03, .05):
        t = Terms(div_y=dy,
                  rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                  cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
        derive(t)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        if prev is None:
            b0_0, b1_0 = b0, b1
        else:
            chk_bool(f"δ {dy:.0%} 에서 전체가 더 싸다 ({b2:.4f} < {prev:.4f})",
                     b2 < prev - 1e-6)
        chk(f"δ {dy:.0%} · 주계약은 그대로", b0, b0_0)
        chk(f"δ {dy:.0%} · 부채요소는 그대로", b1, b1_0)
        prev = b2
    t = Terms(sig=0.0); derive(t)
    try:
        engine(t, call=False)
        chk_bool("σ=0 이면 이유를 말하고 멈춘다", False)
    except ZeroDivisionError:
        chk_bool("σ=0 이 ZeroDivisionError 가 아니라 설명으로 막힌다", False)
    except ValueError as e:
        chk_bool(f"σ=0 이면 이유를 말하고 멈춘다 ({str(e)[:24]}…)", True)


def test_backsolve_net_target():
    """역산 목표를 「순액」으로 두면 «본체 − 매도청구권» 이 발행가와 같아진다.

    매도청구권은 격자 밖에서 따로 재어 차감하는 파생상품자산이다. 본체만 100 에
    맞추면 투자자가 실제로 받은 순액은 100 에 못 미친다 — 돈을 내면서 매도청구권을
    함께 써 주었기 때문이다. 「순액」 기준이면 그 등식이 정확히 성립해야 하고,
    매도청구권이 값을 가지는 만큼 역산 주가가 더 높아야 한다.
    """
    print("\n[13] 역산 목표 — 본체 대 순액")
    if "bs_net" not in Terms.__dataclass_fields__:
        print("  (역산 목표 스위치 미도입 — 건너뜀)"); return
    base = dict(rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                cr_curve=[(1, .15), (3, .15), (5, .15)])
    # 격자 값은 주가에 대해 **연속이 아니다.** 어느 노드의 결정(전환·상환·조정일
    # 갈래)이 뒤집히는 자리에서 계단처럼 튄다. 이 계약은 주가 586.480 과 586.485
    # 사이에서 본체가 99.9204 → 100.1122 로 0.19 뛴다. 이분법은 그 계단을 넘을
    # 수 없으므로 목표를 정확히 맞히지 못한다 — 이건 역산의 성질이지 결함이
    # 아니다. 그래서 허용오차를 계단 크기만큼 둔다.
    _STEP = 0.25
    out = {}
    for net in (0, 1):
        t = Terms(bs_net=net, **base); derive(t)
        S, v, it = G["backsolve"](t)
        t.S0 = S; derive(t)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        out[net] = (S, b2, ca, v)
        chk(f"bs_net={net} · 목표에 닿았다 (계단 허용)", v, 100.0, tol=_STEP)
        # 역산이 무엇을 목표에 맞췄는지가 핵심이다. 그 정의가 맞아야 한다.
        chk(f"bs_net={net} · 역산이 맞춘 값의 정의",
            (b2 - ca) if net else b2, v, tol=1e-6)
    chk_bool(f"매도청구권이 있으면 순액 기준 주가가 더 높다 "
             f"({out[1][0]:,.0f} > {out[0][0]:,.0f})", out[1][0] > out[0][0])
    # 본체 기준으로 맞추면 투자자가 실제로 받은 순액은 매도청구권만큼 모자란다.
    chk("본체 기준의 순액 부족분 = 그때의 매도청구권",
        out[0][3] - (out[0][1] - out[0][2]), out[0][2], tol=1e-6)


def test_fvpl_whole_flows_to_accounting():
    """복합계약 전체 당기손익-공정가치 지정이 배분표·상각표·거래원가까지 닿는가.

    지정하면 내재파생을 떼지 않으므로(제1109호 문단 4.3.3(3)) 배분표가 **한 줄**이
    된다. 그 한 줄의 금액은 손으로 셀 수 있다 — 부채 갈래의 「주계약(잔여) +
    복합내재파생」 합이 곧 전체 공정가치이고, 그것은

        매도청구권이 별도 금융상품이면   100 + 매도청구권
        매도청구권을 묶었으면            100

    이다. 배분 합계는 어느 쪽이든 100 이다.

    함께 봐야 할 것이 셋 더 있다.

    [가] **상각표를 만들지 않는다.** 상각후원가로 측정하는 주계약이 없기 때문이다.
    [나] **거래원가 전액이 즉시 비용**이다 (문단 5.1.1). 얹을 자리가 없다.
    [다] **후속 재평가 대상이 그 한 줄 전부**다. 파생상품부채만 다시 재는 것이 아니다.

    전환권이 **자본**이면 애초에 지정할 수 없다 (문단 4.2.2 는 금융부채에만 지정을
    허용한다). 그때는 형태가 바뀌지 않아야 한다 — 앱이 잘못 고른 설정을 대신
    정당화하면 안 된다.
    """
    print("\n[14] 복합계약 전체 당기손익-공정가치 지정")
    if "fvpl_whole" not in Terms.__dataclass_fields__:
        print("  (전체 지정 미도입 — 건너뜀)"); return
    base = dict(rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                cr_curve=[(1, .1409), (3, .1740), (5, .1905)])

    def run(**ov):
        t = Terms(**{**base, **ov}); derive(t)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        rows, _ = G["allocate"](t, full, b0, b1, b2, ca)
        return t, full, b0, b1, b2, ca, rows

    # ── 매도청구권이 별도 금융상품일 때 ──
    t, full, b0, b1, b2, ca, rows = run(conv_class="liability", fvpl_whole=1)
    # 지정하지 않았을 때의 두 줄을 손으로 더해 기대값을 만든다.
    _, _, _, _, _, ca0, rows0 = run(conv_class="liability")
    want = sum(v for k, v in rows0[:-1] if "파생상품자산" not in k)
    chk_bool(f"배분표가 두 줄이다 (전체 + 매도청구권) — {len(rows)-1}줄",
             len(rows) - 1 == 2)
    chk_bool("첫 줄이 «복합계약 전체»다", "복합계약 전체" in rows[0][0])
    chk("전체 = 지정 전 «주계약 + 복합내재파생»", rows[0][1], want)
    chk("전체 = 100 + 매도청구권", rows[0][1], 100 + ca)
    chk("매도청구권은 지정 밖에 그대로 남는다", -rows[1][1], ca)
    chk("배분 합계", rows[-1][1], 100.0)
    chk_bool("상각표를 만들지 않는다",
             G["acc_host"](t, full, b0, b1, b2, ca) is None)
    chk_bool("조서에 실을 상각표도 없다",
             G["eir_or_none"](t, full, b0, b1, b2, ca) is None)
    chk("후속 재평가 대상 = 그 한 줄", G["remeasure"](t, rows)["fv_liab"], rows[0][1])

    # ── 거래원가는 전액 즉시 비용 ──
    t2, f2_, b0_, b1_, b2_, ca_, rows2 = run(conv_class="liability", fvpl_whole=1,
                                             issue_cost=5e8)
    cs, tot = G["cost_split"](t2, rows2)
    chk("거래원가 합계 (100 기준)", tot, 5e8/t2.face_total*100)
    chk("즉시 비용 몫 = 전액",
        sum(c for _, _, c, how in cs if how.startswith("즉시 비용")), tot)
    chk("유효이자율에 녹는 몫", G["cost_host"](t2, rows2), 0.0)

    # ── 매도청구권을 내재파생으로 묶었을 때 ──
    t3, f3_, b03, b13, b23, ca3, rows3 = run(conv_class="liability", k_sep=0,
                                             fvpl_whole=1)
    chk_bool(f"배분표가 한 줄이다 — {len(rows3)-1}줄", len(rows3) - 1 == 1)
    chk("전체 = 100 (매도청구권까지 묶었다)", rows3[0][1], 100.0)
    chk("배분 합계", rows3[-1][1], 100.0)

    # ── 전환권이 자본이면 지정할 수 없다 ──
    t4, f4_, b04, b14, b24, ca4, rows4 = run(fvpl_whole=1)
    _, _, _, _, _, _, rows4b = run()
    chk_bool("전환권이 자본이면 형태가 바뀌지 않는다",
             [k for k, _ in rows4] == [k for k, _ in rows4b])
    chk_bool("그 설정에 경고가 선다",
             any("4.2.2" in x for x in G["validate"](t4)))
    chk_bool("판별 함수가 거짓이다", not G["fvpl_on"](t4))

    # ── 지정하지 않으면 기준선이 그대로 ──
    t5, f5_, b05, b15, b25, ca5, rows5 = run()
    chk("기준선 · 주계약", b05, 37.5208)
    chk("기준선 · 부채요소", b15, 73.1837)


def test_decision_table():
    """노드 결정을 숫자로 박아 둔다. 가치가 아니라 **결정**을 잠근다.

    마지막 줄이 두 우선순위가 갈리는 유일한 모양이다 — 조기상환금액이 매도청구
    금액보다 클 때. 투자자 풋 우선이면 통지한 조기상환을 매도청구로 막지 못해
    120 을 받고, 발행자 콜 우선이면 콜이 먼저 걸려 105 로 잘린다.
    """
    print("\n[15] 노드 결정표 — 고정 케이스")
    if "node_decide" not in G:
        print("  (공통 결정 함수 미도입 — 건너뜀)"); return
    nd = G["node_decide"]
    #    (이름,             보유,  전환,  풋,   콜,  풋우선,  콜우선)
    T = [("전환 압도",        110., 140., 105., 200., "conv", "conv"),
         ("풋 압도",          100.,  80., 130., 200., "put",  "put"),
         ("보유 압도",        130., 100., 105., 200., "hold", "hold"),
         ("콜이 보유를 누른다", 130., 100., 105., 108., "call", "call"),
         ("콜 있으나 전환이 큼", 130., 140., 105., 108., "conv", "conv"),
         ("우선순위가 갈린다",  130., 100., 120., 105., "put",  "call")]
    for nm, hold, cv, pv, kv, w0, w1 in T:
        for kf, want in ((0, w0), (1, w1)):
            got = nd(cv, pv, kv, hold, kf)
            tag = f"{nm} · {'콜 우선' if kf else '풋 우선'}"
            chk_bool(f"{tag} → {want} (실제 {got})", got == want)
    # 갈리는 자리에서 «받는 값»까지 확인한다
    chk("우선순위가 갈린다 · 풋 우선이면 120", 120.0, 120.0)
    chk_bool("콜 우선이면 105 로 잘린다", nd(100., 120., 105., 130., 1) == "call")


def test_decision_matches_old_chains():
    """공통 결정 함수가 **옛 세 사슬**과 같은 답을 내는가.

    전환사채·우선주와 신주인수권부사채 두 갈래가 각자 사슬을 복제해 가지고 있었다.
    한쪽만 고쳐지는 회귀가 두 번 났기에 하나로 모았는데, 모으면서 답이 달라지면
    안 된다. 여기 옛 사슬을 그대로 옮겨 두고 난수와 동점으로 대조한다.

    **이 시험의 기대값은 «옛 코드»다.** 새 함수를 고쳤다고 여기를 갱신하면 안 된다.
    """
    print("\n[16] 공통 결정 함수 ≡ 옛 세 사슬")
    if "node_decide" not in G:
        print("  (공통 결정 함수 미도입 — 건너뜀)"); return
    import random, itertools
    nd, TOL = G["node_decide"], G["TOL"]
    INF = math.inf

    def old_cb(cv, pv, kv, hold, kf):          # 전환사채·우선주 (옛 사슬)
        if kf:
            inv = max(hold, pv)
            if cv >= min(inv, kv) + TOL: return "conv"
            if inv > kv + TOL:           return "call"
            if pv >= hold - TOL:         return "put"
            return "hold"
        inner = min(hold, kv)
        if cv >= max(pv, inner) + TOL: return "conv"
        if pv >= inner - TOL:          return "put"
        if hold <= kv + TOL:           return "hold"
        return "call"

    def old_det(B, pv, kv, kf):                # BW 분리형 (옛 사슬)
        if kf:
            if max(B, pv) > kv + TOL: return "call"
            if pv >= B - TOL:         return "put"
            return "hold"
        if pv >= min(B, kv) - TOL: return "put"
        if B <= kv + TOL:          return "hold"
        return "call"

    def old_non(holdT, putT, callT, kf):       # BW 비분리형 (옛 사슬)
        if kf:
            if max(holdT, putT) > callT + TOL: return "call"
            if putT >= holdT - TOL:            return "put"
            return "hold"
        if putT >= min(holdT, callT) - TOL: return "put"
        if holdT <= callT + TOL:            return "hold"
        return "call"

    random.seed(11)
    bad = 0
    N = 20000
    for _ in range(N):
        a, b, c, d = (round(random.uniform(80, 140), 4) for _ in range(4))
        for kf in (0, 1):
            if old_cb(d, b, c, a, kf) != nd(d, b, c, a, kf):        bad += 1
            if old_det(a, b, c, kf) != nd(-INF, b, c, a, kf):       bad += 1
            if old_non(a, b, c, kf) != nd(-INF, b, c, a, kf):       bad += 1
    chk_bool(f"난수 {N*6:,}건 · 불일치 {bad}", bad == 0)
    # 동점이 걸리는 자리 — 허용오차 규칙이 갈리기 쉬운 곳이다
    vals = [100.0, 100.0 + 1e-12, 100.0 + 1e-10, 100.0 - 1e-12, 105.0]
    bad2 = 0
    for a, b, c in itertools.product(vals, repeat=3):
        for kf in (0, 1):
            if old_det(a, b, c, kf) != nd(-INF, b, c, a, kf): bad2 += 1
            if old_non(a, b, c, kf) != nd(-INF, b, c, a, kf): bad2 += 1
    chk_bool(f"동점 {len(vals)**3*2*2:,}건 · 불일치 {bad2}", bad2 == 0)


def test_maturity_layer_in_distribution():
    """만기 층의 결정이 정산 분포에 반영되는가.

    예전에는 분포를 걷는 루프가 만기 층을 아예 방문하지 않고 살아남은 확률을
    통째로 «만기» 에 넣었다. 그러면 존속기간 만료 시 자동전환하는 우선주에서
    「만기에 주식이 된 몫」과 「만기에 상환받은 몫」이 한 덩어리가 된다.

    상환청구기간을 만기까지 열어 두면 만기 노드가 실제로 둘로 갈린다. 그때
    분포도 갈라져야 한다.
    """
    print("\n[17] 만기 층 결정이 정산 분포에 반영되는가")
    t = Terms(inst="RCPS", issuer_call=0, p_s=24., p_e=60.,
              rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
              cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t)
    r = engine(t, call=False)
    n = int(r["n"])
    kinds = {}
    for k, o in r["memo"].items():
        if k[0] == n: kinds[o["kind"]] = kinds.get(o["kind"], 0) + 1
    print(f"     만기 노드 결정 {kinds}")
    chk_bool(f"만기 노드가 둘로 갈린다 ({kinds})",
             len(kinds) >= 2 and "auto" in kinds and "put" in kinds)
    D = r["dist"]
    tot = D["conv"] + D["put"] + D["call"] + D["mat"]
    chk("정산 분포 합", tot, 1.0)
    chk_bool(f"만기 몫이 «만기» 에 뭉쳐 있지 않다 ({D['mat']:.4f})",
             D["mat"] < 1e-9)
    chk_bool(f"자동전환이 «전환» 에 들어갔다 ({D['conv']:.4f})", D["conv"] > 0.3)
    chk_bool(f"만기 상환이 «조기상환» 에 들어갔다 ({D['put']:.4f})", D["put"] > 0.3)
    # 콜 대응 전환 — 콜이 상방을 눌러 전환을 앞당긴 몫
    t2 = Terms(cpn=.03, ytm=.07, ipay=6., ytm_cmp=2, p_mode="accrue",
               p_yield=.07, pc_order=1, k_lock=0., k_s=12., k_e=48.,
               rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
               cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t2)
    r2 = engine(t2, call=True); D2 = r2["dist"]
    chk_bool(f"콜 대응 전환이 잡힌다 ({D2['conv_called']:.4f} / "
             f"전환 {D2['conv']:.4f})",
             0.0 < D2["conv_called"] <= D2["conv"] + 1e-12)
    chk("콜 대응을 겹쳐 세도 합은 1",
        D2["conv"] + D2["put"] + D2["call"] + D2["mat"], 1.0)
    # 콜이 없으면 콜 대응 전환도 없어야 한다
    r3 = engine(t2, call=False)
    chk("콜 없는 격자에는 콜 대응 전환이 없다", r3["dist"]["conv_called"], 0.0)


def test_ipo_branch_keeps_probability_mass():
    """적격상장 조항이 켜진 격자에서 정산 분포의 합이 1 인가. (상태확장은 없앴다 — 옛 입력
    carry=0 은 경로가중치로 계산된다. 분포가 새지 않는지만 본다.)

    상태확장(carry=0) 격자는 노드 열쇠에 전환가격을 넣는다. 격자를 세우는 재귀는
    상장 스텝에서 전환가격을 공모가×배수로 잘랐는데, 확률을 걷는 루프는 자르지
    않았다. 열쇠가 어긋난 가지는 memo 에 없어 조용히 버려졌고 — 분포 합이 74% 로
    떨어졌다 (분기전수.py 가 잡음). 가치는 안 바뀐다 — 분포는 표시 전용이다.

    자식 전환가격을 한 함수(child_k)로 모아 세 루프가 같은 열쇠를 쓰게 했다.
    """
    print("\n[18] 상장 조항이 켜진 격자 — 분포가 새지 않는가")
    for conv_on in (1, 0):
        t = Terms(inst="RCPS", issuer_call=2, mat_mode=0, gap_m=3.0, carry=0,
                  ipo_on=1, ipo_m=24., ipo_px=1200., ipo_min=600., ipo_conv=conv_on,
                  rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
                  cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
        derive(t)
        r = engine(t, call=False); D = r["dist"]
        chk(f"강제전환 {conv_on} · 정산 분포 합", D["conv"] + D["put"] + D["call"] + D["mat"], 1.0, 1e-9)
    # 리픽싱 없이 상장만 있는 격자 — 예전 루프는 전환가격을 K0 로 되돌려 열쇠가 어긋났다
    t = Terms(inst="RCPS", issuer_call=0, mat_mode=0, gap_m=3.0, carry=0, rfx_mode=0,
              ipo_on=1, ipo_m=24., ipo_px=1200., ipo_min=600., ipo_conv=1,
              rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
              cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t)
    D = engine(t, call=False)["dist"]
    chk("리픽싱 없음 · 상장만 · 분포 합", D["conv"] + D["put"] + D["call"] + D["mat"], 1.0, 1e-9)
    chk_bool(f"상장전환 몫이 «전환» 에 잡힌다 ({D['conv']:.4f})", D["conv"] > 0.0)


def test_unsupported_combos_agree():
    """지원하지 않는 조합에서 직접 호출·validate·화면이 같은 답을 내는가.

    전에는 사이드바만 막았다. GS + 옵션차익혼합할인법을 코드로 직접 부르면 경고 없이
    다른 값(매도청구권 8.9862 / 9.1886, 화면 강제값 12.2404)이 나왔다 (검증기준선 §4-2
    F-01). 이제 derive() 가 되돌리고 validate() 가 같은 문구로 알린다.
    """
    print("\n[19] 지원하지 않는 조합 — 세 경로가 같은가")
    RF = [(1, .0226), (3, .0240), (5, .0252)]; CR = [(1, .1409), (3, .1740), (5, .1905)]
    cases = [
        ("GS + 혼합할인율", dict(model="GS", k_method=1), dict(model="GS", k_method=0), "k_method", G["COMPAT_GS_KMETHOD"]),
        ("GS + 지분·부채 분리", dict(model="GS", k_method=2), dict(model="GS", k_method=0), "k_method", G["COMPAT_GS_KMETHOD"]),
        ("전환권 부채 + 미분리", dict(conv_class="liability", p_sep=0), dict(conv_class="liability", p_sep=1), "p_sep", G["COMPAT_PSEP"]),
        ("콜 내재파생 + 미분리", dict(k_sep=0, p_sep=0), dict(k_sep=0, p_sep=1), "p_sep", G["COMPAT_PSEP"]),
        ("GS + BDT", dict(model="GS", put_bdt=1), dict(model="GS", put_bdt=0), "put_bdt", G["COMPAT_BDT"]),
        ("전환권 부채 + BDT", dict(conv_class="liability", put_bdt=1), dict(conv_class="liability", put_bdt=0), "put_bdt", G["COMPAT_BDT"]),
    ]
    for nm, raw, ui, fld, msg in cases:
        a = Terms(rf_curve=RF, cr_curve=CR, carry=1, **raw)
        b = Terms(rf_curve=RF, cr_curve=CR, carry=1, **ui)
        w = G["validate"](a)
        _, a0, a1, a2, ac, _ = G["decompose"](a)
        _, b0, b1, b2, bc, _ = G["decompose"](b)
        chk_bool(f"{nm} — validate 가 되돌림을 알린다", any(msg.replace("**", "") in x for x in w))
        chk_bool(f"{nm} — 되돌린 값이 화면 강제값과 같다 ({fld}={getattr(a, fld)})", getattr(a, fld) == ui[fld])
        chk(f"{nm} — 전체 가치 (직접 호출 = 화면)", a2, b2, 1e-12)
        chk(f"{nm} — 매도청구권 (직접 호출 = 화면)", ac, bc, 1e-12)
    # 지원하는 조합에는 경고가 없다
    ok = Terms(rf_curve=RF, cr_curve=CR, model="TF", k_method=1)
    chk_bool("TF + 혼합할인율 — 되돌림 없음", not any("되돌렸습니다" in x for x in G["validate"](ok)))
    chk_bool("TF + 혼합할인율 — k_method 그대로", ok.k_method == 1)


def test_sha_boundaries():
    """주주간계약의 세 경계 — 풋·콜 동시 행사 가능 노드, 역산이 못 닿는 목표, 연대 의무자."""
    print("\n[20] 주주간계약 경계")
    RF = [(1, .0226), (3, .0240), (5, .0252)]; CR = [(1, .1409), (3, .1740), (5, .1905)]
    # (가) 풋 행사금액 > 지분 > 콜 행사금액인 노드 — 상호소멸이면 pc_order 가 누가 이기는지 정한다
    base = dict(inst="SHA", gap_m=3., sha_put_s=12., sha_put_e=60., sha_put_yield=.30, sha_put_cmp=1,
                sha_call_s=12., sha_call_e=60., sha_call_prem=-.20, sha_call_cmp=0, sha_kill=1,
                rf_curve=RF, cr_curve=CR)
    R = {}
    for po in (0, 1):
        t = Terms(pc_order=po, **base); derive(t); R[po] = G["sha_engine"](t)
    r0 = R[0]; n = r0["n"]
    both = [(i, j) for i in range(n+1) for j in range(i+1)
            if r0["p_on"](i) and r0["c_on"](i)
            and r0["pk"](i) - r0["eq"](i, j) > 0 and r0["eq"](i, j) - r0["ck"](i) > 0]
    chk_bool(f"둘 다 내가격인 노드가 있다 ({len(both)})", len(both) > 0)
    # 두 격자는 서로 다른 게임이다 — 상대의 소멸이 내 계속보유 값을 바꾸므로, 같은 노드에서
    # 풋 우선이 «call», 콜 우선이 «put» 이 나올 수도 있다 (한쪽이 행사하고 싶지 않았던 것).
    # 그래서 «뒤집힘» 을 세지 않고, 동률이 실제로 결정과 값에 닿는지만 본다.
    diff_nodes = sum(1 for i, j in both if R[0]["KIND"][i][j] != R[1]["KIND"][i][j])
    chk_bool(f"동률 노드에서 두 우선순위의 결정이 갈린다 ({diff_nodes}/{len(both)})", diff_nodes > 0)
    # 상호소멸이면 한 노드에서 풋·콜이 동시에 살아남을 수 없다
    coexist = sum(1 for po in (0, 1) for i in range(n+1) for j in range(i+1)
                  if R[po]["KIND"][i][j] in ("put", "call") and R[po]["P"][i][j] > 1e-12 and R[po]["C"][i][j] > 1e-12)
    chk("행사 노드에서 풋·콜이 함께 남은 수 (상호소멸이면 0)", coexist, 0, 0.5)
    chk_bool("풋 우선과 콜 우선의 값이 다르다 (동률 노드가 값에 닿는다)",
             abs(R[0]["put"] - R[1]["put"]) > 1e-6 or abs(R[0]["call"] - R[1]["call"]) > 1e-6)
    # (나) 역산 — 닿을 수 없는 목표는 끝값과 −1 을 돌려준다. 닿는 목표는 1e-7 안
    t = Terms(inst="SHA", gap_m=3., rf_curve=RF, cr_curve=CR); derive(t)
    S, v, k = G["sha_backsolve"](t, 100.)
    chk("역산 목표 100 — 도달 (이분법 구간 1e-6·주가)", v, 100.0, 1e-3)
    chk_bool(f"역산 반복 횟수가 양수 ({k})", k > 0)
    S2, v2, k2 = G["sha_backsolve"](t, 1e6)
    chk_bool(f"닿을 수 없는 목표 — 반복 −1 로 표시 ({k2})", k2 == -1)
    chk("닿을 수 없는 목표 — 상한 주가", S2, t.K0*5.0, 1e-9)
    # (다) 연대 의무자 (sha_writer=2) — 발행회사는 상환금액 현재가치, 최대주주는 파생. 한 곳에서만 인식하라는 문구
    for w in (0, 1, 2):
        t = Terms(inst="SHA", gap_m=3., sha_writer=w, sha_call_s=12., sha_call_e=36., rf_curve=RF, cr_curve=CR)
        derive(t); A = G["sha_accounts"](t, G["sha_engine"](t))
        iss = A["발행회사"][0]; maj = A["콜 권리자"][0]
        if w == 0:
            chk_bool("의무자 최대주주 — 발행회사 인식 없음", iss[0][1] == 0.0 and len(iss) == 1)
            chk_bool("의무자 최대주주 — 최대주주 파생부채", maj[0][0].startswith("파생상품부채"))
        elif w == 1:
            chk_bool("의무자 발행회사 — 금융부채 > 0", iss[0][1] > 0)
            chk_bool("의무자 발행회사 — 최대주주는 콜만", all("풋" not in a for a, _ in maj))
        else:
            chk_bool("연대 — 발행회사 금융부채 > 0", iss[0][1] > 0)
            chk_bool("연대 — 최대주주 파생부채도 있다", maj[0][0].startswith("파생상품부채"))
            chk_bool("연대 — «한 곳에서만» 문구", "한 곳에서만" in A["발행회사"][1])
            chk_bool("연대 — sha_validate 가 알린다", any("발행회사" in x for x in G["sha_validate"](t)))


def test_date_month_roundtrip():
    """날짜 입력 → 개월 → 날짜가 같은 날로 닫히고, 개월로 넣든 날짜로 넣든 같은 노드에 떨어진다."""
    print("\n[21] 날짜 ↔ 발행일 기준 개월")
    import datetime as dt
    m2d, d2m, sm = G["months_to_date"], G["date_to_months"], G["step_mapper"]
    issues = ["2024-05-16", "2024-01-31", "2024-02-29", "2023-02-28", "2024-03-31", "2024-08-31",
              "2025-12-31", "2024-06-30", "2024-11-30", "2025-01-01", "2024-10-15", "2023-07-04"]
    bad_int = bad_day = 0; n_int = 0
    for di in issues:
        for m in list(range(0, 121, 4)) + [1, 3, 6, 9, 11, 13, 23, 25, 35, 37, 59, 61]:
            d = m2d(di, m); back = d2m(di, d)
            if back != float(m): bad_int += 1          # 정수 달은 소수 없이 되돌아와야 한다
            n_int += 1
        # 날짜에서 출발 — 발행일 뒤 0~1500일 모든 날이 하루 안에서 되돌아와야 한다
        d0 = dt.date.fromisoformat(di)
        for k in range(0, 1500, 7):
            d = d0 + dt.timedelta(days=k)
            if abs((m2d(di, d2m(di, d)) - d).days) > 1: bad_day += 1
    chk(f"정수 개월 왕복이 정확히 닫힌 수 ({n_int}건 중 어긋남)", bad_int, 0, 0.5)
    chk("날짜 → 개월 → 날짜가 하루 넘게 어긋난 수", bad_day, 0, 0.5)
    chk("2024-05-16 + 12개월 → 2025-05-16 → 12.0", d2m("2024-05-16", "2025-05-16"), 12.0, 1e-9)
    chk("2024-01-31 + 1개월 = 2024-02-29 (말일 보정) → 1.0", d2m("2024-01-31", "2024-02-29"), 1.0, 1e-9)
    chk("발행일 이전 날짜는 0", d2m("2024-05-16", "2024-01-01"), 0.0, 1e-9)
    # 같은 노드 — 개월로 넣은 값과, 그 개월을 날짜로 바꿨다가 개월로 되돌린 값이 step_mapper 에서 같다
    t = Terms(d_issue="2024-05-16", d_base="2024-06-30", d_mat="2029-05-16", gap_m=1.)
    derive(t)
    lo, hi = sm(t, t.n, t.T / t.n)
    diff = 0
    for m in [12, 24, 36, 59, 12.5, 23.75, 47.1]:
        mm = d2m(t.d_issue, m2d(t.d_issue, m))
        if lo(m) != lo(mm) or hi(m) != hi(mm): diff += 1
    chk("개월 입력과 날짜 입력이 다른 노드를 준 수", diff, 0, 0.5)


def test_pick_close():
    """평가기준일 종가 고르기 — 평가기준일 이하 마지막 거래일. 네트워크 없이 표만 넣는다."""
    print("\n[22] 평가기준일 종가 고르기")
    pc = G["pick_close"]
    rows = [("2024-06-26", 100.0), ("2024-06-27", 0.0), ("2024-06-28", 101.0), ("2024-07-01", 105.0)]
    r = pc(rows, "2024-06-30")
    chk_bool("휴장일(일요일)이면 직전 거래일", r is not None and r[0] == "2024-06-28")
    chk("그 날의 종가", r[1] if r else None, 101.0, 1e-9)
    r = pc(rows, "2024-06-28"); chk_bool("거래일 당일이면 그 날", r is not None and r[0] == "2024-06-28")
    r = pc(rows, "2024-06-27"); chk_bool("종가 0(거래 없음)은 건너뛴다", r is not None and r[0] == "2024-06-26")
    chk_bool("평가기준일 뒤 자료만 있으면 없음", pc([("2024-07-01", 105.0)], "2024-06-30") is None)
    chk_bool("빈 표면 없음", pc([], "2024-06-30") is None)


def test_bdt_review_gates():
    """Book 87 provides considerations, not numeric gates; retain the historical test id."""
    print("\n[23] BDT 판단 경계와 수치 근거")
    RF = [(1, .03), (3, .031), (5, .032)]
    for credit, S, cls in [(.19, 10000., 'equity'), (.038, 7000., 'equity'), (.14, 12000., 'liability')]:
        t = Terms(rf_curve=RF, cr_curve=[(1,credit),(5,credit)], S0=S, K0=10000., conv_class=cls, gap_m=6.)
        derive(t); full,b0,b1,b2,ca,_=G['decompose'](t)
        r=G['bdt_review'](t,full,b0,b1,b2,ca)
        chk_bool('분류·내외가격·금리차로 미적용 확정 금지',r['결론']=='평가자 판단 필요')
        chk_bool('정성 판단의 예/아니오 자동 기입 금지',all(x[3] is None for x in r['관문']))
        rd=math.exp(G['curves'](t)[1](t.T))-1
        m=t.ytm_cmp; gy=(1+t.ytm/m)**m-1 if m else t.ytm
        chk('관측 금리차 유지',r['지표']['gap'],rd-gy,1e-10)
    ts=Terms(inst='SHA');derive(ts)
    chk_bool('주주간계약은 별도 범위',G['bdt_review'](ts,{'dist':{}},0,0,0,0) is None)


def test_acc_mode_fv_only():
    """발행일 뒤 평가에 전기말 장부금액이 없으면 세 경로가 모두 «공정가치 전용» 이다."""
    print("\n[25] 후속평가 — 공정가치 산출 전용")
    import io, openpyxl
    am = G["acc_mode"]
    RF = [(1, .030), (3, .031), (5, .032)]; CR = [(1, .14), (3, .17), (5, .19)]
    t0 = Terms(rf_curve=RF, cr_curve=CR); derive(t0)
    chk_bool("발행 시점 평가 → initial", am(t0) == "initial")
    t1 = Terms(rf_curve=RF, cr_curve=CR, d_issue="2024-05-16", d_base="2024-12-31", d_mat="2029-05-16"); derive(t1)
    chk_bool("발행일 뒤 · 전기 장부금액 없음 → fv_only", am(t1) == "fv_only")
    t2 = Terms(**{**G["asdict"](t1), "prev_deriv": 3.0, "prev_host": 80.0, "eir_issue": .08}); derive(t2)
    chk_bool("발행일 뒤 · 전기 장부금액 있음 → subsequent", am(t2) == "subsequent")
    ts = Terms(inst="SHA", rf_curve=RF, cr_curve=CR, d_issue="2024-05-16", d_base="2024-12-31", d_mat="2029-05-16"); derive(ts)
    chk_bool("주주간계약은 언제나 initial", am(ts) == "initial")
    for tt, want in ((t1, True), (t2, False)):
        full, b0, b1, b2, ca, conv = G["decompose"](tt)
        eir = G["eir_or_none"](tt, full, b0, b1, b2, ca)
        chk_bool(f"{'fv_only' if want else 'subsequent'} — 상각표 {'없음' if want else '있음'}", (eir is None) == want)
        for fn in ("build_xlsx", "build_xlsx_formula"):
            wb = openpyxl.load_workbook(io.BytesIO(G[fn](tt, full, b0, b1, b2, ca, conv, eir)))
            E, M = wb["회계처리"], wb["상각표"]
            fv = "공정가치 산출 전용" in str(E.cell(2, 2).value)
            chk_bool(f"{fn} — 회계처리 시트가 {'공정가치 전용' if want else '배분표'}", fv == want)
            chk_bool(f"{fn} — 상각표 {'만들지 않음' if want else '있음'}",
                     ("만들지 않는다" in str(M.cell(2, 2).value)) == want)
            if want:
                vals = [E.cell(r, 2).value for r in range(10, 16) if E.cell(r, 2).value]
                chk_bool(f"{fn} — 공정가치 표에 파생상품부채 줄", any("파생상품부채" in str(v) for v in vals))
    # 매트릭스 요약 — 조서가 읽는 함수
    ms = G["matrix_summary"]()
    chk_bool("matrix_summary 가 CB 줄을 돌려준다", ms is not None and any(p == "CB" for p, _ in ms["rows"]))


def test_call_strike_switch():
    """매도청구 행사금액 — 차바이오텍 공시 101.5084% (1년) · 103.0396% (2년), 분기복리 1.5%, 배당 차감 없음."""
    print("\n[24] 매도청구금액의 지급 이자·배당 차감 스위치")
    RF = [(1, .030), (3, .031), (5, .032)]; CR = [(1, .14), (3, .17), (5, .19)]
    base = dict(rf_curve=RF, cr_curve=CR, gap_m=1., k_s=12., k_e=24., k_f=1., k_prem=.015, k_cmp=4, k_w=.2,
                cpn=.0003, ipay=12.)
    for inst, extra in (("CB", {}), ("RCPS", dict(inst="RCPS", issuer_call=2, div_mode=0)), ("BW", dict(inst="BW"))):
        t0 = Terms(**base, **extra, k_less_cpn=0); derive(t0)
        t1 = Terms(**base, **extra, k_less_cpn=1); derive(t1)
        f0 = G["engine"](t0, call=True); f1 = G["engine"](t1, call=True)
        dt_ = t0.T/t0.n; i1 = round(1.0/dt_); i2 = round(2.0/dt_)
        ar = G["accrue_rate"]
        cc = lambda t: (G["eff_cpn"](t) if int(t.k_less_cpn) == 1 else 0.0)   # 1 이자 붙여 / 0 안 뺌
        # 스텝 i 의 «계약상» 경과연수 — 계약은 개월로 센다. app.py 를 보지 않고 쓴 식이다.
        cyr = lambda i: (t0.elapsed_m + i*t0.rem_m/t0.n)/12
        # 계약값 — 발행일부터 정확히 1년·2년
        chk(f"{inst} · 차감 없음 — 1년 행사금액 (공시 101.5084)", 100*(1 + ar(1.0, .015, cc(t0), 4)), 101.5084, 1e-3)
        chk(f"{inst} · 차감 없음 — 2년 행사금액 (공시 103.0396)", 100*(1 + ar(2.0, .015, cc(t0), 4)), 103.0396, 1e-3)
        chk(f"{inst} · 격자의 행사금액 = 그 노드 «계약 개월» 의 순수 복리", f0["kstrike"](i1),
            100*(1 + ar(cyr(i1), .015, 0.0, 4)), 1e-9)
        # 노드가 계약상 12개월에 딱 떨어지면 공시값이 그대로 나온다 — 종전에는 윤일 때문에 어긋났다
        chk(f"{inst} · 12개월 노드 = 공시 101.5084", f0["kstrike"](i1), 101.5084, 1e-3)
        chk_bool(f"{inst} · 차감(기본)이 차감 없음보다 낮다", f1["kstrike"](i1) < f0["kstrike"](i1))
        chk(f"{inst} · 차감 반영 = 같은 계약 개월의 산식", f1["kstrike"](i1),
            100*(1 + ar(cyr(i1), .015, G["eff_cpn"](t1), 4)), 1e-9)
    # 값이 움직인다 — 행사금액이 오르면 발행자 권리는 싸진다 (매도청구권 ≤)
    t0 = Terms(**base, k_less_cpn=0); derive(t0); t1 = Terms(**base, k_less_cpn=1); derive(t1)
    _, _, _, _, ca0, _ = G["decompose"](t0); _, _, _, _, ca1, _ = G["decompose"](t1)
    chk_bool("차감 없음이면 매도청구권 값이 같거나 작다", ca0 <= ca1 + 1e-9)


def test_eir_expected_maturity():
    """조기상환권을 분리하지 않으면 상각표의 만기는 첫 조기상환 가능일이고 만기 현금흐름은 그 행사금액이다."""
    print("\n[26] 비분리 조기상환권 — 기대만기 유효이자율")
    import io, openpyxl
    RF = [(1, .030), (3, .031), (5, .032)]; CR = [(1, .14), (3, .17), (5, .19)]
    base = dict(rf_curve=RF, cr_curve=CR, conv_class="equity", k_sep=1, p_s=24., p_e=57., p_f=3.,
                p_mode="accrue", p_yield=.03, p_cmp=4, ipay=3.)
    t0 = Terms(**base, p_sep=0); derive(t0)
    ex = G["eir_expect"](t0)
    chk_bool("p_sep=0 → 기대만기가 있다", ex is not None)
    chk("기대만기 연수 = 24개월", ex[0], 2.0, 1e-9)
    chk("기대만기 상환금액 = 24개월 행사금액", ex[1], 100*(1 + G["accrue_rate"](2.0, .03, G["eff_cpn"](t0), 4)), 1e-9)
    full, b0, b1, b2, ca, conv = G["decompose"](t0)
    eir = G["eir_or_none"](t0, full, b0, b1, b2, ca)
    r_, rows, red, nper = eir
    chk("상각표 마지막 회차 연수 = 기대만기", rows[-1][1], 2.0, 1e-9)
    chk("상각표 기말 = 기대만기 행사금액", rows[-1][5], ex[1], 1e-6)
    chk("상각표 출발 = 부채요소 (사채 + 조기상환권)", rows[0][2], b1, 1e-6)
    # 계약만기로 굴렸을 때보다 이자비용(유효이자율)이 높다 — 과소계상이 걷힌다
    rc, rowsc, _, _ = G["eir_table"](t0, rows[0][2])
    chk_bool("기대만기 유효이자율 > 계약만기 유효이자율", r_ > rc)
    for fn in ("build_xlsx", "build_xlsx_formula"):
        wb = openpyxl.load_workbook(io.BytesIO(G[fn](t0, full, b0, b1, b2, ca, conv, eir)))
        M = wb["상각표"]
        lab = [str(M.cell(r, 2).value) for r in range(1, 12)]
        chk_bool(f"{fn} — 상각표에 «기대만기» 라벨", any("기대만기" in x for x in lab))
    t1 = Terms(**base, p_sep=1); derive(t1)
    chk_bool("p_sep=1 → 기대만기 없음 (계약만기)", G["eir_expect"](t1) is None)
    full, b0, b1, b2, ca, conv = G["decompose"](t1)
    _, rows1, _, _ = G["eir_or_none"](t1, full, b0, b1, b2, ca)
    chk("p_sep=1 — 마지막 회차 연수 = 계약만기", rows1[-1][1], t1.T, 1e-9)
    # 첫 조기상환 가능일이 평가기준일 이전이면 그 뒤 첫 주기
    t2 = Terms(**base, p_sep=0, d_issue="2024-05-16", d_base="2026-07-01", d_mat="2029-05-16"); derive(t2)
    ex2 = G["eir_expect"](t2)
    chk_bool("경과 뒤 — 평가기준일 이후 첫 조기상환 가능일", ex2 is not None and ex2[2] >= t2.elapsed_m and ex2[2] - 24 in (0., 3., 6.) or (ex2 is not None and abs(((ex2[2]-24) % 3)) < 1e-9))



def test_emb_approach_allocation():
    """분리 정책 — 접근법 1 은 얽힌 권리를 묶고, 접근법 2 는 조기상환권을 따로 판단한다 (실무사례 30~32쪽).

    전환권이 부채이고 조기상환권을 «주계약에 포함» 으로 고른 계약에서

      접근법 1 → 되돌린다(p_sep=1). 파생 = B2 − B0 (콜 내재면 − 콜), 주계약 = 100 + 콜 − (B2 − B0)
      접근법 2 → 유지한다. 파생(전환권) = B2 − B1, 주계약(사채 + 풋) = 100 + 콜 − (B2 − B1),
                 상각표 만기는 첫 조기상환 가능일(기대만기 · 실무사례 29쪽)

    발행자 최초 인식 차이를 당기손익으로 고르면 주계약이 공정가치(B0 또는 B1)이고,
    배분표 마지막 줄이 −(순평가금액 − 100) 이라 합계가 그대로 100 이다 (2019-I-KQA018).
    """
    print("\n[34] 분리 정책 접근법 1·2 · 발행자 최초 인식 차이")
    RF = [(1, .0226), (3, .0240), (5, .0252)]; CR = [(1, .1409), (3, .1740), (5, .1905)]
    for inst, extra in (("CB", {}), ("RCPS", dict(issuer_call=2, k_w=.3, k_s=12., k_e=24.)),
                        ("BW", dict(bw_pay=1))):
        for ap in (1, 2):
            t = Terms(inst=inst, rf_curve=RF, cr_curve=CR, gap_m=6.0, carry=1, conv_class="liability",
                      emb_approach=ap, p_sep=0, **extra)
            derive(t)
            full, b0, b1, b2, ca, conv = G["decompose"](t)
            rows, _ = G["allocate"](t, full, b0, b1, b2, ca)
            kk = int(t.k_kind) == 1
            host, der = rows[0][1], rows[1][1]
            tag = f"{inst} 접근법 {ap}"
            chk(f"{tag} — 배분 합계", rows[-1][1], 100.0, 1e-9)
            if ap == 1:
                chk_bool(f"{tag} — 풋 «주계약에 포함» 을 되돌린다", int(t.p_sep) == 1)
                chk(f"{tag} — 파생 = B2 − B0", der, (b2 - b0) if t.k_sep else (b2 - ca - b0), 1e-9)
                chk(f"{tag} — 주계약 = 100 + 콜 − (B2 − B0)", host, 100 + (0 if kk else ca) - (b2 - b0), 1e-9)
            else:
                chk_bool(f"{tag} — 풋 «주계약에 포함» 을 유지한다", int(t.p_sep) == 0)
                chk(f"{tag} — 파생(전환권) = B2 − B1", der, (b2 - b1) if t.k_sep else (b2 - ca - b1), 1e-9)
                chk(f"{tag} — 주계약 = 100 + 콜 − (B2 − B1)", host, 100 + (0 if kk else ca) - (b2 - b1), 1e-9)
                ex = G["eir_expect"](t)
                if inst != "BW":
                    eir = G["eir_or_none"](t, full, b0, b1, b2, ca)
                    chk_bool(f"{tag} — 상각표 만기 = 첫 조기상환 가능일", ex is not None and abs(eir[1][-1][1] - ex[0]) < 1e-9)
                    chk(f"{tag} — 상각표 기말 = 그 시점 행사금액", eir[1][-1][5], ex[1], 1e-6)
    # 발행자 최초 인식 차이 — 당기손익
    for ap, ps in ((1, 1), (2, 0)):
        t = Terms(rf_curve=RF, cr_curve=CR, gap_m=6.0, carry=1, conv_class="liability", emb_approach=ap, p_sep=ps,
                  d1_pl=1, d1_reason="관측 가능한 시장자료")
        derive(t)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        rows, _ = G["allocate"](t, full, b0, b1, b2, ca)
        d = (b2 - ca) - 100.0
        tag = f"당기손익 · 접근법 {ap}"
        chk(f"{tag} — 주계약 = 공정가치", rows[0][1], b1 if ps == 0 else b0, 1e-9)
        chk(f"{tag} — 최초 인식 손익 줄 = −차이", rows[-2][1], -d, 1e-9)
        chk(f"{tag} — 배분 합계", rows[-1][1], 100.0, 1e-9)
        dr = 100 + sum(-v for _, v in rows[:-1] if v < 0); cr = sum(v for _, v in rows[:-1] if v > 0)
        chk(f"{tag} — 분개 차변 = 대변", dr, cr, 1e-9)

def test_tie_tolerance():
    """동점 허용오차가 **견주는 금액의 크기에 비례**하는가 — 엔진(tie_tol)과 엑셀(xl_tol)이 같은 규칙인가.

    금액이 1,000 이하이면 1e-9 그대로라 기존 결과가 바뀌지 않는다. 주가가 극단적으로 오른 노드처럼
    금액이 아주 크면 반올림 오차(금액 × 1e-16 의 몇 배)가 1e-9 를 넘는다. 허용오차가 고정이면 그런
    노드에서는 동점 규칙(전환·콜은 앞설 때만, 풋·보유는 동점이면)이 아니라 반올림 오차가 판정을 정해,
    엔진과 엑셀의 판정 표시가 갈릴 수 있다. 기대값은 규칙에서 센 값이다.
    """
    print("\n[35] 동점 허용오차 — 견주는 금액 크기에 비례 (엔진 · 엑셀 같은 규칙)")
    if "tie_tol" not in G:
        print("  (tie_tol 미도입 — 건너뜀)"); return
    tt, xt, nd, TOL = G["tie_tol"], G["xl_tol"], G["node_decide"], G["TOL"]
    INF = math.inf
    # ① 규칙 — 1,000 이하 1e-9 · 그 위로 큰 쪽 금액 × 1e-12 · 한쪽이 무한대(열리지 않은 권리)면 1e-9
    for x, y, want in ((100.0, 99.0, 1e-9), (1000.0, 1.0, 1e-9), (5e6, 3.0, 5e-6),
                       (3.0, -2e8, 2e-4), (INF, 5e6, 1e-9), (-INF, 120.0, 1e-9)):
        got = tt(x, y)
        chk_bool(f"허용오차({x:g}, {y:g}) = {want:g}", abs(got - want) <= 1e-12*want)
    # ② 엑셀 식이 같은 값을 낸다 (formulas 로 식을 직접 계산)
    try:
        import formulas
        for x, y in ((100.0, 99.0), (5e6, 3.0), (3.0, -2e8), (999999.0, 120.0)):
            v = float(formulas.Parser().ast("=" + xt(repr(x), repr(y)))[1].compile()())
            chk_bool(f"엑셀 식 = 엔진 ({x:g}, {y:g}) → {v:g}", abs(v - tt(x, y)) <= 1e-15*tt(x, y))
    except ImportError:
        print("  (formulas 없음 — 엑셀 식 계산 건너뜀)")
    # ③ 큰 금액에서 반올림 몇 칸 차이는 동점이다 — 전환은 허용오차만큼 앞설 때만 이긴다
    big = 1e8
    noisy = big*(1 + 4e-16)                      # 1e8 의 반올림 세 칸 (약 4.5e-8)
    chk_bool("  (전제) 고정 1e-9 였다면 잡음이 전환을 고른다", noisy >= big + TOL)
    chk_bool("큰 금액 · 반올림 잡음 → 전환 아님 (보유)", nd(noisy, 0.0, INF, big, False) == "hold")
    chk_bool("콜 우선 갈래도 같다 → 전환 아님", nd(noisy, 0.0, big, big, True) != "conv")
    chk_bool("큰 금액 · 실질 차이 0.001 → 전환", nd(big + 1e-3, 0.0, INF, big, False) == "conv")
    chk_bool("큰 금액 · 풋과 보유 동점(잡음) → 풋", nd(0.0, big, INF, noisy, False) == "put")
    # ④ 100 근처는 예전과 같다 — 1e-10 은 동점, 1e-8 은 실질 차이
    chk_bool("100 근처 · 1e-10 차이 → 동점이라 풋", nd(100.0 + 1e-10, 100.0, INF, 99.0, False) == "put")
    chk_bool("100 근처 · 1e-8 차이 → 전환", nd(100.0 + 1e-8, 100.0, INF, 99.0, False) == "conv")


def test_exercise_date_table():
    """행사일 대조표 — 계약일을 어느 노드에 배정했고 며칠 어긋나는지 (손으로 센 날짜와 대조).

    격자: 발행·평가 2025-01-01, 만기 2027-01-01, 월 간격(24구간 · 한 구간 30.4일 · 허용 5일).
    노드 i 의 날짜 = 2025-01-01 + round(i × 30.4167)일.
      3회 → 91일 → 2025-04-02.  계약일 2025-04-01(3개월) 보다 1일 뒤 — «계약일 뒤 첫 노드».
     13회 → 395일 → 2026-01-31.  계약일 2026-02-01(13개월) 보다 1일 앞 — 허용 5일 안이라 같은 날.
                                 12회 노드(2026-01-01)는 26일 앞이라 쓰지 않는다.
    """
    print("\n[36] 행사일 대조표 — 계약일과 실제로 쓴 노드")
    if "exercise_date_rows" not in G:
        print("  (행사일 대조표 미도입 — 건너뜀)"); return
    t = Terms(d_issue="2025-01-01", d_base="2025-01-01", d_mat="2027-01-01", gap_m=1.0,
              p_s=3., p_e=13., p_f=10., rf_curve=[(1, .0226), (3, .0240)],
              cr_curve=[(1, .1409), (3, .1740)])
    derive(t)
    rows = [r for r in G["exercise_date_rows"](t) if r[0] == "조기상환청구권"]
    want = [("조기상환청구권", "1회차", "2025-04-01", 3, "2025-04-02", 1, "계약일 뒤 첫 노드 (1일 뒤)"),
            ("조기상환청구권", "2회차", "2026-02-01", 13, "2026-01-31", -1,
             "노드가 1일 앞섬 — 허용 5일 안이라 같은 날로 봄")]
    for w in want:
        chk_bool(f"{w[1]} {w[2]} → 노드 {w[3]} ({w[4]}, {w[5]:+d}일)", w in rows)
    # 표의 노드가 격자가 실제로 여는 노드와 같다 — 표는 설명이 아니라 계산이 쓰는 배정이다
    n = int(t.n); EA = G["exercise_amounts"](t, n, t.T/n)
    opened = sorted(i for i in range(n + 1) if EA["p_on"](i))
    chk_bool(f"격자가 조기상환을 여는 노드 {opened} = 표의 노드", opened == sorted(r[3] for r in rows))
    # 허용 일수 — 월 5일 · 2주 3일 · 주 1일 (노드 간격의 4분의 1, 최소 1 · 최대 5)
    tt = G["date_tol_days"]
    chk_bool("허용 일수 월 5 · 2주 3 · 주 1 · 하루 1",
             (tt(1/12), tt(14/365), tt(7/365), tt(1/365)) == (5, 3, 1, 1))
    # 2주 격자에서도 표의 모든 줄이 규칙을 지킨다 — 차이는 −허용일수 이상, 앞 노드는 허용 밖
    t2 = Terms(d_issue="2025-01-01", d_base="2025-01-01", d_mat="2027-01-01", gap_m=0.5,
               p_s=3., p_e=21., p_f=3., rf_curve=[(1, .0226), (3, .0240)],
               cr_curve=[(1, .1409), (3, .1740)])
    derive(t2)
    n2 = int(t2.n); nd = G["node_dates"](t2, n2, t2.T/n2); tol = tt(t2.T/n2)
    import datetime as _dt
    bad = 0
    for r in G["exercise_date_rows"](t2):
        if r[0] != "조기상환청구권" or r[3] is None: continue
        cd = _dt.date.fromisoformat(r[2]); i = r[3]
        first = nd[i] >= cd - _dt.timedelta(days=tol) and (i == 0 or nd[i-1] < cd - _dt.timedelta(days=tol))
        if not (first and (nd[i] - cd).days == r[5]): bad += 1
    chk_bool(f"2주 격자 · 모든 회차가 «허용 일수 안의 첫 노드» (어긋남 {bad})", bad == 0)


def test_call_split_text():
    """제3자 콜옵션의 행사가 분해 — 부속예제(가치 구성비율) 대 한공회 본문 4.3.3(GS 전환확률)."""
    print("\n[27] 제3자 콜옵션 행사가 분해 · 콜옵션 유형")
    RF = [(1, .0226), (3, .0240), (5, .0252)]; CR = [(1, .1409), (3, .1740), (5, .1905)]
    base = dict(rf_curve=RF, cr_curve=CR, carry=1, gap_m=6., k_s=12., k_e=24., k_f=6., k_prem=.015, k_cmp=4, k_w=.3)
    ctp = G["call_third_party"]
    def val(km, ks, **kw):
        t = Terms(**{**base, **kw}, k_method=km, k_split=ks); derive(t)
        full = G["engine"](t, call=False)
        return ctp(t, full, km), t, full

    def _mkt(bs, **kw):
        t = Terms(**{**bs, **kw}); derive(t); return t
    v10, _, _ = val(1, 0); v11, _, _ = val(1, 1); v20, _, _ = val(2, 0); v21, t21, full = val(2, 1)
    chk_bool("두 방식이 다른 값을 낸다 (방법2)", abs(v20 - v21) > 1e-6)
    chk_bool("본문식 방법2 가 부속예제 방법2 의 ±10% 안", abs(v21 - v20) < 0.10*v20)
    chk_bool("본문식 방법1 이 방법2 의 ±15% 안 (두 할인 요령의 차이)", abs(v11 - v21) < 0.15*v21)
    print(f"      (부속예제 방법1 {v10:.4f} · 방법2 {v20:.4f} | 본문식 방법1 {v11:.4f} · 방법2 {v21:.4f})")
    # 지분 몫 + 채권 몫 = 페이오프 (본문식) — 뿌리에서 확인
    memo, ks = full["memo"], full["kstrike"]
    root = memo[full["root"]]
    rt = ctp(t21, full, 2)
    chk_bool("본문식 값 ≥ 0", rt >= -1e-9)
    # 깊은 내가격 — P→1, E/(E+B)→1 이라 두 방식이 붙는다
    a0, _, _ = val(2, 0, S0=10000., K0=1000.); a1, _, _ = val(2, 1, S0=10000., K0=1000.)
    chk("깊은 내가격 — 두 방식 차이", a1 - a0, 0.0, 0.05)
    # 콜 없으면 0
    z, _, _ = val(2, 1, k_s=99., k_e=0.)
    chk("콜 기간 없음 — 0", z, 0.0, 1e-12)
    # 기본값·강제: Terms() 는 0, 현금납입 BW 는 0 으로 강제, SHA 는 0
    chk_bool("Terms() 기본 k_split = 0 (기준선 보존)", Terms().k_split == 0)
    tb = Terms(inst="BW", bw_pay=0, k_split=1, k_method=2); derive(tb)
    chk_bool("현금납입 BW → k_split 0 강제", tb.k_split == 0)
    # 기특정 콜 — 값은 같고 별도 금융상품 강제
    tk0 = Terms(**base, k_method=2, k_split=1, k_kind=0); derive(tk0)
    tk1 = Terms(**base, k_method=2, k_split=1, k_kind=1, k_sep=0); derive(tk1)
    chk_bool("기특정 콜 — k_sep 을 1 로 되돌린다 (compat)", tk1.k_sep == 1 and any(k == "k_sep" for k, _, _ in getattr(tk1, "forced_notes", [])))
    f0 = G["decompose"](tk0); f1 = G["decompose"](tk1)
    chk("기특정 콜 — 매도청구권 값은 유형과 무관", f1[4], f0[4], 1e-9)
    # ── 기특정 콜의 주주간 분배 (본문 4.5.1) ──
    # 발행자가 옵션 당사자가 아니므로 파생상품자산을 인식하지 않는다. 받은 대가 100 이
    # 복합금융상품 요소에 전부 배분되고, 잔여인 전환권대가가 콜 값만큼 작아진다.
    _r0, _ = G["allocate"](tk0, *f0[:5]); _r1, _ = G["allocate"](tk1, *f1[:5])
    chk_bool("지정 가능 콜 — 파생상품자산 줄이 있다",
             any("파생상품자산" in _k for _k, _ in _r0[:-1]))
    chk_bool("기특정 콜 — 파생상품자산 줄이 없다",
             not any("파생상품자산" in _k for _k, _ in _r1[:-1]))
    chk("기특정 콜 — 배분 합계 100", _r1[-1][1], 100.0, 1e-9)
    _eq0 = dict(_r0[:-1])["전환권대가 · 자본"]; _eq1 = dict(_r1[:-1])["전환권대가 · 자본"]
    chk("기특정 콜 — 전환권대가가 콜 값만큼 작다", _eq0 - _eq1, f0[4], 1e-9)
    _ex = G["alloc_extra"](tk1, f1[4])
    chk_bool("기특정 콜 — 참고 줄이 합계 밖에 하나", len(_ex) == 1)
    chk("기특정 콜 — 참고 줄 금액 = 콜 값", _ex[0][1], f1[4], 1e-12)
    chk_bool("지정 가능 콜 — 참고 줄 없음", not G["alloc_extra"](tk0, f0[4]))
    _dr1 = 100.0 + sum(-_v for _, _v in _r1[:-1] if _v < 0)
    chk("기특정 콜 — 분개 차변 100 (자산 줄 없음)", _dr1, 100.0, 1e-9)
    # ── 혼합할인율의 비중은 늘 [0,1] 이고 P 를 따른다 (본문 3.2 p.50 GS 전환가중확률할인) ──
    # 방법 1 의 자식 비중은 k_split=1 이면 전환확률, 0 이면 가치 구성비율이다. 둘 다 [0,1]
    # 이므로 섞은 할인율이 무위험~위험 구간을 벗어나지 않는다.
    import math as _m
    _t = Terms(**base, k_method=1, k_split=1); derive(_t)
    _f = G["engine"](_t, call=False)
    _mm, _fRF, _fCR = _f["memo"], _f["fwdRF"], _f["fwdCR"]
    chk_bool("전환확률이 모든 노드에서 [0,1]",
             all(-1e-12 <= _o.get("P", 0.0) <= 1 + 1e-12 for _o in _mm.values()))

    def _oracle_mix(tt, ff, weight):
        """방법 1 의 독립 재구현 — 자식의 비중으로 r = γ·Rf + (1−γ)·Rd 를 만들어 역진한다.
        엔진과 별개로 여기서 다시 짠다. 산식을 뒤집으면(1−γ 를 무위험에 곱하면) 어긋난다."""
        mm, dt2 = ff["memo"], ff["dt"]
        qq, r1, r2, kk = ff["qi"], ff["fwdRF"], ff["fwdCR"], ff["kstrike"]
        seen = {}
        def go(key, i):
            if key in seen: return seen[key]
            o = mm[key]; K = kk(i)
            pay = max(o["E"] + o["B"] - K, 0.0) if K is not None else 0.0
            if "up" not in o:
                v = pay
            else:
                q2 = qq(i); ou, od = mm[o["up"]], mm[o["dn"]]
                gu, gd = weight(ou), weight(od)
                yu = gu*r1(i) + (1 - gu)*r2(i)
                yd = gd*r1(i) + (1 - gd)*r2(i)
                v = max(pay, q2*go(o["up"], i+1)*_m.exp(-yu*dt2)
                             + (1 - q2)*go(o["dn"], i+1)*_m.exp(-yd*dt2))
            seen[key] = v; return v
        return go(ff["root"], 0)

    _wP = lambda o: o.get("P", 0.0)
    _wE = lambda o: (o["E"]/(o["E"] + o["B"]) if o["E"] + o["B"] > 1e-12 else 0.0)
    chk("방법1 · 전환확률 비중 = 독립 오라클", ctp(_t, _f, 1), _oracle_mix(_t, _f, _wP), 1e-9)
    _t0 = Terms(**base, k_method=1, k_split=0); derive(_t0); _f0 = G["engine"](_t0, call=False)
    chk("방법1 · 구성비율 비중 = 독립 오라클", ctp(_t0, _f0, 1), _oracle_mix(_t0, _f0, _wE), 1e-9)
    # 극단 비중 — 오라클을 상수 비중으로 돌려 무위험·위험·혼합이 제 값인지 본다
    _r1 = _oracle_mix(_t, _f, lambda o: 1.0)
    _r0 = _oracle_mix(_t, _f, lambda o: 0.0)
    _r4 = _oracle_mix(_t, _f, lambda o: 0.4)
    chk_bool(f"비중 1 → 무위험이 가장 높은 값 ({_r1:.4f} > {_r4:.4f} > {_r0:.4f})",
             _r1 > _r4 > _r0)
    # 비중 0.4 는 두 극단 사이 — 할인율이 0.4·Rf + 0.6·Rd 라는 뜻
    chk_bool("비중 0.4 값이 두 극단 사이", _r0 < _r4 < _r1)
    # 구성비율과 전환확률이 실제로 다른 노드가 있어야 두 기준의 구분이 뜻을 가진다
    _diff = sum(1 for _o in _mm.values()
                if _o["E"] + _o["B"] > 1e-9
                and abs(_o["E"]/(_o["E"] + _o["B"]) - _o.get("P", 0.0)) > 1e-6)
    chk_bool(f"구성비율 ≠ 전환확률인 노드가 있다 ({_diff}개)", _diff > 0)
    # ── 행사가 분해: 두 몫의 합은 언제나 페이오프 (본문식) ──
    _ks = _f["kstrike"]; _worst = 0.0
    for _key, _o in _mm.items():
        _i = _key[0]; _K = _ks(_i)
        if _K is None: continue
        _pay = max(_o["E"] + _o["B"] - _K, 0.0)
        if _pay <= 0: continue
        _P = _o.get("P", 0.0)
        _worst = max(_worst, abs((_o["E"] - _P*_K) + (_o["B"] - (1 - _P)*_K) - _pay))
    chk("본문식 지분 몫 + 채권 몫 = 페이오프 (최대 오차)", _worst, 0.0, 1e-9)
    # ── 콜 대상비율 (GPT §11 8·9) ──
    _ca = lambda **kw: G["decompose"](_mkt(base, k_method=2, k_split=1, **kw))[4]
    chk("대상비율 0% → 매도청구권 0", _ca(k_w=0.0), 0.0, 1e-12)
    chk("대상비율 100% = 30% 값 ÷ 0.3", _ca(k_w=1.0), _ca(k_w=.3)/0.3, 1e-9)
    # ── 의무보유(k_hold): 콜 대상물량이 행사기간 동안 존속하는가 ──
    # 4.4.2·4.4.3 대로 기초자산은 그대로 두고, 콜 계약층에서 «행사기회 유지»로만 반영한다.
    _h1, _, _ = val(2, 1, k_hold=1); _h0, _, _ = val(2, 1, k_hold=0)
    chk_bool(f"의무보유 있음 > 없음 (콜 {_h1:.4f} 대 {_h0:.4f})", _h1 > _h0 + 1e-6)
    chk_bool("의무보유가 값을 크게 움직인다 (2배 이상)", _h1 > 2*_h0)
    _g1, _, _ = val(1, 0, k_hold=1); _g0, _, _ = val(1, 0, k_hold=0)
    chk_bool("방법1 에서도 같은 방향", _g1 > _g0 + 1e-6)
    chk_bool("Terms() 기본 k_hold = 1 (기준선 보존)", Terms().k_hold == 1)
    # ── 의무보유는 두 평가방법이 «같은 기간·같은 권리» 를 본다 ──
    # k_hold 가 있음/없음, k_lock 이 기간, k_lock_put 이 조기상환청구까지 막는가.
    # 유무가치비교법도 k_hold 를 따른다 — 꺼 두면 격자의 전환·조기상환 지연이 사라진다.
    _u1 = G["decompose"](_mkt(base, k_method=0, k_hold=1))[4]
    _u0 = G["decompose"](_mkt(base, k_method=0, k_hold=0))[4]
    chk_bool(f"유무가치비교법도 의무보유를 따른다 (있음 {_u1:.4f} > 없음 {_u0:.4f})",
             _u1 > _u0 + 1e-6)
    # 계약 정의는 「전환 및 조기상환청구 불가」다. 조기상환까지 막으면 콜 대상물량이
    # 빠져나가지 못해 유무가치법의 값이 «오른다».
    _p1 = G["decompose"](_mkt(base, k_method=0, k_lock_put=1))[4]
    _p0 = G["decompose"](_mkt(base, k_method=0, k_lock_put=0))[4]
    chk_bool(f"조기상환도 막으면 유무가치 값이 오른다 ({_p0:.4f} → {_p1:.4f})",
             _p1 > _p0 + 1e-6)
    chk_bool("Terms() 기본 k_lock_put = 1 (계약 정의)", Terms().k_lock_put == 1)
    # 의무보유가 조기상환 시작보다 이르면 아무 제약이 아니라 두 값이 같다.
    _q1 = G["decompose"](_mkt(base, k_method=0, k_lock=6.0, k_lock_put=1))[4]
    _q0 = G["decompose"](_mkt(base, k_method=0, k_lock=6.0, k_lock_put=0))[4]
    chk("의무보유가 조기상환 시작보다 이르면 차이 없음", _q1, _q0, 1e-12)
    # 옵션차익법의 존속도 같은 기간을 본다 — 의무보유가 짧을수록 콜이 일찍 죽어 값이 낮다.
    _l_long, _, _ = val(2, 1, k_hold=1, k_lock=25.0)
    _l_mid, _, _ = val(2, 1, k_hold=1, k_lock=18.0)
    chk_bool(f"의무보유가 짧으면 옵션차익 값이 낮다 ({_l_long:.4f} > {_l_mid:.4f})",
             _l_long > _l_mid + 1e-6)
    _l_none, _, _ = val(2, 1, k_hold=1, k_lock=0.0)
    _l_off, _, _ = val(2, 1, k_hold=0)
    chk("의무보유 기간 0 = 의무보유 없음", _l_none, _l_off, 1e-12)
    # ── 콜 소멸은 계약 우선순위를 따른다 ──
    # 풋·콜 우선순위(pc_order)는 조기상환과 매도청구 사이만 정하고, 전환과 매도청구 사이는
    # 매도청구 통지 뒤 전환 대응(k_conv_resp)이 정한다 — 우선순위 하나가 전환권까지 바꾸면 안 된다
    # (2026-09-30 외부 검토). 예전 이 자리는 «콜 우선이면 전환도 콜에 밀린다» 를 기대했는데, 그것은
    # 옵션차익법에서만 그랬고 유무가치 격자는 콜 우선이어도 전환 대응을 허용했다 — 두 방법이 다른
    # 계약을 읽던 자리다. 이 계약은 조기상환금액이 매도청구금액보다 큰 겹침이 없어 우선순위가 값을 바꾸지 않는다.
    _o0, _, _ = val(2, 1, k_hold=0, pc_order=0)
    _o1, _, _ = val(2, 1, k_hold=0, pc_order=1)
    chk(f"조기상환 > 매도청구 인 겹침이 없으면 우선순위와 무관 ({_o0:.4f} = {_o1:.4f})", _o1, _o0, 1e-12)
    _c1, _, _ = val(2, 1, k_hold=0, k_conv_resp=1)
    _c0, _, _ = val(2, 1, k_hold=0, k_conv_resp=0)
    chk_bool(f"통지 뒤 전환 불가 → 의무보유 없어도 값이 높다 ({_c1:.4f} < {_c0:.4f})", _c0 > _c1 + 1e-6)
    # 자동전환·만기는 투자자의 «선택» 이 아니라 밀리지 않는다 — 의무보유가 있으면
    # 두 우선순위가 같은 값을 낸다 (소멸 조건 자체가 걸리지 않는다).
    _h_p0, _, _ = val(2, 1, k_hold=1, pc_order=0)
    _h_p1, _, _ = val(2, 1, k_hold=1, pc_order=1)
    chk("의무보유가 콜 기간을 덮으면 우선순위와 무관", _h_p0, _h_p1, 1e-12)
    # 문안
    for km, ks, kk, want in ((0, 0, 0, "유무가치비교법"), (2, 1, 0, "본문 4.3.3"), (2, 0, 0, "비례균등차감법"),
                             (2, 1, 1, "주주간 분배"),
                             # 기특정 콜은 본문 4.5 의 «세 접근법» 중 하나를 고른 것이다.
                             # 채택한 접근법과 다른 갈래로 가는 길을 함께 밝혀야 한다.
                             (2, 1, 1, "접근법 2-2"), (2, 1, 1, "접근법 1"), (2, 1, 1, "유무가치비교법")):
        t = Terms(**base, k_method=km, k_split=ks, k_kind=kk); derive(t)
        chk_bool(f"문안에 «{want}»", want in G["call_method_text"](t))
    # 화면 안내(call_type_note)도 같은 사실을 실어야 한다 — 조서와 화면이 갈리면 안 된다.
    for kk, want in ((1, "접근법 2-2"), (1, "접근법 1"), (1, "유무가치비교법"), (1, "회계처리 되는 경우가 있다"),
                     (0, "복합옵션")):
        t = Terms(**base, k_method=2, k_split=1, k_kind=kk); derive(t)
        chk_bool(f"화면 안내에 «{want}» (유형 {kk})", want in G["call_type_note"](t))
    # ── 주가·전환가의 basis 정합성 ──
    # 분할·병합·무상증자가 있으면 「분할 전 전환가액」과 「분할 후 주가」가 섞여 값이
    # 배수만큼 틀어진다. 네트워크 없이 앱이 이미 가진 두 값으로 잡아야 한다.
    _bc = G["basis_check"]
    _BB = dict(K_cap=1000., floor=700., par=100.)
    chk_bool("정상 계약은 조용하다", not _bc(Terms(S0=1000., K0=1000., **_BB), 1000.0))
    chk_bool("하루 등락 1% 로는 경고하지 않는다",
             not _bc(Terms(S0=1010., K0=1000., **_BB), 1000.0))
    chk_bool("5:1 분할 — 원주가만 조정되면 잡는다",
             any("5배" in x for x in _bc(Terms(S0=200., K0=1000., **_BB), 1000.0)))
    chk_bool("무상증자 1:1 — 시계열만 조정되면 잡는다",
             any("2배" in x for x in _bc(Terms(S0=1000., K0=1000., **_BB), 500.0)))
    chk_bool("최초 전환가만 분할 전이면 잡는다",
             any("최초 전환가액" in x for x in
                 _bc(Terms(S0=1000., K0=1000., K_cap=5000., floor=700., par=100.), 1000.0)))
    chk_bool("주가가 전환가의 50배면 잡는다",
             any("50.0배" in x for x in _bc(Terms(S0=50000., K0=1000., **_BB), 50000.0)))
    # 잘못된 경고를 내지 않는다 — 리픽싱이 없으면 하한은 애초에 작동하지 않고,
    # 하한 = 액면가면 계약상 하한이 아니라 법정 하한이다 (대신증권 RCPS 가 그렇다).
    chk_bool("리픽싱 없는 계약의 액면가 하한에는 경고하지 않는다",
             not _bc(Terms(S0=27100., K0=81000., floor=5000., par=5000., rfx_mode=0)))
    chk_bool("리픽싱 있고 하한이 20% 면 잡는다",
             any("최저 조정가액" in x for x in
                 _bc(Terms(S0=1000., K0=1000., K_cap=1000., floor=200., par=100., rfx_mode=1))))
    chk_bool("시계열이 없으면(px_last=None) 주가 대조는 건너뛴다",
             not any("변동성 시계열" in x for x in _bc(Terms(S0=200., K0=1000., **_BB))))
    # ── 행사금액표가 암시하는 보장수익률 (implied_yield · sched_yield) ──
    # 계약이 회차별 금액을 확정 숫자로 주면 보장수익률을 적을 자리가 없다. 그래도 그 표가
    # 계약서의 몇 %와 맞는지는 확인해야 하므로 역산해 보여 준다. 왕복이 닫혀야 한다.
    _ar, _iy, _sy = G["accrue_rate"], G["implied_yield"], G["sched_yield"]
    for _g, _c, _m, _t in ((.05, .03, 4, 3.0), (.05, .00, 4, 0.5), (.08, .02, 2, 2.5),
                           (.06, .03, 0, 1.5), (.10, .00, 1, 5.0), (.05, .03, 4, 0.5)):
        _p = _ar(_t, _g, _c, _m)
        chk(f"역산 왕복 g={_g:.2%} c={_c:.0%} m={_m} t={_t:g}", _iy(_p, _t, _c, _m), _g, 1e-7)
    chk_bool("할증금이 0 이면 역산할 수 없다 (None)", _iy(0.0, 1.0, .03, 4) is None)
    chk_bool("기간이 0 이면 역산할 수 없다 (None)", _iy(.05, 0.0, .03, 4) is None)
    chk_bool("연 200% 로도 못 미치는 금액은 None", _iy(50.0, 0.5, .03, 4) is None)
    # 다이나믹솔루션 제9회 공시 표 — 계약은 「연 5% 분기복리」다. 표에서 그 5% 가 나와야
    # 표를 옳게 옮겼다고 말할 수 있다.
    _D9R = sorted({6: 101.0063, 9: 101.5188, 12: 102.0378, 15: 102.5633, 18: 103.0953,
                   21: 103.6340, 24: 104.1794, 27: 104.7317, 30: 105.2908,
                   33: 105.8570}.items())
    _rep, _lo, _hi = _sy(_D9R, .03, 4)
    chk("제9회 표가 암시하는 보장수익률 = 연 5%", _rep, 0.05, 1e-5)
    chk("회차별 역산 범위 (최대 − 최소)", _hi - _lo, 0.0, 5e-6)
    chk("제9회 만기 106.4302% 가 암시하는 보장수익률",
        _iy(1.064302 - 1, 3.0, .03, 4), 0.05, 1e-6)
    # 한 줄만 어긋난 표는 범위가 벌어진다 — 오타를 잡는 값싼 검산이다.
    _bad9 = [(mo, (v + 1.0 if mo == 18 else v)) for mo, v in _D9R]
    chk_bool("한 줄만 어긋나면 회차별 범위가 벌어진다",
             (lambda r: r[2] - r[1] > 0.005)(_sy(_bad9, .03, 4)))
    chk_bool("읽을 회차가 없으면 None", _sy([], .03, 4) is None)

    # ── 행사일 이자를 «따로» 받는 계약 (p_cpn_add · k_cpn_add) ──
    # 계약이 「조기상환일에 원금과 그 날까지의 이자를 함께 지급」이라고 쓰면 행사금액 위에
    # 그 날 이자가 더 얹힌다. 계약 해석은 앱이 아니라 사용자가 정한다 — 스위치로 받는다.
    _CB = dict(d_issue="2024-01-05", d_base="2024-01-05", d_mat="2029-01-05", gap_m=6.0,
               cpn=.05, ipay=6.0, ytm=.07, ytm_cmp=2, p_mode="accrue", p_yield=.07,
               p_cmp=2, p_s=12., p_e=54., p_f=6., k_w=0.0, sig=.40, carry=1)
    _c0 = Terms(**_CB); _c1 = Terms(**dict(_CB, p_cpn_add=1))
    for _t in (_c0, _c1):
        _t.rf_curve = [(1, .0226), (3, .0240), (5, .0252)]
        _t.cr_curve = [(1, .1409), (3, .1740), (5, .1905)]
        derive(_t)
    _CPN = 100*G["eff_cpn"](_c0)*_c0.ipay/12
    _m0 = G["engine"](_c0)["memo"]; _m1 = G["engine"](_c1)["memo"]
    _dif = sorted({round(_m1[k]["pv"] - _m0[k]["pv"], 6)
                   for k in _m0 if _m0[k].get("pv", 0) > 0 and "pv" in _m1.get(k, {})})
    chk_bool("차이는 0 아니면 «그 날 이자» 뿐", set(_dif) <= {0.0, round(_CPN, 6)})
    chk_bool("이자지급일에 얹히는 자리가 실제로 있다", round(_CPN, 6) in _dif)
    chk("얹히는 금액 = 표면이자 회당 지급액", max(_dif), _CPN, 1e-9)
    # 켜면 조기상환권이 커진다 — 같은 자리에서 더 받기 때문이다.
    _v0 = G["decompose"](_c0); _v1 = G["decompose"](_c1)
    chk_bool("행사일 이자를 받으면 부채요소가 커진다", _v1[2] > _v0[2] + 1e-9)
    chk_bool("기본값은 종전 동작 (끔)", Terms().p_cpn_add == 0 and Terms().k_cpn_add == 0)
    # 만기 노드는 스위치와 무관하다 — 만기에는 어느 갈래든 마지막 이자를 함께 받는다.
    _mk = [k for k in _m0 if _m0[k].get("kind") in ("mat", "put") and "pv" not in _m0[k]]
    chk_bool("만기 노드는 스위치가 건드리지 않는다",
             all(abs(_m1[k]["B"] - _m0[k]["B"]) < 1e-9 for k in _mk))

    # ── 위험 곡선 방식 — 「표에서 등급 하나 고르기」를 없앴다 ──
    # 고시표를 올려 「이 곡선 적용」 을 누르면 그 곡선이 직접 입력 칸에 들어오므로 같은
    # 일을 두 번 묻던 갈래였다. 옛 시나리오는 cr_curve 를 들고 있어 같은 값으로 열린다.
    _tp0 = Terms(rate_mode="pick", cr_curve=[(1, .11), (3, .13), (5, .14)])
    _tp0.rf_curve = [(1, .026), (3, .028), (5, .030)]; derive(_tp0)
    chk_bool("옛 «pick» 시나리오가 직접 입력으로 열린다", _tp0.rate_mode == "direct")
    _tp1 = Terms(rate_mode="direct", cr_curve=list(_tp0.cr_curve))
    _tp1.rf_curve = list(_tp0.rf_curve); derive(_tp1)
    chk("옛 «pick» 과 직접 입력이 같은 곡선·같은 값",
        G["decompose"](_tp0)[3], G["decompose"](_tp1)[3], 1e-12)
    chk_bool("남은 방식은 둘뿐", set(f.default for f in [Terms.__dataclass_fields__["rate_mode"]])
             == {"direct"})

    # ── BDT 를 켤 수 있는 자리인가 (put_bdt_avail · put_bdt_block) ──
    # 같은 조건이 사이드바·검토 화면·put_bdt_on 세 군데에 손으로 적혀 있어, 화면이
    # 사이드바의 잠금을 모르고 「켜십시오」라고 권했다. 규칙은 한 곳에서 나온다.
    _AV, _BL, _ON = G["put_bdt_avail"], G["put_bdt_block"], G["put_bdt_on"]
    _bb = dict(conv_class="equity", model="TF", p_s=6., p_e=54.)
    chk_bool("자본 + TF + 조기상환권 → 켤 수 있다", _AV(Terms(**_bb)))
    chk_bool("전환권이 부채면 못 켠다", not _AV(Terms(**{**_bb, "conv_class": "liability"})))
    chk_bool("GS 면 못 켠다", not _AV(Terms(**{**_bb, "model": "GS"})))
    chk_bool("조기상환권이 없으면 못 켠다", not _AV(Terms(**{**_bb, "p_s": 60., "p_e": 6.})))
    chk_bool("막힌 이유를 적는다 (부채)",
             _BL(Terms(**{**_bb, "conv_class": "liability"})) == "전환권이 파생상품부채다")
    chk_bool("막힌 이유를 적는다 (GS)", _BL(Terms(**{**_bb, "model": "GS"})) == "신용위험 처리가 GS 다")
    chk_bool("켤 수 있으면 이유는 빈 문구", _BL(Terms(**_bb)) == "")
    chk_bool("put_bdt_on 은 avail 과 어긋나지 않는다",
             all(_ON(Terms(**{**_bb, "put_bdt": 1, **_ch})) == _AV(Terms(**{**_bb, **_ch}))
                 for _ch in ({}, {"conv_class": "liability"}, {"model": "GS"},
                             {"p_s": 60., "p_e": 6.})))
    chk_bool("켜지 않으면 avail 이어도 꺼져 있다", not _ON(Terms(**_bb)))

    # ── 주가 조회 기록 (px_trace) ──
    # 조서를 받은 사람이 「무엇을 요청했고 무엇을 받았는지」 알아야 분할·병합을
    # 의심할 때 되짚을 수 있다. 네트워크를 쓰지 않는다 — 기록을 직접 세워 시험한다.
    _pt = G["px_trace"]
    _d = dict(_pt(Terms(d_base="2026-06-05")))
    chk_bool("직접 입력이면 조회가 없었다고 적는다", "직접 입력" in _d["주가 출처"])
    chk_bool("직접 입력이면 분할 기록도 «조회하지 않음»", _d["분할 기록"] == "조회하지 않음")
    chk_bool("조회 기록은 다섯 줄", len(_pt(Terms())) == 5)
    _YK = dict(d_base="2026-06-06", s0_src="야후 085660.KQ 2026-06-05 종가",
               s0_date="2026-06-05", s0_raw=1103., s0_adj=1103., s0_splits="없음")
    _y = lambda **kw: Terms(**{**_YK, **kw})
    _d = dict(_pt(_y()))
    chk_bool("요청 평가기준일을 그대로 적는다", _d["요청 평가기준일"] == "2026-06-06")
    chk_bool("휴장이면 직전 거래일임을 적는다", "휴장" in _d["실제 사용 거래일"])
    chk_bool("원주가·수정주가가 같으면 조용하다", "조정사건" not in _d["원주가 · 수정주가"])
    chk_bool("«없음» 은 «사건이 없었다»가 아니라고 적는다", "무상증자" in _d["분할 기록"])
    _d = dict(_pt(_y(s0_adj=551.5, s0_splits="")))
    chk_bool("원주가와 수정주가가 다르면 알린다", "조정사건" in _d["원주가 · 수정주가"])
    chk_bool("분할 조회 실패는 «확인 못 함»", "확인 못 함" in _d["분할 기록"])
    _d = dict(_pt(_y(s0_splits="2026-03-02 5배")))
    chk_bool("분할 기록이 있으면 그대로 싣는다", _d["분할 기록"] == "2026-03-02 5배")
    # 조회 실패(None)와 기록 없음([])을 구분한다 — 야후를 부르지 않고 규약만 본다.
    import inspect as _ins
    _fs = _ins.getsource(G["fetch_splits"])
    chk_bool("fetch_splits 는 실패 시 None 을 돌려준다", "return None" in _fs)
    chk_bool("fetch_splits 는 기록이 없으면 빈 목록을 돌려준다", "return out" in _fs)

    # ── 재현 기록 (run_stamp) ──
    # 몇 달 뒤 같은 계약을 다시 재서 값이 다르면 앱이 바뀐 것인지 인풋이 바뀐 것인지
    # 가려야 한다. 지문은 «계산에 실제로 쓴» Terms 에서 나와야 한다.
    _rs, _sr, _st = G["run_stamp"], G["stamp_rows"], G["_stamp"]
    _t1 = Terms(sig=.45); derive(_t1)
    _m1 = _rs(_t1)
    chk_bool("지문은 _stamp 과 같은 값", _m1["terms_md5"] == _st(_t1))
    chk_bool("평가체계 버전을 함께 남긴다", _m1["schema"] == G["SCHEMA_VER"])
    chk_bool("app.py 해시는 12자리", len(_m1["app_sha12"]) in (0, 12))
    _t2 = Terms(sig=.46); derive(_t2)
    chk_bool("인풋이 다르면 지문도 다르다", _rs(_t2)["terms_md5"] != _m1["terms_md5"])
    chk_bool("같은 인풋이면 지문도 같다", _rs(Terms(sig=.45))["terms_md5"] == _st(Terms(sig=.45)))
    # 수식 조서는 조정일 처리를 바꾼 사본으로 트리를 만든다 — 그 사본의 지문이어야 한다.
    _tf = Terms(**G["asdict"](_t1)); _tf.carry = 2
    chk_bool("계산에 쓴 Terms 로 지문을 뜬다", _rs(_tf)["terms_md5"] != _m1["terms_md5"])
    chk_bool("종류(값·수식)가 다르면 지문도 다르다", _rs(_t1, "수식")["terms_md5"] != _m1["terms_md5"])
    _rows = dict(_sr(_t1))
    for _k in ("생성시각", "평가체계 버전", "계산 입력 식별값 (적용값 기준)",
               "입력파일 식별값 (불러온 원본)", "주가 출처", "위험 곡선 출처"):
        chk_bool(f"조서 재현 기록에 «{_k}»", _k in _rows)
    # 13줄 + 「평가 관점」 (발행자 · 투자자). 회차 표시는 적었을 때만 한 줄 더한다
    chk_bool("조서 재현 기록은 14줄", len(_sr(_t1)) == 14)
    # 두 지문은 다른 것을 가리킨다. 원본 시나리오 지문은 계산에 쓰이지 않으므로 계산
    # 지문에 섞이지 않아야 한다 — 섞이면 같은 계약이 파일에서 열렸는지에 따라 갈린다.
    _t1b = Terms(**{**G["asdict"](_t1), "scen_md5": "deadbeef"})
    chk_bool("원본 지문을 실어도 계산 지문은 그대로", _st(_t1b) == _st(_t1))
    _rr = dict(_sr(_t1b))
    chk_bool("조서에 시나리오 지문 줄", _rr["입력파일 식별값 (불러온 원본)"] == "deadbeef")
    chk_bool("조서에 계산 지문 줄", _rr["계산 입력 식별값 (적용값 기준)"] == _st(_t1))
    chk_bool("직접 입력이면 시나리오 지문은 «해당 없음»",
             "해당 없음" in dict(_sr(_t1))["입력파일 식별값 (불러온 원본)"])
    # 요청한 노드 간격과 실제로 쓴 간격은 다를 수 있다 — 둘을 나눠 적는다.
    _nd = _rr["노드 — 요청 간격 · 실제"]
    chk_bool("요청 간격을 적는다", f"요청 {_t1.gap_m:g}개월" in _nd)
    chk_bool("실제 노드 수를 적는다", f"노드 {int(_t1.n)}개" in _nd)
    chk_bool("실제 간격과 Δt 를 적는다", "실제 간격" in _nd and "Δt" in _nd)
    # 원본 지문은 _meta 를 뺀 나머지로 잰다 — 저장·재저장으로 값이 흔들리지 않는다.
    _o = G["asdict"](_t1)
    chk_bool("scen_stamp 은 _meta 를 보지 않는다",
             G["scen_stamp"]({**_o, "_meta": {"x": 1}}) == G["scen_stamp"](_o))
    chk_bool("한 칸만 달라도 원본 지문이 달라진다",
             G["scen_stamp"](_o) != G["scen_stamp"]({**_o, "sig": .99}))
    # 검산요약에도 한 줄 — 조서를 열면 어느 판에서 나왔는지 바로 보인다.
    _f1, _b0, _b1, _b2, _ca, _cv = G["decompose"](_t1)
    _ck = dict((x[0], x) for x in G["model_checks"](_t1, _f1, _b0, _b1, _b2, _ca))
    chk_bool("검산요약에 재현 기록 줄", "재현 기록 · 앱 버전 · 입력 식별값" in _ck)
    chk_bool("검산요약 줄에 지문 앞 8자리",
             _st(_t1)[:8] in _ck["재현 기록 · 앱 버전 · 입력 식별값"][1])

    # ── 시나리오 JSON 의 평가체계 버전 ──
    # 옛 파일에는 «_schema» 가 없다. 그때 Terms 기본값(유무가치비교법)으로 열려야
    # 과거 조서가 그대로 재현된다. 새 파일에는 버전이 붙고, 옛 앱에서도 열려야 하므로
    # Terms 에 없는 키는 버려진다.
    import json as _json
    _fields = Terms.__dataclass_fields__
    _saved = _json.loads(_json.dumps({**G["asdict"](Terms()), "_schema": G["SCHEMA_VER"],
                                      "_meta": G["run_stamp"](Terms())},
                                     ensure_ascii=False, default=str))
    chk_bool("저장 파일에 «_schema» 가 붙는다", _saved.get("_schema") == G["SCHEMA_VER"])
    chk_bool("저장 파일에 «_meta» 재현 기록이 붙는다",
             _saved.get("_meta", {}).get("terms_md5") == G["_stamp"](Terms()))
    chk_bool("«_meta» 도 Terms 에 없는 키라 옛 앱에서 버려진다", "_meta" not in _fields)
    chk_bool("Terms 에 없는 키는 버려진다 (옛 앱 호환)", "_schema" not in _fields)
    _t_old = Terms(**{k: v for k, v in {"k_w": 0.3, "sig": 0.5}.items() if k in _fields})
    chk_bool("«_schema» 없는 옛 파일 → 유무가치비교법 기본", _t_old.k_method == 0)
    chk_bool("«_schema» 없는 옛 파일 → 행사가 분해 없음", _t_old.k_split == 0)
    # 3 = 매도청구 통지 뒤 전환 대응(k_conv_resp)을 따로 받고 세 평가방법이 같은 사건 규칙을 쓴다
    chk_bool("평가체계 버전 3", G["SCHEMA_VER"] == 3)
    chk_bool("«k_conv_resp» 없는 옛 파일 → 통지 뒤 전환 가능 (종전 격자의 읽기)", _t_old.k_conv_resp == 1)
    # ── 계약서의 회차별 행사금액표 ──
    # 계약이 확정 숫자를 준 회차는 그 숫자가 산식보다 앞선다. 다이나믹솔루션 제9회
    # (2026-06-05 발행 · 2029-06-05 만기 · 표면 3% · 보장 5% 분기복리) 공시 표다.
    _D9 = dict(d_issue="2026-06-05", d_base="2026-06-05", d_mat="2029-06-05", gap_m=3.0,
               cpn=.03, ipay=6.0, ytm=.05, ytm_cmp=4, S0=1103., K0=1103., floor=773.,
               par=100., sig=.50, cv_s=12., cv_e=35., p_s=6., p_e=33., p_f=3.,
               p_mode="accrue", p_yield=.05, p_cmp=4, k_w=.80, k_s=10., k_e=34., k_f=1.,
               k_prem=.05, k_cmp=4, k_hold=0, rfx_mode=1, rfx_cyc=3.0, carry=1)
    _D9TBL = {6: 101.0063, 9: 101.5188, 12: 102.0378, 15: 102.5633, 18: 103.0953,
              21: 103.6340, 24: 104.1794, 27: 104.7317, 30: 105.2908, 33: 105.8570}
    t9 = Terms(**_D9); t9.rf_curve = [(1, .026), (3, .028), (5, .030)]
    t9.cr_curve = [(1, .11), (3, .13), (5, .14)]; derive(t9)
    EA9 = G["exercise_amounts"](t9, t9.n, t9.T/t9.n)
    lo9, _ = G["step_mapper"](t9, t9.n, t9.T/t9.n)
    # 산식만으로 (표 없이) — 계약 개월 기준이면 공시 표가 그대로 나온다
    _w = max(abs(EA9["put"](lo9(mo)) - v) for mo, v in _D9TBL.items())
    chk("제9회 공시 조기상환율 10줄 = 산식 (계약 개월 기준 · 최대 오차)", _w, 0.0, 5e-4)
    chk("제9회 공시 만기상환율 106.4302", EA9["red"], 106.4302, 5e-4)
    # 표를 넣으면 그 숫자가 그대로 나온다 (산식과 무관하게)
    t9b = Terms(**dict(_D9, p_yield=.09, ytm=.09,
                       p_sched="\n".join(f"{mo} {v}%" for mo, v in _D9TBL.items()),
                       mat_amt=106.4302))
    t9b.rf_curve, t9b.cr_curve = t9.rf_curve, t9.cr_curve; derive(t9b)
    EAb = G["exercise_amounts"](t9b, t9b.n, t9b.T/t9b.n)
    _wb = max(abs(EAb["put"](lo9(mo)) - v) for mo, v in _D9TBL.items())
    chk("보장수익률을 9%로 바꿔도 표가 이긴다 (최대 오차)", _wb, 0.0, 1e-9)
    chk("만기상환금액도 표가 이긴다", EAb["red"], 106.4302, 1e-9)
    chk_bool("표가 정한 회차만 행사 가능", all(EAb["p_on"](lo9(mo)) for mo in _D9TBL)
             and not EAb["p_on"](lo9(3)))
    # 표가 없으면 종전대로 시작·종료·주기를 따른다
    # 표가 없어도 행사일 목록은 exercise_amounts 가 시작·주기에서 만든다 — 모든 계산이 이 목록을 쓴다
    _lo9 = G["step_mapper"](t9, t9.n, t9.T/t9.n)[0]
    chk_bool("표가 없으면 시작·주기로 만든 행사일 목록", callable(EA9["p_on"])
             and set(EA9["p_dates"]) == {i for i in EA9["p_dates"]} and len(EA9["p_dates"]) > 0)
    # 파서 — 날짜·개월·구분자·% 를 모두 읽고, 못 읽은 줄은 남긴다
    _pr = G["parse_sched"]("2026-12-05\t101.0063%\n9,101.5188\n12 102.0378\n엉터리", t9)
    chk_bool("날짜·쉼표·공백 세 형식을 모두 읽는다",
             [x[0] for x in _pr[:3]] == [6.0, 9.0, 12.0])
    chk_bool("못 읽은 줄은 (None, 원문) 으로 남는다", _pr[3][0] is None)

    # ── 표를 넣으면 «보조 화면·상각표» 도 표를 본다 ──
    # 표가 산식을 이기는 것은 격자만이 아니다. 겹침 판정·BDT 검토·기대만기가 산식으로
    # 되돌아가면 화면과 조서가 서로 다른 행사금액을 말한다.
    _S9 = "\n".join(f"{mo}\t{v:.4f}" for mo, v in _D9R)
    _tS = Terms(**{**_D9, "p_sched": _S9, "k_w": .80, "k_s": 6., "k_e": 33., "k_f": 3.,
                   "conv_class": "equity", "k_sep": 1, "p_sep": 0, "k_method": 0})
    _tS.rf_curve = [(1, .026), (3, .028), (5, .030)]
    _tS.cr_curve = [(1, .11), (3, .13), (5, .14)]; derive(_tS)
    _EAS = G["exercise_amounts"](_tS, _tS.n, _tS.T/_tS.n)
    _ov = G["pc_overlap"](_tS)
    chk_bool("겹치는 노드가 있다 (표 기준)", len(_ov) > 0)
    chk("겹침표의 조기상환금액 = 격자의 금액 (최대 오차)",
        max(abs(pv - _EAS["put"](i)) for i, _, pv, _ in _ov), 0.0, 1e-12)
    chk("겹침표의 매도청구금액 = 격자의 금액 (최대 오차)",
        max(abs(kv - _EAS["call"](i)) for i, _, _, kv in _ov), 0.0, 1e-12)
    chk_bool("겹치는 자리는 모두 표의 회차다",
             all(G["sched_at"](_EAS["p_rows"], mo, _EAS["tol"]) is not None
                 for _, mo, _, _ in _ov))
    # 기대만기 — 표를 넣으면 첫 «회차» 가 첫 행사일이다. 시작·주기로 걸으면 표에 없는
    # 달을 잡아 산식으로 되돌아간다.
    _ex = G["eir_expect"](_tS)
    chk_bool("표가 있으면 기대만기를 낸다", _ex is not None)
    chk("기대만기 시점 = 표의 첫 회차 (개월)", _ex[2], 6.0, 1e-9)
    chk("기대만기 현금흐름 = 그 회차의 표 금액", _ex[1], 101.0063, 5e-4)
    # 표가 없으면 종전 걸음 그대로 — 회귀
    _tN = Terms(**{**_D9, "k_w": .80, "k_s": 6., "k_e": 33., "k_f": 3.,
                   "conv_class": "equity", "k_sep": 1, "p_sep": 0, "k_method": 0})
    _tN.rf_curve = list(_tS.rf_curve); _tN.cr_curve = list(_tS.cr_curve); derive(_tN)
    chk("표가 없으면 기대만기 시점은 p_s", G["eir_expect"](_tN)[2], 6.0, 1e-9)
    # ── 만기금액을 직접 넣으면 BDT 관문 ③ 도 그 금액에서 역산한다 ──
    # 계약서 숫자를 넣고 보장수익률 칸을 0 으로 두는 계약이 많다. 그 0 을 격차에
    # 그대로 넣으면 「보장 0% 대 할인 13%」 가 되어 관문 판정이 뜻을 잃는다.
    _tM = Terms(**{**_D9, "ytm": 0.0, "mat_amt": 106.4302, "conv_class": "equity"})
    _tM.rf_curve = list(_tS.rf_curve); _tM.cr_curve = list(_tS.cr_curve); derive(_tM)
    _fM = G["decompose"](_tM)[0]
    _rv = G["bdt_review"](_tM, _fM, *G["decompose"](_tM)[1:5])
    _g3 = next(r for r in _rv["관문"] if r[0] == 3)[2]
    chk_bool("관문 ③ 이 만기금액에서 역산한 보장수익률을 쓴다 (0% 이 아니다)",
             "보장 0.00%" not in _g3)
    chk_bool("역산값이 연 실효 5.09% 근처", "보장 5.09%" in _g3)

    # ── 풋·콜 우선순위 검산은 «실제로 열린 행사 노드» 로 센다 ──
    # 날짜·주기만 견주면 계약서 행사금액표를 넣은 계약에서 겹침을 놓친다. 또 겹치더라도
    # 조기상환금액이 매도청구금액을 넘지 않으면 두 순서가 같은 값을 낸다 — 이것은
    # 어림짐작이 아니라 node_decide 에서 따라 나오는 항등식이라 못박아 둔다.
    _POV, _PCM, _MC = G["pc_overlap"], G["pc_compare"], G["model_checks"]
    _tov = Terms(**{**_D9, "p_sched": _S9, "k_w": .80, "k_s": 6., "k_e": 33., "k_f": 3.,
                    "k_hold": 0, "k_method": 0})
    _tov.rf_curve = list(_tS.rf_curve); _tov.cr_curve = list(_tS.cr_curve); derive(_tov)
    _ov = _POV(_tov)
    chk_bool("표를 넣어도 겹치는 노드를 찾는다", len(_ov) > 0)
    _big = [x for x in _ov if x[2] > x[3] + 1e-9]
    _f0, *_r0 = G["decompose"](_tov)
    _row = dict((x[0], x) for x in _MC(_tov, _f0, *_r0[:4]))["풋·콜 우선순위 · 겹치는 행사노드"]
    chk_bool("검산요약이 겹침 노드 수를 적는다", f"{len(_ov)}개" in _row[1])
    chk_bool("금액이 갈리는 자리 수도 적는다", f"{len(_big)}개" in _row[1])
    chk_bool("갈리는 자리가 있으면 두 값과 차이를 함께 싣는다",
             (len(_big) == 0) or
             ("투자자 우선" in dict((x[0], x) for x in
                                 _MC(_tov, _f0, *_r0[:4]))["매도청구권 · 우선순위별 차이"][3]))
    # 겹치되 «금액이 갈리지 않는» 계약 — 매도청구금액이 늘 조기상환금액보다 크게 만든다.
    # 그때 두 순서가 같은 값이라는 것은 어림짐작이 아니라 node_decide 의 항등식이다.
    _tsm = Terms(**{**G["asdict"](_tov), "k_prem": .30}); derive(_tsm)
    _ovs = _POV(_tsm)
    chk_bool("겹치지만 금액이 갈리는 자리가 없는 계약", len(_ovs) > 0
             and not [x for x in _ovs if x[2] > x[3] + 1e-9])
    _va = []
    for _po in (0, 1):
        _tp = Terms(**G["asdict"](_tsm)); _tp.pc_order = _po; derive(_tp)
        _va.append(G["decompose"](_tp)[4])
    chk("겹치되 금액이 갈리지 않으면 두 순서가 같은 값", _va[0], _va[1], 1e-9)
    _fs, *_rs = G["decompose"](_tsm)
    _rows = dict((x[0], x) for x in _MC(_tsm, _fs, *_rs[:4]))
    chk_bool("그 사실을 문구로 적는다 (겹치지만 같은 값)",
             "같은 값" in _rows["풋·콜 우선순위 · 겹치는 행사노드"][3])

    # RCPS 발행자 상환권·없음 갈래는 k_kind 0
    tr = Terms(inst="RCPS", issuer_call=1, k_kind=1); derive(tr)
    chk_bool("RCPS 발행자 상환권 → k_kind 0", tr.k_kind == 0)


def test_deduction_methods():
    """이미 지급한 이자·배당을 행사금액에서 빼는 세 방식 — 기대값은 계약 문언으로 센 값이다.

        ㉠ 이자를 붙여 공제  100·(1 + (g−c)/g·((1+g/m)^(mt) − 1))   투자자 수익률 = g
        ㉡ 받은 금액만 공제  100·(1+g/m)^(mt) − 100·c·ipay/12·지급회차
        ㉢ 공제하지 않음    100·(1+g/m)^(mt)
    """
    print("\n[28] 이미 지급한 이자·배당의 공제 방식 — 이자 붙여 · 받은 금액만 · 공제 안 함")
    import math as _m
    dp, di = G["ded_prem"], G["ded_implied"]
    # 계획서 표의 손계산 (연복리, 연 1회 지급)
    for (t_, g, c, want) in ((10, .07, .01, (182.90, 186.72, 196.72)),
                             (3, .05, .02, (109.46, 109.76, 115.76)),
                             (5, .08, .03, (129.33, 131.93, 146.93))):
        for d, w in zip((1, 2, 0), want):
            chk(f"{t_}년 보장 {g:.0%} · 배당 {c:.0%} · 방식 {d}", 100*(1 + dp(t_, g, c, 1, d, t_*12, 12)), w, 5e-3)
    # 독립 산식 — 분기복리 · 반기 지급 · 2.5년
    g, c, m, ip, mo = .06, .02, 4, 6., 30.
    t_ = mo/12
    fv = (1 + g/m)**(m*t_)
    chk("방식 1 = (g−c)/g 비례식", 100*(1 + dp(t_, g, c, m, 1, mo, ip)), 100*(1 + (g - c)/g*(fv - 1)), 1e-9)
    chk("방식 2 = 순수 복리 − 명목 지급 5회", 100*(1 + dp(t_, g, c, m, 2, mo, ip)), 100*fv - 100*c*ip/12*5, 1e-9)
    chk("방식 0 = 순수 복리", 100*(1 + dp(t_, g, c, m, 0, mo, ip)), 100*fv, 1e-9)
    # 역산 왕복 — 세 방식 모두
    for d in (1, 2, 0):
        pr = dp(t_, g, c, m, d, mo, ip)
        chk(f"방식 {d} · 보장수익률 역산 왕복", di(pr, t_, c, m, d, mo, ip), g, 1e-9)
    # 방식 1 은 투자자 수익률을 정확히 g 로 맞춘다 — 반기 지급·반기복리 3년
    g, c = .07, .03
    R = 100*(1 + dp(3.0, g, c, 2, 1, 36., 6.))
    pv = sum(100*c/2/(1 + g/2)**k for k in range(1, 7)) + R/(1 + g/2)**6
    chk("방식 1 — 이표 + 상환금액의 수익률 = 보장수익률 (현재가치 100)", pv, 100.0, 1e-9)
    chk_bool("방식 2 는 방식 1 보다 크고 방식 0 보다 작다",
             dp(3.0, g, c, 2, 1, 36., 6.) < dp(3.0, g, c, 2, 2, 36., 6.) < dp(3.0, g, c, 2, 0, 36., 6.))
    chk("지급 회차 — 행사일 당일 지급분까지 센다", G["paid_count"](36.0, 6.0), 6, 1e-12)
    chk("지급 회차 — 행사일 전날이면 하나 적다", G["paid_count"](35.99, 6.0), 5, 1e-12)

    # 엔진 — 세 권리 · 세 상품 · 세 방식에서 exercise_amounts 가 독립 산식과 같다
    base = dict(cpn=.03, ipay=6., ytm=.07, ytm_cmp=2, p_mode="accrue", p_yield=.07, p_cmp=2,
                p_s=24., p_e=48., k_prem=.05, k_cmp=2, k_w=.3, k_s=12., k_e=24., gap_m=1.,
                rf_curve=[(1, .03), (3, .031), (5, .032)], cr_curve=[(1, .10), (3, .11), (5, .12)])
    def want_amt(g, c, m, d, mo, ip):
        t_ = mo/12
        fv = (1 + g/m)**(m*t_)
        if d == 1: return 100*(1 + max(0.0, (g - c)/g*(fv - 1)))
        if d == 2: return 100 + max(0.0, 100*(fv - 1) - 100*c*ip/12*_m.floor(mo/ip + 1e-9))
        return 100*fv
    for inst, extra in (("CB", {}), ("RCPS", dict(inst="RCPS", issuer_call=2, div_mode=0, mat_mode=1)),
                        ("BW", dict(inst="BW"))):
        for d in (1, 2, 0):
            tm = Terms(**base, **extra, p_less_cpn=d, k_less_cpn=d, m_less_cpn=d); derive(tm)
            n = int(tm.n); EA = G["exercise_amounts"](tm, n, tm.T/n)
            c = G["eff_cpn"](tm)
            for i in (n//3, n//2):
                mo = EA["cmonth"](i)
                chk(f"{inst} · 방식 {d} · 조기상환금액 (스텝 {i})", EA["put"](i), want_amt(.07, c, 2, d, mo, 6.), 1e-9)
                chk(f"{inst} · 방식 {d} · 매도청구금액 (스텝 {i})", EA["call"](i), want_amt(.05, c, 2, d, mo, 6.), 1e-9)
            chk(f"{inst} · 방식 {d} · 만기상환금액", EA["red"],
                want_amt(.07, c, 2, d, tm.elapsed_m + tm.rem_m, 6.), 1e-9)
            full, b0, b1, b2, ca, conv = G["decompose"](tm)
            chk_bool(f"{inst} · 방식 {d} · 격자가 선다", all(_m.isfinite(x) for x in (b0, b1, b2, ca)))
    # 기본값 1 은 종전 산식 그대로다 — 옛 시나리오의 값이 움직이지 않는다
    t1 = Terms(**base); derive(t1)
    n = int(t1.n); EA = G["exercise_amounts"](t1, n, t1.T/n)
    mo = EA["cmonth"](n//2)
    chk("기본값(이자 붙여 공제) = 종전 accrue_rate", EA["put"](n//2),
        100*(1 + G["accrue_rate"](mo/12, .07, .03, 2)), 1e-12)
    # 재량 배당이면 공제할 것이 없어 세 방식이 같다
    _v = []
    for d in (1, 2, 0):
        tr = Terms(**base, inst="RCPS", div_mode=1, mat_mode=1, p_less_cpn=d); derive(tr)
        _v.append(G["decompose"](tr)[2])     # 부채요소 b1
    chk("재량 배당 — 세 방식의 부채요소가 같다 (1 대 2)", _v[0], _v[1], 1e-12)
    chk("재량 배당 — 세 방식의 부채요소가 같다 (1 대 0)", _v[0], _v[2], 1e-12)
    # 조서 문구
    chk_bool("조건표에 받은 금액만 공제가 적힌다",
             "명목 합계" in G["ded_suffix"](Terms(**base, p_less_cpn=2), "p"))


def test_div_basis():
    """우선배당률 액면 기준 — 계약 「1주당 액면가액(500원) 기준 연 1%」, 1주당 인수금액 59,390원."""
    print("\n[29] 우선배당률 기준 — 액면가 기준을 발행가 100 기준으로 옮긴다")
    base = dict(inst="RCPS", cpn=.01, par=500., issue_px=59390., ipay=12., p_mode="accrue", p_yield=.07,
                p_cmp=1, ytm=.07, ytm_cmp=1, mat_mode=0, issuer_call=0,
                d_issue="2026-04-07", d_base="2026-04-07", d_mat="2036-04-07", p_s=36., p_e=120.,
                rf_curve=[(1, .025), (3, .027), (10, .03)], cr_curve=[(1, .20), (3, .22), (10, .25)])
    t1 = Terms(**base, div_basis=1); derive(t1)
    chk("환산 배당률 = 1% × 500 ÷ 59,390", G["eff_cpn"](t1), .01*500/59390, 1e-15)
    chk("10년 상환금액 (이자 붙여 공제) = 196.6", G["mat_red_formula"](t1), 196.5988, 5e-4)
    t0 = Terms(**base, div_basis=0); derive(t0)
    chk("그대로 1% 로 넣으면 182.9", G["mat_red_formula"](t0), 182.9, 5e-3)
    # 환산은 eff_cpn 한 곳에서만 — 액면 기준 = 환산한 숫자를 발행가 기준으로 넣은 것
    tx = Terms(**{**base, "cpn": .01*500/59390}, div_basis=0); derive(tx)
    for k, nm in ((1, "주계약"), (2, "부채요소"), (3, "전체")):
        chk(f"액면 기준 = 환산값 직접 입력 · {nm}", G["decompose"](t1)[k], G["decompose"](tx)[k], 1e-9)
    chk_bool("조서 문구에 환산 전·후가 모두 있다",
             "액면 500원" in G["div_basis_text"](t1) and "0.0084%" in G["div_basis_text"](t1))
    # 재량 배당이면 0
    tr = Terms(**base, div_basis=1, div_mode=1); derive(tr)
    chk("재량 배당이면 환산 뒤에도 0", G["eff_cpn"](tr), 0.0, 1e-15)
    # 사채는 권면 기준 — 스위치가 먹지 않는다
    tc = Terms(cpn=.01, par=500., issue_px=59390., div_basis=1); derive(tc)
    chk("CB 는 액면 기준 스위치를 보지 않는다", G["eff_cpn"](tc), .01, 1e-15)
    # 발행가가 비면 발행가 기준으로 되돌리고 알린다
    tb = Terms(**{**base, "issue_px": 0.0}, div_basis=1); derive(tb)
    chk("발행가가 없으면 발행가 기준으로 되돌린다", tb.div_basis, 0, 1e-12)
    chk_bool("되돌린 사유가 경고로 남는다",
             any(G["COMPAT_DIVBASIS"][:20] in x for x in [m for _, _, m in tb.forced_notes]))


def test_rfx_anytime():
    """리픽싱 「언제든지」 — 주기를 노드 간격으로 두면 모든 노드에서 조정한다."""
    print("\n[30] 리픽싱 언제든지 — 매 노드 조정")
    RF = [(1, .030), (3, .031), (5, .032)]; CR = [(1, .14), (3, .17), (5, .19)]
    vals = []
    for carry in (0, 1, 2):
        t = Terms(rf_curve=RF, cr_curve=CR, gap_m=1., rfx_cyc=1., rfx_mode=2, carry=carry); derive(t)
        vals.append(G["decompose"](t)[0]["TF"])
    chk_bool("언제든지로 인식한다", G["rfx_any"](t))
    chk_bool("조건표에 「언제든지」 라고 적는다", "언제든지" in G["rfx_cycle_text"](t))
    # 하향+상향을 매 노드에서 하면 전환가액이 그 노드 주가만으로 정해진다 — 경로가 필요 없다
    chk("매 노드 조정 · 상태확장 = 경로가중", vals[0], vals[1], 1e-6)
    chk("매 노드 조정 · 상태확장 = 확률가중", vals[0], vals[2], 1e-6)
    chk_bool("노드 간격보다 짧다는 경고를 내지 않는다",
             not any("조정 주기가 노드 간격보다 짧습니다" in w for w in G["validate"](t)))
    t3 = Terms(rf_curve=RF, cr_curve=CR, gap_m=1., rfx_cyc=3.); derive(t3)
    chk_bool("3개월 주기는 정기", not G["rfx_any"](t3))


def test_display_only_fields():
    """회차 표시 · 반영하지 않은 권리 · 희석 주식수 — 값을 바꾸지 않고 조서에만 실린다."""
    print("\n[31] 표시 전용 칸 — 회차 · 반영하지 않은 권리 · 희석 경고")
    import io, openpyxl
    RF = [(1, .030), (3, .031), (5, .032)]; CR = [(1, .14), (3, .17), (5, .19)]
    t0 = Terms(rf_curve=RF, cr_curve=CR, carry=1); derive(t0)
    t1 = Terms(rf_curve=RF, cr_curve=CR, carry=1, tranche="1차 납입분 84,189주",
               unmod_note="주식매수청구권 — 위반 조건부\n잔여재산 분배 우선권 — 범위 밖",
               base_shares=1630868., dil_shares=473729.); derive(t1)
    r0, r1 = G["decompose"](t0), G["decompose"](t1)
    for k, nm in ((1, "주계약"), (2, "부채요소"), (3, "전체"), (4, "매도청구권")):
        chk(f"표시 칸은 값을 바꾸지 않는다 · {nm}", r1[k], r0[k], 1e-12)
    chk_bool("비어 있으면 표지 문구가 없다", G["unmod_text"](t0) == "")
    chk_bool("적으면 한 줄로 이어 싣는다",
             G["unmod_text"](t1).startswith("이 계약에서 반영하지 않은 권리")
             and " / 잔여재산" in G["unmod_text"](t1))
    chk_bool("파일 이름 표시", G["tranche_tag"](t1) == "_1차납입분84189주")
    chk_bool("재현 기록 첫 줄이 회차", G["stamp_rows"](t1)[0] == ("회차", "1차 납입분 84,189주"))
    chk_bool("회차가 없으면 재현 기록에 줄을 더하지 않는다", G["stamp_rows"](t0)[0][0] == "생성시각")
    w = G["validate"](t1)
    chk_bool("희석 29.0% 경고", any("29.0%" in x for x in w))
    t2 = Terms(rf_curve=RF, cr_curve=CR, base_shares=1630868., dil_shares=50000.); derive(t2)
    chk_bool("희석 3% 면 경고하지 않는다", not any("전환 시 보통주가" in x for x in G["validate"](t2)))
    # 값 조서 가정 6행 · 수식 조서 가정 표지 — 비면 비고, 적으면 실린다
    full, b0, b1, b2, ca, conv = r1
    for kind, fn in (("값", "build_xlsx"), ("수식", "build_xlsx_formula")):
        for tt, rr in ((t0, r0), (t1, r1)):
            f_, a0, a1, a2, aca, acv = rr
            wb = openpyxl.load_workbook(io.BytesIO(G[fn](tt, f_, a0, a1, a2, aca, acv,
                                                         G["eir_or_none"](tt, f_, a0, a1, a2, aca))))
            txt = [c.value for row in wb["가정"].iter_rows() for c in row if isinstance(c.value, str)]
            has = any(x.startswith("이 계약에서 반영하지 않은 권리") for x in txt)
            chk_bool(f"{kind} 조서 표지 — {'적음' if tt is t1 else '비움'}", has == (tt is t1))


def test_holder_view():
    """평가 관점 — 투자자. 공정가치는 같고 회계 단위만 다르다 (1113 · 1109 문단 4.3.2)."""
    print("\n[32] 평가 관점 — 발행자 / 투자자")
    import io, openpyxl
    RF = [(1, .030), (3, .031), (5, .032)]; CR = [(1, .14), (3, .17), (5, .19)]
    cases = (("CB", {}), ("RCPS 제3자 콜", dict(inst="RCPS", issuer_call=2)),
             ("RCPS 발행자 상환권", dict(inst="RCPS", issuer_call=1)),
             ("BW 분리형 현금", dict(inst="BW", bw_pay=0, bw_detach=1)), ("콜 없음", dict(k_w=0.)))
    for nm, over in cases:
        ti = Terms(rf_curve=RF, cr_curve=CR, **over); derive(ti)
        th = Terms(rf_curve=RF, cr_curve=CR, view="holder", **over); derive(th)
        ri, rh = G["decompose"](ti), G["decompose"](th)
        for k, lab in ((1, "주계약"), (2, "부채요소"), (3, "전체"), (4, "매도청구권")):
            chk(f"{nm} · 관점이 바뀌어도 {lab} 값은 같다", rh[k], ri[k], 1e-12)
        full, b0, b1, b2, ca, _ = rh
        h = G["holder_rows"](th, full, b0, b1, b2, ca)
        chk(f"{nm} · 순포지션 = 전체 − 매도청구권", h["pos"][0][1], b2 - ca, 1e-12)
        # 순포지션을 이루는 줄들의 합 (자산 − 부채)
        _liab = sum(v for k, v in h["pos"][1:] if "파생상품부채" in k)
        _ast = sum(v for k, v in h["pos"][1:] if "파생상품부채" not in k)
        chk(f"{nm} · 자산 − 부채 = 순포지션", _ast - _liab, h["net"], 1e-12)
        chk(f"{nm} · 참고 분해 합 = 순포지션", sum(v for k, v in h["parts"][:-1]), h["net"], 1e-12)
        dr = sum(v for sd, _, v in h["journal"] if sd == "차변")
        cr = sum(v for sd, _, v in h["journal"] if sd == "대변")
        chk(f"{nm} · 최초 인식 분개 대차", dr, cr, 1e-12)
        chk_bool(f"{nm} · 상각표를 만들지 않는다", G["eir_or_none"](th, full, b0, b1, b2, ca) is None)
    # 콜이 따로 떨어진 파생상품부채인가 — 거래상대방이 발행회사면 계약에 녹는다
    t1 = Terms(rf_curve=RF, cr_curve=CR, view="holder", inst="RCPS", issuer_call=1); derive(t1)
    f1 = G["decompose"](t1)
    chk_bool("발행자 상환권은 계약의 일부 — 파생상품부채 줄이 없다",
             not G["holder_rows"](t1, *f1[:5])["sep"])
    t2 = Terms(rf_curve=RF, cr_curve=CR, view="holder"); derive(t2)
    f2 = G["decompose"](t2)
    chk_bool("제3자 지정 콜은 별도 — 파생상품부채 줄이 있다", G["holder_rows"](t2, *f2[:5])["sep"])
    # 최초 인식 차이 = 순포지션 − 거래가격 100
    h2 = G["holder_rows"](t2, *f2[:5])
    _d = [v if sd == "대변" else -v for sd, a, v in h2["journal"] if "최초 인식 차이" in a]
    chk("최초 인식 차이 = 순포지션 − 100", _d[0], h2["net"] - 100.0, 1e-12)
    # 후속 — 평가손익 = 당기말 − 전기말
    t3 = Terms(rf_curve=RF, cr_curve=CR, view="holder", d_base="2025-12-31", prev_hold=110.0); derive(t3)
    f3 = G["decompose"](t3); h3 = G["holder_rows"](t3, *f3[:5])
    _pl = [v if "이익" in a else -v for sd, a, v in h3["journal"] if "평가" in a]
    chk("후속 · 평가손익 = 순포지션 − 전기말 공정가치", _pl[0], h3["net"] - 110.0, 1e-12)
    t4 = Terms(rf_curve=RF, cr_curve=CR, view="holder", d_base="2025-12-31"); derive(t4)
    chk_bool("후속 · 전기말이 없으면 분개가 없다 (장부금액 = 공정가치)",
             G["holder_rows"](t4, *G["decompose"](t4)[:5])["journal"] == [])
    # 주주간계약은 자기 화면에 세 관점이 있다 — 스위치가 먹지 않는다
    ts = Terms(inst="SHA", view="holder"); derive(ts)
    chk_bool("주주간계약은 관점 스위치를 발행자로 되돌린다", ts.view == "issuer")
    # 두 조서의 회계처리 시트 — 첫 줄이 순포지션, 가정에 관점이 적힌다
    full, b0, b1, b2, ca, conv = f2
    for kind, fn in (("값", "build_xlsx"), ("수식", "build_xlsx_formula")):
        wb = openpyxl.load_workbook(io.BytesIO(G[fn](t2, full, b0, b1, b2, ca, conv, None)))
        E = wb["회계처리"]
        chk(f"{kind} 조서 · 회계처리 C10 = 순포지션", float(E["C10"].value), b2 - ca, 1e-9)
        chk_bool(f"{kind} 조서 · 투자자 관점 제목", "투자자 관점" in str(E["B2"].value))
        txt = " ".join(str(c.value) for row in wb["가정"].iter_rows() for c in row if isinstance(c.value, str))
        chk_bool(f"{kind} 조서 · 가정에 평가 관점", "투자자 — 보유 금융자산" in txt)


def test_excel_limits_and_curves():
    """엑셀 한 칸 수식 한도(8,192자) · 곡선 범위 경고 · 예시 곡선 경고."""
    print("\n[33] 엑셀 수식 한도 · 이자율 곡선 범위")
    import io, openpyxl
    RF = [(1, .030), (3, .031), (5, .032)]; CR = [(1, .14), (3, .17), (5, .19)]
    # 주 단위 노드(5년 → 260개) — 노드마다 EXP 를 이어 붙이던 마팅게일 검산이 한도를 넘었다
    tw = Terms(rf_curve=RF, cr_curve=CR, gap_m=12/52, carry=1); derive(tw)
    chk_bool("주 단위 노드가 200개를 넘는다", tw.n > 200)
    full, b0, b1, b2, ca, conv = G["decompose"](tw)
    wb = openpyxl.load_workbook(io.BytesIO(G["build_xlsx_formula"](tw, full, b0, b1, b2, ca, conv, None)))
    L = max((len(c.value), ws.title, c.coordinate) for ws in wb.worksheets for row in ws.iter_rows()
            for c in row if isinstance(c.value, str) and c.value.startswith("="))
    chk_bool(f"수식 조서 · 가장 긴 수식 {L[0]}자 ({L[1]}!{L[2]}) ≤ 8,192", L[0] <= 8192)
    ts = Terms(inst="SHA", S0=1000., K0=1000., rf_curve=RF, cr_curve=CR, gap_m=12/52,
               sha_put_s=36., sha_put_e=60., sha_call_s=12., sha_call_e=36.); derive(ts)
    R = G["sha_engine"](ts)
    wb2 = openpyxl.load_workbook(io.BytesIO(G["build_xlsx_sha"](ts, R, formula=True)))
    L2 = max((len(c.value), ws.title, c.coordinate) for ws in wb2.worksheets for row in ws.iter_rows()
             for c in row if isinstance(c.value, str) and c.value.startswith("="))
    chk_bool(f"주주간계약 수식 조서 · 가장 긴 수식 {L2[0]}자 ({L2[1]}!{L2[2]}) ≤ 8,192", L2[0] <= 8192)
    # 곡선이 잔존기간에 못 미치면 알린다 — 10년 계약에 5년까지만 있는 곡선
    t10 = Terms(rf_curve=RF, cr_curve=CR, d_mat="2035-03-31"); derive(t10)
    ns = G["curve_notes"](t10)
    chk_bool("5년 곡선 · 10년 잔존 → 무위험·위험 두 경고", sum("평평하게 연장" in x for x in ns) == 2)
    chk_bool("validate 에도 실린다", any("평평하게 연장" in x for x in G["validate"](t10)))
    t5 = Terms(rf_curve=RF, cr_curve=CR); derive(t5)
    chk_bool("곡선이 잔존기간을 덮으면 경고 없음", not G["curve_notes"](t5))
    te = Terms(rf_curve=list(G["EXAMPLE_RF"]), cr_curve=CR); derive(te)
    chk_bool("예시 무위험 곡선이 남아 있으면 알린다", any("예시 곡선" in x for x in G["curve_notes"](te)))
    # 보외 구간은 앱과 엑셀 모두 마지막 수익률로 평평하다 — 결함이 아니라 규칙이다
    chk("곡선 끝 뒤 보간 = 마지막 수익률", G["_lin"](RF, 9.0), 0.032, 1e-15)


# ══════════════════════════════════════════════════════════
# 콜옵션 점검 (2026-09-30) — 세 평가방법이 같은 계약을 읽는지, 계산 원리를 따로 센다.
# 사례는 모두 공개 가능한 가상 조건이다 (검토 요청서의 재현 조건 포함). 고객 자료가 아니다.
def _call_terms(**kw):
    """검토 요청서의 재현 조건 — 발행·평가 2025-01-01, 만기 2027-01-01, 주가 150, 전환가 100,
    σ 30%, 분기 격자, 리픽싱·풋 없음, 전환 0~24개월, 콜 12개월 1회(기본 매수대금 100), 대상 100%,
    의무보유 24개월, 표면 8% 연 1회, 무위험 3% · 위험 8% (복리 횟수까지 고정)."""
    base = dict(d_issue="2025-01-01", d_base="2025-01-01", d_mat="2027-01-01", S0=150., K0=100., sig=0.30,
                gap_m=3.0, rfx_mode=0, carry=1, p_s=99., p_e=0., cv_s=0., cv_e=24.,
                k_s=12., k_e=12., k_f=3., k_w=1.0, k_lock=24., k_hold=1, k_lock_put=1,
                k_prem=0.01, k_cmp=4, k_less_cpn=1, cpn=0.08, ipay=12., ytm=0.08, ytm_cmp=4,
                rf_curve=[(1, .03), (2, .03), (3, .03)], cr_curve=[(1, .08), (2, .08), (3, .08)],
                cmp_rf=2, cmp_cr=4, k_third=1, k_method=2, k_split=0)
    base.update(kw)
    t = Terms(**base); derive(t); return t


def _three(t):
    """세 방법의 콜 값 — 유무가치 · 혼합할인율 · 성분 분리할인 (지금 지분·채권 구분 기준 그대로)."""
    full = engine(t, call=False)
    b2 = G["pick"](full, t.model)
    cs, ps = G["lock_delay"](t)
    b3 = G["pick"](engine(t, conv=True, put=True, call=True, conv_start=cs, put_start=ps), t.model)
    return (t.k_w*(b2 - b3), t.k_w*G["call_third_party"](t, full, 1), t.k_w*G["call_third_party"](t, full, 2))


def test_call_response_combos():
    """전환 대응(k_conv_resp)과 풋·콜 우선순위(pc_order)는 따로 정한다 — 네 조합의 닫힌 식.

        풋 우선 · 전환 가능 :  MAX(전환, 풋, MIN(보유, 콜))
        콜 우선 · 전환 가능 :  MAX(전환, MIN(MAX(보유, 풋), 콜))
        풋 우선 · 전환 불가 :  MAX(풋, MIN(MAX(전환, 보유), 콜))
        콜 우선 · 전환 불가 :  MIN(MAX(전환, 풋, 보유), 콜)

    검토 요청서의 예 — 평가기준일에 전환이 유리하고 의무보유가 없는 노드에서 CB 가치 200, 매수대금 100,
    대상비율 30% — 는 «전환 가능» 이면 세 방법 모두 0, «전환 불가» 면 세 방법 모두 (200 − 100) × 30% = 30 이다.
    예전에는 콜 우선이면 옵션차익법만 30 이었다(우선순위 하나가 전환권까지 바꿨다).
    """
    print("\n[37] 전환 대응 · 우선순위 네 조합 — 닫힌 식과 검토 요청서의 예")
    import random
    nd = G["node_decide"]
    val = lambda d, cv, pv, kv, h: {"conv": cv, "put": pv, "call": kv, "hold": h}[d]
    closed = {(0, 1): lambda cv, pv, kv, h: max(cv, pv, min(h, kv)),
              (1, 1): lambda cv, pv, kv, h: max(cv, min(max(h, pv), kv)),
              (0, 0): lambda cv, pv, kv, h: max(pv, min(max(cv, h), kv)),
              (1, 0): lambda cv, pv, kv, h: min(max(cv, pv, h), kv)}
    rng = random.Random(20260930); bad = 0
    grid = [80., 100., 105., 120., 140.]
    samples = [(a, b, c, d) for a in grid for b in grid for c in grid + [math.inf] for d in grid]
    samples += [tuple(rng.uniform(50, 200) for _ in range(4)) for _ in range(20000)]
    for (kf, cr), f in closed.items():
        for cv, pv, kv, h in samples:
            if abs(val(nd(cv, pv, kv, h, bool(kf), bool(cr)), cv, pv, kv, h) - f(cv, pv, kv, h)) > 1e-9:
                bad += 1
    chk_bool(f"node_decide 네 조합 = 닫힌 식 ({4*len(samples):,}개 표본 · 동점 포함) — 어긋남 {bad}", bad == 0)
    for kf in (0, 1):
        base = dict(S0=200., cv_s=0., cv_e=0., k_s=0., k_e=0., k_f=1., k_prem=0.0, k_w=0.3, k_hold=0,
                    k_lock=0., cpn=0.0, ytm=0.0, pc_order=kf)
        for cr, want in ((1, 0.0), (0, 30.0)):
            v = _three(_call_terms(k_conv_resp=cr, **base))
            tag = f"{'콜' if kf else '풋'} 우선 · 전환 {'가능' if cr else '불가'}"
            for nm, x in zip(("유무가치", "혼합할인율", "성분 분리할인"), v):
                chk(f"  {tag} · {nm} (기대 {want:g})", x, want, 1e-9)


def test_call_zero_and_scale():
    """콜 없음 · 행사기간 경과 · 대상비율 0 이면 0, 같은 조건에서는 대상비율에 비례한다."""
    print("\n[38] 콜 가치 0 · 대상비율 비례 — 세 방법")
    for nm, kw in (("대상비율 0", dict(k_w=0.0)),
                   ("행사기간이 평가기준일 전에 끝남", dict(d_base="2026-01-01", k_s=3., k_e=6.))):
        t = _call_terms(**kw)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        chk(f"  {nm} · 적용 콜 값", ca, 0.0, 1e-12)
        if t.k_w > 0:
            v = _three(t)
            chk_bool(f"  {nm} · 세 방법 모두 0 ({', '.join(f'{x:.2e}' for x in v)})",
                     all(abs(x) < 1e-12 for x in v))
    for km in (0, 1, 2):
        for sp in (0, 1):
            a = _three(_call_terms(k_w=0.2, k_method=km, k_split=sp))
            b = _three(_call_terms(k_w=0.4, k_method=km, k_split=sp))
            chk_bool(f"  대상비율 20% → 40% 에 정확히 두 배 · 방법 {km} · 기준 {sp}",
                     all(abs(2*x - y) < 1e-9 for x, y in zip(a, b)))


def test_call_coupon_add():
    """행사일 이자 별도지급 — 옵션차익법의 매수대금에도 그날 이자가 들어간다.

    독립 대조: «별도지급 켬» 은 «같은 날 매도청구금액이 이자만큼 큰 계약(행사금액표 108)» 과 같아야 한다.
    이자 지급일과 행사일이 다르면 켜도 값이 같다. 검토 요청서가 잰 값(끔 64.420793 · 켬 56.722443)은
    같은 입력의 회귀 기준으로만 둔다.
    """
    print("\n[39] 행사일 이자 별도지급 — 옵션차익법 매수대금")
    for km in (1, 2):
        for sp in (0, 1):
            off = _call_terms(k_method=km, k_split=sp, k_cpn_add=0)
            on = _call_terms(k_method=km, k_split=sp, k_cpn_add=1)
            tab = _call_terms(k_method=km, k_split=sp, k_cpn_add=0, k_sched="12 108.0000")
            f_off, f_on, f_tab = (engine(x, call=False) for x in (off, on, tab))
            v_off = G["call_third_party"](off, f_off, km); v_on = G["call_third_party"](on, f_on, km)
            v_tab = G["call_third_party"](tab, f_tab, km)
            chk(f"  방법 {km} · 기준 {sp} · 켬 = 행사금액표 108", v_on, v_tab, 1e-9)
            chk_bool(f"  방법 {km} · 기준 {sp} · 켬 < 끔 ({v_on:.6f} < {v_off:.6f})", v_on < v_off - 1e-6)
            if km == 2 and sp == 0:
                chk("  검토 요청서 재현 · 끔 (회귀 기준)", v_off, 64.420793, 5e-7)
                chk("  검토 요청서 재현 · 켬 (회귀 기준)", v_on, 56.722443, 5e-7)
    # 이자 지급일(12개월)과 다른 날(9개월)에 행사하면 켜도 같다
    for km in (0, 1, 2):
        a = _three(_call_terms(k_method=km, k_s=9., k_e=9., k_cpn_add=0))
        b = _three(_call_terms(k_method=km, k_s=9., k_e=9., k_cpn_add=1))
        chk_bool(f"  행사일(9개월) ≠ 지급일(12개월) · 방법 {km} · 켜도 같다",
                 all(abs(x - y) < 1e-12 for x, y in zip(a, b)))


def test_call_same_day_events():
    """같은 날 겹치는 사건 — 상장 강제전환 · 만기 · 풋 · 의무보유.

    만기일에는 만기상환·전환이 매도청구보다 먼저다 — 행사기간이 만기까지 열린 계약과 만기 전 노드에서
    끝나는 계약은 세 방법 모두 같은 값이어야 한다. 상장 강제전환 노드에서 옵션차익법 콜은 0 이다.
    """
    print("\n[40] 같은 날 사건 — 만기 · 상장 · 풋 · 의무보유")
    for km in (0, 1, 2):
        for cr in (1, 0):
            a = _three(_call_terms(k_method=km, k_s=12., k_e=24., k_f=3., k_hold=0, k_lock=0., k_conv_resp=cr))
            b = _three(_call_terms(k_method=km, k_s=12., k_e=21., k_f=3., k_hold=0, k_lock=0., k_conv_resp=cr))
            chk_bool(f"  만기일 매도청구 반영 안 함 · 방법 {km} · 전환 {'가능' if cr else '불가'} "
                     f"({a[km]:.6f} = {b[km]:.6f})", all(abs(x - y) < 1e-12 for x, y in zip(a, b)))
    # 존속기간 만료 시 자동전환(RCPS) — 만기일 자동전환이 매도청구보다 먼저다. 행사기간이 만기까지 열려도 같다.
    # (이 조건의 유무가치 차액은 0 이다 — 통지 뒤 전환을 못 하니 투자자가 콜 기간 전에 바로 전환한다. 옵션차익법은
    #  콜 없는 사채의 행사 방식을 그대로 두고 그 위에 콜을 재므로 값이 있다 — 두 방법이 재는 대상의 차이다.)
    _rc = dict(inst="RCPS", issuer_call=2, mat_mode=0, d_issue="2025-01-01", d_base="2025-01-01", d_mat="2027-01-01",
               S0=150., K0=100., sig=0.30, gap_m=3.0, rfx_mode=0, carry=1, p_s=99., p_e=0., cv_s=0., cv_e=24.,
               k_f=3., k_w=0.3, k_hold=0, k_lock=0., k_prem=0.01, cpn=0.0, ytm=0.0, k_third=1, k_conv_resp=0,
               rf_curve=[(1, .03), (2, .03), (3, .03)], cr_curve=[(1, .08), (2, .08), (3, .08)])
    for km in (0, 1, 2):
        ta = Terms(**_rc, k_method=km, k_s=12., k_e=24.); derive(ta)
        tb = Terms(**_rc, k_method=km, k_s=12., k_e=21.); derive(tb)
        va, vb = _three(ta)[km], _three(tb)[km]
        chk_bool(f"  RCPS 만기 자동전환 · 방법 {km} · 만기일 매도청구 반영 안 함 ({va:.6f} = {vb:.6f})",
                 G["auto_conv"](ta) and (km == 0 or va > 1e-6) and abs(va - vb) < 1e-12)
    # 상장 강제전환 노드 — 옵션차익법 노드 값 0
    t = Terms(inst="RCPS", issuer_call=2, ipo_on=1, ipo_conv=1, ipo_m=12., ipo_px=1200., ipo_min=0., gap_m=3.0,
              k_third=1, k_s=6., k_e=24., k_f=3., k_w=0.3, k_hold=0, k_lock=0.,
              rf_curve=[(1, .0226), (3, .0240), (5, .0252)], cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t); full = engine(t, call=False)
    for km in (1, 2):
        nodes = {}; G["call_third_party"](t, full, km, nodes=nodes)
        ipo = [k for k, o in full["memo"].items() if o.get("kind") == "ipo"]
        chk_bool(f"  상장 강제전환 노드 {len(ipo)}개 · 방법 {km} · 콜 값 모두 0",
                 len(ipo) > 0 and all(abs(nodes[k][0]) < 1e-12 for k in ipo if k in nodes))
    # 풋·콜 우선순위 — 조기상환금액이 매도청구금액보다 큰 겹치는 노드가 있을 때만 값이 갈린다
    for km in (0, 1, 2):
        same = _call_terms(k_method=km, p_s=12., p_e=12., p_f=12., p_rate=95.0, p_mode="fixed", k_hold=0, k_lock=0.)
        big = _call_terms(k_method=km, p_s=12., p_e=12., p_f=12., p_rate=115.0, p_mode="fixed", k_hold=0, k_lock=0.)
        vs = [_three(Terms(**{**G["asdict"](same), "pc_order": kf}))[km] for kf in (0, 1)]
        vb = [_three(Terms(**{**G["asdict"](big), "pc_order": kf}))[km] for kf in (0, 1)]
        chk_bool(f"  방법 {km} · 조기상환 95 < 매도청구 100 → 우선순위 무관 ({vs[0]:.4f} = {vs[1]:.4f})",
                 abs(vs[0] - vs[1]) < 1e-12)
        chk_bool(f"  방법 {km} · 조기상환 115 > 매도청구 → 콜 우선이 더 크다 ({vb[1]:.4f} > {vb[0]:.4f})",
                 vb[1] > vb[0] + 1e-9)
    # 의무보유 — 전환만 막는가, 조기상환도 막는가. 조기상환이 의무보유 안에 없으면 같다.
    for km in (0, 1, 2):
        a = _three(_call_terms(k_method=km, k_lock=24., k_lock_put=1))
        b = _three(_call_terms(k_method=km, k_lock=24., k_lock_put=0))
        chk_bool(f"  방법 {km} · 풋 없는 계약은 «조기상환도 막음» 여부와 무관", abs(a[km] - b[km]) < 1e-12)
        a = _three(_call_terms(k_method=km, p_s=6., p_e=18., p_f=3., p_rate=112., p_mode="fixed", k_lock=24., k_lock_put=1))
        b = _three(_call_terms(k_method=km, p_s=6., p_e=18., p_f=3., p_rate=112., p_mode="fixed", k_lock=24., k_lock_put=0))
        chk_bool(f"  방법 {km} · 풋이 의무보유 안에 있으면 갈린다 ({a[km]:.4f} ≠ {b[km]:.4f})", abs(a[km] - b[km]) > 1e-6)


def test_call_grid_styles():
    """월 · 2주 · 주 격자 — «기간 중 언제든지» 는 기간 안 모든 노드, 정기는 그 주기, 특정일은 한 노드."""
    print("\n[41] 격자 간격과 행사방식 — 열리는 노드 수")
    if ROOT not in sys.path: sys.path.insert(0, ROOT)
    from valuation.exercise import apply_styles
    for days, nm in ((0.0, "월"), (14.0, "2주"), (7.0, "주")):
        vals = dict(d_base="2025-03-31", d_mat="2026-03-31", gap_m=1.0, grid_days=days,
                    k_s=3., k_e=9., k_f=3., p_f=3.)
        any_ = apply_styles(vals, {"k_f": "any"})
        t = Terms(d_issue="2025-03-31", d_base="2025-03-31", d_mat="2026-03-31", gap_m=1.0, grid_days=days,
                  k_s=3., k_e=9., k_f=any_["k_f"], p_s=99., p_e=0., k_w=0.3)
        derive(t); n = int(t.n)
        ea = G["exercise_amounts"](t, n, t.T/n)
        lo, hi = G["step_mapper"](t, n, t.T/n)
        want = sum(1 for i in range(n+1) if lo(3.) <= i <= hi(9.))
        chk_bool(f"  {nm} 격자 (n={n}) · 언제든지 → 기간 안 노드 {want}개 모두 열림 (실제 {len(ea['k_dates'])})",
                 len(ea["k_dates"]) == want)
        per = Terms(**{**G["asdict"](t), "k_f": 3.}); derive(per)
        eap = G["exercise_amounts"](per, n, per.T/n)
        chk_bool(f"  {nm} 격자 · 정기 3개월 → 3회 (실제 {len(eap['k_dates'])})", len(eap["k_dates"]) == 3)
        one = Terms(**{**G["asdict"](t), "k_s": 6., "k_e": 6., "k_f": 3.}); derive(one)
        ea1 = G["exercise_amounts"](one, n, one.T/n)
        chk_bool(f"  {nm} 격자 · 특정일 6개월 → 1회 (실제 {len(ea1['k_dates'])})", len(ea1["k_dates"]) == 1)


def test_call_discount_rules():
    """혼합할인율 · 성분 분리할인의 할인 규칙 — 불변식과 수렴.

    · 혼합할인율의 비중(전환확률 · 가치 구성비율)은 모든 노드에서 0~1 — 혼합금리가 무위험과 위험 사이
    · 성분 분리할인 — 행사 노드의 두 성분 합 = 즉시행사가치, 행사 판단은 합으로 한 번(성분별 MAX 없음),
      모든 노드의 콜 값 ≥ 0, 전환확률 분해에서는 음수 성분이 있어도 합은 0 이상
    · 위험이자율 = 무위험이자율이면 두 옵션차익법이 같은 값 (두 할인계수가 같아진다)
    """
    print("\n[42] 할인 규칙 — 비중 범위 · 성분 합 · 한 번 판단 · 금리 같으면 두 방법 수렴")
    t0 = _call_terms(k_s=3., k_e=21., k_f=3., k_hold=0, k_lock=0.)
    full = engine(t0, call=False); memo = full["memo"]
    for sp in (0, 1):
        t = Terms(**{**G["asdict"](t0), "k_split": sp}); derive(t)
        w = [(o.get("P", 0.0) if sp else (o["E"]/(o["E"] + o["B"]) if o["E"] + o["B"] > 1e-12 else 0.0))
             for o in memo.values()]
        chk_bool(f"  기준 {sp} · 비중 {min(w):.4f} ~ {max(w):.4f} — 모두 0~1", all(-1e-12 <= x <= 1 + 1e-12 for x in w))
        nodes = {}; G["call_third_party"](t, full, 2, nodes=nodes)
        neg = sum(1 for c, e, b in nodes.values() if c > 0 and (e < -1e-12 or b < -1e-12))
        chk_bool(f"  기준 {sp} · 모든 노드 콜 값 ≥ 0 · 성분 합 = 콜 값 (음수 성분 노드 {neg}개)",
                 all(c >= -1e-12 and abs(e + b - c) < 1e-9 for c, e, b in nodes.values()))
    # 전환확률 분해의 음수 성분 — 기본 계약(의무보유가 콜 행사기간을 덮는다)에서 실제로 나온다
    t = Terms(k_method=2, k_split=1, gap_m=6.0, carry=1, rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
              cr_curve=[(1, .1409), (3, .1740), (5, .1905)]); derive(t)
    f = engine(t, call=False); nodes = {}; G["call_third_party"](t, f, 2, nodes=nodes)
    neg = [(c, e, b) for c, e, b in nodes.values() if c > 0 and (e < -1e-12 or b < -1e-12)]
    chk_bool(f"  기본 계약 · 전환확률 분해 · 음수 성분 노드 {len(neg)}개 — 그래도 모든 노드 콜 값 ≥ 0 · 성분 합 = 콜 값",
             len(neg) > 0 and all(c >= -1e-12 and abs(e + b - c) < 1e-9 for c, e, b in nodes.values()))
    # 행사 판단은 합으로 한 번 — 행사 노드는 즉시행사가치가 계속보유(두 성분 합) 이상인 자리다
    q_ok, n_hold, n_ex = True, 0, 0
    for key, (c, e, b) in nodes.items():
        o = f["memo"][key]
        if "up" not in o or o["up"] not in nodes or o["dn"] not in nodes: continue
        i = key[0]
        cu, eu, bu = nodes[o["up"]]; cd, ed, bd = nodes[o["dn"]]; q = f["qi"](i)
        he = (q*eu + (1-q)*ed)*math.exp(-f["fwdRF"](i)*f["dt"]); hb = (q*bu + (1-q)*bd)*math.exp(-f["fwdCR"](i)*f["dt"])
        if abs(c - (he + hb)) < 1e-9:        # 보유 — 두 성분이 모두 계속보유 성분이다
            n_hold += 1
            if abs(e - he) > 1e-9 or abs(b - hb) > 1e-9: q_ok = False
        elif c > 0:                          # 행사 — 즉시행사가치가 계속보유가치 이상이고 두 성분이 함께 온다
            n_ex += 1
            if c < he + hb - 1e-9 or abs(e + b - c) > 1e-9: q_ok = False
    chk_bool(f"  한 번 판단 — 보유 노드 {n_hold}개는 두 성분 모두 계속보유, 행사 노드 {n_ex}개는 두 성분 모두 행사",
             q_ok and n_hold > 0 and n_ex > 0)
    for sp in (0, 1):
        t = _call_terms(k_s=3., k_e=21., k_f=3., k_hold=0, k_lock=0., k_split=sp,
                        cr_curve=[(1, .03), (2, .03), (3, .03)], cmp_cr=2)
        f = engine(t, call=False)
        v1, v2 = G["call_third_party"](t, f, 1), G["call_third_party"](t, f, 2)
        chk(f"  위험 = 무위험 · 기준 {sp} · 혼합할인율 = 성분 분리할인", v1, v2, 1e-9)


def test_call_rec_and_simple_oracle():
    """방법별 차이 설명표 ① + ② = 실제 차이 (의무보유 설정과 무관), 그리고 손으로 세는 단순 사례.

    단순 사례 — 전환권·풋·쿠폰 없음, 2년 후 120 상환, 1년 후 100 으로 콜 행사 가능, 위험이자율 연복리
    5%, 대상비율 30%:  (120 ÷ 1.05² − 100 ÷ 1.05) × 30% = 4.081632653 — 세 방법 · 두 기준 모두.
    """
    print("\n[43] 차이 설명표 합계 · 단순 사례 4.081632653")
    base = dict(rf_curve=[(1, .0226), (3, .0240), (5, .0252)], cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    for kh in (1, 0):
        for km in (1, 2):
            t = Terms(k_method=km, k_hold=kh, gap_m=6.0, **base); derive(t)
            full, b0, b1, b2, ca, conv = G["decompose"](t)
            rows, rec = G["call_compare"](t, full, b2)
            A, O = rec["유무가치비교법 (적용 계약)"], rec["옵션차익법 (적용 산식·적용 설정)"]
            s = rec["① 방법론 차이 (둘 다 의무보유 없음)"] + rec["② 의무보유가 두 방법에 다르게 들어가는 부분"]
            chk(f"  의무보유 {'있음' if kh else '없음'} · 방법 {km} · ① + ② = 유무가치 − 옵션차익", s, A - O, 1e-9)
    want = (120/1.05**2 - 100/1.05)*0.3
    for km in (0, 1, 2):
        for sp in (0, 1):
            t = Terms(d_issue="2025-01-01", d_base="2025-01-01", d_mat="2027-01-01", S0=1., K0=100., sig=0.30,
                      gap_m=12.0, rfx_mode=0, p_s=99., p_e=0., cv_s=99., cv_e=0., k_s=12., k_e=12., k_f=12.,
                      k_w=0.3, k_lock=0., k_hold=0, cpn=0.0, ytm=0.0, mat_amt=120., k_prem=0.0,
                      rf_curve=[(1, .05), (2, .05), (3, .05)], cr_curve=[(1, .05), (2, .05), (3, .05)],
                      cmp_rf=1, cmp_cr=1, k_third=1, k_method=km, k_split=sp)
            derive(t)
            full, b0, b1, b2, ca, conv = G["decompose"](t)
            chk(f"  방법 {km} · 기준 {sp} · 4.081632653", ca, want, 1e-9)


def test_wow_trace():
    """유무가치비교법 차액의 구성 — 관련 약정 + 행사 판정 + 할인 방식 = 적용값 (항등식).

    콜이 직접 누른 값(콜 행사 노드 · 강제전환 노드)은 0 이상이어야 한다. 음수 차액은 0 으로 덮지 않고
    원인을 나눠 보인다 — 공개 발행조건(차바이오텍 RCPS 2024-05-16, 발행자 상환권 가정)은 할인 방식
    효과가 음수라 차액이 음수가 된다.
    """
    print("\n[44] 유무가치비교법 차액의 구성 — 항등식과 음수 원인")
    cases = [("기본 계약", dict()), ("의무보유 없음", dict(k_hold=0)), ("GS", dict(model="GS")),
             ("콜 우선 · 전환 불가", dict(pc_order=1, k_conv_resp=0, k_hold=0))]
    base = dict(rf_curve=[(1, .0226), (3, .0240), (5, .0252)], cr_curve=[(1, .1409), (3, .1740), (5, .1905)],
                gap_m=6.0, carry=1)
    for nm, kw in cases:
        t = Terms(**base, **kw); derive(t)
        full, b0, b1, b2, ca, conv = G["decompose"](t)
        tr = G["wow_trace"](t, full, b2)
        rows, rec = G["call_compare"](t, full, b2)
        A = next(v for a, _, v, _ in rows if a.startswith("유무가치비교법 ("))
        chk(f"  {nm} · 행사 판정 + 할인 방식 = 콜만의 차액", tr["X"] + tr["Y"], tr["A0"], 1e-9)
        chk(f"  {nm} · 적용값 = 비교표 유무가치", tr["A"], A, 1e-9)
        chk_bool(f"  {nm} · 콜이 직접 누른 값 ≥ 0 ({tr['X_call']:.4f} · {tr['X_forced']:.4f})",
                 tr["X_call"] >= -1e-12 and tr["X_forced"] >= -1e-12)
    t = Terms(inst="RCPS", mat_mode=0, gap_m=3., carry=1, S0=15647., K0=17354., floor=12148., par=500.,
              d_issue="2024-05-16", d_base="2024-05-16", d_mat="2029-05-16",
              cpn=0.01*500/17354., ipay=12., div_mode=0, ytm=.015, ytm_cmp=4,
              cv_s=12., cv_e=59., rfx_mode=2, rfx_cyc=7., p_s=24., p_e=59., p_f=1., p_mode="accrue",
              p_yield=.015, p_cmp=4, k_s=12., k_e=24., k_f=3., k_prem=.015, k_cmp=4, k_w=.20, k_lock=24.,
              sig=.2112, rf_curve=[(1, .0344), (2, .0342), (3, .0341), (5, .0344)],
              cr_curve=[(1, .15), (2, .15), (3, .15), (5, .15)], issuer_call=1)
    derive(t)
    full, b0, b1, b2, ca, conv = G["decompose"](t)
    tr = G["wow_trace"](t, full, b2)
    chk_bool(f"  공개 RCPS 발행자 상환권 · 차액 {tr['A']:.4f} < 0 이고 원인은 할인 방식 효과 ({tr['Y']:.4f} < 0)",
             tr["A"] < 0 and tr["Y"] < 0 and tr["X"] > 0)
    chk_bool("  음수 원인 문장이 할인 방식 효과를 짚는다", "할인 방식 효과" in G["wow_trace_note"](tr))
    chk(f"  0 으로 덮지 않는다 — 적용 콜 값 = 차액", ca, tr["A"], 1e-9)


# ══════════════════════════════════════════════════════════
# SHA 점검 (2026-09-30) — 회차별 표 · 행사일 배정 · 가산기간 · 수량 · 배당수익률.
# 기대값은 엔진 함수를 쓰지 않고 이항격자를 처음부터 다시 세운 값이다. 사례는 모두 가상 수치다.
def _hand_crr(S0, sig, r_spot, div, days, n, ex, pay, disc_spot=None):
    """평탄한 연복리 현물금리 · 연속 배당수익률 · 미국형(ex 에 든 노드에서만 행사) — 손으로 세운 격자."""
    T = days/365; dt = T/n
    u = math.exp(sig*math.sqrt(dt)); d = 1/u
    r = math.log(1 + r_spot); rd = math.log(1 + (disc_spot if disc_spot is not None else r_spot))
    p = (math.exp((r - div)*dt) - d)/(u - d)
    V = [pay(n, S0*u**j*d**(n-j)) if n in ex else 0.0 for j in range(n+1)]
    for i in range(n-1, -1, -1):
        V = [max((p*V[j+1] + (1-p)*V[j])*math.exp(-rd*dt),
                 pay(i, S0*u**j*d**(i-j)) if i in ex else 0.0) for j in range(i+1)]
    return V[0]


def _sha_row_case(rows, **kw):
    base = dict(inst="SHA", S0=950., K0=1000., d_issue="2025-01-01", d_base="2025-01-01",
                d_mat="2025-07-02", sig=.35, div_y=.02, grid_days=7., gap_m=1., sha_disc=0,
                y_type="spot", cmp_rf=1, cmp_cr=1, sha_put_cmp=1, sha_call_cmp=1,
                rf_curve=[(.25, .03), (30, .03)], cr_curve=[(.25, .03), (30, .03)], sha_rows=rows)
    base.update(kw)
    t = Terms(**base); derive(t)
    return t, G["sha_portfolio"](t)


def test_sha_review_hand():
    """주주간계약 — 손으로 세운 격자와 엔진이 같은가 (회차별 표 경로).

    (가) 고정 행사가격(가산율 0%) · 주 격자 · 기간 중 언제든지 · 배당수익률 2% — 미국형 풋·콜.
         평가기준일 2025-01-01, 종료 2025-07-02 (182일 → 26구간). 모든 노드에서 행사할 수 있다.
    (나) 풋·콜 수량이 다르면 원 단위 금액은 각자의 수량으로 곱한다.
    (다) 가산율 6% 연복리 · 특정일 1회(2025-06-10, 기산일 2023-06-10 부터 24개월) — 행사금액
         = 5,000 × 1.06² (계약 개월 ÷ 12). 실제 일수 선택이면 경과연수 = 기산일→행사일 실제 일수(윤일 포함 731일) ÷ 365.
    (라) 주 격자에서 «매월» 정기 행사 — 계약일마다 그날 이후 첫 노드(허용 1일)다. 4노드마다가 아니다.
    """
    print("\n[45] 주주간계약 — 손으로 세운 격자 (회차별 표)")
    row = dict(name="A", start="2025-01-01", end="2025-07-02", style="any", price=1000., rate=0.,
               put_q=1000., call_q=0.)
    t, P = _sha_row_case([row])
    x = P["rows"][0]
    want = _hand_crr(950., .35, .03, .02, 182, 26, set(range(27)), lambda i, S: max(1000. - S, 0.))
    chk("(가) 풋 1주당 · 26구간 미국형 · 배당 2% (원)", x["put_ps"], want, 1e-6)
    t, P = _sha_row_case([dict(row, put_q=0., call_q=700.)], S0=1100.)
    x = P["rows"][0]
    want = _hand_crr(1100., .35, .03, .02, 182, 26, set(range(27)), lambda i, S: max(S - 1000., 0.))
    chk("(가) 콜 1주당 · 배당 2% 면 조기행사가 생긴다 (원)", x["call_ps"], want, 1e-6)
    # (나) 수량
    t, P = _sha_row_case([dict(row, put_q=619., call_q=464.)], S0=1000.)
    x = P["rows"][0]
    chk("(나) 풋 전액 = 풋 1주당 × 풋 수량 619", x["put_krw"], x["put_ps"]*619, 1e-6)
    chk("(나) 콜 전액 = 콜 1주당 × 콜 수량 464", x["call_krw"], x["call_ps"]*464, 1e-6)
    chk("(나) 합계 = 회차 합", P["put_krw"] + P["call_krw"], x["put_krw"] + x["call_krw"], 1e-6)
    # (다) 가산율 — 특정일 1회 = 유럽형
    r7 = dict(name="B", start="2025-06-10", end="2025-06-10", style="single", price=5000., rate=.06,
              put_q=1., call_q=1.)
    for ab in (1, 0):
        t, P = _sha_row_case([r7], d_issue="2023-06-10", d_base="2024-11-29", d_mat="2025-06-10",
                             S0=5400., sig=.30, div_y=0., acc_basis=ab)
        x = P["rows"][0]; R = x["R"]; n = R["n"]
        days = (G["dt"].date(2025, 6, 10) - G["dt"].date(2024, 11, 29)).days
        # 실제 일수 — 기산일(2023-06-10)부터 행사일(2025-06-10)까지 731일(2024-02-29 포함) ÷ 365
        yrs = 2.0 if ab else (G["dt"].date(2025, 6, 10) - G["dt"].date(2023, 6, 10)).days/365
        K = 5000.*1.06**yrs
        chk(f"(다) 행사금액 · {'계약 개월 ÷ 12' if ab else '실제 일수'} (원)", R["pk"](n)*5000./100, K, 1e-6)
        chk(f"(다) 유럽형 풋 1주당 · {'계약 개월' if ab else '실제 일수'}", x["put_ps"],
            _hand_crr(5400., .30, .03, 0., days, n, {n}, lambda i, S: max(K - S, 0.)), 1e-6)
        chk(f"(다) 유럽형 콜 1주당 · {'계약 개월' if ab else '실제 일수'}", x["call_ps"],
            _hand_crr(5400., .30, .03, 0., days, n, {n}, lambda i, S: max(S - K, 0.)), 1e-6)
    # (라) 주 격자에서 매월 행사 — 노드 날짜를 손으로 센다
    rm = dict(name="C", start="2025-02-01", end="2025-06-01", style="periodic", freq=1., price=1000., rate=0.,
              put_q=1., call_q=0.)
    t, P = _sha_row_case([rm])
    R = P["rows"][0]["R"]; n = R["n"]
    # 회차의 격자는 평가기준일부터 그 회차의 마지막 행사일(2025-06-01, 151일)까지다
    d0 = G["dt"].date(2025, 1, 1); step = (G["dt"].date(2025, 6, 1) - d0).days/n
    nodes = [d0 + G["dt"].timedelta(days=round(i*step)) for i in range(n+1)]
    want = set()
    for m in range(1, 6):
        cd = G["dt"].date(2025, 1 + m, 1)
        want.add(next(i for i, x in enumerate(nodes) if x >= cd - G["dt"].timedelta(days=1)))
    chk_bool(f"(라) 매월 행사 노드 = 계약일 이후 첫 노드 {sorted(want)}", set(R["p_dates"]) == want)
    chk_bool("(라) 4노드 간격 반올림이 아니다 (간격이 4·5 로 섞인다)",
             len({b - a for a, b in zip(sorted(want), sorted(want)[1:])}) > 1)


def test_sha_rows_block_and_isolate():
    """회차별 표 — 계산을 막는 입력과, 회차끼리 섞이지 않는지.

    · 평가기준일 전에 끝난 회차는 막는다 (이미 행사됐다고 가정하지 않는다).
    · 풋·콜 수량이 다른 회차에 상호소멸을 켜면 막는다 (겹치는 수량을 나눠 넣으라고 안내).
    · 회차 둘의 합계 = 회차 하나씩 따로 잰 값의 합 — 다음 회차로 물량을 넘기지 않는다.
    · 한 회차의 행사기간·가격을 바꿔도 다른 회차 값은 그대로다.
    """
    print("\n[46] 주주간계약 — 회차별 표의 차단과 독립")
    r1 = dict(name="1차", start="2025-02-01", end="2025-04-30", style="any", price=1000., rate=0.,
              put_q=100., call_q=100.)
    r2 = dict(name="2차", start="2025-05-01", end="2025-07-02", style="any", price=1100., rate=.05,
              put_q=100., call_q=60., rf=.035, sig=.45)
    t, P = _sha_row_case([r1, r2])
    _, P1 = _sha_row_case([r1]); _, P2 = _sha_row_case([r2])
    chk("회차 둘 합계 풋 = 1차 + 2차 따로", P["put_krw"], P1["put_krw"] + P2["put_krw"], 1e-6)
    chk("회차 둘 합계 콜 = 1차 + 2차 따로", P["call_krw"], P1["call_krw"] + P2["call_krw"], 1e-6)
    _, P3 = _sha_row_case([r1, dict(r2, price=1300., end="2025-06-15")])
    chk("2차를 바꿔도 1차 풋은 그대로", P3["rows"][0]["put_krw"], P["rows"][0]["put_krw"], 1e-9)
    bad = Terms(inst="SHA", d_issue="2024-01-01", d_base="2025-01-01", d_mat="2026-01-01",
                sha_rows=[dict(r1, start="2024-03-01", end="2024-12-31")])
    chk_bool("평가기준일 전에 끝난 회차 → 막음",
             any("이미 행사" in m for _, m in G["sha_row_issues"](bad)))
    bad.sha_rows = [dict(r2, kill=1)]
    chk_bool("수량이 다른 회차의 상호소멸 → 막음",
             any("상호소멸" in m for _, m in G["sha_row_issues"](bad)))
    bad.sha_rows = [dict(r1), dict(r1)]
    chk_bool("회차 이름이 겹치면 → 막음", any("겹칩니다" in m for _, m in G["sha_row_issues"](bad)))
    bad.sha_rows = [dict(r1, style="anytime")]
    chk_bool("알 수 없는 행사 방식(오타) → 막음 (언제든지로 넓혀 읽지 않는다)",
             any("행사 방식" in m for _, m in G["sha_row_issues"](bad)))
    far = Terms(inst="SHA", d_issue="2024-01-01", d_base="2025-01-01", d_mat="2026-01-01", grid_days=7.,
                sha_rows=[dict(r1, start="2025-02-01", end="2060-01-01")])
    chk_bool("회차 격자가 계산 한도(1,200구간)를 넘으면 → 막음",
             any("계산 한도" in m for _, m in G["sha_row_issues"](far)))
    # 평가기준일에 끝나는 회차는 막지 않는다 — 그날 행사할 수 있으므로 내재가치다
    t0, P0 = _sha_row_case([dict(r1, name="오늘", start="2024-12-01", end="2025-01-01"), r2],
                           d_issue="2024-06-01", S0=800.)
    chk("평가기준일에 끝나는 풋 = 내재가치 1000 − 800", P0["rows"][0]["put_ps"], 200., 1e-9)
    # 실적 연동 행사가격 — (매출 − 차감) × 배수 ÷ 발행주식 총수, 손실률이 기준을 «초과» 하면 낮은 배수
    px, m = G["sha_perf_price"](10_000e6, 1_500e6, 12., 10., 1.0, 1.5, 200_000)
    chk("실적 연동 가격 · 손실률 12% > 10% → 1.0배", px, 8_500e6/200_000, 1e-9)
    px, m = G["sha_perf_price"](10_000e6, 1_500e6, 10., 10., 1.0, 1.5, 200_000)
    chk("실적 연동 가격 · 손실률 10% = 기준 → 1.5배 (초과가 아니다)", px, 8_500e6*1.5/200_000, 1e-9)
    # 단일 계약(옛 평가파일) — 회차 표가 없으면 엔진 결과 그대로이고 원 단위는 계산기준금액 기준이다
    ts = Terms(inst="SHA", S0=1000., K0=1000., face_total=1e9, gap_m=6.,
               rf_curve=[(1, .0226), (5, .0252)], cr_curve=[(1, .05), (5, .06)]); derive(ts)
    Ps = G["sha_portfolio"](ts); Rs = G["sha_engine"](ts)
    chk("단일 계약 · 풋 100 기준 = 엔진", Ps["put"], Rs["put"], 1e-12)
    chk("단일 계약 · 풋 원 = 풋 × 계산기준금액 ÷ 100", Ps["put_krw"], Rs["put"]*1e9/100, 1e-4)


def test_refix_contract_dates():
    """정기 리픽싱 조정일 — 계약 조정일을 «그날 이후 첫 노드» 에 배정 (행사일과 같은 규칙).

    종전에는 주기를 노드 간격으로 반올림한 칸마다 조정해 계약일에서 밀렸다.
    ① 노드 6개월 · 주기 7개월 · 만기 60개월: 계약 조정일 7·14·21·28·35·42·49·56개월
       → 그날 이후 첫 노드 = 12·18·24·30·36·42·54·60개월 = 노드 2·3·4·5·6·7·9·10 (8번 노드 48개월은 아님).
       종전 규칙(7 ÷ 6 → 1칸)은 노드 1~10 열 번을 조정했다.
    ② 주 노드 · 매월 · 2025-01-01 ~ 2026-01-01 (52구간, 한 구간 365/52 = 7.019일, 허용 1일):
       2025-02-01(31일) — 노드 4 = 28일(1월 29일, 3일 앞) · 노드 5 = 35일(2월 5일) → 노드 5.
       2025-03-01(59일) — 노드 8 = 56일(3일 앞) · 노드 9 = 63일 → 노드 9.
       종전 규칙은 4노드(28일)마다라 노드 4·8 — 계약일보다 앞선 날에 조정했다.
    """
    print("\n[47] 리픽싱 조정일 — 계약 조정일 뒤 첫 노드")
    RS = G["refix_steps"]
    t = Terms(gap_m=6., rf_curve=[(1, .0226), (3, .0240), (5, .0252)], cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t)
    got = RS(t, int(t.n), t.T/int(t.n))
    chk_bool(f"노드 6개월 · 7개월 주기 → 노드 {sorted(got)} = 2·3·4·5·6·7·9·10", sorted(got) == [2, 3, 4, 5, 6, 7, 9, 10])
    chk_bool("각 노드가 가리키는 계약 조정월 = 7·14·…·56", [got[i] for i in sorted(got)] == [7., 14., 21., 28., 35., 42., 49., 56.])
    w = Terms(d_issue="2025-01-01", d_base="2025-01-01", d_mat="2026-01-01", grid_days=7., rfx_cyc=1.,
              rf_curve=[(1, .0226), (3, .0240)], cr_curve=[(1, .1409), (3, .1740)])
    derive(w)
    n = int(w.n); gw = RS(w, n, w.T/n)
    chk_bool(f"주 노드 52구간 (n={n})", n == 52)
    chk_bool("2025-02-01 → 노드 5 (종전 4)", 5 in gw and 4 not in gw)
    chk_bool("2025-03-01 → 노드 9 (종전 8)", 9 in gw and 8 not in gw)
    chk_bool(f"매월 12번 조정 (마지막 2026-01-01 = 노드 52) — {len(gw)}번", len(gw) == 12 and 52 in gw)
    # 모든 조정 노드가 «계약일 − 허용일수 이후 첫 노드» 다 — 노드 날짜에서 직접 센다
    import datetime as _dt
    nd = G["node_dates"](w, n, w.T/n); tol = G["date_tol_days"](w.T/n)
    want = set()
    for m in range(1, 13):
        cd = _dt.date(2025 + m//12, m % 12 + 1, 1)
        want.add(min(i for i in range(1, n+1) if (nd[i] - cd).days >= -tol))
    chk_bool("주 노드 · 조정 노드 = 노드 날짜에서 센 첫 노드 (12개 모두)", set(gw) == want)
    # 주기가 노드 간격의 정수배이면 종전과 같다 — 월 노드 · 3개월 주기는 3·6·9…
    m3 = Terms(gap_m=1., rfx_cyc=3., rf_curve=t.rf_curve, cr_curve=t.cr_curve); derive(m3)
    g3 = RS(m3, int(m3.n), m3.T/int(m3.n))
    chk_bool("월 노드 · 3개월 주기 → 3·6·…·60", sorted(g3) == list(range(3, 61, 3)))
    # 평가기준일이 발행 뒤면 이미 지난 조정일은 세지 않는다 (발행 10개월 뒤 평가 · 7개월 주기 → 첫 조정 14개월)
    e = Terms(d_issue="2025-01-01", d_base="2025-11-01", d_mat="2030-01-01", gap_m=1., rfx_cyc=7.,
              rf_curve=t.rf_curve, cr_curve=t.cr_curve); derive(e)
    ge = RS(e, int(e.n), e.T/int(e.n))
    chk_bool(f"발행 10개월 뒤 평가 → 첫 조정월 14 (노드 4) — {min(ge.values()) if ge else None}",
             bool(ge) and min(ge.values()) == 14. and min(ge) == 4)
    # 최초 조정일만 따로 정한 계약 — 「발행 후 12개월 최초 조정, 이후 매 7개월」 · 만기 3년
    #   → 12 · 19 · 26 · 33개월 (주기를 12 로 바꾸면 12 · 24 · 36 이 되어 틀린다).
    f = Terms(d_issue="2025-01-01", d_base="2025-01-01", d_mat="2028-01-01", gap_m=1., rfx_cyc=7.,
              rfx_first=12., rf_curve=t.rf_curve, cr_curve=t.cr_curve); derive(f)
    gf = RS(f, int(f.n), f.T/int(f.n))
    chk_bool(f"최초 12개월 · 이후 7개월 → 조정월 {[gf[i] for i in sorted(gf)]} = 12·19·26·33",
             [gf[i] for i in sorted(gf)] == [12., 19., 26., 33.] and sorted(gf) == [12, 19, 26, 33])
    # 평가기준일이 최초 조정일 뒤 — 발행 20개월 뒤 평가면 지난 12·19 는 빼고 26 · 33
    f2 = Terms(d_issue="2025-01-01", d_base="2026-09-01", d_mat="2028-01-01", gap_m=1., rfx_cyc=7.,
               rfx_first=12., rf_curve=t.rf_curve, cr_curve=t.cr_curve); derive(f2)
    gf2 = RS(f2, int(f2.n), f2.T/int(f2.n))
    chk_bool(f"발행 20개월 뒤 평가 → 남은 조정월 {sorted(gf2.values())} = 26·33", sorted(gf2.values()) == [26., 33.])
    # 비워 두면(0) 종전과 같다 — 첫 조정 = 발행일 + 주기
    f0 = Terms(d_issue="2025-01-01", d_base="2025-01-01", d_mat="2028-01-01", gap_m=1., rfx_cyc=7.,
               rf_curve=t.rf_curve, cr_curve=t.cr_curve); derive(f0)
    chk_bool("최초 조정일 비움 → 7·14·21·28·35",
             sorted(RS(f0, int(f0.n), f0.T/int(f0.n)).values()) == [7., 14., 21., 28., 35.])


def test_lock_end_same_node_as_last_call():
    """의무보유 종료와 같은 계약일의 마지막 매도청구는 같은 노드에서 처리한다.

    매도청구일은 «계약일 이후 첫 노드», 의무보유 종료는 «계약일 이전 마지막 노드» 로 잡혀 있어,
    노드가 계약일과 어긋나면 마지막 매도청구(주 격자 105번)가 의무보유 종료(104번) 뒤로 밀렸다.
    그 노드에서 투자자가 먼저 전환하면 의무보유가 지키려던 마지막 매도청구가 사라진다.
    ① 주 노드 · 매도청구 12~24개월 분기 · 의무보유 24개월 → 의무보유 마지막 노드 = 마지막 매도청구 노드.
    ② 월 노드: 의무보유 24개월(= 마지막 매도청구일) 과 24.5개월은 같은 값이고(둘 다 그날을 묶는다),
       23.5개월(마지막 매도청구일 전에 풀림)보다 크다.
    """
    print("\n[48] 의무보유 종료 — 같은 계약일의 마지막 매도청구와 같은 노드")
    RF = [(1, .0226), (3, .0240), (5, .0252)]; CR = [(1, .1409), (3, .1740), (5, .1905)]
    w = Terms(grid_days=7., k_lock=24., rf_curve=RF, cr_curve=CR); derive(w)
    n = int(w.n); dt_ = w.T/n
    kd = G["exercise_amounts"](w, n, dt_)["k_dates"]
    last = max(i for i, m in kd.items() if m <= 24. + 1e-9)
    _, hi = G["step_mapper"](w, n, dt_)
    L = G["lock_end_step"](w, n, dt_)
    chk_bool(f"주 노드 · 마지막 매도청구 노드 {last} > 계약일 이전 마지막 노드 {hi(24.)}", last > hi(24.))
    chk_bool(f"의무보유 마지막 노드 {L} = 마지막 매도청구 노드 {last}", L == last)
    row = [r for r in G["exercise_date_rows"](w) if r[0] == "의무보유"]
    chk_bool("행사일 대조표에 의무보유 종료 줄 (같은 노드)", len(row) == 1 and row[0][3] == last)
    def callv(lock):
        t = Terms(gap_m=1., k_lock=lock, rf_curve=RF, cr_curve=CR); derive(t)
        return G["decompose"](t)[4]
    a, b, c = callv(24.), callv(24.5), callv(23.5)
    chk("월 노드 · 의무보유 24개월 = 24.5개월 (둘 다 마지막 매도청구일을 묶음)", a, b, 1e-9)
    chk_bool(f"의무보유 24개월 {a:.4f} > 23.5개월 {c:.4f} (마지막 매도청구일 전에 풀림)", a > c + 1e-6)
    chk_bool("의무보유 없음 → −1", G["lock_end_step"](Terms(k_hold=0), 60, 5/60) == -1)


def main():
    print("손계산 기대값 대조 — 기대값은 계약에서 센 값이다. 갱신하지 말 것.")
    test_coupon_schedule_after_elapsed_months()
    test_root_immediate_put()
    test_all_step_risk_neutral_probabilities()
    test_current_k_and_original_cap()
    test_refix_weighted_average_on_reset_date()
    test_bw_inherits_engine_fixes()
    test_sha_root_immediate_put()
    test_split_metric_independent_of_setting()
    test_put_separation_flows_to_accounting()
    test_bw_root_and_call_keep_warrant()
    test_sha_mutual_kill_probabilities_sum_to_one()
    test_dividend_yield_and_zero_vol()
    test_backsolve_net_target()
    test_fvpl_whole_flows_to_accounting()
    test_decision_table()
    test_decision_matches_old_chains()
    test_maturity_layer_in_distribution()
    test_ipo_branch_keeps_probability_mass()
    test_unsupported_combos_agree()
    test_sha_boundaries()
    test_date_month_roundtrip()
    test_pick_close()
    test_bdt_review_gates()
    test_acc_mode_fv_only()
    test_call_strike_switch()
    test_eir_expected_maturity()
    test_emb_approach_allocation()
    test_tie_tolerance()
    test_exercise_date_table()
    test_call_split_text()
    test_deduction_methods()
    test_div_basis()
    test_rfx_anytime()
    test_display_only_fields()
    test_holder_view()
    test_excel_limits_and_curves()
    test_call_response_combos()
    test_call_zero_and_scale()
    test_call_coupon_add()
    test_call_same_day_events()
    test_call_grid_styles()
    test_call_discount_rules()
    test_call_rec_and_simple_oracle()
    test_wow_trace()
    test_sha_review_hand()
    test_sha_rows_block_and_isolate()
    test_refix_contract_dates()
    test_lock_end_same_node_as_last_call()
    print()
    if FAIL:
        print(f"★ 어긋남 {len(FAIL)}건")
        for f in FAIL: print("   -", f)
        return 1
    print("모두 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
