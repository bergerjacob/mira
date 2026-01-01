"""
MIRA: Deconstruction Visualizer
Plays back a generated dataset entry in-game to visualize the deconstruction layers.
"""
import json
import time
import sys
import os

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.bridge import MinecraftBridge

def visualize_deconstruction(jsonl_path, origin=(20, 100, 0)):
    bridge = MinecraftBridge()
    bridge.connect()
    
    print(f"Reading dataset from {jsonl_path}...")
    with open(jsonl_path, 'r') as f:
        data = json.loads(f.readline())
        
    steps = data['data']['deconstruction_steps']
    ox, oy, oz = origin
    
    print(f"Starting visualization of {len(steps)} steps...")
    bridge.run_command("say Starting Deconstruction Visualization...")
    
    for step in steps:
        print(f"Step {step['step']}: {step['reasoning']}")
        bridge.run_command(f"say Step {step['step']}: {step['reasoning']}")
        
        # Remove the blocks in this layer
        for block in step['removed_blocks']:
            bx, by, bz = block['pos']
            bridge.run_command(f"setblock {ox + bx} {oy + by} {oz + bz} air")
        
        time.sleep(2) # Pause so you can see the layer vanish
        
    bridge.run_command("say Deconstruction Complete.")
    bridge.disconnect()

if __name__ == "__main__":
    # First, let's make sure we have a fresh dataset entry for the factory
    os.system("./.venv/bin/python3 simulation/dataset_generator.py --single-file data/raw_schematics/12gt_Dispenser_Factory_Protected.litematic --output-file data/training/factory_viz.jsonl")
    
    visualize_deconstruction("data/training/factory_viz.jsonl")

