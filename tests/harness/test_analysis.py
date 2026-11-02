"""harness/analysis.py + scripts/analyze_experiment.py: statistics, lift, taxonomy, report on a synthetic experiment."""
import importlib.util
import math
from pathlib import Path

import pytest

from harness import analysis as A
from harness.agent import RunConfig
from harness.experiment import plan_grid, run_grid
from harness.llm import ScriptedLLM
from harness.reference import flawed_script

ROOT = Path(__file__).resolve().parents[2]


def fake(task="t0_a", model="m", arm="blocks", R=0, seed=0, full=False, reward=None, **kw):
    f = {"type": "final", "key": f"{task}|{model}|{arm}|R{R}|s{seed}", "task": task, "tier": "T1" if task.startswith("t1") else "T0",
         "model": model, "arm": arm, "R": R, "seed": seed, "complete": True, "ended_by": "submit", "submitted": True, "turns": 4,
         "rounds_used": min(R, 2), "full_pass": full, "reward": reward if reward is not None else (1.0 if full else 0.4),
         "r0": 1.0, "r1": 1.0, "r2": 1.0, "r3": 1.0 if full else 0.5, "r4": 0.0, "blocks": 5,
         "usage": {"prompt_tokens": 1000, "completion_tokens": 200}, "cost_usd": 0.01, "build_attempts": [], "details": {}}
    f.update(kw)
    return f


# ------------------------------------------------------------------ statistics
def test_wilson_interval_known_values():
    lo, hi = A.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)
    assert A.wilson(0, 10)[0] == 0.0 and A.wilson(10, 10)[1] == 1.0 and math.isnan(A.wilson(0, 0)[0])


def test_bootstrap_ci_is_seeded_and_brackets_the_mean():
    xs = [0, 1, 1, 0, 1, 1, 1, 0, 1, 1]
    a, b = A.bootstrap_ci(xs, seed=3), A.bootstrap_ci(xs, seed=3)
    assert a == b and a[0] <= A.mean(xs) <= a[1] and 0 <= a[0] < a[1] <= 1
    assert A.bootstrap_ci([0.5] * 6) == (0.5, 0.5)
    assert all(math.isnan(v) for v in A.bootstrap_ci([1.0]))


def test_percentile_interpolates():
    assert A.percentile([1, 2, 3, 4], 0.5) == 2.5 and A.percentile([1, 2, 3, 4], 0) == 1 and A.percentile([1, 2, 3, 4], 1) == 4


def test_paired_lift_math():
    base = {f"t{i}": v for i, v in enumerate([0, 0, 0, 1, 0, 0, 0, 0])}
    oth = {f"t{i}": v for i, v in enumerate([1, 1, 0, 1, 1, 0, 1, 0])}
    r = A.paired_lift(base, oth)
    assert r["n"] == 8 and r["base"] == 0.125 and r["other"] == 0.625
    assert r["diff"] == pytest.approx(0.5) and r["ratio"] == pytest.approx(5.0)
    lo, hi = r["diff_ci"]
    assert 0 < lo < 0.5 < hi <= 1.0


def test_paired_lift_uses_only_shared_tasks_and_handles_zero_base():
    r = A.paired_lift({"a": 0.0, "b": 0.0, "c": 1.0}, {"a": 1.0, "b": 0.0, "z": 1.0})
    assert r["n"] == 2 and r["diff"] == 0.5
    z = A.paired_lift({"a": 0.0, "b": 0.0}, {"a": 1.0, "b": 1.0})
    assert math.isnan(z["ratio"]) and z["diff"] == 1.0 and z["diff_ci"] == (1.0, 1.0)
    assert A.paired_lift({"a": 1}, {"b": 1}) is None


