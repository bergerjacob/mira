"""Random combinational netlists over lib/ cells -> placed + routed builder program -> contract v2 task (LLM-free).

    from training.synth.netlist import random_netlist, make_task
    nl = random_netlist(random.Random(3), k=3, n_gates=3, m=2)
    task, program, info = make_task(nl, "sn_demo")          # env Task (contract + solution) and the reference program text

Layout (x east, z south; y=0 is the task's fixed floor, logic at y=1):
  * every signal s (inputs first, then gate outputs in topological order) owns a BUS along +x at z = ZB + PITCH*s, y=1;
    input buses start at a fixed lever at x=0, a gate's bus starts under its output column; output buses end in a fixed lamp;
  * gates sit in one row north of the buses (cell origin z=GZ), left to right in topological order;
  * a TAP leaves bus s northwards through a repeater, climbs to y=3, crosses buses s-1..0 on stone supports at y=2 (the stone
    over the crossed dust also blocks the diagonal dust connection), drops back to y=1 north of bus 0 and enters the gate port
    from the west; a gate OUTPUT runs east one cell, south, climbs over buses 0..s-1 the same way and drops onto its own bus.
  Every column has its own x (spacing 2), so nets never touch; wire_path(check_spacing=True) rejects any short.
"""
import itertools
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

ZB, PITCH, GZ, CS = 9, 4, 1, 3   # bus 0 row, bus pitch, gate-row origin z, column spacing (>= 3: see the docstring)

# cell kind -> library parts, logic function, input port offsets (relative to the cell origin, rel. y=1 plane), output offset, width
GATES = {
    "not":  {"uses": ["not_gate"], "fn": lambda a: 1 - a, "ins": {"A": (0, 0)}, "out": (3, 0), "arity": 1},
    "and":  {"uses": ["and2"], "fn": lambda a, b: a & b, "ins": {"A": (0, 0), "B": (0, 2)}, "out": (7, 1), "arity": 2},
    "or":   {"uses": ["or2"], "fn": lambda a, b: a | b, "ins": {"A": (0, 0), "B": (0, 2)}, "out": (4, 1), "arity": 2},
    "nor":  {"uses": ["nor2"], "fn": lambda a, b: 1 - (a | b), "ins": {"A": (0, 0), "B": (0, 2)}, "out": (6, 1), "arity": 2},
    "nand": {"uses": ["and2", "not_gate"], "fn": lambda a, b: 1 - (a & b), "ins": {"A": (0, 0), "B": (0, 2)}, "out": (11, 1), "arity": 2},
}
DEFAULT_WEIGHTS = {"not": 1, "and": 3, "or": 3, "nor": 2, "nand": 2}


@dataclass
class Gate:
    kind: str
    ins: list                    # signal names
    name: str


@dataclass
class Netlist:
    inputs: list                 # ["I0", ...]
    gates: list                  # [Gate] in topological order
    outputs: dict                # output port name -> signal name
    meta: dict = field(default_factory=dict)

    def signals(self):
        return list(self.inputs) + [g.name for g in self.gates]

    def eval(self, values):
        v = dict(values)
        for g in self.gates:
            v[g.name] = GATES[g.kind]["fn"](*(v[s] for s in g.ins))
        return {o: v[s] for o, s in self.outputs.items()}

    def table(self):
        """[(input tuple, {output: value})] over all 2^k inputs."""
        return [(c, self.eval(dict(zip(self.inputs, c)))) for c in itertools.product((0, 1), repeat=len(self.inputs))]

    def depends(self):
        """output -> inputs that functionally matter (exhaustive)."""
        tab = dict(self.table())
        dep = {o: set() for o in self.outputs}
        for c, out in tab.items():
            for i, name in enumerate(self.inputs):
                flip = tuple(1 - x if j == i else x for j, x in enumerate(c))
                for o in self.outputs:
                    if tab[flip][o] != out[o]:
                        dep[o].add(name)
        return {o: sorted(d, key=self.inputs.index) for o, d in dep.items()}

    def depth(self):
        d = {s: 0 for s in self.inputs}
        for g in self.gates:
            d[g.name] = 1 + max(d[s] for s in g.ins)
        return max(d[s] for s in self.outputs.values())

    def describe(self):
        return [f"{g.name} = {g.kind}({', '.join(g.ins)})" for g in self.gates] + [f"{o} = {s}" for o, s in self.outputs.items()]

    def to_dict(self):
        return {"inputs": self.inputs, "gates": [[g.name, g.kind, g.ins] for g in self.gates], "outputs": self.outputs}

    @classmethod
    def from_dict(cls, d):
        return cls(list(d["inputs"]), [Gate(k, list(i), n) for n, k, i in d["gates"]], dict(d["outputs"]))

    def expression(self, sig):
        g = {x.name: x for x in self.gates}.get(sig)
        if g is None:
            return sig
        args = [self.expression(s) for s in g.ins]
        return {"not": lambda a: f"NOT {a}"}.get(g.kind, lambda *a: "(" + f" {g.kind.upper()} ".join(a) + ")")(*args)


