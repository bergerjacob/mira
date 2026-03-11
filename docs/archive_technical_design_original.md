# Technical Design Document: Project MIRA

## 1. Executive Summary

**MIRA (Minecraft Iterative Reasoning Agent)** is an AI framework designed to automate the generation, debugging, and optimization of complex Minecraft Redstone circuits. Redstone logic, while analogous to real-world digital electronics, presents unique challenges for standard generative models due to its strict spatial requirements and tick-based physics.

MIRA addresses these challenges through an **Iterative Agentic Workflow**. Instead of attempting to generate a complete circuit in a single pass, MIRA operates as an engineering agent that:
1.  **Plans** hierarchical modules.
2.  **Executes** implementation in a live simulation.
3.  **Verifies** success through automated testing.
4.  **Iterates** based on execution feedback.

## 2. Technical Ecosystem

### A. Data Processing: Litematic Standard
We utilize the `.litematic` format as our primary data exchange standard. It stores 3D block data, NBT states (e.g., container contents, sign text), and entity information.
- **Parser:** A custom Python implementation (using `litemapy`) that converts binary NBT data into structured Python objects for the model to process.

### B. Simulation: Carpet Mod & Scarpet
To provide the agent with a "ground truth" environment, we utilize a headless Minecraft server.
- **Carpet Mod:** Provides deep engine hooks, allowing us to freeze time, manipulate tick rates, and spawn "fake" players for testing.
- **Scarpet:** A built-in scripting language used to create our **Verification API**. This allows us to query the world state (e.g., "Is this wire powered?") with millisecond latency.

### C. Bridge: Python to Minecraft
The **Replicator** serves as the hardware-abstraction layer. It converts the AI's high-level instructions into optimized RCON commands, handling coordinate transformations and complex NBT injections that would otherwise exceed RCON packet limits.

## 3. Agent Architecture

MIRA utilizes a dual-mode architecture to manage complexity:

### A. The Architect (Hierarchical Planning)
The Architect breaks a natural language prompt (e.g., "Build a 4x4 seamless piston door") into a spatial manifest. It claims bounding boxes for specific functional modules (input logic, vertical transmission, piston arrays) and defines the "contracts" between them.

### B. The Contractor (Iterative Implementation)
The Contractor generates the actual Python code to place blocks within a module's bounds. It follows a **Contract-First** approach:
1.  **Define Success:** Write the test script first (e.g., "When I flip this lever, the door should be closed at tick 20").
2.  **Implement:** Place blocks to fulfill the test.
3.  **Validate:** Run the simulation. If the test fails, the error log is fed back into the model for debugging.

## 4. Synthetic Training Pipeline

Training MIRA requires high-quality "Reasoning Traces." We generate these traces synthetically by taking known-working circuits and introducing "mechanical faults":
1.  **Corruption:** A script removes or rotates critical components (torches, repeaters, dust).
2.  **Diagnostics:** The simulator identifies exactly where the logic fails.
3.  **Reasoning Trace:** A teacher model (e.g., GPT-4o) synthesizes the thought process required to move from the broken state back to the functional state, using the simulator's feedback as evidence.

## 5. Implementation Roadmap

### Phase 1: Infrastructure (Operational)
- [x] Automated server orchestration and mod deployment.
- [x] RCON Bridge for high-speed block placement.

### Phase 2: Data Factory (Functional)
- [x] Litematic parsing supports blocks, NBT, and entities.
- [x] Developed robust Replicator for coordinate normalization.

### Phase 3: Verification Engine (Active)
- [x] Scarpet-based API for state inspection.
- [x] Integration suite for full-loop validation.
- [ ] Expansion of API for signal-strength event tracking.

### Phase 4: Reasoning Dataset (Architectural Stage)
- [x] Dataset pipeline architecture (JSONL orchestration).
- [x] Iterative deconstruction logic (Reverse Engineering flow).
- [ ] Active LLM integration for engineering reasoning traces.
- [ ] Fault-injection engine for circuit corruption.

### Phase 5: Model Training (Planned)
- [ ] Supervised Fine-Tuning (SFT) on reasoning dataset.
- [ ] Reinforcement Learning from Execution Feedback (RLEF).

## 6. Project Directory Structure

```text
/mira
  /agent                # AI logic (Architect & Contractor)
  /simulation           # Minecraft integration & Verification API
    /server             # Headless server instance (Ignored)
    /scarpet_scripts    # In-game logic verification scripts
    /tests              # Integration and unit tests
  /data_mining          # Schematic parsing and corruption tools
  /training             # Dataset management and training scripts
  /docs                 # Technical documentation and guides
  /scripts              # Maintenance and development utilities
```
