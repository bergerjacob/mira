"""Authoring script for env/tasks/t5/*.json: T5 tasks - less repetitive, less library-shaped circuits (FSMs, feedback registers,
arithmetic with fan-out) whose hidden checks are held-out random sequences / vectors, NOT the public table reordered.

    python env/tasks/author_t5.py --list
    python env/tasks/author_t5.py t5_lfsr4                    # (re)generate one task JSON (keeps stored broken variants of an unchanged reference)
    python env/tasks/author_t5.py --dev t5_lfsr4              # compile + run, print failures (writes nothing)
    python env/tasks/author_t5.py --programs t5_lfsr4
    python env/tasks/author_t5.py --validate t5_lfsr4 --write-broken --mutants 16
    python env/tasks/author_t5.py --oracle t5_lfsr4           # vanilla 26.2, ONE JVM PER CAPTURE -> t5/oracle_report.json

References are builder programs over lib/ parts (same conventions as author_t4.py: devices are fixed scenery, `route` = wire_path with
box / avoid / floor_exists / check_spacing). Behaviour comes from python models in this file (registered in env/models.py style).
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import random  # noqa: E402

from env import library as L  # noqa: E402
from env.contract import TruthTable, Vector  # noqa: E402
from env.models import model  # noqa: E402
from env.task import load_task, save_task  # noqa: E402
from env.tasks.author_t4 import Plan4  # noqa: E402

OUT = Path(__file__).parent / "t5"


class Plan5(Plan4):
    def __init__(self, id, title, size, description, notes=""):
        super().__init__(id, title, size, description, notes)
        self.tier = "T5"


# =========================================================================== python models (state machines stepped per clock edge / press)
@model("lfsr_xnor")
def lfsr_xnor(s, i, bits=4, taps=(2, 3)):
    """One rising clock edge of a Fibonacci LFSR with XNOR feedback: Q0 := XNOR(Q[taps]), Qk := Q(k-1). Start = all zero."""
    q = s.setdefault("q", [0] * bits)
    if i.get("CLK") and not s.get("clk"):
        fb = 1 - (q[taps[0]] ^ q[taps[1]])
        s["q"] = q = [fb] + q[:-1]
    s["clk"] = i.get("CLK", 0)
    return {f"Q{k}": q[k] for k in range(bits)}


def clocked_sequences(plan, seqs, outs, step, clk="CLK", lat=30):
    """seqs: [(name, [(t_edge, high_ticks, {port: value set 14+ ticks before the edge}), ...], public)]. `step(state, sets)` advances the
    model by one rising edge and returns {out: value}. Expectations: every output holds its model value from edge+lat to the next edge."""
    for name, edges, public in seqs:
        ev, exp, state = [], [], {}
        marks = [(0, step(state, None))]
        for te, high, sets in edges:
            if sets:
                ev.append((te - 14, dict(sets)))
            ev.append((te, {clk: 1}))
            ev.append((te + high, {clk: 0}))
            marks.append((te + lat, step(state, sets or {})))
        dur = edges[-1][0] + edges[-1][1] + 60
        for k, (t1, vals) in enumerate(marks):
            t2 = (edges[k][0] - 1) if k < len(edges) else dur - 1
            for o in outs:
                exp.append((o, vals[o], t1, t2))
        plan.seq(name, sorted(ev, key=lambda e: e[0]), exp, dur, public)


# =========================================================================== t5_lfsr4: 4-bit XNOR LFSR (feedback register, period 15)
LF_PX, LF_X0, LF_Z0 = 10, 8, 4


def lfsr_body():
    return f"""
N = 4
PX = {LF_PX}
X0 = {LF_X0}
Z0 = {LF_Z0}
ZF = 15                                                                   # feedback row (south of the lamps)


