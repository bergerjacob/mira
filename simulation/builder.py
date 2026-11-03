"""Part builder: the authoring layer for MIRA builds (see docs/BUILDER.md).

A build is a Python program defining `build(b)`; `compile(src)` runs it in a sandbox and returns
a `Build` whose `.blocks` are (x, y, z, state, nbt) tuples (the flat interchange format):
state strings are canonical (all properties present, sorted, validated against Minecraft 1.21.0),
nbt is a canonical SNBT string or None. Air is never stored. Every block records the part path
and source line that wrote it. `decompile()` turns a .litematic/.schem into such a program.

Nucleation is imported lazily and only in `Build.schematic()`, `use()` and `decompile()`.
"""

import ast
import builtins
import dataclasses
import hashlib
import itertools
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import types
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from simulation.blockstates import (StateError, format_state, is_air, normalize, parse_state,  # noqa: E402
                                    rotate_point, transform_state)

_py_compile = builtins.compile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD_FILENAME = "<build>"
DIRS = ("north", "east", "south", "west", "up", "down")
NORTH, EAST, SOUTH, WEST, UP, DOWN = DIRS
DIR_VEC = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0),
           "up": (0, 1, 0), "down": (0, -1, 0)}
_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east", "up": "down", "down": "up"}

BlockTuple = Tuple[int, int, int, str, Optional[str]]


def opposite(direction: str) -> str:
    return _OPPOSITE[direction]


# --- SNBT (canonical form: sorted keys, quoted strings, numeric literals kept verbatim) --

_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?[bBsSlLfFdD]?$")
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9._+\-]+$")


class SnbtError(ValueError):
    pass


class _SnbtParser:
    def __init__(self, text: str):
        self.s, self.i = text, 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def parse(self):
        v = self.value()
        self.ws()
        if self.i != len(self.s):
            raise SnbtError(f"trailing text at {self.i}: {self.s[self.i:self.i + 20]!r}")
        return v

    def quoted(self) -> str:
        q = self.s[self.i]
        self.i += 1
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 1
                out.append(self.s[self.i])
            elif c == q:
                self.i += 1
                return "".join(out)
            else:
                out.append(c)
            self.i += 1
        raise SnbtError("unterminated string")

    def bare(self, stops: str) -> str:
        j = self.i
        while j < len(self.s) and self.s[j] not in stops:
            j += 1
        tok = self.s[self.i:j].strip()
        self.i = j
        return tok

    def value(self):
        self.ws()
        if self.i >= len(self.s):
            raise SnbtError("unexpected end")
        c = self.s[self.i]
        if c == "{":
            self.i += 1
            d = {}
            self.ws()
            if self.s[self.i] == "}":
                self.i += 1
                return d
            while True:
                self.ws()
                key = self.quoted() if self.s[self.i] in "\"'" else self.bare(":")
                self.ws()
                if self.s[self.i] != ":":
                    raise SnbtError(f"expected ':' after key {key!r}")
                self.i += 1
                d[key] = self.value()
                self.ws()
                c2 = self.s[self.i]
                self.i += 1
                if c2 == "}":
                    return d
                if c2 != ",":
                    raise SnbtError(f"expected ',' or '}}' at {self.i}")
        if c == "[":
            self.i += 1
            self.ws()
            m = re.match(r"([BIL]);", self.s[self.i:self.i + 2])
            kind = None
            if m:
                kind = m.group(1)
                self.i += 2
            items = []
            self.ws()
            if self.s[self.i] == "]":
                self.i += 1
            else:
                while True:
                    items.append(self.value())
                    self.ws()
                    c2 = self.s[self.i]
                    self.i += 1
                    if c2 == "]":
                        break
                    if c2 != ",":
                        raise SnbtError(f"expected ',' or ']' at {self.i}")
            return ("array", kind, items) if kind else items
        if c in "\"'":
            return ("str", self.quoted())
        tok = self.bare(",}]")
        if _NUM_RE.match(tok):
            return ("num", tok[:-1] + tok[-1].upper() if tok[-1] in "bBsSlLfFdD" else tok)
        if tok in ("true", "false"):
            return ("num", tok)
        return ("str", tok)


def _snbt_ser(v) -> str:
    if isinstance(v, dict):
        parts = []
        for k in sorted(v):
            ks = k if _BARE_KEY_RE.match(k) else json.dumps(k)
            parts.append(f"{ks}:{_snbt_ser(v[k])}")
        return "{" + ",".join(parts) + "}"
    if isinstance(v, list):
        return "[" + ",".join(_snbt_ser(x) for x in v) + "]"
    kind = v[0]
    if kind == "num":
        return v[1]
    if kind == "str":
        return json.dumps(v[1], ensure_ascii=False)
    return "[" + v[1] + ";" + ",".join(_snbt_ser(x) for x in v[2]) + "]"


def _py_to_tree(o):
    if isinstance(o, dict):
        return {str(k): _py_to_tree(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_py_to_tree(x) for x in o]
    if isinstance(o, bool):
        return ("num", "1B" if o else "0B")
    if isinstance(o, Byte):
        return ("num", f"{int(o)}B")
    if isinstance(o, int):
        return ("num", str(o))
    if isinstance(o, float):
        return ("num", repr(o) + "D")
    if isinstance(o, str):
        return ("str", o)
    raise SnbtError(f"unsupported NBT value {o!r}")


def parse_snbt(text: str):
    try:
        return _SnbtParser(text).parse()
    except IndexError:
        raise SnbtError("unexpected end of SNBT") from None


def canon_nbt(nbt) -> Optional[str]:
    """dict / SNBT string / None -> canonical SNBT string or None (empty compound -> None)."""
    if nbt is None:
        return None
    tree = parse_snbt(nbt) if isinstance(nbt, str) else _py_to_tree(nbt)
    if not isinstance(tree, dict):
        raise SnbtError("NBT must be a compound {...}")
    return _snbt_ser(tree) if tree else None


def _pop_vec3(tree: dict) -> Optional[Tuple[float, float, float]]:
    pos = tree.pop("Pos", None)
    if isinstance(pos, list) and len(pos) == 3 and all(isinstance(t, tuple) and t[0] == "num" for t in pos):
        return tuple(float(t[1].rstrip("dDfF")) for t in pos)
    return None


class Byte(int):
    """NBT byte: Byte(3) serializes as 3B."""


def items(*stacks, slots: Optional[Iterable[int]] = None) -> Dict[str, Any]:
    """Container NBT: items(("minecraft:redstone", 64), ("minecraft:stone", 1)) -> {"Items": [...]}."""
    out = []
    for i, st in enumerate(stacks):
        slot = list(slots)[i] if slots is not None else i
        item_id, count = st if isinstance(st, tuple) else (st, 1)
        out.append({"Slot": Byte(slot), "id": item_id, "count": count})
    return {"Items": out}


# --- positions -----------------------------------------------------------------------

class P(NamedTuple):
    x: int
    y: int
    z: int

    def __add__(self, o):  # type: ignore[override]
        return P(self.x + o[0], self.y + o[1], self.z + o[2])

    __radd__ = __add__

    def __sub__(self, o):
        return P(self.x - o[0], self.y - o[1], self.z - o[2])

    def __mul__(self, k):  # type: ignore[override]
        return P(self.x * k, self.y * k, self.z * k)

    def step(self, direction: str, n: int = 1) -> "P":
        dx, dy, dz = DIR_VEC[direction]
        return P(self.x + dx * n, self.y + dy * n, self.z + dz * n)


class BuilderError(Exception):
    """Misuse of the builder API; reported with the source line."""


class BuildLimit(BaseException):
    """Block / volume / instance cap exceeded (BaseException so build code can't swallow it)."""


def _pos(p, what: str = "position") -> P:
    try:
        x, y, z = p
        if all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y, z)):
            return P(x, y, z)
    except (TypeError, ValueError):
        pass
    raise BuilderError(f"{what} must be an (x, y, z) tuple of ints, got {p!r}")


@dataclasses.dataclass
class BuildIssue:
    kind: str
    message: str
    line: Optional[int] = None
    path: str = ""

    def __str__(self):
        loc = f"line {self.line}: " if self.line else ""
        where = f" [in {self.path}]" if self.path else ""
        return f"{loc}{self.kind}: {self.message}{where}"

    def to_dict(self):
        return dataclasses.asdict(self)


# --- compile context + Part ----------------------------------------------------------

POINT, FILL = 0, 1  # cell kinds: overlap warnings only fire for point-vs-point writes


class _Ctx:
    def __init__(self, max_blocks=2_000_000, validate=True, lib_root=None):
        self.max_blocks, self.validate, self.lib_root = max_blocks, validate, lib_root
        self.origins: List[Tuple[str, Optional[int]]] = []
        self._origin_ids: Dict[Tuple[str, Optional[int]], int] = {}
        self.errors: List[BuildIssue] = []
        self.warnings: List[BuildIssue] = []
        self.log: List[str] = []
        self.cache: Dict[Any, "_PartResult"] = {}
        self.written = 0
        self.depth = 0
        self.lib_cache: Dict[str, Any] = {}
        self.nets: List[dict] = []      # routed-net records (Part.route), listed by `mira nets`
        self.route_infos = 0            # dust ports the router joined from a side other than their face, harmlessly

    def origin(self, path: str, line: Optional[int]) -> int:
        key = (path, line)
        i = self._origin_ids.get(key)
        if i is None:
            i = self._origin_ids[key] = len(self.origins)
            self.origins.append(key)
        return i

    @staticmethod
    def line() -> Optional[int]:
        f = sys._getframe(1)
        while f is not None:
            if f.f_code.co_filename == BUILD_FILENAME:
                return f.f_lineno
            f = f.f_back
        return None

    def error(self, kind: str, msg: str, path: str = "", line: Optional[int] = None):
        if len(self.errors) < 100:
            self.errors.append(BuildIssue(kind, msg, line if line is not None else self.line(), path))

    def warn(self, kind: str, msg: str, path: str = "", limit: int = 50):
        if len(self.warnings) < limit:
            self.warnings.append(BuildIssue(kind, msg, self.line(), path))

    def count(self, n: int):
        self.written += n
        if self.written > self.max_blocks * 4:
            raise BuildLimit(f"more than {self.max_blocks * 4} block writes (cap {self.max_blocks} blocks)")


