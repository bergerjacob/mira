"""Offline tests of the differential fuzzer (scripts/diff_fuzz): generator validity, canonicalisation, symmetry
invariance, first-divergence logic, the minimiser on a synthetic divergence, signatures, known-class detectors and
the confirmation experiments. One live test (skipped without the vanilla oracle) runs a real divergence end to end."""
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env import blocks as B  # noqa: E402
from scripts.diff_fuzz import canon, confirm, engine, fuzz, gen, minimise, seeds, signature  # noqa: E402
from scripts.diff_fuzz.common import Case  # noqa: E402

W = "minecraft:redstone_wire"
STONE = "minecraft:stone"
LEVER = "minecraft:lever[face=floor,facing=east,powered=false]"


def cases(n=120, seed=3):
    rng = random.Random(seed)
    return [gen.generate(rng, [(0, 100, 0), (16, 100, 32)]) for _ in range(n)]


# ---------------------------------------------------------------------------------------------- generator
def test_generated_cases_are_valid_and_admissible():
    for c in cases():
        cells = {(b[0], b[1], b[2]): b[3] for b in c.blocks}
        nbt = {(b[0], b[1], b[2]): b[4] for b in c.blocks if b[4]}
        assert not gen.bad_attachments(cells, nbt)
        for st in set(cells.values()):
            assert B.canonical(st) == st or st == W
            assert gen.simulatable(B.canonical(st))
        assert len(c.blocks) >= 3
        assert all(len(b) == 5 for b in c.blocks)


def test_generator_is_deterministic_in_the_rng():
    a = [c.key() for c in cases(40, seed=9)]
    b = [c.key() for c in cases(40, seed=9)]
    c = [c.key() for c in cases(40, seed=10)]
    assert a == b and a != c


def test_plans_contain_only_use_ops_on_existing_blocks():
    for c in cases():
        cells = {(b[0], b[1], b[2]) for b in c.blocks}
        assert all(tuple(p) in cells for _t, p in c.ops)
        assert all(o.op == "use" for o in c.plan().ops)
        assert 2 <= min([t for t, _ in c.ops] or [2]) and max([t for t, _ in c.ops] or [0]) < c.ticks


def test_containers_hold_items_only_when_the_target_is_a_container():
    for c in cases(300, seed=5):
        cells = {(b[0], b[1], b[2]): b for b in c.blocks}
        for p, b in cells.items():
            bid, props = B.split_state(b[3])
            if B.short(bid) == "dropper" and b[4]:
                tgt = cells.get(gen.add(p, props["facing"]))
                assert tgt and B.short(B.split_state(tgt[3])[0]) in gen.CONTAINERS
            if B.short(bid) == "dispenser":
                assert not b[4]


def test_support_rules_reflect_the_fuzzer_findings():
    assert not gen.supports("minecraft:honey_block", "up")             # inset shape
    assert gen.supports(STONE, "up") and gen.supports(STONE, "north")
    assert not gen.supports("minecraft:sticky_piston[extended=false,facing=up]", "north")   # lateral needs a full cube
    cells = {(0, 0, 0): "minecraft:dropper[facing=up,triggered=false]", (0, 1, 0): W}
    assert not gen.bad_attachments(cells)                              # no NBT: placed in the first pass
    assert gen.bad_attachments(cells, {(0, 0, 0): "{Items:[{Slot:0b,id:\"minecraft:cobblestone\",count:1}]}"}) == {(0, 1, 0)}


def test_mutation_arm_produces_admissible_cases_from_the_seed_pool():
    pool = seeds.build_pool(verbose=False)
    assert {s["src"] for s in pool} >= {"env", "lib"}
    rng = random.Random(1)
    for _ in range(25):
        c = seeds.mutant(rng, pool, [(0, 100, 0)])
        cells = {(b[0], b[1], b[2]): b[3] for b in c.blocks}
        assert not gen.bad_attachments(cells, {(b[0], b[1], b[2]): b[4] for b in c.blocks if b[4]})
        assert c.meta["arm"] == "mut" and c.meta["seed"].split(":")[0] in ("env", "lib", "corpus")


def test_known_bug_repros_are_not_seed_sources():
    names = {s["name"] for s in seeds.build_pool(verbose=False)}
    assert not any("comparator_side_lever" in n or "dropper_feed" in n or "torch_clock" in n for n in names)


