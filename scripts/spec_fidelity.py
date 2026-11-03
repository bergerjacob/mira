"""Spec-fidelity harness (Experiment 05): does a build that passed ITS OWN spec pass an INDEPENDENT hidden spec?

Hidden specs for three plain-English requests (flush 2x2 piston door toggled by a button; 4-bit button counter with reset;
4-bit ALU with add/and/or/xor and a carry lamp) are written HERE from the request alone and bound to a build only by port NAME
and POSITION (contract v2 `Port` objects read from the agent's spec.json `ports`; nothing else of that spec is used). They never
go into a builder prompt. The same spec runs on any exported hand-over world that carries those labelled ports:

    .venv/bin/python scripts/spec_fidelity.py list
    .venv/bin/python scripts/spec_fidelity.py run door --backend env                     # Nucleation through env.sim (canonical placement)
    .venv/bin/python scripts/spec_fidelity.py run counter --backend vanilla --variant rot   # vanilla 26.2, rotated/mirrored + other origin
    .venv/bin/python scripts/spec_fidelity.py run alu --backend env --own                   # also replay the agent's own spec

Mechanics (see docs/EXPERIMENT_05.md):
* world = the exported .litematic (`include_fixed`, at-rest, baked) at its task coordinates; ports from the agent's spec.json.
* variants: `orig` (rot 0, the build's own cells), `origin` (vanilla only: random absolute origin), `rot` (random non-identity
  rotation/mirror from `env.variants.Xf`, world AND ports AND geometry transformed, random absolute origin in vanilla).
* every scenario = timed port events + checks evaluated on a per-tick world trace. The env side is `env.sim.Sim` (canonical
  Placement load of the handover world, `env.runner.Driver` semantics: lever level / button press) and the vanilla side is
  `tools.oracle.gametest` (one JVM per capture, `isolated=True`), both producing the same `Trace`, evaluated by the same code.
* check tiers: `core` = a reasonable user of the request expects it; `stress` = plausible but faster/rougher use (reported separately);
  `info` = assumption of this spec that a user may not share (never counted as a failure of the build).
"""
import argparse
import json
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from env.contract import Port  # noqa: E402
from env.variants import ELEMENTS, Xf, _port  # noqa: E402
from tools.oracle.trace import Plan, canon_state, use  # noqa: E402

OUT = ROOT / "data/fidelity"
AIR = "minecraft:air"

PROJECTS = {
    "door": {"dir": "/tmp/freeform_tff_door", "litematic": "tff_door.litematic",
             "request": "2x2 piston door that opens and closes each time I press a button; flush in a wall when closed"},
    "counter": {"dir": "/tmp/freeform_counter", "litematic": ".mira/exports/counter.litematic",
                "request": "A 4-bit binary counter that goes up by one every time I press a button, shown on four lamps, "
                           "with a second button that resets it to zero."},
    "alu": {"dir": "/tmp/freeform_alu", "litematic": ".mira/exports/alu.litematic",
            "request": "A small 4-bit calculator: two 4-bit inputs set with levers, a lever pair that chooses add / and / or / xor, "
                       "and the result on lamps with a carry lamp."},
}
DIRV = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0), "up": (0, 1, 0), "down": (0, -1, 0)}


# ------------------------------------------------------------------------------------------------ world / ports / transforms
@dataclass
class World:
    cells: dict                                   # pos -> canonical state (non-air), task coordinates
    nbt: dict = field(default_factory=dict)
    ports: dict = field(default_factory=dict)     # name -> env.contract.Port

    def blocks(self):
        return [(*p, s, self.nbt.get(p)) for p, s in sorted(self.cells.items())]

    def bbox(self):
        ks = list(self.cells)
        return [min(k[i] for k in ks) for i in range(3)] + [max(k[i] for k in ks) for i in range(3)]


def load_world(name: str) -> World:
    """The exported hand-over litematic at task coordinates (`litematic_info.origin` of the last export) + ports (name/kind/pos only
    read from the agent's spec.json: positions, never behaviour)."""
    from tools.oracle.gametest import load_build
    pj = PROJECTS[name]
    d = Path(pj["dir"])
    exp = json.loads(next(d.glob(".mira/exports/last_export.json")).read_text())
    o = exp["litematic_info"]["origin"]
    blocks, _dv = load_build(d / pj["litematic"])
    cells, nbt = {}, {}
    for x, y, z, st, n in blocks:
        p = (x + o[0], y + o[1], z + o[2])
        cells[p] = canon_state(st)
        if n:
            nbt[p] = n
    spec = json.loads((d / "spec.json").read_text())
    ports = {}
    for pd in spec["ports"]:
        keep = {k: v for k, v in pd.items() if k in ("name", "kind", "pos", "facing", "pulse", "box", "region_mode", "block", "blocks")}
        ports[pd["name"]] = Port.from_dict(keep)
    return World(cells, nbt, ports)


def transform(w: World, rot: int, mirror: Optional[str]) -> World:
    xf = Xf(w.bbox(), rot, mirror, (0, 0, 0))
    cache = {}

    def st(s):
        if s not in cache:
            cache[s] = canon_state(xf.state(s))
        return cache[s]
    return World({xf.point(p): st(s) for p, s in w.cells.items()}, {xf.point(p): v for p, v in w.nbt.items()},
                 {n: _port(xf, p) for n, p in w.ports.items()})


def variant_params(name: str, variant: str):
    """-> (rot, mirror, origin or None). Deterministic per (project, variant); `rot` never the identity."""
    rng = random.Random(f"fidelity-{name}-{variant}")
    origin = None
    if variant != "orig":
        origin = (rng.randint(-3000, 3000), rng.randint(30, 90), rng.randint(-3000, 3000))
    rot, mirror = 0, None
    if variant == "rot":
        rot, mirror = rng.choice([e for e in ELEMENTS if e != (0, None)])
    return rot, mirror, origin


# ------------------------------------------------------------------------------------------------ predicates / trace context
def st_id(s):
    return s.split("[", 1)[0]


def st_props(s):
    if "[" not in s:
        return {}
    return dict(kv.split("=", 1) for kv in s.split("[", 1)[1].rstrip("]").split(",") if kv)


@dataclass
class Pred:
    fn: Callable          # cur: dict pos -> state (non-air) -> int
    cells: Optional[set]  # footprint (None = global: re-evaluated every tick)


def lamp_pred(pos):
    pos = tuple(pos)
    return Pred(lambda cur: int(cur.get(pos, AIR).startswith("minecraft:redstone_lamp[") and "lit=true" in cur.get(pos, AIR)), {pos})


def powered_pred(pos):
    pos = tuple(pos)
    return Pred(lambda cur: int("powered=true" in cur.get(pos, AIR)), {pos})


def box_cells(b):
    return [(x, y, z) for x in range(b[0], b[3] + 1) for y in range(b[1], b[4] + 1) for z in range(b[2], b[5] + 1)]


def shift_box(b, v, k):
    return [b[0] + v[0] * k, b[1] + v[1] * k, b[2] + v[2] * k, b[3] + v[0] * k, b[4] + v[1] * k, b[5] + v[2] * k]


def region_pred(cells, test):
    cells = [tuple(c) for c in cells]
    return Pred(lambda cur: int(all(test(cur.get(c, AIR)) for c in cells)), set(cells))


def is_air(s):
    return s.startswith("minecraft:air") or s.startswith("minecraft:cave_air") or s.startswith("minecraft:void_air")


PISTONY = ("piston", "sticky_piston", "piston_head", "moving_piston")
NOT_PLAIN = ("redstone", "button", "lever", "torch", "repeater", "comparator", "slab", "stairs", "wall", "fence", "door", "plate",
             "pane", "carpet", "rail", "observer", "dispenser", "dropper", "hopper", "lamp", "glass", "slime", "honey", "head", "wire",
             "chest", "sign", "banner", "bed", "cake", "flower", "sapling", "pot", "ladder", "vine", "tripwire", "bell", "chain")


def plain_full(s):
    """A plain full cube (stone, planks, ...): not air, not piston-family, not a non-full / device block."""
    i = st_id(s).split(":")[-1]
    return not is_air(s) and i not in PISTONY and not any(t in i for t in NOT_PLAIN)


