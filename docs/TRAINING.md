# Training pipeline (workstream B2, 2026-11-02)

Goal: improve an **open** model on the MIRA build/verify loop with verified data and environment rewards. This page covers what
exists, what runs on the dev box (RTX 3060 12 GB) vs what needs HPC, the measured local numbers, and the recommended plan.
Nothing here changes `env/` or the harness loop: the local model plugs into `harness/agent.py` unchanged, SFT data is rendered
with the same template the local backend generates with, and every RL reward is the harness score.

```
harness/llm_local.py          LocalLLM: HF transformers backend with the harness client interface (native tool calling, $0)
training/local/               paths.py (system-python <-> .venv bridge), models.py (offline loading, tiny test model, context caps),
                              lora.py (hand-written LoRA, PEFT-compatible save/load), run_tests.py (pytest under /usr/bin/python3)
training/sft/                 dataset.py (episodes -> ids + assistant-only labels), train.py (LoRA SFT, resumable),
                              evaluate.py (calls / harness / report), build_fixture.py (transcripts + reference episodes -> SFT rows)
training/sft/hpc/             sft_trl.py + 8B/14B/32B configs, accelerate + DeepSpeed ZeRO-2/3 configs, slurm script, requirements
training/rl/                  reward.py (single-turn + multi-turn env reward adapter, RewardPool), grpo.py (minimal GRPO, local)
training/rl/hpc/              trl_grpo.py (single-turn GRPOTrainer sketch), verl multi-turn config + tool sketch
scripts/run_local_baseline.py open-model baseline through the real harness (+ report with local metrics)
scripts/run_experiment.py     additive `--backend local` (one process, sequential, $0, resumable in chunks)
tests/training_pipeline/      54 tests (52 CPU + 2 GPU-marked)
```

## Environment facts (dev box)

* Interpreters: torch 2.10 + transformers 5.2 live in the **system** Python's user site (`/usr/bin/python3`, 3.13); nucleation and the
  env deps in the repo `.venv` (3.13). `training/local/paths.setup_paths()` appends the *other* interpreter's site-packages at the end
  of `sys.path`, so `/usr/bin/python3` can import nucleation and the env in-process (verified: scores identical to `.venv`).
  The builder sandbox still spawns `sys.executable`, which works under both.
* Not installed and not downloadable here: peft, trl, accelerate, vllm, bitsandbytes, deepspeed, autoawq. Hence the hand-written LoRA,
  trainer and GRPO, and models are moved with `.to(device)` (no `device_map`).
* Cached models that load offline with transformers 5.2: `Qwen/Qwen3-4B-Instruct-2507` (8.0 GB bf16), `Qwen/Qwen3-1.7B` (3.4 GB),
  `meta-llama/Llama-3.2-1B-Instruct`, `google/gemma-3-1b-it` (its template has no tool support). Not usable: `Qwen/Qwen3.5-2B`
  (tokenizer only, no weights), `Qwen3-4B-AWQ` / `Qwen3-14B-AWQ` (need autoawq).
* Tool calls stay under ~9 minutes and background processes die with the call, so every GPU job is **resumable in chunks**:
  `--time-budget-s` stops cleanly, and rerunning the same command resumes from the checkpoint or replay cache.

## Local inference backend (`harness/llm_local.py`)

* Same interface as `OpenRouterLLM` / `ScriptedLLM` (`BaseLLM.chat` -> `Completion` with `message`, `finish_reason`, `usage`,
  `cost = 0`, `cost_source = "local"`); budget reservations are $0, so the shared `Budget` path still works. Usage counts are exact
  tokenizer counts.
* Prompt = the model's chat template with `tools=` (OpenAI history converted by `template_messages`: arguments as dicts). Tool calls are
  parsed from the text: Qwen/Hermes `<tool_call>{json}</tool_call>` (raw newlines inside JSON strings tolerated), Llama-3 bare JSON
  (`parameters`, `;`-separated, `<|python_tag|>`), OpenAI-shaped objects and fenced JSON. An unparseable `<tool_call>` is kept with its
  raw text as arguments, so the harness replies "arguments are not valid JSON", as it would for an API model. Qwen3 `<think>` is split
  off and counted as reasoning tokens. `enable_thinking` (or the harness `reasoning` flag) turns Qwen3 thinking on.
