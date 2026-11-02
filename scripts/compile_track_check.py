#!/usr/bin/env python3
"""Is a "compiler tool tier" a valid benchmark track?  Compile adders with Nucleation's netlist compiler (Hdl.compile_blif) and
check them the way the benchmark checks agent builds.

    python scripts/compile_track_check.py                        # ha, add2, add4 (env sim + vanilla oracle), writes the JSON report
    python scripts/compile_track_check.py --designs ha,add2,add4,add8 --no-oracle
    python scripts/compile_track_check.py --designs add4 --max-vectors 24

For every design (BLIF written here: yosys is not needed, the netlists are plain `.names` covers):
  1. compile  -> Schematic + report/contract JSON (time, blocks, bounding volume, estimated delays)
  2. Nucleation's own typed Executor (Executor.for_schematic) -> records whether it runs at all
  3. env check: levers become fixed `click` ports, the report's probes `wire_power` outputs, an exhaustive (or sampled) truth table is run
     through env.runner.run with all invariants (at_rest, paste_stable, deterministic, ...)
  4. vanilla 26.2 oracle on the same stimulus (tick-by-tick Nucleation-vs-vanilla diff and the contract verdict in vanilla)
  5. footprint against the hand-built reference (lib half_adder, env/tasks/t3/t3_adderN.json) and a mutation sweep (does the verdict notice
     a broken compiled circuit?)
The JSON report goes to env/tasks/t3/compile_track_report.json.
"""
import argparse
import itertools
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "scripts"))

import nucleation as nuc  # noqa: E402

from env import blocks as B  # noqa: E402
from env import library as L  # noqa: E402
from env.contract import Behaviour, Budgets, Contract, DEFAULT_FORBIDDEN, Invariants, Port, TruthTable, Vector  # noqa: E402
from env.runner import prepare, run  # noqa: E402
from env.sim import parse  # noqa: E402
from env.task import load_task  # noqa: E402

REPORT = ROOT / "env" / "tasks" / "t3" / "compile_track_report.json"


# --------------------------------------------------------------------------- netlists
def blif_half_adder():
    return ".model ha\n.inputs a b\n.outputs s c\n.names a b s\n10 1\n01 1\n.names a b c\n11 1\n.end\n"


def blif_adder(n):
    """n-bit ripple adder, outputs s0..s{n} (s{n} = carry out): per bit an XOR3 cover and a majority cover."""
    ins = [f"a{i}" for i in range(n)] + [f"b{i}" for i in range(n)]
    outs = [f"s{i}" for i in range(n + 1)]
    lines = [f".model add{n}", ".inputs " + " ".join(ins), ".outputs " + " ".join(outs),
             ".names a0 b0 s0", "10 1", "01 1", ".names a0 b0 c1", "11 1"]
    for i in range(1, n):
        lines += [f".names a{i} b{i} c{i} s{i}", "100 1", "010 1", "001 1", "111 1",
                  f".names a{i} b{i} c{i} c{i + 1}", "11- 1", "1-1 1", "-11 1"]
    lines += [f".names c{n} s{n}", "1 1", ".end"]
    return "\n".join(lines) + "\n"


DESIGNS = {
    "ha": dict(blif=blif_half_adder, model=lambda v: {"s": v["a"] ^ v["b"], "c": v["a"] & v["b"]}, ref=("lib:half_adder", None)),
}
for _n in (2, 3, 4, 8):
    DESIGNS[f"add{_n}"] = dict(
        blif=(lambda n=_n: blif_adder(n)), ref=("task", f"t3_adder{_n}"),
        model=(lambda v, n=_n: {f"s{k}": ((sum(v[f"a{i}"] << i for i in range(n)) + sum(v[f"b{i}"] << i for i in range(n))) >> k) & 1
                                for k in range(n + 1)}))


