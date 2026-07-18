"""
Edge case tests for replicate_blocks.
Tests various boundary conditions, error handling, and configuration combinations.

All tests marked @pytest.mark.server.
"""
import pytest
import time
from unittest.mock import patch, MagicMock

from simulation.replicator import replicate_blocks
from simulation.bridge import MinecraftBridge


# ---------------------------------------------------------------------------
# Basic edge cases
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_single_block_at_origin(bridge, test_region):
    """Place a single stone block at the origin of the test region."""
    ox, oy, oz = test_region
    blocks = [(0, 0, 0, "minecraft:stone", None)]
    bounds = ((0, 0, 0), (1, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:stone"
    )
    assert "PASS" in resp, f"Single block at origin not placed: {resp}"


@pytest.mark.server
def test_block_at_negative_offset(bridge, test_region):
    """Place a block at negative relative coordinates (should still work)."""
    ox, oy, oz = test_region
    blocks = [(-3, 2, -5, "minecraft:diamond_block", None)]
    bounds = ((-3, 2, -5), (-2, 3, -4))
    replicate_blocks(blocks, test_region, bounds, bridge)

    resp = bridge.run_command(
        f"mira_api check_block {ox - 3} {oy + 2} {oz - 5} minecraft:diamond_block"
    )
    assert "PASS" in resp, (
        f"Block at negative offset {ox-3},{oy+2},{oz-5} not placed: {resp}"
    )


@pytest.mark.server
def test_large_circuit_50_blocks(bridge, test_region):
    """Place 50+ blocks in a 5x10 grid and verify all are placed."""
    ox, oy, oz = test_region
    blocks = []
    for x in range(5):
        for z in range(10):
            blocks.append((x, 0, z, "minecraft:stone", None))
    assert len(blocks) == 50, f"Expected 50 blocks, got {len(blocks)}"

    min_x = min(b[0] for b in blocks)
    min_z = min(b[2] for b in blocks)
    max_x = max(b[0] for b in blocks) + 1
    max_z = max(b[2] for b in blocks) + 1
    bounds = ((min_x, 0, min_z), (max_x, 1, max_z))

    replicate_blocks(blocks, test_region, bounds, bridge)

    # Spot-check 10 positions across the grid
    for sx, sz in [(0, 0), (2, 3), (4, 5), (1, 7), (3, 9)]:
        resp = bridge.run_command(
            f"mira_api check_block {ox + sx} {oy + 0} {oz + sz} minecraft:stone"
        )
        assert "PASS" in resp, (
            f"Missing stone at ({sx},0,{sz}): {resp}"
        )


@pytest.mark.server
def test_empty_block_list(bridge, test_region):
    """Empty block list should not raise any errors."""
    blocks = []
    bounds = ((0, 0, 0), (1, 1, 1))
    # Should complete without exception
    replicate_blocks(blocks, test_region, bounds, bridge)
    # No blocks to verify — just confirm the call succeeded


# ---------------------------------------------------------------------------
# Sorting and order
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_blocks_sorted_y_x_z_order(bridge, test_region):
    """
    Verify blocks are built from bottom to top (Y), then X, then Z.
    Place blocks at different Y levels and confirm they're all placed correctly.
    """
    ox, oy, oz = test_region
    # Blocks at various Y levels that would break if sorted wrong
    blocks = [
        (0, 2, 0, "minecraft:diamond_block", None),   # top
        (0, 1, 0, "minecraft:stone", None),            # middle
        (0, 0, 0, "minecraft:gold_block", None),       # bottom
        (1, 0, 0, "minecraft:iron_block", None),       # bottom
        (1, 1, 0, "minecraft:emerald_block", None),    # middle
    ]
    bounds = ((0, 0, 0), (2, 3, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    checks = [
        (0, 0, 0, "minecraft:gold_block"),
        (0, 1, 0, "minecraft:stone"),
        (0, 2, 0, "minecraft:diamond_block"),
        (1, 0, 0, "minecraft:iron_block"),
        (1, 1, 0, "minecraft:emerald_block"),
    ]
    for rx, ry, rz, expected in checks:
        resp = bridge.run_command(
            f"mira_api check_block {ox + rx} {oy + ry} {oz + rz} {expected}"
        )
        assert "PASS" in resp, (
            f"Block ({rx},{ry},{rz}) mismatch: expected {expected}, got {resp}"
        )


# ---------------------------------------------------------------------------
# Container NBT handling
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_nbt_container_chest_with_items(bridge, test_region):
    """
    Place a chest with items via NBT data.
    Verifies the chest block exists and items are merged.
    """
    ox, oy, oz = test_region
    nbt_items = '{"Items":[{"id":"minecraft:diamond","count":1,"Slot":0b},{"id":"minecraft:stone","count":5,"Slot":1b}]}'
    blocks = [
        (0, 0, 0, "minecraft:chest[facing=north]", nbt_items),
    ]
    bounds = ((0, 0, 0), (1, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Check the chest exists
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:chest[facing=north]"
    )
    assert "PASS" in resp, f"Chest not placed: {resp}"

    # Check inventory via Scarpet API
    resp = bridge.run_command(
        f"mira_api check_inv {ox + 0} {oy + 0} {oz + 0} 0 1 minecraft:diamond"
    )
    assert "PASS" in resp, f"Diamond not found in chest slot 0: {resp}"

    resp = bridge.run_command(
        f"mira_api check_inv {ox + 0} {oy + 0} {oz + 0} 1 1 minecraft:stone"
    )


@pytest.mark.server
def test_nbt_container_empty_chest(bridge, test_region):
    """Place a chest without NBT — should exist with no items."""
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:chest[facing=north]", None),
    ]
    bounds = ((0, 0, 0), (1, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:chest[facing=north]"
    )
    assert "PASS" in resp, f"Empty chest not placed: {resp}"


# ---------------------------------------------------------------------------
# Entity handling
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_entity_in_block_list_skipped(bridge, test_region):
    """
    Entity blocks (starting with 'entity:') should be skipped without error.
    Regular blocks around them should still be placed.
    """
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:stone", None),
        (1, 0, 0, "entity:minecraft:armor_stand", None),
        (2, 0, 0, "minecraft:stone", None),
    ]
    bounds = ((0, 0, 0), (3, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Adjacent blocks should be placed
    resp0 = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:stone"
    )
    assert "PASS" in resp0, f"Block 0 not stone: {resp0}"

    resp2 = bridge.run_command(
        f"mira_api check_block {ox + 2} {oy + 0} {oz + 0} minecraft:stone"
    )
    assert "PASS" in resp2, f"Block 2 not stone: {resp2}"

    # Entity position should remain air (cleared, then skipped)
    resp1 = bridge.run_command(
        f"mira_api check_block {ox + 1} {oy + 0} {oz + 0} minecraft:air"
    )
    assert "PASS" in resp1, (
        f"Entity position should be air, got: {resp1}"
    )


# ---------------------------------------------------------------------------
# Configuration flags
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_use_updates_true(bridge, test_region):
    """
    use_updates=True should enable fillUpdates during building.
    Circuit should still function correctly after force_update_region.
    """
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:redstone_block", None),
        (1, 0, 0, "minecraft:redstone_lamp[lit=false]", None),
    ]
    bounds = ((0, 0, 0), (2, 1, 1))
    replicate_blocks(
        blocks, test_region, bounds, bridge,
        use_updates=True, force_update_region=True
    )

    # Tick to let redstone settle
    bridge.run_command("tick freeze")
    bridge.run_command("tick step 10")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)

    resp = bridge.run_command(
        f"mira_api check_block {ox + 1} {oy + 0} {oz + 0} minecraft:redstone_lamp[lit=true]"
    )
    assert "PASS" in resp, f"Lamp not lit with use_updates=True: {resp}"


@pytest.mark.server
def test_force_update_region_redstone(bridge, test_region):
    """
    force_update_region=True should trigger redstone propagation.
    Redstone block + lamp should work after force update.
    """
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:redstone_block", None),
        (1, 0, 0, "minecraft:redstone_lamp[lit=false]", None),
    ]
    bounds = ((0, 0, 0), (2, 1, 1))
    replicate_blocks(
        blocks, test_region, bounds, bridge, force_update_region=True
    )

    # Tick to let redstone settle
    bridge.run_command("tick freeze")
    bridge.run_command("tick step 10")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)

    resp = bridge.run_command(
        f"mira_api check_block {ox + 1} {oy + 0} {oz + 0} minecraft:redstone_lamp[lit=true]"
    )
    assert "PASS" in resp, (
        f"Lamp not lit after force_update_region=True: {resp}"
    )


