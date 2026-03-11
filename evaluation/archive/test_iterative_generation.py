# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

"""
MIRA: Iterative Generation Test
Tests the iterative approach where LLM builds circuits step-by-step.
This is the recommended approach to eliminate training-serving skew.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage


class IterativeGenerator:
    """Generates circuits iteratively, one block at a time with reasoning."""
    
    def __init__(self, api_key: str, model: str):
        self.client = OpenRouterClient(api_key)
        self.model = model
        self.messages = []
        
    def create_system_prompt(self) -> str:
        """Create system prompt for iterative generation."""
        return """You are a redstone engineering assistant that builds circuits step-by-step.
For each step, you will:
1. Explain what block you're placing and WHY
2. Explain how it connects to previous blocks
3. Explain its function in the overall circuit
4. Provide the exact coordinates and state

Use RELATIVE coordinates with origin at (0,0,0). Y=0 is the circuit base level.
Always think about:
- Signal flow: where does power come from and where does it go?
- Spatial relationships: how are blocks positioned relative to each other?
- Circuit function: what does each component contribute to the overall behavior?

Output format: JSON with fields:
- "step": step number
- "block": {"x": int, "y": int, "z": int, "state": "minecraft:block_name[properties]"}
- "reasoning": "explanation of why this block is placed here and how it connects"
- "connects_to": [list of previous step numbers this block connects to]
- "function": "what this block does in the circuit"
- "remaining_steps": estimated steps remaining
"""
    
    def create_user_prompt(self, circuit_description: str, current_step: int = 1) -> str:
        """Create user prompt for current step."""
        prompt = f"""
Build this circuit: {circuit_description}

You are on STEP {current_step}. Place the next block in the circuit.

