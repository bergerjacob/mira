#!/usr/bin/env python3
"""Oracle-gate every recorded session: replay the final build of data/sessions/*/ in vanilla Minecraft 26.2.

    python scripts/oracle_sessions.py [--sessions SUBSTR,...] [--hold auto|N] [--workers 3] [--batched] [--json OUT]

For each session with a final build (`final_blocks.json`): load the task's contract, place `fixed + final build`, replay the
task's stimulus plan exactly as scripts/oracle_env_diff.py does (every truth-table / reference-model vector held `--hold` ticks, every
sequence its own capture, hidden ones included), and print one row:

    session | task | stored | env now | vanilla | nucleation==vanilla | first tick divergence

* stored  = `full_pass` in the session's result.json when it was submitted;
* env now = the CURRENT contract's verdict on the same blocks (differs from `stored` when the spec was fixed since);
* vanilla = every contract expectation holds in the vanilla trace (names the first failing checks otherwise);
* tick divergence = whole-region comparison of Nucleation against vanilla ("identical" or first differing tick/cell).

Default is ONE JVM PER CAPTURE (`isolated=True`): a verdict never depends on which jobs shared a JVM. `--batched` shares JVMs
(faster; tests/oracle/test_isolation.py shows it is trace-identical since the 2026-11-02 fix). `--hold auto` (default) takes the
longest settle the env needed for any vector of that build, x1.25 + 8, at least 40 ticks, so slow circuits (ripple adders) are
given the time the env gave them; a fixed number is used as is.
Exit status 1 if any session that the env passes fails in vanilla (or vice versa).
"""
import argparse
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from env import runner
from env.score import score
from env.task import load_task
from tools.oracle import gametest
from tools.oracle.diff import diff_traces
from tools.oracle.sim import run_plan

spec = importlib.util.spec_from_file_location("oracle_env_diff", ROOT / "scripts/oracle_env_diff.py")
oed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oed)


def load_session(d: Path):
    st = json.loads((d / "state.json").read_text())
    res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else None
    blocks = json.loads((d / "final_blocks.json").read_text()) if (d / "final_blocks.json").exists() else None
    return st, res, blocks


def auto_hold(run_result) -> int:
    longest = max([v.ticks for v in run_result.vectors] or [0])
    return max(40, math.ceil(longest * 1.25) + 8)


