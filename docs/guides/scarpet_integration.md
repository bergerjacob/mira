# Scarpet Integration Guide

## Overview
Project MIRA uses Scarpet (Carpet Mod scripting) to enable high-fidelity server-side verification and inspection. This allows the agent to:
- Verify block states (e.g., "Is the lamp ON?").
- Read signal strengths.
- Verify inventory contents.
- Verify entity presence.

## Directory Structure
- `simulation/scarpet_scripts/`: Source code for Scarpet apps.
- `simulation/server/world/scripts/`: Deployment target (server execution path).

## Deployment
To deploy scripts to the running server:
```bash
python scripts/deploy_scarpet.py
```
Then, inside Minecraft (or via RCON), the script is usually reloaded automatically if changed, or you can run:
```mc
/script load mira_api
```

## Usage
The `mira_api` app exposes functions as RCON-accessible commands.

### Commands
- `/mira_api test`: Verifies the API is responding.
- `/mira_api check_block <x> <y> <z> <block_state_string>`: Verifies a block state against a generic string (e.g., `minecraft:redstone_wire[power=15]`).
- `/mira_api check_inv <x> <y> <z> <slot> <count> <item_string>`: Verifies inventory contents.
- `/mira_api check_entity <x> <y> <z> <entity_id>`: Verifies entity presence within 1 block radius.
- `/mira_api check_signal <x> <y> <z> <level>`: Verifies signal strength (deprecated in favor of generic `check_block` with power property).

### Python Integration
Use `MinecraftBridge.run_command()` to invoke these endpoints.
```python
bridge.run_command("mira_api check_block 0 100 0 minecraft:stone")
bridge.run_command("mira_api check_block 0 100 0 minecraft:chest[facing=north]")
```

## API Reference

### `check_block(pos, expected_input)`
Verifies if the block at `pos` (tuple) matches the `expected_input` string.
- Supports standard Minecraft syntax: `id[prop=value,prop2=value]`.
- Partial matching: Only verifies the properties specified in the input string.
- Returns `'PASS'` or `'FAIL'`.

### `check_inventory(pos, slot, count, item)`
Verifies if the item in `slot` matches `item` and `count`.
Arguments:
- `pos`: Block position tuple.
- `slot`: Slot string (e.g., `s0`, `container.0`).
- `count`: Integer count.
- `item`: Item ID string (e.g., `minecraft:diamond`).

### `check_entity(pos, expected_type)`
Verifies if an entity of `expected_type` exists near `pos`.
- Uses `entity_selector` with distance check `distance=..1`.
