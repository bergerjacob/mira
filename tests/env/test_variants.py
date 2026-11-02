"""env/variants.py: seeded rotate / mirror / translate copies of a task. Pure-geometry tests run on every task; simulator tests use small tasks.
The full 8-element sweep over every tier is `python env/variants.py --jobs 16 --write` (minutes) and lands in env/tasks/t5/variants_report.json."""
import json

import pytest

from env import blocks as BL
from env import mutate
from env.contract import Port, box_cells, in_box
from env.runner import fixed_cells, run
from env.score import score
from env.task import list_task_ids, load_task
from env.variants import ELEMENTS, REPORT, Xf, allowed_variants, transform_task, variant_of, variant_params

IDS = list_task_ids()


def canon(solution):
    return sorted((b[0], b[1], b[2], BL.canonical(b[3])) for b in solution)     # some stored T0 states are not in sorted-property form


def strip_id(d):
    return {k: v for k, v in d.items() if k not in ("id", "description")}      # text is re-formatted, not byte-identical


def test_seed_zero_is_the_identity_and_seeds_cover_the_dihedral_group():
    assert variant_params(0) == (0, None, (0, 0, 0))
    assert {variant_params(s)[:2] for s in range(8)} == set(ELEMENTS) and len(ELEMENTS) == 8
    assert variant_params(13) == variant_params(13) and variant_params(13)[:2] == ELEMENTS[13 % 8]
    assert variant_params(1)[2] != (0, 0, 0) and variant_params(1)[2][1] >= 0      # never pushed below the original floor level


def test_seed_zero_variant_equals_the_stored_task(task):
    t = task("t1_xor")
    v = variant_of(t, 0)
    assert v.contract.to_dict() == t.contract.to_dict() and v.solution == t.solution and v.broken == t.broken


def test_load_task_variant_argument_matches_variant_of(task):
    for s in (1, 6):
        a, b = load_task("t0_and", variant=s), variant_of(task("t0_and"), s)
        assert a.contract.to_dict() == b.contract.to_dict() and a.solution == b.solution
        assert a.id == f"t0_and@v{s}"


@pytest.mark.parametrize("rot,mirror", [(90, None), (0, "x"), (270, "x")])
def test_group_laws_on_a_whole_task(task, rot, mirror):
    t = task("t2_door_2x2")
    order = 2 if mirror else 4                      # mirror (+ any rotation) is a reflection: order 2
    v = t
    for _ in range(order):
        v = transform_task(v, rot, mirror)
    assert strip_id(v.contract.to_dict()) == strip_id(t.contract.to_dict())
    assert canon(v.solution) == canon(t.solution) and [b["name"] for b in v.broken] == [b["name"] for b in t.broken]


def test_four_quarter_turns_of_every_task_are_the_identity():
    for tid in IDS:
        t = load_task(tid)
        v = t
        for _ in range(4):
            v = transform_task(v, 90)
        assert canon(v.solution) == canon(t.solution), tid
        assert [p.pos for p in v.contract.ports] == [p.pos for p in t.contract.ports], tid


def test_state_properties_are_transformed():
    xf = Xf([0, 0, 0, 9, 9, 9], 90)
    assert xf.state("minecraft:repeater[delay=1,facing=east,locked=false,powered=false]") == \
        "minecraft:repeater[delay=1,facing=south,locked=false,powered=false]"
    assert xf.state("minecraft:redstone_wire[east=side,north=none,power=0,south=none,west=up]") == \
        "minecraft:redstone_wire[east=none,north=up,power=0,south=side,west=none]"
    assert xf.state("minecraft:oak_log[axis=x]") == "minecraft:oak_log[axis=z]"
    m = Xf([0, 0, 0, 9, 9, 9], 0, "x")
    assert m.state("minecraft:lever[face=wall,facing=east,powered=false]") == "minecraft:lever[face=wall,facing=west,powered=false]"
    assert m.state("minecraft:sticky_piston[extended=false,facing=up]") == "minecraft:sticky_piston[extended=false,facing=up]"
    assert m.dir("north") == "north" and xf.dir("north") == "east" and xf.dir("up") == "up"


def test_points_and_boxes_stay_consistent_and_the_bbox_corner_moves_by_the_offset():
    bbox = [-3, 0, 2, 10, 5, 8]
    for rot, mirror in ELEMENTS:
        xf = Xf(bbox, rot, mirror, (5, 1, -7))
        nb = xf.box(bbox)
        assert nb[:3] == [2, 1, -5]                       # min corner = original min + offset
        assert sorted(nb[3 + i] - nb[i] for i in range(3)) == sorted(bbox[3 + i] - bbox[i] for i in range(3))
        assert all(in_box(xf.point(c), nb) for c in box_cells([-3, 0, 2, -1, 1, 4]))


def test_block_prop_ports_rename_side_properties_and_turn_facing():
    xf = Xf([0, 0, 0, 4, 4, 4], 90)
    assert xf.prop("north", "side") == ("east", "side")
    assert xf.prop("facing", "west") == ("facing", "north")
    assert xf.prop("powered", "true") == ("powered", "true")


