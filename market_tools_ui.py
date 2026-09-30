"""Input preparation tools apply directly to the active case."""
import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict
import streamlit as st
from valuation import legacy
from valuation.case import Case, inspect_case
from valuation.bridge import apply_changes
from valuation.service import _prepare
from valuation.market_data import parse_price_table


def apply(case, changes, sources=None, calibration=None):
    from workspace_app import save_case
    candidate = apply_changes(case, changes, validate=False)
    if sources:
        candidate.sources.update(sources)
    if calibration is not None:
        candidate.calibration = calibration
    save_case(candidate)


def main(case):
    tools = ['변동성 산출', '기준일 주가 조회', '주가 역산'] + (['보정 이어 적용'] if case.calibration else []) + ['금리곡선 불러오기', 'BDT 금리 변동성']
    tool = st.selectbox('시장자료 도구', tools)
    if tool == '변동성 산출':
        from vol_app import main as volatility
        volatility()
        return
    if tool == '기준일 주가 조회':
        code = st.text_input('종목코드', value=case.market.get('ticker', ''))
        market = st.selectbox('주식시장', ['KOSPI', 'KOSDAQ', 'US'])
        date = case.effective().get('d_base')
        st.caption(f'평가기준일: {date or "미입력"}. 휴장일은 직전 거래일 종가를 사용합니다.')
        if st.button('종가 조회 후 입력', disabled=not code.strip() or not date):
            try:
                day, close, symbol, adj = legacy.fetch_close(code, market, date)
                if day > date or close <= 0:
                    raise ValueError('조회 날짜 또는 주가를 확인하십시오.')
                src = f'Yahoo Finance {symbol} {day} 원주가'
                apply(case, dict(S0=float(close), ticker=code, s0_date=day, s0_raw=float(close),
                                  s0_adj=float(adj) if adj is not None else -1., s0_src=src, s0_splits='별도 확인 필요'), {'S0': src})
            except Exception as exc:
                st.error(f'종가를 적용하지 못했습니다: {exc}')
    elif tool == '주가 역산':
        target = st.number_input('역산 목표(원금 100 기준)', min_value=.000001, value=float(case.effective().get('bs_target', 100.)))
        net = st.selectbox('역산 기준', [0, 1], format_func=lambda x: '콜 차감 후' if x else '콜 차감 전', index=int(case.effective().get('bs_net', 0)))
        reason = st.text_area('거래가격을 목표로 사용하는 근거')
        eff = case.effective()
        first = eff.get('d_base') and eff.get('d_base') == eff.get('d_issue')
        equity_ps = st.number_input('보정일 지분평가 주당가치(원) — 다음 분기 이어 적용의 기준', min_value=0.,
                                    value=float(eff.get('S0') or 0.), disabled=not first,
                                    help='평가기준일 = 발행일일 때 역산을 적용하면 보정 기록으로 남깁니다 (1113 문단 64).')
        st.caption('주당가치 외 계약조건·변동성·금리를 먼저 입력하십시오. 목표값의 거래 조건과 평가기준일을 확인한 후 적용합니다.')
        key = [case.fingerprint(), target, net]
        if st.button('주가 역산 실행'):
            st.session_state.pop('_backsolve_result', None)
            try:
                draft = apply_changes(case, {'S0': case.effective().get('K0', 100.), 'bs_target': target, 'bs_net': net}, validate=False)
                _, terms, _, _ = _prepare(draft)
                with st.spinner('역산을 위해 반복 평가하고 있습니다.'):
                    value, fitted, iterations = (legacy.sha_backsolve(terms, target) if legacy.is_sha(terms) else legacy.backsolve(terms))
                if iterations < 0:
                    raise ValueError('탐색 범위 내에서 목표값에 도달하지 못했습니다. 입력 조건과 목표값을 검토하십시오.')
                st.session_state._backsolve_result = (key, value, fitted, iterations)
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))
        result = st.session_state.get('_backsolve_result')
        if result and result[0] == key:
            st.metric('역산 주당가치(원)', f'{result[1]:,.6f}')
            st.caption(f'목표 대사값 {result[2]:.8f} / 반복 {result[3]}회')
            if first:
                st.caption('발행일 평가입니다. 적용하면 보정 기록(보정일·보정 전후 주가·당시 주당가치·근거)을 평가파일과 조서 「보정기록」에 남깁니다.')
            if st.button('역산 주당가치 적용', disabled=not reason.strip() or (first and equity_ps <= 0)):
                cal = (dict(date=eff['d_base'], target='S0', before=float(eff.get('S0') or 0.), after=float(result[1]),
                            equity_ps=float(equity_ps), reason=reason.strip()) if first else None)
                apply(case, {'S0': result[1], 'bs_target': target, 'bs_net': net, 's0_src': '거래가격 역산', 's0_date': '', 's0_raw': -1., 's0_adj': -1., 's0_splits': ''}, {'S0': f'목표 {target}, 콜 차감 여부 {net}. {reason}'}, cal)
    elif tool == '보정 이어 적용':
        cal = case.calibration
        st.caption(f"보정 기록 — {cal['date']} · 보정 주가 {cal['after']:,.4f} · 당시 지분평가 주당가치 {cal['equity_ps']:,.4f} · {cal['reason']}")
        now = st.number_input('이번 평가기준일 지분평가 주당가치(원)', min_value=0., value=float(cal['equity_ps']))
        new_s0 = cal['after'] * now / cal['equity_ps'] if cal['equity_ps'] > 0 else 0.
        st.metric('이어 적용 주가(원)', f'{new_s0:,.4f}')
        st.caption(f"새 주가 = 보정 주가 {cal['after']:,.4f} × (이번 {now:,.4f} ÷ 보정일 {cal['equity_ps']:,.4f}). "
                   '전기말 장부금액과의 차이는 후속 분개에서 평가손익으로 나옵니다.')
        if st.button('이어 적용', disabled=now <= 0):
            src = f"보정 이어 적용 — {cal['date']} 보정 주가 {cal['after']:,.4f} × {now:,.4f}/{cal['equity_ps']:,.4f}"
            apply(case, {'S0': new_s0, 's0_src': src, 's0_date': '', 's0_raw': -1., 's0_adj': -1., 's0_splits': ''}, {'S0': src})
    elif tool == '금리곡선 불러오기':
        source = st.radio('금리자료 형식', ['KIS-Net 파일', '표 붙여넣기'], horizontal=True)
        changes, origins, rate_changes = {}, {}, {'rate_mode': 'direct'}
        if source == 'KIS-Net 파일':
            upload = st.file_uploader('KIS-Net 기준수익률 표', type=['xls', 'xlsx'])
            if upload:
                try:
                    curves = legacy.read_kisnet(upload.name, upload.getvalue())
                    if not curves:
                        raise ValueError('금리곡선을 찾지 못했습니다.')
                    a = st.selectbox('무위험 곡선', range(len(curves)), format_func=lambda i: curves[i][0])
                    way = st.radio('위험 곡선', ['한 등급 곡선', '두 등급 보간'], horizontal=True,
                                   help='평가대상 등급이 고시표에 없을 때만 두 등급 보간을 씁니다.')
                    sha = hashlib.sha256(upload.getvalue()).hexdigest()
                    if way == '한 등급 곡선':
                        b = st.selectbox('위험 곡선 (등급)', range(len(curves)), format_func=lambda i: curves[i][0])
                        changes = {'rf_curve': curves[a][1], 'cr_curve': curves[b][1]}
                        origins = {k: f'{upload.name} · {curves[i][0]} · SHA256 {sha}' for k,i in [('rf_curve',a),('cr_curve',b)]}
                        rate_changes = {'rate_mode': 'direct'}
                    else:
                        rated = [i for i in range(len(curves)) if legacy.rating_in(curves[i][0])]
                        if len(rated) < 2:
                            raise ValueError('고시표에서 등급이 적힌 곡선을 두 개 이상 찾지 못했습니다.')
                        r1, r2, r3 = st.columns(3)
                        ia = r1.selectbox('곡선 A', rated, format_func=lambda i: curves[i][0])
                        ib = r2.selectbox('곡선 B', rated, index=len(rated)-1, format_func=lambda i: curves[i][0])
                        tgt = r3.selectbox('평가대상 등급', legacy.RATINGS, index=8)
                        ga, gb = legacy.rating_in(curves[ia][0]), legacy.rating_in(curves[ib][0])
                        xa, xb, xt = (legacy.rating_idx(g) for g in (ga, gb, tgt))
                        if xa == xb:
                            raise ValueError('서로 다른 등급 두 개를 고르십시오.')
                        w = (xt - xa)/(xb - xa)
                        st.caption(f'가중치 — {ga} {1-w:.0%} · {gb} {w:.0%}' + (' · 두 곡선 밖이라 외삽합니다 (금리를 낮게 잡을 수 있음)' if not 0 <= w <= 1 else ''))
                        changes = {'rf_curve': curves[a][1], 'cr_curve': curves[ia][1], 'cr_curve_b': curves[ib][1]}
                        origins = {'rf_curve': f'{upload.name} · {curves[a][0]} · SHA256 {sha}',
                                   'cr_curve': f'{upload.name} · {ga}·{gb} 두 등급 보간 → {tgt} · SHA256 {sha}'}
                        rate_changes = {'rate_mode': 'rating', 'rt_a': ga, 'rt_b': gb, 'rt_tgt': tgt,
                                        'cr_src': f'{ga}·{gb} 두 등급 보간 → {tgt}'}
                except Exception as exc:
                    st.error(f'금리표를 읽지 못했습니다: {exc}')
        else:
            unit = st.selectbox('만기 단위', ['year', 'month'], format_func=lambda x: '년' if x == 'year' else '개월')
            for key, title in [('rf_curve', '무위험'), ('cr_curve', '위험')]:
                text = st.text_area(title + ' 금리표 (만기, 연이율 %)')
                changes[key] = legacy.parse_yields(text, unit)
                origins[key] = st.text_input(title + ' 금리 출처·자료 기준일')
        for key, points in changes.items():
            st.write({'rf_curve': '무위험 금리곡선', 'cr_curve': '위험 금리곡선', 'cr_curve_b': '위험 금리곡선 B'}[key])
            st.dataframe([{'만기(년)': x, '연이율(%)': y * 100} for x,y in points], hide_index=True)
        if st.button('금리곡선 적용', disabled=any(len(changes.get(k, [])) < 2 for k in ['rf_curve','cr_curve'])):
            draft = apply_changes(case, changes, validate=False)
            curve_errors = [i for i in inspect_case(draft) if i.severity == 'error' and i.field in changes]
            if curve_errors:
                st.error(' / '.join(i.message for i in curve_errors))
            else:
                apply(case, {**changes, **(rate_changes if source == 'KIS-Net 파일' and changes else {'rate_mode': 'direct'})}, origins)
    else:
        st.caption('선택한 고정 만기·등급의 양수 금리 시계열로 BDT 상대 변동성을 산출합니다. 음수·0 금리에는 로그정규 방식이 적용되지 않습니다.')
        upload = st.file_uploader('금리 시계열: 첫 열 날짜, 나머지 열 등급·만기별 금리(%)', type=['csv','tsv','txt','xlsx'])
        annual = int(st.number_input('연 관측일수', min_value=1, max_value=366, value=250))
        drop = st.checkbox('금리 이상치 제거', value=True)
        if upload:
            try:
                series = parse_price_table(legacy.read_upload(upload.name, upload.getvalue()))
                # 첫 줄에 제목이 없으면 첫 관측값이 열 이름으로 읽혀 그날 금리가 빠지고, 출처 기록에
                # 「16.027…」 같은 숫자가 열 이름으로 남는다. 숫자 열 이름은 받지 않는다.
                def _numeric(name):
                    try:
                        float(str(name).replace('%', '').replace(',', '').strip()); return True
                    except ValueError:
                        return False
                if any(_numeric(name) for name, _ in series):
                    raise ValueError('파일 첫 줄이 제목이 아니라 숫자로 보입니다(열 이름: '
                                     + ', '.join(str(n) for n, _ in series if _numeric(n))
                                     + '). 첫 관측값이 열 이름으로 읽혀 빠집니다. 첫 줄에 「날짜, BBB- 5Y」처럼 '
                                       '제목을 넣어 다시 올리십시오.')
                idx = st.selectbox('금리 시계열 선택', range(len(series)), format_func=lambda i: series[i][0])
                name, rows = series[idx]
                date = case.effective().get('d_base')
                if not date:
                    raise ValueError('평가기준일을 먼저 입력하십시오.')
                if any(str(d) > date for d, _ in rows):
                    raise ValueError('평가기준일 이후 금리가 있습니다. 원본 자료의 기간을 수정하십시오.')
                result = legacy.rate_vol(rows, annual, drop)
                if not result or not math.isfinite(result['annual']) or result['annual'] <= 0:
                    raise ValueError('변동성 산출에 필요한 관측치가 부족합니다.')
                st.metric('BDT 상대 변동성', f"{result['annual']:.2%}")
                st.caption(f"원본 식별값 {hashlib.sha256(upload.getvalue()).hexdigest()[:16]} · {rows[0][0]} ~ {rows[-1][0]}")
                if st.button('BDT 금리 변동성 적용'):
                    src = f'{upload.name} · {name} · {annual}일 · 이상치제거 {drop} · SHA256 {hashlib.sha256(upload.getvalue()).hexdigest()}'
                    apply(case, {'bdt_sig': result['annual'], 'rvol_how': src}, {'bdt_sig': src})
            except (ValueError, TypeError, ArithmeticError) as exc:
                st.error(str(exc))