def random_netlist(rng, k, n_gates, m, weights=None, tries=200):
    """A random DAG with k inputs, n_gates gates and m outputs such that every input and every gate output is used,
    outputs are distinct non-constant functions and every input influences some output. None if no such net was found."""
    weights = weights or DEFAULT_WEIGHTS
    kinds, w = zip(*weights.items())
    m = min(m, n_gates)
    for _ in range(tries):
        ins = [f"I{i}" for i in range(k)]
        sigs, gates, unused = list(ins), [], list(ins)
        for gi in range(n_gates):
            kind = rng.choices(kinds, w)[0]
            if GATES[kind]["arity"] > len(sigs):
                kind = "not"
            ar = GATES[kind]["arity"]
            pool = [s for s in unused if s in sigs]           # prefer unused signals so nothing dangles
            rng.shuffle(pool)
            picked = pool[:ar]
            rest = [s for s in sigs if s not in picked]
            picked += rng.sample(rest, ar - len(picked))
            name = f"n{gi}"
            gates.append(Gate(kind, picked, name))
            unused = [s for s in unused if s not in picked] + [name]
            sigs.append(name)
        sinks = [s for s in unused if not s.startswith("I")]
        if any(s.startswith("I") for s in unused) or len(sinks) > m:
            continue
        extra = [g.name for g in gates if g.name not in sinks]
        rng.shuffle(extra)
        outs = sorted(sinks + extra[:m - len(sinks)], key=lambda s: int(s[1:]))
        nl = Netlist(ins, gates, {f"Y{j}": s for j, s in enumerate(outs)})
        tab = nl.table()
        cols = [tuple(o[y] for _, o in tab) for y in nl.outputs]
        if any(len(set(c)) < 2 for c in cols) or len(set(cols)) < len(cols):
            continue
        if set(itertools.chain(*nl.depends().values())) != set(ins):
            continue
        return nl
    return None


# --------------------------------------------------------------------------- layout -> builder program
PROGRAM_HELPERS = f'''
ZB, PITCH = {ZB}, {PITCH}     # bus of signal s runs east at z = ZB + PITCH * s, y = 1


def bus_z(s):
    return ZB + PITCH * s


def route(b, a, z, every=15, zfirst=False, taps=None):
    return wire_path(b, a, z, repeat_every=every, floor_exists=True, check_spacing=True, zfirst=zfirst, taps=taps)


def bus(b, s, x0, x1, taps, gate_driven):
    """Bus of signal s from x0 to x1 (a lever at x0 for inputs, a lamp at x1 for outputs); taps = x of the branch columns."""
    z = bus_z(s)
    if gate_driven:
        b.set((x0 + 1, 1, z), repeater(flow=EAST))    # the gate output arrives weak: restore it first
    route(b, (x0, 1, z), (x1, 1, z), 8, taps=[(t, 1, z) for t in taps])


def tap(b, s, x, port):
    """Signal s -> gate input `port`: north out of bus s through a repeater, over buses s-1..0 at y=3, down, into the port."""
    z = bus_z(s)
    b.set((x, 1, z - 1), repeater(flow=NORTH))
    if s > 0:
        route(b, (x, 1, z - 1), (x, 3, z - PITCH))
        if z - PITCH > ZB:
            route(b, (x, 3, z - PITCH), (x, 3, ZB), 6)
        route(b, (x, 3, ZB), (x, 1, ZB - 2))
        b.set((x, 1, ZB - 3), repeater(flow=NORTH))
        route(b, (x, 1, ZB - 3), port, zfirst=True)
    else:
        route(b, (x, 1, z - 1), port, zfirst=True)


def out(b, y, x, s):
    """Gate output dust y -> east to column x, south, over buses 0..s-1 at y=3, down onto bus s (which starts at x)."""
    z = bus_z(s)
    route(b, y, (x, 1, ZB - 5))
    b.set((x, 1, ZB - 4), repeater(flow=SOUTH))
    route(b, (x, 1, ZB - 4), (x, 3, ZB))
    if z - PITCH > ZB:
        route(b, (x, 3, ZB), (x, 3, z - PITCH), 6)
    route(b, (x, 3, z - PITCH), (x, 1, z - 2))
    route(b, (x, 1, z - 2), (x, 1, z))
'''