class _PartResult:
    __slots__ = ("cells", "ports", "labels", "entities", "pinfo")

    def __init__(self, cells, ports, labels, entities, pinfo=None):
        self.cells, self.ports, self.labels, self.entities = cells, ports, labels, entities
        self.pinfo = pinfo or {}      # port name -> (part name, port name) of the lib/inner port it stands for


class PartDef:
    """A reusable cell: `@part` function taking a Part (plus keyword params)."""

    def __init__(self, fn, ports: Optional[Dict[str, Any]] = None):
        self.fn, self.name = fn, fn.__name__
        self.ports = {k: _pos(v, f"port {k!r}") for k, v in (ports or {}).items()}

    def __repr__(self):
        return f"<part {self.name}>"


def part(fn=None, *, ports: Optional[Dict[str, Any]] = None):
    """Decorator: @part or @part(ports={"in": P(0,1,0)}). Cell code runs once per params, then is stamped."""
    if fn is None:
        return lambda f: PartDef(f, ports)
    return PartDef(fn, ports)


class Inst:
    """A placed part; inst["port"] is the port's world position."""

    def __init__(self, name: str, part_name: str, at: P, rot: int, mirror: Optional[str],
                 ports: Dict[str, P]):
        self.name, self.part_name, self.at, self.rot, self.mirror, self.ports = name, part_name, at, rot, mirror, ports

    def __getitem__(self, port: str) -> P:
        try:
            return self.ports[port]
        except KeyError:
            raise BuilderError(f"part {self.part_name!r} has no port {port!r}; ports: "
                               f"{', '.join(sorted(self.ports)) or 'none'}") from None

    def __repr__(self):
        return f"<{self.name} at {tuple(self.at)}>"


def _spec(spec) -> Tuple[str, Any]:
    """A block spec is a state string or a (state, nbt) pair."""
    if isinstance(spec, (tuple, list)) and len(spec) == 2 and isinstance(spec[0], str):
        return spec[0], spec[1]
    if isinstance(spec, str):
        return spec, None
    raise BuilderError(f"block must be a state string or (state, nbt), got {spec!r}")


_INPUT_RE = re.compile(r"(lever|button|pressure_plate)")


