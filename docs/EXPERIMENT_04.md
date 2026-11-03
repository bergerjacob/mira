# Experiment 04: free-form project-mode pilot (2026-11-03)

Round-3 audit step 1. Three fresh Sonnet-class agents, one request each, **no preauthored spec**: they write `spec.json`, build with the Part API / lib / `b.route`, then `test`, `trace`, `notes-check`, `export`, `paste-check` in project mode (`docs/PRODUCT.md`). Projects were left in `/tmp/freeform_{tff_door,counter,alu}/` (not kept in the repo).

| request | blocks (build / export) | revisions | wall | own-spec test | vanilla paste-check |
|---|---|---|---|---|---|
| flush 2x2 piston door toggled by a button | 61 / 478 | 1 | ~6 min | PASS 2 public + 2 hidden | PASS exact, stable at rest |
| 4-bit counter + reset button | 1,962 / 1,962 | 9 | ~15 min | PASS 7/7 sequences | PASS exact |
| 4-bit ALU (add/and/or/xor), carry lamp | 2,766 / 16,917 | 11 | ~21 min | PASS 1024/1024 rows (exhaustive) | PASS exact |

## Reading (read the caveats first)
- **All three are self-passes on specs the agent wrote.** We have NOT yet tested spec fidelity (the audit's P(hidden pass | self pass)): that needs a hidden spec authored by us, bound by port label, replayed in vanilla at a random origin and rotation. Do not call these "solved".
- `paste-check` verifies that blocks land and stay put, not behaviour. No behaviour replay in vanilla was run for these builds.
- Positives: project mode is usable end to end by an unprimed agent in 6-21 minutes; the ALU agent chose an exhaustive 1024-row spec itself after a 150-row spec passed (good sign for spec quality); the counter agent measured and documented its own timing limits (press spacing >= ~54 ticks; BTN/RST within 30 ticks misbehaves).
- Sequential spec weakness found by the agents themselves: unspecified cases (same-tick presses, rapid presses) are untested.

## Friction log (consolidated, the product backlog)
High (correctness traps):
1. **Silent dead gates:** `b.route` joins lib `and2`/`not_gate` bare dust pins from the side (note exists only on `Net.note`), the net is marked ok, R2 still says 100% and only behaviour fails (seen by two agents). Needs port metadata ("needs straight entry") respected or refused, a build warning naming the pins, and `strict` exposed on `Part.route`.
2. **Router `need` wrong for `mux2` and wrapped parts** (mux output follows input strength; wrapped parts get need=1): agents had to pass `need=` and add restorers by hand.
3. **Power-on state sensitivity** (counter: adding an unrelated delay line changed the boot state of a lib cell).

Medium:
4. Build `print()` / router notes / per-net strengths are invisible in project mode: want a `build` log section and a `nets` command.
5. Router has no rip-up inside `Part.route`; net order decides success; failure text does not name the blocking net; each failure costs 10-20 s. Want `route_all` with ordering + rip-up.
6. Hard 60 s build timeout with no override and an obscure message (`sandbox process failed (exit -24)`): want `build --timeout`.
7. No settled view: `query`/`view` show the raw program, not the at-rest world (pistons extended, door closed); want `view/query --settled` and a raw-vs-settled `diff`.
8. No behaviour replay in vanilla from project mode (`verify-vanilla`).
9. `spec.json` not validated until `test` (want `spec-check`: schema, vacuity, IO coverage, mutation score).

Low:
10. R4 shows 1.00 / reward 1.000 with no reference: should print `n/a`. 11. `notes-check` rejects ranges and flags intentionally empty spec cells. 12. `trace --ticks` ignored with `--seq`; no window. 13. Overview groups router wiring under `(root)`; side elevation collapses z; want `view --axis`. 14. `BuilderError` named in docs but undefined in the sandbox. 15. Contract authoring gaps (single `build_box`, pulse press/release semantics) are learned from `env/contract.py`, not docs.

## Decision
Fix items 1-2, 4-9 and 10-11 before the next free-form round; then run the hidden-spec fidelity test (author hidden specs for these three requests, replay in vanilla at random origin/rotation).
