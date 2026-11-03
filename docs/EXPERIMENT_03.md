# Experiment 03: strong-agent baselines on T5 (2026-11-02, legacy placement)

Same sandboxed protocol as Experiment 02 (`docs/VALIDITY.md`): Sonnet-class subagent, builder arm, bwrap + host broker, 20 `run-spec` calls max, `raw` (primitives only) vs `lib` (component library). Scored by the env **before** the canonical-placement change (`docs/VALIDITY.md` section 10 onwards); the numbers may shift slightly after it. Sessions: `data/sandbox/t5_*-1102-1620*`.

| task | variant | blocks | run_spec | hidden | reward | result |
|---|---|---|---|---|---|---|
| t5_lfsr4 | raw | 216 | 1 | 4/4 | 0.996 | full pass |
| t5_lfsr4 | lib | - | 1 | 4/4 | 0.983 | full pass |
| t5_lock4 | raw | - | - | - | - | **no result**: the agent crashed (single response exceeded the 128k output-token cap); retry pending |
| t5_lock4 | lib | - | 12 | 6/6 | 0.993 | full pass |
| t5_mul2x2 | raw | - | 4 | 34/34 | 0.987 | full pass |
| t5_mul2x2 | lib | - | 1 | 34/34 | 1.000 | full pass |
| t5_seq_detector | raw | - | 10 | 6/6 | 0.991 | full pass |
| t5_seq_detector | lib | 177 | 8 | **3/6** | 0.880 | **public 2/2 but hidden fails** |

## Reading
- 6 of 7 completed attempts pass. The single failure is the first **public-pass / hidden-fail** result we have seen from a strong agent: the library variant of `t5_seq_detector` built a shift-register detector with a long OR bus and a skewed clock distribution; it matched the two public sequences and failed on the held-out ones. This is the property the hidden-vector design exists to catch, and it shows hidden vectors are doing real work.
- Stateful tasks need more feedback rounds than T0-T4 combinational ones: lock4 took 12 `run-spec` calls and seq_detector 8-10 (vs 1-4 earlier). The `lib` variant did not help on the sequential tasks (it helped on mul2x2, hurt on seq_detector): the library has no sequential-detector cell, and the d_flipflop clock-skew trap cost the agent.
- Reported tool friction: `probe` is disabled in the sandbox CLI, so diagnosis costs `run-spec` calls; a failed `mira build` falls through to a `run-spec` of the previous build when commands are chained; the `paste_stable` failure message for dust next to a lever does not explain the cause.
- Caveats: 1 run per cell, 1 model family, 4 tasks authored by us. Do not read 6/7 as a pass rate for T5 in general.
