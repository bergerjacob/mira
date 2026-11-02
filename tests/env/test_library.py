"""lib/: the component library (builder parts + .meta.json sidecars), its bench/verification machinery and the promoted corpus cells.

Offline (Nucleation only). The vanilla-oracle results recorded in the sidecars are checked for consistency, not re-run here
(`python env/library.py verify --oracle` and tests/oracle cover that)."""
import functools
import json

import pytest

from env import library as L
from env.runner import prepare
from simulation import builder as BD

REQUIRED = {"not_gate", "nor2", "or2", "and2", "xor2", "full_adder", "sr_latch", "t_flipflop", "edge_detector", "delay_line", "mux2",
            "demux2", "restorer", "wire_link"}
NAMES = L.names()


@functools.lru_cache(maxsize=None)
def bench_ok(name, rot=0, mirror=None, lead=0, params=None):
    c, blocks, _ = L.bench(name, rot, mirror, lead, dict(params) if params else None)
    return L.env_pass(c, blocks)


# --------------------------------------------------------------------------- catalogue
def test_catalogue_has_the_required_components():
    assert REQUIRED <= set(NAMES)
    assert len(NAMES) >= 12


@pytest.mark.parametrize("name", NAMES)
def test_every_component_has_source_meta_and_spec(name):
    assert (L.LIB / f"{name}.py").exists() and (L.LIB / f"{name}.meta.json").exists()
    assert name in L.specs()
    m = L.meta(name)
    assert m["name"] == name and m["title"] and m["description"]
    assert m["verified"]["oracle_ok"] is True, f"{name}: vanilla-oracle verification missing in the sidecar"
    assert "rot0" in m["verified"]["env_ok_orientations"]
    assert m["footprint"]["blocks"] > 0 and m["ports"]


@pytest.mark.parametrize("name", NAMES)
def test_meta_ports_match_the_compiled_part(name):
    m = L.meta(name)
    first = (L.specs()[name].get("variants") or [None])[0]
    plist = "".join(f", {k}={v!r}" for k, v in (first or {}).items())
    b = L.compile_program(name, f"def build(b):\n    b.place({name}, at=(0, 0, 0), name='dut'{plist})\n")
    assert b.ok, b.summary()
    got = L.rotated_ports(b)
    assert {k: (list(v["pos"]), v["kind"], v["face"]) for k, v in got.items()} == \
        {k: (v["pos"], v["dir"], v["face"]) for k, v in m["ports"].items()}


@pytest.mark.parametrize("name", NAMES)
def test_component_compiles_in_the_sandbox_and_is_deterministic(name):
    body = f"def build(b):\n    b.place({name}, at=(1, 0, 2))\n"
    s1 = L.compile_program(name, body, sandbox=True)
    s2 = L.compile_program(name, body, sandbox=False)
    assert s1.ok and s1.hash() == s2.hash()


def test_prelude_orders_dependencies_first_and_deduplicates():
    assert L.closure(["not_gate", "nor2", "not_gate"]) == ["not_gate", "nor2"]
    text = L.prelude(["full_adder", "xor2"])
    assert text.count("from simulation.builder import *") == 1
    assert text.index("def full_adder") < text.index("def xor2")


def test_floor_param_only_removes_the_support_plane():
    with_floor = L.compile_program("nor2", "def build(b):\n    b.place(nor2, at=(0, 0, 0))\n")
    without = L.compile_program("nor2", "def build(b):\n    b.place(nor2, at=(0, 0, 0), floor=False)\n")
    keep = [blk for blk in with_floor.blocks if blk[1] > 0]
    assert keep == [blk for blk in without.blocks if blk[1] > 0] and len(with_floor.blocks) > len(without.blocks)


# --------------------------------------------------------------------------- behaviour in the env simulator
@pytest.mark.parametrize("name", [n for n in NAMES if not L.specs()[n].get("variants")])
def test_identity_bench_is_a_full_pass(name):
    func, inv, det = bench_ok(name)
    assert func and inv, det


