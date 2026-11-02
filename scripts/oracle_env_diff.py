#!/usr/bin/env python3
"""Cross-check env/ tasks' reference solutions against the vanilla oracle (env/ is read-only here).

    python scripts/oracle_env_diff.py [--tasks t0_lamp,t1_xor] [--hold 40] [--workers 4]

For each task: world = fixed cells + reference solution, stimulus derived from the contract
(truth-table / reference-model vectors in authoring order held `--hold` ticks each; every sequence as its
own capture). Reports per capture (a) tick-by-tick Nucleation-vs-vanilla agreement over the whole region,
(b) whether the contract's expectations hold in vanilla and in Nucleation, (c) env.runner's own verdict.
Ports are read after tick t-1 for env trace index t (same convention as env/runner.py).
"""
import argparse
import json
import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from env import runner
from env.contract import box_cells
from env.task import list_task_ids, load_task
from env.sim import parse
from tools.oracle import gametest
from tools.oracle.diff import diff_traces
from tools.oracle.sim import run_plan
from tools.oracle.trace import Plan, put, use

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "oracle")


class Levels:
    """Builds ops from port-value assignments, mirroring env.runner.Driver (levers start off)."""

    def __init__(self, contract):
        self.ports = {p.name: p for p in contract.ports}
        self.lv = {}
        self.ops = []

    def set(self, tick, name, value):
        p, value = self.ports[name], int(value)
        if p.kind == "pad":
            if self.lv.get(name, 0) != value:
                self.ops.append(put(tick, p.pos, "redstone_block" if value else "air"))
        elif p.pulse:
            if value:
                self.ops.append(use(tick, p.pos))
            return
        elif self.lv.get(name, 0) != value:
            self.ops.append(use(tick, p.pos))
        self.lv[name] = value


def read_out(contract, state, pname):
    """Output port value from a {pos: state} map (same rules as runner.read_port)."""
    p = contract.port(pname)
    get = lambda pos: state.get(tuple(pos), "minecraft:air")
    if p.kind == "region":
        cells = [get(c) for c in box_cells(p.box)]
        air = lambda s: s.startswith("minecraft:air")
        if p.region_mode == "air":
            return int(all(air(c) for c in cells))
        if p.region_mode == "solid":
            return int(all(not air(c) for c in cells))
        return int(all(parse(c)[0] == p.block for c in cells))
    bid, props = parse(get(p.pos))
    name = bid.split(":")[-1]
    return {"lamp": int(name == "redstone_lamp" and props.get("lit") == "true"),
            "wire_power": int(name == "redstone_wire" and int(props.get("power", "0")) >= p.min_power),
            "piston": int(name in ("piston", "sticky_piston") and props.get("extended") == "true"),
            "torch": int(name in ("redstone_torch", "redstone_wall_torch") and props.get("lit") == "true"),
            "block_prop": int(props.get((p.prop or {}).get("prop")) == str((p.prop or {}).get("value")))
            }.get(p.kind, -1)


def captures(contract, hold, preroll):
    """-> [(label, Plan, checks)], checks = [(read_index, port, expected_or_None)]; expected may be init/!init."""
    outs = [p.name for p in contract.outputs]
    probes = [tuple(p.pos) for p in contract.outputs if p.pos]
    jobs = []
    vecs = contract.vectors()
    if vecs:
        lv, checks = Levels(contract), []
        for i, v in enumerate(vecs):
            for k, val in v.inputs.items():
                lv.set(i * hold, k, val)
            for k, e in v.expect.items():
                if e is not None:
                    checks.append(((i + 1) * hold, k, e, f"vec{i}:{v.label}"))
        jobs.append(("vectors", Plan(len(vecs) * hold, lv.ops, probes, "place", preroll), checks))
    for s in contract.behaviour.sequences:
        lv, checks = Levels(contract), []
        for e in sorted(s.events, key=lambda e: e.t):
            for k, val in e.set.items():
                lv.set(e.t, k, val)
        for e in s.expect:
            for t in range(e.t1, e.t2 + 1) if e.mode == "all" else [None]:
                checks.append((t, e.port, e.value, f"{s.name}[{e.t1}-{e.t2},{e.mode}]", e))
        jobs.append((f"seq:{s.name}", Plan(s.duration + 1, lv.ops, probes, "place", preroll), checks))
    return jobs


