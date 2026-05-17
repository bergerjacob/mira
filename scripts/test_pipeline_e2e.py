#!/usr/bin/env python3
"""
End-to-end test of the corruption → repair reasoning pipeline.

Uses structured output (json_schema) for reliable JSON from LLMs.
Tests the full flow:
1. Take a circuit (synthetic or real)
2. Corrupt it using CircuitCorruptor
3. Send to LLM with JSON schema enforcement asking for repair reasoning
4. Validate the repair output

Also tests deconstruction quality on diverse circuits.
"""

import json
import os
import sys
import time
import copy
import random

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_mining.corruptor import CircuitCorruptor
from simulation.llm_client import OpenRouterClient, ChatMessage

# ── JSON Schemas for structured output ────────────────────────────────────────

REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {
            "type": "string",
            "description": "What is wrong with the corrupted circuit. Be specific about block positions and states."
        },
        "repair_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pos": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "[x, y, z] position of the block to repair"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["place", "remove", "replace"],
                        "description": "The repair action to take"
                    },
                    "target_state": {
                        "type": "string",
                        "description": "The correct block state string (for place/replace actions)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this repair is needed"
                    }
                },
                "required": ["pos", "action", "target_state", "reason"],
                "additionalProperties": False
            },
            "description": "List of repair actions to fix the corrupted circuit"
        },
        "corruption_type": {
            "type": "string",
            "enum": ["wire_removed", "component_rotated", "power_source_removed", "unknown"],
            "description": "The type of corruption: wire_removed (redstone dust broken), component_rotated (facing direction changed), power_source_removed (torch/lever/block removed), or unknown"
        }
    },
    "required": ["diagnosis", "repair_steps", "corruption_type"],
    "additionalProperties": False
}

DECONSTRUCTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Explain why this layer makes sense to remove as a unit"
        },
        "remove_blocks": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "[x, y, z] coordinate"
            },
            "description": "List of [x, y, z] coordinates to remove"
        },
        "layer_name": {
            "type": "string",
            "description": "A short name for this layer (e.g., 'power supply', 'signal path', 'output mechanism')"
        }
    },
    "required": ["reasoning", "remove_blocks", "layer_name"],
    "additionalProperties": False
}

# ── Test circuits (synthetic, small, diverse) ─────────────────────────────────

SYNTHETIC_CIRCUITS = {
    "simple_lamp": {
        "description": "A simple lever-powered redstone lamp",
        "blocks": [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:stone", None),
            (2, 0, 0, "minecraft:stone", None),
            (0, 1, 0, "minecraft:lever[face=floor,facing=east,powered=false]", None),
            (1, 1, 0, "minecraft:redstone_wire[east=side,power=15,west=side]", None),
            (2, 1, 0, "minecraft:redstone_lamp[lit=false]", None),
        ],
    },
    "t_flip_flop": {
        "description": "A T-flip-flop using a sticky piston and redstone torch",
        "blocks": [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:stone", None),
            (2, 0, 0, "minecraft:stone", None),
            (3, 0, 0, "minecraft:stone", None),
            (0, 1, 0, "minecraft:lever[face=floor,facing=east,powered=false]", None),
            (1, 1, 0, "minecraft:redstone_wire[east=side,power=0,west=side]", None),
            (2, 1, 0, "minecraft:redstone_torch[facing=east,lit=true]", None),
            (3, 1, 0, "minecraft:sticky_piston[facing=east,extended=false]", None),
            (2, 2, 0, "minecraft:stone", None),
            (2, 1, 1, "minecraft:redstone_wire[north=side,south=side,power=15]", None),
        ],
    },
    "repeater_chain": {
        "description": "A redstone repeater chain with 4-tick delay",
        "blocks": [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:stone", None),
            (2, 0, 0, "minecraft:stone", None),
            (3, 0, 0, "minecraft:stone", None),
            (4, 0, 0, "minecraft:stone", None),
            (5, 0, 0, "minecraft:stone", None),
            (0, 1, 0, "minecraft:lever[face=floor,facing=east,powered=false]", None),
            (1, 1, 0, "minecraft:redstone_wire[east=side,power=15,west=side]", None),
            (2, 1, 0, "minecraft:repeater[delay=1,facing=east,locked=false,powered=false]", None),
            (3, 1, 0, "minecraft:redstone_wire[east=side,power=15,west=side]", None),
            (4, 1, 0, "minecraft:repeater[delay=1,facing=east,locked=false,powered=false]", None),
            (5, 1, 0, "minecraft:redstone_lamp[lit=false]", None),
        ],
    },
    "observer_pulse": {
        "description": "An observer-based pulse generator that triggers a note block",
        "blocks": [
            (0, 0, 0, "minecraft:stone", None),
            (1, 0, 0, "minecraft:stone", None),
            (2, 0, 0, "minecraft:stone", None),
            (0, 1, 0, "minecraft:observer[facing=east,powered=false]", None),
            (1, 1, 0, "minecraft:redstone_wire[east=side,power=15,west=side]", None),
            (2, 1, 0, "minecraft:note_block[instrument=harp,note=0,powered=false]", None),
        ],
    },
}

