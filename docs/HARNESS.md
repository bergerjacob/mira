# Harness: agent loop + experiment runner (iteration lift)

Answers `docs/audits/oracle_loop_2026-11.md` unknown #1, *how much does iterating with simulator feedback improve over one-shot?*,
with the environment from `docs/ENV.md` and the authoring layer from `docs/BUILDER.md`. Everything LLM-facing is testable with a
scripted model (no network, no key); the real runs are the parent's job (the build sandbox cannot reach openrouter.ai).

```
harness/llm.py         OpenRouter chat client (native tool calling, retries, usage.cost accounting), Budget (shared ledger), ScriptedLLM
harness/prompts.py     every prompt string (reproduced below)
harness/agent.py       RunConfig, Episode/run_episode (the loop), tool wiring per arm, transcript logging, final-feedback parser
harness/experiment.py  grid planning, resume, ProcessPool workers sharing one budget, graceful stop
harness/estimate.py    conservative cost model used by --dry-run (token sizes measured from a mock episode)
harness/analysis.py    statistics + markdown tables (no numpy)
harness/reference.py   scripted players: reference solution, flawed-then-fixed persona, builder source from a block list
scripts/run_experiment.py, scripts/analyze_experiment.py        CLIs
tests/harness/         offline tests (llm, agent, experiment, analysis, 22-task x 2-arm x 2-R reference end-to-end)
```
No `__init__.py` anywhere (repo convention); scripts put the project root on `sys.path`.

## Commands

```bash
source .venv/bin/activate
python -m pytest tests/harness -q                       # ~120 tests, ~30 s, offline
python -m pytest tests/unit tests/pipeline tests/env tests/oracle tests/harness -q     # whole offline suite: 859 pass + 6 strict xfail

# 0. plan only (no key needed): grid, per-condition $ estimate, verdict against the cap
python scripts/run_experiment.py --exp-id pilot1 --budget-usd 0.60 --pilot --dry-run
# 1. scripted smoke test of the whole pipeline ($0, no network, ledger lives inside the experiment dir)
python scripts/run_experiment.py --exp-id smoke --budget-usd 1 --mock --tasks t0,t1_xor --rounds 0,3 --seeds 1 --workers 4
python scripts/analyze_experiment.py --exp-id smoke --ledger data/experiments/smoke/ledger.jsonl
```

### Parent: pilot, then the real grid

```bash
export OPENROUTER_API_KEY=...            # or leave it in .env (loaded by harness/llm.py; never printed or logged)

# PILOT: 2 models (deepseek-v4-flash, qwen3.7-plus) x 6 tasks x both arms x R in {0,3} x 1 seed = 48 runs.
# The conservative estimate is $1.07 (real spend should be well under it); the cap stops it at $0.60 regardless.
python scripts/run_experiment.py --exp-id pilot1 --budget-usd 0.60 --pilot --dry-run
python scripts/run_experiment.py --exp-id pilot1 --budget-usd 0.60 --pilot
python scripts/analyze_experiment.py --exp-id pilot1 --out docs/experiments/pilot1.md

# Read data/experiments/pilot1/runs/*.jsonl for 2-3 runs per arm BEFORE spending more: do tool calls parse, does the model
# call submit, do both providers accept tool calling (a 404 "No endpoints found ... tool use" is free and means: pick another model).

# FULL GRID that fits the remaining ~$3.3 (cap $2.50): one cheap model, 21 T0/T1 tasks, both arms, R in {0,3,8}, 1 seed = 126 runs.
# Conservative estimate $1.87. Resumable: rerun the same command after any stop.
python scripts/run_experiment.py --exp-id lift1 --budget-usd 2.50 --tasks t0,t1 --models deepseek-v4-flash --seeds 1 --dry-run
python scripts/run_experiment.py --exp-id lift1 --budget-usd 2.50 --tasks t0,t1 --models deepseek-v4-flash --seeds 1
python scripts/analyze_experiment.py --exp-id lift1 --out docs/experiments/lift1.md
```

Useful flags: `--workers 6` (default), `--limit N` (first N runs of the ordered grid), `--arms blocks`, `--rounds 0,3,8`,
`--seeds N`, `--max-turns`, `--max-tokens` (per call, default 16384), `--max-output-tokens` (per episode, default 40000),
`--temperature` (0.7), `--reasoning low|medium|high`, `--keep-reasoning`, `--no-palette-hint`, `--no-require-params`,
`--ledger-cap-usd` (default 3.00), `--exp-root`. **`--budget-usd` has no default and is required.**
Stop a running experiment gracefully with `touch data/experiments/<exp>/STOP` (episodes end after their current turn; they are
recorded as incomplete and rerun on resume) or Ctrl-C (same effect). A re-run deletes a stale `STOP`.

