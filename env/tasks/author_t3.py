"""Authoring script for env/tasks/t3/*.json: T3 tasks (100-500 blocks) whose REFERENCE is a builder program over lib/ components.

    python env/tasks/author_t3.py             # regenerate every T3 task JSON (keeps stored broken variants of unchanged references)
    python env/tasks/author_t3.py t3_adder2   # one task
    python env/tasks/author_t3.py --programs  # print the reference programs

A task = a `Plan` (floor + fixed levers/lamps + ports + behaviour, from env/tasks/author.py) plus a reference builder program:
`env.library.program(uses, body)` prepends the library sources; the body places `lib` parts with `array`/`place` and joins their
ports with `wire_path`. Compiling it gives the full build; the task solution is that build minus the fixed devices and the floor.
Layout conventions: x east, z south, y=0 is the fixed smooth_stone floor, y=1 is the first logic level.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import random  # noqa: E402

from env import library as L  # noqa: E402
from env.contract import Expect, ReferenceModel, TruthTable, Vector  # noqa: E402
from env.task import Task, load_task, save_task  # noqa: E402
from env.tasks.author import LAMP, LEVER, Plan  # noqa: E402

OUT = Path(__file__).parent / "t3"
FLOOR = "minecraft:smooth_stone"


class Plan3(Plan):
    """Plan whose solution comes from a compiled library program. `dev_lever/dev_lamp` register the fixed devices (also written
    into the program so that wire_path sees them as occupied cells)."""

    def __init__(self, id, title, size, description, notes=""):
        super().__init__(id, title, "T3", size, description, notes)
        self.uses, self.body = [], ""
        self.levers, self.lamps = [], []

    def dev_lever(self, name, pos, doc=""):
        self.lever(name, pos, doc)
        self.levers.append((name, tuple(pos)))

    def dev_lamp(self, name, pos, doc=""):
        self.lamp(name, pos, doc)
        self.lamps.append((name, tuple(pos)))

    def program(self, uses, body):
        self.uses, self.body = list(uses), body
        return self

    def compile_solution(self):
        build = L.compile_program(self.uses, self.body, sandbox=False)
        if not build.ok:
            raise ValueError(f"{self.id}: reference program does not compile:\n" + "\n".join(str(e) for e in build.errors[:6]))
        fixed = {}
        for f in self.fixed:
            x1, y1, z1, x2, y2, z2 = f["box"]
            for x in range(x1, x2 + 1):
                for y in range(y1, y2 + 1):
                    for z in range(z1, z2 + 1):
                        fixed[(x, y, z)] = f["state"]
        sol = []
        for x, y, z, st, nbt in build.blocks:
            if (x, y, z) in fixed:
                continue                      # floor / levers / lamps / pad cells are fixed scenery
            if y == 0:
                continue                      # library cells carry their own y=0 supports; the task floor replaces them
            sol.append([x, y, z, st])
        self.solution = sol
        self.build_info = build
        return build

    def build(self):
        self.compile_solution()
        return super().build()


# --------------------------------------------------------------------------- reference models (registered by name)
def adder_body(n, px, x0, oz, zd):
    """Reference program text for an n-bit ripple adder: bit 0 at the east, carry flowing west, I/O devices in the row z=zd."""
    return f"""
N = {n}
PX = {px}
OZ = {oz}
ZD = {zd}


def route(b, a, z, every=6):
    wire_path(b, a, z, repeat_every=every)                      # a repeater after every (every-1) dust keeps the ports >= 10


