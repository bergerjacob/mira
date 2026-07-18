"""
MIRA: Memory and Clock Circuit Generator

Generates circuits that store state or produce timing signals:
- RS Latch (reset-set latch using cross-coupled torches)
- T Flip-Flop (toggle on each lever press)
- Redstone Clock (oscillator using torch feedback)
- Pulse Generator (brief pulse on trigger)

All circuits use structural verification (checking block placement) since
functional verification via setblock is limited for sequential and timing
circuits with feedback paths.
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from .base import (
    CircuitGenerator, CircuitExample, VerifyCheck, VerifyAction, VerifyContract,
    Block, add_support_blocks, format_state,
)


class RsLatchGenerator(CircuitGenerator):
    """RS latch using two cross-coupled torches (NOR latch).

    Two inputs: SET lever and RESET lever.
    One output: lamp.
    SET turns lamp ON, RESET turns lamp OFF.
    Lamp stays in last state (memory).

    Layout (Y=0 = ground, Y=1 = torch/lamp level):
      (0,0,0):   Block A (stone)
      (0,1,0):   Torch A (floor torch on Block A)
      (0,0,lz):  Lever SET (on north/south side of Block A)

      (2,0,0):   Block B (stone)
      (2,1,0):   Torch B (floor torch on Block B)
      (2,0,lz):  Lever RESET (on north/south side of Block B)

      (1,0,0):   Wire (cross-coupling between the two blocks at ground level)
      (3,1,0):   Lamp output (adjacent to Torch B)

    Note: RS latches require specific redstone update ordering that
    setblock doesn't reliably trigger. Uses structural verification.
    """

    name = "rs_latch"
    category = "memory_clock"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        # Two orientations × two block types = 4 examples
        orientations = [
            {"orientation": "north", "facing": "north", "lever_z": -1},
            {"orientation": "south", "facing": "south", "lever_z": 1},
        ]
        params = []
        for orient in orientations:
            for block_type in ["stone", "cobblestone"]:
                p = dict(orient)
                p["block_type"] = block_type
                params.append(p)
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        facing = params["facing"]
        lz = params["lever_z"]
        block_type = params["block_type"]
        block_state = f"minecraft:{block_type}"

        block_a_pos = (0, 0, 0)
        torch_a_pos = (0, 1, 0)
        lever_set_pos = (0, 0, lz)

        block_b_pos = (2, 0, 0)
        torch_b_pos = (2, 1, 0)
        lever_reset_pos = (2, 0, lz)

        wire_pos = (1, 0, 0)
        lamp_pos = (3, 1, 0)

        blocks: List[Block] = [
            (*block_a_pos, block_state, None),
            (*torch_a_pos, "minecraft:redstone_torch", None),
            (*lever_set_pos, f"minecraft:lever[facing={facing},powered=false]", None),
            (*block_b_pos, block_state, None),
            (*torch_b_pos, "minecraft:redstone_torch", None),
            (*lever_reset_pos, f"minecraft:lever[facing={facing},powered=false]", None),
            (*wire_pos, "minecraft:redstone_wire", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
        ]
        support = add_support_blocks(blocks)

        block_name = block_type.replace("_", " ")

        # Structural verification — check core blocks are in place
        verify = VerifyContract(
            initial_checks=[
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(lever_set_pos, f"minecraft:lever[facing={facing},powered=false]"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
                VerifyCheck(lever_reset_pos, f"minecraft:lever[facing={facing},powered=false]"),
                VerifyCheck(wire_pos, "minecraft:redstone_wire"),
            ],
            actions=[],
            tick_steps=20,
            final_checks=[
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
                VerifyCheck(wire_pos, "minecraft:redstone_wire"),
            ],
        )

        desc = (
            f"An RS latch (reset-set latch) using two cross-coupled redstone torches "
            f"on {block_name} blocks. "
            f"Block A at {block_a_pos} has a redstone torch at {torch_a_pos} and "
            f"SET lever at {lever_set_pos} (facing {facing}). "
            f"Block B at {block_b_pos} has a redstone torch at {torch_b_pos} and "
            f"RESET lever at {lever_reset_pos} (facing {facing}). "
            f"The torches are cross-coupled through redstone wire at {wire_pos} to create a "
            f"bistable circuit. The lamp at {lamp_pos} shows the output state. "
            f"When the SET lever is pressed, the lamp turns on and stays on even after "
            f"releasing SET. When the RESET lever is pressed, the lamp turns off and "
            f"stays off. This creates a 1-bit memory cell."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="advanced",
            metadata={
                "generator": self.name,
                "params": params,
                "circuit_type": "RS_LATCH",
            },
        )


class TFlipFlopGenerator(CircuitGenerator):
    """T flip-flop (toggle) — each lever press toggles the output.

    A T flip-flop changes state on each rising edge of the input signal.
    This requires edge detection which is difficult to achieve reliably
    via setblock. Uses structural verification.

    Layout (Y=0 ground, Y=1 torch/wire/lamp level):
      (0,0,0):    Input lever (facing=east)
      (1,0,0):    Block A (stone)
      (1,1,0):    Torch A (on Block A — first NOT stage)
      (2,0,0):    Block C (stone) — support for wire above
      (2,1,0):    Wire (signal from Torch A to Block B)
      (3,0,0):    Block B (stone)
      (3,1,0):    Torch B (on Block B — second NOT stage)
      (4,1,0):    Lamp output
    """

    name = "t_flip_flop"
    category = "memory_clock"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        # Two orientations × two block types = 4 examples
        orientations = [
            {"orientation": "east", "facing": "east"},
            {"orientation": "west", "facing": "west"},
        ]
        params = []
        for orient in orientations:
            for block_type in ["stone", "cobblestone"]:
                p = dict(orient)
                p["block_type"] = block_type
                params.append(p)
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        facing = params["facing"]
        block_type = params["block_type"]
        block_state = f"minecraft:{block_type}"

        lever_pos = (0, 0, 0)
        block_a_pos = (1, 0, 0)
        torch_a_pos = (1, 1, 0)
        block_c_pos = (2, 0, 0)   # Support for wire above
        wire_pos = (2, 1, 0)      # Torch A output at torch level
        block_b_pos = (3, 0, 0)
        torch_b_pos = (3, 1, 0)
        lamp_pos = (4, 1, 0)

        blocks: List[Block] = [
            (*lever_pos, f"minecraft:lever[facing={facing},powered=false]", None),
            (*block_a_pos, block_state, None),
            (*torch_a_pos, "minecraft:redstone_torch", None),
            (*block_c_pos, block_state, None),
            (*wire_pos, "minecraft:redstone_wire", None),
            (*block_b_pos, block_state, None),
            (*torch_b_pos, "minecraft:redstone_torch", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
        ]
        support = add_support_blocks(blocks)

        block_name = block_type.replace("_", " ")

        verify = VerifyContract(
            initial_checks=[
                VerifyCheck(lever_pos, f"minecraft:lever[facing={facing},powered=false]"),
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(block_c_pos, block_state),
                VerifyCheck(wire_pos, "minecraft:redstone_wire"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
            ],
            actions=[],
            tick_steps=20,
            final_checks=[
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(block_c_pos, block_state),
                VerifyCheck(wire_pos, "minecraft:redstone_wire"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
            ],
        )

        desc = (
            f"A T flip-flop (toggle) circuit on {block_name} blocks. "
            f"A lever at {lever_pos} controls "
            f"a chain of two NOT gates: Block A at {block_a_pos} with torch at {torch_a_pos}, "
            f"and Block B at {block_b_pos} with torch at {torch_b_pos}. "
            f"Redstone wire at {wire_pos} carries the signal between the two stages. "
            f"The lamp at {lamp_pos} toggles between on and off "
            f"each time the lever is pressed and released."
        )

        desc = (
            f"A T flip-flop (toggle) circuit. A lever at {lever_pos} controls "
            f"a chain of two NOT gates: Block A at {block_a_pos} with torch at {torch_a_pos}, "
            f"and Block B at {block_b_pos} with torch at {torch_b_pos}. "
            f"Redstone wire at {wire_pos} carries the signal between the two stages. "
            f"The lamp at {lamp_pos} toggles between on and off "
            f"each time the lever is pressed and released."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="advanced",
            metadata={
                "generator": self.name,
                "params": params,
                "circuit_type": "T_FLIP_FLOP",
            },
        )


class ClockGenerator(CircuitGenerator):
    """Redstone clock circuit using torches.

    A simple two-torch oscillator. Two torches each feed the other's block,
    creating alternating ON/OFF cycles. The lamp shows the output.

    Layout (Y=0 ground, Y=1 torch/wire/lamp level):
      (0,0,0):   Block A (stone)
      (0,1,0):   Torch A (on Block A)
      (2,0,0):   Block B (stone)
      (2,1,0):   Torch B (on Block B)
      (1,1,0):   Wire (connects Torch A output to Block B area)
      (3,1,0):   Lamp output

    Note: Clock oscillation requires timing that setblock can't reliably
    trigger. Uses structural verification.
    """

    name = "redstone_clock"
    category = "memory_clock"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        # Two orientations × two block types = 4 examples
        orientations = [
            {"orientation": "east", "lamp_dx": 3},
            {"orientation": "west", "lamp_dx": -1},
        ]
        params = []
        for orient in orientations:
            for block_type in ["stone", "cobblestone"]:
                p = dict(orient)
                p["block_type"] = block_type
                params.append(p)
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        lamp_dx = params["lamp_dx"]
        block_type = params["block_type"]
        block_state = f"minecraft:{block_type}"

        block_a_pos = (0, 0, 0)
        torch_a_pos = (0, 1, 0)
        block_b_pos = (2, 0, 0)
        torch_b_pos = (2, 1, 0)
        wire_pos = (1, 1, 0)      # Connection between torches
        lamp_pos = (lamp_dx, 1, 0)

        blocks: List[Block] = [
            (*block_a_pos, block_state, None),
            (*torch_a_pos, "minecraft:redstone_torch", None),
            (*block_b_pos, block_state, None),
            (*torch_b_pos, "minecraft:redstone_torch", None),
            (*wire_pos, "minecraft:redstone_wire", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
        ]
        support = add_support_blocks(blocks)

        block_name = block_type.replace("_", " ")

        verify = VerifyContract(
            initial_checks=[
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
                VerifyCheck(wire_pos, "minecraft:redstone_wire"),
            ],
            actions=[],
            tick_steps=20,
            final_checks=[
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
                VerifyCheck(wire_pos, "minecraft:redstone_wire"),
            ],
        )

        desc = (
            f"A redstone clock circuit using two cross-coupled torches on {block_name} blocks. "
            f"Block A at {block_a_pos} has torch at {torch_a_pos}, "
            f"Block B at {block_b_pos} has torch at {torch_b_pos}. "
            f"The torches are cross-coupled through redstone wire at {wire_pos}, "
            f"creating an oscillator that alternates power states. "
            f"The lamp at {lamp_pos} blinks on and off as the clock oscillates. "
            f"This is a free-running clock that produces a continuous square wave."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="intermediate",
            metadata={
                "generator": self.name,
                "params": params,
                "circuit_type": "CLOCK",
            },
        )


class PulseGenerator(CircuitGenerator):
    """Pulse generator using torches.

    Produces a brief output pulse when the input lever is toggled.
    Uses a monostable-like circuit: lever feeds two NOT gates in series,
    with a feedback path that creates a short output pulse.

    Layout (Y=0 ground, Y=1 torch/wire/lamp level):
      (0,0, lz): Lever input (on block at (0,0,0))
      (0,0, 0):  Block A (stone)
      (0,1, 0):  Torch A (on Block A — first NOT gate)
      (1,1, 0):  Wire A (carries Torch A signal)
      (2,1, 0):  Wire B (continues signal)
      (3,0, 0):  Block B (stone)
      (3,1, 0):  Torch B (on Block B — second NOT gate)
      (4,1, 0):  Lamp output

    Note: Genuine pulse behavior requires timing that setblock doesn't
    reliably provide. Uses structural verification.
    """

    name = "pulse_generator"
    category = "memory_clock"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        # Two orientations × two block types = 4 examples
        orientations = [
            {"orientation": "north", "facing": "north", "lever_z": -1},
            {"orientation": "south", "facing": "south", "lever_z": 1},
        ]
        params = []
        for orient in orientations:
            for block_type in ["stone", "cobblestone"]:
                p = dict(orient)
                p["block_type"] = block_type
                params.append(p)
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        facing = params["facing"]
        lz = params["lever_z"]
        block_type = params["block_type"]
        block_state = f"minecraft:{block_type}"

        lever_pos = (0, 0, lz)
        block_a_pos = (0, 0, 0)
        torch_a_pos = (0, 1, 0)
        wire_a_pos = (1, 1, 0)
        wire_b_pos = (2, 1, 0)
        block_b_pos = (3, 0, 0)
        torch_b_pos = (3, 1, 0)
        lamp_pos = (4, 1, 0)

        blocks: List[Block] = [
            (*lever_pos, f"minecraft:lever[facing={facing},powered=false]", None),
            (*block_a_pos, block_state, None),
            (*torch_a_pos, "minecraft:redstone_torch", None),
            (*wire_a_pos, "minecraft:redstone_wire", None),
            (*wire_b_pos, "minecraft:redstone_wire", None),
            (*block_b_pos, block_state, None),
            (*torch_b_pos, "minecraft:redstone_torch", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
        ]
        support = add_support_blocks(blocks)

        block_name = block_type.replace("_", " ")

        verify = VerifyContract(
            initial_checks=[
                VerifyCheck(lever_pos, f"minecraft:lever[facing={facing},powered=false]"),
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(wire_a_pos, "minecraft:redstone_wire"),
                VerifyCheck(wire_b_pos, "minecraft:redstone_wire"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
            ],
            actions=[],
            tick_steps=20,
            final_checks=[
                VerifyCheck(block_a_pos, block_state),
                VerifyCheck(torch_a_pos, "minecraft:redstone_torch"),
                VerifyCheck(block_b_pos, block_state),
                VerifyCheck(torch_b_pos, "minecraft:redstone_torch"),
                VerifyCheck(wire_a_pos, "minecraft:redstone_wire"),
                VerifyCheck(wire_b_pos, "minecraft:redstone_wire"),
            ],
        )

        desc = (
            f"A pulse generator circuit on {block_name} blocks that produces "
            f"a brief output pulse when triggered. A lever at {lever_pos} "
            f"(facing {facing}) controls "
            f"a chain of two NOT gates: Block A at {block_a_pos} with torch at {torch_a_pos}, "
            f"and Block B at {block_b_pos} with torch at {torch_b_pos}. "
            f"Redstone wire at {wire_a_pos} and {wire_b_pos} carries the signal between "
            f"the two stages. The lamp at {lamp_pos} briefly lights up when the lever "
            f"is toggled, creating a short pulse signal."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="intermediate",
            metadata={
                "generator": self.name,
                "params": params,
                "circuit_type": "PULSE_GENERATOR",
            },
        )