class Part:
    """Mutable build canvas. The root Part is passed to build(b); `@part` cells get their own."""

    def __init__(self, ctx: Optional[_Ctx] = None, name: str = ""):
        self._ctx = ctx or _Ctx()
        self._name = name
        self._cells: Dict[Tuple[int, int, int], Tuple[str, Optional[str], int, int]] = {}
        self._ports: Dict[str, Tuple[P, str, Optional[str]]] = {}
        self._labels: Dict[str, Tuple[P, P]] = {}
        self._entities: List[Tuple[float, float, float, str, Optional[str]]] = []
        self._insts: List[dict] = []
        self._pinfo: Dict[str, Tuple[str, str]] = {}   # "inst.PORT" -> (part, port): what the router needs/entry rules come from

    # -- internals
    def _state(self, state: str) -> Optional[str]:
        try:
            return normalize(state, strict=self._ctx.validate)
        except StateError as e:
            self._ctx.error(e.kind, str(e), self._name)
            return None

    def _nbt(self, nbt) -> Optional[str]:
        try:
            return canon_nbt(nbt)
        except (SnbtError, IndexError) as e:
            self._ctx.error("bad_nbt", f"cannot parse NBT: {e}", self._name)
            return None

    def _put(self, pos, state: str, nbt: Optional[str], origin: int, kind: int):
        if is_air(state):
            self._cells.pop(pos, None)
            return
        old = self._cells.get(pos)
        if old is not None and kind == POINT and old[3] == POINT and (old[0], old[1]) != (state, nbt):
            self._ctx.warn("overlap", f"{pos} already holds {old[0]} (from line {self._ctx.origins[old[2]][1]}); "
                           f"overwritten by {state}", self._name)
        self._cells[pos] = (state, nbt, origin, kind)
        if len(self._cells) > self._ctx.max_blocks:
            raise BuildLimit(f"build exceeds {self._ctx.max_blocks} blocks")

    def _norm_spec(self, spec) -> Optional[Tuple[str, Optional[str]]]:
        state, nbt = _spec(spec)
        state = self._state(state)
        if state is None:
            return None
        return state, self._nbt(nbt)

    # -- placement
    def set(self, pos, state, nbt=None, port: Optional[str] = None, kind: Optional[str] = None) -> None:
        """Place one block. `state` may be a (state, nbt) pair; port= also registers a named port."""
        pos = _pos(pos)
        if isinstance(state, (tuple, list)):
            state, nbt = _spec(state)
        ns = self._norm_spec((state, nbt))
        self._ctx.count(1)
        if ns is not None:
            self._put(pos, ns[0], ns[1], self._ctx.origin(self._name, self._ctx.line()), POINT)
        if port is not None:
            if kind is None:
                kind = "in" if _INPUT_RE.search(state) else "out"
            self.port(port, pos, kind)

    def points(self, state, *positions) -> None:
        """Place the same block at many positions: points("stone", (0,0,0), (3,0,0))."""
        ns = self._norm_spec(state)
        self._ctx.count(len(positions))
        o = self._ctx.origin(self._name, self._ctx.line())
        for p in positions:
            if ns is not None:
                self._put(tuple(_pos(p)), ns[0], ns[1], o, POINT)

    def fill(self, a, b, state, nbt=None, keep: bool = False) -> None:
        """Fill the inclusive box a..b. keep=True leaves existing blocks untouched."""
        a, b = _pos(a, "fill corner a"), _pos(b, "fill corner b")
        if isinstance(state, (tuple, list)):
            state, nbt = _spec(state)
        lo = (min(a.x, b.x), min(a.y, b.y), min(a.z, b.z))
        hi = (max(a.x, b.x), max(a.y, b.y), max(a.z, b.z))
        vol = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
        if vol > self._ctx.max_blocks:
            raise BuildLimit(f"fill of {vol} blocks exceeds cap {self._ctx.max_blocks}")
        ns = self._norm_spec((state, nbt))
        self._ctx.count(vol)
        if ns is None:
            return
        o = self._ctx.origin(self._name, self._ctx.line())
        for pos in itertools.product(range(lo[0], hi[0] + 1), range(lo[1], hi[1] + 1), range(lo[2], hi[2] + 1)):
            if keep and pos in self._cells:
                continue
            self._put(pos, ns[0], ns[1], o, FILL)

    def layers(self, origin, palette: Dict[str, Any], rows: List[List[str]]) -> None:
        """ASCII layers: rows[y][z][x] chars looked up in palette; '.' and ' ' mean no block.
        Layer y is origin.y + y, row z is origin.z + z, column x is origin.x + x."""
        ox, oy, oz = _pos(origin, "layers origin")
        o = self._ctx.origin(self._name, self._ctx.line())
        for ch in palette:
            if ch in (".", " "):
                raise BuilderError(f"palette key {ch!r} is reserved for 'no block'")
        specs: Dict[str, Any] = {}  # lazily validated; multi-char palette keys are never referenced by rows
        for y, layer in enumerate(rows):
            if isinstance(layer, str):
                raise BuilderError(f"rows must be a list of layers, each a list of strings (layer {y} is a str)")
            for z, row in enumerate(layer):
                self._ctx.count(len(row))
                for x, ch in enumerate(row):
                    if ch in ". ":
                        continue
                    if ch not in specs:
                        if ch not in palette:
                            self._ctx.error("unknown_palette_char",
                                            f"layer {y} row {z} col {x}: {ch!r} is not in the palette")
                            continue
                        specs[ch] = self._norm_spec(palette[ch])
                    if specs[ch] is not None:
                        self._put((ox + x, oy + y, oz + z), specs[ch][0], specs[ch][1], o, POINT)

    def get(self, pos) -> Optional[str]:
        """State currently at pos, or None if empty."""
        c = self._cells.get(tuple(_pos(pos)))
        return c[0] if c else None

    def remove(self, a, b=None) -> None:
        """Clear a block or the inclusive box a..b."""
        self.fill(a, a if b is None else b, "minecraft:air")

    def entity(self, entity_id: str, pos, nbt=None) -> None:
        """Add an entity at float position (x, y, z); nbt must not repeat Pos/id."""
        try:
            x, y, z = (float(v) for v in pos)
        except (TypeError, ValueError):
            raise BuilderError(f"entity position must be (x, y, z) numbers, got {pos!r}") from None
        self._ctx.count(1)
        n = self._nbt(nbt)
        self._entities.append((x, y, z, entity_id if ":" in entity_id else "minecraft:" + entity_id, n))

    # -- structure
    def port(self, name: str, pos, kind: str = "in", face: Optional[str] = None) -> None:
        if kind not in ("in", "out", "io"):
            raise BuilderError(f"port kind must be 'in', 'out' or 'io', got {kind!r}")
        if face is not None and face not in DIRS:
            raise BuilderError(f"port face must be one of {DIRS}, got {face!r}")
        if name in self._ports:
            self._ctx.error("duplicate_port", f"port {name!r} defined twice", self._name)
        self._ports[name] = (_pos(pos, f"port {name!r}"), kind, face)

    def label(self, name: str, a, b=None) -> None:
        a = _pos(a, "label corner")
        b = a if b is None else _pos(b, "label corner")
        self._labels[name] = (P(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z)),
                              P(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z)))

    def place(self, part_def, at=(0, 0, 0), rot: int = 0, mirror: Optional[str] = None,
              name: Optional[str] = None, **params) -> Inst:
        """Stamp a @part cell. Mirror ('x' negates x, 'z' negates z) is applied before rot
        (0/90/180/270, clockwise seen from above), then the cell origin moves to `at`."""
        if not isinstance(part_def, PartDef):
            if callable(part_def):
                part_def = PartDef(part_def)
            else:
                raise BuilderError(f"place() needs a @part function, got {part_def!r}")
        at = _pos(at, "place position")
        if rot % 90 or mirror not in (None, "x", "z"):
            raise BuilderError(f"rot must be a multiple of 90 and mirror None/'x'/'z' (got rot={rot}, mirror={mirror!r})")
        name = name or part_def.name
        res = self._build_cell(part_def, params)
        line = self._ctx.line()
        return self._merge(res, name, part_def, at, rot % 360, mirror, line, params)

    def array(self, part_def, n: int, step, at=(0, 0, 0), rot: int = 0, mirror: Optional[str] = None,
              name: Optional[str] = None, **params) -> List[Inst]:
        """n copies of a cell, instance i at `at + i * step`; returns the Inst list."""
        if not isinstance(n, int) or n < 0 or n > 100_000:
            raise BuilderError(f"array n must be an int in 0..100000, got {n!r}")
        at, step = _pos(at, "array position"), _pos(step, "array step")
        if not isinstance(part_def, PartDef):
            part_def = PartDef(part_def)
        base = name or part_def.name
        res = self._build_cell(part_def, params)
        line = self._ctx.line()
        return [self._merge(res, f"{base}[{i}]", part_def, at + step * i, rot % 360, mirror, line, params)
                for i in range(n)]

    def use(self, path: str, at=(0, 0, 0), rot: int = 0, mirror: Optional[str] = None,
            name: Optional[str] = None) -> Inst:
        """Stamp a library cell (.litematic/.schem under the library root); ports come from
        '<file>.ports.json' ({"name": [x, y, z]}) when present."""
        res = _load_library(self._ctx, path)
        pd = PartDef(lambda p: None)
        pd.name = os.path.splitext(os.path.basename(path))[0]
        return self._merge(res, name or pd.name, pd, _pos(at, "use position"), rot % 360, mirror,
                           self._ctx.line(), {})

    def route(self, src, sinks, name: Optional[str] = None, need: Optional[int] = None, box=None, avoid=None,
              keep_clear=None, floor_y: Optional[int] = None, strict: bool = True, restore: bool = False,
              allow_side: bool = False):
        """3D router (simulation/router.py, docs/ROUTER.md): dust + supports + repeaters from src to every sink, around
        everything already placed (no touching foreign dust/components, staircases for height, bridges over other nets).
        src / sinks: 'inst.PORT', (x, y, z) or ((x, y, z), face); a list of sinks makes a tree.
        need: minimum dust strength at every sink (default: lib ports their measured minimum, mux2 data inputs 13,
        wrapped parts inherit from the lib port they alias, others 1). restore=True: also allow a repeater on the last
        cell before a sink when the strength would otherwise drop below need. A dust port whose neighbour does not
        conduct (lib and2/not_gate...) is only joined through its face: unreachable -> error, unless allow_side=True.
        strict=True raises BuilderError when a net cannot be routed; strict=False returns the Net with ok False.
        Returns the Net (net.cells, net.repeaters, net.sink_level, net.note); every routed net is listed by `nets`."""
        from simulation import router as R
        try:
            return R.part_route(self, src, sinks, name=name, need=need, box=box, avoid=avoid, keep_clear=keep_clear,
                                floor_y=floor_y, strict=strict, restore=restore, allow_side=allow_side)
        except R.RouteError as e:
            raise BuilderError(f"route: {e}") from None

    def route_all(self, nets, order: str = "auto", box=None, avoid=None, keep_clear=None, floor_y: Optional[int] = None,
                  strict: bool = True, restore: bool = False, allow_side: bool = False, need: Optional[int] = None):
        """Route several nets at once: `nets` = [(src, sinks), (src, sinks, name), ...] or dicts with src, sinks, name,
        need, restore, allow_side. order='auto' (most constrained pin first, then shortest), 'short', 'constrained' or
        'given'. Nets that fail are retried after ripping up the nets around them (docs/ROUTER.md); the error names the
        blocking nets and draws a small area map. Returns the Nets in the order given."""
        from simulation import router as R
        try:
            return R.part_route_all(self, nets, order=order, box=box, avoid=avoid, keep_clear=keep_clear, floor_y=floor_y,
                                    strict=strict, restore=restore, allow_side=allow_side, need=need)
        except R.RouteError as e:
            raise BuilderError(f"route_all: {e}") from None

    # -- cell building / merging
    def _build_cell(self, pd: PartDef, params: dict) -> _PartResult:
        key = (id(pd), tuple(sorted(params.items())))
        res = self._ctx.cache.get(key)
        if res is not None:
            return res
        if self._ctx.depth >= 32:
            raise BuilderError("parts nested more than 32 deep (recursion?)")
        sub = Part(self._ctx, "")
        self._ctx.depth += 1
        try:
            pd.fn(sub, **params)
        finally:
            self._ctx.depth -= 1
        ports = {k: (v, "io", None) for k, v in pd.ports.items()}
        ports.update(sub._ports)
        pinfo = dict(sub._pinfo)
        for name, (pos, kind, _) in sub._ports.items():   # a port declared on the very cell of an inner port is an alias of it
            if "." in name or name in pinfo:
                continue
            for other, (opos, okind, _f) in sub._ports.items():
                if other != name and "." in other and other in pinfo and opos == pos and okind == kind:
                    pinfo[name] = pinfo[other]
                    break
        res = _PartResult(sub._cells, ports, sub._labels, sub._entities, pinfo)
        self._ctx.cache[key] = res
        return res

    def _merge(self, res: _PartResult, inst_name: str, pd: PartDef, at: P, rot: int,
               mirror: Optional[str], line: Optional[int], params: dict) -> Inst:
        ctx = self._ctx
        ctx.count(len(res.cells))
        if ctx.written > ctx.max_blocks * 4 or len(self._cells) + len(res.cells) > ctx.max_blocks * 2:
            raise BuildLimit(f"build exceeds {ctx.max_blocks} blocks")
        prefix = f"{self._name}/{inst_name}" if self._name else inst_name
        omap: Dict[int, int] = {}
        ax, ay, az = at
        for pos, (state, nbt, o, kind) in res.cells.items():
            x, y, z = rotate_point(pos, rot, mirror)
            no = omap.get(o)
            if no is None:
                old_path, old_line = ctx.origins[o]
                no = omap[o] = ctx.origin(f"{inst_name}/{old_path}" if old_path else inst_name, old_line)
            ns = transform_state(state, rot, mirror) if (rot or mirror) else state
            self._put((x + ax, y + ay, z + az), ns, nbt, no, kind)
        ports = {}
        for pname, (pp, pkind, pface) in res.ports.items():
            q = P(*rotate_point(pp, rot, mirror)) + at
            ports[pname] = q
            face = _dir_transform(pface, rot, mirror) if pface else None
            self._ports[f"{inst_name}.{pname}"] = (q, pkind, face)
            self._pinfo[f"{inst_name}.{pname}"] = res.pinfo.get(pname) or (pd.name, pname)
        for lname, (a, b) in res.labels.items():
            a2, b2 = P(*rotate_point(a, rot, mirror)) + at, P(*rotate_point(b, rot, mirror)) + at
            self._labels[f"{inst_name}.{lname}"] = (P(min(a2.x, b2.x), min(a2.y, b2.y), min(a2.z, b2.z)),
                                                    P(max(a2.x, b2.x), max(a2.y, b2.y), max(a2.z, b2.z)))
        for (ex, ey, ez, eid, enbt) in res.entities:
            cx, cz = ex - 0.5, ez - 0.5  # rotate about block centres
            px, _, pz = _rotate_f(cx, 0, cz, rot, mirror)
            self._entities.append((px + 0.5 + ax, ey + ay, pz + 0.5 + az, eid, enbt))
        self._insts.append({"name": prefix, "part": pd.name, "at": list(at), "rot": rot,
                            "mirror": mirror, "line": line, "blocks": len(res.cells)})
        return Inst(prefix, pd.name, at, rot, mirror, ports)


def _dir_transform(d: str, rot: int, mirror: Optional[str]) -> str:
    return parse_state(transform_state(f"minecraft:x[facing={d}]", rot, mirror))[1]["facing"] if d else d