def build(b):
    b.fill((0, 0, 0), ({x0 + px * (n - 1) + 14}, 0, {zd + 1}), solid("smooth_stone"))
    fa = b.array(full_adder, n=N, step=(-PX, 0, 0), at=({x0 + px * (n - 1)}, 0, OZ), floor=False, name="fa")
    for i in range(N):
        ox = fa[i].at.x
        oz = fa[i].at.z
        lever_a = P(ox - 4, 1, ZD)
        lever_b = P(ox + 4, 1, ZD)
        lamp_s = P(ox + 9, 1, ZD)
        b.set(lever_a, lever(face="floor", facing=EAST))
        b.set(lever_b, lever(face="floor", facing=EAST))
        b.set(lamp_s, lamp())
        route(b, lever_a, P(ox - 4, 1, oz + 4))           # A_i: north along x = ox-4, then up onto the west face
        route(b, P(ox - 4, 1, oz + 4), P(ox - 1, 2, oz + 4))
        route(b, lever_b, P(ox + 4, 1, oz + 7))           # B_i: straight north into the south face
        route(b, fa[i]["S"], P(ox + 9, 3, oz + 6))        # S_i: east along y=3, then down to the lamp row
        route(b, P(ox + 9, 3, oz + 6), P(ox + 9, 1, ZD - 1))
        if i < N - 1:                                          # carry: COUT_i -> CIN_(i+1) through the gap
            nxt = fa[i + 1]["CIN"]
            route(b, fa[i]["COUT"], P(ox - 4, 3, oz + 1), 3)
            route(b, P(ox - 4, 3, oz + 1), P(nxt.x + 1, 3, oz + 1), 3)
    ox0 = fa[0].at.x
    cin = P(ox0 + 10, 1, OZ + 1)
    b.set(cin, lever(face="floor", facing=EAST))
    route(b, cin, P(ox0 + 9, 1, OZ + 1))                       # CIN: one flat cell next to the lever, then up to the east face of bit 0
    route(b, P(ox0 + 9, 1, OZ + 1), P(ox0 + 7, 3, OZ + 1))
    last = fa[N - 1]
    cout = P(last.at.x - 7, 1, OZ + 2)
    b.set(cout, lamp())
    route(b, last["COUT"], P(last.at.x - 6, 1, OZ + 2))
"""


ADDER_PX, ADDER_X0, ADDER_OZ, ADDER_ZD = 16, 16, 2, 15


def t3_adder(n):
    px, x0, oz, zd = ADDER_PX, ADDER_X0, ADDER_OZ, ADDER_ZD
    ox0 = x0 + px * (n - 1)
    size = (ox0 + 14, 6, zd + 2)
    p = Plan3(f"t3_adder{n}", f"{n}-bit ripple-carry adder", size,
              f"Build a {n}-bit adder. Levers A0..A{n - 1} (bit 0 = least significant) and B0..B{n - 1} and carry-in lever CIN "
              f"are inputs; lamps S0..S{n - 1} show the sum bits and lamp COUT the carry out, i.e. "
              f"(COUT S{n - 1} .. S0) = A + B + CIN as binary numbers. The devices are fixed; connect them. "
              "Library hint (component tier only): a full-adder cell is available.")
    for i in range(n):
        ox = ox0 - px * i
        p.dev_lever(f"A{i}", (ox - 4, 1, zd), f"addend bit {i}")
        p.dev_lever(f"B{i}", (ox + 4, 1, zd), f"addend bit {i}")
        p.dev_lamp(f"S{i}", (ox + 9, 1, zd), f"sum bit {i}")
    p.dev_lever("CIN", (ox0 + 10, 1, oz + 1), "carry in")
    p.dev_lamp("COUT", (ox0 - px * (n - 1) - 7, 1, oz + 2), "carry out")
    p.program(["full_adder"], adder_body(n, px, x0, oz, zd))
    ins = [f"A{i}" for i in range(n)] + [f"B{i}" for i in range(n)] + ["CIN"]
    outs = [f"S{i}" for i in range(n)] + ["COUT"]
    p.behaviour.reference_model = ReferenceModel("adder_cin", ins, outs, n_public=6, n_hidden=40, public_seed=11, hidden_seed=12 + n,
                                                 mode="comb", settle=60 + 30 * n, params={"bits": n})
    set_adder_behaviour(p, n, ins, outs)
    p.budgets.rest = 100 + 20 * n
    p.budgets.max_blocks = 1500
    return p


# --------------------------------------------------------------------------- decoders (tree of demux2 cells, crossover at every bus/feeder crossing)
DEC_PXL, DEC_PZ, DEC_X0, DEC_Z0 = 16, 8, 12, 10


def decoder_geometry(bits):
    """Cell origins per level and the device positions: levers EN, A{bits-1}..A0 on the row ZL, lamps Y0..Y{2^bits-1} east of the leaves.
    Every level is a demux2 cell column; level 0 has one cell whose enable is the EN lever."""
    zl = DEC_Z0 - 8
    levels = []
    for k in range(bits):
        pitch = DEC_PZ << (bits - 1 - k)
        first = DEC_Z0 + (pitch - DEC_PZ) // 2
        levels.append((DEC_X0 + DEC_PXL * k, [first + j * pitch for j in range(1 << k)]))
    levers = {"EN": (DEC_X0 - 3, 1, levels[0][1][0] + 1)}
    for k, (ox, ozs) in enumerate(levels):
        levers[f"A{bits - 1 - k}"] = (ox + 2, 1, zl) if k == 0 else (ox - 4, 1, zl)
    lamps = {}
    ox, ozs = levels[-1]
    for j, oz in enumerate(ozs):
        lamps[f"Y{2 * j}"] = (ox + 8, 1, oz)
        lamps[f"Y{2 * j + 1}"] = (ox + 8, 1, oz + 2)
    size = (ox + 10, 5, ozs[-1] + 5)
    return levels, levers, lamps, size


DEC_LAMP_TAIL = """
    for j in range(len(srcs)):
        src = srcs[j]
        b.set(P(src.x + 3, 1, src.z), lamp())
        route(b, src, P(src.x + 2, 1, src.z))
