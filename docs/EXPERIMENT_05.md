# Experiment 05: spec fidelity of the three free-form pilots (2026-11-03)

Round-3 audit step 2, the "spec gap". Experiment 04 left three builds that passed **specs their own agent wrote**. This experiment
measures `P(independent hidden spec passes | the agent's own spec passed)` by writing, from the plain-English request alone, a hidden
spec for each build and running it on the exported hand-over `.litematic` in the env (Nucleation) and in the vanilla 26.2 oracle
(one JVM per capture), at the original position, at a random second absolute origin, and rotated/mirrored.

**Read the caveats first.** n = 3 builds, one agent run each; the hidden specs were written by one author (this agent) and the tier
boundaries ("what a reasonable user expects") are judgement calls that move the counter verdict (section "Verdict"); the specs live in
`scripts/spec_fidelity.py` in the repo, so they must be kept out of any builder's sandbox before they are used as a held-out test.

## Verdict

| build | own spec (agent) | hidden spec (core tier = what a reasonable user expects) | hidden pass? |
|---|---|---|---|
| flush 2x2 piston door | PASS 4/4 (env, vanilla x3 variants) | **35/35** core in env x2 and vanilla x3; stress 10/13 | **yes** |
| 4-bit counter + reset | PASS 7/7 (env, vanilla x3) | **46/55** core in env, **45/55** in vanilla; stress 15/24 | **NO** |
| 4-bit ALU | PASS 8/8 chunks of the 1024 rows (env x2) | **119/119** core in env; **71/71** in vanilla at the original origin (all 1024 rows), 23/23 at each of the other two variants (subset) | **yes** |

