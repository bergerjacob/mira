"""Round-2 audit M1: a job's vanilla trace must not depend on which jobs shared its JVM (skipped without the oracle).

Root cause (tools/oracle/java/OracleBatch.java, warmChunks): chunks ticketed after earlier jobs had run never reached
ticking, the 600-tick warm-up cap expired silently and the job traced a world whose redstone did not run.
`t1_xor` or `t2_door_2x2` followed by `t3_adder4` reproduced it (adder4: 53 failed checks instead of 8 at hold=40).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from tools.oracle import gametest
from tools.oracle.trace import Plan, put, use

pytestmark = pytest.mark.skipif(not gametest.available(), reason="vanilla oracle not installed")

ROOT = Path(__file__).resolve().parents[2]


def _env_diff():
    spec = importlib.util.spec_from_file_location("oracle_env_diff", ROOT / "scripts/oracle_env_diff.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def reference_jobs(task_id, hold=40, preroll=20):
    """[(name, blocks, plan, checks, contract)] for the task's reference solution, as scripts/oracle_env_diff.py builds them."""
    from env import runner
    from env.task import load_task
    m = _env_diff()
    task = load_task(task_id)
    pr = runner.prepare(task.contract, task.solution)
    blocks = [(*p, s, pr.nbt.get(p)) for p, s in pr.world.items()]
    return [(f"{task_id}__{label.replace(':', '_')}", blocks, plan, checks, task.contract)
            for label, plan, checks in m.captures(task.contract, hold, preroll)]


def same(a, b):
    return a.placed == b.placed and a.initial == b.initial and a.changes == b.changes and a.n_ticks == b.n_ticks


@pytest.fixture(scope="module")
def adder4_alone():
    job = reference_jobs("t3_adder4")[0]
    return job, gametest.capture_batch([job[:3]])[0]


@pytest.mark.parametrize("first", ["t1_xor", "t2_door_2x2", "t3_adder2"])
def test_batch_after_other_task_equals_adder4_alone(adder4_alone, first):
    job, alone = adder4_alone
    jobs = [j[:3] for j in reference_jobs(first)] + [job[:3]]
    traces = gametest.capture_batch(jobs)
    assert same(traces[-1], alone)
    m = _env_diff()
    assert m.evaluate(job[4], traces[-1], job[3]) == m.evaluate(job[4], alone, job[3])


def test_isolated_mode_equals_batch():
    from tools.oracle.circuits import all_circuits
    cs = [c for c in all_circuits() if c.name in ("torch_not_gate", "sticky_piston_rb", "repeater_chain")]
    jobs = [(c.name, c.blocks, c.plan) for c in cs]
    batch = gametest.capture_batch(jobs)
    iso = gametest.capture_batch(jobs, isolated=True, workers=3)
    assert all(same(a, b) for a, b in zip(batch, iso))
    many = gametest.capture_many(jobs, workers=2, isolated=True)
    assert all(same(a, b) for a, b in zip(batch, many))


def test_pending_scheduled_tick_of_previous_job_does_not_fire_in_next_job():
    """Job A ends with a repeater tick still scheduled; job B puts a fresh repeater in the same cell and must stay quiet
    even with idle=0 (the wipe clears scheduled ticks and block events like /clone)."""
    floor = [(x, 0, 0, "minecraft:stone") for x in range(5)]
    rep = (2, 1, 0, "minecraft:repeater[delay=4,facing=west,locked=false,powered=false]")
    lamp = (3, 1, 0, "minecraft:redstone_lamp[lit=false]")
    a = floor + [(0, 1, 0, "minecraft:lever[face=floor,facing=east,powered=false]"), (1, 1, 0, "minecraft:redstone_wire"), rep, lamp]
    b = floor + [(1, 1, 0, "minecraft:redstone_wire"), rep, lamp]
    pa = Plan(3, [use(1, (0, 1, 0))], [(2, 1, 0)])            # the repeater's 8-tick delay is still pending when A ends
    pb = Plan(30, [], [(2, 1, 0), (3, 1, 0)])
    tb = gametest.capture_batch([("a", a, pa), ("b", b, pb)], idle=0)[1]
    assert tb.changes == {}, tb.changes
    assert gametest.capture_batch([("b", b, pb)])[0].changes == {}
