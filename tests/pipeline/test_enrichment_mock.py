"""
Tests for the enrichment pipeline using mock LLM responses.

No server needed, no API calls made. TeacherClient with mock_mode=True
returns deterministic scripts/reasoning without calling any LLM.
"""
import pytest
from unittest.mock import MagicMock

from simulation.teacher_client import TeacherClient


# =========================================================================
# Helpers
# =========================================================================

def _make_block(x, y, z, state="minecraft:stone", nbt=None):
    """Create a (x, y, z, state, nbt) tuple matching TeacherClient input."""
    return (x, y, z, state, nbt)


def _lamp_blocks():
    """Typical block list for a lever + dust + lamp circuit."""
    return [
        _make_block(0, 1, 0, "minecraft:lever[facing=east,powered=false]"),
        _make_block(1, 1, 0, "minecraft:redstone_wire"),
        _make_block(2, 1, 0, "minecraft:redstone_lamp[lit=false]"),
    ]


def _multi_layer_blocks():
    """Blocks at multiple Y-levels for deconstruction tests."""
    return [
        _make_block(0, 0, 0, "minecraft:stone"),
        _make_block(0, 1, 0, "minecraft:redstone_wire"),
        _make_block(0, 2, 0, "minecraft:redstone_lamp[lit=false]"),
    ]


# =========================================================================
# Mock Contract Generation Tests
# =========================================================================

class TestMockContractGeneration:
    """Tests for TeacherClient.generate_test_contract in mock mode."""

    def setup_method(self):
        self.client = TeacherClient(mock_mode=True)

    def test_mock_description_format(self):
        """Mock generation returns a script (string)."""
        result = self.client.generate_test_contract(
            {"name": "test_lamp", "description": "Test lamp circuit"},
            _lamp_blocks(),
        )
        assert isinstance(result, dict)
        assert "script" in result
        assert isinstance(result["script"], str)
        assert len(result["script"]) > 0

    def test_mock_contract_has_verify_function(self):
        """Generated script contains a 'def verify_circuit(ctx)' function."""
        result = self.client.generate_test_contract(
            {"name": "test_lamp", "description": ""},
            _lamp_blocks(),
        )
        assert "def verify_circuit(ctx)" in result["script"]

    def test_mock_contract_lamp_pattern(self):
        """name='test_lamp' → script references redstone_lamp."""
        result = self.client.generate_test_contract(
            {"name": "test_lamp", "description": ""},
            _lamp_blocks(),
        )
        assert "redstone_lamp" in result["script"]

    def test_mock_contract_hopper_pattern(self):
        """name='test_hopper' → script references hopper."""
        hopper_blocks = [
            _make_block(0, 2, 0, "minecraft:chest[facing=west]"),
            _make_block(0, 1, 0, "minecraft:hopper[facing=down]"),
            _make_block(0, 0, 0, "minecraft:chest[facing=west]"),
        ]
        result = self.client.generate_test_contract(
            {"name": "test_hopper", "description": ""},
            hopper_blocks,
        )
        assert "hopper" in result["script"]

    def test_mock_contract_door_pattern(self):
        """name='test_door' → script references stone/piston pattern."""
        door_blocks = [
            _make_block(1, 1, 1, "minecraft:stone"),
            _make_block(1, 2, 1, "minecraft:stone"),
        ]
        result = self.client.generate_test_contract(
            {"name": "test_door", "description": ""},
            door_blocks,
        )
        assert "stone" in result["script"]


# =========================================================================
# Mock Deconstruction Tests
# =========================================================================

