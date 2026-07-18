"""
Unit tests for tests/helpers/circuit_builder.py DSL.

Tests all block functions, utility functions, and edge cases.
No Minecraft server or API calls required.
"""
import pytest
from tests.helpers.circuit_builder import (
    block, lever, lamp, wire, repeater, redstone_block,
    stone, piston, sticky_piston, observer, comparator,
    hopper, dropper, dispenser, button, pressure_plate,
    torch, chest, barrel, glass, dirt, air,
    build, translate, get_bounds, add_support, to_dict_list,
)


# ---------------------------------------------------------------------------
# Tuple format contract
# ---------------------------------------------------------------------------

class TestTupleFormat:
    """Every block function returns a 5-tuple (x, y, z, state_str, nbt)."""

    def test_block_returns_tuple(self):
        b = block(0, 1, 2, "minecraft:stone")
        assert isinstance(b, tuple)
        assert len(b) == 5

    def test_lever_returns_tuple(self):
        b = lever(3, 4, 5)
        assert isinstance(b, tuple)
        assert len(b) == 5
        assert b[0] == 3
        assert b[1] == 4
        assert b[2] == 5

    def test_lamp_returns_tuple(self):
        b = lamp(1, 2, 3, lit=True)
        assert len(b) == 5
        assert b[:3] == (1, 2, 3)

    def test_nbt_is_always_none(self):
        """All builder functions set NBT to None (NBT is added separately)."""
        for fn in [lever, lamp, wire, stone, glass, dirt, air,
                   redstone_block, torch, chest, barrel]:
            b = fn(0, 0, 0)
            assert b[4] is None, f"{fn.__name__} should have nbt=None"


# ---------------------------------------------------------------------------
# State string format
# ---------------------------------------------------------------------------

class TestBlockStateStrings:
    """Verify generated block state strings are correct."""

    def test_stone_no_properties(self):
        b = stone(0, 0, 0)
        assert b[3] == "minecraft:stone"

    def test_glass_no_properties(self):
        b = glass(0, 0, 0)
        assert b[3] == "minecraft:glass"

    def test_dirt_no_properties(self):
        b = dirt(0, 0, 0)
        assert b[3] == "minecraft:dirt"

    def test_air_no_properties(self):
        b = air(0, 0, 0)
        assert b[3] == "minecraft:air"

    def test_lever_with_properties(self):
        b = lever(0, 0, 0, facing="west", powered=False)
        assert b[3] == "minecraft:lever[facing=west,powered=false]"

    def test_lamp_with_properties(self):
        b = lamp(0, 0, 0, lit=True)
        assert b[3] == "minecraft:redstone_lamp[lit=true]"

    def test_wire_no_properties(self):
        b = wire(0, 0, 0)
        assert b[3] == "minecraft:redstone_wire"

    def test_repeater_with_properties(self):
        b = repeater(0, 0, 0, facing="north", delay=2, powered=False, locked=False)
        assert "minecraft:repeater" in b[3]
        assert "facing=north" in b[3]
        assert "delay=2" in b[3]

    def test_piston_properties(self):
        b = piston(0, 0, 0, facing="up", extended=False)
        assert b[3] == "minecraft:piston[extended=false,facing=up]"

    def test_sticky_piston_properties(self):
        b = sticky_piston(0, 0, 0, facing="down", extended=True)
        assert b[3] == "minecraft:sticky_piston[extended=true,facing=down]"

    def test_observer_properties(self):
        b = observer(0, 0, 0, facing="south", powered=True)
        assert b[3] == "minecraft:observer[facing=south,powered=true]"

    def test_comparator_properties(self):
        b = comparator(0, 0, 0, facing="west", mode="subtract", powered=False)
        assert b[3] == "minecraft:comparator[facing=west,mode=subtract,powered=false]"

    def test_hopper_properties(self):
        b = hopper(0, 0, 0, facing="north")
        assert b[3] == "minecraft:hopper[facing=north]"

    def test_dropper_properties(self):
        b = dropper(0, 0, 0, facing="south")
        assert b[3] == "minecraft:dropper[facing=south]"

    def test_dispenser_properties(self):
        b = dispenser(0, 0, 0, facing="up")
        assert b[3] == "minecraft:dispenser[facing=up]"

    def test_button_properties(self):
        b = button(0, 0, 0, facing="north", powered=True)
        assert b[3] == "minecraft:stone_button[facing=north,powered=true]"

    def test_pressure_plate_properties(self):
        b = pressure_plate(0, 0, 0, powered=True)
        assert b[3] == "minecraft:stone_pressure_plate[powered=true]"

    def test_torch_lit(self):
        b = torch(0, 0, 0, lit=True)
        assert b[3] == "minecraft:redstone_torch[lit=true]"

    def test_torch_unlit(self):
        b = torch(0, 0, 0, lit=False)
        assert b[3] == "minecraft:redstone_torch[lit=false]"

    def test_chest_properties(self):
        b = chest(0, 0, 0, facing="north")
        assert b[3] == "minecraft:chest[facing=north]"

    def test_barrel_properties(self):
        b = barrel(0, 0, 0, facing="up")
        assert b[3] == "minecraft:barrel[facing=up]"

    def test_redstone_block_no_properties(self):
        b = redstone_block(0, 0, 0)
        assert b[3] == "minecraft:redstone_block"

    def test_default_parameters(self):
        """Default parameters produce expected state strings."""
        assert lever(0, 0, 0)[3] == "minecraft:lever[facing=east,powered=false]"
        assert lamp(0, 0, 0)[3] == "minecraft:redstone_lamp[lit=false]"
        assert repeater(0, 0, 0)[3] == "minecraft:repeater[delay=1,facing=east,locked=false,powered=false]"
        assert piston(0, 0, 0)[3] == "minecraft:piston[extended=false,facing=east]"
        assert torch(0, 0, 0)[3] == "minecraft:redstone_torch[lit=true]"
        assert comparator(0, 0, 0)[3] == "minecraft:comparator[facing=east,mode=compare,powered=false]"


