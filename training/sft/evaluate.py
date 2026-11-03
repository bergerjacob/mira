#!/usr/bin/env python3
"""Evaluate a base model or base + LoRA adapter.

Two modes (both $0, local GPU, resumable in chunks like scripts/run_local_baseline.py):

  calls    fast format check: for held-out SFT episodes, generate the FIRST assistant turn from the episode's own prompt and
           report how often it is a parseable tool call with valid JSON arguments to an offered tool (and, for build calls, whether
           the program compiles in the sandbox). Answers "does the tuned model still call tools?" in ~1 min.
  harness  the real agent loop (harness/agent.py) on a task split: env tiers (t0, t1, ...) and/or the held-out synthetic tasks of
           an SFT run (its manifest's eval episodes -> data/synth/tasks/<family>/<task>.json); pass rate etc. via
           harness/analysis.py (same report as the baselines).

    /usr/bin/python3 training/sft/evaluate.py calls --model qwen3-1.7b --run data/training_runs/sft_smoke_1.7b [--base]
    /usr/bin/python3 training/sft/evaluate.py harness --model qwen3-1.7b --run data/training_runs/sft_smoke_1.7b \
        --tasks t0 --synth-heldout 8 --rounds 0,3 --exp-id eval_sft_smoke --time-budget-s 450
    /usr/bin/python3 training/sft/evaluate.py report --exp-id eval_sft_smoke
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from training.local.paths import ROOT, setup_paths  # noqa: E402

setup_paths()

SYNTH_TASKS = ROOT / "data" / "synth" / "tasks"


def synth_task_path(task_id: str):
    hits = sorted(SYNTH_TASKS.glob(f"*/{task_id}.json"))
    return hits[0] if hits else None


def heldout_episodes(run_dir: Path, data=None):
    """Eval-side episodes of an SFT run (manifest keys file:line) re-read from the run's data directory."""
    run = json.loads((run_dir / "run.json").read_text())
    man = json.loads((run_dir / "manifest.json").read_text())
    from training.sft.dataset import load_episodes
    eps = {f"{e['meta']['file']}:{e['meta']['line']}": e for e in load_episodes(data or run["data"], verified_only=False)}
    return [eps[k] for k in man["eval"] if k in eps]


def check_calls(llm, episodes, limit: int = 12, max_tokens: int = 3072):
    """Generate the first assistant turn for each episode prompt; classify the reply."""
    from simulation import builder as B
    rows = []
    for ep in episodes[:limit]:
        msgs = ep["messages"]
        k = next(i for i, m in enumerate(msgs) if m["role"] == "assistant")
        offered = {t["function"]["name"] for t in ep["tools"]}
        t0 = time.time()
        comp = llm.chat(msgs[:k], ep["tools"], max_tokens=max_tokens, temperature=0.0, seed=0)
        calls = comp.message.get("tool_calls") or []
        r = {"task": ep["meta"].get("task"), "kind": ep["meta"].get("kind"), "n_calls": len(calls), "finish": comp.finish_reason,
             "completion_tokens": comp.usage["completion_tokens"], "seconds": round(time.time() - t0, 1),
             "names": [c["function"]["name"] for c in calls], "valid_json": True, "known_tool": True, "compiles": None}
        for c in calls:
            try:
                args = json.loads(c["function"]["arguments"])
                if not isinstance(args, dict):
                    r["valid_json"] = False
            except ValueError:
                r["valid_json"] = False
                args = {}
            if c["function"]["name"] not in offered:
                r["known_tool"] = False
            if c["function"]["name"] == "build" and isinstance(args, dict) and isinstance(args.get("source"), str):
                res = B.compile(args["source"], timeout=20.0, max_blocks=50_000)
                r["compiles"] = bool(res.ok)
                if not res.ok:
                    r["compile_error"] = "; ".join(f"{e.kind} line {e.line}: {e.message}" for e in res.errors[:2])[:300]
        r["ok"] = bool(calls) and r["valid_json"] and r["known_tool"] and r["compiles"] is not False
        rows.append(r)
    n = len(rows)
    summary = {"episodes": n, "tool_call_rate": sum(1 for r in rows if r["n_calls"]) / max(1, n),
               "format_ok_rate": sum(1 for r in rows if r["n_calls"] and r["valid_json"] and r["known_tool"]) / max(1, n),
               "valid_call_rate": sum(1 for r in rows if r["ok"]) / max(1, n),
               "build_calls": sum(1 for r in rows if r["compiles"] is not None),
               "build_compiles": sum(1 for r in rows if r["compiles"]),
               "mean_completion_tokens": sum(r["completion_tokens"] for r in rows) / max(1, n)}
    return summary, rows