class TestMockDeconstruction:
    """Tests for TeacherClient.suggest_deconstruction_layer in mock mode."""

    def setup_method(self):
        self.client = TeacherClient(mock_mode=True)

    def test_mock_deconstruction_removes_top_layer(self):
        """Blocks at highest Y are in remove_blocks."""
        blocks = _multi_layer_blocks()
        result = self.client.suggest_deconstruction_layer(blocks)
        remove_blocks = result["response"]["remove_blocks"]
        # Highest Y is 2 (the lamp)
        assert [0, 2, 0] in remove_blocks, (
            f"Expected [0,2,0] in remove_blocks, got {remove_blocks}"
        )

    def test_mock_deconstruction_keeps_lower(self):
        """Blocks at lower Y are NOT in remove_blocks."""
        blocks = _multi_layer_blocks()
        result = self.client.suggest_deconstruction_layer(blocks)
        remove_blocks = result["response"]["remove_blocks"]
        # Y=0 (stone) should NOT be removed (Y=2 is highest)
        assert [0, 0, 0] not in remove_blocks, (
            f"Unexpected [0,0,0] in remove_blocks: {remove_blocks}"
        )

    def test_mock_deconstruction_reasoning(self):
        """Response contains 'reasoning' string."""
        blocks = _lamp_blocks()
        result = self.client.suggest_deconstruction_layer(blocks)
        assert "reasoning" in result["response"]
        assert isinstance(result["response"]["reasoning"], str)
        assert len(result["response"]["reasoning"]) > 0

    def test_mock_deconstruction_remove_blocks_format(self):
        """remove_blocks is a list of [x,y,z] lists."""
        blocks = _lamp_blocks()
        result = self.client.suggest_deconstruction_layer(blocks)
        remove_blocks = result["response"]["remove_blocks"]
        assert isinstance(remove_blocks, list)
        if remove_blocks:
            assert isinstance(remove_blocks[0], list)
            assert len(remove_blocks[0]) == 3

    def test_mock_deconstruction_empty(self):
        """Empty blocks list → empty remove_blocks."""
        result = self.client.suggest_deconstruction_layer([])
        assert result["response"]["remove_blocks"] == []
        assert "already empty" in result["response"]["reasoning"].lower()


# =========================================================================
# Prompt Structure Tests
# =========================================================================

class TestPromptStructure:
    """Tests that the prompt structure is correct."""

    def setup_method(self):
        self.client = TeacherClient(mock_mode=True)

    def test_contract_prompt_structure(self):
        """Contract result has prompt.system and prompt.user."""
        result = self.client.generate_test_contract(
            {"name": "test", "description": ""},
            _lamp_blocks(),
        )
        assert "prompt" in result
        assert "system" in result["prompt"]
        assert "user" in result["prompt"]
        assert isinstance(result["prompt"]["system"], str)
        assert isinstance(result["prompt"]["user"], str)

    def test_deconstruction_prompt_structure(self):
        """Deconstruction result has prompt.system and prompt.user."""
        result = self.client.suggest_deconstruction_layer(_lamp_blocks())
        assert "prompt" in result
        assert "system" in result["prompt"]
        assert "user" in result["prompt"]
        assert isinstance(result["prompt"]["system"], str)
        assert isinstance(result["prompt"]["user"], str)


# =========================================================================
# Mock Mode Guarantee
# =========================================================================

class TestMockModeGuarantee:
    """Verify that mock_mode=True never makes an LLM call."""

    def test_mock_mode_no_llm_call_contract(self):
        """llm_client.complete is never called when mock_mode=True for contracts."""
        mock_llm = MagicMock()
        client = TeacherClient(llm_client=mock_llm, mock_mode=True)

        client.generate_test_contract(
            {"name": "test_lamp", "description": ""},
            _lamp_blocks(),
        )
        mock_llm.complete.assert_not_called()

    def test_mock_mode_no_llm_call_deconstruction(self):
        """llm_client is never called when mock_mode=True for deconstruction."""
        mock_llm = MagicMock()
        client = TeacherClient(llm_client=mock_llm, mock_mode=True)

        client.suggest_deconstruction_layer(_lamp_blocks())
        mock_llm.complete.assert_not_called()


# =========================================================================
# Full Enrichment Pipeline Integration Test
# =========================================================================

