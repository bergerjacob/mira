"""Repair and extend tasks from validated tasks (LLM-free).

repair  (family `repair`): a stored synthetic task + one program bug (training/synth/bugs.py) whose build FAILS the public spec.
        The task text gets the broken program; the reference (= the fix) is the original program, so "fix = original" holds by
        construction. Stored as rp_<base>_<j>.json (+ .py = fix, .broken.py = given program).
repair_env (family `repair_env`): env/tasks t0-t3 references with their stored broken variants (env/mutate.py edits, every one
        known to fail). Programs are flat `b.points(...)` listings (harness/reference.builder_source) of the broken / original block
        list. NOTE: these are the benchmark's own references; keep them out of training sets used for benchmark numbers.
extend  (family `extend`): a stored netlist N + one extra gate and output -> netlist N'. The task is N' (full gate); the given
        program is N's (its lamps sit at N's positions), the reference is N''s program.
"""
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from training.synth import common as C  # noqa: E402

REPAIR_NOTE = ("\n\nREPAIR: the program below was written for this task but does not meet the spec. Load it, find what is wrong and fix it; "
               "keep the rest of the design.\n```python\n{program}```")
EXTEND_NOTE = ("\n\nEXTEND: the program below implements an earlier version of this circuit ({old}) for an earlier layout. Extend it to "
               "the full spec above: the new output is {new}, and the lamps are now at the positions listed under Fixed.\n```python\n{program}```")


def _clone(task, tid, extra_desc):
    from env.task import task_from_dict
    d = task.to_dict()
    d["contract"]["id"] = tid
    d["contract"]["description"] = d["contract"]["description"] + extra_desc
    return task_from_dict(d)


def repair_synth(path, j, seed):
    from training.synth import bugs as G
    task, ref, synth = C.load(path)
    rng = random.Random(f"{task.id}:{seed}")
    cands = G.ordered(ref, rng)
    tried = 0
    for b in (cands[j:] + cands[:j])[:12]:
        tried += 1
        ok, why = G.verify_bug(task, b["text"])
        if not ok:
            continue
        tid = f"rp_{task.id}_{j}"
        rt = _clone(task, tid, REPAIR_NOTE.format(program=b["text"]))
        bug = {k: b[k] for k in ("kind", "why", "fix", "line", "old", "new")}
        out = C.save(rt, ref, {**{k: v for k, v in synth.items() if k not in ("kill",)}, "family": "repair", "source": "synth/repair",
                               "base": task.id, "bug": bug, "public_check": why}, "repair")
        out.with_suffix(".broken.py").write_text(b["text"])
        return {"accepted": True, "id": tid, "bug": b["kind"], "tried": tried, "blocks": len(task.solution)}
    return {"accepted": False, "reason": f"no failing bug among {tried}", "base": task.id}


def repair_env(task_id, j):
    from env.mutate import apply
    from env.task import load_task
    from harness.reference import builder_source
    task = load_task(task_id)
    if j >= len(task.broken):
        return {"accepted": False, "reason": "no stored broken variant", "base": task_id}
    br = task.broken[j]
    broken = builder_source(apply(task.solution, br["edits"]))
    fix = builder_source(task.solution)
    tid = f"rpe_{task_id}_{j}"
    rt = _clone(task, tid, REPAIR_NOTE.format(program=broken))
    rt.contract.tier = task.contract.tier
    bug = {"kind": br["kind"], "why": f"the build differs from a working one by one edit ({br['name']})",
           "fix": "undo that edit", "line": None, "old": None, "new": None}
    out = C.save(rt, fix, {"family": "repair_env", "source": f"env/tasks/{task_id}", "difficulty": task.contract.tier, "base": task_id,
                           "bug": bug, "contamination": "benchmark reference: exclude from training for benchmark numbers"}, "repair_env")
    out.with_suffix(".broken.py").write_text(broken)
    return {"accepted": True, "id": tid, "bug": br["kind"], "blocks": len(task.solution)}


