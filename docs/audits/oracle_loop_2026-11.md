# Oracle audit: loop (2026-11-01)

I recommend building one small, deterministic environment and harness first, and treating training as optional. The task format should give the I/O ports and the spec, not ask the model to write its own test. Then measure how much iterating with simulator feedback improves on one-shot, using that same format. `docs/STACK_PROPOSAL.md` gets the principle right: the agent edits a build program and calls tools, Nucleation runs the inner loop, and the Carpet server only confirms. But its build order puts things in the wrong sequence, it leaves out the module/port layer that "any size" depends on, and it never mentions the netlist compiler Nucleation already has. If that compiler is allowed in the benchmark without rules, the ~1000-block tier stops measuring the agent.

## What I checked
I read only. I made no edits and ran no git commands that change anything.
- I read the three docs, `training/generators/base.py`, `simulation/deconstructor.py`, `scripts/phase0_multi_model.py` and `evaluation/prompts/circuit_descriptions.json` (47 task descriptions), plus the Nekochu model card.
- I listed what nucleation 0.12.0 exposes. It already has a netlist compiler (`Hdl.compile_blif`), placement and routing tools with design-rule checks (`Design`, `Routing.drc/lvs/sta`, `Bus`), a structure detector and resizer (`Autostack`), circuit executors (`TypedCircuitExecutor`, `MchprsWorld`), and lever clicking plus a vanilla GameTest export in the tick simulator (`TickSimulation.use_block`, `gametest_snbt`, `set_rng_seed`).
- I tried the compiler in memory:
  - A 1-bit half adder compiled in 0.02 s.
  - A 4-bit ripple-carry adder compiled in 0.33 s to **12,870 blocks**. Hand-built ones are a few hundred blocks at most.
  - I could **not** confirm that compiled circuits actually work. `Executor.for_schematic` raised `NucleationError(Simulation)` and `cell_contract_json()` raised `NotFound`. "The compiler produces working circuits" is still unconfirmed.

## Problems in the proposal, by severity
| Sev | Where | Finding |
|---|---|---|
| **High** | `STACK_PROPOSAL.md:49-53` | The build order is backwards. The paste receipt on the live server comes first and the experiments that compare harness setups come last. The spike showed the live server is the weakest link: setting blocks by command doesn't send real redstone updates, so live and Nucleation agreed on only 8/14 fixtures. Nucleation-only tasks with fixed specs and the lift experiment should come first. The receipt and Carpet are only a confirmation step for a subset. Lines 3 and 52 are also stale: they say Nucleation couldn't be installed, but it is installed. |
| **High** | `STACK_PROPOSAL.md:31-35` | There is no hierarchical module contract. `components()` and `ports()` are listed as views, but nothing says a module has a spec that is checked on its own before it is composed. Without that, large builds can't be debugged once they no longer fit in context. Fix below. |
| **High** | `STACK_PROPOSAL.md` (whole), `STATUS_2026-11.md:60-64` | The proposal never mentions Nucleation's compiler or router. Benchmark tracks must fix which tools are allowed, or computational tasks just measure the compiler. It also needs a block-count/volume metric: a hand-built 4-bit adder is a few hundred blocks at most vs 12,870 compiled, so footprint is where an agent can beat the compiler. |
| **High** | `training/generators/base.py:8` | The 124 generator examples are limited to circuits that work when placed with `setblock` (no repeaters, comparators or pistons) and only check resting states. That restriction came from the server's input problem. The generators must be redone with real input changes in Nucleation, or they cover only the bottom tier. |
| **High** | `STATUS_2026-11.md` §1 / phase0 | The 17.5% baseline is muddied: the model wrote its own tests, and the gemini runs failed on client errors. "Beats one-shot" has to be judged against a **0-round run of the new harness** with fixed specs, not against 17.5%. |
| Med | `STACK_PROPOSAL.md:29` | "Run both simulators on every fixture continuously" can't be a gate until the live-server inputs are fixed. There are two options: Carpet fake-player clicks, or exporting a vanilla GameTest with `gametest_snbt` (untested, possibly a cleaner oracle). The Minecraft version Nucleation targets vs our 1.21.0 server is still unknown (`Schematic.mc_version` is a place to check). |
| Med | `STACK_PROPOSAL.md:46-47` | The training reward includes "paste receipt clean". That puts the live server inside every rollout. Keep the reward simulator-only, and only give block-count credit when the build passes, or it pushes toward empty builds. |
| Med | `STACK_PROPOSAL.md:12,32` | Making `build.py` the source of truth means running arbitrary Python as the action space. That needs a sandbox and invites reward hacking during training, and v0 doesn't need it. Start with block operations; add a restricted builder API (box/line/stamp/loop) only when tier 3 needs it. |
| Med | `simulation/deconstructor.py` | LLM-narrated reverse deconstruction yields made-up reasoning about removal order. Build order doesn't matter when the whole build is pasted at once. Drop it as training data. The useful version needs no LLM: delete blocks or modules from verified builds and use those as starting states for a reverse curriculum. |
| Low | `STACK_PROPOSAL.md:34` | The note-anchor linter and two-tier facts are too much for v0. A `NOTES.md` plus an auto-generated manifest is enough. |
| Agree | `STACK_PROPOSAL.md:37` | Text by default, renders as a later comparison. Renders help with appearance tasks, not with logic debugging. |

