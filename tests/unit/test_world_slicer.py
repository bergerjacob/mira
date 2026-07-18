"""
Unit tests for data_mining/world_slicer.py WorldSlicer class.

Uses the make_litematic fixture to create test schematics.
No Minecraft server or API calls required.
"""
import os
import pytest
from data_mining.world_slicer import WorldSlicer
from data_mining.parser import SchematicParser


# ---------------------------------------------------------------------------
# Single component
# ---------------------------------------------------------------------------

class TestSingleComponent:
    def test_single_component(self, make_litematic):
        """All blocks connected -> 1 island."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:stone"),
            (2, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="single_comp")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1


# ---------------------------------------------------------------------------
# Multiple disconnected components
# ---------------------------------------------------------------------------

class TestMultipleComponents:
    def test_two_disconnected_components(self, make_litematic):
        """Two groups far apart -> 2 islands."""
        blocks = (
            # Group A at y=0
            [(0, 0, 0, "minecraft:stone"),
             (0, 0, 1, "minecraft:stone"),
             (0, 0, 2, "minecraft:stone")] +
            # Group B at y=5 (gap of 3 > threshold=2)
            [(0, 5, 0, "minecraft:dirt"),
             (0, 5, 1, "minecraft:dirt"),
             (0, 5, 2, "minecraft:dirt")]
        )
        path = make_litematic(blocks, name="two_groups")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 2

    def test_three_components(self, make_litematic):
        """Three separate groups -> 3 islands."""
        blocks = (
            # Group A at (0,0,0)
            [(0, 0, 0, "minecraft:stone"),
             (0, 0, 1, "minecraft:stone"),
             (0, 0, 2, "minecraft:stone")] +
            # Group B at (0,5,0)
            [(0, 5, 0, "minecraft:glass"),
             (0, 5, 1, "minecraft:glass"),
             (0, 5, 2, "minecraft:glass")] +
            # Group C at (0,10,0)
            [(0, 10, 0, "minecraft:dirt"),
             (0, 10, 1, "minecraft:dirt"),
             (0, 10, 2, "minecraft:dirt")]
        )
        path = make_litematic(blocks, name="three_groups")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 3


# ---------------------------------------------------------------------------
# Coordinate normalization
# ---------------------------------------------------------------------------

class TestCoordinateNormalization:
    def test_coordinate_normalization(self, make_litematic):
        """Island coords normalized to start at (0,0,0)."""
        blocks = [
            (5, 5, 5, "minecraft:stone"),
            (5, 5, 6, "minecraft:stone"),
            (5, 6, 5, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="norm_check")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        island = components[0]
        # All coords should have min_x/min_y/min_z subtracted
        xs = {b[0] for b in island}
        ys = {b[1] for b in island}
        zs = {b[2] for b in island}
        assert 0 in xs and 0 in ys and 0 in zs
        # Original (5,5,5) should become (0,0,0)
        assert any(b[0] == 0 and b[1] == 0 and b[2] == 0 for b in island)
        # Original (5,5,6) should become (0,0,1)
        assert any(b[0] == 0 and b[1] == 0 and b[2] == 1 for b in island)
        # Original (5,6,5) should become (0,1,0)
        assert any(b[0] == 0 and b[1] == 1 and b[2] == 0 for b in island)


# ---------------------------------------------------------------------------
# Min size filter
# ---------------------------------------------------------------------------

class TestMinSizeFilter:
    def test_min_size_filter(self, make_litematic):
        """Component with < min_size blocks is filtered out."""
        blocks = (
            # Group A: 4 blocks -> survives with min_size=3
            [(0, 0, 0, "minecraft:stone"),
             (1, 0, 0, "minecraft:stone"),
             (0, 1, 0, "minecraft:stone"),
             (0, 0, 1, "minecraft:stone")] +
            # Group B: 2 blocks -> filtered out with min_size=3
            [(5, 0, 0, "minecraft:dirt"),
             (5, 0, 1, "minecraft:dirt")]
        )
        path = make_litematic(blocks, name="min_size_filter")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        assert len(components[0]) >= 3


# ---------------------------------------------------------------------------
# Chebyshev distance
# ---------------------------------------------------------------------------

class TestChebyshevDistance:
    def test_chebyshev_distance(self, make_litematic):
        """Blocks within distance_threshold are connected."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (2, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="cheb_connected")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        # Both blocks should be in the same component
        assert len(components) == 1
        assert len(components[0]) == 2

    def test_chebyshev_distance_gap(self, make_litematic):
        """Blocks beyond distance_threshold are separate."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (3, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="cheb_disconnected")
        slicer = WorldSlicer(distance_threshold=2, min_size=1)
        components = slicer.slice_schematic(path)
        # Two separate components
        assert len(components) == 2
        assert len(components[0]) == 1
        assert len(components[1]) == 1


# ---------------------------------------------------------------------------
# Save components
# ---------------------------------------------------------------------------

class TestSaveComponents:
    def test_save_components(self, make_litematic, tmp_path):
        """Save to litematic files and read them back."""
        blocks = (
            # Group A
            [(0, 0, 0, "minecraft:stone"),
             (0, 0, 1, "minecraft:stone"),
             (0, 0, 2, "minecraft:stone")] +
            # Group B
            [(5, 5, 5, "minecraft:dirt"),
             (5, 5, 6, "minecraft:dirt"),
             (5, 5, 7, "minecraft:dirt")]
        )
        path = make_litematic(blocks, name="save_test")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 2

        out_dir = str(tmp_path / "slices")
        slicer.save_components_to_litematic(components, out_dir, base_name="comp")
        # Check files exist
        assert os.path.isfile(os.path.join(out_dir, "comp_0.litematic"))
        assert os.path.isfile(os.path.join(out_dir, "comp_1.litematic"))

        # Read back and verify
        p0 = SchematicParser(os.path.join(out_dir, "comp_0.litematic"))
        blocks0 = p0.parse_blocks()
        assert len(blocks0) == 3

        p1 = SchematicParser(os.path.join(out_dir, "comp_1.litematic"))
        blocks1 = p1.parse_blocks()
        assert len(blocks1) == 3


# ---------------------------------------------------------------------------
# Empty schematic
# ---------------------------------------------------------------------------

class TestEmptySchematic:
    def test_empty_schematic(self, make_litematic):
        """No blocks -> empty list."""
        path = make_litematic([], name="empty_schem")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert components == []


# ---------------------------------------------------------------------------
# Sorted output
# ---------------------------------------------------------------------------

class TestSortedOutput:
    def test_sorted_output(self, make_litematic):
        """Normalized islands have blocks sorted by (y, x, z)."""
        blocks = [
            (0, 0, 1, "minecraft:stone"),
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="sort_check")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        island = components[0]
        # Verify sorted by (y, x, z) — all y=0 here, check x then z
        for i in range(len(island) - 1):
            key_i = (island[i][1], island[i][0], island[i][2])
            key_next = (island[i + 1][1], island[i + 1][0], island[i + 1][2])
            assert key_i <= key_next, f"Not sorted: {key_i} > {key_next}"

    def test_sorted_output_by_y_then_x_then_z(self, make_litematic):
        """Blocks sorted by y first, then x, then z."""
        blocks = [
            (1, 2, 0, "minecraft:stone"),
            (0, 1, 0, "minecraft:stone"),
            (0, 0, 0, "minecraft:stone"),
        ]
        path = make_litematic(blocks, name="sort_yxz")
        slicer = WorldSlicer(distance_threshold=2, min_size=3)
        components = slicer.slice_schematic(path)
        assert len(components) == 1
        island = components[0]
        # Expected order after normalization: (0,0,0), (0,1,0), (0,2,0)
        # but normalization subtracts min values, so:
        # (0,0,0) -> (0,0,0), (0,1,0) -> (0,1,0), (1,2,0) -> (1,2,0)
        # Actually input: (0,0,0), (0,1,0), (1,2,0) after normalization (min_x=0, min_y=0, min_z=0)
        # sorted by (y,x,z): (0,0,0) y=0, (0,1,0) y=1, (1,2,0) y=2
        for i in range(len(island) - 1):
            key_i = (island[i][1], island[i][0], island[i][2])
            key_next = (island[i + 1][1], island[i + 1][0], island[i + 1][2])
            assert key_i <= key_next, f"Not sorted: {key_i} > {key_next}"