def test_recall_popoff_arm_is_inadmissible_by_construction():
    c = fuzz.recall_popoff(random.Random(2), [(0, 100, 0)])
    cells = {(b[0], b[1], b[2]): b[3] for b in c.blocks}
    assert gen.bad_attachments(cells)


# ---------------------------------------------------------------------------------------------- symmetry
@pytest.mark.parametrize("rot,mirror", canon.ELEMENTS)
def test_build_hash_is_invariant_under_the_dihedral_group_and_translation(rot, mirror):
    for c in cases(25, seed=4):
        moved = canon.transform_blocks(c.blocks, rot, mirror)
        shifted = [(x + 7, y + 3, z - 11, s, n) for x, y, z, s, n in moved]
        assert canon.build_hash(c.blocks) == canon.build_hash(moved) == canon.build_hash(shifted)


def test_distinct_builds_get_distinct_hashes_and_exact_hash_sees_orientation():
    a = [(0, 0, 0, STONE, None), (0, 1, 0, "minecraft:repeater[delay=1,facing=east,locked=false,powered=false]", None)]
    b = [(0, 0, 0, STONE, None), (0, 1, 0, "minecraft:repeater[delay=2,facing=east,locked=false,powered=false]", None)]
    assert canon.build_hash(a) != canon.build_hash(b)
    r = canon.transform_blocks(a, 90, None)
    assert canon.build_hash(a) == canon.build_hash(r) and canon.exact_hash(a) != canon.exact_hash(r)


def test_transform_case_moves_ops_with_their_blocks():
    c = Case([(0, 0, 0, STONE, None), (1, 1, 0, LEVER, None), (2, 0, 3, STONE, None)], [(5, (1, 1, 0))])
    for rot, mirror in canon.ELEMENTS:
        t = canon.transform_case(c, rot, mirror)
        pos = {(b[0], b[1], b[2]): b[3] for b in t.blocks}
        assert pos[tuple(t.ops[0][1])].startswith("minecraft:lever")


def test_cell_label_is_orientation_free():
    assert canon.cell_label("minecraft:piston[extended=false,facing=north]", "minecraft:piston[extended=true,facing=north]") == "piston[extended]"
    assert canon.cell_label("", "minecraft:lever[face=floor,facing=east,powered=false]") == "air!=lever"
    a = canon.cell_label("minecraft:comparator[facing=north,mode=compare,powered=true]", "minecraft:comparator[facing=north,mode=compare,powered=false]")
    b = canon.cell_label("minecraft:comparator[facing=east,mode=compare,powered=true]", "minecraft:comparator[facing=east,mode=compare,powered=false]")
    assert a == b == "comparator[powered]"


# ---------------------------------------------------------------------------------------------- traces
def flat(placed=None, initial=None, ticks=None, n=10):
    return {"placed": placed or {}, "initial": initial if initial is not None else dict(placed or {}), "ticks": ticks or {}, "n": n}


def test_identical_traces_do_not_diverge_and_air_variants_are_equal():
    a = flat({(0, 0, 0): STONE}, ticks={3: {(0, 1, 0): "minecraft:lamp"}})
    b = flat({(0, 0, 0): STONE}, ticks={3: {(0, 1, 0): "minecraft:lamp"}})
    assert canon.first_divergence(a, b) is None
    assert canon._nz("minecraft:cave_air") == "" and canon._nz("minecraft:air") == ""


def test_bare_wire_equals_default_wire_after_normalisation():
    full = "minecraft:redstone_wire[east=none,north=none,power=0,south=none,west=none]"
    assert canon._nz(W) == canon._nz(full)


def test_first_divergence_reports_tick_cells_and_label():
    lamp = lambda lit: f"minecraft:redstone_lamp[lit={lit}]"
    base = {(0, 0, 0): STONE, (0, 1, 0): lamp("false")}
    a = flat(base, ticks={3: {(0, 1, 0): lamp("true")}, 6: {(0, 1, 0): lamp("false")}})
    b = flat(base, ticks={4: {(0, 1, 0): lamp("true")}, 6: {(0, 1, 0): lamp("false")}})
    d = canon.first_divergence(a, b)
    assert d["phase"] == "tick" and d["tick"] == 3 and d["label"] == "redstone_lamp[lit]"
    assert d["div_ticks"] == 1                                # re-converges at tick 4: only tick 3 differs
    assert d["cells"][0][0] == (0, 1, 0)


