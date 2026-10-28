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
  가득일과 행사 시작일 사이에 퇴사하면 선택권이 남는지 소멸하는지는 계약마다 달라 정하지 않는다 —
  퇴사율을 넣었는데 행사 시작일이 가득일보다 늦은 회차가 있으면 입력 검사가 막는다.
- 근무조건(가득 전 퇴사)·비시장 성과조건은 공정가치가 아니라 «가득 예상 비율» 로 수량에 반영한다 (문단 19).
- 시장조건(목표 주가)은 격자 안에서 판정한다 (문단 21). 회차마다 목표 주가를 넣고 판정 방식을 고른다.
    1 가득일 판정 — 가득일 노드에서 주가가 목표 미만이면 소멸(가치 0). 노드는 로그 주가로 위아래 반 칸씩의 구간을
      대표하므로, 목표 주가가 걸친 노드는 그 구간 가운데 목표 이상인 비율만큼 가치를 남긴다(pass_share) — 격자가
      계단 모양이라 생기는 오차가 닫힌 식(갭 옵션) 대비 수 %에서 0.3% 안쪽으로 준다.
    2 한 번이라도 도달 — 가득일까지 노드 주가가 한 번이라도 목표 이상이면 가득. «아직 닿지 않은 상태» 의 가치를
      따로 계산한다(wait). 닿은 노드에서는 보통 격자의 가치(values)로 바뀐다. 관측은 격자 노드(계산 간격)마다.
  시장조건을 채우지 못해도 근무조건을 채우면 비용을 되돌리지 않는다 — 수량(가득 예상 비율)에는 넣지 않는다.
- 비가득조건(예: 행사가격 납입용 적립 계속)은 공정가치에 넣는다 (문단 21A). 가득일까지 구간마다 연간 중단율만큼
  선택권을 잃는다고 보고 «1 − 구간 중단 확률» 을 곱한다. 기대존속기간은 가득된다고 보고 잰다 (문단 B18⑴).
- 보상원가는 회차마다 «1주당 공정가치 × 부여 수량 × 가득 예상 비율» 이고, 부여일부터
  가득일까지 일수로 나눠 회계연도에 배분한다.

세 가지로 쓴다 (mode):
- 부여일 평가 — 위의 기본.
- 조건 변경 (so_mod) — 측정기준일이 조건 변경일이다. 같은 날의 시장자료로 «변경 전 조건» 과 «변경 후 조건»
  을 각각 재고, 늘어난 만큼(증분 공정가치)을 더 인식한다. 줄어들면 더 인식하지 않는다 (문단 27, 부록 나43·나44).
  당초 부여일 공정가치는 변경표에 회차마다 넣는다 — 당초 보상원가 배분에 쓴다.
- 현금결제형 (so_settle) — 부채다. 결산일마다 공정가치로 다시 재고(문단 30), 근무 경과율만큼 부채로 잡는다.
  당기 비용 = 당기말 부채 − 전기말 부채.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

ROW_KEYS = ("name", "vest", "start", "end", "qty", "keep", "target")
MOD_KEYS = ("name", "K", "vest", "start", "end", "qty", "grant_fv")
EX_LABELS = {0: "만기에만 행사 (유럽형)", 1: "가득 뒤 최적 행사", 2: "행사 배수에 도달하면 행사"}
SETTLE_LABELS = {0: "주식결제형", 1: "현금결제형 (차액보상)"}
MKT_LABELS = {0: "없음", 1: "가득일 주가가 목표 주가 이상이면 가득", 2: "가득일까지 한 번이라도 목표 주가 이상이면 가득"}
MKT_TOL = 1e-12          # 목표 주가 비교 — 엔진과 수식 조서가 같은 여유를 쓴다


def pass_share(S, u, H):
    """가득일 노드가 대표하는 로그 주가 구간 [ln S − ln u, ln S + ln u] 가운데 목표 주가 이상인 비율 (0~1)."""
    return np.clip((np.log(S) + math.log(u) - math.log(H))/(2*math.log(u)), 0.0, 1.0)
# 조기행사가 없을 때 격자값과 블랙-숄즈 값의 허용 차이 (상대). 주 단위 격자면 보통 0.1% 안쪽이다.
BS_TOL = 0.005


def is_so(tm) -> bool:
    return (getattr(tm, "inst", "") or "").upper() == "SO"


MAX_STEPS = 1200          # 회차 격자 한도 — service._prepare 의 계산 한도와 같다


