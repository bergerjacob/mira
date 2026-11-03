"""Live (vanilla 26.2) checks for the A5 findings; skipped when the oracle is not installed."""
import json
from pathlib import Path

import pytest

from env import runner
from env.task import load_task
from tools.oracle import envplan, gametest
from tools.oracle.sim import run_plan
from tools.oracle.trace import Plan, first_divergence, place

pytestmark = pytest.mark.skipif(not gametest.available(), reason="vanilla oracle not installed")
ROOT = Path(__file__).resolve().parents[2]
COUNTER4 = ROOT / "data/sessions/t3_counter4-builder-R20-1102-054924-af52"
LINE = "minecraft:redstone_wire[east=side,north=none,power=0,south=none,west=side]"


def test_vanilla_hand_placement_keeps_line_end():
    """The Nucleation engine-placement repro (tests/oracle/test_placement.py) placed by hand in vanilla, same order."""
    floor = [(x, 0, z, "minecraft:stone") for x in range(6) for z in range(0, 9)]
    ops = [place(0, (0, 1, 3), "minecraft:lever[face=floor,facing=east,powered=false]")]
    ops += [place(0, p, "minecraft:redstone_wire") for p in [(1, 1, 3), (2, 1, 3), (4, 1, 4)]]
    tr = gametest.capture(floor, Plan(3, ops, [], "place", 0, (0, 0, 0)))
    st = tr.state_at(2)
    assert st[(2, 1, 3)] == LINE and st[(1, 1, 3)] == LINE
    assert st[(4, 1, 4)] == "minecraft:redstone_wire[east=side,north=side,power=0,south=side,west=side]"


def test_job_origin_is_honoured_and_t0_and_is_position_independent():
    t = load_task("t0_and")
    rr = runner.run(t.contract, t.solution, fast=True)
    w = envplan.handover_world(t.contract, rr.prepared)
    jobs = []
    for o in ("env", (1007, 70, 519), (-2003, 30, -1037)):
        (_, plan, checks), = envplan.env_captures(t.contract, rr, world=w, origin=o)
        jobs.append((f"and_{len(jobs)}", w, plan))
    trs = gametest.capture_batch(jobs)
    assert [tr.origin for tr in trs] == [(0, 0, 0), (1007, 70, 519), (-2003, 30, -1037)]
    assert first_divergence(trs[0], trs[1]) is None and first_divergence(trs[0], trs[2]) is None
    assert envplan.evaluate(t.contract, trs[2], checks) == []


@pytest.mark.skipif(not COUNTER4.exists(), reason="session data not present")
def test_counter4_session_passes_in_vanilla_as_env_scored_it():
    st = json.loads((COUNTER4 / "state.json").read_text())
    c = load_task(st.get("task_ref") or st["task_id"]).contract
    rr = runner.run(c, json.loads((COUNTER4 / "final_blocks.json").read_text()), fast=True)
    hw, rw = envplan.handover_world(c, rr.prepared), envplan.raw_world(rr.prepared)
    (_, ph, ch), = envplan.env_captures(c, rr, world=hw)
    (_, pr_, cr), = envplan.env_captures(c, rr, world=rw)
    th, tr = gametest.capture_batch([("c4h", hw, ph), ("c4r", rw, pr_)])
    assert envplan.evaluate(c, th, ch) == []
    assert envplan.evaluate(c, tr, cr)                                  # raw paste: wrong power-on state
    assert first_divergence(th, run_plan(hw, ph)) is None               # Nucleation == vanilla tick for tick
