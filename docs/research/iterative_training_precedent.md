# Iterative construction + verifiable-reward training: precedent brief (2026-11-01)

Scope: what 2025-2026 work says about (a) LLM agents building Minecraft structures with execution feedback, (b) simulator-verified RL for hardware (Verilog) as an analogue of redstone, (c) agentic/sparse-reward RL recipes, and (d) the Nekochu RedstoneDoor writeup. Sources were read as abstracts, project pages, or HF model cards unless marked otherwise. Anything not read beyond a snippet is marked **snippet** or **unverified**.

## 1. Minecraft building with execution feedback

| Work | What it is | Numbers / findings | Read depth |
|---|---|---|---|
| **MineCEraft** (arXiv 2608.28884, EMNLP 2026 Findings) | 723 expert-written construction instructions, 17 categories, programmatic checks (floating-block BFS, stress etc. per repo notes) | Abstract gives no headline numbers. Repo-status note quotes frontier ~27-40% problem-level. | Abstract only; the 27-40% figure is **unverified** |
| **Voyager** (arXiv 2305.16291) | Lifelong agent: automatic curriculum + skill library of executable code + iterative prompting with env feedback, execution errors, self-verification | 3.3x unique items, 2.3x distance, 15.3x faster tech-tree milestones vs prior SOTA (GPT-4 blackbox, no fine-tuning) | Abstract |
| **VoxProg** (arXiv 2610.30127, Oct 2026) | VLM writes voxel programs over local surface grids; visual + numeric execution feedback drives iterative revision; 71-task VoxProg-Bench | Claims consistent gains over relation/keypoint/mesh baselines; no per-task numbers in abstract | Abstract |
| **SceneCraft** (arXiv 2403.01248, 2024) | LLM agent writes Blender Python for up to ~100 assets; dual loop with library learning | Older than the window; relevant only for the "library learning + render feedback" pattern | Abstract (snippet) |
| **MineBench** (github Ammaar-Alam/minebench) | Raw-coordinate JSON builds, plus `voxel.exec` tool mode (block/box/line) for large builds; blind pairwise votes -> Elo | Ranking by human votes, not functional checks | Repo description (snippet) |
| **Minedit** | NeoForge mod; model writes builder JS in a sandbox, capped ops | No numbers read | **unverified** (status doc only) |

Takeaway: the build-as-program pattern (Voyager, VoxProg, Minedit, MineBench tool mode) is the consensus. What is **not** established is any functional (redstone-behaviour) success rate for these agents; MineCEraft-style checks are geometric/structural, not behavioural.

## 2. Verified-reward RL for hardware (closest analogue for redstone)

| Work | Verifier | Data | Result | Relevant lesson |
|---|---|---|---|---|
| **CodeV-R1 / QiMeng-CodeV-R1** (arXiv 2505.24183, NeurIPS 2025; v5 Feb 2026) | Rule-based testbench generator with equivalence checking vs golden reference | Round-trip synthesis: open Verilog snippets -> NL -> code, kept only if testbench-equivalent; ~87K examples after filtering | CodeV-R1-7B: **68.6% pass@1 VerilogEval v2, 72.9% RTLLM v1.1**; claims beats 671B DeepSeek-R1 on RTLLM | (a) Distill a cold start first, then RL. (b) **Difficulty filter**: drop prompts the distilled model solves in 5/5 attempts (too easy = zero signal). (c) Adaptive DAPO adjusts sampling rate to cut cost. |
| **VeriRL** (arXiv 2508.18462) | Testbench feedback | Veribench-53K curated from 700K+ problems | SOTA claimed; no headline numbers in abstract | Explicitly says sparse, noisy rewards are the core problem; uses a **trace-back rescore** and a sample-balanced weighting to avoid forgetting/overfit; co-evolves a reward model. |