@pytest.mark.parametrize("variant", [{"ticks": 2}, {"ticks": 20}, {"ticks": 36}])
def test_delay_line_variants(variant):
    func, inv, det = bench_ok("delay_line", params=tuple(variant.items()))
    assert func and inv, det
    c, blocks, _ = L.bench("delay_line", params=variant)
    assert L.characterize(c, blocks)["rise"] == variant["ticks"]


@pytest.mark.parametrize("variant", [{"dx": 4, "dy": 0, "dz": 3}, {"dx": 3, "dy": 3, "dz": 0}, {"dx": 4, "dy": -2, "dz": 3}, {"dx": 24, "dy": 0, "dz": 0}])
def test_wire_path_turns_climbs_and_long_runs(variant):
    func, inv, det = bench_ok("wire_link", params=tuple(variant.items()))
    assert func and inv, det


@pytest.mark.parametrize("name,rot,mirror", [("not_gate", 90, None), ("xor2", 180, None), ("full_adder", 270, "x"), ("crossover", 90, "x"),
                                              ("demux2", 180, "x")])
def test_rotations_and_mirrors_keep_the_function(name, rot, mirror):
    func, inv, det = bench_ok(name, rot, mirror)
    assert func and inv, det
    assert L.orient_name(rot, mirror) in L.meta(name)["verified"]["func_ok_orientations"]


def test_sidecars_record_unsafe_orientations_honestly():
    m = L.meta("t_flipflop")
    assert set(m["verified"]["func_ok_orientations"]) < set(L.orient_name(r, mi) for r, mi in L.ORIENTATIONS)   # rot90 variants fail
    assert all(o["func"] is False for o in m["orientation_failures"])
    func, _, _ = bench_ok("t_flipflop", 90, None)
    assert func is False


def test_input_strength_claims():
    m = L.meta("full_adder")["verified"]["min_in_strength_per_port"]
    assert m["CIN"] == 7 and m["A"] < m["CIN"] and m["B"] < m["CIN"]
    ok = L.bench("full_adder", lead={"CIN": 15 - m["CIN"]})
    assert L.env_pass(ok[0], ok[1])[0]
    bad = L.bench("full_adder", lead={"CIN": 15 - m["CIN"] + 1})
    assert not L.env_pass(bad[0], bad[1])[0]
    nb = L.bench("not_gate", lead=14)
    assert L.env_pass(nb[0], nb[1])[0]                      # strength 1 at the port is enough


def test_broken_component_fails_its_own_spec():
    c, blocks, _ = L.bench("not_gate")
    broken = [b for b in blocks if "torch" not in b[3]]
    assert L.env_pass(c, broken)[0] is False


def test_characterize_reports_delay_and_output_strength():
    c, blocks, _ = L.bench("not_gate")
    t = L.characterize(c, blocks)
    assert t["rise"] == t["fall"] == 2 and t["out_strength"] == {"Y": 15}
    assert L.meta("full_adder")["timing_ticks"]["out_strength"] == {"S": 15, "COUT": 15}


def test_crossover_lines_do_not_leak_into_each_other():
    c, blocks, _ = L.bench("crossover")
    func, inv, det = L.env_pass(c, blocks)
    assert func and inv, det
    rows = {tuple(sorted(v.inputs.items())): v.expect for v in c.vectors()}
    assert rows[(("XW", 1), ("ZN", 0))] == {"XE": 1, "ZS": 0} and rows[(("XW", 0), ("ZN", 1))] == {"XE": 0, "ZS": 1}


def test_bench_structure():
    c, blocks, info = L.bench("nor2", lead=3)
    kinds = {p.name: p.kind for p in c.ports}
    assert kinds == {"A": "pad", "B": "pad", "Y": "wire_power"}
    pr = prepare(c, blocks)
    assert pr.errors == []
    pads = {tuple(f["box"][:3]) for f in c.fixed}
    assert len(pads) == 2 and not (pads & {tuple(b[:3]) for b in blocks})