def layout(nl):
    """-> dict(body, uses, levers {name: pos}, lamps {name: pos}, size). Pure geometry; raises nothing (routing errors show at compile)."""
    sigs = nl.signals()
    sidx = {s: i for i, s in enumerate(sigs)}
    uses, places, gx = [], [], 2 + 2 * CS
    cols = {}                                   # signal -> list of tap column x
    outs_x = {}                                 # gate signal -> (output column x, output port position)
    taps = []                                   # (signal, column x, port expr)
    for gi, g in enumerate(nl.gates):
        spec = GATES[g.kind]
        for u in spec["uses"]:
            if u not in uses:
                uses.append(u)
        names = list(spec["ins"])
        tap_x = [gx - 2 * CS, gx - CS] if len(names) == 2 else [gx - CS]
        var = f"g{gi}"
        if g.kind == "nand":
            places.append(f"    {var} = b.place(and2, at=({gx}, 0, {GZ}), floor=False, name=\"{g.name}\")\n"
                          f"    b.place(not_gate, at=({gx + 8}, 0, {GZ + 1}), floor=False, name=\"{g.name}_not\")   # NAND = NOT(AND)")
        else:
            places.append(f"    {var} = b.place({spec['uses'][0]}, at=({gx}, 0, {GZ}), floor=False, name=\"{g.name}\")")
        for port, x, s in zip(names, tap_x, g.ins):
            ox, oz = spec["ins"][port]
            taps.append((s, x, (gx + ox, 1, GZ + oz)))
            cols.setdefault(s, []).append(x)
        ox, oz = spec["out"]
        xo = gx + ox + 1
        outs_x[g.name] = (xo, (gx + ox, 1, GZ + oz))
        gx = xo + 3 * CS
    xmax = gx - 2 * CS                          # lamp column, east of the last output column
    levers = {s: (0, 1, ZB + PITCH * sidx[s]) for s in nl.inputs}
    lamps = {o: (xmax, 1, ZB + PITCH * sidx[s]) for o, s in nl.outputs.items()}
    out_sigs = set(nl.outputs.values())
    L = [f"# synthetic netlist: {len(nl.inputs)} inputs, {len(nl.gates)} gates, {len(nl.outputs)} outputs"]
    L += [f"#   {line}" for line in nl.describe()]
    L += [PROGRAM_HELPERS.strip("\n"), "", "", "def build(b):"]
    for s, pos in levers.items():
        L.append(f"    b.set({pos}, lever(face=\"floor\", facing=EAST))   # input {s}, bus {sidx[s]}")
    for o, pos in lamps.items():
        L.append(f"    b.set({pos}, lamp())   # output {o} = {nl.outputs[o]}")
    L += places
    for s in sigs:
        x0 = 0 if s in levers else outs_x[s][0]
        x1 = xmax if s in out_sigs else max(cols.get(s, [x0 + 2]))
        L.append(f"    bus(b, {sidx[s]}, {x0}, {x1}, {sorted(cols.get(s, []))}, {s not in levers})   # {s}")
    for s, (xo, ypos) in outs_x.items():
        L.append(f"    out(b, {ypos}, {xo}, {sidx[s]})   # {s} -> its bus")
    for s, x, port in taps:
        L.append(f"    tap(b, {sidx[s]}, {x}, {port})   # {s} -> gate input")
    size = (xmax + 1, 5, ZB + PITCH * (len(sigs) - 1) + 2)
    return {"body": "\n".join(L) + "\n", "uses": uses, "levers": levers, "lamps": lamps, "size": size}


