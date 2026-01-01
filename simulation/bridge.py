"""
MIRA: Minecraft RCON Bridge
Handles low-level communication with the Minecraft server via RCON, 
providing methods for command execution and world manipulation.
"""

import time
import re
from mcrcon import MCRcon
import os

class MinecraftBridge:
    """
    A bridge between Python and the Minecraft server using RCON.
    Allows executing commands and retrieving block data (conceptually, via carpet/api).
    """
    def __init__(self, host='localhost', port=25575, password='mira', timeout=30):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.client = MCRcon(self.host, self.password, self.port, timeout=self.timeout)
        self._connected = False

    def connect(self):
        """Establishes connection to the RCON server."""
        if self._connected:
            return
        try:
            self.client.connect()
            self._connected = True
            print(f"Connected to Minecraft RCON at {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to connect to RCON: {e}")
            raise

    def disconnect(self):
        """Closes the RCON connection."""
        if self._connected:
            self.client.disconnect()
            self._connected = False

    def run_command(self, command: str) -> str:
        """
        Executes a command on the server and returns the output.
        """
        if not self._connected:
            self.connect()
        
        try:
            response = self.client.command(command)
            return response
        except Exception as e:
            print(f"Error executing command '{command}': {e}")
            self._connected = False
            raise e  # Re-raise so caller can handle retry logic

    def set_block(self, x: int, y: int, z: int, block_state: str, nbt: str = None):
        """
        Sets a block at the specified coordinates.
        Optional NBT data can be provided as a string (SNBT format).
        """
        cmd = f"setblock {x} {y} {z} {block_state}"
        if nbt:
            # Append NBT to block state or as separate arg?
            # setblock <pos> <block> [destroy|keep|replace]
            # <block> includes state AND nbt. e.g. chest[facing=north]{Items:...}
            # So we append it to block_state.
            # Ensure block_state doesn't already have NBT?
            # block_state usually comes from parser as "id[props]".
            cmd = f"setblock {x} {y} {z} {block_state}{nbt}"
            
        return self.run_command(cmd)

    def fill(self, x1, y1, z1, x2, y2, z2, block_state: str):
        """
        Fills a region with a block.
        """
        cmd = f"fill {x1} {y1} {z1} {x2} {y2} {z2} {block_state}"
        return self.run_command(cmd)

    def get_block_info(self, x: int, y: int, z: int):
        """
        Gets block info. 
        Note: RCON doesn't natively return structured block data easily without parsing 'data get'.
        Integration with Scarpet API is recommended for robust data retrieval.
        """
        # This is a placeholder for the Scarpet API integration
        # In a real scenario, we'd call: /script run get_block_data(x,y,z)
        cmd = f"data get block {x} {y} {z}"
        return self.run_command(cmd)

    def tick_warp(self, ticks: int):
        """
        Warps the game forward by N ticks using Carpet mod.
        """
        return self.run_command(f"tick warp {ticks}")

    def freeze_time(self):
        """
        Freezes the game tick.
        """
        return self.run_command("tick freeze")

    def unfreeze_time(self):
        """
        Unfreezes the game tick.
        """
        return self.run_command("tick freeze status") # Toggle or set specific logic

# Example usage block
if __name__ == "__main__":
    bridge = MinecraftBridge()
    try:
        bridge.connect()
        print(bridge.run_command("say Hello from MIRA Bridge!"))
    except Exception:
        print("Could not connect to server. Is it running?")

