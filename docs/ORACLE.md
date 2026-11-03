# Vanilla oracle (Nucleation `tools/gametest` on Minecraft 26.2)

Ground truth for redstone behaviour: Mojang's in-process `GameTestServer` on the
unobfuscated 26.2 server jar, no mods, no EULA. Design: `docs/audits/oracle_verify_2026-11.md`.
Nucleation's `TickSimulation` stays the inner-loop scorer; this is the referee.

## Status (2026-11-01): working

Installed and run end to end. Server jar `26.2` (sha1 `823e2250d24b3ddac457a60c92a6a941943fcd6a`, 60,894,273 bytes,
fetched by the parent because the agent sandbox blocks `piston-meta.mojang.com` / `piston-data.mojang.com`) lives in
`data/external/oracle_work/` (gitignored, never redistribute). No EULA file is ever created or needed: no
dedicated server is started; `GameTestServer` runs in-process (verified: no `eula*` file anywhere under the work dirs).

```bash
bash scripts/oracle_setup.sh                    # idempotent: clone @pinned ref, jar sha1, unbundle, javac, provenance
python -m pytest tests/oracle -q                # 40 pass + 6 strict xfail (known divergences); live tests auto-skip w/o oracle
python scripts/oracle_diff.py --repros          # 21 circuits + 7 minimal repros, Nucleation vs vanilla, per mechanic
python scripts/oracle_env_diff.py --broken      # env/tasks reference solutions + hand-written broken variants vs vanilla
python scripts/oracle_bench.py --workers 1,2,4,8 --repeat 64   # throughput / parallelism
python scripts/oracle_capture.py --circuit lever_wire_lamp --sim   # one capture (or BUILD.litematic --plan plan.json)
```

If setup ever needs the network again the only hosts are `github.com` (Nucleation clone, `git fetch` of the pinned
ref), `piston-meta.mojang.com` (manifest + `version_26.2.json`) and `piston-data.mojang.com` (the jar). Offline, put
`server.jar` and `version_26.2.json` in `data/external/oracle_work/` and re-run the script.

## What exists

| path | what |
|---|---|
| `scripts/oracle_setup.sh` | idempotent: pinned Nucleation clone (`346733e`, MIT), jar download + sha1 check, unbundle, `javac` upstream drivers + ours, writes `data/external/oracle_work/oracle_versions.json` (MC version, DataVersion, jar sha1, Nucleation ref, Java) |
| `tools/oracle/java/OracleBatch.java` | our batch driver (compiled and run); same recipe as upstream `TraceCapture` (own tick loop, ticketed chunks, `GameTestHelper.useBlock` clone), but many jobs per JVM |
| `tools/oracle/gametest.py` | `capture`, `capture_batch` (one JVM, many captures), `capture_many` (several JVMs in parallel), `load_blocks` |
| `tools/oracle/sim.py` | same build+plan through Nucleation `TickSimulation` -> `Trace`; shared block-list -> SNBT conversion |
| `tools/oracle/trace.py` | `Plan`/`Op`/`Trace`, canonical state strings, `first_divergence` |
| `tools/oracle/diff.py` | tick-by-tick diff, per-mechanic summary |
| `tools/oracle/circuits.py` | 21 hand-written valid circuits + 7 minimal divergence repros (`all_circuits(repros=True)`) |
| `scripts/oracle_diff.py` | runs all circuits on both sides, prints agreement per mechanic, writes `data/oracle/diff_*.json` and `data/oracle/repros/*.json` |
| `scripts/oracle_capture.py` | one capture (circuit name or `.litematic` + plan JSON) |
| `scripts/oracle_env_diff.py` | env/tasks reference solutions + broken variants vs vanilla (read-only on env/) |
| `scripts/oracle_bench.py` | isolation + throughput / parallelism benchmark |
| `tests/oracle/` | offline tests (15) + live tests (25 + 6 strict xfail), live ones auto-skip when the oracle is not installed |

### Trace semantics

