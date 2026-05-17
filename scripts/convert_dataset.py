#!/usr/bin/env python3
"""
MIRA: Convert Discord dataset JSONL into training-ready formats.

Reads the JSONL produced by ``scripts/ingest_discord.py`` and converts each
entry into one or more output formats suitable for model training.

Input formats:
  - ``generation`` entries → ``block_list`` format
  - ``corruption`` entries → ``repair`` format

Usage:
    python scripts/convert_dataset.py --help
    python scripts/convert_dataset.py --dry-run
    python scripts/convert_dataset.py --formats block_list,repair
    python scripts/convert_dataset.py --max-blocks 5000
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIFFICULTY_THRESHOLDS: List[Tuple[int, str]] = [
    (20, "beginner"),
    (100, "intermediate"),
    (500, "advanced"),
]
# Everything above 500 is "expert"

FALLBACK_DESCRIPTION = "No description available."
FALLBACK_CATEGORY = "uncategorized"

# ---------------------------------------------------------------------------
# Block filtering & normalization
# ---------------------------------------------------------------------------


def _is_entity_block(state: str) -> bool:
    """Return True if *state* starts with ``entity:``."""
    return state.startswith("entity:")


def _normalize_coordinate(value: Any) -> Optional[int]:
    """Convert a coordinate to int if possible.

    - Returns the int if *value* is already int.
    - Returns the int if *value* is a float representing a whole number (e.g. 4.0).
    - Returns None for non-whole floats or non-numeric types.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value == int(value):
            return int(value)
        return None
    if isinstance(value, str):
        try:
            f = float(value)
            if f == int(f):
                return int(f)
            return None
        except (ValueError, TypeError):
            return None
    return None


def filter_and_normalize_blocks(
    blocks: List[Dict[str, Any]],
    max_blocks: int,
) -> Optional[List[Dict[str, Any]]]:
    """Apply filtering rules and coordinate normalization to a block list.

    Steps:
      1. Remove blocks with ``state`` starting with ``entity:``.
      2. Remove blocks with non-integer coordinates.
         Convert whole-number floats (e.g. 4.0) to ints.
      3. Skip circuits that end up with 0 blocks.
      4. Skip circuits exceeding *max_blocks* after filtering.
      5. Normalize coordinates so minimum (x, y, z) = 0.

    Returns the filtered + normalized block list, or ``None`` if the circuit
    should be skipped entirely.
    """
    cleaned: List[Dict[str, Any]] = []

    for block in blocks:
        state = block.get("state", "")
        if _is_entity_block(state):
            continue

        x = _normalize_coordinate(block.get("x"))
        y = _normalize_coordinate(block.get("y"))
        z = _normalize_coordinate(block.get("z"))

        if x is None or y is None or z is None:
            continue

        cleaned.append({"x": x, "y": y, "z": z, "state": state})

    # Skip empty circuits
    if not cleaned:
        return None

    # Skip oversized circuits
    if len(cleaned) > max_blocks:
        return None

    # Normalize coordinates — shift origin so min (x, y, z) = (0, 0, 0)
    min_x = min(b["x"] for b in cleaned)
    min_y = min(b["y"] for b in cleaned)
    min_z = min(b["z"] for b in cleaned)

    if min_x != 0 or min_y != 0 or min_z != 0:
        for block in cleaned:
            block["x"] -= min_x
            block["y"] -= min_y
            block["z"] -= min_z

    return cleaned


# ---------------------------------------------------------------------------
# Difficulty classification
# ---------------------------------------------------------------------------


def classify_difficulty(block_count: int) -> str:
    """Classify circuit difficulty based on block count."""
    for threshold, label in DIFFICULTY_THRESHOLDS:
        if block_count <= threshold:
            return label
    return "expert"


# ---------------------------------------------------------------------------
# Generation → block_list conversion
# ---------------------------------------------------------------------------


