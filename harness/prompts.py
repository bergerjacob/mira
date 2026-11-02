"""Prompt text for the two arms. Everything the model is told lives here (and is reproduced in docs/HARNESS.md).

The task prompt is `Contract.describe(public=True)` (description, world/build boxes, fixed blocks, ports with
positions/facings, public example vectors, the repeater/comparator/torch `facing` gotcha) plus the sections below.
Hidden vectors, seeds and the reference solution are never part of any prompt.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDER_SYSTEM_PATH = ROOT / "evaluation" / "prompts" / "builder_system.md"

# physics gotchas (docs/ENV.md "Simulator caveats" + the facts every task text already states)
PHYSICS = """\
## Redstone facts this simulator enforces
- Coordinates: x east, y up, z south (north = -z). All coordinates are absolute task coordinates.
- Repeater and comparator `facing` is the side the signal comes FROM; the output is on the opposite side. A repeater that must
  carry a signal eastward is `minecraft:repeater[facing=west,delay=1]`. `delay` is 1..4 (each step = 2 game ticks).
- Wall torch, wall lever and button `facing` points AWAY from the block they are attached to: `redstone_wall_torch[facing=east]`
  hangs on the block to its west. `lever` defaults to face=wall; for a lever on the floor use `lever[face=floor,facing=east]`.
- Dust, repeaters, comparators, floor torches and floor levers need a solid supporting block below them; a component without
  support is rejected as "floating" (pop-off). Place redstone dust as bare `minecraft:redstone_wire` (the simulator derives the
  connections; stated connection properties break them).
- Dust carries power 15 and loses 1 per block: put a repeater at least every 14 dust blocks. In this simulator dust powers a
  solid block only when a straight dust line ends pointing into it; a lever, repeater output or torch powers the block it is
  attached to / faces.
- A redstone torch is lit by default and switches OFF while the block it is attached to is powered (NOT gate). A torch powers dust
  beside it and, for a floor torch, the block above it.
- Comparator in subtract mode outputs rear - max(side inputs). Feed side inputs from dust, repeaters, comparators or redstone blocks
  (do not rely on a lever beside a comparator: the simulator and the real game disagree). Dust strength matters there; a
  repeater restores a weak signal to 15.
- A repeater is locked while a repeater/comparator points into its side. Observers: `facing` is the direction they watch, the
  pulse leaves from the back. Pistons: `facing` is the push direction (sticky pistons pull back).
- Stone and smooth_stone conduct power; glass does not. A redstone_block is a constant power source.
- Time is in game ticks (20 = 1 s). Specs are checked with inputs applied in random order WITHOUT resetting the circuit between
  vectors, so stale state (latches, pulses still running) can make a build fail; hidden vectors of the same kind are also checked.
"""

PALETTE = ("Typical palette: stone / smooth_stone (solid, conducts), glass (does not conduct), redstone_wire, repeater, comparator, "
           "redstone_torch / redstone_wall_torch, redstone_block, lever, stone_button, observer, piston, sticky_piston, redstone_lamp, "
           "target, slab. Not every block can be simulated; the tools tell you when one is refused.")

# Worked examples for the blocks arm: the SAME three circuits as the worked examples in evaluation/prompts/builder_system.md
# (lamp, NOT gate, AND from three inverters), written as tool calls instead of builder code, so the two arms differ in
# representation only. tests/harness/test_prompts.py compiles both sets and asserts that they place identical blocks.
BLOCKS_EXAMPLES = """\
## Worked examples
Each of these is a valid sequence of tool calls, and the simulator confirms the stated behaviour. The coordinates belong to the
example; in a task use the task's own coordinates and skip blocks the task already fixes.

### 1. Lever -> wire -> lamp
The lamp is lit exactly when the lever is on.
```
fill(0,0,0, 6,0,0, "minecraft:stone")                                   # floor
set_blocks([[0,1,0,"minecraft:lever[face=floor,facing=east]"], [6,1,0,"minecraft:redstone_lamp"]])
fill(1,1,0, 5,1,0, "minecraft:redstone_wire")
```

