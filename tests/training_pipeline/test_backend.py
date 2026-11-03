"""harness/llm_local.LocalLLM: interface parity with the harness clients, the unchanged agent loop, sampling, caching, limits."""
import json

import pytest

from conftest import needs_torch
from harness.llm import Budget, Completion, LLMError, ScriptedLLM, assistant

pytestmark = needs_torch


class StubLocal:
    """Factory for a LocalLLM on the tiny model whose generate_ids replays scripted assistant TEXT (tokenised), so the full
    render -> generate -> decode -> parse path runs without a real model."""

    def __new__(cls, tiny, replies, **kw):
        from harness.llm_local import LocalLLM
        model, tok = tiny

        class _L(LocalLLM):
            def generate_ids(self, prompt_ids, max_new, temperature, seed, max_time=None):
                self.calls_made = getattr(self, "calls_made", 0) + 1
                txt = replies(self.calls_made - 1) if callable(replies) else replies[min(self.calls_made - 1, len(replies) - 1)]
                ids = tok(txt + "<|im_end|>", add_special_tokens=False)["input_ids"]
                return ids[:max_new], ("eos" if len(ids) <= max_new else "length")

        return _L("qwen3-1.7b", model_obj=model, tokenizer=tok, **kw)


def qwen_call(name, args, text=""):
    return (text + "\n" if text else "") + "<tool_call>\n" + json.dumps({"name": name, "arguments": args}) + "\n</tool_call>"


def test_completion_parity_with_scripted_llm(tiny):
    tools = [{"type": "function", "function": {"name": "submit", "description": "end", "parameters": {"type": "object", "properties": {}}}}]
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    local = StubLocal(tiny, [qwen_call("submit", {}, "ok")])
    a = local.chat(msgs, tools, max_tokens=64, temperature=0.7, seed=1)
    b = ScriptedLLM([assistant("ok", [("submit", {})])]).chat(msgs, tools, max_tokens=64, seed=1)
    assert isinstance(a, Completion)
    assert set(a.message) == set(b.message) == {"role", "content", "tool_calls"}
    assert set(a.usage) == set(b.usage)
    assert a.message["content"] == "ok" and a.tool_calls[0]["function"]["name"] == "submit"
    assert a.finish_reason == "tool_calls" and a.cost == 0.0 and a.cost_source == "local"
    assert a.usage["prompt_tokens"] > 0 and a.usage["completion_tokens"] > 0
    assert local.n_calls == 1 and local.total_cost == 0.0