def _safe_string(value: Any, fallback: str = "") -> str:
    """Return a string value or *fallback* if None/empty."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _extract_category(discord_meta: Dict[str, Any]) -> str:
    """Extract category from Discord metadata with fallback."""
    return _safe_string(discord_meta.get("category", ""), FALLBACK_CATEGORY)


def _extract_description(entry: Dict[str, Any]) -> str:
    """Extract best available description from a generation entry.

    Priority:
      1. discord_metadata.description
      2. schematic_metadata.description
      3. schematic_metadata.name
      4. schematic_id
    """
    discord_meta = entry.get("discord_metadata", {})
    desc = _safe_string(discord_meta.get("description", ""))
    if desc:
        return desc

    schematic_meta = entry.get("schematic_metadata", {})
    desc = _safe_string(schematic_meta.get("description", ""))
    if desc:
        return desc

    desc = _safe_string(schematic_meta.get("name", ""))
    if desc:
        return desc

    return _safe_string(entry.get("schematic_id", ""), FALLBACK_DESCRIPTION)


def _parse_test_steps(verify_contract: str, description: str) -> List[str]:
    """Parse test steps from a verify_contract string.

    If the contract looks like Python code, we extract docstring-like lines.
    Otherwise we fall back to a single step from the description.
    """
    if not verify_contract or not verify_contract.strip():
        return [f"Verify the circuit described as: {description}"]

    # Try to extract lines that look like step descriptions
    # (lines starting with #, or inside triple-quoted strings)
    steps: List[str] = []
    for line in verify_contract.splitlines():
        stripped = line.strip()
        # Match comment-based steps
        if stripped.startswith("# ") and len(stripped) > 2:
            candidate = stripped[2:].strip()
            if candidate and not candidate.startswith("step"):
                steps.append(candidate)
        # Match lines inside docstrings that look step-like
        # (simple heuristic: contains words like "check", "verify", "place", "set")
        if (
            stripped
            and not stripped.startswith("#")
            and not stripped.startswith('"')
            and not stripped.startswith("'")
            and not stripped.startswith("def ")
            and not stripped.startswith("return ")
        ):
            lower = stripped.lower()
            if any(
                keyword in lower
                for keyword in ["check", "verify", "place", "set", "ensure", "test"]
            ):
                if stripped not in steps:
                    steps.append(stripped)

    if not steps:
        steps.append(f"Verify the circuit described as: {description}")

    return steps


def convert_generation_to_block_list(
    entry: Dict[str, Any],
    max_blocks: int,
) -> Optional[Dict[str, Any]]:
    """Convert a single generation entry to ``block_list`` format.

    Returns the converted entry, or ``None`` if it should be skipped after
    filtering.
    """
    schematic_id = entry.get("schematic_id", "unknown")
    description = _extract_description(entry)
    discord_meta = entry.get("discord_metadata", {})
    category = _extract_category(discord_meta)

    # Filter & normalize blocks
    blocks = entry.get("block_list", [])
    filtered = filter_and_normalize_blocks(blocks, max_blocks)
    if filtered is None:
        return None

    # Verification info
    verify_contract = entry.get("verify_contract", "")
    contract_prompt = entry.get("contract_prompt", {})
    test_steps = _parse_test_steps(verify_contract, description)

    # Prefer input_description from contract_prompt if available
    input_desc = description
    if isinstance(contract_prompt, dict):
        user_prompt = _safe_string(contract_prompt.get("user", ""))
        if user_prompt:
            input_desc = user_prompt
        else:
            system_prompt = _safe_string(contract_prompt.get("system", ""))
            if system_prompt:
                input_desc = system_prompt

    output_entry: Dict[str, Any] = {
        "id": f"discord_{schematic_id}",
        "description": description,
        "category": category,
        "difficulty": classify_difficulty(len(filtered)),
        "blocks": filtered,
        "verification": {
            "input_description": input_desc,
            "output_description": description,
            "test_steps": test_steps,
        },
        "source": {
            "type": "discord",
            "schematic_id": schematic_id,
            "channel_name": _safe_string(discord_meta.get("channel_name", "")),
            "author_name": _safe_string(discord_meta.get("author_name", "")),
        },
    }

    return output_entry


# ---------------------------------------------------------------------------
# Corruption → repair conversion
# ---------------------------------------------------------------------------


def _modifications_to_repair_steps(
    modifications: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert raw modification dicts to structured repair steps."""
    steps: List[Dict[str, Any]] = []
    for idx, mod in enumerate(modifications, start=1):
        mod_type = mod.get("type", "unknown")
        pos = mod.get("pos", (0, 0, 0))

        # Normalize pos to a list if it's a tuple
        pos_list: List[int] = [0, 0, 0]
        if isinstance(pos, tuple):
            pos_list = list(pos)
        elif isinstance(pos, list):
            pos_list = pos
        pos = pos_list

        original = mod.get("original", "")
        new_state = mod.get("new", "")

        # Build a human-readable action description
        action = _describe_mod_action(mod_type, pos, original, new_state)

        step: Dict[str, Any] = {
            "step": idx,
            "action": action,
            "pos": pos,
            "original_state": original,
            "target_state": new_state,
        }
        steps.append(step)

    # If there are no modifications, add a placeholder
    if not steps:
        steps.append(
            {
                "step": 1,
                "action": "Restore original blocks",
                "pos": [0, 0, 0],
                "original_state": "",
                "target_state": "",
            }
        )

    return steps