def build(b):
    ff = b.array(d_flipflop, n=N, step=(PX, 0, 0), at=(X0, 0, Z0), floor=False, name="ff")
    for i in range(N):
        ox = ff[i].at.x
        route(b, ff[i]["Q"], DEV["Q" + str(i)] if i == N - 1 else ff[i + 1]["D"], 6)          # Q_i -> D_(i+1)
        if i < N - 1:
            route(b, P(ox + 7, 1, Z0 + 2), DEV["Q" + str(i)], zfirst=True)                   # lamp stub
        route(b, P(ox, 1, Z0 - 2), ff[i]["CLK"], 4, zfirst=True)                             # clock tap
    taps = [P(ff[i].at.x, 1, Z0 - 2) for i in range(N)]
    route(b, DEV["CLK"], P(ff[0].at.x, 1, Z0 - 2), 8, taps=taps)                             # clock bus runs WEST: last place clocked first
    x2, x3 = ff[2].at.x, ff[3].at.x
    xr = b.place(xor2, at=(x3 + 4, 0, 13), rot=180, floor=False, name="xr")                  # A east, B west, Y south
    route(b, ff[3]["Q"], P(x3 + 6, 1, xr["A"].z), zfirst=True)                               # Q3 tap at the Q port (a tap on the lamp
    route(b, P(x3 + 6, 1, xr["A"].z), xr["A"])                                               # stub would turn it into an unpowering corner)
    route(b, P(x2 + 9, 1, Z0 + 2), xr["B"], zfirst=True)                                    # Q2 tap -> xor B (from the west)
    nt = b.place(not_gate, at=(xr["Y"].x - 3, 0, ZF), rot=180, floor=False, name="inv")      # XNOR = NOT(xor); flows west
    route(b, xr["Y"], nt["A"], zfirst=True)
    route(b, nt["Y"], P(X0 - 3, 1, ZF), 8)                                                   # feedback west along ZF ...
    route(b, P(X0 - 3, 1, ZF), P(X0 - 3, 1, Z0 + 2), 8, zfirst=True)                         # ... north ...
    route(b, P(X0 - 3, 1, Z0 + 2), ff[0]["D"])                                               # ... and east into D0
"""


def t5_lfsr4():
    n = 4
    size = (LF_X0 + LF_PX * n + 5, 5, 18)
    p = Plan5("t5_lfsr4", "4-bit LFSR (XNOR feedback)", size,
              "Build a 4-bit linear feedback shift register. CLK is the clock lever; the lamps Q0..Q3 show the register (all off at the start). "
              "On every rising edge of CLK (switched ON) the register shifts by one place and the new Q0 is the XNOR of the old Q2 and Q3: "
              "Q0 := NOT(Q2 XOR Q3), Q1 := old Q0, Q2 := old Q1, Q3 := old Q2. Switching CLK off changes nothing. From all-off the register "
              "runs through 15 different states and then repeats. CLK stays on and off for at least 20 ticks each. "
              "Library hint (component tier only): D flip-flop, XOR gate and NOT gate cells are available.")
    p.dev_lever("CLK", (LF_X0 + LF_PX * n + 1, 1, LF_Z0 - 2), "clock")
    for i in range(n):
        ox = LF_X0 + LF_PX * i
        p.dev_lamp(f"Q{i}", (ox + 7, 1, LF_Z0 + 4) if i < n - 1 else (ox + 8, 1, LF_Z0 + 2), f"register place {i}")
    p.program(["d_flipflop", "xor2", "not_gate"], lfsr_body())

    def step(state, sets):
        if sets is not None:
            lfsr_xnor(state, {"CLK": 0})
            return lfsr_xnor(state, {"CLK": 1})
        return lfsr_xnor(state, {"CLK": 0})

    rng = random.Random(4115)

    def stream(k, period=(70, 70), high=(30, 30)):
        t, out = 20, []
        for _ in range(k):
            out.append((t, rng.randint(*high), None))
            t += rng.randint(*period)
        return out
    seqs = [("first_6", stream(6), True), ("steady_4", stream(4), True),
            ("full_period_17", stream(17, (60, 90), (20, 40)), False), ("random_a", stream(9, (60, 100), (20, 45)), False),
            ("random_b", stream(12, (60, 80), (20, 35)), False), ("long_holds", stream(5, (150, 260), (60, 120)), False)]
    clocked_sequences(p, seqs, [f"Q{i}" for i in range(n)], step, lat=40)
    p.depends = {f"Q{j}": ["CLK"] for j in range(n)}
    p.budgets.rest = 300
    p.budgets.total = 60000
    p.budgets.max_blocks = 1500
    return p


# =========================================================================== t5_lock4: combination lock FSM (buttons L/R, code L R R L)
LOCK_CODE = "LRRL"
LOCK_DELAY = 8                                   # clock delay line (ticks): edge must land inside every stage's D window
LK_Z0, LK_PX, LK_X0 = 4, 20, 6                    # chain row z = 4..6; stage k dff at x = LK_X0 + 20k (stage 0 has no AND)


@model("lock4")
def lock4(s, i, code=LOCK_CODE):
    """One button press (i = {"L": 1} or {"R": 1}): advance on the next code symbol, else restart (1 if the press is code[0], else 0)."""
    k = s.get("k", 0)
    x = "L" if i.get("L") else "R" if i.get("R") else None
    if x is not None:
        k = k + 1 if k < len(code) and x == code[k] else int(x == code[0])
    s["k"] = k
    return {"OPEN": int(k == len(code))}


def lock_body():
    return f"""
