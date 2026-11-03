"""Local HF-transformers backend with the same interface as harness/llm.py's clients (OpenRouterLLM, ScriptedLLM).

    llm = LocalLLM("qwen3-4b")                       # loads Qwen/Qwen3-4B-Instruct-2507 from the HF cache (offline)
    comp = llm.chat(messages, tools=openai_tools, max_tokens=4096, temperature=0.7, seed=1001)
    comp.message -> {"role": "assistant", "content": str, "tool_calls": [...]}; comp.usage; comp.cost == 0.0

Tool calling is native: the conversation (OpenAI format, incl. assistant tool_calls and role=tool messages) is rendered with the
model's own chat template and `tools=`; tool calls are parsed back from the generated text (`parse_tool_calls`: Qwen/Hermes
`<tool_call>{json}</tool_call>`, Llama-style bare JSON `{"name", "parameters"}`). Qwen3 `<think>` blocks are split off,
counted as reasoning tokens and not sent back (the Qwen3 template drops old reasoning anyway).

Determinism: every call reseeds torch with `seed` (the harness passes 1000*seed + turn); temperature <= 0 is greedy.
Sampling uses the harness temperature with the model's own generation_config top_p/top_k (Qwen3-4B-Instruct: 0.8/20, as an
API provider serving the model would); pure sampling (top_p=1, top_k=0, needed for exact RL log-probs) is opt-in.
Optional `cache_path`: completed generations are appended to a JSONL replay cache keyed by the exact rendered prompt and the
sampling settings, so an episode cut by `deadline` (the dev box kills processes after ~9 min) replays its finished turns instantly
when the experiment is resumed. A call that would cross `deadline` touches `stop_file` and raises LLMError: the harness records
the episode as incomplete (`api_error`) and the runner stops at the next episode (STOP file), so the grid resumes cleanly.
torch / transformers are imported lazily; parsing helpers work without them.
"""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import List, Optional

from harness.llm import BaseLLM, Completion, LLMError

THINK_RE = re.compile(r"<think>(.*?)</think>", re.S)
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
FENCE_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
END_MARKERS = ("<|im_end|>", "<|endoftext|>", "<|eot_id|>", "<|eom_id|>", "<end_of_turn>", "<eos>", "</s>")


# --------------------------------------------------------------------------- parsing (no torch needed)
def _loads(s: str):
    """json.loads that tolerates raw newlines/tabs inside strings (models often emit code that way)."""
    return json.loads(s, strict=False)


def _call(name: str, args, i: int) -> dict:
    if not isinstance(args, str):
        args = json.dumps(args if args is not None else {})
    return {"id": f"call_{uuid.uuid4().hex[:8]}_{i}", "type": "function", "function": {"name": name, "arguments": args}}


def _obj_to_call(obj, i: int) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    if "function" in obj and isinstance(obj["function"], dict):      # {"type":"function","function":{name, arguments}}
        obj = obj["function"]
    name = obj.get("name")
    if not isinstance(name, str):
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    return _call(name, args, i)


def split_thinking(text: str):
    """(reasoning, rest). Handles a closed <think>..</think> block and the Qwen3 form where the template already opened
    <think> so the output starts inside it ('...</think> answer')."""
    m = THINK_RE.search(text)
    if m:
        return m.group(1).strip(), (text[:m.start()] + text[m.end():]).strip()
    if "</think>" in text:
        a, b = text.split("</think>", 1)
        return a.replace("<think>", "").strip(), b.strip()
    if text.lstrip().startswith("<think>"):          # unterminated (cut by the token limit): all reasoning
        return text.lstrip()[len("<think>"):].strip(), ""
    return "", text


