#!/usr/bin/env python3
"""
MIRA Discord Ingestion Pipeline.

Bridges Discord scraper output (schematics + clean messages) into the MIRA
training data pipeline.  For each discovered schematic:

  1. Parse it with SchematicParser
  2. Generate deconstruction + build steps via ReverseDatasetGenerator (mock mode)
  3. Enrich with Discord metadata from the matching clean message
  4. Optionally generate N corruption variants via CircuitCorruptor
  5. Write everything to a single JSONL file

Usage:
    python scripts/ingest_discord.py --dry-run --max-schematics 5
    python scripts/ingest_discord.py --corruptions 3
    python scripts/ingest_discord.py --force
"""

import argparse
import json
import os
import re
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_mining.corruptor import CircuitCorruptor
from data_mining.parser import SchematicParser
from simulation.dataset_generator import NBTEncoder, ReverseDatasetGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_snowflake(s: str) -> bool:
    """Return True if *s* is a Discord snowflake (17-20 digit string)."""
    return s.isdigit() and 17 <= len(s) <= 20


def parse_schematic_filename(filename: str) -> Optional[str]:
    """Extract the Discord message_id from a schematic filename.

    Expected pattern: ``{message_id}_{idx}_{original_name}.litematic``

    Returns the message_id string, or ``None`` if it cannot be extracted.
    """
    stem, _ = os.path.splitext(filename)
    parts = stem.split("_", 2)
    if parts and _is_snowflake(parts[0]):
        return parts[0]
    # Fallback: scan for the first snowflake substring in the stem
    for candidate in stem.split("_"):
        if _is_snowflake(candidate):
            return candidate
    return None


