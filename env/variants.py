"""Deterministic task variants: rotate (0/90/180/270 about y, clockwise from above), mirror (x) and translate a whole task.

    from env.variants import variant_of, load_variant, allowed_variants
    v = variant_of(task, 5)               # seed -> (rot, mirror, offset); seed 0 = the task itself
    v = load_variant("t1_xor", 5)         # same as env.task.load_task("t1_xor", variant=5)
    v = variant_of(task, rot=90, mirror="x", offset=(3, 0, -7))

Everything positional is transformed: contract bbox / build_box / fixed boxes and states / ports (pos, facing, region box,
block_prop side props) / keep_clear / no_write / change_boxes, the reference solution and the stored broken variants
(edit positions and states). Direction words and coordinate groups in the description, hints and port docs are rewritten too.
Vectors, sequences, depends, budgets, eval_seed and the reference stats (blocks, volume) are unchanged, so a variant is the same
task up to a rigid motion. Block states are transformed with the builder's tables (`simulation.blockstates.transform_state`):
facing, axis, rotation, per-side wire props, rail/stair shapes, hinges.

`python env/variants.py [ids...] [--jobs N] [--write]` checks that for every element of the dihedral group (8 = 4 rotations x
mirror none/x, seeds 0..7) the reference still fully passes and the stored broken variants still fail; `--write` stores the
result in env/tasks/t5/variants_report.json, which `allowed_variants(task_id)` reads.
"""
import json
import random
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from simulation.blockstates import parse_state, rotate_point, transform_state  # noqa: E402
from env.contract import Contract, Port  # noqa: E402
from env.task import Task  # noqa: E402

REPORT = Path(__file__).parent / "tasks" / "t5" / "variants_report.json"
DIRS = ("north", "east", "south", "west")
ELEMENTS = [(rot, mirror) for mirror in (None, "x") for rot in (0, 90, 180, 270)]   # seed % 8 -> group element


def variant_params(seed):
    """seed -> (rot, mirror, offset). Seed 0 is the identity; other seeds also translate by a seeded offset."""
    seed = int(seed)
    rot, mirror = ELEMENTS[seed % 8]
    if seed == 0:
        return rot, mirror, (0, 0, 0)
    rng = random.Random(seed * 7919 + 17)
    return rot, mirror, (rng.randint(-40, 40), rng.randint(0, 6), rng.randint(-40, 40))


class Xf:
    """mirror -> rotate about the origin -> shift so the bbox min corner moves by `offset`."""

    def __init__(self, bbox, rot=0, mirror=None, offset=(0, 0, 0)):
        if rot % 90 or mirror not in (None, "x", "z"):
            raise ValueError(f"rot must be a multiple of 90 and mirror None/'x'/'z' (got {rot}, {mirror})")
        self.rot, self.mirror = rot % 360, mirror
        c = [rotate_point(p, self.rot, mirror) for p in ((bbox[0], bbox[1], bbox[2]), (bbox[3], bbox[4], bbox[5]))]
        lo = [min(a[i], b[i]) for a, b in [c] for i in range(3)]
        self.shift = tuple(bbox[i] + offset[i] - lo[i] for i in range(3))

    def point(self, p):
        q = rotate_point(tuple(p), self.rot, self.mirror)
        return tuple(q[i] + self.shift[i] for i in range(3))

    def box(self, b):
        a, c = self.point(b[:3]), self.point(b[3:])
        return [min(a[0], c[0]), min(a[1], c[1]), min(a[2], c[2]), max(a[0], c[0]), max(a[1], c[1]), max(a[2], c[2])]

    def state(self, s):
        return transform_state(s, self.rot, self.mirror) if (self.rot or self.mirror) else s

    def dir(self, d):
        if d not in DIRS:
            return d
        return parse_state(transform_state(f"minecraft:x[facing={d}]", self.rot, self.mirror))[1]["facing"]

    def prop(self, k, v):
        """One block property (block_prop ports): side props are renamed, direction values transformed."""
        if k in DIRS:
            return self.dir(k), v
        if k == "facing":
            return k, self.dir(v)
        if k in ("axis", "rotation", "shape", "hinge", "type", "orientation"):
            return k, parse_state(self.state(f"minecraft:x[{k}={v}]"))[1][k]
        return k, v

    # ---------------------------------------------------------------- free text
    def text(self, s):
        if not s:
            return s

        def word(m):
            w = m.group(0)
            t = self.dir(w.lower())
            return t.capitalize() if w[0].isupper() else t
        s = re.sub(r"\b(?:north|east|south|west)\b", word, s, flags=re.I)
        num = r"-?\d+"
        comp = rf"\s*(?:([xyz])\s*=?\s*)?({num})(?:\s*\.\.\s*({num}))?\s*"

        def group(m):
            parts = [m.group(i) for i in range(1, 10)]
            axes = [parts[0], parts[3], parts[6]]
            if any(a and a != "xyz"[i] for i, a in enumerate(axes)):
                return m.group(0)
            lo = [int(parts[1]), int(parts[4]), int(parts[7])]
            hi = [int(parts[2] or parts[1]), int(parts[5] or parts[4]), int(parts[8] or parts[7])]
            if lo == hi and m.group(0)[0] == "[":
                return str(list(self.point(lo)))
            b = self.box(lo + hi)
            return "(" + ", ".join(f"{'xyz'[i]}={b[i]}" if b[i] == b[i + 3] else f"{'xyz'[i]} {b[i]}..{b[i + 3]}" for i in range(3)) + ")"
        return re.sub(rf"[\(\[]{comp},{comp},{comp}[\)\]]", group, s)