def _describe_mod_action(
    mod_type: str,
    pos: List[int],
    original: str,
    new_state: str,
) -> str:
    """Produce a human-readable action string for a modification."""
    pos_str = f"({pos[0]}, {pos[1]}, {pos[2]})"

    type_actions = {
        "break_wire": f"Break the redstone wire at {pos_str} and replace with air",
        "rotate_component": f"Rotate the component at {pos_str} back to original state",
        "remove_source": f"Restore the power source at {pos_str}",
        "add_block": f"Remove the added block at {pos_str}",
        "replace_block": f"Replace block at {pos_str} from '{new_state}' back to '{original}'",
        "swap_wires": f"Swap the wires at {pos_str} back to original configuration",
        "change_state": f"Restore block state at {pos_str} from '{new_state}' to '{original}'",
    }

    return type_actions.get(mod_type, f"Fix the modification at {pos_str}")


def convert_corruption_to_repair(
    entry: Dict[str, Any],
    generation_entries: Dict[str, Dict[str, Any]],
    max_blocks: int,
) -> Optional[Dict[str, Any]]:
    """Convert a single corruption entry to ``repair`` format.

    Attempts to find the parent generation entry from *generation_entries*
    (indexed by schematic_id) to fill in category and other metadata.

    Returns the converted entry, or ``None`` if it should be skipped.
    """
    schematic_id = entry.get("schematic_id", "unknown")
    variant = entry.get("variant", 0)

    # Try to get category from the parent generation entry
    category = FALLBACK_CATEGORY
    parent = generation_entries.get(schematic_id)
    if parent is not None:
        discord_meta = parent.get("discord_metadata", {})
        category = _extract_category(discord_meta)

    # Filter original blocks
    original_blocks_raw = entry.get("original_blocks", [])
    original_filtered = filter_and_normalize_blocks(original_blocks_raw, max_blocks)
    if original_filtered is None:
        return None

    # Filter corrupted blocks
    corrupted_blocks_raw = entry.get("corrupted_blocks", [])
    corrupted_filtered = filter_and_normalize_blocks(corrupted_blocks_raw, max_blocks)
    if corrupted_filtered is None:
        # If corrupted version is empty but original isn't, that's suspicious
        # but we still allow it with an empty corrupted list
        corrupted_filtered = []

    # Normalize modifications coordinates to match shifted origin
    modifications_raw = entry.get("modifications", [])
    modifications = _normalize_modifications(modifications_raw, original_blocks_raw, original_filtered)

    repair_description = _safe_string(
        entry.get("repair_description", ""),
        f"Repair {entry.get('corruption_type', 'unknown')} modification for {schematic_id}",
    )

    # Build repair steps
    repair_steps = _modifications_to_repair_steps(modifications)

    output_entry: Dict[str, Any] = {
        "id": f"discord_{schematic_id}_repair_{variant}",
        "description": repair_description,
        "category": category,
        "corruption_type": entry.get("corruption_type", "unknown"),
        "original_blocks": original_filtered,
        "corrupted_blocks": corrupted_filtered,
        "modifications": modifications,
        "repair_steps": repair_steps,
    }

    return output_entry


