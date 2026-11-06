# Tick debugging: reuse the existing controls

Decision: **add no dependency for rewind or tick control**. The current referee uses vanilla 26.2's GameTest tick loop. Carpet/native controls remain useful for the separate interactive server path, not required for this oracle. This note does not choose a new target version.

## What the agent can rely on
- A declared initial build + tick-indexed `Plan` + absolute origin + pinned oracle version identifies a replay. Operations at tick T occur after T-1 and before T (`tools/oracle/trace.py`).
- A fresh isolated JVM capture is the confirmation path. Batch runs are a speed optimization with reset/contamination tests, not an excuse to rely on an unspecified previous world.
- `env/sim.py` already wraps Nucleation checkpoint/restore for feedback trials. Those are simulator checkpoints, not vanilla world saves or proof of equivalent hidden state.
- Looking earlier in a trace is inspection, not game-state reversal. To try a different action, rebuild/reset the initial world and replay the prefix. Preserve the failing baseline before minimizing it.

## Why not another mod
Java's native `/tick` freeze/step exists since 1.20.3; interactive `/tick step 1` advances the world and cannot undo it. Exact 26.2 command behaviour was not independently exercised in this dependency audit. Carpet supplies fake-player/Scarpet automation, not general rollback. Tick-rate mods change scheduling speed, not established game-state rewind. Subtick-debug extensions may help manual diagnosis but their current version compatibility was not verified. WorldEdit snapshots restore selected world regions from backups; their documentation does not establish recovery of every pending tick/event/entity datum.

Sources checked by the research agent (availability/limits, not installed here):
- https://minecraft.wiki/w/Commands/tick (direct fetch403; availability via search result)
- https://github.com/gnembon/fabric-carpet
- https://github.com/lntricate1/SubTick (compatibility unverified)
- https://worldedit.enginehub.org/en/latest/usage/snapshots/
- https://github.com/Schem-at/Nucleation/blob/master/docs/features/tick-simulation.md

## Reliability gates
1. Same file/build, plan, oracle and absolute origin: fresh captures must have identical per-tick block-state traces.
2. Include delayed scheduled work. A block snapshot alone is not a complete intermediate-state checkpoint. Imported scheduled ticks are currently refused, not silently stripped.
3. Compare batch captures against fresh isolated captures. Follow any discrepancy as a harness bug before calling it a game mechanic.
4. Test alternate origins separately: update order can depend on position, so a different-origin trace need not be identical.
5. Don't claim entity/inventory replay reliability from block-state-only traces. Current certified scope excludes those payloads; more complete tracing would be separate work.

These gates are the protocol, not a claim that arbitrary Minecraft behaviour has been exhaustively tested. Existing measured examples and batch checks are in ORACLE.md, VALIDITY.md and the import checkpoint report.