CODE = {LOCK_CODE!r}
LOCK_DELAY = {LOCK_DELAY}
Z0 = {LK_Z0}
PX = {LK_PX}
X0 = {LK_X0}
ZL, ZR, ZC = 10, 15, 1                                                    # L bus, R bus (south), clock bus (north)


def build(b):
    dx = [X0 + PX * k for k in range(4)]                                  # dff k at dx[k]; and2 k (k >= 1) at dx[k] - 9
    ff = [b.place(d_flipflop, at=(dx[k], 0, Z0), floor=False, name="ff" + str(k)) for k in range(4)]
    ga = [None] + [b.place(and2, at=(dx[k] - 9, 0, Z0), floor=False, name="and" + str(k)) for k in range(1, 4)]
    ltaps, rtaps, xo = [], [], {{}}
    for k in range(1, 4):
        a = ga[k]
        route(b, a["Y"], P(a["Y"].x + 1, 1, Z0 + 1))                      # AND -> D: east, south, east
        route(b, P(a["Y"].x + 1, 1, Z0 + 1), ff[k]["D"], zfirst=True)
        q = ff[k - 1]["Q"]
        route(b, q, P(q.x + 2, 1, q.z))                                   # q_(k-1) -> the AND's north input
        route(b, P(q.x + 2, 1, q.z), a["A"], zfirst=True)
        xt = a["B"].x - 1                                                 # button tap column, enters the AND's south input
        if CODE[k] == "L":
            ltaps.append(P(xt, 1, ZL))
            route(b, P(xt, 1, ZL), a["B"], zfirst=True)
        else:                                                             # R tap crosses the L bus
            xo[k] = b.place(crossover, at=(xt - 2, 0, ZL + 2), mirror="z", floor=False, name="xo" + str(k))
            rtaps.append(P(xt, 1, ZR))
            route(b, P(xt, 1, ZR), xo[k]["ZN"], zfirst=True)
            route(b, xo[k]["ZS"], a["B"], zfirst=True)
    ltaps.append(P(dx[0], 1, ZL))                                         # stage 0: the first code button into D0, 8 ticks late so that
    d0 = b.place(delay_line, at=(dx[0], 0, ZL), rot=270, floor=False, name="d0", ticks=8)   # the (late) stage-0 clock edge lands mid-window
    route(b, d0["Y"], ff[0]["D"], zfirst=True)
    route(b, ff[3]["Q"], DEV["OPEN"])
    # L bus: from the button west, through the crossovers' X lines
    xs = sorted(xo, reverse=True)
    pts = [DEV["L"]] + [p for k in xs for p in (xo[k]["XE"], xo[k]["XW"])] + [P(dx[0], 1, ZL)]
    for a_, z_ in zip(pts[0::2], pts[1::2]):
        route(b, a_, z_, 6, taps=ltaps)
    route(b, DEV["R"], min(rtaps), 6, taps=rtaps)
    # clock: L or R -> delay -> north -> bus running WEST (stage 3 is clocked first)
    xb = DEV["L"][0]
    g = b.place(or2, at=(xb + 5, 0, ZL + 2), floor=False, name="or")
    route(b, P(xb + 1, 1, ZL), P(xb + 3, 1, ZL))
    route(b, P(xb + 3, 1, ZL), g["A"], zfirst=True)
    route(b, P(xb + 1, 1, ZR), P(xb + 2, 1, ZR))
    route(b, P(xb + 2, 1, ZR), g["B"], zfirst=True)
    dl = b.place(delay_line, at=(g["Y"].x + 1, 0, g["Y"].z), floor=False, name="dl", ticks=LOCK_DELAY)
    route(b, g["Y"], dl["A"])
    xc = dl["Y"].x + 2
    route(b, dl["Y"], P(xc, 1, dl["Y"].z))
    route(b, P(xc, 1, dl["Y"].z), P(xc, 1, ZC), 6, zfirst=True)
    ctaps = [P(d, 1, ZC) for d in dx]
    route(b, P(xc, 1, ZC), P(dx[0], 1, ZC), 8, taps=ctaps)
    for k in range(4):
        route(b, P(dx[k], 1, ZC), ff[k]["CLK"], 4, zfirst=True)
