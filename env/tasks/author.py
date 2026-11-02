"""Authoring script for env/tasks/*.json: the single source of the task specs and reference solutions.

    python env/tasks/author.py            # regenerate all tasks
    python env/tasks/author.py t0_not     # regenerate one

Layout conventions: x east, z south, y up; y=0 is a fixed smooth_stone floor. Block lists come from ASCII
layers (rows = z, columns = x). Repeater facing is the INPUT side, so '>' (flows east) is facing=west.
Specs (ports, vectors, sequences) are written here by the author, never by the model under test.
"""
import itertools
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from env.contract import (DEFAULT_FORBIDDEN, Behaviour, Budgets, Contract, Event, Expect, Invariants, Port, Sequence,
                          TruthTable, Vector)
from env.task import Task, load_task, reference_stats, save_task

STONE = "minecraft:stone"
WIRE = "minecraft:redstone_wire"
LEVER = "minecraft:lever[face=floor,facing=east,powered=false]"
LAMP = "minecraft:redstone_lamp[lit=false]"
BASE = {
    "+": WIRE, "#": STONE, ".": None, " ": None,
    "e": "minecraft:redstone_wall_torch[facing=east]", "w": "minecraft:redstone_wall_torch[facing=west]",
    "n": "minecraft:redstone_wall_torch[facing=north]", "s": "minecraft:redstone_wall_torch[facing=south]",
    "T": "minecraft:redstone_torch",
    ">": "minecraft:repeater[facing=west,delay=1]", "<": "minecraft:repeater[facing=east,delay=1]",
    "v": "minecraft:repeater[facing=north,delay=1]", "^": "minecraft:repeater[facing=south,delay=1]",
    "R": "minecraft:redstone_block", "G": "minecraft:glass",
}


def rep(flow, delay=1):
    face = {"east": "west", "west": "east", "south": "north", "north": "south"}[flow]
    return f"minecraft:repeater[facing={face},delay={delay}]"


def comp(flow, subtract=True):
    face = {"east": "west", "west": "east", "south": "north", "north": "south"}[flow]
    return f"minecraft:comparator[facing={face},mode={'subtract' if subtract else 'compare'}]"


def ascii_layer(y, rows, pal=None, x0=0, z0=0):
    """rows[z][x] chars -> [[x,y,z,state]]; pal overrides/extends BASE."""
    pal = {**BASE, **(pal or {})}
    out = []
    for dz, row in enumerate(rows):
        for dx, ch in enumerate(row):
            st = pal.get(ch)
            if ch not in pal:
                raise ValueError(f"unknown palette char {ch!r}")
            if st:
                out.append([x0 + dx, y, z0 + dz, st])
    return out


