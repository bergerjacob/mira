"""Unit tests for simulation/modules.py: modules with ports/specs, Design build (placement + router), checks, fault
localisation (offline; Nucleation through env.sim)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from simulation import modules as M  # noqa: E402
from simulation import router as R  # noqa: E402


def adder2():
    fa = M.Module.lib("full_adder")
    d = M.Design(box=(-5, 0, -5, 50, 7, 30), floor_y=0)
    d.add("fa0", fa, at=(30, 0, 5))
    d.add("fa1", fa, at=(12, 0, 5))
    for i, x in enumerate((36, 18)):
        d.input(f"A{i}", (x - 10, 1, 25))
        d.input(f"B{i}", (x, 1, 25))
    d.input("CIN", (45, 1, 2))
    d.output("S0", "fa0.S")
    d.output("S1", "fa1.S")
    d.output("COUT", "fa1.COUT")
    for i in range(2):
        d.connect(f"A{i}", f"fa{i}.A")
        d.connect(f"B{i}", f"fa{i}.B")
    d.connect("CIN", "fa0.CIN")
    d.connect("fa0.COUT", "fa1.CIN")

    def fn(A0, A1, B0, B1, CIN):
        s = A0 + 2 * A1 + B0 + 2 * B1 + CIN
        return {"S0": s & 1, "S1": (s >> 1) & 1, "COUT": s >> 2}
    d.spec(fn)
    return d


@pytest.fixture(scope="module")
def built():
    d = adder2()
    res = d.build()
    return d, res


# --------------------------------------------------------------------------- modules
def test_lib_module_ports_needs_and_spec():
    fa = M.Module.lib("full_adder")
    assert set(fa.ports) == {"A", "B", "CIN", "S", "COUT"}
    assert fa.ports["CIN"].need == 7 and fa.ports["CIN"].dir == "in" and fa.ports["S"].dir == "out"
    assert fa.inputs == ["A", "B", "CIN"] and fa.outputs == ["S", "COUT"]
    assert fa.eval({"A": 1, "B": 1, "CIN": 0}) == {"S": 0, "COUT": 1}


def test_mux2_is_declared_analog():
    mux = M.Module.lib("mux2")
    assert mux.ports["A"].need == M.ANALOG_NEED and mux.ports["B"].need == M.ANALOG_NEED
    assert mux.ports["Y"].level == M.ANALOG_NEED and mux.ports["S"].need < M.ANALOG_NEED


def test_mux2_output_strength_follows_its_input_strength():
    """The measured fact behind ANALOG: a weak A gives a weak Y (comparators pass the strength through)."""
    from env.sim import Sim, compile_cells
    mux = M.Module.lib("mux2")
    d = M.Design(floor_y=0)
    inst = d.add("u", mux, at=(20, 0, 20))
    d._place()
    readings = {}
    for lead in (0, 6):
        w = dict(inst.cells)
        w.update({(x, 0, z): R.FLOOR for x in range(5, 40) for z in range(5, 40)})
        pos, face = inst.ports["A"]
        for k in range(1, lead + 1):
            w[R.add(pos, R.DIRS[face], k)] = R.DUST
        pad = R.add(pos, R.DIRS[face], lead + 1)
        sim = Sim(*compile_cells(w)[:2], "inworld")
        sim.settle(300)
        sim.place(pad, "minecraft:redstone_block")
        sim.settle(300)
        readings[lead] = int(R.props_of(sim.get(inst.ports["Y"][0]))["power"])
    assert readings[0] == 15 and readings[6] < 15


def test_from_part_module_reads_declared_ports():
    from simulation.builder import part, wire, solid, repeater, EAST

    @part
    def buf(p):
        p.fill((0, 0, 0), (2, 0, 0), solid())
        p.set((0, 1, 0), wire())
        p.set((1, 1, 0), repeater(flow=EAST))
        p.set((2, 1, 0), wire())
        p.port("A", (0, 1, 0), kind="in", face="west")
        p.port("Y", (2, 1, 0), kind="out", face="east")
    m = M.Module.from_part(buf, spec={"inputs": ["A"], "outputs": ["Y"], "rows": [[[0], [0]], [[1], [1]]]})
    assert m.ports["A"].dir == "in" and m.ports["Y"].face == "east" and m.eval({"A": 1}) == {"Y": 1}


def test_sample_rows_is_exhaustive_or_a_deterministic_sample():
    assert len(M.sample_rows(3)) == 8
    a, b = M.sample_rows(11, 64, seed=3), M.sample_rows(11, 64, seed=3)
    assert a == b and len(set(a)) == 64 and (0,) * 11 in a and (1,) * 11 in a


# --------------------------------------------------------------------------- design declaration errors
def test_pin_errors_name_the_problem():
    d = adder2()
    d._place()
    with pytest.raises(ValueError, match="input port"):
        d.pin("fa0.A", "source")
    with pytest.raises(ValueError, match="output port"):
        d.pin("fa0.S", "sink")
    with pytest.raises(ValueError, match="unknown instance"):
        d.pin("zz.A", "sink")
    with pytest.raises(ValueError, match="no port"):
        d.pin("fa0.Q", "sink")


def test_overlapping_instances_are_reported():
    fa = M.Module.lib("full_adder")
    d = M.Design(box=(-5, 0, -5, 40, 7, 30), floor_y=0)
    d.add("a", fa, at=(10, 0, 5))
    d.add("b", fa, at=(12, 0, 5))
    res = d.build()
    assert any("overlap" in e for e in res.errors) and not res.ok


def test_unroutable_net_is_reported_with_a_reason():
    fa = M.Module.lib("full_adder")
    d = M.Design(box=(-5, 0, -5, 40, 7, 30), floor_y=0)
    d.add("fa0", fa, at=(10, 0, 5))
    d.input("X", (30, 1, 28))
    d.keep_clear.append((-5, 0, 20, 40, 7, 27))                   # a wall of keep-clear between the pad and the cell
    d.connect("X", "fa0.A")
    res = d.build(repair=False)
    rep = d.check(nets=False, modules=False, paste=False)
    assert not res.ok and rep.unrouted and "sink" in next(iter(rep.unrouted.values()))


# --------------------------------------------------------------------------- build + check (2-bit adder)
def test_design_builds_and_every_net_routes(built):
    d, res = built
    assert res.ok, res.summary()
    assert len(res.nets) == 6 and all(n.ok for n in res.nets.values())
    assert all(p[1] >= 1 for p in res.router.blocks())             # the floor (y=0) is never written


def test_design_check_passes_nets_modules_vectors_and_paste(built):
    d, _ = built
    rep = d.check()
    assert rep.ok, rep.summary()
    assert len(rep.nets) == 6 and len(rep.modules) == 2 and len(rep.vectors) == 32 and rep.paste[0]


def test_design_contract_passes_the_env_runner(built):
    from env.runner import run
    from env.score import score
    d, _ = built
    c, blocks = d.design_contract()
    sc = score(c, run(c, blocks))
    assert sc.full_pass and sc.reward > 0.99


def test_fault_localisation_names_the_broken_module(built):
    d, res = built
    inst = d.instances["fa1"]
    victim = next(p for p, s in inst.cells.items() if "comparator" in s)
    saved = list(res.build.blocks)
    try:
        res.build.blocks[:] = [b for b in res.build.blocks if tuple(b[:3]) != victim]
        rep = d.check(nets=False, paste=False)
    finally:
        res.build.blocks[:] = saved
    assert not rep.modules["fa1"]["ok"] and rep.modules["fa0"]["ok"]
    assert victim in rep.modules["fa1"]["changed_cells"]
    bad = [v for v in rep.vectors if not v["ok"]]
    assert bad and all(any("fa1" in f for f in v["fault"]) for v in bad)


def test_fault_localisation_names_a_cut_net(built):
    d, res = built
    net = res.nets["fa0.COUT->fa1.CIN"]
    cut = list(net.parent)[len(net.parent) // 2]
    saved = list(res.build.blocks)
    try:
        res.build.blocks[:] = [b for b in res.build.blocks if tuple(b[:3]) != cut]
        rep = d.check(nets=False, modules=False, paste=False)
    finally:
        res.build.blocks[:] = saved
    bad = [v for v in rep.vectors if not v["ok"]]
    assert bad and any(any("fa0.COUT->fa1.CIN" in f for f in v["fault"]) for v in bad)


# --------------------------------------------------------------------------- benchmarks (structure only; the runs are in docs/ROUTER.md)
def test_alu4_design_uses_only_lib_cells_and_router_nets():
    d = M.alu4_design()
    kinds = sorted({i.module.name for i in d.instances.values()})
    assert kinds == ["and2", "full_adder", "mux2", "or2", "xor2"] and len(d.instances) == 28
    assert len(d.nets) == 38 and d.spec_fn is not None
    out = d.spec_fn(**{**{k: 0 for k in d.ins}, "A0": 1, "B0": 1, "OP0": 1, "OP1": 1})   # XOR
    assert out["Y0"] == 0 and out["COUT"] == 0


def test_t4_design_reads_the_task_contract():
    d = M.t4_design("t4_adder16")
    assert d.floor_y == 0 and len(d.instances) == 16
    assert d.ins["A0"].kind == "device" and d.outs["S0"].kind == "device"
    assert len(d.nets) == 16 * 2 + 1 + 16 + 15 + 1


@pytest.mark.slow
def test_random_bench_routed_nets_all_pass_in_the_simulator():
    out = M.bench_random(12, seed=2, size=(24, 8, 24))
    assert out["routed"] >= 8 and out["checked_ok"] == out["routed"] and out["paste_stable"]
