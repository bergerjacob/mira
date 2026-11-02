#!/usr/bin/env python3
"""Turn an experiment's run logs into markdown tables.

    python scripts/analyze_experiment.py --exp-id pilot1 --out docs/experiments/pilot1.md
    python scripts/analyze_experiment.py --exp-dir data/experiments/pilot1            # prints to stdout

Sections: setup, headline (continuation criterion), pass rate/mean reward by model x arm x R, iteration lift with bootstrap CIs,
rounds/tokens/$ per solved task, per-task heatmap, failure taxonomy (+ builder compile errors), caveats. See docs/HARNESS.md.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from harness.analysis import build_report
from harness.llm import DEFAULT_LEDGER, EXPERIMENTS_DIR


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--exp-id")
    g.add_argument("--exp-dir")
    ap.add_argument("--exp-root", default=str(EXPERIMENTS_DIR))
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--title")
    ap.add_argument("--out", help="write the markdown report here (default: stdout)")
    a = ap.parse_args(argv)
    exp_dir = Path(a.exp_dir) if a.exp_dir else Path(a.exp_root) / a.exp_id
    if not (exp_dir / "runs").exists():
        print(f"error: no runs directory in {exp_dir}", file=sys.stderr)
        return 2
    text = build_report(exp_dir, a.title, a.ledger)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
        print(f"wrote {a.out} ({len(text.splitlines())} lines)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