Transferable points for redstone:
- The verifier must be trusted before it is used as reward. Hardware work invests heavily in the testbench; for us the equivalent is the Carpet oracle + Nucleation sim, and our own spike shows they agree on only 8/14 fixtures so far (`docs/SPIKE_NUCLEATION_2026-11.md`). **A reward signal with unknown parity is worse than none.**
- Difficulty filtering is the cheap fix for "all-correct or all-wrong" groups; it is how CodeV-R1 gets usable RL signal at scale.
- Their scale (~87K filtered examples, 7B) is orders of magnitude above our 242 cleaned corpus rows + 124 generators. RL from our current data is not a credible plan; SFT/rejection sampling on verified outputs is.

## 3. Agentic / sparse-reward RL recipes

| Work | Mechanism | Numbers | Relevant lesson |
|---|---|---|---|
| **SWE-smith** (arXiv 2504.21798, NeurIPS 2025 D&B) | Given any repo + environment, *synthesise* task instances that break existing tests (i.e. corrupt working code, keep the test suite as the oracle); trajectories from an agent | 50K instances, 128 repos; SWE-agent-LM-32B **40.2% Pass@1 on SWE-bench Verified** (open SOTA at release) | **Closest analogue to MIRA's corruptor + deconstruction**: generate broken variants of verified contraptions, use the existing verifier as the test suite, collect trajectories. Verified outcomes, not model judgement. |
| **R^3 reverse-curriculum RL** (arXiv 2402.05808, ICML 2024) | Outcome-only reward; start-state slides from the end of a correct demonstration back to its beginning | +4.1 avg over RL baseline on 8 reasoning tasks (Llama2-7B); +4.2 on GSM8K program reasoning | Turns sparse outcome reward into a step-wise curriculum from demonstrations. Maps to "start from a near-working circuit and let the model finish the last component". |
| **DISCOVER** (arXiv 2505.19850, NeurIPS 2025) | Selects exploratory sub-goals in the direction of the target from the agent's own value estimates; bounds time-to-target by initial distance, not task-space volume | Claims solves tasks beyond prior exploration methods (continuous control; no LLM numbers) | Goal-directed curricula beat uniform exploration when the space is combinatorial, which redstone is. Reverse-curriculum is the LLM-friendly version. |
| **GRPO advantage collapse** (e.g. arXiv 2605.21125, 2026; EDGE-GRPO 2507.21848) | When all group samples get identical reward, advantages are zero | Diagnosed as the dominant failure of sparse-reward GRPO | Filter zero-variance groups (DAPO-style dynamic sampling) and/or add shaped/partial reward so groups differ. |

## 4. Nekochu/Gemma-4-31B-RedstoneDoor (HF model card, read in full)

