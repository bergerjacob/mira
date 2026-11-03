"""Module layer: modules with declared ports and specs, designs = placed instances + nets routed by simulation/router.py.

    from simulation.modules import Module, Design
    fa = Module.lib("full_adder")                       # lib/full_adder.py + its meta (ports, spec, min input strengths)
    d = Design(box=(0, 0, 0, 60, 8, 30), floor_y=0)     # floor_y: the plane y=0 is a floor that exists (task scenery)
    d.add("fa0", fa, at=(40, 0, 5)); d.add("fa1", fa, at=(20, 0, 5))
    d.input("A0", (45, 1, 25)); d.output("S0", "fa0.S")  # pad input (the harness puts a redstone block there), output port
    d.connect("A0", "fa0.A", "fa1.A")                   # one net, one source, any number of sinks
    d.connect("fa0.COUT", "fa1.CIN")
    res = d.build()                                     # places instances, routes every net -> DesignBuild (blocks, nets, errors)
    rep = d.check(fn=lambda A0, ...: {...})             # per-net isolation checks, per-module isolation tests, in-context fault localisation

Docs: docs/ROUTER.md. Simulation goes through env.sim (Nucleation); nothing here is sandboxed: run trusted design scripts only.
"""
import itertools
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation import builder as BD  # noqa: E402
from simulation import router as R  # noqa: E402

Pos = Tuple[int, int, int]


@dataclass
class PortDef:
    name: str
    pos: Pos              # module-local
    face: Optional[str]   # side the outside connects to
    dir: str              # in | out
    need: int = 1         # min input strength (inputs)
    level: int = 15       # output strength
    entry: str = "any"    # 'face': the router must join this input dust through its face (lib meta, router.entry_rule)


# lib cells whose output strength follows an input strength (measured, docs/ROUTER.md): mux2's comparators pass the A/B
# strength through to Y (A at 9 -> Y at 9). Their data inputs must arrive strong, and Y is only as strong as that.
ANALOG = R.ANALOG
ANALOG_NEED = R.ANALOG_NEED


class Module:
    """A Part with declared ports and an optional spec ({inputs, outputs, rows: [[ins], [outs]]} or fn(**ins) -> outs)."""

    def __init__(self, name: str, part_def, ports: Dict[str, PortDef], spec: Optional[dict] = None, params=None, lib=None):
        self.name, self.part_def, self.ports, self.spec = name, part_def, ports, spec
        self.params = dict(params or {})
        self.lib = lib

    @classmethod
    def lib(cls, name: str, **params) -> "Module":
        from env import library as L
        ns: dict = {}
        exec(compile(L.prelude([name]), f"lib/{name}.py", "exec"), ns)
        meta = L.meta(name)
        needs = meta.get("verified", {}).get("min_in_strength_per_port", {})
        ports = {k: PortDef(k, tuple(v["pos"]), v.get("face"), "in" if v["dir"] == "in" else "out", int(needs.get(k, 1)),
                         entry=v.get("entry", "any"))
                 for k, v in meta["ports"].items()}
        for out, ins in ANALOG.get(name, {}).items():
            ports[out].level = ANALOG_NEED
            for i in ins:
                ports[i].need = max(ports[i].need, ANALOG_NEED)
        spec = meta.get("spec")
        if spec and not spec.get("rows"):
            spec = None
        return cls(name, ns[name], ports, spec, params, lib=name)

    @classmethod
    def from_part(cls, part_def, spec=None, needs=None, name=None, **params) -> "Module":
        """Any @part with b.port(..., kind='in'|'out', face=...) declarations; spec / needs optional."""
        p = BD.Part()
        p.place(part_def, name="m", **params)
        ports = {}
        for k, (pos, kind, face) in p._ports.items():
            pn = k.split(".", 1)[1]
            ports[pn] = PortDef(pn, tuple(pos), face, "out" if kind == "out" else "in", int((needs or {}).get(pn, 1)),
                                entry=R.entry_rule(p.get, tuple(pos), face) if kind != "out" else "any")
        return cls(name or part_def.name, part_def, ports, spec, params)

    @property
    def inputs(self) -> List[str]:
        return list(self.spec["inputs"]) if self.spec else [k for k, p in self.ports.items() if p.dir == "in"]

    @property
    def outputs(self) -> List[str]:
        return list(self.spec["outputs"]) if self.spec else [k for k, p in self.ports.items() if p.dir == "out"]

    def eval(self, values: Dict[str, int]) -> Optional[Dict[str, int]]:
        """Spec outputs for the given input values (None without a spec)."""
        if not self.spec:
            return None
        key = [int(values.get(i, 0)) for i in self.spec["inputs"]]
        for ins, outs in self.spec["rows"]:
            if list(ins) == key:
                return dict(zip(self.spec["outputs"], outs))
        return None


@dataclass
class Instance:
    name: str
    module: Module
    at: Pos
    rot: int = 0
    mirror: Optional[str] = None
    cells: Dict[Pos, str] = field(default_factory=dict)
    ports: Dict[str, Tuple[Pos, Optional[str]]] = field(default_factory=dict)   # world pos, world face


