"""1-to-2 decoder cell with enable (demultiplexer): Y0 = EN & !A, Y1 = EN & A.
Y0 = EN - A (subtract comparator), Y1 = EN - Y0 (second comparator, side input = Y0 through a repeater): two comparators
and no inverter. Planar: EN (west) -> Y0, Y1 (east); A (north). Trees of this cell make n-to-2^n decoders."""
from simulation.builder import *


@part
def demux2(p, floor=True):
    if floor:
        p.fill((0, 0, -2), (5, 0, 2), solid())
    # EN fan-out: port at (0,1,1), repeaters keep full strength into both comparators
    p.points(wire(), (0, 1, 0), (0, 1, 1), (0, 1, 2), (2, 1, 2))
    p.set((1, 1, 0), repeater(flow=EAST, delay=1))
    p.set((1, 1, 2), repeater(flow=EAST, delay=1))
    p.set((2, 1, 0), comparator(flow=EAST, mode="subtract"))     # C0 = EN - A
    p.set((3, 1, 0), solid())                                    # hub block powered by C0 (= Y0)
    p.set((4, 1, 0), repeater(flow=EAST, delay=1))
    p.set((5, 1, 0), wire())                                     # Y0
    p.set((3, 1, 1), repeater(flow=SOUTH, delay=1))              # Y0 -> side input of C1
    p.set((3, 1, 2), comparator(flow=EAST, mode="subtract"))     # C1 = EN - Y0
    p.set((4, 1, 2), repeater(flow=EAST, delay=1))
    p.set((5, 1, 2), wire())                                     # Y1
    p.set((2, 1, -1), repeater(flow=SOUTH, delay=1))             # A into the side of C0
    p.set((2, 1, -2), wire())                                    # A
    p.port("EN", (0, 1, 1), kind="in", face=WEST)
    p.port("A", (2, 1, -2), kind="in", face=NORTH)
    p.port("Y0", (5, 1, 0), kind="out", face=EAST)
    p.port("Y1", (5, 1, 2), kind="out", face=EAST)
