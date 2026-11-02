import json
import threading

import pytest

from conftest import ROOT, hidden_markers
from env.task import load_task
from harness.reference import builder_source
from mira_mcp import knowledge, session as S


def solve(sid_task, arm, rounds=3, task=None, extras="", **kw):
    """Start a session and build the reference solution into it; returns the Session (not yet submitted)."""
    t = task or load_task(sid_task)
    s = S.start(sid_task, arm=arm, rounds=rounds, extras=extras, **kw)
    if arm == "blocks":
        out = s.call("set_blocks", blocks=[list(b[:4]) for b in t.solution])
        assert "REJECTED" not in out, out
    else:
        out = s.call("build", source=builder_source(t.solution))
        assert out.startswith("build loaded"), out
    return s


# --------------------------------------------------------------------------- reference solutions score 1.0
@pytest.mark.parametrize("tid", ["t0_lamp", "t0_not", "t0_and", "t0_piston", "t1_xor", "t1_clock", "t1_nand"])
@pytest.mark.parametrize("arm", ["blocks", "builder"])
def test_reference_solution_submits_full_pass(tid, arm):
    s = solve(tid, arm)
    out = s.call("submit")
    res = S.Session.load(s.id).state()["result"]
    assert res["reward"] == 1.0 and res["full_pass"] is True, out
    assert res["task"] == tid and res["arm"] == arm


def test_run_spec_reports_public_pass_and_counts():
    s = solve("t0_not", "blocks", rounds=3)
    out = s.call("run_spec")
    assert "ALL PUBLIC CHECKS PASS" in out and "run_spec 1/3 used" in out
    assert s.state()["rounds_used"] == 1


# --------------------------------------------------------------------------- R budget
def test_round_budget_enforced_and_refused_call_not_counted():
    s = solve("t0_lamp", "blocks", rounds=2)
    assert "[run_spec #1]" in s.call("run_spec")
    assert "[run_spec #2]" in s.call("run_spec")
    out = s.call("run_spec")
    assert "budget exhausted" in out
    assert s.call("run_spec").startswith("error")
    assert s.state()["rounds_used"] == 2
    assert "FULL PASS" in s.call("submit")      # submit still works after the budget is gone


def test_zero_rounds_is_one_shot():
    s = solve("t0_lamp", "builder", rounds=0)
    assert "not available" in s.call("run_spec") and s.state()["rounds_used"] == 0
    assert "FULL PASS" in s.call("submit")


def test_unlimited_rounds():
    s = solve("t0_lamp", "blocks", rounds=None)
    for _ in range(6):
        assert "[run_spec #" in s.call("run_spec")
    assert s.state()["rounds_used"] == 6
    assert s.state()["rounds"] is None


def test_negative_rounds_means_unlimited():
    assert S.start("t0_lamp", "blocks", rounds=-1).state()["rounds"] is None


# --------------------------------------------------------------------------- arms
def test_arm_gating():
    b = S.start("t0_lamp", "blocks", rounds=1)
    assert "only available in the builder arm" in b.call("build", source="x")
    u = S.start("t0_lamp", "builder", rounds=1)
    for tool, kw in (("set_blocks", {"blocks": [[1, 1, 1, "minecraft:stone"]]}), ("fill", dict(x1=1, y1=1, z1=1, x2=2, y2=1, z2=1, state="minecraft:stone")),
                     ("remove", dict(x1=1, y1=1, z1=1))):
        assert "not available in the builder arm" in u.call(tool, **kw)
    assert u.state()["cells"] == []


def test_blocks_arm_edit_tools_and_undo():
    s = S.start("t0_lamp", "blocks", rounds=1)
    assert "placed 1" in s.call("set_blocks", blocks=[[1, 1, 1, "minecraft:redstone_wire"]])
    assert "filled 3" in s.call("fill", x1=2, y1=1, z1=1, x2=4, y2=1, z2=1, state="minecraft:redstone_wire")
    assert len(s.export()) == 4
    assert "build now has 2 blocks" in s.call("remove", x1=2, y1=1, z1=1, x2=3, y2=1, z2=1)
    assert len(s.export()) == 2
    assert s.call("undo").startswith("undone") and len(s.export()) == 4
    assert s.call("undo").startswith("undone") and len(s.export()) == 1
    assert s.call("undo").startswith("undone") and len(s.export()) == 0
    assert s.call("undo") == "nothing to undo"


def test_set_blocks_input_forms():
    s = S.start("t0_lamp", "blocks", rounds=1)
    assert "placed 1" in s.call("set_blocks", blocks='[[1,1,1,"minecraft:redstone_wire"]]')           # JSON string
    assert "placed 1" in s.call("set_blocks", blocks=[[2.0, 1, 1, "minecraft:redstone_wire"]])        # float coordinate
    assert "placed 1" in s.call("set_blocks", blocks=[{"x": 3, "y": 1, "z": 1, "state": "minecraft:redstone_wire"}])
    assert s.call("set_blocks", blocks="not json").startswith("error")
    assert "REJECTED" in s.call("set_blocks", blocks=[[99, 1, 1, "minecraft:stone"], [1, 1, 2, "minecraft:redstone_wir"]])
    assert len(s.export()) == 3


