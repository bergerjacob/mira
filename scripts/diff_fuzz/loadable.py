"""Can Nucleation's tick simulator load this canonical state? Decided through the fuzzer's own Nucleation path
(tools.oracle.sim.run_plan on a stone floor), NOT through env/sim.py, so env placement changes cannot move it.
Results are cached in data/fuzz/loadable.json."""
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.diff_fuzz.common import FUZZ_DIR  # noqa: E402
from tools.oracle.trace import Plan  # noqa: E402

_PATH = FUZZ_DIR / "loadable.json"
_CACHE = json.loads(_PATH.read_text()) if _PATH.exists() else {}


def simulatable(state: str) -> bool:
    r = _CACHE.get(state)
    if r is None:
        from tools.oracle import sim
        blocks = [(x, 0, z, "minecraft:smooth_stone", None) for x in range(3) for z in range(3)] + [(1, 1, 1, state, None)]
        try:
            sim.run_plan(blocks, Plan(4, [], [], "place", 0, (0, 100, 0)))
            r = True
        except BaseException as e:      # pyo3 panics
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            r = False
        _CACHE[state] = r
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            _PATH.write_text(json.dumps(_CACHE, sort_keys=True))
        except OSError:
            pass
    return r
