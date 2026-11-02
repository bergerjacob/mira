"""Hand-written, physically valid circuits for the Nucleation-vs-vanilla differential.

Every circuit has real support blocks and correct attachment faces (levers on a floor
or a wall block they are attached to, wall torches facing away from their block).
Note `facing` on repeaters/comparators points at the INPUT side (output is opposite);
on pistons, droppers, hoppers and observers it points at the output/face. Wire/diode/piston states are written bare
(`redstone_wire`): both backends re-derive shapes in the placement pass (settle="place").

`expect` is a sanity check that the circuit really does something: a list of
(tick, pos, substring) that must hold in the *oracle's* trace (tick -1 = at rest after
preroll). A circuit whose expectation fails in vanilla is a bad circuit, not a divergence.
"""

from dataclasses import dataclass, field
from typing import List, Tuple

from tools.oracle.trace import Op, Plan, Pos, put, use

STONE = "minecraft:stone"


@dataclass
class Circuit:
    name: str
    mechanic: str
    description: str
    blocks: list
    plan: Plan
    expect: List[Tuple[int, Pos, str]] = field(default_factory=list)


def floor(x0, x1, z0=0, z1=0, y=0):
    return [(x, y, z, STONE) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)]


def chest_items(full_stacks: int, partial: int = 0, item="minecraft:cobblestone") -> str:
    """Chest SNBT with `full_stacks` stacks of 64 plus one stack of `partial`."""
    slots = [f'{{Slot:{i}b,id:"{item}",count:64}}' for i in range(full_stacks)]
    if partial:
        slots.append(f'{{Slot:{full_stacks}b,id:"{item}",count:{partial}}}')
    return "{Items:[" + ",".join(slots) + "]}"


def items(count: int, item="minecraft:cobblestone") -> str:
    return f'{{Items:[{{Slot:0b,id:"{item}",count:{count}}}]}}'


LEVER = "minecraft:lever[face=floor,facing=east,powered=false]"
LAMP = "minecraft:redstone_lamp[lit=false]"
WIRE = "minecraft:redstone_wire"


def lever_wire_lamp():
    b = floor(0, 4) + [(0, 1, 0, LEVER)] + [(x, 1, 0, WIRE) for x in (1, 2, 3)] + [(4, 1, 0, LAMP)]
    return Circuit(
        "lever_wire_lamp", "wire", "lever -> 3 wire -> lamp; on at t2, off at t15", b,
        Plan(40, [use(2, (0, 1, 0)), use(15, (0, 1, 0))], [(4, 1, 0), (1, 1, 0), (3, 1, 0)]),
        [(-1, (4, 1, 0), "lit=false"), (10, (4, 1, 0), "lit=true"), (39, (4, 1, 0), "lit=false")])


def wire_decay():
    n = 15
    b = floor(0, n + 1) + [(0, 1, 0, LEVER)] + [(x, 1, 0, WIRE) for x in range(1, n + 1)] \
        + [(n + 1, 1, 0, LAMP)]
    return Circuit(
        "wire_decay", "wire", "15 wires (power 15..1) then lamp; the last wire must still light it", b,
        Plan(50, [use(2, (0, 1, 0)), use(25, (0, 1, 0))], [(n + 1, 1, 0), (n, 1, 0), (1, 1, 0)]),
        [(20, (n + 1, 1, 0), "lit=true"), (49, (n + 1, 1, 0), "lit=false")])


def wire_vertical():
    b = floor(0, 1) + [(0, 1, 0, LEVER), (1, 1, 0, WIRE), (2, 1, 0, STONE), (2, 2, 0, WIRE),
                       (3, 2, 0, LAMP)]
    return Circuit(
        "wire_vertical", "wire", "wire climbs one stone step to a lamp", b,
        Plan(40, [use(2, (0, 1, 0)), use(20, (0, 1, 0))], [(3, 2, 0), (2, 2, 0)]),
        [(15, (3, 2, 0), "lit=true"), (39, (3, 2, 0), "lit=false")])


