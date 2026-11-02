"""End to end over the real MCP stdio protocol: spawn mira_mcp/server.py and call tools through the SDK client."""
import json
import os
import sys

import anyio
import pytest

from conftest import ROOT, hidden_markers
from env.task import load_task
from harness.reference import builder_source
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mira_mcp import session as S

EXPECTED_TOOLS = {"list_tasks", "start_task", "resume_session", "session_status", "task_description", "guide", "set_blocks", "fill", "remove",
                  "build", "undo", "view", "probe", "run_spec", "submit", "search_components", "search_corpus", "explain_block"}


def params(sessions_dir):
    env = {**os.environ, "MIRA_SESSIONS_DIR": str(sessions_dir)}
    return StdioServerParameters(command=sys.executable, args=[str(ROOT / "mira_mcp" / "server.py")], env=env, cwd=str(ROOT))


def run(coro_fn):
    return anyio.run(coro_fn)


async def call(c, name, **args):
    r = await c.call_tool(name, args)
    assert not r.is_error, r
    return "".join(b.text for b in r.content)


def test_tools_listed_with_schemas(sessions_dir):
    async def main():
        async with Client(params(sessions_dir)) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert EXPECTED_TOOLS <= set(tools)
            assert "arm" in tools["start_task"].input_schema["properties"]
            assert set(tools["set_blocks"].input_schema["required"]) == {"blocks"}
            assert all(t.description for t in tools.values())
    run(main)


def test_solve_t0_lamp_and_t0_not_over_mcp(sessions_dir):
    lamp, notg = load_task("t0_lamp"), load_task("t0_not")

    async def main():
        async with Client(params(sessions_dir)) as c:
            ls = await call(c, "list_tasks", tier="t0")
            assert "t0_lamp" in ls and "t0_not" in ls
            # no task yet
            assert "no active task" in await call(c, "set_blocks", blocks=[[1, 1, 1, "minecraft:stone"]])
            # --- t0_lamp, blocks arm
            out = await call(c, "start_task", task_id="t0_lamp", arm="blocks", rounds=3, label="e2e")
            assert out.startswith("started session") and "TASK t0_lamp" in out
            assert "Redstone facts" in await call(c, "guide")
            assert "placed 4" in await call(c, "set_blocks", blocks=[list(b[:4]) for b in lamp.solution])
            assert "--- y=1 ---" in await call(c, "view", layer=1)
            assert "ALL PUBLIC CHECKS PASS" in await call(c, "run_spec")
            sub = await call(c, "submit")
            assert "FULL PASS" in sub and "reward=1.000" in sub
            # --- t0_not, builder arm, in the same server process
            out = await call(c, "start_task", task_id="t0_not", arm="builder", rounds=1)
            assert "TASK t0_not" in out
            assert "build loaded: 6 blocks" in await call(c, "build", source=builder_source(notg.solution))
            assert "ALL PUBLIC CHECKS PASS" in await call(c, "run_spec")
            assert "FULL PASS" in await call(c, "submit")
    run(main)
    rows = [json.loads(ln) for ln in (sessions_dir / "results.jsonl").read_text().splitlines()]
    assert [(r["task"], r["arm"], r["reward"], r["full_pass"]) for r in rows] == [("t0_lamp", "blocks", 1.0, True), ("t0_not", "builder", 1.0, True)]
    assert rows[0]["label"] == "e2e"


def test_round_budget_over_mcp(sessions_dir):
    t = load_task("t0_lamp")

    async def main():
        async with Client(params(sessions_dir)) as c:
            await call(c, "start_task", task_id="t0_lamp", arm="blocks", rounds=1)
            await call(c, "set_blocks", blocks=[list(b[:4]) for b in t.solution])
            assert "[run_spec #1]" in await call(c, "run_spec")
            assert "budget exhausted" in await call(c, "run_spec")
            await call(c, "start_task", task_id="t0_lamp", arm="blocks", rounds=0)
            assert "not available" in await call(c, "run_spec")
            assert "disabled" in await call(c, "probe", pos="Y")
    run(main)


