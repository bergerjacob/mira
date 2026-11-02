"""The agent loop: one episode = one (task, model, arm, R, seed) run, logged as a JSONL transcript.

    cfg = RunConfig(task="t1_xor", model="deepseek-v4-flash", arm="blocks", rounds=3, seed=0)
    final = run_episode(cfg, llm, exp_dir="data/experiments/pilot")      # llm: OpenRouterLLM or ScriptedLLM

Arms
  blocks   tools: set_blocks, fill, remove, view, [run_spec], submit        (env/tools.py)
  builder  tools: build(source), view, [run_spec], submit                    (simulation/builder.py sandbox compile)
Feedback rounds R: run_spec is offered only when R > 0 and is refused after R calls (the refused call is not counted).
`view` is always static (the env's `live=True` simulator view is removed so all simulator feedback goes through run_spec).
Static validation (set_blocks rejections, builder compile errors) is free in every condition.
submit() scores with env.score on the hidden spec and ends the episode; an episode that ends any other way
(max turns, output-token cap, no tool call) is scored as it stands via the same call, so every episode has a score.
"""
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from env.contract import in_box
from env.runner import prepare
from env.task import load_task
from env.tools import Env, tool_specs
from env import blocks as EB
from harness import prompts
from harness.llm import BudgetExceeded, InsufficientCredits, LLMError, normalise_message

ARMS = ("blocks", "builder")
TURNS_MODES = ("fixed", "scaled")
FIXED_TURNS = 30                # turn cap independent of R (round-2 audit M2: 10 + 3R mixed feedback with turn budget)
MAX_COMPILE_BLOCKS = 50_000     # builder arm: stop runaway programs early (the contract's own max_blocks is checked after)
NUDGE = ("No tool call received. Use the tools: call build/set_blocks to place blocks, view to inspect, run_spec to simulate "
         "(if offered) and submit() when you are done.")


@dataclass
class RunConfig:
    task: str
    model: str
    arm: str
    rounds: int
    seed: int = 0
    max_turns: Optional[int] = None          # explicit cap; None = derived from turns_mode
    turns_mode: str = "fixed"                # "fixed": FIXED_TURNS for every R (R then changes feedback only) | "scaled": 10 + 3*R (round-1 grid)
    max_tokens: int = 16384                  # per call (8192 truncated reasoning models before the first tool call; see docs/EXPERIMENT_01.md)
    max_output_tokens: int = 40_000          # per episode (completion tokens incl. reasoning)
    temperature: float = 0.7
    reasoning: Optional[str] = None          # OpenRouter reasoning.effort, None = provider default
    palette_hint: bool = True
    blocks_examples: bool = True             # blocks arm: worked examples equal to the builder arm's (False = the round-1 prompt)
    keep_reasoning: bool = False             # echo provider reasoning_details in later turns (some thinking models need it)
    max_result_chars: int = 7000
    max_idle: int = 3                        # consecutive replies without tool calls before the episode is ended

    def __post_init__(self):
        if self.arm not in ARMS:
            raise ValueError(f"arm must be one of {ARMS}")
        if self.rounds < 0:
            raise ValueError("rounds must be >= 0")
        if self.turns_mode not in TURNS_MODES:
            raise ValueError(f"turns_mode must be one of {TURNS_MODES}")
        if self.max_turns is None:
            self.max_turns = FIXED_TURNS if self.turns_mode == "fixed" else 10 + 3 * self.rounds

    @property
    def key(self) -> str:
        return run_key(self.task, self.model, self.arm, self.rounds, self.seed)


def run_key(task, model, arm, rounds, seed) -> str:
    return f"{task}|{model}|{arm}|R{rounds}|s{seed}"


def safe_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", key.replace("|", "__"))


# --------------------------------------------------------------------------- tools
def build_tools(arm: str, rounds: int):
    """OpenAI-format tool list for the arm/R. R=0 never contains run_spec."""
    env_specs = {s["name"]: s for s in tool_specs()}
    specs = []
    if arm == "blocks":
        specs += [env_specs["set_blocks"], env_specs["fill"], env_specs["remove"]]
    else:
        specs.append({"name": "build",
                      "description": "Compile a Python program (defining build(b), see the system prompt) in the sandbox and load its "
                                     "blocks as the CURRENT build, replacing the previous one. On any error nothing is loaded and "
                                     "the errors are returned with line numbers.",
                      "parameters": {"type": "object", "required": ["source"],
                                     "properties": {"source": {"type": "string", "description": "complete Python program text"}}}})
    view = json.loads(json.dumps(env_specs["view"]))
    view["description"] = ("Static ASCII slice of the world (fixed blocks + your current build) with a legend and the ports in the slice. "
                           "axis y: layer=y, rows=z, cols=x; axis x: rows=y, cols=z; axis z: rows=y, cols=x. `box`=[x1,y1,z1,x2,y2,z2] "
                           "limits the area. Shows block states, not signal levels.")
    view["parameters"]["properties"].pop("live", None)
    specs.append(view)
    if rounds > 0:
        rs = dict(env_specs["run_spec"])
        rs["description"] = (f"Simulate the current build against the PUBLIC vectors/sequences and invariants and explain failures "
                             f"(first divergence, waveforms, unreached ports). You may call it at most {rounds} time(s) in total.")
        specs.append(rs)
    sub = dict(env_specs["submit"])
    sub["description"] = ("Finish: score the current build against ALL vectors (hidden ones included) and end the episode. "
                          "Call it when you are done; the episode ends immediately.")
    specs.append(sub)
    return [{"type": "function", "function": s} for s in specs]


