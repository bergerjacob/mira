# Router and module layer (Phase A2, 2026-11-02)

`simulation/router.py` routes redstone nets in 3D around everything already placed; `simulation/modules.py` turns lib
cells into modules with ports and specs, places them, connects their ports with routed nets and localises faults.
`Part.route(...)` in `simulation/builder.py` is a thin delegate so a sandboxed build program can call the router.
Tests: `tests/unit/test_router.py` (23), `tests/unit/test_modules.py` (16, one marked `slow`).

```bash
.venv/bin/python -m pytest tests/unit/test_router.py tests/unit/test_modules.py -q   # ~50 s, offline (Nucleation only)
.venv/bin/python simulation/modules.py random [--n 50] [--passes 4] [--seed 0]        # (a) random 2-pin nets, 32x8x32
.venv/bin/python simulation/modules.py t4_adder16                                     # (b) T4 interconnect rebuilt by the router, env-scored
.venv/bin/python simulation/modules.py t4_addsub8
.venv/bin/python simulation/modules.py alu4 [--rows 128]                              # (c) 4-bit ALU built only with Design + router
```

## API for a model (short)

**Inside a build program** (sandbox; lib parts via `env.library.program`):

```python
def build(b):
    b.fill((0, 0, 0), (40, 0, 20), solid())
    f0 = b.place(full_adder, at=(25, 0, 5), floor=False, name="f0")
    f1 = b.place(full_adder, at=(8, 0, 5), floor=False, name="f1")
    b.set((30, 1, 18), lever(face="floor", facing=EAST), port="A0")
    b.set((2, 1, 18), lamp(), port="S1")
    b.route("f0.COUT", "f1.CIN")                     # port names: "inst.PORT" or names given with port=
    b.route("A0", ["f0.A", "f1.A"], name="a")       # one source, a list of sinks = a tree
    n = b.route("f1.S", "S1")
    print(n.summary())                               # cells, repeaters, strength at every sink
```

`b.route(src, sinks, name=None, need=None, box=None, avoid=None, keep_clear=None, floor_y=None)`; `src`/`sinks` are
`"inst.PORT"`, `(x, y, z)` or `((x, y, z), face)`. Everything already on the Part is an obstacle; earlier routes and their
staircase clearances are remembered per Part. `avoid` = fixed task cells the Part does not hold (levers, lamps) or 6-tuple
boxes. `floor_y=0` = the task floor exists (no supports written there). Default box = bounding box of the Part + 4.
Unroutable -> `BuilderError("route: sink f1.CIN: no legal path ...")` with the reason.

**Outside the sandbox (trusted design scripts, harness tools)**:

```python
from simulation.modules import Module, Design
fa = Module.lib("full_adder")                        # ports, faces, measured min input strength, truth-table spec
d = Design(box=(-5, 0, -5, 50, 7, 30), floor_y=0)    # or Design.from_contract(task.contract): fixed scenery, levers/lamps as pins
d.add("fa0", fa, at=(30, 0, 5)); d.add("fa1", fa, at=(12, 0, 5), rot=0)
d.input("A0", (26, 1, 25))                            # pad input (redstone block for 1); kind="device" for a fixed lever
d.output("S0", "fa0.S")                              # read as dust power >= 1 (or a lamp position)
d.connect("A0", "fa0.A"); d.connect("fa0.COUT", "fa1.CIN")
d.spec(lambda A0, ...: {"S0": ..., ...})             # design truth table over inputs/outputs
res = d.build()                                      # place + route (3 rip-up passes) + paste-stability repair
print(res.summary())                                 # blocks, nets routed, unrouted nets with reasons
rep = d.check()                                      # per-net isolation, per-module isolation, design vectors in context
print(rep.summary())                                 # e.g. "VECTOR {...}: fault: module fa1 (full_adder): inputs {A:1,B:0,CIN:0} -> outputs {S:0,COUT:1}, spec says {S:1,COUT:0}"
c, blocks = d.design_contract(max_rows=128)          # env Contract (pads, wire_power outputs) for env.runner.run / env.score
```

Low level: `R.Router(world, box, keep_clear, reserved, floor_y)`, `.route(name, Pin(pos, face, kind, need), [Pin...])`,
`.unroute(name)`, `.repair_paste()`, `.blocks()`, `.report()`; `R.check_nets(router)` -> `{net: NetCheck}`;
`R.paste_diff(world)` -> the env paste_stable invariant on a plain world.

## Rules the router enforces (each from a verified fact or a measured failure)

