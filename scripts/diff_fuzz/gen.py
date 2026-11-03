"""Grammar-based generator of small valid redstone builds (3-15 components in a box of about 5x3x5).

Valid means: canonical states (`env.blocks.canonical`), every id loadable by Nucleation's simulator
(`scripts.diff_fuzz.loadable.simulatable`, via tools.oracle.sim, not env/sim.py), statically supported (`env.blocks.floating` yields nothing), no entities at t0, and the
stimulus is `use` ops only. Components grow from the inputs: 70% of placements are adjacent to an existing block, so
the builds are connected circuits rather than scatter. Containers get items only where the item stays inside
containers (a dropper whose target is not a container never holds items; dispensers are empty), because item
entities are not traced on either side.
"""
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.append(str(Path(__file__).resolve().parents[2]))

from env import blocks as B  # noqa: E402
from scripts.diff_fuzz.loadable import simulatable  # noqa: E402
from scripts.diff_fuzz.common import Case  # noqa: E402

Pos = Tuple[int, int, int]
H4 = ["north", "east", "south", "west"]
D6 = H4 + ["up", "down"]
VEC = B.DIRS
OPP = B.OPPOSITE

SOLID_POOL = [("minecraft:stone", 10), ("minecraft:smooth_stone", 2), ("minecraft:oak_planks", 1),
              ("minecraft:obsidian", 1), ("minecraft:iron_block", 1), ("minecraft:slime_block", 1.2),
              ("minecraft:honey_block", 0.5), ("minecraft:redstone_block", 1.4), ("minecraft:glass", 0.6),
              ("minecraft:redstone_lamp", 0.7), ("minecraft:target", 0.5)]
SAFE_PLAIN = {s for s, _ in SOLID_POOL}
NO_SUPPORT = {"minecraft:honey_block", "minecraft:hopper"}      # honey: B.supports says full cube, vanilla's shape is inset
CONTAINERS = ("chest", "barrel", "hopper", "dropper", "dispenser")
USABLE = ("repeater", "comparator", "note_block", "trapdoor", "fence_gate")   # right-click changes state (player-equivalent)

# kind -> weight
KINDS = {
    "wire": 24, "repeater": 8, "comparator": 6, "torch": 5, "wall_torch": 6, "lever": 3, "button": 1.2,
    "solid": 9, "slab": 1.2, "lamp": 4, "piston": 4, "sticky": 4, "observer": 3, "target": 1.5, "note": 0.8,
    "rail": 1.2, "hopper": 1.6, "dropper": 1.6, "dispenser": 0.6, "chest": 1.4, "barrel": 0.8, "bulb": 0.7,
    "trapdoor": 0.4, "gate": 0.4,
}
SAFE_IDS = {"redstone_wire", "repeater", "comparator", "redstone_torch", "redstone_wall_torch", "lever", "stone_button",
            "oak_button", "piston", "sticky_piston", "observer", "redstone_lamp", "target", "note_block", "hopper",
            "dropper", "dispenser", "chest", "barrel", "rail", "powered_rail", "activator_rail", "detector_rail",
            "copper_bulb", "waxed_copper_bulb", "oak_trapdoor", "oak_fence_gate", "smooth_stone_slab", "stone_slab"} \
    | {s.split(":")[1] for s in SAFE_PLAIN}


def wchoice(rng: random.Random, pairs):
    total = sum(w for _, w in pairs)
    r = rng.random() * total
    for item, w in pairs:
        r -= w
        if r <= 0:
            return item
    return pairs[-1][0]


def supports(state: str, face: str) -> bool:
    """Can this block hold a component attached to its `face`?  B.supports plus the corrections the fuzzer found:
    honey (inset shape) never supports; a lateral/ceiling attachment needs a full cube because vanilla's
    StructureTemplate.placeInWorld places full cubes first, then other blocks, then blocks with NBT, and a component
    placed before its support pops off (pure harness artefact, see docs/DIFF_FUZZ.md)."""
    if state.split("[")[0] in NO_SUPPORT or not B.supports(state, face):
        return False
    return face == "up" or bool(B.info(state.split("[")[0])["full_cube"])


