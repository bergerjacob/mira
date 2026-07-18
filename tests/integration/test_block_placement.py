"""
Integration tests: basic block placement and verification.
Tests the ability to place blocks, check them, and verify properties.
"""
import pytest

pytestmark = pytest.mark.server


def test_place_single_block(ctx):
    """Place stone, verify with check_block -> PASS."""
    ctx.set_block((0, 0, 0), "minecraft:stone")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:stone")


def test_place_with_properties(ctx):
    """Place repeater[facing=north], verify properties match."""
    # Repeater needs a support block underneath or it breaks when fillUpdates=true
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:repeater[facing=north]")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:repeater[facing=north]")


def test_place_directional_stairs(ctx):
    """Place oak_stairs[facing=west], verify."""
    ctx.set_block((0, 0, 0), "minecraft:oak_stairs[facing=west]")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:oak_stairs[facing=west]")


def test_place_furnace(ctx):
    """Place furnace[facing=north,lit=false], verify."""
    ctx.set_block((0, 0, 0), "minecraft:furnace[facing=north,lit=false]")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:furnace[facing=north,lit=false]")


def test_place_piston(ctx):
    """Place piston[facing=up,extended=false], verify."""
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=up,extended=false]")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=up,extended=false]")


def test_overwrite_block(ctx):
    """Place stone, then glass at same pos, verify glass."""
    ctx.set_block((0, 0, 0), "minecraft:stone")
    ctx.tick()
    ctx.set_block((0, 0, 0), "minecraft:glass")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:glass")


def test_clear_region(ctx, bridge, origin):
    """Fill area with stone, then fill with air, verify air."""
    ox, oy, oz = origin
    ctx.set_block((0, 0, 0), "minecraft:stone")
    ctx.set_block((1, 0, 0), "minecraft:stone")
    ctx.set_block((0, 0, 1), "minecraft:stone")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:stone")

    # Fill with air using absolute coordinates
    bridge.fill(ox, oy, oz, ox + 5, oy + 5, oz + 5, "minecraft:air")
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:air")
    assert ctx.check_block((1, 0, 0), "minecraft:air")
    assert ctx.check_block((0, 0, 1), "minecraft:air")


def test_place_many_blocks(ctx):
    """Place 50 blocks in a line, verify all present."""
    for i in range(50):
        ctx.set_block((i, 0, 0), "minecraft:stone")
    ctx.tick()
    for i in range(50):
        assert ctx.check_block((i, 0, 0), "minecraft:stone"), f"Block at offset {i} missing"


def test_partial_property_match(ctx):
    """Place repeater with full props, check with only one prop -> PASS."""
    # Repeater needs a support block underneath or it breaks when fillUpdates=true
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block(
        (0, 0, 0),
        "minecraft:repeater[delay=2,facing=west,locked=false,powered=false]",
    )
    ctx.tick()
    assert ctx.check_block((0, 0, 0), "minecraft:repeater[facing=west]")


def test_wrong_block_fails(ctx):
    """Place stone, check for glass -> check_block returns False."""
    ctx.set_block((0, 0, 0), "minecraft:stone")
    ctx.tick()
    assert not ctx.check_block((0, 0, 0), "minecraft:glass")


def test_wrong_property_fails(ctx):
    """Place lever[powered=false], check [powered=true] -> check_block returns False."""
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=false]")
    ctx.tick()
    assert not ctx.check_block((0, 0, 0), "minecraft:lever[powered=true]")
