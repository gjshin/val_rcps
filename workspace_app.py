"""Professional workflow. Rendering and editing never invoke a pricing engine."""
import datetime as dt
import hashlib
import io
import json
import math
import zipfile
from dataclasses import asdict
from typing import get_type_hints
import pandas as pd
import streamlit as st
from valuation.case import Case, SCHEMA, RIGHT_KINDS, REQUIRED, RCPS_REQUIRED, section_for, import_legacy, inspect_case, compare_cases
from valuation.legacy import (Terms, months_to_date, issuer_day1_cases, inst_text, CALL_HOLDERS, call_holder,
                              call_holder_fields)
from valuation.presentation import CHOICES, PERCENT, EVENT_DATES, label, display_value, event_months, issue_rows, choices
from valuation.service import AMOUNT_LABELS, calculate, calculation_key, refresh_run, export_bundle
from valuation.analysis import sensitivity

TYPES = get_type_hints(Terms)
DEFAULTS = asdict(Terms())
COUNTS = {'sha_put_cmp', 'sha_call_cmp', 'cur_periods', 'ytm_cmp', 'k_cmp', 'cmp_rf', 'cmp_cr', 'p_cmp'}


def read_case(raw, name):
    obj = json.loads(raw)
    case = Case.from_dict(obj) if isinstance(obj, dict) and obj.get('schema') == SCHEMA else import_legacy(obj, name)
    malformed = [i for i in inspect_case(case) if i.severity == 'error' and i.code in
                 {'type', 'unknown_field', 'wrong_section', 'assumption_shape', 'assumption_field', 'right_shape', 'right_kind', 'right_text', 'source_type'}]
    if malformed:
        raise ValueError(' / '.join(f'{label(i.field)}: {i.message}' for i in malformed))
    return case


def install_case(case):
    st.session_state.case = case
    st.session_state.revision = st.session_state.get('revision', 0) + 1
    for key in ['run', 'bundle', 'analysis', 'bundle_key', '_input_pending', 'trace_i', 'trace_j', 'result_unit', 'workbook_locations', 'locations_run']:
        st.session_state.pop(key, None)


def save_case(case, *, reset_widgets=True):
    if st.session_state.case.to_dict() == case.to_dict():
        return
    run = st.session_state.get('run')
    st.session_state.case = case
    st.session_state.pop('bundle', None)
    if run and not any(i.severity == 'error' for i in inspect_case(case)):
        if run.summary['calculation_key'] == calculation_key(case):
            st.session_state.run = refresh_run(run, case)
    if reset_widgets:
        st.session_state.revision = st.session_state.get('revision', 0) + 1
    st.rerun()


def field(key, edited, case, prefix='input'):
    st.session_state.setdefault('_rendered_fields', set()).add(key)
    rev = st.session_state.get('revision', 0)
    required = REQUIRED | (RCPS_REQUIRED if edited.get('inst') == 'RCPS' else set())
    value = edited.get(key, None if key in required or key in EVENT_DATES else DEFAULTS.get(key))
    widget_key, title = f'{prefix}_{key}_{rev}', label(key, edited.get('inst'))
    override = next((r for r in case.assumptions if r['field'] == key), None)
    if override and prefix == 'input':
        st.caption(f"{title}: 계산에는 별도 가정 {display_value(key, override['value'], case.contract.get('d_issue'), edited.get('inst'))}을 적용합니다. ‘출처·평가가정’에서 변경하십시오.")
    styles = st.session_state.get('_editing_styles')
    if styles is not None and key in {'p_f', 'k_f', 'sha_put_f', 'sha_call_f', 'rfx_cyc'} and prefix == 'input':
        selected = st.selectbox(title.replace('주기(개월)', '방식'), ['periodic', 'any'],
            index=1 if styles.get(key) == 'any' else 0,
            format_func=lambda x: '기간 중 언제든지' if x == 'any' else '정기 행사·조정', key=widget_key + '_style')
        if selected != styles.get(key, 'periodic'):
            styles[key] = selected
        if selected == 'any':
            st.caption('행사기간 내 모든 계산시점에서 행사합니다. 계산 간격 변경 시에도 유지됩니다.')
            return
    if key == 'p_sep' and not (int(edited.get('emb_approach', 1)) == 2 or
                               (edited.get('conv_class', 'equity') == 'equity' and int(edited.get('k_sep', 1)) != 0)):
        # 분리 정책 접근법 1 — 전환권이 부채이거나 콜을 내재파생에 넣으면 조기상환권은 그 파생과
        # 묶여 분리된다 (1109 B4.3.4). 접근법 2 면 조기상환권을 따로 판단하므로 고를 수 있다.
        st.selectbox(title, [1], format_func=lambda v: CHOICES['p_sep'][v], key=widget_key + '_locked', disabled=True)
        st.caption('분리 정책이 접근법 1(얽힌 권리를 먼저 묶고 판단 — 한공회 실무사례 30~31쪽)이라, 전환권이 '
                   '부채이거나 매도청구권을 내재파생에 포함하면 조기상환권은 그 파생과 묶어 하나의 '
                   '복합내재파생상품으로 분리합니다 (1109 B4.3.4). 조기상환권을 따로 판단하려면 분리 정책을 '
                   '접근법 2로 두십시오.')
        edited[key] = 1
        return
    if key in CHOICES:
        options = choices(key, edited.get('inst'))
        keys = list(options)
        new = st.selectbox(title, keys, index=keys.index(value) if value in keys else None,
                           format_func=lambda v: options[v], key=widget_key, placeholder='선택하십시오')
    elif key in EVENT_DATES:
        issue_date = edited.get('d_issue')
        if not issue_date:
            st.caption(f'{title}: 실제 발행일을 먼저 입력하십시오.')
            return
        original_date = case.contract.get('d_issue') or issue_date
        # 최초 조정일 0 은 «따로 정하지 않음» (발행일 + 주기) 이다 — 발행일로 보이지 않게 비워 둔다.
        initial = (months_to_date(original_date, value)
                   if value is not None and not (key == 'rfx_first' and not value) else None)
        chosen = st.date_input(title, value=initial, min_value=dt.date(1900, 1, 1), max_value=dt.date(2200, 12, 31), key=widget_key)
        if chosen and chosen < dt.date.fromisoformat(issue_date):
            st.error(f'{title}이 발행일보다 빠릅니다.'); new = None
        else:
            new = (0.0 if key == 'rfx_first' and chosen is None else
                   event_months(issue_date, chosen, value, original_date))
    elif key.startswith('d_'):
        try:
            initial = dt.date.fromisoformat(value) if value else None
        except ValueError:
            initial = None
        new = st.date_input(title, value=initial,
                            min_value=dt.date(1900, 1, 1), max_value=dt.date(2200, 12, 31), key=widget_key)
        new = new.isoformat() if new else None
    elif TYPES[key] is int and key not in COUNTS:
        new = int(st.checkbox(title, value=bool(value), key=widget_key))
    elif TYPES[key] in (float, int):
        scale = 100 if key in PERCENT else 1
        # 의무보유 물량 비율의 음수는 «콜 대상 비율과 같음» 이다 — 칸을 비워 보여 준다.
        _same = key == 'k_lock_w' and value is not None and value < 0
        displayed = float(value * scale) if value is not None and not _same else None
        number = st.number_input(title, value=displayed, format='%.4f' if key in PERCENT else '%.2f', help='표시 자릿수와 무관하게 수정하지 않은 원값의 정밀도는 유지됩니다.', key=widget_key)
        new = value if number == displayed else number / scale if number is not None else None
        if key == 'k_lock_w' and new is None:
            new = -1.0
        if new is not None and TYPES[key] is int:
            if float(new).is_integer():
                new = int(new)
            else:
                st.error(f'{title}: 정수를 입력하십시오.')
    elif key in {'p_sched', 'k_sched'}:
        new = st.text_area(title, value=value or '', key=widget_key,
                           help='한 줄에 날짜 또는 발행 후 개월과 원금 대비 금액(%)을 입력합니다. 예: 2028-03-31 108.5')
    else:
        new = st.text_input(title, value=value or '', key=widget_key)
    if new is None:
        edited.pop(key, None)
    else:
        edited[key] = new


def fields(keys, edited, case):
    columns = st.columns(2)
    for idx, key in enumerate(keys):
        with columns[idx % 2]:
            field(key, edited, case)


def right_period(title, start, end, edited, case, extra, errors):
    rev = st.session_state.get('revision', 0)
    original_on = edited.get(start, 99) <= edited.get(end, 0)
    on = st.checkbox(title, value=original_on, key=f'right_{start}_{rev}')
    if on:
        if not original_on:
            edited.pop(start, None); edited.pop(end, None)
        fields([start, end] + extra, edited, case)
        if not edited.get('d_issue'):
            errors.append(f'{title}: 실제 발행일을 먼저 입력하십시오.')
        elif start not in edited or end not in edited:
            errors.append(f'{title}: 행사 시작일과 종료일을 입력하십시오.')
        elif edited[start] > edited[end]:
            errors.append(f'{title}: 행사 시작일이 종료일보다 늦습니다.')
    elif original_on or start not in edited:
        edited[start], edited[end] = 99., 0.
    return on


SHA_COLS = [('name', '평가 구분'), ('start', '행사 시작일'), ('end', '행사 종료일'), ('style', '행사 방식'),
            ('freq', '주기(개월)'), ('price', '주당 기준가격(원)'), ('rate', '가격 가산율(연 %)'),
            ('put_q', '풋 수량(주)'), ('call_q', '콜 수량(주)'), ('kill', '한쪽 행사 시 상대 권리 소멸'),
            ('link_q', '같은 주식 물량(주)')]
SHA_COLS_CALL = [('call_start', '콜 시작일'), ('call_end', '콜 종료일'), ('call_price', '콜 기준가격(원)'),
                 ('call_rate', '콜 가산율(연 %)')]
SHA_COLS_MKT = [('sig', '변동성(연 %)'), ('rf', '무위험 금리(연 %)'), ('pdisc', '풋 할인율(연 %)')]
SHA_COLS_ACC = [('acc_from', '가격 가산 기산일')]
# 평가 대상 상태 · 확정 거래 · 추가 조건 · 같은 주식 묶음 — 해당하는 계약에만 켠다
SHA_COLS_STATE = [('status', '평가 대상 상태'), ('side', '확정된 거래'), ('deal_px', '확정 주당 매매대금(원)'),
                  ('settle', '결제 예정일'), ('cond_basis', '조건부 물량 평가 가정'), ('cond_note', '추가 조건 내용'),
                  ('pool', '같은 주식 묶음'), ('pool_cap', '묶음 공통 한도(주)')]
SHA_STATUS = {'open': '미행사', 'agreed': '행사·매매 확정 (미결제)', 'settled': '결제 완료', 'cond': '추가 조건부'}
SHA_SIDE = {'': '', 'put': '풋 행사 확정 (주식 보유자가 판다)', 'call': '콜 행사 확정 (상대가 산다)'}
SHA_COND = {'': '', 'met': '조건 충족 가정 (반영)', 'unmet': '조건 미충족 가정 (제외)'}
SHA_PICK = {'status': SHA_STATUS, 'side': SHA_SIDE, 'cond_basis': SHA_COND}
SHA_PCT = {'rate', 'call_rate', 'sig', 'rf', 'pdisc'}
SHA_DATES = {'start', 'end', 'call_start', 'call_end', 'acc_from', 'settle'}
SHA_STYLE = {'any': '기간 중 언제든지', 'periodic': '정기', 'single': '특정일 1회'}


