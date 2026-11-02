# Build-description languages and APIs for programmatic Minecraft construction (2026-11-01)

Scope: what an LLM agent can write to describe a build, what primitives it gets, how errors reach the model, how big builds stay compact, and what evidence exists on LLM success. Compared against our current formats. Companion to `docs/STATUS_2026-11.md` and `docs/STACK_PROPOSAL.md`.

**Provenance key.** *[read]* = read in a local clone or fetched page today. *[installed]* = introspected in `.venv` (nucleation 0.12.0, Python 3.13) with `python -I`. *[memory]* = from prior knowledge, **not re-checked today**; treat as unverified. Fetches of `arxiv.org/abs/2608.28884` answer-mode and the `mcschematic` GitHub URL failed (404), so MineCEraft numbers come from the HTML full text and mcschematic is marked *[memory]*.

## 0. Our current formats (baseline)

| Format | Where | Representation | Primitives | Notes |
|---|---|---|---|---|
| Block list JSON | `evaluation/schemas/block_list_schema.json` | flat array of `{x,y,z,state}` + `verification` object | none (every block explicit) | `additionalProperties: false`; **no `nbt` field**, although `data_mining/parser.py` block tuples carry `nbt` (see §8). Token cost grows linearly with block count. |
| Python code | `evaluation/schemas/python_code_schema.json`, `evaluation/prompts/python_format_system.md` | `build_circuit(ctx)` + `verify_circuit(ctx)` | `ctx.set_block(x,y,z,state)`, `update_block`, `tick(n)`, `assert_block`, `assert_power` | Imperative: loops and helpers are free. The prompt documents no fill/box primitive, so models loop per block. |
| CircuitBuilder DSL (test helper) | `tests/helpers/circuit_builder.py` | functions return `(x,y,z,state,nbt)` tuples | `block`, `lever`, `lamp`, `wire`, `repeater`, `redstone_block`, `stone` | Same tuple shape as `data_mining/parser.py`. Pure Python, no simulation. Bool props are lowercased. |

## 1. WorldEdit (CraftScript / clipboard / `//schem`) — *[memory]*

- **Representation:** imperative. CraftScripts are Rhino JS/ZS against the WorldEdit API (`context`, `Edit` session). Schematics are binary clipboard files (`.schem`) pasted with `//paste`.
- **Primitives:** `//set`, `//replace`, `//copy`, `//cut`, `//paste` (with `-a`, `-o`), `//rotate`, `//flip`, `//sel` regions, masks, patterns (`#clipboard`, weighted `minecraft:stone_bricks*3,...`). Strong relative-coordinate story via clipboard origin.
- **Errors:** chat messages and exceptions; no structured diff returned to a caller.
- **Size:** `//set` over whole regions and clipboard stamps; compact per call, but scripting is per-command.
- **LLM evidence:** none found today. Not checked.
- **Fit:** good for an operator, poor for an agent loop (no structured feedback, needs a live server). Its *patterns/masks* idea (`#clipboard`, weighted palettes) is worth copying; the rest needs the game.

## 2. Minedit (NeoForge mod, `Angais/minedit`) — *[read]*

Read: `README.md` in clone `/tmp/pi-github-repos/Angais/minedit`.

- **Representation:** imperative JavaScript builder code, run in a restricted sandbox with only the Minedit builder API (no Java/fs/network). Limits: **30 s execution, 500,000 block operations per run** (stated in README).
- **Primitives:** `api.set`, `api.fill`, `api.replaceLine`, `api.clearLine` (named in README). The line-aware patch format shows the current build as numbered lines so models emit small patches instead of rebuilding. Footprint = selected X/Z area; height uncapped.
- **Modes:** one-shot `/build`; `/build stages` (several model calls, each gets the previous stage code); `/build agent` with dynamic tools `place_step`, `render_preview`, `inspect_status`, `finish_build` (Codex bridge); `/build export` / `import` for manual model use. Edit modes: normal edit, quick edit.
- **Errors:** failed responses write debug files (`last-prompt.txt` etc.); no structured diff to the model is described in the README.
- **Size:** `fill`, footprint-relative code, and line patches. Staged generation for large structures.
- **LLM evidence:** none published in the repo. The README's prompt bans (floating blocks, stair orientation, unsupported plants) are prompt engineering, not measured.
- **Fit:** the strongest existing precedent for *what our Python code format should look like* (bounded primitives + caps + line patches). Its caps and patch format are worth copying.