# --------------------------------------------------------------------------- final-feedback parsing
def parse_feedback(text: str) -> dict:
    """Pull structured failure causes out of Env.submit()'s feedback text (for the failure taxonomy)."""
    out = {"r0_errors": [], "r1_violations": [], "invariants_failed": [], "r2_missing": 0, "silent": False}
    mode = None
    for line in text.splitlines():
        if line.startswith("R0 COMPILE: FAIL"):
            mode = "r0"
            continue
        if line.startswith("R1 STRUCTURE: FAIL"):
            mode = "r1"
            continue
        if line.startswith("R1 STRUCTURE: ok") or line.startswith("R0 COMPILE: ok"):
            mode = None
            continue
        if line.startswith("R2 CONNECTIVITY"):
            mode = "r2"
            continue
        if line.startswith("R3 ") or line.startswith("SCORE") or line.startswith("SILENT") or line.startswith("HIDDEN"):
            mode = None
        if line.startswith("SILENT"):
            out["silent"] = True
        m = re.match(r"\s+invariant (\w+):", line)
        if m:
            out["invariants_failed"].append(m.group(1))
            continue
        if line.startswith("  - ") or line.startswith("  note"):
            body = line[4:] if line.startswith("  - ") else line
            if mode == "r0" and line.startswith("  - "):
                out["r0_errors"].append(body)
            elif mode == "r1" and line.startswith("  - "):
                out["r1_violations"].append(body)
            elif mode == "r2" and line.startswith("  - "):
                out["r2_missing"] += 1
    return out


