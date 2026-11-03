#!/usr/bin/env python3
"""Convert harness transcripts and scripted reference episodes into SFT episodes ({messages, tools, meta}).

    # rejection sampling: keep only full-pass runs of an experiment (any backend: OpenRouter, local, scripted)
    python training/sft/build_fixture.py transcripts data/experiments/local_qwen3-4b data/experiments/main1 \
        --out data/sft/transcripts.jsonl [--allow-provider-models]
    # scripted reference episodes (view -> build(reference program) -> [run_spec] -> submit) for env tasks
    python training/sft/build_fixture.py reference --tasks t0,t1 --rounds 0,1 --out data/sft/reference.jsonl

PROVENANCE. Every episode carries meta.source and meta.model. Transcripts produced by commercial API models (OpenRouter ids such
as deepseek/*, google/*, qwen/* plus-models, anthropic/*, openai/*) may be restricted by the provider's terms for training other
models. They are SKIPPED unless --allow-provider-models is given; that is a decision for the project owner (docs/TRAINING.md).
Local open-weight runs (backend local), scripted runs (model mock) and the synthetic engine are ours.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from training.local.paths import setup_paths  # noqa: E402

setup_paths()

OWN_MODEL_PREFIXES = ("mock", "local", "qwen3-4b", "qwen3-1.7b", "llama3.2-1b", "gemma3-1b", "rl")


def is_own(model: str, backend: str = "") -> bool:
    return backend == "local" or str(model).split("+")[0] in OWN_MODEL_PREFIXES


def transcript_to_episode(rows: list) -> dict:
    """Harness transcript rows (header, turn..., final) -> SFT episode, exactly the messages the model saw."""
    from harness.agent import build_tools
    head, fin = rows[0], rows[-1]
    turns = [r for r in rows if r.get("type") == "turn"]
    msgs = [{"role": "system", "content": head["system"]}, {"role": "user", "content": head["task_prompt"]}]
    cfg = head.get("config", {})
    max_turns, R = cfg.get("max_turns"), head["R"]
    used = 0
    for t in turns:
        a = dict(t["assistant"])
        a.pop("reasoning_details", None)
        msgs.append(a)
        res = t.get("results") or []
        if res and "nudge" in res[0]:
            msgs.append({"role": "user", "content": res[0]["nudge"]})
            continue
        for i, r in enumerate(res):
            content = r["result"]
            if r["name"] == "run_spec" and "budget exhausted" not in content:
                used += 1
            last = i == len(res) - 1
            if last and not (r["name"] == "submit"):       # the harness appends the turn footer to the last tool message
                content += f"\n[turn {t['n']}/{max_turns}" + (f" | run_spec {t.get('rounds_used', used)}/{R} used" if R > 0 else "") + "]"
            msgs.append({"role": "tool", "tool_call_id": r["id"], "content": content})
    meta = {"task": head["task"], "source": "harness_transcript", "model": head["model"], "model_id": head.get("model_id"),
            "arm": head["arm"], "rounds": R, "seed": head["seed"], "verified": bool(fin.get("full_pass")) and fin.get("complete", False),
            "reward": fin.get("reward"), "turns": fin.get("turns"), "ended_by": fin.get("ended_by"), "kind": "harness"}
    return {"messages": msgs, "tools": build_tools(head["arm"], R), "meta": meta}


def cmd_transcripts(a):
    from harness.agent import read_transcript
    out, kept, skipped = [], 0, {"not_pass": 0, "provider_model": 0, "incomplete": 0}
    for d in a.dirs:
        cfg = {}
        cp = Path(d) / "config.json"
        if cp.exists():
            cfg = json.loads(cp.read_text()).get("info", {})
        for p in sorted((Path(d) / "runs").glob("*.jsonl")):
            rows = read_transcript(p)
            if not rows or rows[-1].get("type") != "final" or not rows[-1].get("complete"):
                skipped["incomplete"] += 1
                continue
            if not rows[-1].get("full_pass") and not a.all:
                skipped["not_pass"] += 1
                continue
            if not is_own(rows[0]["model"], cfg.get("backend", "")) and not a.allow_provider_models:
                skipped["provider_model"] += 1
                continue
            ep = transcript_to_episode(rows)
            ep["meta"]["experiment"] = Path(d).name
            out.append(ep)
            kept += 1
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("".join(json.dumps(e) + "\n" for e in out))
    print(f"wrote {kept} episodes to {a.out}; skipped {skipped}")
    return 0


def cmd_reference(a):
    from env.task import load_task
    from harness.agent import RunConfig, run_episode
    from harness.experiment import resolve_tasks
    from harness.reference import reference_llm
    import tempfile
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for tid in resolve_tasks(a.tasks):
            t = load_task(tid)
            for R in (int(r) for r in a.rounds.split(",")):
                fin = run_episode(RunConfig(tid, "mock", "builder", R), reference_llm(t, "builder", R), exp_dir=tmp)
                from harness.agent import read_transcript, safe_name
                rows = read_transcript(Path(tmp) / "runs" / f"{safe_name(RunConfig(tid, 'mock', 'builder', R).key)}.jsonl")
                ep = transcript_to_episode(rows)
                ep["meta"].update({"source": "scripted_reference", "kind": "reference", "verified": bool(fin["full_pass"])})
                out.append(ep)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("".join(json.dumps(e) + "\n" for e in out))
    print(f"wrote {len(out)} reference episodes to {a.out} ({sum(e['meta']['verified'] for e in out)} verified)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcripts")
    t.add_argument("dirs", nargs="+")
    t.add_argument("--out", required=True)
    t.add_argument("--all", action="store_true", help="keep failed runs too (meta.verified=false)")
    t.add_argument("--allow-provider-models", action="store_true",
                   help="include transcripts of commercial API models (check the provider's terms first)")
    r = sub.add_parser("reference")
    r.add_argument("--tasks", default="t0,t1")
    r.add_argument("--rounds", default="0,1")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"transcripts": cmd_transcripts, "reference": cmd_reference}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
