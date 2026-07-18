"""
Tests for the full deconstruction pipeline (ReverseDeconstructor + TeacherClient mock).

No server needed, no API calls — TeacherClient operates in mock_mode=True, which
uses a Y-layer heuristic: always removes the highest-Y blocks first.
"""
import copy
import pytest
from simulation.deconstructor import ReverseDeconstructor
from simulation.teacher_client import TeacherClient
from simulation.dataset_generator import ReverseDatasetGenerator


# =========================================================================
# Helpers
# =========================================================================

def _make_block(x, y, z, state="minecraft:stone", nbt=None):
    """Create a (x, y, z, state, nbt) tuple matching deconstructor input."""
    return (x, y, z, state, nbt)


def _simple_3layer():
    """Three blocks at Y=0, Y=1, Y=2 — one per layer."""
    return [
        _make_block(0, 0, 0, "minecraft:stone"),
        _make_block(0, 1, 0, "minecraft:redstone_wire"),
        _make_block(0, 2, 0, "minecraft:redstone_lamp[lit=false]"),
    ]


def _single_block():
    return [_make_block(0, 0, 0, "minecraft:stone")]


def _wide_circuit(n=25):
    """Generate n blocks all at Y=0 (single layer)."""
    return [_make_block(i, 0, 0, "minecraft:stone") for i in range(n)]


def _multi_layer_blocks():
    """Blocks at multiple Y levels for layer tests."""
    return [
        _make_block(0, 0, 0, "minecraft:stone"),
        _make_block(1, 0, 0, "minecraft:glass"),
        _make_block(0, 1, 0, "minecraft:redstone_wire"),
        _make_block(1, 1, 0, "minecraft:redstone_wire"),
        _make_block(0, 2, 0, "minecraft:redstone_lamp[lit=false]"),
    ]


# =========================================================================
# Basic Deconstruction Tests
# =========================================================================

class TestBasicDeconstruction:
    """Fundamental deconstruction behaviour."""

    def setup_method(self):
        self.decon = ReverseDeconstructor(TeacherClient(mock_mode=True))

    def test_3block_3steps(self):
        """3 blocks at Y=0,1,2 → deconstructs to empty in 3 steps."""
        steps = self.decon.plan(_simple_3layer())
        assert len(steps) == 3
        assert steps[-1]["remaining_count"] == 0

    def test_multi_layer_removes_top_first(self):
        """Blocks at Y=0,1,2 → step 0 removes Y=2 (top layer)."""
        blocks = _multi_layer_blocks()
        steps = self.decon.plan(blocks)
        # Step 0 should remove the highest Y blocks (Y=2)
        step0_removed = steps[0]["removed_blocks"]
        removed_ys = [b["pos"][1] for b in step0_removed]
        assert all(y == 2 for y in removed_ys), (
            f"Step 0 should remove Y=2 blocks, got Ys={removed_ys}"
        )

    def test_single_block(self):
        """1 block → deconstructs in 1 step."""
        steps = self.decon.plan(_single_block())
        assert len(steps) == 1
        assert steps[0]["remaining_count"] == 0

    def test_empty_blocks(self):
        """Empty list → deconstructs in 0 steps."""
        steps = self.decon.plan([])
        assert len(steps) == 0

    def test_large_circuit(self):
        """20+ blocks → fully deconstructs within iteration budget."""
        blocks = _wide_circuit(25)
        steps = self.decon.plan(blocks)
        assert len(steps) > 0
        assert steps[-1]["remaining_count"] == 0


# =========================================================================
# Step Structure Tests
# =========================================================================

class TestStepStructure:
    """Each step in the deconstruction output has the required fields."""

    def setup_method(self):
        self.decon = ReverseDeconstructor(TeacherClient(mock_mode=True))

    def test_step_has_all_fields(self):
        """Each step has step, reasoning, removed_blocks, remaining_count, prompt, snapshot_after."""
        steps = self.decon.plan(_simple_3layer())
        for step in steps:
            assert "step" in step
            assert "reasoning" in step
            assert "removed_blocks" in step
            assert "remaining_count" in step
            assert "prompt" in step
            assert "snapshot_after" in step

    def test_step_index_matches(self):
        """step_index matches the iteration position."""
        steps = self.decon.plan(_simple_3layer())
        for i, step in enumerate(steps):
            assert step["step"] == i

    def test_removed_blocks_format(self):
        """Each removed_block has pos [x,y,z], state, nbt."""
        steps = self.decon.plan(_simple_3layer())
        for step in steps:
            for rb in step["removed_blocks"]:
                assert "pos" in rb
                assert "state" in rb
                assert "nbt" in rb
                assert isinstance(rb["pos"], list)
                assert len(rb["pos"]) == 3
                assert all(isinstance(v, int) for v in rb["pos"])
                assert isinstance(rb["state"], str)

    def test_snapshot_after_format(self):
        """snapshot_after is list of {pos, state} sorted by (y, x, z)."""
        steps = self.decon.plan(_multi_layer_blocks())
        for step in steps:
            snap = step["snapshot_after"]
            assert isinstance(snap, list)
            for entry in snap:
                assert "pos" in entry
                assert "state" in entry
                assert isinstance(entry["pos"], list)
                assert len(entry["pos"]) == 3
            # Verify sorted by (y, x, z) — the sort key used in _serialize_snapshot
            for i in range(len(snap) - 1):
                p1 = tuple(snap[i]["pos"])
                p2 = tuple(snap[i + 1]["pos"])
                sort_key1 = (p1[1], p1[0], p1[2])  # (y, x, z)
                sort_key2 = (p2[1], p2[0], p2[2])
                assert sort_key1 <= sort_key2, f"Snapshot not sorted by (y,x,z): {p1} then {p2}"


