import time
import sys
import os

# Add project root to path so we can import simulation.bridge
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from simulation.bridge import MinecraftBridge

def main():
    print("Attempting to connect to Minecraft Server...")
    bridge = MinecraftBridge()
    
    # Retry loop
    for i in range(10):
        try:
            bridge.connect()
            print("Connected!")
            break
        except Exception as e:
            print(f"Connection failed (attempt {i+1}/10): {e}")
            time.sleep(5)
    
    if not bridge._connected:
        print("Could not connect after multiple attempts.")
        sys.exit(1)

    # Test Command
    response = bridge.run_command("list")
    print(f"Server Response to 'list': {response}")
    
    response = bridge.run_command("say MIRA Connection Test Successful")
    print(f"Server Response to 'say': {response}")
    
    bridge.disconnect()
    print("Test passed.")

if __name__ == "__main__":
    main()


