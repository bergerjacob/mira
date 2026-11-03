"""Spec and vanilla checks for project mode (`spec-check`, `verify-vanilla`; docs/PRODUCT.md).

Pure-ish helpers called by `mira_mcp/project.py`:

    schema_problems(raw_json_dict)            readable errors for a contract v2 dict, before Contract.from_dict
    port_cell_findings(contract, world)       spec ports that point at the wrong block in the current build
    vacuity(contract)                         outputs that never change / inputs that never matter / never driven
    coverage_lines(contract)                  IO coverage summary
    mutation(contract, blocks, ...)           spec-weakness: break the build in small ways, count what the spec notices
    vanilla(contract, blocks, ...)            replay the spec in the vanilla 26.2 oracle (one JVM per capture), env vs vanilla

`mutation` runs env.runner in worker processes; `vanilla` needs the oracle (`bash scripts/oracle_setup.sh`).
"""
import difflib
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTRACT_KEYS = ("id", "title", "tier", "description", "bbox", "build_box", "ports", "fixed", "behaviour", "invariants",
                 "budgets", "forbidden", "keep_clear", "no_write", "eval_seed", "depends", "reference", "hints")
REQUIRED = ("id", "title", "tier", "description", "bbox", "build_box", "ports")
PORT_KEYS = ("name", "kind", "pos", "facing", "pulse", "min_power", "prop", "box", "region_mode", "block", "blocks", "doc")
BEHAVIOUR_KEYS = ("truth_table", "sequences", "reference_model")
TABLE_KEYS = ("rows", "settle", "extra", "shuffle", "reset_between", "order_independent")
ROW_KEYS = ("inputs", "expect", "public", "label")
SEQ_KEYS = ("name", "events", "expect", "duration", "public")
EXPECT_KEYS = ("port", "value", "t1", "t2", "mode")
BUDGET_KEYS = ("rest", "total", "max_blocks")
INV_KEYS = ("at_rest", "paste_stable", "deterministic", "no_oscillation", "no_forbidden", "no_collateral", "keep_clear",
            "no_popoff", "change_boxes")

CLICK_BLOCKS = ("lever", "_button", "_pressure_plate")
OUT_BLOCKS = {"lamp": ("redstone_lamp",), "wire_power": ("redstone_wire",), "piston": ("piston", "sticky_piston"),
              "torch": ("redstone_torch", "redstone_wall_torch")}


def _suggest(key, valid) -> str:
    m = difflib.get_close_matches(str(key), valid, n=1)
    return f" (did you mean '{m[0]}'?)" if m else ""


def _is_box(b, n=6) -> bool:
    return isinstance(b, (list, tuple)) and len(b) == n and all(isinstance(v, int) and not isinstance(v, bool) for v in b)


def _unknown(d: dict, valid, where: str, out: List[str]):
    for k in d:
        if k not in valid:
            out.append(f"{where}: unknown key '{k}'{_suggest(k, valid)}; valid keys: {', '.join(valid)}")


