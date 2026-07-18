"""
Integration tests: hopper block placement, facing, and inventory checks.
Tests all 5 hopper facing directions, item storage, and overwrite behavior.
"""
import time
import pytest

pytestmark = pytest.mark.server


def _enable_fill_updates(bridge):
    bridge.run_command("carpet fillUpdates true")


def _tick_and_settle(ctx, ticks=20):
    ctx.tick(ticks)
    time.sleep(0.5)


def test_hopper_facing_down(ctx):
    """Place hopper[facing=down], verify block exists and facing is correct."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:hopper[facing=down]")


def test_hopper_facing_north(ctx):
    """Place hopper[facing=north], verify facing."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=north]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:hopper[facing=north]")


def test_hopper_facing_south(ctx):
    """Place hopper[facing=south], verify facing."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=south]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:hopper[facing=south]")


def test_hopper_facing_east(ctx):
    """Place hopper[facing=east], verify facing."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=east]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:hopper[facing=east]")


def test_hopper_facing_west(ctx):
    """Place hopper[facing=west], verify facing."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=west]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:hopper[facing=west]")


def test_hopper_with_items(ctx, bridge, origin):
    """Place hopper, add diamonds to slot 0, verify via check_inv."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64"
    )
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    assert ctx.check_inv(
        (0, 0, 0), 0, 64, "minecraft:diamond"
    ), "Hopper slot 0 should contain 64 diamonds"


def test_hopper_multiple_items(ctx, bridge, origin):
    """Place hopper, add items to multiple slots, verify both."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64"
    )
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.1 with minecraft:iron_ingot 32"
    )
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    assert ctx.check_inv(
        (0, 0, 0), 0, 64, "minecraft:diamond"
    ), "Slot 0 should have 64 diamonds"
    assert ctx.check_inv(
        (0, 0, 0), 1, 32, "minecraft:iron_ingot"
    ), "Slot 1 should have 32 iron_ingots"


def test_hopper_change_facing(ctx):
    """Place hopper[facing=down], then replace with [facing=north], verify change."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:hopper[facing=down]")
    # Change facing
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=north]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:hopper[facing=north]")


def test_hopper_chain(ctx):
    """Place multiple hoppers pointing same direction — verify all placed."""
    for i in range(5):
        ctx.set_block((i, -1, 0), "minecraft:stone")
        ctx.set_block((i, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    for i in range(5):
        assert ctx.check_block(
            (i, 0, 0), "minecraft:hopper[facing=down]"
        ), f"Hopper at offset {i} missing"


def test_overwrite_hopper_clears_inventory(ctx, bridge, origin):
    """Place hopper with items, replace with new hopper, verify inventory cleared."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    # Add items
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64"
    )
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    # Verify items present
    assert ctx.check_inv(
        (0, 0, 0), 0, 64, "minecraft:diamond"
    ), "Items should exist before overwrite"
    # Overwrite with a new hopper (block replacement clears inventory)
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    # After replacement, slot 0 should be empty
    assert ctx.check_inv(
        (0, 0, 0), 0, 0, "minecraft:air"
    ), "Hopper inventory should be empty after overwrite"


def test_hopper_wrong_facing_fails(ctx):
    """Place hopper[facing=down], check for [facing=north] -> FAIL."""
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((0, 0, 0), "minecraft:hopper[facing=down]")
    _tick_and_settle(ctx)
    assert not ctx.check_block(
        (0, 0, 0), "minecraft:hopper[facing=north]"
    ), "Should NOT match facing=north when hopper is facing=down"
