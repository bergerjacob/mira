# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

"""
MIRA: Generation Strategy Comparison

Tests three different approaches to circuit generation:

1. ONE-SHOT FULL TRACE (Current)
   - Input: Complete block list of finished circuit
   - Output: Full step-by-step reasoning trace
   - Use case: Dataset generation (teacher model creates training data)

2. ITERATIVE STEP-BY-STEP
   - Input: Current state (blocks placed so far) + goal
   - Output: NEXT block to place + reasoning
   - Use case: Could be used for both dataset gen AND MIRA inference

3. MIRA INFERENCE (Target)
   - Input: Text description ONLY (no block list)
   - Output: Generate circuit from scratch, test, iterate
   - Use case: Actual MIRA building circuits

This test validates whether our training data generation strategy
matches what we expect MIRA to do at inference time.
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List

sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Test circuit
TARGET_CIRCUIT = {
    "name": "Simple Lever to Lamp",
    "description": "Build a redstone circuit where a lever powers a redstone lamp through redstone wire.",
    "blocks": [
        {"x": 0, "y": 0, "z": 0, "state": "minecraft:lever[face=floor,facing=east,powered=false]"},
        {"x": 1, "y": 0, "z": 0, "state": "minecraft:redstone_wire"},
        {"x": 2, "y": 0, "z": 0, "state": "minecraft:redstone_lamp[lit=false]"},
    ]
}


def format_blocks(blocks: List[Dict]) -> str:
    """Format blocks as readable list."""
    if not blocks:
        return "  (No blocks placed yet)"
    lines = []
    for b in sorted(blocks, key=lambda x: (x['y'], x['x'], x['z'])):
        lines.append(f"  ({b['x']}, {b['y']}, {b['z']}): {b['state']}")
    return "\n".join(lines)


# Schema for one-shot full trace
FULL_TRACE_SCHEMA = {
    "type": "object",
    "properties": {
        "all_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
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
                    "reasoning": {"type": "string"}
                },
                "required": ["step", "block", "reasoning"]
            }
        }
    },
    "required": ["all_steps"],
    "additionalProperties": False
}


# Schema for iterative next-step
NEXT_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "next_block": {
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
            "description": "Why this block next? What does it connect to? What progress does it make?"
        },
        "progress": {
            "type": "string",
            "description": "What percentage complete? What's left to build?"
        },
        "is_complete": {
            "type": "boolean",
            "description": "Is the circuit complete after this step?"
        }
    },
    "required": ["next_block", "reasoning", "progress", "is_complete"],
    "additionalProperties": False
}


# Schema for MIRA inference (generate from description)
GENERATE_FROM_DESC_SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {
            "type": "string",
            "description": "High-level plan for building this circuit"
        },
        "first_block": {
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
            "description": "Why start with this block? What's the overall strategy?"
        }
    },
    "required": ["plan", "first_block", "reasoning"],
    "additionalProperties": False
}


def test_one_shot_full_trace(client: OpenRouterClient):
    """
    Strategy 1: One-shot full trace generation
    
    Given: Complete circuit (all blocks)
    Output: Full step-by-step trace
    
    This is what we're currently doing for dataset generation.
    """
    print("\n" + "="*70)
    print("STRATEGY 1: ONE-SHOT FULL TRACE")
    print("="*70)
    print("\nInput: COMPLETE circuit (all blocks known)")
    print("Output: Full step-by-step reasoning trace")
    print("\nUse case: Dataset generation (teacher creates training data)")
    
    prompt = f"""
You are analyzing a completed Minecraft redstone circuit to create training data.

**Circuit:** {TARGET_CIRCUIT['name']}
**Description:** {TARGET_CIRCUIT['description']}

**Complete Block List (Final State):**
{format_blocks(TARGET_CIRCUIT['blocks'])}

**Task:** Generate a COMPLETE step-by-step construction trace showing how to build this from scratch.

Output ALL steps in order, with reasoning for each.
"""
    
    system_prompt = """