def _sha_frame(rows, cols):
    """회차 줄(dict) → 표. 비율은 %로, 날짜는 날짜로 보여 준다."""
    out = []
    for r in rows:
        row = {}
        for k, title in cols:
            v = r.get(k)
            if k in SHA_PCT:
                v = None if v in (None, '') else float(v) * 100
            elif k in SHA_DATES:
                try:
                    v = dt.date.fromisoformat(v) if v else None
                except ValueError:
                    v = None
            elif k == 'style':
                v = SHA_STYLE.get(v or 'any', SHA_STYLE['any'])
            elif k in SHA_PICK:
                v = SHA_PICK[k].get(v or ('open' if k == 'status' else ''), '')
            elif k == 'kill':
                v = bool(v)
            row[title] = v
        out.append(row)
    return pd.DataFrame(out, columns=[t for _, t in cols])


def _sha_rows_from(frame, cols, old):
    """표 → 회차 줄. 숨긴 선택 칸(콜 조건·회차 금리·기산일)은 비운다 — 숨기면 공통 값을 쓴다는 뜻이다.

    실적 연동 가격의 산식 기록(price_note)은 같은 이름·같은 가격일 때만 남긴다.
    """
    back = {v: k for k, v in SHA_STYLE.items()}
    prev = {r.get('name'): r for r in old}
    rows = []
    for rec in frame.to_dict('records'):
        if all(v is None or (isinstance(v, float) and pd.isna(v)) or v == '' for v in rec.values()):
            continue
        row = {}
        for k, title in cols:
            v = rec.get(title)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                v = None
            if k in SHA_PCT:
                v = None if v is None else float(v) / 100
            elif k in SHA_DATES:
                v = v.isoformat() if hasattr(v, 'isoformat') else (str(v) if v else '')
            elif k == 'style':
                v = back.get(v, 'any')
            elif k in SHA_PICK:
                v = {t: c for c, t in SHA_PICK[k].items()}.get(v, '') or (None if k != 'status' else 'open')
            elif k == 'kill':
                v = int(bool(v))
            elif k in ('freq', 'price', 'put_q', 'call_q', 'call_price', 'link_q', 'deal_px', 'pool_cap') and v is not None:
                v = float(v)
            elif k == 'name':
                v = str(v or '').strip()
            row[k] = v
        row = {k: v for k, v in row.items() if v not in (None, '')}
        row['name'] = row.get('name') or f'{len(rows)+1}회차'
        p = prev.get(row['name'])
        if p and p.get('price_note') and p.get('price') == row.get('price'):
            row['price_note'] = p['price_note']
        # 실적 연동 산식은 표에 칸이 없다 — 같은 회차·같은 가격이면 그대로 둔다 (가격을 손으로 바꾸면 산식 연결을 끊는다)
        if p and p.get('perf') and p.get('price') == row.get('price'):
            row['perf'] = p['perf']
        rows.append(row)
    return rows


def _sha_row_px(r):
    """회차의 주당 기준가격 — 실적 연동 산식이 있으면 산식 값, 없으면 칸에 넣은 값 (엔진 sha_row_terms 와 같은 순서).

    산식 입력이 모자라거나 잘못되면 None — 그 문장은 입력 검사(sha_row_issues)가 낸다.
    """
    if r.get('perf'):
        from valuation.legacy import sha_perf_calc
        try:
            return float(sha_perf_calc(r['perf'])[0])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None
    return float(r['price']) if r.get('price') else None


DP_OTHER_COLS = [('name', '상품 이름'), ('rank', '순위'), ('issue', '발행일'), ('face', '발행총액(원)'),
                 ('yld', '상환 보장수익률(연 %)'), ('cmp', '복리 방식'), ('start', '상환청구 시작일'),
                 ('end', '상환청구 종료일'), ('div', '우선배당률(연 %, 발행가 기준)'),
                 ('delay', '넘긴 상환금 가산율(연 %)')]
DP_RANK = {'senior': '평가대상이 선순위', 'pari': '동순위 (비율로 나눔)'}
DP_CMP = {1: '연복리', 0: '단리'}


def dp_editor(edited, errors):
    """배당가능이익 상환 제약 — 연도별 추정 배당가능이익(발생연도 기준)과 같은 재원을 쓰는 다른 상품.

    비워 두면 배당이 가능하다는 전제(제한 없음)로 종전과 같이 계산한다.
    """
    rev = st.session_state.get('revision', 0)
    st.session_state.setdefault('_rendered_fields', set()).update({'dp_rows', 'dp_others', 'dp_delay', 'dp_from', 'dp_unpaid'})
    rows = [r for r in (edited.get('dp_rows') or []) if isinstance(r, dict)]
    # 읽을 수 없는 값 — 표에서는 비워 보여 주고, 사용자가 그 칸을 고치기 전까지 원래 값을 그대로 둔다
    # (그래야 입력 점검의 오류 문장이 남는다). keep[(표, 줄, 칸)] = 원래 값.
    bad, keep = [], {}
    # 형식이 틀린 줄(사전이 아닌 값)은 표에 보이지 않지만 지우지 않고 그대로 둔다 — 입력 점검 오류가 남는다
    junk_p = [r for r in (edited.get('dp_rows') or []) if not isinstance(r, dict)]
    junk_o = [r for r in (edited.get('dp_others') or []) if not isinstance(r, dict)]
    if junk_p: bad.append(f'배당가능이익 표의 형식이 틀린 줄 {len(junk_p)}개')
    _xk = sorted({str(k) for r in (edited.get('dp_rows') or []) if isinstance(r, dict) for k in r if k not in ('fy', 'amt')})
    if _xk: bad.append(f"배당가능이익 표의 알 수 없는 칸({', '.join(_xk)})")
    if junk_o: bad.append(f'다른 상품 표의 형식이 틀린 줄 {len(junk_o)}개')

    def _num(v, f=float, what='', slot=None):
        if v in (None, ''):
            return None
        try:
            if isinstance(v, bool): raise ValueError          # 참·거짓은 숫자가 아니다
            x = f(v)
            if isinstance(x, float) and not math.isfinite(x): raise ValueError
            return x
        except (TypeError, ValueError, OverflowError):
            bad.append(what); keep[slot] = v; return None

    def _whole(v):                            # 정수 값만 — 2027.9 를 2027 로 바꾸지 않는다
        x = float(v)
        if not x.is_integer(): raise ValueError
        return int(x)

    def _day(v, what='', slot=None):
        if not v:
            return None
        try:
            if isinstance(v, dt.date):
                return v if not isinstance(v, dt.datetime) else v.date()
            return dt.date.fromisoformat(str(v))          # 입력 점검과 같이 전체를 읽는다 (뒤에 글자가 붙으면 오류)
        except ValueError:
            bad.append(what); keep[slot] = v; return None
    with st.expander('배당가능이익에 따른 상환 제약' + (f' — {len(rows)}개 연도 입력' if rows else ' (넣지 않으면 제한 없음)'),
                     expanded=bool(rows)):
        st.caption('상환주식은 회사의 이익으로 상환합니다. 연도별 추정 배당가능이익을 **발생연도**(그해 결산 기준)로 넣으면 '
                   '다음 해의 우선배당·상환 재원으로 씁니다. 우선배당을 먼저 빼고 남는 금액만큼 상환하며, 갚지 못한 금액은 '
                   '다음 해로 넘깁니다. 넣지 않은 해는 제한이 없습니다(배당이 가능하다는 전제). 발행자 상환권도 같은 '
                   '재원이 있어야 행사할 수 있고, 제3자 지정 매도청구권은 직접 제한을 받지 않습니다.')
        # 숨은 줄 번호(_id) — 줄을 더하거나 지워도 읽을 수 없던 칸이 어느 원래 줄의 것인지 안다
        frame = st.data_editor(pd.DataFrame([{'_id': x, '발생연도': _num(r.get('fy'), _whole, '발생연도', ('p', x, 'fy')),
                                              '배당가능이익(원)': _num(r.get('amt'), float, '배당가능이익', ('p', x, 'amt'))}
                                             for x, r in enumerate(rows)],
                                            columns=['_id', '발생연도', '배당가능이익(원)']),
                               num_rows='dynamic', hide_index=True, key=f'dp_rows_{rev}',
                               column_order=['발생연도', '배당가능이익(원)'],
                               column_config={'발생연도': st.column_config.NumberColumn(format='%d', step=1),
                                              '배당가능이익(원)': st.column_config.NumberColumn(format='%,.0f')})
        new_rows = []
        nan = lambda v: v is None or (isinstance(v, float) and pd.isna(v))
        _rid = lambda rec, n_: (int(rec['_id']) if not nan(rec.get('_id')) and 0 <= int(rec['_id']) < n_ else None)
        for rec in frame.to_dict('records'):
            x = _rid(rec, len(rows))
            fy, amt = rec.get('발생연도'), rec.get('배당가능이익(원)')
            fy = None if nan(fy) else int(fy)
            amt = None if nan(amt) else float(amt)
            if x is not None:                    # 원래 줄 — 읽을 수 없던 칸을 비워 둔 채면 원래 값을 둔다
                if fy is None: fy = keep.get(('p', x, 'fy'))
                if amt is None: amt = keep.get(('p', x, 'amt'))
            extra = {k: v for k, v in rows[x].items() if k not in ('fy', 'amt')} if x is not None else {}
            if fy is None and amt is None and not extra:
                continue
            new_rows.append({'fy': fy, 'amt': amt, **extra})   # 모르는 칸(예: 통화)은 고칠 때까지 그대로 둔다
        edited['dp_rows'] = sorted(new_rows, key=lambda r: (not isinstance(r['fy'], int), r['fy'] if isinstance(r['fy'], int) else 0)) + junk_p
        _g0 = _num(edited.get('dp_delay'), float, '가산율', ('g',))
        if _g0 is not None and not 0 <= _g0 <= 1:
            bad.append('가산율'); keep[('g',)] = edited.get('dp_delay'); _g0 = None
        # 읽을 수 없던 값은 칸을 비워 둔다 — 비운 채면 원래 값을 두고, 무엇이든(0 포함) 넣으면 그 값을 쓴다
        _g = st.number_input('넘긴 상환금에 붙는 연 가산율 (%)', min_value=0.0, max_value=100.0,
                             value=None if ('g',) in keep else min(100.0, max(0.0, (_g0 or 0.0)*100)),
                             step=0.5, key=f'dp_delay_{rev}',
                             help='갚지 못해 다음 해로 넘긴 상환금에 계약상 지연이자가 붙으면 넣으십시오. 없으면 0.')
        edited['dp_delay'] = keep[('g',)] if _g is None and ('g',) in keep else (_g or 0.0)/100
        try:
            _m0, _d0 = (int(x) for x in str(edited.get('dp_from') or '01-01').split('-'))
            dt.date(2001, _m0, _d0)
        except (TypeError, ValueError):
            bad.append('재원 사용 시작일'); keep[('f',)] = edited.get('dp_from'); _m0, _d0 = None, None
        _c1, _c2 = st.columns(2)
        _m = _c1.number_input('재원 사용 시작 (월)', min_value=1, max_value=12, value=_m0, step=1, key=f'dp_from_m_{rev}',
                              help='이 날 전의 청구·지급은 그 전해 재원을 씁니다. 결산 확정(정기주주총회) 뒤부터 직전 연도 이익을 '
                                   '쓴다고 보려면 예를 들어 4월 1일을 넣으십시오. 기본 1월 1일은 해가 바뀌면 바로 씁니다.')
        _d = _c2.number_input('재원 사용 시작 (일)', min_value=1, max_value=31, value=_d0, step=1, key=f'dp_from_d_{rev}')
        edited['dp_from'] = (keep[('f',)] if ('f',) in keep and (_m is None or _d is None)
                             else f"{int(_m or 1):02d}-{int(_d or 1):02d}")
        from valuation.legacy import DP_UNPAID
        _u0 = edited.get('dp_unpaid') or 'extend'
        if _u0 not in DP_UNPAID: bad.append('만기까지 갚지 못한 금액')
        _opts = list(DP_UNPAID)
        _u = st.radio('만기까지 갚지 못한 금액', _opts, format_func=DP_UNPAID.get, key=f'dp_unpaid_{rev}',
                      index=_opts.index(_u0) if _u0 in DP_UNPAID else None, horizontal=True,
                      help='계약서의 상환 미완료 조항을 확인하고 고르십시오. 흔한 조항은 «상환이 끝날 때까지 상환기간을 '
                           '연장(지연이자)» 입니다. «받지 못함» 은 만기일 뒤로 넘어간 지급을 0 으로 봅니다(그만큼 그 뒤 재원은 '
                           '동순위 상품 몫). 만기에 보통주로 바꾸는 조항은 아직 반영하지 않습니다.')
        edited['dp_unpaid'] = _u0 if _u is None else _u
        st.markdown('**같은 배당가능이익을 쓰는 다른 상품** — 기본은 평가대상이 선순위라 다른 상품은 평가대상 상환 뒤에 '
                    '씁니다. «동순위» 로 고른 상품만 그 해 함께 상환청구한다고 보고 남은 상환금 비율로 나눕니다. 상환금은 '
                    '아래 칸(발행일·발행총액·보장수익률)으로 앱이 계산합니다.')
        others = [r for r in (edited.get('dp_others') or []) if isinstance(r, dict)]

        def _cell(x, r, k, t):
            nm = r.get('name') or '다른 상품'
            sl = ('o', x, k)
            if k == 'rank':
                if (r.get('rank') or 'senior') not in DP_RANK: bad.append(f'{nm} 순위'); keep[sl] = r.get('rank')
                return DP_RANK.get(r.get('rank') or 'senior')
            if k == 'cmp':
                c_ = _num(r.get('cmp', 1), _whole, f'{nm} 복리 방식', sl)
                if c_ is not None and c_ not in DP_CMP: bad.append(f'{nm} 복리 방식'); keep[sl] = r.get('cmp')
                return DP_CMP.get(c_)
            if k in ('yld', 'div', 'delay'):
                v = _num(r.get(k), float, f'{nm} {t}', sl); return None if v is None else v*100
            if k in ('issue', 'start', 'end'): return _day(r.get(k), f'{nm} {t}', sl)
            if k == 'face': return _num(r.get(k), float, f'{nm} {t}', sl)
            return r.get(k)
        for r in others:
            extra = [k for k in r if k not in dict(DP_OTHER_COLS)]
            if extra: bad.append(f"{r.get('name') or '다른 상품'} 의 알 수 없는 칸({', '.join(map(str, extra))})")
        _of = pd.DataFrame([{'_id': x, **{t: _cell(x, r, k, t) for k, t in DP_OTHER_COLS}} for x, r in enumerate(others)],
                           columns=['_id'] + [t for _, t in DP_OTHER_COLS])
        if bad:
            st.warning('불러온 파일에 읽을 수 없는 값이 있어 표에서 비워 두었습니다(원래 값은 고칠 때까지 그대로 둡니다) — 확인하고 다시 넣으십시오: '
                       + ', '.join(dict.fromkeys(bad)))
        of = st.data_editor(_of, num_rows='dynamic', hide_index=True, key=f'dp_others_{rev}',
                            column_order=[t for _, t in DP_OTHER_COLS],
                            column_config={'순위': st.column_config.SelectboxColumn(options=list(DP_RANK.values())),
                                           '복리 방식': st.column_config.SelectboxColumn(options=list(DP_CMP.values())),
                                           '발행일': st.column_config.DateColumn(),
                                           '상환청구 시작일': st.column_config.DateColumn(),
                                           '상환청구 종료일': st.column_config.DateColumn(),
                                           '발행총액(원)': st.column_config.NumberColumn(format='%,.0f')})
        back_rank = {v: k for k, v in DP_RANK.items()}; back_cmp = {v: k for k, v in DP_CMP.items()}
        new_o = []
        for rec in of.to_dict('records'):
            x = _rid(rec, len(others))
            _same_o = x is not None                  # 원래 줄이면 읽을 수 없던 칸·모르는 칸을 되살린다
            if all(v is None or (isinstance(v, float) and pd.isna(v)) or v == '' for c_, v in rec.items() if c_ != '_id'):
                continue
            row = {}
            for k, t in DP_OTHER_COLS:
                v = rec.get(t)
                if v is None or (isinstance(v, float) and pd.isna(v)): v = None
                sl = ('o', x, k)
                if _same_o and sl in keep and v is None:
                    row[k] = keep[sl]; continue      # 읽을 수 없던 칸을 고치지 않았으면 원래 값을 둔다
                if k == 'rank': v = back_rank.get(v, 'senior')
                elif k == 'cmp': v = back_cmp.get(v, 1)
                elif k in ('yld', 'div', 'delay'): v = 0.0 if v is None else float(v)/100
                elif k in ('issue', 'start', 'end'): v = v.isoformat() if hasattr(v, 'isoformat') else (str(v) if v else '')
                elif k == 'face': v = None if v is None else float(v)
                elif k == 'name': v = str(v or '').strip() or f'다른 상품 {len(new_o)+1}'
                row[k] = v
            if _same_o:                              # 모르는 칸은 고칠 때까지 그대로 둔다 (입력 점검 오류가 남는다)
                row.update({k: v for k, v in others[x].items() if k not in dict(DP_OTHER_COLS)})
            new_o.append(row)
        edited['dp_others'] = new_o + junk_o
        if new_rows:
            st.caption('평가 결과의 «확인할 사항» 에 넣지 않은 해·재원이 우선배당보다 작은 해·발행자 상환권이 막힌 행사일이 '
                       '나옵니다. 상세 조서의 «00 배당가능이익 상환» 시트에 청구 시점별 지급 일정이 실립니다.')


