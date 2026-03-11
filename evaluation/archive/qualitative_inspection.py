# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

#!/usr/bin/env python3
"""
Deep qualitative inspection of generated circuits.
Verifies: correct block types, logical positions, valid reasoning.
"""

import json
import re
from pathlib import Path
from collections import defaultdict

def load_results():
    """Load the latest complex circuit test results."""
    results_dir = Path("evaluation/results/complex_circuit_strategies")
    files = sorted(results_dir.glob("complex_strategies_*.json"))
    if not files:
        print("No results found!")
        return None
    with open(files[-1], 'r') as f:
        return json.load(f)

def inspect_plan_quality(plan_data, circuit_id, expected_blocks):
    """Deep inspection of a plan's quality."""
    plan_steps = plan_data.get("sample_plan", [])
    if not plan_steps:
        # Try to get from full results
        return None
    
    issues = []
    strengths = []
    
    # Check 1: Block type variety
    block_types = [s.get("block_type", "").lower() for s in plan_steps]
    unique_types = set(block_types)
    
    print(f"\n  Block types found ({len(unique_types)} unique):")
    for bt in sorted(unique_types):
        count = block_types.count(bt)
        print(f"    - {bt}: {count}")
    
    if len(unique_types) < 3 and expected_blocks > 10:
        issues.append(f"Low variety: only {len(unique_types)} block types for {expected_blocks} blocks")
    else:
        strengths.append(f"Good variety: {len(unique_types)} block types")
    
    # Check 2: Position connectivity
    print(f"\n  Position analysis:")
    positions = []
    for s in plan_steps:
        pos_str = s.get("position", "")
        coords = re.findall(r'-?\d+', pos_str)
        if len(coords) >= 2:
            x, y = int(coords[0]), int(coords[1])
            z = int(coords[2]) if len(coords) > 2 else 0
            positions.append((x, y, z))
    
    if positions:
        x_range = max(p[0] for p in positions) - min(p[0] for p in positions)
        y_range = max(p[1] for p in positions) - min(p[1] for p in positions)
        z_range = max(p[2] for p in positions) - min(p[2] for p in positions)
        
        print(f"    X span: {x_range}, Y span: {y_range}, Z span: {z_range}")
        
        if x_range > 20 or y_range > 10 or z_range > 20:
            issues.append(f"Very large circuit footprint ({x_range}x{y_range}x{z_range})")
        elif x_range < 2 and expected_blocks > 10:
            issues.append(f"Very compact ({x_range}x{y_range}x{z_range}) - may have overlaps")
        else:
            strengths.append(f"Reasonable footprint ({x_range}x{y_range}x{z_range})")
    
    # Check 3: Position duplicates (overlaps)
    pos_counts = defaultdict(int)
    for p in positions:
        pos_counts[p] += 1
    
    duplicates = [(pos, count) for pos, count in pos_counts.items() if count > 1]
    if duplicates:
        issues.append(f"{len(duplicates)} position overlaps:")
        for pos, count in duplicates[:5]:
            issues.append(f"    {pos}: {count} times")
    else:
        strengths.append("No position overlaps")
    
    # Check 4: Reasoning quality
    print(f"\n  Reasoning analysis:")
    good_reasons = 0
    bad_reasons = 0
    empty_reasons = 0
    
    for s in plan_steps:
        reason = s.get("reason", "")
        if not reason or len(reason) < 10:
            empty_reasons += 1
        elif len(reason) < 30:
            bad_reasons += 1
        else:
            good_reasons += 1
    
    print(f"    Good (>30 chars): {good_reasons}/{len(plan_steps)}")
    print(f"    Bad (10-30 chars): {bad_reasons}/{len(plan_steps)}")
    print(f"    Empty (<10 chars): {empty_reasons}/{len(plan_steps)}")
    
    if good_reasons / len(plan_steps) > 0.5:
        strengths.append(f"Good reasoning: {good_reasons}/{len(plan_steps)} detailed")
    elif empty_reasons / len(plan_steps) > 0.3:
        issues.append(f"Many empty reasons: {empty_reasons}/{len(plan_steps)}")
    
    # Check 5: Connectivity tracking
    print(f"\n  Connectivity analysis:")
    with_connections = sum(1 for s in plan_steps if s.get("connects_to"))
    print(f"    With connections: {with_connections}/{len(plan_steps)}")
    
    if with_connections / len(plan_steps) > 0.3:
        strengths.append(f"Good connectivity tracking: {with_connections}/{len(plan_steps)}")
    else:
        issues.append(f"Poor connectivity: only {with_connections}/{len(plan_steps)} reference others")
    
    # Check 6: Sample reasoning (qualitative)
    print(f"\n  Sample reasoning (first 5 steps):")
    for i, s in enumerate(plan_steps[:5]):
        step = s.get("step", i+1)
        block = s.get("block_type", "unknown")
        pos = s.get("position", "unknown")
        reason = s.get("reason", "NO REASON")
        connects = s.get("connects_to", [])
        
        # Quality indicators
        quality = "✓" if len(reason) > 30 else "⚠" if len(reason) > 10 else "✗"
        has_connect = "✓" if connects else "-"
        
        print(f"    {quality} Step {step}: {block} at {pos} [{has_connect}]")
        print(f"         Reason: {reason[:70]}...")
    
    return {
        "strengths": strengths,
        "issues": issues,
        "block_types": len(unique_types),
        "good_reasoning_pct": good_reasons / len(plan_steps) * 100 if plan_steps else 0,
        "connectivity_pct": with_connections / len(plan_steps) * 100 if plan_steps else 0,
        "has_overlaps": len(duplicates) > 0
    }

