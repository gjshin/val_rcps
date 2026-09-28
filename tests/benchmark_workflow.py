"""Manual, reproducible local benchmark; synthetic input only.

Run: python tests/benchmark_workflow.py --out docs/업무화면_성능측정.json
Timing is environment-specific and does not measure financial-model accuracy.
"""
import argparse
import copy
import json
import platform
import sys
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from valuation import legacy
from valuation.case import import_legacy
from valuation.service import calculate, refresh_run, export_bundle


def fixture():
    return import_legacy(asdict(legacy.Terms(inst='RCPS', issue_px=100., S0=125., K0=100., par=1.,
        floor=70., d_issue='2025-01-01', d_base='2025-01-01', d_mat='2030-01-01',
        gap_m=1., sig=.3, rfx_mode=1, carry=1, rfx_cyc=6., mat_mode=1, issuer_call=2,
        cv_s=12., cv_e=59., p_s=24., p_e=60., k_s=12., k_e=24., k_w=.2,
        rf_curve=[[1., .025], [5., .025]], cr_curve=[[1., .06], [5., .06]])), 'Synthetic benchmark')


def measure(action):
    original = legacy.engine
    calls = []
    def engine(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    tracemalloc.start()
    started = time.perf_counter()
    with patch.object(legacy, 'engine', engine):
        result = action()
    seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {'seconds': round(seconds, 6), 'peak_python_memory_mib': round(peak / 1024**2, 3),
                    'stock_lattice_calls': len(calls)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    case = fixture()
    run, pricing = measure(lambda: calculate(case))
    basic, basic_measure = measure(lambda: export_bundle(run))
    detail, detail_measure = measure(lambda: export_bundle(run, detail=True))
    changed = copy.deepcopy(case); changed.notes = 'Reviewer note'
    _, refresh = measure(lambda: refresh_run(run, changed))
    record = {'python': sys.version.split()[0], 'platform': platform.platform(),
              'input': case.to_dict(), 'intervals': run.terms.n,
              'calculation': pricing, 'metadata_refresh': refresh,
              'basic_export': {**basic_measure, 'zip_bytes': len(basic)},
              'detailed_export': {**detail_measure, 'zip_bytes': len(detail)},
              'notes': 'Single run with tracemalloc enabled; Python allocations only, not process RSS. Same stored result for both export paths. No claim of unchanged-path pricing speedup.'}
    args.out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in record.items() if k != 'input'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