"""


def decoder_body(bits, tail=DEC_LAMP_TAIL):
    return f"""
BITS = {bits}
PXL = {DEC_PXL}
PZ = {DEC_PZ}
X0 = {DEC_X0}
Z0 = {DEC_Z0}
ZL = Z0 - 8


def route(b, a, z, every=6):
    wire_path(b, a, z, repeat_every=every)


def build(b):
    # level 0: one demux cell, enabled by the EN lever, split by the most significant address lever
    first = Z0 + ((PZ << (BITS - 1)) - PZ) // 2
    root = b.place(demux2, at=(X0, 0, first), floor=False, name="root")
    en = P(X0 - 3, 1, first + 1)
    b.set(en, lever(face="floor", facing=EAST))
    route(b, en, root["EN"])
    msb = P(root["A"].x, 1, ZL)
    b.set(msb, lever(face="floor", facing=EAST))
    route(b, msb, root["A"], 15)
    srcs = [root["Y0"], root["Y1"]]                           # one-hot lines of the levels above, in index order
    for k in range(1, BITS):
        pitch = PZ << (BITS - 1 - k)
        first = Z0 + (pitch - PZ) // 2
        ox = X0 + PXL * k
        lvl = b.array(demux2, n=1 << k, step=(0, 0, pitch), at=(ox, 0, first), floor=False, name="d" + str(k))
        lv = P(ox - 4, 1, ZL)
        b.set(lv, lever(face="floor", facing=EAST))
        prev = lv
        for j in range(len(lvl)):
            c = lvl[j]
            oz = c.at.z
            src = srcs[j]
            xm = src.x + 3
            x = b.place(crossover, at=(ox - 6, 0, oz - 1), floor=False, name="x" + str(k) + "_" + str(j))
            b.set(x["XW"] + (-1, 0, 0), repeater(flow=EAST, delay=1))     # full strength into the crossover
            route(b, src, P(xm, 1, src.z), 4)                 # feeder: east, vertical, east into the crossover (the address bus crosses it)
            route(b, P(xm, 1, src.z), P(xm, 1, x["XW"].z), 4)
            route(b, P(xm, 1, x["XW"].z), x["XW"] + (-1, 0, 0), 4)
            route(b, x["XE"], c["EN"])
            route(b, prev, x["ZN"], 15)                       # address bus: lever -> crossover Z line -> next crossover
            b.set(P(ox - 3, 1, oz - 3), repeater(flow=EAST, delay=1))     # tap in the row north of the cell: restore, then feed its A port
            route(b, P(ox - 4, 1, oz - 3), P(c["A"].x, 1, oz - 3), 15)
            prev = x["ZS"]
        srcs = [pt for c in lvl for pt in (c["Y0"], c["Y1"])]
{tail}"""


def t3_decoder(bits):
    levels, levers, lamps, size = decoder_geometry(bits)
    n = 1 << bits
    p = Plan3(f"t3_decoder{bits}to{n}", f"{bits}-to-{n} decoder", size,
              f"Build a {bits}-to-{n} line decoder with enable. The address levers {', '.join(f'A{k}' for k in range(bits - 1, -1, -1))} (A0 = least significant) "
              f"select exactly one of the {n} lamps Y0..Y{n - 1}: lamp Yk is lit iff the enable lever EN is on and the address equals k; "
              "with EN off every lamp is dark. "
              "Library hint (component tier only): a 1-to-2 decoder cell with enable and a signal crossover are available.")
    for name, pos in sorted(levers.items(), reverse=True):
        p.dev_lever(name, pos, f"address bit {name[1:]}")
    for name, pos in lamps.items():
        p.dev_lamp(name, pos, f"line {name[1:]}")
    p.program(["demux2", "crossover"], decoder_body(bits))
    outs = [f"Y{k}" for k in range(n)]
    import itertools
    ins_order = ["EN"] + [f"A{k}" for k in range(bits - 1, -1, -1)]          # the order of `combos`
    combos = list(itertools.product((0, 1), repeat=len(ins_order)))

    def fn(**kw):
        addr = sum(kw[f"A{k}"] << k for k in range(bits))
        return tuple(int(kw["EN"] and k == addr) for k in range(n))
    rows = vector_rows(ins_order, outs, fn, combos, public=set(range(0, len(combos), 3)))
    rows += [Vector(r.inputs, r.expect, False, r.label + "'") for r in reversed(rows)]
    p.behaviour.truth_table = TruthTable(rows, settle=250, shuffle=True, reset_between=False, order_independent=True)
    p.behaviour.reference_model = ReferenceModel("decoder_en", ins_order, outs, n_public=3, n_hidden=10, public_seed=21, hidden_seed=22 + bits,
                                                 mode="comb", settle=250, params={"bits": bits})
    p.depends = {o: list(ins_order) for o in outs}
    p.budgets.rest = 200
    p.budgets.max_blocks = 1500
    return p


# --------------------------------------------------------------------------- n-bit ripple counters (chains of T flip-flops)
def counter_body(bits, up):
    return f"""
