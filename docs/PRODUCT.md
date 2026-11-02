# Project mode: a contraption as a code project (2026-11-02, Phase A1)

Project mode is the product workflow. An agent (Claude Code, pi, any MCP client, later our own model) works on a redstone
contraption the way it would work on a code project. The project has a program split into modules, a spec, notes, views,
tests and a revision history. At the end the agent hands over a `.litematic` with a receipt showing that it pastes exactly
in vanilla Minecraft 26.2.

```
mira_mcp/project.py     Project: init, import assembly, revisions, every command (returns compact text)
simulation/views.py     pure renderers: overview maps, slices, diffs, timing diagrams (no simulator, no project state)
simulation/export.py    settle -> .litematic (Nucleation), vanilla structure .nbt (own writer), path check, paste_check (oracle)
scripts/mira.py         CLI (same commands)
mira_mcp/server.py      + project_* MCP tools (additive; the session tools are unchanged)
tests/project/          35 offline tests (~20-30 s; the vanilla paste test runs when the oracle is installed, else it is skipped)
```

```bash
alias mira='.venv/bin/python scripts/mira.py'
.venv/bin/python -m pytest tests/project -q
```

## Workflow for an agent

1. **init**: `mira init NAME [--from-task ID] [--from-litematic FILE] [--program FILE] [--dir PATH]`. This creates
   `data/projects/NAME/` (any path works with `--dir`). The project holds `build.py`, `modules/`, `NOTES.md`, `project.json` and
   `.mira/`; `--from-task` also writes `spec.json`.
2. **Write the spec** (`spec.json`, contract v2, `docs/ENV.md`) from the user's request, or copy it from a task. `test` validates it with
   `env.contract` and reports problems as text.
3. **Modules**: put cells and helpers in `modules/NAME.py`. Import them into `build.py` with `from modules.NAME import x`, and import library parts
   with `from lib.full_adder import full_adder` (the project's `lib/` is searched first, then the repo's `lib/`). `mira init-module NAME` writes
   a template and, once the part compiles, a module spec skeleton.
4. **build**: compiles in the sandbox and stores revision N. Errors are reported as `file:line`. A successful build reports blocks, bbox,
   palette, blocks per source file, blocks per instance group, ports, the change against the previous revision, and whether the build obeys
   the spec's rules (build box, fixed cells).
5. **overview**: always under 80 lines at any size (measured up to 101k blocks, below). It shows the module tree with bboxes, ports, a
   top-down class map beside a module-letter map, a side elevation, and the commands to run next. Then drill down:
   - `view --layer Y [--box x0 z0 x1 z1]`
   - `find PATTERN`
   - `query X Y Z [-r R]`, which also shows the source `file:line` that wrote each block
6. **test** (`[--module NAME] [--public-only] [--fast]`): the env runner and score over `spec.json` or a module spec. It rebuilds first if the sources
   changed.
7. **trace / fix**: `trace PORT... [--vector K | --seq NAME | --set "A=1;A=0" | --ticks N]` draws an ASCII timing diagram of spec ports,
   build ports or `x,y,z` cells under the test's stimulus. Fix the code, rebuild, then use `diff` (block-level, grouped by module and region)
   and `log`.
8. **notes**: keep `NOTES.md` true. `notes-check` reports references that no longer exist. Write references as `port:NAME`, `label:NAME`,
   `module:NAME`, `inst:PATH`, `part:NAME` or `(x, y, z)`; backticked names that existed in an earlier revision are checked too.
9. **export**: `export [--litematic OUT] [--nbt OUT] [--no-fixed] [--force]` writes the settled at-rest world (build + spec scenery),
   reads the file back, and reports whether the build is placement-path sensitive.
10. **paste-check**: pastes the export into vanilla 26.2 (the oracle) twice, once quietly and once with the full update pass. Each paste idles
    `--ticks` (default 40), reads every block back and diffs it against the file. The verdict and receipt go to `.mira/paste_receipt.json`.

