"""Offline tests: Nucleation engine-placement dust bug (env/sim.compile_cells) and the order-independent placement prototype.

Vanilla 26.2 reference for the repro (tests/oracle/test_robustness_live.py, needs the oracle): hand-placing lever + two dust + an
unrelated dust two cells away in env's (y, x, z) order leaves the line end a line; Nucleation's `{simulate=true}` placement turns it
into a cross.
"""
import random

import pytest

from env import runner
from env.score import score
from env.sim import Sim, compile_cells
from env.task import load_task
from tools.oracle import placement

LEVER = "minecraft:lever[face=floor,facing=east,powered=false]"
DUST = "minecraft:redstone_wire"
LINE = "minecraft:redstone_wire[east=side,north=none,power=0,south=none,west=side]"


def repro(extra=((4, 1, 4),)):
    cells = {(x, 0, z): "minecraft:stone" for x in range(6) for z in range(0, 9)}
    cells[(0, 1, 3)] = LEVER
    for p in [(1, 1, 3), (2, 1, 3), *extra]:
        cells[p] = DUST
    return cells


def settled(compile_fn, cells, mode="inworld"):
    sc, o, _ = compile_fn(cells)
    s = Sim(sc, o, mode)
    s.settle(100)
    return s.snapshot()


def test_engine_placement_without_neighbour_dust_is_right():
    assert settled(compile_cells, repro(()))[(2, 1, 3)] == LINE


@pytest.mark.xfail(strict=True, reason="Nucleation 0.12.0 Schematic.set_block{simulate=true}: a later dust at (+2, +-1..2) "
                                       "turns a line end into a cross (vanilla keeps the line); docs/ORACLE.md bug 6")
def test_engine_placement_unrelated_dust_keeps_line_end():
    assert settled(compile_cells, repro())[(2, 1, 3)] == LINE


@pytest.mark.parametrize("dz", [-2, -1, 1, 2])
def test_engine_placement_bug_offsets(dz):
    """The offsets measured to corrupt the line end (documents the bug's footprint; flips when Nucleation is fixed)."""
    assert settled(compile_cells, repro(((4, 1, 3 + dz),)))[(2, 1, 3)] != LINE


def test_canonical_compile_resolves_shapes_like_vanilla():
    w = settled(placement.canonical_compile, repro(), "placement")
    assert w[(1, 1, 3)] == LINE and w[(2, 1, 3)] == LINE
    assert w[(4, 1, 4)] == placement.CROSS          # isolated dust: a cross, as a player places it


def test_canonical_compile_is_order_independent():
    cells = repro(((4, 1, 4), (3, 1, 5), (1, 1, 5), (1, 1, 6)))
    ref = settled(placement.canonical_compile, cells, "placement")
    keys = list(cells)
    for seed in range(3):
        random.Random(seed).shuffle(keys)
        assert settled(placement.canonical_compile, {k: cells[k] for k in keys}, "placement") == ref


def test_cross_dust_only_touches_wire():
    out = placement.cross_dust({(0, 0, 0): LINE, (1, 0, 0): LEVER})
    assert out == {(0, 0, 0): placement.CROSS, (1, 0, 0): LEVER}


def test_patched_env_is_process_local_and_restored():
    before = (runner.compile_cells, runner.Sim)
    with placement.patched_env():
        assert runner.compile_cells is placement.canonical_compile and runner.Sim is placement.CanonicalSim
    assert (runner.compile_cells, runner.Sim) == before


def _verdicts(tid, seed):
    from env.variants import variant_of
    t = variant_of(load_task(tid), seed)
    env_ok = score(t.contract, runner.run(t.contract, t.solution, fast=True)).full_pass
    with placement.patched_env():
        can_ok = score(t.contract, runner.run(t.contract, t.solution, fast=True)).full_pass
    return env_ok, can_ok


@pytest.mark.parametrize("tid,seed", [("t1_xor", 4), ("t1_clock", 2), ("t1_t_flipflop", 6), ("t3_counter2", 2)])
def test_rotation_failures_are_placement_artefacts(tid, seed):
    """env fails these rotated/mirrored references; the canonical placement passes them (vanilla passes them too)."""
    assert _verdicts(tid, seed) == (False, True)


@pytest.mark.parametrize("tid", ["t1_xor", "t1_clock", "t3_counter2"])
def test_canonical_placement_keeps_identity_passes(tid):
    assert _verdicts(tid, 0) == (True, True)


def test_canonical_broken_variant_that_works_when_hand_built():
    """t1_repeater_lock 'replace wire [2,1,2] with stone' leaves an isolated dust at [3,1,2]: as a dot (env) it powers nothing
    sideways and the variant fails; hand-placed it is a cross (vanilla, verified with the oracle's `place` op) and the
    'broken' variant works. So canonical placement passing it is correct, the stored variant is not a real negative."""
    t = load_task("t1_repeater_lock")
    b = next(b for b in t.broken if b["name"].startswith("replace wire"))
    cells = {tuple(x[:3]): list(x) for x in t.solution}
    for e in b["edits"]:
        cells[tuple(e[1])] = [*e[1], e[2]]
    blocks = list(cells.values())
    assert not score(t.contract, runner.run(t.contract, blocks, fast=True)).full_pass
    with placement.patched_env():
        assert score(t.contract, runner.run(t.contract, blocks, fast=True)).full_pass
