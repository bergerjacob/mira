"""Every shipped task: spec sanity, reference passes, stored broken variants fail, JSON is in sync with the authoring script."""
import pytest

from env import mutate
from env.runner import run
from env.score import score
from env.task import list_task_ids, load_task, reference_stats
from env.validate_tasks import nonvacuous

IDS = list_task_ids()


def test_at_least_twenty_tasks_across_tiers():
    assert len(IDS) >= 20
    tiers = {load_task(i).contract.tier for i in IDS}
    assert {"T0", "T1", "T2"} <= tiers
    assert sum(i.startswith("t0_") for i in IDS) >= 8 and sum(i.startswith("t1_") for i in IDS) >= 8


@pytest.mark.parametrize("tid", IDS)
def test_spec_is_valid_and_nonvacuous(tid):
    c = load_task(tid).contract
    assert c.validate() == []
    assert nonvacuous(c) == []
    assert c.reference == reference_stats(load_task(tid).solution)
    assert c.description.strip() and c.title


@pytest.mark.parametrize("tid", IDS)
def test_reference_is_a_full_pass_with_every_invariant(tid):
    t = load_task(tid)
    res = run(t.contract, t.solution)
    sc = score(t.contract, res)
    assert res.prepared.errors == [] and res.place_errors == []
    assert res.invariant_failures() == {}, res.invariant_failures()
    assert sc.full_pass and sc.reward == pytest.approx(1.0), sc.to_dict()


@pytest.mark.parametrize("tid", IDS)
def test_stored_broken_variants_fail(tid):
    t = load_task(tid)
    assert len(t.broken) >= 3, "run: python env/validate_tasks.py --write-broken"
    for b in t.broken:
        blocks = mutate.apply(t.solution, [tuple(e) for e in b["edits"]])
        sc = score(t.contract, run(t.contract, blocks, fast=True))
        assert not sc.full_pass and sc.reward < 1.0, (tid, b["name"])


@pytest.mark.parametrize("tid", IDS)
def test_public_checks_reject_the_empty_build(tid):
    # the public run_spec set must not be passable by doing nothing / outputting a constant
    t = load_task(tid)
    res = run(t.contract, [], public_only=True, fast=True)
    assert not score(t.contract, res).full_pass


@pytest.mark.parametrize("tid", IDS)
def test_model_visible_text_has_no_hidden_data(tid):
    c = load_task(tid).contract
    txt = c.describe(public=True)
    for s in c.behaviour.sequences:
        if not s.public:
            assert f"sequence '{s.name}'" not in txt
    pub = c.public_dict()
    assert len(pub["behaviour"]["sequences"]) == sum(s.public for s in c.behaviour.sequences)
    assert pub["eval_seed"] is None


def test_json_matches_authoring_script():
    from env.tasks import author
    for plan in author.build_all():
        t = plan.build()
        stored = load_task(plan.id)
        assert stored.solution == t.solution, plan.id
        assert stored.contract.to_dict() == t.contract.to_dict(), plan.id


def test_door_task_matches_nekochu_spec_coordinates():
    c = load_task("t2_door_2x2").contract
    cells = sorted(p.pos for p in c.ports if p.kind == "region")
    assert cells == [(0, 2, 3), (0, 3, 3), (1, 2, 3), (1, 3, 3)]
    assert c.keep_clear == [[0, 2, -3, 1, 3, 2], [0, 2, 4, 1, 3, 9]]
    assert "minecraft:piston_head" in c.forbidden and "minecraft:moving_piston" in c.forbidden