A `Plan` has `ticks`, `ops`, `probes`, `settle`, `preroll`. Ops apply after tick T-1 finished and
before tick T runs (identical on both sides, mirroring `TraceCapture`):
`use(t, pos)` = right-click with an empty hand (`GameTestHelper.useBlock` path: levers, buttons);
`put(t, pos, block)` = `setBlock(flag 3)` of a block's default state (`redstone_block`, `air`, ...).
`settle="place"` runs vanilla `placeInWorld` with the full update pass (= Nucleation `Placement`);
`"known_shape"` is the quiet placement (= `Quiet`). Nucleation's `InWorld` has no direct oracle
equivalent; use `preroll` (idle ticks after placement, before tick 0) to approximate "built, then at rest".

The oracle records the full state diff of the whole build region plus a 4-block margin every tick
(not only probes), plus container contents; `Trace.probe(pos)` extracts a probe timeline afterwards.
Positions are returned in the caller's frame. The structure's min corner sits at `trace.origin`
(spawn x/z, y=100); the simulator is given the **same origin** because vanilla update order
hashes absolute positions.

Nucleation exposes no container-content read in Python, so containers are compared through their
effects (hopper/dropper -> chest -> comparator -> wire power), and inventories are oracle-only data.

### Circuits (all physically valid: support blocks, attachment faces)

`facing` caveat found while writing them: on **repeaters and comparators `facing` points at the
input side** (the output is opposite), unlike pistons/droppers/hoppers/observers where it points at
the output/face. My first repeater/comparator circuits had it backwards; Nucleation correctly
refused to propagate, which is how I noticed.

| mechanic | circuits |
|---|---|
| wire | `lever_wire_lamp`, `wire_decay` (15 wires), `wire_vertical` |
| block_power | `strong_power`, `redstone_block_lamp` |
| torch / burnout | `torch_not_gate`, `torch_clock_burnout` |
| repeater | `repeater_chain` (delays 1,2,4), `repeater_lock` |
| piston | `sticky_piston_rb` (redstone block placed), `sticky_piston_lever` (real click), `piston_rb`, `slime_pull` |
| piston_qc | `piston_qc` (quasi-connectivity) |
| observer | `observer_pulse`, `observer_pair` (clock) |
| comparator | `comparator_chest`, `comparator_subtract` |
| hopper / dropper | `hopper_chain`, `dropper_chain` (near-full chest + comparator for observability) |
| button | `button_lamp` |

## Results (2026-11-01, Nucleation 0.12.0 @ `346733e` vs vanilla 26.2)

