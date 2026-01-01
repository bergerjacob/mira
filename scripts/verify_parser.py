import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data_mining.parser import SchematicParser

# Create a small dummy litematic if one doesn't exist for testing or use existing
# Since I can't easily create a binary litematic, I will rely on the one the user has:
# data/raw_schematics/12gt_Dispenser_Factory_Protected.litematic

TEST_FILE = "data/raw_schematics/12gt_Dispenser_Factory_Protected.litematic"

def test_parser():
    print(f"Testing parser on {TEST_FILE}")
    if not os.path.exists(TEST_FILE):
        print("Test file not found, skipping.")
        return

    try:
        parser = SchematicParser(TEST_FILE)
        blocks = parser.parse_blocks()
        print(f"Successfully parsed {len(blocks)} blocks.")
        if len(blocks) > 0:
            print(f"Sample block: {blocks[0]}")
    except Exception as e:
        print(f"Parser failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_parser()


