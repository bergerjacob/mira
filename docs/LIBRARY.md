# Component library, T3 tasks and the compiler track (2026-11)

What exists now (all offline-testable, Nucleation only; the vanilla 26.2 oracle is the referee, numbers below were measured on it):

* `lib/` - **16 hand-authored / adapted components** as sandbox-legal builder parts (`lib/<name>.py`), each with a generated sidecar
  (`lib/<name>.meta.json`: ports, spec, timing, input strengths, verified rotations, oracle verdict, provenance). Specs: `lib/specs.py`.
  Plus **109 corpus circuits** promoted into `lib/corpus/` (MIT, verified in env + vanilla; `lib/corpus/index.json`).
* `env/library.py` - loader (`prelude`, `compile_program`), bench generator, verifier (env + oracle), corpus scan/promotion, T3 reference models.
* `env/tasks/t3/` - **12 T3 tasks** (59 .. 993 blocks) built from the library through `Part` arrays + `wire_path`; `env/tasks/author_t3.py` is their single source.
* `scripts/compile_track_check.py` - Nucleation's netlist compiler checked like an agent build; report in `env/tasks/t3/compile_track_report.json`.
* tests: `tests/env/test_library.py` (88), `tests/env/test_t3.py` (72).

```bash
source .venv/bin/activate
python env/library.py list                                  # catalogue table from the sidecars
python env/library.py verify full_adder xor2 --oracle       # 8 orientations x input-strength sweep in env, then the vanilla oracle
python env/library.py write --oracle                        # regenerate every lib/*.meta.json (about 8 min)
python env/library.py corpus --max-blocks 80 --oracle --promote   # scan data/corpus/index.jsonl, ~3 min env + ~1 min oracle; --reuse skips the env scan
python env/tasks/author_t3.py                               # regenerate env/tasks/t3/*.json (keeps stored broken variants of unchanged references)
python env/tasks/author_t3.py --validate --write-broken --mutants 24 --jobs 12   # acceptance gate (env/validate_tasks.evaluate), ~3 min
python env/tasks/author_t3.py --oracle                      # vanilla cross-check of every T3 reference -> env/tasks/t3/oracle_report.json (~3 min)
python scripts/compile_track_check.py --designs ha,add2,add3,add4,add8 --merge
python -m pytest tests/env/test_library.py tests/env/test_t3.py -q   # 160 tests, ~70 s
```

Using a part in a program (what a reference or an agent in the *component tier* writes; the library source is simply prepended):

```python
from env import library as L
text = L.program(["full_adder"], body)            # prelude(full_adder + its requires) + `def build(b): ...`
build = L.compile_program(["full_adder"], body)   # sandbox=False by default (trusted); sandbox=True works too (tested), lib_root=lib/ for b.use(corpus cells)
```

## Conventions

* Local `y=0` is the support plane, logic starts at `y=1`; every part takes `floor=True` (T3 tasks pass `floor=False` and drop the `y=0` plane because
  the task floor is fixed). Parts never write outside their listed footprint.
* **Ports are dust cells** on the component edge; `face` = side the outside connects to (inputs) / leaves through (outputs), transformed by rot/mirror
  (`b.port(..., face=)` -> `build.ports["inst.port"]`). Outputs are driven at strength 15 unless the table says otherwise; each input has a measured
  `min_in_strength` (weakest dust power at the port with the component still passing, other inputs at 15).
* Verification bench (`env/library.py bench`): inputs are `pad` ports (a redstone block `lead+1` cells outside the port, `lead` dust cells between: lead 14 =
  strength 1), outputs `wire_power >= 1`; the spec is an exhaustive truth table (hidden reversed repeat, shuffled, no reset) or timed sequences.
  A component is **verified** when (a) the identity orientation passes functionally and structurally (all env invariants incl. `paste_stable`, `deterministic`), (b) the
  same holds at the weakest passing input strength, (c) every orientation (rot 0/90/180/270 x mirror none/x = the 8 elements of the dihedral group) was run and the
  **flags** `func_ok_orientations` / `paste_stable_orientations` are stored, (d) the vanilla oracle agrees (tick-identical trace of the whole region and the contract verdict) on all 8
  orientations and on the weak-input bench. Orientations that fail are recorded, not hidden (`orientation_failures`).

## Catalogue (generated from the sidecars; `python env/library.py list`)