* Sampling: the harness temperature plus the model card's top_p/top_k from `generation_config` (Qwen3-4B-Instruct: 0.8/20); every
  call reseeds with the harness seed (`1000*seed + turn`); temperature 0 is greedy.
* **Own decode loop** (`sample_tokens`): chunked prefill (512 tokens) plus a KV-cache prefix reuse across turns. Measured problems
  with the stock path: (1) transformers 5.2's `generate(prefill_chunk_size=...)` silently corrupts output (greedy `t0_and` turned into
  repeated pseudo-documentation; my own chunked prefill reproduces unchunked greedy output exactly, which is tested), and (2) unchunked
  prefill of a 14k prompt OOMs on 12 GB. Prefix reuse: a multi-turn episode re-sends the whole history every turn; reusing the
  longest common token prefix cuts a short turn's latency from ~11 s to ~2 s. The Qwen3 template drops the empty `<think></think>`
  of earlier assistant turns, so reuse stops at the previous assistant message.
* Context caps that fit the 3060 (`training/local/models.MAX_CONTEXT`): Qwen3-4B 11.5k prompt+completion tokens (8.0 GB weights
  + 147 KB KV per token; 12k peaked at 10.1 GB; desktop processes hold 1-2 GB of the card). Qwen3-1.7B 32k. A prompt over the cap
  returns an empty `context_overflow` reply; a CUDA OOM returns an empty `oom` reply instead of crashing. The harness nudges, and
  after 3 such replies the episode ends `no_tool_calls`.
* Resumability: `cache_path` keeps every finished generation (keyed by prompt + sampling settings), and `deadline` stops before a
  generation that would be cut. The harness marks that episode incomplete (`api_error`), the STOP file ends the chunk, and the next run
  replays the finished turns instantly.

## Measured: free open-model baseline (Qwen3-4B-Instruct-2507, local, builder arm)

`/usr/bin/python3 scripts/run_local_baseline.py --model qwen3-4b` (chunks), report:
`--report docs/experiments/local_qwen3-4b.md`. T0 + T1 (21 tasks), builder arm, R in {0, 3}, 1 seed, temperature 0.7, 3072 tokens
per call, harness defaults otherwise (30 turns, 40k completion tokens per episode).

| | R=0 | R=3 |
|---|---|---|
| full pass (Wilson 95%) | **1/21 = 5%** [1%, 23%] (t0_fanout) | **2/21 = 10%** [3%, 29%] (t0_wire_long, t1_observer) |
| mean reward / mean R3 | 0.261 / 0.071 | 0.280 / 0.095 |
| turns, completion tokens per episode | 5.9, 4.2k | 9.5, 4.7k (1.6 run_spec used) |
| prompt tokens per episode (sum over turns) | 46k | 84k |
| generation wall time per episode | 201 s | 227 s |
| ended by | submit 10, no_tool_calls 11 | submit 6, no_tool_calls 15 |
| episodes hitting the GPU context cap (overflow/OOM) | 5 | 11 |

Throughput: 243 generations, 154k completion tokens in 7392 s = **20.8 tok/s including prefill** (prefix reuse on; median prompt
8.1k tokens, max 12.4k). 20 of 243 generations hit the 3072-token cap (runaway lists). Full report with the harness tables, per-task
heatmap and failure taxonomy: `docs/experiments/local_qwen3-4b.md`.

Failure taxonomy (from the transcripts):
* **The build never loads.** 28 of 42 episodes ended with an empty build, and 75 of 102 `build()` attempts failed. Of the failed
  attempts, 73% were compiled programs that the task rules rejected; most of the rest were API/runtime/syntax errors. Line by line,
  the rejections were "stone outside the build box" (133 lines: the model lays the floor at y=0 because the builder prompt's worked
  examples do) and writes to fixed cells (lamp 21, lever 10, piston 9). The harness taxonomy files these runs under "R2: input->output
  path missing" (71% / 81%) because what is scored is the empty build.
