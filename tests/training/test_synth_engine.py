"""Offline tests of the synthetic data engine (training/synth/): netlists, layout, bugs, sequential families, repair/extend,
scripted trajectories. Nucleation only, no API key; ~1-2 min."""
import itertools
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env.runner import run  # noqa: E402
from env.score import score  # noqa: E402
from training.synth import bugs as G  # noqa: E402
from training.synth import common as C  # noqa: E402
from training.synth import netlist as N  # noqa: E402
from training.synth import repair as R  # noqa: E402
from training.synth import sequential as S  # noqa: E402
from training.synth import trajectories as T  # noqa: E402


def small_net():
    """I0, I1, I2 -> Y0 = (I0 AND I1) OR I2, Y1 = NOT I2."""
    return N.Netlist(["I0", "I1", "I2"], [N.Gate("and", ["I0", "I1"], "n0"), N.Gate("or", ["n0", "I2"], "n1"), N.Gate("not", ["I2"], "n2")],
                     {"Y0": "n1", "Y1": "n2"})


@pytest.fixture(scope="module")
def built():
    nl = small_net()
    task, prog, info = N.make_task(nl, "sn_test_small", seed=1)
    return nl, task, prog, info


@pytest.fixture(scope="module")
def synth_root(tmp_path_factory, built):
    """A private task store with the small netlist task saved like the generator does."""
    nl, task, prog, info = built
    root = tmp_path_factory.mktemp("synth")
    rep = C.gate(task, 6)
    assert rep["accepted"], rep["problems"]
    path = C.save(task, prog, {"family": "netlist", "source": "synth/netlist", "difficulty": "easy", "netlist": info["netlist"]}, "netlist", root=root)
    return root, path


# --------------------------------------------------------------------------- netlists
def test_random_netlist_is_deterministic_per_seed():
    a = N.random_netlist(random.Random(5), 3, 4, 2)
    b = N.random_netlist(random.Random(5), 3, 4, 2)
    assert a.to_dict() == b.to_dict()


@pytest.mark.parametrize("seed", range(12))
def test_random_netlist_uses_every_input_and_has_distinct_nonconstant_outputs(seed):
    rng = random.Random(seed)
    nl = N.random_netlist(rng, rng.randint(2, 5), rng.randint(2, 6), rng.randint(1, 3))
    if nl is None:
        pytest.skip("no net for these knobs")
    cols = [tuple(o[y] for _, o in nl.table()) for y in nl.outputs]
    assert all(len(set(c)) == 2 for c in cols)
    assert len(set(cols)) == len(cols)
    assert set(itertools.chain(*nl.depends().values())) == set(nl.inputs)
    used = {s for g in nl.gates for s in g.ins} | set(nl.outputs.values())
    assert set(nl.signals()) <= used                                   # nothing dangles


def test_random_netlist_returns_none_when_inputs_cannot_all_be_used():
    assert N.random_netlist(random.Random(0), 4, 1, 1) is None         # one 2-input gate cannot read 4 inputs


def test_eval_table_depends_and_roundtrip():
    nl = small_net()
    tab = dict(nl.table())
    assert tab[(1, 1, 0)] == {"Y0": 1, "Y1": 1} and tab[(0, 1, 1)] == {"Y0": 1, "Y1": 0} and tab[(0, 1, 0)] == {"Y0": 0, "Y1": 1}
    assert nl.depends() == {"Y0": ["I0", "I1", "I2"], "Y1": ["I2"]}
    assert nl.depth() == 2
    assert N.Netlist.from_dict(json.loads(json.dumps(nl.to_dict()))).to_dict() == nl.to_dict()
    assert nl.expression("n1") == "((I0 AND I1) OR I2)"


def test_layout_columns_are_spaced_for_paste_stability():
    """Engine placement corrupts dust shapes when another dust sits 1-2 cells east in a nearby row (docs/DATA_ENGINE.md);
    every tap / output column must be >= CS apart."""
    nl = N.random_netlist(random.Random(11), 4, 6, 2)
    lay = N.layout(nl)
    xs = sorted(int(m.group(2)) for ln in lay["body"].split("\n") if (m := G.TAP.match(ln)))
    xs += sorted(int(m.group(2)) for ln in lay["body"].split("\n") if (m := G.OUT.match(ln)))
    xs.sort()
    assert len(xs) == len(set(xs)) and all(b - a >= N.CS for a, b in zip(xs, xs[1:]))


