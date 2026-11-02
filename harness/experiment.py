"""Experiment grid: plan, resume, run in parallel under one shared budget.

Layout of an experiment (``data/experiments/<exp_id>/``):
    config.json       arguments + the planned grid (rewritten on every start; `history` keeps earlier starts)
    runs/<key>.jsonl  one transcript per run; a run is DONE iff its last line is a ``final`` with ``complete: true``
    STOP              create this file to make running episodes end after their current turn (graceful stop)
The spend ledger is shared by all experiments: ``data/experiments/ledger.jsonl`` (see harness/llm.py).
"""
import json
import multiprocessing
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from env.task import list_task_ids
from harness.agent import RunConfig, final_of, run_episode, safe_name
from harness.llm import (DEFAULT_LEDGER, EXPERIMENTS_DIR, Budget, OpenRouterLLM, load_api_key)

PILOT = {"models": ["deepseek-v4-flash", "qwen3.7-plus"], "rounds": [0, 3], "seeds": 1,
         "tasks": ["t0_lamp", "t0_not", "t0_and", "t1_nand", "t1_xor", "t1_pulse_extender"]}
STOP_STREAK = 6          # consecutive api_error episodes before the runner gives up (bad key/model name)


def resolve_tasks(spec) -> List[str]:
    """'all' | 't0' | 't1' | 't2' | comma list of ids (or tier prefixes)."""
    ids = list_task_ids()
    if spec in (None, "all"):
        return ids
    out = []
    for tok in ([spec] if isinstance(spec, str) else spec):
        for part in str(tok).split(","):
            part = part.strip()
            if not part:
                continue
            if part in ids:
                out.append(part)
            elif part.lower() in ("t0", "t1", "t2", "t3"):
                out += [i for i in ids if i.startswith(part.lower() + "_")]
            else:
                raise ValueError(f"unknown task {part!r}")
    return list(dict.fromkeys(out))


def plan_grid(tasks, models, arms, rounds, seeds, **cfg_kw) -> List[RunConfig]:
    """All RunConfigs, ordered seed -> task -> model -> arm -> R so that stopping early (budget) leaves complete
    paired blocks (same task/seed under every condition) rather than whole conditions missing."""
    seeds = list(range(seeds)) if isinstance(seeds, int) else list(seeds)
    return [RunConfig(task=t, model=m, arm=a, rounds=r, seed=s, **cfg_kw)
            for s in seeds for t in tasks for m in models for a in arms for r in rounds]


def runs_dir(exp_dir) -> Path:
    return Path(exp_dir) / "runs"


def completed_keys(exp_dir) -> set:
    done = set()
    d = runs_dir(exp_dir)
    if d.exists():
        for p in d.glob("*.jsonl"):
            f = final_of(p)
            if f and f.get("complete"):
                done.add(f["key"])
    return done


def pending(cfgs: Iterable[RunConfig], exp_dir) -> List[RunConfig]:
    done = completed_keys(exp_dir)
    return [c for c in cfgs if c.key not in done]


def make_llm(cfg: RunConfig, budget: Optional[Budget], mock: bool, mock_cost: float = 0.0, require_params: bool = True):
    if mock:
        from env.task import load_task
        from harness.reference import reference_llm
        return reference_llm(load_task(cfg.task), cfg.arm, cfg.rounds, model=cfg.model, budget=budget, cost_per_call=mock_cost)
    return OpenRouterLLM(cfg.model, budget, tag=cfg.key, require_params=require_params, keep_reasoning=cfg.keep_reasoning)


def run_job(job: dict) -> dict:
    """Worker entry (top level so it can be pickled): build budget + LLM, run one episode, return the final record."""
    cfg = RunConfig(**job["cfg"])
    budget = Budget(job["cap_usd"], job["exp_id"], job["ledger"], job.get("ledger_cap_usd"))
    llm = make_llm(cfg, budget, job.get("mock", False), job.get("mock_cost", 0.0), job.get("require_params", True))
    try:
        final = run_episode(cfg, llm, exp_dir=job["exp_dir"], stop_file=Path(job["exp_dir"]) / "STOP")
    except Exception as e:  # defensive: an unexpected harness bug must not kill the pool
        final = {"key": cfg.key, "complete": False, "ended_by": "crash", "error": f"{type(e).__name__}: {str(e)[:300]}",
                 "turns": 0, "cost_usd": 0.0}
    final.pop("final_text", None)
    return final


def write_config(exp_dir: Path, info: dict):
    p = exp_dir / "config.json"
    hist = []
    if p.exists():
        try:
            old = json.loads(p.read_text())
            hist = old.get("history", []) + [{k: v for k, v in old.items() if k != "history"}]
        except ValueError:
            pass
    p.write_text(json.dumps({**info, "history": hist}, indent=1, default=str))