class Plan:
    """A task under construction: floor + fixed devices + ports + behaviour + reference solution."""

    def __init__(self, id, title, tier, size, description, notes=""):
        self.id, self.title, self.tier, self.notes = id, title, tier, notes
        self.size = size
        self.desc = description
        self.fixed = [{"box": [0, 0, 0, size[0] - 1, 0, size[2] - 1], "state": "minecraft:smooth_stone"}]
        self.ports = []
        self.solution = []
        self.behaviour = Behaviour()
        self.invariants = Invariants()
        self.budgets = Budgets()
        self.depends = {}
        self.build_box = None
        self.broken = []
        self.bbox_override = self.build_box_override = None
        self.keep_clear, self.no_write, self.forbidden_extra = [], [], []

    def fix(self, pos, state, pos2=None):
        self.fixed.append({"box": [*pos, *(pos2 or pos)], "state": state})

    def lever(self, name, pos, doc="", facing="east"):
        self.fix(pos, LEVER.replace("east", facing))
        self.ports.append(Port(name, "click", tuple(pos), facing=facing, doc=doc or "lever (click toggles)"))

    def button(self, name, pos, state="minecraft:stone_button[face=floor,facing=east,powered=false]", doc=""):
        self.fix(pos, state)
        self.ports.append(Port(name, "click", tuple(pos), pulse=True, doc=doc or "button (click presses for 20 ticks)"))

    def pad(self, name, pos, doc=""):
        self.fix(pos, "minecraft:air")  # reserved empty cell: the harness places a redstone block here for 1
        self.ports.append(Port(name, "pad", tuple(pos), doc=doc or "empty cell; a redstone block is placed here when the input is 1"))

    def lamp(self, name, pos, doc=""):
        self.fix(pos, LAMP)
        self.ports.append(Port(name, "lamp", tuple(pos), doc=doc or "redstone lamp, output 1 = lit"))

    def layer(self, y, rows, pal=None, x0=0, z0=0):
        self.solution += ascii_layer(y, rows, pal, x0, z0)

    def line(self, y, z, x0, items):
        """One row along +x: items are palette chars or full state strings ('' / '.' = skip)."""
        for i, it in enumerate(items):
            st = BASE.get(it) if len(it) == 1 else it
            if st:
                self.solution.append([x0 + i, y, z, st])

    def put(self, pos, state):
        self.solution.append([*pos, state])

    def table(self, ins, outs, fn, public_every=2, extra_hidden_repeat=True, shuffle=True, settle=100, reset_between=False):
        """Exhaustive truth table from fn(**inputs)->tuple; public rows = every `public_every`th (all for <=3 inputs: the truth table is the spec),
        hidden = the rest plus a full second copy, so evaluation order/leftover state matters."""
        combos = list(itertools.product((0, 1), repeat=len(ins)))
        res = {c: fn(**dict(zip(ins, c))) for c in combos}
        res = {c: (r if isinstance(r, tuple) else (r,)) for c, r in res.items()}
        public = {i for i in range(len(combos)) if len(ins) <= 3 or i % public_every == 0}
        for k in range(len(outs)):  # public rows must show both output values, or constant outputs would pass them
            for val in (0, 1):
                if not any(res[combos[i]][k] == val for i in public):
                    public.add(next(i for i, c in enumerate(combos) if res[c][k] == val))
        rows = [Vector(dict(zip(ins, c)), dict(zip(outs, res[c])), i in public, "".join(map(str, c))) for i, c in enumerate(combos)]
        if extra_hidden_repeat:
            rows += [Vector(dict(zip(ins, c)), dict(zip(outs, res[c])), False, "".join(map(str, c)) + "'") for c in reversed(combos)]
        self.behaviour.truth_table = TruthTable(rows, settle=settle, shuffle=shuffle, reset_between=reset_between)

    def seq(self, name, events, expect, duration, public=True):
        self.behaviour.sequences.append(Sequence(name, [Event(t, s) for t, s in events],
                                                  [Expect(*e) if not isinstance(e, Expect) else e for e in expect], duration, public))

    def build(self):
        sx, sy, sz = self.size
        c = Contract(
            id=self.id, title=self.title, tier=self.tier, description=self.desc,
            bbox=self.bbox_override or [0, 0, 0, sx - 1, sy - 1, sz - 1],
            build_box=self.build_box_override or self.build_box or [0, 1, 0, sx - 1, sy - 1, sz - 1],
            ports=self.ports, fixed=self.fixed, behaviour=self.behaviour, invariants=self.invariants,
            budgets=self.budgets, depends=self.depends, eval_seed=hash_id(self.id),
            keep_clear=self.keep_clear, no_write=self.no_write,
            forbidden=DEFAULT_FORBIDDEN + tuple(self.forbidden_extra))
        c.reference = reference_stats(self.solution)
        errs = c.validate()
        if errs:
            raise ValueError(f"{self.id}: {errs}")
        return Task(c, self.solution, self.broken, self.notes)


def hash_id(s):
    return sum(ord(ch) * (i + 1) for i, ch in enumerate(s)) % 100000


# =========================================================================== T0
def t0_lamp():
    p = Plan("t0_lamp", "Lever lights a lamp", "T0", (6, 3, 3),
             "Flipping the lever must light the lamp, and flipping it back must turn the lamp off. "
             "Connect the lever to the lamp with redstone.")
    p.lever("A", (0, 1, 1))
    p.lamp("Y", (5, 1, 1))
    p.layer(1, ["+" * 4], x0=1, z0=1)
    p.table(["A"], ["Y"], lambda A: A)
    return p


def t0_wire_long():
    p = Plan("t0_wire_long", "Signal over a long line", "T0", (22, 3, 3),
             "The lamp is 21 blocks from the lever. Redstone dust loses one signal level per block, so the signal must "
             "be boosted on the way. Light the lamp when the lever is on.")
    p.lever("A", (0, 1, 1))
    p.lamp("Y", (21, 1, 1))
    p.layer(1, ["+" * 9 + ">" + "+" * 10], x0=1, z0=1)
    p.table(["A"], ["Y"], lambda A: A)
    return p


