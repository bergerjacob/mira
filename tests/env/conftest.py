"""Fixtures for the mira_env (env/) tests: offline, Nucleation only."""
import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from env.task import list_task_ids, load_task


@functools.lru_cache(maxsize=None)
def cached_task(task_id):
    return load_task(task_id)


@pytest.fixture
def task():
    """task('t0_not') -> Task (cached; do not mutate)."""
    return cached_task


ALL_IDS = list_task_ids()