def test_builder_compile_error_keeps_previous_build():
    s = S.start("t0_lamp", "builder", rounds=1)
    ok = s.call("build", source="from simulation.builder import *\ndef build(b):\n    b.points(wire(), (1,1,1), (2,1,1))\n")
    assert ok.startswith("build loaded: 2 blocks")
    bad = s.call("build", source="def build(b)\n    pass\n")
    assert bad.startswith("COMPILE FAILED") and "unchanged" in bad
    assert len(s.export()) == 2
    outside = s.call("build", source="from simulation.builder import *\ndef build(b):\n    b.set((50,1,1), stone())\n")
    assert "NOT loaded" in outside and len(s.export()) == 2
    # a successful build replaces, not adds
    assert s.call("build", source="from simulation.builder import *\ndef build(b):\n    b.set((3,1,1), wire())\n").startswith("build loaded: 1 blocks")
    assert len(s.export()) == 1
    assert s.call("undo").startswith("undone") and len(s.export()) == 2
    assert len(list((s.dir / "sources").glob("*.py"))) == 4


def test_fenced_source_is_accepted():
    s = S.start("t0_lamp", "builder", rounds=1)
    out = s.call("build", source="```python\nfrom simulation.builder import *\ndef build(b):\n    b.set((1,1,1), wire())\n```")
    assert out.startswith("build loaded")


# --------------------------------------------------------------------------- persistence
def test_state_persists_across_handles():
    s = S.start("t0_not", "blocks", rounds=3, session_id="persist1")
    s.call("set_blocks", blocks=[[1, 1, 1, "minecraft:redstone_wire"]])
    s.call("run_spec")
    s2 = S.Session.load("persist1")                      # a fresh handle (e.g. another process)
    st = s2.state()
    assert st["rounds_used"] == 1 and len(st["cells"]) == 1 and st["calls"]["run_spec"] == 1
    s2.call("set_blocks", blocks=[[2, 1, 1, "minecraft:stone"]])
    assert len(S.Session.load("persist1").export()) == 2
    assert s.call("undo").startswith("undone") and len(s2.export()) == 1     # undo history survives too
    # round budget survives: 2 left
    s2.call("run_spec"); s2.call("run_spec")
    assert "budget exhausted" in S.Session.load("persist1").call("run_spec")


def test_transcript_and_result_files(sessions_dir):
    s = solve("t0_lamp", "builder")
    s.call("run_spec")
    s.call("submit")
    lines = [json.loads(ln) for ln in (s.dir / "transcript.jsonl").read_text().splitlines()]
    assert lines[0]["event"] == "start"
    assert [ln["tool"] for ln in lines if ln["event"] == "call"] == ["build", "run_spec", "submit"]
    res = json.loads((s.dir / "result.json").read_text())
    for k in ("session", "task", "arm", "R", "rounds_used", "reward", "full_pass", "r0", "r1", "r2", "r3", "r4", "blocks", "hidden", "calls", "wall_s"):
        assert k in res
    assert res["hidden"].split("/")[0] == res["hidden"].split("/")[1]
    assert json.loads((s.dir / "final_blocks.json").read_text())
    summary = [json.loads(ln) for ln in (sessions_dir / "results.jsonl").read_text().splitlines()]
    assert len(summary) == 1 and summary[0]["session"] == s.id and summary[0]["reward"] == 1.0
    assert (s.dir / "sources" / "001.py").exists()


def test_submit_ends_session():
    s = solve("t0_lamp", "blocks")
    s.call("submit")
    assert "already called" in s.call("set_blocks", blocks=[[1, 1, 2, "minecraft:stone"]])
    assert "already called" in s.call("submit")
    assert "already called" in s.call("run_spec")
    assert s.state()["done"] is True
    assert S.Session.load(s.id).describe().count("SUBMITTED") == 1
    assert "--- y=1" in s.call("view", layer=1)          # read-only calls still work


def test_unsubmitted_session_scores_nothing(sessions_dir):
    solve("t0_lamp", "blocks")
    assert not (sessions_dir / "results.jsonl").exists()


def test_session_ids(sessions_dir):
    for bad in ("../x", "a/b", ".hidden", "", "x" * 200):
        with pytest.raises(S.SessionError):
            S.Session(bad)
    S.start("t0_lamp", "blocks", session_id="dup")
    with pytest.raises(S.SessionError, match="already exists"):
        S.start("t0_lamp", "blocks", session_id="dup")
    with pytest.raises(S.SessionError, match="no session"):
        S.Session.load("nope")
    assert [r["id"] for r in S.list_sessions()] == ["dup"]