`P(hidden pass | own pass) = 2/3` (Wilson 95% interval 0.21 - 0.94: n = 3 says almost nothing about the rate, but it falsifies "own-spec pass
implies the user's request is met"). The failure is **not in logic**: every reset-from-a-value case (16/16), the 15 -> 0 wrap, all 1024 ALU rows
in a shuffled order, multi-lever flips, lever flailing and long idle pass. The counter fails exactly where the agent's spec was silent:
**how fast the buttons may be pressed**. Its own NOTES.md disclosed the limits (press spacing >= ~54 ticks, BTN shortly after RST misbehaves), but
the spec used 60-tick spacing, so the PASS did not say that a person pressing a button about once per second gets a wrong count.

Position and orientation play no role here: in vanilla the per-check outcomes are identical at the original origin, at the random second
origin and rotated/mirrored (door 60/60, counter 82/82, ALU 30/30 shared checks), and env gives the same outcomes unrotated and rotated. Only
transient ALU lamp glitches differ by a few ticks (14 of 91,930 compared ticks; see "env vs vanilla").

## What was tested

Builds: the exported hand-over worlds of Experiment 04 (`/tmp/freeform_tff_door/tff_door.litematic`, `/tmp/freeform_counter/.mira/exports/counter.litematic`,
`/tmp/freeform_alu/.mira/exports/alu.litematic`, loaded at the task coordinates of the export; the `/tmp/mira_scratch/pilots/*` copies hold no exports and
are later re-runs, not used). Ports are bound **by label and position only**: `scripts/spec_fidelity.py` reads `name, kind, pos, facing, box` of the `ports` in the
agent's `spec.json` (never windows, vectors or thresholds) into contract-v2 `env.contract.Port` objects. The door's geometry (doorway box, front direction = the
button's `facing`, wall block above the doorway) is derived from those ports and the world, so the same spec runs on any build with those labels.

Engines (one trace format, one evaluator):
* **env**: `env.sim.Sim` with the canonical placement (`compile_cells`, Placement load, settle to rest, checkpoint per scenario), `env.runner.Driver` semantics
  (lever clicked only if the level differs, button = one `use`). Not `env.runner.run`: its fixed/build split cannot take a whole exported world (door cells are reserved).
* **vanilla**: `tools.oracle.gametest.capture_many(isolated=True)` = a fresh 26.2 JVM per capture, `settle=place`, 20 preroll ticks, `Plan.origin` = absolute position of the world's min corner.

Variants (`variant_params`, seeded per build): `orig` = the export's own task coordinates; `origin` = rotation 0, random absolute origin (vanilla only; env is position independent);
`rot` = a random non-identity element of the dihedral group from `env.variants.Xf` applied to the **world, the ports and the geometry**, plus a random origin:

| build | origin variant | rot variant (rotation, mirror, origin) |
|---|---|---|
| door | (-1592, 82, -1205) | 90, none, (380, 87, -279) |
| counter | (-1069, 37, -1366) | 180, mirror x, (-2094, 37, -2296) |
| alu | (2127, 54, 2616) | 270, none, (955, 60, -636) |

Check tiers: **core** = a reasonable user of the request expects it; **stress** = plausible but rougher use (press spacings of 21-35 ticks, 10-15 ticks between the two buttons,
mashing); **info** = an assumption of the hidden spec a user may not share (never counted against a build). A hidden spec "passes" iff all core checks pass.
Press accounting is read **from the world** (rising edges of the button's `powered`), not from the intended clicks, so the stone button's 20-tick lockout (a click while
pressed is ignored in both engines) cannot create false failures.

### Hidden spec summaries (the full text is `door_spec`, `counter_spec`, `alu_spec` in the script)

**Door** (11 scenarios, 19.7k ticks, 60 check outcomes). A press is any rising edge of BTN; after j accepted presses the door must be closed iff (initially closed) == (j even),
within `min(40, gap-2)` ticks, until the next press.
* *closed* = all 4 doorway cells are plain full blocks (not air/piston/head/slab/device); and, in closed windows: the 3 cells in front of the doorway and the 1 cell behind it are air
  (nothing protrudes beyond the wall plane), no `moving_piston` anywhere, every extended piston has its head, every head has an extended base (no stuck pistons). *Open* = all 4 cells air
  and the walk-through passage (2 cells either side) clear.
* scenarios: idle closed 3,000 ticks; presses with gaps 120 / 61 / 40 / 31 (core), 25 / 21 (stress); 24 presses with random 31-200 gaps; long idle (3,000 and 5,000 ticks) between presses;
  double click 10 ticks apart (twice); 40 clicks 5 ticks apart (ends stable and unstuck = core, parity = stress).
* info: starts closed; closed door is made of the wall's own block; front cells never entered at any tick (stress).

**Counter** (41 scenarios, 42.3k ticks, 82 outcomes). `COUNT` = L0 + 2 L1 + 4 L2 + 8 L3. Model: BTN edge = +1 mod 16, RST edge = 0 (a same-tick tie: reset last); checked in every quiet window
`[e + min(60, gap-2), next edge)` and at the end.
* counting 18 presses (wraps 15 -> 0) at gaps 80 / 60 / 45 / 40 / 35 / 30 / 25 / 21 and 35 presses (two wraps) at 45; response time <= 60 ticks (core for gaps >= 45).
  Tier: final count correct = core for gaps >= 25; every-press windows core for gaps >= 40, stress below; gap 21 stress.
* reset from each value 0..15 (count there with 80-tick setup gaps so the rate does not confound, press RST, expect 0 within 60 ticks); BTN then RST d = 60/40/25 (core), 15/10 (stress) ticks later (reset wins, counting continues);
  RST then BTN e = 60/40/25 (core), 15/10 (stress) ticks later (count = 1, counting continues); same-tick BTN+RST (outcome in {0, count+1} and keeps counting and reset still works = core; "reset wins" = info);
  mashing the button (30 clicks 10/14/20 ticks apart; the model uses the clicks the button accepted), 8 resets 30 apart (stress); 3,000 ticks idle (no drift = core, starts at 0 = info).

**ALU** (20 scenarios, 264k ticks in env, 158 outcomes). Inputs read from the levers' `powered` state in the world, so the expected value always follows what was actually set.
* exhaustive: all 1024 (A, B, OP) rows in a seeded shuffle, no reset between rows (so about 5 of the 10 levers change between consecutive rows), hold 110 ticks, in 8 shards; second pass with a different shuffle (env only);
  fast: 300 random steps flipping 1-6 levers in the same tick, 100-tick holds; flail: 30 bursts of 8-16 single-lever flips 3-6 ticks apart, 200 ticks rest.
* per row: R correct for add / and / or / xor (core), CARRY correct for add (core), CARRY == 0 for and/or/xor (info: an assumption), settles within 100 ticks (core) / 60 ticks (info).

## Results

Check counts below are outcomes (one per scenario x check), the unit the script reports; `python scripts/spec_fidelity.py report` regenerates all tables from `data/fidelity/chunks/*.json`.

### Own vs hidden, per run

| build | spec | env orig | env rot | vanilla orig | vanilla origin | vanilla rot |
|---|---|---|---|---|---|---|
| door | own | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| door | hidden: core / stress / info | 35/35, 10/13, 9/12 | same | same | same | same |
| counter | own | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| counter | hidden: core / stress / info | **46/55**, 15/24, 1/2 | **46/55**, 15/24, 1/2 | **45/55**, 15/24, 1/2 | **45/55**, 15/24, 1/2 | **45/55**, 15/24, 1/2 |
| alu | own (1024 rows, 8 chunks) | 8/8 | 8/8 | not run | not run | not run |
| alu | hidden: core / info | 119/119, 31/39 | 119/119, 31/39 | 71/71, 18/23 | 23/23, 6/7 | 23/23, 6/7 |

ALU in vanilla: orig = all 1024 rows of pass 1 + 300 fast steps + 30 bursts (12 captures, 150.7k ticks, ~11 min wall in two calls); origin and rot = 4 captures each (rows of shards 0 and 4 = 256 rows,
100 fast steps, the bursts). Pass 2 (second shuffle) and the own-spec replay of the ALU were env only (the own rows are the same 1024 rows as pass 1).
Every ALU `info` failure is `settles within 60 ticks`: the full add ripple (e.g. A=13, B=3) needs **64 ticks** (3.2 s) to stop glitching, identically in env and vanilla; settling within 100 ticks (core) holds for all rows.

### Hidden-spec failures by check class (core tier: failing / checks)

| build | class | env orig = env rot | vanilla (all 3 variants) |
|---|---|---|---|
| door | toggle / flush / open / pistons / init / idle | 0/8, 0/8, 0/7, 0/10, 0/1, 0/1 | same |
| counter | **count** (rate) | **7/28** | **8/28** |
| counter | **latency** (display must settle <= 60 ticks and show +1) | **2/4** | **2/4** |
| counter | reset (from every value, reset during counting, RST then BTN) | 0/19 | 0/19 |
| counter | simultaneous, idle | 0/3, 0/1 | 0/3, 0/1 |
| alu | alu_result / alu_carry / alu_latency | 0/80, 0/20, 0/19 | 0/48, 0/12, 0/11 (orig); 0 elsewhere |

(The counter `reset` class is 0/19 because the failing RST-then-BTN checks are filed under `count`.)

### Failing hidden checks and their classification

(a) real bug against the request, (b) hidden spec over-strict (a reasonable user would not expect it), (c) env / vanilla disagreement. "Same cause" rows are one root cause.

| # | build | failing checks (tier) | first failing check and why | class |
|---|---|---|---|---|
| 1 | counter | `count_gap_45` every-press + response, `count_gap_45_x35` (same), `count_gap_40` every-press, `count_gap_25` final (core, env + vanilla); `count_gap_35/30/25/21` every-press / final (stress) | `count_gap_45/count_model_every_press`: after the 5th press (4 -> 5) the lamps show **13** instead of 5 (bit 3 spuriously set) and the count stays shifted for the rest of the run. Cause: presses closer than ~54 ticks (env; **55 in vanilla**, section "Thresholds") arrive before the carry chain Q0 -> c2 -> c3 -> x3 -> g3 -> D has settled, which the agent's NOTES also say. At 21-35 ticks the counter drops or double counts presses | **(a)**, high: "goes up by one every time I press a button" is false at the speed of a casual user (one press per ~2 s); disclosed in NOTES, not in the spec |
| 2 | counter | `rst_then_btn_25` count_after_reset_is_1 + model_final (core); `rst_then_btn_15/10` (stress) | `count 6 (expected 1 = reset, then one count 25 ticks later)`: from count 5 the reset takes effect (lamps 5 -> 1 -> 0 about 40 ticks after the RST press) but the BTN press that arrives while the reset is still propagating leaves **garbage** (0 -> 4 -> 6, bits 1 and 2 set) and counting continues from 6; at 15 / 10 ticks the reset takes and the count press is swallowed (0 instead of 1). Threshold 40 (env) / 41 (vanilla) ticks between RST and the next BTN; the agent's NOTES said "~30" | **(a)**, medium: resetting and counting again within ~2 s is ordinary use |
| 3 | counter | `count_gap_40/count_model_final` (env fails, vanilla passes); `rst_then_btn_40` count_after_reset_is_1 + model_final (env passes, vanilla fails) | the same two thresholds sit 1 tick apart in the two engines (54 vs 55, 40 vs 41), so a case exactly at 40 ticks flips | **(c)**, marginal (see "env vs vanilla") |
| 4 | door | `gap_25`, `gap_21`, `spam_5` toggle_parity (stress) | `expected CLOSED in [59,60] but not at tick 59 (after 16 accepted presses)`: the toggle misses a press that follows the previous one within 20-25 ticks (env) / 20-26 ticks (vanilla); sweeps below. The 10-tick double click (second click ignored by the button) is handled | **(b)**-leaning: 20-26 ticks is the fastest a stone button re-arms (1.0-1.3 s); every spacing a person plausibly uses (>= 27 ticks) works; kept as stress |
| 5 | counter | `simultaneous_btn_rst/reset_wins` (info) | BTN and RST in the same tick from count 3 shows 4 (count wins, reset lost). The valid-outcome, keeps-counting and reset-still-works checks all pass | **(b)** (our assumption "reset dominates"; not counted) |
| 6 | alu | `settles_within_60` (info) | worst 64 ticks (full add ripple) | not a failure |
| 7 | door | `wall_material` (info, 3) | only inside the windows of row 4 (the door is not closed there); the closed door is made of stone like the wall | follows row 4 |

The door produced **no core failure**: toggling parity over up to 24 presses with random gaps, every flush / passage / piston-consistency check, 3,000-tick and 5,000-tick idles pass in all five runs.
The ALU produced **no core failure at all**; its hidden spec is mostly its own exhaustive table (the one design for which an own-spec pass really is a full-spec pass), plus latency, shuffle order, multi-lever flips, lever flailing and non-add carries.

### Thresholds (info sweeps, `python scripts/spec_fidelity.py sweep NAME --backend env|vanilla`; gaps 14..70)

| quantity | env | vanilla 26.2 | agent's own statement |
|---|---|---|---|
| counter: smallest button spacing from which all gaps count correctly (18 presses, every-press windows) | 54 | **55** | "passes at 54 and 58, fails at 50 and 46"; spec uses 60 |
| counter: smallest RST -> BTN spacing from which the count after the reset is right | 40 | **41** | "BTN within ~30 ticks of RST misbehaves" (so the real limit is 10 ticks higher) |
| counter: BTN -> RST spacing | every gap >= 14 works | same | "RST 10 / 30 ticks after BTN" tested |
| door: smallest press spacing from which every press toggles | 26 (gaps 14-19 pass because the second click is ignored; 20-25 fail) | **27** (14-19 pass; 20-26 fail) | "at least ~40 apart ... a 40-tick double press also passed" |

### env (Nucleation) vs vanilla

| build | variant | scenarios | check outcomes identical | scenarios with identical per-tick predicate series | ticks that differ / compared | where |
|---|---|---|---|---|---|---|
| door | orig, rot | 11 | 60/60 | 11/11 | 0 / 135,024 | - |
| counter | orig, rot | 41 | 78/81 | 38/41 | 1,024 / 42,325 | `count_gap_40`, `count_gap_30`, `rst_then_btn_40`: the marginal-timing region (class c) |
| alu | orig / rot | 12 / 4 | 94/94, 30/30 | 0/12, 0/4 | 155 / 301,482; 76 / 91,930 | only `RES` transients while the ripple adder settles (e.g. exh1_shard0 tick 2806: env 11, vanilla 15); at most 0.3% of ticks; CARRY never differs; all settled values identical |

Vanilla against vanilla: the outcomes are identical at the original origin, the random origin and rotated (door 60/60, counter 82/82, ALU 30/30), and the per-tick series are identical for the door and counter
(0 differing ticks); the ALU differs in 14 ticks (origin) and 11 ticks (rotation) of 91,930, all transients (update order depends on the absolute position). env runs give identical results unrotated and rotated.
So position or orientation caused **no** failure; the only env / vanilla verdict differences are the 1-tick threshold shifts above. That is a modest result: three builds, one random element each, no
timing-tight design at the original coordinates.

### Negative controls (the hidden specs are not vacuous)

`python scripts/spec_fidelity.py controls` (env): each broken copy is rejected by core checks: door without a sticky piston (18 core checks fail: pistons 10, toggle 8) and without a repeater (9); counter with the RST button replaced
by stone (29) and without a comparator (40); ALU without the middle comparator (3: xor/or rows and latency) and without a repeater (3). (`data/fidelity/controls.json`.)

### Cost

env: door 0.2 s, counter 7 s, ALU 73 s for the whole hidden spec. vanilla (one JVM per capture, 4-8 at a time): door 17 s, counter 105 s, ALU 12 captures / 676 s at the original origin (about 17 ms per tick on a 17k-block world, the cost is
the oracle's whole-region diff per tick), 4 captures / ~250 s per other variant. Sweeps: door 57 captures ~1 min, counter 171 captures 6 min in vanilla.

## What this says about the spec gap

1. **Own-spec PASS is necessary and not sufficient, and the missing piece is a rate/timing envelope, not logic.** The counter's three defects (count rate, RST -> BTN spacing, and the mismatch between its documented "~30 ticks"
   and the measured 40) are all about *how fast inputs may arrive*. An agent that writes its own spec picks a comfortable spacing and, after reaching a PASS, has no reason to probe the edge.
2. **The edge is where env and vanilla disagree**, by one tick (54 vs 55, 40 vs 41, 26 vs 27). A spec whose windows touch a threshold gets a different verdict depending on the engine. Margin of at least 2-3 ticks should be required before a limit is called "supported".
3. **Exhaustive specs close the gap where they are possible** (combinational ALU: 1024 rows = the whole input space; hidden latency / order / flail checks added nothing). For sequential devices the analogue of "exhaustive" is a state x transition x spacing grid, which neither agent spec nor `spec-check` coverage reports today.
4. The door's fidelity is good and its stress failures are an inherent limit of the stone button; but the **door's own spec was thin** (4 sequences, 2 public + 2 hidden, no flush / plane / stuck-piston checks, mostly 70-tick press spacing, one 40-tick double press): the hidden spec added all of those and the door passed them, so the build is better than its spec proved.
5. Honest bound: with the tier boundary moved from "gap >= 25-40 ticks" to "gap >= 55" the counter would pass; with it moved to "any accepted click" the door would fail. The verdict depends on that boundary, so a product must make the user (or the spec author) state it, see below.

## Recommendations for spec-authoring tooling (`spec-check`, `spec-show`)

Concrete, in order of how much each would have caught here:
1. **Press-spacing coverage in `spec-check`.** For every pulse input print the minimum spacing exercised (`BTN: 7 presses, min gap 60 ticks (button re-arms at 20)`) and flag a spec whose smallest gap is far above the device's own minimum;
   for every ordered pair of pulse inputs print which offsets were tried (`RST->BTN: 10, 30 tested; 0, 5, 20, 40 not`). The counter spec would have been flagged immediately ("fastest tested press is 3x slower than the button allows").
2. **`spec-check --sweep`**: automatic threshold sweep (the `sweep` command here): replay the spec's own sequence with every spacing 14..70 and print the pass map and the smallest all-pass spacing per pair of inputs; show it in `spec-show` next to the NOTES claim ("NOTES says ~30; measured 40"). Report the vanilla threshold too and require >= 2 ticks of margin between the supported limit and the nearest failing spacing in either engine.
3. **Edge-based expectations.** Let sequence expectations refer to accepted edges of the input (as `Ctx.edges` does) instead of intended clicks, so button lockout and double clicks are handled by the engine, not by the spec author; print "accepted presses vs clicks" per sequence.
4. **State/transition coverage for stateful outputs**: show the visited output states and transitions (counter: states 0..15 seen, wrap seen, reset from 16/16 states), the first-power-on state, and whether the spec asserts it. A "reset from every value" and "wrap" checklist item would be generated from the fact that 4 lamps are a 16-state code; at least print "states asserted: 6 of 16".
5. **Library predicates for physical claims**, so the door spec cannot omit them: `flush(box, normal)` (plain full blocks, front/back clearance), `pistons_consistent()` (no `moving_piston`, heads <-> extended bases), `passage_clear(box, depth)`; `spec-show` lists which physical predicates the spec asserts about each region/port and which it does not.
6. **Latency table in `spec-show`**: the measured settle time per output and stimulus (counter 32 ticks per press, ALU 64 ticks worst, door 9-11 ticks) next to the windows the spec uses, so over-tight and over-loose windows are visible, and so "the user waits up to N seconds" is stated.
7. **Held-out replay as a normal command.** `verify-vanilla --variants K --second-origin` already covers origin and orientation; add a `--hidden SPEC` option that replays a spec the builder never saw (this experiment's mechanism: the same `Port` labels bound by position) and keep such specs outside the builder-visible tree. Here origin/rotation found nothing, so this is the cheaper half of the audit; the rate sweep is the valuable half.
8. **Tier vocabulary in the spec**: `core` / `stress` / `info` per check, so a spec can say "supported press spacing >= 60 ticks" explicitly and a hand-over note can quote it. `spec-show` should print this envelope as a sentence the user can accept or reject.

## Reproduce

```bash
.venv/bin/python -m pytest tests/oracle/test_spec_fidelity.py -q                          # 7 offline tests (the door ones skip when /tmp/freeform_* is gone)
.venv/bin/python scripts/spec_fidelity.py list
.venv/bin/python scripts/spec_fidelity.py run door --backend env --variant rot [--own]    # env; --variant orig|origin|rot
.venv/bin/python scripts/spec_fidelity.py run counter --backend vanilla --variant origin --workers 8
.venv/bin/python scripts/spec_fidelity.py run alu --backend vanilla --variant orig --workers 6 --only exh1_shard0 exh1_shard1 ...   # chunks of <= 6 captures (~5 min each); results merge in `report`
.venv/bin/python scripts/spec_fidelity.py sweep counter --backend vanilla --workers 8     # threshold sweeps
.venv/bin/python scripts/spec_fidelity.py controls                                        # negative controls
.venv/bin/python scripts/spec_fidelity.py report                                          # tables from data/fidelity/chunks/*.json -> data/fidelity/report.md
```
Outputs: `data/fidelity/` (gitignored): `chunks/*.json` (checks, per-tick RLE of every predicate), `sweep_*.json`, `controls.json`, `report.md`.

## Open problems and limits

* n = 3, one agent each; hidden specs by one author. Tier boundaries are my judgement; they are listed per scenario so they can be re-cut from the JSON (`tier`), and the verdict flips with them (section above).
* The hidden specs are in the repo (`scripts/spec_fidelity.py`). Before any builder run they have to be removed from, or not mounted into, the builder sandbox (`scripts/run_baseline_sandboxed.py`).
* Vanilla ALU coverage: all 1024 rows only at the original origin; origin / rot ran 256 rows + 100 fast steps + the flail; pass 2 and the own spec of the ALU ran in env only. One random origin and one random dihedral element per build (not the full group, not several origins).
* The tested artifact is the Nucleation-baked hand-over world pasted by the oracle's structure placement; the real Litematica paste path is the `paste-check` / paste-receipt work, not repeated here.
* Not tested: entities, items, hoppers (none in these builds); players walking through the door (clearance is checked as air cells); stone button vs other button types; presses while the door is moving other than via the 21-25 gap runs.
* The counter `final count` check is weak by design (errors can cancel mod 16: it passes at gaps 35 and 30 although the display is wrong in between); the every-press windows and the response check are the discriminating ones.
* env and vanilla differ by one tick at every threshold measured; which engine is "right" for a real Java 1.21 server is not established (the paste receipt server is 1.21.0, the oracle 26.2).
* Friction not fixed here (harness): none observed.
