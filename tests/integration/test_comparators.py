"""
Integration tests: comparator placement and properties.
Tests only placement and block-state properties — NOT signal processing,
since comparators (like repeaters) cannot be powered via setblock.
"""
import time
import pytest

pytestmark = pytest.mark.server


def _enable_fill_updates(bridge):
    bridge.run_command("carpet fillUpdates true")


def _tick_and_settle(ctx, ticks=20):
    ctx.tick(ticks)
    time.sleep(0.5)


def _place_support(ctx, positions):
    """Place stone blocks below the given positions.

    Comparators need a solid block underneath or they break immediately
    when fillUpdates is true.
    """
    for x, y, z in positions:
        ctx.set_block((x, y - 1, z), "minecraft:stone")


# ---------------------------------------------------------------------------
# Comparator cardinal facing tests
# ---------------------------------------------------------------------------


def test_comparator_facing_east_compare(ctx, bridge):
    """Place comparator[facing=east,mode=compare], verify it exists."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east,mode=compare]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=east]")


def test_comparator_facing_north_subtract(ctx, bridge):
    """Place comparator[facing=north,mode=subtract], verify facing and mode."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=north,mode=subtract]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=north,mode=subtract]")


def test_comparator_facing_south_compare(ctx, bridge):
    """Place comparator[facing=south,mode=compare], verify."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=south,mode=compare]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=south]")


def test_comparator_facing_west_subtract(ctx, bridge):
    """Place comparator[facing=west,mode=subtract], verify."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=west,mode=subtract]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=west,mode=subtract]")


# ---------------------------------------------------------------------------
# Support block requirement
# ---------------------------------------------------------------------------


def test_comparator_needs_support(ctx, bridge):
    """Comparator without support block underneath breaks when fillUpdates=true.

    Place comparator directly on air (Y-1 is air), tick, and verify the
    comparator is gone (broken by block update).
    """
    _enable_fill_updates(bridge)
    # Intentionally do NOT place a support block underneath
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east,mode=compare]")
    _tick_and_settle(ctx)
    # The comparator should have broken — check it's air
    assert ctx.check_block((0, 0, 0), "minecraft:air"), (
        "Comparator without support should break, leaving air"
    )


# ---------------------------------------------------------------------------
# Comparator with redstone_block behind
# ---------------------------------------------------------------------------


def test_comparator_with_redstone_block_behind(ctx, bridge):
    """Place comparator with redstone_block behind it, verify both exist.

    NOTE: We do NOT test that the comparator becomes powered — that requires
    redstone updates which setblock doesn't trigger for comparators.
    """
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    # Place comparator first (facing east, so redstone_block is to the west)
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east]")
    ctx.set_block((-1, 0, 0), "minecraft:redstone_block")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=east]"), (
        "Comparator should still be present"
    )
    assert ctx.check_block((-1, 0, 0), "minecraft:redstone_block"), (
        "Redstone block should be present behind comparator"
    )


# ---------------------------------------------------------------------------
# Multiple comparators in a row
# ---------------------------------------------------------------------------


def test_multiple_comparators_in_row(ctx, bridge):
    """Place 3 comparators in a row (facing east), verify all placed correctly."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0), (1, 0, 0), (2, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east]")
    ctx.set_block((1, 0, 0), "minecraft:comparator[facing=east]")
    ctx.set_block((2, 0, 0), "minecraft:comparator[facing=east]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=east]"), (
        "First comparator missing"
    )
    assert ctx.check_block((1, 0, 0), "minecraft:comparator[facing=east]"), (
        "Second comparator missing"
    )
    assert ctx.check_block((2, 0, 0), "minecraft:comparator[facing=east]"), (
        "Third comparator missing"
    )


# ---------------------------------------------------------------------------
# Comparator mode change (overwrite)
# ---------------------------------------------------------------------------


def test_comparator_mode_change(ctx, bridge):
    """Place comparator in compare mode, overwrite with subtract mode -> mode changes."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east,mode=compare]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[mode=compare]"), (
        "Should be in compare mode initially"
    )

    # Overwrite with subtract mode
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east,mode=subtract]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[mode=subtract]"), (
        "Should be in subtract mode after overwrite"
    )


# ---------------------------------------------------------------------------
# Additional: facing change on overwrite
# ---------------------------------------------------------------------------


def test_comparator_facing_change(ctx, bridge):
    """Place comparator facing east, overwrite facing north -> facing changes."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east,mode=compare]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=east]")

    # Overwrite with north-facing
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=north,mode=compare]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=north]")


# ---------------------------------------------------------------------------
# Additional: comparator replaced with non-comparator
# ---------------------------------------------------------------------------


def test_comparator_replaced_with_stone(ctx, bridge):
    """Place comparator, overwrite with stone -> comparator gone."""
    _enable_fill_updates(bridge)
    _place_support(ctx, [(0, 0, 0)])
    ctx.set_block((0, 0, 0), "minecraft:comparator[facing=east,mode=compare]")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:comparator[facing=east]")

    # Replace with stone
    ctx.set_block((0, 0, 0), "minecraft:stone")
    _tick_and_settle(ctx)
    assert ctx.check_block((0, 0, 0), "minecraft:stone")
    # Verify comparator is no longer there
    state = ctx.get_state((0, 0, 0))
    assert "comparator" not in state, f"Comparator should be gone, state: {state}"
