"""1-bit full adder (S = A ^ B ^ CIN, COUT = maj(A, B, CIN)) from subtract comparators, 7 wide x 4 high x 7 deep.

Layout adapted from the redstone-verify `full_adder/cand_comparators` layout (MIT, formally certified steady state):
lever inputs became dust ports, lamp outputs became a conducting block + repeater + dust so that the cell can be wired.
Ports: A (west, y=2), B (south, y=1), CIN (east, y=3), S (south, y=3), COUT (west, y=2)."""
from simulation.builder import *


@part
def full_adder(p, floor=True):
    cmp_w = comparator(flow=WEST, mode="subtract")     # RSL "comparator facing=east"
    cmp_e = comparator(flow=EAST, mode="subtract")
    cmp_n = comparator(flow=NORTH, mode="subtract")
    rep_w, rep_e = repeater(flow=WEST, delay=1), repeater(flow=EAST, delay=1)
    rep_n, rep_s = repeater(flow=NORTH, delay=1), repeater(flow=SOUTH, delay=1)
    st = solid()
    # supports and conducting blocks
    p.fill((1, 0, 4), (1, 0, 6), st)
    p.fill((3, 0, 4), (3, 0, 6), st)
    p.fill((3, 1, 1), (3, 1, 3), st)
    p.fill((5, 1, 1), (5, 1, 3), st)
    p.fill((2, 1, 4), (2, 1, 6), st)
    p.fill((0, 2, 0), (2, 2, 0), st)
    p.fill((4, 2, 0), (6, 2, 0), st)
    p.fill((4, 2, 1), (4, 2, 3), st)
    p.points(st, (0, 0, 5), (4, 0, 5), (4, 0, 6), (2, 1, 2), (6, 1, 2), (0, 1, 3), (1, 1, 3), (0, 1, 4),
             (0, 2, 1), (6, 2, 1), (2, 2, 3), (3, 3, 0))
    # level y=1
    p.set((1, 1, 4), rep_e)
    p.set((3, 1, 4), wire())
    p.points(wire(), (0, 1, 5), (4, 1, 5), (1, 1, 6))
    p.set((1, 1, 5), cmp_e)
    p.set((3, 1, 5), cmp_w)
    p.set((3, 1, 6), rep_w)
    p.set((4, 1, 6), wire())                           # input B
    # level y=2
    p.points(wire(), (3, 2, 1), (2, 2, 2), (6, 2, 2), (1, 2, 3), (5, 2, 3), (2, 2, 5))
    p.set((5, 2, 1), rep_w)
    p.set((3, 2, 2), cmp_e)
    p.set((5, 2, 2), cmp_w)
    p.set((0, 2, 3), cmp_n)
    p.set((3, 2, 3), rep_e)
    p.set((0, 2, 4), wire())                           # input A
    p.set((2, 2, 4), rep_n)
    # level y=3
    p.points(wire(), (0, 3, 0), (1, 3, 0), (2, 3, 0), (5, 3, 0), (6, 3, 0), (0, 3, 1), (4, 3, 2))
    p.set((4, 3, 0), cmp_w)
    p.set((4, 3, 1), rep_n)
    p.set((6, 3, 1), wire())                           # input CIN
    p.set((4, 3, 3), rep_s)
    # outputs: the original lamps are conducting blocks; a repeater + dust leaves each of them
    p.set((0, 2, 2), wire())                           # COUT: dust, ORs the comparator output and the powered block (0,2,1)
    p.points(st, (0, 1, 2), (-1, 1, 2), (-2, 1, 2))
    p.set((-1, 2, 2), repeater(flow=WEST, delay=1))
    p.set((-2, 2, 2), wire())
    p.set((4, 3, 4), st)                               # S block
    p.points(st, (4, 2, 5), (4, 2, 6))
    p.set((4, 3, 5), rep_s)
    p.set((4, 3, 6), wire())
    p.port("A", (0, 2, 4), kind="in", face=WEST)
    p.port("B", (4, 1, 6), kind="in", face=SOUTH)
    p.port("CIN", (6, 3, 1), kind="in", face=EAST)
    p.port("COUT", (-2, 2, 2), kind="out", face=WEST)
    p.port("S", (4, 3, 6), kind="out", face=SOUTH)