def _rotate_f(x, y, z, rot, mirror):
    if mirror == "x":
        x = -x
    elif mirror == "z":
        z = -z
    for _ in range(rot // 90):
        x, z = -z, x
    return x, y, z


def _load_library(ctx: _Ctx, path: str) -> _PartResult:
    if ctx.lib_root is None:
        raise BuilderError("use(): no library root configured for this compile (pass lib_root=...)")
    root = os.path.realpath(ctx.lib_root)
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(root + os.sep) or not full.endswith((".litematic", ".schem", ".schematic")):
        raise BuilderError(f"use(): {path!r} must be a .litematic/.schem file under the library root")
    res = ctx.lib_cache.get(full)
    if res is None:
        if not os.path.isfile(full):
            raise BuilderError(f"use(): library cell {path!r} not found")
        blocks, ents, _ = load_normalized(full)
        o = ctx.origin(os.path.splitext(os.path.basename(path))[0], None)
        cells = {pos: (st, nbt, o, POINT) for pos, (st, nbt) in blocks.items()}
        ports = {}
        side = full + ".ports.json"
        if os.path.isfile(side):
            with open(side) as f:
                ports = {k: (_pos(v, f"port {k!r}"), "io", None) for k, v in json.load(f).items()}
        res = ctx.lib_cache[full] = _PartResult(cells, ports, {}, [(e[0], e[1], e[2], e[3], e[4]) for e in ents])
    return res


# --- state factories -----------------------------------------------------------------

def _bool(v) -> str:
    return "true" if v is True else "false" if v is False else str(v)


def block(block_id: str, **props) -> str:
    """Generic state string: block("oak_stairs", facing="east", half="top")."""
    bid = block_id if ":" in block_id else "minecraft:" + block_id
    return format_state(bid, {k: _bool(v) for k, v in props.items()})


def air() -> str:
    return "minecraft:air"


def stone() -> str:
    return "minecraft:stone"


def glass() -> str:
    return "minecraft:glass"


def dirt() -> str:
    return "minecraft:dirt"


def solid(material: str = "stone") -> str:
    """Any plain full block by name: solid("smooth_stone"), solid("oak_planks")."""
    return block(material)


def redstone_block() -> str:
    return "minecraft:redstone_block"


def wire() -> str:
    """Redstone dust; connections are derived by the game/simulator."""
    return "minecraft:redstone_wire"


def lamp(lit: bool = False) -> str:
    return block("redstone_lamp", lit=lit)


def lever(face: str = "wall", facing: str = "north", powered: bool = False) -> str:
    """face=floor|wall|ceiling. wall: attached to the block BEHIND it, facing = side it sticks out
    toward (lever(face="wall", facing="east") hangs on the block to its west). floor/ceiling:
    facing = direction of the handle's axis."""
    return block("lever", face=face, facing=facing, powered=powered)


def button(facing: str = "north", face: str = "wall", powered: bool = False, material: str = "stone") -> str:
    """Same attachment rules as lever; material: stone, oak, ..."""
    return block(f"{material}_button", face=face, facing=facing, powered=powered)


def pressure_plate(material: str = "stone", powered: bool = False) -> str:
    if material in ("light_weighted", "heavy_weighted"):
        return block(f"{material}_pressure_plate", power=15 if powered else 0)
    return block(f"{material}_pressure_plate", powered=powered)


def repeater(flow: Optional[str] = None, delay: int = 1, locked: bool = False, powered: bool = False,
             facing: Optional[str] = None) -> str:
    """flow = direction the signal travels (input enters the opposite side). Minecraft's raw
    `facing` property points at the INPUT side, i.e. opposite(flow); pass facing= to use it raw."""
    if (flow is None) == (facing is None):
        raise BuilderError("repeater(): pass exactly one of flow= or facing=")
    return block("repeater", facing=facing if facing is not None else _OPPOSITE.get(flow, flow),
                 delay=delay, locked=locked, powered=powered)


def comparator(flow: Optional[str] = None, mode: str = "compare", powered: bool = False,
               facing: Optional[str] = None) -> str:
    """Same flow/facing convention as repeater(); mode is "compare" or "subtract"."""
    if (flow is None) == (facing is None):
        raise BuilderError("comparator(): pass exactly one of flow= or facing=")
    return block("comparator", facing=facing if facing is not None else _OPPOSITE.get(flow, flow),
                 mode=mode, powered=powered)


def observer(facing: str = "north", powered: bool = False) -> str:
    """facing = the side that watches; the pulse leaves the opposite (back) side."""
    return block("observer", facing=facing, powered=powered)


def piston(facing: str = "north", extended: bool = False, sticky: bool = False) -> str:
    return block("sticky_piston" if sticky else "piston", facing=facing, extended=extended)


def sticky_piston(facing: str = "north", extended: bool = False) -> str:
    return piston(facing, extended, sticky=True)


def hopper(facing: str = "down", enabled: bool = True) -> str:
    return block("hopper", facing=facing, enabled=enabled)


def dropper(facing: str = "north", triggered: bool = False) -> str:
    return block("dropper", facing=facing, triggered=triggered)


def dispenser(facing: str = "north", triggered: bool = False) -> str:
    return block("dispenser", facing=facing, triggered=triggered)


def torch(wall: Optional[str] = None, lit: bool = True) -> str:
    """Redstone torch. wall=None: standing on the block below. wall="east": wall torch that
    sticks out toward east (attached to the block on its west side)."""
    if wall is None:
        return block("redstone_torch", lit=lit)
    return block("redstone_wall_torch", facing=wall, lit=lit)


def chest(facing: str = "north", kind: str = "single") -> str:
    return block("chest", facing=facing, type=kind)


def barrel(facing: str = "north") -> str:
    return block("barrel", facing=facing)


def slab(material: str = "stone", type: str = "bottom") -> str:  # noqa: A002
    return block(f"{material}_slab", type=type)


def stairs(material: str = "stone", facing: str = "north", half: str = "bottom", shape: str = "straight") -> str:
    return block(f"{material}_stairs", facing=facing, half=half, shape=shape)


def target() -> str:
    return "minecraft:target"


def note_block() -> str:
    return "minecraft:note_block"


def daylight_detector(inverted: bool = False) -> str:
    return block("daylight_detector", inverted=inverted)


def _is_dust(state: Optional[str]) -> bool:
    return state is not None and state.split("[", 1)[0] == "minecraft:redstone_wire"


_THIN = ("redstone_wire", "repeater", "comparator", "redstone_torch", "redstone_wall_torch", "lever", "_button", "_pressure_plate", "rail", "air")


def _conducts(state: Optional[str]) -> bool:
    """Does this block (probably) conduct redstone, i.e. block the diagonal dust connection below it?"""
    if state is None:
        return False
    name = state.split("[", 1)[0].split(":", 1)[-1]
    if "slab" in name:
        return "type=double" in state
    return not any(name == t or name.endswith(t) for t in _THIN)


def _plan_state(plan, pos):
    for c, st in plan:
        if tuple(c) == tuple(pos):
            return st
    return None


def _inline_repeater(state: str, d_in: str, d_out: str) -> bool:
    """A repeater already sitting in the route, flowing the way the route goes (a legitimate in-line cell, kept)."""
    return (d_in == d_out and state.startswith("minecraft:repeater[") and f"facing={_OPPOSITE[d_out]}" in state.split("[", 1)[1].rstrip("]").split(","))


def _cell_set(spec, what: str):
    """`spec` = None | iterable of (x, y, z) cells and/or 6-tuples (x1, y1, z1, x2, y2, z2) boxes -> (cells set, boxes list)."""
    cells, boxes = set(), []
    for item in (spec or ()):
        try:
            if len(item) == 6:
                x1, y1, z1, x2, y2, z2 = item
                boxes.append((min(x1, x2), min(y1, y2), min(z1, z2), max(x1, x2), max(y1, y2), max(z1, z2)))
            else:
                cells.add(tuple(_pos(item, what)))
        except TypeError:
            raise BuilderError(f"{what} entries must be (x, y, z) cells or 6-tuple boxes, got {item!r}") from None
    return cells, boxes


def _in_boxes(c, boxes) -> bool:
    return any(bx[0] <= c[0] <= bx[3] and bx[1] <= c[1] <= bx[4] and bx[2] <= c[2] <= bx[5] for bx in boxes)


def wire_path(b: Part, a, z, support: Optional[str] = "minecraft:stone", repeat_every: int = 15, *, box=None, avoid=None,
              start_exclusive: bool = False, end_exclusive: bool = False, floor_exists: bool = False, floor_y: int = 0,
              taps=None, zfirst: bool = False, check_spacing: bool = False) -> List[P]:
    """Run redstone wire from a to z (inclusive), routed along x then z at a's height; the y difference is climbed
    one block per horizontal step (needs support blocks). Returns the list of path cells (a ... z).

    Cells: an empty cell gets dust over a `support` block (None: no supports); a cell that already holds dust is
    joined (left alone); an INTERIOR cell holding anything else raises BuilderError with its coordinates (nothing is
    written then: the route is planned first and applied only if it is clean). The end cells a and z may hold any
    component (lever, lamp, port dust ...): they are left alone.
    * `avoid`: cells the builder does not know but the task fixes (cells and/or 6-tuple boxes). They are never written
      (not even as supports: a support position inside `avoid` is assumed to exist); an end cell inside `avoid` is
      treated like a known component, an interior cell inside `avoid` is an error.
    * `start_exclusive` / `end_exclusive`: do not write a / z at all (they are fixed devices the builder cannot see:
      the route ends on the cell next to the lamp, or starts on the cell next to the lever).
    * `box`: the build box (x1, y1, z1, x2, y2, z2); any write outside it (dust, repeater, support) is an error.
    * `floor_exists` (+ `floor_y`, default 0): the plane y == floor_y is already there, so no support is written below
      dust at y == floor_y + 1 (otherwise the task would reject the write).
    * Climbing: when an end cell is a component (not dust: a lever, a lamp, a repeater, anything fixed) the climb is
      moved away from it, because dust only steps up or down from dust: one flat cell next to the lever/lamp. If there
      are not enough horizontal steps for that, BuilderError.
    * `check_spacing=True`: a newly written dust cell may not touch FOREIGN dust (dust that is not part of this route):
      orthogonally on the same level, or diagonally one level up/down where dust would connect (nothing solid above
      the lower dust). Touching dust joins the nets (a short), the most common silent routing bug. Off by default.
    * `zfirst=True` routes along z first, then x (default: x first, then z), so a route can arrive at a port along the
      axis the port faces.
    * A repeater replaces every `repeat_every`-th flat, straight cell, never a turn, a climb, a cell next to foreign
      dust/repeater (a branch) or a cell listed in `taps` (future branch points of your own); the next eligible cell
      takes it instead."""
    a, z = _pos(a, "wire_path start"), _pos(z, "wire_path end")
    if repeat_every < 2:
        raise BuilderError("wire_path repeat_every must be >= 2")
    box_t = None
    if box is not None:
        try:
            x1, y1, z1, x2, y2, z2 = box
        except (TypeError, ValueError):
            raise BuilderError(f"wire_path box must be (x1, y1, z1, x2, y2, z2), got {box!r}") from None
        box_t = (min(x1, x2), min(y1, y2), min(z1, z2), max(x1, x2), max(y1, y2), max(z1, z2))
    av_cells, av_boxes = _cell_set(avoid, "wire_path avoid")
    tap_cells, _ = _cell_set(taps, "wire_path taps")

    def avoided(c):
        return tuple(c) in av_cells or _in_boxes(c, av_boxes)

    dx, dy, dz = z.x - a.x, z.y - a.y, z.z - a.z
    xs, zs = [("east" if dx > 0 else "west")] * abs(dx), [("south" if dz > 0 else "north")] * abs(dz)
    steps = zs + xs if zfirst else xs + zs
    n = len(steps)
    a_fixed = start_exclusive or avoided(a)
    z_fixed = end_exclusive or avoided(z)
    a_comp = a_fixed or (b.get(a) is not None and not _is_dust(b.get(a)))
    z_comp = z_fixed or (b.get(z) is not None and not _is_dust(b.get(z)))
    lo = 1 if (dy and a_comp) else 0                     # first climbing step
    hi = n - (1 if (dy and z_comp) else 0)               # climbing must be done before the last step
    if abs(dy) > hi - lo:
        need = abs(dy) + (lo + n - hi)
        raise BuilderError(f"wire_path {tuple(a)} -> {tuple(z)}: height change {dy} needs at least {need} horizontal steps "
                           f"({n} given; a component at an end needs one flat dust cell next to it)")
    cells, dirs, cur = [a], [], a
    for i, d in enumerate(steps):
        cur = cur.step(d)
        if lo <= i < lo + abs(dy):
            cur = cur + (0, 1 if dy > 0 else -1, 0)
        cells.append(cur)
        dirs.append(d)
    last = len(cells) - 1
    # --- plan
    plan, problems, since = [], [], 0
    on_path = set(cells)
    for i, c in enumerate(cells):
        since += 1
        end_cell = i in (0, last)
        if (i == 0 and a_fixed) or (i == last and z_fixed):
            since = 0
            continue
        cur_state = b.get(c)
        if avoided(c):
            if not end_cell:
                problems.append(f"route crosses the fixed cell {tuple(c)}")
            since = 0
            continue
        if cur_state is not None:
            if not end_cell and not _is_dust(cur_state) and not _inline_repeater(cur_state, dirs[i - 1], dirs[i]):
                problems.append(f"would overwrite {cur_state} at {tuple(c)}")
            since = 0
            continue
        flat = 0 < i < last and cells[i - 1].y == c.y == cells[i + 1].y and dirs[i - 1] == dirs[i]
        junction = c in tap_cells or any(
            (nb := c + (ddx, ddy, ddz)) not in on_path and b.get(nb) is not None and b.get(nb).split("[", 1)[0] in
            ("minecraft:redstone_wire", "minecraft:repeater", "minecraft:comparator")
            for ddx, ddz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for ddy in (-1, 0, 1))
        if flat and since >= repeat_every and not junction:
            plan.append((c, repeater(flow=dirs[i])))
            since = 0
        else:
            plan.append((c, wire()))
        below = c + (0, -1, 0)
        if support is not None and not (floor_exists and below.y <= floor_y) and b.get(below) is None and not avoided(below):
            plan.append((below, support))
    for c, st in plan:
        if box_t is not None and not _in_boxes(c, [box_t]):
            hint = " (pass floor_exists=True if the floor is fixed)" if st == support and support is not None and c.y <= floor_y else ""
            problems.append(f"write of {st} at {tuple(c)} is outside the box {box_t}{hint}")
    if check_spacing:
        planned = {tuple(c) for c, st in plan}
        for c, st in plan:
            if st != wire():
                continue
            for ddx, ddz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for ddy in (0, 1, -1):
                    nb = c + (ddx, ddy, ddz)
                    if nb in on_path or tuple(nb) in planned or not _is_dust(b.get(nb)):
                        continue
                    if ddy:                                  # diagonal: connects only if no conductor sits above the LOWER dust
                        above = (c if ddy > 0 else nb) + (0, 1, 0)
                        if _conducts(_plan_state(plan, above) or b.get(above)):
                            continue
                    problems.append(f"dust at {tuple(c)} would touch foreign dust at {tuple(nb)} (check_spacing)")
    if problems:
        raise BuilderError(f"wire_path {tuple(a)} -> {tuple(z)}: " + "; ".join(problems[:6]) + (f"; ... {len(problems)} problems" if len(problems) > 6 else ""))
    for c, st in plan:
        b.set(c, st)
    return cells


# --- Build ---------------------------------------------------------------------------

@dataclasses.dataclass
class Build:
    blocks: List[BlockTuple]
    prov: List[Tuple[str, Optional[int]]]
    entities: List[Tuple[float, float, float, str, Optional[str]]]
    ports: Dict[str, dict]
    labels: Dict[str, dict]
    instances: List[dict]
    errors: List[BuildIssue]
    warnings: List[BuildIssue]
    log: List[str] = dataclasses.field(default_factory=list)
    stats: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def hash(self) -> str:
        """Content hash over blocks + entities (not provenance)."""
        h = hashlib.sha256()
        h.update(json.dumps([self.blocks, self.entities], separators=(",", ":")).encode())
        return h.hexdigest()

    def bounds(self):
        """((minx,miny,minz),(maxx,maxy,maxz)) of blocks, or None if empty."""
        if not self.blocks:
            return None
        xs, ys, zs = zip(*((b[0], b[1], b[2]) for b in self.blocks))
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def provenance(self, x: int, y: int, z: int) -> Optional[Tuple[str, Optional[int]]]:
        idx = self.__dict__.get("_prov_index")
        if idx is None:
            idx = self.__dict__["_prov_index"] = {(b[0], b[1], b[2]): p for b, p in zip(self.blocks, self.prov)}
        return idx.get((x, y, z))

    def state_dict(self) -> Dict[Tuple[int, int, int], Tuple[str, Optional[str]]]:
        return {(b[0], b[1], b[2]): (b[3], b[4]) for b in self.blocks}

    def to_json(self) -> List[dict]:
        """Flat interchange list of {x,y,z,state[,nbt]} dicts."""
        out = []
        for x, y, z, st, nbt in self.blocks:
            d = {"x": x, "y": y, "z": z, "state": st}
            if nbt is not None:
                d["nbt"] = nbt
            out.append(d)
        return out

    def summary(self) -> str:
        """Short text report for a model: counts, bounds, ports, issues."""
        b = self.bounds()
        lines = [f"{len(self.blocks)} blocks, {len(self.entities)} entities, bounds {b[0]}..{b[1]}" if b
                 else "0 blocks"]
        if self.ports:
            lines.append("ports: " + ", ".join(f"{k}@{tuple(v['pos'])}({v['kind']})" for k, v in self.ports.items()))
        lines += [f"ERROR {e}" for e in self.errors] + [f"warning {w}" for w in self.warnings]
        return "\n".join(lines)

    def schematic(self, origin: Tuple[int, int, int] = (0, 0, 0)):
        """Nucleation Schematic with blocks translated so the min corner sits at `origin`."""
        import nucleation
        sc = nucleation.Schematic.create("mira_build")
        b = self.bounds()
        ox, oy, oz = (0, 0, 0) if b is None else (origin[i] - b[0][i] for i in range(3))
        for x, y, z, st, nbt in self.blocks:
            sc.set_block(x + ox, y + oy, z + oz, st)
            if nbt is not None:
                tree = parse_snbt(nbt)
                bid = parse_state(st)[0]
                tree.setdefault("id", ("str", bid))
                sc.set_block_entity(x + ox, y + oy, z + oz, tree["id"][1], _snbt_ser(tree))
        for ex, ey, ez, eid, enbt in self.entities:
            tree = parse_snbt(enbt) if enbt else {}
            tree["id"] = ("str", eid)
            tree["Pos"] = [("num", f"{v + o!r}D") for v, o in zip((ex, ey, ez), (ox, oy, oz))]
            sc.add_entity_from_snbt(_snbt_ser(tree))
        return sc

    def local(self, pos) -> Tuple[int, int, int]:
        """Build coordinates -> coordinates inside schematic() / simulate() (min corner at 0,0,0)."""
        mn = self.bounds()[0]
        return (pos[0] - mn[0], pos[1] - mn[1], pos[2] - mn[2])

    def simulate(self, settle: str = "Placement"):
        """Nucleation TickSimulation of this build. settle="Placement" resolves at-rest states as if
        every block were placed by hand (InWorld does not propagate initial power). Use
        build.local(pos) for the coordinates sim.get_block / use_block expect."""
        import nucleation
        mode = getattr(nucleation.TickSettleMode, settle)
        return nucleation.TickSimulation.from_schematic(self.schematic(), mode, 0, 0, 0, "")

    def save(self, path: str) -> None:
        """Write .litematic / .schem via Nucleation (format from extension)."""
        sc = self.schematic()
        if path.endswith(".litematic"):
            sc.save_to_file_with_format(path, "litematic", "")
        elif path.endswith((".schem", ".schematic")):
            sc.save_to_file_with_format(path, "schematic", "")
        else:
            sc.save_to_file(path)

    def to_dict(self) -> dict:
        return {"blocks": self.blocks, "prov": self.prov, "entities": self.entities, "ports": self.ports,
                "labels": self.labels, "instances": self.instances, "errors": [e.to_dict() for e in self.errors],
                "warnings": [w.to_dict() for w in self.warnings], "log": self.log, "stats": self.stats}


def _finish(ctx: _Ctx, root: Part) -> Build:
    stats = {"writes": ctx.written}
    if ctx.nets:
        stats["nets"] = ctx.nets
    if ctx.route_infos:
        ctx.warn("route", f"{ctx.route_infos} dust port(s) were joined from a side other than their face; harmless (the inward neighbour "
                 "connects to the dust). `nets --warnings` lists them", limit=300)
    cells = sorted(root._cells.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0]))
    blocks = [(x, y, z, st, nbt) for (x, y, z), (st, nbt, _, _) in cells]
    prov = [ctx.origins[c[2]] for _, c in cells]
    ports = {k: {"pos": list(p), "kind": kind, "face": face} for k, (p, kind, face) in sorted(root._ports.items())}
    labels = {k: {"min": list(a), "max": list(b)} for k, (a, b) in sorted(root._labels.items())}
    ents = sorted(root._entities, key=lambda e: (e[1], e[2], e[0], e[3]))
    return Build(blocks, [tuple(p) for p in prov], ents, ports, labels, root._insts,
                 ctx.errors, ctx.warnings, ctx.log, stats)


