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
from env.task import list_task_ids, load_task
from tools.oracle import envplan, gametest
from tools.oracle.diff import diff_traces
from tools.oracle.envplan import Levels, evaluate, read_out  # noqa: F401  (re-exported for older callers)
from tools.oracle.sim import run_plan

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "oracle")


def captures(contract, hold, preroll):
    """-> [(label, Plan, checks)]: authoring order, every vector held `hold` ticks (see tools/oracle/envplan.py)."""
    return envplan.captures(contract, hold, preroll)


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
    ap.add_argument("--isolated", action="store_true", help="one JVM per capture (slow; the reference verdicts)")
    ap.add_argument("--env-mirror", action="store_true",
                    help="replay as env scores: the env's at-rest (handover) world, env vector order and per-vector ticks "
                         "(tools/oracle/envplan.py); default is the raw block list, authoring order, fixed --hold")
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
            rr = runner.run(task.contract, sol, seed=None if args.env_mirror else 0)
            env_pass = bool(all(v.ok for v in rr.vectors) and all(s.ok for s in rr.sequences) and rr.compiled)
            caps = None
            if args.env_mirror:
                try:
                    blocks = envplan.handover_world(task.contract, pr)
                    caps = envplan.env_captures(task.contract, rr, args.preroll, world=blocks)
                except ValueError as e:      # never at rest: fall back to the raw list and fixed hold
                    print(f"{tid}{vname}: {e}; raw world, hold {args.hold}")
            for label, plan, checks in caps or captures(task.contract, args.hold, args.preroll):
                work.append((task, tid + vname, label, blocks, plan, checks, env_pass))
    t0 = time.perf_counter()
    oracle = gametest.capture_many([(f"{w[1]}__{w[2].replace(':', '_')}", w[3], w[4]) for w in work],
                                   workers=args.workers, isolated=args.isolated)
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
