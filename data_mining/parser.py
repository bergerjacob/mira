"""
MIRA: Unified Schematic Parser
Provides a high-level API for extracting block data, NBT states, and entities 
from .litematic and .schem / .schematic files.
"""

import os
import re
from litemapy import Schematic, Region, BlockState
import mcschematic

class SchematicParser:
    def __init__(self, file_path):
        self.file_path = file_path
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Schematic file not found: {file_path}")
        
        self.is_litematic = file_path.lower().endswith(".litematic")
        if self.is_litematic:
            self.schem = Schematic.load(file_path)
        else:
            self.schem = mcschematic.MCSchematic(file_path)

    def get_metadata(self):
        if self.is_litematic:
            return {
                "name": self.schem.name,
                "author": self.schem.author,
                "description": self.schem.description,
                "regions": list(self.schem.regions.keys())
            }
        else:
            return {
                "name": os.path.splitext(os.path.basename(self.file_path))[0],
                "author": "Unknown",
                "description": "Imported schematic",
                "regions": ["main"]
            }

    def get_bounds(self):
        """
        Returns the bounding box of the entire schematic relative to the origin.
        Returns ((min_x, min_y, min_z), (max_x, max_y, max_z))
        """
        if not self.is_litematic:
            struct = self.schem.getStructure()
            block_states = struct.getBlockStates()
            if not block_states:
                return ((0,0,0), (0,0,0))
            coords = list(block_states.keys())
            min_x = min(c[0] for c in coords)
            min_y = min(c[1] for c in coords)
            min_z = min(c[2] for c in coords)
            max_x = max(c[0] for c in coords)
            max_y = max(c[1] for c in coords)
            max_z = max(c[2] for c in coords)
            return ((int(min_x), int(min_y), int(min_z)), (int(max_x), int(max_y), int(max_z)))

        min_x, min_y, min_z = float('inf'), float('inf'), float('inf')
        max_x, max_y, max_z = float('-inf'), float('-inf'), float('-inf')
        
        has_regions = False
        
        for region in self.schem.regions.values():
            has_regions = True
            rx, ry, rz = region.x, region.y, region.z
            w, h, l = region.width, region.height, region.length
            
            # Calculate absolute bounds of this region (relative to schem origin)
            # Handle negative dimensions
            x1 = rx + min(0, w)
            y1 = ry + min(0, h)
            z1 = rz + min(0, l)
            
            x2 = rx + max(0, w)
            y2 = ry + max(0, h)
            z2 = rz + max(0, l)
            
            # Update global bounds
            if x1 < min_x: min_x = x1
            if y1 < min_y: min_y = y1
            if z1 < min_z: min_z = z1
            
            if x2 > max_x: max_x = x2
            if y2 > max_y: max_y = y2
            if z2 > max_z: max_z = z2
            
        if not has_regions:
            return ((0,0,0), (0,0,0))
            
        return ((int(min_x), int(min_y), int(min_z)), (int(max_x), int(max_y), int(max_z)))

    def parse_blocks(self):
        """
        Yields tuples of (x, y, z, block_state_string, nbt)
        Coordinates are relative to the schematic origin.
        """
        if not self.is_litematic:
            blocks = []
            struct = self.schem.getStructure()
            block_states = struct.getBlockStates()
            entities = struct.getBlockEntities()
            
            for (x, y, z) in block_states.keys():
                block_state_str = self.schem.getBlockStateAt((x, y, z))
                if block_state_str == "minecraft:air":
                    continue
                
                # Check for block entity NBT
                raw_ent = entities.get((x, y, z))
                nbt_data = None
                if raw_ent and isinstance(raw_ent, str):
                    # raw_ent is like 'minecraft:chest[facing=north]{Items: [{...}]}'
                    # Extract the NBT string inside { }
                    match = re.match(r'^([^{]+)(\{.+\})$', raw_ent)
                    if match:
                        nbt_data = match.group(2)
                
                blocks.append((x, y, z, block_state_str, nbt_data))
            return blocks

        blocks = []
        # Litematica can have multiple sub-regions.
        # We iterate through all of them.
        for region_name, region in self.schem.regions.items():
            # Calculate absolute offsets if regions have them (simplified for now)
            # region.x, region.y, region.z are offsets relative to the schematic origin
            
            rx, ry, rz = region.x, region.y, region.z
            
            # Handle Entities
            if hasattr(region, 'entities') and region.entities:
                for entity in region.entities:
                    try:
                        e_id = None
                        e_pos = None
                        e_nbt = None
                        
                        if hasattr(entity, 'id'):
                            e_id = entity.id
                        
                        if hasattr(entity, 'position'):
                            e_pos = entity.position
                            
                        if hasattr(entity, 'data'):
                            e_nbt = entity.data
                        
                        if not e_id and hasattr(entity, 'get'):
                             e_id = entity.get('id')
                        
                        if e_id and e_pos and len(e_pos) >= 3:
                            ex = float(e_pos[0]) + rx
                            ey = float(e_pos[1]) + ry
                            ez = float(e_pos[2]) + rz
                            
                            blocks.append((ex, ey, ez, f"entity:{e_id}", e_nbt))
                            
                    except Exception as e:
                        print(f"Error parsing entity: {e}")

            # Build Tile Entity Lookup
            te_lookup = {}
            if hasattr(region, 'tile_entities') and region.tile_entities:
                for te in region.tile_entities:
                    try:
                        if hasattr(te, 'position') and hasattr(te, 'data'):
                            pos = tuple(te.position)
                            te_lookup[pos] = te.data
                        elif isinstance(te, dict):
                            t_x = int(te.get('x', 0))
                            t_y = int(te.get('y', 0))
                            t_z = int(te.get('z', 0))
                            te_lookup[(t_x, t_y, t_z)] = te
                    except Exception:
                        pass
            
            x_range = range(region.width) if region.width > 0 else range(0, region.width, -1)
            y_range = range(region.height) if region.height > 0 else range(0, region.height, -1)
            z_range = range(region.length) if region.length > 0 else range(0, region.length, -1)
            
            for x in x_range:
                for y in y_range:
                    for z in z_range:
                        try:
                            block = region[x, y, z]
                        except IndexError:
                            continue
                            
                        if block.id != "minecraft:air":
                            block_str = block.id
                            
                            try:
                                props_dict = {}
                                if callable(block.properties):
                                    props_items = block.properties()
                                    props_dict = dict(props_items)
                                
                                if props_dict:
                                    props = ",".join([f"{k}={v}" for k, v in sorted(props_dict.items())])
                                    block_str = f"{block_str}[{props}]"
                            except Exception as e:
                                print(f"Error parsing properties for {block}: {e}")
                                pass
                            
                            nbt_data = None
                            
                            candidates = [
                                (x, y, z),
                                (x + region.x, y + region.y, z + region.z)
                            ]
                            
                            for cx, cy, cz in candidates:
                                if (cx, cy, cz) in te_lookup:
                                    raw_nbt = te_lookup[(cx, cy, cz)]
                                    
                                    if hasattr(raw_nbt, 'keys'):
                                        for k in ['x', 'y', 'z']:
                                            if k in raw_nbt:
                                                del raw_nbt[k]
                                    
                                    nbt_data = raw_nbt
                                    break
                            
                            blocks.append((rx + x, ry + y, rz + z, block_str, nbt_data))
        return blocks

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        parser = SchematicParser(path)
        print(f"Metadata: {parser.get_metadata()}")
        print(f"Found {len(parser.parse_blocks())} blocks.")
    else:
        print("Usage: python parser.py <path_to_schematic>")