@dataclass
class DesignBuild:
    part: object
    build: object                   # simulation.builder.Build of instances + routes
    router: R.Router
    nets: Dict[str, R.Net]
    errors: List[str]

    @property
    def ok(self) -> bool:
        return not self.errors and all(n.ok for n in self.nets.values())

    def blocks(self) -> List[list]:
        return [[x, y, z, s] for x, y, z, s, _ in self.build.blocks]

    def summary(self) -> str:
        lines = [f"design: {len(self.build.blocks)} blocks, {sum(n.ok for n in self.nets.values())}/{len(self.nets)} nets routed"]
        lines += [f"ERROR {e}" for e in self.errors]
        lines += [n.summary() for n in self.nets.values() if not n.ok]
        return "\n".join(lines)


class Design:
    """Instances of modules at positions (rot/mirror as in Part.place) plus nets between their ports.

    box: inclusive build box; floor_y: a floor plane that exists (the task's, or `fixed`'s) - modules are placed with
    floor=False and the router neither writes nor needs supports at that level; fixed: {pos: state} scenery the design
    must not touch (levers, lamps, floor); keep_clear: boxes never written."""

    def __init__(self, box=None, floor_y: Optional[int] = None, fixed=None, keep_clear=(), reserved=(), **router_kw):
        self.box, self.floor_y = box, floor_y
        self.fixed = {tuple(p): BD.normalize(s) for p, s in (fixed or {}).items()}
        self.keep_clear, self.reserved = list(keep_clear), [tuple(p) for p in reserved]
        self.router_kw = router_kw
        self.instances: Dict[str, Instance] = {}
        self.ins: Dict[str, R.Pin] = {}        # design inputs (pads / levers)
        self.outs: Dict[str, object] = {}      # design outputs: "inst.PORT" or a device Pin
        self.nets: List[Tuple[str, str, List[str]]] = []
        self.spec_fn: Optional[Callable] = None
        self.result: Optional[DesignBuild] = None
        self.contract = None

    @classmethod
    def from_contract(cls, contract, **router_kw) -> "Design":
        """Design over a task: fixed scenery, build box, keep_clear; click ports become lever inputs, lamp / wire_power
        ports become device outputs, pad ports become pad inputs. The floor (fixed plane at the bottom of bbox) is used."""
        from env.runner import fixed_cells
        fixed = fixed_cells(contract)
        ys = [p[1] for p, s in fixed.items() if R.name_of(s) != "air"]
        floor_y = contract.bbox[1] if ys and sum(1 for y in ys if y == contract.bbox[1]) > 0.5 * len(ys) else None
        d = cls(box=contract.build_box, floor_y=floor_y, fixed={p: s for p, s in fixed.items() if R.name_of(s) != "air"},
                keep_clear=list(contract.keep_clear) + list(contract.no_write),
                reserved=[p for p, s in fixed.items() if R.name_of(s) == "air"], **router_kw)
        for p in contract.ports:
            if p.kind == "click":
                d.ins[p.name] = R.Pin(tuple(p.pos), None, "device", name=p.name)
            elif p.kind == "pad":
                d.ins[p.name] = R.Pin(tuple(p.pos), None, "pad", name=p.name)
            elif p.kind in ("lamp", "wire_power"):
                d.outs[p.name] = R.Pin(tuple(p.pos), None, "device" if p.kind == "lamp" else "dust",
                                       need=p.min_power if p.kind == "wire_power" else 1, name=p.name)
        d.contract = contract
        return d

    # -- declaration
    def add(self, name: str, module: Module, at, rot: int = 0, mirror: Optional[str] = None) -> Instance:
        if name in self.instances or "." in name:
            raise ValueError(f"instance name {name!r} is taken or contains '.'")
        inst = Instance(name, module, tuple(at), rot % 360, mirror)
        self.instances[name] = inst
        return inst

    def input(self, name: str, pos, face: Optional[str] = None, kind: str = "pad"):
        """A design input: 'pad' = reserved cell where a redstone block is placed for 1 (default), 'device' = a lever
        already in `fixed`."""
        self.ins[name] = R.Pin(tuple(pos), face, kind, name=name)
        if kind == "pad":
            self.reserved.append(tuple(pos))

    def output(self, name: str, ref):
        """A design output: 'inst.PORT' (read as dust power >= 1) or a device position (lamp) in `fixed`."""
        self.outs[name] = ref if isinstance(ref, str) else R.Pin(tuple(ref), None, "device", name=name)

    def connect(self, src: str, *sinks: str, name: Optional[str] = None) -> str:
        """One net: src = design input or 'inst.PORT' (an output port); sinks = 'inst.PORT' inputs or design outputs."""
        nm = name or f"{src}->{','.join(sinks)}"
        self.nets.append((nm, src, list(sinks)))
        return nm

    def spec(self, fn: Callable):
        """Design truth table: fn(**inputs) -> {output: value} over the design inputs / outputs."""
        self.spec_fn = fn

    # -- build
    def _place(self):
        root = BD.Part(BD._Ctx(validate=True))
        errors = []
        owner: Dict[Pos, str] = {}
        for inst in self.instances.values():
            m = inst.module
            params = dict(m.params)
            if "floor" not in params and self.floor_y is not None:
                params["floor"] = False
            scratch = BD.Part()
            scratch.place(m.part_def, at=inst.at, rot=inst.rot, mirror=inst.mirror, name=inst.name, **params)
            inst.cells = {p: c[0] for p, c in scratch._cells.items()}
            inst.ports = {k.split(".", 1)[1]: (tuple(v[0]), v[2]) for k, v in scratch._ports.items()}
            for p in inst.cells:
                if p in owner:
                    errors.append(f"instances {owner[p]} and {inst.name} overlap at {p}")
                elif p in self.fixed:
                    errors.append(f"instance {inst.name} writes the fixed cell {p}")
                owner.setdefault(p, inst.name)
                if self.box is not None and not R._inside(p, R._box(self.box)):
                    errors.append(f"instance {inst.name} leaves the box at {p}")
                    break
            root.place(m.part_def, at=inst.at, rot=inst.rot, mirror=inst.mirror, name=inst.name, **params)
        return root, owner, sorted(set(errors))[:20]

    def pin(self, ref: str, role: str) -> R.Pin:
        if ref in self.ins:
            if role != "source":
                raise ValueError(f"design input {ref!r} can only drive a net")
            return self.ins[ref]
        if ref in self.outs and not isinstance(self.outs[ref], str):
            return self.outs[ref]
        if "." not in ref:
            raise ValueError(f"unknown pin {ref!r}: use a design input/output name or 'inst.PORT'")
        iname, pname = ref.split(".", 1)
        if iname not in self.instances:
            raise ValueError(f"unknown instance {iname!r} in {ref!r}")
        inst = self.instances[iname]
        pd = inst.module.ports.get(pname)
        if pd is None or pname not in inst.ports:
            raise ValueError(f"{inst.module.name} has no port {pname!r}; ports: {', '.join(sorted(inst.module.ports))}")
        if role == "source" and pd.dir != "out":
            raise ValueError(f"{ref} is an input port; a net is driven by an output port or a design input")
        if role == "sink" and pd.dir != "in":
            raise ValueError(f"{ref} is an output port; sinks are input ports or design outputs")
        pos, face = inst.ports[pname]
        return R.Pin(pos, face, "dust", need=pd.need, level=pd.level, name=ref, entry=pd.entry if pd.dir == "in" else "any",
                     why=inst.module.name + "." + pname)

    def build(self, order: str = "declared", passes: int = 3, repair: bool = True) -> DesignBuild:
        """Place the instances and route every net. order: 'declared' or 'short' (shortest nets first). A net that fails
        is moved to the front and everything is re-routed, up to `passes` times (simple rip-up and retry). repair=True
        then runs the env's paste_stable check on the whole design and re-routes nets next to cells that do not come
        back unchanged (Router.repair_paste; errors list what is left)."""
        root, owner, errors = self._place()
        world = dict(self.fixed)
        world.update({p: c[0] for p, c in root._cells.items()})
        specs = []
        for nm, src, sinks in self.nets:
            try:
                specs.append((nm, self.pin(src, "source"), [self.pin(s, "sink") for s in sinks]))
            except ValueError as e:
                errors.append(f"net {nm}: {e}")
        if order == "short":
            specs.sort(key=lambda t: sum(R._dist(t[1].pos, k.pos) for k in t[2]))
        router, best, weight = None, None, {}
        for _ in range(max(1, passes)):
            router = R.Router(world, box=self.box, keep_clear=self.keep_clear, reserved=self.reserved,
                              floor_y=self.floor_y, **self.router_kw)
            router.owner.update(owner)
            for nm, sp, kp in specs:
                router.reserve_pins(nm, [sp] + kp, weight.get(nm, 1.0))
            for nm, sp, kp in specs:
                router.route(nm, sp, kp)
            failed = [t for t in specs if not router.nets[t[0]].ok]
            if best is None or len(failed) < best[1]:
                best = (router, len(failed))
            if not failed:
                break
            for t in failed:
                weight[t[0]] = weight.get(t[0], 1.0) * 3
            specs = failed + [t for t in specs if router.nets[t[0]].ok]
        router = best[0]
        if repair and not errors:
            ok, det = router.repair_paste()
            if not ok:
                errors.append("paste_stable (env R1) fails after repair: " + "; ".join(det))
        for net in router.nets.values():
            for p, s in net.cells.items():
                root.set(p, s)
        build = BD._finish(root._ctx, root)
        errors += [str(e) for e in build.errors]
        self._owner = dict(owner)
        for net in router.nets.values():
            for p in net.cells:
                self._owner[p] = "net:" + net.name
        self.result = DesignBuild(root, build, router, dict(router.nets), errors)
        return self.result

    # -- simulation helpers
    def world(self) -> Dict[Pos, str]:
        res = self.result or self.build()
        w = dict(self.fixed)
        w.update({(x, y, z): s for x, y, z, s, _ in res.build.blocks})
        if self.floor_y is not None and not any(p[1] == self.floor_y for p in self.fixed):
            xs = [p[0] for p in w] + [p.pos[0] for p in self.ins.values()]
            zs = [p[2] for p in w] + [p.pos[2] for p in self.ins.values()]
            for x in range(min(xs) - 1, max(xs) + 2):
                for z in range(min(zs) - 1, max(zs) + 2):
                    w.setdefault((x, self.floor_y, z), R.FLOOR)
        return w

    def design_contract(self, fn: Optional[Callable] = None, settle: int = 300, name: str = "design",
                        max_rows: Optional[int] = None, seed: int = 0):
        """An env Contract for the design: pad inputs, wire_power outputs (the output ports), the truth table from fn
        (exhaustive over the inputs, or `max_rows` seeded random rows; hidden reversed repeat), the floor as fixed
        scenery. -> (contract, blocks)."""
        from env.contract import Behaviour, Budgets, Contract, DEFAULT_FORBIDDEN, Invariants, Port, TruthTable, Vector
        fn = fn or self.spec_fn
        res = self.result or self.build()
        w = self.world()
        floor = {p: s for p, s in w.items() if self.floor_y is not None and p[1] == self.floor_y and p not in
                 {(x, y, z) for x, y, z, _, _ in res.build.blocks}}
        names_in = list(self.ins)
        ports = [Port(n, "pad", p.pos, doc="design input") for n, p in self.ins.items() if p.kind == "pad"]
        for n, ref in self.outs.items():
            pos = self.pin(ref, "out").pos if isinstance(ref, str) else ref.pos
            ports.append(Port(n, "wire_power" if isinstance(ref, str) else "lamp", pos, min_power=1, doc="design output"))
        vecs = []
        for combo in sample_rows(len(names_in), max_rows, seed):
            ins = dict(zip(names_in, combo))
            vecs.append(Vector(ins, {k: int(v) for k, v in fn(**ins).items()}, True, "".join(map(str, combo))))
        vecs += [Vector(v.inputs, v.expect, False, v.label + "'") for v in reversed(vecs)]
        allp = list(w) + [p.pos for p in self.ins.values()]
        bbox = [min(p[0] for p in allp) - 1, min(p[1] for p in allp), min(p[2] for p in allp) - 1,
                max(p[0] for p in allp) + 1, max(p[1] for p in allp) + 1, max(p[2] for p in allp) + 1]
        fixed = [{"box": [*p, *p], "state": s} for p, s in sorted(floor.items())]
        fixed += [{"box": [*p.pos, *p.pos], "state": "minecraft:air"} for p in self.ins.values() if p.kind == "pad"]
        c = Contract(id=name, title=name, tier="DESIGN", description="module design", bbox=bbox, build_box=list(bbox),
                     ports=ports, fixed=fixed, behaviour=Behaviour(truth_table=TruthTable(vecs, settle=settle, shuffle=True,
                                                                                         reset_between=False)),
                     invariants=Invariants(), budgets=Budgets(rest=400, total=2_000_000, max_blocks=100_000),
                     eval_seed=11, forbidden=DEFAULT_FORBIDDEN)
        errs = c.validate()
        if errs:
            raise ValueError(f"design contract invalid: {errs[:3]}")
        return c, res.blocks()

    def pin_out(self, ref) -> R.Pin:
        return self.pin(ref, "out")

    # -- check
    def check(self, fn: Optional[Callable] = None, vectors: Optional[List[dict]] = None, nets: bool = True,
              modules: bool = True, paste: bool = True, settle: int = 400) -> "CheckReport":
        """Diagnostics: unrouted nets (with reasons); per-net isolation checks (power the source alone: every sink gets
        enough strength, nothing outside the net changes); per-module isolation tests of every instance against its
        module spec; with fn (or d.spec) the design truth table in context, localising each failing vector to the first
        module whose observed outputs contradict its spec on its observed inputs, or the net whose sinks disagree with
        its source."""
        res = self.result or self.build()
        rep = CheckReport()
        rep.errors = list(res.errors)
        for n in res.nets.values():
            if not n.ok:
                rep.unrouted[n.name] = n.reason
        if nets:
            for nm, c in R.check_nets(res.router).items():
                rep.nets[nm] = c
        if modules:
            built = {(x, y, z): s for x, y, z, s, _ in res.build.blocks}
            for inst in self.instances.values():
                if inst.module.spec:
                    rep.modules[inst.name] = module_test(inst, self.floor_y, settle, built)
        if paste:
            rep.paste = R.paste_stable(self.world(), settle)
        fn = fn or self.spec_fn
        if fn is not None:
            rep.vectors = self._in_context(fn, vectors, settle)
        return rep

    def _in_context(self, fn, vectors, settle):
        from env.sim import Sim, compile_cells
        res = self.result
        w = self.world()
        for p in self.ins.values():
            if p.kind == "pad":
                w.pop(p.pos, None)
        sc, corner, errs = compile_cells(w)
        sim = Sim(sc, corner, "inworld")
        sim.settle(settle)
        names_in = list(self.ins)
        vecs = vectors or [dict(zip(names_in, c)) for c in itertools.product((0, 1), repeat=len(names_in))]
        out = []
        cur = {n: 0 for n in names_in}
        for vec in vecs:
            for n, v in vec.items():
                if v == cur.get(n):
                    continue
                pin = self.ins[n]
                if pin.kind == "pad":
                    sim.place(pin.pos, "minecraft:redstone_block" if v else "minecraft:air")
                else:
                    sim.use(pin.pos)
                cur[n] = v
            _, settled = sim.settle(settle)
            exp = {k: int(v) for k, v in fn(**{n: cur[n] for n in names_in}).items()}
            got = {}
            for k in exp:
                ref = self.outs[k]
                pin = self.pin(ref, "out") if isinstance(ref, str) else ref
                got[k] = int(R._read(sim.get(pin.pos), pin) > 0)
            row = {"inputs": dict(cur), "expect": exp, "got": got, "ok": got == exp and settled, "fault": None}
            if not row["ok"]:
                row["fault"] = self._localise(sim, cur)
            out.append(row)
        return out

    def _localise(self, sim, values):
        """First module (in signal order) whose outputs contradict its spec, or net whose sinks disagree with its source."""
        def port_val(inst, pn):
            pos, _ = inst.ports[pn]
            return int(R._read(sim.get(pos), R.Pin(pos)) > 0)
        findings = []
        for nm, src, sinks in self.nets:
            net = self.result.nets.get(nm)
            if net is None or not net.ok:
                findings.append(f"net {nm} is unrouted")
                continue
            if src in self.ins:
                sv = values[src]
            else:
                i, p = src.split(".", 1)
                sv = port_val(self.instances[i], p)
            for s in sinks:
                if s in self.outs and not isinstance(self.outs[s], str):
                    continue
                i, p = s.split(".", 1)
                v = port_val(self.instances[i], p)
                if v != sv:
                    findings.append(f"net {nm}: source {src}={sv} but sink {s}={v}")
        for inst in self.instances.values():
            m = inst.module
            if not m.spec:
                continue
            ins = {p: port_val(inst, p) for p in m.inputs}
            exp = m.eval(ins)
            got = {p: port_val(inst, p) for p in m.outputs}
            if exp is not None and exp != got:
                findings.append(f"module {inst.name} ({m.name}): inputs {ins} -> outputs {got}, spec says {exp}")
        return findings or ["no module or net contradicts its spec at this vector (timing? output read too early?)"]


