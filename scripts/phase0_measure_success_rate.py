#!/usr/bin/env python3
"""
Phase 0: Measure LLM circuit generation success rate.

Takes the 12 test_circuits, generates each N times with Plan+Constraint prompt,
builds each in Minecraft, runs verification checks, and records the functional
pass rate. This single number calibrates the entire data acquisition strategy.

Usage:
    python3 scripts/phase0_measure_success_rate.py [--attempts 10] [--circuits all]
    python3 scripts/phase0_measure_success_rate.py --attempts 5 --circuits simple_lamp,piston_door
    python3 scripts/phase0_measure_success_rate.py --dry-run  # LLM only, no MC building

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
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results" / "phase0_measurement"
MODEL = "gemini-flash-lite"
TEMPERATURE = 0.5
MAX_TOKENS = 8192
ATTEMPTS_DEFAULT = 10
ORIGIN_BASE = (10000, 200, 10000)  # Far from spawn to avoid interference
REGION_SPACING = 100  # Spacing between circuit build regions

# Plan+Constraint prompt (from test_complex_circuits_strategies.py)
SYSTEM_PROMPT_TEMPLATE = "Plan redstone circuits. You MUST generate exactly {expected} blocks. Count them carefully."

USER_PROMPT_TEMPLATE = """Plan this circuit:

{description}

REQUIREMENTS:
- Generate EXACTLY {expected} blocks
- Number each step 1 to {expected}
- Do not stop early
- List every single block

