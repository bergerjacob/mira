"""Seed pool for the mutation arm: env task references, lib/ parts and small corpus logic circuits, reduced to the
fuzzer's vocabulary (gen.SAFE_IDS) and filtered to admissible builds, plus the small-edit mutation operators.

The known-bug repros (tools/oracle/circuits.py REPROS) and env's hand-written broken variants are NOT seed sources.
`python scripts/diff_fuzz/seeds.py` writes data/fuzz/seeds.json (deterministic; rerun is idempotent).
"""
import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.append(str(Path(__file__).resolve().parents[2]))

from env import blocks as B  # noqa: E402
from scripts.diff_fuzz.loadable import simulatable  # noqa: E402
from scripts.diff_fuzz import gen  # noqa: E402
from scripts.diff_fuzz.common import FUZZ_DIR, ROOT, Case, jdump, jload  # noqa: E402

SEEDS_PATH = FUZZ_DIR / "seeds.json"
DYNAMIC_DEFAULT = ("powered", "lit", "extended", "triggered", "power", "enabled", "open", "locked")
MAX_SEED_BLOCKS = 90


def tame(state: str) -> Optional[str]:
    """Map a source block state into the fuzzer vocabulary (dynamic properties back to their defaults, wire bare,
    plain full cubes -> stone) or None when it has no faithful image."""
    try:
        bid, props = B.split_state(state)
    except B.BlockError:
        return None
    name = B.short(bid)
    if B.is_air(state):
        return None
    if name == "redstone_wire":
        return "minecraft:redstone_wire"
    if name in ("stone_slab", "smooth_stone_slab") or name.endswith("_slab"):
        return f"minecraft:smooth_stone_slab[type={props.get('type', 'bottom')},waterlogged=false]"
    if name in gen.SAFE_IDS or bid in gen.SAFE_PLAIN:
        try:
            i = B.info(bid)
            full = dict(i["default_state"])
            for k, v in props.items():
                if k in full and k not in DYNAMIC_DEFAULT:
                    full[k] = v
            if name == "chest":
                full["type"] = "single"
            return B.canonical(B.join_state(bid, full))
        except Exception:
            return None
    i = B.info(bid)
    if i and i["full_cube"] and not i["properties"] and i["kind"] in ("minecraft:block",) and name not in (
            "tnt", "sand", "gravel", "red_sand", "dragon_egg", "cobweb", "bedrock", "barrier"):
        return "minecraft:stone"
    return None


def admissible(cells: Dict[tuple, str], nbt: Optional[dict] = None) -> bool:
    if not cells or not all(simulatable(B.canonical(s)) for s in set(cells.values())):
        return False
    return not gen.bad_attachments(cells, nbt)


def reduce_world(blocks) -> Optional[Tuple[Dict[tuple, str], Dict[tuple, str]]]:
    """[(x,y,z,state,nbt)] -> (cells, nbt) in vocabulary, or None."""
    cells, nbt = {}, {}
    for b in blocks:
        st = tame(b[3])
        if st is None:
            return None
        p = (b[0], b[1], b[2])
        cells[p] = st
        if len(b) > 4 and b[4] and B.short(B.split_state(st)[0]) in gen.CONTAINERS and "Slot" in str(b[4]):
            nbt[p] = b[4]
    mn = [min(p[i] for p in cells) for i in range(3)]
    cells = {tuple(p[i] - mn[i] for i in range(3)): s for p, s in cells.items()}
    nbt = {tuple(p[i] - mn[i] for i in range(3)): s for p, s in nbt.items()}
    if len(cells) > MAX_SEED_BLOCKS or not admissible(cells, nbt):
        return None
    return cells, nbt


