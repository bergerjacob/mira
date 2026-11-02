"""Backend-neutral build/plan/trace types shared by the oracle and Nucleation sides.

Coordinates are in the caller's frame; each backend shifts to its own origin
internally and shifts results back, so a diff compares like with like.

Stimulus semantics (identical on both sides, mirrors TraceCapture): ops for tick T
are applied after tick T-1 finished and before tick T runs; the trace entry for T
is the block-state diff after tick T ran. `preroll` idle ticks run before tick 0
(after placement), so `placed` is the state right after placement and `initial`
the state after preroll.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

Pos = Tuple[int, int, int]


def canon_state(state: str) -> str:
    """Canonical block-state string: `minecraft:` prefix, sorted properties."""
    state = state.strip()
    m = re.match(r"^([^\[]+)(?:\[(.*)\])?$", state)
    name, props = m.group(1), m.group(2)
    if ":" not in name:
        name = "minecraft:" + name
    if not props:
        return name
    pairs = sorted(p.split("=", 1) for p in props.split(",") if p)
    return name + "[" + ",".join(f"{k}={v}" for k, v in pairs) + "]"


def state_props(state: str) -> Dict[str, str]:
    m = re.match(r"^[^\[]+(?:\[(.*)\])?$", state)
    if not m or not m.group(1):
        return {}
    return dict(p.split("=", 1) for p in m.group(1).split(",") if p)


def block_name(state: str) -> str:
    return canon_state(state).split("[", 1)[0]


@dataclass(frozen=True)
class Op:
    """One stimulus: `use` right-clicks pos; `set` writes `block` (registry id, default state)."""
    tick: int
    op: str
    pos: Pos
    block: Optional[str] = None

    def to_json(self):
        return {"tick": self.tick, "op": self.op, "pos": list(self.pos), "block": self.block}


def use(tick: int, pos: Pos) -> Op:
    return Op(tick, "use", tuple(pos))


def put(tick: int, pos: Pos, block: str) -> Op:
    if not block.startswith("minecraft:"):
        block = "minecraft:" + block
    return Op(tick, "set", tuple(pos), block)


@dataclass
class Plan:
    ticks: int
    ops: List[Op] = field(default_factory=list)
    probes: List[Pos] = field(default_factory=list)
    settle: str = "place"        # "place" (full update pass) | "known_shape" (quiet)
    preroll: int = 0

    def to_json(self):
        return {"ticks": self.ticks, "ops": [o.to_json() for o in self.ops],
                "probes": [list(p) for p in self.probes], "settle": self.settle,
                "preroll": self.preroll}


def normalize_blocks(blocks: Iterable) -> List[Tuple[int, int, int, str, Optional[dict]]]:
    """Accept (x,y,z,state[,nbt]) tuples or {x,y,z,state[,nbt]} dicts; air dropped."""
    out = []
    for b in blocks:
        if isinstance(b, dict):
            x, y, z, st, nbt = b["x"], b["y"], b["z"], b["state"], b.get("nbt")
        else:
            x, y, z, st = b[:4]
            nbt = b[4] if len(b) > 4 else None
        st = canon_state(st)
        if st.split("[")[0] == "minecraft:air":
            continue
        out.append((int(x), int(y), int(z), st, nbt))
    return out


def blocks_hash(blocks) -> str:
    rows = sorted((x, y, z, s, json.dumps(n, sort_keys=True)) for x, y, z, s, n in blocks)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


@dataclass
class Trace:
    backend: str
    mc_version: str
    data_version: int
    origin: Pos
    n_ticks: int
    placed: Dict[Pos, str]                     # non-air state after placement
    initial: Dict[Pos, str]                    # after preroll (== placed if preroll 0)
    changes: Dict[int, List[Tuple[Pos, str, str]]]   # tick -> [(pos, from, to)]
    wall_ms: Optional[float] = None
    meta: dict = field(default_factory=dict)   # backend extras (inventory events, timings)

    def state_at(self, tick: int) -> Dict[Pos, str]:
        """State after `tick` ran; tick -1 is `initial`."""
        cur = dict(self.initial)
        for t in range(0, tick + 1):
            for pos, _was, now in self.changes.get(t, ()):
                if now.startswith("minecraft:air"):
                    cur.pop(pos, None)
                else:
                    cur[pos] = now
        return cur

    def probe(self, pos: Pos) -> List[str]:
        """State of pos after each tick 0..n_ticks-1."""
        cur = self.initial.get(pos, "minecraft:air")
        by_tick = {t: [c for c in cs if c[0] == pos] for t, cs in self.changes.items()}
        out = []
        for t in range(self.n_ticks):
            for _p, _was, now in by_tick.get(t, ()):
                cur = now
            out.append(cur)
        return out

    def to_json(self):
        enc = lambda d: {f"{x},{y},{z}": s for (x, y, z), s in sorted(d.items())}
        return {
            "backend": self.backend, "mc_version": self.mc_version,
            "data_version": self.data_version, "origin": list(self.origin),
            "n_ticks": self.n_ticks, "placed": enc(self.placed), "initial": enc(self.initial),
            "changes": {str(t): [[*p, a, b] for p, a, b in cs]
                        for t, cs in sorted(self.changes.items())},
            "wall_ms": self.wall_ms, "meta": self.meta,
        }

    @staticmethod
    def from_json(d) -> "Trace":
        dec = lambda m: {tuple(int(v) for v in k.split(",")): s for k, s in m.items()}
        return Trace(
            backend=d["backend"], mc_version=d["mc_version"], data_version=d["data_version"],
            origin=tuple(d["origin"]), n_ticks=d["n_ticks"], placed=dec(d["placed"]),
            initial=dec(d["initial"]),
            changes={int(t): [((c[0], c[1], c[2]), c[3], c[4]) for c in cs]
                     for t, cs in d["changes"].items()},
            wall_ms=d.get("wall_ms"), meta=d.get("meta", {}))


def first_divergence(a: Trace, b: Trace, positions: Optional[Iterable[Pos]] = None):
    """First (tick, pos, state_a, state_b) where the two traces differ, else None.

    tick -1 compares the post-preroll initial state. `positions=None` compares every
    cell either trace has seen.
    """
    a_pos = set(a.initial) | {c[0] for cs in a.changes.values() for c in cs}
    b_pos = set(b.initial) | {c[0] for cs in b.changes.values() for c in cs}
    cellset = set(positions) if positions is not None else a_pos | b_pos
    cells = sorted(cellset)
    air = "minecraft:air"
    sa, sb = a.state_at(-1), b.state_at(-1)
    for p in cells:
        if sa.get(p, air) != sb.get(p, air):
            return (-1, p, sa.get(p, air), sb.get(p, air))
    n = min(a.n_ticks, b.n_ticks)
    for t in range(n):
        touched = {c[0] for c in a.changes.get(t, ())} | {c[0] for c in b.changes.get(t, ())}
        for _pos, _was, now in a.changes.get(t, ()):
            sa[_pos] = air if now.startswith(air) else now
        for _pos, _was, now in b.changes.get(t, ()):
            sb[_pos] = air if now.startswith(air) else now
        for p in sorted(touched):
            if p in cellset and sa.get(p, air) != sb.get(p, air):
                return (t, p, sa.get(p, air), sb.get(p, air))
    return None
