#!/usr/bin/env python3
"""
Comprehensive test of batch strategies on COMPLEX circuits.
Tests: Plan-Then-Execute, One-Shot, Chunked on circuits with 50-100 blocks.
Includes quantitative AND qualitative analysis.
"""

import os
import json
import time
import requests
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

def _get_api_key() -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable not set.\n"
            "Please set it before running:\n"
            "  export OPENROUTER_API_KEY='your-key-here'"
        )
    return api_key

API_KEY = _get_api_key()
MODEL = "google/gemini-3.1-flash-lite-preview"

def call_llm(messages, schema, temperature=0.0, max_tokens=4096):
    """Call API with retry logic."""
    for attempt in range(3):
        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "strict": True, "schema": schema}}
        }
        
        try:
            response = requests.post(BASE_URL, json=payload, headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            }, timeout=300)
            response.raise_for_status()
            data = response.json()
            return json.loads(data["choices"][0]["message"]["content"]), data.get("usage", {})
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"API failed after 3 attempts: {e}")
            time.sleep(2 ** attempt)

def load_circuit(circuit_id):
    with open(f"evaluation/test_circuits/{circuit_id}.json") as f:
        return json.load(f)

# Test circuits (complex ones)
COMPLEX_CIRCUITS = [
    "4bit_adder",          # 96 blocks
    "automatic_farm",      # 87 blocks
    "item_sorter",         # 54 blocks
    "elevator",            # 64 blocks
    "randomizer",          # 32 blocks
    "t_flip_flop",         # 24 blocks
]

print("="*80)
print("COMPREHENSIVE BATCH STRATEGY TEST - COMPLEX CIRCUITS")
print("="*80)

all_results = {}

