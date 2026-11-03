#!/usr/bin/env python3
"""Oracle-gate every recorded session: replay its final build in vanilla Minecraft 26.2 exactly as env scored it.

    python scripts/oracle_sessions.py [--sessions SUBSTR,...] [--world both|handover|raw] [--hold env|auto|N]
                                      [--workers 3] [--batched] [--json OUT]

Sessions: every dir with a `state.json` under `--sessions-dir` (default data/sessions and data/sandbox/*/session; a sandbox copy whose
final build equals the data/sessions one is listed once). Per session one row:

    session | task | stored | env now | vanilla | nuc==van | raw paste | first tick divergence

* stored   = `full_pass` in the session's result.json when it was submitted;
* env now  = the CURRENT contract's verdict on the same blocks with the contract's `eval_seed` (what submit uses);
* vanilla  = every contract expectation holds in vanilla on the HANDOVER world (the env's at-rest world = runner.export_litematic),
             replayed with env's schedule (tools/oracle/envplan.py: env vector order, per-vector settle+extra ticks, public and hidden
             in one run, each sequence its own capture);
* nuc==van = whole-region tick comparison of Nucleation against vanilla on the same captures;
* raw paste = the same replay on the RAW block list (the program output as written, placed like a structure paste). It differs from
             the handover when the build's power-on state depends on how it is placed (bistable latches / locked repeaters):
             diagnostic, not the verdict.

`--hold auto` replaces env timing by a uniform hold (longest env settle x1.25 + 8, >= 40) in authoring order (the pre-2026-11-02
replay); `--hold N` uses N. Default is ONE JVM PER CAPTURE; `--batched` shares JVMs.
Exit status 1 if any session's env verdict and vanilla (handover) verdict disagree.
"""
import argparse
import hashlib
import json
import math
import os
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from env import runner
from env.score import score
from env.task import load_task
from tools.oracle import envplan, gametest
from tools.oracle.diff import diff_traces
from tools.oracle.sim import run_plan


def load_session(d: Path):
    st = json.loads((d / "state.json").read_text())
    res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else None
    blocks = json.loads((d / "final_blocks.json").read_text()) if (d / "final_blocks.json").exists() else None
    return st, res, blocks


def session_dirs(roots, want=None):
    """Session dirs under the roots (a root may be a glob); sandbox copies identical to an earlier dir are dropped."""
    seen, out = {}, []
    for root in roots:
        for base in sorted(ROOT.glob(root) if any(c in root for c in "*?[") else [Path(root)]):
            if not base.is_dir():
                continue
            for d in sorted(p for p in base.iterdir() if p.is_dir() and (p / "state.json").exists()):
                if want and not any(w in d.name for w in want):
                    continue
                fb = d / "final_blocks.json"
                h = hashlib.sha1(fb.read_bytes()).hexdigest() if fb.exists() else None
                if seen.get(d.name, "-") == h:
                    continue
                seen.setdefault(d.name, h)
                out.append(d)
    return out


CACHE = ROOT / "data/oracle/_cache"


def cached_env_run(d: Path, contract, blocks, task_file=None):
    """env.runner.run(contract, blocks) at eval_seed, pickled per (session, blocks, task file) under data/oracle/_cache
    (T4 env runs take minutes; the oracle replay can then run in a separate process)."""
    key = hashlib.sha1(json.dumps(blocks).encode() + (Path(task_file).read_bytes() if task_file else b"")).hexdigest()[:16]
    f = CACHE / f"{d.name}_{key}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    rr = runner.run(contract, blocks)
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_bytes(pickle.dumps(rr))
    return rr


def auto_hold(run_result) -> int:
    longest = max([v.ticks for v in run_result.vectors] or [0])
    return max(40, math.ceil(longest * 1.25) + 8)


