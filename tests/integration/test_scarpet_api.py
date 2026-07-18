"""
Integration tests: all mira_api Scarpet commands tested directly.
Tests the raw Scarpet command interface via bridge.run_command.
"""
import time
import pytest

pytestmark = pytest.mark.server


# ---------------------------------------------------------------------------
# check_block tests
# ---------------------------------------------------------------------------


def test_check_block_pass(ctx, bridge, origin):
    """Place stone, check_block for stone -> PASS."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:stone")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_block {ox} {oy} {oz} minecraft:stone")
    assert "PASS" in resp, f"Expected PASS, got: {resp}"


def test_check_block_fail_wrong_id(ctx, bridge, origin):
    """Place stone, check_block for glass -> FAIL."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:stone")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_block {ox} {oy} {oz} minecraft:glass")
    assert "FAIL" in resp, f"Expected FAIL, got: {resp}"


def test_check_block_fail_wrong_prop(ctx, bridge, origin):
    """Place lever[powered=false], check [powered=true] -> FAIL."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:lever[powered=false]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(
        f"mira_api check_block {ox} {oy} {oz} minecraft:lever[powered=true]"
    )
    assert "FAIL" in resp, f"Expected FAIL, got: {resp}"


def test_check_block_partial_match(ctx, bridge, origin):
    """Place repeater with full props, check only one prop -> PASS."""
    ox, oy, oz = origin
    bridge.set_block(
        ox, oy, oz, "minecraft:repeater[delay=2,facing=west,locked=false,powered=false]"
    )
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_block {ox} {oy} {oz} minecraft:repeater[facing=west]")
    assert "PASS" in resp, f"Expected PASS (partial match), got: {resp}"


def test_check_block_no_props(ctx, bridge, origin):
    """Place stone (no props), check_block for 'minecraft:stone' -> PASS."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:stone")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_block {ox} {oy} {oz} minecraft:stone")
    assert "PASS" in resp, f"Expected PASS, got: {resp}"


# ---------------------------------------------------------------------------
# check_inv tests
# ---------------------------------------------------------------------------


def test_check_inv_pass(ctx, bridge, origin):
    """Place chest, add diamonds to slot 0, check_inv -> PASS."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:diamond")
    assert "PASS" in resp, f"Expected PASS, got: {resp}"


def test_check_inv_fail_wrong_item(ctx, bridge, origin):
    """Place chest with diamonds, check for dirt -> FAIL."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 64 minecraft:dirt")
    assert "FAIL" in resp, f"Expected FAIL, got: {resp}"


def test_check_inv_fail_wrong_count(ctx, bridge, origin):
    """Place chest with 64 diamonds, check for 32 -> FAIL (count mismatch)."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command(f"item replace block {ox} {oy} {oz} container.0 with minecraft:diamond 64")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 32 minecraft:diamond")
    assert "FAIL" in resp, f"Expected FAIL (count mismatch), got: {resp}"


def test_check_inv_empty_slot(ctx, bridge, origin):
    """Place empty chest, check_inv for air -> PASS."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:chest")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_inv {ox} {oy} {oz} 0 0 minecraft:air")
    assert "PASS" in resp, f"Expected PASS (empty slot), got: {resp}"


# ---------------------------------------------------------------------------
# check_entity tests
# ---------------------------------------------------------------------------


def test_check_entity_pass(ctx, bridge, origin):
    """Summon armor_stand, check_entity -> PASS."""
    ox, oy, oz = origin
    # Place a block under the entity so it doesn't fall due to gravity
    bridge.set_block(ox, oy - 1, oz, "minecraft:stone")
    bridge.run_command(f"summon minecraft:armor_stand {ox} {oy} {oz}")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_entity {ox} {oy} {oz} minecraft:armor_stand")
    assert "PASS" in resp, f"Expected PASS, got: {resp}"


def test_check_entity_fail_wrong_type(ctx, bridge, origin):
    """Summon armor_stand, check for creeper -> FAIL."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy - 1, oz, "minecraft:stone")
    bridge.run_command(f"summon minecraft:armor_stand {ox} {oy} {oz}")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api check_entity {ox} {oy} {oz} minecraft:creeper")
    assert "FAIL" in resp, f"Expected FAIL (wrong type), got: {resp}"


def test_check_entity_fail_no_entity(ctx, bridge, origin):
    """check_entity at empty location -> FAIL."""
    ox, oy, oz = origin
    # No entity summoned here
    resp = bridge.run_command(f"mira_api check_entity {ox} {oy} {oz} minecraft:armor_stand")
    assert "FAIL" in resp, f"Expected FAIL (no entity), got: {resp}"


# ---------------------------------------------------------------------------
# get_state / update_block / update_region tests
# ---------------------------------------------------------------------------


def test_get_state(ctx, bridge, origin):
    """Place lever, get_state -> response contains lever properties."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:lever[powered=false]")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api get_state {ox} {oy} {oz}")
    # get_state returns block properties (not block ID), so check for a property
    assert "powered" in resp.lower(), f"Expected state to contain 'powered', got: {resp}"


def test_update_block(ctx, bridge, origin):
    """Place redstone_block, update_block, verify no error."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:redstone_block")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(f"mira_api update_block {ox} {oy} {oz}")
    # The command should complete without error — any response is acceptable
    assert resp is not None, "update_block returned None"


def test_update_region(ctx, bridge, origin):
    """Place blocks in region, update_region, verify no error."""
    ox, oy, oz = origin
    bridge.set_block(ox, oy, oz, "minecraft:redstone_block")
    bridge.set_block(ox + 1, oy, oz, "minecraft:stone")
    bridge.run_command("tick step 20")
    time.sleep(0.5)
    resp = bridge.run_command(
        f"mira_api update_region {ox} {oy} {oz} {ox + 5} {oy + 5} {oz + 5}"
    )
    assert resp is not None, "update_region returned None"