You are an expert Minecraft Redstone Engineer creating training data.
Analyze the completed circuit and explain the optimal construction order.
"""
    
    try:
        result = client.complete_with_schema(
            model="gemini-flash-lite",
            prompt=prompt,
            system_prompt=system_prompt,
            schema=FULL_TRACE_SCHEMA,
            temperature=0.7,
        )
        
        steps = result.get('all_steps', [])
        print(f"\n✓ Generated {len(steps)} steps in one shot")
        
        for step in steps:
            b = step['block']
            print(f"  Step {step['step']}: {b['state'][:40]} at ({b['x']},{b['y']},{b['z']})")
            print(f"    Reasoning: {step['reasoning'][:80]}...")
        
        return True, result
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False, None


def test_iterative_step_by_step(client: OpenRouterClient):
    """
    Strategy 2: Iterative step-by-step generation
    
    Given: Current state + goal
    Output: NEXT block only
    
    This could be used for both dataset gen AND inference.
    """
    print("\n" + "="*70)
    print("STRATEGY 2: ITERATIVE STEP-BY-STEP")
    print("="*70)
    print("\nInput: Current state (blocks placed so far) + goal")
    print("Output: NEXT block to place")
    print("\nUse case: Both dataset gen AND MIRA inference")
    
    # Simulate iterative generation
    placed_blocks = []
    all_steps = []
    max_steps = 10
    
    for step_num in range(max_steps):
        prompt = f"""
You are building a Minecraft redstone circuit step-by-step.

**Goal:** {TARGET_CIRCUIT['description']}

**Current State (Blocks Already Placed):**
{format_blocks(placed_blocks)}

**Task:** Decide the NEXT single block to place.

Consider:
- What's the logical next step?
- What needs to be in place first?
- How does this block connect to existing ones?
"""
        
        system_prompt = """
You are building a redstone circuit iteratively.
Decide the NEXT block to place, with reasoning.
Stop when the circuit is complete.
"""
        
        try:
            result = client.complete_with_schema(
                model="gemini-flash-lite",
                prompt=prompt,
                system_prompt=system_prompt,
                schema=NEXT_STEP_SCHEMA,
                temperature=0.7,
            )
            
            next_block = result['next_block']
            reasoning = result['reasoning']
            progress = result['progress']
            is_complete = result['is_complete']
            
            print(f"\nStep {step_num + 1}:")
            print(f"  Place: {next_block['state'][:40]} at ({next_block['x']},{next_block['y']},{next_block['z']})")
            print(f"  Reasoning: {reasoning[:80]}...")
            print(f"  Progress: {progress[:60]}...")
            
            placed_blocks.append(next_block)
            all_steps.append({
                "step": step_num + 1,
                "block": next_block,
                "reasoning": reasoning,
            })
            
            if is_complete:
                print(f"\n✓ Circuit complete after {step_num + 1} steps")
                break
        
        except Exception as e:
            print(f"✗ Error at step {step_num + 1}: {e}")
            break
    
    return True, {"iterative_steps": all_steps, "total_steps": len(all_steps)}


def test_mira_inference_from_description(client: OpenRouterClient):
    """
    Strategy 3: MIRA inference (generate from description only)
    
    Given: Text description ONLY (no block list)
    Output: Plan + first block
    
    This is what MIRA actually needs to do at inference time.
    """
    print("\n" + "="*70)
    print("STRATEGY 3: MIRA INFERENCE (From Description)")
    print("="*70)
    print("\nInput: Text description ONLY (no block list)")
    print("Output: Plan + first block to place")
    print("\nUse case: Actual MIRA building circuits from scratch")
    
    prompt = f"""
You need to build a Minecraft redstone circuit from this description.

**Task:** {TARGET_CIRCUIT['description']}

You don't know the exact block list. You need to:
1. Plan the circuit design
2. Decide the first block to place
3. (Later steps will be generated iteratively)

What's your plan, and what's the first block?
"""
    
    system_prompt = """
You are MIRA, an AI redstone engineer.
You receive text descriptions and must build circuits from scratch.
Create a plan and start building.
"""
    
    try:
        result = client.complete_with_schema(
            model="gemini-flash-lite",
            prompt=prompt,
            system_prompt=system_prompt,
            schema=GENERATE_FROM_DESC_SCHEMA,
            temperature=0.7,
        )
        
        plan = result['plan']
        first_block = result['first_block']
        reasoning = result['reasoning']
        
        print(f"\nPlan:")
        print(f"  {plan[:200]}...")
        
        print(f"\nFirst Block:")
        print(f"  {first_block['state'][:40]} at ({first_block['x']},{first_block['y']},{first_block['z']})")
        
        print(f"\nReasoning:")
        print(f"  {reasoning[:150]}...")
        
        # Check if first block matches target
        target_first = TARGET_CIRCUIT['blocks'][0]
        matches = (first_block['x'] == target_first['x'] and 
                   first_block['y'] == target_first['y'] and 
                   first_block['z'] == target_first['z'] and
                   first_block['state'] == target_first['state'])
        
        print(f"\n{'✓' if matches else '⚠'} First block matches target: {matches}")
        
        return True, result
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False, None


def compare_strategies():
    """Compare all three strategies and provide analysis."""
    
    print("\n" + "="*70)
    print("STRATEGY COMPARISON & ANALYSIS")
    print("="*70)
    
    print("""