### 2. NOT gate
The lamp is lit exactly when the lever is OFF. The lever powers a block; a torch on that block inverts it.
```
fill(0,0,0, 6,0,0, "minecraft:stone")                                   # floor under everything
set_blocks([[1,1,0,"minecraft:stone"]])                                 # block the lever powers
set_blocks([[0,1,0,"minecraft:lever[face=wall,facing=west]"]])          # sticks out west, mounted on (1,1,0)
set_blocks([[2,1,0,"minecraft:redstone_wall_torch[facing=east]"]])      # sticks out east, mounted on (1,1,0)
fill(3,1,0, 5,1,0, "minecraft:redstone_wire")
set_blocks([[6,1,0,"minecraft:redstone_lamp"]])
```

### 3. AND gate from three inverters (NOR of NOTs)
Levers `A` and `B` are at (-1,1,0) and (-1,1,4), the lamp `Q` is at (8,1,2); `Q` is lit only when both levers are on. An inverter is
floor, dust, stone, wall torch, dust in a row along x; it is written out three times (z=0, z=4, and z=2 shifted east). The dust column
at x=3 joins the two inverter outputs (an OR of the inverted inputs) and feeds the third inverter.
```
# inverter A (input dust at (0,1,0), output dust at (3,1,0))
fill(0,0,0, 3,0,0, "minecraft:stone")
set_blocks([[0,1,0,"minecraft:redstone_wire"], [1,1,0,"minecraft:stone"], [2,1,0,"minecraft:redstone_wall_torch[facing=east]"], [3,1,0,"minecraft:redstone_wire"]])
# inverter B (same, 4 blocks south)
fill(0,0,4, 3,0,4, "minecraft:stone")
set_blocks([[0,1,4,"minecraft:redstone_wire"], [1,1,4,"minecraft:stone"], [2,1,4,"minecraft:redstone_wall_torch[facing=east]"], [3,1,4,"minecraft:redstone_wire"]])
# OR of the inverted inputs: a dust column on stone between the two outputs
fill(3,0,1, 3,0,3, "minecraft:stone")
fill(3,1,1, 3,1,3, "minecraft:redstone_wire")
# output inverter: NOT(OR(NOT a, NOT b)) = a AND b; its input dust (4,1,2) touches the column
fill(4,0,2, 7,0,2, "minecraft:stone")
set_blocks([[4,1,2,"minecraft:redstone_wire"], [5,1,2,"minecraft:stone"], [6,1,2,"minecraft:redstone_wall_torch[facing=east]"], [7,1,2,"minecraft:redstone_wire"]])
# inputs and output
set_blocks([[-1,0,0,"minecraft:stone"], [-1,0,4,"minecraft:stone"]])
set_blocks([[-1,1,0,"minecraft:lever[face=floor,facing=east]"], [-1,1,4,"minecraft:lever[face=floor,facing=east]"]])
set_blocks([[8,0,2,"minecraft:stone"], [8,1,2,"minecraft:redstone_lamp"]])
```

"""

def _blocks_system(examples: bool = True) -> str:
    return BLOCKS_SYSTEM_TEMPLATE.replace("@@EXAMPLES@@", BLOCKS_EXAMPLES if examples else "")


BLOCKS_SYSTEM_TEMPLATE = f"""\
You are an expert Minecraft redstone engineer. You build redstone contraptions in a deterministic simulator of vanilla Minecraft
Java redstone by calling tools. A task gives you fixed devices (levers, lamps, pistons...), named ports, a build box and the
behaviour to meet. You never write the test; you place blocks, and your build is scored on the stated behaviour (public example
vectors plus hidden vectors of the same kind), on structural validity and on size.

## Tools
Call tools; do not describe a build in prose only. The build is whatever blocks you currently have placed.
- set_blocks(blocks): [[x,y,z,"minecraft:block[prop=value]"], ...]. Air removes a block. Invalid states, cells outside the build box
  and fixed cells are rejected with a message (the rest of the call is applied).
