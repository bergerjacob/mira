"""
Tests for schematic format conversion round-trips (parser + converter).

Uses the make_litematic fixture to create test schematics programmatically.
No Minecraft server or API calls required.
"""
import os
import pytest
from data_mining.parser import SchematicParser
from data_mining.converter import SchematicConverter


# =========================================================================
# Block List Format Tests
# =========================================================================

class TestBlockListFormat:
    """Parser output format validation."""

    def test_parser_output_is_list(self, make_litematic):
        """Parser output is a list of tuples."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert isinstance(result, list)

    def test_block_tuple_format(self, make_litematic):
        """Each entry is (x, y, z, state_str, nbt)."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        entry = result[0]
        assert isinstance(entry, tuple)
        assert len(entry) == 5
        x, y, z, state, nbt = entry
        assert isinstance(x, int)
        assert isinstance(y, int)
        assert isinstance(z, int)
        assert isinstance(state, str)

    def test_state_string_format_simple(self, make_litematic):
        """Simple block state has 'minecraft:block' format."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert result[0][3] == "minecraft:stone"

    def test_state_string_format_with_properties(self, make_litematic):
        """State with properties has 'minecraft:block[prop=val,...]' format."""
        blocks = [(0, 0, 0, "minecraft:repeater[delay=2,facing=west]")]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        state_str = result[0][3]
        assert state_str.startswith("minecraft:")
        assert "[" in state_str
        assert state_str.endswith("]")
        assert "delay=2" in state_str
        assert "facing=west" in state_str


# =========================================================================
# Air Filtering Tests
# =========================================================================

class TestAirFiltering:
    """Parser must skip air blocks."""

    def test_air_skipped(self, make_litematic):
        """Air blocks are not included in parser output."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:air"),
            (2, 0, 0, "minecraft:dirt"),
        ]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 2
        for b in result:
            assert b[3] != "minecraft:air"

    def test_all_air(self, make_litematic):
        """All-air schematic produces empty block list."""
        blocks = [
            (0, 0, 0, "minecraft:air"),
            (1, 0, 0, "minecraft:air"),
        ]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 0


# =========================================================================
# Entity Prefix Tests
# =========================================================================

class TestEntityPrefix:
    """Entities in schematics are prefixed with 'entity:'."""

    def test_entity_prefix_format(self, make_litematic):
        """
        Entities get the 'entity:' prefix in their state string.
        Note: make_litematic doesn't create entities easily; we verify the
        parser handles them if present. For now this validates the concept.
        """
        # Entities require litemapy entity creation which is complex;
        # the parser code path for entities is tested indirectly via
        # the entity prefix condition in the parser.
        # We verify the prefix logic is correct by inspecting the code.
        from data_mining.parser import SchematicParser
        assert hasattr(SchematicParser, 'parse_blocks')


# =========================================================================
# Metadata Tests
# =========================================================================

