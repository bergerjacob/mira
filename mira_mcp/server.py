"""MIRA build/verify environment as an MCP server (stdio).

    .venv/bin/python mira_mcp/server.py [--sessions-dir DIR] [--resume SESSION_ID]

Register it in Claude Code / pi with the snippet in docs/MCP.md. One server process drives one active session at a time
(start_task begins a new one, resume_session reattaches to one on disk); all state lives in data/sessions/<id>/ so the CLI
(scripts/mira_env_cli.py) can continue it. Every tool returns plain text sized for an LLM context (explicit truncation notices);
hidden vectors, the evaluation seed and the reference solution are never part of any response.
"""
import argparse
import os
import sys
from pathlib import Path
from typing import Any, Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from mira_mcp import knowledge, session as S  # noqa: E402

INSTRUCTIONS = """\
MIRA redstone build/verify environment. Workflow: list_tasks -> start_task(task_id, arm, rounds) -> task_description() and guide() ->
build (set_blocks/fill/remove for arm 'blocks', build(source) for arm 'builder') -> view() -> run_spec() (limited by the round
budget) -> fix -> submit() once. submit() scores against hidden vectors and ends the task. Coordinates are absolute task
coordinates; x east, y up, z south. Do not read env/, lib/specs.py or data/ files to find answers: the point is to measure the build/verify loop."""

mcp = MCPServer("mira-env", instructions=INSTRUCTIONS)
_current: dict = {"id": None}


def _sess():
    sid = _current["id"] or os.environ.get("MIRA_SESSION")
    if not sid:
        return None
    return S.Session.load(sid)


def _run(tool: str, **kw) -> str:
    try:
        s = _sess()
    except S.SessionError as e:
        return f"error: {e}"
    if s is None:
        return "error: no active task; call list_tasks() then start_task(task_id, arm, rounds) first (or resume_session(id))"
    return s.call(tool, **kw)


@mcp.tool()
def list_tasks(tier: str = "") -> str:
    """List task ids with tier, port counts and reference size. tier: '' or 'all', 't0', 't1', 't2', 't3'."""
    return S.list_tasks(tier)


@mcp.tool()
def start_task(task_id: str, arm: str = "builder", rounds: int = 3, extras: str = "", session_id: str = "", label: str = "") -> str:
    """Begin an attempt at a task and make it the active session. arm: 'blocks' (set_blocks/fill/remove) or 'builder'
    (build(source), Python Part API). rounds: how many run_spec() simulator-feedback calls you get (0 = one-shot, -1 = unlimited).
    extras: comma list of 'probe' (probe()/view live), 'components' (library parts), 'corpus' (corpus search) or 'all'; default none.
    task_id may also be a path to a task/contract JSON. label: free text recorded in the results line (e.g. the model name)."""
    try:
        s = S.start(task_id, arm=arm, rounds=None if rounds < 0 else rounds, extras=extras, session_id=session_id or None, label=label)
    except (S.SessionError, ValueError) as e:
        return f"error: {e}"
    _current["id"] = s.id
    return f"started session {s.id}\n\n" + s.call("task_description")


@mcp.tool()
def resume_session(session_id: str) -> str:
    """Attach to an existing session on disk (started here or by the CLI) and show its state."""
    try:
        s = S.Session.load(session_id)
    except S.SessionError as e:
        return f"error: {e}"
    _current["id"] = s.id
    return s.call("show")


@mcp.tool()
def session_status() -> str:
    """The active session's task, arm, rounds used, build size and whether it was submitted."""
    return _run("show")


@mcp.tool()
def task_description() -> str:
    """The task text for the active session: description, boxes, fixed blocks, ports, PUBLIC example vectors, budgets and feedback rules."""
    return _run("task_description")


@mcp.tool()
def guide() -> str:
    """Redstone facts this simulator enforces (facing conventions, wire rules, timing) and, for the builder arm, the Part API reference."""
    return _run("guide")


@mcp.tool()
def set_blocks(blocks: Union[list[Any], str]) -> str:
    """[blocks arm] Place blocks: [[x,y,z,"minecraft:redstone_wire"], ...] (or dicts with x,y,z,state). Air removes. Invalid states, cells outside
    the build box and fixed cells are rejected with a message; the rest is applied."""
    return _run("set_blocks", blocks=blocks)


@mcp.tool()
def fill(x1: int, y1: int, z1: int, x2: int, y2: int, z2: int, state: str) -> str:
    """[blocks arm] Fill the inclusive box with a block state (e.g. "minecraft:stone"); "minecraft:air" clears it."""
    return _run("fill", x1=x1, y1=y1, z1=z1, x2=x2, y2=y2, z2=z2, state=state)


