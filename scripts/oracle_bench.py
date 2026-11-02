#!/usr/bin/env python3
"""Oracle throughput: isolation check (batch vs one-JVM-per-job) and scaling over parallel JVMs.

    python scripts/oracle_bench.py --workers 1,2,4,8 --repeat 8
"""
import argparse
import os
import resource
import sys
import threading
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.oracle import gametest
from tools.oracle.circuits import all_circuits


def mem_used_gb():
    kv = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
    return (kv["MemTotal"] - kv["MemAvailable"]) / 1048576


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", default="1,2,4,8")
    ap.add_argument("--repeat", type=int, default=8, help="copies of the circuit set per run")
    ap.add_argument("--skip-isolation", action="store_true")
    args = ap.parse_args()
    cs = all_circuits()
    jobs = [(c.name, c.blocks, c.plan) for c in cs]

    t0 = time.perf_counter()
    one = gametest.capture_batch(jobs[:1])
    print(f"single capture, fresh JVM: {time.perf_counter() - t0:.2f}s wall "
          f"(boot {one[0].meta['boot_ms']:.0f} ms, job {one[0].meta['job_ms']:.0f} ms, "
          f"max child RSS {resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024:.0f} MB)")

    if not args.skip_isolation:
        batch = gametest.capture_batch(jobs)
        solo = gametest.capture_many(jobs, workers=len(jobs))     # one JVM per job
        bad = [c.name for c, a, b in zip(cs, batch, solo)
               if a.changes != b.changes or a.placed != b.placed or a.initial != b.initial]
        print(f"isolation: {len(jobs) - len(bad)}/{len(jobs)} batch traces identical to fresh-JVM traces"
              + (f"; DIFFER: {bad}" if bad else ""))

    big = [(f"{n}_{i}", b, p) for i in range(args.repeat) for n, b, p in jobs]
    print(f"\nscaling over {len(big)} jobs ({args.repeat} x {len(jobs)} circuits), "
          f"{os.cpu_count()} cores, baseline RAM used {mem_used_gb():.1f} GB")
    for w in [int(x) for x in args.workers.split(",")]:
        peak = [mem_used_gb()]
        stop = threading.Event()

        def sample():
            while not stop.wait(0.25):
                peak.append(mem_used_gb())
        th = threading.Thread(target=sample)
        th.start()
        t0 = time.perf_counter()
        gametest.capture_many(big, workers=w)
        wall = time.perf_counter() - t0
        stop.set()
        th.join()
        print(f"  workers={w:2d}: {wall:6.1f}s  {len(big) / wall:6.1f} captures/s  "
              f"peak system RAM +{max(peak) - peak[0]:.1f} GB")


if __name__ == "__main__":
    main()
