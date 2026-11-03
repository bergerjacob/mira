#!/usr/bin/env python3
"""Position / rotation / placement-order robustness of env verdicts, with vanilla 26.2 as the referee (env/ read-only).

    python scripts/oracle_robustness.py placement [--tasks t1_xor,...] [--seeds 0-7] [--sessions] [--jobs 6]
        env verdict vs the order-independent placement prototype (tools/oracle/placement.py) for every task variant
        (env/variants.py seeds: rotation x mirror + offset): reference must pass, stored broken variants must fail.
    python scripts/oracle_robustness.py offsets [--tasks ...] [--workers 4]
        every reference's env-mirrored replay (tools/oracle/envplan.py) in vanilla at 3 absolute origins; traces compared.
    python scripts/oracle_robustness.py rotations [--tasks ...] [--workers 4]
        T3/T4 references at rot 0/90/180/270: env verdict, canonical verdict, vanilla verdict of the structure paste (raw world).

Writes data/oracle/robustness_<mode>.json. The `placement` mode is Nucleation only (fast); the others start JVMs.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from env import runner
from env.score import score
from env.task import list_task_ids, load_task
from tools.oracle import envplan
from tools.oracle.placement import patched_env

OUT = ROOT / "data/oracle"
# absolute positions of the build's min corner: env's own cells, far +x/+z with odd offsets, negative coordinates
ORIGINS = {"env": "env", "far": (1007, 70, 519), "neg": (-2003, 30, -1037)}


def apply_edits(solution, edits):
    cells = {tuple(b[:3]): list(b) for b in solution}
    for e in edits:
        pos = tuple(e[1])
        if e[0] == "remove":
            cells.pop(pos, None)
        else:
            cells[pos] = [*pos, e[2]]
    return list(cells.values())


def verdict(contract, blocks, canonical=False):
    def one():
        rr = runner.run(contract, blocks)
        sc = score(contract, rr)
        why = list(rr.invariant_failures()) + [f"{sum(not v.ok for v in rr.vectors)} vectors"] * any(not v.ok for v in rr.vectors) \
            + [s.name for s in rr.sequences if not s.ok] + rr.prepared.errors[:1]
        return bool(sc.full_pass), why
    if canonical:
        with patched_env():
            return one()
    return one()


def variant(tid, seed):
    from env.variants import variant_of
    return variant_of(load_task(tid), seed)


def placement_job(args):
    tid, seed = args
    t = variant(tid, seed)
    out = {"task": tid, "seed": seed}
    for kind, canon in (("env", False), ("canonical", True)):
        ok, why = verdict(t.contract, t.solution, canon)
        broken = [verdict(t.contract, apply_edits(t.solution, b["edits"]), canon)[0] for b in t.broken]
        out[kind] = {"ref": ok, "why": why[:4], "broken_pass": sum(broken), "broken": len(broken)}
    return out


def session_job(d):
    st = json.loads((Path(d) / "state.json").read_text())
    blocks = json.loads((Path(d) / "final_blocks.json").read_text())
    c = load_task(st.get("task_ref") or st["task_id"]).contract
    return {"session": Path(d).name, "env": verdict(c, blocks), "canonical": verdict(c, blocks, True)}


def cmd_placement(args):
    ids = args.tasks.split(",") if args.tasks else [t for t in list_task_ids() if not t.startswith("t4_")]
    lo, hi = map(int, args.seeds.split("-"))
    jobs = [(t, s) for t in ids for s in range(lo, hi + 1)]
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(placement_job, jobs))
        sess = list(ex.map(session_job, [str(p.parent) for p in sorted((ROOT / "data/sessions").glob("*/final_blocks.json"))
                                         if not p.parent.name.startswith("t4_")])) if args.sessions else []
    for r in rows:
        e, c = r["env"], r["canonical"]
        flag = "" if e["ref"] and c["ref"] and not e["broken_pass"] and not c["broken_pass"] else "  <<"
        print(f"{r['task']:20s} v{r['seed']}  env ref {'pass' if e['ref'] else 'FAIL'} broken passing {e['broken_pass']}/{e['broken']}"
              f" | canonical ref {'pass' if c['ref'] else 'FAIL'} broken passing {c['broken_pass']}/{c['broken']}{flag}"
              + (f"  env: {e['why'][:2]}" if not e["ref"] else "") + (f"  canonical: {c['why'][:2]}" if not c["ref"] else ""))
    n = len(rows)
    print(f"\nreferences passing: env {sum(r['env']['ref'] for r in rows)}/{n}, canonical {sum(r['canonical']['ref'] for r in rows)}/{n}; "
          f"broken variants passing: env {sum(r['env']['broken_pass'] for r in rows)}, canonical {sum(r['canonical']['broken_pass'] for r in rows)}"
          f" of {sum(r['env']['broken'] for r in rows)}")
    for s in sess:
        print(f"session {s['session'][:48]:48s} env {s['env'][0]} canonical {s['canonical'][0]} {s['canonical'][1][:3]}")
    return {"rows": rows, "sessions": sess}


def replay_rows(items, workers, origins):
    """items: [(name, contract, run_result, world)] -> per item and origin: vanilla verdict + nuc==vanilla + trace equality
    across origins (traces are in the caller's frame, so equal traces = position-independent behaviour)."""
    from tools.oracle import gametest
    from tools.oracle.diff import diff_traces
    from tools.oracle.sim import run_plan
    from tools.oracle.trace import first_divergence
    work = []
    for name, c, rr, world in items:
        for oname, o in origins.items():
            for label, plan, checks in envplan.env_captures(c, rr, 20, world=world, origin=o):
                work.append((name, c, oname, label, world, plan, checks))
    t0 = time.perf_counter()
    traces = gametest.capture_many([(f"{w[0]}__{w[2]}__{w[3].replace(':', '_')}", w[4], w[5]) for w in work], workers=workers)
    wall = time.perf_counter() - t0
    res = {}
    for w, ot in zip(work, traces):
        name, c, oname, label, world, plan, checks = w
        st = run_plan(world, plan)
        r = res.setdefault(name, {}).setdefault(oname, {"vanilla_fail": [], "nuc_fail": [], "agree": True, "traces": {}})
        r["vanilla_fail"] += [f"{label}:{f}" for f in envplan.evaluate(c, ot, checks)]
        r["nuc_fail"] += [f"{label}:{f}" for f in envplan.evaluate(c, st, checks)]
        r["agree"] &= diff_traces(name, "rob", ot, st, plan).agree_full
        r["traces"][label] = ot
    rows = []
    for name, per in res.items():
        base = per[next(iter(origins))]["traces"]
        row = {"name": name}
        for oname, r in per.items():
            div = [(lab, first_divergence(base[lab], tr)) for lab, tr in r["traces"].items()]
            div = [(lab, d) for lab, d in div if d is not None]
            row[oname] = {"vanilla": not r["vanilla_fail"], "vanilla_failures": r["vanilla_fail"][:4],
                          "nucleation": not r["nuc_fail"], "nuc_eq_vanilla": r["agree"],
                          "same_trace_as_first_origin": not div,
                          "first_divergence": None if not div else {"capture": div[0][0], "tick": div[0][1][0],
                                                                      "pos": list(div[0][1][1]), "a": div[0][1][2], "b": div[0][1][3]}}
        rows.append(row)
    return rows, wall


def print_replay(rows, origins):
    yn = lambda v: "pass" if v else "FAIL"
    for row in rows:
        cells = []
        for o in origins:
            r = row.get(o)
            if r:
                cells.append(f"{o}: van {yn(r['vanilla'])} nuc {yn(r['nucleation'])}{'' if r['nuc_eq_vanilla'] else ' (nuc!=van)'}"
                             f"{'' if r['same_trace_as_first_origin'] else ' TRACE DIFFERS'}")
        print(f"{row['name'][:28]:28s} " + " | ".join(cells))
        for o in origins:
            r = row.get(o)
            if r and (not r["same_trace_as_first_origin"] or not r["vanilla"]):
                print(f"    {o}: {r['vanilla_failures'][:2]} first divergence {r['first_divergence']}")


def env_item(tid, canonical=False):
    t = load_task(tid)
    if canonical:
        with patched_env():
            rr = runner.run(t.contract, t.solution)
    else:
        rr = runner.run(t.contract, t.solution)
    return tid, t.contract, rr, envplan.handover_world(t.contract, rr.prepared, canonical)


def cmd_offsets(args):
    ids = args.tasks.split(",") if args.tasks else list_task_ids()
    with ProcessPoolExecutor(args.jobs) as ex:
        items = list(ex.map(env_item, ids))
    rows, wall = replay_rows(items, args.workers, ORIGINS)
    print_replay(rows, ORIGINS)
    bad = [r["name"] for r in rows if not all(r[o]["same_trace_as_first_origin"] and r[o]["vanilla"] for o in ORIGINS)]
    print(f"\n{len(rows)} references x {len(ORIGINS)} origins {list(ORIGINS.values())}, oracle wall {wall:.0f}s; "
          f"position-dependent or failing: {bad or 'none'}")
    return {"origins": {k: v for k, v in ORIGINS.items()}, "rows": rows, "wall_s": wall}


def rot_item(args):
    tid, rot = args
    from env.variants import variant_of
    t = variant_of(load_task(tid), rot=rot)
    env_ok = verdict(t.contract, t.solution)
    with patched_env():
        rr = runner.run(t.contract, t.solution)
    can_ok = bool(score(t.contract, rr).full_pass)
    return f"{tid}@r{rot}", t.contract, rr, envplan.raw_world(rr.prepared), env_ok, can_ok


def cmd_rotations(args):
    ids = args.tasks.split(",") if args.tasks else [t for t in list_task_ids() if t.startswith(("t3_", "t4_"))]
    with ProcessPoolExecutor(args.jobs) as ex:
        got = list(ex.map(rot_item, [(t, r) for t in ids for r in (0, 90, 180, 270)]))
    # raw world = what a structure / Litematica paste of the program output places; timing from the canonical env run
    rows, wall = replay_rows([g[:4] for g in got], args.workers, {"env": "env"})
    meta = {g[0]: (g[4], g[5]) for g in got}
    yn = lambda v: "pass" if v else "FAIL"
    for row in rows:
        (e_ok, e_why), c_ok = meta[row["name"]]
        r = row["env"]
        row.update(env=e_ok, env_why=e_why[:3], canonical=c_ok, vanilla_raw=r)
        print(f"{row['name']:24s} env {yn(e_ok)} canonical {yn(c_ok)} vanilla(raw paste) {yn(r['vanilla'])}"
              f" nuc==van {r['nuc_eq_vanilla']}" + (f"   env: {e_why[:2]}" if not e_ok else "")
              + (f"   vanilla: {r['vanilla_failures'][:2]}" if not r["vanilla"] else ""))
    print(f"\noracle wall {wall:.0f}s")
    return {"rows": rows, "wall_s": wall}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["placement", "offsets", "rotations"])
    ap.add_argument("--tasks")
    ap.add_argument("--seeds", default="0-7")
    ap.add_argument("--sessions", action="store_true", help="placement: also the recorded non-T4 sessions")
    ap.add_argument("--jobs", type=int, default=6, help="env processes")
    ap.add_argument("--workers", type=int, default=4, help="JVMs (batched; one JVM per worker)")
    ap.add_argument("--json")
    args = ap.parse_args()
    out = {"placement": cmd_placement, "offsets": cmd_offsets, "rotations": cmd_rotations}[args.mode](args)
    path = Path(args.json) if args.json else OUT / f"robustness_{args.mode}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str))
    print("wrote", path)


if __name__ == "__main__":
    main()
