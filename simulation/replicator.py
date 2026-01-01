import sys
import os
import time
import math

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.bridge import MinecraftBridge
from data_mining.parser import SchematicParser

# Rate limit configuration (Litematica defaults)
MAX_COMMANDS_PER_TICK = 64
TICK_INTERVAL = 0.05 # 20 TPS = 50ms

def replicate_schematic(schematic_path, origin=(0, 100, 0), rate_limit=MAX_COMMANDS_PER_TICK):
    """
    Robustly builds a schematic using Litematica-style logic.
    Wrapper around replicate_blocks.
    """
    print(f"Loading schematic: {schematic_path}")
    try:
        parser = SchematicParser(schematic_path)
        meta = parser.get_metadata()
        print(f"Metadata: {meta}")
        blocks = parser.parse_blocks()
        print(f"Found {len(blocks)} blocks to place.")
        bounds = parser.get_bounds()
    except Exception as e:
        print(f"Error loading schematic: {e}")
        return

    bridge = MinecraftBridge()
    try:
        bridge.connect()
    except Exception as e:
        print(f"Could not connect to server: {e}")
        return

    replicate_blocks(blocks, origin, bounds, bridge, rate_limit)
    bridge.disconnect()

def replicate_blocks(blocks, origin, bounds, bridge, rate_limit=MAX_COMMANDS_PER_TICK, use_updates=False, force_update_region=False):
    """
    Core building logic.
    blocks: List of (x, y, z, block_state, nbt)
    origin: (ox, oy, oz) absolute
    bounds: ((min_x, min_y, min_z), (max_x, max_y, max_z)) relative
    bridge: Connected MinecraftBridge instance
    use_updates: If True, enables block updates (fillUpdates true). Slower but allows physics.
    force_update_region: If True, calls mira_api update_region on the bounding box after building.
    """
    print("Connected. preparing to build...")
    
    ox, oy, oz = origin
    
    # 1. Disable Feedback & Freeze Time
    print("Disabling command feedback & Freezing time...")
    bridge.run_command("gamerule sendCommandFeedback false")
    bridge.run_command("tick freeze")
    
    # Enable fillUpdates based on flag
    fill_updates_val = "true" if use_updates else "false"
    bridge.run_command(f"carpet fillUpdates {fill_updates_val}")
    
    # 2. Clear Area (Set Air)
    (p_min_x, p_min_y, p_min_z), (p_max_x, p_max_y, p_max_z) = bounds
    
    # Calculate absolute bounds with buffer
    min_x = ox + p_min_x - 1
    min_y = oy + p_min_y - 1
    min_z = oz + p_min_z - 1
    
    max_x = ox + p_max_x 
    max_y = oy + p_max_y
    max_z = oz + p_max_z
    
    print(f"Schematic Bounds (Relative): {p_min_x},{p_min_y},{p_min_z} to {p_max_x},{p_max_y},{p_max_z}")
    print(f"Clearing Area (Absolute, Buffered): {min_x},{min_y},{min_z} to {max_x},{max_y},{max_z}")
    
    # Kill entities
    dx = max_x - min_x
    dy = max_y - min_y
    dz = max_z - min_z
    
    kill_cmd = f"kill @e[x={min_x},y={min_y},z={min_z},dx={dx},dy={dy},dz={dz},type=!player]"
    try:
        bridge.run_command(kill_cmd)
    except Exception as e:
        print(f"Warning clearing entities: {e}")

    # Set Air (using fill)
    volume = (dx + 1) * (dy + 1) * (dz + 1)
    if volume > 32768:
        print(f"Warning: Clear volume {volume} exceeds fill limit. Clearing chunk by chunk...")
        for cx in range(min_x, max_x + 1, 32):
             for cy in range(min_y, max_y + 1, 32):
                 for cz in range(min_z, max_z + 1, 32):
                     c_mx = min(cx + 31, max_x)
                     c_my = min(cy + 31, max_y)
                     c_mz = min(cz + 31, max_z)
                     bridge.run_command(f"fill {cx} {cy} {cz} {c_mx} {c_my} {c_mz} air")
                     time.sleep(0.05)
    else:
        bridge.run_command(f"fill {min_x} {min_y} {min_z} {max_x} {max_y} {max_z} air")
        
    time.sleep(1.0)
    
    # Sort Blocks (by Y, then X, then Z)
    # Sorting by Y ensures blocks are built from the bottom up, which is critical
    # for certain Minecraft block dependencies (like doors or tall plants).
    blocks.sort(key=lambda b: (b[1], b[0], b[2]))

    count = 0
    total = len(blocks)
    commands_sent = 0
    
    print(f"Starting build at {origin}...")
    
    for x, y, z, block_state, nbt_obj in blocks:
        abs_x = ox + x
        abs_y = oy + y
        abs_z = oz + z
        
        # --- Entity Handling ---
        if block_state.startswith("entity:"):
            entity_id = block_state.split(":", 1)[1]
            nbt_str = "{}"
            if nbt_obj:
                if hasattr(nbt_obj, 'copy'): nbt_copy = nbt_obj.copy()
                else: nbt_copy = dict(nbt_obj)
                
                # Strip conflicting NBT keys
                for key in ['Pos', 'UUID', 'OnGround', 'Dimension', 'PortalCooldown', 'id']:
                    if key in nbt_copy: del nbt_copy[key]
                
                if hasattr(nbt_obj, 'snbt'):
                     try: nbt_str = type(nbt_obj)(nbt_copy).snbt()
                     except: nbt_str = str(nbt_copy)
                else: nbt_str = str(nbt_copy)
            
            cmd = f"summon {entity_id} {abs_x} {abs_y} {abs_z} {nbt_str}"
            try: bridge.run_command(cmd)
            except Exception as e: print(f"Error summoning entity: {e}")
            count += 1
            continue
        
        # --- Block Handling ---
        final_nbt_str = None
        items_to_add = []
        
        if nbt_obj:
            if hasattr(nbt_obj, 'copy'): nbt_copy = nbt_obj.copy()
            else: nbt_copy = dict(nbt_obj)
            
            # Split items into separate commands to avoid RCON packet limits
            if 'Items' in nbt_copy:
                 items_list = nbt_copy['Items']
                 if len(items_list) > 0:
                     items_to_add = list(items_list)
                     del nbt_copy['Items']
                 
            try:
                if hasattr(nbt_obj, 'snbt'): final_nbt_str = type(nbt_obj)(nbt_copy).snbt()
                else: final_nbt_str = str(nbt_copy)
            except Exception as e:
                print(f"Error serializing NBT: {e}")
                final_nbt_str = str(nbt_copy)
        
        # Placement with retries
        max_retries = 3
        block_placed = False
        
        for attempt in range(max_retries):
            try:
                resp = bridge.set_block(abs_x, abs_y, abs_z, block_state, final_nbt_str)
                if resp and ("Incorrect" in resp or "Invalid" in resp or "Expected" in resp or "Unknown" in resp or "Error" in resp):
                    print(f"ERROR: Failed to place block at {abs_x},{abs_y},{abs_z}: {resp}")
                    if "Incorrect" in resp or "Invalid" in resp: break
                else:
                    block_placed = True
                break
            except Exception as e:
                print(f"Warning: Exception setting block (Attempt {attempt+1}/{max_retries}): {e}")
                is_network_error = "Broken pipe" in str(e) or "timeout" in str(e)
                if is_network_error:
                    print("Network error detected. Attempting to reconnect...")
                    try: bridge.disconnect()
                    except: pass
                    time.sleep(1 * (attempt + 1))
                    try: bridge.connect()
                    except: pass
                else:
                    time.sleep(0.1)

        # Container Inventory Handling
        if items_to_add and block_placed:
            for i, item in enumerate(items_to_add):
                # Standardize 'count' for Minecraft 1.21
                if 'Count' in item:
                    item['count'] = item['Count']
                    del item['Count']
                
                item_snbt = item.snbt() if hasattr(item, 'snbt') else str(item)
                cmd = f"data modify block {abs_x} {abs_y} {abs_z} Items append value {item_snbt}"
                try: 
                    bridge.run_command(cmd)
                except Exception as e: 
                    print(f"Failed to add item {i} to container: {e}")
                
                commands_sent += 1
                if commands_sent >= rate_limit:
                    time.sleep(TICK_INTERVAL)
                    commands_sent = 0
        
        count += 1
        commands_sent += 1
        
        if commands_sent >= rate_limit:
            time.sleep(TICK_INTERVAL)
            commands_sent = 0
            
        if count % 100 == 0:
            print(f"Progress: {count}/{total} blocks placed.")

    # 3. Post-Build Updates
    if force_update_region:
        print("Forcing update in schematic region...")
        u_min_x = ox + p_min_x
        u_min_y = oy + p_min_y
        u_min_z = oz + p_min_z
        
        u_max_x = ox + p_max_x - 1
        u_max_y = oy + p_max_y - 1
        u_max_z = oz + p_max_z - 1
        
        if u_max_x >= u_min_x and u_max_y >= u_min_y and u_max_z >= u_min_z:
            resp = bridge.run_command(f"mira_api update_region {u_min_x} {u_min_y} {u_min_z} {u_max_x} {u_max_y} {u_max_z}")
            print(f"Update Region Response: {resp}")

    # 4. Restore
    bridge.run_command("gamerule sendCommandFeedback true")
    bridge.run_command("carpet fillUpdates true") # Always restore to true
    bridge.run_command("tick unfreeze")
    print("Build complete.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python replicator.py <path> [x] [y] [z]")
        sys.exit(1)
    path = sys.argv[1]
    x, y, z = 0, 100, 0
    if len(sys.argv) >= 5:
        x = int(sys.argv[2])
        y = int(sys.argv[3])
        z = int(sys.argv[4])
    replicate_schematic(path, (x, y, z))
