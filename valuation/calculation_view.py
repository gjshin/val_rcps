"""Read-only, lossless data for the in-app calculation worksheets."""

import datetime as dt
import json
import math
from pathlib import Path
from . import legacy as L
from .explain import KINDS


def _date(t, step, delta):
    return (dt.date.fromisoformat(t.d_base) + dt.timedelta(days=round(step * delta * 365))).isoformat()


def _finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def node_values(run, start=0, end=None):
    """Use the saved tree, including its actual child keys; never price again."""
    t, tree = run.terms, run.raw['full']
    end = t.n if end is None else min(end, t.n)
    delta = tree['dt']
    pays = L.pay_steps(t, t.n, delta)
    steps = []
    for i in range(t.n + 1):
        s = dict(
            i=i,
            date=_date(t, i, delta),
            coupon=100 * L.eff_cpn(t) * t.ipay / 12 * pays.get(i, 0),
            cv=bool(tree['can_convert'](i)),
            put=bool(tree['can_put'](i)),
            call=bool(tree['can_call'](i)),
        )
        if i < t.n:
            q, rf, cr = tree['qi'](i), tree['fwdRF'](i), tree['fwdCR'](i)
            s.update(q=q, rf=rf, cr=cr, df_rf=math.exp(-rf * delta), df_cr=math.exp(-cr * delta))
        steps.append(s)
    nodes = {}
    for (i, j), o in tree['memo'].items():
        # Include the next column so the final visible column's references remain inspectable.
        if not start <= i <= end + 1:
            continue
        s = steps[i]
        terminal = 'up' not in o
        kind = o.get('kind', '')
        # GS 의 처리는 엔진이 고른 값(gkind)을 그대로 쓴다 — 화면에서 따로 다시 판정하지 않는다. TF 의 kind 와 다를 수 있다.
        gs_kind = o.get('gkind', kind)
        if t.model == 'GS':
            kind = gs_kind
        r = dict(
            i=i,
            j=j,
            down=i - j,
            stock=tree['S'](i, j),
            K=o.get('K'),
            value=o['V'] if t.model == 'GS' else o['E'] + o['B'],
            E=o['E'],
            B=o['B'],
            P=o['P'],
            kind=KINDS.get(kind, kind),
            probability_kind=KINDS.get(gs_kind, gs_kind),
            terminal=terminal,
            cv=_finite(o.get('cv')) if s['cv'] or kind in ('auto', 'ipo') else None,
            pv=_finite(o.get('pv')) if s['put'] else None,
            kv=_finite(o.get('kv')) if s['call'] else None,
            wv=_finite(o.get('wv')),
            wx=o.get('wx'),
            hold=None,
            equity_hold=None,
            debt_hold=None,
            up=None,
            dn=None,
        )
        if not terminal:
            a, b = tree['memo'][o['up']], tree['memo'][o['dn']]
            eh = (s['q'] * a['E'] + (1 - s['q']) * b['E']) * s['df_rf']
            bh = (s['q'] * a['B'] + (1 - s['q']) * b['B']) * s['df_cr'] + s['coupon']
            ya = a['P'] * s['rf'] + (1 - a['P']) * s['cr']
            yb = b['P'] * s['rf'] + (1 - b['P']) * s['cr']
            r.update(
                hold=o['Vc'] if t.model == 'GS' else o['hold'],
                equity_hold=eh,
                debt_hold=bh,
                ya=ya,
                yb=yb,
                df_up=math.exp(-ya * delta),
                df_down=math.exp(-yb * delta),
                up=list(o['up']),
                dn=list(o['dn']),
            )
        nodes[f'{i},{i-j}'] = r
    return dict(
        mode='nodes',
        model=t.model,
        n=t.n,
        start=start,
        end=end,
        steps=steps,
        nodes=nodes,
        delta=delta,
        u=tree['u'],
        d=tree['d'],
        S0=t.S0,
        sig=t.sig,
        div_y=t.div_y,
        bw_cash=L.bw_cash(t),
        bw_detach=bool(t.bw_detach),
        pc_order=t.pc_order,
        conv_resp=L.conv_resp(t),
        run_id=run.summary['run_id'],
    )


