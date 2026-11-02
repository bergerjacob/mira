"""End to end: the scripted reference player must score 1.0 on every one of the 22 tasks in both arms."""
import pytest

from env.task import list_task_ids
from harness.agent import RunConfig, run_episode
from harness.reference import reference_llm


@pytest.mark.parametrize("tid", list_task_ids())
def test_reference_player_scores_one_in_both_arms(tid, task):
    t = task(tid)
    for arm in ("blocks", "builder"):
        for rounds in (0, 1):
            f = run_episode(RunConfig(tid, "mock", arm, rounds), reference_llm(t, arm, rounds))
            assert f["reward"] == 1.0 and f["full_pass"] and f["ended_by"] == "submit", (tid, arm, rounds, f["final_text"])
            assert f["rounds_used"] == rounds and f["r4"] == 1.0
