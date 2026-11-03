"""3D signal router for redstone nets (docs/ROUTER.md).

A net runs from one source pin to one or more sink pins as a tree of redstone dust (with supports) and repeaters.
A* over (cell, incoming direction, support kind) finds each branch; the rules it enforces are the ones verified in the
simulator (docs/T4.md "Physics facts", tests/unit/test_router.py):

* every dust/repeater stands on a support: an existing plain block or a new one (stone; a top slab where a stone block
  would touch a foreign component: slabs do not conduct). Dust on a slab cannot step DOWN (the signal dies);
* no new dust/repeater touches a foreign component or foreign dust (6-neighbourhood), no foreign dust sits one level
  up/down diagonally (it would connect), no neighbouring plain block may be strongly powered by a foreign component, and
  no new stone support touches a foreign component (the dust above powers it);
* climbing = dust staircase: the cell above the lower dust must stay air (a conductor there cuts the step), stepping
  down needs a full block under the upper dust and air above the lower one. These air cells are reserved for the rest
  of the routing session, so later nets cannot cut them;
* crossings are plain 3D routing: a net goes over another (a top-slab hop is the cheapest legal bridge);
* repeaters only on flat, straight, non-junction cells, chosen after routing so that the signal never drops to 0 and
  every sink gets at least its `need` (lib cells: `min_in_strength_per_port` from lib/<cell>.meta.json); `flow` = the
  direction the signal travels;
* nothing is written outside `box`, into `keep_clear` boxes or onto existing blocks.

`check_net` powers a routed net's source in Nucleation and verifies that every sink gets power and nothing outside the
net changes. Only stdlib + simulation.blockstates are imported at module level (Part.route runs inside the sandbox).
"""
import heapq
import itertools
import json
import os
import weakref
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from simulation.blockstates import normalize

Pos = Tuple[int, int, int]
DIRS = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0)}
OPP = {"north": "south", "south": "north", "east": "west", "west": "east", "up": "down", "down": "up"}
ALL6 = list(DIRS.values()) + [(0, 1, 0), (0, -1, 0)]
DUST = normalize("minecraft:redstone_wire")
TOP_SINKS = ("redstone_lamp",)    # device sinks that may also be fed by dust standing on top of them
STONE = normalize("minecraft:stone")
SLAB = normalize("minecraft:smooth_stone_slab[type=top]")
FLOOR = normalize("minecraft:smooth_stone")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_ACTIVE = ("redstone", "repeater", "comparator", "torch", "lever", "button", "pressure_plate", "lamp", "piston",
           "observer", "target", "dropper", "dispenser", "hopper", "note_block", "door", "trapdoor", "fence_gate", "rail",
           "tripwire", "daylight", "bulb", "tnt", "bell", "crafter", "lectern", "sculk", "lightning_rod", "trapped_chest",
           "moving_piston", "pad")
_NO_SUPPORT = ("pane", "fence", "wall", "carpet", "sign", "banner", "glass", "leaves", "ice", "bars", "chain", "air")


def add(p, d, k=1) -> Pos:
    return (p[0] + d[0] * k, p[1] + d[1] * k, p[2] + d[2] * k)


def name_of(state: str) -> str:
    return state.split("[", 1)[0].split(":")[-1]


def props_of(state: str) -> dict:
    if "[" not in state:
        return {}
    return dict(kv.split("=", 1) for kv in state.split("[", 1)[1].rstrip("]").split(",") if kv)


def is_dust(state: Optional[str]) -> bool:
    return state is not None and name_of(state) == "redstone_wire"


def is_active(state: Optional[str]) -> bool:
    """Anything that emits, receives or carries power (not a plain block)."""
    return state is not None and any(k in name_of(state) for k in _ACTIVE)


def holds(state: str) -> bool:
    """Can dust / a repeater stand on this block?"""
    n, p = name_of(state), props_of(state)
    if is_active(state) or any(k in n for k in _NO_SUPPORT):
        return False
    if n.endswith("slab"):
        return p.get("type") in ("top", "double")
    if n.endswith("stairs"):
        return p.get("half") == "top"
    return True


def full(state: str) -> bool:
    """A full conducting block (dust on it can step down)."""
    n = name_of(state)
    return holds(state) and not ((n.endswith("slab") and props_of(state).get("type") != "double") or n.endswith("stairs"))


def strongly_powers(pos: Pos, state: str) -> List[Pos]:
    """Blocks a foreign component may strongly power (they would in turn power our dust next to them)."""
    n, p = name_of(state), props_of(state)
    f = p.get("facing")
    if n in ("repeater", "comparator") and f in DIRS:
        return [add(pos, DIRS[OPP[f]])]
    if n in ("redstone_torch", "redstone_wall_torch"):
        return [add(pos, (0, 1, 0))]
    if n == "lever" or n.endswith("_button"):
        face = p.get("face", "wall")
        if face == "floor":
            return [add(pos, (0, -1, 0))]
        if face == "ceiling":
            return [add(pos, (0, 1, 0))]
        return [add(pos, DIRS[OPP[f]])] if f in DIRS else []
    if n == "observer" and f in DIRS:
        return [add(pos, DIRS[OPP[f]])]
    if n == "observer" and f in ("up", "down"):
        return [add(pos, (0, -1 if f == "up" else 1, 0))]
    return []


def repeater_state(flow: str, delay: int = 1) -> str:
    return normalize(f"minecraft:repeater[facing={OPP[flow]},delay={delay}]")


def dir_name(d) -> str:
    for k, v in DIRS.items():
        if v == (d[0], 0, d[2]):
            return k
    raise ValueError(f"not a horizontal direction: {d}")


# lib cells whose output strength follows an input strength (measured, docs/ROUTER.md): mux2's comparators pass the A/B
# strength through to Y (A at 9 -> Y at 9). Their data inputs must arrive strong, and Y is only as strong as that.
ANALOG = {"mux2": {"Y": ("A", "B")}}
ANALOG_NEED = 13

# blocks a redstone wire connects to from the side whatever their orientation (vanilla RedStoneWireBlock.shouldConnectTo:
# isSignalSource); repeaters connect along their axis, observers at their back
_WIRE_SOURCES = ("redstone_torch", "redstone_wall_torch", "lever", "_button", "redstone_block", "_pressure_plate", "comparator",
                 "daylight_detector", "target", "tripwire_hook", "lightning_rod", "sculk_sensor", "detector_rail", "lectern",
                 "trapped_chest")


def wire_connects(state: Optional[str], d: Tuple[int, int, int]) -> bool:
    """Does dust connect sideways to the neighbour `state` lying in direction d (horizontal unit vector) from the dust?"""
    if state is None:
        return False
    n, p = name_of(state), props_of(state)
    if n == "redstone_wire":
        return True
    f = p.get("facing")
    if n == "repeater":
        return f in DIRS and DIRS[f][0] * d[0] + DIRS[f][2] * d[2] != 0       # same axis
    if n == "observer":
        return f in DIRS and DIRS[f] == d
    return any(n == k or n.endswith(k) for k in _WIRE_SOURCES)


def entry_rule(get, pos: Pos, face: Optional[str]) -> str:
    """How may a net join the dust port at `pos` (route arrives from the `face` side)? get(p) -> state or None.
    'face': the port dust only does its job while its single connection is the face side. Joined from a side the dust bends and
    stops powering the block it points at (dust powers the block it points to and the block under it, nothing else), so a gate whose
    first element is a plain block fed by the port (`and2`, `not_gate`) is silently dead. 'any': a side join only adds a connection
    because (a) the neighbour inside the part connects to dust (dust, aligned repeater, comparator, lever, redstone torch, an
    observer's back), or (b) a repeater/comparator reads the block under the dust (dust always powers that block), as in
    `full_adder.A/CIN`, `xor2.B`, `half_adder.B`. Checked against the simulator for every lib part (docs/ROUTER.md)."""
    if face not in DIRS:
        return "any"
    inward = DIRS[OPP[face]]
    for dy in (0, 1, -1):
        q = add(add(pos, inward), (0, dy, 0))
        st = get(q)
        if dy == 0:
            if wire_connects(st, inward):
                return "any"
        elif is_dust(st):
            return "any"
    under = add(pos, (0, -1, 0))
    ust = get(under)
    if ust is not None and holds(ust):                   # a block under the dust is powered by it
        for d in DIRS.values():
            nb = get(add(under, d))
            if nb is not None and name_of(nb) in ("repeater", "comparator"):
                f = props_of(nb).get("facing")
                if f in DIRS and DIRS[f] == (-d[0], 0, -d[2]):      # its input side faces the block under the dust
                    return "any"
    return "face"


