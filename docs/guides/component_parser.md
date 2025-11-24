# Component: Litematic Parser & Data Factory

## Overview
This component is responsible for converting binary `.litematic` files (from the Litematica mod) into MIRA's internal Python representation. This allows the agent to "read" existing circuits to learn from them or to verify its own generation.

## Usages

### 1. Parsing a Schematic
The `parser.py` script uses `litemapy` to read a file and extract:
- Block data (ID, properties like `facing`, `powered`)
- Dimensions
- Metadata (Author, Name)

### 2. The "Replicator" Test
To verify the parser works, we use a "Replicator" workflow:
1.  **Input**: `door.litematic`
2.  **Parse**: Converts to Python List of `(x, y, z, block_state)` tuples.
3.  **Build**: The Agent connects to the server via RCON and executes `setblock` for every block in the list.
4.  **Verify**: The User joins the world and sees the structure appear block-by-block.

## Next Steps for Contributors
- Add `.litematic` files to `data/raw_schematics/`.
- Run `python scripts/manual_test_paste.py <filename>`.

