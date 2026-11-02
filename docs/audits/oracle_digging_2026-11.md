# Oracle audit: "Lean for Minecraft mechanics" (code digging), 2026-11-02

Read-only design audit. I read `docs/PLAN_2026-11.md`, `ORACLE.md`, `ENV.md`, `VALIDITY.md`, `T4.md`, `STATUS_2026-11.md`, `tools/oracle/{trace,gametest}.py` and `tools/oracle/java/OracleBatch.java`, and the local Mojang manifest. I fetched the Minecraft EULA and `fxmorin/carpet-fixes`. **Not read:** the Mojira backend (`bugs.mojang.com/rest/api/2/search` now returns a single-page-app shell, so the API is unknown), the Microsoft Services Agreement, and the Minecraft Usage Guidelines. **Nothing was run** except read-only inspection.

**Bottom line.** Do **not** start open-ended digging now. Run one bounded pilot that pays for itself inside MIRA: **family D, Nucleation-vs-vanilla differential fuzzing**. It has ground truth (vanilla is right by definition), a recall test (the 4 known Nucleation bugs), and its output is MIRA's biggest validity risk (the inner-loop scorer's fidelity). Version differentials (B) come second. Effect discovery (A) comes only after the driver can trace entities and items and an admissibility checker exists.

## 1. Theorem, proof, checker

Lean proves ∀-statements. Here **the game can only witness ∃-statements and test ∀-statements over a finite domain**. Every stored fact must therefore record the domain it was checked over: rotations, origins, seeds, versions. The 17 facts in `docs/T4.md` are tested claims of this kind, not proofs.

- **Theorem**: `∃ B ∈ Adm(V), P ∈ Stim : φ(trace_V(B, P))`, where:
  - `Adm(V)` is the set of admissible builds in version V;
  - `Stim` is the set of player-equivalent stimulus plans;
  - φ is a predicate in a frozen JSON DSL over the trace (below).
- **Differential theorems**: φ = `¬(trace_X ≡ trace_Y)` modulo canonicalisation, where X and Y are two engines or two versions.
- **Proof**: a *certificate* that replays deterministically. It contains:
  - jar sha1 and Nucleation ref;
  - the canonical block list (or the builder program plus its compiled hash);
  - the plan (ops, ticks, preroll, settle);
  - origin and seed;
  - trace hash;
  - a **1-minimal** build: delta-debugged so that removing any block kills φ.
- **Checker**: `OracleBatch`, applied in five steps.
  1. The static admissibility check runs first.
  2. The capture is replayed in an **isolated JVM** (`capture_batch(isolated=True)`), at **2 origins** and **2 rotations** unless the claim is explicitly position-dependent (vanilla update order hashes position).
  3. A **no-stimulus control run** must not satisfy φ (causality).
  4. For finalists, a confirmation on a real dedicated server with a fake player.
  5. The certificate is stored.

  Step 4 matters because GameTestServer + `useBlock` clone + the region wipe + the reflective `RECENT_TOGGLES` clear (`OracleBatch.java:261-285`) are harness, not game.

**Predicate DSL v0.** Extend env Contract v2 port readers into atoms over trace ticks:
- `state(pos) ~ pattern`
- `count_blocks(id, box)`
- `items(id)`, summed over container slots and, once traced, item entities
- `entities(type, box)`
- `first_tick(atom)`
- `changes(pos, window)`

Plus `all/any` over windows (the env `sequences` semantics) and arithmetic comparisons. Use STL-style quantitative robustness (for example Δcount, or a latency margin) as the search fitness, so the signal is graded rather than pass/fail.