# --------------------------------------------------------------------------- schema
def schema_problems(d) -> List[str]:
    """Readable problems of a contract v2 JSON dict (docs/ENV.md, docs/PRODUCT.md cheat sheet); [] when it loads and validates."""
    from env.contract import INPUT_KINDS, OUTPUT_KINDS, REGION_MODES, Contract
    out: List[str] = []
    if not isinstance(d, dict):
        return ["spec.json must be a JSON object (contract v2)"]
    for k in REQUIRED:
        if k not in d:
            out.append(f"missing key '{k}' (required: {', '.join(REQUIRED)})")
    _unknown(d, CONTRACT_KEYS, "spec", out)
    for k in ("bbox", "build_box"):
        if k in d and not _is_box(d[k]):
            hint = " (a single box [x1,y1,z1,x2,y2,z2]; several boxes are not supported, take the bounding box of them)" if k == "build_box" else ""
            out.append(f"{k} must be 6 integers [x1,y1,z1,x2,y2,z2], got {d[k]!r}{hint}")
    bbox, bb = d.get("bbox"), d.get("build_box")
    if _is_box(bbox) and _is_box(bb):
        if any(bb[i] < bbox[i] or bb[i + 3] > bbox[i + 3] for i in range(3)):
            out.append(f"build_box {bb} sticks out of bbox {bbox}: the simulated world (bbox) must contain everything that may be built")
    names = []
    kinds = INPUT_KINDS + OUTPUT_KINDS
    for i, p in enumerate(d.get("ports") or []):
        w = f"ports[{i}]" + (f" '{p.get('name')}'" if isinstance(p, dict) and p.get("name") else "")
        if not isinstance(p, dict):
            out.append(f"{w}: must be an object")
            continue
        _unknown(p, PORT_KEYS, w, out)
        if not p.get("name") or not isinstance(p.get("name"), str):
            out.append(f"{w}: needs a non-empty string 'name'")
        else:
            names.append(p["name"])
        if p.get("kind") not in kinds:
            out.append(f"{w}: kind {p.get('kind')!r} is not one of inputs {INPUT_KINDS} / outputs {OUTPUT_KINDS}")
        if not _is_box(p.get("pos"), 3):
            out.append(f"{w}: 'pos' must be 3 integers [x,y,z], got {p.get('pos')!r}")
        elif _is_box(bbox) and any(not bbox[i] <= p["pos"][i] <= bbox[i + 3] for i in range(3)):
            out.append(f"{w}: pos {p['pos']} is outside bbox {bbox}")
        if p.get("kind") == "region" and (not _is_box(p.get("box")) or p.get("region_mode", "air") not in REGION_MODES):
            out.append(f"{w}: a region port needs 'box' [x1,y1,z1,x2,y2,z2] and region_mode in {REGION_MODES}")
        if p.get("pulse") and p.get("kind") != "click":
            out.append(f"{w}: 'pulse' only applies to click ports (value 1 = press the button, 0 = nothing)")
        if p.get("kind") == "wire_power" and not 1 <= int(p.get("min_power", 1) or 1) <= 15:
            out.append(f"{w}: min_power must be 1..15")
    for dup in sorted(n for n, c in Counter(names).items() if c > 1):
        out.append(f"duplicate port name '{dup}'")
    for i, f in enumerate(d.get("fixed") or []):
        if not isinstance(f, dict) or not _is_box(f.get("box")) or not isinstance(f.get("state"), str):
            out.append(f"fixed[{i}]: needs {{'box': [x1,y1,z1,x2,y2,z2], 'state': 'minecraft:...'}} (a single cell is box [x,y,z,x,y,z]); "
                       f"an air state reserves the cells (pads), got {f!r}"[:240])
    for k in ("keep_clear", "no_write"):
        for i, b in enumerate(d.get(k) or []):
            if not _is_box(b):
                out.append(f"{k}[{i}] must be a box of 6 integers, got {b!r}")
    beh = d.get("behaviour")
    if beh is not None:
        if not isinstance(beh, dict):
            out.append("behaviour must be an object with truth_table / sequences / reference_model")
        else:
            _unknown(beh, BEHAVIOUR_KEYS, "behaviour", out)
            tt = beh.get("truth_table")
            if tt:
                _unknown(tt, TABLE_KEYS, "behaviour.truth_table", out)
                for i, r in enumerate(tt.get("rows") or []):
                    w = f"truth_table.rows[{i}]"
                    if not isinstance(r, dict):
                        out.append(f"{w}: must be an object {{inputs, expect}}")
                        continue
                    _unknown(r, ROW_KEYS, w, out)
                    for k in ("inputs", "expect"):
                        if not isinstance(r.get(k), dict):
                            out.append(f"{w}: '{k}' must be an object {{port: value}}")
                            continue
                        for pn, val in r[k].items():
                            if names and pn not in names:
                                out.append(f"{w}.{k}: unknown port '{pn}'{_suggest(pn, names)}")
                            if val not in (0, 1, None) or isinstance(val, bool):
                                out.append(f"{w}.{k}.{pn}: value must be 0 or 1{' or null (don' + chr(39) + 't care)' if k == 'expect' else ''}, got {val!r}")
                    if len(out) > 60:
                        break
            for i, s in enumerate(beh.get("sequences") or []):
                w = f"sequences[{i}]" + (f" '{s.get('name')}'" if isinstance(s, dict) else "")
                if not isinstance(s, dict):
                    out.append(f"{w}: must be an object")
                    continue
                _unknown(s, SEQ_KEYS, w, out)
                dur = s.get("duration")
                if not isinstance(dur, int) or dur <= 0:
                    out.append(f"{w}: 'duration' must be a positive integer (ticks), got {dur!r}")
                for j, e in enumerate(s.get("events") or []):
                    if not isinstance(e, dict) or not isinstance(e.get("t"), int) or not isinstance(e.get("set"), dict):
                        out.append(f"{w}.events[{j}]: needs {{'t': tick, 'set': {{port: 0|1}}}}, got {e!r}"[:200])
                        continue
                    if isinstance(dur, int) and not 0 <= e["t"] <= dur:
                        out.append(f"{w}.events[{j}]: t={e['t']} is outside 0..{dur}")
                    for pn in e["set"]:
                        if names and pn not in names:
                            out.append(f"{w}.events[{j}]: unknown port '{pn}'{_suggest(pn, names)}")
                for j, e in enumerate(s.get("expect") or []):
                    if not isinstance(e, dict):
                        out.append(f"{w}.expect[{j}]: must be an object {{port, value, t1, t2, mode}}")
                        continue
                    _unknown(e, EXPECT_KEYS, f"{w}.expect[{j}]", out)
                    if e.get("mode", "all") not in ("all", "any"):
                        out.append(f"{w}.expect[{j}]: mode must be 'all' or 'any'")
                    if names and e.get("port") not in names:
                        out.append(f"{w}.expect[{j}]: unknown port '{e.get('port')}'{_suggest(e.get('port'), names)}")
    _unknown(d.get("budgets") or {}, BUDGET_KEYS, "budgets", out)
    _unknown(d.get("invariants") or {}, INV_KEYS, "invariants", out)
    if out:
        return out[:40]
    try:
        c = Contract.from_dict(d)
    except (TypeError, KeyError, ValueError) as e:
        return [f"contract v2 does not load: {type(e).__name__}: {e}"]
    return c.validate()[:40]