def prepare_session(d: Path, hold_arg: str, preroll: int, worlds=("handover", "raw"), name=None):
    """-> (row skeleton, contract, [(world kind, job name, blocks, plan, checks)])."""
    st, res, blocks = load_session(d)
    name = name or d.name
    row = {"session": name, "dir": str(d.relative_to(ROOT)) if d.is_relative_to(ROOT) else str(d),
           "task": st.get("task_id") or st.get("task_ref"), "stored": None if res is None else bool(res.get("full_pass")),
           "arm": st.get("arm"), "label": st.get("label"), "env_now": None, "vanilla": None, "agree": None,
           "first_divergence": None, "note": "", "hold": None, "raw": None}
    if blocks is None:
        row["note"] = "no final_blocks.json (empty build)" if not st.get("cells") else "no final_blocks.json"
        return row, None, []
    task = load_task(st.get("task_ref") or st["task_id"])
    contract = task.contract
    pr = runner.prepare(contract, blocks)
    if pr.errors:
        row["note"] = "prepare errors: " + "; ".join(pr.errors[:2])
        return row, None, []
    rr = cached_env_run(d, contract, blocks, getattr(task, "path", None))   # seed=None -> eval_seed, as Env.submit
    row["env_now"] = bool(score(contract, rr).full_pass)
    row["env_max_vector_ticks"] = max([v.ticks for v in rr.vectors] or [0])
    jobs = []
    for kind in worlds:
        if kind == "handover":
            try:
                world = envplan.handover_world(contract, pr)
            except ValueError as e:
                row["note"] = f"no handover world: {e}"
                continue
        else:
            world = envplan.raw_world(pr)
        if hold_arg == "env":
            caps = envplan.env_captures(contract, rr, preroll, world=world)
            row["hold"] = "env"
        else:
            hold = auto_hold(rr) if hold_arg == "auto" else int(hold_arg)
            caps = envplan.captures(contract, hold, preroll, world=world)
            row["hold"] = hold
        jobs += [(kind, f"{name}__{kind}__{label.replace(':', '_')}", world, plan, checks) for label, plan, checks in caps]
    return row, contract, jobs


