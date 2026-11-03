# Differential fuzzer: Nucleation vs vanilla 26.2 (family D pilot)

Implements the week-1 pilot of `docs/audits/oracle_digging_2026-11.md` section 4. Vanilla (the in-process GameTest oracle,
`tools/oracle/`) is ground truth; every confirmed divergence is a Nucleation 0.12.0 deviation, a harness artefact we
found and filtered, or a known interaction gap. Nothing has been filed or published anywhere.

Code: `scripts/diff_fuzz/` (fuzzer), `tests/fuzz/test_diff_fuzz.py` (39 tests, 2 live), outputs under `data/fuzz/` (gitignored).
The fuzzer imports `tools/oracle` read-only. It does **not** use `env/sim.py` placement: block-state canonicalisation and
static support come from `env/blocks.py` (registry only); "can Nucleation load this id" is `scripts/diff_fuzz/loadable.py`
(through `tools.oracle.sim.run_plan`, cached in `data/fuzz/loadable.json`). The *seed pool* (`seeds.py`, cached in
`data/fuzz/seeds.json`) is built once from env task references, `lib/` parts and `data/corpus/index.jsonl` small logic circuits
(`small_logic`, `sim_ok`, <= 40 blocks); rebuilding it (`python scripts/diff_fuzz/seeds.py`) does touch `env.runner/library`.

## Design

| file | role |
|---|---|
| `common.py` | `Case` = blocks + `use` ops + ticks/preroll/settle/origin (+meta); stable key, JSON |
| `gen.py` | grammar generator: 3-15 components in about 5x3x5, 24 component kinds (wire, repeater delay/facing, comparator compare/subtract, torch/wall torch, lever/button on floor/wall/ceiling, solids incl. slime/honey/redstone block/lamp/target/slab, pistons, observers, note block, rails, hopper/dropper/dispenser/chest/barrel with small item lists, copper bulbs, trapdoor, fence gate). 70% of placements are adjacent to existing parts. Support planning writes nothing until the plan is valid |
| `seeds.py` | mutation arm: seeds reduced to the vocabulary (dynamic props reset, wire bare), then 1-3 edits (remove, turn, delay/mode, same-class swap, add, shift, add input) + re-validation |
| `engine.py` | same Case through `tools.oracle.sim.run_plan` and `gametest.capture_batch` (failed batches are bisected, a failing job becomes an `EngineError`, never a verdict) |
| `canon.py` | full-region per-tick trace -> flat form clipped to build box + 4 margin, air variants merged, omitted properties filled from the registry; `first_divergence`; dihedral-group canonical build (4 rotations x mirror, then translation) and its hash |
| `fuzz.py` | resumable driver: deterministic chunks of 1200 cases under `data/fuzz/<run>/chunks`, 4 worker processes, semaphore of **3 JVMs**, arm cycle 6 grammar : 2 corpus-mutation : 1 env : 1 lib, random origins (even x/z in +-80, y in {80, 96, 100}) |
| `minimise.py` | ddmin over blocks (with dependency closure, so every candidate stays admissible), then ops, then run length/preroll, second pass; many minimisations advance in lock step so each round is one fat vanilla batch over 3 JVMs. A reduced case keeps the divergence if phase is equal and the cell-label sets overlap |
| `signature.py`, `classify.py` | signature = (symmetry-invariant build hash, orientation-free divergence label such as `comparator[powered]` or `air!=lever`, phase, component ids); fine class key = phase/label/non-generic ids; known-class detectors; root-cause **families** (rule based, by hand) |
| `confirm.py` | confirmation + certificate (below) |

Admissibility (what "valid" means, all enforced by `gen.bad_attachments`): canonical states, every id loadable by Nucleation, every attachable
component supported, `use` ops only (no `set` ops), no entities at t0, item lists only in containers that keep them in containers. Three
admissibility rules were **found by the fuzzer itself** and are harness artefacts, not simulator bugs (see false positives).

Confirmation protocol, per class representative: isolated JVM at the primary origin and at a second origin, two other orientations
(rot 90, mirror-x + rot 270) via the same blockstate tables as `env/variants.py`, and a no-stimulus control (ops removed; for ops-free cases the
control is not applicable and `causal` is reported `null`). Also checks batched trace hash == isolated trace hash. Certificate JSON per
representative (`data/fuzz/pilot/certs/<id>.json`): jar sha1 `823e2250...` (from `oracle_versions.json`), MC 26.2 / DataVersion 4903, Nucleation python 0.12.0 and
the oracle's pinned ref `346733e`, the minimised build, plan, both origins, first divergence, signature, per-run vanilla/Nucleation trace hashes,
verdict. Replay: `python scripts/diff_fuzz/confirm.py replay data/fuzz/pilot/certs/<id>.json` (compares both trace hashes).