def _too_long(tm, label, end, db):
    """회차 격자 구간 수가 한도를 넘는가 — 회차마다(조건 변경이면 변경 후도) 따로 센다."""
    n = steps_for(tm, max(0.0, (end - db).days/365))
    return ([f"{label}: 격자가 {n:,}구간이라 계산 한도({MAX_STEPS:,}구간)를 넘습니다 — 계산 간격을 넓히십시오."]
            if n > MAX_STEPS else [])


def mode(tm) -> str:
    """grant 부여일 평가 · mod 조건 변경 · cash 현금결제형 재측정."""
    if int(getattr(tm, "so_settle", 0)) == 1:
        return "cash"
    return "mod" if int(getattr(tm, "so_mod", 0)) == 1 else "grant"


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and math.isnan(v))


def mod_row(row: dict, m: dict, tm) -> dict:
    """변경 후 회차 — 빈칸은 변경 전과 같다.

    행사 시작일 빈칸: 변경 전에 가득일부터 행사했으면 변경 후 가득일부터, 따로 정했으면 그날과 변경 후 가득일 중 늦은 날.
    """
    m = m or {}
    vest = row["vest"] if _blank(m.get("vest")) else str(m["vest"])
    if not _blank(m.get("start")):
        start = str(m["start"])
    else:
        start = vest if str(row["start"]) == str(row["vest"]) else max(str(row["start"]), vest)
    return dict(name=row["name"], vest=vest, start=start,
                end=row["end"] if _blank(m.get("end")) else str(m["end"]),
                qty=float(row["qty"]) if _blank(m.get("qty")) else float(m["qty"]), keep=row["keep"],
                K=float(tm.K0) if _blank(m.get("K")) else float(m["K"]), target=row.get("target"))


def mod_map(tm) -> dict:
    return {str(m.get("name", "")).strip(): m for m in (tm.so_mod_rows or []) if isinstance(m, dict)}


def _date(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


def row_defaults(row: dict, tm=None) -> dict:
    out = {"name": "", "vest": "", "start": "", "end": "", "qty": 0.0, "keep": 1.0, "target": None}
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
    if int(tm.so_settle) not in SETTLE_LABELS:
        out.append("결제 방식을 고르십시오.")
    if int(tm.so_settle) == 1 and int(tm.so_mod) == 1:
        out.append("현금결제형은 결산일마다 공정가치로 다시 재므로 조건 변경 평가를 따로 하지 않습니다. "
                   "변경된 조건으로 결산일 평가를 하십시오.")
    if not (math.isfinite(float(tm.so_paid)) and tm.so_paid >= 0):
        out.append("당기 현금 결제액은 0 이상이어야 합니다.")
    if int(tm.so_settle) != 1 and tm.so_paid > 0:
        out.append("당기 현금 결제액은 현금결제형에서만 넣습니다.")
    if int(tm.so_ex) not in EX_LABELS:
        out.append("조기행사 가정을 고르십시오.")
    if int(getattr(tm, "so_mkt", 0)) not in MKT_LABELS:
        out.append("시장조건 판정 방식을 고르십시오.")
    nv = getattr(tm, "so_nv", 0.0)
    if not (isinstance(nv, (int, float)) and math.isfinite(nv) and 0 <= nv < 1):
        out.append("비가득조건 연간 중단율은 0% 이상 100% 미만이어야 합니다.")
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
        out += _too_long(tm, label, end, db)
        cash = int(tm.so_settle) == 1           # 현금결제형은 결산일에 남은 수량 — 모두 결제했으면 0
        if not (isinstance(r["qty"], (int, float)) and math.isfinite(r["qty"]) and (r["qty"] >= 0 if cash else r["qty"] > 0)):
            out.append(f"{label}: 남은 수량은 0 이상이어야 합니다." if cash else f"{label}: 부여 수량은 0보다 커야 합니다.")
        if not (isinstance(r["keep"], (int, float)) and math.isfinite(r["keep"]) and 0 < r["keep"] <= 1):
            out.append(f"{label}: 가득 예상 비율은 0%보다 크고 100% 이하여야 합니다.")
        out += _target_issues(tm, label, r, vest, db)
    if tm.so_exit > 0:
        late = [str(r["name"]) for r in (row_defaults(x, tm) for x in rs) if str(r["start"]) > str(r["vest"])]
        if late:
            out.append("가득 뒤 퇴사율을 넣었는데 행사 시작일이 가득일보다 늦은 회차가 있습니다(" + ", ".join(late) +
                       "). 그 사이에 퇴사하면 선택권이 남는지 소멸하는지 앱이 정할 수 없습니다. 퇴사율을 0%로 두고 "
                       "가득 예상 비율에 반영하거나, 계약상 그 기간 퇴사자가 바로 행사할 수 있으면 행사 시작일을 가득일과 같게 넣으십시오.")
    dup = sorted({n for n in names if n and names.count(n) > 1})
    if dup:
        out.append("회차 이름이 겹칩니다: " + ", ".join(dup))
    if int(getattr(tm, "so_mkt", 0)) in (1, 2) and not any(target(row_defaults(x, tm)) for x in rs):
        out.append("«시장조건 판정» 을 골랐는데 목표 주가를 넣은 회차가 없습니다 — 회차표의 «목표 주가(원)» 에 넣으십시오.")
    if int(tm.so_mod) == 1 and int(tm.so_settle) == 0:
        out += mod_issues(tm, rs, di, db, dm)
    return out


def target(row):
    """회차의 시장조건 목표 주가 — 비었으면 None (그 회차에는 시장조건이 없다)."""
    v = row.get("target")
    if _blank(v) or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        return None                      # 잘못된 값은 입력 검사(_target_issues)가 따로 알린다
    return float(v)


def _target_issues(tm, label, r, vest, db) -> list[str]:
    v = r.get("target")
    if _blank(v):
        return []
    if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0):
        return [f"{label}: 목표 주가는 0보다 커야 합니다."]
    if int(getattr(tm, "so_mkt", 0)) == 0:
        return [f"{label}: 목표 주가를 넣었는데 «시장조건 판정» 이 «없음» 입니다 — 판정 방식을 고르거나 목표 주가를 지우십시오."]
    if vest <= db:
        return [f"{label}: 가득일이 측정기준일 이전이라 시장조건이 이미 판정됐습니다 — 목표 주가를 지우십시오."]
    return []