"""


def t5_lock4():
    dx3 = LK_X0 + 3 * LK_PX
    xb = dx3 + 12
    size = (xb + 22, 6, 18)
    p = Plan5("t5_lock4", "combination lock (4-press code)", size,
              "Build a combination lock with two buttons, L and R, and one lamp OPEN. The secret code is L, R, R, L. Every button press either "
              "continues the code (the lock advances one step) or cancels the attempt; a cancelled attempt restarts immediately when the press was "
              "L (the first symbol of the code), i.e. after a wrong press the lock is at step 1 if that press was L, else at step 0. After the fourth "
              "correct press in a row the lamp OPEN lights and stays lit until the next press (which cancels / restarts as above). The lamp is off at the "
              "start. Presses are at least 70 ticks apart; a press holds the button for 20 ticks. "
              "Library hint (component tier only): D flip-flop, AND, OR, delay line and crossover cells are available.")
    p.dev_lamp("OPEN", (dx3 + 8, 1, LK_Z0 + 2), "lit = unlocked")
    p.dev_button("L", (xb, 1, 10), "code button L")
    p.dev_button("R", (xb, 1, 15), "code button R")
    p.program(["d_flipflop", "and2", "or2", "delay_line", "crossover"], lock_body())

    def presses(word, gap=(80, 80)):
        t, ev = 20, []
        for ch in word:
            ev.append((t, ch))
            t += rng.randint(*gap)
        return ev
    rng = random.Random(4004)

    def seq(name, word, public, gap=(80, 80), lat=60):
        ev = presses(word, gap)
        state = {}
        exp, prev = [], (0, lock4(state, {})["OPEN"])
        marks = [prev]
        for t, ch in ev:
            marks.append((t + lat, lock4(state, {ch: 1})["OPEN"]))
        dur = ev[-1][0] + 120
        for k, (t1, v) in enumerate(marks):
            t2 = ev[k][0] - 1 if k < len(ev) else dur - 1
            exp.append(("OPEN", v, t1, t2))
        p.seq(name, [(t, {ch: 1}) for t, ch in ev], exp, dur, public)
    seq("correct_code", "LRRL", True)
    seq("wrong_then_correct", "LRLLRRLR", True)
    seq("restart_rule", "RLRRRLLRRL", False, (75, 110))
    seq("open_then_close", "LRRLRLRRLL", False, (75, 110))
    seq("near_misses", "LRRRLRLLRRRL", False, (75, 110))
    for j in range(3):
        word = "".join(rng.choice("LR") for _ in range(rng.randint(3, 6))) + "LRRL" + "".join(rng.choice("LR") for _ in range(rng.randint(0, 3)))
        seq(f"random{j}", word, False, (75, 120))
    p.depends = {"OPEN": ["L", "R"]}
    p.budgets.rest = 300
    p.budgets.total = 60000
    p.budgets.max_blocks = 2000
    return p


# =========================================================================== combinational helpers: public rows + held-out rows + random multi-input stream
def comb_rows(fn, ins, outs, n_public=6, n_stream=24, seed=0):
    """Exhaustive table split into public rows (a seeded subset showing both values of every output) and HIDDEN rows that were never
    published, plus a seeded random stream of `n_stream` hidden vectors (several inputs change at once). fn(**inputs) -> {out: v}."""
    import itertools
    rng = random.Random(seed)
    combos = list(itertools.product((0, 1), repeat=len(ins)))
    res = {c: fn(**dict(zip(ins, c))) for c in combos}
    order = combos[:]
    rng.shuffle(order)
    public = set(order[:n_public])
    for o in outs:                                       # every output shown in both states
        for val in (0, 1):
            if not any(res[c][o] == val for c in public):
                public.add(next(c for c in order if res[c][o] == val))
    rows = [Vector(dict(zip(ins, c)), {o: res[c][o] for o in outs}, c in public, "".join(map(str, c))) for c in combos]
    for k in range(n_stream):
        c = rng.choice(combos)
        rows.append(Vector(dict(zip(ins, c)), {o: res[c][o] for o in outs}, False, f"rnd{k}"))
    return rows


# =========================================================================== t5_mul2x2: 2x2-bit multiplier
@model("mul2x2")
def mul2x2(s, i):
    p = (i["A0"] + 2 * i["A1"]) * (i["B0"] + 2 * i["B1"])
    return {f"P{k}": (p >> k) & 1 for k in range(4)}


MU_A, MU_ZL = 6, 32                             # lever row: A0, B0, A1, B1 at x = a, a+2, a+4, a+6, z = ZL


def mul_body():
    return f"""