# --- sandbox -------------------------------------------------------------------------

_FORBIDDEN_NAMES = frozenset({"eval", "exec", "compile", "open", "input", "breakpoint", "getattr", "setattr",
                              "delattr", "globals", "locals", "vars", "dir", "memoryview", "BaseException",
                              "type", "object", "super"})


# frame / code / traceback attributes would let a generator or exception walk out to real globals
_FORBIDDEN_ATTRS = frozenset({"gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame",
                              "ag_code", "ag_await", "f_back", "f_globals", "f_locals", "f_builtins", "f_code",
                              "f_trace", "tb_frame", "tb_next", "co_code", "mro"})


def check_source(tree: ast.AST) -> List[Tuple[int, str]]:
    """Static checks for model-written source: returns (line, message) violations."""
    bad = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in _SAFE_MODULES:
                    bad.append((line, f"import of {a.name!r} is not allowed (allowed: {', '.join(sorted(_SAFE_MODULES))})"))
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in _SAFE_MODULES:
                bad.append((line, f"import from {node.module!r} is not allowed (allowed: {', '.join(sorted(_SAFE_MODULES))})"))
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in _FORBIDDEN_ATTRS):
            bad.append((line, f"access to attribute {node.attr!r} is not allowed"))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and (
                node.id in _FORBIDDEN_NAMES or node.id.startswith("__")):
            bad.append((line, f"use of name {node.id!r} is not allowed"))
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            bad.append((line, f"{type(node).__name__} is not allowed"))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            bad.append((line, "bare except is not allowed"))
    return bad