# --------------------------------------------------------------------------- compile + load
def compile_design(name, bake=True):
    spec = DESIGNS[name]
    blif = spec["blif"]()
    t0 = time.perf_counter()
    schem = nuc.Hdl.compile_blif(blif, name, bake)
    t_compile = time.perf_counter() - t0
    report = json.loads(nuc.Hdl.compile_blif_report(blif, name))
    contract_json = nuc.Hdl.compile_blif_contract(blif, name)
    schem.set_cell_contract_json(contract_json)
    blocks = []
    for b in json.loads(schem.get_non_air_blocks_json()):
        props = ",".join(f"{k}={v}" for k, v in b.get("properties", []))
        blocks.append((b["x"], b["y"], b["z"], b["name"] + (f"[{props}]" if props else "")))
    return dict(blif=blif, schem=schem, report=report, contract=json.loads(contract_json), blocks=blocks, compile_s=round(t_compile, 3))


def executor_check(c, vectors, model):
    """Nucleation's typed Executor on the compiled cell. Returns {"ok": bool, "stage": ..., "error": str|None, "pass": n, "total": n}."""
    stage = "for_schematic"
    try:
        ex = nuc.design.Executor.for_schematic(c["schem"])
        good = 0
        for v in vectors:
            stage = "set_input"
            for k, val in v.items():
                ex[k] = val
            stage = "settle"
            ex.settle()
            stage = "read_output"
            exp = model(v)
            good += all(int(ex[k]) == e for k, e in exp.items())
    except Exception as e:  # the audit saw NucleationErrorCode.Simulation here
        return {"ok": False, "stage": stage, "error": f"{type(e).__name__}: {e}", "pass": 0, "total": len(vectors)}
    return {"ok": True, "stage": None, "error": None, "pass": good, "total": len(vectors)}


def tick_sim_check(c, vectors, model, ticks=400):
    """Directly in TickSimulation (Placement settle): flip the lever bank, run `ticks`, read the probe dust power."""
    from nucleation import TickSettleMode, TickSimulation
    rep = c["report"]
    lever = {l["signal"].split(".")[0]: tuple(l["pos"]) for l in rep["levers"]}
    probe = {o["name"]: tuple(rep["probes"][o["probe"]]) for o in rep["outputs"]}
    sim = TickSimulation.from_schematic(c["schem"], TickSettleMode.Placement, 0, 0, 0, "")
    state = {k: 0 for k in lever}
    good = 0
    for v in vectors:
        for k, val in v.items():
            if state[k] != val:
                sim.use_block(*lever[k])
                state[k] = val
        for _ in range(ticks):
            sim.step()
        exp = model(v)
        got = {k: int(int(parse(sim.get_block(*probe[k]))[1].get("power", "0")) > 0) for k in exp}
        good += got == exp
    return {"pass": good, "total": len(vectors)}


def make_contract(name, c, vectors, model, settle):
    rep = c["report"]
    lever = {l["signal"].split(".")[0]: tuple(l["pos"]) for l in rep["levers"]}
    probe = {o["name"]: tuple(rep["probes"][o["probe"]]) for o in rep["outputs"]}
    lo = [min(b[i] for b in c["blocks"]) for i in range(3)]
    hi = [max(b[i] for b in c["blocks"]) for i in range(3)]
    bbox = [lo[0] - 1, lo[1] - 1, lo[2] - 1, hi[0] + 1, hi[1] + 1, hi[2] + 1]
    fixed, ports, sol = [], [], []
    lever_state = {tuple(b[:3]): b[3] for b in c["blocks"] if b[3].startswith("minecraft:lever")}
    for k, pos in lever.items():
        st = B.canonical(lever_state[pos])
        fixed.append({"box": [*pos, *pos], "state": st})
        ports.append(Port(k, "click", pos, doc="lever"))
    for k, pos in probe.items():
        ports.append(Port(k, "wire_power", pos, min_power=1))
    for b in c["blocks"]:
        if tuple(b[:3]) not in lever_state:
            sol.append(list(b))
    rows = [Vector(dict(v), model(v), True, f"v{i}") for i, v in enumerate(vectors)]
    return Contract(
        id=f"compiled_{name}", title=f"compiled {name}", tier="COMPILED", description="Nucleation Hdl.compile_blif output", bbox=bbox,
        build_box=list(bbox), ports=ports, fixed=fixed,
        behaviour=Behaviour(truth_table=TruthTable(rows, settle=settle, shuffle=True, order_independent=True)),
        invariants=Invariants(), budgets=Budgets(rest=3000, total=5_000_000, max_blocks=60000), eval_seed=3, forbidden=DEFAULT_FORBIDDEN), sol


