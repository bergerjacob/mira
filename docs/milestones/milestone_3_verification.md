# Milestone 3: Simulation & Verification (Retrospective)

## Overview
In Milestone 3, we transitioned from "blind building" to "verified execution." We turned the Minecraft server into a unit-testing engine for redstone logic.

## Engineering Challenges & Solutions

### 1. High-Latency Signal Checks
**Problem:** Redstone signals take time to propagate. Polling block states via RCON immediately after a build often results in "false negatives" because the signal hasn't reached the end of the wire yet.
**Solution:** Developed a `tick step <n>` wrapper. The verification API now forces the server to process a specific number of game ticks before checking the final state, ensuring the "physics" have settled.

### 2. The "mira_api" Scarpet Wrapper
**Problem:** Standard Minecraft commands like `/testforblock` are limited and return messy text output.
**Solution:** Wrote a custom Scarpet app (`mira_api.sc`). It provides a clean API for Python to call. For example, `check_block` doesn't just check the ID; it can perform a partial NBT/Property match, allowing us to check if a repeater is `powered=true` without caring about its `delay`.

### 3. Cleaning the "Dirty" World
**Problem:** Sequential tests would often interfere with each other if entities or blocks from the previous test weren't fully removed.
**Solution:** Implemented an aggressive "Buffered Clear" in the Replicator. It calculates the bounding box of the schematic, adds a 1-block padding, kills all non-player entities, and fills the volume with air before every build.

## Current Status
The integration test suite (`test_integration.py`) now covers the most common redstone failure modes. This environment is ready to act as the "Ground Truth" for training the AI agent.