def parse_tool_calls(text: str):
    """Generated assistant text -> (content, tool_calls). A <tool_call> block whose JSON does not parse is still returned as
    a call (name recovered by regex if possible) with the raw text as `arguments`, so the harness answers with its
    "arguments are not valid JSON" error instead of silently dropping the attempt, as an API provider would."""
    for m in END_MARKERS:
        text = text.replace(m, "")
    calls = []
    blocks = list(TOOL_CALL_RE.finditer(text))
    if blocks:
        for i, m in enumerate(blocks):
            raw = m.group(1).strip()
            c = None
            try:
                c = _obj_to_call(_loads(raw), i)
            except ValueError:
                pass
            if c is None:
                nm = re.search(r'"name"\s*:\s*"([^"]+)"', raw)
                c = _call(nm.group(1) if nm else "", raw, i)
            calls.append(c)
        content = TOOL_CALL_RE.sub("", text).strip()
        return content, calls
    # Llama 3.x style: the whole reply (optionally after <|python_tag|> or in a ```json fence) is one or more JSON calls
    body = text.replace("<|python_tag|>", "").strip()
    cands = []
    if body.startswith("{") or body.startswith("["):
        try:
            obj = _loads(body)
            cands = obj if isinstance(obj, list) else [obj]
        except ValueError:
            parts = [p for p in body.split(";") if p.strip()]       # Llama emits several calls separated by ';'
            try:
                cands = [_loads(p) for p in parts]
            except ValueError:
                cands = []
    else:
        for m in FENCE_JSON_RE.finditer(text):
            try:
                cands.append(_loads(m.group(1)))
            except ValueError:
                continue
    for i, obj in enumerate(cands):
        c = _obj_to_call(obj, i)
        if c is not None:
            calls.append(c)
    if calls:
        content = "" if (body.startswith("{") or body.startswith("[")) else FENCE_JSON_RE.sub("", text).strip()
        return content, calls
    return text.strip(), []


def template_messages(messages: List[dict]) -> List[dict]:
    """OpenAI-format history -> what HF chat templates expect: tool-call arguments as dicts (Qwen/Llama templates render
    dicts with tojson; a JSON string would be double-encoded by some) unless they are not valid JSON (kept as the raw string,
    i.e. exactly what the model wrote), provider-only keys dropped."""
    out = []
    for m in messages:
        mm = {"role": m["role"], "content": m.get("content") or ""}
        if m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        parsed = _loads(args)
                    except ValueError:
                        parsed = None
                    # valid JSON -> dict (templates tojson it); invalid (e.g. cut by the token limit) -> the raw text, which
                    # Qwen templates emit verbatim. A wrapper like {"_raw": ...} was imitated and nested by Qwen3-1.7B.
                    args = parsed if isinstance(parsed, dict) else args
                calls.append({"id": c.get("id"), "type": "function", "function": {"name": fn.get("name", ""), "arguments": args}})
            mm["tool_calls"] = calls
        if m["role"] == "tool":
            if m.get("tool_call_id"):
                mm["tool_call_id"] = m["tool_call_id"]
            if m.get("name"):
                mm["name"] = m["name"]
        out.append(mm)
    return out


def render_prompt(tokenizer, messages, tools=None, enable_thinking: bool = False, add_generation_prompt: bool = True) -> str:
    """Chat-template text for `messages` (+ `tools`), ending with the assistant generation header."""
    kw = {"tokenize": False, "add_generation_prompt": add_generation_prompt, "enable_thinking": enable_thinking}
    if tools:
        kw["tools"] = tools
    return tokenizer.apply_chat_template(template_messages(messages), **kw)


# --------------------------------------------------------------------------- sampling
PREFILL_CHUNK = 512


def filter_logits(logits, temperature: float, top_p=None, top_k=None):
    """Temperature + top-k + nucleus filtering of a [V] or [B, V] float tensor (masked entries = -inf)."""
    import torch
    logits = logits / max(float(temperature), 1e-6)
    if top_k is not None and 0 < int(top_k) < logits.shape[-1]:
        kth = torch.topk(logits, int(top_k), dim=-1).values[..., -1:]
        logits = logits.masked_fill(logits < kth, float("-inf"))
    if top_p is not None and 0 < float(top_p) < 1:
        srt, idx = torch.sort(logits, descending=True, dim=-1)
        probs = torch.softmax(srt, dim=-1)
        drop = probs.cumsum(-1) - probs > float(top_p)        # keep the smallest prefix whose mass reaches top_p
        srt = srt.masked_fill(drop, float("-inf"))
        logits = torch.full_like(logits, float("-inf")).scatter(-1, idx, srt)
    return logits