def extend_netlist(path, seed, mutants=8):
    import itertools
    from training.synth.netlist import GATES, Gate, Netlist, make_task
    task, ref, synth = C.load(path)
    nl = Netlist.from_dict(synth["netlist"])
    rng = random.Random(f"ext:{task.id}:{seed}")
    old_cols = {tuple(o[y] for _, o in nl.table()) for y in nl.outputs}
    for _ in range(40):
        kind = rng.choice(["and", "or", "nor", "nand", "not"])
        ar = GATES[kind]["arity"]
        ins = rng.sample(nl.signals(), ar)
        g = Gate(kind, ins, f"n{len(nl.gates)}")
        new = Netlist(nl.inputs, nl.gates + [g], {**nl.outputs, f"Y{len(nl.outputs)}": g.name})
        col = tuple(o[f"Y{len(nl.outputs)}"] for _, o in new.table())
        if len(set(col)) == 2 and col not in old_cols and C.claim("extend", new.to_dict()):
            break
    else:
        return {"accepted": False, "reason": "no new distinct output", "base": task.id}
    tid = f"ex_{task.id}"
    t2, prog2, info = make_task(new, tid, seed=seed)
    rep = C.gate(t2, mutants)
    if not rep["accepted"]:
        return {"accepted": False, "reason": "; ".join(rep["problems"]), "base": task.id}
    yname = f"Y{len(nl.outputs)}"
    t2.contract.description += EXTEND_NOTE.format(old=", ".join(nl.outputs), new=f"{yname} = {new.expression(g.name)}", program=ref)
    xlamp = t2.contract.port(yname).pos[0]
    bug = {"kind": "extend", "why": f"the program has no gate for {yname} = {new.expression(g.name)} and its lamps are still at the old positions",
           "fix": f"add gate {g.name} = {kind}({', '.join(ins)}) at the east end, its bus and taps, and move the lamp column to x={xlamp}",
           "line": None, "old": None, "new": None}
    out = C.save(t2, prog2, {"family": "extend", "source": "synth/extend", "difficulty": synth.get("difficulty"), "base": task.id,
                             "netlist": info["netlist"], "depth": info["depth"], "bug": bug, "n_blocks": len(t2.solution),
                             "kill": f"{rep['killed']}/{rep['mutants']}"}, "extend")
    out.with_suffix(".broken.py").write_text(ref)
    return {"accepted": True, "id": tid, "blocks": len(t2.solution), "kill": f"{rep['killed']}/{rep['mutants']}"}


def jobs(what, per_task=2, limit=0, families=("netlist", "sequential")):
    if what == "repair":
        paths = [p for f in families for p in C.stored(f)]
        out = [("repair", str(p), j) for p in paths for j in range(per_task)]
        from env.task import list_task_ids
        out += [("repair_env", tid, j) for tid in list_task_ids() if tid[:2] in ("t0", "t1", "t2", "t3") for j in range(per_task)]
    else:
        paths = [p for p in C.stored("netlist") if "_hard_" not in p.stem]
        out = [("extend", str(p), 0) for p in paths]
    return out[:limit] if limit else out


def attempt(args):
    from env import library  # noqa: F401  (T3 reference models)
    kind, src, j = args
    tid = {"repair": f"rp_{Path(src).stem}_{j}", "repair_env": f"rpe_{src}_{j}", "extend": f"ex_{Path(src).stem}"}[kind]
    if (C.TASKS / kind / f"{tid}.json").exists():
        return {"accepted": True, "id": tid, "cached": True, "family": kind, "src": Path(src).stem, "seconds": 0}
    with C.Timer() as t:
        try:
            if kind == "repair":
                r = repair_synth(src, j, j)
            elif kind == "repair_env":
                r = repair_env(src, j)
            else:
                r = extend_netlist(src, j)
        except Exception as e:
            r = {"accepted": False, "reason": f"{type(e).__name__}: {str(e)[:300]}"}
    r.update(family=kind, src=Path(src).stem, seconds=t.s)
    return r
