"""
Mock-based unit tests for simulation/teacher_client.py TeacherClient.
Tests mock-mode behaviour — no LLM API calls.
"""

import pytest
from unittest.mock import MagicMock


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def teacher():
    """TeacherClient in default mock mode."""
    from simulation.teacher_client import TeacherClient

    return TeacherClient()


@pytest.fixture
def lamp_meta():
    return {"name": "lamp_circuit", "description": "A simple lamp circuit"}


@pytest.fixture
def hopper_meta():
    return {"name": "hopper_sorter", "description": "Hopper sorting system"}


@pytest.fixture
def door_meta():
    return {"name": "iron_door", "description": "Iron door with button"}


@pytest.fixture
def piston_meta():
    return {"name": "piston_extender", "description": "A piston extender"}


@pytest.fixture
def generic_meta():
    return {"name": "random_gadget", "description": "Some redstone gadget"}


@pytest.fixture
def simple_blocks():
    return [(0, 0, 0, "minecraft:stone", None)]


@pytest.fixture
def multi_level_blocks():
    """Blocks at Y=0, Y=1, Y=2 for deconstruction tests."""
    return [
        (0, 0, 0, "minecraft:stone", None),
        (1, 1, 0, "minecraft:redstone_wire", None),
        (2, 2, 0, "minecraft:glass", None),
    ]


# ================================================================= #
# Mock mode default
# ================================================================= #


class TestMockModeDefault:
    """TeacherClient defaults."""

    def test_mock_mode_default(self):
        """mock_mode is True by default."""
        from simulation.teacher_client import TeacherClient

        tc = TeacherClient()
        assert tc.mock_mode is True

    def test_llm_client_accepts_mock_mode_false(self):
        """Can explicitly set mock_mode to False."""
        from simulation.teacher_client import TeacherClient

        tc = TeacherClient(mock_mode=False)
        assert tc.mock_mode is False


# ================================================================= #
# Contract generation — content
# ================================================================= #


class TestGenerateContractContent:
    """Verify mock contract responses contain expected content."""

    def test_generate_contract_lamp(self, teacher, lamp_meta, simple_blocks):
        """name contains 'lamp' → script has redstone_lamp assertions."""
        result = teacher.generate_test_contract(lamp_meta, simple_blocks)
        script = result["script"]
        assert "redstone_lamp" in script
        assert "def verify_circuit(ctx)" in script

    def test_generate_contract_hopper(self, teacher, hopper_meta, simple_blocks):
        """name contains 'hopper' → script has hopper/chest assertions."""
        result = teacher.generate_test_contract(hopper_meta, simple_blocks)
        script = result["script"]
        assert "hopper" in script
        assert "chest" in script
        assert "def verify_circuit(ctx)" in script

    def test_generate_contract_door(self, teacher, door_meta, simple_blocks):
        """name contains 'door' → script has door/piston assertions."""
        result = teacher.generate_test_contract(door_meta, simple_blocks)
        script = result["script"]
        assert "def verify_circuit(ctx)" in script
        # The door/piston script uses stone and lever assertions
        assert "lever" in script or "stone" in script

    def test_generate_contract_piston(self, teacher, piston_meta, simple_blocks):
        """name contains 'piston' → shared door/piston script."""
        result = teacher.generate_test_contract(piston_meta, simple_blocks)
        script = result["script"]
        assert "def verify_circuit(ctx)" in script
        assert "lever" in script

    def test_generate_contract_generic(self, teacher, generic_meta, simple_blocks):
        """unknown name → generic script with ctx.tick(5)."""
        result = teacher.generate_test_contract(generic_meta, simple_blocks)
        script = result["script"]
        assert "ctx.tick(5)" in script
        assert "def verify_circuit(ctx)" in script


# ================================================================= #
# Contract generation — structure
# ================================================================= #