def piston_consistency(cur):
    """1 iff no moving_piston anywhere, every extended piston has its head in front, every head has an extended base behind it."""
    for p, s in cur.items():
        i = st_id(s)
        if i == "minecraft:moving_piston":
            return 0
        if i in ("minecraft:piston", "minecraft:sticky_piston"):
            pr = st_props(s)
            if pr.get("extended") == "true":
                v = DIRV[pr["facing"]]
                h = cur.get((p[0] + v[0], p[1] + v[1], p[2] + v[2]), AIR)
                if st_id(h) != "minecraft:piston_head" or st_props(h).get("facing") != pr["facing"]:
                    return 0
        elif i == "minecraft:piston_head":
            pr = st_props(s)
            v = DIRV[pr["facing"]]
            b = cur.get((p[0] - v[0], p[1] - v[1], p[2] - v[2]), AIR)
            if st_id(b) not in ("minecraft:piston", "minecraft:sticky_piston") or st_props(b).get("extended") != "true":
                return 0
    return 1


class Ctx:
    """Per-scenario evaluation context over a Trace: predicate series indexed by tick (index i = state after i ticks)."""

    def __init__(self, trace, preds, uses, in_ports, meta):
        self.n = trace.n_ticks
        self.meta = meta
        cur = dict(trace.initial)
        last, series = {}, {}
        for u in uses:
            v = preds[u].fn(cur)
            last[u] = v
            series[u] = [v]
        for t in range(self.n):
            ch = trace.changes.get(t, ())
            if ch:
                touched = set()
                for pos, _was, now in ch:
                    touched.add(pos)
                    if is_air(now):
                        cur.pop(pos, None)
                    else:
                        cur[pos] = now
                for u in uses:
                    p = preds[u]
                    if p.cells is None or not p.cells.isdisjoint(touched):
                        last[u] = p.fn(cur)
            for u in uses:
                series[u].append(last[u])
        self.s = series
        self.final = cur if len(cur) < 3000 else None
        self.in_ports = in_ports

    def edges(self, port):
        """Indices i where the (button) input `port` became powered (state after i ticks)."""
        s = self.s["in:" + port]
        return [i for i in range(1, len(s)) if s[i] and not s[i - 1]]

    def win(self, pred, a, b):
        return self.s[pred][max(0, a):min(self.n, b) + 1]

    def all_eq(self, pred, a, b, want):
        """-> (ok, first_bad_index, got)"""
        seg = self.s[pred][max(0, a):min(self.n, b) + 1]
        for k, v in enumerate(seg):
            if v != want:
                return False, max(0, a) + k, v
        return True, None, None


@dataclass
class Chk:
    id: str
    cls: str
    tier: str
    fn: Callable          # Ctx -> (ok, detail)


@dataclass
class Scn:
    name: str
    events: list          # (tick, port, value)
    duration: int
    uses: list
    checks: list
    meta: dict = field(default_factory=dict)


@dataclass
class Spec:
    name: str
    preds: dict
    scns: list
    in_ports: dict        # name -> Port (inputs)
    doc: str = ""


def levels_to_ops(world: World, events, in_ports):
    """events [(t, port, value)] -> oracle ops mirroring env.runner.Driver: a lever is clicked only when its level differs, a
    pulse button is clicked for value 1."""
    lv = {n: int("powered=true" in world.cells.get(tuple(p.pos), "")) for n, p in in_ports.items() if not p.pulse}
    ops = []
    for t, n, v in sorted(events, key=lambda e: e[0]):
        p = in_ports[n]
        if p.pulse:
            if v:
                ops.append(use(t, p.pos))
        elif lv[n] != int(v):
            ops.append(use(t, p.pos))
            lv[n] = int(v)
    return ops


def plan_of(world: World, scn: Scn, in_ports, origin=None, preroll=20) -> Plan:
    return Plan(scn.duration + 1, levels_to_ops(world, scn.events, in_ports), [], "place", preroll, origin)


# ------------------------------------------------------------------------------------------------ backends
class EnvBackend:
    """Nucleation through env.sim: the handover world is compiled exactly as env.runner does (canonical placement: raw states, dust a
    cross, Placement load), settled to rest (<= 400 ticks) and checkpointed; each scenario restores that checkpoint."""

    def __init__(self, world: World):
        from env.sim import Sim, compile_cells
        sc, corner, errs = compile_cells(world.cells, world.nbt)
        self.place_errors = errs
        self.sim = Sim(sc, corner)
        self.sim.set_seed(0)
        self.rest_ticks, self.at_rest = self.sim.settle(400)
        self.sim.clear_changes()
        self.base = {p: canon_state(s) for p, s in self.sim.snapshot().items() if not is_air(s)}
        self.sim.checkpoint()

    def run(self, plan: Plan):
        from tools.oracle.trace import Trace
        sim = self.sim
        sim.restore()
        cur = dict(self.base)
        by_t = {}
        for o in plan.ops:
            by_t.setdefault(o.tick, []).append(o)
        changes = {}
        for t in range(plan.ticks):
            for o in by_t.get(t, ()):
                if o.op == "use":
                    sim.use(o.pos)
                else:
                    sim.place(o.pos, o.block)
            sim.step()
            last = {}
            for _tick, pos, _was, now in sim.changes():
                last[pos] = canon_state(now)
            sim.clear_changes()
            diff = []
            for pos, now in last.items():
                before = cur.get(pos, AIR)
                after = AIR if is_air(now) else now
                if before != after:
                    diff.append((pos, before, after))
                    if after == AIR:
                        cur.pop(pos, None)
                    else:
                        cur[pos] = after
            if diff:
                changes[t] = diff
        return Trace("env-nucleation", "nucleation", 0, (0, 0, 0), plan.ticks, dict(self.base), dict(self.base), changes)


def run_vanilla(world: World, jobs, workers=4, timeout=None):
    """jobs [(name, Plan)] -> traces (one fresh JVM per capture)."""
    from tools.oracle import gametest
    blocks = world.blocks()
    return gametest.capture_many([(n, blocks, p) for n, p in jobs], workers=workers, isolated=True, timeout=timeout)


# ------------------------------------------------------------------------------------------------ evaluation
def rle(vals):
    out, last = [], None
    for t, v in enumerate(vals):
        if v != last:
            out.append(f"{v}@{t}")
            last = v
    return " ".join(out)


def evaluate(spec: Spec, scn: Scn, trace):
    """-> (check rows, {pred: RLE of its per-tick series}); the RLE is the comparable fingerprint of a run (env vs vanilla)."""
    uses = sorted(set(scn.uses) | {"in:" + n for n in spec.in_ports})
    ctx = Ctx(trace, spec.preds, uses, spec.in_ports, scn.meta)
    out = []
    for c in scn.checks:
        try:
            ok, detail = c.fn(ctx)
        except Exception as e:  # a broken check must never read as a pass
            ok, detail = False, f"check error: {type(e).__name__}: {e}"
        out.append({"scn": scn.name, "id": c.id, "cls": c.cls, "tier": c.tier, "ok": bool(ok), "detail": detail})
    return out, {u: rle(ctx.s[u]) for u in uses}