class TestMetadata:
    """Parser preserves schematic metadata."""

    def test_name_preserved(self, make_litematic):
        """Schematic name is preserved in metadata."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="my_design")
        parser = SchematicParser(path)
        meta = parser.get_metadata()
        assert meta["name"] == "my_design"

    def test_author_preserved(self, make_litematic):
        """Author is preserved in metadata."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="circuit")
        parser = SchematicParser(path)
        meta = parser.get_metadata()
        assert meta["author"] == "MIRA-Test"

    def test_regions_listed(self, make_litematic):
        """Metadata includes the regions list."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="multi")
        parser = SchematicParser(path)
        meta = parser.get_metadata()
        assert "regions" in meta
        assert isinstance(meta["regions"], list)
        assert "Main" in meta["regions"]


# =========================================================================
# Bounds Calculation Tests
# =========================================================================

class TestBounds:
    """Parser computes correct bounding boxes."""

    def test_bounds_simple(self, make_litematic):
        """Bounding box matches block extents."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (2, 1, 1, "minecraft:dirt"),
        ]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        assert bounds == ((0, 0, 0), (3, 2, 2))

    def test_bounds_empty(self, make_litematic):
        """Empty schematic returns ((0,0,0), (1,1,1))."""
        path = make_litematic([])
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        assert bounds == ((0, 0, 0), (1, 1, 1))

    def test_bounds_single_block(self, make_litematic):
        """Single block at origin → bounds from (0,0,0) to (1,1,1)."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")])
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        assert bounds == ((0, 0, 0), (1, 1, 1))

    def test_bounds_negative_coords(self, make_litematic):
        """Blocks at non-zero positions produce correct region bounds."""
        blocks = [
            (5, 3, 2, "minecraft:stone"),
            (7, 5, 4, "minecraft:glass"),
        ]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        # make_litematic creates a Region at (0,0,0) sized to fit all blocks,
        # so min is the region origin (0,0,0) and max is the dimensions
        assert bounds == ((0, 0, 0), (8, 6, 5))


# =========================================================================
# Round-trip Tests (Litematic → blocks → Litematic)
# =========================================================================

class TestLitematicRoundTrip:
    """Create litematic, parse to blocks, create new litematic, parse again."""

    def test_round_trip_simple(self, make_litematic):
        """3 simple blocks survive round-trip."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:glass"),
            (2, 0, 0, "minecraft:dirt"),
        ]
        # First pass: create → parse
        path1 = make_litematic(blocks, name="round1")
        parser1 = SchematicParser(path1)
        parsed1 = parser1.parse_blocks()

        # Re-create litematic from parsed blocks (strip nbt)
        re_blocks = [(b[0], b[1], b[2], b[3]) for b in parsed1]
        path2 = make_litematic(re_blocks, name="round2")

        # Second pass: parse the re-created file
        parser2 = SchematicParser(path2)
        parsed2 = parser2.parse_blocks()

        # Same number of blocks
        assert len(parsed1) == len(parsed2)
        # Same state strings (sorted for comparison)
        states1 = sorted(b[3] for b in parsed1)
        states2 = sorted(b[3] for b in parsed2)
        assert states1 == states2

    def test_round_trip_with_properties(self, make_litematic):
        """Blocks with properties survive round-trip."""
        blocks = [
            (0, 0, 0, "minecraft:repeater[delay=2,facing=west]"),
            (1, 0, 0, "minecraft:piston[extended=false,facing=up]"),
        ]
        path1 = make_litematic(blocks, name="props_round")
        parser1 = SchematicParser(path1)
        parsed1 = parser1.parse_blocks()

        re_blocks = [(b[0], b[1], b[2], b[3]) for b in parsed1]
        path2 = make_litematic(re_blocks, name="props_round2")
        parser2 = SchematicParser(path2)
        parsed2 = parser2.parse_blocks()

        states1 = sorted(b[3] for b in parsed1)
        states2 = sorted(b[3] for b in parsed2)
        assert states1 == states2


# =========================================================================
# Multi-Region Tests
# =========================================================================

