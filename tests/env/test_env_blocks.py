import pytest

from env import blocks as B


def test_canonical_fills_defaults():
    # a repeater without locked/powered breaks TickSimulation loading in Nucleation; canonical() fills them
    s = B.canonical("minecraft:repeater[facing=west,delay=2]")
    assert s == "minecraft:repeater[delay=2,facing=west,locked=false,powered=false]"


def test_canonical_adds_namespace_and_sorts():
    assert B.canonical("lever[powered=false,facing=east,face=floor]") == "minecraft:lever[face=floor,facing=east,powered=false]"


@pytest.mark.parametrize("bad", ["minecraft:redstone_wir", "minecraft:repeater[delay=9]", "minecraft:stone[foo=bar]",
                                 "minecraft:repeater[facing=up]", "not a block", "minecraft:lever[face"])
def test_canonical_rejects_invalid(bad):
    # Nucleation's set_block silently accepts all of these
    with pytest.raises(B.BlockError):
        B.canonical(bad)


def test_floating_detects_unsupported_components():
    world = {(0, 1, 0): B.canonical("minecraft:redstone_wire"),
             (2, 1, 0): B.canonical("minecraft:lever[face=wall,facing=east]"),
             (4, 1, 0): B.canonical("minecraft:redstone_wall_torch[facing=east]")}
    bad = {p for p, _, _ in B.floating(world)}
    assert bad == set(world)


def test_floating_accepts_supported_components():
    world = {(0, 0, 0): "minecraft:stone", (0, 1, 0): B.canonical("minecraft:redstone_wire"),
             (1, 1, 0): "minecraft:stone", (2, 1, 0): B.canonical("minecraft:redstone_wall_torch[facing=east]"),
             (5, 0, 0): "minecraft:smooth_stone", (5, 1, 0): B.canonical("minecraft:lever[face=floor,facing=east]")}
    assert list(B.floating(world)) == []


def test_support_rules():
    assert B.supports("minecraft:stone", "up")
    assert not B.supports("minecraft:redstone_wire", "up")
    assert B.supports(B.canonical("minecraft:oak_slab[type=top]"), "up")
    assert not B.supports(B.canonical("minecraft:oak_slab[type=bottom]"), "up")
    assert not B.supports("minecraft:air", "up")


def test_simulatable_matches_known_cases():
    from env.sim import simulatable
    assert simulatable(B.canonical("minecraft:lever"))
    assert simulatable(B.canonical("minecraft:stone_button"))
    assert not simulatable(B.canonical("minecraft:daylight_detector"))
    assert not simulatable(B.canonical("minecraft:spruce_pressure_plate"))