- fill(x1,y1,z1,x2,y2,z2,state) / remove(x1,y1,z1[,x2,y2,z2]): box edits.
- view(layer=N, axis="y"): ASCII slice of the world (fixed blocks + your blocks), with a legend. Static: it shows block states, not
  signal levels.
- run_spec(): (only when offered) simulate the build against the PUBLIC vectors and explain failures with waveforms.
- submit(): final scoring against all vectors, hidden ones included. It ends the episode. Submit when you are done or out of options;
  an episode that ends without submit is scored as it stands.

{PHYSICS}
@@EXAMPLES@@## Working style
Plan the signal path first (source -> transmission -> logic -> output), then write coordinates. Use few large set_blocks/fill calls
rather than many tiny ones. Smaller, simpler builds score slightly higher once they are correct.
"""

BUILDER_APPENDIX = f"""\

---
# Harness protocol (this overrides "Reply with ONE fenced python block" above)

You are not replying with a code block. You pass the complete program as the `source` argument of the **build** tool. Each build()
call REPLACES the current build with the program's output. If the program has errors it is not loaded (the previous build, if any,
stays) and every error is returned with its line number.

- Coordinates in your program are the absolute task coordinates listed in the task (not relative to anything). Only the build box
  may be written.
- Fixed blocks (floor, levers, lamps, pistons given in the task) already exist in the world. Do not place them again (an identical
  block is ignored, a different block on a fixed cell is an error).
- Tools: build(source), view(layer, axis) for a static ASCII slice, run_spec() (only when offered) to simulate the current build against
  the PUBLIC vectors, submit() for final scoring against all vectors including hidden ones (ends the episode). An episode that ends
  without submit is scored as it stands.
- Block states are checked against Minecraft 1.21.0 names; the simulator refuses a few loadable-looking blocks (the tools say which).

{PHYSICS}"""


def builder_system() -> str:
    return BUILDER_SYSTEM_PATH.read_text() + BUILDER_APPENDIX


BLOCKS_SYSTEM = _blocks_system(True)
BLOCKS_SYSTEM_NO_EXAMPLES = _blocks_system(False)     # the round-1 grid's blocks prompt, byte for byte


def system_prompt(arm: str, blocks_examples: bool = True) -> str:
    if arm == "blocks":
        return BLOCKS_SYSTEM if blocks_examples else BLOCKS_SYSTEM_NO_EXAMPLES
    if arm == "builder":
        return builder_system()
    raise ValueError(f"unknown arm {arm!r}")


def feedback_section(rounds: int) -> str:
    if rounds <= 0:
        return ("## Feedback\nThis is a ONE-SHOT episode: there is no simulator feedback. You may inspect your layout with view(), "
                "then you must submit() your first complete build. You cannot run the specification before submitting.")
    return (f"## Feedback\nYou may call run_spec() at most {rounds} time{'s' if rounds != 1 else ''}. Each call simulates your current "
            f"build against the public vectors and returns diagnostics (first divergence, waveforms, unreached ports). "
            f"After that you must refine from your own reasoning and submit(). Spend the rounds wisely: build a complete candidate "
            f"first, then iterate on the reported failures.")


def task_prompt(task, arm: str, rounds: int, max_turns: int, palette_hint: bool = True) -> str:
    c = task.contract
    parts = [c.describe(public=True), ""]
    parts.append(f"Budget: at most {c.budgets.max_blocks} blocks of your own; at most {max_turns} assistant turns "
                 f"(each tool-calling reply is one turn).")
    if palette_hint:
        extra = f" Task hints: {' '.join(c.hints)}" if c.hints else ""
        parts.append(PALETTE + extra)
    parts.append("")
    parts.append(feedback_section(rounds))
    parts.append("")
    if arm == "blocks":
        parts.append('Example set_blocks argument: [[1,1,1,"minecraft:redstone_wire"],[2,1,1,"minecraft:repeater[facing=west,delay=1]"]]')
    else:
        parts.append("Write the program with build(source). Example statements inside build(b): "
                     "b.set((1,1,1), wire()); b.fill((2,1,1),(4,1,1), wire()); b.set((5,1,1), repeater(flow=EAST, delay=1)).")
    return "\n".join(parts)