def test_concurrent_calls_are_serialised():
    s = S.start("t0_lamp", "blocks", rounds=1)
    errs = []

    def put(i):
        try:
            S.Session.load(s.id).call("set_blocks", blocks=[[i % 3 + 1, 1, 0 if i < 3 else 2, "minecraft:stone"]])
        except Exception as e:   # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=put, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(s.export()) == 6 and s.state()["calls"]["set_blocks"] == 6


# --------------------------------------------------------------------------- hidden information
@pytest.mark.parametrize("tid", ["t1_rs_latch", "t1_pulse_extender", "t1_clock", "t0_delay"])
def test_hidden_sequences_never_leak(tid, sessions_dir):
    t = load_task(tid)
    marks = hidden_markers(t)
    assert marks, "task has no hidden-only sequence to check"
    s = solve(tid, "blocks")
    seen = [s.call("task_description"), s.call("guide"), s.call("show"), s.call("view", layer=1), s.call("run_spec"), s.call("submit")]
    seen += [s.call("probe", pos=t.contract.ports[-1].name)]          # refused; must not leak either
    blob = "\n".join(seen)
    for m in marks:
        assert m not in blob, m
    assert "HIDDEN checks" in blob and "details withheld" in blob
    # nothing on disk carries the contract: no eval seed field, no vectors, no reference solution
    disk = "\n".join(p.read_text() for p in s.dir.rglob("*") if p.is_file() and p.name != "final_blocks.json")
    assert "eval_seed" not in disk and "truth_table" not in disk and '"reference"' not in disk
    for m in marks:
        assert m not in disk


def test_truth_table_hidden_rows_are_reordered_copies_only():
    # for combinational tasks the hidden vectors repeat the public inputs in another order: nothing extra can leak, and the
    # session must still say hidden checks exist
    s = solve("t0_and", "blocks")
    assert "Hidden vectors/sequences of the same kind are also checked." in s.call("task_description")


def test_task_description_equals_public_contract_plus_rules():
    t = load_task("t1_xor")
    text = S.task_text(t, "blocks", 3, set())
    assert t.contract.describe(public=True) in text
    assert "at most 3 time" in text


# --------------------------------------------------------------------------- extras gating
def test_extras_default_off_then_on():
    off = solve("t0_lamp", "blocks", rounds=3)
    assert "disabled" in off.call("probe", pos="Y")
    assert "disabled" in off.call("search_components", query="and")
    assert "disabled" in off.call("search_corpus", query="and")
    live = off.call("view", layer=1, live=True)
    assert "live view is disabled" in live and "lamp[lit=" in live
    on = solve("t0_lamp", "blocks", rounds=3, extras="probe")
    out = on.call("probe", pos="Y", inputs={"A": 1}, ticks=10)
    assert out.startswith("probe Y")
    assert "live view is disabled" not in on.call("view", layer=1, live=True)
    with pytest.raises(S.SessionError):
        S.start("t0_lamp", "blocks", extras="telepathy")
    assert S.parse_extras("all") == set(S.EXTRAS)


def test_probe_position_string_and_inputs_string():
    s = solve("t0_lamp", "blocks", extras="probe")
    assert s.call("probe", pos="5,1,1", inputs="A=1", ticks=5).startswith("probe [5, 1, 1]")


def test_components_through_build():
    if not (knowledge.LIB / "not_gate.py").exists():
        pytest.skip("lib/ not present")
    s = S.start("t0_not", "builder", rounds=1, extras="components")
    src = "def build(b):\n    b.place(not_gate, at=(1, 0, 1), floor=False, name='n')\n"
    out = s.call("build", source=src, components=["not_gate"])
    assert "prelude lines" in out or out.startswith("build loaded") or "NOT loaded" in out
    off = S.start("t0_not", "builder", rounds=1)
    assert "disabled" in off.call("build", source=src, components=["not_gate"])


# --------------------------------------------------------------------------- truncation, tasks
def test_output_truncated_with_notice(monkeypatch):
    s = S.start("t2_door_2x2", "blocks", rounds=1)
    monkeypatch.setenv("MIRA_MAX_CHARS", "600")
    out = s.call("view", layer=1)
    assert len(out) < 600 + 200 and "[truncated" in out
    assert S.clip("short") == "short"


def test_view_requires_small_slices():
    s = S.start("t2_door_2x2", "blocks", rounds=1)
    assert "error" in s.call("view", axis="y")            # too many layers at once