Every command prints compact text, clipped at `MIRA_MAX_CHARS` (12000) with an explicit notice. The CLI exit code is 1 when the first line
says `error:`, `FAILED`, `FAIL` or `REFUSED`. Which project is used: `-p NAME|PATH`, else `$MIRA_PROJECT`, else the project that contains
the current directory. The MCP tools are `project_init, project_build, project_overview, project_view, project_find, project_query,
project_diff, project_test, project_trace, project_notes_check, project_export, project_paste_check, project_init_module, project_log`.
Each takes `project` (name or path) and returns the same text as the CLI. The agent edits files with its own file tools.

## Project layout and the import mechanism

```
project.json   {name, created, task, origin, lenient}
build.py       def build(b): ...                       (Part API, docs/BUILDER.md)
modules/*.py   project modules; modules/NAME.spec.json = module spec
lib/*.py       optional project-local library parts (override repo lib/)
spec.json      contract v2 of the whole build
NOTES.md       agent-maintained facts
.mira/revs/NNNN/{meta.json, build.json.gz, src/...}   every successful build: blocks+provenance+ports+labels+instances+source map,
                                                    a snapshot of all sources (repo lib parts used are copied to src/_repo/)
.mira/{history.jsonl, tests.jsonl, last_test.json, last_build.json, exports/, paste_receipt.json, traces/last.txt}
```

`simulation/builder.py`'s sandbox allows no imports besides the builder (and I may not edit it), so imports are **resolved by
pre-processing** (`project.Assembly`):

- Parse each file. For each `from modules.X import ...` or `from lib.X import ...`, visit X first (dependencies first; cycles are
  errors). Repo lib parts also pull in their `requires` from `lib/*.meta.json`.
- Replace the import statement with `pass` on the same line. Then concatenate the files behind one `from simulation.builder import *`
  and compile the result with the usual sandboxed `builder.compile`. The sandbox is still the security boundary: `import os` in a module is
  rejected (tested).
- A source map translates the program's line numbers back to `file:line`. This works for compile errors, warnings and **block
  provenance**, which is how `build` counts blocks per file and `query` names the line that wrote a block.
- Files are read only from the project directory and the repo `lib/`. Module paths must be identifiers (`modules.a.b` resolves to
  `modules/a/b.py`).
- All files share one namespace. So it is an error when a top-level name is defined in two files, when a module defines `build`, when an
  imported name does not exist, and when a file uses `import modules.x` or `as`.

**Module specs** (`modules/NAME.spec.json`):

```json
{"part": "buf", "params": {}, "at": [0, 0, 0], "rot": 0, "mirror": null, "contract": { ...contract v2 in module-local coordinates... }}
```

`test --module NAME` compiles `modules/NAME.py` (with its imports) plus a generated `build(b): b.place(part, at, rot, mirror, **params)`
and runs the contract. `init-module NAME` derives the ports from the compiled part:
- an `in` port becomes a `pad` one cell outside the port along its face, with a fixed air cell reserving it;
- an `out` port becomes a `wire_power` output;
- ports of parts nested inside the module are skipped.

It leaves one truth-table row to fill in. Measured (`tests/project/test_test_trace_export.py::test_module_spec_skeleton_and_module_test`):
- two `not_gate`s chained as a buffer pass their module spec;
- an inverted truth table fails it;
- `trace A Y --module buf --set "A=1;A=0"` shows `Y: 0@0 1@4 0@10`, the expected 4-tick delay.

## Worked example (run end to end, 2026-11-02): the strong-agent `t4_addsub8` session as a project

Input: `data/sessions/t4_addsub8-builder-R25-1102-054924-bf70/sources/004.py`, the last program of the Sonnet-class sandboxed session
(`docs/EXPERIMENT_02.md`). It contains the concatenated library prelude (`full_adder`, `xor2`) and the agent's program. I split it the way
an agent in project mode would:
- `modules/slice.py` holds the helpers `D`, `S` and `slice_` and imports `from lib.full_adder import full_adder` and
  `from lib.xor2 import xor2`;