Grid order is `seed > task > model > arm > R`, so when the budget stops a run the finished part is a set of whole tasks with
every condition (paired), not whole conditions missing.

## The episode (`harness/agent.py`)

One episode = one `(task, model, arm, R, seed)`. The model gets a system prompt and a task prompt, then loops: assistant reply
(possibly several parallel tool calls, executed in order) -> tool results -> next reply, until `submit()`.

| arm | tools | notes |
|---|---|---|
| `blocks` | `set_blocks, fill, remove, view, [run_spec], submit` | `env/tools.py` unchanged (`undo`, `probe`, `task_info` are not offered) |
| `builder` | `build(source), view, [run_spec], submit` | `build` runs `simulation/builder.compile` (sandboxed subprocess, 20 s) and loads the result as the CURRENT build (replacing the previous one). A program with errors is **not loaded** (the previous build stays) and all errors come back with line numbers. After compile the blocks go through the task rules (`env.runner.prepare`: build box, fixed cells, block budget, Nucleation registry/simulatability); violations also reject the load. Blocks identical to a fixed block (lever, lamp, floor) are ignored with a note; builder NBT is dropped with a warning |

* **R (feedback rounds).** `run_spec` is offered only when R > 0; a call is counted only if the env actually ran it, so the
  (R+1)-th call returns the env's "feedback budget exhausted" error and is not counted (`refused_run_spec` in the log). At R=0
  the tool is absent from the tool list and, if a model hallucinates the call, refused ("not available"); `Env(max_rounds=0)`
  is a second guard. Every tool result ends with `[turn k/N | run_spec j/R used]`.
* **What counts as feedback.** Simulator feedback only comes from `run_spec`. `view` is *always static*: the env's `live=True`
  simulated view is stripped from the tool schema and ignored if passed. Static validation (set_blocks rejections, builder compile
  errors, `view`) is free in every condition, including R=0, so "R=0" is "no simulation", not "no tooling".
* **Scoring.** `submit()` calls `Env.submit()`: hidden vectors, all invariants, `env.score` R0-R4 (`reward = 0.1 R0 + 0.1 R1 + 0.1 R2
  + 0.6 R3[R1=1] + 0.1 R4[R3=1]`). **Pass = `full_pass`** (all vectors and invariants, R1 = 1; implies R3 == 1.0).
  An episode that ends any other way is scored as it stands through the same call: `max_turns` (default `10 + 3R`),
  `token_cap` (completion tokens, default 40k per episode; `--max-tokens` 16384 per call), `no_tool_calls` (3 consecutive replies
  without a tool call, each answered with a nudge), `budget` / `no_credits` / `api_error` / `stopped`. Only the first group is
  `complete: true`; the last group is `complete: false`, stays pending, and is redone on resume (analysis ignores them but counts them).
  A run whose very first call is refused by the budget leaves no transcript at all.
* **Determinism / seeds.** The env has **no port randomisation** (ports are fixed in each task file). `seed` is (a) `Env(seed=)`,
  which orders the public vectors in `run_spec`, and (b) sent to the provider as `seed = 1000*seed + turn` for sampling (best effort at
  providers; reruns are not bit-identical). Scripted runs are fully deterministic (tested).
* **Tool-argument robustness.** Invalid JSON arguments, unknown tools, string-encoded `blocks`, float coordinates and exceptions
  inside a tool all become an `error: ...` tool message; nothing can crash an episode. Tool results are truncated at 7000 chars.

### Transcripts (`data/experiments/<exp>/runs/<task>__<model>__<arm>__R<R>__s<seed>.jsonl`)

One JSON object per line: `header` (key, task, tier, model, model_id, arm, R, seed, full config, tool names, **system prompt, task
prompt**), one `turn` per assistant reply (`assistant` message with tool calls, `finish_reason`, `usage` {prompt, completion, reasoning,
cached tokens}, `cost`, `cost_source` (`api` = OpenRouter `usage.cost`, `estimate` = price table fallback), `latency`, `results`
[{id, name, args, result}], `rounds_used`), and `final`: `complete, ended_by, submitted, turns, rounds_used, refused_run_spec,
score` (the full `Score` dict), `reward, full_pass, r0..r4, blocks`, `usage`, `cost_usd`, `build_attempts` (builder arm:
turn / ok / error kinds / stage), `spec_history` (public R1-R3 after each run_spec), `details` (parsed R0 errors, R1 violations,
failing behavioural invariants, missing R2 paths), `final_text` (the feedback shown at submit), `duration_s`. A run is DONE iff its
last line is a `final` with `complete: true`; that is the only resume criterion.