"""
        # Add conversation history
        if self.messages:
            prompt += "\nPrevious steps:\n"
            for msg in self.messages[-5:]:  # Last 5 steps for context
                if msg.get('role') == 'assistant':
                    try:
                        content = json.loads(msg['content'])
                        step = content.get('step', '?')
                        block = content.get('block', {})
                        func = content.get('function', '')
                        prompt += f"  Step {step}: {block.get('state', 'unknown')} at ({block.get('x', '?')}, {block.get('y', '?')}, {block.get('z', '?')}) - {func}\n"
                    except:
                        pass
        
        prompt += "\nProvide the NEXT block placement with reasoning."
        return prompt
    
    def create_schema(self) -> Dict:
        """Create JSON schema for step output."""
        return {
            "type": "object",
            "required": ["step", "block", "reasoning", "connects_to", "function", "remaining_steps"],
            "properties": {
                "step": {"type": "integer", "description": "Current step number"},
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
                "remaining_steps": {"type": "integer"}
            }
        }
    
    def generate_step(self, circuit_description: str, current_step: int = 1, 
                      temperature: float = 0.0) -> Optional[Dict]:
        """Generate one step of the circuit."""
        system_prompt = self.create_system_prompt()
        user_prompt = self.create_user_prompt(circuit_description, current_step)
        schema = self.create_schema()
        
        try:
            result = self.client.complete_with_schema(
                model=self.model,
                prompt=user_prompt,
                system_prompt=system_prompt,
                schema=schema,
                temperature=temperature,
            )
            
            # Add to conversation history
            self.messages.append({
                "role": "user",
                "content": user_prompt
            })
            self.messages.append({
                "role": "assistant",
                "content": json.dumps(result)
            })
            
            return result
        
        except Exception as e:
            print(f"Error generating step {current_step}: {e}")
            return None
    
    def generate_full_circuit(self, circuit_description: str, max_steps: int = 50,
                              temperature: float = 0.0) -> Dict[str, Any]:
        """Generate complete circuit iteratively."""
        print(f"\n{'='*70}")
        print(f"ITERATIVE GENERATION: {self.model}")
        print(f"{'='*70}")
        print(f"Circuit: {circuit_description[:100]}...")
        print(f"Max steps: {max_steps}")
        print(f"Temperature: {temperature}")
        
        self.messages = []  # Reset conversation
        steps = []
        total_cost = 0.0
        total_input_tokens = 0
        total_output_tokens = 0
        
        for step_num in range(1, max_steps + 1):
            print(f"\nStep {step_num}...", end=" ", flush=True)
            
            start_time = time.time()
            result = self.generate_step(circuit_description, step_num, temperature)
            elapsed = time.time() - start_time
            
            if result is None:
                print("FAILED")
                break
            
            # Track metrics
            if hasattr(result, '_last_usage') and result._last_usage:
                usage = result._last_usage
                total_cost += usage.get('cost', 0.0)
                total_input_tokens += usage.get('prompt_tokens', 0)
                total_output_tokens += usage.get('completion_tokens', 0)
            
            steps.append(result)
            
            block = result.get('block', {})
            func = result.get('function', '')
            remaining = result.get('remaining_steps', 0)
            
            print(f"✓ {block.get('state', 'unknown')[:30]:30s} | {func[:40]} | {elapsed:.1f}s")
            
            # Check if complete
            if remaining <= 0:
                print(f"\n✓ Circuit complete in {len(steps)} steps")
                break
            
            # Rate limiting
            time.sleep(0.3)
        
        return {
            "steps": steps,
            "total_steps": len(steps),
            "total_time": sum(s.get('elapsed_time_sec', 0) for s in steps) if steps else 0,
            "total_cost": total_cost,
            "total_tokens_input": total_input_tokens,
            "total_tokens_output": total_output_tokens,
            "model": self.model,
            "temperature": temperature
        }


class IterativeTestRunner:
    """Runs comprehensive iterative generation tests."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.results = []
    
    def load_circuits(self) -> List[Dict]:
        """Load circuit test cases."""
        circuits_dir = Path(__file__).parent / "test_circuits"
        circuits = []
        
        for json_file in sorted(circuits_dir.glob("*.json")):
            with open(json_file, 'r') as f:
                circuit = json.load(f)
                circuits.append(circuit)
        
        return circuits
    
    def test_circuit(self, circuit: Dict, model: str, temperature: float = 0.0) -> Dict:
        """Test iterative generation on a single circuit."""
        generator = IterativeGenerator(self.api_key, model)
        
        description = f"{circuit['name']}: {circuit['description']}"
        if circuit.get('hints'):
            description += " Hints: " + ", ".join(circuit['hints'][:3])
        
        max_steps = circuit.get('expected_blocks', 20) + 5  # Buffer
        
        result = generator.generate_full_circuit(description, max_steps, temperature)
        
        # Analyze quality
        quality = self.analyze_trace_quality(result['steps'])
        
        record = {
            "circuit_id": circuit['id'],
            "circuit_name": circuit['name'],
            "difficulty": circuit.get('difficulty', 'unknown'),
            "expected_blocks": circuit.get('expected_blocks', 'unknown'),
            "model": model,
            "temperature": temperature,
            "generated_steps": result['total_steps'],
            "total_cost": result['total_cost'],
            "total_tokens": result['total_tokens_input'] + result['total_tokens_output'],
            "quality_score": quality['overall_score'],
            "quality_metrics": quality,
            "has_spatial_reasoning": quality['spatial_awareness'] > 0.5,
            "has_functional_reasoning": quality['functional_clarity'] > 0.5,
            "has_contextual_reasoning": quality['contextual_reasoning'] > 0.5
        }
        
        self.results.append(record)
        return record
    
    def analyze_trace_quality(self, steps: List[Dict]) -> Dict[str, float]:
        """Analyze quality of generated reasoning trace."""
        if not steps:
            return {"overall_score": 0.0}
        
        metrics = {
            "spatial_awareness": 0,
            "functional_clarity": 0,
            "contextual_reasoning": 0,
            "connectivity_tracking": 0,
            "total_steps": len(steps)
        }
        
        for step in steps:
            reasoning = step.get('reasoning', '').lower()
            function = step.get('function', '').lower()
            connects_to = step.get('connects_to', [])
            
            # Spatial awareness: mentions coordinates, directions, positions
            spatial_keywords = ['coordinate', 'position', 'next to', 'above', 'below', 
                              'left', 'right', 'facing', 'at', 'connects']
            spatial_count = sum(1 for kw in spatial_keywords if kw in reasoning)
            metrics["spatial_awareness"] += min(spatial_count / 3, 1.0)
            
            # Functional clarity: explains purpose
            func_keywords = ['power', 'signal', 'connect', 'activate', 'output', 
                           'input', 'control', 'enable', 'provide']
            func_count = sum(1 for kw in func_keywords if kw in function or kw in reasoning)
            metrics["functional_clarity"] += min(func_count / 2, 1.0)
            
            # Contextual reasoning: explains WHY not just WHAT
            why_keywords = ['because', 'so that', 'therefore', 'since', 'in order to',
                          'this allows', 'needed for', 'required for', 'enables']
            why_count = sum(1 for kw in why_keywords if kw in reasoning)
            metrics["contextual_reasoning"] += min(why_count / 2, 1.0)
            
            # Connectivity tracking: references other blocks
            metrics["connectivity_tracking"] += 1 if connects_to else 0
        
        # Normalize to 0-100 scale
        n = metrics["total_steps"]
        metrics["spatial_awareness"] = (metrics["spatial_awareness"] / n * 100) if n > 0 else 0
        metrics["functional_clarity"] = (metrics["functional_clarity"] / n * 100) if n > 0 else 0
        metrics["contextual_reasoning"] = (metrics["contextual_reasoning"] / n * 100) if n > 0 else 0
        metrics["connectivity_tracking"] = (metrics["connectivity_tracking"] / n * 100) if n > 0 else 0
        
        # Overall score
        metrics["overall_score"] = (
            metrics["spatial_awareness"] * 0.3 +
            metrics["functional_clarity"] * 0.3 +
            metrics["contextual_reasoning"] * 0.2 +
            metrics["connectivity_tracking"] * 0.2
        )
        
        return metrics
    
    def run_tests(self, models: List[str], circuit_ids: List[str], 
                  temperatures: List[float] = [0.0]):
        """Run iterative generation tests."""
        circuits = self.load_circuits()
        circuit_map = {c['id']: c for c in circuits}
        
        for model in models:
            for circuit_id in circuit_ids:
                if circuit_id not in circuit_map:
                    print(f"⚠ Circuit not found: {circuit_id}")
                    continue
                
                circuit = circuit_map[circuit_id]
                
                for temp in temperatures:
                    self.test_circuit(circuit, model, temp)
                    time.sleep(0.5)
        
        return self.results
    
    def generate_report(self) -> Dict:
        """Generate test report."""
        report = {
            "summary": {
                "total_tests": len(self.results),
                "avg_quality_score": sum(r['quality_score'] for r in self.results) / len(self.results) if self.results else 0,
                "avg_cost": sum(r['total_cost'] for r in self.results) / len(self.results) if self.results else 0,
                "avg_tokens": sum(r['total_tokens'] for r in self.results) / len(self.results) if self.results else 0
            },
            "by_model": {},
            "by_difficulty": {},
            "results": self.results
        }
        
        # Group by model
        for result in self.results:
            model = result['model']
            if model not in report["by_model"]:
                report["by_model"][model] = {
                    "tests": 0,
                    "total_quality": 0,
                    "total_cost": 0
                }
            
            report["by_model"][model]["tests"] += 1
            report["by_model"][model]["total_quality"] += result['quality_score']
            report["by_model"][model]["total_cost"] += result['total_cost']
        
        # Calculate averages
        for model, data in report["by_model"].items():
            data["avg_quality"] = data["total_quality"] / data["tests"]
            data["avg_cost"] = data["total_cost"] / data["tests"]
        
        return report


