# Experiment pilot1: Pilot 1: iteration lift, 6 tasks, 2 cheap models

## Setup

- runs: 47 complete, 1 incomplete ({'unfinished': 1})
- tasks: 6 (T0, T1); models: deepseek-v4-flash, gemma-4-31b; arms: blocks, builder; R: [0, 3]; seeds: [0]
- spend: $0.3814 in complete runs, $0.3837 in the ledger (incl. incomplete runs and unsettled reservations); cap $0.50
- ended by: {'no_tool_calls': 10, 'submit': 27, 'max_turns': 9, 'token_cap': 1}

## Headline

- **deepseek-v4-flash / blocks**: pass rate R=0: 17%, R=3: 33%
- **deepseek-v4-flash / builder**: pass rate R=0: 33%, R=3: 67%
- **gemma-4-31b / blocks**: pass rate R=0: 0%, R=3: 17%
- **gemma-4-31b / builder**: pass rate R=0: 33%, R=3: 33%
- **ALL / blocks**: pass rate R=0: 9%, R=3: 25%
- **ALL / builder**: pass rate R=0: 33%, R=3: 50%

## 1. Pass rate and mean reward by model x arm x R

| model | arm | R | runs | pass | pass rate | Wilson 95% | mean reward | mean R3 |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | 0 | 6 | 1/6 | 0.17 | [0.03, 0.56] | 0.333 | 0.167 |
| deepseek-v4-flash | blocks | 3 | 6 | 2/6 | 0.33 | [0.10, 0.70] | 0.483 | 0.333 |
| deepseek-v4-flash | builder | 0 | 6 | 2/6 | 0.33 | [0.10, 0.70] | 0.467 | 0.333 |
| deepseek-v4-flash | builder | 3 | 6 | 4/6 | 0.67 | [0.30, 0.90] | 0.733 | 0.667 |
| gemma-4-31b | blocks | 0 | 5 | 0/5 | 0.00 | [0.00, 0.43] | 0.200 | 0.000 |
| gemma-4-31b | blocks | 3 | 6 | 1/6 | 0.17 | [0.03, 0.56] | 0.333 | 0.167 |
| gemma-4-31b | builder | 0 | 6 | 2/6 | 0.33 | [0.10, 0.70] | 0.550 | 0.417 |
| gemma-4-31b | builder | 3 | 6 | 2/6 | 0.33 | [0.10, 0.70] | 0.490 | 0.417 |

## 2. Iteration lift (paired by task, bootstrap 95% CI over tasks)

### Pass rate

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | R=3 vs R=0 | 6 | 0.17 | 0.33 | +0.17 | [+0.00, +0.50] | 2.00x [1.00, 4.00] |
| deepseek-v4-flash | builder | R=3 vs R=0 | 6 | 0.33 | 0.67 | +0.33 | [+0.00, +0.67] | 2.00x [1.00, 5.00] |
| gemma-4-31b | blocks | R=3 vs R=0 | 5 | 0.00 | 0.20 | +0.20 | [+0.00, +0.60] | n/a |
| gemma-4-31b | builder | R=3 vs R=0 | 6 | 0.33 | 0.33 | +0.00 | [+0.00, +0.00] | 1.00x [1.00, 1.00] |
| ALL | blocks | R=3 vs R=0 | 6 | 0.08 | 0.25 | +0.17 | [+0.00, +0.33] | 3.00x [2.00, 5.00] |
| ALL | builder | R=3 vs R=0 | 6 | 0.33 | 0.50 | +0.17 | [+0.00, +0.33] | 1.50x [1.00, 3.50] |

### Mean reward

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | R=3 vs R=0 | 6 | 0.33 | 0.48 | +0.15 | [+0.00, +0.41] | 1.45x [1.00, 2.99] |
| deepseek-v4-flash | builder | R=3 vs R=0 | 6 | 0.47 | 0.73 | +0.27 | [+0.00, +0.53] | 1.57x [1.00, 3.00] |
| gemma-4-31b | blocks | R=3 vs R=0 | 5 | 0.20 | 0.36 | +0.16 | [+0.00, +0.48] | 1.80x [1.00, 3.40] |
| gemma-4-31b | builder | R=3 vs R=0 | 6 | 0.55 | 0.49 | -0.06 | [-0.17, +0.00] | 0.89x [0.69, 1.00] |
| ALL | blocks | R=3 vs R=0 | 6 | 0.27 | 0.41 | +0.14 | [+0.01, +0.27] | 1.53x [1.04, 2.03] |
| ALL | builder | R=3 vs R=0 | 6 | 0.51 | 0.61 | +0.10 | [-0.05, +0.26] | 1.20x [0.89, 1.69] |

## 3. Rounds, tokens and cost

