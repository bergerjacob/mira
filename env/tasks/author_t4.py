"""Authoring script for env/tasks/t4/*.json: T4 tasks (hundreds to a few thousand blocks) whose REFERENCE is a builder program over lib/
components, written against the upgraded `wire_path` (box / avoid / floor_exists / zfirst / taps).

    python env/tasks/author_t4.py t4_addsub8     # (re)generate one task JSON (keeps stored broken variants of an unchanged reference)
    python env/tasks/author_t4.py --list         # task ids and what they are
    python env/tasks/author_t4.py --programs t4_addsub8
    python env/tasks/author_t4.py --validate t4_addsub8 [--write-broken] [--mutants N]   # acceptance gate (env/validate_tasks.evaluate)
    python env/tasks/author_t4.py --oracle t4_addsub8                                    # vanilla 26.2 cross-check -> t4/oracle_report.json
    python env/tasks/author_t4.py --dev t4_addsub8                                       # compile + run public/hidden vectors, print what fails

At least one task id is required (nothing is rewritten by accident). Devices (levers, lamps, buttons) are FIXED scenery of the task:
the reference programs never place them, they hand their positions to wire_path as `avoid` cells, exactly what an agent has to do.
Layout conventions: x east, z south, y=0 is the fixed smooth_stone floor (floor_exists=True), y=1 the first logic level.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import itertools  # noqa: E402

from env import library as L  # noqa: E402
from env.contract import ReferenceModel, TruthTable, Vector  # noqa: E402
from env.task import load_task, save_task  # noqa: E402
from env.tasks.author_t3 import ADDER_OZ, ADDER_PX, ADDER_X0, ADDER_ZD, Plan3, adder_body, set_adder_behaviour, vector_rows  # noqa: E402

OUT = Path(__file__).parent / "t4"


class Plan4(Plan3):
    """Plan3 whose reference body gets a generated header: DEV (device positions), BOX (build box), FIXED, and a `route` helper."""

    def __init__(self, id, title, size, description, notes=""):
        super().__init__(id, title, size, description, notes)
        self.tier = "T4"
        self.dev = {}

    def dev_lever(self, name, pos, doc=""):
        self.dev[name] = tuple(pos)
        super().dev_lever(name, pos, doc)

    def dev_lamp(self, name, pos, doc=""):
        self.dev[name] = tuple(pos)
        super().dev_lamp(name, pos, doc)

    def dev_button(self, name, pos, doc=""):
        self.dev[name] = tuple(pos)
        self.button(name, pos, doc=doc)
        self.levers.append((name, tuple(pos)))

    def program(self, uses, body):
        sx, sy, sz = self.size
        header = (f"DEV = {self.dev!r}\n"
                  f"BOX = (0, 1, 0, {sx - 1}, {sy - 1}, {sz - 1})                 # the build box of the task\n"
                  "FIXED = list(DEV.values())                                      # levers / lamps / buttons: fixed scenery, never written\n\n\n"
                  "def route(b, a, z, every=6, **kw):\n"
                  "    return wire_path(b, a, z, repeat_every=every, box=BOX, avoid=FIXED, floor_exists=True, check_spacing=True, **kw)\n\n\n")
        return super().program(uses, header + body.strip("\n") + "\n")


def model_rows(model_fn, params, ins, outs, combos, public=(), tag=""):
    """Explicit rows whose expectations come from the registered reference model (comb)."""
    from env.models import get_model
    fn = get_model(model_fn)
    rows = []
    for i, combo in enumerate(combos):
        vec = dict(zip(ins, combo))
        got = fn({}, dict(vec), **params)
        rows.append(Vector(vec, {k: got[k] for k in outs}, i in public, f"{tag}{i}"))
    return rows


# =========================================================================== t4_addsub: n-bit adder / subtractor with a mode lever
ADDSUB_PX, ADDSUB_X0, ADDSUB_OZ, ADDSUB_ZD, ADDSUB_ZM = 16, 16, 2, 15, 17


def addsub_body(n):
    return f"""