| rule | why |
|---|---|
| every dust/repeater stands on a full block or a top slab; new supports are stone, or a top slab when a stone would touch a component | dust/repeaters pop without support (`env.blocks.floating`); a slab does not conduct |
| new dust/repeaters never touch (6-neighbourhood) a foreign component or foreign dust, and no foreign dust sits one level up/down diagonally | touching joins nets (shorts) or powers a lamp/component; diagonal dust connects unless a conductor is above the lower one |
| no new dust next to a block a foreign repeater/torch/lever/comparator strongly powers; no new stone support touching a component | a strongly powered block powers adjacent dust; a stone support under our dust is powered by it |
| climb: the cell above the lower dust stays air; step down: the upper dust stands on a full block (not a slab), the cell above the lower dust stays air. These air cells are reserved for the whole session | a conductor above the lower dust cuts the step; dust on a slab cannot step down (`step_down_over_slab_is_dead`) |
| crossings = 3D routing; a route over a foreign line uses a top-slab support | "hop on top slabs does not leak", "hop on full blocks leaks" (T4 facts) |
| repeaters only on flat, straight, single-child, non-pin cells, inserted after routing from the sinks upward so that every sink gets its `need`; `facing = opposite(flow)` | repeaters on turns/junctions kill branches; a repeater only takes input from behind |
| lib input ports need their measured `min_in_strength_per_port` (`full_adder.CIN` 7); `mux2` data inputs need 13 and its Y is assumed to carry only 13 | `mux2` passes the input strength through (measured: A at 9 -> Y at 9, see below) |
| a sink lamp may also be fed by dust standing on it | a lamp is a conductor |
| nothing outside `box`, inside `keep_clear`, on reserved pad cells or on existing blocks; a one-sided dust port is joined from its face, other sides only as a fallback (`net.note` says so) | task bounds; lib port conventions |

Search: A* over (cell, incoming direction, support kind), costs for length, turns (0.4), height steps (+0.5), new supports
(0.5), slabs (+0.3), passing next to other nets (0.15) and passing next to the pins of nets not routed yet (soft keep-out,
weight x3 for nets that failed in the previous pass). Multi-sink nets grow a tree (nearest sink first, later sinks branch from
any tree cell). Paths that touch themselves are rejected and re-searched with the offending cells banned. `Design.build`
does up to 3 rip-up-and-retry passes (failed nets first) and keeps the pass with the fewest failures.

### The engine-placement dust bug (A4 finding) and the repair

`env/sim.compile_cells` places blocks through the engine in (y, x, z) order. Some dust then keeps a wrong shape and the
env's `paste_stable` invariant (R1) fails although the behaviour is right. Minimal repro (shrunk from a real routed
world; `test_paste_diff_reproduces_the_engine_placement_dust_bug`): floor, `lever[face=floor,facing=north]` at (0,1,-1),
dust (0,1,0), and one more dust at (2,1,2) -> the stub at (0,1,0) differs after a Placement reload. Offsets of a later dust
that trigger it (measured, foreign dust at same y, |d| <= 4): stub off a lever (+1,+2), (+2,+2); end of a westward line
(+2, +-1..2); corner / line middle (+3, +-1..2). Nothing at dz = 0, nothing to the west. Vanilla behaviour unknown (the
validity agent classifies it). Because it is placement-order dependent and the full rule is not known, the router does
**not** use a clearance rule; it verifies instead: `Router.repair_paste()` runs the env-equivalent check (`paste_diff`) on
the whole world; for every differing cell it rips up the nets with route cells within 3 cells, bans those cells, re-routes
them (old route restored if that fails) and checks again (3 rounds). `Design.build()` does this by default and lists any
remaining failure in `res.errors`; `Design.check()` reports `paste_stable` too. Measured: it triggered in two random-bench
runs (seed 0 with 4 passes in an earlier version, seed 1 below) and was repaired every time; it did not trigger in the T4 / ALU
designs.

### Measured lib fact: `mux2` is analog

Driving `mux2.A` through k dust cells: Y = 15 (k=0), 9 (k=6), 5 (k=10) (`test_mux2_output_strength_follows_its_input_strength`).
All other combinational lib cells restore 15 (`not_gate nor2 or2 and2 xor2 half_adder full_adder demux2 restorer`; full_adder
COUT goes to 0 at k=10 because CIN needs 7). The catalogue in `docs/LIBRARY.md` lists mux2 output strength 15 (true only for
strong inputs). The first ALU build failed exactly here (fault report: `net m0_0.Y->m2_0.A: source m0_0.Y=1 but sink m2_0.A=0`).

## Benchmarks (measured 2026-11-02 on a shared machine, load average ~27; wall times are inflated and noisy)

### (a) random 2-pin nets, 32x8x32 box, random stone obstacles (8 %/(1+y)), floor at y=0

Pins are floor levers (source) and lamps (sink) at y=1, at least 2 cells apart; nets are routed one after another in one
session; every routed net is then checked in Nucleation with the whole world present (source lever on alone: the lamp must
light, nothing outside the net may change), and the whole world gets the paste check + repair.

| run | routable alone | routed | sim-checked ok | paste_stable first / after repair | route time total / max per net | route cells / Manhattan |
|---|---|---|---|---|---|---|
| 50 nets, seed 0, 1 pass | 50 | **33** | 33/33 | ok / ok | 34 s / 4.8 s | 816 / 983 (of all pins) |
| 50 nets, seed 0, 4 passes | 50 | 33 (best pass = first) | 33/33 | ok / ok | 18 s / 2.3 s | |
| 50 nets, seed 1, 1 pass | 50 | 32 | 32/32 | **fail (1 cell) / ok** | 24 s / 2.6 s | 710 / 997 |
| 50 nets, seed 2, 1 pass | 50 | 25 | 25/25 | ok / ok | 28 s / 5.4 s | 684 / 1101 |
| 25 nets, seed 0, 4 passes | 25 | **25** | 25/25 | ok / ok | 12 s / 2.2 s | 639 / 549 |
| before the pin keep-out (earlier code): 50 nets seed 0 | 50 | 27 | 27/27 | | 11 s | |

