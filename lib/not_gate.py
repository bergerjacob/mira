"""NOT gate: torch inverter. A (west) -> Y (east). 4x1x? footprint, 2 ticks."""
from simulation.builder import *


@part
def not_gate(p, floor=True):
    if floor:
        p.fill((0, 0, 0), (3, 0, 0), solid())
    p.set((0, 1, 0), wire())
    p.set((1, 1, 0), solid())                 # block powered by the input dust
    p.set((2, 1, 0), torch(wall=EAST))        # torch on the block's east face: off while the block is powered
    p.set((3, 1, 0), wire())
    p.port("A", (0, 1, 0), kind="in", face=WEST)
    p.port("Y", (3, 1, 0), kind="out", face=EAST)