def t0_not():
    p = Plan("t0_not", "NOT gate", "T0", (8, 3, 3),
             "Make a redstone torch inverter: the lamp is ON while the lever is off and turns OFF when the lever is on.")
    p.lever("A", (0, 1, 1))
    p.lamp("Y", (7, 1, 1))
    p.layer(1, ["+#e+++"], x0=1, z0=1)
    p.table(["A"], ["Y"], lambda A: 1 - A)
    return p


def t0_or():
    p = Plan("t0_or", "OR gate", "T0", (6, 3, 3),
             "The lamp must be on when lever A, lever B, or both are on, and off only when both are off.")
    p.lever("A", (0, 1, 0))
    p.lever("B", (0, 1, 2))
    p.lamp("Y", (4, 1, 1))
    p.layer(1, ["++", "..", "++"], x0=1, z0=0)  # arms
    p.layer(1, ["+", "+", "+"], x0=2, z0=0)     # spine
    p.layer(1, ["+"], x0=3, z0=1)
    p.table(["A", "B"], ["Y"], lambda A, B: A | B)
    return p


def t0_and():
    p = Plan("t0_and", "AND gate", "T0", (10, 3, 3),
             "The lamp must be on only when both lever A and lever B are on (use redstone torches as inverters).")
    p.lever("A", (0, 1, 0))
    p.lever("B", (0, 1, 2))
    p.lamp("Y", (9, 1, 1))
    p.layer(1, ["+#e", "...", "+#e"], x0=1)
    p.layer(1, ["+", "+", "+"], x0=4)
    p.layer(1, ["+#e+"], x0=5, z0=1)
    p.table(["A", "B"], ["Y"], lambda A, B: A & B)
    return p


def t0_nor():
    p = Plan("t0_nor", "NOR gate", "T0", (8, 3, 3),
             "The lamp must be on only when both levers are off.")
    p.lever("A", (0, 1, 0))
    p.lever("B", (0, 1, 2))
    p.lamp("Y", (7, 1, 1))
    p.layer(1, ["++", "..", "++"], x0=1)
    p.layer(1, ["+", "+", "+"], x0=2)
    p.layer(1, ["+#e+"], x0=3, z0=1)
    p.table(["A", "B"], ["Y"], lambda A, B: 1 - (A | B))
    return p


def t0_fanout():
    p = Plan("t0_fanout", "One lever, two lamps", "T0", (7, 3, 5),
             "Flipping the lever must light both lamps at the same time.")
    p.lever("A", (0, 1, 2))
    p.lamp("Y1", (6, 1, 0))
    p.lamp("Y2", (6, 1, 4))
    p.layer(1, ["+++++"], x0=1, z0=0)
    p.layer(1, ["+++++"], x0=1, z0=4)
    p.layer(1, ["+", "+", "+"], x0=1, z0=1)
    p.table(["A"], ["Y1", "Y2"], lambda A: (A, A))
    return p


def t0_pad_lamp():
    p = Plan("t0_pad_lamp", "Redstone-block input", "T0", (6, 3, 3),
             "A redstone block will be placed in the empty marked cell next to the west wall when input P is on (and removed "
             "when it is off). Light the lamp while the block is present.")
    p.pad("P", (0, 1, 1))
    p.lamp("Y", (5, 1, 1))
    p.layer(1, ["+" * 4], x0=1, z0=1)
    p.table(["P"], ["Y"], lambda P: P)
    return p


def t0_piston():
    p = Plan("t0_piston", "Lever extends a piston", "T0", (7, 3, 3),
             "Flipping the lever must extend the piston (pointing up); switching the lever off must retract it.")
    p.lever("A", (0, 1, 1))
    p.fix((5, 1, 1), "minecraft:piston[extended=false,facing=up]")
    p.ports.append(Port("Y", "piston", (5, 1, 1), doc="piston (output 1 = extended)"))
    p.layer(1, ["++++"], x0=1, z0=1)
    p.table(["A"], ["Y"], lambda A: A)
    return p