N = {n}
PX = {ADDSUB_PX}
OX0 = {ADDSUB_X0 + ADDSUB_PX * (n - 1)}
OZ = {ADDSUB_OZ}
ZM = {ADDSUB_ZM}


def build(b):
    fa = b.array(full_adder, n=N, step=(-PX, 0, 0), at=(OX0, 0, OZ), floor=False, name="fa")
    xr = b.array(xor2, n=N, step=(-PX, 0, 0), at=(OX0 + 2, 0, 10), floor=False, name="xr")      # B ^ M in front of every adder cell
    for i in range(N):
        ox = fa[i].at.x
        route(b, DEV["A" + str(i)], P(ox - 4, 1, OZ + 4))                 # A_i: north along x = ox-4, then up onto the west face
        route(b, P(ox - 4, 1, OZ + 4), fa[i]["A"])
        route(b, DEV["B" + str(i)], xr[i]["A"], zfirst=True)              # B_i -> xor2.A (arrives from the west)
        route(b, xr[i]["Y"], fa[i]["B"])                                  # (B_i ^ M) -> adder B (south face)
        route(b, P(ox + 7, 1, ZM), P(ox + 7, 1, 14), 4, zfirst=True)      # M tap: north from the mode bus ...
        route(b, P(ox + 7, 1, 14), xr[i]["B"], 4, zfirst=True)            # ... and onto xor2.B from the east
        route(b, fa[i]["S"], P(ox + 9, 3, OZ + 6))                        # S_i: east along y=3, then down to its lamp
        route(b, P(ox + 9, 3, OZ + 6), DEV["S" + str(i)])
        if i < N - 1:                                                     # carry: COUT_i -> CIN_(i+1) through the gap
            nxt = fa[i + 1]["CIN"]
            route(b, fa[i]["COUT"], P(ox - 4, 3, OZ + 1), 3)
            route(b, P(ox - 4, 3, OZ + 1), nxt, 3)
    taps = [P(fa[i].at.x + 7, 1, ZM) for i in range(N)] + [P(OX0 + 11, 1, ZM)]
    route(b, DEV["M"], P(fa[N - 1].at.x + 7, 1, ZM), 8, taps=taps)        # the mode bus: west along z = ZM, repeater every 8
    route(b, P(OX0 + 11, 1, ZM), P(OX0 + 11, 1, OZ + 1), 6, zfirst=True)  # M is also the carry-in of bit 0
    route(b, P(OX0 + 11, 1, OZ + 1), fa[0]["CIN"])
    route(b, fa[N - 1]["COUT"], DEV["COUT"])
