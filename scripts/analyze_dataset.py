#!/usr/bin/env python3
"""
MIRA Dataset Analyzer — quality report for the training dataset.

Analyzes a MIRA training JSONL file (produced by ingest_discord.py) and
produces a structured quality report covering overview stats, quality issues,
category distribution, block type analysis, corruption analysis, and
description quality.

Auto-detects both the standard Discord-ingested format (discord_dataset.jsonl)
and the older reverse_dataset.jsonl format.

Usage:
    python scripts/analyze_dataset.py
    python scripts/analyze_dataset.py --input data/training/discord_dataset.jsonl
    python scripts/analyze_dataset.py --verbose
    python scripts/analyze_dataset.py --json
    python scripts/analyze_dataset.py --json --verbose > report.json
"""

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

class Colors:
    """ANSI escape codes for terminal output."""
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"
    DIM = "\033[2m"

    @staticmethod
    def ok(text: str) -> str:
        return f"{Colors.GREEN}{text}{Colors.END}"

    @staticmethod
    def warn(text: str) -> str:
        return f"{Colors.YELLOW}{text}{Colors.END}"

    @staticmethod
    def error(text: str) -> str:
        return f"{Colors.RED}{text}{Colors.END}"

    @staticmethod
    def info(text: str) -> str:
        return f"{Colors.CYAN}{text}{Colors.END}"

    @staticmethod
    def bold(text: str) -> str:
        return f"{Colors.BOLD}{text}{Colors.END}"

    @staticmethod
    def dim(text: str) -> str:
        return f"{Colors.DIM}{text}{Colors.END}"

    @staticmethod
    def head(text: str) -> str:
        return f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.END}"


# ---------------------------------------------------------------------------
# Data loading & format detection
# ---------------------------------------------------------------------------

REDSTONE_BLOCKS = {
    "redstone_wire", "repeater", "comparator", "redstone_torch",
    "redstone_wall_torch", "redstone_block", "lever", "observer",
    "piston", "sticky_piston", "dispenser", "dropper", "note_block",
    "daylight_detector", "target", "tnt", "tripwire", "tripwire_hook",
    "pressure_plate", "stone_pressure_plate", "light_weighted_pressure_plate",
    "heavy_weighted_pressure_plate", "oak_pressure_plate", "spruce_pressure_plate",
    "birch_pressure_plate", "jungle_pressure_plate", "acacia_pressure_plate",
    "dark_oak_pressure_plate", "mangrove_pressure_plate", "cherry_pressure_plate",
    "bamboo_pressure_plate", "crimson_pressure_plate", "warped_pressure_plate",
    "polished_blackstone_pressure_plate", "sculk_sensor", "calibrated_sculk_sensor",
    "sculk_shrieker",
}

DIFFICULTY_TIERS = [
    ("beginner", 0, 20),
    ("intermediate", 21, 100),
    ("advanced", 101, 500),
    ("expert", 501, float("inf")),
]


