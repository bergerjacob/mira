"""harness/llm.py: budget, ledger concurrency, OpenRouter client behaviour (fake session), scripted backend."""
import json
import subprocess
import sys
import textwrap
import time

import pytest

from harness.llm import (Budget, BudgetExceeded, InsufficientCredits, LLMError, OpenRouterLLM, ScriptedLLM, assistant,
                         load_api_key, price_for, resolve_model, tool_call)

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
SECRET = "sk-or-v1-SECRETSECRETSECRET"


# ------------------------------------------------------------------ Budget
def test_budget_requires_positive_cap(ledger):
    with pytest.raises(ValueError):
        Budget(None, "x", ledger)
    with pytest.raises(ValueError):
        Budget(0, "x", ledger)


def test_budget_reserve_blocks_when_over_cap(ledger):
    b = Budget(0.05, "x", ledger)
    b.reserve(0.03)
    with pytest.raises(BudgetExceeded):
        b.reserve(0.03)
    assert b.spent() == pytest.approx(0.03)


def test_budget_settle_replaces_reservation(ledger):
    b = Budget(0.05, "x", ledger)
    rid = b.reserve(0.04)
    b.settle(rid, 0.01)
    assert b.spent() == pytest.approx(0.01)
    b.reserve(0.04)  # fits again after the settle
    assert b.remaining() == pytest.approx(0.0, abs=1e-9)


def test_budget_persisted_across_instances(ledger):
    Budget(1.0, "x", ledger).charge(0.4)
    assert Budget(1.0, "x", ledger).spent() == pytest.approx(0.4)
    with pytest.raises(BudgetExceeded):
        Budget(0.5, "x", ledger).reserve(0.2)


def test_budget_stale_reservation_expires(ledger):
    b = Budget(0.05, "x", ledger, ttl=0.0)
    b.reserve(0.05)           # crashed process: never settled
    time.sleep(0.01)
    assert b.spent() == 0.0
    b.reserve(0.05)


def test_budget_ledger_cap_spans_experiments(ledger):
    Budget(10, "a", ledger).charge(0.9)
    b = Budget(10, "b", ledger, ledger_cap_usd=1.0)
    assert b.spent() == 0.0
    with pytest.raises(BudgetExceeded):
        b.reserve(0.2)
    b.reserve(0.05)


def test_budget_ignores_garbled_ledger_lines(ledger):
    ledger.write_text('{"broken\n')
    b = Budget(1.0, "x", ledger)
    b.reserve(0.1)
    assert b.spent() == pytest.approx(0.1)


