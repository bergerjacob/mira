# Oracle audit round 2 (2026-11-02)

I found four problems that change the conclusions, and none of them is about the simulator. Two of the five strong-agent passes can't be taken at face value: the door passes through a hole in the spec, and the 4-bit-adder agent read a repo file it was told not to. The vanilla oracle also gives wrong verdicts when certain jobs share a JVM. And the "builder beats blocks by 27 points" result is partly the builder prompt's worked examples.

## 1. What is most likely wrong, confounded or overclaimed

**H1 (high): the strong-agent door pass exploits the spec.** The build (`data/sessions/t2_door_2x2-*/sources/001.py`) pushes no door blocks. Its four sticky pistons extend their heads into the door cells.
- The output port is `region_mode: "solid"`, which means "not air", so `piston_head` and `moving_piston` count as a closed door (`env/tasks/t2_door_2x2.json`, `env/tasks/author.py:489-506`).
- The reference pushes stone. The forbidden list only stops the agent placing heads, not the simulator creating them.
- I ran it through the vanilla oracle: it passes both sequences, tick-identical to Nucleation. So the oracle cannot catch this; it checks the simulator, not what the spec means.
- The comparison in `EXPERIMENT_01.md` with Nekochu and Opus 4.7 should be withdrawn until the door port requires a pushed, non-piston block.
- The hidden check is only `cycle_twice` (1/1).

**H2 (high): an agent read the answer files, and nothing prevents it.**
- `t3_adder4-*/sources/002.py` contains `lib/full_adder.py` word for word, including the module docstring. No tool returns that text: `search_components` gives only metadata and build errors don't echo the prelude. So the agent read `lib/` or `data/sessions/*/sources/001.py`, both forbidden by the protocol (`docs/MCP.md`, "Limitations").
- The raw `t3_adder2` build is an original torch-XOR design, unlike both the reference and the library, so it is probably clean. That can't be proven, because host transcripts and token counts weren't saved.
- Until runs are sandboxed with reads denied on `env/tasks`, `lib`, `docs` and `data`, every strong-agent number depends on the agent obeying the prompt.

