# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

"""
MIRA: Reasoning Trace Quality Analysis
Deep dive into the QUALITY of reasoning traces, not just validity.

Tests whether reasoning is:
1. Context-aware (references other blocks in circuit)
2. Function-driven (explains WHY based on circuit behavior)
3. Dependency-aware (understands block relationships)
4. Not just descriptive ("place block here") but explanatory ("place here BECAUSE...")
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List

sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# More complex circuit for better reasoning evaluation
PISTON_DOOR = {
    "name": "2x2 Piston Door",
    "description": "A lever-activated piston door that opens a 2x2 passage. When the lever is flipped, four sticky pistons extend upward, pushing four stone blocks up to open the doorway.",
    "blocks": [
        # Lever and control
        {"x": -2, "y": 0, "z": 0, "state": "minecraft:lever[face=floor,facing=east,powered=false]"},
        {"x": -1, "y": 0, "z": 0, "state": "minecraft:redstone_wire"},
        
        # Redstone line to pistons
        {"x": 0, "y": 0, "z": 0, "state": "minecraft:redstone_wire"},
        {"x": 1, "y": 0, "z": 0, "state": "minecraft:redstone_wire"},
        
        # Four sticky pistons (2x2 arrangement)
        {"x": 2, "y": 0, "z": 0, "state": "minecraft:sticky_piston[facing=up,extended=false]"},
        {"x": 3, "y": 0, "z": 0, "state": "minecraft:sticky_piston[facing=up,extended=false]"},
        {"x": 2, "y": 0, "z": 1, "state": "minecraft:sticky_piston[facing=up,extended=false]"},
        {"x": 3, "y": 0, "z": 1, "state": "minecraft:sticky_piston[facing=up,extended=false]"},
        
        # Four door blocks (2x2 arrangement above pistons)
        {"x": 2, "y": 1, "z": 0, "state": "minecraft:stone"},
        {"x": 3, "y": 1, "z": 0, "state": "minecraft:stone"},
        {"x": 2, "y": 1, "z": 1, "state": "minecraft:stone"},
        {"x": 3, "y": 1, "z": 1, "state": "minecraft:stone"},
    ]
}

SIMPLE_REDSTONE_LINE = {
    "name": "Redstone Line with Corner",
    "description": "A redstone line that goes from a lever, makes a 90-degree corner, and powers a lamp at the end.",
    "blocks": [
        {"x": 0, "y": 0, "z": 0, "state": "minecraft:lever[face=floor,facing=east,powered=false]"},
        {"x": 1, "y": 0, "z": 0, "state": "minecraft:redstone_wire"},
        {"x": 2, "y": 0, "z": 0, "state": "minecraft:redstone_wire"},
        {"x": 3, "y": 0, "z": 0, "state": "minecraft:redstone_wire"},
        {"x": 3, "y": 0, "z": 1, "state": "minecraft:redstone_wire"},
        {"x": 3, "y": 0, "z": 2, "state": "minecraft:redstone_wire"},
        {"x": 3, "y": 0, "z": 3, "state": "minecraft:redstone_lamp[lit=false]"},
    ]
}


def format_blocks(blocks: List[Dict]) -> str:
    """Format blocks as a readable list."""
    lines = []
    for b in sorted(blocks, key=lambda x: (x['y'], x['x'], x['z'])):
        lines.append(f"  ({b['x']}, {b['y']}, {b['z']}): {b['state']}")
    return "\n".join(lines)


# Schema for detailed reasoning trace
DETAILED_REASONING_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_plan": {
            "type": "string",
            "description": "High-level plan: What are the main components and how do they connect? What is the signal flow?"
        },
        "construction_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "block_to_place": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "z": {"type": "integer"},
                            "state": {"type": "string"}
                        },
                        "required": ["x", "y", "z", "state"]
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "WHY this block here? Reference other blocks, signal flow, dependencies. NOT just 'place block' but 'place block BECAUSE it connects A to B'"
                    },
                    "connects_to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Coordinates of blocks this connects to (e.g., ['(0,0,0)', '(2,0,0)'])"
                    },
                    "function": {
                        "type": "string",
                        "description": "What role does this block serve? (e.g., 'power source', 'signal transmission', 'actuator')"
                    }
                },
                "required": ["step", "block_to_place", "reasoning", "function"]
            }
        }
    },
    "required": ["overall_plan", "construction_steps"],
    "additionalProperties": False
}


def test_reasoning_quality(circuit: Dict, prompt_variant: str, client: OpenRouterClient):
    """Test reasoning quality with different prompt approaches."""
    
    prompt = f"""