"""


def t4_addsub(n):
    px, x0, oz, zd, zm = ADDSUB_PX, ADDSUB_X0, ADDSUB_OZ, ADDSUB_ZD, ADDSUB_ZM
    ox0 = x0 + px * (n - 1)
    size = (ox0 + 14, 6, zm + 2)
    p = Plan4(f"t4_addsub{n}", f"{n}-bit adder / subtractor", size,
              f"Build a {n}-bit adder/subtractor. Inputs: levers A0..A{n - 1} and B0..B{n - 1} (bit 0 = least significant) and the mode lever M. "
              f"Outputs: lamps S0..S{n - 1} and COUT. With M off the circuit adds: (COUT S{n - 1} .. S0) = A + B. With M on it subtracts: "
              f"S = (A - B) mod 2^{n} (two's complement) and COUT is lit exactly when no borrow is needed, i.e. when A >= B "
              f"(the carry out of A + NOT(B) + 1). The devices are fixed; connect them. "
              "Library hint (component tier only): a full-adder cell and an XOR gate with dust ports are available.")
    for i in range(n):
        ox = ox0 - px * i
        p.dev_lever(f"A{i}", (ox - 4, 1, zd), f"minuend / addend bit {i}")
        p.dev_lever(f"B{i}", (ox, 1, zd), f"subtrahend / addend bit {i}")
        p.dev_lamp(f"S{i}", (ox + 9, 1, zd), f"result bit {i}")
    p.dev_lever("M", (ox0 + 12, 1, zm), "mode: 0 = add, 1 = subtract")
    p.dev_lamp("COUT", (x0 - 7, 1, oz + 2), "carry out (no borrow when subtracting)")
    p.program(["full_adder", "xor2"], addsub_body(n))
    ins = [f"A{i}" for i in range(n)] + [f"B{i}" for i in range(n)] + ["M"]
    outs = [f"S{i}" for i in range(n)] + ["COUT"]
    full, half = (1 << n) - 1, 1 << (n - 1)
    pairs = [(full, 1, 0), (0, 1, 1), (1, 1, 1), (full, full, 1), (half, half, 0), (0, 0, 1), (0x55 & full, 0xAA & full, 1), (0xAA & full, 0x55 & full, 0),
             (full, 0, 1), (half, half - 1, 1), (5 & full, 3 & full, 1), (3 & full, 5 & full, 1)]
    combos = [tuple((a >> k) & 1 for k in range(n)) + tuple((b >> k) & 1 for k in range(n)) + (m,) for a, b, m in pairs]
    rows = model_rows("addsub", {"bits": n}, ins, outs, combos, tag="corner")
    settle = 100 + 30 * n
    p.behaviour.truth_table = TruthTable(rows, settle=settle, shuffle=True, reset_between=False, order_independent=True)
    p.behaviour.reference_model = ReferenceModel("addsub", ins, outs, n_public=6, n_hidden=40, public_seed=71, hidden_seed=72 + n, mode="comb",
                                                 settle=settle, params={"bits": n})
    p.depends = {f"S{k}": [f"A{j}" for j in range(k + 1)] + [f"B{j}" for j in range(k + 1)] + ["M"] for k in range(n)}
    p.depends["COUT"] = list(ins)
    p.budgets.rest = 150 + 20 * n
    p.budgets.total = 120000
    p.budgets.max_blocks = 4000
    return p


# =========================================================================== t4_adder16: the T3 ripple adder at 16 bits (size scaling)
def t4_adder(n):
    px, x0, oz, zd = ADDER_PX, ADDER_X0, ADDER_OZ, ADDER_ZD
    ox0 = x0 + px * (n - 1)
    p = Plan4(f"t4_adder{n}", f"{n}-bit ripple-carry adder", (ox0 + 14, 6, zd + 2),
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
    p.dev_lamp("COUT", (x0 - 7, 1, oz + 2), "carry out")
    p.program(["full_adder"], adder_body(n, px, x0, oz, zd))
    ins = [f"A{i}" for i in range(n)] + [f"B{i}" for i in range(n)] + ["CIN"]
    outs = [f"S{i}" for i in range(n)] + ["COUT"]
    p.behaviour.reference_model = ReferenceModel("adder_cin", ins, outs, n_public=6, n_hidden=40, public_seed=81, hidden_seed=82 + n, mode="comb",
                                                 settle=100 + 30 * n, params={"bits": n})
    set_adder_behaviour(p, n, ins, outs)
    p.behaviour.truth_table.settle = 100 + 30 * n
    p.budgets.rest = 150 + 20 * n
    p.budgets.total = 200000
    p.budgets.max_blocks = 5000
    return p


# =========================================================================== t4_accum: n-bit accumulator ACC <= ACC + IN on every rising clock edge
ACC_PX, ACC_X0, ACC_ZL, ACC_ZC = 26, 8, 15, 17


def accum_body(n):
    return f"""
N = {n}
PX = {ACC_PX}
OX0 = {ACC_X0 + ACC_PX * (n - 1) + 4}
ZR = 17                                                                   # return row (Q back to A), south of the lever row
ZB = 22                                                                   # clock bus, south of the return row


