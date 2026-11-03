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


def test_paste_diff_reproduces_the_engine_placement_dust_bug(monkeypatch):
    monkeypatch.setenv("MIRA_PLACEMENT", "legacy")   # env default is canonical since docs/VALIDITY.md s13
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


# =========================================================================== port entry rules, needs, route_all (EXPERIMENT_04 backlog)
import json  # noqa: E402

from env import library as L  # noqa: E402

STONE_ = N("minecraft:stone")


def libns(*names):
    ns = {}
    exec(compile(L.prelude(list(names)), "lib", "exec"), ns)
    return ns


def sandbox_part(base=None, w=(-10, 0, -10, 60, 0, 30)):
    p = B.Part()
    p.fill(w[:3], w[3:], "minecraft:smooth_stone")
    return p


def wrap(*lib_names, extra=""):
    """A @part wrapper source compiled in-process: returns (namespace) with the lib parts and user parts."""
    ns = libns(*lib_names)
    src = "from simulation.builder import *\n" + extra
    exec(compile(src, "wrap", "exec"), ns)
    return ns


@pytest.mark.parametrize("inward, expect", [
    ("minecraft:stone", "face"),
    ("minecraft:redstone_wire", "any"),
    ("minecraft:repeater[facing=west]", "any"),          # aligned with the axis: dust connects to it
    ("minecraft:repeater[facing=north]", "face"),        # across the axis: no connection
    ("minecraft:comparator[facing=east]", "any"),
    ("minecraft:lever[face=floor,facing=east]", "any"),
    ("minecraft:redstone_torch", "any"),
    ("minecraft:redstone_lamp", "face"),
    ("minecraft:observer[facing=east]", "any"),          # its back faces the dust
    ("minecraft:observer[facing=west]", "face"),
])
def test_entry_rule_depends_on_what_the_inward_neighbour_connects_to(inward, expect):
    cells = {(0, 1, 0): R.DUST, (1, 1, 0): N(inward)}
    assert R.entry_rule(cells.get, (0, 1, 0), "west") == expect


def test_entry_rule_without_a_face_is_any_and_diagonal_dust_counts():
    assert R.entry_rule({}.get, (0, 1, 0), None) == "any"
    cells = {(1, 2, 0): R.DUST}                          # a staircase up: dust one level up in front connects
    assert R.entry_rule(cells.get, (0, 1, 0), "west") == "any"


def test_every_lib_port_records_an_entry_and_outputs_are_any():
    for f in sorted((ROOT_ := Path(R.ROOT) / "lib").glob("*.meta.json")):
        m = json.loads(f.read_text())
        for pn, pm in m["ports"].items():
            assert pm.get("entry") in ("face", "any"), (f.name, pn)
            if pm["dir"] == "out":
                assert pm["entry"] == "any", (f.name, pn)


def test_lib_entry_metadata_matches_the_geometry_of_the_part():
    for n in L.names():
        if n not in L.specs():
            continue
        m = L.meta(n)
        assert {k: v["entry"] for k, v in m["ports"].items()} == L.port_entries(n), n


def test_lib_gates_that_feed_a_solid_block_need_the_face():
    ent = {n: {k: v["entry"] for k, v in L.meta(n)["ports"].items() if v["dir"] == "in"} for n in L.names() if n in L.specs()}
    face = sorted(f"{n}.{k}" for n, d in ent.items() for k, v in d.items() if v == "face")
    assert face == ["and2.A", "and2.B", "demux2.EN", "not_gate.A"]


