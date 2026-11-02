"""Fixtures for harness tests: offline, scripted LLM only, no network, no API key."""
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


@pytest.fixture
def task():
    """task('t0_not') -> Task (cached; do not mutate)."""
    return cached_task


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "ledger.jsonl"