def _edit(xf, e):
    e = list(e)
    if e[0] == "remove":
        return ["remove", list(xf.point(e[1]))]
    return [e[0], list(xf.point(e[1])), xf.state(e[2]), *e[3:]]


def _port(xf, p: Port):
    kw = {"pos": xf.point(p.pos), "facing": xf.dir(p.facing) if p.facing else p.facing, "doc": xf.text(p.doc)}
    if p.box:
        kw["box"] = xf.box(p.box)
    if p.prop:
        prop = dict(p.prop)
        prop["prop"], prop["value"] = xf.prop(prop["prop"], prop["value"])
        kw["prop"] = prop
    return replace(p, **kw)


def transform_task(task: Task, rot=0, mirror=None, offset=(0, 0, 0), tag=None) -> Task:
    c = task.contract
    xf = Xf(c.bbox, rot, mirror, offset)
    d = c.to_dict()
    nc = Contract.from_dict(d)          # deep copy
    nc.id = c.id + (f"@{tag}" if tag else "")
    nc.description = xf.text(c.description)
    nc.hints = [xf.text(h) for h in c.hints]
    nc.bbox, nc.build_box = xf.box(c.bbox), xf.box(c.build_box)
    nc.fixed = [{**f, "box": xf.box(f["box"]), "state": xf.state(f["state"])} for f in c.fixed]
    nc.ports = [_port(xf, p) for p in c.ports]
    nc.keep_clear = [xf.box(b) for b in c.keep_clear]
    nc.no_write = [xf.box(b) for b in c.no_write]
    nc.invariants.change_boxes = [xf.box(b) for b in c.invariants.change_boxes]
    sol = [[*xf.point(b[:3]), xf.state(b[3]), *b[4:]] for b in task.solution]
    broken = [{**b, "edits": [_edit(xf, e) for e in b["edits"]]} for b in task.broken]
    notes = task.notes + (f"\n[variant {tag}: rot={xf.rot} mirror={mirror} shift={list(xf.shift)} of {c.id}]" if tag else "")
    return Task(nc, sol, broken, notes, task.path)


def variant_of(task: Task, seed=None, rot=None, mirror=None, offset=None) -> Task:
    """Seeded variant (see variant_params), or an explicit rot / mirror / offset. Seed 0 / no args = an unchanged copy."""
    if seed is not None:
        rot, mirror, offset = variant_params(seed)
        tag = f"v{seed}" if seed else None
    else:
        rot, offset = rot or 0, tuple(offset or (0, 0, 0))
        tag = f"r{rot % 360}{'m' + mirror if mirror else ''}o{'_'.join(map(str, offset))}" if (rot % 360 or mirror or any(offset)) else None
    return transform_task(task, rot, mirror, offset, tag)


def elem_name(seed):
    rot, m = ELEMENTS[int(seed) % 8]
    return f"rot{rot}" + ("+mirror_x" if m else "")


def load_variant(task_id_or_path, seed):
    from env.task import load_task
    return variant_of(load_task(task_id_or_path), seed)


def allowed_variants(task_id):
    """Seeds 0..7 whose variant passed the check in variants_report.json (all 8 when the task was never checked)."""
    rep = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    r = rep.get("tasks", {}).get(task_id.split("@")[0])
    return list(r["allowed"]) if r else list(range(8))


