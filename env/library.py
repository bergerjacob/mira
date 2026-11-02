"""Component library loader, bench generator and verifier for lib/.

    python env/library.py list                       # catalogue table
    python env/library.py verify [names] [--oracle]  # env (+ vanilla oracle) verification of every orientation
    python env/library.py write [names] [--oracle]   # verify and rewrite lib/<name>.meta.json

A component is `lib/<name>.py` (sandbox-legal builder source defining the `@part` function `<name>`) plus
`lib/<name>.meta.json` (ports, spec, timing, verified orientations; generated from `lib/specs.py` + a verification run).
Component conventions (see docs/LIBRARY.md):

* local y=0 is the floor/support plane, logic starts at y=1; every part takes `floor=True` (False = rely on a ground the
  caller provides, e.g. a task's fixed floor); the part never writes outside its bounding box;
* every port is a redstone dust cell on the component edge; `face` is the side the outside world connects from (inputs)
  or leaves to (outputs), in the part's own frame (it is transformed with rot/mirror);
* outputs are driven at full strength (15); inputs accept any strength >= 1.

Programs are composed by concatenating sources (`prelude`) in front of a `def build(b)` body, so a task reference is plain
builder code that stays inside the sandbox (`simulation.builder.compile`).
"""
import argparse
import itertools
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]  # env/tools.py would shadow tools/
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "scripts"))

from env import blocks as B  # noqa: E402
from env import models as M  # noqa: E402
from env.contract import (Behaviour, Budgets, Contract, DEFAULT_FORBIDDEN, Event, Expect, Invariants, Port,  # noqa: E402
                          Sequence, TruthTable, Vector)
from env.runner import prepare, run  # noqa: E402
from env.sim import Sim, compile_cells, parse  # noqa: E402
from simulation import builder as BD  # noqa: E402

LIB = ROOT / "lib"
DIRS = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0)}
ORIENTATIONS = [(r, m) for m in (None, "x") for r in (0, 90, 180, 270)]  # the 8 elements of the dihedral group
IMPORT_LINE = re.compile(r"^\s*from\s+simulation\.builder\s+import\s+\*\s*$", re.M)


def orient_name(rot, mirror):
    return f"rot{rot}" + (f"+mirror_{mirror}" if mirror else "")