class TestGenerateContractStructure:
    """Verify the structure of the returned dict."""

    def test_generate_contract_returns_prompt(self, teacher, lamp_meta, simple_blocks):
        """Result has 'prompt' with 'system' and 'user' keys."""
        result = teacher.generate_test_contract(lamp_meta, simple_blocks)
        assert "prompt" in result
        assert "system" in result["prompt"]
        assert "user" in result["prompt"]
        assert isinstance(result["prompt"]["system"], str)
        assert isinstance(result["prompt"]["user"], str)
        assert len(result["prompt"]["system"]) > 0
        assert len(result["prompt"]["user"]) > 0

    def test_generate_contract_returns_script(self, teacher, lamp_meta, simple_blocks):
        """Result has 'script' key with non-empty string."""
        result = teacher.generate_test_contract(lamp_meta, simple_blocks)
        assert "script" in result
        assert isinstance(result["script"], str)
        assert len(result["script"]) > 0

    def test_generate_contract_script_has_verify_function(
        self, teacher, lamp_meta, simple_blocks
    ):
        """Script contains 'def verify_circuit(ctx)'."""
        result = teacher.generate_test_contract(lamp_meta, simple_blocks)
        assert "def verify_circuit(ctx)" in result["script"]


# ================================================================= #
# Deconstruction — content
# ================================================================= #


class TestDeconstructionContent:
    """Verify mock deconstruction behaviour."""

    def test_deconstruction_removes_highest_y(
        self, teacher, multi_level_blocks
    ):
        """Blocks at highest Y are in remove_blocks."""
        result = teacher.suggest_deconstruction_layer(multi_level_blocks)
        remove_blocks = result["response"]["remove_blocks"]
        # Highest Y is 2 → block (2, 2, 0) should be listed
        assert [2, 2, 0] in remove_blocks

    def test_deconstruction_keeps_lower_blocks(
        self, teacher, multi_level_blocks
    ):
        """Blocks at lower Y are NOT in remove_blocks."""
        result = teacher.suggest_deconstruction_layer(multi_level_blocks)
        remove_blocks = result["response"]["remove_blocks"]
        # Y=0 and Y=1 should NOT be removed
        assert [0, 0, 0] not in remove_blocks
        assert [1, 1, 0] not in remove_blocks

    def test_deconstruction_empty_blocks(self, teacher):
        """Empty list → empty remove_blocks, 'already empty' reasoning."""
        result = teacher.suggest_deconstruction_layer([])
        response = result["response"]
        assert response["remove_blocks"] == []
        assert "already empty" in response["reasoning"].lower()

    def test_deconstruction_iteration_param(self, teacher, multi_level_blocks):
        """Function accepts iteration parameter."""
        result = teacher.suggest_deconstruction_layer(
            multi_level_blocks, iteration=3
        )
        assert "response" in result
        # Iteration doesn't change mock behaviour, but should not error


# ================================================================= #
# Deconstruction — structure
# ================================================================= #


class TestDeconstructionStructure:
    """Verify the structure of the returned dict."""

    def test_deconstruction_returns_prompt(
        self, teacher, multi_level_blocks
    ):
        """Result has 'prompt' with 'system' and 'user' keys."""
        result = teacher.suggest_deconstruction_layer(multi_level_blocks)
        assert "prompt" in result
        assert "system" in result["prompt"]
        assert "user" in result["prompt"]
        assert isinstance(result["prompt"]["system"], str)
        assert isinstance(result["prompt"]["user"], str)

    def test_deconstruction_response_format(
        self, teacher, multi_level_blocks
    ):
        """Response has 'reasoning' (str) and 'remove_blocks' (list of lists)."""
        result = teacher.suggest_deconstruction_layer(multi_level_blocks)
        response = result["response"]
        assert "reasoning" in response
        assert isinstance(response["reasoning"], str)
        assert "remove_blocks" in response
        assert isinstance(response["remove_blocks"], list)
        if response["remove_blocks"]:
            for coord in response["remove_blocks"]:
                assert isinstance(coord, list)
                assert len(coord) == 3


# ================================================================= #
# LLM client not used in mock mode
# ================================================================= #


class TestLLMClientNotCalled:
    """When mock_mode=True, llm_client is never called."""

    def test_llm_client_not_used_in_mock_mode(self):
        """llm_client.complete is never called in mock mode."""
        from simulation.teacher_client import TeacherClient

        llm_client = MagicMock()
        tc = TeacherClient(llm_client=llm_client, mock_mode=True)

        meta = {"name": "lamp", "description": "test"}
        blocks = [(0, 0, 0, "minecraft:stone", None)]

        tc.generate_test_contract(meta, blocks)
        tc.suggest_deconstruction_layer(blocks)

        llm_client.complete.assert_not_called()
