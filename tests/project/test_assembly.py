"""Import pre-processing: modules/ and lib/ imports become one sandboxed program with a source map."""
from mira_mcp import project as PJ
from tests.project.conftest import write

MOD = '''from simulation.builder import *


@part
def cell(p, floor=True):
    if floor:
        p.fill((0, 0, 0), (2, 0, 0), stone())
    p.set((0, 1, 0), wire())
    p.set((1, 1, 0), repeater(flow=EAST))
    p.set((2, 1, 0), wire())
    p.port("A", (0, 1, 0), "in", face=WEST)
    p.port("Y", (2, 1, 0), "out", face=EAST)
'''

BUILD = '''from simulation.builder import *
from modules.cells import cell


def build(b):
    b.array(cell, 3, (0, 0, 2), name="row")
    b.set((5, 0, 0), stone())
'''


def test_module_import_builds_and_counts_blocks_per_file(make):
    p = make(build_py=BUILD, files={"modules/cells.py": MOD})
    out = p.build()
    assert out.startswith("BUILD OK rev 1"), out
    assert "modules/cells.py 18" in out and "build.py 1" in out
    assert "row[] x3 18" in out
    assert p.rev().meta["files"] == ["modules/cells.py", "build.py"]


def test_import_lines_keep_line_numbers_in_errors(make):
    bad = MOD.replace("p.set((2, 1, 0), wire())", "p.set((2, 1, 0), wire())\n    p.set((3, 1, 0), 'minecraft:redstone_wir')")
    p = make(build_py=BUILD, files={"modules/cells.py": bad})
    out = p.build()
    assert out.startswith("BUILD FAILED"), out
    line = bad.splitlines().index("    p.set((3, 1, 0), 'minecraft:redstone_wir')") + 1
    assert f"modules/cells.py:{line}: unknown_block" in out
    assert not p.revs()


def test_runtime_error_maps_to_build_py_line(make):
    p = make(build_py="from simulation.builder import *\n\n\ndef build(b):\n    b.set((0, 0, 0), stone())\n    x = 1 / 0\n")
    out = p.build()
    assert "build.py:6: runtime: ZeroDivisionError" in out


def test_syntax_error_in_module_is_reported_with_file_and_line(make):
    p = make(build_py=BUILD, files={"modules/cells.py": "def cell(p):\n    return (\n"})
    out = p.build()
    assert "modules/cells.py:" in out and "syntax" in out


def test_missing_module_and_missing_name(make):
    p = make(build_py=BUILD.replace("modules.cells", "modules.nope"))
    assert "cannot find modules/nope.py" in p.build()
    p2 = make("p2", build_py=BUILD.replace("import cell", "import cell, other"), files={"modules/cells.py": MOD})
    assert "modules/cells.py has no top-level name 'other'" in p2.build()


def test_duplicate_names_cycles_plain_imports_and_build_in_module_are_errors(make):
    p = make(build_py=BUILD + "\n\ndef cell(p):\n    pass\n", files={"modules/cells.py": MOD})
    assert "'cell' is already defined in modules/cells.py" in p.build()
    p = make("cyc", build_py="from modules.a import f\n\ndef build(b):\n    f(b)\n",
             files={"modules/a.py": "from modules.b import g\n\ndef f(b):\n    g(b)\n",
                    "modules/b.py": "from modules.a import f\n\ndef g(b):\n    pass\n"})
    assert "import cycle" in p.build()
    p = make("plain", build_py="import modules.cells\n\ndef build(b):\n    pass\n", files={"modules/cells.py": MOD})
    assert "plain `import` of project modules is not supported" in p.build()
    p = make("twobuild", build_py=BUILD, files={"modules/cells.py": MOD + "\n\ndef build(b):\n    pass\n"})
    assert "only build.py may define build()" in p.build()


def test_repo_lib_part_and_project_lib_override(make):
    prog = "from simulation.builder import *\nfrom lib.not_gate import not_gate\n\n\ndef build(b):\n    b.place(not_gate, at=(0, 0, 0))\n"
    p = make(build_py=prog)
    out = p.build()
    assert "lib/not_gate.py 8" in out, out
    assert (p.rev().dir / "src/_repo/lib/not_gate.py").exists()       # revision snapshot is self-contained
    write(p.dir, "lib/not_gate.py", "from simulation.builder import *\n\n\n@part\ndef not_gate(p):\n    p.set((0, 0, 0), glass())\n")
    out = p.build()
    assert "lib/not_gate.py 1" in out and p.rev().n == 2


def test_assembly_source_map_round_trips_every_line(make):
    p = make(build_py=BUILD, files={"modules/cells.py": MOD})
    asm = PJ.Assembly(p.dir)
    assert not asm.errors
    combined = asm.source.splitlines()
    for rel in asm.files:
        text = (p.dir / rel).read_text().splitlines()
        for ln, line in enumerate(text, 1):
            if "import" in line and ("modules." in line or "lib." in line):
                continue
            seg = next(s for s in asm.segments if s.file == rel)
            assert combined[seg.start + ln - 2] == line
            assert asm.where(seg.start + ln - 1) == (rel, ln)


def test_sandbox_still_applies_to_modules(make):
    p = make(build_py=BUILD, files={"modules/cells.py": "import os\n\ndef cell(p):\n    pass\n"})
    out = p.build()
    assert "BUILD FAILED" in out and "import of 'os' is not allowed" in out
