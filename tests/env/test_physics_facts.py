"""Redstone facts quoted in evaluation/prompts/builder_system.md, docs/BUILDER.md and docs/T4.md: every claim is a tiny simulation here.

The scenarios live in scripts/t4_physics_facts.py (also runnable against the vanilla 26.2 oracle: `--oracle`; the live class below
re-checks them there when the oracle is installed). Offline otherwise (Nucleation only)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "scripts"))

import t4_physics_facts as PF  # noqa: E402
from env import blocks as EB  # noqa: E402
from env.contract import Port  # noqa: E402
from env.runner import prepare, read_port  # noqa: E402
from env.sim import Sim, compile_cells, parse  # noqa: E402
from env.task import load_task  # noqa: E402
from simulation import builder as BD  # noqa: E402


@pytest.mark.parametrize("fid", sorted(PF.FACTS))
def test_fact_holds_in_nucleation(fid):
    fails, _ = PF.evaluate(PF.FACTS[fid], PF.run_nucleation(PF.FACTS[fid]))
    assert not fails, PF.FACTS[fid]["claim"] + "\n" + "\n".join(fails)


def test_every_fact_has_a_claim_and_a_probe_in_both_states_where_it_matters():
    assert len(PF.FACTS) >= 15
    for f in PF.FACTS.values():
        assert f["claim"] and f["expect"] and f["src"].startswith("def build(b):")
    # facts with a lit/powered end state also prove the rest state is the opposite (the click matters)
    assert PF.FACTS["lever_adjacent_dust"]["rest"] and PF.FACTS["torch_inverts_its_block"]["rest"]


# --------------------------------------------------------------------------- the same facts in the vanilla oracle
from tools.oracle import gametest  # noqa: E402


@pytest.mark.skipif(not gametest.available(), reason="vanilla oracle not installed")
def test_all_facts_agree_with_vanilla_26_2():
    ids = sorted(PF.FACTS)
    traces = gametest.capture_batch([(i, PF.blocks_of(PF.FACTS[i]["src"]), PF.plan_of(PF.FACTS[i])) for i in ids])
    for i, tr in zip(ids, traces):
        fails, got = PF.evaluate(PF.FACTS[i], tr)
        n_fails, n_got = PF.evaluate(PF.FACTS[i], PF.run_nucleation(PF.FACTS[i]))
        assert not fails and got == n_got, (i, fails, got, n_got)


# --------------------------------------------------------------------------- sandbox: what a program may contain
SANDBOX_OK = '''
from simulation.builder import *
import itertools
from functools import partial, reduce

LEN = 3

def column(b, x, h):                       # module-level helper functions are fine
    for y in range(1, h + 1):
        b.set((x, y, 0), solid())

SQ = lambda v: v * v                       # module-level lambda

def build(b):
    def row(z):                            # nested def
        return [(x, 1, z) for x in range(LEN)]
    pick = lambda p: p                     # nested lambda
    for z in range(2):
        for p in row(z):
            b.set(pick(p), glass())
    column(b, 5, SQ(2))
    total = reduce(lambda a, c: a + c, [1, 2, 3])
    b.set((10, 1, total), lamp())
    partial(column, b)(12, 2)
    names = {k: v for k, v in zip("ab", itertools.count())}
    b.set((13, 1, names["b"]), glass())
'''


def test_sandbox_allows_nested_defs_lambdas_module_functions_and_comprehensions():
    build = BD.compile(SANDBOX_OK, sandbox=True)
    assert build.ok, build.errors
    assert len(build.blocks) == 6 + 4 + 1 + 2 + 1


@pytest.mark.parametrize("snippet,word", [("class A:\n    pass\n", "ClassDef"), ("x = getattr(b, 'set')\n", "getattr"),
                                          ("import os\n", "os"), ("y = b._cells\n", "_cells"), ("try:\n    pass\nexcept:\n    pass\n", "bare except")])
def test_sandbox_still_forbids_classes_getattr_imports_underscores_and_bare_except(snippet, word):
    src = "from simulation.builder import *\n\ndef build(b):\n" + "".join("    " + line + "\n" for line in snippet.splitlines()) + "    b.set((0, 0, 0), stone())\n"
    build = BD.compile(src, sandbox=True)
    assert not build.ok and word in str(build.errors[0])


# --------------------------------------------------------------------------- support rules (env.blocks.floating = the R1 no_popoff check)
def floating(states):
    world = {pos: EB.canonical(st) for pos, st in states.items()}
    return {pos for pos, _, _ in EB.floating(world)}


def test_plain_blocks_and_lamps_need_no_support_but_dust_repeaters_comparators_torches_levers_do():
    free = {(0, 5, 0): "minecraft:stone", (2, 5, 0): "minecraft:redstone_lamp", (4, 5, 0): "minecraft:redstone_block",
            (6, 5, 0): "minecraft:glass", (8, 5, 0): "minecraft:piston[facing=up]", (10, 5, 0): "minecraft:target"}
    assert floating(free) == set()
    needy = {(0, 5, 0): "minecraft:redstone_wire", (2, 5, 0): "minecraft:repeater[facing=east]", (4, 5, 0): "minecraft:comparator[facing=east]",
             (6, 5, 0): "minecraft:redstone_torch", (8, 5, 0): "minecraft:lever[face=floor]"}
    assert floating(needy) == set(needy)
    on_stone = {(x, 0, 0): "minecraft:stone" for x in range(0, 10, 2)}
    on_stone.update({(x, 1, 0): s for (x, _, _), s in needy.items()})
    assert floating(on_stone) == set()


def test_only_a_top_slab_holds_dust_up_not_a_bottom_slab_and_wall_attachments_need_their_block():
    assert floating({(0, 0, 0): "minecraft:stone_slab[type=top]", (0, 1, 0): "minecraft:redstone_wire"}) == set()
    assert floating({(0, 0, 0): "minecraft:stone_slab[type=bottom]", (0, 1, 0): "minecraft:redstone_wire"}) == {(0, 1, 0)}
    assert floating({(1, 1, 0): "minecraft:stone", (2, 1, 0): "minecraft:redstone_wall_torch[facing=east]"}) == set()
    assert floating({(2, 1, 0): "minecraft:redstone_wall_torch[facing=east]"}) == {(2, 1, 0)}
    assert floating({(1, 1, 0): "minecraft:redstone_lamp", (0, 1, 0): "minecraft:lever[face=wall,facing=west]"}) == set()


def test_the_simulator_does_not_pop_floating_dust_but_the_env_reports_it():
    """Nucleation keeps unsupported dust (vanilla would drop it): the env's static check is what rejects it."""
    cells = {(x, 0, z): "minecraft:smooth_stone" for x in range(4) for z in range(3)}
    cells[(1, 4, 1)] = "minecraft:redstone_wire"
    sc, origin, errs = compile_cells(cells)
    sim = Sim(sc, origin, "inworld")
    sim.settle(20)
    assert parse(sim.get((1, 4, 1)))[0] == "minecraft:redstone_wire"          # still there
    assert [p for p, _, _ in EB.floating({**cells, (1, 4, 1): EB.canonical("minecraft:redstone_wire")})] == [(1, 4, 1)]