def schedule_values(run):
    t, tree = run.terms, run.raw['full']
    delta = tree['dt']
    ea = L.exercise_amounts(t, t.n, delta)
    pays = L.pay_steps(t, t.n, delta)
    refix = L.refix_steps(t, t.n, delta)
    coupons = [100 * L.eff_cpn(t) * t.ipay / 12 * pays.get(i, 0) for i in range(t.n + 1)]
    host = [0.0] * (t.n + 1)
    host[-1] = ea['red_val'] + coupons[-1]
    for i in range(t.n - 1, -1, -1):
        host[i] = host[i + 1] * math.exp(-tree['fwdCR'](i) * delta) + coupons[i]
    rows = []
    for i in range(t.n + 1):
        r = dict(
            i=i,
            date=_date(t, i, delta),
            time=i * delta,
            cv=bool(tree['can_convert'](i)),
            put=bool(tree['can_put'](i)),
            call=bool(t.k_w > 0 and ea['k_on'](i)) and i < t.n,
            refix=i in refix,
            put_contract=ea['put'](i) if ea['p_on'](i) else None,
            put_value=ea['put_val'](i) if ea['p_on'](i) else None,
            call_contract=ea['call'](i) if t.k_w > 0 and i in ea['k_dates'] else None,
            coupon=100 * L.eff_cpn(t) * t.ipay / 12 * pays.get(i, 0),
            pays=pays.get(i, 0),
            host=host[i],
            redemption=ea['red_val'] if i == t.n else None,
            ipo=bool(t.ipo_on and i == tree['st_lo'](t.ipo_m) and t.ipo_px > 0 and 0 < i <= t.n),
            rf=None,
            cr=None,
            q=None,
            df_rf=None,
            df_cr=None,
        )
        if i < t.n:
            r.update(rf=tree['fwdRF'](i), cr=tree['fwdCR'](i), q=tree['qi'](i))
            r.update(df_rf=math.exp(-r['rf'] * delta), df_cr=math.exp(-r['cr'] * delta))
        rows.append(r)
    return dict(
        mode='schedule',
        model=t.model,
        n=t.n,
        steps=rows,
        delta=delta,
        u=tree['u'],
        d=tree['d'],
        sig=t.sig,
        div_y=t.div_y,
        cpn=L.eff_cpn(t),
        ipay=t.ipay,
        run_id=run.summary['run_id'],
    )


def bootstrap_values(run, *, horizon=None, risk_free_only=False):
    """Explain the same curve builder, including stub and short first maturities.

    주식선택권은 무위험 곡선만 쓰고, 가장 긴 회차의 행사 종료일까지 푼다 (stock_option.rate_fn 과 같은 만기).
    """
    t = run.terms
    Tmax = t.T if horizon is None else horizon
    result = []
    curves = [('무위험금리 Rf', t.rf_curve, t.cmp_rf)] + ([] if risk_free_only else [('위험금리 Kd', L.credit_curve(t), t.cmp_cr)])
    for name, pts, m in curves:
        rows = []
        if t.y_type == 'spot':
            for term, rate in pts:
                spot = m * math.log(1 + rate / m)
                rows.append(
                    dict(
                        t=term,
                        y=rate,
                        c=rate / m,
                        df=math.exp(-spot * term),
                        spot=spot,
                        kind='spot',
                        acc=None,
                        pv=None,
                        refs=[],
                    )
                )
        else:
            dfs = dict(L.bootstrap_df(pts, Tmax, m))
            known, grid = [], []
            for term, kind in L.boot_times(pts, Tmax, m):
                y = L._lin(pts, term)
                c = y / m
                refs = []
                if kind == 'grid':
                    refs = [dict(t=x, weight=1.0, df=d) for x, d in grid]
                    pv = c * sum(d for _, d in grid)
                    acc = sum(d for _, d in grid)
                elif kind == 'stub':
                    times = []
                    x = term - 1 / m
                    while x > 1e-9:
                        times.append(x)
                        x -= 1 / m
                    for x in sorted(times):
                        weight = x * m if x == times[-1] else 1.0
                        refs.append(dict(t=x, weight=weight, df=math.exp(-L._lin(known, x) * x)))
                    pv = c * sum(r['weight'] * r['df'] for r in refs)
                    acc = None
                else:
                    pv, acc = None, None
                df = dfs[term]
                spot = -math.log(df) / term
                rows.append(dict(t=term, y=y, c=c, df=df, spot=spot, kind=kind, acc=acc, pv=pv, refs=refs))
                if kind == 'grid':
                    grid.append((term, df))
                known.append((term, spot))
                known.sort()
        result.append(dict(name=name, m=m, inputs=pts, rows=rows))
    return dict(mode='bootstrap', curves=result, y_type=t.y_type, run_id=run.summary['run_id'])