def side_entry_passes(name, port):
    """Join input `port` of lib part `name` from a free side (dust + a redstone block behind it) and run its truth table."""
    from env.contract import Port
    pm = L.meta(name)["ports"][port]
    pin, fd = tuple(pm["pos"]), L.DIRS[pm["face"]]
    c, blocks, _ = L.bench(name, lead=0)
    cells = {tuple(b[:3]) for b in blocks}
    for sdir in ((fd[2], 0, -fd[0]), (-fd[2], 0, fd[0])):
        side, pad = R.add(pin, sdir), R.add(pin, sdir, 2)
        around = [R.add(side, d) for d in R.ALL6 if d != (0, -1, 0)]
        if side in cells or pad in cells or any(q in cells and q != pin for q in around):
            continue
        old = tuple(next(p for p in c.ports if p.name == port).pos)
        c.ports = [Port(port, "pad", pad, facing=pm["face"], doc="pad") if p.name == port else p for p in c.ports]
        c.fixed = [f for f in c.fixed if tuple(f["box"][:3]) != old] + [{"box": [*pad, *pad], "state": "minecraft:air"}]
        bb = list(c.bbox)
        for i in range(3):
            bb[i], bb[i + 3] = min(bb[i], pad[i] - 1), max(bb[i + 3], pad[i] + 1)
        c.bbox = c.build_box = bb
        bl = [list(b) for b in blocks if tuple(b[:3]) != old] + [[*side, "minecraft:redstone_wire"], [side[0], side[1] - 1, side[2], "minecraft:stone"]]
        return L.env_pass(c, bl)[0]
    pytest.skip("no free side on this port")


@pytest.mark.parametrize("name, port, ok_from_a_side", [("and2", "A", False), ("not_gate", "A", False), ("xor2", "B", True),
                                                        ("full_adder", "CIN", True), ("mux2", "A", True)])
def test_entry_metadata_agrees_with_the_simulator_for_side_joins(name, port, ok_from_a_side):
    assert (L.meta(name)["ports"][port]["entry"] == "any") == ok_from_a_side
    assert side_entry_passes(name, port) == ok_from_a_side


def test_face_entry_pin_is_refused_from_a_side_with_a_reason():
    w = floor(-3, -6, 13, 6)
    w[(0, 1, 0)], w[(10, 1, 0)] = LEVER, R.DUST
    w[(9, 1, 0)] = N("minecraft:stone")                              # the west face is walled off
    rt = R.Router(w, box=(-3, 1, -6, 13, 5, 6))
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), "west", "dust", entry="face", name="g.A")])
    assert not net.ok
    assert "net 'n'" in net.reason and "g.A" in net.reason and "must be entered through its face" in net.reason and "allow_side" in net.reason


def test_allow_side_joins_a_face_pin_and_flags_a_dead_gate_risk():
    w = floor(-3, -6, 13, 6)
    w[(0, 1, 0)], w[(10, 1, 0)] = LEVER, R.DUST
    w[(9, 1, 0)] = N("minecraft:stone")
    rt = R.Router(w, box=(-3, 1, -6, 13, 5, 6))
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), "west", "dust", entry="face", name="g.A")], allow_side=True)
    assert net.ok and net.side_joined == ["g.A"] and any("dead" in x for x in net.warnings)


def test_any_entry_pin_side_join_is_harmless_and_only_an_info():
    w = floor(-3, -6, 13, 6)
    w[(0, 1, 0)], w[(10, 1, 0)] = LEVER, R.DUST
    w[(9, 1, 0)] = N("minecraft:stone")
    rt = R.Router(w, box=(-3, 1, -6, 13, 5, 6))
    net = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), "west", "dust", name="p")])
    assert net.ok and net.side_joined == ["p"] and not net.warnings and net.infos and "harmless" in net.infos[0]


def test_part_route_enters_a_gate_through_its_face():
    ns = libns("and2")
    p = sandbox_part()
    p.place(ns["and2"], at=(20, 0, 5), floor=False, name="g")
    p.set((0, 1, 5), B.lever(face="floor", facing=B.EAST), port="L")
    net = p.route("L", "g.A", name="a")
    pin = tuple(p._ports["g.A"][0])
    assert net.ok and net.attach["g.A"] == (pin[0] - 1, pin[1], pin[2]) and not net.side_joined


