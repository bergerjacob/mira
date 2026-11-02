"""Fixtures for mira_mcp tests: offline, every session lives in a tmp dir (MIRA_SESSIONS_DIR), no network, no API key."""
import functools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest

from env.task import load_task


@functools.lru_cache(maxsize=None)
def cached_task(task_id):
    return load_task(task_id)


@pytest.fixture(autouse=True)
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    monkeypatch.setenv("MIRA_SESSIONS_DIR", str(d))
    monkeypatch.delenv("MIRA_SESSION", raising=False)
    monkeypatch.delenv("MIRA_MAX_CHARS", raising=False)
    return d


@pytest.fixture
def task():
    return cached_task


def hidden_markers(task_obj):
    """Strings that must never appear in anything shown to the agent: hidden-only vector inputs and hidden sequence names."""
    c = task_obj.contract
    public_inputs = [v.inputs for v in c.vectors() if v.public]
    marks = [f"set {v.inputs} ->" for v in c.vectors() if not v.public and v.inputs not in public_inputs]
    marks += [f"Timing sequence '{s.name}'" for s in c.behaviour.sequences if not s.public]
    return marks
