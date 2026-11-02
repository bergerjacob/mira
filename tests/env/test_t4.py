"""T4 tasks (env/tasks/t4/, 860-2000 block references over lib/ parts): spec hygiene, stored broken variants, reference == authoring script,
recorded vanilla cross-checks, the new `d_flipflop` part. The full gate (`env/tasks/author_t4.py --validate`) takes minutes per task and is
run by hand; here the cheap parts run always and the big references are `slow`."""
import json
from pathlib import Path

import pytest

from env import library as L
from env import mutate
from env.runner import run
from env.score import score
from env.task import list_task_ids, load_task
from env.validate_tasks import nonvacuous
from env.tasks import author_t4 as A

T4 = Path(__file__).resolve().parents[2] / "env" / "tasks" / "t4"
IDS = sorted(p.stem for p in T4.glob("t4_*.json"))
EXPECTED = {"t4_addsub8", "t4_adder16", "t4_shift32", "t4_accum4"}


def test_t4_has_the_expected_tasks_and_listing_sees_them():
    assert set(IDS) == EXPECTED == set(A.STORED)
    assert EXPECTED <= set(list_task_ids()) and set(list_task_ids("t4")) == EXPECTED
    assert load_task("t4_accum4").id == "t4_accum4"          # bare id resolves through env/tasks/t4/


@pytest.mark.parametrize("tid", IDS)
def test_spec_is_valid_nonvacuous_and_sized_for_t4(tid):
    t = load_task(T4 / f"{tid}.json")
    c = t.contract
    assert c.tier == "T4" and c.validate() == [] and nonvacuous(c) == []
    assert 800 <= len(t.solution) <= 2500 and c.reference["blocks"] == len(t.solution)
    assert c.description.strip() and any(not s.public for s in c.behaviour.sequences) or any(not v.public for v in c.vectors())
    assert c.budgets.max_blocks >= len(t.solution)


@pytest.mark.parametrize("tid", IDS)
def test_models_and_hidden_data_are_not_exposed(tid):
    pub = load_task(T4 / f"{tid}.json").contract.public_dict()
    assert pub["eval_seed"] is None and pub["reference"] == {}
    rm = pub["behaviour"]["reference_model"]
    if rm:
        assert rm["hidden_seed"] is None and rm["n_hidden"] is None


@pytest.mark.parametrize("tid", IDS)
def test_at_least_three_stored_broken_variants(tid):
    t = load_task(T4 / f"{tid}.json")
    assert len(t.broken) >= 3
    sol = {tuple(b[:3]) for b in t.solution}
    for b in t.broken:                                         # edits only touch cells that exist in the reference (or add to it)
        assert b["edits"] and all(e[0] in ("remove", "set") for e in b["edits"])
        assert all(tuple(e[1]) in sol for e in b["edits"] if e[0] == "remove")


@pytest.mark.parametrize("tid", IDS)
def test_json_is_in_sync_with_the_authoring_script(tid):
    plan = A.BUILDERS[tid]()
    plan.compile_solution()
    stored = load_task(T4 / f"{tid}.json")
    assert sorted(map(tuple, plan.solution)) == sorted(map(tuple, stored.solution))


@pytest.mark.parametrize("tid", IDS)
def test_reference_program_is_sandbox_legal_builder_code_with_fixed_devices_as_avoid_cells(tid):
    from simulation import builder as BD
    plan = A.BUILDERS[tid]()
    plan.compile_solution()
    text = L.program(plan.uses, plan.body)
    assert "avoid=FIXED" in text and "floor_exists=True" in text and "check_spacing=True" in text and "box=BOX" in text
    s = BD.compile(text, sandbox=True, lib_root=str(L.LIB))
    assert s.ok, s.errors
    assert s.hash() == plan.build_info.hash()
    low = [b[:3] for b in s.blocks if b[1] <= 0]
    if tid != "t4_adder16":                                     # adder16 reuses the T3 body, which lays its own y=0 floor (dropped by compile_solution)
        assert low == []                                        # floor=False everywhere: nothing at y=0 (the task floor is fixed)
    fixed = set(plan.dev.values())
    if tid != "t4_adder16":                                     # (the T3 adder body writes its devices itself; compile_solution filters them)
        assert not any(tuple(b[:3]) in fixed for b in s.blocks)    # the reference never writes the fixed devices


@pytest.mark.parametrize("tid", IDS)
def test_reference_stays_inside_the_build_box(tid):
    t = load_task(T4 / f"{tid}.json")
    x1, y1, z1, x2, y2, z2 = t.contract.build_box
    assert all(x1 <= b[0] <= x2 and y1 <= b[1] <= y2 and z1 <= b[2] <= z2 for b in t.solution)


def test_accum4_reference_is_a_full_pass_with_every_invariant_and_its_broken_variants_fail():
    t = load_task(T4 / "t4_accum4.json")
    sc = score(t.contract, run(t.contract, t.solution))
    assert sc.full_pass and sc.reward == 1.0
    assert not score(t.contract, run(t.contract, [], fast=True)).full_pass
    for b in t.broken:
        r = score(t.contract, run(t.contract, mutate.apply(t.solution, [tuple(e) for e in b["edits"]]), fast=True))
        assert not r.full_pass, b["name"]