# ---------------------------------------------------------------------------
# Boolean property formatting
# ---------------------------------------------------------------------------

class TestBooleanProperties:
    """Boolean properties must be lowercase strings."""

    def test_powered_false(self):
        b = lever(0, 0, 0, powered=False)
        assert "powered=false" in b[3]

    def test_powered_true(self):
        b = lever(0, 0, 0, powered=True)
        assert "powered=true" in b[3]

    def test_lit_true(self):
        b = lamp(0, 0, 0, lit=True)
        assert "lit=true" in b[3]

    def test_lit_false(self):
        b = lamp(0, 0, 0, lit=False)
        assert "lit=false" in b[3]

    def test_extended_true(self):
        b = piston(0, 0, 0, extended=True)
        assert "extended=true" in b[3]

    def test_mixed_booleans(self):
        b = repeater(0, 0, 0, powered=True, locked=False)
        assert "powered=true" in b[3]
        assert "locked=false" in b[3]


# ---------------------------------------------------------------------------
# build() — combining blocks
# ---------------------------------------------------------------------------

class TestBuild:
    def test_build_empty(self):
        assert build() == []

    def test_build_single(self):
        b = build(stone(0, 0, 0))
        assert len(b) == 1
        assert b[0][3] == "minecraft:stone"

    def test_build_multiple(self):
        b = build(stone(0, 0, 0), glass(1, 0, 0), dirt(2, 0, 0))
        assert len(b) == 3

    def test_build_with_varied_types(self):
        b = build(
            lever(0, 0, 0),
            wire(1, 0, 0),
            lamp(2, 0, 0, lit=False),
        )
        assert len(b) == 3
        assert b[0][3].startswith("minecraft:lever")
        assert b[1][3] == "minecraft:redstone_wire"
        assert b[2][3].startswith("minecraft:redstone_lamp")


# ---------------------------------------------------------------------------
# translate()
# ---------------------------------------------------------------------------