Delay = ticks from an input flip to the last change of an output cell (max over all contexts, rise/fall). Sequential cells list `-` (their timing is in the notes).
"Rot OK" = orientations (of 8) that pass functionally / are paste-stable. "Oracle" = bench captures tick-identical to vanilla / captures (all also pass the contract in vanilla).

| component | ports (dir, face) | size | blocks | delay | out strength | min input strength | Rot OK | Oracle |
|---|---|---|---|---|---|---|---|---|
| `not_gate` | A (in, W), Y (out, E) | 4x2x1 | 8 | 2/2 | 15 | A 1 | 8/8 | 9/9 |
| `nor2` | A, B (in, W), Y (out, E) | 7x2x3 | 32 | 4/4 | 15 | 1 | 8/8 | 9/9 |
| `or2` | A, B (in, W), Y (out, E) | 5x2x3 | 24 | 4/4 | 15 | 1 | 8/8 | 9/9 |
| `and2` | A, B (in, W), Y (out, E) | 8x2x3 | 37 | 4/4 | 15 | 1 | 8/8 | 9/9 |
| `xor2` (2 comparators) | A (in, W), B (in, E), Y (out, N) | 5x3x4 | 27 | 6/6 | 15 | 2 | 8/8 | 9/9 |
| `half_adder` | A (W), B (E) -> S (N), C (W) | 5x3x5 | 38 | 10/10 | 15 | A 2, B 3 | 8/8 | 9/9 |
| `full_adder` (comparators) | A (W), B (S), CIN (E) -> COUT (W), S (S) | 9x4x7 | 80 | 14/16 | 15 | A 2, B 2, **CIN 7** | 8/7 | 9/9 |
| `mux2` | A (W), B (E), S (S) -> Y (N) | 5x3x4 | 34 | 6/6 | 15 | A 1, B 1, S 3 | 8/8 | 9/9 |
| `demux2` (1-to-2 decoder cell, enable) | EN (W), A (N) -> Y0, Y1 (E) | 6x2x5 | 46 | 10/10 | 15 | EN 2, A 1 | 8/8 | 9/9 |
| `crossover` (two signals cross) | XW (W) -> XE (E); ZN (N) -> ZS (S) | 5x4x7 | 52 | 2/2 | XE 11, ZS 13 | XW 5, ZN 3 | 8/8 | 9/9 |
| `restorer` (strength restorer) | A (W), Y (E) | 3x2x1 | 6 | 2/2 | 15 | 1 | 8/8 | 9/9 |
| `delay_line(ticks)` | A (W), Y (E) | (n+2)x2x1 | 6..14 | = ticks (2, 8, 20, 36 measured) | 15 | 1 | 8/8 | 9/9 per variant |
| `wire_link(dx,dy,dz)` (`wire_path` turns, climbs, long runs) | A (in), Y (out) | per variant | 8..50 | 1-2 | 9 (6 for 24 cells) | 7 | 8/8 | 9/9 per variant (9 variants) |
| `edge_detector` (pulse limiter) | A (W), Y (E) | 9x2x3 | 43 | Y rises 4 ticks after A, lasts 4 ticks | 15 | 2 | 8/8 | 18/18 |
| `sr_latch` (NOR) | R (W), S (E) -> Q (E) | 9x2x3 | 44 | - | - | 1 | 8/7 | 36/36 |
| `t_flipflop` (master/slave) | T (W) -> Q (E) | 7x2x6 | 66 | Q toggles 6 ticks after the rising edge; edges >= 12-16 ticks apart toggle in Nucleation (spec/oracle use >= 50) | - | 3 | **6/6** (rot90 and rot90+mirror_x fail) | 27/27 |

`wire_link` variants: (6,0,0), (4,0,3), (-4,0,-3), (3,3,0), (4,-2,3), (3,2,-4), (0,0,6), (24,0,0) [repeater after 14 dust], (12,4,12).

Facts the verification forced (all measured; each cost at least one failed attempt):

1. **A dust that is fed from a lever / pad is not the same as the lever.** A lever powers the block it sits on and adjacent dust; a dust powers the block it *sits on* and the block it *points at*,
   and a strongly powered block powers every adjacent dust (also the one below it). Designs taken from lever/lamp layouts therefore needed their `lamp` outputs replaced by
   `block -> repeater -> dust` and a full stone under an output dust became a *leak* (fix: `slab(type="top")` supports, which are not conductors, in `half_adder`).
