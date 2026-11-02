"""XOR gate from two subtract comparators facing one block (Y = A xor B); the half adder without its carry stage
(layout adapted from redstone-verify `half_adder/cand_comparators`, MIT). A (west, y=1), B (east, y=2) -> Y (north, y=2)."""
from simulation.builder import *


@part
def xor2(p, floor=True):
    p.points(solid(), (0, 0, 1), (1, 0, 1), (0, 0, 2), (1, 0, 2), (3, 0, 1), (3, 0, 2), (3, 0, 3), (4, 0, 2), (1, 0, 3))
    p.fill((2, 1, 1), (2, 1, 3), solid())            # X column: A hub, XOR block, B hub
    p.points(solid(), (4, 1, 3), (2, 1, 0))
    p.set((0, 1, 1), wire())                          # input A (port)
    p.set((0, 1, 2), wire())
    p.set((1, 1, 1), repeater(flow=EAST, delay=1))
    p.set((1, 1, 2), comparator(flow=EAST, mode="subtract"))   # A - B
    p.set((3, 1, 2), comparator(flow=WEST, mode="subtract"))   # B - A
    p.set((4, 1, 2), wire())
    p.set((1, 1, 3), wire())
    p.set((3, 1, 1), wire())
    p.set((3, 1, 3), repeater(flow=WEST, delay=1))
    p.set((2, 2, 2), wire())                          # on the XOR block
    p.set((2, 2, 1), repeater(flow=NORTH, delay=1))
    p.set((2, 2, 0), wire())                          # output Y
    p.set((4, 2, 3), wire())                          # input B (port)
    p.port("A", (0, 1, 1), kind="in", face=WEST)
    p.port("B", (4, 2, 3), kind="in", face=EAST)
    p.port("Y", (2, 2, 0), kind="out", face=NORTH)