def sample_rows(n_inputs: int, max_rows: Optional[int] = None, seed: int = 0) -> List[Tuple[int, ...]]:
    """All input combinations, or `max_rows` distinct seeded random ones (always including all-0 and all-1)."""
    if max_rows is None or 2 ** n_inputs <= max_rows:
        return list(itertools.product((0, 1), repeat=n_inputs))
    import random
    rnd = random.Random(seed)
    picked = {0, 2 ** n_inputs - 1}
    while len(picked) < max_rows:
        picked.add(rnd.randrange(2 ** n_inputs))
    return [tuple((k >> (n_inputs - 1 - j)) & 1 for j in range(n_inputs)) for k in sorted(picked, key=lambda _: rnd.random())]


@dataclass
class CheckReport:
    errors: List[str] = field(default_factory=list)
    unrouted: Dict[str, str] = field(default_factory=dict)
    nets: Dict[str, R.NetCheck] = field(default_factory=dict)
    modules: Dict[str, dict] = field(default_factory=dict)
    vectors: List[dict] = field(default_factory=list)
    paste: Optional[Tuple[bool, List[str]]] = None    # env paste_stable invariant on the whole design (ok, details)

    @property
    def ok(self) -> bool:
        return ((self.paste is None or self.paste[0]) and not self.errors and not self.unrouted and all(c.ok for c in self.nets.values())
                and all(m["ok"] for m in self.modules.values()) and all(v["ok"] for v in self.vectors))

    def summary(self, max_lines: int = 30) -> str:
        lines = [f"check: {'OK' if self.ok else 'FAIL'}; nets {sum(c.ok for c in self.nets.values())}/{len(self.nets)} isolated-ok, "
                 f"{len(self.unrouted)} unrouted; modules {sum(m['ok'] for m in self.modules.values())}/{len(self.modules)} pass alone; "
                 f"vectors {sum(v['ok'] for v in self.vectors)}/{len(self.vectors)}; paste_stable "
                 f"{'n/a' if self.paste is None else 'ok' if self.paste[0] else 'FAIL ' + '; '.join(self.paste[1])}"]
        lines += [f"ERROR {e}" for e in self.errors]
        lines += [f"UNROUTED {n}: {r}" for n, r in self.unrouted.items()]
        lines += [c.summary() for c in self.nets.values() if not c.ok]
        lines += [f"MODULE {n} fails alone (rows [inputs, expect, got]): {m['fails'][:3]}; cells differing from the module: "
                  f"{m.get('changed_cells', [])[:3]}" for n, m in self.modules.items() if not m["ok"]]
        for v in self.vectors:
            if not v["ok"]:
                lines.append(f"VECTOR {v['inputs']}: expect {v['expect']} got {v['got']}; fault: {v['fault'][:3]}")
        return "\n".join(lines[:max_lines])


