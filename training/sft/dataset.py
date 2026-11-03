"""SFT dataset: JSONL episodes -> token ids + labels with the loss on assistant tokens only.

Episode format (one JSON object per line; written by training/synth to data/synth/sft/*.jsonl and by
training/sft/build_fixture.py from harness transcripts):
    {"messages": [OpenAI chat: system, user, assistant (content + tool_calls), tool (tool_call_id, content), ...],
     "tools": [OpenAI tool specs], "meta": {"task", "source", "kind", "verified", "reward", ...}}

Rendering uses the model's own chat template with `tools=` (the same `template_messages` conversion the local inference
backend uses, so training text == inference text). The whole conversation is rendered once; assistant spans are found by
the template's turn markers on character offsets and every other token gets label -100. For Qwen3 hybrid-thinking models
the empty `<think>\\n\\n</think>\\n\\n` block that the inference prompt opens with (enable_thinking=False) is inserted into
every assistant turn as prompt (masked), so each trained turn starts exactly where generation starts.
"""
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from harness.llm_local import template_messages

IGNORE = -100
# (turn opener, turn closer) per template family; the closer is trained (the model must learn to stop)
MARKERS = {
    "chatml": ("<|im_start|>assistant\n", "<|im_end|>"),
    "llama3": ("<|start_header_id|>assistant<|end_header_id|>\n\n", "<|eot_id|>"),
    "gemma": ("<start_of_turn>model\n", "<end_of_turn>"),
}
EMPTY_THINK = "<think>\n\n</think>\n\n"


def load_episodes(paths, verified_only: bool = True, min_reward: Optional[float] = None) -> List[dict]:
    """Read JSONL files / globs / dirs. Keeps `meta.verified` episodes (and reward >= min_reward) by default."""
    files = []
    for p in ([paths] if isinstance(paths, (str, Path)) else paths):
        p = Path(p)
        if p.is_dir():
            files += sorted(p.glob("*.jsonl"))
        elif any(ch in str(p) for ch in "*?["):
            files += sorted(Path().glob(str(p)))
        else:
            files.append(p)
    eps = []
    for f in files:
        for i, line in enumerate(f.read_text().splitlines()):
            if not line.strip():
                continue
            ep = json.loads(line)
            meta = ep.setdefault("meta", {})
            meta.setdefault("file", f.name)
            meta.setdefault("line", i)
            if verified_only and not meta.get("verified", False):
                continue
            if min_reward is not None and float(meta.get("reward", 0)) < min_reward:
                continue
            eps.append(ep)
    return eps


def template_family(tokenizer) -> str:
    t = tokenizer.chat_template or ""
    if "<|im_start|>" in t:
        return "chatml"
    if "<|start_header_id|>" in t:
        return "llama3"
    if "<start_of_turn>" in t:
        return "gemma"
    raise ValueError("unknown chat template family (add its markers to training/sft/dataset.MARKERS)")


def generation_prefix(tokenizer, enable_thinking: bool = False) -> str:
    """What the inference prompt appends after the turn opener (Qwen3 hybrid: the empty think block)."""
    msgs = [{"role": "user", "content": "x"}]
    gen = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=enable_thinking)
    opener = MARKERS[template_family(tokenizer)][0]
    i = gen.rfind(opener)
    return gen[i + len(opener):] if i >= 0 else ""


@dataclass
class Example:
    input_ids: List[int]
    labels: List[int]
    meta: dict = field(default_factory=dict)

    @property
    def n_tokens(self):
        return len(self.input_ids)

    @property
    def n_train(self):
        return sum(1 for x in self.labels if x != IGNORE)


def render_episode(tokenizer, episode: dict, enable_thinking: bool = False) -> Example:
    """Tokenise one episode; labels = token ids inside assistant turns (content, tool calls, closer), IGNORE elsewhere."""
    fam = template_family(tokenizer)
    opener, closer = MARKERS[fam]
    text = tokenizer.apply_chat_template(template_messages(episode["messages"]), tools=episode.get("tools") or None,
                                         tokenize=False, add_generation_prompt=False, enable_thinking=enable_thinking)
    gp = generation_prefix(tokenizer, enable_thinking)
    # character spans [a, b) to train on
    spans, out, pos = [], [], 0
    while True:
        i = text.find(opener, pos)
        if i < 0:
            out.append(text[pos:])
            break
        head = i + len(opener)
        out.append(text[pos:head])
        body_start = head
        if gp and not text.startswith(gp, head) and not text.startswith("<think>", head):
            out.append(gp)                       # masked: the inference prompt already contains it
        j = text.find(closer, body_start)
        if j < 0:
            raise ValueError("assistant turn without closer")
        end = j + len(closer)
        base = sum(len(s) for s in out)
        spans.append((base, base + (end - body_start)))
        out.append(text[body_start:end])
        pos = end
    full = "".join(out)
    enc = tokenizer(full, add_special_tokens=False, return_offsets_mapping=True)
    ids, offs = enc["input_ids"], enc["offset_mapping"]
    labels = [IGNORE] * len(ids)
    k = 0
    for t, (a, b) in enumerate(offs):
        while k < len(spans) and spans[k][1] <= a:
            k += 1
        if k < len(spans) and a >= spans[k][0] and b <= spans[k][1] and b > a:
            labels[t] = ids[t]
    return Example(ids, labels, dict(episode.get("meta", {})))


def length_report(examples: List[Example], limits=(4096, 8192, 16384)) -> dict:
    n = len(examples)
    lens = sorted(e.n_tokens for e in examples)
    rep = {"episodes": n, "tokens_p50": lens[n // 2] if n else 0, "tokens_max": lens[-1] if n else 0,
           "tokens_min": lens[0] if n else 0,
           "train_tokens_total": sum(e.n_train for e in examples), "tokens_total": sum(lens)}
    for L in limits:
        rep[f"fit_{L}"] = sum(1 for x in lens if x <= L)
    return rep


def group_of(meta: dict, i: int = 0, key: str = "task") -> str:
    """Split group: the base circuit (`meta.base_task`, set by the synth engine for repair/extend episodes) or the task itself, so
    a repair or extend episode never lands on the other side of the split from the circuit it was derived from."""
    return str(meta.get("base_task") or meta.get(key, i))


def split(examples: List[Example], eval_frac: float = 0.1, seed: int = 0, key: str = "task"):
    """Held-out split by group (`group_of`): all episodes of a base circuit land on the same side."""
    groups = sorted({group_of(e.meta, i, key) for i, e in enumerate(examples)})
    rng = random.Random(seed)
    rng.shuffle(groups)
    n_eval = max(1, int(round(len(groups) * eval_frac))) if eval_frac > 0 and len(groups) > 1 else 0
    held = set(groups[:n_eval])
    tr = [e for i, e in enumerate(examples) if group_of(e.meta, i, key) not in held]
    ev = [e for i, e in enumerate(examples) if group_of(e.meta, i, key) in held]
    return tr, ev


def build(paths, tokenizer, max_len: int = 8192, enable_thinking: bool = False, verified_only: bool = True):
    """(examples within max_len, length report over all rendered episodes)."""
    eps = load_episodes(paths, verified_only=verified_only)
    ex = [render_episode(tokenizer, e, enable_thinking) for e in eps]
    rep = length_report(ex)
    keep = [e for e in ex if e.n_tokens <= max_len and e.n_train > 0]
    rep["kept"] = len(keep)
    rep["max_len"] = max_len
    return keep, rep