# ================================================================================================ DOOR
def door_spec(w: World, sweep=()) -> Spec:
    P = w.ports
    box = list(P["CLOSED"].box)                       # the doorway (position bound by label)
    btn = P["BTN"]
    f = DIRV[btn.facing]                              # button on the FRONT face, facing outward
    cells = box_cells(box)
    wall_id = st_id(w.cells.get((box[0], box[4] + 1, box[2]), "minecraft:stone"))      # the wall block above the doorway
    front = [c for k in (1, 2, 3) for c in box_cells(shift_box(box, f, k))]
    back1 = box_cells(shift_box(box, f, -1))
    passage = [c for k in (-2, -1, 1, 2) for c in box_cells(shift_box(box, f, k))]
    preds = {
        "FULL": region_pred(cells, plain_full),
        "WALLMAT": region_pred(cells, lambda s: st_id(s) == wall_id),
        "AIR": region_pred(cells, is_air),
        "FRONT": region_pred(front, is_air),
        "BACK1": region_pred(back1, is_air),
        "PASSAGE": region_pred(passage, is_air),
        "PISTONS": Pred(piston_consistency, None),
        "in:BTN": powered_pred(btn.pos),
    }
    DL = 40       # a door that is still moving 2 s after the press is slower than a person expects

    def windows(ctx, dl=DL):
        """Quiet windows [(a, b, presses_so_far)] between accepted presses (rising edges of the button, read from the world)."""
        E = ctx.edges("BTN")
        out = [(0, (E[0] - 1) if E else ctx.n, 0)]
        for k, e in enumerate(E):
            nxt = E[k + 1] if k + 1 < len(E) else ctx.n + 1
            gap = nxt - e
            out.append((e + min(dl, gap - 2), nxt - 1, k + 1))
        return [(a, b, j) for a, b, j in out if a <= b]

    def c0_closed(ctx):
        return 1 if ctx.s["FULL"][0] or not ctx.s["AIR"][0] else 0

    def states(ctx, dl=DL):
        """-> (closed windows, open windows) from the initial state and the number of accepted presses so far."""
        c0 = c0_closed(ctx)
        closed, opn = [], []
        for a, b, j in windows(ctx, dl):
            (closed if (c0 == 1) == (j % 2 == 0) else opn).append((a, b))
        return closed, opn

    def chk_parity(ctx):
        closed, opn = states(ctx)
        n = len(ctx.edges("BTN"))
        for a, b in closed:
            ok, t, _ = ctx.all_eq("FULL", a, b, 1)
            if not ok:
                return False, f"expected CLOSED (4 plain full blocks in the doorway) in [{a},{b}] but not at tick {t} (after {n} accepted presses in all)"
        for a, b in opn:
            ok, t, _ = ctx.all_eq("AIR", a, b, 1)
            if not ok:
                return False, f"expected OPEN (4 air cells) in [{a},{b}] but not at tick {t}"
        return True, f"{n} accepted presses, {len(closed) + len(opn)} quiet windows"

    def chk_win(pred, which, label):
        def f_(ctx):
            closed, opn = states(ctx)
            for a, b in (closed if which == "closed" else opn):
                ok, t, _ = ctx.all_eq(pred, a, b, 1)
                if not ok:
                    return False, f"{label} violated in {which} window [{a},{b}] at tick {t}"
            return True, ""
        return f_

    def chk_pistons_end(ctx):
        closed, opn = states(ctx)
        a, b = (closed + opn)[-1] if closed + opn else (0, 0)
        ok, t, _ = ctx.all_eq("PISTONS", a, b, 1)
        return ok, "" if ok else f"stuck/inconsistent piston or moving_piston at rest at tick {t}"

    def chk_init(ctx):
        a, o = ctx.s["FULL"][0], ctx.s["AIR"][0]
        return bool(a) != bool(o), f"initial FULL={a} AIR={o}"

    def chk_starts_closed(ctx):
        return ctx.s["FULL"][0] == 1, f"initial FULL={ctx.s['FULL'][0]} AIR={ctx.s['AIR'][0]}"

    def chk_front_always(ctx):
        ok, t, _ = ctx.all_eq("FRONT", 0, ctx.n, 1)
        return ok, "" if ok else f"something enters the 3 cells in front of the doorway at tick {t}"

    def chk_stable_end(ctx):
        """After the scenario's last press the door is in exactly one of {closed, open} and stays for >= 100 ticks."""
        n = ctx.n
        a = n - 100
        ok1, t1, _ = ctx.all_eq("FULL", a, n, 1)
        ok2, t2, _ = ctx.all_eq("AIR", a, n, 1)
        return ok1 or ok2, "" if ok1 or ok2 else "last 100 ticks: neither 4 full blocks nor 4 air cells"

    def chk_toggled_once(ctx):
        E = ctx.edges("BTN")
        c0 = c0_closed(ctx)
        want_closed = (c0 == 1) == (len(E) % 2 == 0)
        key = "FULL" if want_closed else "AIR"
        ok = ctx.s[key][ctx.n] == 1
        return ok, f"{len(E)} accepted press(es) -> expected {'closed' if want_closed else 'open'} at the end"

    S = []

    def seq(name, gaps, tier, first=10, tail=150, extra=(), fast_front=False, desc=""):
        t, ev = first, []
        for g in [0] + list(gaps):
            t += g
            ev.append((t, "BTN", 1))
        dur = t + tail
        tier_state = tier
        checks = [Chk("toggle_parity", "toggle", tier_state, chk_parity),
                  Chk("flush_closed", "flush", tier_state, lambda ctx: _all(ctx, [chk_win("FRONT", "closed", "front cells clear"),
                                                                                chk_win("BACK1", "closed", "back cells clear")])),
                  Chk("open_passage", "open", tier_state, chk_win("PASSAGE", "open", "walk-through passage (+-2 cells) clear")),
                  Chk("no_stuck_pistons", "pistons", tier_state, lambda ctx: _all(ctx, [chk_win("PISTONS", "closed", "piston consistency"),
                                                                                       chk_win("PISTONS", "open", "piston consistency")])),
                  Chk("wall_material", "flush", "info", chk_win("WALLMAT", "closed", "closed door made of the wall's block"))]
        if fast_front:
            checks.append(Chk("front_never_blocked", "flush", "stress", chk_front_always))
        checks += list(extra)
        S.append(Scn(name, ev, dur, ["FULL", "AIR", "FRONT", "BACK1", "PASSAGE", "PISTONS", "WALLMAT"], checks, {"desc": desc}))

    def _all(ctx, fns):
        for f_ in fns:
            ok, d = f_(ctx)
            if not ok:
                return False, d
        return True, ""

    S.append(Scn("idle_closed_long", [], 3000, ["FULL", "AIR", "FRONT", "BACK1", "PISTONS", "WALLMAT"], [
        Chk("initial_valid", "init", "core", chk_init),
        Chk("starts_closed", "init", "info", chk_starts_closed),
        Chk("holds_3000_ticks", "idle", "core", lambda ctx: (lambda r: (r[0], "" if r[0] else f"FULL changed at tick {r[1]}"))(ctx.all_eq("FULL", 0, ctx.n, ctx.s["FULL"][0]))),
        Chk("flush_closed", "flush", "core", lambda ctx: _all(ctx, [lambda c: _eq(c, "FRONT", 0, c.n), lambda c: _eq(c, "BACK1", 0, c.n)])),
        Chk("no_stuck_pistons", "pistons", "core", lambda ctx: (lambda r: (r[0], "" if r[0] else f"inconsistent pistons at tick {r[1]}"))(ctx.all_eq("PISTONS", 0, ctx.n, 1))),
        Chk("wall_material", "flush", "info", lambda ctx: (lambda r: (r[0], "" if r[0] else f"doorway block differs from the wall's at tick {r[1]}"))(ctx.all_eq("WALLMAT", 0, ctx.n, 1))),
    ], {"desc": "nothing pressed for 150 s: still a valid, flush, stable door"}))

    def _eq(ctx, pred, a, b):
        ok, t, _ = ctx.all_eq(pred, a, b, 1)
        return ok, "" if ok else f"{pred} violated at tick {t}"

    seq("gap_120", [120] * 7, "core", fast_front=True, desc="8 presses 6 s apart")
    seq("gap_61", [61] * 9, "core", desc="10 presses ~3 s apart (odd tick phase)")
    seq("gap_40", [40] * 11, "core", desc="12 presses 2 s apart")
    seq("gap_31", [31] * 15, "core", desc="16 presses 1.55 s apart (door has 29 ticks to finish)")
    seq("gap_25", [25] * 15, "stress", desc="16 presses 1.25 s apart (button released 5 ticks before)")
    seq("gap_21", [21] * 15, "stress", desc="16 presses every 21 ticks (fastest the stone button re-arms)")
    rng = random.Random(51)
    seq("gap_random", [rng.randint(31, 200) for _ in range(23)], "core", tail=300, desc="24 presses with random 31..200 tick gaps")
    seq("idle_between", [3000, 5000, 40], "core", tail=400, desc="long idle (150 s, 250 s) between presses")
    seq("double_click_10", [10, 150, 10, 150], "core", extra=[Chk("one_toggle_per_accepted_press", "toggle", "core", chk_toggled_once)],
        desc="two clicks 10 ticks apart (the stone button ignores the second while pressed), twice")
    seq("spam_5", [5] * 39, "stress", tail=300, extra=[Chk("ends_stable", "pistons", "core", chk_stable_end),
                                                      Chk("pistons_ok_at_end", "pistons", "core", chk_pistons_end)],
        desc="40 clicks 5 ticks apart (button mashing)")
    for g in sweep:
        seq(f"sweep_gap_{g}", [g] * 11, "info", desc=f"threshold sweep: 12 presses {g} ticks apart")
    return Spec("door", preds, S, {"BTN": btn}, "flush 2x2 toggle door")


