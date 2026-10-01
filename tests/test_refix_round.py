"""조정 후 전환가격의 원 단위 미만 처리 (계약서 문구) — 가상 수치.

정기 조정일에는 그날 주가로 새 전환가격을 정하고 원 단위 미만을 절상(또는 절사)한 뒤 하한·상한을 건다.
이월값(경로 가중 평균)은 건드리지 않는다. 처리 없음(기본)이면 종전과 같은 값이다.
"""
import math
from valuation import legacy


def terms(**over):
    t = legacy.Terms(rf_curve=[(1, .0226), (3, .0240), (5, .0252)], cr_curve=[(1, .1409), (3, .1740), (5, .1905)],
                     carry=1, gap_m=6.0, **over)
    legacy.derive(t)
    return t


def kgrid(t):
    full = legacy.engine(t, call=False)
    return {k[:2]: v["K"] for k, v in full["memo"].items()}, full


def test_default_unchanged():
    a = legacy.decompose(terms())[1:5]
    b = legacy.decompose(terms(rfx_round=0))[1:5]
    assert a == b


def test_round_up_at_refix_nodes():
    t = terms(rfx_round=1)
    n, dt_ = t.n, t.T/t.n
    rfx = set(legacy.refix_steps(t, n, dt_))
    K, _ = kgrid(t)
    u = math.exp(t.sig*math.sqrt(dt_)); d = 1/u
    seen = 0
    for (i, j), k in K.items():
        if i in rfx and i > 0:
            s = t.S0*u**j*d**(i-j)
            want = min(max(math.ceil(s - 1e-9), t.floor, t.par), legacy.k_cap(t))
            assert abs(k - want) < 1e-9, (i, j, k, want)
            seen += 1
    assert seen > 0


def test_round_moves_value_and_down_is_lower():
    v0 = legacy.decompose(terms())[3]
    vu = legacy.decompose(terms(rfx_round=1))[3]
    vd = legacy.decompose(terms(rfx_round=2))[3]
    # 절상하면 전환가격이 오르니 전환권 가치가 줄고, 절사하면 늘어난다
    assert vu < v0 < vd


def test_k_round_boundary():
    t = terms(rfx_round=1)
    assert legacy.k_round(t, 1447.0000000001) == 1447.0
    assert legacy.k_round(t, 1446.8320) == 1447.0
    t.rfx_round = 2
    assert legacy.k_round(t, 1446.9999999999) == 1447.0
    assert legacy.k_round(t, 1446.8320) == 1446.0
