"""SFT trainer (training/sft/train.py) on the tiny CPU model: masked loss, learning, checkpoint/resume, frozen split.
Plus the GPU smoke checks (marked gpu): the cached 1.7B model loads, generates a tool call, and one LoRA step fits."""
import json

import pytest

from conftest import needs_torch
from harness.llm import assistant, tool_call

pytestmark = needs_torch


def _examples(tok, n=6):
    from training.sft.dataset import render_episode
    out = []
    for i in range(n):
        ep = {"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": f"task {i}"},
                           assistant("ok", [tool_call("submit", {}, "c1")]),
                           {"role": "tool", "tool_call_id": "c1", "content": "scored"}],
              "tools": [], "meta": {"task": f"t{i}", "file": "f.jsonl", "line": i}}
        out.append(render_episode(tok, ep))
    return out


def test_masked_loss_equals_manual_cross_entropy(tiny):
    import torch
    import torch.nn.functional as F
    from training.sft.dataset import IGNORE
    from training.sft.train import masked_lm_loss
    model, tok = tiny
    ex = _examples(tok, 1)[0]
    ids, lab = torch.tensor([ex.input_ids]), torch.tensor([ex.labels])
    with torch.no_grad():
        s, n = masked_lm_loss(model, ids, lab, chunk=3, ckpt=False)
        logits = model(input_ids=ids).logits[0, :-1].float()
    want = F.cross_entropy(logits, lab[0, 1:], ignore_index=IGNORE, reduction="sum")
    assert n == ex.n_train - (1 if ex.labels[0] != IGNORE else 0)
    assert float(s) == pytest.approx(float(want), rel=1e-4)


def test_lr_schedule_warmup_then_cosine():
    from training.sft.train import lr_lambda
    f = lr_lambda(warmup=4, total=20, min_ratio=0.1)
    assert f(0) == pytest.approx(0.25) and f(3) == pytest.approx(1.0)
    assert f(4) == pytest.approx(1.0) and f(20) == pytest.approx(0.1) and f(12) < f(5)


def test_trainer_loss_falls_and_resume_continues(fresh_tiny, tmp_path):
    from training.local import lora as L
    from training.sft.train import Trainer
    model, tok = fresh_tiny
    L.apply_lora(model, r=8, alpha=16)
    data = _examples(tok, 4)
    tr = Trainer(model, data, data[:2], tmp_path, lr=5e-3, steps=6, warmup=1, grad_accum=2, device="cpu", eval_every=3,
                 save_every=3, log=lambda *_: None)
    res = tr.fit()
    assert res["done"]
    m = [json.loads(l) for l in (tmp_path / "metrics.jsonl").read_text().splitlines()]
    evals = [r["eval_loss"] for r in m if "eval_loss" in r]
    assert evals[-1] < evals[0]
    assert all(r["grad_norm"] == r["grad_norm"] for r in m if "grad_norm" in r)       # no NaN
    assert (tmp_path / "adapter" / "adapter_model.safetensors").exists()
    # resume: a new trainer on a fresh copy picks up step, data position and weights
    from training.local.models import tiny_model
    m2, _ = tiny_model(seed=1)
    L.apply_lora(m2, r=8, alpha=16)
    tr2 = Trainer(m2, data, data[:2], tmp_path, lr=5e-3, steps=8, warmup=1, grad_accum=2, device="cpu", log=lambda *_: None)
    assert tr2.resume() and tr2.state["step"] == 6 and tr2.state["epoch"] == 2 and tr2.state["idx"] == 4   # 12 episodes seen, epoch wraps lazily
    assert tr2.evaluate() == pytest.approx(tr.evaluate(), rel=1e-5)
    tr2.fit()
    assert tr2.state["step"] == 8


def test_frozen_split_survives_growing_data(tokenizer, tmp_path):
    from training.sft.train import frozen_split
    data = _examples(tokenizer, 10)
    tr, ev = frozen_split(tmp_path / "manifest.json", data, 0.2, 0)
    more = data + _examples(tokenizer, 14)[10:]
    for i, e in enumerate(more[10:]):
        e.meta["line"] = 100 + i
    tr2, ev2 = frozen_split(tmp_path / "manifest.json", more, 0.2, 0)
    assert [x.meta["line"] for x in tr2] == [x.meta["line"] for x in tr]
    assert [x.meta["line"] for x in ev2] == [x.meta["line"] for x in ev]


@pytest.mark.gpu
def test_gpu_local_model_emits_parseable_tool_call():
    from harness.llm_local import LocalLLM
    llm = LocalLLM("qwen3-1.7b")
    tools = [{"type": "function", "function": {"name": "submit", "description": "Finish the episode.",
                                               "parameters": {"type": "object", "properties": {}}}}]
    comp = llm.chat([{"role": "user", "content": "You are done. Call the submit tool now."}], tools, max_tokens=128,
                    temperature=0.0, seed=0)
    assert comp.tool_calls and comp.tool_calls[0]["function"]["name"] == "submit"


@pytest.mark.gpu
def test_gpu_one_lora_step_fits_8k_tokens():
    import torch
    from training.local import lora as L
    from training.local.models import load_model
    from training.sft.train import masked_lm_loss
    model, tok, _ = load_model("qwen3-1.7b")
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    L.apply_lora(model)
    model.train()
    torch.cuda.reset_peak_memory_stats()
    ids = torch.randint(1000, 30000, (1, 8192), device="cuda")
    lab = ids.clone()
    lab[:, :6000] = -100
    s, n = masked_lm_loss(model, ids, lab)
    (s / n).backward()
    assert torch.isfinite(s) and torch.cuda.max_memory_allocated() < 11e9