class RouteError(Exception):
    pass


@dataclass
class Pin:
    """A net terminal. kind: 'dust' (a dust port cell, the route attaches at pos+face on the same level), 'device'
    (lever / lamp / any component: dust next to it; a sink is entered moving straight toward it), 'pad' (a reserved
    cell that becomes a redstone block). face None = any free horizontal side. need = minimum strength at a sink pin
    (dust) ; level = strength a dust source carries at the pin (15 for lib outputs)."""
    pos: Pos
    face: Optional[str] = None
    kind: str = "dust"
    need: int = 1
    level: int = 15
    name: str = ""
    any_side: bool = False     # also allow the other horizontal sides (set by the router's fallback for dust pins)
    entry: str = "any"         # 'face': a dead gate unless the route arrives through `face` (see entry_rule); never relaxed
                               # to a side join unless the caller allows it
    why: str = ""              # where need / entry came from (error messages)

    def __post_init__(self):
        self.pos = tuple(int(v) for v in self.pos)
        if self.face is not None and self.face not in DIRS:
            raise RouteError(f"pin face must be one of {sorted(DIRS)} or None, got {self.face!r}")
        if self.kind not in ("dust", "device", "pad"):
            raise RouteError(f"pin kind must be dust/device/pad, got {self.kind!r}")
        if self.entry not in ("any", "face"):
            raise RouteError(f"pin entry must be 'any' or 'face', got {self.entry!r}")

    def label(self) -> str:
        return self.name or str(self.pos)

    def sides(self) -> List[str]:
        if self.face and not self.any_side:
            return [self.face]
        return ([self.face] if self.face else []) + [d for d in DIRS if d != self.face]


@dataclass
class Net:
    name: str
    src: Pin
    sinks: List[Pin]
    ok: bool = False
    reason: str = ""
    cells: Dict[Pos, str] = field(default_factory=dict)      # every block written (dust, repeaters, supports)
    parent: Dict[Pos, Optional[Pos]] = field(default_factory=dict)   # tree of dust/repeater cells (root parent = None)
    attach: Dict[str, Pos] = field(default_factory=dict)    # sink label -> route cell next to it
    air: set = field(default_factory=set)                   # cells that must stay air (staircase clearances)
    strength: Dict[Pos, int] = field(default_factory=dict)  # dust strength per route cell (repeaters: 15 out)
    sink_level: Dict[str, int] = field(default_factory=dict)
    delay: Dict[str, int] = field(default_factory=dict)     # repeater ticks from the source to each sink
    explored: int = 0
    note: str = ""
    restore: bool = False
    allow_side: bool = False
    side_joined: List[str] = field(default_factory=list)    # pin labels joined from a side other than their face
    warnings: List[str] = field(default_factory=list)       # risky: a dead gate is likely (build WARNING)
    infos: List[str] = field(default_factory=list)          # harmless side joins (listed by `nets`, one summary warning)

    @property
    def repeaters(self) -> List[Pos]:
        return [p for p, s in self.cells.items() if name_of(s) == "repeater"]

    @property
    def route(self) -> List[Pos]:
        return list(self.parent)

    def summary(self) -> str:
        if not self.ok:
            return f"net {self.name}: UNROUTED - {self.reason}"
        lv = ", ".join(f"{k}={v}" for k, v in self.sink_level.items())
        return (f"net {self.name}: {len(self.parent)} route cells, {len(self.cells)} blocks, {len(self.repeaters)} repeaters, "
                f"sink strength {lv}")


