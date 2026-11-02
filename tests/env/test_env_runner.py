import copy

from env.contract import Contract, TruthTable, Vector
from env.runner import prepare, run

WIRE = "minecraft:redstone_wire"


def fresh(task):
    return Contract.from_dict(copy.deepcopy(task.contract.to_dict()))


def test_reference_passes_with_all_invariants(task):
    t = task("t0_not")
    r = run(t.contract, t.solution)
    assert r.compiled and not r.prepared.errors
    assert r.invariant_failures() == {}
    assert all(v.ok for v in r.vectors)
    assert set(r.invariants) >= {"at_rest", "paste_stable", "deterministic", "no_oscillation", "no_collateral", "no_popoff", "no_forbidden"}


def test_missing_wire_fails_with_first_divergence_and_waveform(task):
    t = task("t0_lamp")
    r = run(t.contract, [b for b in t.solution if b[0] != 3])
    bad = [v for v in r.vectors if not v.ok]
    assert bad and all(v.inputs == {"A": 1} for v in bad)
    v = bad[0]
    assert v.diverge["port"] == "Y" and v.diverge["expected"] == 1 and v.diverge["got"] == 0
    assert v.trace["Y"] == "0@0" and v.trace["in:A"] == "1@0"


def test_timing_trace_shows_when_output_changes(task):
    t = task("t0_delay")
    r = run(t.contract, t.solution)
    seq = {q.name: q for q in r.sequences}["on_delay"]
    assert seq.ok
    wave = seq.trace["Y"].split()
    assert wave[0] == "0@0" and wave[1].startswith("1@") and 10 <= int(wave[1].split("@")[1]) <= 22


def test_block_outside_build_box_is_an_r0_error(task):
    t = task("t0_lamp")
    r = run(t.contract, t.solution + [[50, 1, 1, WIRE]])
    assert not r.compiled and any("outside the build box" in e for e in r.prepared.errors)


def test_writing_a_fixed_cell_is_rejected(task):
    t = task("t0_lamp")
    r = run(t.contract, t.solution + [[5, 1, 1, "minecraft:air"]])  # air does not clear fixed lamp...
    assert r.compiled  # air = remove-from-build only
    r = run(t.contract, t.solution + [[5, 1, 1, "minecraft:redstone_block"]])
    assert not r.compiled and any("fixed cell" in e for e in r.prepared.errors)


def test_invalid_state_is_an_r0_error_with_message(task):
    t = task("t0_lamp")
    r = run(t.contract, t.solution + [[1, 2, 1, "minecraft:redstone_wir"]])
    assert not r.compiled and any("unknown block" in e for e in r.prepared.errors)
    r = run(t.contract, t.solution + [[1, 2, 1, "minecraft:repeater[delay=9]"]])
    assert any("invalid" in e for e in r.prepared.errors)


def test_malformed_block_list_does_not_raise(task):
    t = task("t0_lamp")
    r = run(t.contract, [[1, 2]])
    assert not r.compiled and r.prepared.errors


def test_forbidden_block_flagged(task):
    t = task("t0_lamp")
    r = run(t.contract, t.solution + [[1, 2, 2, "minecraft:command_block"]])
    assert not r.invariants["no_forbidden"]["ok"]


def test_floating_component_flagged_as_popoff(task):
    t = task("t0_lamp")
    r = run(t.contract, t.solution + [[3, 2, 1, WIRE]])
    assert not r.invariants["no_popoff"]["ok"] and "[3, 2, 1]" in r.invariants["no_popoff"]["detail"]


def test_block_budget_enforced(task):
    c = fresh(task("t0_lamp"))
    c.budgets.max_blocks = 2
    r = run(c, task("t0_lamp").solution)
    assert not r.compiled and any("budget" in e for e in r.prepared.errors)


def test_runs_are_deterministic_for_a_seed_and_results_do_not_depend_on_seed(task):
    t = task("t1_xor")
    a, b = run(t.contract, t.solution, seed=3), run(t.contract, t.solution, seed=3)
    assert a.fingerprint == b.fingerprint and a.invariants["deterministic"]["ok"]
    c = run(t.contract, t.solution, seed=4)
    assert [v.ok for v in c.vectors] == [v.ok for v in a.vectors]


def test_vectors_run_in_seeded_random_order_without_reset(task):
    t = task("t1_xor")
    # the trace of the first evaluated vector starts from the at-rest state, later ones from the previous vector:
    # different seeds therefore give different waveforms for the same vector index
    traces = {s: tuple(v.trace["Y"] for v in run(t.contract, t.solution, seed=s, fast=True).vectors) for s in range(6)}
    assert len(set(traces.values())) > 1


def test_leftover_state_is_detected_by_order_independence_check(task):
    # a latch is not combinational: declare it so and the second random order must disagree for some seed
    c = fresh(task("t1_rs_latch"))
    c.behaviour.sequences = []
    c.behaviour.truth_table = TruthTable([Vector({"S": 1, "R": 0}, {"Q": 1}), Vector({"S": 0, "R": 1}, {"Q": 0}),
                                         Vector({"S": 0, "R": 0}, {"Q": None}), Vector({"S": 0, "R": 0}, {"Q": None}, label="again")])
    flagged = [s for s in range(8) if not run(c, task("t1_rs_latch").solution, seed=s, fast=True).invariants.get("order_independent", {"ok": True})["ok"]]
    assert flagged, "hold state should make some random order produce different outputs"


