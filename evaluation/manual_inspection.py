#!/usr/bin/env python3
"""
Generate sample circuits and manually inspect actual block outputs.
Verify: correct block types, logical positions, valid reasoning.
"""

import os
import json
import requests
import re

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

def call_llm(messages, schema, temperature=0.5, max_tokens=8192):
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": "output", "strict": True, "schema": schema}}
    }
    
    response = requests.post(BASE_URL, json=payload, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }, timeout=300)
    response.raise_for_status()
    
    data = response.json()
    return json.loads(data["choices"][0]["message"]["content"])

# Test on simple circuit we can verify: piston_door (15 blocks)
print("="*80)
print("MANUAL INSPECTION: Piston Door (15 blocks)")
print("="*80)
print("\nExpected components:")
print("  - 1 lever (power source)")
print("  - 2-3 redstone wires (signal path)")
print("  - 1-2 repeaters (signal boost)")
print("  - 2 sticky pistons (push door up)")
print("  - 2-4 door blocks (stone/wood)")
print("  - Total: ~15 blocks")

plan_schema = {
    "type": "object",
    "required": ["plan"],
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
        }
    }
}

messages = [
    {"role": "system", "content": "Plan redstone circuits step-by-step. Be precise about block types and positions."},
    {"role": "user", "content": """Plan this piston door circuit with EXACTLY 15 blocks:

Build a 1x2 piston door that opens when a lever is flipped. Use sticky pistons to push blocks up and down. The door should be 1 block wide and 2 blocks tall, with a lever to activate it.

REQUIREMENTS:
- Generate EXACTLY 15 blocks
- Number each step 1 to 15
- Use correct block types (sticky_piston, not regular piston)
- Include reason for each block
- List connections to previous blocks

Circuit needs: lever, redstone, repeaters, sticky pistons, door blocks"""},
]

print("\nGenerating plan...")
result = call_llm(messages, plan_schema, temperature=0.5)

plan = result.get("plan", [])
print(f"\nGenerated {len(plan)}/15 blocks")

# Analyze block types
print(f"\n{'='*80}")
print("BLOCK TYPE ANALYSIS")
print(f"{'='*80}")

block_types = {}
for step in plan:
    bt = step.get("block_type", "unknown").lower()
    if bt not in block_types:
        block_types[bt] = []
    block_types[bt].append(step)

print(f"\nBlock types found ({len(block_types)} unique):")
for bt, steps in sorted(block_types.items()):
    print(f"  {bt:25s}: {len(steps):2d} blocks")

# Check for required components
required = {
    "lever": False,
    "redstone": False,
    "piston": False,
    "sticky_piston": False,
    "repeater": False,
    "door": False,
    "stone": False,
    "wood": False,
}

for bt in block_types.keys():
    if "lever" in bt:
        required["lever"] = True
    if "redstone" in bt or "wire" in bt:
        required["redstone"] = True
    if "sticky_piston" in bt:
        required["sticky_piston"] = True
        required["piston"] = True
    elif "piston" in bt:
        required["piston"] = True
    if "repeater" in bt:
        required["repeater"] = True
    if "stone" in bt or "brick" in bt or "block" in bt:
        required["stone"] = True
    if "wood" in bt or "plank" in bt:
        required["wood"] = True
        required["door"] = True

print(f"\nRequired components:")
for comp, found in required.items():
    status = "✓" if found else "✗"
    print(f"  {status} {comp:20s}: {'Found' if found else 'MISSING'}")

# Detailed step-by-step inspection
print(f"\n{'='*80}")
print("STEP-BY-STEP INSPECTION")
print(f"{'='*80}")

for step in plan[:15]:  # First 15 steps
    step_num = step.get("step", "?")
    block_type = step.get("block_type", "unknown")
    position = step.get("position", "unknown")
    reason = step.get("reason", "NO REASON")
    connects = step.get("connects_to", [])
    
    # Parse position
    coords = re.findall(r'-?\d+', position)
    if len(coords) >= 2:
        x, y = int(coords[0]), int(coords[1])
        z = int(coords[2]) if len(coords) > 2 else 0
        pos_str = f"({x},{y},{z})"
    else:
        pos_str = position
    
    # Quality check
    reason_quality = "✓" if len(reason) > 30 else "⚠" if len(reason) > 10 else "✗"
    connect_str = f"←{connects}" if connects else ""
    
    print(f"\n  Step {step_num:2d}: {block_type:25s} at {pos_str} {connect_str} {reason_quality}")
    print(f"           Reason: {reason[:80]}")