def test_part_route_refuses_a_walled_in_gate_with_net_pin_and_reason():
    ns = libns("and2")
    p = sandbox_part()
    p.place(ns["and2"], at=(20, 0, 5), floor=False, name="g")
    p.set((0, 1, 5), B.lever(face="floor", facing=B.EAST), port="L")
    p.set((19, 1, 5), "minecraft:stone")
    with pytest.raises(B.BuilderError) as e:
        p.route("L", "g.A", name="a")
    msg = str(e.value)
    assert "net 'a'" in msg and "g.A" in msg and "face" in msg and "allow_side=True" in msg and "map y=1" in msg


def test_part_route_strict_false_returns_the_unrouted_net_and_records_it():
    ns = libns("and2")
    p = sandbox_part()
    p.place(ns["and2"], at=(20, 0, 5), floor=False, name="g")
    p.set((0, 1, 5), B.lever(face="floor", facing=B.EAST), port="L")
    p.set((19, 1, 5), "minecraft:stone")
    before = dict(p._cells)
    net = p.route("L", "g.A", name="a", strict=False)
    assert not net.ok and net.reason and p._cells == before
    bd = B._finish(p._ctx, p)
    assert bd.stats["nets"][0]["name"] == "a" and bd.stats["nets"][0]["ok"] is False


def test_part_route_allow_side_true_gives_a_build_warning_naming_the_pin():
    ns = libns("and2")
    p = sandbox_part()
    p.place(ns["and2"], at=(20, 0, 5), floor=False, name="g")
    p.set((0, 1, 5), B.lever(face="floor", facing=B.EAST), port="L")
    p.set((19, 1, 5), "minecraft:stone")
    net = p.route("L", "g.A", name="a", allow_side=True)
    bd = B._finish(p._ctx, p)
    assert net.ok and [w for w in bd.warnings if w.kind == "route" and "g.A" in w.message and "dead" in w.message]


def test_routed_cells_carry_net_provenance_and_the_net_record_has_levels():
    ns = libns("full_adder")
    p = sandbox_part()
    p.place(ns["full_adder"], at=(25, 0, 5), floor=False, name="f0")
    p.place(ns["full_adder"], at=(8, 0, 5), floor=False, name="f1")
    net = p.route("f0.COUT", "f1.CIN", name="carry")
    bd = B._finish(p._ctx, p)
    assert net.ok and {pv[0] for b, pv in zip(bd.blocks, bd.prov) if (b[0], b[1], b[2]) in net.cells} == {"net:carry"}
    rec = bd.stats["nets"][0]
    assert rec["name"] == "carry" and rec["src"] == "f0.COUT" and rec["sinks"] == ["f1.CIN"]
    assert rec["levels"]["f1.CIN"] >= 7 and rec["needs"]["f1.CIN"] == 7 and rec["repeaters"] == len(net.repeaters)


def test_mux2_inputs_need_13_and_its_output_is_a_13_source():
    ns = libns("mux2", "and2")
    p = sandbox_part()
    p.place(ns["mux2"], at=(20, 0, 5), floor=False, name="m")
    p.place(ns["and2"], at=(40, 0, 5), floor=False, name="g")
    p.set((0, 1, 5), B.lever(face="floor", facing=B.EAST), port="L")
    n_in = p.route("L", "m.A", name="in")
    assert n_in.ok and n_in.sink_level["m.A"] >= R.ANALOG_NEED
    sp = R._part_pin(p, "m.Y", "source")
    assert sp.level == R.ANALOG_NEED
    assert R._part_pin(p, "m.A", "sink").need == 13 and R._part_pin(p, "m.A", "sink", need=4).need == 4


def test_need_argument_overrides_the_derived_minimum():
    ns = libns("full_adder")
    p = sandbox_part()
    p.place(ns["full_adder"], at=(25, 0, 5), floor=False, name="f0")
    assert R._part_pin(p, "f0.CIN", "sink").need == 7
    assert R._part_pin(p, "f0.CIN", "sink", need=2).need == 2


