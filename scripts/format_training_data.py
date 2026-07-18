#!/usr/bin/env python3
"""
Format procedural circuit examples as training data for Qwen 2.5 Coder 7B SFT.

Iterates over ALL_GENERATORS from training.generators, generates all circuit
examples, and formats each as a JSONL training entry with the Plan+Constraint
prompt schema.

Usage:
    python scripts/format_training_data.py                    # Generate dataset
    python scripts/format_training_data.py --stats            # Stats only
    python scripts/format_training_data.py --output PATH      # Custom output
"""
from __future__ import annotations
import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from training.generators import ALL_GENERATORS, make_plan, CircuitExample

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Plan redstone circuits. You MUST generate exactly N blocks. Count them carefully."
)

USER_PROMPT_TEMPLATE = (
    "Plan this circuit:\n\n{description}\n\n"
    "REQUIREMENTS:\n"
    "- Generate EXACTLY {N} blocks\n"
    "- Number each step 1 to {N}\n"
    "- Do not stop early\n"
    "- List every single block\n"
    "\n"
    "Circuit needs {N} total blocks. Be complete."
)

# ---------------------------------------------------------------------------
# Block reason generation
# ---------------------------------------------------------------------------


def _parse_block_state(state: str) -> Tuple[str, Dict[str, str]]:
    """Parse a block state string into block name and properties dict."""
    if "[" in state:
        block_name, props_str = state.split("[", 1)
        props_str = props_str.rstrip("]")
        props = {}
        for prop in props_str.split(","):
            if "=" in prop:
                k, v = prop.split("=", 1)
                props[k] = v
        return block_name, props
    return state, {}


def _short_name(state: str) -> str:
    """Extract the short block name from a state string."""
    name = state.split("[")[0]
    return name.split(":", 1)[-1] if ":" in name else name


def generate_reason(x: int, y: int, z: int, state: str,
                    nbt: Optional[str]) -> str:
    """Generate a meaningful placement reason for a single block."""
    short = _short_name(state)
    _, props = _parse_block_state(state)
    pos = f"({x},{y},{z})"

    # --- Power / signal sources ---
    if short == "lever":
        facing = props.get("facing", "east")
        return (
            f"Place lever at {pos} facing {facing} "
            f"– this is the input that controls the circuit"
        )

    if short == "redstone_block":
        return (
            f"Place redstone block at {pos} "
            f"– provides constant power to adjacent components"
        )

    # --- Signal carriers ---
    if short == "redstone_wire":
        return (
            f"Place redstone wire at {pos} "
            f"– carries the redstone signal between components"
        )

    # --- Signal processors ---
    if short == "redstone_torch":
        return (
            f"Place redstone torch at {pos} "
            f"– inverts or propagates the redstone signal"
        )

    if short in ("repeater", "comparator"):
        return (
            f"Place {short.replace('_', ' ')} at {pos} "
            f"– processes the redstone signal"
        )

    # --- Outputs ---
    if short == "redstone_lamp":
        return (
            f"Place redstone lamp at {pos} "
            f"– the output that lights up when powered"
        )

    # --- Structural blocks ---
    if short in ("stone", "cobblestone", "stone_bricks"):
        return (
            f"Place {short.replace('_', ' ')} block at {pos} "
            f"– structural support for the component above"
        )

    # --- Containers ---
    if short == "chest":
        return (
            f"Place chest at {pos} "
            f"– stores items"
        )

    if short == "barrel":
        return (
            f"Place barrel at {pos} facing {props.get('facing', 'up')} "
            f"– stores items"
        )

    if short == "hopper":
        facing = props.get("facing", "down")
        return (
            f"Place hopper at {pos} facing {facing} "
            f"– transports items{' downward' if facing == 'down' else ''}"
        )

    if short in ("dropper", "dispenser"):
        facing = props.get("facing", "down")
        ct = short.replace("_", " ")
        return (
            f"Place {ct} at {pos} facing {facing} "
            f"– {('dispenses' if short == 'dispenser' else 'ejects')} items"
        )

    # --- Fallback ---
    display = short.replace("_", " ")
    return f"Place {display} at {pos}"


# ---------------------------------------------------------------------------
# Reasoning summary
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    "power_distribution": "power distribution",
    "logic_gate": "logic gate",
    "signal_routing": "signal routing",
    "memory_clock": "memory and clock",
    "containers": "container",
}


