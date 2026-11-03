# Oracle audit round 3 (2026-11-03)

Read-only audit of everything since round 2. Read: EXPERIMENT_02/03, VALIDITY §9-13, PRODUCT, ROUTER, T5, DATA_ENGINE, DIFF_FUZZ and TRAINING. Checked against: `env/tasks/t5/variants_report.json`, `data/oracle/sessions_r3/r4.json`, `paste_receipt_server*.json`, `data/training_runs/snap_synth_sft_1`, `data/synth/tasks/sequential` and the git timestamps.

Commands run:
- `scripts/synth_replay_check.py --per-shard 3 --seed 11`: 18/18 rows pass under canonical placement.
- A solution diff of `sq_counter4` against `t3_counter4`.

## 1. What is still wrong, overclaimed or confounded

**H1 (high): synthetic training data contains benchmark references, and only one of the leaks is flagged.**
- `data/synth/tasks/sequential/sq_counter4.json` equals `t3_counter4`: the same 130 cells and the same description.
- `sq_counter2` and `sq_counter2_down` are made the same way (`training/synth/sequential.py:119`, `t3_counter`). `sq_shift02..10` come from the `t4_shift` generator.
- All of these are in the SFT snapshot `data/training_runs/snap_synth_sft_1`: 23 counter/shift episodes, plus 6 `rp_sq_shift` repair rows.
- `DATA_ENGINE.md` flags only `repair_env`.
- `TRAINING.md` (step 2) also puts `build_fixture.py reference` episodes of the env tasks into the SFT mix, while T0/T1 are the proposed evaluation. `data/training_runs/fixture_reference_t0.jsonl` already holds 20 such rows. That is training on the test set.
- The netlist tasks also reuse T3's `Plan3` scenery and the same lib cells. That is a milder, function-level overlap.
- **Fix:** keep a contamination ledger (solution hash plus generator lineage per task) and evaluate only on held-out families and randomised variants.

**H2 (high): headline numbers are stale after the switch to canonical placement.**
- `EXPERIMENT_02.md` still says "All eight pass". Under canonical placement `t3_counter4 …af52` fails 100/100 (VALIDITY §13). T0-T4 is **7/8**.
- `EXPERIMENT_03` "6/7" is **6 passes of 8 cells**: `t5_lock4` raw crashed (an infrastructure failure, not excluded by design). Each cell is n=1 with one model family.
- `sessions_r4.json` (15:51) and the 1.21.0 paste receipts (16:53) predate commit `c557305` (19:20). They verify **legacy** handovers. VALIDITY §13 has a canonical re-gate (0 env/vanilla disagreements over 18 sessions), but no committed artefact backs it; I found only `_cache` pickles.
- Verified true: 336/336 variants pass and every stored broken variant fails (`variants_report.json`, `placement: "canonical"`). Note the circularity: canonical placement was chosen partly because it makes the references pass. Its justification is that it agrees with vanilla structure paste, and that only holds in that sense (see H3).

**H3 (high for the product): "pastes exactly" is only shown for one paste procedure.**
What has been shown:
- The receipt pastes with `tick freeze` and `carpet fillUpdates false`, then reads back. That is a static check.
- On 1.21.0, *behaviour* was checked only for `t0_and` and `t1_clock`.
- The `.litematic` (DataVersion 5023) was never loaded by the Litematica mod on 1.21.0.

Canonical placement can still pass builds that fail in real Minecraft:
- **Bistable boot state:** the counter4 session showed that boot state depends on how the build is placed. Users get other placement procedures: Litematica command paste on a server without Carpet (setblock *with* updates), or survival hand-building from the Litematica overlay in the user's own order.
- **Position-dependent timing:** `t3_adder2 …4b87` passes only at env's absolute cells with env timing (VALIDITY §9). The second-origin request (§10 item 5) is not done.

What is fine:
- Lone dust written as a cross matches hand placement, so it is benign.

Open decision:
- The target version is undecided: a 1.21.0 server, the 26.2 oracle, and Nucleation's 26.3 label. Pick one.

**M1: the simulator is wrong on mechanical builds, and agents iterate against it.**
- DIFF_FUZZ F4 (piston-moved blocks keep their state, 65 cases), F1 (copper bulb) and F7 (observer) are all divergences in powered state.
- Even near-reference mutants diverge in 1.1% of captures.
- Doors and piston logic therefore get possibly wrong feedback, and only an *author-run* oracle replay catches it.
- Project mode's `paste-check` checks placement only, not behaviour (PRODUCT.md "Deviations").

**M2: the benchmark is self-referential.**
- Tasks, references, library, specs, synthetic data and router all come from one pipeline. Strong agents saturate it.
- T5 kill rates are 17-19 of 24 mutants. For ≤3-input tasks, the T0-T3 hidden rows are still the public rows reversed.
- The docs contradict each other on alu4. `T5.md` says the router's alu4 fails the env; `ROUTER.md` says it is a full pass on 128 of 2048 sampled rows. It never became a task.
- Router bias today is negligible: no benchmark reference is router-built; only the 9 `netlist_routed` synthetic tasks are. That changes if alu4 or RAM references come from the router: R4 (block ratio) and the scenery layout would then favour router users. Keep references router-independent, or score R4 against the best known build.