# --------------------------------------------------------------------------- the spec against the build
def _name(state: Optional[str]) -> str:
    return (state or "").split("[", 1)[0].split(":")[-1]


def port_cell_findings(contract, world: Dict[tuple, str]) -> List[str]:
    """Ports whose cell holds a block of the wrong kind in the current build (+ fixed scenery)."""
    out = []
    for p in contract.ports:
        if not p.pos:
            continue
        n = _name(world.get(tuple(p.pos)))
        if p.kind == "click" and not any(n == k or n.endswith(k) for k in CLICK_BLOCKS):
            out.append(f"input '{p.name}' (click) at {list(p.pos)} holds {n or 'air'}, not a lever/button/plate: nothing can be clicked there")
        elif p.kind == "pad" and n and n != "air":
            out.append(f"input '{p.name}' (pad) at {list(p.pos)} holds {n}: a pad cell must be empty (reserve it with a fixed air box); the harness places a redstone block there")
        elif p.kind in OUT_BLOCKS and n not in OUT_BLOCKS[p.kind]:
            out.append(f"output '{p.name}' ({p.kind}) at {list(p.pos)} holds {n or 'air'}, expected {' / '.join(OUT_BLOCKS[p.kind])}: it reads constant 0")
    return out


def _expected_values(contract):
    exp = defaultdict(set)
    for v in contract.vectors():
        for k, e in v.expect.items():
            if e is not None:
                exp[k].add(e)
    for s in contract.behaviour.sequences:
        for e in s.expect:
            exp[e.port].add(e.value)
    return exp