def _sources() -> List[dict]:
    out = []
    # env task references (world = fixed scenery + the reference solution)
    from env.runner import prepare
    from env.task import list_task_ids, load_task
    for tid in list_task_ids():
        try:
            t = load_task(tid)
            pr = prepare(t.contract, t.solution)
            if pr.errors:
                continue
            blocks = [(p[0], p[1], p[2], s, pr.nbt.get(p)) for p, s in pr.world.items()]
        except Exception:
            continue
        if len(blocks) <= 400:
            out.append({"src": "env", "name": tid, "blocks": blocks})
    # lib parts
    try:
        from env import library as L
        for name in L.names():
            try:
                res = L.compile_program(name, f"def build(b):\n    b.place({name}, at=(0, 0, 0), name='dut')\n")
                if res.ok:
                    out.append({"src": "lib", "name": name, "blocks": [tuple(b) for b in res.blocks]})
            except Exception:
                continue
    except Exception:
        pass
    # small corpus logic circuits (index.jsonl: small_logic, <= 40 blocks, simulator-loadable)
    from tools.oracle import gametest
    idx = ROOT / "data/corpus/index.jsonl"
    if idx.exists():
        for line in idx.read_text().splitlines():
            r = json.loads(line)
            if not (r.get("small_logic") and r.get("sim_ok") and r["block_count"] <= 40) or r.get("entity_dependent"):
                continue
            path = ROOT / r["path"]
            if not path.exists():
                continue
            try:
                blocks, _dv = gametest.load_build(path)
            except Exception:
                continue
            out.append({"src": "corpus", "name": r.get("sha256", path.stem)[:12] + ":" + path.stem[:40], "blocks": blocks})
    return out


def build_pool(verbose: bool = True) -> List[dict]:
    pool, drop = [], {}
    for s in _sources():
        red = reduce_world(s["blocks"])
        if red is None:
            drop[s["src"]] = drop.get(s["src"], 0) + 1
            continue
        cells, nbt = red
        pool.append({"src": s["src"], "name": s["name"], "n": len(cells),
                     "blocks": [[p[0], p[1], p[2], st, nbt.get(p)] for p, st in sorted(cells.items())]})
    if verbose:
        by = {}
        for p in pool:
            by[p["src"]] = by.get(p["src"], 0) + 1
        print("seed pool:", by, "dropped:", drop)
    return pool


def load_pool() -> List[dict]:
    pool = jload(SEEDS_PATH)
    if pool is None:
        pool = build_pool()
        jdump(SEEDS_PATH, pool)
    return pool


# ------------------------------------------------------------------------------------------ mutation
SWAPS = {"piston": "sticky_piston", "sticky_piston": "piston", "repeater": "comparator", "comparator": "repeater",
         "redstone_lamp": "target", "redstone_torch": "lever", "stone": "redstone_block", "redstone_block": "stone",
         "dropper": "dispenser", "dispenser": "hopper", "hopper": "dropper", "observer": "piston", "chest": "barrel"}


def world_of(rng: random.Random, cells, nbt, pad: int = 1) -> gen.World:
    w = gen.World(rng, (max(p[0] for p in cells) + 1 + 2 * pad, max(p[1] for p in cells) + 1 + 2, max(p[2] for p in cells) + 1 + 2 * pad))
    for p, st in cells.items():
        q = (p[0] + pad, p[1], p[2] + pad)
        name = B.short(B.split_state(st)[0])
        w.cells[q] = st
        w.kind[q] = "support" if st in gen.SAFE_PLAIN or name.endswith("_slab") else name
        if p in nbt:
            w.nbt[q] = nbt[p]
        if name in ("lever", "stone_button", "oak_button"):
            w.inputs.append(q)
    return w