def module_test(inst: Instance, floor_y: Optional[int] = None, settle: int = 400, built=None) -> dict:
    """Run one placed instance alone (its cells as they are in `built` - the design's blocks - or as placed, a floor if
    the design has one, a redstone block pad directly outside each input port) through its module's whole truth table,
    without reset between rows. -> {ok, fails, missing}"""
    from env.sim import Sim, compile_cells
    m = inst.module
    w = dict(inst.cells) if built is None else {p: built[p] for p in inst.cells if p in built}
    missing = [p for p in inst.cells if p not in w or w[p] != inst.cells[p]]
    if floor_y is not None:
        xs, zs = [p[0] for p in w], [p[2] for p in w]
        for x in range(min(xs) - 2, max(xs) + 3):
            for z in range(min(zs) - 2, max(zs) + 3):
                w.setdefault((x, floor_y, z), R.FLOOR)
    pads = {}
    for pn in m.inputs:
        pos, face = inst.ports[pn]
        pads[pn] = R.add(pos, R.DIRS[face])
        if pads[pn] in w:
            return {"ok": False, "fails": [f"pad cell for {pn} at {pads[pn]} is occupied"]}
    sc, corner, errs = compile_cells(w)
    sim = Sim(sc, corner, "inworld")
    sim.settle(settle)
    fails, cur = [], {pn: 0 for pn in m.inputs}
    rows = list(m.spec["rows"])
    for ins, outs in rows + rows[::-1]:
        for pn, v in zip(m.spec["inputs"], ins):
            if cur[pn] != v:
                sim.place(pads[pn], "minecraft:redstone_block" if v else "minecraft:air")
                cur[pn] = v
        sim.settle(settle)
        got = [int(R._read(sim.get(inst.ports[o][0]), R.Pin(inst.ports[o][0])) > 0) for o in m.spec["outputs"]]
        if got != list(outs):
            fails.append((list(ins), list(outs), got))
    return {"ok": not fails, "fails": fails, "changed_cells": missing[:10]}