def vacuity(contract) -> List[str]:
    """Findings that make the spec too easy to pass. Static part only (the empty-build run is done by the caller)."""
    out = []
    exp = _expected_values(contract)
    vecs, seqs = contract.vectors(), contract.behaviour.sequences
    for o in contract.outputs:
        vals = exp.get(o.name, set())
        if not vals:
            out.append(f"output '{o.name}' is never checked by any vector or sequence")
        elif vals <= {0} or vals <= {1} or vals <= {"init"} or vals <= {"!init"}:
            out.append(f"output '{o.name}' is only ever expected to be {sorted(map(str, vals))[0]}: a build that never drives it can pass; "
                       "check it in both states (0 and 1, or 'init' and '!init')")
    names = [p.name for p in contract.inputs]
    full = [{**{n: 0 for n in names}, **v.inputs} for v in vecs]
    stim = {k for s in seqs for e in s.events for k in e.set}
    seen = defaultdict(set)
    for f in full:
        for k, val in f.items():
            seen[k].add(val)
    for p in contract.inputs:
        driven_rows = len(seen[p.name]) > 1 or any(p.name in v.inputs and v.inputs[p.name] for v in vecs) if p.pulse else len(seen[p.name]) > 1
        if not driven_rows and p.name not in stim:
            out.append(f"input '{p.name}' is never driven (no vector sets it to both values, no sequence event touches it)")
            continue
        if p.name in stim and not vecs:
            continue
        # does flipping only this input ever change an expected output?
        flips = False
        for i, a in enumerate(vecs):
            ia = full[i]
            for j in range(i + 1, len(vecs)):
                ib = full[j]
                diff = [k for k in ia if ia[k] != ib[k]]
                if diff == [p.name] and any(e is not None and vecs[j].expect.get(o) is not None and e != vecs[j].expect[o]
                                            for o, e in a.expect.items()):
                    flips = True
                    break
            if flips:
                break
        if vecs and not flips and p.name not in stim and len(vecs) <= 4096:
            out.append(f"input '{p.name}' is set in vectors but no pair of vectors differing only in '{p.name}' expects a different output: "
                       "the spec cannot tell whether it is wired")
    return out