def t0_delay():
    p = Plan("t0_delay", "Delay line", "T0", (14, 3, 3),
             "When the lever turns on, the lamp must NOT light for at least 10 ticks, but must be lit within 22 ticks. "
             "When the lever turns off the lamp goes off (lamps hold 4 ticks extra). Repeaters delay by 2 ticks per setting.")
    p.lever("A", (0, 1, 1))
    p.lamp("Y", (13, 1, 1))
    p.line(1, 1, 1, ["+", rep("east", 4), "+", rep("east", 3)] + ["+"] * 8)
    p.seq("on_delay", [(0, {"A": 1})], [Expect("Y", 0, 0, 9), Expect("Y", 1, 22, 40)], 40)
    p.seq("off", [(0, {"A": 1}), (40, {"A": 0})], [Expect("Y", 1, 30, 40), Expect("Y", 0, 66, 90)], 90, public=False)
    p.seq("pulse", [(0, {"A": 1}), (6, {"A": 0})], [Expect("Y", 0, 0, 9)], 60, public=False)
    return p


# =========================================================================== T1
def t1_nand():
    p = Plan("t1_nand", "NAND gate", "T1", (8, 3, 3),
             "The lamp must be on unless both lever A and lever B are on.")
    p.lever("A", (0, 1, 0))
    p.lever("B", (0, 1, 2))
    p.lamp("Y", (7, 1, 1))
    p.layer(1, ["+#e", "...", "+#e"], x0=1)
    p.layer(1, ["+", "+", "+"], x0=4)
    p.layer(1, ["++"], x0=5, z0=1)
    p.table(["A", "B"], ["Y"], lambda A, B: 1 - (A & B))
    return p


def t1_xor():
    p = Plan("t1_xor", "XOR gate", "T1", (9, 3, 5),
             "The lamp must be on when exactly one of the two levers is on (not when both or neither are). "
             "Redstone comparators in subtract mode make this compact: output = rear - max(side inputs).")
    p.lever("A", (0, 1, 2))
    p.lever("B", (8, 1, 2))
    p.lamp("Y", (4, 1, 2))
    p.put((3, 1, 2), comp("east"))
    p.put((5, 1, 2), comp("west"))
    for x, z in [(1, 2), (2, 2), (1, 1), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0),
                 (7, 2), (6, 2), (7, 3), (7, 4), (6, 4), (5, 4), (4, 4), (3, 4)]:
        p.put((x, 1, z), WIRE)
    # repeaters restore full strength on the side inputs (a comparator subtracts the side STRENGTH)
    p.put((5, 1, 1), rep("south"))
    p.put((3, 1, 3), rep("north"))
    p.table(["A", "B"], ["Y"], lambda A, B: A ^ B)
    return p


def t1_mux2():
    p = Plan("t1_mux2", "2:1 multiplexer", "T1", (9, 3, 4),
             "Lamp Y must follow lever A while select lever S is off and follow lever B while S is on.")
    p.lever("S", (3, 1, 0))
    p.lever("A", (0, 1, 3))
    p.lever("B", (8, 1, 3))
    p.lamp("Y", (4, 1, 3))
    p.put((3, 1, 3), comp("east"))
    p.put((5, 1, 3), comp("west"))
    for x, z in [(3, 1), (4, 0), (1, 3), (2, 3), (7, 3), (6, 3)]:
        p.put((x, 1, z), WIRE)
    p.put((3, 1, 2), rep("south"))  # full-strength side inputs: a comparator subtracts side STRENGTH
    p.put((5, 1, 2), rep("south"))
    p.put((5, 1, 0), STONE)
    p.put((5, 1, 1), BASE["s"])
    p.table(["S", "A", "B"], ["Y"], lambda S, A, B: B if S else A, public_every=3)
    return p


