import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data_mining.parser import SchematicParser

SCHEM_DIR = "data/raw_schematics"

def list_schematics():
    if not os.path.exists(SCHEM_DIR):
        print(f"Directory {SCHEM_DIR} does not exist.")
        return

    files = [f for f in os.listdir(SCHEM_DIR) if f.endswith(".litematic")]
    
    if not files:
        print(f"No .litematic files found in {SCHEM_DIR}")
        return

    print(f"Found {len(files)} schematics:")
    print("-" * 40)
    
    for f in files:
        path = os.path.join(SCHEM_DIR, f)
        try:
            parser = SchematicParser(path)
            meta = parser.get_metadata()
            print(f"File: {f}")
            print(f"  Name: {meta.get('name')}")
            print(f"  Author: {meta.get('author')}")
            print(f"  Regions: {len(meta.get('regions', []))}")
            print("-" * 40)
        except Exception as e:
            print(f"File: {f} (Error reading metadata: {e})")
            print("-" * 40)

if __name__ == "__main__":
    list_schematics()


