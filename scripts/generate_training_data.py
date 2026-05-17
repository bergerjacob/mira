#!/usr/bin/env python3
"""
MIRA Training Data Generation Pipeline.

Takes converted circuit data (block_list.jsonl) and enriches each circuit
with LLM-generated descriptions, verification contracts, and deconstruction reasoning.

Usage:
    python scripts/generate_training_data.py
    python scripts/generate_training_data.py --dry-run --limit 3
    python scripts/generate_training_data.py --resume --tasks description,verify
    python scripts/generate_training_data.py --model qwen/qwen3.5-122b-a10b --fallback-model deepseek/deepseek-v4-flash
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.llm_client import OpenRouterClient, ChatMessage

# ── Cost per model (USD per million tokens) ──────────────────────────────────
# Source: OpenRouter pricing as of May 2026
MODEL_COST = {
    "google/gemini-3.1-flash-lite-preview": {"input": 0.10, "output": 0.40},
    "deepseek/deepseek-v4-flash": {"input": 0.10, "output": 0.40},
    "qwen/qwen3.5-122b-a10b": {"input": 0.35, "output": 1.40},
}

# ── Token budgets per task ───────────────────────────────────────────────────
# Models with reasoning tokens (qwen3.5, deepseek) need much higher budgets
# because reasoning tokens count against max_tokens.
# Base budgets are for non-reasoning models; reasoning models get a multiplier.
TOKEN_BUDGETS = {
    "description": 1024,
    "verify": 4096,
    "deconstruction": 4096,
}

# Models that use reasoning tokens need higher max_tokens budgets
REASONING_MODELS = {
    "qwen/qwen3.5-122b-a10b",
    # deepseek-v4-flash: no reasoning tokens when max_tokens >= 4096
    # gemini-flash-lite: no reasoning tokens
}

# Multiplier for reasoning models (they consume tokens for thinking)
REASONING_MULTIPLIER = 4


def get_max_tokens(task: str, model_id: str) -> int:
    """Get max_tokens for a task, applying reasoning multiplier if needed."""
    base = TOKEN_BUDGETS.get(task, 4096)
    if any(rm in model_id for rm in REASONING_MODELS):
        return base * REASONING_MULTIPLIER
    return base

# ── Prompt templates (from test_llm_quality.py, validated in testing) ────────

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

# ── Deconstruction JSON schema for structured output ─────────────────────────
DECONSTRUCTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "remove_blocks": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 3,
                "maxItems": 3,
            },
        },
    },
    "required": ["reasoning", "remove_blocks"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def format_blocks(blocks: list[dict], max_blocks: int = 200) -> str:
    """Format block list for prompt inclusion.

    Formats as ``(x, y, z): state`` lines, truncating at ``max_blocks``
    with a note about remaining blocks.
    """
    lines = []
    for b in blocks[:max_blocks]:
        lines.append(f"({b['x']}, {b['y']}, {b['z']}): {b['state']}")
    if len(blocks) > max_blocks:
        lines.append(f"... and {len(blocks) - max_blocks} more blocks")
    return "\n".join(lines)


def strip_markdown_code_blocks(text: str, language: str = "python") -> str:
    """Strip markdown code-block fences from LLM output.

    Handles::

        ```python
        code here
        ```

        ```py
        code here
        ```

        ```json
        {"key": "value"}
        ```

    Falls back to returning ``text`` unchanged if no fences found.
    """
    # Match ```<lang> ... ``` (with optional leading/trailing whitespace)
    pattern = re.compile(
        r"^```" + re.escape(language) + r"\s*\n(.*?)\n```\s*$",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    if match:
        return match.group(1).strip()

    # Generic ``` ... ``` (any or no language tag)
    generic = re.compile(
        r"^```(?:\w*)\s*\n(.*?)\n```\s*$", re.DOTALL | re.MULTILINE
    )
    match = generic.search(text)
    if match:
        return match.group(1).strip()

    return text.strip()


def estimate_cost(model_id: str, usage: dict) -> float:
    """Estimate cost in USD from token usage returned by OpenRouter.

    Falls back to internal pricing table if cost not provided by API.
    """
    # Use API-provided cost if available (most reliable)
    api_cost = usage.get("cost")
    if api_cost is not None:
        return float(api_cost)

    # Fall back to estimated cost from token counts
    pricing = MODEL_COST.get(model_id)
    if pricing is None:
        return 0.0

    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    cost = (prompt_tokens / 1_000_000) * pricing["input"]
    cost += (completion_tokens / 1_000_000) * pricing["output"]
    return round(cost, 6)


def resolve_model_id(model_key: str) -> str:
    """Resolve a model short name or full ID.

    Checks ``OpenRouterClient.MODELS`` first, then our local ``MODELS`` dict
    from ``test_llm_quality.py``, then falls through to use the string as-is.
    """
    # Check client's known models
    if model_key in OpenRouterClient.MODELS:
        return OpenRouterClient.MODELS[model_key]

    # Check for known full model strings passed directly
    known = {
        "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash": "deepseek/deepseek-v4-flash",
        "qwen3.5-122b": "qwen/qwen3.5-122b-a10b",
        "qwen/qwen3.5-122b-a10b": "qwen/qwen3.5-122b-a10b",
    }
    if model_key in known:
        return known[model_key]

    # Assume it's a full model ID already
    return model_key


# ═══════════════════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════════════════


def load_circuits(input_path: str, max_blocks: int = 300) -> list[dict]:
    """Load circuits from converted dataset, filtering by block count."""
    path = Path(input_path)
    if not path.exists():
        print(f"ERROR: Input file not found: {path}")
        sys.exit(1)

    circuits = []
    skipped_big = 0
    with open(path) as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                block_count = len(entry.get("blocks", []))
                if block_count <= max_blocks:
                    circuits.append(entry)
                else:
                    skipped_big += 1

    if skipped_big:
        print(f"  Skipped {skipped_big} circuits with >{max_blocks} blocks")

    # Sort by block count (smallest first) for predictable progression
    circuits.sort(key=lambda c: len(c.get("blocks", [])))
    return circuits


def load_processed_ids(output_path: str) -> set[str]:
    """Load circuit IDs that already have LLM enrichment from existing output.

    Reads the existing output file and collects IDs of entries that have
    a non-empty ``llm_enrichment`` field.
    """
    path = Path(output_path)
    if not path.exists():
        return set()

    processed = set()
    with open(path) as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                if entry.get("llm_enrichment"):
                    processed.add(entry["id"])
    return processed


# ═══════════════════════════════════════════════════════════════════════════════
# Task Generators
# ═══════════════════════════════════════════════════════════════════════════════


def generate_description(
    client: OpenRouterClient, model_id: str, circuit: dict
) -> dict:
    """Generate a circuit description via LLM."""
    name = circuit.get("id", "Unknown").replace("discord_", "")
    category = circuit.get("category", "unknown")
    blocks_str = format_blocks(circuit.get("blocks", []))

    prompt = DESCRIPTION_USER.format(
        name=name,
        category=category,
        block_count=len(circuit.get("blocks", [])),
        blocks_str=blocks_str,
    )

    resp = client.chat(
        model=model_id,
        messages=[ChatMessage(role="user", content=prompt)],
        system_prompt=DESCRIPTION_SYSTEM,
        temperature=0.3,
        max_tokens=get_max_tokens("description", model_id),
    )

    content = (resp.content or "").strip()
    # Debug: log if content is empty but tokens were used (reasoning model issue)
    if not content and resp.usage:
        reasoning_tok = resp.usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
        total_tok = resp.usage.get("completion_tokens", 0)
        if total_tok > 0:
            print(f"    [WARN] Empty content but {total_tok} completion tokens ({reasoning_tok} reasoning)")
    return {
        "content": content,
        "usage": resp.usage,
    }


def generate_verify_contract(
    client: OpenRouterClient, model_id: str, circuit: dict, description: str
) -> dict:
    """Generate a verification contract via LLM."""
    name = circuit.get("id", "Unknown").replace("discord_", "")
    blocks_str = format_blocks(circuit.get("blocks", []))

    prompt = VERIFY_USER.format(
        name=name,
        description=description or circuit.get("description", ""),
        block_count=len(circuit.get("blocks", [])),
        blocks_str=blocks_str,
    )

    resp = client.chat(
        model=model_id,
        messages=[ChatMessage(role="user", content=prompt)],
        system_prompt=VERIFY_SYSTEM,
        temperature=0.1,
        max_tokens=get_max_tokens("verify", model_id),
    )

    content = (resp.content or "").strip()
    # Strip markdown code fences (deepseek often wraps in ```python...```)
    content = strip_markdown_code_blocks(content, "python")

    return {
        "content": content,
        "usage": resp.usage,
    }


def generate_deconstruction(
    client: OpenRouterClient, model_id: str, circuit: dict
) -> dict:
    """Generate deconstruction reasoning via LLM with JSON schema output."""
    blocks_str = format_blocks(circuit.get("blocks", []))

    prompt = DECONSTRUCT_USER.format(
        block_count=len(circuit.get("blocks", [])),
        blocks_str=blocks_str,
    )

    resp = client.chat(
        model=model_id,
        messages=[ChatMessage(role="user", content=prompt)],
        system_prompt=DECONSTRUCT_SYSTEM,
        temperature=0.1,
        max_tokens=get_max_tokens("deconstruction", model_id),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "deconstruction",
                "strict": True,
                "schema": DECONSTRUCTION_SCHEMA,
            },
        },
    )

    content = (resp.content or "").strip()
    usage = resp.usage

    # Try to parse JSON, handling markdown-wrapped responses
    parsed = None
    parse_error = None
    for attempt_text in [content]:
        # First try direct JSON parse
        try:
            parsed = json.loads(attempt_text)
            break
        except (json.JSONDecodeError, TypeError):
            pass

        # Try extracting from markdown code blocks
        for lang in ["json", ""]:
            extracted = strip_markdown_code_blocks(attempt_text, lang)
            if extracted and extracted != attempt_text:
                try:
                    parsed = json.loads(extracted)
                    break
                except (json.JSONDecodeError, TypeError):
                    pass
        if parsed:
            break

    if parsed is None:
        parse_error = "Failed to parse deconstruction JSON from response"

    return {
        "content": content,
        "parsed": parsed,
        "usage": usage,
        "parse_error": parse_error,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Circuit Processing
# ═══════════════════════════════════════════════════════════════════════════════


def process_circuit(
    client: OpenRouterClient,
    circuit: dict,
    model_id: str,
    tasks: list[str],
    fallback_model_id: str | None = None,
) -> dict:
    """Process a single circuit through all requested tasks.

    Returns the input circuit enriched with ``llm_enrichment``.
    If a task fails, its error is recorded and processing continues.
    """
    result = dict(circuit)  # shallow copy
    enrichment = {
        "description": None,
        "verify_contract": None,
        "deconstruction": None,
        "model": model_id,
        "cost_usd": 0.0,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "errors": {},
    }

    total_cost = 0.0
    current_model = model_id

    # ── 1. Description ────────────────────────────────────────────────────
    if "description" in tasks:
        try:
            desc_result = generate_description(client, current_model, circuit)
            content = desc_result["content"]
            if not content:
                # Some models return content in reasoning field when max_tokens is too low
                # Try to get content from raw response
                raw = desc_result.get("raw_response")
                if raw:
                    msg = raw.get("choices", [{}])[0].get("message", {})
                    reasoning = msg.get("reasoning", "")
                    if reasoning and not content:
                        enrichment["errors"]["description"] = "Empty content (reasoning tokens consumed output budget)"
                        content = ""
            enrichment["description"] = content
            total_cost += estimate_cost(current_model, desc_result["usage"])
        except Exception as e:
            enrichment["errors"]["description"] = str(e)
            enrichment["description"] = ""

        time.sleep(1)  # Rate limit

    # ── 2. Verification Contract ──────────────────────────────────────────
    if "verify" in tasks:
        try:
            verify_result = generate_verify_contract(
                client, current_model, circuit, enrichment.get("description") or ""
            )
            enrichment["verify_contract"] = verify_result["content"]
            total_cost += estimate_cost(current_model, verify_result["usage"])
        except Exception as e:
            enrichment["errors"]["verify"] = str(e)
            enrichment["verify_contract"] = ""

        time.sleep(1)

    # ── 3. Deconstruction Reasoning ───────────────────────────────────────
    if "deconstruction" in tasks:
        try:
            decon_result = generate_deconstruction(client, current_model, circuit)
            if decon_result["parse_error"]:
                # Attempt fallback model if available
                if fallback_model_id and fallback_model_id != current_model:
                    print(
                        f"    Deconstruction parse failed with primary model, "
                        f"retrying with fallback: {fallback_model_id}"
                    )
                    time.sleep(2)  # Extra delay between model switches
                    decon_result = generate_deconstruction(
                        client, fallback_model_id, circuit
                    )
                    if decon_result["parsed"]:
                        enrichment["errors"].pop("deconstruction_parse", None)
                        current_model = fallback_model_id
                    else:
                        enrichment["errors"]["deconstruction_parse"] = (
                            decon_result["parse_error"]
                        )
                else:
                    enrichment["errors"]["deconstruction_parse"] = (
                        decon_result["parse_error"]
                    )

            enrichment["deconstruction"] = (
                decon_result["parsed"]
                if decon_result["parsed"]
                else {
                    "reasoning": "FAILED_TO_PARSE",
                    "remove_blocks": [],
                }
            )
            total_cost += estimate_cost(current_model, decon_result["usage"])
        except Exception as e:
            enrichment["errors"]["deconstruction"] = str(e)
            enrichment["deconstruction"] = {
                "reasoning": "ERROR",
                "remove_blocks": [],
            }

        time.sleep(1)

    enrichment["model"] = current_model
    enrichment["cost_usd"] = round(total_cost, 6)
    result["llm_enrichment"] = enrichment
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate LLM-enriched training data for MIRA circuits"
    )
    parser.add_argument(
        "--input",
        default="data/training/converted/block_list.jsonl",
        help="Path to input block_list.jsonl (default: data/training/converted/block_list.jsonl)",
    )
    parser.add_argument(
        "--output",
        default="data/training/llm_enriched.jsonl",
        help="Path to output enriched JSONL (default: data/training/llm_enriched.jsonl)",
    )
    parser.add_argument(
        "--model",
        default="google/gemini-3.1-flash-lite-preview",
        help="Primary model for generation (default: google/gemini-3.1-flash-lite-preview)",
    )
    parser.add_argument(
        "--fallback-model",
        default="deepseek/deepseek-v4-flash",
        help="Fallback model for retries (default: deepseek/deepseek-v4-flash)",
    )
    parser.add_argument(
        "--max-blocks",
        type=int,
        default=300,
        help="Maximum blocks per circuit to process (default: 300, 0=no limit)",
    )
    parser.add_argument(
        "--tasks",
        default="description,verify,deconstruction",
        help="Comma-separated tasks: description,verify,deconstruction (default: all)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip circuits already present in the output file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count circuits to process and estimate cost without making API calls",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of circuits to process (0 = no limit)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Resolve model IDs
    model_id = resolve_model_id(args.model)
    fallback_model_id = resolve_model_id(args.fallback_model)

    print(f"MIRA Training Data Generation Pipeline")
    print(f"{'=' * 60}")
    print(f"  Input:         {args.input}")
    print(f"  Output:        {args.output}")
    print(f"  Model:         {model_id}")
    print(f"  Fallback:      {fallback_model_id}")
    print(f"  Max blocks:    {args.max_blocks if args.max_blocks > 0 else 'unlimited'}")
    print(f"  Tasks:         {args.tasks}")
    print(f"  Resume:        {'yes' if args.resume else 'no'}")
    print(f"  Dry run:       {'yes' if args.dry_run else 'no'}")
    print(f"  Limit:         {args.limit if args.limit > 0 else 'unlimited'}")
    print()

    # Load circuits
    circuits = load_circuits(args.input, args.max_blocks)
    print(f"Loaded {len(circuits)} circuits from {args.input}")

    if not circuits:
        print("No circuits to process. Exiting.")
        return

    # Resume: skip already-processed circuits
    already_done: set[str] = set()
    if args.resume:
        already_done = load_processed_ids(args.output)
        if already_done:
            print(f"Resume mode: {len(already_done)} circuits already processed")
            circuits = [c for c in circuits if c["id"] not in already_done]
            print(f"  Remaining: {len(circuits)} circuits")

    # Apply limit
    if args.limit > 0 and len(circuits) > args.limit:
        circuits = circuits[:args.limit]
        print(f"  Limited to first {args.limit} circuits")

    if not circuits:
        print("No new circuits to process. Exiting.")
        return

    # Parse task list
    task_list = [t.strip() for t in args.tasks.split(",")]
    valid_tasks = {"description", "verify", "deconstruction"}
    for t in task_list:
        if t not in valid_tasks:
            print(f"ERROR: Unknown task '{t}'. Valid tasks: {', '.join(sorted(valid_tasks))}")
            sys.exit(1)

    # Quick summary
    total_blocks = sum(len(c.get("blocks", [])) for c in circuits)
    print(f"\nCircuits to process: {len(circuits)} ({total_blocks} total blocks)")
    print(f"  Tasks: {', '.join(task_list)}")
    for c in circuits:
        print(f"    [{len(c.get('blocks', [])):4d}b] {c['id'][:60]}")
    print()

    # ── Dry-run mode ──────────────────────────────────────────────────────
    if args.dry_run:
        # Estimate cost assuming all circuits get all tasks
        # Rough per-circuit token estimates (description + verify + deconstruction)
        # description: ~3k prompt + ~1k completion
        # verify: ~4k prompt + ~2k completion
        # deconstruction: ~4k prompt + ~2k completion (incl. reasoning tokens)
        # Reasoning models use ~4-8x more output tokens for thinking
        is_reasoning = any(rm in model_id for rm in REASONING_MODELS)
        est_input_tokens = 11000   # sum of all three tasks
        est_output_tokens = 20000 if is_reasoning else 5000

        pricing = MODEL_COST.get(model_id, {"input": 0.35, "output": 1.40})
        est_cost_per_circuit = (
            (est_input_tokens / 1_000_000) * pricing["input"]
            + (est_output_tokens / 1_000_000) * pricing["output"]
        )
        n_tasks = len(task_list)
        est_total = est_cost_per_circuit * len(circuits)
        est_time_sec = len(circuits) * (n_tasks * 2 + 1)  # 2s per task + 1s between circuits

        print("DRY RUN — No API calls will be made")
        print(f"  Estimated cost per circuit (all {n_tasks} tasks): ${est_cost_per_circuit:.6f}")
        print(f"  Estimated total cost: ${est_total:.4f}")
        print(f"  ~{est_time_sec} seconds (~{est_time_sec / 60:.1f} min) with rate limiting")
        return

    # ── Production mode ───────────────────────────────────────────────────
    client = OpenRouterClient()

    # Ensure output directory exists
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stats = {
        "processed": 0,
        "errors": 0,
        "total_cost": 0.0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "start_time": time.time(),
    }

    # Open output file in append mode for resume safety
    output_file = open(output_path, "a" if args.resume and output_path.exists() else "w")
    try:
        for idx, circuit in enumerate(circuits):
            circuit_id = circuit["id"]
            block_count = len(circuit.get("blocks", []))
            print(f"\n[{idx + 1}/{len(circuits)}] {circuit_id[:60]}")
            print(f"  Blocks: {block_count}  Category: {circuit.get('category', '?')}  "
                  f"Difficulty: {circuit.get('difficulty', '?')}")

            result = process_circuit(
                client, circuit, model_id, task_list, fallback_model_id
            )

            enrichment = result.get("llm_enrichment", {})
            errors = enrichment.get("errors", {})

            # Report results
            if enrichment.get("description"):
                desc_preview = enrichment["description"][:80].replace("\n", " ")
                print(f"  Description: {desc_preview}...")
            elif "description" in task_list:
                print(f"  Description: ERROR ({errors.get('description', 'unknown')})")

            if enrichment.get("verify_contract"):
                vc_lines = enrichment["verify_contract"].count("\n") + 1
                vc_first = enrichment["verify_contract"].split("\n")[0][:80]
                print(f"  Verify: {vc_lines} lines — {vc_first}")
            elif "verify" in task_list:
                print(f"  Verify: ERROR ({errors.get('verify', 'unknown')})")

            if enrichment.get("deconstruction"):
                decon = enrichment["deconstruction"]
                if decon.get("reasoning") and decon["reasoning"] not in ("FAILED_TO_PARSE", "ERROR"):
                    decon_preview = decon["reasoning"][:80].replace("\n", " ")
                    n_blocks = len(decon.get("remove_blocks", []))
                    print(f"  Deconstruction: {n_blocks} blocks — {decon_preview}...")
                else:
                    print(f"  Deconstruction: {decon.get('reasoning', 'ERROR')}")
            elif "deconstruction" in task_list:
                print(f"  Deconstruction: ERROR ({errors.get('deconstruction', 'unknown')})")

            # Accumulate usage stats
            cost = enrichment.get("cost_usd", 0.0)
            stats["total_cost"] += cost
            stats["processed"] += 1
            if errors:
                stats["errors"] += 1

            print(f"  Cost: ${cost:.6f}  |  Model: {enrichment.get('model', model_id)}")

            # Write to output file
            output_file.write(json.dumps(result, default=str) + "\n")
            output_file.flush()

            # Rate limiting between circuits
            if idx < len(circuits) - 1:
                delay = 1.0  # 1s between circuits
                time.sleep(delay)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Progress saved.")
    finally:
        output_file.close()

    # ── Final Summary ─────────────────────────────────────────────────────
    elapsed = time.time() - stats["start_time"]
    print(f"\n{'=' * 60}")
    print(f"COMPLETE — {stats['processed']} circuits processed in {elapsed:.0f}s")
    print(f"  Errors:        {stats['errors']}")
    print(f"  Total cost:    ${stats['total_cost']:.6f}")
    print(f"  Output:        {output_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