def build(b):
    fa = b.array(full_adder, n=N, step=(-PX, 0, 0), at=(OX0, 0, 2), floor=False, name="fa")
    ff = b.array(d_flipflop, n=N, step=(-PX, 0, 0), at=(OX0 + 11, 0, 12), mirror="z", floor=False, name="ff")    # D / Q on the north row, CLK on the south row
    xo = b.array(crossover, n=N, step=(-PX, 0, 0), at=(OX0 + 13, 0, 19), rot=180, floor=False, name="xo")       # the clock tap crosses the return row
    for i in range(N):
        ox = fa[i].at.x
        route(b, DEV["IN" + str(i)], fa[i]["B"])                                  # IN_i: north along x = ox+4 into the south face of the adder
        route(b, fa[i]["S"], P(ox + 9, 3, 8))                                    # new bit: east along y=3 ...
        route(b, P(ox + 9, 3, 8), ff[i]["D"], zfirst=True)                      # ... then down and east into the flip-flop's D
        route(b, ff[i]["Q"], P(ox + 18, 1, 10))                                  # Q: east, south along x = ox+18, then west along the return row
        route(b, P(ox + 18, 1, 10), P(ox + 18, 1, ZR), zfirst=True)
        route(b, P(ox + 18, 1, ZR), xo[i]["XW"])                                 # into the crossover (X line) ...
        route(b, xo[i]["XE"], P(ox - 4, 1, ZR))                                  # ... out of it and on to the A column
        route(b, P(ox - 4, 1, ZR), P(ox - 4, 1, 6), zfirst=True)                 # north along x = ox-4 ...
        route(b, P(ox - 4, 1, 6), fa[i]["A"])                                    # ... and east into the west face of the adder
        route(b, P(ox + 18, 1, 13), DEV["ACC" + str(i)])                          # display lamp: stub east of the Q column
        route(b, P(ox + 11, 1, ZB), xo[i]["ZN"], 6, zfirst=True)              # clock tap from the bus into the crossover (Z line) ...
        route(b, xo[i]["ZS"], ff[i]["CLK"], 6, zfirst=True)                       # ... and on to CLK
        if i < N - 1:
            route(b, fa[i]["COUT"], P(ox - 4, 3, 3), 3)                           # carry: through the gap, y=3
            route(b, P(ox - 4, 3, 3), fa[i + 1]["CIN"], 3)
    taps = [P(fa[i].at.x + 11, 1, ZB) for i in range(N)]
    route(b, DEV["CLK"], P(fa[0].at.x + 11, 1, ZB), 8, taps=taps)             # the clock bus runs EAST from the high end: high bits are clocked first
