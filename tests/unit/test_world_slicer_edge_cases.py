"""
Unit tests for data_mining/world_slicer.py — edge cases.

Tests single blocks, disconnected blocks, adjacency thresholds,
3D structures, normalization, sorting, min_size filtering,
and save functionality.
Uses the make_litematic fixture from tests/conftest.py.
No Minecraft server or API calls required.
"""
import os
import pytest
from data_mining.world_slicer import WorldSlicer
from data_mining.parser import SchematicParser


# ---------------------------------------------------------------------------
# Single block
# ---------------------------------------------------------------------------

class TestSingleBlock:
    def test_single_block(self, make_litematic):
        """One block -> one component with one block."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        path = make_litematic(blocks, name="single_block")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        assert len(components[0]) == 1


# ---------------------------------------------------------------------------
# Two disconnected single blocks
# ---------------------------------------------------------------------------

class TestTwoDisconnectedSingleBlocks:
    def test_two_singles_far_apart(self, make_litematic):
        """Two disconnected single blocks -> two components."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (10, 0, 0, "minecraft:glass"),
        ]
        path = make_litematic(blocks, name="two_singles")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 2
        assert len(components[0]) == 1
        assert len(components[1]) == 1


# ---------------------------------------------------------------------------
# Adjacency (Chebyshev distance)
# ---------------------------------------------------------------------------

class TestAdjacency:
    def test_touching_blocks_one_component(self, make_litematic):
        """Two blocks at Chebyshev distance 1 -> one component."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="touching")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        assert len(components[0]) == 2

    def test_gap_of_one_within_threshold(self, make_litematic):
        """Blocks with gap of 1 (distance 2) -> one component (threshold=2)."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (2, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="gap_one")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        assert len(components[0]) == 2

    def test_gap_of_two_beyond_threshold(self, make_litematic):
        """Blocks with gap of 2 (distance 3) -> two components (threshold=2)."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (3, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="gap_two")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 2

    def test_diagonal_touching(self, make_litematic):
        """Diagonal blocks (Chebyshev distance 1) -> one component."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 1, 1, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="diagonal")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        assert len(components[0]) == 2


# ---------------------------------------------------------------------------
# 3D structure (different Y levels)
# ---------------------------------------------------------------------------

class Test3DStructures:
    def test_vertical_stack(self, make_litematic):
        """Blocks stacked vertically -> one component."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (0, 1, 0, "minecraft:stone"),
            (0, 2, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="vertical_stack")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        assert len(components[0]) == 3

    def test_vertical_separated(self, make_litematic):
        """Blocks far apart vertically -> two components."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (0, 5, 0, "minecraft:stone"),
            (0, 6, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="vertical_sep")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 2  # (0,0,0) alone, (0,5,0)+(0,6,0) together

    def test_3d_cube(self, make_litematic):
        """Small 3D cube of blocks -> one component."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:stone"),
            (0, 0, 1, "minecraft:stone"),
            (1, 0, 1, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="cube_2x2")
        slicer = WorldSlicer(distance_threshold=2, min_size=4)
        components = slicer.slice_schematic(path)
        assert len(components) == 1


# ---------------------------------------------------------------------------
# Large gap in middle
# ---------------------------------------------------------------------------

class TestLargeGap:
    def test_large_gap_splits(self, make_litematic):
        """Two groups with large gap -> two components."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (0, 0, 1, "minecraft:stone"),
            (0, 0, 2, "minecraft:stone"),
            (10, 0, 0, "minecraft:dirt"),
            (10, 0, 1, "minecraft:dirt"),
            (10, 0, 2, "minecraft:dirt"),
        ]
        path = make_litematic(blocks, name="large_gap")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 2


# ---------------------------------------------------------------------------
# Min size filter
# ---------------------------------------------------------------------------

class TestMinSizeFilter:
    def test_min_size_filters_small(self, make_litematic):
        """Component below min_size -> filtered out."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:stone"),
            (10, 10, 10, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="min_size_filter")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        # Both components have 1 or 2 blocks, all below min_size=3
        assert components == []

    def test_min_size_one_passes_all(self, make_litematic):
        """min_size=1 -> all components pass."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (10, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="min_size_one")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        assert len(components) == 2

    def test_mixed_sizes(self, make_litematic):
        """Big component passes, small component filtered."""
        blocks = (
            [(0, 0, 0, "minecraft:stone"),
             (0, 0, 1, "minecraft:stone"),
             (0, 0, 2, "minecraft:stone")] +
            [(10, 0, 0, "minecraft:dirt"),
             (10, 0, 1, "minecraft:dirt")]
        )
        path = make_litematic(blocks, name="mixed_sizes")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        assert len(components[0]) == 3


# ---------------------------------------------------------------------------
# Coordinate normalization
# ---------------------------------------------------------------------------

