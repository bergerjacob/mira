"""Unit tests for simulation/router.py (offline; the simulator checks use Nucleation through env.sim)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from simulation import builder as B  # noqa: E402
from simulation import router as R  # noqa: E402
from simulation.blockstates import normalize as N  # noqa: E402

LEVER = N("minecraft:lever[face=floor,facing=east]")
LAMP = N("minecraft:redstone_lamp")


def floor(x1, z1, x2, z2, y=0):
    return {(x, y, z): R.FLOOR for x in range(x1, x2 + 1) for z in range(z1, z2 + 1)}


def lever_lamp(dist=10, extra=None, box_h=6):
    w = floor(-3, -6, dist + 3, 6)
    w[(0, 1, 0)] = LEVER
    w[(dist, 1, 0)] = LAMP
    w.update(extra or {})
    rt = R.Router(w, box=(-3, 1, -6, dist + 3, box_h, 6))
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((dist, 1, 0), None, "device")])
    return rt, net


def neighbours6(p):
    return [R.add(p, d) for d in R.ALL6]


# --------------------------------------------------------------------------- block knowledge
def test_holds_only_full_blocks_and_top_slabs():
    assert R.holds(N("minecraft:stone"))
    assert R.holds(N("minecraft:smooth_stone_slab[type=top]"))
    assert not R.holds(N("minecraft:smooth_stone_slab[type=bottom]"))
    assert not R.holds(N("minecraft:glass"))
    assert not R.holds(LAMP)                 # a lamp is a component: never used as a support
    assert R.full(N("minecraft:stone")) and not R.full(N("minecraft:smooth_stone_slab[type=top]"))


def test_strongly_powered_blocks_of_components():
    assert R.strongly_powers((0, 1, 0), N("minecraft:repeater[facing=west]")) == [(1, 1, 0)]   # flow east -> powers east
    assert R.strongly_powers((0, 1, 0), N("minecraft:redstone_torch")) == [(0, 2, 0)]
    assert R.strongly_powers((0, 1, 0), LEVER) == [(0, 0, 0)]                                     # floor lever: block below
    assert R.strongly_powers((0, 1, 0), N("minecraft:lever[face=wall,facing=east]")) == [(-1, 1, 0)]


def test_pin_validation():
    with pytest.raises(R.RouteError):
        R.Pin((0, 0, 0), face="up")
    with pytest.raises(R.RouteError):
        R.Pin((0, 0, 0), kind="lamp")
    assert R.Pin((0, 0, 0), "east").sides() == ["east"]
    assert R.Pin((0, 0, 0), "east", any_side=True).sides()[0] == "east"


def test_lib_need_reads_measured_min_input_strength():
    assert R.lib_need("full_adder", "CIN") == 7
    assert R.lib_need("full_adder", "A") == 2
    assert R.lib_need("no_such_part", "A") == 1


# --------------------------------------------------------------------------- routing rules (static)
def test_simple_route_has_support_under_every_cell_and_writes_only_free_cells():
    world0 = floor(-3, -6, 13, 6)
    rt, net = lever_lamp()
    assert net.ok, net.reason
    for c in net.parent:
        below = R.add(c, (0, -1, 0))
        st = rt.state(below)
        assert st is not None and R.holds(st), (c, st)
    assert not set(net.cells) & set(world0)                      # nothing pre-existing was overwritten
    assert all(rt.box[1] <= p[1] <= rt.box[4] for p in net.cells)


def test_route_stays_in_box_or_fails_with_a_reason():
    w = floor(-3, -6, 13, 6)
    w[(0, 1, 0)], w[(10, 1, 0)] = LEVER, LAMP
    for x in range(-3, 14):                                       # a wall the box does not let the route climb over
        if x not in (0, 10):
            w[(x, 1, 2)] = N("minecraft:stone")
    w[(5, 1, 0)] = N("minecraft:stone")
    w.update({(5, y, 0): N("minecraft:stone") for y in (2, 3)})
    rt = R.Router(w, box=(-3, 1, -1, 13, 3, 1))                   # 3 cells deep, the stone column blocks z=0
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), None, "device")])
    assert all(R._inside(p, rt.box) for p in net.cells)
    if not net.ok:
        assert "sink" in net.reason or "source" in net.reason


def test_unroutable_net_strict_raises_and_part_route_raises_builder_error():
    w = floor(-1, -1, 3, 1)
    w[(0, 1, 0)], w[(2, 1, 0)] = LEVER, LAMP
    w[(1, 1, 0)] = N("minecraft:stone")
    rt = R.Router(w, box=(0, 1, 0, 2, 1, 0))                      # one-row box: the only cell between them is taken
    with pytest.raises(R.RouteError):
        rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((2, 1, 0), None, "device")], strict=True)
    p = B.Part()
    p.set((0, 1, 0), "minecraft:stone")
    with pytest.raises(B.BuilderError, match="route:"):
        p.route((0, 1, 0), (0, 1, 0))


def test_route_never_touches_foreign_dust():
    extra = {(5, 1, z): R.DUST for z in range(-6, 7)}            # a foreign dust line across the way
    extra.update({(5, 0, z): R.FLOOR for z in range(-6, 7)})
    rt, net = lever_lamp(extra=extra)
    assert net.ok, net.reason
    own = set(net.parent)
    for c in own:
        for q in neighbours6(c):
            assert not (R.is_dust(rt.state(q)) and q not in own), (c, q)
        for d in R.DIRS.values():                                 # no diagonal step that would connect
            for dy in (1, -1):
                q = R.add(R.add(c, d), (0, dy, 0))
                assert not (R.is_dust(rt.state(q)) and q not in own and q != R.add(c, (0, dy, 0))), (c, q)


def test_crossing_goes_over_on_a_non_conducting_slab():
    extra = {(5, 1, z): R.DUST for z in range(-6, 7)}
    rt, net = lever_lamp(extra=extra)
    over = [c for c in net.parent if c[0] == 5]
    assert over and all(c[1] >= 3 for c in over)                # a bridge over the line
    assert all(rt.state(R.add(c, (0, -1, 0))) != N("minecraft:stone") or c[1] > 3 for c in over)


def test_repeaters_only_on_straight_flat_cells_and_flow_forward():
    rt, net = lever_lamp(dist=30)
    assert net.ok and net.repeaters, net.summary()
    for c in net.repeaters:
        par = net.parent[c]
        kid = [k for k, v in net.parent.items() if v == c]
        assert par is not None and len(kid) == 1
        assert par[1] == c[1] == kid[0][1] and R._sub(c, par) == R._sub(kid[0], c)
        flow = R.dir_name(R._sub(c, par))
        assert R.props_of(net.cells[c])["facing"] == R.OPP[flow]  # Minecraft facing points at the input side


def test_strength_model_keeps_every_sink_above_its_need():
    w = floor(-3, -6, 33, 6)
    w[(0, 1, 0)] = LEVER
    w[(30, 1, 0)] = R.DUST                                        # a dust input port that needs strength 7
    rt = R.Router(w, box=(-3, 1, -6, 33, 5, 6))
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((30, 1, 0), "west", "dust", need=7)])
    assert net.ok and net.sink_level[str((30, 1, 0))] >= 7
    assert all(v >= 1 for v in net.strength.values())


def test_staircase_air_cells_are_reserved_for_later_nets():
    rt, net = lever_lamp(extra={(5, 1, z): R.DUST for z in range(-6, 7)})
    assert net.air and net.air <= rt.air
    assert all(rt.state(p) is None for p in net.air)
    assert not any(rt.free(p) for p in net.air)


def test_keep_clear_box_is_never_written():
    w = floor(-3, -6, 13, 6)
    w[(0, 1, 0)], w[(10, 1, 0)] = LEVER, LAMP
    rt = R.Router(w, box=(-3, 1, -6, 13, 5, 6), keep_clear=[(4, 1, -1, 6, 5, 1)])
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), None, "device")])
    assert net.ok
    assert not [p for p in net.cells if 4 <= p[0] <= 6 and -1 <= p[2] <= 1]


def test_multi_sink_tree_has_one_root_and_reaches_every_sink():
    w = floor(-3, -8, 20, 8)
    w[(0, 1, 0)] = LEVER
    lamps = [(12, 1, -5), (15, 1, 0), (12, 1, 5)]
    for p in lamps:
        w[p] = LAMP
    rt = R.Router(w, box=(-3, 1, -8, 20, 5, 8))
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin(p, None, "device") for p in lamps])
    assert net.ok, net.reason
    assert sum(1 for v in net.parent.values() if v is None) == 1
    chk = R.check_nets(rt)["n"]
    assert chk.ok, chk.summary()
    assert all(v == 1 for v in chk.sinks.values())


def test_unroute_releases_cells_and_air():
    rt, net = lever_lamp(extra={(5, 1, z): R.DUST for z in range(-6, 7)})
    cells = set(net.cells)
    rt.unroute("n")
    assert not cells & set(rt.world) and not net.air & rt.air and "n" not in rt.nets


def test_dust_port_falls_back_to_another_side_when_its_face_is_blocked():
    w = floor(-3, -6, 13, 6)
    w[(0, 1, 0)] = LEVER
    w[(10, 1, 0)] = R.DUST
    w[(9, 1, 0)] = N("minecraft:stone")                           # the west face is walled off
    rt = R.Router(w, box=(-3, 1, -6, 13, 5, 6))
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), "west", "dust")])
    assert net.ok and net.note


# --------------------------------------------------------------------------- simulator checks
def test_check_net_powers_the_lamp_and_reports_no_leak():
    rt, net = lever_lamp(dist=24, extra={(8, 1, z): R.DUST for z in range(-6, 7)})
    chk = R.check_nets(rt, isolated=False)["n"]
    assert chk.ok, chk.summary()
    assert chk.sinks == {str((24, 1, 0)): 1} and chk.low == {str((24, 1, 0)): 0}


def test_check_net_detects_a_leak_into_foreign_dust():
    rt, net = lever_lamp()
    first = next(iter(net.parent))                                # foreign dust glued to the route afterwards
    side = R.add(first, (0, 0, 1)) if R.add(first, (0, 0, 1)) not in net.cells else R.add(first, (0, 0, -1))
    rt.world[side] = R.DUST
    chk = R.check_nets(rt, isolated=False)["n"]
    assert not chk.ok and any(p == side for p, _, _ in chk.leaks)


def test_check_net_detects_a_cut_route():
    rt, net = lever_lamp()
    mid = list(net.parent)[len(net.parent) // 2]
    del rt.world[mid]
    net.cells.pop(mid)
    chk = R.check_nets(rt, isolated=False)["n"]
    assert not chk.ok and "weak/unpowered" in chk.detail


def test_paste_diff_reproduces_the_engine_placement_dust_bug():
    """docs/ROUTER.md: env engine placement mis-shapes a lever stub when dust is placed later at (+2, +2)."""
    w = floor(-3, -3, 5, 5)
    w[(0, 1, -1)] = N("minecraft:lever[face=floor,facing=north]")
    w[(0, 1, 0)] = R.DUST
    ok_alone, _, _ = R.paste_diff(dict(w))
    w[(2, 1, 2)] = R.DUST
    ok, details, cells = R.paste_diff(w)
    assert ok_alone and not ok and (0, 1, 0) in cells and details


def test_repair_paste_reroutes_the_net_next_to_a_bad_cell(monkeypatch):
    rt, net = lever_lamp(extra={(5, 1, z): R.DUST for z in range(-6, 7)})
    bad = list(net.parent)[3]
    calls = []

    def fake(world, settle=400):
        calls.append(1)
        return (len(calls) > 1, ["fake"], [] if len(calls) > 1 else [bad])
    monkeypatch.setattr(R, "paste_diff", fake)
    ok, _ = rt.repair_paste()
    assert ok and len(calls) == 2
    new = rt.nets["n"]
    assert new.ok and bad not in new.parent and not rt.ban


# --------------------------------------------------------------------------- Part.route (builder integration)
def test_part_route_in_the_sandbox_between_lib_ports():
    from env import library as L
    body = """
def build(b):
    b.fill((0, 0, 0), (40, 0, 20), solid())
    f0 = b.place(full_adder, at=(25, 0, 5), floor=False, name="f0")
    f1 = b.place(full_adder, at=(8, 0, 5), floor=False, name="f1")
    n = b.route("f0.COUT", "f1.CIN", name="carry")
    print(n.summary())
"""
    bd = L.compile_program(["full_adder"], body, sandbox=True)
    assert bd.ok, bd.errors
    assert "carry" in bd.log[0] and "f1.CIN=" in bd.log[0]
    level = int(bd.log[0].split("f1.CIN=")[1].split()[0].rstrip(","))
    assert level >= 7                                            # the measured min input strength of CIN


def test_part_route_error_is_reported_as_an_api_error():
    src = "from simulation.builder import *\ndef build(b):\n    b.set((0,1,0), stone())\n    b.route('nope.Y', (5,1,0))\n"
    bd = B.compile(src, sandbox=False)
    assert not bd.ok and bd.errors[0].kind == "api" and "route" in bd.errors[0].message