Predicates are written by a human or a separate spec agent and **frozen before search**. The searching agent never edits φ (MIRA's hidden-spec principle).

### Task families, ranked by verifiability

| rank | family | predicate | cost per check (measured basis: ORACLE.md) | anti-hacking | novel vs known |
|---|---|---|---|---|---|
| **1** | **D: simulator differential** (Nucleation vs vanilla 26.2) | trace inequality after `settle=place`, on Nucleation-loadable ids only (1023/1286) | vanilla ~5 ms/capture batched (213/s @ 4 JVMs); Nucleation tens of ms. **Effectively free; LLM tokens dominate** | admissible = statically supported (`env.blocks.floating`), canonical states (`env/blocks.canonical`), at-rest before tick 0, `use` ops only. Otherwise known divergences 4/5 (pop-off, `known_shape`) flood the results | signature = (minimised canonical build up to rotation/mirror/translation, block ids and properties at first divergence). Known list = `tools/oracle/circuits.py` repros + Nucleation issues. Vanilla is ground truth, so every confirmed class is a real Nucleation bug |
| **2** | **B: version differential** (26.2 vs 26.3 vs the latest snapshot `26.4-snapshot-6`; later 1.21.x) | trace inequality over the intersection of the two block registries | same per capture, ×2. One-off: ~61 MB jar per version, fetched by the parent (sandbox blocks piston-data). All 26.x jars are unobfuscated (the manifest has no mappings entries). 1.21.x needs Mojang mappings plus remapping: ~1-2 days | same as D, plus registry/datafix differences masked | explained = in the changelog or a Mojira fix-version. **Unexplained diff in a snapshot = candidate regression**: the most community-valuable output, and still machine-checked |
| **3** | **A: effect discovery** ("minimal contraption in V with effect E") | DSL φ: block moved or destroyed, `Δ(items+blocks of X) > 0` (dupes), `first_tick` latency below X, a state reachable only under a condition | same, but long and entity-heavy captures are slower (T4: 4800-tick captures took minutes). **Blocked today**: `OracleBatch.java:273-277` discards entities, and nothing traces item entities | survival allowlist of ids (no command/structure/jigsaw/test/barrier/light/bedrock as a build block); only player-settable properties (derived ones recomputed by `place`); legal container stacks, no custom NBT; no entities at t0; **no `set` ops** (`setBlock` = a command); causality control; conservation across block↔item representations | calibrate on "textbook theorems" (the miniF2F analogue): TNT/rail/carpet dupers, QC/BUD, update detectors, instant wire, piston spitting. Novel = new signature **and** not matched by a search of the wiki, Mojira and carpet-fixes (192 MC ids, but stale: last commit Apr 2024, MC 1.20.4) **and** expert confirmation before any public claim |
| 4 | C: Mojira repro | ticket text → φ, then A | as A | the ticket→φ translation is not machine-checkable; a human must sign φ | is the ticket ID |

C is not its own family. It is A with a human-authored φ. Rank it last.

## 2. Agent architecture

**Node** = (hypothesis text, frozen φ, program [builder Python + plan], observations [Nucleation/vanilla trace summaries, first divergence, robustness], cost so far). The search is best-first with progressive widening. Priority = fitness (robustness) + novelty bonus + LLM promise score − spent cost. A subtree is killed after K expansions without a fitness gain.

**Expansion operators, cheapest first:**
1. **Non-LLM mutation**: `env/mutate.py` operators, same-class block swaps, orientation and delay changes, stimulus timing shifts. These feed a MAP-Elites archive keyed by (block-id set, effect class) to keep diversity. AlphaEvolve-style means the LLM proposes *program diffs* sampled from the archive, and the evaluator keeps the elites.
2. **LLM program edit**: diff on the builder program. This is MIRA project mode reused.
3. **LLM hypothesis**: a new φ-guided root, from the stored facts plus code reading.
4. **Delta-debug minimiser** on every hit.

The LLM is for hypotheses and for interpreting traces. Raw search volume comes from mutation.

**Shared stores:**
- **Facts**: claim, tested domain, versions, certificate ids.
- **Negative results** ("tried X across N contexts, nothing"). Without them an endless search re-mines the same ground.
- **Signature index**.
- **Optional coverage fingerprint**: JaCoCo agent on the server jar records the game methods hit in the divergent ticks. This gives the best dedupe key ("same root cause") and an AFL-style novelty reward. The overhead is unmeasured.

**Scheduling.** The proposed "cheap sim first, then expensive vanilla" cascade is mostly wrong here. Small vanilla captures already cost ~5 ms when batched. Nucleation is least trustworthy exactly in the quirky corners digging targets, and it cannot load 263 ids. The cascade should be:
1. Static admissibility.
2. **Batched vanilla** in a persistent 4-JVM pool (the measured optimum).
3. Isolated-JVM confirmation for hits only.

Nucleation is used only in D and as a pre-filter for long redstone-only captures, on mechanics the fact store marks as agreeing. Budget is accounted per node in dollars (tokens) plus oracle-seconds. Crash-prone families get their own JVM and a timeout; a crash is a *quarantined observation*, never a reward.

**Code reading.** Decompile the unobfuscated jar locally (Vineflower/CFR) into gitignored `data/external/src_<ver>/`. Never commit, publish or redistribute it. The EULA's line is "do not distribute anything we've made", and I found no decompile clause in the EULA text; the MSA and Usage Guidelines were not read. Index it with:
- tree-sitter/ctags plus ripgrep;
- embeddings over method bodies;
- a bytecode call graph (ASM- or WALA-based), exposed as tools: `src_search`, `src_read(Class#method)`, `callers/callees`, `overrides(neighborChanged|updateShape|tick|getSignal|onPlace)`, and `src_diff(v1, v2, pkg)`.

**`src_diff` is the strongest hypothesis generator for B.** Changed methods in `world.level.block`/`redstone` packages become targeted builds, and coverage confirms the changed code was hit. Caveat: pasting decompiled code into hosted-LLM prompts is arguably "giving copies". Keep excerpts short or use a local model for bulk reading.

## 3. Relation to MIRA

**Shared:**
- builder + `blockstates` validation;
- env port readers → DSL atoms;
- `tools/oracle` (driver, `Trace`, `first_divergence`);
- sandbox + broker (`run_baseline_sandboxed.py`);
- project-mode workspace.

**Must diverge:**
- objective: existential, novelty-seeking, no hidden test set;
- admissibility: survival legality matters; MIRA's legality is "pastes and passes";
- entity/item tracing and multi-version oracle work dirs (`oracle_setup.sh` writes one `$WORK/server.jar`, so each version needs its own `ORACLE_WORK`);
- the code-reading index and the tree orchestrator;
- the publication policy.

**Where it lives:** keep the D pilot inside MIRA (`scripts/` + `tools/oracle`), because its output is MIRA validity. **Split to a separate repo when A or B starts**: different audience, Mojang-code-adjacent artifacts, a disclosure policy. At that point extract `tools/oracle` as a pinned dependency.

**Feedback into MIRA:**
- Nucleation bug reports and Rust fixes upstream (MIT);
- a per-mechanic fidelity map that flags env tasks touching unfaithful mechanics;
- new verified facts → `builder_system.md` gotchas + `tests/env/test_physics_facts.py`;
- mechanics-as-tasks (pulse shorteners, instant wire) for T5+;
- hypothesis→build→observe→revise trajectories as SFT data for debugging skill;
- DSL temporal atoms back into Contract v2.

## 4. Staged plan

**Week-1 pilot: family D only**, run with what exists.

| day | work |
|---|---|
| 1 | Differential runner over `tools/oracle/sim.py` + `gametest.capture_batch` + `diff.py`. A grammar fuzzer generates 3-15 loadable, supported redstone components in a 5×3×5 box with random lever/button `use` schedules. A seeded arm mutates env references, library parts and corpus small logic circuits filtered by `env/block_support.py`. |
| 2 | Delta-debug minimiser, signature, clustering. Every hit is confirmed in an isolated JVM at 2 origins. |
| 3 | LLM arm: cheap model, hypothesis + targeted builds from the cluster list. **The 4 known bugs and their repros are withheld from prompts and seeds**; env broken variants with wall torches are excluded from seeds. |
| 4-5 | Recall/novelty report; draft upstream Nucleation issues. **Filing them is outward-facing and needs user approval.** |

**Cost:**
- **Oracle and simulator**: 1M captures ≈ 1.5 h vanilla + ~1 h Nucleation on 16 cores, so **≤3 machine-hours**. The box is shared (~45 GB in use by others), so cap at 4 JVMs.
- **LLM: ≤$10.** The OpenRouter key has ~$3 left: top it up, or run the hypothesis arm from a subscription agent.

**Success criteria (all within 1M captures):**
- the grammar arm rediscovers ≥3 of the 4 known classes from scratch;
- ≥3 **new** minimised classes (≤12 blocks), each isolated-JVM- and 2-origin-confirmed with a one-line mechanic explanation;
- the LLM arm either finds ≥1 class the fuzzer missed or matches it with ≤50% of the captures.

**Kill criterion:** fewer than 2/4 rediscovered and 0 new after 1M captures. That means the pipeline is broken or the space is clean; fix or stop. If the LLM arm shows no advantage, keep only the fuzzer: it is still useful as a MIRA regression gate.

**Milestones, each gated on the previous one:**
- **M2 (B)**: 26.2 vs 26.3 vs the latest snapshot. Requires the jars, a per-version driver compile, and `src_diff` targeting.
  - Success: every diff is explained or flagged.
  - Kill: zero unexplained diffs after covering every changed redstone method.
- **M3 (A prerequisites)**: entity/item counts per tick in `OracleBatch`, the admissibility checker, DSL v1, and the textbook-theorem set.
  - Metric: solve rate per dollar.
- **M4**: open-ended A with the novelty pipeline and expert review; C as human-signed φ.
- **Later**: 1.21.x through mappings, only if TMC partners ask.

## 5. Risks

| risk | severity | mitigation |
|---|---|---|
| Unverifiable novelty claims | high | Three tiers: self-novel (signature), not-found-in-known-lists, expert-confirmed. Only the last is claimed publicly. Mojira search is currently unscriptable (SPA). |
| Harness artifacts mistaken for mechanics | high | `set` ops, wipe/reflection, origin y=100, `useBlock` ≠ player. Mitigate with isolated JVM, 2 origins, causality control, and dedicated-server + fake-player confirmation for finalists. |
| Infinite search with no signal (A) | high | Quantitative robustness fitness, textbook calibration first, K-expansion subtree kill, negative-results store, stop when the marginal discovery rate falls below a threshold. |
| Floods by known divergences | med | Admissibility (support, `place` settle) plus signature clustering; budget per cluster. |
| Mojang terms | med | Local decompile only, gitignored, no published code excerpts, short excerpts to hosted LLMs. MSA/Usage Guidelines unread: check before M2. |
| Safety: crash/DoS (update suppression, lag machines) and dupes on current versions | high | No reward for crashes or lag. Crash/DoS repros are never published; report privately to Mojang/MSRC. New dupes on current release are held for private report / expert review; only patched or community-standard mechanics go into public datasets. |
| Measuring "endless" | med | Unique verified classes per $ and per oracle-hour, cumulative discovery curve, precision (expert-confirmed / claimed), recall on held-out known sets, coverage growth in block/redstone packages, MIRA impact (upstream fixes, facts added). |

## 6. Should it be done now?

**No, except the D pilot.** MIRA's critical path is A1-A5, B1-B2 and training (`PLAN_2026-11.md` day-2 phases). Open-ended digging competes for the same oracle box, agent time and a nearly empty API budget, and it has no path to the main goal beyond fidelity and facts.

The **minimal slice that keeps the option open** is ~3 agent-days:
1. the D fuzzer + minimiser + signature store;
2. a certificate written for every oracle capture: jar sha1, build hash, plan, origin, seed, trace hash;
3. per-tick entity and item-entity counts in `OracleBatch`, about 30 lines of Java;
4. a smoke test that `MC_VERSION=26.3` compiles `OracleBatch` in its own `ORACLE_WORK`.

Defer the decompiled-source index, the multi-version oracle, effect discovery and anything public.