a = {MU_A}
ZL = {MU_ZL}
ZG = ZL + 28                                    # south AND gate (A0 B1) input row
ZX = ZL + 11                                    # xor row
ZP, ZQ = 20, 9                                  # north AND gates (P3, P2) input rows


def build(b):
    # every lever feeds one gate on its north side and one on its south side: north A0B0, A1B1; south B0A1, A0B1
    g00 = b.place(and2, at=(a, 0, ZL - 1), rot=270, floor=False, name="g00")          # inputs from the south, output north
    g11 = b.place(and2, at=(a + 4, 0, ZL - 1), rot=270, floor=False, name="g11")
    g10 = b.place(and2, at=(a + 4, 0, ZL + 1), rot=90, floor=False, name="g10")       # inputs from the north, output south
    g01 = b.place(and2, at=(a + 2, 0, ZG), rot=270, floor=False, name="g01")
    route(b, DEV["A0"], P(a - 2, 1, ZL))                                             # A0 and B1 go round the south gate
    route(b, P(a - 2, 1, ZL), P(a - 2, 1, ZG + 2), zfirst=True)
    route(b, P(a - 2, 1, ZG + 2), P(a + 2, 1, ZG + 2))
    route(b, P(a + 2, 1, ZG + 2), g01["A"], zfirst=True)
    route(b, DEV["B1"], P(a + 8, 1, ZL))
    route(b, P(a + 8, 1, ZL), P(a + 8, 1, ZG + 2), zfirst=True)
    route(b, P(a + 8, 1, ZG + 2), P(a + 4, 1, ZG + 2))
    route(b, P(a + 4, 1, ZG + 2), g01["B"], zfirst=True)
    # P1 = (A1 B0) xor (A0 B1); its lamp sits inside the A0 / B1 loop
    xr = b.place(xor2, at=(a + 4, 0, ZX), rot=90, floor=False, name="xr")             # A from the north, B from the south, Y east
    route(b, g10["Y"], xr["A"], zfirst=True)
    route(b, g01["Y"], xr["B"])
    # north: P0 = A0 B0, P3 = P0 and A1B1, P2 = A1B1 and not P0
    c1, c2 = a + 1, a + 7
    route(b, g11["Y"], P(a + 5, 1, ZL - 9), zfirst=True)
    route(b, P(a + 5, 1, ZL - 9), P(c2, 1, ZL - 9))
    route(b, P(c2, 1, ZL - 9), P(c2, 1, ZQ + 1), zfirst=True)
    route(b, g00["Y"], P(c1, 1, ZP + 1), zfirst=True)
    route(b, P(c1, 1, ZL - 9), DEV["P0"])
    g3 = b.place(and2, at=(c1 + 2, 0, ZP), rot=270, floor=False, name="g3")
    route(b, P(c1, 1, ZP + 1), g3["A"])
    route(b, P(c2, 1, ZP + 1), g3["B"])
    route(b, g3["Y"], DEV["P3"])
    nt = b.place(not_gate, at=(c1, 0, ZP - 2), rot=270, floor=False, name="inv")
    route(b, P(c1, 1, ZP + 1), nt["A"], zfirst=True)
    route(b, nt["Y"], P(c1, 1, ZQ + 1), zfirst=True)
    g2 = b.place(and2, at=(c1 + 2, 0, ZQ), rot=270, floor=False, name="g2")
    route(b, P(c1, 1, ZQ + 1), g2["A"])
    route(b, P(c2, 1, ZQ + 1), g2["B"])
    route(b, g2["Y"], DEV["P2"])