## LLM client and budget (`harness/llm.py`)

* `OpenRouterLLM(model, budget)`: `requests` POST to `/api/v1/chat/completions`, `tools` + `tool_choice: auto`, `usage: {include: true}`
  (cost comes back as `usage.cost`), optional `reasoning: {effort}`, `seed`, `provider.require_parameters: true` (so a model/provider
  without tool support fails fast with a free 404 instead of silently ignoring tools; `--no-require-params` disables). Model short names
  resolve through `OpenRouterClient.MODELS`; full ids pass through. Retries (5, exponential backoff with jitter, `Retry-After` honoured)
  on 408/409/425/429/5xx, connection errors and 200-without-choices; 402 raises `InsufficientCredits` (treated as a budget stop), other 4xx
  fail immediately. The key is read from `OPENROUTER_API_KEY` or the `export OPENROUTER_API_KEY=` line of `.env`, held in memory only,
  and appears in no log, ledger line, transcript or exception text (tested).
* `Budget(cap_usd, exp_id, ledger_path, ledger_cap_usd=None)`: before **every** call (all backends) the worst-case cost
  (`prompt_chars/3 * price_in + max_tokens * price_out`, price table = `OpenRouterClient.MODELS` comments, unknown model = $3/$15 per M)
  is *reserved* in `data/experiments/ledger.jsonl` under an `flock`; if `spent + reserve > cap` it raises `BudgetExceeded` and nothing is
  sent. After the call the reservation is settled with the real cost (0 on failure). Reservations of crashed processes expire after 15 min.
  `cap_usd` is per experiment (and required, no default anywhere); `--ledger-cap-usd` (default **3.00**) caps the sum over *all*
  experiments in the shared ledger, a second line of defence for the ~$3.3 left on the key. Spend that happened outside this ledger is
  unknown to it. Concurrency is tested with 6 processes racing for a $1.00 cap (exactly 100 x $0.01 succeed). `--mock` runs use
  `<exp>/ledger.jsonl` so they never touch the shared one.
* `ScriptedLLM(script)`: same interface and same budget path; `script` is a list of assistant messages (`assistant(text, [(tool, args)])`)
  or a callable; records every call for assertions; synthetic usage/cost.

## Cost model (`--dry-run`, `harness/estimate.py`)

Token sizes are measured from a scripted episode through the real harness (system+task prompt+tool specs, a build message, the view and
run_spec results; 3 chars/token). Each episode is modelled as `view -> build -> [run_spec -> fix] x R -> 2 slack turns -> submit`, every turn
re-sending the whole history, with: build message = 1.6 x the reference's size + 250 tokens, a fix = 0.6 of a build, 800 hidden reasoning
tokens per turn billed at the output price, no prompt-caching discount, x1.5 safety. Estimates (deepseek-v4-flash $0.09/$0.19 per M tokens,
qwen3.7-plus $0.32/$1.28 per M):

| plan | runs | conservative estimate |
|---|---|---|
| `--pilot` (2 models, 6 tasks, both arms, R 0/3, 1 seed) | 48 | $1.07 |
| `--tasks t0,t1 --models deepseek-v4-flash --seeds 1` (R 0/3/8, both arms) | 126 | $1.87 |
| same, 2 seeds | 252 | $3.74 (exceeds the cap; stops at $2.50, about two thirds of it) |
| same with deepseek-v4-flash + qwen3.7-plus, 1 seed | 252 | $9.94 |
| per run, deepseek-v4-flash blocks arm R=0 / 3 / 8 | | $0.004 / $0.010 / $0.025 (builder arm: $0.005 / $0.014 / $0.031) |

Prompt tokens dominate (33M prompt vs 5M output tokens for 396 runs): cost grows roughly quadratically with R because the history is re-sent.
The estimate is a planning figure, not a bound: **only the Budget is a bound**. No real-API run was possible here, so the estimator has
not been compared with a real bill; compare `usage` in the pilot transcripts with the estimate before scaling.

## Analysis (`scripts/analyze_experiment.py --exp-id X --out report.md`)

