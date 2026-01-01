import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data_mining.parser import SchematicParser

TEST_FILE = "data/raw_schematics/12gt_Dispenser_Factory_Protected.litematic"

def debug_parser():
    if not os.path.exists(TEST_FILE):
        print(f"File not found: {TEST_FILE}")
        return

    print(f"Inspecting {TEST_FILE}...")
    parser = SchematicParser(TEST_FILE)
    blocks = parser.parse_blocks()
    
    print(f"Total blocks: {len(blocks)}")
    
    # Find interesting blocks (chests, dispensers, pistons)
    interesting = []
    for b in blocks:
        # b is (x, y, z, block_state_str, nbt_str)
        if "chest" in b[3] or "piston" in b[3] or "dispenser" in b[3]:
            interesting.append(b)
            
    print(f"Found {len(interesting)} interesting blocks.")
    for i in range(min(10, len(interesting))):
        print(f"Block: {interesting[i]}")

if __name__ == "__main__":
    debug_parser()


