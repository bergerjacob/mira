"""Fixtures for project-mode tests: offline, every project lives under tmp_path (MIRA_PROJECTS_DIR)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest

from mira_mcp import project as PJ


@pytest.fixture(autouse=True)
def projects_dir(tmp_path, monkeypatch):
    d = tmp_path / "projects"
    monkeypatch.setenv("MIRA_PROJECTS_DIR", str(d))
    monkeypatch.delenv("MIRA_PROJECT", raising=False)
    monkeypatch.delenv("MIRA_MAX_CHARS", raising=False)
    return d


def write(p: Path, rel: str, text: str):
    f = p / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def program_from_blocks(blocks) -> str:
    """A Part API program that places exactly these (x, y, z, state) blocks."""
    lines = ["from simulation.builder import *", "", "", "def build(b):"]
    lines += [f"    b.set(({x}, {y}, {z}), {s!r})" for x, y, z, s, *_ in blocks] or ["    pass"]
    return "\n".join(lines) + "\n"


@pytest.fixture
def make():
    """make(name, build_py=None, files={}, **init_kw) -> Project."""
    def _make(name="p", build_py=None, files=None, **kw):
        prj, _ = PJ.Project.init(name, **kw)
        if build_py is not None:
            write(prj.dir, "build.py", build_py)
        for rel, text in (files or {}).items():
            write(prj.dir, rel, text)
        return prj
    return _make


NOT_TASK_PROGRAM = '''"""t0_not: torch inverter between the fixed lever (0,1,1) and lamp (7,1,1)."""
from simulation.builder import *
from modules.inv import inverter_row


def build(b):
    inverter_row(b, 1)
'''

NOT_MODULE = '''from simulation.builder import *


def inverter_row(b, z):
    b.set((1, 1, z), wire())
    b.set((2, 1, z), stone())
    b.set((3, 1, z), torch(wall=EAST))
    b.fill((4, 1, z), (6, 1, z), wire())
'''
