"""Two-signal crossover: X runs west -> east at y=1 under the Z signal, which hops over it at y=3 (dust staircase, repeater on a
block above the X dust). The block above the X dust is NOT powered (a repeater does not power the block it stands on), so the
lines do not leak into each other. XW (west) -> XE (east); ZN (north) -> ZS (south). Z is restored to 15 by the repeater."""
from simulation.builder import *


@part
def crossover(p, floor=True):
    if floor:
        p.fill((0, 0, -1), (4, 0, 5), solid())
    p.fill((0, 1, 2), (4, 1, 2), wire())                  # X line (ports XW, XE at its ends)
    st = solid()
    p.set((2, 1, -1), wire())                             # ZN
    p.set((2, 1, 0), st)
    p.set((2, 2, 0), wire())
    p.set((2, 2, 1), st)
    p.set((2, 3, 1), wire())
    p.set((2, 2, 2), st)                                  # block above the X dust: blocks any connection from it
    p.set((2, 3, 2), repeater(flow=SOUTH, delay=1))
    p.set((2, 2, 3), st)
    p.set((2, 3, 3), wire())
    p.set((2, 1, 4), st)
    p.set((2, 2, 4), wire())
    p.set((2, 1, 5), wire())                              # ZS
    p.port("XW", (0, 1, 2), kind="in", face=WEST)
    p.port("XE", (4, 1, 2), kind="out", face=EAST)
    p.port("ZN", (2, 1, -1), kind="in", face=NORTH)
    p.port("ZS", (2, 1, 5), kind="out", face=SOUTH)