| model | arm | R | runs | rounds used | turns | prompt tok/run | output tok/run | $/run | $ per solved | tokens per solved | submitted |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | blocks | 0 | 6 | 0.00 | 3.0 | 7.9k | 16.9k | $0.0177 | $0.1059 | 149k | 2/6 |
| deepseek-v4-flash | blocks | 3 | 6 | 1.00 | 6.2 | 45.9k | 22.9k | $0.0195 | $0.0586 | 206k | 3/6 |
| deepseek-v4-flash | builder | 0 | 6 | 0.00 | 4.2 | 42.5k | 13.2k | $0.0044 | $0.0133 | 167k | 4/6 |
| deepseek-v4-flash | builder | 3 | 6 | 0.67 | 3.2 | 20.5k | 11.9k | $0.0035 | $0.0052 | 49k | 4/6 |
| gemma-4-31b | blocks | 0 | 5 | 0.00 | 8.2 | 20.9k | 1.6k | $0.0026 | n/a | n/a | 1/5 |
| gemma-4-31b | blocks | 3 | 6 | 0.50 | 17.2 | 51.6k | 2.3k | $0.0058 | $0.0346 | 323k | 1/6 |
| gemma-4-31b | builder | 0 | 6 | 0.00 | 2.5 | 14.6k | 1.8k | $0.0026 | $0.0077 | 49k | 6/6 |
| gemma-4-31b | builder | 3 | 6 | 2.50 | 6.8 | 50.8k | 4.6k | $0.0080 | $0.0240 | 166k | 6/6 |

## 4. Per-task pass rate

| task | deepseek-v4-flash<br>blocks R=0 | deepseek-v4-flash<br>blocks R=3 | deepseek-v4-flash<br>builder R=0 | deepseek-v4-flash<br>builder R=3 | gemma-4-31b<br>blocks R=0 | gemma-4-31b<br>blocks R=3 | gemma-4-31b<br>builder R=0 | gemma-4-31b<br>builder R=3 |
|---|---|---|---|---|---|---|---|---|
| t0_and | · 0.00 | █ 1.00 | · 0.00 | █ 1.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| t0_lamp | █ 1.00 | █ 1.00 | █ 1.00 | █ 1.00 | · 0.00 | █ 1.00 | █ 1.00 | █ 1.00 |
| t0_not | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 |  | · 0.00 | · 0.00 | · 0.00 |
| t1_nand | · 0.00 | · 0.00 | · 0.00 | █ 1.00 | · 0.00 | · 0.00 | █ 1.00 | █ 1.00 |
| t1_pulse_extender | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| t1_xor | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 | · 0.00 |
| **pass rate** | 0.17 | 0.33 | 0.33 | 0.67 | 0.00 | 0.17 | 0.33 | 0.33 |

Legend: █ >= 0.99, ▓ >= 0.67, ▒ >= 0.34, ░ > 0, · 0 (pass rate over seeds).

## 5. Failure taxonomy

| class (gate order R0>R1>R2>R3) | blocks R=0 | blocks R=3 | builder R=0 | builder R=3 |
|---|---|---|---|---|
| pass | 1 (9%) | 3 (25%) | 4 (33%) | 6 (50%) |
| R1: no_popoff |  |  |  | 1 (8%) |
| R2: input->output path missing | 10 (91%) | 8 (67%) | 6 (50%) | 4 (33%) |
| R3: behaviour wrong (output constant / inverted) |  | 1 (8%) | 1 (8%) | 1 (8%) |
| R3: mostly right (0.5 <= R3 < 0.99) |  |  | 1 (8%) |  |
| **runs** | 11 | 12 | 12 | 12 |

### Builder arm: compile attempts

44 build() attempts in 24 builder episodes: 8 failed (18%); 7 episodes had at least one failed attempt, 7 of them ended with a loaded build.

| error kind (attempts containing it) | attempts | share of failed |
|---|---|---|
| r0_check | 7 | 88% |
| bad_value | 1 | 12% |

## 6. Caveats

- The scorer is the Nucleation simulator (inner loop), not the real game. Tasks are author-written and validated only against this
  simulator and (reference solutions) the vanilla 26.2 oracle (docs/ORACLE.md); a model may exploit a simulator quirk.
- Hidden vectors are of the same kind as the public ones. `run_spec` shows public vectors only, `submit` runs everything.
- R=0 means no simulator feedback; static validation (set_blocks rejections, builder compile errors, static `view`) is available in
  every condition. Port positions are fixed per task (the env has no port randomisation); `seed` changes the sampling seed sent to
  the provider and the order of public vectors in run_spec. Providers treat `seed` as best effort, so reruns are not bit-identical.
- Tasks are the clusters of the bootstrap; with few tasks the CIs are wide. A partial run (budget stop) covers the first tasks of the
  grid only (grid order seed > task > model > arm > R).
