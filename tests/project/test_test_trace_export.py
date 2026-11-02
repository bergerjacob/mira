"""test / module specs / trace / export / structure .nbt / paste-check / CLI / MCP wrappers."""
import json
import os
import subprocess
import sys

import pytest

from mira_mcp import project as PJ
from simulation import export as X
from simulation import views as V
from tests.project.conftest import NOT_MODULE, NOT_TASK_PROGRAM, ROOT, write


@pytest.fixture
def notp():
    prj, _ = PJ.Project.init("not", from_task="t0_not")
    write(prj.dir, "build.py", NOT_TASK_PROGRAM)
    write(prj.dir, "modules/inv.py", NOT_MODULE)
    return prj


def test_test_passes_the_reference_and_records_the_report(notp):
    out = notp.test()                       # builds rev 1 first (sources changed)
    assert out.startswith("TEST rev 1: PASS  public"), out
    assert "R1 STRUCTURE: ok" in out
    rec = json.loads((notp.mira / "last_test.json").read_text())
    assert rec["full_pass"] and rec["rev"] == 1 and rec["verdict"] == "PASS"
    assert "test: PASS" in notp.log()
    assert "last test rev 1: PASS" in notp.overview()


def test_test_fails_a_broken_build_and_shows_the_vector(notp):
    write(notp.dir, "modules/inv.py", NOT_MODULE.replace("torch(wall=EAST)", "torch(wall=WEST)"))
    out = notp.test(fast=True)
    assert out.startswith("TEST rev 1: FAIL"), out
    assert "FAIL" in out and ("vector" in out or "R1 STRUCTURE: FAIL" in out)
    assert notp.revs() == [1]


def test_test_rebuilds_when_sources_change(notp):
    notp.test(fast=True)
    write(notp.dir, "build.py", NOT_TASK_PROGRAM + "\n# comment\n")
    out = notp.test(fast=True)
    assert out.startswith("TEST rev 2: PASS"), out


def test_module_spec_skeleton_and_module_test(make):
    p = make(files={"modules/buf.py": '''from simulation.builder import *
from lib.not_gate import not_gate


@part
def buf(p, floor=True):
    a = p.place(not_gate, at=(0, 0, 0), floor=floor, name="n1")
    b = p.place(not_gate, at=(4, 0, 0), floor=floor, name="n2")
    p.port("A", a["A"], "in", face=WEST)
    p.port("Y", b["Y"], "out", face=EAST)
'''})
    out = p.init_module("buf")
    assert "1 pad inputs" in out and "1 wire_power outputs" in out
    sp = p.dir / "modules/buf.spec.json"
    d = json.loads(sp.read_text())
    assert [q["name"] for q in d["contract"]["ports"]] == ["A", "Y"]
    assert d["contract"]["ports"][0]["pos"] == [-1, 1, 0]          # one cell west of the input port
    d["contract"]["behaviour"] = {"truth_table": {"rows": [
        {"inputs": {"A": 0}, "expect": {"Y": 0}, "public": True, "label": "0"},
        {"inputs": {"A": 1}, "expect": {"Y": 1}, "public": True, "label": "1"}]}}
    sp.write_text(json.dumps(d))
    assert p.test(module="buf").startswith("TEST module buf: PASS")
    d["contract"]["behaviour"]["truth_table"]["rows"][1]["expect"]["Y"] = 0
    d["contract"]["behaviour"]["truth_table"]["rows"][0]["expect"]["Y"] = 1
    sp.write_text(json.dumps(d))
    assert p.test(module="buf").startswith("TEST module buf: FAIL")
    tr = p.trace(["A", "Y"], sets="A=1;A=0", module="buf")
    assert "Y: 0@0 1@4 0@10" in tr, tr                                # two inverters = 4 ticks each way


def test_trace_custom_vectors_and_raw_blocks(notp):
    tr = notp.trace(["A", "Y", "3,1,1", "4,1,1"], sets="A=1;A=0")
    assert "Y: 1@0 0@" in tr and "A: 0@0 1@1" in tr
    assert "(4,1,1): f@0 0@" in tr                                    # dust next to the torch, hex power
    assert "s0: set {'A': 1} -> settled in" in tr
    assert (notp.mira / "traces/last.txt").exists()