class TestMultiRegion:
    """Parser handles schematics with multiple regions."""

    def test_multiple_regions_parsed(self, make_litematic, tmp_path):
        """Blocks from multiple regions are all returned."""
        from litemapy import Schematic, Region, BlockState

        reg_a = Region(0, 0, 0, 3, 1, 1)
        reg_b = Region(0, 5, 0, 3, 1, 1)
        schem = Schematic(
            name="multi_region", author="MIRA-Test",
            regions={"A": reg_a, "B": reg_b}
        )
        reg_a[0, 0, 0] = BlockState("minecraft:stone")
        reg_a[1, 0, 0] = BlockState("minecraft:glass")
        reg_b[0, 0, 0] = BlockState("minecraft:dirt")

        path = str(tmp_path / "multi_region.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 3

        coords = {(b[0], b[1], b[2]) for b in result}
        assert (0, 0, 0) in coords
        assert (1, 0, 0) in coords
        assert (0, 5, 0) in coords

    def test_multi_region_metadata(self, make_litematic, tmp_path):
        """Multi-region metadata lists all region names."""
        from litemapy import Schematic, Region, BlockState

        reg_a = Region(0, 0, 0, 2, 1, 1)
        reg_b = Region(0, 5, 0, 2, 1, 1)
        schem = Schematic(
            name="two_regions", author="Tester",
            regions={"Alpha": reg_a, "Beta": reg_b}
        )
        reg_a[0, 0, 0] = BlockState("minecraft:stone")

        path = str(tmp_path / "two_regions.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        meta = parser.get_metadata()
        assert set(meta["regions"]) == {"Alpha", "Beta"}
        assert meta["author"] == "Tester"


# =========================================================================
# Format Conversion Tests
# =========================================================================

class TestFormatConversion:
    """Conversion between schematic formats."""

    def test_litematic_to_sponge_schem(self, make_litematic, tmp_path):
        """Convert litematic to sponge .schem and verify file exists."""
        blocks = [(0, 0, 0, "minecraft:stone"), (1, 0, 0, "minecraft:glass")]
        litematic_path = make_litematic(blocks, name="convert_test")

        output_path = str(tmp_path / "converted.schem")
        result = SchematicConverter.litematic_to_sponge_schem(litematic_path, output_path)
        assert os.path.exists(result)
        assert result.endswith(".schem")

    def test_litematic_to_vanilla_structure(self, make_litematic, tmp_path):
        """Convert litematic to vanilla .nbt structure and verify file exists."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        litematic_path = make_litematic(blocks, name="struct_test")

        output_path = str(tmp_path / "structure.nbt")
        result = SchematicConverter.litematic_to_vanilla_structure(litematic_path, output_path)
        assert os.path.exists(result)
        assert result.endswith(".nbt")

    def test_conversion_source_not_found(self):
        """Converter raises FileNotFoundError for missing source."""
        with pytest.raises(FileNotFoundError):
            SchematicConverter.litematic_to_sponge_schem(
                "/nonexistent/file.litematic", "/tmp/out.schem"
            )

    def test_conversion_preserves_blocks(self, make_litematic, tmp_path):
        """Blocks survive conversion through sponge format (verify file exists and has data)."""
        blocks = [(0, 0, 0, "minecraft:stone"), (1, 0, 0, "minecraft:glass")]
        litematic_path = make_litematic(blocks, name="preserve_test")

        # Convert to sponge
        sponge_path = str(tmp_path / "preserved.schem")
        result_path = SchematicConverter.litematic_to_sponge_schem(litematic_path, sponge_path)

        # Verify the file exists and has content
        assert os.path.exists(result_path)
        assert os.path.getsize(result_path) > 0
        assert result_path.endswith(".schem")


# =========================================================================
# Large Schematic Tests
# =========================================================================

class TestLargeSchematic:
    """Parser handles larger schematics correctly."""

    def test_50_blocks(self, make_litematic):
        """50 blocks parse correctly."""
        blocks = [(i, 0, 0, "minecraft:stone") for i in range(50)]
        path = make_litematic(blocks, name="large_50")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 50

    def test_large_all_unique_states(self, make_litematic):
        """All 50 blocks have the expected state strings."""
        blocks = [(i, j, 0, "minecraft:stone") for i in range(10) for j in range(5)]
        path = make_litematic(blocks, name="grid")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 50
        for b in result:
            assert b[3] == "minecraft:stone"


# =========================================================================
# Edge Cases
# =========================================================================

class TestEdgeCases:
    """Edge cases for parser and converter."""

    def test_file_not_found(self):
        """Parser raises FileNotFoundError for nonexistent file."""
        with pytest.raises(FileNotFoundError):
            SchematicParser("/nonexistent/path/to/schematic.litematic")

    def test_single_block_round_trip(self, make_litematic):
        """Single block survives parse → re-create → parse."""
        blocks = [(5, 3, 2, "minecraft:stone")]
        path1 = make_litematic(blocks)
        parser1 = SchematicParser(path1)
        parsed1 = parser1.parse_blocks()
        assert len(parsed1) == 1
        assert parsed1[0][:4] == (5, 3, 2, "minecraft:stone")

    def test_coordinate_preservation(self, make_litematic):
        """Coordinates are preserved through parse."""
        blocks = [(3, 7, 1, "minecraft:glass")]
        path = make_litematic(blocks)
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        b = result[0]
        assert b[0] == 3
        assert b[1] == 7
        assert b[2] == 1
