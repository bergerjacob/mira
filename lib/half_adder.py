"""Half adder (S = A xor B, C = A and B) from three subtract comparators; layout adapted from the redstone-verify
`half_adder/cand_comparators` layout (MIT, formally certified steady state), with lever inputs / lamp outputs replaced by
dust ports. A (west, y=1), B (east, y=2) -> S (north, y=2), C (west, y=2)."""
from simulation.builder import *


@part
def half_adder(p, floor=True):
    cmp_e = comparator(flow=EAST, mode="subtract")
    cmp_w = comparator(flow=WEST, mode="subtract")
    # supports / ground
    p.points(solid(), (0, 0, 1), (1, 0, 1), (0, 0, 2), (1, 0, 2), (3, 0, 1), (3, 0, 2), (3, 0, 3), (4, 0, 2), (1, 0, 3))
    p.fill((2, 1, 1), (2, 1, 4), solid())           # column X: the shared block powered by both XOR comparators
    p.points(solid(), (4, 1, 3), (3, 1, 4), (4, 1, 4), (2, 1, 0))
    p.set((1, 1, 4), slab(type="top"))              # non-conducting support: a full block would be powered by the wire at (1,1,3)
    # level y=1
    p.set((0, 1, 1), wire())                        # input A (port)
    p.set((0, 1, 2), wire())
    p.set((1, 1, 1), repeater(flow=EAST, delay=1))  # copy of A -> block (2,1,1) and side of the b-comparator
    p.set((1, 1, 2), cmp_e)                         # C1 = A - B
    p.set((3, 1, 2), cmp_w)                         # C2 = B - A
    p.set((4, 1, 2), wire())
    p.set((1, 1, 3), wire())
    p.set((3, 1, 1), wire())
    p.set((3, 1, 3), repeater(flow=WEST, delay=1))
    # level y=2
    p.set((2, 2, 2), wire())                        # on X: carries S
    p.set((2, 2, 1), repeater(flow=NORTH, delay=1))
    p.set((2, 2, 0), wire())                        # output S
    p.set((2, 2, 3), repeater(flow=SOUTH, delay=1))
    p.set((2, 2, 4), cmp_w)                         # C = B - S
    p.set((3, 2, 4), wire())
    p.set((4, 2, 4), wire())
    p.set((4, 2, 3), wire())                        # input B (port)
    p.set((1, 2, 4), repeater(flow=WEST, delay=1))  # restores C to 15 and keeps the output dust apart from the wire at (1,1,3)
    p.set((0, 1, 4), solid())
    p.set((0, 2, 4), wire())                        # output C
    p.port("A", (0, 1, 1), kind="in", face=WEST)
    p.port("B", (4, 2, 3), kind="in", face=EAST)
    p.port("S", (2, 2, 0), kind="out", face=NORTH)
    p.port("C", (0, 2, 4), kind="out", face=WEST)