def test_lift_rows_clusters_by_task_and_pools_seeds():
    fs = []
    for t in ("t0_a", "t0_b", "t0_c", "t0_d"):
        for s in (0, 1):
            fs.append(fake(t, R=0, seed=s, full=(t == "t0_a" and s == 0)))             # R0: task a passes in 1 of 2 seeds
            fs.append(fake(t, R=3, seed=s, full=True))
    rows = {(r["model"], r["arm"], r["k"]): r for r in A.lift_rows(fs, ks=(3,))}
    r = rows[("m", "blocks", 3)]
    assert r["n"] == 4 and r["base"] == pytest.approx(0.125) and r["other"] == 1.0 and r["diff"] == pytest.approx(0.875)
    assert ("ALL", "blocks", 3) in rows
    assert A.lift_rows(fs, ks=(8,)) == []


def test_lift_table_and_headline_text():
    fs = [fake(t, R=r, full=(r == 8 or t == "t1_a")) for t in ("t1_a", "t1_b", "t1_c", "t1_d") for r in (0, 3, 8)]
    txt = A.lift_table(fs, ks=(8,))
    assert "R=8 vs R=0" in txt and "+0.75" in txt
    h = A.headline(fs)
    assert "criterion" in h and "MET" in h
    fs2 = [fake(t, R=r, full=False) for t in ("t1_a", "t1_b") for r in (0, 8)]
    assert "not met" in A.headline(fs2)


# ------------------------------------------------------------------ taxonomy
def test_classify_gate_order():
    assert A.classify(fake(full=True)) == "pass"
    assert A.classify(fake(r0=0.0, r1=0, r2=0, r3=0, details={"r0_errors": ["[9, 1, 1] minecraft:stone is outside the build box"]})).startswith("R0: outside the build box")
    assert "fixed cell" in A.classify(fake(r0=0.0, details={"r0_errors": ["[1, 1, 1] is a fixed cell (lever) and cannot be written"]}))
    assert "unknown" in A.classify(fake(r0=0.0, details={"r0_errors": ["[1, 1, 1]: unknown block minecraft:redstone_wir"]}))
    assert A.classify(fake(r1=0.75, details={"r1_violations": ["no_popoff: floating wire"]})) == "R1: no_popoff"
    assert A.classify(fake(r2=0.5)).startswith("R2")
    assert "empty build" in A.classify(fake(blocks=0, r3=0.0))
    assert "wrong" in A.classify(fake(r3=0.0))
    assert "partial" in A.classify(fake(r3=0.3)) and "mostly right" in A.classify(fake(r3=0.8))
    assert "invariant deterministic" in A.classify(fake(r3=0.99, details={"invariants_failed": ["deterministic"]}))


def test_taxonomy_table_counts_by_arm_and_r():
    fs = [fake(R=0, full=True), fake("t0_b", R=0, r3=0.0), fake("t0_c", R=0, r3=0.0), fake(R=3, full=True)]
    t = A.taxonomy_table(fs)
    assert "blocks R=0" in t and "2 (67%)" in t and "| pass | 1 (33%)" in t and "**runs**" in t


def test_builder_compile_table_counts_kinds_and_recovery():
    att_bad = [{"turn": 1, "ok": False, "kinds": ["syntax"], "stage": "compile"}, {"turn": 2, "ok": True, "kinds": [], "stage": "loaded"}]
    att_bad2 = [{"turn": 1, "ok": False, "kinds": ["unknown_block", "bad_value"], "stage": "compile"}]
    fs = [fake(arm="builder", build_attempts=att_bad), fake("t0_b", arm="builder", build_attempts=att_bad2), fake("t0_c", arm="blocks")]
    t = A.builder_compile_table(fs)
    assert "3 build() attempts in 2 builder episodes: 2 failed (67%)" in t and "1 of them ended with a loaded build" in t
    assert "syntax" in t and "unknown_block" in t and A.builder_compile_table([fake()]) is None


def test_effort_table_cost_per_solved():
    fs = [fake(full=True, cost_usd=0.02), fake("t0_b", full=False, cost_usd=0.04)]
    t = A.effort_table(fs)
    assert "$0.0300" in t and "$0.0600" in t                      # $/run and $ per solved (0.06 total / 1 solved)