class Router:
    """Routing session over a world of existing blocks.

    world: {pos: state} of everything already placed (modules, devices, floor); box: inclusive (x1,y1,z1,x2,y2,z2) every
    write must stay in; keep_clear: boxes never written (hallways, door cells); reserved: cells that must stay air (pads);
    floor_y: the plane y == floor_y is a solid floor that exists (not written; nothing below it is used)."""

    def __init__(self, world=None, box=None, keep_clear=(), reserved=(), floor_y=None, turn_cost=0.4, step_cost=1.5,
                 slab_cost=0.3, support_cost=0.5, near_cost=0.15, zone_cost=3.0, max_states=400_000, first_try_states=40_000):
        self.world: Dict[Pos, str] = {tuple(p): s for p, s in (world or {}).items()}
        self.owner: Dict[Pos, str] = {}
        self.box = _box(box) if box is not None else None
        self.keep_clear = [_box(b) for b in keep_clear]
        self.air = {tuple(p) for p in reserved}
        self.reserved = set(self.air)
        self.ban: set = set()                   # cells no new route may use (paste-stability repair)
        self.paste_issues: List[str] = []
        self.floor_y = floor_y
        self.turn_cost, self.step_cost, self.slab_cost, self.near_cost = turn_cost, step_cost, slab_cost, near_cost
        self.support_cost = support_cost
        self.max_states, self.first_try_states = max_states, first_try_states
        self.nets: Dict[str, Net] = {}
        self._current = ""
        self.zone: Dict[Pos, dict] = {}         # cells around pins of nets not routed yet -> {net: weight} (soft keep-out)
        self.zone_cost = zone_cost
        self.hot: set = set()
        for p, s in self.world.items():
            self._note_hot(p, s)

    @classmethod
    def from_contract(cls, contract, blocks=(), **kw):
        """Router over a task: fixed scenery + `blocks` (already built), build_box, keep_clear/no_write, pad cells reserved."""
        from env.runner import fixed_cells
        fixed = fixed_cells(contract)
        world = {p: s for p, s in fixed.items() if name_of(s) != "air"}
        for b in blocks:
            p, s = (tuple(b[:3]), b[3]) if not isinstance(b, dict) else ((b["x"], b["y"], b["z"]), b["state"])
            world[p] = normalize(s)
        reserved = [p for p, s in fixed.items() if name_of(s) == "air"]
        return cls(world, box=contract.build_box, keep_clear=list(contract.keep_clear) + list(contract.no_write),
                   reserved=reserved, **kw)

    # -- world queries
    def _note_hot(self, p, s):
        if is_active(s):
            self.hot.update(strongly_powers(p, s))

    def state(self, p) -> Optional[str]:
        s = self.world.get(p)
        if s is None and self.floor_y is not None and p[1] <= self.floor_y:
            return FLOOR if p[1] == self.floor_y else "minecraft:bedrock"
        return s

    def writable(self, p) -> bool:
        if self.box is not None and not _inside(p, self.box):
            return False
        if self.floor_y is not None and p[1] <= self.floor_y:
            return False
        return not any(_inside(p, b) for b in self.keep_clear)

    def free(self, p, extra=()) -> bool:
        return self.state(p) is None and p not in self.air and p not in extra and p not in self.ban and self.writable(p)

    # -- the move rules
    def _dust_ok(self, n, prev, pins, net_cells, slab_support) -> bool:
        """May route dust/repeater stand at n (prev = route cell it comes from; pins = foreign cells it may touch)?"""
        for d in ALL6:
            q = add(n, d)
            if q == prev or q in pins:
                continue
            if q in net_cells:
                return False                       # own tree: only the cell it comes from may touch it
            s = self.state(q)
            if s is None:
                continue
            if is_active(s):
                return False
            if q in self.hot and not (d == (0, -1, 0) and slab_support):
                return False
        for d in DIRS.values():
            for dy in (1, -1):
                q = add(add(n, d), (0, dy, 0))
                if q == prev or q in pins:
                    continue
                if q in net_cells or is_dust(self.state(q)):
                    return False
        return True

    def _support(self, n, pins, extra):
        """-> list of (support_kind, cost) for dust at n: 'have' (existing block), 'stone', 'slab'."""
        s = add(n, (0, -1, 0))
        st = self.state(s)
        if s in pins and name_of(st) in TOP_SINKS:
            return [("have", 0.0)]                 # dust on top of a lamp (a conductor) powers it
        if st is not None:
            if not holds(st) or s in self.hot:
                return []
            for d in ALL6:
                q = add(s, d)
                if q != n and q not in pins and is_active(self.state(q)):
                    return []
            return [("have" if full(st) else "have_slab", 0.0)]
        if not self.free(s, extra):
            return []
        out = [("slab", self.support_cost + self.slab_cost)]
        if all(q == n or q in pins or not is_active(self.state(q)) for q in (add(s, d) for d in ALL6)):
            out.insert(0, ("stone", self.support_cost))
        return out

    def _near(self, n, net_name) -> float:
        c = 0.0
        z = self.zone.get(n)
        if z:
            c += self.zone_cost * max((w for k, w in z.items() if k != net_name), default=0.0)
        if self.near_cost:
            for dx, dy, dz in itertools.product((-1, 0, 1), repeat=3):
                q = (n[0] + dx, n[1] + dy, n[2] + dz)
                if q != n and q in self.owner:
                    return c + self.near_cost
        return c

    def reserve_pins(self, name: str, pins: List[Pin], weight: float = 1.0, radius: int = 2):
        """Announce the pins of a net that will be routed later: other nets pay `zone_cost * weight * f` to pass within
        `radius` cells of them (f = 3 next to the pin, 1.5 two cells away, halved two levels up): dust there would take
        the attach cells of the pending net (dust may not touch foreign dust). Cleared when the net is routed."""
        for pin in pins:
            for dx, dz in itertools.product(range(-radius, radius + 1), repeat=2):
                for dy in (0, 1, 2):
                    f = (3.0 if max(abs(dx), abs(dz)) <= 1 else 1.5) * (0.5 if dy == 2 else 1.0)
                    zz = self.zone.setdefault((pin.pos[0] + dx, pin.pos[1] + dy, pin.pos[2] + dz), {})
                    zz[name] = max(zz.get(name, 0.0), weight * f)
            if pin.face:      # a one-sided port: keep the cells around its attach cell free (dust there would block it)
                at = add(pin.pos, DIRS[pin.face])
                for dx, dy, dz in itertools.product((-1, 0, 1), repeat=3):
                    q = (at[0] + dx, at[1] + dy, at[2] + dz)
                    if q != pin.pos:
                        zz = self.zone.setdefault(q, {})
                        zz[name] = max(zz.get(name, 0.0), weight * 5.0)

    def _release(self, name: str):
        for p in [p for p, v in self.zone.items() if name in v]:
            del self.zone[p][name]
            if not self.zone[p]:
                del self.zone[p]

    # -- A*
    def _search(self, starts, goals, net_cells, occupied, net_air, sink_pin):
        """starts: [(cell, d_in, support_kind)] already validated; goals: {cell: required arrival dir or None};
        net_cells: this net's route cells so far, occupied: + its new supports and staircase air.
        Returns ([(cell, support_kind, d_in, clear_cell)], explored) or (None, explored)."""
        gx = list(goals)

        def h(c):
            return min(max(abs(c[0] - g[0]) + abs(c[2] - g[2]), abs(c[1] - g[1])) + abs(c[1] - g[1]) * (self.step_cost - 1)
                       for g in gx)

        tie = itertools.count()
        openh, best, came = [], {}, {}
        memo: Dict[Tuple[Pos, Pos], list] = {}
        net_name = self._current
        for cell, d_in, sk in starts:
            key = (cell, d_in, sk)
            best[key] = 0.0
            came[key] = None
            heapq.heappush(openh, (h(cell), next(tie), 0.0, key))
        explored = 0
        while openh:
            f, _, g, key = heapq.heappop(openh)
            if g > best.get(key, 1e18):
                continue
            c, d_in, sk = key
            req = goals.get(c, False)
            if req is not False and (req is None or req == d_in) and came[key] is not None:
                return self._unwind(came, key), explored
            explored += 1
            if explored > self.max_states:
                break
            for dn, d in DIRS.items():
                if d_in is not None and dn == OPP[d_in]:
                    continue
                for dy in (0, 1, -1):
                    n = (c[0] + d[0], c[1] + dy, c[2] + d[2])
                    goal_here = n in goals
                    if goal_here and goals[n] is not None and goals[n] != dn:
                        continue
                    if dy == -1 and sk not in ("stone", "have"):
                        continue                       # dust on a slab cannot step down
                    clear = add(c, (0, 1, 0)) if dy == 1 else add(c, d) if dy == -1 else None   # above the lower dust
                    opts = memo.get((n, c))
                    if opts is None:
                        opts = []
                        if self.free(n, occupied) and not (clear and not (self.free(clear, occupied) or (
                                clear in net_air and self.state(clear) is None))):
                            pins = sink_pin if goal_here else ()
                            for nk, kc in self._support(n, pins, occupied if not clear else occupied | {clear}):
                                if self._dust_ok(n, c, pins, net_cells, nk in ("slab", "have_slab")):
                                    opts.append((nk, kc + self._near(n, net_name)))
                        memo[(n, c)] = opts
                    for nk, kc in opts:
                        cost = 1.0 + (self.step_cost - 1 if dy else 0) + (self.turn_cost if d_in and dn != d_in else 0) + kc
                        nkey = (n, dn, nk)
                        ng = g + cost
                        if ng < best.get(nkey, 1e18):
                            best[nkey] = ng
                            came[nkey] = (key, dy, clear)
                            heapq.heappush(openh, (ng + h(n), next(tie), ng, nkey))
        return None, explored

    def _search_clean(self, starts, goals, net_cells, occupied, net_air, sink_pin, tries: int = 8):
        """_search, then ban cells where the new path touches itself or the tree (A* states do not know the path) and retry."""
        ban, total = set(), 0
        for _ in range(tries):
            path, ex = self._search(starts, goals, net_cells, occupied | ban, net_air, sink_pin)
            total += ex
            if path is None:
                return None, total
            bad = self._touch(path, net_cells, starts)
            if not bad:
                return path, total
            ban |= bad
        return None, total

    @staticmethod
    def _touch(path, net_cells, starts):
        cells = [p[0] for p in path]
        idx = {c: i for i, c in enumerate(cells)}
        start_cells = {s[0] for s in starts}
        bad = set()
        for i, c in enumerate(cells):
            for d in DIRS.values():
                for dy in (0, 1, -1):
                    q = (c[0] + d[0], c[1] + dy, c[2] + d[2])
                    j = idx.get(q)
                    if j is not None and abs(j - i) > 1:
                        bad.add(cells[max(i, j)])
                    elif q in net_cells and not (i == 0 or (i == 1 and cells[0] in start_cells and q == cells[0])):
                        bad.add(c)
            if i and c in net_cells:
                bad.add(c)
        # a support (cell below a path cell) filling another path cell's staircase air is caught later by _self_check
        return bad

    @staticmethod
    def _unwind(came, key):
        out = []
        while key is not None:
            prev = came[key]
            out.append((key[0], key[2], key[1], prev[2] if prev else None))
            key = prev[0] if prev else None
        return out[::-1]   # [(cell, support_kind, d_in, clear_cell)]

    # -- public
    def route(self, name: str, src: Pin, sinks: List[Pin], strict: bool = False, restore: bool = False,
              allow_side: bool = False) -> Net:
        """Route one net; on success the cells are committed to the session. Returns the Net (ok, reason, cells, ...).
        strict=True raises RouteError instead of returning an unrouted net. restore=True lets a repeater stand on the last
        cell before a sink (a restorer) when the strength would otherwise be too low. A dust pin with entry='face' is
        joined only through its face unless allow_side=True."""
        if name in self.nets:
            if self.nets[name].ok:
                raise RouteError(f"net {name!r} already routed")
            del self.nets[name]
        net = Net(name, src, list(sinks), restore=restore, allow_side=allow_side)
        self._current = name
        try:
            cap = self.max_states
            self.max_states = min(cap, self.first_try_states) if any(p.kind == "dust" and p.face for p in [src, *sinks]) else cap
            try:
                self._route(net)
            except RouteError as e1:
                self.max_states = cap
                # fallback: a dust port may also be joined from another side (its face is only the preferred side), except
                # ports whose dust would then no longer power what it feeds (entry='face') unless the caller allows it
                pins = [src] + list(sinks)
                movable = [p for p in pins if p.kind == "dust" and p.face and not p.any_side and (p.entry != "face" or allow_side)]
                if not movable:
                    stuck = [p.label() for p in pins if p.kind == "dust" and p.face and p.entry == "face"]
                    if stuck:
                        raise RouteError(f"{e1}; port{'s' if len(stuck) > 1 else ''} {', '.join(stuck)} must be entered through "
                                         f"{'its' if len(stuck) == 1 else 'their'} face (the dust feeds a non-conducting block: a side join "
                                         "leaves the gate dead); free the cells in front of it, or pass allow_side=True") from None
                    raise
                relax = [replace(p, any_side=True) if p in movable else p for p in pins]
                net = Net(name, relax[0], relax[1:], restore=restore, allow_side=allow_side)
                try:
                    self._route(net)
                except RouteError as e2:
                    raise RouteError(f"{e1} (other sides of the dust ports: {e2})") from None
            finally:
                self.max_states = cap
            net.ok = True
            self._side_report(net)
            if net.side_joined:
                net.note = "a dust port was joined from a side other than its face: " + ", ".join(net.side_joined)
        except RouteError as e:
            net.ok, net.reason, net.cells, net.parent, net.air = False, f"net {name!r}: {e}", {}, {}, set()
            if strict:
                raise RouteError(net.reason) from None
        if net.ok:
            self._commit(name, net)
        self.nets[name] = net
        return net

    def _commit(self, name: str, net: Net):
        for p, s in net.cells.items():
            self.world[p] = s
            self.owner[p] = name
            self._note_hot(p, s)
        self.air |= net.air
        self._release(name)

    def _side_report(self, net: Net):
        """Which dust ports with a face did the route join from another side? -> net.side_joined, net.warnings."""
        roots = [c for c, par in net.parent.items() if par is None]
        for pin in [net.src] + net.sinks:
            if pin.kind != "dust" or not pin.face:
                continue
            front = add(pin.pos, DIRS[pin.face])
            adj = [c for c in net.parent if c != pin.pos and abs(c[0] - pin.pos[0]) + abs(c[2] - pin.pos[2]) == 1 and c[1] == pin.pos[1]]
            if pin is net.src:
                adj = [c for c in adj if c in roots] or adj
            if adj and front not in adj:
                side = next((k for k, v in DIRS.items() if add(pin.pos, v) == adj[0]), "?")
                net.side_joined.append(pin.label())
                if pin.entry == "face":
                    net.warnings.append(f"net {net.name!r}: port {pin.label()} joined from the {side} side but its face is {pin.face}: "
                                        f"its dust feeds a non-conducting block, so the gate is probably dead (allow_side=True was set)")
                else:
                    net.infos.append(f"port {pin.label()} joined from the {side} side (face {pin.face}); harmless, "
                                     "its inward neighbour connects to the dust")

    # -- several nets at once
    def _constraint(self, src: Pin, sinks: List[Pin]) -> int:
        """Free attach cells at the tightest pin of a net (0-4; a one-sided dust port counts its face only)."""
        best = 4
        for pin in [src] + list(sinks):
            if pin.kind == "pad":
                continue
            sides = [pin.face] if (pin.face and (pin.entry == "face" or not pin.any_side) and pin.kind == "dust") else list(DIRS)
            best = min(best, sum(1 for sd in sides if self.free(add(pin.pos, DIRS[sd]))))
        return best

    def order_nets(self, specs, order: str = "auto"):
        """specs: [(name, src, sinks, opts)] -> the same list in routing order. 'given' keeps it; 'short' = least total
        Manhattan length first; 'constrained' = fewest free attach cells at a pin first; 'auto' = nets with a pin that has
        at most one free attach cell first, then shortest first (trees last among equals)."""
        if order not in ("auto", "short", "constrained", "given"):
            raise RouteError(f"order must be auto, short, constrained or given, got {order!r}")
        if order == "given":
            return list(specs)

        def length(sp):
            return sum(_dist(sp[1].pos, k.pos) for k in sp[2])

        def key(sp):
            c = self._constraint(sp[1], sp[2])
            if order == "short":
                return (length(sp), len(sp[2]))
            if order == "constrained":
                return (c, length(sp))
            return (0 if c <= 1 else 1, length(sp) + 6 * (len(sp[2]) - 1))
        return sorted(specs, key=key)

    def route_all(self, specs, order: str = "auto", rounds: int = 2, max_retries: int = 16) -> List[Net]:
        """Route a batch of nets: specs = [(name, src, sinks, opts)], opts = {"restore": bool, "allow_side": bool}.
        Pins of nets still pending are announced to the search (soft keep-out) so early nets do not wall them in. Nets that
        fail are retried after ripping up the nets of this batch next to their pins (the nearest blocker alone, then the
        two or three nearest), and the batch keeps the best state; the others are re-routed behind them. Returns the Nets in
        the order of `specs`."""
        specs = list(specs)
        names = [sp[0] for sp in specs]
        if len(set(names)) != len(names):
            raise RouteError("route_all: duplicate net names")
        by_name = {sp[0]: sp for sp in specs}
        self._batch_specs = specs
        for sp in specs:
            self.reserve_pins(sp[0], [sp[1]] + list(sp[2]), 1.0, 2)
        for sp in self.order_nets(specs, order):
            self.route(sp[0], sp[1], sp[2], strict=False, **sp[3])
        retries = 0
        for _ in range(rounds):
            failed = [n for n in names if not self.nets[n].ok]
            if not failed or retries >= max_retries:
                break
            gained = False
            for f in failed:
                if retries >= max_retries:
                    break
                for k in (1, 2, 3):
                    retries += 1
                    if self._ripup_retry(by_name[f], names, k):
                        gained = True
                        break
                    if retries >= max_retries:
                        break
            if not gained:
                break
        for n in names:
            self._release(n)
        return [self.nets[n] for n in names]

    def blockers(self, pins, batch=None, radius: int = 2) -> List[str]:
        """Nets (of `batch`, default all) owning route cells within `radius` of the pins, nearest first."""
        score: Dict[str, float] = {}
        for pin in pins:
            for p, o in self.owner.items():
                if (batch is not None and o not in batch) or o not in self.nets or not self.nets[o].ok:
                    continue
                d = max(abs(p[0] - pin.pos[0]), abs(p[2] - pin.pos[2]))
                if d <= radius and abs(p[1] - pin.pos[1]) <= 2:
                    score[o] = score.get(o, 0.0) + 1.0 / (1 + d)
        return [n for n, _ in sorted(score.items(), key=lambda kv: -kv[1])]

    def _ripup_retry(self, sp, batch, k: int) -> bool:
        name, src, sinks, opts = sp
        bl = self.blockers([src] + list(sinks), set(batch) - {name})[:k]
        if not bl:
            return False
        saved = {n: self.nets[n] for n in bl}
        failed_old = self.nets.get(name)
        cap, first = self.max_states, self.first_try_states
        self.max_states, self.first_try_states = min(cap, 80_000), min(first, 30_000)   # retries must stay cheap
        try:
            return self._ripup_try(sp, bl, saved, failed_old)
        finally:
            self.max_states, self.first_try_states = cap, first

    def _ripup_try(self, sp, bl, saved, failed_old) -> bool:
        name = sp[0]
        for n in bl:
            self.unroute(n)
        self._release(name)
        done: List[str] = []
        ok = True
        for n in [name] + bl:       # the net that failed goes first now; the blockers route around it
            spec = sp if n == name else next(x for x in self._batch_specs if x[0] == n)
            self.nets.pop(n, None)
            net = self.route(n, spec[1], spec[2], strict=False, **spec[3])
            if not net.ok:
                ok = False
                break
            done.append(n)
        if ok:
            return True
        for n in done:
            self.unroute(n)
        for n, old in saved.items():
            self.nets.pop(n, None)
            self.nets[n] = old
            for p, st in old.cells.items():
                self.world[p] = st
                self.owner[p] = n
                self._note_hot(p, st)
            self.air |= old.air
        if failed_old is not None:
            self.nets[name] = failed_old
        return False

    def explain(self, pins, reason: str = "", batch=None, rx: int = 9, rz: int = 5) -> str:
        """Why a net cannot get through: who holds the cells next to its pins + a small map of the first pin named in
        `reason` (else the last pin)."""
        pin = next((p for p in pins if p.label() and p.label() in reason), pins[-1])
        held = []
        for sd in ([pin.face] if pin.face else list(DIRS)):
            g = add(pin.pos, DIRS[sd])
            o = self.owner.get(g)
            st = self.state(g)
            if o:
                held.append(f"{sd} cell {g}: net {o!r}")
            elif st:
                held.append(f"{sd} cell {g}: {name_of(st)}")
            elif g in self.air:
                held.append(f"{sd} cell {g}: reserved air (a staircase or pad)")
            elif not self.writable(g):
                held.append(f"{sd} cell {g}: outside the box / keep_clear")
            else:
                near = {self.owner[q] for q in [add(g, d) for d in ALL6] + [add(add(g, d), (0, dy, 0)) for d in DIRS.values() for dy in (1, -1)]
                        if q in self.owner and q != pin.pos}
                if near:
                    held.append(f"{sd} cell {g}: free, but dust there would touch net(s) {', '.join(repr(n) for n in sorted(near))}")
        bl = self.blockers(pins, batch)
        lines = []
        if held:
            lines.append(f"next to {pin.label()}: " + "; ".join(held))
        if bl:
            lines.append("blocking nets nearby: " + ", ".join(repr(n) for n in bl[:6]))
        lines += self.area_map(pin.pos, rx, rz)
        return "\n".join(lines)

    def area_map(self, center: Pos, rx: int = 9, rz: int = 5) -> List[str]:
        """ASCII map at the level of `center` (x right, z down): @ pin, letters = nets, # other blocks, ~ not writable / reserved."""
        y = center[1]
        letters: Dict[str, str] = {}
        rows = []
        for z in range(center[2] - rz, center[2] + rz + 1):
            row = []
            for x in range(center[0] - rx, center[0] + rx + 1):
                p = (x, y, z)
                o = self.owner.get(p)
                if p == tuple(center):
                    row.append("@")
                elif o:
                    ch = letters.setdefault(o, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"[len(letters) % 52])
                    row.append(ch)
                elif self.state(p):
                    row.append("#")
                elif p in self.air or not self.writable(p):
                    row.append("~")
                else:
                    row.append(".")
            rows.append("".join(row))
        head = f"map y={y}, x {center[0] - rx}..{center[0] + rx}, z {center[2] - rz}..{center[2] + rz} (@ = pin)"
        leg = "  ".join(f"{c}={n}" for n, c in letters.items())
        return [head] + ["  " + r for r in rows] + [f"  legend: {leg + '  ' if leg else ''}#=block ~=reserved/outside/keep_clear .=free"]

    def unroute(self, name: str) -> Net:
        """Rip up a routed net: its cells leave the world, its staircase air cells are released."""
        net = self.nets.pop(name)
        for p in net.cells:
            if self.owner.get(p) == name:
                del self.owner[p]
                self.world.pop(p, None)
        self.air = set(self.reserved).union(*[n.air for n in self.nets.values() if n.ok])
        self.hot = set()
        for p, st in self.world.items():
            self._note_hot(p, st)
        return net

    def world_with_floor(self) -> Dict[Pos, str]:
        w = dict(self.world)
        if self.floor_y is not None:
            for x, _, z in list(w):
                w.setdefault((x, self.floor_y, z), FLOOR)
        return w

    def repair_paste(self, extra=None, rounds: int = 3, radius: int = 3) -> Tuple[bool, List[str]]:
        """Nucleation's engine placement (env/sim.compile_cells, y-x-z order) mis-shapes some dust next to dust placed
        later at dx +1..+3, dz +-1..2 (docs/ROUTER.md), and then the env's paste_stable invariant (R1) fails. Check the
        whole world; for every cell that does not come back unchanged, rip up the nets owning route cells within
        `radius` of it, ban those cells and route the nets again (old route restored if that fails). -> (ok, details)."""
        details: List[str] = []
        for _ in range(rounds + 1):
            w = self.world_with_floor()
            if extra:
                w.update(extra)
            ok, details, cells = paste_diff(w)
            if ok:
                self.paste_issues = []
                return True, []
            hit: Dict[str, set] = {}
            for c in cells:
                for p, o in self.owner.items():
                    if o in self.nets and abs(p[0] - c[0]) <= radius and abs(p[2] - c[2]) <= radius and abs(p[1] - c[1]) <= 1 \
                            and p in self.nets[o].parent:
                        hit.setdefault(o, set()).add(p)
            if not hit:
                break
            for name, near in hit.items():
                old = self.unroute(name)
                self.ban |= near
                new = self.route(name, old.src, old.sinks, restore=old.restore, allow_side=old.allow_side)
                self.ban -= near
                if not new.ok:      # keep the old route rather than losing the net
                    self.unroute(name)
                    self.nets[name] = old
                    for p, st in old.cells.items():
                        self.world[p] = st
                        self.owner[p] = name
                        self._note_hot(p, st)
                    self.air |= old.air
        self.paste_issues = details
        return False, details

    def _route(self, net: Net):
        src = net.src
        if not net.sinks:
            raise RouteError("no sinks")
        tree: Dict[Pos, Optional[Pos]] = {}
        din: Dict[Pos, str] = {}            # route cell -> direction the signal enters it
        support: Dict[Pos, str] = {}        # route cell -> support kind
        new_support: Dict[Pos, str] = {}    # support cell -> state
        nair: set = set()
        src_pins = {src.pos}
        sinks = sorted(net.sinks, key=lambda s: _dist(s.pos, src.pos))
        for sink in sinks:
            if sink.pos == src.pos:
                raise RouteError(f"sink {sink.label()} is the source cell")
            goals = {}
            why = []
            for side in sink.sides():
                g = add(sink.pos, DIRS[side])
                if g in tree:
                    why.append(f"{side}: {g} already on this net")
                    continue
                if not self.free(g, tree):
                    why.append(f"{side}: {g} {'holds ' + name_of(self.state(g)) if self.state(g) else 'is outside the box / reserved'}")
                    continue
                goals[g] = OPP[side] if sink.kind != "dust" else None
            top = add(sink.pos, (0, 1, 0))
            if sink.kind == "device" and name_of(self.state(sink.pos) or "") in TOP_SINKS and top not in tree and self.free(top, tree):
                goals[top] = None
            if not goals:
                raise RouteError(f"sink {sink.label()}: no free cell to attach ({'; '.join(why)})")
            if not tree:
                starts, why = [], []
                for side in src.sides():
                    r0 = add(src.pos, DIRS[side])
                    if not self.free(r0):
                        why.append(f"{side}: {r0} {'holds ' + name_of(self.state(r0)) if self.state(r0) else 'is outside the box / reserved'}")
                        continue
                    pins = src_pins | ({sink.pos} if r0 in goals else set())
                    for sk, _ in self._support(r0, pins, ()):
                        if self._dust_ok(r0, src.pos, pins, {}, sk in ("slab", "have_slab")):
                            starts.append((r0, side, sk))
                    if not any(s[0] == r0 for s in starts):
                        why.append(f"{side}: {r0} would touch a foreign component / dust or has no legal support")
                if not starts:
                    raise RouteError(f"source {src.label()}: no legal first cell ({'; '.join(why)})")
                # a start that is itself a goal is a one-cell route
                direct = [s for s in starts if s[0] in goals and (goals[s[0]] is None or goals[s[0]] == s[1])]
                if direct:
                    r0, side, sk = direct[0]
                    path = [(r0, sk, side, None)]
                else:
                    path, ex = self._search_clean(starts, goals, set(), set(), nair, {sink.pos})
                    net.explored += ex
                    if path is None:
                        raise RouteError(f"sink {sink.label()}: no legal path ({ex} states explored"
                                         f"{', search limit hit' if ex > self.max_states else ''})")
                prev = None
            else:
                starts = [(c, din[c], support[c]) for c in tree if c not in net.attach.values()]
                path, ex = self._search_clean(starts, goals, set(tree), set(tree) | set(new_support) | nair, nair, {sink.pos})
                net.explored += ex
                if path is None:
                    raise RouteError(f"sink {sink.label()}: no legal branch from the routed tree ({ex} states explored"
                                     f"{', search limit hit' if ex > self.max_states else ''})")
                prev = path[0][0]
                path = path[1:]
            for cell, sk, d_in, clear in path:
                tree[cell] = prev
                din[cell] = d_in
                support[cell] = sk
                below = add(cell, (0, -1, 0))
                if sk == "stone":
                    new_support[below] = STONE
                elif sk == "slab":
                    new_support[below] = SLAB
                if clear:
                    nair.add(clear)
                prev = cell
            net.attach[sink.label()] = prev
        for p in new_support:
            if p in tree:
                raise RouteError(f"internal: support {p} collides with the route")
        self._self_check(tree, src_pins | {s.pos for s in net.sinks}, nair, new_support)
        net.parent, net.air = tree, nair
        reps = self._repeaters(net, tree, support)
        net.cells = dict(new_support)
        for c, par in tree.items():
            net.cells[c] = repeater_state(dir_name(_sub(c, par if par is not None else src.pos))) if c in reps else DUST
        self._strengths(net, reps)

    def _self_check(self, tree, pins, nair, new_support):
        """Own-net adjacency the A* cannot see (a branch running next to an older part of its tree, a support filling a
        reserved staircase air cell)."""
        for p in new_support:
            if p in nair:
                raise RouteError(f"route blocks its own staircase at {p}; try another box or pin side")
        kids = {}
        for c, par in tree.items():
            kids.setdefault(par, []).append(c)
        for c, par in tree.items():
            linked = set(kids.get(c, [])) | ({par} if par is not None else set())
            for d in DIRS.values():
                for dy in (0, 1, -1):
                    q = add(add(c, d), (0, dy, 0))
                    if q in tree and q not in linked:
                        raise RouteError(f"route touches itself at {c} / {q}")

    def _repeaters(self, net, tree, support) -> set:
        """Choose repeater cells (bottom-up, as far upstream as the strength allows) -> set of cells."""
        src = net.src
        kids: Dict[Pos, List[Pos]] = {c: [] for c in tree}
        roots = []
        for c, par in tree.items():
            if par is None:
                roots.append(c)
            else:
                kids[par].append(c)
        need: Dict[Pos, int] = {}
        for s in net.sinks:
            c = net.attach[s.label()]
            need[c] = max(need.get(c, 1), s.need + 1 if s.kind == "dust" else 1)
        order, todo = [], list(roots)
        while todo:
            c = todo.pop()
            order.append(c)
            todo.extend(kids[c])
        depth = {}
        for c in order:
            depth[c] = 0 if tree[c] is None else depth[tree[c]] + 1
        s0 = 15 if src.kind in ("device", "pad") else src.level - 1

        leaf_sink = {}
        if net.restore:    # a restorer on the last cell: the repeater faces the sink pin and powers it with a fresh 15
            at = {}
            for sk in net.sinks:
                at.setdefault(net.attach[sk.label()], []).append(sk)
            leaf_sink = {c: v[0] for c, v in at.items() if len(v) == 1 and not kids[c]}

        def eligible(c):
            par = tree[c]
            ks = kids[c]
            if c in leaf_sink:
                if par is None and src.kind != "dust":
                    return False
                a, b = par if par is not None else src.pos, leaf_sink[c].pos
                return a[1] == c[1] == b[1] and _sub(c, a) == _sub(b, c)
            if len(ks) != 1 or c in need:
                return False
            a = par if par is not None else src.pos
            b = ks[0]
            if not (a[1] == c[1] == b[1]) or _sub(c, a) != _sub(b, c):
                return False
            if par is None and src.kind != "dust":
                return False   # a repeater right next to a lever/pad: keep the first cell dust
            return True

        elig = {c for c in tree if eligible(c)}

        def up_eligible(c):
            k, p = 0, tree[c]
            while p is not None:
                k += 1
                if p in elig:
                    return k
                p = tree[p]
            return None

        reps, req = set(), {}
        for c in reversed(order):
            r = need.get(c, 1)
            for ch in kids[c]:
                r = max(r, 1 if ch in reps else req[ch] + 1)
            if c in elig:
                k = up_eligible(c)
                too_weak = (r + k - 1 > 15) if k is not None else (r > s0 - depth[c])
                if too_weak:
                    if max([req[ch] for ch in kids[c]] + [0]) > 15:
                        raise RouteError(f"signal too weak after the repeater at {c}: no straight flat cell for another repeater"
                                         + ("" if net.restore else " (restore=True also allows a repeater on the last cell before a sink)"))
                    reps.add(c)
                    r = 1
            req[c] = r
        return reps

    def _strengths(self, net, reps):
        src, tree = net.src, net.parent
        s0 = 15 if src.kind in ("device", "pad") else src.level - 1
        val, delay = {}, {}
        for c in tree:   # insertion order is parent-before-child
            par = tree[c]
            if par is None:
                inp, dl = s0 + 1, 0
            else:
                inp, dl = val[par], delay[par]
            if c in reps:
                val[c] = 16 if inp >= 1 else 0
                delay[c] = dl + 2
            else:
                val[c] = max(0, inp - 1)
                delay[c] = dl
        net.strength = {c: (15 if c in reps else v) for c, v in val.items()}
        for s in net.sinks:
            c = net.attach[s.label()]
            lv = min(15, val[c] - 1 if s.kind == "dust" else val[c])
            net.sink_level[s.label()] = lv
            net.delay[s.label()] = delay[c]
            if lv < (s.need if s.kind == "dust" else 1):
                raise RouteError(f"sink {s.label()} would get strength {lv} < {s.need} ({s.why or 'need'}): no straight flat cell left for a "
                                 "repeater" + ("" if net.restore else " (restore=True also allows a repeater on the last cell before a sink; "
                                               "need= overrides the minimum)"))

    # -- results
    def blocks(self, names=None) -> Dict[Pos, str]:
        out = {}
        for n, net in self.nets.items():
            if net.ok and (names is None or n in names):
                out.update(net.cells)
        return out

    def report(self) -> str:
        ok = sum(n.ok for n in self.nets.values())
        lines = [f"{ok}/{len(self.nets)} nets routed, {len(self.blocks())} blocks"]
        lines += [n.summary() for n in self.nets.values()]
        return "\n".join(lines)


def _box(b):
    x1, y1, z1, x2, y2, z2 = b
    return (min(x1, x2), min(y1, y2), min(z1, z2), max(x1, x2), max(y1, y2), max(z1, z2))


def _inside(p, b) -> bool:
    return b[0] <= p[0] <= b[3] and b[1] <= p[1] <= b[4] and b[2] <= p[2] <= b[5]


def _dist(a, b) -> int:
    return sum(abs(a[i] - b[i]) for i in range(3))


def _sub(a, b) -> Pos:
    return (a[0] - b[0], 0, a[2] - b[2])


# --------------------------------------------------------------------------- lib knowledge
_META: Dict[str, Optional[dict]] = {}


def lib_meta(part_name: str) -> Optional[dict]:
    if part_name not in _META:
        p = os.path.join(ROOT, "lib", f"{part_name}.meta.json")
        try:
            with open(p) as f:
                _META[part_name] = json.load(f)
        except OSError:
            _META[part_name] = None
    return _META[part_name]


def lib_need(part_name: str, port: str) -> int:
    """Measured minimum input strength of a lib cell's port (1 if unknown)."""
    m = lib_meta(part_name)
    try:
        return int(m["verified"]["min_in_strength_per_port"][port])
    except (TypeError, KeyError, ValueError):
        return 1


# --------------------------------------------------------------------------- Part integration (simulation.builder.Part.route)
_SESSIONS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _port_ref(part, name: str):
    """'inst.PORT' -> (lib or wrapper part name, its port) from the builder's port table, else from the instance table."""
    info = part._pinfo.get(name)
    if info:
        return info
    if "." in name:
        inst, port = name.rsplit(".", 1)
        pn = next((i["part"] for i in part._insts if i["name"] == inst or i["name"].endswith("/" + inst)), None)
        if pn:
            return pn, port
    return None


def _analog_restored(part, name: str) -> bool:
    """Is every lib output of the instance that owns port `name` a non-analog one (a restorer behind the mux)?"""
    inst = name.rsplit(".", 1)[0]
    outs = [k for k, (_, kind, _f) in part._ports.items()
            if kind == "out" and k.startswith(inst + ".") and "." not in k[len(inst) + 1:]]
    refs = [part._pinfo.get(k) for k in outs]
    return bool(refs) and all(r and lib_meta(r[0]) is not None and r[0] not in ANALOG for r in refs)


def _part_pin(part, spec, role: str, need: Optional[int] = None) -> Pin:
    """'inst.PORT' (a registered port), (x,y,z), ((x,y,z), face) or a Pin -> Pin. Sinks get their minimum strength and
    entry rule from the lib port they stand for (wrapped parts: the inner port on the same cell)."""
    if isinstance(spec, Pin):
        if need is not None:
            spec.need = need
        return spec
    ref, label = None, None
    if isinstance(spec, str):
        ports = part._ports
        if spec not in ports:
            raise RouteError(f"{role}: no port {spec!r}; ports: {', '.join(sorted(ports)) or 'none'}")
        pos, kind, face = ports[spec]
        ref, label = _port_ref(part, spec), spec
    else:
        if len(spec) == 2 and isinstance(spec[1], str):
            pos, face = spec
        else:
            pos, face = spec, None
        pos = tuple(int(v) for v in pos)
        hit = next((k for k, v in part._ports.items() if tuple(v[0]) == pos and k in part._pinfo), None)
        if hit:     # a coordinate that is a registered lib port: same rules as by name
            ref, label = _port_ref(part, hit), hit
            face = face or part._ports[hit][2]
    pos = tuple(pos)
    st = part.get(pos)
    kind = "dust" if is_dust(st) else "device" if st is not None else "pad"
    pin = Pin(pos, face, kind, name=label if isinstance(spec, str) else "")
    if kind == "dust" and role == "sink":
        meta = lib_meta(ref[0]) if ref else None
        mp = (meta or {}).get("ports", {}).get(ref[1]) if ref else None
        if mp is not None:
            pin.need = lib_need(*ref)
            pin.entry = mp.get("entry") or entry_rule(part.get, pos, face)
            pin.why = f"{ref[0]}.{ref[1]}"
            if ref[0] in ANALOG and any(ref[1] in ins for ins in ANALOG[ref[0]].values()) and label \
                    and not _analog_restored(part, label):
                pin.need = max(pin.need, ANALOG_NEED)
                pin.why += f" (analog input: its output follows it, so {ANALOG_NEED}+)"
        else:
            pin.entry = entry_rule(part.get, pos, face)
    elif kind == "dust" and role == "source" and ref and ref[0] in ANALOG and ref[1] in ANALOG[ref[0]]:
        if lib_meta(ref[0]) is not None:
            pin.level = ANALOG_NEED
    if need is not None:
        pin.need = need
    return pin


def _part_session(part, sources, box, avoid, keep_clear, floor_y, kw):
    """The Router session of a Part (created on first use), synced with the cells the Part holds now.
    -> (router, auto_box). `sources` = the pins' (x,y,z) for the default box."""
    s = _SESSIONS.get(part)
    world = {p: c[0] for p, c in part._cells.items()}
    for p in avoid or ():
        if len(p) == 6:
            keep_clear = list(keep_clear or []) + [p]
        else:
            world.setdefault(tuple(p), "minecraft:pad")    # a fixed cell the Part does not know: treat as a component
    if s is None:
        s = Router(world, box=box, keep_clear=keep_clear or (), floor_y=floor_y, **kw)
        _SESSIONS[part] = s
    else:
        s.world = world
        s.hot = set()
        for p, st in world.items():
            s._note_hot(p, st)
        if box is not None:
            s.box = _box(box)
        if keep_clear:
            s.keep_clear = [_box(b) for b in keep_clear]
        if floor_y is not None:
            s.floor_y = floor_y
    if s.box is None:   # default: bounding box of everything known + 4
        pts = list(world) + list(sources)
        lo = [min(p[i] for p in pts) - 4 for i in range(3)]
        hi = [max(p[i] for p in pts) + 4 for i in range(3)]
        lo[1] = max(lo[1], min(p[1] for p in pts) - 1)
        s.box = tuple(lo + hi)
        return s, True
    return s, False


def _as_sinks(sinks):
    if not isinstance(sinks, (list, tuple)) or (len(sinks) == 3 and all(isinstance(v, int) for v in sinks)) \
            or (len(sinks) == 2 and isinstance(sinks[1], str) and not isinstance(sinks[0], str)):
        return [sinks]
    return list(sinks)


def _pin_pos(part, spec) -> Pos:
    if isinstance(spec, Pin):
        return spec.pos
    if isinstance(spec, str):
        if spec not in part._ports:
            raise RouteError(f"no port {spec!r}; ports: {', '.join(sorted(part._ports)) or 'none'}")
        return tuple(part._ports[spec][0])
    return tuple(int(v) for v in (spec[0] if len(spec) == 2 and isinstance(spec[1], str) else spec))


def _publish(part, s: Router, net: Net, line):
    """Write a routed net into the Part (provenance 'net:NAME'), record it for `nets`, surface its warnings."""
    ctx = part._ctx
    if net.ok:
        origin = ctx.origin(f"net:{net.name}", line)
        ctx.count(len(net.cells))
        for p, st in net.cells.items():
            part._put(p, st, None, origin, 0)
    for w in net.warnings:
        ctx.warn("route", w, limit=300)
    if net.infos:     # one aggregated line per build (see _finish_infos): the details live in `nets`
        ctx.route_infos += len(net.infos)
    ctx.nets.append({
        "name": net.name, "ok": net.ok, "src": net.src.label(), "sinks": [k.label() for k in net.sinks],
        "route_cells": len(net.parent), "blocks": len(net.cells), "repeaters": len(net.repeaters),
        "levels": dict(net.sink_level), "needs": {k.label(): k.need for k in net.sinks if k.kind == "dust"},
        "why": {k.label(): k.why for k in net.sinks if k.why}, "delay": dict(net.delay), "note": net.note,
        "warnings": list(net.warnings), "infos": list(net.infos), "reason": net.reason, "line": line})


def part_route(part, src, sinks, name=None, need=None, box=None, avoid=None, keep_clear=None, floor_y=None,
               strict=True, restore=False, allow_side=False, **kw) -> Net:
    """Route on a builder Part (the cells already placed are the obstacles; earlier routes and their staircase air cells
    are remembered per Part). src / sinks: 'inst.PORT', (x,y,z) or ((x,y,z), face). Raises RouteError (a BuilderError
    in the sandbox) when the net cannot be routed (strict=True), else returns the unrouted Net."""
    sinks = _as_sinks(sinks)
    pts = [_pin_pos(part, src)] + [_pin_pos(part, k) for k in sinks]
    s, auto_box = _part_session(part, pts, box, avoid, keep_clear, floor_y, kw)
    try:
        sp = _part_pin(part, src, "source")
        kp = [_part_pin(part, k, "sink", need) for k in sinks]
        nm = name or f"net{len(s.nets)}"
        net = s.route(nm, sp, kp, strict=False, restore=restore, allow_side=allow_side)
        if not net.ok:
            net.reason += "\n" + s.explain([sp] + kp, net.reason)
    finally:
        if auto_box:
            s.box = None
    _publish(part, s, net, part._ctx.line())
    if not net.ok and strict:
        s.nets.pop(nm, None)
        raise RouteError(net.reason)
    return net


def part_route_all(part, nets, order="auto", box=None, avoid=None, keep_clear=None, floor_y=None, strict=True,
                   restore=False, allow_side=False, need=None, **kw) -> List[Net]:
    """Several nets in one go (Router.route_all): ordered by constraint/length, ripped up and retried around failures.
    nets: [(src, sinks[, name[, need]])] or dicts {src, sinks, name, need, restore, allow_side}."""
    items = []
    for i, it in enumerate(nets):
        if isinstance(it, dict):
            d = dict(it)
        else:
            it = tuple(it)
            if not 2 <= len(it) <= 4:
                raise RouteError(f"net {i}: expected (src, sinks[, name[, need]]), got {it!r}")
            d = dict(zip(("src", "sinks", "name", "need"), it))
        if "src" not in d or "sinks" not in d:
            raise RouteError(f"net {i}: needs src and sinks")
        items.append(d)
    pts = []
    for d in items:
        d["sinks"] = _as_sinks(d["sinks"])
        pts += [_pin_pos(part, d["src"])] + [_pin_pos(part, k) for k in d["sinks"]]
    s, auto_box = _part_session(part, pts, box, avoid, keep_clear, floor_y, kw)
    line = part._ctx.line()
    try:
        specs, base = [], len(s.nets)
        for i, d in enumerate(items):
            nm = d.get("name") or f"net{base + i}"
            n_need = d.get("need", need)
            sp = _part_pin(part, d["src"], "source")
            kp = [_part_pin(part, k, "sink", n_need) for k in d["sinks"]]
            specs.append((nm, sp, kp, {"restore": d.get("restore", restore), "allow_side": d.get("allow_side", allow_side)}))
        if len({sp[0] for sp in specs}) != len(specs):
            raise RouteError("duplicate net names in route_all")
        done = s.route_all(specs, order=order)
        for net, sp in zip(done, specs):
            if not net.ok:
                net.reason += "\n" + s.explain([sp[1]] + sp[2], net.reason)
    finally:
        if auto_box:
            s.box = None
    for net in done:
        _publish(part, s, net, line)
    bad = [n for n in done if not n.ok]
    if bad and strict:
        for n in bad:
            s.nets.pop(n.name, None)
        raise RouteError(f"{len(bad)} of {len(done)} nets unroutable (after ordering and rip-up):\n" + "\n".join(n.reason for n in bad[:4])
                         + (f"\n... (+{len(bad) - 4} more)" if len(bad) > 4 else ""))
    return done


# --------------------------------------------------------------------------- simulator checks
@dataclass
class NetCheck:
    name: str
    ok: bool
    sinks: Dict[str, int]          # sink label -> power (dust) / 1 lit (device) with the source high
    low: Dict[str, int]            # same with the source low
    leaks: List[Tuple[Pos, str, str]]   # (pos, owner, change) of cells outside the net that changed
    detail: str = ""

    def summary(self) -> str:
        s = f"check {self.name}: {'ok' if self.ok else 'FAIL'} high={self.sinks} low={self.low}"
        if self.leaks:
            s += " leaks=" + ", ".join(f"{p}({o}: {c})" for p, o, c in self.leaks[:5])
        return s + (f" {self.detail}" if self.detail else "")


def _read(state: str, pin: Pin) -> int:
    n, p = name_of(state), props_of(state)
    if n == "redstone_wire":
        return int(p.get("power", "0"))
    if n == "redstone_lamp":
        return int(p.get("lit") == "true")
    return int(p.get("powered") == "true" or p.get("lit") == "true")


class NetChecker:
    """Compile a world once, then power each net's source alone (from rest) and diff.

    world: every block (modules, devices, nets); owner: pos -> tag (net name / module instance) used to name leaks;
    allowed(net) -> set of positions that may change besides the net's own cells and pins (e.g. the modules downstream)."""

    def __init__(self, world: Dict[Pos, str], owner: Optional[Dict[Pos, str]] = None, settle: int = 400):
        from env.sim import Sim, compile_cells
        self.world, self.owner, self.budget = dict(world), dict(owner or {}), settle
        sc, corner, errs = compile_cells(self.world)
        if errs:
            raise RouteError(f"simulator refused blocks: {errs[:3]}")
        self.sim = Sim(sc, corner, "inworld")
        self.sim.settle(settle)
        self.cp = self.sim.checkpoint()

    def _drive(self, pin: Pin, high: bool):
        st = self.sim.get(pin.pos)
        if pin.kind == "device" and (name_of(st) == "lever" or name_of(st).endswith("_button")):
            if (props_of(st).get("powered") == "true") != high:
                self.sim.use(pin.pos)
        else:
            self.sim.place(pin.pos, "minecraft:redstone_block" if high else "minecraft:air")

    def check(self, net: Net, allowed=()) -> NetCheck:
        if not net.ok:
            return NetCheck(net.name, False, {}, {}, [], "unrouted: " + net.reason)
        sim = self.sim
        sim.restore(self.cp)
        self._drive(net.src, False)
        sim.settle(self.budget)
        low = sim.snapshot()
        self._drive(net.src, True)
        _, settled = sim.settle(self.budget)
        high = sim.snapshot()
        hi = {s.label(): _read(high.get(s.pos, "minecraft:air"), s) for s in net.sinks}
        lo = {s.label(): _read(low.get(s.pos, "minecraft:air"), s) for s in net.sinks}
        members = set(net.cells) | {net.src.pos} | {s.pos for s in net.sinks} | set(allowed)
        leaks = []
        for p in sorted(set(low) | set(high)):
            a, b = low.get(p, "minecraft:air"), high.get(p, "minecraft:air")
            if a != b and p not in members:
                leaks.append((p, self.owner.get(p, "scenery"), f"{name_of(a)}->{name_of(b)} {_diffprops(a, b)}"))
        bad = [s.label() for s in net.sinks if hi[s.label()] < (s.need if s.kind == "dust" else 1)]
        stuck = [s.label() for s in net.sinks if lo[s.label()] > 0]
        detail = []
        if bad:
            detail.append(f"weak/unpowered sinks {bad}")
        if stuck:
            detail.append(f"sinks powered with the source low {stuck}")
        if not settled:
            detail.append("never settled")
        return NetCheck(net.name, not (bad or stuck or leaks) and settled, hi, lo, leaks, "; ".join(detail))


def _diffprops(a, b) -> str:
    pa, pb = props_of(a), props_of(b)
    return ",".join(f"{k}={pa.get(k)}->{pb.get(k)}" for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k))


