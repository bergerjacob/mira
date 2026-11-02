# Builder: the authoring layer (2026-11)

Implements the format decision in `docs/audits/oracle_format_2026-11.md`: a build is plain Python written against a small `Part` API, compiled in a sandbox to the flat `(x, y, z, state, nbt)` block list that everything else already speaks. Nucleation sits underneath for storage, transforms and simulation. The reverse direction, `decompile()`, turns any `.litematic`/`.schem` into the same kind of program, gated on a lossless round trip.

Files: `simulation/builder.py` (API, sandbox, `compile`, `decompile`), `simulation/blockstates.py` + `simulation/blockstates_1_21.json` (validator, canonical form, rotate/mirror tables), `scripts/corpus_decompile.py`, `tests/unit/test_builder.py` (95 tests), `evaluation/prompts/builder_system.md` (model-facing prompt with three worked examples).

## Quick start

```bash
source .venv/bin/activate                       # or call .venv/bin/python directly
python -m pytest tests/unit/test_builder.py -q   # 95 tests, ~3 s, no server/API key
python simulation/builder.py my_build.py         # compile a program, print the report
python simulation/builder.py some.litematic      # decompile to program text on stdout
python scripts/corpus_decompile.py               # whole corpus -> data/corpus/decompiled/*.py (+ stats), ~1 min
python simulation/blockstates.py --regen         # rebuild the 1.21.0 registry from simulation/server/server.jar
```

```python
import sys; sys.path.append(".")
from simulation import builder as B

build = B.compile(open("my_build.py").read())     # sandboxed subprocess; never raises
print(build.summary())                             # counts, bounds, ports, errors/warnings with line numbers
build.blocks      # [(x, y, z, "minecraft:...[sorted,props]", nbt_snbt_or_None), ...] sorted by (y, z, x)
build.prov        # parallel list of (part/instance path, source line) per block
build.ports       # {"name": {"pos": [x,y,z], "kind": "in|out|io", "face": ...}}
build.hash()      # sha256 over blocks + entities (determinism check)
sim = build.simulate()                             # Nucleation TickSimulation, settle="Placement"
sim.use_block(*build.local(build.ports["A"]["pos"]))
build.save("out.litematic")                        # via Nucleation (.litematic / .schem)
text = B.decompile("some.litematic")               # lossless-gated; raises DecompileError(reason=...)
```

Program shape: `from simulation.builder import *` then `def build(b):`. `evaluation/prompts/builder_system.md` is the full model-facing reference with the three examples (lamp, NOT, AND-from-three-inverters); `tests/unit/test_builder.py` extracts and simulates exactly those code blocks, so the prompt cannot drift from the code.

## API reference

**Positions.** `P(x, y, z)` is a NamedTuple: `P + (dx, dy, dz)`, `p.step("south", 3)`. Plain tuples work everywhere. x east, y up, z south.

**Part methods** (`b` is the root part; `@part` cells receive their own):

| method | notes |
|---|---|
| `set(pos, state, nbt=None, port=None, kind=None)` | `state` may be `(state, nbt)`; `port=` registers a port (kind guessed: lever/button/plate = in, else out) |
| `points(state, *positions)` | same block at many positions |
| `fill(a, b, state, nbt=None, keep=False)` | inclusive box; volume checked against the cap before expanding |
| `layers(origin, palette, rows)` | `rows[y][z][x]`; `"."`/space = no block; palette keys must be 1 char to be used in rows (longer keys are ignored, so one dict can serve `layers` and `set`) |
| `get(pos)`, `remove(a, b=None)` | air is never stored; setting `air()` deletes |
| `entity(id, (x, y, z), nbt=None)` | float position; rotated/mirrored with the part, other entity NBT is not |
| `port(name, pos, kind="in", face=None)`, `label(name, a, b=None)` | named positions/regions that move with the part |
| `place(part, at, rot=0, mirror=None, name=None, **params)` -> `Inst` | `inst["port"]` = world position after transform |
| `array(part, n, step, at, rot, mirror, name, **params)` -> `[Inst]` | instance i at `at + i*step` (world-space step); ports are also registered as `"<part>[i].<port>"` |
| `use(path, at, rot, mirror, name)` | library cell: `.litematic/.schem` under `lib_root`, ports from `<file>.ports.json` |

