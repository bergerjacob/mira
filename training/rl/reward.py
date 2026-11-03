"""Environment reward adapter for RL (GRPO-style) training.

Single-turn variant
    prompt     = builder system prompt (evaluation/prompts/builder_system.md + single-turn protocol + physics facts) and the
                 public task text (Contract.describe(public=True) + budget + palette)
    completion = text with ONE fenced python block defining build(b)
    reward     = the harness score of that program: it goes through exactly the harness builder path (sandboxed compile, 20 s
                 timeout, canonical states, task rules, fixed-block handling) and Env.submit() -> env.score R0-R4, by replaying
                 `build(source); submit()` through harness/agent.run_episode with a scripted player. So "reward in training" ==
                 "reward the harness would give if the model built this and submitted".
                 Scoring runs in a pool of worker processes with a hard per-item timeout (a hung simulation scores 0).
Multi-turn variant
    `rollout_episode(task, llm, cfg)`: a full harness episode with the local backend (trace=True keeps (prompt_ids,
    completion_ids) of every turn); trajectory reward = the final env reward (shaped like the single-turn one), per-turn signals
    (compile ok / error kinds, run_spec R1-R3 after each round) are returned for logging or turn-level credit.

Shaping (`shape`): staged and monotone. No program, a compile error or an empty build -> 0 (the env would score the EMPTY build,
which earns R0+R1 = 0.2 and would reward not trying); a program that compiles but breaks the task rules (build box, fixed cells,
unknown states) -> `rejected` (0.02); a loaded build -> 0.05 R0 + 0.05 R1 + 0.8 R3[R1=1] + 0.1 R4[R3=1] (>= 0.05). The 0.02 stage
exists because a weak base model (Qwen3-1.7B single-turn on T0: 0 of 40 samples loaded) otherwise gives zero reward variance. R2 (connectivity) is down-weighted to 0 by default (round-2 audit: a connection
with no correct behaviour still earns it). Raw env reward and all components are always returned for logging.
"""
import json
import multiprocessing
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)
DEFAULT_WEIGHTS = {"r0": 0.05, "r1": 0.05, "r2": 0.0, "r3": 0.8, "r4": 0.1, "rejected": 0.02}

SINGLE_TURN_APPENDIX = """

---
# Protocol for this task
Reply with ONE fenced python block defining build(b) (as described above); nothing else is executed and there is no second turn.
- Coordinates in your program are the absolute task coordinates listed in the task. Only the build box may be written.
- Fixed blocks (floor, levers, lamps, pistons given in the task) already exist. Do not place them again (an identical block is
  ignored, a different block on a fixed cell makes the program invalid).
- The program is compiled, loaded and scored against the public AND hidden vectors of the task.
"""


# --------------------------------------------------------------------------- prompts
def single_turn_messages(task) -> List[dict]:
    from harness import prompts
    system = prompts.BUILDER_SYSTEM_PATH.read_text() + SINGLE_TURN_APPENDIX + "\n" + prompts.PHYSICS
    c = task.contract
    user = c.describe(public=True)
    user += f"\n\nBudget: at most {c.budgets.max_blocks} blocks of your own.\n{prompts.PALETTE}"
    if getattr(c, "hints", None):
        user += "\n" + "\n".join(c.hints) if isinstance(c.hints, list) else f"\n{c.hints}"
    user += "\n\nReply with one fenced python block."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_program(text: str) -> Optional[str]:
    """Last fenced python block that defines build(b) (models sometimes show a draft first). Without any fence, a reply that
    contains `def build` is taken as the program from its first `from simulation`/`import`/`@part`/`def` line on (small models
    often drop the fence; measured: Qwen3-1.7B single-turn, 32/32 unfenced)."""
    text = text or ""
    blocks = [m.group(1) for m in FENCE_RE.finditer(text)]
    good = [b for b in blocks if "def build" in b]
    if good or blocks:
        return (good or blocks)[-1]
    if "def build" not in text:
        return None
    m = re.search(r"^(from simulation|import |@part|def )", text, re.M)
    body = text[m.start():] if m else text
    return body.split("```")[0]


# --------------------------------------------------------------------------- scoring
@dataclass
class RewardResult:
    reward: float                       # shaped reward used for the policy gradient
    env_reward: float = 0.0             # env.score reward of what the harness scored
    full_pass: bool = False
    components: Dict[str, float] = field(default_factory=dict)   # r0..r4
    status: str = "ok"                  # ok | no_program | compile_error | rejected | timeout | error
    detail: str = ""
    blocks: int = 0
    seconds: float = 0.0

    def to_dict(self):
        return self.__dict__.copy()


def shape(final: dict, loaded: bool, weights=None) -> float:
    """Shaped reward from a harness `final` record (fields r0..r4, full_pass)."""
    if not loaded:
        return 0.0
    w = dict(DEFAULT_WEIGHTS, **(weights or {}))
    r0, r1, r2, r3, r4 = (float(final.get(k, 0) or 0) for k in ("r0", "r1", "r2", "r3", "r4"))
    gate1 = 1.0 if r1 >= 1.0 else 0.0
    gate3 = 1.0 if r3 >= 1.0 and gate1 else 0.0
    return w["r0"] * r0 + w["r1"] * r1 + w["r2"] * r2 + w["r3"] * r3 * gate1 + w["r4"] * r4 * gate3


def _load_task(task):
    from env.task import load_task
    return load_task(task) if isinstance(task, (str, Path)) else task


