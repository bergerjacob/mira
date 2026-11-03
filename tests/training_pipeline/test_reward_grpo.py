"""RL reward adapter (training/rl/reward.py) and the GRPO pieces (training/rl/grpo.py)."""
import math

import pytest

from conftest import needs_torch
from harness.reference import builder_source
from training.rl.reward import (DEFAULT_WEIGHTS, RewardPool, extract_program, score_completions, score_program, shape,
                                single_turn_messages)


def test_extract_program_fenced_unfenced_and_missing():
    two = "draft:\n```python\nx = 1\n```\nfinal:\n```python\nfrom simulation.builder import *\ndef build(b):\n    pass\n```"
    assert "def build" in extract_program(two) and "x = 1" not in extract_program(two)
    bare = "Here is the program.\nfrom simulation.builder import *\n\ndef build(b):\n    b.set((1,1,1), wire())\n"
    assert extract_program(bare).startswith("from simulation.builder import *")
    assert extract_program("I cannot do this.") is None and extract_program("") is None


def test_reference_program_scores_full_reward(task):
    t = task("t1_xor")
    r = score_program(t, builder_source(t.solution))
    assert r.status == "ok" and r.full_pass and r.reward == pytest.approx(1.0) and r.env_reward == pytest.approx(1.0)
    assert r.components["r3"] == 1.0 and r.blocks == len(t.solution)


def test_staged_rewards_are_monotone(task):
    t = task("t0_lamp")
    none = score_program(t, None)
    syntax = score_program(t, "def build(b)\n    pass\n")
    empty = score_program(t, "from simulation.builder import *\ndef build(b):\n    pass\n")
    outside = score_program(t, "from simulation.builder import *\ndef build(b):\n    b.set((0, 9, 0), stone())\n")
    good = score_program(t, builder_source(t.solution))
    assert (none.status, syntax.status, empty.status, outside.status) == ("no_program", "compile_error", "empty", "rejected")
    assert none.reward == syntax.reward == empty.reward == 0.0
    assert outside.reward == pytest.approx(DEFAULT_WEIGHTS["rejected"])
    assert empty.env_reward == pytest.approx(0.2)          # what the env alone would have paid for not trying
    assert good.reward > outside.reward > syntax.reward


def test_broken_variant_is_partial_and_r2_is_not_rewarded(task):
    from env import mutate
    t = task("t0_and")
    bad = mutate.apply(t.solution, t.broken[0]["edits"])
    r = score_program(t, builder_source(bad))
    assert r.status == "ok" and not r.full_pass and 0 < r.reward < 1
    fin = {"r0": 1, "r1": 1, "r2": 1, "r3": 0, "r4": 0}
    assert shape(fin, True) == pytest.approx(0.1)          # R2 weight 0 by default
    assert shape(fin, True, {"r2": 0.1}) == pytest.approx(0.2)
    assert shape(fin, False) == 0.0


def test_reward_pool_matches_inprocess_scoring(task):
    t = task("t0_not")
    good = "```python\n" + builder_source(t.solution) + "```"
    with RewardPool(workers=2, timeout=120) as pool:
        par = score_completions("t0_not", [good, "no code here"], pool)
    seq = score_completions(t, [good, "no code here"])
    assert [r.reward for r in par] == [r.reward for r in seq] == [pytest.approx(1.0), 0.0]
    assert [r.status for r in par] == ["ok", "no_program"]


def test_single_turn_prompt_hides_hidden_vectors(task):
    t = task("t1_xor")
    msgs = single_turn_messages(t)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "fenced python block" in msgs[0]["content"] and "build(b)" in msgs[0]["content"]
    assert t.contract.describe(public=True) in msgs[1]["content"]
    hidden = t.contract.describe(public=False)
    assert hidden != t.contract.describe(public=True)
    assert hidden not in msgs[1]["content"]


@needs_torch
def test_advantages_and_token_loss_math():
    import torch
    from training.rl.grpo import advantages, grpo_token_loss
    adv, std = advantages([0.0, 1.0, 1.0, 0.0])
    assert std == pytest.approx(0.5) and adv == pytest.approx([-1.0, 1.0, 1.0, -1.0], abs=1e-3)
    assert advantages([0.3, 0.3])[0] == [0.0, 0.0]
    lp = torch.tensor([-1.0, -2.0], requires_grad=True)
    loss, pg, kl = grpo_token_loss(lp, lp.detach(), lp.detach(), adv=2.0, beta=0.1)
    assert torch.allclose(kl, torch.zeros(2)) and torch.allclose(pg, torch.tensor([-2.0, -2.0]))
    loss.sum().backward()
    assert torch.allclose(lp.grad, torch.tensor([-2.0, -2.0]))          # d/dlogp of -A*rho at rho=1
    _, _, kl2 = grpo_token_loss(torch.tensor([-1.0]), torch.tensor([-1.0]), torch.tensor([-1.5]), 0.0)
    d = -0.5
    assert float(kl2) == pytest.approx(math.exp(d) - d - 1)


