"""init, revisions, overview/view/find/query, diff, log, notes-check."""
import json

import pytest

from env.contract import Contract
from mira_mcp import project as PJ
from tests.project.conftest import NOT_MODULE, NOT_TASK_PROGRAM, write

ROW = '''from simulation.builder import *


@part
def cell(p):
    p.fill((0, 0, 0), (3, 0, 0), stone())
    p.set((0, 1, 0), wire())
    p.set((1, 1, 0), repeater(flow=EAST, delay=DELAY))
    p.fill((2, 1, 0), (3, 1, 0), wire())
    p.port("A", (0, 1, 0), "in", face=WEST)


DELAY = 1


def build(b):
    b.array(cell, 4, (0, 0, 3), name="row")
    b.label("first_row", (0, 0, 0), (3, 1, 0))
'''


def test_init_layout_and_resolve(projects_dir):
    prj, out = PJ.Project.init("demo")
    assert prj.dir == projects_dir / "demo"
    for f in ("project.json", "build.py", "NOTES.md"):
        assert (prj.dir / f).exists()
    assert (prj.dir / "modules").is_dir() and not (prj.dir / "spec.json").exists()
    with pytest.raises(PJ.ProjectError):
        PJ.Project.init("demo")
    assert PJ.resolve("./x").name == "x" and PJ.resolve("demo") == prj.dir
    with pytest.raises(PJ.ProjectError):
        PJ.resolve("bad name!")


def test_init_from_task_writes_a_valid_spec_and_notes():
    prj, out = PJ.Project.init("n", from_task="t0_not")
    c = Contract.from_dict(json.loads((prj.dir / "spec.json").read_text()))
    assert c.id == "t0_not" and not c.validate()
    notes = (prj.dir / "NOTES.md").read_text()
    assert "port:A at (0, 1, 1)" in notes and "port:Y at (7, 1, 1)" in notes
    assert "#   A: in  click at [0, 1, 1]" in (prj.dir / "build.py").read_text()


def test_build_stores_revisions_dedupes_and_snapshots_sources(make):
    p = make(build_py=ROW)
    assert p.build("first").startswith("BUILD OK rev 1")
    assert "unchanged: rev 1" in p.build()
    (p.dir / "build.py").write_text(ROW.replace("DELAY = 1", "DELAY = 2"))
    out = p.build("slower")
    assert out.startswith("BUILD OK rev 2") and "vs rev 1: +0 -0 ~4" in out
    r1 = p.rev(1)
    assert "DELAY = 1" in (r1.dir / "src/build.py").read_text()
    assert len(r1.meta["hash"]) == 64 and r1.meta["blocks"] == 32
    log = p.log()
    assert "rev   1" in log and "slower" in log


def test_diff_summarises_by_module_and_shows_property_changes(make):
    p = make(build_py=ROW)
    p.build()
    (p.dir / "build.py").write_text(ROW.replace("DELAY = 1", "DELAY = 2").replace("4, (0, 0, 3)", "5, (0, 0, 3)"))
    p.build()
    out = p.diff()
    assert "+8 added, -0 removed, ~4 changed" in out
    assert "row[] +8 -0 ~4" in out
    assert "repeater[delay=1->2]" in out
    assert "source files changed: build.py" in out
    assert "rev 1 is the first revision" in p.diff(None, 1)


def test_overview_sections_and_letters(make):
    p = make(build_py=ROW)
    p.build()
    out = p.overview()
    for s in ("PROJECT p", "MODULES", "row[]", "TOP VIEW", "SIDE VIEW", "next:", "labels (1): first_row"):
        assert s in out, s
    assert "build ports (4): row[{0..3}].A in x4 @ x 0 y 1 z 0..9" in out
    assert len(out.splitlines()) <= 80


