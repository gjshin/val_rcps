#!/usr/bin/env python3
"""Run the unchanged workbook assertions in isolated, resumable case processes.

    python tests/run_long_checks.py --jobs 4 --out /tmp/valuation-long-checks

Case results are reusable only with an identical engine and test source hash.
Settings checks retain the original ordered cross-case movement comparisons.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SUITES = ('조서대조', '설정전수대조')


def load_suite(name):
    spec = importlib.util.spec_from_file_location('check_' + name, ROOT / 'tests' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_hash():
    paths = [ROOT / 'valuation' / 'legacy.py', Path(__file__).resolve()]
    paths += [ROOT / 'tests' / (name + '.py') for name in SUITES]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--out', type=Path, default=ROOT / 'tests' / 'output' / 'long_checks')
    parser.add_argument('--worker', choices=SUITES)
    parser.add_argument('--index', type=int)
    args = parser.parse_args()
    if args.worker:
        module = load_suite(args.worker)
        if args.worker == '설정전수대조':
            return module.run_one(args.index)
        module.CASES = [module.CASES[args.index]]
        sys.argv = [sys.argv[0]]
        return module.main()
    if not 1 <= args.jobs <= 8:
        parser.error('--jobs must be between 1 and 8')
    if importlib.util.find_spec('formulas') is None:
        parser.error('Install requirements-dev.txt before running workbook checks.')
    modules = {name: load_suite(name) for name in SUITES}
    key = source_hash()
    folder = args.out / key[:16]
    folder.mkdir(parents=True, exist_ok=True)
    jobs = [(name, i, case[0]) for name, module in modules.items() for i, case in enumerate(module.CASES)]

    def execute(job):
        name, index, label = job
        path = folder / f'{name}_{index:03d}.json'
        if path.exists():
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved['source_hash'] == key and saved['code'] == 0:
                return saved
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', name, '--index', str(index)]
        start = time.monotonic()
        env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env)
        record = dict(suite=name, index=index, label=label, code=result.returncode,
                      seconds=round(time.monotonic()-start, 2), source_hash=key,
                      stdout=result.stdout, stderr=result.stderr)
        atomic_json(path, record)
        return record

    start = time.monotonic()
    records = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(execute, job) for job in jobs]
        for future in as_completed(futures):
            record = future.result()
            records[(record['suite'], record['index'])] = record
            print(f"{len(records)}/{len(jobs)} {'PASS' if record['code'] == 0 else 'FAIL'} "
                  f"{record['suite']} {record['index']}: {record['label']}", flush=True)
    results = []
    for name, module in modules.items():
        rows = [records[(name, i)] for i in range(len(module.CASES))]
        code = int(any(row['code'] != 0 for row in rows))
        assertions = ''
        if name == '설정전수대조':
            completed = [subprocess.CompletedProcess([], row['code'], row['stdout'], row['stderr']) for row in rows]
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                code = max(code, module.main(completed=completed))
            assertions = capture.getvalue()
            print(assertions, flush=True)
        results.append(dict(suite=name, ok=code == 0, cases=len(rows),
                            case_results=[{k:v for k,v in row.items() if k not in ('stdout','stderr','source_hash')} for row in rows],
                            assertions=assertions))
    summary = dict(source_hash=key, source_head=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                   jobs=args.jobs, seconds=round(time.monotonic()-start, 2), suites=results,
                   all_ok=all(result['ok'] for result in results))
    atomic_json(folder / 'summary.json', summary)
    atomic_json(args.out / 'latest.json', summary)
    print(f"Result: {folder / 'summary.json'} — all_ok={summary['all_ok']}", flush=True)
    return 0 if summary['all_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