def coverage_lines(contract) -> List[str]:
    vecs, seqs = contract.vectors(), contract.behaviour.sequences
    L = []
    ins, outs = contract.inputs, contract.outputs
    if vecs:
        pub = sum(1 for v in vecs if v.public)
        n = len(ins)
        combos = {tuple(({**{p.name: 0 for p in ins}, **v.inputs})[p.name] for p in ins) for v in vecs}
        cov = f", {len(combos)}/{2 ** n} input combinations ({len(combos) / 2 ** n:.0%})" if n <= 14 else f", {len(combos)} distinct input combinations of 2^{n}"
        dc = sum(1 for v in vecs for e in v.expect.values() if e is None)
        tot = sum(len(v.expect) for v in vecs) or 1
        L.append(f"vectors: {len(vecs)} ({pub} public, {len(vecs) - pub} hidden){cov}; {dc / tot:.0%} of expectations are don't-care")
    if seqs:
        L.append("sequences: " + ", ".join(f"{s.name}({len(s.events)} events, {len(s.expect)} checks, {s.duration}t{'' if s.public else ', hidden'})"
                                          for s in seqs[:8]) + (f" ... (+{len(seqs) - 8})" if len(seqs) > 8 else ""))
    tog = {}
    for p in ins:
        ups = downs = 0
        last = 0
        for v in vecs:
            val = v.inputs.get(p.name)
            if val is None:
                continue
            ups += val == 1 and last == 0
            downs += val == 0 and last == 1
            last = val
        for s in seqs:
            last = 0
            for e in sorted(s.events, key=lambda e: e.t):
                if p.name in e.set:
                    ups += e.set[p.name] == 1 and last == 0
                    downs += e.set[p.name] == 0 and last == 1
                    last = e.set[p.name]
        tog[p.name] = (ups, downs)
    if tog:
        weak = [k for k, (u, d) in tog.items() if (u == 0 or (d == 0 and not contract.port(k).pulse))]
        L.append(f"inputs ({len(ins)}): " + _compact(f"{k} {u} presses" if contract.port(k).pulse else f"{k} {u}^/{d}v" for k, (u, d) in tog.items())
                 + (f"; never rising or never falling: {', '.join(weak)}" if weak
                    else "; every level input rises and falls" if any(not p.pulse for p in ins) else ""))
    exp = defaultdict(lambda: Counter())
    for v in vecs:
        for k, e in v.expect.items():
            if e is not None:
                exp[k][e] += 1
    for s in seqs:
        for e in s.expect:
            exp[e.port][e.value] += 1
    if outs:
        L.append(f"outputs ({len(outs)}): " + _compact(
            f"{o.name} {sum(exp[o.name].values())} checks ({'/'.join(f'{k}:{v}' for k, v in sorted(exp[o.name].items(), key=lambda kv: str(kv[0])))})"
            for o in outs))
    return L


def _compact(items, limit: int = 10) -> str:
    items = list(items)
    return "; ".join(items[:limit]) + (f"; ... (+{len(items) - limit})" if len(items) > limit else "")


# --------------------------------------------------------------------------- mutation
def apply_mutant(blocks, edits):
    """env.mutate.apply keeping block-entity NBT (apply() drops it)."""
    from env import mutate
    nbt = {tuple(b[:3]): b[4] for b in blocks if len(b) > 4 and b[4]}
    res = mutate.apply([[b[0], b[1], b[2], b[3]] for b in blocks], edits)
    return [[x, y, z, s] + ([nbt[(x, y, z)]] if (x, y, z) in nbt else []) for x, y, z, s in res]


def _mutant_job(args):
    """Worker: run the spec on one mutant -> dict(behaviour_fail, structural_fail, reward)."""
    cd, blocks, edits, seed = args
    from env.contract import Contract
    from env.runner import run
    from env.score import score
    c = Contract.from_dict(cd)
    res = run(c, apply_mutant(blocks, edits), seed=seed, fast=True)
    sc = score(c, res)
    behaviour = (not res.compiled) or any(not v.ok for v in res.vectors) or any(not q.ok for q in res.sequences)
    return {"behaviour_fail": bool(behaviour), "full_pass": bool(sc.full_pass), "reward": round(sc.reward, 3),
            "inv": sorted(k for k, v in res.invariant_failures().items())[:3]}


