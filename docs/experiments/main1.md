# Experiment main1: Experiment 01 (main1): iteration lift, builder vs blocks, T0-T1, 2 cheap models

## Setup

- runs: 204 complete, 7 incomplete ({'unfinished': 5, 'budget': 2})
- tasks: 18 (T0, T1); models: deepseek-v4-flash, gemma-4-31b; arms: blocks, builder; R: [0, 3, 8]; seeds: [0]
- spend: $1.9406 in complete runs, $1.9993 in the ledger (incl. incomplete runs and unsettled reservations); cap $2.00
- ended by: {'no_tool_calls': 23, 'submit': 126, 'token_cap': 11, 'max_turns': 44}

## Headline

- **deepseek-v4-flash / blocks**: pass rate R=0: 39%, R=3: 44%, R=8: 56% -> criterion (R=8 >= 2x R=0 and > 60% on T1: 0.25): **not met**
- **deepseek-v4-flash / builder**: pass rate R=0: 56%, R=3: 72%, R=8: 59% -> criterion (R=8 >= 2x R=0 and > 60% on T1: 0.43): **not met**
- **gemma-4-31b / blocks**: pass rate R=0: 0%, R=3: 6%, R=8: 0% -> criterion (R=8 >= 2x R=0 and > 60% on T1: 0.00): **not met**
- **gemma-4-31b / builder**: pass rate R=0: 38%, R=3: 50%, R=8: 50% -> criterion (R=8 >= 2x R=0 and > 60% on T1: 0.20): **not met**
- **ALL / blocks**: pass rate R=0: 20%, R=3: 26%, R=8: 29% -> criterion (R=8 >= 2x R=0 and > 60% on T1: 0.13): **not met**
- **ALL / builder**: pass rate R=0: 47%, R=3: 62%, R=8: 55% -> criterion (R=8 >= 2x R=0 and > 60% on T1: 0.33): **not met**

## 1. Pass rate and mean reward by model x arm x R

| model | arm | R | runs | pass | pass rate | Wilson 95% | mean reward | mean R3 |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | 0 | 18 | 7/18 | 0.39 | [0.20, 0.61] | 0.531 | 0.400 |
| deepseek-v4-flash | blocks | 3 | 18 | 8/18 | 0.44 | [0.25, 0.66] | 0.573 | 0.456 |
| deepseek-v4-flash | blocks | 8 | 18 | 10/18 | 0.56 | [0.34, 0.75] | 0.639 | 0.556 |
| deepseek-v4-flash | builder | 0 | 18 | 10/18 | 0.56 | [0.34, 0.75] | 0.649 | 0.556 |
| deepseek-v4-flash | builder | 3 | 18 | 13/18 | 0.72 | [0.49, 0.88] | 0.782 | 0.722 |
| deepseek-v4-flash | builder | 8 | 17 | 10/17 | 0.59 | [0.36, 0.78] | 0.679 | 0.588 |
| gemma-4-31b | blocks | 0 | 17 | 0/17 | 0.00 | [0.00, 0.18] | 0.200 | 0.000 |
| gemma-4-31b | blocks | 3 | 17 | 1/17 | 0.06 | [0.01, 0.27] | 0.244 | 0.059 |
| gemma-4-31b | blocks | 8 | 17 | 0/17 | 0.00 | [0.00, 0.18] | 0.200 | 0.000 |
| gemma-4-31b | builder | 0 | 16 | 6/16 | 0.38 | [0.18, 0.61] | 0.529 | 0.450 |
| gemma-4-31b | builder | 3 | 16 | 8/16 | 0.50 | [0.28, 0.72] | 0.616 | 0.500 |
| gemma-4-31b | builder | 8 | 14 | 7/14 | 0.50 | [0.27, 0.73] | 0.644 | 0.557 |

## 2. Iteration lift (paired by task, bootstrap 95% CI over tasks)

