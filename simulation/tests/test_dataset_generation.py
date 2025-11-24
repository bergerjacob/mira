import sys
import os
import time
import unittest

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_blocks
from data_mining.corruptor import CircuitCorruptor

class TestDatasetGeneration(unittest.TestCase):
    def setUp(self):
        self.bridge = MinecraftBridge()
        self.bridge.connect()
        # Ensure latest API is loaded
        resp = self.bridge.run_command("script load mira_api")
        print(f"Script Load Response: {resp}")
        self.origin = (0, 100, 0)
        
        # Define a simple valid circuit: Redstone Block -> Lamp
        self.valid_circuit = [
            (0, 0, 0, "minecraft:redstone_block", None),
            (1, 0, 0, "minecraft:redstone_lamp[lit=false]", None)
        ]
        
        # Bounds: Relative (min, max)
        self.bounds = ((0, 0, 0), (2, 1, 1)) 

    def tearDown(self):
        self.bridge.disconnect()

    def test_trajectory_generation(self):
        """
        Tests the full loop with simpler circuit.
        """
        print("\n--- Starting Trajectory Generation Test (Simple) ---")
        
        # 1. Corrupt the circuit
        # We manually corrupt for this test to be sure
        broken_circuit = [
            (0, 0, 0, "minecraft:air", None),
            (1, 0, 0, "minecraft:redstone_lamp[lit=false]", None)
        ]
        
        # 2. Build Broken Circuit
        print("Building BROKEN circuit...")
        # Enable updates so physics can resolve (lamp turning off if it was on, though here it starts off)
        # Use force_update_region to clear ghost signals
        replicate_blocks(broken_circuit, self.origin, self.bounds, self.bridge, use_updates=False, force_update_region=True)
        
        # Allow physics to settle
        self.bridge.run_command("tick step 20")
        time.sleep(1.0) # wait for lamp to turn off
        
        # 3. Verify Failure (Lamp should be OFF)
        # We expect the lamp at (2, 0, 0) relative -> (2, 100, 0) absolute to be unlit.
        lamp_pos = (self.origin[0] + 1, self.origin[1], self.origin[2])
        
        # Check actual state
        check_cmd = f"mira_api check_block {lamp_pos[0]} {lamp_pos[1]} {lamp_pos[2]} minecraft:redstone_lamp[lit=true]"
        response = self.bridge.run_command(check_cmd)
        print(f"Verification Response (Expect FAIL): {response}")
        self.assertIn("FAIL", response)

        # 4. Build Fixed Circuit
        print("Building FIXED circuit...")
        # Enable updates so lamp sees the power source and turns ON
        replicate_blocks(self.valid_circuit, self.origin, self.bounds, self.bridge, use_updates=False, force_update_region=True)
        
        # Allow physics to settle
        self.bridge.run_command("tick step 20")
        time.sleep(1.0)
        
        # 5. Verify Success (Lamp should be ON)
        response = self.bridge.run_command(check_cmd)
        print(f"Verification Response (Expect PASS): {response}")
        self.assertIn("PASS", response)
        
        print("Trajectory Test Passed!")

if __name__ == "__main__":
    unittest.main()