- `build.py` holds `from modules.slice import slice_, D, S` and `build()`.

Unchanged lines are kept verbatim. Outputs below are copied from the runs; nothing was edited apart from removing the absolute path prefix
in two places.

```text
$ mira init addsub8 --from-task t4_addsub8
initialised project 'addsub8' at .../data/projects/addsub8
spec.json <- task t4_addsub8 (17 inputs, 9 outputs, build box [0, 1, 0, 141, 5, 18]); fixed blocks are part of the world, not of build.py
build.py <- template
NOTES.md <- template
next: edit build.py / modules/*.py, then `build`, `overview`, `test`

$ mira -p addsub8 build -m "session program split into modules/slice.py + lib parts"      # 0.14 s
BUILD OK rev 1 hash 9eaf249bfaf1  1485 blocks  bbox [9, 1, 0]..[140, 3, 17] (132x3x18)  0.1s
palette: redstone_wire 791, stone 500, repeater 130, comparator 64  [4 kinds]
blocks by source file: modules/slice.py 634, lib/full_adder.py 568, lib/xor2.py 144, build.py 139
blocks by instance group: (root) 773, fa# x8 568, xr# x8 144
ports (64): fa{0..7}.A, fa{0..7}.B, fa{0..7}.CIN, fa{0..7}.COUT, fa{0..7}.S, xr{0..7}.A, xr{0..7}.B, xr{0..7}.Y
task rules: ok (1485 blocks inside build box [0, 1, 0, 141, 5, 18])
```

The hash `9eaf249bfaf1` is identical to the unsplit session program built as one file (`init flat --program 004.py`; checked), and the 1485
block positions equal the session's `final_blocks.json`.

