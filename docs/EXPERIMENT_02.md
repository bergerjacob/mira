# Experiment 02: strong-agent baselines on the sandboxed protocol (2026-11-02)

Eight Sonnet-class subagents (one fresh context each, builder arm, `scripts/run_baseline_sandboxed.py` wrapper, no network, no repo copy, `mira` client only). Protocol and checks: `docs/VALIDITY.md`, `docs/MCP.md` ("Baseline protocol"). Sessions: `data/sandbox/*-1102-054924-*` (host-side `broker.jsonl`, `shell.log`, `session/`, `summary.json`).

| task | tier | extras | blocks | run_spec calls | builds | hidden | reward | wall |
|---|---|---|---|---|---|---|---|---|
| t1_clock | T1 | none | 14 | 1 | 1 | 1/1 | 1.000 | 16 min |
| t1_edge_detector | T1 | none | 18 | 1 | 1 | 1/1 | 0.967 | 17 min |
| t1_pulse_extender | T1 | none | 12 | 1 | 1 | 2/2 | 0.985 | 16 min |
| t3_counter4 | T3 | none | 141 | 2 | 3 | 60/60 | 0.989 | 17 min |
| t4_accum4 | T4 | library | 758 | 1 | 1 | 7/7 | 1.000 | 19 min |
| t4_adder16 | T4 | library | 1884 | 1 | 1 | 50/50 | 0.999 | 18 min |
| t4_addsub8 | T4 | library | 1485 | 4 | 4 | 52/52 | 0.988 | 24 min |
| t4_shift32 | T4 | library | 1004 | 2 | 2 | 6/6 | 0.975 | 18 min |

**Correction (round-3 audit, canonical placement; `docs/VALIDITY.md` s13): `t3_counter4` now FAILS in env (and in vanilla on the handover world): its raw program boots the counter at 14 when pasted. T0-T4 is therefore 7/8, not 8/8.** The rows below were scored under the legacy placement. All eight passed the hidden checks then, most on the first or second `run_spec`. These include the three tasks (t1_clock, t1_edge_detector, t1_pulse_extender) that neither cheap model ever solved in Experiment 01, and a 16-bit adder and a 32-stage shift register.

## Integrity checks
- Tool-call audit of all eight agent transcripts: only `bash` through the sandbox wrapper (one agent also sent a progress message); no reads of `env/tasks`, `lib`, `tests`, `data`, git or the harness. (`run_baseline_sandboxed.py audit` prints heuristic false positives because the wrapper path contains `data/sandbox`; the tool-call parse above is the real check.) The `lib` variant agents read only `/mira/lib` and `docs/LIBRARY.md`, as intended.
- Vanilla 26.2 replay of these sessions (`scripts/oracle_sessions.py --sessions 054924 --json data/oracle/sessions_r3.json`, one JVM per capture) was started and is slow for `t4_shift32` (about 40 min per capture). **Check `data/oracle/sessions_r3.json` before quoting any number from this page**; until that file shows vanilla = pass for a row, treat the row as env-verified only.

## What this means
- The current ladder (T0-T4 as authored) **saturates for a strong agent with the builder API and, for large tasks, the library.** The ceiling is not reached; the open question is where tools, not the model, become the limit. The audit's M3 caveat still applies in part: one model family, one run per cell, tasks authored by us, fixed port positions, and several T4 tasks are repetitive (16 identical slices).
- Effort is dominated by up-front layout planning (15-20 min of reasoning per task, 1-4 simulator calls), not by debugging. That is where a router and a module layer would help, and what a smaller model would struggle with.
- Next measurement should use harder, less repetitive and less library-shaped tasks (ALU, addressed RAM, a small CPU slice, a door/flush-door family, free-form requests with a model-proposed contract), randomised port positions/rotations, several seeds and a second model family, and report tokens and wall time per block. See `docs/audits/oracle_round2_2026-11.md`, section 2.
