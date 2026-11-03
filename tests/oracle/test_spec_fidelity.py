"""Offline tests for scripts/spec_fidelity.py (Experiment 05 hidden-spec harness). No JVM; the pilot-project tests skip when
/tmp/freeform_* (the exported hand-over worlds) are gone."""
import importlib.util
from pathlib import Path

import pytest

from tools.oracle.trace import Trace

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("spec_fidelity", ROOT / "scripts/spec_fidelity.py")
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

have_door = Path(F.PROJECTS["door"]["dir"], F.PROJECTS["door"]["litematic"]).exists()


def _trace(initial, changes, n):
    return Trace("t", "t", 0, (0, 0, 0), n, dict(initial), dict(initial), changes)


def test_alu_model():
    assert F.alu_expect(9, 9, 0) == (2, 1)           # 18 = 0b1_0010
    assert F.alu_expect(12, 10, 1) == (8, 0)
    assert F.alu_expect(12, 10, 2) == (14, 0)
    assert F.alu_expect(12, 10, 3) == (6, 0)


def test_piston_consistency():
    ok = {(0, 0, 0): "minecraft:sticky_piston[extended=true,facing=east]", (1, 0, 0): "minecraft:piston_head[facing=east,type=sticky]"}
    assert F.piston_consistency(ok) == 1
    assert F.piston_consistency({(0, 0, 0): ok[(0, 0, 0)]}) == 0                                 # extended without a head
    assert F.piston_consistency({(1, 0, 0): ok[(1, 0, 0)]}) == 0                                 # head without a base
    assert F.piston_consistency({(0, 0, 0): "minecraft:moving_piston[facing=east,type=normal]"}) == 0
    assert F.piston_consistency({(0, 0, 0): "minecraft:sticky_piston[extended=false,facing=east]"}) == 1


def test_plain_full():
    assert F.plain_full("minecraft:stone") and F.plain_full("minecraft:oak_planks")
    for s in ("minecraft:air", "minecraft:piston_head[facing=east]", "minecraft:smooth_stone_slab", "minecraft:redstone_lamp[lit=false]",
              "minecraft:stone_button[face=wall]"):
        assert not F.plain_full(s)


def test_ctx_edges_and_windows():
    btn = (0, 0, 0)
    off, on = "minecraft:stone_button[powered=false]", "minecraft:stone_button[powered=true]"
    tr = _trace({btn: off}, {2: [(btn, off, on)], 22: [(btn, on, off)], 40: [(btn, off, on)]}, 60)
    preds = {"in:BTN": F.powered_pred(btn)}
    ctx = F.Ctx(tr, preds, ["in:BTN"], {}, {})
    assert ctx.edges("BTN") == [3, 41]                       # state after 3 / 41 ticks
    assert ctx.all_eq("in:BTN", 3, 22, 1) == (True, None, None)
    assert ctx.all_eq("in:BTN", 3, 23, 1)[:2] == (False, 23)


def test_rle_and_first_diff():
    assert F.rle([0, 0, 1, 1, 0]) == "0@0 1@2 0@4"
    assert F.first_diff("0@0 1@2", "0@0 1@3") == (2, "1", "0")
    assert F.first_diff("0@0", "0@0") is None


@pytest.mark.skipif(not have_door, reason="exported pilot world /tmp/freeform_tff_door not available")
def test_door_hidden_spec_on_pilot_and_rejects_mutant():
    w = F.load_world("door")
    spec = F.door_spec(w)
    scn = next(s for s in spec.scns if s.name == "gap_61")
    be = F.EnvBackend(w)
    rows, rle = F.evaluate(spec, scn, be.run(F.plan_of(w, scn, spec.in_ports)))
    assert all(r["ok"] for r in rows if r["tier"] == "core"), rows
    assert "FULL" in rle
    mut = F.mutants("door", w)["remove_a_piston"]
    spec2 = F.door_spec(mut)
    rows2, _ = F.evaluate(spec2, scn, F.EnvBackend(mut).run(F.plan_of(mut, scn, spec2.in_ports)))
    assert any(not r["ok"] for r in rows2 if r["tier"] == "core")


@pytest.mark.skipif(not have_door, reason="exported pilot world /tmp/freeform_tff_door not available")
def test_rotated_world_keeps_ports_bound_by_label():
    w = F.load_world("door")
    w2 = F.transform(w, 90, None)
    p = w2.ports["BTN"]
    assert w2.cells[tuple(p.pos)].startswith("minecraft:stone_button") and f"facing={p.facing}" in w2.cells[tuple(p.pos)]
    spec = F.door_spec(w2)
    scn = next(s for s in spec.scns if s.name == "gap_40")
    rows, _ = F.evaluate(spec, scn, F.EnvBackend(w2).run(F.plan_of(w2, scn, spec.in_ports)))
    assert all(r["ok"] for r in rows if r["tier"] == "core"), rows
