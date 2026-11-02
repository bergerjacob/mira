# Nucleation spike (2026-11-01)

Rough, time-boxed. Scripts: `scripts/dev_tools/nucleation_spike/` (hardcoded `/tmp/nuc_spike` + home paths; throwaway quality).
Setup: `nucleation 0.12.0` (pip, Python 3.13, now in `requirements.txt`); live oracle = our Fabric 1.21.0 + Carpet server via RCON.

## What works (measured)
- Installs and runs headless. Loads our corpus `.litematic` files (`Schematic.load_from_file`); `.litematic` save/load round-trips.
- `TickSimulation` of a lever->wire->lamp build: ~3 ms including setup.
- Exposes what the harness needs: `Diff`, `copy_region`/`stamp_region`/`fill_cuboid`, `DefinitionRegion` (named regions), `RedstoneGraph`, `machine_graph_json`, `checkpoint`/`restore`, `read_probes`, `changes_json`, `Renderer`, `from_world_directory`/`from_mca`/`save_world` (Anvil <-> schematic).

## Gotchas
- `TickSimulation` addresses blocks **relative to the schematic's min corner**, not absolute coordinates.
- Properties like `dimensions`/`block_count` are methods (nanobind), `Diff.compute` needs a preset string.
- `redstone_wire` etc. stored with default state are NOT connected. Derived state is only resolved if placed with the `{simulate=true}` tag (engine "places like a hand") - otherwise the schematic stores unresolved states.

## Differential results (small; not conclusive)
1. Existing 14 fixtures (`tests/fixtures/circuits`), Nucleation vs the fixture expectations: **11/14** in `Placement` mode, 8/14 in `InWorld`.
2. Same fixtures on the live server (literal `/setblock` paste, `carpet fillUpdates` on or off, lever flipped via `mira_api toggle_lever`): **0/14 match the fixtures** with raw states; with Nucleation-settled states pasted literally, live matched Nucleation on **8/14**. The fixtures are physically sloppy (floating wall levers, bare wire states, a redstone block next to a solid block does not power it), so they are poor oracles; the repo's own `test_not_gate_functional` also fails on this server today.
3. Clean, valid circuits activated by placing a redstone block: **wire->lamp matches exactly** (wire power 15 -> 11, lamp lit) between settled-paste-live and Nucleation; **sticky piston differs** (Nucleation extends and pushes the stone; live does not move) - almost certainly because `setblock` of the activator does not deliver redstone-grade updates (our own `conftest.py` documents this), i.e. a weakness of our stimulus, not necessarily of the simulator.

## Conclusions
- Nucleation is a credible inner-loop simulator and schematic engine. Parity with the real game is **not yet established**; the live oracle's stimulus is the weak link.
- Fix the oracle before trusting any parity number: drive the server with a **Carpet fake player** (`/player <name> spawn|look|use`) so levers/buttons are clicked exactly as a client would; generate test circuits from a settled, valid source (not hand-written bare states).
- Core reliability rule that falls out of this: **compile builds to fully resolved, at-rest states** (run placement through the engine) before saving; then literal paste (Litematica-style) and the simulator agree about what was saved.