def dp_panel(run):
    """배당가능이익 상환 제약 — 청구 연도별 계약 상환금과 실제 지급 일정의 현재가치 (평가에 쓴 값)."""
    from valuation import legacy
    t = run.terms
    if not legacy.dp_active(t):
        return
    n = int(t.n); dt_ = t.T/n
    EA = legacy.exercise_amounts(t, n, dt_)
    rows, seen = [], set()
    for i in sorted(EA["p_dates"]):
        y = legacy.dp_fund_year(t, EA["dp"].claim_dt(i, "put"))
        if y in seen:
            continue
        seen.add(y)
        a, v = EA["put"](i), EA["put_val"](i)
        rows.append({'청구 연도 (첫 청구일)': f"{y} ({EA['dp'].claim_dt(i, 'put').date()})",
                     '쓰는 배당가능이익 (발생연도)': (f"{y-1}년 {EA['dp'].P[y-1]:,.0f}원" if (y-1) in EA['dp'].P else f"{y-1}년 — 넣지 않음 (제한 없음)"),
                     '계약 상환금 (100 기준)': a, '실제 지급 현재가치 (100 기준)': v, '비율': (v/a if a else None)})
    rows.append({'청구 연도 (첫 청구일)': f"만기 {legacy.dp_step_dt(t, dt_, n).date()} (만기상환)", '쓰는 배당가능이익 (발생연도)': '',
                 '계약 상환금 (100 기준)': EA['red'], '실제 지급 현재가치 (100 기준)': EA['red_val'],
                 '비율': (EA['red_val']/EA['red'] if EA['red'] else None)})
    with st.expander('배당가능이익 반영 — 청구 연도별 상환청구 가치', expanded=False):
        st.caption('평가에 쓴 상환청구 가치는 계약 상환금이 아니라, 우선배당을 먼저 빼고 남은 배당가능이익만큼 해마다 나눠 받는 '
                   '일정의 현재가치입니다. 연도마다 첫 청구일만 보여 줍니다 — 전체는 조서의 «00 배당가능이익 상환» 시트에 있습니다.')
        st.dataframe(pd.DataFrame(rows).style.format({'계약 상환금 (100 기준)': '{:,.4f}', '실제 지급 현재가치 (100 기준)': '{:,.4f}',
                                                     '비율': '{:.2%}'}, na_rep='—'), hide_index=True, use_container_width=True)


