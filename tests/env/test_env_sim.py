"""Pins the Nucleation behaviours the env relies on (so a nucleation upgrade that changes them fails loudly)."""
from env import blocks as B
from env.sim import Sim, compile_cells, graph_reaches, parse, structural_graph


def world(*items):
    cells = {(x, 0, z): "minecraft:smooth_stone" for x in range(14) for z in range(3)}
    for pos, st in items:
        cells[pos] = B.canonical(st)
    return cells


LEVER = "minecraft:lever[face=floor,facing=east]"


def test_engine_placement_connects_wires_even_if_states_say_none():
    # canonical wires are 'none' on every side; the engine must recompute them or nothing conducts
    cells = world(((0, 1, 1), LEVER), ((1, 1, 1), "minecraft:redstone_wire"), ((2, 1, 1), "minecraft:redstone_wire"),
                  ((3, 1, 1), "minecraft:redstone_lamp"))
    sc, corner, errs = compile_cells(cells)
    assert errs == []
    sim = Sim(sc, corner)
    assert parse(sim.get((1, 1, 1)))[1]["east"] == "side"
    sim.use((0, 1, 1))
    sim.settle(20)
    assert parse(sim.get((3, 1, 1)))[1]["lit"] == "true"


def test_repeater_facing_is_the_input_side_and_delay_is_two_ticks_per_step():
    cells = world(((0, 1, 1), LEVER), ((1, 1, 1), "minecraft:repeater[facing=west,delay=4]"), ((2, 1, 1), "minecraft:redstone_lamp"))
    sc, corner, _ = compile_cells(cells)
    sim = Sim(sc, corner)
    sim.settle(10)
    sim.use((0, 1, 1))
    ticks = 0
    while parse(sim.get((2, 1, 1)))[1]["lit"] != "true":
        sim.step()
        ticks += 1
        assert ticks < 30
    assert ticks == 8  # delay 4 = 4 redstone ticks = 8 game ticks


def test_wrong_repeater_orientation_blocks_the_signal():
    cells = world(((0, 1, 1), LEVER), ((1, 1, 1), "minecraft:repeater[facing=east,delay=1]"), ((2, 1, 1), "minecraft:redstone_lamp"))
    sc, corner, _ = compile_cells(cells)
    sim = Sim(sc, corner)
    sim.use((0, 1, 1))
    sim.settle(30)
    assert parse(sim.get((2, 1, 1)))[1]["lit"] == "false"


def test_restore_returns_to_checkpoint_and_clears_change_log():
    cells = world(((0, 1, 1), LEVER), ((1, 1, 1), "minecraft:redstone_lamp"))
    sc, corner, _ = compile_cells(cells)
    sim = Sim(sc, corner)
    sim.settle(10)
    sim.checkpoint()
    sim.use((0, 1, 1))
    sim.settle(10)
    assert sim.changes()
    sim.restore()
    assert parse(sim.get((0, 1, 1)))[1]["powered"] == "false"
    assert sim.changes() == []


def test_negative_coordinates_work_for_pushed_blocks():
    cells = world(((1, 1, 1), "minecraft:sticky_piston[facing=west]"), ((0, 1, 1), "minecraft:stone"), ((1, 1, 0), "minecraft:stone"))
    cells[(1, 0, 2)] = "minecraft:smooth_stone"
    sc, corner, _ = compile_cells(cells)
    sim = Sim(sc, corner)
    sim.place((1, 1, 0), "minecraft:redstone_block")
    sim.settle(20)
    assert parse(sim.get((-1, 1, 1)))[0] == "minecraft:stone"  # pushed beyond the schematic's min corner


def test_structural_graph_paths():
    cells = world(((0, 1, 1), LEVER), ((1, 1, 1), "minecraft:redstone_wire"), ((2, 1, 1), "minecraft:redstone_lamp"),
                  ((6, 1, 1), "minecraft:redstone_lamp"))
    sc, corner, _ = compile_cells(cells)
    g = structural_graph(sc, corner)
    assert graph_reaches(g, (0, 1, 1), (2, 1, 1)) is True
    assert graph_reaches(g, (0, 1, 1), (6, 1, 1)) is False
    assert graph_reaches(g, (0, 1, 1), (9, 9, 9)) is None


# --------------------------------------------------------------------------- placement (docs/VALIDITY.md section 13)
def test_placement_flag_defaults_to_canonical_and_rejects_unknown_values(monkeypatch):
    import pytest
    from env.sim import placement_mode
    monkeypatch.delenv("MIRA_PLACEMENT", raising=False)
    assert placement_mode() == "canonical"
    monkeypatch.setenv("MIRA_PLACEMENT", "Legacy")
    assert placement_mode() == "legacy"
    monkeypatch.setenv("MIRA_PLACEMENT", "bogus")
    with pytest.raises(ValueError):
        placement_mode()


def test_canonical_compile_writes_cross_dust_raw_and_loads_with_placement(monkeypatch):
    from env.sim import is_canonical
    cells = world(((0, 1, 1), LEVER), ((1, 1, 1), "minecraft:redstone_wire"), ((2, 1, 1), "minecraft:redstone_wire"),
                  ((5, 1, 1), "minecraft:redstone_wire"), ((6, 1, 1), "minecraft:redstone_lamp"))
    monkeypatch.setenv("MIRA_PLACEMENT", "canonical")
    sc, corner, errs = compile_cells(cells)
    assert errs == [] and is_canonical(sc)
    raw = {(b["x"], b["y"], b["z"]): b for b in __import__("json").loads(sc.get_non_air_blocks_json())}
    assert dict(raw[(1, 1, 1)]["properties"]) == {"east": "side", "north": "side", "south": "side", "west": "side", "power": "0"}
    sim = Sim(sc, corner)
    assert sim.mode == "placement"
    sim.settle(20)
    assert parse(sim.get((1, 1, 1)))[1]["north"] == "none" and parse(sim.get((1, 1, 1)))[1]["west"] == "side"   # shapes resolved from the neighbours
    assert parse(sim.get((5, 1, 1)))[1]["east"] == "side"                      # an isolated dust stays a cross, as when placed by hand
    sim.use((0, 1, 1))
    sim.settle(20)
    assert int(parse(sim.get((2, 1, 1)))[1]["power"]) > 0                      # the lever's signal runs down the resolved line
    monkeypatch.setenv("MIRA_PLACEMENT", "legacy")
    sc2, corner2, _ = compile_cells(cells)
    assert not is_canonical(sc2) and Sim(sc2, corner2).mode == "inworld"
    assert compile_cells(cells, engine_place=False)[0].name() != "mira_env_canonical"     # raw stores (paste_stable) are never retagged


def test_raw_schematics_keep_the_requested_load_mode():
    sc, corner, _ = compile_cells(world(((1, 1, 1), "minecraft:redstone_lamp")), engine_place=False)
    assert Sim(sc, corner, "inworld").mode == "inworld" and Sim(sc, corner, "placement").mode == "placement"
