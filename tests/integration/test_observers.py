"""
Integration tests: observer block placement and properties.
Tests placement of observers in all 6 directions and property verification.
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
# Observer cardinal facing tests
# ---------------------------------------------------------------------------


def test_observer_facing_east(ctx, bridge):
    """Place observer[facing=east], verify exists with correct facing."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=east]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=east]")


def test_observer_facing_north(ctx, bridge):
    """Place observer[facing=north], verify exists with correct facing."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=north]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=north]")


def test_observer_facing_south(ctx, bridge):
    """Place observer[facing=south], verify exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=south]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=south]")


def test_observer_facing_west(ctx, bridge):
    """Place observer[facing=west], verify exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=west]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=west]")


def test_observer_facing_up(ctx, bridge):
    """Place observer[facing=up], verify exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=up]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=up]")


def test_observer_facing_down(ctx, bridge):
    """Place observer[facing=down], verify exists."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=down]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=down]")


# ---------------------------------------------------------------------------
# Multiple observers facing different directions
# ---------------------------------------------------------------------------


def test_multiple_observers_different_facings(ctx, bridge):
    """Place 4 observers with different facings, verify all correct."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=east]")
    ctx.set_block((2, 0, 0), "minecraft:observer[facing=north]")
    ctx.set_block((4, 0, 0), "minecraft:observer[facing=south]")
    ctx.set_block((6, 0, 0), "minecraft:observer[facing=west]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=east]")
    assert ctx.check_block((2, 0, 0), "minecraft:observer[facing=north]")
    assert ctx.check_block((4, 0, 0), "minecraft:observer[facing=south]")
    assert ctx.check_block((6, 0, 0), "minecraft:observer[facing=west]")


# ---------------------------------------------------------------------------
# Observer facing change on overwrite
# ---------------------------------------------------------------------------


def test_observer_facing_change(ctx, bridge):
    """Place observer facing east, overwrite facing north -> facing changes."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=east]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=east]")

    # Overwrite with north-facing
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=north]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=north]")
    # Verify it's no longer east
    state = ctx.get_state((0, 0, 0))
    assert "east" not in state or "facing" not in state.split("east")[0].split(",")[-1], (
        f"Observer should no longer face east, state: {state}"
    )


# ---------------------------------------------------------------------------
# Observer powered state verification
# ---------------------------------------------------------------------------


def test_observer_powered_false(ctx, bridge):
    """Place observer[facing=east,powered=false], verify powered=false."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=east,powered=false]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=east,powered=false]")


# ---------------------------------------------------------------------------
# Observer replaced with different block
# ---------------------------------------------------------------------------


def test_observer_replaced_with_stone(ctx, bridge):
    """Place observer, overwrite with stone -> observer gone."""
    _enable_fill_updates(bridge)
    ctx.set_block((0, 0, 0), "minecraft:observer[facing=east]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:observer[facing=east]")

    ctx.set_block((0, 0, 0), "minecraft:stone")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone")
    state = ctx.get_state((0, 0, 0))
    assert "observer" not in state, f"Observer should be gone, state: {state}"