### Pass rate

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | R=3 vs R=0 | 18 | 0.39 | 0.44 | +0.06 | [+0.00, +0.17] | 1.14x [1.00, 1.75] |
| deepseek-v4-flash | blocks | R=8 vs R=0 | 18 | 0.39 | 0.56 | +0.17 | [+0.00, +0.33] | 1.43x [1.00, 2.50] |
| deepseek-v4-flash | builder | R=3 vs R=0 | 18 | 0.56 | 0.72 | +0.17 | [-0.06, +0.39] | 1.30x [0.91, 2.00] |
| deepseek-v4-flash | builder | R=8 vs R=0 | 17 | 0.53 | 0.59 | +0.06 | [-0.12, +0.24] | 1.11x [0.79, 1.67] |
| gemma-4-31b | blocks | R=3 vs R=0 | 17 | 0.00 | 0.06 | +0.06 | [+0.00, +0.18] | n/a |
| gemma-4-31b | blocks | R=8 vs R=0 | 17 | 0.00 | 0.00 | +0.00 | [+0.00, +0.00] | n/a |
| gemma-4-31b | builder | R=3 vs R=0 | 15 | 0.40 | 0.47 | +0.07 | [+0.00, +0.20] | 1.17x [1.00, 1.80] |
| gemma-4-31b | builder | R=8 vs R=0 | 13 | 0.46 | 0.54 | +0.08 | [+0.00, +0.23] | 1.17x [1.00, 2.00] |
| ALL | blocks | R=3 vs R=0 | 18 | 0.22 | 0.28 | +0.06 | [+0.00, +0.14] | 1.25x [1.00, 2.00] |
| ALL | blocks | R=8 vs R=0 | 18 | 0.22 | 0.31 | +0.08 | [+0.00, +0.17] | 1.38x [1.00, 2.33] |
| ALL | builder | R=3 vs R=0 | 18 | 0.50 | 0.64 | +0.14 | [+0.00, +0.31] | 1.28x [1.00, 1.80] |
| ALL | builder | R=8 vs R=0 | 17 | 0.47 | 0.53 | +0.06 | [-0.09, +0.24] | 1.12x [0.82, 1.64] |

### Mean reward

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | R=3 vs R=0 | 18 | 0.53 | 0.57 | +0.04 | [+0.00, +0.12] | 1.08x [1.00, 1.29] |
| deepseek-v4-flash | blocks | R=8 vs R=0 | 18 | 0.53 | 0.64 | +0.11 | [-0.02, +0.25] | 1.20x [0.97, 1.58] |
| deepseek-v4-flash | builder | R=3 vs R=0 | 18 | 0.65 | 0.78 | +0.13 | [-0.04, +0.31] | 1.21x [0.94, 1.61] |
| deepseek-v4-flash | builder | R=8 vs R=0 | 17 | 0.63 | 0.68 | +0.05 | [-0.08, +0.19] | 1.08x [0.88, 1.37] |
| gemma-4-31b | blocks | R=3 vs R=0 | 17 | 0.20 | 0.24 | +0.04 | [+0.00, +0.13] | 1.22x [1.00, 1.66] |
| gemma-4-31b | blocks | R=8 vs R=0 | 17 | 0.20 | 0.20 | +0.00 | [+0.00, +0.00] | 1.00x [1.00, 1.00] |
| gemma-4-31b | builder | R=3 vs R=0 | 15 | 0.55 | 0.59 | +0.04 | [-0.03, +0.14] | 1.07x [0.95, 1.29] |
| gemma-4-31b | builder | R=8 vs R=0 | 13 | 0.58 | 0.68 | +0.09 | [-0.01, +0.25] | 1.16x [0.98, 1.53] |
| ALL | blocks | R=3 vs R=0 | 18 | 0.39 | 0.43 | +0.04 | [+0.00, +0.10] | 1.11x [1.00, 1.29] |
| ALL | blocks | R=8 vs R=0 | 18 | 0.39 | 0.44 | +0.05 | [-0.01, +0.13] | 1.14x [0.98, 1.37] |
| ALL | builder | R=3 vs R=0 | 18 | 0.61 | 0.72 | +0.11 | [-0.01, +0.23] | 1.17x [0.99, 1.44] |
| ALL | builder | R=8 vs R=0 | 17 | 0.59 | 0.65 | +0.06 | [-0.06, +0.18] | 1.09x [0.90, 1.35] |

## 3. Rounds, tokens and cost

| model | arm | R | runs | rounds used | turns | prompt tok/run | output tok/run | $/run | $ per solved | tokens per solved | submitted |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | 0 | 18 | 0.00 | 4.1 | 20.6k | 15.5k | $0.0060 | $0.0153 | 93k | 11/18 |
| deepseek-v4-flash | blocks | 3 | 18 | 0.94 | 7.9 | 68.7k | 18.8k | $0.0081 | $0.0183 | 197k | 8/18 |
| deepseek-v4-flash | blocks | 8 | 18 | 1.06 | 7.1 | 63.6k | 18.5k | $0.0101 | $0.0181 | 148k | 10/18 |
| deepseek-v4-flash | builder | 0 | 18 | 0.00 | 3.8 | 29.4k | 10.4k | $0.0044 | $0.0079 | 72k | 14/18 |
| deepseek-v4-flash | builder | 3 | 18 | 1.00 | 4.8 | 50.7k | 11.1k | $0.0047 | $0.0064 | 86k | 13/18 |
| deepseek-v4-flash | builder | 8 | 17 | 2.35 | 9.5 | 132.7k | 20.6k | $0.0098 | $0.0167 | 261k | 9/17 |
| gemma-4-31b | blocks | 0 | 17 | 0.00 | 9.5 | 37.2k | 2.7k | $0.0051 | n/a | n/a | 4/17 |
| gemma-4-31b | blocks | 3 | 17 | 0.18 | 19.0 | 98.5k | 4.1k | $0.0121 | $0.2063 | 1744k | 2/17 |
| gemma-4-31b | blocks | 8 | 17 | 0.18 | 34.0 | 291.4k | 6.3k | $0.0313 | n/a | n/a | 9/17 |
| gemma-4-31b | builder | 0 | 16 | 0.00 | 2.4 | 15.4k | 2.1k | $0.0028 | $0.0074 | 47k | 16/16 |
| gemma-4-31b | builder | 3 | 16 | 2.06 | 6.1 | 51.7k | 4.0k | $0.0068 | $0.0135 | 111k | 16/16 |
| gemma-4-31b | builder | 8 | 14 | 4.14 | 10.7 | 133.8k | 6.3k | $0.0142 | $0.0284 | 280k | 14/14 |

