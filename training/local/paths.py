"""Interpreter bridge for the local (torch) training stack.

torch/transformers live in the SYSTEM interpreter's user site (/usr/bin/python3 3.13), the environment deps (nucleation,
json_repair, ...) in the repo `.venv` (also 3.13). `setup_paths()` puts the project root first on sys.path and APPENDS the
other interpreter's site-packages at the END, so packages already importable win and only missing ones are borrowed.
Works in both directions (system python borrowing nucleation, venv python borrowing transformers).

    import sys, os; sys.path.insert(0, ROOT)
    from training.local.paths import setup_paths; setup_paths()
"""
import os
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV_SITE = ROOT / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
USER_SITE = Path(site.getuserbase()) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
RUNS_DIR = ROOT / "data" / "training_runs"          # gitignored (data/)


def setup_paths(offline: bool = True) -> None:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    for p in (VENV_SITE, USER_SITE):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.append(str(p))
    if offline:
        # cached models only; never reach the hub (the sandbox has no network anyway)
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")   # long prompts on the 12 GB card fragment otherwise