# =========================================================================
# Iteration Budget Tests
# =========================================================================

class TestIterationBudget:
    """Deconstructor respects the iteration budget."""

    def setup_method(self):
        self.decon = ReverseDeconstructor(TeacherClient(mock_mode=True))

    def test_budget_not_exceeded(self):
        """max_iters = len(blocks) + 5, actual iterations <= that."""
        blocks = _wide_circuit(20)
        steps = self.decon.plan(blocks)
        max_budget = len(blocks) + 5
        assert len(steps) <= max_budget, (
            f"Steps {len(steps)} exceeded budget {max_budget}"
        )

    def test_convergence(self):
        """Deconstruction always reaches remaining_count == 0."""
        blocks = _wide_circuit(30)
        steps = self.decon.plan(blocks)
        assert steps[-1]["remaining_count"] == 0

    def test_progressive_stripping(self):
        """Each step removes at least 1 block."""
        blocks = _multi_layer_blocks()
        steps = self.decon.plan(blocks)
        for step in steps:
            assert len(step["removed_blocks"]) >= 1, (
                f"Step {step['step']} removed 0 blocks"
            )


# =========================================================================
# Fallback Behaviour Tests
# =========================================================================

class TestFallbackBehaviour:
    """When the teacher suggests invalid blocks, deconstructor falls back."""

    def test_fallback_on_invalid_suggestion(self):
        """
        When teacher suggests blocks not in remaining, fallback removes highest Y.
        We simulate this by providing a teacher that always suggests a non-existent block.
        """
        blocks = _simple_3layer()
        teacher = TeacherClient(mock_mode=True)
        # Monkey-patch the mock to always return a block NOT in the list
        original_suggest = teacher.suggest_deconstruction_layer

        def patched_suggest(current_blocks, iteration=0):
            result = original_suggest(current_blocks, iteration)
            # Replace remove_blocks with a position that doesn't exist
            result["response"]["remove_blocks"] = [[999, 999, 999]]
            return result

        teacher.suggest_deconstruction_layer = patched_suggest
        decon = ReverseDeconstructor(teacher)
        steps = decon.plan(blocks)

        # Fallback should kick in and remove valid blocks
        assert len(steps) == 3
        assert steps[-1]["remaining_count"] == 0
        # Each step should have one removed block (the fallback)
        for step in steps:
            assert len(step["removed_blocks"]) == 1
            # The fallback reasoning should contain "Fallback"
            assert "fallback" in step["reasoning"].lower()


# =========================================================================
# Reasoning and Prompt Tests
# =========================================================================

class TestReasoningAndPrompts:
    """Every step must have reasoning and prompt data."""

    def setup_method(self):
        self.decon = ReverseDeconstructor(TeacherClient(mock_mode=True))

    def test_reasoning_present(self):
        """Every step has non-empty reasoning string."""
        steps = self.decon.plan(_multi_layer_blocks())
        for step in steps:
            assert isinstance(step["reasoning"], str)
            assert len(step["reasoning"]) > 0

    def test_prompt_has_system_and_user(self):
        """Every step has prompt with system and user strings."""
        steps = self.decon.plan(_simple_3layer())
        for step in steps:
            assert "prompt" in step
            assert "system" in step["prompt"]
            assert "user" in step["prompt"]
            assert isinstance(step["prompt"]["system"], str)
            assert isinstance(step["prompt"]["user"], str)
            assert len(step["prompt"]["system"]) > 0
            assert len(step["prompt"]["user"]) > 0


# =========================================================================
# Build Steps Derivation Tests
# =========================================================================

