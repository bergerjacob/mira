"""harness/agent.py: arms, R gating, caps, scoring, transcripts. All with scripted LLMs (no network)."""
import json

import pytest

from env.runner import run
from env.score import score
from harness import prompts
from harness.agent import RunConfig, build_tools, final_of, parse_feedback, read_transcript, run_episode, safe_name
from harness.llm import Budget, ScriptedLLM, assistant
from harness.reference import builder_source, flawed_script, reference_llm, reference_script


def direct_score(task, blocks):
    res = run(task.contract, blocks, seed=task.contract.eval_seed)
    return score(task.contract, res)


def names(tools):
    return {t["function"]["name"] for t in tools}


# ------------------------------------------------------------------ tool surface / R gating
@pytest.mark.parametrize("arm", ["blocks", "builder"])
def test_r0_hides_run_spec(arm, task):
    assert "run_spec" not in names(build_tools(arm, 0))
    assert "run_spec" in names(build_tools(arm, 3))
    llm = reference_llm(task("t0_lamp"), arm, 0)
    f = run_episode(RunConfig("t0_lamp", "m", arm, 0), llm)
    assert all("run_spec" not in c["tools"] for c in llm.calls)
    assert f["rounds_used"] == 0 and f["full_pass"]


@pytest.mark.parametrize("arm", ["blocks", "builder"])
def test_arm_tool_sets(arm):
    n = names(build_tools(arm, 3))
    assert n == ({"set_blocks", "fill", "remove", "view", "run_spec", "submit"} if arm == "blocks"
                 else {"build", "view", "run_spec", "submit"})


def test_prompt_states_one_shot_for_r0_and_round_cap_for_r3(task):
    t = task("t0_lamp")
    assert "ONE-SHOT" in prompts.task_prompt(t, "blocks", 0, 10)
    assert "at most 3 times" in prompts.task_prompt(t, "blocks", 3, 19)


def test_view_has_no_live_option():
    v = [t for t in build_tools("blocks", 3) if t["function"]["name"] == "view"][0]
    assert "live" not in v["function"]["parameters"]["properties"]


def test_view_live_argument_is_ignored(task):
    msgs = [assistant("", [("view", {"layer": 1, "live": True})]), assistant("", [("submit", {})])]
    llm = ScriptedLLM(msgs)
    f = run_episode(RunConfig("t0_lamp", "m", "blocks", 0), llm)
    tool_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert "error" not in tool_msg and "--- y=1 ---" in tool_msg and "legend" in tool_msg


def test_run_spec_call_cap(task):
    t = task("t0_lamp")
    msgs = [assistant("", [("set_blocks", {"blocks": [list(b[:4]) for b in t.solution]})])]
    msgs += [assistant("", [("run_spec", {})]) for _ in range(4)] + [assistant("", [("submit", {})])]
    llm = ScriptedLLM(msgs)
    f = run_episode(RunConfig("t0_lamp", "m", "blocks", 2), llm)
    results = [m["content"] for m in llm.calls[-1]["messages"] if m["role"] == "tool"]
    assert sum("[run_spec #" in r for r in results) == 2
    assert sum("feedback budget exhausted" in r for r in results) == 2
    assert f["rounds_used"] == 2 and f["refused_run_spec"] == 2 and len(f["spec_history"]) == 2


def test_run_spec_not_executable_at_r0_even_if_called(task):
    t = task("t0_lamp")
    msgs = [assistant("", [("run_spec", {})]), assistant("", [("submit", {})])]
    llm = ScriptedLLM(msgs)
    f = run_episode(RunConfig("t0_lamp", "m", "blocks", 0), llm)
    res = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "not available" in res and f["rounds_used"] == 0


def test_tool_result_footer_counts_turns_and_rounds(task):
    t = task("t0_lamp")
    llm = reference_llm(t, "blocks", 3)
    run_episode(RunConfig("t0_lamp", "m", "blocks", 3), llm)
    last = [m["content"] for m in llm.calls[-1]["messages"] if m["role"] == "tool"][-1]
    assert "[turn 3/30 | run_spec 1/3 used]" in last