Facts reported by the author:
- Task: 2x2 then 3x3 sticky-piston door, verified live in Minecraft Java 1.21 by a Mineflayer bot (`verify_door.py`, `full_toggle_stable`). Output format: raw `/setblock` lines.
- Cost ~2 months, ~10 attempts, ~$300 (H200). Unsloth GRPO.
- **GRPO from the base model: 0 working doors in 1343 completions over 500 steps.** `reward_std = 0` after ~step 50 (no learning signal). Qwen3.6-27B random exploration also 0.
- Smaller model (Gemma 4 E4B, 125 steps): reward rose then plateaued at "lever placed, pistons don't move".
- Frontier models: Opus 4.6 (max thinking) fails; Opus 4.7 one design after ~15 min / 32k+ thinking tokens; GPT-5.5 and Kimi-2.6 fail 2x2.
- **Working model = overfit SFT on ONE verified door** (13 command examples + 203 Q&A; only the single door's body/wiring kept, since "too many designs = a noisy blur"). Reward was rebalanced from -1/+1 to -7.0/+30.8 (granularity).
- Anti-collapse: every GRPO step also re-shows the one good door so the policy stays anchored; without it "training death-spirals".
- Quantisation sensitivity: Q5_K_M 16/16 pass@16; Q4_K_M ~4/16. Overfit must be merged into weights to survive shrinking; adapters broke.
- 3x3: 0/96 sampled; model gets ~6/9 piston geometry but never wires the toggle (`changed=0`).
- Rejected: a Python redstone simulator, because it "wouldn't replicate quasi-connectivity, tick timing, and piston update order reliably". Also: teacher feedback loops (Opus reviews appended to failed prompts) confused the policy because the prompt changed each step. Converting WorldEdit schematics to `/setblock` silently broke ~25% of blocks (support blocks must be placed first).

Lessons, in order of importance for MIRA:
1. **Random-exploration GRPO from a base model does not find 3D redstone layouts.** Seed with verified SFT first; this is their explicit conclusion.
2. Reward density is the bottleneck; they hit the same zero-variance wall MIRA's design doc predicts.
3. Overfit-then-generalise is a hack for one design; it does not give a family of doors. Treat their result as "the policy can be made to output one circuit", not "the model can design circuits".
4. Their simulator rejection is the one point MIRA's Nucleation plan contradicts. Nucleation's parity is not yet shown (8/14 vs the live server, and fixtures are sloppy), so their objection stands until we measure it.
5. Anchoring (re-show a known-good example) and reward-scale tuning are cheap and should be copied.

## 5. What this implies for MIRA

1. **Use the verifier as a filter before it is a reward.** Build the rejection-sampling loop first: generate candidates, keep only those passing truth-table/timing checks on both Carpet and Nucleation, SFT on those. Their 2x2 result says this is required, not optional.
2. **Make the corruptor the curriculum (SWE-smith + R^3).** Take verified contraptions from the 242 cleaned rows and 124 generators, break them in controlled ways (remove/swap one component, shift a piston, wrong facing), and train repair; then move the broken region earlier/larger for harder tasks. The `deconstructor.py` pipeline already does reverse-order removal; reuse it rather than writing new generators.
3. **Shape reward, don't just gate it.** Score per-probe partial credit: correct output per input vector, correct toggle count, correct settle time, no unintended block changes, block-cost penalty. Nekochu's rebalancing and VeriRL's rescore both point this way. Keep the -1/+1 outcome as the terminal reward only.
4. **Difficulty-filter and drop zero-variance groups** (CodeV-R1 adaptive sampling, DAPO-style). Log the fraction of groups with zero variance from the first run; that number decides whether RL is even running.
5. **Anchor with a known-good example** in each prompt/batch (Nekochu anti-collapse), and keep the anchor as a fixed unit test so the model cannot "win" by being wrong in the same way.
6. **Verifier parity gates training.** Before any reward uses Nucleation, require agreement with the Carpet oracle on a corpus of settled, valid, fixed-state circuits (the spike's 8/14 was on sloppy fixtures; that is not a valid gate). Use Carpet fake-player stimulus (`/player ... use`), not `setblock`, for the oracle; the spike notes this as the weak link. Nekochu's objection about piston update order is exactly what this gate tests.
7. **Freeze the test authorship.** Both the Phase 0 `unfair_test` (25 runs) and the RedstoneDoor design make the model's own test a confound. Verification specs come from the harness/corpus, never from the generating model.
8. **Scale expectation.** Frontier-level 2x2 doors are rare; treat one-shot success on 3x3+ as ~0 for open models and measure the iterative loop's gain (pass@k vs. pass@1, number of repair rounds) rather than a single pass rate. Budget: Nekochu spent ~$300 for a failed-then-overfit result; a rejection-sampling round on a 30B open model is the cheaper first go/no-go (per STACK_PROPOSAL.md's order).
9. **Quantisation check.** If shipping a small model, evaluate after quantisation (Nekochu: 16/16 to ~4/16).

## 6. Residual gaps (not done)

- MineCEraft, MineBench, Minedit, VoxProg per-task numbers and failure taxonomies were not read beyond abstracts/snippets; no functional (behaviour-level) success rate for any LLM building agent was found.
- No published RL result for redstone or Minecraft circuits exists beyond Nekochu's blog-style report; Redstone Bench (per status doc) is placeholder data.
- VeriRL's headline numbers and CodeV-R1's ablations were not read in full; the figures above come from abstracts and the CodeV-R1 project page.
- SceneCraft, VoxProg and DISCOVER numbers are from abstracts; their applicability to discrete block worlds is an inference.