def _fence_strip(src: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", src, re.S)
    return m.group(1) if m else src


# --------------------------------------------------------------------------- episode
class Episode:
    def __init__(self, cfg: RunConfig, llm, exp_dir=None, task=None, stop_file=None):
        self.cfg, self.llm = cfg, llm
        self.task = task or load_task(cfg.task)
        self.contract = self.task.contract
        self.env = Env(self.task, seed=cfg.seed, max_rounds=cfg.rounds if cfg.rounds > 0 else 0)
        self.tools = build_tools(cfg.arm, cfg.rounds)
        self.tool_names = {t["function"]["name"] for t in self.tools}
        self.system = prompts.system_prompt(cfg.arm, cfg.blocks_examples)
        self.user = prompts.task_prompt(self.task, cfg.arm, cfg.rounds, cfg.max_turns, cfg.palette_hint)
        self.stop_file = Path(stop_file) if stop_file else None
        self.log_path = None
        if exp_dir is not None:
            runs = Path(exp_dir) / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            self.log_path = runs / f"{safe_name(cfg.key)}.jsonl"
        self.build_attempts = []
        self.spec_history = []
        self.refused_spec = 0
        self.turn = 0
        self.t0 = time.time()
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cost": 0.0}
        self._fh = None

    # -- logging
    def _log(self, rec):
        if self._fh is not None:
            self._fh.write(json.dumps(rec, default=str) + "\n")
            self._fh.flush()

    # -- tool execution
    def _clip(self, text: str) -> str:
        n = self.cfg.max_result_chars
        return text if len(text) <= n else text[:n] + f"\n... [truncated {len(text) - n} chars; use a smaller view]"

    def _int(self, v):
        return int(round(float(v)))

    def _exec(self, name: str, args) -> str:
        if name not in self.tool_names:
            return f"error: tool {name!r} is not available; available tools: {sorted(self.tool_names)}"
        if not isinstance(args, dict):
            return "error: arguments must be a JSON object"
        try:
            if name == "build":
                return self._tool_build(args.get("source"))
            if name == "set_blocks":
                blocks = args.get("blocks")
                if isinstance(blocks, str):
                    blocks = json.loads(blocks)
                return self.env.set_blocks(blocks)
            if name in ("fill", "remove"):
                a = {k: self._int(v) for k, v in args.items() if k != "state"}
                if name == "fill":
                    return self.env.fill(**a, state=args.get("state", ""))
                return self.env.remove(**a)
            if name == "view":
                kw = {k: args[k] for k in ("layer", "axis", "box") if args.get(k) is not None}
                if "layer" in kw:
                    kw["layer"] = self._int(kw["layer"])
                if "box" in kw:
                    kw["box"] = [self._int(v) for v in kw["box"]]
                return self.env.view(live=False, **kw)
            if name == "run_spec":
                before = self.env.rounds
                out = self.env.run_spec()
                if self.env.rounds > before:
                    sc = self.env.last_score
                    self.spec_history.append({"turn": self.turn, "r0": sc.r0, "r1": sc.r1, "r2": sc.r2, "r3": sc.r3,
                                              "full_pass": sc.full_pass, "blocks": sc.blocks})
                else:
                    self.refused_spec += 1
                return out
            if name == "submit":
                return self._final_submit()
        except Exception as e:  # a tool must never crash the episode
            return f"error: {name} failed: {type(e).__name__}: {str(e)[:300]}"
        return f"error: unhandled tool {name}"

    def _final_submit(self) -> str:
        out = self.env.submit()
        self.final_text = out
        return out

    def _tool_build(self, source) -> str:
        from simulation import builder as B
        if not isinstance(source, str) or not source.strip():
            return "error: build(source) needs the complete Python program text as a string"
        src = _fence_strip(source)
        res = B.compile(src, timeout=20.0, max_blocks=MAX_COMPILE_BLOCKS)
        att = {"turn": self.turn, "ok": False, "kinds": [e.kind for e in res.errors], "stage": "compile"}
        self.build_attempts.append(att)
        if res.errors:
            return "COMPILE FAILED - nothing was loaded; the current build is unchanged.\n" + res.summary()
        fixed, put, ignored, bad = self.env.fixed, [], 0, []
        for x, y, z, st, nbt in res.blocks:
            pos = (x, y, z)
            try:
                cst = EB.canonical(st)
            except EB.BlockError as e:
                bad.append(f"{[x, y, z]}: {e}")
                continue
            if pos in fixed and fixed[pos] == cst:
                ignored += 1
                continue
            put.append((pos, cst))
        pr = prepare(self.contract, [[p[0], p[1], p[2], s] for p, s in put]) if not bad else None
        errs = bad + (pr.errors if pr else [])
        if errs:
            att["stage"] = "validate"
            att["kinds"] = ["r0_check"]
            return ("COMPILE OK but the build was NOT loaded (task rules), current build unchanged:\n  " + "\n  ".join(errs[:12])
                    + ("\n" + res.summary() if res.warnings else ""))
        self.env.state.apply(put=put, delete=list(self.env.state.cells))
        att["ok"] = True
        att["stage"] = "loaded"
        b = res.bounds()
        msg = f"build loaded: {len(put)} blocks" + (f" ({ignored} identical to fixed blocks ignored)" if ignored else "")
        if b:
            msg += f", bounds {list(b[0])}..{list(b[1])}"
        warn = [f"warning {w}" for w in res.warnings[:8]]
        nbt_n = sum(1 for b_ in res.blocks if b_[4])
        if nbt_n:
            warn.append(f"warning: block NBT on {nbt_n} blocks is ignored by this task environment")
        return msg + ("\n" + "\n".join(warn) if warn else "")

    # -- the loop
    def run(self) -> dict:
        cfg = self.cfg
        if self.log_path is not None:
            self._fh = open(self.log_path, "w")
        self.final_text = None
        self._log({"type": "header", "key": cfg.key, "task": cfg.task, "tier": self.contract.tier, "model": cfg.model,
                   "model_id": getattr(self.llm, "model_id", cfg.model), "arm": cfg.arm, "R": cfg.rounds, "seed": cfg.seed,
                   "config": asdict(cfg), "tools": sorted(self.tool_names), "system": self.system, "task_prompt": self.user,
                   "t_start": self.t0})
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": self.user}]
        ended, idle, err, complete = "max_turns", 0, None, True
        while self.turn < cfg.max_turns and not self.env.done:
            if self.stop_file is not None and self.stop_file.exists():
                ended, complete = "stopped", False
                break
            if self.usage["completion_tokens"] >= cfg.max_output_tokens:
                ended = "token_cap"
                break
            self.turn += 1
            try:
                comp = self.llm.chat(messages, self.tools, max_tokens=cfg.max_tokens, temperature=cfg.temperature,
                                     seed=cfg.seed * 1000 + self.turn, reasoning=cfg.reasoning)
            except BudgetExceeded as e:
                self.turn -= 1
                ended, complete, err = "budget", False, str(e)
                break
            except InsufficientCredits as e:
                self.turn -= 1
                ended, complete, err = "no_credits", False, str(e)
                break
            except LLMError as e:
                self.turn -= 1
                ended, complete, err = "api_error", False, str(e)[:400]
                break
            u = comp.usage
            self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
            self.usage["completion_tokens"] += u.get("completion_tokens", 0)
            self.usage["reasoning_tokens"] += u.get("reasoning_tokens", 0)
            self.usage["cost"] += comp.cost
            msg = normalise_message(comp.message)
            messages.append(msg)
            results = []
            calls = msg.get("tool_calls") or []
            if not calls:
                idle += 1
                note = NUDGE + (" (your reply was cut off by the output limit; be concise)" if comp.finish_reason == "length" else "")
                messages.append({"role": "user", "content": note})
                results.append({"nudge": note})
            else:
                idle = 0
                for c in calls:
                    fn = c["function"]
                    if self.env.done:
                        res = "skipped: the episode already ended with submit()"
                        args = fn.get("arguments")
                    else:
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except ValueError:
                            args = None
                        res = self._exec(fn["name"], args) if args is not None else \
                            "error: tool arguments are not valid JSON (the output may have been truncated)"
                    res = self._clip(res)
                    results.append({"id": c["id"], "name": fn["name"], "args": args if not isinstance(args, str) else args, "result": res})
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": res})
                if not self.env.done:
                    footer = f"\n[turn {self.turn}/{cfg.max_turns}" + (f" | run_spec {self.env.rounds}/{cfg.rounds} used" if cfg.rounds > 0 else "") + "]"
                    messages[-1]["content"] += footer
            self._log({"type": "turn", "n": self.turn, "assistant": msg, "finish_reason": comp.finish_reason, "usage": u,
                       "cost": comp.cost, "cost_source": comp.cost_source, "latency": round(comp.latency, 2),
                       "results": results, "rounds_used": self.env.rounds})
            if self.env.done:
                ended = "submit"
                break
            if idle >= cfg.max_idle:
                ended = "no_tool_calls"
                break
        if self.env.done:
            ended = "submit"
        if self.turn == 0 and not complete:
            # nothing happened (budget/credit/API failure before the first reply): leave no transcript, the run stays pending
            if self._fh is not None:
                self._fh.close()
                self._fh = None
                self.log_path.unlink(missing_ok=True)
            return {"type": "final", "key": cfg.key, "task": cfg.task, "model": cfg.model, "arm": cfg.arm, "R": cfg.rounds,
                    "seed": cfg.seed, "complete": False, "ended_by": ended, "error": err, "turns": 0, "started": False,
                    "cost_usd": 0.0}
        return self._finish(ended, complete, err)

    def _finish(self, ended, complete, err) -> dict:
        cfg = self.cfg
        if not self.env.done:
            self._final_submit()          # unsubmitted end: score the current build as it stands
        sc = self.env.final
        det = parse_feedback(self.final_text or "")
        final = {"type": "final", "key": cfg.key, "task": cfg.task, "tier": self.contract.tier, "model": cfg.model,
                 "arm": cfg.arm, "R": cfg.rounds, "seed": cfg.seed, "complete": complete, "ended_by": ended, "error": err,
                 "submitted": ended == "submit", "turns": self.turn, "rounds_used": self.env.rounds,
                 "refused_run_spec": self.refused_spec, "score": sc.to_dict(), "reward": round(sc.reward, 4),
                 "full_pass": bool(sc.full_pass), "r0": sc.r0, "r1": sc.r1, "r2": sc.r2, "r3": round(sc.r3, 4), "r4": round(sc.r4, 4),
                 "blocks": sc.blocks, "usage": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.usage.items()},
                 "cost_usd": round(self.usage["cost"], 6), "build_attempts": self.build_attempts,
                 "spec_history": self.spec_history, "details": det, "final_text": self.final_text,
                 "duration_s": round(time.time() - self.t0, 2)}
        self._log(final)
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        return final


def run_episode(cfg: RunConfig, llm, exp_dir=None, task=None, stop_file=None) -> dict:
    """Run one episode and return its `final` record (also the last line of the transcript)."""
    return Episode(cfg, llm, exp_dir, task, stop_file).run()


def read_transcript(path) -> list:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def final_of(path) -> Optional[dict]:
    """The final record of a transcript, or None if the run never finished."""
    try:
        rows = read_transcript(path)
    except (OSError, ValueError):
        return None
    return rows[-1] if rows and rows[-1].get("type") == "final" else None