# --------------------------------------------------------------------------- models registered for the T3 tasks
def test_t3_reference_models():
    from env.models import get_model
    assert get_model("adder_cin")({}, {"A0": 1, "A1": 1, "B0": 1, "B1": 0, "CIN": 0}, bits=2) == {"S0": 0, "S1": 0, "COUT": 1}
    assert get_model("decoder_en")({}, {"EN": 1, "A1": 1, "A0": 0}, bits=2) == {"Y0": 0, "Y1": 0, "Y2": 1, "Y3": 0}
    assert get_model("decoder_en")({}, {"EN": 0, "A1": 1, "A0": 0}, bits=2) == {f"Y{k}": 0 for k in range(4)}
    assert get_model("majority")({}, {"I0": 1, "I1": 0, "I2": 1}, n=3) == {"Y": 1}
    assert get_model("parity")({}, {"I0": 1, "I1": 1, "I2": 1, "I3": 0}, n=4) == {"Y": 1}
    assert get_model("rom_en")({}, {"EN": 1, "A1": 1, "A0": 1}, words=[1, 3, 6, 12], width=4, abits=2) == {"D0": 0, "D1": 0, "D2": 1, "D3": 1}
    s = {}
    seen = []
    for clk in (1, 1, 0, 1, 0, 1, 0, 1):
        seen.append(tuple(get_model("counter_tff")(s, {"CLK": clk}, bits=2).values()))
    assert seen[-1] == (0, 0) and seen[0] == (1, 0)          # 4 rising edges wrap a 2-bit counter
    d = {}
    assert get_model("counter_tff")(d, {"CLK": 1}, bits=2, down=True) == {"Q0": 1, "Q1": 1}


# --------------------------------------------------------------------------- promoted corpus cells
def test_corpus_promotion_index():
    idx = L.corpus_index()
    assert len(idx) >= 100
    for e in idx:
        assert (L.LIB / e["file"]).exists() and (L.LIB / (e["file"] + ".ports.json")).exists()
        prov = e["provenance"]
        assert prov["licence"] == "MIT" and prov["redistributable"] is True and prov["commit"]
        assert e["verified"]["env"] and e["verified"]["oracle_vanilla"] and e["verified"]["oracle_mc"] == "26.2"
    names = [e["name"] for e in idx]
    assert len(set(names)) == len(names)


def test_corpus_cell_roundtrips_through_use_and_the_env():
    idx = {e["name"]: e for e in L.corpus_index()}
    e = idx["claudestone__and__and_comparator_lightless"]
    b = L.compile_program([], f"def build(b):\n    b.use({e['file']!r}, at=(2, 0, 2), name='c')\n")
    assert b.ok and len(b.blocks) == e["blocks"] and "c.out" in b.ports
    row = next(r for r in L.corpus_candidates(80) if r["path"].endswith("and_comparator_lightless.litematic"))
    c, sol, info = L.corpus_contract(row)
    func, inv, det = L.env_pass(c, sol)
    assert func and inv, det


def test_truth_table_parsers():
    assert L.parse_truth_table("a b | y\n0 0 | 0\n1 1 | 1\n") == (["a", "b"], ["y"], [([0, 0], [0]), ([1, 1], [1])])
    ins, outs, rows = L.verilog_truth_table("module m(input a, input b, input c, output y);\n  assign y = (a & b) | ~c;\nendmodule\n",
                                            ["a", "b", "c"], ["y"])
    assert dict((tuple(i), o[0]) for i, o in rows)[(1, 1, 1)] == 1 and dict((tuple(i), o[0]) for i, o in rows)[(0, 1, 1)] == 0
    assert dict((tuple(i), o[0]) for i, o in rows)[(0, 0, 0)] == 1
    assert L.verilog_truth_table("assign y = a + b;", ["a", "b"], ["y"]) is None