# ------------------------------------------------------------------ scoring
@pytest.mark.parametrize("tid", ["t0_not", "t1_xor", "t1_t_flipflop"])
@pytest.mark.parametrize("arm", ["blocks", "builder"])
def test_submit_score_equals_direct_env_scoring(tid, arm, task):
    t = task(tid)
    f = run_episode(RunConfig(tid, "m", arm, 3), reference_llm(t, arm, 3))
    d = direct_score(t, t.solution)
    assert f["score"] == d.to_dict() and f["reward"] == pytest.approx(d.reward, abs=1e-4)
    assert f["full_pass"] and f["ended_by"] == "submit" and f["submitted"]


def test_flawed_build_scored_with_hidden_spec_equals_direct(task):
    from env import mutate
    t = task("t1_xor")
    bad = mutate.apply(t.solution, t.broken[0]["edits"])
    f = run_episode(RunConfig("t1_xor", "m", "blocks", 0), ScriptedLLM(flawed_script(t, "blocks", 0, 0)))
    d = direct_score(t, bad)
    assert f["score"] == d.to_dict() and not f["full_pass"]


def test_unsubmitted_end_is_scored_as_it_stands(task):
    t = task("t0_not")
    msgs = [assistant("", [("set_blocks", {"blocks": [list(b[:4]) for b in t.solution]})])] + [assistant("", [("view", {"layer": 1})])] * 10
    f = run_episode(RunConfig("t0_not", "m", "blocks", 0, max_turns=4), ScriptedLLM(msgs))
    assert f["ended_by"] == "max_turns" and not f["submitted"] and f["turns"] == 4 and f["complete"]
    assert f["score"] == direct_score(t, t.solution).to_dict() and f["full_pass"]


def test_unsubmitted_empty_build_still_gets_a_score(task):
    f = run_episode(RunConfig("t0_not", "m", "blocks", 0, max_turns=2), ScriptedLLM([assistant("", [("view", {"layer": 1})])] * 3))
    assert f["blocks"] == 0 and not f["full_pass"] and f["reward"] < 1 and "score" in f


def test_output_token_cap_ends_episode_and_scores(task):
    t = task("t0_not")
    msgs = [assistant("", [("set_blocks", {"blocks": [list(b[:4]) for b in t.solution]})])] + [assistant("", [("view", {"layer": 1})])] * 5
    f = run_episode(RunConfig("t0_not", "m", "blocks", 3, max_output_tokens=5), ScriptedLLM(msgs))
    assert f["ended_by"] == "token_cap" and f["turns"] == 1 and f["full_pass"]


def test_max_turns_default_is_fixed_and_independent_of_rounds():
    assert [RunConfig("t", "m", "blocks", r).max_turns for r in (0, 3, 8, 25)] == [30, 30, 30, 30]


def test_turns_mode_scaled_keeps_the_round_1_budget_and_explicit_cap_wins():
    assert RunConfig("t", "m", "blocks", 0, turns_mode="scaled").max_turns == 10
    assert RunConfig("t", "m", "blocks", 8, turns_mode="scaled").max_turns == 34
    assert RunConfig("t", "m", "blocks", 8, turns_mode="scaled", max_turns=7).max_turns == 7
    with pytest.raises(ValueError):
        RunConfig("t", "m", "blocks", 0, turns_mode="elastic")


def test_idle_replies_get_nudged_then_end(task):
    llm = ScriptedLLM([assistant("I think I am done.")] * 5)
    f = run_episode(RunConfig("t0_not", "m", "blocks", 0), llm)
    assert f["ended_by"] == "no_tool_calls" and f["turns"] == 3
    assert "No tool call received" in llm.calls[1]["messages"][-1]["content"]


