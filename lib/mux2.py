"""2:1 multiplexer: Y = B if S else A. Two subtract comparators face one block; S gates them through a wire (S) and a torch
(!S) on the select block. Layout adapted from redstone-verify `mux2/cand_comparators` (MIT).
A (west), B (east), S (south, y=2) -> Y (north, y=2)."""
from simulation.builder import *


@part
def mux2(p, floor=True):
    if floor:
        p.fill((0, 0, 0), (4, 0, 3), solid())
    p.set((0, 1, 0), wire())                                     # A
    p.set((4, 1, 0), wire())                                     # B
    p.set((1, 1, 0), comparator(flow=EAST, mode="subtract"))     # A - S
    p.set((3, 1, 0), comparator(flow=WEST, mode="subtract"))     # B - !S
    p.set((2, 1, 0), solid())                                    # Y block (strongly powered by either comparator)
    p.set((1, 1, 1), repeater(flow=NORTH, delay=1))
    p.set((3, 1, 1), repeater(flow=NORTH, delay=1))
    p.set((1, 1, 2), wire())
    p.set((2, 1, 2), solid())                                    # select block
    p.set((3, 1, 2), torch(wall=EAST))                           # !S
    p.set((2, 1, 3), solid())
    p.set((2, 2, 3), wire())                                     # S port (dust joins the select-block dust below)
    p.set((2, 2, 2), wire())                                     # on the select block
    p.set((2, 2, 0), wire())                                     # Y, on the Y block
    p.port("A", (0, 1, 0), kind="in", face=WEST)
    p.port("B", (4, 1, 0), kind="in", face=EAST)
    p.port("S", (2, 2, 3), kind="in", face=SOUTH)
    p.port("Y", (2, 2, 0), kind="out", face=NORTH)
