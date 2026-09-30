#!/usr/bin/env python3
"""앱과 같은 입력·계산·조서를 사용하는 단일 명령 도구."""
import argparse
import copy
import csv
import io
import json
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from valuation.case import Case, import_legacy, inspect_case
from valuation.service import calculate, export_bundle
from valuation.market_data import validate_pack, export_volatility_workbook
from valuation import legacy
from tools.valuation_cli_support import apply_set


def read_case(path):
    obj = json.loads(path.read_text(encoding='utf-8'))
    return Case.from_dict(obj) if isinstance(obj, dict) and 'schema' in obj else import_legacy(obj, path.stem)


def override(case, changes):
    if not changes:
        return case
    from valuation.bridge import apply_changes
    from valuation.case import FIELDS
    terms = legacy.Terms(**case.effective())
    apply_set(vars(legacy), terms, changes)
    values = asdict(terms)
    return apply_changes(case, {k:values[k] for k in FIELDS if values[k] != case.effective().get(k, getattr(legacy.Terms(), k))})


def main(argv=None, *, legacy_cli=False):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', type=Path)
    p.add_argument('--import-legacy', action='store_true', help='이전 입력 파일을 현재 평가파일로 변환')
    p.add_argument('--inspect', action='store_true')
    p.add_argument('--out', type=Path, help='ZIP 파일 또는 출력 폴더')
    p.add_argument('--previous', type=Path)
    p.add_argument('--formula', action='store_true')
    p.add_argument('--detail', action='store_true')
    p.add_argument('--accounting', action='store_true', help='회계처리·분개·상각표 초안 포함')
    p.add_argument('--no-judgment', action='store_true', help='판단·근거·분리 판단·검산요약·모형검증 시트 제외')
    p.add_argument('--set', action='append', default=[], metavar='항목=값')
    p.add_argument('--sens', type=Path, help='[{"label":"이름","set":{"S0":100}}] 민감도 파일')
    p.add_argument('--vol', type=Path, help='원본 주가와 조회 조건을 포함한 변동성 패키지')
    p.add_argument('--vol-out', type=Path, help='변동성 산출 엑셀 저장 경로')
    p.add_argument('--check-formulas', action='store_true', help='출력한 전체 격자를 LibreOffice로 재계산·대사')
    p.add_argument('--name', help='출력 파일명')
    p.add_argument('--no-formula', action='store_true', help='이전 명령 호환: 값 조서만 출력')
    p.add_argument('--no-check', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--final', action='store_true', help='이전 명령 호환: 승인 절차 없이 계산 조서 출력')
    a = p.parse_args(argv)
    formula = (a.formula or legacy_cli) and not a.no_formula
    if a.check_formulas and not formula:
        p.error('--check-formulas에는 --formula가 필요합니다.')
    if a.vol_out and not a.vol:
        p.error('--vol-out에는 --vol 패키지가 필요합니다.')
    if not a.inspect and a.out is None:
        p.error('--out 출력 파일 또는 폴더가 필요합니다.')
    try:
        case = read_case(a.case)
        if a.vol:
            pack = json.loads(a.vol.read_text(encoding='utf-8'))
            validate_pack(pack, asof=case.effective()['d_base'])
            case.market['sig'] = pack['sigma']; case.market_evidence['sig'] = pack
        changes = dict(s.split('=', 1) for s in a.set)
        case = override(case, changes)
        if a.inspect:
            issues = inspect_case(case)
            print(json.dumps([asdict(i) for i in issues], ensure_ascii=False, indent=2))
            return int(any(i.severity == 'error' for i in issues))
        if a.import_legacy:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(case.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
            return 0
        run = calculate(case)
        previous = read_case(a.previous) if a.previous else None
        data = export_bundle(run, formula=formula, detail=a.detail or legacy_cli,
                             accounting=a.accounting, previous=previous, judgment=not a.no_judgment)
        extras = {}
        if a.sens:
            rows = [['경우', '전체(100)', '콜(100)', '순포지션(100)', '순포지션(원)', '풋(100)']]
            for scenario in json.loads(a.sens.read_text(encoding='utf-8')):
                result = calculate(override(copy.deepcopy(case), scenario.get('set', {})))
                v = result.summary['amounts_100']
                rows.append([scenario['label'], v.get('whole_before_call'), v.get('call_deduction', v.get('call')),
                             v.get('net'), result.summary['amounts_total'].get('net'), v.get('put')])
            stream = io.StringIO(); csv.writer(stream).writerows(rows)
            extras['민감도.csv'] = stream.getvalue().encode('utf-8-sig')
        if a.check_formulas:
            from valuation.xlsx_validation import recalculate_and_compare
            from valuation import legacy as _lg
            _r = run.raw
            _eir = (_lg.eir_or_none(run.terms, _r["full"], _r["b0"], _r["b1"], _r["b2"], _r["ca"])
                    if (a.accounting and not _lg.is_sha(run.terms)) else None)
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                checked = recalculate_and_compare(bundle.read('formula_review.xlsx'),
                                                  expected=_lg.formula_key_cells(run.terms, _r, _eir))
            extras['수식재계산검사.json'] = json.dumps(checked, ensure_ascii=False, indent=2).encode()
        if a.vol:
            extras['변동성.xlsx'] = export_volatility_workbook(pack)
        if extras:
            import hashlib
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                files = {n: bundle.read(n) for n in bundle.namelist() if n != 'manifest.json'}
            files.update(extras)
            files['manifest.json'] = json.dumps({n:hashlib.sha256(v).hexdigest() for n,v in files.items()}, ensure_ascii=False, indent=2).encode()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
                for name, content in files.items():
                    bundle.writestr(name, content)
            data = buffer.getvalue()
        if a.out.suffix.lower() == '.zip' and not legacy_cli:
            a.out.parent.mkdir(parents=True, exist_ok=True); a.out.write_bytes(data)
        else:
            a.out.mkdir(parents=True, exist_ok=True)
            prefix = ''
            if a.name:
                a.out = a.out/Path(a.name).name
                a.out.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                for name in bundle.namelist():
                    (a.out/(prefix+name)).write_bytes(bundle.read(name))
            if legacy_cli and formula:
                with zipfile.ZipFile(io.BytesIO(export_bundle(run, detail=True, accounting=a.accounting, judgment=not a.no_judgment))) as bundle:
                    (a.out/(prefix+'value_review.xlsx')).write_bytes(bundle.read('value_review.xlsx'))
        if a.vol_out:
            a.vol_out.parent.mkdir(parents=True, exist_ok=True); a.vol_out.write_bytes(extras['변동성.xlsx'])
        print(f'저장: {a.out}')
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f'처리 실패: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
