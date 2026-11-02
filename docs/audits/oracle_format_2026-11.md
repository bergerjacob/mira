# Oracle audit: format (2026-11-01)

## Recommendation

Use a hybrid: write each build as plain Python, and keep the existing `(x,y,z,state,nbt)` block-tuple list as the flat format that everything compiles to. Nucleation sits underneath and handles schematic storage, rotation/mirroring, settling and simulation. Don't invent a new DSL, and don't author in JSON block lists.

You remembered two formats, and both exist:
- **The JSON block list:** `evaluation/schemas/block_list_schema.json`, `evaluation/prompts/json_format_system.md`, the fixtures in `tests/fixtures/circuits`, and the tuples in `training/generators`.
- **The "code that adds blocks" format:** `ctx.set_block` in `evaluation/prompts/python_format_system.md`, run against `VerificationContext` in `tests/integration/conftest.py`.

Verdict on each:
- **JSON block list as the thing the model writes: worse, retire it.** It is fine around 10 blocks, too costly to write and edit at 1k, and impossible at 100k. It survives as the flat interchange format, which the replicator, fixtures, generators, receipts and diffs already use.
- **The `ctx` Python format: right idea, but it has never worked.** It has to be rebuilt as a `Part` builder (sketch below).
- **A new declarative DSL: no.** Models have no prior for it. Nucleation's ASCII `SchematicBuilder` already exists and only returns bare error codes.

### Measured token cost

I loaded a random sample of 60 Discord `.litematic` files with Nucleation and estimated tokens as characters ÷ 3.6. "Compact" means a palette plus `x y z id` lines; "fills" means boxes merged greedily per block state. Redstone-dense machines barely shrink with fills (about 1.2–2× over compact); bulk structure shrinks 5–35×.

| Build (blocks) | JSON list | Compact | Fills |
|---|---|---|---|
| TickStasis (1,082) | 26.6k | 5.7k | 4.6k |
| Hoglin farm (12,612) | 214k | 35k | 8.8k |
| YAMS (88,661) | 1.97M | 339k | 102k |

- **10 blocks:** JSON is about 230 tokens and Python `set()` lines about 150. Either works, so one authoring format is enough.
- **1k blocks:** JSON is about 23k tokens to write, and every edit means rewriting coordinates. A builder costs roughly (distinct cells × cell size) + placements. A 4-bit adder made of four 1-bit adder cells is about 1–2k tokens.
- **100k blocks:** no flat form fits, even with fills. Only a hierarchy works: named parts, arrays and library cells, with the model reading views on demand and never the whole flat build.

### How the Python builder compares to the JSON list

- **Edit locality:** a fix inside one cell is one hunk and updates every copy; re-spacing a carry chain is one number. In JSON the same change is N copies or a rewrite of every downstream coordinate.
- **Error reporting:** Python tracebacks give line numbers. On top of that, every block records where it came from (part path and source line), so simulator failures, overlaps and validation errors point back to code lines.
- **Determinism:** run the build file in a sandboxed subprocess with only the builder importable, no clock or randomness, and caps on time and block count. Compile twice and compare hashes.
- **Rotation and mirroring:** Nucleation transforms block states correctly. I checked that `rotate_y(90)` turns a repeater from east to south, and `flip_x` turns stairs and repeaters east→west and swaps the wire's side/up properties.
- **Components and ports:** ports are named positions that move with the part's transform, so wiring code and specs refer to names, not coordinates.

### Turning the corpus into the same format (for SFT)

