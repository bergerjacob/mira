# Experiment local_qwen3-4b: Local baseline local_qwen3-4b

## Setup

- runs: 42 complete, 0 incomplete
- tasks: 21 (T0, T1); models: qwen3-4b; arms: builder; R: [0, 3]; seeds: [0]
- spend: $0.0000 in complete runs, $0.0000 in the ledger (incl. incomplete runs and unsettled reservations); cap $1.00
- ended by: {'submit': 16, 'no_tool_calls': 26}

## Headline

- **qwen3-4b / builder**: pass rate R=0: 5%, R=3: 10%
- **ALL / builder**: pass rate R=0: 5%, R=3: 10%

## 1. Pass rate and mean reward by model x arm x R

| model | arm | R | runs | pass | pass rate | Wilson 95% | mean reward | mean R3 |
|---|---|---|---|---|---|---|---|---|
| qwen3-4b | builder | 0 | 21 | 1/21 | 0.05 | [0.01, 0.23] | 0.261 | 0.071 |
| qwen3-4b | builder | 3 | 21 | 2/21 | 0.10 | [0.03, 0.29] | 0.280 | 0.095 |

## 2. Iteration lift (paired by task, bootstrap 95% CI over tasks)

### Pass rate

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|
| qwen3-4b | builder | R=3 vs R=0 | 21 | 0.05 | 0.10 | +0.05 | [-0.10, +0.24] | 2.00x [0.00, 5.00] |
| ALL | builder | R=3 vs R=0 | 21 | 0.05 | 0.10 | +0.05 | [-0.10, +0.24] | 2.00x [0.00, 5.00] |

### Mean reward

| model | arm | comparison | tasks | R=0 | R=k | lift (abs) | 95% CI | ratio |
|---|---|---|---|---|---|---|---|---|
| qwen3-4b | builder | R=3 vs R=0 | 21 | 0.26 | 0.28 | +0.02 | [-0.11, +0.16] | 1.07x [0.66, 1.74] |
| ALL | builder | R=3 vs R=0 | 21 | 0.26 | 0.28 | +0.02 | [-0.11, +0.16] | 1.07x [0.66, 1.74] |

## 3. Rounds, tokens and cost

| model | arm | R | runs | rounds used | turns | prompt tok/run | output tok/run | $/run | $ per solved | tokens per solved | submitted |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3-4b | builder | 0 | 21 | 0.00 | 5.9 | 46.4k | 4.2k | $0.0000 | $0.0000 | 1063k | 10/21 |
| qwen3-4b | builder | 3 | 21 | 1.57 | 9.5 | 83.9k | 4.7k | $0.0000 | $0.0000 | 931k | 6/21 |

## 4. Per-task pass rate

| task | qwen3-4b<br>builder R=0 | qwen3-4b<br>builder R=3 |
|---|---|---|
| t0_and | · 0.00 | · 0.00 |
| t0_delay | · 0.00 | · 0.00 |
| t0_fanout | █ 1.00 | · 0.00 |
| t0_lamp | · 0.00 | · 0.00 |
| t0_nor | · 0.00 | · 0.00 |
| t0_not | · 0.00 | · 0.00 |
| t0_or | · 0.00 | · 0.00 |
| t0_pad_lamp | · 0.00 | · 0.00 |
| t0_piston | · 0.00 | · 0.00 |
| t0_wire_long | · 0.00 | █ 1.00 |
| t1_and3 | · 0.00 | · 0.00 |
| t1_clock | · 0.00 | · 0.00 |
| t1_edge_detector | · 0.00 | · 0.00 |
| t1_mux2 | · 0.00 | · 0.00 |
| t1_nand | · 0.00 | · 0.00 |
| t1_observer | · 0.00 | █ 1.00 |
| t1_pulse_extender | · 0.00 | · 0.00 |
| t1_repeater_lock | · 0.00 | · 0.00 |
| t1_rs_latch | · 0.00 | · 0.00 |
| t1_t_flipflop | · 0.00 | · 0.00 |
| t1_xor | · 0.00 | · 0.00 |
| **pass rate** | 0.05 | 0.10 |

Legend: █ >= 0.99, ▓ >= 0.67, ▒ >= 0.34, ░ > 0, · 0 (pass rate over seeds).

## 5. Failure taxonomy

| class (gate order R0>R1>R2>R3) | builder R=0 | builder R=3 |
|---|---|---|
| pass | 1 (5%) | 2 (10%) |
| R1: no_popoff | 4 (19%) | 1 (5%) |
| R2: input->output path missing | 15 (71%) | 17 (81%) |
| R3: behaviour wrong (output constant / inverted) | 1 (5%) | 1 (5%) |
| **runs** | 21 | 21 |

### Builder arm: compile attempts

102 build() attempts in 42 builder episodes: 75 failed (74%); 27 episodes had at least one failed attempt, 8 of them ended with a loaded build.

| error kind (attempts containing it) | attempts | share of failed |
|---|---|---|
| r0_check | 55 | 73% |
| api | 9 | 12% |
| runtime | 5 | 7% |
| syntax | 4 | 5% |
| duplicate_port | 1 | 1% |
| bad_value | 1 | 1% |

## 6. Caveats

- The scorer is the Nucleation simulator (inner loop), not the real game. Tasks are author-written and validated only against this
  simulator and (reference solutions) the vanilla 26.2 oracle (docs/ORACLE.md); a model may exploit a simulator quirk.
- Hidden vectors are of the same kind as the public ones. `run_spec` shows public vectors only, `submit` runs everything.
- R=0 means no simulator feedback; static validation (set_blocks rejections, builder compile errors, static `view`) is available in
  every condition. Port positions are fixed per task (the env has no port randomisation); `seed` changes the sampling seed sent to
  the provider and the order of public vectors in run_spec. Providers treat `seed` as best effort, so reruns are not bit-identical.
- Tasks are the clusters of the bootstrap; with few tasks the CIs are wide. A partial run (budget stop) covers the first tasks of the
  grid only (grid order seed > task > model > arm > R).

## Local generation metrics

| R | runs | pass | mean reward | turns/ep | completion tok/ep | max prompt tok | gen wall s/ep | tok/s | ctx overflow/OOM eps | tool calls/ep | replies w/o call | bad-JSON calls | ended_by |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 21 | 1/21 | 0.261 | 5.9 | 4169 | 11821 | 201 | 20.7 | 5 | 3.3 | 2.5 | 3 | no_tool_calls 11, submit 10 |
| 3 | 21 | 2/21 | 0.280 | 9.5 | 4733 | 12434 | 227 | 20.9 | 11 | 5.5 | 4.0 | 4 | no_tool_calls 15, submit 6 |
| all | 42 | 3/42 | 0.271 | 7.7 | 4451 | 12434 | 214 | 20.8 | 16 | 4.4 | 3.3 | 7 | no_tool_calls 26, submit 16 |

gen wall s/ep = sum of per-turn generation latencies (replayed turns keep their original latency); environment/tool time is not included. ctx overflow eps = episodes in which the prompt outgrew the model's max context on this GPU (the harness then nudges and ends the episode as no_tool_calls).
