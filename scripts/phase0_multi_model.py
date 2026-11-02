#!/usr/bin/env python3
"""
Phase 0 (Enhanced): Multi-model measurement with LLM-generated verification.

Key innovations over phase0_measure_success_rate.py:
1. Circuit + verification spec generated in ONE call (model decides what to check)
2. Automatic negative checks: after model's test passes, toggle the power source
   and check the OPPOSITE state. If the test still passes, it's an unfair test
   (false positive — model wrote a test that always passes).
3. Multi-model support: test all OpenRouter models in the MODELS dict.
4. Optional iteration: if verification fails, send failure back to model for repair.

Usage:
    # Test all models on simple_lamp, 3 attempts each
    python3 scripts/phase0_multi_model.py --models all --circuits simple_lamp --attempts 3

    # Test specific models on specific circuits
    python3 scripts/phase0_multi_model.py --models deepseek-v4-flash,qwen3-coder-30b --circuits simple_lamp,piston_door --attempts 5

    # Dry run (LLM only, no MC building)
    python3 scripts/phase0_multi_model.py --models deepseek-v4-flash --circuits simple_lamp --dry-run

    # With iteration (send failure back to model for repair, up to N rounds)
    python3 scripts/phase0_multi_model.py --models deepseek-v4-flash --circuits simple_lamp --attempts 5 --iterate 2

Requires:
    - OPENROUTER_API_KEY env var
    - Running MC server on localhost:25575 (unless --dry-run)
"""

import argparse
import json
import os
import re
import sys
import time
import copy
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))

from simulation.llm_client import OpenRouterClient, ChatMessage
from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_blocks

# Import prompt variations
sys.path.insert(0, str(PROJECT_ROOT / "evaluation" / "prompts"))
from prompt_variations import PROMPT_VARIATIONS


# ============================================================================
# Configuration
# ============================================================================

CIRCUITS_DIR = PROJECT_ROOT / "evaluation" / "test_circuits"
DESCRIPTIONS_FILE = PROJECT_ROOT / "evaluation" / "prompts" / "circuit_descriptions.json"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results" / "phase0_multi_model"
TEMPERATURE = 0.5
MAX_TOKENS = 8192
ATTEMPTS_DEFAULT = 3
ORIGIN_BASE = (10000, 200, 10000)
REGION_SPACING = 100

# Models to test (subset of MODELS dict — the affordable + newest ones)
DEFAULT_MODELS = [
    "deepseek-v4-flash",      # $0.09/$0.19 — best value, SWE-bench 80.6%
    "gemma-4-31b",            # $0.12/$0.37 — LiveCodeBench 80.0%, Apache 2.0
    "gemma-4-26b",            # $0.07/$0.34 — MoE, near-31B quality
    "minimax-m3",             # $0.30/$1.20 — SWE-bench Pro 59.0%, open weights
    "qwen3.7-plus",           # $0.32/$1.28 — agentic coding
    "deepseek-v4-pro",        # $0.44/$0.87 — high quality
    "qwen3.6-plus",           # $0.33/$1.95 — SWE-bench 78.8%
    "glm-5.2",                # $0.94/$2.95 — SWE-bench Pro 62.1%, MIT
    "gemini-3.5-flash",       # $1.50/$9.00 — TB 2.1 76.2%
    "claude-sonnet-5",        # $2.00/$10.00 — SWE-bench Pro 63.2%
]


# ============================================================================
# Prompt Design — Circuit + Verification in One Call
# ============================================================================

SYSTEM_PROMPT = """You are a Minecraft redstone engineer. You design compact, functional redstone circuits and write verification specs for them.

You will be given a circuit description. You must output:
1. A `plan` — list of every block with x,y,z coordinates (relative to 0,0,0), block type, and reason
2. A `verification` spec — how to test that the circuit works

Verification spec format:
- `initial_checks`: list of blocks to check BEFORE any action (block must be in specified state)
- `actions`: list of actions to perform (set_block to toggle levers, place items, etc.)
- `tick_steps`: how many game ticks to wait after actions (usually 10-20)
- `final_checks`: list of blocks to check AFTER actions+ticks (block must be in specified state)

Each check is: {"position": "x,y,z", "expected_state": "minecraft:block[property=value]"}

IMPORTANT RULES:
1. Generate EXACTLY the requested number of blocks. Count carefully.
2. All positions are RELATIVE to (0,0,0). Use small coordinates (0-20 range).
3. Redstone wire, repeater, comparator, lever, button, pressure plate need a SOLID block underneath (stone, etc.)
4. Use valid Minecraft block IDs: minecraft:redstone_wire (not "redstone_dust"), minecraft:redstone_lamp (not "lamp"), etc.
5. Lever property: minecraft:lever[facing=north,powered=false]
6. Lamp property: minecraft:redstone_lamp[lit=false]
7. The verification must be FAIR — it should FAIL if the circuit is broken, and PASS only if the circuit works.
8. For toggle circuits (lever → lamp), the final_checks should verify the lamp is lit=true AFTER the lever is set to powered=true.
9. Do NOT write trivial tests like "check that stone exists at 0,0,0" — test the FUNCTIONAL behavior."""

USER_PROMPT_TEMPLATE = """Build this circuit:

{description}

Expected block count: {expected}

REQUIREMENTS:
- Generate EXACTLY {expected} blocks in the plan
- Number each step 1 to {expected}
- Include support blocks (stone under redstone components) in your count
- Write a verification spec that tests the circuit's FUNCTIONAL behavior
- The verification must FAIL if the circuit is broken (don't write trivial tests)

Circuit needs {expected} total blocks. Be complete and accurate."""

# System prompt for natural language descriptions (no block count constraint)
SYSTEM_PROMPT_DESCRIPTIONS = """You are a Minecraft redstone engineer. You design compact, functional redstone circuits and write verification specs for them.

You will be given a circuit description. You must output:
1. A `plan` — list of every block with x,y,z coordinates (relative to 0,0,0), block type, and reason
2. A `verification` spec — how to test that the circuit works

Verification spec format:
- `initial_checks`: list of blocks to check BEFORE any action (block must be in specified state)
- `actions`: list of actions to perform (set_block to toggle levers, place items, etc.)
- `tick_steps`: how many game ticks to wait after actions (use 20-30 for torch circuits)
- `final_checks`: list of blocks to check AFTER actions+ticks (block must be in specified state)

Each check is: {"position": "x,y,z", "expected_state": "minecraft:block[property=value]"}

IMPORTANT RULES:
1. Generate as many blocks as needed to make the circuit work. Include support blocks (stone under redstone components).
2. All positions are RELATIVE to (0,0,0). Use small coordinates (0-20 range).
3. Redstone wire, repeater, comparator, lever, button, pressure plate need a SOLID block underneath (stone, etc.)
4. Use valid Minecraft block IDs: minecraft:redstone_wire (not "redstone_dust"), minecraft:redstone_lamp (not "lamp"), etc.
5. The verification must be FAIR — it should FAIL if the circuit is broken, and PASS only if the circuit works.
6. Do NOT write trivial tests like "check that stone exists at 0,0,0" — test the FUNCTIONAL behavior.

RESTING STATE RULES (critical for initial_checks):
- The circuit is built in its RESTING state — levers are powered=false (OFF), buttons are unpressed.
- A lamp directly adjacent to a redstone_block is lit=true (always powered).
- A lamp adjacent to an unpowered lever (powered=false) is lit=false.
- A lamp powered by a redstone_torch is lit=true (torch is on when its input is off).
- In a NOT gate: lever OFF → torch ON → lamp lit=true. (Initial state has lamp ON.)
- In a lever→lamp circuit: lever OFF → lamp lit=false. (Initial state has lamp OFF.)
- Do NOT check the `facing` property on levers in verification — the server determines facing from attachment context. Only check `powered` on levers: minecraft:lever[powered=false]
- For torch circuits, use tick_steps of 20-30 to allow signal propagation through torches.

ACTION RULES:
- To toggle a lever: {"type": "set_block", "position": "x,y,z", "state": "minecraft:lever[powered=true]"}
- To place an item in a container: {"type": "place_item", "position": "x,y,z", "state": "minecraft:diamond 1"}
- The state in actions should match what you want AFTER the action (e.g., powered=true to turn a lever ON).

LEVER PLACEMENT RULES (critical):
- ALWAYS specify lever facing: minecraft:lever[facing=north,powered=false]
- facing=north means lever is on the south wall, attached to block at (x,y,z+1), powers block at (x,y,z-1) to the north
- facing=south means lever is on the north wall, attached to block at (x,y,z-1), powers block at (x,y,z+1) to the south
- facing=east means lever is on the west wall, attached to block at (x-1,y,z), powers block at (x+1,y,z) to the east
- facing=west means lever is on the east wall, attached to block at (x+1,y,z), powers block at (x-1,y,z) to the west
- The lever MUST have a solid block on the side it's attached to, or it will break
- Example: lever at (0,0,1) with facing=north is attached to block at (0,0,2) and powers wire at (0,0,0)

TICK STEPS:
- Use tick_steps of at least 15 for wire circuits (signal needs time to propagate)
- Use tick_steps of 20-30 for torch circuits (torches need more time to re-evaluate)
- Do NOT use tick_steps less than 10"""

