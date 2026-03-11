"""
MIRA: Quick LLM Quality Test
Tests model output quality WITHOUT requiring Minecraft server.
Validates JSON parsing, block format, and reasoning quality.
"""

import sys
import json
from pathlib import Path
from typing import Dict, List, Any

sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage


def load_schema(schema_path: str) -> Dict[str, Any]:
    with open(schema_path, 'r') as f:
        return json.load(f)


def load_prompt(prompt_path: str) -> str:
    with open(prompt_path, 'r') as f:
        return f.read()


def validate_block_format(block: Dict) -> List[str]:
    """Validate a single block entry."""
    errors = []
    
    # Check required fields
    for field in ['x', 'y', 'z', 'state']:
        if field not in block:
            errors.append(f"Missing field: {field}")
    
    # Check coordinate types
    for coord in ['x', 'y', 'z']:
        if coord in block and not isinstance(block[coord], int):
            errors.append(f"{coord} should be integer, got {type(block[coord]).__name__}")
    
    # Check state format
    if 'state' in block:
        state = block['state']
        if not isinstance(state, str):
            errors.append("state should be string")
        elif not state.startswith('minecraft:'):
            # Some models omit namespace - not critical but worth noting
            errors.append(f"state should start with 'minecraft:', got: {state[:30]}")
    
    return errors


def test_model_output(model: str, circuit: Dict, format_type: str, client: OpenRouterClient):
    """Test a model on a circuit and validate output quality."""
    
    base_dir = Path(__file__).parent
    
    if format_type == "json":
        schema = load_schema(str(base_dir / "schemas" / "block_list_schema.json"))
        system_prompt = load_prompt(str(base_dir / "prompts" / "json_format_system.md"))
    else:
        schema = load_schema(str(base_dir / "schemas" / "python_code_schema.json"))
        system_prompt = load_prompt(str(base_dir / "prompts" / "python_format_system.md"))
    
    # Build prompt
    user_prompt = f"""
Build the following redstone circuit:

**Name:** {circuit['name']}
**Description:** {circuit['description']}
**Difficulty:** {circuit.get('difficulty', 'unknown')}
"""
    
    if circuit.get('hints'):
        user_prompt += "\n**Hints:**\n" + "\n".join(f"- {h}" for h in circuit['hints'])
    
    print(f"\nTesting {model} on {circuit['id']} ({format_type})...")
    
    try:
        # Call model
        result = client.complete_with_schema(
            model=model,
            prompt=user_prompt,
            system_prompt=system_prompt,
            schema=schema,
            temperature=0.0,
        )
        
        # Validate based on format
        if format_type == "json":
            # Check structure
            if 'reasoning' not in result:
                print(f"  ✗ Missing 'reasoning' field")
                return False
            
            if 'blocks' not in result:
                print(f"  ✗ Missing 'blocks' field")
                return False
            
            blocks = result['blocks']
            
            # Validate each block
            total_errors = 0
            for i, block in enumerate(blocks):
                errors = validate_block_format(block)
                if errors:
                    total_errors += len(errors)
                    if i < 3:  # Only show first few errors
                        print(f"    Block {i} errors: {errors}")
            
            # Summary
            print(f"  ✓ Generated {len(blocks)} blocks")
            print(f"  ✓ Reasoning: {len(result['reasoning'])} chars")
            
            if total_errors > 0:
                print(f"  ⚠ {total_errors} validation warnings")
            
            # Check if it has the expected components
            states = [b.get('state', '').lower() for b in blocks]
            states_str = ' '.join(states)
            
            has_lever = any('lever' in s for s in states)
            has_lamp = any('lamp' in s for s in states)
            has_wire = any('wire' in s or 'redstone' in s for s in states)
            
            if circuit['id'] == 'simple_lamp':
                if has_lever and has_lamp and has_wire:
                    print(f"  ✓ Contains expected components (lever, wire, lamp)")
                else:
                    print(f"  ⚠ Missing expected components: lever={has_lever}, lamp={has_lamp}, wire={has_wire}")
            
            return True
            
        else:  # python format
            if 'reasoning' not in result:
                print(f"  ✗ Missing 'reasoning' field")
                return False
            
            if 'code' not in result:
                print(f"  ✗ Missing 'code' field")
                return False
            
            code = result['code']
            
            # Basic code validation
            has_build = 'def build_circuit' in code
            has_verify = 'def verify_circuit' in code
            has_set_block = 'set_block' in code
            
            print(f"  ✓ Generated {len(code)} chars of code")
            print(f"  ✓ Has build_circuit: {has_build}")
            print(f"  ✓ Has verify_circuit: {has_verify}")
            print(f"  ✓ Uses set_block: {has_set_block}")
            
            if has_build and has_set_block:
                return True
            return False
    
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False


def main():
    api_key = "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"
    client = OpenRouterClient(api_key)
    
    # Test circuits (just simple ones for now)
    circuits = [
        {
            "id": "simple_lamp",
            "name": "Simple Lever-Activated Lamp",
            "description": "Build a simple redstone circuit where flipping a lever turns on a redstone lamp. The lever should be at one end, redstone dust in the middle, and the lamp at the other end.",
            "difficulty": "beginner",
            "expected_blocks": 3,
        },
        {
            "id": "powered_wire",
            "name": "Redstone Wire with Repeater", 
            "description": "Build a redstone line that uses a repeater to maintain signal strength. Place a redstone block as power source, then redstone dust, a repeater, and more dust ending at a lamp.",
            "difficulty": "beginner",
            "hints": [
                "Redstone signal degrades by 1 per block",
                "Repeaters restore signal to strength 15",
                "Repeater must face the direction of signal flow"
            ],
        },
    ]
    
    # Models to test
    models = ["glm-5", "kimi-k2.5", "gemini-flash-lite"]
    
    print("="*70)
    print("MIRA LLM Quality Test (No Server Required)")
    print("="*70)
    
    results = {model: {"json": 0, "python": 0, "json_total": 0, "python_total": 0} for model in models}
    
    for circuit in circuits:
        for model in models:
            for format_type in ["json", "python"]:
                success = test_model_output(model, circuit, format_type, client)
                
                results[model][f"{format_type}_total"] += 1
                if success:
                    results[model][format_type] += 1
                
                # Small delay
                import time
                time.sleep(0.5)
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    for model in models:
        json_rate = results[model]["json"] / results[model]["json_total"] if results[model]["json_total"] > 0 else 0
        python_rate = results[model]["python"] / results[model]["python_total"] if results[model]["python_total"] > 0 else 0
        
        print(f"\n{model}:")
        print(f"  JSON format:   {results[model]['json']}/{results[model]['json_total']} ({100*json_rate:.0f}%)")
        print(f"  Python format: {results[model]['python']}/{results[model]['python_total']} ({100*python_rate:.0f}%)")


if __name__ == "__main__":
    main()