### Versions
| side | Minecraft | DataVersion |
|---|---|---|
| oracle | 26.2 (`GameTestServer`, Java 25.0.3) | **4903** (read from the jar's `version.json`) |
| Nucleation `TickSimulation` | engine "verified against 26.2" | schematics it writes carry **5023** (= 26.3, `canonical_data_version()`) |

The 4903/5023 mismatch is a label, not data: the oracle input is written by `tools/oracle/sim.py::structure_snbt`
straight from the block list with DataVersion 4903 (older corpus files keep their source version and are upgraded by the
game's own `DataFixTypes.STRUCTURE`).

**Important finding:** Nucleation's `gametest_snbt` is *its own flavour* (`id`/`properties`); the game's
`StructureTemplate.load` wants `Name`/`Properties` and, given the Nucleation flavour, **silently places nothing**
(a green run that tested nothing, exactly the failure mode upstream warns about). We therefore never use
`gametest_snbt` for the oracle; `OracleBatch` additionally has no way to notice an empty placement, so
`tests/oracle` asserts expectations against vanilla traces (every circuit with an `expect` list must act as designed).

### Agreement per mechanic (21 circuits + 7 repros, `settle=place`, tick-by-tick over the whole region)
Fully-agreeing circuits / total, then ticks agreeing (whole region incl. 4-block margin):

| mechanic | circuits | ticks |
|---|---|---|
| wire (decay to 1, vertical step) | 3/3 | 130/130 |
| block_power (lever strong power, redstone block) | 2/2 | 60/60 |
| torch NOT gate | 1/1 | 50/50 |
| repeater (delays 1/2/4 chain, locking) | 2/2 | 110/110 |
| piston (plain, sticky via block, sticky via lever, slime pull) | 4/4 | 200/200 |
| piston quasi-connectivity (+ update trigger) | 2/2 | 80/80 |
| observer (pulse, face-to-face clock) | 2/2 | 100/100 |
| button | 1/1 | 50/50 |
| hopper -> chest -> comparator | 1/1 | 80/80 |
| comparator (container read) | 1/1 chest + 1/1 control | |
| **comparator (lever as side input)** | 0/2 | 120/150 overall |
| **dropper** (feed dropper into dropper under QC situation) | 2/4 | 103/160 |
| **torch burnout clock** | 0/1 | 106/120 |
| **unsupported wall torch at placement** | 0/1 | 0/10 |
| **total** | **22/28 circuits** (18/21 of the original set) | |

Env tasks (`scripts/oracle_env_diff.py`, env/ read-only): all 22 reference solutions give **37/37 captures
tick-identical** between Nucleation and vanilla (vectors held 40 ticks each, every sequence its own capture) and
every contract expectation holds in vanilla (22/22 pass). Negative controls: of the 110 hand-written broken variants,
**110/110 fail in vanilla, in Nucleation and in env's own verdict** (verdict agreement 110/110). Nucleation is
tick-identical to vanilla on 108/132 variants; the other 24 are broken variants involving wall torches (below).

### Divergences (every one has a minimal repro: `data/oracle/repros/*.json` after `oracle_diff.py --repros`; circuits in `tools/oracle/circuits.py`)

1. **Comparator side input from a lever: Nucleation bug.** `comparator_side_lever`: subtract-mode comparator, rear = redstone
   block (15), side = lever. Vanilla ignores the lever (only redstone blocks / dust / diodes count as side input; output stays 15, lamp
   stays lit); Nucleation subtracts it (comparator off when the lever is on). Control `comparator_side_block` (redstone
   block as side input) agrees tick-for-tick, so the engine's subtract logic is right and only the side-input source filter is wrong.
2. **Dropper triggered by an item arriving (false QC trigger): Nucleation bug.** `dropper_feed_dropper_qc`: dropper 1 is powered by a
   redstone block on top of it, which is also beside the cell above dropper 2 (a quasi-connectivity position). When dropper 1 drops an
   item into dropper 2, Nucleation sets dropper 2 `triggered=true` (and fires it); vanilla does not (no neighbour update reaches
   dropper 2). Controls `dropper_qc_diag` (power beside the above-cell, no item) and `dropper_feed_dropper` (item, no QC power) both agree,
   so it is the combination: Nucleation re-evaluates QC power on the item insertion. `dropper_chain` and `comparator_subtract` diverge only because they contain these.
3. **Torch burnout clock: Nucleation bug.** `torch_clock_burnout` (6-wire loop torch -> dust -> its own support block). Vanilla toggles
   the torch every 2 ticks (t1,3,5,...,29) and then burns out (stays off from t29). Nucleation turns it off once at t1 and never relights (7 changes
   in 120 ticks vs 105). Same shape as a lever-driven NOT gate, which agrees; the difference is that here the unpowering wire change happens in the same tick as
   the torch's own off-tick, so the relight tick is probably lost to de-duplication against the tick being executed (hypothesis, not confirmed in Rust).
   Several env broken variants (rs_latch / t_flipflop / clock with turned torches) show the same wire-power difference at rest.
4. **Unsupported wall torch at placement: settle difference.** `torch_unsupported`: vanilla's `placeInWorld` pops the torch off (its support is air); Nucleation
   `Placement` keeps it. This is the 24-variant disagreement among env's broken builds (torches turned/flipped onto air, or their stone removed). It only affects
   invalid builds; env's `no_popoff` invariant is the right guard and this oracle can confirm it.
5. **`settle="known_shape"` (Nucleation `Quiet`) is not equivalent to vanilla `knownShape`** on 13 of 28 circuits (vs 6 with `place`), mostly already in the placed state (e.g. wire shape/power on `lever_wire_lamp`); use `place`. Not adjudicated further.

### The questions from the spike / audit
- **Sticky piston.** Vanilla 26.2 extends the sticky piston and pushes the stone when a `redstone_block` is `setBlock`ed beside it (flag 3, as the spike did)
  *and* when an adjacent lever is clicked, and pulls back correctly (`sticky_piston_rb`, `sticky_piston_lever`; t2 extend start, head at t4, retract at t20-22).
  Nucleation matches vanilla tick-for-tick in both. So the spike's disagreement was a **stimulus/paste artifact of the live 1.21 Carpet server**, not a simulator bug. (Why the
  live server did not move is not isolated here; the oracle simply shows `setBlock` activation works in 26.2.)
- **`piston_qc`.** Not a simulator bug. A redstone block set diagonal-above the piston does *nothing* in vanilla either until the piston receives a block update
  (`piston_qc`: no change in either engine); with a later update (`piston_qc_update`) both extend at the same tick. My earlier suspicion was wrong.
- **`torch_clock_burnout`:** simulator bug, item 3.

## Measured

Machine: 16 cores, 62 GB RAM (~45 GB used by other tenants at the time), OpenJDK 25.0.3, flags `-Xmx1536m -XX:+UseSerialGC -XX:TieredStopAtLevel=1`.

| | wall |
|---|---|
| one capture, fresh JVM (boot 2.5 s + job ~0.2 s) | **2.8-3.0 s**; max child RSS **~560 MB** |
| 21 circuits in one JVM (`capture_batch`) | **3.1 s** total (boot amortised; ~12 ms/job steady state for small builds) |
| 168 small jobs, 1 JVM | 4.5 s (37 captures/s incl. boot) |
| 1344 small jobs, 1 JVM / 2 / 4 / 8 JVMs | 13.0 s (103/s) / 8.4 s (160/s) / 6.4 s (211/s) / 6.3 s (213/s) |
| 168 jobs, 16 JVMs | 7.8 s (slower: 16 simultaneous 2.5 s boots on 16 cores) |
| corpus build, 165 blocks (`12gt_Dispenser_Factory`), 60 ticks | job ~1.2 s, 3.9 s end to end incl. datafix |
| upstream `run.sh` (4 tests) | ~4.5 min, almost all of it one JVM per `.snbt` conversion; our driver reads SNBT in-process and skips that |

- **Batching gain:** ~25x (21 captures 3.1 s vs ~60 s as 21 fresh JVMs). **Isolation:** all 21 batch traces are identical to one-JVM-per-job traces
  (`oracle_bench.py`, also a pytest). The driver wipes the region, clears torch-burnout history and runs 10 idle ticks between jobs.
- **Parallelism:** the practical optimum is **4 JVMs** (~210 captures/s); 8 adds nothing, 16 hurts. RAM is not the limit: ~0.55 GB RSS each (8 JVMs added +4.7 GB measured system-wide),
  CPU/boot contention is. Use one JVM per ~4 cores with big job lists; for a single large capture, parallelism does not help.
- **Upstream examples:** `tools/gametest/run.sh` (copy of the jar via symlink, `WORK=/tmp/...`): 3 of 4 upstream tests pass
  (`torch_inverts`, `comparator_update_collection`, `tnt_minecart_rail_shielding`); `lava_push_speed` fails ("Lava pushed armor stand too quickly ... tick 56, should finish in tick 61")
  on this jar+Java. Entity/lava, outside our redstone scope; not investigated.

## Limits / open problems
- Container contents: Nucleation has no read API in Python, so item flow is compared via block-state effects (comparators/wire); the oracle's slot-level inventory events are in `Trace.meta`.
- Entities (mobs/items in files) are dropped on load (`load_build`); only blocks and block entities are captured.
- Oracle has no `InWorld` equivalent; `preroll` ticks stand in for "built then at rest". Placement/pop-off semantics differ for invalid builds (item 4).
- Corpus scan (`.litematic` idle runs) was only spot-checked: 3 of the sampled files were in range, 2 refused by Nucleation (`NucleationErrorCode.Simulation`, unsupported block/entity) and one loaded and showed a
  crafter `triggered` difference at tick 1. Not a measured rate.
- Position-hashed update order depends on absolute position: the oracle origin is fixed at (spawn x, 100, spawn z) = `(0,100,0)` and the simulator is given the same origin.
- Latest release is 26.3 (DataVersion 5023); the oracle pins 26.2 as the audits require. Re-run setup with `MC_VERSION=26.3` to compare when Nucleation moves.

## Day 2 additions (2026-11-02, phase A5)

### New pieces
| path | what |
|---|---|
| `tools/oracle/envplan.py` | env contract -> oracle replay that mirrors `env/runner.py`: the env's at-rest **handover world** (`handover_world`, = what `runner.export_litematic` saves), env vector order (`eval_seed` shuffle for comb, authoring order for seq), env's per-vector tick counts (quiescence + extra), public + hidden in one capture, levers' initial level read from the world, and the build placed at **env's absolute cells** (`env_origin`). Also `read_out` / `evaluate` (one pass per trace; the old `state_at` per check was quadratic and took minutes on T4 sequences) |
| `tools/oracle/placement.py` | prototype of an order-independent build placement for env (raw states, every dust a cross, Nucleation `Placement` load); `patched_env()` routes `env.runner` through it in-process without touching env/ |
| `OracleBatch.java` | per-job absolute `origin` (`Plan.origin`; default still spawn x/z, y=100), every origin's region warmed before the first job; new op `place` = hand placement of a full state (dust: `getConnectionState(crossState)` as `getStateForPlacement`; others `updateFromNeighbourShapes`; flag 3; `setPlacedBy`) |
| `tools/oracle/sim.py` | `run_plan` re-reads only the cells in Nucleation's change log per tick (20-60x faster on T4 builds; equal to the full snapshot diff on 37 captures, tested); honours `Plan.origin`; `place` ops map to `place_block` (which refuses `redstone_wire`: NotFound) |
| `scripts/oracle_sessions.py` | rewritten: replays the handover world with env timing (`--hold env`, default) and the raw program output as a diagnostic column; sessions from `data/sessions` and `data/sandbox/*/session` (identical copies once); env results cached in `data/oracle/_cache` (`--prepare-only`) |
| `scripts/oracle_env_diff.py` | `--env-mirror` (handover world + env schedule); captures/evaluate now come from envplan |
| `scripts/oracle_robustness.py` | `placement` (env vs canonical placement over the 8 dihedral variants), `offsets` (3 absolute origins in vanilla), `rotations` (T3/T4 at 4 rotations in vanilla) |
| `scripts/paste_receipt_server.py` | Litematica-style paste into the Fabric+Carpet **1.21.0** server, full read-back receipt, Carpet fake-player stimulus |
| `tests/oracle/test_envplan.py`, `test_placement.py`, `test_paste_receipt.py` (offline, 46 + 1 strict xfail), `test_robustness_live.py` (3, vanilla) | |

### Bug 6: Nucleation engine placement corrupts dust shapes (env's `compile_cells` path)
`Schematic.set_block("minecraft:redstone_wire{simulate=true}")` (what `env/sim.compile_cells` does for every block, bottom-up y,x,z)
turns the END of a dust line into a cross when a later dust is placed two cells further along x and 1-2 cells off in z.
Repro: stone floor x 0..5, z 0..8; lever (0,1,3); dust (1,1,3), (2,1,3); unrelated dust (4,1,4) (also (4,1,1), (4,1,2), (4,1,5)).
Nucleation: (2,1,3) = `east=side,north=side,south=side,west=side`; without the unrelated dust: `east=side,west=side`.
**Vanilla 26.2**, same blocks hand-placed in the same order (`place` op): (2,1,3) stays `east=side,west=side`
(`tests/oracle/test_robustness_live.py::test_vanilla_hand_placement_keeps_line_end`). Nucleation's `Placement` load of the raw states
gives the vanilla result. Classification: **Nucleation bug in the `{simulate=true}` placement path**, exposed by env's placement
choice; env verdicts on such builds fail `paste_stable` (the at-rest world re-loads differently) or, for state circuits, start in another state.
Strict xfail: `tests/oracle/test_placement.py::test_engine_placement_unrelated_dust_keeps_line_end`.

### Results (vanilla 26.2, one JVM per capture unless noted)
**Sessions** (`data/oracle/sessions_r4.json`, `--hold env`; 13 distinct sessions, sandbox copies identical to `data/sessions`):

| session | stored | env now | vanilla (handover) | nuc==van | raw paste (vanilla) | ticks |
|---|---|---|---|---|---|---|
| t1_clock …1226 | pass | pass | pass | identical | pass | 323 |
| t1_edge_detector …6bad | pass | pass | pass | identical | pass | 252 |
| t1_pulse_extender …5e5f | pass | pass | pass | identical | pass | 373 |
| t2_door_2x2 …685c | pass | FAIL (spec fixed) | FAIL | identical | FAIL | 1082 |
| t3_adder2 …0dae | pass | pass | pass | identical | pass | 1500 |
| t3_adder2 …4b87 | pass | pass | pass | identical | pass | 1188 |
| t3_adder4 …798b | pass | pass | pass | identical | pass | 1998 |
| **t3_counter4 …af52** | pass | pass | **pass** | identical | **FAIL** (86 cells differ at rest) | 1050 |
| t3_decoder2to4 …e26a | pass | pass | pass | identical | pass | 504 |
| t4_accum4 …eb33 | pass | pass | pass | identical | pass | 13709 |
| t4_adder16 …f690 | pass | pass | pass | identical | pass | 3364 |
| t4_addsub8 …bf70 | pass | pass | pass | identical | pass | 3494 |
| t4_shift32 …959d | pass | pass | pass | identical | not run in vanilla (Nucleation: raw == handover at rest) | 31688 |

env / vanilla verdict disagreements: **0 of 13**. Wall: 62 s for the 9 T1-T3 sessions (4 JVMs), 60-175 s per T4 session (env runs of 1.5-4 min each come on top; machine load ~26 on 16 cores).

**Offsets** (`data/oracle/robustness_offsets_*.json`, every T0-T5 reference, env-mirrored replay, build min corner at env's cells, at (1007,70,519) and at (-2003,30,-1037); batched, 4 JVMs):
all **42 references pass in vanilla at all 3 origins**. Traces are position-independent for 36; the 6 ripple adders (t3_adder2/3/4/8, t4_addsub8,
t4_adder16) show **different transient lamp glitches** at different origins (e.g. adder2 tick 441 (9,1,4); adder16 tick 117 (265,1,15)) but the same verdict:
vanilla update order really depends on absolute position, and only the in-between ticks differ. Nucleation == vanilla at every origin except
the known transient divergences of t3_adder3, t3_decoder3to8, t4_addsub8 (verdicts agree).

**Rotations** (`data/oracle/robustness_rotations_*.json`, `env.variants.variant_of(rot=0/90/180/270)`, vanilla on the structure paste of the
reference, env-mirrored timing from the canonical-placement env run): T3 48/48 and T4 12/12 (accum4, addsub8, adder16; shift32 not run, time) **pass in vanilla**,
Nucleation == vanilla tick for tick except the known transients. env itself fails 7/48 T3 and 3/12 T4 rotations (paste_stable or all vectors) and the
canonical placement prototype fails 1 (adder8 r180: tick budget). See docs/VALIDITY.md section 10.

### 1.21.0 server paste (`scripts/paste_receipt_server.py`, `data/oracle/paste_receipt_server*.json`)
Fabric 1.21 + Carpet 1.4.147 (`system_info('game_version')` = 1.21), started and stopped by the script (`--start-server`). Per build: export the env
handover with `runner.export_litematic`, load the `.litematic` back with Nucleation, paste at x=3000+400k, y=100, z=3000: forceload, clear box+2,
`tick freeze`, `carpet fillUpdates false`, bottom-up `setblock`/`fill` runs (<=64 commands per 50 ms), `data merge` for block entities; read back
EVERY cell (scarpet, one row of 40 per RCON call) while frozen, then unfreeze + fillUpdates true, 3 s of ticking, read back again.

| build | blocks | commands | paste | read-back (frozen) | rejected states | after 3 s unfrozen |
|---|---|---|---|---|---|---|
| 8 strong-agent sessions (t1_clock … t4_shift32) | 38 - 6524 | 11 - 1547 | 0.1 - 6.6 s | **all cells match** (48 - 18360 cells each) | 0 | 0 cells differ |
| 4 T4 references + t0_and | 46 - 6633 | 19 - 1554 | 0.1 - 6.4 s | all cells match | 0 | 0 |

None of the states (validated against the 1.21 tables, scored in a 26.x simulator) is rejected by 1.21.0. No build has block entities (0/0).
**Fake player:** `/player mira_bot spawn` on a pasted block next to the lever, `look at` lever, `use once` (lever state read back after each click):
t0_and reference truth table 8/8 rows right; t1_clock session `stop` sequence: lamp toggles at game ticks 3,17,22,37,43,57 after the enable click
(env trace: 4,18,24,38,44,58; RCON polling and click latency are 1-2 ticks), stays off 70-120 after the disable click at t=53 (`ok`); t1_clock reference also passes.
Not verified: pasting the `.litematic` FILE with the Litematica client mod (the file carries DataVersion 5023, newer than 1.21.0; the receipt pastes its
states by command, which is what Litematica's server paste mode sends).
