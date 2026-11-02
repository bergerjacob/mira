"""Wire link: a dust run from A at the origin to Y at (dx, dy, dz) built with simulation.builder.wire_path (x first, then z;
one block of climb per horizontal step; stone supports; a repeater after every 14 dust). Verifies turns, climbs and long
runs. Y has the strength left after the run (>= 1; the last dust before a repeater is 2), not a restored 15."""
from simulation.builder import *


@part
def wire_link(p, floor=True, dx=4, dy=0, dz=0, every=15):
    start, end = P(0, 0, 0), P(dx, dy, dz)
    p.set(start + (0, -1, 0), solid())
    wire_path(p, start, end, repeat_every=every)
    first = "east" if dx > 0 else "west" if dx < 0 else "south" if dz > 0 else "north"
    last = ("south" if dz > 0 else "north") if dz else ("east" if dx > 0 else "west")
    p.port("A", start, kind="in", face=opposite(first))
    p.port("Y", end, kind="out", face=last)
