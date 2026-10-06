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
        s = dict(i=i, date=_date(t, i, delta), coupon=100*L.eff_cpn(t)*t.ipay/12*pays.get(i, 0),
                 cv=bool(tree['can_convert'](i)), put=bool(tree['can_put'](i)), call=bool(tree['can_call'](i)))
        if i < t.n:
            q, rf, cr = tree['qi'](i), tree['fwdRF'](i), tree['fwdCR'](i)
            s.update(q=q, rf=rf, cr=cr, df_rf=math.exp(-rf*delta), df_cr=math.exp(-cr*delta))
        steps.append(s)
    nodes = {}
    for (i, j), o in tree['memo'].items():
        # Include the next column so the final visible column's references remain inspectable.
        if not start <= i <= end + 1:
            continue
        s = steps[i]
        terminal = 'up' not in o
        kind = o.get('kind', '')
        gs_kind = kind
        if not terminal and not L.bw_cash(t):
            # memo.kind belongs to TF; GS's own V/P can select a different branch.
            cash = [k for k in ('pv', 'kv') if _finite(o.get(k)) is not None and
                    (o[k] > 0 if k == 'pv' else True) and abs(o['V']-o[k]) < L.tie_tol(o['V'], o[k])]
            if cash:
                gs_kind = 'put' if 'pv' in cash and (not t.pc_order or 'kv' not in cash) else 'call'
            elif o.get('cv', 0) > 0 and abs(o['V']-o['cv']) < L.tie_tol(o['V'], o['cv']):
                gs_kind = 'conv'
            else:
                gs_kind = 'hold'
        if t.model == 'GS':
            kind = gs_kind
        r = dict(i=i, j=j, down=i-j, stock=tree['S'](i, j), K=o.get('K'),
                 value=o['V'] if t.model == 'GS' else o['E']+o['B'], E=o['E'], B=o['B'], P=o['P'],
                 kind=KINDS.get(kind, kind), probability_kind=KINDS.get(gs_kind, gs_kind), terminal=terminal,
                 cv=_finite(o.get('cv')) if s['cv'] or kind in ('auto', 'ipo') else None,
                 pv=_finite(o.get('pv')) if s['put'] else None,
                 kv=_finite(o.get('kv')) if s['call'] else None,
                 wv=_finite(o.get('wv')), wx=o.get('wx'),
                 hold=None, equity_hold=None, debt_hold=None, up=None, dn=None)
        if not terminal:
            a, b = tree['memo'][o['up']], tree['memo'][o['dn']]
            eh = (s['q']*a['E']+(1-s['q'])*b['E'])*s['df_rf']
            bh = (s['q']*a['B']+(1-s['q'])*b['B'])*s['df_cr']+s['coupon']
            ya = a['P']*s['rf']+(1-a['P'])*s['cr']
            yb = b['P']*s['rf']+(1-b['P'])*s['cr']
            r.update(hold=o['Vc'] if t.model == 'GS' else o['hold'], equity_hold=eh, debt_hold=bh,
                     ya=ya, yb=yb, df_up=math.exp(-ya*delta), df_down=math.exp(-yb*delta),
                     up=list(o['up']), dn=list(o['dn']))
        nodes[f'{i},{i-j}'] = r
    return dict(mode='nodes', model=t.model, n=t.n, start=start, end=end, steps=steps, nodes=nodes,
                delta=delta, u=tree['u'], d=tree['d'], S0=t.S0, sig=t.sig, div_y=t.div_y,
                bw_cash=L.bw_cash(t), bw_detach=bool(t.bw_detach),
                pc_order=t.pc_order, conv_resp=L.conv_resp(t), run_id=run.summary['run_id'])


