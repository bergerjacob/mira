"""Signal-strength restorer: a repeater turns any input strength >= 1 into a fresh 15 (2 ticks). A (west) -> Y (east)."""
from simulation.builder import *


@part
def restorer(p, floor=True):
    if floor:
        p.fill((0, 0, 0), (2, 0, 0), solid())
    p.set((0, 1, 0), wire())
    p.set((1, 1, 0), repeater(flow=EAST, delay=1))
    p.set((2, 1, 0), wire())
    p.port("A", (0, 1, 0), kind="in", face=WEST)
    p.port("Y", (2, 1, 0), kind="out", face=EAST)