## 3. MineBench `voxel.exec` (`Ammaar-Alam/minebench`) — *[read]*

Read: `docs/voxel-exec-raw-output.md`, `README.md` in clone `/tmp/pi-github-repos/Ammaar-Alam/minebench`.

- **Representation:** two modes. (a) raw JSON coordinates of blocks; (b) tool mode, where the model emits JavaScript run in a Node `vm` sandbox.
- **Primitives (exact):** `block(x,y,z,type)`, `box(x1,y1,z1,x2,y2,z2,type)`, `line(x1,y1,z1,x2,y2,z2,type)`, seeded `rng()`, `Math`, constants `GRID_SIZE`, `PALETTE`. Documented as "intentionally minimal".
- **Limits:** `MINEBENCH_TOOL_TIMEOUT_MS`, `MINEBENCH_TOOL_MAX_BOXES`, `MINEBENCH_TOOL_MAX_LINES`, `MINEBENCH_TOOL_MAX_BLOCKS`; 30 s default up to grid 512; primitive-count limits; bounds checks and dedup in import.
- **Errors:** validation failures reject the job; the model does not get a structured diff. Raw response is saved for recovery.
- **Size:** `box` and `line` make 256³-scale builds expressible in a few hundred tokens. This is the key compactness trick.
- **LLM evidence:** MineBench is a blind human-vote arena (Bradley–Terry ranking); it has no functional pass metric. Useful for aesthetics, not for correctness.
- **Fit:** the cleanest model of a *minimal, bounded* primitive set. Our block list JSON has no equivalent compactness.

## 4. MineCEraft (EMNLP 2026 Findings, arXiv 2608.28884) — *[read]*

Read: full HTML `arxiv.org/html/2608.28884`.

- **Representation:** not a build language. Models act as **Mineflayer agents in a Mindcraft-style harness** issuing skill commands (`!placeBlock`-style actions plus a "!newAction" code path). Builds are judged from the logged placements.
- **Benchmark:** 723 problems, 17 task categories, all graded by code (material/shape/size match, BFS for floating blocks and ground connectivity, von Mises stress for bridges, symmetry tests, dependency order, efficiency as L1 path length).
- **Results (problem-level, all criteria at once), Table 3:** gpt5-mini 286/723 = 39.6%; gemini-3-pro 277/723 = 38.3%; claude-4-5-sonnet 192/723 = 26.6%; llama-4-17b 3/723 = 0.4%. Human reference 90% on MineCEraft-Lite. Dependency and planning are the weak categories for some models (e.g. claude-4-5-sonnet dependency 7.4%).
- **Errors:** the programmatic checks are the grader; the paper says the agent sees no visual feedback. Whether the model sees its own check results during the run is not stated in what I read.
- **Fit:** the best *evaluation* precedent: rule-based, code-checkable criteria (floating blocks by BFS, stress). We could reuse the checker ideas (connectivity BFS, support check) in our `check()` tool.

## 5. Lemma Redstone MCP (`lemma-redstone-mcp`, npm) — *[read]*

Read: npm package readme (fetch, 2026-11-01). Clean-room tick engine, not Nucleation.

- **Representation:** declarative component list: `build {name, components:[{type, at:[x,y,z], ...}]}`; `add`, `inspect`, `clear`.
- **Primitives:** components `dust, block, redstone_block, lever, button, torch, lamp, repeater(facing, delay 1–4), comparator(facing, mode), observer, piston/sticky_piston, target`. Facing is `north/south/east/west/up/down` with the explicit rule "repeater facing = the side it reads input from". Named patterns `insert_pattern` (`lamp_switch, not_gate, or_gate, and_gate, torch_clock, piston_door`) with named terminals.
- **Verify:** `simulate {ticks}` returns a per-tick trace of every component; `truth_table {inputs, outputs}` enumerates all input combinations and compares to an intended table; `assert {checks:[{at, expect, ticks}]}`; `probe {at}`.
- **Errors:** `build` reports "placement issues" (e.g. dust without a solid support). The readme says clocks are not reliably simulatable and that the engine verifies **logic, not physical placement or clock timing**.
- **Export:** schem/litematic/nbt/mcstructure base64; `push_to_game` via a Fabric bridge.
- **LLM evidence:** none published.
- **Fit:** the best *agent-facing interface* for verification: `truth_table` + `assert` + per-tick trace + named patterns. Copy the tool shape and the facing convention, not the engine.

