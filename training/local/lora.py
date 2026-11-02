"""Hand-written LoRA (no peft): y = W x + (alpha / r) * B A dropout(x), base weights frozen.

A is Kaiming-uniform, B is zero (so a fresh adapter is exactly the base model). LoRA parameters are kept in fp32 (master
weights for AdamW) and cast to the activation dtype (bf16) inside forward, so gradients reach the fp32 copies.
Adapters are saved in PEFT's layout (`adapter_model.safetensors` with `base_model.model.<module>.lora_{A,B}.weight` keys +
`adapter_config.json`), so a PEFT/vLLM install on the HPC side can load them as is.
"""
import contextlib
import json
import math
from pathlib import Path

import torch
import torch.nn as nn

DEFAULT_TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear, r: int = 16, alpha: float = 32.0, dropout: float = 0.0):
        super().__init__()
        if r <= 0:
            raise ValueError("LoRA rank must be positive")
        self.base = base
        for p in self.base.parameters():
            p.requires_grad_(False)
        self.r, self.alpha = int(r), float(alpha)
        self.scaling = self.alpha / self.r
        dev = base.weight.device
        self.lora_A = nn.Parameter(torch.empty(self.r, base.in_features, dtype=torch.float32, device=dev))
        self.lora_B = nn.Parameter(torch.zeros(base.out_features, self.r, dtype=torch.float32, device=dev))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        self.dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()
        self.enabled = True
        self.merged = False

    @property
    def in_features(self):
        return self.base.in_features

    @property
    def out_features(self):
        return self.base.out_features

    @property
    def weight(self):                      # some HF code paths peek at .weight (dtype/device checks)
        return self.base.weight

    def delta_weight(self) -> torch.Tensor:
        return (self.lora_B @ self.lora_A) * self.scaling

    def forward(self, x):
        y = self.base(x)
        if not self.enabled or self.merged:
            return y
        a = self.lora_A.to(x.dtype)
        b = self.lora_B.to(x.dtype)
        return y + (self.dropout(x) @ a.t() @ b.t()) * self.scaling

    @torch.no_grad()
    def merge(self):
        if not self.merged:
            self.base.weight += self.delta_weight().to(self.base.weight.dtype)
            self.merged = True

    def extra_repr(self):
        return f"r={self.r}, alpha={self.alpha}, in={self.in_features}, out={self.out_features}"


def _parent(model, name):
    *path, leaf = name.split(".")
    mod = model
    for p in path:
        mod = getattr(mod, p)
    return mod, leaf


def apply_lora(model, r: int = 16, alpha: float = 32.0, dropout: float = 0.0, targets=DEFAULT_TARGETS):
    """Wrap every nn.Linear whose leaf name is in `targets` (not lm_head). Freezes all other parameters. Returns the
    wrapped module names."""
    for p in model.parameters():
        p.requires_grad_(False)
    names = [n for n, m in model.named_modules() if isinstance(m, nn.Linear) and n.split(".")[-1] in targets]
    for n in names:
        parent, leaf = _parent(model, n)
        setattr(parent, leaf, LoRALinear(getattr(parent, leaf), r, alpha, dropout))
    model._lora_config = {"r": r, "lora_alpha": alpha, "lora_dropout": dropout, "target_modules": sorted(set(targets))}
    return names


def lora_modules(model):
    return [(n, m) for n, m in model.named_modules() if isinstance(m, LoRALinear)]


def lora_parameters(model):
    return [p for _, m in lora_modules(model) for p in (m.lora_A, m.lora_B)]


def count_trainable(model) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


@contextlib.contextmanager
def lora_disabled(model):
    """Run the frozen base model (reference policy for the KL term)."""
    mods = [m for _, m in lora_modules(model)]
    prev = [m.enabled for m in mods]
    for m in mods:
        m.enabled = False
    try:
        yield
    finally:
        for m, e in zip(mods, prev):
            m.enabled = e


def peft_key(module_name: str, which: str) -> str:
    return f"base_model.model.{module_name}.lora_{which}.weight"


def adapter_state(model) -> dict:
    sd = {}
    for n, m in lora_modules(model):
        sd[peft_key(n, "A")] = m.lora_A.detach().float().cpu().contiguous()
        sd[peft_key(n, "B")] = m.lora_B.detach().float().cpu().contiguous()
    return sd


def save_adapter(model, out_dir, base_model_id: str = "", extra: dict = None):
    from safetensors.torch import save_file
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = dict(getattr(model, "_lora_config", {}))
    cfg.update({"peft_type": "LORA", "task_type": "CAUSAL_LM", "base_model_name_or_path": base_model_id, "bias": "none",
                "inference_mode": True, "fan_in_fan_out": False, "mira": extra or {}})
    tmp = out / "adapter_model.safetensors.tmp"
    save_file(adapter_state(model), str(tmp))
    tmp.replace(out / "adapter_model.safetensors")
    (out / "adapter_config.json").write_text(json.dumps(cfg, indent=1))
    return out


def load_adapter(model, adapter_dir, merge: bool = False):
    """Apply (if needed) and load a saved adapter into `model`. `merge=True` folds it into the base weights (inference)."""
    from safetensors.torch import load_file
    d = Path(adapter_dir)
    cfg = json.loads((d / "adapter_config.json").read_text())
    if not lora_modules(model):
        apply_lora(model, cfg["r"], cfg["lora_alpha"], 0.0, tuple(cfg["target_modules"]))
    sd = load_file(str(d / "adapter_model.safetensors"))
    mods = dict(lora_modules(model))
    seen = 0
    for n, m in mods.items():
        a, b = sd.get(peft_key(n, "A")), sd.get(peft_key(n, "B"))
        if a is None or b is None:
            raise KeyError(f"adapter {d} has no weights for {n}")
        with torch.no_grad():
            m.lora_A.copy_(a.to(m.lora_A.device))
            m.lora_B.copy_(b.to(m.lora_B.device))
        seen += 1
    if seen * 2 != len(sd):
        raise KeyError(f"adapter {d} has {len(sd)} tensors for {seen} modules")
    if merge:
        merge_lora(model)
    return cfg


def merge_lora(model):
    for _, m in lora_modules(model):
        m.merge()
