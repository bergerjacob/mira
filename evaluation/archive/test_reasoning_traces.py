# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

"""
MIRA: Reasoning Trace Generation Test
Tests whether LLMs can generate useful reasoning traces for fine-tuning.

Tests multiple approaches:
1. Forward construction traces (step-by-step building)
2. Reverse deconstruction traces (step-by-step removal)
3. Repair traces (fixing broken circuits)
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List

sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


# Known working circuit from test results (100% success rate)
HOPPER_TRANSPORTER = {
    "name": "Hopper Item Transporter",
    "description": "Items placed in the top chest flow through a hopper into a bottom chest.",
    "blocks": [
        {"x": 0, "y": 2, "z": 0, "state": "minecraft:chest[facing=north,type=single]"},
        {"x": 0, "y": 1, "z": 0, "state": "minecraft:hopper[facing=down]"},
        {"x": 0, "y": 0, "z": 0, "state": "minecraft:chest[facing=north,type=single]"},
    ]
}


def format_blocks(blocks: List[Dict]) -> str:
    """Format blocks as a readable list."""
    lines = []
    for b in sorted(blocks, key=lambda x: (x['y'], x['x'], x['z'])):
        lines.append(f"  ({b['x']}, {b['y']}, {b['z']}): {b['state']}")
    return "\n".join(lines)


# Schema for forward construction trace
FORWARD_TRACE_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": "Step-by-step construction process",
            "items": {
                "type": "object",
                "properties": {
                    "step_number": {"type": "integer"},
                    "action": {"type": "string", "description": "What block to place and where"},
                    "reasoning": {"type": "string", "description": "Why this block is placed here at this step"},
                    "block": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "z": {"type": "integer"},
                            "state": {"type": "string"}
                        },
                        "required": ["x", "y", "z", "state"]
                    },
                    "state_after": {"type": "string", "description": "What the circuit looks like after this step"}
                },
                "required": ["step_number", "action", "reasoning", "block"]
            }
        },
        "overall_reasoning": {
            "type": "string",
            "description": "High-level explanation of the circuit design"
        }
    },
    "required": ["steps", "overall_reasoning"],
    "additionalProperties": False
}


# Schema for reverse deconstruction trace
REVERSE_TRACE_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": "Step-by-step deconstruction process (removing blocks)",
            "items": {
                "type": "object",
                "properties": {
                    "step_number": {"type": "integer"},
                    "action": {"type": "string", "description": "What block to remove and where"},
                    "reasoning": {"type": "string", "description": "Why this block can be removed at this step"},
                    "block_removed": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "z": {"type": "integer"},
                            "state": {"type": "string"}
                        },
                        "required": ["x", "y", "z", "state"]
                    },
                    "remaining_blocks": {"type": "string", "description": "What blocks remain after this removal"}
                },
                "required": ["step_number", "action", "reasoning", "block_removed"]
            }
        },
        "overall_reasoning": {
            "type": "string",
            "description": "High-level explanation of deconstruction strategy"
        }
    },
    "required": ["steps", "overall_reasoning"],
    "additionalProperties": False
}


# Schema for repair trace (fixing a broken circuit)
REPAIR_TRACE_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {
            "type": "string",
            "description": "Analysis of what's wrong with the broken circuit"
        },
        "repair_steps": {
            "type": "array",
            "description": "Steps to fix the circuit",
            "items": {
                "type": "object",
                "properties": {
                    "step_number": {"type": "integer"},
                    "action": {"type": "string", "description": "What to change"},
                    "reasoning": {"type": "string", "description": "Why this fixes the problem"},
                    "change_type": {
                        "type": "string",
                        "enum": ["add_block", "remove_block", "modify_block"],
                        "description": "Type of change"
                    },
                    "block": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "z": {"type": "integer"},
                            "state": {"type": "string"}
                        },
                        "required": ["x", "y", "z", "state"]
                    }
                },
                "required": ["step_number", "action", "reasoning", "change_type", "block"]
            }
        }
    },
    "required": ["diagnosis", "repair_steps"],
    "additionalProperties": False
}


def test_forward_construction(model: str, client: OpenRouterClient):
    """Test: Can model generate step-by-step construction traces?"""
    
    prompt = f"""
