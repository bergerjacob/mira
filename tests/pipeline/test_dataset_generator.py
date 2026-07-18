"""
Tests for the ReverseDatasetGenerator pipeline.

Uses the make_litematic fixture to create test schematics and runs the
full pipeline with mock_mode=True — no server, no API calls.
"""
import pytest
from simulation.dataset_generator import ReverseDatasetGenerator
from simulation.teacher_client import TeacherClient
from simulation.deconstructor import ReverseDeconstructor


# =========================================================================
# Helpers
# =========================================================================

def _make_block(x, y, z, state="minecraft:stone", nbt=None):
    return (x, y, z, state, nbt)


# =========================================================================
# Basic Pipeline Tests
# =========================================================================

class TestBasicPipeline:
    """Fundamental ReverseDatasetGenerator behaviour."""

    def setup_method(self):
        self.generator = ReverseDatasetGenerator()

    def test_process_schematic_returns_dict(self, make_litematic):
        """process_schematic returns a dictionary."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="simple")
        result = self.generator.process_schematic(path)
        assert isinstance(result, dict)

    def test_schematic_id_matches_name(self, make_litematic):
        """schematic_id matches the schematic name."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="my_circuit")
        result = self.generator.process_schematic(path)
        assert result["schematic_id"] == "my_circuit"

    def test_status_is_success(self, make_litematic):
        """status field is 'success'."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="test")
        result = self.generator.process_schematic(path)
        assert result["status"] == "success"

    def test_data_present(self, make_litematic):
        """Result contains a 'data' key with nested structure."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="test")
        result = self.generator.process_schematic(path)
        assert "data" in result
        assert isinstance(result["data"], dict)


# =========================================================================
# Metadata Tests
# =========================================================================

class TestMetadata:
    """Pipeline preserves schematic metadata."""

    def setup_method(self):
        self.generator = ReverseDatasetGenerator()

    def test_metadata_in_output(self, make_litematic):
        """Output has metadata from parser."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="meta_test")
        result = self.generator.process_schematic(path)
        data = result["data"]
        assert "metadata" in data
        assert data["metadata"]["name"] == "meta_test"
        assert data["metadata"]["author"] == "MIRA-Test"

    def test_metadata_has_expected_fields(self, make_litematic):
        """Metadata has name, author, description, regions."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="fields")
        result = self.generator.process_schematic(path)
        meta = result["data"]["metadata"]
        assert "name" in meta
        assert "author" in meta
        assert "description" in meta
        assert "regions" in meta


# =========================================================================
# Contract Tests
# =========================================================================

class TestContract:
    """Pipeline generates verification contracts."""

    def setup_method(self):
        self.generator = ReverseDatasetGenerator()

    def test_contract_prompt_in_output(self, make_litematic):
        """Output has contract_prompt with system and user."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="lamp_test")
        result = self.generator.process_schematic(path)
        data = result["data"]
        assert "contract_prompt" in data
        assert "system" in data["contract_prompt"]
        assert "user" in data["contract_prompt"]
        assert isinstance(data["contract_prompt"]["system"], str)
        assert isinstance(data["contract_prompt"]["user"], str)

    def test_verify_contract_in_output(self, make_litematic):
        """Output has verify_contract script string."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="lamp_test")
        result = self.generator.process_schematic(path)
        data = result["data"]
        assert "verify_contract" in data
        assert isinstance(data["verify_contract"], str)
        assert len(data["verify_contract"]) > 0

    def test_verify_contract_has_function(self, make_litematic):
        """verify_contract contains 'def verify_circuit(ctx)'."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="lamp_test")
        result = self.generator.process_schematic(path)
        assert "def verify_circuit(ctx)" in result["data"]["verify_contract"]

    def test_contract_prompt_has_system_content(self, make_litematic):
        """contract_prompt system is the CONTRACT_SYSTEM_PROMPT."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="any")
        result = self.generator.process_schematic(path)
        system = result["data"]["contract_prompt"]["system"]
        assert "Minecraft Redstone Engineer" in system
        assert "verify_circuit" in system


# =========================================================================
# Deconstruction Tests
# =========================================================================

class TestDeconstruction:
    """Pipeline generates deconstruction steps."""

    def setup_method(self):
        self.generator = ReverseDatasetGenerator()

    def test_deconstruction_steps_in_output(self, make_litematic):
        """Output has deconstruction_steps list."""
        path = make_litematic(
            [(0, 0, 0, "minecraft:stone")],
            name="decon_test",
        )
        result = self.generator.process_schematic(path)
        data = result["data"]
        assert "deconstruction_steps" in data
        assert isinstance(data["deconstruction_steps"], list)

    def test_deconstruction_has_at_least_one_step(self, make_litematic):
        """Non-empty schematic produces at least one deconstruction step."""
        blocks = [(i, 0, 0, "minecraft:stone") for i in range(3)]
        path = make_litematic(blocks, name="multi_block")
        result = self.generator.process_schematic(path)
        steps = result["data"]["deconstruction_steps"]
        assert len(steps) >= 1

    def test_deconstruction_step_structure(self, make_litematic):
        """Each deconstruction step has required fields."""
        path = make_litematic(
            [(0, 0, 0, "minecraft:redstone_lamp[lit=false]")],
            name="lamp_test",
        )
        result = self.generator.process_schematic(path)
        for step in result["data"]["deconstruction_steps"]:
            assert "step" in step
            assert "reasoning" in step
            assert "removed_blocks" in step
            assert "remaining_count" in step
            assert "prompt" in step
            assert "snapshot_after" in step


# =========================================================================
# Build Steps Tests
# =========================================================================