@needs_torch
def test_token_logprobs_match_full_forward(tiny):
    import torch
    from training.rl.grpo import token_logprobs
    model, tok = tiny
    p, c = tok("prompt tokens here")["input_ids"], tok(" and a completion")["input_ids"]
    with torch.no_grad():
        got = token_logprobs(model, p, c)
        full = torch.log_softmax(model(input_ids=torch.tensor([p + c])).logits[0].float(), -1)
    want = torch.stack([full[len(p) - 1 + i, t] for i, t in enumerate(c)])
    assert torch.allclose(got, want, atol=1e-4)


@needs_torch
def test_policy_update_is_finite_and_moves_only_lora(fresh_tiny):
    import torch
    from training.local import lora as L
    from training.rl.grpo import batched_sample, policy_update
    model, tok = fresh_tiny
    L.apply_lora(model, r=4, alpha=8)
    params = L.lora_parameters(model)
    base_before = model.lm_head.weight.detach().clone()
    opt = torch.optim.AdamW(params, lr=1e-2)
    p = tok("build a lamp")["input_ids"]
    torch.manual_seed(0)
    comps = batched_sample(model, p, 3, 6, eos_ids=[tok.convert_tokens_to_ids("<|im_end|>")], temperature=1.0,
                           pad_id=tok.pad_token_id)
    assert len(comps) == 3 and all(1 <= len(c) <= 6 for c in comps)
    b_before = [m.lora_B.detach().clone() for _, m in L.lora_modules(model)]
    st = policy_update(model, opt, params, [(p, comps[0], 1.0), (p, comps[1], -1.0), (p, comps[2], 0.0)], beta=0.04, clip_eps=0.2)
    assert st["finite"] and st["tokens"] == sum(len(c) for c in comps) and math.isfinite(st["kl"])
    assert any(not torch.equal(b, m.lora_B) for b, (_, m) in zip(b_before, L.lora_modules(model)))
    assert torch.equal(base_before, model.lm_head.weight)
    st2 = policy_update(model, opt, params, [(p, comps[0], 1.0)], beta=0.04, clip_eps=0.2)
    assert st2["kl"] > 0                                    # the policy now differs from the frozen reference


@needs_torch
def test_multi_turn_rollout_collects_turn_traces(tiny, task):
    """rollout_episode drives the real harness with a trace-enabled LocalLLM; every turn becomes a (prompt, completion) segment."""
    import json as _json
    from harness.agent import RunConfig
    from harness.llm_local import LocalLLM
    from training.rl.reward import rollout_episode
    model, tok = tiny
    t = task("t0_lamp")
    replies = ["<tool_call>" + _json.dumps({"name": "build", "arguments": {"source": builder_source(t.solution)}}) + "</tool_call>",
               "<tool_call>" + _json.dumps({"name": "submit", "arguments": {}}) + "</tool_call>"]

    class L(LocalLLM):
        def generate_ids(self, prompt_ids, max_new, temperature, seed, max_time=None):
            i = len(self.traces)
            return tok(replies[i] + "<|im_end|>", add_special_tokens=False)["input_ids"], "eos"

    llm = L("qwen3-1.7b", model_obj=model, tokenizer=tok)
    tr = rollout_episode(t, llm, RunConfig(task="t0_lamp", model="local", arm="builder", rounds=0, max_turns=4))
    assert tr.reward == pytest.approx(1.0) and tr.final["full_pass"]
    assert len(tr.turns) == 2 and all(x["prompt_ids"] and x["completion_ids"] for x in tr.turns)
    # turn 2's prompt re-renders turn 1 from the history; Qwen3 drops the empty <think></think> block that the generation prompt
    # opened, so the prompts share everything up to that header (each turn is trained against its own rendered prompt)
    p0, p1 = tr.turns[0]["prompt_ids"], tr.turns[1]["prompt_ids"]
    lcp = next((i for i, (a, b) in enumerate(zip(p0, p1)) if a != b), len(p0))
    assert len(p0) - 6 <= lcp < len(p1)
    assert tr.signals["ended_by"] == "submit" and tr.signals["build_attempts"][0]["ok"]
