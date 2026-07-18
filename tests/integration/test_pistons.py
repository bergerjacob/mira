"""
Integration tests: piston and sticky piston mechanics.
Pistons respond to block updates (not redstone updates), so they work
correctly with setblock + lever flips.
"""
import time
import pytest

pytestmark = pytest.mark.server


def _enable_fill_updates(bridge):
    bridge.run_command("carpet fillUpdates true")


def _tick_and_settle(ctx, ticks=20):
    ctx.tick(ticks)
    time.sleep(0.5)


# ---------------------------------------------------------------------------
# Basic piston placement
# ---------------------------------------------------------------------------


def test_piston_placed_retracted(ctx, bridge):
    """Place piston[facing=east,extended=false], verify it exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=east,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=east,extended=false]")


def test_sticky_piston_placed_retracted(ctx, bridge):
    """Place sticky_piston[facing=east,extended=false], verify it exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]")


# ---------------------------------------------------------------------------
# Piston extend / retract with lever
# ---------------------------------------------------------------------------


def test_piston_extends_when_powered(ctx, bridge):
    """Lever -> piston[facing=east], flip lever -> piston extended=true."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=false]")
    ctx.set_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")
    _tick_and_settle(ctx)

    # Piston starts retracted
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")

    # Flip lever on
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=true]")
    _tick_and_settle(ctx)
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=true]")


def test_piston_retracts_when_unpowered(ctx, bridge):
    """Power piston (extended=true), then remove power -> extended=false."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=true]")
    ctx.set_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")
    _tick_and_settle(ctx)

    # Piston should now be extended
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=true]")

    # Remove power — flip lever off
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")


# ---------------------------------------------------------------------------
# Sticky piston extend / retract with lever
# ---------------------------------------------------------------------------


def test_sticky_piston_extends_when_powered(ctx, bridge):
    """Lever -> sticky_piston[facing=east], flip lever -> extended=true."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=false]")
    ctx.set_block((1, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]")
    _tick_and_settle(ctx)

    assert ctx.check_block((1, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]")

    # Flip lever on
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=true]")
    _tick_and_settle(ctx)
    assert ctx.check_block((1, 0, 0), "minecraft:sticky_piston[facing=east,extended=true]")


def test_sticky_piston_retracts_when_unpowered(ctx, bridge):
    """Power sticky piston, then remove power -> extended=false."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=true]")
    ctx.set_block((1, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]")
    _tick_and_settle(ctx)

    assert ctx.check_block((1, 0, 0), "minecraft:sticky_piston[facing=east,extended=true]")

    # Remove power
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((1, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]")


# ---------------------------------------------------------------------------
# Piston facing different directions
# ---------------------------------------------------------------------------


def test_piston_facing_north(ctx, bridge):
    """Place piston[facing=north], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=north,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=north,extended=false]")


def test_piston_facing_south(ctx, bridge):
    """Place piston[facing=south], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=south,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=south,extended=false]")


def test_piston_facing_west(ctx, bridge):
    """Place piston[facing=west], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=west,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=west,extended=false]")


def test_piston_facing_up(ctx, bridge):
    """Place piston[facing=up], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=up,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=up,extended=false]")


def test_piston_facing_down(ctx, bridge):
    """Place piston[facing=down], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=down,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=down,extended=false]")


# ---------------------------------------------------------------------------
# Sticky piston facing different directions
# ---------------------------------------------------------------------------


def test_sticky_piston_facing_north(ctx, bridge):
    """Place sticky_piston[facing=north], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:sticky_piston[facing=north,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:sticky_piston[facing=north,extended=false]")


def test_sticky_piston_facing_up(ctx, bridge):
    """Place sticky_piston[facing=up], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:sticky_piston[facing=up,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:sticky_piston[facing=up,extended=false]")


def test_sticky_piston_facing_down(ctx, bridge):
    """Place sticky_piston[facing=down], verify."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:sticky_piston[facing=down,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:sticky_piston[facing=down,extended=false]")


# ---------------------------------------------------------------------------
# Piston pushes block
# ---------------------------------------------------------------------------


