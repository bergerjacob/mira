"""
Integration tests: dropper and dispenser placement and inventory.
Tests placement in all directions, inventory content via check_inv,
and block type replacement.
"""
import time
import pytest

pytestmark = pytest.mark.server


def _enable_fill_updates(bridge):
    bridge.run_command("carpet fillUpdates true")


def _tick_and_settle(ctx, ticks=20):
    ctx.tick(ticks)
    time.sleep(0.5)


def _add_item_to_slot(bridge, origin, slot, item, count=1):
    """Add items to a container's slot via the /item command (absolute coords)."""
    ox, oy, oz = origin
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.{slot} with {item} {count}"
    )


# ---------------------------------------------------------------------------
# Basic placement
# ---------------------------------------------------------------------------


def test_place_dropper_facing_east(ctx, bridge):
    """Place dropper[facing=east], verify exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:dropper[facing=east]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:dropper[facing=east]")


def test_place_dispenser_facing_north(ctx, bridge):
    """Place dispenser[facing=north], verify exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:dispenser[facing=north]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:dispenser[facing=north]")


# ---------------------------------------------------------------------------
# Inventory verification
# ---------------------------------------------------------------------------


def test_dropper_with_items(ctx, bridge, origin):
    """Place dropper, add diamonds to slot 0, check_inv -> PASS."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:dropper[facing=east]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    _add_item_to_slot(bridge, origin, 0, "minecraft:diamond", 64)
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    resp = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:diamond"
    )
    assert "PASS" in resp, f"Expected PASS for dropper with diamonds, got: {resp}"


def test_dispenser_with_items(ctx, bridge, origin):
    """Place dispenser, add arrows to slot 0, check_inv -> PASS."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:dispenser[facing=north]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    _add_item_to_slot(bridge, origin, 0, "minecraft:arrow", 32)
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    resp = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 0 32 minecraft:arrow"
    )
    assert "PASS" in resp, f"Expected PASS for dispenser with arrows, got: {resp}"


# ---------------------------------------------------------------------------
# Dropper facing all 6 directions
# ---------------------------------------------------------------------------


def test_dropper_facing_directions(ctx, bridge):
    """Place droppers in all 6 directions, verify each has correct facing."""
    _enable_fill_updates(bridge)
    facings = {
        (0, 0, 0): "down",
        (2, 0, 0): "up",
        (4, 0, 0): "north",
        (6, 0, 0): "south",
        (8, 0, 0): "west",
        (10, 0, 0): "east",
    }
    for (x, y, z), facing in facings.items():
        ctx.set_block((x, y, z), f"minecraft:dropper[facing={facing}]")
    _tick_and_settle(ctx)

    for (x, y, z), facing in facings.items():
        assert ctx.check_block((x, y, z), f"minecraft:dropper[facing={facing}]"), (
            f"Dropper facing {facing} at ({x},{y},{z}) not placed correctly"
        )


# ---------------------------------------------------------------------------
# Dispenser facing different directions
# ---------------------------------------------------------------------------


def test_dispenser_facing_directions(ctx, bridge):
    """Place dispensers in multiple directions, verify each has correct facing."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:dispenser[facing=down]")
    ctx.set_block((2, 0, 0), "minecraft:dispenser[facing=up]")
    ctx.set_block((4, 0, 0), "minecraft:dispenser[facing=south]")
    ctx.set_block((6, 0, 0), "minecraft:dispenser[facing=west]")
    _tick_and_settle(ctx)

    assert ctx.check_block((0, 0, 0), "minecraft:dispenser[facing=down]")
    assert ctx.check_block((2, 0, 0), "minecraft:dispenser[facing=up]")
    assert ctx.check_block((4, 0, 0), "minecraft:dispenser[facing=south]")
    assert ctx.check_block((6, 0, 0), "minecraft:dispenser[facing=west]")


# ---------------------------------------------------------------------------
# Dropper facing change on overwrite
# ---------------------------------------------------------------------------


def test_dropper_facing_change(ctx, bridge):
    """Place dropper facing east, overwrite facing north -> facing changes."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:dropper[facing=east]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:dropper[facing=east]")

    # Overwrite with north-facing
    ctx.set_block((0, 0, 0), "minecraft:dropper[facing=north]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:dropper[facing=north]")
    state = ctx.get_state((0, 0, 0))
    assert "north" in state, f"Dropper should face north, state: {state}"


