# Phase 4: Dataset Generation Prototype

## Overview
This phase focuses on building the "Teacher" for the MIRA agent. We use a synthetic data pipeline to generating training examples for Supervised Fine-Tuning (SFT).

## Architecture
The pipeline follows the "Test-Driven" token order for SFT:
`[User Goal]` -> `[Broken Code]` -> `[Reasoning Trace]` -> `[Test Contract]` -> `[Fixed Code]`

### Components

1.  **Mechanical Corruptor (`data_mining/corruptor.py`)**:
    - Injects faults into valid schematics (e.g., breaking wires, rotating pistons).
    - Generates the "Broken Code" input.

2.  **Test Generator (`simulation/test_generator.py`)**:
    - (Mocked/LLM) Generates a functional Python verification script given the *valid* schematic.
    - Defines the "Contract" for success (e.g., "If I flip this lever, that door must close").

3.  **Simulator (`simulation/replicator.py` + `dataset_generator.py`)**:
    - Builds the schematic on the Carpet server.
    - Executes the generated Test Script.
    - Captures the **Runtime Error** (e.g., "Assertion Failed: Door didn't close").

4.  **Teacher Pipeline (`simulation/dataset_generator.py`)**:
    - (Mocked/LLM) Synthesizes the "Reasoning Trace" (`<THOUGHT>`) explaining the fix based on the error log.

## Prototype Status
- **Implemented**:
    - Full Corrupt -> Build -> Test -> Fail loop.
    - Functional Test generation (hardcoded for Piston Door example).
    - Dynamic state verification using `mira_api`.
- **Verified**:
    - `simple_piston_door.litematic` correctly fails when a wire is broken and passes when fixed.
    - Output JSON contains full source code for Broken, Test, and Working states.

## Usage
```bash
python simulation/dataset_generator.py
```

## Next Steps (Phase 5)
1.  **Connect LLMs**: Replace mocks in `TestGenerator` and `DatasetGenerator` with real OpenAI/Anthropic API calls.
2.  **Scale**: Run on the full dataset of schematics.