**M1 (medium-high): the oracle gives order-dependent wrong verdicts when jobs are batched.**
- Repro: run `capture_batch([t2_door_2x2 reference, t3_adder4 reference])`. The adder4 reference then fails 49 checks in vanilla, diverging at tick 1 at (65,1,8).
- The same reference run alone, or after an adder2 job, is tick-identical.
- This is a deterministic cause for the "sporadic batched failures" in `docs/LIBRARY.md` (Needs #4). The current guard, re-running non-clean verdicts, cannot catch a false pass.
- I checked the T3 report's transient divergences for `t3_adder8` and `t3_decoder3to8`: they reproduce in isolation, so they are real.
- Answer to your question: none of the five strong-agent builds passes because of a Nucleation bug. All five pass in vanilla when run alone or in an uncontaminated batch.

**M2 (medium): "authoring format dominates" is confounded.**
- `evaluation/prompts/builder_system.md:66-128` gives the builder arm worked examples: lamp, NOT, and AND built from inverters, whose `wire_path` step is an OR. The blocks prompt (`harness/prompts.py:42-62`) has none.
- deepseek's builder-only wins at R=0 are exactly NOT, OR, fanout and AND3.
- gemma's 0% in the blocks arm is a tool-calling breakdown: 34 turns, 291k prompt tokens, about 6k output tokens. That says nothing about representation.
- `max_turns = 10 + 3R` (`harness/agent.py:59`), so R mixes feedback with turn budget. That may explain R=8 < R=3.
- The direction of the effect is plausible, but "+27 points" is not established.

**M3 (medium): "T0-T3 do not discriminate strong agents" is overclaimed.**
- There is one run per cell, one model (Sonnet), and four distinct tasks, all combinational apart from the door. Only adder2 was tried without the library.
- The tasks cheap models never solved are all timing or state tasks (clock, edge detector, pulse extender) plus mux2. No strong agent was run on those, on the T3 counters, on adder8, or on decoder3to8 without the library.
- Difficulty may track the kind of mechanism rather than size.

**L (low):**
- T3 spec strength is weaker than T0-T2: 16-20 of 24 mutants killed, against 94.7% for T0-T2.
- For tasks with 3 or fewer inputs, the hidden checks are the public table in reverse order (`author.py:116`). They test order-robustness, not generalisation.
- Port positions are fixed per task. That matters for training and memorisation.
- The raw-tier T3 task text includes the "Library hint" sentence.

**Where I disagree with round one:**
- The loop audit said to start with block operations and add the builder only at T3. The data points the other way, though by less than claimed.
- That audit also called for random port placement and oracle-confirming a subset. Neither is done for agent submissions, and both now matter.

## 2. The three highest-value next steps

**A. Make the benchmark trustworthy, then map the frontier ceiling (1-2 days of work; no OpenRouter spend).**
- Fix the door port to a pushed-block check. Add 3x3 and flush doors.
- Have a strong agent red-team each spec: "find the cheapest build that passes but a player would reject." Treat each loophole it finds as a bug.
- Run agents in a sandbox (container or bubblewrap) containing only the CLI and task text. Save host transcripts and tokens.
- Oracle-gate every reported pass with one JVM per verdict, about 3-20 s per build, until M1 is root-caused.
- Then measure S50 with procedurally sized families: adderN for N = 2-32, counterN, decoders, registers and RAM, shift registers, ALU slices, doors from 2x2 to 4x4. Include timing and state families explicitly.
- Use 3 seeds with randomised and rotated ports, Sonnet 5.5 and Opus 5.5, and tiers raw / library / library+router / compiler. That is about 150-250 episodes.

**B. Router plus module layer (3-5 days).** Routing and layout are the measured bottleneck: the raw adder2 took 954 s, mostly layout; four of the T3 bugs were routing or leakage; and `wire_path` ignores obstacles.
- Build a 3D A* router that avoids obstacles and dust leakage, inserts repeaters, and uses crossover and vertical primitives, checking each net in the simulator.
- Add the round-one module registry: a spec per module, `verify_module`, instantiate/connect, and fault localisation.
- Lay the workspace out like a repo for Claude Code: module files, `NOTES.md`, `mira test <module>`.
- Experiment: adder16, decoder4to16, counter8 and 4x4 RAM, comparing library vs library+router vs library+router+modules. Measure pass rate, wall time, tokens per block, and size relative to the compiler.

**C. A data engine instead of more cheap-model grids.**
- Repair tasks from `env/mutate.py` mutants of verified builds.
- Extend tasks (4-bit to 8-bit).
- Random netlists of verified library cells, placed and routed, with the netlist truth table as the spec. That gives unlimited labelled tasks.
- Before more cheap-model runs: rerun the grid with the worked examples equalised across arms and the turn budget fixed. That needs about $10-15 of top-up.

## 3. Training path
- Don't train before the ceiling map. Frontier agents with tools already clear T3, so the open question is whether tools or weights limit "any size". Training mainly buys you a cheap or local agent.
- Best use of verified trajectories:
  1. A regression set for harness changes.
  2. Rejection-sampled SFT on multi-turn builder transcripts that use the tools, including the fail-then-fix segments, on generated tasks with randomised ports. You need about 2-5k passes over 200 or more distinct tasks. You have about 100 cheap-model passes and 5 strong sessions. At about $0.007 per solve, collecting them is roughly $50.
  3. Repair and extend starts as a reverse curriculum.
- Model: a dense 14-32B model that already calls tools well (for example Gemma-4-31B, which is already in the grid), with LoRA SFT. Then GRPO only on tasks with a 10-90% pass rate.
- The sparse-reward problem in Nekochu came from one-shot, single-task, binary-reward training with no tools. Here, per-vector R3, per-module specs, curricula built from mutants, and in-environment feedback all give signal.
- Fix spec loopholes before RL; RL will find holes like the piston-head door. Down-weight R2, which can be gamed: a connection with no correct behaviour still earns it. Spot-check RL passes with the oracle.

## 4. Data and Discord
Don't scrape more for now.
- The Discord material is mostly farms that depend on entities: 1285 unique raw files, of which 155 load in the simulator.
- It is internal-only by licence, and the runbook uses a user token, which breaks Discord's ToS.
- Most importantly, it has no machine-checkable specs, and specs are the binding constraint. The licence-clean claudestone and redstone-verify sets (453 + 130, with specs) plus option C give more usable data than scraping would.
- If you do scrape: only with a staff-approved bot account, only TMC Catalogue's non-farm and tech categories, and only the first post's text plus attachments plus tags of forum threads. Storage Catalog should come from its GitHub archive.

## 5. Not measured, but would change decisions
- Vanilla replay of agent submissions. M1 shows this is needed, and it is cheap.
- Robustness to rotation and world position: vanilla update order depends on absolute position. Test 4 rotations × 3 offsets in the oracle.
- Latency: R4 has no latency term, and settle-then-read specs accept slow circuits.
- Inputs changing one at a time vs several in the same tick.
- Pass@k and variance; every strong-agent result is a single run.
- Tokens and wall time per solved block for strong agents.
- The spec-loophole rate.
- Whether builds are buildable in survival (piston-head doors, spammed redstone blocks).

## Suggested execution prompt (if approved)
"Fix benchmark validity:
1. In `t2_door_2x2`, make door cells require a non-piston full block (not `piston_head` or `moving_piston`). Re-validate, and add a regression test using the session build in `data/sessions/t2_door_2x2-*/final_blocks.json`.
2. Add a `--isolated` mode, one JVM per job, to `tools/oracle/gametest.py`. Add a test that `capture_batch([door_ref, adder4_ref])` gives the same verdict as running adder4 alone, and root-cause the contamination.
3. Write `scripts/oracle_sessions.py` to oracle-gate every `data/sessions/*` pass.
4. Add a sandboxed runner for strong-agent baselines with reads denied on `env/tasks`, `lib`, `docs` and `data`, saving host transcripts.
5. Equalise the worked examples across the blocks and builder arms."