def cmd_calls(a):
    from harness.llm_local import LocalLLM
    run_dir = Path(a.run)
    eps = heldout_episodes(run_dir)
    adapter = None if a.base else (run_dir / "adapter" if (run_dir / "adapter").exists() else run_dir / "checkpoint")
    llm = LocalLLM(a.model, adapter=adapter)
    summary, rows = check_calls(llm, eps, a.limit, a.max_tokens)
    summary.update({"model": a.model, "adapter": str(adapter) if adapter else None})
    print(json.dumps(summary, indent=1))
    for r in rows:
        print(f"  {r['task']:28s} {r['kind'] or '':10s} calls={r['names']} ok={r['ok']} compiles={r['compiles']} "
              f"{r['completion_tokens']} tok {r['seconds']} s")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    return 0


def cmd_harness(a):
    from harness.agent import RunConfig
    from harness.experiment import pending, resolve_tasks, run_grid
    from harness.llm import EXPERIMENTS_DIR
    from harness.llm_local import local_factory
    tasks = resolve_tasks(a.tasks) if a.tasks else []
    if a.synth_heldout:
        seen = []
        for ep in heldout_episodes(Path(a.run)):
            t = ep["meta"].get("task")
            p = synth_task_path(t) if t else None
            if p is not None and str(p) not in seen:
                seen.append(str(p))
        tasks += seen[:a.synth_heldout]
    run_dir = Path(a.run) if a.run else None
    adapter = None
    if run_dir is not None and not a.base:
        adapter = run_dir / "adapter" if (run_dir / "adapter").exists() else run_dir / "checkpoint"
    label = a.model + ("+" + run_dir.name if adapter else "")
    kw = {"max_turns": a.max_turns} if a.max_turns else {}
    cfgs = [RunConfig(task=t, model=label, arm="builder", rounds=int(r), seed=s, max_tokens=a.max_tokens,
                      temperature=a.temperature, **kw) for s in range(a.seeds) for t in tasks for r in a.rounds.split(",")]
    exp_dir = EXPERIMENTS_DIR / a.exp_id
    todo = pending(cfgs, exp_dir)
    print(f"{len(cfgs)} runs planned, {len(todo)} pending; model {label}")
    if not todo:
        return 0
    t0 = time.time()
    factory = local_factory(a.model, exp_dir=exp_dir, adapter=adapter, model_label=label)
    deadline = t0 + a.time_budget_s if a.time_budget_s else None
    inner = factory

    def fac(cfg, budget):
        if deadline is not None and time.time() > deadline - 60:
            (exp_dir / "STOP").write_text("local time budget")
        llm = inner(cfg, budget)
        llm.deadline = deadline
        return llm
    s = run_grid(a.exp_id, cfgs, 1.0, ledger=exp_dir / "ledger.jsonl", workers=1, inprocess_factory=fac,
                 info={"backend": "local", "model": a.model, "adapter": str(adapter) if adapter else None})
    print(f"done: {s['finished']} finished, {s['remaining']} still pending ({time.time() - t0:.0f} s)")
    return 0


def cmd_report(a):
    from harness.llm import EXPERIMENTS_DIR
    import importlib.util
    spec = importlib.util.spec_from_file_location("rlb", ROOT / "scripts" / "run_local_baseline.py")
    rlb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rlb)
    from harness.analysis import build_report
    exp_dir = EXPERIMENTS_DIR / a.exp_id
    text = build_report(exp_dir, f"Local eval {a.exp_id}", exp_dir / "ledger.jsonl") + rlb.local_metrics(exp_dir)
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calls")
    c.add_argument("--model", default="qwen3-1.7b")
    c.add_argument("--run", required=True, help="SFT run dir (manifest.json, adapter/ or checkpoint/)")
    c.add_argument("--base", action="store_true", help="evaluate the base model (no adapter) on the same prompts")
    c.add_argument("--limit", type=int, default=12)
    c.add_argument("--max-tokens", type=int, default=3072)
    c.add_argument("--out")
    h = sub.add_parser("harness")
    h.add_argument("--model", default="qwen3-1.7b")
    h.add_argument("--run", help="SFT run dir (adapter + held-out synthetic tasks)")
    h.add_argument("--base", action="store_true")
    h.add_argument("--tasks", help="env task spec, e.g. t0,t1")
    h.add_argument("--synth-heldout", type=int, default=0, help="also run the first N held-out synthetic tasks of --run")
    h.add_argument("--rounds", default="0,3")
    h.add_argument("--seeds", type=int, default=1)
    h.add_argument("--max-tokens", type=int, default=3072)
    h.add_argument("--temperature", type=float, default=0.7)
    h.add_argument("--max-turns", type=int, help="cap assistant turns (default: harness default 30)")
    h.add_argument("--exp-id", required=True)
    h.add_argument("--time-budget-s", type=float, default=450)
    r = sub.add_parser("report")
    r.add_argument("--exp-id", required=True)
    r.add_argument("--out")
    a = ap.parse_args(argv)
    return {"calls": cmd_calls, "harness": cmd_harness, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