def test_trace_test_stimulus_vector_window_and_errors(notp):
    notp.test(public_only=True, fast=True)
    tr = notp.trace(["Y"])
    assert "public vectors in the test's order" in tr and "v0" in tr
    one = notp.trace(["Y"], vector=0)
    assert "showing only v0" in one
    assert "unknown signal" in notp.trace(["Y", "nope"])
    assert notp.trace(["nope"]).startswith("error: nothing to trace")
    assert notp.trace(["Y"], vector=99).startswith("error: vector 99")


def test_trace_idle_without_spec(make):
    p = make(build_py="from simulation.builder import *\n\n\ndef build(b):\n    b.set((0, 0, 0), stone())\n    b.set((0, 1, 0), lever(face='floor', powered=True))\n    b.set((1, 1, 0), wire())\n")
    tr = p.trace(["1,1,0"], ticks=5)
    assert "idle 5 ticks" in tr and "(1,1,0): f@0" in tr


def test_export_litematic_nbt_and_path_check(notp, tmp_path):
    out = notp.export(nbt=str(tmp_path / "not.nbt"))
    assert out.startswith("EXPORT rev 1"), out
    assert "readback ok" in out and "not placement-path sensitive" in out
    rec = json.loads((notp.mira / "exports/last_export.json").read_text())
    lit = rec["litematic"]
    cells = X.read_blocks(lit)
    wire = cells[(5, 1, 1)]                            # dust is exported connected (settled), not as authored
    assert "east=side" in wire and "west=side" in wire
    assert cells[(0, 0, 0)] == "minecraft:smooth_stone"            # spec scenery included
    assert X.read_structure_nbt(str(tmp_path / "not.nbt")) == cells
    assert rec["path_check"]["paste_stable"] and not rec["path_check"]["sensitive"]
    no_fixed = notp.export(litematic=str(tmp_path / "bare.litematic"), include_fixed=False)
    bare = X.read_blocks(str(tmp_path / "bare.litematic"))
    assert "not included" in no_fixed and len(bare) == 6 and "minecraft:smooth_stone" not in bare.values()


def test_export_refuses_a_build_that_never_rests(notp, monkeypatch):
    notp.build()
    monkeypatch.setattr(X, "settle", lambda world, budget=400: (world, {"ticks": budget, "at_rest": False, "place_errors": [], "changed": 0}))
    assert notp.export().startswith("EXPORT REFUSED")


def test_structure_nbt_writer_types_and_block_entities(tmp_path):
    cells = {(0, 0, 0): "minecraft:stone", (1, 0, 0): "minecraft:chest[facing=north,type=single,waterlogged=false]"}
    nbt = {(1, 0, 0): '{Items:[{Slot:0B,count:5,id:"minecraft:redstone"}]}'}
    info = X.write_structure_nbt(cells, str(tmp_path / "s.nbt"), nbt)
    assert info["readback_mismatches"] == 0
    d = X.read_nbt(str(tmp_path / "s.nbt"))
    assert d["DataVersion"] == X.DATA_VERSION and d["size"] == [2, 1, 1]
    chest = next(b for b in d["blocks"] if b["pos"] == [1, 0, 0])
    assert chest["nbt"]["Items"] == [{"Slot": 0, "count": 5, "id": "minecraft:redstone"}]
    assert chest["nbt"]["id"] == "minecraft:chest"
    t = X.from_snbt("{a:1b,b:2s,c:3L,d:1.5f,e:2.5d,f:7,g:[I;1,2],h:true}")
    assert {k: v.tag for k, v in t.v.items()} == {"a": "byte", "b": "short", "c": "long", "d": "float", "e": "double",
                                                  "f": "int", "g": "int_array", "h": "byte"}