CURRENT APPROACH (One-Shot Full Trace):
---------------------------------------
✅ Good for: Dataset generation when you HAVE the complete circuit
✅ Pros: 
   - Generates full trace in one API call (fast, cheap)
   - Sees entire circuit, can optimize order
   - Good for creating training data from scraped schematics

❌ Bad for: Matching MIRA's actual inference behavior
❌ Cons:
   - MIRA won't have complete block list at inference time
   - MIRA needs to GENERATE the circuit, not EXPLAIN it
   - Training on "explain this" ≠ "generate from scratch"


ITERATIVE APPROACH (Step-by-Step):
----------------------------------
✅ Good for: Both dataset gen AND matching inference behavior
✅ Pros:
   - Matches how MIRA will actually build (one block at a time)
   - Can incorporate test feedback between steps
   - More realistic training data

❌ Cons:
   - More API calls (slower, more expensive)
   - Can get stuck in loops
   - Harder to ensure completeness


MIRA INFERENCE (From Description):
----------------------------------
✅ This is the TARGET behavior
✅ Pros:
   - Exactly what MIRA needs to do
   - Tests actual generation capability
   - Most realistic evaluation

❌ Cons:
   - Hardest task (no ground truth to reference)
   - May generate incorrect circuits
   - Needs iterative testing/repair loop


RECOMMENDATION:
--------------

For DATASET GENERATION:
  → Use ITERATIVE approach (Strategy 2)
  → Even though we have complete circuits, generate traces iteratively
  → This creates training data that matches inference behavior
  → Cost: ~3x more API calls, but worth it for better alignment

For MIRA TRAINING:
  → Train on iterative traces (not one-shot)
  → MIRA learns to: "Given current state, what's next?"
  → At inference: MIRA starts empty, iteratively builds, tests, repairs

For MIRA INFERENCE:
  → Start with Strategy 3 (description → first block)
  → Then iterate: current state → next block
  → After each block (or group), run verification tests
  → If test fails, generate repair steps
  → Loop until all tests pass

KEY INSIGHT:
-----------
The teacher model (Gemini) should generate training data the SAME WAY
MIRA will use it at inference time. This is called "training-serving skew"
and avoiding it is critical for good performance.

Current one-shot approach has training-serving skew:
  Training: "Here's complete circuit, explain it"
  Inference: "Here's description, build it from scratch"

Iterative approach eliminates skew:
  Training: "Here's current state, what's next?"
  Inference: "Here's current state, what's next?"
""")


def main():
    client = OpenRouterClient(OPENROUTER_API_KEY)
    
    print("\n" + "="*70)
    print("MIRA GENERATION STRATEGY COMPARISON")
    print("="*70)
    print("\nTesting whether our dataset generation strategy matches")
    print("what MIRA will actually do at inference time.")
    
    # Test all three strategies
    test_one_shot_full_trace(client)
    
    import time
    time.sleep(1)
    
    test_iterative_step_by_step(client)
    
    time.sleep(1)
    
    test_mira_inference_from_description(client)
    
    # Compare and analyze
    compare_strategies()
    
    print("\n" + "="*70)
    print("NEXT STEPS")
    print("="*70)
    print("""
1. ✅ Switch dataset generation to ITERATIVE approach
   - Modify test_reasoning_traces.py to generate step-by-step
   - More expensive but better alignment

2. ✅ Create iterative training format
   - Each training example: (current_state, description) → (next_block, reasoning)
   - NOT: (complete_circuit) → (full_trace)

3. ✅ Implement MIRA inference loop
   - Start: description only
   - Loop: generate next block, place it, test
   - If test fails: generate repair
   - End: all tests pass

4. ⏳ Later: Add iterative repair capability
   - When verification fails, generate fix steps
   - This is where fine-tuned MIRA shines
""")


if __name__ == "__main__":
    main()