def sample_tokens(model, prompt_ids, max_new: int, eos_ids, temperature: float = 0.0, top_p=None, top_k=None,
                  max_time=None, chunk: int = PREFILL_CHUNK, prefix_state: Optional[dict] = None):
    """KV-cached decoding with our own chunked prefill (transformers 5.2's `generate(prefill_chunk_size=...)` corrupts the
    output: greedy t0_and turned into gibberish, measured 2026-11-02; unchunked prefill of a 14k prompt does not fit 12 GB).
    `prefix_state` (a dict owned by the caller) keeps the KV cache of the previous call: the next prompt only prefills the
    tokens after the longest common prefix (a multi-turn episode re-sends the whole history every turn; prefill of 5-10k
    tokens was 60-90% of a short turn's latency). Returns (completion_ids, stopped_by in eos | length | time).
    Seeding is the caller's job."""
    import torch
    from transformers import DynamicCache
    dev = next(model.parameters()).device
    prompt_ids = list(prompt_ids)
    cache, start = None, 0
    if prefix_state is not None and prefix_state.get("cache") is not None:
        old = prefix_state["ids"]
        n = min(len(old), len(prompt_ids) - 1)        # always prefill at least one token (we need its logits)
        lcp = 0
        while lcp < n and old[lcp] == prompt_ids[lcp]:
            lcp += 1
        if lcp > 0:
            cache = prefix_state["cache"]
            cache.crop(lcp)
            start = lcp
        prefix_state["cache"] = None
    if cache is None:
        cache = DynamicCache(config=model.config)
    ids = torch.tensor([prompt_ids], device=dev)
    eos = set(int(e) for e in eos_ids)
    out_ids, t0, stopped = [], time.time(), "length"
    with torch.inference_mode():
        for s in range(start, ids.shape[1], chunk):
            out = model(input_ids=ids[:, s:s + chunk], past_key_values=cache, use_cache=True, logits_to_keep=1)
        logits = out.logits[0, -1].float()
        for _ in range(max_new):
            if temperature and temperature > 0:
                probs = torch.softmax(filter_logits(logits, temperature, top_p, top_k), dim=-1)
                tok = int(torch.multinomial(probs, 1))
            else:
                tok = int(torch.argmax(logits))
            out_ids.append(tok)
            if tok in eos:
                stopped = "eos"
                break
            if max_time is not None and time.time() - t0 > max_time:
                stopped = "time"
                break
            out = model(input_ids=torch.tensor([[tok]], device=dev), past_key_values=cache, use_cache=True)
            logits = out.logits[0, -1].float()
    if prefix_state is not None:
        prefix_state["ids"] = prompt_ids + out_ids[:-1]     # the last sampled token was never fed
        prefix_state["cache"] = cache
        prefix_state["reused"] = start
    return out_ids, stopped