def check_nets(router: Router, extra_world=None, isolated: bool = True) -> Dict[str, NetCheck]:
    """Check every routed net of a session. isolated=True: the world holds only the nets, their pins (and the blocks the
    pins stand on), so every change outside the powered net is a leak; isolated=False: the whole session world (modules
    included; then module cells downstream of a sink legitimately change and are reported as leaks too - use
    modules.Design.check for the netlist-aware version)."""
    world = {p: s for p, s in router.world.items() if name_of(s) != "pad"}
    if extra_world:
        world.update(extra_world)
    if isolated:
        keep = {}
        for n in router.nets.values():
            if not n.ok:
                continue
            keep.update(n.cells)
            for pin in [n.src] + n.sinks:
                for q in (pin.pos, add(pin.pos, (0, -1, 0))):
                    if q in world:
                        keep[q] = world[q]
                if pin.kind == "device" and pin.pos in world and name_of(world[pin.pos]) == "lever":
                    for q in strongly_powers(pin.pos, world[pin.pos]):
                        if q in world:
                            keep[q] = world[q]
        if router.floor_y is not None:
            for p in list(keep):
                q = (p[0], router.floor_y, p[2])
                keep.setdefault(q, FLOOR)
        world = keep
    owner = dict(router.owner)
    for n in router.nets.values():
        for pin in [n.src] + n.sinks:
            owner.setdefault(pin.pos, f"pin:{pin.label()}")
    chk = NetChecker(world, owner)
    return {name: chk.check(n) for name, n in router.nets.items()}