@mcp.tool()
def remove(x1: int, y1: int, z1: int, x2: Optional[int] = None, y2: Optional[int] = None, z2: Optional[int] = None) -> str:
    """[blocks arm] Remove all blocks in the box (or the single block when only x1,y1,z1 are given)."""
    return _run("remove", x1=x1, y1=y1, z1=z1, x2=x2, y2=y2, z2=z2)


@mcp.tool()
def build(source: str, components: Optional[list[str]] = None) -> str:
    """[builder arm] Compile a complete Python program (defines build(b); see guide()) in the sandbox and load its blocks as the CURRENT build,
    replacing the previous one. On any error nothing is loaded and errors come back with line numbers. components: library part names
    to prepend (only when the session has extras 'components')."""
    return _run("build", source=source, components=components)


@mcp.tool()
def undo() -> str:
    """Undo the last build-changing call (set_blocks / fill / remove / build)."""
    return _run("undo")


@mcp.tool()
def view(layer: Optional[int] = None, axis: str = "y", box: Optional[list[int]] = None, live: bool = False) -> str:
    """ASCII slice of the world (fixed blocks + your build) with a legend and the ports in the slice. axis y: layer=y, rows=z, cols=x;
    axis x: rows=y, cols=z; axis z: rows=y, cols=x. box=[x1,y1,z1,x2,y2,z2] limits the area (max 8 layers). live=true shows simulated
    signal levels (only when the session has extras 'probe')."""
    return _run("view", layer=layer, axis=axis, box=box, live=live)


@mcp.tool()
def probe(pos: Union[list[int], str], ticks: int = 20, inputs: Optional[dict[str, int]] = None) -> str:
    """Watch one cell (or an output port name) change over time from the at-rest start; inputs like {"A":1} are applied at tick 0.
    Only when the session has extras 'probe' (it is simulator feedback outside the run_spec budget)."""
    return _run("probe", pos=pos, ticks=ticks, inputs=inputs)


@mcp.tool()
def run_spec() -> str:
    """Simulate the current build against the PUBLIC vectors/sequences and invariants and explain failures (first divergence, waveforms,
    unreached ports). Counts against the session's feedback-round budget."""
    return _run("run_spec")


@mcp.tool()
def submit() -> str:
    """Finish: score the current build against ALL vectors (hidden ones included), record the result in data/sessions and end the task."""
    return _run("submit")


@mcp.tool()
def search_components(query: str, limit: int = 5) -> str:
    """Search the verified component library lib/ (gates, adders, latches...): ports, footprint, timing, how to place it from build().
    Needs extras 'components' in an active session."""
    return _run("search_components", query=query, limit=limit) if _current["id"] or os.environ.get("MIRA_SESSION") else knowledge.search_components(query, limit)


@mcp.tool()
def search_corpus(query: str, max_blocks: int = 80, limit: int = 5, show_program: int = 1) -> str:
    """Search the schematic corpus (data/corpus/index.jsonl; clean-licence and internal entries only, simulatable, no entities, <= max_blocks).
    Returns metadata and, for the top show_program hits, the builder program text (relative coordinates). 'id:<hash>' fetches one entry.
    Needs extras 'corpus' in an active session."""
    if _current["id"] or os.environ.get("MIRA_SESSION"):
        return _run("search_corpus", query=query, max_blocks=max_blocks, limit=limit, show_program=show_program)
    return knowledge.search_corpus(query, max_blocks, limit, show_program)


@mcp.tool()
def explain_block(block_id: str) -> str:
    """Valid properties and defaults of a block (e.g. 'repeater', 'minecraft:comparator'), whether the simulator can load it, and facing notes.
    Pass a full state like 'repeater[delay=5]' to validate it."""
    return knowledge.explain_block(block_id)


# --------------------------------------------------------------------------- project mode (docs/PRODUCT.md)
# A project is a directory (name -> data/projects/<name>, or a path): build.py + modules/ + spec.json + NOTES.md + .mira/.
# Edit the files with your own file tools, then call these; they mirror `scripts/mira.py` subcommands.

def _proj(project: str, cmd: str, **kw) -> str:
    from mira_mcp import project as PJ
    return PJ.clip(PJ.run(project, cmd, **kw))


@mcp.tool()
def project_init(name: str, path: str = "", from_task: str = "", from_litematic: str = "", program: str = "") -> str:
    """Create a project (build.py, modules/, NOTES.md, .mira/; spec.json when from_task is given). path: directory (default
    data/projects/<name>). from_litematic: decompile a schematic into build.py. program: copy a Part API program into build.py."""
    return _proj(name, "init", name=name, path=path or None, from_task=from_task or None,
                 from_litematic=from_litematic or None, program=program or None)


