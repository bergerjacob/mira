"""Run a flat block list against a Contract in the Nucleation simulator.

    res = run(contract, blocks, seed=0)          # RunResult (structured); env/score.py turns it into rewards/text

blocks: iterable of (x,y,z,state[,nbt]) tuples/lists or {"x","y","z","state","nbt"} dicts (the repo's flat
format) in absolute task coordinates. Everything is deterministic given (contract, blocks, seed).
"""
import hashlib
import random
from dataclasses import dataclass, field
from typing import Optional

from env import blocks as B
from env.contract import Contract, Port, box_cells, in_box
from env.sim import Sim, compile_cells, graph_reaches, parse, prop, simulatable, structural_graph

COMPONENT_IDS = {"redstone_wire", "repeater", "comparator", "redstone_torch", "redstone_wall_torch", "piston",
                 "sticky_piston", "observer", "redstone_lamp", "dropper", "dispenser", "hopper", "note_block"}


# --------------------------------------------------------------------------- build preparation (R0)
@dataclass
class Prepared:
    build: dict = field(default_factory=dict)      # pos -> canonical state (model's blocks)
    nbt: dict = field(default_factory=dict)
    fixed: dict = field(default_factory=dict)      # pos -> canonical state (task scenery/devices)
    world: dict = field(default_factory=dict)      # merged
    errors: list = field(default_factory=list)     # R0 failures
    warnings: list = field(default_factory=list)


def parse_blocks(blocks):
    """-> list[(pos, state, nbt)]; raises ValueError on malformed entries."""
    out = []
    for b in blocks:
        if isinstance(b, dict):
            out.append(((int(b["x"]), int(b["y"]), int(b["z"])), b["state"], b.get("nbt")))
        else:
            if len(b) < 4:
                raise ValueError(f"block entry needs x,y,z,state: {b!r}")
            out.append(((int(b[0]), int(b[1]), int(b[2])), b[3], b[4] if len(b) > 4 else None))
    return out


def fixed_cells(contract: Contract):
    cells = {}
    for f in contract.fixed:
        st = B.canonical(f["state"])
        for p in box_cells(f["box"]):
            cells[p] = st
    return cells


def prepare(contract: Contract, blocks) -> Prepared:
    pr = Prepared(fixed=fixed_cells(contract))
    try:
        entries = parse_blocks(blocks)
    except (ValueError, KeyError, TypeError) as e:
        pr.errors.append(f"malformed block list: {e}")
        return pr
    forb = set(contract.forbidden)
    bad = 0
    for pos, state, nbt in entries:
        try:
            st = B.canonical(state)
        except B.BlockError as e:
            bad += 1
            if bad <= 8:
                pr.errors.append(f"{list(pos)}: {e}")
            continue
        if B.is_air(st):
            pr.build.pop(pos, None)  # explicit air = remove
            continue
        if not in_box(pos, contract.build_box):
            pr.errors.append(f"{list(pos)} {B.short(B.split_state(st)[0])} is outside the build box {contract.build_box}")
            continue
        if any(in_box(pos, bx) for bx in contract.keep_clear + contract.no_write):
            pr.errors.append(f"{list(pos)} is in a reserved area (hallway/door cells) that must not be built in directly")
            continue
        if pos in pr.fixed:
            pr.errors.append(f"{list(pos)} is a fixed cell ({B.short(B.split_state(pr.fixed[pos])[0])}) and cannot be written")
            continue
        if pos in pr.build:
            pr.warnings.append(f"{list(pos)} written twice; last wins")
        pr.build[pos] = st
        if nbt:
            pr.nbt[pos] = nbt
    if bad > 8:
        pr.errors.append(f"... and {bad - 8} more invalid block states")
    unsim = sorted({st for st in pr.build.values() if not simulatable(st)})
    for st in unsim[:6]:
        pr.errors.append(f"{B.short(B.split_state(st)[0])} ({st.split('[', 1)[-1].rstrip(']') if '[' in st else 'default state'}) "
                         f"cannot be simulated by Nucleation (it refuses to load or place it); use another block/state")
    if len(pr.build) > contract.budgets.max_blocks:
        pr.errors.append(f"{len(pr.build)} blocks exceeds the budget of {contract.budgets.max_blocks}")
    pr.world = {p: s for p, s in {**pr.build, **pr.fixed}.items() if not B.is_air(s)}  # fixed air only reserves a cell
    return pr


