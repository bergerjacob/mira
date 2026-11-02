#!/usr/bin/env python3
"""Redstone facts the baselines struggled with, as tiny verifiable scenarios (Nucleation) with an optional vanilla 26.2 cross-check.

    .venv/bin/python scripts/t4_physics_facts.py              # run every scenario in Nucleation, print pass/fail
    .venv/bin/python scripts/t4_physics_facts.py --oracle     # also capture each scenario in the vanilla oracle and compare the probes

Each fact = a builder program, the levers to click (at tick 2, after 10 idle ticks), and `expect`: {(pos, property): value} read
after 30 ticks (`rest` = the same before any click). Properties: `lit` (lamp), `power` (dust), `block` (block id, short) ...
tests/env/test_physics_facts.py asserts the Nucleation side of every fact; docs/T4.md and evaluation/prompts/builder_system.md quote them.
"""
import argparse
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation import builder as BD  # noqa: E402
from tools.oracle.trace import Plan, use  # noqa: E402

FLOOR = "    b.fill((-1, 0, -5), (14, 0, 9), solid())\n"
TICKS = 30
FACTS = {}


def fact(fid, claim, body, clicks, expect, rest=None):
    FACTS[fid] = dict(id=fid, claim=claim, src="def build(b):\n" + FLOOR + body, clicks=clicks, expect=expect, rest=rest or {})


LEVER_E = "lever(face='floor', facing=EAST)"

fact("lever_adjacent_dust", "A lever powers the dust next to it on every horizontal side (15, then 14 per dust further on).",
     f"    b.set((0,1,0), {LEVER_E})\n    b.points(wire(), (1,1,0), (2,1,0), (0,1,1), (0,1,-1))\n",
     [(0, 1, 0)], {((1, 1, 0), "power"): "15", ((2, 1, 0), "power"): "14", ((0, 1, 1), "power"): "15", ((0, 1, -1), "power"): "15"},
     {((1, 1, 0), "power"): "0"})

fact("lever_attached_block", "A lever powers the block it is mounted on (strongly): a lamp or dust next to that block gets power; so does dust next to the lever.",
     "    b.set((3,1,0), solid())\n    b.set((3,2,0), lever(face='floor', facing=EAST))\n    b.set((4,1,0), lamp())\n    b.set((2,1,0), wire())\n    b.set((3,1,1), solid())\n    b.set((3,2,1), wire())\n",
     [(3, 2, 0)], {((4, 1, 0), "lit"): "true", ((2, 1, 0), "power"): "15", ((3, 2, 1), "power"): "15"}, {((4, 1, 0), "lit"): "false"})

fact("lever_no_diagonal", "Dust one level up and one cell over from a lever (a diagonal) gets nothing from the lever; one flat dust first, then up, works.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.points(solid(), (1,1,0), (2,1,0))\n    b.points(wire(), (1,2,0), (2,2,0))\n"
     f"    b.set((0,1,3), {LEVER_E})\n    b.set((1,1,3), wire())\n    b.points(solid(), (2,1,3), (3,1,3))\n    b.points(wire(), (2,2,3), (3,2,3))\n",
     [(0, 1, 0), (0, 1, 3)],
     {((1, 2, 0), "power"): "0", ((2, 2, 0), "power"): "0", ((1, 1, 3), "power"): "15", ((2, 2, 3), "power"): "14", ((3, 2, 3), "power"): "13"})

fact("dust_line_end", "A straight dust line powers the block at its END (a lamp there lights) but not the cells beside its middle.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.points(wire(), (1,1,0), (2,1,0), (3,1,0))\n    b.set((4,1,0), lamp())\n    b.set((2,1,-1), lamp())\n    b.set((2,1,1), lamp())\n",
     [(0, 1, 0)], {((4, 1, 0), "lit"): "true", ((2, 1, -1), "lit"): "false", ((2, 1, 1), "lit"): "false"})

fact("dust_stub", "A dust with ONE connection (here the lever) also points at the opposite side (the 'stub'): a lamp there lights.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.set((1,1,0), wire())\n    b.set((2,1,0), lamp())\n    b.set((1,1,-1), lamp())\n    b.set((1,1,1), lamp())\n",
     [(0, 1, 0)], {((2, 1, 0), "lit"): "true", ((1, 1, -1), "lit"): "false", ((1, 1, 1), "lit"): "false"})

fact("dust_corner", "A dust bent at a corner powers only the blocks its two connections point at, not the block on its outer side.",
     f"    b.set((0,1,-3), {LEVER_E})\n    b.points(wire(), (1,1,-3), (1,1,-2))\n    b.set((2,1,-3), lamp())\n    b.set((1,1,-4), lamp())\n    b.set((1,1,-1), lamp())\n",
     [(0, 1, -3)], {((1, 1, -1), "lit"): "true", ((2, 1, -3), "lit"): "false", ((1, 1, -4), "lit"): "false"})

