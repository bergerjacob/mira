"""Nucleation (TickSimulation) side of the differential: run build + plan -> Trace.

Also owns the shared block-list -> Schematic / gametest-SNBT conversion so both
backends see byte-identical input.
"""

import json
import time
from typing import List, Optional, Tuple

import nucleation
from nucleation import Schematic, TickSettleMode, TickSimulation

from tools.oracle.trace import Plan, Pos, Trace, canon_state, normalize_blocks

# Oracle places the structure here (spawn-anchored); the sim gets the same origin so
# position-hashed update order (BlockPos.hashCode) matches. Set by the oracle capture
# result when available; this is the default used if the sim runs alone.
DEFAULT_ORIGIN: Pos = (0, 100, 0)

_SETTLE = {"place": TickSettleMode.Placement, "known_shape": TickSettleMode.Quiet,
           "in_world": TickSettleMode.InWorld}


def snbt_of(value) -> str:
    """Python value -> SNBT text (ints plain, strings quoted)."""
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "1b" if value else "0b"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(snbt_of(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}:{snbt_of(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"cannot encode {type(value)} as SNBT")


def build_schematic(blocks, name: str = "oracle") -> Tuple[Schematic, Pos]:
    """Block list -> (Schematic with min corner at 0,0,0, shift added to caller coords)."""
    blocks = normalize_blocks(blocks)
    if not blocks:
        raise ValueError("empty block list")
    mn = (min(b[0] for b in blocks), min(b[1] for b in blocks), min(b[2] for b in blocks))
    sch = Schematic.create(name)
    for x, y, z, st, nbt in blocks:
        lx, ly, lz = x - mn[0], y - mn[1], z - mn[2]
        sch.set_block(lx, ly, lz, st)
        if nbt:
            bid = st.split("[", 1)[0]
            sch.set_block_entity(lx, ly, lz, bid, nbt if isinstance(nbt, str) else snbt_of(nbt))
    return sch, (-mn[0], -mn[1], -mn[2])


def structure_snbt(blocks, data_version: int) -> Tuple[str, Pos]:
    """Block list -> vanilla structure-template SNBT (Name/Properties) and the shift applied.

    Nucleation's own `gametest_snbt` uses lower-case id/properties (its engine's flavour);
    the game's StructureTemplate.load wants Name/Properties and silently places nothing
    otherwise, so the oracle input is written here from the same normalized block list.
    """
    blocks = normalize_blocks(blocks)
    if not blocks:
        raise ValueError("empty block list")
    mn = (min(b[0] for b in blocks), min(b[1] for b in blocks), min(b[2] for b in blocks))
    mx = (max(b[0] for b in blocks), max(b[1] for b in blocks), max(b[2] for b in blocks))
    palette, rows = {}, []
    for x, y, z, st, nbt in sorted(blocks, key=lambda b: (b[1], b[2], b[0])):
        idx = palette.setdefault(st, len(palette))
        row = f"{{pos:[{x - mn[0]},{y - mn[1]},{z - mn[2]}],state:{idx}"
        if nbt:
            body = (nbt if isinstance(nbt, str) else snbt_of(nbt)).strip()
            row += f',nbt:{{id:"{st.split("[", 1)[0]}",' + body[1:]
        rows.append(row + "}")
    pal = []
    for st in palette:
        name, props = (st.split("[", 1) + [""])[:2]
        entry = f'{{Name:"{name}"'
        if props:
            kv = ",".join(f'{k}:"{v}"' for k, v in (p.split("=", 1) for p in props.rstrip("]").split(",")))
            entry += f",Properties:{{{kv}}}"
        pal.append(entry + "}")
    size = [mx[i] - mn[i] + 1 for i in range(3)]
    text = (f"{{DataVersion:{data_version},size:[{size[0]},{size[1]},{size[2]}],"
            f"palette:[{','.join(pal)}],blocks:[{','.join(rows)}],entities:[]}}")
    return text, (-mn[0], -mn[1], -mn[2])


def run_plan(blocks, plan: Plan, origin: Pos = DEFAULT_ORIGIN, shift_to_origin: bool = True) -> Trace:
    """Run blocks+plan in Nucleation's tick engine.

    `origin` is the world position of the structure's min corner (use the oracle's).
    Sim coordinates are relative to the schematic min corner; traces are returned in
    the caller's frame.
    """
    t0 = time.perf_counter()
    sch, shift = build_schematic(blocks)
    sim = TickSimulation.from_schematic(sch, _SETTLE[plan.settle], *origin, "")
    inv = lambda p: (p[0] - shift[0], p[1] - shift[1], p[2] - shift[2])    # sim -> caller
    fwd = lambda p: (p[0] + shift[0], p[1] + shift[1], p[2] + shift[2])    # caller -> sim

    def snap():
        return {inv(tuple(e["pos"])): canon_state(e["state"])
                for e in json.loads(sim.world_snapshot_json())}

    placed = snap()
    for _ in range(plan.preroll):
        sim.step()
    initial = snap() if plan.preroll else dict(placed)
    prev = initial
    by_tick = {}
    for o in plan.ops:
        by_tick.setdefault(o.tick, []).append(o)
    changes = {}
    air = "minecraft:air"
    for t in range(plan.ticks):
        for o in by_tick.get(t, ()):
            fx, fy, fz = fwd(o.pos)
            if o.op == "use":
                sim.use_block(fx, fy, fz)
            elif o.op == "set":
                sim.place_block(fx, fy, fz, o.block)
            else:
                raise ValueError(f"unknown op {o.op}")
        sim.step()
        cur = snap()
        diff = []
        for p in sorted(set(prev) | set(cur)):
            a, b = prev.get(p, air), cur.get(p, air)
            if a != b:
                diff.append((p, a, b))
        if diff:
            changes[t] = diff
        prev = cur
    return Trace(
        backend="nucleation", mc_version=f"nucleation-{nucleation.__version__}"
        if hasattr(nucleation, "__version__") else "nucleation",
        data_version=sch.canonical_data_version(), origin=tuple(origin), n_ticks=plan.ticks,
        placed=placed, initial=initial, changes=changes,
        wall_ms=(time.perf_counter() - t0) * 1000,
        meta={"settle": plan.settle, "audit": json.loads(TickSimulation.block_entity_audit_json(sch))})
