"""The CLI drives the same on-disk sessions: separate processes resume each other's state."""
import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT
from env.task import load_task
from harness.reference import builder_source
from mira_mcp import session as S

CLI = str(ROOT / "scripts" / "mira_env_cli.py")


def cli(*args, stdin=None, env_extra=None, check=True):
    env = {**os.environ, **(env_extra or {})}
    p = subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, input=stdin, env=env, cwd="/tmp", timeout=120)
    if check:
        assert p.returncode == 0, p.stdout + p.stderr
    return p


def test_full_blocks_episode_through_cli(sessions_dir, tmp_path):
    t = load_task("t0_lamp")
    out = cli("start", "t0_lamp", "--arm", "blocks", "--rounds", "2", "--id", "cli1", "--label", "unit").stdout
    assert "started session cli1" in out and "TASK t0_lamp" in out and "MIRA_SESSION=cli1" in out
    f = tmp_path / "blocks.json"
    f.write_text(json.dumps([list(b[:4]) for b in t.solution]))
    assert "placed 4" in cli("set", "--file", str(f)).stdout
    assert "ALL PUBLIC CHECKS PASS" in cli("run-spec").stdout
    assert "FULL PASS" in cli("submit").stdout
    show = cli("show").stdout
    assert "SUBMITTED" in show and "run_spec used 1" in show
    res = json.loads((sessions_dir / "results.jsonl").read_text().splitlines()[0])
    assert res["reward"] == 1.0 and res["label"] == "unit" and res["session"] == "cli1"


def test_set_forms_fill_remove_view_undo(sessions_dir, tmp_path):
    cli("start", "t0_lamp", "--arm", "blocks", "--rounds", "1")
    assert "placed 1" in cli("set", "1", "1", "1", "minecraft:redstone_wire").stdout
    assert "placed 2" in cli("set", "--json", '[[2,1,1,"minecraft:stone"],[3,1,1,"minecraft:stone"]]').stdout
    lines = tmp_path / "b.txt"
    lines.write_text("# comment\n4 1 1 minecraft:redstone_wire\n1,1,2,minecraft:stone\n")
    assert "placed 2" in cli("set", "--file", str(lines)).stdout
    assert "filled 2" in cli("fill", "1", "1", "0", "2", "1", "0", "minecraft:stone").stdout
    assert "build now has 5 blocks" in cli("remove", "1", "1", "0", "2", "1", "0").stdout
    v = cli("view", "--layer", "1").stdout
    assert "--- y=1 ---" in v and "legend:" in v
    assert cli("undo").stdout.startswith("undone")
    assert cli("tasks", "--tier", "t0").stdout.count("t0_") >= 10


def test_builder_arm_stdin_and_resume_in_new_process(sessions_dir):
    t = load_task("t0_not")
    sid = "cli_b"
    cli("start", "t0_not", "--arm", "builder", "--rounds", "inf", "--id", sid)
    out = cli("build", "--file", "-", stdin=builder_source(t.solution)).stdout
    assert out.startswith("build loaded: 6 blocks")
    # a second, unrelated "agent" shell attaches via env var instead of the .current pointer
    other = cli("show", env_extra={"MIRA_SESSION": sid}).stdout
    assert "arm builder" in other and "R inf" in other and "6 blocks" in other
    assert "FULL PASS" in cli("--session", sid, "submit").stdout


def test_two_sessions_do_not_share_pointer(sessions_dir):
    cli("start", "t0_lamp", "--arm", "blocks", "--id", "a1")
    cli("start", "t0_not", "--arm", "blocks", "--id", "b1")
    assert "t0_not" in cli("show").stdout                                    # .current -> latest
    assert "t0_lamp" in cli("--session", "a1", "show").stdout
    assert "t0_lamp" in cli("show", env_extra={"MIRA_SESSION": "a1"}).stdout
    assert len(cli("sessions").stdout.strip().splitlines()) == 2


def test_round_budget_through_cli():
    cli("start", "t0_lamp", "--arm", "blocks", "--rounds", "1")
    assert "[run_spec #1]" in cli("run-spec").stdout
    assert "budget exhausted" in cli("run-spec").stdout


def test_no_session_is_an_error():
    p = cli("show", check=False)
    assert p.returncode != 0 and "no active session" in (p.stdout + p.stderr)
    p = cli("--session", "ghost", "show", check=False)
    assert p.returncode != 0 and "no session" in (p.stdout + p.stderr)


def test_block_corpus_components_subcommands():
    assert "delay: 1 | 2 | 3 | 4" in cli("block", "repeater").stdout
    cli("start", "t0_lamp", "--arm", "blocks", "--extras", "components,corpus,probe")
    out = cli("components", "not gate").stdout
    assert "not_gate" in out or "no component" in out
    assert "blocks" in cli("corpus", "not gate", "--max-blocks", "10", "--program", "0").stdout
    assert "probe" in cli("probe", "Y", "--ticks", "3").stdout


def test_extras_off_by_default_in_cli():
    cli("start", "t0_lamp", "--arm", "blocks")
    assert "disabled" in cli("components", "and").stdout
    assert "disabled" in cli("corpus", "and").stdout
    assert "disabled" in cli("probe", "Y").stdout


def test_cli_output_is_compact_for_the_agent():
    out = cli("start", "t0_lamp", "--arm", "blocks").stdout
    assert len(out) < 4000


def test_t3_task_works_in_a_fresh_process():
    if not (ROOT / "env" / "tasks" / "t3" / "t3_adder2.json").exists():
        pytest.skip("no t3 tasks")
    out = cli("start", "t3_adder2", "--arm", "builder", "--rounds", "1").stdout
    assert "TASK t3_adder2" in out and "error" not in out.splitlines()[0]