N = {bits}
UP = {up}


def route(b, a, z, every=6):
    wire_path(b, a, z, repeat_every=every)


def build(b):
    clk = P(0, 1, 3)
    b.set(clk, lever(face="floor", facing=EAST))
    src = P(1, 1, 3)
    route(b, clk, src)
    ox, oz = 2, 3
    for i in range(N):
        ff = b.place(t_flipflop, at=(ox, 0, oz), floor=False, name="ff" + str(i))
        route(b, src, ff["T"])
        q = ff["Q"]
        tap = P(q.x + 1, 1, q.z)
        route(b, q, tap)
        route(b, tap, P(q.x + 1, 1, q.z + 1))                 # stub south of the Q wire to the count lamp
        b.set(P(q.x + 1, 1, q.z + 2), lamp())
        if i < N - 1:
            if UP:                                            # the next stage toggles on the FALLING edge of Q: clock it with !Q
                inv = b.place(not_gate, at=(q.x + 2, 0, q.z), floor=False, name="inv" + str(i))
                route(b, tap, inv["A"])
                src = inv["Y"]
            else:                                             # rising edge of Q: the count goes 0, 3, 2, 1 (down)
                src = P(q.x + 2, 1, q.z)
                route(b, tap, src)
            ox, oz = src.x + 4, q.z
"""


def counter_public_seed(n_public, bits, down=False, p_one=0.5, start=1):
    """The runner executes public then hidden model vectors on ONE circuit without reset, but ReferenceModel.generate restarts the
    model for the hidden stream. So pick a public seed whose stream ends back at count 0 with CLK low (and has >= 2^bits rising edges)."""
    import random as _r
    from env.models import get_model
    fn = get_model("counter_tff")
    for seed in range(start, 200000):
        rng, state, edges, prev, last = _r.Random(seed), {}, 0, 0, 0
        for _ in range(n_public):
            clk = int(rng.random() < p_one)
            fn(state, {"CLK": clk}, bits=bits, down=down)
            edges += int(clk and not prev)
            prev = last = clk
        if last == 0 and state["n"] == 0 and edges >= (1 << bits):
            return seed
    raise ValueError("no public seed found")


def t3_counter(bits, up=True):
    tid = f"t3_counter{bits}" + ("" if up else "_down")
    n = 1 << bits
    seq = "00 -> 11 -> 10 -> 01 -> 00" if bits == 2 else "counting down"
    p = Plan3(tid, f"{bits}-bit {'up' if up else 'down'} counter", (16 * bits - 2 + (3 if not up else 0), 5, 8 + 3 * bits + 4),
              f"Build a {bits}-bit ripple counter from toggle (T) flip-flops. The CLK lever is the clock: every time it is switched ON (rising edge) "
              f"the count (lamps Q{bits - 1} .. Q0 as a binary number, Q0 = least significant) "
              + (f"goes up by one and wraps from {n - 1} to 0. " if up else f"goes DOWN by one and wraps from 0 to {n - 1}, i.e. {seq}. ")
              + "Switching CLK off changes nothing. All lamps are off at the start. A T flip-flop toggles on a rising edge of its input: "
              "locked repeaters give a master/slave design. Library hint (component tier only): a T flip-flop and a NOT gate are available.")
    p.dev_lever("CLK", (0, 1, 3), "clock")
    for i in range(bits):
        p.dev_lamp(f"Q{i}", (10 + (16 if up else 13) * i, 1, 8 + 3 * i), f"count bit {i}")
    p.program(["t_flipflop"] + (["not_gate"] if up else []), counter_body(bits, up))
    outs = [f"Q{i}" for i in range(bits)]
    n_public = 14 if bits == 2 else 40
    p.behaviour.reference_model = ReferenceModel("counter_tff", ["CLK"], outs, n_public=n_public, n_hidden=40 if bits == 2 else 60,
                                                 public_seed=counter_public_seed(n_public, bits, not up), hidden_seed=32,
                                                 mode="seq", settle=300, params={"bits": bits, "down": not up})
    p.budgets.rest = 300
    p.budgets.max_blocks = 1000
    p.notes = ("The public model stream is chosen so that it ends at count 0 with CLK low: the hidden stream is generated by a fresh "
               "model instance but executed on the same circuit without reset (env/contract.py ReferenceModel.generate does not chain state).")
    return p


# --------------------------------------------------------------------------- parity of 4 (tree of xor2), majority of 3 (full-adder carry)
PARITY_BODY = """
def route(b, a, z, every=6):
    wire_path(b, a, z, repeat_every=every)


