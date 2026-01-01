# MIRA: Minecraft Iterative Reasoning Agent

MIRA is an experimental AI framework designed to bridge the gap between high-level engineering requirements and functional Minecraft Redstone circuits. Unlike standard generative models that often struggle with the strict logic of redstone physics, MIRA uses an iterative approach—combining hierarchical planning, real-time simulation, and automated verification.

> **Status:** Work in Progress. Current focus is on dataset generation and simulation fidelity. Core infrastructure is operational but not yet fully optimized for production environments.

## Core Features

- **Hierarchical Planning:** Breaks down complex circuits into manageable modules with defined spatial constraints.
- **Headless Simulation:** Utilizes a Fabric-based Minecraft server with [Carpet Mod](https://github.com/gnembon/fabric-carpet) for high-speed block-state verification.
- **Automated Replicator:** A robust Python-to-Minecraft bridge that handles block states, NBT data, and entity summoning via RCON.
- **Verification Engine:** In-game Scarpet scripts provide real-time feedback on redstone signals, container states, and logic flow.

## Project Structure

- `agent/`: Core AI logic implementing Architect (planning) and Contractor (implementation) modes.
- `simulation/`: Minecraft server integration, RCON bridge, and the Scarpet-based verification API.
- `data_mining/`: Tools for extracting and processing redstone knowledge from `.litematic` schematics.
- `training/`: Pipelines for synthetic dataset generation and reinforcement learning.
- `docs/`: Technical design documents and implementation guides.

## Getting Started

### Prerequisites

- **Python 3.10+**
- **Java 21** (Required for Minecraft 1.21 server)

### Setup

1. **Environment Initialization:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Simulation Environment Setup:**
   Run the automated setup script to download the server, Fabric loader, and necessary mods:
   ```bash
   python setup.py
   ```

## Development & Testing

### Integration Testing
MIRA includes a suite of integration tests that verify the full "Parse -> Build -> Verify" loop. This ensures the replicator and simulation API are working in sync.

```bash
python simulation/tests/test_integration.py
```

### Manual Schematic Replication
To manually test the replication of a specific `.litematic` file:
```bash
python scripts/replicate_schematic.py data/raw_schematics/your_file.litematic <x> <y> <z>
```

### Dataset Generation (Phase 4)
To generate a synthetic reasoning dataset from a directory of schematics:
```bash
python simulation/dataset_generator.py --input-dir data/raw_schematics --output-file data/training/reverse_dataset.jsonl
```

## Roadmap & Progress

| Phase | Milestone | Status | Details |
| :--- | :--- | :--- | :--- |
| **1** | Infrastructure | **Stable** | RCON bridge and server orchestration are operational. |
| **2** | Data Factory | **Stable** | Litematic parsing supports blocks, NBT, and entities. |
| **3** | Verification | **Stable** | Scarpet API validates redstone signal propagation and state. |
| **4** | Reasoning | **In Progress** | Infrastructure for synthetic reasoning traces is architected. |
| **5** | Training | **Planned** | SFT and RL training on generated datasets. |
