"""
MIRA: Structure Verification Utility
Manually triggers a verification pass on a specific coordinate region 
to compare current server blocks against an expected state.
"""

import sys
import os
import time

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.bridge import MinecraftBridge
from data_mining.converter import SchematicConverter, setup_datapack

SERVER_DIR = os.path.abspath("simulation/server")

def run_manual_test_structure(litematic_path, origin=(0, 100, 0)):
    filename = os.path.basename(litematic_path)
    # Use simple name 'factory' to avoid ID issues
    schem_name = "factory"
    
    print(f"1. Setting up Datapack & Converting...")
    struct_dir = setup_datapack(SERVER_DIR, namespace="mira")
    target_nbt_path = os.path.join(struct_dir, f"{schem_name}.nbt")
    
    try:
        SchematicConverter.litematic_to_vanilla_structure(litematic_path, target_nbt_path)
        print(f"   Converted to {target_nbt_path}")
    except Exception as e:
        print(f"   Conversion failed: {e}")
        return

    print("2. Connecting to Server...")
    bridge = MinecraftBridge()
    try:
        bridge.connect()
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    print("3. Reloading Datapacks...")
    # Essential to make server recognize the new file
    bridge.run_command("reload")
    time.sleep(2) # Wait for reload
    
    print("   Checking datapack status...")
    resp = bridge.run_command("datapack list enabled")
    if "mira_structures" not in resp and "file/mira_structures" not in resp:
        print(f"Warning: Datapack 'mira_structures' not found in enabled list: {resp}")
        # Try enabling it explicitly
        bridge.run_command("datapack enable file/mira_structures")
    else:
        print("   Datapack confirmed enabled.")

    print(f"4. Placing Structure 'mira:{schem_name}' at {origin}...")
    x, y, z = origin
    
    # Vanilla place command
    # Syntax: /place structure <structure> <pos> [rotation] [mirror] [integrity] [seed]
    cmd = f"place structure mira:{schem_name} {x} {y} {z}"
    
    resp = bridge.run_command(cmd)
    print(f"Server Response: {resp}")
    
    if "Unknown command" in resp:
        print("Error: /place command not found. Are you on 1.19+?")
    elif "Failed to load" in resp:
        print("Error: Structure not found. Check datapack loading.")

    print("Done. Check in-game.")
    bridge.disconnect()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python manual_test_structure.py <path_to_litematic> [x] [y] [z]")
    else:
        path = sys.argv[1]
        x, y, z = 0, 100, 0
        if len(sys.argv) >= 5:
            x = int(sys.argv[2])
            y = int(sys.argv[3])
            z = int(sys.argv[4])
        
        run_manual_test_structure(path, (x, y, z))

