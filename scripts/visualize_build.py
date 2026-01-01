"""
MIRA: Build Visualizer
Plays back the 'build_steps' from a dataset entry to show the machine growing.
"""
import json
import time
import sys
import os

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.bridge import MinecraftBridge

def visualize_build(jsonl_path, origin=(40, 100, 0)):
    bridge = MinecraftBridge()
    try:
        bridge.connect()
    except:
        print("Could not connect to server.")
        return
    
    # 1. Clear the area first
    print("Clearing area...")
    bridge.run_command(f"fill {origin[0]-1} {origin[1]-1} {origin[2]-1} {origin[0]+10} {origin[1]+10} {origin[2]+10} air")
    bridge.run_command("say Starting Build Visualization (Bottom-Up)...")
    
    print(f"Reading dataset from {jsonl_path}...")
    with open(jsonl_path, 'r') as f:
        data = json.loads(f.readline())
        
    # We use 'build_steps' which are already in the correct order (empty -> complex)
    steps = data['data']['build_steps']
    ox, oy, oz = origin
    
    print(f"Starting visualization of {len(steps)} stages...")
    for step in steps:
        reasoning = step.get("source_reasoning", "No reasoning provided.")
        print(f"Stage {step['stage']}: {step['instruction']}")
        
        # Display reasoning in Minecraft chat
        bridge.run_command(f"say Stage {step['stage']}: {step['instruction']}")
        bridge.run_command(f"say Reasoning: {reasoning}")
        
        for block in step['blocks_to_place']:
            bx, by, bz = block['pos']
            state = block['state']
            # Simple setblock
            bridge.run_command(f"setblock {ox + bx} {oy + by} {oz + bz} {state}")
        
        time.sleep(1.5) # Watch it grow
        
    bridge.run_command("say Build Complete.")
    bridge.disconnect()

if __name__ == "__main__":
    # Generate fresh data for the Piston Door
    os.system("./.venv/bin/python3 simulation/dataset_generator.py --single-file data/raw_schematics/simple_piston_door.litematic --output-file data/training/build_viz.jsonl")
    visualize_build("data/training/build_viz.jsonl")