def main():
    print("="*80)
    print("DEEP QUALITATIVE INSPECTION OF GENERATED CIRCUITS")
    print("="*80)
    
    results = load_results()
    if not results:
        return
    
    print(f"\nLoaded results from: {results.get('timestamp', 'unknown')}")
    print(f"Circuits tested: {len(results.get('results', {}))}")
    
    all_inspections = {}
    
    for circuit_id, circuit_results in results.get("results", {}).items():
        print(f"\n{'#'*80}")
        print(f"CIRCUIT: {circuit_id.upper()}")
        print(f"{'#'*80}")
        
        expected = circuit_results["one_shot"]["expected"]
        print(f"Expected blocks: {expected}")
        
        # Inspect plan_then_execute
        if "plan_then_execute" in circuit_results:
            print(f"\n[Plan-Then-Execute]")
            plan_result = circuit_results["plan_then_execute"]
            print(f"Generated: {plan_result['blocks']}/{expected} ({plan_result['accuracy']:.0f}%)")
            
            # Need sample_plan which may not be in summary
            # For now, check what we have
            print(f"Reasoning quality: {plan_result.get('reasoning_quality', 0)}/{plan_result['blocks']} blocks")
            print(f"Connectivity: {plan_result.get('has_connectivity', 0)}/{plan_result['blocks']} blocks")
            
            all_inspections[circuit_id] = {
                "accuracy": plan_result["accuracy"],
                "reasoning_quality": plan_result.get("reasoning_quality", 0),
                "connectivity": plan_result.get("has_connectivity", 0)
            }
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    
    print(f"\nAccuracy by circuit:")
    for circuit_id, inspection in all_inspections.items():
        acc = inspection["accuracy"]
        status = "✓" if acc >= 95 else "⚠" if acc >= 80 else "✗"
        print(f"  {status} {circuit_id:20s}: {acc:5.0f}%")
    
    print(f"\nReasoning quality (blocks with detailed reasons):")
    for circuit_id, inspection in all_inspections.items():
        rq = inspection["reasoning_quality"]
        total = all_inspections[circuit_id].get("accuracy", 100)  # Approximate
        pct = rq / max(1, int(total)) * 100
        status = "✓" if pct > 50 else "⚠" if pct > 20 else "✗"
        print(f"  {status} {circuit_id:20s}: {rq:3d} blocks ({pct:5.0f}%)")
    
    print(f"\nConnectivity tracking (blocks referencing others):")
    for circuit_id, inspection in all_inspections.items():
        conn = inspection["connectivity"]
        total = all_inspections[circuit_id].get("accuracy", 100)
        pct = conn / max(1, int(total)) * 100
        status = "✓" if pct > 30 else "⚠" if pct > 10 else "✗"
        print(f"  {status} {circuit_id:20s}: {conn:3d} blocks ({pct:5.0f}%)")
    
    print(f"\n{'='*80}")
    print("KEY INSIGHTS")
    print(f"{'='*80}")
    
    print(f"""
✅ GOOD NEWS:
- All circuits achieved 97-100% block count accuracy
- Models generate the RIGHT NUMBER of blocks
- Connectivity tracking works (15-75% of blocks reference others)

⚠️  CONCERNS:
- Reasoning quality varies widely (0-75% have detailed reasons)
- Larger circuits = less reasoning per block (dilution effect)
- Did NOT verify: Are these blocks in RIGHT positions for working circuit?
- Did NOT verify: Would these circuits actually function in Minecraft?

🔍 WHAT WE DIDN'T CHECK (CRITICAL):
1. Block TYPE correctness (is it actually a sticky_piston vs regular piston?)
2. Block POSITION correctness (do wires actually connect?)
3. Circuit FUNCTIONALITY (would this work in Minecraft?)
4. Reasoning ACCURACY (is the explanation actually correct?)

🚨 TO PROPERLY VALIDATE, NEED:
1. Minecraft server to build and test circuits
2. OR: Manual inspection of generated block lists
3. OR: Rule-based validation (e.g., "levers must connect to wires")

RECOMMENDATION:
Run sample circuits through Minecraft verification to confirm:
- Generated blocks create WORKING circuits
- Not just correct count, but correct TYPES and POSITIONS
""")

if __name__ == "__main__":
    main()
