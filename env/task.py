"""Task = Contract + reference solution (+ optional hand-written broken variants), stored as env/tasks/<id>.json."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from env.contract import Contract, box_volume

TASK_DIR = Path(__file__).parent / "tasks"


@dataclass
class Task:
    contract: Contract
    solution: list                          # flat block list [[x,y,z,state], ...] (model-placed blocks only)
    broken: list = field(default_factory=list)   # hand-written variants: [{"name", "edits": [{"op","pos"/...}]}]
    notes: str = ""
    path: Path = None

    @property
    def id(self):
        return self.contract.id

    def to_dict(self):
        return {"contract": self.contract.to_dict(), "solution": self.solution, "broken": self.broken, "notes": self.notes}


def reference_stats(solution):
    """{"blocks": n, "volume": bounding-box volume of the solution} used by R4."""
    if not solution:
        return {"blocks": 0, "volume": 0}
    xs, ys, zs = ([b[i] for b in solution] for i in range(3))
    return {"blocks": len(solution), "volume": box_volume([min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)])}


def task_from_dict(d, path=None):
    c = Contract.from_dict(d["contract"])
    if not c.reference:
        c.reference = reference_stats(d["solution"])
    return Task(c, [list(b) for b in d["solution"]], d.get("broken", []), d.get("notes", ""), path)


def load_task(task_id_or_path):
    p = Path(task_id_or_path)
    if not p.suffix:
        p = TASK_DIR / f"{task_id_or_path}.json"
        for sub in ("t3", "t4"):
            if not p.exists():
                p = TASK_DIR / sub / f"{task_id_or_path}.json"
    return task_from_dict(json.loads(p.read_text()), p)


def list_task_ids(tier=None):
    ids = sorted(p.stem for p in [*TASK_DIR.glob("*.json"), *TASK_DIR.glob("t3/t3_*.json"), *TASK_DIR.glob("t4/t4_*.json")])
    if tier:
        ids = [i for i in ids if i.startswith(tier.lower() + "_")]
    return ids


def load_all(tier=None):
    return [load_task(i) for i in list_task_ids(tier)]


def dumps_task(d):
    """JSON with one block per line in `solution` (readable diffs)."""
    body = {k: v for k, v in d.items() if k != "solution"}
    s = json.dumps(body, indent=1, default=list)
    s = re.sub(r"\[\s+([^\[\]{}]*?)\s+\]", lambda m: "[" + re.sub(r"\s+", " ", m.group(1)) + "]", s)  # scalar lists on one line
    sol = ",\n".join("  " + json.dumps(b) for b in d["solution"])
    return s[:-2] + ',\n "solution": [\n' + sol + "\n ]\n}\n"


def save_task(task: Task, path=None):
    p = Path(path) if path else TASK_DIR / f"{task.id}.json"
    p.write_text(dumps_task(task.to_dict()))
    return p
