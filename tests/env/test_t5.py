"""T5 tasks (env/tasks/t5/): spec hygiene, held-out hidden checks (not the public table reordered), python models, stored broken
variants, reference == authoring script, recorded vanilla verdicts. The gate (`env/tasks/author_t5.py --validate`) is run by hand."""
import itertools
import json
from pathlib import Path

import pytest

from env import mutate
from env.runner import run
from env.score import score
from env.task import list_task_ids, load_task
from env.validate_tasks import nonvacuous
from env.tasks import author_t5 as A

T5 = Path(__file__).resolve().parents[2] / "env" / "tasks" / "t5"
IDS = sorted(p.stem for p in T5.glob("t5_*.json"))
EXPECTED = {"t5_lfsr4", "t5_lock4", "t5_mul2x2", "t5_seq_detector"}


def test_t5_task_set_is_listed_everywhere():
    assert set(IDS) == EXPECTED == set(A.STORED)
    assert set(list_task_ids("t5")) == EXPECTED and load_task("t5_lock4").id == "t5_lock4"
    from mira_mcp.session import task_files
    assert EXPECTED <= set(task_files())


@pytest.mark.parametrize("tid", IDS)
def test_spec_is_valid_nonvacuous_and_t5(tid):
    t = load_task(tid)
    c = t.contract
    assert c.tier == "T5" and c.validate() == [] and nonvacuous(c) == []
    assert c.reference["blocks"] == len(t.solution) <= c.budgets.max_blocks
    assert len(t.broken) >= 3


@pytest.mark.parametrize("tid", IDS)
def test_hidden_checks_are_held_out_not_the_public_ones_reordered(tid):
    c = load_task(tid).contract
    pub_v = {tuple(sorted(v.inputs.items())) for v in c.vectors() if v.public}
    hid_v = {tuple(sorted(v.inputs.items())) for v in c.vectors() if not v.public}
    pub_s = [tuple((e.t, tuple(sorted(e.set.items()))) for e in s.events) for s in c.behaviour.sequences if s.public]
    hid_s = [tuple((e.t, tuple(sorted(e.set.items()))) for e in s.events) for s in c.behaviour.sequences if not s.public]
    if pub_v or hid_v:
        assert hid_v - pub_v, "hidden rows must contain input combinations never shown"
    if hid_s:
        assert not set(hid_s) & set(pub_s) and len(hid_s) >= 4
        assert max(len(s) for s in hid_s) > max(len(s) for s in pub_s)       # longer held-out streams than any public one


@pytest.mark.parametrize("tid", IDS)
def test_public_view_hides_hidden_checks(tid):
    pub = load_task(tid).contract.public_dict()
    bh = pub["behaviour"]
    assert all(s["public"] for s in bh["sequences"])
    assert not bh["truth_table"] or all(r["public"] for r in bh["truth_table"]["rows"])
    assert pub["eval_seed"] is None and pub["reference"] == {}


def test_lfsr_model_has_period_15_and_no_lockup_from_zero():
    s, seen = {}, []
    for _ in range(30):
        A.lfsr_xnor(s, {"CLK": 0})
        seen.append(tuple(A.lfsr_xnor(s, {"CLK": 1}).values()))
    assert len(set(seen)) == 15 and seen[:15] == seen[15:] and (1, 1, 1, 1) not in seen
    s = {}
    assert A.lfsr_xnor(s, {"CLK": 1}) == A.lfsr_xnor(s, {"CLK": 1})          # holding CLK high is one edge, not two


def test_lock_model_rules():
    def feed(word):
        s, out = {}, []
        for ch in word:
            out.append(A.lock4(s, {ch: 1})["OPEN"])
        return out
    assert feed("LRRL") == [0, 0, 0, 1]
    assert feed("LRRLL") == [0, 0, 0, 1, 0] and feed("LRRLR") == [0, 0, 0, 1, 0]
    assert feed("LLRRL") == [0, 0, 0, 0, 1]                                 # a wrong L restarts at step 1
    assert feed("LRLRRL") == [0, 0, 0, 0, 0, 1]
    assert feed("RRLRRL")[-1] == 1 and feed("LRRRL")[-1] == 0
    assert A.lock4({}, {})["OPEN"] == 0


