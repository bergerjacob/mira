# Oracle audit: verify (2026-11-01)

Our current verification stack can't be trusted as an oracle: the Phase 0 numbers, the Carpet scoring path and the 14 fixtures all have defects that can flip pass/fail. Nucleation already ships a better oracle (real Minecraft 26.2 running headless, with no mods). Make Nucleation's simulator the scorer, make its vanilla 26.2 harness the referee, and stop using our 1.21.0 Carpet server for functional verdicts.

I read the code and results, ran the Nucleation simulator on a scratch build in memory, and read the upstream Nucleation repo. I didn't touch the live server and made no repo changes.

## Findings

**Blockers**
1. **The "unfair test" check is itself broken** (`scripts/phase0_multi_model.py:1092-1160`). I went through all 25 `unfair_test` runs in `phase0_multi_model_20260721_130540.json`:
   - **10 are fair tests that got mislabelled.** In the redstone-block lamp tasks, the model removes the block and expects the lamp off. The check's "break the circuit" step does the same thing (redstone block becomes stone), so the lamp stays off and the test looks unfair.
   - **4 OR-gate runs** (claude ×3, deepseek ×1) were doomed by `toggle_lever`. It *flips* every lever instead of switching them off, so levers that were off get turned on and the lamp stays lit.
   - **8 are genuinely empty or vacuous** (qwen): with no checks, `all([])` returns true and the run counts as a pass.
   - **1 is real gaming:** minimax's test action overwrote the output lamp itself.
   - **2 are unclear** (gemma, and one qwen OR).

   So only about 9 of 25 are actual model gaming or empty tests. The 17/120 (17.5%) figure is unreliable either way, because the server checks are also unreliable (finding 3). As many as about 31/120 runs might have been real passes, but nothing has confirmed that.
2. **The model's own test can write any block, anywhere** (`phase0_multi_model.py:1036-1048`), including the output it is being scored on. Tests may only drive declared input ports.

**High**
3. **The checks force updates that real play wouldn't produce.** `_staged_tick` (`phase0_multi_model.py:887-927`) and `scripts/verify_procedural_generators.py:108-150` send a block update to *every* block in the circuit after each action. That can hide missing-update bugs and make broken circuits pass. The procedural generators' "99.2% self-verify" was measured this way, so those labels need re-checking.
4. **The integration-test builder uses the scan our own docs call dangerous.** `build_circuit` (`tests/integration/conftest.py:377-391`) sets `force_update_region=True`, which runs `mira_api update_region`. AGENTS.md says that scan gives false positives on negative tests.
5. **The lever toggle never reaches far neighbours.** The `toggle_lever` docstring (`conftest.py:248-262`) says it emits redstone updates; `mira_api.sc:91-128` says it does not, and the caller only steps 5 ticks. Blocks next to the lever's attached block (e.g. the torch in a NOT gate) never update. That plausibly explains why `test_not_gate_functional` fails.
6. **`press_button` is broken** (`mira_api.sc:133-146`). `execute positioned … run player Steve use once` doesn't aim the fake player at the button; it uses whatever Steve is looking at, if Steve even exists.
7. **The fixtures are wrong, not just sloppy.**
   - `tests/fixtures/circuits/and_gate.json`: the second lever at (0,0,2) touches nothing, and there's no test with one lever on, so it can't tell an AND gate from a plain wire.
   - Every `lever[facing=east]` defaults to a wall lever attached to air at x−1, i.e. floating.
8. **Version mismatch.** Nucleation's simulator is verified against MC **26.2**, and it holds schematics in memory at DataVersion 5023 (26.3) (`dataconverter/README.md:12`; `gametest_snbt` emitted 5023 in my test). Our server is 1.21.0. The spike's parity numbers compared two different game versions.

**Medium**
9. `evaluation/test_circuits/*.json` checks are prose only; 9 of 12 have no reference build. `t_flip_flop` uses an invalid `redstone_torch[facing=east]`. Archive them.
10. `training/generators/base.py:8-10` bans repeaters, comparators and pistons only because the old stimulus couldn't drive them. Lift the ban.
11. Matching `"PASS"` in RCON text responses (`conftest.py:262`, `phase0_multi_model.py:949`) is fragile.

