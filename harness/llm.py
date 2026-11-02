"""Chat-completions client for OpenRouter with native tool calling, cost accounting and a hard shared budget.

    budget = Budget(cap_usd=2.50, exp_id="pilot")                   # shared ledger: data/experiments/ledger.jsonl
    llm = OpenRouterLLM("deepseek-v4-flash", budget=budget)
    comp = llm.chat(messages, tools=openai_tools, max_tokens=4096)  # Completion(message, usage, cost, ...)

Every LLM backend (`OpenRouterLLM`, `ScriptedLLM`) goes through `BaseLLM.chat`, which reserves the worst-case
cost of the call in the ledger *before* the request (raising `BudgetExceeded` if that would pass the cap) and
settles the actual cost afterwards. The ledger is an append-only JSONL file guarded by `flock`, so parallel
processes share one budget safely. The API key is only ever read from the environment / `.env` and is never
logged or put into exception text.
"""
import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - non-posix
    fcntl = None

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = ROOT / "data" / "experiments"
DEFAULT_LEDGER = EXPERIMENTS_DIR / "ledger.jsonl"
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# USD per million tokens (input, output), from the OpenRouterClient.MODELS comments (July 2026). Only used for the
# pre-call reservation and as a fallback when the response carries no usage.cost.
PRICES = {
    "deepseek/deepseek-v4-flash": (0.09, 0.19), "google/gemma-4-31b-it": (0.12, 0.37),
    "google/gemma-4-26b-a4b-it": (0.07, 0.34), "minimax/minimax-m3": (0.30, 1.20),
    "qwen/qwen3.7-plus": (0.32, 1.28), "deepseek/deepseek-v4-pro": (0.44, 0.87), "qwen/qwen3.6-plus": (0.33, 1.95),
    "moonshotai/kimi-k2.6": (0.68, 3.42), "z-ai/glm-5.2": (0.94, 2.95), "google/gemini-3.5-flash": (1.50, 9.00),
    "anthropic/claude-sonnet-5": (2.00, 10.00), "qwen/qwen3.7-max": (1.48, 4.43),
    "google/gemini-3.1-flash-lite": (0.25, 1.50), "qwen/qwen3-coder-30b-a3b-instruct": (0.07, 0.27),
    "meta-llama/llama-3.3-70b-instruct": (0.13, 0.40), "z-ai/glm-5": (0.95, 3.15), "moonshotai/kimi-k3": (3.00, 15.00),
    "moonshotai/kimi-k2.5": (0.57, 2.85), "anthropic/claude-sonnet-4.6": (3.00, 15.00), "openai/gpt-4o": (2.50, 10.00),
}
DEFAULT_PRICE = (3.00, 15.00)  # unknown model: assume expensive so the reservation is conservative
CHARS_PER_TOKEN = 3.0          # pessimistic (real text is ~3.5-4 chars/token) for the pre-call prompt estimate


EPS = 1e-9  # float slack so 100 x $0.01 fits a $1.00 cap


class BudgetExceeded(Exception):
    """The next call could push spend over the cap; nothing was sent."""


class LLMError(RuntimeError):
    """API failure after retries (message never contains the key)."""


class InsufficientCredits(LLMError):
    """HTTP 402: the OpenRouter account is out of credit. Treated like a budget stop by the runner."""


def resolve_model(name: str) -> str:
    """Short name from OpenRouterClient.MODELS -> OpenRouter id; full ids pass through."""
    from simulation.llm_client import OpenRouterClient
    return OpenRouterClient.MODELS.get(name, name)


def price_for(model_id: str):
    return PRICES.get(model_id, DEFAULT_PRICE)