def mutation(contract, blocks, max_mutants: int = 12, seed: int = 0, budget_s: float = 240.0, workers: Optional[int] = None,
             groups: Optional[Dict[tuple, str]] = None) -> dict:
    """Break the current build in small ways (env/mutate.py: remove / flip / turn / shift / repeater delay / torch->stone /
    wire->stone), count how many the spec notices. Candidates are redstone components only. A survivor = a mutant that still
    passes every vector, sequence and invariant: behaviour the spec does not pin down."""
    from concurrent.futures import ProcessPoolExecutor, wait
    from env import mutate
    from env.runner import run
    from env.score import score
    from simulation.router import is_active
    t0 = time.time()
    base = score(contract, run(contract, blocks, fast=True))
    if not base.full_pass:
        return {"skipped": "the current build does not pass its own spec (run `test`); mutation needs a passing build"}
    sol = [[b[0], b[1], b[2], b[3]] for b in blocks]
    state = {tuple(b[:3]): b[3] for b in blocks}
    cands = [m for m in mutate.pick(sol, contract.build_box, seed=seed)
             if is_active(state.get(tuple(m["edits"][0][1])))]
    if not cands:
        cands = mutate.pick(sol, contract.build_box, seed=seed)
    cands = cands[:max_mutants]
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 1, len(cands)))
    cd = contract.to_dict()
    results = []
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(_mutant_job, (cd, blocks, [list(e) for e in m["edits"]], seed)): m for m in cands}
        done, pending = wait(list(futs), timeout=max(5.0, budget_s - (time.time() - t0)))
        for f in done:
            try:
                results.append((futs[f], f.result()))
            except Exception as e:  # noqa: BLE001
                results.append((futs[f], {"error": f"{type(e).__name__}: {e}"[:120]}))
        for f in pending:
            f.cancel()
        ex.shutdown(wait=False, cancel_futures=True)
    ok = [(m, r) for m, r in results if "error" not in r]
    killed = [m for m, r in ok if r["behaviour_fail"]]
    structural = [m for m, r in ok if not r["behaviour_fail"] and not r["full_pass"]]
    survivors = [m for m, r in ok if r["full_pass"]]
    return {"tried": len(ok), "planned": len(cands), "errors": len(results) - len(ok), "killed": killed, "structural": structural,
            "survivors": survivors, "seconds": round(time.time() - t0, 1),
            "timed_out": len(cands) - len(results)}


def mutation_lines(res: dict, groups: Optional[Dict[tuple, str]] = None) -> List[str]:
    if res.get("skipped"):
        return [f"mutation: skipped - {res['skipped']}"]
    tried = res["tried"]
    if not tried:
        return ["mutation: no mutants could be run"]
    k, s, u = len(res["killed"]), len(res["structural"]), len(res["survivors"])
    L = [f"mutation: {k}/{tried} mutants of the build killed by the spec ({k / tried:.0%}), {s} broke only structural invariants "
         f"(not behaviour), {u} survived  [{res['seconds']}s" + (f"; {res['timed_out']} of {res['planned']} planned mutants not run within the budget" if res["timed_out"] else "") + "]"]
    for m in res["survivors"][:6]:
        pos = tuple(m["edits"][0][1])
        L.append(f"  SURVIVED: {m['name']}" + (f"  [{groups.get(pos, '?')}]" if groups else ""))
    if res["survivors"]:
        by = Counter((groups or {}).get(tuple(m["edits"][0][1]), "?") for m in res["survivors"])
        L.append("  weakness hints: the spec does not observe " + ", ".join(f"{g} ({n})" for g, n in by.most_common(3))
                 + " - add vectors/sequences that exercise the inputs feeding it and check the outputs it drives, "
                   "or accept the survivors if those cells are redundant (e.g. a delay nothing depends on)")
    if res["structural"]:
        L.append("  note: structural-only kills (e.g. floating dust) say nothing about spec strength: " + ", ".join(m["name"] for m in res["structural"][:3]))
    return L