## 4. Per-task pass rate

| task | deepseek-v4-flash<br>blocks R=0 | deepseek-v4-flash<br>blocks R=3 | deepseek-v4-flash<br>blocks R=8 | deepseek-v4-flash<br>builder R=0 | deepseek-v4-flash<br>builder R=3 | deepseek-v4-flash<br>builder R=8 | gemma-4-31b<br>blocks R=0 | gemma-4-31b<br>blocks R=3 | gemma-4-31b<br>blocks R=8 | gemma-4-31b<br>builder R=0 | gemma-4-31b<br>builder R=3 | gemma-4-31b<br>builder R=8 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t0_and | · 0.00 | · 0.00 | █ 1.00 | · 0.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| t0_delay | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 |
| t0_fanout | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |  |
| t0_lamp | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 |
| t0_nor | · 0.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |  | · 0.00 |
| t0_not | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | █ 1.00 | · 0.00 |  | █ 1.00 | · 0.00 |
| t0_or | · 0.00 | █ 1.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 |
| t0_pad_lamp | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 |
| t0_piston | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 |
| t0_wire_long | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 |
| t1_and3 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | · 0.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| t1_clock | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| t1_edge_detector | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| t1_mux2 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| t1_nand | · 0.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |  |
| t1_observer | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 |
| t1_pulse_extender | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |  |
| t1_repeater_lock | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 |  |  |  |  |  |  |  |
| **pass rate** | 0.39 | 0.44 | 0.56 | 0.56 | 0.72 | 0.59 | 0.00 | 0.06 | 0.00 | 0.38 | 0.50 | 0.50 |

Legend: █ >= 0.99, ▓ >= 0.67, ▒ >= 0.34, ░ > 0, · 0 (pass rate over seeds).

## 5. Failure taxonomy

| class (gate order R0>R1>R2>R3) | blocks R=0 | blocks R=3 | blocks R=8 | builder R=0 | builder R=3 | builder R=8 |
|---|---|---|---|---|---|---|
| pass | 7 (20%) | 9 (26%) | 10 (29%) | 16 (47%) | 21 (62%) | 17 (55%) |
| R1: no_popoff | 1 (3%) | 1 (3%) | 1 (3%) | 3 (9%) | 4 (12%) | 3 (10%) |
| R1: no_popoff+paste_stable |  |  |  | 1 (3%) |  |  |
| R2: input->output path missing | 24 (69%) | 23 (66%) | 24 (69%) | 10 (29%) | 6 (18%) | 9 (29%) |
| R3: behaviour wrong (output constant / inverted) | 2 (6%) | 1 (3%) |  | 4 (12%) | 3 (9%) | 1 (3%) |
| R3: mostly right (0.5 <= R3 < 0.99) |  |  |  |  |  | 1 (3%) |
| R3: partial (0 < R3 < 0.99) | 1 (3%) | 1 (3%) |  |  |  |  |
| **runs** | 35 | 35 | 35 | 34 | 34 | 31 |

### Builder arm: compile attempts

278 build() attempts in 99 builder episodes: 40 failed (14%); 27 episodes had at least one failed attempt, 27 of them ended with a loaded build.

| error kind (attempts containing it) | attempts | share of failed |
|---|---|---|
| r0_check | 34 | 85% |
| bad_value | 6 | 15% |

## 6. Caveats

- The scorer is the Nucleation simulator (inner loop), not the real game. Tasks are author-written and validated only against this
  simulator and (reference solutions) the vanilla 26.2 oracle (docs/ORACLE.md); a model may exploit a simulator quirk.
- Hidden vectors are of the same kind as the public ones. `run_spec` shows public vectors only, `submit` runs everything.
- R=0 means no simulator feedback; static validation (set_blocks rejections, builder compile errors, static `view`) is available in
  every condition. Port positions are fixed per task (the env has no port randomisation); `seed` changes the sampling seed sent to
  the provider and the order of public vectors in run_spec. Providers treat `seed` as best effort, so reruns are not bit-identical.
- Tasks are the clusters of the bootstrap; with few tasks the CIs are wide. A partial run (budget stop) covers the first tasks of the
  grid only (grid order seed > task > model > arm > R).
