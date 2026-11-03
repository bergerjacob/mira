"""EXPERIMENT_04 backlog: build logs, nets, settled views, axis slices, trace windows, notes ranges, R4 n/a, spec-check, verify-vanilla."""
import json
import subprocess
import sys

import pytest

from mira_mcp import project as PJ
from mira_mcp import project_checks as C
from tests.project.conftest import NOT_MODULE, NOT_TASK_PROGRAM, ROOT, write

GATE_PROGRAM = '''from simulation.builder import *
from lib.and2 import and2


def build(b):
    b.fill((-2, 0, -2), (40, 0, 14), solid())
    b.place(and2, at=(20, 0, 5), floor=False, name="g")
    b.set((0, 1, 5), lever(face="floor", facing=EAST), port="L")
    b.set((0, 1, 7), lever(face="floor", facing=EAST), port="M")
    print("routing the gate inputs")
    b.route("L", "g.A", name="a")
    b.route("M", "g.B", name="b")
'''

WALLED = GATE_PROGRAM.replace('    print("routing', '    b.set((19, 1, 5), stone())\n    print("routing')


@pytest.fixture
def gate(make):
    return make("gate", build_py=GATE_PROGRAM)


@pytest.fixture
def notp():
    prj, _ = PJ.Project.init("not", from_task="t0_not")
    write(prj.dir, "build.py", NOT_TASK_PROGRAM)
    write(prj.dir, "modules/inv.py", NOT_MODULE)
    return prj


def set_spec(prj, mutate):
    sp = prj.dir / "spec.json"
    d = json.loads(sp.read_text())
    mutate(d)
    sp.write_text(json.dumps(d))
    return d


# --------------------------------------------------------------------------- build output
def test_build_shows_print_output_and_a_net_summary(gate):
    out = gate.build()
    assert out.startswith("BUILD OK rev 1"), out
    assert "build log (1 line printed by the program):" in out and "| routing the gate inputs" in out
    assert "nets: 2/2 routed" in out


def test_build_log_is_capped(make):
    prog = "from simulation.builder import *\n\n\ndef build(b):\n    for i in range(120):\n        print('line', i)\n    b.set((0, 0, 0), stone())\n"
    p = make("noisy", build_py=prog)
    out = p.build()
    assert "120 lines printed" in out and "first 40" in out and out.count("  | line") == 40


def test_build_failure_shows_log_router_reason_and_area_map(make):
    p = make("walled", build_py=WALLED)
    out = p.build()
    assert out.startswith("BUILD FAILED") and "net 'a'" in out and "g.A" in out and "allow_side=True" in out
    assert "map y=1" in out and "routing the gate inputs" in out


def test_build_warns_when_a_face_pin_was_joined_from_a_side(make):
    p = make("side", build_py=WALLED.replace('name="a")', 'name="a", allow_side=True)'))
    out = p.build()
    assert out.startswith("BUILD OK"), out
    assert "WARNING route build.py:" in out and "g.A" in out and "dead" in out
    assert "1 with warnings" in out


def test_build_flags_unrouted_nets_of_a_non_strict_route(make):
    p = make("lax", build_py=WALLED.replace('name="a")', 'name="a", strict=False)'))
    out = p.build()
    assert out.startswith("BUILD OK") and "nets: 1/2 routed" in out and "WARNING route: 1 net(s) UNROUTED (strict=False): a" in out
    assert "UNROUTED" in p.nets() and "must be entered through its face" in p.nets(name="a")


def test_build_timeout_option_and_a_clear_message(make):
    p = make("slow", build_py="from simulation.builder import *\n\n\ndef build(b):\n    while True:\n        pass\n")
    out = p.build(timeout=2)
    assert out.startswith("BUILD FAILED") and "exceeded its 2s limit" in out and "--timeout" in out and "exit -24" not in out