USER_PROMPT_DESCRIPTIONS = """Build this circuit:

{description}

Verification behavior: {verification_behavior}

REQUIREMENTS:
- Generate as many blocks as needed (no fixed count)
- Include support blocks (stone under redstone components)
- Write a verification spec that tests the circuit's FUNCTIONAL behavior
- The verification must FAIL if the circuit is broken (don't write trivial tests)"""

# ============================================================================
# JSON Schema — Circuit + Verification
# ============================================================================

COMBINED_SCHEMA = {
    "type": "object",
    "required": ["plan", "verification", "reasoning"],
    "properties": {
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step", "block_type", "position", "reason", "connects_to"],
                "properties": {
                    "step": {"type": "integer"},
                    "block_type": {"type": "string"},
                    "position": {"type": "string"},
                    "reason": {"type": "string"},
                    "connects_to": {"type": "array", "items": {"type": "integer"}}
                }
            }
        },
        "verification": {
            "type": "object",
            "required": ["initial_checks", "actions", "tick_steps", "final_checks"],
            "properties": {
                "initial_checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["position", "expected_state"],
                        "properties": {
                            "position": {"type": "string"},
                            "expected_state": {"type": "string"}
                        }
                    }
                },
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["type", "position", "state"],
                        "properties": {
                            "type": {"type": "string", "enum": ["set_block", "place_item", "wait"]},
                            "position": {"type": "string"},
                            "state": {"type": "string"}
                        }
                    }
                },
                "tick_steps": {"type": "integer"},
                "final_checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["position", "expected_state"],
                        "properties": {
                            "position": {"type": "string"},
                            "expected_state": {"type": "string"}
                        }
                    }
                }
            }
        },
        "reasoning": {"type": "string"}
    }
}


# ============================================================================
# Block name normalization (reused from phase0_measure_success_rate.py)
# ============================================================================

BLOCK_NAME_FIXES = {
    "minecraft:redstone_dust": "minecraft:redstone_wire",
    "minecraft:lamp": "minecraft:redstone_lamp",
    "minecraft:block_iron": "minecraft:iron_block",
    "minecraft:block_gold": "minecraft:gold_block",
    "minecraft:block_diamond": "minecraft:diamond_block",
    "minecraft:block_emerald": "minecraft:emerald_block",
    "minecraft:block_redstone": "minecraft:redstone_block",
    "minecraft:block_quartz": "minecraft:quartz_block",
    "minecraft:block_netherite": "minecraft:netherite_block",
    "minecraft:block_coal": "minecraft:coal_block",
    "minecraft:block_lapis": "minecraft:lapis_block",
    "minecraft:block_brick": "minecraft:bricks",
    "minecraft:block_stone": "minecraft:stone",
    "minecraft:stone_block": "minecraft:stone",
    "minecraft:cobblestone_block": "minecraft:cobblestone",
    "minecraft:oak_plank": "minecraft:oak_planks",
    "minecraft:plank": "minecraft:oak_planks",
    "minecraft:planks": "minecraft:oak_planks",
    "minecraft:wood": "minecraft:oak_planks",
    "minecraft:oak_wood": "minecraft:oak_log",
    "minecraft:redstone": "minecraft:redstone_wire",
    "minecraft:wire": "minecraft:redstone_wire",
    "minecraft:dust": "minecraft:redstone_wire",
    "minecraft:torch": "minecraft:redstone_torch",
    "minecraft:piston_sticky": "minecraft:sticky_piston",
    "minecraft:sticky": "minecraft:sticky_piston",
    "minecraft:button_stone": "minecraft:stone_button",
    "minecraft:button_oak": "minecraft:oak_button",
    "minecraft:plate_stone": "minecraft:stone_pressure_plate",
    "minecraft:plate_oak": "minecraft:oak_pressure_plate",
    "minecraft:plate_wood": "minecraft:oak_pressure_plate",
    "minecraft:plate_iron": "minecraft:heavy_weighted_pressure_plate",
    "minecraft:plate_gold": "minecraft:light_weighted_pressure_plate",
    "minecraft:glass_block": "minecraft:glass",
    "minecraft:dirt_block": "minecraft:dirt",
    "minecraft:lever_block": "minecraft:lever",
}

VALID_BLOCK_IDS = {
    "minecraft:stone", "minecraft:cobblestone", "minecraft:dirt", "minecraft:grass_block",
    "minecraft:oak_planks", "minecraft:oak_log", "minecraft:spruce_planks", "minecraft:birch_planks",
    "minecraft:sand", "minecraft:gravel", "minecraft:glass", "minecraft:glass_pane",
    "minecraft:bricks", "minecraft:stone_bricks", "minecraft:nether_bricks",
    "minecraft:oak_leaves", "minecraft:spruce_leaves", "minecraft:birch_leaves",
    "minecraft:iron_block", "minecraft:gold_block", "minecraft:diamond_block",
    "minecraft:emerald_block", "minecraft:redstone_block", "minecraft:lapis_block",
    "minecraft:quartz_block", "minecraft:netherite_block", "minecraft:coal_block",
    "minecraft:obsidian", "minecraft:bedrock", "minecraft:netherrack",
    "minecraft:lever", "minecraft:redstone_wire", "minecraft:redstone_torch",
    "minecraft:redstone_lamp", "minecraft:repeater", "minecraft:comparator",
    "minecraft:piston", "minecraft:sticky_piston", "minecraft:observer",
    "minecraft:stone_button", "minecraft:oak_button", "minecraft:spruce_button",
    "minecraft:stone_pressure_plate", "minecraft:oak_pressure_plate",
    "minecraft:light_weighted_pressure_plate", "minecraft:heavy_weighted_pressure_plate",
    "minecraft:chest", "minecraft:barrel", "minecraft:hopper", "minecraft:dropper",
    "minecraft:dispenser", "minecraft:shulker_box", "minecraft:ender_chest",
    "minecraft:furnace", "minecraft:blast_furnace", "minecraft:smoker",
    "minecraft:oak_fence", "minecraft:spruce_fence", "minecraft:birch_fence",
    "minecraft:oak_door", "minecraft:iron_door", "minecraft:oak_trapdoor", "minecraft:iron_trapdoor",
    "minecraft:oak_stairs", "minecraft:spruce_stairs", "minecraft:birch_stairs",
    "minecraft:oak_slab", "minecraft:spruce_slab", "minecraft:birch_slab",
    "minecraft:stone_slab", "minecraft:cobblestone_slab",
    "minecraft:wall_torch", "minecraft:torch_wall",
    "minecraft:water", "minecraft:lava", "minecraft:ice", "minecraft:snow_block",
    "minecraft:clay", "minecraft:terracotta", "minecraft:white_wool", "minecraft:black_wool",
    "minecraft:white_concrete", "minecraft:white_glazed_terracotta",
    "minecraft:bookshelf", "minecraft:crafting_table", "minecraft:enchanting_table",
    "minecraft:anvil", "minecraft:chipped_anvil", "minecraft:damaged_anvil",
    "minecraft:jukebox", "minecraft:note_block", "minecraft:daylight_detector",
    "minecraft:iron_bars", "minecraft:ladder", "minecraft:vine",
    "minecraft:tripwire_hook", "minecraft:tripwire",
    "minecraft:tnt", "minecraft:target",
    "minecraft:scaffolding", "minecraft:loom", "minecraft:cartography_table",
    "minecraft:fletching_table", "minecraft:smithing_table", "minecraft:grindstone",
    "minecraft:stonecutter", "minecraft:composter",
    "minecraft:lectern", "minecraft:bell",
    "minecraft:armor_stand", "minecraft:item_frame", "minecraft:painting",
    "minecraft:air",
}