# --------------------------------------------------------------------------- backend
class LocalLLM(BaseLLM):
    """HF transformers generation behind the BaseLLM interface (budget path kept: reservations are $0).

    `model` is a short name (training/local/models.MODEL_ALIASES) or hub id; pass `model_obj` + `tokenizer` to share an already
    loaded model between episodes (or a tiny model in tests). `adapter` = LoRA adapter dir (training/local/lora.py).
    `enable_thinking`: Qwen3 hybrid models; the harness `reasoning` argument ("low"/"medium"/"high") also switches it on.
    `trace=True` keeps (prompt_ids, completion_ids) of every call in `self.traces` (RL rollouts)."""

    def __init__(self, model: str = "qwen3-4b", budget=None, *, tag: str = "", model_obj=None, tokenizer=None, adapter=None,
                 device=None, dtype: str = "bfloat16", enable_thinking: bool = False, max_context: Optional[int] = None,
                 cache_path=None, deadline: Optional[float] = None, stop_file=None, trace: bool = False,
                 model_name: Optional[str] = None, top_p: Optional[float] = None, top_k: Optional[int] = None,
                 prefix_cache: bool = True, prefix_state: Optional[dict] = None):
        from training.local.models import max_context as ctx_of, resolve
        super().__init__(model_name or model, budget, tag)
        self.model_id = resolve(model)
        self.enable_thinking = enable_thinking
        self.max_context = max_context or ctx_of(model)
        self.cache_path = Path(cache_path) if cache_path else None
        self.deadline = deadline
        self.stop_file = Path(stop_file) if stop_file else None
        self.trace = trace
        self.traces = []
        self.top_p, self.top_k = top_p, top_k          # None = the model's generation_config (model-card sampling)
        self.prefix_cache = prefix_cache
        self.prefix_state = prefix_state if prefix_state is not None else {}
        self.reused_tokens = 0
        self.oom_events = 0
        self.gen_seconds = 0.0
        self.generated_tokens = 0
        self.adapter = str(adapter) if adapter else None
        if model_obj is None:
            from training.local.models import load_model
            model_obj, tokenizer, self.load_info = load_model(model, device=device, dtype=dtype)
        else:
            self.load_info = {"model_id": self.model_id, "injected": True}
        if adapter:
            from training.local.lora import load_adapter
            load_adapter(model_obj, adapter, merge=True)
        self.hf_model, self.tokenizer = model_obj, tokenizer
        self._cache = self._load_cache()
        self._eos = self._eos_ids()

    # -- budget: local generation is free; reserve $0 so a shared Budget still works
    def worst_case_cost(self, messages, tools, max_tokens) -> float:
        return 0.0

    def _eos_ids(self):
        ids = set()
        gc = getattr(self.hf_model, "generation_config", None)
        e = getattr(gc, "eos_token_id", None) if gc is not None else None
        for v in (e if isinstance(e, (list, tuple)) else [e]):
            if v is not None:
                ids.add(int(v))
        for t in END_MARKERS:
            i = self.tokenizer.convert_tokens_to_ids(t)
            if isinstance(i, int) and i >= 0 and i != self.tokenizer.unk_token_id:
                ids.add(i)
        if self.tokenizer.eos_token_id is not None:
            ids.add(int(self.tokenizer.eos_token_id))
        return sorted(ids)

    # -- replay cache
    def _load_cache(self):
        c = {}
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text().splitlines():
                try:
                    r = json.loads(line)
                    c[r["key"]] = r
                except (ValueError, KeyError):
                    continue
        return c

    def _key(self, prompt: str, max_new: int, temperature: float, seed, thinking: bool) -> str:
        h = hashlib.sha256()
        h.update(json.dumps([self.model_id, self.adapter, max_new, round(float(temperature), 4), seed, thinking,
                             self.top_p, self.top_k]).encode())
        h.update(prompt.encode())
        return h.hexdigest()

    def _hit_deadline(self, why: str):
        if self.stop_file is not None:
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.write_text(f"local deadline: {why}")
        raise LLMError(f"local deadline reached ({why}); episode left pending for resume")

    # -- generation
    def generate_ids(self, prompt_ids: List[int], max_new: int, temperature: float, seed, max_time=None):
        """Raw sampling; returns (completion_ids, stopped_by) with stopped_by in eos | length | time."""
        import torch
        if seed is not None:
            torch.manual_seed(int(seed))
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(int(seed))
        gc = getattr(self.hf_model, "generation_config", None)
        top_p = self.top_p if self.top_p is not None else getattr(gc, "top_p", None)
        top_k = self.top_k if self.top_k is not None else getattr(gc, "top_k", None)
        st = self.prefix_state if self.prefix_cache else None
        out = sample_tokens(self.hf_model, prompt_ids, max_new, self._eos, temperature=temperature, top_p=top_p, top_k=top_k,
                            max_time=max_time, prefix_state=st)
        if st is not None:
            self.reused_tokens += st.get("reused", 0)
        return out

    def _complete(self, messages, tools, max_tokens, temperature, seed, reasoning) -> Completion:
        thinking = self.enable_thinking or (reasoning in ("low", "medium", "high"))
        prompt = render_prompt(self.tokenizer, messages, tools, enable_thinking=thinking)
        prompt_ids = self.tokenizer(prompt, add_special_tokens=False)["input_ids"]
        n_prompt = len(prompt_ids)
        room = self.max_context - n_prompt
        if room < 16:
            msg = {"role": "assistant", "content": ""}
            return Completion(msg, "context_overflow", {"prompt_tokens": n_prompt, "completion_tokens": 0, "reasoning_tokens": 0,
                                                        "cached_tokens": 0}, 0.0, "local", 0.0, self.model_id)
        max_new = min(int(max_tokens), room)
        key = self._key(prompt, max_new, temperature, seed, thinking)
        hit = self._cache.get(key)
        t0 = time.time()
        if hit is not None:
            comp_ids, stopped, latency = hit["completion_ids"], hit["stopped"], hit["latency"]
        else:
            max_time = None
            if self.deadline is not None:
                left = self.deadline - time.time()
                if left < 90:                  # a typical build message takes 30-60 s; do not start one that will be cut
                    self._hit_deadline("no time left for another generation")
                max_time = left
            try:
                comp_ids, stopped = self.generate_ids(prompt_ids, max_new, temperature, seed, max_time=max_time)
            except Exception as e:           # CUDA OOM on a long prompt: a (recorded) empty reply, not a crash
                if "OutOfMemory" not in type(e).__name__:
                    raise
                import torch
                self.prefix_state.clear()
                torch.cuda.empty_cache()
                self.oom_events += 1
                msg = {"role": "assistant", "content": ""}
                return Completion(msg, "oom", {"prompt_tokens": n_prompt, "completion_tokens": 0, "reasoning_tokens": 0,
                                               "cached_tokens": 0}, 0.0, "local", time.time() - t0, self.model_id)
            latency = time.time() - t0
            if stopped == "time":
                self._hit_deadline("generation cut by the time limit")
            if self.cache_path is not None:
                rec = {"key": key, "completion_ids": comp_ids, "stopped": stopped, "latency": round(latency, 3),
                       "n_prompt": n_prompt, "t": round(time.time(), 1)}
                self._cache[key] = rec
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_path, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
            self.gen_seconds += latency
            self.generated_tokens += len(comp_ids)
        raw = self.tokenizer.decode(comp_ids, skip_special_tokens=False)
        if thinking and "<think>" in prompt[-40:] and "<think>" not in raw:
            raw = "<think>" + raw       # template opened the think block in the prompt
        reasoning_txt, rest = split_thinking(raw)
        content, calls = parse_tool_calls(rest)
        n_reason = len(self.tokenizer(reasoning_txt, add_special_tokens=False)["input_ids"]) if reasoning_txt else 0
        msg = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = calls
        finish = "length" if stopped == "length" else ("tool_calls" if calls else "stop")
        usage = {"prompt_tokens": n_prompt, "completion_tokens": len(comp_ids), "reasoning_tokens": n_reason, "cached_tokens": 0}
        if self.trace:
            self.traces.append({"prompt_ids": prompt_ids, "completion_ids": comp_ids, "raw": raw, "finish": finish,
                                "cached": hit is not None})
        return Completion(msg, finish, usage, 0.0, "local", latency, self.model_id)