The reverse compiler needs only Nucleation and about 200 lines of our own code:
1. Load with `Schematic.load_from_file`. It is fast: 88k blocks took 0.4 s.
2. Translate to the minimum corner and write state strings in one canonical form. Nucleation does not sort properties; I saw `north,power,south,west,east` on a wire.
3. Optionally split into connected pieces with `split_connected_attach_nearby`.
4. Find repeated motifs with `Autostack.detect_structures`. It took under 0.05 s and found a 1D array covering 99% of the trading hall and a 2D array covering 85% of the hoglin farm. Emit one `array(cell, n, step)` plus an explicit list of exceptions, so the result stays lossless.
5. Turn the leftovers into `fill` boxes, or `set` for single blocks. Pass block-entity NBT and entities through unchanged.
6. **Gate on round-trip:** keep a file only if compiling the generated program reproduces the original exactly (states, block entities, entities). One fixed printer means one schematic always gives the same program text.

## Minimal API sketch

New module `simulation/builder.py`, following the repo's no-`__init__` convention:

```python
class P(NamedTuple): x:int; y:int; z:int            # supports P + (dx,dy,dz)
class Part:
    def set(self, pos, state: str, nbt: dict|None = None, port: str|None = None) -> None
    def fill(self, a, b, state: str) -> None
    def layers(self, origin, palette: dict[str, str], rows: list[list[str]]) -> None  # own impl, row/col errors
    def use(self, path: str) -> None                 # library cell (.litematic + ports sidecar)
    def place(self, part, at, rot: int = 0, mirror: str|None = None, name: str|None = None) -> Inst
    def array(self, part, n: int, step, at=(0,0,0), rot: int = 0, name: str|None = None) -> list[Inst]
    def port(self, name: str, pos, kind: str = "in", face: str|None = None) -> None
    def label(self, name: str, a, b=None) -> None    # named region for notes/views
class Inst:  def __getitem__(self, port: str) -> P   # world position after transform
def part(ports: dict[str, P] | None = None)         # decorator; cached, deterministic
def wire_path(b: Part, a, z, support="minecraft:stone", repeat_every=15) -> None
# state factories moved from tests/helpers/circuit_builder.py: lever(face, facing), repeater(facing, delay), wire(), torch(wall=None), lamp()...
def compile(src: str) -> Build   # Build.blocks: list[BlockTuple]; .provenance; .ports; .errors; .schematic(); .save(path)
def decompile(src_or_schematic, min_fill: int = 3, motifs: bool = True) -> str   # compile(decompile(x)) == x
```

What it reuses:
- `BlockTuple` and the state-string helpers from `circuit_builder.py`, with `face` added.
- `VerifyContract` from `training/generators/base.py`, kept as a separate spec file that refers to port names.
- `replicator.replicate_blocks` for pasting.
- Nucleation for transforms, `stamp_region`, settling, `Diff`, `Autostack` and the simulator.
- Optionally, lowering a part into a Nucleation `Design` cell to get `route_bus` and DRC/LVS checks. I confirmed `add_cell` plus `place(rot=90)` works with a hand-written contract.

Example: a 4-bit adder built from four 1-bit full-adder cells (`builds/adder4/build.py`). The cell's internals come from a library file or `p.layers`, so this is illustrative, not a verified design.

```python
from simulation.builder import part, Part, P, wire_path, lever, lamp

@part(ports={"a": P(0,1,0), "b": P(0,1,2), "cin": P(3,1,-1), "s": P(7,1,1), "cout": P(3,1,4)})
def full_adder(p: Part):
    p.use("lib/full_adder_v1.litematic")          # sim-verified library cell (or p.layers(...))

def build(b: Part):
    fa = b.array(full_adder, n=4, step=(0, 0, 6), name="fa")   # carry chain along +z
    for i in range(3):
        wire_path(b, fa[i]["cout"], fa[i + 1]["cin"])
    for i in range(4):
        b.set(fa[i]["a"] + (-1, 0, 0), lever(face="floor", facing="west"), port=f"A{i}")
        b.set(fa[i]["b"] + (-1, 0, 0), lever(face="floor", facing="west"), port=f"B{i}")
        b.set(fa[i]["s"] + (1, 0, 0), lamp(), port=f"S{i}")
    b.port("CIN", fa[0]["cin"]); b.port("COUT", fa[3]["cout"], kind="out")
```