# ---------------------------------------------------------------------------- check: references pass, broken fail, in all 8 elements
def check_task(args):
    """-> {"id", "seeds": {seed: {"ref": bool, "reward", "broken_fail": k/n, "why": ...}}, "allowed": [...]}"""
    from env.runner import run
    from env.score import score
    from env.mutate import apply
    from env.task import load_task
    tid, seeds, broken_limit = args
    task = load_task(tid)
    out = {"id": task.id, "blocks": len(task.solution), "seeds": {}}
    t0 = time.time()
    for s in seeds:
        v = variant_of(task, s)
        res = run(v.contract, v.solution, seed=0)
        sc = score(v.contract, res)
        row = {"elem": elem_name(s), "ref": bool(sc.full_pass), "reward": round(sc.reward, 4)}
        if not sc.full_pass:
            row["why"] = {"invariants": {k: str(x["detail"])[:160] for k, x in res.invariant_failures().items()},
                          "vectors_bad": sum(not x.ok for x in res.vectors), "vectors": len(res.vectors),
                          "sequences_bad": [q.name for q in res.sequences if not q.ok], "errors": res.prepared.errors[:2]}
        bad = 0
        for b in v.broken[:broken_limit]:
            r = run(v.contract, apply(v.solution, b["edits"]), seed=0, fast=True)
            bad += not score(v.contract, r).full_pass
        row["broken_fail"] = f"{bad}/{min(len(v.broken), broken_limit)}"
        row["ok"] = row["ref"] and bad == min(len(v.broken), broken_limit)
        out["seeds"][s] = row
    out["allowed"] = [s for s in seeds if out["seeds"][s]["ok"]]
    out["seconds"] = round(time.time() - t0, 1)
    return out


def oracle_variants(pairs, jobs=4):
    """Vanilla 26.2 verdict (one JVM per capture) for [(task_id, seed)] -> {"id@vS": {...oracle_task result}}."""
    from env import library as L
    from env.task import load_task
    from scripts.t4_oracle import capture_many_isolated
    from tools.oracle import gametest
    gametest.capture_many = lambda js, workers=4, **kw: capture_many_isolated(js, jobs, **kw)
    out = {}
    for tid, s in pairs:
        t0 = time.time()
        r = L.oracle_task(variant_of(load_task(tid), s), workers=jobs)
        r = {k: r[k] for k in ("vanilla_pass", "nucleation_pass", "agree", "captures", "hold") if k in r} | {
            "vanilla_failures": r.get("vanilla_failures", [])[:3], "elem": elem_name(s), "seconds": round(time.time() - t0, 1)}
        out[f"{tid}@v{s}"] = r
        print(f"{tid}@v{s:<3} {elem_name(s):16} vanilla={'pass' if r['vanilla_pass'] else 'FAIL'} nucleation={'pass' if r['nucleation_pass'] else 'FAIL'} "
              f"tick-identical={r['agree']}/{r['captures']} {r['seconds']}s", flush=True)
    return out


def main(argv=None):
    import argparse
    from concurrent.futures import ProcessPoolExecutor
    from env.task import list_task_ids
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--seeds", default="0-7", help="e.g. 0-7 or 1,5")
    ap.add_argument("--broken", type=int, default=5, help="stored broken variants checked per seed")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--write", action="store_true", help=f"merge results into {REPORT.relative_to(REPORT.parents[3])}")
    ap.add_argument("--oracle", action="store_true", help="vanilla verdict of each (id, seed) instead of the env check (needs tools/oracle)")
    a = ap.parse_args(argv)
    seeds = list(range(int(a.seeds.split("-")[0]), int(a.seeds.split("-")[1]) + 1)) if "-" in a.seeds else [int(s) for s in a.seeds.split(",")]
    ids = a.ids or list_task_ids()
    if a.oracle:
        res = oracle_variants([(t, s) for t in ids for s in seeds], a.jobs)
        if a.write:
            rep = json.loads(REPORT.read_text()) if REPORT.exists() else {"tasks": {}}
            rep.setdefault("vanilla", {}).update(res)
            REPORT.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
        return 0 if all(r["vanilla_pass"] for r in res.values()) else 1
    with ProcessPoolExecutor(max(1, a.jobs)) as ex:                 # one job per (task, seed): big tasks spread over the pool
        parts = list(ex.map(check_task, [(t, [s], a.broken) for t in ids for s in seeds]))
    reps = []
    for t in ids:
        mine = [r for r in parts if r["id"] == t]
        seeds_t = {s: x for r in mine for s, x in r["seeds"].items()}
        reps.append({"id": t, "blocks": mine[0]["blocks"], "seeds": seeds_t, "allowed": [s for s in seeds if seeds_t[s]["ok"]],
                     "seconds": round(sum(r["seconds"] for r in mine), 1)})
    for r in reps:
        fails = {s: x for s, x in r["seeds"].items() if not x["ok"]}
        print(f"{r['id']:22} {r['blocks']:>5} allowed {len(r['allowed'])}/{len(seeds)} {r['seconds']:>6}s "
              + ("" if not fails else "FAIL " + "; ".join(f"v{s} {x['elem']} ref={x['ref']} broken_fail={x['broken_fail']} {json.dumps(x.get('why', ''))[:200]}"
                                                       for s, x in fails.items())), flush=True)
    if a.write:
        rep = json.loads(REPORT.read_text()) if REPORT.exists() else {"tasks": {}}
        for r in reps:
            rep["tasks"][r["id"]] = r
        rep["elements"] = {s: elem_name(s) for s in range(8)}
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    return 0 if all(len(r["allowed"]) == len(seeds) for r in reps) else 1


if __name__ == "__main__":
    sys.exit(main())