def test_scan_summary_is_consistent_with_the_promotions():
    scan = json.loads((L.LIB / "corpus" / "scan.json").read_text())
    passed = [r for r in scan if r["oracle"] and r["oracle"]["vanilla_pass"] and r["oracle"]["nucleation_pass"]]
    assert len(passed) == len(L.corpus_index()) >= 100
    assert all(r["status"] == "env_pass" for r in passed)
    assert BD  # builder imported: the cells are loadable through simulation.builder.use


# --------------------------------------------------------------------------- floor=False (friction fix, 2026-11-02)
def _plane_offset(name):
    return 1 if name == "wire_link" else 0     # wire_link's origin is the DUST level (support plane at its y=-1)


def _place_src(name, floor, extra=""):
    return f"def build(b):\n    b.place({name}, at=(0, {1 if name == 'wire_link' else 0}, 0), floor={floor}{extra})\n"


@pytest.mark.parametrize("name", NAMES)
def test_floor_false_writes_nothing_at_or_below_the_support_plane(name):
    """Tasks have a fixed floor and reject writes outside the build box [.., y=1, ..]: floor=False must not write y=0."""
    variants = L.specs()[name].get("variants") or [{}]
    for params in variants:
        extra = "".join(f", {k}={v!r}" for k, v in params.items())
        if params.get("dy", 0) < 0:
            continue
        build = L.compile_program(name, _place_src(name, False, extra))
        assert build.ok, build.errors
        low = [blk[:3] for blk in build.blocks if blk[1] <= 0]
        assert low == [], f"{name}{params} writes at y<=0: {low[:4]}"
        assert min(blk[1] for blk in build.blocks) >= 1


@pytest.mark.parametrize("name", [n for n in NAMES if n != "wire_link"])
def test_floor_false_is_floor_true_minus_the_plane(name):
    with_floor = L.compile_program(name, _place_src(name, True))
    without = L.compile_program(name, _place_src(name, False))
    assert [b for b in with_floor.blocks if b[1] > 0] == without.blocks
    assert len(with_floor.blocks) > len(without.blocks) or name == "delay_line"


def test_wire_link_with_a_downhill_route_and_no_floor_stays_above_the_plane_of_its_lowest_dust():
    build = L.compile_program("wire_link", "def build(b):\n    b.place(wire_link, at=(0, 5, 0), floor=False, dx=4, dy=-2, dz=3)\n")
    assert build.ok and min(b[1] for b in build.blocks) >= 5 - 2    # the plane under the lowest dust (y=2) is not written
    dust = [b for b in build.blocks if "redstone_wire" in b[3]]
    assert min(b[1] for b in dust) == 3


@pytest.mark.parametrize("name", ["full_adder", "half_adder", "xor2"])
def test_floor_false_parts_work_on_an_external_floor(name):
    """The three parts that used to write y=0 regardless: bench them with floor=False standing on a separate floor."""
    c, blocks, _ = L.bench(name, params={"floor": False})
    have = {(b[0], b[1], b[2]) for b in blocks}
    x1, _, z1, x2, _, z2 = c.bbox
    floor = [[x, 0, z, "minecraft:stone"] for x in range(x1, x2 + 1) for z in range(z1, z2 + 1) if (x, 0, z) not in have]
    func, inv, detail = L.env_pass(c, blocks + floor)
    assert func and inv, detail


def test_t3_references_compile_to_the_stored_solutions_after_the_wire_path_upgrade():
    """wire_path gained boxes, avoid sets, flat starts at components and junction-safe repeaters; the stored T3 references must not move."""
    from env.tasks import author_t3 as A
    from env.task import load_task
    for plan in A.build_all():
        plan.compile_solution()
        stored = load_task(A.OUT / f"{plan.id}.json").solution
        assert sorted(map(tuple, plan.solution)) == sorted(map(tuple, stored)), plan.id