SPACE_FIXES = {
    "redstone dust": "redstone_wire",
    "redstone wire": "redstone_wire",
    "redstone lamp": "redstone_lamp",
    "redstone torch": "redstone_torch",
    "redstone block": "redstone_block",
    "sticky piston": "sticky_piston",
    "stone button": "stone_button",
    "oak button": "oak_button",
    "stone pressure plate": "stone_pressure_plate",
    "oak pressure plate": "oak_pressure_plate",
    "light weighted pressure plate": "light_weighted_pressure_plate",
    "heavy weighted pressure plate": "heavy_weighted_pressure_plate",
    "iron block": "iron_block",
    "gold block": "gold_block",
    "diamond block": "diamond_block",
    "emerald block": "emerald_block",
    "coal block": "coal_block",
    "lapis block": "lapis_block",
    "quartz block": "quartz_block",
    "oak planks": "oak_planks",
    "oak log": "oak_log",
    "oak fence": "oak_fence",
    "oak door": "oak_door",
    "oak stairs": "oak_stairs",
    "oak slab": "oak_slab",
    "oak leaves": "oak_leaves",
    "oak trapdoor": "oak_trapdoor",
    "stone bricks": "stone_bricks",
    "nether bricks": "nether_bricks",
}


def normalize_block_name(block_type: str) -> str:
    """Fix common LLM block naming mistakes."""
    if isinstance(block_type, dict):
        # two_step prompt sometimes produces dict expected_state
        block_id = block_type.get("block", block_type.get("block_type", ""))
        props = {k: v for k, v in block_type.items() 
                 if k not in ("block", "block_type", "position") and not isinstance(v, (dict, list))}
        if props and block_id:
            prop_str = ",".join(f"{k}={str(v).lower()}" for k, v in sorted(props.items()))
            return f"{block_id}[{prop_str}]"
        return str(block_id)
    if not isinstance(block_type, str):
        block_type = str(block_type)
    block_type = block_type.strip()
    lower = block_type.lower()
    if lower in SPACE_FIXES:
        block_type = SPACE_FIXES[lower]
    if not block_type.startswith("minecraft:"):
        block_type = f"minecraft:{block_type}"
    return BLOCK_NAME_FIXES.get(block_type, block_type)


def is_valid_block(block_type: str) -> bool:
    """Check if a block type is valid (or can be fixed)."""
    normalized = normalize_block_name(block_type)
    base = normalized.split("[")[0]
    return base in VALID_BLOCK_IDS or base in BLOCK_NAME_FIXES.values()


def parse_position(pos_str: str) -> tuple:
    """Parse a position string like '0,0,0' or '(0, 1, -2)' into (x, y, z)."""
    coords = re.findall(r'-?\d+', str(pos_str))
    if len(coords) >= 3:
        return int(coords[0]), int(coords[1]), int(coords[2])
    return None


# ============================================================================
# JSON repair (for models that produce malformed JSON)
# ============================================================================