## 6. minecraft-circuts (`RyanRana/minecraft-circuts`, npm) — *[read]*

Read: README in clone `/tmp/pi-github-repos/RyanRana/minecraft-circuts`. Extracted from Mindcraft.

- **Representation:** netlist (JSON gates `$_AND_`, `$_OR_`, …, inputs/outputs by bit) or Verilog via Yosys → relative block list `{block, dx, dy, dz}` plus `size`, `inputPositions`, `outputPositions`.
- **Primitives:** gate placement only (no user-level geometry). `autoFixDesign` adds missing support blocks and dedupes.
- **Errors:** `validateCircuitDesign` throws on missing fields; `parseJSON` strips code fences. No simulation.
- **LLM support:** ships `VERILOG_PROMPT`, `DESIGNER_PROMPT`, `PLANNER_PROMPT`, `CORRECTOR_PROMPT`. No success numbers published.
- **Fit:** shows the "LLM writes netlist/Verilog, compiler places" split. Our repo already produces a netlist-like redstone graph (STACK_PROPOSAL §4), so we can borrow the input contract (`inputPositions` / `outputPositions` as named ports). Verilog path needs yosys.

## 7. Nucleation (installed 0.12.0) — CircuitBuilder, Insign, Design/HDL flow

Read: GitHub clone `Schem-at/Nucleation` (README, `docs/features/regions-and-transforms.md`, `docs/features/redstone-simulation.md`, `docs/features/tick-simulation.md`, `redstone-eda/README.md`); introspected installed package with `python -I`.

**Representations (all three coexist):**

- *Procedural/imperative:* `Schematic.set_block_in_region(name,x,y,z,"minecraft:...[props]{snbt}")`, `fill_cuboid`, `copy_region`, `stamp`, `Shape.cuboid/sphere/line/torus/...`, `Brush` (solid, gradients, shaded), `Sdf` combinators (`union_with`, `subtract`, `translate`, `rotate`, `twist`, ...). **[installed]** names confirmed.
- *Named regions:* `create_region`, `rename_region`, `region_bounding_box_json`, `region_palette_json`. Litematica-style sub-volumes. **[read]**.
- *Typed IO (CircuitBuilder / Insign):* `CircuitBuilder.create(schematic).with_input(name, IoType, LayoutFunction, region_positions).with_output(...)`, `build`, `build_validated`, `validate`. Insign = signs in the schematic with `io.<name>` regions and sort options (`distance`, `y_first`, `x_first`, `z_first`). **[installed]** method names; **[read]** Insign syntax. `TypedCircuitExecutor.set_input/execute/tick/read_output/reset` runs with **integers as typed words**, not coordinates.
- *HDL/EDA (`redstone-eda/`, needs yosys + feature build):* `Hdl.compile_blif(blif, top, bake)` + `compile_blif_contract` → `CellContract` JSON (typed ports grouped into words, keepouts). `Design.add_cell / place / route_bus / check(strict) / bake / save`. `DesignCheckError` raised on a dirty check. **[read]** README examples; **[installed]** method names. Its own README says each artifact is proven in `mc-tick` before save and saved baked at rest.

**Simulation & errors:**

- `TickSimulation` (vanilla-order tick engine, per Nucleation docs "checked against captures from headless Minecraft"). Unsupported blocks/entities **refuse to load and are named** rather than run silently. **[read]** `tick-simulation.md`.
- `set_block` with `{simulate=true}` places "like a hand" and resolves derived state; without it the stored state may be unresolved (our spike noted this). **[read]** spike doc.
- `check(strict=True)` returns a structured report; `DesignCheckError` carries it. `Design.check()` reports shorts and repeater rings. **[read]**.
- `NucleationErrorCode` enum: `AlreadyConsumed, Generation, InvalidArgument, Io, Lock, Mesh, NotFound, NucleationErrorCode, NullArgument, Parse, Render, Serialize, Simulation, Store`. **[installed]**.

