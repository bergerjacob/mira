# Synthetic data engine (Phase A4, 2026-11-02)

An LLM-free generator of **verified tasks** and **scripted training trajectories** for later SFT / RL. Everything is produced by our own
code from our own library cells (`lib/`) and checked by our own environment (`env/`, Nucleation). No model, no network, no third-party
content beyond what `lib/` already carries (see "Licence / provenance").

```
training/synth/netlist.py       random combinational netlists over lib cells -> placement -> wire_path routing -> contract v2 task
                                (+ optional 3D-router layout, make_task(router=True))
training/synth/sequential.py    parameter sweeps: delay lines, off-delay timers, n-bit counters, n-place shift registers
training/synth/repair.py        repair tasks (program bug + spec, fix = original), repair_env (env/tasks t0-t3 references), extend tasks
training/synth/bugs.py          program-level bugs + templated diagnosis; solution_of() = the harness build tool's view of a program
training/synth/trajectories.py  scripted builder-arm episodes run THROUGH harness.agent.Episode (real tool outputs) -> chat JSONL rows
training/synth/common.py        acceptance gate, storage, dedup claims, program/solution consistency check, pmap (<= 8 workers)
training/synth/stats.py         yields / runtimes / size distributions -> data/synth/stats.json
scripts/synth_generate.py       CLI: netlist | sequential | repair | extend | stats      (every attempt logged to data/synth/attempts.jsonl)
scripts/synth_trajectories.py   CLI: episodes for a task family -> data/synth/sft/<out>.jsonl (resumable)
scripts/synth_replay_check.py   independent replay of random exported rows (last build -> full env run)
tests/training/test_synth_engine.py   44 offline tests (~2-3 min on a loaded machine)
data/synth/                     outputs (gitignored): tasks/<family>/<id>.{json,py[,broken.py]}, sft/*.jsonl, stats.json, router_failures/
```

## Commands

```bash
.venv/bin/python -m pytest tests/training -q                                            # 44 tests
.venv/bin/python scripts/synth_generate.py netlist --bucket medium --start 0 --n 100 --workers 8
.venv/bin/python scripts/synth_generate.py netlist --bucket medium --router --start 1000 --n 24   # router layout (family netlist_routed)
.venv/bin/python scripts/synth_generate.py sequential --workers 8
.venv/bin/python scripts/synth_generate.py repair --per-task 2 --workers 8                # synth tasks + env/tasks t0-t3 (repair_env)
.venv/bin/python scripts/synth_generate.py extend --limit 40 --workers 8
.venv/bin/python scripts/synth_trajectories.py --family netlist --match sn_medium --stride 2 --out netlist --workers 8
.venv/bin/python scripts/synth_trajectories.py --family repair --kinds repair --out repair
.venv/bin/python scripts/synth_generate.py stats                                        # -> data/synth/stats.json
.venv/bin/python scripts/synth_replay_check.py --per-shard 5                            # re-score exported rows independently
```

All generators are deterministic in their seeds (`random.Random(f"{bucket}:{seed}")`), resumable (existing outputs / JSONL rows are skipped)
and parallel through `ProcessPoolExecutor` capped at `MAX_WORKERS = 8`. Long runs must stay in the foreground of one call or in one tmux pane
(background jobs die with the tool call in this sandbox).

## 1. Random netlists -> tasks (`netlist.py`)

* **Netlist**: `random_netlist(rng, k, n_gates, m)` draws a DAG over the cells NOT (`not_gate`), AND (`and2`), OR (`or2`), NOR (`nor2`) and
  NAND (`and2` + `not_gate`), weights 1/3/3/2/2. Accepted only if every input and every gate output is used, all outputs are distinct, non-constant
  functions and every input influences some output. Duplicates (same structure up to commutative input order) are rejected by an atomic
  signature claim (`data/synth/tasks/<family>/.sig/`), so parallel workers never store the same net twice.
* **Placement + routing** (`layout`): every signal owns a bus running east at `y=1, z = 9 + 4*s` (levers at `x=0`, lamps at the east end, fixed
  scenery like T3/T4); the gate cells sit in one row north of the buses in topological order; a gate input branches off its bus through a
  repeater, climbs to `y=3`, crosses the buses in between on stone supports (the stone over each crossed dust also blocks the diagonal
  connection), drops in front of the gate and enters the port from the west; a gate output runs back south onto its own bus the same way. All
  routes go through `simulation.builder.wire_path(..., floor_exists=True, check_spacing=True)`; tap/output columns are >= 3 cells apart (see
  "Nucleation quirk" below). The program is self-contained (library prelude + helpers `bus/tap/out` + one line per net), e.g.
  `tap(b, 2, 18, (24, 1, 3))   # n0 -> gate input`.
