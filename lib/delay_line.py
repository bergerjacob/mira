"""Repeater delay line: Y follows A after `ticks` game ticks (an even number >= 2; chains of repeaters with delay 4, 3, ...).
A (west) -> Y (east). Length = number of repeaters + 2."""
from simulation.builder import *


@part
def delay_line(p, floor=True, ticks=8):
    n = max(1, -(-ticks // 8))                       # repeaters needed (each up to 4 * 2 = 8 ticks)
    per = [ticks // 2 // n + (1 if i < (ticks // 2) % n else 0) for i in range(n)]
    if floor:
        p.fill((0, 0, 0), (n + 1, 0, 0), solid())
    p.set((0, 1, 0), wire())
    for i, d in enumerate(per):
        p.set((1 + i, 1, 0), repeater(flow=EAST, delay=d))
    p.set((n + 1, 1, 0), wire())
    p.port("A", (0, 1, 0), kind="in", face=WEST)
    p.port("Y", (n + 1, 1, 0), kind="out", face=EAST)