def repair_json(raw_text: str) -> Optional[dict]:
    """
    Attempt to repair malformed JSON from LLM output.
    
    Common issues handled:
    - Markdown code block wrappers (```json ... ```)
    - Extra text before/after JSON
    - Trailing commas before } or ]
    - Missing closing brackets
    
    Returns parsed dict on success, None on failure.
    """
    if not raw_text or not raw_text.strip():
        return None
    
    text = raw_text.strip()
    
    # 1. Strip markdown code block wrappers
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    
    # 2. Extract JSON object (find first { and matching last })
    first_brace = text.find('{')
    if first_brace == -1:
        return None
    
    # Try to find matching closing brace via depth tracking
    depth = 0
    end_idx = -1
    for i in range(first_brace, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break
    
    if end_idx == -1:
        # Unmatched braces — try adding missing closing brackets
        text = text[first_brace:]
    else:
        text = text[first_brace:end_idx]
    
    # 3. Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    
    # 4. Try to parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 5. More aggressive repair: balance brackets
    open_braces = text.count('{')
    close_braces = text.count('}')
    open_brackets = text.count('[')
    close_brackets = text.count(']')
    
    if open_braces > close_braces:
        text += '}' * (open_braces - close_braces)
    if open_brackets > close_brackets:
        text += ']' * (open_brackets - close_brackets)
    
    text = re.sub(r',\s*([}\]])', r'\1', text)
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ============================================================================
# Circuit loading
# ============================================================================

def load_circuit(name: str) -> dict:
    path = CIRCUITS_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def list_circuits() -> list:
    return sorted(p.stem for p in CIRCUITS_DIR.glob("*.json"))


def load_descriptions() -> list:
    """Load natural language circuit descriptions."""
    with open(DESCRIPTIONS_FILE) as f:
        return json.load(f)


def list_description_ids() -> list:
    """List all description IDs."""
    return sorted(d["id"] for d in load_descriptions())


# ============================================================================
# LLM generation
# ============================================================================

def generate_circuit_and_verification(
    client: OpenRouterClient,
    model: str,
    circuit: dict,
    source: str = "test_circuits",
    prompt_variation: str = "baseline",
) -> dict:
    """Generate circuit + verification spec in one LLM call."""
    if prompt_variation != "baseline" and PROMPT_VARIATIONS.get(prompt_variation) is not None:
        var_system, var_user_template = PROMPT_VARIATIONS[prompt_variation]
        system_prompt = var_system
        user_prompt = var_user_template.format(
            description=circuit["description"],
            verification_behavior=circuit.get("verification_behavior", ""),
        )
    elif source == "descriptions":
        system_prompt = SYSTEM_PROMPT_DESCRIPTIONS
        user_prompt = USER_PROMPT_DESCRIPTIONS.format(
            description=circuit["description"],
            verification_behavior=circuit["verification_behavior"],
        )
    else:
        expected = circuit["expected_blocks"]
        description = circuit["description"]
        system_prompt = SYSTEM_PROMPT
        user_prompt = USER_PROMPT_TEMPLATE.format(description=description, expected=expected)

    try:
        if prompt_variation == "two_step":
            # Two-step has a different schema (adds layout_analysis field).
            # Use json_object mode instead of strict json_schema to avoid validation errors.
            response = client.chat(
                model=model,
                messages=[ChatMessage(role="user", content=user_prompt)],
                system_prompt=system_prompt,
                response_format={"type": "json_object"},
                temperature=TEMPERATURE,
                reasoning_effort="none",
            )
            content = response.content
            if content is None:
                # Some models put output in 'reasoning' field
                reasoning = response.raw_response.get("choices", [{}])[0].get("message", {}).get("reasoning")
                if reasoning:
                    content = reasoning
            if content is None:
                return {"error": "Model returned no content for two_step variation"}
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                repaired = repair_json(content)
                if repaired is not None:
                    return repaired
                print(f"      Could not repair two_step JSON from {model}. Raw (first 500 chars): {content[:500]}")
                return {"error": "Failed to parse two_step JSON"}
        else:
            result = client.complete_with_schema(
                model=model,
                prompt=user_prompt,
                system_prompt=system_prompt,
                schema=COMBINED_SCHEMA,
                temperature=TEMPERATURE,
                reasoning_effort="none",  # Critical for structured output
            )
            return result
    except Exception as e:
        error_str = str(e)
        # Even with client-side repair, some models may still produce bad JSON.
        # Try to extract raw content from the error and apply additional repair.
        raw_match = re.search(r'Raw: (.+)$', error_str, re.DOTALL)
        if raw_match:
            raw_content = raw_match.group(1)
            repaired = repair_json(raw_content)
            if repaired is not None:
                return repaired
            print(f"      Could not repair JSON from {model}. Raw (first 500 chars): {raw_content[:500]}")
        return {"error": error_str, "exception_type": type(e).__name__}


def iterate_on_failure(
    client: OpenRouterClient,
    model: str,
    circuit: dict,
    previous_output: dict,
    failure_info: dict,
    source: str = "test_circuits",
    prompt_variation: str = "baseline",
) -> dict:
    """Send failure back to model for repair."""
    description = circuit["description"]
    block_count = len(previous_output.get("plan", []))

    if prompt_variation != "baseline" and PROMPT_VARIATIONS.get(prompt_variation) is not None:
        system_prompt = PROMPT_VARIATIONS[prompt_variation][0]  # Use variation's system prompt
    elif source == "descriptions":
        system_prompt = SYSTEM_PROMPT_DESCRIPTIONS
    else:
        system_prompt = SYSTEM_PROMPT

    if source == "descriptions":
        repair_prompt = f"""Your previous attempt at this circuit FAILED verification. Please fix it.

Circuit description: {description}

Your previous plan had {block_count} blocks and failed verification. Please fix the circuit.
Failure reason: {failure_info.get("reason", "unknown")}
Failure details: {json.dumps(failure_info.get("details", {}), indent=2)[:1000]}

Please regenerate the circuit with fixes. Make sure:
1. The verification spec is FAIR (fails if broken, passes if working)
2. All block names are valid Minecraft IDs
3. Support blocks are included for redstone components"""
    else:
        expected = circuit["expected_blocks"]
        repair_prompt = f"""Your previous attempt at this circuit FAILED verification. Please fix it.

Circuit description: {description}
Expected blocks: {expected}

Your previous plan had {block_count} blocks.
Failure reason: {failure_info.get("reason", "unknown")}
Failure details: {json.dumps(failure_info.get("details", {}), indent=2)[:1000]}

Please regenerate the circuit with fixes. Make sure:
1. The plan has EXACTLY {expected} blocks
2. The verification spec is FAIR (fails if broken, passes if working)
3. All block names are valid Minecraft IDs
4. Support blocks are included for redstone components"""

    try:
        if prompt_variation == "two_step":
            response = client.chat(
                model=model,
                messages=[ChatMessage(role="user", content=repair_prompt)],
                system_prompt=system_prompt,
                response_format={"type": "json_object"},
                temperature=TEMPERATURE,
                reasoning_effort="none",
            )
            content = response.content
            if content is None:
                reasoning = response.raw_response.get("choices", [{}])[0].get("message", {}).get("reasoning")
                if reasoning:
                    content = reasoning
            if content is None:
                return {"error": "Model returned no content on iteration for two_step variation"}
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                repaired = repair_json(content)
                if repaired is not None:
                    return repaired
                return {"error": "Failed to parse two_step JSON on iteration"}
        else:
            result = client.complete_with_schema(
                model=model,
                prompt=repair_prompt,
                system_prompt=system_prompt,
                schema=COMBINED_SCHEMA,
                temperature=TEMPERATURE,
                reasoning_effort="none",
            )
            return result
    except Exception as e:
        return {"error": str(e), "exception_type": type(e).__name__}


# ============================================================================
# Parse LLM output
# ============================================================================

def parse_llm_output(llm_output: dict) -> tuple:
    """Parse LLM output into (blocks, verification_spec)."""
    if "error" in llm_output:
        return [], None

    plan = llm_output.get("plan", [])
    blocks = []
    skipped = []
    for step in plan:
        pos = parse_position(step.get("position", ""))
        if pos is None:
            continue
        block_type = step.get("block_type", "")
        block_type = normalize_block_name(block_type)
        if not is_valid_block(block_type):
            skipped.append(block_type)
            continue
        blocks.append((pos[0], pos[1], pos[2], block_type, None))

    verification = llm_output.get("verification", {})
    return blocks, verification


# ============================================================================
# Build circuit in MC
# ============================================================================

def calculate_bounds(blocks: list) -> tuple:
    if not blocks:
        return ((0, 0, 0), (0, 0, 0))
    xs = [b[0] for b in blocks]
    ys = [b[1] for b in blocks]
    zs = [b[2] for b in blocks]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def clear_region(bridge: MinecraftBridge, origin: tuple, bounds: tuple):
    (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds
    ox, oy, oz = origin
    x1, y1, z1 = ox + min_x - 5, oy + min_y - 5, oz + min_z - 5
    x2, y2, z2 = ox + max_x + 5, oy + max_y + 5, oz + max_z + 5
    size = (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1)
    if size > 32000:
        for y in range(y1, y2 + 1, 5):
            y_end = min(y + 4, y2)
            bridge.fill(x1, y, z1, x2, y_end, z2, "minecraft:air")
    else:
        bridge.fill(x1, y1, z1, x2, y2, z2, "minecraft:air")


def add_support_blocks(blocks: list) -> list:
    """Add stone support blocks under redstone components and attachment blocks for levers."""
    needs_support = {
        "minecraft:redstone_wire", "minecraft:repeater", "minecraft:comparator",
        "minecraft:lever", "minecraft:stone_button", "minecraft:oak_button",
        "minecraft:stone_pressure_plate", "minecraft:oak_pressure_plate",
        "minecraft:light_weighted_pressure_plate", "minecraft:heavy_weighted_pressure_plate",
        "minecraft:rail", "minecraft:powered_rail", "minecraft:detector_rail", "minecraft:activator_rail",
    }
    existing_positions = set((b[0], b[1], b[2]) for b in blocks)
    support_blocks = []
    for b in blocks:
        base = b[3].split("[")[0]
        if base in needs_support:
            below = (b[0], b[1] - 1, b[2])
            if below not in existing_positions:
                support_blocks.append((below[0], below[1], below[2], "minecraft:stone", None))
                existing_positions.add(below)
    return blocks + support_blocks


def normalize_lever_facing(blocks: list) -> list:
    """Fix lever block states: replace facing=up with face=floor, add facing if missing,
    and ensure attachment blocks exist."""
    result = []
    existing_positions = set((b[0], b[1], b[2]) for b in blocks)
    extra_blocks = []
    
    for b in blocks:
        x, y, z, state, nbt = b
        if "minecraft:lever" in state:
            # Fix facing=up → face=floor (facing=up is invalid for levers)
            if "facing=up" in state:
                state = state.replace("facing=up", "face=floor")
                if "face=floor" in state and "facing=" not in state:
                    # face=floor levers don't need a facing, but the server will add one
                    pass
            # If no facing and no face property, add facing=east as default
            if "facing=" not in state and "face=" not in state:
                if "[" in state:
                    state = state.replace("[", "[facing=east,")
                else:
                    state = state.replace("minecraft:lever", "minecraft:lever[facing=east]")
            
            # Ensure attachment block exists for wall-mounted levers
            facing = None
            for prop in state.split("[")[1].rstrip("]").split(",") if "[" in state else []:
                if prop.startswith("facing="):
                    facing = prop.split("=")[1]
                    break
            if facing and facing != "up":
                attach_pos = None
                if facing == "north":
                    attach_pos = (x, y, z + 1)  # attached to block to the south
                elif facing == "south":
                    attach_pos = (x, y, z - 1)  # attached to block to the north
                elif facing == "east":
                    attach_pos = (x - 1, y, z)  # attached to block to the west
                elif facing == "west":
                    attach_pos = (x + 1, y, z)  # attached to block to the east
                if attach_pos and attach_pos not in existing_positions:
                    extra_blocks.append((attach_pos[0], attach_pos[1], attach_pos[2], "minecraft:stone", None))
                    existing_positions.add(attach_pos)
        result.append((x, y, z, state, nbt))
    
    return result + extra_blocks


def build_circuit_in_mc(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    if not blocks:
        return {"success": False, "error": "No blocks to build"}

    blocks = add_support_blocks(blocks)
    blocks = normalize_lever_facing(blocks)
    bounds = calculate_bounds(blocks)
    ox, oy, oz = origin
    (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds

    try:
        bridge.run_command(f"forceload add {ox + min_x - 16} {oz + min_z - 16} {ox + max_x + 16} {oz + max_z + 16}")
        bridge.run_command("carpet fillUpdates false")
        clear_region(bridge, origin, bounds)
        bridge.run_command("carpet fillUpdates true")

        replicate_blocks(
            blocks=blocks, origin=origin, bounds=bounds, bridge=bridge,
            rate_limit=64, use_updates=True, force_update_region=False,
        )

        # Force redstone updates on all blocks (NC updates at priority 0)
        # This is needed because setblock triggers block updates but NOT redstone updates.
        # mira_api redstone_update uses TIS Addition's manipulate block emit block_update.
        for b in blocks:
            x, y, z = ox + b[0], oy + b[1], oz + b[2]
            bridge.run_command(f"mira_api redstone_update {x} {y} {z}")

        # Settle: let the circuit reach its resting state before verification.
        # Redstone components (torches, lamps) need ticks to re-evaluate after placement.
        # Use staged approach for reliable propagation through torch chains.
        all_build_positions = [(ox + b[0], oy + b[1], oz + b[2]) for b in blocks]
        _staged_tick(bridge, 20, all_build_positions)

        return {"success": True, "bounds": bounds}
    except Exception as e:
        return {"success": False, "error": str(e), "bounds": bounds}


def reconnect_bridge(bridge: MinecraftBridge, max_retries: int = 3) -> bool:
    for i in range(max_retries):
        try:
            bridge.disconnect()
        except:
            pass
        time.sleep(2)
        try:
            bridge.connect()
            bridge.run_command("script unload mira_api")
            bridge.run_command("script load mira_api")
            return True
        except Exception as e:
            print(f"    Reconnect attempt {i+1}/{max_retries} failed: {e}")
    return False


# ============================================================================
# Verification — Run LLM's verification spec + automatic negative checks
# ============================================================================

def _tick(bridge: MinecraftBridge, ticks: int = 20, update_positions: list = None):
    """Run tick freeze → step → unfreeze. Optionally emit redstone updates first.
    
    Uses mira_api redstone_update (TIS Addition's manipulate block emit block_update)
    which emits NC updates at priority 0 — the only kind redstone components respond to.
    
    Note: For circuits with redstone torches (NOT gate, etc.), use _staged_tick()
    which interleaves redstone_updates and tick steps for reliable signal propagation.
    """
    if update_positions:
        for pos in update_positions:
            bridge.run_command(f"mira_api redstone_update {pos[0]} {pos[1]} {pos[2]}")
    bridge.run_command("tick freeze")
    bridge.run_command(f"tick step {ticks}")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)


def _staged_tick(bridge: MinecraftBridge, total_ticks: int, all_positions: list):
    """Staged NC update propagation for reliable redstone torch signal chains.
    
    This is needed because redstone propagation is sequential — the torch needs to
    see the stone block's updated state BEFORE it can re-evaluate, and the lamp
    needs to see the torch's updated state. A single redstone_update + tick step
    doesn't work for multi-stage chains like: lever → stone → torch → lamp.
    
    Stages:
    1. tick step 5  — let lever state propagate to attached block (block updates)
    2. redstone_update on ALL blocks — emit NC updates (torch sees stone's power)
    3. tick step 10 — let torch re-evaluate
    4. redstone_update on ALL blocks again — emit NC updates (lamp sees torch)
    5. tick step remaining — final settle
    """
    # Stage 1: tick step (let lever state propagate to attached block)
    bridge.run_command("tick freeze")
    bridge.run_command("tick step 5")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)
    
    # Stage 2: redstone_update on ALL circuit blocks (emit NC updates)
    for pos in all_positions:
        bridge.run_command(f"mira_api redstone_update {pos[0]} {pos[1]} {pos[2]}")
    
    # Stage 3: tick step (let torch re-evaluate)
    bridge.run_command("tick freeze")
    bridge.run_command("tick step 10")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)
    
    # Stage 4: redstone_update on ALL circuit blocks again (let lamp see updated torch)
    for pos in all_positions:
        bridge.run_command(f"mira_api redstone_update {pos[0]} {pos[1]} {pos[2]}")
    
    # Stage 5: tick step remaining (final settle)
    remaining = max(total_ticks - 15, 5)
    bridge.run_command("tick freeze")
    bridge.run_command(f"tick step {remaining}")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)


