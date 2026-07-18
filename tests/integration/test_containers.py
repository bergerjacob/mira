"""
Integration tests: container blocks and inventory verification.
Tests chests, barrels, and their inventory slots via the Scarpet API.
"""
import time
import pytest

pytestmark = pytest.mark.server


# ---------------------------------------------------------------------------
# Block placement tests
# ---------------------------------------------------------------------------


def test_place_chest(ctx):
    """Place chest, verify block exists."""
    ctx.set_block((0, 0, 0), "minecraft:chest")
    ctx.tick()
    time.sleep(0.5)
    assert ctx.check_block((0, 0, 0), "minecraft:chest"), "Chest should be placed"


def test_place_barrel(ctx):
    """Place barrel, verify block exists."""
    ctx.set_block((0, 0, 0), "minecraft:barrel")
    ctx.tick()
    time.sleep(0.5)
    assert ctx.check_block((0, 0, 0), "minecraft:barrel"), "Barrel should be placed"


# ---------------------------------------------------------------------------
# Empty inventory tests
# ---------------------------------------------------------------------------


def test_chest_empty_inventory(ctx, bridge, origin):
    """Place chest, check_inv slot 0 for air -> PASS."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 0 minecraft:air")
    assert "PASS" in resp, f"Expected PASS for empty slot, got: {resp}"


# ---------------------------------------------------------------------------
# Chest with items
# ---------------------------------------------------------------------------


def test_chest_with_items(ctx, bridge, origin):
    """Place chest, add diamonds to slot 0, check_inv -> PASS."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:diamond")
    assert "PASS" in resp, f"Expected PASS for diamond in slot 0, got: {resp}"


def test_chest_wrong_item(ctx, bridge, origin):
    """Place chest with diamonds, check for dirt -> FAIL."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:dirt")
    assert "FAIL" in resp, f"Expected FAIL for wrong item, got: {resp}"


def test_chest_wrong_count(ctx, bridge, origin):
    """Place chest with 64 diamonds, check for 32 -> FAIL (count mismatch)."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 32 minecraft:diamond")
    assert "FAIL" in resp, f"Expected FAIL for wrong count, got: {resp}"


def test_chest_multiple_slots(ctx, bridge, origin):
    """Place chest, add items to slots 0 and 1, verify both."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64"
    )
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.1 with minecraft:iron_ingot 32"
    )
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    # Verify slot 0
    resp0 = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:diamond")
    assert "PASS" in resp0, f"Expected PASS for slot 0, got: {resp0}"

    # Verify slot 1
    resp1 = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 1 32 minecraft:iron_ingot")
    assert "PASS" in resp1, f"Expected PASS for slot 1, got: {resp1}"


# ---------------------------------------------------------------------------
# Barrel with items
# ---------------------------------------------------------------------------


def test_barrel_with_items(ctx, bridge, origin):
    """Place barrel, add items, verify."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:barrel")
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.0 with minecraft:emerald 16"
    )
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 16 minecraft:emerald")
    assert "PASS" in resp, f"Expected PASS for barrel with emeralds, got: {resp}"


# ---------------------------------------------------------------------------
# Overwrite container — items should be cleared
# ---------------------------------------------------------------------------


def test_overwrite_container(ctx, bridge, origin):
    """Place chest with items, replace with new chest, verify empty."""
    ox, oy, oz = origin

    # Place first chest with items
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(
        f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64"
    )
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    # Verify items are there
    resp_before = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:diamond")
    assert "PASS" in resp_before, f"Expected PASS before overwrite, got: {resp_before}"

    # Overwrite with a new chest (replaces the block, clearing inventory)
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command("tick step 20")
    time.sleep(0.5)

    # After replacement, slot 0 should be empty
    resp_after = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 0 minecraft:air")
    assert "PASS" in resp_after, f"Expected PASS (empty) after overwrite, got: {resp_after}"
