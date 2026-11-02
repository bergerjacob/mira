# Validity of the measurements (round-2 audit follow-up, 2026-11-02)

Source: `docs/audits/oracle_round2_2026-11.md`. This file says what was wrong, what is fixed (with the measurement), how to gate a
reported number, and what is still open. Nothing here changes the simulator; it changes what the benchmark accepts and how runs are made.

## 1. What was wrong and what was done

| id | problem | status |
|---|---|---|
| H1 | `t2_door_2x2` accepted any non-air block in the door cells; sticky-piston heads (no pushed block) passed | **fixed** (section 2) |
| H2 | an agent read `lib/full_adder.py`; nothing stopped it | **fixed for runs made with the sandbox** (section 4) |
| M1 | vanilla oracle: batched jobs could trace a dead world | **root-caused and fixed** (section 3) |
| M2 | blocks arm lacked the builder arm's worked examples; `max_turns = 10 + 3R` mixed feedback with turn budget | **fixed, round-1 behaviour selectable** (section 5) |
| M3 | "T0-T3 do not discriminate strong agents" overclaimed | not addressed here (needs runs); see section 7 |

## 2. Door: pushed block required (H1)

* **New region mode `block_in`** (`env/contract.py`, `env/runner.py`, and the copy of the port reader in `scripts/oracle_env_diff.py`):
  a `region` port reads 1 iff every cell holds one of `Port.blocks` (an allowlist of block ids; states ignored). `solid` is unchanged
  (any non-air block, still used by nothing shipped) and is documented as accepting `piston_head` / `moving_piston`. A `block_in` port without
  a `blocks` list fails `Contract.validate()`. The JSON field `blocks` is optional, so every other task file loads unchanged.
* **`t2_door_2x2`** (`env/tasks/author.py`, `door_ports`): per door cell a port `D<i><j>` (`block_in`: stone, cobblestone, smooth_stone, stone_bricks,
  dirt, bricks, oak/spruce/birch planks, white_wool, netherrack) and a port `A<i><j>` (`air`). Sequences expect every port `init` / `!init` as before, so a door
  may rest open or closed, but each cell must be real air when open and a real pushed block when closed. A piston head or a moving piston is in neither list. Task text says so.
  The reference (stone pushed by the four sticky pistons) is unchanged and still reward 1.0; the stored broken variants are unchanged and fail;
  `validate_tasks.py env/tasks/t2_door_2x2.json --mutants 500`: ACCEPT, 94/98 mutants killed (same as before).
* **Regression tests** (`tests/env/test_door_validity.py`, 14): (a) the strong-agent session build (`tests/fixtures/door2x2_piston_head_exploit.json`, its
  program as `.py.txt`, checked to compile to exactly those blocks) now fails: R3 = 0, all four `D` ports never read 1; (b) the reference passes; (c) the stored broken variants fail;
  (d) meta-test: with the old ports (4 x `solid`, no `A` ports) the same build passes, so (a) has teeth; plus port semantics for air / stone / head /
  moving_piston / piston / wire in both modes. The vanilla oracle replay of that session also fails the new spec, tick-identical to Nucleation (section 6).
* **Side effect for another owner:** `tests/env/test_physics_facts.py::test_init_means_the_value_read_at_rest...` (T4 agent) asserted 4 `solid` door ports and that the
  lever turns them to "non-air (block or head)"; it needs `region_mode == "block_in"` ports and a real block.
* **3x3 door: not built, and not buildable in this contract.** With cells reachable only from above, below and the sides (the hallway
  `keep_clear` forbids pistons in front of or behind the plane) the centre cell can only be filled through a neighbouring door cell. A sticky
  piston pulls back only the block next to its head, so a two-block push chain leaves the second block in a cell when the door opens (and a chain that
  reaches the centre keeps a block or a head in an edge cell). A design needs a moving piston-on-piston structure or a sliding multi-stroke door;
  I did not find one and did not spend more time on it. 0/96 for Nekochu's 3x3 is consistent. Left as a research item; a flush or 3x3 door also needs
  its own red-team pass.

## 3. Oracle batching contamination (M1)