def test_oscillation_detected_for_vectors_that_never_settle(task):
    c = fresh(task("t1_clock"))
    c.invariants.no_oscillation = True
    c.behaviour.sequences = []
    c.behaviour.truth_table = TruthTable([Vector({"E": 1}, {"Y": 1})], settle=40)
    r = run(c, task("t1_clock").solution, fast=True)
    assert not r.invariants["no_oscillation"]["ok"] and not r.vectors[0].settled


def test_paste_stability_catches_observer_clock(task):
    # observers facing each other are quiescent when loaded InWorld but start clocking when pasted (Placement)
    t = task("t0_lamp")
    c = fresh(t)
    c.build_box = [0, 1, 0, 5, 3, 2]
    r = run(c, [[1, 2, 2, "minecraft:observer[facing=east]"], [2, 2, 2, "minecraft:observer[facing=west]"],
                [1, 1, 2, "minecraft:stone"], [2, 1, 2, "minecraft:stone"]])
    assert not r.invariants["paste_stable"]["ok"]


def test_collateral_when_a_piston_pushes_a_fixed_block_out_of_the_box(task):
    t = task("t0_lamp")
    sol = [[x, 1, 1, WIRE] for x in range(1, 4)] + [[4, 1, 1, "minecraft:piston[facing=east]"]]
    r = run(t.contract, sol)
    assert not r.invariants["no_collateral"]["ok"]


def test_pad_input_places_and_removes_a_redstone_block(task):
    t = task("t0_pad_lamp")
    r = run(t.contract, t.solution)
    assert r.invariants["no_collateral"]["ok"]  # the harness-placed pad block is not 'collateral'
    ys = [v for v in r.vectors if v.inputs == {"P": 1}]
    assert ys and all(v.got["Y"] == 1 for v in ys)


def test_button_pulse_input(task):
    t = task("t1_pulse_extender")
    r = run(t.contract, t.solution)
    assert all(q.ok for q in r.sequences)


def test_init_relative_expectations_handle_either_polarity(task):
    # the door spec says "same as at rest" / "opposite"; the reference starts open, so open = 0 resolves correctly
    t = task("t2_door_2x2")
    r = run(t.contract, t.solution, public_only=True, fast=True)
    assert r.sequences and all(q.ok for q in r.sequences)
    assert all(w.value in (0, 1) for q in r.sequences for w in q.windows)


def test_keep_clear_and_no_write_areas(task):
    t = task("t2_door_2x2")
    r = run(t.contract, t.solution + [[0, 2, 0, "minecraft:stone"]], fast=True)
    assert not r.compiled and any("reserved area" in e for e in r.prepared.errors)


def test_public_only_skips_hidden_sequences(task):
    t = task("t1_rs_latch")
    pub = run(t.contract, t.solution, public_only=True, fast=True)
    full = run(t.contract, t.solution, fast=True)
    assert len(pub.sequences) == 2 and len(full.sequences) == 4


def test_engine_errors_become_r0_not_exceptions(task):
    t = task("t0_lamp")
    c = fresh(t)
    c.budgets.total = 3   # tick budget exceeded mid-run
    r = run(c, t.solution)
    assert not r.compiled and any("tick budget" in e for e in r.prepared.errors)


def test_prepare_accepts_dict_blocks():
    from env.task import load_task
    t = load_task("t0_lamp")
    blocks = [{"x": b[0], "y": b[1], "z": b[2], "state": b[3]} for b in t.solution]
    assert prepare(t.contract, blocks).build == prepare(t.contract, t.solution).build


def test_export_litematic_saves_the_settled_at_rest_world(tmp_path, task):
    import json
    from nucleation import Schematic
    from env.runner import export_litematic
    t = task("t0_not")
    path = tmp_path / "not.litematic"
    n = export_litematic(t.contract, t.solution, path)
    back = {(b["x"], b["y"], b["z"]): b for b in json.loads(Schematic.load_from_file(str(path)).get_non_air_blocks_json())}
    assert len(back) == n
    lamp = back[(7, 1, 1)]
    assert lamp["name"] == "minecraft:redstone_lamp" and ["lit", "true"] in lamp["properties"]  # NOT gate rests lit
    wire = back[(6, 1, 1)]
    assert ["power", "13"] in wire["properties"] or any(k == "power" and int(v) > 0 for k, v in wire["properties"])


def test_export_refuses_unsettled_or_invalid_builds(tmp_path, task):
    import pytest
    from env.runner import export_litematic
    t = task("t1_clock")
    c = fresh(t)
    c.budgets.rest = 5
    # clock with the lever off is at rest -> fine
    export_litematic(c, t.solution, tmp_path / "a.litematic")
    with pytest.raises(ValueError):
        export_litematic(t.contract, [[1, 1, 1, "minecraft:nope"]], tmp_path / "b.litematic")


def test_blocks_the_simulator_cannot_load_are_named_in_r0(task):
    t = task("t0_lamp")
    r = run(t.contract, t.solution + [[1, 2, 1, "minecraft:daylight_detector"], [2, 2, 1, "minecraft:spruce_button"]])
    assert not r.compiled
    msg = " ".join(r.prepared.errors)
    assert "daylight_detector" in msg and "spruce_button" in msg and "cannot be simulated" in msg
    # the supported twins work
    assert run(t.contract, t.solution + [[1, 2, 1, "minecraft:stone_button[face=floor]"]], fast=True).compiled