# --------------------------------------------------------------------------- benchmarks (docs/ROUTER.md)
def t4_design(task_id: str) -> Design:
    """The T4 adder16 / addsub8 interconnect rebuilt from lib cells + the router (cells at the reference positions, every
    wire routed; the hand-written wire_path waypoints of env/tasks/author_t4.py are not used)."""
    from env.task import load_task
    d = Design.from_contract(load_task(task_id).contract)
    fa = Module.lib("full_adder")
    if task_id == "t4_adder16":
        n = 16
        for i in range(n):
            d.add(f"fa{i}", fa, at=(256 - 16 * i, 0, 2))
        d.connect("CIN", "fa0.CIN")
        for i in range(n):
            d.connect(f"A{i}", f"fa{i}.A")
            d.connect(f"B{i}", f"fa{i}.B")
    elif task_id == "t4_addsub8":
        n = 8
        xr = Module.lib("xor2")
        for i in range(n):
            d.add(f"fa{i}", fa, at=(128 - 16 * i, 0, 2))
            d.add(f"xr{i}", xr, at=(130 - 16 * i, 0, 10))
        d.connect("M", *[f"xr{i}.B" for i in range(n)], "fa0.CIN", name="M")
        for i in range(n):
            d.connect(f"A{i}", f"fa{i}.A")
            d.connect(f"B{i}", f"xr{i}.A")
            d.connect(f"xr{i}.Y", f"fa{i}.B")
    else:
        raise ValueError(task_id)
    for i in range(n):
        d.connect(f"fa{i}.S", f"S{i}")
        if i < n - 1:
            d.connect(f"fa{i}.COUT", f"fa{i + 1}.CIN")
    d.connect(f"fa{n - 1}.COUT", "COUT")
    return d


