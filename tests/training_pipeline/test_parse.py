"""Tool-call parsing and chat-template conversion (harness/llm_local.py); no torch needed."""
import json

from harness.llm_local import parse_tool_calls, split_thinking, template_messages


def _args(call):
    return json.loads(call["function"]["arguments"])


def test_qwen_single_call_with_text():
    content, calls = parse_tool_calls('Plan first.\n<tool_call>\n{"name": "view", "arguments": {"layer": 1}}\n</tool_call><|im_end|>')
    assert content == "Plan first."
    assert len(calls) == 1 and calls[0]["function"]["name"] == "view" and _args(calls[0]) == {"layer": 1}
    assert calls[0]["type"] == "function" and calls[0]["id"].startswith("call_")


def test_qwen_parallel_calls_keep_order_and_unique_ids():
    txt = ('<tool_call>{"name": "build", "arguments": {"source": "x"}}</tool_call>\n'
           '<tool_call>{"name": "submit", "arguments": {}}</tool_call>')
    _, calls = parse_tool_calls(txt)
    assert [c["function"]["name"] for c in calls] == ["build", "submit"]
    assert len({c["id"] for c in calls}) == 2


def test_raw_newlines_inside_json_strings_are_accepted():
    src = "from simulation.builder import *\n\ndef build(b):\n\tb.set((1,1,1), wire())\n"
    raw = '<tool_call>\n{"name": "build", "arguments": {"source": "' + src + '"}}\n</tool_call>'
    _, calls = parse_tool_calls(raw)
    assert _args(calls[0])["source"] == src


def test_invalid_json_is_kept_as_raw_call_with_name():
    _, calls = parse_tool_calls('<tool_call>{"name": "build", "arguments": {"source": "unterminated}</tool_call>')
    assert calls[0]["function"]["name"] == "build"
    try:
        json.loads(calls[0]["function"]["arguments"])
        ok = True
    except ValueError:
        ok = False
    assert not ok          # the harness then answers "arguments are not valid JSON"


def test_unterminated_tool_call_cut_by_length():
    _, calls = parse_tool_calls('<tool_call>\n{"name": "submit", "arguments": {}}')
    assert calls and calls[0]["function"]["name"] == "submit"


def test_llama_style_json_and_semicolon_list():
    _, calls = parse_tool_calls('{"name": "view", "parameters": {"layer": 2}}')
    assert calls[0]["function"]["name"] == "view" and _args(calls[0]) == {"layer": 2}
    _, calls = parse_tool_calls('<|python_tag|>{"name": "view", "parameters": {}}; {"name": "submit", "parameters": {}}')
    assert [c["function"]["name"] for c in calls] == ["view", "submit"]


def test_openai_shaped_and_fenced_json():
    _, calls = parse_tool_calls('{"type": "function", "function": {"name": "submit", "arguments": {}}}')
    assert calls[0]["function"]["name"] == "submit"
    content, calls = parse_tool_calls('I will submit.\n```json\n{"name": "submit", "arguments": {}}\n```')
    assert calls[0]["function"]["name"] == "submit" and content == "I will submit."


def test_plain_text_and_python_fence_are_not_calls():
    content, calls = parse_tool_calls("```python\nfrom simulation.builder import *\n```")
    assert calls == [] and content.startswith("```python")
    assert parse_tool_calls("just thinking out loud<|im_end|>") == ("just thinking out loud", [])


def test_split_thinking_variants():
    assert split_thinking("<think>a\nb</think>\n\nanswer") == ("a\nb", "answer")
    assert split_thinking("reasoning started in the prompt</think>answer") == ("reasoning started in the prompt", "answer")
    assert split_thinking("<think>cut off by the limit") == ("cut off by the limit", "")
    assert split_thinking("no thinking") == ("", "no thinking")


def test_template_messages_converts_arguments_and_drops_provider_keys():
    msgs = [{"role": "assistant", "content": None, "reasoning_details": [1],
             "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "build", "arguments": '{"source": "x"}'}},
                            {"id": "c2", "type": "function", "function": {"name": "view", "arguments": "not json"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
    out = template_messages(msgs)
    assert out[0]["content"] == "" and "reasoning_details" not in out[0]
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {"source": "x"}
    assert out[0]["tool_calls"][1]["function"]["arguments"] == "not json"      # invalid JSON stays the model's raw text
    assert out[1] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}
