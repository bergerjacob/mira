# Manual Testing Guide

## 1. Connecting to the Server
The server runs on `localhost:25565`.
- **Version**: 1.21.1
- **Modloader**: Fabric
- **Mods**: Carpet, Fabric API

## 2. Running the Bridge Test
To verify RCON is working:
```bash
source .venv/bin/activate
python simulation/tests/test_connection.py
```

## 3. Testing Schematic Parsing & Building (The "Replicator")
We use a custom Python-based builder (`simulation/replicator.py`) that mimics Litematica's client-side placement. This ensures high fidelity for NBT data, block states, and entities.

### Prerequisites
- Server running.
- A `.litematic` file in `data/raw_schematics/`.

### Usage
```bash
python scripts/manual_test_paste.py <path_to_schematic> [x] [y] [z]
```

**Example:**
```bash
python scripts/manual_test_paste.py data/raw_schematics/12gt_Dispenser_Factory_Protected.litematic 0 100 0
```

### Verification
1.  Join the server.
2.  Teleport to the build location.
3.  Check that:
    - Blocks are correct.
    - Chests contain items (and stack sizes are correct).
    - Entities (e.g., Armor Stands, Minecarts) are present.

## 4. Integration Testing
To verify the full pipeline (Schematic Generation -> Replicator -> Scarpet Verification) automatically:

```bash
python simulation/tests/test_integration.py
```

This script:
1.  Generates a test schematic with Redstone components.
2.  Builds it on the server.
3.  Uses Scarpet API to verify block states and inventory contents.