* **Contract v2**: levers (`click`) in, lamps out, `tier="SYN"`, settle `120 + 40*depth`, explicit `depends`, `max_blocks` 6000. Truth table:
  exhaustive for k <= 4 (public = every other row, always showing both values of every output, plus a hidden reversed copy) and, for k = 5-6,
  24 random rows (8 public, 16 hidden, disjoint) + the reversed hidden copy, with rows added until every output is seen in both states.
* **Gate** (`common.gate` = `env.validate_tasks.evaluate`, the same acceptance gate as `env/tasks`): spec valid and non-vacuous, reference
  reward exactly 1.0 with every invariant (incl. `paste_stable`, `deterministic`, `order_independent`), empty build fails, >= 3 of 8
  auto-mutants fail; the failing mutants are stored as `broken`. Before the gate, `program_matches` checks that the reference program, compiled
  and filtered exactly like the harness `build` tool, loads exactly the task solution.
* **Router option** (`make_task(router=True)`, family `netlist_routed`): same placement, one `b.route(src, [sinks])` per net through
  `simulation/router.py` (A2, written in parallel). Not used by default (see yields).

Buckets (`scripts/synth_generate.py`): easy = 1-2 gates, 1-3 inputs, 1 output; medium = 3-4 gates, 2-4 inputs, 1-2 outputs;
hard = 5-7 gates, 3-6 inputs, 1-3 outputs.

## 2. Sequential families (`sequential.py`)

| family | construction | spec |
|---|---|---|
| `sq_delay_<T>_<v>` (T = 2..40, 3 layout variants) | lever -> `delay_line(ticks=T)` -> lamp, random distances/offsets | rise/fall ticks **measured** on the reference in Nucleation, task text states them, sequences demand them within +-2 ticks (one public, two hidden: later start, two pulses) |
| `sq_offdelay_<D>_<v>` (D = 4..40) | lever -> `or2`(A, `delay_line`(A, D)) -> lamp | lamp on with the lever, off about D ticks after it (measured, +-2) |
| `sq_counter<n>[_down]` (n = 1-4) | `env/tasks/author_t3.t3_counter` (T flip-flop chain) renamed | its python-model stream (`counter_tff`) |
| `sq_shift<n>` (n = 2-10) | `env/tasks/author_t4.t4_shift` (D flip-flop chain, clock bus against the data) renamed | its timed sequences |

The T3 counter bodies call `wire_path` without `floor_exists=True`, so as programs they write stone supports onto the fixed floor and the
harness `build` tool refuses them (author_t3 strips y=0 from the stored solution, so env/tasks is unaffected). The synth copies patch the body;
caught by `program_matches` (all 8 counter programs failed it before the patch).

## 3. Repair / extend (`repair.py`, `bugs.py`)

* **repair** (family `repair`, id `rp_<base>_<j>`): a stored synth task + one program bug from `bugs.ordered` (round-robin over kinds) that compiles
  and **fails the public spec** (`verify_bug` compiles like the harness and runs the public spec). Bug kinds: `missing_tap`, `missing_out`,
  `wrong_signal` (tap of another bus), `short_bus` (bus stops short of its lamp), `wrong_gate` (or2 <-> and2, nor2 -> or2), `missing_net` (router
  layouts), `missing_route` (any library program), `wrong_ticks` (delay line +-6). The task text = base text + `REPAIR:` + the broken program; the
  reference is the original program, so "the fix is the original" holds by construction; the base passed the gate. `.broken.py` holds the given program.
* **repair_env** (family `repair_env`, `rpe_<id>_<j>`): every env/tasks t0-t3 reference x its stored broken variants (`env/mutate.py` edits, all
  known to fail), as flat `b.points(...)` programs (`harness/reference.builder_source`). **These are the benchmark's references: the rows carry
  `contamination` in their synth meta and live in their own shard (`repair_env.jsonl`); exclude them from any model whose benchmark numbers are reported.**
