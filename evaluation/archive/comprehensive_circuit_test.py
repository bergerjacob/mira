# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

"""
MIRA: Comprehensive Circuit Builder Test
Tests LLM ability to build redstone circuits of varying complexity.
Includes detailed metrics, token usage tracking, and quality analysis.
"""

import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage


class CircuitTestRunner:
    # Model pricing (input/output per 1M tokens)
    MODEL_PRICING = {
        "z-ai/glm-5": (0.3, 0.3),
        "moonshotai/kimi-k2.5": (0.2, 0.2),
        "google/gemini-3.1-flash-lite-preview-06-12": (0.15, 0.60),
        "google/gemini-3.1-flash-lite-preview": (0.15, 0.60),
    }
    
    def __init__(self, api_key: str):
        self.client = OpenRouterClient(api_key)
        self.results = []
        self.token_usage = {
            "total_input": 0,
            "total_output": 0,
            "total_cost": 0.0
        }
    
    def estimate_cost(self, model: str, usage: Dict) -> float:
        """Estimate API cost based on model pricing."""
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        
        # Try to find pricing for this model
        pricing = None
        for key, price in self.MODEL_PRICING.items():
            if key in model:
                pricing = price
                break
        
        if pricing is None:
            # Default pricing
            pricing = (0.25, 1.25)
        
        input_price, output_price = pricing
        cost = (input_tokens * input_price / 1_000_000) + (output_tokens * output_price / 1_000_000)
        return cost
        
    def load_circuit(self, circuit_id: str) -> Dict:
        """Load circuit definition from JSON file."""
        circuit_path = Path(__file__).parent / "test_circuits" / f"{circuit_id}.json"
        with open(circuit_path, 'r') as f:
            return json.load(f)
    
    def load_schema(self, format_type: str) -> Dict:
        """Load JSON schema for output format."""
        schema_name = "block_list_schema.json" if format_type == "json" else "python_code_schema.json"
        schema_path = Path(__file__).parent / "schemas" / schema_name
        with open(schema_path, 'r') as f:
            return json.load(f)
    
    def load_prompt(self, format_type: str) -> str:
        """Load system prompt for output format."""
        prompt_name = "json_format_system.md" if format_type == "json" else "python_format_system.md"
        prompt_path = Path(__file__).parent / "prompts" / prompt_name
        with open(prompt_path, 'r') as f:
            return f.read()
    
    def build_user_prompt(self, circuit: Dict, include_hints: bool = True) -> str:
        """Build user prompt from circuit definition."""
        prompt = f"""
Build the following redstone circuit:

**Name:** {circuit['name']}
**Description:** {circuit['description']}
**Difficulty:** {circuit.get('difficulty', 'unknown')}
**Expected blocks:** {circuit.get('expected_blocks', 'unknown')}
"""
        if include_hints and circuit.get('hints'):
            prompt += "\n**Hints:**\n"
            for hint in circuit['hints']:
                prompt += f"- {hint}\n"
        
        return prompt
    
    def validate_output(self, result: Dict, format_type: str, circuit: Dict) -> Dict[str, Any]:
        """Validate model output and return metrics."""
        validation = {
            "valid": False,
            "block_count": 0,
            "has_reasoning": False,
            "reasoning_length": 0,
            "errors": [],
            "warnings": [],
            "components_found": []
        }
        
        try:
            if format_type == "json":
                if 'reasoning' not in result:
                    validation["errors"].append("Missing 'reasoning' field")
                else:
                    validation["has_reasoning"] = True
                    validation["reasoning_length"] = len(result['reasoning'])
                
                if 'blocks' not in result:
                    validation["errors"].append("Missing 'blocks' field")
                    return validation
                
                blocks = result['blocks']
                validation["block_count"] = len(blocks)
                
                # Validate each block
                for i, block in enumerate(blocks):
                    if not isinstance(block, dict):
                        validation["errors"].append(f"Block {i} is not a dictionary")
                        continue
                    
                    # Check required fields
                    for field in ['x', 'y', 'z', 'state']:
                        if field not in block:
                            validation["errors"].append(f"Block {i} missing field: {field}")
                    
                    # Check coordinate types
                    for coord in ['x', 'y', 'z']:
                        if coord in block and not isinstance(block[coord], int):
                            validation["warnings"].append(f"Block {i} {coord} should be integer")
                    
                    # Extract component type
                    if 'state' in block:
                        state = block['state'].lower()
                        if 'lever' in state:
                            validation["components_found"].append("lever")
                        elif 'lamp' in state:
                            validation["components_found"].append("lamp")
                        elif 'piston' in state:
                            validation["components_found"].append("piston")
                        elif 'repeater' in state:
                            validation["components_found"].append("repeater")
                        elif 'comparator' in state:
                            validation["components_found"].append("comparator")
                        elif 'observer' in state:
                            validation["components_found"].append("observer")
                        elif 'hopper' in state:
                            validation["components_found"].append("hopper")
                        elif 'chest' in state:
                            validation["components_found"].append("chest")
                        elif 'wire' in state or 'redstone' in state:
                            validation["components_found"].append("redstone_wire")
                        elif 'torch' in state:
                            validation["components_found"].append("torch")
                
                validation["valid"] = len(validation["errors"]) == 0
                
            else:  # python format
                if 'reasoning' not in result:
                    validation["errors"].append("Missing 'reasoning' field")
                else:
                    validation["has_reasoning"] = True
                    validation["reasoning_length"] = len(result['reasoning'])
                
                if 'code' not in result:
                    validation["errors"].append("Missing 'code' field")
                    return validation
                
                code = result['code']
                validation["block_count"] = code.count('set_block')
                
                # Check for key functions
                if 'def build_circuit' in code:
                    validation["components_found"].append("build_function")
                if 'def verify_circuit' in code:
                    validation["components_found"].append("verify_function")
                
                validation["valid"] = len(validation["errors"]) == 0
        
        except Exception as e:
            validation["errors"].append(f"Validation error: {str(e)}")
        
        return validation
    
    def test_circuit(self, model: str, circuit_id: str, format_type: str, 
                     temperature: float = 0.0) -> Dict[str, Any]:
        """Test a single circuit build."""
        print(f"\n{'='*70}")
        print(f"Testing: {model} | {circuit_id} | {format_type} | temp={temperature}")
        print(f"{'='*70}")
        
        # Load circuit and schema
        circuit = self.load_circuit(circuit_id)
        schema = self.load_schema(format_type)
        system_prompt = self.load_prompt(format_type)
        user_prompt = self.build_user_prompt(circuit)
        
        print(f"Circuit: {circuit['name']}")
        print(f"Difficulty: {circuit.get('difficulty', 'unknown')}")
        print(f"Expected blocks: {circuit.get('expected_blocks', 'unknown')}")
        
        start_time = time.time()
        
        try:
            # Call model using chat method to get LLMResponse with usage
            from simulation.llm_client import ChatMessage
            
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": schema,
                }
            }
            
            llm_response = self.client.chat(
                model=model,
                messages=[ChatMessage(role="user", content=user_prompt)],
                system_prompt=system_prompt,
                temperature=temperature,
                response_format=response_format,
            )
            
            # Track token usage
            usage = llm_response.usage
            self.token_usage["total_input"] += usage.get("prompt_tokens", 0)
            self.token_usage["total_output"] += usage.get("completion_tokens", 0)
            # Estimate cost based on model pricing
            self.token_usage["total_cost"] += self.estimate_cost(model, usage)
            
            # Parse JSON response
            import json
            result = json.loads(llm_response.content)
            
            elapsed_time = time.time() - start_time
            
            # Validate output
            validation = self.validate_output(result, format_type, circuit)
            
            # Build result record
            record = {
                "timestamp": datetime.now().isoformat(),
                "model": model,
                "circuit_id": circuit_id,
                "circuit_name": circuit['name'],
                "difficulty": circuit.get('difficulty', 'unknown'),
                "format_type": format_type,
                "temperature": temperature,
                "success": validation["valid"],
                "block_count": validation["block_count"],
                "expected_blocks": circuit.get('expected_blocks', 'unknown'),
                "has_reasoning": validation["has_reasoning"],
                "reasoning_length": validation["reasoning_length"],
                "components_found": list(set(validation["components_found"])),
                "errors": validation["errors"],
                "warnings": validation["warnings"],
                "elapsed_time_sec": round(elapsed_time, 2),
                "token_usage": {
                    "input": usage.get("prompt_tokens", 0) if 'usage' in dir() else 0,
                    "output": usage.get("completion_tokens", 0) if 'usage' in dir() else 0,
                    "cost": usage.get("cost", 0.0) if 'usage' in dir() else 0.0
                }
            }
            
            # Print summary
            status = "✓" if validation["valid"] else "✗"
            print(f"\n{status} Result: {'VALID' if validation['valid'] else 'INVALID'}")
            print(f"  Blocks: {validation['block_count']} (expected: {circuit.get('expected_blocks', 'unknown')})")
            print(f"  Reasoning: {validation['reasoning_length']} chars")
            print(f"  Components: {', '.join(record['components_found'])}")
            print(f"  Time: {elapsed_time:.2f}s")
            if 'usage' in dir():
                print(f"  Tokens: {usage.get('prompt_tokens', 0)} in, {usage.get('completion_tokens', 0)} out")
                print(f"  Cost: ${usage.get('cost', 0.0):.4f}")
            
            if validation["errors"]:
                print(f"  Errors: {validation['errors'][:3]}")  # Show first 3
            
            self.results.append(record)
            return record
        
        except Exception as e:
            elapsed_time = time.time() - start_time
            print(f"✗ FAILED: {str(e)}")
            
            record = {
                "timestamp": datetime.now().isoformat(),
                "model": model,
                "circuit_id": circuit_id,
                "circuit_name": circuit['name'],
                "difficulty": circuit.get('difficulty', 'unknown'),
                "format_type": format_type,
                "temperature": temperature,
                "success": False,
                "error": str(e),
                "elapsed_time_sec": round(elapsed_time, 2)
            }
            
            self.results.append(record)
            return record
    
    def run_benchmark(self, models: List[str], circuits: List[str], 
                      formats: List[str] = ["json"], temperatures: List[float] = [0.0]):
        """Run full benchmark across all combinations."""
        print(f"\n{'#'*70}")
        print(f"STARTING COMPREHENSIVE BENCHMARK")
        print(f"{'#'*70}")
        print(f"Models: {', '.join(models)}")
        print(f"Circuits: {', '.join(circuits)}")
        print(f"Formats: {', '.join(formats)}")
        print(f"Temperatures: {temperatures}")
        print(f"Total tests: {len(models) * len(circuits) * len(formats) * len(temperatures)}")
        
        total_tests = 0
        for model in models:
            for circuit in circuits:
                for format_type in formats:
                    for temp in temperatures:
                        total_tests += 1
                        self.test_circuit(model, circuit, format_type, temp)
                        
                        # Rate limiting
                        time.sleep(0.5)
        
        return self.results
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive report from results."""
        report = {
            "summary": {
                "total_tests": len(self.results),
                "successful": sum(1 for r in self.results if r.get("success", False)),
                "failed": sum(1 for r in self.results if not r.get("success", True)),
                "success_rate": 0.0,
                "total_tokens_input": self.token_usage["total_input"],
                "total_tokens_output": self.token_usage["total_output"],
                "total_cost": self.token_usage["total_cost"],
            },
            "by_model": {},
            "by_difficulty": {},
            "by_format": {},
            "by_circuit": {},
            "detailed_results": self.results
        }
        
        # Calculate success rate
        if report["summary"]["total_tests"] > 0:
            report["summary"]["success_rate"] = (
                report["summary"]["successful"] / report["summary"]["total_tests"]
            ) * 100
        
        # Group by model
        for result in self.results:
            model = result["model"]
            if model not in report["by_model"]:
                report["by_model"][model] = {
                    "total": 0,
                    "successful": 0,
                    "avg_blocks": 0,
                    "avg_time": 0,
                    "tests": []
                }
            
            report["by_model"][model]["total"] += 1
            if result.get("success", False):
                report["by_model"][model]["successful"] += 1
            report["by_model"][model]["tests"].append(result)
        
        # Calculate averages by model
        for model, data in report["by_model"].items():
            if data["total"] > 0:
                data["success_rate"] = (data["successful"] / data["total"]) * 100
                data["avg_blocks"] = sum(
                    r.get("block_count", 0) for r in data["tests"]
                ) / data["total"]
                data["avg_time"] = sum(
                    r.get("elapsed_time_sec", 0) for r in data["tests"]
                ) / data["total"]
        
        # Group by difficulty
        difficulties = ["beginner", "intermediate", "advanced", "expert"]
        for diff in difficulties:
            diff_results = [r for r in self.results if r.get("difficulty") == diff]
            if diff_results:
                successful = sum(1 for r in diff_results if r.get("success", False))
                report["by_difficulty"][diff] = {
                    "total": len(diff_results),
                    "successful": successful,
                    "success_rate": (successful / len(diff_results)) * 100
                }
        
        # Group by format
        for format_type in ["json", "python"]:
            format_results = [r for r in self.results if r.get("format_type") == format_type]
            if format_results:
                successful = sum(1 for r in format_results if r.get("success", False))
                report["by_format"][format_type] = {
                    "total": len(format_results),
                    "successful": successful,
                    "success_rate": (successful / len(format_results)) * 100
                }
        
        return report


def main():
    api_key = "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"
    
    # Define test parameters
    models = [
        "z-ai/glm-5",
        "moonshotai/kimi-k2.5:latest", 
        "google/gemini-3.1-flash-lite-preview-06-12"
    ]
    
    # All circuits including new complex ones
    circuits = [
        "simple_lamp",           # 3 blocks - beginner
        "hopper_transporter",    # 3 blocks - beginner
        "power_repeater",        # 6 blocks - beginner
        "observer_torch",        # 4 blocks - intermediate
        "piston_door",           # 15 blocks - intermediate
        "comparator_subtractor", # 8 blocks - advanced
        "randomizer",            # 32 blocks - advanced
        "elevator",              # 64 blocks - advanced
        "t_flip_flop",           # 24 blocks - expert
        "item_sorter",           # 54 blocks - expert
        "automatic_farm",        # 87 blocks - expert
        # "4bit_adder"            # 96 blocks - expert (commented out - very complex)
    ]
    
    formats = ["json"]  # JSON is more reliable
    temperatures = [0.0, 0.5, 1.0]  # Test temperature effects
    
    runner = CircuitTestRunner(api_key)
    
    try:
        results = runner.run_benchmark(models, circuits, formats, temperatures)
        
        # Generate report
        report = runner.generate_report()
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_dir = Path(__file__).parent / "results" / "comprehensive_benchmark"
        results_dir.mkdir(parents=True, exist_ok=True)
        
        # Save full report
        report_path = results_dir / f"comprehensive_report_{timestamp}.json"
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\n✓ Full report saved to: {report_path}")
        
        # Save summary
        summary = {
            "summary": report["summary"],
            "by_model": {k: {kk: vv for kk, vv in v.items() if kk != "tests"} 
                        for k, v in report["by_model"].items()},
            "by_difficulty": report["by_difficulty"],
            "by_format": report["by_format"]
        }
        summary_path = results_dir / f"comprehensive_summary_{timestamp}.json"
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        print(f"✓ Summary saved to: {summary_path}")
        
        # Print summary
        print(f"\n{'='*70}")
        print("BENCHMARK COMPLETE")
        print(f"{'='*70}")
        print(f"Total tests: {report['summary']['total_tests']}")
        print(f"Successful: {report['summary']['successful']} ({report['summary']['success_rate']:.1f}%)")
        print(f"Failed: {report['summary']['failed']}")
        print(f"Total cost: ${report['summary']['total_cost']:.4f}")
        print(f"\nBy Model:")
        for model, data in report["by_model"].items():
            print(f"  {model}: {data['successful']}/{data['total']} ({data['success_rate']:.1f}%)")
        print(f"\nBy Difficulty:")
        for diff, data in report["by_difficulty"].items():
            print(f"  {diff}: {data['successful']}/{data['total']} ({data['success_rate']:.1f}%)")
        
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user")
        if runner.results:
            report = runner.generate_report()
            print(f"Partial results: {len(runner.results)} tests completed")
            print(f"Cost so far: ${runner.token_usage['total_cost']:.4f}")


if __name__ == "__main__":
    main()