def test_heatmap_symbols():
    fs = [fake("t0_a", R=0, full=True), fake("t0_b", R=0, full=False), fake("t0_a", R=3, full=True), fake("t0_b", R=3, full=True)]
    h = A.heatmap(fs)
    assert "█ 1.00" in h and "· 0.00" in h and "**pass rate**" in h


# ------------------------------------------------------------------ end to end on logged runs
def test_report_from_real_logged_runs(tmp_path, task):
    """Persona players: flawed at R=0, repaired after the first run_spec when R>0. Numbers must match a direct recount."""
    tasks = ["t0_not", "t0_and", "t1_nand"]
    cfgs = plan_grid(tasks, ["pm"], ["blocks", "builder"], [0, 3], 1)

    def factory(cfg, budget):
        t = task(cfg.task)
        return ScriptedLLM(flawed_script(t, cfg.arm, cfg.rounds, flaw=1, fix_at=1, compile_typo=(cfg.arm == "builder")), model=cfg.model,
                           budget=budget, cost_per_call=0.002)
    s = run_grid("syn", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl", inprocess_factory=factory, progress=lambda m: None)
    assert s["finished"] == 12
    ld = A.load_finals(tmp_path / "syn")
    fin = ld["complete"]
    assert len(fin) == 12 and not ld["incomplete"]
    p0 = A.mean(A.PASS(f) for f in fin if f["R"] == 0)
    p3 = A.mean(A.PASS(f) for f in fin if f["R"] == 3)
    assert p3 == 1.0 and p0 < 1.0
    rows = {(r["model"], r["arm"], r["k"]): r for r in A.lift_rows(fin, ks=(3,))}
    assert rows[("ALL", "blocks", 3)]["diff"] == pytest.approx(1.0 - A.mean(A.PASS(f) for f in fin if f["R"] == 0 and f["arm"] == "blocks"))
    text = A.build_report(tmp_path / "syn", "synthetic", tmp_path / "l.jsonl")
    for section in ("## Setup", "## Headline", "## 1. Pass rate", "## 2. Iteration lift", "## 3. Rounds, tokens", "## 4. Per-task",
                    "## 5. Failure taxonomy", "### Builder arm: compile attempts", "## 6. Caveats"):
        assert section in text
    assert "syntax" in text and "12 complete, 0 incomplete" in text and "pm" in text


def test_report_with_no_runs(tmp_path):
    (tmp_path / "e" / "runs").mkdir(parents=True)
    assert "No completed runs" in A.build_report(tmp_path / "e")


def test_incomplete_runs_are_excluded_but_counted(tmp_path, task):
    cfgs = plan_grid(["t0_not"], ["pm"], ["blocks"], [3], 1)
    run_grid("x", cfgs, 1.0, exp_root=tmp_path, ledger=tmp_path / "l.jsonl",
             inprocess_factory=lambda c, b: ScriptedLLM(flawed_script(task(c.task), c.arm, c.rounds), budget=b, cost_per_call=0.4),
             progress=lambda m: None)
    ld = A.load_finals(tmp_path / "x")
    assert len(ld["incomplete"]) == 1 and ld["incomplete"][0]["ended_by"] == "budget" and not ld["complete"]


def test_analyze_script_writes_markdown(tmp_path, task):
    cfgs = plan_grid(["t0_not"], ["pm"], ["blocks"], [0, 3], 1)
    run_grid("w", cfgs, 1.0, exp_root=tmp_path, mock=True, workers=1, progress=lambda m: None)
    spec = importlib.util.spec_from_file_location("analyze_experiment", ROOT / "scripts" / "analyze_experiment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "report.md"
    assert mod.main(["--exp-id", "w", "--exp-root", str(tmp_path), "--out", str(out), "--ledger", str(tmp_path / "w" / "ledger.jsonl")]) == 0
    assert out.read_text().startswith("# Experiment w") and "Iteration lift" in out.read_text()
    assert mod.main(["--exp-id", "nope", "--exp-root", str(tmp_path)]) == 2
