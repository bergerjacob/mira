# Roadmap & Planning (Internal)

This document tracks the evolving logic and architecture of MIRA.

## Current Focus: Phase 4 (Synthetic Data)
The goal is to generate "Reasoning Traces." We need a script that:
1. Loads a working circuit.
2. Runs the Scarpet test to confirm it works.
3. "Corrupts" it (e.g., rotates a repeater or removes a wire).
4. Records the "Broken State" + "Error Log" -> "Human Fix" -> "Fixed State."

## Research Notes: Fault Injection
- **Mechanical Faults:** Deleting blocks, changing repeater delays.
- **Topological Faults:** Breaking the redstone wire path.
- **State Faults:** Locking a hopper so items can't flow.

## To-Do (Brainstorming)
- [ ] Experiment with GPT-4o for "Teacher Trace" generation.
- [ ] Profile the Scarpet API to see how many tests we can run per second.
- [ ] Investigate `mc-proto` as a faster alternative to RCON if latency becomes a bottleneck.