def strong_power():
    b = floor(0, 3) + [(2, 1, 0, STONE), (2, 2, 0, LEVER), (3, 1, 0, LAMP)]
    return Circuit(
        "strong_power", "block_power", "lever on top of stone strongly powers an adjacent lamp", b,
        Plan(30, [use(2, (2, 2, 0)), use(15, (2, 2, 0))], [(3, 1, 0)]),
        [(10, (3, 1, 0), "lit=true"), (29, (3, 1, 0), "lit=false")])


def redstone_block_lamp():
    b = floor(0, 3) + [(2, 1, 0, LAMP)]
    return Circuit(
        "redstone_block_lamp", "block_power", "redstone block placed/removed next to a lamp", b,
        Plan(30, [put(2, (1, 1, 0), "redstone_block"), put(12, (1, 1, 0), "air")], [(2, 1, 0)]),
        [(5, (2, 1, 0), "lit=true"), (29, (2, 1, 0), "lit=false")])


def torch_not_gate():
    b = floor(0, 4) + [(0, 1, 0, LEVER), (1, 1, 0, WIRE), (2, 1, 0, STONE),
                       (3, 1, 0, "minecraft:redstone_wall_torch[facing=east,lit=true]"),
                       (4, 1, 0, LAMP)]
    return Circuit(
        "torch_not_gate", "torch", "lever -> wire -> block with wall torch -> lamp (NOT)", b,
        Plan(50, [use(5, (0, 1, 0)), use(25, (0, 1, 0))], [(4, 1, 0), (3, 1, 0)]),
        [(3, (4, 1, 0), "lit=true"), (15, (4, 1, 0), "lit=false"), (49, (4, 1, 0), "lit=true")])


def torch_clock_burnout():
    # torch on block A drives a wire loop that powers A again: oscillates until burnout
    wires = [(3, 1, 0), (3, 1, 1), (3, 1, 2), (2, 1, 2), (1, 1, 2), (1, 1, 1)]
    b = floor(0, 3, 0, 2) + [(1, 1, 0, STONE),
                             (2, 1, 0, "minecraft:redstone_wall_torch[facing=east,lit=true]")] \
        + [(x, y, z, WIRE) for x, y, z in wires]
    return Circuit(
        "torch_clock_burnout", "torch_burnout", "torch+wire loop clock; burns out after 8 toggles/60t", b,
        Plan(120, [], [(2, 1, 0), (1, 1, 1)]),
        [])


def repeater_chain():
    def rep(x, d):
        return (x, 1, 0, f"minecraft:repeater[delay={d},facing=west,locked=false,powered=false]")
    b = floor(0, 5) + [(0, 1, 0, LEVER), (1, 1, 0, WIRE), rep(2, 1), rep(3, 2), rep(4, 4),
                       (5, 1, 0, LAMP)]
    return Circuit(
        "repeater_chain", "repeater", "wire -> repeaters delay 1,2,4 -> lamp (latency 14 ticks on, 14 off)", b,
        Plan(60, [use(2, (0, 1, 0)), use(30, (0, 1, 0))], [(5, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0)]),
        [(40, (5, 1, 0), "lit=true"), (59, (5, 1, 0), "lit=false")])


def repeater_lock():
    r = "minecraft:repeater[delay=1,facing=west,locked=false,powered=false]"
    lock = "minecraft:repeater[delay=1,facing=south,locked=false,powered=false]"
    b = floor(0, 2, 0, 2) + [(0, 1, 0, LEVER), (1, 1, 0, r), (2, 1, 0, LAMP), (1, 1, 1, lock),
                             (1, 1, 2, LEVER)]
    return Circuit(
        "repeater_lock", "repeater", "side repeater locks the main one; unlocking releases the lamp", b,
        Plan(50, [use(2, (1, 1, 2)), use(8, (0, 1, 0)), use(25, (1, 1, 2))],
             [(2, 1, 0), (1, 1, 0), (1, 1, 1)]),
        [(20, (2, 1, 0), "lit=false"), (40, (2, 1, 0), "lit=true")])


def _piston_rig(piston):
    b = floor(0, 4, -1, 1) + [(1, 1, 0, f"minecraft:{piston}[extended=false,facing=east]"),
                              (2, 1, 0, STONE)]
    return b


