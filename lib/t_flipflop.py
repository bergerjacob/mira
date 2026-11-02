"""T flip-flop: Q toggles on every rising edge of T (master/slave repeaters locked by a pulse-derived clock).
Q changes 6 ticks after the rising edge of T; in Nucleation pulses of 2..20 ticks work and rising edges 12-16 ticks apart still
toggle (the spec and the oracle only exercise edges >= 50 ticks apart, tighter spacing is not checked in vanilla).
Layout from the env task t1_t_flipflop (oracle-verified). T (west) -> Q (east)."""
from simulation.builder import *


@part
def t_flipflop(p, floor=True):
    if floor:
        p.fill((1, 0, 0), (7, 0, 5), solid())
    p.points(wire(), (1, 1, 0), (2, 1, 0), (3, 1, 0), (1, 1, 2), (3, 1, 2), (4, 1, 2), (6, 1, 2), (6, 1, 3), (6, 1, 4), (6, 1, 5),
             (5, 1, 5), (2, 1, 5), (1, 1, 5), (1, 1, 4), (1, 1, 3), (7, 1, 3))
    p.set((4, 1, 0), solid())
    p.set((5, 1, 0), torch(wall=EAST))
    p.set((2, 1, 1), repeater(flow=SOUTH, delay=1))   # lock of the master
    p.set((5, 1, 1), repeater(flow=SOUTH, delay=1))   # lock of the slave
    p.set((2, 1, 2), repeater(flow=EAST, delay=3))    # master
    p.set((5, 1, 2), repeater(flow=EAST, delay=1))    # slave
    p.set((4, 1, 5), solid())
    p.set((3, 1, 5), torch(wall=WEST))
    p.port("T", (1, 1, 0), kind="in", face=WEST)
    p.port("Q", (7, 1, 3), kind="out", face=EAST)