def alu4_design():
    """4-bit ALU-ish: OP1 OP0 select ADD (00) / AND (01) / OR (10) / XOR (11) of A and B per bit; Y0..Y3, COUT = adder carry.
    Built ONLY with Design + router: 4 full_adder, 4 and2, 4 or2, 4 xor2, 12 mux2 (two levels per bit)."""
    mods = {k: Module.lib(k) for k in ("full_adder", "and2", "or2", "xor2", "mux2")}
    d = Design(box=(-4, 0, -4, 175, 9, 60), floor_y=0)
    for i in range(4):
        x0 = 140 - 40 * i
        d.add(f"fa{i}", mods["full_adder"], at=(x0, 0, 4))
        d.add(f"and{i}", mods["and2"], at=(x0 - 10, 0, 18))
        d.add(f"or{i}", mods["or2"], at=(x0 + 6, 0, 18))
        d.add(f"xor{i}", mods["xor2"], at=(x0 + 16, 0, 18))
        d.add(f"m0_{i}", mods["mux2"], at=(x0 - 6, 0, 30))      # ADD / AND by OP0
        d.add(f"m1_{i}", mods["mux2"], at=(x0 + 10, 0, 30))     # OR / XOR by OP0
        d.add(f"m2_{i}", mods["mux2"], at=(x0 + 2, 0, 40))      # by OP1
        d.input(f"A{i}", (x0 - 4, 1, 56))
        d.input(f"B{i}", (x0 + 8, 1, 56))
        d.output(f"Y{i}", f"m2_{i}.Y")
    d.input("CIN0", (165, 1, 2))
    d.input("OP0", (170, 1, 34))
    d.input("OP1", (170, 1, 46))
    d.output("COUT", "fa3.COUT")
    for i in range(4):
        d.connect(f"A{i}", f"fa{i}.A", f"and{i}.A", f"or{i}.A", f"xor{i}.A", name=f"A{i}")
        d.connect(f"B{i}", f"fa{i}.B", f"and{i}.B", f"or{i}.B", f"xor{i}.B", name=f"B{i}")
        d.connect(f"fa{i}.S", f"m0_{i}.A")
        d.connect(f"and{i}.Y", f"m0_{i}.B")
        d.connect(f"or{i}.Y", f"m1_{i}.A")
        d.connect(f"xor{i}.Y", f"m1_{i}.B")
        d.connect(f"m0_{i}.Y", f"m2_{i}.A")
        d.connect(f"m1_{i}.Y", f"m2_{i}.B")
        if i < 3:
            d.connect(f"fa{i}.COUT", f"fa{i + 1}.CIN")
    d.connect("CIN0", "fa0.CIN")
    d.connect("OP0", *[f"m{k}_{i}.S" for i in range(4) for k in (0, 1)], name="OP0")
    d.connect("OP1", *[f"m2_{i}.S" for i in range(4)], name="OP1")

    def fn(**v):
        a = sum(v[f"A{i}"] << i for i in range(4))
        b = sum(v[f"B{i}"] << i for i in range(4))
        op = v["OP0"] | (v["OP1"] << 1)
        s = a + b + v["CIN0"]
        y = [s, a & b, a | b, a ^ b][op] & 15
        out = {f"Y{i}": (y >> i) & 1 for i in range(4)}
        out["COUT"] = s >> 4
        return out
    d.spec(fn)
    return d