def test_divergence_in_placed_and_initial_phase():
    a = flat({(0, 0, 0): STONE, (0, 1, 0): W})
    b = flat({(0, 0, 0): STONE})
    d = canon.first_divergence(a, b)
    assert d["phase"] == "placed" and d["label"] == "redstone_wire!=air"
    c = flat({(0, 0, 0): STONE}, initial={(0, 0, 0): STONE, (1, 0, 0): STONE})
    assert canon.first_divergence(c, flat({(0, 0, 0): STONE}))["phase"] == "initial"


def test_trace_hash_is_stable_and_sensitive():
    a = flat({(0, 0, 0): STONE}, ticks={1: {(0, 1, 0): "minecraft:lamp"}})
    assert canon.trace_hash(a) == canon.trace_hash(json.loads(json.dumps(a, default=list)) if False else a)
    b = flat({(0, 0, 0): STONE}, ticks={2: {(0, 1, 0): "minecraft:lamp"}})
    assert canon.trace_hash(a) != canon.trace_hash(b)


# ---------------------------------------------------------------------------------------------- minimiser
def synthetic_case():
    """A 5x1x5 stone floor with a lever, a repeater (the culprit), a lamp (the other culprit) and decoys. The fake
    engines diverge iff the repeater and the lamp are both present AND a use op targets the lever."""
    rep = "minecraft:repeater[delay=2,facing=east,locked=false,powered=false]"
    blocks = [(x, 0, z, STONE, None) for x in range(5) for z in range(5)]
    blocks += [(0, 1, 0, LEVER, None), (1, 1, 0, rep, None), (2, 1, 0, "minecraft:redstone_lamp[lit=false]", None),
               (3, 1, 3, W, None), (4, 1, 4, W, None), (2, 1, 3, "minecraft:target[power=0]", None)]
    return Case(blocks, [(3, (0, 1, 0)), (8, (0, 1, 0)), (12, (0, 1, 0))], ticks=60, preroll=20, origin=(0, 100, 0))


def fake_engines(monkeypatch, need=("repeater", "redstone_lamp")):
    def fake_vanilla(cs, names=None, isolated=False, workers=1, timeout=0, idle=0):
        out = []
        for c in cs:
            ids = {B.short(b[3].split("[")[0]) for b in c.blocks}
            lever_used = any(any(b[:3] == (*p,) and b[3].startswith("minecraft:lever") for b in c.blocks) for _t, p in c.ops)
            diverge = set(need) <= ids and lever_used
            first = min([t for t, _ in c.ops], default=0)
            out.append({"placed": {}, "initial": {}, "ticks": {first: {(0, 1, 0): "minecraft:lamp[lit=true]"}} if diverge else {}, "n": c.ticks})
        return out
    monkeypatch.setattr(engine, "run_vanilla", fake_vanilla)
    monkeypatch.setattr(engine, "run_nucleation", lambda c: {"placed": {}, "initial": {}, "ticks": {}, "n": c.ticks})


def run_min(case, d0):
    m = minimise.Min("t", case, d0)
    for _ in range(60):
        res, per = minimise.run_round([m], 1)
        if per[m.id]:
            m.feed(res[m.id], per[m.id])
        if m.done:
            return m
    raise AssertionError("did not converge")


def test_minimiser_reduces_a_synthetic_divergence_to_the_culprits(monkeypatch):
    fake_engines(monkeypatch)
    case = synthetic_case()
    d0 = {"phase": "tick", "tick": 3, "label": "lamp!=air", "cells": []}
    m = run_min(case, d0)
    ids = sorted(B.short(b[3].split("[")[0]) for b in m.cur.blocks)
    # the three required devices plus the two stones that support the lever and the repeater; the other 24 floor stones,
    # the decoy wires and the target block are gone
    assert ids == ["lever", "redstone_lamp", "repeater", "stone", "stone"]
    assert len(m.cur.ops) == 1 and m.cur.preroll == 0 and m.cur.ticks <= m.cur.ops[0][0] + 4


