"""Contract v2: the scored spec for one task. Written by the task author, never by the model under test.

JSON-serialisable dataclasses. Coordinates are absolute task coordinates; boxes are inclusive
[x1,y1,z1,x2,y2,z2]. The model sees `public_dict()` (ports, description, public vectors); the checker
keeps hidden vectors and seeds.
"""
import random
from dataclasses import dataclass, field, asdict
from typing import Optional

INPUT_KINDS = ("click", "pad")  # click: use_block on a lever/button; pad: redstone_block placed/removed in an empty cell
OUTPUT_KINDS = ("lamp", "wire_power", "piston", "torch", "block_prop", "region")
REGION_MODES = ("air", "solid", "block")  # output=1 iff all cells are air / non-air / the given block
DEFAULT_FORBIDDEN = ("minecraft:command_block", "minecraft:chain_command_block", "minecraft:repeating_command_block",
                     "minecraft:structure_block", "minecraft:jigsaw", "minecraft:test_block",
                     "minecraft:test_instance_block", "minecraft:barrier")


def _tup(v):
    return tuple(v) if v is not None else None


@dataclass
class Port:
    name: str
    kind: str
    pos: tuple
    facing: Optional[str] = None      # informational: side the signal comes from / leaves
    pulse: bool = False               # click ports on buttons: value 1 = press, 0 = nothing
    min_power: int = 1                # wire_power: output=1 iff power >= min_power
    prop: Optional[dict] = None       # block_prop: {"prop": "powered", "value": "true", "block": optional id}
    box: Optional[list] = None        # region: cells to test
    region_mode: str = "air"
    block: Optional[str] = None       # region_mode == "block"
    doc: str = ""

    @property
    def is_input(self):
        return self.kind in INPUT_KINDS

    @property
    def is_output(self):
        return self.kind in OUTPUT_KINDS

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["pos"] = _tup(d["pos"])
        return cls(**d)


@dataclass
class Vector:
    inputs: dict            # port -> 0/1 (ports not listed keep their value)
    expect: dict            # port -> 0/1/None (None = don't care)
    public: bool = True
    label: str = ""


@dataclass
class TruthTable:
    rows: list = field(default_factory=list)   # list[Vector]
    settle: int = 100            # per-vector quiescence budget (ticks)
    extra: int = 2               # extra ticks after quiescence before reading
    shuffle: bool = True         # evaluate in seeded random order, no reset between vectors
    reset_between: bool = False  # restore the at-rest baseline between vectors
    order_independent: bool = True  # combinational: a second pass in another order must agree

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["rows"] = [Vector(**r) for r in d.get("rows", [])]
        return cls(**d)


@dataclass
class Event:
    t: int
    set: dict  # port -> 0/1


@dataclass
class Expect:
    port: str
    value: object       # 0/1, or "init" / "!init" = same as / opposite of the output's at-rest value
    t1: int
    t2: int
    mode: str = "all"   # all: value holds at every tick in [t1,t2]; any: occurs at least once


@dataclass
class Sequence:
    name: str
    events: list
    expect: list
    duration: int
    public: bool = True

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["events"] = [Event(**e) for e in d["events"]]
        d["expect"] = [Expect(**e) for e in d["expect"]]
        return cls(**d)


@dataclass
class ReferenceModel:
    fn: str                      # name registered in env/models.py
    inputs: list
    outputs: list
    n_public: int = 4
    n_hidden: int = 12
    public_seed: int = 1
    hidden_seed: int = 2         # never shown to the model
    mode: str = "comb"           # comb: random order allowed; seq: model carries state, generated order kept
    settle: int = 100
    extra: int = 2
    reset_between: bool = False
    p_one: float = 0.5
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    def generate(self):
        """-> list[Vector] with expected outputs from the python model. Deterministic in the seeds."""
        from env.models import get_model
        fn = get_model(self.fn)
        out = []
        for public, seed, n in ((True, self.public_seed, self.n_public), (False, self.hidden_seed, self.n_hidden)):
            rng = random.Random(seed)
            state = {}
            for i in range(n):
                ins = {p: int(rng.random() < self.p_one) for p in self.inputs}
                exp = fn(state, dict(ins), **self.params)
                out.append(Vector(ins, {k: exp[k] for k in self.outputs}, public, f"model#{i}"))
        return out


@dataclass
class Behaviour:
    truth_table: Optional[TruthTable] = None
    sequences: list = field(default_factory=list)
    reference_model: Optional[ReferenceModel] = None

    @classmethod
    def from_dict(cls, d):
        return cls(
            truth_table=TruthTable.from_dict(d["truth_table"]) if d.get("truth_table") else None,
            sequences=[Sequence.from_dict(s) for s in d.get("sequences", [])],
            reference_model=ReferenceModel.from_dict(d["reference_model"]) if d.get("reference_model") else None,
        )


