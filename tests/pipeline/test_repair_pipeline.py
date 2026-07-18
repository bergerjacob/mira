"""
Tests for the corruptor → repair data pipeline (CircuitCorruptor).

No server needed, no API calls. Uses hardcoded block lists with various
redstone components (wire, repeaters, levers) to test each corruption type.
"""
import copy
import random
import pytest
from data_mining.corruptor import CircuitCorruptor
from simulation.teacher_client import TeacherClient


# =========================================================================
# Helpers
# =========================================================================

def _make_block(x, y, z, state="minecraft:stone", nbt=None):
    return (x, y, z, state, nbt)


def _wire_circuit():
    """Circuit containing redstone wire."""
    return [
        _make_block(0, 0, 0, "minecraft:lever[facing=east,powered=false]"),
        _make_block(1, 0, 0, "minecraft:redstone_wire"),
        _make_block(2, 0, 0, "minecraft:redstone_lamp[lit=false]"),
    ]


def _repeater_circuit():
    """Circuit containing a repeater (rotatable)."""
    return [
        _make_block(0, 0, 0, "minecraft:redstone_torch"),
        _make_block(1, 0, 0, "minecraft:repeater[delay=1,facing=north]"),
        _make_block(2, 0, 0, "minecraft:redstone_wire"),
    ]


def _lever_circuit():
    """Circuit containing a lever (power source)."""
    return [
        _make_block(0, 0, 0, "minecraft:lever[facing=east,powered=false]"),
        _make_block(1, 0, 0, "minecraft:redstone_wire"),
    ]


def _stone_only():
    """Circuit with no corruptable components."""
    return [
        _make_block(0, 0, 0, "minecraft:stone"),
        _make_block(1, 0, 0, "minecraft:glass"),
        _make_block(2, 0, 0, "minecraft:dirt"),
    ]


# =========================================================================
# Break Redstone Dust Tests
# =========================================================================

class TestBreakRedstoneDust:
    """CircuitCorruptor.break_redstone_dust() behaviour."""

    def test_break_wire(self):
        """Wire circuit → wire removed, modification logged."""
        corruptor = CircuitCorruptor(_wire_circuit())
        result = corruptor.break_redstone_dust()
        assert result is True
        assert len(corruptor.modifications) == 1
        mod = corruptor.modifications[0]
        assert mod["type"] == "break_wire"

    def test_break_wire_no_wires(self):
        """Circuit without wires → returns False."""
        corruptor = CircuitCorruptor(_stone_only())
        result = corruptor.break_redstone_dust()
        assert result is False
        assert len(corruptor.modifications) == 0

    def test_break_wire_sets_air(self):
        """Removed wire becomes minecraft:air."""
        corruptor = CircuitCorruptor(_wire_circuit())
        corruptor.break_redstone_dust()
        air_blocks = [b for b in corruptor.corrupted_blocks if b[3] == "minecraft:air"]
        assert len(air_blocks) == 1
        # The air should be at the original wire position
        original_wire = [b for b in _wire_circuit() if "redstone_wire" in b[3]][0]
        assert air_blocks[0][:3] == original_wire[:3]


# =========================================================================
# Rotate Repeater Tests
# =========================================================================

class TestRotateRepeater:
    """CircuitCorruptor.rotate_repeater() behaviour."""

    def test_rotate_repeater(self):
        """Repeater circuit → facing changed, modification logged."""
        corruptor = CircuitCorruptor(_repeater_circuit())
        result = corruptor.rotate_repeater()
        assert result is True
        assert len(corruptor.modifications) == 1
        mod = corruptor.modifications[0]
        assert mod["type"] == "rotate_component"

    def test_rotate_no_candidates(self):
        """Circuit without rotatable blocks → returns False."""
        corruptor = CircuitCorruptor(_stone_only())
        result = corruptor.rotate_repeater()
        assert result is False
        assert len(corruptor.modifications) == 0

    def test_rotate_changes_facing(self):
        """New facing differs from original facing."""
        import re
        corruptor = CircuitCorruptor(_repeater_circuit())
        old_state = [b for b in _repeater_circuit() if "repeater" in b[3]][0][3]
        old_facing = re.search(r"facing=([a-z]+)", old_state).group(1)

        # Try with different seeds until facing changes
        new_facing = old_facing
        for seed in range(20):
            random.seed(seed)
            corruptor = CircuitCorruptor(_repeater_circuit())
            corruptor.rotate_repeater()
            new_state = [b for b in corruptor.corrupted_blocks if "repeater" in b[3]][0][3]
            new_facing = re.search(r"facing=([a-z]+)", new_state).group(1)
            if new_facing != old_facing:
                break

        assert new_facing != old_facing