class TestBuildStepsDerivation:
    """ReverseDatasetGenerator._derive_build_steps reverses deconstruction."""

    def setup_method(self):
        self.decon = ReverseDeconstructor(TeacherClient(mock_mode=True))
        self.generator = ReverseDatasetGenerator()
        # Override the deconstructor on the generator to use our instance
        self.generator.deconstructor = self.decon

    def test_build_steps_reversed(self):
        """Build steps are the reverse of deconstruction steps."""
        blocks = _simple_3layer()
        decon_steps = self.decon.plan(blocks)
        build_steps = self.generator._derive_build_steps(decon_steps)

        # Number of build steps should match non-empty deconstruction steps
        non_empty = [s for s in decon_steps if s["removed_blocks"]]
        assert len(build_steps) == len(non_empty)

        # First build step should place what was removed in last deconstruction step
        if len(decon_steps) >= 2:
            first_build = build_steps[0]
            last_decon = decon_steps[-1]
            assert first_build["blocks_to_place"] == last_decon["removed_blocks"]

    def test_build_steps_have_required_fields(self):
        """Each build step has stage, instruction, blocks_to_place, source_reasoning."""
        blocks = _simple_3layer()
        decon_steps = self.decon.plan(blocks)
        build_steps = self.generator._derive_build_steps(decon_steps)

        for step in build_steps:
            assert "stage" in step
            assert "instruction" in step
            assert "blocks_to_place" in step
            assert "source_reasoning" in step
            assert isinstance(step["stage"], int)
            assert isinstance(step["instruction"], str)
            assert isinstance(step["blocks_to_place"], list)

    def test_build_stages_sequential(self):
        """Build stages are numbered 0, 1, 2, ..."""
        blocks = _multi_layer_blocks()
        decon_steps = self.decon.plan(blocks)
        build_steps = self.generator._derive_build_steps(decon_steps)

        for i, step in enumerate(build_steps):
            assert step["stage"] == i

    def test_empty_deconstruction_yields_empty_build(self):
        """Empty deconstruction steps → empty build steps."""
        build_steps = self.generator._derive_build_steps([])
        assert build_steps == []

    def test_all_blocks_recovered_in_build(self):
        """Total blocks in build steps equals original block count."""
        blocks = _multi_layer_blocks()
        decon_steps = self.decon.plan(blocks)
        build_steps = self.generator._derive_build_steps(decon_steps)

        total_build_blocks = sum(
            len(step["blocks_to_place"]) for step in build_steps
        )
        assert total_build_blocks == len(blocks)


# =========================================================================
# Edge Cases
# =========================================================================

class TestEdgeCases:
    """Edge cases for deconstruction."""

    def test_single_layer_circuit(self):
        """All blocks at same Y → removed in one step."""
        blocks = [
            _make_block(0, 0, 0, "minecraft:stone"),
            _make_block(1, 0, 0, "minecraft:glass"),
            _make_block(2, 0, 0, "minecraft:dirt"),
        ]
        decon = ReverseDeconstructor(TeacherClient(mock_mode=True))
        steps = decon.plan(blocks)
        assert len(steps) == 1
        assert steps[0]["remaining_count"] == 0
        assert len(steps[0]["removed_blocks"]) == 3

    def test_all_same_y_same_x(self):
        """Blocks stacked at same (x, z) with varying Y → removed one Y-level per step."""
        blocks = [
            _make_block(0, 0, 0, "minecraft:stone"),
            _make_block(0, 1, 0, "minecraft:glass"),
            _make_block(0, 2, 0, "minecraft:dirt"),
        ]
        decon = ReverseDeconstructor(TeacherClient(mock_mode=True))
        steps = decon.plan(blocks)
        assert len(steps) == 3
        # Each step removes exactly 1 block
        for step in steps:
            assert len(step["removed_blocks"]) == 1

    def test_nbt_preserved(self):
        """NBT data is preserved in removed_blocks."""
        blocks = [
            _make_block(0, 0, 0, "minecraft:chest[facing=west]", "{Items:[]}"),
        ]
        decon = ReverseDeconstructor(TeacherClient(mock_mode=True))
        steps = decon.plan(blocks)
        assert len(steps) == 1
        removed = steps[0]["removed_blocks"][0]
        assert removed["nbt"] == "{Items:[]}"

    def test_remaining_count_decreases(self):
        """Remaining count strictly decreases each step."""
        blocks = _multi_layer_blocks()
        decon = ReverseDeconstructor(TeacherClient(mock_mode=True))
        steps = decon.plan(blocks)
        prev_remaining = len(blocks)
        for step in steps:
            assert step["remaining_count"] < prev_remaining
            prev_remaining = step["remaining_count"]