"""


def t5_mul2x2():
    a, zl = MU_A, MU_ZL
    p = Plan5("t5_mul2x2", "2x2-bit multiplier", (a + 11, 6, zl + 32),
              "Build a 2-bit by 2-bit multiplier. Levers A1 A0 and B1 B0 are two 2-bit numbers (bit 0 = least significant); the lamps "
              "P3 P2 P1 P0 show the 4-bit product A * B. Note where the lamps are: P1 sits south of the lever row, between the inputs. "
              "Library hint (component tier only): AND, XOR and NOT cells are available.")
    for k, n in enumerate(["A0", "B0", "A1", "B1"]):
        p.dev_lever(n, (a + 2 * k, 1, zl), f"input {n}")
    p.dev_lamp("P0", (a - 2, 1, zl - 9), "product bit 0")
    p.dev_lamp("P1", (a + 5, 2, zl + 11 + 2), "product bit 1 (floating lamp at y=2)")
    p.dev_lamp("P2", (a + 4, 1, 1), "product bit 2")
    p.dev_lamp("P3", (a + 4, 1, 12), "product bit 3")
    p.program(["and2", "xor2", "not_gate"], mul_body())
    ins, outs = ["A0", "A1", "B0", "B1"], ["P0", "P1", "P2", "P3"]
    rows = comb_rows(lambda **v: mul2x2({}, v), ins, outs, n_public=6, n_stream=24, seed=2202)
    p.behaviour.truth_table = TruthTable(rows, settle=150, shuffle=True, reset_between=False, order_independent=True)
    p.budgets.rest = 200
    p.budgets.total = 40000
    p.budgets.max_blocks = 1500
    return p


# =========================================================================== t5_seq_detector: overlapping serial pattern detector (last four bits 1011)
SD_PATTERN = (1, 0, 1, 1)                        # oldest .. newest
SD_PX, SD_X0, SD_Z0, SD_ZI = 10, 8, 4, 9


@model("seq_detector")
def seq_detector(s, i, pattern=SD_PATTERN):
    """One rising clock edge: shift DIN into the 4-bit history; MATCH = the last four bits (oldest first) equal `pattern`."""
    h = s.setdefault("h", [0] * len(pattern))
    if i.get("CLK") and not s.get("clk"):
        s["h"] = h = h[1:] + [i.get("DIN", 0)]
    s["clk"] = i.get("CLK", 0)
    return {"MATCH": int(tuple(h) == tuple(pattern))}


def seqdet_body():
    return f"""
N = 4
PAT = {SD_PATTERN!r}                                                      # oldest .. newest = Q3 .. Q0
PX = {SD_PX}
X0 = {SD_X0}
Z0 = {SD_Z0}
ZI = {SD_ZI}                                                              # inverter row; the OR line runs along ZI + 4


