"""주식선택권(주식결제형) 공정가치와 보상원가 — 기업회계기준서 제1102호.

전환사채·상환전환우선주와 따로 계산한다. 사채가 없고 행사가격이 있는 콜옵션이다.

- 회차(단계 가득)마다 따로 이항격자(CRR)를 세운다. 행사 시작일 전에는 행사할 수 없다.
  행사 시작일은 보통 가득일과 같지만, 근무조건은 먼저 끝나고 행사만 늦게 열리는 회차도 있다
  (예: 2년 근무 뒤 50%는 바로, 50%는 1년 뒤부터 행사). 비용은 가득일까지 나눈다.
- 가득 뒤의 조기행사는 평가자가 고른 가정을 따른다 (문단 B5, B16~B19).
    0 만기에만 행사 — 유럽형. 블랙-숄즈-머튼 공식과 같은 값으로 수렴한다.
    1 최적 행사 — 노드마다 «지금 행사» 와 «계속 보유» 중 큰 쪽.
    2 행사 배수 — 주가가 행사가격의 배수 이상이면 행사한다.
  가득 뒤 퇴사율을 넣으면 매 구간 그 비율만큼 퇴사해 내가격이면 행사, 아니면 소멸한다.
- 근무조건(가득 전 퇴사)은 공정가치가 아니라 «가득 예상 비율» 로 수량에 반영한다 (문단 19).
- 보상원가는 회차마다 «1주당 공정가치 × 부여 수량 × 가득 예상 비율» 이고, 부여일부터
  가득일까지 일수로 나눠 회계연도에 배분한다.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

ROW_KEYS = ("name", "vest", "start", "end", "qty", "keep")
EX_LABELS = {0: "만기에만 행사 (유럽형)", 1: "가득 뒤 최적 행사", 2: "행사 배수에 도달하면 행사"}
SETTLE_LABELS = {0: "주식결제형", 1: "현금결제형 (2단계 예정)"}
# 조기행사가 없을 때 격자값과 블랙-숄즈 값의 허용 차이 (상대). 주 단위 격자면 보통 0.1% 안쪽이다.
BS_TOL = 0.005


def is_so(tm) -> bool:
    return (getattr(tm, "inst", "") or "").upper() == "SO"


def _date(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


def row_defaults(row: dict, tm=None) -> dict:
    out = {"name": "", "vest": "", "start": "", "end": "", "qty": 0.0, "keep": 1.0}
    out.update({k: v for k, v in (row or {}).items() if k in ROW_KEYS})
    if not str(out["start"] or "").strip():
        out["start"] = out["vest"]          # 빈칸 = 가득일부터 행사
    if tm is not None and not str(out["end"] or "").strip():
        out["end"] = tm.d_mat
    return out


def rows(tm) -> list[dict]:
    return [row_defaults(r, tm) for r in (tm.so_rows or [])]


def issues(tm) -> list[str]:
    """입력 검사 — 계산 전에 막을 것만. 문장은 화면에 그대로 나간다."""
    out = []
    if int(tm.so_settle) != 0:
        out.append("현금결제형(차액보상형)은 2단계에서 지원합니다. 지금은 주식결제형만 평가합니다.")
    if int(tm.so_ex) not in EX_LABELS:
        out.append("조기행사 가정을 고르십시오.")
    if int(tm.so_ex) == 2 and not tm.so_mult > 1:
        out.append("행사 배수는 1보다 커야 합니다 (주가가 행사가격의 몇 배일 때 행사하는가).")
    if not 0 <= tm.so_exit < 1:
        out.append("가득 뒤 연간 퇴사율은 0% 이상 100% 미만이어야 합니다.")
    try:
        m, d = (int(x) for x in str(tm.so_fy_end).split("-"))
        dt.date(2001, m, d)
    except (ValueError, TypeError):
        out.append("결산일은 «12-31» 처럼 월-일로 넣으십시오.")
    rs = tm.so_rows or []
    if not rs:
        out.append("회차표에 회차를 한 줄 이상 넣으십시오 (가득일·부여 수량).")
    try:
        di, db, dm = _date(tm.d_issue), _date(tm.d_base), _date(tm.d_mat)
    except (ValueError, TypeError):
        return out + ["부여일·측정기준일·행사기간 종료일을 날짜로 넣으십시오."]
    names = []
    for k, raw in enumerate(rs, start=1):
        r = row_defaults(raw, tm)
        label = str(r["name"]).strip() or f"{k}번째 줄"
        names.append(str(r["name"]).strip())
        if not str(r["name"]).strip():
            out.append(f"{k}번째 줄: 회차 이름을 넣으십시오.")
        try:
            vest, start, end = _date(str(r["vest"])), _date(str(r["start"])), _date(str(r["end"]))
        except (ValueError, TypeError):
            out.append(f"{label}: 가득일·행사 시작일·행사 종료일은 YYYY-MM-DD 로 넣으십시오.")
            continue
        if vest < di:
            out.append(f"{label}: 가득일이 부여일보다 빠릅니다.")
        if start < vest:
            out.append(f"{label}: 행사 시작일이 가득일보다 빠릅니다 (가득 전에는 행사할 수 없습니다).")
        if not start <= end:
            out.append(f"{label}: 행사 종료일이 행사 시작일보다 빠릅니다.")
        if end > dm:
            out.append(f"{label}: 행사 종료일이 행사기간 종료일({tm.d_mat})보다 늦습니다.")
        if end <= db:
            out.append(f"{label}: 행사 종료일이 측정기준일 이전이라 평가할 기간이 없습니다.")
        if not (isinstance(r["qty"], (int, float)) and math.isfinite(r["qty"]) and r["qty"] > 0):
            out.append(f"{label}: 부여 수량은 0보다 커야 합니다.")
        if not (isinstance(r["keep"], (int, float)) and math.isfinite(r["keep"]) and 0 < r["keep"] <= 1):
            out.append(f"{label}: 가득 예상 비율은 0%보다 크고 100% 이하여야 합니다.")
    dup = sorted({n for n in names if n and names.count(n) > 1})
    if dup:
        out.append("회차 이름이 겹칩니다: " + ", ".join(dup))
    return out


def rate_fn(tm, horizon):
    """무위험 연속복리 현물이자율 r(t). 위험 곡선은 쓰지 않는다 — 신용위험이 없는 지분상품이다."""
    from .legacy import make_curve, spot_from_zero
    if tm.y_type == "spot":
        return spot_from_zero(tm.rf_curve, tm.cmp_rf)
    return make_curve(tm.rf_curve, max(horizon, 1e-6), tm.cmp_rf)


def _ncdf(x):
    return 0.5*math.erfc(-x/math.sqrt(2.0))


def black_scholes(S, K, T, r, q, sig) -> float:
    """블랙-숄즈-머튼 유럽형 콜 (연속복리 r·q)."""
    if T <= 0:
        return max(S - K, 0.0)
    v = sig*math.sqrt(T)
    d1 = (math.log(S/K) + (r - q + 0.5*sig*sig)*T)/v
    return S*math.exp(-q*T)*_ncdf(d1) - K*math.exp(-r*T)*_ncdf(d1 - v)


def steps_for(tm, years: float) -> int:
    """측정기준일부터 그 회차 행사 종료일까지의 구간 수 — 계산 간격(주·2주·월)을 따른다."""
    if tm.grid_days and tm.grid_days > 0:
        n = round(years*365/tm.grid_days)
    else:
        n = round(years*12/max(0.25, tm.gap_m))
    return int(max(4, n))


def value_row(tm, row: dict, R=None) -> dict:
    """한 회차를 격자에서 잰다. 돌려주는 값은 1주당(원)."""
    from .legacy import forward_rate, lattice_ud
    db = _date(tm.d_base)
    t_ex = max(0.0, (_date(row["start"]) - db).days/365)      # 행사 시작
    t_end = (_date(row["end"]) - db).days/365
    n = steps_for(tm, t_end)
    dt_ = t_end/n
    R = R or rate_fn(tm, t_end)
    u, d = lattice_ud(tm, dt_)
    q = float(tm.div_y)
    K, S0 = float(tm.K0), float(tm.S0)
    # 행사 시작일 이후 첫 노드부터 행사할 수 있다 (노드 사이면 다음 노드).
    ex_step = min(n, max(0, int(math.ceil(t_ex/dt_ - 1e-9))))
    fwd = np.array([forward_rate(R, i*dt_, (i+1)*dt_) for i in range(n)])
    p = (np.exp((fwd - q)*dt_) - d)/(u - d)
    disc = np.exp(-fwd*dt_)
    qbad = bool(np.any((p <= 0) | (p >= 1)))
    e_step = 1.0 - (1.0 - float(tm.so_exit))**dt_          # 한 구간에 퇴사할 확률 (행사 시작 뒤)
    mode, mult = int(tm.so_ex), float(tm.so_mult)
    j = np.arange(n + 1)
    S = S0*u**(n - 2*j)
    V = np.maximum(S - K, 0.0)
    values, prices, exercise = [None]*(n + 1), [None]*(n + 1), [None]*(n + 1)
    values[n], prices[n], exercise[n] = V.copy(), S.copy(), (S - K > 0)
    for i in range(n - 1, -1, -1):
        jj = np.arange(i + 1)
        S = S0*u**(i - 2*jj)
        cont = disc[i]*(p[i]*V[:-1] + (1 - p[i])*V[1:])
        ex = S - K
        if i >= ex_step:
            hold = (1 - e_step)*cont + e_step*np.maximum(ex, 0.0)
            if mode == 1:
                go = ex > hold + 1e-12*np.maximum(1.0, np.abs(hold))
            elif mode == 2:
                go = (S >= mult*K - 1e-12*K) & (ex > 0)
            else:
                go = np.zeros(i + 1, dtype=bool)
            V = np.where(go, ex, hold)
        else:
            go = np.zeros(i + 1, dtype=bool)
            V = cont
        values[i], prices[i], exercise[i] = V.copy(), S.copy(), go
    fv = float(V[0])
    # 기대존속기간 — 위험중립확률로 «행사·퇴사·만기» 중 먼저 오는 때를 평균한다.
    mass, life = np.array([1.0]), 0.0
    for i in range(n):
        t = i*dt_
        go = exercise[i]
        life += float(mass[go].sum())*t
        stay = np.where(go, 0.0, mass)
        if i >= ex_step and e_step > 0:
            life += float(stay.sum())*e_step*t
            stay = stay*(1 - e_step)
        nxt = np.zeros(i + 2)
        nxt[:-1] += stay*p[i]
        nxt[1:] += stay*(1 - p[i])
        mass = nxt
    life += float(mass.sum())*t_end
    r_end = R(t_end)
    bs_end = black_scholes(S0, K, t_end, r_end, q, float(tm.sig))
    r_life = R(max(life, 1e-6))
    bs_life = black_scholes(S0, K, life, r_life, q, float(tm.sig))
    return dict(
        name=row["name"], vest=row["vest"], start=row["start"], end=row["end"], qty=float(row["qty"]),
        keep=float(row["keep"]), t_ex=t_ex, t_end=t_end, n=n, dt=dt_, ex_step=ex_step, u=u, d=d,
        fv=fv, bs_end=bs_end, bs_life=bs_life, life=life, r_end=r_end, qbad=qbad,
        p_min=float(p.min()), p_max=float(p.max()), fwd=fwd.tolist(), p=p.tolist(),
        values=[v.tolist() for v in values], prices=[s.tolist() for s in prices],
        exercise=[g.tolist() for g in exercise],
    )


def fiscal_ends(start: dt.date, last: dt.date, fy_end: str) -> list[dt.date]:
    """start 이후 첫 결산일부터 last 를 덮는 결산일까지."""
    m, d = (int(x) for x in fy_end.split("-"))

    def end_in(y):
        import calendar
        return dt.date(y, m, min(d, calendar.monthrange(y, m)[1]))
    y = start.year
    while end_in(y) < start:
        y += 1
    out = [end_in(y)]
    while out[-1] < last:
        y += 1
        out.append(end_in(y))
    return out


def schedule(tm, rows_out: list[dict]) -> list[dict]:
    """회계연도별 보상원가 배분 — 부여일부터 가득일까지 일수 비례 (회차마다 따로)."""
    di = _date(tm.d_issue)
    last = max(_date(r["vest"]) for r in rows_out)
    ends = fiscal_ends(di, max(last, di), str(tm.so_fy_end))
    out = []
    for r in rows_out:
        vest, total = _date(r["vest"]), r["cost"]
        span = (vest - di).days
        prev = 0.0
        for e in ends:
            share = 1.0 if span <= 0 else min(1.0, max(0.0, (e - di).days/span))
            cum = total*share
            out.append(dict(fy_end=e.isoformat(), name=r["name"], expense=cum - prev, cum=cum, share=share))
            prev = cum
            if share >= 1.0:
                break                    # 가득된 회차는 그 뒤 결산일에 비용이 없다
    return out


def value(tm) -> dict:
    """회차마다 평가하고, 합계·가중평균·비용 배분·점검을 붙인다."""
    rs = rows(tm)
    horizon = max((_date(r["end"]) - _date(tm.d_base)).days/365 for r in rs)
    R = rate_fn(tm, horizon)
    out_rows = []
    for r in rs:
        v = value_row(tm, r, R)
        v["fair"] = v["fv"]*v["qty"]                  # 부여 수량 전체의 공정가치
        v["cost"] = v["fv"]*v["qty"]*v["keep"]        # 보상원가 (가득 예상 수량)
        out_rows.append(v)
    qty = sum(r["qty"] for r in out_rows)
    fair = sum(r["fair"] for r in out_rows)
    cost = sum(r["cost"] for r in out_rows)
    fv_avg = fair/qty if qty else 0.0
    sched = schedule(tm, out_rows)
    raw = dict(rows=out_rows, qty=qty, fair=fair, cost=cost, fv_avg=fv_avg, schedule=sched,
               qbad=any(r["qbad"] for r in out_rows))
    raw["integrity"] = integrity(tm, raw)
    return raw


def integrity(tm, raw) -> list[str]:
    """계산이 고장 나지 않았는지 — 걸리면 조서를 만들지 않는다 (service.export_bundle)."""
    bad = []
    S0 = float(tm.S0)
    for r in raw["rows"]:
        if not (0 <= r["fv"] <= S0*(1 + 1e-9)) or not math.isfinite(r["fv"]):
            bad.append(f"{r['name']} 공정가치 범위(0 ~ 주가)")
        if int(tm.so_ex) == 0 and tm.so_exit == 0 and r["bs_end"] > 1e-6*S0:
            if abs(r["fv"] - r["bs_end"]) > BS_TOL*r["bs_end"] + 1e-9*S0:
                bad.append(f"{r['name']} 유럽형 격자값과 블랙-숄즈 값의 수렴")
    total = sum(x["expense"] for x in raw["schedule"])
    if abs(total - raw["cost"]) > 1e-6*max(1.0, raw["cost"]):
        bad.append("비용 배분 합계와 총 보상원가")
    return bad


def checks(tm, raw) -> list[dict]:
    """화면·조서의 «계산 점검» 표 — 이름·통과·내용."""
    S0 = float(tm.S0)
    rows_ = raw["rows"]
    pmin = min(r["p_min"] for r in rows_)
    pmax = max(r["p_max"] for r in rows_)
    out = [
        dict(name="유한한 평가금액", passed=all(math.isfinite(r["fv"]) for r in rows_),
             detail="숫자가 아닌 값·무한대가 없는지 확인"),
        dict(name="위험중립확률 범위", passed=0 < pmin and pmax < 1,
             detail=f"최소 {pmin:.8f}, 최대 {pmax:.8f}; 0 < 확률 < 1"),
        dict(name="공정가치 범위", passed=all(0 <= r["fv"] <= S0*(1 + 1e-9) for r in rows_),
             detail="0 ≤ 1주당 공정가치 ≤ 부여일 주가"),
        dict(name="보상원가 합계 대사",
             passed=abs(sum(r["cost"] for r in rows_) - raw["cost"]) <= 1e-6*max(1.0, raw["cost"]),
             detail="회차별 «공정가치 × 수량 × 가득 예상 비율» 의 합 = 총 보상원가"),
        dict(name="비용 배분 합계 대사",
             passed=abs(sum(x["expense"] for x in raw["schedule"]) - raw["cost"]) <= 1e-6*max(1.0, raw["cost"]),
             detail="회계연도별 비용의 합 = 총 보상원가"),
    ]
    if int(tm.so_ex) == 0 and tm.so_exit == 0:
        worst = max(abs(r["fv"] - r["bs_end"])/max(r["bs_end"], 1e-12) for r in rows_)
        out.append(dict(name="블랙-숄즈 수렴 (유럽형)", passed=worst <= BS_TOL,
                        detail=f"격자값과 블랙-숄즈 값의 최대 상대 차이 {worst:.4%} (허용 {BS_TOL:.1%})"))
    return out


def node_trace(tm, row: dict, i: int, j: int) -> dict:
    """한 노드의 계산을 풀어 쓴다 — 새로 평가하지 않고 value_row 가 남긴 격자를 읽는다.

    j 는 위에서부터 센 자리(0 = 가장 높은 주가)다. 돌려주는 값으로 «계속 보유 · 지금 행사 · 퇴사» 를
    손으로 다시 계산할 수 있다.
    """
    n = row["n"]
    i, j = int(i), int(j)
    if not (0 <= i <= n and 0 <= j <= i):
        raise ValueError(f"노드 범위를 벗어났습니다 (시점 0~{n}, 자리 0~시점).")
    K, dt_ = float(tm.K0), row["dt"]
    S = row["prices"][i][j]
    out = dict(step=i, pos=j, time=i*dt_, date=(_date(tm.d_base) + dt.timedelta(days=round(i*dt_*365))).isoformat(),
               stock=S, strike=K, intrinsic=S - K, value=row["values"][i][j], exercise=bool(row["exercise"][i][j]),
               vested=i >= row["ex_step"], terminal=i == n)
    if i == n:
        out.update(rule="행사 종료일 — 내가격이면 행사, 아니면 소멸: max(주가 − 행사가격, 0)")
        return out
    p, f = row["p"][i], row["fwd"][i]
    up, dn = row["values"][i + 1][j], row["values"][i + 1][j + 1]
    disc = math.exp(-f*dt_)
    cont = disc*(p*up + (1 - p)*dn)
    e = 1.0 - (1.0 - float(tm.so_exit))**dt_ if out["vested"] else 0.0
    hold = (1 - e)*cont + e*max(S - K, 0.0)
    out.update(up=up, down=dn, up_stock=row["prices"][i + 1][j], down_stock=row["prices"][i + 1][j + 1],
               p=p, fwd=f, disc=disc, cont=cont, exit_step=e, hold=hold,
               reconciliation=out["value"] - (S - K if out["exercise"] else hold))
    if not out["vested"]:
        out["rule"] = "행사 시작 전 — 행사할 수 없어 계속 보유 가치"
    elif out["exercise"]:
        out["rule"] = {1: "지금 행사 가치가 계속 보유 가치보다 커서 행사",
                       2: f"주가가 행사가격의 {float(tm.so_mult):g}배 이상이라 행사"}.get(int(tm.so_ex), "행사")
    else:
        out["rule"] = "계속 보유" + (" (퇴사하면 그때 내가격분만 받음)" if e > 0 else "")
    return out
