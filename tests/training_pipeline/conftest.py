"""Offline tests for the training pipeline (harness/llm_local.py, training/local, training/sft, training/rl).

Run with the system interpreter (torch + transformers live in its user site, pytest/nucleation are borrowed from .venv):
    /usr/bin/python3 training/local/run_tests.py tests/training_pipeline -q
    /usr/bin/python3 training/local/run_tests.py tests/training_pipeline -q -m gpu        # only the GPU tests (cached 1.7B model)
Everything here runs on CPU with a random 2-layer Qwen3 (`tiny`) and the cached Qwen3 tokenizer; tests marked `gpu` are skipped
unless selected with `-m gpu` or MIRA_GPU_TESTS=1.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from training.local.paths import setup_paths  # noqa: E402

setup_paths()

import pytest  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "gpu: needs CUDA and a cached HF model (skipped unless -m gpu or MIRA_GPU_TESTS=1)")


def pytest_collection_modifyitems(config, items):
    selected = "gpu" in (config.getoption("-m") or "") or os.environ.get("MIRA_GPU_TESTS") == "1"
    if selected:
        return
    skip = pytest.mark.skip(reason="GPU test (run with -m gpu or MIRA_GPU_TESTS=1)")
    for it in items:
        if "gpu" in it.keywords:
            it.add_marker(skip)


def _have_torch():
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except Exception:
        return False


HAVE_TORCH = _have_torch()
needs_torch = pytest.mark.skipif(not HAVE_TORCH, reason="torch/transformers not importable")


@pytest.fixture(scope="session")
def tokenizer():
    if not HAVE_TORCH:
        pytest.skip("torch/transformers not importable")
    from training.local.models import load_tokenizer, tokenizer_cached
    if not tokenizer_cached():
        pytest.skip("Qwen3 tokenizer not in the HF cache")
    return load_tokenizer("qwen3-1.7b")


@pytest.fixture(scope="session")
def tiny(tokenizer):
    """(model, tokenizer): random 2-layer Qwen3 on CPU, fp32."""
    from training.local.models import tiny_model
    model, tok = tiny_model(seed=0)
    return model, tok


@pytest.fixture
def fresh_tiny(tokenizer):
    from training.local.models import tiny_model
    return tiny_model(seed=1)


@pytest.fixture
def task():
    """task('t0_lamp') -> Task (cached; do not mutate)."""
    import functools
    from env.task import load_task
    return functools.lru_cache(maxsize=None)(load_task)