**Root cause.** `OracleBatch` ticketed and force-loaded the chunks of each job right before it, then waited for them to tick with a 600-tick cap that
expired *silently*. In a JVM that had already run jobs, chunks that no earlier job needed never reached ticking (reproduced with `t1_xor` or
`t2_door_2x2` followed by `t3_adder4`; `adder2` followed by `adder4` is fine because its chunks already cover adder4's): the chunk task queue
stayed pending (57-636 tasks) for 5000 ticks, also with real-time pacing, a synchronous `getChunk(FULL)`, 3 s of idle ticks, or no region wipe. The job then
traced a world where nothing ticked: lever/pad inputs had no effect, so a correct build "failed" (adder4: 49 failed checks instead of 8; the 8 are the
too-short `--hold 40`, section 6). Why the chunk scheduler stalls was not pinned down further (it only works for the first tickets, right after boot);
this is stated in the code comment. Not state leaking between jobs: entities, block entities, scheduled ticks, piston heads were all ruled out for this case.

**Fix** (`tools/oracle/java/OracleBatch.java`, `tools/oracle/gametest.py`):
1. the union of all job regions is ticketed and warmed once before the first job (works for every job order and size);
2. a region that is still not ticking is an error (`IllegalStateException` -> `capture_batch` raises), never a silent wrong trace;
3. the between-job wipe also clears scheduled block/fluid ticks and queued block events of the dirty region (as `/clone` does). This closes a *latent* leak found while testing:
   with `idle=0`, a repeater still scheduled at the end of one job fires in the next job's fresh repeater (test below); `idle=10` hid it for delays <= 10 ticks but not for
   longer schedules (buttons, plates: 20-30 ticks);
4. `gametest.py`: `capture_batch(..., isolated=True, workers=N)` and `capture_many(..., isolated=True)` run one JVM per job; `ORACLE_CLASSES` selects another class dir;
   `OracleBatch.class` is rebuilt automatically when the `.java` is newer.

**Measured** (`scripts/oracle_env_diff.py` on 34 tasks = every non-T4 task, 49 captures; hold 40, preroll 20):

| run | wall | result |
|---|---|---|
| pre-fix, batched (4 JVMs, round-robin shards) | 30 s | 2/49 captures differ from isolated: `t3_adder4` 49 vs 8 failed checks, `t3_adder8` 54 vs 16 |
| post-fix, batched | 28 s | identical to isolated on all 49 (verdicts, failures, tick-divergence summary) |
| post-fix, isolated (`--isolated`, 4 JVMs at a time) | 185 s | reference |

`tests/oracle/test_isolation.py` (6 tests, ~80 s, skipped when the oracle is not installed): adder4 after `t1_xor` / `t2_door_2x2` / `t3_adder2` is trace-identical
to adder4 alone; `isolated=True` and `capture_many(isolated=True)` equal the batch; the stale-scheduled-tick leak (fails on the old driver, passes now).

## 4. Sandboxed strong-agent runs (H2)

`scripts/run_baseline_sandboxed.py` (design, tests and limits in `docs/MCP.md`, "Baseline protocol"): bubblewrap sandbox without network; the agent sees only a
`mira` client, three docs (+ the library for variant `lib`) and `/tmp/work`; the session code runs host-side behind a unix-socket broker that pins the session and whitelists
subcommands. Verified by `tests/harness/test_sandbox.py` (10 tests). Host records: `broker.jsonl`, `shell.log`, copied session, `usage.json` (token counts via
`record-usage`), `summary.json`. **Not verified:** an actual strong agent run through it (the `subagent` tool was not used here), and that a harness that
launches agents keeps their other tools (read/grep) out of the repo; `audit` is the after-the-fact check.

## 5. Equalised arms (M2): what changed, so the grid is comparable

* `harness/prompts.py`: the blocks system prompt now contains `BLOCKS_EXAMPLES`, the same three worked examples as `evaluation/prompts/builder_system.md`
  (lever->lamp, NOT gate, AND from three inverters) as `fill` / `set_blocks` calls with the same titles and descriptions. `tests/harness/test_prompts.py` compiles both sets
  and asserts they place **identical blocks** (canonical states). The builder prompt is unchanged. The old prompt is selectable: `--no-blocks-examples` /
  `RunConfig(blocks_examples=False)` (tested byte-identical to the round-1 blocks prompt). Added ~2.4k characters (about 700 tokens) per blocks episode.
* `harness/agent.py`, `scripts/run_experiment.py`: `--turns-mode fixed|scaled` (`RunConfig.turns_mode`), **default `fixed` = 30 assistant turns for every R**; `scaled` is the round-1
  `10 + 3R` (R=0: 10, R=3: 19, R=8: 34); `--max-turns N` still overrides both. The turn cap is printed in the task prompt, so the model sees it.
  Effect on a rerun vs round 1: R=0 episodes get 30 turns instead of 10, R=8 get 30 instead of 34, R=3 get 30 instead of 19; with the examples a blocks R=0 model has the same help as the builder.
  `RunConfig.key` does not contain `turns_mode` / `blocks_examples`: use a fresh `--exp-id` for any rerun (resume would otherwise treat old runs as done).
* To reproduce the round-1 conditions exactly: `--turns-mode scaled --no-blocks-examples`. To measure the representation effect alone: defaults.

## 6. Oracle gate of every recorded session (`scripts/oracle_sessions.py`)

One JVM per capture (default; `--batched` to share), vectors held `--hold auto` (longest env settle x1.25 + 8, at least 40 ticks: the fixed 40 of `oracle_env_diff.py` is too short for
ripple adders, which is why its T3 adder rows show vanilla and Nucleation both "failing" the reference). Run 2026-11-02 on all five sessions (23 s, `data/oracle/sessions_r2.json`):

| session | task | stored | env now | vanilla | Nucleation == vanilla |
|---|---|---|---|---|---|
| t2_door_2x2-builder-R15-...-685c | t2_door_2x2 | pass | **FAIL** (spec fixed) | **FAIL** (open_close window 60-240: no block in the cells) | identical (1082 ticks) |
| t3_adder2-builder-R15-...-0dae (library) | t3_adder2 | pass | pass | pass | identical (3744 ticks) |
| t3_adder2-builder-R15-...-4b87 (raw) | t3_adder2 | pass | pass | pass | identical (3120 ticks) |
| t3_adder4-builder-R25-...-798b (library, read `lib/`) | t3_adder4 | pass | pass | pass | identical (4816 ticks) |
| t3_decoder2to4-builder-R15-...-e26a (library) | t3_decoder2to4 | pass | pass | pass | identical (1334 ticks) |

No env/vanilla verdict disagreement. This says the passes are real *physics*; it does not make the adder4 run clean (it still read `lib/`; see H2) or the door run valid (it is now a fail).

## 7. How to gate any reported number (checklist)

1. **Spec**: task accepted by `python env/validate_tasks.py <task>` at the commit used; the mutation kill rate and the spec-strength caveats of the task tier are stated (T3: 16-20 of 24 mutants);
   for every new task a strong agent has tried to find "the cheapest build that passes but a player would reject" and each loophole is a bug (the piston-head door was the first).
2. **Isolation**: strong-agent runs made through `run_baseline_sandboxed.py`; `audit` on the agent transcript is clean; `broker.jsonl` shows only the intended commands; one fresh agent context per attempt.
3. **Equal conditions across arms**: same R, same extras tier (stated), `--turns-mode fixed` (30), blocks examples on; the config in each transcript header says so. Round-1 numbers are not comparable without `--turns-mode scaled --no-blocks-examples`.
4. **Oracle gate**: every pass that is reported goes through `scripts/oracle_sessions.py` (isolated, `--hold auto`); require vanilla = pass and report every Nucleation/vanilla divergence. For OpenRouter-grid passes, export the final blocks first (not
   automated: harness transcripts hold the program text, not `final_blocks.json`).
5. **Report**: n episodes, seeds, models/ids, pass@1 with intervals, wall time, input/output tokens and cost per solved episode (`record-usage`), unsubmitted attempts separately, tier/extras.
6. **Never** quote: a pass of a session started before a spec change; a number from a run whose agent had repository read access; a vanilla verdict from a pre-2026-11-02 batched capture of a task whose region is larger than the first job's.

## 8. Remaining validity risks

* **T3 spec strength** is lower than T0-T2 (16-20 of 24 mutants killed vs 94.7%); a build can pass with a defect the specs do not exercise.
* **Hidden vectors for tasks with <= 3 inputs are the public table in reverse order** (`author.py`, `Plan.table`): they test order independence, not generalisation. Memorisation of the table passes.
* **Fixed port positions per task**: memorisation across runs/training; no random port placement, rotation or offset. Vanilla update order depends on absolute position, so a pass at one offset is not shown at others
  (audit: test 4 rotations x 3 offsets in the oracle; not done).
* **The oracle replays the spec's stimulus plan**, so it cannot find behaviour the spec never exercises (inputs changing together, latency, survival buildability: a piston-head or spammed redstone-block design can still be accepted if the allowlist/permission allows it).
  `block_in` allows 11 block ids in the door cells; a door of wool or bricks passes.
* **R2 can be gamed** (a connection with no correct behaviour earns it) and R4 has no latency term; do not train on them without the oracle spot-check.
* **Single run per cell, one model family, four distinct strong-agent tasks, mostly combinational** (M3): no strong agent has run the timing/state tasks, the T3 counters, adder8, or decoder3to8 without the library.
* `oracle_env_diff.py` with its fixed hold 40 reports T3 adder references as vanilla failures in both modes (hold too short); use `oracle_sessions.py --hold auto` logic for T3.
* The chunk-scheduler stall behind M1 is avoided, not understood; the guard (error on a non-ticking region) is what protects against a recurrence.