def sticky_piston_rb():
    b = _piston_rig("sticky_piston")
    return Circuit(
        "sticky_piston_rb", "piston", "sticky piston pushes/pulls stone; activated by placing a redstone block", b,
        Plan(50, [put(2, (1, 1, 1), "redstone_block"), put(20, (1, 1, 1), "air")],
             [(1, 1, 0), (2, 1, 0), (3, 1, 0), (2, 1, 0)]),
        [(10, (3, 1, 0), "minecraft:stone"), (49, (2, 1, 0), "minecraft:stone")])


def sticky_piston_lever():
    b = _piston_rig("sticky_piston")
    b.append((1, 1, 1, LEVER))
    return Circuit(
        "sticky_piston_lever", "piston", "sticky piston driven by an adjacent lever (real use path)", b,
        Plan(50, [use(2, (1, 1, 1)), use(20, (1, 1, 1))], [(1, 1, 0), (2, 1, 0), (3, 1, 0)]),
        [(10, (3, 1, 0), "minecraft:stone"), (49, (2, 1, 0), "minecraft:stone")])


def piston_rb():
    b = _piston_rig("piston")
    return Circuit(
        "piston_rb", "piston", "plain piston pushes stone, leaves it when released", b,
        Plan(40, [put(2, (1, 1, 1), "redstone_block"), put(15, (1, 1, 1), "air")],
             [(1, 1, 0), (2, 1, 0), (3, 1, 0)]),
        [(10, (3, 1, 0), "minecraft:stone"), (39, (3, 1, 0), "minecraft:stone")])


def piston_qc():
    b = _piston_rig("piston")
    return Circuit(
        "piston_qc", "piston_qc", "quasi-connectivity: power at the diagonal-above cell extends the piston", b,
        Plan(40, [put(2, (1, 2, 1), "redstone_block"), put(15, (1, 2, 1), "air")],
             [(1, 1, 0), (2, 1, 0), (3, 1, 0)]),
        [])


def slime_pull():
    b = floor(0, 5, -1, 2) + [
        (1, 1, 0, "minecraft:sticky_piston[extended=false,facing=east]"),
        (2, 1, 0, "minecraft:slime_block"), (2, 1, 1, STONE), (3, 1, 0, STONE)]
    return Circuit(
        "slime_pull", "piston", "sticky piston moves slime + side-attached stone + stone ahead and pulls back", b,
        Plan(60, [put(2, (1, 1, -1), "redstone_block"), put(25, (1, 1, -1), "air")],
             [(2, 1, 0), (3, 1, 0), (4, 1, 0), (2, 1, 1), (3, 1, 1)]),
        [])


def observer_pulse():
    b = floor(-2, 3) + [(-1, 1, 0, LAMP), (0, 1, 0, WIRE),
                        (1, 1, 0, "minecraft:observer[facing=east,powered=false]")]
    return Circuit(
        "observer_pulse", "observer", "observer sees a redstone block appear/vanish and pulses a lamp", b,
        Plan(40, [put(5, (2, 1, 0), "redstone_block"), put(20, (2, 1, 0), "air")],
             [(-1, 1, 0), (0, 1, 0), (1, 1, 0)]),
        [])


def observer_pair():
    b = floor(-1, 4) + [(0, 1, 0, LAMP), (1, 1, 0, "minecraft:observer[facing=east,powered=false]"),
                        (2, 1, 0, "minecraft:observer[facing=west,powered=false]"), (3, 1, 0, LAMP)]
    return Circuit(
        "observer_pair", "observer", "two observers facing each other (observer clock), each lighting a lamp", b,
        Plan(60, [], [(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0)]),
        [])


def comparator_chest():
    b = floor(0, 4) + [
        (0, 1, 0, "minecraft:chest[facing=east,type=single,waterlogged=false]", chest_items(9)),
        (1, 1, 0, "minecraft:comparator[facing=west,mode=compare,powered=false]"),
        (2, 1, 0, WIRE), (3, 1, 0, WIRE), (4, 1, 0, LAMP)]
    return Circuit(
        "comparator_chest", "comparator", "comparator reads a chest with 9 full stacks (signal 5) -> wire -> lamp", b,
        Plan(30, [], [(1, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0)]),
        [])


