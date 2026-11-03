"""SFT dataset rendering and loss masking (training/sft/dataset.py) with the real Qwen3 chat template."""
import json

from conftest import needs_torch
from harness.llm import assistant, tool_call

pytestmark = needs_torch

TOOLS = [{"type": "function", "function": {"name": "build", "description": "compile",
                                           "parameters": {"type": "object", "properties": {"source": {"type": "string"}}}}},
         {"type": "function", "function": {"name": "submit", "description": "end",
                                           "parameters": {"type": "object", "properties": {}}}}]


def episode(task="t_demo", verified=True):
    a1 = assistant("Writing the program.", [tool_call("build", {"source": "def build(b):\n    pass\n"}, "c1")])
    a2 = assistant("Done.", [tool_call("submit", {}, "c2")])
    return {"messages": [{"role": "system", "content": "SYSTEM PROMPT TEXT"}, {"role": "user", "content": "USER TASK TEXT"},
                         a1, {"role": "tool", "tool_call_id": "c1", "content": "TOOL RESULT ONE"},
                         a2, {"role": "tool", "tool_call_id": "c2", "content": "TOOL RESULT TWO"}],
            "tools": TOOLS, "meta": {"task": task, "verified": verified, "reward": 1.0 if verified else 0.2}}


def test_loss_only_on_assistant_tokens(tokenizer):
    from training.sft.dataset import IGNORE, render_episode
    ex = render_episode(tokenizer, episode())
    assert len(ex.input_ids) == len(ex.labels)
    trained = tokenizer.decode([t for t, l in zip(ex.input_ids, ex.labels) if l != IGNORE])
    masked = tokenizer.decode([t for t, l in zip(ex.input_ids, ex.labels) if l == IGNORE])
    for s in ("Writing the program.", '"name": "build"', "def build(b):", "Done.", '"name": "submit"', "<|im_end|>"):
        assert s in trained
    for s in ("SYSTEM PROMPT TEXT", "USER TASK TEXT", "TOOL RESULT ONE", "TOOL RESULT TWO", "<|im_start|>assistant", "<think>"):
        assert s not in trained and s in masked
    assert trained.count("<|im_end|>") == 2                 # each assistant turn learns to stop
    assert all(l == t for t, l in zip(ex.input_ids, ex.labels) if l != IGNORE)


def test_trained_turn_starts_where_generation_starts(tokenizer):
    """Every assistant turn is preceded by exactly the generation prompt the local backend uses at inference."""
    from harness.llm_local import render_prompt
    from training.sft.dataset import IGNORE, render_episode
    ep = episode()
    ex = render_episode(tokenizer, ep)
    first = next(i for i, l in enumerate(ex.labels) if l != IGNORE)
    prompt = render_prompt(tokenizer, ep["messages"][:2], ep["tools"], enable_thinking=False)
    assert tokenizer.decode(ex.input_ids[:first]) == prompt


def test_load_filters_unverified_and_split_is_by_task(tmp_path, tokenizer):
    from training.sft.dataset import build, load_episodes, split
    p = tmp_path / "eps.jsonl"
    rows = [episode(f"task{i % 5}") for i in range(20)] + [episode("bad", verified=False)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert len(load_episodes(p)) == 20
    assert len(load_episodes(p, verified_only=False)) == 21
    ex, rep = build([tmp_path], tokenizer, max_len=4096)
    assert rep["episodes"] == 20 and rep["kept"] == 20 and rep["fit_4096"] == 20
    tr, ev = split(ex, eval_frac=0.2, seed=0)
    assert ev and tr and not ({e.meta["task"] for e in tr} & {e.meta["task"] for e in ev})
    assert len(tr) + len(ev) == 20


def test_max_len_drops_long_episodes(tokenizer, tmp_path):
    from training.sft.dataset import build
    ep = episode()
    ep["messages"][1]["content"] = "word " * 3000
    (tmp_path / "a.jsonl").write_text(json.dumps(ep) + "\n" + json.dumps(episode()) + "\n")
    ex, rep = build([tmp_path], tokenizer, max_len=1024)
    assert rep["episodes"] == 2 and rep["kept"] == 1


def test_transcript_conversion_reproduces_what_the_model_saw(tmp_path, task):
    """A logged harness episode converts back to exactly the message list the model received (+ its last reply)."""
    from harness.agent import RunConfig, read_transcript, run_episode, safe_name
    from harness.reference import flawed_script
    from harness.llm import ScriptedLLM
    from training.sft.build_fixture import is_own, transcript_to_episode
    t = task("t1_xor")
    cfg = RunConfig("t1_xor", "mock", "builder", 2)
    llm = ScriptedLLM(flawed_script(t, "builder", 2, compile_typo=True))
    fin = run_episode(cfg, llm, exp_dir=tmp_path, task=t)
    ep = transcript_to_episode(read_transcript(tmp_path / "runs" / f"{safe_name(cfg.key)}.jsonl"))
    seen = llm.calls[-1]["messages"]
    assert ep["messages"][:len(seen)] == seen
    assert ep["meta"]["verified"] == fin["full_pass"] and ep["meta"]["source"] == "harness_transcript"
    assert [x["function"]["name"] for x in ep["tools"]] == llm.calls[0]["tools"]
    assert is_own("mock") and is_own("qwen3-4b+sft") and not is_own("deepseek-v4-flash")


def test_split_keeps_derived_episodes_with_their_base_circuit():
    from training.sft.dataset import Example, split
    ex = []
    for b in range(10):
        ex.append(Example([1], [1], {"task": f"sn_{b}"}))
        ex.append(Example([1], [1], {"task": f"rp_sn_{b}_0", "base_task": f"sn_{b}"}))
        ex.append(Example([1], [1], {"task": f"ex_sn_{b}", "base_task": f"sn_{b}"}))
    tr, ev = split(ex, 0.3, 0)
    base = lambda e: e.meta.get("base_task") or e.meta["task"]
    assert ev and not ({base(e) for e in tr} & {base(e) for e in ev})
    assert len(ev) % 3 == 0