def mod_issues(tm, rs, di, db, dm) -> list[str]:
    """조건 변경표 검사 — 회차마다 한 줄, 부여일 공정가치, 변경 후 날짜·수량·행사가격."""
    out = []
    if not db > di:
        out.append("조건 변경 평가는 측정기준일(조건 변경일)이 부여일보다 뒤여야 합니다.")
    ms = tm.so_mod_rows or []
    names = [str(m.get("name", "")).strip() for m in ms if isinstance(m, dict)]
    want = [str(row_defaults(r, tm)["name"]).strip() for r in rs]
    missing = [n for n in want if n not in names]
    extra = sorted({n for n in names if n not in want})
    dup = sorted({n for n in names if names.count(n) > 1})
    if missing:
        out.append("조건 변경표에 없는 회차가 있습니다: " + ", ".join(missing) +
                   " — 바뀌지 않은 회차도 한 줄 넣고 부여일 1주당 공정가치를 적으십시오.")
    if extra:
        out.append("조건 변경표의 회차 이름이 회차표에 없습니다: " + ", ".join(extra))
    if dup:
        out.append("조건 변경표의 회차 이름이 겹칩니다: " + ", ".join(dup))
    mm = mod_map(tm)
    for raw in rs:
        r = row_defaults(raw, tm)
        m = mm.get(str(r["name"]).strip())
        if m is None:
            continue
        label = str(r["name"])
        g = m.get("grant_fv")
        if not (isinstance(g, (int, float)) and not isinstance(g, bool) and math.isfinite(g) and g >= 0):
            out.append(f"{label}: 부여일 1주당 공정가치를 넣으십시오 (부여일 평가 결과 — 당초 보상원가 배분에 씁니다).")
        try:
            new = mod_row(r, m, tm)
            vest, start, end = _date(new["vest"]), _date(new["start"]), _date(new["end"])
        except (ValueError, TypeError):
            out.append(f"{label}: 변경 후 가득일·행사 시작일·행사 종료일은 YYYY-MM-DD 로 넣으십시오.")
            continue
        if not (math.isfinite(new["K"]) and new["K"] > 0):
            out.append(f"{label}: 변경 후 행사가격은 0보다 커야 합니다.")
        if new["qty"] < float(r["qty"]) - 1e-9:
            out.append(f"{label}: 변경 후 수량이 줄었습니다. 수량 감소는 일부 취소로 따로 회계처리합니다 (문단 28) — "
                       "이 표에서는 늘거나 같은 수량만 받습니다.")
        if _date(str(r["end"])) <= db:
            out.append(f"{label}: 변경 전 행사 종료일이 조건 변경일 이전이라 변경할 선택권이 없습니다.")
        if not _blank(m.get("vest")) and vest < db and str(m["vest"]) != str(r["vest"]):
            out.append(f"{label}: 변경 후 가득일이 조건 변경일보다 빠릅니다 — 즉시 가득으로 바꿨으면 조건 변경일을 넣으십시오.")
        if start < vest:
            out.append(f"{label}: 변경 후 행사 시작일이 가득일보다 빠릅니다.")
        if not start <= end:
            out.append(f"{label}: 변경 후 행사 종료일이 행사 시작일보다 빠릅니다.")
        if end <= db:
            out.append(f"{label}: 변경 후 행사 종료일이 조건 변경일 이전입니다.")
        out += _too_long(tm, label + " (변경 후)", end, db)    # 연장한 종료일은 행사기간 종료일 한도 검사를 거치지 않는다
        # 변경 후 행사 종료일은 부여 조건의 행사기간 종료일보다 늦어도 된다 — 행사기간 종료일을 늘리면
        # 빈칸으로 둔 변경 전 행사 종료일까지 늘어나 연장의 증분이 사라진다 (자동 검토 지적)
        if tm.so_exit > 0 and start > vest:
            out.append(f"{label}: 가득 뒤 퇴사율을 넣었는데 변경 후 행사 시작일이 가득일보다 늦습니다 — 퇴사율을 0%로 두십시오.")
        if target(new) and vest <= db and int(getattr(tm, "so_mkt", 0)):
            out.append(f"{label}: 변경 후 가득일이 조건 변경일 이전이라 시장조건이 이미 판정됐습니다 — 목표 주가를 지우십시오.")
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
    """한 회차를 격자에서 잰다. 돌려주는 값은 1주당(원). 행사가격은 row["K"] (없으면 K0)."""
    from .legacy import forward_rate, lattice_ud
    db = _date(tm.d_base)
    t_ex = max(0.0, (_date(row["start"]) - db).days/365)      # 행사 시작
    t_end = (_date(row["end"]) - db).days/365
    n = steps_for(tm, t_end)
    dt_ = t_end/n
    R = R or rate_fn(tm, t_end)
    u, d = lattice_ud(tm, dt_)
    q = float(tm.div_y)
    K, S0 = float(row.get("K") or tm.K0), float(tm.S0)
    # 행사 시작일 이후 첫 노드부터 행사할 수 있다 (노드 사이면 다음 노드).
    ex_step = min(n, max(0, int(math.ceil(t_ex/dt_ - 1e-9))))
    # 가득 시점 — 시장조건 판정과 비가득조건이 끝나는 노드 (가득일 이후 첫 노드, 행사 시작 노드보다 늦지 않음)
    t_vest = max(0.0, (_date(row["vest"]) - db).days/365)
    vest_step = min(ex_step, max(0, int(math.ceil(t_vest/dt_ - 1e-9))))
    H = target(row)
    mkt = int(getattr(tm, "so_mkt", 0)) if H else 0
    nv = float(getattr(tm, "so_nv", 0.0))
    nv_step = 1.0 - (1.0 - nv)**dt_ if nv > 0 else 0.0     # 한 구간에 비가득조건을 그만둘 확률 (가득 전)
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
    exercise_life = [None]*(n + 1)             # 기대존속기간용 — 가득된다고 보고 잰다 (시장조건 소멸 전)
    go = S - K > 0
    exercise_life[n] = go.copy()
    if mkt == 1 and vest_step == n:
        w = pass_share(S, u, H)
        V, go = w*V, go & (w >= 1)
    values[n], prices[n], exercise[n] = V.copy(), S.copy(), go
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
            if i < vest_step and nv_step:
                V = (1 - nv_step)*V                # 가득 전 비가득조건 — 그만두면 소멸 (문단 21A)
        exercise_life[i] = go.copy()
        if mkt == 1 and i == vest_step:
            w = pass_share(S, u, H)                # 가득일 주가가 목표 미만이면 소멸 (문단 21), 걸친 노드는 비율만큼
            V, go = w*V, go & (w >= 1)
        values[i], prices[i], exercise[i] = V.copy(), S.copy(), go
    fv = float(V[0])
    wait = None
    if mkt == 2:
        # 아직 목표 주가에 닿지 않은 상태의 가치 — 가득일에 닿지 못했으면 소멸, 닿은 노드에서는 보통 격자 가치
        k = vest_step
        Sk = S0*u**(k - 2*np.arange(k + 1))
        Wv = np.where(Sk >= H*(1 - MKT_TOL), values[k], 0.0)
        wait = [None]*(k + 1)
        wait[k] = Wv.copy()
        for i in range(k - 1, -1, -1):
            Si = S0*u**(i - 2*np.arange(i + 1))
            Wv = disc[i]*(p[i]*Wv[:-1] + (1 - p[i])*Wv[1:])
            if nv_step:
                Wv = (1 - nv_step)*Wv
            Wv = np.where(Si >= H*(1 - MKT_TOL), values[i], Wv)
            wait[i] = Wv.copy()
        fv = float(Wv[0])
    # 기대존속기간 — 위험중립확률로 «행사·퇴사·만기» 중 먼저 오는 때를 평균한다 (가득된다고 보고, 문단 B18⑴).
    mass, life = np.array([1.0]), 0.0
    for i in range(n):
        t = i*dt_
        go = exercise_life[i]
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
        keep=float(row["keep"]), K=K, t_ex=t_ex, t_end=t_end, n=n, dt=dt_, ex_step=ex_step, u=u, d=d,
        vest_step=vest_step, target=H, mkt=mkt, nv_step=nv_step,
        vest_exercise=exercise_life[vest_step].tolist() if mkt == 1 else None,   # 비율을 곱하기 전 행사 여부
        wait=None if wait is None else [w.tolist() for w in wait],
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


