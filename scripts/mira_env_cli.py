#!/usr/bin/env python3
"""Drive a MIRA build/verify session from the shell (for agents that only have bash). Same operations and state as the MCP server.

    python scripts/mira_env_cli.py tasks [--tier t0]
    python scripts/mira_env_cli.py start t0_not --arm blocks --rounds 3 [--extras probe,components,corpus] [--label NAME]
    python scripts/mira_env_cli.py desc | guide | show
    python scripts/mira_env_cli.py set 1 1 1 minecraft:redstone_wire       # or: set --file blocks.json | --json '[[x,y,z,state],..]'
    python scripts/mira_env_cli.py fill X1 Y1 Z1 X2 Y2 Z2 STATE | remove X1 Y1 Z1 [X2 Y2 Z2]
    python scripts/mira_env_cli.py build --file prog.py                   # builder arm; '-' reads stdin
    python scripts/mira_env_cli.py view [--layer N] [--axis y] [--box x1 y1 z1 x2 y2 z2] [--live]
    python scripts/mira_env_cli.py probe 3,1,2 [--ticks 20] [--inputs A=1,B=0]     # needs --extras probe
    python scripts/mira_env_cli.py run-spec | undo | submit
    python scripts/mira_env_cli.py components QUERY | corpus QUERY [--max-blocks 80] [--program 1] | block ID
    python scripts/mira_env_cli.py sessions | results

The active session is, in order: --session ID, $MIRA_SESSION, data/sessions/.current (written by `start`). Parallel agents should
pass --session or export MIRA_SESSION (printed by `start`) so they do not share the pointer. $MIRA_SESSIONS_DIR relocates the store.
Exit status 0 = the command ran (an 'error: ...' line is a normal tool result); 2 = no/invalid session or usage.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mira_mcp import knowledge, session as S  # noqa: E402


def current_file():
    return S.sessions_dir() / ".current"


def pick(args):
    sid = args.session or os.environ.get("MIRA_SESSION")
    if not sid and current_file().exists():
        sid = current_file().read_text().strip()
    if not sid:
        sys.exit("no active session: run `start TASK` first, or pass --session ID / set MIRA_SESSION")
    try:
        return S.Session.load(sid)
    except S.SessionError as e:
        sys.exit(f"error: {e}")


def read_text(path):
    return sys.stdin.read() if path == "-" else Path(path).read_text()


def parse_blocks_file(text):
    """JSON array, or lines `x y z state` (also `x,y,z,state`); '#' comments."""
    t = text.strip()
    if t.startswith("[") or t.startswith("{"):
        return json.loads(t)
    out = []
    for ln in t.splitlines():
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        parts = ln.replace(",", " ", 3).split(None, 3)
        out.append([int(parts[0]), int(parts[1]), int(parts[2]), parts[3].strip()])
    return out


def rounds_arg(v):
    return None if str(v).lower() in ("inf", "unlimited", "-1") else int(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--session", help="session id (default: $MIRA_SESSION or data/sessions/.current)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tasks", help="list task ids"); p.add_argument("--tier", default="")
    p = sub.add_parser("start", help="begin an attempt")
    p.add_argument("task"); p.add_argument("--arm", default="builder", choices=S.ARMS)
    p.add_argument("--rounds", default="3", help="run_spec budget: N, or 'inf'"); p.add_argument("--extras", default="")
    p.add_argument("--id", default=None); p.add_argument("--label", default=""); p.add_argument("--seed", type=int, default=0)
    for n in ("desc", "guide", "show", "run-spec", "undo", "submit", "sessions", "results"):
        sub.add_parser(n)
    p = sub.add_parser("set"); p.add_argument("xyzs", nargs="*"); p.add_argument("--json"); p.add_argument("--file")
    p = sub.add_parser("fill"); p.add_argument("args", nargs=7)
    p = sub.add_parser("remove"); p.add_argument("coords", nargs="+", type=int)
    p = sub.add_parser("build"); p.add_argument("--file", required=True); p.add_argument("--components", default="")
    p = sub.add_parser("view"); p.add_argument("--layer", type=int); p.add_argument("--axis", default="y")
    p.add_argument("--box", nargs=6, type=int); p.add_argument("--live", action="store_true")
    p = sub.add_parser("probe"); p.add_argument("pos", help="x,y,z or an output port name"); p.add_argument("--ticks", type=int, default=20)
    p.add_argument("--inputs", default="")
    p = sub.add_parser("components"); p.add_argument("query"); p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("corpus"); p.add_argument("query"); p.add_argument("--max-blocks", type=int, default=80)
    p.add_argument("--limit", type=int, default=5); p.add_argument("--program", type=int, default=1)
    p = sub.add_parser("block"); p.add_argument("block_id")
    a = ap.parse_args(argv)

    c = a.cmd
    if c == "tasks":
        print(S.list_tasks(a.tier)); return 0
    if c == "sessions":
        for r in S.list_sessions():
            print(json.dumps(r))
        return 0
    if c == "results":
        f = S.sessions_dir() / "results.jsonl"
        print(f.read_text().rstrip() if f.exists() else "no results yet")
        return 0
    if c == "block":
        print(knowledge.explain_block(a.block_id)); return 0
    if c == "start":
        try:
            s = S.start(a.task, a.arm, rounds_arg(a.rounds), a.extras, a.id, a.seed, a.label)
        except (S.SessionError, ValueError) as e:
            print(f"error: {e}"); return 2
        current_file().write_text(s.id)
        print(f"started session {s.id}\n(parallel agents: export MIRA_SESSION={s.id} or pass --session {s.id})\n")
        print(s.call("task_description"))
        return 0

    s = pick(a)
    kw, tool = {}, c.replace("-", "_")
    if c == "desc":
        tool = "task_description"
    elif c == "set":
        if a.json:
            blocks = json.loads(a.json)
        elif a.file:
            blocks = parse_blocks_file(read_text(a.file))
        elif len(a.xyzs) == 4:
            blocks = [[int(a.xyzs[0]), int(a.xyzs[1]), int(a.xyzs[2]), a.xyzs[3]]]
        else:
            print("usage: set X Y Z STATE | set --file F | set --json '[[x,y,z,state],...]'"); return 2
        tool, kw = "set_blocks", {"blocks": blocks}
    elif c == "fill":
        x1, y1, z1, x2, y2, z2 = (int(v) for v in a.args[:6])
        kw = dict(x1=x1, y1=y1, z1=z1, x2=x2, y2=y2, z2=z2, state=a.args[6])
    elif c == "remove":
        if len(a.coords) not in (3, 6):
            print("usage: remove X1 Y1 Z1 [X2 Y2 Z2]"); return 2
        kw = dict(zip(("x1", "y1", "z1", "x2", "y2", "z2"), a.coords))
    elif c == "build":
        kw = {"source": read_text(a.file), "components": [x for x in a.components.split(",") if x] or None}
    elif c == "view":
        kw = {"layer": a.layer, "axis": a.axis, "box": a.box, "live": a.live}
    elif c == "probe":
        kw = {"pos": a.pos, "ticks": a.ticks, "inputs": a.inputs or None}
    elif c == "components":
        tool, kw = "search_components", {"query": a.query, "limit": a.limit}
    elif c == "corpus":
        tool, kw = "search_corpus", {"query": a.query, "max_blocks": a.max_blocks, "limit": a.limit, "show_program": a.program}
    out = s.call(tool, **kw)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
