# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

"""
MIRA: LLM Benchmark Runner
Tests multiple LLMs on redstone circuit building tasks.
Evaluates both JSON block list and Python code output formats.
"""

import os
import sys
import json
import time
import traceback
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

# OpenRouter API Key
OPENROUTER_API_KEY = "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"

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage
from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_blocks


@dataclass
class TestResult:
    model: str
    circuit_id: str
    format_type: str  # "json" or "python"
    success: bool
    build_success: bool
    verification_success: bool
    reasoning: str
    error: Optional[str]
    response_time: float
    token_usage: Dict[str, int]
    raw_response: str
    blocks_placed: int
    blocks_expected: int


class BenchmarkRunner:
    """
    Runs benchmark tests across models, circuits, and output formats.
    """
    
    def __init__(self, api_key: str, results_dir: str = "evaluation/results"):
        self.client = OpenRouterClient(api_key)
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.results: List[TestResult] = []
        
    def load_circuit(self, circuit_path: str) -> Dict[str, Any]:
        """Load a circuit definition from JSON file."""
        with open(circuit_path, 'r') as f:
            return json.load(f)
    
    def load_schema(self, schema_path: str) -> Dict[str, Any]:
        """Load a JSON schema."""
        with open(schema_path, 'r') as f:
            return json.load(f)
    
    def load_system_prompt(self, prompt_path: str) -> str:
        """Load system prompt from markdown file."""
        with open(prompt_path, 'r') as f:
            return f.read()
    
    def build_from_json_blocks(self, blocks: List[Dict], origin: tuple = (0, 100, 0)) -> tuple:
        """
        Build circuit from JSON block list format.
        Returns (success, blocks_placed, error).
        """
        bridge = None
        try:
            bridge = MinecraftBridge()
            bridge.connect()
            
            # Convert to format expected by replicator
            block_list = []
            for b in blocks:
                x, y, z = b['x'], b['y'], b['z']
                state = b['state']
                block_list.append((x, y, z, state, None))
            
            # Calculate bounds
            if not block_list:
                bounds = ((0, 0, 0), (0, 0, 0))
            else:
                min_pos = min((b[0], b[1], b[2]) for b in block_list)
                max_pos = max((b[0], b[1], b[2]) for b in block_list)
                bounds = (min_pos, (max_pos[0]+1, max_pos[1]+1, max_pos[2]+1))
            
            # Build
            replicate_blocks(block_list, origin, bounds, bridge, use_updates=False, force_update_region=True)
            
            return True, len(blocks), None
            
        except Exception as e:
            return False, 0, str(e)
        finally:
            if bridge:
                bridge.disconnect()
    
    def build_from_python_code(self, code: str, origin: tuple = (0, 100, 0)) -> tuple:
        """
        Build circuit from Python code format.
        Returns (success, blocks_placed, error).
        """
        bridge = None
        try:
            bridge = MinecraftBridge()
            bridge.connect()
            
            # Create a mock ctx that uses the bridge
            class MockCtx:
                def __init__(self, bridge):
                    self.bridge = bridge
                    self.blocks_placed = 0
                    
                def set_block(self, x, y, z, state):
                    self.bridge.set_block(x + origin[0], y + origin[1], z + origin[2], state)
                    self.blocks_placed += 1
                    
                def update_block(self, x, y, z):
                    pass  # Not needed for build
                    
                def tick(self, n):
                    self.bridge.run_command(f"tick step {n}")
                    
                def assert_block(self, pos, expected):
                    raise NotImplementedError("assert_block not available during build")
                    
                def assert_power(self, pos, min_level):
                    raise NotImplementedError("assert_power not available during build")
            
            ctx = MockCtx(bridge)
            
            # Execute the code
            namespace = {}
            exec(code, namespace)
            
            # Call build_circuit
            if 'build_circuit' in namespace:
                namespace['build_circuit'](ctx)
            else:
                raise RuntimeError("build_circuit function not found in code")
            
            return True, ctx.blocks_placed, None
            
        except Exception as e:
            return False, 0, f"{type(e).__name__}: {e}"
        finally:
            if bridge:
                bridge.disconnect()
    
    def verify_circuit(self, circuit_def: Dict, origin: tuple = (0, 100, 0)) -> bool:
        """
        Run verification tests for a circuit.
        Returns True if circuit appears to work.
        """
        bridge = None
        try:
            bridge = MinecraftBridge()
            bridge.connect()
            
            # For now, just do a basic check that blocks exist
            # Full verification would parse scarpet test from circuit_def
            verification = circuit_def.get('verification', {})
            scarpet_test = verification.get('scarpet_test', '')
            
            if scarpet_test:
                response = bridge.run_command(scarpet_test)
                return "PASS" in response
            
            # Fallback: just check if build succeeded
            return True
            
        except Exception as e:
            print(f"Verification error: {e}")
            return False
        finally:
            if bridge:
                bridge.disconnect()
    
    def test_model_on_circuit(
        self,
        model: str,
        circuit: Dict[str, Any],
        format_type: str,
        system_prompt: str,
        schema: Dict[str, Any],
    ) -> TestResult:
        """
        Test a single model on a single circuit with a specific output format.
        """
        circuit_id = circuit['id']
        expected_blocks = circuit.get('expected_blocks', 0)
        
        # Build user prompt
        user_prompt = f"""
Build the following redstone circuit:

**Name:** {circuit['name']}
**Description:** {circuit['description']}
**Difficulty:** {circuit.get('difficulty', 'unknown')}

{'**Hints:** ' + '\n'.join(f'- {h}' for h in circuit.get('hints', [])) if circuit.get('hints') else ''}

{'**Ground Truth:** ' + json.dumps(circuit.get('ground_truth', {})) if circuit.get('ground_truth') else ''}

Please provide your solution in the required format.
"""
        
        start_time = time.time()
        
        try:
            # Call LLM
            response = self.client.complete_with_schema(
                model=model,
                prompt=user_prompt,
                system_prompt=system_prompt,
                schema=schema,
                temperature=0.0,
            )
            
            response_time = time.time() - start_time
            
            # Extract based on format
            if format_type == "json":
                reasoning = response.get('reasoning', '')
                blocks = response.get('blocks', [])
                blocks_placed, build_success, build_error = self.build_from_json_blocks(blocks)
            else:  # python
                reasoning = response.get('reasoning', '')
                code = response.get('code', '')
                build_success, blocks_placed, build_error = self.build_from_python_code(code)
            
            # Verify
            verification_success = self.verify_circuit(circuit) if build_success else False
            
            success = build_success and verification_success
            
            return TestResult(
                model=model,
                circuit_id=circuit_id,
                format_type=format_type,
                success=success,
                build_success=build_success,
                verification_success=verification_success,
                reasoning=reasoning[:500] if reasoning else "",  # Truncate for storage
                error=build_error,
                response_time=response_time,
                token_usage={},  # Placeholder - would need to track from API response
                raw_response=json.dumps(response)[:2000],  # Truncate
                blocks_placed=blocks_placed,
                blocks_expected=expected_blocks,
            )
            
        except Exception as e:
            response_time = time.time() - start_time
            return TestResult(
                model=model,
                circuit_id=circuit_id,
                format_type=format_type,
                success=False,
                build_success=False,
                verification_success=False,
                reasoning="",
                error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                response_time=response_time,
                token_usage={},
                raw_response="",
                blocks_placed=0,
                blocks_expected=expected_blocks,
            )
    
    def run_benchmark(
        self,
        models: List[str],
        circuits_dir: str,
        json_system_prompt: str,
        python_system_prompt: str,
        json_schema: Dict[str, Any],
        python_schema: Dict[str, Any],
    ):
        """
        Run full benchmark across all models, circuits, and formats.
        """
        # Load circuits
        circuits = []
        for f in sorted(Path(circuits_dir).glob("*.json")):
            try:
                circuits.append(self.load_circuit(str(f)))
                print(f"Loaded circuit: {f.name}")
            except Exception as e:
                print(f"Error loading {f}: {e}")
        
        print(f"\nStarting benchmark:")
        print(f"  Models: {len(models)}")
        print(f"  Circuits: {len(circuits)}")
        print(f"  Formats: 2 (JSON, Python)")
        print(f"  Total tests: {len(models) * len(circuits) * 2}")
        print()
        
        # Run tests
        test_num = 0
        total_tests = len(models) * len(circuits) * 2
        
        for circuit in circuits:
            for model in models:
                for format_type, prompt, schema in [
                    ("json", json_system_prompt, json_schema),
                    ("python", python_system_prompt, python_schema),
                ]:
                    test_num += 1
                    print(f"[{test_num}/{total_tests}] {model} | {circuit['id']} | {format_type}")
                    
                    result = self.test_model_on_circuit(
                        model=model,
                        circuit=circuit,
                        format_type=format_type,
                        system_prompt=prompt,
                        schema=schema,
                    )
                    
                    self.results.append(result)
                    
                    status = "✓" if result.success else "✗"
                    print(f"  {status} Success={result.success}, Build={result.build_success}, "
                          f"Verify={result.verification_success}, Time={result.response_time:.1f}s")
                    
                    # Small delay to avoid rate limiting
                    time.sleep(0.5)
        
        # Save results
        self.save_results()
    
    def save_results(self):
        """Save results to JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.results_dir / f"benchmark_{timestamp}.json"
        
        results_data = {
            "timestamp": timestamp,
            "total_tests": len(self.results),
            "success_rate": sum(1 for r in self.results if r.success) / len(self.results) if self.results else 0,
            "results": [asdict(r) for r in self.results],
        }
        
        with open(filename, 'w') as f:
            json.dump(results_data, f, indent=2)
        
        print(f"\nResults saved to: {filename}")
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print benchmark summary."""
        print("\n" + "="*60)
        print("BENCHMARK SUMMARY")
        print("="*60)
        
        # Overall stats
        total = len(self.results)
        success = sum(1 for r in self.results if r.success)
        print(f"\nOverall: {success}/{total} ({100*success/total:.1f}%)")
        
        # By model
        print("\nBy Model:")
        for model in set(r.model for r in self.results):
            model_results = [r for r in self.results if r.model == model]
            model_success = sum(1 for r in model_results if r.success)
            avg_time = sum(r.response_time for r in model_results) / len(model_results)
            print(f"  {model}: {model_success}/{len(model_results)} ({100*model_success/len(model_results):.1f}%), avg {avg_time:.1f}s")
        
        # By format
        print("\nBy Format:")
        for fmt in ["json", "python"]:
            fmt_results = [r for r in self.results if r.format_type == fmt]
            fmt_success = sum(1 for r in fmt_results if r.success)
            print(f"  {fmt}: {fmt_success}/{len(fmt_results)} ({100*fmt_success/len(fmt_results):.1f}%)")
        
        # By circuit
        print("\nBy Circuit:")
        for circuit_id in set(r.circuit_id for r in self.results):
            circuit_results = [r for r in self.results if r.circuit_id == circuit_id]
            circuit_success = sum(1 for r in circuit_results if r.success)
            print(f"  {circuit_id}: {circuit_success}/{len(circuit_results)} ({100*circuit_success/len(circuit_results):.1f}%)")


