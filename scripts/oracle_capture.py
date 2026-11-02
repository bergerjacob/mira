#!/usr/bin/env python3
"""Capture a per-tick trace from the vanilla oracle (and optionally Nucleation) for one build.

    python scripts/oracle_capture.py --circuit lever_wire_lamp --out trace.json
    python scripts/oracle_capture.py build.litematic --plan plan.json --out trace.json --sim

plan.json: {"ticks": 40, "ops": [{"tick": 2, "op": "use", "pos": [0,1,0]},
                                  {"tick": 5, "op": "set", "pos": [1,1,0], "block": "redstone_block"}],
            "probes": [[4,1,0]], "settle": "place", "preroll": 0}
Prints probe timelines (state of each probe after each tick where it changes).
"""
import argparse
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.oracle import gametest
from tools.oracle.circuits import all_circuits
from tools.oracle.sim import run_plan
from tools.oracle.trace import Op, Plan


def load_plan(d) -> Plan:
    ops = [Op(o["tick"], o["op"], tuple(o["pos"]), o.get("block")) for o in d.get("ops", [])]
    return Plan(d["ticks"], ops, [tuple(p) for p in d.get("probes", [])], d.get("settle", "place"),
                d.get("preroll", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("build", nargs="?", help=".litematic/.schem file")
    ap.add_argument("--circuit", help="name of a built-in circuit instead of a file")
    ap.add_argument("--plan", help="plan JSON (required with a build file)")
    ap.add_argument("--out", help="write the oracle trace JSON here")
    ap.add_argument("--sim", action="store_true", help="also run Nucleation and print first divergence")
    args = ap.parse_args()

    dv = None
    if args.circuit:
        c = next(c for c in all_circuits() if c.name == args.circuit)
        blocks, plan = c.blocks, c.plan
    else:
        if not (args.build and args.plan):
            ap.error("need BUILD and --plan, or --circuit")
        (blocks, dv), plan = gametest.load_build(args.build), load_plan(json.load(open(args.plan)))
    tr = gametest.capture(blocks, plan, source_data_version=dv)
    print(f"oracle: MC {tr.mc_version} DataVersion {tr.data_version} origin {tr.origin} "
          f"job {tr.meta['job_ms']:.0f} ms (boot {tr.meta['boot_ms']:.0f} ms, ticks {tr.meta['tick_ms']:.0f} ms)")
    for p in plan.probes:
        last, line = None, []
        for t, s in enumerate(tr.probe(p)):
            if s != last:
                line.append(f"t{t}:{s.replace('minecraft:', '')}")
                last = s
        print(f"  probe {p}: " + " | ".join(line))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(tr.to_json(), f)
    if args.sim:
        from tools.oracle.trace import first_divergence
        st = run_plan(blocks, plan, origin=tr.origin)
        print("first divergence vs nucleation:", first_divergence(tr, st))


if __name__ == "__main__":
    main()