`@part` / `@part(ports={"in": P(0,1,0)})`: the body runs once per distinct params and is cached, then stamped per placement (deterministic; provenance still gets per-instance paths). `rot` is degrees clockwise from above, `mirror="x"` negates x, `"z"` negates z, mirror first then rot, then translate.

**State factories** return canonical-able strings (validated when they reach `set`): `block(id, **props)`, `solid(material)`, `stone glass dirt air redstone_block wire lamp(lit) lever(face,facing,powered) button(facing,face,powered,material) pressure_plate(material,powered) repeater(flow|facing, delay, locked, powered) comparator(flow|facing, mode, powered) observer piston(facing,extended,sticky) sticky_piston hopper dropper dispenser torch(wall=None,lit) chest barrel slab stairs target note_block daylight_detector`, `items(...)` (container NBT), `Byte`.

`wire_path(b, a, z, support="minecraft:stone", repeat_every=15)`: dust from a to z, x first then z, climbing one block per horizontal step when heights differ, supports under each wire, a repeater replacing every 15th flat cell (so 14 dust blocks between repeaters; conservative), occupied cells (port components) left alone.

**Build** fields: `blocks, prov, entities, ports, labels, instances, errors, warnings, log, stats`; `ok`, `hash()`, `bounds()`, `provenance(x,y,z)`, `state_dict()`, `to_json()` (the `{x,y,z,state[,nbt]}` interchange list), `summary()`, `schematic()`, `simulate(settle="Placement")`, `local(pos)`, `save(path)`. Entities are `(x, y, z, id, nbt)` with float positions.

**Issues** (`BuildIssue(kind, message, line, path)`): `syntax no_build sandbox runtime api limit timeout unknown_block unknown_property bad_value bad_nbt unknown_palette_char duplicate_port` (errors) and `overlap` (warning: a point write replaces a different point-written block; fills overwrite silently). Validation errors do not stop the build: the bad block is skipped, everything else is placed, and all errors are reported at once.

## Decisions and why

1. **Canonical state form = fully explicit, sorted.** `minecraft:repeater[delay=1,facing=east,locked=false,powered=false]`. Defaults are filled in from the 1.21.0 registry, properties sorted by name. This resolves the two canonical forms the audit found (`circuit_builder._state` sorted vs `format_state` unsorted) and makes tuple equality meaningful. Printed programs (decompile) drop default-valued properties and the `minecraft:` prefix; compile restores them, and the round-trip gate proves it.
2. **Validator source = the real 1.21.0 server data report** (`blocks.json` from `java -DbundlerMainClass=net.minecraft.data.Main -jar server.jar --reports`), condensed to 59 KB (1060 blocks, 107 distinct property sets, defaults). Not Nucleation: it stores anything (`redstone_wir`, `repeater[facing=up]`, `stone[foo=bar]` all accepted). Errors name the block/property/allowed values and suggest close matches. `strict=False`/`validate=False` passes unknown blocks through (needed for corpus files from newer versions).
3. **Own rotate/mirror tables, not Nucleation's.** Measured against `Schematic.rotate_y(90)/flip_x/flip_z` over every property of every block: Nucleation does not transform rail `shape`, crafter `orientation`, chest `type`, or sign/skull `rotation` under `flip_z`; it gives `inner_right` for a *rotated* `inner_left` stair and does not swap handedness on stair mirror; it resets `jukebox[has_record]`. The tables here rotate/mirror `facing`, `axis`, `rotation`, `orientation`, per-side props (wire/fence/wall/pane/vine), rail `shape`, stair `shape`, door `hinge`, chest `type`. Checked by (a) group laws over the whole registry (4 rotations = identity, mirror twice = identity, mirror-x then mirror-z = rot 180, every output a valid state: ~80k transforms), (b) agreement with Nucleation on `facing/axis/rotation` for rot90/flip_x, (c) **simulation**: a lever -> NOT -> lamp cell stays functional under rot 90/180/270, mirror z, mirror x + rot 90 (`test_transformed_inverter_still_inverts`).
4. **`repeater(flow=...)`/`comparator(flow=...)` take the direction the signal travels** and set `facing = opposite(flow)`. Minecraft's raw `facing` points at the *input* side, which is the single most common LLM mistake; `facing=` is still accepted for raw access. This deviates from the audit sketch (`repeater(facing, delay)`); verified in simulation (`test_repeater_flow_convention_delivers_signal_forward`: reversed repeater blocks the signal). Raw state strings are untouched, so mirror/rotate semantics stay Minecraft's.
5. **Sandbox = three layers.** (i) Static AST check: only `simulation.builder`/`builder`/`math`/`itertools`/`functools`(reduce, partial, lru_cache, cache)/`typing` imports; no attribute starting with `_` and no frame/code attributes (`gi_frame`, `f_back`, ...); no `eval exec compile open getattr setattr globals vars dir type object ...`, classes, async, bare `except`. (ii) Runtime: restricted builtins and an import hook that hands out *synthetic* module objects containing only the public API (so `from simulation.builder import os` fails), `print` captured into `build.log`. (iii) Process: `compile()` runs a fresh `python -s -P` child with an empty environment (so no API keys), `PYTHONHASHSEED=0` (string-set iteration is deterministic), an empty temp cwd, `RLIMIT_AS` 6 GiB, `RLIMIT_CPU`, `RLIMIT_FSIZE=0`, `RLIMIT_NOFILE=32`, recursion limit 600, wall-clock timeout (default 20 s), block cap (default 2M; `BuildLimit` derives from `BaseException` so `except Exception` cannot swallow it). Startup + tiny compile: ~30 ms; 400k-block fill: 0.5 s.
   **Honest limits:** this is defense in depth for model-written programs, not a security boundary against a determined attacker who can find a new Python object-graph escape; there is no network namespace/seccomp. Do not run untrusted *humans'* code with it without adding an OS-level jail. `compile(src, sandbox=False)` runs the same restricted namespace in-process for trusted/generated programs (decompile's gate uses it).