def test_list_tasks():
    allt = S.list_tasks()
    assert "t0_lamp" in allt and "t1_xor" in allt and "t2_door_2x2" in allt
    t0 = S.list_tasks("t0")
    assert "t0_lamp" in t0 and "t1_xor" not in t0
    assert "no tasks" in S.list_tasks("t9")
    assert len(S.task_files()) >= 22
    assert "oracle_report" not in S.task_files()


def test_custom_contract_paths(tmp_path):
    base = json.loads((ROOT / "env/tasks/t0_lamp.json").read_text())
    full = tmp_path / "mine.json"
    full.write_text(json.dumps(base))
    s = S.start(str(full), "blocks", rounds=1)
    s.call("set_blocks", blocks=[list(b[:4]) for b in base["solution"]])
    assert "FULL PASS" in s.call("submit")
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(base["contract"]))                                  # contract only, no reference solution
    s2 = S.start(str(bare), "blocks", rounds=1)
    s2.call("set_blocks", blocks=[list(b[:4]) for b in base["solution"]])
    assert "SCORE" in s2.call("submit")
    with pytest.raises(ValueError, match="unknown task"):
        S.start("no_such_task")


def test_tier3_task_loads():
    ids = [i for i in S.task_files() if i.startswith("t3_")]
    if not ids:
        pytest.skip("no t3 tasks")
    s = S.start(ids[0], "builder", rounds=1)
    assert ids[0] in s.call("task_description")


def test_builder_guide_contains_part_api():
    b, k = S.guide_text("builder"), S.guide_text("blocks")
    assert "Part" in b and "Redstone facts" in b and "Redstone facts" in k and len(b) > len(k)


# --------------------------------------------------------------------------- knowledge tools
def test_explain_block():
    out = knowledge.explain_block("repeater")
    assert "delay: 1 | 2 | 3 | 4" in out and "facing" in out and "simulatable in this environment: yes" in out
    assert "minecraft:comparator" in knowledge.explain_block("minecraft:comparator")
    assert "mode: compare | subtract" in knowledge.explain_block("comparator")
    assert "INVALID" in knowledge.explain_block("repeater[delay=5]")
    assert "validates" in knowledge.explain_block("repeater[delay=2]")
    assert "unknown block" in knowledge.explain_block("redstone_wir") and "redstone_wire" in knowledge.explain_block("redstone_wir")
    assert knowledge.explain_block("").startswith("error")
    assert "NO" in knowledge.explain_block("daylight_detector")


def test_search_components():
    if not (knowledge.LIB / "and2.meta.json").exists():
        pytest.skip("lib/ not present")
    out = knowledge.search_components("and gate")
    assert out.startswith("and2") and "ports A:in" in out and "components=['and2']" in out
    assert "no component matches" in knowledge.search_components("zzzzqqq")
    assert len(knowledge.search_components("", limit=3).split("\n\n")) <= 4


def test_search_corpus_filters_licence_and_size():
    if not knowledge.CORPUS_INDEX.exists():
        pytest.skip("corpus index not present")
    rows = knowledge._corpus()
    assert rows and all(r["ml_use"] in ("clean", "internal-only") for r in rows)
    out = knowledge.search_corpus("not gate", max_blocks=12, limit=3, show_program=1)
    assert "not_gate" in out and "def build(b)" in out
    for line in out.splitlines():
        if line.startswith("["):
            assert int(line.split(" blocks")[0].rsplit(" ", 1)[1]) <= 12
    assert "permission" not in out
    first_id = out.split("]")[0].lstrip("[")
    byid = knowledge.search_corpus(f"id:{first_id}", show_program=0)
    assert byid.startswith(f"[{first_id}]") and "program" not in byid
    assert "no corpus entry" in knowledge.search_corpus("zzzzqqq")
    capped = knowledge.search_corpus("farm", max_blocks=5)
    assert "no corpus entry" in capped or "5 blocks" in capped or "blocks" in capped


def test_search_corpus_truncates_long_programs():
    if not knowledge.CORPUS_INDEX.exists():
        pytest.skip("corpus index not present")
    out = knowledge.search_corpus("adder", max_blocks=500, limit=1, show_program=1)
    assert len(out) < knowledge.MAX_PROGRAM_CHARS + 2500


@pytest.mark.slow
def test_every_task_reference_reaches_full_pass_through_sessions():
    """All shipped T0-T3 references, both arms, through the session layer (what an external agent's perfect run would score)."""
    bad = []
    for tid in S.task_files():
        t = load_task(S.task_files()[tid])
        for arm in ("blocks", "builder"):
            s = S.start(tid, arm, rounds=0)
            out = s.call("set_blocks", blocks=[list(b[:4]) for b in t.solution]) if arm == "blocks" else s.call("build", source=builder_source(t.solution))
            s.call("submit")
            res = s.state()["result"]
            if not (res["reward"] == 1.0 and res["full_pass"]):
                bad.append((tid, arm, res["reward"], out[:120]))
    assert not bad, bad
