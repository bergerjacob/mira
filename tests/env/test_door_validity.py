"""Round-2 audit H1: the 2x2 door must need a pushed non-piston full block, not a sticky piston's own head.

`tests/fixtures/door2x2_piston_head_exploit.*` is the strong-agent session build (data/sessions/t2_door_2x2-builder-R15-*):
its four sticky pistons extend their heads into the door cells and push nothing. It passed the first version of the
task (`region_mode: solid` = not air); it must fail now.
"""
import copy
import dataclasses
import json
from pathlib import Path

import pytest

import simulation.builder as BD
from env.contract import Port
from env.mutate import apply
from env.runner import read_port, run
from env.score import score
from env.task import load_task

FIX = Path(__file__).resolve().parents[1] / "fixtures"
EXPLOIT = [tuple(b) for b in json.loads((FIX / "door2x2_piston_head_exploit.json").read_text())]


@pytest.fixture(scope="module")
def task():
    return load_task("t2_door_2x2")


def test_exploit_fixture_is_the_builder_program_output():
    b = BD.compile((FIX / "door2x2_piston_head_exploit.py.txt").read_text(), sandbox=False)
    assert sorted((x, y, z, s) for x, y, z, s, _ in b.blocks) == sorted(EXPLOIT)
    assert not any(z == 3 and (x, y) in {(0, 2), (1, 2), (0, 3), (1, 3)} for x, y, z, s in EXPLOIT)   # nothing pushed or placed in the cells


def test_reference_passes(task):
    sc = score(task.contract, run(task.contract, task.solution, seed=0))
    assert sc.full_pass and sc.reward == 1.0


def test_piston_head_exploit_now_fails(task):
    res = run(task.contract, EXPLOIT, seed=0)
    sc = score(task.contract, res)
    assert not sc.full_pass and sc.r3 == 0.0
    bad = {w.port for q in res.sequences for w in q.windows if not w.ok}
    assert {"D00", "D01", "D10", "D11"} <= bad            # no cell ever holds a real block


def test_the_exploit_did_pass_the_old_not_air_spec(task):
    """Meta-test: with the old ports (4 x 'solid', no air ports) the same build passes, so the test above has teeth."""
    c = copy.deepcopy(task.contract)
    c.ports = [p for p in c.ports if not p.name.startswith("A")]
    for p in c.ports:
        if p.name.startswith("D"):
            p.region_mode, p.blocks = "solid", None
    for s in c.behaviour.sequences:
        s.expect = [e for e in s.expect if not e.port.startswith("A")]
    sc = score(c, run(c, EXPLOIT, seed=0))
    assert sc.full_pass


def test_stored_broken_variants_fail(task):
    assert len(task.broken) >= 3
    for b in task.broken:
        res = run(task.contract, apply(task.solution, b["edits"]), seed=0)
        assert not res.prepared.errors, b["name"]                 # the variant is a valid build that misbehaves
        assert not score(task.contract, res).full_pass, b["name"]


def test_every_door_cell_has_a_block_in_port_and_an_air_port(task):
    ports = {p.name: p for p in task.contract.ports if p.kind == "region"}
    assert sorted(ports) == ["A00", "A01", "A10", "A11", "D00", "D01", "D10", "D11"]
    for n, p in ports.items():
        assert p.region_mode == ("block_in" if n[0] == "D" else "air")
    allow = ports["D00"].blocks
    assert "minecraft:stone" in allow and not [b for b in allow if "piston" in b]


def _read(mode_kwargs, world):
    class S:
        def get(self, pos):
            return world.get(tuple(pos), "minecraft:air")
    return read_port(S(), Port("P", "region", (0, 0, 0), box=[0, 0, 0, 0, 0, 0], **mode_kwargs))


@pytest.mark.parametrize("state,solid,block_in", [
    ("minecraft:air", 0, 0),
    ("minecraft:stone", 1, 1),
    ("minecraft:piston_head[facing=up,short=false,type=sticky]", 1, 0),
    ("minecraft:moving_piston[facing=up,type=sticky]", 1, 0),
    ("minecraft:sticky_piston[extended=false,facing=up]", 1, 0),
    ("minecraft:redstone_wire", 1, 0),
])
def test_region_modes_on_single_cell(state, solid, block_in):
    w = {(0, 0, 0): state}
    assert _read({"region_mode": "solid"}, w) == solid
    assert _read({"region_mode": "block_in", "blocks": ["minecraft:stone", "minecraft:dirt"]}, w) == block_in


def test_block_in_needs_an_allowlist(task):
    c = copy.deepcopy(task.contract)
    next(p for p in c.ports if p.name == "D00").blocks = None
    assert any("block_in needs" in e for e in c.validate())
    assert not task.contract.validate()


def test_port_roundtrips_with_blocks(task):
    d = task.contract.to_dict()
    assert Port.from_dict(next(p for p in d["ports"] if p["name"] == "D00")) == next(p for p in task.contract.ports if p.name == "D00")
    assert "blocks" in {f.name for f in dataclasses.fields(Port)}