def comparator_subtract():
    b = floor(0, 4, 0, 1) + [
        (0, 1, 0, "minecraft:redstone_block"),
        (1, 1, 0, "minecraft:comparator[facing=west,mode=subtract,powered=false]"),
        (1, 1, 1, LEVER), (2, 1, 0, WIRE), (3, 1, 0, LAMP)]
    return Circuit(
        "comparator_subtract", "comparator", "subtract mode: rear 15 minus side lever 15 -> 0", b,
        Plan(40, [use(5, (1, 1, 1)), use(20, (1, 1, 1))], [(1, 1, 0), (2, 1, 0), (3, 1, 0)]),
        [])


def hopper_chain():
    full = chest_items(26, 62)
    b = floor(0, 4, 0, 0) + [
        (0, 2, 0, "minecraft:hopper[enabled=true,facing=east]", items(3)),
        (1, 2, 0, "minecraft:hopper[enabled=true,facing=down]", "{Items:[]}"),
        (1, 1, 0, "minecraft:chest[facing=east,type=single,waterlogged=false]", full),
        (2, 1, 0, "minecraft:comparator[facing=west,mode=compare,powered=false]"),
        (3, 1, 0, WIRE), (4, 1, 0, LAMP)]
    return Circuit(
        "hopper_chain", "hopper", "hopper -> hopper -> near-full chest -> comparator -> wire (14 -> 15)", b,
        Plan(80, [], [(2, 1, 0), (3, 1, 0), (4, 1, 0)]),
        [])


def dropper_chain():
    full = chest_items(26, 62)
    d = "minecraft:dropper[facing=east,triggered=false]"
    b = floor(0, 6, 0, 0) + [
        (1, 1, 0, d, items(2)), (2, 1, 0, d, "{Items:[]}"),
        (3, 1, 0, "minecraft:chest[facing=east,type=single,waterlogged=false]", full),
        (4, 1, 0, "minecraft:comparator[facing=west,mode=compare,powered=false]"),
        (5, 1, 0, WIRE), (6, 1, 0, LAMP)]
    ops = [put(2, (1, 2, 0), "redstone_block"), put(8, (1, 2, 0), "air"),
           put(14, (1, 2, 0), "redstone_block"), put(20, (1, 2, 0), "air"),
           put(30, (2, 2, 0), "redstone_block"), put(36, (2, 2, 0), "air"),
           put(42, (2, 2, 0), "redstone_block"), put(48, (2, 2, 0), "air")]
    return Circuit(
        "dropper_chain", "dropper", "dropper -> dropper -> near-full chest -> comparator -> wire", b,
        Plan(70, ops, [(1, 1, 0), (2, 1, 0), (4, 1, 0), (5, 1, 0), (6, 1, 0)]),
        [])


def button_lamp():
    b = floor(0, 3) + [(0, 1, 0, STONE), (1, 1, 0, "minecraft:stone_button[face=wall,facing=east,powered=false]"),
                       (2, 1, 0, WIRE), (3, 1, 0, LAMP)]
    return Circuit(
        "button_lamp", "button", "stone button (20t) -> wire -> lamp", b,
        Plan(50, [use(3, (1, 1, 0))], [(1, 1, 0), (2, 1, 0), (3, 1, 0)]),
        [(10, (3, 1, 0), "lit=true"), (49, (3, 1, 0), "lit=false")])


# --- minimal repros for divergences found against the oracle ---------------------------------

def piston_qc_update():
    """QC piston: power at the cell diagonal-above, then a block update on the piston."""
    c = piston_qc()
    c.name, c.description = "piston_qc_update", "as piston_qc, then stone placed beside the piston at t6 (block update)"
    c.plan = Plan(40, [put(2, (1, 2, 1), "redstone_block"), put(6, (0, 1, 0), "stone"),
                       put(20, (1, 2, 1), "air"), put(24, (0, 1, 0), "air")],
                  [(1, 1, 0), (2, 1, 0), (3, 1, 0)])
    return c