## Decisions

### 1. How specs are written
- **Only the task author writes the scored spec.** That's a human or a generator, never the model being tested. The model sees port names, positions and the behaviour description, plus a few public example vectors. The checker keeps hidden vectors and a hidden seed.
- **Contract v2** extends the existing `VerifyContract` / fixture JSON:
  - **`ports`**: name, position on the task-fixed bounding box, direction, and kind.
    - Input kinds: `click` (lever or button, via the simulator's `use_block`) or `pad` (the harness places or removes a redstone block in an empty cell).
    - Output kinds: lamp `lit`, wire/comparator power ≥ k, piston `extended`, or a region predicate.
  - **Multi-bit buses** use Nucleation's `IoLayout` / `TypedCircuitExecutor`.
  - **`behaviour`**, one of:
    - `truth_table`: exhaustive up to about 10 inputs, applied in a random order with no reset between vectors (this catches leftover state).
    - `sequence` (timing diagram): stimulus at tick t, expected output within [t1, t2].
    - `reference_model`: a Python function or state machine plus random sequences with a fixed hidden seed (adders, CPUs).
  - **`invariants`**, checked on every build for free:
    - The build loads at rest, i.e. quiescent (nothing still changing) under `InWorld`.
    - Paste-stable: settling under `Placement` gives the same result as `InWorld`.
    - Deterministic: two runs give the same trace.
    - No oscillation unless declared.
    - Repeatable: running the stimulus cycle twice gives the same outputs.
    - No collateral damage outside the bounding box or declared moving regions.
    - No forbidden blocks (command, structure or test blocks).
- **Specs are validated when the task is created.** The reference build must pass and at least 3 broken variants must fail. This replaces the runtime "unfair test" heuristic.
- **Scaling from a lamp to a 1000-block computer:** the scored spec stays at the top-level ports. Big designs are tested by programs: load a ROM or program, run N cycles, check outputs or optional probe ports. Specs on sub-modules are debugging aids the agent may write, never scored.
- **Product mode** (free-form requests): the agent proposes a contract and it is frozen before building. It must pass non-vacuity rules: at least one stimulus, every output seen in both states, no writes to outputs. It must also kill at least k broken variants of the agent's own build, and a human approves it.

### 2. Simulator vs live server
- **Inner loop and RL reward:** Nucleation's `TickSimulation`, settled with `run_until_quiescent` and an explicit tick budget. A lever→lamp build behaved correctly in my test (instant on, 4-tick off, wire power 14).
- **MCHPRS fast mode:** only for bulk random-vector sweeps on large logic designs, after the main simulator agrees with it on a sample.
- **Settle rule for compiling builds:** build program → place every block through the engine (`{simulate=true}` / `Placement`) → run to quiescence → save the at-rest `.litematic` with block entities. Score under `InWorld` and require the paste-stable invariant.
- **Oracle: Nucleation's `tools/gametest`.**
  - It runs Mojang's in-process GameTestServer on the unobfuscated 26.2 server jar, with no mods and no EULA. It needs Java 25, which this machine has (25.0.3).
  - `TraceCapture` provides `--use` (the same code path as a real click), `--at T:pos:redstone_block` scheduled writes, `--probe` and per-tick traces. Upstream already validates every test against a broken control.
  - Gaps: `--use` takes one position (multiple ticks are fine), and each capture is a JVM launch. Multi-input specs either use `pad` ports or need a small Java patch for repeated `--use`.
  - It's cheaper than repairing our Carpet stimulus and matches the simulator's version.
- **Cross-check policy:** run the oracle on every fixture and generator output, on 2–5% of agent outputs, and on every pass behind a headline benchmark number. Any disagreement quarantines that mechanic or task family until resolved, and only the oracle's verdict counts. The capture becomes the upstream bug report.
- **Version:** pin **26.2** for simulator, oracle and saved schematics. Move the Carpet server to 26.2 later, and only for human viewing, Litematica paste receipts and farm soak tests. Drop carpet-tis `manipulate`, the `redstone_update` / `update_region` hacks and `_staged_tick` from all scoring paths.
- **Carpet fake player:** needed only for live demos and farms. Use `spawn at` + `look at` + `use once`, with the bot within reach of each port.

### 3. Score breakdown
Each tier gates the next:
- **R0, compiles (gate):** the program runs, stays within the block budget and bounding box, and the simulator loads it (an unknown block refusing to load fails here).
- **R1, structural:** at rest, paste-stable, no blocks that pop off during placement, no forbidden blocks.
- **R2, connectivity:** a path from each input port to each output (`RedstoneGraph` / `conduction_trace`). This is a cheap, dense learning signal.
- **R3, behaviour:** per output, use *balanced* accuracy rescaled so that a constant output scores 0; take the minimum across outputs. Add a timing/latency tolerance and the invariants. Plain fraction-correct is gameable: a constant 0 scores 75% on an AND gate.
- **R4, efficiency:** blocks, volume and latency relative to the reference build, counted **only after R3 fully passes**. Phase 0's "100%" was really just block count.
- **Suggested RL reward:** 0.1·R0 + 0.1·R1 + 0.1·R2 + 0.6·R3·[R1] + 0.1·R4·[R3 = 1]. Rely mostly on the binary all-pass, and report oracle-confirmed passes separately.
- **Harness feedback:** structured diagnostics (first failing public vector, port waveform, silent components, structural errors). Never reveal hidden vectors.

### 4. Builds that depend on mobs or items (farms, most of the corpus)
- **Exclude whole-farm throughput** from scored tasks and RL. Nucleation's simulator refuses mobs with AI, and farm rates are random.
- **Partially test farm sub-systems** (sorters, hopper and dropper chains, flush clocks, minecart transport, kill-trigger circuits). Items, hoppers, droppers and minecarts are simulated and can be checked with item-injection stimulus.
- **Optional live soak test:** Carpet `hopperCounters` and `tick sprint` with a fake player for spawn range, giving items per hour. It's a demo metric only and never gates anything.
- **Tag the corpus automatically:** load each file into the simulator. Refused by name means entity-dependent, and that becomes a free label.

## Build order
1. **(2 h)** Re-label the Phase 0 results with the breakdown above. Retire model-written specs from scoring and mark 17.5% as unreliable.
2. **(1.5 d)** Contract v2 schema and a checker on the simulator: ports, `use_block` / pad stimulus, quiescent settle, vectors and sequences, invariants, scores R0–R4, diagnostics.
3. **(0.5 d)** The at-rest compile step. Regenerate the 14 fixtures through it and fix `and_gate` (including a one-input-on vector).
4. **(1–2 d)** Wire up the vanilla 26.2 oracle (run `tools/gametest`, adapt contracts to `TraceCapture` arguments, possibly the multi-`--use` patch). Cross-check fixtures and generator outputs. Gate: at least 95% agreement plus a list of mechanics where they diverge.
5. **(1–1.5 d)** Task ladder with author-written specs:
   - port the generators to contract v2 and lift the component ban;
   - add a T flip-flop, pulse extender and clock;
   - add a 2×2 piston door with region predicates and repeated cycles;
   - add 4- and 8-bit adders with a reference function and broken-variant validation.
6. **(0.5 d)** Feedback renderer and reward module, then rerun the 0/1/3-round iteration-lift measurement.

**Cut:**
- the Carpet server as a functional oracle;
- the `redstone_update` / `update_region` / `_staged_tick` paths, `mira_api` verification extensions and `teacher_client` model-written contracts in scoring;
- `evaluation/test_circuits` (archive);
- fake-player work, upgrading the live server, MCHPRS fast mode, and farm simulation until step 6 is done.

## Risks
- The oracle is upstream code: an alpha-quality 26.x harness with a single `--use` position and one JVM per capture.
- The corpus spans many versions; migrating to 26.x is lossless forward, but redstone behaviour may still differ.
- My Phase 0 re-labelling is from reading the logs. Nothing confirmed whether those circuits actually worked.
- The sticky-piston disagreement in the spike is unexplained. Vanilla `/setblock` does notify neighbours, so "setblock can't power it" isn't proven; re-check it on the oracle.
