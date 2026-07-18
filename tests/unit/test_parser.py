"""
Unit tests for data_mining/parser.py SchematicParser class.

Uses the make_litematic fixture to create test schematics programmatically.
No Minecraft server or API calls required.
"""
import pytest
from data_mining.parser import SchematicParser


class TestSchematicParserFileNotFound:
    def test_file_not_found(self):
        """Raises FileNotFoundError for a non-existent file."""
        with pytest.raises(FileNotFoundError):
            SchematicParser("/nonexistent/path/to/schematic.litematic")


class TestSchematicParserBasic:
    def test_parse_basic(self, make_litematic):
        """3 blocks (stone, glass, dirt) -> correct count and state strings."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:glass"),
            (2, 0, 0, "minecraft:dirt"),
        ]
        path = make_litematic(blocks, name="basic_three")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 3
        # Verify each block state string
        state_strings = {b[3] for b in result}
        assert "minecraft:stone" in state_strings
        assert "minecraft:glass" in state_strings
        assert "minecraft:dirt" in state_strings

    def test_parse_single_block(self, make_litematic):
        """One block -> list of 1."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="single")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 1
        assert result[0][3] == "minecraft:stone"


class TestSchematicParserWithProperties:
    def test_parse_with_properties(self, make_litematic):
        """Blocks with properties preserve state strings."""
        blocks = [
            (0, 0, 0, "minecraft:repeater[delay=2,facing=west]"),
            (1, 0, 0, "minecraft:furnace[facing=north,lit=false]"),
            (2, 0, 0, "minecraft:piston[extended=false,facing=up]"),
        ]
        path = make_litematic(blocks, name="with_props")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 3
        state_strings = {b[3] for b in result}
        assert "minecraft:repeater[delay=2,facing=west]" in state_strings
        assert "minecraft:furnace[facing=north,lit=false]" in state_strings
        assert "minecraft:piston[extended=false,facing=up]" in state_strings


class TestSchematicParserAirBlocks:
    def test_air_blocks_skipped(self, make_litematic):
        """Schematic with air blocks -> air not in output."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:air"),
            (2, 0, 0, "minecraft:dirt"),
            (3, 0, 0, "minecraft:air"),
        ]
        path = make_litematic(blocks, name="with_air")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 2
        for b in result:
            assert b[3] != "minecraft:air"


class TestSchematicParserMetadata:
    def test_get_metadata(self, make_litematic):
        """Name, author, regions are correct."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        path = make_litematic(blocks, name="my_circuit")
        parser = SchematicParser(path)
        meta = parser.get_metadata()
        assert meta["name"] == "my_circuit"
        assert meta["author"] == "MIRA-Test"
        assert "Main" in meta["regions"]


class TestSchematicParserBounds:
    def test_get_bounds(self, make_litematic):
        """Bounding box matches block extents."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (2, 1, 1, "minecraft:dirt"),
        ]
        path = make_litematic(blocks, name="bounds_test")
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        # Region is (0,0,0,3,2,2) -> (min, max) = ((0,0,0), (3,2,2))
        assert bounds == ((0, 0, 0), (3, 2, 2))

    def test_get_bounds_empty(self, make_litematic):
        """Empty schematic (no blocks placed) -> ((0,0,0),(1,1,1))."""
        path = make_litematic([], name="empty")
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        # Region is (0,0,0,1,1,1) because blocks=[]
        assert bounds == ((0, 0, 0), (1, 1, 1))


class TestSchematicParserBlockStateFormat:
    def test_block_state_format_with_properties(self, make_litematic):
        """Parsed block states have [props] format when applicable."""
        blocks = [
            (0, 0, 0, "minecraft:repeater[delay=2,facing=west]"),
        ]
        path = make_litematic(blocks, name="format_check")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 1
        state_str = result[0][3]
        # Must have minecraft: prefix
        assert state_str.startswith("minecraft:")
        # Must have [] for property blocks
        assert "[" in state_str
        assert state_str.endswith("]")
        assert "delay=2" in state_str
        assert "facing=west" in state_str

    def test_block_state_format_simple(self, make_litematic):
        """Simple blocks without properties have no [] suffix."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="simple_format")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 1
        state_str = result[0][3]
        assert state_str == "minecraft:stone"
        assert "[" not in state_str


class TestSchematicParserMultipleRegions:
    def test_multiple_regions(self, make_litematic, tmp_path):
        """Schematic with two regions — blocks from both are returned."""
        from litemapy import Schematic, Region, BlockState

        # Region offsets (x, y, z) define global position; local coords are 0-based.
        reg_a = Region(0, 0, 0, 3, 1, 1)
        reg_b = Region(0, 5, 0, 3, 1, 1)
        schem = Schematic(name="multi_region", author="MIRA-Test",
                          regions={"A": reg_a, "B": reg_b})
        reg_a[0, 0, 0] = BlockState("minecraft:stone")
        reg_a[1, 0, 0] = BlockState("minecraft:glass")
        reg_b[0, 0, 0] = BlockState("minecraft:dirt")   # local (0,0,0) -> global (0,5,0)

        path = str(tmp_path / "multi_region.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 3

        coords = {(b[0], b[1], b[2]) for b in result}
        assert (0, 0, 0) in coords
        assert (1, 0, 0) in coords
        assert (0, 5, 0) in coords
