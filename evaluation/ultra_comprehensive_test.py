#!/usr/bin/env python3
"""
MIRA: Ultra-Comprehensive Test Suite
Runs extensive tests across all models, circuits, and configurations.
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

# Configuration
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

# Model pricing (input/output per 1M tokens)
MODEL_PRICING = {
    "glm-5": (0.30, 0.30),
    "kimi-k2.5": (0.20, 0.20),
    "gemini-flash-lite": (0.15, 0.60),
}

# Models to test - using only gemini-flash-lite (best value/performance)
MODELS = {
    "gemini-flash-lite": "google/gemini-3.1-flash-lite-preview",
}

def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate API cost."""
    pricing = MODEL_PRICING.get(model_name, (0.25, 1.25))
    return (input_tokens * pricing[0] + output_tokens * pricing[1]) / 1_000_000

def call_llm(model: str, system_prompt: str, user_prompt: str, 
             schema: Dict, temperature: float = 0.0, max_retries: int = 3) -> tuple:
    """Call OpenRouter API with retry logic and return (result, usage_dict)."""
    
    for attempt in range(max_retries):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": temperature,
            "max_tokens": 4096,  # Reduced to avoid context limit
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "circuit_output",
                    "strict": True,
                    "schema": schema
                }
            },
            "provider": {"require_parameters": True}
        }
        
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        
        try:
            response = requests.post(BASE_URL, json=payload, headers=headers, timeout=180)
            response.raise_for_status()
            
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            
            result = json.loads(content)
            cost = estimate_cost(model.split('/')[1].split(':')[0], 
                                usage.get("prompt_tokens", 0),
                                usage.get("completion_tokens", 0))
            
            return result, {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "cost": cost
            }
            
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"API error after {max_retries} attempts: {e}")
            time.sleep(2 ** attempt)  # Exponential backoff
        except json.JSONDecodeError as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"JSON parse error after {max_retries} attempts: {e}")
            time.sleep(2 ** attempt)
    
    raise RuntimeError("Unexpected error in retry loop")

def load_json(path: Path) -> Any:
    with open(path, 'r') as f:
        return json.load(f)

def validate_circuit(result: Dict, expected_blocks: int = 0) -> Dict:
    """Validate circuit output."""
    validation = {
        "valid": False,
        "block_count": 0,
        "has_reasoning": False,
        "reasoning_quality": 0,
        "errors": [],
        "components": []
    }
    
    if 'blocks' not in result:
        validation["errors"].append("Missing 'blocks' field")
        return validation
    
    if 'reasoning' not in result:
        validation["errors"].append("Missing 'reasoning' field")
    else:
        validation["has_reasoning"] = True
        reasoning = result['reasoning'].lower()
        # Simple quality heuristic
        quality_words = ['connect', 'signal', 'power', 'because', 'therefore', 'enables']
        validation["reasoning_quality"] = sum(1 for w in quality_words if w in reasoning)
    
    blocks = result['blocks']
    validation["block_count"] = len(blocks)
    
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            validation["errors"].append(f"Block {i} not a dict")
            continue
        
        for field in ['x', 'y', 'z', 'state']:
            if field not in block:
                validation["errors"].append(f"Block {i} missing {field}")
        
        if 'state' in block:
            state = block['state'].lower()
            for comp in ['lever', 'lamp', 'piston', 'repeater', 'comparator', 
                        'observer', 'hopper', 'chest', 'torch', 'wire']:
                if comp in state and comp not in validation["components"]:
                    validation["components"].append(comp)
    
    validation["valid"] = len(validation["errors"]) == 0
    
    return validation