def sha_editor(edited, case, errors):
    """주주간계약 입력 — 회차별 표(주식수·원 단위)가 기본이다. 기존 평가파일의 단일 계약 칸도 연다."""
    rev = st.session_state.get('revision', 0)
    rows = list(edited.get('sha_rows') or [])
    mode = st.radio('입력 방식', ['회차별 표 (주식수·원)', '단일 계약 칸 (한 줄짜리 계약)'],
                    index=0 if (rows or not case.facts().get('sha_put_e')) else 1, horizontal=True,
                    key=f'sha_mode_{rev}',
                    help='회차별 표 — 계약·연도·물량별로 행사기간·주당 기준가격·가산율·풋·콜 수량을 한 줄씩 넣습니다. '
                         '연도별 물량을 한 줄로 합치지 마십시오 — 합치면 모든 물량을 모든 기간에 행사할 수 있는 것으로 '
                         '계산됩니다. 미행사 물량을 다음 연도로 넘기지 않습니다.')
    st.caption('풋 = 주식 보유자가 상대방에게 주식을 사 달라고 요구할 권리 · 콜 = 상대방이 주식 보유자에게 주식을 팔라고 '
               '요구할 권리. 계약서의 «매수청구권·매도청구권» 명칭만 보고 정하지 말고, 누가 누구에게 어떤 거래를 요구하는지로 '
               '나누십시오. 콜 권리자가 늘 최대주주인 것은 아닙니다.')
    if mode.startswith('단일'):
        if rows:
            st.info('회차별 표를 비우고 단일 계약 칸으로 계산합니다. 표의 내용은 이 입력을 저장하면 지워집니다.')
        edited['sha_rows'] = []
        st.caption('모든 금액은 계산기준금액 100 기준입니다. 주당 기준가격 칸에 1주당 기준매매가격을 넣습니다.')
        right_period('풋 있음', 'sha_put_s', 'sha_put_e', edited, case, ['sha_put_f', 'sha_put_yield', 'sha_put_cmp'], errors)
        right_period('콜 있음', 'sha_call_s', 'sha_call_e', edited, case, ['sha_call_f', 'sha_call_prem', 'sha_call_cmp', 'sha_call_k'], errors)
        st.markdown('**대상 주식 · 권리 관계**')
        fields(['sha_put_q', 'sha_call_q', 'sha_kill', 'sha_link_q', 'sha_writer', 'pc_order', 'sha_disc', 'sha_side'],
               edited, case)
        if edited.get('sha_disc') == 2:
            field('sha_spread', edited, case)
        return
    if not rows:
        rows = [dict(name='1회차', start='', end='', style='any', price=None, rate=0., put_q=0., call_q=0., kill=0)]
    c1, c2, c3 = st.columns(3)
    show_call = c1.checkbox('풋·콜 조건이 다른 회차가 있다', value=any(r.get(k) not in (None, '') for r in rows
                            for k, _ in SHA_COLS_CALL), key=f'sha_callcols_{rev}',
                            help='콜의 행사기간·기준가격·가산율이 풋과 다르면 켭니다. 비운 칸은 풋과 같습니다.')
    show_mkt = c2.checkbox('회차별 금리·변동성', value=any(r.get(k) is not None for r in rows for k, _ in SHA_COLS_MKT),
                           key=f'sha_mktcols_{rev}', help='비운 칸은 아래 시장자료(공통 곡선·변동성)를 씁니다. 금리를 넣으면 그 회차는 그 '
                                                         '금리를 평평하게 씁니다(무위험은 콜·확률, 풋 할인율은 풋 할인).')
    show_acc = c3.checkbox('가격 가산 기산일이 계약일과 다른 회차', value=any(r.get('acc_from') for r in rows),
                           key=f'sha_acccols_{rev}')
    show_state = st.checkbox('행사·매매가 확정됐거나 결제를 마친 물량, 추가 조건이 있는 물량, 같은 주식·공통 한도를 쓰는 회차가 있다',
                             value=any(r.get(k) not in (None, '', 'open') for r in rows for k, _ in SHA_COLS_STATE),
                             key=f'sha_statecols_{rev}',
                             help='평가기준일 현재 선택권이 남아 있는지를 회차마다 정합니다 — 미행사 / 행사·매매 확정(미결제: 확정 '
                                  '거래만 평가) / 결제 완료(평가 제외) / 추가 조건부(조건 충족·미충족 가정을 직접 고름). 같은 '
                                  '보유주식이나 공통 한도를 쓰는 회차는 묶음 이름과 한도를 넣으면 합계를 점검합니다. 계약 해석은 '
                                  '직접 입력하십시오 — 앱이 정하지 않습니다.')
    cols = (SHA_COLS + (SHA_COLS_CALL if show_call else []) + (SHA_COLS_MKT if show_mkt else [])
            + (SHA_COLS_ACC if show_acc else []) + (SHA_COLS_STATE if show_state else []))
    cfg = {'행사 방식': st.column_config.SelectboxColumn(options=list(SHA_STYLE.values()), required=True),
           '행사 시작일': st.column_config.DateColumn(format='YYYY-MM-DD'),
           '행사 종료일': st.column_config.DateColumn(format='YYYY-MM-DD'),
           '콜 시작일': st.column_config.DateColumn(format='YYYY-MM-DD'),
           '콜 종료일': st.column_config.DateColumn(format='YYYY-MM-DD'),
           '가격 가산 기산일': st.column_config.DateColumn(format='YYYY-MM-DD'),
           '주당 기준가격(원)': st.column_config.NumberColumn(min_value=0., format='%.2f'),
           '콜 기준가격(원)': st.column_config.NumberColumn(min_value=0., format='%.2f'),
           '풋 수량(주)': st.column_config.NumberColumn(min_value=0., format='%.0f'),
           '콜 수량(주)': st.column_config.NumberColumn(min_value=0., format='%.0f'),
           '주기(개월)': st.column_config.NumberColumn(min_value=0., format='%.2f', help='정기 행사일 때만 씁니다.'),
           '가격 가산율(연 %)': st.column_config.NumberColumn(format='%.4f', help='0 이면 고정 행사가격입니다.'),
           '평가 대상 상태': st.column_config.SelectboxColumn(options=list(SHA_STATUS.values())),
           '확정된 거래': st.column_config.SelectboxColumn(options=[v for v in SHA_SIDE.values() if v]),
           '조건부 물량 평가 가정': st.column_config.SelectboxColumn(
               options=[v for v in SHA_COND.values() if v],
               help='근거 없는 확률을 넣지 않습니다. 고른 가정만 평가금액에 들어가고, 조건 충족 시 금액은 차이로 함께 보입니다.'),
           '결제 예정일': st.column_config.DateColumn(format='YYYY-MM-DD'),
           '확정 주당 매매대금(원)': st.column_config.NumberColumn(min_value=0., format='%.2f'),
           '묶음 공통 한도(주)': st.column_config.NumberColumn(min_value=0., format='%.0f'),
           '한쪽 행사 시 상대 권리 소멸': st.column_config.CheckboxColumn(
               help='계약상 한쪽이 행사하면 같은 주식에 붙은 상대 권리가 끝나면 켭니다. 그 물량은 두 권리자가 끝나는 '
                    '상대 권리까지 보고 행사 여부를 정합니다(연계 판단).'),
           '같은 주식 물량(주)': st.column_config.NumberColumn(
               min_value=0., format='%.0f',
               help='풋과 콜이 같은 주식에 붙어 한쪽 행사로 함께 끝나는 물량입니다. 풋·콜 수량이 같으면 비워도 전부로 '
                    '봅니다. 수량이 다르면 반드시 넣으십시오 — 앱이 수량만 보고 자동으로 잇지 않습니다. 나머지 풋·콜 '
                    '물량은 앱이 따로 평가해 더합니다.')}
    frame = st.data_editor(_sha_frame(rows, cols), num_rows='dynamic', hide_index=True, key=f'sha_rows_{rev}',
                           column_config=cfg, use_container_width=True)
    new_rows = _sha_rows_from(frame, cols, rows)
    edited['sha_rows'] = new_rows
    st.session_state.setdefault('_rendered_fields', set()).update({'sha_rows', 'K0', 'face_total', 'd_mat'})
    # 표에서 정해지는 공통 칸 — 평가 종료일(마지막 행사일) · 첫 회차 기준가격 · 계산기준금액
    ends, base, qrows = [], 0., []
    for r in new_rows:
        for k in ('end', 'call_end'):
            if r.get(k): ends.append(r[k])
        _qp, _qc = float(r.get('put_q') or 0), float(r.get('call_q') or 0)
        if r.get('status') == 'settled':
            continue                                   # 결제 완료 — 평가 대상이 아니다
        if r.get('status') == 'agreed':
            _qp, _qc = (_qp, 0.) if r.get('side') == 'put' else (0., _qc)
        _lq = r.get('link_q')
        # 같은 주식 물량 — 넣은 값, 아니면 (상대 권리 소멸이고 수량이 같을 때) 전부, 아니면 겹침 없음으로 보지 않고
        # 계약 대상 주식수는 작은 쪽을 한 번만 센다 (엔진 sha_contract_shares 와 같다).
        _ov = float(_lq) if _lq is not None else min(_qp, _qc)
        _lk = (float(_lq) if _lq is not None else (_qp if abs(_qp - _qc) < 1e-9 else None)) if r.get('kill') else 0.
        base += float(_sha_row_px(r) or 0) * (_qp + _qc - _ov)
        qrows.append({'회차': r.get('name'), '풋 수량': _qp, '콜 수량': _qc,
                      '같은 주식 · 연계 판단': _lk, '풋만': (_qp - _lk) if _lk is not None else None,
                      '콜만': (_qc - _lk) if _lk is not None else None, '계약 대상 주식': _qp + _qc - _ov})
    if ends:
        edited['d_mat'] = max(ends)
    _priced = [r for r in new_rows if _sha_row_px(r) and r.get('status', 'open') in ('open', 'cond')]
    if _priced:
        edited['K0'] = _sha_row_px(_priced[0])
    for r in new_rows:
        # 실적 연동 산식이 있는 회차는 산식이 가격을 정한다 — 산식 입력의 빈칸·오류는 입력 검사가 알린다.
        if not r.get('price') and not r.get('perf') and r.get('status', 'open') in ('open', 'cond'):
            errors.append(f"{r.get('name')}의 주당 기준가격을 입력하십시오.")
    if qrows:
        _qt = pd.DataFrame(qrows)
        _sum = {'회차': '합계', **{c: (_qt[c].sum() if _qt[c].notna().all() else None) for c in _qt.columns if c != '회차'}}
        st.markdown('**물량 나눔** — 같은 주식에 붙어 연계 판단하는 물량과 풋만·콜만 남는 물량 (앱이 나눠 평가한 뒤 더합니다)')
        st.dataframe(pd.concat([_qt, pd.DataFrame([_sum])], ignore_index=True).style.format(
            {c: '{:,.0f}' for c in _qt.columns if c != '회차'}, na_rep='— 같은 주식 물량을 넣으십시오'),
            hide_index=True, use_container_width=True)
    if base > 0:
        edited['face_total'] = base
    st.caption(f'회차 {len(new_rows)}개 · 계산기준금액 {base:,.0f}원 (주당 기준가격 × 대상 주식수의 합) · 평가 종료일 '
               f'{edited.get("d_mat", "—")}. 가격 가산 기산일은 비우면 계약일입니다. 가산율 0% 는 고정 행사가격입니다.')
    st.markdown('**권리 관계** — 풋 권리자는 주식 보유자, 콜 권리자는 상대방입니다. 풋이 행사되면 주식을 사 주는 쪽과 '
                '같은 날 두 권리자가 모두 행사하려 할 때의 우선권을 넣으십시오.')
    fields(['sha_writer', 'pc_order', 'sha_disc', 'sha_side', 'sha_put_cmp', 'acc_basis', 'sha_hold_q'], edited, case)
    if edited.get('sha_disc') == 2:
        field('sha_spread', edited, case)
    st.caption('가산 복리 횟수는 풋·콜 모든 회차에 씁니다. 풋 할인 방식은 회차별 풋 할인율을 비운 회차에 적용합니다.')
    edited['sha_call_cmp'] = edited.get('sha_put_cmp', 1)
    with st.expander('실적 연동 행사가격 계산 (보조)'):
        sha_price_helper(edited, rev)
    with st.expander('이 모델이 다루지 않는 계약 조건'):
        st.markdown('- 한쪽 행사로 상대 권리가 끝나는 것은 **같은 회차 안에서만** 적용합니다. 회차 사이의 소멸·물량 이월은 반영하지 않습니다.\n'
                    '- 같은 날 풋·콜이 함께 행사되면 «동시 행사 우선권»을 따릅니다 — 계약의 통지·우선 조항을 확인하십시오. '
                    '풋 의무자가 상대 주주와 대상회사 연대인 계약의 연계 판단은 지원하지 않습니다(입력 검사가 막습니다).\n'
                    '- 행사일부터 실제 대금 지급일까지의 시차, 동반매도·우선매수권, 상대방의 부도 가능성(할인율로만 반영)은 '
                    '모형에 넣지 않습니다.\n'
                    '- 실적에 따라 달라지는 행사가격은 추정 재무수치로 계산한 가격을 **고정해** 평가합니다. 미래 실적의 '
                    '불확실성은 반영하지 않습니다.')