```text
$ mira -p addsub8 overview                    # 31 lines, 2861 chars (~795 tokens), 0.09 s
PROJECT addsub8  rev 1/1  hash 9eaf249bfaf1  1485 blocks  bbox [9, 1, 0]..[140, 3, 17] = 132x3x18
spec: t4_addsub8 (17 in / 9 out), fixed scenery 2724 blocks; not tested yet
palette: redstone_wire 791, stone 500, repeater 130, comparator 64  [4 kinds]
MODULES (16 instances in 2 groups; root blocks by file)
  fa#            full_adder               x8   71 each      [10, 1, 0]..[130, 3, 6]
  xr#            xor2                     x8   18 each      [15, 1, 9]..[131, 2, 12]
  (root)  modules/slice.py 634, build.py 139
spec inputs (17): A{0..7} click x8 @ x 12..124 y 1 z 15; B{0..7} click x8 @ x 16..128 y 1 z 15; M click [140, 1, 17]
spec outputs (9): S{0..7} lamp x8 @ x 25..137 y 1 z 15; COUT lamp [9, 1, 4]
build ports (64): fa{0..7}.A in x8 @ x 12..124 y 2 z 4; fa{0..7}.B in x8 @ x 16..128 y 1 z 6; fa{0..7}.CIN in x8 @ x 18..130 y 3 z 1; fa{0..7}.COUT out x8 @ x 10..122 y 2 z 2; fa{0..7}.S out x8 @ x 16..128 y 3 z 6; xr{0..7}.A in x8 @ x 15..127 y 1 z 10; xr{0..7}.B in x8 @ x 19..131 y 2 z 12; xr{0..7}.Y out x8 @ x 17..129 y 2 z 9
TOP VIEW (x -> columns, z -> rows; 1 char = 3x2 blocks (x by z); dominant class, redstone parts win | module letters)
   0         30        60        90        120        0         30        60        90        120
 0 ####++++#++++++++++#++++#++++++++++#++++#++++++#   ,,,,CCBB,CCCBBCCCBB,CCBB,CCCBBCCCBB,CCBB,CCCBEE,
 2 ###+++++++++#++++#++++++++++#++++#++++++++++##+#   ,,,CCCCBBCCC,BCCC,BCCCCBBCCC,BCCC,BCCCCBBCCC,,E,
 4 ###+++###++###+++##+++###++###+++##+++###++###+#   ,,,BCC,,,CC,,,BCC,,BCC,,,CC,,,BCC,,BCC,,,CC,,,E,
 6 ###++++++++++++++++++++++++++++++++++++++++++++#   ,,,BCBBBBBCBBBBCBBBBCBBBBBCBBBBCBBBBCBBBBBCBBBE,
 8 ###+#+##++#+#++#+##+#+##++#+#++#+##+#+##++#+#++#   ,,,B,D,,BB,D,BB,D,,B,D,,BB,D,BB,D,,B,D,,BB,D,BE,
10 ###++++#++++#+++++#++++#++++#+++++#++++#++++#++#   ,,,BBDD,BBDD,BBDDD,BBDD,BBDD,BBDDD,BBDD,BBDD,BE,
12 ###++++#++++++++++#++++#++++++++++#++++#+++++++#   ,,,BBDB,BBBDBBBBDB,BBDB,BBBDBBBBDB,BBDB,BBBDBBE,
14 ###++++#++++++++++#++++#++++++++++#++++#+++++++#   ,,,BBBB,BBBBBBBBBB,BBBB,BBBBBBBBBB,BBBB,BBBBBBE,
16 ####+++++++++++++++++++++++++++++++++++++++++++#   ,,,,BEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE,
18 ################################################   ,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
legend: +=wire  #=solid/other
modules: ,=(fixed)  B=modules/slice.py  C=fa#  D=xr#  E=build.py
SIDE VIEW (x -> columns, y -> rows, top = highest; 1 char = 3x1 blocks (x by y); all z collapsed)
  0         30        60        90        120
3 ....++++++++++++++++++++++++++++++++++++++++++..
2 ...+++++++++++++++++++++++++++++++++++++++++++..
1 ...++++++++++++++++++++++++++++++++++++++++++++.
0 ################################################
next: view --layer Y (y 1..3) [--box x0 z0 x1 z1] | find NAME | query X Y Z | test | trace PORT | diff | log

$ mira -p addsub8 test                         # 36 s (full run incl. the determinism re-run; 23 s with --fast)
TEST rev 1: PASS  public 6/6  other 52/52  reward 0.988  (1485 blocks, 36.3s)
BUILD: 1485 blocks, bounding volume 7128
R0 COMPILE: ok
R1 STRUCTURE: ok (at rest, paste-stable, nothing floating)
R2 CONNECTIVITY: 100% of required input->output paths reached
R3 BEHAVIOUR (public vectors): 6/6 pass
  per-output score (balanced accuracy, constant output = 0): S0=1.00, S1=1.00, S2=1.00, S3=1.00, S4=1.00, S5=1.00, S6=1.00, S7=1.00, COUT=1.00
SCORE (public checks only): R1=1.00 R2=1.00 R3=1.00  ALL PUBLIC CHECKS PASS
next: trace PORT [--vector K] to see timing; view/query around a failing output
```

This reproduces the session result (hidden 52/52, reward 0.988, `docs/EXPERIMENT_02.md`). Since this run the last score line of a full test
reads `SCORE (all checks): reward=0.988  R0=1.00 R1=1.00 R2=1.00 R3=1.00 R4=0.88  FULL PASS` (rev 2 output below).

