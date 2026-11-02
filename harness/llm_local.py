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
    dicts with tojson; a JSON string would be double-encoded by some), provider-only keys dropped."""
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
                        args = _loads(args)
                    except ValueError:
                        args = {"_raw": args}
                    if not isinstance(args, dict):
                        args = {"_raw": args}
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
                 model_name: Optional[str] = None, top_p: Optional[float] = None, top_k: Optional[int] = None):
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
        dev = next(self.hf_model.parameters()).device
        inp = torch.tensor([prompt_ids], device=dev)
        if seed is not None:
            torch.manual_seed(int(seed))
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(int(seed))
        kw = dict(max_new_tokens=max_new, eos_token_id=self._eos, pad_token_id=self.tokenizer.pad_token_id,
                  attention_mask=torch.ones_like(inp), prefill_chunk_size=1024)
        if temperature and temperature > 0:
            kw.update(do_sample=True, temperature=float(temperature))
            if self.top_p is not None:
                kw["top_p"] = float(self.top_p)
            if self.top_k is not None:
                kw["top_k"] = int(self.top_k)
        else:
            kw.update(do_sample=False, temperature=None, top_p=None, top_k=None)
        if max_time is not None:
            kw["max_time"] = max(1.0, float(max_time))
        with torch.no_grad():
            out = self.hf_model.generate(inp, **kw)
        comp = out[0, len(prompt_ids):].tolist()
        if comp and comp[-1] in self._eos:
            return comp, "eos"
        if len(comp) >= max_new:
            return comp, "length"
        return comp, ("time" if max_time is not None else "eos")

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
                if left < 20:
                    self._hit_deadline("no time left for another generation")
                max_time = left
            comp_ids, stopped = self.generate_ids(prompt_ids, max_new, temperature, seed, max_time=max_time)
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

    def factory(cfg, budget):
        llm = LocalLLM(model, budget, tag=cfg.key, model_obj=hf_model, tokenizer=tok, enable_thinking=enable_thinking,
                       max_context=max_context, deadline=deadline, model_name=model_label or cfg.model,
                       cache_path=(exp_dir / "local_cache.jsonl") if exp_dir else None,
                       stop_file=(exp_dir / "STOP") if exp_dir else None)
        llm.adapter = str(adapter) if adapter else None     # already merged into the shared model; part of the cache key
        return llm
    factory.load_info = info
    factory.adapter = adapter
    return factory