def service_share(tm, vest: str) -> float:
    """측정기준일까지 지난 근무 비율 — (측정기준일 − 부여일) ÷ (가득일 − 부여일), 1을 넘지 않음."""
    di, db, v = _date(tm.d_issue), _date(tm.d_base), _date(vest)
    span = (v - di).days
    return 1.0 if span <= 0 else min(1.0, max(0.0, (db - di).days/span))


def value(tm) -> dict:
    """회차마다 평가하고, 합계·가중평균·비용 배분·점검을 붙인다."""
    rs = rows(tm)
    md = mode(tm)
    mm = mod_map(tm) if md == "mod" else {}
    news = [mod_row(r, mm.get(str(r["name"]).strip()), tm) for r in rs] if md == "mod" else rs
    horizon = max((_date(r["end"]) - _date(tm.d_base)).days/365 for r in rs + news)
    R = rate_fn(tm, horizon)
    out_rows, before_rows = [], []
    for r, new in zip(rs, news):
        if md == "mod":
            b = value_row(tm, r, R)                   # 변경 전 조건 — 조건 변경일의 시장자료
            v = value_row(tm, new, R)                 # 변경 후 조건 — 같은 날
            g = float(mm[str(r["name"]).strip()]["grant_fv"])
            old_q, add_q = float(r["qty"]), max(0.0, float(new["qty"]) - float(r["qty"]))
            incr = max(0.0, v["fv"] - b["fv"])        # 줄면 더 인식하지 않는다 (문단 27, 부록 나44)
            v.update(fv_before=b["fv"], incr=incr, grant_fv=g, qty_before=old_q, qty_added=add_q,
                     vest_before=r["vest"], start_before=r["start"], end_before=r["end"], K_before=b["K"],
                     grant_cost=g*old_q*v["keep"],
                     fair=incr*old_q + v["fv"]*add_q,
                     cost=(incr*old_q + v["fv"]*add_q)*v["keep"])
            b["name"] = r["name"]
            before_rows.append(b)
        else:
            v = value_row(tm, r, R)
            v["fair"] = v["fv"]*v["qty"]              # 부여 수량 전체의 공정가치
            v["cost"] = v["fv"]*v["qty"]*v["keep"]    # 보상원가 (가득 예상 수량)
            if md == "cash":
                v["service"] = service_share(tm, r["vest"])
                v["liab"] = v["cost"]*v["service"]    # 결산일 부채 = 공정가치 × 가득 예상 수량 × 근무 경과율
        out_rows.append(v)
    qty = sum(r.get("qty_before", r["qty"]) for r in out_rows)
    fair = sum(r["fair"] for r in out_rows)
    cost = sum(r["cost"] for r in out_rows)
    raw = dict(mode=md, rows=out_rows, qty=qty, fair=fair, cost=cost, fv_avg=fair/qty if qty else 0.0,
               qbad=any(r["qbad"] for r in out_rows + before_rows))
    if md == "mod":
        raw["before_rows"] = before_rows
        raw["grant_cost"] = sum(r["grant_cost"] for r in out_rows)
        raw["schedule"] = schedule_mod(tm, out_rows)
    elif md == "cash":
        raw["liab"] = sum(r["liab"] for r in out_rows)
        raw["prev_liab"] = max(0.0, float(tm.so_prev_liab))
        raw["paid"] = float(tm.so_paid)
        # 부채 증감 = 비용 − 결제 → 비용 = 당기말 부채 − 전기말 부채 + 당기 결제액 (문단 30)
        raw["expense"] = raw["liab"] - raw["prev_liab"] + raw["paid"]
        raw["schedule"] = [dict(fy_end=tm.d_base, name=r["name"], expense=r["liab"], cum=r["liab"], share=r["service"],
                                part="부채") for r in out_rows]
    else:
        raw["schedule"] = schedule(tm, out_rows)
    raw["integrity"] = integrity(tm, raw)
    return raw