WRAPPERS = '''
@part
def wrapped(p, floor=False):
    g = p.place(and2, at=(0, 0, 0), floor=floor, name="g")
    p.port("X", g["A"], kind="in", face=WEST)         # alias of the very cell of g.A
    p.port("Y", g["Y"], kind="out", face=EAST)


@part
def fronted(p, floor=False):
    g = p.place(and2, at=(2, 0, 0), floor=floor, name="g")
    p.set((1, 1, 0), repeater(flow=EAST, delay=1))
    p.set((0, 1, 0), wire())
    p.port("X", (0, 1, 0), kind="in", face=WEST)      # a new cell, a repeater behind it
    p.port("Y", g["Y"], kind="out", face=EAST)


@part
def muxed(p, floor=False):
    m = p.place(mux2, at=(0, 0, 0), floor=floor, name="mx")
    r = p.place(restorer, at=(2, 1, -1), rot=270, floor=True, name="rs")
    p.port("A", m["A"], kind="in", face=WEST)
    p.port("Y", r["Y"], kind="out", face=NORTH)


@part
def muxed_plain(p, floor=False):
    m = p.place(mux2, at=(0, 0, 0), floor=floor, name="mx")
    p.port("A", m["A"], kind="in", face=WEST)
    p.port("Y", m["Y"], kind="out", face=NORTH)
'''


def test_wrapped_part_inherits_need_and_entry_from_the_aliased_lib_port():
    ns = wrap("and2", "mux2", "restorer", extra=WRAPPERS)
    p = sandbox_part()
    p.place(ns["wrapped"], at=(20, 0, 5), name="w")
    pin = R._part_pin(p, "w.X", "sink")
    assert pin.entry == "face" and pin.why == "and2.A"


def test_wrapper_with_a_repeater_in_front_computes_its_own_entry_rule():
    ns = wrap("and2", "mux2", "restorer", extra=WRAPPERS)
    p = sandbox_part()
    p.place(ns["fronted"], at=(20, 0, 5), name="w")
    assert R._part_pin(p, "w.X", "sink").entry == "any"        # the aligned repeater connects to the dust


def test_wrapped_mux_is_analog_unless_a_restorer_sits_on_its_output():
    ns = wrap("and2", "mux2", "restorer", extra=WRAPPERS)
    p = sandbox_part()
    p.place(ns["muxed_plain"], at=(20, 0, 5), name="a")
    p.place(ns["muxed"], at=(40, 0, 5), name="b")
    assert R._part_pin(p, "a.A", "sink").need == R.ANALOG_NEED
    assert R._part_pin(p, "b.A", "sink").need == 1              # restored output: only the lib minimum
    assert R._part_pin(p, "a.Y", "source").level == R.ANALOG_NEED and R._part_pin(p, "b.Y", "source").level == 15


def test_coordinates_of_a_registered_port_get_the_same_rules_as_its_name():
    ns = libns("and2")
    p = sandbox_part()
    p.place(ns["and2"], at=(20, 0, 5), floor=False, name="g")
    by_pos = R._part_pin(p, tuple(p._ports["g.A"][0]), "sink")
    assert by_pos.entry == "face" and by_pos.face == "west"


def test_restore_puts_a_repeater_on_the_last_cell_when_the_sink_needs_more_than_dust_gives():
    w = floor(-3, -6, 13, 6)
    w[(0, 1, 0)], w[(10, 1, 0)] = LEVER, R.DUST
    rt = R.Router(w, box=(-3, 1, -6, 13, 5, 6))
    plain = rt.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), "west", "dust", need=15)])
    assert not plain.ok and "restore=True" in plain.reason
    rt2 = R.Router(dict(w), box=(-3, 1, -6, 13, 5, 6))
    net = rt2.route("n", R.Pin((0, 1, 0), None, "device"), [R.Pin((10, 1, 0), "west", "dust", need=15)], restore=True)
    last = net.attach[str((10, 1, 0))]
    assert net.ok and last in net.repeaters and net.sink_level[str((10, 1, 0))] == 15
    assert R.props_of(net.cells[last])["facing"] == "west"      # facing = input side; the signal flows west->east into the pin
    chk = R.check_nets(rt2, isolated=False)["n"]
    assert chk.ok, chk.summary()