def load_entries(path: str) -> List[Dict[str, Any]]:
    """Load all entries from a JSONL file."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def detect_format(entries: List[Dict[str, Any]]) -> str:
    """Detect whether entries use the standard or legacy format.

    Returns:
        ``"standard"`` — entries have a ``type`` field (``generation``/``corruption``)
            as produced by ``ingest_discord.py``.
        ``"legacy"`` — entries have no ``type`` but have a ``data`` dict with nested
            metadata, as produced by the older ``ReverseDatasetGenerator``.
    """
    if not entries:
        return "unknown"
    # Check first few entries for a type field
    for e in entries[:5]:
        if e.get("type") in ("generation", "corruption"):
            return "standard"
    # Check for legacy format: has "data" key with nested structure
    for e in entries[:5]:
        if "data" in e and isinstance(e["data"], dict):
            return "legacy"
    return "standard"  # default assumption


def _extract_blocks_from_decon(decon_steps: List[Dict]) -> List[Dict[str, Any]]:
    """Extract unique blocks from deconstruction steps (legacy format)."""
    seen = set()
    blocks = []
    for step in decon_steps:
        for rb in step.get("removed_blocks", []):
            pos = rb.get("pos")
            state = rb.get("state", "")
            if pos is None:
                continue
            key = (tuple(pos) if isinstance(pos, list) else pos, state)
            if key not in seen:
                seen.add(key)
                if isinstance(pos, (list, tuple)) and len(pos) == 3:
                    blocks.append({"x": pos[0], "y": pos[1], "z": pos[2], "state": state})
                elif isinstance(pos, dict):
                    blocks.append({"x": pos.get("x", 0), "y": pos.get("y", 0),
                                   "z": pos.get("z", 0), "state": state})
    return blocks


def normalize_entries(entries: List[Dict[str, Any]], fmt: str) -> List[Dict[str, Any]]:
    """Normalize entries to the standard format for analysis.

    Legacy entries (from reverse_dataset.jsonl) are restructured to look like
    standard generation entries.
    """
    if fmt == "standard":
        return entries  # already in the expected format

    normalized = []
    for e in entries:
        if "data" not in e or not isinstance(e["data"], dict):
            # Can't normalize; include as-is
            normalized.append(e)
            continue

        data = e["data"]
        meta = data.get("metadata", {})

        # Build block_list from deconstruction steps
        decon_steps = data.get("deconstruction_steps", [])
        block_list = _extract_blocks_from_decon(decon_steps)

        norm = {
            "type": "generation",
            "schematic_id": e.get("schematic_id", "unknown"),
            "source": "legacy",
            "discord_metadata": {
                "message_id": "",
                "channel_name": "",
                "category": meta.get("category", ""),
                "author_name": meta.get("author", ""),
                "description": meta.get("description", ""),
            },
            "schematic_metadata": {
                "name": meta.get("name", ""),
                "author": meta.get("author", ""),
                "description": meta.get("description", ""),
                "regions": meta.get("regions", []),
            },
            "block_list": block_list,
            "deconstruction_steps": decon_steps,
            "build_steps": data.get("build_steps", []),
            "verify_contract": data.get("verify_contract", ""),
            "contract_prompt": data.get("contract_prompt", {"system": "", "user": ""}),
            "_legacy_status": e.get("status", ""),
        }
        normalized.append(norm)

    return normalized


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_overview(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Section 1: Overview statistics."""
    total = len(entries)
    gen_entries = [e for e in entries if e.get("type") == "generation"]
    corr_entries = [e for e in entries if e.get("type") == "corruption"]

    schematic_ids = set()
    for e in entries:
        sid = e.get("schematic_id")
        if sid:
            schematic_ids.add(sid)

    # Block counts per generation entry
    block_counts = []
    total_blocks = 0
    for e in gen_entries:
        bl = e.get("block_list", [])
        n = len(bl)
        block_counts.append(n)
        total_blocks += n

    block_stats: Dict[str, Any] = {}
    if block_counts:
        block_stats = {
            "total": total_blocks,
            "average": round(sum(block_counts) / len(block_counts), 1),
            "min": min(block_counts),
            "max": max(block_counts),
            "median": sorted(block_counts)[len(block_counts) // 2],
        }

    return {
        "total_entries": total,
        "generation_entries": len(gen_entries),
        "corruption_entries": len(corr_entries),
        "unique_schematic_ids": len(schematic_ids),
        "block_stats": block_stats,
    }


def analyze_quality(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Section 2: Quality issues."""
    # Thresholds
    OVERSIZE_THRESHOLDS = [1000, 5000, 10000]
    UNDERSIZE_THRESHOLD = 5

    gen_entries = [e for e in entries if e.get("type") == "generation"]

    entity_blocks = 0
    float_coords = 0
    empty_descriptions = 0
    missing_metadata: Dict[str, int] = Counter()
    schematic_id_counts: Counter = Counter()
    oversize_counts = {t: 0 for t in OVERSIZE_THRESHOLDS}
    undersize_count = 0

    for e in entries:
        sid = e.get("schematic_id")
        if sid:
            schematic_id_counts[sid] += 1

    # Required fields per entry type
    required_fields = {
        "generation": ["schematic_id", "block_list", "source", "discord_metadata"],
        "corruption": ["schematic_id", "corruption_type", "original_blocks",
                       "corrupted_blocks", "modifications"],
    }

    for e in entries:
        etype = e.get("type", "")

        # Check required fields
        for field in required_fields.get(etype, []):
            if field not in e or e[field] is None or e[field] == "":
                missing_metadata[f"{etype}.{field}"] += 1
            elif isinstance(e[field], (list, dict)) and len(e[field]) == 0:
                # Only flag empty lists/dicts for certain fields
                if field not in ("deconstruction_steps", "build_steps",
                                 "contract_prompt", "discord_metadata"):
                    pass  # allow empty metadata
                pass

    # Per-generation-entry checks
    for e in gen_entries:
        bl = e.get("block_list", [])
        block_count = len(bl)

        # Oversize / undersize
        if block_count < UNDERSIZE_THRESHOLD:
            undersize_count += 1
        for t in OVERSIZE_THRESHOLDS:
            if block_count > t:
                oversize_counts[t] += 1

        # Entity blocks
        for b in bl:
            state = b.get("state", "")
            if state.startswith("entity:"):
                entity_blocks += 1

            # Float coordinates
            for coord in ("x", "y", "z"):
                val = b.get(coord)
                if val is not None and not isinstance(val, int):
                    # Check if it's a float with non-zero fractional part
                    if isinstance(val, float) and val != int(val):
                        float_coords += 1
                        break
                    # Also catch cases where it's stored as float but is whole
                    # (e.g. 13.0) - still report as it's not an integer type
                    if isinstance(val, float):
                        float_coords += 1
                        break

        # Empty description
        desc = e.get("discord_metadata", {}).get("description", "")
        if not desc or not desc.strip():
            empty_descriptions += 1

    # Duplicate schematic IDs
    duplicates = {sid: count for sid, count in schematic_id_counts.items()
                  if count > 1}

    return {
        "entity_blocks": entity_blocks,
        "float_coordinate_blocks": float_coords,
        "empty_descriptions": empty_descriptions,
        "missing_metadata": dict(missing_metadata),
        "duplicate_schematic_ids": len(duplicates),
        "duplicate_schematic_id_list": duplicates,
        "oversize_circuits": oversize_counts,
        "undersize_circuits_lt_5": undersize_count,
    }


def analyze_categories(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Section 3: Category distribution."""
    gen_entries = [e for e in entries if e.get("type") == "generation"]

    category_counts: Counter = Counter()
    difficulty_counts: Counter = Counter()

    for e in gen_entries:
        cat = e.get("discord_metadata", {}).get("category", "unknown")
        if not cat:
            cat = "unknown"
        category_counts[cat] += 1

        # Difficulty tier
        bl = e.get("block_list", [])
        n = len(bl)
        for tier_name, lo, hi in DIFFICULTY_TIERS:
            if lo <= n <= hi:
                difficulty_counts[tier_name] += 1
                break
        else:
            difficulty_counts["unknown"] += 1

    return {
        "category_counts": dict(category_counts),
        "difficulty_counts": dict(difficulty_counts),
    }


def analyze_blocks(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Section 4: Block type analysis."""
    gen_entries = [e for e in entries if e.get("type") == "generation"]

    # Full block state counter (e.g. "minecraft:redstone_wire[east=none,...]")
    full_state_counter: Counter = Counter()
    # Just the base block type (e.g. "minecraft:redstone_wire")
    base_type_counter: Counter = Counter()
    redstone_counter: Counter = Counter()

    for e in gen_entries:
        for b in e.get("block_list", []):
            state = b.get("state", "")
            full_state_counter[state] += 1

            # Extract base type (everything before the first '[')
            base = state.split("[")[0] if "[" in state else state
            base_type_counter[base] += 1

            # Check for redstone-specific blocks
            # base is like "minecraft:redstone_wire"
            block_name = base.split(":", 1)[1] if ":" in base else base
            if block_name in REDSTONE_BLOCKS:
                redstone_counter[base] += 1

    top20 = full_state_counter.most_common(20)

    return {
        "top_20_block_states": [(s, c) for s, c in top20],
        "unique_block_types": len(base_type_counter),
        "unique_block_states": len(full_state_counter),
        "redstone_block_types": dict(redstone_counter),
        "total_redstone_blocks": sum(redstone_counter.values()),
    }


def analyze_corruptions(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Section 5: Corruption analysis."""
    corr_entries = [e for e in entries if e.get("type") == "corruption"]

    corruption_type_counts: Counter = Counter()
    modifications_counts = []

    for e in corr_entries:
        ct = e.get("corruption_type", "unknown")
        corruption_type_counts[ct] += 1

        mods = e.get("modifications", [])
        modifications_counts.append(len(mods))

    avg_mods = 0.0
    if modifications_counts:
        avg_mods = round(sum(modifications_counts) / len(modifications_counts), 2)

    return {
        "total_corruption_entries": len(corr_entries),
        "corruption_type_counts": dict(corruption_type_counts),
        "average_modifications_per_entry": avg_mods,
    }


def analyze_descriptions(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Section 6: Description quality."""
    gen_entries = [e for e in entries if e.get("type") == "generation"]

    lengths = []
    starts_with_dash = 0
    empty_count = 0

    for e in gen_entries:
        desc = e.get("discord_metadata", {}).get("description", "")
        if not desc or not desc.strip():
            empty_count += 1
            continue

        lengths.append(len(desc))

        # Archiver Bot structured format typically starts with "-"
        if desc.strip().startswith("-"):
            starts_with_dash += 1

    avg_length = 0.0
    if lengths:
        avg_length = round(sum(lengths) / len(lengths), 1)

    min_length = min(lengths) if lengths else 0
    max_length = max(lengths) if lengths else 0

    return {
        "total_descriptions": len(gen_entries),
        "empty_descriptions": empty_count,
        "non_empty_descriptions": len(lengths),
        "average_length_chars": avg_length,
        "min_length_chars": min_length,
        "max_length_chars": max_length,
        "archiver_bot_format_starts_with_dash": starts_with_dash,
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the full analysis report."""
    return {
        "overview": analyze_overview(entries),
        "quality_issues": analyze_quality(entries),
        "category_distribution": analyze_categories(entries),
        "block_type_analysis": analyze_blocks(entries),
        "corruption_analysis": analyze_corruptions(entries),
        "description_quality": analyze_descriptions(entries),
    }


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_report_text(report: Dict[str, Any], verbose: bool = False) -> str:
    """Format report as colored human-readable text."""
    lines: List[str] = []

    def add(text: str = "") -> None:
        lines.append(text)

    def section_header(title: str) -> None:
        add()
        add(f"{Colors.head('=' * 60)}")
        add(f"{Colors.head(f'  {title}')}")
        add(f"{Colors.head('=' * 60)}")

    def subsection_header(title: str) -> None:
        add(f"  {Colors.bold(title)}")
        add(f"  {Colors.dim('-' * 40)}")

    def kv(key: str, val: Any, indent: int = 2, val_color: str = "") -> None:
        pad = " " * indent
        v = str(val)
        if val_color:
            v = f"{val_color}{v}{Colors.END}"
        add(f"{pad}{Colors.dim(key + ':')} {v}")

    def warn_kv(key: str, val: Any, indent: int = 2) -> None:
        pad = " " * indent
        add(f"{pad}{Colors.dim(key + ':')} {Colors.warn(str(val))}{Colors.END}")

    def error_kv(key: str, val: Any, indent: int = 2) -> None:
        pad = " " * indent
        add(f"{pad}{Colors.dim(key + ':')} {Colors.error(str(val))}{Colors.END}")

    def ok_kv(key: str, val: Any, indent: int = 2) -> None:
        pad = " " * indent
        add(f"{pad}{Colors.dim(key + ':')} {Colors.ok(str(val))}{Colors.END}")

    def dict_block(d: Dict[str, Any], indent: int = 4) -> None:
        pad = " " * indent
        for k, v in d.items():
            k_str = k.replace("_", " ").title()
            add(f"{pad}{Colors.dim(k_str + ':')} {v}")

    def warning_flag(label: str, count: int, detail: str = "") -> None:
        """Print a warning or ok line depending on count."""
        pad = "    "
        if count > 0:
            icon = Colors.warn("⚠")
            val = Colors.warn(str(count))
        else:
            icon = Colors.ok("✓")
            val = Colors.ok("0")
        detail_str = f"  {Colors.dim(detail)}" if detail else ""
        add(f"{pad}{icon} {label}: {val}{detail_str}")

    # =================================================================
    # 1. Overview
    # =================================================================
    section_header("1. OVERVIEW STATS")

    ov = report["overview"]
    kv("Total entries", ov["total_entries"])
    kv("Generation entries", ov["generation_entries"])
    kv("Corruption entries", ov["corruption_entries"], val_color=Colors.CYAN)
    kv("Unique schematic IDs", ov["unique_schematic_ids"])

    bs = ov["block_stats"]
    if bs:
        add()
        subsection_header("Block Counts (per generation entry)")
        kv("Total blocks across all circuits", bs["total"])
        kv("Average blocks per circuit", bs["average"])
        kv("Min blocks", bs["min"])
        kv("Max blocks", bs["max"])
        kv("Median blocks", bs["median"])

    # =================================================================
    # 2. Quality Issues
    # =================================================================
    section_header("2. QUALITY ISSUES")

    qi = report["quality_issues"]

    warning_flag("Entity blocks (unplaceable)", qi["entity_blocks"],
                  "state starts with 'entity:'")
    warning_flag("Blocks with float coordinates", qi["float_coordinate_blocks"],
                  "x/y/z may be non-integer")
    warning_flag("Empty descriptions", qi["empty_descriptions"],
                  "discord_metadata.description is empty/missing")
    warning_flag("Duplicate schematic IDs", qi["duplicate_schematic_ids"],
                  "IDs shared across multiple entries")

    if qi.get("missing_metadata"):
        add()
        subsection_header("Missing Metadata")
        for field, count in sorted(qi["missing_metadata"].items()):
            warning_flag(f"Missing '{field}'", count)

    add()
    subsection_header("Circuit Size Anomalies")
    for threshold, count in qi.get("oversize_circuits", {}).items():
        warning_flag(f"Circuits > {threshold} blocks", count)
    warning_flag(f"Circuits < 5 blocks (undersized)", qi["undersize_circuits_lt_5"])

    if verbose and qi.get("duplicate_schematic_id_list"):
        dups = qi["duplicate_schematic_id_list"]
        if dups:
            add()
            subsection_header("Duplicate Schematic ID Details")
            for sid, count in sorted(dups.items(), key=lambda x: -x[1]):
                warn_kv(sid, f"appears {count} times")

    # =================================================================
    # 3. Category Distribution
    # =================================================================
    section_header("3. CATEGORY DISTRIBUTION")

    cd = report["category_distribution"]
    add(f"  {Colors.bold('Categories')}")
    for cat, count in sorted(cd.get("category_counts", {}).items(),
                              key=lambda x: -x[1]):
        kv(cat, count, indent=4)

    add()
    add(f"  {Colors.bold('Difficulty Tiers')}")
    for tier, count in sorted(cd.get("difficulty_counts", {}).items(),
                               key=lambda x: -x[1]):
        kv(tier, count, indent=4)

    # =================================================================
    # 4. Block Type Analysis
    # =================================================================
    section_header("4. BLOCK TYPE ANALYSIS")

    ba = report["block_type_analysis"]
    kv("Unique block types (base name)", ba["unique_block_types"])
    kv("Unique block states (full)", ba["unique_block_states"])
    kv("Redstone-specific blocks total", ba["total_redstone_blocks"])

    add()
    add(f"  {Colors.bold('Top 20 Most Common Block States')}")
    add(f"  {Colors.dim('-' * 40)}")
    for i, (state, count) in enumerate(ba.get("top_20_block_states", []), 1):
        add(f"    {i:>2}. {Colors.dim(state)}  {Colors.ok(f'({count})')}")

    if verbose and ba.get("redstone_block_types"):
        add()
        add(f"  {Colors.bold('Redstone Block Breakdown')}")
        add(f"  {Colors.dim('-' * 40)}")
        for state, count in sorted(ba["redstone_block_types"].items(),
                                    key=lambda x: -x[1]):
            kv(state, count, indent=4)

    # =================================================================
    # 5. Corruption Analysis
    # =================================================================
    section_header("5. CORRUPTION ANALYSIS")

    ca = report["corruption_analysis"]
    kv("Total corruption entries", ca["total_corruption_entries"])
    kv("Average modifications per entry", ca["average_modifications_per_entry"])

    add()
    add(f"  {Colors.bold('Corruption Types')}")
    for ctype, count in sorted(ca.get("corruption_type_counts", {}).items(),
                                key=lambda x: -x[1]):
        kv(ctype, count, indent=4)

    # =================================================================
    # 6. Description Quality
    # =================================================================
    section_header("6. DESCRIPTION QUALITY")

    dq = report["description_quality"]
    kv("Total generation entries with descriptions", dq["total_descriptions"])
    kv("Non-empty descriptions", dq["non_empty_descriptions"])
    kv("Empty descriptions", dq["empty_descriptions"])
    kv("Average description length (chars)", dq["average_length_chars"])
    kv("Min description length (chars)", dq["min_length_chars"])
    kv("Max description length (chars)", dq["max_length_chars"])
    kv('Archiver Bot format (starts with "-")',
       dq["archiver_bot_format_starts_with_dash"])

    add()
    add(f"{Colors.head('=' * 60)}")
    add(f"{Colors.ok('Report complete.')}")

    return "\n".join(lines)


def format_report_json(report: Dict[str, Any]) -> str:
    """Format report as JSON."""
    return json.dumps(report, indent=2, default=str)


# ---------------------------------------------------------------------------
# Verbose helpers
# ---------------------------------------------------------------------------

def build_verbose_details(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build per-circuit details for verbose mode."""
    gen_entries = [e for e in entries if e.get("type") == "generation"]

    details = []
    for e in gen_entries:
        bl = e.get("block_list", [])
        desc = e.get("discord_metadata", {}).get("description", "")
        cat = e.get("discord_metadata", {}).get("category", "?")
        sid = e.get("schematic_id", "?")
        author = e.get("schematic_metadata", {}).get("author", "?")

        entity_count = sum(1 for b in bl
                           if b.get("state", "").startswith("entity:"))
        redstone_count = sum(1 for b in bl
                             if b.get("state", "").split("[")[0].split(":", 1)[-1]
                             in REDSTONE_BLOCKS)

        ds_len = len(e.get("deconstruction_steps", []))
        bs_len = len(e.get("build_steps", []))

        details.append({
            "schematic_id": sid,
            "author": author,
            "category": cat,
            "blocks": len(bl),
            "entity_blocks": entity_count,
            "redstone_blocks": redstone_count,
            "deconstruction_steps": ds_len,
            "build_steps": bs_len,
            "description_preview": desc[:120] + "..." if len(desc) > 120 else desc,
            "has_contract": bool(e.get("verify_contract", "")),
        })

    return {"per_circuit": details}


def format_verbose_text(report: Dict[str, Any],
                        verbose_details: Dict[str, Any]) -> str:
    """Format verbose per-circuit details as colored text."""
    lines: List[str] = []
    details = verbose_details.get("per_circuit", [])

    if not details:
        return ""

    lines.append("")
    lines.append(f"{Colors.head('=' * 60)}")
    lines.append(f"{Colors.head('  PER-CIRCUIT DETAILS')}")
    lines.append(f"{Colors.head('=' * 60)}")
    lines.append("")
    lines.append(f"  {Colors.dim(str(len(details)) + ' generation entries shown')}")
    lines.append("")

    for i, d in enumerate(details, 1):
        lines.append(f"  {Colors.bold(f'Circuit #{i}:')} "
                     f"{Colors.info(d['schematic_id'])}")
        lines.append(f"    {'Blocks:':<22} {d['blocks']}")
        lines.append(f"    {'Author:':<22} {Colors.dim(d['author'])}")
        lines.append(f"    {'Category:':<22} {d['category']}")

        if d['entity_blocks'] > 0:
            lines.append(f"    {'Entity blocks:':<22} "
                         f"{Colors.warn(str(d['entity_blocks']))}")
        else:
            lines.append(f"    {'Entity blocks:':<22} 0")

        lines.append(f"    {'Redstone blocks:':<22} {d['redstone_blocks']}")
        lines.append(f"    {'Decon. steps:':<22} {d['deconstruction_steps']}")
        lines.append(f"    {'Build steps:':<22} {d['build_steps']}")
        lines.append(f"    {'Has verify contract:':<22} "
                     f"{Colors.ok('yes') if d['has_contract'] else Colors.warn('no')}")

        if d['description_preview']:
            lines.append(f"    {'Description:':<22} {Colors.dim(d['description_preview'])}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze the MIRA training dataset and produce a quality report."
    )
    parser.add_argument(
        "--input",
        default="data/training/discord_dataset.jsonl",
        help="Input JSONL file (default: data/training/discord_dataset.jsonl)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show per-circuit details in addition to summary",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON instead of formatted text",
    )

    args = parser.parse_args()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Resolve input path
    input_path = args.input
    if not os.path.isfile(input_path):
        alt_path = os.path.join(project_root, input_path)
        if os.path.isfile(alt_path):
            input_path = alt_path
        else:
            # Fallback: look for any .jsonl in data/training/
            training_dir = os.path.join(project_root, "data", "training")
            fallback_candidates = []
            if os.path.isdir(training_dir):
                fallback_candidates = sorted(
                    f for f in os.listdir(training_dir) if f.endswith(".jsonl")
                )
            if fallback_candidates:
                fallback_path = os.path.join(training_dir, fallback_candidates[0])
                print(f"{Colors.warn('Warning:')} Specified input not found. "
                      f"Using fallback: {Colors.info(fallback_path)}",
                      file=sys.stderr)
                input_path = fallback_path
            else:
                print(f"{Colors.error('Error:')} Input file not found: "
                      f"{args.input}",
                      file=sys.stderr)
                sys.exit(1)

    # Load data
    try:
        entries = load_entries(input_path)
    except json.JSONDecodeError as e:
        print(f"{Colors.error('Error:')} Invalid JSONL file: {e}",
              file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.error('Error:')} Failed to load {input_path}: {e}",
              file=sys.stderr)
        sys.exit(1)

    if not entries:
        print(f"{Colors.warn('Warning:')} No entries found in {input_path}.")
        sys.exit(0)

    # Detect format and normalize
    fmt = detect_format(entries)
    if fmt == "legacy":
        print(f"{Colors.info('Info:')} Detected legacy dataset format "
              f"({len(entries)} entries). Normalizing for analysis.",
              file=sys.stderr)
    entries = normalize_entries(entries, fmt)

    # Build report
    report = build_report(entries)

    # Verbose details
    verbose_details = build_verbose_details(entries) if args.verbose else {}

    if args.json:
        # Merge verbose details into report for JSON output
        report["_format"] = fmt
        if args.verbose and verbose_details:
            report["per_circuit"] = verbose_details.get("per_circuit", [])
        print(format_report_json(report))
    else:
        # Text output
        output = format_report_text(report, verbose=args.verbose)
        if args.verbose and verbose_details:
            output += format_verbose_text(report, verbose_details)
        print(output)


if __name__ == "__main__":
    main()