def test_bad_tool_arguments_do_not_crash(task):
    bad_json = {"id": "c1", "type": "function", "function": {"name": "set_blocks", "arguments": '{"blocks": [[1,1'}}
    msgs = [assistant("", [bad_json, ("nosuchtool", {}), ("fill", {"x1": "a"}), ("set_blocks", {"blocks": "not json"})]),
            assistant("", [("submit", {})])]
    llm = ScriptedLLM(msgs)
    f = run_episode(RunConfig("t0_not", "m", "blocks", 0), llm)
    res = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "not valid JSON" in res[0] and "not available" in res[1] and res[2].startswith("error") and res[3].startswith("error")
    assert f["submitted"]


def test_calls_after_submit_in_same_message_are_skipped(task):
    msgs = [assistant("", [("submit", {}), ("view", {"layer": 1})])]
    f = run_episode(RunConfig("t0_not", "m", "blocks", 0), ScriptedLLM(msgs))
    assert f["ended_by"] == "submit" and f["turns"] == 1


# ------------------------------------------------------------------ prompts never leak hidden checks
def test_task_prompt_has_only_public_vectors(task):
    for tid in ("t1_xor", "t1_clock", "t2_door_2x2"):
        t = task(tid)
        c = t.contract
        text = prompts.task_prompt(t, "blocks", 3, 19)
        pub_rows = [v for v in c.vectors() if v.public]
        hidden_rows = [v for v in c.vectors() if not v.public]
        assert text.count("  set {") >= len(pub_rows)
        for v in hidden_rows:
            line = f"set {v.inputs} -> expect {v.expect}"
            if not any(f"set {p.inputs} -> expect {p.expect}" == line for p in pub_rows):
                assert line not in text
        for s in c.behaviour.sequences:
            if not s.public:
                assert f"sequence '{s.name}'" not in text
        assert "eval_seed" not in text and "hidden_seed" not in text
        # reference stats / block lists are not part of the prompt
        assert "ref_blocks" not in text and "reference" not in text.lower().replace("reference_model", "")


def test_prompt_contains_ports_bbox_and_gotchas(task):
    t = task("t1_xor")
    text = prompts.task_prompt(t, "blocks", 3, 19)
    for p in t.contract.ports:
        assert f"{p.name}:" in text and str(list(p.pos)) in text
    assert str(t.contract.build_box[:3]) in text
    sysmsg = prompts.system_prompt("blocks")
    assert "facing` is the side the signal comes FROM" in sysmsg and "AWAY from the block" in sysmsg
    assert prompts.system_prompt("builder").startswith(prompts.BUILDER_SYSTEM_PATH.read_text()[:200])


def test_system_prompt_per_arm():
    assert "set_blocks" in prompts.system_prompt("blocks") and "build(source)" not in prompts.system_prompt("blocks")
    assert "Harness protocol" in prompts.system_prompt("builder")
    with pytest.raises(ValueError):
        prompts.system_prompt("nope")


# ------------------------------------------------------------------ builder arm
def builder_msgs(*sources, tail=("submit",)):
    msgs = [assistant("", [("build", {"source": s})]) for s in sources]
    return msgs + [assistant("", [(n, {}) for n in tail])]


def test_builder_syntax_error_is_fed_back_and_build_unchanged(task):
    t = task("t0_lamp")
    good = builder_source(t.solution)
    llm = ScriptedLLM(builder_msgs("def build(b)\n  pass\n", good, tail=("view", "submit")))
    f = run_episode(RunConfig("t0_lamp", "m", "builder", 0), llm)
    first = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert first.startswith("COMPILE FAILED") and "syntax" in first and "line" in first and "unchanged" in first
    assert [a["ok"] for a in f["build_attempts"]] == [False, True] and f["build_attempts"][0]["kinds"] == ["syntax"]
    assert f["full_pass"]


def test_builder_error_does_not_replace_previous_good_build(task):
    t = task("t0_lamp")
    bad = "from simulation.builder import *\ndef build(b):\n    b.set((1,1,1), 'minecraft:redstone_wir')\n"
    f = run_episode(RunConfig("t0_lamp", "m", "builder", 0), ScriptedLLM(builder_msgs(builder_source(t.solution), bad)))
    assert [a["kinds"] for a in f["build_attempts"]] == [[], ["unknown_block"]]
    assert f["full_pass"] and f["blocks"] == len(t.solution)          # the good build survived the failed one


