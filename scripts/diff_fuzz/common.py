"""Shared types for the Nucleation-vs-vanilla differential fuzzer (family D, docs/DIFF_FUZZ.md).

A `Case` is one build + stimulus plan + origin. Blocks are `(x, y, z, state, nbt)` tuples with canonical states
(wire is kept bare: both engines re-derive its shape in the placement pass). Stimuli are `use` ops only
(levers, buttons and a few other things a player can right-click); there are no `set` ops (those are commands).
"""
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from tools.oracle.trace import Op, Plan  # noqa: E402

FUZZ_DIR = ROOT / "data" / "fuzz"
MARGIN = 4          # OracleBatch records the build region plus this many blocks on every side

Block = Tuple[int, int, int, str, Optional[str]]


@dataclass
class Case:
    blocks: List[Block]
    ops: List[Tuple[int, Tuple[int, int, int]]]       # (tick, pos) right-clicks
    ticks: int = 48
    preroll: int = 0
    settle: str = "place"
    origin: Tuple[int, int, int] = (0, 100, 0)
    meta: dict = field(default_factory=dict)          # arm, seed, parent, flags (not part of the identity)

    def plan(self) -> Plan:
        return Plan(self.ticks, [Op(t, "use", tuple(p)) for t, p in sorted(self.ops)], [], self.settle,
                    self.preroll, tuple(self.origin))

    def key(self) -> str:
        """Identity of the experiment (build, plan, origin); not the metadata."""
        rows = sorted((x, y, z, s, n or "") for x, y, z, s, n in self.blocks)
        body = json.dumps([rows, sorted((t, list(p)) for t, p in self.ops), self.ticks, self.preroll,
                           self.settle, list(self.origin)])
        return hashlib.sha1(body.encode()).hexdigest()[:16]

    def to_json(self) -> dict:
        return {"blocks": [list(b) for b in sorted(self.blocks, key=lambda b: (b[1], b[2], b[0]))],
                "ops": [[t, list(p)] for t, p in sorted(self.ops)], "ticks": self.ticks, "preroll": self.preroll,
                "settle": self.settle, "origin": list(self.origin), "meta": self.meta}

    @staticmethod
    def from_json(d: dict) -> "Case":
        return Case([(int(b[0]), int(b[1]), int(b[2]), b[3], b[4] if len(b) > 4 else None) for b in d["blocks"]],
                    [(int(t), tuple(int(v) for v in p)) for t, p in d["ops"]], d["ticks"], d.get("preroll", 0),
                    d.get("settle", "place"), tuple(d.get("origin", (0, 100, 0))), dict(d.get("meta", {})))

    def with_(self, **kw) -> "Case":
        d = dict(blocks=self.blocks, ops=self.ops, ticks=self.ticks, preroll=self.preroll, settle=self.settle,
                 origin=self.origin, meta=dict(self.meta))
        d.update(kw)
        return Case(**d)


def jdump(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    tmp.replace(path)


def jload(path, default=None):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default
