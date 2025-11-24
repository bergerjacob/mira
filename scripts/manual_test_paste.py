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
    Robustly builds a schematic using Litematica-style logic:
    1. chunk sorting/loading (simulated)
    2. rate limiting
    3. feedback suppression
    """
    print(f"Loading schematic: {schematic_path}")
    try:
        parser = SchematicParser(schematic_path)
        meta = parser.get_metadata()
        print(f"Metadata: {meta}")
        blocks = parser.parse_blocks()
        print(f"Found {len(blocks)} blocks to place.")
    except Exception as e:
        print(f"Error loading schematic: {e}")
        return

    bridge = MinecraftBridge()
    try:
        bridge.connect()
    except Exception as e:
        print(f"Could not connect to server: {e}")
        return

    print("Connected. preparing to build...")
    
    ox, oy, oz = origin
    
    # 1. Disable Feedback & Freeze Time
    print("Disabling command feedback & Freezing time...")
    bridge.run_command("gamerule sendCommandFeedback false")
    bridge.run_command("tick freeze")
    
    # Enable fillUpdates false (permanent setting request)
    bridge.run_command("carpet fillUpdates false")
    
    # 2. Clear Area (Set Air)
    # Get accurate schematic dimensions from parser
    (p_min_x, p_min_y, p_min_z), (p_max_x, p_max_y, p_max_z) = parser.get_bounds()
    
    # These are relative to origin.
    # Note: p_max is exclusive upper bound from region math (start + size).
    # We want inclusive coordinates for the fill command.
    # So we use p_max - 1 for the coordinate of the last block.
    
    # Calculate absolute bounds with buffer
    min_x = ox + p_min_x - 1
    min_y = oy + p_min_y - 1
    min_z = oz + p_min_z - 1
    
    max_x = ox + p_max_x # (p_max is exclusive, so p_max - 1 + 1 buffer = p_max)
    max_y = oy + p_max_y
    max_z = oz + p_max_z
    
    print(f"Schematic Bounds (Relative): {p_min_x},{p_min_y},{p_min_z} to {p_max_x},{p_max_y},{p_max_z}")
    print(f"Clearing Area (Absolute, Buffered): {min_x},{min_y},{min_z} to {max_x},{max_y},{max_z}")
    
    # Kill entities in the area first
    # Command: /kill @e[x=...,y=...,z=...,dx=...,dy=...,dz=...]
    # dx, dy, dz are distances from x,y,z.
    dx = max_x - min_x
    dy = max_y - min_y
    dz = max_z - min_z
    
    # Careful with Kill All - ensure it's strictly bounded
    kill_cmd = f"kill @e[x={min_x},y={min_y},z={min_z},dx={dx},dy={dy},dz={dz},type=!player]"
    try:
        bridge.run_command(kill_cmd)
    except Exception as e:
        print(f"Warning clearing entities: {e}")

    # Set Air (using fill)
    # Volume check
    volume = (dx + 1) * (dy + 1) * (dz + 1)
    if volume > 32768:
        print(f"Warning: Clear volume {volume} exceeds fill limit. Clearing chunk by chunk...")
        # Simple chunking loop
        for cx in range(min_x, max_x + 1, 32):
             for cy in range(min_y, max_y + 1, 32):
                 for cz in range(min_z, max_z + 1, 32):
                     c_mx = min(cx + 31, max_x)
                     c_my = min(cy + 31, max_y)
                     c_mz = min(cz + 31, max_z)
                     bridge.run_command(f"fill {cx} {cy} {cz} {c_mx} {c_my} {c_mz} air")
                     time.sleep(0.05) # Brief pause
    else:
        bridge.run_command(f"fill {min_x} {min_y} {min_z} {max_x} {max_y} {max_z} air")
        
    # Wait for air to settle (user request)
    time.sleep(1.0)
    
    # 3. Sort Blocks (by Y, then X/Z for chunks)
    # Litematica sorts by chunks. Here we just sort by Y to build up.
    # Grouping by chunk would be better for loading check, but for now global sort is fine for small builds.
    blocks.sort(key=lambda b: (b[1], b[0], b[2])) # Y, X, Z

    count = 0
    total = len(blocks)
    
    # Rate limiting state
    commands_sent = 0
    start_time = time.time()
    
    print(f"Starting build at {origin}...")
    
    for x, y, z, block_state, nbt_obj in blocks:
        # Calculate absolute position
        abs_x = ox + x
        abs_y = oy + y
        abs_z = oz + z
        
        # Check if it's an entity or block
        if block_state.startswith("entity:"):
            # Handle Entity Summon
            entity_id = block_state.split(":", 1)[1] # remove "entity:" prefix
            # nbt_obj is the entity NBT
            
            # Prepare Entity NBT
            # Remove Pos, UUID, etc to avoid conflicts
            nbt_str = "{}"
            if nbt_obj:
                # Ensure it's a dict/compound copy
                if hasattr(nbt_obj, 'copy'):
                    nbt_copy = nbt_obj.copy()
                else:
                    nbt_copy = dict(nbt_obj)
                
                # Remove conflict keys
                for key in ['Pos', 'UUID', 'OnGround', 'Dimension', 'PortalCooldown', 'id']:
                    if key in nbt_copy:
                        del nbt_copy[key]
                
                if hasattr(nbt_obj, 'snbt'):
                     # Try to preserve type info if possible, but nbt_copy might be dict if nbt_obj was compound
                     # We need to cast back if nbt_obj was Compound
                     try:
                         nbt_str = type(nbt_obj)(nbt_copy).snbt()
                     except:
                         nbt_str = str(nbt_copy)
                else:
                     nbt_str = str(nbt_copy)
            
            # Use /summon
            # Note: Python floats might need formatting? Minecraft handles them fine usually.
            cmd = f"summon {entity_id} {abs_x} {abs_y} {abs_z} {nbt_str}"
            
            try:
                bridge.run_command(cmd)
            except Exception as e:
                print(f"Error summoning entity {entity_id} at {abs_x},{abs_y},{abs_z}: {e}")
                
            count += 1
            continue # Skip block placement logic
        
        # Prepare NBT
        final_nbt_str = None
        items_to_add = []
        
        if nbt_obj:
            # Create a shallow copy to avoid modifying the original parsed data
            # nbtlib.Compound supports .copy() (it inherits from dict or UserDict usually)
            if hasattr(nbt_obj, 'copy'):
                nbt_copy = nbt_obj.copy()
            else:
                nbt_copy = dict(nbt_obj) # Fallback
            
            # Check if we need to split (if it has Items list)
            if 'Items' in nbt_copy:
                 items_list = nbt_copy['Items']
                 if len(items_list) > 0:
                     items_to_add = list(items_list) # Copy content
                     del nbt_copy['Items'] # Remove from base to reduce size
                 
            # Serialize the base NBT
            # If nbt_copy is a dict (from .copy() or dict()), it lacks .snbt()
            # We try to cast it back to the original NBT type (nbtlib.Compound) to use .snbt()
            try:
                if hasattr(nbt_obj, 'snbt'):
                    final_nbt_str = type(nbt_obj)(nbt_copy).snbt()
                else:
                    final_nbt_str = str(nbt_copy)
            except Exception as e:
                print(f"Error serializing NBT: {e}")
                final_nbt_str = str(nbt_copy)
        
        # DEBUG: Log every 50th block or if it has NBT
        # if nbt_obj or count % 50 == 0:
        #    print(f"DEBUG: Placing {block_state} at {abs_x},{abs_y},{abs_z} | NBT: {bool(nbt_obj)}")
        
        # Send command with retries
        max_retries = 3
        block_placed = False
        
        for attempt in range(max_retries):
            try:
                resp = bridge.set_block(abs_x, abs_y, abs_z, block_state, final_nbt_str)
                # Check response for Minecraft errors
                if resp and ("Incorrect" in resp or "Invalid" in resp or "Expected" in resp or "Unknown" in resp or "Error" in resp):
                    print(f"ERROR placing block at {abs_x},{abs_y},{abs_z}: {resp}")
                    # If it's a syntax error, retrying won't help.
                    if "Incorrect" in resp or "Invalid" in resp:
                         break
                else:
                    block_placed = True
                    # print(f"DEBUG: Success placing {block_state}")
                
                break
            except Exception as e:
                # Handle connection timeout or packet size issues
                print(f"Warning: Exception setting block at {abs_x},{abs_y},{abs_z} (Attempt {attempt+1}/{max_retries}): {e}")
                
                # Check for network errors
                is_network_error = "Broken pipe" in str(e) or "timeout" in str(e) or "Connection refused" in str(e)
                
                if is_network_error:
                    print("Network error detected. Reconnecting...")
                    try:
                        bridge.disconnect()
                    except:
                        pass
                    
                    # Backoff
                    time.sleep(1 * (attempt + 1))
                    
                    try:
                        bridge.connect()
                    except Exception as conn_err:
                        print(f"Reconnect failed: {conn_err}")
                else:
                    time.sleep(0.1)

            if attempt == max_retries - 1:
                print(f"FAILED to place block at {abs_x},{abs_y},{abs_z} after {max_retries} attempts.")
        
        # Add Items separately (if any)
        if items_to_add:
            if not block_placed:
                print(f"Skipping items for {abs_x},{abs_y},{abs_z} because block wasn't placed.")
            else:
                print(f"Adding {len(items_to_add)} items to container at {abs_x},{abs_y},{abs_z}...")
                for i, item in enumerate(items_to_add):
                    # Ensure item is an nbtlib object to preserve types (Byte vs Int)
                    # item is already from nbtlib.List so it should be fine
                    
                    # FIX: Minecraft 1.21 container items use lowercase 'count' or require it to match internal format.
                    # Our tests showed that sending 'count' (lowercase) with either Byte or Int works.
                    # 'Count' (uppercase) resulted in 1.
                    if 'Count' in item:
                        item['count'] = item['Count']
                        del item['Count']
                    
                    item_snbt = item.snbt() if hasattr(item, 'snbt') else str(item)
                    # Command: data modify block <x> <y> <z> Items append value <item>
                    cmd = f"data modify block {abs_x} {abs_y} {abs_z} Items append value {item_snbt}"
                    
                    # DEBUG: Print exact command
                    # print(f"DEBUG_CMD: {cmd}")
                    
                    try:
                        bridge.run_command(cmd)
                        # resp = bridge.run_command(cmd)
                        # if resp and ("Error" in resp or "No such" in resp or "Incorrect" in resp):
                        #      print(f"ERROR adding item {i} to {abs_x},{abs_y},{abs_z}: {resp}")
                    except Exception as e:
                         print(f"Failed to add item {i} to {abs_x},{abs_y},{abs_z}: {e}")
                    
                    commands_sent += 1
                    if commands_sent >= rate_limit:
                        time.sleep(TICK_INTERVAL)
                        commands_sent = 0

                # Verify NBT after placement
                # Only check the first container to avoid spamming logs, or check specific ones
                # if count % 50 == 0:
                #      print(f"Verifying NBT at {abs_x},{abs_y},{abs_z}...")
                #      check_resp = bridge.run_command(f"data get block {abs_x} {abs_y} {abs_z} Items")
                #      print(f"VERIFY: {check_resp}")
        
        count += 1
        commands_sent += 1
        
        # Rate Limiting
        if commands_sent >= rate_limit:
            # Sleep to simulate tick wait
            # In real mod it waits for next tick. Here we sleep 50ms.
            time.sleep(TICK_INTERVAL)
            commands_sent = 0
            
        if count % 100 == 0:
            print(f"Progress: {count}/{total} blocks placed.")

    # 3. Re-enable Feedback & Unfreeze
    bridge.run_command("gamerule sendCommandFeedback true")
    bridge.run_command("tick freeze") # Toggles freeze off if already frozen, or use 'tick unfreeze' if available?
    # Standard carpet/vanilla 1.21: 'tick freeze' toggles or 'tick unfreeze' exists?
    # Vanilla 1.21 has /tick freeze [start|stop] ?? No, Vanilla is /tick freeze.
    # Wait, Vanilla 1.21 added /tick command.
    # Syntax: /tick freeze <true|false> ?
    # Let's use /tick unfreeze if it exists, or check syntax.
    # Actually, 'tick freeze' usually toggles. But let's be safe.
    # Vanilla: /tick freeze -> freezes. /tick unfreeze -> unfreezes.
    
    # Unfreeze ticks
    bridge.run_command("tick unfreeze")
    
    print("Build complete.")
    bridge.run_command(f"say Build of {meta.get('name', 'Unknown')} complete at {origin}")
    bridge.disconnect()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python manual_test_paste.py <path_to_litematic> [x] [y] [z]")
        sys.exit(1)
        
    path = sys.argv[1]
    
    x, y, z = 0, 100, 0
    if len(sys.argv) >= 5:
        x = int(sys.argv[2])
        y = int(sys.argv[3])
        z = int(sys.argv[4])
        
    replicate_schematic(path, (x, y, z))