```text
$ mira -p addsub8 trace S0 S1 COUT M --vector 3        # 14 s (replays the 58 vectors in the test's order, shows one)
TRACE S0, S1, COUT, M  stimulus: all vectors in the test's order (seed 5176); showing only v3 (ticks 257..370 of the run); at rest after 0 ticks
ticks 0..113, 1 column = 2 ticks; _ = 0, # = 1, hex = wire power, * = changed inside the column
      |v3
     0         20        40        60        80        100
  S0 #############*___________________________________________
  S1 ______________*####*_____________________________________
COUT ##############*_______________________________________*##
   M #########################################################
edges (value@tick): S0: 1@0 0@27 | S1: 0@0 1@29 0@39 | COUT: 1@0 0@29 1@109 | M: 1@0
stimulus:
  v3 @t259: set {A0=1,A1=1,A2=1,A3=1,A4=1,A5=1,A6=1,A7=1,+9} -> settle 110t  ok

$ mira -p addsub8 find fa3
build port fa3.A (in, face west) at [76, 2, 4]
build port fa3.B (in, face south) at [80, 1, 6]
build port fa3.CIN (in, face east) at [82, 3, 1]
build port fa3.COUT (out, face west) at [74, 2, 2]
build port fa3.S (out, face south) at [80, 3, 6]
instance fa3 = full_adder at [76, 0, 0] rot 0, 71 blocks [74, 1, 0]..[82, 3, 6] (placed at modules/slice.py:19)
```

The trace shows the ripple: on vector 3, S1 glitches high for 10 ticks and COUT takes 109 ticks to settle (a carry/borrow running through
all 8 slices).

Then a change: the M-bus repeaters go from delay 1 to 2 (an edit in `modules/slice.py`).

```text
$ mira -p addsub8 build -m "M bus repeaters delay 2"
BUILD OK rev 2 hash 36731a4964e7  1485 blocks  bbox [9, 1, 0]..[140, 3, 17] (132x3x18)  0.1s
...(palette / per-file / per-group lines as rev 1)
vs rev 1: +0 -0 ~8  (details: diff)
task rules: ok (1485 blocks inside build box [0, 1, 0, 141, 5, 18])

$ mira -p addsub8 diff
rev 1 -> rev 2
diff: +0 added, -0 removed, ~8 changed (1485 -> 1485 blocks)
by module: modules/slice.py +0 -0 ~8
by region (16x16 x-z tiles, origin -> edits): x112..127 z16..31: 1; x16..31 z16..31: 1; x32..47 z16..31: 1; x80..95 z16..31: 1; x64..79 z16..31: 1; x48..63 z16..31: 1; x128..143 z16..31: 1; x96..111 z16..31: 1
examples (up to 10 per kind):
  ~ [22, 1, 17] repeater[delay=1->2]
  ...(6 more lines, one per slice)
  ~ [134, 1, 17] repeater[delay=1->2]
source files changed: modules/slice.py (2 lines)

$ mira -p addsub8 query 134 1 17 -r 1
query [134, 1, 17] radius 1: 12 non-air cells
[133, 1, 17] redstone_wire[power=0]  <- build.py:8
[134, 1, 17] repeater[delay=2,facing=east,powered=false] (signal flows west)  <- modules/slice.py:55
[135, 1, 17] redstone_wire[power=0]  <- build.py:8
[133, 0, 16] smooth_stone  <- spec fixed
...(8 more floor cells)

$ mira -p addsub8 view --layer 2 --box 120 0 141 17
layer y=2  x 120..141 (columns)  z 0..17 (rows)  61 blocks
   2         3         4
   0123456789012345678901
 0 ....###.###...........
 1 ....#..+#<###+........
 2 <++<+.+}#{+...........
 3 ....A+#>#+............
 4 ...++.^...............
 5 ...+..+.#.............
 6 ##......##########....
 ...(rows 7-17)
legend: +=wire  ><^=repeater (arrow = signal flow)  #=solid/other  {}A=comparator (} { A V = flow east/west/north/south)
ports on this layer: fa0.A[124, 2, 4], fa0.COUT[122, 2, 2], xr0.B[131, 2, 12], xr0.Y[129, 2, 9]

$ mira -p addsub8 test                          # 46 s on a loaded machine
TEST rev 2: PASS  public 6/6  other 52/52  reward 0.988  (1485 blocks, 46.4s)
...
SCORE (all checks): reward=0.988  R0=1.00 R1=1.00 R2=1.00 R3=1.00 R4=0.88  FULL PASS
```

