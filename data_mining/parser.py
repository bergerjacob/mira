"""
MIRA: Litematic Schematic Parser
Provides a high-level API for extracting block data, NBT states, and entities 
from .litematic files.
"""

import os
from litemapy import Schematic, Region, BlockState

class SchematicParser:
    def __init__(self, file_path):
        self.file_path = file_path
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Schematic file not found: {file_path}")
        self.schem = Schematic.load(file_path)

    def get_metadata(self):
        return {
            "name": self.schem.name,
            "author": self.schem.author,
            "description": self.schem.description,
            "regions": list(self.schem.regions.keys())
        }

    def get_bounds(self):
        """
        Returns the bounding box of the entire schematic relative to the origin.
        Returns ((min_x, min_y, min_z), (max_x, max_y, max_z))
        """
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
            
        # Convert to int (Litematica uses ints for coords)
        # Note: Max bounds are exclusive in terms of volume (start + size), 
        # but for coordinates we often want inclusive max index.
        # x2 calculated above is exclusive limit (start + size).
        # e.g. x=0, w=1 -> x1=0, x2=1. Block is at 0.
        # So x2 is the exclusive upper bound.
        
        return ((int(min_x), int(min_y), int(min_z)), (int(max_x), int(max_y), int(max_z)))

    def parse_blocks(self):
        """
        Yields tuples of (x, y, z, block_state_string)
        Coordinates are relative to the schematic origin.
        """
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
                    # Inspect entity structure
                    # litemapy.minecraft.Entity has attributes like .id, .position, .data (nbt)
                    try:
                        e_id = None
                        e_pos = None
                        e_nbt = None
                        
                        # Check attributes first (it's an object, not a dict)
                        if hasattr(entity, 'id'):
                            e_id = entity.id
                        
                        if hasattr(entity, 'position'):
                            # .position is usually a list or tuple [x, y, z]
                            e_pos = entity.position
                            
                        if hasattr(entity, 'data'):
                            e_nbt = entity.data
                        
                        # Fallback for dict-like access if attributes failed (unlikely given inspection)
                        if not e_id and hasattr(entity, 'get'):
                             e_id = entity.get('id')
                        
                        if e_id and e_pos and len(e_pos) >= 3:
                            # Calculate relative position
                            # litemapy entity.position seems to be relative to the region origin in newer versions?
                            # Or maybe it's absolute?
                            # Let's assume it follows the same logic as blocks (relative to region anchor)
                            # But litematica usually stores entities with "Pos" which is absolute in world...
                            # Wait, if I save a schematic, entities are saved with offsets relative to the region origin.
                            
                            # e_pos is likely relative to the region min corner.
                            # We need to add the placement origin (rx, ry, rz are offsets of the region within the schematic)
                            # But we want to output coordinates relative to the schematic origin (0,0,0).
                            
                            # rx, ry, rz are the region's offset from the schematic origin.
                            ex = float(e_pos[0]) + rx
                            ey = float(e_pos[1]) + ry
                            ez = float(e_pos[2]) + rz
                            
                            # Construct entity tuple: (x, y, z, "entity:id", nbt_obj)
                            blocks.append((ex, ey, ez, f"entity:{e_id}", e_nbt))
                            
                    except Exception as e:
                        print(f"Error parsing entity: {e}")

            # Build Tile Entity Lookup
            te_lookup = {}
            # Litemapy Region.tile_entities is a list of litemapy.minecraft.TileEntity objects
            if hasattr(region, 'tile_entities') and region.tile_entities:
                for te in region.tile_entities:
                    try:
                        # te.position is a tuple (x, y, z) in newer litemapy
                        if hasattr(te, 'position') and hasattr(te, 'data'):
                            pos = tuple(te.position)
                            te_lookup[pos] = te.data
                        elif isinstance(te, dict): # Fallback for raw dicts
                            t_x = int(te.get('x', 0))
                            t_y = int(te.get('y', 0))
                            t_z = int(te.get('z', 0))
                            te_lookup[(t_x, t_y, t_z)] = te
                    except Exception:
                        pass

            # Iterate over region blocks
            # Litematica regions can have negative dimensions (e.g. width = -4)
            # litemapy iteration logic seems to require probing valid indices based on debug script.
            # Valid indices are range(min_idx, max_idx) where min can be negative.
            # Specifically, for a dimension -N, valid indices are -N, -(N-1), ..., -1 (no, wait)
            # Debug script showed: for -4 width, valid indices are -6 to 0?
            # No, wait.
            # Debug script output:
            # Dimensions: -4x6x-3
            # x=-4 is VALID, x=-3 is VALID, x=-2 is VALID, x=-1 is VALID, x=0 is VALID
            # (also -5, -6 valid? Maybe region access wraps or allows OOB?)
            # But crucially, positive x=1 was INVALID (index out of bounds).
            
            # This suggests we should iterate:
            # If dimension is positive (W): 0 to W-1
            # If dimension is negative (-W): -W to 0 ? Or -(W-1) to 0?
            # Or maybe simply from min(0, dim) to max(0, dim)?
            # Let's try iterating range(0, dim, -1) if dim < 0.
            
            x_range = range(region.width) if region.width > 0 else range(0, region.width, -1)
            # Wait, range(0, -4, -1) -> 0, -1, -2, -3. This matches size 4.
            
            y_range = range(region.height) if region.height > 0 else range(0, region.height, -1)
            z_range = range(region.length) if region.length > 0 else range(0, region.length, -1)
            
            for x in x_range:
                for y in y_range:
                    for z in z_range:
                        # Use array syntax as getblock is deprecated
                        try:
                            block = region[x, y, z]
                        except IndexError:
                            # Should not happen with correct range
                            continue
                            
                        if block.id != "minecraft:air":
                            # Construct the full block state string
                            block_str = block.id
                            
                            # Handle Properties
                            # block.properties is a method returning dict_items
                            try:
                                props_dict = {}
                                if callable(block.properties):
                                    props_items = block.properties()
                                    props_dict = dict(props_items)
                                
                                if props_dict:
                                    # Sort keys for consistent testing
                                    props = ",".join([f"{k}={v}" for k, v in sorted(props_dict.items())])
                                    block_str = f"{block_str}[{props}]"
                            except Exception as e:
                                print(f"Error parsing properties for {block}: {e}")
                                pass
                            
                            # Handle NBT (Tile Entities)
                            # Calculate absolute coordinate of this block
                            # Litematica TEs are usually stored with coords relative to the region anchor?
                            # Or absolute?
                            # If absolute, we need region.min_x etc.
                            # If we don't know, we might miss them.
                            # Let's assume they match the (x,y,z) used in getblock() if they are normalized.
                            # Or maybe (x + region.x, ...)?
                            
                            # Let's try to find a matching TE.
                            # We check multiple offsets just in case.
                            nbt_data = None
                            
                            # Attempt 1: Direct match (if normalized)
                            candidates = [
                                (x, y, z),
                                (x + region.x, y + region.y, z + region.z)
                            ]
                            
                            for cx, cy, cz in candidates:
                                if (cx, cy, cz) in te_lookup:
                                    # Found it!
                                    raw_nbt = te_lookup[(cx, cy, cz)]
                                    
                                    # Strip coordinate keys if present
                                    if hasattr(raw_nbt, 'keys'):
                                        for k in ['x', 'y', 'z']:
                                            if k in raw_nbt:
                                                del raw_nbt[k]
                                    
                                    # Return the raw NBT object (nbtlib.Compound or dict)
                                    # We let the builder handle serialization and splitting
                                    nbt_data = raw_nbt
                                    break
                            
                            # Return block with NBT object if present
                            blocks.append((rx + x, ry + y, rz + z, block_str, nbt_data))
        return blocks

if __name__ == "__main__":
    # Test run
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        parser = SchematicParser(path)
        print(f"Metadata: {parser.get_metadata()}")
        print(f"Found {len(parser.parse_blocks())} blocks.")
    else:
        print("Usage: python parser.py <path_to_litematic>")