class TestFullEnrichmentPipeline:
    """
    Simulate the full enrichment pipeline with mock data.
    Takes a hardcoded block list, runs through TeacherClient for contract +
    deconstruction, and verifies output structure matches expected schema.
    """

    PIPELINE_BLOCKS = [
        _make_block(0, 0, 0, "minecraft:stone"),
        _make_block(1, 0, 0, "minecraft:stone"),
        _make_block(0, 1, 0, "minecraft:redstone_wire"),
        _make_block(1, 1, 0, "minecraft:redstone_wire"),
        _make_block(0, 2, 0, "minecraft:redstone_lamp[lit=false]"),
    ]
    PIPELINE_META = {"name": "test_lamp", "description": "Test enrichment pipeline"}

    def setup_method(self):
        self.client = TeacherClient(mock_mode=True)

    def test_full_enrichment_pipeline_mock(self):
        """
        Run both contract generation and deconstruction with mock data,
        verify the full output structure matches expected schemas.
        """
        # --- Phase 1: Contract generation ---
        contract = self.client.generate_test_contract(
            self.PIPELINE_META, self.PIPELINE_BLOCKS
        )

        # Validate contract structure
        assert "script" in contract, "Contract missing 'script'"
        assert "prompt" in contract, "Contract missing 'prompt'"
        assert "system" in contract["prompt"], "Contract prompt missing 'system'"
        assert "user" in contract["prompt"], "Contract prompt missing 'user'"

        script = contract["script"]
        assert "def verify_circuit(ctx)" in script, (
            "Script missing verify_circuit function"
        )

        # The script must use at least one of the ctx API methods
        ctx_usage = sum(
            1 for method in ["ctx.assert_block", "ctx.set_block", "ctx.tick"]
            if method in script
        )
        assert ctx_usage >= 1, (
            f"Script doesn't use any ctx API methods: {script[:200]}"
        )

        # --- Phase 2: Deconstruction ---
        decon = self.client.suggest_deconstruction_layer(self.PIPELINE_BLOCKS)

        # Validate deconstruction structure
        assert "response" in decon, "Deconstruction missing 'response'"
        assert "prompt" in decon, "Deconstruction missing 'prompt'"
        assert "system" in decon["prompt"], "Decon prompt missing 'system'"
        assert "user" in decon["prompt"], "Decon prompt missing 'user'"

        response = decon["response"]
        assert "reasoning" in response, "Decon response missing 'reasoning'"
        assert "remove_blocks" in response, "Decon response missing 'remove_blocks'"

        remove_blocks = response["remove_blocks"]
        assert isinstance(remove_blocks, list), "remove_blocks should be a list"

        # Verify remove_blocks format: each entry must be [x, y, z]
        for entry in remove_blocks:
            assert isinstance(entry, list), f"remove_block entry not a list: {entry}"
            assert len(entry) == 3, f"remove_block entry wrong length: {entry}"
            assert all(isinstance(v, int) for v in entry), (
                f"remove_block coords not int: {entry}"
            )

        # With mock heuristic, highest Y blocks should be in remove_blocks
        assert [0, 2, 0] in remove_blocks, (
            f"Expected [0,2,0] in remove_blocks (highest Y), got {remove_blocks}"
        )

        # --- Phase 3: Sequential deconstruction (simulate progressive stripping) ---
        remaining = list(self.PIPELINE_BLOCKS)
        decon_steps = []

        for step in range(4):
            step_result = self.client.suggest_deconstruction_layer(remaining)
            step_remove = step_result["response"]["remove_blocks"]

            decon_steps.append({
                "step": step,
                "remaining_before": len(remaining),
                "remove_count": len(step_remove),
                "reasoning": step_result["response"]["reasoning"],
            })

            # Remove the suggested blocks from remaining
            remove_set = set(tuple(b) for b in step_remove)
            remaining = [b for b in remaining if tuple(b[:3]) not in remove_set]

            if not step_remove:
                break

        # After enough deconstruction steps, remaining should shrink
        assert len(decon_steps) > 1, "Should have at least 2 deconstruction steps"
        assert decon_steps[-1]["remaining_before"] <= len(self.PIPELINE_BLOCKS), (
            "Remaining block count should not increase"
        )

        # Verify each step had reasoning
        for step in decon_steps:
            assert len(step["reasoning"]) > 0, (
                f"Step {step['step']} missing reasoning"
            )
