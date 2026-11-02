"""RS latch (two cross-coupled NOR torches). A pulse on S makes Q 1 and it stays; a pulse on R clears it. S and R must not
be 1 together. Inputs enter through repeaters (diodes) so the input lines do not see the latch loop.
R (west), S (east) -> Q (east). Layout from the env task t1_rs_latch (oracle-verified)."""
from simulation.builder import *


@part
def sr_latch(p, floor=True):
    if floor:
        p.fill((0, 0, 1), (8, 0, 3), solid())
    p.points(solid(), (4, 1, 1), (4, 1, 3))
    p.set((5, 1, 1), torch(wall=EAST))                 # Q torch (on the block fed by the R side of the loop)
    p.set((3, 1, 3), torch(wall=WEST))                 # !Q torch
    p.points(wire(), (6, 1, 1), (7, 1, 1), (6, 1, 2), (6, 1, 3), (5, 1, 3), (2, 1, 3), (2, 1, 2), (2, 1, 1), (3, 1, 1))
    p.set((1, 1, 2), repeater(flow=EAST, delay=1))     # R in
    p.set((0, 1, 2), wire())
    p.set((7, 1, 3), repeater(flow=WEST, delay=1))     # S in
    p.set((8, 1, 3), wire())
    p.port("R", (0, 1, 2), kind="in", face=WEST)
    p.port("S", (8, 1, 3), kind="in", face=EAST)
    p.port("Q", (7, 1, 1), kind="out", face=EAST)