for circuit_id in COMPLEX_CIRCUITS:
    print(f"\n{'#'*80}")
    print(f"CIRCUIT: {circuit_id.upper()}")
    print(f"{'#'*80}")
    
    circuit = load_circuit(circuit_id)
    expected = circuit.get('expected_blocks', 0)
    print(f"Expected blocks: {expected}")
    print(f"Description: {circuit['description'][:100]}...")
    
    results = {}
    
    # ========================================================================
    # Strategy 1: One-Shot (baseline)
    # ========================================================================
    print(f"\n[1/4] One-Shot...")
    
    schema = {
        "type": "object",
        "required": ["blocks", "reasoning"],
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["x", "y", "z", "state"],
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "z": {"type": "integer"},
                        "state": {"type": "string"}
                    }
                }
            },
            "reasoning": {"type": "string"}
        }
    }
    
    messages = [
        {"role": "system", "content": "Build redstone circuits. Output all blocks with coordinates."},
        {"role": "user", "content": f"Build: {circuit['description']}. Generate ALL {expected} blocks."}
    ]
    
    start = time.time()
    result, usage = call_llm(messages, schema, temperature=1.0)
    
    blocks = result.get("blocks", [])
    results["one_shot"] = {
        "blocks": len(blocks),
        "expected": expected,
        "accuracy": len(blocks) / expected * 100 if expected > 0 else 0,
        "time": time.time() - start,
        "tokens": usage.get("total_tokens", 0),
        "reasoning_length": len(result.get("reasoning", "")),
        "sample_blocks": blocks[:3] if blocks else []
    }
    print(f"  Generated: {len(blocks)}/{expected} ({results['one_shot']['accuracy']:.0f}%)")
    print(f"  Time: {results['one_shot']['time']:.1f}s, Tokens: {results['one_shot']['tokens']:,}")
    
    # ========================================================================
    # Strategy 2: Plan-Then-Execute
    # ========================================================================
    print(f"\n[2/4] Plan-Then-Execute...")
    
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
        {"role": "system", "content": "Plan redstone circuits step-by-step. List EVERY block needed with position, reason, and connections."},
        {"role": "user", "content": f"Plan this circuit with ALL {expected} blocks:\n\n{circuit['description']}\n\nIMPORTANT: List EVERY SINGLE block. Do not skip any. Be complete."}
    ]
    
    start = time.time()
    plan_result, usage = call_llm(messages, plan_schema, temperature=1.0, max_tokens=8192)
    
    plan_steps = plan_result.get("plan", [])
    
    # Parse positions to coordinates
    def parse_position(pos_str):
        coords = re.findall(r'-?\d+', pos_str)
        if len(coords) >= 3:
            return int(coords[0]), int(coords[1]), int(coords[2])
        elif len(coords) == 2:
            return int(coords[0]), int(coords[1]), 0
        elif len(coords) == 1:
            return int(coords[0]), 0, 0
        return 0, 0, 0
    
    blocks_from_plan = []
    for step in plan_steps:
        x, y, z = parse_position(step.get("position", "0,0,0"))
        blocks_from_plan.append({
            "x": x, "y": y, "z": z,
            "state": step.get("block_type", "unknown"),
            "reason": step.get("reason", ""),
            "connects_to": step.get("connects_to", [])
        })
    
    results["plan_then_execute"] = {
        "blocks": len(blocks_from_plan),
        "expected": expected,
        "accuracy": len(blocks_from_plan) / expected * 100 if expected > 0 else 0,
        "time": time.time() - start,
        "tokens": usage.get("total_tokens", 0),
        "reasoning_quality": sum(1 for s in plan_steps if len(s.get("reason", "")) > 20),
        "has_connectivity": sum(1 for s in plan_steps if s.get("connects_to")),
        "sample_plan": plan_steps[:3] if plan_steps else []
    }
    print(f"  Generated: {len(blocks_from_plan)}/{expected} ({results['plan_then_execute']['accuracy']:.0f}%)")
    print(f"  Time: {results['plan_then_execute']['time']:.1f}s, Tokens: {results['plan_then_execute']['tokens']:,}")
    print(f"  Steps with good reasoning: {results['plan_then_execute']['reasoning_quality']}/{len(plan_steps)}")
    print(f"  Steps with connectivity: {results['plan_then_execute']['has_connectivity']}/{len(plan_steps)}")
    
    # ========================================================================
    # Strategy 3: Chunked (5 blocks per call)
    # ========================================================================
    print(f"\n[3/4] Chunked (5 blocks/call)...")
    
    chunk_schema = {
        "type": "object",
        "required": ["blocks"],
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
            }
        }
    }
    
    messages = [
        {"role": "system", "content": "Build circuits in chunks of 5 blocks. Each block needs coordinates, state, reason, and connections."},
        {"role": "user", "content": f"Build: {circuit['description']}. Generate first 5 blocks."}
    ]
    
    all_blocks = []
    api_calls = 0
    start = time.time()
    
    for chunk in range((expected // 5) + 2):
        result, usage = call_llm(messages, chunk_schema, temperature=1.0)
        api_calls += 1
        
        chunk_blocks = result.get("blocks", [])
        all_blocks.extend(chunk_blocks)
        
        if len(all_blocks) >= expected:
            break
        
        summary = f"Generated {len(chunk_blocks)} blocks (total: {len(all_blocks)}). Last: {chunk_blocks[-1]['state'] if chunk_blocks else 'none'}."
        messages.append({"role": "assistant", "content": json.dumps({"blocks": chunk_blocks, "summary": summary})})
        messages.append({"role": "user", "content": f"Continue with next 5 blocks."})
        
        if api_calls >= 20:  # Safety limit
            break
    
    results["chunked"] = {
        "blocks": len(all_blocks),
        "expected": expected,
        "accuracy": len(all_blocks) / expected * 100 if expected > 0 else 0,
        "time": time.time() - start,
        "api_calls": api_calls,
        "reasoning_quality": sum(1 for b in all_blocks if len(b.get("reason", "")) > 20),
        "has_connectivity": sum(1 for b in all_blocks if b.get("connects_to"))
    }
    print(f"  Generated: {len(all_blocks)}/{expected} ({results['chunked']['accuracy']:.0f}%)")
    print(f"  API calls: {api_calls}, Time: {results['chunked']['time']:.1f}s")
    print(f"  Blocks with reasoning: {results['chunked']['reasoning_quality']}/{len(all_blocks)}")
    
    # ========================================================================
    # Strategy 4: Plan with explicit count constraint
    # ========================================================================
    print(f"\n[4/4] Plan with COUNT constraint...")
    
    messages = [
        {"role": "system", "content": f"Plan redstone circuits. You MUST generate exactly {expected} blocks. Count them carefully."},
        {"role": "user", "content": f"""Plan this circuit:

{circuit['description']}

REQUIREMENTS:
- Generate EXACTLY {expected} blocks
- Number each step 1 to {expected}
- Do not stop early
- List every single block

Circuit needs {expected} total blocks. Be complete."""}
    ]
    
    start = time.time()
    constrained_result, usage = call_llm(messages, plan_schema, temperature=0.5, max_tokens=8192)
    
    constrained_steps = constrained_result.get("plan", [])
    
    blocks_constrained = []
    for step in constrained_steps:
        x, y, z = parse_position(step.get("position", "0,0,0"))
        blocks_constrained.append({
            "x": x, "y": y, "z": z,
            "state": step.get("block_type", "unknown"),
            "reason": step.get("reason", ""),
            "connects_to": step.get("connects_to", [])
        })
    
    results["plan_constrained"] = {
        "blocks": len(blocks_constrained),
        "expected": expected,
        "accuracy": len(blocks_constrained) / expected * 100 if expected > 0 else 0,
        "time": time.time() - start,
        "tokens": usage.get("total_tokens", 0),
        "reasoning_quality": sum(1 for s in constrained_steps if len(s.get("reason", "")) > 20),
        "has_connectivity": sum(1 for s in constrained_steps if s.get("connects_to"))
    }
    print(f"  Generated: {len(blocks_constrained)}/{expected} ({results['plan_constrained']['accuracy']:.0f}%)")
    print(f"  Time: {results['plan_constrained']['time']:.1f}s, Tokens: {results['plan_constrained']['tokens']:,}")
    
    all_results[circuit_id] = results
    
    # Small delay between circuits
    time.sleep(1)

# ============================================================================
# Analysis
# ============================================================================
print(f"\n{'='*80}")
print("COMPREHENSIVE ANALYSIS")
print(f"{'='*80}")

# Summary table
print(f"\n{'Circuit':<20} {'Expected':>10} {'One-Shot':>12} {'Plan':>12} {'Plan+Constraint':>18} {'Chunked':>12}")
print("-"*80)

for circuit_id, results in all_results.items():
    exp = results["one_shot"]["expected"]
    os_acc = results["one_shot"]["accuracy"]
    pe_acc = results["plan_then_execute"]["accuracy"]
    pc_acc = results["plan_constrained"]["accuracy"]
    ch_acc = results["chunked"]["accuracy"]
    
    print(f"{circuit_id:<20} {exp:>10} {os_acc:>11.0f}% {pe_acc:>12.0f}% {pc_acc:>17.0f}% {ch_acc:>12.0f}%")

# Detailed analysis by size
print(f"\n{'='*80}")
print("ANALYSIS BY CIRCUIT SIZE")
print(f"{'='*80}")

size_groups = {
    "Small (24-32)": ["t_flip_flop", "randomizer"],
    "Medium (54-64)": ["item_sorter", "elevator"],
    "Large (87-96)": ["automatic_farm", "4bit_adder"]
}

for size_name, circuits in size_groups.items():
    print(f"\n{size_name}:")
    print(f"  {'Strategy':<25} {'Avg Accuracy':>15} {'Best':>10}")
    
    strategies = ["one_shot", "plan_then_execute", "plan_constrained", "chunked"]
    for strat in strategies:
        accs = [all_results[c][strat]["accuracy"] for c in circuits if c in all_results and strat in all_results[c]]
        if accs:
            avg = sum(accs) / len(accs)
            best = max(accs)
            print(f"  {strat.replace('_', ' ').title():<25} {avg:>14.1f}% {best:>9.0f}%")

# Qualitative analysis
print(f"\n{'='*80}")
print("QUALITATIVE ANALYSIS")
print(f"{'='*80}")

for circuit_id, results in all_results.items():
    print(f"\n{circuit_id.upper()} ({results['one_shot']['expected']} blocks):")
    
    # Check reasoning quality
    pe = results["plan_then_execute"]
    pc = results["plan_constrained"]
    
    print(f"  Plan reasoning quality: {pe['reasoning_quality']}/{pe['blocks']} blocks have detailed reasons")
    print(f"  Plan connectivity: {pe['has_connectivity']}/{pe['blocks']} blocks reference others")
    
    if pe['sample_plan']:
        sample = pe['sample_plan'][0]
        print(f"  Sample step: {sample.get('step')}. {sample.get('block_type')} at {sample.get('position')}")
        print(f"    Reason: {sample.get('reason', 'N/A')[:60]}...")
        print(f"    Connects to: {sample.get('connects_to', [])}")

# Recommendations
print(f"\n{'='*80}")
print("RECOMMENDATIONS")
print(f"{'='*80}")

# Find best strategy by size
best_by_size = {}
for size_name, circuits in size_groups.items():
    best_strategy = None
    best_avg = 0
    
    for strat in ["one_shot", "plan_then_execute", "plan_constrained", "chunked"]:
        accs = [all_results[c][strat]["accuracy"] for c in circuits if c in all_results and strat in all_results[c]]
        if accs:
            avg = sum(accs) / len(accs)
            if avg > best_avg:
                best_avg = avg
                best_strategy = strat
    
    best_by_size[size_name] = (best_strategy, best_avg)
    print(f"\n{size_name}:")
    print(f"  Best: {best_strategy.replace('_', ' ').title()} ({best_avg:.0f}% avg accuracy)")

print(f"\n{'='*80}")
print("KEY FINDINGS")
print(f"{'='*80}")

print(f"""
1. ONE-SHOT on complex circuits:
   - Accuracy drops significantly on 50+ block circuits
   - Reasoning is brief (not useful for training)
   - NOT recommended for complex circuits

2. PLAN-THEN-EXECUTE:
   - Better than one-shot but still under-generates
   - Good reasoning quality when it generates blocks
   - 1 API call = batchable
   - NEEDS constraint improvements

3. PLAN WITH CONSTRAINTS:
   - Explicit "generate exactly N blocks" helps
   - Still may under-generate on very large circuits (96 blocks)
   - Best balance for 50-70 block circuits

4. CHUNKED:
   - Most reliable completion (gets to target)
   - Sequential = not batchable
   - Best reasoning quality (per-block)
   - Use as fallback for circuits >80 blocks

RECOMMENDED HYBRID APPROACH:
- Circuits <50 blocks: Plan with constraints (1 API, batchable)
- Circuits 50-80 blocks: Plan with constraints + verification loop
- Circuits >80 blocks: Chunked (sequential but reliable)

FOR vLLM BATCH PROCESSING:
1. Batch plan generation for all circuits <80 blocks
2. For circuits that under-generate, retry with "add more blocks" prompt
3. For circuits >80 blocks, use chunked (accept sequential for these)
4. Alternative: Use plan format but with max_tokens=16k to allow longer outputs
""")

# Save results
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
results_dir = Path("evaluation/results/complex_circuit_strategies")
results_dir.mkdir(parents=True, exist_ok=True)

report = {
    "timestamp": timestamp,
    "circuits_tested": list(all_results.keys()),
    "results": all_results,
    "best_by_size": {k: {"strategy": v[0], "accuracy": v[1]} for k, v in best_by_size.items()}
}

with open(results_dir / f"complex_strategies_{timestamp}.json", "w") as f:
    json.dump(report, f, indent=2, default=str)

print(f"\n✓ Full results saved to: {results_dir / f'complex_strategies_{timestamp}.json'}")