* **No error recovery.** The same rejected program is resubmitted with small changes. Context runs out after 6-10 such turns, and the
  episode ends `no_tool_calls` (26 of 42).
* **Few format failures.** 7 tool calls with invalid JSON (all cut by the length cap), 3.3 replies without a tool call per episode
  (mostly code blocks in prose or context-cap empties).
* **Where it does load**: `no_popoff`, i.e. wall torches without a mounting block (4 episodes), and wrong behaviour.

Reading: a 4B open model is far below the cheap API models of Experiment 01 (builder arm 47% / 62% at R=0 / R=3). The gap is mostly
the "fixed blocks and floor already exist" convention plus error recovery, which is exactly what SFT on verified episodes targets.
The 12 GB context cap also costs this model: 16 of 42 episodes hit it.
A cheap harness-side mitigation for the parent to decide on: treat builder writes of the floor material at the floor cells as no-ops
(currently only *identical* states are ignored, and the floor is `smooth_stone` while models write `stone`).

### Qwen3-1.7B: base vs SFT adapter in the harness (`training/sft/evaluate.py harness`)
5 T0 tasks (lamp, not, and, or, wire_long), R=0, reduced budget for time (6 turns, 2048 tokens per call), same settings for both:

| | full pass | mean reward | tool replies |
|---|---|---|---|
| base Qwen3-1.7B | 0/5 | 0.20 | 19 of 30 calls had invalid JSON (runaway generations cut by the length cap), 11 compile failures |
| + SFT smoke adapter | 0/5 | 0.20 | 1 invalid-JSON call; 27 compile failures (mostly `wire_path` writing outside the build box), 2 views |

The adapter fixed the protocol but not correctness, and it does not learn from errors: it resubmits the same failing program, although
the synthetic data has debug episodes. The 1.7B model is a pipeline test bed, not a candidate.

## Measured: SFT smoke run (Qwen3-1.7B + LoRA on the synthetic engine's episodes)

Data: a frozen snapshot of `data/synth/sft/*.jsonl` taken at 2026-11-02 (`data/training_runs/snap_synth_sft_1`, 924 verified
episodes: netlist one-shot / verify / debug / debug_view / typo, repair, extend, sequential; all builder-arm tool-calling transcripts
produced by the synthetic engine, no LLM text).

| token length (Qwen3 tokenizer) | value |
|---|---|
| episodes | 924 (min 6.0k, p50 9.1k, max 19.1k tokens; the system prompt alone is ~5k) |
| fit in 4k / 8k / 16k | 0 / 363 / 890 |
| assistant (trained) tokens | 2.49M of 9.12M (27%) |

Run: `training/sft/train.py --model qwen3-1.7b --data data/training_runs/snap_synth_sft_1 --out data/training_runs/sft_smoke_1.7b
--max-len 12288 --steps 200 --grad-accum 1 --lr 2e-4 --eval-frac 0.05` (LoRA r16/alpha32 on q/k/v/o/gate/up/down = 17.4M params,
bf16 base, fp32 LoRA, gradient checkpointing, assistant-token loss in checkpointed 1024-token LM-head chunks). 724 episodes fit 12k,
688 train / 36 eval. Five resumable chunks, 1467 s of training.

| steps | mean train loss | held-out eval loss (at end of block) | s/step | peak GPU |
|---|---|---|---|---|
| 0 | | 0.636 | | |
| 1-25 | 0.590 | 0.243 | 7.6 | 7.6 GB |
| 26-50 | 0.175 | 0.118 | 7.5 | 7.5 GB |
| 51-100 | 0.060 | 0.052 | 7.3 | 7.7 GB |
| 101-150 | 0.018 | 0.034 | 7.1 | 7.7 GB |
| 151-200 | 0.023 | 0.031 | 7.5 | 7.7 GB |