# Connectivity analysis
print(f"\n{'='*80}")
print("CONNECTIVITY ANALYSIS")
print(f"{'='*80}")

connections = [s.get("connects_to", []) for s in plan]
with_connections = sum(1 for c in connections if c)
total_connections = sum(len(c) for c in connections)

print(f"\n  Blocks with connections: {with_connections}/{len(plan)} ({with_connections/len(plan)*100:.0f}%)")
print(f"  Total connection references: {total_connections}")
print(f"  Avg connections per block: {total_connections/len(plan):.2f}")

# Position analysis
print(f"\n{'='*80}")
print("POSITION ANALYSIS")
print(f"{'='*80}")

positions = []
for step in plan:
    pos_str = step.get("position", "")
    coords = re.findall(r'-?\d+', pos_str)
    if len(coords) >= 2:
        x, y = int(coords[0]), int(coords[1])
        z = int(coords[2]) if len(coords) > 2 else 0
        positions.append((x, y, z))

if positions:
    x_vals = [p[0] for p in positions]
    y_vals = [p[1] for p in positions]
    z_vals = [p[2] for p in positions]
    
    print(f"\n  X range: {min(x_vals)} to {max(x_vals)} (span: {max(x_vals)-min(x_vals)})")
    print(f"  Y range: {min(y_vals)} to {max(y_vals)} (span: {max(y_vals)-min(y_vals)})")
    print(f"  Z range: {min(z_vals)} to {max(z_vals)} (span: {max(z_vals)-min(z_vals)})")
    
    # Check for duplicates
    from collections import Counter
    pos_counts = Counter(positions)
    duplicates = [(pos, count) for pos, count in pos_counts.items() if count > 1]
    
    if duplicates:
        print(f"\n  ⚠ WARNING: {len(duplicates)} position overlaps!")
        for pos, count in duplicates[:5]:
            print(f"    {pos}: {count} blocks")
    else:
        print(f"\n  ✓ No position overlaps")

# Final verdict
print(f"\n{'='*80}")
print("VERDICT")
print(f"{'='*80}")

issues = []
strengths = []

if len(plan) == 15:
    strengths.append("✓ Correct block count (15/15)")
else:
    issues.append(f"✗ Wrong block count ({len(plan)}/15)")

if required["lever"] and required["sticky_piston"] and required["redstone"]:
    strengths.append("✓ Has all required components")
else:
    missing = [k for k, v in required.items() if not v and k in ["lever", "sticky_piston", "redstone"]]
    issues.append(f"✗ Missing components: {', '.join(missing)}")

if with_connections / len(plan) > 0.3:
    strengths.append(f"✓ Good connectivity ({with_connections}/{len(plan)} blocks reference others)")
else:
    issues.append(f"⚠ Poor connectivity ({with_connections}/{len(plan)} blocks)")

if not duplicates:
    strengths.append("✓ No position overlaps")
else:
    issues.append(f"✗ {len(duplicates)} position overlaps")

good_reasons = sum(1 for s in plan if len(s.get("reason", "")) > 20)
if good_reasons / len(plan) > 0.5:
    strengths.append(f"✓ Good reasoning ({good_reasons}/{len(plan)} blocks)")
else:
    issues.append(f"⚠ Weak reasoning ({good_reasons}/{len(plan)} blocks)")

print(f"\nStrengths:")
for s in strengths:
    print(f"  {s}")

print(f"\nIssues:")
for i in issues:
    print(f"  {i}")

if not issues:
    print(f"\n✅ VERDICT: Circuit looks CORRECT and would likely work!")
elif len(issues) <= 2:
    print(f"\n⚠️  VERDICT: Circuit has minor issues but may work")
else:
    print(f"\n❌ VERDICT: Circuit has significant issues, may not work")

# Save for inspection
with open("evaluation/results/manual_inspection_piston_door.json", "w") as f:
    json.dump(result, f, indent=2)

print(f"\n✓ Full plan saved to: evaluation/results/manual_inspection_piston_door.json")
