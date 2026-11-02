# MIRA Stack Proposal (2026-11-01)

Follows `STATUS_2026-11.md`. Goal: an agent that designs things iteratively like a codebase, in a world where "paste it and it is exactly what a Litematica client paste would give". Items marked **unverified** were read in docs only; nothing below was run (sandbox allows the PyPI index but blocks `files.pythonhosted.org`, so `nucleation` could not be installed).

## Principle

The agent never talks to a character. It edits a **build program** and calls **tools**; the world is a disposable void-world server used for confirmation. Everything crosses layers as a schematic file.

```
agent (Claude Code / pi / open model)        <- actions = edit files + call tools
  |
build repo:  build.py  (source of truth)  +  CONTRAPTION.md / contraption.json (facts)
  |   compile
schematic:  .schem (Sponge v3)  <->  .litematic   (Nucleation reads/writes both)
  |                                   \
fast twin: Nucleation tick sim         paste -> Fabric + Carpet void world -> read-back receipt
(inner loop, headless, RL-scale)       (oracle; same semantics as Litematica server paste)
```

## Layer decisions

**1. Format.** Interchange = Sponge `.schem` v3 (documented spec; WorldEdit/FAWE native; already installed on our server as WorldEdit 7.3.8). Import/export `.litematic` because our scraped corpus and the community use it, and it additionally stores scheduled ticks, entities and block entities per region. Litematica is a client mod, not a library: "integrating" means (a) producing/consuming its file format and (b) matching its paste semantics, so a human can load the agent's output in Litematica and see the same thing.

**2. Paste semantics (what "perfect" means).** On a server Litematica pastes with plain `/setblock` + `/fill` at a rate limit (`commandLimitPerTick`, default 64), optionally with Carpet `fillUpdates false` to suppress block updates; it cannot restore scheduled ticks on a server, and block-entity NBT needs extra `data modify` steps. Single player is exact (no updates, ticks and NBT restored). Our `simulation/replicator.py` already implements the server-style path (64 cmds/tick, `carpet fillUpdates`, `data merge` for containers). Consequences:
- Treat contraptions as **saved at rest** (Litematica's own guidance); then command paste is faithful.
- Make fidelity **checked, not trusted**: after every paste, read the region back and diff against the schematic (blockstate, block-entity NBT, entities), emit a **paste receipt**. This is cheap, needs only our existing server, and is the real reliability feature.
- Nucleation's settle modes (`InWorld` vs `Placement` vs `Quiet`) model exactly this distinction ("saved state trusted" vs "block-by-block placement fires observers"); use them to simulate the same paste mode we use. **Unverified.**

**3. Two simulators, one contract.** Inner loop = Nucleation (Rust; `mc-tick` vanilla-order tick engine incl. pistons, observers, water, some entities, refuses unsupported entities by name; checked against captures from headless Minecraft per its docs; MCHPRS Redpiler for pure logic). Oracle = Carpet server. Run both on every fixture continuously (differential testing); any disagreement is a bug report on one of them. **Unverified: which Minecraft version Nucleation targets and parity with our 1.21.0 server.**

**4. Agent interface = a build repo.**
- `build.py` (or a small DSL): parametric, modular, uses primitives/loops/components/imports from a component library. Edits are diffs. Deterministic compile to schematic.
- Tools (MCP or plain CLI; both are thin): `query(box)`, `slice(y|axis)`, `find(pattern)`, `components()`, `ports()`, `diff(a,b)`, `sim(inputs, ticks)` with traces, `check()` (floating blocks, bounding box, support), `paste()`, `receipt()`.
- Facts: **two tiers**. (a) auto-derived each build and never hand-edited: bbox, palette, connected components, detected IO, redstone graph (netlist-like). (b) agent-authored `CONTRAPTION.md`: intent, "lever = start", "piston bank = door". Notes reference **named regions/ports in `contraption.json`**, not raw coordinates, and a linter flags any note whose anchor no longer matches (e.g. "lever" region contains no lever). Nucleation's sign-based region DSL (Insign) is an optional in-schematic encoding; keep the sidecar JSON as the source so schematics stay clean.
- High-level "picture" without vision = multi-resolution text: manifest tree -> top-down coarse ASCII map with legend -> functional graph -> per-slice detail on demand.

**5. Vision.** Default: none; optional `render(view)` tool for vision-capable models and aesthetics. Evidence (abstracts only, none Minecraft-specific): SpatialEval (NeurIPS 2024) finds VLMs often underperform their text-only LLM counterparts on spatial tasks and lean on text when it is sufficient; GSU (Mar 2026) finds visual exposure does not give VLMs a usable 3D understanding on grid tasks and that frontier text models are strong but still weak at identifying 3D shapes from coordinate lists; VoxProg (Oct 2026) gets gains from programs over local voxel views with execution feedback. Counter-signal: MineCEraft's arch-bridge examples show ASCII drawings failing where an image prompt succeeds, and SceneCraft/BlenderGym-style loops use renders (for appearance tasks). I found no controlled Minecraft ablation of text vs image observations, so make it a cheap experiment later rather than a design commitment.

**6. Not in the core stack.** Mineflayer/avatar bots: reserve for end-to-end checks in a real client session (e.g. a bot presses the lever) and for entity/farm behaviours no simulator covers. Mindcraft/MCP avatar servers: skip.

**7. Version.** Server is Minecraft 1.21.0 with Fabric API 0.102, Carpet, carpet-tis, WorldEdit 7.3.8. Current Minecraft is 26.x (Carpet and Litematica both ship 26.1-26.3 builds as of Oct 2026). Recommend staying on 1.21.x until the Nucleation spike reports its target version and parity, then pin one version for everything including the corpus.

## RL fit
Actions are text/code edits and tool calls, so any agentic-RL stack with tool-calling environments works. Reward = fixed truth-table/timing spec passes in the sim, paste receipt clean, block-count cost. Trajectory seeds come from frontier models in the harness; tasks from the procedural generators; the scraped corpus supplies component library and reverse-curriculum material.

## Build order (needs only what we have, then Nucleation)
1. Paste receipt on the existing server (read-back diff, entities/NBT) + deterministic void-world config. No new dependencies.
2. Build-repo core (`build.py` compile, facts/manifest, query/slice/diff tools) over the existing parser/litemapy.
3. Nucleation spike and differential tests (blocked on installing the wheel).
4. Sim-backed `sim()`/`check()` tools; harness ablations with frontier models.
