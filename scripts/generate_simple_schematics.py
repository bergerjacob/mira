import os
import sys
from litemapy import Schematic, Region, BlockState
import random

# Ensure output directory exists
OUTPUT_DIR = "data/raw_schematics"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def create_simple_lamp():
    """
    Creates a simple circuit: Lever -> Redstone Wire -> Redstone Lamp
    Size: 3x2x1
    """
    name = "Simple_Lamp_Test"
    print(f"Generating {name}...")
    
    reg = Region(0, 0, 0, 3, 2, 1) # x, y, z dimensions
    schem = Schematic(name=name, author="MIRA_Generator", regions={name: reg})
    
    # Floor (Stone)
    stone = BlockState("minecraft:stone")
    for x in range(3):
        reg[x, 0, 0] = stone
        
    # Components on top (y=1)
    # 0: Lever
    lever = BlockState("minecraft:lever")
    # Using name mangling to set private property
    lever._BlockState__properties = {"face": "floor", "facing": "east", "powered": "false"}
    reg[0, 1, 0] = lever
    
    # 1: Redstone Wire
    wire = BlockState("minecraft:redstone_wire")
    wire._BlockState__properties = {"power": "0", "east": "side", "west": "side"}
    reg[1, 1, 0] = wire
    
    # 2: Redstone Lamp
    lamp = BlockState("minecraft:redstone_lamp")
    lamp._BlockState__properties = {"lit": "false"}
    reg[2, 1, 0] = lamp
    
    filename = os.path.join(OUTPUT_DIR, "simple_lamp.litematic")
    schem.save(filename)
    print(f"Saved to {filename}")

def create_hopper_chain():
    """
    Creates a simple item transfer: Chest -> Hopper -> Chest
    Size: 1x3x1
    """
    name = "Hopper_Drop_Test"
    print(f"Generating {name}...")
    
    reg = Region(0, 0, 0, 1, 3, 1)
    schem = Schematic(name=name, author="MIRA_Generator", regions={name: reg})
    
    # y=0: Bottom Chest
    chest = BlockState("minecraft:chest")
    chest._BlockState__properties = {"facing": "west"}
    reg[0, 0, 0] = chest
    
    # y=1: Hopper (pointing down)
    hopper = BlockState("minecraft:hopper")
    hopper._BlockState__properties = {"facing": "down", "enabled": "true"}
    reg[0, 1, 0] = hopper
    
    # y=2: Top Chest
    # Use copy or new instance
    chest_top = BlockState("minecraft:chest")
    chest_top._BlockState__properties = {"facing": "west"}
    reg[0, 2, 0] = chest_top
    
    filename = os.path.join(OUTPUT_DIR, "hopper_drop.litematic")
    schem.save(filename)
    print(f"Saved to {filename}")

if __name__ == "__main__":
    create_simple_lamp()
    create_hopper_chain()