def prepare_session(d: Path, hold_arg: str, preroll: int):
    """-> dict row skeleton + list of (job name, blocks, plan, checks) (empty when the build cannot be replayed)."""
    st, res, blocks = load_session(d)
    row = {"session": d.name, "task": st.get("task_id") or st.get("task_ref"), "stored": None if res is None else bool(res.get("full_pass")),
           "arm": st.get("arm"), "label": st.get("label"), "env_now": None, "vanilla": None, "agree": None,
           "first_divergence": None, "note": "", "hold": None}
    if blocks is None:
        row["note"] = "no final_blocks.json (empty build)" if not st.get("cells") else "no final_blocks.json"
        return row, None, []
    task = load_task(st.get("task_ref") or st["task_id"])
    contract = task.contract
    pr = runner.prepare(contract, blocks)
    if pr.errors:
        row["note"] = "prepare errors: " + "; ".join(pr.errors[:2])
        return row, None, []
    rr = runner.run(contract, blocks, seed=0)
    row["env_now"] = bool(score(contract, rr).full_pass)
    hold = auto_hold(rr) if hold_arg == "auto" else int(hold_arg)
    row["hold"] = hold
    world = [(*p, s, pr.nbt.get(p)) for p, s in pr.world.items()]
    jobs = [(f"{d.name}__{label.replace(':', '_')}", world, plan, checks)
            for label, plan, checks in oed.captures(contract, hold, preroll)]
    return row, contract, jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions-dir", default=os.environ.get("MIRA_SESSIONS_DIR", str(ROOT / "data/sessions")))
    ap.add_argument("--sessions", help="comma-separated substrings of session ids (default: all)")
    ap.add_argument("--hold", default="auto", help="ticks per truth-table vector, or 'auto'")
    ap.add_argument("--preroll", type=int, default=20)
    ap.add_argument("--workers", type=int, default=3, help="JVMs at a time")
    ap.add_argument("--batched", action="store_true", help="share JVMs between captures instead of one JVM per capture")
    ap.add_argument("--json", help="write rows here (default data/oracle/sessions_<timestamp>.json)")
    args = ap.parse_args()
    if not gametest.available():
        sys.exit("vanilla oracle not installed: bash scripts/oracle_setup.sh")

    want = args.sessions.split(",") if args.sessions else None
    dirs = sorted(p for p in Path(args.sessions_dir).iterdir()
                  if p.is_dir() and (p / "state.json").exists() and (want is None or any(w in p.name for w in want)))
    rows, work = [], []              # work: (row index, contract, name, blocks, plan, checks)
    for d in dirs:
        row, contract, jobs = prepare_session(d, args.hold, args.preroll)
        rows.append(row)
        work += [(len(rows) - 1, contract, *j) for j in jobs]
    t0 = time.perf_counter()
    jobs = [(w[2], w[3], w[4]) for w in work]
    if args.batched:
        traces = gametest.capture_many(jobs, workers=args.workers)
    else:
        traces = gametest.capture_many(jobs, workers=args.workers, isolated=True)
    wall = time.perf_counter() - t0

    per = {}
    for w, ot in zip(work, traces):
        i, contract, name, blocks, plan, checks = w
        st = run_plan(blocks, plan, origin=ot.origin)
        d = diff_traces(name, "session", ot, st, plan)
        r = per.setdefault(i, {"vanilla_fail": [], "nuc_fail": [], "agree": True, "first": None, "ticks": 0, "ticks_equal": 0})
        r["vanilla_fail"] += [f"{name.split('__', 1)[1]}:{f}" for f in oed.evaluate(contract, ot, checks)]
        r["nuc_fail"] += oed.evaluate(contract, st, checks)
        r["agree"] &= d.agree_full
        r["ticks"] += d.ticks_total
        r["ticks_equal"] += d.ticks_equal_full
        if r["first"] is None and d.first_full is not None:
            r["first"] = {"capture": name.split("__", 1)[1], "tick": d.first_full[0], "pos": list(d.first_full[1]),
                          "vanilla": d.first_full[2], "nucleation": d.first_full[3]}
    for i, r in per.items():
        row = rows[i]
        row["vanilla"] = not r["vanilla_fail"]
        row["vanilla_failures"] = r["vanilla_fail"][:6]
        row["nucleation"] = not r["nuc_fail"]
        row["agree"] = r["agree"]
        row["ticks"], row["ticks_equal"] = r["ticks"], r["ticks_equal"]
        row["first_divergence"] = r["first"]

    yn = lambda v: "-" if v is None else "pass" if v else "FAIL"
    print(f"{'session':44s} {'task':18s} {'stored':7s} {'env now':8s} {'vanilla':8s} {'nuc==van':9s} tick divergence")
    bad = 0
    for row in rows:
        if row["agree"] is None:
            div = row["note"]
        elif row["agree"]:
            div = f"identical ({row['ticks']} ticks)"
        else:
            f = row["first_divergence"]
            div = f"{row['ticks_equal']}/{row['ticks']} equal; first {f['capture']} t={f['tick']} {f['pos']}"
        print(f"{row['session']:44s} {str(row['task']):18s} {yn(row['stored']):7s} {yn(row['env_now']):8s} {yn(row['vanilla']):8s} "
              f"{'-' if row['agree'] is None else ('yes' if row['agree'] else 'NO'):9s} {div}")
        if row["vanilla"] is False and row.get("vanilla_failures"):
            print(f"    vanilla failures: {row['vanilla_failures'][:3]}")
        if row["env_now"] is not None and row["vanilla"] is not None and row["env_now"] != row["vanilla"]:
            bad += 1
    print(f"\n{len(rows)} sessions, {len(work)} captures, {'batched' if args.batched else 'one JVM per capture'}, wall {wall:.0f}s; "
          f"env/vanilla verdict disagreements: {bad}")
    out = Path(args.json) if args.json else ROOT / "data/oracle" / f"sessions_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows, "wall_s": wall, "batched": args.batched}, indent=1, default=str))
    print("wrote", out)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
