# Router and module layer (Phase A2, 2026-11-02)

`simulation/router.py` routes redstone nets in 3D around everything already placed; `simulation/modules.py` turns lib
cells into modules with ports and specs, places them, connects their ports with routed nets and localises faults.
`Part.route(...)` in `simulation/builder.py` is a thin delegate so a sandboxed build program can call the router.
Tests: `tests/unit/test_router.py` (61), `tests/unit/test_modules.py` (19, one marked `slow`), `tests/project/test_nets_and_checks.py` (`nets`, build log, warnings).

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

`b.route(src, sinks, name=None, need=None, box=None, avoid=None, keep_clear=None, floor_y=None, strict=True, restore=False, allow_side=False)`;
`src`/`sinks` are `"inst.PORT"`, `(x, y, z)` or `((x, y, z), face)`. Everything already on the Part is an obstacle; earlier routes and
their staircase clearances are remembered per Part. `avoid` = fixed task cells the Part does not hold (levers, lamps) or 6-tuple
boxes. `floor_y=0` = the task floor exists (no supports written there). Default box = bounding box of the Part + 4.

| argument | meaning |
|---|---|
| `need` | minimum dust strength at **every dust sink** of this net. Overrides everything derived (below). Default: the measured minimum of the lib port the sink stands for (`full_adder.CIN` 7), `mux2` data inputs 13, otherwise 1 |
| `strict` | `True` (default): an unroutable net raises `BuilderError("route: net 'q1': sink g1.B: no legal path ... (reason, blocking nets, ASCII map)")`; `False`: returns the `Net` with `ok == False`, `net.reason`, no cells |
| `restore` | `True`: a repeater may also stand on the last cell before a sink (it faces the pin and powers it with a fresh 15). Without it a sink whose `need` cannot be met by dust + in-line repeaters fails with a hint |
| `allow_side` | `True`: a face-only dust pin (below) may be joined from a side if its face is unreachable. The net routes, `net.warnings` and a build WARNING say the gate is probably dead |

`b.route_all(nets, order="auto", box=None, avoid=None, keep_clear=None, floor_y=None, strict=True, restore=False, allow_side=False, need=None)`
routes a batch: `nets` = `[(src, sinks)]`, `(src, sinks, name)`, `(src, sinks, name, need)` or dicts (`src, sinks, name, need, restore,
allow_side`). Returns the `Net`s in the order given. See "route_all" below.

Every routed net is written into the Part with provenance `net:NAME` and listed in `build.stats["nets"]`; project mode shows them with
`mira nets` and groups them as `(nets)` in `overview` (docs/PRODUCT.md). `net.summary()` prints cells, repeaters and the strength at every
sink; `net.note` / `net.warnings` / `net.infos` list side joins.

### Port entry rules (dead gates)

