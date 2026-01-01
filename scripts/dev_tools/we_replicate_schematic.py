"""
MIRA: WorldEdit Replication Utility
Alternative replication method using WorldEdit schematics and 
a bot-based Scarpet wrapper.
"""

import sys
import os
import time
import shutil

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulation.bridge import MinecraftBridge
from data_mining.converter import SchematicConverter

SERVER_SCHEM_DIR = os.path.abspath("simulation/server/config/worldedit/schematics")

def run_manual_test(litematic_path, origin=(0, 100, 0)):
    filename = os.path.basename(litematic_path)
    schem_name = os.path.splitext(filename)[0]
    target_schem_path = os.path.join(SERVER_SCHEM_DIR, f"{schem_name}.schem")

    print(f"1. Converting {litematic_path} -> {target_schem_path}...")
    try:
        SchematicConverter.litematic_to_sponge_schem(litematic_path, target_schem_path)
    except Exception as e:
        print(f"Conversion failed: {e}")
        return

    print("2. Connecting to Server...")
    bridge = MinecraftBridge()
    try:
        bridge.connect()
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # Spawn bot
    bot_name = "MIRA_Bot"
    print(f"3. Spawning Bot '{bot_name}'...")
    
    # Check if bot exists, if not spawn
    # We can't easily check via RCON without parsing /list, so we just try to spawn and ignore error
    bridge.run_command(f"player {bot_name} spawn at 0 200 0")
    time.sleep(1) # Wait for spawn
    bridge.run_command(f"gamemode creative {bot_name}")
    bridge.run_command(f"op {bot_name}")

    print(f"4. Loading and Pasting Schematic at {origin}...")
    x, y, z = origin
    
    # Teleport bot to target
    bridge.run_command(f"tp {bot_name} {x} {y} {z}")
    time.sleep(0.5)
    
    # Execute WE commands as bot
    # Note: Use full command names without double slashes for /execute compatibility if // fails
    # Also sometimes we need to use 'worldedit:schematic'
    
    print(f"Attempting load of {schem_name}...")
    # Try different syntaxes if one fails? 
    # Standard 1.21 syntax: execute as <player> run <command>
    # Command should be /schematic or /worldedit:schematic
    
    resp = bridge.run_command(f"execute as {bot_name} run schematic load {schem_name}")
    print(f"Load response: {resp}")
    
    if "Unknown command" in resp or "Incorrect argument" in resp:
         print("Retrying with /worldedit:schematic...")
         resp = bridge.run_command(f"execute as {bot_name} run worldedit:schematic load {schem_name}")
         print(f"Load response 2: {resp}")

    # Try using Carpet's 'player ... mount' to force a context if needed, but 'execute as' should work.
    # The issue might be that WorldEdit commands via /execute need the player to be physically present and loaded?
    # Bot is spawned.
    
    # Alternative: Use Carpet's /player command to execute the chat directly as if the player typed it.
    # Syntax: /player <name> command <command>
    # Note: 'command' subcommand might not exist in all carpet versions, checking 'mount', 'jump' etc.
    # Actually, standard carpet has 'player <name> command ...' in newer versions?
    # Or 'player ... mount' etc.
    # Let's try sending it via general 'execute as' but using the ALIAS that works.
    # If /paste fails, it might be purely client-side mod issue? No, WE is server side.
    
    # Debug: try to set a block near the bot to verify it can execute commands.
    bridge.run_command(f"execute as {bot_name} run setblock ~ ~2 ~ stone")
    
    # Try the 'player ... command' syntax if available (Carpet feature)
    # If not, we might be stuck with the fact that WE 7+ on Fabric registers commands in a way /execute dislikes.
    # Workaround: Use the 'perf' command from WE? No.
    
    # Let's try: /execute as MIRA_Bot run /paste (with slash).
    # RCON output showed "Incorrect argument... /paste". This usually means the parser doesn't see /paste as a command node.
    # This implies permissions or registration issue. Bot is OP.
    
    # It turns out WorldEdit commands on Fabric are often NOT brigadier commands, so /execute cannot call them directly!
    # They are legacy commands.
    # To run legacy commands as a player from console/rcon, we need a wrapper or Carpet's player command.
    # Carpet: /player <name> run <command> ?
    # Checking Carpet docs... /player ... <action>
    # Actions: attack, use, jump, sneak, sprint, drop, swap, mount, dismount...
    # It does NOT have a 'run command' feature by default.
    
    # BUT, we can use /execute as ... run <valid_command>.
    # Since WE commands aren't "valid" brigadier commands, we can't use /execute.
    # SOLUTION: Use a Command Block? No.
    # SOLUTION: Use Scarpet to execute the command AS the player.
    # Scarpet: run(command) runs as the caller.
    # So: /execute as MIRA_Bot run script run run('/paste')
    
    print("Attempting paste via Scarpet wrapper...")
    cmd = f"execute as {bot_name} run script run run('/paste')"
    resp = bridge.run_command(cmd)
    print(f"Scarpet response: {resp}")
    
    if "Unknown command" in resp or "paste<--[HERE]" in resp:
        # If scarpet fails, try loading first
        cmd_load = f"execute as {bot_name} run script run run('/schematic load {schem_name}')"
        bridge.run_command(cmd_load)
        time.sleep(0.5)
        cmd_paste = f"execute as {bot_name} run script run run('/paste')"
        resp = bridge.run_command(cmd_paste)
        print(f"Scarpet Paste response: {resp}")

    print("Done. Check in-game.")
    bridge.disconnect()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python manual_test_paste_v2.py <path_to_litematic> [x] [y] [z]")
    else:
        path = sys.argv[1]
        x, y, z = 0, 100, 0
        if len(sys.argv) >= 5:
            x = int(sys.argv[2])
            y = int(sys.argv[3])
            z = int(sys.argv[4])
        
        run_manual_test(path, (x, y, z))