def build(b):
    ff = b.array(d_flipflop, n=N, step=(PX, 0, 0), at=(X0, 0, Z0), floor=False, name="ff")
    route(b, DEV["DIN"], ff[0]["D"])
    for i in range(N):
        ox = ff[i].at.x
        if i < N - 1:
            route(b, ff[i]["Q"], ff[i + 1]["D"], 6)                        # Q_i -> D_(i+1)
        route(b, P(ox, 1, Z0 - 2), ff[i]["CLK"], 4, zfirst=True)          # clock tap
    route(b, ff[N - 1]["Q"], P(ff[N - 1].at.x + 7, 1, Z0 + 2))
    taps = [P(ff[i].at.x, 1, Z0 - 2) for i in range(N)]
    route(b, DEV["CLK"], P(ff[0].at.x, 1, Z0 - 2), 8, taps=taps)          # clock bus runs WEST: last place clocked first
    # MATCH = NOR over the places that differ from the pattern: !Q where the bit must be 1, Q (through a diode) where it must be 0
    js = []
    for i in range(N):
        tx = ff[i].at.x + 7
        route(b, P(tx, 1, Z0 + 2), P(tx, 1, ZI), zfirst=True)
        if PAT[N - 1 - i]:
            inv = b.place(not_gate, at=(tx, 0, ZI), rot=90, floor=False, name="inv" + str(i))   # from the north, out south
            out = inv["Y"]
        else:
            b.set(P(tx, 1, ZI + 1), solid())
            b.set(P(tx, 1, ZI + 2), repeater(flow=SOUTH, delay=1))
            out = P(tx, 1, ZI + 3)
            b.set(out, wire())
        route(b, out, P(tx, 1, ZI + 4))
        js.append(P(tx, 1, ZI + 4))
    xe = js[-1].x + 4
    for a_, z_ in zip(js, js[1:] + [P(xe, 1, ZI + 4)]):
        route(b, a_, z_, 8)                                               # the OR line, repeaters flow east
    nt = b.place(not_gate, at=(xe, 0, ZI + 4), floor=False, name="out")
    route(b, nt["Y"], DEV["MATCH"])