# ---------------------------------------------------------------------------
# Dispenser with water bucket in slot
# ---------------------------------------------------------------------------


def test_dispenser_with_water_bucket(ctx, bridge, origin):
    """Place dispenser, add water bucket to slot 0, verify item in slot."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:dispenser[facing=north]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    _add_item_to_slot(bridge, origin, 0, "minecraft:water_bucket", 1)
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    resp = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 0 1 minecraft:water_bucket"
    )
    assert "PASS" in resp, f"Expected PASS for water bucket, got: {resp}"


# ---------------------------------------------------------------------------
# Dropper with multiple items in different slots
# ---------------------------------------------------------------------------


def test_dropper_multiple_slots(ctx, bridge, origin):
    """Place dropper, add items to slots 0, 1, 2, verify all."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:dropper[facing=east]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    _add_item_to_slot(bridge, origin, 0, "minecraft:diamond", 64)
    _add_item_to_slot(bridge, origin, 1, "minecraft:iron_ingot", 32)
    _add_item_to_slot(bridge, origin, 2, "minecraft:redstone", 16)
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    resp0 = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:diamond"
    )
    assert "PASS" in resp0, f"Slot 0 expected diamonds, got: {resp0}"

    resp1 = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 1 32 minecraft:iron_ingot"
    )
    assert "PASS" in resp1, f"Slot 1 expected iron ingots, got: {resp1}"

    resp2 = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 2 16 minecraft:redstone"
    )
    assert "PASS" in resp2, f"Slot 2 expected redstone, got: {resp2}"


# ---------------------------------------------------------------------------
# Overwrite dropper with dispenser
# ---------------------------------------------------------------------------


def test_overwrite_dropper_with_dispenser(ctx, bridge, origin):
    """Place dropper with items, overwrite with dispenser -> type changes, inventory cleared."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin

    # Place dropper with items
    bridge.set_block(ox, oy, oz, "minecraft:dropper[facing=east]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    _add_item_to_slot(bridge, origin, 0, "minecraft:diamond", 64)
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    # Verify dropper exists with items
    resp_before = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:diamond"
    )
    assert "PASS" in resp_before, f"Expected diamonds before overwrite, got: {resp_before}"
    assert ctx.check_block((0, 0, 0), "minecraft:dropper[facing=east]"), (
        "Should be a dropper before overwrite"
    )

    # Overwrite with dispenser
    bridge.set_block(ox, oy, oz, "minecraft:dispenser[facing=north]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    # Verify it's now a dispenser
    assert ctx.check_block((0, 0, 0), "minecraft:dispenser[facing=north]"), (
        "Should be a dispenser after overwrite"
    )

    # Inventory should be cleared after replacement
    resp_after = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 0 0 minecraft:air"
    )
    assert "PASS" in resp_after, f"Expected empty inventory after overwrite, got: {resp_after}"


# ---------------------------------------------------------------------------
# Dispenser empty inventory check
# ---------------------------------------------------------------------------


def test_dispenser_empty_inventory(ctx, bridge, origin):
    """Place dispenser, check_inv slot 0 for air -> PASS (empty)."""
    _enable_fill_updates(bridge)
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:dispenser[facing=north]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    resp = bridge.run_command(
        f"mira_api check_inv {ox} {oy} {oz} 0 0 minecraft:air"
    )
    assert "PASS" in resp, f"Expected PASS for empty slot, got: {resp}"
