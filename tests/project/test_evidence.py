"""A result belongs to a source/spec/engine/file, not merely a revision number."""
import json
import shutil
from pathlib import Path

import pytest

from mira_mcp import evidence as E
from mira_mcp import project as PJ
from simulation import export as X
from tests.project.conftest import NOT_MODULE, NOT_TASK_PROGRAM, write


@pytest.fixture
def p():
    p, _ = PJ.Project.init("evidence", from_task="t0_not")
    write(p.dir, "build.py", NOT_TASK_PROGRAM)
    write(p.dir, "modules/inv.py", NOT_MODULE)
    return p


def read(p, rel):
    return json.loads((p.mira / rel).read_text())


def test_spec_only_edit_stales_a_passing_test_without_a_rebuild(p):
    assert ": PASS" in p.test(fast=True)
    assert "test: CURRENT PASS" in p.status()
    n = p.rev().n
    d = json.loads((p.dir / "spec.json").read_text())
    d["description"] += " new user requirement"
    write(p.dir, "spec.json", json.dumps(d))
    assert "test: STALE PASS" in p.status()
    assert "PASS STALE" in p.overview()
    assert p._current().n == n  # spec is evidence input, not Python input


def test_fast_test_explicitly_disclaims_determinism(p):
    p.test(fast=True)
    assert "determinism not checked" in p.status()


def test_notes_do_not_stale_behaviour_evidence(p):
    p.test(fast=True)
    write(p.dir, "NOTES.md", "Changed wording only")
    assert "test: CURRENT PASS" in p.status()


def test_source_edit_stales_even_when_blocks_would_be_identical(p):
    p.test(fast=True)
    write(p.dir, "build.py", NOT_TASK_PROGRAM + "\n# edit\n")
    assert "sources STALE" in p.status()
    assert "test: STALE PASS" in p.status()


def test_imported_repo_cell_changes_source_hash(make, tmp_path, monkeypatch):
    lib = tmp_path / "repo-lib"
    write(lib, "cell.py", "from simulation.builder import *\n@part\ndef cell(p):\n    p.set((0,0,0), stone())\n")
    monkeypatch.setattr(PJ, "REPO_LIB", lib)
    p = make(build_py="from lib.cell import cell\ndef build(b):\n    b.place(cell)\n")
    a = p.source_hash()
    write(lib, "cell.py", (lib / "cell.py").read_text() + "\n# dependency edit\n")
    assert p.source_hash() != a


def test_engine_and_placement_stale_results(p, monkeypatch):
    p.test(fast=True)
    monkeypatch.setenv("MIRA_PLACEMENT", "legacy")
    assert "test: STALE PASS" in p.status()


def test_legacy_unbound_pass_is_stale(p):
    p.build()
    (p.mira / "last_test.json").write_text(json.dumps({"rev": 1, "verdict": "PASS"}))
    assert "test: STALE PASS" in p.status()


def test_export_reuse_depends_on_spec_and_options(p):
    p.export(include_fixed=False)
    file, rec = p._export_record()
    assert rec["options"] == {"include_fixed": True, "force": False}
    assert "minecraft:smooth_stone" in X.read_blocks(file).values()
    old = rec["binding"]["id"]
    d = json.loads((p.dir / "spec.json").read_text())
    d["title"] += " changed"
    write(p.dir, "spec.json", json.dumps(d))
    _, rec = p._export_record()
    assert rec["binding"]["id"] != old


def test_replaced_file_is_not_reused_as_original_export(p):
    p.export()
    file, rec = p._export_record()
    file.write_bytes(b"tampered")
    assert "export: STALE" in p.status()
    file, new = p._export_record()
    assert E.artifact_matches(p.dir, new["artifact"])
    assert file.read_bytes() != b"tampered"