def summarise(rows, work, traces):
    """Fill the vanilla / Nucleation verdict columns of `rows` from the captures."""
    per = {}
    for w, ot in zip(work, traces):
        i, contract, kind, name, blocks, plan, checks = w
        st = run_plan(blocks, plan, origin=ot.origin)
        d = diff_traces(name, "session", ot, st, plan)
        r = per.setdefault((i, kind), {"vanilla_fail": [], "nuc_fail": [], "agree": True, "first": None, "ticks": 0,
                                       "ticks_equal": 0, "initial": None})
        cap = name.split("__", 2)[2]
        r["vanilla_fail"] += [f"{cap}:{f}" for f in envplan.evaluate(contract, ot, checks)]
        r["nuc_fail"] += [f"{cap}:{f}" for f in envplan.evaluate(contract, st, checks)]
        r["agree"] &= d.agree_full
        r["ticks"] += d.ticks_total
        r["ticks_equal"] += d.ticks_equal_full
        if r["initial"] is None:
            r["initial"] = ot.initial
        if r["first"] is None and d.first_full is not None:
            r["first"] = {"capture": cap, "tick": d.first_full[0], "pos": list(d.first_full[1]),
                          "vanilla": d.first_full[2], "nucleation": d.first_full[3]}
    for (i, kind), r in per.items():
        row = rows[i]
        if kind == "handover":
            row.update(vanilla=not r["vanilla_fail"], vanilla_failures=r["vanilla_fail"][:8], nucleation=not r["nuc_fail"],
                       nucleation_failures=r["nuc_fail"][:8], agree=r["agree"], ticks=r["ticks"], ticks_equal=r["ticks_equal"],
                       first_divergence=r["first"])
        else:
            row["raw"] = {"vanilla": not r["vanilla_fail"], "vanilla_failures": r["vanilla_fail"][:8],
                          "nucleation": not r["nuc_fail"], "agree": r["agree"], "first_divergence": r["first"]}
    for i, row in enumerate(rows):
        h, rw = per.get((i, "handover")), per.get((i, "raw"))
        if h and rw:   # cells whose vanilla at-rest state differs between the raw paste and the handover world
            a, b = h["initial"], rw["initial"]
            row["raw"]["initial_cells_differ"] = sum(a.get(p) != b.get(p) for p in set(a) | set(b))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions-dir", action="append",
                    help="session root (repeatable, globs allowed); default data/sessions and data/sandbox/*/session")
    ap.add_argument("--sessions", help="comma-separated substrings of session ids (default: all)")
    ap.add_argument("--world", default="both", choices=["both", "handover", "raw"])
    ap.add_argument("--hold", default="env", help="'env' (mirror env timing), 'auto' or ticks per vector")
    ap.add_argument("--preroll", type=int, default=20)
    ap.add_argument("--workers", type=int, default=3, help="JVMs at a time")
    ap.add_argument("--batched", action="store_true", help="share JVMs between captures instead of one JVM per capture")
    ap.add_argument("--json", help="write rows here (default data/oracle/sessions_<timestamp>.json)")
    ap.add_argument("--prepare-only", action="store_true", help="only run env (fills the cache) and exit")
    args = ap.parse_args()
    if not gametest.available():
        sys.exit("vanilla oracle not installed: bash scripts/oracle_setup.sh")

    roots = args.sessions_dir or [os.environ.get("MIRA_SESSIONS_DIR", "data/sessions"), "data/sandbox/*/session"]
    want = args.sessions.split(",") if args.sessions else None
    worlds = ("handover", "raw") if args.world == "both" else (args.world,)
    rows, work = [], []              # work: (row index, contract, world kind, name, blocks, plan, checks)
    names = set()
    for d in session_dirs(roots, want):
        name = d.name if d.name not in names else f"{d.name}~{d.parent.parent.name}"    # differing sandbox copy
        names.add(name)
        row, contract, jobs = prepare_session(d, args.hold, args.preroll, worlds, name)
        rows.append(row)
        work += [(len(rows) - 1, contract, *j) for j in jobs]
        if args.prepare_only:
            print(f"prepared {row['session']}: env {row['env_now']}, {len(jobs)} captures")
    if args.prepare_only:
        return
    t0 = time.perf_counter()
    jobs = [(w[3], w[4], w[5]) for w in work]
    traces = gametest.capture_many(jobs, workers=args.workers, isolated=not args.batched) if jobs else []
    wall = time.perf_counter() - t0
    summarise(rows, work, traces)

    yn = lambda v: "-" if v is None else "pass" if v else "FAIL"
    print(f"{'session':46s} {'task':17s} {'stored':6s} {'env now':7s} {'vanilla':7s} {'nuc==van':8s} {'raw paste':16s} tick divergence")
    bad = 0
    for row in rows:
        if row["agree"] is None:
            div = row["note"]
        elif row["agree"]:
            div = f"identical ({row['ticks']} ticks)"
        else:
            f = row["first_divergence"]
            div = f"{row['ticks_equal']}/{row['ticks']} equal; first {f['capture']} t={f['tick']} {f['pos']}"
        raw = row.get("raw")
        rawc = "-" if not raw else f"{yn(raw['vanilla'])} ({raw.get('initial_cells_differ', '?')} cells)"
        print(f"{row['session'][:46]:46s} {str(row['task'])[:17]:17s} {yn(row['stored']):6s} {yn(row['env_now']):7s} "
              f"{yn(row['vanilla']):7s} {'-' if row['agree'] is None else ('yes' if row['agree'] else 'NO'):8s} {rawc:16s} {div}")
        if row["vanilla"] is False and row.get("vanilla_failures"):
            print(f"    vanilla failures: {row['vanilla_failures'][:3]}")
        if raw and raw["vanilla"] is False:
            print(f"    raw-paste failures: {raw['vanilla_failures'][:3]}")
        if row["env_now"] is not None and row["vanilla"] is not None and row["env_now"] != row["vanilla"]:
            bad += 1
    print(f"\n{len(rows)} sessions, {len(work)} captures, {'batched' if args.batched else 'one JVM per capture'}, hold {args.hold}, "
          f"wall {wall:.0f}s; env/vanilla(handover) verdict disagreements: {bad}")
    out = Path(args.json) if args.json else ROOT / "data/oracle" / f"sessions_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows, "wall_s": wall, "batched": args.batched, "hold": args.hold,
                               "world": args.world}, indent=1, default=str))
    print("wrote", out)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
