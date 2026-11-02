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
from dataclasses import dataclass, field
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

    def __post_init__(self):
        self.pos = tuple(int(v) for v in self.pos)
        if self.face is not None and self.face not in DIRS:
            raise RouteError(f"pin face must be one of {sorted(DIRS)} or None, got {self.face!r}")
        if self.kind not in ("dust", "device", "pad"):
            raise RouteError(f"pin kind must be dust/device/pad, got {self.kind!r}")

    def label(self) -> str:
        return self.name or str(self.pos)

    def sides(self) -> List[str]:
        return [self.face] if self.face else list(DIRS)


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
                 slab_cost=0.3, support_cost=0.5, near_cost=0.15, zone_cost=3.0, max_states=400_000):
        self.world: Dict[Pos, str] = {tuple(p): s for p, s in (world or {}).items()}
        self.owner: Dict[Pos, str] = {}
        self.box = _box(box) if box is not None else None
        self.keep_clear = [_box(b) for b in keep_clear]
        self.air = {tuple(p) for p in reserved}
        self.floor_y = floor_y
        self.turn_cost, self.step_cost, self.slab_cost, self.near_cost = turn_cost, step_cost, slab_cost, near_cost
        self.support_cost = support_cost
        self.max_states = max_states
        self.nets: Dict[str, Net] = {}
        self._current = ""
        self.zone: Dict[Pos, set] = {}          # cells around pins of nets not routed yet -> those nets (soft keep-out)
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
        return self.state(p) is None and p not in self.air and p not in extra and self.writable(p)

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
        if z and (len(z) > 1 or net_name not in z):
            c += self.zone_cost
        if self.near_cost:
            for dx, dy, dz in itertools.product((-1, 0, 1), repeat=3):
                q = (n[0] + dx, n[1] + dy, n[2] + dz)
                if q != n and q in self.owner:
                    return c + self.near_cost
        return c

    def reserve_pins(self, name: str, pins: List[Pin]):
        """Announce the pins of a net that will be routed later: other nets pay `zone_cost` to pass right next to them
        (keeps the attach cells of pending nets usable). Cleared when the net is routed."""
        for pin in pins:
            for dx, dz in itertools.product((-1, 0, 1), repeat=2):
                for dy in (0, 1):
                    self.zone.setdefault((pin.pos[0] + dx, pin.pos[1] + dy, pin.pos[2] + dz), set()).add(name)

    def _release(self, name: str):
        for p in [p for p, v in self.zone.items() if name in v]:
            self.zone[p].discard(name)
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
    def route(self, name: str, src: Pin, sinks: List[Pin], strict: bool = False) -> Net:
        """Route one net; on success the cells are committed to the session. Returns the Net (ok, reason, cells, ...).
        strict=True raises RouteError instead of returning an unrouted net."""
        if name in self.nets:
            raise RouteError(f"net {name!r} already routed")
        net = Net(name, src, list(sinks))
        self._current = name
        try:
            self._route(net)
            net.ok = True
        except RouteError as e:
            net.ok, net.reason, net.cells, net.parent, net.air = False, str(e), {}, {}, set()
            if strict:
                raise
        if net.ok:
            for p, s in net.cells.items():
                self.world[p] = s
                self.owner[p] = name
                self._note_hot(p, s)
            self.air |= net.air
            self._release(name)
        self.nets[name] = net
        return net

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

        def eligible(c):
            par = tree[c]
            ks = kids[c]
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
                        raise RouteError(f"signal too weak after the repeater at {c}: no straight flat cell for another repeater")
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
            lv = val[c] - 1 if s.kind == "dust" else val[c]
            net.sink_level[s.label()] = lv
            net.delay[s.label()] = delay[c]
            if lv < (s.need if s.kind == "dust" else 1):
                raise RouteError(f"sink {s.label()} would get strength {lv} < {s.need}: no straight flat cell left for a repeater")

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