def evaluate(contract, trace, checks):
    """Failed check labels. Handles 'init'/'!init' and mode=any windows."""
    outs = [p.name for p in contract.outputs]
    rd = lambda idx, port: read_out(contract, trace.state_at(idx - 1), port)
    init = {p: read_out(contract, trace.state_at(-1), p) for p in outs}
    res = lambda v, port: init[port] if v == "init" else 1 - init[port] if v == "!init" else v
    bad, any_seen = [], {}
    for chk in checks:
        t, port, val, label = chk[:4]
        want = res(val, port)
        if len(chk) == 5 and chk[4].mode == "any":
            e = chk[4]
            if any(rd(i, port) == want for i in range(e.t1, min(e.t2, trace.n_ticks) + 1)):
                continue
            bad.append(label)
        elif t <= trace.n_ticks and rd(t, port) != want:
            bad.append(f"{label}@{t}")
    return sorted(set(bad))


def apply_edits(solution, edits):
    cells = {tuple(b[:3]): list(b) for b in solution}
    for e in edits:
        pos = tuple(e[1])
        if e[0] == "remove":
            cells.pop(pos, None)
        elif e[0] == "set":
            cells[pos] = [*pos, e[2]]
        else:
            raise ValueError(f"unknown edit {e}")
    return list(cells.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks")
    ap.add_argument("--hold", type=int, default=40, help="ticks per truth-table vector")
    ap.add_argument("--preroll", type=int, default=20, help="idle ticks after placement before tick 0")
    ap.add_argument("--broken", action="store_true",
                    help="also run each task's hand-written broken variants (negative controls: must fail)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tag", default=time.strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()
    ids = args.tasks.split(",") if args.tasks else list_task_ids()
    work = []                    # (task, id, label, blocks, plan, checks, env_pass)
    for tid in ids:
        task = load_task(tid)
        variants = [("", task.solution)]
        if args.broken:
            variants += [(f"~{v['name']}", apply_edits(task.solution, v["edits"])) for v in task.broken]
        for vname, sol in variants:
            pr = runner.prepare(task.contract, sol)
            if pr.errors:
                print(f"{tid}{vname}: prepare errors {pr.errors[:2]}")
                continue
            blocks = [(*p, s, pr.nbt.get(p)) for p, s in pr.world.items()]
            rr = runner.run(task.contract, sol, seed=0)
            env_pass = bool(all(v.ok for v in rr.vectors) and all(s.ok for s in rr.sequences) and rr.compiled)
            for label, plan, checks in captures(task.contract, args.hold, args.preroll):
                work.append((task, tid + vname, label, blocks, plan, checks, env_pass))
    t0 = time.perf_counter()
    oracle = gametest.capture_many([(f"{w[1]}__{w[2].replace(':', '_')}", w[3], w[4]) for w in work],
                                   workers=args.workers)
    wall = time.perf_counter() - t0
    rows, agree = [], 0
    print(f"{'capture':34s} {'sim==vanilla':14s} {'vanilla contract':18s} {'nucleation contract':20s} env")
    for w, ot in zip(work, oracle):
        task, tid, label, blocks, plan, checks, env_pass = w
        st = run_plan(blocks, plan, origin=ot.origin)
        d = diff_traces(f"{tid}:{label}", "env", ot, st, plan)
        vb, sb = evaluate(task.contract, ot, checks), evaluate(task.contract, st, checks)
        agree += d.agree_full
        rows.append({"task": tid, "capture": label, "agree_full": d.agree_full, "ticks_equal": d.ticks_equal_full,
                     "ticks": d.ticks_total, "vanilla_failures": vb, "nucleation_failures": sb,
                     "env_pass": env_pass, "first_divergence": d.first_full})
        print(f"{tid[:60] + ':' + label:34s} {('AGREE' if d.agree_full else f'{d.ticks_equal_full}/{d.ticks_total}'):14s} "
              f"{'pass' if not vb else 'FAIL ' + str(vb[:2]):18s} {'pass' if not sb else 'FAIL ' + str(sb[:2]):20s} "
              f"{'pass' if env_pass else 'FAIL'}")
    print(f"\n{agree}/{len(rows)} captures fully agree tick-by-tick; oracle wall {wall:.1f}s with {args.workers} JVMs")
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"env_diff_{args.tag}.json")
    json.dump({"rows": rows, "oracle_wall_s": wall}, open(path, "w"), indent=1, default=str)
    print("wrote", os.path.abspath(path))


if __name__ == "__main__":
    main()
