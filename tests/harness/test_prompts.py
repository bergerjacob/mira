"""Equalised prompts (round-2 audit M2): the blocks arm gets the same worked examples as the builder arm, as tool calls."""
import re
import sys

import pytest

import simulation.builder as BD
from env import blocks as EB
from harness import prompts

sys.path.insert(0, ".")


def builder_examples():
    t = prompts.BUILDER_SYSTEM_PATH.read_text()
    codes = re.findall(r"```python\n(.*?)```", t[t.index("## Worked examples"):], re.S)
    out = {}
    for c in codes:
        name = c.splitlines()[0].replace("# example:", "").strip()
        out[name] = c
    return out


def blocks_examples():
    """{n: code} for the fenced blocks of prompts.BLOCKS_EXAMPLES, in order."""
    return re.findall(r"```\n(.*?)```", prompts.BLOCKS_EXAMPLES, re.S)


def canon_set(cells):
    return {(x, y, z): EB.canonical(s) if hasattr(EB, "canonical") else s for x, y, z, s in cells}


def run_blocks_example(code):
    cells = {}

    def set_blocks(blocks):
        for x, y, z, s in blocks:
            cells[(x, y, z)] = s

    def fill(x1, y1, z1, x2, y2, z2, state):
        for x in range(min(x1, x2), max(x1, x2) + 1):
            for y in range(min(y1, y2), max(y1, y2) + 1):
                for z in range(min(z1, z2), max(z1, z2) + 1):
                    cells[(x, y, z)] = state

    exec(code, {"set_blocks": set_blocks, "fill": fill})
    return cells


@pytest.mark.parametrize("i,name", [(0, "lamp"), (1, "not_gate"), (2, "and_gate")])
def test_blocks_examples_place_exactly_the_blocks_of_the_builder_examples(i, name):
    b = BD.compile(builder_examples()[name], sandbox=False)
    assert not [e for e in b.errors if getattr(e, "severity", "error") == "error"] if hasattr(b, "errors") else True
    want = {(x, y, z): EB.canonical(s) for x, y, z, s, _ in b.blocks}
    got = {pos: EB.canonical(s) for pos, s in run_blocks_example(blocks_examples()[i]).items()}
    assert got == want, sorted(set(got.items()) ^ set(want.items()))[:6]


def test_three_examples_in_the_same_order_and_with_the_same_titles():
    t = prompts.BUILDER_SYSTEM_PATH.read_text()
    titles_b = re.findall(r"^### \d\. (.*)$", t[t.index("## Worked examples"):], re.M)
    titles_k = re.findall(r"^### \d\. (.*)$", prompts.BLOCKS_EXAMPLES, re.M)
    assert titles_k == titles_b == ["Lever -> wire -> lamp", "NOT gate", "AND gate from three inverters (NOR of NOTs)"]


def test_blocks_system_prompt_contains_the_examples_and_builder_prompt_is_untouched():
    assert prompts.BLOCKS_EXAMPLES in prompts.system_prompt("blocks")
    assert "## Worked examples" in prompts.system_prompt("builder") and "BLOCKS_EXAMPLES" not in prompts.system_prompt("builder")
    assert "set_blocks([[0,1,0," in prompts.system_prompt("blocks")


def test_round_1_blocks_prompt_is_still_selectable_and_is_the_new_one_minus_the_examples():
    old = prompts.system_prompt("blocks", blocks_examples=False)
    new = prompts.system_prompt("blocks")
    assert "Worked examples" not in old and new.replace(prompts.BLOCKS_EXAMPLES, "") == old
    from harness.agent import Episode, RunConfig
    assert RunConfig("t0_not", "m", "blocks", 0, blocks_examples=False).blocks_examples is False