About 6.6k tokens per second of training throughput. A one-off probe trained at max length 18.6k with an 8.5 GB peak, so the 1.7B
model fits every synthetic episode. Caveats:
1. **The eval split was by `task`.** Extend and repair episodes share their base circuit with netlist episodes, so the eval loss is
   optimistic (near-duplicate programs). Fixed afterwards: `dataset.split` groups by `meta.base_task` (tested).
2. **The synthetic data is very templated** (the library prelude is copied verbatim), so a low loss means the format and templates
   were learned, not redstone.

**Does the tuned adapter still produce valid tool calls?** `training/sft/evaluate.py calls`: first assistant turn of 6 held-out
episodes, greedy:

| | parseable tool call, valid JSON, offered tool | build program compiles |
|---|---|---|
| base Qwen3-1.7B | 2/6 (3 ran into the 3072-token limit, 1 replied in prose) | 0/2 |
| + SFT adapter | **6/6** (all `build`) | 0/6 (`wire_path` overwrite or spacing errors, `Part.copy`, undefined names: real API mistakes on medium netlists) |

So 200 steps fixed the protocol (format, stopping, tool choice) but not correctness. Pass rate after SFT has not been measured
(harness mode below); expect little from a 1.7B model on this data.

## Measured: GRPO plumbing smoke test (Qwen3-1.7B + LoRA, single-turn, T0)

`training/rl/grpo.py --model qwen3-1.7b --tasks t0 --group 4 --steps 10 --out data/training_runs/grpo_smoke` (one 386 s chunk).
Per step: batched sampling of 4 completions (temperature 1.0, pure sampling, at most 1024 tokens; prompt ~4.4k tokens), parallel
scoring through the harness path (4 worker processes, 0.0-0.9 s), and one GRPO update (k3 KL to the LoRA-disabled base,
beta 0.04, token-mean).

| | result |
|---|---|
| rewards reach the policy | yes: statuses per sample (`no_program / compile_error / empty / rejected / ok`), staged rewards 0 / 0.02 |
| groups with reward variance (updated) | 6 of 10 (the other 4 were all-0 or all-0.02 and were skipped, as GRPO must) |
| loss / grad norm | finite at every update; grad norm 0.51-0.67 (clip 1.0) |
| KL | exactly 0 at the first update (LoRA B = 0), then 3e-4 to 1.4e-3 |
| time / memory | generation 13-47 s, update ~16 s, peak 6.9 GB |
| full passes | 0 of 40 samples; no sample produced a build that loads under the task rules |

The base 1.7B model writes plausible builder programs, but unfenced (fixed: `extract_program` accepts an unfenced program containing
`def build`). They place the floor or fixed devices themselves, so the task rules reject them. Without the 0.02 "compiles but
breaks the rules" stage, every group had zero variance: the Nekochu problem. RL needs SFT first and tasks in the 10-90% band.

## Reward adapter (`training/rl/reward.py`)