def test_reference_program_compiles_in_the_sandbox_to_the_task_solution(built):
    nl, task, prog, info = built
    sol = G.solution_of(task, prog)
    assert not isinstance(sol, str), sol
    assert sorted(map(tuple, sol)) == sorted(tuple(b[:4]) for b in task.solution)
    from simulation import builder as BD
    assert BD.compile(prog, sandbox=True, timeout=60).ok


def test_task_spec_is_exhaustive_with_hidden_reversed_copy(built):
    nl, task, prog, info = built
    c = task.contract
    assert c.validate() == [] and c.tier == "SYN"
    rows = c.behaviour.truth_table.rows
    combos = {tuple(r.inputs[i] for i in nl.inputs) for r in rows}
    assert len(combos) == 8 and len(rows) == 16
    pub = [r for r in rows if r.public]
    for o in nl.outputs:
        assert {r.expect[o] for r in pub} == {0, 1}
    assert c.depends == nl.depends()


def test_random_vectors_for_wide_nets_cover_both_values():
    nl = N.random_netlist(random.Random(3), 6, 5, 2)
    rows = N.truth_rows(nl, random.Random(0))
    first = rows[:len(rows) // 2]
    assert len({r.label for r in first}) == len(first) < 64       # sampled, no repeats
    assert any(r.public for r in first) and any(not r.public for r in first)
    for o in nl.outputs:
        assert {r.expect[o] for r in first} == {0, 1}


def test_reference_passes_env_with_reward_one(built):
    nl, task, prog, info = built
    sc = score(task.contract, run(task.contract, task.solution, seed=0))
    assert sc.full_pass and sc.reward == pytest.approx(1.0)


def test_engine_placement_dust_quirk_still_present():
    """Documents the Nucleation quirk the layout spacing works around: a line end next to a lever + a foreign dust two cells
    east and one south -> paste_stable differs. If this starts failing, the CS spacing could be relaxed."""
    from env import blocks as EB
    from env.sim import Sim, compile_cells
    cells = {(x, 0, z): "minecraft:smooth_stone" for x in range(6) for z in range(6)}
    cells[(0, 1, 3)] = EB.canonical("minecraft:lever[face=floor,facing=east]")
    for p in [(1, 1, 3), (2, 1, 3), (4, 1, 4)]:
        cells[p] = EB.canonical("minecraft:redstone_wire")
    sc, o, _ = compile_cells(cells)
    sim = Sim(sc, o)
    sim.settle(60)
    base = sim.snapshot()
    raw, o2, _ = compile_cells(base, None, engine_place=False)
    s2 = Sim(raw, o2, "placement")
    s2.settle(60)
    assert s2.snapshot()[(2, 1, 3)] != base[(2, 1, 3)]


# --------------------------------------------------------------------------- bugs
def test_bug_candidates_edit_exactly_one_line(built):
    nl, task, prog, info = built
    cands = G.candidates(prog)
    assert {"missing_tap", "missing_out", "wrong_signal", "short_bus"} <= {b["kind"] for b in cands}
    n = len(prog.split("\n"))
    for b in cands:
        new = b["text"].split("\n")
        assert len(new) in (n, n - 1) and b["text"] != prog


def test_missing_tap_fails_the_public_spec(built):
    nl, task, prog, info = built
    b = next(b for b in G.candidates(prog) if b["kind"] == "missing_tap")
    ok, why = G.verify_bug(task, b["text"])
    assert ok, why


def test_typo_is_a_compile_error(built):
    nl, task, prog, info = built
    text, name = G.typo(prog, random.Random(0))
    assert name in ("repeter", "wirepath", "levr")
    assert G.solution_of(task, text).startswith("compile:")


def test_ordered_returns_every_candidate_once(built):
    nl, task, prog, info = built
    a = G.ordered(prog, random.Random(1))
    assert sorted(b["text"] for b in a) == sorted(b["text"] for b in G.candidates(prog))
    assert len({b["kind"] for b in a[:4]}) == 4                          # diverse kinds first


def test_generic_bugs_on_library_programs():
    p, desc = S.offdelay_plan(16, 1)
    from env import library as L
    kinds = {b["kind"] for b in G.candidates(L.program(p.uses, p.body))}
    assert {"missing_route", "wrong_ticks"} <= kinds


# --------------------------------------------------------------------------- sequential
def test_sequential_jobs_cover_all_families():
    fams = {j[0] for j in S.jobs()}
    assert fams == {"delay", "offdelay", "counter", "shift"} and len(S.jobs()) == 101


def test_delay_family_is_measured_and_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "TASKS", tmp_path)
    rec = S.attempt(("delay", 8, 0, 6))
    assert rec["accepted"], rec
    assert abs(rec["rise"] - 8) <= 2
    task, prog, synth = C.load(tmp_path / "sequential" / "sq_delay_08_0.json")
    assert synth["rise"] == rec["rise"] and f"{rec['rise']} ticks" in task.contract.description
    assert [s.public for s in task.contract.behaviour.sequences] == [True, False, False]


