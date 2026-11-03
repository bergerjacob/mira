"""The only module that talks to Nucleation (nucleation 0.12.0). Swap this file to swap the simulator.

Coordinates in/out of `Sim` are absolute task coordinates; internally shifted by the schematic's min corner
(TickSimulation addresses blocks relative to it; negative coordinates work, so pistons may push past it).

Placement (`MIRA_PLACEMENT`, default `canonical`): `compile_cells` builds the world the way a structure / Litematica paste does. Every
state is written raw, every redstone_wire as a cross (east/north/south/west=side, power=0: the state a hand placement starts from),
and `Sim` loads the schematic with `TickSettleMode.Placement`, which resolves wire shapes and torch/repeater state after ALL blocks
are present and keeps the ticks the placement scheduled (baking right after the load would drop them). The result does not depend
on the order of the blocks, hence not on the rotation / mirroring of a build, and equals what a user's paste produces
(docs/VALIDITY.md sections 9-10, 13). `MIRA_PLACEMENT=legacy` restores the old procedure: blocks placed one by one through the
engine (`{simulate=true}`, bottom-up y,x,z) and loaded InWorld; it mis-shapes some dust and picks order-dependent latch states.
"""
import json
import os
from functools import lru_cache

from nucleation import MchprsWorld, Schematic, TickSimulation, TickSettleMode

MODES = {"inworld": TickSettleMode.InWorld, "placement": TickSettleMode.Placement, "quiet": TickSettleMode.Quiet}


@lru_cache(maxsize=65536)
def parse(state: str):
    """'minecraft:lamp[lit=true]' -> ('minecraft:lamp', {'lit': 'true'}). Result is shared: do not mutate."""
    if "[" not in state:
        return state, {}
    bid, rest = state.split("[", 1)
    return bid, dict(kv.split("=", 1) for kv in rest.rstrip("]").split(",") if kv)


def prop(state: str, key: str, default=None):
    return parse(state)[1].get(key, default)


def shift(pos, origin):
    return (pos[0] - origin[0], pos[1] - origin[1], pos[2] - origin[2])


def unshift(pos, origin):
    return (pos[0] + origin[0], pos[1] + origin[1], pos[2] + origin[2])


def corner_of(cells):
    return tuple(min(p[i] for p in cells) for i in range(3)) if cells else (0, 0, 0)


PLACEMENTS = ("canonical", "legacy")
DEFAULT_PLACEMENT = "canonical"
CANONICAL_TAG = "mira_env_canonical"      # schematic name of a canonical compile; Sim loads such schematics with Placement
CROSS = "minecraft:redstone_wire[east=side,north=side,power=0,south=side,west=side]"


def placement_mode() -> str:
    """'canonical' or 'legacy' (env var MIRA_PLACEMENT, read on every call)."""
    m = os.environ.get("MIRA_PLACEMENT", DEFAULT_PLACEMENT).strip().lower() or DEFAULT_PLACEMENT
    if m not in PLACEMENTS:
        raise ValueError(f"MIRA_PLACEMENT must be one of {PLACEMENTS}, got {m!r}")
    return m


def engine_state(state: str) -> str:
    """Drop properties the engine must derive from neighbours (wire shape/power); stated values would be
    taken literally and leave wires disconnected."""
    bid, _ = parse(state)
    return "minecraft:redstone_wire" if bid == "minecraft:redstone_wire" else state


def compile_cells(cells: dict, nbt=None, engine_place=True):
    """cells: {abs_pos: canonical_state}. Returns (Schematic, corner, place_errors[(pos,state,msg)]); give
    `corner` to Sim. engine_place=True is the build placement (see the module docstring: canonical = raw states with
    cross dust, loaded with Placement; legacy = per-block engine placement). engine_place=False stores the states
    exactly as given (used for the paste-stability comparison)."""
    canonical = engine_place and placement_mode() == "canonical"
    sc = Schematic.create("mira_env")
    if canonical:
        sc.set_name(CANONICAL_TAG)
    origin = corner_of(cells)
    errors = []
    for pos in sorted(cells, key=lambda p: (p[1], p[0], p[2])):
        st = cells[pos]
        if canonical and parse(st)[0] == "minecraft:redstone_wire":
            st = CROSS
        x, y, z = shift(pos, origin)
        try:
            if nbt and pos in nbt:
                sc.set_block_with_nbt(x, y, z, st, json.dumps(nbt[pos]))
            else:
                sc.set_block(x, y, z, engine_state(st) + "{simulate=true}" if engine_place and not canonical else st)
        except Exception as e:  # engine refused: store raw so the sim still loads, report it
            errors.append((pos, st, str(e)))
            try:
                sc.set_block(x, y, z, st)
            except Exception as e2:
                errors.append((pos, st, "raw placement failed: " + str(e2)))
    return sc, origin, errors


