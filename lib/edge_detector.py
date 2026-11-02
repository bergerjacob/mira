"""Rising-edge detector / pulse limiter: Y pulses for about one torch+repeater delay when A rises and stays low while A is
held; nothing on the falling edge. Y = A and not A-delayed (NOR of !A and the 3-repeater-tick delayed A).
Layout from the env task t1_edge_detector (oracle-verified). A (west) -> Y (east)."""
from simulation.builder import *


@part
def edge_detector(p, floor=True):
    if floor:
        p.fill((0, 0, 0), (8, 0, 2), solid())
    p.points(wire(), (0, 1, 0), (1, 1, 0), (4, 1, 0), (4, 1, 1), (4, 1, 2), (0, 1, 2), (1, 1, 2), (2, 1, 2), (3, 1, 2), (5, 1, 1), (8, 1, 1))
    p.set((2, 1, 0), solid())
    p.set((3, 1, 0), torch(wall=EAST))                 # !A
    p.set((0, 1, 1), repeater(flow=SOUTH, delay=3))    # delayed A
    p.set((6, 1, 1), solid())
    p.set((7, 1, 1), torch(wall=EAST))
    p.port("A", (0, 1, 0), kind="in", face=WEST)
    p.port("Y", (8, 1, 1), kind="out", face=EAST)