_PUBLIC = ["Byte", "P", "Part", "Inst", "PartDef", "part", "wire_path", "items", "opposite", "DIRS", "DIR_VEC", "BuilderError",
           "NORTH", "EAST", "SOUTH", "WEST", "UP", "DOWN", "block", "air", "stone", "glass", "dirt", "solid",
           "redstone_block", "wire", "lamp", "lever", "button", "pressure_plate", "repeater", "comparator",
           "observer", "piston", "sticky_piston", "hopper", "dropper", "dispenser", "torch", "chest", "barrel",
           "slab", "stairs", "target", "note_block", "daylight_detector"]
__all__ = _PUBLIC + ["Build", "BuildIssue", "decompile", "DecompileError"]


def _synthetic(name: str, attrs: dict) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    m.__all__ = list(attrs)
    return m


def _safe_modules() -> Dict[str, types.ModuleType]:
    import functools
    g = globals()
    return {
        "simulation.builder": _synthetic("simulation.builder", {k: g[k] for k in _PUBLIC}),
        "builder": _synthetic("builder", {k: g[k] for k in _PUBLIC}),
        "math": math,
        "itertools": itertools,
        "functools": _synthetic("functools", {k: getattr(functools, k) for k in ("reduce", "partial", "lru_cache", "cache")}),
        "typing": _synthetic("typing", {k: object for k in ("List", "Dict", "Tuple", "Optional", "Any", "Union", "Iterable")}),
    }


_SAFE_MODULES = frozenset({"simulation.builder", "builder", "math", "itertools", "functools", "typing"})


def _safe_builtins(ctx: _Ctx) -> dict:
    mods = _safe_modules()

    def _import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if level or name not in mods:
            raise ImportError(f"import of {name!r} is not allowed (allowed: {', '.join(sorted(mods))})")
        return mods[name]

    def _print(*args, **kw):
        if len(ctx.log) < 200:
            ctx.log.append(" ".join(str(a) for a in args)[:300])

    names = ("abs all any bool dict divmod enumerate filter float frozenset hasattr int isinstance issubclass "
             "len list map max min pow range repr reversed round set slice sorted str sum tuple zip chr ord "
             "bin hex oct format iter next callable hash").split()
    b = {n: getattr(builtins, n) for n in names}
    for exc in ("Exception ValueError TypeError KeyError IndexError AssertionError ArithmeticError ZeroDivisionError "
                "StopIteration RuntimeError NotImplementedError AttributeError LookupError OverflowError").split():
        b[exc] = getattr(builtins, exc)
    b.update({"BuilderError": BuilderError, "__import__": _import, "print": _print, "True": True, "False": False, "None": None,
              "__build_class__": None})
    return b


def _execute(src: str, max_blocks: int = 2_000_000, validate: bool = True, lib_root: Optional[str] = None) -> Build:
    """Run build source in the restricted namespace (in this process). Never raises for bad source."""
    ctx = _Ctx(max_blocks, validate, lib_root)
    root = Part(ctx, "")
    try:
        tree = ast.parse(src, BUILD_FILENAME)
    except SyntaxError as e:
        ctx.error("syntax", f"{e.msg}", line=e.lineno)
        return _finish(ctx, root)
    violations = check_source(tree)
    if violations:
        for line, msg in violations:
            ctx.error("sandbox", msg, line=line)
        return _finish(ctx, root)
    ns = {"__builtins__": _safe_builtins(ctx), "__name__": "build"}
    try:
        exec(_py_compile(tree, BUILD_FILENAME, "exec"), ns)
        fn = ns.get("build")
        if not callable(fn):
            ctx.error("no_build", "source must define `def build(b):`", line=1)
        else:
            fn(root)
    except BuildLimit as e:
        ctx.error("limit", str(e), line=_tb_line())
    except RecursionError:
        ctx.error("runtime", "maximum recursion depth exceeded", line=_tb_line())
    except MemoryError:
        ctx.error("limit", "out of memory", line=_tb_line())
    except SyntaxError as e:
        ctx.error("syntax", e.msg, line=e.lineno)
    except Exception as e:  # noqa: BLE001
        kind = "api" if isinstance(e, BuilderError) else "runtime"
        ctx.error(kind, f"{type(e).__name__}: {e}", line=_tb_line())
    return _finish(ctx, root)


def _tb_line() -> Optional[int]:
    tb = sys.exc_info()[2]
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == BUILD_FILENAME:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def _from_dict(d: dict) -> Build:
    return Build(
        blocks=[(b[0], b[1], b[2], b[3], b[4]) for b in d["blocks"]],
        prov=[(p[0], p[1]) for p in d["prov"]],
        entities=[tuple(e) for e in d["entities"]], ports=d["ports"], labels=d["labels"],
        instances=d["instances"], errors=[BuildIssue(**e) for e in d["errors"]],
        warnings=[BuildIssue(**w) for w in d["warnings"]], log=d["log"], stats=d.get("stats", {}))


def _error_build(kind: str, msg: str) -> Build:
    return Build([], [], [], {}, {}, [], [BuildIssue(kind, msg)], [])