## 1. Harness
Build one Python environment that takes a tool call and returns an observation. Put three front ends on it: an OpenRouter tool-calling loop for the benchmark (reuse `llm_client`), an MCP server for Claude Code/pi, and a later RL training environment. Benchmark numbers and training rollouts then come from the same code.

**v0, measurable within a week (aimed at the large lift):**
- **Task = `spec.json`.** It gives fixed port positions and facing (as Nekochu's door spec does), a build bounding box, and the behaviour to check:
  - a truth table, or
  - a sequence of input events with output windows given in ticks.

  The model never writes the test. The tool is `run_spec()`, which reports for each test vector whether it passed. On a failure it shows the signal at each port over time and the first tick where the output differs from the expected one.
- **Tools:** `set_blocks`, `fill`, `remove`, `view(layer|box)`, `probe(pos, ticks)`, `run_spec`, `submit`.
- **Placement:** builds go through Nucleation's placement path, so blockstates are fully settled when saved (rule from the spike). Inputs are driven by `use_block`, and runs are seeded.
- **Comparison:** the same model and token budget at 0, 3 and 8 feedback rounds.

**v1, to scale past the context window:**
- **Module registry.** A module is a name, ports, a spec, a footprint, a verified badge and a content hash. `verify_module` tests it alone with driven inputs. `instantiate(module, origin, rotation)` places it, and `connect(portA, portB)`, optionally auto-routed in the compiler track, wires two ports. The top-level check runs only after every module is green.
- **Views:** a manifest tree of modules, ports and bounding boxes; a coarse ASCII map with one character per module footprint; layer slices on demand; and a redstone-graph summary. A diff since the last passing check shows what changed.
- **Fault localization.** When a top-level vector fails, walk the module ports and report the first module whose output doesn't match its own spec. This is the feature that matters most for large builds.
- **Memory.** `NOTES.md` plus the registry. The agent's context can be rebuilt from manifest + notes, so a build can be larger than one context window.
- **Renders** come only as a later comparison arm.

## 2. Evaluation ladder (a deliverable in its own right)
| Tier | Size | Families | Spec |
|---|---|---|---|
| T0 | 2–10 | lamp, NOT/OR/AND, wire line, repeater line, piston extend | truth table |
| T1 | 10–40 | XOR, NAND, 2:1 mux, RS latch, T flip-flop, pulse limiter/extender, clock with period P, edge detector | event sequence + tick windows |
| T2 | 20–150 | 2×2 / 3×3 piston door using **Nekochu's exact spec** (directly comparable to their reported results), flush and hidden doors | door cells toggle, hallway clear, state holds |
| T3 | 100–500 | n-bit adder (n=2..8), counters, 3:8 decoder → lamps, small RAM, combination lock | random + exhaustive vectors |
| T4 | 500–5000 | 8-bit ALU, addressed RAM, counter + 7-segment display, datapath slice | sampled vectors and timing |

**Other axes:**
- Tracks: Build, Repair (a verified build broken with the existing `data_mining/corruptor.py`), and Extend (e.g. 4-bit → 8-bit).
- Tool tier: raw blocks / component library / compiler allowed.
- Ports are randomly placed and rotated to reduce memorization.
- A task enters the benchmark only once a reference solution passes in Nucleation; a subset is also confirmed in Carpet.

**Metrics:**
- All-vectors pass@1 and pass@k, plus fraction of vectors passed.
- Rounds and tokens/$ per solved task.
- Block count and bounding-box volume relative to the reference.
- Agreement between simulator and Carpet on the confirmed subset.
- The headline number is **S50**: the build size at which pass rate falls below 50%, per model and harness. That is the "any size" number.

## 3. Training path
- **Data, in priority order:**
  1. Verified frontier-model trajectories from the harness, including failed-then-fixed segments.
  2. Generator tasks (redone with real input changes) plus Repair tasks and reverse-curriculum starting states built from verified solutions.
  3. The scraped corpus as a retrieval/component library only. It is mostly farms that depend on entities.
- **Method:** rejection-sampled SFT → expert iteration (the student runs in the harness and verified runs are kept) → GRPO only on tasks where the student's pass rate is roughly 10–90%. Use multi-turn rollouts with tool feedback in the environment, and give partial credit by vectors passed, only for passing builds.
- **What Nekochu shows:** they generated one-shot `/setblock` lists with no tools, on one hard task, with a nearly binary reward and about 10 seed examples. Reward variance hit 0 by around step 50. That doesn't show RL fails. It shows **RL needs a nonzero starting pass rate**, and filtering by curriculum, reverse-curriculum starts, partial credit and in-harness iteration are what create one. Their "feedback confused the model" problem came from changing the GRPO prompt between attempts; feedback inside the environment doesn't have that problem.
- **Model size and compute (estimates):** start with an 8–14B dense model; avoid MoE, given Nekochu's tooling pain. SFT on 1–3k trajectories takes hours on one 8×H100-class node. Multi-turn GRPO at 32k context is feasible for 8B on one node; 30B needs several nodes. Collecting data is roughly $100–300 with cheaper open frontier models, roughly 10× that with Claude-class models; that is an estimate, not measured. Training is only worth it if v0/v1 show a large lift and the benchmark exists.

## 4. Three biggest unknowns and the cheapest test for each
1. **How much does iteration actually help?** Run 3 models × 20 T0–T1 tasks × {0, 3, 8} feedback rounds × 3 seeds, Nucleation only. About 2 days and an estimated $20–50. Continue if 8 rounds reach at least 2× the 0-round score and above 60% on T1.
2. **Does Nucleation match the real game on the mechanics the ladder uses?** Take about 30 known-good circuits plus broken variants and compare Nucleation against vanilla (Carpet fake player or GameTest export), per mechanic: pistons, quasi-connectivity, observers, repeater locking, comparators. Also confirm the version it targets. About 1 day. Mechanics that disagree get dropped from training rewards and are confirmed in Carpet instead.
3. **Does module composition hold up as builds grow?** Build n-bit adders and counters at n = 1/2/4/8 with a flat harness vs the module harness, one model, and compute S50. About 2–3 days. If routing is where it breaks, add an auto-router (allowed only in the compiler track).

A fourth question comes after these: does SFT on about 1k trajectories transfer to an 8B model on held-out task families? About 1 GPU-day.

## Execution prompt (if implementation is approved)
"Implement `mira_env` (Nucleation-only):
- `spec.json` with fixed ports and a truth table or event/tick-window spec.
- A runner: settled placement, `use_block` inputs, probe reads, results per vector, first-divergence trace.
- Tools: `set_blocks`/`fill`/`remove`/`view`/`probe`/`run_spec`/`submit`.
- An OpenRouter loop with a configurable number of feedback rounds.
- 20 T0–T1 tasks taken from `evaluation/prompts/circuit_descriptions.json`, each with a reference solution that passes.
- Run 3 models × {0, 3, 8} rounds × 3 seeds and report pass@1, vectors passed, rounds and $. Don't touch the Carpet path."
