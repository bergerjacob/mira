"""T3 tasks (env/tasks/t3/*.json): references built from lib/ components, specs, broken variants, reproducibility and the recorded
vanilla-oracle / compiler-track results. Offline (Nucleation only)."""
import json
from pathlib import Path

import pytest

from env import library as L          # registers the T3 reference models
from env import mutate
from env.runner import run
from env.score import score
from env.task import load_task
from env.tasks import author_t3 as A
from env.validate_tasks import evaluate, nonvacuous

T3 = Path(A.OUT)
IDS = sorted(p.stem for p in T3.glob("t3_*.json"))
FAST = [i for i in IDS if i != "t3_adder8"]
SMALL = ["t3_counter2", "t3_counter2_down", "t3_majority3", "t3_parity4", "t3_decoder2to4", "t3_rom4x4"]


def test_t3_has_the_planned_tasks():
    assert set(IDS) == {"t3_adder2", "t3_adder3", "t3_adder4", "t3_adder8", "t3_decoder2to4", "t3_decoder3to8", "t3_counter2",
                        "t3_counter2_down", "t3_counter4", "t3_parity4", "t3_majority3", "t3_rom4x4"}


@pytest.mark.parametrize("tid", IDS)
def test_spec_is_valid_and_nonvacuous(tid):
    t = load_task(T3 / f"{tid}.json")
    assert t.contract.tier == "T3" and t.contract.validate() == [] and nonvacuous(t.contract) == []
    assert 50 <= len(t.solution) <= 1200 and t.contract.reference["blocks"] == len(t.solution)
    assert t.contract.description.strip() and any(not v.public for v in t.contract.vectors())


@pytest.mark.parametrize("tid", IDS)
def test_models_and_hidden_data_are_not_exposed(tid):
    c = load_task(T3 / f"{tid}.json").contract
    pub = c.public_dict()
    assert pub["eval_seed"] is None and pub["reference"] == {}
    rm = pub["behaviour"]["reference_model"]
    if rm:
        assert rm["hidden_seed"] is None and rm["n_hidden"] is None
    assert all(r["public"] for r in (pub["behaviour"]["truth_table"] or {"rows": []})["rows"])


@pytest.mark.parametrize("tid", FAST)
def test_reference_is_a_full_pass_with_every_invariant(tid):
    t = load_task(T3 / f"{tid}.json")
    res = run(t.contract, t.solution)
    sc = score(t.contract, res)
    assert res.prepared.errors == [] and res.place_errors == [] and res.invariant_failures() == {}, res.invariant_failures()
    assert sc.full_pass and sc.reward == pytest.approx(1.0), sc.to_dict()


@pytest.mark.slow
def test_adder8_reference_is_a_full_pass():
    t = load_task(T3 / "t3_adder8.json")
    sc = score(t.contract, run(t.contract, t.solution, fast=True))
    assert sc.full_pass and sc.reward == pytest.approx(1.0)
    assert len(t.solution) > 900                              # the 8-bit adder is already T4-sized


@pytest.mark.parametrize("tid", IDS)
def test_at_least_three_stored_broken_variants(tid):
    t = load_task(T3 / f"{tid}.json")
    assert len(t.broken) >= 3 and len({b["kind"] for b in t.broken}) >= 2


@pytest.mark.parametrize("tid", SMALL)
def test_stored_broken_variants_fail_and_empty_build_fails(tid):
    t = load_task(T3 / f"{tid}.json")
    for b in t.broken[:4]:
        s = score(t.contract, run(t.contract, mutate.apply(t.solution, [tuple(e) for e in b["edits"]]), fast=True))
        assert not s.full_pass, b["name"]
    assert not score(t.contract, run(t.contract, [], fast=True)).full_pass


@pytest.mark.parametrize("tid", ["t3_counter2", "t3_majority3"])
def test_acceptance_gate_accepts_the_task(tid):
    rep = evaluate(load_task(T3 / f"{tid}.json"), max_mutants=6)
    assert rep["accepted"], rep["problems"]
    assert rep["killed"] >= 3


@pytest.mark.parametrize("tid", SMALL)
def test_json_is_in_sync_with_the_authoring_script(tid):
    plan = {p.id: p for p in A.build_all()}[tid]
    task = plan.build()
    stored = load_task(T3 / f"{tid}.json")
    assert task.solution == stored.solution
    assert task.contract.to_dict() == stored.contract.to_dict()


def test_reference_programs_are_plain_builder_code_over_library_parts():
    plan = {p.id: p for p in A.build_all()}["t3_adder3"]
    text = L.program(plan.uses, plan.body)
    assert "b.array(full_adder" in text and "wire_path" in text and plan.uses == ["full_adder"]
    s = BDcompile(text, sandbox=True)
    u = BDcompile(text, sandbox=False)
    assert s.ok and s.hash() == u.hash()