def choose_vectors(name, c, max_vectors, seed=0):
    ins = [l["signal"].split(".")[0] for l in c["report"]["levers"]]
    allv = [dict(zip(ins, bits)) for bits in itertools.product((0, 1), repeat=len(ins))]
    if len(allv) <= max_vectors:
        return allv
    rng = random.Random(seed)
    pick = {0, len(allv) - 1}
    pick |= set(rng.sample(range(len(allv)), max_vectors - len(pick)))
    return [allv[i] for i in sorted(pick)]


# --------------------------------------------------------------------------- footprint of the hand-built reference
def reference_footprint(name):
    kind, ref = DESIGNS[name]["ref"]
    if kind == "task":
        t = load_task(ROOT / "env" / "tasks" / "t3" / f"{ref}.json")
        return {"ref": ref, "blocks": t.contract.reference["blocks"], "volume": t.contract.reference["volume"]}
    m = L.meta("half_adder")
    fp = m["footprint"]
    return {"ref": "lib/half_adder", "blocks": fp["blocks"], "volume": fp["size"][0] * fp["size"][1] * fp["size"][2]}


def mutation_sweep(contract, sol, n=10, seed=0):
    """Remove n random redstone components of the compiled circuit: how many does the env verdict catch?"""
    from env import mutate
    from env.score import score
    comps = [b for b in sol if B.short(B.split_state(B.canonical(b[3]))[0]) in ("redstone_wire", "repeater", "redstone_torch", "redstone_wall_torch")]
    rng = random.Random(seed)
    caught = 0
    for b in rng.sample(comps, min(n, len(comps))):
        r = run(contract, mutate.apply(sol, [("remove", tuple(b[:3]))]), seed=0, fast=True)
        caught += not score(contract, r).full_pass
    return {"tried": min(n, len(comps)), "caught": caught}


# --------------------------------------------------------------------------- one design
def check(name, a):
    out = {"design": name}
    c = compile_design(name, bake=True)
    rep = c["report"]
    xs, ys, zs = zip(*[b[:3] for b in c["blocks"]])
    out["compile"] = {"seconds": c["compile_s"], "blocks": len(c["blocks"]), "bounds": rep["bounds"],
                      "volume": (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1) * (max(zs) - min(zs) + 1),
                      "prims": rep.get("prims"), "levels": rep.get("levels"), "est_delay_rt_max": max([d["delay_rt"] for d in c["contract"]["physical"]["delays_rt"]] or [0]),
                      "block_kinds": {}}
    for b in c["blocks"]:
        k = B.short(b[3].split("[")[0])
        out["compile"]["block_kinds"][k] = out["compile"]["block_kinds"].get(k, 0) + 1
    out["reference"] = reference_footprint(name)
    ref = out["reference"]
    out["footprint_ratio"] = {"blocks": round(len(c["blocks"]) / ref["blocks"], 1), "volume": round(out["compile"]["volume"] / ref["volume"], 1)}
    model = DESIGNS[name]["model"]
    vectors = choose_vectors(name, c, a.max_vectors)
    out["vectors"] = len(vectors)
    out["executor"] = executor_check(c, vectors[:8], model)
    t0 = time.perf_counter()
    out["tick_sim"] = tick_sim_check(c, choose_vectors(name, c, a.tick_vectors, seed=1), model)
    out["tick_sim"]["seconds"] = round(time.perf_counter() - t0, 1)
    if len(c["blocks"]) > a.skip_env_above:
        out["env"] = out["oracle"] = {"skipped": f"{len(c['blocks'])} blocks > --skip-env-above {a.skip_env_above}"}
        return out
    settle = max(600, 2 * 10 * (rep.get("levels") or 1) * 8)
    contract, sol = make_contract(name, c, vectors, model, settle)
    errs = contract.validate()
    out["contract_errors"] = errs
    pr = prepare(contract, sol)
    out["prepare_errors"] = pr.errors[:4]
    if not pr.errors and not errs:
        t0 = time.perf_counter()
        res = run(contract, sol, seed=0, fast=not a.full_invariants)
        out["env"] = {"seconds": round(time.perf_counter() - t0, 1), "compiled": res.compiled,
                      "vectors_pass": sum(v.ok for v in res.vectors), "vectors": len(res.vectors),
                      "invariants": {k: v["ok"] for k, v in res.invariants.items()},
                      "invariant_failures": {k: v["detail"][:200] for k, v in res.invariant_failures().items()},
                      "place_errors": [(list(p), s, m[:80]) for p, s, m in res.place_errors][:3],
                      "ticks_per_vector_max": max([v.ticks for v in res.vectors] or [0]),
                      "unsettled": [v.index for v in res.vectors if not v.settled][:5]}
        if a.mutants:
            out["mutants"] = mutation_sweep(contract, sol, a.mutants)
        if a.oracle:
            t0 = time.perf_counter()
            try:
                hold = out["env"]["ticks_per_vector_max"] + 30
                o = L.oracle_check([(name, contract, [list(b) for b in sol])], hold=hold, preroll=20, workers=1)[name]
                o["seconds"] = round(time.perf_counter() - t0, 1)
                o["hold"] = hold
                out["oracle"] = o
            except Exception as e:
                out["oracle"] = {"error": f"{type(e).__name__}: {str(e)[:300]}", "seconds": round(time.perf_counter() - t0, 1)}
    # unbaked compile: does it matter?
    return out