class TestTranslate:
    def test_translate_zero(self):
        blocks = build(stone(0, 0, 0), glass(1, 1, 1))
        t = translate(blocks, 0, 0, 0)
        assert t == blocks

    def test_translate_positive(self):
        blocks = build(stone(0, 0, 0), glass(1, 1, 1))
        t = translate(blocks, 10, 20, 30)
        assert t[0][:3] == (10, 20, 30)
        assert t[1][:3] == (11, 21, 31)
        assert t[0][3] == "minecraft:stone"  # state preserved

    def test_translate_negative(self):
        blocks = build(stone(5, 5, 5))
        t = translate(blocks, -5, -5, -5)
        assert t[0][:3] == (0, 0, 0)

    def test_translate_partial(self):
        blocks = build(stone(0, 0, 0))
        t = translate(blocks, dx=1, dz=2)
        assert t[0][:3] == (1, 0, 2)

    def test_translate_preserves_nbt(self):
        blocks = build((0, 0, 0, "minecraft:chest", '{Items:[]}'))
        t = translate(blocks, 5, 5, 5)
        assert t[0][4] == '{Items:[]}'


# ---------------------------------------------------------------------------
# get_bounds()
# ---------------------------------------------------------------------------

class TestGetBounds:
    def test_single_block(self):
        blocks = build(stone(3, 4, 5))
        assert get_bounds(blocks) == ((3, 4, 5), (3, 4, 5))

    def test_two_blocks(self):
        blocks = build(stone(0, 0, 0), glass(5, 3, 2))
        assert get_bounds(blocks) == ((0, 0, 0), (5, 3, 2))

    def test_negative_coordinates(self):
        blocks = build(stone(-3, -2, -1), glass(2, 1, 0))
        assert get_bounds(blocks) == ((-3, -2, -1), (2, 1, 0))

    def test_empty_blocks_raises(self):
        with pytest.raises(ValueError, match="empty"):
            get_bounds([])


# ---------------------------------------------------------------------------
# add_support()
# ---------------------------------------------------------------------------

class TestAddSupport:
    def test_single_block_adds_support(self):
        blocks = build(stone(0, 0, 0))
        supported = add_support(blocks, dy=-1)
        assert len(supported) == 2
        assert (0, -1, 0, "minecraft:stone", None) in supported

    def test_no_duplicate_support(self):
        """If support already exists, don't add another at same position."""
        blocks = build(stone(0, 0, 0), stone(0, -1, 0))
        supported = add_support(blocks, dy=-1)
        # The bottom stone (0,-1,0) also needs support at (0,-2,0)
        # because it's a non-air block without anything below it
        assert len(supported) == 3

    def test_air_blocks_get_no_support(self):
        blocks = build(air(0, 0, 0))
        supported = add_support(blocks, dy=-1)
        assert len(supported) == 1  # only the air block, no support

    def test_multiple_blocks_with_support(self):
        blocks = build(stone(0, 0, 0), glass(2, 0, 0))
        supported = add_support(blocks, dy=-1)
        assert len(supported) == 4
        assert (0, -1, 0, "minecraft:stone", None) in supported
        assert (2, -1, 0, "minecraft:stone", None) in supported

    def test_mixed_air_and_blocks(self):
        """Only non-air blocks get support."""
        blocks = build(stone(0, 0, 0), air(1, 0, 0), glass(2, 0, 0))
        supported = add_support(blocks, dy=-1)
        assert len(supported) == 5  # 3 original + 2 supports

    def test_custom_dy(self):
        blocks = build(stone(0, 0, 0))
        supported = add_support(blocks, dy=-2)
        assert len(supported) == 2
        assert (0, -2, 0, "minecraft:stone", None) in supported

    def test_shared_support(self):
        """Two blocks adjacent don't double-add support at same position."""
        blocks = build(stone(0, 0, 0), stone(1, 0, 0))
        supported = add_support(blocks, dy=-1)
        assert len(supported) == 4  # 2 original + 2 distinct supports
        # The two supports should be at different positions
        supports = [(b[0], b[1], b[2]) for b in supported if b[1] == -1]
        assert len(supports) == 2
        assert (0, -1, 0) in supports
        assert (1, -1, 0) in supports


# ---------------------------------------------------------------------------
# to_dict_list()
# ---------------------------------------------------------------------------

