"""OR gate: input repeaters (accept strength >= 1), dust merge, output repeater. A, B (west) -> Y (east)."""
from simulation.builder import *


@part
def or2(p, floor=True):
    if floor:
        p.fill((0, 0, 0), (4, 0, 2), solid())
    p.points(wire(), (0, 1, 0), (0, 1, 2), (2, 1, 0), (2, 1, 1), (2, 1, 2), (4, 1, 1))
    p.points(repeater(flow=EAST, delay=1), (1, 1, 0), (1, 1, 2), (3, 1, 1))
    p.port("A", (0, 1, 0), kind="in", face=WEST)
    p.port("B", (0, 1, 2), kind="in", face=WEST)
    p.port("Y", (4, 1, 1), kind="out", face=EAST)