def _check_block(bridge: MinecraftBridge, x: int, y: int, z: int, expected: str) -> bool:
    """Check a block at absolute coordinates.
    
    For levers, only check `powered` property (not `facing` or `face`) since the server
    determines facing from attachment context and may override what the model specified.
    """
    # Normalize lever checks: strip facing/face, only check powered
    if "minecraft:lever" in expected:
        # Extract powered property
        powered = None
        if "powered=true" in expected:
            powered = "true"
        elif "powered=false" in expected:
            powered = "false"
        if powered:
            expected = f"minecraft:lever[powered={powered}]"
        else:
            expected = "minecraft:lever"
    resp = bridge.run_command(f"mira_api check_block {x} {y} {z} {expected}")
    return "PASS" in resp


def _set_block(bridge: MinecraftBridge, x: int, y: int, z: int, state: str):
    """Set a block at absolute coordinates."""
    bridge.run_command(f"setblock {x} {y} {z} {state}")


def _power_lever(lever_state: str) -> str:
    """Set a lever to powered=true."""
    if "powered=true" in lever_state:
        return lever_state
    if "powered=false" in lever_state:
        return lever_state.replace("powered=false", "powered=true")
    if "[" in lever_state:
        return lever_state.replace("]", ",powered=true]")
    else:
        return f"{lever_state}[powered=true]"


def _unpower_lever(lever_state: str) -> str:
    """Set a lever to powered=false."""
    if "powered=false" in lever_state:
        return lever_state
    if "powered=true" in lever_state:
        return lever_state.replace("powered=true", "powered=false")
    if "[" in lever_state:
        return lever_state.replace("]", ",powered=false]")
    else:
        return f"{lever_state}[powered=false]"