## Commands

```bash
python scripts/diff_fuzz/fuzz.py run --run pilot --chunk-size 1200 --workers 4 --budget 380   # resumable, ~7 min per call
python scripts/diff_fuzz/fuzz.py status --run pilot
python scripts/diff_fuzz/fuzz.py recall --run pilot --max-chunks 3                            # known_shape + pop-off arms (separate runs)
python scripts/diff_fuzz/minimise.py --run pilot --per-label 3 --max-cases 1600 --budget 400  # resumable
python scripts/diff_fuzz/classify.py --run pilot                 # signatures, clusters, known filter -> classes.json
python scripts/diff_fuzz/classify.py --run pilot --families      # root-cause families
python scripts/diff_fuzz/classify.py --run pilot --confirm-families   # isolated confirmation + certificates
python -m pytest tests/fuzz -q
```

## Results (run `pilot`, 2026-11-02)

**Volume and throughput.** 631,200 captures (536 chunks; 22,612 = 3.6% divergent in the first-divergence sense; 0 vanilla errors, 0 Nucleation errors),
plus 7,200 in the two recall arms. About 1.6 h of fuzz wall time in ~7-minute calls under the 3-JVM cap, plus minimisation/classification/confirmation
(~35 min). The first call ran at 30 captures/s, later calls at 150-196 captures/s: the box was heavily loaded early (load average 25-35 on 16 cores) and
the batch size was raised from 200 to 1200 jobs per JVM (boot 2.5-14 s is the dominant cost of a batch). Average ~110 captures/s, **not** the 213/s
of the idle-box benchmark; and the 1M target was not reached (63%). Per-capture cost: ~40-55 ms vanilla (tick loop, 59 ticks average) and
~5-10 ms Nucleation. Captures per arm: grammar 381,600; corpus mutation 124,800; env mutation 62,400; lib mutation 62,400. Divergence rate: grammar 3.8%, corpus 5.3%, env 1.1%, lib 1.1%.

Minimised: 741 of the 22,612 divergent cases (a stratified sample: smallest 3-4 per fine label, diversified by seed) -> 4 unstable on re-run
(dropped), 737 classified into 146 fine classes. 859 further minimisations were still running when the time ran out
(`data/fuzz/pilot/min/m_*.json`; resumable). **All class counts below are counts in this sample, not population frequencies.**

### Recall of the known Nucleation divergences (withheld from seeds; seed pool excludes `REPROS` and env broken variants)

| known class (docs/ORACLE.md) | found from scratch? | evidence |
|---|---|---|
| 1 comparator side input from lever | **yes** | 27 minimised members (grammar 12, corpus mutation 15), smallest 5 blocks; the detector also matches the generalisation to buttons and torches on a comparator side (not verified individually in vanilla beyond the two probes in the run, see risks); cert `a8c4cf373f90` |
| 2 dropper false QC trigger | **yes** | 19 members (grammar 12, corpus 7), smallest 3 blocks; cert `5cb9ad4ae978` |
| 3 torch burnout clock | **yes** | 31 members (grammar 21, corpus 9, env 1), smallest 4 blocks (torch + wire + two stones); detector = vanilla toggles >= 4 and counts differ; cert `01dc6d0fd85d` |
| 4 unsupported component at placement (pop-off) | **by construction only** | the main arms are admissible, so *static* pop-off never occurs there. Arm `recall_popoff` (support removed from a valid build): 3600/3600 diverge. Found from scratch is the *dynamic* variant: a piston moves a block and vanilla pops the wire/lever/torch/button on it, Nucleation keeps it: 407 members (kind `popoff_dynamic`), smallest 2 blocks (lever + piston); cert `75e7691ea231` |
| 5 `settle=known_shape` | **yes, weakly** | arm `recall_known_shape`: 188/3600 divergent; of 150 re-run under `place`, 131 still diverge (ordinary classes), 19 diverge only under `known_shape` (mostly `hopper[enabled]`) |