def bad_attachments(cells: dict, nbt: Optional[dict] = None) -> set:
    """Cells holding a component that is floating, or whose support would not exist yet when vanilla places it
    (support carries NBT, or is not a full cube for a lateral/ceiling attachment)."""
    nbt = nbt or {}
    out = {p for p, _s, _w in B.floating(cells)}
    for pos, st in cells.items():
        a = B.attachment(st)
        if a is None:
            continue
        sp = add(pos, a[0])
        sst = cells.get(sp)
        if sst is None:
            continue
        if not supports(sst, OPP[a[0]]) or nbt.get(sp):
            out.add(pos)
    return out


def floating(world: dict):
    """(pos, state, why) of components that are not statically supported (see supports)."""
    for pos in sorted(bad_attachments(world)):
        yield pos, world[pos], "unsupported"


def add(p: Pos, d: str) -> Pos:
    v = VEC[d]
    return (p[0] + v[0], p[1] + v[1], p[2] + v[2])


def items_nbt(stacks: List[int]) -> Optional[str]:
    """Container SNBT, or None for an empty one: an empty `{Items:[]}` still makes vanilla place the block in its
    last pass (blocks with NBT), after the components that rest on it."""
    slots = [f'{{Slot:{i}b,id:"minecraft:cobblestone",count:{n}}}' for i, n in enumerate(stacks) if n > 0]
    return "{Items:[" + ",".join(slots) + "]}" if slots else None