def schedule_mod(tm, rows_out: list[dict]) -> list[dict]:
    """조건 변경 — 당초 보상원가와 증분 보상원가를 따로 배분한다 (부록 나43).

    당초: 부여일부터 가득일까지. 가득기간을 줄였으면 줄인 가득일까지(나43⑶), 늘렸으면 당초 가득일 그대로(나44⑶).
          조건 변경일 전 결산일은 그때 추정(당초 가득일)대로 두고, 그 뒤 결산일에 누적액을 맞춘다 (문단 20).
    증분: 조건 변경일부터 변경 후 가득일까지. 이미 가득됐으면 변경일이 속한 결산일에 한꺼번에.
    """
    di, db = _date(tm.d_issue), _date(tm.d_base)
    last = max(max(_date(r["vest"]), _date(r["vest_before"])) for r in rows_out)
    ends = fiscal_ends(di, max(last, db), str(tm.so_fy_end))
    out = []

    def frac(e, start, vest):
        span = (vest - start).days
        return 1.0 if span <= 0 else min(1.0, max(0.0, (e - start).days/span))

    def spread(name, part, total, start, vest, vest_past=None):
        prev = 0.0
        for e in ends:
            if e < start:
                continue                 # 배분을 시작하기 전의 결산일
            share = frac(e, start, vest_past if vest_past and e < db else vest)
            cum = total*share
            out.append(dict(fy_end=e.isoformat(), name=name, expense=cum - prev, cum=cum, share=share, part=part))
            prev = cum
            if share >= 1.0:
                break                    # 당초 가득일 ≥ 변경 후 적용 가득일이라 뒤 결산일도 100%
    for r in rows_out:
        v_old, v_new = _date(r["vest_before"]), _date(r["vest"])
        spread(r["name"], "당초", r["grant_cost"], di, min(v_old, v_new), vest_past=v_old)
        # 증분이 0이어도 줄을 만든다 — 수식 조서에서 주가 등을 바꿔 증분이 생기면 이 줄이 받는다
        spread(r["name"], "증분", r["cost"], db, v_new)
    return out