def t1_rs_latch():
    p = Plan("t1_rs_latch", "RS NOR latch", "T1", (8, 3, 5),
             "Build a latch that remembers which lever was switched on last: briefly switching S on makes lamp Q light and "
             "stay lit; briefly switching R on turns it off and it stays off. (S and R are never on together.)")
    p.lever("S", (6, 1, 3), doc="set")
    p.lever("R", (0, 1, 2), doc="reset")
    p.lamp("Q", (7, 1, 1))
    for pos, st in [((3, 1, 1), STONE), ((4, 1, 1), BASE["e"]), ((3, 1, 3), STONE), ((2, 1, 3), BASE["w"])]:
        p.put(pos, st)
    for x, z in [(5, 1), (6, 1), (5, 2), (5, 3), (4, 3), (1, 3), (1, 2), (1, 1), (2, 1)]:
        p.put((x, 1, z), WIRE)
    pulse = lambda port, t: [(t, {port: 1}), (t + 8, {port: 0})]
    p.seq("reset_set_hold", pulse("R", 0) + pulse("S", 20), [Expect("Q", 0, 14, 19), Expect("Q", 1, 34, 60)], 60)
    p.seq("set_reset_hold", pulse("R", 0) + pulse("S", 20) + pulse("R", 50),
          [Expect("Q", 1, 34, 49), Expect("Q", 0, 64, 90)], 90)
    p.seq("set_first", pulse("S", 0) + pulse("R", 30) + pulse("S", 60),
          [Expect("Q", 1, 14, 29), Expect("Q", 0, 44, 59), Expect("Q", 1, 74, 100)], 100, public=False)
    p.seq("reset_twice", pulse("S", 0) + pulse("R", 20) + pulse("R", 40) + pulse("S", 70) + pulse("S", 90),
          [Expect("Q", 0, 34, 69), Expect("Q", 1, 84, 120)], 120, public=False)
    return p


def t1_pulse_extender():
    p = Plan("t1_pulse_extender", "Pulse extender", "T1", (8, 3, 3),
             "A button press holds its output for 20 ticks. Make the lamp stay lit clearly longer than that: it must still "
             "be lit 34 ticks after the press, but must be off again by tick 50.")
    p.button("B", (0, 1, 1))
    p.lamp("Y", (7, 1, 1))
    for pos in [(1, 1, 1), (2, 1, 1), (3, 1, 1), (5, 1, 1), (6, 1, 1), (2, 1, 0), (5, 1, 0)]:
        p.put(pos, WIRE)
    p.put((4, 1, 1), rep("east"))  # diode: keeps the delayed copy from feeding back into the delay line
    p.put((3, 1, 0), rep("east", 4))
    p.put((4, 1, 0), rep("east", 4))
    p.seq("press", [(0, {"B": 1})], [Expect("Y", 1, 2, 34), Expect("Y", 0, 50, 80)], 80)
    p.seq("press_late", [(10, {"B": 1})], [Expect("Y", 0, 0, 9), Expect("Y", 1, 12, 44), Expect("Y", 0, 60, 90)], 90, public=False)
    p.seq("press_twice", [(0, {"B": 1}), (120, {"B": 1})], [Expect("Y", 0, 50, 119), Expect("Y", 1, 122, 154), Expect("Y", 0, 170, 200)], 200, public=False)
    return p


def t1_edge_detector():
    p = Plan("t1_edge_detector", "Rising-edge detector", "T1", (10, 3, 3),
             "Flipping the lever ON must make the lamp flash briefly (and then go dark again even though the lever stays on). "
             "Flipping it OFF must not light the lamp.")
    p.lever("A", (0, 1, 0))
    p.lamp("Y", (9, 1, 1))
    for pos in [(1, 1, 0), (4, 1, 0), (4, 1, 1), (4, 1, 2), (0, 1, 2), (1, 1, 2), (2, 1, 2), (3, 1, 2), (5, 1, 1), (8, 1, 1)]:
        p.put(pos, WIRE)
    p.put((2, 1, 0), STONE)
    p.put((3, 1, 0), BASE["e"])
    p.put((0, 1, 1), rep("south", 3))
    p.put((6, 1, 1), STONE)
    p.put((7, 1, 1), BASE["e"])
    p.seq("rise", [(10, {"A": 1})], [Expect("Y", 0, 0, 9), Expect("Y", 1, 10, 24, "any"), Expect("Y", 0, 34, 80)], 80)
    p.seq("rise_fall_rise", [(10, {"A": 1}), (60, {"A": 0}), (110, {"A": 1})],
          [Expect("Y", 1, 10, 24, "any"), Expect("Y", 0, 34, 59), Expect("Y", 0, 60, 100), Expect("Y", 1, 110, 124, "any"), Expect("Y", 0, 134, 170)], 170, public=False)
    return p


