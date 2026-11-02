"""harness/experiment.py + scripts/run_experiment.py: grid planning, resume, budget stop, parallel workers, dry run."""
import importlib.util
import json
from pathlib import Path

import pytest

from harness.agent import RunConfig, final_of, safe_name
from harness.estimate import estimate_grid, estimate_run, measure
from harness.experiment import PILOT, completed_keys, pending, plan_grid, resolve_tasks, run_grid
from harness.llm import Budget, ScriptedLLM
from harness.reference import reference_llm, reference_script

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def small_grid(**kw):
    return plan_grid(["t0_lamp", "t0_not"], ["m1"], ["blocks", "builder"], [0, 3], 1, **kw)


# ------------------------------------------------------------------ planning
def test_resolve_tasks_tiers_and_ids():
    assert len(resolve_tasks("all")) == 22
    assert resolve_tasks("t2") == ["t2_door_2x2"]
    assert resolve_tasks("t0,t1_xor")[-1] == "t1_xor" and len(resolve_tasks("t0")) == 10
    with pytest.raises(ValueError):
        resolve_tasks("t9_nope")


def test_plan_grid_is_full_cross_product_in_paired_order():
    g = plan_grid(["a", "b"], ["m1", "m2"], ["blocks", "builder"], [0, 3, 8], 2)
    assert len(g) == 2 * 2 * 2 * 3 * 2
    assert len({c.key for c in g}) == len(g)
    assert [c.seed for c in g[:24]] == [0] * 24                        # seed is the outermost loop
    first_task = g[:12]
    assert {c.task for c in first_task} == {"a"}                       # then task: a partial run keeps tasks complete
    assert [c.rounds for c in g[:3]] == [0, 3, 8]


def test_pilot_preset_via_script():
    rx = load_script("run_experiment")
    a = rx.parse_args(["--exp-id", "p", "--budget-usd", "1", "--pilot"])
    cfgs = rx.build_plan(a)
    assert len({c.model for c in cfgs}) == 2 and len({c.task for c in cfgs}) == 6
    assert {c.rounds for c in cfgs} == {0, 3} and {c.seed for c in cfgs} == {0} and len(cfgs) == 2 * 6 * 2 * 2
    a = rx.parse_args(["--exp-id", "p", "--budget-usd", "1", "--pilot", "--models", "gemma-4-31b", "--rounds", "0,8"])
    cfgs = rx.build_plan(a)
    assert {c.model for c in cfgs} == {"gemma-4-31b"} and {c.rounds for c in cfgs} == {0, 8}


def test_budget_cap_is_a_required_argument():
    rx = load_script("run_experiment")
    with pytest.raises(SystemExit):
        rx.parse_args(["--exp-id", "p"])
    with pytest.raises(SystemExit):
        rx.parse_args(["--exp-id", "p", "--pilot", "--dry-run"])