def build_message_index(clean_messages_dir: str) -> Dict[str, Dict[str, Any]]:
    """Walk *clean_messages_dir* and index every message by message_id.

    Returns a dict::

        {
            "<message_id>": {
                "message_id": ...,
                "channel_id": ...,
                "channel_name": ...,
                "category": ...,
                "author_id": ...,
                "author_name": ...,
                "content": ...,
                "schematics": [...],
            },
            ...
        }
    """
    index: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(clean_messages_dir):
        return index

    for server_dir in os.listdir(clean_messages_dir):
        server_path = os.path.join(clean_messages_dir, server_dir)
        if not os.path.isdir(server_path):
            continue
        for channel_dir in os.listdir(server_path):
            jsonl_path = os.path.join(server_path, channel_dir, "messages.jsonl")
            if not os.path.isfile(jsonl_path):
                continue
            try:
                with open(jsonl_path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        mid = msg.get("message_id", "")
                        if mid:
                            index[mid] = msg
            except Exception:
                # Silently skip unreadable files
                pass
    return index


def extract_description(msg: Optional[Dict[str, Any]]) -> str:
    """Extract the best available description from a Discord message dict.

    Priority:
      1. Archiver Bot structured content (``## Description`` section)
      2. Raw ``content`` field
      3. ``channel_name`` as fallback
    """
    if msg is None:
        return ""

    content = (msg.get("content") or "").strip()
    channel_name = (msg.get("channel_name") or "").strip()

    # Try to extract the Description section from Archiver Bot messages
    if content.startswith("##"):
        # Look for "## Description" followed by text (may span multiple lines)
        desc_match = re.search(
            r"^## Description\s*\n(.+?)(?:\n##\s|\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if desc_match:
            desc = desc_match.group(1).strip()
            if desc:
                return desc

    if content:
        return content
    return channel_name


def _block_tuple_to_dict(block: Tuple) -> Dict[str, Any]:
    """Convert a parser block tuple ``(x, y, z, state, nbt)`` to a plain dict."""
    d: Dict[str, Any] = {
        "x": block[0],
        "y": block[1],
        "z": block[2],
        "state": block[3],
    }
    # Optionally keep NBT (not included in block_list but available)
    return d


def _format_step_blocks(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize removed_blocks in deconstruction/build steps to dict format."""
    out = []
    for step in steps:
        s = dict(step)
        removed = s.get("removed_blocks", [])
        s["removed_blocks"] = [
            {"pos": rb.get("pos"), "state": rb.get("state")}
            for rb in removed
        ]
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Main processing logic
# ---------------------------------------------------------------------------

def discover_schematics(
    source_dir: str,
    max_schematics: Optional[int] = None,
) -> List[Tuple[str, str]]:
    """Discover (schematic_path, schematic_filename) pairs.

    Looks in ``raw_schematics/<server_id>/`` and ``clean_schematics/<server_id>/``
    under *source_dir*.
    """
    results: List[Tuple[str, str]] = []
    for subdir in ("raw_schematics", "clean_schematics"):
        base = os.path.join(source_dir, subdir)
        if not os.path.isdir(base):
            continue
        for server_id in sorted(os.listdir(base)):
            server_path = os.path.join(base, server_id)
            if not os.path.isdir(server_path):
                continue
            for fname in sorted(os.listdir(server_path)):
                if not fname.endswith(".litematic"):
                    continue
                results.append((os.path.join(server_path, fname), fname))
                if max_schematics is not None and len(results) >= max_schematics:
                    return results
    return results


def process_schematic(
    schematic_path: str,
    filename: str,
    message_index: Dict[str, Dict[str, Any]],
    generator: ReverseDatasetGenerator,
    num_corruptions: int,
) -> List[Dict[str, Any]]:
    """Process a single schematic and return a list of output entries.

    Returns one generation entry and (optionally) N corruption entries.
    Returns an empty list on failure (error already printed to stderr).
    """
    entries: List[Dict[str, Any]] = []

    # --- Parse ---
    parser = SchematicParser(schematic_path)
    blocks_raw = parser.parse_blocks()
    meta = parser.get_metadata()

    # --- Discord metadata ---
    message_id = parse_schematic_filename(filename) or ""
    msg = message_index.get(message_id) if message_id else None
    discord_meta: Dict[str, Any] = {
        "message_id": message_id,
        "channel_name": "",
        "category": "",
        "author_name": "",
        "description": "",
    }
    if msg:
        discord_meta = {
            "message_id": msg.get("message_id", message_id),
            "channel_name": msg.get("channel_name", ""),
            "category": msg.get("category", ""),
            "author_name": msg.get("author_name", ""),
            "description": extract_description(msg),
        }

    # --- Block list (drop nbt for top-level field) ---
    block_list = [_block_tuple_to_dict(b) for b in blocks_raw]

    # --- Generate training data via ReverseDatasetGenerator ---
    try:
        result = generator.process_schematic(schematic_path)
    except Exception as exc:
        print(f"  FAILED during dataset generation: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # Still produce a stub entry with block_list + metadata if possible
        result = {
            "schematic_id": meta.get("name") or filename,
            "status": "partial",
            "data": {
                "metadata": meta,
                "contract_prompt": {"system": "", "user": ""},
                "verify_contract": "",
                "deconstruction_steps": [],
                "build_steps": [],
            },
        }

    data = result.get("data", {})

    # --- Generation entry ---
    generation_entry: Dict[str, Any] = {
        "type": "generation",
        "schematic_id": meta.get("name") or filename,
        "source": "discord",
        "discord_metadata": discord_meta,
        "schematic_metadata": meta,
        "block_list": block_list,
        "deconstruction_steps": _format_step_blocks(
            data.get("deconstruction_steps", [])
        ),
        "build_steps": data.get("build_steps", []),
        "verify_contract": data.get("verify_contract", ""),
        "contract_prompt": data.get("contract_prompt", {"system": "", "user": ""}),
    }
    entries.append(generation_entry)

    # --- Corruption variants ---
    if num_corruptions > 0:
        for variant_idx in range(num_corruptions):
            try:
                corruptor = CircuitCorruptor(blocks_raw)
                corrupted_blocks, modifications = corruptor.corrupt(mode="random")

                # Build a repair description from the modifications
                repair_parts = []
                for mod in modifications:
                    mtype = mod.get("type", "unknown")
                    pos = mod.get("pos", (0, 0, 0))
                    if mtype == "break_wire":
                        repair_parts.append(
                            f"Break the redstone wire at {pos} and replace with air"
                        )
                    elif mtype == "rotate_component":
                        repair_parts.append(
                            f"Rotate the component at {pos} back to original state"
                        )
                    elif mtype == "remove_source":
                        repair_parts.append(
                            f"Restore the power source at {pos}"
                        )
                    else:
                        repair_parts.append(
                            f"Fix the modification at {pos}"
                        )
                repair_desc = "; ".join(repair_parts) if repair_parts else "Unknown repair"

                # Pick the first modification's type as the overall corruption_type
                corruption_type = modifications[0]["type"] if modifications else "unknown"

                corruption_entry: Dict[str, Any] = {
                    "type": "corruption",
                    "schematic_id": meta.get("name") or filename,
                    "source": "discord",
                    "variant": variant_idx,
                    "corruption_type": corruption_type,
                    "original_blocks": [_block_tuple_to_dict(b) for b in blocks_raw],
                    "corrupted_blocks": [
                        _block_tuple_to_dict(b) for b in corrupted_blocks
                    ],
                    "modifications": modifications,
                    "repair_description": repair_desc,
                }
                entries.append(corruption_entry)

            except Exception as exc:
                print(
                    f"  FAILED corruption variant {variant_idx}: {exc}",
                    file=sys.stderr,
                )
                traceback.print_exc(file=sys.stderr)

    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Discord scraper output into the MIRA training pipeline."
    )
    parser.add_argument(
        "--source-dir",
        default="discord_scraper/data",
        help="Base directory for Discord data (default: discord_scraper/data)",
    )
    parser.add_argument(
        "--output-file",
        default="data/training/discord_dataset.jsonl",
        help="Output JSONL file (default: data/training/discord_dataset.jsonl)",
    )
    parser.add_argument(
        "--corruptions",
        type=int,
        default=3,
        help="Number of corruption variants per circuit (default: 3, 0 to skip)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without writing output",
    )
    parser.add_argument(
        "--max-schematics",
        type=int,
        default=None,
        help="Limit number of schematics to process",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=True,
        help="Use mock mode for TeacherClient (default: True)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output file instead of appending",
    )

    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Discover schematics
    # ------------------------------------------------------------------
    schematics = discover_schematics(args.source_dir, args.max_schematics)
    total = len(schematics)

    if total == 0:
        print("No .litematic schematics found. Exiting.")
        return

    print(f"Discovered {total} schematics.")
    if args.dry_run:
        print("\nDry-run preview (would process these schematics):")
        for i, (path, fname) in enumerate(schematics, 1):
            mid = parse_schematic_filename(fname) or "—"
            print(f"  {i:>3}/{total}: [{mid}] {fname}")
        print(f"\nOutput file      : {args.output_file}")
        print(f"Corruptions      : {args.corruptions}")
        print("Dry-run complete — no files written.")
        return

    # ------------------------------------------------------------------
    # 2. Build message index
    # ------------------------------------------------------------------
    clean_messages_dir = os.path.join(args.source_dir, "clean_messages")
    print("Building message index from clean_messages/ ...", end=" ", flush=True)
    message_index = build_message_index(clean_messages_dir)
    print(f"done ({len(message_index)} messages indexed).")

    # ------------------------------------------------------------------
    # 3. Prepare output
    # ------------------------------------------------------------------
    output_path = args.output_file
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if args.force:
        mode = "w"
        print(f"Overwriting {output_path} (--force).")
    else:
        mode = "a"
        print(f"Appending to {output_path}.")

    # ------------------------------------------------------------------
    # 4. Initialize generator (mock mode by default)
    # ------------------------------------------------------------------
    generator = ReverseDatasetGenerator()
    if not args.mock:
        # TeacherClient defaults to mock_mode=True; setting mock=False would
        # require an OpenRouter client.  We keep it in mock mode regardless
        # for safety (the flag is accepted but honored as always-mock).
        print("Note: --mock flag is ignored; TeacherClient is always in mock mode.")

    # ------------------------------------------------------------------
    # 5. Process schematics
    # ------------------------------------------------------------------
    entries_written = 0
    corrupted = 0

    with open(output_path, mode, encoding="utf-8") as outfile:
        for idx, (schematic_path, filename) in enumerate(schematics, 1):
            print(f"Processing {idx}/{total}: {filename} ...", end=" ", flush=True)
            try:
                entries = process_schematic(
                    schematic_path=schematic_path,
                    filename=filename,
                    message_index=message_index,
                    generator=generator,
                    num_corruptions=args.corruptions,
                )
            except Exception as exc:
                print(f"FAILED: {exc}")
                traceback.print_exc(file=sys.stderr)
                continue

            if not entries:
                print("FAILED (no entries produced)")
                continue

            for entry in entries:
                outfile.write(json.dumps(entry, cls=NBTEncoder) + "\n")
                if entry["type"] == "generation":
                    entries_written += 1
                else:
                    corrupted += 1

            print("OK")

    # ------------------------------------------------------------------
    # 6. Summary
    # ------------------------------------------------------------------
    print(
        f"\nDone. {entries_written} generation + {corrupted} corruption entries "
        f"written to {output_path}."
    )


if __name__ == "__main__":
    main()
