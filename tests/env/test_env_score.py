import pytest

from env.runner import run
from env.score import balanced, feedback, score

WIRE = "minecraft:redstone_wire"


def reward(task, blocks, **kw):
    t = task
    res = run(t.contract, blocks, fast=True, **kw)
    return score(t.contract, res), res


def test_balanced_accuracy_rescaled():
    assert balanced([(1, True), (1, True), (0, True), (0, True)]) == 1.0
    assert balanced([(1, False), (1, False), (0, True), (0, True)]) == 0.0      # constant 0 output
    assert balanced([(1, True), (0, False)]) == 0.0                              # constant 1 output
    assert balanced([(1, False), (0, False)]) == 0.0                             # inverted: no negative credit
    assert balanced([(1, True), (1, False), (0, True), (0, False)]) == 0.0       # chance
    assert balanced([(1, True), (1, True), (1, True), (0, True)]) == 1.0
    assert balanced([(1, True), (1, False)]) == 0.5                              # one class only: plain accuracy
    assert balanced([]) == 0.0


def test_reference_scores_exactly_one(task):
    for tid in ("t0_lamp", "t1_xor", "t1_t_flipflop"):
        sc, _ = reward(task(tid), task(tid).solution)
        assert sc.reward == pytest.approx(1.0) and sc.full_pass, tid
        assert (sc.r0, sc.r1, sc.r2, sc.r3, sc.r4) == (1, 1, 1, 1, 1)


def test_reward_formula_components(task):
    t = task("t0_and")
    sc, _ = reward(t, t.solution)
    assert sc.reward == pytest.approx(0.1 * sc.r0 + 0.1 * sc.r1 + 0.1 * sc.r2 + 0.6 * sc.r3 + 0.1 * sc.r4)


def test_empty_build_is_compilable_but_scores_low(task):
    t = task("t0_and")
    sc, _ = reward(t, [])
    assert sc.r0 == 1 and sc.r1 == 1 and sc.r2 == 0 and sc.r3 == 0 and sc.r4 == 0
    assert sc.reward == pytest.approx(0.2) and not sc.full_pass


def test_compile_failure_scores_zero(task):
    t = task("t0_and")
    sc, _ = reward(t, [[0, 0, 0, "minecraft:nope"]])
    assert sc.reward == 0 and sc.r0 == 0


def test_constant_output_cheat_scores_zero_on_behaviour(task):
    # redstone block glued to the lamp: lamp always lit -> 'fraction correct' would be 50%, balanced accuracy is 0
    t = task("t0_not")
    sc, _ = reward(t, [[6, 1, 0, "minecraft:redstone_block"], [6, 1, 2, "minecraft:redstone_block"]])
    assert sc.r3 == 0 and not sc.full_pass


def test_constant_off_scores_zero_on_and_gate(task):
    t = task("t0_and")
    sc, _ = reward(t, [[1, 1, 0, WIRE]])  # lamp never lights: 75% plain accuracy on an AND truth table
    assert sc.r3 == 0


def test_r3_is_min_over_outputs(task):
    t = task("t0_fanout")
    good_only_y1 = [b for b in t.solution if not (b[2] == 4)]  # cut the branch to Y2
    sc, _ = reward(t, good_only_y1)
    assert sc.per_output["Y1"] == 1.0 and sc.per_output["Y2"] == 0.0 and sc.r3 == 0.0


def test_r4_only_after_full_pass(task):
    t = task("t0_lamp")
    extra = t.solution + [[1, 1, 2, "minecraft:stone"], [2, 1, 2, "minecraft:stone"]]
    sc, _ = reward(t, extra)
    assert sc.r3 == 1 and 0 < sc.r4 < 1 and sc.reward < 1
    broken = [b for b in extra if b[0] != 3]
    sc2, _ = reward(t, broken)
    assert sc2.r3 < 1 and sc2.r4 == 0


def test_r1_gates_r3_in_the_reward(task):
    t = task("t0_lamp")
    sc, res = reward(t, t.solution + [[3, 2, 1, WIRE]])  # works, but a floating wire pops off
    assert sc.r3 == 1 and sc.r1 < 1 and not sc.full_pass
    assert sc.reward == pytest.approx(0.1 + 0.1 * sc.r1 + 0.1 * sc.r2)


def test_behavioural_invariant_failure_caps_r3(task):
    t = task("t0_lamp")
    sol = [[x, 1, 1, WIRE] for x in range(1, 4)] + [[4, 1, 1, "minecraft:piston[facing=east]"]]
    sc, res = reward(t, sol)  # piston shoves the fixed lamp out of the box
    assert not res.invariants["no_collateral"]["ok"] and sc.r3 <= 0.99


def test_feedback_text_public_only_and_no_hidden_leak(task):
    t = task("t1_rs_latch")
    sc, res = reward(t, [b for b in t.solution if b[0] != 4], public_only=True)
    txt = feedback(t.contract, res, sc, final=False)
    assert "FAIL" in txt and "waveform" in txt and "HIDDEN" not in txt
    assert "set_first" not in txt and "reset_twice" not in txt
    res = run(t.contract, t.solution, fast=True)
    fin = feedback(t.contract, res, score(t.contract, res), final=True)
    assert "HIDDEN checks: 2/2 pass (details withheld)" in fin and "set_first" not in fin


def test_feedback_reports_first_divergence_and_silent_components(task):
    t = task("t0_and")
    sc, res = reward(t, [b for b in t.solution if b[:3] != [7, 1, 1]], public_only=True)  # final torch missing
    txt = feedback(t.contract, res, sc)
    assert "expected" in txt and "first divergence" in txt
    sc, res = reward(t, t.solution + [[1, 1, 1, "minecraft:stone"]])
    assert "R0 COMPILE: ok" in feedback(t.contract, res, sc)


def test_feedback_reports_compile_errors(task):
    t = task("t0_lamp")
    sc, res = reward(t, [[1, 1, 1, "minecraft:redstone_wir"]])
    txt = feedback(t.contract, res, sc)
    assert "R0 COMPILE: FAIL" in txt and "unknown block" in txt