You are analyzing a Minecraft redstone circuit for training data generation.

**Circuit:** {HOPPER_TRANSPORTER['name']}
**Description:** {HOPPER_TRANSPORTER['description']}

**Complete Block List:**
{format_blocks(HOPPER_TRANSPORTER['blocks'])}

**Task:** Generate a step-by-step CONSTRUCTION trace showing how to build this circuit from scratch.

For each step, explain:
1. Which block to place next
2. WHY it's placed there (what function it serves)
3. How it connects to previously placed blocks
4. What the circuit can do after this step

Order steps logically (e.g., bottom-up, or by dependency).
"""
    
    system_prompt = """
You are an expert Minecraft Redstone Engineer creating training data.
Generate detailed reasoning traces that explain the construction process.
Each step should have clear reasoning about WHY that block is placed there.
"""
    
    print(f"\n{'='*70}")
    print(f"TEST: Forward Construction Trace | Model: {model}")
    print(f"{'='*70}")
    
    try:
        result = client.complete_with_schema(
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            schema=FORWARD_TRACE_SCHEMA,
            temperature=0.7,  # Higher temp for creativity
        )
        
        print(f"\nOverall Reasoning:")
        print(result.get('overall_reasoning', 'N/A')[:500])
        
        print(f"\nConstruction Steps ({len(result.get('steps', []))} steps):")
        for step in result.get('steps', []):
            print(f"\n  Step {step.get('step_number')}: {step.get('action')[:60]}...")
            print(f"    Reasoning: {step.get('reasoning', '')[:100]}...")
        
        # Quality metrics
        has_reasoning = all('reasoning' in s and len(s['reasoning']) > 20 for s in result.get('steps', []))
        correct_order = len(result.get('steps', [])) == len(HOPPER_TRANSPORTER['blocks'])
        
        print(f"\n✓ Quality: Reasoning present={has_reasoning}, Correct step count={correct_order}")
        return True, result
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False, None


def test_reverse_deconstruction(model: str, client: OpenRouterClient):
    """Test: Can model generate step-by-step deconstruction traces?"""
    
    prompt = f"""
You are analyzing a Minecraft redstone circuit for training data generation.

**Circuit:** {HOPPER_TRANSPORTER['name']}
**Description:** {HOPPER_TRANSPORTER['description']}

**Complete Block List:**
{format_blocks(HOPPER_TRANSPORTER['blocks'])}

**Task:** Generate a step-by-step DECONSTRUCTION trace showing how to remove blocks in reverse order.

For each step, explain:
1. Which block to remove next
2. WHY it can be removed at this point (what dependencies does it have?)
3. What blocks remain after removal
4. What the remaining structure represents

Order steps so you're removing output/decoration first, core mechanisms last.
"""
    
    system_prompt = """
You are an expert Minecraft Redstone Engineer creating training data.
Generate detailed reasoning traces for deconstructing a circuit.
Explain the reverse-engineering logic clearly.
"""
    
    print(f"\n{'='*70}")
    print(f"TEST: Reverse Deconstruction Trace | Model: {model}")
    print(f"{'='*70}")
    
    try:
        result = client.complete_with_schema(
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            schema=REVERSE_TRACE_SCHEMA,
            temperature=0.7,
        )
        
        print(f"\nOverall Reasoning:")
        print(result.get('overall_reasoning', 'N/A')[:500])
        
        print(f"\nDeconstruction Steps ({len(result.get('steps', []))} steps):")
        for step in result.get('steps', []):
            print(f"\n  Step {step.get('step_number')}: {step.get('action')[:60]}...")
            print(f"    Reasoning: {step.get('reasoning', '')[:100]}...")
        
        # Quality metrics
        has_reasoning = all('reasoning' in s and len(s['reasoning']) > 20 for s in result.get('steps', []))
        correct_order = len(result.get('steps', [])) == len(HOPPER_TRANSPORTER['blocks'])
        
        print(f"\n✓ Quality: Reasoning present={has_reasoning}, Correct step count={correct_order}")
        return True, result
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False, None


def test_repair_trace(model: str, client: OpenRouterClient):
    """Test: Can model generate repair traces for a broken circuit?"""
    
    # Create a broken version (missing the hopper)
    broken_blocks = [
        {"x": 0, "y": 2, "z": 0, "state": "minecraft:chest[facing=north,type=single]"},
        # Hopper is MISSING!
        {"x": 0, "y": 0, "z": 0, "state": "minecraft:chest[facing=north,type=single]"},
    ]
    
    prompt = f"""
