"""
Unit tests for block state parsing utilities from tests/conftest.py.

Tests for parse_block_state, format_block_state, and states_match.
"""
import pytest
from tests.conftest import parse_block_state, format_block_state, states_match


# ---------------------------------------------------------------------------
# parse_block_state tests
# ---------------------------------------------------------------------------

class TestParseBlockState:
    def test_simple_id(self):
        """minecraft:stone -> ('minecraft:stone', {})"""
        bid, props = parse_block_state("minecraft:stone")
        assert bid == "minecraft:stone"
        assert props == {}

    def test_with_properties(self):
        """minecraft:repeater[delay=2,facing=west] -> parsed correctly"""
        bid, props = parse_block_state("minecraft:repeater[delay=2,facing=west]")
        assert bid == "minecraft:repeater"
        assert props == {"delay": "2", "facing": "west"}

    def test_multiple_properties(self):
        """Many properties are all parsed"""
        state = "minecraft:redstone_wire[power=15,east=side,north=none,south=up,west=side]"
        bid, props = parse_block_state(state)
        assert bid == "minecraft:redstone_wire"
        assert props == {"power": "15", "east": "side", "north": "none", "south": "up", "west": "side"}

    def test_no_namespace(self):
        """'stone' -> ('stone', {}) — no namespace required"""
        bid, props = parse_block_state("stone")
        assert bid == "stone"
        assert props == {}

    def test_empty_string(self):
        """Empty string yields empty id"""
        bid, props = parse_block_state("")
        assert bid == ""
        assert props == {}

    def test_property_value_with_underscores(self):
        """Property values with underscores are preserved"""
        bid, props = parse_block_state("minecraft:command_block[condition=false,facing=up,powered=false]")
        assert bid == "minecraft:command_block"
        assert props == {"condition": "false", "facing": "up", "powered": "false"}


# ---------------------------------------------------------------------------
# format_block_state tests
# ---------------------------------------------------------------------------

class TestFormatBlockState:
    def test_simple_id(self):
        """format_block_state('minecraft:stone') -> 'minecraft:stone'"""
        assert format_block_state("minecraft:stone") == "minecraft:stone"

    def test_with_properties(self):
        """Properties are sorted alphabetically"""
        result = format_block_state("minecraft:repeater", {"delay": "2", "facing": "west"})
        assert result == "minecraft:repeater[delay=2,facing=west]"

    def test_empty_dict_props(self):
        """Empty dict yields bare id"""
        assert format_block_state("minecraft:stone", {}) == "minecraft:stone"

    def test_none_props(self):
        """None props yields bare id"""
        assert format_block_state("minecraft:stone", None) == "minecraft:stone"

    def test_alphabetical_ordering(self):
        """Properties are always in alphabetical key order"""
        result = format_block_state("minecraft:observer", {"facing": "north", "powered": "true", "mode": "fast"})
        # Alphabetical: facing < mode < powered
        assert result == "minecraft:observer[facing=north,mode=fast,powered=true]"

    def test_single_property(self):
        """Single property formatted correctly"""
        result = format_block_state("minecraft:lever", {"powered": "true"})
        assert result == "minecraft:lever[powered=true]"


# ---------------------------------------------------------------------------
# states_match tests
# ---------------------------------------------------------------------------

class TestStatesMatch:
    def test_exact_match(self):
        """Identical state strings match"""
        assert states_match(
            "minecraft:repeater[delay=2,facing=west]",
            "minecraft:repeater[delay=2,facing=west]",
        )

    def test_partial_match(self):
        """Actual has more props than expected -> still matches on checked props"""
        assert states_match(
            "minecraft:repeater[delay=2,facing=west,locked=false]",
            "minecraft:repeater[delay=2]",
        )

    def test_id_mismatch(self):
        """Different block ids -> False"""
        assert not states_match("minecraft:stone", "minecraft:dirt")

    def test_property_value_mismatch(self):
        """Same property but different value -> False"""
        assert not states_match(
            "minecraft:repeater[delay=2,facing=west]",
            "minecraft:repeater[delay=4]",
        )

    def test_expected_has_prop_not_in_actual(self):
        """Expected property does not exist in actual -> False"""
        assert not states_match("minecraft:stone", "minecraft:stone[powered=false]")

    def test_no_props_in_expected(self):
        """Expected has no properties, actual has some -> True (undemanding match)"""
        assert states_match(
            "minecraft:repeater[delay=2,facing=west]",
            "minecraft:repeater",
        )

    def test_match_no_props_both(self):
        """Both have no properties -> True"""
        assert states_match("minecraft:stone", "minecraft:stone")

    def test_property_value_partial_match_among_many(self):
        """Only one property checked among many on the actual block"""
        assert states_match(
            "minecraft:furnace[facing=north,lit=false]",
            "minecraft:furnace[lit=false]",
        )