def generate_reasoning_summary(example: CircuitExample) -> str:
    """Generate a reasoning summary that explains the circuit's function."""
    cat = _CATEGORY_LABELS.get(example.category, example.category)
    total = len(example.blocks) + len(example.support_blocks)

    # Choose a sentence about build order based on category
    if example.category == "power_distribution":
        order_hint = (
            "Place the power source and support blocks first, "
            "then signal carriers, and finally the output lamps."
        )
    elif example.category == "logic_gate":
        order_hint = (
            "Place inputs (levers) and structural blocks first, "
            "then signal processing (torches, wire), "
            "and finally the output lamp."
        )
    elif example.category == "signal_routing":
        order_hint = (
            "Place the signal source first, "
            "then the routing components (wire, torches) in order, "
            "and finally the output lamps."
        )
    elif example.category == "memory_clock":
        order_hint = (
            "Place the structural blocks first, "
            "then the cross-coupled torches and wire, "
            "then the input levers, and finally the output lamp."
        )
    elif example.category == "containers":
        order_hint = (
            "Place container blocks from bottom to top "
            "or from source to destination."
        )
    else:
        order_hint = "Build the circuit from bottom to top."

    return (
        f"This is a {cat} circuit ({example.difficulty} difficulty). "
        f"{example.description} "
        f"The circuit requires {total} blocks total, including both "
        f"functional redstone components and structural support blocks. "
        f"{order_hint}"
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_example(example: CircuitExample) -> Dict[str, Any]:
    """Format a single CircuitExample as a complete training-data entry."""
    # Combine blocks with support_blocks
    all_blocks = list(example.blocks) + list(example.support_blocks)

    # Sort by (y, x, z) — bottom-up build order
    all_blocks.sort(key=lambda b: (b[1], b[0], b[2]))

    n = len(all_blocks)

    # Generate reasons
    reasons = [
        generate_reason(x, y, z, state, nbt)
        for x, y, z, state, nbt in all_blocks
    ]

    # Convert to plan format (connects_to left as empty — not needed for SFT)
    plan = make_plan(all_blocks, reasons)

    # Reasoning summary
    reasoning = generate_reasoning_summary(example)

    # Build the assistant response as a JSON string (pretty-printed)
    assistant_json = json.dumps({
        "plan": plan,
        "reasoning": reasoning,
    }, indent=2)

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                description=example.description,
                N=n,
            )},
            {"role": "assistant", "content": assistant_json},
        ],
        "metadata": {
            "category": example.category,
            "difficulty": example.difficulty,
            "generator": example.metadata.get("generator", "unknown"),
            "block_count": n,
            "verified": True,
        },
    }


def collect_examples() -> List[CircuitExample]:
    """Collect all examples from every registered generator."""
    examples: List[CircuitExample] = []
    for generator_cls in ALL_GENERATORS:
        gen = generator_cls()
        gen_examples = gen.generate_all()
        examples.extend(gen_examples)
    return examples


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def print_stats(examples: List[CircuitExample]) -> None:
    """Print a summary of the collected dataset."""
    total = len(examples)

    # Category breakdown
    cat_counts: Dict[str, int] = {}
    for ex in examples:
        cat_counts[ex.category] = cat_counts.get(ex.category, 0) + 1

    # Difficulty breakdown
    diff_counts: Dict[str, int] = {}
    for ex in examples:
        diff_counts[ex.difficulty] = diff_counts.get(ex.difficulty, 0) + 1

    # Generator breakdown
    gen_counts: Dict[str, int] = {}
    for ex in examples:
        g = ex.metadata.get("generator", "unknown")
        gen_counts[g] = gen_counts.get(g, 0) + 1

    # Block counts
    block_counts = [
        len(ex.blocks) + len(ex.support_blocks) for ex in examples
    ]

    print("=" * 60)
    print("  PROCEDURAL CIRCUIT DATASET STATISTICS")
    print("=" * 60)
    print(f"\n  Total examples: {total}\n")

    print("  By category:")
    for cat, count in sorted(cat_counts.items()):
        print(f"    {cat:25s} {count:3d}")

    print("\n  By difficulty:")
    for diff, count in sorted(diff_counts.items()):
        print(f"    {diff:25s} {count:3d}")

    print("\n  By generator:")
    for gen, count in sorted(gen_counts.items()):
        print(f"    {gen:25s} {count:3d}")

    if block_counts:
        sorted_counts = sorted(block_counts)
        avg = sum(sorted_counts) / len(sorted_counts)
        median = sorted_counts[len(sorted_counts) // 2]
        print(f"\n  Block count:  min={min(sorted_counts)}  "
              f"max={max(sorted_counts)}  avg={avg:.1f}  median={median}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Format procedural circuit examples as training data"
    )
    parser.add_argument(
        "--output", type=str, default="data/training/procedural_dataset.jsonl",
        help="Output JSONL file path (default: data/training/procedural_dataset.jsonl)",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print statistics only, don't write file",
    )
    args = parser.parse_args()

    print("Collecting examples from all generators...")
    examples = collect_examples()

    if not examples:
        print("ERROR: No examples generated. Check generators.")
        sys.exit(1)

    if args.stats:
        print_stats(examples)
        return

    # Format and write
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Formatting {len(examples)} examples...")
    with open(output_path, "w") as f:
        for ex in examples:
            entry = format_example(ex)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Dataset written to {output_path}\n")
    print_stats(examples)

    # Verify
    with open(output_path) as f:
        lines = f.readlines()
    n_lines = len(lines)
    n_expected = len(examples)
    status = "OK" if n_lines == n_expected else "MISMATCH"
    print(f"\n  Verification: {n_lines} lines written (expected {n_expected}) [{status}]")

    # Print first 2 entries
    print("\n  --- First 2 entries ---")
    with open(output_path) as f:
        for i in range(2):
            line = f.readline()
            if not line:
                break
            entry = json.loads(line)
            plan = json.loads(entry["messages"][2]["content"])
            n_steps = len(plan["plan"])
            meta = entry["metadata"]
            print(f"\n  Entry {i + 1}:")
            print(f"    Generator:   {meta['generator']}")
            print(f"    Category:    {meta['category']}")
            print(f"    Difficulty:  {meta['difficulty']}")
            print(f"    Block count: {meta['block_count']} (plan steps: {n_steps})")
            print(f"    User prompt: {entry['messages'][1]['content'][:90]}...")
            print(f"    Reasoning:   {plan['reasoning'][:100]}...")


if __name__ == "__main__":
    main()
