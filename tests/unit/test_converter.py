"""
Unit tests for data_mining/converter.py SchematicConverter class.

Uses the make_litematic fixture to create test schematics.
No Minecraft server or API calls required.
"""
import os
import json
import pytest
from data_mining.converter import SchematicConverter, setup_datapack


# ---------------------------------------------------------------------------
# litematic_to_sponge_schem
# ---------------------------------------------------------------------------

class TestLitematicToSpongeSchem:
    def test_conversion_creates_file(self, make_litematic, tmp_path):
        """Convert a litematic to .schem file, verify it exists."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:glass"),
        ]
        litematic_path = make_litematic(blocks, name="test_sponge")
        output_path = str(tmp_path / "test_sponge.schem")

        result = SchematicConverter.litematic_to_sponge_schem(litematic_path, output_path)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_conversion_output_is_nbt_file(self, make_litematic, tmp_path):
        """.schem output is a valid NBT file (not empty)."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        litematic_path = make_litematic(blocks, name="valid_nbt")
        output_path = str(tmp_path / "valid_nbt.schem")

        SchematicConverter.litematic_to_sponge_schem(litematic_path, output_path)
        assert os.path.getsize(output_path) > 0

    def test_conversion_with_properties(self, make_litematic, tmp_path):
        """Blocks with properties survive the conversion."""
        blocks = [
            (0, 0, 0, "minecraft:repeater[delay=2,facing=west]"),
            (1, 0, 0, "minecraft:lever[facing=east,powered=false]"),
        ]
        litematic_path = make_litematic(blocks, name="props_sponge")
        output_path = str(tmp_path / "props_sponge.schem")

        SchematicConverter.litematic_to_sponge_schem(litematic_path, output_path)
        assert os.path.exists(output_path)

    def test_conversion_empty_region_succeeds(self, make_litematic, tmp_path):
        """Schematic with a region but no blocks still converts successfully (all air)."""
        from litemapy import Schematic, Region

        reg = Region(0, 0, 0, 1, 1, 1)
        schem = Schematic(name="empty_region", author="test",
                          regions={"Main": reg})
        empty_path = str(tmp_path / "empty_region.litematic")
        schem.save(empty_path)

        output_path = str(tmp_path / "empty_region.schem")
        # Should succeed — empty region yields a valid sponge schematic
        result = SchematicConverter.litematic_to_sponge_schem(empty_path, output_path)
        assert result == output_path
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0

    def test_conversion_round_trip(self, make_litematic, tmp_path):
        """Create litematic, convert to sponge, verify file is valid NBT."""
        import nbtlib
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:dirt"),
        ]
        litematic_path = make_litematic(blocks, name="round_trip")
        output_path = str(tmp_path / "round_trip.schem")

        SchematicConverter.litematic_to_sponge_schem(litematic_path, output_path)

        # Load the NBT file to verify it's valid
        nbt_data = nbtlib.load(output_path)
        assert nbt_data is not None
        # Sponge schem files have a 'Palette' key
        assert "Palette" in nbt_data or "palette" in nbt_data


# ---------------------------------------------------------------------------
# litematic_to_vanilla_structure
# ---------------------------------------------------------------------------

class TestLitematicToVanillaStructure:
    def test_conversion_creates_file(self, make_litematic, tmp_path):
        """Convert a litematic to .nbt structure file, verify it exists."""
        blocks = [(0, 0, 0, "minecraft:stone")]
        litematic_path = make_litematic(blocks, name="test_struct")
        output_path = str(tmp_path / "test_struct.nbt")

        result = SchematicConverter.litematic_to_vanilla_structure(litematic_path, output_path)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_conversion_data_version(self, make_litematic, tmp_path):
        """Vanilla structure has DataVersion set to 3953."""
        import nbtlib
        blocks = [(0, 0, 0, "minecraft:stone")]
        litematic_path = make_litematic(blocks, name="dataversion")
        output_path = str(tmp_path / "dataversion.nbt")

        SchematicConverter.litematic_to_vanilla_structure(litematic_path, output_path)
        nbt_data = nbtlib.load(output_path)
        # Should have DataVersion key (3953 for 1.21)
        assert "DataVersion" in nbt_data
        assert nbt_data["DataVersion"] == 3953

    def test_conversion_empty_region_succeeds(self, make_litematic, tmp_path):
        """Schematic with a region but no blocks converts to vanilla structure too."""
        from litemapy import Schematic, Region

        reg = Region(0, 0, 0, 1, 1, 1)
        schem = Schematic(name="empty_region", author="test",
                          regions={"Main": reg})
        empty_path = str(tmp_path / "empty_struct.litematic")
        schem.save(empty_path)

        output_path = str(tmp_path / "empty_struct.nbt")
        # Should succeed — empty region yields a valid structure
        result = SchematicConverter.litematic_to_vanilla_structure(empty_path, output_path)
        assert result == output_path
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0


# ---------------------------------------------------------------------------
# FileNotFoundError
# ---------------------------------------------------------------------------

class TestFileNotFound:
    def test_litematic_to_sponge_not_found(self):
        """Non-existent litematic raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="not found"):
            SchematicConverter.litematic_to_sponge_schem(
                "/nonexistent/file.litematic",
                "/tmp/out.schem",
            )

    def test_litematic_to_vanilla_not_found(self):
        """Non-existent litematic raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="not found"):
            SchematicConverter.litematic_to_vanilla_structure(
                "/nonexistent/file.litematic",
                "/tmp/out.nbt",
            )


# ---------------------------------------------------------------------------
# setup_datapack
# ---------------------------------------------------------------------------

class TestSetupDatapack:
    def test_setup_datapack_creates_directories(self, tmp_path):
        """setup_datapack creates the expected directory structure."""
        server_path = str(tmp_path / "server")
        os.makedirs(server_path)

        struct_dir = setup_datapack(server_path, namespace="mira")

        expected = os.path.join(
            server_path, "world", "datapacks", "mira_structures",
            "data", "mira", "structures",
        )
        assert struct_dir == expected
        assert os.path.isdir(expected)

    def test_setup_datapack_creates_mcmeta(self, tmp_path):
        """setup_datapack creates pack.mcmeta with correct content."""
        server_path = str(tmp_path / "server")
        os.makedirs(server_path)

        setup_datapack(server_path, namespace="mira")

        mcmeta_path = os.path.join(
            server_path, "world", "datapacks", "mira_structures",
            "pack.mcmeta",
        )
        assert os.path.exists(mcmeta_path)

        with open(mcmeta_path) as f:
            mcmeta = json.load(f)

        assert "pack" in mcmeta
        assert mcmeta["pack"]["pack_format"] == 48
        assert "MIRA" in mcmeta["pack"]["description"]

    def test_setup_datapack_custom_namespace(self, tmp_path):
        """Custom namespace is reflected in directory structure."""
        server_path = str(tmp_path / "server")
        os.makedirs(server_path)

        struct_dir = setup_datapack(server_path, namespace="custom_ns")

        expected = os.path.join(
            server_path, "world", "datapacks", "mira_structures",
            "data", "custom_ns", "structures",
        )
        assert struct_dir == expected
        assert os.path.isdir(expected)

    def test_setup_datapack_idempotent(self, tmp_path):
        """Calling setup_datapack twice doesn't error."""
        server_path = str(tmp_path / "server")
        os.makedirs(server_path)

        struct_dir1 = setup_datapack(server_path)
        struct_dir2 = setup_datapack(server_path)

        assert struct_dir1 == struct_dir2
        assert os.path.isdir(struct_dir1)