Notes. I filled the intent and facts into `NOTES.md`. The template had already listed the 26 spec ports as `port:NAME at (x, y, z)`. One
line describes an old draft on purpose:

```text
$ mira -p addsub8 notes-check
NOTES STALE (rev 2): 2 problem(s); 30 coord, 1 label, 1 module, 2 name, 2 part, 27 port checked
NOTES.md:43: label:carry_chain does not exist in rev 2
NOTES.md:43: no block at [50, 4, 3] in rev 2 (and no port there)
```

Hand-over:

```text
$ mira -p addsub8 export --nbt data/projects/addsub8/.mira/exports/addsub8.nbt      # 13.8 s
EXPORT rev 2: .../data/projects/addsub8/.mira/exports/addsub8-r2.litematic (4209 blocks, settled in 0 ticks, 791 cells changed by settling, readback ok) sha256 3164804e96e5621c
  file origin = world [0, 0, 0] (min corner); scenery from spec.json included
  vanilla structure data/projects/addsub8/.mira/exports/addsub8.nbt: 12589 bytes, DataVersion 4903, readback ok; no air stored (load with a structure block)
  placement path: raw program states vs this baked file after settling: 791 cells differ (quiet load), 0 (with an update pass); baked file re-loaded: 0 / 0  -> not placement-path sensitive
  13.7s; next: paste-check

$ mira -p addsub8 paste-check                                                         # 14.3 s, two fresh JVMs
PASTE CHECK PASS: 4209 blocks, vanilla 26.2 (DataVersion 4903), 40 idle ticks; exact=True stable_at_rest=True
file .../data/projects/addsub8/.mira/exports/addsub8-r2.litematic sha256 3164804e96e5621c
  known_shape placed==file: yes (missing 0, extra 0, changed 0); after idle==file: yes (missing 0, extra 0, changed 0); idle block changes 0
  place       placed==file: yes (missing 0, extra 0, changed 0); after idle==file: yes (missing 0, extra 0, changed 0); idle block changes 0
receipt: .../data/projects/addsub8/.mira/paste_receipt.json

$ mira -p addsub8 log
2 revisions (newest last)
rev   1  11-02T10:00  9eaf249bfa    1485 blocks         test: PASS  session program split into modules/slice.py + lib parts
rev   2  11-02T10:00  36731a4964    1485 blocks     +0  test: PASS; PASS  M bus repeaters delay 2
```

The project is left in `data/projects/addsub8/` (gitignored, like all of `data/`).

### Placement-path sensitivity (bistable builds)

`export` writes the env's **baked** world. That means placement through the engine in the env's order (`env.sim.compile_cells`, sorted
y,x,z), then a settle to rest, then reading the states back. It never writes the raw program blocks. It then compares this baked file with
the raw program states pasted and settled. The two numbers it reports:
- **quiet load**: almost always > 0, because a quiet paste of raw states leaves dust unconnected.
- **with an update pass**: if this is > 0, the build is flagged **placement-path sensitive**.

On the `t3_counter4` strong-agent session (`...-054924-af52`, T flip-flops) the export reports, measured:
- `125 cells differ (quiet load), 86 (with an update pass)` and `WARNING placement-path sensitive (bistable parts?): hand over THIS baked file, never the raw program blocks`;
- the baked file re-loaded: 0 / 0;
- `paste-check` PASS in vanilla (1634 blocks, both modes, 40 idle ticks);
- `test --fast` PASS (public 40/40, other 60/60).

This matches the A5 finding that pasting the raw program puts the counter in a different state.

A negative check that paste-check can fail: a lever/dust/lamp build saved straight from `Build.save` (authored states, not settled) gives
`PASTE CHECK FAIL` with `[1, 1, 0] file ...redstone_wire[east=none,...,power=0...] -> ...[east=side,...,power=15...]` (tested).

## Overview size against build size (measured)

Builds: corpus decompiles (`data/corpus/decompiled/*.py`, nearest block counts to the targets, entity-free, with redstone), the addsub8
project, and a synthetic test build. Tokens are estimated as chars / 3.6, the convention of `docs/BUILDER.md`. Times are CLI wall times,
including Python start-up.

