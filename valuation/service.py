"""One execution and export path for app and command-line callers."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import math
import zipfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import legacy
from .case import Case, Issue, FIELDS, inspect_case, compare_cases, section_for

AMOUNT_LABELS = {
    "whole_before_call": "본체 가치(콜 차감 전)", "call_deduction": "콜옵션 차감",
    "net": "순포지션 가치", "host_reference": "주계약(참고)",
    "put_increment": "상환권 증분(참고)", "conversion_increment": "전환권 증분(참고)",
    "put": "풋옵션", "call": "콜옵션",
}


class CaseError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("\n".join(f"{i.field}: {i.message}" for i in issues if i.severity == "error"))


@dataclass
class Run:
    case: Case
    terms: legacy.Terms
    raw: dict
    summary: dict
    issues: list[Issue]


def code_fingerprint() -> str:
    h = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


# 분개 표기만 바꾸는 선택 — 평가값에 영향이 없어 재평가 없이 조서에 반영한다.
ACCOUNTING_ONLY = frozenset({"d1_pl", "d1_reason"})


def day1_summary(terms, raw):
    """최초 인식(평가기준일 = 발행일)의 모형값 · 거래가격 100 · 차이 — 투자자와 발행자.

    발행자는 회계기준원 질의회신 2019-I-KQA018 대로 순평가금액(콜 차감 후)을 거래가격과 견준다.
    전환권이 부채(복합계약)면 이연(기본) 또는 당기손익을 고르고, 자본이면 차이가 자본요소에 흡수된다.
    """
    if legacy.is_sha(terms) or terms.elapsed_m > 0.01:
        return None
    if legacy.holder_on(terms):
        net = float(raw["b2"] - raw["ca"])
        choice = True
        mode = "당기손익" if int(terms.d1_pl) == 1 else "이연"
    else:
        d1 = legacy.issuer_day1(terms, raw["b0"], raw["b1"], raw["b2"], raw["ca"])
        if d1 is None:
            return None
        net, choice, mode = float(d1["net"]), bool(d1["hybrid"]), d1["mode"]
    diff = net - 100.0
    return {"model": net, "price": 100.0, "diff": diff, "mode": mode,
            "view": "holder" if legacy.holder_on(terms) else "issuer", "choice": choice,
            "nums": f"모형값 {net:,.4f} / 거래가격 100 / 차이 {diff:+,.4f}",
            "verdict": ("차이 없음 (보정됨)" if abs(diff) < 0.005 else f"차이 {diff:+,.4f} — 처리: {mode}")}


def calculation_key(case: Case) -> str:
    """Conservative, session-local reuse key. Only case metadata is excluded.

    Every Terms input, including unused settings, participates. Excluding fewer
    inputs is preferable to accidentally reusing a value from another contract.
    """
    inputs = {k: v for k, v in case.effective().items() if k not in ACCOUNTING_ONLY}
    payload = {"code": code_fingerprint(), "inputs": inputs}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _prepare(case: Case):
    # A run owns a snapshot. Editing the UI afterward must not change its evidence.
    case = Case.from_dict(case.to_dict())
    issues = inspect_case(case)
    if any(i.severity == "error" for i in issues):
        raise CaseError(issues)
    effective = case.effective()
    terms = legacy.Terms(**effective)
    incompatible = legacy.compat(terms)
    if incompatible:
        issues += [Issue("error", "incompatible", key, f"{message} 필요한 값: {value}")
                   for key, value, message in incompatible]
        raise CaseError(issues)
    before = asdict(terms)
    legacy.derive(terms)
    if terms.n > 1200:
        issues.append(Issue("error", "resource_limit", "gap_m", "검토 작업공간의 계산 한도를 초과합니다. 격자는 1,200구간까지 지원합니다."))
        raise CaseError(issues)
    normalized = {k: {"input": before[k], "applied": v}
                  for k, v in asdict(terms).items() if k in FIELDS and before[k] != v}
    if normalized:
        issues.append(Issue("review", "normalized", "applied_terms", "상품별 비적용 설정이 정규화되었습니다. 적용입력표의 변경내역을 확인하십시오."))
    return case, terms, issues, normalized


def calculate(case: Case) -> Run:
    started = time.perf_counter()
    case, terms, issues, normalized = _prepare(case)
    if legacy.is_sha(terms):
        raw = legacy.sha_engine(terms)
        raw["integrity"] = legacy.sha_integrity(terms, raw)
    else:
        full, b0, b1, b2, ca, conv = legacy.decompose(terms)
        raw = dict(full=full, b0=b0, b1=b1, b2=b2, ca=ca, conv=conv)
        # 계산이 고장 나지 않았는지 보는 개발용 점검 — 조서에는 싣지 않고, 걸리면 조서를
        # 만들지 않는다(export_bundle). 평가할 때 한 번 재어 실행 기록에 남긴다.
        _args = (terms, full, b0, b1, b2, ca)
        raw["integrity"] = [c[0] for c in legacy.model_checks(*_args, legacy.eir_or_none(*_args), light=True)
                            if c[2] == "확인 필요" and c[0] in legacy.HARD_CHECKS]
    run = _assemble(case, terms, raw, issues, normalized)
    run.summary["calculation_seconds"] = time.perf_counter() - started
    return run


def refresh_run(run: Run, case: Case) -> Run:
    """Update evidence without repricing; reject changed numerical inputs/code."""
    if calculation_key(case) != run.summary["calculation_key"]:
        raise ValueError("계산 입력이 바뀌었습니다. 현재 입력으로 다시 평가하십시오.")
    case, terms, issues, normalized = _prepare(case)
    result = _assemble(case, terms, run.raw, issues, normalized,
                       warnings=[i for i in run.issues if i.code == 'input_check'])
    result.summary["calculation_seconds"] = run.summary["calculation_seconds"]
    result.summary["calculated_at"] = run.summary["calculated_at"]
    return result


def _assemble(case, terms, raw, issues, normalized, warnings=None):
    # 입력 경고는 계산할 때 한 번 만든다. 증빙만 바꾼 갱신(refresh_run)은 다시 만들지 않는다.
    from .review import review_issues, arithmetic_checks, input_warnings
    from .evidence import evidence_cards
    if legacy.is_sha(terms):
        amounts = {"put": float(raw["put"]), "call": float(raw["call"])}
        full = raw
    else:
        full, b0, b1, b2, ca = (raw[k] for k in ("full", "b0", "b1", "b2", "ca"))
        amounts = {"whole_before_call": float(b2), "call_deduction": float(ca),
                   "net": float(b2-ca), "host_reference": float(b0),
                   "put_increment": float(b1-b0), "conversion_increment": float(b2-b1)}
        if legacy.auto_conv(terms):
            issues.append(Issue("review", "auto_conversion_split", "mat_mode", "자동전환 RCPS의 주계약·전환권 차액은 기존 분해방식의 참고값입니다. 회계상 인식액으로 확정하지 마십시오."))
    if full.get("qbad"):
        issues.append(Issue("error", "risk_neutral_probability", "sig", "일부 격자의 위험중립확률이 (0, 1)을 벗어났습니다."))
        raise CaseError(issues)
    if not all(math.isfinite(v) for v in amounts.values()):
        raise CaseError(issues + [Issue("error", "nonfinite_result", "result", "유한한 평가값이 산출되지 않았습니다.")])
    # Legacy validate() mixes assumptions, accounting conclusions and extra
    # valuations. Basic workflow uses bounded checks on the existing result.
    issues += review_issues(case, terms, raw)
    issues += input_warnings(terms) if warnings is None else list(warnings)
    day1 = day1_summary(terms, raw)
    if day1 and abs(day1["diff"]) >= 0.005:
        issues.append(Issue("review", "day1_gap", "S0", f"최초 인식 {day1['nums']}.",
                            "보정하지 않으면 최초 인식 차이가 생깁니다 (1109 B5.1.2A).",
                            "주가 역산으로 보정하거나(1113 문단 64) 판단·근거 탭의 원인 점검 3항목에 답하십시오."))
    checks = arithmetic_checks(terms, raw, amounts)
    if any(not row["passed"] for row in checks):
        raise CaseError(issues + [Issue("error", "arithmetic", "result", "산술 대사에 차이가 있습니다. 계산내역을 확인하십시오.")])
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    summary = {
        "status": "review_only", "name": case.name, "schema": case.schema,
        "case_sha256": case.fingerprint(), "code_sha256": code_fingerprint(),
        "generated_at": now, "calculated_at": now,
        "calculation_key": calculation_key(case), "checks": checks,
        "basis": "발행금액 또는 투자원금 100 기준", "amounts_100": amounts,
        "amounts_total": {k: v * terms.face_total / 100 for k, v in amounts.items()},
        "amounts_per_share": {k: v * terms.issue_px / 100 for k, v in amounts.items()}
                             if legacy.is_rcps(terms) else None,
        "grid": {"intervals": terms.n, "average_days": terms.T*365/terms.n,
                 "requested_months": terms.gap_m, "requested_days": terms.grid_days,
                 "type": "day_target_equal_time" if terms.grid_days else "legacy_equal_time"},
        "normalizations": normalized,
        "applied_terms": asdict(terms),
        "engine_defaults": sorted(FIELDS - set(case.effective())),
        "issues": [asdict(i) for i in issues],
        "judgment_evidence": evidence_cards(case),
        "day1": day1,
    }
    return Run(case=case, terms=terms, raw=raw, summary=summary, issues=issues)


def input_rows(run: Run) -> list[dict]:
    original = run.case.facts()
    assumptions = {row["field"]: row for row in run.case.assumptions}
    rows = []
    for key in sorted(FIELDS):
        row = assumptions.get(key)
        rows.append({
            "영역": section_for(key), "항목": key, "원본 입력": original.get(key),
            "적용값": run.summary["applied_terms"][key],
            "가정 변경 근거": row["rationale"] if row else "",
            "출처": run.case.sources.get(key, ""),
            "기본값 보충": key in run.case.imported_defaults or key in run.summary["engine_defaults"],
        })
    return rows


def _json(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _cell(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


def _evidence_workbook(data: bytes, run: Run) -> bytes:
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = load_workbook(io.BytesIO(data))
    ws = wb.create_sheet("V2_검토기록", 0)
    rows = [
        ["검토용 평가 — 계약조건과 평가가정을 확인하십시오."],
        ["건명", run.case.name], ["입력 SHA256", run.summary["case_sha256"]],
        ["코드 SHA256", run.summary["code_sha256"]],
        ["작성시각(UTC)", run.summary["generated_at"]],
        ["모형", "기존 TF/GS 계산부. 별도 계약권리의 직접 모형화는 추가 개발 대상."],
        ["구간 수", run.terms.n, "평균 일수", run.summary["grid"]["average_days"]],
        [], ["구분", "코드", "항목", "검토사항"],
    ] + [[i.severity, i.code, i.field, i.message] for i in run.issues]
    for row in rows:
        ws.append(row)
    facts = wb.create_sheet("V2_계약과가정", 1)
    data_rows = input_rows(run)
    facts.append(list(data_rows[0]))
    for row in data_rows:
        facts.append([_cell(v) for v in row.values()])
    facts.freeze_panes = "A2"
    facts.auto_filter.ref = facts.dimensions
    rights = wb.create_sheet("V2_추가권리", 2)
    rights.append(["종류", "계약조항", "반영방식", "근거", "연결 가정"])
    for row in run.case.additional_rights:
        rights.append([_cell(row[k]) for k in ("kind", "clause", "treatment", "rationale", "assumption_fields")])
    from .evidence import evidence_rows
    evidence = wb.create_sheet('판단근거')
    for row in evidence_rows(run.case):
        evidence.append(row)
    for sheet in (ws, facts, rights, evidence):
        for cell in sheet[1]:
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = PatternFill("solid", fgColor="17365D")
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 25
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                # Imported contract text must remain text, never executable Excel formulas.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_bundle(run: Run, *, formula: bool = False, previous: Case | None = None,
                  detail: bool = False, accounting: bool = False, judgment: bool = True) -> bytes:
    """Export the exact run snapshot; refuse implicit approximate formula conversion.

    judgment — 판단·근거 시트와 분리 판단·검산요약·모형검증 시트를 싣는다(기본).
    """
    terms = copy.deepcopy(run.terms)
    # 계산이 고장 나지 않았는지 본 결과(calculate 가 잰 것). 개발용 점검이라 조서에는
    # 싣지 않고, 걸리면 조서를 만들지 않는다 — 틀린 계산이 조서로 나가는 마지막 관문이다.
    bad = run.raw.get("integrity") or []
    if bad:
        raise ValueError("계산 점검에서 이상이 발견되어 조서를 만들지 않았습니다 — "
                         + ", ".join(bad) + ". 입력을 확인하고, 그대로라면 개발 담당자에게 알려 주십시오.")
    from .report import basic_workbook, append_basic_accounting, finish_calculation_workbook
    if not formula and not detail:
        wb = basic_workbook(run, previous=previous, as_workbook=True)
        if accounting:
            append_basic_accounting(wb, run)
    else:
        pack = run.case.market_evidence.get('sig')
        attach = dict(px=(pack['series'], pack['opt']) if pack else None, ir=True)
        if legacy.is_sha(terms):
            wb = legacy.build_xlsx_sha(terms, run.raw, formula=formula, attach=attach, as_workbook=True)
        else:
            r = run.raw
            args = (terms, r["full"], r["b0"], r["b1"], r["b2"], r["ca"])
            eir = legacy.eir_or_none(*args) if accounting else None
            wb = (legacy.build_xlsx_formula if formula else legacy.build_xlsx)(
                *args, r['conv'], eir, attach=attach, as_workbook=True, include_review=judgment)
    workbook = finish_calculation_workbook(wb, run, formula=formula, accounting=accounting, judgment=judgment)
    md = [f"# {run.case.name} — 계산 결과", "",
          f"- 입력 SHA256: {run.summary['case_sha256']}",
          f"- 코드 SHA256: {run.summary['code_sha256']}",
          f"- 구간 수: {terms.n}; 평균 일수: {run.summary['grid']['average_days']:.6f}", "",
          "| 결과 | 100 기준 | 총액 |", "|---|---:|---:|"]
    for key, value in run.summary["amounts_100"].items():
        md.append(f"| {AMOUNT_LABELS[key]} | {value:,.6f} | {run.summary['amounts_total'][key]:,.0f} |")
    numeric_issues = [i for i in run.issues if i.code not in {'source', 'legacy_defaults', 'engine_defaults', 'judgement_scope', 'market_date'}]
    md += ["", "확인할 사항", ""] + [f"- {i.field}: {i.message}" for i in numeric_issues]
    md += ["", "수식 조서는 선택한 계산방법을 유지합니다. 이 실행에서 Excel 전체 재계산은 수행하지 않았습니다."
           if formula else "값 조서는 이 실행의 결과 스냅샷입니다."]
    output_summary = {k:v for k,v in run.summary.items() if k != 'judgment_evidence'}
    output_summary['status'] = 'calculation'
    output_summary['issues'] = [asdict(i) for i in numeric_issues]
    files = {"case.json": _json(run.case.to_dict()), "result.json": _json(output_summary),
             "applied_inputs.json": _json(input_rows(run)), "review.md": "\n".join(md).encode(),
             ("formula_review.xlsx" if formula else "value_review.xlsx"): workbook}
    files['market_evidence.json'] = _json(run.case.market_evidence)
    if previous:
        files["changes.json"] = _json(compare_cases(previous, run.case))
    files["manifest.json"] = _json({name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def export_final_bundle(run: Run) -> bytes:
    """Final values only; independent review cannot certify live Excel formulas."""
    from .controls import is_final, blockers
    if not is_final(run):
        messages = [r['message'] for r in blockers(run)]
        raise ValueError('최종 확정본을 만들 수 없습니다. ' + ' / '.join(messages or ['현재 결과를 최종 확정하십시오.']))
    # A mutable Run must not carry edited figures/terms into the final package.
    case, terms, issues, normalized = _prepare(run.case)
    checked = _assemble(case, terms, run.raw, issues, normalized,
                        warnings=[i for i in run.issues if i.code == 'input_check'])
    for key in ('amounts_100', 'amounts_total', 'amounts_per_share', 'applied_terms', 'checks', 'normalizations'):
        if checked.summary[key] != run.summary[key]:
            raise ValueError('저장된 결과와 계산 원본이 일치하지 않습니다. 다시 평가하십시오.')
    if asdict(terms) != asdict(run.terms):
        raise ValueError('저장된 적용입력이 변경되었습니다. 다시 평가하십시오.')
    draft = export_bundle(run)
    with zipfile.ZipFile(io.BytesIO(draft)) as z:
        files = {name: z.read(name) for name in z.namelist() if name != 'manifest.json'}
    from .report import append_controls
    files['final_values.xlsx'] = append_controls(files.pop('value_review.xlsx'), run, final=True)
    result = copy.deepcopy(run.summary)
    result['status'] = 'reviewed_final_values'
    result['finalization'] = copy.deepcopy(run.case.review_controls['final'])
    files['result.json'] = _json(result)
    files['review_controls.json'] = _json(dict(state='최종 확정', export_status='final_values', blockers=[], records=run.case.review_controls))
    files['review.md'] = (f'# {run.case.name} — 최종 값 조서\n\n'
        '작성자·검토자의 확인 기록에 따라 기본 모형 결과를 확정한 값 조서입니다. '
        '조건부 분석값은 합산하지 않았습니다. 독립 검산 근거와 계약 반영표를 함께 확인하십시오. '
        '본 파일은 Microsoft Excel 수식 재계산 검증을 뜻하지 않으며 전자서명도 아닙니다.\n\n' +
        json.dumps(run.case.review_controls['review'], ensure_ascii=False, indent=2)).encode()
    files['manifest.json'] = _json({name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()