fact("dust_powers_block_below", "Dust powers the block it sits on: a lamp placed next to the floor block under powered dust lights.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.points(wire(), (1,1,0), (2,1,0))\n    b.set((2,0,1), lamp())\n",
     [(0, 1, 0)], {((2, 0, 1), "lit"): "true"}, {((2, 0, 1), "lit"): "false"})

fact("lamp_conducts", "A redstone lamp is a conductor: dust pointing at one powers it strongly and every lamp or dust next to IT lights too.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.set((1,1,0), wire())\n    b.set((2,1,0), lamp())\n    b.set((2,1,1), lamp())\n    b.set((3,1,0), lamp())\n    b.set((2,2,0), lamp())\n",
     [(0, 1, 0)], {((2, 1, 0), "lit"): "true", ((2, 1, 1), "lit"): "true", ((3, 1, 0), "lit"): "true", ((2, 2, 0), "lit"): "true"})

fact("stone_conducts", "A plain block powered by dust (or a lever) powers the lamps next to it, above it and below it the same way.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.set((1,1,0), wire())\n    b.set((2,1,0), solid())\n    b.set((2,1,1), lamp())\n    b.set((3,1,0), lamp())\n    b.set((2,2,0), lamp())\n",
     [(0, 1, 0)], {((2, 1, 1), "lit"): "true", ((3, 1, 0), "lit"): "true", ((2, 2, 0), "lit"): "true"})

fact("torch_powers_adjacent_dust", "A torch powers dust next to it on every side (15, then 14): a wall torch and a floor torch alike.",
     "    b.set((1,1,0), solid())\n    b.set((2,1,0), torch(wall=EAST))\n    b.points(wire(), (3,1,0), (4,1,0))\n"
     "    b.set((6,1,0), torch())\n    b.points(wire(), (7,1,0), (6,1,1))\n",
     [], {((3, 1, 0), "power"): "15", ((4, 1, 0), "power"): "14", ((7, 1, 0), "power"): "15", ((6, 1, 1), "power"): "15"})

fact("torch_inverts_its_block", "A torch is switched OFF by power in the block it is mounted on (and its dust then drops to 0).",
     f"    b.set((0,1,0), {LEVER_E})\n    b.set((1,1,0), wire())\n    b.set((2,1,0), solid())\n    b.set((3,1,0), torch(wall=EAST))\n    b.points(wire(), (4,1,0), (5,1,0))\n    b.set((6,1,0), lamp())\n",
     [(0, 1, 0)], {((3, 1, 0), "lit"): "false", ((4, 1, 0), "power"): "0", ((6, 1, 0), "lit"): "false"},
     {((3, 1, 0), "lit"): "true", ((4, 1, 0), "power"): "15", ((6, 1, 0), "lit"): "true"})

fact("repeater_input_is_behind", "A repeater takes its input only from behind: dust beside it does nothing, dust behind it works (the output is full strength).",
     f"    b.set((0,1,3), {LEVER_E})\n    b.set((1,1,3), wire())\n    b.set((1,1,4), repeater(flow=EAST))\n    b.set((2,1,4), lamp())\n"
     f"    b.set((0,1,7), {LEVER_E})\n    b.set((1,1,7), wire())\n    b.set((2,1,7), repeater(flow=EAST))\n    b.set((3,1,7), lamp())\n",
     [(0, 1, 3), (0, 1, 7)], {((2, 1, 4), "lit"): "false", ((3, 1, 7), "lit"): "true"})

fact("dust_step_up", "Dust steps up onto a block from dust beside it only if the cell above the LOWER dust is not a conductor.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.set((1,1,0), wire())\n    b.points(solid(), (2,1,0), (3,1,0))\n    b.points(wire(), (2,2,0), (3,2,0))\n"
     f"    b.set((0,1,3), {LEVER_E})\n    b.set((1,1,3), wire())\n    b.points(solid(), (2,1,3), (3,1,3), (1,2,3))\n    b.points(wire(), (2,2,3), (3,2,3))\n",
     [(0, 1, 0), (0, 1, 3)], {((2, 2, 0), "power"): "14", ((3, 2, 0), "power"): "13", ((2, 2, 3), "power"): "0"})

fact("wire_path_flat_start_climbs", "wire_path from a lever that must change height starts with one flat dust, then climbs: the lamp at the end lights.",
     f"    b.set((0,1,0), {LEVER_E})\n    wire_path(b, P(0,1,0), P(5,3,0), end_exclusive=True)\n    b.set((5,3,0), lamp())\n    b.set((5,2,0), solid())\n",
     [(0, 1, 0)], {((5, 3, 0), "lit"): "true"}, {((5, 3, 0), "lit"): "false"})