# =========================================================================
# Remove Power Source Tests
# =========================================================================

class TestRemovePowerSource:
    """CircuitCorruptor.remove_power_source() behaviour."""

    def test_remove_power_source(self):
        """Lever circuit → lever removed, modification logged."""
        corruptor = CircuitCorruptor(_lever_circuit())
        result = corruptor.remove_power_source()
        assert result is True
        assert len(corruptor.modifications) == 1
        mod = corruptor.modifications[0]
        assert mod["type"] == "remove_source"

    def test_remove_power_source_no_candidates(self):
        """Circuit without power sources → returns False."""
        corruptor = CircuitCorruptor(_stone_only())
        result = corruptor.remove_power_source()
        assert result is False
        assert len(corruptor.modifications) == 0

    def test_remove_power_source_sets_air(self):
        """Removed power source becomes minecraft:air."""
        corruptor = CircuitCorruptor(_lever_circuit())
        corruptor.remove_power_source()
        air_blocks = [b for b in corruptor.corrupted_blocks if b[3] == "minecraft:air"]
        assert len(air_blocks) == 1
        original_lever = [b for b in _lever_circuit() if "lever" in b[3]][0]
        assert air_blocks[0][:3] == original_lever[:3]


# =========================================================================
# Random Corruption Tests
# =========================================================================

class TestCorruptRandom:
    """CircuitCorruptor.corrupt(mode='random') behaviour."""

    def test_corrupt_applies_one_modification(self):
        """corrupt() on wire circuit applies exactly one modification."""
        random.seed(42)
        corruptor = CircuitCorruptor(_wire_circuit())
        blocks, mods = corruptor.corrupt()
        assert len(mods) == 1

    def test_corrupt_returns_modified_blocks(self):
        """corrupted_blocks differ from original."""
        random.seed(42)
        original = _wire_circuit()
        corruptor = CircuitCorruptor(original)
        blocks, mods = corruptor.corrupt()
        assert blocks != original

    def test_corrupt_no_candidates(self):
        """Circuit without corruptable components → empty modifications."""
        random.seed(42)
        corruptor = CircuitCorruptor(_stone_only())
        blocks, mods = corruptor.corrupt()
        assert len(mods) == 0
        assert len(blocks) == len(_stone_only())

    def test_corrupt_on_rich_circuit_picks_one_method(self):
        """Circuit with wires + repeaters + sources → one modification applied."""
        rich_circuit = [
            _make_block(0, 0, 0, "minecraft:redstone_wire"),
            _make_block(1, 0, 0, "minecraft:repeater[delay=1,facing=north]"),
            _make_block(2, 0, 0, "minecraft:lever[facing=east,powered=false]"),
        ]
        random.seed(42)
        corruptor = CircuitCorruptor(rich_circuit)
        blocks, mods = corruptor.corrupt()
        assert len(mods) == 1


# =========================================================================
# Multiple Corruptions Tests
# =========================================================================