def simulatable(state: str) -> bool:
    return _simulatable(state, placement_mode())


@lru_cache(maxsize=None)
def _simulatable(state: str, placement: str) -> bool:
    """Can Nucleation place and load this canonical state at all? (263 of 1286 block ids cannot be loaded by
    TickSimulation, e.g. daylight_detector, sculk sensors, most buttons/plates; some states of crafter/lectern
    cannot even be placed.) Tested in isolation on a stone floor and cached."""
    cells = {(x, 0, z): "minecraft:smooth_stone" for x in range(3) for z in range(3)}
    cells[(1, 1, 1)] = state
    sc, corner, errs = compile_cells(cells)
    if errs:
        return False
    try:
        Sim(sc, corner).settle(20)
        return True
    except Exception:
        return False


def is_canonical(schematic) -> bool:
    return schematic.name() == CANONICAL_TAG


class Sim:
    def __init__(self, schematic, origin, mode="inworld"):
        """mode applies to raw schematics (`compile_cells(..., engine_place=False)`, loaded from files); a schematic made by a
        canonical `compile_cells` is always loaded with Placement (InWorld would skip the shape pass on its cross dust)."""
        if is_canonical(schematic) and mode == "inworld":
            mode = "placement"
        self.mode = mode
        self.origin = tuple(origin)
        self.sim = TickSimulation.from_schematic(schematic, MODES[mode], 0, 0, 0, "")
        self._base = None
        self.steps = 0
        self.max_steps = None   # total tick budget across all runs on this Sim; exceeded -> RuntimeError

    # -- state ------------------------------------------------------------
    def get(self, pos) -> str:
        return self.sim.get_block(*shift(pos, self.origin))

    def snapshot(self) -> dict:
        return {unshift(tuple(e["pos"]), self.origin): e["state"] for e in json.loads(self.sim.world_snapshot_json())}

    @property
    def tick(self) -> int:
        return self.sim.tick_count()

    def quiescent(self) -> bool:
        return self.sim.is_quiescent()

    # -- actions ----------------------------------------------------------
    def use(self, pos):
        self.sim.use_block(*shift(pos, self.origin))

    def place(self, pos, state):
        self.sim.place_block(*shift(pos, self.origin), state)

    def _count(self):
        self.steps += 1
        if self.max_steps is not None and self.steps > self.max_steps:
            raise RuntimeError(f"tick budget of {self.max_steps} exceeded")

    def step(self, n=1):
        for _ in range(n):
            self._count()
            self.sim.step()

    def settle(self, budget, on_tick=None):
        """Step until quiescent or budget ticks. Returns (ticks_used, settled)."""
        n = 0
        while not self.sim.is_quiescent():
            if n >= budget:
                return n, False
            self._count()
            self.sim.step()
            n += 1
            if on_tick:
                on_tick(n)
        return n, True

    def checkpoint(self):
        self._base = self.sim.checkpoint()
        return self._base

    def restore(self, cp=None):
        self.sim.restore(self._base if cp is None else cp)
        self.sim.clear_changes()

    def set_seed(self, seed):
        self.sim.set_rng_seed(seed)

    def bake(self):
        """The current world as a new Schematic (min corner at 0,0,0): the at-rest world once settled."""
        out = Schematic.create("mira_env_settled")
        self.sim.bake_to(out)
        return out

    # -- change log -------------------------------------------------------
    def clear_changes(self):
        self.sim.clear_changes()

    def changes(self):
        """[(tick, abs_pos, from_state, to_state)]"""
        return [(c["tick"], unshift(tuple(c["pos"]), self.origin), c["from"], c["to"]) for c in json.loads(self.sim.changes_json())]



def structural_graph(schematic, corner):
    """Signal graph from Nucleation's MCHPRS compiler: ({node_id: (abs_pos, kind)}, {src: {dst}}), or None when the
    compiler cannot represent the build (e.g. pistons, observers). Used for R2 path checks."""
    try:
        g = MchprsWorld.create(schematic).export_graph_structural()
        nodes = {n["id"]: (unshift(tuple(n["pos"]), corner), n["kind"]) for n in json.loads(g.nodes_json())}
        adj = {}
        for e in json.loads(g.edges_json()):
            adj.setdefault(e["source"], set()).add(e["target"])
        return nodes, adj
    except Exception:
        return None


def graph_reaches(graph, src_pos, dst_pos):
    """Is there a directed path from the node at src_pos to the node at dst_pos? None if either is not a graph node."""
    nodes, adj = graph
    by_pos = {p: i for i, (p, _) in nodes.items()}
    if src_pos not in by_pos or dst_pos not in by_pos:
        return None
    seen, todo = {by_pos[src_pos]}, [by_pos[src_pos]]
    while todo:
        for nxt in adj.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return by_pos[dst_pos] in seen
