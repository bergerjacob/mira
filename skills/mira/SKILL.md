---
name: mira
description: Design, build, test and hand over Minecraft redstone contraptions of any size as a code project (Python Part API, simulator + vanilla oracle checks, exact-paste .litematic export). Use for requests like "build me a piston door / counter / ALU / farm trigger", fixing or extending an existing .litematic, or verifying that a contraption works.
---

# MIRA: a contraption is a code project

You edit files with your normal file tools and run the `mira` CLI (`<repo>/bin/mira`, or `.venv/bin/python scripts/mira.py`). Never place blocks one at a time by hand: write a program, build it, test it, look at it, fix it.

## Loop
1. `mira init NAME --dir PATH` (add `--from-litematic FILE` to edit an existing build, `--from-task ID` for a benchmark task). Work only inside the project.
2. **Write `spec.json` first** (contract v2; cheat sheet in `docs/PRODUCT.md` "Spec authoring"). It is the definition of "works". State assumptions at the top of `NOTES.md`.
3. Write `build.py` (+ `modules/*.py`) with the Part API: `from simulation.builder import *`; library cells `from lib.full_adder import full_adder` (`mira` lists them: see `docs/LIBRARY.md`); wire with `b.route_all([...])` instead of hand-placing dust. API reference: `docs/BUILDER.md`; prompt with worked examples: `evaluation/prompts/builder_system.md`.
4. `mira build` -> read errors/warnings (route WARNINGs matter) -> `mira overview` (under 80 lines at any size) -> drill down with `view --layer Y`, `view --settled`, `query X Y Z`, `find`, `nets`, `diff`.
5. `mira test` (rebuilds if needed) -> on failure `mira trace PORT... --seq NAME` shows the timing diagram. Fix, rebuild, repeat. Test modules alone with `mira test --module NAME`.
6. Before you trust a pass: `mira spec-check` (is the spec vacuous/weak? surviving mutants = untested behaviour) and `mira verify-vanilla` (does real Minecraft 26.2 agree?).
7. Hand over: `mira export --litematic OUT` writes the settled at-rest world, then `mira paste-check`. Hand over THIS file, never a raw block list (memory circuits boot differently when placed another way).
8. Keep `NOTES.md` true (named places as `port:X`, `label:X`, `module:X`, `(x, y, z)`); `mira notes-check` finds stale references.

## Spec basics people trip over
- Units are game ticks: 20 ticks = 1 s; a repeater step = 2 ticks; a redstone torch inverts after 2 ticks; **a redstone lamp turns off 4 ticks after losing power** (turns on immediately), so a lamp-port timing window must allow it (a "1 s on / 1 s off" blinker reads ~24 on / 16 off).
- `budgets.total` is the cumulative tick budget across ALL sequences/vectors; long sequences need it raised (e.g. 20000), otherwise `tick budget of N exceeded`.
- Sequences with `"public": false` form the hidden set (`test` reports public vs other); omit it and everything counts as public. Write both: a few public examples, more hidden ones.
- `verify-vanilla` replays spec sequences/vectors (sampled for big tables; `--all` for everything).
- Enable-style clock recipe: a lever powers its mount block; a torch is inverted by its mount; a torch powers the block above it; hold a ring oscillator in reset with a second torch on the lever's mount block.

## Rules that save hours
- Your own spec passing is not enough. Independent testing showed a counter that passed its own spec but broke when buttons were pressed faster than its spec assumed. Spec the timing a real user produces (fast presses, simultaneous inputs, idle periods), or write the assumption into NOTES.md as a stated limit.
- Unsure what the user wants? State your assumption in NOTES.md and proceed; list it in your final message.
- The simulator is wrong in a few known places (comparator with a lever side input, dropper-into-dropper, torch burnout clocks): use `verify-vanilla` for anything involving them.
- Repeater `facing` points at the INPUT side (builder `flow=` takes signal direction). Place wire bare. Library gates `and2`/`not_gate` must be entered from their face (the router enforces it).
- Large builds: decompose into modules with their own specs, test each, then compose; use `overview` instead of dumping blocks.
- Report honestly: say what was verified (env, vanilla replay, paste-check) and what was not.
