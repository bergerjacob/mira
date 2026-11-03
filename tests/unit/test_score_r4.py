"""R4 is 'n/a' (and flagged machine-readably) when the contract has no reference size."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from env.runner import run  # noqa: E402
from env.score import feedback, score  # noqa: E402
from env.task import load_task  # noqa: E402


def full_pass(reference):
    t = load_task("t0_not")
    t.contract.reference = reference
    res = run(t.contract, t.solution, fast=True)
    return t, res, score(t.contract, res)


def test_without_a_reference_r4_is_flagged_and_printed_n_a_but_the_reward_is_unchanged():
    t, res, sc = full_pass({})
    assert sc.full_pass and sc.r4 == 1.0 and sc.r4_na is True and sc.r4_text == "n/a"
    assert sc.to_dict()["r4_na"] is True and sc.reward == 1.0
    assert "R4=n/a" in feedback(t.contract, res, sc, final=True)


def test_with_a_reference_r4_is_a_number():
    t, res, sc = full_pass({"blocks": 3, "volume": 3})
    assert sc.r4_na is False and 0 < sc.r4 < 1 and sc.r4_text == f"{sc.r4:.2f}"
    assert f"R4={sc.r4:.2f}" in feedback(t.contract, res, sc, final=True)


def test_r4_is_not_flagged_before_a_full_pass():
    t = load_task("t0_not")
    t.contract.reference = {}
    sc = score(t.contract, run(t.contract, [], fast=True))
    assert not sc.full_pass and sc.r4 == 0.0 and sc.r4_na is False