def sha_price_helper(edited, rev):
    """주당 행사가격 = (직전 회계연도 매출액 − 차감액) × 적용 배수 ÷ 발행주식 총수.

    영업손실률이 기준을 **초과**하면 낮은 배수, 아니면 높은 배수. 추정 재무수치로 계산한 가격을 고정해
    회차 표에 넣는다 — 미래 실적의 불확실성까지 반영한 모형이 아니다.
    """
    st.caption('주당 행사가격 = (산식 재무제표 연도 매출액 − 차감액) × 적용 배수 ÷ 발행주식 총수. 영업손실률이 기준을 '
               '**초과**하면 «초과 시 배수», 아니면 «이하 시 배수». 계약상 산식에 쓰는 재무제표는 보통 행사연도의 **직전** '
               '회계연도입니다 — 두 연도를 따로 적으십시오. 추정 재무수치로 계산한 가격을 고정해 평가합니다 (미래 실적의 '
               '불확실성은 반영하지 않습니다).')
    rows = [r['name'] for r in edited.get('sha_rows') or []]
    # 기준 손실률·배수·차감액은 계약 원문에서 읽어 넣는다 — 앱이 기본값을 두지 않는다.
    base = st.session_state.get('_sha_price_tbl') or [dict(회차=(rows[0] if rows else ''), 행사연도=None, 재무제표연도=None,
                                                          **{'실적 구분': '추정', '매출액(원)': None, '차감액(원)': None,
                                                             '영업손익(원, 손실은 음수)': None, '기준 손실률(%)': None,
                                                             '초과 시 배수': None, '이하 시 배수': None,
                                                             '계약상 발행주식 총수(주)': None})]
    tbl = st.data_editor(pd.DataFrame(base), num_rows='dynamic', hide_index=True, key=f'sha_price_{rev}',
                         column_config={'회차': st.column_config.SelectboxColumn(options=rows),
                                        '실적 구분': st.column_config.SelectboxColumn(options=['확정', '추정'])})
    out, notes = [], {}
    for rec in tbl.to_dict('records'):
        try:
            rev_, ded, op, thr = (float(rec[k]) for k in ('매출액(원)', '차감액(원)', '영업손익(원, 손실은 음수)', '기준 손실률(%)'))
            hi, lo_, n_sh = float(rec['초과 시 배수']), float(rec['이하 시 배수']), float(rec['계약상 발행주식 총수(주)'])
        except (TypeError, ValueError, KeyError):
            continue
        if n_sh <= 0 or rev_ <= 0 or any(pd.isna(x) for x in (rev_, ded, op, thr, hi, lo_, n_sh)):
            continue
        from valuation.legacy import sha_perf_calc
        fy, ey = rec.get('재무제표연도'), rec.get('행사연도')
        pf = dict(rev=rev_, ded=ded, op=op, thr=thr/100, hi=hi, lo=lo_, sh=n_sh, fy=fy, ey=ey, kind=rec.get('실적 구분'))
        px, mult, loss = sha_perf_calc(pf)
        note = (f"실적 연동 — ({rev_:,.0f} − {ded:,.0f}) × {mult:g}배 ÷ {n_sh:,.0f}주 = {px:,.2f}원 · 영업손실률 {loss:.4%} "
                f"{'>' if round(loss, 12) > round(thr/100, 12) else '≤'} {thr:g}% · 행사연도 {ey or '?'} · 실적 {fy or '?'}년 "
                f"({rec.get('실적 구분') or '구분 미입력'})")
        out.append({'회차': rec.get('회차'), '행사연도': ey, '실적연도': fy, '영업손실률': f'{loss:.4%}', '적용 배수': mult,
                    '주당 행사가격(원)': px})
        if rec.get('회차'):
            notes[rec['회차']] = (px, note, pf)
        if ey and fy and str(ey).isdigit() and str(fy).isdigit() and int(fy) >= int(ey):
            st.warning(f'{rec.get("회차") or ey}: 재무제표 연도({fy})가 행사연도({ey})보다 늦지 않습니다 — 계약이 «직전 회계연도» '
                       '를 쓰는지 확인하십시오.')
    if out:
        st.dataframe(pd.DataFrame(out), hide_index=True)
    st.session_state['_sha_price_tbl'] = tbl.to_dict('records')
    if notes and st.button('산식을 회차에 연결하기', key=f'sha_price_apply_{rev}',
                           help='같은 이름의 회차에 산식 입력을 저장합니다 — 주당 기준가격은 산식이 정하고(엑셀 조서에도 '
                                '수식으로 실림), 가격 가산율은 0%(고정 가격)로 둡니다.'):
        for r in edited.get('sha_rows') or []:
            if r['name'] in notes:
                r['price'], r['rate'], r['price_note'], r['perf'] = notes[r['name']][0], 0., notes[r['name']][1], notes[r['name']][2]
        st.session_state['_sha_price_applied'] = True


def curve_editor(key, edited):
    st.session_state.setdefault('_rendered_fields', set()).add(key)
    current = edited.get(key, [])
    initial = pd.DataFrame([[x, y * 100] for x, y in current], columns=['만기(년)', '연이율(%)'], dtype=float)
    st.write(label(key))
    table = st.data_editor(initial, num_rows='dynamic', key=f'{key}_{st.session_state.get("revision", 0)}', hide_index=True,
                           column_config={'만기(년)': st.column_config.NumberColumn(min_value=.001, required=True),
                                          '연이율(%)': st.column_config.NumberColumn(required=True, format='%.6f')})
    if table.values.tolist() != initial.values.tolist():
        edited[key] = [[float(x), float(y) / 100] for x, y in table.values.tolist()]