def test_receipts_survive_moving_project(p, tmp_path):
    p.export()
    rec = read(p, "exports/last_export.json")
    assert not Path(rec["artifact"]["file"]).is_absolute()
    moved = tmp_path / "moved"
    shutil.copytree(p.dir, moved)
    q = PJ.Project(moved)
    file, qrec = q._export_record()
    assert file.is_relative_to(moved)
    assert qrec["binding"] == rec["binding"]


def test_export_invalid_spec_fails_closed(p):
    p.build()
    write(p.dir, "spec.json", "{broken json")
    out = PJ.run(p.dir, "export")
    assert out.startswith("error:") and "invalid JSON" in out
    assert not (p.mira / "exports/last_export.json").exists()


def test_export_rejects_fixed_conflicts(p):
    write(p.dir, "build.py", NOT_TASK_PROGRAM.replace("    inverter_row", "    b.set((0,0,0), 'minecraft:gold_block')\n    inverter_row"))
    assert p.export().startswith("EXPORT REFUSED: task rules:")


def test_public_test_scope_is_not_a_full_test_claim(p):
    p.test(public_only=True, fast=True)
    assert read(p, "last_test.json")["scope"] == "public-only; fast: determinism not checked"
    assert "[public-only; fast: determinism not checked]" in p.status()


def test_export_rejects_authored_payloads(make):
    p = make(build_py='def build(b):\n    b.set((0,0,0), "minecraft:chest", "{Items:[]}")\n')
    assert "NBT-free" in p.export()


def test_paste_cannot_certify_payloads(tmp_path):
    f = tmp_path / "chest.litematic"
    X.save_litematic({(0, 0, 0): "minecraft:chest"}, str(f), {(0, 0, 0): "{Items:[]}"})
    with pytest.raises(ValueError, match="payload"):
        X.paste_check(f)


@pytest.mark.parametrize("ticks,modes", [(0, ("place",)), (-1, ("place",)), (20, ()), (20, ("bogus",)), (20, ("place", "place"))])
def test_invalid_paste_options_are_rejected_before_oracle(ticks, modes):
    with pytest.raises(ValueError, match="ticks must be positive"):
        X.paste_check("missing.litematic", ticks=ticks, modes=modes)


def test_explicit_external_file_requires_coordinate_mapping(p, tmp_path):
    p.build()
    out = p.verify_vanilla(file=str(tmp_path / "elsewhere.litematic"))
    assert out.startswith("error: --file needs --origin")


def test_vanilla_consumes_loaded_artifact_not_regenerated_world(p, monkeypatch):
    from mira_mcp import project_checks as C
    p.export()
    file, rec = p._export_record()
    # Change the delivered file while leaving the source intact. Verification must see it.
    cells = X.read_blocks(file)
    cells[(7, 1, 1)] = "minecraft:stone"
    X.save_litematic(cells, str(file))
    seen = {}
    def replay(c, b, **kw):
        seen.update(kw)
        return {"available": False}
    monkeypatch.setattr(C, "vanilla", replay)
    out = p.verify_vanilla(file=str(file))
    assert "UNAVAILABLE" in out
    assert next(b[3] for b in seen["artifact_world"] if tuple(b[:3]) == (7, 1, 1)) == "minecraft:stone"
    assert E.file_hash(file) in out


def test_artifact_origin_is_preserved_in_vanilla_capture(monkeypatch):
    from env.task import load_task
    from env.variants import variant_of
    from env import runner
    from mira_mcp import project_checks as C
    from tools.oracle import gametest
    t = variant_of(load_task("t0_not"), offset=(30, 3, -20))
    pr = runner.prepare(t.contract, t.solution)
    world = [(*p, s, None) for p, s in pr.world.items()]
    monkeypatch.setattr(gametest, "available", lambda: True)
    def captures(jobs, **kw):
        assert jobs[0][2].origin == tuple(min(b[i] for b in world) for i in range(3))
        raise RuntimeError("origin preserved")
    monkeypatch.setattr(gametest, "capture_many", captures)
    with pytest.raises(RuntimeError, match="origin preserved"):
        C.vanilla(t.contract, t.solution, artifact_world=world)
