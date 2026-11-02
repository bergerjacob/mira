#!/usr/bin/env python3
"""Vanilla 26.2 cross-check of stored T4 references with ONE JVM PER CAPTURE (batched jobs in a shared JVM can contaminate each other).

    .venv/bin/python scripts/t4_oracle.py t4_addsub8 [t4_accum4 ...] [--jobs 3]

Same stimulus / verdict machinery as env.library.oracle_task (= scripts/oracle_env_diff.py); only the JVM sharding differs.
Results are merged into env/tasks/t4/oracle_report.json with "jvm_per_capture": true.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from env import library as L  # noqa: E402
from env.task import load_task  # noqa: E402
from tools.oracle import gametest  # noqa: E402

OUT = ROOT / "env" / "tasks" / "t4"


def capture_many_isolated(jobs, workers=3, **kw):
    """Drop-in for gametest.capture_many: every job gets its own JVM (run `workers` at a time)."""
    jobs = list(jobs)
    with ThreadPoolExecutor(max(1, min(workers, len(jobs)))) as ex:
        return list(ex.map(lambda j: gametest.capture_batch([j], **kw)[0], jobs))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args(argv)
    gametest.capture_many = lambda jobs, workers=4, **kw: capture_many_isolated(jobs, a.jobs, **kw)
    path = OUT / "oracle_report.json"
    for tid in a.ids:
        t0 = time.time()
        r = L.oracle_task(load_task(OUT / f"{tid}.json"), workers=a.jobs)
        r["seconds"] = round(time.time() - t0, 1)
        r["jvm_per_capture"] = True
        prev = json.loads(path.read_text()) if path.exists() else {}
        prev[tid] = r
        path.write_text(json.dumps(prev, indent=1, sort_keys=True) + "\n")
        print(f"{tid:14} vanilla={'pass' if r['vanilla_pass'] else 'FAIL ' + str(r['vanilla_failures'][:3])} nucleation={'pass' if r['nucleation_pass'] else 'FAIL'} "
              f"tick-identical={r['agree']}/{r['captures']} hold={r['hold']} vectors={r['vectors']} {r['seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