def _comparator_side(name, side_block, desc):
    b = floor(0, 3, 0, 1) + [
        (0, 1, 0, "minecraft:redstone_block"),
        (1, 1, 0, "minecraft:comparator[facing=west,mode=subtract,powered=false]"),
        (1, 1, 1, side_block), (2, 1, 0, WIRE), (3, 1, 0, LAMP)]
    ops = [use(5, (1, 1, 1)), use(20, (1, 1, 1))] if "lever" in side_block else \
        [put(5, (1, 1, 1), "air"), put(20, (1, 1, 1), "redstone_block")]
    return Circuit(name, "comparator", desc, b, Plan(40, ops, [(1, 1, 0), (2, 1, 0), (3, 1, 0)]), [])


def comparator_side_lever():
    return _comparator_side("comparator_side_lever", LEVER,
                            "subtract mode, side input is a lever (not a comparator side input in vanilla)")


def comparator_side_block():
    return _comparator_side("comparator_side_block", "minecraft:redstone_block",
                            "control: side input is a redstone block, removed at t5, restored at t20")


def dropper_qc_diag():
    d = "minecraft:dropper[facing=east,triggered=false]"
    b = floor(0, 3, 0, 0) + [(2, 1, 0, d, items(2)), (3, 1, 0, STONE)]
    return Circuit(
        "dropper_qc_diag", "dropper", "redstone block beside the cell above a dropper, no update to the dropper", b,
        Plan(30, [put(2, (1, 2, 0), "redstone_block"), put(15, (1, 2, 0), "air")], [(2, 1, 0)]), [])


def dropper_feed_dropper():
    d = "minecraft:dropper[facing=east,triggered=false]"
    b = floor(0, 3, 0, 0) + [(1, 1, 0, d, items(2)), (2, 1, 0, d, "{Items:[]}")]
    return Circuit(
        "dropper_feed_dropper", "dropper", "dropper 1 (powered from the west) drops into unpowered dropper 2", b,
        Plan(30, [put(2, (0, 1, 0), "redstone_block"), put(12, (0, 1, 0), "air")], [(1, 1, 0), (2, 1, 0)]), [])


def dropper_feed_dropper_qc():
    d = "minecraft:dropper[facing=east,triggered=false]"
    b = floor(0, 3, 0, 0) + [(1, 1, 0, d, items(2)), (2, 1, 0, d, "{Items:[]}")]
    return Circuit(
        "dropper_feed_dropper_qc", "dropper", "as dropper_feed_dropper but d1 is powered from above, "
        "which is also beside the cell above d2 (QC situation for d2)", b,
        Plan(30, [put(2, (1, 2, 0), "redstone_block"), put(12, (1, 2, 0), "air")], [(1, 1, 0), (2, 1, 0)]), [])


def torch_unsupported():
    """Wall torch whose attachment block is air: vanilla pops it off during placement. INVALID by design."""
    b = floor(0, 3) + [(2, 1, 0, "minecraft:redstone_wall_torch[facing=east,lit=true]"), (3, 1, 0, LAMP)]
    return Circuit("torch_unsupported", "popoff", "wall torch with no support block (invalid build)", b,
                   Plan(10, [], [(2, 1, 0)]), [])


REPROS = [piston_qc_update, comparator_side_lever, comparator_side_block, dropper_qc_diag,
          dropper_feed_dropper, dropper_feed_dropper_qc, torch_unsupported]


ALL = [lever_wire_lamp, wire_decay, wire_vertical, strong_power, redstone_block_lamp,
       torch_not_gate, torch_clock_burnout, repeater_chain, repeater_lock, sticky_piston_rb,
       sticky_piston_lever, piston_rb, piston_qc, slime_pull, observer_pulse, observer_pair,
       comparator_chest, comparator_subtract, hopper_chain, dropper_chain, button_lamp]


def all_circuits(repros: bool = False) -> List[Circuit]:
    return [f() for f in ALL + (REPROS if repros else [])]
