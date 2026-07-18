"""
Stability tests — run the same operation many times to verify reliability.

All tests marked @pytest.mark.server and @pytest.mark.stability.
"""
import pytest
import time


# ---------------------------------------------------------------------------
# Single block placement stability
# ---------------------------------------------------------------------------

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(50))
def test_block_placement_50x(run, bridge, test_region):
    """Place and verify a single stone block 50 times in a row."""
    ox, oy, oz = test_region
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:stone")
    time.sleep(0.05)
    resp = bridge.run_command(f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:stone")
    assert "PASS" in resp, (
        f"Run {run}: Block not stone: {resp}"
    )


# ---------------------------------------------------------------------------
# Lamp toggle stability
# ---------------------------------------------------------------------------

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(20))
def test_lamp_toggle_20x(run, load_circuit, build_circuit, ctx, test_region):
    """
    Build lever+dust+lamp, toggle lever on/off 20 times,
    verify correct state after each toggle.
    """
    # Build once
    fixture = load_circuit("lever_lamp")
    build_circuit(fixture, test_region)

    # Initial should be off
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=false]")

    # Toggle on
    ctx.set_block([0, 0, 0], "minecraft:lever[facing=east,powered=true]")
    ctx.tick(20)
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=true]")

    # Toggle off
    ctx.set_block([0, 0, 0], "minecraft:lever[facing=east,powered=false]")
    ctx.tick(20)
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=false]")


# ---------------------------------------------------------------------------
# Circuit build stability
# ---------------------------------------------------------------------------

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(10))
def test_circuit_build_10x(run, load_circuit, build_and_verify, test_region):
    """
    Build the lever_lamp fixture 10 times at different origins,
    verify each works correctly.
    """
    fixture = load_circuit("lever_lamp")
    ctx = build_and_verify(fixture, test_region)

    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=false]")
    ctx.set_block([0, 0, 0], "minecraft:lever[facing=east,powered=true]")
    ctx.tick(20)
    ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=true]")


# ---------------------------------------------------------------------------
# Clear and rebuild stability
# ---------------------------------------------------------------------------

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(10))
def test_clear_and_rebuild_10x(run, bridge, test_region):
    """Fill area with stone, clear to air, verify cleared, repeat."""
    ox, oy, oz = test_region

    # Fill area with stone
    bridge.fill(ox + 0, oy + 0, oz + 0, ox + 5, oy + 0, oz + 5, "minecraft:stone")
    time.sleep(0.1)

    # Clear with air
    bridge.fill(ox + 0, oy + 0, oz + 0, ox + 5, oy + 0, oz + 5, "minecraft:air")
    time.sleep(0.1)

    # Spot-check a few positions
    for sx in [0, 2, 4]:
        for sz in [0, 2, 4]:
            resp = bridge.run_command(f"mira_api check_block {ox+sx} {oy+0} {oz+sz} minecraft:air")
            assert "PASS" in resp, (
                f"Run {run}: Position ({sx},0,{sz}) not cleared: {resp}"
            )


# ---------------------------------------------------------------------------
# Scarpet check stability
# ---------------------------------------------------------------------------

@pytest.mark.stability
@pytest.mark.server
@pytest.mark.parametrize("run", range(50))
def test_scarpet_check_50x(run, bridge, test_region):
    """Place a block, check_block 50 times, verify consistent PASS result."""
    ox, oy, oz = test_region
    bridge.set_block(ox + 0, oy + 0, oz + 0, "minecraft:stone")
    time.sleep(0.05)

    resp = bridge.run_command(
        f"mira_api check_block {ox+0} {oy+0} {oz+0} minecraft:stone"
    )
    assert "PASS" in resp, (
        f"Run {run}: check_block did not return PASS: {resp}"
    )