def run_grid(exp_id: str, cfgs: List[RunConfig], cap_usd: float, *, exp_root=EXPERIMENTS_DIR, ledger=None,
             ledger_cap_usd: Optional[float] = None, workers: int = 6, mock: bool = False, mock_cost: float = 0.0,
             require_params: bool = True, progress: Callable[[str], None] = print, info: Optional[dict] = None,
             inprocess_factory: Optional[Callable] = None) -> dict:
    """Run every not-yet-completed config. Returns a summary dict. `inprocess_factory(cfg, budget)` (tests) forces
    sequential in-process execution with a custom LLM."""
    exp_dir = Path(exp_root) / exp_id
    exp_dir.mkdir(parents=True, exist_ok=True)
    ledger = Path(ledger) if ledger else (exp_dir / "ledger.jsonl" if mock else DEFAULT_LEDGER)
    stop_file = exp_dir / "STOP"
    stop_file.unlink(missing_ok=True)
    todo = pending(cfgs, exp_dir)
    write_config(exp_dir, {"exp_id": exp_id, "cap_usd": cap_usd, "ledger": str(ledger), "ledger_cap_usd": ledger_cap_usd,
                           "workers": workers, "mock": mock, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "planned": len(cfgs), "pending": len(todo), "info": info or {},
                           "grid": [c.key for c in cfgs]})
    budget = Budget(cap_usd, exp_id, ledger, ledger_cap_usd)
    summary = {"planned": len(cfgs), "skipped_done": len(cfgs) - len(todo), "finished": 0, "incomplete": 0, "stopped_by": None,
               "results": []}
    state = {"streak": 0}

    def handle(final):
        summary["results"].append(final)
        if final.get("complete"):
            summary["finished"] += 1
            state["streak"] = 0
            progress(f"[{summary['finished'] + summary['skipped_done']}/{len(cfgs)}] {final['key']}: reward={final['reward']:.3f} "
                     f"{'PASS' if final['full_pass'] else 'fail'} turns={final['turns']} rounds={final['rounds_used']} "
                     f"${final['cost_usd']:.4f} | spent ${budget.spent():.3f}/${cap_usd:.2f}")
            return False
        summary["incomplete"] += 1
        eb = final.get("ended_by")
        progress(f"[incomplete] {final.get('key')}: {eb} {final.get('error') or ''}")
        if eb in ("budget", "no_credits"):
            summary["stopped_by"] = eb
            return True
        state["streak"] = state["streak"] + 1 if eb in ("api_error", "crash") else 0
        if state["streak"] >= STOP_STREAK:
            summary["stopped_by"] = "repeated_api_errors"
            return True
        if eb == "stopped":
            summary["stopped_by"] = "stop_file"
            return True
        return False

    def job_for(c):
        return {"cfg": asdict(c), "exp_dir": str(exp_dir), "exp_id": exp_id, "cap_usd": cap_usd, "ledger": str(ledger),
                "ledger_cap_usd": ledger_cap_usd, "mock": mock, "mock_cost": mock_cost, "require_params": require_params}

    if inprocess_factory is not None or workers <= 1:
        for c in todo:
            if summary["stopped_by"] or budget.remaining() <= 0:
                summary["stopped_by"] = summary["stopped_by"] or "budget"
                break
            if inprocess_factory is not None:
                final = run_episode(c, inprocess_factory(c, budget), exp_dir=exp_dir, stop_file=stop_file)
                final.pop("final_text", None)
            else:
                final = run_job(job_for(c))
            if handle(final):
                break
    else:
        ctx = multiprocessing.get_context("spawn")
        queue = list(todo)
        running = set()
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
            try:
                while (queue or running):
                    while queue and len(running) < workers and not summary["stopped_by"]:
                        if budget.remaining() <= 0:
                            summary["stopped_by"] = "budget"
                            break
                        running.add(ex.submit(run_job, job_for(queue.pop(0))))
                    if not running:
                        break
                    done, running = wait(running, return_when=FIRST_COMPLETED)
                    for f in done:
                        try:
                            handle(f.result())
                        except Exception as e:  # worker process died
                            handle({"key": "?", "complete": False, "ended_by": "crash", "error": str(e)[:200]})
                    if summary["stopped_by"]:
                        queue.clear()
            except KeyboardInterrupt:
                progress("interrupted: asking running episodes to stop after their current turn (Ctrl-C again to abort)")
                stop_file.write_text("stop")
                summary["stopped_by"] = "interrupt"
                for f in running:
                    f.cancel()
    summary["spent_usd"] = round(budget.spent(), 4)
    summary["remaining"] = len(pending(cfgs, exp_dir))
    return summary