def test_seq_detector_model_counts_overlapping_matches():
    s, hits = {}, []
    for bit in (1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1):
        A.seq_detector(s, {"CLK": 0, "DIN": bit})
        hits.append(A.seq_detector(s, {"CLK": 1, "DIN": bit})["MATCH"])
    assert hits == [0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1]
    c = load_task("t5_seq_detector").contract
    assert all(any(e.value == 1 for e in q.expect) for q in c.behaviour.sequences)     # every stream contains a match


def test_mul_model_is_multiplication():
    for a, b in itertools.product(range(4), repeat=2):
        out = A.mul2x2({}, {"A0": a & 1, "A1": a >> 1, "B0": b & 1, "B1": b >> 1})
        assert sum(out[f"P{k}"] << k for k in range(4)) == a * b


def test_mul_rows_are_the_model_and_every_combination_appears():
    c = load_task("t5_mul2x2").contract
    combos = set()
    for v in c.vectors():
        assert v.expect == A.mul2x2({}, v.inputs)
        combos.add(tuple(v.inputs[k] for k in ("A0", "A1", "B0", "B1")))
    assert len(combos) == 16
    assert sum(v.public for v in c.vectors()) <= 8 and sum(not v.public for v in c.vectors()) >= 30


@pytest.mark.parametrize("tid", ["t5_lfsr4", "t5_lock4", "t5_seq_detector"])
def test_sequence_expectations_follow_the_python_model(tid):
    c = load_task(tid).contract
    for s in c.behaviour.sequences:
        state = {}
        if tid == "t5_lock4":
            vals = [A.lock4(state, e.set)["OPEN"] for e in s.events]          # one event per button press
            assert [x.value for x in sorted(s.expect, key=lambda x: x.t1)] == [0] + vals
        else:
            edges = [e for e in s.events if e.set.get("CLK") == 1]
            din = 0
            for k, e in enumerate(edges):
                din = next((x.set["DIN"] for x in s.events if "DIN" in x.set and e.t - 20 < x.t < e.t), din)
                fn = A.lfsr_xnor if tid == "t5_lfsr4" else A.seq_detector
                fn(state, {"CLK": 0, "DIN": din})
                got = fn(state, {"CLK": 1, "DIN": din})
                win = [x for x in s.expect if x.t1 > e.t and x.t1 <= e.t + 60]
                assert win and all(x.value == got[x.port] for x in win), (s.name, k)


@pytest.mark.parametrize("tid", IDS)
def test_reference_matches_the_authoring_script(tid):
    assert A.BUILDERS[tid]().build().solution == load_task(tid).solution


@pytest.mark.parametrize("tid", ["t5_mul2x2", "t5_lfsr4", "t5_seq_detector"])
def test_reference_full_pass_and_broken_variants_fail(tid):
    t = load_task(tid)
    sc = score(t.contract, run(t.contract, t.solution))
    assert sc.full_pass and sc.reward == pytest.approx(1.0)
    for b in t.broken[:3]:
        assert not score(t.contract, run(t.contract, mutate.apply(t.solution, b["edits"]), fast=True)).full_pass, b["name"]


@pytest.mark.slow
def test_lock_reference_full_pass_and_broken_variants_fail():
    t = load_task("t5_lock4")
    assert score(t.contract, run(t.contract, t.solution)).full_pass
    for b in t.broken:
        assert not score(t.contract, run(t.contract, mutate.apply(t.solution, b["edits"]), fast=True)).full_pass, b["name"]


def test_lock_without_its_button_buses_fails():
    """Register and gates only (everything north of the L bus row): the lamp never lights, R3 < 1."""
    t = load_task("t5_lock4")
    sc = score(t.contract, run(t.contract, [b for b in t.solution if b[2] < 9], fast=True))     # gates and register only, no buses
    assert not sc.full_pass and sc.r3 < 1


@pytest.mark.parametrize("tid", IDS)
def test_empty_build_fails(tid):
    c = load_task(tid).contract
    sc = score(c, run(c, [], fast=True))
    assert not sc.full_pass and sc.r3 < 0.5


def test_vanilla_verdicts_are_recorded_one_jvm_per_capture():
    rep = json.loads((T5 / "oracle_report.json").read_text())
    for tid in EXPECTED:
        r = rep[tid]
        assert r["vanilla_pass"] and r["nucleation_pass"] and r["agree"] == r["captures"] and r["jvm_per_capture"], tid


def test_variant_sweep_covers_t5():
    rep = json.loads((T5 / "variants_report.json").read_text())["tasks"]
    assert EXPECTED <= set(rep)
    assert rep["t5_mul2x2"]["allowed"] == list(range(8))