@dataclass
class Invariants:
    at_rest: bool = True          # quiescent after compile (set False for free-running clocks)
    paste_stable: bool = True     # saved at-rest world is unchanged by InWorld load and by Placement settling
    deterministic: bool = True    # two runs give identical traces
    no_oscillation: bool = True   # every vector settles within budget
    no_forbidden: bool = True
    no_collateral: bool = True    # no block changes outside change_boxes
    keep_clear: bool = True       # cells in Contract.keep_clear stay air at every sampled tick
    no_popoff: bool = True        # every attached component has a supporting block
    change_boxes: list = field(default_factory=list)  # extra boxes where changes are allowed (default: build_box)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class Budgets:
    rest: int = 200        # ticks allowed to reach quiescence after compile
    total: int = 20000     # max simulated ticks per run
    max_blocks: int = 2000


@dataclass
class Contract:
    id: str
    title: str
    tier: str
    description: str
    bbox: list                    # whole world area [x1,y1,z1,x2,y2,z2]
    build_box: list               # where the model may place blocks
    ports: list
    fixed: list = field(default_factory=list)   # [{"box": [...], "state": "..."}] immutable scenery/devices
    behaviour: Behaviour = field(default_factory=Behaviour)
    invariants: Invariants = field(default_factory=Invariants)
    budgets: Budgets = field(default_factory=Budgets)
    forbidden: tuple = DEFAULT_FORBIDDEN
    keep_clear: list = field(default_factory=list)  # boxes the model may not build in and that must stay air (hallway)
    no_write: list = field(default_factory=list)    # boxes the model may not write (cells only pistons may fill)
    eval_seed: int = 0
    depends: dict = field(default_factory=dict)  # output -> [inputs] override for R2 (default: derived from vectors)
    reference: dict = field(default_factory=dict)  # {"blocks": n, "volume": v} for R4, filled from the reference solution
    hints: list = field(default_factory=list)

    # --- (de)serialisation -------------------------------------------------
    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["ports"] = [Port.from_dict(p) for p in d["ports"]]
        d["behaviour"] = Behaviour.from_dict(d.get("behaviour", {}))
        d["invariants"] = Invariants.from_dict(d.get("invariants", {}))
        d["budgets"] = Budgets(**d.get("budgets", {}))
        d["forbidden"] = tuple(d.get("forbidden", DEFAULT_FORBIDDEN))
        return cls(**d)

    def to_dict(self):
        return asdict(self)

    def public_dict(self):
        """What a model may see: no hidden vectors/sequences, no seeds, no reference stats."""
        d = self.to_dict()
        bh = d["behaviour"]
        if bh["truth_table"]:
            bh["truth_table"]["rows"] = [r for r in bh["truth_table"]["rows"] if r["public"]]
        bh["sequences"] = [s for s in bh["sequences"] if s["public"]]
        if bh["reference_model"]:
            rm = bh["reference_model"]
            rm["hidden_seed"] = None
            rm["n_hidden"] = None
        d["eval_seed"] = None
        d["reference"] = {}
        return d

    # --- derived views -----------------------------------------------------
    def port(self, name):
        for p in self.ports:
            if p.name == name:
                return p
        raise KeyError(name)

    @property
    def inputs(self):
        return [p for p in self.ports if p.is_input]

    @property
    def outputs(self):
        return [p for p in self.ports if p.is_output]

    def vectors(self):
        """All vectors (truth table rows + generated model vectors) in authoring order."""
        v = []
        if self.behaviour.truth_table:
            v += self.behaviour.truth_table.rows
        if self.behaviour.reference_model:
            v += self.behaviour.reference_model.generate()
        return v

    def vector_settings(self):
        """(settle, extra, shuffle, reset_between, order_independent) for vectors()."""
        tt, rm = self.behaviour.truth_table, self.behaviour.reference_model
        if tt:
            return tt.settle, tt.extra, tt.shuffle, tt.reset_between, tt.order_independent
        if rm:
            return rm.settle, rm.extra, rm.mode == "comb", rm.reset_between, rm.mode == "comb"
        return 100, 2, False, False, False

    def dependencies(self):
        """output -> sorted inputs it must be connected to (R2). Explicit `depends`, else derived: an input
        matters for an output if two vectors differing only in that input have different expected output, or
        (sequences/stateful) the input is stimulated in any sequence expecting that output."""
        if self.depends:
            return {o: sorted(i) for o, i in self.depends.items()}
        dep = {p.name: set() for p in self.outputs}
        names = [p.name for p in self.inputs]
        full = [(v, {**{n: 0 for n in names}, **v.inputs}) for v in self.vectors()]
        for (va, ia), (vb, ib) in ((a, b) for a in full for b in full):
            diff = [k for k in ia if ia[k] != ib.get(k)]
            if len(diff) == 1:
                for o, e in va.expect.items():
                    if e is not None and vb.expect.get(o) is not None and e != vb.expect[o]:
                        dep[o].add(diff[0])
        for s in self.behaviour.sequences:
            stim = {k for e in s.events for k in e.set}
            for e in s.expect:
                if e.port in dep:
                    dep[e.port] |= stim
        # fall back to all inputs for outputs with no derivable dependency (e.g. single-vector specs)
        for o in dep:
            if not dep[o]:
                dep[o] = set(names)
        return {o: sorted(i) for o, i in dep.items()}

    # --- validation (spec sanity, run when a task is authored) -------------
    def validate(self):
        errs = []
        pnames = [p.name for p in self.ports]
        if len(set(pnames)) != len(pnames):
            errs.append("duplicate port names")
        for p in self.ports:
            if not (p.is_input or p.is_output):
                errs.append(f"port {p.name}: unknown kind {p.kind}")
            if p.kind == "region" and (not p.box or p.region_mode not in REGION_MODES):
                errs.append(f"port {p.name}: region needs box and mode in {REGION_MODES}")
            if p.kind == "region" and p.region_mode == "block" and not p.block:
                errs.append(f"port {p.name}: region_mode=block needs `block`")
        if not any(p.is_input for p in self.ports):
            errs.append("no input ports")
        if not any(p.is_output for p in self.ports):
            errs.append("no output ports")
        names = set(pnames)
        vecs = self.vectors()
        for v in vecs:
            for k in list(v.inputs) + list(v.expect):
                if k not in names:
                    errs.append(f"vector references unknown port {k}")
        for s in self.behaviour.sequences:
            for e in s.events:
                for k in e.set:
                    if k not in names:
                        errs.append(f"sequence {s.name}: unknown port {k}")
            for e in s.expect:
                if e.port not in names:
                    errs.append(f"sequence {s.name}: unknown port {e.port}")
                if e.value not in (0, 1, "init", "!init"):
                    errs.append(f"sequence {s.name}: bad expected value {e.value!r}")
                if e.t2 > s.duration or e.t1 > e.t2:
                    errs.append(f"sequence {s.name}: bad window {e.t1}-{e.t2} (duration {s.duration})")
        if not (vecs or self.behaviour.sequences):
            errs.append("no behaviour specified")
        elif not any(v.public for v in vecs) and not any(s.public for s in self.behaviour.sequences):
            errs.append("no public vector or sequence for the model to see")
        bb, bu = self.bbox, self.build_box
        if not (all(bb[i] <= bu[i] for i in range(3)) and all(bu[i] <= bb[i] for i in range(3, 6))):
            errs.append("build_box not inside bbox")
        return errs

    def describe(self, public=True):
        """Model-readable task text: description, bounds, ports and the public examples."""
        lines = [f"TASK {self.id} [{self.tier}] {self.title}", self.description.strip(), ""]
        lines.append(f"World box (inclusive): {self.bbox[:3]} .. {self.bbox[3:]}. "
                     f"You may place blocks only inside {self.build_box[:3]} .. {self.build_box[3:]}.")
        lines.append("Block states: repeater/comparator `facing` is the side the signal comes FROM (it outputs the "
                     "opposite way); wall torch/lever/button `facing` points away from the block they are attached to; "
                     "time is in game ticks (20 = 1 s, repeater delay=1 is 2 ticks).")
        lines.append("Fixed (cannot be changed):")
        for f in self.fixed:
            b = f["box"]
            lines.append(f"  {f['state']}  at {b[:3]}" + ("" if b[:3] == b[3:] else f" .. {b[3:]}"))
        lines.append("Ports:")
        for p in self.ports:
            extra = f" facing={p.facing}" if p.facing else ""
            lines.append(f"  {p.name}: {'input' if p.is_input else 'output'} {p.kind} at {list(p.pos)}{extra}"
                         + (f" - {p.doc}" if p.doc else ""))
        vecs = [v for v in self.vectors() if v.public or not public]
        if vecs:
            lines.append("Example vectors (applied in a random order without reset, so state persists between them):")
            for v in vecs:
                lines.append(f"  set {v.inputs} -> expect {v.expect}")
        for s in self.behaviour.sequences:
            if s.public or not public:
                lines.append(f"Timing sequence '{s.name}' (ticks counted from the at-rest start):")
                for e in s.events:
                    lines.append(f"  t={e.t}: set {e.set}")
                for e in s.expect:
                    lines.append(f"  expect {e.port}={e.value} {'throughout' if e.mode == 'all' else 'at some tick'} t={e.t1}..{e.t2}")
        lines.append("Hidden vectors/sequences of the same kind are also checked.")
        return "\n".join(lines)


def box_cells(box):
    x1, y1, z1, x2, y2, z2 = box
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            for z in range(z1, z2 + 1):
                yield (x, y, z)


def in_box(pos, box):
    return all(box[i] <= pos[i] <= box[i + 3] for i in range(3))


def box_volume(box):
    return (box[3] - box[0] + 1) * (box[4] - box[1] + 1) * (box[5] - box[2] + 1)
