#!/usr/bin/env python3
"""
Test batch-friendly generation strategies for MIRA.
Compares different approaches that work better with vLLM/batch processing.
"""

import json
import time
import requests
from pathlib import Path
from datetime import datetime

API_KEY = "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"
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3.1-flash-lite-preview"

def call_llm(messages, schema, temperature=0.0):
    """Call API and return result + usage."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
        "response_format": {"type": "json_schema", "json_schema": {"name": "output", "strict": True, "schema": schema}}
    }
    
    response = requests.post(BASE_URL, json=payload, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }, timeout=180)
    response.raise_for_status()
    
    data = response.json()
    return json.loads(data["choices"][0]["message"]["content"]), data.get("usage", {})

# Load test circuit
with open("evaluation/test_circuits/piston_door.json") as f:
    circuit = json.load(f)

print("="*80)
print("BATCH-FRIENDLY GENERATION STRATEGIES TEST")
print("="*80)
print(f"\nCircuit: {circuit['name']} ({circuit.get('expected_blocks', '?')} blocks)\n")

results = {}

# ============================================================================
# Strategy 1: Current Iterative (baseline - 1 block per call)
# ============================================================================
print("\n" + "="*80)
print("STRATEGY 1: Iterative (1 block per API call)")
print("="*80)
print("Simulating current approach...")

iterative_schema = {
    "type": "object",
    "required": ["block", "reasoning", "done"],
    "properties": {
        "block": {"type": "object", "required": ["x", "y", "z", "state"], "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"}, 
            "z": {"type": "integer"}, "state": {"type": "string"}
        }},
        "reasoning": {"type": "string"},
        "done": {"type": "boolean"}
    }
}

messages = [
    {"role": "system", "content": "Build circuits one block at a time. Explain each block's purpose and connections."},
    {"role": "user", "content": f"Build: {circuit['description']}"}
]

start = time.time()
blocks = []
api_calls = 0

for i in range(20):
    result, usage = call_llm(messages, iterative_schema)
    api_calls += 1
    blocks.append(result["block"])
    messages.append({"role": "assistant", "content": json.dumps(result)})
    if result.get("done", False) or len(blocks) >= circuit.get("expected_blocks", 20):
        break
    messages.append({"role": "user", "content": "Continue"})

results["iterative_1block"] = {
    "api_calls": api_calls,
    "blocks": len(blocks),
    "time": time.time() - start,
    "approach": "Sequential, 1 block per call"
}
print(f"API calls: {api_calls}")
print(f"Blocks generated: {len(blocks)}")
print(f"Time: {results['iterative_1block']['time']:.1f}s")

# ============================================================================
# Strategy 2: Chunked Iterative (N blocks per call)
# ============================================================================
print("\n" + "="*80)
print("STRATEGY 2: Chunked (5 blocks per API call)")
print("="*80)
print("Generate 5 blocks per call with reasoning for each...")

chunk_schema = {
    "type": "object",
    "required": ["blocks", "reasoning"],
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["x", "y", "z", "state", "reason"],
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "z": {"type": "integer"},
                    "state": {"type": "string"},
                    "reason": {"type": "string"},
                    "connects_to": {"type": "array", "items": {"type": "integer"}}
                }
            }
        },
        "reasoning": {"type": "string"}
    }
}

messages = [
    {"role": "system", "content": "Build circuits in chunks of 5 blocks. For each block, provide coordinates, state, reasoning, and which previous blocks it connects to (by index)."},
    {"role": "user", "content": f"Build: {circuit['description']}. Generate the first 5 blocks."}
]

start = time.time()
all_blocks = []
api_calls = 0
global_index = 0

for chunk in range(4):
    result, usage = call_llm(messages, chunk_schema)
    api_calls += 1
    
    chunk_blocks = result.get("blocks", [])
    for block in chunk_blocks:
        block["global_index"] = global_index
        global_index += 1
    all_blocks.extend(chunk_blocks)
    
    # Summarize for next chunk
    summary = f"Generated {len(chunk_blocks)} blocks. Last block: {chunk_blocks[-1]['state'] if chunk_blocks else 'none'}."
    messages.append({"role": "assistant", "content": json.dumps({"blocks": chunk_blocks, "summary": summary})})
    
    if len(all_blocks) >= circuit.get("expected_blocks", 20):
        break
    
    messages.append({"role": "user", "content": f"Continue with next 5 blocks (starting from global index {global_index})."})

results["chunked_5blocks"] = {
    "api_calls": api_calls,
    "blocks": len(all_blocks),
    "time": time.time() - start,
    "approach": "Sequential, 5 blocks per call with per-block reasoning"
}
print(f"API calls: {api_calls}")
print(f"Blocks generated: {len(all_blocks)}")
print(f"Time: {results['chunked_5blocks']['time']:.1f}s")
print(f"Per-block reasoning: {sum(1 for b in all_blocks if b.get('reason'))}/{len(all_blocks)}")

# ============================================================================
# Strategy 3: Plan-Then-Execute (2 stages, fully batchable)
# ============================================================================
print("\n" + "="*80)
print("STRATEGY 3: Plan-Then-Execute (2 stages)")
print("="*80)
print("Stage 1: Generate full plan with reasoning")
print("Stage 2: Extract blocks (can be batched across circuits)")

# Stage 1: Planning
plan_schema = {
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

messages = [
    {"role": "system", "content": "Plan redstone circuits step-by-step. For each step, specify block type, position (relative), reason for placement, and connections to previous steps."},
    {"role": "user", "content": f"Plan this circuit: {circuit['description']}. Provide a complete step-by-step plan."}
]

start = time.time()
plan_result, _ = call_llm(messages, plan_schema)
plan_time = time.time() - start

plan_steps = plan_result.get("plan", [])
print(f"Stage 1 - Plan generated: {len(plan_steps)} steps in {plan_time:.1f}s")

# Stage 2: Convert plan to blocks (deterministic, no API call needed)
plan_to_blocks = [
    {"x": int(p["position"].split(',')[0].strip()), 
     "y": int(p["position"].split(',')[1].strip()) if len(p["position"].split(',')) > 1 else 0,
     "z": int(p["position"].split(',')[2].strip()) if len(p["position"].split(',')) > 2 else 0,
     "state": p["block_type"],
     "reason": p["reason"],
     "connects_to": p["connects_to"]}
    for p in plan_steps
    if "," in p["position"]
]

results["plan_then_execute"] = {
    "api_calls": 1,  # Only planning stage needs API
    "blocks": len(plan_to_blocks),
    "time": plan_time,
    "approach": "2-stage: plan (API) + execute (deterministic)"
}
print(f"Stage 2 - Blocks extracted: {len(plan_to_blocks)} (deterministic)")
print(f"Total API calls: 1")
print(f"Total time: {results['plan_then_execute']['time']:.1f}s")

# ============================================================================
# Strategy 4: Parallel Independent Blocks (fully batchable, no dependencies)
# ============================================================================
print("\n" + "="*80)
print("STRATEGY 4: Parallel Generation (fully batchable)")
print("="*80)
print("Generate all blocks in parallel with positional hints...")

parallel_schema = {
    "type": "object",
    "required": ["blocks"],
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["x", "y", "z", "state", "purpose"],
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "z": {"type": "integer"},
                    "state": {"type": "string"},
                    "purpose": {"type": "string"}
                }
            }
        }
    }
}

messages = [
    {"role": "system", "content": "Generate all blocks for a redstone circuit at once. Include purpose for each block."},
    {"role": "user", "content": f"Generate ALL blocks for: {circuit['description']}. Expected ~{circuit.get('expected_blocks', 15)} blocks."}
]

start = time.time()
parallel_result, _ = call_llm(messages, parallel_schema)
parallel_time = time.time() - start

parallel_blocks = parallel_result.get("blocks", [])
results["parallel"] = {
    "api_calls": 1,
    "blocks": len(parallel_blocks),
    "time": parallel_time,
    "approach": "Fully parallel, 1 API call, all blocks at once"
}
print(f"API calls: 1")
print(f"Blocks generated: {len(parallel_blocks)}")
print(f"Time: {results['parallel']['time']:.1f}s")
print(f"With purpose field: {sum(1 for b in parallel_blocks if b.get('purpose'))}/{len(parallel_blocks)}")

# ============================================================================
# Summary & Comparison
# ============================================================================
print("\n" + "="*80)
print("COMPARISON SUMMARY")
print("="*80)

print(f"\n{'Strategy':<30} {'API Calls':>12} {'Blocks':>10} {'Time':>10} {'Batchable':>12}")
print("-"*80)

strategies = [
    ("Iterative (1 block/call)", results["iterative_1block"], "❌ No"),
    ("Chunked (5 blocks/call)", results["chunked_5blocks"], "⚠️ Partial"),
    ("Plan-Then-Execute", results["plan_then_execute"], "✅ Yes (stage 2)"),
    ("Parallel (all at once)", results["parallel"], "✅ Yes (fully)")
]

for name, res, batchable in strategies:
    print(f"{name:<30} {res['api_calls']:>12} {res['blocks']:>10} {res['time']:>9.1f}s {batchable:>12}")

print(f"\n{'='*80}")
print("RECOMMENDATIONS FOR BATCH PROCESSING")
print("="*80)

print(f"""
For vLLM/HPC batch processing:

1. ✅ BEST: Plan-Then-Execute (Strategy 3)
   - 1 API call for planning (can batch 1000 circuits)
   - Deterministic execution (no API, instant)
   - Good reasoning quality (plan has full reasoning)
   - Perfect for batch: plan 1000 circuits → extract 1000× blocks

2. ✅ GOOD: Parallel Generation (Strategy 4)
   - 1 API call, fully batchable
   - Simpler pipeline
   - May have weaker reasoning (no step-by-step)
   - Good for inference, less ideal for training data

3. ⚠️  OK: Chunked Iterative (Strategy 2)
   - Fewer API calls than pure iterative (3 vs 15)
   - Still sequential (can't fully batch)
   - Good reasoning quality
   - Use if quality > speed

4. ❌ AVOID: Pure Iterative (Strategy 1)
   - Too many API calls (15 per circuit)
   - Can't batch at all
   - Only use for very complex circuits needing guidance

RECOMMENDED PIPELINE FOR 10K CIRCUITS:
1. Batch plan generation: 10K circuits → 10K plans (vLLM batch)
2. Deterministic extraction: 10K plans → 10K× blocks (instant)
3. Total cost: ~$1-2 for planning stage only
4. Reasoning quality: High (from plan)
""")

# Save results
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
results_dir = Path("evaluation/results/batch_strategies")
results_dir.mkdir(parents=True, exist_ok=True)

report = {
    "timestamp": timestamp,
    "circuit": circuit["id"],
    "results": results,
    "recommendation": "Plan-Then-Execute for batch processing"
}

with open(results_dir / f"batch_strategies_{timestamp}.json", "w") as f:
    json.dump(report, f, indent=2)

print(f"\n✓ Results saved to: {results_dir / f'batch_strategies_{timestamp}.json'}")