def t1_clock():
    p = Plan("t1_clock", "Switchable clock", "T1", (10, 3, 5),
             "While the lever is ON the lamp must blink steadily (several full on/off cycles within 100 ticks, none longer "
             "than ~30 ticks per phase); with the lever OFF the lamp must stay dark and the circuit must be at rest.")
    p.lever("E", (0, 1, 0), doc="enable")
    p.lamp("Y", (9, 1, 2))
    p.invariants.no_oscillation = False
    for pos in [(1, 1, 0), (2, 1, 2), (3, 1, 2), (4, 1, 2), (3, 1, 3), (3, 1, 4), (4, 1, 4), (5, 1, 4), (7, 1, 4), (7, 1, 3), (7, 1, 2), (8, 1, 2)]:
        p.put(pos, WIRE)
    p.put((2, 1, 0), STONE)
    p.put((2, 1, 1), BASE["s"])
    p.put((5, 1, 2), STONE)
    p.put((6, 1, 2), BASE["e"])
    p.put((6, 1, 4), rep("west", 2))
    p.seq("off", [], [Expect("Y", 0, 0, 100)], 100)
    p.seq("run", [(0, {"E": 1})], [Expect("Y", 1, 0, 30, "any"), Expect("Y", 0, 0, 30, "any"), Expect("Y", 1, 30, 60, "any"),
                                    Expect("Y", 0, 30, 60, "any"), Expect("Y", 1, 60, 100, "any"), Expect("Y", 0, 60, 100, "any")], 100)
    p.seq("stop", [(0, {"E": 1}), (50, {"E": 0})], [Expect("Y", 0, 70, 120)], 120, public=False)
    return p


def t1_observer():
    p = Plan("t1_observer", "Observer block detector", "T1", (7, 3, 3),
             "An observer watches the marked empty cell. When a redstone block appears there (input P on) or disappears (P off), "
             "the lamp must flash briefly; otherwise it stays dark.")
    p.pad("P", (0, 1, 1))
    p.lamp("Y", (5, 1, 1))
    p.put((1, 1, 1), "minecraft:observer[facing=west]")
    for pos in [(2, 1, 1), (3, 1, 1), (4, 1, 1)]:
        p.put(pos, WIRE)
    p.seq("appear", [(10, {"P": 1})], [Expect("Y", 0, 0, 9), Expect("Y", 1, 10, 20, "any"), Expect("Y", 0, 30, 60)], 60)
    p.seq("appear_disappear", [(10, {"P": 1}), (50, {"P": 0})], [Expect("Y", 1, 10, 20, "any"), Expect("Y", 0, 30, 49), Expect("Y", 1, 50, 60, "any"), Expect("Y", 0, 70, 100)], 100, public=False)
    return p


def t1_and3():
    p = Plan("t1_and3", "3-input AND", "T1", (10, 3, 5),
             "The lamp must be on only when all three levers A, B and C are on.")
    p.lever("A", (0, 1, 0))
    p.lever("B", (0, 1, 2))
    p.lever("C", (0, 1, 4))
    p.lamp("Y", (9, 1, 2))
    p.layer(1, ["+#e", "...", "+#e", "...", "+#e"], x0=1)
    p.layer(1, ["+"] * 5, x0=4)
    p.layer(1, ["+#e+"], x0=5, z0=2)
    p.table(["A", "B", "C"], ["Y"], lambda A, B, C: A & B & C, public_every=3)
    return p


def t1_repeater_lock():
    p = Plan("t1_repeater_lock", "Repeater-lock latch", "T1", (6, 3, 3),
             "Lamp Y follows lever A while the lock lever L is off. While L is on, Y must keep its last value "
             "even if A changes (repeaters can be locked by a powered repeater pointing into their side).")
    p.lever("A", (0, 1, 2))
    p.lever("L", (1, 1, 0))
    p.lamp("Y", (4, 1, 2))
    p.put((1, 1, 2), rep("east"))
    p.put((1, 1, 1), rep("south"))
    p.put((2, 1, 2), WIRE)
    p.put((3, 1, 2), WIRE)
    ev = lambda *a: [(t, d) for t, d in a]
    p.seq("follow_then_hold", ev((0, {"A": 1}), (20, {"L": 1}), (30, {"A": 0}), (60, {"L": 0})),
          [Expect("Y", 1, 6, 20), Expect("Y", 1, 36, 59), Expect("Y", 0, 70, 90)], 90)
    p.seq("lock_low", ev((0, {"L": 1}), (10, {"A": 1}), (40, {"L": 0})),
          [Expect("Y", 0, 0, 39), Expect("Y", 1, 50, 70)], 70, public=False)
    p.seq("lock_high_then_drop", ev((0, {"A": 1}), (10, {"L": 1}), (20, {"A": 0}), (50, {"A": 1}), (70, {"A": 0}), (90, {"L": 0})),
          [Expect("Y", 1, 5, 89), Expect("Y", 0, 100, 120)], 120, public=False)
    return p