class TestToDictList:
    def test_empty_list(self):
        assert to_dict_list([]) == []

    def test_single_block(self):
        blocks = build(stone(0, 1, 2))
        result = to_dict_list(blocks)
        assert len(result) == 1
        assert result[0] == {"x": 0, "y": 1, "z": 2, "state": "minecraft:stone"}

    def test_multiple_blocks(self):
        blocks = build(stone(0, 0, 0), lever(1, 0, 0, powered=True))
        result = to_dict_list(blocks)
        assert len(result) == 2
        assert result[0]["x"] == 0
        assert result[1]["x"] == 1
        assert result[1]["state"] == "minecraft:lever[facing=east,powered=true]"

    def test_with_nbt(self):
        blocks = build((0, 0, 0, "minecraft:chest", '{"Items":[]}'))
        result = to_dict_list(blocks)
        assert result[0]["nbt"] == '{"Items":[]}'

    def test_json_serializable(self):
        """to_dict_list output should be JSON-serializable."""
        import json
        blocks = build(stone(0, 0, 0), lever(1, 0, 0, powered=True))
        result = to_dict_list(blocks)
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert len(parsed) == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_build(self):
        """build() with no args returns empty list."""
        assert build() == []

    def test_single_block_get_bounds(self):
        """get_bounds with single block returns (pos, pos)."""
        b = build(stone(5, 5, 5))
        assert get_bounds(b) == ((5, 5, 5), (5, 5, 5))

    def test_blocks_at_same_position(self):
        """Duplicate positions are allowed (last write wins)."""
        blocks = build(
            stone(0, 0, 0),
            glass(0, 0, 0),  # same position, different block
        )
        assert len(blocks) == 2
        assert blocks[0][3] == "minecraft:stone"
        assert blocks[1][3] == "minecraft:glass"

    def test_translate_preserves_state_and_nbt(self):
        """translate preserves state string and NBT."""
        blocks = build(lamp(0, 0, 0, lit=True))
        t = translate(blocks, 10, 0, 0)
        assert t[0][3] == "minecraft:redstone_lamp[lit=true]"
        assert t[0][4] is None

    def test_add_support_empty_blocks(self):
        """add_support with empty list returns empty list."""
        assert add_support([]) == []

    def test_add_support_no_effect_for_already_supported(self):
        """Blocks that already have support don't get extra."""
        blocks = build(stone(0, 0, 0), stone(0, -1, 0))
        result = add_support(blocks, dy=-1)
        # Original block (0,0,0) is supported by (0,-1,0) — no extra.
        # But (0,-1,0) itself gets support at (0,-2,0).
        assert len(result) == 3

    def test_high_coordinates(self):
        """Blocks at high coordinates work fine."""
        b = stone(1000000, 0, 0)
        assert b[0] == 1000000

    def test_negative_coordinates(self):
        """Blocks at negative coordinates."""
        b = lever(-5, -10, -15, powered=True)
        assert b[:3] == (-5, -10, -15)
        assert "powered=true" in b[3]

    def test_default_wire(self):
        """wire has no properties."""
        b = wire(0, 0, 0)
        assert "[" not in b[3]

    def test_default_redstone_block(self):
        """redstone_block has no properties."""
        b = redstone_block(0, 0, 0)
        assert "[" not in b[3]

    def test_build_chain(self):
        """Building multiple chains of blocks."""
        b1 = build(stone(0, 0, 0), wire(1, 0, 0))
        b2 = build(lamp(2, 0, 0))
        combined = b1 + b2
        assert len(combined) == 3


# ---------------------------------------------------------------------------
# to_dict_list — edge cases
# ---------------------------------------------------------------------------

class TestToDictListEdgeCases:
    def test_to_dict_list_no_nbt_key_when_none(self):
        """When nbt is None, no 'nbt' key in dict."""
        blocks = build(stone(0, 0, 0))
        result = to_dict_list(blocks)
        assert "nbt" not in result[0]

    def test_to_dict_list_with_nbt_key(self):
        """When nbt is not None, 'nbt' key is present."""
        blocks = build((0, 0, 0, "minecraft:chest", '{"Items":[]}'))
        result = to_dict_list(blocks)
        assert "nbt" in result[0]