# --------------------------------------------------------------------------- task
def truth_rows(nl, rng, exhaustive_max=4, n_public=8, n_hidden=16):
    """Exhaustive (k <= exhaustive_max: public = about half, always showing both values of every output, plus a hidden reversed
    copy) or random public + disjoint random hidden vectors (k > exhaustive_max)."""
    from env.contract import Vector
    tab = nl.table()
    outs = list(nl.outputs)
    if len(nl.inputs) <= exhaustive_max:
        idx = list(range(len(tab)))
        public = set(idx[::2]) if len(nl.inputs) > 2 else set(idx)
    else:
        idx = rng.sample(range(len(tab)), min(len(tab), n_public + n_hidden))
        public = set(idx[:n_public])
    for o in outs:                              # public rows must show both values of every output
        for val in (0, 1):
            if not any(tab[i][1][o] == val for i in public):
                cand = [i for i in idx if tab[i][1][o] == val]
                if cand:
                    public.add(cand[0])
    rows = [Vector(dict(zip(nl.inputs, tab[i][0])), dict(tab[i][1]), i in public, "".join(map(str, tab[i][0]))) for i in idx]
    rows += [Vector(r.inputs, r.expect, False, r.label + "'") for r in reversed(rows)]
    return rows


def plan_from_layout(task_id, title, description, lay, tier="SYN"):
    from env.tasks.author_t3 import Plan3
    p = Plan3(task_id, title, lay["size"], description)
    p.tier = tier
    for name, pos in lay["levers"].items():
        p.dev_lever(name, pos, "input lever")
    for name, pos in lay["lamps"].items():
        p.dev_lamp(name, pos, "output lamp")
    p.program(lay["uses"], lay["body"])
    return p


def describe_task(nl):
    lines = [f"Build a combinational circuit with {len(nl.inputs)} input lever(s) {', '.join(nl.inputs)} and {len(nl.outputs)} output lamp(s) "
             f"{', '.join(nl.outputs)}. The lamps must show:"]
    lines += [f"  {o} = {nl.expression(s)}" for o, s in nl.outputs.items()]
    lines.append("(AND, OR, NOR, NAND, NOT are the usual Boolean operators; 1 = lever on / lamp lit.) The devices are fixed; connect them. "
                 "Library hint (component tier only): NOT, AND, OR and NOR gate cells are available.")
    return "\n".join(lines)


def make_task(nl, task_id, seed=0, tier="SYN"):
    """-> (Task, program text, info). Raises ValueError when the reference program does not compile (routing conflict)."""
    from env import library as L
    from env.contract import TruthTable
    lay = layout(nl)
    depth = nl.depth()
    p = plan_from_layout(task_id, f"synthetic netlist ({len(nl.gates)} gates)", describe_task(nl), lay, tier)
    settle = 120 + 40 * depth
    p.behaviour.truth_table = TruthTable(truth_rows(nl, random.Random(seed)), settle=settle, shuffle=True, reset_between=False,
                                         order_independent=True)
    p.depends = nl.depends()
    p.budgets.rest = settle
    p.budgets.total = 200000
    p.budgets.max_blocks = 6000
    task = p.build()
    info = {"depth": depth, "uses": lay["uses"], "size": lay["size"], "netlist": nl.to_dict()}
    return task, L.program(lay["uses"], lay["body"]), info


def difficulty(n_gates, k):
    return "easy" if n_gates <= 2 else "medium" if n_gates <= 4 else "hard"