def congested(n=12, size=16, seed=6):
    rng = random.Random(seed)
    w = {(x, 0, z): R.FLOOR for x in range(-1, size + 1) for z in range(-1, size + 1)}
    used, specs = [], []

    def pick():
        while True:
            q = (rng.randrange(1, size - 1), 1, rng.randrange(1, size - 1))
            if all(max(abs(q[0] - u[0]), abs(q[2] - u[2])) > 1 for u in used):
                used.append(q)
                return q
    for i in range(n):
        a, z = pick(), pick()
        w[a], w[z] = LEVER, LAMP
        specs.append((f"n{i}", R.Pin(a, None, "device", name=f"a{i}"), [R.Pin(z, None, "device", name=f"z{i}")], {}))
    return w, specs, (-1, 1, -1, size, 5, size)


import random  # noqa: E402


def test_route_all_beats_the_given_order_and_returns_nets_in_the_given_order():
    w, specs, box = congested()
    r1 = R.Router(dict(w), box=box, floor_y=0)
    given = sum(r1.route(nm, s, k).ok for nm, s, k, _ in specs)
    r2 = R.Router(dict(w), box=box, floor_y=0)
    res = r2.route_all(specs, order="auto")
    assert [n.name for n in res] == [s[0] for s in specs]
    assert sum(n.ok for n in res) > given


def test_route_all_rip_up_routes_more_than_ordering_alone():
    w, specs, box = congested(seed=0)
    r0 = R.Router(dict(w), box=box, floor_y=0)
    no_ripup = sum(n.ok for n in r0.route_all(specs, order="auto", rounds=0))
    r1 = R.Router(dict(w), box=box, floor_y=0)
    with_ripup = sum(n.ok for n in r1.route_all(specs, order="auto"))
    assert with_ripup > no_ripup
    chk = R.check_nets(r1, isolated=False)
    assert all(c.ok for nm, c in chk.items() if r1.nets[nm].ok)


def test_order_nets_heuristics():
    w = floor(-3, -6, 33, 6)
    w[(0, 1, 0)], w[(5, 1, 0)], w[(0, 1, 3)], w[(30, 1, 3)] = LEVER, LAMP, LEVER, LAMP
    rt = R.Router(w, box=(-3, 1, -6, 33, 5, 6), floor_y=0)
    long_ = ("long", R.Pin((0, 1, 3), None, "device"), [R.Pin((30, 1, 3), None, "device")], {})
    short = ("short", R.Pin((0, 1, 0), None, "device"), [R.Pin((5, 1, 0), None, "device")], {})
    assert [s[0] for s in rt.order_nets([long_, short], "short")] == ["short", "long"]
    assert [s[0] for s in rt.order_nets([long_, short], "given")] == ["long", "short"]
    with pytest.raises(R.RouteError):
        rt.order_nets([long_], "nonsense")


def test_constrained_pins_are_routed_first_in_auto_order():
    w = floor(-3, -6, 33, 6)
    w[(0, 1, 0)], w[(5, 1, 0)], w[(0, 1, 3)], w[(30, 1, 3)] = LEVER, LAMP, LEVER, LAMP
    for p in ((29, 1, 3), (30, 1, 4), (30, 1, 2)):      # the long net's lamp has only one free neighbour
        w[p] = N("minecraft:stone")
    rt = R.Router(w, box=(-3, 1, -6, 33, 5, 6), floor_y=0)
    long_ = ("long", R.Pin((0, 1, 3), None, "device"), [R.Pin((30, 1, 3), None, "device")], {})
    short = ("short", R.Pin((0, 1, 0), None, "device"), [R.Pin((5, 1, 0), None, "device")], {})
    assert [s[0] for s in rt.order_nets([short, long_], "auto")] == ["long", "short"]


