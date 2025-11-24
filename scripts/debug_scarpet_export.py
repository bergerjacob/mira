import sys
import os
import time

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.bridge import MinecraftBridge

def debug_export():
    bridge = MinecraftBridge()
    try:
        bridge.connect()
        print("Connected to RCON.")
        print("Loading app...")
        print(bridge.run_command("script load mira_api"))
        
        # Check import list to see exported functions
        print("\n--- Import List ---")
        resp = bridge.run_command("script run import('mira_api')")
        print(f"Response: {resp}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        bridge.disconnect()

if __name__ == "__main__":
    debug_export()