# --------------------------------------------------------------------------- port IO
def read_port(sim: Sim, port: Port) -> int:
    k = port.kind
    if k == "region":
        cells = [sim.get(p) for p in box_cells(port.box)]
        if port.region_mode == "air":
            return int(all(B.is_air(c) for c in cells))
        if port.region_mode == "solid":
            return int(all(not B.is_air(c) for c in cells))
        if port.region_mode == "block_in":
            return int(all(parse(c)[0] in port.blocks for c in cells))
        return int(all(parse(c)[0] == port.block for c in cells))
    bid, props = parse(sim.get(port.pos))
    name = bid.split(":")[-1]
    if k == "lamp":
        return int(name == "redstone_lamp" and props.get("lit") == "true")
    if k == "wire_power":
        return int(name == "redstone_wire" and int(props.get("power", "0")) >= port.min_power)
    if k == "piston":
        return int(name in ("piston", "sticky_piston") and props.get("extended") == "true")
    if k == "torch":
        return int(name in ("redstone_torch", "redstone_wall_torch") and props.get("lit") == "true")
    if k == "block_prop":
        p = port.prop or {}
        if p.get("block") and bid != p["block"]:
            return 0
        return int(props.get(p.get("prop")) == str(p.get("value")))
    raise ValueError(f"unknown output kind {k}")


class Driver:
    """Applies input-port values to a Sim, remembering pad state."""

    def __init__(self, sim: Sim, contract: Contract):
        self.sim, self.contract = sim, contract
        self.pads = {}

    def reset_state(self):
        self.pads = {}

    def level(self, port: Port) -> int:
        if port.kind == "pad":
            return self.pads.get(port.name, 0)
        return int(prop(self.sim.get(port.pos), "powered") == "true")

    def set(self, port: Port, value: int):
        value = int(value)
        if port.kind == "pad":
            if self.pads.get(port.name, 0) != value:
                self.sim.place(port.pos, "minecraft:redstone_block" if value else "minecraft:air")
                self.pads[port.name] = value
        elif port.pulse:
            if value:
                self.sim.use(port.pos)
        elif self.level(port) != value:
            self.sim.use(port.pos)


# --------------------------------------------------------------------------- results
@dataclass
class VectorResult:
    index: int
    label: str
    public: bool
    inputs: dict
    expect: dict
    got: dict
    ok: bool
    settled: bool
    ticks: int
    diverge: Optional[dict] = None          # {"port","tick","expected","got"}
    trace: dict = field(default_factory=dict)   # port -> RLE string "0@0 1@4"


@dataclass
class WindowResult:
    port: str
    value: int
    t1: int
    t2: int
    mode: str
    ok: bool
    first_bad: Optional[int] = None


@dataclass
class SequenceResult:
    name: str
    public: bool
    ok: bool
    windows: list
    trace: dict = field(default_factory=dict)


@dataclass
class RunResult:
    contract_id: str
    prepared: Prepared
    compiled: bool = False                      # R0
    place_errors: list = field(default_factory=list)
    invariants: dict = field(default_factory=dict)   # name -> {"ok": bool, "detail": str}
    vectors: list = field(default_factory=list)
    sequences: list = field(default_factory=list)
    connectivity: dict = field(default_factory=dict)  # (input, output) -> {"reached": bool, "last": pos}
    silent: list = field(default_factory=list)        # [(pos, state)] components that never changed
    ticks: int = 0
    fingerprint: str = ""
    public_only: bool = False

    def invariant_failures(self):
        return {k: v for k, v in self.invariants.items() if not v["ok"]}


def rle(vals):
    out, last = [], None
    for t, v in enumerate(vals):
        if v != last:
            out.append(f"{v}@{t}")
            last = v
    return " ".join(out)