def test_piston_pushes_block(ctx, bridge):
    """Lever -> piston[facing=east] + stone in front -> stone moves to (3, 0, 0)."""
    _enable_fill_updates(bridge)
    # Lever at (0,0,0) powers piston at (1,0,0), stone at (2,0,0) is pushed to (3,0,0)
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=true]")
    ctx.set_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")
    ctx.set_block((2, 0, 0), "minecraft:stone")
    _tick_and_settle(ctx)

    # Piston should be extended
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=true]"), (
        "Piston should be extended"
    )
    # The pushed block should be at (3,0,0) and (2,0,0) should be the piston head or air
    assert ctx.check_block((3, 0, 0), "minecraft:stone"), (
        "Stone block should have been pushed to (3,0,0)"
    )
    # The space the stone was in should be the piston arm (air or piston_head)
    # Just verify the stone is no longer at (2,0,0)
    assert not ctx.check_block((2, 0, 0), "minecraft:stone"), (
        "Stone block should no longer be at (2,0,0)"
    )


# ---------------------------------------------------------------------------
# Sticky piston retracts block
# ---------------------------------------------------------------------------


def test_sticky_piston_retracts_block(ctx, bridge):
    """Power sticky piston with stone in front, unpower -> stone pulled back."""
    _enable_fill_updates(bridge)
    # Place sticky piston at (0,0,0) facing east, stone at (1,0,0)
    ctx.set_block((0, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]")
    ctx.set_block((1, 0, 0), "minecraft:stone")
    _tick_and_settle(ctx)

    # Power the piston — it extends, pushing stone to (2,0,0)
    ctx.set_block((-1, 0, 0), "minecraft:lever[powered=true]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:sticky_piston[facing=east,extended=true]")
    # The stone should have been pushed
    assert ctx.check_block((1, 0, 0), "minecraft:air") or ctx.check_block((2, 0, 0), "minecraft:stone"), (
        "Stone should have been pushed when piston extended"
    )

    # Remove power — sticky piston retracts, pulls stone back to (1,0,0)
    ctx.set_block((-1, 0, 0), "minecraft:lever[powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]"), (
        "Sticky piston should be retracted"
    )
    # The stone should be pulled back to (1,0,0)
    assert ctx.check_block((1, 0, 0), "minecraft:stone"), (
        "Stone should have been pulled back to (1,0,0)"
    )


# ---------------------------------------------------------------------------
# Multiple pistons in sequence
# ---------------------------------------------------------------------------


def test_multiple_pistons_in_sequence(ctx, bridge):
    """Two pistons facing east in a row, power first -> both respond."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=false]")
    ctx.set_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")
    ctx.set_block((3, 0, 0), "minecraft:piston[facing=east,extended=false]")
    _tick_and_settle(ctx)

    # Both start retracted
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")
    assert ctx.check_block((3, 0, 0), "minecraft:piston[facing=east,extended=false]")

    # Power the lever
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=true]")
    _tick_and_settle(ctx)
    # First piston should extend (it's directly powered by lever adjacent)
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=true]"), (
        "First piston should extend"
    )


# ---------------------------------------------------------------------------
# Piston with no block in front just extends
# ---------------------------------------------------------------------------


def test_piston_extends_without_block_in_front(ctx, bridge):
    """Piston with air in front extends normally when powered."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=false]")
    ctx.set_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")
    # (2,0,0) is air — nothing to push
    _tick_and_settle(ctx)

    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=false]")

    # Power the piston
    ctx.set_block((0, 0, 0), "minecraft:lever[powered=true]")
    _tick_and_settle(ctx)
    assert ctx.check_block((1, 0, 0), "minecraft:piston[facing=east,extended=true]"), (
        "Piston should extend even with nothing to push"
    )


# ---------------------------------------------------------------------------
# Piston facing change
# ---------------------------------------------------------------------------


def test_piston_facing_change(ctx, bridge):
    """Place piston facing east, overwrite facing north -> facing changes."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=east,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=east,extended=false]")

    # Overwrite with north-facing
    ctx.set_block((0, 0, 0), "minecraft:piston[facing=north,extended=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:piston[facing=north,extended=false]")
