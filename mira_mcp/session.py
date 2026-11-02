"""A stateful, disk-persisted build/verify session for external agents (MCP server, CLI, scripts).

    s = start("t0_not", arm="blocks", rounds=3)            # -> Session (state in data/sessions/<id>/)
    print(s.call("set_blocks", blocks=[[1, 1, 1, "minecraft:redstone_wire"]]))
    print(s.call("run_spec")); print(s.call("submit"))
    s = Session.load(s.id)                                  # any later process resumes from disk

Every call loads `state.json`, runs one tool against an `env.tools.Env` rebuilt from it, saves, and appends one line to
`transcript.jsonl`, all under a per-session file lock, so the MCP server and the CLI can share a session and nothing lives
only in memory. The contract itself (hidden vectors, evaluation seed, reference solution) is never written to the session
directory: `state.json` stores the task *reference* (id or path) and the agent's own build.

Arms (same as harness/agent.py): `blocks` = set_blocks / fill / remove, `builder` = build(source) (sandboxed
simulation.builder.compile; the program REPLACES the current build). `undo`, `view`, `run_spec`, `submit` exist in both.
Feedback rounds R = number of `run_spec` calls (None = unlimited). Simulator feedback outside run_spec (`probe`, `view live`)
and the research tools (`search_components`, `search_corpus`) are off unless the session was started with `extras`.
"""
import fcntl
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from env import blocks as EB  # noqa: E402
from env.runner import prepare  # noqa: E402
from env.task import Task, TASK_DIR, reference_stats, task_from_dict  # noqa: E402
from env.tools import Env  # noqa: E402

ARMS = ("blocks", "builder")
EXTRAS = ("probe", "components", "corpus")
ENV_TOOLS_BLOCKS = ("set_blocks", "fill", "remove")
MAX_COMPILE_BLOCKS = 50_000
UNDO_DEPTH = 40
LOG_RESULT_CHARS = 60_000


def sessions_dir() -> Path:
    p = Path(os.environ.get("MIRA_SESSIONS_DIR") or ROOT / "data" / "sessions")
    p.mkdir(parents=True, exist_ok=True)
    return p


def max_chars() -> int:
    try:
        return max(500, int(os.environ.get("MIRA_MAX_CHARS", "12000")))
    except ValueError:
        return 12000


def clip(text: str, n: Optional[int] = None) -> str:
    """Truncate to n chars with an explicit notice (so the model knows output is missing)."""
    n = n or max_chars()
    if len(text) <= n:
        return text
    return text[:n] + f"\n... [truncated {len(text) - n} of {len(text)} chars; narrow the request (smaller view box / one layer)]"


# --------------------------------------------------------------------------- tasks
def task_files():
    """{task id: path} for env/tasks/*.json and env/tasks/t3/*.json."""
    out = {}
    for d in (TASK_DIR, TASK_DIR / "t3"):
        for p in sorted(d.glob("*.json")):
            if p.stem.endswith("_report") or p.stem in ("compile_track_report", "oracle_report"):
                continue
            out[p.stem] = p
    return out


def load_task_ref(ref: str) -> Task:
    """`ref` = task id (env/tasks/<id>.json, env/tasks/t3/<id>.json) or a path to a task JSON ({"contract", "solution"?}) or a bare
    contract JSON (contract fields at top level; no reference solution, R4 then uses the build's own size)."""
    try:
        import env.library  # noqa: F401  registers the reference models of the T3 tasks (adder_cin, decoder, ...)
    except Exception:       # lib/ is written by another agent and may be incomplete: T0-T2 tasks do not need it
        pass
    files = task_files()
    p = files.get(ref)
    if p is None:
        p = Path(ref).expanduser()
        if not p.is_absolute():
            p = ROOT / p if (ROOT / p).exists() else p
        if not p.exists():
            raise ValueError(f"unknown task {ref!r}; known ids: {', '.join(sorted(files))} (or give a path to a task/contract JSON)")
    d = json.loads(p.read_text())
    if "contract" not in d:
        d = {"contract": d, "solution": []}
    t = task_from_dict(d, p)
    if not t.solution and not t.contract.reference:
        t.contract.reference = reference_stats([])
    return t