def _normalize_modifications(
    modifications: List[Dict[str, Any]],
    original_blocks_raw: List[Dict[str, Any]],
    original_filtered: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Adjust modification positions to match the shifted coordinate origin.

    Calculates the shift between the raw (unfiltered, unnormalized) block
    positions and the filtered + normalized ones, and applies the same shift
    to modification positions.
    """
    if not modifications:
        return []

    if not original_blocks_raw or not original_filtered:
        return modifications

    # Compute shift from raw (after entity/float filtering) → normalized
    raw_clean: List[Dict[str, Any]] = []
    for block in original_blocks_raw:
        state = block.get("state", "")
        if _is_entity_block(state):
            continue
        x = _normalize_coordinate(block.get("x"))
        y = _normalize_coordinate(block.get("y"))
        z = _normalize_coordinate(block.get("z"))
        if x is not None and y is not None and z is not None:
            raw_clean.append({"x": x, "y": y, "z": z})

    if not raw_clean:
        return modifications

    min_x = min(b["x"] for b in raw_clean)
    min_y = min(b["y"] for b in raw_clean)
    min_z = min(b["z"] for b in raw_clean)

    shifted: List[Dict[str, Any]] = []
    for mod in modifications:
        pos = mod.get("pos", (0, 0, 0))
        if isinstance(pos, tuple):
            pos = list(pos)
        elif not isinstance(pos, list):
            pos = [0, 0, 0]

        shifted_mod = dict(mod)
        shifted_mod["pos"] = [
            pos[0] - min_x if len(pos) > 0 else 0,
            pos[1] - min_y if len(pos) > 1 else 0,
            pos[2] - min_z if len(pos) > 2 else 0,
        ]
        shifted.append(shifted_mod)

    return shifted


# ---------------------------------------------------------------------------
# Main conversion logic
# ---------------------------------------------------------------------------


def read_entries(input_path: str) -> List[Dict[str, Any]]:
    """Read all entries from a JSONL file."""
    entries: List[Dict[str, Any]] = []
    if not os.path.isfile(input_path):
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        return entries

    with open(input_path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError as e:
                print(
                    f"WARNING: Skipping line {line_no} — JSON decode error: {e}",
                    file=sys.stderr,
                )
    return entries


def index_generation_entries(
    entries: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build an index of generation entries keyed by schematic_id."""
    index: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if entry.get("type") == "generation":
            sid = entry.get("schematic_id", "")
            if sid:
                index[sid] = entry
    return index


def process(
    input_path: str,
    output_dir: str,
    max_blocks: int,
    formats: List[str],
    dry_run: bool,
) -> Dict[str, int]:
    """Run the full conversion pipeline.

    Returns a dict with counts of entries read, converted, and skipped.
    """
    stats: Dict[str, int] = {
        "generation_read": 0,
        "corruption_read": 0,
        "generation_converted": 0,
        "corruption_converted": 0,
        "generation_skipped": 0,
        "corruption_skipped": 0,
        "other_entry_types": 0,
    }

    # 1. Read entries
    print(f"Reading entries from: {input_path}")
    entries = read_entries(input_path)
    print(f"  Total entries read: {len(entries)}")

    if not entries:
        return stats

    # Count by type
    for entry in entries:
        etype = entry.get("type", "unknown")
        if etype == "generation":
            stats["generation_read"] += 1
        elif etype == "corruption":
            stats["corruption_read"] += 1
        else:
            stats["other_entry_types"] += 1

    print(
        f"  Generation: {stats['generation_read']}, "
        f"Corruption: {stats['corruption_read']}, "
        f"Other: {stats['other_entry_types']}"
    )

    # Build index of generation entries (needed by repair conversion)
    generation_index = index_generation_entries(entries)

    # 2. Convert
    want_block_list = "block_list" in formats
    want_repair = "repair" in formats

    block_list_entries: List[Dict[str, Any]] = []
    repair_entries: List[Dict[str, Any]] = []

    for entry in entries:
        etype = entry.get("type", "")

        if etype == "generation" and want_block_list:
            converted = convert_generation_to_block_list(entry, max_blocks)
            if converted is not None:
                block_list_entries.append(converted)
                stats["generation_converted"] += 1
            else:
                stats["generation_skipped"] += 1

        elif etype == "corruption" and want_repair:
            converted = convert_corruption_to_repair(
                entry, generation_index, max_blocks
            )
            if converted is not None:
                repair_entries.append(converted)
                stats["corruption_converted"] += 1
            else:
                stats["corruption_skipped"] += 1

    # 3. Summary
    print(f"\nConversion summary:")
    print(
        f"  block_list: {stats['generation_converted']} written, "
        f"{stats['generation_skipped']} skipped"
    )
    print(
        f"  repair:     {stats['corruption_converted']} written, "
        f"{stats['corruption_skipped']} skipped"
    )

    if dry_run:
        print("\nDRY RUN — no files written.")
        return stats

    # 4. Write output files
    os.makedirs(output_dir, exist_ok=True)

    if want_block_list and block_list_entries:
        block_list_path = os.path.join(output_dir, "block_list.jsonl")
        with open(block_list_path, "w", encoding="utf-8") as fh:
            for entry in block_list_entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  Wrote {len(block_list_entries)} entries to: {block_list_path}")

    if want_repair and repair_entries:
        repair_path = os.path.join(output_dir, "repair.jsonl")
        with open(repair_path, "w", encoding="utf-8") as fh:
            for entry in repair_entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  Wrote {len(repair_entries)} entries to: {repair_path}")

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_formats(formats_str: str) -> List[str]:
    """Parse comma-separated format names; validates known formats."""
    known = {"block_list", "repair"}
    parsed = [f.strip().lower() for f in formats_str.split(",") if f.strip()]
    unknown = set(parsed) - known
    if unknown:
        print(
            f"WARNING: Unknown format(s): {', '.join(sorted(unknown))}. "
            f"Known formats: {', '.join(sorted(known))}",
            file=sys.stderr,
        )
    return [f for f in parsed if f in known]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert MIRA Discord dataset JSONL into training-ready formats "
            "(block_list, repair)."
        )
    )
    parser.add_argument(
        "--input",
        default="data/training/discord_dataset.jsonl",
        help="Input JSONL file (default: data/training/discord_dataset.jsonl)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/training/converted",
        help="Output directory (default: data/training/converted)",
    )
    parser.add_argument(
        "--max-blocks",
        type=int,
        default=5000,
        help="Maximum blocks per circuit after filtering (default: 5000)",
    )
    parser.add_argument(
        "--formats",
        default="block_list,repair",
        help="Comma-separated output formats (default: block_list,repair)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show conversion statistics without writing files",
    )

    args = parser.parse_args()

    # Validate max-blocks
    if args.max_blocks < 1:
        print("ERROR: --max-blocks must be at least 1.", file=sys.stderr)
        sys.exit(1)

    # Parse formats
    formats = parse_formats(args.formats)
    if not formats:
        print(
            "ERROR: No valid formats specified. Use --formats block_list,repair",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check input exists
    if not os.path.isfile(args.input):
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Run
    print(f"MIRA Dataset Converter")
    print(f"  Input:     {args.input}")
    print(f"  Output:    {args.output_dir}")
    print(f"  Max blocks: {args.max_blocks}")
    print(f"  Formats:   {', '.join(formats)}")
    if args.dry_run:
        print(f"  Mode:      DRY RUN\n")
    else:
        print()

    stats = process(
        input_path=args.input,
        output_dir=args.output_dir,
        max_blocks=args.max_blocks,
        formats=formats,
        dry_run=args.dry_run,
    )

    total_read = stats["generation_read"] + stats["corruption_read"]
    total_converted = stats["generation_converted"] + stats["corruption_converted"]
    total_skipped = stats["generation_skipped"] + stats["corruption_skipped"]

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Done.")
    print(f"  Read: {total_read} entries ({stats['generation_read']} gen, {stats['corruption_read']} corr)")
    print(f"  Converted: {total_converted}")
    print(f"  Skipped:   {total_skipped}")
    if stats["other_entry_types"]:
        print(f"  Other:     {stats['other_entry_types']} (ignored)")

    if args.dry_run:
        print("\nNo files were written.")


if __name__ == "__main__":
    main()