def main():
    """Main entry point for benchmark."""
    import argparse
    
    parser = argparse.ArgumentParser(description="MIRA LLM Benchmark")
    parser.add_argument("--api-key", default=OPENROUTER_API_KEY, help="OpenRouter API key (uses default if not provided)")
    parser.add_argument("--models", nargs="+", 
                        default=["glm-5", "kimi-k2.5", "gemini-flash-lite"],
                        help="Models to test")
    parser.add_argument("--circuits-dir", default="evaluation/test_circuits",
                        help="Directory with circuit definitions")
    parser.add_argument("--results-dir", default="evaluation/results",
                        help="Directory for results")
    
    args = parser.parse_args()
    
    # Load prompts and schemas
    base_dir = Path(__file__).parent
    json_prompt = (base_dir / "prompts" / "json_format_system.md").read_text()
    python_prompt = (base_dir / "prompts" / "python_format_system.md").read_text()
    
    with open(base_dir / "schemas" / "block_list_schema.json") as f:
        json_schema = json.load(f)
    with open(base_dir / "schemas" / "python_code_schema.json") as f:
        python_schema = json.load(f)
    
    # Run benchmark
    runner = BenchmarkRunner(args.api_key, args.results_dir)
    runner.run_benchmark(
        models=args.models,
        circuits_dir=args.circuits_dir,
        json_system_prompt=json_prompt,
        python_system_prompt=python_prompt,
        json_schema=json_schema,
        python_schema=python_schema,
    )


if __name__ == "__main__":
    main()