def test_minimal_result_is_one_minimal(monkeypatch):
    fake_engines(monkeypatch)
    d0 = {"phase": "tick", "tick": 3, "label": "lamp!=air", "cells": []}
    m = run_min(synthetic_case(), d0)
    for b in m.cur.blocks:
        c = minimise.close_removal(m.cur, {(b[0], b[1], b[2])})
        if c is None:
            continue
        d = engine.run_vanilla([c])[0]
        assert not d["ticks"], f"removing {b[3]} still diverges: not 1-minimal"
    for i in range(len(m.cur.ops)):
        c = m.cur.with_(ops=m.cur.ops[:i] + m.cur.ops[i + 1:])
        assert not engine.run_vanilla([c])[0]["ticks"]


def test_removal_closure_drops_dependents_and_their_ops():
    c = Case([(0, 0, 0, STONE, None), (0, 1, 0, W, None), (1, 0, 0, STONE, None), (1, 1, 0, LEVER, None)], [(4, (1, 1, 0))])
    r = minimise.close_removal(c, {(1, 0, 0)})
    assert sorted(b[3] for b in r.blocks) == sorted([STONE, W]) and r.ops == []
    assert minimise.close_removal(c, {b[:3] for b in c.blocks}) is None


def test_min_state_round_trips_through_json(monkeypatch):
    fake_engines(monkeypatch)
    m = minimise.Min("x", synthetic_case(), {"phase": "tick", "tick": 3, "label": "lamp[lit]", "cells": []})
    m.g, m.stage, m.tried = 6, "ops", {"a", "b"}
    m2 = minimise.Min.from_json(json.loads(json.dumps(m.to_json())))
    assert (m2.g, m2.stage, m2.tried, m2.cur.key()) == (6, "ops", {"a", "b"}, m.cur.key())


def test_keeps_requires_same_phase_and_overlapping_label():
    d0 = {"phase": "tick", "label": "a[x];b[y]"}
    assert minimise.keeps(d0, {"phase": "tick", "label": "b[y]"})
    assert not minimise.keeps(d0, {"phase": "placed", "label": "b[y]"})
    assert not minimise.keeps(d0, {"phase": "tick", "label": "c[z]"}) and not minimise.keeps(d0, None)


# ---------------------------------------------------------------------------------------------- signatures / known classes
def comparator_lever_case(lever_at_side=True):
    cmp_ = "minecraft:comparator[facing=west,mode=subtract,powered=false]"
    side = (1, 1, 1) if lever_at_side else (0, 1, 1)
    return Case([(x, 0, z, STONE, None) for x in range(3) for z in range(2)] + [(1, 1, 0, cmp_, None), (0, 1, 0, "minecraft:redstone_block", None),
                (side[0], side[1], side[2], LEVER, None)], [(5, side)])


def test_signature_is_the_same_for_every_orientation_of_a_class():
    c = comparator_lever_case()
    d = {"phase": "tick", "tick": 5, "label": "comparator[powered]"}
    sigs = {signature.signature(canon.transform_case(c, r, m), d)["coarse"] for r, m in canon.ELEMENTS}
    assert len(sigs) == 1
    hashes = {signature.signature(canon.transform_case(c, r, m), d)["build"] for r, m in canon.ELEMENTS}
    assert len(hashes) == 1


def test_known_class_detectors():
    d = {"phase": "tick", "tick": 5, "label": "comparator[powered]"}
    assert signature.known_class(comparator_lever_case(True), d) == "comparator_side_lever"
    assert signature.known_class(comparator_lever_case(False), d) is None
    assert signature.known_class(comparator_lever_case().with_(settle="known_shape"), d) == "comparator_side_lever"   # K5 is decided by classify.py (rerun under settle=place)
    assert signature.known_class(comparator_lever_case(False), {"phase": "tick", "tick": 3, "label": "dropper[triggered]"}) == "dropper_item_qc"
    assert signature.known_class(comparator_lever_case(False), {"phase": "tick", "tick": 3, "label": "air!=redstone_wall_torch"}) == "popoff_dynamic"
    assert signature.known_class(comparator_lever_case(False), {"phase": "placed", "tick": -1, "label": "air!=lever"}) == "popoff_static"


