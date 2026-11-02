"""Task acceptance: the reference must pass (reward 1.0) and broken variants must fail.

    python env/validate_tasks.py [task_id ...] [--mutants N] [--json]

A task is ACCEPTED iff: spec validates; reference is a full pass with every invariant; the empty build fails;
every output is expected in both states (non-vacuous); >= 3 broken variants (auto-mutated + hand-written)
are not full passes. Also reports the mutation kill rate (spec strength). Exit status 1 if any task is rejected.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from env import mutate
from env.runner import run
from env.score import score
from env.task import list_task_ids, load_task, save_task

MIN_BROKEN = 3
MAX_STORED = 5


def nonvacuous(contract):
    """Each output must be expected as both 0 and 1 somewhere (constant outputs score 0 anyway)."""
    seen = {p.name: set() for p in contract.outputs}
    for v in contract.vectors():
        for k, e in v.expect.items():
            if e is not None:
                seen[k].add(e)
    for s in contract.behaviour.sequences:
        for e in s.expect:
            seen[e.port] |= {0, 1} if isinstance(e.value, str) else {e.value}
    return [k for k, v in seen.items() if len(v) < 2]


def evaluate(task, max_mutants=30, seed=0):
    c = task.contract
    rep = {"id": task.id, "tier": c.tier, "ref_blocks": len(task.solution), "problems": [], "accepted": False}
    t0 = time.time()
    errs = c.validate()
    if errs:
        rep["problems"] += [f"spec: {e}" for e in errs]
        return rep
    flat = [b for b in nonvacuous(c)]
    if flat:
        rep["problems"].append(f"vacuous outputs (never expected in both states): {flat}")
    res = run(c, task.solution, seed=seed)
    sc = score(c, res)
    rep["ref"] = sc.to_dict()
    if not sc.full_pass or abs(sc.reward - 1.0) > 1e-9:
        rep["problems"].append("reference does not fully pass")
        rep["ref_failures"] = {
            "errors": res.prepared.errors, "place_errors": [(list(p), s, m) for p, s, m in res.place_errors][:3],
            "invariants": {k: v["detail"] for k, v in res.invariant_failures().items()},
            "vectors": [(v.inputs, v.expect, v.got) for v in res.vectors if not v.ok][:3],
            "sequences": [(q.name, [(w.port, w.value, w.t1, w.t2, w.first_bad) for w in q.windows if not w.ok]) for q in res.sequences if not q.ok],
            "per_output": sc.per_output,
        }
    empty = score(c, run(c, [], seed=seed, fast=True))
    rep["empty_reward"] = round(empty.reward, 3)
    if empty.full_pass or empty.r3 >= 0.5:
        rep["problems"].append("empty build passes (spec too weak)")
    sig = lambda m: tuple(tuple(map(str, e)) for e in m["edits"])
    cands = [{"name": b["name"], "kind": b.get("kind", "manual"), "edits": [tuple(e) for e in b["edits"]]} for b in task.broken]
    seen = {sig(m) for m in cands}
    cands += [m for m in mutate.pick(task.solution, c.build_box, seed=seed)[:max_mutants] if sig(m) not in seen]
    killed, survived = [], []
    for m in cands:
        r = run(c, mutate.apply(task.solution, m["edits"]), seed=seed, fast=True)
        s = score(c, r)
        (survived if s.full_pass else killed).append({"name": m["name"], "kind": m["kind"], "reward": round(s.reward, 3),
                                                       "r3": round(s.r3, 3), "edits": [list(e) for e in m["edits"]]})
    rep["mutants"] = len(cands)
    rep["killed"] = len(killed)
    rep["kill_rate"] = round(len(killed) / max(1, len(cands)), 3)
    rep["killed_kinds"] = sorted({k["kind"] for k in killed})
    # keep a diverse, stable set of failing variants (one per kind first) so demos/tests do not re-sweep
    keep, seen = [], set()
    for k in killed:
        if k["kind"] not in seen:
            keep.append(k)
            seen.add(k["kind"])
    keep += [k for k in killed if k not in keep]
    rep["broken_examples"] = keep[:MAX_STORED]
    rep["survivors"] = [s["name"] for s in survived[:6]]
    if len(killed) < MIN_BROKEN:
        rep["problems"].append(f"only {len(killed)} broken variants fail (need {MIN_BROKEN})")
    rep["accepted"] = not rep["problems"]
    rep["seconds"] = round(time.time() - t0, 1)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--mutants", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-broken", action="store_true", help="store the diverse failing variants in each accepted task's JSON")
    a = ap.parse_args(argv)
    ids = a.ids or list_task_ids()
    reps = []
    for i in ids:
        task = load_task(i)
        rep = evaluate(task, a.mutants)
        reps.append(rep)
        if a.write_broken and rep["accepted"]:
            task.broken = [{"name": b["name"], "kind": b["kind"], "edits": b["edits"]} for b in rep["broken_examples"]]
            save_task(task)
    if a.json:
        print(json.dumps(reps, indent=1))
    else:
        print(f"{'task':22} {'blocks':>6} {'ref':>5} {'kill':>9} {'status'}")
        for r in reps:
            ref = r.get("ref", {}).get("reward", 0)
            kill = f"{r.get('killed', 0)}/{r.get('mutants', 0)}"
            print(f"{r['id']:22} {r['ref_blocks']:>6} {ref:>5.2f} {kill:>9} {'ACCEPT' if r['accepted'] else 'REJECT ' + '; '.join(r['problems'])}")
            if r.get("ref_failures"):
                print("    ", json.dumps(r["ref_failures"])[:700])
        print(f"{sum(r['accepted'] for r in reps)}/{len(reps)} accepted")
    return 0 if all(r["accepted"] for r in reps) else 1


if __name__ == "__main__":
    sys.exit(main())