# ── Prompt templates ──────────────────────────────────────────────────────────

REPAIR_SYSTEM_PROMPT = """You are a Minecraft redstone circuit repair expert. You will be given:
1. The ORIGINAL circuit (correct, working version)
2. The CORRUPTED circuit (with one or more faults introduced)

Identify what was corrupted and provide a repair plan. Be specific about block positions and states."""

REPAIR_USER_TEMPLATE = """## ORIGINAL CIRCUIT (correct version)
{original_blocks}

## CORRUPTED CIRCUIT (broken version)
{corrupted_blocks}

## DESCRIPTION
{description}

Identify the corruption and provide a repair plan."""

DECON_SYSTEM_PROMPT = """You are reverse-engineering a Minecraft redstone circuit. You will be given a list of blocks that make up a working circuit.

Identify ONE logical layer of blocks that can be removed together (they form a functional unit or are at the same structural level)."""

DECON_USER_TEMPLATE = """## Circuit: {description}

## Blocks
{blocks}

Identify one logical layer to remove."""


def format_blocks(blocks):
    """Format block list for LLM prompt."""
    lines = []
    for x, y, z, state, nbt in blocks:
        lines.append(f"({x},{y},{z}) {state}")
    return "\n".join(lines)


def test_repair_pipeline(client, model_id, circuits_to_test):
    """Test the corruption → repair reasoning pipeline using structured output."""
    results = []
    
    for name, circuit in circuits_to_test.items():
        blocks = circuit["blocks"]
        description = circuit["description"]
        
        # Corrupt the circuit
        corruptor = CircuitCorruptor(blocks)
        corrupted_blocks, modifications = corruptor.corrupt()
        
        if not modifications:
            print(f"  [{name}] Could not corrupt (no corruptible blocks), skipping")
            continue
        
        mod = modifications[0]
        mod_type = mod["type"]
        # Map internal corruption types to schema enum values
        type_map = {
            "break_wire": "wire_removed",
            "rotate_component": "component_rotated",
            "remove_source": "power_source_removed",
        }
        actual_type = type_map.get(mod_type, "unknown")
        print(f"\n  [{name}] Corruption: {mod['type']} at {mod['pos']}")
        print(f"    Original: {mod['original']}")
        print(f"    Changed to: {mod['new']}")
        
        # Format prompts
        original_str = format_blocks(blocks)
        corrupted_str = format_blocks(corrupted_blocks)
        
        user_prompt = REPAIR_USER_TEMPLATE.format(
            original_blocks=original_str,
            corrupted_blocks=corrupted_str,
            description=description,
        )
        
        start = time.time()
        try:
            parsed = client.complete_with_schema(
                model=model_id,
                prompt=user_prompt,
                system_prompt=REPAIR_SYSTEM_PROMPT,
                schema=REPAIR_SCHEMA,
                temperature=0.3,
            )
            elapsed = time.time() - start
            
            result = {
                "circuit": name,
                "corruption_type": mod_type,
                "corruption_pos": list(mod["pos"]),
                "original_state": mod["original"],
                "corrupted_state": mod["new"],
                "model": model_id,
                "elapsed_s": round(elapsed, 2),
                "parsed_json": True,
                "diagnosis": parsed.get("diagnosis", ""),
                "repair_steps": len(parsed.get("repair_steps", [])),
                "detected_corruption_type": parsed.get("corruption_type", ""),
                "correct_detection": parsed.get("corruption_type", "") == actual_type,
            }
            results.append(result)
            
            print(f"    {elapsed:.1f}s | diagnosis: {parsed.get('diagnosis', '')[:80]}...")
            print(f"    Repair steps: {len(parsed.get('repair_steps', []))}")
            print(f"    Detected type: {parsed.get('corruption_type', 'N/A')} (actual: {actual_type})")
            print(f"    Correct: {parsed.get('corruption_type', '') == actual_type}")
            
            # Check if repair steps match the actual corruption
            if parsed.get("repair_steps"):
                step = parsed["repair_steps"][0]
                print(f"    First repair: {step.get('action')} at {step.get('pos')} → {step.get('target_state', '')[:60]}")
            
        except Exception as e:
            elapsed = time.time() - start
            print(f"    FAILED: {e}")
            results.append({
                "circuit": name,
                "model": model_id,
                "error": str(e),
                "elapsed_s": round(elapsed, 2),
                "parsed_json": False,
            })
        
        time.sleep(1)
    
    return results


