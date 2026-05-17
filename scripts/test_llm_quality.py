#!/usr/bin/env python3
"""
MIRA LLM Quality Test — Compare models on circuit description & verification generation.

Tests multiple models on:
1. Circuit description generation (from block list)
2. Verification contract generation (from block list + metadata)
3. Deconstruction reasoning (from block list)

Usage:
    source .venv/bin/activate && source .env
    python3 scripts/test_llm_quality.py [--max-blocks 100] [--models qwen3.5-122b deepseek-v4-flash] [--circuit N]
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.llm_client import OpenRouterClient, ChatMessage

# ── Model definitions ──────────────────────────────────────────────────────
# Full OpenRouter model IDs (not in MODELS dict, use directly)
MODELS = {
    "qwen3.5-122b": "qwen/qwen3.5-122b-a10b",
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "gemini-flash-lite": "google/gemini-3.1-flash-lite-preview",
    "kimi-k2.6": "moonshotai/kimi-k2.6",
}

# ── Prompt templates ───────────────────────────────────────────────────────

DESCRIPTION_SYSTEM = """You are an expert Minecraft Redstone Engineer. Given a list of blocks from a Minecraft circuit, write a clear, concise description of what the circuit does and how it works.

Focus on:
- What the circuit's purpose is (e.g., "iron farm", "villager breeder", "piston door")
- Key mechanical components and how they interact
- Input/output behavior

Be specific about block interactions. Keep it under 3 sentences."""

DESCRIPTION_USER = """[CIRCUIT METADATA]
Name: {name}
Category: {category}

[BLOCK LIST] ({block_count} blocks)
{blocks_str}

Write a clear description of this circuit's purpose and how it works."""

VERIFY_SYSTEM = """You are an expert Minecraft Redstone Engineer.

Your goal is to write a Python verification script for a Redstone circuit using the MIRA_API.

The MIRA Verification API:
1. `ctx.set_block(pos, block_state)`: Places blocks/levers.
2. `ctx.tick(ticks)`: Advances the game simulation.
3. `ctx.assert_block(pos, block_id)`: Checks if a block matches the ID (throws AssertionError if not).
4. `ctx.assert_power(pos, min_level)`: Checks redstone power level.

Instructions:
1. Analyze the provided Block List.
2. Identify the INPUT (Levers, Buttons, Observers) and the OUTPUT (Pistons, Lamps, Doors).
3. Write a `verify_circuit(ctx)` function that:
   - Asserts the initial state (e.g., Door is Closed).
   - Triggers the Input (e.g., Flicks Lever).
   - Ticks the engine (allow 10-20 ticks for propagation).
   - Asserts the final state (e.g., Door is Open).

4. Output ONLY the Python code. No markdown, no explanations."""

VERIFY_USER = """[METADATA]
Name: {name}
Description: {description}

[BLOCK_LIST] ({block_count} blocks)
{blocks_str}

[TASK]
Write the `verify_circuit(ctx)` function."""

DECONSTRUCT_SYSTEM = """You are a Reverse-Engineering Architect.

You are given a list of blocks representing a Minecraft Redstone Machine.

Your Goal:
Identify a "Logical Layer" of blocks that can be REMOVED to return the machine to a previous, simpler state.

We are simulating the construction process in reverse.

Rules for Removal:
1. Remove Output/Decoration First: Frames, lamps, and final pushed blocks are usually the last things added.
2. Remove Control Wiring Second: Redstone dust, levers, and buttons are usually added after the machinery.
3. Remove Core Mechanisms Last: Pistons, observers, and droppers are usually the "Skeleton" placed first.
4. Do not break dependency chains: If a Repeater sits on a Stone block, do not remove the Stone block while the Repeater is still there. Remove the Repeater first.

Output Format:
Return a JSON object:
{
  "reasoning": "Explaining why this layer is the next logical step to remove.",
  "remove_blocks": [[x,y,z], [x,y,z], ...]
}"""

DECONSTRUCT_USER = """[CURRENT_STATE] ({block_count} blocks)
{blocks_str}

[TASK]
Identify the next set of blocks to delete to strip this down to the "Skeleton"."""