Analyze this Minecraft redstone circuit and explain the construction process.

**Circuit:** {circuit['name']}
**Description:** {circuit['description']}

**Complete Block List:**
{format_blocks(circuit['blocks'])}

{prompt_variant}

CRITICAL: Your reasoning must be CONTEXTUAL and FUNCTIONAL:
- BAD: "Place redstone wire at (1,0,0)"
- GOOD: "Place redstone wire at (1,0,0) to CONNECT the lever at (0,0,0) to the next segment, extending the signal path toward the lamp"
- BAD: "Place piston here"
- GOOD: "Place sticky piston at (2,0,0) facing UP because it needs to PUSH the door block at (2,1,0) upward when powered by the redstone line"

Reference OTHER blocks in your reasoning. Explain SIGNAL FLOW. Explain DEPENDENCIES.
"""
    
    system_prompt = """
You are an expert Minecraft Redstone Engineer creating HIGH-QUALITY training data.

Your reasoning must demonstrate:
1. SPATIAL AWARENESS: Reference positions relative to other blocks
2. SIGNAL FLOW: Explain how redstone power travels through the circuit
3. FUNCTIONAL PURPOSE: Explain WHY each block is needed for the circuit to work
4. DEPENDENCIES: Explain which blocks must be placed before others

DO NOT just describe WHAT to place. Explain WHY it goes there and HOW it connects to the rest of the circuit.
"""
    
    try:
        result = client.complete_with_schema(
            model="gemini-flash-lite",
            prompt=prompt,
            system_prompt=system_prompt,
            schema=DETAILED_REASONING_SCHEMA,
            temperature=0.7,
        )
        
        return True, result
        
    except Exception as e:
        print(f"Error: {e}")
        return False, None


def analyze_reasoning_quality(result: Dict, circuit_name: str):
    """Analyze the quality of reasoning in the result."""
    
    print(f"\n{'='*70}")
    print(f"QUALITY ANALYSIS: {circuit_name}")
    print(f"{'='*70}")
    
    print(f"\nOverall Plan:")
    print(result.get('overall_plan', 'N/A')[:500])
    
    steps = result.get('construction_steps', [])
    print(f"\n\nStep-by-Step Analysis ({len(steps)} steps):")
    
    quality_scores = {
        "references_other_blocks": 0,
        "explains_signal_flow": 0,
        "explains_function": 0,
        "mentions_dependencies": 0,
        "contextual_reasoning": 0,
    }
    
    for step in steps:
        step_num = step.get('step', '?')
        block = step.get('block_to_place', {})
        reasoning = step.get('reasoning', '')
        function = step.get('function', '')
        connects_to = step.get('connects_to', [])
        
        print(f"\n  Step {step_num}: {block.get('state', 'unknown')[:40]} at ({block.get('x')}, {block.get('y')}, {block.get('z')})")
        print(f"    Function: {function}")
        print(f"    Reasoning: {reasoning[:150]}...")
        
        if connects_to:
            print(f"    Connects to: {connects_to}")
        
        # Quality analysis
        reasoning_lower = reasoning.lower()
        
        # Check if it references other blocks (coordinates or relative positions)
        if any(word in reasoning_lower for word in ['connect', 'connects', 'from', 'to', 'between', 'adjacent', 'next to', 'above', 'below', 'at (']):
            quality_scores["references_other_blocks"] += 1
        
        # Check if it explains signal/power
        if any(word in reasoning_lower for word in ['signal', 'power', 'powered', 'electricity', 'flow', 'transmit']):
            quality_scores["explains_signal_flow"] += 1
        
        # Check if it explains function/purpose
        if any(word in reasoning_lower for word in ['because', 'so that', 'to', 'for', 'purpose', 'function', 'role']):
            quality_scores["explains_function"] += 1
        
        # Check if it mentions dependencies
        if any(word in reasoning_lower for word in ['before', 'after', 'depends', 'requires', 'needs', 'must', 'first', 'then']):
            quality_scores["mentions_dependencies"] += 1
        
        # Overall contextual reasoning (references specific coordinates or block relationships)
        if '(' in reasoning and ')' in reasoning and ',' in reasoning:
            quality_scores["contextual_reasoning"] += 1
    
    # Calculate percentages
    total = len(steps) if steps else 1
    print(f"\n\n{'='*70}")
    print("QUALITY METRICS:")
    print(f"{'='*70}")
    
    metrics = [
        ("References other blocks", quality_scores["references_other_blocks"]),
        ("Explains signal flow", quality_scores["explains_signal_flow"]),
        ("Explains function/purpose", quality_scores["explains_function"]),
        ("Mentions dependencies", quality_scores["mentions_dependencies"]),
        ("Contextual (coords/relationships)", quality_scores["contextual_reasoning"]),
    ]
    
    for metric, count in metrics:
        pct = 100 * count / total
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        print(f"  {metric:35s} {count:2d}/{total:2d} ({pct:5.1f}%) {bar}")
    
    # Overall quality score
    total_score = sum(quality_scores.values())
    max_score = total * 5
    overall_pct = 100 * total_score / max_score if max_score > 0 else 0
    
    print(f"\n  OVERALL QUALITY SCORE: {overall_pct:5.1f}%")
    
    if overall_pct >= 80:
        print("  ✅ EXCELLENT - Ready for training data")
    elif overall_pct >= 60:
        print("  ⚠️  GOOD - May need minor improvements")
    else:
        print("  ❌ POOR - Needs significant prompt improvement")
    
    return overall_pct, result


def main():
    client = OpenRouterClient(OPENROUTER_API_KEY)
    
    print("\n" + "="*70)
    print("MIRA REASONING TRACE QUALITY ANALYSIS")
    print("="*70)
    print("\nTesting whether reasoning is CONTEXTUAL and FUNCTIONAL,")
    print("not just descriptive.")
    
    # Test different prompt variants
    prompt_variants = [
        "Explain the construction step-by-step, focusing on WHY each block is placed where it is.",
        "Explain the construction like you're teaching a student. Reference how blocks connect and how signals flow.",
        "Explain the construction emphasizing SPATIAL RELATIONSHIPS and SIGNAL PATHS. For each block, say what it connects to.",
    ]
    
    circuits = [
        (SIMPLE_REDSTONE_LINE, "Redstone Line (Corner)"),
        (PISTON_DOOR, "Piston Door (2x2)"),
    ]
    
    all_results = {}
    
    for circuit, circuit_name in circuits:
        print(f"\n\n{'#'*70}")
        print(f"# CIRCUIT: {circuit_name}")
        print(f"{'#'*70}")
        
        best_score = 0
        best_variant = ""
        best_result = None
        
        for i, variant in enumerate(prompt_variants, 1):
            print(f"\n\n[Prompt Variant {i}/{len(prompt_variants)}]")
            print(f"-"*70)
            
            success, result = test_reasoning_quality(circuit, variant, client)
            
            if success:
                score, result = analyze_reasoning_quality(result, circuit_name)
                
                if score > best_score:
                    best_score = score
                    best_variant = variant
                    best_result = result
                
                # Save result
                output_dir = Path("evaluation/results/quality_analysis")
                output_dir.mkdir(parents=True, exist_ok=True)
                
                output_file = output_dir / f"{circuit_name.replace(' ', '_')}_variant_{i}.json"
                with open(output_file, 'w') as f:
                    json.dump({
                        "circuit": circuit_name,
                        "prompt_variant": i,
                        "quality_score": score,
                        "result": result,
                    }, f, indent=2)
            
            import time
            time.sleep(1)
        
        all_results[circuit_name] = {
            "best_score": best_score,
            "best_variant": best_variant,
            "best_result": best_result,
        }
    
    # Final summary
    print("\n\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)
    
    for circuit_name, data in all_results.items():
        print(f"\n{circuit_name}:")
        print(f"  Best Quality Score: {data['best_score']:.1f}%")
        print(f"  Best Prompt Variant: {data['best_variant'][:80]}...")
    
    # Save summary
    summary_file = Path("evaluation/results/quality_summary.json")
    with open(summary_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    print("\n\n" + "="*70)
    print("CONCLUSION")
    print("="*70)
    print("""
If quality scores are HIGH (≥80%):
  ✅ Current prompts are good
  ✅ Can proceed with large-scale trace generation
  ✅ Reasoning will add value to fine-tuning

If quality scores are MEDIUM (60-80%):
  ⚠️  Iterate on prompts
  ⚠️  Try few-shot examples
  ⚠️  May still be usable with some filtering

If quality scores are LOW (<60%):
  ❌ Need major prompt redesign
  ❌ May need Chain-of-Thought prompting
  ❌ Consider using more capable model (Claude/GPT-4)
""")


if __name__ == "__main__":
    main()