def test_no_hidden_leak_over_mcp(sessions_dir):
    t = load_task("t1_rs_latch")
    marks = hidden_markers(t)
    assert marks

    async def main():
        texts = []
        async with Client(params(sessions_dir)) as c:
            texts.append(await call(c, "start_task", task_id="t1_rs_latch", arm="blocks", rounds=2))
            texts.append(await call(c, "task_description"))
            texts.append(await call(c, "session_status"))
            await call(c, "set_blocks", blocks=[list(b[:4]) for b in t.solution])
            texts.append(await call(c, "run_spec"))
            texts.append(await call(c, "view", layer=1))
            texts.append(await call(c, "submit"))
            tools = (await c.list_tools()).tools
            texts.append(json.dumps([x.model_dump(mode="json") for x in tools]))
        return "\n".join(texts)
    blob = {}

    async def wrapper():
        blob["t"] = await main()
    run(wrapper)
    for m in marks:
        assert m not in blob["t"], m
    assert "details withheld" in blob["t"]


def test_session_started_over_mcp_resumes_in_cli_and_back(sessions_dir):
    t = load_task("t0_not")

    async def first():
        async with Client(params(sessions_dir)) as c:
            out = await call(c, "start_task", task_id="t0_not", arm="blocks", rounds=3, session_id="xproc")
            assert "session xproc" in out
            await call(c, "set_blocks", blocks=[list(b[:4]) for b in t.solution[:3]])
    run(first)
    import subprocess
    env = {**os.environ, "MIRA_SESSIONS_DIR": str(sessions_dir)}
    cli = [sys.executable, str(ROOT / "scripts" / "mira_env_cli.py"), "--session", "xproc"]
    show = subprocess.run(cli + ["show"], capture_output=True, text=True, env=env, cwd="/tmp", timeout=60).stdout
    assert "3 blocks" in show and "arm blocks" in show
    rest = [[*b[:4]] for b in t.solution[3:]]
    assert "placed 3" in subprocess.run(cli + ["set", "--json", json.dumps(rest)], capture_output=True, text=True, env=env, cwd="/tmp", timeout=60).stdout

    async def second():
        async with Client(params(sessions_dir)) as c:
            assert "6 blocks" in await call(c, "resume_session", session_id="xproc")
            assert "ALL PUBLIC CHECKS PASS" in await call(c, "run_spec")
            assert "FULL PASS" in await call(c, "submit")
            assert "error" in await call(c, "resume_session", session_id="ghost")
    run(second)


def test_stateless_lookup_tools_without_a_session(sessions_dir):
    async def main():
        async with Client(params(sessions_dir)) as c:
            assert "delay: 1 | 2 | 3 | 4" in await call(c, "explain_block", block_id="repeater")
            assert "and2" in await call(c, "search_components", query="and gate")
            assert "blocks" in await call(c, "search_corpus", query="not gate", max_blocks=10, show_program=0)
    if not (ROOT / "lib" / "and2.meta.json").exists() or not (ROOT / "data/corpus/index.jsonl").exists():
        pytest.skip("lib/ or corpus index missing")
    run(main)


def test_bad_arguments_return_text_not_crash(sessions_dir):
    async def main():
        async with Client(params(sessions_dir)) as c:
            await call(c, "start_task", task_id="t0_lamp", arm="blocks", rounds=1)
            assert "error" in await call(c, "set_blocks", blocks="[[1,1]]")
            assert "REJECTED" in await call(c, "set_blocks", blocks=[[1, 1, 1, "minecraft:not_a_block"]])
            assert "error" in await call(c, "start_task", task_id="nope")
            assert "error" in await call(c, "start_task", task_id="t0_lamp", arm="weird")
            assert "error" in await call(c, "view", axis="q")
            assert "arm blocks" in await call(c, "session_status")
    run(main)