class TestBuildSteps:
    """Pipeline generates build steps from deconstruction."""

    def setup_method(self):
        self.generator = ReverseDatasetGenerator()

    def test_build_steps_in_output(self, make_litematic):
        """Output has build_steps list."""
        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="build_test")
        result = self.generator.process_schematic(path)
        data = result["data"]
        assert "build_steps" in data
        assert isinstance(data["build_steps"], list)

    def test_build_steps_reversed(self, make_litematic):
        """First build step = last non-empty deconstruction step."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (0, 1, 0, "minecraft:redstone_wire"),
        ]
        path = make_litematic(blocks, name="two_layer")
        result = self.generator.process_schematic(path)
        decon = result["data"]["deconstruction_steps"]
        build = result["data"]["build_steps"]

        if decon and build:
            # Remove empty deconstruction steps
            non_empty = [s for s in decon if s["removed_blocks"]]
            if non_empty and build:
                assert build[0]["blocks_to_place"] == non_empty[-1]["removed_blocks"]

    def test_build_step_structure(self, make_litematic):
        """Each build step has stage, instruction, blocks_to_place, source_reasoning."""
        path = make_litematic(
            [(0, 0, 0, "minecraft:stone")],
            name="struct_test",
        )
        result = self.generator.process_schematic(path)
        for step in result["data"]["build_steps"]:
            assert "stage" in step
            assert "instruction" in step
            assert "blocks_to_place" in step
            assert "source_reasoning" in step

    def test_build_stages_sequential(self, make_litematic):
        """Build stages are numbered sequentially from 0."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (0, 1, 0, "minecraft:glass"),
            (0, 2, 0, "minecraft:dirt"),
        ]
        path = make_litematic(blocks, name="layers")
        result = self.generator.process_schematic(path)
        for i, step in enumerate(result["data"]["build_steps"]):
            assert step["stage"] == i

    def test_build_blocks_match_total(self, make_litematic):
        """Total blocks in build steps equals original block count."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),
            (1, 0, 0, "minecraft:glass"),
            (0, 1, 0, "minecraft:dirt"),
        ]
        path = make_litematic(blocks, name="count_test")
        result = self.generator.process_schematic(path)
        build = result["data"]["build_steps"]
        total = sum(len(s["blocks_to_place"]) for s in build)
        assert total == len(blocks)

    def test_empty_build_for_empty_schematic(self, make_litematic):
        """Empty schematic → empty build steps."""
        path = make_litematic([], name="empty")
        result = self.generator.process_schematic(path)
        assert result["data"]["build_steps"] == []


# =========================================================================
# Full Pipeline Test
# =========================================================================

class TestFullPipeline:
    """End-to-end pipeline verification."""

    def setup_method(self):
        self.generator = ReverseDatasetGenerator()

    def test_full_pipeline_all_fields(self, make_litematic):
        """All expected fields present and correctly typed."""
        blocks = [
            (0, 0, 0, "minecraft:lever[facing=east,powered=false]"),
            (1, 0, 0, "minecraft:redstone_wire"),
            (2, 0, 0, "minecraft:redstone_lamp[lit=false]"),
        ]
        path = make_litematic(blocks, name="full_circuit")
        result = self.generator.process_schematic(path)

        # Top-level fields
        assert isinstance(result["schematic_id"], str)
        assert result["status"] == "success"

        data = result["data"]

        # Metadata
        assert isinstance(data["metadata"], dict)
        assert isinstance(data["metadata"]["name"], str)
        assert isinstance(data["metadata"]["author"], str)

        # Contract
        assert isinstance(data["contract_prompt"], dict)
        assert isinstance(data["contract_prompt"]["system"], str)
        assert isinstance(data["contract_prompt"]["user"], str)
        assert isinstance(data["verify_contract"], str)

        # Deconstruction steps
        assert isinstance(data["deconstruction_steps"], list)
        for step in data["deconstruction_steps"]:
            assert isinstance(step["step"], int)
            assert isinstance(step["reasoning"], str)
            assert isinstance(step["removed_blocks"], list)
            assert isinstance(step["remaining_count"], int)
            assert isinstance(step["prompt"], dict)

        # Build steps
        assert isinstance(data["build_steps"], list)
        for step in data["build_steps"]:
            assert isinstance(step["stage"], int)
            assert isinstance(step["instruction"], str)
            assert isinstance(step["blocks_to_place"], list)
            assert isinstance(step["source_reasoning"], str)

    def test_full_pipeline_multi_layer(self, make_litematic):
        """Multi-layer circuit produces multiple deconstruction and build steps."""
        blocks = [
            (0, 0, 0, "minecraft:stone"),           # Y=0
            (0, 1, 0, "minecraft:redstone_wire"),    # Y=1
            (0, 2, 0, "minecraft:redstone_lamp[lit=false]"),  # Y=2
        ]
        path = make_litematic(blocks, name="three_layers")
        result = self.generator.process_schematic(path)

        # Should have 3 deconstruction steps (one per Y layer)
        assert len(result["data"]["deconstruction_steps"]) == 3
        # Build steps should have 3 entries (matching non-empty decon steps)
        assert len(result["data"]["build_steps"]) == 3

    def test_mock_mode_no_api_calls(self, make_litematic):
        """No LLM client is ever called."""
        from unittest.mock import MagicMock

        # Create generator with explicit mock TeacherClient
        teacher = TeacherClient(mock_mode=True)
        mock_llm = MagicMock()
        teacher.llm_client = mock_llm

        generator = ReverseDatasetGenerator()
        generator.teacher = teacher
        generator.deconstructor = ReverseDeconstructor(teacher)

        path = make_litematic([(0, 0, 0, "minecraft:stone")], name="no_api")
        generator.process_schematic(path)

        mock_llm.complete.assert_not_called()