def build(b):
    x1 = b.place(xor2, at=(10, 0, 20), floor=False, name="x1")           # I0 ^ I1
    x2 = b.place(xor2, at=(24, 0, 20), floor=False, name="x2")           # I2 ^ I3
    x3 = b.place(xor2, at=(17, 0, 6), floor=False, name="x3")            # (I0 ^ I1) ^ (I2 ^ I3)
    for name, cell, a_lever, b_lever in (("a", x1, P(7, 1, 21), P(18, 1, 23)), ("b", x2, P(21, 1, 21), P(32, 1, 23))):
        b.set(a_lever, lever(face="floor", facing=EAST))
        b.set(b_lever, lever(face="floor", facing=EAST))
        route(b, a_lever, cell["A"])                                      # straight east into the west face
        side = P(b_lever.x - 1, 1, b_lever.z)                             # one flat cell next to the lever, then up onto the east port (y=2)
        route(b, b_lever, side)
        route(b, side, P(cell["B"].x + 1, 2, cell["B"].z))
    y1, y2 = x1["Y"], x2["Y"]
    route(b, y1, P(y1.x, 2, y1.z - 2))                                    # leave the cell northwards first (its dust must not touch the xor wires)
    route(b, P(y1.x, 2, y1.z - 2), P(15, 1, y1.z - 2))
    route(b, P(15, 1, y1.z - 2), P(15, 1, x3["A"].z))
    route(b, P(15, 1, x3["A"].z), P(x3["A"].x - 1, 1, x3["A"].z))
    route(b, y2, P(y2.x, 2, y2.z - 2))
    route(b, P(y2.x, 2, y2.z - 2), P(x3["B"].x + 2, 2, y2.z - 2))
    route(b, P(x3["B"].x + 2, 2, y2.z - 2), P(x3["B"].x + 2, 2, x3["B"].z))
    route(b, P(x3["B"].x + 2, 2, x3["B"].z), P(x3["B"].x + 1, 2, x3["B"].z))
    out = x3["Y"]
    route(b, out, P(out.x, 1, out.z - 3))
    b.set(P(out.x, 1, out.z - 4), lamp())
"""


def t3_parity4():
    p = Plan3("t3_parity4", "4-input parity", (36, 5, 26),
              "Build a parity (odd-ones) detector: the lamp Y must be lit exactly when an odd number (1 or 3) of the four levers I0..I3 are on. "
              "Library hint (component tier only): an XOR gate with dust ports is available.")
    for name, pos in (("I0", (7, 1, 21)), ("I1", (18, 1, 23)), ("I2", (21, 1, 21)), ("I3", (32, 1, 23))):
        p.dev_lever(name, pos, "input")
    p.dev_lamp("Y", (19, 1, 2), "odd parity")
    p.program(["xor2"], PARITY_BODY)
    ins = ["I0", "I1", "I2", "I3"]
    import itertools
    combos = list(itertools.product((0, 1), repeat=4))
    rows = vector_rows(ins, ["Y"], lambda **kw: sum(kw.values()) & 1, combos, public=set(range(0, 16, 4)))
    rows += [Vector(r.inputs, r.expect, False, r.label + "'") for r in reversed(rows)]
    p.behaviour.truth_table = TruthTable(rows, settle=200, shuffle=True, reset_between=False, order_independent=True)
    p.behaviour.reference_model = ReferenceModel("parity", ins, ["Y"], n_public=3, n_hidden=10, public_seed=41, hidden_seed=42, mode="comb",
                                                 settle=200, params={"n": 4})
    p.depends = {"Y": ins}
    p.budgets.rest = 150
    p.budgets.max_blocks = 800
    return p


MAJ_BODY = """
def route(b, a, z, every=6):
    wire_path(b, a, z, repeat_every=every)


