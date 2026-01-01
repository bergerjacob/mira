import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.replicator import replicate_schematic

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python replicate_schematic.py <path_to_litematic> [x] [y] [z]")
        sys.exit(1)
        
    path = sys.argv[1]
    
    x, y, z = 0, 100, 0
    if len(sys.argv) >= 5:
        x = int(sys.argv[2])
        y = int(sys.argv[3])
        z = int(sys.argv[4])
        
    print(f"Starting replication of {path} at origin ({x}, {y}, {z})...")
    replicate_schematic(path, (x, y, z))
    print("Replication process finished.")