def local_factory(model: str, *, exp_dir=None, deadline: Optional[float] = None, adapter=None, enable_thinking: bool = False,
                  max_context: Optional[int] = None, device=None, model_label: Optional[str] = None):
    """`inprocess_factory(cfg, budget)` for harness.experiment.run_grid: loads the model ONCE and hands every episode a
    LocalLLM sharing it (replay cache + STOP file in `exp_dir`)."""
    from training.local.models import load_model
    hf_model, tok, info = load_model(model, device=device)
    if adapter:
        from training.local.lora import load_adapter
        load_adapter(hf_model, adapter, merge=True)
    exp_dir = Path(exp_dir) if exp_dir else None
    shared_prefix = {}            # one KV prefix cache for the whole grid: episodes share the system prompt + tool specs

    def factory(cfg, budget):
        llm = LocalLLM(model, budget, tag=cfg.key, model_obj=hf_model, tokenizer=tok, enable_thinking=enable_thinking,
                       max_context=max_context, deadline=deadline, model_name=model_label or cfg.model,
                       cache_path=(exp_dir / "local_cache.jsonl") if exp_dir else None,
                       stop_file=(exp_dir / "STOP") if exp_dir else None, prefix_state=shared_prefix)
        llm.adapter = str(adapter) if adapter else None     # already merged into the shared model; part of the cache key
        return llm
    factory.load_info = info
    factory.adapter = adapter
    return factory