def input_editor(case, autosave=False):
    st.session_state._rendered_fields = set()
    styles = dict(case.exercise_styles)
    st.session_state._editing_styles = styles
    edited = case.facts().copy()
    draft_errors = []
    inst = edited['inst']
    st.subheader('평가대상 및 기준일')
    if inst == 'SHA' and edited.get('sha_rows'):
        # 회차별 표 — 평가 종료일·계산기준금액·주당 기준가격은 표에서 정해진다 (sha_editor).
        fields(['tranche', 'd_issue', 'd_base'], edited, case)
    elif inst == 'SHA':
        fields(['tranche', 'd_issue', 'd_base', 'd_mat', 'face_total'], edited, case)
    else:
        fields(['tranche', 'view', 'd_issue', 'd_base', 'd_mat', 'face_total'], edited, case)
    if inst == 'RCPS':
        fields(['issue_px', 'par', 'mat_mode', 'div_mode', 'div_basis'], edited, case)
    if inst == 'BW':
        fields(['bw_pay', 'bw_detach'], edited, case)
    if inst == 'SHA':
        st.subheader('계약조건')
        if not edited.get('sha_rows'):
            field('K0', edited, case)
        sha_editor(edited, case, draft_errors)
    else:
        # 권리별로 묶는다 — 각 권리의 계약조건 바로 아래에 그 권리의 회계처리·분리 판단 칸을 둔다.
        _cv = '신주인수권' if inst == 'BW' else '전환권'
        st.subheader('이자·만기 상환' if inst != 'RCPS' else '우선배당·만기 상환')
        fields(['cpn', 'ipay'], edited, case)
        if inst != 'RCPS' or edited.get('mat_mode') == 1:
            fields(['ytm', 'ytm_cmp', 'mat_amt', 'm_less_cpn'], edited, case)
        st.subheader(_cv)
        field('K0', edited, case)
        if right_period('전환·신주인수권 행사 가능', 'cv_s', 'cv_e', edited, case, [], draft_errors):
            cv_style = st.selectbox('전환·신주인수권 행사방식', ['any', 'single'],
                index=1 if styles.get('cv') == 'single' else 0,
                format_func=lambda x: '기간 중 언제든지' if x == 'any' else '특정일에만 행사', key=f'cv_style_{st.session_state.get("revision",0)}')
            if cv_style != styles.get('cv', 'any'):
                styles['cv'] = cv_style
            if cv_style == 'single':
                edited['cv_e'] = edited.get('cv_s')
                st.caption('특정일 행사는 위 행사 시작일을 적용합니다. 종료일은 시작일과 같습니다.')
            else:
                st.caption('전환권은 행사기간 내 모든 계산시점에 행사할 수 있습니다.')
        # 전환가격(행사가격) 조정은 전환권의 조건이다 — 전환 칸 바로 아래에 둔다.
        field('rfx_mode', edited, case)
        if edited.get('rfx_mode'):
            fields(['rfx_cyc', 'rfx_first', 'floor', 'rfx_round', 'K_cap', 'carry'], edited, case)
        with st.expander(f'{_cv} 회계 분류'):
            st.caption('확정 수량의 주식을 확정 금액과 교환하지 못하면(행사가격 조정 등) 자본이 아니라 파생상품부채입니다 '
                       '(1032 문단 16). 고른 분류에 따라 분리 판단과 배분이 달라집니다.')
            field('conv_class', edited, case)
        st.subheader('상환청구권 (투자자)' if inst == 'RCPS' else '조기상환청구권 (투자자 풋)')
        put = right_period('투자자 상환청구권 있음', 'p_s', 'p_e', edited, case, ['p_f', 'p_mode'], draft_errors)
        if put:
            fields(['p_rate'] if edited.get('p_mode') == 'fixed' else ['p_yield', 'p_cmp'], edited, case)
            with st.expander('상환청구금액의 상세 조건'):
                fields(['p_less_cpn', 'p_cpn_add', 'p_sched'], edited, case)
            with st.expander('조기상환청구권 회계처리·분리 판단' if inst != 'RCPS' else '상환청구권 회계처리·분리 판단'):
                st.caption('풋 분리 판단 — 조기상환 행사금액을 자본요소 분리 전 상각후원가와 비교합니다(전환권이 부채면 그 규정을 '
                           '준용해 전환권을 떼기 전 금액). 비교기준(기본 10%)은 기준서가 정한 수치가 아니므로 회계정책으로 '
                           '정합니다. 출발 금액은 0 이하로 두면 앱 자동값(발행금액 100, 발행회사가 별개 콜을 함께 샀으면 + 콜 '
                           '가치)을 쓰고, 실제 회계상 배분액이 다르면 그 금액과 근거를 넣습니다. 평가기준일이 발행일보다 '
                           '뒤이면 다시 판정하지 않고 최초 인식 때의 결론을 이어 씁니다 (1109 B4.3.11).')
                fields(['p_sep', 'p_lost_int', 'split_tol', 'split_base_in', 'split_base_why'], edited, case)
        if inst == 'RCPS':
            dp_editor(edited, draft_errors)
        st.subheader('매도청구권 (콜)' if inst != 'RCPS' else '발행회사 상환권·매도청구권 (콜)')
        if inst == 'RCPS':
            field('issuer_call', edited, case)
            call_on = bool(edited.get('issuer_call'))
        else:
            call_on = st.checkbox('콜 권리 있음', value=edited.get('k_w', 0) > 0, key=f'call_{st.session_state.get("revision", 0)}')
        if call_on:
            old_call = bool(case.facts().get('k_w', 0) or case.facts().get('issuer_call', 0))
            changed_type = inst == 'RCPS' and edited.get('issuer_call') != case.contract.get('issuer_call', 0)
            linked = ['k_method', 'k_split']
            if inst != 'RCPS':
                # 콜 권리자가 첫 선택이다 — 계약서 문구(«발행회사» / «발행회사 또는 발행회사가 지정하는 자» /
                # 발행 시 정해진 제3자)를 그대로 고르면 저장 칸(제3자 지정 · 콜 유형)과 기본 평가방법·회계처리가 따라온다.
                rev = st.session_state.get("revision", 0)
                now_h = call_holder({**DEFAULTS, **case.facts(), **edited})
                holder = st.selectbox('콜 권리자', list(CALL_HOLDERS), index=now_h,
                                      format_func=CALL_HOLDERS.get, key=f'input_call_holder_{rev}',
                                      help='계약서의 매도청구권 행사자 문구를 고르십시오. «발행회사 및 발행회사가 지정하는 자» 이면 '
                                           '지정 가능, 발행 시 최대주주 등 특정인이 정해져 있으면 사전 특정입니다.')
                st.session_state.setdefault('_rendered_fields', set()).update({'k_third', 'k_kind'})
                pol = call_holder_fields(holder, edited.get('model', 'TF'))
                edited['k_third'], edited['k_kind'] = pol['k_third'], pol['k_kind']
                changed_type = holder != call_holder({**DEFAULTS, **case.facts()})
                linked = ['k_method', 'k_split', 'k_sep']
            if not old_call or changed_type:
                third_party = edited.get('issuer_call') == 2 if inst == 'RCPS' else bool(edited['k_third'])
                pol = call_holder_fields(1 if third_party else 0, edited.get('model', 'TF'))
                for key in linked:
                    edited[key] = pol[key]
                    widget_key = f'input_{key}_{st.session_state.get("revision", 0)}'
                    if widget_key in st.session_state:
                        st.session_state[widget_key] = edited[key]
            st.caption('발행회사 본인만 행사하는 콜(발행자 상환권)은 콜 유무 가치 비교·복합내재파생에 포함, 제3자 콜은 '
                       '옵션차익 성분 분리할인(주식결제·현금결제)·별도 금융상품을 초기 설정으로 사용합니다. 콜 권리자를 바꾸면 '
                       '초기 설정으로 다시 맞추고, 기존 평가파일의 선택은 유지합니다. 다른 방법을 선택한 경우 근거를 기록하십시오.')
            fields(['k_s', 'k_e', 'k_f', 'k_prem', 'k_cmp'], edited, case)
            if inst == 'RCPS' and edited.get('issuer_call') == 1:
                edited['k_w'] = 1.
                st.caption('발행자 상환권은 전체 물량에 적용합니다.')
            else:
                fields(['k_w', 'k_hold'], edited, case)
                if edited.get('k_hold'):
                    fields(['k_lock', 'k_lock_put', 'k_lock_w'], edited, case)
                fields(['k_method', 'k_split'], edited, case)
                third_now = edited.get('issuer_call') == 2 if inst == 'RCPS' else bool(edited.get('k_third', DEFAULTS['k_third']))
                policy = (2 if third_now and edited.get('model', 'TF') == 'TF' else 0, 1)
                if (edited.get('k_method'), edited.get('k_split')) != policy:
                    if st.button('현재 기본 평가방법으로 전환', key=f'call_policy_{st.session_state.get("revision", 0)}',
                                 help='발행자 상환권 → 유무가치 비교, 제3자 콜(TF) → 옵션차익 성분 분리할인(주식결제·현금결제) + 전환확률 분해. 값이 달라질 수 있습니다.'):
                        edited['k_method'], edited['k_split'] = policy
                        for key in ['k_method', 'k_split']:
                            st.session_state.pop(f'input_{key}_{st.session_state.get("revision", 0)}', None)
            with st.expander('콜 권리의 상세 조건'):
                fields((['k_kind', 'k_third'] if inst == 'RCPS' else []) +
                       ['k_transfer', 'k_less_cpn', 'k_cpn_add', 'k_sched', 'k_basis', 'pc_order', 'k_conv_resp'], edited, case)
            with st.expander('매도청구권 회계처리'):
                st.caption('발행회사 본인만 행사하면 거래상대방이 그대로인 내재파생상품이라 전환권·조기상환권과 묶을 수 '
                           '있고 (1109 B4.3.4), 제3자가 행사할 수 있으면 별도의 금융상품입니다 (1109 문단 4.3.1). '
                           '콜 권리자를 고르면 기본값이 정해집니다.')
                field('k_sep', edited, case)
            if edited.get('k_w', 0) <= 0:
                draft_errors.append('콜 권리가 있으면 콜 대상 비율을 0%보다 크게 입력하십시오.')
            if edited.get('k_s', 0) > edited.get('k_e', float('inf')):
                draft_errors.append('콜 행사 시작일이 종료일보다 늦습니다.')
        else:
            edited['k_w'] = 0.
        st.subheader('회계 정책 (공통)')
        st.caption('내재파생 분리 정책은 회사가 고르는 회계정책입니다 (한공회 실무사례 30~32쪽). 접근법 1은 서로 '
                   '얽힌 권리(전환권·조기상환권·발행회사 콜)를 먼저 묶고 판단하고, 접근법 2는 권리마다 분리 여부를 '
                   '판단한 뒤 분리 대상끼리 묶습니다. 비슷한 거래에 같은 정책을 쓰십시오. 권리별 처리는 위 각 권리 아래에 있습니다.')
        fields(['acc_basis', 'emb_approach', 'fvpl_whole'], edited, case)
    with st.expander('회계 처리 입력 — 거래원가·전기 장부금액'):
        st.caption('평가금액에는 영향이 없고 회계 참고표(배분·상각·분개)에만 쓰입니다. 전기 장부금액을 넣으면 후속평가 '
                   '분개를, 상환·재매입 지급대가를 넣으면 제거 분개를 만듭니다. 없는 항목은 비워 두십시오(−1).')
        fields(['issue_cost', 'eir_issue', 'prev_host', 'prev_deriv', 'prev_hold', 'cur_periods', 'settle_amt'], edited, case)
    with st.expander('IPO 조건·미반영 권리 메모'):
        field('ipo_on', edited, case)
        if edited.get('ipo_on') and inst == 'SHA':
            # 주주간계약 — 실제 상장(사건)과 주가 기준을 구분한다. 고르지 않으면 계산을 막는다.
            field('sha_ipo_kind', edited, case)
            fields(['ipo_m'] + (['ipo_min'] if edited.get('sha_ipo_kind') == 0 else []), edited, case)
            field('sha_qipo_kill', edited, case)
            if edited.get('sha_ipo_kind') == 1:
                st.caption('상장 시점은 실제 상장일 또는 사용자가 정한 상장 가정일입니다. 그 날 주가와 무관하게 권리가 '
                           '끝납니다 — 상장 시점은 가정이므로 근거를 «출처·평가가정» 에 남기십시오.')
        elif edited.get('ipo_on'):
            fields(['ipo_m', 'ipo_px', 'ipo_mult', 'ipo_min'], edited, case)
            field('ipo_conv', edited, case)
        fields(['unmod_note', 'base_shares', 'dil_shares'], edited, case)
    st.subheader('시장자료 및 계산방법')
    fields(['S0', 'sig', 'div_y', 'model'], edited, case)
    grid_choices = {0: '월', 14: '2주(14일 기준)', 7: '주(7일 기준)'}
    requested_grid = edited.get('grid_days', 0)
    if requested_grid not in grid_choices:
        st.error('저장된 계산 간격이 지원 범위를 벗어났습니다. 월·2주·주 중에서 선택하십시오.')
    grid = st.selectbox('계산 간격 단위', list(grid_choices),
                        index=list(grid_choices).index(requested_grid) if requested_grid in grid_choices else None,
                        format_func=grid_choices.get, key=f'grid_unit_{st.session_state.get("revision", 0)}')
    if grid is not None and (grid or 'grid_days' in edited):
        edited['grid_days'] = float(grid)
    st.session_state._rendered_fields.add('grid_days')
    if grid == 0:
        field('gap_m', edited, case)
    else:
        st.caption('평가기준일과 만기일 사이를 선택한 일수에 가장 가까운 동일 간격으로 나눕니다. 실제 평균 일수는 평가 결과에 표시합니다.')
    fields(['y_type', 'cmp_rf', 'cmp_cr'], edited, case)
    st.caption('비율은 % 단위입니다. 주·2주 간격은 월 간격보다 평가와 조서 생성에 시간이 더 걸립니다.')
    curve_editor('rf_curve', edited); curve_editor('cr_curve', edited)
    with st.expander('금리곡선·BDT 상세 설정'):
        field('rate_mode', edited, case)
        if edited.get('rate_mode') == 'rating':
            fields(['rt_a', 'rt_b', 'rt_tgt'], edited, case); curve_editor('cr_curve_b', edited)
        if inst != 'SHA':
            field('put_bdt', edited, case)
            if edited.get('put_bdt'):
                fields(['bdt_sig', 'bdt_base', 'rvol_rating', 'rvol_tenor', 'rvol_how'], edited, case)
    if inst == 'SHA' and not edited.get('sha_rows'):
        with st.expander('행사금액 경과기간 기준'):
            field('acc_basis', edited, case)
    st.session_state.pop('_editing_styles', None)
    candidate = Case.from_dict(case.to_dict())
    for group in ['contract', 'market', 'method']:
        setattr(candidate, group, {k: v for k, v in edited.items() if section_for(k) == group})
    candidate.exercise_styles = styles
    changed = {k for k in edited if k in case.facts() and edited[k] != case.facts()[k]}
    supplied = {k for k in edited if k not in case.facts() and edited[k] == DEFAULTS.get(k)}
    candidate.imported_defaults = sorted((set(case.imported_defaults) | supplied) - changed)
    pending = candidate.facts() != case.facts() or styles != case.exercise_styles or bool(draft_errors)
    for message in draft_errors:
        st.error(message)
    if autosave:
        st.session_state._input_pending = bool(draft_errors)
    if autosave and pending and not draft_errors:
        save_case(candidate, reset_widgets=False)
    if pending and not autosave:
        st.info('입력 변경사항이 아직 저장되지 않았습니다. 저장 후 평가·조서를 진행하십시오.')
    if not autosave and st.button('입력 저장', type='primary', disabled=bool(draft_errors)):
        save_case(candidate)
    return pending


def evidence_editor(case, pending=False):
    rev = st.session_state.get('revision', 0)
    with st.expander('자료 출처·검토메모'):
        with st.form(f'evidence_{rev}'):
            sources = dict(case.sources)
            sources['market_date'] = st.text_input('시장자료 기준일(YYYY-MM-DD)', sources.get('market_date', ''))
            for key in ['S0', 'sig', 'rf_curve', 'cr_curve', 'contract']:
                title = '계약서 위치·조항' if key == 'contract' else f'{label(key)} 출처·산출근거'
                sources[key] = st.text_input(title, sources.get(key, ''))
            notes = st.text_area('검토메모', case.notes)
            if st.form_submit_button('출처·메모 저장', disabled=pending):
                candidate = Case.from_dict(case.to_dict())
                candidate.sources, candidate.notes = sources, notes
                save_case(candidate)
    with st.expander('평가자 메모 · 선택 기록'):
        from judgment_ui import review_topics
        review_topics(case)
    with st.expander('계약조건과 다른 평가가정'):
        st.caption('계약 원본은 유지하고 계산에 사용할 별도 가정과 근거를 기록합니다.')
        for idx, row in enumerate(case.assumptions):
            st.write(f"{label(row['field'])}: {display_value(row['field'], row['value'], case.contract.get('d_issue'), case.facts().get('inst'))} · {row['rationale']}")
            if st.button('이 가정 삭제', key=f'del_assumption_{idx}_{rev}', disabled=pending):
                candidate = Case.from_dict(case.to_dict()); candidate.assumptions.pop(idx); save_case(candidate)
        options = sorted(k for k in case.facts() if k != 'inst' and TYPES[k] is not list)
        if options:
            key = st.selectbox('가정으로 변경할 항목', options, format_func=label, key=f'assumption_key_{rev}')
            proposed = case.effective()
            field(key, proposed, case, prefix='assumption')
            reason = st.text_input('가정 변경 근거', key=f'assumption_reason_{rev}')
            if st.button('가정 반영', disabled=pending or not reason.strip() or key not in proposed):
                candidate = Case.from_dict(case.to_dict())
                candidate.assumptions = [r for r in candidate.assumptions if r['field'] != key]
                candidate.assumptions.append(dict(field=key, value=proposed[key], rationale=reason)); save_case(candidate)


