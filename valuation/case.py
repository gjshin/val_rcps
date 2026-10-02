"""Versioned case input shared by conversation tools and the review app.

Contract facts never change when an analyst applies a modelling assumption.
Legacy numerical defaults are retained explicitly and disclosed on migration.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, get_type_hints

from .legacy import Terms

SCHEMA = "valuation-case/2"
MARKET = frozenset({
    "S0", "sig", "rf_curve", "cr_curve", "cr_curve_b", "div_y", "rate_mode",
    "rt_a", "rt_b", "rt_tgt", "cr_src", "s0_src", "s0_date", "s0_raw", "s0_adj",
    "s0_splits", "ticker", "cmp_rf", "cmp_cr", "y_type", "bdt_sig", "bdt_base",
    "rvol_rating", "rvol_tenor", "rvol_how", "sha_spread",
})
METHOD = frozenset({
    "d_base", "model", "gap_m", "grid_days", "carry", "view", "conv_class", "emb_approach", "p_sep", "k_sep",
    "k_method", "k_split", "put_bdt", "fvpl_whole", "bs_target", "bs_net",
    "prev_hold", "d1_pl", "d1_reason", "prev_host", "prev_deriv", "eir_issue", "cur_periods", "settle_amt",
    "split_tol", "split_base_in", "split_base_why",
})
DERIVED = frozenset({"T", "n", "elapsed_m", "rem_m", "scen_md5"})
FIELDS = frozenset(Terms.__dataclass_fields__) - DERIVED
REQUIRED = frozenset({
    "inst", "d_issue", "d_mat", "d_base", "S0", "K0", "sig", "rf_curve",
    "cr_curve", "face_total", "model", "view", "gap_m", "rfx_mode", "cpn",
    "cv_s", "cv_e", "p_s", "p_e", "k_w",
})
RCPS_REQUIRED = frozenset({"issue_px", "par", "mat_mode", "div_mode", "div_basis", "issuer_call"})
RIGHT_KINDS = {
    "distributable_profit": "배당가능이익에 따른 상환 제약",
    "unpaid_dividend_extension": "미지급 배당에 따른 존속기간 연장",
    "event_refixing": "저가발행·M&A 등 사건형 전환가액 조정",
    "contingent_purchase": "계약위반 등에 따른 조건부 매수청구권",
    "liquidation_waterfall": "청산우선권·참가권·종류주식 간 배분",
    "dilution": "전환 시 자본구조 변화·희석",
    "settlement_delay": "행사일부터 실제 지급일까지의 시차",
    "other": "기타 별도 검토 권리",
}


def section_for(key: str) -> str:
    return "market" if key in MARKET else "method" if key in METHOD else "contract"


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    field: str
    message: str
    impact: str = ""
    action: str = ""


@dataclass
class Case:
    name: str
    schema: str = SCHEMA
    contract: dict = field(default_factory=dict)
    market: dict = field(default_factory=dict)
    method: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)
    additional_rights: list = field(default_factory=list)
    judgments: dict = field(default_factory=dict)
    exercise_styles: dict = field(default_factory=dict)
    contract_review: dict = field(default_factory=dict)
    contract_scenarios: list = field(default_factory=list)
    cashflow_scenarios: list = field(default_factory=list)
    market_evidence: dict = field(default_factory=dict)
    review_controls: dict = field(default_factory=dict)
    imported_defaults: list = field(default_factory=list)
    notes: str = ""
    memos: dict = field(default_factory=dict)
    calibration: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, obj: dict) -> "Case":
        if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
            raise ValueError("V2 평가파일이 아닙니다. 구 시나리오는 가져오기 기능을 사용하십시오.")
        unknown = set(obj) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"알 수 없는 프로젝트 항목: {', '.join(sorted(unknown))}")
        case = cls(**copy.deepcopy(obj))
        _migrate_carry(case)
        if not isinstance(case.name, str) or not case.name.strip():
            raise ValueError("평가 건명을 입력하십시오.")
        for key in ("contract", "market", "method", "sources", "judgments", "market_evidence", "review_controls", "exercise_styles", "contract_review", "memos", "calibration"):
            if not isinstance(getattr(case, key), dict):
                raise ValueError(f"{key}: 객체 형식이 필요합니다.")
        for key in ("assumptions", "additional_rights", "imported_defaults", "contract_scenarios", "cashflow_scenarios"):
            if not isinstance(getattr(case, key), list):
                raise ValueError(f"{key}: 목록 형식이 필요합니다.")
        if not all(isinstance(k, str) and k in FIELDS for k in case.imported_defaults):
            raise ValueError("기본값 보충 목록에는 유효한 항목명만 기록할 수 있습니다.")
        if not isinstance(case.notes, str):
            raise ValueError("notes에는 문자열이 필요합니다.")
        for key, row in case.memos.items():
            if not isinstance(key, str) or not isinstance(row, dict) or set(row) != {'decision', 'reason'} or not all(isinstance(v, str) for v in row.values()):
                raise ValueError('평가자 판단 기록은 판단(decision)과 근거(reason) 문자열이 필요합니다.')
        validate_calibration(case.calibration)
        from .exercise import validate_styles
        validate_styles(case.exercise_styles)
        from .contract_intake import validate_review
        validate_review(case.contract_review)
        from .cashflows import validate_shape
        names = set()
        for scenario in case.cashflow_scenarios:
            validate_shape(scenario)
            if scenario['name'] in names:
                raise ValueError('현금흐름 분석 이름이 중복됩니다.')
            names.add(scenario['name'])
        from .controls import validate_controls
        validate_controls(case.review_controls)
        if set(case.market_evidence) - {'sig'}:
            raise ValueError('지원하지 않는 시장자료 증빙 항목입니다.')
        if 'sig' in case.market_evidence:
            from .market_data import validate_pack
            validate_pack(case.market_evidence['sig'])
        from .contract_analysis import FIELDS as SCENARIO_FIELDS, ROW_FIELDS
        names = set()
        for row in case.contract_scenarios:
            if not isinstance(row, dict) or set(row) != SCENARIO_FIELDS or not isinstance(row['name'], str) or not row['name'].strip() or row['name'] in names:
                raise ValueError('계약조건 분석의 이름·필수 항목·중복 여부를 확인하십시오.')
            if not isinstance(row['schedule'], list) or any(not isinstance(r, dict) or set(r) != ROW_FIELDS for r in row['schedule']):
                raise ValueError('계약조건 분석의 일정 형식을 확인하십시오.')
            if not isinstance(row['rationale'], str) or row['terminal'] not in {'redeem', 'convert'} or row['conversion_arrears'] not in {'pay', 'forfeit'}:
                raise ValueError('계약조건 분석의 근거·종료 처리·배당 처리를 확인하십시오.')
            for key in ['opening_unpaid', 'arrears_rate']:
                if not _finite(row[key]) or row[key] < 0:
                    raise ValueError('계약조건 분석의 미지급 배당·가산이율에는 0 이상의 유한한 숫자가 필요합니다.')
            try:
                dt.date.fromisoformat(row['end_date'])
                for event in row['schedule']:
                    dt.date.fromisoformat(event['date'])
                    if type(event['redemption_allowed']) is not bool or any(not _finite(event[k]) or event[k] < 0 for k in ROW_FIELDS - {'date', 'redemption_allowed'}):
                        raise ValueError('계약조건 일정의 금액과 상환 허용 여부를 확인하십시오.')
            except (ValueError, TypeError) as exc:
                raise ValueError('계약조건 일정의 날짜·숫자·상환 허용 형식을 확인하십시오.') from exc
            names.add(row['name'])
        from .evidence import TOPICS
        topic_ids = {t['id'] for t in TOPICS}
        required_review = {'status', 'conclusion', 'rationale', 'reviewer', 'contract_clause', 'reviewed_at', 'input_key', 'source_version'}
        for key, row in case.judgments.items():
            if key not in topic_ids or not isinstance(row, dict) or set(row) != required_review or not all(isinstance(v, str) for v in row.values()):
                raise ValueError('판단 검토기록의 주제·필수 항목·문자열 형식을 확인하십시오.')
            if row['status'] not in {'검토 중', '검토 완료', '해당 없음'} or not row['reviewer'].strip() or not row['rationale'].strip() or (row['status'] == '검토 완료' and not row['conclusion'].strip()):
                raise ValueError('검토기록에는 유효한 상태·검토자·근거와 완료 시 결론이 필요합니다.')
        return case

    def to_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def facts(self) -> dict:
        return {**self.contract, **self.market, **self.method}

    def effective(self) -> dict:
        result = copy.deepcopy(self.facts())
        for row in self.assumptions:
            result[row["field"]] = copy.deepcopy(row["value"])
        from .exercise import apply_styles
        return apply_styles(result, self.exercise_styles)


CALIBRATION_FIELDS = {'date', 'target', 'before', 'after', 'equity_ps', 'reason'}


def validate_calibration(row):
    """최초 인식 보정 기록 (제1113호 문단 64). 비어 있으면 기록 없음."""
    if not row:
        return
    if set(row) != CALIBRATION_FIELDS:
        raise ValueError('보정 기록에는 보정일·대상·보정 전후 값·당시 주당가치·근거가 필요합니다.')
    if row['target'] not in {'S0', 'spread'}:
        raise ValueError('보정 대상은 주가(S0) 또는 스프레드 가산(spread)입니다.')
    dt.date.fromisoformat(row['date'])
    if not all(_finite(row[k]) for k in ('before', 'after', 'equity_ps')) or row['equity_ps'] <= 0:
        raise ValueError('보정 전후 값과 당시 주당가치에는 유한한 숫자가 필요합니다(주당가치 > 0).')
    if not isinstance(row['reason'], str) or not row['reason'].strip():
        raise ValueError('보정 근거를 입력하십시오.')


def _migrate_carry(case: Case) -> None:
    """상태확장(carry=0)은 없앴다 — 옛 파일은 경로가중치(1)로 연다.

    세 입력 영역과 가정(assumptions) 줄 모두에서 바꾼다. 가정 줄이 effective() 에서
    입력을 덮어쓰므로 한 곳이라도 남으면 검증에서 막힌다.
    """
    for _sec in (case.contract, case.market, case.method):
        if isinstance(_sec, dict) and _sec.get("carry") == 0:
            _sec["carry"] = 1
    for _row in case.assumptions or []:
        if isinstance(_row, dict) and _row.get("field") == "carry" and _row.get("value") == 0:
            _row["value"] = 1


def import_legacy(obj: dict, name: str = "가져온 평가") -> Case:
    """Make historical defaults visible; never silently drop unknown input fields."""
    if not isinstance(obj, dict):
        raise ValueError("시나리오는 객체 형식이어야 합니다.")
    unknown = {k for k in obj if k not in Terms.__dataclass_fields__ and not k.startswith("_")}
    if unknown:
        raise ValueError(f"알 수 없는 기존 시나리오 항목: {', '.join(sorted(unknown))}")
    values = {k: v for k, v in asdict(Terms()).items() if k in FIELDS}
    values.update({k: copy.deepcopy(v) for k, v in obj.items() if k in FIELDS})
    case = Case(name=name, imported_defaults=sorted(FIELDS - set(obj)))
    for key, value in values.items():
        getattr(case, section_for(key))[key] = value
    _migrate_carry(case)
    case.notes = "기존 시나리오에서 가져옴. 보충된 기본값과 별도 계약조건을 검토하십시오."
    if isinstance(obj.get('_issue_px_source'), str):
        case.sources['issue_px'] = obj['_issue_px_source']
    return case


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def inspect_case(case: Case) -> list[Issue]:
    issues: list[Issue] = []

    def add(severity, code, key, message):
        issues.append(Issue(severity, code, key, message))

    for group in ("contract", "market", "method"):
        for key in getattr(case, group):
            if key not in FIELDS:
                add("error", "unknown_field", key, "알 수 없거나 계산으로 산출되는 항목입니다.")
            elif section_for(key) != group:
                add("error", "wrong_section", key, f"{section_for(key)} 영역에 기록해야 합니다.")
    seen = set()
    for row in case.assumptions:
        if not isinstance(row, dict) or set(row) != {"field", "value", "rationale"}:
            add("error", "assumption_shape", "assumptions", "각 가정에는 field, value, rationale가 필요합니다.")
            continue
        key = row["field"]
        if not isinstance(key, str) or key not in FIELDS or key == "inst":
            add("error", "assumption_field", str(key), "가정으로 변경할 수 없는 항목입니다.")
        elif key in seen:
            add("error", "duplicate_assumption", key, "같은 항목의 가정이 중복됩니다.")
        else:
            seen.add(key)
        if not isinstance(row["rationale"], str) or not row["rationale"].strip():
            add("error", "missing_rationale", str(key), "가정 변경의 근거를 입력하십시오.")
    if any(i.severity == "error" for i in issues):
        return issues
    values = case.effective()
    for row in case.assumptions:
        if case.exercise_styles.get(row['field']) == 'any':
            add('error', 'exercise_assumption', row['field'], '상시 행사와 행사주기 가정이 충돌합니다. 행사방식 또는 별도 가정을 수정하십시오.')
    for freq, sched in [('p_f', 'p_sched'), ('k_f', 'k_sched')]:
        if case.exercise_styles.get(freq) == 'any' and isinstance(values.get(sched), str) and values[sched].strip():
            add('error', 'exercise_conflict', freq, '상시 행사와 회차별 행사일·금액표가 함께 설정되어 있습니다. 계약상 행사일을 확인하여 하나의 방식으로 입력하십시오.')
    required = REQUIRED | (RCPS_REQUIRED if values.get("inst") == "RCPS" else set())
    if values.get('k_w', 0) or values.get('issuer_call', 0):
        required = required | {'k_s', 'k_e', 'k_f', 'k_prem', 'k_cmp'}
    if values.get('rfx_mode', 0):
        required = required | {'rfx_cyc', 'floor', 'carry'}
    for key in sorted(required - set(values)):
        add("error", "required", key, "필수 입력값이 없습니다.")
    types = get_type_hints(Terms)
    for key, value in values.items():
        typ = types[key]
        valid = (isinstance(value, str) if typ is str else
                 isinstance(value, list) if typ is list else
                 _finite(value) and (typ is not int or isinstance(value, int)))
        if not valid:
            add("error", "type", key, "자료형 또는 숫자를 확인하십시오. NaN·무한대는 허용하지 않습니다.")
    if any(i.severity == "error" for i in issues):
        return issues
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in case.sources.items()):
        add('error', 'source_type', 'sources', '자료 출처에는 항목명과 문자열 기록이 필요합니다.')
    for key, allowed in {"inst": {"CB", "BW", "RCPS", "SHA"}, "model": {"TF", "GS"},
                         "view": {"holder", "issuer"}, "y_type": {"par", "spot"},
                         "conv_class": {"equity", "liability"},
                         "p_mode": {"fixed", "accrue"},
                         "rate_mode": {"direct", "rating", "pick"},
                         # 내재파생 분리 정책은 «접근법 1 · 접근법 2» 다 — 0 은 없는 값이다.
                         # 일반 0/1 선택항목으로 검사하면 화면에서 고른 접근법 2 를 막고 0 을 통과시킨다.
                         "emb_approach": {1, 2}}.items():
        if key in values and values[key] not in allowed:
            add("error", "enum", key, f"지원하는 값: {sorted(allowed)}")
    counts = {"sha_put_cmp", "sha_call_cmp", "cur_periods", "ytm_cmp", "k_cmp", "cmp_rf", "cmp_cr", "p_cmp"}
    multi = {"sha_writer": 2, "sha_disc": 2, "issuer_call": 2, "rfx_mode": 2,
             "carry": 3, "k_method": 2, "k_less_cpn": 2, "p_less_cpn": 2, "m_less_cpn": 2,
             "rfx_round": 2}            # 원 단위 미만 0 처리 없음 / 1 절상 / 2 절사
    for key, value in values.items():
        if key == "sha_ipo_kind" and value == -1:
            continue                     # 주주간계약 상장 종료 조건 미선택 — 상장 조항을 켜면 sha_ipo_issues 가 막는다
        if types[key] is int and key != "emb_approach":
            if value < (1 if key == "carry" else 0) or (key not in counts and value > multi.get(key, 1)):
                add("error", "enum", key, "지원하지 않는 선택값입니다.")
    if not 0 <= values.get("k_w", 0) <= 1:
        add("error", "fraction", "k_w", "콜 대상 비율은 0~1이어야 합니다.")
    # 콜이 꺼졌거나(콜 한도 0) 의무보유가 꺼지면 의무보유 물량은 쓰이지 않는다 — 화면에서 숨겨진 예전 값이
    # 정상 입력을 막지 않게 콜과 의무보유가 모두 켜져 있을 때만 본다 (lock_share 도 그때만 쓴다).
    _lw = values.get("k_lock_w", -1.0)
    if (values.get("k_w", 0) > 0 and int(values.get("k_hold", 1)) == 1
            and _lw >= 0 and _lw > values.get("k_w", 0) + 1e-12):
        add("error", "fraction", "k_lock_w",
            "의무보유 물량 비율이 콜 대상 비율보다 큽니다. 콜 대상 밖 물량의 의무보유는 이 모형이 반영하지 않습니다 — "
            "콜 대상 안에서 묶인 물량만 입력하십시오 (비우면 콜 대상 전부).")
    for key in ("S0", "K0", "sig", "gap_m", "face_total", "par", "cmp_rf", "cmp_cr"):
        if key in values and values[key] <= 0:
            add("error", "positive", key, "0보다 큰 값이 필요합니다.")
    for key in ('ipay', 'p_f', 'k_f', 'rfx_cyc', 'sha_put_f', 'sha_call_f'):
        if key in values and values[key] <= 0:
            add('error', 'positive', key, '주기는 0보다 커야 합니다.')
    if 'split_tol' in values and not 0 < values['split_tol'] < 1:
        add('error', 'fraction', 'split_tol', '분리 판단 비교기준은 0%보다 크고 100%보다 작아야 합니다.')
    if values.get('split_base_in', -1) > 0 and not str(values.get('split_base_why', '')).strip():
        add('error', 'split_base_reason', 'split_base_why', '분리 판단 출발 금액을 직접 넣었으면 그 근거(실제 회계상 배분액 등)를 적으십시오.')
    if values.get('rfx_first', 0) < 0:
        add('error', 'negative', 'rfx_first', '최초 조정일은 발행일 이후여야 합니다 (비우면 발행일 + 주기).')
    for key in ('cpn', 'bdt_sig'):
        if key in values and values[key] < 0:
            add('error', 'negative', key, '음수는 지원하지 않습니다.')
    if values.get("inst") == "RCPS" and values.get("issue_px", 0) <= 0:
        add("error", "positive", "issue_px", "RCPS 1주당 발행가를 입력하십시오.")
    try:
        di, db, dm = (dt.date.fromisoformat(values[k]) for k in ("d_issue", "d_base", "d_mat"))
        if not di <= db < dm:
            add("error", "date_order", "d_base", "발행일 ≤ 평가기준일 < 만기일이어야 합니다.")
    except (ValueError, KeyError):
        add("error", "date", "dates", "발행일·기준일·만기일은 YYYY-MM-DD 형식이어야 합니다.")
    if values.get('inst') == 'SHA' and values.get('sha_rows'):
        from .legacy import SHA_ROW_KEYS, sha_row_issues, sha_row_issue_text
        rows = values['sha_rows']
        if not all(isinstance(r, dict) and set(r) <= set(SHA_ROW_KEYS) for r in rows):
            add('error', 'sha_rows', 'sha_rows', '회차별 표의 형식이 올바르지 않습니다.')
        elif not any(i.severity == 'error' for i in issues):
            for k, message in sha_row_issues(Terms(**values)):
                add('error', 'sha_rows', 'sha_rows', sha_row_issue_text(k, message))
    if values.get('inst') == 'SHA' and not values.get('sha_rows') and not any(i.severity == 'error' for i in issues):
        from .legacy import sha_contract_issues
        for message in sha_contract_issues(Terms(**values)):
            add('error', 'sha_link', 'sha_link_q', message)
    if not any(i.severity == 'error' for i in issues):
        from .legacy import parse_sched
        terms = Terms(**values)
        for key in ('p_sched', 'k_sched'):
            if values.get(key):
                rows = parse_sched(values[key], terms)
                if any(m is None or amount is None for m, amount in rows):
                    add('error', 'schedule', key, '행사금액표에 읽을 수 없는 줄이 있습니다. 날짜 또는 개월과 금액(%)을 한 줄씩 입력하십시오.')
    for key in ("rf_curve", "cr_curve", "cr_curve_b"):
        curve = values.get(key, [])
        if key == "cr_curve_b" and not curve and values.get("rate_mode") != "rating":
            continue
        ok = len(curve) >= 2 and all(isinstance(p, (list, tuple)) and len(p) == 2
                                    and _finite(p[0]) and p[0] > 0 and _finite(p[1])
                                    for p in curve)
        if ok:
            ok = all(curve[i][0] < curve[i+1][0] for i in range(len(curve)-1))
        if not ok:
            add("error", "curve", key, "만기·금리 쌍을 2개 이상, 만기 오름차순·중복 없이 입력하십시오.")
    if values.get('d1_pl', 0) == 1 and not str(values.get('d1_reason', '')).strip():
        add('error', 'day1_reason', 'd1_reason', '최초 인식 차이를 당기손익으로 처리하려면 관측 가능한 시장자료만 사용했다는 근거를 적으십시오 (1109 B5.1.2A(1)).')
    if values.get('grid_days', 0) not in (0, 7, 14):
        add('error', 'grid_days', 'grid_days', '일수 기준 간격은 주(7일) 또는 2주(14일)를 선택하십시오.')
    if values.get('grid_days', 0) == 0 and values.get("gap_m", 1) < .25:
        add("error", "legacy_week_grid", "gap_m", "월 기준의 최소 간격은 0.25개월입니다. 주 간격은 계산 간격에서 주(7일 기준)를 선택하십시오.")
    if case.imported_defaults:
        add("review", "legacy_defaults", "imported_defaults", f"기존 기본값 {len(case.imported_defaults)}개가 명시적으로 보충되었습니다.")
    missing_optional = FIELDS - set(values)
    if missing_optional:
        add("review", "engine_defaults", "defaults", "미입력 선택항목에는 기존 엔진 기본값을 적용합니다. 적용입력표에 전부 표시합니다.")
    for key in ("S0", "sig", "rf_curve", "cr_curve"):
        if not case.sources.get(key):
            add("review", "source", key, "기준일·출처·산출근거를 기록하십시오.")
    for idx, row in enumerate(case.additional_rights):
        path = f"additional_rights[{idx}]"
        if not isinstance(row, dict) or set(row) != {"kind", "clause", "treatment", "rationale", "assumption_fields"}:
            add("error", "right_shape", path, "권리에는 kind, clause, treatment, rationale, assumption_fields가 필요합니다.")
            continue
        if not isinstance(row["kind"], str) or row["kind"] not in RIGHT_KINDS:
            add("error", "right_kind", path, "등록되지 않은 권리 유형입니다.")
            continue
        if not isinstance(row["clause"], str) or not isinstance(row["rationale"], str):
            add("error", "right_text", path, "계약조항과 근거에는 문자열이 필요합니다.")
            continue
        if row["treatment"] not in ("unresolved", "excluded", "scenario"):
            add("error", "unsupported_exact", path, "이 권리는 직접 계산을 지원하지 않습니다. 미해결·제외·시나리오만 기록할 수 있습니다.")
        if not isinstance(row["assumption_fields"], list) or any(not isinstance(k, str) for k in row["assumption_fields"]):
            add("error", "right_assumptions", path, "assumption_fields에는 항목명 목록이 필요합니다.")
            continue
        if row["treatment"] == "scenario" and (not row["assumption_fields"] or not set(row["assumption_fields"]) <= seen):
            add("error", "right_scenario", path, "시나리오에 사용하는 별도 평가가정을 연결하십시오.")
        if row["treatment"] != "unresolved" and not str(row["rationale"]).strip():
            add("error", "right_rationale", path, "권리를 제외하거나 근사하는 근거가 필요합니다.")
        treatment = {"unresolved": "미해결", "excluded": "평가에서 제외", "scenario": "별도 가정으로 근사"}.get(row['treatment'], "미지원")
        add("review", "additional_right", path, f"{RIGHT_KINDS.get(row['kind'], '추가 권리')}: {treatment}. 직접 모형화되지 않았습니다.")
    if not case.additional_rights:
        add("review", "coverage_review", "additional_rights", "별도 계약조건 목록이 비어 있습니다. 계약서 전체의 반영 여부는 자동 검증되지 않습니다.")
    return issues


def compare_cases(previous: Case, current: Case) -> list[dict]:
    rows = []
    for group in ("contract", "market", "method", "sources"):
        before, after = getattr(previous, group), getattr(current, group)
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                rows.append(dict(section=group, field=key, previous=before.get(key), current=after.get(key)))
    for group in ("assumptions", "additional_rights", "imported_defaults", "notes", "judgments", "contract_scenarios"):
        before, after = getattr(previous, group), getattr(current, group)
        if before != after:
            rows.append(dict(section=group, field=group, previous=before, current=after))
    return rows