2. Dust connects diagonally up/down to other dust when the cell above the lower dust is not a conductor: an output dust next to a lower wire silently shorts (`half_adder` C output).
   A conducting block above the lower line blocks that connection (`crossover`).
3. Repeaters make clean input stages: they accept strength >= 1, restore 15 and isolate loops (`nor2`, `or2`, `sr_latch`). Comparator cells subtract *strength*: a weak side input
   breaks them, so every comparator side input comes through a repeater.
4. **Pre-powered comparator/repeater cells are not paste-stable in this simulator.** A design whose *rest* state has an enabled demux/comparator (e.g. a decoder whose root outputs `!A` at rest)
   settles differently under `InWorld` and `Placement` (`paste_stable` fails, wrong lamp lit at tick 0). All library cells and tasks therefore rest with their comparators' inputs low; the decoders carry
   an `EN` lever (a decoder-with-enable, 74138 style) instead of a constant 1.
5. Sequential cells are orientation-sensitive: `sr_latch` rot270 and `t_flipflop` rot90 (and rot90+mirror_x) fail (placement-order dependent rest states); `full_adder` is functional in all 8 but one orientation is not paste-stable.
   The vanilla oracle accepts the rot90 `t_flipflop` that env rejects, i.e. this is a simulator/placement effect, not a property of the circuit.
6. `wire_path` (`simulation.builder`): starting a route at a lever with a height change climbs *diagonally from the lever*, which dust cannot connect to (add one flat cell first);
   a repeater may land exactly on a junction cell (then the branch is dead), so branch points need `repeat_every` large or an explicit repeater placed first; routes ignore obstacles; it adds support
   blocks that are powered by the dust above them and must not touch foreign dust.

## Corpus promotion (`lib/corpus/`, `env/library.py corpus`)