def test_budget_path_reserves_zero(tiny, tmp_path):
    budget = Budget(0.01, "loc", tmp_path / "ledger.jsonl")
    local = StubLocal(tiny, ["hello"], budget=budget)
    for _ in range(3):
        local.chat([{"role": "user", "content": "x"}], None, max_tokens=8)
    rows = [json.loads(l) for l in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 6 and all(r["usd"] == 0 for r in rows) and budget.spent() == 0


def test_real_agent_loop_runs_unchanged_and_passes(tiny, task):
    """builder arm, R=1: view -> build(reference program) -> run_spec -> submit, through harness/agent.run_episode."""
    from harness.agent import RunConfig, run_episode
    from harness.reference import builder_source
    t = task("t0_lamp")
    script = [qwen_call("view", {"layer": 1}), qwen_call("build", {"source": builder_source(t.solution)}, "Writing it."),
              qwen_call("run_spec", {}), qwen_call("submit", {})]
    llm = StubLocal(tiny, script)
    fin = run_episode(RunConfig(task="t0_lamp", model="local", arm="builder", rounds=1), llm, task=t)
    assert fin["complete"] and fin["ended_by"] == "submit" and fin["full_pass"] and fin["reward"] == 1.0
    assert fin["rounds_used"] == 1 and fin["build_attempts"][0]["ok"]
    assert fin["usage"]["prompt_tokens"] > 0 and fin["cost_usd"] == 0.0


def test_bad_json_tool_call_gets_harness_error_and_episode_continues(tiny, task):
    from harness.agent import RunConfig, run_episode
    t = task("t0_lamp")
    script = ['<tool_call>{"name": "build", "arguments": {"source": "unterminated}</tool_call>', qwen_call("submit", {})]
    fin = run_episode(RunConfig(task="t0_lamp", model="local", arm="builder", rounds=0), StubLocal(tiny, script), task=t)
    assert fin["complete"] and fin["ended_by"] == "submit" and not fin["full_pass"]


def test_greedy_chunked_prefill_and_prefix_reuse_match(tiny):
    from harness.llm_local import sample_tokens
    model, tok = tiny
    p = tok("the quick brown fox jumps over the lazy dog " * 6)["input_ids"]
    a, sa = sample_tokens(model, p, 12, [], temperature=0.0, chunk=100000)
    b, sb = sample_tokens(model, p, 12, [], temperature=0.0, chunk=7)
    assert a == b and sa == sb == "length"
    st = {}
    sample_tokens(model, p[:30], 5, [], temperature=0.0, prefix_state=st)
    c, _ = sample_tokens(model, p, 12, [], temperature=0.0, prefix_state=st)
    assert st["reused"] >= 29                 # only the suffix after the 30-token common prefix was prefilled
    assert c == a


def test_seeded_sampling_is_deterministic(tiny):
    import torch
    from harness.llm_local import sample_tokens
    model, tok = tiny
    p = tok("seeded sampling")["input_ids"]
    outs = []
    for seed in (5, 5, 6):
        torch.manual_seed(seed)
        outs.append(sample_tokens(model, p, 16, [], temperature=1.0)[0])
    assert outs[0] == outs[1] and outs[0] != outs[2]


def test_filter_logits_top_k_and_top_p():
    import torch
    from harness.llm_local import filter_logits
    lg = torch.tensor([3.0, 2.0, 1.0, 0.0])
    k = filter_logits(lg, 1.0, top_k=2)
    assert torch.isfinite(k).tolist() == [True, True, False, False]
    p = filter_logits(lg, 1.0, top_p=0.5)
    assert torch.isfinite(p).tolist() == [True, False, False, False]
    assert torch.equal(filter_logits(lg, 1.0), lg)


def test_replay_cache_skips_generation(tiny, tmp_path):
    cache = tmp_path / "cache.jsonl"
    msgs = [{"role": "user", "content": "x"}]
    a = StubLocal(tiny, ["first answer"], cache_path=cache)
    ra = a.chat(msgs, None, max_tokens=32, seed=3)

    def boom(i):
        raise AssertionError("generate_ids must not run on a cache hit")
    b = StubLocal(tiny, boom, cache_path=cache)
    rb = b.chat(msgs, None, max_tokens=32, seed=3)
    assert rb.message == ra.message and getattr(b, "calls_made", 0) == 0
    c = StubLocal(tiny, ["other"], cache_path=cache)
    assert c.chat(msgs, None, max_tokens=32, seed=4).message["content"] == "other"     # different seed = different key


def test_deadline_raises_llmerror_and_writes_stop(tiny, tmp_path):
    import time
    stop = tmp_path / "STOP"
    llm = StubLocal(tiny, ["x"], deadline=time.time() + 5, stop_file=stop)
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "x"}], None, max_tokens=8)
    assert stop.exists()


def test_context_overflow_returns_empty_reply(tiny):
    llm = StubLocal(tiny, ["never"], max_context=20)
    comp = llm.chat([{"role": "user", "content": "a long prompt " * 20}], None, max_tokens=8)
    assert comp.finish_reason == "context_overflow" and comp.message["content"] == "" and not comp.tool_calls


def test_run_experiment_backend_flag_is_additive():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "scripts" / "run_experiment.py"
    spec = importlib.util.spec_from_file_location("run_experiment_t", p)
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    a = rx.parse_args(["--exp-id", "x", "--budget-usd", "1"])
    assert a.backend == "openrouter" and a.adapter is None
    b = rx.parse_args(["--exp-id", "x", "--budget-usd", "1", "--backend", "local", "--models", "qwen3-4b", "--time-budget-s", "60"])
    assert b.backend == "local" and b.time_budget_s == 60