def test_offdelay_holds_after_switch_off():
    p, desc = S.offdelay_plan(24, 0)
    p.desc = desc
    p.seq("probe", [(0, {"A": 1})], [("Y", 1, 0, 10, "any")], 20)
    rise, fall = S.measure(p.build())
    assert rise <= 6 and 24 <= fall <= 36


# --------------------------------------------------------------------------- repair / extend
def test_repair_task_from_synthetic_netlist(synth_root, monkeypatch):
    root, path = synth_root
    monkeypatch.setattr(C, "TASKS", root)
    r = R.attempt(("repair", str(path), 0))
    assert r["accepted"], r
    task, fix, synth = C.load(root / "repair" / f"{r['id']}.json")
    broken = (root / "repair" / f"{r['id']}.broken.py").read_text()
    assert fix == path.with_suffix(".py").read_text() and broken != fix
    assert "REPAIR:" in task.contract.description and broken in task.contract.description
    assert G.verify_bug(task, broken)[0]
    assert R.attempt(("repair", str(path), 0)).get("cached")              # resumable


def test_repair_env_task_is_flagged_as_benchmark_material(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "TASKS", tmp_path)
    r = R.attempt(("repair_env", "t0_and", 0))
    assert r["accepted"], r
    task, fix, synth = C.load(tmp_path / "repair_env" / "rpe_t0_and_0.json")
    assert "contamination" in synth and synth["base"] == "t0_and"
    sol = G.solution_of(task, fix)
    assert score(task.contract, run(task.contract, sol, seed=0, fast=True)).full_pass
    broken = (tmp_path / "repair_env" / "rpe_t0_and_0.broken.py").read_text()
    assert not score(task.contract, run(task.contract, G.solution_of(task, broken), seed=0, fast=True)).full_pass


def test_extend_adds_one_gate_and_output(synth_root, monkeypatch):
    root, path = synth_root
    monkeypatch.setattr(C, "TASKS", root)
    r = R.attempt(("extend", str(path), 0))
    assert r["accepted"], r
    task, prog, synth = C.load(root / "extend" / f"{r['id']}.json")
    old = json.loads(path.read_text())["synth"]["netlist"]
    assert len(synth["netlist"]["gates"]) == len(old["gates"]) + 1 and len(synth["netlist"]["outputs"]) == len(old["outputs"]) + 1
    assert "EXTEND:" in task.contract.description


# --------------------------------------------------------------------------- trajectories
def _check_chat(rec):
    msgs = rec["messages"]
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    pending = []
    for m in msgs[2:]:
        if m["role"] == "assistant":
            assert not pending
            pending = [c["id"] for c in m.get("tool_calls", [])]
            assert pending, "every scripted assistant turn calls a tool"
            for c in m["tool_calls"]:
                json.loads(c["function"]["arguments"])
        else:
            assert m["role"] == "tool" and m["tool_call_id"] == pending.pop(0)
    assert not pending
    assert msgs[-1]["content"].startswith("[submit]")


