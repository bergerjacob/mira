"""
Mock-based unit tests for simulation/replicator.py replicate_blocks.
Uses a MagicMock bridge — no real server connection.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from math import isclose

# TICK_INTERVAL used by replicator for rate limiting
TICK_INTERVAL = 0.05


@pytest.fixture
def mock_bridge():
    """Return a fresh MagicMock that stands in for MinecraftBridge."""
    return MagicMock()


def _default_blocks():
    """Convenience: a single stone block at relative (0,0,0)."""
    return [(0, 0, 0, "minecraft:stone", None)]


def _default_bounds():
    """Convenience: a 1x1x1 bounding box."""
    return ((0, 0, 0), (1, 1, 1))


# ================================================================= #
# Core tests
# ================================================================= #


class TestReplicateSingleBlock:
    """Tests focusing on a single block being placed."""

    def test_replicate_single_block(self, mock_bridge):
        """1 block -> set_block called once with correct absolute coords."""
        from simulation.replicator import replicate_blocks

        blocks = [(0, 0, 0, "minecraft:stone", None)]
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        # Verify the block was set at origin + relative coords
        mock_bridge.set_block.assert_called_with(
            10, 100, 10, "minecraft:stone", None
        )


class TestOriginOffset:
    """Blocks are translated by the origin vector."""

    def test_origin_offset(self, mock_bridge):
        """blocks at (5,3,2) with origin (10,100,10) → placed at (15,103,12)."""
        from simulation.replicator import replicate_blocks

        blocks = [(5, 3, 2, "minecraft:redstone_block", None)]
        origin = (10, 100, 10)
        bounds = ((5, 3, 2), (6, 4, 3))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        mock_bridge.set_block.assert_called_with(
            15, 103, 12, "minecraft:redstone_block", None
        )


class TestMultipleBlocks:
    """Multiple blocks are all placed."""

    def test_replicate_multiple_blocks(self, mock_bridge):
        """3 blocks -> all placed at correct positions."""
        from simulation.replicator import replicate_blocks

        blocks = [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:dirt", None),
            (2, 0, 0, "minecraft:grass_block", None),
        ]
        origin = (0, 100, 0)
        bounds = ((0, 0, 0), (3, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        mock_bridge.set_block.assert_any_call(0, 100, 0, "minecraft:stone", None)
        mock_bridge.set_block.assert_any_call(1, 100, 0, "minecraft:dirt", None)
        mock_bridge.set_block.assert_any_call(2, 100, 0, "minecraft:grass_block", None)
        assert mock_bridge.set_block.call_count == 3


class TestBlockSorting:
    """Blocks must be sorted by (y, x, z) before placement."""

    def test_block_sorting(self, mock_bridge):
        """Blocks sorted by (y, x, z) before placement."""
        from simulation.replicator import replicate_blocks

        # Out-of-order input
        blocks = [
            (2, 2, 0, "minecraft:glass", None),
            (0, 0, 0, "minecraft:stone", None),
            (1, 1, 1, "minecraft:redstone_wire", None),
        ]
        origin = (0, 100, 0)
        bounds = ((0, 0, 0), (3, 3, 2))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        calls = mock_bridge.set_block.call_args_list
        # After sorting by (y, x, z): stone (y=0), redstone_wire (y=1), glass (y=2)
        assert len(calls) >= 3
        assert calls[0] == call(0, 100, 0, "minecraft:stone", None)
        assert calls[1] == call(1, 101, 1, "minecraft:redstone_wire", None)
        assert calls[2] == call(2, 102, 0, "minecraft:glass", None)


# ================================================================= #
# Entity skipping
# ================================================================= #


class TestEntitySkipping:
    """Blocks with 'entity:' prefix are skipped."""

    def test_entity_skipping(self, mock_bridge):
        """entity: blocks are skipped (no set_block call for them)."""
        from simulation.replicator import replicate_blocks

        blocks = [
            (0, 0, 0, "entity:minecraft:armor_stand", None),
            (1, 0, 0, "minecraft:stone", None),
        ]
        origin = (0, 100, 0)
        bounds = ((0, 0, 0), (2, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        # Only the stone block should be placed
        mock_bridge.set_block.assert_called_once_with(
            1, 100, 0, "minecraft:stone", None
        )


# ================================================================= #
# Pre/post commands
# ================================================================= #


class TestSetupTeardown:
    """Commands sent before and after building."""

    def test_air_clearing(self, mock_bridge):
        """fill command sent to clear area before building."""
        from simulation.replicator import replicate_blocks

        blocks = _default_blocks()
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        # Expected: fill extends bounds by -1 on min side
        # min = 10+0-1=9, max = 10+1=11, etc.
        mock_bridge.run_command.assert_any_call(
            "fill 9 99 9 11 101 11 air"
        )

    def test_freeze_unfreeze(self, mock_bridge):
        """tick freeze sent at start, tick unfreeze at end."""
        from simulation.replicator import replicate_blocks

        blocks = _default_blocks()
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        mock_bridge.run_command.assert_any_call("tick freeze")
        mock_bridge.run_command.assert_any_call("tick unfreeze")

    def test_gamerule_feedback(self, mock_bridge):
        """sendCommandFeedback false at start, true at end."""
        from simulation.replicator import replicate_blocks

        blocks = _default_blocks()
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        mock_bridge.run_command.assert_any_call(
            "gamerule sendCommandFeedback false"
        )
        mock_bridge.run_command.assert_any_call(
            "gamerule sendCommandFeedback true"
        )


class TestFillUpdates:
    """Carpet fillUpdates gamerule."""

    def test_fill_updates_disabled(self, mock_bridge):
        """carpet fillUpdates false sent when use_updates=False."""
        from simulation.replicator import replicate_blocks

        blocks = _default_blocks()
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge, use_updates=False)

        mock_bridge.run_command.assert_any_call("carpet fillUpdates false")

    def test_fill_updates_enabled(self, mock_bridge):
        """carpet fillUpdates true sent when use_updates=True."""
        from simulation.replicator import replicate_blocks

        blocks = _default_blocks()
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge, use_updates=True)

        mock_bridge.run_command.assert_any_call("carpet fillUpdates true")


# ================================================================= #
# Region update
# ================================================================= #


class TestForceUpdateRegion:
    """force_update_region option."""

    def test_force_update_region(self, mock_bridge):
        """mira_api update_region called when force_update_region=True."""
        from simulation.replicator import replicate_blocks

        blocks = [(0, 0, 0, "minecraft:stone", None)]
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(
            blocks, origin, bounds, mock_bridge, force_update_region=True
        )

        # update_region uses original bounds without buffer:
        # u_min = (10+0, 100+0, 10+0) = (10,100,10)
        # u_max = (10+1-1, 100+1-1, 10+1-1) = (10,100,10)
        mock_bridge.run_command.assert_any_call(
            "mira_api update_region 10 100 10 10 100 10"
        )

    def test_no_force_update(self, mock_bridge):
        """mira_api update_region NOT called when force_update_region=False."""
        from simulation.replicator import replicate_blocks

        blocks = _default_blocks()
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(
            blocks, origin, bounds, mock_bridge, force_update_region=False
        )

        # Verify no update_region command was sent
        for call_args in mock_bridge.run_command.call_args_list:
            assert "update_region" not in call_args[0][0]


# ================================================================= #
# Rate limiting
# ================================================================= #


class TestRateLimiting:
    """Rate limiting — sleeps after rate_limit commands."""

    @patch("simulation.replicator.time")
    def test_rate_limiting(self, mock_time, mock_bridge):
        """Sleeps are called after rate_limit set_block commands."""
        from simulation.replicator import replicate_blocks

        # 5 blocks, rate_limit=2 → should sleep after command 2 and 4
        blocks = [
            (i, 0, 0, "minecraft:stone", None) for i in range(5)
        ]
        origin = (0, 100, 0)
        bounds = ((0, 0, 0), (5, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge, rate_limit=2)

        # Count calls to time.sleep with TICK_INTERVAL (0.05) — excludes
        # the initial 1.0s post-clear sleep and any retry sleeps.
        tick_sleeps = [
            c for c in mock_time.sleep.call_args_list
            if isclose(c[0][0], TICK_INTERVAL)
        ]
        # With 5 blocks and rate_limit=2: sleeps after 2nd and 4th commands
        assert len(tick_sleeps) >= 2, (
            f"Expected at least 2 rate-limit sleeps, got {len(tick_sleeps)}"
        )


# ================================================================= #
# NBT handling
# ================================================================= #


class TestNBTHandling:
    """NBT data in blocks."""

    def test_nbt_string_handling(self, mock_bridge):
        """NBT as a string is passed through to set_block."""
        from simulation.replicator import replicate_blocks

        blocks = [(0, 0, 0, "minecraft:chest", '{Items:[]}')]
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        mock_bridge.set_block.assert_called_with(
            10, 100, 10, "minecraft:chest", '{Items:[]}'
        )

    def test_container_items(self, mock_bridge):
        """Blocks with Items in dict NBT get separate data merge commands."""
        from simulation.replicator import replicate_blocks

        nbt_dict = {
            "Items": [
                {"id": "minecraft:stone", "count": 1, "Slot": 0}
            ]
        }
        blocks = [(0, 0, 0, "minecraft:chest", nbt_dict)]
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        # set_block called for the chest
        mock_bridge.set_block.assert_called_with(
            10, 100, 10, "minecraft:chest", "{}"
        )

        # data merge command sent for container items
        mock_bridge.run_command.assert_any_call(
            'data merge block 10 100 10 {Items:[{id:"minecraft:stone",count:1,Slot:0b}]}'
        )


# ================================================================= #
# Retry / error resilience
# ================================================================= #


class TestRetry:
    """Retry on network errors."""

    @patch("simulation.replicator.time")
    def test_retry_on_network_error(self, mock_time, mock_bridge):
        """Retries on 'Broken pipe' error, reconnect logic triggered."""
        from simulation.replicator import replicate_blocks

        # First call raises Broken pipe, second succeeds
        mock_bridge.set_block.side_effect = [
            Exception("Broken pipe"),
            "success",
        ]

        blocks = [(0, 0, 0, "minecraft:stone", None)]
        origin = (10, 100, 10)
        bounds = ((0, 0, 0), (1, 1, 1))

        replicate_blocks(blocks, origin, bounds, mock_bridge)

        # set_block was called twice (retry)
        assert mock_bridge.set_block.call_count == 2

        # disconnect and connect were called during retry
        mock_bridge.disconnect.assert_called()
        mock_bridge.connect.assert_called()