So 3/3 of the stimulus-driven known classes and the dynamic pop-off were rediscovered from scratch (success criterion >= 3 of 4 met, counting classes 1-3 plus dynamic 4);
class 4 proper and class 5 were only measured with dedicated arms and class 5 only weakly.

### New classes (<= 12 blocks), as root-cause families

146 fine classes collapse into seven root-cause families by hand-written rules (`classify.FAMILIES`) plus the known ones. **One representative per family is
confirmed** (isolated JVMs, 2 origins, 2 orientations; all 12 representatives reproduce in all runs). The hypotheses are mine, from reading minimal builds and a few
hand probes (named below); none was checked in decompiled vanilla or in the Nucleation Rust source.

| family | cases / fine classes in the sample | smallest rep (blocks) | cert | divergence in the rep | mechanic hypothesis |
|---|---|---|---|---|---|
| F1 copper bulb | 108 / 50 | 3: observer, copper bulb, piston above | `c883f189cb45` | piston extends in Nucleation at tick 1, not in vanilla | Nucleation lets a (waxed) copper bulb pass power to its neighbours like a conductor block (probe with a use op: lever on a bulb lights an adjacent lamp in Nucleation only; observer above stone, glass, iron block, target, lamp agree). Second symptom: a comparator reading a bulb that is `lit=true,powered=false` reads 15 in vanilla and 0 in Nucleation (analog output follows `lit`, Nucleation seems to use `powered`) |
| F2 rail shape | 27 / 21 | 4: two powered rails (perpendicular) | `70b08d04f71d` | `shape` differs at placement | vanilla re-shapes rails to connect to neighbouring rails and slopes during the placement pass; Nucleation keeps the stated shape (probes: a lone rail agrees; two perpendicular rails or a rail next to a higher rail diverge). Derived state not recomputed |
| F3 note instrument | 4 / 2 | 2: iron block + note block | `0cde4c5746da` | `instrument` = `iron_xylophone` vs `harp` | the derived instrument is not recomputed from the block below at placement (probe: 15 of 17 block types diverge; stone, dirt, redstone block map to harp). Cosmetic for redstone, matters for any reading of the property |
| F4 piston movement | 65 / 51 | 3: piston, lamp, button on the lamp | `f0817b8ae794` | after the piston pushes the lamp up, vanilla's lamp is unlit at tick 13, Nucleation's stays lit | what moved blocks re-evaluate: a lamp (and other powered-state blocks) pushed away from its power source is re-evaluated in vanilla but keeps `lit` in Nucleation; the same family contains observers not noticing a piston-moved block, hopper `enabled` and wire shapes next to a moved block. Mixed bag, probably several bugs, not separated |
| F5 comparator | 8 / 8 | 6: repeater, subtract comparator, observer, redstone block | `6bc4080de60b` | vanilla locks the repeater at tick 1 (comparator side signal) | comparator/repeater input sampling right after placement or after an observer pulse. Under-investigated; the large known-class comparator mass was already split off |
| F6 repeater | 9 / 3 | 6: lever, stone, two repeaters in series, pulsed three times | `ad159b8d9fc3` | second repeater powered at tick 28 in vanilla only | repeater scheduling with quick toggles (pulse shorter than the delay is swallowed / re-queued differently). Hypothesis only |
| F7 observer | 7 / 3 | 3: lever on a lamp, observer watching the lamp | `1e4dd6eebef8` | observer fires in vanilla only | observer does not see a lamp state change caused through a ceiling-mounted lever (a wall lever / lever on stone agree in two probes). Hypothesis only |

Counts: 7 families, all of whose representatives are <= 6 blocks, i.e. the "at least 3 new classes <= 12 blocks, each isolated-JVM- and 2-origin-confirmed with a one-line
explanation" criterion is **met on paper at the family level** (F1, F2, F3 have probe evidence behind the hypothesis; F4-F7 have not). Caveat that matters: the reps of F1, F2, F3 and F5
have **no stimulus ops** (the divergence arises from placement settle, an observer firing at tick 1, or derived state), so the no-stimulus control does not apply (`causal` = null in their
certificates); F1 additionally has the causal lever-on-bulb probe. Whether these are "bugs" that matter for MIRA is a separate question: F2/F3 only change shape/instrument values; F1 and F4 change powered state.
Unclassified residue: `F8 other` is empty after the rules; 4 unstable cases dropped. Per-class hypotheses are in `data/fuzz/pilot/classes.json` coarse keys.

### False positives found and how they are filtered