def test_cli_build_accepts_timeout_and_nets_runs(gate):
    r = subprocess.run([sys.executable, str(ROOT / "scripts/mira.py"), "-p", str(gate.dir), "build", "--timeout", "30"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and "BUILD OK" in r.stdout, r.stdout + r.stderr
    r = subprocess.run([sys.executable, str(ROOT / "scripts/mira.py"), "-p", str(gate.dir), "nets"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and "NETS rev 1: 2 net(s)" in r.stdout


# --------------------------------------------------------------------------- nets / overview / find
def test_nets_lists_every_routed_net(gate):
    gate.build()
    out = gate.nets()
    assert out.startswith("NETS rev 1: 2 net(s)") and "g.A=" in out and "g.B=" in out and "build.py:" in out
    assert "L -> g.A" in out and "M -> g.B" in out


def test_nets_filter_and_single_net_detail(gate):
    gate.build()
    one = gate.nets(name="a")
    assert "1 net(s)" in one and "delay (repeater ticks) per sink" in one and "and2.A" in one
    assert gate.nets(name="zzz").startswith("no net matches")


def test_nets_warnings_only_and_without_nets(make):
    p = make("side", build_py=WALLED.replace('name="a")', 'name="a", allow_side=True)'))
    p.build()
    out = p.nets(warnings_only=True)
    assert "1 net(s)" in out and "! net 'a'" in out and "dead" in out
    q = make("plain", build_py="from simulation.builder import *\n\n\ndef build(b):\n    b.set((0, 0, 0), stone())\n")
    q.build()
    assert q.nets().startswith("no routed nets")


def test_overview_groups_router_wiring_by_net_not_root(gate):
    gate.build()
    ov = gate.overview()
    line = next(x for x in ov.splitlines() if x.strip().startswith("(nets)"))
    assert "2 routed nets" in line and "`nets`" in line
    root = next((x for x in ov.splitlines() if x.strip().startswith("(root)")), "")
    assert "build.py" in root and "build.py 4" not in root          # only the floor / levers live in (root), not the wiring
    assert gate.find("a").count("net a:") == 1 or "net a:" in gate.find("g.A")


def test_find_matches_nets_by_name_and_endpoint(gate):
    gate.build()
    assert "net b: M -> g.B" in gate.find("g.B")
    assert "net a:" in gate.find("L")


def test_query_names_the_net_that_wrote_a_cell(gate):
    gate.build()
    rev = gate.rev()
    cell = next(b for b, (path, _) in zip(rev.data["blocks"], rev.data["prov"]) if path == "net:a")
    assert "[net:a]" in gate.query(cell[0], cell[1], cell[2])


# --------------------------------------------------------------------------- views
def test_view_axis_slices_and_errors(notp):
    notp.build()
    x = notp.view(axis="x", at=3)
    assert x.startswith("slice x=3") and "z 0..2 (columns)" in x
    z = notp.view(axis="z", at=1)
    assert z.startswith("slice z=1") and "x 0.." in z
    assert notp.view(layer=1).startswith("layer y=1")
    assert notp.view(axis="y", at=1).startswith("layer y=1")
    assert notp.view().startswith("error:") and notp.view(axis="w", at=1).startswith("error:")


def test_settled_view_query_and_raw_vs_settled_diff(notp):
    notp.build()
    raw = notp.query(3, 1, 1)
    sett = notp.query(3, 1, 1, settled=True)
    assert sett.startswith("SETTLED world") and "(raw: " not in raw
    q = notp.query(5, 1, 1, settled=True)                                  # dust behind the lit torch
    assert "redstone_wire[power=14]" in q and "(raw: redstone_wire[power=0])" in q
    assert "(raw differs:" in notp.query(1, 1, 1, settled=True)            # same power, but the connections changed
    v = notp.view(layer=1, settled=True)
    assert v.startswith("SETTLED world") and "@" in v                      # the lamp is lit at rest
    d = notp.diff(raw_vs_settled=True)
    assert "settling changed" in d and "redstone_wire" in d and "power=0->1" in d
    assert "redstone_lamp" in d                                           # lit=false -> true


def test_settled_world_is_cached_per_revision(notp):
    notp.build()
    r = notp.rev()
    notp.settled(r)
    files = list((notp.mira / "cache").glob("settled-1-*.json.gz"))
    assert len(files) == 1
    cells, info = notp.settled(r)
    assert info["at_rest"] and cells


SEQ_SPEC = {"name": "toggle", "events": [{"t": 5, "set": {"A": 1}}, {"t": 40, "set": {"A": 0}}],
            "expect": [{"port": "Y", "value": 1, "t1": 0, "t2": 3, "mode": "all"}, {"port": "Y", "value": 0, "t1": 25, "t2": 35, "mode": "all"}],
            "duration": 80, "public": True}


def test_trace_seq_honours_ticks_and_from_to(notp):
    notp.build()
    set_spec(notp, lambda d: d["behaviour"].update(truth_table=None, sequences=[SEQ_SPEC]))
    full = notp.trace(["Y"], seq="toggle")
    assert "sequence toggle" in full and "first" not in full.split("\n")[0]
    cut = notp.trace(["Y"], seq="toggle", ticks=20)
    assert "first 20 of 80 ticks" in cut.split("\n")[0]
    win = notp.trace(["Y"], seq="toggle", from_tick=10, to_tick=60)
    assert "window ticks 10..60 of 61" in win.split("\n")[0]
    assert notp.trace(["Y"], seq="toggle", from_tick=500).startswith("error:")


# --------------------------------------------------------------------------- notes-check
def test_notes_check_accepts_ranges_and_flags_missing_members(make):
    prog = ("from simulation.builder import *\nfrom lib.not_gate import not_gate\n\n\ndef build(b):\n"
            "    for i in range(4):\n        b.place(not_gate, at=(0, 0, 6 * i), name=f'ff{i}')\n")
    p = make("rng", build_py=prog)
    write(p.dir, "NOTES.md", "# n\nflip-flops inst:ff0..ff3 and `ff0..ff3` and inst:ff0..3 exist.\n")
    p.build()
    assert p.notes_check().startswith("NOTES OK")
    write(p.dir, "NOTES.md", "# n\nbits inst:ff0..ff5 here.\n")
    out = p.notes_check()
    assert "NOTES STALE" in out and "inst:ff4 (from ff0..ff5)" in out and "inst:ff5" in out and "inst:ff3" not in out


def test_notes_check_treats_spec_boxes_as_known_cells(notp):
    notp.build()
    write(notp.dir, "NOTES.md", "# n\nthe hallway (3, 2, 1) stays empty.\n")
    assert "no block at [3, 2, 1]" in notp.notes_check()
    set_spec(notp, lambda d: d.update(keep_clear=[[2, 2, 0, 4, 2, 2]]))
    assert notp.notes_check().startswith("NOTES OK")
    set_spec(notp, lambda d: d.update(keep_clear=[], fixed=d["fixed"] + [{"box": [2, 2, 0, 4, 2, 2], "state": "minecraft:air"}]))
    assert notp.notes_check().startswith("NOTES OK")


# --------------------------------------------------------------------------- R4
def test_test_prints_r4_n_a_when_the_spec_has_no_reference(notp):
    set_spec(notp, lambda d: d.update(reference={}))
    out = notp.test(fast=True)
    assert "FULL PASS" in out and "R4=n/a" in out and "R4 n/a: spec has no reference size" in out and "reward 1.000" in out
    rec = json.loads((notp.mira / "last_test.json").read_text())
    assert rec["r4_na"] is True and rec["r"][4] == 1.0                      # machine-readable: r4 stays 1.0, r4_na says it is not earned


def test_test_prints_a_number_for_r4_when_there_is_a_reference(notp):
    out = notp.test(fast=True)
    assert "R4=n/a" not in out and "R4=1.00" in out
    assert json.loads((notp.mira / "last_test.json").read_text())["r4_na"] is False


# --------------------------------------------------------------------------- spec-check
def test_spec_check_schema_errors_are_readable(notp):
    def bad(d):
        d["behvaiour"] = d.pop("behaviour")
        d["build_box"] = [[0, 1, 0, 7, 2, 2], [0, 1, 0, 7, 2, 2]]
        d["ports"][0]["kind"] = "button"
        d["ports"][1]["pos"] = [50, 1, 1]
        d["ports"][1]["extra"] = 1
        d["ports"].append({"name": "A", "kind": "lamp", "pos": [1, 1, 1]})
    set_spec(notp, bad)
    out = notp.spec_check()
    assert out.startswith("SPEC-CHECK FAIL") and "unknown key 'behvaiour' (did you mean 'behaviour'?)" in out
    assert "build_box must be 6 integers" in out and "single box" in out
    assert "kind 'button' is not one of" in out and "outside bbox" in out and "unknown key 'extra'" in out and "duplicate port name 'A'" in out


def test_spec_check_catches_unknown_ports_and_bad_values_in_behaviour(notp):
    def bad(d):
        d["behaviour"]["truth_table"]["rows"][0]["inputs"] = {"Q": 1}
        d["behaviour"]["truth_table"]["rows"][1]["expect"] = {"Y": 2}
        d["behaviour"]["sequences"] = [{"name": "s", "events": [{"t": 99, "set": {"A": 1}}], "expect": [{"port": "Y", "value": 1, "t1": 0, "t2": 3, "mode": "some"}], "duration": 10}]
    set_spec(notp, bad)
    out = notp.spec_check()
    assert "unknown port 'Q'" in out and "value must be 0 or 1" in out and "t=99 is outside 0..10" in out and "mode must be 'all' or 'any'" in out


def test_spec_check_invalid_json_and_missing_spec(notp, make):
    (notp.dir / "spec.json").write_text("{not json")
    assert notp.spec_check().startswith("SPEC-CHECK FAIL: spec.json is not valid JSON")
    p = make("nospec")
    assert p.spec_check().startswith("error: no spec.json")


def test_spec_check_good_spec_reports_coverage_and_mutation(notp):
    out = notp.spec_check(mutants=4)
    assert out.splitlines()[0].startswith("SPEC-CHECK"), out
    assert "schema ok" in out and "coverage: vectors: 4 (2 public, 2 hidden), 2/2 input combinations (100%)" in out
    assert "inputs (1): A 1^/1v" in out and "outputs (1): Y 4 checks (0:2/1:2)" in out
    assert "mutation: " in out and "mutants of the build killed by the spec" in out


def test_spec_check_vacuity_findings(notp):
    def vac(d):
        d["behaviour"]["truth_table"]["rows"] = [{"inputs": {"A": 0}, "expect": {"Y": 0}, "public": True, "label": "const"},
                                                {"inputs": {"A": 1}, "expect": {"Y": 0}, "public": False, "label": "const1"}]
    set_spec(notp, vac)
    out = notp.spec_check(no_mutation=True)
    assert out.startswith("SPEC-CHECK WARN")
    assert "output 'Y' is only ever expected to be 0" in out
    assert "no pair of vectors differing only in 'A' expects a different output" in out
    assert "mutation: skipped" in out


def test_spec_check_flags_an_input_that_is_never_driven_and_an_unchecked_output(notp):
    def vac(d):
        d["ports"].append({"name": "B", "kind": "click", "pos": [0, 1, 1]})
        d["ports"].append({"name": "Z", "kind": "lamp", "pos": [7, 1, 1]})
    set_spec(notp, vac)
    out = notp.spec_check(no_mutation=True)
    assert "input 'B' is never driven" in out and "output 'Z' is never checked" in out


def test_spec_check_port_cell_findings_for_the_wrong_block():
    c_dict = {"id": "x", "title": "x", "tier": "T", "description": "d", "bbox": [0, 0, 0, 3, 2, 3], "build_box": [0, 1, 0, 3, 2, 3],
              "ports": [{"name": "I", "kind": "click", "pos": [0, 1, 0]}, {"name": "O", "kind": "lamp", "pos": [3, 1, 0]},
                        {"name": "P", "kind": "pad", "pos": [0, 1, 2]}],
              "behaviour": {"truth_table": {"rows": [{"inputs": {"I": 1}, "expect": {"O": 1}}]}}}
    from env.contract import Contract
    c = Contract.from_dict(c_dict)
    got = C.port_cell_findings(c, {(0, 1, 0): "minecraft:stone", (3, 1, 0): "minecraft:redstone_lamp[lit=false]", (0, 1, 2): "minecraft:stone"})
    assert len(got) == 2 and "holds stone, not a lever" in got[0] and "pad cell must be empty" in got[1]


def test_spec_check_empty_build_passes_is_a_finding(notp):
    def vac(d):
        d["behaviour"]["truth_table"]["rows"] = [{"inputs": {"A": 0}, "expect": {"Y": 0}, "public": True, "label": "0"}]
        d["fixed"] = [f for f in d["fixed"] if "lamp" not in f["state"]]       # no lamp: Y is 0 in an empty world
    set_spec(notp, vac)
    out = notp.spec_check(no_mutation=True)
    assert "an EMPTY build passes this spec" in out or "holds air, expected redstone_lamp" in out


def test_mutation_survivors_are_reported_with_hints(notp):
    from env.contract import Contract
    contract, _ = notp.contract()
    rev = notp.rev() if notp.revs() else (notp.build() and notp.rev())
    blocks = notp._for_contract(contract, [tuple(b) for b in rev.data["blocks"]])
    # a spec that only checks A=0 -> Y=1 lets most breakages of the inverter through when A stays 0? no: it kills torch removal;
    res = C.mutation(contract, blocks, max_mutants=6, seed=0, budget_s=120)
    assert res["tried"] == 6 and len(res["killed"]) + len(res["structural"]) + len(res["survivors"]) == 6
    text = "\n".join(C.mutation_lines(res, {}))
    assert "mutants of the build killed by the spec" in text


def test_mutation_is_skipped_when_the_build_fails_its_own_spec(notp):
    write(notp.dir, "modules/inv.py", NOT_MODULE.replace("torch(wall=EAST)", "torch(wall=WEST)"))
    out = notp.spec_check(mutants=2)
    assert "mutation: skipped - the current build does not pass its own spec" in out


def test_apply_mutant_keeps_block_entity_nbt():
    blocks = [(0, 1, 0, "minecraft:chest[facing=north]", "{Items:[]}"), (1, 1, 0, "minecraft:repeater[delay=1,facing=east]", None)]
    out = C.apply_mutant(blocks, [("set", (1, 1, 0), "minecraft:repeater[delay=2,facing=east]")])
    assert [0, 1, 0, "minecraft:chest[facing=north]", "{Items:[]}"] in out and any("delay=2" in b[3] for b in out)


# --------------------------------------------------------------------------- verify-vanilla
oracle = pytest.mark.skipif(not __import__("tools.oracle.gametest", fromlist=["available"]).available(), reason="vanilla oracle not installed")


@oracle
def test_verify_vanilla_replays_the_spec_and_agrees_with_the_env(notp):
    out = notp.verify_vanilla(max_vectors=4)
    assert out.startswith("VERIFY-VANILLA rev 1: PASS"), out
    assert "4/4 checks pass in vanilla 26.2" in out and "case origin: env full pass yes" in out
    rec = json.loads((notp.mira / "vanilla_receipt.json").read_text())
    assert rec["verdict"] == "PASS" and rec["rev"] == 1


@oracle
def test_verify_vanilla_flags_a_build_that_fails_in_vanilla_and_samples_big_tables(notp):
    write(notp.dir, "modules/inv.py", NOT_MODULE.replace("torch(wall=EAST)", "torch(wall=WEST)"))
    out = notp.verify_vanilla(max_vectors=2, second_origin=True)
    assert out.startswith("VERIFY-VANILLA rev 1: FAIL"), out
    assert "vectors replayed 2/4" in out and "second origin" in out and "FAIL vector #" in out


def test_verify_vanilla_text_reports_unavailable_and_mismatch_classes():
    assert C.vanilla_text({"available": False}, 3).startswith("VERIFY-VANILLA UNAVAILABLE")
    res = {"available": True, "seconds": 1.0, "cases": [{"case": "origin", "vectors_total": 2, "vectors_run": 2, "sampled": False, "env_pass": True}],
           "captures": [{"case": "origin", "origin": "env origin", "capture": "vectors", "ticks": 10, "origin_abs": [0, 0, 0],
                         "labels": {0: ({"A": 1}, {"Y": 0}), 1: ({"A": 0}, {"Y": 1})},
                         "rows": [(0, True, False, False), (1, False, True, True)]}]}
    text = C.vanilla_text(res, 3)
    assert text.startswith("VERIFY-VANILLA rev 3: FAIL  1/2 checks pass") and "1 pass in the env but FAIL in vanilla" in text
    assert "1 fail in the env but pass in vanilla" in text and "FAIL vector #0 set {A=1} expect {Y=0}" in text