def test_deconstruction_pipeline(client, model_id, circuits_to_test):
    """Test the deconstruction reasoning pipeline using structured output."""
    results = []
    
    for name, circuit in circuits_to_test.items():
        blocks = circuit["blocks"]
        description = circuit["description"]
        
        blocks_str = format_blocks(blocks)
        
        user_prompt = DECON_USER_TEMPLATE.format(
            description=description,
            blocks=blocks_str,
        )
        
        start = time.time()
        try:
            parsed = client.complete_with_schema(
                model=model_id,
                prompt=user_prompt,
                system_prompt=DECON_SYSTEM_PROMPT,
                schema=DECONSTRUCTION_SCHEMA,
                temperature=0.3,
            )
            elapsed = time.time() - start
            
            result = {
                "circuit": name,
                "model": model_id,
                "elapsed_s": round(elapsed, 2),
                "parsed_json": True,
                "reasoning": parsed.get("reasoning", "")[:100],
                "remove_count": len(parsed.get("remove_blocks", [])),
                "layer_name": parsed.get("layer_name", ""),
            }
            results.append(result)
            
            print(f"\n  [{name}] {elapsed:.1f}s | layer: {parsed.get('layer_name', 'N/A')}")
            print(f"    Remove {len(parsed.get('remove_blocks', []))} blocks")
            print(f"    Reasoning: {parsed.get('reasoning', 'N/A')[:120]}...")
            
        except Exception as e:
            elapsed = time.time() - start
            print(f"    FAILED: {e}")
            results.append({"circuit": name, "model": model_id, "error": str(e), "elapsed_s": round(elapsed, 2), "parsed_json": False})
        
        time.sleep(1)
    
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="End-to-end pipeline test with structured output")
    parser.add_argument("--model", default="google/gemini-3.1-flash-lite-preview", help="Model to test")
    parser.add_argument("--task", choices=["repair", "decon", "both"], default="both", help="Which task to test")
    parser.add_argument("--circuit", default=None, help="Specific circuit name to test (default: all synthetic)")
    parser.add_argument("--use-real", action="store_true", help="Use real circuits from block_list.jsonl instead of synthetic")
    args = parser.parse_args()
    
    client = OpenRouterClient()
    
    # Select circuits
    if args.use_real:
        circuits = {}
        with open("data/training/converted/block_list.jsonl") as f:
            for line in f:
                entry = json.loads(line)
                if len(entry["blocks"]) <= 50:
                    blocks = [(b["x"], b["y"], b["z"], b["state"], None) for b in entry["blocks"]]
                    circuits[entry["id"]] = {
                        "description": entry["description"],
                        "blocks": blocks,
                    }
        print(f"Loaded {len(circuits)} real circuits (≤50 blocks)")
    else:
        circuits = SYNTHETIC_CIRCUITS
    
    if args.circuit:
        circuits = {k: v for k, v in circuits.items() if k == args.circuit}
    
    print(f"\n{'='*70}")
    print(f"MIRA End-to-End Pipeline Test (Structured Output)")
    print(f"Model: {args.model}")
    print(f"Circuits: {len(circuits)}")
    print(f"{'='*70}")
    
    all_results = {}
    
    if args.task in ("repair", "both"):
        print(f"\n--- CORRUPTION → REPAIR TEST ---")
        repair_results = test_repair_pipeline(client, args.model, circuits)
        all_results["repair"] = repair_results
        
        correct = sum(1 for r in repair_results if r.get("correct_detection"))
        parsed = sum(1 for r in repair_results if r.get("parsed_json"))
        total = len(repair_results)
        print(f"\n  Repair Summary: {parsed}/{total} parsed JSON, {correct}/{total} correctly detected corruption type")
    
    if args.task in ("decon", "both"):
        print(f"\n--- DECONSTRUCTION TEST ---")
        decon_results = test_deconstruction_pipeline(client, args.model, circuits)
        all_results["decon"] = decon_results
        
        parsed = sum(1 for r in decon_results if r.get("parsed_json"))
        total = len(decon_results)
        print(f"\n  Decon Summary: {parsed}/{total} parsed JSON")
    
    # Save results
    out_path = "data/training/pipeline_test_results.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()