def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    
    # Test on subset of circuits (iterative is expensive)
    test_circuits = [
        "simple_lamp",         # 3 blocks - quick test
        "piston_door",         # 15 blocks - medium
        "comparator_subtractor" # 8 blocks - advanced
    ]
    
    models = [
        "google/gemini-3.1-flash-lite-preview-06-12"  # Best value
    ]
    
    temperatures = [0.0, 0.5]
    
    runner = IterativeTestRunner(api_key)
    
    try:
        print("\n" + "#"*70)
        print("ITERATIVE GENERATION TEST")
        print("#"*70)
        
        results = runner.run_tests(models, test_circuits, temperatures)
        
        # Generate report
        report = runner.generate_report()
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_dir = Path(__file__).parent / "results" / "iterative_tests"
        results_dir.mkdir(parents=True, exist_ok=True)
        
        report_path = results_dir / f"iterative_report_{timestamp}.json"
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\n✓ Report saved to: {report_path}")
        
        # Print summary
        print(f"\n{'='*70}")
        print("ITERATIVE TEST COMPLETE")
        print(f"{'='*70}")
        print(f"Total tests: {report['summary']['total_tests']}")
        print(f"Avg quality score: {report['summary']['avg_quality_score']:.1f}/100")
        print(f"Avg cost per circuit: ${report['summary']['avg_cost']:.4f}")
        print(f"Avg tokens per circuit: {report['summary']['avg_tokens']:.0f}")
        
        print(f"\nBy Model:")
        for model, data in report["by_model"].items():
            print(f"  {model}:")
            print(f"    Avg quality: {data['avg_quality']:.1f}/100")
            print(f"    Avg cost: ${data['avg_cost']:.4f}")
        
    except KeyboardInterrupt:
        print("\n\nTest interrupted")


if __name__ == "__main__":
    from datetime import datetime
    main()
