#!/usr/bin/env python3
"""Differential: same build + stimulus in Nucleation's TickSimulation and the vanilla oracle.

    python scripts/oracle_diff.py                         # all hand-written circuits
    python scripts/oracle_diff.py --circuits piston_qc,torch_not_gate --workers 4
    python scripts/oracle_diff.py --sim-only              # no oracle needed (sanity of circuits)

Writes data/oracle/diff_<tag>.json (per-circuit traces' summary + divergences, plus repro
circuits under data/oracle/repros/) and prints per-mechanic agreement.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import replace

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.oracle import gametest
from tools.oracle.circuits import all_circuits
from tools.oracle.diff import diff_traces, summarize
from tools.oracle.sim import run_plan

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "oracle")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--circuits", help="comma-separated names (default: all)")
    ap.add_argument("--workers", type=int, default=1, help="parallel oracle JVMs")
    ap.add_argument("--settle", choices=["place", "known_shape"], help="override plan.settle")
    ap.add_argument("--preroll", type=int, help="override plan.preroll (idle ticks before tick 0)")
    ap.add_argument("--idle", type=int, default=10, help="idle ticks between jobs in a JVM")
    ap.add_argument("--repros", action="store_true", help="also run the minimal divergence repros")
    ap.add_argument("--sim-only", action="store_true")
    ap.add_argument("--tag", default=time.strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()

    circuits = all_circuits(repros=args.repros)
    if args.circuits:
        want = set(args.circuits.split(","))
        circuits = [c for c in circuits if c.name in want]
        missing = want - {c.name for c in circuits}
        if missing:
            sys.exit(f"unknown circuits: {sorted(missing)}")
    for c in circuits:
        if args.settle:
            c.plan = replace(c.plan, settle=args.settle)
        if args.preroll is not None:
            c.plan = replace(c.plan, preroll=args.preroll)

    os.makedirs(os.path.join(OUT, "repros"), exist_ok=True)
    if args.sim_only or not gametest.available():
        if not args.sim_only:
            print("oracle not installed (scripts/oracle_setup.sh); running Nucleation only")
        for c in circuits:
            tr = run_plan(c.blocks, c.plan)
            print(f"{c.name:24s} sim ok, {sum(len(v) for v in tr.changes.values())} changes")
        return 0

    t0 = time.perf_counter()
    oracle = gametest.capture_many([(c.name, c.blocks, c.plan) for c in circuits],
                                   workers=args.workers, idle=args.idle)
    oracle_wall = time.perf_counter() - t0
    results, rows = [], []
    for c, ot in zip(circuits, oracle):
        st = run_plan(c.blocks, c.plan, origin=ot.origin)
        r = diff_traces(c.name, c.mechanic, ot, st, c.plan, c.expect)
        results.append(r)
        status = "AGREE" if r.agree_full else ("probes-ok" if r.agree_probes else "DIVERGE")
        print(f"{c.name:24s} {status:9s} placed={'=' if r.placed_equal else '!='} "
              f"full {r.ticks_equal_full}/{r.ticks_total} probes {r.ticks_equal_probes}/{r.ticks_total}"
              + (f"  ORACLE-EXPECT-FAIL {r.expect_failures}" if r.expect_failures else ""))
        if not r.agree_full:
            rows.append(r)
            with open(os.path.join(OUT, "repros", f"{c.name}.json"), "w") as f:
                json.dump({"name": c.name, "mechanic": c.mechanic, "description": c.description,
                           "blocks": [list(b) for b in c.blocks], "plan": c.plan.to_json(),
                           "first_divergence": r.first_full and
                           {"tick": r.first_full[0], "pos": list(r.first_full[1]),
                            "oracle": r.first_full[2], "sim": r.first_full[3]}}, f, indent=1)
    summ = summarize(results)
    print("\nper-mechanic agreement (circuits fully agreeing / total; tick agreement):")
    for m, s in sorted(summ.items()):
        print(f"  {m:14s} {s['agree_full']}/{s['circuits']} circuits  "
              f"ticks full {s['ticks_equal_full']}/{s['ticks']}  probes {s['ticks_equal_probes']}/{s['ticks']}  "
              f"placed {s['placed_equal']}/{s['circuits']}")
    tot = sum(r.agree_full for r in results)
    print(f"\nTOTAL fully agreeing: {tot}/{len(results)}  (oracle wall {oracle_wall:.1f}s, "
          f"{args.workers} JVM(s))")
    ot = oracle[0]
    print(f"oracle: MC {ot.mc_version} DataVersion {ot.data_version} origin {ot.origin}; "
          f"nucleation DataVersion {run_plan(circuits[0].blocks, circuits[0].plan, origin=ot.origin).data_version}")
    out = {"tag": args.tag, "oracle_wall_s": oracle_wall, "workers": args.workers,
           "oracle": {"mc_version": ot.mc_version, "data_version": ot.data_version},
           "summary": summ, "results": [r.to_json() for r in results]}
    path = os.path.join(OUT, f"diff_{args.tag}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("wrote", os.path.abspath(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