def schedule_values(run):
    t, tree = run.terms, run.raw['full']
    delta = tree['dt']
    ea = L.exercise_amounts(t, t.n, delta)
    pays = L.pay_steps(t, t.n, delta)
    refix = L.refix_steps(t, t.n, delta)
    coupons = [100*L.eff_cpn(t)*t.ipay/12*pays.get(i, 0) for i in range(t.n+1)]
    host = [0.]*(t.n+1)
    host[-1] = ea['red_val']+coupons[-1]
    for i in range(t.n-1, -1, -1):
        host[i] = host[i+1]*math.exp(-tree['fwdCR'](i)*delta)+coupons[i]
    rows = []
    for i in range(t.n + 1):
        r = dict(i=i, date=_date(t, i, delta), time=i*delta,
                 cv=bool(tree['can_convert'](i)), put=bool(tree['can_put'](i)),
                 call=bool(t.k_w > 0 and ea['k_on'](i)) and i<t.n, refix=i in refix,
                 put_contract=ea['put'](i) if ea['p_on'](i) else None,
                 put_value=ea['put_val'](i) if ea['p_on'](i) else None,
                 call_contract=ea['call'](i) if t.k_w > 0 and i in ea['k_dates'] else None,
                 coupon=100*L.eff_cpn(t)*t.ipay/12*pays.get(i, 0), pays=pays.get(i, 0),
                 host=host[i],
                 redemption=ea['red_val'] if i==t.n else None,
                 ipo=bool(t.ipo_on and i==tree['st_lo'](t.ipo_m) and t.ipo_px>0 and 0<i<=t.n),
                 rf=None, cr=None, q=None, df_rf=None, df_cr=None)
        if i < t.n:
            r.update(rf=tree['fwdRF'](i), cr=tree['fwdCR'](i), q=tree['qi'](i))
            r.update(df_rf=math.exp(-r['rf']*delta), df_cr=math.exp(-r['cr']*delta))
        rows.append(r)
    return dict(mode='schedule', model=t.model, n=t.n, steps=rows, delta=delta,
                u=tree['u'], d=tree['d'], sig=t.sig, div_y=t.div_y,
                cpn=L.eff_cpn(t), ipay=t.ipay, run_id=run.summary['run_id'])


def bootstrap_values(run):
    """Explain the same curve builder, including stub and short first maturities."""
    t = run.terms
    result = []
    for name, pts, m in [('무위험금리 Rf', t.rf_curve, t.cmp_rf),
                         ('위험금리 Kd', L.credit_curve(t), t.cmp_cr)]:
        rows = []
        if t.y_type == 'spot':
            for term, rate in pts:
                spot = m*math.log(1+rate/m)
                rows.append(dict(t=term, y=rate, c=rate/m, df=math.exp(-spot*term), spot=spot,
                                 kind='spot', acc=None, pv=None, refs=[]))
        else:
            dfs = dict(L.bootstrap_df(pts, t.T, m))
            known, grid = [], []
            for term, kind in L.boot_times(pts, t.T, m):
                y = L._lin(pts, term); c = y/m
                refs = []
                if kind == 'grid':
                    refs = [dict(t=x, weight=1., df=d) for x, d in grid]
                    pv = c*sum(d for _, d in grid)
                    acc = sum(d for _, d in grid)
                elif kind == 'stub':
                    times = []; x = term-1/m
                    while x > 1e-9:
                        times.append(x); x -= 1/m
                    for x in sorted(times):
                        weight = x*m if x==times[-1] else 1.
                        refs.append(dict(t=x, weight=weight, df=math.exp(-L._lin(known, x)*x)))
                    pv = c*sum(r['weight']*r['df'] for r in refs)
                    acc = None
                else:
                    pv, acc = None, None
                df = dfs[term]; spot = -math.log(df)/term
                rows.append(dict(t=term, y=y, c=c, df=df, spot=spot, kind=kind, acc=acc, pv=pv, refs=refs))
                if kind=='grid': grid.append((term, df))
                known.append((term, spot)); known.sort()
        result.append(dict(name=name, m=m, inputs=pts, rows=rows))
    return dict(mode='bootstrap', curves=result, y_type=t.y_type, run_id=run.summary['run_id'])


def worksheet_html(payload):
    # No remote assets, no network, no user strings interpolated as HTML.
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    raw = raw.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return Path(__file__).with_name('calculation_grid.html').read_text(encoding='utf-8').replace('__PAYLOAD__', raw)