def mutate(rng: random.Random, w: gen.World) -> List[str]:
    """Apply 1-3 small edits in place; returns their names. The caller re-validates."""
    done = []
    for _ in range(rng.randint(1, 3)):
        comps = w.components()
        op = rng.choice(["remove", "turn", "param", "swap", "add", "add", "shift", "input"])
        if op == "add" or not comps:
            kind = gen.wchoice(rng, list(gen.KINDS.items()))
            if w.place(kind, w.pick_cell()):
                done.append(f"add {kind}")
            continue
        p = rng.choice(comps)
        bid, props = B.split_state(w.cells[p])
        name = B.short(bid)
        if op == "remove":
            del w.cells[p]
            w.kind.pop(p, None)
            w.nbt.pop(p, None)
            if p in w.inputs:
                w.inputs.remove(p)
            done.append(f"remove {name}")
        elif op == "turn" and "facing" in props:
            opts = gen.D6 if props["facing"] in gen.D6 and name in ("piston", "sticky_piston", "observer", "dropper", "dispenser") else gen.H4
            w.cells[p] = B.canonical(B.join_state(bid, {**props, "facing": rng.choice([o for o in opts if o != props["facing"]] or opts)}))
            done.append(f"turn {name}")
        elif op == "param" and name == "repeater":
            w.cells[p] = B.canonical(B.join_state(bid, {**props, "delay": str(rng.randint(1, 4))}))
            done.append("repeater delay")
        elif op == "param" and name == "comparator":
            w.cells[p] = B.canonical(B.join_state(bid, {**props, "mode": "subtract" if props["mode"] == "compare" else "compare"}))
            done.append("comparator mode")
        elif op == "swap" and name in SWAPS:
            new = B.canonical(f"minecraft:{SWAPS[name]}")
            keep = {k: v for k, v in props.items() if k in B.info("minecraft:" + SWAPS[name])["properties"] and k not in DYNAMIC_DEFAULT}
            try:
                new = B.canonical(B.join_state("minecraft:" + SWAPS[name], keep))
            except B.BlockError:
                pass
            w.cells[p] = new
            w.kind[p] = SWAPS[name]
            if name in gen.CONTAINERS or SWAPS[name] in gen.CONTAINERS:
                w.nbt.pop(p, None)
            done.append(f"swap {name}->{SWAPS[name]}")
        elif op == "shift":
            d = rng.choice(gen.H4)
            q = gen.add(p, d)
            st, k, n = w.cells.pop(p), w.kind.pop(p, None), w.nbt.pop(p, None)
            if w.free(q):
                w.put(q, st, k, n)
                if p in w.inputs:
                    w.inputs[w.inputs.index(p)] = q
                done.append(f"shift {name}")
            else:
                w.put(p, st, k, n)
        elif op == "input":
            if w.place(rng.choice(["lever", "button"]), w.pick_cell()):
                done.append("add input")
    return done


def prune_floating(w: gen.World):
    """Remove components that lost their support (repeat until stable); supports are never removed."""
    while True:
        bad = gen.bad_attachments(w.cells, w.nbt)
        if not bad:
            return
        for p in bad:
            w.cells.pop(p, None)
            w.kind.pop(p, None)
            w.nbt.pop(p, None)
            if p in w.inputs:
                w.inputs.remove(p)


def mutant(rng: random.Random, pool: List[dict], origins=None, arm: str = "mut", src: Optional[str] = None) -> Case:
    cands = [s for s in pool if src is None or s["src"] == src]
    for _ in range(60):
        s = rng.choice(cands)
        cells = {(b[0], b[1], b[2]): b[3] for b in s["blocks"]}
        nbt = {(b[0], b[1], b[2]): b[4] for b in s["blocks"] if b[4]}
        w = world_of(rng, cells, nbt)
        edits = mutate(rng, w)
        prune_floating(w)
        w.fix_containers()
        if not w.cells or not w.components() or len(w.cells) > MAX_SEED_BLOCKS + 10 or not w.valid():
            continue
        if not w.inputs and rng.random() < 0.7:     # make sure something can be stimulated most of the time
            for _t in range(8):
                if w.place(rng.choice(["lever", "button"]), w.pick_cell()):
                    break
            if not w.valid():
                continue
        ticks, preroll, ops = gen.make_plan(rng, w)
        mn = [min(p[i] for p in w.cells) for i in range(3)]
        blocks = [(p[0] - mn[0], p[1] - mn[1], p[2] - mn[2], st, w.nbt.get(p)) for p, st in w.cells.items()]
        ops = [(t, (p[0] - mn[0], p[1] - mn[1], p[2] - mn[2])) for t, p in ops]
        origin = rng.choice(origins) if origins else (0, 100, 0)
        return Case(blocks, ops, ticks, preroll, "place", tuple(origin),
                    {"arm": arm, "seed": f"{s['src']}:{s['name']}", "edits": edits})
    raise RuntimeError("mutation arm failed to produce a valid case")


if __name__ == "__main__":
    pool = build_pool()
    jdump(SEEDS_PATH, pool)
    print("wrote", SEEDS_PATH, len(pool), "seeds; sizes:", sorted(p["n"] for p in pool)[::max(1, len(pool) // 12)])