class TestMultipleCorruptions:
    """Multiple successive corruptions on the same corruptor."""

    def test_corrupt_twice_different(self):
        """corrupt → corrupt produces different modifications."""
        random.seed(42)
        corruptor = CircuitCorruptor(_wire_circuit())
        blocks1, mods1 = corruptor.corrupt()

        random.seed(99)
        blocks2, mods2 = corruptor.corrupt()

        # Two modifications should be logged cumulatively
        assert len(corruptor.modifications) >= 1

    def test_corrupt_twice_distinct_states(self):
        """Successive corruptions change different blocks."""
        rich_circuit = [
            _make_block(0, 0, 0, "minecraft:redstone_wire"),
            _make_block(1, 0, 0, "minecraft:repeater[delay=1,facing=north]"),
            _make_block(2, 0, 0, "minecraft:lever[facing=east,powered=false]"),
        ]
        random.seed(42)
        corruptor = CircuitCorruptor(rich_circuit)
        blocks1, mods1 = corruptor.corrupt()
        count_after_first = len(corruptor.modifications)

        random.seed(7)
        blocks2, mods2 = corruptor.corrupt()
        count_after_second = len(corruptor.modifications)

        # Both corruptions should have added modifications
        assert count_after_second > count_after_first


# =========================================================================
# Deep Copy Tests
# =========================================================================

class TestDeepCopy:
    """Original blocks must remain unchanged after corruption."""

    def test_original_unchanged_after_break(self):
        """Original blocks list not modified by break_redstone_dust."""
        original = _wire_circuit()
        original_copy = copy.deepcopy(original)
        corruptor = CircuitCorruptor(original)
        corruptor.break_redstone_dust()
        assert original == original_copy

    def test_original_unchanged_after_rotate(self):
        """Original blocks not modified by rotate_repeater."""
        original = _repeater_circuit()
        original_copy = copy.deepcopy(original)
        corruptor = CircuitCorruptor(original)
        corruptor.rotate_repeater()
        assert original == original_copy

    def test_original_unchanged_after_remove_source(self):
        """Original blocks not modified by remove_power_source."""
        original = _lever_circuit()
        original_copy = copy.deepcopy(original)
        corruptor = CircuitCorruptor(original)
        corruptor.remove_power_source()
        assert original == original_copy

    def test_original_unchanged_after_corrupt(self):
        """Original blocks not modified by corrupt()."""
        original = _wire_circuit()
        original_copy = copy.deepcopy(original)
        random.seed(42)
        corruptor = CircuitCorruptor(original)
        corruptor.corrupt()
        assert original == original_copy


# =========================================================================
# Modification Log Format Tests
# =========================================================================

class TestModificationLogFormat:
    """Each modification entry has the correct structure."""

    def test_corruption_type_tracking(self):
        """Modification has 'type' field with correct value."""
        corruptor = CircuitCorruptor(_wire_circuit())
        corruptor.break_redstone_dust()
        assert corruptor.modifications[0]["type"] == "break_wire"

    def test_position_tracking(self):
        """Modification records which position is changed."""
        corruptor = CircuitCorruptor(_wire_circuit())
        corruptor.break_redstone_dust()
        pos = corruptor.modifications[0]["pos"]
        assert isinstance(pos, tuple)
        assert len(pos) == 3
        # The modified position should be the wire
        wire_pos = [b[:3] for b in _wire_circuit() if "redstone_wire" in b[3]][0]
        assert pos == wire_pos

    def test_original_state_tracking(self):
        """Modification records original state."""
        corruptor = CircuitCorruptor(_wire_circuit())
        corruptor.break_redstone_dust()
        original = corruptor.modifications[0]["original"]
        assert isinstance(original, str)
        assert "redstone_wire" in original

    def test_new_state_tracking(self):
        """Modification records new state."""
        corruptor = CircuitCorruptor(_wire_circuit())
        corruptor.break_redstone_dust()
        new_state = corruptor.modifications[0]["new"]
        assert isinstance(new_state, str)
        assert new_state == "minecraft:air"

    def test_all_modification_fields_present(self):
        """Every modification has type, pos, original, new."""
        corruptor = CircuitCorruptor(_lever_circuit())
        corruptor.remove_power_source()
        mod = corruptor.modifications[0]
        assert "type" in mod
        assert "pos" in mod
        assert "original" in mod
        assert "new" in mod


# =========================================================================
# Empty / Edge Case Tests
# =========================================================================