Sections: Setup (runs, spend from runs and from the ledger, ended-by counts), Headline (per model|ALL x arm pass rate at each R and the audit's
continuation criterion "R=8 pass >= 2x R=0 and > 60% on T1 tasks"), (1) pass rate (Wilson 95% CI) and mean reward / R3 by model x arm x R,
(2) iteration lift of R=3 and R=8 vs R=0 for pass rate and for mean reward: **paired by task** (a task's value = its mean over seeds, `ALL` rows
also over models), percentile **bootstrap 95% CI over tasks** (2000 resamples, fixed seed) for the absolute difference and for the ratio
(`n/a` when the R=0 mean is 0), (3) rounds used, turns, tokens, $/run, **$ and tokens per solved task**, submit rate, (4) per-task pass-rate
heatmap, (5) failure taxonomy by arm x R in gate order R0 > R1 > R2 > R3 (R0 by cause: outside build box, wrote fixed cell, reserved area,
unknown block/property, not simulatable, block budget, malformed; R1 by violated invariant; R2 missing path; R3 empty / wrong / partial / mostly
right / invariant-only) plus, for the builder arm, compile attempts by error kind and recovery, (6) caveats. The output format is this
report; there was no `docs/EXPERIMENT_TEMPLATE.md` in the repo to follow, so the section list above is the template.

## Prompt text (verbatim from `harness/prompts.py`)

### Task prompt (both arms)
`Contract.describe(public=True)` (description, world box and build box, `Block states:` gotcha line, fixed blocks, ports with kind/position/facing/doc,
public example vectors or timing sequences, "Hidden vectors/sequences of the same kind are also checked.") followed by:

```
Budget: at most {max_blocks} blocks of your own; at most {max_turns} assistant turns (each tool-calling reply is one turn).
{PALETTE} [+ contract.hints if the task has any]
## Feedback
{one-shot text for R=0 | "You may call run_spec() at most R times..." for R>0}
{blocks arm: Example set_blocks argument ... | builder arm: Example statements inside build(b) ...}
```
Palette hint (generic, identical for every task, `--no-palette-hint` removes it; the reference's block list is never shown):
> Typical palette: stone / smooth_stone (solid, conducts), glass (does not conduct), redstone_wire, repeater, comparator, redstone_torch / redstone_wall_torch, redstone_block, lever, stone_button, observer, piston, sticky_piston, redstone_lamp, target, slab. Not every block can be simulated; the tools tell you when one is refused.

Feedback text, R=0:
> ## Feedback
> This is a ONE-SHOT episode: there is no simulator feedback. You may inspect your layout with view(), then you must submit() your first complete build. You cannot run the specification before submitting.

Feedback text, R=3:
> ## Feedback
> You may call run_spec() at most 3 times. Each call simulates your current build against the public vectors and returns diagnostics (first divergence, waveforms, unreached ports). After that you must refine from your own reasoning and submit(). Spend the rounds wisely: build a complete candidate first, then iterate on the reported failures.

### System prompt, `blocks` arm
```
You are an expert Minecraft redstone engineer. You build redstone contraptions in a deterministic simulator of vanilla Minecraft
Java redstone by calling tools. A task gives you fixed devices (levers, lamps, pistons...), named ports, a build box and the
behaviour to meet. You never write the test; you place blocks, and your build is scored on the stated behaviour (public example
vectors plus hidden vectors of the same kind), on structural validity and on size.

## Tools
Call tools; do not describe a build in prose only. The build is whatever blocks you currently have placed.
- set_blocks(blocks): [[x,y,z,"minecraft:block[prop=value]"], ...]. Air removes a block. Invalid states, cells outside the build box
  and fixed cells are rejected with a message (the rest of the call is applied).
- fill(x1,y1,z1,x2,y2,z2,state) / remove(x1,y1,z1[,x2,y2,z2]): box edits.
- view(layer=N, axis="y"): ASCII slice of the world (fixed blocks + your blocks), with a legend. Static: it shows block states, not
  signal levels.
- run_spec(): (only when offered) simulate the build against the PUBLIC vectors and explain failures with waveforms.
- submit(): final scoring against all vectors, hidden ones included. It ends the episode. Submit when you are done or out of options;
  an episode that ends without submit is scored as it stands.

## Redstone facts this simulator enforces
- Coordinates: x east, y up, z south (north = -z). All coordinates are absolute task coordinates.
- Repeater and comparator `facing` is the side the signal comes FROM; the output is on the opposite side. A repeater that must
  carry a signal eastward is `minecraft:repeater[facing=west,delay=1]`. `delay` is 1..4 (each step = 2 game ticks).
- Wall torch, wall lever and button `facing` points AWAY from the block they are attached to: `redstone_wall_torch[facing=east]`
  hangs on the block to its west. `lever` defaults to face=wall; for a lever on the floor use `lever[face=floor,facing=east]`.
- Dust, repeaters, comparators, floor torches and floor levers need a solid supporting block below them; a component without
  support is rejected as "floating" (pop-off). Place redstone dust as bare `minecraft:redstone_wire` (the simulator derives the
  connections; stated connection properties break them).
- Dust carries power 15 and loses 1 per block: put a repeater at least every 14 dust blocks. In this simulator dust powers a
  solid block only when a straight dust line ends pointing into it; a lever, repeater output or torch powers the block it is
  attached to / faces.
- A redstone torch is lit by default and switches OFF while the block it is attached to is powered (NOT gate). A torch powers dust
  beside it and, for a floor torch, the block above it.
- Comparator in subtract mode outputs rear - max(side inputs). Feed side inputs from dust, repeaters, comparators or redstone blocks
  (do not rely on a lever beside a comparator: the simulator and the real game disagree). Dust strength matters there; a
  repeater restores a weak signal to 15.
- A repeater is locked while a repeater/comparator points into its side. Observers: `facing` is the direction they watch, the
  pulse leaves from the back. Pistons: `facing` is the push direction (sticky pistons pull back).
- Stone and smooth_stone conduct power; glass does not. A redstone_block is a constant power source.
- Time is in game ticks (20 = 1 s). Specs are checked with inputs applied in random order WITHOUT resetting the circuit between
  vectors, so stale state (latches, pulses still running) can make a build fail; hidden vectors of the same kind are also checked.

## Working style
Plan the signal path first (source -> transmission -> logic -> output), then write coordinates. Use few large set_blocks/fill calls
rather than many tiny ones. Smaller, simpler builds score slightly higher once they are correct.
```

### System prompt, `builder` arm
`evaluation/prompts/builder_system.md` unchanged, followed by this appendix (which overrides its "Reply with ONE fenced python block"):
```
---
# Harness protocol (this overrides "Reply with ONE fenced python block" above)

You are not replying with a code block. You pass the complete program as the `source` argument of the **build** tool. Each build()
call REPLACES the current build with the program's output. If the program has errors it is not loaded (the previous build, if any,
stays) and every error is returned with its line number.

- Coordinates in your program are the absolute task coordinates listed in the task (not relative to anything). Only the build box
  may be written.
- Fixed blocks (floor, levers, lamps, pistons given in the task) already exist in the world. Do not place them again (an identical
  block is ignored, a different block on a fixed cell is an error).
- Tools: build(source), view(layer, axis) for a static ASCII slice, run_spec() (only when offered) to simulate the current build against
  the PUBLIC vectors, submit() for final scoring against all vectors including hidden ones (ends the episode). An episode that ends
  without submit is scored as it stands.
- Block states are checked against Minecraft 1.21.0 names; the simulator refuses a few loadable-looking blocks (the tools say which).

## Redstone facts this simulator enforces
- Coordinates: x east, y up, z south (north = -z). All coordinates are absolute task coordinates.
- Repeater and comparator `facing` is the side the signal comes FROM; the output is on the opposite side. A repeater that must
  carry a signal eastward is `minecraft:repeater[facing=west,delay=1]`. `delay` is 1..4 (each step = 2 game ticks).
- Wall torch, wall lever and button `facing` points AWAY from the block they are attached to: `redstone_wall_torch[facing=east]`
  hangs on the block to its west. `lever` defaults to face=wall; for a lever on the floor use `lever[face=floor,facing=east]`.
- Dust, repeaters, comparators, floor torches and floor levers need a solid supporting block below them; a component without
  support is rejected as "floating" (pop-off). Place redstone dust as bare `minecraft:redstone_wire` (the simulator derives the
  connections; stated connection properties break them).
- Dust carries power 15 and loses 1 per block: put a repeater at least every 14 dust blocks. In this simulator dust powers a
  solid block only when a straight dust line ends pointing into it; a lever, repeater output or torch powers the block it is
  attached to / faces.
- A redstone torch is lit by default and switches OFF while the block it is attached to is powered (NOT gate). A torch powers dust
  beside it and, for a floor torch, the block above it.
- Comparator in subtract mode outputs rear - max(side inputs). Feed side inputs from dust, repeaters, comparators or redstone blocks
  (do not rely on a lever beside a comparator: the simulator and the real game disagree). Dust strength matters there; a
  repeater restores a weak signal to 15.
- A repeater is locked while a repeater/comparator points into its side. Observers: `facing` is the direction they watch, the
  pulse leaves from the back. Pistons: `facing` is the push direction (sticky pistons pull back).
- Stone and smooth_stone conduct power; glass does not. A redstone_block is a constant power source.
- Time is in game ticks (20 = 1 s). Specs are checked with inputs applied in random order WITHOUT resetting the circuit between
  vectors, so stale state (latches, pulses still running) can make a build fail; hidden vectors of the same kind are also checked.
```

### Tool descriptions
Taken from `env/tools.py` docstrings/schemas (`set_blocks`, `fill`, `remove`), with `view` rewritten as static (no `live`), `run_spec`
carrying the call cap, `submit` stating that it ends the episode, and the harness-defined `build(source)`.

## Verification done here

* `tests/harness`: budget (cap, settle, ttl, ledger cap, garbled lines, 6-process race), OpenRouter client against a fake session
  (payload, usage/cost parsing, price fallback, retry/backoff/Retry-After, 402, 4xx, no key leakage, budget before network), R=0 hides
  `run_spec`, run_spec cap and refusal, submit score == direct `env.runner.run` + `env.score` on the same blocks (3 tasks x 2 arms, plus a
  deliberately flawed build), unsubmitted ends scored as they stand, token/turn caps, nudges, malformed tool arguments, hidden vectors
  absent from prompts, builder compile/validation errors fed back and the previous build kept, fixed-block handling, transcript schema,
  determinism, seed pass-through, budget stop mid-episode and before the first turn, STOP file, grid order / pilot preset / required cap /
  dry run writes nothing, resume after a torn transcript, budget-stop then resume, ledger cap across experiments, parallel workers, repeated
  API errors abort the grid, statistics (Wilson, bootstrap, paired lift, ratio), failure classification, report generation on real logged runs.
* **End to end with the scripted reference player** (`tests/harness/test_reference_e2e.py`, `run_experiment.py --mock`): all 22 tasks x both
  arms x R in {0,1}: reward 1.0, full pass, R4 = 1.0 in all 88 episodes (builder source generated from the reference blocks as `b.points(...)`).
  A 44-run parallel mock grid (4 workers) finishes in about 6 s.

## Deviations, caveats and open problems

* **Public vectors are few, so run_spec feedback is weak.** E.g. `t1_xor` shows 2 of 8 vectors, `t0_and` 3 of 8, `t1_mux2` 3 of 16, most
  sequence tasks 1 of 2-3 sequences. A build can pass every public check and fail hidden ones: the stored broken xor variant `broken[0]`
  ("turn repeater [3,1,3] south->west") does exactly that (`run_spec`: "ALL PUBLIC CHECKS PASS", submit: 4/6 hidden). This caps the measurable
  lift from iteration and is a spec-publishing choice in `env/tasks/author.py` (not changed here). For tasks with <= 3 inputs publishing the
  full truth table costs nothing (nothing to overfit).
* `view` is forced static and `undo/probe/task_info` are not offered (`probe` is simulator feedback outside the R budget).
* R=0 still allows free static validation and several `view`/`set_blocks` turns; it is "first complete build submitted without any simulation".
* A builder program with *any* compile error is rejected whole (BUILDER.md loads the valid part and reports the rest; here a half-built circuit
  would blur the arms). Builder states are checked against the 1.21.0 registry, the env against Nucleation's: a block only one of them knows is
  rejected at the second check with the env's message.
* Prompt-level facts were taken from `docs/ENV.md`; the comparator/lever side-input claim was deliberately **not** stated because Nucleation and
  vanilla disagree on it (ORACLE.md divergence 1). Real-game parity is only established for the 22 reference solutions.
* Untested against the real API (sandbox): exact provider behaviour for tool-calling models, `provider.require_parameters` 404s, multi-turn tool use
  with thinking models (use `--keep-reasoning` if a model rejects the history), whether `usage.cost` is present for every provider (otherwise the
  price-table fallback is used and the ledger marks `cost_source: estimate`). A timed-out request that the provider still bills is invisible to the ledger.
* `AGENTS.md` should list `harness/`, `scripts/run_experiment.py`, `scripts/analyze_experiment.py`, `tests/harness/` (not owned by this task).
* Not done: module/hierarchy layer, repair/extend tracks, port randomisation, SFT-data export from transcripts (the JSONL has everything needed),
  caching-aware cost estimate.