**M3: T5 public/hidden independence is OK.**
- Different event lists from the at-rest checkpoint, held-out comb rows plus random multi-input vectors, and submit-once.
- The `seq_detector` hidden fail shows the hidden checks have teeth.
- Not exercised: same-tick presses, short gaps.

**L: synthetic episode tool text was produced under legacy placement** (15:29-16:12). The references still pass under canonical (my replay: 18/18). The text is templated and the geometry comes from a single family.

## 2. Highest-value next three steps
1. **Free-form project-mode pilot (no preauthored spec).**
   - Project mode has never been driven by an agent, and in product mode `test` checks the agent's *own* spec. That brings back Phase 0's unfair-test confound (25 of 120 runs).
   - Protocol: 3 requests × 3 fresh agents (2 Claude-class plus 1 other family): T flip-flop plus 2x2 door; 4-bit counter with reset and lamps; 8-bit ALU with flags (~3-6k blocks).
   - The agent gets only project mode and the natural-language request. It must write `spec.json` with labelled ports, then build, test, export and paste-check.
   - Afterwards we author a hidden spec bound by port label and replay it in vanilla at a random origin and rotation.
   - Measure:
     - spec fidelity: P(hidden pass | self pass);
     - mutation kill of the agent's spec on its own build;
     - vanilla pass;
     - tokens, wall time, and a friction log;
     - questions asked of the user.
   - Add first: `spec-check` (vacuity, IO coverage, mutation score), a `spec-show` truth-table/timing render for user sign-off, vanilla behaviour replay inside `paste-check`, and `probe`/`trace --set` in the sandbox.
2. **Missing tiers:**
   - free-form (above);
   - large non-repetitive, at least 5k blocks: ALU8 with flags, RAM 4x4 or 8x4, a CPU datapath slice, a 7-segment decoder;
   - mechanical, judged by vanilla verdicts only: 3x3 and flush doors, piston logic;
   - modify an existing `.litematic`;
   - every episode on a random variant plus a second-origin hidden run plus an "update-paste boot" invariant (or a required reset).
   All of these with a decontaminated ledger.
3. **Hierarchy and scale tooling:**
   - Per-module test cache and incremental tests: today a 1.5k-block `test` takes 36-54 s and nothing is cached.
   - A negotiated-congestion router plus a floorplanner: 50 nets in 32x32 route only 50-66%.
   - Clock-skew and delay balancing: the lock4 and seq_detector traps were both skew.
   - Array generators (register files, RAM) and netlist→layout as a tool.
   - Gate each size step (1k→5k→20k blocks) with a vanilla pass.

## 3. Training
- **It is unlikely to beat frontier + harness for this goal.** Qwen3-4B scores 5-10% on T0-T1. Frontier agents clear T0-T5 at about 13/16 cells. The binding constraints are specs, layout and tools, not weights.
- Train only for a local or cheap agent, as a research artifact, or if pilot 1 shows a skill that tools cannot supply.
- **First use of HPC: not training.**
  - Serve 2-3 large open-weight models (vLLM; MoE is fine for inference) through the harness on T0-T3 random variants with 3 seeds, to find the best open model and its 10-90% band.
  - Use the HPC CPUs for oracle gating at scale.
- **Then SFT** a dense 8-14B model, or 32B with QLoRA:
  - Data: rejection-sampled passing trajectories of that open model (licence-clean) plus decontaminated synthetic data. Drop `sq_counter*`, `sq_shift*`, `rpe_*` and the reference fixtures; cap templated synthetic data at ≤40% of trained tokens.
  - Group the split by base circuit.
- **Evaluate** on held-out families (T1 timing, T3 counters, T5) and randomised variants, with oracle-gated passes, pass@1 × 3 seeds, against the base model and frontier cost per solve.
- **GRPO** only in the 10-90% band, with R2 weight 0.
- **Stop** if SFT is below base +15 points or below 30% on held-out T0-T2 variants.

## 4. Single biggest risk
**The spec gap.** Every positive number assumes a hidden spec we wrote, with fixed scenery. The product has neither. If agents cannot write, and users cannot confirm, a correct spec for a free-form request, nothing verifies "any size", and the agent's self-test passes are unfalsifiable. That is the Phase 0 confound at product scale. Second: Nucleation's divergence on mechanical contraptions.

## 5. Go/no-go for the next milestone
- **GO (harness product)** if all of these hold:
  - In the free-form pilot, ≥6 of 9 handovers pass the label-bound hidden spec in vanilla at a random origin and rotation.
  - No more than 1 of 9 is a self-pass with a hidden fail.
  - Agent specs reach mutation kill ≥70%.
  - At least one build of ≥5k blocks with non-repetitive structure passes in vanilla in under 2 h of agent wall time.
  - A canonical-era paste receipt is re-run on the chosen version, including a Litematica file load and a with-updates paste.
- **NO-GO / rethink** if:
  - spec fidelity is below 70%, in which case spec authoring and review tooling comes before more builder tooling; or
  - the vanilla gate disagrees with the env on more than 5% of mechanical builds, in which case vanilla-in-the-loop becomes required for those.
- **Training GO** only if the HPC open-model scan finds a model in the 10-90% band and the decontaminated eval set exists. Otherwise do not train.