class World:
    """Mutable cell map with the placement rules; also used by the mutation arm to add components."""

    def __init__(self, rng: random.Random, dims=(5, 3, 5)):
        self.rng = rng
        self.dims = dims
        self.cells: Dict[Pos, str] = {}
        self.nbt: Dict[Pos, str] = {}
        self.kind: Dict[Pos, str] = {}          # 'support' | 'floor' | component kind
        self.inputs: List[Pos] = []

    # ---- geometry
    def inbox(self, p: Pos) -> bool:
        return all(0 <= p[i] < self.dims[i] for i in range(3))

    def free(self, p: Pos) -> bool:
        return self.inbox(p) and p not in self.cells

    def put(self, p: Pos, state: str, kind: str, nbt: Optional[str] = None):
        self.cells[p] = state if state == "minecraft:redstone_wire" else B.canonical(state)
        self.kind[p] = kind
        if nbt:
            self.nbt[p] = nbt

    def components(self) -> List[Pos]:
        return [p for p, k in self.kind.items() if k not in ("support", "floor")]

    # ---- support planning (nothing is written until the whole plan is valid)
    def _need(self, plan: list, spos: Pos, face: str) -> bool:
        if not self.inbox(spos):
            return False
        cur = self.cells.get(spos)
        if cur is not None:
            return supports(cur, face) and spos not in self.nbt
        for q, st, _k, _n in plan:
            if q == spos:
                return supports(st, face)
        plan.append((spos, wchoice(self.rng, SOLID_POOL), "support", None))
        return True

    def _slot(self, p: Pos, plan: list) -> bool:
        return self.free(p) and all(q != p for q, *_ in plan)

    def plan_for(self, kind: str, p: Pos) -> Optional[list]:
        """[(pos, state, kind, nbt)] including any support blocks, or None when `kind` cannot go at `p`."""
        rng = self.rng
        if not self.free(p):
            return None
        plan: list = []
        st = None
        nbt = None

        def below():
            return self._need(plan, add(p, "down"), "up")

        if kind == "wire":
            if not below():
                return None
            st = "minecraft:redstone_wire"
        elif kind == "repeater":
            if not below():
                return None
            st = f"minecraft:repeater[delay={rng.randint(1, 4)},facing={rng.choice(H4)},locked=false,powered=false]"
        elif kind == "comparator":
            if not below():
                return None
            st = f"minecraft:comparator[facing={rng.choice(H4)},mode={rng.choice(['compare', 'subtract'])},powered=false]"
        elif kind == "torch":
            if not below():
                return None
            st = "minecraft:redstone_torch[lit=true]"
        elif kind == "wall_torch":
            f = rng.choice(H4)
            if not self._need(plan, add(p, OPP[f]), f):
                return None
            st = f"minecraft:redstone_wall_torch[facing={f},lit=true]"
        elif kind in ("lever", "button"):
            face = rng.choice(["floor", "wall", "wall", "wall", "ceiling"])
            f = rng.choice(H4)
            if face == "floor":
                ok = below()
            elif face == "ceiling":
                ok = self._need(plan, add(p, "up"), "down")
            else:
                ok = self._need(plan, add(p, OPP[f]), f)
            if not ok:
                return None
            if kind == "lever":
                st = f"minecraft:lever[face={face},facing={f},powered=false]"
            else:
                st = f"minecraft:{rng.choice(['stone_button', 'oak_button'])}[face={face},facing={f},powered=false]"
        elif kind == "solid":
            st = wchoice(rng, SOLID_POOL)
        elif kind == "slab":
            st = f"minecraft:smooth_stone_slab[type={rng.choice(['top', 'bottom', 'double'])},waterlogged=false]"
        elif kind == "lamp":
            st = "minecraft:redstone_lamp[lit=false]"
        elif kind in ("piston", "sticky"):
            st = f"minecraft:{'piston' if kind == 'piston' else 'sticky_piston'}[extended=false,facing={rng.choice(D6)}]"
        elif kind == "observer":
            st = f"minecraft:observer[facing={rng.choice(D6)},powered=false]"
        elif kind == "target":
            st = "minecraft:target[power=0]"
        elif kind == "note":
            st = "minecraft:note_block[instrument=harp,note=0,powered=false]"
        elif kind == "rail":
            if not below():
                return None
            name = wchoice(rng, [("rail", 1), ("powered_rail", 3), ("activator_rail", 1), ("detector_rail", 1)])
            st = f"minecraft:{name}[shape={rng.choice(['north_south', 'east_west'])}]"
        elif kind == "hopper":
            st = f"minecraft:hopper[enabled=true,facing={rng.choice(['down', 'down'] + H4)}]"
            nbt = items_nbt([rng.randint(1, 5)]) if rng.random() < 0.5 else None
        elif kind == "dropper":
            st = f"minecraft:dropper[facing={rng.choice(D6)},triggered=false]"
            nbt = items_nbt([rng.randint(1, 4), rng.randint(0, 2)]) if rng.random() < 0.7 else None
        elif kind == "dispenser":
            st = f"minecraft:dispenser[facing={rng.choice(D6)},triggered=false]"
        elif kind == "chest":
            st = f"minecraft:chest[facing={rng.choice(H4)},type=single,waterlogged=false]"
            nbt = items_nbt(rng.choice([[], [3], [64] * 3, [64] * 9, [64] * 13, [64] * 26 + [rng.randint(1, 63)], [64] * 27]))
        elif kind == "barrel":
            st = f"minecraft:barrel[facing={rng.choice(D6)},open=false]"
            nbt = items_nbt(rng.choice([[], [5], [64] * 4, [64] * 14, [64] * 27]))
        elif kind == "bulb":
            st = f"minecraft:{rng.choice(['copper_bulb', 'waxed_copper_bulb'])}[lit=false,powered=false]"
        elif kind == "trapdoor":
            st = f"minecraft:oak_trapdoor[facing={rng.choice(H4)},half={rng.choice(['bottom', 'top'])},open=false,powered=false,waterlogged=false]"
        elif kind == "gate":
            st = f"minecraft:oak_fence_gate[facing={rng.choice(H4)},in_wall=false,open=false,powered=false]"
        else:
            raise ValueError(kind)
        plan.append((p, st, kind, nbt))
        return plan

    def place(self, kind: str, p: Pos) -> bool:
        plan = self.plan_for(kind, p)
        if plan is None:
            return False
        for q, st, k, n in plan:
            self.put(q, st, k, n)
        if kind in ("lever", "button"):
            self.inputs.append(p)
        return True

    # ---- picking cells
    def neighbours(self) -> List[Pos]:
        out = set()
        for p in self.components():
            for d in D6:
                q = add(p, d)
                if self.free(q):
                    out.add(q)
            for d in H4:                                     # stepped wire
                for dy in (-1, 1):
                    q = add(p, d)
                    q = (q[0], q[1] + dy, q[2])
                    if self.free(q):
                        out.add(q)
        return sorted(out)

    def random_cell(self) -> Pos:
        return (self.rng.randrange(self.dims[0]), self.rng.randrange(self.dims[1]), self.rng.randrange(self.dims[2]))

    def pick_cell(self) -> Pos:
        if self.components() and self.rng.random() < 0.7:
            nb = self.neighbours()
            if nb:
                return self.rng.choice(nb)
        return self.random_cell()

    # ---- containers
    def fix_containers(self):
        """Items only where they cannot become item entities."""
        for p, st in list(self.cells.items()):
            bid, props = B.split_state(st)
            name = B.short(bid)
            if name == "dispenser":
                self.nbt.pop(p, None)
            elif name == "dropper":
                tgt = self.cells.get(add(p, props["facing"]))
                if not (tgt and B.short(B.split_state(tgt)[0]) in CONTAINERS):
                    self.nbt.pop(p, None)

    def valid(self) -> bool:
        if not all(simulatable(B.canonical(st)) for st in set(self.cells.values())):
            return False
        return not bad_attachments(self.cells, self.nbt)

    def to_blocks(self) -> list:
        return [(p[0], p[1], p[2], st, self.nbt.get(p)) for p, st in self.cells.items()]


