"""Scripted 'oracle player': a ScriptedLLM script that builds each task's reference solution and submits.

Used by the end-to-end test (every task must reach reward 1.0 in both arms), by `run_experiment.py --mock`, and by the
dry-run cost estimate (token sizes of the prompt, a full build message and the tool results).
"""
from harness.llm import ScriptedLLM, assistant


def builder_source(solution) -> str:
    """Builder program (absolute coordinates) that reproduces a flat block list; later duplicates win."""
    cells = {}
    for b in solution:
        cells[(b[0], b[1], b[2])] = b[3]
    by = {}
    for pos, st in cells.items():
        by.setdefault(st, []).append(pos)
    lines = ["from simulation.builder import *", "", "def build(b):"]
    for st, ps in by.items():
        lines.append(f"    b.points({st!r}, " + ", ".join(str(p) for p in ps) + ")")
    return "\n".join(lines) + "\n"


def reference_script(task, arm: str, rounds: int) -> list:
    c = task.contract
    y = c.build_box[1]
    msgs = [assistant("Inspect the floor plan first.", [("view", {"layer": y})])]
    if arm == "blocks":
        msgs.append(assistant("Placing the build.", [("set_blocks", {"blocks": [list(b[:4]) for b in task.solution]})]))
    else:
        msgs.append(assistant("Writing the program.", [("build", {"source": builder_source(task.solution)})]))
    if rounds > 0:
        msgs.append(assistant("Check against the public vectors.", [("run_spec", {})]))
    msgs.append(assistant("Done.", [("submit", {})]))
    return msgs


def reference_llm(task, arm: str, rounds: int, model: str = "mock", budget=None, **kw) -> ScriptedLLM:
    return ScriptedLLM(reference_script(task, arm, rounds), model=model, budget=budget, **kw)


def flawed_script(task, arm: str, rounds: int, flaw: int = 0, fix_at: int = 1, compile_typo: bool = False) -> list:
    """A player that first builds a broken variant of the reference (task.broken[flaw]) and, when it gets `fix_at`-th
    run_spec feedback, switches to the reference. `fix_at=None` never fixes. With R=0 it submits the flawed build.
    `compile_typo` (builder arm) first sends a program with a syntax error."""
    from env import mutate
    bad = mutate.apply(task.solution, task.broken[flaw % len(task.broken)]["edits"]) if task.broken else task.solution

    def build_msg(blocks):
        if arm == "blocks":
            return assistant("build", [("set_blocks", {"blocks": [list(b[:4]) for b in blocks]})])
        return assistant("build", [("build", {"source": builder_source(blocks)})])

    msgs = []
    if compile_typo and arm == "builder":
        msgs.append(assistant("first try", [("build", {"source": "def build(b)\n    b.set((1,1,1), wire())\n"})]))
    msgs.append(build_msg(bad))
    fixed = False
    for i in range(1, rounds + 1):
        msgs.append(assistant("check", [("run_spec", {})]))
        if fix_at is not None and i >= fix_at and not fixed:
            msgs.append(build_msg(task.solution))
            fixed = True
    msgs.append(assistant("done", [("submit", {})]))
    return msgs
