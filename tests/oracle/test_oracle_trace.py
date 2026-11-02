"""Offline tests for tools/oracle (trace types, circuits on Nucleation, diff logic). No JVM needed."""
import pytest

pytest.importorskip("nucleation")

from tools.oracle.circuits import all_circuits
from tools.oracle.diff import diff_traces, region_equal_per_tick, summarize
from tools.oracle.sim import run_plan, snbt_of, structure_snbt
from tools.oracle.trace import (Plan, Trace, canon_state, first_divergence, normalize_blocks,
                                put, state_props, use)


def mk(initial, changes, n=5, backend="x"):
    return Trace(backend, "t", 0, (0, 0, 0), n, dict(initial), dict(initial), changes)


def test_canon_state_sorts_and_prefixes():
    assert canon_state("lever[powered=false,face=floor]") == "minecraft:lever[face=floor,powered=false]"
    assert canon_state("stone") == "minecraft:stone"
    assert state_props("minecraft:repeater[delay=2,facing=west]") == {"delay": "2", "facing": "west"}


def test_normalize_blocks_drops_air_and_accepts_dicts():
    out = normalize_blocks([(0, 0, 0, "air"), {"x": 1, "y": 0, "z": 0, "state": "stone"}])
    assert out == [(1, 0, 0, "minecraft:stone", None)]


def test_snbt_of():
    assert snbt_of({"Items": [{"Slot": 0, "id": "minecraft:stone", "count": 3}]}) == \
        '{Items:[{Slot:0,id:"minecraft:stone",count:3}]}'


def test_trace_state_and_probe():
    p = (0, 0, 0)
    t = mk({p: "minecraft:lamp[lit=false]"}, {1: [(p, "minecraft:lamp[lit=false]", "minecraft:lamp[lit=true]")],
                                              3: [(p, "minecraft:lamp[lit=true]", "minecraft:air")]})
    assert t.state_at(-1)[p].endswith("false]")
    assert t.state_at(2)[p].endswith("true]")
    assert p not in t.state_at(3)
    assert [s.split("[")[-1] for s in t.probe(p)[:3]] == ["lit=false]", "lit=true]", "lit=true]"]
    assert Trace.from_json(t.to_json()).changes == t.changes


def test_first_divergence_and_equal_per_tick():
    p = (0, 0, 0)
    a = mk({p: "minecraft:stone"}, {2: [(p, "minecraft:stone", "minecraft:dirt")]})
    b = mk({p: "minecraft:stone"}, {3: [(p, "minecraft:stone", "minecraft:dirt")]})
    assert first_divergence(a, a) is None
    assert first_divergence(a, b)[0] == 2
    assert region_equal_per_tick(a, b) == [True, True, False, True, True]


def test_diff_traces_summary():
    p = (0, 0, 0)
    a = mk({p: "minecraft:stone"}, {})
    plan = Plan(5, probes=[p])
    r = diff_traces("c", "m", a, a, plan)
    assert r.agree_full and r.agree_probes
    assert summarize([r])["m"]["agree_full"] == 1


def test_structure_snbt_vanilla_flavour():
    text, shift = structure_snbt([(3, 5, 7, "minecraft:stone"),
                                  (4, 5, 7, "minecraft:chest[facing=east,type=single,waterlogged=false]",
                                   '{Items:[{Slot:0b,id:"minecraft:stone",count:2}]}')], 4903)
    assert shift == (-3, -5, -7)
    assert "DataVersion:4903" in text and 'Name:"minecraft:chest"' in text and "Properties:{" in text
    assert 'nbt:{id:"minecraft:chest",Items:' in text and "size:[2,1,1]" in text


def test_circuits_are_well_formed_and_unique():
    cs = all_circuits()
    assert len(cs) >= 10 and len({c.name for c in cs}) == len(cs)
    for c in cs:
        assert c.blocks and c.plan.ticks > 0
        cells = [b[:3] for b in c.blocks]
        assert len(cells) == len(set(cells)), f"{c.name}: overlapping blocks"
        assert all(o.pos is not None and 0 <= o.tick < c.plan.ticks for o in c.plan.ops)


@pytest.mark.parametrize("name", ["lever_wire_lamp", "wire_vertical", "repeater_chain", "button_lamp",
                                  "torch_not_gate", "sticky_piston_lever"])
def test_baseline_circuits_behave_in_nucleation(name):
    c = next(c for c in all_circuits() if c.name == name)
    tr = run_plan(c.blocks, c.plan)
    assert c.expect
    for tick, pos, sub in c.expect:
        assert sub in tr.state_at(tick).get(pos, "air"), (name, tick, pos)


def test_sim_is_deterministic():
    c = next(c for c in all_circuits() if c.name == "observer_pair")
    a, b = run_plan(c.blocks, c.plan), run_plan(c.blocks, c.plan)
    assert a.changes == b.changes
