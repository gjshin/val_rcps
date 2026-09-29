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
from valuation.cashflows import calculate_cashflows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--scenario', help='평가파일의 저장된 계약조건 분석 이름. 생략 시 용역 묶음 평가.')
    parser.add_argument('--cashflow', help='평가파일의 저장된 부분상환·지급시차 분석 이름.')
    args=parser.parse_args()
    if args.scenario and args.cashflow:
        parser.error('--scenario와 --cashflow 중 하나만 선택하십시오.')
    try:
        obj=json.loads(args.input.read_text(encoding='utf-8'))
        if args.scenario or args.cashflow:
            case=Case.from_dict(obj)
            scenarios = case.cashflow_scenarios if args.cashflow else case.contract_scenarios
            scenario=next((s for s in scenarios if s['name']==(args.cashflow or args.scenario)), None)
            if scenario is None:raise ValueError('해당 이름의 계약조건 분석이 없습니다.')
            result = calculate_cashflows(case,scenario) if args.cashflow else calculate_schedule(case,scenario)
            data=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False).encode()
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
