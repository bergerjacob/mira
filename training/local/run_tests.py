#!/usr/bin/env python3
"""Run pytest under the system interpreter (which has torch/transformers but no pytest).

    /usr/bin/python3 training/local/run_tests.py tests/training_pipeline -q
    /usr/bin/python3 training/local/run_tests.py tests/training_pipeline -m gpu      # include the GPU tests

pytest, pluggy, iniconfig ... are borrowed from the repo .venv (appended to sys.path, so system torch/numpy win).
`.venv/bin/python -m pytest tests/training_pipeline` also works (the conftest borrows transformers from the user site).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from training.local.paths import setup_paths  # noqa: E402

setup_paths()
import pytest  # noqa: E402

if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:]))