# --------------------------------------------------------------------------- catalogue / sources
def specs():
    """lib/specs.py: the hand-authored spec of every component (loaded lazily, by path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("lib_specs", LIB / "specs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SPECS


def names():
    return sorted(p.stem for p in LIB.glob("*.py") if not p.stem.startswith("_") and p.stem != "specs")


def source(name):
    return (LIB / f"{name}.py").read_text()


def meta(name):
    return json.loads((LIB / f"{name}.meta.json").read_text())


def all_meta():
    return {n: meta(n) for n in names() if (LIB / f"{n}.meta.json").exists()}


def requires(name):
    p = LIB / f"{name}.meta.json"
    if p.exists():
        return json.loads(p.read_text()).get("requires", [])
    return specs()[name].get("requires", [])


def closure(wanted):
    """Component names needed for `wanted` (str or list), dependencies first."""
    out = []

    def visit(n):
        if n in out:
            return
        for r in requires(n):
            visit(r)
        out.append(n)
    for n in ([wanted] if isinstance(wanted, str) else wanted):
        visit(n)
    return out


def prelude(wanted):
    """Concatenated library sources (dependencies first) behind a single `from simulation.builder import *`."""
    parts = ["from simulation.builder import *\n"]
    for n in closure(wanted):
        parts.append(f"# --- lib/{n}.py\n" + IMPORT_LINE.sub("", source(n)).strip("\n") + "\n")
    return "\n".join(parts)


def program(wanted, body):
    """Library prelude + task code (which defines `build(b)`), ready for simulation.builder.compile."""
    return prelude(wanted) + "\n# --- task\n" + body.strip("\n") + "\n"


def compile_program(wanted, body, sandbox=False, **kw):
    """Compile `program(wanted, body)`; corpus cells promoted into lib/corpus are available as `b.use("corpus/<file>.litematic")`."""
    kw.setdefault("lib_root", str(LIB))
    return BD.compile(program(wanted, body), sandbox=sandbox, **kw)


def corpus_index():
    """Promoted corpus circuits (lib/corpus/index.json): name, file, ports, truth-table size, provenance, verification."""
    p = LIB / "corpus" / "index.json"
    return json.loads(p.read_text()) if p.exists() else []


# --------------------------------------------------------------------------- reference models for T3 (registered by name)
# env/models.py is read-only for the library; the T3 models are registered from here. Importing `env.library` registers them
# (the stock validate_tasks CLI needs `import env.library` in env/models.py, see docs/LIBRARY.md).
def _bits(i, prefix, n):
    return sum(i[f"{prefix}{k}"] << k for k in range(n))


@M.model("adder_cin")
def adder_cin(s, i, bits=2):
    t = _bits(i, "A", bits) + _bits(i, "B", bits) + i.get("CIN", 0)
    out = {f"S{k}": (t >> k) & 1 for k in range(bits)}
    out["COUT"] = (t >> bits) & 1
    return out


@M.model("decoder")
def decoder(s, i, bits=2):
    sel = _bits(i, "A", bits)
    return {f"Y{k}": int(k == sel) for k in range(1 << bits)}


@M.model("decoder_en")
def decoder_en(s, i, bits=2):
    sel = _bits(i, "A", bits)
    return {f"Y{k}": int(k == sel and i["EN"]) for k in range(1 << bits)}


@M.model("majority")
def majority(s, i, n=3):
    return {"Y": int(sum(i[f"I{k}"] for k in range(n)) * 2 > n)}


@M.model("parity")
def parity(s, i, n=4):
    return {"Y": sum(i[f"I{k}"] for k in range(n)) & 1}


@M.model("rom_en")
def rom_en(s, i, words=(), width=4, abits=2):
    addr = _bits(i, "A", abits)
    w = words[addr] if i.get("EN", 1) else 0
    return {f"D{k}": (w >> k) & 1 for k in range(width)}


@M.model("rom")
def rom(s, i, words=(), width=4, abits=2):
    addr = _bits(i, "A", abits)
    w = words[addr]
    return {f"D{k}": (w >> k) & 1 for k in range(width)}


@M.model("counter_tff")
def counter_tff(s, i, bits=2, down=False):
    """Ripple counter of T flip-flops clocked by CLK: the count steps on every rising edge of CLK (up by default, down when
    each stage toggles on the rising edge of the previous Q). Outputs Q0..Q{bits-1}; the stream order matters (mode=seq)."""
    clk = i.get("CLK", 0)
    if clk and not s.get("prev", 0):
        s["n"] = (s.get("n", 0) + (-1 if down else 1)) % (1 << bits)
    s["prev"] = clk
    return {f"Q{k}": (s.get("n", 0) >> k) & 1 for k in range(bits)}


# --------------------------------------------------------------------------- reference models for T4
@M.model("addsub")
def addsub(s, i, bits=8):
    """A + B (M=0) or A - B (M=1) as A + ~B + 1: S0..S{bits-1}, COUT (= no borrow when subtracting)."""
    a, b, m = _bits(i, "A", bits), _bits(i, "B", bits), i["M"]
    t = a + ((~b) & ((1 << bits) - 1) if m else b) + m
    out = {f"S{k}": (t >> k) & 1 for k in range(bits)}
    out["COUT"] = (t >> bits) & 1
    return out


# --------------------------------------------------------------------------- bench
def rotated_ports(build, inst="dut"):
    """-> {port: {"pos": (x,y,z), "kind": in|out, "face": dir}} for the placed component."""
    out = {}
    pre = inst + "."
    for k, v in build.ports.items():
        if k.startswith(pre):
            out[k[len(pre):]] = {"pos": tuple(v["pos"]), "kind": v["kind"], "face": v["face"]}
    return out


def _spec_behaviour(spec, ports, hold_settle, params=None):
    """lib spec -> Behaviour (truth table with hidden repeat, or sequences)."""
    bh = Behaviour()
    ins, outs = spec.get("inputs", []), spec.get("outputs", [])
    rows = spec.get("rows")
    if rows is None and spec.get("fn") is not None:
        rows = []
        for combo in itertools.product((0, 1), repeat=len(ins)):
            r = spec["fn"](**dict(zip(ins, combo)))
            r = r if isinstance(r, tuple) else (r,)
            rows.append([list(combo), list(r)])
    if rows:
        vecs = [Vector(dict(zip(ins, c)), dict(zip(outs, o)), j % 2 == 0 or True, "".join(map(str, c))) for j, (c, o) in enumerate(rows)]
        vecs += [Vector(v.inputs, v.expect, False, v.label + "'") for v in reversed(vecs)]
        bh.truth_table = TruthTable(vecs, settle=hold_settle, shuffle=True, reset_between=False,
                                    order_independent=spec.get("order_independent", True))
    seqs = spec.get("sequences") or []
    if callable(seqs):
        seqs = seqs(dict(params or {}))
    for sq in seqs:
        from env.contract import Sequence as Seq
        bh.sequences.append(Seq(sq["name"], [Event(t, dict(s)) for t, s in sq["events"]],
                                [Expect(*e) for e in sq["expect"]], sq["duration"], True))
    return bh


def bench(name, rot=0, mirror=None, lead=0, params=None, spec=None, margin=1):
    """Build the verification bench for one component in one orientation.

    Inputs are `pad` ports: a redstone block is placed `lead + 1` cells outside the input port, on the port's face, joined to
    the port by `lead` dust cells (lead=14 delivers strength 1 at the port); outputs are `wire_power >= 1` readers on the
    output port cells (strength is characterised separately, see `characterize`). Returns (contract, blocks, info)."""
    sp = spec or specs()[name]
    params = dict(params or {})
    plist = "".join(f", {k}={v!r}" for k, v in params.items())
    body = f"def build(b):\n    b.place({name}, at=(0, 0, 0), rot={rot}, mirror={mirror!r}, name='dut'{plist})\n"
    build = compile_program(name, body)
    if not build.ok:
        raise ValueError(f"{name} does not compile: {[str(e) for e in build.errors][:3]}")
    ports = rotated_ports(build)
    cells = {(b[0], b[1], b[2]): b[3] for b in build.blocks}
    extra, fixed, cports = {}, [], []
    for pn, pt in sorted(ports.items()):
        if pt["kind"] == "in":
            d = DIRS[pt["face"]]
            x, y, z = pt["pos"]
            lead_n = lead.get(pn, 0) if isinstance(lead, dict) else lead
            for k in range(1, lead_n + 1):
                c = (x + d[0] * k, y, z + d[2] * k)
                extra[c] = "minecraft:redstone_wire"
                extra[(c[0], y - 1, c[2])] = "minecraft:stone"
            pad = (x + d[0] * (lead_n + 1), y, z + d[2] * (lead_n + 1))
            fixed.append({"box": [*pad, *pad], "state": "minecraft:air"})
            cports.append(Port(pn, "pad", pad, facing=pt["face"], doc="pad"))
        else:
            cports.append(Port(pn, "wire_power", pt["pos"], facing=pt["face"],
                               min_power=sp.get("min_out", {}).get(pn, 1)))
    overlap = set(extra) & set(cells)
    if overlap:
        raise ValueError(f"{name} {orient_name(rot, mirror)}: input leads overlap the component at {sorted(overlap)[:3]}")
    allc = {**cells, **extra}
    pads = [tuple(f["box"][:3]) for f in fixed]
    xs, ys, zs = zip(*(list(allc) + pads))
    bbox = [min(xs) - margin, min(ys) - margin, min(zs) - margin, max(xs) + margin, max(ys) + margin, max(zs) + margin]
    bh = _spec_behaviour(sp, ports, sp.get("settle", 150), params)
    c = Contract(id=f"lib_{name}", title=f"lib/{name} {orient_name(rot, mirror)}", tier="LIB", description="library bench",
                 bbox=bbox, build_box=list(bbox), ports=cports, fixed=fixed, behaviour=bh,
                 invariants=Invariants(**sp.get("invariants", {})), budgets=Budgets(rest=sp.get("rest", 200), total=200000, max_blocks=5000),
                 eval_seed=7, forbidden=DEFAULT_FORBIDDEN)
    errs = c.validate()
    if errs:
        raise ValueError(f"{name} bench invalid: {errs}")
    blocks = [[*p, s] for p, s in sorted(allc.items())]
    return c, blocks, {"ports": ports, "bounds": build.bounds(), "blocks": len(build.blocks), "build": build}


# --------------------------------------------------------------------------- verification
def env_pass(contract, blocks, seed=0):
    """Run the bench in the env runner -> (functional_ok, invariants_ok, detail). Functional = compiled and every vector and
    sequence passes; invariants = at_rest, paste_stable, deterministic, ... all hold."""
    r = run(contract, blocks, seed=seed)
    func = bool(r.compiled and all(v.ok for v in r.vectors) and all(s.ok for s in r.sequences))
    inv = all(i["ok"] for i in r.invariants.values())
    info = {"max_ticks": max([v.ticks for v in r.vectors] + [0])}
    if not (func and inv):
        info["errors"] = r.prepared.errors[:3]
        info["invariants"] = {k: v["detail"] for k, v in r.invariant_failures().items()}
        info["vectors"] = [(v.inputs, v.expect, v.got) for v in r.vectors if not v.ok][:3]
        info["sequences"] = [(q.name, [(w.port, w.value, w.t1, w.t2, w.first_bad) for w in q.windows if not w.ok]) for q in r.sequences if not q.ok]
    return func, inv, info


def characterize(contract, blocks):
    """Combinational characterisation on the bare Nucleation sim: for every single-input flip from every context of the
    other inputs, the settle time (last change of an output cell, in ticks, max over contexts, rising/falling input) and,
    over all exhaustive vectors, the minimum dust power on each output port cell while the output is on (`out_strength`)
    and the maximum while it is off (must be 0)."""
    pr = prepare(contract, blocks)
    sc, origin, _ = compile_cells(pr.world, pr.nbt)
    sim = Sim(sc, origin, "inworld")
    sim.settle(contract.budgets.rest)
    sim.checkpoint()
    ins = list(contract.inputs)
    outs = {p.name: tuple(p.pos) for p in contract.outputs}
    out_cells = set(outs.values())
    res = {"rise": 0, "fall": 0}
    on_min, off_max = {}, {}
    for flip in ins:
        others = [p for p in ins if p is not flip]
        for ctx in itertools.product((0, 1), repeat=len(others)):
            for direction in ("rise", "fall"):
                sim.restore()
                sim.clear_changes()
                for p, v in zip(others, ctx):
                    if v:
                        sim.place(p.pos, "minecraft:redstone_block")
                if direction == "fall":
                    sim.place(flip.pos, "minecraft:redstone_block")
                sim.settle(contract.budgets.rest)
                sim.step(2)
                sim.clear_changes()
                t0 = sim.tick
                sim.place(flip.pos, "minecraft:redstone_block" if direction == "rise" else "minecraft:air")
                sim.settle(contract.budgets.rest)
                last = 0
                for t, p, a, b in sim.changes():
                    if p in out_cells:
                        last = max(last, t - t0 + 1)
                res[direction] = max(res[direction], last)
                for n, pos in outs.items():
                    pw = int(parse(sim.get(pos))[1].get("power", "0"))
                    if pw:
                        on_min[n] = min(on_min.get(n, 15), pw)
                    else:
                        off_max[n] = 0
    res["out_strength"] = on_min
    return res


def _oracle_once(items, hold, preroll, workers):
    import oracle_env_diff as OD
    from tools.oracle import gametest
    from tools.oracle.diff import diff_traces
    from tools.oracle.sim import run_plan
    work = []
    for label, contract, blocks in items:
        pr = prepare(contract, blocks)
        if pr.errors:
            raise ValueError(f"{label}: {pr.errors[:2]}")
        bl = [(*p, s, pr.nbt.get(p)) for p, s in pr.world.items()]
        for cap, plan, checks in OD.captures(contract, hold, preroll):
            work.append((label, contract, cap, bl, plan, checks))
    oracle = gametest.capture_many([(f"cap{k}", w[3], w[4]) for k, w in enumerate(work)], workers=workers)
    out = {}
    for w, ot in zip(work, oracle):
        label, contract, cap, bl, plan, checks = w
        st = run_plan(bl, plan, origin=ot.origin)
        d = diff_traces(label, "lib", ot, st, plan)
        r = out.setdefault(label, {"captures": 0, "agree": 0, "vanilla_failures": [], "nucleation_failures": [], "first_divergence": None})
        r["captures"] += 1
        r["agree"] += int(d.agree_full)
        r["vanilla_failures"] += OD.evaluate(contract, ot, checks)
        r["nucleation_failures"] += OD.evaluate(contract, st, checks)
        if not d.agree_full and r["first_divergence"] is None:
            r["first_divergence"] = str(d.first_full)
    for r in out.values():
        r["vanilla_pass"] = not r["vanilla_failures"]
        r["nucleation_pass"] = not r["nucleation_failures"]
    return out


def oracle_check(items, hold=40, preroll=20, workers=4, retry=True):
    """items: [(label, contract, blocks)] -> {label: {"captures": n, "agree": n, "vanilla_pass", "nucleation_pass", ...}}.
    Same stimulus/trace/verdict machinery as scripts/oracle_env_diff.py (vectors held `hold` ticks each, every sequence a capture).
    Any label that does not pass cleanly is re-run ALONE in a fresh JVM once (`retry`): large shared batches on a loaded machine produced
    sporadic vanilla failures that never reproduced in isolation (docs/LIBRARY.md); the entry then carries `retried` and
    `first_attempt` so the flake stays visible."""
    out = _oracle_once(items, hold, preroll, workers)
    if retry:
        for label, contract, blocks in items:
            r = out[label]
            if not (r["vanilla_pass"] and r["nucleation_pass"] and r["agree"] == r["captures"]):
                again = _oracle_once([(label, contract, blocks)], hold, preroll, 1)[label]
                again["retried"] = True
                again["first_attempt"] = {k: r[k] for k in ("agree", "captures", "vanilla_failures", "nucleation_failures", "first_divergence")}
                out[label] = again
    return out


def oracle_task(task, hold=None, max_rows=40, max_model=24, workers=4, preroll=20):
    """Vanilla-oracle cross-check of a task's reference solution (same machinery as scripts/oracle_env_diff.py): vectors held `hold`
    ticks (default: the longest settle seen in an env run + 20), long truth tables sub-sampled to `max_rows`, comb model streams capped
    at `max_model` hidden vectors, every sequence its own capture. Returns the oracle_check entry for the task."""
    import copy
    c = copy.deepcopy(task.contract)
    if hold is None:
        r = run(task.contract, task.solution, seed=0, fast=True)
        hold = max([v.ticks for v in r.vectors] + [20]) + 20
    tt = c.behaviour.truth_table
    if tt and len(tt.rows) > max_rows:
        step = len(tt.rows) / max_rows
        keep = sorted({int(i * step) for i in range(max_rows)})
        tt.rows = [tt.rows[i] for i in keep]
    rm = c.behaviour.reference_model
    if rm and rm.mode == "comb" and rm.n_hidden and rm.n_hidden > max_model:
        rm.n_hidden = max_model
    res = oracle_check([(task.id, c, [list(b) for b in task.solution])], hold=hold, preroll=preroll, workers=workers)
    out = res[task.id]
    out["hold"] = hold
    out["vectors"] = len(c.vectors())
    return out


def verify(name, orientations=ORIENTATIONS, oracle=False, workers=4, params=None, max_lead=14):
    """Verify one component. (1) input-strength sweep in the identity orientation: leads 0..max_lead of dust before every input
    port (strength 15 - lead at the port); `min_in_strength` = the weakest strength with all stronger ones passing. (2) every
    orientation (rot 0/90/180/270 x mirror none/x) at lead 0 and at the weakest passing lead: `func` (vectors/sequences) and
    `inv` (structural invariants incl. paste_stable) recorded separately. (3) optional vanilla oracle on the same benches.
    The report fails (`env_ok` False) only when the identity orientation fails; other orientations that fail are recorded as
    unverified (rotation safety is a property of each cell, not a requirement)."""
    sp = specs()[name]
    t0 = time.time()
    rep = {"orientations": {}, "lead_sweep": {}, "failures": []}
    items = []
    best = 0
    for lead in range(0, max_lead + 1):          # all inputs weak together
        c, blocks, info = bench(name, 0, None, lead, params)
        func, inv, det = env_pass(c, blocks)
        rep["lead_sweep"][lead] = func and inv
        if lead == 0:
            rep["footprint"] = {"size": [info["bounds"][1][i] - info["bounds"][0][i] + 1 for i in range(3)], "blocks": info["blocks"]}
            if sp.get("kind", "comb") == "comb" or sp.get("characterize"):
                rep["timing"] = characterize(c, blocks)
        if not (func and inv):
            if lead == 0:
                rep["failures"].append({"orientation": "rot0", "lead": 0, "func": func, "inv": inv, **det})
            break
        best = lead
        if not any(p.kind == "pad" for p in c.ports):
            break
    rep["min_in_strength"] = 15 - best
    in_names = [p.name for p in bench(name, 0, None, 0, params)[0].inputs]
    per_port = {}
    if not rep["failures"]:
        for pn in in_names:                       # one weak input at a time, the others at full strength
            ok_lead = 0
            for lead in range(1, max_lead + 1):
                c, blocks, _ = bench(name, 0, None, {pn: lead}, params)
                func, _inv, _det = env_pass(c, blocks)
                if not func:
                    break
                ok_lead = lead
            per_port[pn] = 15 - ok_lead
    rep["min_in_strength_per_port"] = per_port
    for rot, mir in orientations:
        for lead in sorted({0, best}):
            c, blocks, info = bench(name, rot, mir, lead, params)
            func, inv, det = env_pass(c, blocks)
            key = orient_name(rot, mir)
            rep["orientations"].setdefault(key, {})[f"lead{lead}"] = {"func": func, "inv": inv}
            if (rot, mir) == (0, None) and not (func and inv):
                rep["failures"].append({"orientation": key, "lead": lead, "func": func, "inv": inv, **det})
            elif not (func and inv):
                rep.setdefault("orientation_failures", []).append({"orientation": key, "lead": lead, "func": func, "inv": inv})
            if oracle and (lead == 0 or (rot, mir) == (0, None)):
                items.append((f"{name}:{key}:lead{lead}", c, blocks))
    if oracle and items:
        rep["oracle"] = oracle_check(items, workers=workers)
    rep["env_ok"] = not rep["failures"]
    rep["oracle_ok"] = (all(r["vanilla_pass"] and r["nucleation_pass"] for r in rep["oracle"].values()) if oracle and items else None)
    rep["seconds"] = round(time.time() - t0, 1)
    return rep


def verify_all(name, oracle=False, workers=4, orientations=ORIENTATIONS):
    """verify() for every parameter variant of the spec (a single run when it has none). Returns the first variant's report
    with a `variants` list: [{"params", "report"}]."""
    sp = specs()[name]
    variants = sp.get("variants") or [None]
    reps = [(v, verify(name, orientations, oracle, workers, params=v)) for v in variants]
    top = dict(reps[0][1])
    if len(reps) > 1 or variants[0] is not None:
        top["variants"] = [{"params": v, "report": r} for v, r in reps]
        top["env_ok"] = all(r["env_ok"] for _, r in reps)
        top["oracle_ok"] = None if any(r["oracle_ok"] is None for _, r in reps) else all(r["oracle_ok"] for _, r in reps)
        top["seconds"] = round(sum(r["seconds"] for _, r in reps), 1)
    return top


def _verified_block(rep):
    ors = rep.get("oracle") or {}
    return {
        "env": rep["orientations"],
        "env_ok_orientations": sorted(k for k, v in rep["orientations"].items() if all(x["func"] and x["inv"] for x in v.values())),
        "func_ok_orientations": sorted(k for k, v in rep["orientations"].items() if all(x["func"] for x in v.values())),
        "paste_stable_orientations": sorted(k for k, v in rep["orientations"].items() if all(x["inv"] for x in v.values())),
        "min_in_strength": rep["min_in_strength"],
        "min_in_strength_per_port": rep.get("min_in_strength_per_port"),
        "oracle_captures": sum(r["captures"] for r in ors.values()) if ors else None,
        "oracle_tick_identical": sum(r["agree"] for r in ors.values()) if ors else None,
        "oracle_vanilla_pass": all(r["vanilla_pass"] for r in ors.values()) if ors else None,
        "oracle_nucleation_pass": all(r["nucleation_pass"] for r in ors.values()) if ors else None,
        "oracle_ok": rep.get("oracle_ok"),
    }


def build_meta(name, rep):
    sp = specs()[name]
    first = (sp.get("variants") or [None])[0]
    plist = "".join(f", {k}={v!r}" for k, v in (first or {}).items())
    build = compile_program(name, f"def build(b):\n    b.place({name}, at=(0, 0, 0), name='dut'{plist})\n")
    ports = rotated_ports(build)
    rows = None
    if sp.get("rows") is not None or sp.get("fn") is not None:
        ins = sp.get("inputs", [])
        rows = sp.get("rows") or [[list(c), list(r if isinstance(r, tuple) else (r,))]
                                  for c in itertools.product((0, 1), repeat=len(ins)) for r in [sp["fn"](**dict(zip(ins, c)))]]
    seqs = sp.get("sequences")
    if callable(seqs):
        seqs = seqs(dict(first or {}))
    m = {
        "name": name, "title": sp["title"], "description": sp["description"], "requires": sp.get("requires", []),
        "params": {"floor": True, **(first or {})}, "kind": sp.get("kind", "comb"),
        "ports": {k: {"dir": v["kind"], "pos": list(v["pos"]), "face": v["face"]} for k, v in ports.items()},
        "footprint": rep.get("footprint"), "timing_ticks": rep.get("timing"), "notes": sp.get("notes", ""),
        "spec": {"inputs": sp.get("inputs"), "outputs": sp.get("outputs"), "rows": rows, "sequences": seqs},
        "verified": _verified_block(rep), "provenance": sp.get("provenance", DEFAULT_PROVENANCE),
    }
    if rep.get("variants"):
        m["variants"] = [{"params": v["params"], "footprint": v["report"].get("footprint"), "timing_ticks": v["report"].get("timing"),
                          "verified": _verified_block(v["report"])} for v in rep["variants"]]
    if rep.get("orientation_failures"):
        m["orientation_failures"] = rep["orientation_failures"]
    return m


def write_meta(name, rep):
    m = build_meta(name, rep)
    (LIB / f"{name}.meta.json").write_text(json.dumps(m, indent=1, default=list) + "\n")
    return m


DEFAULT_PROVENANCE = {"origin": "hand-authored for MIRA", "licence": "repo licence (not third-party)", "redistributable": True}


# --------------------------------------------------------------------------- corpus mining: promote verified small circuits
CORPUS_INDEX = ROOT / "data" / "corpus" / "index.jsonl"
LIB_CORPUS = LIB / "corpus"
POWERED_KINDS = ("comparator", "repeater", "observer")


def parse_truth_table(text):
    """'a b | y\n0 0 | 0 ...' -> (inputs, outputs, [(in_bits, out_bits)]) or None."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines or "|" not in lines[0]:
        return None
    ins, outs = (side.split() for side in lines[0].split("|"))
    rows = []
    for ln in lines[1:]:
        left, right = ln.split("|")
        rows.append(([int(x) for x in left.split()], [int(x) for x in right.split()]))
    return ins, outs, rows


