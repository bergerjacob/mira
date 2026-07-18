"""
Unit tests for data_mining/corruptor.py — edge cases.

Tests empty blocks, circuits without specific components,
sequential corruptions, deep copy behavior, and modification logs.
No Minecraft server or API calls required.
"""
import copy
import random
import pytest
from data_mining.corruptor import CircuitCorruptor


# ---------------------------------------------------------------------------
# Empty block list
# ---------------------------------------------------------------------------

class TestEmptyBlocks:
    def test_empty_blocks_corrupt_returns_empty_mods(self):
        """Empty blocks list -> corrupt() returns original blocks with empty modifications."""
        corruptor = CircuitCorruptor([])
        blocks, mods = corruptor.corrupt()
        assert blocks == []
        assert mods == []

    def test_empty_blocks_break_dust_fails(self):
        """Empty blocks -> break_redstone_dust returns False."""
        corruptor = CircuitCorruptor([])
        assert corruptor.break_redstone_dust() is False

    def test_empty_blocks_rotate_fails(self):
        """Empty blocks -> rotate_repeater returns False."""
        corruptor = CircuitCorruptor([])
        assert corruptor.rotate_repeater() is False

    def test_empty_blocks_remove_source_fails(self):
        """Empty blocks -> remove_power_source returns False."""
        corruptor = CircuitCorruptor([])
        assert corruptor.remove_power_source() is False


# ---------------------------------------------------------------------------
# No redstone wire
# ---------------------------------------------------------------------------