def _part_pin(part, spec, role: str) -> Pin:
    """'inst.PORT' (a registered port), (x,y,z), ((x,y,z), face) or a Pin -> Pin."""
    if isinstance(spec, Pin):
        return spec
    if isinstance(spec, str):
        ports = part._ports
        if spec not in ports:
            raise RouteError(f"{role}: no port {spec!r}; ports: {', '.join(sorted(ports)) or 'none'}")
        pos, kind, face = ports[spec]
        need = 1
        if role == "sink" and "." in spec:
            inst, port = spec.rsplit(".", 1)
            pn = next((i["part"] for i in part._insts if i["name"] == inst or i["name"].endswith("/" + inst)), None)
            need = lib_need(pn, port) if pn else 1
        st = part.get(pos)
        return Pin(tuple(pos), face, "dust" if is_dust(st) else "device", need=need, name=spec)
    if len(spec) == 2 and isinstance(spec[1], str):
        pos, face = spec
    else:
        pos, face = spec, None
    pos = tuple(int(v) for v in pos)
    st = part.get(pos)
    return Pin(pos, face, "dust" if is_dust(st) else "device" if st is not None else "pad")


def part_route(part, src, sinks, name=None, need=None, box=None, avoid=None, keep_clear=None, floor_y=None,
               strict=True, **kw) -> Net:
    """Route on a builder Part (the cells already placed are the obstacles; earlier routes and their staircase air cells
    are remembered per Part). src / sinks: 'inst.PORT', (x,y,z) or ((x,y,z), face). Raises RouteError (a BuilderError
    in the sandbox) when the net cannot be routed (strict=True)."""
    if not isinstance(sinks, (list, tuple)) or (len(sinks) == 3 and all(isinstance(v, int) for v in sinks)) \
            or (len(sinks) == 2 and isinstance(sinks[1], str) and not isinstance(sinks[0], str)):
        sinks = [sinks]
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
        pts = list(world) + [_part_pin(part, src, "source").pos] + [_part_pin(part, k, "sink").pos for k in sinks]
        lo = [min(p[i] for p in pts) - 4 for i in range(3)]
        hi = [max(p[i] for p in pts) + 4 for i in range(3)]
        lo[1] = max(lo[1], min(p[1] for p in pts) - 1)
        s.box = tuple(lo + hi)
        auto_box = True
    else:
        auto_box = False
    try:
        sp = _part_pin(part, src, "source")
        kp = [_part_pin(part, k, "sink") for k in sinks]
        if need is not None:
            for k in kp:
                k.need = need
        nm = name or f"net{len(s.nets)}"
        net = s.route(nm, sp, kp, strict=strict)
    finally:
        if auto_box:
            s.box = None
    for p, st in net.cells.items():
        part.set(p, st)
    return net


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


def paste_stable(world: Dict[Pos, str], settle: int = 400) -> Tuple[bool, List[str]]:
    """The env's paste_stable invariant (env/runner.py) on a plain world: place through the engine, settle, save the raw
    at-rest states, reload them InWorld and as Placement; every cell must come back unchanged. -> (ok, details)."""
    from env.sim import Sim, compile_cells
    sc, corner, errs = compile_cells(world)
    sim = Sim(sc, corner, "inworld")
    _, ok = sim.settle(settle)
    base = sim.snapshot()
    details = [] if ok else ["never quiescent after placement"]
    raw, corner2, _ = compile_cells(base, engine_place=False)
    for mode in ("inworld", "placement"):
        s2 = Sim(raw, corner2, mode)
        _, ok2 = s2.settle(settle)
        snap = s2.snapshot()
        diff = sorted(p for p in set(snap) | set(base) if snap.get(p) != base.get(p))
        if diff or not ok2:
            details.append(f"{mode}: {len(diff)} cells differ" + (f", e.g. {diff[0]} {name_of(base.get(diff[0], 'air'))} "
                                                                 f"{_diffprops(base.get(diff[0], 'air'), snap.get(diff[0], 'air'))}" if diff else "")
                           + ("" if ok2 else ", never quiescent"))
    return not details, details