def compile(src: str, *, sandbox: bool = True, timeout: float = 20.0, max_blocks: int = 2_000_000,  # noqa: A001
            validate: bool = True, lib_root: Optional[str] = None) -> Build:
    """Compile build source (defining `build(b)`) into a Build.

    sandbox=True (default): run in a fresh subprocess (no env, empty cwd, PYTHONHASHSEED=0,
    rlimits, wall-time `timeout`) on top of the AST/import/builtins restrictions. sandbox=False
    runs the same restricted namespace in-process (trusted, generated programs only).
    Errors never raise; inspect build.errors / build.ok."""
    if not sandbox:
        return _execute(src, max_blocks, validate, lib_root)
    req = json.dumps({"src": src, "max_blocks": max_blocks, "validate": validate, "lib_root": lib_root,
                      "timeout": timeout})
    boot = f"import sys; sys.path.insert(0, {ROOT!r}); from simulation.builder import _sandbox_main; _sandbox_main()"
    with tempfile.TemporaryDirectory(prefix="mira_sbx_") as tmp:
        try:
            r = subprocess.run([sys.executable, "-s", "-P", "-c", boot], input=req, capture_output=True,
                               text=True, timeout=timeout + 5, cwd=tmp,
                               env={"PYTHONHASHSEED": "0", "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"})
        except subprocess.TimeoutExpired:
            return _error_build("timeout", f"build did not finish within {timeout:g}s")
    if r.returncode != 0 or not r.stdout.strip():
        tail = (r.stderr or "").strip().splitlines()[-3:]
        kind = "limit" if r.returncode in (-9, -24, -11) or "MemoryError" in (r.stderr or "") else "sandbox"
        return _error_build(kind, f"sandbox process failed (exit {r.returncode}): {' | '.join(tail)}"[:500])
    return _from_dict(json.loads(r.stdout))


def _sandbox_main() -> None:
    """Child entry: read request JSON on stdin, apply rlimits, execute, write Build JSON to stdout."""
    import resource
    req = json.loads(sys.stdin.read())
    out = sys.stdout
    sys.stdout = open(os.devnull, "w")
    t = int(req["timeout"]) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (t, t + 1))
    for lim, val in ((resource.RLIMIT_AS, 6 << 30), (resource.RLIMIT_NOFILE, 32), (resource.RLIMIT_FSIZE, 0)):
        resource.setrlimit(lim, (val, val))
    sys.setrecursionlimit(600)
    build = _execute(req["src"], req["max_blocks"], req["validate"], req["lib_root"])
    out.write(json.dumps(build.to_dict(), separators=(",", ":")))
    out.flush()


# --- loading schematics (Nucleation) -------------------------------------------------

def _num_f(tok) -> float:
    return float(tok[1].rstrip("dDfF"))


def load_normalized(path_or_schem, strict_states: bool = False):
    """Load a .litematic/.schem (or Nucleation Schematic) -> (blocks, entities, offset).

    blocks: {(x,y,z): (state, nbt)} translated so the min non-air corner is (0,0,0); entities:
    [(x, y, z, id, nbt)] with Pos shifted by the same offset; offset: the (x,y,z) subtracted.
    States are canonicalized with normalize(strict=False); block-entity x/y/z keys are dropped."""
    import nucleation
    sc = nucleation.Schematic.load_from_file(path_or_schem) if isinstance(path_or_schem, str) else path_or_schem
    raw = json.loads(sc.get_non_air_blocks_json())
    raw = [r for r in raw if r["name"] not in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")]
    if not raw:
        offset = (0, 0, 0)
    else:
        offset = (min(r["x"] for r in raw), min(r["y"] for r in raw), min(r["z"] for r in raw))
    ox, oy, oz = offset
    blocks: Dict[Tuple[int, int, int], Tuple[str, Optional[str]]] = {}
    memo: Dict[Tuple[str, tuple], str] = {}
    for r in raw:
        key = (r["name"], tuple(map(tuple, r["properties"])))
        st = memo.get(key)
        if st is None:
            try:
                st = memo[key] = normalize(format_state(r["name"], dict(r["properties"])), strict=strict_states)
            except StateError as e:
                raise DecompileError("bad_state", str(e)[:150]) from None
        blocks[(r["x"] - ox, r["y"] - oy, r["z"] - oz)] = (st, None)
    for be in json.loads(sc.get_all_block_entities_snbt_json()):
        px, py, pz = be["position"]
        pos = (px - ox, py - oy, pz - oz)
        if pos not in blocks:
            continue
        tree = parse_snbt(be["snbt"])
        for k in ("x", "y", "z"):
            tree.pop(k, None)
        blocks[pos] = (blocks[pos][0], _snbt_ser(tree) if tree else None)
    entities = []
    for snbt in json.loads(sc.get_entities_snbt_json()):
        tree = parse_snbt(snbt)
        pos = _pop_vec3(tree)
        eid = tree.pop("id", None)
        if pos is None or eid is None:
            continue
        entities.append((pos[0] - ox, pos[1] - oy, pos[2] - oz, eid[1], _snbt_ser(tree) if tree else None))
    entities.sort(key=lambda e: (e[1], e[2], e[0], e[3]))
    return blocks, entities, offset


# --- decompile -----------------------------------------------------------------------

class DecompileError(Exception):
    def __init__(self, reason: str, message: str = ""):
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason


_KEY_CHARS = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
              "#$%&*+-/:;<=>?@^~!|(){}`,_")


def _short_state(state: str) -> str:
    """Printable form: drop `minecraft:` and default-valued props (compile() restores them).
    States that don't validate against 1.21.0 are printed verbatim."""
    from simulation.blockstates import block_defaults
    try:
        if normalize(state, strict=True) != state:
            return state
    except StateError:
        return state
    bid, props = parse_state(state)
    d = block_defaults(bid)
    props = {k: v for k, v in props.items() if d.get(k) != v}
    return format_state(bid[len("minecraft:"):], props)


def _greedy_boxes(cells: Dict[Tuple[int, int, int], int], min_fill: int):
    """Greedy x-then-z-then-y box merge per kind. Returns (boxes [(lo, hi, kind)], leftover dict)."""
    free = dict(cells)
    boxes = []
    for pos in sorted(cells, key=lambda p: (p[1], p[2], p[0])):
        k = free.get(pos)
        if k is None:
            continue
        x, y, z = pos
        x2 = x
        while free.get((x2 + 1, y, z)) == k:
            x2 += 1
        z2 = z
        while all(free.get((xx, y, z2 + 1)) == k for xx in range(x, x2 + 1)):
            z2 += 1
        y2 = y
        while all(free.get((xx, y2 + 1, zz)) == k for xx in range(x, x2 + 1) for zz in range(z, z2 + 1)):
            y2 += 1
        if (x2 - x + 1) * (z2 - z + 1) * (y2 - y + 1) >= min_fill:
            for xx in range(x, x2 + 1):
                for zz in range(z, z2 + 1):
                    for yy in range(y, y2 + 1):
                        del free[(xx, yy, zz)]
            boxes.append(((x, y, z), (x2, y2, z2), k))
    return boxes, free


def _find_arrays(sc, cells: Dict[Tuple[int, int, int], int], offset, min_blocks: int = 24):
    """Autostack motifs, verified exactly against `cells`. Returns (array dicts, claimed positions)."""
    if sc is None:
        return [], set()
    try:
        cands = json.loads(nucleation_autostack(sc))
    except Exception:  # noqa: BLE001
        return [], set()
    arrays, claimed = [], set()
    ox, oy, oz = offset
    for c in cands:
        vecs = [tuple(v) for v in c["vectors"]]
        if len(vecs) not in (1, 2):
            continue
        sub = lambda t: (t[0] - ox, t[1] - oy, t[2] - oz)  # noqa: E731
        cmin, cmax, rmin, rmax = sub(c["cell_min"]), sub(c["cell_max"]), sub(c["region_min"]), sub(c["region_max"])
        size = [cmax[i] - cmin[i] + 1 for i in range(3)]
        axes = []
        for v in vecs:  # each step must move along one axis by at least the cell size (no overlap)
            nz = [a for a in range(3) if v[a]]
            if len(nz) != 1 or abs(v[nz[0]]) < size[nz[0]]:
                break
            axes.append(nz[0])
        else:
            if size[0] * size[1] * size[2] > 8192:
                continue
            avail = {p: k for p, k in cells.items() if p not in claimed}
            base = _cell_content(avail, cmin, cmax)
            if not base:
                continue
            ranges = []
            for v, ax in zip(vecs, axes):
                i1, i2 = (rmin[ax] - cmin[ax]) / v[ax], (rmax[ax] - cmax[ax]) / v[ax]
                ranges.append(range(math.ceil(min(i1, i2)), math.floor(max(i1, i2)) + 1))
            if math.prod(len(r) for r in ranges) > 4096:
                continue
            ok = {}
            for idx in itertools.product(*ranges):
                off = tuple(sum(i * v[a] for i, v in zip(idx, vecs)) for a in range(3))
                lo = tuple(cmin[a] + off[a] for a in range(3))
                if _cell_content(avail, lo, tuple(cmax[a] + off[a] for a in range(3))) == base:
                    ok[idx] = off
            best = _best_lattice(ok, len(vecs))
            if best is None:
                continue
            ns, start = best
            if math.prod(ns) < 3 or math.prod(ns) * len(base) < min_blocks:
                continue
            origin = tuple(cmin[a] + sum(start[i] * vecs[i][a] for i in range(len(vecs))) for a in range(3))
            for idx in itertools.product(*(range(s0, s0 + n) for s0, n in zip(start, ns))):
                off = tuple(sum(i * v[a] for i, v in zip(idx, vecs)) for a in range(3))
                for p in base:
                    claimed.add((cmin[0] + p[0] + off[0], cmin[1] + p[1] + off[1], cmin[2] + p[2] + off[2]))
            arrays.append({"cell": base, "vecs": vecs, "ns": ns, "origin": origin})
    return arrays, claimed


