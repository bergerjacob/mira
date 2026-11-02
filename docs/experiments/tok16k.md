# Experiment tok16k: tok16k: 16k per-call output cap confirmation (deepseek builder R3, 7 hard tasks)

## Setup

- runs: 7 complete, 0 incomplete
- tasks: 7 (T0, T1); models: deepseek-v4-flash; arms: builder; R: [3]; seeds: [0]
- spend: $0.0591 in complete runs, $0.0591 in the ledger (incl. incomplete runs and unsettled reservations); cap $0.20
- ended by: {'submit': 4, 'token_cap': 1, 'no_tool_calls': 2}

## Headline

(need at least two R values to compare)

## 1. Pass rate and mean reward by model x arm x R

| model | arm | R | runs | pass | pass rate | Wilson 95% | mean reward | mean R3 |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | builder | 3 | 7 | 4/7 | 0.57 | [0.25, 0.84] | 0.688 | 0.600 |

## 2. Iteration lift (paired by task, bootstrap 95% CI over tasks)

### Pass rate

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|

### Mean reward

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|

## 3. Rounds, tokens and cost

| model | arm | R | runs | rounds used | turns | prompt tok/run | output tok/run | $/run | $ per solved | tokens per solved | submitted |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | builder | 3 | 7 | 1.43 | 5.7 | 59.3k | 32.4k | $0.0084 | $0.0148 | 161k | 4/7 |

## 4. Per-task pass rate

| task | deepseek-v4-flash<br>builder R=3 |
|---|---|
| t0_and | █ 1.00 |
| t0_nor | █ 1.00 |
| t1_clock | █ 1.00 |
| t1_edge_detector | · 0.00 |
| t1_mux2 | · 0.00 |
| t1_nand | █ 1.00 |
| t1_pulse_extender | · 0.00 |
| **pass rate** | 0.57 |

Legend: █ >= 0.99, ▓ >= 0.67, ▒ >= 0.34, ░ > 0, · 0 (pass rate over seeds).

## 5. Failure taxonomy

| class (gate order R0>R1>R2>R3) | builder R=3 |
|---|---|
| pass | 4 (57%) |
| R2: input->output path missing | 2 (29%) |
| R3: partial (0 < R3 < 0.99) | 1 (14%) |
| **runs** | 7 |

### Builder arm: compile attempts

15 build() attempts in 7 builder episodes: 2 failed (13%); 2 episodes had at least one failed attempt, 2 of them ended with a loaded build.

| error kind (attempts containing it) | attempts | share of failed |
|---|---|---|
| timeout | 2 | 100% |

## 6. Caveats

- The scorer is the Nucleation simulator (inner loop), not the real game. Tasks are author-written and validated only against this
  simulator and (reference solutions) the vanilla 26.2 oracle (docs/ORACLE.md); a model may exploit a simulator quirk.
- Hidden vectors are of the same kind as the public ones. `run_spec` shows public vectors only, `submit` runs everything.
- R=0 means no simulator feedback; static validation (set_blocks rejections, builder compile errors, static `view`) is available in
  every condition. Port positions are fixed per task (the env has no port randomisation); `seed` changes the sampling seed sent to
  the provider and the order of public vectors in run_spec. Providers treat `seed` as best effort, so reruns are not bit-identical.
- Tasks are the clusters of the bootstrap; with few tasks the CIs are wide. A partial run (budget stop) covers the first tasks of the
  grid only (grid order seed > task > model > arm > R).