class TestNoRedstoneWire:
    def test_no_wire_break_dust_fails(self):
        """Circuit without redstone wire -> break_redstone_dust returns False."""
        blocks = [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.break_redstone_dust() is False
        assert len(corruptor.modifications) == 0

    def test_only_redstone_wire_break_succeeds(self):
        """Circuit with only redstone wire -> break succeeds."""
        blocks = [
            (0, 0, 0, "minecraft:redstone_wire", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.break_redstone_dust() is True
        assert len(corruptor.modifications) == 1


# ---------------------------------------------------------------------------
# No rotatable blocks
# ---------------------------------------------------------------------------

class TestNoRotatableBlocks:
    def test_no_rotatable_rotate_fails(self):
        """Circuit without rotatable blocks -> rotate_repeater returns False."""
        blocks = [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:redstone_wire", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.rotate_repeater() is False
        assert len(corruptor.modifications) == 0

    def test_only_repeater_rotate_succeeds(self):
        """Circuit with only a repeater -> rotate succeeds."""
        blocks = [
            (0, 0, 0, "minecraft:repeater[delay=1,facing=north]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.rotate_repeater() is True
        assert len(corruptor.modifications) == 1


# ---------------------------------------------------------------------------
# No power source
# ---------------------------------------------------------------------------

class TestNoPowerSource:
    def test_no_power_source_remove_fails(self):
        """Circuit without power source -> remove_power_source returns False."""
        blocks = [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:redstone_wire", None),
            (2, 0, 0, "minecraft:redstone_lamp[lit=false]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.remove_power_source() is False
        assert len(corruptor.modifications) == 0

    def test_only_lever_remove_succeeds(self):
        """Circuit with only a lever -> remove succeeds."""
        blocks = [
            (0, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.remove_power_source() is True
        assert len(corruptor.modifications) == 1


# ---------------------------------------------------------------------------
# Only specific component types
# ---------------------------------------------------------------------------

class TestOnlyComponentTypes:
    def test_only_torch_remove_succeeds(self):
        """Circuit with only redstone_torch -> remove power source succeeds."""
        blocks = [
            (0, 0, 0, "minecraft:redstone_torch[lit=true]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.remove_power_source() is True

    def test_only_redstone_block_remove_succeeds(self):
        """Circuit with only redstone_block -> remove power source succeeds."""
        blocks = [
            (0, 0, 0, "minecraft:redstone_block", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.remove_power_source() is True

    def test_only_observer_rotate_succeeds(self):
        """Circuit with only observer -> rotate succeeds."""
        blocks = [
            (0, 0, 0, "minecraft:observer[facing=east,powered=false]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        assert corruptor.rotate_repeater() is True


# ---------------------------------------------------------------------------
# Sequential corruptions
# ---------------------------------------------------------------------------

class TestSequentialCorruptions:
    def test_corrupt_twice(self):
        """Corrupt twice -> two modifications accumulated."""
        blocks = [
            (0, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
            (1, 0, 0, "minecraft:redstone_wire", None),
            (2, 0, 0, "minecraft:repeater[delay=1,facing=north]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        random.seed(0)
        corruptor.corrupt()
        assert len(corruptor.modifications) == 1
        random.seed(1)
        corruptor.corrupt()
        assert len(corruptor.modifications) == 2  # modifications accumulate
        assert corruptor.modifications[0]["type"] in ("break_wire", "rotate_component", "remove_source")
        assert corruptor.modifications[1]["type"] in ("break_wire", "rotate_component", "remove_source")

    def test_corrupt_thrice(self):
        """Corrupt three times -> three modifications accumulated."""
        blocks = [
            (0, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
            (1, 0, 0, "minecraft:redstone_wire", None),
            (2, 0, 0, "minecraft:repeater[delay=1,facing=north]", None),
            (3, 0, 0, "minecraft:observer[facing=east,powered=false]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        random.seed(10)
        corruptor.corrupt()
        random.seed(20)
        corruptor.corrupt()
        random.seed(30)
        _, mods = corruptor.corrupt()
        assert len(mods) == 3


# ---------------------------------------------------------------------------
# Deep copy verification
# ---------------------------------------------------------------------------

class TestDeepCopy:
    def test_original_unchanged_after_break(self):
        """Original block list is NOT modified after break_redstone_dust."""
        original = [
            (0, 0, 0, "minecraft:redstone_wire", None),
        ]
        original_copy = copy.deepcopy(original)
        corruptor = CircuitCorruptor(original)
        corruptor.break_redstone_dust()
        assert original == original_copy

    def test_original_unchanged_after_rotate(self):
        """Original block list is NOT modified after rotate_repeater."""
        original = [
            (0, 0, 0, "minecraft:repeater[delay=1,facing=north]", None),
        ]
        original_copy = copy.deepcopy(original)
        corruptor = CircuitCorruptor(original)
        corruptor.rotate_repeater()
        assert original == original_copy

    def test_original_unchanged_after_remove_source(self):
        """Original block list is NOT modified after remove_power_source."""
        original = [
            (0, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        ]
        original_copy = copy.deepcopy(original)
        corruptor = CircuitCorruptor(original)
        corruptor.remove_power_source()
        assert original == original_copy

    def test_original_unchanged_after_corrupt(self):
        """Original block list is NOT modified after corrupt()."""
        original = [
            (0, 0, 0, "minecraft:redstone_wire", None),
        ]
        original_copy = copy.deepcopy(original)
        corruptor = CircuitCorruptor(original)
        random.seed(42)
        corruptor.corrupt()
        assert original == original_copy


# ---------------------------------------------------------------------------
# Modification log format
# ---------------------------------------------------------------------------

class TestModificationLogFormat:
    def test_mod_log_has_type_pos_original_new(self):
        """Modification entry has type, pos, original, new fields."""
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

    def test_mod_log_types_are_correct(self):
        """Modification type strings match the corruption method."""
        # break_wire
        c1 = CircuitCorruptor([(0, 0, 0, "minecraft:redstone_wire", None)])
        c1.break_redstone_dust()
        assert c1.modifications[0]["type"] == "break_wire"

        # rotate_component
        c2 = CircuitCorruptor([(0, 0, 0, "minecraft:repeater[delay=1,facing=north]", None)])
        c2.rotate_repeater()
        assert c2.modifications[0]["type"] == "rotate_component"

        # remove_source
        c3 = CircuitCorruptor([(0, 0, 0, "minecraft:lever[facing=east,powered=false]", None)])
        c3.remove_power_source()
        assert c3.modifications[0]["type"] == "remove_source"

    def test_mod_log_pos_is_3_tuple(self):
        """Modification position is a 3-tuple of ints."""
        blocks = [(1, 2, 3, "minecraft:redstone_wire", None)]
        corruptor = CircuitCorruptor(blocks)
        corruptor.break_redstone_dust()
        pos = corruptor.modifications[0]["pos"]
        assert isinstance(pos, tuple)
        assert len(pos) == 3
        assert all(isinstance(c, int) for c in pos)
        assert pos == (1, 2, 3)

    def test_mod_log_pos_after_translate(self):
        """Modification position reflects original position."""
        blocks = [(5, 10, 15, "minecraft:redstone_wire", None)]
        corruptor = CircuitCorruptor(blocks)
        corruptor.break_redstone_dust()
        assert corruptor.modifications[0]["pos"] == (5, 10, 15)


# ---------------------------------------------------------------------------
# corrupt(mode='random') tries all methods
# ---------------------------------------------------------------------------

class TestCorruptRandomMode:
    def test_corrupt_random_tries_all_methods(self):
        """corrupt() with mode='random' tries break/rotate/remove in random order."""
        blocks = [
            (0, 0, 0, "minecraft:redstone_wire", None),
            (1, 0, 0, "minecraft:repeater[delay=1,facing=north]", None),
            (2, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        random.seed(42)
        _, mods = corruptor.corrupt()
        assert len(mods) == 1
        assert mods[0]["type"] in ("break_wire", "rotate_component", "remove_source")

    def test_corrupt_all_modes_possible(self):
        """With all component types present, any corruption type can occur."""
        break_seen = False
        rotate_seen = False
        remove_seen = False

        blocks = [
            (0, 0, 0, "minecraft:redstone_wire", None),
            (1, 0, 0, "minecraft:repeater[delay=1,facing=north]", None),
            (2, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        ]

        for seed in range(50):
            corruptor = CircuitCorruptor(blocks)
            random.seed(seed)
            _, mods = corruptor.corrupt()
            if mods:
                t = mods[0]["type"]
                if t == "break_wire":
                    break_seen = True
                elif t == "rotate_component":
                    rotate_seen = True
                elif t == "remove_source":
                    remove_seen = True
            if break_seen and rotate_seen and remove_seen:
                break

        assert break_seen, "break_wire was never selected"
        assert rotate_seen, "rotate_component was never selected"
        assert remove_seen, "remove_source was never selected"

    def test_corrupt_returns_modified_list(self):
        """corrupt() returns the modified block list (not original)."""
        blocks = [
            (0, 0, 0, "minecraft:redstone_wire", None),
        ]
        corruptor = CircuitCorruptor(blocks)
        random.seed(42)
        corrupted_blocks, _ = corruptor.corrupt()
        assert corrupted_blocks[0][3] == "minecraft:air"
