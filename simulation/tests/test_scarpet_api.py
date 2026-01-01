import sys
import os
import unittest

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from simulation.bridge import MinecraftBridge

class TestScarpetAPI(unittest.TestCase):
    def setUp(self):
        self.bridge = MinecraftBridge()
        try:
            self.bridge.connect()
        except Exception as e:
            self.skipTest(f"Could not connect to server: {e}")

    def tearDown(self):
        self.bridge.disconnect()

    def test_hello_command(self):
        """Test the basic /mira_api test command."""
        resp = self.bridge.run_command("mira_api test")
        self.assertIn("Command Working", resp)

    def test_check_block(self):
        """Test the check_block function via RCON."""
        # Check air high up in the sky
        resp = self.bridge.run_command("mira_api check_block 0 300 0 minecraft:air")
        self.assertIn("PASS", resp)

    def test_check_block_fail(self):
        """Test that check_block correctly reports failure."""
        # Expect bedrock at 300 height (should fail)
        resp = self.bridge.run_command("mira_api check_block 0 300 0 minecraft:bedrock")
        self.assertIn("FAIL", resp)

if __name__ == "__main__":
    unittest.main()