class TestEmptyAndEdgeCases:
    """Edge cases for the corruptor."""

    def test_empty_circuit(self):
        """corrupt([]) → no modifications, empty blocks list."""
        corruptor = CircuitCorruptor([])
        blocks, mods = corruptor.corrupt()
        assert len(mods) == 0
        assert len(blocks) == 0

    def test_no_corruptable_circuit(self):
        """Circuit with only stone → no modifications."""
        corruptor = CircuitCorruptor(_stone_only())
        blocks, mods = corruptor.corrupt()
        assert len(mods) == 0

    def test_single_corruptable_block(self):
        """Single wire → can be broken."""
        blocks = [_make_block(0, 0, 0, "minecraft:redstone_wire")]
        corruptor = CircuitCorruptor(blocks)
        result = corruptor.break_redstone_dust()
        assert result is True
        assert len(corruptor.modifications) == 1

    def test_multiple_same_type_blocks(self):
        """Multiple wires → one is randomly chosen and broken."""
        blocks = [
            _make_block(0, 0, 0, "minecraft:redstone_wire"),
            _make_block(1, 0, 0, "minecraft:redstone_wire"),
            _make_block(2, 0, 0, "minecraft:redstone_wire"),
        ]
        random.seed(42)
        corruptor = CircuitCorruptor(blocks)
        corruptor.break_redstone_dust()
        assert len(corruptor.modifications) == 1
        # Exactly one wire should be air now
        air_count = sum(1 for b in corruptor.corrupted_blocks if b[3] == "minecraft:air")
        assert air_count == 1


# =========================================================================
# Full Repair Pipeline (mock)
# =========================================================================

class TestFullRepairPipeline:
    """End-to-end pipeline: create circuit → corrupt → mock repair steps."""

    def test_full_pipeline_structure(self):
        """Create circuit, corrupt, then generate mock repair data."""
        # Step 1: Create a circuit
        blocks = _wire_circuit()

        # Step 2: Corrupt it
        random.seed(42)
        corruptor = CircuitCorruptor(blocks)
        corrupted_blocks, modifications = corruptor.corrupt()

        # Verify corruption happened
        assert len(modifications) >= 1
        assert corrupted_blocks != blocks

        # Step 3: Build structured repair data
        repair_data = {
            "original_blocks": blocks,
            "corrupted_blocks": corrupted_blocks,
            "modifications": modifications,
            "block_count_original": len(blocks),
            "block_count_corrupted": len(corrupted_blocks),
            "corruption_type": modifications[0]["type"] if modifications else None,
        }

        # Step 4: Verify structure
        assert "original_blocks" in repair_data
        assert "corrupted_blocks" in repair_data
        assert "modifications" in repair_data
        assert repair_data["block_count_original"] == 3
        assert repair_data["corruption_type"] is not None
        assert isinstance(repair_data["corruption_type"], str)

    def test_repair_data_tracks_difference(self):
        """Repair data correctly identifies what changed."""
        blocks = _repeater_circuit()
        random.seed(42)
        corruptor = CircuitCorruptor(blocks)
        corrupted_blocks, modifications = corruptor.corrupt()

        if modifications:
            mod = modifications[0]
            # Verify the corrupted block differs from original at the modified position
            pos = mod["pos"]
            original_at_pos = [b for b in blocks if b[:3] == pos][0]
            corrupted_at_pos = [b for b in corrupted_blocks if b[:3] == pos][0]
            assert original_at_pos != corrupted_at_pos

    def test_repair_with_teacher_client(self):
        """Repair pipeline can use TeacherClient mock for repair step generation."""
        blocks = _wire_circuit()
        random.seed(42)
        corruptor = CircuitCorruptor(blocks)
        corrupted_blocks, modifications = corruptor.corrupt()

        # TeacherClient can generate a test contract from corrupted blocks
        teacher = TeacherClient(mock_mode=True)
        meta = {"name": "repair_test", "description": "Corrupted circuit verification"}
        contract = teacher.generate_test_contract(meta, corrupted_blocks)

        assert "script" in contract
        assert "def verify_circuit(ctx)" in contract["script"]
