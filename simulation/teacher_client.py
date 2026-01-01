import json
import textwrap
from typing import Any, Dict, List, Optional, Tuple


class TeacherClient:
    """
    Phase 4 LLM Interface for:
      1. Verification contract generation.
      2. Reverse deconstruction planning.
    """

    CONTRACT_SYSTEM_PROMPT = textwrap.dedent(
        """\
        [SYSTEM PROMPT]

        You are an expert Minecraft Redstone Engineer.

        Your goal is to write a Python verification script for a Redstone circuit using the 'MIRA_API'.**The MIRA Verification API:**

        1. `ctx.set_block(pos, block_state)`: Places blocks/levers.

        2. `ctx.tick(ticks)`: Advances the game simulation.

        3. `ctx.assert_block(pos, block_id)`: Checks if a block matches the ID (throws AssertionError if not).

        4. `ctx.assert_power(pos, min_level)`: Checks redstone power level.**Instructions:**

        1. Analyze the provided Block List.

        2. Identify the **INPUT** (Levers, Buttons, Observers) and the **OUTPUT** (Pistons, Lamps, Doors).

        3. Write a `verify_circuit(ctx)` function that:   - Asserts the initial state (e.g., Door is Closed).   - Triggers the Input (e.g., Flicks Lever).   - Ticks the engine (allow 10-20 ticks for propagation).   - Asserts the final state (e.g., Door is Open).4. Output **ONLY** the Python code. No markdown, no explanations.
        """
    )

    DECONSTRUCTOR_SYSTEM_PROMPT = textwrap.dedent(
        """\
        [SYSTEM PROMPT]

        You are a Reverse-Engineering Architect.

        You are given a list of blocks representing a Minecraft Redstone Machine.**Your Goal:**

        Identify a "Logical Layer" of blocks that can be **REMOVED** to return the machine to a previous, simpler state.

        We are simulating the construction process in reverse.**Rules for Removal:**1. **Remove Output/Decoration First:** Frames, lamps, and final pushed blocks are usually the last things added.2. **Remove Control Wiring Second:** Redstone dust, levers, and buttons are usually added after the machinery.3. **Remove Core Mechanisms Last:** Pistons, observers, and droppers are usually the "Skeleton" placed first.4. **Do not break dependency chains:** If a Repeater sits on a Stone block, do not remove the Stone block while the Repeater is still there. Remove the Repeater first.**Output Format:**

        Return a JSON object:

        {

          "reasoning": "Explaining why this layer is the next logical step to remove.",

          "remove_blocks": [ [x,y,z], [x,y,z] ... ]

        }
        """
    )

    def __init__(self, llm_client: Optional[Any] = None, mock_mode: bool = True):
        self.llm_client = llm_client
        self.mock_mode = mock_mode

    # ------------------------------------------------------------------ #
    # Contract Generation
    # ------------------------------------------------------------------ #
    def generate_test_contract(
        self, meta: Dict[str, Any], blocks: List[Tuple[int, int, int, str, Any]]
    ) -> Dict[str, Any]:
        system_prompt = self.CONTRACT_SYSTEM_PROMPT.strip()
        user_prompt = self._build_contract_user_prompt(meta, blocks)

        if self.mock_mode or self.llm_client is None:
            script = self._mock_contract_response(meta, blocks)
        else:
            script = self.llm_client.complete(system_prompt=system_prompt, user_prompt=user_prompt)

        return {
            "prompt": {
                "system": system_prompt,
                "user": user_prompt,
            },
            "script": script.strip(),
        }

    def _build_contract_user_prompt(
        self, meta: Dict[str, Any], blocks: List[Tuple[int, int, int, str, Any]]
    ) -> str:
        name = meta.get("name", "Unknown Schematic")
        desc = meta.get("description", "No description provided.")

        lines = [
            "[USER PROMPT]",
            "[METADATA]",
            f"Name: {name}",
            f"Description: {desc}",
            "",
            "[BLOCK_LIST]",
            "# Relative Coordinates (x,y,z)",
        ]

        for x, y, z, state, _ in sorted(blocks, key=lambda b: (b[1], b[0], b[2])):
            lines.append(f"({x}, {y}, {z}): {state}")

        lines.append("")
        lines.append("[TASK]")
        lines.append("Write the `verify_circuit(ctx)` function.")

        return "\n".join(lines)

    def _mock_contract_response(
        self, meta: Dict[str, Any], blocks: List[Tuple[int, int, int, str, Any]]
    ) -> str:
        name = (meta.get("name") or "").lower()

        if "lamp" in name:
            return textwrap.dedent(
                """\
                def verify_circuit(ctx):
                    ctx.assert_block((2, 1, 0), "minecraft:redstone_lamp[lit=false]")
                    ctx.set_block((0, 1, 0), "minecraft:lever[face=floor,facing=east,powered=true]")
                    ctx.tick(12)
                    ctx.assert_block((2, 1, 0), "minecraft:redstone_lamp[lit=true]")
                """
            )

        if "hopper" in name:
            return textwrap.dedent(
                """\
                def verify_circuit(ctx):
                    ctx.assert_block((0, 2, 0), "minecraft:chest[facing=west]")
                    ctx.assert_block((0, 1, 0), "minecraft:hopper[facing=down]")
                    ctx.assert_block((0, 0, 0), "minecraft:chest[facing=west]")
                """
            )

        if "door" in name or "piston" in name:
            return textwrap.dedent(
                """\
                def verify_circuit(ctx):
                    ctx.assert_block((1, 1, 1), "minecraft:stone")
                    ctx.assert_block((1, 2, 1), "minecraft:stone")
                    ctx.set_block((2, 4, 1), "minecraft:lever[face=floor,facing=north,powered=true]")
                    ctx.tick(15)
                    ctx.assert_block((2, 1, 1), "minecraft:stone")
                """
            )

        return textwrap.dedent(
            """\
            def verify_circuit(ctx):
                ctx.tick(5)
            """
        )

    # ------------------------------------------------------------------ #
    # Deconstruction
    # ------------------------------------------------------------------ #
    def suggest_deconstruction_layer(
        self, blocks: List[Tuple[int, int, int, str, Any]], iteration: int = 0
    ) -> Dict[str, Any]:
        """
        Suggests blocks for removal based on logical layers.
        
        In production, this interface connects to an LLM to generate engineering-grade
        reasoning. In the architectural stage, it utilizes a Y-layer heuristic to
        demonstrate pipeline functionality.
        """
        system_prompt = self.DECONSTRUCTOR_SYSTEM_PROMPT.strip()
        user_prompt = self._build_deconstruction_user_prompt(blocks)

        if self.mock_mode or self.llm_client is None:
            # Heuristic: Remove the entire top-most layer (highest Y)
            if not blocks:
                response = {"reasoning": "Structure already empty.", "remove_blocks": []}
            else:
                highest_y = max(b[1] for b in blocks)
                layer_blocks = [list(b[:3]) for b in blocks if b[1] == highest_y]
                response = {
                    "reasoning": f"[HEURISTIC] Removing all blocks at Y={highest_y} to simulate layer-by-layer deconstruction.",
                    "remove_blocks": layer_blocks
                }
        else:
            raw = self.llm_client.complete(system_prompt=system_prompt, user_prompt=user_prompt)
            response = json.loads(raw)

        return {
            "prompt": {
                "system": system_prompt,
                "user": user_prompt,
            },
            "response": response,
        }

    def _build_deconstruction_user_prompt(
        self, blocks: List[Tuple[int, int, int, str, Any]]
    ) -> str:
        lines = ["[USER PROMPT]", "[CURRENT_STATE]"]

        for x, y, z, state, _ in sorted(blocks, key=lambda b: (b[1], b[0], b[2])):
            lines.append(f"({x}, {y}, {z}): {state}")

        lines.append("")
        lines.append("[TASK]")
        lines.append('Identify the next set of blocks to delete to strip this down to the "Skeleton".')

        return "\n".join(lines)