# --------------------------------------------------------------------------- region ports: air / solid, init, pistons
PISTON = """
def build(b):
    b.fill((0, 0, -3), (8, 0, 6), solid())
    b.set((0, 1, 0), lever(face="floor", facing=EAST))
    b.points(wire(), (1, 1, 0), (2, 1, 0))
    b.set((3, 1, 0), sticky_piston(UP))
"""


def piston_sim():
    build = BD.compile("from simulation.builder import *\n" + PISTON, sandbox=False)
    cells = {(x, y, z): s for x, y, z, s, _ in build.blocks}
    sc, origin, _ = compile_cells(cells)
    sim = Sim(sc, origin, "inworld")
    sim.settle(40)
    return sim


def test_extended_piston_head_and_moving_piston_count_as_solid_but_not_as_a_pushed_block():
    """region_mode `solid` = any non-air block (head and moving_piston included); `block_in` = a real pushed block from an allowlist.
    This is why the door ports are block_in/air since the validity audit: a sticky piston's own head must not close a door."""
    cell = (3, 2, 0)
    air = Port("air", "region", cell, box=[*cell, *cell], region_mode="air")
    solid = Port("solid", "region", cell, box=[*cell, *cell], region_mode="solid")
    pushed = Port("pushed", "region", cell, box=[*cell, *cell], region_mode="block_in", blocks=["minecraft:stone"])
    sim = piston_sim()
    assert parse(sim.get(cell))[0] == "minecraft:air" and (read_port(sim, air), read_port(sim, solid), read_port(sim, pushed)) == (1, 0, 0)
    sim.use((0, 1, 0))
    seen = []
    for _ in range(6):
        sim.step()
        seen.append(parse(sim.get(cell))[0].split(":")[1])
        assert read_port(sim, solid) == 1 and read_port(sim, pushed) == 0          # moving_piston / piston_head: solid, but not a block
    assert "moving_piston" in seen and seen[-1] == "piston_head"
    sim.settle(40)
    assert (read_port(sim, air), read_port(sim, solid), read_port(sim, pushed)) == (0, 1, 0)


def test_door_reference_rests_open_with_air_and_closes_with_real_blocks_not_heads():
    task = load_task("t2_door_2x2")
    c = task.contract
    exp = [e.value for s in c.behaviour.sequences for e in s.expect]
    assert "init" in exp and "!init" in exp                                    # polarity-agnostic: the door may rest open or closed
    pr = prepare(c, task.solution)
    sc, origin, _ = compile_cells(pr.world, pr.nbt)
    sim = Sim(sc, origin, "inworld")
    sim.settle(c.budgets.rest)
    closed = [p for p in c.ports if p.kind == "region" and p.region_mode == "block_in"]
    opened = [p for p in c.ports if p.kind == "region" and p.region_mode == "air"]
    assert len(closed) == 4 and len(opened) == 4
    assert [read_port(sim, p) for p in closed] == [0] * 4 and [read_port(sim, p) for p in opened] == [1] * 4     # init: air at rest
    sim.use((2, 1, 2))
    sim.settle(c.budgets.rest)
    assert [read_port(sim, p) for p in closed] == [1] * 4 and [read_port(sim, p) for p in opened] == [0] * 4
    assert all(parse(sim.get(tuple(p.pos)))[0] != "minecraft:piston_head" for p in closed)


# --------------------------------------------------------------------------- wire_path against the simulator
def test_wire_path_from_a_lever_that_climbs_works_and_the_old_diagonal_shape_does_not():
    ok = PF.FACTS["wire_path_flat_start_climbs"]
    assert not PF.evaluate(ok, PF.run_nucleation(ok))[0]
    diag = dict(PF.FACTS["lever_no_diagonal"])
    assert diag["expect"][((1, 2, 0), "power")] == "0"                         # the shape wire_path used to emit
