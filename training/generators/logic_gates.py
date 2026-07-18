"""
MIRA: Logic Gate Circuit Generator

Generates torch-based logic gate circuits. All circuits use floor-mounted
redstone torches (facing=up) since wall-mounted torches don't power
adjacent blocks via setblock.

Supported gates:
- NOT gate (inverter): lever → stone → torch → lamp
- OR gate: two levers → wire → lamp (lamp on if either lever on)
- AND gate: two levers → two input torches → output torch → lamp
  (lamp on only when both levers on)

Key constraint: levers must be on the SIDE of blocks (not top) to avoid
conflict with torches on top.
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from .base import (
    CircuitGenerator, CircuitExample, VerifyCheck, VerifyAction, VerifyContract,
    Block, add_support_blocks, format_state,
)


class NotGate(CircuitGenerator):
    """NOT gate: lever → stone → torch (inverts signal) → lamp.

    Layout (Y=0 is ground level):
      (-1,0,0): Lever (on north side of stone, facing=north)
      (0,0,0):  Stone block
      (0,1,0):  Redstone torch (on top of stone)
      (1,1,0):  Redstone lamp (next to torch)

    Truth table:
      Lever OFF → Torch ON → Lamp ON
      Lever ON  → Torch OFF → Lamp OFF
    """

    name = "not_gate"
    category = "logic_gate"

    DIRECTIONS = [
        ("north", 0, 0, -1, 1, 0, 0),   # lever north, lamp east
        ("south", 0, 0, 1, 1, 0, 0),    # lever south, lamp east
        ("east", 1, 0, 0, 0, 0, -1),    # lever east, lamp north
        ("west", -1, 0, 0, 0, 0, -1),   # lever west, lamp north
    ]

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for direction, lx, ly, lz, lamp_dx, lamp_dy, lamp_dz in self.DIRECTIONS:
            params.append({
                "direction": direction,
                "lever_offset": (lx, ly, lz),
                "lamp_offset": (lamp_dx, lamp_dy, lamp_dz),
            })
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        direction = params["direction"]
        lx, ly, lz = params["lever_offset"]
        lamp_dx, lamp_dy, lamp_dz = params["lamp_offset"]

        lever_pos = (lx, ly, lz)
        stone_pos = (0, 0, 0)
        torch_pos = (0, 1, 0)
        lamp_pos = (lamp_dx, lamp_dy + 1, lamp_dz)  # same Y as torch

        blocks: List[Block] = [
            (*lever_pos, f"minecraft:lever[facing={direction},powered=false]", None),
            (*stone_pos, "minecraft:stone", None),
            (*torch_pos, "minecraft:redstone_torch", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
        ]
        support = add_support_blocks(blocks)

        verify = VerifyContract(
            initial_checks=[
                # Lever OFF → torch ON → lamp ON
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=true]"),
            ],
            actions=[
                # Power the lever
                VerifyAction(lever_pos, f"minecraft:lever[facing={direction},powered=true]"),
            ],
            tick_steps=20,
            final_checks=[
                # Lever ON → torch OFF → lamp OFF
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
            ],
            negative_checks=[
                # Unpower lever → torch ON → lamp ON
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=true]"),
            ],
        )
        desc = (f"A NOT gate (inverter) circuit. A lever at {lever_pos} controls a redstone "
                f"torch on top of a stone block at {stone_pos}. The torch powers a redstone "
                f"lamp at {lamp_pos}. When the lever is off, the torch is on and the lamp is lit. "
                f"When the lever is on, the torch turns off and the lamp goes dark. "
                f"This inverts the signal: output is the opposite of input.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="beginner",
            metadata={"generator": self.name, "params": params, "gate_type": "NOT"},
        )


class OrGate(CircuitGenerator):
    """OR gate: two levers → wire → lamp (lamp on if either lever on).

    Layout:
      (0,0,0):  Lever A (facing=east)
      (1,0,0):  Redstone wire
      (2,0,0):  Redstone wire (junction)
      (3,0,0):  Redstone lamp
      (2,0,1):  Redstone wire (branch south)
      (2,0,2):  Lever B (facing=east, on south side of wire)

    Actually simpler: both levers on same wire.
      (0,0,0):  Lever A (facing=east)
      (1,0,0):  Wire
      (2,0,0):  Wire
      (3,0,0):  Lamp
      (4,0,0):  Wire
      (5,0,0):  Lever B (facing=west)

    Wait, levers don't sit on wire. Let me use:
      (0,0,0):  Lever A (facing=east) on stone support
      (1,0,0):  Wire
      (2,0,0):  Wire
      (3,0,0):  Lamp
      (4,0,0):  Wire
      (5,0,0):  Lever B (facing=west) on stone support
    """

    name = "or_gate"
    category = "logic_gate"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        # Just one variant for now — can add orientations later
        return [{}]

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        lever_a_pos = (0, 0, 0)
        wire1_pos = (1, 0, 0)
        wire2_pos = (2, 0, 0)
        lamp_pos = (3, 0, 0)
        wire3_pos = (4, 0, 0)
        lever_b_pos = (5, 0, 0)

        blocks: List[Block] = [
            (*lever_a_pos, "minecraft:lever[facing=east,powered=false]", None),
            (*wire1_pos, "minecraft:redstone_wire", None),
            (*wire2_pos, "minecraft:redstone_wire", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
            (*wire3_pos, "minecraft:redstone_wire", None),
            (*lever_b_pos, "minecraft:lever[facing=west,powered=false]", None),
        ]
        support = add_support_blocks(blocks)

        verify = VerifyContract(
            initial_checks=[
                # Both levers OFF → lamp OFF
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
            ],
            actions=[
                # Power lever A only
                VerifyAction(lever_a_pos, "minecraft:lever[facing=east,powered=true]"),
            ],
            tick_steps=20,
            final_checks=[
                # Either lever ON → lamp ON
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=true]"),
            ],
            negative_checks=[
                # Unpower A, lamp should go OFF
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
            ],
        )
        desc = (f"An OR gate circuit. Two levers at {lever_a_pos} and {lever_b_pos} "
                f"are connected to a redstone wire line with a lamp at {lamp_pos} in the middle. "
                f"The lamp turns on when either lever is powered. "
                f"Truth table: OFF+OFF=OFF, ON+OFF=ON, OFF+ON=ON, ON+ON=ON.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="intermediate",
            metadata={"generator": self.name, "params": params, "gate_type": "OR"},
        )


class AndGate(CircuitGenerator):
    """AND gate: two levers → two NOT-torches → wire AND → output torch → lamp.

    Uses two NOT gates whose torch outputs feed into a wire that powers a
    third block with an output torch. The output torch is OFF when either
    input torch is ON (i.e., when either lever is OFF).

    Layout (Y=0 is ground, Y=1 is torch level):
      Lever A at (0,0,-1) facing=north, on side of Block A at (0,0,0)
      Torch A at (0,1,0) on top of Block A (inverts lever A)
      Wire at (1,1,0) connecting Torch A to Block C
      Block C at (2,1,0) — receives signal from both wires
      Wire at (3,1,0) connecting Torch B to Block C
      Torch B at (4,1,0) on top of Block B (inverts lever B)
      Block B at (4,0,0)
      Lever B at (4,0,-1) facing=north, on side of Block B
      Output Torch at (2,2,0) on top of Block C
      Lamp at (3,2,0) next to Output Torch

    Wait — this still has the same problem. Torches power adjacent blocks
    via redstone signal, but a torch on top of a block doesn't power the
    block it's on. The torch powers blocks ADJACENT to it (north/south/east/west).

    Revised approach: use a wire that connects both torch outputs to a
    single block, which has the output torch on top.

    Layout:
      Block A at (0,0,0), Lever A at (0,0,-1) facing=north, Torch A at (0,1,0)
      Wire at (1,1,0) — powered by Torch A (adjacent east)
      Block C at (2,1,0) — wait, wire can't be on top of a block...

    Simpler: just use OR gate + NOT gate (De Morgan's: A AND B = NOT(NOT A OR NOT B))
    But that's complex. Let me use a different AND topology:

    Two levers each power a wire. Both wires meet at a block. The block has
    a torch on top. The torch is ON only when the block is NOT powered.
    The block is powered when EITHER wire is powered (so torch is OFF when
    either lever is ON). That's actually a NAND gate.

    For a proper AND gate, we need: torch OFF when block powered, torch ON
    when block NOT powered. Block powered when BOTH wires powered.

    The issue: redstone wire doesn't have "AND" semantics. Two wires meeting
    at a block = OR (block powered if either wire is powered).

    Real AND gate in Minecraft uses torches on sides of blocks, with the
    output torch on a block that's powered only when both input torches
    are off. This requires specific block placement.

    Let me use the simplest working AND gate: two NOT gates whose outputs
    feed into an OR gate, then a final NOT gate (De Morgan's law).

    Actually, the simplest correct AND gate uses this topology:
    - Two input blocks, each with a lever on side and torch on top
    - Both torches are OFF when their lever is ON
    - Both torches power a wire that goes to a third block
    - Third block has a torch on top (output torch)
    - Output torch is ON when third block is NOT powered
    - Third block is powered when EITHER torch is ON (i.e., when either lever is OFF)
    - So output torch is ON only when BOTH torches are OFF (both levers ON)

    This is actually a NAND-NAND implementation which equals AND.
    """

    name = "and_gate"
    category = "logic_gate"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        return [{}]

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        # Layout:
        # Block A at (0,0,0), Lever A at (0,0,-1) facing=north, Torch A at (0,1,0)
        # Block B at (4,0,0), Lever B at (4,0,-1) facing=north, Torch B at (4,1,0)
        # Wire at (1,1,0), (2,1,0), (3,1,0) connecting torches to Block C
        # Block C at (2,1,0) — wait, wire can't be on top of block C
        #
        # Let me use: torches at (0,1,0) and (4,1,0), wire at (1,1,0), (2,1,0), (3,1,0)
        # Block C at (2,0,0) with output torch at (2,1,0) — but wire is at (2,1,0)
        # Conflict. Let me put Block C at (2,0,0), wire at (2,1,0) on top of it,
        # output torch at (2,2,0).
        # Wire at (1,1,0) connects Torch A to wire at (2,1,0)
        # Wire at (3,1,0) connects Torch B to wire at (2,1,0)
        # Block C at (2,0,0) supports the wire at (2,1,0)
        # Output torch at (2,2,0) on top of wire? No, torch can't be on wire.
        #
        # OK, let me use a cleaner layout:
        # Torch A at (0,1,0) on Block A at (0,0,0), Lever A at (0,0,-1)
        # Torch B at (4,1,0) on Block B at (4,0,0), Lever B at (4,0,-1)
        # Wire at (1,1,0), (2,1,0), (3,1,0) — connects torches
        # Block C at (2,0,0) — supports wire at (2,1,0)
        # Output torch at (2,2,0) — but this is on top of wire, not a block
        #
        # The output torch needs to be on a solid block. Let me put it on
        # Block C at (2,0,0), but then wire can't be at (2,1,0).
        #
        # Final layout:
        # Torch A at (0,1,0), Torch B at (4,1,0)
        # Wire at (1,1,0), (2,1,0), (3,1,0)
        # Block D at (2,2,0) — floating? No, needs support.
        # Block D at (2,2,0) supported by... nothing. Let me put it at (2,1,1)
        # and have wire at (2,1,0) power it from the side.
        #
        # Actually, the simplest approach: the wire at (2,1,0) powers an
        # adjacent block. Let me put Block D at (2,2,0) with a support block
        # at (2,1,0) — but that's where the wire is.
        #
        # I'll use a different approach: the wire powers a lamp directly.
        # Wire at (2,1,0) is powered when either torch is ON.
        # Lamp at (2,2,0) — but wire doesn't power blocks above it.
        # Lamp at (2,1,1) — wire powers adjacent blocks on X axis only.
        #
        # This is getting complicated. Let me just use the OR gate + NOT gate
        # approach: OR the two inverted signals, then invert again.
        # A AND B = NOT(NOT A OR NOT B)

        # Simplest correct AND gate:
        # - Two NOT gates (lever → block → torch)
        # - Both torches power a wire
        # - Wire powers a block with an output torch (inverts the OR)
        # - Output torch powers a lamp

        lever_a_pos = (0, 0, -1)
        block_a_pos = (0, 0, 0)
        torch_a_pos = (0, 1, 0)
        lever_b_pos = (4, 0, -1)
        block_b_pos = (4, 0, 0)
        torch_b_pos = (4, 1, 0)
        # Wire connecting torches
        wire1_pos = (1, 1, 0)
        wire2_pos = (2, 1, 0)
        wire3_pos = (3, 1, 0)
        # Block C supports wire2 and has output torch on top
        # Wait, can't have both. Let me put output torch on a separate block.
        # Block C at (2, 0, 1) — south of wire2, powered by wire2 (X axis)
        # Output torch at (2, 1, 1) on top of Block C
        # Lamp at (3, 1, 1) next to output torch
        block_c_pos = (2, 0, 1)
        out_torch_pos = (2, 1, 1)
        lamp_pos = (3, 1, 1)

        blocks: List[Block] = [
            (*lever_a_pos, "minecraft:lever[facing=north,powered=false]", None),
            (*block_a_pos, "minecraft:stone", None),
            (*torch_a_pos, "minecraft:redstone_torch", None),
            (*lever_b_pos, "minecraft:lever[facing=north,powered=false]", None),
            (*block_b_pos, "minecraft:stone", None),
            (*torch_b_pos, "minecraft:redstone_torch", None),
            (*wire1_pos, "minecraft:redstone_wire", None),
            (*wire2_pos, "minecraft:redstone_wire", None),
            (*wire3_pos, "minecraft:redstone_wire", None),
            (*block_c_pos, "minecraft:stone", None),
            (*out_torch_pos, "minecraft:redstone_torch", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
        ]

        verify = VerifyContract(
            initial_checks=[
                # Both levers OFF → both torches ON → wire powered → Block C powered → OutTorch OFF → Lamp OFF
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
            ],
            actions=[
                # Power BOTH levers
                VerifyAction(lever_a_pos, "minecraft:lever[facing=north,powered=true]"),
                VerifyAction(lever_b_pos, "minecraft:lever[facing=north,powered=true]"),
            ],
            tick_steps=20,
            final_checks=[
                # Both levers ON → both torches OFF → wire NOT powered → Block C NOT powered → OutTorch ON → Lamp ON
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=true]"),
            ],
            negative_checks=[
                # Unpower both → lamp OFF
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
            ],
        )
        desc = (f"An AND gate circuit using redstone torches (NAND-NAND implementation). "
                f"Two levers at {lever_a_pos} and {lever_b_pos} each control a redstone torch "
                f"that inverts their signal. Both torches power a redstone wire that feeds into "
                f"a central stone block. An output torch on top of the central block inverts the "
                f"combined signal and powers a lamp. The lamp turns on only when BOTH levers are "
                f"powered (both input torches turn off, unpowering the central block, allowing "
                f"the output torch to light). Truth table: OFF+OFF=OFF, ON+OFF=OFF, OFF+ON=OFF, ON+ON=ON.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=[],
            verify_contract=verify,
            category=self.category,
            difficulty="advanced",
            metadata={"generator": self.name, "params": params, "gate_type": "AND"},
        )