def main():
    print("="*80)
    print("MIRA ULTRA-COMPREHENSIVE TEST SUITE")
    print("="*80)
    
    # Load schemas
    base_dir = Path(__file__).parent
    json_schema = load_json(base_dir / "schemas" / "block_list_schema.json")
    json_system = (base_dir / "prompts" / "json_format_system.md").read_text()
    
    # Load circuits
    circuits_dir = base_dir / "test_circuits"
    circuits = []
    for json_file in sorted(circuits_dir.glob("*.json")):
        circuit = load_json(json_file)
        circuits.append(circuit)
    
    print(f"\nLoaded {len(circuits)} circuits:")
    for c in circuits:
        print(f"  - {c['id']:30s} ({c.get('difficulty', '?'):10s}) {c.get('expected_blocks', '?'):3d} blocks")
    
    # Test configuration
    temperatures = [0.0, 0.5, 1.0]
    formats = ["json"]
    
    total_tests = len(MODELS) * len(circuits) * len(temperatures) * len(formats)
    print(f"\nTest matrix: {len(MODELS)} models × {len(circuits)} circuits × "
          f"{len(temperatures)} temps × {len(formats)} formats = {total_tests} tests")
    
    # Results storage
    all_results = []
    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    
    start_time = time.time()
    
    try:
        for model_name, model_id in MODELS.items():
            print(f"\n{'#'*80}")
            print(f"MODEL: {model_name} ({model_id})")
            print(f"{'#'*80}")
            
            for circuit in circuits:
                for temp in temperatures:
                    for fmt in formats:
                        test_id = f"{model_name}_{circuit['id']}_t{temp}_{fmt}"
                        
                        print(f"\n[{test_id}] ", end="", flush=True)
                        
                        start = time.time()
                        
                        try:
                            # Build prompt
                            prompt = f"""
Build this redstone circuit:

**Name:** {circuit['name']}
**Description:** {circuit['description']}
**Difficulty:** {circuit.get('difficulty', 'unknown')}
**Expected blocks:** {circuit.get('expected_blocks', 'unknown')}
"""
                            if circuit.get('hints'):
                                prompt += "\n**Hints:**\n" + "\n".join(f"- {h}" for h in circuit['hints'][:3])
                            
                            # Call model
                            result, usage = call_llm(
                                model=model_id,
                                system_prompt=json_system,
                                user_prompt=prompt,
                                schema=json_schema,
                                temperature=temp
                            )
                            
                            elapsed = time.time() - start
                            
                            # Validate
                            validation = validate_circuit(result, circuit.get('expected_blocks'))
                            
                            # Record
                            record = {
                                "test_id": test_id,
                                "model": model_name,
                                "circuit_id": circuit['id'],
                                "difficulty": circuit.get('difficulty'),
                                "temperature": temp,
                                "format": fmt,
                                "success": validation["valid"],
                                "block_count": validation["block_count"],
                                "expected_blocks": circuit.get('expected_blocks'),
                                "reasoning_quality": validation["reasoning_quality"],
                                "components": validation["components"],
                                "errors": validation["errors"][:3],
                                "time_sec": round(elapsed, 2),
                                "input_tokens": usage["input_tokens"],
                                "output_tokens": usage["output_tokens"],
                                "cost": usage["cost"]
                            }
                            
                            all_results.append(record)
                            total_cost += usage["cost"]
                            total_input_tokens += usage["input_tokens"]
                            total_output_tokens += usage["output_tokens"]
                            
                            status = "✓" if validation["valid"] else "✗"
                            expected = circuit.get('expected_blocks', '?')
                            print(f"{status} {circuit['id']:25s} | "
                                  f"blocks={validation['block_count']:3d}/{str(expected):3s} | "
                                  f"quality={validation['reasoning_quality']:2d} | "
                                  f"{elapsed:.1f}s | ${usage['cost']:.5f}")
                            
                        except Exception as e:
                            elapsed = time.time() - start
                            print(f"✗ ERROR: {str(e)[:50]}")
                            
                            all_results.append({
                                "test_id": test_id,
                                "model": model_name,
                                "circuit_id": circuit['id'],
                                "temperature": temp,
                                "success": False,
                                "error": str(e),
                                "time_sec": round(elapsed, 2)
                            })
                        
                        # Rate limiting
                        time.sleep(0.3)
    
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user")
    
    # Generate report
    elapsed_total = time.time() - start_time
    
    print(f"\n{'='*80}")
    print("TEST COMPLETE")
    print(f"{'='*80}")
    
    successful = sum(1 for r in all_results if r.get('success', False))
    print(f"Tests: {len(all_results)}/{total_tests} ({100*successful/len(all_results):.1f}% success)")
    print(f"Time: {elapsed_total/60:.1f} minutes")
    print(f"Tokens: {total_input_tokens:,} in, {total_output_tokens:,} out")
    print(f"Cost: ${total_cost:.4f}")
    
    # By model
    print(f"\nBy Model:")
    for model_name in MODELS.keys():
        model_results = [r for r in all_results if r['model'] == model_name]
        if model_results:
            success_rate = sum(1 for r in model_results if r.get('success', False)) / len(model_results) * 100
            avg_quality = sum(r.get('reasoning_quality', 0) for r in model_results) / len(model_results)
            model_cost = sum(r.get('cost', 0) for r in model_results)
            print(f"  {model_name:20s}: {success_rate:5.1f}% success, "
                  f"avg_quality={avg_quality:.1f}, cost=${model_cost:.4f}")
    
    # By difficulty
    print(f"\nBy Difficulty:")
    for diff in ['beginner', 'intermediate', 'advanced', 'expert']:
        diff_results = [r for r in all_results if r.get('difficulty') == diff]
        if diff_results:
            success_rate = sum(1 for r in diff_results if r.get('success', False)) / len(diff_results) * 100
            print(f"  {diff:15s}: {success_rate:5.1f}% ({sum(1 for r in diff_results if r.get('success'))}/{len(diff_results)})")
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = base_dir / "results" / "ultra_comprehensive"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    report = {
        "timestamp": timestamp,
        "total_tests": len(all_results),
        "successful": successful,
        "success_rate": 100 * successful / len(all_results) if all_results else 0,
        "total_time_sec": elapsed_total,
        "total_cost": total_cost,
        "total_tokens_input": total_input_tokens,
        "total_tokens_output": total_output_tokens,
        "results": all_results
    }
    
    report_path = results_dir / f"ultra_comprehensive_{timestamp}.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n✓ Full report saved to: {report_path}")
    
    # Save summary (without full results)
    summary = {k: v for k, v in report.items() if k != 'results'}
    summary_path = results_dir / f"summary_{timestamp}.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"✓ Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()
