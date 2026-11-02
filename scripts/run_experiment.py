#!/usr/bin/env python3
"""Run (or plan) the iteration-lift experiment: tasks x models x arms x R x seeds under a hard USD cap.

    python scripts/run_experiment.py --exp-id pilot1 --budget-usd 0.60 --pilot --dry-run
    python scripts/run_experiment.py --exp-id pilot1 --budget-usd 0.60 --pilot
    python scripts/run_experiment.py --exp-id full1 --budget-usd 2.00 --tasks t0,t1 --models deepseek-v4-flash \
        --arms blocks,builder --rounds 0,3,8 --seeds 2
    python scripts/run_experiment.py --exp-id smoke --budget-usd 1 --mock        # scripted reference player, no network
    /usr/bin/python3 scripts/run_experiment.py --exp-id local1 --budget-usd 1 --backend local --models qwen3-4b \
        --tasks t0 --arms builder --rounds 0,3 --seeds 1 --time-budget-s 480    # local HF model on the GPU ($0, see docs/TRAINING.md)

--budget-usd is REQUIRED (hard cap for this experiment; the shared ledger data/experiments/ledger.jsonl is also capped
by --ledger-cap-usd). Re-running the same command resumes: finished runs are skipped. See docs/HARNESS.md.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if "--backend" in sys.argv:  # local backend: system python (torch) borrows nucleation etc. from the repo .venv
    from training.local.paths import setup_paths
    setup_paths()

from harness.estimate import estimate_grid
from harness.experiment import PILOT, pending, plan_grid, resolve_tasks, run_grid
from harness.llm import DEFAULT_LEDGER, EXPERIMENTS_DIR, Budget, load_api_key


def csv(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--budget-usd", type=float, required=True, help="hard USD cap for this experiment (required, no default)")
    ap.add_argument("--ledger-cap-usd", type=float, default=3.0,
                    help="cap on the SUM of every experiment in the shared ledger (default 3.00; raise after topping up the key)")
    ap.add_argument("--pilot", action="store_true", help="preset: 2 models, 6 tasks, R in {0,3}, 1 seed (flags below override)")
    ap.add_argument("--tasks", help="all | t0 | t1 | t2 | comma list of task ids (default all)")
    ap.add_argument("--models", help="comma list of short names (OpenRouterClient.MODELS) or OpenRouter ids")
    ap.add_argument("--arms", default=None, help="blocks,builder (default both)")
    ap.add_argument("--rounds", default=None, help="comma list of feedback rounds R (default 0,3,8)")
    ap.add_argument("--seeds", type=int, default=None, help="number of seeds 0..N-1 (default 3)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, help="only the first N runs of the (ordered) grid")
    ap.add_argument("--dry-run", action="store_true", help="print the planned grid and a conservative cost estimate, run nothing")
    ap.add_argument("--mock", action="store_true", help="scripted reference-solution player instead of a real model (no network, $0)")
    ap.add_argument("--turns-mode", choices=["fixed", "scaled"], default="fixed",
                    help="fixed: 30 assistant turns for every R (default; R changes feedback only) | scaled: 10 + 3R (the round-1 grid)")
    ap.add_argument("--max-turns", type=int, help="explicit cap on assistant turns (overrides --turns-mode)")
    ap.add_argument("--max-tokens", type=int, default=16384, help="max output tokens per call")
    ap.add_argument("--max-output-tokens", type=int, default=40000, help="episode cap on completion tokens")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--reasoning", choices=["low", "medium", "high"], help="OpenRouter reasoning.effort (default: provider default)")
    ap.add_argument("--no-palette-hint", action="store_true")
    ap.add_argument("--no-blocks-examples", action="store_true", help="blocks arm without the worked examples (the round-1 prompt; default: with)")
    ap.add_argument("--keep-reasoning", action="store_true", help="echo provider reasoning_details back (try if a thinking model 400s mid-episode)")
    ap.add_argument("--no-require-params", action="store_true", help="do not send provider.require_parameters")
    ap.add_argument("--reasoning-tokens-est", type=int, default=800, help="dry-run: assumed hidden reasoning tokens per turn")
    ap.add_argument("--exp-root", default=str(EXPERIMENTS_DIR))
    ap.add_argument("--ledger", default=None, help=f"ledger path (default {DEFAULT_LEDGER}; <exp>/ledger.jsonl with --mock)")
    loc = ap.add_argument_group("local backend (harness/llm_local.py; run with /usr/bin/python3, which has torch)")
    loc.add_argument("--backend", choices=["openrouter", "local"], default="openrouter")
    loc.add_argument("--local-model", help="short name or hub id to load (default: the single --models entry)")
    loc.add_argument("--adapter", help="LoRA adapter dir (training/local/lora.py) merged into the local model")
    loc.add_argument("--thinking", action="store_true", help="Qwen3 hybrid models: enable_thinking=True")
    loc.add_argument("--max-context", type=int, help="prompt+completion token limit (default: what fits the 12 GB GPU, "
                     "training/local/models.MAX_CONTEXT)")
    loc.add_argument("--time-budget-s", type=float, help="stop starting generations after this many seconds; unfinished episodes "
                     "stay pending and replay their finished turns from <exp>/local_cache.jsonl on resume")
    return ap.parse_args(argv)


def build_plan(a):
    base = PILOT if a.pilot else {}
    tasks = resolve_tasks(a.tasks if a.tasks else base.get("tasks", "all"))
    models = csv(a.models) if a.models else base.get("models", ["deepseek-v4-flash"])
    arms = csv(a.arms) if a.arms else ["blocks", "builder"]
    rounds = [int(r) for r in csv(a.rounds)] if a.rounds else base.get("rounds", [0, 3, 8])
    seeds = a.seeds if a.seeds is not None else base.get("seeds", 3)
    kw = dict(turns_mode=a.turns_mode, max_tokens=a.max_tokens, max_output_tokens=a.max_output_tokens, temperature=a.temperature, reasoning=a.reasoning,
              palette_hint=not a.no_palette_hint, blocks_examples=not a.no_blocks_examples, keep_reasoning=a.keep_reasoning)
    if a.max_turns:
        kw["max_turns"] = a.max_turns
    cfgs = plan_grid(tasks, models, arms, rounds, seeds, **kw)
    return cfgs[:a.limit] if a.limit else cfgs


def print_plan(a, cfgs, out=print):
    exp_dir = Path(a.exp_root) / a.exp_id
    todo = pending(cfgs, exp_dir)
    tasks, models = sorted({c.task for c in cfgs}), sorted({c.model for c in cfgs})
    out(f"experiment {a.exp_id}: {len(cfgs)} runs planned ({len(cfgs) - len(todo)} already complete, {len(todo)} to run)")
    out(f"  tasks ({len(tasks)}): {', '.join(tasks)}")
    out(f"  models: {', '.join(models)} | arms: {', '.join(sorted({c.arm for c in cfgs}))} | "
        f"R: {sorted({c.rounds for c in cfgs})} | seeds: {sorted({c.seed for c in cfgs})}")
    est = estimate_grid(todo, reasoning_tokens=a.reasoning_tokens_est)
    out(f"  conservative cost estimate for the {len(todo)} pending runs: ${est['total']:.3f} "
        f"(~{est['prompt_tokens'] / 1e6:.2f}M prompt + {est['output_tokens'] / 1e6:.2f}M output tokens; "
        f"no caching discount, {a.reasoning_tokens_est} reasoning tokens/turn, x1.5 safety)")
    out("  per-run estimate by condition (model, arm, R): usd/run x n")
    for (m, arm, r), (usd, n) in sorted(est["by_condition"].items()):
        out(f"    {m:22s} {arm:8s} R={r}: ${usd:.4f} x {n}")
    cap = a.budget_usd
    verdict = "fits within" if est["total"] <= cap else "EXCEEDS"
    out(f"  budget cap ${cap:.2f}: estimate {verdict} the cap" +
        ("" if est["total"] <= cap else f" -> the run will stop when the budget is hit ({cap / max(est['total'], 1e-9):.0%} of the grid expected; "
                                         "grid order is seed > task > model > arm > R so a partial run stays paired)"))
    return est


def main(argv=None):
    a = parse_args(argv)
    cfgs = build_plan(a)
    if not cfgs:
        print("empty grid")
        return 1
    if a.backend == "local":
        return run_local(a, cfgs, argv)
    print_plan(a, cfgs)
    if a.dry_run:
        return 0
    if not a.mock:
        try:
            load_api_key()
        except RuntimeError as e:
            print(f"error: {e}")
            return 2
    info = {"argv": argv if argv is not None else sys.argv[1:], "pilot": a.pilot}
    s = run_grid(a.exp_id, cfgs, a.budget_usd, exp_root=a.exp_root, ledger=a.ledger, ledger_cap_usd=a.ledger_cap_usd,
                 workers=a.workers, mock=a.mock, require_params=not a.no_require_params, info=info)
    print(f"done: {s['finished']} finished, {s['skipped_done']} already done, {s['incomplete']} incomplete, "
          f"{s['remaining']} still pending, spent ${s['spent_usd']:.3f}/${a.budget_usd:.2f}"
          + (f", stopped by {s['stopped_by']}" if s["stopped_by"] else ""))
    return 0 if not s["stopped_by"] or s["stopped_by"] == "budget" else 3


def run_local(a, cfgs, argv):
    """--backend local: one process, one model load, episodes in order (sequential), $0 ledger inside the experiment dir."""
    import time
    models = sorted({c.model for c in cfgs})
    local = a.local_model or (models[0] if len(models) == 1 else None)
    if local is None:
        print("error: --backend local runs one model per invocation (pass one --models label or --local-model)")
        return 2
    exp_dir = Path(a.exp_root) / a.exp_id
    todo = pending(cfgs, exp_dir)
    print(f"experiment {a.exp_id}: {len(cfgs)} runs planned, {len(todo)} pending; local model {local}"
          + (f" + adapter {a.adapter}" if a.adapter else "") + " ($0, sequential)")
    if a.dry_run or not todo:
        return 0
    from harness.llm_local import local_factory
    t0 = time.time()
    deadline = t0 + a.time_budget_s if a.time_budget_s else None
    factory = local_factory(local, exp_dir=exp_dir, adapter=a.adapter, enable_thinking=a.thinking, max_context=a.max_context)
    print(f"  loaded {factory.load_info}")
    if deadline is not None:
        deadline += time.time() - t0          # the budget counts from after the model load
        inner = factory

        def factory(cfg, budget):            # noqa: F811 - do not start an episode in the last 60 s
            if time.time() > deadline - 60:
                (exp_dir / "STOP").write_text("local time budget")
            llm = inner(cfg, budget)
            llm.deadline = deadline
            return llm
    info = {"argv": argv if argv is not None else sys.argv[1:], "backend": "local", "local_model": local, "adapter": a.adapter,
            "thinking": a.thinking}
    s = run_grid(a.exp_id, cfgs, a.budget_usd, exp_root=a.exp_root, ledger=a.ledger or (exp_dir / "ledger.jsonl"),
                 ledger_cap_usd=None, workers=1, info=info, inprocess_factory=factory)
    print(f"done: {s['finished']} finished, {s['skipped_done']} already done, {s['incomplete']} incomplete, "
          f"{s['remaining']} still pending" + (f", stopped by {s['stopped_by']}" if s["stopped_by"] else "")
          + f" ({time.time() - t0:.0f} s)")
    return 0 if s["stopped_by"] in (None, "stop_file") else 3


if __name__ == "__main__":
    sys.exit(main())