| cause | symptom | filter |
|---|---|---|
| bare redstone wire vs fully-specified wire | every build with wire diverged at `placed` (118 of the first 300 captures) | `canon._nz` fills omitted properties from the registry; trace normalisation, not a simulator difference |
| `honey_block` as a support | levers/buttons on honey popped off in vanilla (inset shape) | `gen.NO_SUPPORT`; `env.blocks.supports` says honey is a full cube |
| empty `{Items:[]}` NBT on container used as a support | vanilla's `StructureTemplate.placeInWorld` places full cubes first, then other blocks, then NBT blocks last: a wire/torch resting on a block that carries NBT (even empty) is placed before its support exists and pops off | empty NBT is dropped; supports with NBT or non-full-cube supports for lateral/ceiling attachments are inadmissible (`gen.bad_attachments`); the same rule applies in the minimiser |
| right-click on repeater/comparator/note block/trapdoor/gate | Nucleation's `use_block` does not implement it (delay/mode change); known interaction gap, 25 members | rejected from "new" as `use_toggle`; ops on those blocks are only 1% of cases |
| minimiser shrinks the run to the first divergence | torch burnout needs >= 30 ticks to show | classification re-runs torch builds for 120 ticks before judging K3 |
| stimulus-ordering inside one tick | none seen | ops are applied identically on both sides (`Plan` semantics) |
| batching | none seen: for each of the 12 representatives the batched vanilla hash equals the isolated one | M1 guard (`OracleBatch` union warm-up) plus the check in `confirm.py` |

Not filtered, but worth knowing: **settle differences at placement** (vanilla runs observers/updates during `placeInWorld`, so ops-free builds can diverge purely from
settle order; this is the main reason for the F1/F2/F3/F5 caveat above), and vanilla update order depends on absolute position, so every certificate also records a second origin.

## Open problems / honest assessment

* Kill criterion not triggered (all five known classes seen, 7 new families), but **the novelty claim is weak**: families are my grouping, three have probe-level evidence only, four have none, and none
  was compared with Mojira, the wiki or the Nucleation issue tracker. By the audit's tiers these are "self-novel" only.
* 1M captures not reached (631k); 859 minimisations unfinished; the sample for classification is 3.3% of the divergent cases, so rare classes may be missing and the family sizes are not frequencies.
* The comparator known-class detector was widened during the run (lever -> lever/button/torch on a comparator side); the 27-member count includes some cases not individually checked.
* F4 is surely several bugs; separating them needs one more minimisation pass with a finer detector.
* Entities and items are untraced on both sides, so item-flow bugs (dupers, hopper timing) are invisible; ops are `use` only.
* Mutation arm from env/lib seeds is nearly barren (1.1% divergence vs 3.8% grammar): the references are well inside the verified subset.
* The LLM arm (day 3 of the plan) was not built.

## Requests to the oracle owner (not implemented here)

**B family (multi-version differential, 26.2 vs 26.3 / snapshots):**
1. `OracleBatch` per version: `oracle_setup.sh` writes one `$WORK/server.jar`; the fuzzer needs `capture_batch(..., work=Path)` (or an env var per call) so two versions run in one process, and `oracle_versions.json` per work dir (certificates already read the jar sha1 from there).
2. A smoke test that `MC_VERSION=26.3` compiles `OracleBatch` (the audit's minimal slice item 4), and the 26.3 jar fetched by the parent.
3. Identical `Case` replay on both versions needs the same origin; `Plan.origin` already supports it. Registry differences must be masked: expose `block ids present in this jar` so the generator can use the intersection.
4. Version-aware canonicalisation for properties added or removed between versions (the fuzzer's `_nz` fills defaults from Nucleation's registry, which is 26.2/26.3 hybrid).

**Entity / item tracing:**
1. Per-tick entity list (type, rounded position, motion hash) of the region in `OracleBatch.runJob` next to `tick_changes` (the audit estimates ~30 lines); today `wipe` discards entities and the trace has none.
2. Item entities counted separately from container contents (`inventory_events` exists for containers only), so conservation checks (items in + on ground + in containers) become possible.
3. A Nucleation-side equivalent (`TickSimulation` exposes no entity list in Python) or the fuzzer must restrict itself to container-only item flow, as it does now.
4. `use` on non-block targets and a held item (to place items, buckets) for the next generation of stimuli; and a `useBlock` flag for "sneaking" which changes lever/piston-adjacent behaviour.