def test_dry_run_prints_estimate_and_runs_nothing(tmp_path, capsys):
    rx = load_script("run_experiment")
    rc = rx.main(["--exp-id", "d", "--budget-usd", "0.01", "--pilot", "--dry-run", "--exp-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "48 runs planned" in out and "conservative cost estimate" in out and "EXCEEDS" in out
    assert "deepseek-v4-flash" in out and "R=3" in out
    assert not (tmp_path / "d").exists()                                # nothing written


def test_estimate_grows_with_rounds_and_price():
    s = measure("t1_xor", "blocks")
    e0, e3, e8 = (estimate_run(s, "deepseek-v4-flash", r) for r in (0, 3, 8))
    assert 0 < e0["usd"] < e3["usd"] < e8["usd"] and e0["turns"] < e3["turns"]
    assert estimate_run(s, "qwen3.7-plus", 3)["usd"] > e3["usd"]
    assert measure("t1_xor", "builder").prompt_tokens > s.prompt_tokens * 0.5
    g = estimate_grid(small_grid())
    assert g["runs"] == 8 and g["total"] > 0 and len(g["by_condition"]) == 4


# ------------------------------------------------------------------ running, resuming
def mock_factory(cost=0.0):
    from env.task import load_task
    return lambda cfg, budget: reference_llm(load_task(cfg.task), cfg.arm, cfg.rounds, model=cfg.model, budget=budget, cost_per_call=cost)


def test_run_grid_runs_everything_then_resumes_without_rerunning(tmp_path):
    cfgs = small_grid()
    s1 = run_grid("e", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl", inprocess_factory=mock_factory(), progress=lambda m: None)
    assert s1["finished"] == 8 and s1["skipped_done"] == 0 and s1["remaining"] == 0
    exp = tmp_path / "e"
    assert len(completed_keys(exp)) == 8 and (exp / "config.json").exists()
    mtimes = {p.name: p.stat().st_mtime_ns for p in (exp / "runs").glob("*.jsonl")}
    s2 = run_grid("e", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl", inprocess_factory=mock_factory(), progress=lambda m: None)
    assert s2["finished"] == 0 and s2["skipped_done"] == 8
    assert mtimes == {p.name: p.stat().st_mtime_ns for p in (exp / "runs").glob("*.jsonl")}      # untouched


def test_resume_redoes_only_unfinished_runs(tmp_path):
    cfgs = small_grid()
    run_grid("e", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl", inprocess_factory=mock_factory(), progress=lambda m: None)
    exp = tmp_path / "e"
    victim = exp / "runs" / f"{safe_name(cfgs[3].key)}.jsonl"
    lines = victim.read_text().splitlines()
    victim.write_text("\n".join(lines[:-1]) + "\n")                       # crash before the final record
    assert [c.key for c in pending(cfgs, exp)] == [cfgs[3].key]
    s = run_grid("e", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl", inprocess_factory=mock_factory(), progress=lambda m: None)
    assert s["finished"] == 1 and s["skipped_done"] == 7 and final_of(victim)["complete"]


def test_budget_stop_is_graceful_and_resumable(tmp_path):
    cfgs = plan_grid(["t0_lamp", "t0_not", "t0_and"], ["m1"], ["blocks"], [3], 1)
    ledger = tmp_path / "l.jsonl"
    # 4 calls per run at $0.1 -> a $1.0 cap cannot finish three runs (1.2)
    s = run_grid("e", cfgs, 1.0, exp_root=tmp_path, ledger=ledger, inprocess_factory=mock_factory(0.1), progress=lambda m: None)
    assert s["stopped_by"] == "budget" and s["finished"] == 2 and s["remaining"] == 1
    assert Budget(1.0, "e", ledger).spent() <= 1.0 + 1e-9
    partial = final_of(tmp_path / "e" / "runs" / f"{safe_name(cfgs[2].key)}.jsonl")
    assert partial is not None and partial["complete"] is False and partial["ended_by"] == "budget" and "score" in partial
    s2 = run_grid("e", cfgs, 3.0, exp_root=tmp_path, ledger=ledger, inprocess_factory=mock_factory(0.1), progress=lambda m: None)
    assert s2["finished"] == 1 and s2["remaining"] == 0                  # only the unfinished run is redone


def test_ledger_cap_stops_run_across_experiments(tmp_path):
    ledger = tmp_path / "l.jsonl"
    Budget(10, "other", ledger).charge(0.95)
    cfgs = plan_grid(["t0_lamp"], ["m1"], ["blocks"], [3], 1)
    s = run_grid("e", cfgs, 5.0, exp_root=tmp_path, ledger=ledger, ledger_cap_usd=1.0, inprocess_factory=mock_factory(0.1), progress=lambda m: None)
    assert s["stopped_by"] == "budget" and s["finished"] == 0
    assert Budget(10, "e", ledger).spent() == 0.0


def test_stop_file_created_by_user_ends_runs(tmp_path):
    cfgs = small_grid()
    exp = tmp_path / "e"
    seen = []

    def factory(cfg, budget):
        if len(seen) == 1:
            (exp / "STOP").write_text("x")             # created while the second run is about to start
        seen.append(cfg.key)
        from env.task import load_task
        return reference_llm(load_task(cfg.task), cfg.arm, cfg.rounds, model=cfg.model, budget=budget)
    s = run_grid("e", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl", inprocess_factory=factory, progress=lambda m: None)
    assert s["stopped_by"] == "stop_file" and s["finished"] == 1


def test_parallel_workers_share_one_experiment(tmp_path):
    cfgs = small_grid()
    s = run_grid("par", cfgs, 1.0, exp_root=tmp_path, workers=3, mock=True, progress=lambda m: None)
    assert s["finished"] == 8 and s["incomplete"] == 0
    finals = [final_of(p) for p in (tmp_path / "par" / "runs").glob("*.jsonl")]
    assert len(finals) == 8 and all(f["full_pass"] and f["complete"] for f in finals)
    assert (tmp_path / "par" / "ledger.jsonl").exists()                 # mock runs never touch the shared real ledger


def test_repeated_api_errors_abort_the_grid(tmp_path):
    from harness.llm import LLMError

    def factory(cfg, budget):
        def boom(m, t, n):
            raise LLMError("HTTP 404: no endpoints")
        return ScriptedLLM(boom, budget=budget)
    cfgs = plan_grid(["t0_lamp", "t0_not", "t0_and"], ["m1", "m2"], ["blocks", "builder"], [0, 3], 1)
    s = run_grid("e", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl", inprocess_factory=factory, progress=lambda m: None)
    assert s["stopped_by"] == "repeated_api_errors" and s["finished"] == 0 and s["incomplete"] == 6