`data/corpus/index.jsonl` rows of `converted/claudestone` and `converted/redstone-verify` with `ml_use == clean` and <= 80 blocks: **369 candidates**.
Combinational truth tables are taken from the claudestone sidecar (`tests[].truth_table`) or evaluated from the Verilog `assign` of the redstone-verify spec; inputs are `pad` ports (or the file's levers as `click`), outputs `wire_power` / `lamp` / `torch` / `powered` of the probe block.

| result | count |
|---|---|
| skipped: no combinational truth table (sequential, counters, pistons, ...) | 178 |
| skipped: multi-position bus port / unsupported probe block | 30 |
| env fail (spec evaluated like our harness: shuffled, no reset; none passes with a reset between vectors either) | 52 |
| **env pass (all invariants)** | **109** |
| of those: vanilla pass, tick-identical | **109 / 109** (one needed an isolated re-run, below) |

The 109 are promoted: `lib/corpus/<source>__<family>__<name>.litematic`, `.ports.json` (positions in `simulation.builder.use()` frame, verified against the probe blocks),
and `lib/corpus/index.json` (ports, truth-table size, provenance: upstream path / spec / commit, **licence MIT (claudestone 88, redstone-verify 20), redistributable, keep the notice**; entries with `ml_use == permission` were not scanned).
Families: crossing 14, not 9, and 8, vertical_wire 8, mux 7, xor 7, delay 5, encoder 5, d_latch 4, signal_strength 4, wire 4, half/full adders, xnor, imply, ... (`index.json`).
Use: `b.use("corpus/claudestone__full_adder__add_carry_cancel_cell.litematic", at=..., rot=90)`; ports are `inst["cin"]`, `inst["cout"]`. They keep their authors' lever/pad and lamp/probe
conventions (outputs are the probe block, often a comparator or repeater) - they are references and footprint baselines (e.g. a 59-block carry cell, 2-block comparator ANDs), not drop-in dust-port cells.
The four rv-derived hand-verified cells (`half_adder`, `xor2`, `full_adder`, `mux2`) carry `provenance.upstream_commit` and the MIT notice in their sidecars.

## T3 tasks (`env/tasks/t3/`, all accepted by the gate)

Reference = builder program over library parts (print it: `python env/tasks/author_t3.py --programs t3_adder4`). Behaviour = `reference_model` (seeded random vectors) plus explicit rows (exhaustive for the adders up to 3 bits,
decoders, parity, majority, ROM; carry-chain corner rows for the 4/8-bit adders). Each has 5 stored broken variants (all fail), the empty build fails, `depends` is explicit (the derived R2 dependencies are wrong for random vectors).
Kill = auto-mutants (24: remove / flip+turn / shift / repeater delay / wire->stone) that fail; survivors are repeater-delay +1 and wire->stone on redundant cells.

| task | what | ref blocks / volume | in / out | vectors (public) | kill | vanilla 26.2 | notes |
|---|---|---|---|---|---|---|---|
| `t3_adder2` | 2-bit ripple adder (+CIN, COUT) | 243 / 1248 | 5 / 3 | 78 (13) exhaustive+model | 18/24 | pass, identical | 2 `full_adder` cells, pitch 16 |
| `t3_adder3` | 3-bit | 368 / 1872 | 7 / 4 | 174 (13) exhaustive | 19/24 | pass, identical | |
| `t3_adder4` | 4-bit | 493 / 2496 | 9 / 5 | 56 (6) corners+model | 19/24 | pass, identical | |
| `t3_adder8` | 8-bit (T3/T4 border) | 993 / 4992 | 17 / 9 | 56 (6) | 20/24 | pass; not tick-identical (transient, see below) | 13 s per full run |
| `t3_decoder2to4` | decoder with EN | 135 / 1560 | 3 / 4 | 29 (6) exhaustive | 16/24 | pass, identical | 3 `demux2` + 2 `crossover` |
| `t3_decoder3to8` | decoder with EN | 367 / 4536 | 4 / 8 | 45 (9) exhaustive | 18/24 | pass; not tick-identical (transient) | 7 `demux2` + 6 `crossover` |
| `t3_counter2`, `t3_counter2_down` | 2-bit up / down counter, lever clock | 62 / 59 | 1 / 2 | 54 (14) model stream | 18/24 | pass, identical | `t_flipflop` chain (+ `not_gate` for up) |
| `t3_counter4` | 4-bit up counter | 130 / 870 | 1 / 4 | 100 (40) model stream | 17/24 | pass, identical | |
| `t3_parity4` | odd parity of 4 | 120 / 1008 | 4 / 1 | 45 (7) exhaustive | 19/24 | pass, identical | 3 `xor2` |
| `t3_majority3` | majority of 3 | 99 / 576 | 3 / 1 | 26 (6) exhaustive | 18/24 | pass, identical | `full_adder` COUT only |
| `t3_rom4x4` | 4 x 4 ROM, EN | 193 / 2280 | 3 / 4 | 28 (6) exhaustive | 17/24 | pass, identical | decoder + planar band of 3 `or2` (words 0001, 0011, 0110, 1100) |

Everything the task list asked for passed; nothing had to be dropped. What did **not** pass on the way and why (kept as design rules above): a decoder with a constant-enabled root (paste-unstable, wrong rest state),
adder bit-slices with long un-restored routes (CIN needs >= 7: the carry link is routed with a repeater every 3 cells), the first T flip-flop counters (the public model stream had to be chosen so that it ends at count 0 with CLK low, see below).

Oracle detail (`env/tasks/t3/oracle_report.json`; vectors held `hold` ticks, long tables sub-sampled to 40 rows): 12/12 pass in vanilla and in Nucleation; 10/12 tick-identical over the whole region.
`t3_decoder3to8` (about 30 of 3200 ticks per capture, in 3 episodes of 10 ticks: a comparator's `powered` flag in the level-0 demux cell, then a glitch on downstream dust) and `t3_adder8` (a transient on a carry wire) differ only in
transient states after simultaneous multi-input changes; settled values and verdicts agree. This is another instance of the comparator-timing differences listed in `docs/ORACLE.md`; it does not affect a settle-then-read spec but would affect a strict tick-window spec.

Needs from files this work does not own (nothing was modified there):

1. `env/models.py`: add `from env import library  # noqa` (or move the T3 models), otherwise the stock `python env/validate_tasks.py env/tasks/t3/<id>.json` raises `KeyError: unknown reference model`. The wrapper `author_t3.py --validate` imports the models first and calls `validate_tasks.evaluate` unchanged.
2. `env/task.py`: `list_task_ids()` / `load_all()` only see `env/tasks/*.json`; T3 lives in `env/tasks/t3/` (load by path works).
3. `env/contract.py`: `ReferenceModel.generate()` restarts the model for the hidden stream although the runner continues the same circuit; sequential models need the state chained (the counter tasks work around it by choosing a public seed whose stream ends at count 0 with CLK low).
4. `tools/oracle/gametest.py`: batched captures produced sporadic vanilla failures (3 cases over 5 batched runs: a `t_flipflop` bench, corpus `wire_analog_comparator`, corpus `wire_repeater_line`) that never reproduce alone; `oracle_check` re-runs a non-clean label alone and records `retried` + `first_attempt`. Root cause not found (machine was loaded; the driver's isolation claim holds in `tests/oracle`).
5. `AGENTS.md` / `docs/ENV.md`: mention `lib/`, `env/library.py`, `env/tasks/t3/`.

## Compiler track (`scripts/compile_track_check.py`)

Question from `docs/audits/oracle_loop_2026-11.md`: does Nucleation's netlist compiler (`Hdl.compile_blif`) produce *working* circuits, and does a "compiler tool tier" make a valid track?
Method: hand-written BLIF (yosys is not installed; `.names` covers: half adder, n-bit ripple adder with XOR3 / majority covers), `Hdl.compile_blif(blif, name, bake=True)`, then the
benchmark's own checks on the compiled schematic: levers -> fixed `click` ports, report probes -> `wire_power` outputs, truth table through `env.runner.run`, direct `TickSimulation`, and the vanilla oracle.

| design | compile | blocks (redstone dust / repeaters / torches) | volume | vs hand build (blocks / volume) | TickSimulation | env runner (all invariants) | vanilla 26.2 | est. vs measured settle |
|---|---|---|---|---|---|---|---|---|
| half adder | 0.02 s | 702 (298 / 26 / 19) | 9240 | **18.5x / 123x** of `half_adder` (38) | 4/4 | 4/4 | pass, tick-identical | 4 vs 26 ticks |
| 2-bit adder | 0.09 s | 3488 (1519 / 129 / 70) | 82128 | **14.4x / 66x** of `t3_adder2` (243) | 16/16 | 16/16 | pass, identical | 12 vs 66 |
| 3-bit adder | 0.19 s | 7446 | 191952 | **20.2x / 103x** of `t3_adder3` (368) | 32/32 | 32/32 | pass, identical | 16 vs 90 |
| 4-bit adder | 0.33 s | 12870 (5670 / 487 / 206) | 347472 | **26.1x / 139x** of `t3_adder4` (493) | **256/256 exhaustive** | 16/16 | pass, identical | 20 vs 116 |
| 8-bit adder | 1.1 s | 42162 | 1174824 | **42.5x / 235x** of `t3_adder8` (993) | 48/48 sampled | not run (size) | not run | 36 vs - |

(vector counts: env/oracle 4 / 16 / 32 / 16 sampled of 2^(2n); the contract's `delays_rt` are *estimated* from the levelisation depth, measured settle ticks are 5-6x larger.) Mutation check: removing a random dust/repeater/torch of the compiled circuit is caught by the env verdict in 6/6 (4/4 for the half adder).

Findings:

* **The compiled circuits work.** They pass exhaustive (n <= 4) or sampled truth tables in TickSimulation, in `env.runner` with every invariant (`at_rest`, `paste_stable`, `order_independent`, `no_popoff`, ...) and in the **vanilla 26.2 oracle with tick-identical traces**.
  The audit's open question is answered: yes. Its failed `Executor.for_schematic` call was a different problem: in nucleation 0.12.0 `Executor.for_schematic` builds, but the first `ex[name] = value` raises `NucleationErrorCode.Simulation`
  for every compiled cell (with and without `bake`, with the contract attached). TickSimulation / MchprsWorld load the same schematic fine, so use the lever/probe coordinates from `compile_blif_report`.
* **Footprint is the discriminator.** The compiler uses a dual-rail PLA layout of coloured concrete + dust: 14-42x the blocks and 66-235x the bounding volume of the hand/library builds, growing superlinearly (blocks/bit: 1744, 2482, 3218, 5270).
  R4 of the existing score for a compiled adder would be about `mean(1/26, 1/139) = 0.02`. As a pure R0-R3 task the track is trivial (one call: 0.3 s, 100% vectors), so it measures the tool, not the agent; as a *footprint* track
  ("how much smaller than the compiler can you build") it is meaningful because the baseline is known and large.
* Valid benchmark track? **Yes, as a separately reported tool tier** with these rules: (1) the compiler is only allowed in the compiler tier; the score is R4-style (blocks and volume relative to the *library* reference), never mixed with the raw / component tiers; (2) the model must supply the netlist (BLIF or
  Verilog -> yosys, which needs `yosys` in the sandbox: absent here) - then the track also tests specification-to-netlist skill and the compiler's input language; (3) cap the size where it stops fitting (the 8-bit adder is 42k blocks: env placement alone takes ~100 s per 13k blocks, the
  oracle ~220 s per capture batch - single-digit-hour runs for T4 sizes); (4) report that compiled builds are not human-friendly (concrete colour codes, no module boundaries).
* Failure modes found: typed `Executor` unusable (above); `paste_safe: false` / `initial_state: unspecified` in the cell contract (the env's paste-stability check nevertheless passed on all four); 26 -> 116 measured ticks vs 2 -> 20 estimated (`delays_rt` is a depth estimate);
  the pure-Python HDL flow needs yosys; sequential Verilog (`always @(posedge clk)`) was not tried (no yosys). Unsupported/odd blocks: none for TickSimulation (coloured concrete is loadable).

## What T4 (8-bit ALU, RAM) still needs

* A bigger verified cell set than this library: a 1-bit **register / D latch cell** with write enable (the T flip-flop is orientation-sensitive and needs >= 50 ticks between edges), a **mux4 / wide mux**, a **bus crossover array** (the `crossover` costs 52 blocks and 7x5; a 8x8 matrix needs the dense variant), 8-bit-pitch `array` cells with a shared clock/enable spine, a **comparator / zero detect**.
* Tools to place at scale: `wire_path` that avoids obstacles and foreign dust (every T3 route is hand-waypointed; four of the T3 bugs were route/leak problems), and a spec-level view of port faces, otherwise a model has to rediscover the rules in "Facts" above.
* Simulator fidelity: tick-level comparator timing differs from vanilla on simultaneous input changes (3to8 decoder, 8-bit adder), and pre-powered comparator cells are paste-unstable; an ALU rests with many enabled comparators, so either fix the settle path or add a "cold start" convention (all enables low at rest) to every T4 spec.
* Throughput: an 8-bit adder reference is 993 blocks and 13 s per full env run (50 s oracle for a 40-row sample); a 1000-block ALU with 256 vectors is ~30 s per run per candidate, an addressed RAM several times that. The mutation gate (24 mutants) already needs `--jobs 12` and 3 minutes for T3.
* Specs: program-level specs (load a ROM, run N cycles) need a clock port kind and a register-readout region (`reference_model` + `mode: seq` + chained state across public/hidden streams, see "Needs" 3).

## Deviations from the audits / open problems

* The audit's "component library" tier is realised as concatenated builder source (`L.program`) instead of `b.use("lib/x.litematic")` cells with `ports.json` sidecars: parametric parts (`delay_line(ticks)`, `wire_link(dx,dy,dz)`, rotations) are plain Python and stay deterministic; `use()` is used only for the promoted corpus cells.
* Hand-authored from scratch: `not_gate`, `nor2`, `or2`, `and2`, `demux2`, `crossover`, `restorer`, `delay_line`, `wire_link`; adapted from MIT sources: `half_adder`, `xor2`, `full_adder`, `mux2` (redstone-verify layouts), `sr_latch`, `edge_detector`, `t_flipflop` (this repo's env tasks). XOR/adder cells use comparators, i.e. they inherit the simulator's comparator semantics (the vanilla oracle agrees on all of them; the side-input-from-lever bug of `docs/ORACLE.md` is not exercised: sides are always repeaters or blocks).
* `min_in_strength` is measured against Nucleation only; the oracle runs the weak-input bench at the weakest passing lead and agrees, but there is no finer sweep in vanilla.
* Oracle flakiness in batched captures (above) is unresolved; every verdict in this document is the isolated re-run when a first attempt failed.
* The corpus scan reads truth tables under *our* harness conventions (pad drive, shuffled order, no reset); the 52 env failures include circuits whose author harness resets between vectors or reads at fixed ticks - they are not necessarily wrong circuits.
* `t3_majority3` has 99 blocks (just under the 100 lower bound of the ladder); `t3_adder8` (993) is above the 500 bound and is the first T4-sized item; `t3_counter2*` are 59-62 blocks and belong to the T2/T3 border (they are included because sequential T3 coverage was requested).
* No `reference_model` task in `env/tasks/*.json` (T0-T2) was changed; the 12 T3 JSONs sit in `env/tasks/t3/`.
