import sys
import os
from litemapy import Schematic

TEST_FILE = "data/raw_schematics/12gt_Dispenser_Factory_Protected.litematic"

def inspect_region_te():
    schem = Schematic.load(TEST_FILE)
    region = list(schem.regions.values())[0]
    
    if len(region.tile_entities) > 0:
        te = region.tile_entities[0]
        # Check `position` attribute (from previous dir output)
        print(f"TE Position: {te.position}")
        # Check `data` attribute
        print(f"TE Data: {te.data}")
        print(f"TE Data Type: {type(te.data)}")

if __name__ == "__main__":
    inspect_region_te()
