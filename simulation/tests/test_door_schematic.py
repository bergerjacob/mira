import sys
import os
import time
import unittest

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_schematic

class TestPistonDoor(unittest.TestCase):
    def setUp(self):
        self.bridge = MinecraftBridge()
        self.bridge.connect()
        self.origin = (0, 100, 0)
        self.schematic_path = "data/raw_schematics/simple_piston_door.litematic"
        
    def tearDown(self):
        self.bridge.disconnect()

    def test_door_functionality(self):
        if not os.path.exists(self.schematic_path):
            self.skipTest("Schematic not found")
            
        print("Replicating Piston Door...")
        replicate_schematic(self.schematic_path, self.origin)
        
        # Let physics settle
        time.sleep(1.0)
        
        # Verify initial state (Open)
        # Door blocks should be at X=1 (relative to origin) -> (1, 100, 1)
        # No, wait. My schematic logic:
        # X=0 Pistons. X=1 Stone. X=2 Air. X=3 Wall.
        # Retracted state.
        # Lever is at (2, 4, 1). Powered? No, I placed a lever block. Default is powered=false.
        
        # Check if door is OPEN
        # Stone at X=1?
        # (1, 101, 1) and (1, 102, 1)
        self.verify_block(1, 1, 1, "minecraft:stone")
        self.verify_block(2, 1, 1, "minecraft:air")
        
        print("Initial State: Door OPEN verified.")
        
        # Activate Lever
        # Lever at (2, 4, 1) relative -> (2, 104, 1) absolute
        # Use block toggle? Or setblock powered?
        # lever[face=floor, facing=north, powered=true]
        print("Activating Lever...")
        self.bridge.set_block(2 + self.origin[0], 4 + self.origin[1], 1 + self.origin[2], 
                              "minecraft:lever[face=floor,facing=north,powered=true]")
        
        # Force update to propagate signal?
        # Replicator turns off updates. We need to ensure they are on.
        # Replicator restores updates at the end.
        
        time.sleep(1.0) # Wait for piston extension
        
        # Verify CLOSED state
        # Pistons extend. Piston head at X=1. Stone pushed to X=2.
        # Check X=2 is Stone.
        self.verify_block(2, 1, 1, "minecraft:stone")
        
        print("Final State: Door CLOSED verified.")
        
    def verify_block(self, rx, ry, rz, expected_id):
        ax, ay, az = self.origin[0] + rx, self.origin[1] + ry, self.origin[2] + rz
        # We can use mira_api check_block
        resp = self.bridge.run_command(f"mira_api check_block {ax} {ay} {az} {expected_id}")
        if "FAIL" in resp:
            self.fail(f"Block verification failed at {rx},{ry},{rz}: {resp}")

if __name__ == "__main__":
    unittest.main()


