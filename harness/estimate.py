"""Conservative cost model for a planned grid (used by ``run_experiment.py --dry-run``).

Token sizes are measured from a mock episode (the scripted reference player, R=1) through the real harness, per
(task, arm): the system+task prompt+tool specs, a full build message, and the tool results. The episode shape is then
modelled turn by turn, with each turn re-sending the whole history:

    view -> build -> [run_spec -> fix] x R -> submit          (+ `slack_turns` extra small turns)

Pessimistic choices: 3 chars/token, a model's build message is `build_factor` x the reference's size, a fix message is
`fix_fraction` of a build, `reasoning_tokens` hidden thinking tokens are billed per turn at the output price, no prompt
caching discount, and the total is multiplied by `safety`. Real spend should come in below the estimate; it is not a bound
(the hard bound is the Budget).
"""
import json
from dataclasses import dataclass

from env.task import load_task
from harness.agent import RunConfig, build_tools, run_episode
from harness.llm import CHARS_PER_TOKEN, price_for, resolve_model
from harness.reference import reference_llm
from harness import prompts


@dataclass
class Sizes:
    prompt_tokens: float        # system + task prompt + tool specs
    build_tokens: float         # one full build message (set_blocks / build call) of the reference
    view_tokens: float
    spec_tokens: float
    submit_tokens: float


_CACHE = {}


def measure(task_id: str, arm: str) -> Sizes:
    key = (task_id, arm)
    if key in _CACHE:
        return _CACHE[key]
    task = load_task(task_id)
    cfg = RunConfig(task_id, "mock", arm, 1)
    llm = reference_llm(task, arm, 1)
    run_episode(cfg, llm)
    msgs = llm.calls[-1]["messages"]       # history as of the last (submit) call
    tools = build_tools(arm, 1)
    t = lambda s: len(s if isinstance(s, str) else json.dumps(s)) / CHARS_PER_TOKEN
    prompt = t(prompts.system_prompt(arm)) + t(msgs[1]["content"]) + t(tools)
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    asst = [m for m in msgs if m["role"] == "assistant"]
    sizes = Sizes(prompt, t(asst[1]), t(tool_msgs[0]["content"]), t(tool_msgs[2]["content"]) if len(tool_msgs) > 2 else 300,
                  400)
    _CACHE[key] = sizes
    return sizes


def estimate_run(sizes: Sizes, model: str, rounds: int, *, reasoning_tokens: int = 800, build_factor: float = 1.6,
                 fix_fraction: float = 0.6, slack_turns: int = 2, safety: float = 1.5) -> dict:
    pin, pout = price_for(resolve_model(model))
    build = sizes.build_tokens * build_factor + 250
    turns = [(120, sizes.view_tokens), (build, 120)]
    for _ in range(rounds):
        turns += [(80, sizes.spec_tokens * 1.5), (build * fix_fraction, 120)]
    turns += [(150, 100)] * slack_turns
    turns += [(50, sizes.submit_tokens)]
    ctx, p_tok, o_tok, usd = sizes.prompt_tokens, 0.0, 0.0, 0.0
    for out, result in turns:
        out += reasoning_tokens
        p_tok += ctx
        o_tok += out
        usd += ctx * pin / 1e6 + out * pout / 1e6
        ctx += (out - reasoning_tokens) + result    # reasoning is not re-sent
    return {"turns": len(turns), "prompt_tokens": int(p_tok), "output_tokens": int(o_tok), "usd": usd * safety}


def estimate_grid(cfgs, **kw) -> dict:
    """-> {"total": usd, "by_condition": {(model, arm, R): [usd per run mean, n]}, "runs": n, "tokens": {...}}"""
    by, total, p, o = {}, 0.0, 0, 0
    for c in cfgs:
        e = estimate_run(measure(c.task, c.arm), c.model, c.rounds, **kw)
        total += e["usd"]
        p += e["prompt_tokens"]
        o += e["output_tokens"]
        s = by.setdefault((c.model, c.arm, c.rounds), [0.0, 0])
        s[0] += e["usd"]
        s[1] += 1
    return {"total": total, "runs": len(cfgs), "prompt_tokens": p, "output_tokens": o,
            "by_condition": {k: (v[0] / v[1], v[1]) for k, v in by.items()}}