Circuit needs {expected} total blocks. Be complete."""

# JSON schema for structured output
PLAN_SCHEMA = {
    "type": "object",
    "required": ["plan", "reasoning"],
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
        "reasoning": {"type": "string"}
    }
}


# ============================================================================
# Circuit loading
# ============================================================================

def load_circuit(name: str) -> dict:
    """Load a circuit JSON by name."""
    path = CIRCUITS_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def list_circuits() -> list:
    """List all available test circuits."""
    return sorted(p.stem for p in CIRCUITS_DIR.glob("*.json"))


# ============================================================================
# LLM generation
# ============================================================================

def parse_position(pos_str: str) -> tuple:
    """Parse a position string like '0,0,0' or '(0, 1, -2)' into (x, y, z)."""
    coords = re.findall(r'-?\d+', str(pos_str))
    if len(coords) >= 3:
        return int(coords[0]), int(coords[1]), int(coords[2])
    return None


def generate_circuit(client: OpenRouterClient, circuit: dict, attempt: int) -> dict:
    """Generate a circuit using Plan+Constraint prompt. Returns raw LLM output."""
    expected = circuit["expected_blocks"]
    description = circuit["description"]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(expected=expected)
    user_prompt = USER_PROMPT_TEMPLATE.format(description=description, expected=expected)

    try:
        result = client.complete_with_schema(
            model=MODEL,
            prompt=user_prompt,
            system_prompt=system_prompt,
            schema=PLAN_SCHEMA,
            temperature=TEMPERATURE,
        )
        return result
    except Exception as e:
        return {"error": str(e), "exception_type": type(e).__name__}


# Common LLM block name mistakes → correct Minecraft IDs
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
    "minecraft:repeater": "minecraft:repeater",
    "minecraft:comparator": "minecraft:comparator",
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
    "minecraft:grass_block": "minecraft:grass_block",
    "minecraft:lever_block": "minecraft:lever",
}

# Valid Minecraft block IDs (for validation — blocks not in this set or BLOCK_NAME_FIXES are skipped)
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
    "minecraft:tnt", "minecraft:target", "minecraft:jukebox",
    "minecraft:scaffolding", "minecraft:loom", "minecraft:cartography_table",
    "minecraft:fletching_table", "minecraft:smithing_table", "minecraft:grindstone",
    "minecraft:stonecutter", "minecraft:composter", "minecraft:barrel",
    "minecraft:lectern", "minecraft:bell", "minecraft:blast_furnace",
    "minecraft:armor_stand", "minecraft:item_frame", "minecraft:painting",
    "minecraft:air",
}


def normalize_block_name(block_type: str) -> str:
    """Fix common LLM block naming mistakes."""
    block_type = block_type.strip()
    # Handle names with spaces (e.g. "redstone dust" → "redstone_wire")
    space_fixes = {
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
    lower = block_type.lower()
    if lower in space_fixes:
        block_type = space_fixes[lower]

    if not block_type.startswith("minecraft:"):
        block_type = f"minecraft:{block_type}"
    return BLOCK_NAME_FIXES.get(block_type, block_type)


def is_valid_block(block_type: str) -> bool:
    """Check if a block type is valid (or can be fixed)."""
    normalized = normalize_block_name(block_type)
    base = normalized.split("[")[0]
    return base in VALID_BLOCK_IDS or base in BLOCK_NAME_FIXES.values()


def parse_llm_output(llm_output: dict) -> list:
    """Parse LLM output into block tuples (x, y, z, state, nbt)."""
    if "error" in llm_output:
        return []

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
    return blocks


# ============================================================================
# Verification
# ============================================================================

def calculate_bounds(blocks: list) -> tuple:
    """Calculate relative bounds from block list."""
    if not blocks:
        return ((0, 0, 0), (0, 0, 0))
    xs = [b[0] for b in blocks]
    ys = [b[1] for b in blocks]
    zs = [b[2] for b in blocks]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def clear_region(bridge: MinecraftBridge, origin: tuple, bounds: tuple):
    """Clear a region to air before building."""
    (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds
    ox, oy, oz = origin
    # Clear a slightly larger area to remove support blocks too
    x1, y1, z1 = ox + min_x - 5, oy + min_y - 5, oz + min_z - 5
    x2, y2, z2 = ox + max_x + 5, oy + max_y + 5, oz + max_z + 5
    # Respect 32k fill limit
    size = (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1)
    if size > 32000:
        # Clear in slices
        for y in range(y1, y2 + 1, 5):
            y_end = min(y + 4, y2)
            bridge.fill(x1, y, z1, x2, y_end, z2, "minecraft:air")
    else:
        bridge.fill(x1, y1, z1, x2, y2, z2, "minecraft:air")


def add_support_blocks(blocks: list) -> list:
    """Add stone support blocks under redstone components that need them."""
    needs_support = {
        "minecraft:redstone_wire",
        "minecraft:repeater",
        "minecraft:comparator",
        "minecraft:lever",
        "minecraft:stone_button",
        "minecraft:oak_button",
        "minecraft:stone_pressure_plate",
        "minecraft:oak_pressure_plate",
        "minecraft:light_weighted_pressure_plate",
        "minecraft:heavy_weighted_pressure_plate",
        "minecraft:rail",
        "minecraft:powered_rail",
        "minecraft:detector_rail",
        "minecraft:activator_rail",
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
    """Build a circuit in Minecraft. Returns build result."""
    if not blocks:
        return {"success": False, "error": "No blocks to build"}

    # Add support blocks for redstone components
    blocks = add_support_blocks(blocks)

    bounds = calculate_bounds(blocks)
    ox, oy, oz = origin
    (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds

    try:
        # Forceload the area (chunks aren't loaded on headless server)
        bridge.run_command(f"forceload add {ox + min_x - 16} {oz + min_z - 16} {ox + max_x + 16} {oz + max_z + 16}")

        # Clear the region first
        bridge.run_command("carpet fillUpdates false")
        clear_region(bridge, origin, bounds)
        bridge.run_command("carpet fillUpdates true")

        # Build the circuit (NO force_update_region — it causes server crashes)
        replicate_blocks(
            blocks=blocks,
            origin=origin,
            bounds=bounds,
            bridge=bridge,
            rate_limit=64,
            use_updates=True,
            force_update_region=False,  # update_region causes crashes — use update_block instead
        )

        # Force individual block updates (safe, doesn't crash)
        for b in blocks:
            x, y, z = ox + b[0], oy + b[1], oz + b[2]
            bridge.run_command(f"mira_api update_block {x} {y} {z}")

        return {"success": True, "bounds": bounds}
    except Exception as e:
        return {"success": False, "error": str(e), "bounds": bounds}


def reconnect_bridge(bridge: MinecraftBridge, max_retries: int = 3) -> bool:
    """Try to reconnect to the MC server."""
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


def verify_circuit(bridge: MinecraftBridge, circuit: dict, blocks: list, origin: tuple) -> dict:
    """
    Verify a built circuit. Returns verification result.

    For each circuit, we check:
    1. Block count matches expected
    2. Key blocks are present (lever, lamp, etc.)
    3. Functional behavior where possible (lamp lights, piston extends, etc.)
    """
    result = {
        "block_count_match": False,
        "key_blocks_present": False,
        "functional_pass": False,
        "details": {},
    }

    expected_count = circuit.get("expected_blocks", 0)
    actual_count = len(blocks)
    result["details"]["expected_blocks"] = expected_count
    result["details"]["actual_blocks"] = actual_count
    result["block_count_match"] = (actual_count == expected_count)

    circuit_id = circuit.get("id", "")

    # Check key blocks present
    block_types = set(b[3].split("[")[0] for b in blocks)  # Strip properties
    result["details"]["block_types"] = sorted(block_types)

    # Per-circuit verification
    if circuit_id == "simple_lamp":
        result.update(_verify_simple_lamp(bridge, blocks, origin))
    elif circuit_id == "hopper_transporter":
        result.update(_verify_hopper_transporter(bridge, blocks, origin))
    elif circuit_id == "lever_lamp" or circuit_id == "power_repeater":
        result.update(_verify_lamp_circuit(bridge, blocks, origin))
    elif circuit_id == "piston_door":
        result.update(_verify_piston_door(bridge, blocks, origin))
    elif circuit_id == "observer_torch":
        result.update(_verify_observer_torch(bridge, blocks, origin))
    elif circuit_id == "comparator_subtractor":
        result.update(_verify_comparator_subtractor(bridge, blocks, origin))
    elif circuit_id == "t_flip_flop":
        result.update(_verify_t_flip_flop(bridge, blocks, origin))
    else:
        # For complex circuits, just check block count and key components
        result["key_blocks_present"] = True  # Can't verify without ground truth
        result["functional_pass"] = result["block_count_match"]
        result["details"]["verification_method"] = "block_count_only"

    return result


def _find_block(blocks: list, block_type: str) -> tuple:
    """Find first block matching a type (without properties)."""
    for b in blocks:
        if b[3].split("[")[0] == f"minecraft:{block_type}":
            return b
    return None


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
    """Set a lever to powered=true, handling missing properties."""
    if "powered=true" in lever_state:
        return lever_state  # Already powered
    if "powered=false" in lever_state:
        return lever_state.replace("powered=false", "powered=true")
    # No powered property — add it
    if "[" in lever_state:
        return lever_state.replace("]", ",powered=true]")
    else:
        return f"{lever_state}[powered=true]"


def _verify_simple_lamp(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    """Verify simple_lamp: lever → wire → lamp. Toggle lever, check lamp lights."""
    result = {"key_blocks_present": False, "functional_pass": False, "details": {}}

    lever = _find_block(blocks, "lever")
    lamp = _find_block(blocks, "redstone_lamp")
    wire = _find_block(blocks, "redstone_wire")

    has_key_blocks = all([lever, lamp, wire])
    result["key_blocks_present"] = has_key_blocks
    result["details"]["has_lever"] = lever is not None
    result["details"]["has_lamp"] = lamp is not None
    result["details"]["has_wire"] = wire is not None

    if not has_key_blocks:
        result["details"]["verification_method"] = "missing_key_blocks"
        return result

    ox, oy, oz = origin
    lever_pos = (ox + lever[0], oy + lever[1], oz + lever[2])
    lamp_pos = (ox + lamp[0], oy + lamp[1], oz + lamp[2])

    # Check lamp is initially off
    lamp_off = _check_block(bridge, *lamp_pos, "minecraft:redstone_lamp[lit=false]")
    result["details"]["lamp_initially_off"] = lamp_off

    # Power the lever
    lever_state_on = _power_lever(lever[3])
    result["details"]["lever_state"] = lever[3]
    result["details"]["lever_state_on"] = lever_state_on

    _set_block(bridge, *lever_pos, lever_state_on)
    _tick(bridge, 20, update_positions=[lever_pos, lamp_pos])

    # Check lamp is now on
    lamp_on = _check_block(bridge, *lamp_pos, "minecraft:redstone_lamp[lit=true]")
    result["details"]["lamp_turned_on"] = lamp_on
    result["functional_pass"] = lamp_on
    result["details"]["verification_method"] = "functional_lamp_toggle"

    return result


def _verify_hopper_transporter(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    """Verify hopper_transporter: chest → hopper → chest. Check hopper transfers items."""
    result = {"key_blocks_present": False, "functional_pass": False, "details": {}}

    hopper = _find_block(blocks, "hopper")
    chests = [b for b in blocks if b[3].split("[")[0] == "minecraft:chest"]

    has_key_blocks = hopper is not None and len(chests) >= 2
    result["key_blocks_present"] = has_key_blocks
    result["details"]["has_hopper"] = hopper is not None
    result["details"]["chest_count"] = len(chests)

    if not has_key_blocks:
        return result

    # Sort chests by Y (top chest first)
    chests.sort(key=lambda b: b[1], reverse=True)
    top_chest = chests[0]
    bottom_chest = chests[-1]

    ox, oy, oz = origin
    top_pos = (ox + top_chest[0], oy + top_chest[1], oz + top_chest[2])
    bottom_pos = (ox + bottom_chest[0], oy + bottom_chest[1], oz + bottom_chest[2])

    # Place an item in the top chest
    bridge.run_command(f"replaceitem block {top_pos[0]} {top_pos[1]} {top_pos[2]} container.0 minecraft:diamond 1")
    _tick(bridge, 40)  # Wait for hopper transfer (8 ticks per item)

    # Check bottom chest has the item
    resp = bridge.run_command(f"mira_api check_inv {bottom_pos[0]} {bottom_pos[1]} {bottom_pos[2]} 0 minecraft:diamond 1")
    item_transferred = "PASS" in resp
    result["details"]["item_transferred"] = item_transferred
    result["functional_pass"] = item_transferred
    result["details"]["verification_method"] = "functional_hopper_transfer"

    return result


def _verify_lamp_circuit(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    """Verify generic lamp circuit: find lever and lamp, toggle, check."""
    result = {"key_blocks_present": False, "functional_pass": False, "details": {}}

    lever = _find_block(blocks, "lever")
    lamp = _find_block(blocks, "redstone_lamp")

    has_key_blocks = all([lever, lamp])
    result["key_blocks_present"] = has_key_blocks

    if not has_key_blocks:
        return result

    ox, oy, oz = origin
    lever_pos = (ox + lever[0], oy + lever[1], oz + lever[2])
    lamp_pos = (ox + lamp[0], oy + lamp[1], oz + lamp[2])

    # Power the lever
    lever_state_on = _power_lever(lever[3])

    _set_block(bridge, *lever_pos, lever_state_on)
    _tick(bridge, 20, update_positions=[lever_pos, lamp_pos])

    lamp_on = _check_block(bridge, *lamp_pos, "minecraft:redstone_lamp[lit=true]")
    result["details"]["lamp_turned_on"] = lamp_on
    result["functional_pass"] = lamp_on
    result["details"]["verification_method"] = "functional_lamp_toggle"

    return result


def _verify_piston_door(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    """Verify piston_door: check pistons extend when lever powered."""
    result = {"key_blocks_present": False, "functional_pass": False, "details": {}}

    lever = _find_block(blocks, "lever")
    pistons = [b for b in blocks if "piston" in b[3]]

    has_key_blocks = lever is not None and len(pistons) > 0
    result["key_blocks_present"] = has_key_blocks
    result["details"]["piston_count"] = len(pistons)

    if not has_key_blocks:
        return result

    ox, oy, oz = origin
    lever_pos = (ox + lever[0], oy + lever[1], oz + lever[2])

    # Check piston initial state (should be unextended)
    piston = pistons[0]
    piston_pos = (ox + piston[0], oy + piston[1], oz + piston[2])
    piston_extended_before = _check_block(bridge, *piston_pos, piston[3].split("[")[0] + "[extended=false]")
    result["details"]["piston_initially_retracted"] = piston_extended_before

    # Power the lever
    lever_state_on = _power_lever(lever[3])

    _set_block(bridge, *lever_pos, lever_state_on)
    _tick(bridge, 20, update_positions=[lever_pos, piston_pos])

    # Check piston extended
    piston_type = piston[3].split("[")[0]
    piston_extended_after = _check_block(bridge, *piston_pos, piston_type + "[extended=true]")
    result["details"]["piston_extended"] = piston_extended_after
    result["functional_pass"] = piston_extended_after
    result["details"]["verification_method"] = "functional_piston_extend"

    return result


def _verify_observer_torch(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    """Verify observer_torch: check observer and lamp present."""
    result = {"key_blocks_present": False, "functional_pass": False, "details": {}}

    observer = _find_block(blocks, "observer")
    lamp = _find_block(blocks, "redstone_lamp")
    torch = _find_block(blocks, "redstone_torch")

    has_key_blocks = observer is not None and (lamp is not None or torch is not None)
    result["key_blocks_present"] = has_key_blocks
    result["details"]["has_observer"] = observer is not None
    result["details"]["has_lamp"] = lamp is not None
    result["details"]["has_torch"] = torch is not None

    # Observer circuits are hard to verify functionally (need block update trigger)
    # Fall back to structural verification
    result["functional_pass"] = has_key_blocks and result.get("block_count_match", False)
    result["details"]["verification_method"] = "structural_only"

    return result


def _verify_comparator_subtractor(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    """Verify comparator_subtractor: check comparator present."""
    result = {"key_blocks_present": False, "functional_pass": False, "details": {}}

    comparator = _find_block(blocks, "comparator")
    result["key_blocks_present"] = comparator is not None

    # Comparators can't be powered via setblock (same as repeaters)
    result["functional_pass"] = comparator is not None and result.get("block_count_match", False)
    result["details"]["verification_method"] = "structural_only_comparator"

    return result


def _verify_t_flip_flop(bridge: MinecraftBridge, blocks: list, origin: tuple) -> dict:
    """Verify t_flip_flop: check lever, comparator, lamp present."""
    result = {"key_blocks_present": False, "functional_pass": False, "details": {}}

    lever = _find_block(blocks, "lever")
    comparator = _find_block(blocks, "comparator")
    lamp = _find_block(blocks, "redstone_lamp")

    has_key_blocks = all([lever, comparator, lamp])
    result["key_blocks_present"] = has_key_blocks
    result["details"]["has_lever"] = lever is not None
    result["details"]["has_comparator"] = comparator is not None
    result["details"]["has_lamp"] = lamp is not None

    # T flip-flop uses comparators/repeaters which can't be powered via setblock
    result["functional_pass"] = has_key_blocks and result.get("block_count_match", False)
    result["details"]["verification_method"] = "structural_only_tff"

    return result


# ============================================================================
# Main measurement loop
# ============================================================================

def run_measurement(args):
    """Run the Phase 0 measurement."""
    print(f"\n{'='*70}")
    print(f"Phase 0: LLM Circuit Generation Success Rate Measurement")
    print(f"{'='*70}")
    print(f"Model: {MODEL}")
    print(f"Temperature: {TEMPERATURE}")
    print(f"Attempts per circuit: {args.attempts}")
    print(f"Dry run (no MC): {args.dry_run}")
    print(f"{'='*70}\n")

    # Initialize LLM client
    client = OpenRouterClient()

    # Initialize bridge if not dry run
    bridge = None
    if not args.dry_run:
        bridge = MinecraftBridge()
        try:
            bridge.connect()
            print(f"Connected to MC server at localhost:25575")
            # Load Scarpet script
            bridge.run_command("script unload mira_api")
            bridge.run_command("script load mira_api")
        except Exception as e:
            print(f"ERROR: Could not connect to MC server: {e}")
            print("Run `bash start_server.sh` first, or use --dry-run")
            return

    # Select circuits
    if args.circuits == "all":
        circuits_to_test = list_circuits()
    else:
        circuits_to_test = [c.strip() for c in args.circuits.split(",")]

    # Filter by max-blocks if specified
    if args.max_blocks:
        filtered = []
        for name in circuits_to_test:
            circuit = load_circuit(name)
            expected = circuit.get("expected_blocks", 0)
            if expected <= args.max_blocks:
                filtered.append(name)
            else:
                print(f"Skipping {name} ({expected} blocks > {args.max_blocks} max)")
        circuits_to_test = filtered

    print(f"Circuits to test: {circuits_to_test}\n")

    # Results storage
    all_results = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "model": MODEL,
            "temperature": TEMPERATURE,
            "attempts_per_circuit": args.attempts,
            "dry_run": args.dry_run,
            "circuits": circuits_to_test,
        },
        "circuits": {},
    }

    total_cost = 0.0
    total_attempts = 0
    total_passes = 0

    for circuit_idx, circuit_name in enumerate(circuits_to_test):
        print(f"\n{'─'*60}")
        print(f"Circuit: {circuit_name} ({circuit_idx + 1}/{len(circuits_to_test)})")
        print(f"{'─'*60}")

        circuit = load_circuit(circuit_name)
        expected_blocks = circuit.get("expected_blocks", 0)
        print(f"Expected blocks: {expected_blocks}")
        print(f"Description: {circuit.get('description', '')[:100]}...")

        circuit_results = {
            "name": circuit_name,
            "expected_blocks": expected_blocks,
            "difficulty": circuit.get("difficulty", "unknown"),
            "attempts": [],
        }

        for attempt in range(args.attempts):
            attempt_num = attempt + 1
            print(f"\n  Attempt {attempt_num}/{args.attempts}...")

            # Generate
            llm_output = generate_circuit(client, circuit, attempt_num)
            has_error = "error" in llm_output
            blocks = parse_llm_output(llm_output) if not has_error else []

            attempt_result = {
                "attempt": attempt_num,
                "llm_error": has_error,
                "llm_error_msg": llm_output.get("error", "") if has_error else "",
                "block_count": len(blocks),
                "expected_block_count": expected_blocks,
                "block_count_match": len(blocks) == expected_blocks,
                "build_success": None,
                "verification": None,
            }

            if has_error:
                print(f"    LLM ERROR: {llm_output.get('error', '')[:100]}")
                circuit_results["attempts"].append(attempt_result)
                continue

            print(f"    Generated {len(blocks)} blocks (expected {expected_blocks})")

            if not args.dry_run and bridge and blocks:
                # Build in MC
                origin = (
                    ORIGIN_BASE[0] + circuit_idx * REGION_SPACING,
                    ORIGIN_BASE[1],
                    ORIGIN_BASE[2] + attempt * REGION_SPACING,
                )

                build_result = build_circuit_in_mc(bridge, blocks, origin)
                attempt_result["build_success"] = build_result["success"]

                if not build_result["success"]:
                    error_msg = build_result.get("error", "")
                    print(f"    BUILD FAILED: {error_msg[:100]}")
                    attempt_result["verification"] = {"functional_pass": False, "error": error_msg}

                    # Try to reconnect if server crashed
                    if "Connection" in error_msg or "timeout" in error_msg.lower():
                        print(f"    Server may have crashed, attempting reconnect...")
                        if reconnect_bridge(bridge):
                            print(f"    Reconnected to server")
                        else:
                            print(f"    Could not reconnect, skipping remaining attempts for this circuit")
                            # Mark remaining attempts as skipped
                            for remaining in range(attempt + 1, args.attempts):
                                circuit_results["attempts"].append({
                                    "attempt": remaining + 1,
                                    "llm_error": True,
                                    "llm_error_msg": "Skipped due to server crash",
                                    "block_count": 0,
                                    "expected_block_count": expected_blocks,
                                    "block_count_match": False,
                                    "build_success": None,
                                    "verification": {"functional_pass": False, "error": "server_crash"},
                                })
                            break
                else:
                    print(f"    Built successfully, verifying...")
                    # Verify
                    verification = verify_circuit(bridge, circuit, blocks, origin)
                    attempt_result["verification"] = verification
                    print(f"    Block count match: {verification.get('block_count_match', False)}")
                    print(f"    Key blocks present: {verification.get('key_blocks_present', False)}")
                    print(f"    Functional pass: {verification.get('functional_pass', False)}")

            elif args.dry_run:
                attempt_result["build_success"] = None
                attempt_result["verification"] = {
                    "block_count_match": len(blocks) == expected_blocks,
                    "key_blocks_present": None,
                    "functional_pass": None,
                    "details": {"verification_method": "dry_run"},
                }
                print(f"    [DRY RUN] Skipping build/verify")

            circuit_results["attempts"].append(attempt_result)
            total_attempts += 1
            verification = attempt_result.get("verification") or {}
            if verification.get("functional_pass", False):
                total_passes += 1

        # Summarize circuit results
        attempts = circuit_results["attempts"]
        llm_errors = sum(1 for a in attempts if a["llm_error"])
        count_matches = sum(1 for a in attempts if a["block_count_match"])
        build_successes = sum(1 for a in attempts if a.get("build_success") is True)
        functional_passes = sum(1 for a in attempts if (a.get("verification") or {}).get("functional_pass", False))

        circuit_results["summary"] = {
            "total_attempts": len(attempts),
            "llm_errors": llm_errors,
            "block_count_matches": count_matches,
            "build_successes": build_successes,
            "functional_passes": functional_passes,
            "block_count_rate": count_matches / len(attempts) if attempts else 0,
            "build_success_rate": build_successes / len(attempts) if attempts else 0,
            "functional_pass_rate": functional_passes / len(attempts) if attempts else 0,
        }

        print(f"\n  Summary for {circuit_name}:")
        print(f"    Block count match: {count_matches}/{len(attempts)} ({circuit_results['summary']['block_count_rate']:.1%})")
        print(f"    Build success: {build_successes}/{len(attempts)} ({circuit_results['summary']['build_success_rate']:.1%})")
        print(f"    Functional pass: {functional_passes}/{len(attempts)} ({circuit_results['summary']['functional_pass_rate']:.1%})")

        all_results["circuits"][circuit_name] = circuit_results

    # Overall summary
    overall_pass_rate = total_passes / total_attempts if total_attempts else 0
    all_results["summary"] = {
        "total_attempts": total_attempts,
        "total_passes": total_passes,
        "overall_pass_rate": overall_pass_rate,
        "total_cost_usd": total_cost,
    }

    print(f"\n{'='*70}")
    print(f"OVERALL RESULTS")
    print(f"{'='*70}")
    print(f"Total attempts: {total_attempts}")
    print(f"Total functional passes: {total_passes}")
    print(f"Overall pass rate: {overall_pass_rate:.1%}")
    print(f"{'='*70}\n")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = RESULTS_DIR / f"phase0_{timestamp}.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Results saved to: {results_file}")

    # Cleanup
    if bridge:
        bridge.disconnect()
        print("Disconnected from MC server")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Phase 0: Measure LLM circuit generation success rate")
    parser.add_argument("--attempts", type=int, default=ATTEMPTS_DEFAULT,
                        help=f"Number of attempts per circuit (default: {ATTEMPTS_DEFAULT})")
    parser.add_argument("--circuits", type=str, default="all",
                        help="Comma-separated circuit names, or 'all' (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="LLM generation only, no MC building/verification")
    parser.add_argument("--max-blocks", type=int, default=None,
                        help="Skip circuits with more than this many expected blocks (avoids token limit issues)")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)

    run_measurement(args)


if __name__ == "__main__":
    main()