def load_api_key(env_file: Optional[Path] = None) -> str:
    """OPENROUTER_API_KEY from the environment, else from `.env` (``export KEY=value`` or ``KEY=value`` lines).
    Raises RuntimeError without echoing anything secret."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    p = Path(env_file) if env_file else ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*(.*?)\s*$", line)
            if m:
                v = m.group(1).strip().strip("'\"")
                if v:
                    return v
    raise RuntimeError("OPENROUTER_API_KEY is not set (environment or .env)")


# --------------------------------------------------------------------------- budget
class Budget:
    """Hard spend cap shared by all processes through an append-only JSONL ledger.

    Lines: {"t","exp","kind":"reserve"|"settle","id","usd",...}. A call's *reserve* line (its worst-case cost) counts
    until the matching *settle* line (actual cost) arrives; reserves older than `ttl` seconds with no settle (crashed
    process) stop counting. `cap_usd` limits this experiment (`exp_id`); `ledger_cap_usd` (optional) limits the sum
    over every experiment in the ledger file."""

    def __init__(self, cap_usd: float, exp_id: str, ledger_path=DEFAULT_LEDGER, ledger_cap_usd: Optional[float] = None,
                 ttl: float = 900.0):
        if cap_usd is None or cap_usd <= 0:
            raise ValueError("cap_usd must be a positive number")
        self.cap_usd, self.exp_id = float(cap_usd), exp_id
        self.ledger_cap_usd = ledger_cap_usd
        self.path = Path(ledger_path)
        self.ttl = ttl
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- ledger access (callers hold the lock when they need atomicity)
    def _read(self, fh):
        fh.seek(0)
        rows = []
        for line in fh.read().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # torn/garbled line: ignore, never crash the run
        return rows

    def _totals(self, rows, now=None):
        now = now or time.time()
        settled = {r["id"]: r for r in rows if r.get("kind") == "settle"}
        reserved = {r["id"] for r in rows if r.get("kind") == "reserve"}
        per_exp = {}
        for r in rows:
            if r.get("kind") == "reserve":
                s = settled.get(r["id"])
                if s is not None:
                    usd = s["usd"]
                elif now - r.get("t", 0) <= self.ttl:
                    usd = r["usd"]
                else:
                    usd = 0.0
                per_exp[r["exp"]] = per_exp.get(r["exp"], 0.0) + usd
            elif r.get("kind") == "settle" and r["id"] not in reserved:
                per_exp[r["exp"]] = per_exp.get(r["exp"], 0.0) + r["usd"]  # settle with no reserve line (manual add)
        return per_exp

    def _append(self, fh, row):
        os.write(fh.fileno(), (json.dumps(row, separators=(",", ":")) + "\n").encode())

    def _locked(self):
        fh = open(self.path, "a+")
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        return fh

    # -- public API
    def spent(self) -> float:
        """Settled + outstanding-reserved USD of this experiment."""
        fh = self._locked()
        try:
            return self._totals(self._read(fh)).get(self.exp_id, 0.0)
        finally:
            fh.close()

    def spent_total(self) -> float:
        fh = self._locked()
        try:
            return sum(self._totals(self._read(fh)).values())
        finally:
            fh.close()

    def remaining(self) -> float:
        fh = self._locked()
        try:
            tot = self._totals(self._read(fh))
        finally:
            fh.close()
        rem = self.cap_usd - tot.get(self.exp_id, 0.0)
        if self.ledger_cap_usd is not None:
            rem = min(rem, self.ledger_cap_usd - sum(tot.values()))
        return rem

    def reserve(self, usd: float, **meta) -> str:
        """Atomically check `spent + usd <= cap` and record the reservation; returns its id."""
        fh = self._locked()
        try:
            tot = self._totals(self._read(fh))
            mine, allx = tot.get(self.exp_id, 0.0), sum(tot.values())
            if mine + usd > self.cap_usd + EPS:
                raise BudgetExceeded(f"experiment {self.exp_id}: spent ${mine:.4f} + reserve ${usd:.4f} > cap ${self.cap_usd:.2f}")
            if self.ledger_cap_usd is not None and allx + usd > self.ledger_cap_usd + EPS:
                raise BudgetExceeded(f"ledger total ${allx:.4f} + reserve ${usd:.4f} > ledger cap ${self.ledger_cap_usd:.2f}")
            rid = uuid.uuid4().hex[:16]
            self._append(fh, {"t": round(time.time(), 3), "exp": self.exp_id, "kind": "reserve", "id": rid,
                              "usd": round(usd, 6), **meta})
            return rid
        finally:
            fh.close()

    def settle(self, rid: str, usd: float, **meta):
        fh = self._locked()
        try:
            self._append(fh, {"t": round(time.time(), 3), "exp": self.exp_id, "kind": "settle", "id": rid,
                              "usd": round(float(usd), 6), **meta})
        finally:
            fh.close()

    def charge(self, usd: float, **meta):
        """Record spend that has no preceding reservation (manual top-up of the ledger)."""
        self.settle(uuid.uuid4().hex[:16], usd, **meta)


# --------------------------------------------------------------------------- completions
@dataclass
class Completion:
    message: dict                     # {"role": "assistant", "content": str, "tool_calls": [...]}
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)   # prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens
    cost: float = 0.0
    cost_source: str = "api"          # "api" (usage.cost), "estimate" (price table), "mock"
    latency: float = 0.0
    model: str = ""

    @property
    def tool_calls(self):
        return self.message.get("tool_calls") or []

    @property
    def text(self):
        return self.message.get("content") or ""


def normalise_message(msg: dict) -> dict:
    """Assistant message as we send it back: role/content/tool_calls only, every call has an id."""
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("reasoning_details"):
        out["reasoning_details"] = msg["reasoning_details"]   # only present when the client opted in (keep_reasoning)
    calls = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function", {})
        args = fn.get("arguments", "{}")
        if not isinstance(args, str):
            args = json.dumps(args)
        calls.append({"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}_{i}", "type": "function",
                      "function": {"name": fn.get("name", ""), "arguments": args}})
    if calls:
        out["tool_calls"] = calls
    return out


def estimate_tokens(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"), default=str)
    return int(len(s) / CHARS_PER_TOKEN) + 1


class BaseLLM:
    """chat() = reserve -> _complete -> settle. Subclasses implement `_complete`."""

    def __init__(self, model: str, budget: Optional[Budget] = None, tag: str = ""):
        self.model = model
        self.model_id = resolve_model(model)
        self.budget = budget
        self.tag = tag
        self.total_cost = 0.0
        self.total_prompt = 0
        self.total_completion = 0
        self.n_calls = 0

    def worst_case_cost(self, messages, tools, max_tokens: int) -> float:
        pin, pout = price_for(self.model_id)
        return (estimate_tokens(messages) + estimate_tokens(tools or [])) * pin / 1e6 + max_tokens * pout / 1e6

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None, *, max_tokens: int = 4096,
             temperature: float = 0.7, seed: Optional[int] = None, reasoning: Optional[str] = None) -> Completion:
        rid = None
        if self.budget is not None:
            rid = self.budget.reserve(self.worst_case_cost(messages, tools, max_tokens), model=self.model_id, tag=self.tag)
        try:
            comp = self._complete(messages, tools, max_tokens, temperature, seed, reasoning)
        except BaseException:
            if rid is not None:
                self.budget.settle(rid, 0.0, note="call failed")
            raise
        if rid is not None:
            self.budget.settle(rid, comp.cost, model=self.model_id, tag=self.tag, cost_source=comp.cost_source,
                               prompt_tokens=comp.usage.get("prompt_tokens", 0),
                               completion_tokens=comp.usage.get("completion_tokens", 0))
        self.n_calls += 1
        self.total_cost += comp.cost
        self.total_prompt += comp.usage.get("prompt_tokens", 0)
        self.total_completion += comp.usage.get("completion_tokens", 0)
        return comp

    def _complete(self, messages, tools, max_tokens, temperature, seed, reasoning) -> Completion:  # pragma: no cover
        raise NotImplementedError


RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


class OpenRouterLLM(BaseLLM):
    """OpenAI-compatible /chat/completions on OpenRouter with usage accounting (`usage: {include: true}`).

    `require_params=True` sends provider.require_parameters so a model/provider that cannot do tool calling fails fast
    (HTTP 404, free) instead of silently ignoring `tools`. `keep_reasoning=True` echoes the provider's `reasoning_details`
    back in later turns (needed by some thinking models for multi-turn tool use; larger prompts). `session` and `sleep`
    are injectable for tests."""

    def __init__(self, model: str, budget: Optional[Budget] = None, *, api_key: Optional[str] = None, tag: str = "",
                 timeout: float = 180.0, max_retries: int = 5, backoff: float = 2.0, require_params: bool = True,
                 keep_reasoning: bool = False, session=None, sleep: Callable[[float], None] = time.sleep, base_url: str = BASE_URL):
        super().__init__(model, budget, tag)
        self._key = api_key or load_api_key()
        self.timeout, self.max_retries, self.backoff = timeout, max_retries, backoff
        self.require_params = require_params
        self.keep_reasoning = keep_reasoning
        self.sleep = sleep
        self.base_url = base_url
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def payload(self, messages, tools, max_tokens, temperature, seed, reasoning) -> dict:
        p = {"model": self.model_id, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
             "usage": {"include": True}}
        if tools:
            p["tools"] = tools
            p["tool_choice"] = "auto"
            if self.require_params:
                p["provider"] = {"require_parameters": True}
        if seed is not None:
            p["seed"] = int(seed)
        if reasoning:
            p["reasoning"] = {"effort": reasoning}
        return p

    def _complete(self, messages, tools, max_tokens, temperature, seed, reasoning) -> Completion:
        body = self.payload(messages, tools, max_tokens, temperature, seed, reasoning)
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   "X-Title": "MIRA harness"}
        last = "no attempt"
        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            delay = None
            try:
                r = self.session.post(self.base_url, headers=headers, json=body, timeout=self.timeout)
            except Exception as e:  # connection reset, timeout, DNS ...
                last = f"{type(e).__name__}"
                delay = self._delay(attempt, None)
            else:
                status = r.status_code
                if status == 200:
                    try:
                        data = r.json()
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and data.get("choices"):
                        return self._parse(data, time.time() - t0)
                    err = (data or {}).get("error") if isinstance(data, dict) else None
                    last = f"200 without choices ({str(err)[:200] if err else 'empty body'})"
                    delay = self._delay(attempt, None)
                elif status == 402:
                    raise InsufficientCredits("OpenRouter returned 402 (insufficient credits)")
                elif status in RETRY_STATUS:
                    last = f"HTTP {status} {self._err_text(r)}"
                    delay = self._delay(attempt, r.headers.get("Retry-After"))
                else:
                    raise LLMError(f"HTTP {status}: {self._err_text(r)}")
            if attempt >= self.max_retries:
                break
            self.sleep(delay)
        raise LLMError(f"giving up after {self.max_retries + 1} attempts: {last}")

    def _delay(self, attempt, retry_after):
        try:
            if retry_after is not None:
                return min(60.0, float(retry_after))
        except (TypeError, ValueError):
            pass
        return min(60.0, self.backoff * (2 ** attempt)) * (0.75 + 0.5 * random.random())

    @staticmethod
    def _err_text(r):
        try:
            d = r.json()
            e = d.get("error", d)
            return (e.get("message") if isinstance(e, dict) else str(e))[:300]
        except Exception:
            return (getattr(r, "text", "") or "")[:200]

    def _parse(self, data: dict, latency: float) -> Completion:
        ch = data["choices"][0]
        raw = dict(ch.get("message") or {})
        if not self.keep_reasoning:
            raw.pop("reasoning_details", None)
        msg = normalise_message(raw)
        u = data.get("usage") or {}
        pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        usage = {"prompt_tokens": pt, "completion_tokens": ct,
                 "reasoning_tokens": int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                 "cached_tokens": int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)}
        if u.get("cost") is not None:
            cost, src = float(u["cost"]), "api"
        else:
            pin, pout = price_for(self.model_id)
            cost, src = (pt * pin + ct * pout) / 1e6, "estimate"
        return Completion(msg, ch.get("finish_reason") or "stop", usage, cost, src, latency, data.get("model", self.model_id))


# --------------------------------------------------------------------------- scripted backend
def tool_call(name: str, args: Optional[dict] = None, call_id: Optional[str] = None) -> dict:
    return {"id": call_id or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {})}}


def assistant(text: str = "", calls: Optional[list] = None) -> dict:
    """Scripted assistant message; `calls` = [(name, args), ...] or ready tool_call dicts."""
    m = {"role": "assistant", "content": text}
    if calls:
        m["tool_calls"] = [c if isinstance(c, dict) else tool_call(*c) for c in calls]
    return m


class ScriptedLLM(BaseLLM):
    """Offline backend with the OpenRouterLLM interface.

    `script` is a list of assistant messages (see `assistant()`) consumed one per call, or a callable
    `(messages, tools, n_calls) -> message`. Usage is synthetic (chars/4); cost is `cost_per_call` if given, else
    from `price` (in, out per M tokens). Every call is recorded in `self.calls` for assertions."""

    def __init__(self, script, model: str = "mock", budget: Optional[Budget] = None, *, cost_per_call: Optional[float] = None,
                 price=(1.0, 2.0), tag: str = "", finish_reason: Optional[str] = None):
        super().__init__(model, budget, tag)
        self.script = script
        self.cost_per_call = cost_per_call
        self.price = price
        self.finish_reason = finish_reason
        self.calls = []

    def worst_case_cost(self, messages, tools, max_tokens):
        if self.cost_per_call is not None:
            return self.cost_per_call
        return (estimate_tokens(messages) + estimate_tokens(tools or [])) * self.price[0] / 1e6 + max_tokens * self.price[1] / 1e6

    def _complete(self, messages, tools, max_tokens, temperature, seed, reasoning) -> Completion:
        n = len(self.calls)
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": [t["function"]["name"] for t in (tools or [])],
                           "tool_specs": tools, "max_tokens": max_tokens, "seed": seed})
        if callable(self.script):
            raw = self.script(messages, tools, n)
        elif n < len(self.script):
            raw = self.script[n]
        else:
            raw = assistant("(script exhausted)")
        msg = normalise_message(raw)
        pt = sum(len(json.dumps(m)) for m in messages) // 4 + 1
        ct = len(json.dumps(msg)) // 4 + 1
        cost = self.cost_per_call if self.cost_per_call is not None else (pt * self.price[0] + ct * self.price[1]) / 1e6
        fr = self.finish_reason or ("tool_calls" if msg.get("tool_calls") else "stop")
        return Completion(msg, fr, {"prompt_tokens": pt, "completion_tokens": ct, "reasoning_tokens": 0, "cached_tokens": 0},
                          cost, "mock", 0.0, self.model_id)