def integrity(tm, raw) -> list[str]:
    """계산이 고장 나지 않았는지 — 걸리면 조서를 만들지 않는다 (service.export_bundle)."""
    bad = []
    S0 = float(tm.S0)
    for r in raw["rows"]:
        if not (0 <= r["fv"] <= S0*(1 + 1e-9)) or not math.isfinite(r["fv"]):
            bad.append(f"{r['name']} 공정가치 범위(0 ~ 주가)")
        if int(tm.so_ex) == 0 and tm.so_exit == 0 and not conditioned(r) and r["bs_end"] > 1e-6*S0:
            if abs(r["fv"] - r["bs_end"]) > BS_TOL*r["bs_end"] + 1e-9*S0:
                bad.append(f"{r['name']} 유럽형 격자값과 블랙-숄즈 값의 수렴")
    for b in raw.get("before_rows", []):
        if not (0 <= b["fv"] <= S0*(1 + 1e-9)) or not math.isfinite(b["fv"]):
            bad.append(f"{b['name']} 변경 전 공정가치 범위(0 ~ 주가)")
    total, want = sched_total(raw)
    if abs(total - want) > 1e-6*max(1.0, want):
        bad.append("비용 배분 합계와 총 보상원가")
    return bad


def conditioned(r) -> bool:
    """시장조건·비가득조건이 들어간 회차 — 조건 없는 블랙-숄즈 값과 같을 까닭이 없다."""
    return bool(r.get("mkt")) or bool(r.get("nv_step"))


