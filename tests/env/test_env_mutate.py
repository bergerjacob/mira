from env import mutate
from env.task import load_task


def test_apply_edits():
    sol = [[0, 1, 0, "minecraft:stone"], [1, 1, 0, "minecraft:redstone_wire"]]
    out = mutate.apply(sol, [("remove", (0, 1, 0)), ("set", (2, 1, 0), "minecraft:glass")])
    assert sorted(map(tuple, out)) == [(1, 1, 0, "minecraft:redstone_wire"), (2, 1, 0, "minecraft:glass")]


def test_candidates_cover_the_main_breakage_kinds():
    t = load_task("t1_mux2")
    kinds = {c["kind"] for c in mutate.candidates(t.solution, t.contract.build_box)}
    assert {"remove", "facing", "shift", "delay", "swap"} <= kinds


def test_pick_is_deterministic_and_interleaves_kinds():
    t = load_task("t1_xor")
    a = [m["name"] for m in mutate.pick(t.solution, t.contract.build_box, seed=1)]
    b = [m["name"] for m in mutate.pick(t.solution, t.contract.build_box, seed=1)]
    assert a == b
    first = {m["kind"] for m in mutate.pick(t.solution, t.contract.build_box, seed=1)[:4]}
    assert len(first) >= 3


def test_facing_mutations_flip_or_turn():
    sol = [[0, 1, 0, "minecraft:repeater[facing=west,delay=1]"]]
    names = {c["name"] for c in mutate.candidates(sol)}
    assert any("flip" in n for n in names) and any("turn" in n for n in names) and any("delay" in n for n in names)