You are debugging a broken Minecraft redstone circuit.

**Circuit Name:** {HOPPER_TRANSPORTER['name']}
**Expected Function:** {HOPPER_TRANSPORTER['description']}

**Current (Broken) Block List:**
{format_blocks(broken_blocks)}

**Expected (Working) Block List:**
{format_blocks(HOPPER_TRANSPORTER['blocks'])}

**Task:** Analyze what's wrong and generate a REPAIR trace.

1. Diagnose the problem - what's missing or wrong?
2. Explain WHY this causes the circuit to fail
3. Provide step-by-step repair instructions with reasoning
"""
    
    system_prompt = """
You are an expert Minecraft Redstone Engineer debugging circuits.
Analyze broken circuits and provide clear repair instructions with reasoning.
"""
    
    print(f"\n{'='*70}")
    print(f"TEST: Repair Trace | Model: {model}")
    print(f"{'='*70}")
    
    try:
        result = client.complete_with_schema(
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            schema=REPAIR_TRACE_SCHEMA,
            temperature=0.7,
        )
        
        print(f"\nDiagnosis:")
        print(result.get('diagnosis', 'N/A')[:500])
        
        print(f"\nRepair Steps ({len(result.get('repair_steps', []))} steps):")
        for step in result.get('repair_steps', []):
            print(f"\n  Step {step.get('step_number')}: {step.get('action')[:60]}...")
            print(f"    Reasoning: {step.get('reasoning', '')[:100]}...")
        
        # Quality metrics
        identified_hopper = 'hopper' in result.get('diagnosis', '').lower()
        has_reasoning = all('reasoning' in s and len(s['reasoning']) > 20 for s in result.get('repair_steps', []))
        
        print(f"\n✓ Quality: Identified missing hopper={identified_hopper}, Reasoning present={has_reasoning}")
        return True, result
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False, None


def main():
    client = OpenRouterClient(OPENROUTER_API_KEY)
    
    # Test models
    models = ["kimi-k2.5", "gemini-flash-lite"]
    
    print("\n" + "="*70)
    print("MIRA REASONING TRACE GENERATION TEST")
    print("="*70)
    print("\nTesting whether LLMs can generate useful training data traces.")
    print("Circuit: Hopper Transporter (3 blocks, known working)")
    
    results = {}
    
    for model in models:
        print(f"\n{'#'*70}")
        print(f"# MODEL: {model}")
        print(f"{'#'*70}")
        
        # Test all three trace types
        success_fwd, result_fwd = test_forward_construction(model, client)
        success_rev, result_rev = test_reverse_deconstruction(model, client)
        success_rep, result_rep = test_repair_trace(model, client)
        
        results[model] = {
            "forward": success_fwd,
            "reverse": success_rev,
            "repair": success_rep,
        }
        
        # Save results
        output_file = Path("evaluation/results/reasoning_trace_test.json")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        import time
        time.sleep(1)
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    for model, res in results.items():
        total = sum(res.values())
        print(f"\n{model}: {total}/3 trace types successful")
        print(f"  Forward construction: {'✓' if res['forward'] else '✗'}")
        print(f"  Reverse deconstruction: {'✓' if res['reverse'] else '✗'}")
        print(f"  Repair traces: {'✓' if res['repair'] else '✗'}")
    
    print("\n" + "="*70)
    print("CONCLUSION")
    print("="*70)
    print("""
If models can generate quality reasoning traces:
  → Dataset generation is FEASIBLE with current LLMs
  → We can scrape schematics and auto-generate training traces
  → Fine-tuning becomes practical (no manual trace creation needed)

If models struggle with traces:
  → Need to simplify trace format
  → Or use teacher model (GPT-4/Claude) for trace generation
  → May need human-in-the-loop for quality control
""")


if __name__ == "__main__":
    main()