def sha_result_panel(run):
    """주주간계약 결과 — 회차별 풋·콜과 합계, 순액(관점). 계산은 하지 않고 실행 결과만 보여 준다."""
    t, tot = run.terms, run.summary['amounts_total']
    side = int(getattr(t, 'sha_side', 0))
    net = (tot['call'] - tot['put']) if side == 0 else (tot['put'] - tot['call'])
    st.metric(('순액 — 콜 권리자 관점 (콜 − 풋)' if side == 0 else '순액 — 풋 권리자 관점 (풋 − 콜)') + ' (원)', f'{net:,.0f}',
              help='두 권리는 보유자가 달라 각자 총액으로 싣습니다. 순액은 참고값입니다.')
    rows = run.summary.get('sha_rows')
    if rows:
        frame = pd.DataFrame(rows)
        total = {'회차': '합계', '풋 전액': frame['풋 전액'].sum(), '콜 전액': frame['콜 전액'].sum(),
                 '풋 수량': frame['풋 수량'].sum(), '콜 수량': frame['콜 수량'].sum(),
                 **({'같은 주식 물량 (연계 판단)': frame['같은 주식 물량 (연계 판단)'].sum()}
                    if '같은 주식 물량 (연계 판단)' in frame else {})}
        frame = pd.concat([frame, pd.DataFrame([total])], ignore_index=True)
        st.dataframe(frame.style.format({'주당 기준가격': '{:,.2f}', '풋 수량': '{:,.0f}', '콜 수량': '{:,.0f}',
                                         '같은 주식 물량 (연계 판단)': '{:,.0f}',
                                         '풋 1주당': '{:,.2f}', '콜 1주당': '{:,.2f}', '풋 전액': '{:,.0f}',
                                         '콜 전액': '{:,.0f}'}, na_rep=''), hide_index=True, use_container_width=True)
        st.caption('회차마다 따로 계산해 더했습니다. 연도별 미행사 물량을 다음 회차로 넘기지 않습니다. 같은 주식 물량은 '
                   '두 권리자가 끝나는 상대 권리까지 보고 행사 여부를 정했고, 나머지 풋·콜 물량은 따로 평가했습니다. '
                   '1주당 금액은 풋·콜 각자의 수량으로 나눈 값입니다.')
        _cond = [r for r in rows if r.get('평가 대상 상태') == '추가 조건부']
        if _cond:
            st.markdown('**추가 조건부 물량 — 조건 충족 전·후 차이 (평가금액에는 고른 가정만 들어 있음)**')
            st.dataframe(pd.DataFrame([{'회차': r['회차'], '반영한 가정': '조건 충족' if r['반영'] else '조건 미충족',
                                        '평가금액 풋': r['풋 전액'], '평가금액 콜': r['콜 전액'],
                                        '조건 충족 시 풋': r['조건 충족 시 풋 전액'], '조건 충족 시 콜': r['조건 충족 시 콜 전액'],
                                        '차이 풋': r['조건 충족 시 풋 전액'] - r['풋 전액'],
                                        '차이 콜': r['조건 충족 시 콜 전액'] - r['콜 전액']} for r in _cond]).style.format(
                {c: '{:,.0f}' for c in ['평가금액 풋', '평가금액 콜', '조건 충족 시 풋', '조건 충족 시 콜', '차이 풋', '차이 콜']}),
                hide_index=True, use_container_width=True)
            st.caption('조건 충족 가능성을 확률로 반영한 값이 아닙니다. 충족 가정과 미충족 가정의 차이를 보여 줄 뿐입니다.')
    _perf_rows = [x for x in (run.terms.sha_rows or []) if isinstance(x, dict) and x.get('perf')]
    if _perf_rows:
        with st.expander('실적 연동 행사가격 — 매출·손실률 민감도 (선택한 회차만 다시 계산)'):
            st.caption('매출액을 ±10%·±20% 바꾸고, 영업손실률을 입력값 · 기준과 같음 · 기준 + 0.1%p 로 바꿔 주당 행사가격과 '
                       '그 회차의 풋·콜 금액을 다시 계산합니다. 확률을 붙이지 않으며 평가금액에는 반영되지 않습니다.')
            _nm = st.selectbox('회차', [x.get('name') for x in _perf_rows], key='sha_perf_sens_row')
            if st.button('민감도 계산', key='sha_perf_sens_btn'):
                from valuation.legacy import sha_perf_sensitivity
                with st.spinner('회차를 다시 계산합니다'):
                    st.session_state['_sha_perf_sens'] = (_nm, sha_perf_sensitivity(run.terms, _nm))
            _res = st.session_state.get('_sha_perf_sens')
            if _res and _res[0] == _nm:
                st.dataframe(pd.DataFrame(_res[1], columns=['매출 배율', '손실률 시나리오', '영업손실률', '적용 배수',
                                                            '주당 행사가격(원)', '풋 (원)', '콜 (원)']).style.format(
                    {'매출 배율': '{:.0%}', '영업손실률': '{:.2%}', '적용 배수': '{:g}', '주당 행사가격(원)': '{:,.2f}',
                     '풋 (원)': '{:,.0f}', '콜 (원)': '{:,.0f}'}), hide_index=True, use_container_width=True)
    recon = run.summary.get('sha_recon')
    if recon:
        st.markdown('**수량 대사 — 평가 대상과 제외 물량**')
        st.dataframe(pd.DataFrame(recon).style.format({'풋 주식수': '{:,.0f}', '콜 주식수': '{:,.0f}', '계약 대상 주식': '{:,.0f}'}),
                     hide_index=True, use_container_width=True)
        _h = float(getattr(t, 'sha_hold_q', -1.0))
        if _h >= 0:
            _used = sum(r['계약 대상 주식'] for r in recon)
            st.caption(f'평가기준일 보유주식 {_h:,.0f}주 · 평가하는 회차의 대상 주식 {_used:,.0f}주 · 이 계약 밖 보유 {_h - _used:,.0f}주')


def split_panel(run):
    """평가 직후 — 내재파생 분리 판단의 결론과 이용자 설정이 맞는지 한 표로 (전환사채·신주인수권부사채·상환전환우선주).

    종전에는 «분석 도구 → 상세 계산·회계 참고표 → 판단·근거» 까지 들어가야 보였다. 근거 문장과 수치는 그대로 그 탭에 있다.
    """
    from valuation import legacy as L
    t, r = run.terms, run.raw
    if t.inst == 'SHA':
        return
    key = run.summary['calculation_key']
    cache = st.session_state.get('_split_panel')
    if not cache or cache[0] != key:
        args = (t, r['full'], r['b0'], r['b1'], r['b2'], r['ca'])
        ah = L.acc_host(*args)
        sp = L.split_test(*args, [] if ah is None else L.eir_table(t, ah)[1])
        rows = []
        for k, nm in (('warrant', '신주인수권'), ('put', inst_text(t, '조기상환청구권')), ('call', '매도청구권')):
            d = sp.get(k)
            if not d or not d.get('있음'):
                continue
            if k == 'warrant':
                rows.append({'권리': nm, '수치 판정': d['결론'], '이용자 설정 (회계처리)': '—', '판정과 설정': '—'})
                continue
            cho = dict(L.split_policy_rows(t, k))['선택한 처리 (이 조서의 회계처리)']
            ok = L.split_compare(t, k, d).split('→', 1)[-1].strip()
            rows.append({'권리': nm, '수치 판정': d['결론'], '이용자 설정 (회계처리)': cho, '판정과 설정': ok})
        cache = (key, rows)
        st.session_state['_split_panel'] = cache
    if not cache[1]:
        return
    st.markdown('**내재파생 분리 판단 — 평가 직후 요약**')
    st.dataframe(pd.DataFrame(cache[1]), hide_index=True, use_container_width=True)
    if any(x['판정과 설정'].startswith('검토 필요') for x in cache[1]):
        st.warning('수치 판정과 이용자 설정이 다른 권리가 있습니다. 계약과 회계정책을 확인하고 근거를 남기십시오.')
    st.caption('근거 문장·검토용 수치는 아래 분석 도구 «상세 계산·회계 참고표» → 판단·근거에 있습니다. '
               '설정은 입력화면의 각 권리 아래 «회계처리·분리 판단» 에서 바꿉니다.')


def day1_panel(run, case):
    """최초 인식 — 모형값 / 거래가격 100 / 차이, 그리고 차이 처리 선택 (투자자·발행자)."""
    day1 = run.summary.get('day1')
    if not day1:
        return
    from sources_ui import show as source
    c1, c2 = st.columns([5, 1])
    if abs(day1['diff']) < 0.005:
        c1.success(f"최초 인식 — {day1['nums']} · 보정되어 차이가 없습니다.")
        with c2:
            source('day1')
        return
    c1.warning(f"최초 인식 — {day1['nums']}. 거래가격이 공정가치라면 먼저 평가를 거래가격에 보정하십시오 "
               "(주가 역산 등 · 1113 문단 64). 보정하지 않으면 아래에서 차이 처리를 고릅니다.")
    with c2:
        source('day1')
    from judgment_ui import day1_checks
    day1_checks(run)
    if day1.get('view') == 'issuer':
        # 세 갈래(자본 흡수 · 당기손익 · 이연)를 나란히 보이고 이 평가가 어느 쪽인지 표시한다.
        _cases = issuer_day1_cases(dict(hybrid=bool(day1.get('choice')), pl=day1.get('mode') == '당기손익'))
        st.dataframe(pd.DataFrame([[inst_text(run.terms, k), v, on] for k, v, on in _cases],
                                  columns=['최초 인식 차이의 세 가지 구분', '처리', '이 평가']),
                     use_container_width=True, hide_index=True)
    if not day1.get('choice', True):
        # 발행자 · 전환권 자본 — 차이는 잔여인 자본요소(전환권대가)에 흡수된다.
        st.caption(inst_text(run.terms, '전환권이 자본이므로 차이는 잔여인 자본요소(전환권대가)에 흡수됩니다 (1032 문단 31). '
                                    '최초 인식 손익은 생기지 않습니다.'))
        return
    rev = st.session_state.get('revision', 0)
    eff = case.effective()
    d1, d2, d3 = st.columns([2, 4, 1])
    mode = d1.selectbox('최초 인식 차이 처리', [0, 1], index=int(eff.get('d1_pl', 0)), key=f'd1_pl_{rev}',
                        format_func=lambda x: '이연 (기본)' if x == 0 else '당기손익')
    reason = d2.text_input('당기손익 근거 — 관측 가능한 시장자료만 사용했다는 근거 (1109 B5.1.2A(1))',
                           value=eff.get('d1_reason', ''), key=f'd1_reason_{rev}', disabled=mode == 0)
    if d3.button('저장', key=f'd1_save_{rev}', disabled=mode == 1 and not reason.strip()):
        candidate = Case.from_dict(case.to_dict())
        candidate.method['d1_pl'] = int(mode)
        candidate.method['d1_reason'] = reason.strip() if mode == 1 else ''
        save_case(candidate)
    if day1.get('view') == 'issuer':
        st.caption(("현재 처리 — 당기손익: 주계약을 공정가치로 두고 차이를 «최초 인식 손익» 으로 분개합니다. "
                    if day1['mode'] == '당기손익' else
                    "현재 처리 — 이연: 주계약 장부금액에서 차이를 빼 두고 유효이자율로 기간에 걸쳐 인식합니다. ")
                   + '회계기준원 질의회신 2019-I-KQA018. 원인 점검 항목은 위 «원인 점검 4항목» 에 있습니다.')
        return
    st.caption(f"현재 분개 표기 — {'금융자산평가이익(손실) — 최초 인식 차이' if day1['mode'] == '당기손익' else '최초 인식 차이 — 이연'}. "
               '원인 점검 항목은 위 «원인 점검 4항목» 에 있습니다.')


