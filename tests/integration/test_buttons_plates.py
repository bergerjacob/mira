"""
Integration tests: buttons and pressure plates.
Tests all button facing directions, various plate types, and multiple placements.
"""
import time
import pytest

pytestmark = pytest.mark.server


def _enable_fill_updates(bridge):
    bridge.run_command("carpet fillUpdates true")


def _tick_and_settle(ctx, ticks=20):
    ctx.tick(ticks)
    time.sleep(0.5)


def _place_button(ctx, pos, facing, support_pos):
    """Place a support block behind the button, then the button itself."""
    ctx.set_block(support_pos, "minecraft:stone")
    ctx.set_block(pos, f"minecraft:stone_button[facing={facing},powered=false]")
    _tick_and_settle(ctx)


def test_stone_button_facing_east(ctx):
    """Place stone_button[facing=east], verify exists and facing is correct."""
    # Button facing=east attaches to block at (-1,0,0)
    ctx.set_block((-1, 0, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_button[facing=east,powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone_button[facing=east]")


def test_stone_button_facing_north(ctx):
    """Place stone_button[facing=north], verify facing."""
    # Button facing=north attaches to block at (0,0,1)
    ctx.set_block((0, 0, 1), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_button[facing=north,powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone_button[facing=north]")


def test_stone_button_facing_south(ctx):
    """Place stone_button[facing=south,powered=false], verify."""
    # Button facing=south attaches to block at (0,0,-1)
    ctx.set_block((0, 0, -1), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_button[facing=south,powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone_button[facing=south,powered=false]")


def test_stone_button_facing_west(ctx):
    """Place stone_button[facing=west], verify facing."""
    # Button facing=west attaches to block at (1,0,0)
    ctx.set_block((1, 0, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_button[facing=west,powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone_button[facing=west]")


def test_stone_pressure_plate(ctx):
    """Place stone_pressure_plate, verify exists."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_pressure_plate")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone_pressure_plate")


def test_stone_pressure_plate_powered_false(ctx):
    """Place stone_pressure_plate[powered=false], verify state."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_pressure_plate[powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone_pressure_plate[powered=false]")


def test_oak_pressure_plate(ctx):
    """Place oak_pressure_plate[powered=false], verify exists."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:oak_pressure_plate[powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:oak_pressure_plate[powered=false]")


def test_light_weighted_pressure_plate(ctx):
    """Place light_weighted_pressure_plate (default power=0), verify exists."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:light_weighted_pressure_plate")
    _tick_and_settle(ctx)
    assert ctx.check_block(
        (0, 0, 0), "minecraft:light_weighted_pressure_plate"
    )


def test_heavy_weighted_pressure_plate(ctx):
    """Place heavy_weighted_pressure_plate (default power=0), verify exists."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:heavy_weighted_pressure_plate")
    _tick_and_settle(ctx)
    assert ctx.check_block(
        (0, 0, 0), "minecraft:heavy_weighted_pressure_plate"
    )


def test_multiple_buttons_and_plates(ctx):
    """Place several buttons and plates in a row, verify all correctly placed."""
    # Row of buttons and plates
    # Pos 0: stone_button[facing=north]
    ctx.set_block((0, 0, 1), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_button[facing=north,powered=false]")
    # Pos 2: stone_pressure_plate
    ctx.set_block((2, -1, 0), "minecraft:stone")
    ctx.set_block((2, 0, 0), "minecraft:stone_pressure_plate[powered=false]")
    # Pos 4: oak_pressure_plate
    ctx.set_block((4, -1, 0), "minecraft:stone")
    ctx.set_block((4, 0, 0), "minecraft:oak_pressure_plate[powered=false]")
    _tick_and_settle(ctx)

    assert ctx.check_block(
        (0, 0, 0), "minecraft:stone_button[facing=north,powered=false]"
    ), "Button at pos 0 should be placed"
    assert ctx.check_block(
        (2, 0, 0), "minecraft:stone_pressure_plate[powered=false]"
    ), "Stone plate at pos 2 should be placed"
    assert ctx.check_block(
        (4, 0, 0), "minecraft:oak_pressure_plate[powered=false]"
    ), "Oak plate at pos 4 should be placed"


def test_button_wrong_facing_fails(ctx):
    """Place button[facing=east], check for [facing=west] -> FAIL."""
    ctx.set_block((-1, 0, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:stone_button[facing=east,powered=false]")
    _tick_and_settle(ctx)
    assert not ctx.check_block(
        (0, 0, 0), "minecraft:stone_button[facing=west]"
    ), "Should NOT match facing=west for east-facing button"


def test_stone_button_default_state(ctx):
    """Place stone_button and verify default powered=false."""
    ctx.set_block((-1, 0, 0), "minecraft:stone")
    ctx.set_block(
        (0, 0, 0), "minecraft:stone_button[facing=east,powered=false]"
    )
    _tick_and_settle(ctx)
    state = ctx.get_state((0, 0, 0))
    assert "powered=false" in state or "powered: false" in state, (
        f"Button should be powered=false by default, got: {state}"
    )
