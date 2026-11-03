"""Load cached HF causal LMs offline (no accelerate: load on CPU, then move), plus a tiny random model for tests.

Short names -> hub ids of the models cached on the dev box (checked 2026-11-02 with transformers 5.2, offline):
  qwen3-4b    Qwen/Qwen3-4B-Instruct-2507   loads, 8.1 GB bf16, native <tool_call> template, non-thinking
  qwen3-1.7b  Qwen/Qwen3-1.7B               loads, 3.4 GB bf16, hybrid thinking (enable_thinking)
  llama3.2-1b meta-llama/Llama-3.2-1B-Instruct   loads; JSON tool calls (parsed by harness/llm_local.parse_tool_calls)
  gemma3-1b   google/gemma-3-1b-it          loads; its template has no tools support
Not usable here: Qwen/Qwen3.5-2B (tokenizer only, no weights in the cache), Qwen/Qwen3-4B-AWQ and Qwen3-14B-AWQ (need autoawq).
"""
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # cached models only (must be set before huggingface_hub is imported)
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

MODEL_ALIASES = {
    "qwen3-4b": "Qwen/Qwen3-4B-Instruct-2507",
    "qwen3-1.7b": "Qwen/Qwen3-1.7B",
    "llama3.2-1b": "meta-llama/Llama-3.2-1B-Instruct",
    "gemma3-1b": "google/gemma-3-1b-it",
}
TOKENIZER_FOR_TINY = "Qwen/Qwen3-1.7B"
# prompt+completion tokens that fit the RTX 3060 (12 GB, ~1.1 GB used by the desktop) in bf16 with 512-token chunked prefill
# (harness/llm_local.sample_tokens): Qwen3-4B peaks at 10.1 GB with a 12k prompt and OOMs at 16k (8.0 GB weights + 147 KB of
# KV cache per token; measured 2026-11-02 with ~1.1 GB used by the desktop; later the desktop held ~2 GB and an 11k prompt OOMed,
# so the default is 11500 and an OOM during generation is returned as an empty `oom` reply). The 1.7B model (3.4 GB + 114 KB/token)
# fits its 32k window.
MAX_CONTEXT = {"Qwen/Qwen3-4B-Instruct-2507": 11500, "Qwen/Qwen3-1.7B": 32768}


def resolve(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


def max_context(name: str, default: int = 32768) -> int:
    return MAX_CONTEXT.get(resolve(name), default)


def pick_device(device=None) -> str:
    import torch
    if device:
        return device
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_tokenizer(name: str):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(resolve(name), local_files_only=True)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    return tok


def load_model(name: str, device=None, dtype: str = "bfloat16", attn: str = "sdpa"):
    """Model in eval mode on `device` (default cuda if available). Returns (model, tokenizer, info)."""
    import torch
    from transformers import AutoModelForCausalLM
    device = pick_device(device)
    t0 = time.time()
    tok = load_tokenizer(name)
    model = AutoModelForCausalLM.from_pretrained(resolve(name), local_files_only=True, dtype=getattr(torch, dtype),
                                                 attn_implementation=attn)
    model.to(device).eval()
    info = {"model_id": resolve(name), "device": device, "dtype": dtype, "load_s": round(time.time() - t0, 1),
            "params": sum(p.numel() for p in model.parameters())}
    return model, tok, info


def tiny_model(seed: int = 0, vocab_size=None, hidden: int = 64, layers: int = 2):
    """Random 2-layer Qwen3 with the real Qwen3 tokenizer (cached), for CPU tests of generation/LoRA/GRPO plumbing.
    Returns (model, tokenizer)."""
    import torch
    from transformers import Qwen3Config, Qwen3ForCausalLM
    tok = load_tokenizer(TOKENIZER_FOR_TINY)
    torch.manual_seed(seed)
    cfg = Qwen3Config(vocab_size=vocab_size or len(tok), hidden_size=hidden, intermediate_size=hidden * 2,
                      num_hidden_layers=layers, num_attention_heads=4, num_key_value_heads=2, head_dim=hidden // 4,
                      max_position_embeddings=4096, tie_word_embeddings=True, eos_token_id=tok.convert_tokens_to_ids("<|im_end|>"),
                      pad_token_id=tok.pad_token_id)
    model = Qwen3ForCausalLM(cfg).to(torch.float32).eval()
    return model, tok


def tokenizer_cached(name: str = TOKENIZER_FOR_TINY) -> bool:
    """True if the tokenizer can be loaded offline (tests skip otherwise)."""
    try:
        load_tokenizer(name)
        return True
    except Exception:
        return False


def gpu_mem_gb() -> dict:
    import torch
    if not torch.cuda.is_available():
        return {}
    return {"allocated": round(torch.cuda.memory_allocated() / 1e9, 2),
            "peak": round(torch.cuda.max_memory_allocated() / 1e9, 2),
            "total": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 2)}


def model_dir(name: str) -> Path:
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(resolve(name), local_files_only=True))
