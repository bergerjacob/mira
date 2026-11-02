"""AND gate: NOR of the two inverted inputs (three torches). A, B (west) -> Y (east)."""
from simulation.builder import *


@part
def and2(p, floor=True):
    if floor:
        p.fill((0, 0, 0), (7, 0, 2), solid())
    for z in (0, 2):
        p.set((0, 1, z), wire())
        p.set((1, 1, z), solid())
        p.set((2, 1, z), torch(wall=EAST))
    p.points(wire(), (3, 1, 0), (3, 1, 1), (3, 1, 2), (4, 1, 1), (7, 1, 1))
    p.set((5, 1, 1), solid())
    p.set((6, 1, 1), torch(wall=EAST))
    p.port("A", (0, 1, 0), kind="in", face=WEST)
    p.port("B", (0, 1, 2), kind="in", face=WEST)
    p.port("Y", (7, 1, 1), kind="out", face=EAST)
