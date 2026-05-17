#!/usr/bin/env python3
"""Quick model speed/latency test. Sends one simple prompt to each model and times it."""

import os
import sys
import time
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.llm_client import OpenRouterClient, ChatMessage

# Simple 20-block redstone lamp circuit
SIMPLE_CIRCUIT = """Describe this Minecraft redstone circuit in 2-3 sentences:

Blocks:
(0,0,0) minecraft:stone
(1,0,0) minecraft:stone
(2,0,0) minecraft:stone
(0,1,0) minecraft:lever[face=floor,facing=east,powered=false]
(1,1,0) minecraft:redstone_wire[east=side,power=15,west=side]
(2,1,0) minecraft:redstone_lamp[lit=false]
(0,0,1) minecraft:stone
(1,0,1) minecraft:stone
(2,0,1) minecraft:stone
(1,1,1) minecraft:redstone_wire[north=side,south=side,power=15]
(1,2,0) minecraft:redstone_wire[east=side,power=15,west=side]

This is a simple lever → redstone → lamp circuit."""

MODELS = [
    ("gemini-flash-lite", "google/gemini-3.1-flash-lite-preview"),
    ("deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
    ("qwen3.5-122b", "qwen/qwen3.5-122b-a10b"),
]

def test_model(client, name, model_id):
    """Test a single model and return timing + output stats."""
    print(f"\n{'='*60}")
    print(f"Testing: {name} ({model_id})")
    print(f"{'='*60}")
    
    start = time.time()
    try:
        response = client.chat(
            model=model_id,
            messages=[ChatMessage(role="user", content=SIMPLE_CIRCUIT)],
            system_prompt="You are a Minecraft redstone expert. Describe circuits concisely.",
            temperature=0.3,
            max_tokens=512,
        )
        elapsed = time.time() - start
        
        content = response.content or ""
        usage = response.usage or {}
        
        # Check for reasoning tokens
        raw = response.raw_response
        reasoning_content = raw.get("choices", [{}])[0].get("message", {}).get("reasoning", "")
        reasoning_tokens = len(reasoning_content.split()) if reasoning_content else 0
        
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        cost = usage.get("cost", 0) or raw.get("usage", {}).get("cost", 0)
        
        # Calculate tokens/sec
        tok_per_sec = completion_tokens / elapsed if elapsed > 0 else 0
        
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Tokens: {prompt_tokens} prompt + {completion_tokens} completion = {total_tokens} total")
        print(f"  Speed: {tok_per_sec:.0f} tok/s")
        print(f"  Reasoning tokens: ~{reasoning_tokens}")
        print(f"  Cost: ${cost:.6f}" if cost else "  Cost: N/A")
        print(f"  Output preview: {content[:200]}...")
        
        return {
            "name": name,
            "model_id": model_id,
            "elapsed_s": round(elapsed, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "tok_per_sec": round(tok_per_sec, 1),
            "reasoning_tokens_approx": reasoning_tokens,
            "cost": cost,
            "content_length": len(content),
            "success": True,
        }
    except Exception as e:
        elapsed = time.time() - start
        print(f"  FAILED after {elapsed:.1f}s: {e}")
        return {
            "name": name,
            "model_id": model_id,
            "elapsed_s": round(elapsed, 2),
            "success": False,
            "error": str(e),
        }

def main():
    client = OpenRouterClient()
    
    results = []
    for name, model_id in MODELS:
        result = test_model(client, name, model_id)
        results.append(result)
        time.sleep(1)  # Brief pause between calls
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Model':<25} {'Time':>6} {'tok/s':>7} {'Output':>7} {'Cost':>10}")
    print(f"{'-'*25} {'-'*6} {'-'*7} {'-'*7} {'-'*10}")
    for r in results:
        if r["success"]:
            print(f"{r['name']:<25} {r['elapsed_s']:>5.1f}s {r['tok_per_sec']:>6.0f} {r['content_length']:>7} ${r.get('cost', 0):>9.6f}")
        else:
            print(f"{r['name']:<25} FAILED  {r.get('error', '')[:40]}")
    
    # Save results
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "training", "speed_test_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()