# --------------------------------------------------------------------------- vanilla replay
def vanilla(contract, blocks, max_vectors: int = 12, every: bool = False, variants: int = 0, seed: int = 1, workers: int = 3,
            preroll: int = 20, second_origin: bool = False, progress=None) -> dict:
    """Replay the spec in the vanilla oracle, as the env scored it (env order, per-vector ticks, handover world at the env's
    absolute cells; tools/oracle/envplan.py), one JVM per capture. variants=K adds K rotated / mirrored / translated copies
    (env/variants.py seeds `seed`..`seed+K-1`; each is re-run in the env first); second_origin=True also replays the build
    at a random second absolute origin (the update order of vanilla depends on position).
    Returns {"cases": [...], "captures": [...], "available": bool}."""
    from env import runner
    from env.task import Task
    from env.variants import elem_name, variant_of, variant_params
    from tools.oracle import envplan, gametest
    from tools.oracle.envplan import evaluate
    from tools.oracle.sim import run_plan

    if not gametest.available():
        return {"available": False}
    task = Task(contract, [list(b) for b in blocks])
    cases = [("origin", task)]
    for k in range(max(0, variants)):
        sd = int(seed) + k
        if sd % 8 == 0:
            sd += 1       # seed 0 mod 8 is the identity element
        rot, mirror, off = variant_params(sd)
        cases.append((elem_name(sd), variant_of(task, seed=sd)))
    work, info = [], []
    for label, t in cases:
        c, sol = t.contract, t.solution
        pr = runner.prepare(c, sol)
        if pr.errors:
            info.append({"case": label, "error": "; ".join(pr.errors[:2])})
            continue
        rr = runner.run(c, sol, seed=None)
        env_v = {v.index: v.ok for v in rr.vectors}
        env_s = {q.name: q.ok for q in rr.sequences}
        try:
            world = envplan.handover_world(c, pr)
        except ValueError as e:
            info.append({"case": label, "error": str(e)})
            continue
        vecs = c.vectors()
        order, reset = envplan.env_schedule(c)
        ticks = {v.index: v.ticks for v in rr.vectors}
        total = len(order)
        sel = order
        sampled = False
        if not every and total > max_vectors:
            shuffle = c.vector_settings()[2]
            if shuffle or reset:
                pick = sorted(random.Random(seed).sample(range(total), max_vectors))
                sel = [order[i] for i in pick]
            else:
                sel = order[:max_vectors]
            sampled = True
        origin = envplan.env_origin(pr, world)
        origins = [("env origin", origin)]
        if second_origin and label == "origin":
            rng = random.Random(seed * 31 + 7)
            dx, dz = rng.randint(20, 90), rng.randint(20, 90)
            origins.append((f"second origin +{dx},0,+{dz}", (origin[0] + dx, origin[1], origin[2] + dz)))
        for oname, org in origins:
            caps = envplan.captures(c, preroll=preroll, vec_ticks=ticks, order=sel, reset=reset, world=world, origin=org)
            for cl, plan, checks in caps:
                work.append({"case": label, "origin": oname, "capture": cl, "contract": c, "world": world, "plan": plan,
                             "checks": checks, "env_v": env_v, "env_s": env_s, "vecs": vecs})
        info.append({"case": label, "vectors_total": total, "vectors_run": len(sel) if vecs else 0, "sampled": sampled,
                     "env_pass": bool(rr.compiled and all(env_v.values()) and all(env_s.values()))})
    if progress:
        progress(f"{len(work)} capture(s) -> vanilla (one JVM each, {workers} at a time)")
    t0 = time.time()
    traces = gametest.capture_many([(f"vv{i}", w["world"], w["plan"]) for i, w in enumerate(work)],
                                   workers=workers, isolated=True) if work else []
    caps = []
    for w, ot in zip(work, traces):
        c = w["contract"]
        st = run_plan(w["world"], w["plan"], origin=ot.origin)
        vb, sb = set(evaluate(c, ot, w["checks"])), set(evaluate(c, st, w["checks"]))
        rows = []
        if w["capture"].startswith("seq:"):
            nm = w["capture"][4:]
            bad_v = any(lbl.startswith(nm + "[") for lbl in vb)
            bad_s = any(lbl.startswith(nm + "[") for lbl in sb)
            rows.append((nm, w["env_s"].get(nm), not bad_v, not bad_s))
        else:
            idx = sorted({int(chk[3].split(":")[0][3:]) for chk in w["checks"]})
            for i in idx:
                lbl = f"vec{i}:"
                rows.append((i, w["env_v"].get(i), not any(x.startswith(lbl) for x in vb), not any(x.startswith(lbl) for x in sb)))
        caps.append({"case": w["case"], "origin": w["origin"], "capture": w["capture"], "rows": rows,
                     "ticks": ot.n_ticks, "origin_abs": list(ot.origin),
                     "labels": {i: (w["vecs"][i].inputs, w["vecs"][i].expect) for i in range(len(w["vecs"]))} if w["vecs"] else {}})
    return {"available": True, "cases": info, "captures": caps, "seconds": round(time.time() - t0, 1)}


