"""Prototype of an order-independent build placement for env (env/ itself is frozen; see docs/VALIDITY.md, section 9).

env/sim.compile_cells places blocks one by one through Nucleation's `Schematic.set_block("...{simulate=true}")` in (y, x, z)
order. That path corrupts redstone-dust shapes: a dust placed later at (+2, ±1..2) of a line end turns the end into a cross
(vanilla 26.2 does not; repro in tests/oracle and docs/ORACLE.md), and bistable cells come up in an order-dependent state.
Both make env verdicts depend on rotation/mirroring of an otherwise identical task.

`canonical_compile` instead writes every state raw, with every redstone dust written as a CROSS (all four sides `side`, power 0:
the state a player's hand placement starts from), and the world is loaded with Nucleation's `Placement` settle, which resolves
shapes after ALL blocks are present (and keeps the ticks that placement scheduled; baking right after the load would drop them). That is what vanilla's structure placement (and therefore a Litematica/structure paste) does; Nucleation's Placement is
tick-identical to vanilla on all T0-T4 references (docs/ORACLE.md). A dust with no neighbours stays a cross (as by hand), a dust
with one or more connections becomes the line/corner/T vanilla computes from the final neighbours, independent of order.

    with patched_env():            # env.runner uses canonical_compile for the build (paste_stable keeps the raw path)
        rr = runner.run(contract, blocks)
"""
from contextlib import contextmanager

from env import sim as env_sim

CROSS = "minecraft:redstone_wire[east=side,north=side,power=0,south=side,west=side]"
_orig_compile = env_sim.compile_cells


def cross_dust(cells: dict) -> dict:
    return {p: CROSS if s.split("[", 1)[0] == "minecraft:redstone_wire" else s for p, s in cells.items()}


_canonical = set()      # ids of schematics made by canonical_compile: env.sim.Sim loads them with Placement


def canonical_compile(cells: dict, nbt=None, engine_place=True):
    """Drop-in for env.sim.compile_cells: (raw Schematic with cross dust, corner, errors); load it with mode "placement"."""
    if not engine_place:
        return _orig_compile(cells, nbt, engine_place=False)
    raw, corner, errors = _orig_compile(cross_dust(cells), nbt, engine_place=False)
    _canonical.add(id(raw))
    return raw, corner, errors


class CanonicalSim(env_sim.Sim):
    def __init__(self, schematic, origin, mode="inworld"):
        super().__init__(schematic, origin, "placement" if id(schematic) in _canonical else mode)


@contextmanager
def patched_env():
    """Temporarily route env.runner's build compilation through canonical_compile + Placement load (process-local, env/ files
    untouched). paste_stable keeps comparing the saved at-rest world loaded InWorld and Placement."""
    from env import runner
    old = runner.compile_cells, runner.Sim
    runner.compile_cells, runner.Sim = canonical_compile, CanonicalSim
    try:
        yield
    finally:
        runner.compile_cells, runner.Sim = old
        _canonical.clear()
