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

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))

from simulation.llm_client import OpenRouterClient
from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_blocks


# ============================================================================
# Configuration
# ============================================================================

CIRCUITS_DIR = PROJECT_ROOT / "evaluation" / "test_circuits"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results" / "phase0_multi_model"
TEMPERATURE = 0.5
MAX_TOKENS = 8192
ATTEMPTS_DEFAULT = 3
ORIGIN_BASE = (10000, 200, 10000)
REGION_SPACING = 100

# Models to test (subset of MODELS dict — the affordable ones)
DEFAULT_MODELS = [
    "deepseek-v4-flash",      # $0.09/$0.18 — best value
    "qwen3-coder-30b",        # $0.07/$0.27 — code-specialized
    "deepseek-v4-pro",        # $0.71/$1.43 — high quality
    "llama-3.3-70b",          # $0.13/$0.40 — reliable 70B
    "glm-5.2",                # $0.30/$0.94 — engineering focus
    "gemini-flash-lite",      # $0.25/$1.50 — Google route (GA, not preview)
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
# Circuit loading
# ============================================================================

def load_circuit(name: str) -> dict:
    path = CIRCUITS_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def list_circuits() -> list:
    return sorted(p.stem for p in CIRCUITS_DIR.glob("*.json"))


# ============================================================================
# LLM generation
# ============================================================================

def generate_circuit_and_verification(
    client: OpenRouterClient,
    model: str,
    circuit: dict,
) -> dict:
    """Generate circuit + verification spec in one LLM call."""
    expected = circuit["expected_blocks"]
    description = circuit["description"]

    user_prompt = USER_PROMPT_TEMPLATE.format(description=description, expected=expected)

    try:
        result = client.complete_with_schema(
            model=model,
            prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            schema=COMBINED_SCHEMA,
            temperature=TEMPERATURE,
            reasoning_effort="none",  # Critical for structured output
        )
        return result
    except Exception as e:
        return {"error": str(e), "exception_type": type(e).__name__}


def iterate_on_failure(
    client: OpenRouterClient,
    model: str,
    circuit: dict,
    previous_output: dict,
    failure_info: dict,
) -> dict:
    """Send failure back to model for repair."""
    expected = circuit["expected_blocks"]
    description = circuit["description"]

    # Build a repair prompt that includes the failure info
    repair_prompt = f"""Your previous attempt at this circuit FAILED verification. Please fix it.

Circuit description: {description}
Expected blocks: {expected}

Your previous plan had {len(previous_output.get("plan", []))} blocks.
Failure reason: {failure_info.get("reason", "unknown")}
Failure details: {json.dumps(failure_info.get("details", {}), indent=2)[:1000]}

Please regenerate the circuit with fixes. Make sure:
1. The plan has EXACTLY {expected} blocks
2. The verification spec is FAIR (fails if broken, passes if working)
3. All block names are valid Minecraft IDs
4. Support blocks are included for redstone components"""

    try:
        result = client.complete_with_schema(
            model=model,
            prompt=repair_prompt,
            system_prompt=SYSTEM_PROMPT,
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
    """Add stone support blocks under redstone components."""
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


def build_circuit_in_mc(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    if not blocks:
        return {"success": False, "error": "No blocks to build"}

    blocks = add_support_blocks(blocks)
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

        # Force individual block updates (safe, doesn't crash)
        for b in blocks:
            x, y, z = ox + b[0], oy + b[1], oz + b[2]
            bridge.run_command(f"mira_api update_block {x} {y} {z}")

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
    """Run tick freeze → step → unfreeze. Optionally update blocks first."""
    if update_positions:
        for pos in update_positions:
            bridge.run_command(f"mira_api update_block {pos[0]} {pos[1]} {pos[2]}")
    bridge.run_command("tick freeze")
    bridge.run_command(f"tick step {ticks}")
    bridge.run_command("tick unfreeze")
    time.sleep(0.3)


def _check_block(bridge: MinecraftBridge, x: int, y: int, z: int, expected: str) -> bool:
    """Check a block at absolute coordinates."""
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
            _set_block(bridge, *abs_pos, state)
            action_positions.append(abs_pos)
        elif action_type == "place_item":
            # place_item: state is "minecraft:diamond 1" format
            bridge.run_command(f"replaceitem block {abs_pos[0]} {abs_pos[1]} {abs_pos[2]} container.0 {state}")
        elif action_type == "wait":
            _tick(bridge, 10)
    result["details"]["actions_executed"] = len(actions)

    # 3. Tick
    tick_steps = verification.get("tick_steps", 20)
    _tick(bridge, tick_steps, update_positions=action_positions)

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
    else:
        # Unpower all levers (breaks the circuit)
        for lever in levers:
            unpowered = _unpower_lever(lever[3])
            abs_pos = (ox + lever[0], oy + lever[1], oz + lever[2])
            _set_block(bridge, *abs_pos, unpowered)

    # Tick to let the broken state propagate
    tick_steps = verification.get("tick_steps", 20)
    _tick(bridge, tick_steps)

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
            "timestamp": datetime.now().isoformat(),
        }

        # 1. Generate circuit + verification
        llm_output = generate_circuit_and_verification(client, model, circuit)
        attempt_result["llm_output"] = llm_output

        if "error" in llm_output:
            print(f"LLM ERROR: {llm_output['error'][:80]}")
            attempt_result["status"] = "llm_error"
            results.append(attempt_result)
            continue

        # 2. Parse blocks + verification
        blocks, verification = parse_llm_output(llm_output)
        attempt_result["block_count"] = len(blocks)
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

            if negative_result["is_fair"]:
                print("PASS (fair)")
                attempt_result["status"] = "pass"
            else:
                print("UNFAIR (test passes on broken circuit)")
                attempt_result["status"] = "unfair_test"
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
                    llm_output = iterate_on_failure(client, model, circuit, llm_output, failure_info)
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
                            attempt_result["iterations_used"] = round_num
                            break
                        else:
                            print("UNFAIR after iteration")
                            attempt_result["status"] = "unfair_after_iteration"
                    else:
                        print("FAIL again")

        results.append(attempt_result)

    return results


def main():
    parser = argparse.ArgumentParser(description="Phase 0 multi-model measurement")
    parser.add_argument("--models", type=str, default="deepseek-v4-flash",
                        help="Comma-separated model names (or 'all')")
    parser.add_argument("--circuits", type=str, default="simple_lamp",
                        help="Comma-separated circuit names (or 'all')")
    parser.add_argument("--attempts", type=int, default=ATTEMPTS_DEFAULT,
                        help="Number of attempts per model per circuit")
    parser.add_argument("--iterate", type=int, default=0,
                        help="Number of iteration rounds on failure (0=disabled)")
    parser.add_argument("--dry-run", action="store_true",
                        help="LLM only, no MC building")
    parser.add_argument("--max-blocks", type=int, default=50,
                        help="Skip circuits with more than this many blocks")
    args = parser.parse_args()

    # Parse models
    if args.models == "all":
        models = DEFAULT_MODELS
    else:
        models = [m.strip() for m in args.models.split(",")]

    # Parse circuits
    if args.circuits == "all":
        circuits = list_circuits()
    else:
        circuits = [c.strip() for c in args.circuits.split(",")]

    # Filter by max-blocks
    all_circuits = {name: load_circuit(name) for name in circuits}
    circuits = [name for name, c in all_circuits.items() if c.get("expected_blocks", 0) <= args.max_blocks]
    print(f"Models: {models}")
    print(f"Circuits (max {args.max_blocks} blocks): {circuits}")
    print(f"Attempts per model per circuit: {args.attempts}")
    print(f"Iteration rounds: {args.iterate}")
    print(f"Dry run: {args.dry_run}")
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
            print(f"\nCircuit: {circuit_name} ({circuit.get('expected_blocks', 0)} blocks)")
            results = measure_model_on_circuit(
                client=client,
                model=model,
                circuit=circuit,
                attempts=args.attempts,
                bridge=bridge,
                iterate_rounds=args.iterate,
                dry_run=args.dry_run,
            )
            all_results.extend(results)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = RESULTS_DIR / f"phase0_multi_model_{timestamp}.json"
    with open(results_path, "w") as f:
        json.dump({
            "timestamp": timestamp,
            "models": models,
            "circuits": circuits,
            "attempts": args.attempts,
            "iterate_rounds": args.iterate,
            "results": all_results,
        }, f, indent=2)
    print(f"\nResults saved to {results_path}")

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Model':<25} {'Circuit':<20} {'Pass':<6} {'Unfair':<7} {'Fail':<6} {'Error':<6}")
    print("-" * 70)
    for model in models:
        for circuit_name in circuits:
            model_circuit_results = [r for r in all_results if r["model"] == model and r["circuit"] == circuit_name]
            passes = sum(1 for r in model_circuit_results if r.get("status") in ("pass", "pass_after_iteration"))
            unfair = sum(1 for r in model_circuit_results if "unfair" in r.get("status", ""))
            fails = sum(1 for r in model_circuit_results if r.get("status") == "fail")
            errors = sum(1 for r in model_circuit_results if "error" in r.get("status", "") or r.get("status") == "server_crash")
            print(f"{model:<25} {circuit_name:<20} {passes:<6} {unfair:<7} {fails:<6} {errors:<6}")

    if bridge:
        bridge.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