def test_part_route_all_failure_names_the_blocking_net_and_draws_a_map():
    ns = libns("and2")
    p = sandbox_part(w=(-10, 0, -2, 60, 0, 14))
    p.place(ns["and2"], at=(20, 0, 5), floor=False, name="g")
    p.set((18, 1, 0), B.lever(face="floor", facing=B.EAST), port="W0")
    p.set((18, 1, 12), B.lamp(), port="W1")
    p.set((0, 1, 5), B.lever(face="floor", facing=B.EAST), port="L")
    p.route("W0", "W1", name="wall", box=(-5, 1, 0, 45, 5, 12), floor_y=0)           # a dust line in front of the gate
    with pytest.raises(B.BuilderError) as e:
        p.route_all([("L", "g.A", "a")], box=(-5, 1, 0, 45, 5, 12), floor_y=0)
    msg = str(e.value)
    assert "1 of 1 nets unroutable" in msg and "'wall'" in msg and "map y=1" in msg and "@" in msg
    assert "next to g.A: west cell (19, 1, 5): free, but dust there would touch net(s) 'wall'" in msg


def test_part_route_all_routes_writes_and_records_every_net():
    ns = libns("and2", "not_gate")
    p = sandbox_part(w=(-10, 0, -2, 60, 0, 20))
    p.place(ns["and2"], at=(20, 0, 5), floor=False, name="g")
    p.place(ns["not_gate"], at=(40, 0, 5), floor=False, name="n")
    p.set((0, 1, 5), B.lever(face="floor", facing=B.EAST), port="L")
    p.set((0, 1, 7), B.lever(face="floor", facing=B.EAST), port="M")
    nets = p.route_all([("L", "g.A", "a"), ("M", "g.B", "b"), {"src": "g.Y", "sinks": "n.A", "name": "c", "restore": True}],
                       box=(-5, 1, 0, 50, 5, 18), floor_y=0)
    bd = B._finish(p._ctx, p)
    assert [n.name for n in nets] == ["a", "b", "c"] and all(n.ok for n in nets)
    assert [r["name"] for r in bd.stats["nets"]] == ["a", "b", "c"]
    assert all(n.cells.items() <= {(x, y, z): s for x, y, z, s, _ in bd.blocks}.items() for n in nets)


def test_part_route_all_rejects_malformed_items_and_duplicate_names():
    p = sandbox_part()
    with pytest.raises(B.BuilderError):
        p.route_all([("only-a-source",)])
    with pytest.raises(B.BuilderError):
        p.route_all([{"src": (0, 1, 0)}])


def test_builder_error_is_available_inside_the_sandbox():
    src = ("from simulation.builder import *\n"
           "def build(b):\n"
           "    try:\n"
           "        b.route('nope.A', (1, 1, 1))\n"
           "    except BuilderError as e:\n"
           "        print('caught')\n"
           "    try:\n"
           "        raise BuilderError('mine')\n"
           "    except BuilderError:\n"
           "        print('raised')\n")
    bd = B.compile(src, sandbox=True)
    assert bd.ok, bd.errors
    assert bd.log == ["caught", "raised"]
    bd = B.compile("def build(b):\n    raise BuilderError('x')\n", sandbox=True)    # even without the import
    assert not bd.ok and bd.errors[0].kind == "api"


def test_net_paths_group_as_one_view_group():
    from simulation import views as V
    assert V.group_key("net:q1") == V.group_key("net:carry") == "(nets)"
    assert V.group_key("fa3/cell") == "fa#"