# ================================================================================================ COUNTER
def counter_spec(w: World, sweep=()) -> Spec:
    P = w.ports
    L = [P[f"L{i}"] for i in range(4)]
    preds = {f"L{i}": lamp_pred(L[i].pos) for i in range(4)}
    cells = [tuple(p.pos) for p in L]

    def count_fn(cur):
        v = 0
        for i, c in enumerate(cells):
            s = cur.get(c, AIR)
            v += (s.startswith("minecraft:redstone_lamp[") and "lit=true" in s) << i
        return v
    preds["COUNT"] = Pred(count_fn, set(cells))
    preds["in:BTN"] = powered_pred(P["BTN"].pos)
    preds["in:RST"] = powered_pred(P["RST"].pos)

    def timeline(ctx):
        """[(edge_index, 'B'|'R')] sorted; same index: count first, reset after (reset wins a tie)."""
        ev = [(e, 0, "B") for e in ctx.edges("BTN")] + [(e, 1, "R") for e in ctx.edges("RST")]
        return [(e, k) for e, _o, k in sorted(ev)]

    def model(ctx, c0, lag):
        """-> windows [(a, b, expected, n_edges)] in which the count must equal the sequential model."""
        tl, v, out = timeline(ctx), c0, []
        out.append((0, (tl[0][0] - 1) if tl else ctx.n, v, 0))
        for k, (e, kind) in enumerate(tl):
            v = (v + 1) % 16 if kind == "B" else 0
            nxt = tl[k + 1][0] if k + 1 < len(tl) else ctx.n + 1
            if k + 1 < len(tl) and nxt == e:     # same tick: wait for the group
                continue
            out.append((e + min(lag, max(0, nxt - e - 2)) if lag is not None else e + 150, nxt - 1, v, k + 1))
        return [(a, b, x, n) for a, b, x, n in out if a <= b]

    def chk_model(lag_cap, only_final=False, label=""):
        def f(ctx):
            c0 = ctx.s["COUNT"][0]
            wins = model(ctx, c0, lag_cap)
            if only_final:
                wins = wins[-1:]
            for a, b, want, n in wins:
                ok, t, got = ctx.all_eq("COUNT", a, b, want)
                if not ok:
                    return False, f"{label}count should be {want} (model after {n} edges) in [{a},{b}] but is {got} at tick {t}"
            return True, f"{len(wins)} windows"
        return f

    def chk_final(ctx):
        return chk_model(None, True, "final: ")(ctx)

    def chk_init_zero(ctx):
        return ctx.s["COUNT"][0] == 0, f"initial count {ctx.s['COUNT'][0]}"

    def chk_stable(ctx):
        ok, t, got = ctx.all_eq("COUNT", 0, ctx.n, ctx.s["COUNT"][0])
        return ok, "" if ok else f"count changed to {got} at tick {t} with no input"

    def chk_continue(n_presses, after):
        """After the incident (index `after` = 'quiet since'), the next presses count +1 each from the displayed value."""
        def f(ctx):
            E = ctx.edges("BTN")
            tail = [e for e in E if e >= after]
            if len(tail) < n_presses:
                return False, f"only {len(tail)} of the {n_presses} continuation presses were accepted"
            v0 = ctx.s["COUNT"][tail[0] - 1]
            for k in range(n_presses):
                a = tail[k] + 60
                b = min([e for e, _k in timeline(ctx) if e > tail[k]] + [ctx.n + 1]) - 1     # up to the next press of either button
                ok, t, got = ctx.all_eq("COUNT", a, b, (v0 + k + 1) % 16)
                if not ok:
                    return False, f"continuation press {k + 1}: count {got} instead of {(v0 + k + 1) % 16} (displayed {v0} before) at tick {t}"
            return True, ""
        return f

    def chk_lat(limit):
        """Response time of every accepted BTN edge that has a settled display before it and >= 45 quiet ticks after it: ticks from
        the press until the display stops changing (it must then show previous + 1)."""
        def f(ctx):
            worst, n = 0, 0
            tl = timeline(ctx)
            for k, (e, kind) in enumerate(tl):
                if kind != "B":
                    continue
                prev_t = tl[k - 1][0] if k else -10 ** 9
                nxt = tl[k + 1][0] if k + 1 < len(tl) else ctx.n + 1
                if e - prev_t < 45 or nxt - e < 45:
                    continue
                n += 1
                want = (ctx.s["COUNT"][e - 1] + 1) % 16
                last = max([i for i in range(e, nxt) if ctx.s["COUNT"][i] != ctx.s["COUNT"][i - 1]] or [e - 1])
                if ctx.s["COUNT"][nxt - 1] != want:
                    return False, f"press at {e}: display {ctx.s['COUNT'][nxt - 1]} instead of {want}"
                worst = max(worst, last - e + 1)
            return worst <= limit, f"worst response {worst} ticks over {n} presses (limit {limit})"
        return f

    S = []
    base_uses = ["COUNT"]

    def counting(name, gap, n, tier, lag=60, desc=""):
        ev = [(10 + i * gap, "BTN", 1) for i in range(n)]
        dur = 10 + n * gap + 150
        checks = [Chk("count_model_final", "count", tier, chk_final)]
        if gap >= 40:
            checks.append(Chk("count_model_every_press", "count", tier, chk_model(lag)))
            if gap >= 45:
                checks.append(Chk("response_le_60", "latency", tier, chk_lat(60)))
        else:
            checks.append(Chk("count_model_every_press", "count", "stress", chk_model(lag)))
        S.append(Scn(name, ev, dur, base_uses, checks, {"desc": desc}))

    S.append(Scn("idle_long", [], 3000, base_uses, [Chk("initial_zero", "init", "info", chk_init_zero),
                                                   Chk("no_drift", "idle", "core", chk_stable)],
                 {"desc": "nothing pressed for 150 s"}))
    counting("count_gap_80", 80, 18, "core", desc="18 presses 4 s apart, wraps 15->0 at the 16th")
    counting("count_gap_60", 60, 18, "core", desc="18 presses 3 s apart")
    counting("count_gap_45", 45, 18, "core", desc="18 presses 2.25 s apart")
    counting("count_gap_40", 40, 18, "core", desc="18 presses 2 s apart")
    counting("count_gap_35", 35, 18, "core", desc="18 presses 1.75 s apart (reasonable: someone tapping about once a second and a bit)")
    counting("count_gap_30", 30, 18, "core", desc="18 presses 1.5 s apart")
    counting("count_gap_25", 25, 18, "core", desc="18 presses 1.25 s apart")
    counting("count_gap_21", 21, 18, "stress", desc="18 presses every 21 ticks (the fastest the stone button re-arms)")
    counting("count_gap_45_x35", 45, 35, "core", desc="35 presses: two full wraps")

    # setup presses in the scenarios below use an 80-tick gap so that the press RATE (tested in count_gap_*) does not confound them
    # reset from every value
    for v in range(16):
        ev = [(10, "RST", 1)] + [(90 + i * 80, "BTN", 1) for i in range(v)]
        t_r = 90 + v * 80 + 60
        ev.append((t_r, "RST", 1))
        dur = t_r + 200

        def mk(v=v, t_r=t_r):
            def f(ctx):
                ok1, t1, g1 = ctx.all_eq("COUNT", 80, 80, 0)       # after the first reset
                if not ok1:
                    return False, f"initial reset (RST at rest) left count {g1}"
                a = t_r - 5
                ok2, t2, g2 = ctx.all_eq("COUNT", a - 1, a - 1, v)
                if not ok2:
                    return False, f"count before the reset should be {v}, is {g2}"
                ok3, t3, g3 = ctx.all_eq("COUNT", t_r + 60, ctx.n, 0)
                return ok3, "" if ok3 else f"reset from {v}: count {g3} at tick {t3}, expected 0"
            return f
        S.append(Scn(f"reset_from_{v:02d}", ev, dur, base_uses, [Chk("reset_to_zero", "reset", "core", mk())], {"desc": f"count to {v}, press reset"}))

    # reset pressed d ticks after a count press (reset wins), then keep counting
    for d, tier in ((60, "core"), (40, "core"), (25, "core"), (15, "stress"), (10, "stress")):
        ev = [(10, "RST", 1)] + [(90 + 80 * i, "BTN", 1) for i in range(3)]
        tb = 90 + 3 * 80 + 60
        ev += [(tb, "BTN", 1), (tb + d, "RST", 1)]
        cont = tb + d + 200
        ev += [(cont + 80 * i, "BTN", 1) for i in range(3)]
        dur = cont + 3 * 80 + 150
        S.append(Scn(f"btn_then_rst_{d}", ev, dur, base_uses, [
            Chk("reset_wins", "reset", tier, (lambda tb=tb, d=d, cont=cont: lambda ctx: (lambda r: (r[0], "" if r[0] else f"count {r[2]} (expected 0 after RST {d} ticks after BTN) at tick {r[1]}"))(ctx.all_eq("COUNT", tb + d + 100, cont - 1, 0)))()),
            Chk("keeps_counting_after", "count", tier, chk_continue(3, cont - 1)),
            Chk("model_final", "count", tier, chk_final)], {"desc": f"BTN then RST {d} ticks later; then 3 more presses"}))
    # count pressed e ticks after a reset (count survives), then more
    for e_, tier in ((60, "core"), (40, "core"), (25, "core"), (15, "stress"), (10, "stress")):
        ev = [(10, "RST", 1)] + [(90 + 80 * i, "BTN", 1) for i in range(5)]
        tr = 90 + 5 * 80 + 60
        ev += [(tr, "RST", 1), (tr + e_, "BTN", 1)]
        cont = tr + e_ + 200
        ev += [(cont + 80 * i, "BTN", 1) for i in range(3)]
        dur = cont + 3 * 80 + 150
        S.append(Scn(f"rst_then_btn_{e_}", ev, dur, base_uses, [
            Chk("count_after_reset_is_1", "count", tier, (lambda tr=tr, e_=e_, cont=cont: lambda ctx: (lambda r: (r[0], "" if r[0] else f"count {r[2]} (expected 1 = reset, then one count {e_} ticks later) at tick {r[1]}"))(ctx.all_eq("COUNT", tr + e_ + 100, cont - 1, 1)))()),
            Chk("keeps_counting_after", "count", tier, chk_continue(3, cont - 1)),
            Chk("model_final", "count", tier, chk_final)], {"desc": f"RST then BTN {e_} ticks later; then 3 more presses"}))

    # simultaneous BTN + RST
    ev = [(10, "RST", 1)] + [(90 + 80 * i, "BTN", 1) for i in range(3)]
    ts = 90 + 3 * 80 + 60
    ev += [(ts, "BTN", 1), (ts, "RST", 1)]
    cont = ts + 200
    ev += [(cont + 80 * i, "BTN", 1) for i in range(3)]
    ev += [(cont + 3 * 80 + 60, "RST", 1)]
    dur = cont + 3 * 80 + 60 + 250

    def chk_simul_valid(ctx):
        v = ctx.s["COUNT"][ts + 120]
        return v in (0, 4), f"count {v} after BTN+RST in the same tick from 3 (acceptable: 0 reset wins, 4 count wins)"

    def chk_simul_zero(ctx):
        v = ctx.s["COUNT"][ts + 120]
        return v == 0, f"count {v}, reset-wins would be 0"

    def chk_simul_final_reset(ctx):
        ok, t, g = ctx.all_eq("COUNT", ctx.n - 100, ctx.n, 0)
        return ok, "" if ok else f"final RST did not clear: {g}"
    S.append(Scn("simultaneous_btn_rst", ev, dur, base_uses, [
        Chk("valid_outcome", "simultaneous", "core", chk_simul_valid),
        Chk("reset_wins", "simultaneous", "info", chk_simul_zero),
        Chk("keeps_counting_after", "simultaneous", "core", chk_continue(3, cont - 1)),
        Chk("reset_still_works", "simultaneous", "core", chk_simul_final_reset)], {"desc": "BTN and RST in the same tick, then 3 presses and a reset"}))

    # button mashing: pure sequential model over the accepted edges
    for g in (10, 14, 20):
        ev = [(10 + i * g, "BTN", 1) for i in range(30)]
        dur = 10 + 30 * g + 200
        S.append(Scn(f"mash_btn_{g}", ev, dur, base_uses, [
            Chk("model_final", "count", "stress", chk_final),
            Chk("keeps_counting_after", "count", "stress", chk_continue(1, 10 + 30 * g + 100))], {"desc": f"30 clicks {g} ticks apart (the button accepts every {'2nd' if g == 10 else 'one' if g >= 20 else '2nd'} click)"}))
    # keep the last continuation honest: one more press after the mash
    for sc_ in S:
        if sc_.name.startswith("mash_btn_"):
            g = int(sc_.name.rsplit("_", 1)[1])
            sc_.events.append((10 + 30 * g + 100, "BTN", 1))
    ev = [(10, "RST", 1)] + [(90 + 60 * i, "BTN", 1) for i in range(5)] + [(500 + 30 * i, "RST", 1) for i in range(8)]
    S.append(Scn("mash_rst_30", ev, 500 + 8 * 30 + 250, base_uses, [Chk("model_final", "reset", "stress", chk_final)], {"desc": "reset pressed 8 times 30 ticks apart"}))
    for g in sweep:       # threshold sweeps (tier info): press rate, RST -> BTN spacing, BTN -> RST spacing
        counting(f"sweep_gap_{g}", g, 18, "info", desc=f"threshold sweep: 18 presses {g} ticks apart")
        ev = [(10, "RST", 1)] + [(90 + 80 * i, "BTN", 1) for i in range(5)] + [(90 + 5 * 80 + 60, "RST", 1), (90 + 5 * 80 + 60 + g, "BTN", 1)]
        tr = 90 + 5 * 80 + 60
        S.append(Scn(f"sweep_rst_btn_{g}", ev, tr + g + 250, base_uses, [Chk("count_after_reset_is_1", "count", "info", (lambda tr=tr, g=g: lambda ctx: (lambda r: (r[0], "" if r[0] else f"count {r[2]}"))(ctx.all_eq("COUNT", tr + g + 100, ctx.n, 1)))())], {}))
        ev = [(10, "RST", 1)] + [(90 + 80 * i, "BTN", 1) for i in range(3)] + [(90 + 3 * 80 + 60, "BTN", 1), (90 + 3 * 80 + 60 + g, "RST", 1)]
        tb = 90 + 3 * 80 + 60
        S.append(Scn(f"sweep_btn_rst_{g}", ev, tb + g + 250, base_uses, [Chk("reset_wins", "reset", "info", (lambda tb=tb, g=g: lambda ctx: (lambda r: (r[0], "" if r[0] else f"count {r[2]}"))(ctx.all_eq("COUNT", tb + g + 100, ctx.n, 0)))())], {}))
    return Spec("counter", preds, S, {"BTN": P["BTN"], "RST": P["RST"]}, "4-bit counter")