def run_llm_verification(
    bridge: MinecraftBridge,
    verification: dict,
    blocks: list,
    origin: tuple,
) -> dict:
    """
    Run the LLM's verification spec against the built circuit.
    Returns {passed: bool, details: dict}.
    """
    result = {
        "passed": False,
        "initial_checks_passed": False,
        "final_checks_passed": False,
        "details": {},
    }

    if not verification:
        result["details"]["error"] = "No verification spec provided"
        return result

    ox, oy, oz = origin

    # 1. Run initial checks
    initial_checks = verification.get("initial_checks", [])
    initial_results = []
    all_initial_pass = True
    for check in initial_checks:
        pos = parse_position(check.get("position", ""))
        if pos is None:
            initial_results.append({"check": check, "passed": False, "error": "invalid position"})
            all_initial_pass = False
            continue
        expected = normalize_block_name(check.get("expected_state", ""))
        passed = _check_block(bridge, ox + pos[0], oy + pos[1], oz + pos[2], expected)
        initial_results.append({"position": check.get("position"), "expected": expected, "passed": passed})
        if not passed:
            all_initial_pass = False
    result["initial_checks_passed"] = all_initial_pass
    result["details"]["initial_checks"] = initial_results

    # 2. Run actions
    actions = verification.get("actions", [])
    action_positions = []
    for action in actions:
        action_type = action.get("type", "set_block")
        pos = parse_position(action.get("position", ""))
        if pos is None:
            continue
        state = normalize_block_name(action.get("state", ""))
        abs_pos = (ox + pos[0], oy + pos[1], oz + pos[2])
        if action_type == "set_block":
            # If this is a lever toggle, use mira_api toggle_lever which properly
            # emits redstone updates via TIS Addition's manipulate block emit block_update.
            # Plain setblock doesn't trigger redstone updates (fundamental MC limitation).
            if "lever" in state:
                bridge.run_command(f"mira_api toggle_lever {abs_pos[0]} {abs_pos[1]} {abs_pos[2]}")
            else:
                _set_block(bridge, *abs_pos, state)
            action_positions.append(abs_pos)
        elif action_type == "place_item":
            # place_item: state is "minecraft:diamond 1" format
            bridge.run_command(f"replaceitem block {abs_pos[0]} {abs_pos[1]} {abs_pos[2]} container.0 {state}")
        elif action_type == "wait":
            _tick(bridge, 10)
    result["details"]["actions_executed"] = len(actions)

    # 3. Tick — use staged NC update propagation for reliable signal chains
    # (lever → stone → torch → lamp). A single redstone_update + tick doesn't work
    # for multi-stage chains because the torch needs to see the stone block's updated
    # state BEFORE it can re-evaluate.
    # Handle tick_steps being a list [20] instead of int 20
    raw_tick_steps = verification.get("tick_steps", 20)
    if isinstance(raw_tick_steps, list):
        tick_steps = raw_tick_steps[0] if raw_tick_steps else 20
    elif isinstance(raw_tick_steps, int):
        tick_steps = raw_tick_steps
    else:
        tick_steps = 20
    if tick_steps < 15:
        tick_steps = 15
    
    # Compute all positions for staged redstone updates
    all_positions = list(action_positions)
    for b in blocks:
        all_positions.append((ox + b[0], oy + b[1], oz + b[2]))
    
    _staged_tick(bridge, tick_steps, all_positions)

    # 4. Run final checks
    final_checks = verification.get("final_checks", [])
    final_results = []
    all_final_pass = True
    for check in final_checks:
        pos = parse_position(check.get("position", ""))
        if pos is None:
            final_results.append({"check": check, "passed": False, "error": "invalid position"})
            all_final_pass = False
            continue
        expected = normalize_block_name(check.get("expected_state", ""))
        passed = _check_block(bridge, ox + pos[0], oy + pos[1], oz + pos[2], expected)
        final_results.append({"position": check.get("position"), "expected": expected, "passed": passed})
        if not passed:
            all_final_pass = False
    result["final_checks_passed"] = all_final_pass
    result["details"]["final_checks"] = final_results

    result["passed"] = all_initial_pass and all_final_pass
    return result


def run_negative_check(
    bridge: MinecraftBridge,
    verification: dict,
    blocks: list,
    origin: tuple,
) -> dict:
    """
    Automatic negative check: after the LLM's verification passes, we BREAK the
    circuit by toggling the power source (unpower any levers) and re-run the
    final checks. If the final checks STILL pass, the verification is unfair
    (false positive — the test would pass even on a broken circuit).

    Returns {is_fair: bool, details: dict}.
    """
    result = {"is_fair": True, "details": {}}

    if not verification:
        result["details"]["error"] = "No verification spec"
        return result

    ox, oy, oz = origin

    # Find all levers in the circuit
    levers = [b for b in blocks if b[3].split("[")[0] == "minecraft:lever"]
    unpower_positions = []
    if not levers:
        # No levers to toggle — try redstone blocks
        redstone_blocks = [b for b in blocks if b[3].split("[")[0] == "minecraft:redstone_block"]
        if not redstone_blocks:
            result["details"]["skip_reason"] = "No levers or redstone_blocks to toggle"
            return result
        # Replace redstone blocks with stone (breaks the circuit)
        for rb in redstone_blocks:
            abs_pos = (ox + rb[0], oy + rb[1], oz + rb[2])
            _set_block(bridge, *abs_pos, "minecraft:stone")
            unpower_positions.append(abs_pos)
    else:
        # Unpower all levers (breaks the circuit) using toggle_lever for proper redstone updates
        for lever in levers:
            abs_pos = (ox + lever[0], oy + lever[1], oz + lever[2])
            bridge.run_command(f"mira_api toggle_lever {abs_pos[0]} {abs_pos[1]} {abs_pos[2]}")
            unpower_positions.append(abs_pos)

    # Tick to let the broken state propagate (staged approach for torch circuits)
    tick_steps = verification.get("tick_steps", 20)
    all_positions = [(ox + b[0], oy + b[1], oz + b[2]) for b in blocks]
    _staged_tick(bridge, tick_steps, all_positions)

    # Re-run final checks — they should now FAIL (circuit is broken)
    final_checks = verification.get("final_checks", [])
    negative_results = []
    any_failed = False
    for check in final_checks:
        pos = parse_position(check.get("position", ""))
        if pos is None:
            continue
        expected = normalize_block_name(check.get("expected_state", ""))
        passed = _check_block(bridge, ox + pos[0], oy + pos[1], oz + pos[2], expected)
        negative_results.append({"position": check.get("position"), "expected": expected, "passed": passed})
        if not passed:
            any_failed = True

    # If ANY final check failed on the broken circuit, the verification is FAIR
    # (it correctly detects a broken circuit)
    result["is_fair"] = any_failed
    result["details"]["negative_checks"] = negative_results
    result["details"]["any_check_failed_on_broken"] = any_failed
    return result


# ============================================================================
# Main measurement loop
# ============================================================================