def summarize(r):
    cm, env, orc = r["compile"], r.get("env"), r.get("oracle")
    if env and "skipped" in env:
        env = orc = None
    parts = [f"{r['design']:5} compile {cm['seconds']}s {cm['blocks']} blocks vol {cm['volume']} ({r['footprint_ratio']['blocks']}x blocks / {r['footprint_ratio']['volume']}x volume of {r['reference']['ref']})"]
    parts.append(f"executor={'ok' if r['executor']['ok'] else 'ERR@' + str(r['executor']['stage']) + ' ' + str(r['executor']['error'])[-40:]}")
    parts.append(f"tick_sim={r['tick_sim']['pass']}/{r['tick_sim']['total']}")
    if env:
        parts.append(f"env={env['vectors_pass']}/{env['vectors']} inv_fail={list(env['invariant_failures'])} {env['seconds']}s")
    if orc and "error" not in orc:
        parts.append(f"oracle vanilla={'pass' if orc['vanilla_pass'] else 'FAIL'} nucleation={'pass' if orc['nucleation_pass'] else 'FAIL'} identical={orc['agree']}/{orc['captures']}")
    elif orc:
        parts.append(f"oracle ERR {orc['error'][:60]}")
    if "mutants" in r:
        parts.append(f"mutants caught {r['mutants']['caught']}/{r['mutants']['tried']}")
    return " | ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--designs", default="ha,add2,add4")
    ap.add_argument("--max-vectors", type=int, default=32, help="truth-table rows per design (all combinations when fewer)")
    ap.add_argument("--tick-vectors", type=int, default=256, help="truth-table rows for the direct TickSimulation check (cheap)")
    ap.add_argument("--skip-env-above", type=int, default=20000, help="skip the env runner / oracle for compiled circuits with more blocks")
    ap.add_argument("--merge", action="store_true", help="update the existing JSON report by design name instead of overwriting it")
    ap.add_argument("--no-oracle", dest="oracle", action="store_false")
    ap.add_argument("--full-invariants", action="store_true", help="include the deterministic re-run (doubles env time)")
    ap.add_argument("--mutants", type=int, default=6)
    ap.add_argument("--out", default=str(REPORT))
    a = ap.parse_args(argv)
    results = []
    for name in a.designs.split(","):
        r = check(name, a)
        results.append(r)
        print(summarize(r), flush=True)
    if a.merge and Path(a.out).exists():
        old = {r["design"]: r for r in json.loads(Path(a.out).read_text())}
        old.update({r["design"]: r for r in results})
        results = [old[k] for k in sorted(old, key=lambda k: (len(k), k))]
    Path(a.out).write_text(json.dumps(results, indent=1, default=str) + "\n")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
