"""Env contract -> oracle replay, mirroring env/runner.py (read-only use of env/).

What env/runner.py does and this module copies:
* world: the build is compiled by hand-placement order (`env.sim.compile_cells`, `{simulate=true}`, bottom-up y,x,z), loaded
  InWorld and settled up to `budgets.rest`. That at-rest world is what env scores and what `runner.export_litematic` saves, so it is
  the world handed over (`handover_world`). The raw block list is NOT equivalent for memory circuits: placed literally (Placement
  settle, as a structure/Litematica paste does) bistable cells (locked repeaters, latches) can come up in another state.
* vectors: applied in the env's order (seeded shuffle with `eval_seed` for combinational tasks, authoring order for `seq`, public then
  hidden in ONE run), each input change followed by "step until quiescent (<= settle) + extra" ticks, read, next vector at once.
  `env_schedule` recomputes the order; the per-vector tick counts come from the env's own RunResult.
* sequences: each from the at-rest world, events at their tick, read index t = state after t ticks.
* position: env.sim.Sim runs the build with its min corner at engine (0,0,0); vanilla (and Nucleation) update order depends on
  absolute position, so an exact mirror places the oracle structure at the same absolute cells (`env_origin`; y=0 is inside the
  oracle's world). Timing-tight builds (read 2 ticks after quiescence) can pass at one position and fail at another.
Ops for tick T apply before tick T runs, and read index i is the state after tick i-1 (`Trace.state_at(i - 1)`), so a vector applied
at T and read after k ticks is checked at index T + k.
"""
import json
import random

from env.contract import box_cells
from env.sim import Sim, compile_cells, corner_of, parse
from tools.oracle.trace import Plan, put, use


def handover_world(contract, prepared):
    """-> [(x,y,z,state,nbt)] of the env's at-rest world (absolute coords), or raise ValueError if it never rests."""
    sc, corner, _ = compile_cells(prepared.world, prepared.nbt)
    sim = Sim(sc, corner)
    _, rest = sim.settle(contract.budgets.rest)
    if not rest:
        raise ValueError("build never comes to rest")
    snap = sim.snapshot()
    nbt = dict(prepared.nbt)
    baked = sim.bake()
    if snap:
        mn = tuple(min(p[i] for p in snap if not snap[p].endswith(":air")) for i in range(3))
        for e in json.loads(baked.get_all_block_entities_snbt_json()):
            x, y, z = e["position"]
            nbt[(x + mn[0], y + mn[1], z + mn[2])] = e["snbt"]
    return [(*p, s, nbt.get(p)) for p, s in sorted(snap.items()) if not s.startswith("minecraft:air")]


def env_origin(prepared, world):
    """Absolute oracle origin (world position of `world`'s min corner) that puts every cell where env's Sim has it."""
    corner, mn = corner_of(prepared.world), corner_of([tuple(b[:3]) for b in world])
    return tuple(mn[i] - corner[i] for i in range(3))


def raw_world(prepared):
    return [(*p, s, prepared.nbt.get(p)) for p, s in sorted(prepared.world.items())]


class Levels:
    """Builds ops from port-value assignments, mirroring env.runner.Driver. Lever levels start from `world`
    (the lever's `powered` state, as Driver.level reads it) or off."""

    def __init__(self, contract, world=None):
        self.ports = {p.name: p for p in contract.ports}
        st = {tuple(b[:3]): b[3] for b in (world or [])}
        self.lv = {p.name: int(parse(st.get(tuple(p.pos), ""))[1].get("powered") == "true")
                   for p in contract.inputs if p.kind != "pad" and p.pos}
        self.ops = []

    def set(self, tick, name, value):
        p, value = self.ports[name], int(value)
        if p.kind == "pad":
            if self.lv.get(name, 0) != value:
                self.ops.append(put(tick, p.pos, "redstone_block" if value else "air"))
        elif p.pulse:
            if value:
                self.ops.append(use(tick, p.pos))
            return
        elif self.lv.get(name, 0) != value:
            self.ops.append(use(tick, p.pos))
        self.lv[name] = value


def env_schedule(contract, seed=None):
    """(order, reset) of contract.vectors() as env.runner applies them with `seed` (None = eval_seed)."""
    seed = contract.eval_seed if seed is None else seed
    _budget, _extra, shuffle, reset, _oi = contract.vector_settings()
    order = list(range(len(contract.vectors())))
    if shuffle:
        random.Random(seed).shuffle(order)
    return order, reset