def test_ledger_concurrency_never_exceeds_cap(ledger, tmp_path):
    prog = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(ROOT)!r})
        from harness.llm import Budget, BudgetExceeded
        b = Budget(1.0, "conc", {str(ledger)!r})
        ok = 0
        for _ in range(40):
            try:
                rid = b.reserve(0.01)
            except BudgetExceeded:
                continue
            b.settle(rid, 0.01); ok += 1
        print(ok)
    """)
    procs = [subprocess.Popen([sys.executable, "-c", prog], stdout=subprocess.PIPE, text=True) for _ in range(6)]
    ok = sum(int(p.communicate()[0]) for p in procs)
    assert ok == 100                                  # 240 attempts, exactly cap/0.01 succeed
    assert Budget(1.0, "conc", ledger).spent() == pytest.approx(1.0)
    rows = [json.loads(l) for l in ledger.read_text().splitlines()]    # every line intact JSON
    assert len(rows) == 200


# ------------------------------------------------------------------ ScriptedLLM + budget
def test_scripted_llm_charges_ledger(ledger):
    b = Budget(1.0, "x", ledger)
    llm = ScriptedLLM([assistant("hi")], budget=b, cost_per_call=0.1)
    c = llm.chat([{"role": "user", "content": "q"}])
    assert c.text == "hi" and c.cost == 0.1
    assert b.spent() == pytest.approx(0.1)
    assert llm.n_calls == 1 and llm.total_cost == pytest.approx(0.1)


def test_budget_exceeded_raised_before_the_call(ledger):
    b = Budget(0.15, "x", ledger)
    llm = ScriptedLLM([assistant("a"), assistant("b")], budget=b, cost_per_call=0.1)
    llm.chat([{"role": "user", "content": "q"}])
    with pytest.raises(BudgetExceeded):
        llm.chat([{"role": "user", "content": "q"}])
    assert len(llm.calls) == 1                      # backend never reached
    assert b.spent() == pytest.approx(0.1)


def test_failed_call_releases_reservation(ledger):
    b = Budget(1.0, "x", ledger)

    def boom(messages, tools, n):
        raise LLMError("boom")
    llm = ScriptedLLM(boom, budget=b, cost_per_call=0.5)
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "q"}])
    assert b.spent() == 0.0


def test_scripted_records_tools_and_seed():
    llm = ScriptedLLM([assistant("x", [("view", {"layer": 1})])])
    tools = [{"type": "function", "function": {"name": "view"}}]
    c = llm.chat([{"role": "user", "content": "q"}], tools, seed=7)
    assert llm.calls[0]["tools"] == ["view"] and llm.calls[0]["seed"] == 7
    assert c.tool_calls[0]["function"]["name"] == "view" and json.loads(c.tool_calls[0]["function"]["arguments"]) == {"layer": 1}
    assert c.finish_reason == "tool_calls"


def test_scripted_callable_script():
    llm = ScriptedLLM(lambda m, t, n: assistant(f"call {n}"))
    assert [llm.chat([{"role": "user", "content": "q"}]).text for _ in range(2)] == ["call 0", "call 1"]


# ------------------------------------------------------------------ OpenRouterLLM with a fake session
class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


OK_BODY = {"model": "deepseek/deepseek-v4-flash", "choices": [{"finish_reason": "tool_calls", "message": {
    "role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "view", "arguments": "{}"}}]}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.000123,
              "completion_tokens_details": {"reasoning_tokens": 50}, "prompt_tokens_details": {"cached_tokens": 10}}}


class FakeSession:
    def __init__(self, responses):
        self.responses, self.posts = list(responses), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(responses, **kw):
    sleeps = []
    llm = OpenRouterLLM("deepseek-v4-flash", api_key=SECRET, session=FakeSession(responses), sleep=sleeps.append, **kw)
    return llm, sleeps


def test_openrouter_payload_and_usage_parsing():
    llm, _ = make([Resp(200, OK_BODY)])
    tools = [{"type": "function", "function": {"name": "view", "parameters": {}}}]
    c = llm.chat([{"role": "user", "content": "hi"}], tools, max_tokens=500, temperature=0.3, seed=11, reasoning="low")
    sent = llm.session.posts[0]
    p = sent["json"]
    assert p["model"] == "deepseek/deepseek-v4-flash"            # short name resolved via OpenRouterClient.MODELS
    assert p["usage"] == {"include": True} and p["max_tokens"] == 500 and p["seed"] == 11
    assert p["tools"] == tools and p["tool_choice"] == "auto" and p["provider"] == {"require_parameters": True}
    assert p["reasoning"] == {"effort": "low"}
    assert SECRET not in json.dumps(p) and sent["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert c.cost == 0.000123 and c.cost_source == "api"
    assert c.usage == {"prompt_tokens": 1000, "completion_tokens": 200, "reasoning_tokens": 50, "cached_tokens": 10}
    assert c.tool_calls[0]["function"]["name"] == "view" and c.text == ""


def test_openrouter_cost_falls_back_to_price_table():
    body = json.loads(json.dumps(OK_BODY))
    del body["usage"]["cost"]
    llm, _ = make([Resp(200, body)])
    c = llm.chat([{"role": "user", "content": "hi"}])
    pin, pout = price_for("deepseek/deepseek-v4-flash")
    assert c.cost_source == "estimate" and c.cost == pytest.approx((1000 * pin + 200 * pout) / 1e6)


def test_openrouter_retries_429_then_succeeds():
    llm, sleeps = make([Resp(429, {"error": {"message": "slow down"}}, {"Retry-After": "3"}), Resp(503, {"error": "x"}), Resp(200, OK_BODY)])
    c = llm.chat([{"role": "user", "content": "hi"}])
    assert c.cost == 0.000123 and len(llm.session.posts) == 3
    assert sleeps[0] == 3.0 and len(sleeps) == 2


def test_openrouter_retries_connection_errors_and_empty_200():
    llm, sleeps = make([ConnectionError("reset"), Resp(200, {"error": {"message": "upstream"}}), Resp(200, OK_BODY)])
    assert llm.chat([{"role": "user", "content": "hi"}]).cost > 0
    assert len(sleeps) == 2


def test_openrouter_gives_up_after_max_retries_without_leaking_key():
    llm, sleeps = make([Resp(500, {"error": {"message": "dead"}})] * 3, max_retries=2)
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "hi"}])
    assert SECRET not in str(e.value) and "3 attempts" in str(e.value) and len(sleeps) == 2


def test_openrouter_402_is_insufficient_credits_no_retry():
    llm, sleeps = make([Resp(402, {"error": {"message": "credits"}})])
    with pytest.raises(InsufficientCredits):
        llm.chat([{"role": "user", "content": "hi"}])
    assert sleeps == [] and len(llm.session.posts) == 1


def test_openrouter_400_fails_fast_with_message():
    llm, _ = make([Resp(400, {"error": {"message": "bad tool schema"}})])
    with pytest.raises(LLMError, match="bad tool schema"):
        llm.chat([{"role": "user", "content": "hi"}])
    assert len(llm.session.posts) == 1


def test_openrouter_goes_through_budget(ledger):
    b = Budget(1.0, "x", ledger)
    llm = OpenRouterLLM("deepseek-v4-flash", b, api_key=SECRET, session=FakeSession([Resp(200, OK_BODY)]), sleep=lambda s: None)
    llm.chat([{"role": "user", "content": "hi"}])
    assert b.spent() == pytest.approx(0.000123)
    rows = [json.loads(l) for l in ledger.read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["reserve", "settle"] and SECRET not in ledger.read_text()


def test_openrouter_budget_stops_before_network(ledger):
    b = Budget(0.0000001, "x", ledger)
    sess = FakeSession([Resp(200, OK_BODY)])
    llm = OpenRouterLLM("deepseek-v4-flash", b, api_key=SECRET, session=sess, sleep=lambda s: None)
    with pytest.raises(BudgetExceeded):
        llm.chat([{"role": "user", "content": "hi"}], max_tokens=1000)
    assert sess.posts == []


# ------------------------------------------------------------------ keys / names
def test_load_api_key_env_then_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text("export DISCORD_TOKEN=abc\nexport OPENROUTER_API_KEY='sk-xyz'\n")
    assert load_api_key(f) == "sk-xyz"
    f.write_text("OPENROUTER_API_KEY=sk-plain\n")
    assert load_api_key(f) == "sk-plain"
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert load_api_key(f) == "from-env"


def test_load_api_key_missing_raises_without_leaking(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text("export DISCORD_TOKEN=topsecret\n")
    with pytest.raises(RuntimeError) as e:
        load_api_key(f)
    assert "topsecret" not in str(e.value)


def test_resolve_model_short_and_full():
    assert resolve_model("deepseek-v4-flash") == "deepseek/deepseek-v4-flash"
    assert resolve_model("vendor/custom-model") == "vendor/custom-model"
    assert price_for("vendor/custom-model") == (3.0, 15.0)


def test_reasoning_details_only_echoed_when_opted_in():
    body = json.loads(json.dumps(OK_BODY))
    body["choices"][0]["message"]["reasoning_details"] = [{"type": "reasoning.text", "text": "hmm"}]
    off, _ = make([Resp(200, body)])
    assert "reasoning_details" not in off.chat([{"role": "user", "content": "hi"}]).message
    on, _ = make([Resp(200, body)], keep_reasoning=True)
    assert on.chat([{"role": "user", "content": "hi"}]).message["reasoning_details"][0]["text"] == "hmm"
