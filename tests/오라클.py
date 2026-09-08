#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""독립 오라클 — 엔진을 부르지 않는 작은 검산 (모형 검증 프로그램 3단계).

다른 시험은 「엔진 = 조서」를 본다. 둘이 같이 틀리면 통과한다. 그래서 여기는
**계약 정의에서 곧바로 쓴 식**을 두고 엔진과 견준다. 고성능 재구현이 아니라
1~4기간의 확정값이다 — 손으로 따라 셀 수 있어야 한다.

이 파일의 앞부분(§A)은 `app.py` 를 **읽지 않는다.** 뒷부분(§B)이 엔진을 불러
앞부분과 대조한다. 앞부분 식을 고칠 일이 있으면 계약이나 교과서를 다시 봐야지
엔진을 봐서는 안 된다.

허용오차는 항목마다 근거를 적는다 (지시서 §1.3-6).

    python3 tests/오라클.py
"""
import sys, os, math, types, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


# ══════════════════════════════════════════════════════════
# §A. 독립 식 — 계약·교과서에서. app.py 를 보지 않는다.
# ══════════════════════════════════════════════════════════
def cont(y, m):
    """명목 연이율 y, 연 m 회 복리 → 연속복리. m=0 은 단리라 여기서 안 쓴다."""
    return m*math.log(1 + y/m)


def df(y, m, t):
    """할인계수. 평탄 곡선이면 현물 = 만기수익률이라 이것으로 충분하다."""
    return math.exp(-cont(y, m)*t)


def pv_bond(face, cpn, ipay_m, y, m, T):
    """이표채 현재가치. 이표는 발행일부터 ipay_m 개월마다, 만기에 원금(+마지막 이표).

    face·cpn·ipay/12 가 회당 이표다. 이 앱의 격자는 이표를 «지급일 노드에서 더하고
    그 자리부터 위험 이자율로 할인» 하므로, 평탄 곡선이면 이 식과 같아야 한다.
    """
    k = ipay_m/12
    c = face*cpn*k
    n = int(round(T/k))
    pv = sum(c*df(y, m, k*i) for i in range(1, n+1))
    return pv + face*df(y, m, T)


def accrue_simple(t, g, c):
    """단리 상환할증금률 — 「발행가에 연 g% 단리를 가산, 이미 지급한 이자 c 는 뺀다」."""
    return max(0.0, (g - c)*t)


def accrue_comp(t, g, c, m):
    """복리 상환할증금률 — 매 회차 (g−c)/m 를 적립해 g/m 로 굴린 연금의 미래가치.

        Σ_{k=1..mt} (g−c)/m · (1+g/m)^(mt−k)  =  (g−c)/g · ((1+g/m)^(mt) − 1)

    식을 닫힌 꼴로 쓰지 않고 **합으로 그대로 센다** — 독립성을 위해서다.
    """
    n = int(round(m*t))
    if n <= 0: return 0.0
    r = g/m; a = (g - c)/m
    return max(0.0, sum(a*(1 + r)**(n - k) for k in range(1, n+1)))


def crr(S0, sig, r_cont, dt):
    """CRR 한 스텝. u = e^{σ√Δt}, d = 1/u, q = (e^{rΔt} − d)/(u − d)."""
    u = math.exp(sig*math.sqrt(dt)); d = 1/u
    q = (math.exp(r_cont*dt) - d)/(u - d)
    return u, d, q


def node_rule(cv, pv, kv, hold, kfirst):
    """같은 노드 풋·콜 우선순위 — **max/min 닫힌 꼴**. 엔진의 if 사슬과 다른 표현이다.

        투자자 풋 우선 :  MAX(전환, 풋, MIN(보유, 콜))
        발행자 콜 우선 :  MAX(전환, MIN(MAX(보유, 풋), 콜))   (Hull)
    """
    if kfirst: return max(cv, min(max(hold, pv), kv))
    return max(cv, pv, min(hold, kv))


def refix_once(K, S, floor, par, cap, allow_up):
    """리픽싱 한 번. 하향만이면 min(K, S), 상향도 되면 S. 하한 max(floor, par), 상한 cap."""
    lo = max(floor, par)
    newK = S if allow_up else min(K, S)
    return min(max(newK, lo), cap)


def one_step_tf(S0, K, sig, rf, cr, dt, face, red, put_amt=None, call_amt=None,
                kfirst=False, cont_only=False):
    """1기간 TF 전환사채 — 만기 두 노드에서 정하고 뿌리로 할인.

    만기: 전환가치 cv = face·S/K 가 상환금액 red 보다 크면 전환(E=cv, B=0),
          아니면 상환(E=0, B=red). 전환은 동점이면 지지 않는다 (현금).
    뿌리: E 는 무위험, B 는 위험으로 할인한 뒤 보유 = E+B 를 풋·콜과 견준다.
    cont_only 면 뿌리에서 행사하지 않고 보유가치만 돌려준다 (B0·B1 검산용).
    """
    u, d, q = crr(S0, sig, rf, dt)
    def term(S):
        cv = face*S/K
        return (cv, 0.0) if cv > red else (0.0, red)
    Eu, Bu = term(S0*u); Ed, Bd = term(S0*d)
    E = (q*Eu + (1-q)*Ed)*math.exp(-rf*dt)
    B = (q*Bu + (1-q)*Bd)*math.exp(-cr*dt)
    hold = E + B
    if cont_only: return hold, E, B
    cv0 = face*S0/K
    pv0 = put_amt if put_amt is not None else -math.inf
    kv0 = call_amt if call_amt is not None else math.inf
    return node_rule(cv0, pv0, kv0, hold, kfirst), E, B


def bw_cash_one_step(S0, K, sig, rf, cr, dt, face, red):
    """현금납입 BW 1기간 — 사채는 남고 신주인수권은 max(cv − face, 0). 둘을 더한다."""
    u, d, q = crr(S0, sig, rf, dt)
    w = (q*max(face*S0*u/K - face, 0) + (1-q)*max(face*S0*d/K - face, 0))*math.exp(-rf*dt)
    b = red*math.exp(-cr*dt)
    return w + b, w, b


def sha_european(S0, K0, sig, rf, dt, pk, ck, put_disc):
    """주주간계약 1기간 유럽형 — 풋 (pk − 지분)+ 를 의무자 신용으로, 콜 (지분 − ck)+ 를 무위험으로."""
    u, d, q = crr(S0, sig, rf, dt)
    eq = lambda S: 100*S/K0
    put = (q*max(pk - eq(S0*u), 0) + (1-q)*max(pk - eq(S0*d), 0))*math.exp(-put_disc*dt)
    call = (q*max(eq(S0*u) - ck, 0) + (1-q)*max(eq(S0*d) - ck, 0))*math.exp(-rf*dt)
    return put, call


# ══════════════════════════════════════════════════════════
# §B. 엔진과 대조
# ══════════════════════════════════════════════════════════
def load_app():
    stub = types.ModuleType("streamlit"); stub.cache_data = lambda **k: (lambda f: f)
    sys.modules["streamlit"] = stub
    m = types.ModuleType("cbapp"); sys.modules["cbapp"] = m
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    exec(compile(src.split("st.set_page_config")[0], "app.py", "exec"), m.__dict__)
    return m.__dict__


def chk(tag, got, want, tol, why):
    ok = got is not None and abs(got - want) <= tol
    print("  %-56s %14.8f  기대 %14.8f  ±%-8g %s" % (tag, got if got is not None else float("nan"),
                                                     want, tol, "OK" if ok else "★"))
    if not ok: FAIL.append(f"{tag} — {why}")


# 허용오차 근거
#   금액(100 기준)  1e-6 : 격자와 닫힌 식이 같은 부동소수 연산을 다른 순서로 한다. 1e-9 차이가 난다.
#   이자율·확률     1e-12: 같은 식이면 기계 오차뿐이다.
#   전환가격(원)    1e-6 : clip 과 반올림(6자리)이 있다.
TOL_MONEY, TOL_RATE, TOL_K = 1e-6, 1e-12, 1e-6


def flat(G, y_rf, y_cr, **ov):
    """평탄 곡선의 Terms. 만기 1년 · 노드 4 (derive 가 최소 4 로 올린다)."""
    t = G["Terms"](d_issue="2025-01-01", d_base="2025-01-01", d_mat="2026-01-01",
                   gap_m=3.0, rfx_mode=0, k_w=0.0, p_s=99., p_e=0., cv_s=0., cv_e=12.,
                   cpn=0.0, ytm=0.0, carry=1)
    t.rf_curve = [(0.5, y_rf), (1, y_rf), (2, y_rf), (5, y_rf)]
    t.cr_curve = [(0.5, y_cr), (1, y_cr), (2, y_cr), (5, y_cr)]
    for k, v in ov.items(): setattr(t, k, v)
    G["derive"](t)
    return t


def test_discounting(G):
    print("\n[A] 옵션 없는 사채 = 닫힌 식 현재가치 (평탄 곡선)")
    for cpn, ipay, y, m in ((0.0, 3., .05, 4), (.06, 6., .05, 2), (.03, 3., .12, 4)):
        t = flat(G, .03, y, cpn=cpn, ipay=ipay, cmp_cr=m, cmp_rf=2)
        b0 = G["pick"](G["engine"](t, conv=False, put=False, call=False), "TF")
        want = pv_bond(100, cpn, ipay, y, m, 1.0)
        chk(f"표면 {cpn:.0%} · {ipay:.0f}개월 · 위험 {y:.0%} 연 {m}회", b0, want, TOL_MONEY,
            "격자 할인이 닫힌 식과 다르다 — 이표 일정이나 선도이자율 배선을 의심")


def test_crr(G):
    print("\n[B] CRR 상승·하락계수와 위험중립가중치")
    t = flat(G, .04, .10, sig=.30, cmp_rf=2)
    r = G["engine"](t, call=False)
    u, d, q = crr(t.S0, .30, cont(.04, 2), r["dt"])
    chk("u", r["u"], u, TOL_RATE, "u = exp(σ√Δt)")
    chk("d", r["d"], d, TOL_RATE, "d = 1/u")
    chk("q (첫 구간, 평탄 곡선)", r["q"], q, TOL_RATE, "q = (e^{rΔt} − d)/(u − d) — 연속환산이 다르다")


def test_accrue(G):
    print("\n[C] 상환할증금률 — 단리·복리를 합으로 센 값")
    for t_, g, c, m in ((2.0, .07, .03, 4), (3.0, .05, .0, 1), (1.25, .08, .02, 0), (5.0, .06, .06, 2)):
        want = accrue_simple(t_, g, c) if m == 0 else accrue_comp(t_, g, c, m)
        chk(f"t={t_} g={g:.0%} c={c:.0%} m={m}", G["accrue_rate"](t_, g, c, m), want, TOL_RATE,
            "닫힌 꼴과 합이 다르다")
    # 회차가 정수가 아닐 때(연 1회 복리 · 3.5년) — 합으로는 셀 수 없다. 이 앱은 지수를
    # 분수로 이어 (1+g)^{3.5} 를 쓴다 (부분 기간 복리 관행). 계약이 «완결 회차만 센다»고
    # 쓰면 (1+g)^3 에 단리 반년을 얹어야 하고 값이 다르다. 관행 선택이지 결함이 아니다 —
    # 여기 못 박아 두고, 4단계 결함표에는 「입력 안내에 적을 것」으로만 올린다.
    frac = (.05 - 0)/.05*((1 + .05)**3.5 - 1)
    chk("t=3.5 g=5% m=1 — 부분 기간은 분수 지수 (관행)", G["accrue_rate"](3.5, .05, 0., 1), frac, TOL_RATE,
        "부분 기간 관행이 바뀌었다 — 바꿨다면 입력 안내와 조서 xl_prem 도 같이 바꿔야 한다")


def test_node_rule(G):
    print("\n[D] 노드 우선순위 — 닫힌 꼴 max/min 과 엔진 if 사슬")
    import random
    random.seed(5); bad = 0; N = 50000
    nd = G["node_decide"]
    for _ in range(N):
        cv, pv, kv, hold = (round(random.uniform(80, 140), 3) for _ in range(4))
        for kf in (0, 1):
            want = node_rule(cv, pv, kv, hold, kf)
            k = nd(cv, pv, kv, hold, kf)
            got = {"conv": cv, "put": pv, "call": kv, "hold": hold}[k]
            if abs(got - want) > 1e-12: bad += 1
    chk(f"난수 {N*2:,}건 — 닫힌 꼴 값과 결정이 고른 값의 불일치 수", bad, 0, 0,
        "if 사슬이 max/min 항등식과 어긋난다")
    # 동점: 전환은 +TOL 앞설 때만 이긴다. 닫힌 꼴은 동점에서 어느 쪽이든 같은 값이다.
    for cv, pv in ((100.0, 100.0), (100.0 + 5e-10, 100.0)):
        k = nd(cv, pv, math.inf, 90.0, 0)
        chk(f"동점 전환 {cv} 대 풋 {pv} → 현금", 1.0 if k == "put" else 0.0, 1.0, 0,
            "동점이면 현금(상환)이어야 한다")


def test_refix(G):
    print("\n[E] 리픽싱 한 번 — 하한·상한")
    t = flat(G, .03, .10, rfx_mode=1, rfx_cyc=3., floor=700., par=500., K0=1000., S0=850., sig=.40)
    t.K_cap = 1000.
    G["derive"](t)
    r = G["engine"](t, call=False)
    Kg = r["Kg"]
    # 첫 조정 노드(스텝 1) — 하향만: 아래 노드는 S=850·d, 위 노드는 S=850·u(>K 이면 그대로)
    u, d, _ = crr(850., .40, cont(.03, 2), r["dt"])
    want_dn = refix_once(1000., 850.*d, 700., 500., 1000., False)
    want_up = refix_once(1000., 850.*u, 700., 500., 1000., False)
    chk("스텝1 하락 노드 K", Kg[1][0], want_dn, TOL_K, "하향 리픽싱·하한이 다르다")
    chk("스텝1 상승 노드 K (하향만이라 그대로)", Kg[1][1], want_up, TOL_K, "상향 금지인데 올랐다")
    t2 = flat(G, .03, .10, rfx_mode=2, rfx_cyc=3., floor=700., par=500., K0=800., S0=850., sig=.40)
    t2.K_cap = 1000.; G["derive"](t2)
    r2 = G["engine"](t2, call=False)
    u2, d2, _ = crr(850., .40, cont(.03, 2), r2["dt"])
    chk("하향+상향 · 상승 노드 K (상한 1000 안)", r2["Kg"][1][1],
        refix_once(800., 850.*u2, 700., 500., 1000., True), TOL_K, "상향 조정·상한이 다르다")


def test_one_step_tree(G):
    print("\n[F] 1기간 전환사채 — 만기 두 노드에서 뿌리로")
    # n 이 최소 4 라 dt 가 4 스텝이다. 만기까지 전환·상환만 있고 중간 행사가 없게
    # 두면 4 스텝 격자가 «각 층에서 보유»라 닫힌 식과 같은 논리로 돈다. 그래서
    # 1기간 식은 dt=T/4 의 «마지막 한 스텝» 을 손으로 재는 데 쓴다 — 가장 위·아래
    # 만기 노드에서 한 스텝 앞 노드의 E·B 를 엔진 memo 와 견준다.
    t = flat(G, .04, .12, S0=1000., K0=1000., sig=.35, cmp_rf=2, cmp_cr=4)
    r = G["engine"](t, call=False)
    n, dt_ = r["n"], r["dt"]
    red = 100.0
    rf, cr = cont(.04, 2), cont(.12, 4)
    for j, nm in ((n-1, "맨 위"), (0, "맨 아래")):
        node = r["memo"][(n-1, j)]
        S = t.S0 * r["u"]**j * r["d"]**(n-1-j)
        hold, E, B = one_step_tf(S, 1000., .35, rf, cr, dt_, 100, red, cont_only=True)
        chk(f"스텝 {n-1} {nm} 노드 · 지분 E", node["E"], E, TOL_MONEY, "만기 전환 판정이나 무위험 할인")
        chk(f"스텝 {n-1} {nm} 노드 · 부채 B", node["B"], B, TOL_MONEY, "만기 상환 판정이나 위험 할인")


def test_bw_cash(G):
    print("\n[G] 현금납입 BW = 사채 + 신주인수권 (마지막 한 스텝)")
    t = flat(G, .04, .12, inst="BW", bw_pay=0, bw_detach=1, S0=1000., K0=1000., sig=.35,
             cmp_rf=2, cmp_cr=4)
    r = G["engine"](t, call=False)
    n, dt_ = r["n"], r["dt"]
    node = r["memo"][(n-1, n-1)]
    S = t.S0 * r["u"]**(n-1)
    tot, w, b = bw_cash_one_step(S, 1000., .35, cont(.04, 2), cont(.12, 4), dt_, 100, 100.)
    chk("맨 위 노드 · 신주인수권 E", node["E"], w, TOL_MONEY, "신주인수권 페이오프 (cv − 100)+")
    chk("맨 위 노드 · 사채 B", node["B"], b, TOL_MONEY, "사채가 소멸하지 않고 남아야 한다")


def test_sha_european(G):
    print("\n[H] 주주간계약 마지막 한 스텝 — 유럽형 풋·콜")
    t = G["Terms"](inst="SHA", d_issue="2025-01-01", d_base="2025-01-01", d_mat="2026-01-01",
                   gap_m=3.0, S0=1000., K0=1000., sig=.35, sha_put_s=0., sha_put_e=12., sha_put_f=3.,
                   sha_put_yield=.08, sha_put_cmp=1, sha_call_s=0., sha_call_e=12., sha_call_f=3.,
                   sha_call_prem=.10, sha_call_cmp=1, sha_disc=0, cmp_rf=2)
    t.rf_curve = [(0.5, .04), (1, .04), (2, .04), (5, .04)]
    t.cr_curve = [(0.5, .12), (1, .12), (2, .12), (5, .12)]
    G["derive"](t)
    R = G["sha_engine"](t)
    n, dt_ = R["n"], R["dt"]
    # 만기 한 스텝 앞 맨 아래 노드: 그 자리 즉시행사와 다음 스텝 기대값 중 큰 쪽
    j = 0; S = t.S0 * R["d"]**(n-1)
    pk, ck = R["pk"](n), R["ck"](n)
    put_e, call_e = sha_european(S, 1000., .35, cont(.04, 2), dt_, pk, ck, cont(.04, 2))
    imm_p = max(R["pk"](n-1) - 100*S/1000., 0.0)
    chk("맨 아래 노드 풋 = MAX(즉시행사, 유럽형)", R["P"][n-1][j], max(imm_p, put_e), TOL_MONEY,
        "풋 할인율·행사금액")
    chk("맨 아래 노드 콜 (깊은 외가격 → 0)", R["C"][n-1][j], call_e, TOL_MONEY, "콜이 무위험으로 할인")


def test_date_boundaries(G):
    print("\n[J] 날짜 경계 — 행사기간의 첫·마지막 노드를 달력에서 직접 센다")
    # 독립 정의: 노드 i 의 날짜 = 평가기준일 + round(i·Δt·365) 일. 계약일 = 발행일 + m 개월
    # (그 달에 그 날이 없으면 말일). 시작은 계약일 «이후» 첫 노드, 종료는 계약일 «이전» 마지막
    # 노드. 노드가 계약일과 며칠 어긋나면(격자 간격의 1/4, 최대 5일) 같은 날로 본다.
    import datetime as D, calendar
    def addm(d, k):
        y, mo = d.year + (d.month - 1 + k)//12, (d.month - 1 + k) % 12 + 1
        return D.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))
    def ref(t):
        n = int(t.n); dt_ = t.T/n; day = dt_*365
        di, db = D.date.fromisoformat(t.d_issue), D.date.fromisoformat(t.d_base)
        nd = [db + D.timedelta(days=round(i*day)) for i in range(n+1)]
        tol = D.timedelta(days=min(5, max(1, int(day//4))))
        def lo(m):
            c = addm(di, int(m)) - tol
            return next((i for i, x in enumerate(nd) if x >= c), n+1)
        def hi(m):
            c = addm(di, int(m)) + tol
            return next((i for i in range(n, -1, -1) if nd[i] <= c), -1)
        return lo, hi, n
    cases = [
        ("발행일 = 기준일 · 월 노드 · 12/24개월", dict(d_issue="2025-03-31", d_base="2025-03-31", d_mat="2030-03-31", gap_m=1.), (12, 24, 59, 60)),
        ("기준일이 발행 6개월 뒤 · 3개월 노드",   dict(d_issue="2025-03-31", d_base="2025-09-30", d_mat="2030-03-31", gap_m=3.), (6, 12, 24, 57, 60)),
        ("말일 발행 (1/31) · 2월 말일 맞춤",      dict(d_issue="2025-01-31", d_base="2025-01-31", d_mat="2030-01-31", gap_m=1.), (1, 13, 25)),
        ("기준일이 노드와 안 맞는 날 (4/15)",     dict(d_issue="2025-03-31", d_base="2025-04-15", d_mat="2030-03-31", gap_m=1.), (1, 12, 24)),
        ("경과 후 첫 행사가 이미 지난 경우",       dict(d_issue="2023-03-31", d_base="2025-09-30", d_mat="2030-03-31", gap_m=1.), (12, 24, 30, 31)),
    ]
    for nm, kw, ms in cases:
        t = G["Terms"](**kw); t.rf_curve = [(1, .03), (5, .03)]; t.cr_curve = [(1, .10), (5, .10)]
        G["derive"](t)
        lo, hi, n = ref(t)
        elo, ehi = G["step_mapper"](t, n, t.T/n)
        bad = [(m, elo(m), lo(m), ehi(m), hi(m)) for m in ms if (elo(m), ehi(m)) != (lo(m), hi(m))]
        chk(f"{nm} — 어긋난 개월 수", len(bad), 0, 0, f"step_mapper 가 달력과 다르다: {bad}")
        # 경계 성질: 시작 노드는 계약일보다 앞설 수 없고(허용 어긋남 안), 종료 노드는 뒤설 수 없다
        for m in ms:
            a, b = elo(m), ehi(m)
            if 0 <= a <= n and 0 <= b <= n:
                chk_ok = a <= b + 1
                if not chk_ok: FAIL.append(f"{nm} m={m}: lo {a} > hi+1 {b}")
    # 만기까지 열린 풋(p_e = 만기 개월)은 만기 노드 n 에서 닫혀야 하고, 만기 +1 개월은 비어야 한다
    t = G["Terms"](d_issue="2025-03-31", d_base="2025-03-31", d_mat="2030-03-31", gap_m=1.)
    t.rf_curve = [(1, .03), (5, .03)]; t.cr_curve = [(1, .10), (5, .10)]; G["derive"](t)
    _, ehi = G["step_mapper"](t, int(t.n), t.T/int(t.n))
    chk("만기 60개월 → 마지막 노드 n", ehi(60), int(t.n), 0, "만기 노드가 행사기간에서 빠진다")
    chk("만기 넘는 61개월 → n (넘어도 만기까지)", ehi(61), int(t.n), 0, "만기 뒤 개월이 n 을 넘긴다")


def test_sequential_identities(G):
    print("\n[I] 순차 차감 항등식 — 기본 계약")
    t = G["Terms"](); t.rf_curve = [(1, .0226), (3, .0240), (5, .0252)]
    t.cr_curve = [(1, .1409), (3, .1740), (5, .1905)]; G["derive"](t)
    full, b0, b1, b2, ca, conv = G["decompose"](t)
    rows, _ = G["allocate"](t, full, b0, b1, b2, ca)
    chk("풋 = B1 − B0 ≥ 0", max(b1 - b0, 0), b1 - b0, 0, "풋 값이 음수")
    chk("전환 = B2 − B1 ≥ 0", max(b2 - b1, 0), b2 - b1, 0, "전환권 값이 음수")
    chk("배분표 합계 = 100", rows[-1][1], 100.0, 1e-9, "배분이 발행대가와 안 맞는다")
    chk("B0 + 풋 + 전환 = B2", b0 + (b1 - b0) + (b2 - b1), b2, 1e-12, "차감 사슬이 안 닫힌다")


def main():
    print("독립 오라클 대조 — 앞부분 식은 app.py 를 보지 않고 썼다. 엔진을 고쳤다고 여기를 고치지 말 것.")
    G = load_app()
    for f in (test_discounting, test_crr, test_accrue, test_node_rule, test_refix,
              test_one_step_tree, test_bw_cash, test_sha_european, test_date_boundaries,
              test_sequential_identities):
        f(G)
    print()
    if FAIL:
        print(f"★ 어긋남 {len(FAIL)}건")
        for x in FAIL: print("   -", x)
        return 1
    print("모두 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