"""


def accum_sequences(n, plan, rng_seed, gap=None):
    """Timed sequences generated from the python model: IN values are set 120+ ticks before each rising clock edge."""
    import random
    gap = gap or (110 + 20 * n)
    full = (1 << n) - 1

    def one(name, ins, public):
        ev, exp, t, acc = [], [], 0, 0
        prev = [0] * n
        exp.append(["ACC%d" % i for i in range(n)])
        exp = []
        windows = [(0, 0, 0)]              # (value, t_from, ...) built below
        marks = []
        cur_in = 0
        for v in ins:
            sets = {f"IN{i}": (v >> i) & 1 for i in range(n) if ((v >> i) & 1) != ((cur_in >> i) & 1)}
            if sets:
                ev.append((t, sets))
            cur_in = v
            ev.append((t + gap, {"CLK": 1}))
            ev.append((t + gap + 60, {"CLK": 0}))
            marks.append((t + gap, (acc + v) & full))
            acc = (acc + v) & full
            t += gap + 100
        dur = t + 40
        # expectations: ACC constant between settle after one edge and just before the next one
        edges = [(0, 0)] + marks
        for k, (te, val) in enumerate(edges):
            t1 = te + (0 if k == 0 else 40)
            t2 = (edges[k + 1][0] - 1) if k + 1 < len(edges) else dur - 1
            for i in range(n):
                exp.append((f"ACC{i}", (val >> i) & 1, t1, t2))
        return name, ev, exp, dur, public

    rng = random.Random(rng_seed)
    seqs = [one("count_by_ones", [1, 1, 1, 1, 1, 1, 1, 1, 1, 1][:max(3, min(10, full + 1))], True),
            one("mixed", [rng.randrange(1, full + 1) for _ in range(4)], True)]
    seqs += [one("wrap", [full, 1, 1], False), one("hold_zero", [0, 0, 5 & full, 0], False)]
    for j in range(4):
        seqs.append(one(f"random{j}", [rng.randrange(0, full + 1) for _ in range(5)], False))
    seqs.append(one("big_steps", [full, full, full, 3 & full, full], False))
    for name, ev, exp, dur, public in seqs:
        plan.seq(name, ev, exp, dur, public)


def t4_accum(n):
    px, x0 = ACC_PX, ACC_X0
    ox0 = x0 + px * (n - 1) + 4
    size = (ox0 + 23, 6, 25)
    p = Plan4(f"t4_accum{n}", f"{n}-bit accumulator", size,
              f"Build a {n}-bit accumulator. Levers IN0..IN{n - 1} set an addend (bit 0 = least significant); CLK is the clock lever. Every time CLK is switched ON "
              f"(rising edge) the register is increased by IN: ACC := (ACC + IN) mod 2^{n}; the lamps ACC{n - 1} .. ACC0 show the register as a binary number. "
              f"Switching CLK off changes nothing. The register is 0 at the start (all lamps dark). The value of IN present just before the rising edge is the one that is added "
              f"(IN is never changed within 100 ticks before an edge). "
              "Library hint (component tier only): a full-adder cell, a D flip-flop (captures D on the rising edge of CLK) and a signal crossover are available.")
    p.dev_lever("CLK", (ox0 - px * (n - 1) + 8, 1, 22), "clock")
    for i in range(n):
        ox = ox0 - px * i
        p.dev_lever(f"IN{i}", (ox + 4, 1, ACC_ZL), f"addend bit {i}")
        p.dev_lamp(f"ACC{i}", (ox + 20, 1, 13), f"register bit {i}")
    p.program(["full_adder", "d_flipflop", "crossover"], accum_body(n))
    accum_sequences(n, p, 91 + n)
    p.depends = {f"ACC{j}": ["CLK"] + [f"IN{i}" for i in range(j + 1)] for j in range(n)}
    p.budgets.rest = 300
    p.budgets.total = 400000
    p.budgets.max_blocks = 4000
    return p


# =========================================================================== t4_shift: n-bit serial-in / parallel-out shift register (chain of D flip-flops)
SH_PX, SH_X0, SH_Z0 = 10, 8, 4


def shift_body(n):
    return f"""
N = {n}
PX = {SH_PX}
Z0 = {SH_Z0}


def build(b):
    ff = b.array(d_flipflop, n=N, step=(PX, 0, 0), at=({SH_X0}, 0, Z0), floor=False, name="ff")
    route(b, DEV["DIN"], ff[0]["D"])
    for i in range(N):
        ox = ff[i].at.x
        route(b, ff[i]["Q"], DEV["Q" + str(i)] if i == N - 1 else ff[i + 1]["D"], 6)         # Q_i -> D_(i+1) (the last one feeds its lamp)
        if i < N - 1:
            route(b, P(ox + 7, 1, Z0 + 2), DEV["Q" + str(i)], zfirst=True)                  # lamp branch south of the Q wire
        route(b, P(ox, 1, Z0 - 2), ff[i]["CLK"], 4, zfirst=True)                            # clock tap: down from the bus, then east into CLK
    taps = [P(ff[i].at.x, 1, Z0 - 2) for i in range(N)]
    route(b, DEV["CLK"], P(ff[0].at.x, 1, Z0 - 2), 8, taps=taps)                            # the clock bus runs WEST from the far end: the last place is clocked first (no race-through)