fact("wire_path_branch_keeps_repeater_off_the_junction", "A branch hanging off a wire_path cell stays alive: the repeater moves to the next flat cell.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.points(wire(), (3,1,1), (3,1,2))\n    b.set((3,0,1), solid())\n    b.set((3,1,3), lamp())\n"
     "    wire_path(b, P(0,1,0), P(8,1,0), repeat_every=4)\n    b.set((9,1,0), lamp())\n",
     [(0, 1, 0)], {((3, 1, 3), "lit"): "true", ((9, 1, 0), "lit"): "true"})

fact("step_down_over_stone", "A dust line steps DOWN from dust on a stone block to dust beside it: power carries (14 -> 13 -> 12).",
     f"    b.set((0,1,0), {LEVER_E})\n    b.set((1,1,0), wire())\n    b.set((2,1,0), solid())\n    b.points(wire(), (2,2,0), (3,2,0))\n"
     "    b.points(solid(), (3,1,0))\n    b.set((4,1,0), wire())\n",
     [(0, 1, 0)], {((2, 2, 0), "power"): "14", ((3, 2, 0), "power"): "13", ((4, 1, 0), "power"): "12"})

fact("step_down_over_slab_is_dead", "...but a step down from dust that sits on a (top) slab does NOT carry, while climbing up onto the slab does.",
     f"    b.set((0,1,0), {LEVER_E})\n    b.set((1,1,0), wire())\n    b.set((2,1,0), slab(type='top'))\n    b.points(wire(), (2,2,0), (3,2,0))\n"
     "    b.set((3,1,0), slab(type='top'))\n    b.set((4,1,0), wire())\n",
     [(0, 1, 0)], {((2, 2, 0), "power"): "14", ((3, 2, 0), "power"): "13", ((4, 1, 0), "power"): "0"})


def read_prop(state, key):
    from env.sim import parse
    bid, props = parse(state)
    if key == "block":
        return bid.split(":")[-1]
    return props.get(key, "?")


def blocks_of(src):
    b = BD.compile("from simulation.builder import *\n" + src, sandbox=False)
    if not b.ok:
        raise ValueError("fact does not compile: " + "; ".join(str(e) for e in b.errors))
    return b.blocks


def plan_of(f):
    probes = sorted({k[0] for k in {**f["expect"], **f["rest"]}})
    return Plan(TICKS, [use(2, p) for p in f["clicks"]], probes, "place", 10)


def evaluate(f, trace):
    """-> (failures, readings); `rest` is read before the click (state after the idle ticks), `expect` at the end."""
    rest_state, end_state = trace.state_at(-1), trace.state_at(trace.n_ticks - 1)
    fails, got = [], {}
    for want, state, tag in ((f["rest"], rest_state, "rest"), (f["expect"], end_state, "end")):
        for (pos, key), val in want.items():
            r = read_prop(state.get(pos, "minecraft:air"), key)
            got[(tag, pos, key)] = r
            if r != val:
                fails.append(f"{tag} {pos} {key}: got {r}, expected {val}")
    return fails, got


def run_nucleation(f):
    from tools.oracle.sim import run_plan
    return run_plan(blocks_of(f["src"]), plan_of(f))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--oracle", action="store_true", help="also capture every scenario in vanilla 26.2 and compare the readings")
    a = ap.parse_args(argv)
    ids = a.ids or list(FACTS)
    bad = 0
    traces = {i: run_nucleation(FACTS[i]) for i in ids}
    vanilla = {}
    if a.oracle:
        from tools.oracle import gametest
        jobs = [(i, blocks_of(FACTS[i]["src"]), plan_of(FACTS[i])) for i in ids]
        for i, tr in zip(ids, gametest.capture_batch(jobs)):
            vanilla[i] = tr
    for i in ids:
        f = FACTS[i]
        fails, got = evaluate(f, traces[i])
        line = f"{i:48s} nucleation {'ok  ' if not fails else 'FAIL'}"
        if a.oracle:
            vf, vgot = evaluate(f, vanilla[i])
            line += f" vanilla {'ok  ' if not vf else 'FAIL'} readings {'identical' if vgot == got else 'DIFFER'}"
            bad += bool(vf) or vgot != got
            for m in vf:
                line += "\n    vanilla: " + m
        bad += bool(fails)
        for m in fails:
            line += "\n    nucleation: " + m
        print(line)
    print(f"{len(ids) - bad}/{len(ids)} facts hold")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