def sched_total(raw):
    """(배분 합계, 맞아야 할 금액) — 부여일: 보상원가 · 조건 변경: 당초 + 증분 · 현금결제형: 결산일 부채."""
    total = sum(x["expense"] for x in raw["schedule"])
    want = {"mod": raw.get("grant_cost", 0.0) + raw["cost"], "cash": raw.get("liab", 0.0)}.get(raw.get("mode"), raw["cost"])
    return total, want


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
             passed=abs(sched_total(raw)[0] - sched_total(raw)[1]) <= 1e-6*max(1.0, sched_total(raw)[1]),
             detail={"mod": "회계연도별 비용의 합 = 당초 보상원가 + 증분 보상원가",
                     "cash": "회차별 부채의 합 = 결산일 주식보상부채"}.get(raw.get("mode"), "회계연도별 비용의 합 = 총 보상원가")),
    ]
    if raw.get("mode") == "mod":
        out.append(dict(name="증분 공정가치 (음수 없음)", passed=all(r["incr"] >= 0 for r in rows_),
                        detail="증분 = 큰 값(변경 후 − 변경 전, 0) — 줄어든 공정가치는 인식하지 않음 (문단 27)"))
    if any(conditioned(r) for r in rows_):
        out.append(dict(name="시장조건·비가득조건 (공정가치 ≤ 조건 없는 블랙-숄즈)",
                        passed=all(r["fv"] <= r["bs_end"]*(1 + BS_TOL) + 1e-9*S0 or int(tm.so_ex) != 0 for r in rows_),
                        detail="조건이 있으면 같은 선택권의 조건 없는 값보다 작아야 함 — 블랙-숄즈 수렴 점검은 하지 않음"))
    elif int(tm.so_ex) == 0 and tm.so_exit == 0:
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
    K, dt_ = float(row.get("K") or tm.K0), row["dt"]
    S = row["prices"][i][j]
    out = dict(step=i, pos=j, time=i*dt_, date=(_date(tm.d_base) + dt.timedelta(days=round(i*dt_*365))).isoformat(),
               stock=S, strike=K, intrinsic=S - K, value=row["values"][i][j], exercise=bool(row["exercise"][i][j]),
               vested=i >= row["ex_step"], terminal=i == n)
    if i == n:
        out.update(rule="행사 종료일 — 내가격이면 행사, 아니면 소멸: max(주가 − 행사가격, 0)")
        if row.get("mkt") == 1 and row.get("vest_step") == n:
            share = float(pass_share(np.array([S]), row["u"], row["target"])[0])
            out.update(pass_share=share, forfeit=share < 1.0, reconciliation=out["value"] - share*max(S - K, 0.0))
            if share < 1.0:
                out["rule"] += f" × 목표 주가 이상인 비율 {share:.4f} (가득일 시장조건 판정)"
        return out
    p, f = row["p"][i], row["fwd"][i]
    up, dn = row["values"][i + 1][j], row["values"][i + 1][j + 1]
    disc = math.exp(-f*dt_)
    cont = disc*(p*up + (1 - p)*dn)
    e = 1.0 - (1.0 - float(tm.so_exit))**dt_ if out["vested"] else 0.0
    nv = row.get("nv_step", 0.0) if i < row.get("vest_step", 0) else 0.0
    hold = (1 - e)*(1 - nv)*cont + e*max(S - K, 0.0)
    judged = row.get("mkt") == 1 and i == row.get("vest_step")
    share = float(pass_share(np.array([S]), row["u"], row["target"])[0]) if judged else 1.0
    forfeit = share < 1.0
    went = row["vest_exercise"][j] if judged else out["exercise"]
    raw_v = S - K if went else hold                    # 시장조건 비율을 곱하기 전 가치
    want = share*raw_v
    out.update(up=up, down=dn, up_stock=row["prices"][i + 1][j], down_stock=row["prices"][i + 1][j + 1],
               p=p, fwd=f, disc=disc, cont=cont, exit_step=e, nv_step=nv, hold=hold, forfeit=forfeit,
               reconciliation=out["value"] - want)
    if row.get("wait") is not None and i <= row["vest_step"]:
        # «아직 목표 주가에 닿지 않은 상태» 의 가치 — 닿았으면 보통 격자 가치, 가득일까지 못 닿으면 0
        w = row["wait"][i][j]
        if S >= row["target"]*(1 - MKT_TOL):
            w_want, w_rule = out["value"], "목표 주가 이상 — 보통 격자 가치로 바뀜"
        elif i == row["vest_step"]:
            w_want, w_rule = 0.0, "가득일까지 목표 주가에 닿지 못해 소멸"
        else:
            wu, wd = row["wait"][i + 1][j], row["wait"][i + 1][j + 1]
            w_want = (1 - nv)*disc*(p*wu + (1 - p)*wd)
            w_rule = "아직 목표 주가 미만 — 다음 시점의 «닿지 않은 상태» 가치를 할인"
            out.update(wait_up=wu, wait_down=wd)
        out.update(wait=w, wait_rule=w_rule, wait_reconciliation=w - w_want)
    out.update(pass_share=share, before_share=raw_v)
    if forfeit and share == 0:
        out["rule"] = f"가득일 주가가 목표 주가 {row['target']:,.0f}원 미만 — 시장조건을 못 채워 소멸 (가치 0)"
    elif forfeit:
        out["rule"] = (f"목표 주가 {row['target']:,.0f}원이 이 노드가 대표하는 주가 구간에 걸쳐 있어 "
                       f"구간 가운데 목표 이상인 비율 {share:.4f}만큼만 가치를 남김")
    elif not out["vested"]:
        out["rule"] = ("행사 시작 전 — 행사할 수 없어 계속 보유 가치"
                       + (" × (1 − 비가득조건 중단 확률)" if nv else ""))
    elif out["exercise"]:
        out["rule"] = {1: "지금 행사 가치가 계속 보유 가치보다 커서 행사",
                       2: f"주가가 행사가격의 {float(tm.so_mult):g}배 이상이라 행사"}.get(int(tm.so_ex), "행사")
    else:
        out["rule"] = "계속 보유" + (" (퇴사하면 그때 내가격분만 받음)" if e > 0 else "")
    return out


