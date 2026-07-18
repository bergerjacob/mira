"""
Advanced stability tests — run the same operations many times to verify reliability.

All tests marked @pytest.mark.server and @pytest.mark.stability.
Uses test_region (coordinate isolation), ctx (VerificationContext), and bridge.
"""
import pytest
import time


# ===========================================================================
# 1. Single block placement stability (100x)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(100))
def test_single_block_100x(run, bridge, test_region):
    """Place and verify a single stone block 100 times in a row."""
    ox, oy, oz = test_region
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:stone")
    time.sleep(0.05)
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:stone"
    )
    assert "PASS" in resp, (
        f"Run {run}: Block not stone: {resp}"
    )


# ===========================================================================
# 2. Lever toggle stability (50x ON/OFF cycles)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(50))
def test_lever_toggle_50x(run, load_circuit, build_circuit, ctx, test_region):
    """
    Build lever+dust+lamp, toggle lever ON then OFF.
    Verify correct state after each toggle.
    """
    fixture = load_circuit("lever_lamp")
    build_circuit(fixture, test_region)

    # Initial: off
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=false]")

    # Toggle ON
    ctx.set_block([0, 0, 0], "minecraft:lever[facing=east,powered=true]")
    ctx.tick(20)
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=true]")

    # Toggle OFF
    ctx.set_block([0, 0, 0], "minecraft:lever[facing=east,powered=false]")
    ctx.tick(20)
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=false]")


# ===========================================================================
# 3. Circuit build + clear + rebuild stability (30x)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(15))
def test_circuit_build_15x(run, load_circuit, build_and_verify, test_region):
    """
    Build the lever_lamp fixture 15 times at different origins,
    verify each works correctly.
    """
    fixture = load_circuit("lever_lamp")
    ctx = build_and_verify(fixture, test_region)

    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=false]")
    ctx.set_block([0, 0, 0], "minecraft:lever[facing=east,powered=true]")
    ctx.tick(20)
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=true]")


@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(15))
def test_clear_and_rebuild_15x(run, bridge, test_region, load_circuit, build_circuit):
    """Build a circuit, clear area, rebuild, verify 15 times."""
    ox, oy, oz = test_region
    fixture = load_circuit("lever_lamp")

    # Build
    build_circuit(fixture, test_region)
    time.sleep(0.2)

    # Verify built
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:lever"
    )
    assert "PASS" in resp, (
        f"Run {run}: Build failed — lever not found: {resp}"
    )

    # Clear area
    bridge.run_command("carpet fillUpdates false")
    bridge.fill(ox - 2, oy - 2, oz - 2, ox + 5, oy + 2, oz + 5, "minecraft:air")
    bridge.run_command("carpet fillUpdates true")
    time.sleep(0.2)

    # Verify cleared
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:air"
    )
    assert "PASS" in resp, (
        f"Run {run}: Clear failed — lever not removed: {resp}"
    )

    # Rebuild
    build_circuit(fixture, test_region)
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:lever"
    )
    assert "PASS" in resp, (
        f"Run {run}: Rebuild failed — lever not found: {resp}"
    )


# ===========================================================================
# 4. Scarpet check_block stability (20x, same position)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(20))
def test_scarpet_check_block_20x(run, bridge, test_region):
    """Place a block, then check_block 20 times on the same position."""
    ox, oy, oz = test_region
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:diamond_block")
    time.sleep(0.05)

    for i in range(20):
        resp = bridge.run_command(
            f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:diamond_block"
        )
        assert "PASS" in resp, (
            f"Run {run}, check iteration {i}: check_block failed: {resp}"
        )


# ===========================================================================
# 5. Container place + inventory check + clear (20x)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(20))
def test_container_20x(run, bridge, test_region, ctx):
    """Place a chest, add items, verify inventory, then clear."""
    ox, oy, oz = test_region

    # Place chest
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:chest[facing=north]")
    time.sleep(0.1)

    # Verify chest placed
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:chest[facing=north]"
    )
    assert "PASS" in resp, (
        f"Run {run}: Chest not placed: {resp}"
    )

    # Add item via data merge
    bridge.run_command(
        f"data merge block {ox + 0} {oy + 0} {oz + 0} "
        '{Items:[{id:"minecraft:diamond",count:1,Slot:0b}]}'
    )
    time.sleep(0.1)

    # Verify item in slot
    resp = bridge.run_command(
        f"mira_api check_inv {ox + 0} {oy + 0} {oz + 0} 0 1 minecraft:diamond"
    )
    assert "PASS" in resp, (
        f"Run {run}: Diamond not found in chest: {resp}"
    )

    # Clear
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:air")
    time.sleep(0.05)
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:air"
    )
    assert "PASS" in resp, (
        f"Run {run}: Chest not cleared: {resp}"
    )