## Findings in the existing code

| Severity | Where | Finding |
|---|---|---|
| high | `evaluation/quick_test.py:146-155`, `evaluation/prompts/python_format_system.md` | The Python format was never executed. It only checks that the text contains `def build_circuit` and `set_block`. The prompt's `set_block(x,y,z,state)` doesn't match `tests/integration/conftest.py:203` `set_block(pos, state)`. The model also writes its own `verify_circuit`, which is the "unfair test" problem. |
| high | Nucleation `Schematic.set_block` | It silently accepts invalid input: I tried `minecraft:redstone_wir`, `repeater[facing=up]`, `delay=9` and `stone[foo=bar]`, and all were stored as given. Its block list is from 1.21.6 or later (`copper_golem_statue` and `dried_ghast` are present), but our server is 1.21.0. We need our own validator built from the 1.21.0 server's data reports. |
| medium | Nucleation `Design` | `add_cell` needs a contract JSON. Errors come back as bare codes (`InvalidArgument`, `Parse`). `Design.import_litematic` failed with `Parse` on 3 of 3 plain corpus files, so it can't be the corpus import path or the authoring surface. |
| medium | `tests/helpers/circuit_builder.py` | `lever` and `button` have no `face` argument (the 1.21 block-list default is `face=wall`), and there is no wall-torch helper. It lives under `tests/`, so runtime code can't import it cleanly. Its prop ordering also differs from `format_state` in `training/generators/base.py` (sorted vs. unsorted): two canonical forms. |
| medium | `data_mining/parser.py` | Entities go into the block list as `entity:<id>` with float coordinates. Block-entity positions are guessed (`candidates` local vs. absolute), and loading loops block by block in litemapy. Don't use it for a lossless reverse compiler; use Nucleation. |
| medium | `simulation/replicator.py` | It sorts the caller's list in place, skips entities, and cuts container items down to id/count/Slot. Pasting is lossy, so paste receipts must report this. |
| low | `evaluation/schemas/block_list_schema.json` | No `nbt` field, although fixtures and `to_dict_list` emit one. |

## Risks, and what would falsify the recommendation

1. **The builder may not beat JSON plus patch tools in practice.** Test: 20 tasks from lamp up to 4-bit adder and an 8-wide door, one or two frontier models, fixed specs, Nucleation simulator, 3 feedback rounds. Compare a JSON+patch arm with a builder arm on pass rate, tokens per success, rounds, and lines changed per round. The builder is falsified if it doesn't match JSON on builds of 50 blocks or fewer and clearly win on 300 or more. Cost: tens of dollars, after the measurement fix.
2. **The corpus may not round-trip cheaply.** Test (local, free): decompile all 2,296 files and record lossless rate, size relative to the JSON list, and runtime. Prediction: about 100% lossless, a median of at least 5× smaller, and at least 20× on repetitive builds.
3. **Rotation and mirroring may change behaviour,** because some contraptions are directional. Test: rotate the 14 fixtures and 124 generated circuits through 4 rotations and a mirror, simulate each, and record the share that still behaves the same. If many break, parts need flags marking which orientations are verified.
4. **Settling depends on placement order** (placing "like a hand" with `{simulate=true}`). Test: compile with shuffled placement order in fresh processes and compare hashes of the settled schematic.
5. **Sandboxing Python** is a security and determinism problem: subprocess, whitelisted imports, caps on time and blocks.
6. **SFT style mismatch:** decompiled programs come out flat, with fills, unlike hierarchical programs an agent would write. The corpus is also mostly farms that depend on mobs. Use it for retrieval and repair/edit tasks, not as the main generation-SFT source.
7. **Nucleation is at 0.12 and changing.** Keep all Nucleation calls in one module; the block-tuple format means it can be swapped out.
8. **Two sources of truth** if patch tools edit compiled artifacts directly. All edits should go to the source file; for imported schematics, the decompiled program becomes the source.
