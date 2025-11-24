import os
import shutil
import json
from litemapy import Schematic
import nbtlib

class SchematicConverter:
    @staticmethod
    def litematic_to_sponge_schem(litematic_path, output_path):
        """Converts to Sponge .schem (WorldEdit)"""
        if not os.path.exists(litematic_path):
            raise FileNotFoundError(f"Source file not found: {litematic_path}")
        schem = Schematic.load(litematic_path)
        regions = list(schem.regions.values())
        if not regions:
             raise ValueError("No regions found.")
        region = regions[0]
        sponge_nbt = region.to_sponge_nbt()
        file = nbtlib.File(sponge_nbt)
        file.save(output_path, gzipped=True)
        return output_path

    @staticmethod
    def litematic_to_vanilla_structure(litematic_path, output_path):
        """Converts to Vanilla .nbt Structure format"""
        if not os.path.exists(litematic_path):
            raise FileNotFoundError(f"Source file not found: {litematic_path}")
        
        schem = Schematic.load(litematic_path)
        regions = list(schem.regions.values())
        if not regions:
             raise ValueError("No regions found.")
        
        # Vanilla structures generally support one region. 
        # If multiple, we might need multiple files or merge. 
        # For MIRA, we assume module-based schematics (single region).
        region = regions[0]
        
        # Use litemapy's native conversion
        structure_nbt = region.to_structure_nbt()
        
        # Patch DataVersion for 1.21 (3953) to ensure visibility
        if "DataVersion" in structure_nbt:
            structure_nbt["DataVersion"] = nbtlib.Int(3953)
        
        # Save
        file = nbtlib.File(structure_nbt)
        file.save(output_path, gzipped=True)
        return output_path

def setup_datapack(server_path, namespace="mira"):
    """
    Creates/Updates a datapack in the server to host these structures.
    Returns the structure directory path.
    """
    world_dir = os.path.join(server_path, "world") # Assuming 'world' is level-name
    if not os.path.exists(world_dir):
        # Maybe server hasn't run yet or name diff
        # Check server.properties? For now assume 'world'
        pass

    datapacks_dir = os.path.join(world_dir, "datapacks")
    pack_dir = os.path.join(datapacks_dir, "mira_structures")
    
    # Structure path: data/<namespace>/structures/
    struct_dir = os.path.join(pack_dir, "data", namespace, "structures")
    os.makedirs(struct_dir, exist_ok=True)
    
    # pack.mcmeta
    mcmeta = {
        "pack": {
            "pack_format": 48, # 1.21 format
            "description": "MIRA Generated Structures"
        }
    }
    
    with open(os.path.join(pack_dir, "pack.mcmeta"), "w") as f:
        json.dump(mcmeta, f, indent=2)
        
    return struct_dir
