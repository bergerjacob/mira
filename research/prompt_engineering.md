## Prompt: Generate Python For Custom Litematic

You are an expert Minecraft technical builder and Python engineer. Write production-grade Python that uses `litemapy` to build a `.litematic` schematic exactly matching the user’s design. Follow these instructions exactly so there is zero ambiguity.

---

### 1. Design Brief
- The schematic description will be injected here: `<<<DESCRIBE SCHEMATIC HERE>>>`.
- Assume all relevant dimensions, materials, redstone behaviors, and orientation rules are fully specified inside that block; never invent details.
- Translate every requirement into coordinates, block states, and helper routines. If something seems ambiguous, resolve it by stating your assumption explicitly before the code.

### 2. Deliverables
Return **only**:
1. A short overview (≤120 words) explaining the structure, coordinate frame, and any assumptions.
2. A fully self-contained Python module in one fenced code block (language tag `python`). The entire block will be copied verbatim into `scripts/generated_schematic.py`, so include imports, constants, helpers, `SCHEMATIC_META`, and `build_schematic`.
3. A verification checklist (bullet list) confirming bounding box, block palettes, redstone connectivity, and orientation.

No extra commentary, no TODOs, no pseudocode outside the code block.

### 3. Required Module Structure
Produce the following exact outline, filling in real logic for the described build. Everything in this outline belongs inside the single code block that will be saved to `scripts/generated_schematic.py`.

```text
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple
from litemapy import BlockState, Region

SCHEMATIC_META = {
    "name": "...",
    "size": (X, Y, Z),
    "filename": "...",
}

@dataclass(frozen=True)
class BuildContext:
    region: Region
    origin: Tuple[int, int, int]
    size: Tuple[int, int, int]

def block(block_id: str, **props: str) -> BlockState: ...

def validate_request(ctx: BuildContext) -> None: ...

def place(ctx: BuildContext, x: int, y: int, z: int, state: BlockState) -> None: ...

def place_volume(ctx: BuildContext, *, start: Tuple[int,int,int], size: Tuple[int,int,int], state: BlockState) -> None: ...

def build_layers(ctx: BuildContext) -> None: ...

def wire_redstone(ctx: BuildContext) -> None: ...

def build_schematic(region: Region) -> None:
    ctx = BuildContext(region=region, origin=(0,0,0), size=(X,Y,Z))
    validate_request(ctx)
    build_layers(ctx)
    wire_redstone(ctx)
```

You may introduce extra helpers (e.g., `def place_line`, `def carve_air`, `def install_components`) but they must follow the same pattern: full type hints, docstrings, and explicit `ctx` usage. If you add new helpers, make sure they appear *above* `build_schematic` so that importing the module exposes a single public entry point. Always set `SCHEMATIC_META["size"]` to the exact `(x, y, z)` bounds used when creating `BuildContext`.

### 4. Python & API Expectations
- Only import: `__future__` annotations, `dataclasses`, `typing`, `math`/`itertools` if required, `litemapy`.
- Every function must have a docstring summarizing behavior, inputs, and side effects.
- Use `BlockState` via a dedicated `block()` helper that always injects full property maps (`{"facing": "north", "powered": "false"}`).
- Treat `BlockState` instances as truthy only when checking for `None`. Their `__bool__` reflects property count, so use `if block is not None:` instead of `if block:`.
- Provide constant dictionaries for block palettes, e.g. `BLOCKS = {"frame": block("minecraft:stone_bricks"), ...}`.
- All placement helpers must enforce bounds: raise `ValueError` if `(x, y, z)` falls outside `ctx.size`.
- When iterating volumes, use explicit loops (`for dx in range(size_x): ...`) so the user can audit coordinates.
- Explain axis conventions in comments: `# x=east (+), y=up (+), z=south (+)`.
- If the design includes moving parts (pistons, observers, hoppers), set every orientation/face property explicitly.

### 5. Region & Context Handling
- Document how `ctx.size` ties back to the described width/height/depth.
- Show how air padding is enforced (e.g., clear volume with `block("minecraft:air")` before placing components).
- When the design has sub-assemblies, offset them via derived anchors (`base = (ctx.origin[0] + 2, ...)`).
- Provide inline comments anytime a coordinate is non-obvious (e.g., `# place comparator one block north of input chest`).

### 6. Output & Integration Instructions
- After the code block, provide a numbered list with the exact steps to integrate:
  1. Create/overwrite `scripts/generated_schematic.py` with the code block contents.
  2. Update `SCHEMATIC_META["name"|"size"|"filename"]` inside `scripts/template_litematic_generator.py`.
  3. Run `python scripts/template_litematic_generator.py --output ./data/raw_schematics`.
  4. Open the resulting `.litematic` in Litematica to validate against the checklist.

### 7. Tone & Formatting
- Confident, instructional voice.
- Sentences ≤20 words when possible.
- Use ordered lists for procedures, bullets for attribute lists, and inline code for identifiers.

---

Return only the narrative overview, the single Python code block, the verification bullets, and the integration steps.