class TestCoordinateNormalization:
    def test_normalized_to_zero(self, make_litematic):
        """Component coordinates normalized to start at (0,0,0)."""
        blocks = [
            (5, 5, 5, "minecraft:stone"),
            (5, 5, 6, "minecraft:stone"),
            (5, 6, 5, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="normalize_check")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        comp = components[0]
        xs = {b[0] for b in comp}
        ys = {b[1] for b in comp}
        zs = {b[2] for b in comp}
        assert 0 in xs
        assert 0 in ys
        assert 0 in zs

    def test_negative_coordinates_normalized(self, tmp_path):
        """Negative coordinates are normalized to (0,0,0)."""
        from litemapy import Schematic, Region, BlockState

        # Region at offset (-3, -2, -1) with blocks at local (0,0,0), (1,0,0), (2,0,0)
        reg = Region(-3, -2, -1, 3, 1, 1)
        schem = Schematic(name="negative_norm", author="MIRA-Test",
                          regions={"Main": reg})
        reg[0, 0, 0] = BlockState("minecraft:stone")
        reg[1, 0, 0] = BlockState("minecraft:stone")
        reg[2, 0, 0] = BlockState("minecraft:stone")

        path = str(tmp_path / "negative_norm.litematic")
        schem.save(path)

        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        comp = components[0]
        xs = {b[0] for b in comp}
        ys = {b[1] for b in comp}
        zs = {b[2] for b in comp}
        assert 0 in xs
        assert 0 in ys
        assert 0 in zs


# ---------------------------------------------------------------------------
# Sorted output
# ---------------------------------------------------------------------------

class TestSortedOutput:
    def test_sorted_by_yxz(self, make_litematic):
        """Blocks within each component sorted by (y, x, z)."""
        blocks = [
            (1, 2, 0, "minecraft:stone"),
            (0, 1, 0, "minecraft:stone"),
            (0, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="sorted_yxz")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        comp = components[0]
        for i in range(len(comp) - 1):
            key_i = (comp[i][1], comp[i][0], comp[i][2])
            key_next = (comp[i + 1][1], comp[i + 1][0], comp[i + 1][2])
            assert key_i <= key_next

    def test_sorted_maintains_order_after_normalization(self, make_litematic):
        """Sort order holds after coordinate normalization."""
        blocks = [
            (0, 0, 1, "minecraft:stone"),
            (0, 0, 0, "minecraft:stone"),
            (1, 1, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="sorted_after_norm")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        comp = components[0]
        for i in range(len(comp) - 1):
            key_i = (comp[i][1], comp[i][0], comp[i][2])
            key_next = (comp[i + 1][1], comp[i + 1][0], comp[i + 1][2])
            assert key_i <= key_next


# ---------------------------------------------------------------------------
# Save components to litematic
# ---------------------------------------------------------------------------

class TestSaveComponents:
    def test_save_creates_valid_litematic(self, make_litematic, tmp_path):
        """Saved .litematic files are valid and can be read back."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (0, 0, 1, "minecraft:stone"),
            (0, 0, 2, "minecraft:stone"),
            (10, 0, 0, "minecraft:dirt"),
            (10, 0, 1, "minecraft:dirt"),
            (10, 0, 2, "minecraft:dirt"),
        ]
        path = make_litematic(blocks, name="save_components")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 2

        out_dir = str(tmp_path / "slices")
        slicer.save_components_to_litematic(components, out_dir, base_name="comp")

        # Verify files exist
        for i in range(2):
            file_path = os.path.join(out_dir, f"comp_{i}.litematic")
            assert os.path.isfile(file_path), f"Expected {file_path} to exist"

            # Read back and verify
            parser = SchematicParser(file_path)
            parsed = parser.parse_blocks()
            assert len(parsed) == 3

    def test_save_with_properties(self, make_litematic, tmp_path):
        """Blocks with state properties survive the save round-trip."""
        blocks = [
            (0, 0, 0, "minecraft:repeater[delay=2,facing=west]"),
            (0, 0, 1, "minecraft:lever[facing=east,powered=false]"),
            (0, 0, 2, "minecraft:redstone_lamp[lit=false]"),
        ]
        path = make_litematic(blocks, name="save_props")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1

        out_dir = str(tmp_path / "props_slices")
        slicer.save_components_to_litematic(components, out_dir, base_name="props")

        file_path = os.path.join(out_dir, "props_0.litematic")
        parser = SchematicParser(file_path)
        parsed = parser.parse_blocks()
        assert len(parsed) == 3

        # Verify properties preserved
        state_strings = {b[3] for b in parsed}
        assert "minecraft:repeater[delay=2,facing=west]" in state_strings

    def test_no_components_to_save(self, make_litematic, tmp_path):
        """Saving zero components -> no error, no files."""
        path = make_litematic([], name="empty_save")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert components == []

        out_dir = str(tmp_path / "empty_slices")
        os.makedirs(out_dir, exist_ok=True)
        slicer.save_components_to_litematic(components, out_dir, base_name="empty")
        # No files should have been created
        assert not os.listdir(out_dir)