* **extend** (family `extend`, `ex_<base>`): netlist N + one random gate and output -> N' (new distinct non-constant output, fully gated as a new
  task). Task text = N' text + `EXTEND:` + N's program (its lamps sit at N's old positions); reference = N''s program.
  (adder2 -> adder3 style extends of the T3 adders were not done: their lever/lamp geometry changes with n.)

## 4. Scripted trajectories (`trajectories.py`)

Each episode is run through **`harness.agent.Episode` itself** with a reactive `ScriptedLLM` callable, so system prompt
(`prompts.system_prompt("builder")`), task prompt (`prompts.task_prompt`, R and the 30-turn cap), tool list (`build_tools("builder", R)`),
tool-result clipping (7000 chars), the `[turn i/30 | run_spec j/R used]` footers and the final scoring are byte-for-byte those of an LLM
run of the builder arm (asserted in `test_verify_episode_matches_the_harness_prompts_and_tools`). Only the assistant side is scripted.

| kind | R | assistant script (tool results are real) |
|---|---|---|
| `oneshot` | 0 | [view(layer=1), 50%] -> build(reference) -> submit |
| `verify` | 3 | build(reference) -> run_spec -> submit |
| `debug` | 3 | build(bugged) -> run_spec (fails) -> build(reference) with a diagnosis -> run_spec (passes) -> submit |
| `debug_view` | 3 | as debug, plus view(layer=1) before the fix |
| `typo` | 3 | build(program with `repeter`/`wirepath`/`levr`) -> COMPILE FAILED -> build(reference) -> run_spec -> submit |
| `repair` / `extend` | 3 | build(given program) -> run_spec (fails) -> [view, 50%] -> build(fix) -> run_spec -> submit |

An episode is exported only if the final `submit` is a full pass and, for debug/repair, the first run_spec really failed (and for typo the compile
really failed). Assistant text is templated: the plan sentence comes from the netlist, the diagnosis quotes the first failing public vector /
missing path from the real run_spec output and states the ground-truth cause and fix of the injected bug.

**Row format** (`data/synth/sft/<shard>.jsonl`): `{"messages": [OpenAI chat: system, user, assistant(tool_calls), tool(tool_call_id), ...],
"tools": [OpenAI function specs of the builder arm], "meta": {task, source, family, kind, difficulty, n_blocks, verified: true, reward,
rounds, rounds_used, turns, arm: "builder", base_task, bug, seconds, synthetic_spans}}`. `synthetic_spans[i]` says where message i comes from:
`harness_prompt` (system/user), `template` (assistant text) with per-call `reference_program` / `bugged_program` / `tool_call_template`, and
`env_real` (every tool message). Programs include the full library prelude (the builder arm has no `components` extra), so rows are
20-50k characters (p50 about 6-10k tokens; the system prompt alone is 15k chars).

### Example episode (`sn_easy_00002`, kind `debug`, abridged)

```
system  <builder system prompt, 15098 chars>          user  <task text + public vectors, 2243 chars>
A  "Netlist: n0 = not(I0); outputs Y0 = n0. Plan: one bus per signal running east at y=1 ... Writing the program."  build(<2693 chars>)
T  build loaded: 22 blocks (2 identical to fixed blocks ignored), bounds [0, 1, 1]..[15, 1, 13]  [turn 1/30 | run_spec 0/3 used]
A  "Loaded. Checking it against the public vectors."  run_spec()
T  [run_spec #1] ... R2 CONNECTIVITY: 0% ... - I0 -> Y0: no circuit path ... R3 BEHAVIOUR (public vectors): 1/2 pass
   FAIL vector: set {'I0': 0} -> expected {'Y0': 1}, got {'Y0': 0} ... SILENT components ...
A  "run_spec: R3 BEHAVIOUR (public vectors): 1/2 pass; FAIL vector: ... Cause: the output of gate n0 at (11, 1, 1) is never routed onto its
   bus 1. Fix: add back `out(b, (11, 1, 1), 12, 1)`."  build(<2741 chars>)
T  build loaded: 38 blocks ...                         A  "Re-checking after the fix."  run_spec()
T  [run_spec #2] ... R3 BEHAVIOUR (public vectors): 2/2 pass ... ALL PUBLIC CHECKS PASS
A  "All public checks pass now. Submitting."  submit()
T  [submit] ... HIDDEN checks: 2/2 pass (details withheld)  SCORE: reward=1.000 ... FULL PASS
```

## 5. Measured (2026-11-02, this machine, 16 cores shared with four other agents; see `data/synth/stats.json` for the live numbers)

**Task generation** (gated = reached the acceptance gate; duplicates/no-net draws are skipped before it):

| family / bucket | gated | accepted | yield | reject reasons | s/attempt p50 (p90) | blocks p10 / p50 / p90 (max) |
|---|---|---|---|---|---|---|
| netlist easy | 32 (+48 duplicate draws) | 32 | 100% | - | 8.9 (14) | 74 / 168 / 223 (231) |
| netlist medium | 250 | 249 | 99.6% | 1 x only 2 mutants fail | 47 (82) | 312 / 458 / 626 (716) |
| netlist hard | 99 | 93 | 93.9% | 5 x vacuous output in the random sample (fixed afterwards), 1 x weak spec | 103 (177) | 802 / 1152 / 1520 (1852) |
| netlist_routed medium (router) | 24 | 9 | 37.5% | 9 unroutable (router search limit), 6 routed but failing (4 functional, 2 paste_stable) | 22 (63) | 145 / 200 / 253 |
| sequential delay | 60 | 38 | 63% | 22 layout variants where the lamp never follows the lever (route geometry; now rejected before the gate) | 2.1 | 11 / 13 / 16 |
| sequential offdelay / counter / shift | 24 / 8 / 9 | 24 / 8 / 9 | 100% | - | 4.6 / 28 / 44 | 31 / 90 / 182 (p50) |
| repair (synth) | 469 | 453 | 96.6% | 16 x no failing bug among 12 candidates (counters / tiny delay lines) | 3.7 (14) | 14 / 391 / 1109 |
| repair_env (t0-t3) | 66 | 66 | 100% | - | 0.0 | 4 / 17 / 367 (993) |

Netlist yield is the headline: **99.6% medium, 94% hard** with wire_path routing (the 5 vacuous-output hard rejects are a spec-sampling bug fixed
in `truth_rows`; the generator itself never produced a reference that failed). Hard tasks take about 100 s each, mostly the 8-mutant gate.

**Stored validated tasks** (`stats.json` -> `stored`; blocks = reference solution size, p50):

| family | tasks | by difficulty (n, blocks p50) |
|---|---|---|
| netlist | **367** | easy 26 (175), medium 248 (458), hard 93 (1152; max 1852) |
| netlist_routed | 9 | medium 9 (200) |
| sequential | 79 | delay 38, offdelay 24, counters 8, shift registers 9 (easy 66, medium 8, hard 5) |
| extend | 40 | easy 26 (305), medium 14 (612); 39/39 gated extends accepted |
| repair (synth) | 892 | easy 184, medium 512, hard 196; bug kinds short_bus, missing_out, missing_tap, wrong_signal, missing_route, wrong_ticks, wrong_gate |
| repair_env | 68 | T0 20, T1 22, T2 2, T3 24 (contaminating, separate) |
| **total** | **1455** | 495 newly generated circuits (netlist + routed + sequential + extend) + 960 repair variants |

**Episodes** (`data/synth/sft/`, every row `verified: true`, final reward 1.0; chars = all message text incl. programs):

| shard | episodes | kinds | difficulty | tasks | chars p50 / max | est. tokens p50 | s/episode p50 |
|---|---|---|---|---|---|---|---|
| `netlist.jsonl` | 699 | oneshot 150, verify 174, debug 174, debug_view 150, typo 51 | easy 113, medium 538, hard 48 | 174 | 28k / 38k | 7.8k | 13 |
| `netlist_routed.jsonl` | 39 | all five | medium | 9 | 25k / 32k | 6.9k | 10 |
| `sequential.jsonl` | 343 | oneshot/verify/debug/debug_view 79 each, typo 27 | easy 287, medium 34, hard 22 | 79 | 22k / 36k | 6.2k | 1.1 |
| `repair.jsonl` | 306 | repair 248, verify 58 | easy 145, medium 86, hard 75 | 248 | 34k / 51k | 9.5k | 9.9 |
| `extend.jsonl` | 80 | extend 40, verify 40 | easy 52, medium 28 | 40 | 31k / 44k | 8.7k | 11 |
| `repair_env.jsonl` (benchmark material) | 133 | repair 65, verify 68 | T0-T3 | 68 | 21k / 69k | 5.9k | 1.0 |
| **total** | **1600** (1467 without repair_env) | | | | | | |

Only 3 episodes were rejected by the export filter in the final runs (all `repair_env`: e.g. `rpe_t3_rom4x4_0`, whose stored broken variant
passes every public vector and fails only hidden ones, so the scripted "run_spec shows the failure" premise does not hold). Turns p50 4, max 6.
Independent replay (`scripts/synth_replay_check.py --per-shard 5`: last `build` source of 30 random rows from all six shards -> `bugs.solution_of` -> full `env.runner.run`
with the eval seed): **30/30 full pass**.

## Nucleation quirk found (affects every layout generator and the router)

Engine placement (`env/sim.compile_cells`, `{simulate=true}` in y, x, z order) corrupts the **shape** of a dust line end / corner when another
dust is placed later about 2 cells east and 1-3 cells north/south of it: e.g. lever (0,1,3), dust (1,1,3), (2,1,3), dust (4,1,4) on a floor ->
(2,1,3) becomes a 4-way cross; re-pasting the settled world (`Placement`) reverts it, so `paste_stable` fails although the behaviour is correct.
First netlist layouts with columns 2 apart: 2/6 paste-stable; with 3 apart: 12/12. Measured hit pattern (offsets of the foreign dust relative to
the corner/end): end (+2, +-1..2), straight (+3, +-1..2), corner N (+1..2, -3 / +2), corner S (+1..2, -2 / +3). Kept as a regression test
(`test_engine_placement_dust_quirk_still_present`); two of the six router failures look like the same effect. Not checked in vanilla.

## How to scale

* Generation is embarrassingly parallel per (bucket, seed); every worker writes its own files, duplicates are prevented by `.sig` claims,
  attempts are appended to one JSONL. 8 workers: about 10 medium tasks/min or 3-5 hard tasks/min on this (shared) machine; more machines =
  disjoint `--start` ranges.
* Cost is dominated by the acceptance gate (8 mutants x a full public+hidden run) and, for episodes, by `submit` (full run incl. determinism
  re-run). Cheaper options if needed: `--mutants 4`, skip `deterministic` in the gate for families already proven deterministic, reuse the
  gate's reference run for the `verify` episode.
* Variety knobs: bucket sizes, gate weights, k up to 6 (random vectors), the router layout, more lib cells (xor2/mux2/half/full adders are
  natural next additions: their ports face other directions, which the bus layout does not handle yet), sequential parameter grids.
* Episodes: `--stride` subsamples tasks, `--kinds` picks kinds; rows are resumable by (task, kind).

## Licence / provenance

Clean. Every task, program and trajectory is generated by this repo's code from this repo's library (`lib/`: hand-authored cells plus four
cells adapted from MIT-licensed redstone-verify layouts, notice kept in their sidecars) and verified by this repo's environment. No LLM output,
no scraped data, no corpus builds. The only non-synthetic material is the `repair_env` shard (our own benchmark references; licence-clean but
**contaminating** for benchmark evaluation). Tool messages are real simulator output; assistant messages are templates (`synthetic_spans`).

## Deviations / open problems

* Library coverage: the netlist generator uses not/and/or/nor/nand only. `xor2`, `mux2`, half/full adders were requested; their ports face
  north/east/south and need another router layout (the bus/tap layout feeds gates from the west only). With the router option they could be added
  once its yield is higher. Not done.
* Extend tasks for adder2 -> adder3 were not built (the T3 adder geometry moves every device with n); netlist extends are.
* Assistant text is templated and the diagnosis uses ground truth of the injected bug; a model trained on it learns the format and the
  causal links, not free-form reasoning. Mix with rejection-sampled real transcripts.
* The netlist layout is one family of geometry (west-to-east buses): a model trained only on it would overfit it. Router layouts,
  rotated/mirrored variants (env/variants.py, A3) and sequential/repair_env shards are the counterweight.
* Vanilla oracle: none of the synthetic tasks were replayed in vanilla 26.2 (the library cells and T3/T4 references they reuse were).
* Hard netlists with random vectors check 24 of 64 rows (+ reversed copy); a wrong build could pass the hidden sample.

## Requests to owners of frozen / other files

1. **`env/tasks/author_t3.py` (counters)**: pass `floor_exists=True` in `counter_body`'s `route` (or strip y=0 writes in the program), so the
   reference *program* is loadable by the harness build tool; today only the stored solution is valid.
2. **A2 router (`simulation/router.py`)**: repros of 4 functional + 2 paste_stable failures in `data/synth/router_failures/*.{json,py}` (all functional
   ones contain a NAND = `and2` -> `not_gate` pair; NAND output stuck at 1). 9 of 24 nets were unroutable at the search limit (gate `B` ports).
3. **`env/sim.py` / `env/runner.py`**: consider placing dust after all other blocks (or a second shape-update pass) in `compile_cells`: the
   engine-placement dust-shape quirk above makes correct layouts fail `paste_stable` and constrains every generator's spacing.
4. **`env/validate_tasks.py`**: an incremental mutant runner (reuse the placed world) would cut the gate (most of the generation time) by several x.