def test_builder_unknown_property_reported_with_line_number(task):
    src = "from simulation.builder import *\n\ndef build(b):\n    b.set((1,1,1), 'minecraft:repeater[delay=9]')\n"
    llm = ScriptedLLM(builder_msgs(src, tail=("submit",)))
    run_episode(RunConfig("t0_lamp", "m", "builder", 0), llm)
    res = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "ERROR line 4" in res and ("bad_value" in res or "unknown_property" in res)


def test_builder_sandbox_violation_reported(task):
    src = "import os\ndef build(b):\n    pass\n"
    llm = ScriptedLLM(builder_msgs(src))
    f = run_episode(RunConfig("t0_lamp", "m", "builder", 0), llm)
    assert f["build_attempts"][0]["ok"] is False and f["blocks"] == 0


def test_builder_out_of_box_rejected_by_task_rules(task):
    src = "from simulation.builder import *\ndef build(b):\n    b.set((50,1,1), wire())\n"
    llm = ScriptedLLM(builder_msgs(src))
    f = run_episode(RunConfig("t0_lamp", "m", "builder", 0), llm)
    res = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "NOT loaded" in res and "outside the build box" in res
    assert f["build_attempts"][0]["kinds"] == ["r0_check"] and f["blocks"] == 0


def test_builder_identical_fixed_blocks_ignored_different_rejected(task):
    t = task("t0_lamp")
    base = builder_source(t.solution)
    ident = base + "    b.set((0, 1, 1), lever(face='floor', facing=EAST))\n    b.set((5, 1, 1), lamp())\n"
    llm = ScriptedLLM(builder_msgs(ident))
    f = run_episode(RunConfig("t0_lamp", "m", "builder", 0), llm)
    res = [m["content"] for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "build loaded" in res and "2 identical to fixed" in res and f["full_pass"]
    clash = base + "    b.set((5, 1, 1), stone())\n"
    f2 = run_episode(RunConfig("t0_lamp", "m", "builder", 0), ScriptedLLM(builder_msgs(clash)))
    assert f2["build_attempts"][0]["ok"] is False and f2["blocks"] == 0


def test_builder_and_blocks_arms_score_identically_on_same_design(task):
    t = task("t1_nand")
    a = run_episode(RunConfig("t1_nand", "m", "blocks", 3), reference_llm(t, "blocks", 3))
    b = run_episode(RunConfig("t1_nand", "m", "builder", 3), reference_llm(t, "builder", 3))
    assert a["score"] == b["score"]


def test_builder_arm_view_shows_loaded_build(task):
    t = task("t0_lamp")
    llm = ScriptedLLM(builder_msgs(builder_source(t.solution), tail=("view",)) + [assistant("", [("submit", {})])])
    run_episode(RunConfig("t0_lamp", "m", "builder", 0), llm)
    view = [m["content"] for m in llm.calls[2]["messages"] if m["role"] == "tool"][-1]
    assert "redstone_wire" in view


# ------------------------------------------------------------------ transcripts / determinism / stops
def test_transcript_is_complete_jsonl(task, tmp_path):
    t = task("t1_xor")
    cfg = RunConfig("t1_xor", "m", "blocks", 3, seed=4)
    f = run_episode(cfg, reference_llm(t, "blocks", 3, cost_per_call=0.001), exp_dir=tmp_path)
    p = tmp_path / "runs" / f"{safe_name(cfg.key)}.jsonl"
    rows = read_transcript(p)
    assert [r["type"] for r in rows] == ["header", "turn", "turn", "turn", "turn", "final"]
    h = rows[0]
    assert (h["arm"], h["model"], h["task"], h["R"], h["seed"]) == ("blocks", "m", "t1_xor", 3, 4)
    assert h["system"] and "TASK t1_xor" in h["task_prompt"] and "run_spec" in h["tools"]
    turn = rows[2]
    assert turn["cost"] == 0.001 and turn["usage"]["prompt_tokens"] > 0 and turn["results"][0]["name"] == "set_blocks"
    assert turn["results"][0]["args"]["blocks"] and turn["assistant"]["tool_calls"]
    fin = rows[-1]
    assert fin["score"]["reward"] == 1.0 and fin["cost_usd"] == pytest.approx(0.004) and fin["usage"]["prompt_tokens"] > 0
    assert fin["details"] and fin["spec_history"][0]["full_pass"] and "FULL PASS" in fin["final_text"]
    assert final_of(p)["key"] == cfg.key and f["key"] == cfg.key


def test_runs_are_deterministic(task):
    t = task("t1_xor")
    a = run_episode(RunConfig("t1_xor", "m", "blocks", 3, seed=1), ScriptedLLM(flawed_script(t, "blocks", 3, 1, 2)))
    b = run_episode(RunConfig("t1_xor", "m", "blocks", 3, seed=1), ScriptedLLM(flawed_script(t, "blocks", 3, 1, 2)))
    for k in ("score", "turns", "rounds_used", "spec_history", "details"):
        assert a[k] == b[k]


def test_seed_is_passed_to_provider(task):
    llm = ScriptedLLM([assistant("", [("submit", {})])])
    run_episode(RunConfig("t0_not", "m", "blocks", 0, seed=3), llm)
    assert llm.calls[0]["seed"] == 3001


def test_budget_exhausted_mid_episode_scores_current_build(task, ledger):
    t = task("t0_not")
    llm = ScriptedLLM(reference_script(t, "blocks", 3), budget=Budget(0.25, "b", ledger), cost_per_call=0.1)
    f = run_episode(RunConfig("t0_not", "m", "blocks", 3), llm)
    assert f["ended_by"] == "budget" and f["complete"] is False and f["turns"] == 2
    assert f["blocks"] == len(t.solution) and "score" in f          # build had been placed before the budget ran out


def test_budget_exhausted_before_first_turn_leaves_no_transcript(task, ledger, tmp_path):
    llm = ScriptedLLM(reference_script(task("t0_not"), "blocks", 0), budget=Budget(0.05, "b", ledger), cost_per_call=0.1)
    cfg = RunConfig("t0_not", "m", "blocks", 0)
    f = run_episode(cfg, llm, exp_dir=tmp_path)
    assert f["ended_by"] == "budget" and f["started"] is False
    assert not list((tmp_path / "runs").glob("*.jsonl"))


def test_stop_file_ends_episode_incomplete(task, tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("x")
    f = run_episode(RunConfig("t0_not", "m", "blocks", 0), reference_llm(task("t0_not"), "blocks", 0), exp_dir=tmp_path, stop_file=stop)
    assert f.get("complete") is False and f["ended_by"] == "stopped"


def test_parse_feedback_extracts_causes():
    text = ("BUILD: 3 blocks\nR0 COMPILE: ok\nR1 STRUCTURE: FAIL\n  - no_popoff: floating wire at (1,1,1)\n  - at_rest: still changing\n"
            "R2 CONNECTIVITY: 50% of required input->output paths reached\n  - A -> Y: no circuit path\n"
            "R3 BEHAVIOUR (public vectors): 0/2 pass\n  invariant no_oscillation: bad\nSILENT components (never changed): x\nHIDDEN checks: 0/4")
    d = parse_feedback(text)
    assert d["r1_violations"] == ["no_popoff: floating wire at (1,1,1)", "at_rest: still changing"]
    assert d["r2_missing"] == 1 and d["invariants_failed"] == ["no_oscillation"] and d["silent"]
    d0 = parse_feedback("BUILD\nR0 COMPILE: FAIL\n  - [1, 1, 1] is a fixed cell\nSCORE: reward=0.00")
    assert d0["r0_errors"] == ["[1, 1, 1] is a fixed cell"]