def test_description_directions_and_coordinate_groups_are_rewritten(task):
    t = task("t2_door_2x2")
    v = variant_of(t, rot=90, offset=(0, 0, 0))
    xf = Xf(t.contract.bbox, 90)
    assert "(0..1, 2..3, z=3)" in t.contract.description and "(0..1, 2..3, z=3)" not in v.contract.description
    cells = xf.box([0, 2, 3, 1, 3, 3])
    assert f"(x={cells[0]}, y {cells[1]}..{cells[4]}, z {cells[2]}..{cells[5]})" in v.contract.description
    w = variant_of(task("t0_pad_lamp"), rot=90)
    assert "west wall" in task("t0_pad_lamp").contract.description and "north wall" in w.contract.description
    assert Xf([0, 0, 0, 9, 9, 9], 0, "x").text("the East side, [1, 2, 3]") == "the West side, [8, 2, 3]"


@pytest.mark.parametrize("tid", IDS)
def test_every_variant_of_every_task_is_a_consistent_task(tid):
    t = load_task(tid)
    for s in range(8):
        v = variant_of(t, s)
        c = v.contract
        assert c.validate() == [], (tid, s)
        assert c.reference == t.contract.reference and len(v.solution) == len(t.solution)
        assert all(in_box(b[:3], c.build_box) for b in v.solution), (tid, s)
        fixed = fixed_cells(c)
        assert not {tuple(b[:3]) for b in v.solution} & set(fixed), (tid, s)
        assert all(in_box(p.pos, c.bbox) for p in c.ports)
        assert [p.name for p in c.ports] == [p.name for p in t.contract.ports]
        assert c.behaviour == t.contract.behaviour and c.budgets == t.contract.budgets


def test_broken_edits_commute_with_the_transform(task):
    t = task("t1_xor")
    for s in (3, 5):
        v = variant_of(t, s)
        for b, vb in zip(t.broken, v.broken):
            moved = variant_of(type(t)(t.contract, mutate.apply(t.solution, b["edits"]), [], ""), s).solution
            assert canon(moved) == canon(mutate.apply(v.solution, vb["edits"]))


def test_explicit_rot_mirror_offset_api_and_tag():
    t = load_task("t0_not")
    v = variant_of(t, rot=180, mirror="x", offset=(1, 2, 3))
    assert v.id == "t0_not@r180mxo1_2_3" and v.contract.bbox[:3] == [t.contract.bbox[i] + (1, 2, 3)[i] for i in range(3)]
    assert variant_of(t, rot=0).id == "t0_not"
    with pytest.raises(ValueError):
        variant_of(t, rot=45)


def test_port_facing_is_turned(task):
    t = task("t0_and")
    v = variant_of(t, rot=90)
    for p, q in zip(t.contract.ports, v.contract.ports):
        if p.facing:
            assert q.facing == Xf(t.contract.bbox, 90).dir(p.facing)


@pytest.mark.parametrize("seed", range(1, 8))
@pytest.mark.parametrize("tid", ["t0_and", "t1_mux2", "t1_rs_latch"])
def test_reference_of_rotation_invariant_tasks_passes_in_every_variant(tid, seed):
    v = load_task(tid, variant=seed)
    sc = score(v.contract, run(v.contract, v.solution, fast=True))
    assert sc.full_pass and sc.reward == pytest.approx(1.0), (tid, seed, sc.to_dict())


@pytest.mark.parametrize("seed", (2, 7))
def test_stored_broken_variants_still_fail_in_a_variant(seed):
    v = load_task("t0_and", variant=seed)
    for b in v.broken:
        assert not score(v.contract, run(v.contract, mutate.apply(v.solution, b["edits"]), fast=True)).full_pass, b["name"]


def test_pure_translation_gives_the_same_score_breakdown(task):
    t = task("t1_edge_detector")
    v = variant_of(t, rot=0, offset=(17, 3, -29))
    a, b = score(t.contract, run(t.contract, t.solution, fast=True)), score(v.contract, run(v.contract, v.solution, fast=True))
    assert a.full_pass and b.full_pass and a.per_output == b.per_output


def test_door_variant_keeps_the_hallway_semantics():
    v = load_task("t2_door_2x2", variant=3)
    c = v.contract
    assert len(c.keep_clear) == 2 and len(c.no_write) == 1
    sc = score(c, run(c, v.solution, fast=True))
    assert sc.full_pass
    hall = next(iter(box_cells(c.keep_clear[0])))
    bad = v.solution + [[*hall, "minecraft:stone"]]
    assert run(c, bad, fast=True).prepared.errors                    # writing the (moved) hallway is still rejected


def test_report_lists_every_task_and_allowed_variants_reads_it():
    rep = json.loads(REPORT.read_text())
    assert set(IDS) <= set(rep["tasks"])
    for tid, r in rep["tasks"].items():
        assert set(map(int, r["seeds"])) == set(range(8)) and r["allowed"] == allowed_variants(tid)
        assert all(r["seeds"][str(s)]["ok"] for s in r["allowed"])
    assert allowed_variants("t0_and") == list(range(8))
    assert 4 not in allowed_variants("t1_xor") and 0 in allowed_variants("t1_xor")
    assert allowed_variants("no_such_task") == list(range(8))


def test_env_failures_of_variants_are_not_vanilla_failures():
    """Every variant that the env rejects and that was replayed in vanilla 26.2 passes there (the env placement path is order-dependent)."""
    rep = json.loads(REPORT.read_text())
    van = rep.get("vanilla", {})
    assert len(van) >= 5
    for key, r in van.items():
        tid, s = key.split("@v")
        assert r["vanilla_pass"], key
    assert any(int(k.split("@v")[1]) not in rep["tasks"][k.split("@v")[0]]["allowed"] for k in van)