6. **Tuples carry canonical SNBT strings for NBT** (`None` if none): `{Items:[{Slot:0B,count:64,id:"minecraft:redstone"}],...}`. Sorted keys, quoted strings, numeric literals kept verbatim with upper-case suffix. `replicator.py` already accepts string NBT. `Part.set` also takes a Python dict (bools -> `1B`/`0B`, ints plain, floats `D`, `Byte(n)` -> `nB`).
7. **Provenance** per block = `(instance path, line)`, e.g. `("fa[2]/cell", 7)`; `build.instances` lists each placement with its `place`/`array` call line. Remapped per instance, not per block, so arrays stay cheap.
8. **`settle` is not baked into `compile`.** `schematic()` stores the compiled states as given; `simulate()` uses `TickSettleMode.Placement` because `InWorld` does not propagate initial power: measured with the AND gate, an `InWorld` load leaves lit torches next to unpowered dust (the circuit then misbehaves), `Placement` is correct (spike's "compile to at-rest states" rule). Saving *settled* files (`sim.bake_to`) is left to the harness.

## decompile(): schematic -> program

`load_normalized()` (Nucleation `get_non_air_blocks_json`, block entities as SNBT, entities as SNBT) translates to the min non-air corner, canonicalizes states (`normalize(strict=False)`), drops block-entity `x/y/z`, shifts entity `Pos`. The printer then emits, deterministically:

1. `K = {...}` palette (1 char per block kind, most frequent first; kinds with NBT are `(state, nbt)` pairs; >88 kinds fall back to `k<n>` keys that only `set/fill/points` can use);
2. **arrays**: `Autostack.detect_structures` candidates (1D/2D), *verified exactly* against the data (every lattice cell compared to the template, including empty space), largest all-matching run/rectangle kept, emitted as `@part def cellN(p)` + `b.array(...)` (2D = a row part arrayed again); non-overlapping, axis-aligned steps only, <=8192-cell boxes, >=3 instances, >=24 blocks;
3. greedy x-z-y **fill boxes** (>= `min_fill`=3 cells of one kind);
4. leftovers per y-layer as **`b.layers`** when the ASCII grid is cheaper than `points`, else **`b.points(K[..], (x,y,z), ...)`**;
5. `b.entity(...)` lines.

**Gate:** with `verify=True` the program is executed again and compared with the original: block states, block-entity NBT, entities (id, float `Pos`, NBT), `DecompileError(reason)` with `reason` in `program_error | block_mismatch | nbt_mismatch | entity_mismatch | bad_state`. Same input -> same text (tested). Air-likes (`air`, `cave_air`, `void_air`) are treated as empty on both sides. Entity NBT other than `Pos`/`id` (including the stale `AABB` that litematica stores in world coordinates) is passed through verbatim.

## Measured: corpus decompile (2296 `.litematic`, 16 workers)

`python scripts/corpus_decompile.py` -> `data/corpus/decompiled/*.py`, `data/corpus/decompile_stats.jsonl`, `data/corpus/decompile_summary.json`. Tokens = chars / 3.6. "JSON" = `json.dumps` of `{x,y,z,state[,nbt]}` rows (+ entities); "compact" = palette lines + one `x y z idx` line per block.

| metric | value |
|---|---|
| files / lossless | 2296 / **2260 (98.4%)**; every file Nucleation can load is lossless on the first attempt (motif mode); the flat fallback was never needed |
| failures | 35 `load_error` (Nucleation `InvalidArgument`: litematic regions with **negative sizes**, e.g. `Unnamed (-12,-9,-3)`), 1 `bad_state` (corrupt palette entry `sticky_piston[properties={"facing":...}]`) |
| wall time | 55 s for the whole corpus (16 workers; 854 CPU-s); median 0.046 s/file, max 23.6 s (595k-block gold farm, incl. verify) |
| blocks (lossless files) | 20.45M |
| tokens JSON / compact / program | 519.6M / 100.4M / **67.1M** |
| program vs JSON, median | **4.27x** (p10 1.72x, p90 18.2x); builds >= 1000 blocks (1115 files): 7.34x; >= 10k blocks (331 files): 12.0x |
| files with a detected array (397) | median 8.4x, p90 72.8x; 190 files (8.4%) >= 20x |
| files < 1x (program bigger than JSON) | 4 |
| program vs compact | median 1.05x (>=1000 blocks: 1.27x) |
| tokens per block (median) | 6.1 program vs 26.1 JSON |
| program tokens p50 / p90 / max | 5.3k / 66.6k / 2.33M (JSON: 29k / 463k / 11.2M) |

Reading: the audit's prediction ("median at least 5x, 20x on repetitive builds") is *close but not met overall* (4.27x median; 7.3x for non-trivial builds; 8.4x median / 72x p90 where an array exists). The program is only ~5% shorter than the compact palette listing at the median, so the real win over compact comes from fills/arrays on bulk-heavy builds. The chars/3.6 estimate favours digit-heavy compact lines (digits tokenize worse than prose), so true compact-vs-program is probably better for the program than shown. By character, 51% of the program text is `K` entries and 17% entity lines: NBT payloads (chest inventories, signs, mob data) that the lossless gate forces us to carry; a lossy "geometry-only" view would shrink programs a lot but is not implemented.

## Deviations from the audits / things to flag

- `repeater`/`comparator` take `flow=` (see decision 4).
- `wire_path` repeater cadence is "every 15th cell" (14 dust between repeaters), not "after 15 dust".
- `place`/`array` accept cell parameters as `**params`; `array` steps are in world space (after rot).
- `Build.blocks` coordinates are as authored (not translated); `schematic()` translates to a given origin; decompile output is translated to (0,0,0).
- The sketch's `compile` name shadows the builtin; it is deliberately *not* in `__all__`, call `builder.compile`.
- Block-state data is a new committed data file, `simulation/blockstates_1_21.json` (59 KB), generated from the server jar; it is 1.21.0 while Nucleation/oracle target 26.x. A 26.x registry would let the validator match the oracle (`--regen` accepts any server jar with the data generator).
- Audit "risk 3/4" (rotation changes behaviour; settle order) are only spot-checked here (one cell, six transforms; no shuffled-order test).

## Open problems

- 35 corpus files with negative region sizes need a litemapy fallback (its tile-entity coordinates for negative regions are unverified, so none was added).
- NBT dominates program size; consider an explicit lossy "strip NBT/entities" view for retrieval/SFT.
- `use()` library cells are implemented but only exercised through the same loader the corpus uses (no committed library yet).
- Sandbox is not an OS jail (see decision 5).
- `AGENTS.md` should mention builder/blockstates/corpus_decompile and the `Placement` settle rule; `requirements.txt` already has nucleation. Neither file is owned by this task.