Reading: zero false routes (every net the router accepts works in the simulator and does not disturb any other net), but
**50 nets in 32x32 is congestion-limited at ~50-66 %**: 100 pins on one level, each pin needs a free 3x3 neighbourhood, and
the strict no-touch rules make every net a 3-wide corridor. 25 nets route completely. Failures are "no legal path"
(pins walled in by earlier nets). Rip-up passes do not help yet (no negotiated-congestion history on cells, only on pins).

### (b) T4 interconnect rebuilt from lib cells + router (`t4_design`), scored by the env on the task contract

Cells at the reference positions, every wire routed by the router (no `wire_path` waypoints); levers/lamps/floor are the
task's fixed scenery (`Design.from_contract`).

| task | nets | build + route | blocks (ref) | repeaters | net checks | env (all vectors, all invariants) |
|---|---|---|---|---|---|---|
| `t4_adder16` | 65/65 | 11 s | **1839** (1993) | 15 | 65/65 ok, paste ok | **full_pass, reward 1.000**, 56/56 vectors, 108 s |
| `t4_addsub8` | 41/41 (M = one 9-sink tree) | 19 s | 1657 (1311) | 24 | 41/41 ok, paste ok | **full_pass, reward 0.971** (R4 < 1: 26 % more blocks), 58/58 vectors, 59 s |

An earlier run of addsub8 (before the pin keep-out cost) gave 1364 blocks / reward 0.985: route length depends on the cost
weights; nothing was tuned per task.

### (c) 4-bit ALU-ish design built only via Design + router (`alu4_design`)

OP1 OP0 = 00 ADD (with CIN0, COUT), 01 AND, 10 OR, 11 XOR; per bit `full_adder`, `and2`, `or2`, `xor2` and a two-level
`mux2` select (3 per bit): 28 lib instances, 38 nets (A_i, B_i fan out to 4 cells; OP0 = 8-sink tree, OP1 = 4-sink tree),
11 inputs, 5 outputs.

| step | result |
|---|---|
| build + route | 38/38 nets, **2597 blocks, 106 repeaters**, 49 s (routing alone, 1 pass, no paste repair: 25.6 s) |
| `Design.check` | 38/38 nets ok in isolation, 28/28 module instances pass their truth table alone, **128/128 sampled design vectors** in context, paste_stable ok (87 s) |
| env runner on `design_contract(max_rows=128)` | **full_pass, reward 1.000, 256/256 vectors** (128 sampled rows + hidden reversed copy), every invariant, 115 s |

Not exhaustive: 2048 rows x 2 would take ~30 min in the env; the sample always contains all-0 / all-1 and is seeded.

## Deviations, limits, open problems

- **Congestion**: no negotiated congestion (PathFinder history costs), no layer assignment; dense random netlists route ~2/3.
  Big designs route because modules are spaced out by the designer. Next: history costs per cell, a preferred "routing
  layer" (y >= 3) for long nets, and dust-free vertical/crossing primitives.
- **No new lib primitives** were added (vertical elevator, compact crossover): staircases and slab hops were enough for every
  benchmark; `lib/` is untouched.
- Isolated net checks drive a dust source with a redstone block (strength 15); analog sources (mux2 Y) are checked at 15, the
  strength model assumes 13. The in-context design check and the env run are the real test.
- Paste repair is a check-and-retry, not a rule; a design can still end with a paste failure (reported in `res.errors`).
- Repeater placement is strength-driven only; there is no delay balancing (hold times of sequential designs are untested
  with routed nets; only combinational designs were benchmarked).
- `Part.route` derives lib sink needs from the instance's part name (`lib/<part>.meta.json`), so renamed copies get need 1.
- `Design` runs in-process (not sandboxed): for trusted scripts / harness tools only.
- Timing numbers were taken under heavy load from four parallel agents.

## Requests to owners of files this work does not own

1. `docs/LIBRARY.md` / `lib/specs.py` (library owner): mux2 output strength is not 15 for weak inputs (measured table above);
   record it in the catalogue and the sidecar (`out_strength_follows_input`).
2. `env/sim.py` / validity agent: the engine-placement dust-shape bug above (repro in `tests/unit/test_router.py`). If
   vanilla agrees with the Placement reload, `compile_cells` should place dust in a second pass (all non-dust first, then
   dust, then re-place dust ends) or settle shapes after placement; then `repair_paste` becomes a no-op.
3. `AGENTS.md` and `evaluation/prompts/builder_system.md`: mention `b.route(...)` and `simulation/modules.py` (example above).
4. `mira_mcp/` (A1 project mode): expose `Design.check().summary()` and `Router.report()` as tools; both return plain text.