def captures(contract, hold=40, preroll=20, vec_ticks=None, order=None, reset=False, world=None, origin=None):
    """-> [(label, Plan, checks)], checks = [(read_index, port, expected, label[, Expect])]; expected may be init/!init.

    Vectors go in `order` (default authoring order) and vector i is held `vec_ticks[i]` ticks (default `hold` for all);
    `reset=True` makes every vector its own capture (env's reset_between). Every sequence is its own capture.
    `origin` = absolute position of the build's min corner in the oracle (None: oracle default)."""
    probes = [tuple(p.pos) for p in contract.outputs if p.pos]
    jobs = []
    vecs = contract.vectors()
    order = list(range(len(vecs))) if order is None else list(order)
    groups = [[i] for i in order] if reset else ([order] if vecs else [])
    for g in groups:
        lv, checks, t = Levels(contract, world), [], 0
        for i in g:
            v = vecs[i]
            for k, val in v.inputs.items():
                lv.set(t, k, val)
            t += hold if vec_ticks is None else vec_ticks[i]
            for k, e in v.expect.items():
                if e is not None:
                    checks.append((t, k, e, f"vec{i}:{v.label}"))
        jobs.append(("vectors" if not reset else f"vec{g[0]}", Plan(t, lv.ops, probes, "place", preroll, origin), checks))
    for s in contract.behaviour.sequences:
        lv, checks = Levels(contract, world), []
        for e in sorted(s.events, key=lambda e: e.t):
            for k, val in e.set.items():
                lv.set(e.t, k, val)
        for e in s.expect:
            for t in range(e.t1, e.t2 + 1) if e.mode == "all" else [None]:
                checks.append((t, e.port, e.value, f"{s.name}[{e.t1}-{e.t2},{e.mode}]", e))
        jobs.append((f"seq:{s.name}", Plan(s.duration + 1, lv.ops, probes, "place", preroll, origin), checks))
    return jobs


def env_captures(contract, run_result, preroll=20, world=None, seed=None, origin="env"):
    """Captures with the env's exact schedule: env order and the per-vector tick counts env used (`run_result` from
    env.runner.run with the same seed, not public_only). origin="env" puts the build at env's absolute cells (`env_origin`)."""
    order, reset = env_schedule(contract, seed)
    ticks = {r.index: r.ticks for r in run_result.vectors}
    if contract.vectors() and len(ticks) != len(contract.vectors()):
        raise ValueError("run_result has no tick count for every vector (public_only run?)")
    if origin == "env":
        origin = env_origin(run_result.prepared, world or raw_world(run_result.prepared))
    return captures(contract, preroll=preroll, vec_ticks=ticks, order=order, reset=reset, world=world, origin=origin)


def read_out(contract, state, pname):
    """Output port value from a {pos: state} map (same rules as runner.read_port)."""
    p = contract.port(pname)
    get = lambda pos: state.get(tuple(pos), "minecraft:air")
    if p.kind == "region":
        cells = [get(c) for c in box_cells(p.box)]
        air = lambda s: s.startswith("minecraft:air")
        if p.region_mode == "air":
            return int(all(air(c) for c in cells))
        if p.region_mode == "solid":
            return int(all(not air(c) for c in cells))
        if p.region_mode == "block_in":
            return int(all(parse(c)[0] in p.blocks for c in cells))
        return int(all(parse(c)[0] == p.block for c in cells))
    bid, props = parse(get(p.pos))
    name = bid.split(":")[-1]
    if p.kind == "block_prop":
        pp = p.prop or {}
        if pp.get("block") and bid != pp["block"]:
            return 0
        return int(props.get(pp.get("prop")) == str(pp.get("value")))
    return {"lamp": int(name == "redstone_lamp" and props.get("lit") == "true"),
            "wire_power": int(name == "redstone_wire" and int(props.get("power", "0")) >= p.min_power),
            "piston": int(name in ("piston", "sticky_piston") and props.get("extended") == "true"),
            "torch": int(name in ("redstone_torch", "redstone_wall_torch") and props.get("lit") == "true"),
            }.get(p.kind, -1)


def evaluate(contract, trace, checks):
    """Failed check labels. Handles 'init'/'!init' and mode=any windows."""
    outs = [p.name for p in contract.outputs]
    rd = lambda idx, port: read_out(contract, trace.state_at(idx - 1), port)
    init = {p: read_out(contract, trace.state_at(-1), p) for p in outs}
    res = lambda v, port: init[port] if v == "init" else 1 - init[port] if v == "!init" else v
    bad = []
    for chk in checks:
        t, port, val, label = chk[:4]
        want = res(val, port)
        if len(chk) == 5 and chk[4].mode == "any":
            e = chk[4]
            if any(rd(i, port) == want for i in range(e.t1, min(e.t2, trace.n_ticks) + 1)):
                continue
            bad.append(label)
        elif t <= trace.n_ticks and rd(t, port) != want:
            bad.append(f"{label}@{t}")
    return sorted(set(bad))
