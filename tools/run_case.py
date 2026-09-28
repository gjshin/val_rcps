#!/usr/bin/env python3
"""V2 case import, inspection and valuation (same service as workspace_app)."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from valuation.case import Case, import_legacy, inspect_case
from valuation.service import CaseError, calculate, export_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--import-legacy", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--formula", action="store_true")
    parser.add_argument("--detail", action="store_true", help="상세 계산 시트 포함(기본은 간단한 값 조서)")
    args = parser.parse_args(argv)
    if not args.inspect and args.out is None:
        parser.error("--out 출력 파일이 필요합니다.")
    try:
        obj = json.loads(args.case.read_text(encoding="utf-8"))
        case = import_legacy(obj, args.case.stem) if args.import_legacy else Case.from_dict(obj)
        if args.inspect:
            issues = inspect_case(case)
            print(json.dumps([asdict(i) for i in issues], ensure_ascii=False, indent=2))
            return int(any(i.severity == "error" for i in issues))
        if args.import_legacy:
            data = json.dumps(case.to_dict(), ensure_ascii=False, indent=2).encode()
        else:
            previous = Case.from_dict(json.loads(args.previous.read_text(encoding="utf-8"))) if args.previous else None
            run = calculate(case)
            data = export_bundle(run, formula=args.formula, detail=args.detail, previous=previous)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(data)
        print(f"저장: {args.out}")
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"처리 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