def corpus_candidates(max_blocks=60, sources=("converted/claudestone", "converted/redstone-verify"), limit=None):
    out = []
    for line in open(CORPUS_INDEX):
        r = json.loads(line)
        if r["source"] in sources and r["ml_use"] == "clean" and r["block_count"] <= max_blocks:
            out.append(r)
    return out[:limit] if limit else out


def load_litematic_blocks(path):
    import nucleation as nuc
    sch = nuc.Schematic.from_litematic(Path(path).read_bytes())
    blocks = {}
    for b in json.loads(sch.get_non_air_blocks_json()):
        props = ",".join(f"{k}={v}" for k, v in b.get("properties", []))
        blocks[(b["x"], b["y"], b["z"])] = b["name"] + (f"[{props}]" if props else "")
    return blocks


def corpus_contract(row, hold_settle=None):
    """Index row (+ its .meta.json sidecar) -> (Contract, solution, info) or (None, None, reason). Combinational truth tables only:
    claudestone `tests[].truth_table`, redstone-verify rsl via the Verilog `assign` of its spec (single-bit ports)."""
    path = ROOT / row["path"]
    side = json.loads(Path(str(path) + ".meta.json").read_text())
    blocks = load_litematic_blocks(path)
    ports = side["ports"]
    ins_pos = ports.get("inputs") or ports.get("in") or {}
    outs_pos = ports.get("outputs") or ports.get("out") or {}
    table = None
    delay = 20
    if side.get("kind") == "claudestone-spec":
        for t in side.get("tests", []):
            if t.get("truth_table"):
                table = parse_truth_table(t["truth_table"])
                delay = max(delay, int(t.get("max_delay") or 0))
                break
    elif side.get("spec_v"):
        table = verilog_truth_table(side["spec_v"], list(ins_pos), list(outs_pos))
    if not table:
        return None, None, "no combinational truth table"
    t_ins, t_outs, rows = table
    norm = lambda pos: tuple(pos[0]) if isinstance(pos[0], (list, tuple)) else tuple(pos)
    for name in t_ins:
        if name not in ins_pos or (isinstance(ins_pos[name][0], list) and len(ins_pos[name]) != 1):
            return None, None, f"input {name!r}: multi-position bus"
    for name in t_outs:
        if name not in outs_pos or (isinstance(outs_pos[name][0], list) and len(outs_pos[name]) != 1):
            return None, None, f"output {name!r}: multi-position bus"
    fixed, cports, solution = [], [], []
    lever_cells = set()
    for name in t_ins:
        pos = norm(ins_pos[name])
        st = blocks.get(pos)
        if st is None:
            fixed.append({"box": [*pos, *pos], "state": "minecraft:air"})
            cports.append(Port(name, "pad", pos))
        elif st.startswith("minecraft:lever"):
            fixed.append({"box": [*pos, *pos], "state": B.canonical(st)})
            cports.append(Port(name, "click", pos))
            lever_cells.add(pos)
        else:
            return None, None, f"input {name!r}: unexpected block {st} at {pos}"
    for name in t_outs:
        pos = norm(outs_pos[name])
        st = blocks.get(pos)
        short = B.short(st.split("[")[0]) if st else None
        if short == "redstone_wire":
            cports.append(Port(name, "wire_power", pos, min_power=1))
        elif short == "redstone_lamp":
            cports.append(Port(name, "lamp", pos))
        elif short in ("redstone_torch", "redstone_wall_torch"):
            cports.append(Port(name, "torch", pos))
        elif short in POWERED_KINDS:
            cports.append(Port(name, "block_prop", pos, prop={"prop": "powered", "value": "true", "block": "minecraft:" + short}))
        else:
            return None, None, f"output {name!r}: unsupported probe block {short}"
    for pos, st in blocks.items():
        if pos not in lever_cells:
            solution.append([*pos, st])
    allpos = list(blocks) + [p.pos for p in cports]
    lo = [min(p[i] for p in allpos) for i in range(3)]
    hi = [max(p[i] for p in allpos) for i in range(3)]
    bbox = [lo[0] - 1, lo[1] - 1, lo[2] - 1, hi[0] + 1, hi[1] + 1, hi[2] + 1]
    vecs = []
    for i, (ib, ob) in enumerate(rows):
        vecs.append(Vector(dict(zip(t_ins, ib)), dict(zip(t_outs, ob)), i % 2 == 0, "".join(map(str, ib))))
    vecs += [Vector(v.inputs, v.expect, False, v.label + "'") for v in reversed(vecs)]
    inv = Invariants(paste_stable=True, no_popoff=True)
    c = Contract(id="corpus", title=row["path"], tier="CORPUS", description="corpus circuit", bbox=bbox, build_box=list(bbox), ports=cports,
                 fixed=fixed, behaviour=Behaviour(truth_table=TruthTable(vecs, settle=hold_settle or max(150, 6 * delay), shuffle=True)),
                 invariants=inv, budgets=Budgets(rest=400, total=500000, max_blocks=2000), eval_seed=5, forbidden=DEFAULT_FORBIDDEN)
    errs = c.validate()
    if errs:
        return None, None, f"contract invalid: {errs[:2]}"
    return c, solution, {"inputs": t_ins, "outputs": t_outs, "rows": rows, "delay": delay, "licence_note": side.get("licence"),
                         "commit": side.get("commit"), "spec_path": side.get("spec_path") or side.get("source_layout")}