def paste_diff(world: Dict[Pos, str], settle: int = 400) -> Tuple[bool, List[str], List[Pos]]:
    """The env's paste_stable invariant (env/runner.py) on a plain world: place through the engine, settle, save the raw
    at-rest states, reload them InWorld and as Placement; every cell must come back unchanged. -> (ok, details, cells)."""
    from env.sim import Sim, compile_cells
    sc, corner, errs = compile_cells(world)
    sim = Sim(sc, corner, "inworld")
    _, ok = sim.settle(settle)
    base = sim.snapshot()
    details = [] if ok else ["never quiescent after placement"]
    raw, corner2, _ = compile_cells(base, engine_place=False)
    cells: set = set()
    for mode in ("inworld", "placement"):
        s2 = Sim(raw, corner2, mode)
        _, ok2 = s2.settle(settle)
        snap = s2.snapshot()
        diff = sorted(p for p in set(snap) | set(base) if snap.get(p) != base.get(p))
        cells.update(diff)
        if diff or not ok2:
            details.append(f"{mode}: {len(diff)} cells differ" + (f", e.g. {diff[0]} {name_of(base.get(diff[0], 'air'))} "
                                                                 f"{_diffprops(base.get(diff[0], 'air'), snap.get(diff[0], 'air'))}" if diff else "")
                           + ("" if ok2 else ", never quiescent"))
    return not details, details, sorted(cells)


def paste_stable(world: Dict[Pos, str], settle: int = 400) -> Tuple[bool, List[str]]:
    """paste_diff without the cell list -> (ok, details)."""
    ok, details, _ = paste_diff(world, settle)
    return ok, details