def test_views_helpers():
    assert V.compress_names(["A0", "A1", "A2", "A3", "B0", "M"]) == ["A{0..3}", "B0", "M"]
    assert V.group_key("fa3/cell") == "fa#" and V.group_key("row[12]") == "row[]"
    assert V.glyph("minecraft:repeater[delay=1,facing=west,locked=false,powered=false]") == ">"
    assert V.glyph("minecraft:comparator[facing=north,mode=compare,powered=false]") == "V"
    td = V.timing_diagram({"x": list("0" * 150 + "1" * 150)}, {0: "v0"}, width=100)
    assert "1 column = 3 ticks" in td and td.splitlines()[-1].strip().endswith("#")
    cells = {(x, 0, z): "minecraft:stone" for x in range(500) for z in range(300)}
    cells[(250, 1, 150)] = "minecraft:redstone_wire[power=0]"
    lines, note, used = V.top_map(cells, max_cols=60, max_rows=18)
    assert len(lines) <= 19 and note == "1 char = 9x17 blocks (x by z)" and "+" in used


@pytest.mark.skipif(not __import__("tools.oracle.gametest", fromlist=["available"]).available(), reason="vanilla oracle not installed")
def test_paste_check_in_vanilla_passes_baked_and_fails_raw(notp, tmp_path):
    out = notp.paste_check(ticks=20)
    assert out.startswith("PASTE CHECK PASS"), out
    rec = json.loads((notp.mira / "paste_receipt.json").read_text())
    assert rec["exact"] and rec["stable_at_rest"] and rec["rev"] == 1
    from simulation import builder as B
    raw = B.compile("from simulation.builder import *\n\n\ndef build(b):\n    b.fill((0, 0, 0), (4, 0, 0), stone())\n"
                    "    b.set((0, 1, 0), lever(face='floor', facing='east', powered=True))\n    b.fill((1, 1, 0), (3, 1, 0), wire())\n"
                    "    b.set((4, 1, 0), lamp())\n")
    raw.save(str(tmp_path / "raw.litematic"))                          # authored states, NOT settled
    out = notp.paste_check(file=str(tmp_path / "raw.litematic"), ticks=20)
    assert out.startswith("PASTE CHECK FAIL"), out


def test_cli_end_to_end(tmp_path, projects_dir):
    env = {**os.environ, "MIRA_PROJECTS_DIR": str(projects_dir)}
    cli = [sys.executable, str(ROOT / "scripts/mira.py")]
    r = subprocess.run(cli + ["init", "c", "--from-task", "t0_not"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "initialised project 'c'" in r.stdout
    write(projects_dir / "c", "build.py", NOT_TASK_PROGRAM)
    write(projects_dir / "c", "modules/inv.py", NOT_MODULE)
    r = subprocess.run(cli + ["-p", "c", "build"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.startswith("BUILD OK rev 1")
    r = subprocess.run(cli + ["build"], capture_output=True, text=True, env=env, cwd=projects_dir / "c" / "modules")
    assert "unchanged: rev 1" in r.stdout                               # project found from the cwd
    r = subprocess.run(cli + ["-p", "c", "view", "--layer", "1"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "layer y=1" in r.stdout
    r = subprocess.run(cli + ["-p", "nope", "log"], capture_output=True, text=True, env=env)
    assert r.returncode == 1 and r.stdout.startswith("error:")


def test_mcp_project_tools_mirror_the_cli(projects_dir):
    from mira_mcp import server
    assert server.project_init("m", from_task="t0_not").startswith("initialised project 'm'")
    write(projects_dir / "m", "build.py", NOT_TASK_PROGRAM)
    write(projects_dir / "m", "modules/inv.py", NOT_MODULE)
    assert server.project_build("m", "first").startswith("BUILD OK rev 1")
    assert "TOP VIEW" in server.project_overview("m")
    assert server.project_test("m", fast=True).startswith("TEST rev 1: PASS")
    assert "redstone_wall_torch" in server.project_query("m", 3, 1, 1)
    assert "rev   1" in server.project_log("m")
    assert server.project_build("missing").startswith("error:")
    import asyncio
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"project_build", "project_overview", "project_paste_check", "project_trace", "build", "run_spec"} <= names
