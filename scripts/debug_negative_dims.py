import sys
import os
from litemapy import Schematic

def debug_negative_dims(path):
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    print(f"Loading {path}...")
    schem = Schematic.load(path)
    
    for name, region in schem.regions.items():
        print(f"Region: {name}")
        print(f"  Dimensions: {region.width}x{region.height}x{region.length}")
        
        # Probe indices
        print("\nProbing Indices:")
        
        valid_indices = []
        
        # Check X axis
        for x in range(-6, 6):
            try:
                # Try to access a block at x, 0, 0
                _ = region[x, 0, 0]
                print(f"  x={x} is VALID")
            except Exception as e:
                # print(f"  x={x} failed: {e}")
                pass

        # Check Z axis
        for z in range(-6, 6):
            try:
                _ = region[0, 0, z]
                print(f"  z={z} is VALID")
            except:
                pass

if __name__ == "__main__":
    debug_negative_dims("data/raw_schematics/3_wide_water_remover_.litematic")
