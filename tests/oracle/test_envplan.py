"""Offline tests: env-mirrored oracle replay (tools/oracle/envplan.py), run_plan fast path, origins, the counter4 root cause.

No JVM needed; Nucleation only. The t3_counter4 session discrepancy (env pass, old replay fail) is pinned here: the old replay pasted
the RAW program output, whose bistable flip-flops come up at count 14; env scores its own hand-placed at-rest world (count 0).
"""
import importlib.util
import json
import random
from pathlib import Path

import pytest

from env import runner
from env.task import load_task
from tools.oracle import envplan, gametest
from tools.oracle.circuits import all_circuits
from tools.oracle.sim import run_plan
from tools.oracle.trace import Plan, Trace, canon_state, place, put, use

ROOT = Path(__file__).resolve().parents[2]
COUNTER4 = ROOT / "data/sessions/t3_counter4-builder-R20-1102-054924-af52"
ADDER2 = ROOT / "data/sessions/t3_adder2-builder-R15-1102-013746-4b87"


def _session(d):
    st = json.loads((d / "state.json").read_text())
    c = load_task(st.get("task_ref") or st["task_id"]).contract
    return c, json.loads((d / "final_blocks.json").read_text())


def _mod(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ schedule / plan construction
def test_env_schedule_comb_is_seeded_shuffle():
    c = load_task("t1_xor").contract
    order, reset = envplan.env_schedule(c)
    want = list(range(len(c.vectors())))
    random.Random(c.eval_seed).shuffle(want)
    assert order == want and not reset


def test_env_schedule_seq_keeps_authoring_order():
    c = load_task("t3_counter4").contract
    order, _ = envplan.env_schedule(c)
    assert order == list(range(len(c.vectors())))


def test_captures_cumulative_vector_ticks():
    c = load_task("t0_and").contract
    n = len(c.vectors())
    ticks = {i: 3 + i for i in range(n)}
    (label, plan, checks), = envplan.captures(c, vec_ticks=ticks)
    assert label == "vectors"
    assert plan.ticks == sum(ticks.values())
    ends = sorted({chk[0] for chk in checks})
    assert ends == [sum(ticks[j] for j in range(i + 1)) for i in range(n)]


def test_captures_custom_order_applies_ops_in_that_order():
    c = load_task("t0_and").contract
    order = list(reversed(range(len(c.vectors()))))
    (_, plan, checks), = envplan.captures(c, hold=10, order=order)
    assert checks[0][3].startswith(f"vec{order[0]}:")


def test_reset_between_gives_one_capture_per_vector():
    c = load_task("t0_and").contract
    caps = envplan.captures(c, hold=10, reset=True)
    assert len(caps) == len(c.vectors()) and all(p.ticks == 10 for _, p, _ in caps)


def test_sequences_are_own_captures():
    c = load_task("t1_clock").contract
    caps = envplan.captures(c)
    assert [l for l, _, _ in caps] == [f"seq:{s.name}" for s in c.behaviour.sequences]


def test_levels_reads_initial_lever_state_from_world():
    c = load_task("t0_and").contract
    a = c.port("A")
    world = [(*a.pos, "minecraft:lever[face=floor,facing=east,powered=true]")]
    lv = envplan.Levels(c, world)
    lv.set(0, "A", 1)          # already on: no click
    lv.set(5, "A", 0)
    assert [(o.tick, o.op) for o in lv.ops] == [(5, "use")]


def test_levels_pad_and_pulse():
    c = load_task("t0_pad_lamp").contract
    pad = next(p for p in c.inputs if p.kind == "pad")
    lv = envplan.Levels(c)
    lv.set(0, pad.name, 1)
    lv.set(1, pad.name, 1)
    lv.set(2, pad.name, 0)
    assert [(o.tick, o.block) for o in lv.ops] == [(0, "minecraft:redstone_block"), (2, "minecraft:air")]


def test_env_captures_needs_every_vector():
    c = load_task("t0_and").contract
    rr = runner.run(c, load_task("t0_and").solution, public_only=True, fast=True)
    hidden = [v for v in c.vectors() if not v.public]
    if hidden:
        with pytest.raises(ValueError):
            envplan.env_captures(c, rr)
    else:
        assert envplan.env_captures(c, rr)


def test_env_origin_puts_cells_where_env_has_them():
    t = load_task("t0_and")
    pr = runner.prepare(t.contract, t.solution)
    world = envplan.raw_world(pr)
    assert envplan.env_origin(pr, world) == (0, 0, 0)
    shifted = [b for b in world if b[1] > 0]           # a world whose min corner is one layer up
    assert envplan.env_origin(pr, shifted) == (0, 1, 0)


# ------------------------------------------------------------------ read / evaluate
def _trace(initial, changes, n):
    return Trace("t", "t", 0, (0, 0, 0), n, dict(initial), dict(initial), changes)


def test_port_series_and_evaluate_match_state_at():
    c = load_task("t0_and").contract
    y = tuple(c.port("Y").pos)
    on, off = "minecraft:redstone_lamp[lit=true]", "minecraft:redstone_lamp[lit=false]"
    tr = _trace({y: off}, {3: [(y, off, on)], 7: [(y, on, off)]}, 10)
    ser = envplan.port_series(c, tr)["Y"]
    assert len(ser) == 11 and ser == [envplan.read_out(c, tr.state_at(i - 1), "Y") for i in range(11)]
    checks = [(4, "Y", 1, "a"), (8, "Y", 1, "b"), (2, "Y", "!init", "c")]
    assert envplan.evaluate(c, tr, checks) == ["b@8", "c@2"]


def test_read_out_region_modes():
    c = load_task("t2_door_2x2").contract
    d = next(p for p in c.outputs if p.kind == "region" and p.region_mode == "block_in")
    a = next(p for p in c.outputs if p.kind == "region" and p.region_mode == "air")
    from env.contract import box_cells
    stone = {cell: "minecraft:stone" for cell in box_cells(d.box)}
    head = {cell: "minecraft:piston_head[facing=up,short=false,type=sticky]" for cell in box_cells(d.box)}
    assert envplan.read_out(c, stone, d.name) == 1 and envplan.read_out(c, head, d.name) == 0
    assert envplan.read_out(c, {}, a.name) == 1


def test_oracle_env_diff_reexports_envplan():
    m = _mod("oed", "scripts/oracle_env_diff.py")
    assert m.evaluate is envplan.evaluate and m.read_out is envplan.read_out
    c = load_task("t1_xor").contract
    assert [l for l, _, _ in m.captures(c, 40, 20)] == [l for l, _, _ in envplan.captures(c, 40, 20)]


# ------------------------------------------------------------------ trace plumbing
def test_run_plan_fast_path_equals_full_scan():
    for circ in all_circuits(repros=True)[:12]:
        a, b = run_plan(circ.blocks, circ.plan), run_plan(circ.blocks, circ.plan, full_scan=True)
        assert a.to_json()["changes"] == b.to_json()["changes"], circ.name


def test_plan_origin_is_used_and_serialised():
    circ = all_circuits()[0]
    p = Plan(circ.plan.ticks, circ.plan.ops, circ.plan.probes, "place", 0, (5, 64, -9))
    assert run_plan(circ.blocks, p).origin == (5, 64, -9)
    assert p.to_json()["origin"] == [5, 64, -9]
    job, _ = gametest._job_json("x", circ.blocks, p)
    assert job["origin"] == [5, 64, -9]
    job2, _ = gametest._job_json("x", circ.blocks, circ.plan)
    assert "origin" not in job2


def test_place_op_canonicalises_and_runs_in_nucleation():
    op = place(0, (1, 1, 0), "redstone_wire[west=side,east=side]")
    assert op.op == "place" and op.block == canon_state("minecraft:redstone_wire[east=side,west=side]")
    floor = [(x, 0, 0, "minecraft:stone") for x in range(3)] + [(0, 1, 0, "minecraft:lever[face=floor,facing=east,powered=false]")]
    tr = run_plan(floor, Plan(2, [place(0, (1, 1, 0), "redstone_block")], [], "place", 0))
    assert tr.state_at(1)[(1, 1, 0)] == "minecraft:redstone_block"
    # Nucleation's TickSimulation.place_block refuses redstone_wire (NotFound): the `place` op is vanilla-only for dust
    with pytest.raises(Exception):
        run_plan(floor, Plan(2, [op], [], "place", 0))


def test_put_and_use_unchanged():
    assert put(0, (0, 0, 0), "air").block == "minecraft:air" and use(1, (0, 0, 0)).op == "use"


# ------------------------------------------------------------------ the counter4 root cause
@pytest.mark.skipif(not COUNTER4.exists(), reason="session data not present")
def test_counter4_raw_paste_comes_up_in_another_state():
    c, blocks = _session(COUNTER4)
    pr = runner.prepare(c, blocks)
    hand = {tuple(b[:3]): b[3] for b in envplan.handover_world(c, pr)}
    raw = run_plan(envplan.raw_world(pr), Plan(1, [], [], "place", 20, (0, 0, 0))).initial
    q = {p.name: (envplan.read_out(c, hand, p.name), envplan.read_out(c, raw, p.name)) for p in c.outputs}
    assert all(h == 0 for h, _ in q.values())                     # env / handover: count 0
    assert [q[k][1] for k in ("Q0", "Q1", "Q2", "Q3")] == [0, 1, 1, 1]   # raw paste: count 14


@pytest.mark.skipif(not COUNTER4.exists(), reason="session data not present")
def test_counter4_env_mirrored_replay_passes_on_handover_fails_on_raw():
    c, blocks = _session(COUNTER4)
    rr = runner.run(c, blocks, fast=True)
    hw, rw = envplan.handover_world(c, rr.prepared), envplan.raw_world(rr.prepared)
    (_, ph, ch), = envplan.env_captures(c, rr, world=hw)
    (_, pw, cw), = envplan.env_captures(c, rr, world=rw)
    assert envplan.evaluate(c, run_plan(hw, ph), ch) == []
    assert envplan.evaluate(c, run_plan(rw, pw), cw)[:1] == ["vec0:model#0@18"]


@pytest.mark.skipif(not ADDER2.exists(), reason="session data not present")
def test_timing_tight_build_depends_on_absolute_position():
    """Read 2 ticks after quiescence: passes where env ran it, fails at the old oracle origin (0,100,0)."""
    c, blocks = _session(ADDER2)
    rr = runner.run(c, blocks, fast=True)
    w = envplan.handover_world(c, rr.prepared)
    (_, plan, checks), = envplan.env_captures(c, rr, world=w)
    assert envplan.evaluate(c, run_plan(w, plan), checks) == []
    assert envplan.evaluate(c, run_plan(w, plan, origin=(0, 100, 0)), checks)


def test_oracle_sessions_auto_hold_and_dedup(tmp_path):
    m = _mod("osess", "scripts/oracle_sessions.py")

    class V:
        def __init__(self, t):
            self.ticks = t

    class R:
        vectors = [V(10), V(42)]
    assert m.auto_hold(R()) == max(40, 42 * 5 // 4 + 1 + 8)
    for root in ("a", "b/x/session"):
        d = tmp_path / root / "s1"
        d.mkdir(parents=True)
        (d / "state.json").write_text("{}")
        (d / "final_blocks.json").write_text("[]")
    dirs = m.session_dirs([str(tmp_path / "a"), str(tmp_path / "b/x/session")])
    assert [d.parent.name for d in dirs] == ["a"]                 # identical sandbox copy listed once
    (tmp_path / "b/x/session/s1/final_blocks.json").write_text("[[0,0,0,\"stone\"]]")
    assert len(m.session_dirs([str(tmp_path / "a"), str(tmp_path / "b/x/session")])) == 2
