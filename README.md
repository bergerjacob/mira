# MIRA

MIRA is an AI system designed to generate, debug, and optimize Minecraft Redstone circuits. It uses a hierarchical planning architecture, headless simulation (Carpet Mod), and reinforcement learning to build and verify complex redstone contraptions.

## Project Structure

- `docs/`: Documentation and Technical Design Documents.
- `data_mining/`: Tools for scraping and processing schematic data.
- `simulation/`: Minecraft server interface and simulation environment.
  - `server/`: The Minecraft server instance (ignored in git).
  - `bridge.py`: Python-to-Minecraft RCON bridge.
  - `replicator.py`: Robust schematic builder (Python -> Minecraft).
  - `scarpet_scripts/`: In-game API scripts using Scarpet.
- `agent/`: The core AI logic (Architect and Contractor modes).
- `training/`: Training pipelines and dataset management.

## Getting Started

### Prerequisites

- Python 3.8+
- Java 21 (for Minecraft 1.21 Server)

### Setup

1. Install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Set up the simulation environment:
   ```bash
   python setup.py
   ```

## Usage

### 1. Manual Testing (Replicator)
To build a `.litematic` file in the server:
```bash
python scripts/manual_test_paste.py data/raw_schematics/file.litematic 0 100 0
```

### 2. Integration Testing
To run the full Parse -> Build -> Verify loop:
```bash
python simulation/tests/test_integration.py
```
This runs a comprehensive suite of scenarios checking Blocks, NBT, Entities, Inventories, and Redstone Signals.

## Phase Status
- **Phase 1**: Infrastructure (Completed)
- **Phase 2**: Data Factory & Replicator (Completed)
- **Phase 3**: Simulator & Verification (Completed)
- **Phase 4**: Dataset Generation (Next Steps)
