# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

#!/usr/bin/env python3
"""
Iterative generation test - generates circuits step by step.
Compares quality and cost with one-shot generation.
"""

import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

API_KEY = "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"
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

MODEL = "google/gemini-3.1-flash-lite-preview"

def call_llm(messages: List[Dict], schema: Dict, temperature: float = 0.0) -> tuple:
    """Call OpenRouter API and return (result, usage_dict)."""
    
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2048,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "step_output",
                "strict": True,
                "schema": schema
            }
        }
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    
    response = requests.post(BASE_URL, json=payload, headers=headers, timeout=180)
    response.raise_for_status()
    
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    
    result = json.loads(content)
    
    # Estimate cost: $0.15/1M input, $0.60/1M output
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    cost = (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000
    
    return result, {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": cost
    }

def generate_iterative(circuit: Dict, max_steps: int = 50, temperature: float = 0.0) -> Dict:
    """Generate circuit iteratively, one block at a time."""
    
    # Schema for step output
    step_schema = {
        "type": "object",
        "required": ["step", "block", "reasoning", "connects_to", "function", "done"],
        "properties": {
            "step": {"type": "integer"},
            "block": {
                "type": "object",
                "required": ["x", "y", "z", "state"],
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "z": {"type": "integer"},
                    "state": {"type": "string"}
                }
            },
            "reasoning": {"type": "string"},
            "connects_to": {"type": "array", "items": {"type": "integer"}},
            "function": {"type": "string"},
            "done": {"type": "boolean", "description": "Set to true when circuit is complete"}
        }
    }
    
    system_prompt = """You are a redstone engineer building circuits step-by-step.
For each step:
1. Place ONE block with exact coordinates (x, y, z) and state
2. Explain WHY this block is needed and HOW it connects to previous blocks
3. Describe its FUNCTION in the circuit
4. List which previous steps it connects to (by step number)
5. Set done=true when the circuit is complete

Use RELATIVE coordinates starting from (0,0,0). Y=0 is the base level.
Think about signal flow, spatial relationships, and circuit function."""
    
    user_prompt = f"""Build this circuit:

**Name:** {circuit['name']}
**Description:** {circuit['description']}
**Difficulty:** {circuit.get('difficulty', 'unknown')}
**Expected blocks:** {circuit.get('expected_blocks', 'unknown')}
"""
    if circuit.get('hints'):
        user_prompt += "\n**Hints:**\n" + "\n".join(f"- {h}" for h in circuit['hints'][:3])
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    steps = []
    total_cost = 0.0
    total_input = 0
    total_output = 0
    
    print(f"\nGenerating {circuit['name']} iteratively...")
    
    for step_num in range(1, max_steps + 1):
        print(f"  Step {step_num}...", end=" ", flush=True)
        
        start = time.time()
        result, usage = call_llm(messages, step_schema, temperature)
        elapsed = time.time() - start
        
        steps.append({
            "step": result.get('step', step_num),
            "block": result.get('block', {}),
            "reasoning": result.get('reasoning', ''),
            "connects_to": result.get('connects_to', []),
            "function": result.get('function', ''),
            "time_sec": elapsed
        })
        
        total_cost += usage['cost']
        total_input += usage['input_tokens']
        total_output += usage['output_tokens']
        
        block = result.get('block', {})
        func = result.get('function', '')[:40]
        print(f"✓ {block.get('state', '?')[:25]:25s} | {func} | {elapsed:.1f}s")
        
        # Add assistant response to conversation
        messages.append({"role": "assistant", "content": json.dumps(result)})
        
        # Check if done
        if result.get('done', False):
            print(f"  Circuit complete in {len(steps)} steps")
            break
        
        # Prompt for next step
        messages.append({"role": "user", "content": "Continue with the next block."})
        
        time.sleep(0.2)  # Rate limiting
    
    return {
        "steps": steps,
        "total_steps": len(steps),
        "total_cost": total_cost,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "blocks": [s['block'] for s in steps]
    }

def analyze_trace_quality(steps: List[Dict]) -> Dict:
    """Analyze quality of reasoning trace."""
    if not steps:
        return {"overall": 0.0}
    
    spatial_scores = []
    functional_scores = []
    contextual_scores = []
    connectivity_scores = []
    
    for step in steps:
        reasoning = step.get('reasoning', '').lower()
        function = step.get('function', '').lower()
        connects_to = step.get('connects_to', [])
        
        # Spatial awareness
        spatial_keywords = ['coordinate', 'position', 'next to', 'above', 'below', 
                          'left', 'right', 'facing', 'at', 'connects', 'adjacent']
        spatial_scores.append(min(sum(1 for kw in spatial_keywords if kw in reasoning) / 2, 1.0))
        
        # Functional clarity
        func_keywords = ['power', 'signal', 'connect', 'activate', 'output', 
                        'input', 'control', 'enable', 'provide', 'transmit']
        functional_scores.append(min(sum(1 for kw in func_keywords if kw in function or kw in reasoning) / 2, 1.0))
        
        # Contextual reasoning (explains WHY)
        why_keywords = ['because', 'so that', 'therefore', 'since', 'in order to',
                       'this allows', 'needed for', 'required for', 'enables', 'so']
        contextual_scores.append(min(sum(1 for kw in why_keywords if kw in reasoning) / 2, 1.0))
        
        # Connectivity tracking
        connectivity_scores.append(1.0 if connects_to else 0.0)
    
    return {
        "spatial_awareness": sum(spatial_scores) / len(spatial_scores) * 100,
        "functional_clarity": sum(functional_scores) / len(functional_scores) * 100,
        "contextual_reasoning": sum(contextual_scores) / len(contextual_scores) * 100,
        "connectivity_tracking": sum(connectivity_scores) / len(connectivity_scores) * 100,
        "overall": (
            sum(spatial_scores) / len(spatial_scores) * 30 +
            sum(functional_scores) / len(functional_scores) * 30 +
            sum(contextual_scores) / len(contextual_scores) * 25 +
            sum(connectivity_scores) / len(connectivity_scores) * 15
        )
    }