* **Single-turn**: prompt = builder system prompt (`evaluation/prompts/builder_system.md` + a single-turn protocol + the harness
  physics facts) and the public task text. The reply's last fenced (or unfenced) program is scored by **replaying `build(source);
  submit()` through `harness/agent.run_episode`** with a scripted player. The sandboxed compile, task rules, fixed-cell handling and
  hidden-vector scoring are therefore byte-identical to the benchmark (tested: the reference program scores 1.0, a stored broken
  variant scores partially). `RewardPool` scores in spawn worker processes with a hard timeout; a hung item gets 0 and the pool is
  rebuilt.
* **Shaping** (default weights, all components logged):
  * no program, a compile error or an empty build: 0;
  * compiles but breaks the task rules: 0.02;
  * loaded: 0.05 R0 + 0.05 R1 + 0.8 R3·[R1=1] + 0.1 R4·[R3=1].

  The env alone pays 0.2 for an *empty* build, which would reward not trying. R2 (connectivity) has weight 0 because it can be
  gamed (round-2 audit).
* **Multi-turn**: `rollout_episode(task, LocalLLM(trace=True), cfg)` runs a real harness episode. It returns a `Trajectory` with the
  final shaped reward, the harness final record, per-turn `(prompt_ids, completion_ids)` segments, and per-turn signals (build
  attempts with error kinds, R1-R3 after each `run_spec`, ended_by). `grpo.py --mode multi` gives every turn's tokens the trajectory
  advantage. It is tested offline with the tiny model; it has not been GPU smoke-tested (too slow for a 9-minute chunk).

## What runs where

| | dev box (3060, 12 GB) | HPC |
|---|---|---|
| inference / baselines | Qwen3-4B (11.5k context, ~21 tok/s incl. prefill, about 3.5 min per episode), Qwen3-1.7B | vLLM/SGLang behind an OpenAI-compatible endpoint, plus the existing `OpenRouterLLM` code path |
| SFT | LoRA on 1.7B at 12-18k tokens (7-8.5 GB); 4B LoRA is untested (8 GB of weights leaves ~2 GB, so expect at most 2-4k tokens: too short for these episodes) | `training/sft/hpc/sft_trl.py`: 8B LoRA (1 GPU), 14B LoRA (8xH100 ZeRO-3), 32B QLoRA (ZeRO-2) |
| RL | GRPO plumbing on 1.7B (G=4, 1k completion) | TRL GRPOTrainer single-turn with vLLM colocate (`trl_grpo.py`); verl multi-turn with tools (`verl_grpo_multiturn.yaml`) |
| env scoring | 4 worker processes, 0.03-1 s per sample | CPU workers next to the GPUs (scoring is CPU only) |

HPC packages (`training/sft/hpc/requirements-hpc.txt`, minimum versions from the APIs used; **not installed or run anywhere yet**):
torch >= 2.6, transformers >= 4.55, datasets >= 3, accelerate >= 1.6, peft >= 0.15, trl >= 0.19, deepspeed >= 0.16,
bitsandbytes >= 0.45 (QLoRA), flash-attn >= 2.7 (optional), verl >= 0.5 with sglang or vllm >= 0.9 (multi-turn RL), plus the repo's
`requirements.txt` (nucleation) on the scoring workers. The HPC SFT script pre-tokenises with `training/sft/dataset.py`, so local and
cluster runs train on identical tokens and labels. The verl tool sketch (`verl_mira_tool.py`) wraps the harness Episode, so tool
results equal the benchmark's (tested without verl).

## Recommended training plan

1. **Model**: a dense 8-14B tool-calling model for the first real run (Qwen3-8B, then Qwen3-14B; Gemma-4-31B if the budget allows,
   since it already solves 38% of T0/T1 one-shot in the builder arm, Experiment 01). Avoid MoE (tooling). The 1.7B and 4B results
   here measure the pipeline, not a viable agent: the 4B baseline is almost always rejected for writing the floor and fixed devices.
2. **Data mix for SFT** (all verified by the env; loss on assistant turns only):
   * synthetic engine episodes (ours): one-shot, verify, debug/repair, extend, sequential. Group the split by base circuit. Cap
     near-duplicates: the same library prelude in every program teaches copying;
   * scripted reference episodes for the env tasks (`build_fixture.py reference`): small, but they show the "floor and devices are
     already there" convention that the base models violate;
   * **rejection-sampled harness transcripts** (`build_fixture.py transcripts`): full-pass runs only, *of models we may train on*
     (see provenance), including the fail-then-fix segments;
   * hold out whole task families (e.g. all of `t1_*` timing tasks, one synthetic family) as the generalisation test, not just
     random tasks.
3. **Curriculum**: T0 then T1 then synthetic netlists by gate count then repair/extend then T3. Reverse-curriculum starts (repair of
   a mutated verified build) give the RL stage a non-zero pass rate.
4. **RL**: only after SFT, only on tasks with a 10-90% pass@8 for the current policy; single-turn GRPO first (cheap), then multi-turn
   with `run_spec` feedback (R=3). Keep R2 at weight 0. Spot-check RL passes with the vanilla oracle, since RL will find spec
   loopholes (piston-head door).
5. **Evaluation**: `training/sft/evaluate.py harness` on env T0/T1 (R=0 and R=3) plus held-out synthetic tasks, pass@1 over 2-3 seeds,
   compared with the base model in the same harness (`scripts/run_local_baseline.py`), and the oracle on passes.

## Data provenance (decision needed from the project owner)

* **Ours**: the synthetic engine's episodes (`data/synth/sft`: program text from our generators and library, tool results from our
  env), scripted reference episodes, transcripts of local open-weight models (their licences, e.g. Apache-2.0 for Qwen3, allow it;
  check the licence of any model you add), and the env/harness itself.
* **Possibly restricted**: transcripts produced by **commercial API models** (Experiment 01's deepseek-v4-flash and gemma-4-31b via
  OpenRouter, the Sonnet-class strong-agent sessions, any future Opus/Sonnet/GPT/Gemini runs). Provider terms may forbid using
  outputs to train other models, and OpenRouter adds its own terms on top of each provider's. **This is not decided here.**
  `build_fixture.py transcripts` skips such runs by default (80 full-pass transcripts are currently skipped). They are included only
  with `--allow-provider-models`, and every episode carries `meta.model` and `meta.source`, so a mix can be audited or filtered later.

## Commands

```bash
# tests (CPU, ~10 s; GPU ones need the cached Qwen3-1.7B)
/usr/bin/python3 training/local/run_tests.py tests/training_pipeline -q
/usr/bin/python3 training/local/run_tests.py tests/training_pipeline -q -m gpu
# open-model baseline (rerun until 0 pending), report
/usr/bin/python3 scripts/run_local_baseline.py --model qwen3-4b
/usr/bin/python3 scripts/run_local_baseline.py --model qwen3-4b --report docs/experiments/local_qwen3-4b.md
/usr/bin/python3 scripts/run_experiment.py --exp-id X --budget-usd 1 --backend local --models qwen3-4b --tasks t0 --arms builder --rounds 0,3 --seeds 1 --time-budget-s 450
# SFT (rerun to resume), evaluation
/usr/bin/python3 training/sft/train.py --model qwen3-1.7b --data data/synth/sft --out data/training_runs/sft_X --max-len 12288 --steps 200 --time-budget-s 480
/usr/bin/python3 training/sft/evaluate.py calls --run data/training_runs/sft_X --limit 6 [--base]
/usr/bin/python3 training/sft/evaluate.py harness --run data/training_runs/sft_X --tasks t0 --synth-heldout 8 --rounds 0 --exp-id eval_X
/usr/bin/python3 training/sft/evaluate.py report --exp-id eval_X
# data
/usr/bin/python3 training/sft/build_fixture.py transcripts data/experiments/local_qwen3-4b --out data/sft/transcripts.jsonl
/usr/bin/python3 training/sft/build_fixture.py reference --tasks t0,t1 --rounds 0,1 --out data/sft/reference.jsonl
# GRPO smoke (rerun to resume)
/usr/bin/python3 training/rl/grpo.py --model qwen3-1.7b --tasks t0 --group 4 --steps 10 --out data/training_runs/grpo_X --time-budget-s 480
```

## Open problems

* Local decode is ~15-21 tok/s for 4B in bf16 (Python per-token overhead). A compiled static-cache decode was slower here (6.6 tok/s
  after a 70 s compile). Batched rollouts or vLLM on HPC are the fix; on the 3060 an episode takes ~3.5 min.
* The local SFT eval loss of the smoke run is optimistic (split leakage, since fixed) and the data is templated; there is no
  pass-rate number after SFT yet (`evaluate.py harness` runs the experiment, but a full T0+T1 comparison costs about 2.5 h on this GPU).
* Qwen3-4B LoRA training on 12 GB is unmeasured and probably too short-context to be useful; 4-bit bases need bitsandbytes.
* Multi-turn GRPO is implemented and unit-tested, but has not been run on the GPU.
* `tests/harness` now exceeds 9 minutes because `test_reference_e2e` covers T3-T5 (other workstreams); the T0-T2 subset passes
  (137 tests in 61 s with the local-backend change).