def nucleation_autostack(sc) -> str:
    import nucleation
    return nucleation.Autostack.detect_structures(sc)


def _cell_content(cells, lo, hi):
    """{pos relative to lo: kind} for cells inside the box lo..hi."""
    out = {}
    for x in range(lo[0], hi[0] + 1):
        for y in range(lo[1], hi[1] + 1):
            for z in range(lo[2], hi[2] + 1):
                k = cells.get((x, y, z))
                if k is not None:
                    out[(x - lo[0], y - lo[1], z - lo[2])] = k
    return out


def _best_lattice(ok: Dict[tuple, tuple], dims: int):
    """Largest all-matching rectangle (1D run or 2D rectangle) of lattice indices -> (counts, start)."""
    if not ok:
        return None
    if dims == 1:
        idxs = sorted(i[0] for i in ok)
        best, run_start, prev = (0, 0), idxs[0], None
        for i in idxs:
            if prev is not None and i != prev + 1:
                run_start = i
            if i - run_start + 1 > best[0]:
                best = (i - run_start + 1, run_start)
            prev = i
        return (best[0],), (best[1],)
    rows: Dict[int, set] = {}
    for a, b in ok:
        rows.setdefault(a, set()).add(b)
    best, best_area = None, 0
    keys = sorted(rows)
    for i, a0 in enumerate(keys):  # extend rectangles row-run by row-run
        common = set(rows[a0])
        a1 = a0
        j = i
        while True:
            # largest contiguous run of b in `common`
            run, run_start, prev, brun = 0, None, None, None
            for b in sorted(common):
                if prev is None or b != prev + 1:
                    run, run_start = 0, b
                run += 1
                if brun is None or run > brun[0]:
                    brun = (run, run_start)
                prev = b
            if brun is None:
                break
            area = (a1 - a0 + 1) * brun[0]
            if area > best_area:
                best_area, best = area, ((a1 - a0 + 1, brun[0]), (a0, brun[1]))
            j += 1
            if j >= len(keys) or keys[j] != a1 + 1:
                break
            a1 = keys[j]
            common &= rows[a1]
    return best


class _Printer:
    """Deterministic program printer for decompile()."""

    def __init__(self, kinds: List[Tuple[str, Optional[str]]], counts: Dict[int, int]):
        order = sorted(range(len(kinds)), key=lambda i: (-counts.get(i, 0), kinds[i][0], kinds[i][1] or ""))
        self.kinds = kinds
        self.key: Dict[int, str] = {}
        self.layer_ok: set = set()
        for n, i in enumerate(order):
            if n < len(_KEY_CHARS):
                self.key[i] = _KEY_CHARS[n]
                self.layer_ok.add(i)
            else:
                self.key[i] = f"k{n}"

    def header(self) -> List[str]:
        lines = ["from simulation.builder import *", "", "K = {"]
        for i in sorted(self.key, key=lambda i: (len(self.key[i]), self.key[i])):
            st, nbt = self.kinds[i]
            val = json.dumps(_short_state(st), ensure_ascii=False) if nbt is None else f"({json.dumps(_short_state(st), ensure_ascii=False)}, {json.dumps(nbt, ensure_ascii=False)})"
            lines.append(f"    {json.dumps(self.key[i], ensure_ascii=False)}: {val},")
        lines += ["}", ""]
        return lines

    def ref(self, i: int) -> str:
        return f"K[{json.dumps(self.key[i], ensure_ascii=False)}]"


def _fmt(p) -> str:
    return f"({p[0]},{p[1]},{p[2]})"


def _emit_body(pr: _Printer, cells: Dict[Tuple[int, int, int], int], var: str, min_fill: int, ind: str) -> List[str]:
    """Statements reproducing `cells` on Part `var` (fills, layers, points)."""
    out = []
    boxes, rest = _greedy_boxes(cells, min_fill)
    for lo, hi, k in boxes:
        out.append(f"{ind}{var}.fill({_fmt(lo)},{_fmt(hi)},{pr.ref(k)})")
    by_y: Dict[int, Dict[Tuple[int, int, int], int]] = {}
    for p, k in rest.items():
        by_y.setdefault(p[1], {})[p] = k
    sparse: Dict[int, List[Tuple[int, int, int]]] = {}
    for y in sorted(by_y):
        layer = by_y[y]
        if all(k in pr.layer_ok for k in layer.values()):
            xs, zs = [p[0] for p in layer], [p[2] for p in layer]
            x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
            cost_layers = (x1 - x0 + 4) * (z1 - z0 + 1)
            cost_points = 8 * len(layer) + 6 * len({k for k in layer.values()})
            if cost_layers < cost_points:
                rows = []
                for z in range(z0, z1 + 1):
                    rows.append("".join(pr.key[layer[(x, y, z)]] if (x, y, z) in layer else "."
                                        for x in range(x0, x1 + 1)))
                body = ",\n".join(f"{ind}    {json.dumps(r, ensure_ascii=False)}" for r in rows)
                out.append(f"{ind}{var}.layers({_fmt((x0, y, z0))}, K, [[\n{body}]])")
                continue
        for p, k in layer.items():
            sparse.setdefault(k, []).append(p)
    for k in sorted(sparse, key=lambda k: pr.key[k]):
        pts = ",".join(_fmt(p) for p in sorted(sparse[k], key=lambda p: (p[1], p[2], p[0])))
        out.append(f"{ind}{var}.points({pr.ref(k)},{pts})")
    return out


def decompile(src, min_fill: int = 3, motifs: bool = True, verify: bool = True) -> str:
    """.litematic/.schem path (or Nucleation Schematic) -> canonical program text.

    Coordinates are translated so the min block corner is (0,0,0). With verify=True the program
    is compiled again and compared with the original (states, block-entity NBT, entities);
    a mismatch raises DecompileError(reason=...). The same input always yields the same text."""
    import nucleation
    sc = nucleation.Schematic.load_from_file(src) if isinstance(src, str) else src
    blocks, entities, offset = load_normalized(sc)
    text = _print_program(sc if motifs else None, blocks, entities, offset, min_fill)
    if verify:
        _verify_roundtrip(text, blocks, entities)
    return text


def _print_program(sc, blocks, entities, offset, min_fill) -> str:
    kinds: List[Tuple[str, Optional[str]]] = []
    kind_ids: Dict[Tuple[str, Optional[str]], int] = {}
    cells: Dict[Tuple[int, int, int], int] = {}
    for pos, spec in blocks.items():
        k = kind_ids.get(spec)
        if k is None:
            k = kind_ids[spec] = len(kinds)
            kinds.append(spec)
        cells[pos] = k
    counts: Dict[int, int] = {}
    for k in cells.values():
        counts[k] = counts.get(k, 0) + 1
    pr = _Printer(kinds, counts)
    arrays, claimed = _find_arrays(sc, cells, offset)
    lines = pr.header()
    body: List[str] = []
    for n, arr in enumerate(arrays):
        cell_cells = arr["cell"]
        lines += ["@part", f"def cell{n}(p):"] + (_emit_body(pr, cell_cells, "p", min_fill, "    ") or ["    pass"]) + [""]
        if len(arr["vecs"]) == 1:
            body.append(f"    b.array(cell{n},{arr['ns'][0]},{_fmt(arr['vecs'][0])},at={_fmt(arr['origin'])})")
        else:
            lines += ["@part", f"def row{n}(p):",
                      f"    p.array(cell{n},{arr['ns'][0]},{_fmt(arr['vecs'][0])})", ""]
            body.append(f"    b.array(row{n},{arr['ns'][1]},{_fmt(arr['vecs'][1])},at={_fmt(arr['origin'])})")
    rest = {p: k for p, k in cells.items() if p not in claimed}
    body += _emit_body(pr, rest, "b", min_fill, "    ")
    for (x, y, z, eid, nbt) in entities:
        body.append(f"    b.entity({json.dumps(eid, ensure_ascii=False)},({x!r},{y!r},{z!r})" + (f",{json.dumps(nbt, ensure_ascii=False)})" if nbt else ")"))
    lines.append("def build(b):")
    lines += body or ["    pass"]
    return "\n".join(lines) + "\n"


def _verify_roundtrip(text: str, blocks, entities) -> None:
    b = _execute(text, max_blocks=max(2_000_000, len(blocks) * 2), validate=False)
    if b.errors:
        raise DecompileError("program_error", str(b.errors[0]))
    got = b.state_dict()
    if got != blocks:
        diff = [p for p in blocks if got.get(p) != blocks[p]] + [p for p in got if p not in blocks]
        any_nbt = any(blocks.get(p, (None, None))[1] != got.get(p, (None, None))[1] for p in diff)
        raise DecompileError("nbt_mismatch" if any_nbt and all(
            blocks.get(p, ("",))[0] == got.get(p, ("",))[0] for p in diff) else "block_mismatch",
            f"{len(diff)} differing positions, first {diff[0]}")
    if [tuple(e) for e in b.entities] != [tuple(e) for e in entities]:
        raise DecompileError("entity_mismatch", f"{len(b.entities)} vs {len(entities)} entities")


if __name__ == "__main__":
    src_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not src_path:
        print(__doc__)
    elif src_path.endswith(".py"):
        with open(src_path) as f:
            print(compile(f.read()).summary())
    else:
        print(decompile(src_path))