def verilog_truth_table(spec_v, in_names, out_names):
    """Evaluate `assign y = <expr>;` lines (single-bit; operators & | ^ ~ and parentheses) into a truth table, or None."""
    import ast
    import re
    exprs = dict(re.findall(r"assign\s+(\w+)\s*=\s*([^;]+);", spec_v))
    if not exprs or set(out_names) - set(exprs):
        return None
    if re.search(r"\[|\{|\?|<<|>>|\+|-|==", spec_v.split("endmodule")[0].split(");", 1)[-1]):
        return None

    def ev(e, env):
        tree = ast.parse(re.sub(r"~", "1^", e.strip()), mode="eval")
        for n in ast.walk(tree):
            if not isinstance(n, (ast.Expression, ast.BinOp, ast.Name, ast.Constant, ast.BitAnd, ast.BitOr, ast.BitXor, ast.Load, ast.UnaryOp)):
                raise ValueError("unsupported expression")
        return eval(compile(tree, "<spec>", "eval"), {"__builtins__": {}}, env) & 1
    rows = []
    try:
        for bits in itertools.product((0, 1), repeat=len(in_names)):
            env = dict(zip(in_names, bits))
            vals = dict(env)
            for _ in range(len(exprs) + 1):                     # resolve intermediate wires by iteration
                for k, e in exprs.items():
                    try:
                        vals[k] = ev(e, vals)
                    except NameError:
                        pass
            rows.append((list(bits), [vals[o] for o in out_names]))
    except (ValueError, SyntaxError, KeyError):
        return None
    return list(in_names), list(out_names), rows