# ================================================================================================ ALU
def alu_expect(a, b, op):
    if op == 0:
        s = a + b
        return s & 15, s >> 4
    return ((a & b) if op == 1 else (a | b) if op == 2 else (a ^ b)), 0


def alu_spec(w: World, passes=((101, 110), (202, 110)), shards=8, fast_n=300, burst_n=30) -> Spec:
    P = w.ports
    names = [f"A{i}" for i in range(4)] + [f"B{i}" for i in range(4)] + ["OP0", "OP1"]
    preds = {"in:" + n: powered_pred(P[n].pos) for n in names}
    rcells = [tuple(P[f"R{i}"].pos) for i in range(4)]
    cc = tuple(P["CARRY"].pos)

    def res_fn(cur):
        v = 0
        for i, c in enumerate(rcells):
            s = cur.get(c, AIR)
            v += (s.startswith("minecraft:redstone_lamp[") and "lit=true" in s) << i
        return v
    preds["RES"] = Pred(res_fn, set(rcells))
    preds["CARRY"] = lamp_pred(cc)

    def inputs_at(ctx, i):
        g = lambda n: ctx.s["in:" + n][i]
        a = sum(g(f"A{k}") << k for k in range(4))
        b = sum(g(f"B{k}") << k for k in range(4))
        return a, b, g("OP1") * 2 + g("OP0")

    def vec_events(t, prev, new):
        ev = []
        for k in range(4):
            for nm, idx in ((f"A{k}", 0), (f"B{k}", 1)):
                if (prev[idx] >> k) & 1 != (new[idx] >> k) & 1:
                    ev.append((t, nm, (new[idx] >> k) & 1))
        for k, nm in enumerate(("OP0", "OP1")):
            if (prev[2] >> k) & 1 != (new[2] >> k) & 1:
                ev.append((t, nm, (new[2] >> k) & 1))
        return ev

    allrows = [(a, b, o) for o in range(4) for a in range(16) for b in range(16)]

    def hold_checks(sample_idx, hold, tier_res="core", latency_limits=(100, 60)):
        """sample_idx: list of (start_index, end_index) per hold; check the settled value at the end, carry rules, settling time."""
        def res_chk(ops):
            def f(ctx):
                bad = 0
                first = None
                for st_, en in sample_idx:
                    a, b, o = inputs_at(ctx, en)
                    if o not in ops:
                        continue
                    r, c = alu_expect(a, b, o)
                    if ctx.s["RES"][en] != r:
                        bad += 1
                        first = first or f"A={a} B={b} op={o}: R={ctx.s['RES'][en]} expected {r} at tick {en}"
                return bad == 0, f"{bad} wrong rows; first: {first}" if bad else f"{sum(1 for _s, en in sample_idx if inputs_at(ctx, en)[2] in ops)} rows"
            return f

        def carry_chk(ops):
            def f(ctx):
                bad, first, n = 0, None, 0
                for st_, en in sample_idx:
                    a, b, o = inputs_at(ctx, en)
                    if o not in ops:
                        continue
                    n += 1
                    r, c = alu_expect(a, b, o)
                    if ctx.s["CARRY"][en] != c:
                        bad += 1
                        first = first or f"A={a} B={b} op={o}: CARRY={ctx.s['CARRY'][en]} expected {c} at tick {en}"
                return bad == 0, f"{bad}/{n} wrong; first: {first}" if bad else f"{n} rows"
            return f

        def settle_chk(limit):
            def f(ctx):
                worst, wrow, over = 0, None, 0
                for st_, en in sample_idx:
                    a, b, o = inputs_at(ctx, en)
                    r, c = alu_expect(a, b, o)
                    last_bad = None
                    for i in range(st_ + 1, en + 1):
                        if ctx.s["RES"][i] != r or ctx.s["CARRY"][i] != c:
                            last_bad = i
                    lat = 0 if last_bad is None else last_bad - st_
                    if lat > limit:
                        over += 1
                    if lat > worst:
                        worst, wrow = lat, (a, b, o)
                return over == 0, f"{over} rows settle later than {limit} ticks; worst {worst} ticks at A,B,op={wrow}"
            return f
        out = [Chk("result_add", "alu_result", tier_res, res_chk({0})), Chk("result_and", "alu_result", tier_res, res_chk({1})),
               Chk("result_or", "alu_result", tier_res, res_chk({2})), Chk("result_xor", "alu_result", tier_res, res_chk({3})),
               Chk("carry_add", "alu_carry", tier_res, carry_chk({0})), Chk("carry_nonadd_is_0", "alu_carry", "info", carry_chk({1, 2, 3}))]
        if tier_res == "core":
            out += [Chk(f"settles_within_{latency_limits[0]}", "alu_latency", "core", settle_chk(latency_limits[0])),
                    Chk(f"settles_within_{latency_limits[1]}", "alu_latency", "info", settle_chk(latency_limits[1]))]
        return out

    S = []
    ins0 = (0, 0, 0)

    def rows_scn(name, rows, hold, tier="core", prev0=None, desc="", gap_lead=30):
        prev = prev0 if prev0 is not None else ins0
        ev, idx, t = [], [], gap_lead
        for r in rows:
            ev += vec_events(t, prev, r)
            prev = r
            t += hold
            idx.append((t - hold, t))
        # first row: levers start from the world's own lever state (read at run time); the hold start is the event tick
        sc = Scn(name, ev, t + 5, ["RES", "CARRY"], hold_checks([(a + 1, b) for a, b in idx], hold, tier), {"desc": desc})
        return sc

    # initial levers: all off in the export? read from the world so row 0 events are right
    lv0 = [int("powered=true" in w.cells.get(tuple(P[n].pos), "")) for n in names]
    ins0 = (sum(lv0[k] << k for k in range(4)), sum(lv0[4 + k] << k for k in range(4)), lv0[8] + 2 * lv0[9])
    for pi, (seed, hold) in enumerate(passes):
        rows = list(allrows)
        random.Random(seed).shuffle(rows)
        n = len(rows) // shards
        for s in range(shards):
            S.append(rows_scn(f"exh{pi + 1}_shard{s}", rows[s * n:(s + 1) * n], hold, "core",
                              desc=f"exhaustive pass {pi + 1}: rows {s * n}..{(s + 1) * n - 1} of a seeded shuffle of all 1024 (A,B,OP), hold {hold}"))
    rng = random.Random(303)
    cur, rows = ins0, []
    for _ in range(fast_n):
        k = rng.choice([1, 2, 3, 4, 6])
        bits = rng.sample(range(10), k)
        a, b, o = cur
        for bit in bits:
            if bit < 4:
                a ^= 1 << bit
            elif bit < 8:
                b ^= 1 << (bit - 4)
            else:
                o ^= 1 << (bit - 8)
        cur = (a, b, o)
        rows.append(cur)
    for s in range(3):
        S.append(rows_scn(f"fast_shard{s}", rows[s * (fast_n // 3):(s + 1) * (fast_n // 3)], 100, "core",
                          desc="random multi-lever flips (1-6 levers in the same tick), 100-tick holds"))
    # bursts: flail the levers (a flip every 3..6 ticks), then idle 200 ticks and read
    rng2 = random.Random(404)
    ev, idx, t = [], [], 30
    cur = ins0
    for _ in range(burst_n):
        for _f in range(rng2.randint(8, 16)):
            bit = rng2.randrange(10)
            a, b, o = cur
            nxt = (a ^ (1 << bit), b, o) if bit < 4 else (a, b ^ (1 << (bit - 4)), o) if bit < 8 else (a, b, o ^ (1 << (bit - 8)))
            ev += vec_events(t, cur, nxt)
            cur = nxt
            t += rng2.randint(3, 6)
        start = t
        t += 200
        idx.append((start, t))
    S.append(Scn("burst_flail", ev, t + 5, ["RES", "CARRY"], hold_checks([(a + 1, b) for a, b in idx], 200, "core", (100, 60))[:6],
                 {"desc": "30 bursts of 8-16 single-lever flips 3-6 ticks apart (levers flailed), 200-tick rest, read"}))
    return Spec("alu", preds, S, {n: P[n] for n in names}, "4-bit ALU")


# ================================================================================================ own spec (the agent's spec.json) replay
def own_spec(name: str, w: World) -> Spec:
    """Re-express the agent's OWN spec.json (sequences / truth table) on the same world so own-spec and hidden-spec verdicts come
    from one harness: windows as written, truth tables with a 150-tick hold in authoring order."""
    from env.contract import Contract
    d = json.loads((Path(PROJECTS[name]["dir"]) / "spec.json").read_text())
    c = Contract.from_dict(d)
    P = w.ports
    ins = {n: p for n, p in P.items() if p.is_input}
    preds = {"in:" + n: powered_pred(p.pos) for n, p in ins.items()}

    def rd(p):
        if p.kind == "lamp":
            return lamp_pred(p.pos)
        if p.kind == "region":
            cl = box_cells(p.box)
            if p.region_mode == "air":
                return region_pred(cl, is_air)
            if p.region_mode == "block":
                return region_pred(cl, lambda s, b=p.block: st_id(s) == b)
            if p.region_mode == "solid":
                return region_pred(cl, lambda s: not is_air(s))
            return region_pred(cl, lambda s, bl=p.blocks: st_id(s) in bl)
        raise ValueError(p.kind)
    for n, p in P.items():
        if p.is_output:
            preds["out:" + n] = rd(p)
    S = []
    for sq in c.behaviour.sequences:
        evs = [(e.t, k, v) for e in sq.events for k, v in e.set.items()]

        def mk(sq=sq):
            def f(ctx):
                for e in sq.expect:
                    s = ctx.s["out:" + e.port]
                    seg = s[e.t1:e.t2 + 1]
                    want = e.value
                    if want in ("init", "!init"):
                        want = s[0] if want == "init" else 1 - s[0]
                    ok = any(x == want for x in seg) if e.mode == "any" else all(x == want for x in seg)
                    if not ok:
                        return False, f"{e.port}={want} [{e.t1},{e.t2}] {e.mode} violated"
                return True, ""
            return f
        S.append(Scn(sq.name, evs, sq.duration, ["out:" + o for o in sorted({e.port for e in sq.expect})],
                     [Chk("own_sequence", "own", "core", mk())], {"desc": "agent's own sequence"}))
    tt = c.behaviour.truth_table
    if tt:
        outs = sorted({k for r in tt.rows for k in r.expect})
        hold = 150
        rows = list(tt.rows)
        n = max(1, len(rows) // 8)
        for s in range(0, len(rows), n):
            chunk = rows[s:s + n]
            ev, idx, t = [], [], 30
            for r in chunk:
                for k, v in r.inputs.items():
                    ev.append((t, k, v))
                t += hold
                idx.append((t - hold + 1, t))

            def mk(chunk=chunk, idx=idx):
                def f(ctx):
                    bad = 0
                    first = None
                    for r, (_a, en) in zip(chunk, idx):
                        for k, e in r.expect.items():
                            if e is not None and ctx.s["out:" + k][en] != e:
                                bad += 1
                                first = first or f"{r.inputs}: {k}={ctx.s['out:' + k][en]} expected {e}"
                    return bad == 0, f"{bad} wrong rows; first {first}" if bad else f"{len(chunk)} rows"
                return f
            S.append(Scn(f"own_rows_{s}", ev, t + 5, ["out:" + o for o in outs], [Chk("own_rows", "own", "core", mk())], {"desc": "agent's own truth-table rows"}))
    return Spec(name + "_own", preds, S, {n: p for n, p in ins.items()}, "agent's own spec")


SPECS = {"door": door_spec, "counter": counter_spec, "alu": alu_spec}


# ================================================================================================ negative controls
def mutants(name: str, w: World):
    """A few deliberately broken copies of the build: the hidden spec must reject each one (it is not vacuous)."""
    P = w.ports

    def without(pos):
        return World({p: st for p, st in w.cells.items() if p != pos}, w.nbt, w.ports)

    def nth(block, k):
        xs = sorted(p for p, st in w.cells.items() if st_id(st) == block)
        return xs[k % len(xs)]
    if name == "door":
        return {"remove_a_piston": without(nth("minecraft:sticky_piston", 0)), "remove_a_repeater": without(nth("minecraft:repeater", 0))}
    if name == "counter":
        return {"rst_button_is_stone": World({**w.cells, tuple(P["RST"].pos): "minecraft:stone"}, w.nbt, w.ports),
                "remove_a_comparator": without(nth("minecraft:comparator", 0))}
    return {"remove_a_comparator": without(nth("minecraft:comparator", 28)), "remove_a_repeater": without(nth("minecraft:repeater", 66))}


def run_controls(name):
    w = load_world(name)
    out = {}
    for mname, mw in mutants(name, w).items():
        spec = SPECS[name](mw)
        be = EnvBackend(mw)
        bad, first = Counter(), None
        for s in spec.scns:
            if name == "alu" and not (s.name == "exh1_shard0" or s.name == "burst_flail"):
                continue
            rows, _ = evaluate(spec, s, be.run(plan_of(mw, s, spec.in_ports)))
            for r in rows:
                if r["tier"] == "core" and not r["ok"]:
                    bad[r["cls"]] += 1
                    first = first or f"{r['scn']}/{r['id']}: {r['detail'][:100]}"
        out[mname] = {"core_failing_checks_by_class": dict(bad), "first": first, "rejected": bool(bad)}
        print(f"  control {name}/{mname}: rejected={bool(bad)} {dict(bad)}  {first}")
    return out


# ================================================================================================ driver
def run_one(name, backend, variant, own=False, workers=4, only=None, spec_kwargs=None):
    rot, mirror, origin = variant_params(name, variant)
    w0 = load_world(name)
    w = transform(w0, rot, mirror) if (rot or mirror) else w0
    spec = own_spec(name, w) if own else SPECS[name](w, **(spec_kwargs or {}))
    scns = [s for s in spec.scns if not only or any(o in s.name for o in only)]
    res = {"project": name, "backend": backend, "variant": variant, "own": own, "rot": rot, "mirror": mirror, "origin": origin,
           "scenarios": [], "checks": [], "rle": {}}
    t0 = time.time()
    if backend == "env":
        be = EnvBackend(w)
        res["env_at_rest"] = {"ticks": be.rest_ticks, "at_rest": be.at_rest, "place_errors": len(be.place_errors)}
        for s in scns:
            plan = plan_of(w, s, spec.in_ports)
            t1 = time.time()
            tr = be.run(plan)
            rows, rl = evaluate(spec, s, tr)
            res["checks"] += rows
            res["rle"][s.name] = rl
            res["scenarios"].append({"name": s.name, "ticks": plan.ticks, "sec": round(time.time() - t1, 1), "desc": s.meta.get("desc", "")})
            print(f"  [{backend}/{variant}] {s.name:24} {plan.ticks:>7} ticks {time.time() - t1:5.1f}s "
                  f"{sum(not c['ok'] for c in res['checks'] if c['scn'] == s.name and c['tier'] == 'core')} core fails", flush=True)
    else:
        org = origin or tuple(min(p[i] for p in w.cells) for i in range(3))
        jobs = [(s.name, plan_of(w, s, spec.in_ports, org)) for s in scns]
        traces = run_vanilla(w, jobs, workers)
        for s, (n, plan), tr in zip(scns, jobs, traces):
            rows, rl = evaluate(spec, s, tr)
            res["checks"] += rows
            res["rle"][s.name] = rl
            res["scenarios"].append({"name": s.name, "ticks": plan.ticks, "desc": s.meta.get("desc", "")})
        res["origin"] = list(org)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def summarize(res):
    by = Counter()
    for c in res["checks"]:
        by[(c["tier"], c["ok"])] += 1
    core_fail = [c for c in res["checks"] if c["tier"] == "core" and not c["ok"]]
    return {"core_pass": by[("core", True)], "core_fail": by[("core", False)], "stress_pass": by[("stress", True)],
            "stress_fail": by[("stress", False)], "info_pass": by[("info", True)], "info_fail": by[("info", False)],
            "first_core_fail": (core_fail[0] if core_fail else None)}


def sweep(name, backend="env", workers=8):
    """Env-only threshold sweeps: the smallest press spacing at which the build still works (info only, not part of the verdict)."""
    gaps = list(range(14, 71))
    w = load_world(name)
    spec = SPECS[name](w, sweep=gaps)
    sw = [x for x in spec.scns if x.name.startswith("sweep_")]
    if backend == "env":
        be = EnvBackend(w)
        traces = [be.run(plan_of(w, x, spec.in_ports)) for x in sw]
    else:
        org = tuple(min(p[i] for p in w.cells) for i in range(3))
        traces = run_vanilla(w, [(x.name, plan_of(w, x, spec.in_ports, org)) for x in sw], workers)
    rows = {}
    for sc_, tr in zip(sw, traces):
        r, _ = evaluate(spec, sc_, tr)
        kind, g = sc_.name.rsplit("_", 1)
        rows.setdefault(kind, {})[int(g)] = {c["id"]: c["ok"] for c in r}
    out = {}
    for kind, d in rows.items():
        line = "".join("." if all(v.values()) else "X" for g, v in sorted(d.items()))
        ok = [g for g, v in sorted(d.items()) if all(v.values())]
        fails = [g for g, v in sorted(d.items()) if not all(v.values())]
        out[kind] = {"gaps": f"{min(d)}..{max(d)}", "pass_map": line, "lowest_all_pass_from": next((g for g in sorted(d) if all(all(x.values()) for gg, x in d.items() if gg >= g)), None),
                     "failing": fails}
        print(f"{name} {kind:18} gaps {min(d)}..{max(d)}  {line}   (X = fails; all gaps >= {out[kind]['lowest_all_pass_from']} pass)")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"sweep_{name}_{backend}.json").write_text(json.dumps(out, indent=1))
    return 0


RUNS = [("env", "orig"), ("env", "rot"), ("vanilla", "orig"), ("vanilla", "origin"), ("vanilla", "rot")]


def load_runs():
    """Merge data/fidelity/chunks/*.json (one file per `run` call; vanilla runs of the big ALU build are split into chunks of
    scenarios) into one record per (project, backend, variant, own); a later chunk overrides an earlier one per (scenario, check)."""
    runs = {}
    for f in sorted((OUT / "chunks").glob("*.json")):
        r = json.loads(f.read_text())
        key = (r["project"], r["backend"], r["variant"], bool(r["own"]))
        m = runs.setdefault(key, {**{k: r[k] for k in ("project", "backend", "variant", "own", "rot", "mirror", "origin")},
                                  "checks": [], "rle": {}, "scenarios": [], "seconds": 0})
        done = {(c["scn"], c["id"]) for c in r["checks"]}
        m["checks"] = [c for c in m["checks"] if (c["scn"], c["id"]) not in done] + r["checks"]
        m["rle"].update(r["rle"])
        m["scenarios"] += r["scenarios"]
        m["seconds"] += r["seconds"]
    return runs


def first_diff(a: str, b: str):
    """first tick where two RLE strings differ -> (tick, a_value, b_value) or None"""
    def expand(x):
        out = {}
        for tok in x.split():
            v, t = tok.split("@")
            out[int(t)] = v
        return out
    ea, eb = expand(a), expand(b)
    if ea == eb:
        return None
    ts = sorted(set(ea) | set(eb))
    ca = cb = None
    for t in ts:
        ca, cb = ea.get(t, ca), eb.get(t, cb)
        if ca != cb:
            return t, ca, cb
    return None


def report():
    runs = load_runs()
    lines = []
    P = lines.append
    P("## Verdicts per run (core / stress / info checks passing)\n")
    P("| build | spec | " + " | ".join(f"{b}/{v}" for b, v in RUNS) + " |")
    P("|---|---|" + "---|" * len(RUNS))
    for name in PROJECTS:
        for own in (True, False):
            cells = []
            for b, v in RUNS:
                r = runs.get((name, b, v, own))
                if not r:
                    cells.append("-")
                    continue
                s_ = summarize(r)
                tot = lambda k: s_[k + "_pass"] + s_[k + "_fail"]
                cells.append(f"{s_['core_pass']}/{tot('core')} core" + ("" if own else f", {s_['stress_pass']}/{tot('stress')} stress, {s_['info_pass']}/{tot('info')} info"))
            P(f"| {name} | {'own' if own else 'hidden'} | " + " | ".join(cells) + " |")
    P("\n## Hidden-spec failures by check class (core tier, failing checks / checks), per run\n")
    classes = sorted({c["cls"] for r in runs.values() if not r["own"] for c in r["checks"]})
    P("| build | class | " + " | ".join(f"{b}/{v}" for b, v in RUNS) + " |")
    P("|---|---|" + "---|" * len(RUNS))
    for name in PROJECTS:
        for cl in classes:
            cells, any_ = [], False
            for b, v in RUNS:
                r = runs.get((name, b, v, False))
                if not r:
                    cells.append("-")
                    continue
                cs = [c for c in r["checks"] if c["cls"] == cl and c["tier"] == "core"]
                any_ |= bool(cs)
                cells.append(f"{sum(not c['ok'] for c in cs)}/{len(cs)}" if cs else "")
            if any_:
                P(f"| {name} | {cl} | " + " | ".join(cells) + " |")
    P("\n## Failing hidden checks (tier core/stress), result per run (P pass, F fail, - not run)\n")
    P("| build | scenario | check | tier | " + " | ".join(f"{b}/{v}" for b, v in RUNS) + " | first failure (env/orig, else first available) |")
    P("|---|---|---|---|" + "---|" * len(RUNS) + "---|")
    for name in PROJECTS:
        base = {}
        for b, v in RUNS:
            r = runs.get((name, b, v, False))
            if r:
                for c in r["checks"]:
                    base.setdefault((c["scn"], c["id"], c["tier"]), {})[(b, v)] = c
        for key, d in base.items():
            if key[2] == "info" or all(c["ok"] for c in d.values()):
                continue
            det = next((c["detail"] for k in RUNS for c in [d.get(k)] if c and not c["ok"]), "")
            P(f"| {name} | {key[0]} | {key[1]} | {key[2]} | " + " | ".join(("P" if d[k]["ok"] else "F") if k in d else "-" for k in RUNS) + f" | {det[:140]} |")
    P("\n## env (Nucleation) vs vanilla on the same world/variant\n")
    P("| build | variant | scenarios | check outcomes identical | scenarios with identical per-tick series | ticks that differ (of all compared) | first differences |")
    P("|---|---|---|---|---|---|---|")

    def expand(x, n):
        ev, out, cur = {}, [], None
        for tok in x.split():
            v_, t_ = tok.split("@")
            ev[int(t_)] = v_
        for i in range(n + 1):
            cur = ev.get(i, cur)
            out.append(cur)
        return out
    for name in PROJECTS:
        for v in ("orig", "rot"):
            e, van = runs.get((name, "env", v, False)), runs.get((name, "vanilla", v, False))
            if not e or not van:
                continue
            ticks = {s_["name"]: s_["ticks"] for s_ in van["scenarios"]}
            same_scn, tot_scn, nd, nt, diffs = 0, 0, 0, 0, []
            for scn, rl in van["rle"].items():
                if scn not in e["rle"]:
                    continue
                tot_scn += 1
                bad = False
                for pr_, st_ in rl.items():
                    if pr_.startswith("in:"):
                        continue
                    a_, b_ = expand(e["rle"][scn][pr_], ticks[scn]), expand(st_, ticks[scn])
                    dd = [i for i in range(len(a_)) if a_[i] != b_[i]]
                    nt += len(a_)
                    nd += len(dd)
                    if dd:
                        bad = True
                        if len(diffs) < 3:
                            diffs.append(f"{scn}: {pr_} tick {dd[0]} env={a_[dd[0]]} vanilla={b_[dd[0]]}")
                same_scn += not bad
            ek = {(c["scn"], c["id"]): c["ok"] for c in e["checks"]}
            vk = {(c["scn"], c["id"]): c["ok"] for c in van["checks"]}
            both = [k for k in vk if k in ek]
            P(f"| {name} | {v} | {tot_scn} | {sum(ek[k] == vk[k] for k in both)}/{len(both)} | {same_scn}/{tot_scn} | {nd} / {nt} | {'; '.join(diffs)} |")
    txt = "\n".join(lines)
    (OUT / "report.md").write_text(txt)
    print(txt)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["list", "run", "controls", "report", "sweep"])
    ap.add_argument("project", nargs="?", choices=sorted(PROJECTS))
    ap.add_argument("--backend", choices=["env", "vanilla"], default="env")
    ap.add_argument("--variant", choices=["orig", "origin", "rot"], default="orig")
    ap.add_argument("--own", action="store_true", help="run the agent's own spec.json instead of the hidden one")
    ap.add_argument("--only", nargs="*", help="substring filter on scenario names")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for n, p in PROJECTS.items():
            w = load_world(n)
            sp = SPECS[n](w)
            print(f"{n:8} {len(w.cells):>6} blocks, ports {sorted(w.ports)}\n   request: {p['request']}")
            for s in sp.scns:
                print(f"   {s.name:24} ticks={s.duration + 1:>7} checks={[(c.id, c.tier) for c in s.checks][:3]}...")
        return 0
    if a.cmd == "controls":
        OUT.mkdir(parents=True, exist_ok=True)
        allc = {n: run_controls(n) for n in ([a.project] if a.project else PROJECTS)}
        (OUT / "controls.json").write_text(json.dumps(allc, indent=1))
        return 0 if all(c["rejected"] for v in allc.values() for c in v.values()) else 1
    if a.cmd == "report":
        return report()
    if a.cmd == "sweep":
        return sweep(a.project, a.backend, a.workers)
    res = run_one(a.project, a.backend, a.variant, a.own, a.workers, a.only)
    (OUT / "chunks").mkdir(parents=True, exist_ok=True)
    tag = "all" if not a.only else "-".join(a.only)
    out = Path(a.out) if a.out else OUT / "chunks" / f"{a.project}_{a.backend}_{a.variant}{'_own' if a.own else ''}__{tag}.json"
    res["summary"] = summarize(res)
    out.write_text(json.dumps(res, indent=1, default=str))
    s = res["summary"]
    print(f"{a.project} {a.backend} {a.variant}{' own' if a.own else ''}: core {s['core_pass']}/{s['core_pass'] + s['core_fail']}, "
          f"stress {s['stress_pass']}/{s['stress_pass'] + s['stress_fail']}, info {s['info_pass']}/{s['info_pass'] + s['info_fail']}  "
          f"({res['seconds']}s) -> {out}")
    if s["first_core_fail"]:
        c = s["first_core_fail"]
        print(f"  first core failure: {c['scn']}/{c['id']}: {c['detail']}")
    return 0 if not s["core_fail"] else 1


if __name__ == "__main__":
    sys.exit(main())