# ── 주식선택권 — value_row 가 남긴 회차 격자를 읽는다 (다시 평가하지 않는다) ─────────────────────

def _so_kind(row, tr):
    if tr['exercise']:
        return '행사'
    share = tr.get('pass_share', 1.0)
    if share == 0:
        return '소멸 (시장조건)'
    if share < 1:
        return '일부 소멸 (시장조건)'
    if tr['terminal']:
        return '소멸 (외가격)'
    if not tr['vested']:
        return '가득 전' if tr['step'] < row['vest_step'] else '행사 시작 전'
    return '계속 보유'


def _so_steps(t, row, e_step):
    n, delta = row['n'], row['dt']
    steps = []
    for i in range(n + 1):
        s = dict(i=i, date=_date(t, i, delta), can_ex=i >= row['ex_step'], before_vest=i < row['vest_step'],
                 judged=bool(row.get('mkt')) and (i == row['vest_step'] if row['mkt'] == 1 else i <= row['vest_step']))
        if i < n:
            s.update(q=row['p'][i], rf=row['fwd'][i], df=math.exp(-row['fwd'][i]*delta),
                     exit=e_step if i >= row['ex_step'] else 0.0,
                     nv=row['nv_step'] if i < row['vest_step'] else 0.0)
        steps.append(s)
    return steps


def _so_head(run, row, mode):
    from . import stock_option as SO
    t, delta = run.terms, row['dt']
    e_step = 1.0 - (1.0 - float(t.so_exit))**delta
    return dict(mode=mode, name=row['name'], n=row['n'], start=0, end=row['n'], steps=_so_steps(t, row, e_step),
                nodes={}, delta=delta, u=row['u'], d=row['d'], S0=float(t.S0), K=float(row.get('K') or t.K0),
                sig=float(t.sig), div_y=float(t.div_y), so_ex=int(t.so_ex), so_mult=float(t.so_mult),
                so_exit=float(t.so_exit), nv=float(getattr(t, 'so_nv', 0.0)), nv_step=row.get('nv_step', 0.0),
                exit_step=e_step, mkt=int(row.get('mkt') or 0),
                mkt_label=('측정기준일 전에 목표 도달 — 시장조건 충족' if row.get('hit') else SO.MKT_LABELS[int(row.get('mkt') or 0)]),
                target=row.get('target'), vest_step=row['vest_step'], ex_step=row['ex_step'],
                ex_label=SO.EX_LABELS[int(t.so_ex)], run_id=run.summary['run_id'])


def so_node_values(run, row, start=0, end=None):
    """회차 하나의 격자 노드 — 칸마다 node_trace 의 풀이를 싣는다 (화면 계산식 = 엔진 계산)."""
    from . import stock_option as SO
    t, n = run.terms, row['n']
    end = n if end is None else min(end, n)
    view = _so_head(run, row, 'so_nodes')
    nodes = view['nodes']
    for i in range(start, min(end + 1, n) + 1):          # 마지막 열이 참조하는 다음 열까지 싣는다
        for down in range(i + 1):
            tr = SO.node_trace(t, row, i, down)
            r = dict(i=i, j=i - down, down=down, stock=tr['stock'], K=tr['strike'], value=tr['value'],
                     intrinsic=tr['intrinsic'], exercise=tr['exercise'], terminal=tr['terminal'], kind=_so_kind(row, tr),
                     rule=tr['rule'], share=tr.get('pass_share', 1.0), before_share=tr.get('before_share'),
                     hold=tr.get('hold'), cont=tr.get('cont'), wait=_finite(tr.get('wait')), wait_rule=tr.get('wait_rule'),
                     up=None, dn=None)
            if not tr['terminal']:
                r.update(up=[i + 1, i + 1 - down], dn=[i + 1, i - down])
            nodes[f'{i},{down}'] = r
    view.update(start=start, end=end)
    return view


def so_schedule_values(run, row):
    """회차 하나의 시점별 금리·확률·행사 가능 여부."""
    return _so_head(run, row, 'so_schedule')


def worksheet_html(payload):
    # No remote assets, no network, no user strings interpolated as HTML.
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    raw = raw.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    from .ui_theme import css_vars
    page = Path(__file__).with_name('calculation_grid.html').read_text(encoding='utf-8')
    return page.replace('__THEME__', css_vars()).replace('__PAYLOAD__', raw)
