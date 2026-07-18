"""
Unit tests for data_mining/corruptor.py CircuitCorruptor class.

Uses hardcoded block lists — no Minecraft server or API calls.
"""
import random
import copy
import pytest
from data_mining.corruptor import CircuitCorruptor


# ---------------------------------------------------------------------------
# Fixtures: reusable block lists
# ---------------------------------------------------------------------------

@pytest.fixture
def circuit_with_wire():
    """A small circuit containing redstone wire."""
    return [
        (0, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        (1, 0, 0, "minecraft:redstone_wire", None),
        (2, 0, 0, "minecraft:redstone_lamp[lit=false]", None),
    ]


@pytest.fixture
def circuit_with_repeater():
    """A circuit containing a repeater."""
    return [
        (0, 0, 0, "minecraft:redstone_torch", None),
        (1, 0, 0, "minecraft:repeater[delay=1,facing=north]", None),
        (2, 0, 0, "minecraft:redstone_wire", None),
    ]


@pytest.fixture
def circuit_with_power_source():
    """A circuit containing a lever (power source)."""
    return [
        (0, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        (1, 0, 0, "minecraft:redstone_wire", None),
        (2, 0, 0, "minecraft:redstone_lamp[lit=false]", None),
    ]


@pytest.fixture
def circuit_no_candidates():
    """A circuit with no wires, no rotatable blocks, no power sources."""
    return [
        (0, 0, 0, "minecraft:stone", None),
        (1, 0, 0, "minecraft:glass", None),
        (2, 0, 0, "minecraft:dirt", None),
    ]


# ---------------------------------------------------------------------------
# break_redstone_dust tests
# ---------------------------------------------------------------------------

class TestBreakRedstoneDust:
    def test_break_redstone_dust(self, circuit_with_wire):
        """Circuit with wire -> wire removed, modification logged."""
        corruptor = CircuitCorruptor(circuit_with_wire)
        result = corruptor.break_redstone_dust()
        assert result is True
        assert len(corruptor.modifications) == 1
        mod = corruptor.modifications[0]
        assert mod["type"] == "break_wire"

    def test_break_redstone_dust_no_wires(self, circuit_no_candidates):
        """Circuit without wires -> returns False, no modifications."""
        corruptor = CircuitCorruptor(circuit_no_candidates)
        result = corruptor.break_redstone_dust()
        assert result is False
        assert len(corruptor.modifications) == 0

    def test_break_redstone_dust_sets_air(self, circuit_with_wire):
        """Removed wire becomes minecraft:air."""
        corruptor = CircuitCorruptor(circuit_with_wire)
        corruptor.break_redstone_dust()
        # Exactly one block should now be air
        air_blocks = [b for b in corruptor.corrupted_blocks if b[3] == "minecraft:air"]
        assert len(air_blocks) == 1
        # And the original had exactly one wire
        original_wire_pos = None
        for b in circuit_with_wire:
            if "redstone_wire" in b[3]:
                original_wire_pos = (b[0], b[1], b[2])
                break
        assert original_wire_pos is not None
        # The air block is at the wire's position
        assert air_blocks[0][:3] == original_wire_pos


# ---------------------------------------------------------------------------
# rotate_repeater tests
# ---------------------------------------------------------------------------

class TestRotateRepeater:
    def test_rotate_repeater(self, circuit_with_repeater):
        """Circuit with repeater -> facing changed, logged."""
        seed = random.randint(0, 2**31)
        random.seed(seed)
        corruptor = CircuitCorruptor(circuit_with_repeater)
        result = corruptor.rotate_repeater()
        assert result is True
        assert len(corruptor.modifications) == 1
        mod = corruptor.modifications[0]
        assert mod["type"] == "rotate_component"

    def test_rotate_repeater_no_candidates(self, circuit_no_candidates):
        """Circuit without rotatable blocks -> returns False."""
        corruptor = CircuitCorruptor(circuit_no_candidates)
        result = corruptor.rotate_repeater()
        assert result is False
        assert len(corruptor.modifications) == 0

    def test_rotate_repeater_changes_facing(self, circuit_with_repeater):
        """New facing != old facing."""
        # Isolate the repeater index
        repeater_idx = None
        for i, b in enumerate(circuit_with_repeater):
            if "repeater" in b[3]:
                repeater_idx = i
                break

        corruptor = CircuitCorruptor(circuit_with_repeater)
        old_state = circuit_with_repeater[repeater_idx][3]
        # Extract old facing
        import re
        old_facing = re.search(r"facing=([a-z]+)", old_state).group(1)

        # Try rotating a few times to get a different facing (there are 3 other options)
        new_facing = old_facing
        attempts = 0
        while new_facing == old_facing and attempts < 10:
            random.seed(attempts)
            corruptor = CircuitCorruptor(circuit_with_repeater)
            corruptor.rotate_repeater()
            new_state = corruptor.corrupted_blocks[repeater_idx][3]
            new_facing = re.search(r"facing=([a-z]+)", new_state).group(1)
            attempts += 1

        assert new_facing != old_facing, "Failed to change facing after 10 attempts with different seeds"


# ---------------------------------------------------------------------------
# remove_power_source tests
# ---------------------------------------------------------------------------

class TestRemovePowerSource:
    def test_remove_power_source(self, circuit_with_power_source):
        """Circuit with lever -> lever removed, logged."""
        corruptor = CircuitCorruptor(circuit_with_power_source)
        result = corruptor.remove_power_source()
        assert result is True
        assert len(corruptor.modifications) == 1
        mod = corruptor.modifications[0]
        assert mod["type"] == "remove_source"

    def test_remove_power_source_no_candidates(self, circuit_no_candidates):
        """Circuit without power sources -> returns False."""
        corruptor = CircuitCorruptor(circuit_no_candidates)
        result = corruptor.remove_power_source()
        assert result is False
        assert len(corruptor.modifications) == 0

    def test_remove_power_source_sets_air(self, circuit_with_power_source):
        """Removed power source becomes minecraft:air."""
        corruptor = CircuitCorruptor(circuit_with_power_source)
        corruptor.remove_power_source()
        air_blocks = [b for b in corruptor.corrupted_blocks if b[3] == "minecraft:air"]
        assert len(air_blocks) == 1
        # The removed block should be the lever
        original_lever_pos = None
        for b in circuit_with_power_source:
            if "lever" in b[3]:
                original_lever_pos = (b[0], b[1], b[2])
                break
        assert original_lever_pos is not None
        assert air_blocks[0][:3] == original_lever_pos


# ---------------------------------------------------------------------------
# corrupt() random mode tests
# ---------------------------------------------------------------------------

class TestCorruptRandom:
    def test_corrupt_applies_one_modification(self, circuit_with_wire):
        """corrupt() applies exactly one modification."""
        random.seed(42)
        corruptor = CircuitCorruptor(circuit_with_wire)
        blocks, mods = corruptor.corrupt()
        assert len(mods) == 1

    def test_corrupt_returns_modified_blocks(self, circuit_with_wire):
        """corrupted_blocks is different from original."""
        random.seed(42)
        corruptor = CircuitCorruptor(circuit_with_wire)
        blocks, mods = corruptor.corrupt()
        assert blocks != circuit_with_wire

    def test_corrupt_returns_modifications(self, circuit_with_wire):
        """modifications list has entries."""
        random.seed(42)
        corruptor = CircuitCorruptor(circuit_with_wire)
        blocks, mods = corruptor.corrupt()
        assert len(mods) >= 1

    def test_corrupt_on_no_candidate_returns_empty_mods(self, circuit_no_candidates):
        """corrupt() on circuit without candidates returns empty mods but still returns blocks."""
        random.seed(42)
        corruptor = CircuitCorruptor(circuit_no_candidates)
        blocks, mods = corruptor.corrupt()
        assert len(mods) == 0
        assert len(blocks) == len(circuit_no_candidates)


# ---------------------------------------------------------------------------
# Deep copy test
# ---------------------------------------------------------------------------

class TestDeepCopy:
    def test_deep_copy(self, circuit_with_wire):
        """Original blocks list is NOT modified after corruption."""
        original_copy = copy.deepcopy(circuit_with_wire)
        corruptor = CircuitCorruptor(circuit_with_wire)
        corruptor.break_redstone_dust()
        # The original list passed to __init__ should be unchanged
        assert circuit_with_wire == original_copy


# ---------------------------------------------------------------------------
# Modification log format test
# ---------------------------------------------------------------------------

class TestModificationLogFormat:
    def test_modification_log_format(self):
        """Each modification has type, pos, original, new fields."""
        blocks = [
            (0, 0, 0, "minecraft:redstone_wire", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        corruptor.break_redstone_dust()
        mod = corruptor.modifications[0]
        assert "type" in mod
        assert "pos" in mod
        assert "original" in mod
        assert "new" in mod
        # Verify value types
        assert isinstance(mod["type"], str)
        assert isinstance(mod["pos"], tuple) and len(mod["pos"]) == 3
        assert isinstance(mod["original"], str)
        assert isinstance(mod["new"], str)
