"""D flip-flop (positive edge): Q takes the value D has when CLK rises and holds it until the next rising edge.
Master/slave repeaters locked by the clock (the t_flipflop without its feedback ring): CLK high locks the master, CLK low (through a
torch inverter) locks the slave. Q changes about 6 ticks after the rising edge; a D that changes in the very tick of the
edge is not captured (old value wins), so Q of one cell can feed D of the next one (shift registers, accumulators). Keep CLK high / low >= 10 ticks.
Layout derived from the env task t1_t_flipflop (oracle-verified). CLK (west, y=1), D (west) -> Q (east)."""
from simulation.builder import *


@part
def d_flipflop(p, floor=True):
    if floor:
        p.fill((1, 0, 0), (7, 0, 2), solid())
    p.points(wire(), (1, 1, 0), (2, 1, 0), (3, 1, 0), (1, 1, 2), (3, 1, 2), (4, 1, 2), (6, 1, 2))
    p.set((4, 1, 0), solid())
    p.set((5, 1, 0), torch(wall=EAST))
    p.set((2, 1, 1), repeater(flow=SOUTH, delay=1))   # lock of the master (CLK high)
    p.set((5, 1, 1), repeater(flow=SOUTH, delay=1))   # lock of the slave (CLK low)
    p.set((2, 1, 2), repeater(flow=EAST, delay=3))    # master
    p.set((5, 1, 2), repeater(flow=EAST, delay=1))    # slave
    p.port("CLK", (1, 1, 0), kind="in", face=WEST)
    p.port("D", (1, 1, 2), kind="in", face=WEST)
    p.port("Q", (6, 1, 2), kind="out", face=EAST)