# ---------------------------------------------------------------------------
# Bounds and origin
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_bounds_calculation(bridge, test_region):
    """
    Verify bounds determine the area that gets cleared.
    Place a pre-existing block just outside bounds — should survive.
    Place a pre-existing block inside bounds — should get cleared.
    """
    ox, oy, oz = test_region

    # Place a block just outside where bounds will clear
    bridge.set_block(ox + 5, oy + 0, oz + 0, "minecraft:diamond_block")

    # Place a block inside where bounds will clear
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:emerald_block")

    blocks = [(0, 0, 0, "minecraft:stone", None)]
    bounds = ((0, 0, 0), (1, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Inside bounds: should have been cleared and replaced with stone
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:stone"
    )
    assert "PASS" in resp, (
        f"Inside bounds: expected stone (was cleared), got: {resp}"
    )

    # Outside bounds: should still be diamond
    resp = bridge.run_command(
        f"mira_api check_block {ox + 5} {oy + 0} {oz + 0} minecraft:diamond_block"
    )
    assert "PASS" in resp, (
        f"Outside bounds: expected diamond_block (not cleared), got: {resp}"
    )


@pytest.mark.server
def test_origin_offset_non_zero(bridge):
    """
    Place blocks with a non-zero origin (e.g. y=100, x=1000).
    Verify blocks end up at absolute positions.
    """
    origin = (1000, 90, 2000)
    ox, oy, oz = origin

    # Forceload the chunk at the target coordinates (no players → chunks not loaded)
    bridge.run_command(f"forceload add {ox} {oz} {ox + 48} {oz + 48}")
    try:
        blocks = [(0, 0, 0, "minecraft:stone", None)]
        bounds = ((0, 0, 0), (1, 1, 1))
        replicate_blocks(blocks, origin, bounds, bridge)

        resp = bridge.run_command(
            f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:stone"
        )
        assert "PASS" in resp, (
            f"Block at non-zero origin ({ox},{oy},{oz}) not placed: {resp}"
        )
    finally:
        bridge.run_command(f"forceload remove {ox} {oz} {ox + 48} {oz + 48}")


# ---------------------------------------------------------------------------
# Mixed block types and overlap
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_mixed_block_types(bridge, test_region):
    """Place many different block types in a single call."""
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:stone", None),
        (1, 0, 0, "minecraft:diamond_block", None),
        (2, 0, 0, "minecraft:gold_block", None),
        (3, 0, 0, "minecraft:redstone_block", None),
        (4, 0, 0, "minecraft:redstone_lamp[lit=false]", None),
        (5, 0, 0, "minecraft:oak_stairs[facing=east]", None),
        (6, 0, 0, "minecraft:glass", None),
        (7, 0, 0, "minecraft:chest[facing=north]", None),
        (8, 0, 0, "minecraft:redstone_wire", None),
        (9, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
    ]
    bounds = ((0, 0, 0), (10, 1, 1))

    # Add support blocks for wire and lever
    support_blocks = [
        (8, -1, 0, "minecraft:stone", None),
        (9, -1, 0, "minecraft:stone", None),
    ]
    replicate_blocks(
        blocks + support_blocks, test_region, bounds, bridge
    )

    checks = [
        (0, "minecraft:stone"),
        (1, "minecraft:diamond_block"),
        (2, "minecraft:gold_block"),
        (3, "minecraft:redstone_block"),
        (4, "minecraft:redstone_lamp"),
        (5, "minecraft:oak_stairs[facing=east]"),
        (6, "minecraft:glass"),
        (7, "minecraft:chest[facing=north]"),
        (8, "minecraft:redstone_wire"),
        (9, "minecraft:lever[facing=east,powered=false]"),
    ]
    for rx, expected in checks:
        resp = bridge.run_command(
            f"mira_api check_block {ox + rx} {oy + 0} {oz + 0} {expected}"
        )
        assert "PASS" in resp, (
            f"Block at x={rx}: expected {expected}, got: {resp}"
        )


@pytest.mark.server
def test_overlapping_positions_last_wins(bridge, test_region):
    """
    When multiple blocks share the same position, the last one in the list
    should be the one that remains (since replicate_blocks places them
    sequentially and later setblock calls overwrite earlier ones).
    """
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:stone", None),
        (0, 0, 0, "minecraft:diamond_block", None),
        (0, 0, 0, "minecraft:gold_block", None),
    ]
    bounds = ((0, 0, 0), (1, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:gold_block"
    )
    assert "PASS" in resp, (
        f"Expected gold_block (last wins), got: {resp}"
    )


# ---------------------------------------------------------------------------
# Rate limiting and sleep behavior
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_rate_limiting_behavior(bridge, test_region):
    """
    When command count exceeds rate_limit, replicate_blocks should sleep.
    Use a large block count (100+) to trigger rate limiting.
    Verify all blocks are placed correctly despite rate limiting.
    """
    ox, oy, oz = test_region
    # Use a 10x10 grid (100 blocks) to stay within forceloaded area (48x48)
    blocks = []
    for x in range(10):
        for z in range(10):
            blocks.append((x, 0, z, "minecraft:stone", None))
    bounds = ((0, 0, 0), (10, 1, 10))

    # Use a low rate limit to force multiple sleep cycles
    replicate_blocks(blocks, test_region, bounds, bridge, rate_limit=50)

    # Spot-check first, middle, and last blocks
    check_positions = [(0, 0), (5, 5), (9, 9)]
    for x, z in check_positions:
        resp = bridge.run_command(
            f"mira_api check_block {ox + x} {oy + 0} {oz + z} minecraft:stone"
        )
        assert "PASS" in resp, (
            f"Block at ({x},0,{z}) not placed after rate-limited build: {resp}"
        )


# ---------------------------------------------------------------------------
# Network error retry (mocked)
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_retry_on_network_error(bridge, test_region):
    """
    Test that replicate_blocks retries on network errors.
    We mock bridge.set_block to fail twice then succeed.
    """
    ox, oy, oz = test_region
    call_count = [0]

    original_set_block = bridge.set_block

    def flaky_set_block(x, y, z, state, nbt=None):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise ConnectionError("Broken pipe: simulated network error")
        return original_set_block(x, y, z, state, nbt)

    with patch.object(bridge, 'set_block', side_effect=flaky_set_block):
        blocks = [(0, 0, 0, "minecraft:diamond_block", None)]
        bounds = ((0, 0, 0), (1, 1, 1))
        replicate_blocks(blocks, test_region, bounds, bridge)

    # Should have succeeded after retries
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:diamond_block"
    )
    assert "PASS" in resp, f"Block not placed after retry: {resp}"