def t1_t_flipflop():
    p = Plan("t1_t_flipflop", "T flip-flop (button toggle)", "T1", (9, 3, 6),
             "Pressing the button must toggle the lamp: first press turns it on, second press turns it off, and so on. "
             "(Medium_t_flip_flop: make a button act like a lever.) Repeaters can be locked by a powered repeater "
             "pointing into their side, which allows a master-slave design.")
    p.button("B", (0, 1, 0))
    p.lamp("Q", (8, 1, 3))
    # top row: clk -> lock_m, clk -> inverter -> lock_s; ring: Mst -> Slv -> down -> NOT -> up -> Mst
    for x, z in [(1, 0), (2, 0), (3, 0), (1, 2), (3, 2), (4, 2), (6, 2), (6, 3), (6, 4), (6, 5), (5, 5),
                 (2, 5), (1, 5), (1, 4), (1, 3), (7, 3)]:
        p.put((x, 1, z), WIRE)
    p.put((4, 1, 0), STONE)
    p.put((5, 1, 0), BASE["e"])
    p.put((2, 1, 1), rep("south"))      # lock_m
    p.put((5, 1, 1), rep("south"))      # lock_s
    p.put((2, 1, 2), rep("east", 3))    # master
    p.put((5, 1, 2), rep("east", 1))    # slave
    p.put((4, 1, 5), STONE)
    p.put((3, 1, 5), BASE["w"])
    press = lambda t: [(t, {"B": 1})]
    p.seq("toggle", press(0) + press(60) + press(120),
          [Expect("Q", 0, 0, 0), Expect("Q", 1, 20, 55), Expect("Q", 0, 80, 115), Expect("Q", 1, 140, 180)], 180)
    p.seq("toggle4", press(0) + press(50) + press(100) + press(150),
          [Expect("Q", 1, 20, 45), Expect("Q", 0, 70, 95), Expect("Q", 1, 120, 145), Expect("Q", 0, 170, 200)], 200, public=False)
    return p


# =========================================================================== T2
DOOR_SOLUTION = """
0 0 3 sticky_piston[facing=up]|1 0 3 sticky_piston[facing=up]|0 5 3 sticky_piston[facing=down]|1 5 3 sticky_piston[facing=down]
0 1 3 stone|1 1 3 stone|0 4 3 stone|1 4 3 stone|0 0 2 stone|1 0 2 stone|-1 0 2 stone|-1 1 1 stone|-1 2 0 stone|-1 3 -1 stone
-1 4 -2 stone|-1 5 -3 stone|0 5 -3 stone|0 5 -2 stone|0 5 -1 stone|0 5 0 stone|0 5 1 stone|0 5 2 stone|1 5 2 stone|2 0 2 stone
0 1 2 redstone_wire|1 1 2 redstone_wire|-1 1 2 redstone_wire|-1 2 1 redstone_wire|-1 3 0 redstone_wire|-1 4 -1 redstone_wire
-1 5 -2 redstone_wire|-1 6 -3 redstone_wire|0 6 -3 redstone_wire|0 6 -2 redstone_wire|0 6 -1 redstone_wire|0 6 0 redstone_wire
0 6 1 redstone_wire|0 6 2 redstone_wire|1 6 2 redstone_wire
"""


# Blocks that count as a closed door cell: pushed opaque full blocks. piston_head / moving_piston / pistons are NOT in the list
# (the old 'solid' = not-air port accepted a piston's own extended head; see docs/VALIDITY.md).
DOOR_BLOCKS = ["minecraft:" + b for b in ("stone", "cobblestone", "smooth_stone", "stone_bricks", "dirt", "bricks",
                                           "oak_planks", "spruce_planks", "birch_planks", "white_wool", "netherrack")]


