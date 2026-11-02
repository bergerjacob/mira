# Experiment 01: how much does the harness help? (2026-11-02)

Question (oracle_loop unknown #1): does simulator feedback ("rounds") and the builder authoring layer lift pass rate over a one-shot build? Raw outputs: `docs/experiments/{pilot1,main1,tok16k}.md`; transcripts under `data/experiments/` (gitignored).

## Setup
- Environment: `env/` tasks T0-T1 (18 of 21 reached before the budget ran out), hidden vectors, full pass = R3 == 1 with all invariants. Scored by the Nucleation simulator.
- Models: `deepseek/deepseek-v4-flash` (reasoning model) and `google/gemma-4-31b-it`, via OpenRouter. 1 seed, temperature 0.7.
- Arms: `blocks` (set_blocks/fill/remove tools) vs `builder` (Python Part API, compiled in the sandbox).
- Feedback rounds R in {0, 3, 8}: R is the number of `run_spec` calls allowed; R=0 means one-shot with no simulator feedback (static validation only).
- Spend: $2.00 (main1) + $0.38 (pilot) + small confirmation runs, against an OpenRouter key limit of $5 (about $0.9 left).

## Results (main1, 204 complete runs, 1 seed)
| arm | R=0 | R=3 | R=8 |
|---|---|---|---|
| blocks (both models) | 20% | 26% | 29% |
| builder (both models) | 47% | 62% | 55% |
| deepseek blocks | 39% | 44% | 56% |
| deepseek builder | 56% | 72% | 59% |
| gemma blocks | 0% | 6% | 0% |
| gemma builder | 38% | 50% | 50% |

1. **Authoring format dominates.** The builder arm beats the block-list arm by 27 points at R=0 for the two models together, and gemma goes from 0% to 38%. The block-list arm mostly fails R2 (no input-to-output path, 69% of failures); the builder arm halves that (29%). This is the most solid result, and it is consistent across both models and all R values.
2. **Feedback helps, modestly, and is noisy.** R=3 is +6 points (blocks) and +14 points (builder) over R=0, but the bootstrap 95% intervals include 0 for the builder arm and R=8 is not better than R=3. The pre-registered criterion (R=8 >= 2x R=0 and > 60% on T1) is **not met** for any model/arm. Cheap models use extra rounds poorly (more turns, more tokens, no more passes).
3. **Cost per solved task** is lowest for the builder arm at R=0 or R=3 ($0.006-0.008 for deepseek, $0.007-0.014 for gemma); blocks are 2-3x more expensive per solve for deepseek and nearly unaffordable for gemma.
4. **Never solved by either model in any condition:** t1_clock, t1_edge_detector, t1_mux2, t1_pulse_extender (sequential/timing or multi-gate circuits). Whether that is capability or harness limits is open (next point).

## A harness confound that was found and measured
The per-call output cap was 8192 tokens. deepseek-v4-flash is a reasoning model and spent 6-8k of it on thinking; about 17% of its runs ended with `finish_reason=length` and no tool call (`no_tool_calls` + `token_cap` = 34 of 204). `reasoning=low` did not reduce this (the provider ignored it). Confirmation run `tok16k` (deepseek, builder, R=3, same 7 hard tasks, 16k per-call cap): 4/7 passed against 3/7 in main1; t1_clock was rescued, t1_edge_detector, t1_mux2 and t1_pulse_extender are still failing, 2 of 7 episodes still ended `no_tool_calls`. So the cap cost roughly one task in seven on the hard subset and the headline conclusions stand, but the absolute numbers for deepseek are slightly pessimistic. The harness default is now 16384 per call.

## Strong-agent baselines (no OpenRouter; Sonnet-class subagents through `scripts/mira_env_cli.py`)
All five attempts passed the hidden checks:
| task | variant | run_spec calls | note |
|---|---|---|---|
| t2_door_2x2 | primitives only | 1 | 40 blocks, 3 min |
| t3_adder2 | primitives only | 3 | 224 blocks; most of the effort was pen-and-paper layout |
| t3_adder2 | with library | 2 | 293 blocks, R4 lower (0.78) |
| t3_decoder2to4 | with library | 1 | recipe nearly given by docs/LIBRARY.md |
| t3_adder4 | with library | 1 | needed a local workaround for a library bug |

So T0-T3 do not discriminate strong agents; the benchmark needs a harder tier (T4, being built) and the S50 curve. The baselines reported tooling friction (library `floor=False` bug, `wire_path` with fixed cells, undocumented physics facts), which is being fixed in `docs/T4.md`.

## What this does and does not show
- It does show: representation (builder code vs block lists) matters more than extra feedback rounds for cheap models, and the environment/harness work end to end at about $0.005-0.02 per episode.
- It does not show: that iteration is unhelpful in general. Only two cheap models, one seed, 18 tasks, wide intervals, and a known truncation problem. Strong models solve T0-T3 in 1-3 rounds, so feedback matters where one-shot fails, which these tasks barely probe.
- Decision: keep the builder arm as the default authoring interface; do not spend more on cheap-model grids until T4 exists and the key is topped up. Next measurement: strong models on T4 (and S50) with/without library and compiler tiers.