def main():
    print("="*80)
    print("ITERATIVE GENERATION TEST")
    print("="*80)
    
    # Load circuits (subset for testing)
    circuits_dir = Path("/home/bergerj/main/personal/minecraft-dev/mira/evaluation/test_circuits")
    test_circuits = [
        "simple_lamp",         # 3 blocks
        "piston_door",         # 15 blocks
        "comparator_subtractor" # 8 blocks
    ]
    
    circuits = []
    for circuit_id in test_circuits:
        json_file = circuits_dir / f"{circuit_id}.json"
        if json_file.exists():
            with open(json_file, 'r') as f:
                circuits.append(json.load(f))
    
    print(f"\nTesting iterative generation on {len(circuits)} circuits:")
    for c in circuits:
        print(f"  - {c['id']} ({c.get('expected_blocks', '?')} blocks)")
    
    results = []
    total_cost = 0.0
    
    for circuit in circuits:
        print(f"\n{'#'*80}")
        print(f"CIRCUIT: {circuit['name']}")
        print(f"{'#'*80}")
        
        for temp in [0.0, 0.5]:
            print(f"\nTemperature: {temp}")
            
            result = generate_iterative(circuit, max_steps=circuit.get('expected_blocks', 20) + 10, temperature=temp)
            
            # Analyze quality
            quality = analyze_trace_quality(result['steps'])
            
            record = {
                "circuit_id": circuit['id'],
                "difficulty": circuit.get('difficulty'),
                "expected_blocks": circuit.get('expected_blocks'),
                "generated_blocks": result['total_steps'],
                "temperature": temp,
                "total_cost": result['total_cost'],
                "total_tokens": result['total_input_tokens'] + result['total_output_tokens'],
                "tokens_input": result['total_input_tokens'],
                "tokens_output": result['total_output_tokens'],
                "quality_score": quality['overall'],
                "quality_breakdown": quality
            }
            
            results.append(record)
            total_cost += result['total_cost']
            
            print(f"\nResults:")
            print(f"  Blocks generated: {result['total_steps']}/{circuit.get('expected_blocks', '?')}")
            print(f"  Cost: ${result['total_cost']:.5f}")
            print(f"  Tokens: {result['total_input_tokens']} in, {result['total_output_tokens']} out")
            print(f"  Quality score: {quality['overall']:.1f}/100")
            print(f"    - Spatial awareness: {quality['spatial_awareness']:.1f}%")
            print(f"    - Functional clarity: {quality['functional_clarity']:.1f}%")
            print(f"    - Contextual reasoning: {quality['contextual_reasoning']:.1f}%")
            print(f"    - Connectivity tracking: {quality['connectivity_tracking']:.1f}%")
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    
    print(f"\nTotal circuits tested: {len(circuits) * 2}")  # 2 temperatures
    print(f"Total cost: ${total_cost:.4f}")
    
    if results:
        avg_quality = sum(r['quality_score'] for r in results) / len(results)
        avg_cost = sum(r['total_cost'] for r in results) / len(results)
        avg_tokens = sum(r['total_tokens'] for r in results) / len(results)
        
        print(f"\nAverages per circuit:")
        print(f"  Quality score: {avg_quality:.1f}/100")
        print(f"  Cost: ${avg_cost:.5f}")
        print(f"  Tokens: {avg_tokens:.0f}")
        
        # Compare with one-shot
        print(f"\nComparison with one-shot generation:")
        print(f"  One-shot avg cost: ~$0.0014")
        print(f"  Iterative avg cost: ${avg_cost:.5f}")
        print(f"  Iterative is {avg_cost/0.0014:.1f}x more expensive")
        print(f"  BUT: Much better reasoning traces (quality {avg_quality:.0f}/100 vs ~12/100)")
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path("/home/bergerj/main/personal/minecraft-dev/mira/evaluation/results/iterative_tests")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    report = {
        "timestamp": timestamp,
        "total_tests": len(results),
        "total_cost": total_cost,
        "results": results
    }
    
    report_path = results_dir / f"iterative_test_{timestamp}.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n✓ Results saved to: {report_path}")

if __name__ == "__main__":
    main()