def door_ports(p, x0, y0, z, w, h):
    """Output ports for a w*h door whose min corner cell is (x0,y0,z). D<i><j> = cell holds a pushed full block (closed),
    A<i><j> = cell is air (open). A sequence expects each port 'init' / '!init', so a door that rests closed or open both
    qualify, but every cell must really be air when open and a real block when closed (never a piston head)."""
    names = {}
    allow = ", ".join(b.split(":")[1] for b in DOOR_BLOCKS)
    for i in range(w):
        for j in range(h):
            pos = (x0 + i, y0 + j, z)
            p.ports.append(Port(f"D{i}{j}", "region", pos, box=[*pos, *pos], region_mode="block_in", blocks=list(DOOR_BLOCKS),
                                doc=f"door cell closed: 1 = holds a pushed full block ({allow}); a piston head or moving piston is 0"))
            p.ports.append(Port(f"A{i}{j}", "region", pos, box=[*pos, *pos], region_mode="air", doc="door cell open: 1 = air"))
            names[f"D{i}{j}"] = names[f"A{i}{j}"] = pos
    return names


def t2_door_2x2():
    """Spec in the style of Nekochu/Gemma-4-31B-RedstoneDoor (same hallway/door coordinates); the activation lever is
    fixed at (2,1,2) instead of model-chosen. The reference is that model card's live-verified 2x2 door.
    Round-2 audit fix: closed = a pushed non-piston full block (block_in allowlist), open = air; the first version accepted
    any non-air block and a sticky piston's own head filled the cells."""
    p = Plan("t2_door_2x2", "2x2 piston door", "T2", (22, 16, 25),
             "Build a working 2x2 piston door. The four door cells (0..1, 2..3, z=3) must toggle between air (open) and a solid "
             "full block (closed: a block your pistons push into the cell, e.g. stone) when the lever is flipped, and then hold their "
             "state indefinitely in both positions. A piston head in a cell does not count as a closed door. The hallway (x 0..1, "
             "y 2..3, z -3..9) must stay air except for the door cells (and may not be built in). Use sticky pistons; do not place "
             "piston_head or moving_piston blocks.")
    p.fixed = [{"box": [-10, -3, -6, 11, -3, 18], "state": "minecraft:smooth_stone"}]
    p.fix((2, 1, 2), "minecraft:lever[face=floor,facing=north,powered=false]")
    p.ports.append(Port("L", "click", (2, 1, 2), facing="north", doc="activation lever (fixed)"))
    names = door_ports(p, 0, 2, 3, 2, 2)
    for ln in DOOR_SOLUTION.strip().splitlines():
        for item in ln.split("|"):
            x, y, z, st = item.split()
            p.put((int(x), int(y), int(z)), "minecraft:" + st)
    p.size = (22, 16, 25)
    p.bbox_override = [-10, -3, -6, 11, 12, 18]
    p.build_box_override = [-10, -2, -6, 11, 12, 18]
    p.keep_clear = [[0, 2, -3, 1, 3, 2], [0, 2, 4, 1, 3, 9]]
    p.no_write = [[0, 2, 3, 1, 3, 3]]
    p.forbidden_extra = ["minecraft:piston_head", "minecraft:moving_piston"]
    p.budgets.rest = 100
    p.budgets.max_blocks = 400
    toggle = lambda *a: [(t, {"L": v}) for t, v in a]
    p.seq("open_close", toggle((10, 1), (250, 0)),
          [Expect(n, "init", 0, 9) for n in names] + [Expect(n, "!init", 60, 240) for n in names] + [Expect(n, "init", 300, 480) for n in names], 480)
    p.seq("cycle_twice", toggle((10, 1), (150, 0), (300, 1), (450, 0)),
          [Expect(n, "!init", 60, 140) for n in names] + [Expect(n, "init", 200, 290) for n in names]
          + [Expect(n, "!init", 350, 440) for n in names] + [Expect(n, "init", 500, 600) for n in names], 600, public=False)
    return p


def build_all():
    return [f() for n, f in sorted(globals().items()) if n.startswith("t") and n[1:2].isdigit() and callable(f)]


if __name__ == "__main__":
    want = set(sys.argv[1:])
    for plan in build_all():
        if want and plan.id not in want:
            continue
        task = plan.build()
        old = Path(__file__).parent / f"{plan.id}.json"
        if old.exists():  # keep stored broken variants while the reference is unchanged (refresh: validate_tasks.py --write-broken)
            prev = load_task(old)
            if prev.solution == task.solution:
                task.broken = prev.broken
        print("writing", save_task(task))