def corpus_scan(rows, oracle=False, workers=4, progress=True):
    """env (+ vanilla oracle) verdict for every candidate; returns a list of result dicts."""
    results, items = [], []
    for i, row in enumerate(rows):
        res = {"path": row["path"], "blocks": row["block_count"], "source": row["source"]}
        try:
            c, sol, info = corpus_contract(row)
        except Exception as e:  # unreadable file / sidecar quirks
            c, sol, info = None, None, f"{type(e).__name__}: {str(e)[:80]}"
        if c is None:
            res.update(status="skipped", reason=info)
        else:
            func, inv, det = env_pass(c, sol)
            res.update(status="env_pass" if func and inv else "env_fail", func=func, inv=inv, ports=len(c.ports),
                       vectors=len(c.vectors()) // 2, detail=det if not (func and inv) else None, info=info)
            if not func:                                    # state-dependent? retry with the baseline restored before every vector
                c.behaviour.truth_table.reset_between, c.behaviour.truth_table.shuffle = True, False
                res["passes_with_reset_between"] = bool(env_pass(c, sol)[0])
                c.behaviour.truth_table.reset_between, c.behaviour.truth_table.shuffle = False, True
            if oracle and func and inv:
                items.append((row["path"], c, sol, det["max_ticks"]))
        results.append(res)
        if progress and (i + 1) % 25 == 0:
            print(f"  scanned {i + 1}/{len(rows)}", flush=True)
    if oracle and items:
        by = {r["path"]: r for r in results}
        for label, r in corpus_oracle(items, workers).items():
            by[label]["oracle"] = r
    return results


def corpus_oracle(entries, workers=4):
    """entries: [(label, contract, solution, ticks)] -> {label: oracle result}. Vectors are held max(80, ticks + 40) ticks (`ticks` = the longest
    vector settle seen in the env run); entries are grouped by hold so slow circuits (delay lines) do not make every other circuit wait."""
    groups = {}
    for label, c, sol, ticks in entries:
        groups.setdefault(max(80, 40 * (-(-(ticks + 40) // 40))), []).append((label, c, sol))
    out = {}
    for hold, items in sorted(groups.items()):
        for k in range(0, len(items), 30):
            try:
                out.update(oracle_check(items[k:k + 30], hold=hold, workers=workers))
            except Exception as e:
                for label, _c, _s in items[k:k + 30]:
                    out[label] = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
    return out


def compact_scan(results):
    """Write lib/corpus/scan.json: one small record per candidate (verdicts only, no vectors / waveforms)."""
    slim = []
    for r in results:
        o = r.get("oracle") or {}
        slim.append({"path": r["path"], "source": r.get("source"), "blocks": r["blocks"], "status": r["status"], "reason": r.get("reason"),
                     "passes_with_reset_between": r.get("passes_with_reset_between"),
                     "oracle": ({"vanilla_pass": o.get("vanilla_pass"), "nucleation_pass": o.get("nucleation_pass"), "retried": o.get("retried"),
                                 "tick_identical": o.get("agree") == o.get("captures"), "error": o.get("error"),
                                 "vanilla_failures": (o.get("vanilla_failures") or [])[:3]} if o else None)})
    LIB_CORPUS.mkdir(parents=True, exist_ok=True)
    (LIB_CORPUS / "scan.json").write_text(json.dumps(slim, indent=0) + "\n")


def promote(results, max_blocks=60):
    """Copy env- and oracle-verified corpus circuits into lib/corpus/ with a provenance sidecar. Returns the promoted index entries."""
    import shutil
    LIB_CORPUS.mkdir(parents=True, exist_ok=True)
    out = []
    for r in results:
        o = r.get("oracle") or {}
        if r["status"] != "env_pass" or not (o.get("vanilla_pass") and o.get("nucleation_pass")) or r["blocks"] > max_blocks:
            continue
        src = ROOT / r["path"]
        name = "__".join(Path(r["path"]).parts[-3:]).replace(".litematic", "")
        dst = LIB_CORPUS / f"{name}.litematic"
        shutil.copy(src, dst)
        side = json.loads(Path(str(src) + ".meta.json").read_text())
        entry = {"name": name, "file": f"corpus/{name}.litematic", "blocks": r["blocks"], "inputs": r["info"]["inputs"], "outputs": r["info"]["outputs"],
                 "truth_table_rows": len(r["info"]["rows"]), "ports": side["ports"],
                 "provenance": {"source": r["source"], "path": r["path"], "upstream_spec": r["info"]["spec_path"], "commit": r["info"]["commit"],
                                "licence": side.get("licence"), "redistributable": True,
                                "note": "converted by scripts/corpus_convert.py; MIT-licensed upstream, keep the licence notice"},
                 "verified": {"env": True, "oracle_vanilla": True, "oracle_tick_identical": o.get("agree") == o.get("captures"),
                              "oracle_mc": "26.2", "nucleation": "0.12.0"}}
        out.append(entry)
        blocks = load_litematic_blocks(src)
        mn = [min(p[i] for p in blocks) for i in range(3)]       # simulation.builder.use() translates a cell to its min non-air corner
        flat = {}
        for grp in ("inputs", "in", "outputs", "out"):
            for pname, poss in (side["ports"].get(grp) or {}).items():
                pos = poss[0] if isinstance(poss[0], (list, tuple)) else poss
                flat[pname] = [pos[i] - mn[i] for i in range(3)]
        Path(str(dst) + ".ports.json").write_text(json.dumps(flat, indent=0) + "\n")   # simulation.builder.use() sidecar
    (LIB_CORPUS / "index.json").write_text(json.dumps(out, indent=1) + "\n")
    return out


def catalogue_table():
    rows = []
    for n, m in all_meta().items():
        fp = m.get("footprint") or {}
        rows.append((n, "x".join(map(str, fp.get("size", []))), fp.get("blocks"), m.get("timing_ticks"),
                     len(m["verified"]["env_ok_orientations"]), m["verified"].get("oracle_ok")))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "verify", "write", "corpus"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-blocks", type=int, default=60)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="corpus: reuse lib/corpus/scan.json (skip the env scan, only run the oracle on its env passes)")
    a = ap.parse_args(argv)
    if a.cmd == "corpus":
        rows = corpus_candidates(a.max_blocks, limit=a.limit)
        if a.reuse and (LIB_CORPUS / "scan.json").exists():
            res = json.loads((LIB_CORPUS / "scan.json").read_text())
            by_path = {r["path"]: r for r in rows}
            items = []
            for r in res:
                if r["status"] == "env_pass" and r["path"] in by_path:
                    c, sol, _info = corpus_contract(by_path[r["path"]])
                    r["info"] = _info
                    items.append((r["path"], c, sol, env_pass(c, sol)[2]["max_ticks"]))
            byres = {r["path"]: r for r in res}
            for label, o in corpus_oracle(items, a.workers).items():
                byres[label]["oracle"] = o
        else:
            res = corpus_scan(rows, oracle=a.oracle, workers=a.workers)
        import collections
        print(collections.Counter(r["status"] for r in res))
        print(collections.Counter(r.get("reason", "")[:50] for r in res if r["status"] == "skipped").most_common(8))
        if a.oracle:
            def verdict(o):
                if o.get("error"):
                    return "error"
                return "pass" if o.get("vanilla_pass") and o.get("nucleation_pass") else "fail"
            print("oracle:", collections.Counter(verdict(r["oracle"]) for r in res if r.get("oracle")))
        compact_scan(res)
        if a.promote:
            print("promoted", len(promote(res, a.max_blocks)))
        return 0
    if a.cmd == "list":
        print(f"{'component':16} {'size':10} {'blocks':>6} {'timing':24} {'orient':>6} oracle")
        for r in catalogue_table():
            print(f"{r[0]:16} {r[1]:10} {r[2]!s:>6} {str(r[3]):24} {r[4]:>6} {r[5]}")
        return 0
    todo = a.names or [n for n in names() if n in specs()]
    bad = 0
    for n in todo:
        rep = verify_all(n, oracle=a.oracle, workers=a.workers)
        print(f"{n:16} env={'OK' if rep['env_ok'] else 'FAIL'} oracle={rep['oracle_ok']} {rep.get('footprint')} timing={rep.get('timing')} {rep['seconds']}s")
        for f in rep["failures"][:3]:
            print("   ", json.dumps(f)[:400])
        bad += not rep["env_ok"] or rep["oracle_ok"] is False
        if a.cmd == "write":
            write_meta(n, rep)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
