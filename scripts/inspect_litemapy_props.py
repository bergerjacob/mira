import sys
import os
from litemapy import Schematic

TEST_FILE = "data/raw_schematics/simple_lamp.litematic"

def inspect_properties():
    if not os.path.exists(TEST_FILE):
        print(f"File not found: {TEST_FILE}")
        return

    schem = Schematic.load(TEST_FILE)
    if not schem.regions:
        print("No regions found.")
        return
        
    region = list(schem.regions.values())[0]
    
    print(f"Region size: {region.width}x{region.height}x{region.length}")
    
    for x in range(region.width):
        for y in range(region.height):
            for z in range(region.length):
                block = region[x, y, z]
                if block.id != "minecraft:air" and block.id != "minecraft:stone":
                    print(f"Block at {x},{y},{z}: {block.id}")
                    print(f"  Properties: {block.properties()}")

if __name__ == "__main__":
    inspect_properties()