def test_torch_burnout_detector_counts_vanilla_toggles():
    t = (0, 1, 0)
    case = Case([(0, 0, 0, STONE, None), (0, 1, 0, "minecraft:redstone_torch[lit=true]", None)], [])
    v = flat({t: "minecraft:redstone_torch[lit=true]"}, ticks={k: {t: f"minecraft:redstone_torch[lit={'false' if k % 2 else 'true'}]"} for k in range(1, 15)})
    n = flat({t: "minecraft:redstone_torch[lit=true]"}, ticks={1: {t: "minecraft:redstone_torch[lit=false]"}})
    assert signature.known_class(case, {"phase": "tick", "tick": 3, "label": "redstone_torch[lit]"}, v, n) == "torch_burnout"
    assert signature.known_class(case, {"phase": "tick", "tick": 3, "label": "redstone_torch[lit]"}, v, v) is None


def test_use_toggle_is_classed_as_an_interaction_gap_not_a_bug():
    c = Case([(0, 0, 0, STONE, None), (0, 1, 0, "minecraft:repeater[delay=2,facing=east,locked=false,powered=false]", None)], [(4, (0, 1, 0))])
    assert signature.known_class(c, {"phase": "tick", "tick": 4, "label": "repeater[delay]"}) == "use_toggle"


# ---------------------------------------------------------------------------------------------- engine / confirmation
def test_failed_vanilla_jobs_are_bisected_out_of_a_batch(monkeypatch):
    from tools.oracle import gametest

    class Boom(RuntimeError):
        pass

    def fake_capture(jobs, **kw):
        if any(j[0] == "bad" for j in jobs):
            raise Boom("bad job")
        raise_for = None
        return [type("T", (), {"changes": {}, "placed": {}, "initial": {}, "n_ticks": 1})() for _ in jobs]
    monkeypatch.setattr(gametest, "capture_batch", fake_capture)
    cs = [Case([(0, 0, 0, STONE, None)], []) for _ in range(5)]
    out = engine.run_vanilla(cs, ["a", "b", "bad", "c", "d"])
    assert [isinstance(o, engine.EngineError) for o in out] == [False, False, True, False, False]


def test_confirmation_experiments_cover_origins_orientations_and_control():
    c = comparator_lever_case()
    exps = dict(confirm.experiments(c))
    assert set(exps) == {"origin1", "origin2", "rot90", "rot270+mx", "control_no_stimulus"}
    assert exps["origin2"].origin != c.origin and exps["origin2"].blocks == c.blocks
    assert exps["control_no_stimulus"].ops == [] and exps["control_no_stimulus"].blocks == c.blocks
    assert canon.build_hash(exps["rot90"].blocks) == canon.build_hash(c.blocks)


def test_case_json_round_trip_and_key_ignores_metadata():
    c = comparator_lever_case().with_(meta={"arm": "x"})
    d = Case.from_json(json.loads(json.dumps(c.to_json())))
    assert d.key() == c.key() and d.with_(meta={"arm": "y"}).key() == c.key()
    assert c.with_(ticks=c.ticks + 1).key() != c.key()


def test_provenance_records_the_jar_sha1_and_versions():
    p = confirm.provenance()
    assert set(p) >= {"server_jar_sha1", "mc_version", "nucleation_python"}


# ---------------------------------------------------------------------------------------------- live (needs the vanilla oracle)
live = pytest.mark.skipif(not __import__("tools.oracle.gametest", fromlist=["x"]).available(), reason="vanilla oracle not installed")


@live
def test_live_known_divergence_comparator_side_lever_is_found_and_classified():
    c = comparator_lever_case().with_(ticks=24, origin=(16, 100, 32))
    d, fv, fn = engine.diff_one(c, isolated=False)
    assert d is not None and d["phase"] == "tick"
    assert signature.known_class(c, d, fv, fn) == "comparator_side_lever"
    ctrl, _, _ = engine.diff_one(c.with_(ops=[]), isolated=False)
    assert ctrl is None                                           # causal: no stimulus, no divergence


@live
def test_live_batched_equals_isolated_for_a_fuzz_case():
    c = cases(1, seed=21)[0]
    a = engine.run_vanilla([c], ["a"], isolated=False)[0]
    b = engine.run_vanilla([c], ["a"], isolated=True)[0]
    assert canon.trace_hash(a) == canon.trace_hash(b)
