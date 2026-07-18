"""
Tests for the replicate_blocks function directly against the running Minecraft server.

All tests require a running Minecraft server (RCON on localhost:25575).
"""
import pytest
import time

from simulation.replicator import replicate_blocks


@pytest.mark.server
def test_replicate_3_blocks(bridge, test_region):
    """Place 3 simple blocks and verify all are present."""
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:stone", None),
        (1, 0, 0, "minecraft:stone", None),
        (2, 0, 0, "minecraft:diamond_block", None),
    ]
    bounds = ((0, 0, 0), (3, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Verify each block
    resp = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:stone")
    assert "PASS" in resp, f"Block 0 not stone: {resp}"

    resp = bridge.run_command(f"mira_api check_block {ox+1} {oy+0} {oz+0} minecraft:stone")
    assert "PASS" in resp, f"Block 1 not stone: {resp}"

    resp = bridge.run_command(f"mira_api check_block {ox+2} {oy+0} {oz+0} minecraft:diamond_block")
    assert "PASS" in resp, f"Block 2 not diamond: {resp}"


@pytest.mark.server
def test_replicate_15_blocks(bridge, test_region):
    """Place 15 blocks in a 3x5 pattern and verify all are placed."""
    ox, oy, oz = test_region
    blocks = []
    for x in range(3):
        for z in range(5):
            blocks.append((x, 0, z, "minecraft:stone", None))

    bounds = ((0, 0, 0), (3, 1, 5))
    replicate_blocks(blocks, test_region, bounds, bridge)

    for x in range(3):
        for z in range(5):
            resp = bridge.run_command(f"mira_api check_block {ox+x} {oy+0} {oz+z} minecraft:stone")
            assert "PASS" in resp, (
                f"Missing stone at {x},0,{z}: {resp}"
            )


@pytest.mark.server
def test_replicate_directional_blocks(bridge, test_region):
    """Place blocks with directional properties and verify states are preserved."""
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:oak_stairs[facing=north]", None),
        (1, 0, 0, "minecraft:oak_stairs[facing=east]", None),
        (2, 0, 0, "minecraft:oak_stairs[facing=south]", None),
    ]
    bounds = ((0, 0, 0), (3, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Use Scarpet API to check exact block states
    resp = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:oak_stairs[facing=north]")
    assert "PASS" in resp, f"north stairs mismatch: {resp}"

    resp = bridge.run_command(f"mira_api check_block {ox+1} {oy+0} {oz+0} minecraft:oak_stairs[facing=east]")
    assert "PASS" in resp, f"east stairs mismatch: {resp}"

    resp = bridge.run_command(f"mira_api check_block {ox+2} {oy+0} {oz+0} minecraft:oak_stairs[facing=south]")
    assert "PASS" in resp, f"south stairs mismatch: {resp}"


@pytest.mark.server
def test_replicate_with_force_update(bridge, test_region):
    """Use force_update_region=True and verify redstone propagates."""
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:redstone_block", None),
        (1, 0, 0, "minecraft:redstone_lamp[lit=false]", None),
    ]
    bounds = ((0, 0, 0), (2, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge, force_update_region=True)

    bridge.run_command("tick freeze")
    bridge.run_command("tick step 10")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)

    resp = bridge.run_command(f"mira_api check_block {ox+1} {oy+0} {oz+0} minecraft:redstone_lamp[lit=true]")
    assert "PASS" in resp, f"Lamp not lit after force update: {resp}"


@pytest.mark.server
def test_replicate_overwrite(bridge, test_region):
    """Place blocks, then replace with different blocks at same location."""
    ox, oy, oz = test_region
    # First placement
    blocks_a = [(0, 0, 0, "minecraft:stone", None)]
    bounds = ((0, 0, 0), (1, 1, 1))
    replicate_blocks(blocks_a, test_region, bounds, bridge)

    resp = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:stone")
    assert "PASS" in resp, f"Expected stone: {resp}"

    # Overwrite with different block
    blocks_b = [(0, 0, 0, "minecraft:diamond_block", None)]
    replicate_blocks(blocks_b, test_region, bounds, bridge)

    resp = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:diamond_block")
    assert "PASS" in resp, (
        f"Expected diamond_block after overwrite: {resp}"
    )


@pytest.mark.server
def test_replicate_clears_area_first(bridge, test_region):
    """Place blocks in target area, then replicate different blocks — old blocks must be gone."""
    ox, oy, oz = test_region

    # Manually place a block that should be cleared
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:diamond_block")

    # Now replicate a different block over the same area
    blocks = [(0, 0, 0, "minecraft:stone", None)]
    bounds = ((0, 0, 0), (1, 1, 1))
    replicate_blocks(blocks, test_region, bounds, bridge)

    resp = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:stone")
    assert "PASS" in resp, (
        f"Expected stone (old diamond_block should be cleared): {resp}"
    )


@pytest.mark.server
def test_replicate_stability_10x(bridge, test_region):
    """Run a simple 3-block circuit build 10 times at the same origin — all must succeed."""
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:stone", None),
        (1, 0, 0, "minecraft:stone", None),
        (2, 0, 0, "minecraft:stone", None),
    ]
    bounds = ((0, 0, 0), (3, 1, 1))

    for i in range(10):
        replicate_blocks(blocks, test_region, bounds, bridge)

        resp0 = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:stone")
        resp1 = bridge.run_command(f"mira_api check_block {ox+1} {oy+0} {oz+0} minecraft:stone")
        resp2 = bridge.run_command(f"mira_api check_block {ox+2} {oy+0} {oz+0} minecraft:stone")

        assert "PASS" in resp0, (
            f"Run {i}: Block 0 not stone: {resp0}"
        )
        assert "PASS" in resp1, (
            f"Run {i}: Block 1 not stone: {resp1}"
        )
        assert "PASS" in resp2, (
            f"Run {i}: Block 2 not stone: {resp2}"
        )


@pytest.mark.server
def test_replicate_entity_skipped(bridge, test_region):
    """Entity blocks (starting with 'entity:') should be skipped without error."""
    ox, oy, oz = test_region
    blocks = [
        (0, 0, 0, "minecraft:stone", None),
        (1, 0, 0, "entity:minecraft:armor_stand", None),
        (2, 0, 0, "minecraft:stone", None),
    ]
    bounds = ((0, 0, 0), (3, 1, 1))

    # Should not raise any exception
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Regular blocks should still be placed
    resp0 = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:stone")
    assert "PASS" in resp0, (
        f"Block 0 not stone: {resp0}"
    )

    resp2 = bridge.run_command(f"mira_api check_block {ox+2} {oy+0} {oz+0} minecraft:stone")
    assert "PASS" in resp2, (
        f"Block 2 not stone: {resp2}"
    )

    # The area at entity position should be air (cleared, entity block skipped)
    # Note: the entity won't be placed, so the position should be air
    time.sleep(0.2)  # Give server a moment
