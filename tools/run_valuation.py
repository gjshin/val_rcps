#!/usr/bin/env python3
"""旧 명령의 호환 입구. 모든 입출력·계산은 run_case.py를 사용합니다."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.valuation_cli_support import apply_set, make_terms, load_app, run, scan, self_check
from tools.run_case import main

if __name__ == '__main__':
    raise SystemExit(main(legacy_cli=True))