def BDcompile(text, sandbox):
    from simulation import builder as BD
    return BD.compile(text, sandbox=sandbox, lib_root=str(L.LIB))


def test_adder_tasks_use_the_expected_cells():
    for n in (2, 3, 4, 8):
        t = load_task(T3 / f"t3_adder{n}.json")
        assert [p.name for p in t.contract.outputs] == [f"S{i}" for i in range(n)] + ["COUT"]
        assert len([p for p in t.contract.inputs]) == 2 * n + 1
        assert t.contract.behaviour.reference_model.fn == "adder_cin"
    sizes = [len(load_task(T3 / f"t3_adder{n}.json").solution) for n in (2, 3, 4, 8)]
    assert sizes == sorted(sizes) and sizes[0] > 200 and sizes[2] < 500 < sizes[3]


def test_counter_public_stream_returns_to_zero():
    from env.models import get_model
    for bits, down in ((2, False), (2, True), (4, False)):
        n_public = 14 if bits == 2 else 40
        seed = A.counter_public_seed(n_public, bits, down)
        rm = load_task(T3 / f"t3_counter{bits}{'_down' if down else ''}.json").contract.behaviour.reference_model
        assert rm.public_seed == seed
        state, last = {}, None
        for v in rm.generate()[:n_public]:
            last = v.expect
        assert last == {f"Q{k}": 0 for k in range(bits)}


def test_decoder_tasks_are_decoders_with_enable():
    t = load_task(T3 / "t3_decoder3to8.json")
    rows = [v for v in t.contract.vectors()]
    assert all(sum(v.expect.values()) <= 1 for v in rows)
    assert any(sum(v.expect.values()) == 1 for v in rows) and any(v.inputs.get("EN") == 0 and not any(v.expect.values()) for v in rows)


def test_rom_content():
    t = load_task(T3 / "t3_rom4x4.json")
    words = {}
    for v in t.contract.behaviour.truth_table.rows:
        if v.inputs["EN"]:
            words[(v.inputs["A1"], v.inputs["A0"])] = sum(v.expect[f"D{j}"] << j for j in range(4))
    assert words == {(0, 0): 0b0001, (0, 1): 0b0011, (1, 0): 0b0110, (1, 1): 0b1100}


# --------------------------------------------------------------------------- recorded cross-checks
def test_oracle_report_covers_every_task_and_vanilla_passes():
    rep = json.loads((T3 / "oracle_report.json").read_text())
    assert set(rep) >= set(IDS)
    for tid in IDS:
        assert rep[tid]["vanilla_pass"] and rep[tid]["nucleation_pass"], tid
    # simultaneous multi-input changes through comparators differ tick-wise in two big tasks (settled values agree)
    assert {t for t in IDS if rep[t]["agree"] != rep[t]["captures"]} <= {"t3_adder8", "t3_decoder3to8"}


def test_compile_track_report_findings():
    rep = {r["design"]: r for r in json.loads((T3 / "compile_track_report.json").read_text())}
    assert {"ha", "add2", "add3", "add4", "add8"} <= set(rep)
    for name in ("ha", "add2", "add3", "add4"):
        r = rep[name]
        assert r["tick_sim"]["pass"] == r["tick_sim"]["total"] and r["env"]["vectors_pass"] == r["env"]["vectors"]
        assert r["footprint_ratio"]["blocks"] > 10 and r["footprint_ratio"]["volume"] > 50
    for name in ("ha", "add2", "add3"):
        o = rep[name]["oracle"]
        assert o["vanilla_pass"] and o["nucleation_pass"]
    assert rep["add8"]["tick_sim"]["pass"] == rep["add8"]["tick_sim"]["total"] and rep["add8"]["compile"]["blocks"] > 40000
    assert not rep["add4"]["executor"]["ok"]                 # Executor.for_schematic does not drive compiled cells in nucleation 0.12.0


def test_compile_track_script_half_adder_end_to_end():
    import sys
    sys.path.insert(0, str(L.ROOT / "scripts"))
    import compile_track_check as C
    blif = C.blif_adder(2)
    assert blif.startswith(".model add2") and blif.count(".names") == 5 and ".outputs s0 s1 s2" in blif
    c = C.compile_design("ha")
    assert len(c["blocks"]) > 600 and c["report"]["levers"]
    vectors = C.choose_vectors("ha", c, 8)
    assert len(vectors) == 4
    assert C.tick_sim_check(c, vectors, C.DESIGNS["ha"]["model"]) == {"pass": 4, "total": 4}
    contract, sol = C.make_contract("ha", c, vectors, C.DESIGNS["ha"]["model"], 400)
    assert contract.validate() == []
    sc = score(contract, run(contract, sol, fast=True))
    assert sc.full_pass


def test_docs_list_every_component_and_task():
    text = (L.ROOT / "docs" / "LIBRARY.md").read_text()
    for n in L.names():
        assert f"`{n}`" in text, n
    for tid in IDS:
        assert tid in text, tid