def test_overview_stays_under_80_lines_for_a_large_many_module_build(make):
    prog = '''from simulation.builder import *


@part
def tile(p, k=0):
    p.fill((0, 0, 0), (7, 3, 7), stone())
    p.fill((0, 4, 0), (7, 4, 7), wire())
    p.set((3, 5, 3), repeater(flow=NORTH))


def build(b):
    for i in range(30):
        b.array(tile, 12, (9, 0, 0), at=(0, 0, 9 * i), name="band%d_" % i, k=i % 3)
    b.fill((0, -1, 0), (200, -1, 300), "minecraft:smooth_stone")
'''
    p = make(build_py=prog)
    out = p.build()
    assert out.startswith("BUILD OK"), out
    assert p.rev().meta["blocks"] > 100000
    ov = p.overview()
    assert len(ov.splitlines()) <= 80, len(ov.splitlines())
    assert "... (+" in ov and "1 char =" in ov
    assert max(len(line) for line in ov.splitlines()) <= 200


def test_view_slice_glyphs_ports_and_clipping(make):
    p = make(build_py=ROW)
    p.build()
    out = p.view(1)
    lines = out.splitlines()
    assert lines[0].startswith("layer y=1  x 0..3")
    assert " +>++" in out                                  # wire, repeater flowing east, wire, wire
    assert "ports on this layer: row[0].A[0, 1, 0]" in out
    clipped = p.view(1, None, None, max_cols=2, max_rows=2)
    assert "CLIPPED" in clipped and "--box" in clipped
    assert "layer y=1  x 2..3" in p.view(1, [2, 0, 3, 9])


def test_find_and_query_report_provenance(make):
    p = make(build_py="from modules.inv import inverter_row\n\n\ndef build(b):\n    inverter_row(b, 1)\n",
             files={"modules/inv.py": NOT_MODULE})
    p.build()
    f = p.find("torch")
    assert "block redstone_wall_torch: 1 e.g. [3, 1, 1]" in f
    assert "source file modules/inv.py: 6 blocks" in p.find("inv")
    assert "no match" in p.find("zzz")
    q = p.query(3, 1, 1)
    assert "redstone_wall_torch[facing=east" in q and "modules/inv.py:7" in q
    assert "13 non-air cells" not in p.query(3, 1, 1, radius=1)
    assert "[2, 1, 1] stone  <- modules/inv.py:6" in p.query(3, 1, 1, radius=1)


def test_notes_check_reports_stale_references(make):
    p = make(build_py=ROW)
    p.build()
    write(p.dir, "NOTES.md", "# notes\n- label:first_row is the input side, cell `row[2]` at (1, 1, 6)\n- port:row[0].A\n")
    assert p.notes_check().startswith("NOTES OK")
    (p.dir / "build.py").write_text(ROW.replace('name="row"', 'name="lane"').replace("first_row", "inputs"))
    p.build()
    out = p.notes_check()
    assert out.startswith("NOTES STALE")
    assert "label:first_row does not exist" in out
    assert "`row[2]` was a port/label/instance/module in an earlier revision" in out
    assert "port:row[0].A does not exist in rev 2 (it existed in an earlier revision)" in out
    write(p.dir, "NOTES.md", "<!-- port:ghost -->\n- empty cell (9, 9, 9)\n")
    out = p.notes_check()
    assert "ghost" not in out and "no block at [9, 9, 9]" in out


def test_task_rules_are_checked_at_build_time():
    prj, _ = PJ.Project.init("rules", from_task="t0_not")
    write(prj.dir, "build.py", "from simulation.builder import *\n\n\ndef build(b):\n    b.set((20, 1, 1), stone())\n")
    out = prj.build()
    assert "task rules: 1 problem(s)" in out and "outside the build box" in out


def test_spec_problems_are_reported_not_raised(make):
    p = make(build_py=ROW)
    p.build()
    write(p.dir, "spec.json", '{"id": "x"}')
    assert PJ.run(p.dir, "test").startswith("error: spec.json: not a contract v2")
    write(p.dir, "spec.json", "{nope")
    assert "invalid JSON" in PJ.run(p.dir, "test")
    assert PJ.run(p.dir, "frobnicate").startswith("error: unknown command")


def test_not_task_project_layout_end_to_end_without_oracle():
    prj, _ = PJ.Project.init("not", from_task="t0_not")
    write(prj.dir, "build.py", NOT_TASK_PROGRAM)
    write(prj.dir, "modules/inv.py", NOT_MODULE)
    assert prj.build().startswith("BUILD OK rev 1")
    ov = prj.overview()
    assert "spec: t0_not (1 in / 1 out)" in ov and "modules: ,=(fixed)" in ov