"""


def t5_seq_detector():
    xe = SD_X0 + SD_PX * 3 + 7 + 4
    p = Plan5("t5_seq_detector", "serial pattern detector (1011, overlapping)", (xe + 7, 5, SD_ZI + 7),
              "Build a serial pattern detector. DIN is the data lever and CLK the clock lever. On every rising edge of CLK (switched ON) the "
              "circuit takes in the value DIN had just before the edge. The lamp MATCH is lit exactly while the last four bits taken in were "
              "1, 0, 1, 1 (oldest first; overlapping matches count: 1011011 matches twice). Before four bits have been taken in, the missing "
              "older bits count as 0, so MATCH is off at the start. Switching CLK off changes nothing. DIN is changed at least 14 ticks before "
              "an edge; CLK stays on and off for at least 20 ticks each. "
              "Library hint (component tier only): D flip-flop and NOT gate cells are available.")
    p.dev_lever("CLK", (SD_X0 + SD_PX * 4 + 1, 1, SD_Z0 - 2), "clock")
    p.dev_lever("DIN", (SD_X0 - 5, 1, SD_Z0 + 2), "serial data in")
    p.dev_lamp("MATCH", (xe + 4, 1, SD_ZI + 4), "lit while the last four bits are 1011")
    p.program(["d_flipflop", "not_gate"], seqdet_body())

    def step(state, sets):
        if sets is None:
            return seq_detector(state, {"CLK": 0})
        state["din"] = sets.get("DIN", state.get("din", 0))
        seq_detector(state, {"CLK": 0, "DIN": state["din"]})
        return seq_detector(state, {"CLK": 1, "DIN": state["din"]})

    rng = random.Random(1011)

    def stream(bits, period=(70, 70), high=(30, 30)):
        t, out, cur = 20, [], 0
        for bit in bits:
            out.append((t, rng.randint(*high), {"DIN": bit} if bit != cur else {}))
            cur = bit
            t += rng.randint(*period)
        return out
    seqs = [("one_match", stream([1, 0, 1, 1, 0]), True), ("overlap", stream([1, 0, 1, 1, 0, 1, 1]), True),
            ("near_misses", stream([1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1], (60, 90), (20, 40)), False),
            ("ones", stream([1] * 7 + [0, 1, 1], (60, 90), (20, 40)), False)]
    for j in range(4):
        bits = [rng.randrange(2) for _ in range(10)]
        k = rng.randrange(0, 7)
        bits[k:k] = [1, 0, 1, 1]                                          # every random stream contains at least one match
        seqs.append((f"random{j}", stream(bits, (60, 100), (20, 45)), False))
    clocked_sequences(p, seqs, ["MATCH"], step, lat=40)
    p.depends = {"MATCH": ["CLK", "DIN"]}
    p.budgets.rest = 300
    p.budgets.total = 60000
    p.budgets.max_blocks = 1500
    return p


# =========================================================================== registry / CLI
BUILDERS = {"t5_lfsr4": t5_lfsr4, "t5_lock4": t5_lock4, "t5_mul2x2": t5_mul2x2, "t5_seq_detector": t5_seq_detector}
STORED = ["t5_lfsr4", "t5_lock4", "t5_mul2x2", "t5_seq_detector"]


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
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--programs", action="store_true")
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--oracle", action="store_true", help="vanilla 26.2, one JVM per capture -> t5/oracle_report.json")
    ap.add_argument("--mutants", type=int, default=24)
    ap.add_argument("--write-broken", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    if a.list:
        print("\n".join(STORED))
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
            print(f"# {tid}\n{L.program(plan.uses, plan.body)}")
        return 0
    if a.oracle:
        import time
        from scripts.t4_oracle import capture_many_isolated
        from tools.oracle import gametest
        gametest.capture_many = lambda jobs, workers=4, **kw: capture_many_isolated(jobs, a.jobs, **kw)
        path = OUT / "oracle_report.json"
        ok = True
        for tid in a.ids:
            t0 = time.time()
            r = L.oracle_task(load_task(OUT / f"{tid}.json"), workers=a.jobs)
            r["seconds"] = round(time.time() - t0, 1)
            r["jvm_per_capture"] = True
            prev = json.loads(path.read_text()) if path.exists() else {}
            prev[tid] = r
            path.write_text(json.dumps(prev, indent=1, sort_keys=True) + "\n")
            ok &= bool(r["vanilla_pass"] and r["nucleation_pass"])
            print(f"{tid:16} vanilla={'pass' if r['vanilla_pass'] else 'FAIL ' + str(r['vanilla_failures'][:3])} nucleation={'pass' if r['nucleation_pass'] else 'FAIL'} "
                  f"tick-identical={r['agree']}/{r['captures']} hold={r['hold']} vectors={r['vectors']} {r['seconds']}s", flush=True)
        return 0 if ok else 1
    if a.validate:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max(1, min(a.jobs, len(a.ids)))) as ex:
            reps = list(ex.map(validate_one, [(t, a.mutants, a.write_broken) for t in a.ids]))
        print(f"{'task':16} {'blocks':>6} {'ref':>5} {'kill':>9} {'secs':>6} status")
        for r in reps:
            print(f"{r['id']:16} {r['ref_blocks']:>6} {r.get('ref', {}).get('reward', 0):>5.2f} {r.get('killed', 0)}/{r.get('mutants', 0):<5} "
                  f"{r.get('seconds', 0):>6} {'ACCEPT' if r['accepted'] else 'REJECT ' + '; '.join(r['problems'])}  survivors={r.get('survivors')}")
            if r.get("ref_failures"):
                print("    ", json.dumps(r["ref_failures"], default=str)[:600])
        return 0 if all(r["accepted"] for r in reps) else 1
    OUT.mkdir(exist_ok=True)
    for tid in a.ids:
        task = BUILDERS[tid]().build()
        old = OUT / f"{tid}.json"
        if old.exists():
            prev = load_task(old)
            if prev.solution == task.solution:
                task.broken = prev.broken
        print("writing", save_task(task, old), f"({len(task.solution)} blocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