# ── 결과 요약 — 화면·조서·결과 기록이 같은 표를 쓴다 ─────────────────────
AMOUNT_KEYS = {"grant": ("so_fair", "so_cost"), "mod": ("so_incr_fair", "so_incr_cost"), "cash": ("so_fair", "so_liab")}


def amounts(tm, raw):
    """(총액, 1주당, 주가 100 기준) — 키는 평가 종류마다 다르다 (AMOUNT_KEYS)."""
    k1, k2 = AMOUNT_KEYS[raw["mode"]]
    total = {k1: float(raw["fair"]), k2: float(raw["liab"] if raw["mode"] == "cash" else raw["cost"])}
    qty, S0 = float(raw["qty"]), float(tm.S0)
    share = {k: v/qty if qty else 0.0 for k, v in total.items()}     # 현금결제형에서 모두 결제했으면 수량 0
    return total, share, {k: v/S0*100 for k, v in share.items()}


def summary_rows(tm, raw) -> list[dict]:
    md = raw["mode"]
    if md == "mod":
        return [{"회차": x["name"], "변경 전 행사가격": x["K_before"], "변경 후 행사가격": x["K"],
                 "변경 전 가득일": x["vest_before"], "변경 후 가득일": x["vest"],
                 "변경 전 행사 종료일": x["end_before"], "변경 후 행사 종료일": x["end"],
                 "변경 전 수량": x["qty_before"], "늘어난 수량": x["qty_added"], "가득 예상 비율": x["keep"],
                 "부여일 1주당 공정가치": x["grant_fv"], "변경 전 1주당 (변경일)": x["fv_before"],
                 "변경 후 1주당 (변경일)": x["fv"], "1주당 증분": x["incr"],
                 "당초 보상원가": x["grant_cost"], "증분 공정가치": x["fair"], "증분 보상원가": x["cost"]}
                for x in raw["rows"]]
    base = [{"회차": x["name"], "가득일": x["vest"], "행사 시작일": x["start"], "행사 종료일": x["end"],
             "부여 수량": x["qty"], "가득 예상 비율": x["keep"], **({"시장조건 목표 주가": x.get("target")}
                                                             if any(r.get("target") for r in raw["rows"]) else {}),
             "구간 수": x["n"],
             "1주당 공정가치": x["fv"], "블랙-숄즈(행사 종료일)": x["bs_end"],
             "기대존속기간(년)": x["life"], "공정가치": x["fair"], "보상원가": x["cost"]} for x in raw["rows"]]
    if md == "cash":
        for b, x in zip(base, raw["rows"]):
            b.pop("보상원가")
            b.update({"가득 예상 수량의 공정가치": x["cost"], "근무 경과율": x["service"], "주식보상부채": x["liab"]})
    return base


def summary_schedule(tm, raw) -> list[dict]:
    md = raw["mode"]
    if md == "cash":
        return [{"결산일": x["fy_end"], "회차": x["name"], "근무 경과율": x["share"], "주식보상부채": x["cum"]}
                for x in raw["schedule"]]
    out = []
    for x in raw["schedule"]:
        row = {"결산일": x["fy_end"], "회차": x["name"]}
        if md == "mod":
            row["구분"] = x["part"]
        row.update({"누적 배분율": x["share"], "당기 비용": x["expense"], "누적 비용": x["cum"]})
        out.append(row)
    return out