def build(b):
    fa = b.place(full_adder, at=(14, 0, 4), floor=False, name="fa")      # only its carry output is used
    ox, oz = fa.at.x, fa.at.z
    i0, i1, i2 = P(ox - 4, 1, 16), P(ox + 4, 1, 16), P(ox + 10, 1, oz + 1)
    for lv in (i0, i1, i2):
        b.set(lv, lever(face="floor", facing=EAST))
    route(b, i0, P(ox - 4, 1, oz + 4))
    route(b, P(ox - 4, 1, oz + 4), P(ox - 1, 2, oz + 4))
    route(b, i1, P(ox + 4, 1, oz + 7))
    route(b, i2, P(ox + 9, 1, oz + 1))
    route(b, P(ox + 9, 1, oz + 1), P(ox + 7, 3, oz + 1))
    y = P(ox - 7, 1, oz + 2)
    b.set(y, lamp())
    route(b, fa["COUT"], P(ox - 6, 1, oz + 2))
"""


def t3_majority3():
    p = Plan3("t3_majority3", "3-input majority", (30, 6, 18),
              "Build a majority voter: the lamp Y must be lit exactly when at least two of the three levers I0, I1, I2 are on. "
              "Library hint (component tier only): a full-adder cell has a majority output.")
    for name, pos in (("I0", (10, 1, 16)), ("I1", (18, 1, 16)), ("I2", (24, 1, 5))):
        p.dev_lever(name, pos, "input")
    p.dev_lamp("Y", (7, 1, 6), "majority")
    p.program(["full_adder"], MAJ_BODY)
    ins = ["I0", "I1", "I2"]
    import itertools
    combos = list(itertools.product((0, 1), repeat=3))
    rows = vector_rows(ins, ["Y"], lambda **kw: int(sum(kw.values()) >= 2), combos, public=set(range(0, 8, 2)))
    rows += [Vector(r.inputs, r.expect, False, r.label + "'") for r in reversed(rows)]
    p.behaviour.truth_table = TruthTable(rows, settle=200, shuffle=True, reset_between=False, order_independent=True)
    p.behaviour.reference_model = ReferenceModel("majority", ins, ["Y"], n_public=2, n_hidden=8, public_seed=51, hidden_seed=52, mode="comb",
                                                 settle=200, params={"n": 3})
    p.depends = {"Y": ins}
    p.budgets.rest = 150
    p.budgets.max_blocks = 600
    return p


# --------------------------------------------------------------------------- 4 x 4 lookup table (decoder + staircase OR network)
ROM_WORDS = (0b0001, 0b0011, 0b0110, 0b1100)       # word k = address k; bit j is set iff D_j = Y_j | Y_(j+1) selects it (planar band)
ROM_TAIL = """
    xo = srcs[0].x + 8
    z0 = srcs[0].z
    d0 = b.place(or2, at=(xo, 0, z0), floor=False, name="d0")           # D0 = Y0 | Y1
    d1 = b.place(or2, at=(xo, 0, z0 + 4), floor=False, name="d1")       # D1 = Y1 | Y2
    d2 = b.place(or2, at=(xo, 0, z0 + 8), floor=False, name="d2")       # D2 = Y2 | Y3
    route(b, srcs[0], d0["A"], 15)
    route(b, srcs[1], d0["B"], 15)
    route(b, P(xo - 1, 1, z0 + 2), P(xo - 1, 1, z0 + 4), 15)            # Y1 also feeds D1.A through the dust link next to the ports
    route(b, srcs[2], P(xo - 1, 1, z0 + 8), 15)
    route(b, P(xo - 1, 1, z0 + 8), P(xo - 1, 1, z0 + 6), 15)            # Y2 feeds D2.A (z0 + 8) and D1.B (z0 + 6)
    route(b, srcs[3], d2["B"], 15)
    for cell in (d0, d1, d2):
        y = cell["Y"]
        b.set(P(y.x + 3, 1, y.z), lamp())
        route(b, y, P(y.x + 2, 1, y.z))
    stub = P(xo - 3, 1, z0 + 10)                                        # D3 = Y3: a stub south of the Y3 line to a lamp
    route(b, stub, P(xo - 3, 1, z0 + 11), 15)
    b.set(P(xo - 3, 1, z0 + 12), lamp())