# --------------------------------------------------------------------------- the run
def run(contract: Contract, blocks, seed: Optional[int] = None, public_only: bool = False,
        fast: bool = False) -> RunResult:
    """Full evaluation. fast=True skips the second deterministic pass (RL inner loops / mutant sweeps keep
    all invariants except `deterministic`). Never raises on a bad build: simulator failures become R0 errors."""
    pr = blocks if isinstance(blocks, Prepared) else prepare(contract, blocks)
    res = RunResult(contract.id, pr, public_only=public_only)
    try:
        return _run(contract, pr, res, seed, public_only, fast)
    except Exception as e:  # engine panics, tick budget, ...
        res.compiled = False
        pr.errors.append(f"evaluation aborted: {type(e).__name__}: {e}")
        return res


def _run(contract, pr, res, seed, public_only, fast) -> RunResult:
    inv = contract.invariants
    seed = contract.eval_seed if seed is None else seed

    def note(name, ok, detail=""):
        res.invariants[name] = {"ok": bool(ok), "detail": detail}

    if pr.errors:
        return res
    # static structural checks
    forb = [(p, s) for p, s in pr.world.items() if B.split_state(s)[0] in contract.forbidden]
    note("no_forbidden", not forb or not inv.no_forbidden,
         "; ".join(f"{B.short(B.split_state(s)[0])} at {list(p)}" for p, s in forb[:5]))
    fl = list(B.floating(pr.world))
    note("no_popoff", not fl or not inv.no_popoff,
         "; ".join(f"{r} at {list(p)} ({B.short(B.split_state(s)[0])})" for p, s, r in fl[:6]))

    sc, origin, perr = compile_cells(pr.world, pr.nbt)
    res.place_errors = perr
    try:
        sim = Sim(sc, origin, "inworld")
    except Exception as e:
        pr.errors.append(f"simulator refused to load the build: {e}")
        return res
    res.compiled = True
    graph = structural_graph(sc, origin)
    sim.set_seed(seed)
    sim.max_steps = contract.budgets.total

    # at rest: settle the freshly placed world; baseline = state after settling
    t_rest, rest_ok = sim.settle(contract.budgets.rest)
    note("at_rest", rest_ok or not inv.at_rest,
         "" if rest_ok else f"still changing after {t_rest} ticks (clock/oscillator?)")
    base_world = sim.snapshot() if rest_ok else None
    changed_cells = set()   # every pos that changed in any run (silent-component detection, collateral)
    change_log = []

    def collect():
        for t, p, a, b in sim.changes():
            changed_cells.add(p)
            change_log.append((p, a, b))
        sim.clear_changes()

    collect()
    sim.checkpoint()
    # paste-stable: the saved at-rest world must not change when loaded InWorld or settled as Placement
    if rest_ok and inv.paste_stable:
        raw, origin2, _ = compile_cells(base_world, pr.nbt, engine_place=False)
        details = []
        for mode in ("inworld", "placement"):
            try:
                s2 = Sim(raw, origin2, mode)
                _, ok2 = s2.settle(contract.budgets.rest)
                snap = s2.snapshot()
            except Exception as e:
                details.append(f"{mode}: sim error {e}")
                continue
            if not ok2:
                details.append(f"{mode}: never quiescent")
            diff = [p for p in set(snap) | set(base_world) if snap.get(p) != base_world.get(p)]
            if diff:
                p = sorted(diff)[0]
                details.append(f"{mode}: {len(diff)} cells differ, e.g. {list(p)} saved {B.split_state(base_world.get(p, 'minecraft:air'))[0].split(':')[-1]}"
                               f" -> {B.split_state(snap.get(p, 'minecraft:air'))[0].split(':')[-1]} {snap.get(p, '')}")
        note("paste_stable", not details, "; ".join(details))
    elif inv.paste_stable:
        note("paste_stable", False, "skipped: build is not at rest")

    # ---- behaviour
    ports = {p.name: p for p in contract.ports}
    outs = contract.outputs
    ins = contract.inputs
    driver = Driver(sim, contract)
    rng = random.Random(seed)
    unsettled = []
    fp = hashlib.sha1()

    def read_all():
        return {p.name: read_port(sim, p) for p in outs}

    clear_cells = [c for bx in contract.keep_clear for c in box_cells(bx)] if inv.keep_clear else []
    clear_bad = []

    def check_clear():
        if clear_cells and len(clear_bad) < 4:
            for c in clear_cells:
                if not B.is_air(sim.get(c)):
                    clear_bad.append((sim.tick, c, sim.get(c)))
                    break

    init_out = read_all()  # at-rest outputs: reference for "init" / "!init" expectations

    def resolve(v, port):
        return init_out[port] if v == "init" else 1 - init_out[port] if v == "!init" else v

    def run_vector(i, v, budget, extra):
        for k, val in v.inputs.items():
            driver.set(ports[k], val)
        traces = {p.name: [] for p in outs}
        cur = read_all()
        for k in traces:
            traces[k].append(cur[k])

        def tick_cb(_n):
            c = read_all()
            for k in traces:
                traces[k].append(c[k])
        n, settled = sim.settle(budget, tick_cb)
        for _ in range(extra):
            sim.step()
            tick_cb(0)
        got = read_all()
        check_clear()
        ok = True
        div = None
        for k, e in v.expect.items():
            if e is None:
                continue
            if got[k] != e:
                ok = False
                if div is None:
                    wrong, tr = got[k], traces[k]
                    t = len(tr) - 1
                    while t > 0 and tr[t - 1] == wrong:
                        t -= 1
                    div = {"port": k, "tick": t, "expected": e, "got": got[k]}
        in_tr = {k: f"{val}@0" for k, val in v.inputs.items()}
        return VectorResult(i, v.label, v.public, dict(v.inputs), dict(v.expect), got, ok and settled, settled, n + extra,
                            div, {**{f"in:{k}": s for k, s in in_tr.items()}, **{k: rle(t) for k, t in traces.items()}})

    def vector_pass(order, vecs, budget, extra, reset):
        results = {}
        for i in order:
            if reset:
                sim.restore()
                driver.reset_state()
            r = run_vector(i, vecs[i], budget, extra)
            results[i] = r
            if not r.settled:
                unsettled.append(r)
            fp.update(f"v{i}:{sorted(r.got.items())}:{r.ticks}".encode())
        collect()
        return results

    all_vecs = contract.vectors()
    vecs = [v for v in all_vecs if v.public or not public_only]
    if vecs:
        budget, extra, shuffle, reset, order_indep = contract.vector_settings()
        order = list(range(len(vecs)))
        if shuffle:
            rng.shuffle(order)
        first = vector_pass(order, vecs, budget, extra, reset)
        res.vectors = [first[i] for i in sorted(first)]
        if order_indep and shuffle and len(vecs) > 1 and all(r.settled for r in res.vectors):
            sim.restore()
            driver.reset_state()
            order2 = list(range(len(vecs)))
            rng.shuffle(order2)
            second = vector_pass(order2, vecs, budget, extra, reset)
            diff = [i for i in first if first[i].got != second[i].got]
            note("order_independent", not diff,
                 "" if not diff else "vectors " + ",".join(str(i) for i in diff[:6]) + " give different outputs in a second random order (leftover state?)")

    for s in contract.behaviour.sequences:
        if public_only and not s.public:
            continue
        sim.restore()
        driver.reset_state()
        evs = sorted(s.events, key=lambda e: e.t)
        traces = {p.name: [] for p in outs}
        ei = 0
        for t in range(s.duration + 1):
            while ei < len(evs) and evs[ei].t <= t:
                for k, val in evs[ei].set.items():
                    driver.set(ports[k], val)
                ei += 1
            cur = read_all()
            check_clear()
            for k in traces:
                traces[k].append(cur[k])
            if t < s.duration:
                sim.step()
        wins = []
        for e in s.expect:
            seg = traces[e.port][e.t1:e.t2 + 1]
            want = resolve(e.value, e.port)
            if e.mode == "any":
                ok = any(x == want for x in seg)
                bad = None if ok else e.t1
            else:
                badl = [e.t1 + j for j, x in enumerate(seg) if x != want]
                ok = not badl
                bad = badl[0] if badl else None
            wins.append(WindowResult(e.port, want, e.t1, e.t2, e.mode, ok, bad))
        res.sequences.append(SequenceResult(s.name, s.public, all(w.ok for w in wins), wins, {k: rle(t) for k, t in traces.items()}))
        fp.update(f"s{s.name}:{sorted((k, rle(t)) for k, t in traces.items())}".encode())
        collect()

    note("no_oscillation", not unsettled or not inv.no_oscillation,
         "; ".join(f"vector {r.index} did not settle in {r.ticks} ticks" for r in unsettled[:4]))

    if clear_cells:
        note("keep_clear", not clear_bad, "; ".join(f"{list(c)} became {B.short(B.split_state(st)[0])} at tick {t}" for t, c, st in clear_bad))

    # ---- collateral damage
    if inv.no_collateral:
        allowed = [contract.bbox] + list(inv.change_boxes)
        bad = [(p, a, b) for p, a, b in change_log if not any(in_box(p, bx) for bx in allowed)]
        pads = {pt.pos for pt in ins if pt.kind == "pad"}
        for p, a, b in change_log:
            if p in pr.fixed and p not in pads and parse(a)[0] != parse(b)[0] and parse(b)[0] != parse(pr.fixed[p])[0]:
                # fixed device replaced by something else (e.g. pushed by a piston); moving_piston states are transient
                if "moving_piston" not in b and parse(b)[0] != "minecraft:air":
                    bad.append((p, a, b))
        note("no_collateral", not bad,
             "; ".join(f"{list(p)}: {parse(a)[0].split(':')[-1]} -> {parse(b)[0].split(':')[-1]}" for p, a, b in bad[:4]))

    # ---- connectivity probes (R2): flip each input in several contexts of the other inputs and see how far it reaches
    dep = contract.dependencies()
    need_in = sorted({i for v in dep.values() for i in v})
    reach = {}
    all_in = [p.name for p in ins]
    for iname in need_in:
        others = [n for n in all_in if n != iname]
        contexts = [{}, {n: 1 for n in others}] + [{n: 1} for n in others if len(others) > 1]
        cells = set()
        for ctx in contexts:
            sim.restore()
            driver.reset_state()
            for n, val in ctx.items():
                driver.set(ports[n], val)
            sim.settle(contract.budgets.rest)
            collect()
            driver.set(ports[iname], 1)
            sim.settle(contract.budgets.rest)
            sim.step(2)
            cells |= {p for _, p, a, b in sim.changes() if p != ports[iname].pos}
            collect()
        reach[iname] = cells
    for o, ilist in dep.items():
        op = ports[o]
        cells = set(box_cells(op.box)) if op.kind == "region" else {op.pos}
        near = set(cells)
        for c in cells:
            for d in B.DIRS.values():
                near.add((c[0] + d[0], c[1] + d[1], c[2] + d[2]))
        for iname in ilist:
            hit = reach.get(iname, set()) & near
            path = graph_reaches(graph, ports[iname].pos, op.pos) if graph and op.kind != "region" else None
            res.connectivity[(iname, o)] = {"reached": bool(hit) or bool(path), "direct": bool(reach.get(iname, set()) & cells),
                                            "graph_path": path}
    res.silent = sorted((p, s) for p, s in pr.build.items() if B.short(B.split_state(s)[0]) in COMPONENT_IDS and p not in changed_cells)
    res.ticks = sim.tick

    # ---- determinism: rebuild from scratch and replay the same run
    if inv.deterministic and not fast:
        res2 = run(contract, pr, seed=seed, public_only=public_only, fast=True)
        same = res2.fingerprint == fp.hexdigest()
        note("deterministic", same, "" if same else "two identical runs produced different traces")
    res.fingerprint = fp.hexdigest()
    return res


def export_litematic(contract: Contract, blocks, path) -> int:
    """Compile -> place through the engine -> settle to rest -> save the at-rest .litematic (min corner = file origin).
    Returns the number of blocks written. Raises ValueError for builds that do not compile or never come to rest."""
    pr = blocks if isinstance(blocks, Prepared) else prepare(contract, blocks)
    if pr.errors:
        raise ValueError("build does not compile: " + "; ".join(pr.errors))
    sc, corner, _ = compile_cells(pr.world, pr.nbt)
    sim = Sim(sc, corner)
    _, rest = sim.settle(contract.budgets.rest)
    if not rest:
        raise ValueError("build never comes to rest; refusing to save an unsettled world")
    out = sim.bake()
    out.save_to_file(str(path))
    return len(sim.snapshot())
