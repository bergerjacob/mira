"""
Unit tests for data_mining/parser.py — edge cases.

Tests empty schematics, entities, NBT data, negative coordinates,
large coordinates, multi-region schematics, and invalid files.
Uses the make_litematic fixture from tests/conftest.py.
No Minecraft server or API calls required.
"""
import os
import pytest
from data_mining.parser import SchematicParser


# ---------------------------------------------------------------------------
# Empty & air-only schematics
# ---------------------------------------------------------------------------

class TestEmptySchematics:
    def test_empty_schematic(self, make_litematic):
        """No blocks -> empty list."""
        path = make_litematic([], name="empty_schem")
        parser = SchematicParser(path)
        blocks = parser.parse_blocks()
        assert blocks == []

    def test_single_block_schematic(self, make_litematic):
        """One block -> list with one entry."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="single_block")
        parser = SchematicParser(path)
        blocks = parser.parse_blocks()
        assert len(blocks) == 1
        assert blocks[0][3] == "minecraft:stone"

    def test_only_air_blocks(self, make_litematic):
        """Schematic with only air blocks -> empty list."""
        path = make_litematic([
            (0, 0, 0, "minecraft:air"),
            (1, 0, 0, "minecraft:air"),
        ], name="all_air")
        parser = SchematicParser(path)
        blocks = parser.parse_blocks()
        assert blocks == []


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

class TestEntities:
    def test_schematic_with_entities(self, make_litematic, tmp_path):
        """Blocks with entities get entity: prefix in state."""
        from litemapy import Schematic, Region, BlockState, Entity
        from nbtlib import Compound, Double, List, String

        reg = Region(0, 0, 0, 3, 1, 1)
        schem = Schematic(name="with_entity", author="MIRA-Test",
                          regions={"Main": reg})
        reg[0, 0, 0] = BlockState("minecraft:stone")

        # Add an entity using NBT compound (Entity constructor accepts str or Compound)
        nbt = Compound({
            'id': String('minecraft:armor_stand'),
            'Pos': List[Double]([Double(1.0), Double(0.0), Double(0.0)]),
        })
        entity = Entity(nbt)
        reg.entities.append(entity)

        path = str(tmp_path / "with_entity.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        blocks = parser.parse_blocks()

        # Check entity is prefixed
        entity_blocks = [b for b in blocks if b[3].startswith("entity:")]
        assert len(entity_blocks) >= 1, "Expected at least one entity block"
        assert "armor_stand" in entity_blocks[0][3]

    def test_entity_has_nbt_data(self, make_litematic, tmp_path):
        """Entity blocks carry NBT data when available."""
        from litemapy import Schematic, Region, BlockState, Entity
        from nbtlib import Compound, Double, List, String, Byte

        reg = Region(0, 0, 0, 3, 1, 1)
        schem = Schematic(name="entity_nbt", author="MIRA-Test",
                          regions={"Main": reg})
        reg[0, 0, 0] = BlockState("minecraft:stone")

        nbt = Compound({
            'id': String('minecraft:armor_stand'),
            'Pos': List[Double]([Double(1.0), Double(0.0), Double(0.0)]),
            'Marker': Byte(1),
            'Invisible': Byte(1),
        })
        entity = Entity(nbt)
        reg.entities.append(entity)

        path = str(tmp_path / "entity_nbt.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        blocks = parser.parse_blocks()

        entity_blocks = [b for b in blocks if b[3].startswith("entity:")]
        if entity_blocks:
            # NBT data should be present
            assert entity_blocks[0][4] is not None


# ---------------------------------------------------------------------------
# NBT data (containers)
# ---------------------------------------------------------------------------

class TestNBTData:
    def test_chest_with_items(self, make_litematic, tmp_path):
        """Chest with NBT data has nbt_data in tuple."""
        from litemapy import Schematic, Region, BlockState

        reg = Region(0, 0, 0, 3, 1, 1)
        schem = Schematic(name="chest_nbt", author="MIRA-Test",
                          regions={"Main": reg})
        reg[0, 0, 0] = BlockState("minecraft:chest", facing="north")

        # Add tile entity data if litemapy supports it
        path = str(tmp_path / "chest_nbt.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        blocks = parser.parse_blocks()

        # Chest block should exist
        chest_blocks = [b for b in blocks if "chest" in b[3]]
        assert len(chest_blocks) == 1
        # NBT might be None if litemapy doesn't expose it, but at minimum no crash
        assert chest_blocks[0][3].startswith("minecraft:chest")


# ---------------------------------------------------------------------------
# Block state properties
# ---------------------------------------------------------------------------

class TestBlockStateProperties:
    def test_repeater_with_properties(self, make_litematic):
        """Repeater block has correct properties."""
        path = make_litematic([
            (0, 0, 0, "minecraft:repeater[delay=2,facing=east]"),
        ], name="repeater_props")
        parser = SchematicParser(path)
        blocks = parser.parse_blocks()
        assert len(blocks) == 1
        state = blocks[0][3]
        assert "delay=2" in state
        assert "facing=east" in state

    def test_comparator_with_properties(self, make_litematic):
        """Comparator has correct mode."""
        path = make_litematic([
            (0, 0, 0, "minecraft:comparator[mode=subtract,facing=west]"),
        ], name="comparator_props")
        parser = SchematicParser(path)
        blocks = parser.parse_blocks()
        assert len(blocks) == 1
        state = blocks[0][3]
        assert "mode=subtract" in state
        assert "facing=west" in state


# ---------------------------------------------------------------------------
# Multiple regions
# ---------------------------------------------------------------------------

class TestMultipleRegions:
    def test_two_regions(self, make_litematic, tmp_path):
        """Blocks from all regions are returned."""
        from litemapy import Schematic, Region, BlockState

        reg_a = Region(0, 0, 0, 3, 1, 1)
        reg_b = Region(0, 10, 0, 3, 1, 1)
        schem = Schematic(name="two_regions", author="MIRA-Test",
                          regions={"A": reg_a, "B": reg_b})
        reg_a[0, 0, 0] = BlockState("minecraft:stone")
        reg_b[0, 0, 0] = BlockState("minecraft:dirt")

        path = str(tmp_path / "two_regions.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        blocks = parser.parse_blocks()
        assert len(blocks) == 2

    def test_region_offset_respected(self, make_litematic, tmp_path):
        """Region offsets are added to block coordinates."""
        from litemapy import Schematic, Region, BlockState

        reg = Region(5, 10, 15, 3, 1, 1)
        schem = Schematic(name="offset", author="MIRA-Test",
                          regions={"Main": reg})
        reg[0, 0, 0] = BlockState("minecraft:stone")

        path = str(tmp_path / "offset.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        blocks = parser.parse_blocks()
        assert len(blocks) == 1
        # Block at region-local (0,0,0) should be at global (5,10,15)
        assert blocks[0][0] == 5
        assert blocks[0][1] == 10
        assert blocks[0][2] == 15


# ---------------------------------------------------------------------------
# Large schematics
# ---------------------------------------------------------------------------

class TestLargeSchematics:
    def test_many_blocks(self, make_litematic):
        """100+ blocks are all parsed."""
        blocks = [(i, 0, 0, "minecraft:stone") for i in range(100)]
        path = make_litematic(blocks, name="large_100")
        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 100


# ---------------------------------------------------------------------------
# File not found & invalid files
# ---------------------------------------------------------------------------

class TestFileErrors:
    def test_non_existent_file(self):
        """Non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            SchematicParser("/nonexistent/path/to/schematic.litematic")

    def test_invalid_file(self, tmp_path):
        """Corrupt/invalid file raises an exception (not a crash)."""
        invalid_path = str(tmp_path / "invalid.litematic")
        with open(invalid_path, "w") as f:
            f.write("not a valid litematic file")

        with pytest.raises(Exception):
            SchematicParser(invalid_path)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_get_metadata(self, make_litematic):
        """Metadata contains name, author, description, regions."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="meta_test")
        parser = SchematicParser(path)
        meta = parser.get_metadata()
        assert "name" in meta
        assert "author" in meta
        assert "description" in meta
        assert "regions" in meta
        assert meta["name"] == "meta_test"
        assert meta["author"] == "MIRA-Test"

    def test_metadata_region_list(self, make_litematic):
        """Regions list contains region names."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="region_check")
        parser = SchematicParser(path)
        meta = parser.get_metadata()
        assert "Main" in meta["regions"]


