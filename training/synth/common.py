"""Shared pieces of the synthetic data engine: acceptance gate, task/program storage, parallel map.

Every stored task passed `env.validate_tasks.evaluate` (the same gate as env/tasks): spec valid and non-vacuous, reference
reward exactly 1.0 with every invariant, empty build fails, >= 3 auto-mutants fail. Stored under data/synth/tasks/<family>/
as <id>.json (env task schema + a "synth" block) and <id>.py (the reference builder program, self-contained: library prelude
+ body, compiles with simulation.builder.compile in the sandbox).
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from env.task import dumps_task, task_from_dict  # noqa: E402

DATA = ROOT / "data" / "synth"
TASKS = DATA / "tasks"
SFT = DATA / "sft"
MAX_WORKERS = 8


def gate(task, mutants=8, seed=0):
    """Acceptance gate; on success the failing mutants are stored as task.broken. -> report dict (env.validate_tasks format)."""
    from env import validate_tasks as V
    rep = V.evaluate(task, mutants, seed)
    if rep["accepted"]:
        task.broken = [{"name": b["name"], "kind": b["kind"], "edits": b["edits"]} for b in rep["broken_examples"]]
    return rep


def program_matches(task, program):
    """The program, compiled and filtered like the harness build tool, loads exactly the task solution."""
    from training.synth.bugs import solution_of
    sol = solution_of(task, program)
    return not isinstance(sol, str) and sorted(map(tuple, sol)) == sorted(tuple(b[:4]) for b in task.solution)


def save(task, program, synth, family, root=None):
    d = Path(root or TASKS) / family
    d.mkdir(parents=True, exist_ok=True)
    body = task.to_dict()
    body["synth"] = synth
    (d / f"{task.id}.json").write_text(dumps_task(body))
    (d / f"{task.id}.py").write_text(program)
    return d / f"{task.id}.json"


def load(path):
    """-> (Task, program text, synth meta) for a stored synthetic task."""
    path = Path(path)
    d = json.loads(path.read_text())
    return task_from_dict(d, path), path.with_suffix(".py").read_text(), d.get("synth", {})


def stored(family=None, root=None):
    pat = f"{family}/*.json" if family else "*/*.json"
    return sorted(Path(root or TASKS).glob(pat))


def signature(obj):
    import hashlib
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def claim(family, obj, root=None):
    """Atomically reserve the signature of `obj` (e.g. a netlist) for `family`; False if it was generated before (dedup across workers)."""
    d = Path(root or TASKS) / family / ".sig"
    d.mkdir(parents=True, exist_ok=True)
    try:
        (d / signature(obj)).open("x").close()
        return True
    except FileExistsError:
        return False


def pmap(fn, items, workers=MAX_WORKERS, progress=None):
    """Parallel map in worker processes (capped at MAX_WORKERS); yields results in completion order."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    workers = max(1, min(workers, MAX_WORKERS))
    if workers == 1:
        for it in items:
            yield fn(it)
        return
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(fn, it) for it in items]
        for f in as_completed(futs):
            yield f.result()


class Timer:
    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *a):
        self.s = round(time.time() - self.t0, 2)
