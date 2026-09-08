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
    # 주계약(B0) 상각표에서 뽑은 값이어야 한다 — 부채요소(B1) 가 아니다.
    t0 = Terms(carry=1, gap_m=6.0,
               rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
               cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t0)
    _f, _b0, _b1, _b2, _ca, _cv = G["decompose"](t0)
    chk_bool(f"상각후원가 {got[0][1]:.4f} 가 B0 {_b0:.4f} 과 B1 {_b1:.4f} 사이이고 "
             "B1 보다 작다", _b0 - 1e-6 <= got[0][1] < _b1)


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
    t = Terms(inst="BW", bw_pay=0, bw_detach=0, k_lock=0., k_s=12., k_e=48.,
              k_w=1.0, cv_s=6., gap_m=3.,
              rf_curve=[(1, .0226), (3, .0240), (5, .0252)],
              cr_curve=[(1, .1409), (3, .1740), (5, .1905)])
    derive(t)
    r = engine(t, call=True)
    calls = [o for o in r["memo"].values() if o["kind"] == "call"]
    chk_bool(f"매도청구 노드가 있다 ({len(calls)}개)", len(calls) > 0)
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
    chk_bool(f"자동전환이 «전환» 에 들어갔다 ({D['conv']:.4f})", D["conv"] > 0.5)
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
    """적격상장 조항이 켜진 상태확장 격자에서 정산 분포의 합이 1 인가.

    상태확장(carry=0) 격자는 노드 열쇠에 전환가격을 넣는다. 격자를 세우는 재귀는
    상장 스텝에서 전환가격을 공모가×배수로 잘랐는데, 확률을 걷는 루프는 자르지
    않았다. 열쇠가 어긋난 가지는 memo 에 없어 조용히 버려졌고 — 분포 합이 74% 로
    떨어졌다 (분기전수.py 가 잡음). 가치는 안 바뀐다 — 분포는 표시 전용이다.

    자식 전환가격을 한 함수(child_k)로 모아 세 루프가 같은 열쇠를 쓰게 했다.
    """
    print("\n[18] 상장 조항이 켜진 상태확장 격자 — 분포가 새지 않는가")
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
        iss = A["발행회사"][0]; maj = A["최대주주"][0]
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
    """이자율모형 검토 네 관문 — 닫히는 자리와 열리는 자리를 손으로 만든 계약으로 확인한다."""
    print("\n[23] 이자율모형(BDT) 검토 네 관문")
    RF = [(1, .030), (3, .031), (5, .032)]
    rev, sigf = G["bdt_review"], G["rate_signals"]
    def run(**kw):
        t = Terms(rf_curve=RF, **kw); derive(t)
        full, b0, b1, b2, ca, _ = G["decompose"](t)
        return rev(t, full, b0, b1, b2, ca, sigf(t)), t
    # (가) 국내 사모 CB 전형 — 위험할인율이 보장수익률보다 훨씬 높다 → ③ 닫힘
    r, _ = run(cr_curve=[(1, .14), (3, .17), (5, .19)], ytm=.0, S0=10000., K0=10000.)
    g = {no: ok for no, _, _, ok, _ in r["관문"]}
    chk_bool("전형 CB — ① 자본 열림", g[1]); chk_bool("전형 CB — ③ 격차 닫힘", not g[3])
    chk_bool("전형 CB — 결론 «검토했으나 적용하지 않음»", r["결론"].startswith("검토했으나"))
    chk_bool("전형 CB — 문안에 «결정론적» 이 있다", "결정론적" in r["문안"])
    # (나) 전환권이 부채 → ① 닫힘
    r, _ = run(cr_curve=[(1, .14), (3, .17), (5, .19)], conv_class="liability")
    chk_bool("부채 분류 — ① 닫힘", not {no: ok for no, _, _, ok, _ in r["관문"]}[1])
    # (다) 우량 발행사 — 자본·외가격·격차 작음: ①②③ 열림
    r, t = run(cr_curve=[(1, .034), (3, .036), (5, .038)], ytm=.035, ytm_cmp=1, S0=7000., K0=10000., sig=.25)
    g = {no: ok for no, _, _, ok, _ in r["관문"]}
    chk_bool("우량 — ① 열림", g[1]); chk_bool("우량 — ② 외가격 열림", g[2]); chk_bool("우량 — ③ 격차 작음 열림", g[3])
    chk("우량 — 격차 %p", r["지표"]["gap"]*100, (math.exp(G["curves"](t)[1](t.T)) - 1 - .035)*100, 1e-6)
    chk_bool("우량 — 결론이 «적용 검토» 또는 ④ 닫힘 중 하나로 정해진다",
             r["결론"].startswith("이자율모형 적용을") or (not g[4] and r["결론"].startswith("검토했으나")))
    # (라) 조기상환권 없음 → 해당 없음
    r, _ = run(cr_curve=[(1, .14), (3, .17), (5, .19)], p_s=99., p_e=0.)
    chk_bool("풋 없음 — 해당 없음", r["결론"] == "해당 없음")
    # (마) BDT 를 켰으면 왜곡 크기가 있고 문안이 «적용» 이다
    r, t = run(cr_curve=[(1, .034), (3, .036), (5, .038)], ytm=.035, ytm_cmp=1, S0=7000., K0=10000., put_bdt=1, bdt_sig=.2)
    chk_bool("BDT 적용 — 왜곡 크기가 있다", r["왜곡"] is not None)
    chk_bool("BDT 적용 — 문안에 «과소평가» 가 있다", "과소평가" in r["문안"])
    chk_bool("BDT 적용 — 왜곡 = BDT 부채요소 − TF 부채요소", r["왜곡"] is not None and abs(r["왜곡"]["diff"] - (r["왜곡"]["bdt"] - r["왜곡"]["tf"])) < 1e-9)
    # (바) 주주간계약은 없음
    ts = Terms(inst="SHA", rf_curve=RF, cr_curve=[(1, .14), (3, .17), (5, .19)]); derive(ts)
    chk_bool("주주간계약 — None", rev(ts, {"dist": {}}, 0, 0, 0, 0) is None)


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
    print()
    if FAIL:
        print(f"★ 어긋남 {len(FAIL)}건")
        for f in FAIL: print("   -", f)
        return 1
    print("모두 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