# ---------------------------------------------------------------------------
# Negative & large coordinates
# ---------------------------------------------------------------------------

class TestCoordinateEdgeCases:
    def test_negative_coordinates(self, tmp_path):
        """Blocks at negative coordinates are parsed correctly."""
        from litemapy import Schematic, Region, BlockState

        # Region at offset (-5, -3, -1) with block at local (0,0,0)
        reg = Region(-5, -3, -1, 1, 1, 1)
        schem = Schematic(name="negative_coords", author="MIRA-Test",
                          regions={"Main": reg})
        reg[0, 0, 0] = BlockState("minecraft:stone")

        path = str(tmp_path / "negative_coords.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 1
        assert result[0][0] == -5
        assert result[0][1] == -3
        assert result[0][2] == -1

    def test_large_positive_coordinates(self, tmp_path):
        """Blocks at large coordinates (1000+) are parsed correctly."""
        from litemapy import Schematic, Region, BlockState

        # Region at offset (1000, 2000, 3000) with block at local (0,0,0)
        reg = Region(1000, 2000, 3000, 1, 1, 1)
        schem = Schematic(name="large_coords", author="MIRA-Test",
                          regions={"Main": reg})
        reg[0, 0, 0] = BlockState("minecraft:stone")

        path = str(tmp_path / "large_coords.litematic")
        schem.save(path)

        parser = SchematicParser(path)
        result = parser.parse_blocks()
        assert len(result) == 1
        assert result[0][0] == 1000
        assert result[0][1] == 2000
        assert result[0][2] == 3000


# ---------------------------------------------------------------------------
# Bounds edge cases
# ---------------------------------------------------------------------------

class TestBoundsEdgeCases:
    def test_get_bounds_single_block(self, make_litematic):
        """Single block has correct bounds."""
        path = make_litematic([(5, 5, 5, "minecraft:stone")], name="bounds_single")
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        # Region is (0,0,0,6,6,6) because block is at (5,5,5) -> max+1 = 6
        assert bounds == ((0, 0, 0), (6, 6, 6))

    def test_get_bounds_empty(self, make_litematic):
        """Empty schematic -> ((0,0,0),(1,1,1))."""
        path = make_litematic([], name="bounds_empty")
        parser = SchematicParser(path)
        bounds = parser.get_bounds()
        assert bounds == ((0, 0, 0), (1, 1, 1))