"""


def t3_rom4x4():
    levels, levers, lamps, size = decoder_geometry(2)
    leaf_x = levels[-1][0]
    xo = leaf_x + 5 + 8
    z0 = levels[-1][1][0]
    size = (xo + 12, 5, z0 + 16)
    p = Plan3("t3_rom4x4", "4 x 4 read-only memory", size,
              "Build a 4-word read-only memory. The address levers A1 A0 (A0 = least significant) select a word; the lamps D3 D2 D1 D0 must show "
              "the stored 4-bit word: address 0 -> 0001, 1 -> 0011, 2 -> 0110, 3 -> 1100 (D0 is the right-most bit). The enable lever EN must be on "
              "for the memory to output; with EN off all data lamps are dark. "
              "Library hint (component tier only): a 1-to-2 decoder cell with enable, a signal crossover and an OR gate are available.")
    for name, pos in sorted(levers.items(), reverse=True):
        p.dev_lever(name, pos, "enable" if name == "EN" else f"address bit {name[1:]}")
    p.dev_lamp("D0", (xo + 4 + 3, 1, z0 + 1), "data bit 0")
    p.dev_lamp("D1", (xo + 4 + 3, 1, z0 + 5), "data bit 1")
    p.dev_lamp("D2", (xo + 4 + 3, 1, z0 + 9), "data bit 2")
    p.dev_lamp("D3", (xo - 3, 1, z0 + 12), "data bit 3")
    p.program(["demux2", "crossover", "or2"], decoder_body(2, ROM_TAIL))
    import itertools
    ins = ["EN", "A1", "A0"]
    outs = ["D0", "D1", "D2", "D3"]
    combos = list(itertools.product((0, 1), repeat=3))

    def fn(**kw):
        w = ROM_WORDS[kw["A1"] * 2 + kw["A0"]] if kw["EN"] else 0
        return tuple((w >> j) & 1 for j in range(4))
    rows = vector_rows(ins, outs, fn, combos, public=set(range(0, 8, 2)))
    rows += [Vector(r.inputs, r.expect, False, r.label + "'") for r in reversed(rows)]
    p.behaviour.truth_table = TruthTable(rows, settle=250, shuffle=True, reset_between=False, order_independent=True)
    p.behaviour.reference_model = ReferenceModel("rom_en", ins, outs, n_public=2, n_hidden=10, public_seed=61, hidden_seed=62, mode="comb",
                                                 settle=250, params={"words": list(ROM_WORDS), "width": 4, "abits": 2})
    p.depends = {o: ["EN", "A1", "A0"] for o in outs}
    p.budgets.rest = 200
    p.budgets.max_blocks = 1000
    return p


def vector_rows(ins, outs, fn, combos, public=(), tag=""):
    """Explicit truth-table rows: fn(**inputs) -> tuple/dict of expected outputs; rows whose index is in `public` are visible."""
    rows = []
    for i, combo in enumerate(combos):
        got = fn(**dict(zip(ins, combo)))
        got = got if isinstance(got, (tuple, list)) else (got,)
        rows.append(Vector(dict(zip(ins, combo)), dict(zip(outs, got)), i in public, f"{tag}{i}"))
    return rows


def set_adder_behaviour(p, n, ins, outs):
    """Reference model (seeded random vectors) + exhaustive rows for n <= 3 or carry-chain corner rows for larger n."""
    import itertools

    def fn(**kw):
        t = sum(kw[f"A{k}"] << k for k in range(n)) + sum(kw[f"B{k}"] << k for k in range(n)) + kw["CIN"]
        return tuple((t >> k) & 1 for k in range(n)) + ((t >> n) & 1,)
    if n <= 3:
        combos = list(itertools.product((0, 1), repeat=len(ins)))
        rows = vector_rows(ins, outs, fn, combos, public=set(range(0, len(combos), max(1, len(combos) // 6))), tag="all")
    else:
        full, half = (1 << n) - 1, 1 << (n - 1)
        pairs = [(full, 1, 0), (half - 1, 1, 0), (full, full, 1), (half, half, 0), (0, 0, 1), (full, 0, 1), (0x55 & full, 0xAA & full, 1),
                 (0xAA & full, 0x55 & full, 0), (1, full, 0), (half, 0, 0)]
        combos = [tuple(((a >> k) & 1) for k in range(n)) + tuple(((b >> k) & 1) for k in range(n)) + (c,) for a, b, c in pairs]
        rows = vector_rows(ins, outs, fn, combos, public=set(), tag="corner")
    settle = 60 + 30 * n
    p.behaviour.truth_table = TruthTable(rows, settle=settle, shuffle=True, reset_between=False, order_independent=True)
    p.depends = {f"S{k}": [f"A{j}" for j in range(k + 1)] + [f"B{j}" for j in range(k + 1)] + ["CIN"] for k in range(n)}
    p.depends["COUT"] = list(ins)


def build_all():
    return [t3_adder(2), t3_adder(3), t3_adder(4), t3_adder(8), t3_decoder(2), t3_decoder(3), t3_counter(2, True), t3_counter(2, False), t3_counter(4, True),
            t3_parity4(), t3_majority3(), t3_rom4x4()]


def task_ids():
    return sorted(p.stem for p in OUT.glob("t3_*.json"))


def validate_one(args):
    """Acceptance gate (env/validate_tasks.evaluate) for one stored T3 task; models of env.library are registered by the import above."""
    from env import validate_tasks as V
    tid, mutants, write = args
    task = load_task(OUT / f"{tid}.json")
    rep = V.evaluate(task, mutants)
    if write and rep["accepted"]:
        task.broken = [{"name": b["name"], "kind": b["kind"], "edits": b["edits"]} for b in rep["broken_examples"]]
        save_task(task, OUT / f"{tid}.json")
    return rep


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--programs", action="store_true", help="print the reference programs")
    ap.add_argument("--validate", action="store_true", help="run the acceptance gate on the stored task JSONs instead of regenerating them")
    ap.add_argument("--oracle", action="store_true", help="cross-check the stored references on the vanilla 26.2 oracle; writes t3/oracle_report.json")
    ap.add_argument("--mutants", type=int, default=24)
    ap.add_argument("--write-broken", action="store_true", help="(with --validate) store the failing variants of accepted tasks in the JSON")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    want = set(a.ids)
    if a.oracle:
        import json
        import time
        report = {}
        for tid in [t for t in task_ids() if not want or t in want]:
            t0 = time.time()
            r = L.oracle_task(load_task(OUT / f"{tid}.json"))
            r["seconds"] = round(time.time() - t0, 1)
            report[tid] = r
            print(f"{tid:20} vanilla={'pass' if r['vanilla_pass'] else 'FAIL ' + str(r['vanilla_failures'][:3])} nucleation={'pass' if r['nucleation_pass'] else 'FAIL'} "
                  f"tick-identical={r['agree']}/{r['captures']} hold={r['hold']} vectors={r['vectors']} {r['seconds']}s", flush=True)
        path = OUT / "oracle_report.json"
        prev = json.loads(path.read_text()) if path.exists() else {}
        prev.update(report)
        path.write_text(json.dumps(prev, indent=1, sort_keys=True) + "\n")
        return 0 if all(r["vanilla_pass"] and r["nucleation_pass"] and r["agree"] == r["captures"] for r in report.values()) else 1
    if a.validate:
        from concurrent.futures import ProcessPoolExecutor
        ids = [t for t in task_ids() if not want or t in want]
        with ProcessPoolExecutor(max(1, min(a.jobs, len(ids)))) as ex:
            reps = list(ex.map(validate_one, [(t, a.mutants, a.write_broken) for t in ids]))
        if a.json:
            import json
            print(json.dumps(reps, indent=1, default=str))
        else:
            print(f"{'task':20} {'blocks':>6} {'ref':>5} {'kill':>9} {'secs':>6} status")
            for r in reps:
                print(f"{r['id']:20} {r['ref_blocks']:>6} {r.get('ref', {}).get('reward', 0):>5.2f} {r.get('killed', 0)}/{r.get('mutants', 0):<5} "
                      f"{r.get('seconds', 0):>6} {'ACCEPT' if r['accepted'] else 'REJECT ' + '; '.join(r['problems'])}")
                if r.get("ref_failures"):
                    print("    ", json.dumps(r["ref_failures"], default=str)[:600])
            print(f"{sum(r['accepted'] for r in reps)}/{len(reps)} accepted")
        return 0 if all(r["accepted"] for r in reps) else 1
    OUT.mkdir(exist_ok=True)
    for plan in build_all():
        if want and plan.id not in want:
            continue
        if a.programs:
            print(f"# {plan.id}\n{L.program(plan.uses, plan.body)}")
            continue
        task = plan.build()
        old = OUT / f"{plan.id}.json"
        if old.exists():
            prev = load_task(old)
            if prev.solution == task.solution:
                task.broken = prev.broken
        print("writing", save_task(task, old), f"({len(task.solution)} blocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