def make_plan(rng: random.Random, world: World, use_any_p: float = 0.01):
    """(ticks, preroll, ops). Inputs are right-clicked at random ticks; sometimes another usable block is too."""
    ticks = wchoice(rng, [(24, 2), (40, 4), (64, 2), (100, 0.4)])
    preroll = 0 if rng.random() < 0.75 else 20
    ops: List[Tuple[int, Pos]] = []
    targets = list(world.inputs)
    if rng.random() < use_any_p:
        usable = [p for p, st in world.cells.items() if B.short(B.split_state(st)[0]).split("_")[-1] in USABLE
                  or B.short(B.split_state(st)[0]) in USABLE]
        targets += usable[:2]
    if targets:
        for _ in range(rng.randint(1, 6)):
            t = rng.randint(2, max(3, ticks - 8))
            p = rng.choice(targets)
            if all(not (t == t2 and p == p2) for t2, p2 in ops):
                ops.append((t, p))
    return ticks, preroll, sorted(ops)


def generate(rng: random.Random, origins=None, arm: str = "grammar") -> Case:
    """One random valid case. Deterministic in `rng`."""
    for _ in range(50):
        dims = (rng.randint(3, 6), rng.randint(2, 4), rng.randint(3, 6))
        w = World(rng, dims)
        if rng.random() < 0.5:
            fl = "minecraft:stone" if rng.random() < 0.8 else "minecraft:smooth_stone"
            for x in range(dims[0]):
                for z in range(dims[2]):
                    w.put((x, 0, z), fl, "floor")
        n_in = wchoice(rng, [(0, 0.1), (1, 0.5), (2, 0.3), (3, 0.1)])
        for _i in range(n_in):
            for _try in range(12):
                if w.place(rng.choice(["lever", "lever", "button"]), w.pick_cell()):
                    break
        target = min(15, max(3, 3 + int(rng.expovariate(1 / 5.0))))
        n = len(w.components())
        for _try in range(250):
            if n >= target:
                break
            kind = wchoice(rng, list(KINDS.items()))
            if w.place(kind, w.pick_cell()):
                n += 1
        w.fix_containers()
        if len(w.components()) < 3 or not w.valid():
            continue
        ticks, preroll, ops = make_plan(rng, w)
        origin = rng.choice(origins) if origins else (0, 100, 0)
        return Case(w.to_blocks(), ops, ticks, preroll, "place", tuple(origin), {"arm": arm})
    raise RuntimeError("generator failed to produce a valid case in 50 attempts")