| build | blocks | bbox | overview lines | chars | ~tokens | widest line | build | overview |
|---|---|---|---|---|---|---|---|---|
| `Torch_Based_Rail_Duper` | 10 | 3x5x1 | 17 | 645 | 179 | 108 | 0.33 s | 0.12 s |
| `71k_coral_farm_v2` | 152 | 8x7x7 | 25 | 966 | 268 | 175 | 0.17 s | 0.22 s |
| `t4_addsub8` project (2 module groups, spec) | 1485 (+2724 scenery) | 132x3x18 | 31 | 2861 | 795 | 157 | 0.14 s | 0.09 s |
| `lazy_never4get_power_5` | 1502 | 197x23x198 | 39 | 3417 | 949 | 153 | 0.22 s | 0.20 s |
| `Witch_Hut_GMF_Phazer_1.2.1` (needs `--lenient`) | 5001 | 43x131x39 | 32 | 1913 | 531 | 169 | ~0.3 s | ~0.2 s |
| `2m-cobble` | 19427 | 37x30x37 | 32 | 1755 | 488 | 180 | 1.0 s | 0.40 s |
| `600w--QQDT` | 101223 | 168x62x75 | 34 | 2286 | 635 | 179 | 4.2 s | 1.8 s |
| synthetic: 360 instances in 30 groups + floor (test) | >100k | 201x?x301 | <= 80 (asserted) | | | <= 200 (asserted) | | |

Reading: size depends on structure (number of module groups, port groups, kinds), not on block count. The maps cap at 60x18 (top view) and
60x8 (side view). Module rows cap at 10 groups plus "...", port groups at 8, and the widest lines are the palette and legend lines. The
largest is 39 lines / ~950 tokens. Not measured: builds with hundreds of differently named port groups (port lines are capped, the rest
reachable via `find`).

## Command reference (CLI = MCP)

| command | what it returns |
|---|---|
| `init NAME [--from-task ID] [--from-litematic F] [--program F] [--dir P] [--lenient]` | created files; `--from-litematic` decompiles (lossless gate) and implies `--lenient` (block ids unknown to the 1.21 validator pass through) |
| `build [-m MSG]` | `BUILD OK rev N hash ...` + palette, per-file and per-instance-group counts, ports, change vs previous, spec rules; or `BUILD FAILED` with `file:line: kind: message` |
| `overview [--rev N]` | see above |
| `view --layer Y [--box x0 z0 x1 z1]` | 1:1 slice; repeaters `> < ^ v` and comparators `} { A V` show the signal flow; `@` lit lamp, `t` wall torch; clipped at 100x60 with a notice |
| `find PATTERN` | spec ports (+doc), build ports (+face), labels, instances (part, at, rot, blocks, bbox, `file:line` of the place call), files, block ids with example coords; substring or glob |
| `query X Y Z [-r R]` | states (redstone-relevant props) + `file:line [instance]` or `spec fixed`, NBT for one cell; repeater/comparator flow spelled out |
| `diff [A [B]]` | added/removed/changed counts, by module group and 16x16 tile, examples with property-level changes, changed source files |
| `test [--module M] [--public-only] [--fast]` | verdict line (public, other, reward), `env.score.feedback` text, failing non-public vectors/sequences (product mode: the spec is the agent's own) |
| `trace SIG... [--vector K] [--seq S] [--set "A=1;A=0"] [--ticks N] [--all] [--module M]` | timing diagram (100 columns, downsampled with `*` for activity), edges, stimulus list with settle ticks and WRONG outputs |
| `notes-check` | stale references with NOTES.md line numbers |
| `export [--litematic F] [--nbt F] [--no-fixed] [--force]` | file, settle, readback, path check; refuses builds that never rest unless `--force` |
| `paste-check [--file F] [--ticks N] [--modes known_shape,place]` | receipt |
| `log [-n N]` | revisions with test verdicts and messages |
| `init-module NAME [--part P]` | module template + spec skeleton |

## Deviations, limits, open problems

- **Paste-check checks what the paste puts into the world, not behaviour in vanilla.** It places the file, idles and diffs. Replaying the
  spec in vanilla is `scripts/oracle_sessions.py` (the A5 owner). The two modes approximate Litematica's direct paste (`known_shape`, no
  neighbour updates) and `/setblock`/structure-block placement with updates (`place`). The real Litematica mod and the 1.21.0 Carpet server
  were **not** exercised.