"""


def shift_sequences(n, plan, seed):
    import random
    lat = 2 * (n * SH_PX // 8) + 20                 # clock bus: one repeater (2 ticks) per 8 cells; the first place is clocked last
    period, high = lat + 40, 30

    def one(name, bits, public):
        ev, exp, reg = [], [], [0] * n
        t, dur = 0, 0
        marks = []
        cur = 0
        for k, bit in enumerate(bits):
            te = 20 + period * k
            if bit != cur:
                ev.append((te - 14, {"DIN": bit}))
                cur = bit
            ev.append((te, {"CLK": 1}))
            ev.append((te + high, {"CLK": 0}))
            reg = [bit] + reg[:-1]
            marks.append((te, list(reg)))
        dur = 20 + period * len(bits) + 20
        edges = [(-1, [0] * n)] + marks
        for k, (te, val) in enumerate(edges):
            t1 = 0 if k == 0 else te + lat
            t2 = (edges[k + 1][0] - 1) if k + 1 < len(edges) else dur - 1
            for i in range(n):
                exp.append((f"Q{i}", val[i], t1, t2))
        return name, ev, exp, dur, public

    rng = random.Random(seed)
    seqs = [one("shift_in_1011", [1, 0, 1, 1, 0], True), one("walking_one", [1, 0, 0, 0, 0, 0], True),
            one("fill_with_ones", [1] * (n + 2), False), one("alternating", [k % 2 for k in range(n + 3)], False),
            one("one_then_run_through", [1] + [0] * (n + 1), False), one("random_a", [rng.randrange(2) for _ in range(n + 4)], False),
            one("random_b", [rng.randrange(2) for _ in range(n + 4)], False), one("random_c", [rng.randrange(2) for _ in range(n + 6)], False)]
    for name, ev, exp, dur, public in seqs:
        plan.seq(name, sorted(ev, key=lambda e: e[0]), exp, dur, public)


def t4_shift(n):
    size = (SH_X0 + SH_PX * n + 5, 5, SH_Z0 + 7)
    p = Plan4(f"t4_shift{n}", f"{n}-bit shift register", size,
              f"Build a {n}-bit serial-in parallel-out shift register. DIN is the data lever, CLK the clock lever. On every rising edge of CLK the register shifts by one place "
              f"and takes in the value DIN had just before the edge: Q0 := DIN, Q1 := old Q0, ..., Q{n - 1} := old Q{n - 2}. The lamps Q0..Q{n - 1} show the register "
              f"(Q0 is the first place, nearest to DIN). Switching CLK off changes nothing. All lamps are off at the start. DIN is changed at least 10 ticks before an edge. "
              "Library hint (component tier only): a D flip-flop (captures D on the rising edge of CLK) is available.")
    p.dev_lever("CLK", (SH_X0 + SH_PX * n + 1, 1, SH_Z0 - 2), "clock")
    p.dev_lever("DIN", (SH_X0 - 5, 1, SH_Z0 + 2), "serial data in")
    for i in range(n):
        ox = SH_X0 + SH_PX * i
        p.dev_lamp(f"Q{i}", (ox + 7, 1, SH_Z0 + 4) if i < n - 1 else (ox + 8, 1, SH_Z0 + 2), f"register place {i}")
    p.program(["d_flipflop"], shift_body(n))
    shift_sequences(n, p, 101 + n)
    p.depends = {f"Q{j}": ["CLK", "DIN"] for j in range(n)}
    p.budgets.rest = 300
    p.budgets.total = 400000
    p.budgets.max_blocks = 3000
    return p


# =========================================================================== registry / CLI
BUILDERS = {"t4_addsub8": lambda: t4_addsub(8), "t4_addsub2": lambda: t4_addsub(2),    # t4_addsub2 only for development (not stored)
            "t4_adder16": lambda: t4_adder(16), "t4_accum2": lambda: t4_accum(2), "t4_shift4": lambda: t4_shift(4), "t4_shift16": lambda: t4_shift(16), "t4_shift32": lambda: t4_shift(32), "t4_accum4": lambda: t4_accum(4)}
STORED = ["t4_addsub8", "t4_adder16", "t4_shift32", "t4_accum4"]


def build_all():
    return [BUILDERS[i]() for i in STORED]


def task_ids():
    return sorted(p.stem for p in OUT.glob("t4_*.json"))


def validate_one(args):
    from env import validate_tasks as V
    tid, mutants, write = args
    task = load_task(OUT / f"{tid}.json")
    rep = V.evaluate(task, mutants)
    if write and rep["accepted"]:
        task.broken = [{"name": b["name"], "kind": b["kind"], "edits": b["edits"]} for b in rep["broken_examples"]]
        save_task(task, OUT / f"{tid}.json")
    return rep


def dev_report(tid):
    """Compile the reference and run every vector in the env; print what fails (development aid, writes nothing)."""
    import time
    from env.runner import run
    from env.score import score
    plan = BUILDERS[tid]()
    t0 = time.time()
    task = plan.build()
    t1 = time.time()
    res = run(task.contract, task.solution, seed=0)
    sc = score(task.contract, res)
    print(f"{tid}: {len(task.solution)} blocks, compile {t1 - t0:.1f}s, run {time.time() - t1:.1f}s, reward {sc.reward:.3f} full_pass={sc.full_pass}")
    print("errors:", res.prepared.errors[:5], "place_errors:", [(p, s[-30:], m) for p, s, m in res.place_errors][:3])
    print("invariants failing:", {k: v["detail"] for k, v in res.invariant_failures().items()})
    bad = [v for v in res.vectors if not v.ok]
    print(f"vectors: {len(res.vectors) - len(bad)}/{len(res.vectors)} ok")
    for v in bad[:4]:
        wrong = {k: (v.expect[k], v.got[k]) for k in v.expect if v.expect[k] is not None and v.got.get(k) != v.expect[k]}
        print("  FAIL", {k: x for k, x in v.inputs.items() if x}, "wrong (expected, got):", wrong)
    for q in res.sequences:
        print("  seq", q.name, "ok" if q.ok else [(w.port, w.value, w.t1, w.t2, w.first_bad) for w in q.windows if not w.ok][:4])
    return task, res


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--programs", action="store_true", help="print the reference programs")
    ap.add_argument("--dev", action="store_true", help="compile + run the vectors, print failures (writes nothing)")
    ap.add_argument("--validate", action="store_true", help="acceptance gate on the stored JSONs")
    ap.add_argument("--oracle", action="store_true", help="vanilla 26.2 cross-check of the stored references -> t4/oracle_report.json")
    ap.add_argument("--mutants", type=int, default=24)
    ap.add_argument("--write-broken", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    if a.list:
        for i in STORED:
            print(i)
        return 0
    if not a.ids:
        ap.error("give at least one task id (see --list)")
    if a.dev:
        for tid in a.ids:
            dev_report(tid)
        return 0
    if a.programs:
        for tid in a.ids:
            plan = BUILDERS[tid]()
            plan.build() if False else None
            print(f"# {tid}\n{L.program(plan.uses, plan.body)}")
        return 0
    if a.oracle:
        import json
        import time
        report = {}
        for tid in a.ids:
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
        return 0 if all(r["vanilla_pass"] and r["nucleation_pass"] for r in report.values()) else 1
    if a.validate:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max(1, min(a.jobs, len(a.ids)))) as ex:
            reps = list(ex.map(validate_one, [(t, a.mutants, a.write_broken) for t in a.ids]))
        import json
        print(f"{'task':20} {'blocks':>6} {'ref':>5} {'kill':>9} {'secs':>6} status")
        for r in reps:
            print(f"{r['id']:20} {r['ref_blocks']:>6} {r.get('ref', {}).get('reward', 0):>5.2f} {r.get('killed', 0)}/{r.get('mutants', 0):<5} "
                  f"{r.get('seconds', 0):>6} {'ACCEPT' if r['accepted'] else 'REJECT ' + '; '.join(r['problems'])}")
            if r.get("ref_failures"):
                print("    ", json.dumps(r["ref_failures"], default=str)[:600])
        return 0 if all(r["accepted"] for r in reps) else 1
    OUT.mkdir(exist_ok=True)
    for tid in a.ids:
        plan = BUILDERS[tid]()
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