def list_tasks(tier: Optional[str] = None) -> str:
    rows = []
    for tid, p in task_files().items():
        try:
            c = json.loads(p.read_text())["contract"]
        except (OSError, ValueError, KeyError):
            continue
        if tier and tier.lower() not in ("", "all") and not str(c.get("tier", "")).lower().startswith(tier.lower()):
            continue
        ports = c.get("ports", [])
        ins = sum(1 for q in ports if q.get("kind") in ("click", "pad"))
        rows.append((tid, c.get("tier", "?"), c.get("title", ""), ins, len(ports) - ins, (c.get("reference") or {}).get("blocks"),
                     (c.get("budgets") or {}).get("max_blocks")))
    if not rows:
        return f"no tasks for tier {tier!r}"
    lines = [f"{'id':<22} {'tier':<4} {'in':>2} {'out':>3} {'ref':>5} {'max':>5}  title"]
    for tid, t, title, i, o, rb, mb in rows:
        lines.append(f"{tid:<22} {t:<4} {i:>2} {o:>3} {rb if rb is not None else '?':>5} {mb if mb is not None else '?':>5}  {title}")
    lines.append(f"{len(rows)} tasks. start_task(task_id, arm, rounds) begins one; the reference size ('ref') is the block count R4 compares against.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- prompts
def guide_text(arm: str) -> str:
    """Physics facts (+ for the builder arm the Part API reference): the same text the OpenRouter harness puts in its system prompt."""
    from harness import prompts
    if arm == "builder":
        return prompts.BUILDER_SYSTEM_PATH.read_text() + "\n\n" + prompts.PHYSICS
    return prompts.PHYSICS


def feedback_text(rounds: Optional[int], probe: bool) -> str:
    from harness import prompts
    if rounds is None:
        t = ("## Feedback\nUnlimited run_spec() calls: each simulates your current build against the public vectors and returns diagnostics "
             "(first divergence, waveforms, unreached ports). Iterate as long as you like, then submit().")
    else:
        t = prompts.feedback_section(rounds)
    return t + ("\nExtra simulator tools enabled for this session: probe(), view(live=true)." if probe else
                "\nprobe() and view(live=true) are disabled in this session; view() is static.")


def task_text(task: Task, arm: str, rounds: Optional[int], extras) -> str:
    from harness import prompts
    c = task.contract
    parts = [c.describe(public=True), "", f"Budget: at most {c.budgets.max_blocks} blocks of your own.", prompts.PALETTE
             + (f" Task hints: {' '.join(c.hints)}" if c.hints else ""), "", feedback_text(rounds, "probe" in extras), ""]
    if arm == "blocks":
        parts.append("Arm `blocks`: use set_blocks / fill / remove (undo available). Example set_blocks argument: "
                     '[[1,1,1,"minecraft:redstone_wire"],[2,1,1,"minecraft:repeater[facing=west,delay=1]"]]')
    else:
        parts.append("Arm `builder`: write a Python program and pass it to build(source); each build() REPLACES the current build. "
                     "Call guide() once for the Part API reference. Example statements inside build(b): "
                     "b.set((1,1,1), wire()); b.fill((2,1,1),(4,1,1), wire()); b.set((5,1,1), repeater(flow=EAST, delay=1)).")
    parts.append("Call guide() for the redstone facts this simulator enforces. submit() ends the episode and scores against ALL vectors.")
    return "\n".join(parts)


# --------------------------------------------------------------------------- session
class SessionError(Exception):
    pass


def _new_id(task_id, arm, rounds):
    r = "inf" if rounds is None else str(rounds)
    return f"{task_id}-{arm}-R{r}-{time.strftime('%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _valid_id(sid: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.\-]{1,120}", sid)) and not sid.startswith(".")


