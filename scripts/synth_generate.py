"""Generate verified synthetic tasks (no LLM): random netlists, sequential families, repair tasks.

    .venv/bin/python scripts/synth_generate.py netlist --n 120 --bucket easy --workers 8      # attempts per bucket
    .venv/bin/python scripts/synth_generate.py sequential --workers 8
    .venv/bin/python scripts/synth_generate.py repair --per-task 2 --workers 8
    .venv/bin/python scripts/synth_generate.py stats                                       # yields/distributions -> data/synth/stats.json

Every attempt (accepted or not) is logged to data/synth/attempts.jsonl with its timing and failure reason.
"""
import argparse
import json
import random
import sys
import traceback
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from training.synth import common as C  # noqa: E402

BUCKETS = {   # gates, inputs, outputs
    "easy": ((1, 2), (1, 3), (1, 1)),
    "medium": ((3, 4), (2, 4), (1, 2)),
    "hard": ((5, 7), (3, 6), (1, 3)),
}
ATTEMPTS = C.DATA / "attempts.jsonl"


def netlist_attempt(args):
    from training.synth.netlist import make_task, random_netlist
    bucket, seed, mutants = args[:3]
    router = len(args) > 3 and args[3]
    fam, pre = ("netlist_routed", "snr") if router else ("netlist", "sn")
    (g1, g2), (k1, k2), (m1, m2) = BUCKETS[bucket]
    rng = random.Random(f"{bucket}:{seed}")
    k, g, m = rng.randint(k1, k2), rng.randint(g1, g2), rng.randint(m1, m2)
    rec = {"family": fam, "bucket": bucket, "seed": seed, "k": k, "gates": g, "m": m, "accepted": False}
    with C.Timer() as t:
        try:
            nl = random_netlist(rng, k, g, m)
            if nl is None:
                rec["reason"] = "no netlist"
            elif not C.claim(fam, {**nl.to_dict(), "gates": [[n, k, sorted(i)] for n, k, i in nl.to_dict()["gates"]]}):
                rec["reason"] = "duplicate netlist"
            else:
                tid = f"{pre}_{bucket}_{seed:05d}"
                task, prog, info = make_task(nl, tid, seed=seed, router=router)
                if not C.program_matches(task, prog):
                    raise ValueError("reference program does not load to the task solution through the harness build rules")
                rep = C.gate(task, mutants)
                rec.update(id=tid, blocks=len(task.solution), volume=task.contract.reference["volume"], depth=info["depth"],
                           m=len(nl.outputs), kill=f"{rep.get('killed', 0)}/{rep.get('mutants', 0)}", accepted=rep["accepted"],
                           reason="; ".join(rep["problems"]), gate_s=rep.get("seconds"))
                if rep["accepted"]:
                    C.save(task, prog, {"family": fam, "source": f"synth/{fam}", "difficulty": bucket, "seed": seed,
                                        "netlist": info["netlist"], "depth": info["depth"], "uses": info["uses"],
                                        "kill": rec["kill"], "n_blocks": len(task.solution), "router": bool(router)}, fam)
        except Exception as e:                                  # routing conflicts arrive here as ValueError from the compile
            rec["reason"] = f"{type(e).__name__}: {str(e)[:300]}"
            if not isinstance(e, ValueError):
                rec["trace"] = traceback.format_exc()[-800:]
    rec["seconds"] = t.s
    return rec


def run_attempts(fn, items, workers):
    ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
    ok = n = 0
    with ATTEMPTS.open("a") as fh:
        for rec in C.pmap(fn, items, workers):
            n += 1
            ok += rec["accepted"]
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"[{n}/{len(items)}] {rec.get('id', rec.get('family'))} {'OK ' if rec['accepted'] else 'REJ'} "
                  f"{rec.get('blocks', '-')} blocks {rec['seconds']}s {rec.get('reason', '')[:120]}", flush=True)
    print(f"accepted {ok}/{n}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["netlist", "sequential", "repair", "extend", "stats"])
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--bucket", default="easy", choices=list(BUCKETS))
    ap.add_argument("--mutants", type=int, default=8)
    ap.add_argument("--per-task", type=int, default=2)
    ap.add_argument("--workers", type=int, default=C.MAX_WORKERS)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--router", action="store_true", help="netlist: route nets with simulation/router.py (family netlist_routed)")
    a = ap.parse_args(argv)
    if a.what == "netlist":
        run_attempts(netlist_attempt, [(a.bucket, s, a.mutants, a.router) for s in range(a.start, a.start + a.n)], a.workers)
    elif a.what == "sequential":
        from training.synth import sequential as S
        run_attempts(S.attempt, S.jobs(a.mutants), a.workers)
    elif a.what in ("repair", "extend"):
        from training.synth import repair as R
        jobs = R.jobs(a.what, a.per_task, a.limit)
        run_attempts(R.attempt, jobs, a.workers)
    else:
        from training.synth import stats
        print(json.dumps(stats.summary(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