A dust port is joined by a route cell on its **face** side. When the cell inside the part (opposite the face) does **not connect to
dust** (a solid block that feeds a torch, a lamp, ...), the port dust only powers it while its single connection is the face: joined
from a side the dust bends (`west + north`) and no longer points at that block. The first free-form pilot hit this twice (`and2`/
`not_gate` joined from the side, net marked ok, R2 100 %, the gate silent). `lib/*.meta.json` now records per port
`entry`: `"face"` (the route must arrive through the face) or `"any"` (the inward neighbour is dust, an aligned repeater, a comparator, a
lever, a redstone torch, an observer's back: the side join only adds a connection). Measured on all lib parts with
`python env/library.py entries` (rule: `router.entry_rule`) and checked against the simulator by joining each input from a free side:
`and2.A/B` and `not_gate.A` are `face` and the truth table fails from either free side; `demux2.EN` is `face` (no free side to test). Every
other input of the 17 parts (27 ports: `mux2`, `or2`, `nor2`, `xor2` A/B, `half_adder` A/B, `full_adder` A/B/CIN, `restorer`, `delay_line`,
`edge_detector`, `d_flipflop`, `t_flipflop`, `sr_latch`, `crossover`, `wire_link`, `demux2.A`) is `any` and passes from every free side that was
testable (a first rule that only looked at the neighbour inside the part called `xor2.B`, `half_adder.B`, `full_adder.A/CIN` face-only; the
probe showed they work from a side because a repeater under-and-beside them reads the block under the dust, so the rule now includes it).
Outputs are always `any`.

- The router never relaxes a `face` pin to a side join unless `allow_side=True`.
- `any` pins may still fall back to a side when their face is blocked; the build then carries one aggregated WARNING ("N dust port(s)
  joined from a side other than their face; harmless") and `nets` lists the pins in `net.infos`.
- Ports of your own parts: a wrapper port declared on the same cell as an inner lib port (`p.port("A", g["A"], kind="in", face=WEST)`)
  is an alias and inherits entry rule, measured need and analog-ness from it (the builder records `(part, port)` per port, nested
  names included, so renamed instances work). Any other port gets `entry_rule` from the cells in the Part (a wrapper with a dust cell and
  a repeater in front of the gate is `any`). A raw coordinate that is a registered port gets the same rules as its name.

### Needs, analog parts, restoring

- `need` per sink = `min_in_strength_per_port` of the lib port (alias-aware), raised to 13 for `mux2.A/B` (**analog**: Y follows the
  input strength, measured 15/9/5 for 0/6/10 dust cells). A wrapper that puts a lib non-analog output (a `restorer`) on `Y` is not
  analog: its inputs only need the lib minimum. Source side: `mux2.Y` is a 13-strength source (`Pin.level`), so the router adds repeaters
  after it when the net is long. Override with `need=`.
- The router still inserts repeaters only on flat straight non-pin cells; `restore=True` adds the last cell before a sink for leaf sinks.
  Where neither is possible the error says so (`no straight flat cell left for a repeater (restore=True also allows ...)`); then move a
  module or lower `need` if the lib cell tolerates it.

### route_all: ordering, rip-up, failure reports

`Router.route_all(specs, order="auto", rounds=2, max_retries=16)` (`Part.route_all` wraps it):

1. Pins of every pending net are announced (`reserve_pins`) so early nets do not wall in later pins.
2. Order: `auto` = nets with a pin that has at most one free attach cell first (a face-only port counts its face only), then shortest total
   Manhattan length first (each extra sink adds 6); `short`, `constrained` (fewest free attach cells), `given` also exist.
3. Failed nets are retried after ripping up the nets **of this batch** whose cells are within 2 of their pins: the nearest blocker alone,
   then the 2 and 3 nearest; the failed net routes first, the blockers behind it; any failure restores the previous state exactly.
   Retries use a smaller search budget (80k states) so a hopeless net costs seconds, not minutes.
4. A net that still fails reports `net 'a': sink g.A: no legal path ...`, who holds the cells next to the pin, `blocking nets nearby:
   'wall'`, and a map:

```
route_all: 1 of 1 nets unroutable (after ordering and rip-up):
net 'a': sink g.A: no legal path (21756 states explored); port g.A must be entered through its face (the dust feeds a non-conducting block: a side join leaves the gate dead); free the cells in front of it, or pass allow_side=True
next to g.A: west cell (19, 1, 5): free, but dust there would touch net(s) 'wall'
blocking nets nearby: 'wall'
map y=1, x 11..29, z 0..10 (@ = pin)
  .......#...........
  .......a...........
  .......a.@###......
  ...
  legend: a=wall  #=block ~=reserved/outside/keep_clear .=free
```
(the `next to PIN` line lists, per attach cell of the pin, the block, net, reserved air, box limit or touching net that denies it.)

Measured (`tests/unit/test_router.py`, 16x16 box, 12 lever->lamp nets): in-order routing 8 nets (seed 6), `route_all(auto)` 12; with
rip-up 11 vs 10 without it (seed 0). Pilots (2026-11-03, plain lib gates, no wrappers, one `route_all` call): 4-bit counter 20/20 nets,
1950 blocks, build 17 s, 7/7 sequences pass in the env and in vanilla; 4-bit ALU 44/44 nets with default needs (mux inputs 13), 2833 blocks,
build 8.5 s, 1024/1024 rows pass (`test --fast` 60 s), 12 sampled vectors pass in vanilla at two origins.

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
| lib input ports need their measured `min_in_strength_per_port` (`full_adder.CIN` 7); `mux2` data inputs need 13 and its Y is assumed to carry only 13 (unless a restorer sits behind it) | `mux2` passes the input strength through (measured: A at 9 -> Y at 9, see below) |
| a sink lamp may also be fed by dust standing on it | a lamp is a conductor |
| nothing outside `box`, inside `keep_clear`, on reserved pad cells or on existing blocks; a one-sided dust port is joined from its face; other sides only as a fallback for `entry: any` ports (or with `allow_side=True`) | task bounds; lib port conventions; a face-only port joined from a side is a dead gate (entry rules above) |

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
- `Part.route` resolves sink needs/entries through the builder's port table (`part -> lib port` per port, alias by cell), so renamed instances and aliased wrapper ports work; a wrapper port on a *new* cell (a repeater in front of the gate) gets need 1 and its geometric entry rule. Pass `need=` for it.
- `restore=True` does not conjure space: it only adds the last-cell repeater. Where a net needs strength and has no straight flat cell, it still fails.
- `route_all` ripping up is local (nets of the batch near the failed pins), not a global negotiated-congestion router.
- `Design` runs in-process (not sandboxed): for trusted scripts / harness tools only.
- Timing numbers were taken under heavy load from four parallel agents.

## Requests to owners of files this work does not own

1. `docs/LIBRARY.md` / `lib/specs.py` (library owner): mux2 output strength is not 15 for weak inputs (measured table above);
   record it in the catalogue and the sidecar (`out_strength_follows_input`).
2. `env/sim.py` / validity agent: the engine-placement dust-shape bug above (repro in `tests/unit/test_router.py`). If
   vanilla agrees with the Placement reload, `compile_cells` should place dust in a second pass (all non-dust first, then
   dust, then re-place dust ends) or settle shapes after placement; then `repair_paste` becomes a no-op.
3. `AGENTS.md` and `evaluation/prompts/builder_system.md`: mention `b.route(...)` and `simulation/modules.py` (example above).
4. `mira_mcp/` (A1 project mode): expose `Design.check().summary()` and `Router.report()` as tools; both return plain text (project mode now has `nets`, which lists `Part.route` nets from the build record; `Design` is still not exposed).
