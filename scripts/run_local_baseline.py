#!/usr/bin/env python3
"""Free open-model baseline: a cached HF model (default Qwen3-4B-Instruct-2507) through the REAL harness loop on the GPU.

    # one resumable chunk (the dev box kills processes after ~9 min; rerun the same command until "0 pending")
    /usr/bin/python3 scripts/run_local_baseline.py --model qwen3-4b --time-budget-s 450
    /usr/bin/python3 scripts/run_local_baseline.py --model qwen3-1.7b --time-budget-s 450
    # report: harness/analysis.py tables + local metrics (tok/s, generation wall time, context overflows, parse errors)
    /usr/bin/python3 scripts/run_local_baseline.py --model qwen3-4b --report docs/experiments/local_qwen3-4b.md

Defaults: tasks t0,t1, builder arm, R in {0,3}, 1 seed, temperature 0.7, 4096 tokens per call (a longer runaway generation
would not fit one chunk), harness defaults otherwise (30 turns, 40k completion tokens per episode). Experiment dir:
data/experiments/local_<model>/ (transcripts in the harness format, `local_cache.jsonl` = replay cache).
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from training.local.paths import setup_paths  # noqa: E402

setup_paths()


def local_metrics(exp_dir: Path) -> str:
    """Markdown section with the numbers specific to local generation."""
    from harness.agent import read_transcript
    rows = []
    for p in sorted((exp_dir / "runs").glob("*.jsonl")):
        tr = read_transcript(p)
        fin = tr[-1] if tr and tr[-1].get("type") == "final" else None
        if not fin or not fin.get("complete"):
            continue
        turns = [r for r in tr if r.get("type") == "turn"]
        gen_s = sum(t.get("latency", 0) for t in turns)
        comp = sum(t["usage"].get("completion_tokens", 0) for t in turns)
        prompt_max = max((t["usage"].get("prompt_tokens", 0) for t in turns), default=0)
        overflow = sum(1 for t in turns if t.get("finish_reason") == "context_overflow")
        length = sum(1 for t in turns if t.get("finish_reason") == "length")
        bad_json = sum(1 for t in turns for r in t.get("results", []) if "not valid JSON" in str(r.get("result", "")))
        calls = sum(len(t["assistant"].get("tool_calls") or []) for t in turns)
        idle = sum(1 for t in turns if not t["assistant"].get("tool_calls"))
        rows.append({"key": fin["key"], "R": fin["R"], "pass": fin["full_pass"], "reward": fin["reward"], "turns": len(turns),
                     "gen_s": gen_s, "comp": comp, "prompt_max": prompt_max, "overflow": overflow, "length": length,
                     "bad_json": bad_json, "calls": calls, "idle": idle, "ended": fin["ended_by"],
                     "env_s": max(0.0, fin.get("duration_s", 0) - gen_s)})
    if not rows:
        return "\n## Local generation metrics\n\n(no complete runs)\n"
    out = ["", "## Local generation metrics", "",
           "| R | runs | pass | mean reward | turns/ep | completion tok/ep | max prompt tok | gen wall s/ep | tok/s | "
           "ctx overflow eps | tool calls/ep | replies w/o call | bad-JSON calls | ended_by |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for R in sorted({r["R"] for r in rows}) + ["all"]:
        g = [r for r in rows if R == "all" or r["R"] == R]
        n = len(g)
        tok = sum(r["comp"] for r in g)
        gs = sum(r["gen_s"] for r in g)
        ended = {}
        for r in g:
            ended[r["ended"]] = ended.get(r["ended"], 0) + 1
        out.append(f"| {R} | {n} | {sum(r['pass'] for r in g)}/{n} | {sum(r['reward'] for r in g) / n:.3f} | "
                   f"{sum(r['turns'] for r in g) / n:.1f} | {tok / n:.0f} | {max(r['prompt_max'] for r in g)} | {gs / n:.0f} | "
                   f"{tok / max(gs, 1e-9):.1f} | {sum(1 for r in g if r['overflow'])} | {sum(r['calls'] for r in g) / n:.1f} | "
                   f"{sum(r['idle'] for r in g) / n:.1f} | {sum(r['bad_json'] for r in g)} | "
                   + ", ".join(f"{k} {v}" for k, v in sorted(ended.items())) + " |")
    out += ["", "gen wall s/ep = sum of per-turn generation latencies (replayed turns keep their original latency); "
            "environment/tool time is not included. ctx overflow eps = episodes in which the prompt outgrew the model's "
            "max context on this GPU (the harness then nudges and ends the episode as no_tool_calls)."]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3-4b")
    ap.add_argument("--exp-id", help="default local_<model>")
    ap.add_argument("--tasks", default="t0,t1")
    ap.add_argument("--arms", default="builder")
    ap.add_argument("--rounds", default="0,3")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--adapter")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--max-context", type=int)
    ap.add_argument("--time-budget-s", type=float, default=450)
    ap.add_argument("--exp-root", default=None)
    ap.add_argument("--report", help="write the markdown report here instead of running")
    a = ap.parse_args(argv)
    from harness.llm import EXPERIMENTS_DIR
    exp_id = a.exp_id or f"local_{a.model.replace('/', '_')}"
    root = Path(a.exp_root) if a.exp_root else EXPERIMENTS_DIR
    if a.report:
        from harness.analysis import build_report
        exp_dir = root / exp_id
        text = build_report(exp_dir, f"Local baseline {exp_id}", exp_dir / "ledger.jsonl") + local_metrics(exp_dir)
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(text)
        print(f"wrote {a.report}")
        return 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_experiment", Path(__file__).with_name("run_experiment.py"))
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    args = ["--exp-id", exp_id, "--budget-usd", "1", "--backend", "local", "--models", a.model + ("+" + Path(a.adapter).name if a.adapter else ""),
            "--local-model", a.model, "--tasks", a.tasks, "--arms", a.arms, "--rounds", a.rounds, "--seeds", str(a.seeds),
            "--temperature", str(a.temperature), "--max-tokens", str(a.max_tokens), "--time-budget-s", str(a.time_budget_s)]
    if a.exp_root:
        args += ["--exp-root", a.exp_root]
    if a.adapter:
        args += ["--adapter", a.adapter]
    if a.thinking:
        args.append("--thinking")
    if a.max_context:
        args += ["--max-context", str(a.max_context)]
    return rx.main(args)


if __name__ == "__main__":
    sys.exit(main())