**Size:** shapes/SDF/fields and `copy/stamp` regions; EDA composes cells with `place` and `route_bus`. A 32-bit Kogge-Stone adder is 154,152 blocks from a few hundred lines of Python. **[read]**.

**LLM evidence:** none in the repo; the "verified" claims are about the engine vs captures, not models. Our own spike (`docs/SPIKE_NUCLEATION_2026-11.md`) found 11/14 fixture agreement in Placement mode and 8/14 in-world; parity with live Carpet is **not established**.

**Fit:** the single best candidate to reuse. It already gives a verification contract (`CellContract`, typed executor, `check(strict)`), and it is installed. Borrow the **typed-port contract** and the **"settled at rest, check before save"** rule.

## 8. Other stacks

| Tool | Representation | Primitives | Errors / feedback | LLM evidence | Notes |
|---|---|---|---|---|---|
| **MCHPRS / Redpiler** (`MCHPR/MCHPRS`) | Rust server with Redpiler compile step | none for authoring; it is an engine | simulation results | none | Used under Nucleation's typed executor. Not an authoring format. *[read: named in STATUS doc; not re-read]* |
| **Amulet** (Python, world editor) | Python API on Block objects and chunks, palette-based | `level.set_version_block`, block/palette access, `world` I/O | Python exceptions | none | Good for reading/writing worlds and version conversion. No verification. *[memory]* |
| **mcschematic** (Python) | `MCSchematic` with `setBlock`, `placeSchematic`, rotate; writes `.schem`/`.litematic` | place and rotate helpers | exceptions | none | GitHub URL 404 today; **unverified**. Good for building files, no sim. |
| **litemapy** (Python) | `Schematic` / `Region` / `BlockState` for `.litematic` | get/set block, regions, `(x,y,z)` | exceptions | none | Our `.litematic` reader lineage; checked nothing today. *[memory]* |
| **Structure block NBT** | `blocks:[{pos,state(index into palette),nbt}]` + `palette` | none (data only) | game-side | none | Vanilla format; can be `POST`ed via GDMC (below). *[memory]* |
| **Axiom** | GUI/editor, no text API | copy/paste/brush tools | GUI | none | Not agent-accessible. *[memory]* |
| **Create blueprint** (Create mod) | in-game blueprint/schematic tables and schematicannon | GUI-driven | in-game | none | Not agent-accessible via text. *[memory]* |
| **GDMC-HTTP** (`Niels-NTG/gdmc_http_interface`, MC 1.21.11 build in doc) | HTTP+JSON: `GET/PUT /blocks` (array of `{id,x,y,z,state,data}`), `GET /structure`, `POST /structure` (NBT/SNBT), `GET/POST /commands` (plain text, one per line), `/entities`, `/chunks`, `/biomes` | per-block place/read, structure file place (mirror/rotate/pivot), any console command | `400` for bad params/JSON; PUT returns **per-block** `{status:0|1, message}` in request order; commands return `[{status,message,data}]` per line | none published | The best existing *remote-control* API shape. Relevant to our RCON bridge: per-item status arrays and structured errors are worth copying. **[read]** Endpoints.md. |

## 9. Comparison table

Columns: representation, primitive set, error/diff to model, compactness for big builds, LLM evidence. Ratings are my judgment from the sources above.