@mcp.tool()
def project_build(project: str, message: str = "") -> str:
    """Compile build.py (+ `from modules.X import ...` / `from lib.X import ...`) in the sandbox and store a revision.
    Errors come back as file:line; on success: blocks, bbox, palette, blocks per source file and per instance group."""
    return _proj(project, "build", message=message)


@mcp.tool()
def project_overview(project: str, rev: str = "") -> str:
    """Multi-resolution summary of the latest (or given) revision, under ~80 lines for any size: modules, ports, top-down
    class map + module-letter map, side elevation, pointers to detail commands."""
    return _proj(project, "overview", rev=rev or None)


@mcp.tool()
def project_view(project: str, layer: int, box: Optional[list[int]] = None, rev: str = "") -> str:
    """Full-resolution ASCII slice of layer y (scenery from spec.json included). box = [x0, z0, x1, z1]; clipped with a notice."""
    return _proj(project, "view", layer=layer, box=box, rev=rev or None)


@mcp.tool()
def project_find(project: str, pattern: str) -> str:
    """Spec ports, build ports, labels, instances/parts, source files and block ids matching a substring or glob, with coordinates."""
    return _proj(project, "find", pattern=pattern)


@mcp.tool()
def project_query(project: str, x: int, y: int, z: int, radius: int = 0) -> str:
    """Block states around a point and the source file:line (and instance) that wrote each block."""
    return _proj(project, "query", x=x, y=y, z=z, radius=radius)


@mcp.tool()
def project_diff(project: str, rev_a: str = "", rev_b: str = "", examples: int = 10) -> str:
    """Block-level diff between two revisions (default previous -> latest), summarised by module and region, with examples."""
    return _proj(project, "diff", a=rev_a or None, b=rev_b or None, examples=examples)


@mcp.tool()
def project_test(project: str, module: str = "", public_only: bool = False, fast: bool = False) -> str:
    """Run spec.json (or modules/<module>.spec.json) in the simulator: verdict, failing vectors with first divergence and
    waveforms, invariants (at rest, paste-stable, ...). Rebuilds first when the sources changed."""
    return _proj(project, "test", module=module or None, public_only=public_only, fast=fast)


@mcp.tool()
def project_trace(project: str, signals: Optional[list[str]] = None, ticks: Optional[int] = None, seq: str = "", sets: str = "",
                  vector: Optional[int] = None, module: str = "") -> str:
    """ASCII timing diagram of spec ports, build ports or "x,y,z" blocks under the last test's stimulus (vectors in the test's
    order), a named sequence (seq), custom vectors (sets="A=1,B=0;A=0") or an idle run (ticks). vector=K shows only vector K."""
    return _proj(project, "trace", signals=signals or [], ticks=ticks, seq=seq or None, sets=sets or None, vector=vector,
                 module=module or None)


@mcp.tool()
def project_notes_check(project: str) -> str:
    """Lint NOTES.md: port:/label:/module:/inst:/part: references, coordinates (x, y, z) and backticked names must still exist."""
    return _proj(project, "notes-check")


@mcp.tool()
def project_export(project: str, litematic: str = "", nbt: str = "", include_fixed: bool = True, force: bool = False) -> str:
    """Write the settled at-rest world (build + spec scenery) as .litematic (default .mira/exports/) and optionally a vanilla
    structure .nbt; reports whether the build is placement-path sensitive."""
    return _proj(project, "export", litematic=litematic or None, nbt=nbt or None, include_fixed=include_fixed, force=force)


@mcp.tool()
def project_paste_check(project: str, file: str = "", ticks: int = 40) -> str:
    """Paste the export into vanilla Minecraft 26.2 (oracle; quiet and full-update placement), idle `ticks`, read every block
    back and diff against the file -> receipt (.mira/paste_receipt.json). Exports first when needed."""
    return _proj(project, "paste-check", file=file or None, ticks=ticks)


@mcp.tool()
def project_init_module(project: str, name: str, part: str = "") -> str:
    """modules/<name>.py template and, once the part compiles, a modules/<name>.spec.json skeleton derived from its ports
    (pad inputs one cell outside each 'in' port, wire_power outputs); fill in the truth table, then project_test(module=name)."""
    return _proj(project, "init-module", name=name, part=part or None)


@mcp.tool()
def project_log(project: str, n: int = 20) -> str:
    """Revisions with time, hash, block count, test verdicts and build messages."""
    return _proj(project, "log", n=n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sessions-dir", help="session store (default data/sessions; env MIRA_SESSIONS_DIR)")
    ap.add_argument("--resume", help="attach to this session id at startup")
    a = ap.parse_args(argv)
    if a.sessions_dir:
        os.environ["MIRA_SESSIONS_DIR"] = str(Path(a.sessions_dir).resolve())
    if a.resume:
        _current["id"] = a.resume
    mcp.run("stdio")


if __name__ == "__main__":
    main()
