# mira_env: the Nucleation-only verification / reward environment

Implements the design decided in `docs/audits/oracle_verify_2026-11.md` and `oracle_loop_2026-11.md`:
author-written scored specs ("Contract v2"), a deterministic simulator runner, the R0-R4 reward, an
LLM-independent tool surface, and a validated task ladder. Only `nucleation 0.12.0` is needed (no server,
no API key). All Nucleation calls live in `env/sim.py`.

```
env/contract.py   Contract v2 dataclasses (JSON schema), public/hidden split, describe()
env/blocks.py     state validation/canonicalisation from Nucleation's block registry; static support (pop-off) check
env/sim.py        the only Nucleation wrapper: canonical placement (`MIRA_PLACEMENT`), Sim (ticks, checkpoint/restore, change log), structural graph
env/runner.py     prepare() (R0) + run() -> RunResult; export_litematic()
env/score.py      R0-R4 + reward, balanced accuracy, model-facing diagnostics (feedback())
env/tools.py      Env: set_blocks / fill / remove / view / probe / run_spec / submit / undo / task_info (+ tool_specs())
env/models.py     python reference models for reference_model behaviours
env/mutate.py     broken-variant generator (remove / flip+turn facing / shift / repeater delay / swap)
env/task.py       Task = Contract + reference solution + stored broken variants (env/tasks/<id>.json)
env/tasks/author.py   authoring script: the single source of every task spec + reference (regenerates the JSON)
env/validate_tasks.py task acceptance gate
env/block_support.py  which block ids Nucleation's simulator can load (1023 of 1286)
scripts/run_env_demo.py  table of references + broken variants; --episode scripted tool transcript
tests/env/        200 offline tests
```

## Commands

```bash
source .venv/bin/activate                         # python 3.13, nucleation 0.12.0
python -m pytest tests/env -q                     # ~15 s, offline
python env/validate_tasks.py                      # acceptance gate for every task (exit 1 on any rejection)
python env/validate_tasks.py t1_xor --mutants 500 --json
python env/tasks/author.py [task_id]              # regenerate env/tasks/*.json from the authoring script
python env/validate_tasks.py --write-broken       # refresh stored broken variants (needed after changing a reference)
python scripts/run_env_demo.py                    # table: reference + broken variants for all tasks
python scripts/run_env_demo.py --episode t0_and   # scripted agent episode through the tool surface
```

```python
import sys; sys.path.append(".")                  # repo convention: project root on sys.path, no __init__.py
from env.task import load_task
from env.runner import run
from env.score import score, feedback
from env.tools import Env

t = load_task("t1_xor")
res = run(t.contract, t.solution, seed=0)          # blocks: [(x,y,z,state[,nbt])] or dicts {"x","y","z","state"}
sc = score(t.contract, res)                        # sc.reward, sc.r0..r4, sc.full_pass, sc.per_output
env = Env(t, max_rounds=8); env.set_blocks(t.solution); print(env.run_spec()); print(env.submit())
```

## Contract v2 (`env/contract.py`)

A task is a `Contract` serialised as JSON (`env/tasks/<id>.json` has `contract`, `solution`, `broken`, `notes`).
Coordinates are absolute task coordinates (may be negative); boxes are inclusive `[x1,y1,z1,x2,y2,z2]`.
The model never writes a contract; `Contract.public_dict()` / `describe(public=True)` is all it sees.

| field | meaning |
|---|---|
| `id,title,tier,description` | T0/T1/T2 label and the task text |
| `bbox`, `build_box` | whole world area; the sub-box the model may write |
| `fixed` | `[{box,state}]` immutable scenery and devices (floor, levers, buttons, lamps, pistons). A fixed `minecraft:air` entry reserves a cell (pad inputs) |
| `ports` | see below |
| `behaviour` | `truth_table` and/or `sequences` and/or `reference_model` |
| `invariants` | `at_rest, paste_stable, deterministic, no_oscillation, no_forbidden, no_collateral, no_popoff, keep_clear` (+ `change_boxes`) |
| `budgets` | `rest` (ticks to reach quiescence), `total` (ticks per run, raises R0 "tick budget exceeded"), `max_blocks` |
| `forbidden` | blocks that are never allowed (command/structure/jigsaw/test blocks; the door adds `piston_head`, `moving_piston`) |
| `keep_clear`, `no_write` | boxes the model may not build in; `keep_clear` cells must also stay air at every sampled tick (hallway), `no_write` cells only pistons may fill (door cells) |
| `depends` | optional output -> inputs override for R2 (default: derived from the vectors/sequences) |
| `eval_seed`, `reference` | hidden evaluation seed; `{blocks, volume}` of the reference for R4 |