def format_blocks(blocks: list[dict], max_blocks: int = 200) -> str:
    """Format block list for prompt."""
    lines = []
    for b in blocks[:max_blocks]:
        lines.append(f"({b['x']}, {b['y']}, {b['z']}): {b['state']}")
    if len(blocks) > max_blocks:
        lines.append(f"... and {len(blocks) - max_blocks} more blocks")
    return "\n".join(lines)


def load_circuits(max_blocks: int = 100) -> list[dict]:
    """Load circuits from converted dataset."""
    path = Path("data/training/converted/block_list.jsonl")
    if not path.exists():
        print(f"ERROR: {path} not found. Run scripts/convert_dataset.py first.")
        sys.exit(1)

    circuits = []
    with open(path) as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                if len(entry.get("blocks", [])) <= max_blocks:
                    circuits.append(entry)

    # Sort by block count (smallest first)
    circuits.sort(key=lambda c: len(c.get("blocks", [])))
    return circuits


def test_model(client: OpenRouterClient, model_key: str, model_id: str, circuit: dict):
    """Test a model on all three tasks for one circuit."""
    results = {"model": model_key, "model_id": model_id, "circuit": circuit["id"],
               "block_count": len(circuit["blocks"]), "tasks": {}}

    blocks_str = format_blocks(circuit["blocks"])
    name = circuit.get("id", "Unknown").replace("discord_", "")
    desc = circuit.get("description", "")
    category = circuit.get("category", "unknown")

    # ── Task 1: Description Generation ──────────────────────────────────
    prompt = DESCRIPTION_USER.format(
        name=name, category=category,
        block_count=len(circuit["blocks"]), blocks_str=blocks_str
    )
    try:
        start = time.time()
        resp = client.chat(
            model=model_id, messages=[ChatMessage(role="user", content=prompt)],
            system_prompt=DESCRIPTION_SYSTEM, temperature=0.3, max_tokens=1024
        )
        elapsed = time.time() - start
        results["tasks"]["description"] = {
            "content": resp.content or "",
            "usage": resp.usage,
            "elapsed_s": round(elapsed, 2),
            "error": None
        }
    except Exception as e:
        results["tasks"]["description"] = {"error": str(e)}

    time.sleep(1)  # Rate limiting

    # ── Task 2: Verification Contract ────────────────────────────────────
    prompt = VERIFY_USER.format(
        name=name, description=desc,
        block_count=len(circuit["blocks"]), blocks_str=blocks_str
    )
    try:
        start = time.time()
        resp = client.chat(
            model=model_id, messages=[ChatMessage(role="user", content=prompt)],
            system_prompt=VERIFY_SYSTEM, temperature=0.1, max_tokens=4096
        )
        elapsed = time.time() - start
        results["tasks"]["verify_contract"] = {
            "content": resp.content or "",
            "usage": resp.usage,
            "elapsed_s": round(elapsed, 2),
            "error": None
        }
    except Exception as e:
        results["tasks"]["verify_contract"] = {"error": str(e)}

    time.sleep(1)

    # ── Task 3: Deconstruction Reasoning ─────────────────────────────────
    prompt = DECONSTRUCT_USER.format(
        block_count=len(circuit["blocks"]), blocks_str=blocks_str
    )
    try:
        start = time.time()
        resp = client.chat(
            model=model_id, messages=[ChatMessage(role="user", content=prompt)],
            system_prompt=DECONSTRUCT_SYSTEM, temperature=0.1, max_tokens=2048,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "deconstruction",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "reasoning": {"type": "string"},
                            "remove_blocks": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 3, "maxItems": 3
                                }
                            }
                        },
                        "required": ["reasoning", "remove_blocks"]
                    }
                }
            }
        )
        elapsed = time.time() - start
        content = resp.content or ""
        # Try to parse JSON
        try:
            parsed = json.loads(content)
            decon_quality = "valid_json"
        except (json.JSONDecodeError, TypeError):
            # Try extracting JSON from markdown code blocks
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(1))
                    decon_quality = "valid_json_from_markdown"
                except json.JSONDecodeError:
                    decon_quality = "invalid_json"
                    parsed = None
            else:
                decon_quality = "invalid_json"
                parsed = None

        results["tasks"]["deconstruction"] = {
            "content": content,
            "parsed": parsed,
            "quality": decon_quality,
            "usage": resp.usage,
            "elapsed_s": round(elapsed, 2),
            "error": None
        }
    except Exception as e:
        results["tasks"]["deconstruction"] = {"error": str(e)}

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Test LLM quality on circuit tasks")
    parser.add_argument("--max-blocks", type=int, default=100, help="Max blocks per circuit")
    parser.add_argument("--models", nargs="+", default=["qwen3.5-122b", "deepseek-v4-flash"],
                        choices=list(MODELS.keys()), help="Models to test")
    parser.add_argument("--circuit", type=int, default=0,
                        help="Circuit index (0=smallest). -1 for all under max-blocks")
    parser.add_argument("--output", default="data/training/llm_test_results.json",
                        help="Output file for results")
    args = parser.parse_args()

    client = OpenRouterClient()
    circuits = load_circuits(args.max_blocks)

    if not circuits:
        print("No circuits found under max_blocks limit.")
        return

    print(f"Found {len(circuits)} circuits under {args.max_blocks} blocks")
    print(f"Testing models: {args.models}")

    # Select circuits to test
    if args.circuit >= 0:
        test_circuits = [circuits[args.circuit]]
        print(f"Testing circuit {args.circuit}: {test_circuits[0]['id']} ({len(test_circuits[0]['blocks'])} blocks)")
    else:
        # Test a few representative sizes
        test_circuits = []
        # Smallest, medium, largest
        if len(circuits) >= 3:
            test_circuits = [circuits[0], circuits[len(circuits)//2], circuits[-1]]
        else:
            test_circuits = circuits
        print(f"Testing {len(test_circuits)} circuits: {[f'{c['id'][:30]}({len(c['blocks'])}b)' for c in test_circuits]}")

    all_results = []
    total_cost_estimate = 0.0

    for circuit in test_circuits:
        print(f"\n{'='*60}")
        print(f"Circuit: {circuit['id']} ({len(circuit['blocks'])} blocks, {circuit.get('difficulty','?')})")
        print(f"Description: {circuit.get('description','')[:80]}")
        print(f"{'='*60}")

        for model_key in args.models:
            model_id = MODELS[model_key]
            print(f"\n--- {model_key} ({model_id}) ---")

            result = test_model(client, model_key, model_id, circuit)
            all_results.append(result)

            # Print results
            for task_name, task_result in result["tasks"].items():
                if task_result.get("error"):
                    print(f"  {task_name}: ERROR - {task_result['error']}")
                    continue

                content = task_result.get("content") or ""
                usage = task_result.get("usage", {})
                elapsed = task_result.get("elapsed_s", 0)

                # Estimate cost (rough)
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                actual_output = completion_tokens - reasoning_tokens
                cost = usage.get("cost", 0)
                print(f"  {task_name}: {elapsed}s, {prompt_tokens}in/{actual_output}out+{reasoning_tokens}reason tok, ${cost:.4f}")

                if task_name == "description":
                    print(f"    → {content[:200]}")
                elif task_name == "verify_contract":
                    # Show first few lines
                    lines = content.strip().split("\n") if content else ["<empty>"]
                    print(f"    → {lines[0][:100]}")
                    if len(lines) > 1:
                        print(f"    → {lines[1][:100]}")
                    if len(lines) > 2:
                        print(f"    → ... ({len(lines)} total lines)")
                elif task_name == "deconstruction":
                    quality = task_result.get("quality", "?")
                    parsed = task_result.get("parsed")
                    print(f"    → quality: {quality}")
                    if parsed:
                        reasoning = parsed.get("reasoning", "")[:150]
                        n_blocks = len(parsed.get("remove_blocks", []))
                        print(f"    → reasoning: {reasoning}")
                        print(f"    → remove_blocks: {n_blocks} blocks")

                time.sleep(2)  # Rate limiting between models

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for result in all_results:
        model = result["model"]
        circuit = result["circuit"][:40]
        for task_name, task_result in result["tasks"].items():
            if task_result.get("error"):
                print(f"  {model:20} | {circuit:40} | {task_name:20} | ERROR")
            else:
                usage = task_result.get("usage", {})
                pt = usage.get("prompt_tokens", "?")
                ct = usage.get("completion_tokens", "?")
                elapsed = task_result.get("elapsed_s", "?")
                print(f"  {model:20} | {circuit:40} | {task_name:20} | {pt}/{ct} tok, {elapsed}s")


if __name__ == "__main__":
    main()