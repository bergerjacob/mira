import json

from env.tools import TOOL_NAMES, Env, tool_specs


def env_for(task, tid, **kw):
    return Env(task(tid), **kw)


def test_task_info_lists_ports_and_public_examples(task):
    txt = env_for(task, "t1_xor").task_info()
    assert "XOR" in txt and "A: input click" in txt and "Y: output lamp" in txt and "Example vectors" in txt


def test_set_blocks_places_and_rejects_bad_cells(task):
    e = env_for(task, "t0_lamp")
    out = e.set_blocks([[1, 1, 1, "minecraft:redstone_wire"], [0, 1, 1, "minecraft:stone"], [99, 1, 1, "minecraft:stone"],
                        [2, 1, 1, "minecraft:nonsense"]])
    assert "placed 1" in out and "REJECTED 3" in out and "fixed cell" in out and "outside the build box" in out and "unknown block" in out
    assert len(e.state.cells) == 1


def test_malformed_input_is_an_error_string(task):
    e = env_for(task, "t0_lamp")
    assert e.set_blocks("nope").startswith("error")
    assert e.set_blocks([[1, 2]]).startswith("error")


def test_fill_remove_undo(task):
    e = env_for(task, "t0_lamp")
    assert "filled 4" in e.fill(1, 1, 1, 4, 1, 1, "minecraft:redstone_wire")
    assert len(e.state.cells) == 4
    e.remove(2, 1, 1, 3, 1, 1)
    assert len(e.state.cells) == 2
    assert e.undo().startswith("undone") and len(e.state.cells) == 4
    assert e.undo().startswith("undone") and len(e.state.cells) == 0
    assert e.undo() == "nothing to undo"


def test_fill_skips_fixed_and_limits_size(task):
    e = env_for(task, "t0_lamp")
    assert "1 skipped" in e.fill(4, 1, 1, 5, 1, 1, "minecraft:stone") or "skipped" in e.fill(4, 1, 1, 5, 1, 1, "minecraft:stone")
    assert "exceeds the limit" in e.fill(-500, -500, -500, 500, 500, 500, "minecraft:stone")


def test_view_slice_has_legend_ports_and_arrows(task):
    e = env_for(task, "t1_pulse_extender")
    e.set_blocks(task("t1_pulse_extender").solution)
    v = e.view(layer=1)
    assert "legend:" in v and "ports in slice: B@" in v and ">" in v and "y=1" in v
    assert "L" not in e.view(layer=2)  # nothing on layer 2
    assert e.view(axis="q").startswith("error")


def test_live_view_shows_wire_power_hex(task):
    e = env_for(task, "t0_lamp")
    e.set_blocks(task("t0_lamp").solution)
    assert "legend" in e.view(layer=1, live=True)


def test_probe_reports_changes_over_time(task):
    t = task("t0_delay")
    e = Env(t)
    e.set_blocks(t.solution)
    out = e.probe("Y", ticks=30, inputs={"A": 1})
    assert "t=0: redstone_lamp[lit=false]" in out and "lit=true" in out
    assert "never changed" in e.probe("Y", ticks=10)
    assert e.probe("nope").startswith("error")
    assert e.probe("Y", inputs={"Y": 1}).startswith("error")


def test_run_spec_on_empty_build_explains_failure(task):
    e = env_for(task, "t0_not")
    out = e.run_spec()
    assert "[run_spec #1]" in out and "FAIL vector" in out and "first divergence" in out
    assert e.rounds == 1 and e.last_score.r3 < 1


def test_run_spec_passes_for_reference_and_only_shows_public(task):
    t = task("t1_xor")
    e = Env(t)
    e.set_blocks(t.solution)
    out = e.run_spec()
    assert "ALL PUBLIC CHECKS PASS" in out and "HIDDEN" not in out


def test_feedback_budget(task):
    e = env_for(task, "t0_lamp", max_rounds=2)
    e.run_spec(), e.run_spec()
    assert e.run_spec().startswith("error: feedback budget exhausted")


def test_submit_finishes_the_episode(task):
    t = task("t0_or")
    e = Env(t)
    e.set_blocks(t.solution)
    out = e.submit()
    assert "FULL PASS" in out and "HIDDEN checks: " in out and e.final.reward == 1.0 and e.done
    assert e.set_blocks([[1, 1, 1, "minecraft:stone"]]).startswith("episode finished")
    assert e.run_spec().startswith("episode finished")


def test_call_dispatch_and_specs(task):
    e = env_for(task, "t0_lamp")
    assert e.call("nope").startswith("error: unknown tool")
    assert "bad arguments" in e.call("fill", x1=1)
    specs = tool_specs()
    assert [s["name"] for s in specs] == TOOL_NAMES
    json.dumps(specs)
    assert all(s["description"] for s in specs)


def test_export_roundtrips_into_runner(task):
    from env.runner import run
    t = task("t1_nand")
    e = Env(t)
    e.set_blocks(t.solution)
    assert run(t.contract, e.export(), fast=True).invariant_failures() == {}


def test_unsimulatable_blocks_are_rejected_when_placed(task):
    e = env_for(task, "t0_lamp")
    assert "cannot be simulated" in e.set_blocks([[1, 2, 1, "minecraft:daylight_detector"]])
    assert e.fill(1, 2, 0, 2, 2, 0, "minecraft:sculk_sensor").startswith("error")
    assert len(e.state.cells) == 0