def vanilla_text(res: dict, rev: int) -> str:
    if not res.get("available"):
        return ("VERIFY-VANILLA UNAVAILABLE: the vanilla 26.2 oracle is not installed in this environment "
                "(`bash scripts/oracle_setup.sh`, then retry)")
    if not res["captures"]:
        return "VERIFY-VANILLA FAILED: nothing to replay: " + "; ".join(f"{c['case']}: {c.get('error')}" for c in res["cases"])
    n_rows = n_bad = n_env_ok_van_bad = n_env_bad_van_ok = n_nuc_diff = 0
    L = []
    for c in res["captures"]:
        for key, env_ok, van_ok, nuc_ok in c["rows"]:
            n_rows += 1
            n_bad += not van_ok
            n_env_ok_van_bad += bool(env_ok) and not van_ok
            n_env_bad_van_ok += (env_ok is False) and van_ok
            n_nuc_diff += nuc_ok != van_ok
    errs = [c for c in res["cases"] if c.get("error")]
    verdict = "PASS" if not n_bad and not errs else "FAIL"
    L.append(f"VERIFY-VANILLA rev {rev}: {verdict}  {n_rows - n_bad}/{n_rows} checks pass in vanilla 26.2 "
             f"({len(res['captures'])} captures, one JVM each, {res['seconds']}s)"
             + (f"; {n_env_ok_van_bad} pass in the env but FAIL in vanilla" if n_env_ok_van_bad else "")
             + (f"; {n_env_bad_van_ok} fail in the env but pass in vanilla" if n_env_bad_van_ok else "")
             + (f"; Nucleation replay differs from vanilla on {n_nuc_diff}" if n_nuc_diff else ""))
    for c in res["cases"]:
        if c.get("error"):
            L.append(f"  case {c['case']}: {c['error']}")
        else:
            L.append(f"  case {c['case']}: env full pass {'yes' if c['env_pass'] else 'NO'}; vectors replayed {c['vectors_run']}/{c['vectors_total']}"
                     + (" (sampled; --all replays every vector)" if c["sampled"] else ""))
    by = defaultdict(list)
    for c in res["captures"]:
        by[(c["case"], c["origin"])].append(c)
    for (case, origin), cs in by.items():
        rows = [r for c in cs for r in c["rows"]]
        bad = [r for r in rows if not r[2]]
        L.append(f"  {case} @ {origin}: {len(rows) - len(bad)}/{len(rows)} ok in vanilla"
                 + (f" (vanilla origin {cs[0]['origin_abs']})" if cs else ""))
        for key, env_ok, van_ok, nuc_ok in bad[:6]:
            lab = ""
            for c in cs:
                if isinstance(key, int) and key in c["labels"]:
                    ins, exp = c["labels"][key]
                    lab = f" set {_short(ins)} expect {_short({k: v for k, v in exp.items() if v is not None})}"
            L.append(f"    FAIL {'vector #' + str(key) if isinstance(key, int) else 'sequence ' + str(key)}{lab}: "
                     f"env {'ok' if env_ok else 'FAIL'}, vanilla FAIL, Nucleation {'ok' if nuc_ok else 'FAIL'}")
        if len(bad) > 6:
            L.append(f"    ... (+{len(bad) - 6} more failing)")
    return "\n".join(L)


def _short(d: dict, limit: int = 6) -> str:
    it = list(d.items())
    return "{" + ",".join(f"{k}={v}" for k, v in it[:limit]) + (f",+{len(it) - limit}" if len(it) > limit else "") + "}"