| Option | Representation | Primitives | Error/diff to model | Compact big builds | LLM evidence | Reuse? |
|---|---|---|---|---|---|---|
| Our block list JSON | flat list | none | none (schema only) | poor (linear) | 17.5% functional (ours) | baseline |
| Our Python `ctx` format | imperative | `set_block`, `tick`, `assert_*` | assertion errors | poor (no fill) | 17.5% (ours; same prompt) | keep, add primitives |
| `circuit_builder.py` DSL | tuples | per-block helpers | none | poor | n/a (tests only) | keep as tests helper |
| WorldEdit CraftScript | imperative | set/copy/paste/rotate, masks, patterns | chat messages | good (regions) | none | borrow patterns idea only |
| Minedit | imperative JS, sandbox | set, fill, line patches | debug files | good (fill, line patches, staging) | none published | **borrow** caps + line-patch |
| MineBench voxel.exec | imperative JS, sandbox | block, box, line | validation reject | very good (box/line) | blind votes only | **borrow** primitive set |
| MineCEraft | agent actions in Mindcraft | skill commands | graded by code | n/a | 39.6% best (problem-level), 26.6–0.4% others | **borrow** checker ideas |
| Lemma Redstone MCP | declarative components | dust…target, named patterns | placement issues, per-tick trace, truth_table, assert | good (patterns) | none | **borrow** verify tool shape |
| minecraft-circuts | netlist / Verilog → blocks | gates only | throws on bad JSON | good (compiler) | prompts only, no numbers | borrow named IO contract |
| Nucleation Design/CircuitBuilder/HDL | imperative + typed IO + HDL | shapes, SDF, regions, `place`, `route_bus`, cells | `check(strict)` report, `NucleationErrorCode`, refuse-to-load | very good (cells, bus routing) | none | **reuse** (installed) |
| GDMC-HTTP | HTTP JSON + NBT | per-block PUT, structure place, commands | per-item status arrays, 400s | good (structure files) | none | borrow response shape |

## 10. Recommendation: which 2–3 to reuse or borrow

1. **Nucleation's typed-IO + check contract (reuse, highest priority).** It is already installed and pinned in `requirements.txt`. Use `CircuitBuilder` / `TypedCircuitExecutor` for "set inputs by name and integer, read outputs" and `Design.check(strict=True)` for structural errors. Copy the CellContract idea: a sidecar with named ports so the verifier does not depend on coordinates. Watch the caveat from our spike: `TickSimulation` coordinates are relative to the schematic minimum corner, and derived state needs `{simulate=true}`.

2. **MineBench/Minedit primitive surface with hard caps (borrow).** Extend our Python `ctx` format with `box`, `line`, and `fill` (in MineBench's exact names) and enforce op-count and time caps, as Minedit (500k ops, 30 s) and MineBench do. Add line-aware patch edits for iterative revisions. This addresses the "no fill primitive" gap in `python_format_system.md` and gives large builds a compact expression. Note: the repo's Python execution sandbox is **not verified** today.

3. **Lemma's verification tool shape (borrow).** Expose `simulate` (per-tick trace), `truth_table` (enumerate inputs, compare to expected outputs), and `assert` (cell at tick N equals value) as tool outputs. Use the facing convention in the README (repeater/comparator facing = input side). Pair with MineCEraft's programmatic checks (BFS connectivity, support) for the structural tier.

The **block list JSON** should stay as the interchange format for model-free checks and replay, but it is not a good authoring format. Add `nbt` to its schema if we keep it (see §11).

## 11. Findings in our repo (for the parent)

- `evaluation/schemas/block_list_schema.json:` has `additionalProperties: false` and no `nbt` property, while `data_mining/parser.py` (`parse_blocks`) and `tests/helpers/circuit_builder.py` return 5-tuples `(x,y,z,state,nbt)`. **Severity: low.** Block-entity NBT cannot be expressed in the LLM-facing schema. Fix: add an optional `nbt` string or drop the field from the tuple contract.
- `evaluation/prompts/python_format_system.md` documents no `fill`/`box`/`line` primitive, so models must loop per block. **Severity: medium** for large-build compactness; this is the MineBench/Minedit gap above.
- `evaluation/schemas/python_code_schema.json` describes `code` as "Python code" with no sandbox or op cap stated. **Severity: medium** if model code runs unsandboxed; not verified here.

## 12. Residual risks and gaps

- Sections 1, 7 (WorldEdit), 8 (Amulet, mcschematic, litemapy, structure NBT, Axiom, Create) are from memory or unread. mcschematic's GitHub URL returned 404. Treat them as unverified.
- No source found gives a **functional** LLM success rate for any of these build languages on redstone. MineCEraft is the only controlled number, and it is for a Mindcraft agent on non-redstone construction.
- Nucleation parity with our Carpet 1.21.0 server is not established (see SPIKE doc). Its redstone coverage on pistons/observers/hoppers is only as broad as its docs claim.
- The MineCEraft arXiv abstract page failed in answer mode; the figures are from the HTML full text.
- Installed Nucleation: I did not run any Nucleation example; only names and signatures were introspected.