def measure_model_on_circuit(
    client: OpenRouterClient,
    model: str,
    circuit: dict,
    attempts: int,
    bridge: MinecraftBridge,
    iterate_rounds: int = 0,
    dry_run: bool = False,
    source: str = "test_circuits",
    strict_negative: bool = True,
    prompt_variation: str = "baseline",
) -> list:
    """Run N attempts of a model on a circuit. Returns list of attempt results."""
    results = []
    circuit_id = circuit.get("id", "unknown")
    expected = circuit.get("expected_blocks", 0)

    for attempt in range(1, attempts + 1):
        print(f"  [{model}] {circuit_id} attempt {attempt}/{attempts}...", end=" ", flush=True)

        attempt_result = {
            "model": model,
            "circuit": circuit_id,
            "attempt": attempt,
            "expected_blocks": expected,
            "prompt_variation": prompt_variation,
            "timestamp": datetime.now().isoformat(),
        }

        # Add metadata from descriptions source
        if source == "descriptions":
            attempt_result["complexity"] = circuit.get("complexity")
            attempt_result["circuit_type"] = circuit.get("circuit_type")

        # 1. Generate circuit + verification
        llm_output = generate_circuit_and_verification(
            client, model, circuit, source=source, prompt_variation=prompt_variation,
        )
        attempt_result["llm_output"] = llm_output

        if "error" in llm_output:
            print(f"LLM ERROR: {llm_output['error'][:80]}")
            attempt_result["status"] = "llm_error"
            results.append(attempt_result)
            continue

        # 2. Parse blocks + verification
        blocks, verification = parse_llm_output(llm_output)
        attempt_result["block_count"] = len(blocks)
        if source == "test_circuits":
            attempt_result["block_count_match"] = (len(blocks) == expected)
        attempt_result["has_verification"] = verification is not None

        if not blocks:
            print("PARSE ERROR: no blocks")
            attempt_result["status"] = "parse_error"
            results.append(attempt_result)
            continue

        if dry_run:
            print(f"DRY RUN: {len(blocks)} blocks, verification={'yes' if verification else 'no'}")
            attempt_result["status"] = "dry_run"
            results.append(attempt_result)
            continue

        # 3. Build in MC
        origin = (ORIGIN_BASE[0] + attempt * REGION_SPACING, ORIGIN_BASE[1], ORIGIN_BASE[2])
        build_result = build_circuit_in_mc(bridge, blocks, origin)
        attempt_result["build_success"] = build_result.get("success", False)
        attempt_result["build_error"] = build_result.get("error")

        if not build_result.get("success", False):
            print(f"BUILD ERROR: {build_result.get('error', 'unknown')[:80]}")
            attempt_result["status"] = "build_error"
            # Try to reconnect if server crashed
            if not reconnect_bridge(bridge):
                print("    SERVER CRASHED — skipping remaining attempts")
                attempt_result["status"] = "server_crash"
                results.append(attempt_result)
                break
            results.append(attempt_result)
            continue

        # 4. Run LLM's verification
        verify_result = run_llm_verification(bridge, verification, blocks, origin)
        attempt_result["verification_passed"] = verify_result["passed"]
        attempt_result["verification_details"] = verify_result["details"]

        # 5. If verification passed, run negative check (fairness test)
        if verify_result["passed"]:
            negative_result = run_negative_check(bridge, verification, blocks, origin)
            attempt_result["verification_is_fair"] = negative_result["is_fair"]
            attempt_result["negative_check_details"] = negative_result["details"]
            negative_check_failed = not negative_result["is_fair"]

            if negative_result["is_fair"]:
                print("PASS (fair)")
                attempt_result["status"] = "pass"
                attempt_result["functional_pass"] = True
            else:
                print("UNFAIR (test passes on broken circuit)")
                attempt_result["status"] = "unfair_test"
                # When strict_negative is True, mark as functional failure
                # because the verification spec is unreliable — it would pass
                # even on a broken circuit (unfair test).
                if strict_negative:
                    attempt_result["functional_pass"] = False
                    attempt_result["failure_reason"] = "unfair_verification_spec"
                else:
                    attempt_result["functional_pass"] = True
        else:
            print("FAIL (verification didn't pass)")
            attempt_result["status"] = "fail"

            # 6. Optional iteration: send failure back to model
            if iterate_rounds > 0:
                for round_num in range(1, iterate_rounds + 1):
                    print(f"    Iterating round {round_num}/{iterate_rounds}...", end=" ", flush=True)
                    failure_info = {
                        "reason": "verification_failed",
                        "details": verify_result["details"],
                    }
                    llm_output = iterate_on_failure(
                        client, model, circuit, llm_output, failure_info,
                        source=source, prompt_variation=prompt_variation,
                    )
                    blocks, verification = parse_llm_output(llm_output)

                    if not blocks:
                        print("PARSE ERROR on iteration")
                        continue

                    # Rebuild
                    origin = (ORIGIN_BASE[0] + attempt * REGION_SPACING + 50, ORIGIN_BASE[1], ORIGIN_BASE[2])
                    build_result = build_circuit_in_mc(bridge, blocks, origin)
                    if not build_result.get("success", False):
                        print(f"BUILD ERROR on iteration: {build_result.get('error', 'unknown')[:60]}")
                        continue

                    # Re-verify
                    verify_result = run_llm_verification(bridge, verification, blocks, origin)
                    if verify_result["passed"]:
                        negative_result = run_negative_check(bridge, verification, blocks, origin)
                        if negative_result["is_fair"]:
                            print("PASS (fair) after iteration")
                            attempt_result["status"] = "pass_after_iteration"
                            attempt_result["functional_pass"] = True
                            attempt_result["iterations_used"] = round_num
                            break
                        else:
                            print("UNFAIR after iteration")
                            attempt_result["status"] = "unfair_after_iteration"
                            if strict_negative:
                                attempt_result["functional_pass"] = False
                                attempt_result["failure_reason"] = "unfair_verification_spec"
                            else:
                                attempt_result["functional_pass"] = True
                    else:
                        print("FAIL again")

        results.append(attempt_result)

    return results


