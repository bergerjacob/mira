"""Hand-written LoRA (training/local/lora.py): math, freezing, merge, save/load in PEFT layout."""
import json

import pytest

from conftest import needs_torch

pytestmark = needs_torch


def _lin(i=8, o=6, seed=0):
    import torch
    torch.manual_seed(seed)
    return torch.nn.Linear(i, o, bias=True)


def test_fresh_adapter_is_identity_and_matches_formula():
    import torch
    from training.local.lora import LoRALinear
    base = _lin()
    x = torch.randn(3, 8)
    ref = base(x).detach()
    m = LoRALinear(base, r=4, alpha=8)
    assert torch.allclose(m(x), ref)                     # B = 0 at init
    with torch.no_grad():
        m.lora_B.normal_()
    expect = ref + (x @ m.lora_A.t() @ m.lora_B.t()) * (8 / 4)
    assert torch.allclose(m(x), expect, atol=1e-5)
    assert torch.allclose(m.delta_weight(), (m.lora_B @ m.lora_A) * 2.0)


def test_merge_equals_unmerged_and_disable_restores_base():
    import torch
    from training.local.lora import LoRALinear
    base = _lin(seed=1)
    m = LoRALinear(base, r=2, alpha=4)
    x = torch.randn(5, 8)
    with torch.no_grad():
        m.lora_B.normal_()
    y = m(x).detach()
    m.enabled = False
    assert not torch.allclose(m(x), y)
    m.enabled = True
    m.merge()
    assert torch.allclose(m(x), y, atol=1e-5) and m.merged


def test_only_lora_params_train_and_receive_gradients(fresh_tiny):
    import torch
    from training.local.lora import apply_lora, count_trainable, lora_parameters
    model, tok = fresh_tiny
    names = apply_lora(model, r=4, alpha=8)
    assert names and all(n.split(".")[-1] in ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
                         for n in names)
    assert "lm_head" not in " ".join(names)
    params = lora_parameters(model)
    assert count_trainable(model) == sum(p.numel() for p in params)
    ids = torch.tensor([tok("hello world, a redstone torch")["input_ids"]])
    model.train()
    out = model(input_ids=ids, labels=ids)
    out.loss.backward()
    assert all(p.grad is not None for p in params)
    assert any(float(p.grad.abs().sum()) > 0 for p in params)
    assert all(p.grad is None for n, p in model.named_parameters() if "lora_" not in n)


def test_lora_disabled_context_gives_base_logits(fresh_tiny):
    import torch
    from training.local.lora import apply_lora, lora_disabled, lora_modules
    model, tok = fresh_tiny
    ids = torch.tensor([tok("abc def")["input_ids"]])
    with torch.no_grad():
        base = model(input_ids=ids).logits
        apply_lora(model, r=2, alpha=4)
        for _, m in lora_modules(model):
            m.lora_B.normal_(std=0.1)
        tuned = model(input_ids=ids).logits
        with lora_disabled(model):
            again = model(input_ids=ids).logits
    assert not torch.allclose(base, tuned)
    assert torch.allclose(base, again, atol=1e-5)


def test_save_load_roundtrip_peft_layout(fresh_tiny, tmp_path):
    import torch
    from safetensors.torch import load_file
    from training.local.lora import apply_lora, load_adapter, lora_modules, save_adapter
    from training.local.models import tiny_model
    model, tok = fresh_tiny
    apply_lora(model, r=4, alpha=16)
    for _, m in lora_modules(model):
        torch.nn.init.normal_(m.lora_B, std=0.05)
    save_adapter(model, tmp_path / "ad", "tiny/base")
    cfg = json.loads((tmp_path / "ad" / "adapter_config.json").read_text())
    assert cfg["peft_type"] == "LORA" and cfg["r"] == 4 and cfg["lora_alpha"] == 16
    keys = load_file(str(tmp_path / "ad" / "adapter_model.safetensors"))
    assert all(k.startswith("base_model.model.") and (k.endswith("lora_A.weight") or k.endswith("lora_B.weight")) for k in keys)
    ids = torch.tensor([tok("roundtrip")["input_ids"]])
    with torch.no_grad():
        want = model(input_ids=ids).logits
    other, _ = tiny_model(seed=1)                       # same base weights (seed 1), fresh process-like copy
    load_adapter(other, tmp_path / "ad")
    with torch.no_grad():
        got = other(input_ids=ids).logits
    assert torch.allclose(want, got, atol=1e-5)
    merged, _ = tiny_model(seed=1)
    load_adapter(merged, tmp_path / "ad", merge=True)
    with torch.no_grad():
        assert torch.allclose(want, merged(input_ids=ids).logits, atol=1e-4)


def test_rank_must_be_positive():
    from training.local.lora import LoRALinear
    with pytest.raises(ValueError):
        LoRALinear(_lin(), r=0)