# ---------------------------------------------------------------------------
# Large area edge cases
# ---------------------------------------------------------------------------

@pytest.mark.server
def test_large_area_exceeds_32k_fill_limit(bridge, test_region):
    """
    Test that replicate_blocks handles volumes that exceed the 32,768 block
    fill limit by chunking the fill operation.
    """
    ox, oy, oz = test_region
    # Create a volume that exceeds 32k (100x10x50 = 50k blocks for the fill area)
    fill_area = 100 * 1 * 50  # Just 50x50 = 50k if 1 block high
    # Actually the bounds determine the area cleared, not blocks placed.
    # Make bounds large: 50x10x50 = 25k (under 32k - safe) but let's do a big one
    # Set bounds to clear a 60x10x60 = 36k volume (over 32k)
    blocks = [(i, 0, 0, "minecraft:stone", None) for i in range(5)]

    # Bounds that exceed 32k fill volume
    bounds = ((0, 0, 0), (60, 10, 60))  # 60*10*60 = 36,000 > 32,768

    # Should complete without error (chunked fill)
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Verify the blocks were placed
    for idx in [0, 2, 4]:
        resp = bridge.run_command(
            f"mira_api check_block {ox + idx} {oy + 0} {oz + 0} minecraft:stone"
        )
        assert "PASS" in resp, (
            f"Block {idx} not placed with large bounds: {resp}"
        )