def bench_random(n: int = 50, seed: int = 0, size=(32, 8, 32), obstacles: float = 0.08, passes: int = 1, max_states: int = 60_000,
                 gap: int = 2, repair: bool = True):
    """(a) n random 2-pin nets in a box with random obstacles, routed one after another in one session; every routed net is
    then checked in the simulator in isolation. -> dict of numbers."""
    import random
    import time
    rnd = random.Random(seed)
    sx, sy, sz = size
    world = {(x, 0, z): R.FLOOR for x in range(sx) for z in range(sz)}
    for x, y, z in itertools.product(range(sx), range(1, sy - 2), range(sz)):
        if rnd.random() < obstacles / (1 + y):
            world[(x, y, z)] = "minecraft:stone"
    rt = R.Router(world, box=(0, 1, 0, sx - 1, sy - 1, sz - 1), floor_y=0)
    pins, times = [], []
    lever = BD.normalize("minecraft:lever[face=floor,facing=north]")
    lamp = BD.normalize("minecraft:redstone_lamp")
    used = set()

    def free_spot():
        for _ in range(100_000):
            p = (rnd.randrange(1, sx - 1), 1, rnd.randrange(1, sz - 1))
            if p not in world and all(R.add(p, d) not in world for d in R.ALL6[:4]) and \
                    all(max(abs(p[0] - q[0]), abs(p[2] - q[2])) >= gap for q in used):
                used.add(p)
                return p
        raise RuntimeError("no room for another pin")
    for i in range(n):
        a, z = free_spot(), free_spot()
        world[a], world[z] = lever, lamp
        rt.world[a], rt.world[z] = lever, lamp
        rt._note_hot(a, lever)
        pins.append((a, z))
    order = list(range(n))
    base = dict(rt.world), set(rt.hot)
    alone = 0                          # upper bound: nets routable in the empty world (devices + obstacles only)
    for a, z in pins:
        r1 = R.Router(dict(base[0]), box=(0, 1, 0, sx - 1, sy - 1, sz - 1), floor_y=0, max_states=max_states)
        alone += r1.route("x", R.Pin(a, None, "device"), [R.Pin(z, None, "device")]).ok
    passes_used, weight, best = 0, {i: 1.0 for i in range(n)}, None
    for _ in range(passes):           # rip-up and retry: failed nets first, their pin zones weigh more (negotiated congestion)
        passes_used += 1
        rt = R.Router(dict(base[0]), box=(0, 1, 0, sx - 1, sy - 1, sz - 1), floor_y=0, max_states=max_states)
        for i in order:
            rt.reserve_pins(f"n{i}", [R.Pin(pins[i][0]), R.Pin(pins[i][1])], weight[i], 2 if weight[i] == 1 else 3)
        times = []
        for i in order:
            a, z = pins[i]
            t = time.time()
            rt.route(f"n{i}", R.Pin(a, None, "device"), [R.Pin(z, None, "device")])
            times.append(time.time() - t)
        failed = [i for i in order if not rt.nets[f"n{i}"].ok]
        if best is None or len(failed) < best[1]:
            best = (rt, len(failed), list(times))
        if not failed:
            break
        for i in failed:
            weight[i] *= 3
        order = failed + [i for i in order if i not in failed]
    rt, _, times = best                 # keep the pass that routed the most nets
    routed_first = sum(nt.ok for nt in rt.nets.values())
    paste_first = R.paste_stable(rt.world_with_floor())[0]
    t = time.time()
    paste_ok, paste_detail = rt.repair_paste() if repair else (paste_first, [])
    trep = time.time() - t
    t = time.time()
    floor = {p: s for p, s in rt.world_with_floor().items() if p[1] == 0}
    checks = R.check_nets(rt, extra_world=floor, isolated=False)
    tchk = time.time() - t
    ok = [nm for nm, nt in rt.nets.items() if nt.ok]
    return {"nets": n, "routable_alone": alone, "routed_before_repair": routed_first, "routed": len(ok), "passes_used": passes_used, "checked_ok": sum(checks[nm].ok for nm in ok),
            "check_fail": [checks[nm].summary() for nm in ok if not checks[nm].ok][:5],
            "unrouted": [rt.nets[nm].reason for nm in rt.nets if not rt.nets[nm].ok][:5],
            "route_s_total": round(sum(times), 2), "route_s_max": round(max(times), 3), "check_s": round(tchk, 1),
            "blocks": len(rt.blocks()), "repeaters": sum(len(nt.repeaters) for nt in rt.nets.values()),
            "manhattan": sum(R._dist(a, z) for a, z in pins), "route_cells": sum(len(nt.parent) for nt in rt.nets.values() if nt.ok),
            "paste_stable_first": paste_first, "paste_stable": paste_ok, "paste_detail": paste_detail[:2],
            "repair_s": round(trep, 1)}