def test_verify_episode_matches_the_harness_prompts_and_tools(built):
    from harness import prompts
    from harness.agent import build_tools
    nl, task, prog, info = built
    ep, final = T.episode(task, "verify", prog, {"netlist": nl.to_dict()}, seed=0)
    assert ep is not None and final["full_pass"]
    _check_chat(ep)
    assert ep["messages"][0]["content"] == prompts.system_prompt("builder")
    assert ep["messages"][1]["content"] == prompts.task_prompt(task, "builder", 3, 30)
    assert ep["tools"] == build_tools("builder", 3)
    assert [m["tool_calls"][0]["function"]["name"] for m in ep["messages"] if m["role"] == "assistant"] == ["build", "run_spec", "submit"]


def test_debug_episode_fails_first_then_passes(built):
    nl, task, prog, info = built
    bug = next(b for b in G.candidates(prog) if b["kind"] == "missing_out")
    ep, final = T.episode(task, "debug_view", prog, {"netlist": nl.to_dict()}, bug=bug, seed=0)
    assert ep is not None
    _check_chat(ep)
    specs = [m["content"] for m in ep["messages"] if m["role"] == "tool" and m["content"].startswith("[run_spec")]
    assert len(specs) == 2 and not T._passes(specs[0]) and T._passes(specs[1])
    calls = [c for s in ep["spans"] if s["role"] == "assistant" for c in s["calls"]]
    assert calls[0] == "bugged_program" and "reference_program" in calls
    assert any(m["role"] == "assistant" and m["tool_calls"][0]["function"]["name"] == "view" for m in ep["messages"][2:])
    assert all(s["source"] == "env_real" for s in ep["spans"] if s["role"] == "tool")


def test_oneshot_episode_has_no_run_spec_tool(built):
    nl, task, prog, info = built
    ep, final = T.episode(task, "oneshot", prog, {"netlist": nl.to_dict()}, seed=2)
    assert ep is not None
    assert "run_spec" not in {t["function"]["name"] for t in ep["tools"]}
    rec = T.record(task, "oneshot", ep, final, {"difficulty": "easy"}, "netlist")
    assert rec["meta"]["verified"] is True and rec["meta"]["n_blocks"] == len(task.solution) and rec["meta"]["rounds"] == 0


def test_unfixed_debug_episode_is_rejected(built):
    nl, task, prog, info = built
    bug = next(b for b in G.candidates(prog) if b["kind"] == "missing_tap")
    ep, final = T.episode(task, "debug", bug["text"], {"netlist": nl.to_dict()}, bug={**bug}, seed=0)   # "fix" is the bug again
    assert ep is None and not final["full_pass"]


# --------------------------------------------------------------------------- storage / exported data
def test_claim_deduplicates(tmp_path):
    assert C.claim("x", {"a": 1}, root=tmp_path) and not C.claim("x", {"a": 1}, root=tmp_path) and C.claim("x", {"a": 2}, root=tmp_path)


def test_save_load_roundtrip(built, tmp_path):
    nl, task, prog, info = built
    p = C.save(task, prog, {"family": "netlist", "k": 1}, "netlist", root=tmp_path)
    t2, prog2, syn = C.load(p)
    assert t2.contract.to_dict() == task.contract.to_dict() and t2.solution == task.solution and prog2 == prog and syn["k"] == 1


@pytest.mark.parametrize("shard", sorted(p.name for p in C.SFT.glob("*.jsonl")) or ["none"])
def test_exported_sft_rows_are_well_formed(shard):
    path = C.SFT / shard
    if not path.exists():
        pytest.skip("no exported shard")
    lines = path.read_text().splitlines()
    for ln in lines[:: max(1, len(lines) // 15)]:
        rec = json.loads(ln)
        _check_chat(rec)
        m = rec["meta"]
        assert m["verified"] is True and m["reward"] >= 0.99 and {"task", "source", "difficulty", "n_blocks", "kind"} <= set(m)
        assert {t["function"]["name"] for t in rec["tools"]} >= {"build", "view", "submit"}
