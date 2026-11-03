#!/usr/bin/env python3
"""Run pytest under the system interpreter (which has torch/transformers but no pytest).

    /usr/bin/python3 training/local/run_tests.py tests/training_pipeline -q
    /usr/bin/python3 training/local/run_tests.py tests/training_pipeline -m gpu      # include the GPU tests

pytest, pluggy, iniconfig ... are borrowed from the repo .venv (appended to sys.path, so system torch/numpy win).
Under `.venv/bin/python` only the 20 torch-free tests run (the venv's own torch 2.9 does not pair with the user-site
transformers 5.2); the other 34 skip.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from training.local.paths import setup_paths  # noqa: E402

setup_paths()
import pytest  # noqa: E402

if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:]))
