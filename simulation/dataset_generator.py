import json
import os
import sys
import time
import argparse
from typing import Dict, Any, List, Tuple, Optional

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_blocks
from simulation.teacher_client import TeacherClient
from data_mining.corruptor import CircuitCorruptor
from data_mining.parser import SchematicParser

class DatasetGenerator:
    """
    Orchestrates the creation of synthetic training examples (Broken -> Reasoning -> Fixed).
    Refactored for Phase 4: Professionalized Pipeline.
    """
    def __init__(self, bridge: MinecraftBridge):
        self.bridge = bridge
        self.teacher = TeacherClient()
        self.origin = (0, 100, 0) # Build origin

    def process_schematic(self, schematic_path: str, samples_per_schematic: int, outfile) -> int:
        """
        Full pipeline for a single schematic:
        1. Parse & Setup
        2. Golden Run Validation
        3. Corruption Loop (N samples)
        4. Save to file
        
        Returns number of valid samples generated.
        """
        # Step A: The Setup
        print(f"\n[Phase A] Setting up {os.path.basename(schematic_path)}...")
        
        try:
            parser = SchematicParser(schematic_path)
            working_blocks = parser.parse_blocks()
            meta = parser.get_metadata()
            bounds = parser.get_bounds()
        except Exception as e:
            print(f"Error parsing schematic: {e}")
            return 0
            
        working_code = self.blocks_to_text(working_blocks)
        
        # Generate Test Contract
        verify_script = self.teacher.generate_test_contract(working_code, meta)
        
        # Golden Validation
        print("  Running Golden Validation...")
        if not self.run_validation(working_blocks, bounds, verify_script, expect_success=True):
            print("  CRITICAL: Golden Run Failed. Discarding schematic.")
            return 0
            
        print("  Golden Run PASSED. Proceeding to corruption...")
        
        success_count = 0
        
        # Step B: The Corruption Loop
        for i in range(samples_per_schematic):
            print(f"  Sample {i+1}/{samples_per_schematic}...")
            
            sample_data = self.generate_single_sample(working_blocks, bounds, verify_script, meta, working_code)
            
            if sample_data:
                # Step D: The Save
                json_line = json.dumps(sample_data)
                outfile.write(json_line + "\n")
                outfile.flush()
                success_count += 1
                
        return success_count

    def generate_single_sample(self, working_blocks, bounds, verify_script, meta, working_code) -> Optional[Dict]:
        """
        Generates a single broken->fixed example.
        Retries corruption until a valid failure is found.
        """
        corruptor = CircuitCorruptor(working_blocks)
        max_retries = 5
        
        for attempt in range(max_retries):
            # Apply Corruption
            broken_blocks, modifications = corruptor.corrupt()
            if not modifications:
                continue
                
            # Run Test against Broken Circuit
            # We expect this to FAIL. If it passes, the corruption was too weak.
            print(f"    Corruption Attempt {attempt+1}: {modifications[0]['type']}...", end=" ")
            
            error_log = self.run_validation(broken_blocks, bounds, verify_script, expect_success=False)
            
            if error_log:
                # Success! The circuit broke.
                print("FAILED (Good).")
                
                # Step C: Reasoning Generation
                broken_code = self.blocks_to_text(broken_blocks)
                
                context_prompt = f"Name: {meta.get('name')}\nDesc: {meta.get('description')}"
                
                reasoning = self.teacher.generate_reasoning_trace(
                    context=context_prompt,
                    broken_code=broken_code,
                    error_log=error_log,
                    fixed_code=working_code
                )
                
                # Construct Output Object
                return {
                    "schematic_id": meta.get("name", "unknown"),
                    "status": "success",
                    "data": {
                        "context_prompt": context_prompt,
                        "test_contract": verify_script,
                        "broken_code": broken_code,
                        "error_log": error_log,
                        "teacher_trace": reasoning["teacher_trace"],
                        "fixed_code": working_code
                    }
                }
            else:
                # The test Passed, meaning the corruption didn't break functionality.
                print("PASSED (Bad - Weak Corruption). Retrying...")
        
        print("    Failed to generate valid corruption after max retries.")
        return None

    def run_validation(self, blocks, bounds, verify_script, expect_success: bool):
        """
        Deploys blocks and runs the test.
        If expect_success is True: Returns True if Pass, False if Fail.
        If expect_success is False: Returns Error Log string if Fail, None if Pass.
        """
        # Build
        replicate_blocks(blocks, self.origin, bounds, self.bridge, use_updates=False, force_update_region=True)
        
        # Settle
        self.bridge.run_command("tick step 20")
        time.sleep(0.5)
        
        # Execute Test
        try:
            self.execute_test(verify_script, self.origin)
            # Test Passed
            if expect_success:
                return True
            else:
                return None # We wanted failure, but got success
        except Exception as e:
            # Test Failed
            if expect_success:
                print(f"    Validation Error: {e}")
                return False
            else:
                return str(e) # We wanted failure, return the error log

    def execute_test(self, script_source, origin):
        """
        Executes the generated test script.
        """
        local_scope = {}
        # Safe-ish exec. In production, sanitize this.
        try:
            exec(script_source, globals(), local_scope)
        except Exception as e:
             raise Exception(f"Syntax/Import Error in Test Script: {e}")
        
        if 'run_test' not in local_scope:
            raise Exception("Test script did not define 'run_test'")
            
        # Run it
        local_scope['run_test'](self.bridge, origin)

    def blocks_to_text(self, blocks):
        """
        Convert block list to a text representation (Contractor Mode).
        """
        lines = []
        for x, y, z, state, nbt in blocks:
            # Simple representation for now. 
            # In Phase 5 we might want full decorators/functions.
            lines.append(f"ctx.set_block(({x}, {y}, {z}), \"{state}\")")
        return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="MIRA Dataset Generator (Phase 4)")
    parser.add_argument("--input-dir", default="data/raw_schematics", help="Directory containing .litematic files")
    parser.add_argument("--output-file", default="data/training/sft_dataset.jsonl", help="Output JSONL file")
    parser.add_argument("--samples", type=int, default=3, help="Samples per schematic")
    parser.add_argument("--single-file", help="Process a single schematic file")
    
    args = parser.parse_args()
    
    bridge = MinecraftBridge()
    try:
        bridge.connect()
        bridge.run_command("script load mira_api")
    except Exception as e:
        print(f"Failed to connect to server: {e}")
        return

    generator = DatasetGenerator(bridge)
    
    files = []
    if args.single_file:
        files.append(args.single_file)
    else:
        for root, dirs, fnames in os.walk(args.input_dir):
            for f in fnames:
                if f.endswith(".litematic"):
                    files.append(os.path.join(root, f))
    
    print(f"Found {len(files)} schematics to process.")
    
    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    
    total_generated = 0
    with open(args.output_file, "a") as outfile:
        for path in files:
            count = generator.process_schematic(path, args.samples, outfile)
            total_generated += count
            
    print(f"\nProcessing Complete. Generated {total_generated} total samples.")

if __name__ == "__main__":
    main()