def main():
    if not st.session_state.get('_app_embedded'):
        st.set_page_config(page_title='복합금융상품 평가', layout='wide')
    st.title('복합금융상품 평가')
    st.caption('입력·시장자료 → 평가·분석 → 조서 출력')
    with st.sidebar:
        st.subheader('평가파일')
        with st.expander('새 평가 만들기'):
            with st.form('new_case'):
                name = st.text_input('평가 건명')
                instrument = st.selectbox('평가 상품', ['RCPS', 'CB', 'BW', 'SHA'])
                if st.form_submit_button('빈 입력안 만들기'):
                    if name.strip():
                        contract = dict(inst=instrument, rfx_mode=0, cpn=0., cv_s=99., cv_e=0., p_s=99., p_e=0., k_w=0.,
                                        sha_put_s=99., sha_put_e=0., sha_call_s=99., sha_call_e=0.)
                        install_case(Case(name=name.strip(), contract=contract, method=dict(model='TF', view='holder', gap_m=1.)))
                    else:
                        st.error('평가 건명을 입력하십시오.')
        upload = st.file_uploader('평가파일 불러오기', type='json')
        previous_upload = st.file_uploader('전기 평가파일(선택)', type='json', key='previous_upload')
        st.caption('전기 자료를 복사한 경우 새 기준일의 주당가치·변동성·금리를 확인하십시오.')
    if upload:
        digest = hashlib.sha256(upload.getvalue()).hexdigest()
        if st.session_state.get('upload_digest') != digest:
            try:
                install_case(read_case(upload.getvalue(), upload.name.removesuffix('.json')))
                st.session_state.upload_digest = digest
            except (ValueError, TypeError) as exc:
                st.error(f'평가파일을 읽을 수 없습니다: {exc}'); st.stop()
    previous = None
    if previous_upload:
        try:
            previous = read_case(previous_upload.getvalue(), previous_upload.name)
        except (ValueError, TypeError) as exc:
            st.error(f'전기 평가파일을 읽을 수 없습니다: {exc}')
    if previous:
        with st.sidebar:
            if st.button('전기 입력을 새 평가로 복사'):
                cloned = Case.from_dict(previous.to_dict())
                cloned.name += ' — 갱신'
                cloned.notes += '\n전기 입력 복사: 평가기준일과 시장자료, 계약 변경 여부를 확인할 것.'
                install_case(cloned)
    if 'case' not in st.session_state:
        st.info('왼쪽에서 새 평가를 만들거나 기존 평가파일을 불러오십시오.'); st.stop()
    case = st.session_state.case
    st.subheader(f'{case.name} · {case.contract.get("inst", "")}')
    # One active step only: hidden analyses never execute on input changes.
    if st.session_state.get('_workflow_stage') not in (None, '입력·시장자료', '평가·분석', '조서 출력'):
        st.session_state['_workflow_stage'] = '입력·시장자료'
    stage = st.radio('평가 진행', ['입력·시장자료', '평가·분석', '조서 출력'],
                     index=0, horizontal=True, key='_workflow_stage')
    st.sidebar.download_button('평가파일 저장', json.dumps(case.to_dict(), ensure_ascii=False, indent=2), '평가입력.json', 'application/json')
    st.sidebar.caption('입력은 현재 세션에 반영됩니다. 종료 전 평가파일을 저장하십시오.')
    pending = st.session_state.get('_input_pending', False)
    if pending and stage != '입력·시장자료':
        st.warning('입력화면에 저장되지 않은 오류가 있습니다. 입력·시장자료에서 확인하십시오.')
    if stage == '입력·시장자료':
        area = st.radio('입력 항목', ['계약·평가 입력', '주가·변동성·금리 자료', '출처·평가가정'], horizontal=True, key='_input_area')
        if area == '계약·평가 입력':
            pending = input_editor(case, autosave=True)
        elif area == '주가·변동성·금리 자료':
            from market_tools_ui import main as market_tools
            market_tools(case)
        else:
            evidence_editor(case)
        return
    issues = inspect_case(case)
    errors = [i for i in issues if i.severity == 'error']
    if stage == '평가·분석':
        if errors:
            st.error(f'입력 오류 {len(errors)}건을 수정해야 평가할 수 있습니다.')
            st.dataframe(pd.DataFrame(issue_rows(errors)), hide_index=True)
        if pending:
            st.warning('저장하지 않은 입력이 있습니다. 입력 저장 후 실행하십시오.')
        if st.button('현재 입력으로 평가', type='primary', disabled=bool(errors) or pending):
            try:
                with st.spinner('입력한 조건으로 평가 중입니다.'):
                    old = st.session_state.get('run')
                    if old and old.summary['calculation_key'] == calculation_key(case):
                        st.session_state.run = refresh_run(old, case)
                    else:
                        st.session_state.run = calculate(case)
                        st.session_state.pop('analysis', None)
                    st.session_state.pop('bundle', None)
            except (ValueError, ArithmeticError) as exc:
                st.error(f'평가를 완료하지 못했습니다: {exc}')
        run = st.session_state.get('run')
        current = run is not None and not errors and run.summary['calculation_key'] == calculation_key(case)
        if run:
            if not current:
                st.warning('아래는 변경 전 입력의 결과입니다. 현재 입력으로 다시 평가해야 조서를 저장할 수 있습니다.')
            st.caption(f"평가기준일 {run.terms.d_base} · {run.terms.n:,}구간 · 계산 {run.summary['calculation_seconds']:.3f}초")
            values = run.summary['amounts_total']
            keys = ['put', 'call'] if run.terms.inst == 'SHA' else ['whole_before_call', 'call_deduction', 'net']
            for col, key in zip(st.columns(len(keys)), keys):
                col.metric(AMOUNT_LABELS[key] + ' (원)', f'{values[key]:,.0f}')
                if run.summary['amounts_per_share']:
                    col.caption(f"1주당 {run.summary['amounts_per_share'][key]:,.2f}원")
            if run.terms.inst == 'SHA':
                sha_result_panel(run)
            day1_panel(run, case)
            split_panel(run)
            dp_panel(run)
            with st.expander('구성요소·원금 100 기준 상세' if run.terms.inst != 'SHA' else '계산기준금액 100 기준 상세'):
                st.caption('순차 차감에 따른 참고값입니다. 회계상 인식액을 확정한 표가 아닙니다.')
                st.dataframe(pd.DataFrame([{'항목': AMOUNT_LABELS[k], '총액(원)': values[k], '원금 100 기준': v}
                                          for k, v in run.summary['amounts_100'].items()]), hide_index=True)
            st.subheader('산술 검산')
            st.dataframe(pd.DataFrame([{'검사': r['name'], '결과': '통과' if r['passed'] else '차이 발생', '범위': r['detail']}
                                      for r in run.summary['checks']]), hide_index=True)
            st.subheader('확인할 사항')
            numerical_issues = [i for i in run.issues if i.code not in {'source', 'legacy_defaults', 'engine_defaults', 'judgement_scope', 'market_date'}]
            st.dataframe(pd.DataFrame(issue_rows(numerical_issues)), hide_index=True)
            with st.expander('추가 분석 — 선택한 변수만 계산'):
                variable = st.selectbox('민감도 변수', ['S0', 'sig', 'rf_curve', 'cr_curve'], format_func=label)
                magnitude = st.number_input('변화폭(주당가치는 %, 변동성·금리는 %p)', min_value=.01, max_value=50. if variable == 'S0' else 10., value=10. if variable == 'S0' else 1.)
                st.caption('감소·증가 조건 각 1회, 총 2회 추가 평가합니다. 금리곡선은 모든 만기의 금리를 같은 폭으로 이동합니다.')
                if st.button('민감도 계산', disabled=not current or pending):
                    try:
                        with st.spinner('민감도 2개 조건 계산 중입니다.'):
                            st.session_state.analysis = sensitivity(run, variable, magnitude)
                    except (ValueError, ArithmeticError) as exc:
                        st.error(str(exc))
                analysis = st.session_state.get('analysis')
                if analysis and analysis['calculation_key'] == run.summary['calculation_key'] and current:
                    st.write(f"계산된 변수: {label(analysis['variable'])} / 변화폭: ±{analysis['change']:g}")
                    table = pd.DataFrame(analysis['rows']).rename(columns=AMOUNT_LABELS)
                    st.dataframe(table, hide_index=True)
                    st.download_button('민감도 결과 저장', table.to_csv(index=False).encode('utf-8-sig'), '민감도.csv', 'text/csv')
        if current:
            analysis_mode = st.selectbox('분석 도구', ['결과 요약', '상세 계산·회계 참고표'])
            if analysis_mode == '상세 계산·회계 참고표':
                from application import detailed
                detailed(run)
        if previous:
            with st.expander('전기 대비 입력 변경'):
                st.dataframe(pd.DataFrame([{'항목': label(r['field']), '전기': str(r['previous']), '당기': str(r['current'])}
                                          for r in compare_cases(previous, case)]), hide_index=True)
    if stage == '조서 출력':
        run = st.session_state.get('run')
        current = run is not None and not errors and run.summary['calculation_key'] == calculation_key(case)
        st.download_button('평가 입력파일 저장', json.dumps(case.to_dict(), ensure_ascii=False, indent=2), '평가입력.json', 'application/json')
        st.subheader('계산 조서')
        st.write('기본 조서: 평가 결과, 적용 입력, 금리·변동성 자료, 평가가정, 조서 정보(계산 기록·자료 출처·확인할 사항)')
        st.caption('산술 검산과 계산 점검은 앱이 평가할 때 돌립니다. 조서에는 싣지 않고, 이상이 있으면 조서를 만들지 않습니다.')
        option = st.radio('조서 구성', ['기본 값 조서', '상세 계산 값 조서', '상세 계산 수식 조서'])
        accounting = st.checkbox('회계처리·분개·상각표 포함 (초안)', value=False)
        judgment = st.checkbox('판단·근거 시트 포함 (분리 판정·평가자 판단·근거 원문, 상세 조서는 해설·분리 판단 시트 포함)', value=True)
        if option != '기본 값 조서':
            st.info('상세 조서는 모든 계산 노드를 포함합니다. 주 간격의 장기 평가에서는 생성에 시간이 걸릴 수 있습니다.')
        if not current:
            st.warning('현재 입력으로 평가·분석 단계에서 먼저 평가를 실행하십시오. 입력을 바꾼 뒤에는 재평가해야 조서를 만들 수 있습니다.')
        elif pending:
            st.warning('입력·시장자료 단계에서 저장되지 않은 입력을 확인하십시오.')
        if current:
            st.caption(f'현재 평가: {run.terms.n:,}구간 · 평균 {run.summary["grid"]["average_days"]:.4f}일. 조서도 같은 격자를 사용합니다.')
        if st.button('조서 생성', disabled=not current or pending):
            st.session_state.pop('bundle', None)
            st.session_state.pop('bundle_key', None)
            try:
                with st.spinner('조서를 생성하고 입력·결과 기록을 묶는 중입니다.'):
                    st.session_state.bundle = export_bundle(run, formula=option == '상세 계산 수식 조서', detail=option != '기본 값 조서', previous=previous, accounting=accounting, judgment=judgment)
                    st.session_state.bundle_key = (run.case.fingerprint(), option, accounting, judgment, previous.fingerprint() if previous else None)
            except (ValueError, ArithmeticError) as exc:
                st.error(f'조서를 생성하지 못했습니다: {exc}')
            except (MemoryError, OSError, RuntimeError, OverflowError) as exc:
                st.error(f'조서 생성 중 서버 자원 또는 파일 처리 오류가 발생했습니다 ({type(exc).__name__}). '
                         '상세 계산 값 조서로 저장하거나 계산 간격을 늘리고 재평가하십시오.')
        bundle_key = (case.fingerprint(), option, accounting, judgment, previous.fingerprint() if previous else None) if current else None
        if current and not pending and st.session_state.get('bundle_key') == bundle_key and 'bundle' in st.session_state:
            data = st.session_state.bundle
            st.success(f'조서 생성 완료 · {len(data) / 1024 / 1024:.1f} MB. 아래에서 Excel 파일이나 전체 묶음을 저장하십시오.')
            st.download_button('평가 조서 묶음 저장', data, '평가조서.zip', 'application/zip')
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = next(n for n in z.namelist() if n.endswith('.xlsx'))
                st.download_button('Excel 조서만 저장', z.read(name), '평가조서.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == '__main__':
    main()