- **The vanilla structure `.nbt` is written by our own NBT writer**, because Nucleation 0.12.0 cannot write it (`save_to_file_with_format`
  rejects `nbt`/`structure`, and it cannot load one either). It is verified only by our own reader (round-trip test, chest NBT, typed tags).
  It was **not loaded in vanilla**, because the oracle API takes SNBT, not a file. It stores no air, so pasting with a structure block leaves
  existing blocks in empty cells.
- **DataVersion labels.** Nucleation writes the `.litematic` with DataVersion 5023 (26.3 label). The paste-check feeds the oracle the file's
  blocks labelled 4903 (26.2) so that no DataFixer runs on them. The receipt records both numbers.
- **The settle for export ignores block NBT** (`env.sim.compile_cells` would need its NBT format). Container contents are re-attached to the
  file afterwards, so comparators that read containers may settle differently than in game. Not exercised by any task.
- `trace` replays the test's **first** vector pass (same seeded order). The test's second order-independence pass and the R2 probes are not
  shown. Raw-cell signals show wire power as hex and `1/0` for lit/powered/extended/triggered.
- **Module tree depth.** `builder._merge` does not propagate instances placed *inside* a part into `build.instances`, so the tree is one level
  of instances. Deeper levels appear only as `sub:` counts derived from the provenance paths.
- **One namespace** for all project files (a consequence of concatenation). It is enforced by the duplicate-name check, but helpers in
  different modules cannot share a name.
- **Time.** A full `test` of the 1485-block addsub8 takes 36-54 s on a loaded machine (`--fast` 23 s). Nothing is cached per module or
  incrementally.
- Not done:
  - no `checkout`/`undo` command (the sources of every revision are in `.mira/revs/N/src/`; restoring is a copy);
  - `view` slices are y-layers only;
  - unknown backticked words in NOTES.md are ignored unless they were names in an earlier revision.
- `--lenient` lets unknown block ids compile, but `export`, `test` and `trace` then depend on Nucleation loading them; a failure comes back
  as `EXPORT FAILED: simulator cannot load the build: ...`.

## Requests to owners of frozen files (nothing there was edited)

1. `env/score.py::feedback`: a `product=True` mode would help. Project mode sees the whole spec, so it should list failing non-public vectors
   and print the full R0-R4 score line. Today `project.test` adds those lines itself and rewrites the `SCORE (public checks only)` line.
2. `env/sim.py::compile_cells`: document or accept SNBT for `nbt` (it passes `json.dumps(nbt[pos])` to `set_block_with_nbt`). Then export
   can settle containers with their contents.
3. `simulation/builder.py` (not frozen, but not owned here):
   - an official multi-file compile, e.g. `compile(sources={"build.py": ..., "modules/x.py": ...})` with per-file line numbers in `BuildIssue`
     and provenance; this would replace the pre-processor;
   - propagation of nested `_insts` into `build.instances` (full module tree).
4. `tools/oracle`: a cheaper "place and read back" job (no per-tick change capture) for paste receipts; and a comparison of container
   inventories against the file's NBT (the oracle records `inventory_initial`, the receipt does not use it yet).
5. `AGENTS.md`: list `mira_mcp/project.py`, `simulation/views.py`, `simulation/export.py`, `scripts/mira.py`, `tests/project/`,
   `docs/PRODUCT.md` and `data/projects/` (default project location, gitignored).