**Ports** (`name, kind, pos, facing, doc, ...`)

| kind | direction | semantics |
|---|---|---|
| `click` | input | `use_block` on a fixed lever (value 1/0 = powered/unpowered; clicks only when the level differs) or, with `pulse: true`, a fixed button (value 1 = press, auto-releases after 20 ticks stone / 30 wood, measured) |
| `pad` | input | empty reserved cell; the harness places `redstone_block` for 1 and `air` for 0 |
| `lamp` | output | `redstone_lamp[lit=true]` at `pos` |
| `wire_power` | output | `redstone_wire` with `power >= min_power` |
| `piston` | output | `piston`/`sticky_piston` with `extended=true` |
| `torch` | output | lit `redstone_torch`/`wall_torch` |
| `block_prop` | output | `{"prop","value","block"?}` equality on the block at `pos` |
| `region` | output | `box` + `region_mode`: `air` (1 iff all air), `solid` (1 iff none air), `block` (1 iff all `block`) |

**Behaviour**

* `truth_table`: rows `{inputs, expect, public, label}`, `expect` may be `null` (don't care). Evaluated in a
  seeded random order with **no reset between vectors** (`shuffle`, `reset_between` configurable); each vector
  applies the changed inputs, steps until quiescent (`settle` budget), then `extra` ticks, and reads outputs.
  `order_independent` re-runs in a second random order and requires identical outputs (catches leftover state).
  The authoring helper `Plan.table()` adds a full second hidden copy in reverse order and makes the public rows
  contain both values of every output (otherwise a constant output could pass the public check).
* `sequences`: `events [{t, set}]` and `expect [{port, value, t1, t2, mode}]`. Each sequence starts from the
  at-rest checkpoint. Tick `t` = state after `t` game ticks (events at `t` are applied before reading `t`).
  `mode: all` = value holds at every tick of `[t1,t2]`, `any` = occurs at least once. `value` is `0/1`, or
  `"init"` / `"!init"` = same as / opposite of the output's at-rest value (polarity-agnostic toggles, as in the door spec).
* `reference_model`: `fn` registered in `env/models.py`, seeded random vectors (`public_seed`/`hidden_seed`),
  `mode: comb|seq`. Declared and tested (`xor2`, `full_adder`, `adder`, ...), not yet used by a shipped task.

Time is in **game ticks** (20 = 1 s; a repeater step is 2 ticks).

## Runner (`env/runner.py`)

`prepare()` (R0): canonicalise every state against Nucleation's registry (unknown block / property / value -> error),
bounds (`build_box`, `fixed`, reserved areas, `max_blocks`). `run()`: build `fixed + build` with the **canonical placement**
(below: raw states, dust as a cross, one `Placement` load, like a structure / Litematica paste), run to quiescence
(`budgets.rest`, up to ~25 ticks are spent just settling the placement's scheduled ticks), checkpoint; then invariants, vectors, sequences,
connectivity probes, silent-component list, and (unless `fast=True`) a full deterministic re-run from scratch.
`run()` never raises on a bad build: simulator errors become R0 errors.

* `paste_stable`: the settled at-rest world is saved as raw states and re-loaded twice (`InWorld`, `Placement`); after
  quiescence it must equal the saved world. With the canonical placement this is mostly true by construction (the build already
  came from a `Placement` load); it still catches a world that is not at rest in the sense of a paste, e.g. a latch whose saved
  state moves when re-loaded or a build with pending transients (`lib/t_flipflop` rotated, legacy: 3 repeaters).
* `at_rest`: quiescent within `budgets.rest`. `no_oscillation`: every vector settles within its budget.
* `no_popoff`: static support check (see caveats: the simulator does not pop floating components).
* `no_collateral`: no block change outside `bbox` ∪ `change_boxes`, and no fixed device replaced by another block.
* `deterministic`: a second independent compile+run gives the same trace fingerprint.
* On failure each `VectorResult` carries `diverge` (first tick from which the wrong value stands) and RLE port waveforms
  (`"0@0 1@4 0@12"` = value@tick); sequence windows carry `first_bad`.
* `export_litematic(contract, blocks, path)` compiles (the same canonical placement as `run`, so the handover world is the world the
  env scored), settles, bakes and saves the at-rest `.litematic` (the audit's "save only fully resolved at-rest states"); refuses
  builds that do not compile or never rest.

## Scoring (`env/score.py`)

`reward = 0.1*R0 + 0.1*R1 + 0.1*R2 + 0.6*R3*[R1==1] + 0.1*R4*[R3==1]`

| | definition |
|---|---|
| R0 | compiles: valid states, in bounds, within block budget, simulator loads it (gate: R1-R4 = 0 otherwise) |
| R1 | fraction of structural checks that hold: `no_forbidden, no_popoff, at_rest, paste_stable, engine_placement` (the reward gate needs all of them) |
| R2 | fraction of required (input, output) pairs connected. Connected = a directed path in Nucleation's MCHPRS structural graph (`MchprsWorld.export_graph_structural`), **or** flipping the input (alone, in several contexts of the other inputs) changes a block next to the output. The dynamic arm covers pistons/observers/pads that the MCHPRS graph cannot represent |
| R3 | per output: balanced accuracy over expected-0 and expected-1 samples, rescaled `max(0, 2*bal-1)` (constant/chance/inverted = 0; one-class outputs fall back to plain accuracy); min over outputs. Failing a behavioural invariant (`no_oscillation, deterministic, order_independent, no_collateral, keep_clear`) caps R3 at 0.99, so it cannot be a full pass |
| R4 | `mean(min(1, ref_blocks/blocks), min(1, ref_volume/volume))`, only when R3 == 1 and R1 == 1 (a latency term is not implemented) |

`Score.full_pass` = every vector/sequence, every invariant, R1 = 1. `feedback()` renders structured text
(R0 errors, R1 violations, R2 missing paths, failing **public** vectors with first divergence + waveforms,
per-output scores, silent components); hidden vectors are reported only as `HIDDEN checks: k/n pass` after `submit()`.
`Env.run_spec()` runs public vectors only (and `fast=True`); `Env.submit()` runs everything.

## Tools (`env/tools.py`)

Plain methods returning strings; `Env.call(name, **kw)` dispatches, `tool_specs()` returns JSON-schema function specs.
`set_blocks([[x,y,z,state],...])`, `fill(x1,y1,z1,x2,y2,z2,state)`, `remove(...)`, `undo()` (state object with undo stack),
`view(layer|box, axis, live)` (ASCII slice, auto legend, repeater arrows show flow direction, ports listed, `live=True`
shows wire power as hex and lit lamps), `probe(pos|port, ticks, inputs)` (state changes of one cell over time from rest),
`run_spec()`, `submit()`, `task_info()`. Writes outside the build box / onto fixed cells are rejected with a message;
`max_rounds` limits `run_spec` calls.

## Tasks (22; `python env/validate_tasks.py`)

Acceptance (`validate_tasks.py`): spec validates and is non-vacuous (every output expected in both states); reference is a
full pass with all invariants (reward exactly 1.0); the empty build fails; at least 3 broken variants fail
(auto-mutated: remove / flip+turn facing / shift / repeater delay / wire->stone, 5 diverse ones stored in the JSON `broken`).
Survivors are reported (kill rate = spec strength). **All 22 accepted.** Mutation sweep (all single-edit mutants):

| task | tier | ref blocks | mutants killed | surviving examples (all physically equivalent or within the timing tolerance) |
|---|---|---|---|---|
| `t0_lamp` | T0 | 4 | 16/17 (94%) | wire->stone next to lamp (stone conducts) |
| `t0_wire_long` | T0 | 20 | 63/67 (94%) | repeater delay 1->2, wire->stone |
| `t0_not` | T0 | 6 | 23/24 (96%) | wire->stone |
| `t0_or` | T0 | 8 | 23/23 (100%) | - |
| `t0_and` | T0 | 13 | 47/47 (100%) | - |
| `t0_nor` | T0 | 11 | 33/33 (100%) | - |
| `t0_fanout` | T0 | 13 | 42/44 (96%) | wire->stone |
| `t0_pad_lamp` | T0 | 4 | 16/17 (94%) | wire->stone |
| `t0_piston` | T0 | 4 | 16/17 (94%) | wire->stone |
| `t0_delay` | T0 | 12 | 40/45 (89%) | delay 4->3 (inside the 10..22 tick window), wire->stone |
| `t1_nand` | T1 | 11 | 39/40 (98%) | wire->stone |
| `t1_and3` | T1 | 18 | 62/62 (100%) | - |
| `t1_xor` (2 subtract comparators) | T1 | 20 | 66/71 (93%) | side-input repeater delay 1->2 |
| `t1_mux2` (2 comparators + torch) | T1 | 12 | 44/48 (92%) | side-input repeater delay, wire->stone |
| `t1_rs_latch` (NOR latch, S/R merged into the feedback lines) | T1 | 13 | 46/46 (100%) | - |
| `t1_repeater_lock` (repeater-lock D latch) | T1 | 4 | 17/21 (81%, canonical placement; 18/21 legacy) | delay changes, wire->stone (the repeater powers the stone strongly and a hand-placed dust is a cross) |
| `t1_t_flipflop` (master-slave, repeater locks, button toggles) | T1 | 24 | 78/86 (91%) | delay changes, wire->stone |
| `t1_pulse_extender` (button, +16 ticks) | T1 | 10 | 36/39 (92%) | delay 4->3 |
| `t1_edge_detector` (rising edge, NOR of torch and delayed copy) | T1 | 15 | 51/53 (96%) | delay 3->4 |
| `t1_clock` (lever-enabled torch+repeater loop) | T1 | 17 | 57/60 (95%) | delay 2->3 |
| `t1_observer` (pad input, observer pulse) | T1 | 4 | 16/18 (89%) | wire->stone |
| `t2_door_2x2` (Nekochu coordinates) | T2 | 39 | 94/98 (96%) | two redundant top-row dust blocks of the reference |

Mean kill rate 94.7% (926/976). Specs were written to tolerate any design whose timing is inside a window, so
timing-only mutants may survive on purpose.

`t2_door_2x2` uses the exact hallway / door-cell coordinates, build boundary and the "do not place piston_head /
moving_piston" rule of the Nekochu/Gemma-4-31B-RedstoneDoor prompt; the lever is fixed at `(2,1,2)` instead of model-chosen,
and the reference is that model card's 40-command door. Per that model card it was verified live in Java 1.21; here it passes
all invariants plus toggle + hold for 5 s in each state (closes at tick +1, i.e. matches the sim, not a measurement of the real game).
Not done: the 3x3 door.

## Simulator caveats (each measured here unless marked)

* **Parity with the real game is not established** (spike doc; version 26.2 vs our 1.21 server). The only external check is
  the Nekochu door above (their claim, unverified by me). The vanilla gametest oracle from the audit is not built.
* `Schematic.set_block` accepts anything (`redstone_wir`, `delay=9`, `stone[foo=bar]`). `env/blocks.canonical()` validates
  against `Blocks.get_json` (Nucleation's registry, 1286 blocks) and fills defaults.
* **263 of the 1286 block ids cannot be loaded by `TickSimulation`** (`Simulation` error even alone on a floor, default
  state): e.g. every button/pressure plate except stone/oak, `daylight_detector`, the sculk family, `*_lightning_rod` (oxidised
  variants), copper chests, `ender_chest`, beds, plants. Loadable: wire, torch, repeater, comparator, lever, stone/oak button,
  stone/oak/weighted plates, lamp, target, observer, piston/sticky, hopper, dropper, dispenser, rails, tripwire, note block,
  doors/trapdoors/gates, chest/barrel, copper bulbs, tnt, slime/honey, command blocks. Some states of `crafter`/`lectern`
  (`crafting=true`, `has_book=true`) cannot even be placed. `env` reports these as R0 errors naming the block (`simulatable()`,
  cached per state); `python env/block_support.py --list` reproduces the sweep. Whether the loadable ones behave like vanilla is untested.
* A repeater placed without `locked`/`powered` makes `TickSimulation.from_schematic` raise `Simulation`; hopper, note block,
  pressure plate without full props too. Canonical states avoid this; (legacy placement only) a half-specified repeater also poisons
  later `{simulate=true}` placements nearby (`InvalidArgument`).
* (Legacy placement only, `MIRA_PLACEMENT=legacy`) `{simulate=true}` takes **stated** properties literally, so wires must be placed
  bare (`engine_state()` strips them) or they stay disconnected; and it errors when it replaces an existing non-air block. The canonical
  placement writes dust as a cross and lets the `Placement` load resolve the shapes.
* The simulator does **not** pop floating components (a wire/lever/torch with no support just stays): R1 uses a static
  support check (`blocks.floating`).
* Repeater/comparator `facing` is the **input** side (output goes the other way); wall torch/lever/button `facing` points away from
  the attachment block; `lever` defaults to `face=wall`. All verified, all stated in the task text.
* Wire strength matters at comparator sides (`rear - side` is analog); the XOR/MUX references use a repeater to restore
  strength on the side inputs (a bare long dust branch made `A=B=1` light the lamp).
* Dust powers a solid block only when it is the end of a straight single-connection line pointing at it; a corner dust next to
  the same stone did not power it (measured with a torch-on-stone probe). Unverified against the real game.
* `RedstoneGraph` has no public constructor from a simulation; the usable source is `MchprsWorld.export_graph_structural()`
  (works for wire/torch/repeater/comparator/lever/button/lamp, incl. lock "Side" edges; not pistons/observers).
  `TickSimulation.conduction_trace` is a backward trace of the *currently powered* state and showed a bogus cycle through a lit
  torch, `machine_graph_json` is a piston-machine graph; neither is used.
* `TickSimulation.restore` does not clear the change log (the env clears it); `run_until_quiescent` is replaced by a
  stepping loop so waveforms can be recorded; `is_quiescent` does account for pending repeater/torch ticks.
* Observers facing each other are quiescent `InWorld` but start clocking under `Placement` (caught by `paste_stable`).
* Bistable parts (NOR latch, locked-repeater flip-flops) come up in whatever state the placement procedure produces; the state of a
  latch is not part of the physics, so its specs start with an explicit reset pulse. Under the canonical placement the state no
  longer depends on block order or orientation, but a program that relies on a particular power-on state (as t3_counter4
  `…af52` did) can still fail: it fails exactly when a user pastes it.
* Items, hoppers with items, entities, mobs, comparators on containers are not exercised; NBT blocks are placed with
  `set_block_with_nbt` (no engine settling) and are untested.
* Timing: full run 57 ms (lamp) .. 330 ms (door); `fast` (no determinism re-run) about half. The tick budget is enforced per run.

## Deviations from / gaps against the audits

* Audit step "vanilla 26.2 gametest oracle + cross-check policy": **not built** (out of scope); no agreement numbers exist.
* R2 uses the MCHPRS structural graph plus a dynamic reach probe instead of `conduction_trace` (reasons above).
* Multi-bit buses via `IoLayout`/`TypedCircuitExecutor` are not used; the contract has individual ports only (T3 would need them or
  a port-array shorthand).
* R4 has no latency term; `reference_model` behaviour exists but no shipped task uses it (T3 adders are not authored).
* The audit says specs are "frozen before building" only for product mode; here all specs are author-written files.
* Two contract features beyond the audit: `keep_clear` / `no_write` (door hallway) and `init` / `!init` expectations.
* `Plan.table()` publishes only about half of the truth-table rows (always showing both output values); hidden rows are
  the rest plus a reversed full copy.

## Open problems

* Tasks are authored by one person (me) and validated only against this simulator: a reference that exploits a sim quirk would pass
  here and fail in the game. Needs the oracle cross-check before any headline number.
* The T flip-flop reference misses a press that follows the previous one by less than ~28 ticks (gap 25 failed, 28 worked; the
  button is held 20 ticks and the master repeater needs time to open). The spec presses 50..60 ticks apart.
* Equivalent mutants (wire<->stone between powered components) and tolerated delay changes count as survivors; kill rate is a
  lower bound on spec strength, not a coverage measure.
* Only 1 T2 task; no T3/T4 (adders, ALU), no module/hierarchy layer, no LLM loop (not in scope of this task).

## Placement: canonical vs legacy (2026-11-02)

`env.sim.compile_cells` + `Sim` build the world the way a structure / Litematica paste does (`MIRA_PLACEMENT=canonical`, the default):

1. every state is written raw, every `redstone_wire` as a **cross** (`east/north/south/west=side, power=0`, the state a hand placement starts
   from; an isolated dust therefore stays a cross, as when placed by hand);
2. `Sim` loads the schematic with `TickSettleMode.Placement` (shapes and torch/repeater state resolved after ALL blocks are present; the
   ticks scheduled by the placement are kept, baking right after the load would lose them), then the usual settle;
3. `compile_cells` tags its schematic (`name() == "mira_env_canonical"`); `Sim` always loads a tagged schematic with Placement, so every
   caller (`runner`, `tools`, `library`, `router`, `modules`, `project`, `export`) inherits the placement without a change. Raw schematics
   (`compile_cells(..., engine_place=False)`, files) keep the load mode they are given (`paste_stable`).

`MIRA_PLACEMENT=legacy` restores the old procedure (per-block `{simulate=true}` placement, sorted y,x,z, loaded InWorld). It is read on every
call, so tests can switch it with `monkeypatch.setenv`. Why: the legacy path corrupts dust shapes (ORACLE bug 6) and picks order-dependent
latch states, so 36 of 336 dihedral variants of T0-T5 references failed in the env while passing in vanilla (docs/VALIDITY.md sections 10, 13).

| measured with `env/validate_tasks.py` (42 tasks) and `env/variants.py` (42 x 8) | legacy | canonical |
|---|---|---|
| tasks accepted (reference 1.0, empty fails, >= 3 broken fail) | 42/42 | 42/42 |
| mutants killed (auto + stored) | 1172/1360 (86.2%) | 1169/1360 (86.0%) |
| dihedral variants whose reference passes | 300/336 (15 tasks with a rejection) | **336/336** |
| stored broken variants that pass (5 per variant, T4: 3; all 336 variants) | 0 | 0 (after replacing the one invalid variant, see below) |
| `validate_tasks` wall time per task, adder8 / addsub8 / shift32 / accum4 | 404 / 417 / 494 / 202 s | 167 / 246 / 311 / 35 s |
| ticks needed to reach rest at build time | 0 for all references | 0-25 (placement ticks) |

Kill-rate difference, 3 mutants, no physics: `t1_repeater_lock` 18/21 -> 17/21 (the stored "wire -> stone" variant was killed in legacy only because the lone dust
stayed a dot; a hand-placed dust is a cross and then the circuit works, so it is not a negative; replaced by "remove lock repeater [1,1,1]", failing in vanilla) and `t3_adder8`
29/35 -> 27/35 (two repeater delay+1 mutants were "killed" only by exhausting `budgets.total` 20000; with 30000 they pass, as they do in vanilla; the stored
delay variant was replaced by "remove repeater [95,2,4]", failing in env and vanilla).

**Migration.** Numbers quoted from before the change were measured under the legacy placement.
* Re-scored with the canonical env: the 20 stored session builds with a `final_blocks.json` (`data/sessions`, = the 8 sandbox sessions + earlier
  and T5 sessions): 19 identical verdicts and rewards, 1 changed: `t3_counter4 …af52` pass -> **fail** (reward 0.990 -> 0.300; all 100 vectors; vanilla fails the
  same world). The at-rest (handover) world is cell-identical to the legacy one for the other 19 builds, so their vanilla results (docs/ORACLE.md,
  `data/oracle/sessions_r*.json`) still apply. Experiment 02 (docs/EXPERIMENT_02.md): 7/8 rows unchanged, **t3_counter4 is now a fail** in the env.
* Experiment 01 (docs/EXPERIMENT_01.md): not re-scored (the transcripts store the model messages, not the final blocks). Results that could
  change are only those on tasks with bistable or dust-shape-sensitive parts (`t1_clock`, `t1_t_flipflop`, `t1_rs_latch`, `t1_repeater_lock`,
  `t1_edge_detector`, `t1_pulse_extender`, `t1_xor`) and only for builds that (a) depend on a power-on state, (b) leave a dust at a line end
  next to a dust two cells away (bug 6), or (c) failed R1 `paste_stable` / `at_rest`. Reference solutions and 19 of 20 session builds kept their verdicts, so
  the expected effect is a few cells of the grid, not the conclusions; treat the absolute rates as "legacy-placement" numbers and re-run before quoting
  them against new results.
* `lib/*.meta.json` orientation claims (`func_ok_orientations`, `orientation_failures`) were measured under the legacy placement
  (`t_flipflop` rot90 now passes in the env); regenerate with `python env/library.py write`.
* Isolated dust: a dust with no neighbour is a cross in the canonical env (as when placed by hand) and stays a cross in the handover file; the
  raw program state (bare dust) pasted as a structure would be a dot, which powers nothing sideways. The handover file is the product, so the verdicts agree.