@pytest.mark.slow
@pytest.mark.parametrize("tid", ["t4_addsub8", "t4_adder16", "t4_shift32"])
def test_big_references_are_full_passes(tid):
    t = load_task(T4 / f"{tid}.json")
    assert score(t.contract, run(t.contract, t.solution)).full_pass


def test_accum_model_sequences_follow_the_python_model():
    """The accumulator's timed expectations are generated from ACC := ACC + IN (mod 2^n): re-derive the final values."""
    t = load_task(T4 / "t4_accum4.json")
    for s in t.contract.behaviour.sequences:
        ins, acc, cur, edges = {}, 0, 0, []
        for e in sorted(s.events, key=lambda e: e.t):
            for k, v in e.set.items():
                if k.startswith("IN"):
                    cur = (cur & ~(1 << int(k[2:]))) | (v << int(k[2:]))
                if k == "CLK" and v == 1:
                    acc = (acc + cur) & 15
                    edges.append((e.t, acc))
        last_t, last_acc = (edges[-1] if edges else (0, 0))
        want = {x.port: x.value for x in s.expect if x.t2 >= s.duration - 1}
        assert want == {f"ACC{i}": (last_acc >> i) & 1 for i in range(4)}, s.name


def test_addsub_and_shift_models():
    from env.models import get_model
    fn = get_model("addsub")
    ins = {**{f"A{k}": (200 >> k) & 1 for k in range(8)}, **{f"B{k}": (7 >> k) & 1 for k in range(8)}, "M": 1}
    out = fn({}, ins, bits=8)
    assert sum(out[f"S{k}"] << k for k in range(8)) == 193 and out["COUT"] == 1       # 200 - 7, no borrow
    ins["M"] = 0
    out = fn({}, ins, bits=8)
    assert sum(out[f"S{k}"] << k for k in range(8)) == 207 and out["COUT"] == 0
    ins = {**{f"A{k}": (3 >> k) & 1 for k in range(8)}, **{f"B{k}": (5 >> k) & 1 for k in range(8)}, "M": 1}
    out = fn({}, ins, bits=8)
    assert sum(out[f"S{k}"] << k for k in range(8)) == 254 and out["COUT"] == 0       # 3 - 5 borrows


def test_addsub8_corner_rows_cover_borrow_and_carry_chains():
    c = load_task(T4 / "t4_addsub8.json").contract
    rows = c.behaviour.truth_table.rows
    assert any(r.inputs["M"] == 1 and r.expect["COUT"] == 0 for r in rows) and any(r.inputs["M"] == 1 and r.expect["COUT"] == 1 for r in rows)
    assert any(r.inputs["M"] == 0 and r.expect["COUT"] == 1 for r in rows)


def test_shift_register_timing_is_derived_from_the_clock_bus_latency():
    c = load_task(T4 / "t4_shift32.json").contract
    s = c.behaviour.sequences[0]
    clk_rises = [e.t for e in s.events if e.set.get("CLK") == 1]
    assert len(clk_rises) >= 4 and min(b - a for a, b in zip(clk_rises, clk_rises[1:])) >= 100      # edges far enough apart for 32 places


# --------------------------------------------------------------------------- recorded cross-checks
def test_oracle_report_isolated_verdicts_and_honest_gaps():
    """Isolated (one JVM per capture) vanilla verdicts exist for addsub8, accum4 and adder16 (tick-identical); shift32 has none (cancelled after 40 min)."""
    rep = json.loads((T4 / "oracle_report.json").read_text())
    assert set(rep) == EXPECTED
    for tid in ("t4_addsub8", "t4_accum4", "t4_adder16"):
        r = rep[tid]
        assert r["jvm_per_capture"] and r["vanilla_pass"] and r["nucleation_pass"] and r["agree"] == r["captures"] > 0, tid
    assert rep["t4_shift32"]["status"] == "not_completed" and "vanilla_pass" not in rep["t4_shift32"]


# --------------------------------------------------------------------------- d_flipflop
def test_d_flipflop_part_is_verified_in_every_orientation_and_in_vanilla():
    m = L.meta("d_flipflop")
    v = m["verified"]
    assert len(v["func_ok_orientations"]) == 8 and len(v["paste_stable_orientations"]) == 8
    assert v["oracle_ok"] and v["oracle_tick_identical"] == v["oracle_captures"] > 0
    assert set(m["ports"]) == {"CLK", "D", "Q"} and m["footprint"]["blocks"] == 34


def test_d_flipflop_floor_false_and_sandbox():
    b = L.compile_program("d_flipflop", "def build(b):\n    b.place(d_flipflop, at=(0, 0, 0), floor=False)\n", sandbox=True)
    assert b.ok and min(x[1] for x in b.blocks) == 1