class Session:
    """One agent attempt at one task. Cheap handle: all state is on disk."""

    def __init__(self, sid: str):
        if not _valid_id(sid):
            raise SessionError(f"invalid session id {sid!r}")
        self.id = sid
        self.dir = sessions_dir() / sid

    # -- creation / loading
    @classmethod
    def create(cls, task_ref: str, arm: str = "builder", rounds: Optional[int] = 3, extras="", session_id: Optional[str] = None,
               seed: int = 0, label: str = "") -> "Session":
        if arm not in ARMS:
            raise SessionError(f"arm must be one of {ARMS}")
        if rounds is not None and rounds < 0:
            rounds = None
        ex = parse_extras(extras)
        task = load_task_ref(task_ref)
        sid = session_id or _new_id(task.id, arm, rounds)
        s = cls(sid)
        if s.dir.exists():
            raise SessionError(f"session {sid!r} already exists")
        s.dir.mkdir(parents=True)
        (s.dir / "sources").mkdir()
        state = {"id": sid, "task_ref": task_ref, "task_id": task.id, "tier": task.contract.tier, "arm": arm, "rounds": rounds,
                 "extras": sorted(ex), "seed": seed, "label": label, "created": time.time(), "rounds_used": 0, "done": False,
                 "cells": [], "undo": [], "builds": 0, "calls": {}, "result": None}
        with s._lock():
            s._write(state)
        s._log({"event": "start", "task": task.id, "arm": arm, "rounds": rounds, "extras": sorted(ex), "label": label})
        return s

    @classmethod
    def load(cls, sid: str) -> "Session":
        s = cls(sid)
        if not (s.dir / "state.json").exists():
            raise SessionError(f"no session {sid!r} in {sessions_dir()}")
        return s

    # -- disk
    def _lock(self):
        class _L:
            def __enter__(l):
                l.fh = open(self.dir / ".lock", "a")
                fcntl.flock(l.fh, fcntl.LOCK_EX)

            def __exit__(l, *a):
                fcntl.flock(l.fh, fcntl.LOCK_UN)
                l.fh.close()
        return _L()

    def _read(self) -> dict:
        return json.loads((self.dir / "state.json").read_text())

    def _write(self, st: dict):
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(st))
        os.replace(tmp, self.dir / "state.json")

    def _log(self, rec: dict):
        rec = {"t": round(time.time(), 3), **rec}
        with open(self.dir / "transcript.jsonl", "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def state(self) -> dict:
        return self._read()

    # -- env reconstruction
    def _env(self, st: dict):
        task = load_task_ref(st["task_ref"])
        env = Env(task, seed=st["seed"], max_rounds=st["rounds"])
        env.state.cells = {(x, y, z): s for x, y, z, s in st["cells"]}
        env.state.undo_stack = [{(x, y, z): s for x, y, z, s in snap} for snap in st["undo"]]
        env.rounds = st["rounds_used"]
        env.done = st["done"]
        return task, env

    @staticmethod
    def _dump(env, st):
        st["cells"] = env.state.as_list()
        st["undo"] = [[[x, y, z, s] for (x, y, z), s in snap.items()] for snap in env.state.undo_stack[-UNDO_DEPTH:]]
        st["rounds_used"] = env.rounds
        st["done"] = env.done

    # -- tool calls
    def call(self, tool: str, **kw) -> str:
        """Run one tool. Never raises for agent mistakes: they come back as 'error: ...' text. Result is clipped for context."""
        if tool not in TOOLS:
            return f"error: unknown tool {tool!r}; tools: {sorted(TOOLS)}"
        t0 = time.time()
        with self._lock():
            st = self._read()
            try:
                out = self._dispatch(st, tool, kw)
            except Exception as e:   # a tool must never kill the server
                out = f"error: {tool} failed: {type(e).__name__}: {str(e)[:300]}"
            st["calls"][tool] = st["calls"].get(tool, 0) + 1
            self._write(st)
        self._log({"event": "call", "tool": tool, "args": kw, "result": out[:LOG_RESULT_CHARS], "ms": int((time.time() - t0) * 1000)})
        return clip(out)

    def _footer(self, st) -> str:
        r = st["rounds"]
        return f"[{st['task_id']} {st['arm']} | run_spec {st['rounds_used']}/{'inf' if r is None else r} used | blocks {len(st['cells'])}]"

    def _dispatch(self, st: dict, tool: str, kw: dict) -> str:
        arm, extras = st["arm"], set(st["extras"])
        if tool in ("show",):
            return self.describe(st)
        if tool == "task_description":
            return task_text(load_task_ref(st["task_ref"]), arm, st["rounds"], extras)
        if tool == "guide":
            return guide_text(arm)
        if tool in ("search_components", "search_corpus") and ("components" if tool == "search_components" else "corpus") not in extras:
            return (f"error: {tool} is disabled in this session (started without extras={tool.split('_')[1]!r}); "
                    f"this baseline arm uses only the build/verify tools")
        if tool == "explain_block":
            from mira_mcp import knowledge
            return knowledge.explain_block(kw.get("block_id", ""))
        if tool == "search_components":
            from mira_mcp import knowledge
            return knowledge.search_components(kw.get("query", ""), kw.get("limit", 5))
        if tool == "search_corpus":
            from mira_mcp import knowledge
            return knowledge.search_corpus(kw.get("query", ""), kw.get("max_blocks", 80), kw.get("limit", 5), kw.get("show_program", 1))
        if st["done"] and tool not in ("view",):
            return "episode finished: submit() was already called (start a new task to try again)\n" + self._result_line(st)
        task, env = self._env(st)
        if tool in ENV_TOOLS_BLOCKS and arm != "blocks":
            return f"error: {tool} is not available in the builder arm; pass a program to build(source)"
        out = None
        if tool == "build":
            if arm != "builder":
                return "error: build(source) is only available in the builder arm; use set_blocks / fill / remove"
            out = self._build(st, task, env, kw.get("source"), kw.get("components"), "components" in extras)
        elif tool == "set_blocks":
            blocks = kw.get("blocks")
            if isinstance(blocks, str):
                try:
                    blocks = json.loads(blocks)
                except ValueError:
                    return "error: blocks must be a JSON array [[x,y,z,state], ...]"
            if isinstance(blocks, list):
                try:
                    blocks = [[int(round(float(v))) if i < 3 else v for i, v in enumerate(b)] if isinstance(b, (list, tuple)) else b
                              for b in blocks]
                except (TypeError, ValueError):
                    return "error: malformed blocks; expected [[x,y,z,state], ...] with integer coordinates"
            out = env.set_blocks(blocks)
        elif tool in ("fill", "remove"):
            try:
                a = {k: int(round(float(v))) for k, v in kw.items() if k != "state" and v is not None}
            except (TypeError, ValueError):
                return f"error: {tool} needs integer coordinates"
            out = env.fill(**a, state=kw.get("state", "")) if tool == "fill" else env.remove(**a)
        elif tool == "undo":
            out = env.undo()
        elif tool == "view":
            live = bool(kw.get("live"))
            note = ""
            if live and "probe" not in extras:
                live, note = False, "\n(note: live view is disabled in this session; showing the static view)"
            vk = {k: kw[k] for k in ("layer", "axis", "box") if kw.get(k) is not None}
            if "layer" in vk:
                vk["layer"] = int(round(float(vk["layer"])))
            if "box" in vk:
                vk["box"] = [int(round(float(v))) for v in vk["box"]]
            out = env.view(live=live, **vk) + note
        elif tool == "probe":
            if "probe" not in extras:
                return "error: probe is disabled in this session (it is simulator feedback outside the run_spec budget)"
            pos = kw.get("pos")
            if isinstance(pos, str) and re.fullmatch(r"\s*-?\d+\s*[, ]\s*-?\d+\s*[, ]\s*-?\d+\s*", pos):
                pos = [int(v) for v in re.split(r"[, ]+", pos.strip())]
            inputs = kw.get("inputs")
            if isinstance(inputs, str):
                inputs = _parse_inputs(inputs)
            out = env.probe(pos, ticks=int(kw.get("ticks") or 20), inputs=inputs)
        elif tool == "run_spec":
            if st["rounds"] == 0:
                return "error: run_spec is not available in this session (R=0: one-shot, no simulator feedback); call submit() when ready"
            if st["rounds"] is not None and st["rounds_used"] >= st["rounds"]:
                return f"error: feedback budget exhausted ({st['rounds']} run_spec calls used); refine from your own reasoning and call submit()"
            out = env.run_spec()
            out += "\n" + self._footer({**st, "rounds_used": env.rounds})
        elif tool == "submit":
            out = self._submit(st, task, env)
        else:
            return f"error: unhandled tool {tool}"
        self._dump(env, st)
        return out

    # -- tools that need more than Env
    def _build(self, st, task, env, source, components, components_ok) -> str:
        from simulation import builder as B
        if not isinstance(source, str) or not source.strip():
            return "error: build(source) needs the complete Python program text as a string"
        src = re.search(r"```(?:python|py)?\s*\n(.*?)```", source, re.S)
        src = src.group(1) if src else source
        offset = 0
        if components:
            if not components_ok:
                return "error: library components are disabled in this session (started without extras=components)"
            try:
                from env import library
                names = [components] if isinstance(components, str) else list(components)
                pre = library.prelude(names)
            except Exception as e:
                return f"error: cannot load components {components!r}: {type(e).__name__}: {str(e)[:200]}"
            src = pre + "\n# --- your program\n" + src
            offset = pre.count("\n") + 2
        st["builds"] += 1
        (self.dir / "sources" / f"{st['builds']:03d}.py").write_text(src)
        res = B.compile(src, timeout=20.0, max_blocks=MAX_COMPILE_BLOCKS)
        note = f"\n(line numbers include {offset} prelude lines from the selected components)" if offset else ""
        if res.errors:
            return "COMPILE FAILED - nothing was loaded; the current build is unchanged.\n" + res.summary() + note
        fixed, put, ignored, bad = env.fixed, [], 0, []
        for x, y, z, s, _nbt in res.blocks:
            pos = (x, y, z)
            try:
                cs = EB.canonical(s)
            except EB.BlockError as e:
                bad.append(f"{[x, y, z]}: {e}")
                continue
            if pos in fixed and fixed[pos] == cs:
                ignored += 1
                continue
            put.append((pos, cs))
        pr = prepare(task.contract, [[p[0], p[1], p[2], s] for p, s in put]) if not bad else None
        errs = bad + (pr.errors if pr else [])
        if errs:
            return ("COMPILE OK but the build was NOT loaded (task rules), current build unchanged:\n  " + "\n  ".join(errs[:12])
                    + ("\n" + res.summary() if res.warnings else "") + note)
        env.state.apply(put=put, delete=list(env.state.cells))
        b = res.bounds()
        msg = f"build loaded: {len(put)} blocks" + (f" ({ignored} identical to fixed blocks ignored)" if ignored else "")
        if b:
            msg += f", bounds {list(b[0])}..{list(b[1])}"
        warn = [f"warning {w}" for w in res.warnings[:8]]
        nbt_n = sum(1 for b_ in res.blocks if b_[4])
        if nbt_n:
            warn.append(f"warning: block NBT on {nbt_n} blocks is ignored by this task environment")
        return msg + ("\n" + "\n".join(warn) if warn else "") + note

    def _submit(self, st, task, env) -> str:
        out = env.submit()
        sc = env.final
        wall = round(time.time() - st["created"], 1)
        res = {"session": st["id"], "task": st["task_id"], "tier": st["tier"], "arm": st["arm"],
               "R": "inf" if st["rounds"] is None else st["rounds"], "extras": st["extras"], "rounds_used": env.rounds,
               "label": st["label"], "reward": round(sc.reward, 4), "full_pass": bool(sc.full_pass),
               "r0": round(sc.r0, 4), "r1": round(sc.r1, 4), "r2": round(sc.r2, 4), "r3": round(sc.r3, 4), "r4": round(sc.r4, 4),
               "blocks": sc.blocks, "hidden": f"{sc.n_hidden_ok}/{sc.n_hidden}", "builds": st["builds"],
               "calls": sum(st["calls"].values()) + 1, "wall_s": wall, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        st["result"] = res
        (self.dir / "result.json").write_text(json.dumps({**res, "feedback": out}, indent=1))
        (self.dir / "final_blocks.json").write_text(json.dumps(env.export()))
        with open(sessions_dir() / "results.jsonl", "a") as fh:
            fh.write(json.dumps(res) + "\n")
        return out + "\n" + self._result_line({"result": res})

    @staticmethod
    def _result_line(st) -> str:
        return "RESULT " + json.dumps(st["result"]) if st.get("result") else ""

    def describe(self, st=None) -> str:
        st = st or self._read()
        r = st["rounds"]
        lines = [f"session {st['id']}", f"task {st['task_id']} [{st['tier']}]  arm {st['arm']}  R {'inf' if r is None else r}  "
                 f"run_spec used {st['rounds_used']}  extras {st['extras'] or 'none'}",
                 f"build: {len(st['cells'])} blocks, {len(st['undo'])} undo steps, {st['builds']} builds; calls {st['calls']}",
                 "state: " + ("SUBMITTED" if st["done"] else "open")]
        if st.get("result"):
            lines.append(self._result_line(st))
        return "\n".join(lines)

    def export(self) -> list:
        """The current build as a flat block list (for scripts/tests)."""
        return self._read()["cells"]


def parse_extras(extras) -> set:
    if isinstance(extras, str):
        parts = [e.strip() for e in extras.split(",") if e.strip()]
    else:
        parts = list(extras or [])
    if "all" in parts:
        return set(EXTRAS)
    bad = [e for e in parts if e not in EXTRAS]
    if bad:
        raise SessionError(f"unknown extras {bad}; choose from {EXTRAS} or 'all'")
    return set(parts)


def _parse_inputs(text: str) -> dict:
    out = {}
    for kv in re.split(r"[,\s]+", text.strip()):
        if kv:
            k, _, v = kv.partition("=")
            out[k] = int(v)
    return out


TOOLS = {"show", "task_description", "guide", "set_blocks", "fill", "remove", "build", "view", "probe", "run_spec", "submit", "undo",
         "search_components", "search_corpus", "explain_block"}


def start(task_ref: str, arm: str = "builder", rounds: Optional[int] = 3, extras="", session_id: Optional[str] = None,
          seed: int = 0, label: str = "") -> Session:
    return Session.create(task_ref, arm, rounds, extras, session_id, seed, label)


def list_sessions() -> list:
    out = []
    for p in sorted(sessions_dir().iterdir()):
        if p.is_dir() and (p / "state.json").exists():
            try:
                st = json.loads((p / "state.json").read_text())
            except ValueError:
                continue
            out.append({"id": st["id"], "task": st["task_id"], "arm": st["arm"], "R": "inf" if st["rounds"] is None else st["rounds"],
                        "done": st["done"], "reward": (st.get("result") or {}).get("reward")})
    return out