def main(argv=None):
    import argparse
    import json
    import time
    ap = argparse.ArgumentParser(description="router / module-layer benchmarks (docs/ROUTER.md)")
    ap.add_argument("what", choices=["random", "t4_adder16", "t4_addsub8", "alu4"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--obstacles", type=float, default=0.08)
    ap.add_argument("--rows", type=int, default=128, help="sampled truth-table rows for designs with many inputs")
    ap.add_argument("--passes", type=int, default=1, help="rip-up-and-retry passes (random)")
    ap.add_argument("--gap", type=int, default=2, help="min Chebyshev distance between random pins")
    a = ap.parse_args(argv)
    if a.what == "random":
        print(json.dumps(bench_random(a.n, a.seed, obstacles=a.obstacles, passes=a.passes, gap=a.gap), indent=1))
        return 0
    from env.runner import run
    from env.score import score
    t = time.time()
    d = t4_design(a.what) if a.what.startswith("t4") else alu4_design()
    res = d.build()
    tb = time.time() - t
    print(res.summary())
    print(f"build+route {tb:.1f}s, {len(res.build.blocks)} blocks, {sum(len(n.repeaters) for n in res.nets.values())} repeaters")
    t = time.time()
    names_in = list(d.ins)
    vec = [dict(zip(names_in, c)) for c in sample_rows(len(names_in), a.rows, a.seed)]
    rep = d.check(vectors=vec, nets=True, modules=a.what == "alu4")
    print(rep.summary())
    print(f"net checks {time.time() - t:.1f}s")
    if d.contract is not None:
        c, blocks = d.contract, res.blocks()
    else:
        c, blocks = d.design_contract(max_rows=a.rows, seed=a.seed)
    t = time.time()
    r = run(c, blocks)
    sc = score(c, r)
    fails = [v for v in r.vectors if not v.ok]
    print(f"env: full_pass={sc.full_pass} reward={sc.reward:.3f} r3={sc.r3:.3f} vectors {len(r.vectors) - len(fails)}/{len(r.vectors)} "
          f"invariant failures {[k for k, v in r.invariants.items() if not v['ok']]} ({time.time() - t:.0f}s); "
          f"reference blocks {getattr(c, 'reference', {}) and c.reference}")
    for v in fails[:3]:
        print("  FAIL", v.inputs, v.expect, v.got)
    return 0 if sc.full_pass else 1


if __name__ == "__main__":
    sys.exit(main())
