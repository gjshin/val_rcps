#!/usr/bin/env python3
"""Run a saved engagement or an explicit contract schedule without the UI."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from valuation.case import Case
from valuation.engagement import Engagement, calculate_engagement, engagement_bundle
from valuation.contract_analysis import calculate_schedule


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--scenario', help='평가파일의 저장된 계약조건 분석 이름. 생략 시 용역 묶음 평가.')
    args=parser.parse_args()
    try:
        obj=json.loads(args.input.read_text(encoding='utf-8'))
        if args.scenario:
            case=Case.from_dict(obj)
            scenario=next((s for s in case.contract_scenarios if s['name']==args.scenario), None)
            if scenario is None:raise ValueError('해당 이름의 계약조건 분석이 없습니다.')
            data=json.dumps(calculate_schedule(case,scenario),ensure_ascii=False,indent=2,allow_nan=False).encode()
        else:
            engagement=Engagement.from_dict(obj)
            data=engagement_bundle(engagement,calculate_engagement(engagement))
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_bytes(data)
        print(f'저장: {args.out}')
        return 0
    except (ValueError,TypeError,KeyError,OSError,ArithmeticError) as exc:
        print(f'처리 실패: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