def main():
    parser = argparse.ArgumentParser(description="Phase 0 multi-model measurement")
    parser.add_argument("--models", type=str, default="deepseek-v4-flash",
                        help="Comma-separated model names (or 'all')")
    parser.add_argument("--circuits", type=str, default="simple_lever_lamp",
                        help="Comma-separated circuit names (or 'all')")
    parser.add_argument("--attempts", type=int, default=ATTEMPTS_DEFAULT,
                        help="Number of attempts per model per circuit")
    parser.add_argument("--iterate", type=int, default=0,
                        help="Number of iteration rounds on failure (0=disabled)")
    parser.add_argument("--dry-run", action="store_true",
                        help="LLM only, no MC building")
    parser.add_argument("--max-blocks", type=int, default=50,
                        help="Skip circuits with expected_blocks > this (estimate for descriptions)")
    parser.add_argument("--source", type=str, default="descriptions",
                        choices=["test_circuits", "descriptions"],
                        help="Circuit source: test_circuits (position-encoded) or descriptions (natural language)")
    parser.add_argument("--no-strict-negative", action="store_false", dest="strict_negative",
                        default=True,
                        help="Count unfair verification specs as passes (for analysis purposes)")
    parser.add_argument("--complexity", type=str, default=None,
                        help="Filter by complexity (simple/medium/complex) — descriptions source only")
    parser.add_argument("--circuit-type", type=str, default=None,
                        help="Filter by circuit_type — descriptions source only")
    parser.add_argument("--prompt-variation", type=str, default="baseline",
                        choices=list(PROMPT_VARIATIONS.keys()),
                        help="Prompt strategy variation (default: baseline)")
    args = parser.parse_args()

    # Parse models
    if args.models == "all":
        models = DEFAULT_MODELS
    else:
        models = [m.strip() for m in args.models.split(",")]

    # Load circuits based on source
    source = args.source
    if source == "descriptions":
        all_descriptions = load_descriptions()
        desc_ids = [d["id"] for d in all_descriptions]

        if args.circuits == "all":
            selected_ids = desc_ids
        else:
            selected_ids = [c.strip() for c in args.circuits.split(",")]

        # Build dict
        all_circuits = {d["id"]: d for d in all_descriptions}

        # Filter by complexity
        if args.complexity:
            target = [c.strip() for c in args.complexity.split(",")]
            selected_ids = [n for n in selected_ids if n in all_circuits and all_circuits[n].get("complexity") in target]

        # Filter by circuit_type
        if args.circuit_type:
            target = [c.strip() for c in args.circuit_type.split(",")]
            selected_ids = [n for n in selected_ids if n in all_circuits and all_circuits[n].get("circuit_type") in target]

        # Filter by max-blocks (using expected_blocks as estimate)
        selected_ids = [n for n in selected_ids if n in all_circuits and all_circuits[n].get("expected_blocks", 0) <= args.max_blocks]

        circuits = selected_ids
        print(f"Source: descriptions ({DESCRIPTIONS_FILE.name})")
    else:
        if args.circuits == "all":
            circuits = list_circuits()
        else:
            circuits = [c.strip() for c in args.circuits.split(",")]

        all_circuits = {name: load_circuit(name) for name in circuits}

        # Filter by max-blocks
        circuits = [name for name, c in all_circuits.items() if c.get("expected_blocks", 0) <= args.max_blocks]

        print(f"Source: test_circuits ({CIRCUITS_DIR})")

    if args.complexity and source != "descriptions":
        print("Note: --complexity filter only applies to --source descriptions (ignored)")

    if args.circuit_type and source != "descriptions":
        print("Note: --circuit-type filter only applies to --source descriptions (ignored)")

    print(f"Models: {models}")
    print(f"Circuits (max {args.max_blocks} blocks): {circuits}")
    print(f"Attempts per model per circuit: {args.attempts}")
    print(f"Iteration rounds: {args.iterate}")
    print(f"Dry run: {args.dry_run}")
    print(f"Prompt variation: {args.prompt_variation}")
    print(f"Strict negative: {args.strict_negative} (unfair tests {'count as failures' if args.strict_negative else 'counted as passes'})")
    print()

    # Init LLM client
    client = OpenRouterClient()

    # Init bridge (unless dry run)
    bridge = None
    if not args.dry_run:
        bridge = MinecraftBridge()
        try:
            bridge.connect()
            bridge.run_command("script unload mira_api")
            bridge.run_command("script load mira_api")
            print(f"Connected to MC server")
        except Exception as e:
            print(f"Failed to connect to MC server: {e}")
            print("Run with --dry-run for LLM-only testing.")
            return 1

    # Run measurements
    all_results = []
    for model in models:
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        print(f"{'='*60}")
        for circuit_name in circuits:
            circuit = all_circuits[circuit_name]
            est = circuit.get('expected_blocks', '?')
            extra = ""
            if source == "descriptions":
                extra = f" [{circuit.get('complexity', '?')}/{circuit.get('circuit_type', '?')}]"
            print(f"\nCircuit: {circuit_name} (~{est} blocks{extra})")
            results = measure_model_on_circuit(
                client=client,
                model=model,
                circuit=circuit,
                attempts=args.attempts,
                bridge=bridge,
                iterate_rounds=args.iterate,
                dry_run=args.dry_run,
                source=source,
                strict_negative=args.strict_negative,
                prompt_variation=args.prompt_variation,
            )
            all_results.extend(results)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = RESULTS_DIR / f"phase0_multi_model_{timestamp}.json"
    with open(results_path, "w") as f:
        json.dump({
            "timestamp": timestamp,
            "source": source,
            "models": models,
            "circuits": circuits,
            "attempts": args.attempts,
            "iterate_rounds": args.iterate,
            "prompt_variation": args.prompt_variation,
            "results": all_results,
        }, f, indent=2)
    print(f"\nResults saved to {results_path}")

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    if source == "descriptions":
        print(f"{'Model':<25} {'Circuit':<25} {'Variation':<14} {'Comp':<9} {'Type':<20} {'Pass':<6} {'Unfair':<7} {'Fail':<6} {'Error':<6}")
        print("-" * 120)
    else:
        print(f"{'Model':<25} {'Circuit':<20} {'Variation':<14} {'Pass':<6} {'Unfair':<7} {'Fail':<6} {'Error':<6}")
        print("-" * 86)
    for model in models:
        for circuit_name in circuits:
            model_circuit_results = [r for r in all_results if r["model"] == model and r["circuit"] == circuit_name]
            passes = sum(1 for r in model_circuit_results if r.get("status") in ("pass", "pass_after_iteration"))
            unfair = sum(1 for r in model_circuit_results if "unfair" in r.get("status", ""))
            fails = sum(1 for r in model_circuit_results if r.get("status") == "fail")
            errors = sum(1 for r in model_circuit_results if "error" in r.get("status", "") or r.get("status") == "server_crash")
            variation = model_circuit_results[0].get("prompt_variation", "?") if model_circuit_results else "?"
            if source == "descriptions":
                ckt = all_circuits.get(circuit_name, {})
                comp = ckt.get("complexity", "?")
                ctype = ckt.get("circuit_type", "?")
                print(f"{model:<25} {circuit_name:<25} {variation:<14} {comp:<9} {ctype:<20} {passes:<6} {unfair:<7} {fails:<6} {errors:<6}")
            else:
                print(f"{model:<25} {circuit_name:<20} {variation:<14} {passes:<6} {unfair:<7} {fails:<6} {errors:<6}")

    # Print analysis
    print_analysis(all_results)

    if bridge:
        bridge.disconnect()

    return 0


def print_analysis(all_results: list):
    """Print detailed analysis of results."""
    if not all_results:
        return

    print(f"\n{'='*60}")
    print("ANALYSIS")
    print(f"{'='*60}")

    # Success rate by model (overall)
    print("\n--- Success Rate by Model ---")
    models = sorted(set(r["model"] for r in all_results))
    for model in models:
        model_results = [r for r in all_results if r["model"] == model]
        total = len(model_results)
        passed = sum(1 for r in model_results if r.get("status") in ("pass", "pass_after_iteration"))
        pct = (passed / total * 100) if total > 0 else 0
        print(f"  {model:<25}: {passed}/{total} ({pct:.0f}%)")

    # Success rate by complexity
    if any("complexity" in r for r in all_results):
        print("\n--- Success Rate by Complexity ---")
        for comp in ["simple", "medium", "complex"]:
            comp_results = [r for r in all_results if r.get("complexity") == comp]
            if comp_results:
                total = len(comp_results)
                passed = sum(1 for r in comp_results if r.get("status") in ("pass", "pass_after_iteration"))
                pct = (passed / total * 100) if total > 0 else 0
                print(f"  {comp:<10}: {passed}/{total} ({pct:.0f}%)")

    # Success rate by circuit_type
    if any("circuit_type" in r for r in all_results):
        print("\n--- Success Rate by Circuit Type ---")
        types = sorted(set(r.get("circuit_type", "") for r in all_results if "circuit_type" in r))
        for ct in types:
            ct_results = [r for r in all_results if r.get("circuit_type") == ct]
            if ct_results:
                total = len(ct_results)
                passed = sum(1 for r in ct_results if r.get("status") in ("pass", "pass_after_iteration"))
                pct = (passed / total * 100) if total > 0 else 0
                print(f"  {ct:<20}: {passed}/{total} ({pct:.0f}%)")

    # Success rate by block count bucket
    print("\n--- Success Rate by Block Count ---")
    buckets = [(1, 5), (6, 10), (11, 20), (21, 9999)]
    for lo, hi in buckets:
        bucket_results = [r for r in all_results if lo <= r.get("block_count", 0) <= hi and "block_count" in r]
        if bucket_results:
            total = len(bucket_results)
            passed = sum(1 for r in bucket_results if r.get("status") in ("pass", "pass_after_iteration"))
            pct = (passed / total * 100) if total > 0 else 0
            label = f"{lo}-{hi}" if hi < 9999 else f"{lo}+"
            print(f"  {label:<8}: {passed}/{total} ({pct:.0f}%)")

    # Iteration improvement
    iter_results = [r for r in all_results if r.get("status") == "pass_after_iteration"]
    if iter_results:
        print(f"\n--- Iteration Improvement ---")
        print(f"  Attempts saved by iteration: {len(iter_results)}")
        for r in iter_results:
            print(f"  {r['model']:<25} {r['circuit']:<25} iterated {r.get('iterations_used', '?')} round(s)")


if __name__ == "__main__":
    sys.exit(main())
