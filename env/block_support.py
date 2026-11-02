"""Which block ids can Nucleation's simulator load?   python env/block_support.py [--list]

Places every registry block (default state) alone on a stone floor and tries to load it into TickSimulation."""
import json
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nucleation import Blocks

from env import blocks as B
from env.sim import simulatable


def sweep():
    ids = json.loads(Blocks.ids_json())
    ok = [i for i in ids if simulatable(B.canonical(i))]
    return ok, [i for i in ids if i not in set(ok)]


if __name__ == "__main__":
    t0 = time.time()
    ok, bad = sweep()
    print(f"{len(ok)} loadable, {len(bad)} refused of {len(ok) + len(bad)} block ids ({time.time() - t0:.1f}s)")
    if "--list" in sys.argv:
        print("refused:", " ".join(b.split(":")[1] for b in bad))