# ===========================================================================
# 6. Entity summon + verify + kill (20x)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(20))
def test_entity_summon_20x(run, bridge, test_region):
    """
    Summon an armor stand, verify it exists, then kill it.
    (20 iterations)
    """
    ox, oy, oz = test_region

    # Summon armor stand
    bridge.run_command(
        f"summon minecraft:armor_stand {ox + 0.5} {oy + 0} {oz + 0.5}"
    )
    time.sleep(0.1)

    # Verify with Scarpet
    resp = bridge.run_command(
        f"mira_api check_entity {ox + 0} {oy + 0} {oz + 0} minecraft:armor_stand"
    )
    assert "PASS" in resp, (
        f"Run {run}: Armor stand not found: {resp}"
    )

    # Kill entity
    bridge.run_command(
        f"kill @e[x={ox},y={oy},z={oz},dx=2,dy=2,dz=2,type=minecraft:armor_stand]"
    )
    time.sleep(0.1)

    # Verify killed
    resp = bridge.run_command(
        f"mira_api check_entity {ox + 0} {oy + 0} {oz + 0} minecraft:armor_stand"
    )
    assert "FAIL" in resp or "FAIL" in resp.upper() or "not found" in resp.lower(), (
        f"Run {run}: Armor stand still present after kill: {resp}"
    )


# ===========================================================================
# 7. Large circuit (20 blocks) build + verify (10x)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(10))
def test_large_circuit_10x(run, bridge, test_region):
    """
    Build a 20-block circuit (5x4 stone grid) and verify all blocks 10 times.
    """
    ox, oy, oz = test_region
    blocks = [(x, 0, z, "minecraft:stone", None) for x in range(5) for z in range(4)]

    from simulation.replicator import replicate_blocks
    bounds = ((0, 0, 0), (5, 1, 4))
    replicate_blocks(blocks, test_region, bounds, bridge)

    # Verify all positions
    for x in range(5):
        for z in range(4):
            resp = bridge.run_command(
                f"mira_api check_block {ox + x} {oy + 0} {oz + z} minecraft:stone"
            )
            assert "PASS" in resp, (
                f"Run {run}: Missing stone at ({x},0,{z}): {resp}"
            )


# ===========================================================================
# 8. Directional block rotation cycle (10x)
# ===========================================================================

STAIRS_DIRECTIONS = [
    "minecraft:oak_stairs[facing=north]",
    "minecraft:oak_stairs[facing=east]",
    "minecraft:oak_stairs[facing=south]",
    "minecraft:oak_stairs[facing=west]",
]


@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(10))
def test_directional_rotation_10x(run, bridge, test_region, ctx):
    """
    Cycle a directional block (stair) through all 4 cardinal directions.
    Verify each direction is correctly placed.
    """
    ox, oy, oz = test_region
    for direction in STAIRS_DIRECTIONS:
        ctx.set_block([0, 0, 0], direction)
        ctx.tick(5)
        resp = bridge.run_command(
            f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} {direction}"
        )
        assert "PASS" in resp, (
            f"Run {run}: Direction {direction} failed: {resp}"
        )


# ===========================================================================
# 9. Redstone_block → lamp → check lit → clear (10x)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(10))
def test_redstone_block_lamp_10x(run, ctx, bridge, test_region):
    """
    Place redstone_block next to lamp, verify lamp lights,
    then clear both blocks.
    """
    # Place and verify lit
    ctx.set_block([0, 0, 0], "minecraft:redstone_block")
    ctx.set_block([1, 0, 0], "minecraft:redstone_lamp[lit=false]")
    ctx.tick(20)
    ctx.assert_block([1, 0, 0], "minecraft:redstone_lamp[lit=true]")

    # Clear both
    ctx.set_block([0, 0, 0], "minecraft:air")
    ctx.set_block([1, 0, 0], "minecraft:air")
    ctx.tick(5)

    ox, oy, oz = test_region
    resp = bridge.run_command(
        f"mira_api check_block {ox + 0} {oy + 0} {oz + 0} minecraft:air"
    )
    assert "PASS" in resp, (
        f"Run {run}: Block 0 not cleared: {resp}"
    )
    resp = bridge.run_command(
        f"mira_api check_block {ox + 1} {oy + 0} {oz + 0} minecraft:air"
    )
    assert "PASS" in resp, (
        f"Run {run}: Block 1 not cleared: {resp}"
    )


# ===========================================================================
# 10. Wire chain build + power check + clear (10x)
# ===========================================================================

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(10))
def test_wire_chain_10x(run, ctx, bridge, test_region):
    """
    Build a 4-block wire chain (redstone_block → wire → wire → wire),
    check power levels, then clear.
    """
    # Support blocks for wires
    ctx.set_block([1, -1, 0], "minecraft:stone")
    ctx.set_block([2, -1, 0], "minecraft:stone")
    ctx.set_block([3, -1, 0], "minecraft:stone")

    # Build chain
    ctx.set_block([0, 0, 0], "minecraft:redstone_block")
    ctx.set_block([1, 0, 0], "minecraft:redstone_wire")
    ctx.set_block([2, 0, 0], "minecraft:redstone_wire")
    ctx.set_block([3, 0, 0], "minecraft:redstone_wire")
    ctx.tick(20)

    # Check power levels
    ctx.assert_power([1, 0, 0], 15)
    ctx.assert_power([2, 0, 0], 14)
    ctx.assert_power([3, 0, 0], 13)

    # Clear
    ctx.set_block([0, 0, 0], "minecraft:air")
    ctx.set_block([1, 0, 0], "minecraft:air")
    ctx.set_block([2, 0, 0], "minecraft:air")
    ctx.set_block([3, 0, 0], "minecraft:air")
    ctx.tick(5)

    ox, oy, oz = test_region
    for x in range(4):
        resp = bridge.run_command(
            f"mira_api check_block {ox + x} {oy + 0} {oz + 0} minecraft:air"
        )
        assert "PASS" in resp, (
            f"Run {run}: Position {x},0,0 not cleared: {resp}"
        )