def score_program(task, source: Optional[str], weights=None, compile_timeout: float = 20.0) -> RewardResult:
    """In-process scoring through the real harness path (build -> submit). Never raises."""
    t0 = time.time()
    if not source or not source.strip():
        return RewardResult(0.0, status="no_program", detail="no fenced python block", seconds=time.time() - t0)
    try:
        from harness.agent import RunConfig, run_episode
        from harness.llm import ScriptedLLM, assistant
        tk = _load_task(task)
        cfg = RunConfig(task=tk.id, model="rl", arm="builder", rounds=0, seed=0, max_turns=3)
        llm = ScriptedLLM([assistant("", [("build", {"source": source})]), assistant("", [("submit", {})])])
        fin = run_episode(cfg, llm, exp_dir=None, task=tk)
        att = (fin.get("build_attempts") or [{}])[0]
        loaded = bool(att.get("ok")) and int(fin.get("blocks") or 0) > 0     # an empty program is not an attempt
        status = "ok" if loaded else ("compile_error" if att.get("stage") == "compile" else
                                      "empty" if att.get("ok") else "rejected")
        comps = {k: float(fin.get(k, 0) or 0) for k in ("r0", "r1", "r2", "r3", "r4")}
        detail = "" if loaded else ",".join(att.get("kinds") or [])
        w = dict(DEFAULT_WEIGHTS, **(weights or {}))
        r = shape(fin, loaded, weights) if loaded else (w["rejected"] if status == "rejected" else 0.0)
        return RewardResult(r, float(fin["reward"]), bool(fin["full_pass"]) and loaded, comps, status,
                            detail, int(fin.get("blocks") or 0), time.time() - t0)
    except Exception as e:  # pragma: no cover - defensive
        return RewardResult(0.0, status="error", detail=f"{type(e).__name__}: {str(e)[:200]}", seconds=time.time() - t0)


def _worker(args):
    task, source, weights = args
    return score_program(task, source, weights).to_dict()


def _init_worker():
    import os
    import sys
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from training.local.paths import setup_paths
    setup_paths()
    os.environ["CUDA_VISIBLE_DEVICES"] = ""


class RewardPool:
    """Parallel scoring with a hard per-item timeout. A timed-out item scores 0 (status timeout) and the pool is rebuilt so a
    stuck simulation cannot leak. Use as a context manager; `workers=0` scores in-process (tests)."""

    def __init__(self, workers: int = 4, timeout: float = 60.0, weights=None):
        self.workers, self.timeout, self.weights = workers, timeout, weights
        self.pool = None

    def _ensure(self):
        if self.pool is None and self.workers > 0:
            self.pool = multiprocessing.get_context("spawn").Pool(self.workers, initializer=_init_worker)

    def score(self, items) -> List[RewardResult]:
        """items: [(task_id_or_path, program_text_or_None)]"""
        if self.workers <= 0:
            return [score_program(t, s, self.weights) for t, s in items]
        self._ensure()
        futs = [self.pool.apply_async(_worker, ((str(t) if isinstance(t, Path) else t, s, self.weights),)) for t, s in items]
        out, broken = [], False
        deadline = time.time() + self.timeout
        for f in futs:
            try:
                out.append(RewardResult(**f.get(timeout=max(0.1, deadline - time.time()))))
            except multiprocessing.TimeoutError:
                out.append(RewardResult(0.0, status="timeout", detail=f"scoring exceeded {self.timeout:g}s"))
                broken = True
        if broken:
            self.close()
        return out

    def close(self):
        if self.pool is not None:
            self.pool.terminate()
            self.pool.join()
            self.pool = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def score_completions(task, completions: List[str], pool: Optional[RewardPool] = None, weights=None) -> List[RewardResult]:
    items = [(task, extract_program(c)) for c in completions]
    if pool is None:
        return [score_program(t, s, weights) for t, s in items]
    return pool.score(items)


# --------------------------------------------------------------------------- multi-turn
@dataclass
class Trajectory:
    reward: float                      # shaped final reward (trajectory-level)
    final: dict                        # the harness final record
    turns: List[dict]                  # per-turn: prompt_ids, completion_ids, tool names, build ok, spec after the turn
    signals: dict                      # build attempts, spec history, ended_by

    def token_count(self):
        return sum(len(t["completion_ids"]) for t in self.turns)


def rollout_episode(task, llm, cfg=None, weights=None) -> Trajectory:
    """One multi-turn harness episode with a trace-enabled LocalLLM (`llm.trace = True`). The policy-gradient unit is each turn's
    completion tokens given its prompt tokens, all sharing the trajectory advantage."""
    from harness.agent import RunConfig, run_episode
    tk = _load_task(task)
    cfg = cfg or RunConfig(task=tk.id, model=getattr(llm, "model", "local"), arm="builder", rounds=3, seed=0, max_turns=12,
                           max_tokens=1024)
    llm.trace = True
    n0 = len(llm.traces)
    fin = run_episode(cfg, llm, exp_dir=None, task=tk)
    traces = llm.traces[n0:]
    loaded = any(a.get("ok") for a in fin.get("build_attempts") or [])
    turns = []
    for i, tr in enumerate(traces):
        turns.append({"turn": i + 1, "prompt_ids": tr["prompt_ids"], "completion_ids": tr["completion_ids"], "finish": tr["finish"],
                      "build_ok": [a["ok"] for a in fin.get("build_attempts", []) if a.get("turn") == i + 1]})
    signals = {"ended_by": fin["ended_by"], "build_attempts": fin.get("build_attempts"), "spec_history": fin.get("spec_history"),
               "turns": fin["turns"], "rounds_used": fin["rounds_used"]}
    return Trajectory(shape(fin, loaded, weights), fin, turns, signals)


def dumps(results) -> str:
    return json.dumps([r.to_dict() if hasattr(r, "to_dict") else r for r in results])
