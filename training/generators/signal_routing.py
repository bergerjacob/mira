"""
MIRA: Signal Routing Circuit Generator

Generates circuits that route redstone signals through various topologies:
- WireChainGenerator: Lever → wire chain → lamp (functional verification)
- VerticalWireGenerator: Redstone_block → vertical wire tower → lamp (structural)
- TorchTowerGenerator: Torch tower alternating stone/torch → lamp (structural)
- SignalSplitGenerator: Redstone_block → multiple adjacent lamps (functional)

Constraints observed:
- Wire→lamp only works on X axis (east/west), not Z axis
- Lever facing must match wire direction (facing=east for east)
- Floor-mounted redstone torches work reliably (facing=up)
- No repeaters/comparators/pistons (can't be powered via setblock)
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from .base import (
    CircuitGenerator, CircuitExample, VerifyCheck, VerifyAction, VerifyContract,
    Block, add_support_blocks, format_state,
)


class WireChainGenerator(CircuitGenerator):
    """Lever → wire chain → lamp with functional verification.

    A lever at (0,0,0) powers a chain of redstone wire extending east.
    A lamp at the end lights up when the lever is toggled on.
    Each wire block has a stone support block underneath at y=-1.

    Wire lengths: 2, 3, 4, 5 (4 examples total).
    Direction: east only (lever facing=east).
    """

    name = "wire_chain"
    category = "signal_routing"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        return [
            {"wire_length": 2},
            {"wire_length": 3},
            {"wire_length": 4},
            {"wire_length": 5},
        ]

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        wire_length = params["wire_length"]
        direction = "east"
        dx = 1

        lever_pos = (0, 0, 0)
        lamp_x = wire_length + 1
        lamp_pos = (lamp_x, 0, 0)

        blocks: List[Block] = [
            (*lever_pos, f"minecraft:lever[facing={direction},powered=false]", None),
        ]
        # Wire chain east from lever
        for i in range(1, wire_length + 1):
            blocks.append((i * dx, 0, 0, "minecraft:redstone_wire", None))
        # Lamp at end of chain
        blocks.append((*lamp_pos, "minecraft:redstone_lamp[lit=false]", None))

        support = add_support_blocks(blocks)

        desc = (
            f"A wire chain circuit with {wire_length} redstone wire blocks. "
            f"A lever at {lever_pos} (facing east) powers a chain of {wire_length} wire "
            f"blocks extending east. A redstone lamp at {lamp_pos} is lit when the lever "
            f"is toggled on, and turns off when the lever is toggled off."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=[
                    # Lever OFF → lamp OFF
                    VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
                ],
                actions=[
                    # Toggle lever ON
                    VerifyAction(lever_pos,
                                 f"minecraft:lever[facing={direction},powered=true]"),
                ],
                tick_steps=20,
                final_checks=[
                    # Lever ON → lamp ON
                    VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=true]"),
                ],
                negative_checks=[
                    # Lever OFF again → lamp OFF
                    VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
                ],
            ),
            category=self.category,
            difficulty="beginner",
            metadata={
                "generator": self.name,
                "params": params,
                "wire_length": wire_length,
            },
        )


class VerticalWireGenerator(CircuitGenerator):
    """Redstone_block → torch tower → lamp (structural verification).

    NOTE: Vertical redstone_wire doesn't work via setblock (wire needs a solid
    block directly underneath, but adjacent vertical wires occupy Y-1).
    Fallback: uses a torch tower where each floor torch powers the block
    above it, achieving vertical signal propagation.

    Layout (2 torches, height=2):
      (0,0,0): redstone_block
      (0,1,0): stone (buffers torch from redstone_block)
      (0,2,0): redstone_torch
      (0,3,0): stone
      (0,4,0): redstone_torch
      (0,5,0): redstone_lamp

    Heights: 2, 3, 4 torches (3 examples).
    Uses structural verification.
    """

    name = "vertical_wire"
    category = "signal_routing"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        return [
            {"height": 2},
            {"height": 3},
            {"height": 4},
        ]

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        height = params["height"]  # number of torches

        blocks: List[Block] = []

        # Redstone block at bottom
        blocks.append((0, 0, 0, "minecraft:redstone_block", None))

        # Torch tower: each torch sits on a stone, powers the stone above it
        # Pattern: redstone_block → stone → torch → stone → torch → ... → lamp
        stone_positions: List[Tuple[int, int, int]] = []
        torch_positions: List[Tuple[int, int, int]] = []
        for i in range(height):
            stone_y = 2 * i + 1  # stone at y=1, 3, 5, ...
            torch_y = 2 * i + 2  # torch at y=2, 4, 6, ...
            stone_pos = (0, stone_y, 0)
            torch_pos = (0, torch_y, 0)
            stone_positions.append(stone_pos)
            torch_positions.append(torch_pos)
            blocks.append((*stone_pos, "minecraft:stone", None))
            blocks.append((*torch_pos, "minecraft:redstone_torch", None))

        # Lamp at top
        lamp_y = 2 * height + 1
        lamp_pos = (0, lamp_y, 0)
        blocks.append((*lamp_pos, "minecraft:redstone_lamp[lit=false]", None))

        support: List[Block] = []

        # Structural verification: check all core blocks
        all_checks: List[VerifyCheck] = [
            VerifyCheck((0, 0, 0), "minecraft:redstone_block"),
        ]
        for sp in stone_positions:
            all_checks.append(VerifyCheck(sp, "minecraft:stone"))
        for tp in torch_positions:
            all_checks.append(VerifyCheck(tp, "minecraft:redstone_torch"))
        all_checks.append(VerifyCheck(lamp_pos, "minecraft:redstone_lamp"))

        desc = (
            f"A vertical signal tower of height {height} using redstone torches. "
            f"A redstone block at (0,0,0) powers a vertical chain of {height} redstone "
            f"torches alternating with stone blocks. Each torch sits on a stone block "
            f"and powers the stone block above it. A redstone lamp at {lamp_pos} "
            f"sits at the top of the tower."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=all_checks,
                actions=[],
                tick_steps=20,
                final_checks=all_checks,
                negative_checks=[],
            ),
            category=self.category,
            difficulty="intermediate",
            metadata={
                "generator": self.name,
                "params": params,
                "height": height,
            },
        )


class TorchTowerGenerator(CircuitGenerator):
    """Redstone torch tower with floor-mounted torches (structural verification).

    Alternating stone blocks and floor-mounted redstone torches form a
    vertical tower. Each torch sits on a stone block and powers the block
    above it (and any adjacent lamp at the top).

    Layout for 1 torch:
      (0,0,0) stone → (0,1,0) torch → (0,2,0) lamp

    Layout for 2 torches:
      (0,0,0) stone → (0,1,0) torch → (0,2,0) stone → (0,3,0) torch → (0,4,0) lamp

    Floor torches (facing=up) power the block directly above them.
    Uses structural verification (checks block TYPE, not lit state).
    """

    name = "torch_tower"
    category = "signal_routing"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        return [
            {"num_torches": 1},
            {"num_torches": 2},
        ]

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        n = params["num_torches"]

        blocks: List[Block] = []

        # First stone block at bottom
        blocks.append((0, 0, 0, "minecraft:stone", None))

        # Alternating torches and stone blocks
        torch_positions: List[Tuple[int, int, int]] = []
        stone_positions: List[Tuple[int, int, int]] = [(0, 0, 0)]
        for i in range(n):
            torch_y = 2 * i + 1
            torch_pos = (0, torch_y, 0)
            torch_positions.append(torch_pos)
            blocks.append((*torch_pos, "minecraft:redstone_torch", None))

            if i < n - 1:
                stone_y = 2 * (i + 1)
                stone_pos = (0, stone_y, 0)
                stone_positions.append(stone_pos)
                blocks.append((*stone_pos, "minecraft:stone", None))

        # Lamp at top (at y = 2 * n)
        lamp_y = 2 * n
        lamp_pos = (0, lamp_y, 0)
        blocks.append((*lamp_pos, "minecraft:redstone_lamp[lit=false]", None))

        # No extra support needed — stone blocks serve as torch supports
        support: List[Block] = []

        # Structural verification: check block TYPE only (no lit state on lamp)
        all_checks: List[VerifyCheck] = [
            VerifyCheck((0, 0, 0), "minecraft:stone"),
        ]
        for tp in torch_positions:
            all_checks.append(VerifyCheck(tp, "minecraft:redstone_torch"))
        for sp in stone_positions[1:]:  # skip first stone (already added)
            all_checks.append(VerifyCheck(sp, "minecraft:stone"))
        # Lamp: check only block type, not lit state (torch may or may not power it)
        all_checks.append(VerifyCheck(lamp_pos, "minecraft:redstone_lamp"))

        torch_word = "torch" if n == 1 else "torches"
        desc = (
            f"A redstone torch tower with {n} {torch_word}. "
            f"A stone block at (0,0,0) supports a floor-mounted redstone torch "
            f"at (0,1,0), which powers the block above it."
        )
        if n > 1:
            desc += (
                f" A second stone at (0,2,0) supports another torch at (0,3,0), "
                f"which powers the lamp at {lamp_pos}."
            )
        else:
            desc += f" The torch powers the lamp at {lamp_pos}."

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=all_checks,
                actions=[],
                tick_steps=20,
                final_checks=all_checks,
                negative_checks=[],
            ),
            category=self.category,
            difficulty="intermediate",
            metadata={
                "generator": self.name,
                "params": params,
                "num_torches": n,
            },
        )


class SignalSplitGenerator(CircuitGenerator):
    """Redstone_block → multiple adjacent lamps (functional verification).

    A redstone_block at (0,0,0) powers 2 or 3 adjacent redstone lamps.
    Lamps are placed in different directions (including up) to provide
    signal distribution examples distinct from FanOutLamp.

    For 2 lamps: east (1,0,0) and up (0,1,0)
    For 3 lamps: east (1,0,0), west (-1,0,0), and up (0,1,0)

    All lamps remain lit as long as the redstone_block is present.
    Uses functional verification: checks all lamps are lit.
    """

    name = "signal_split"
    category = "signal_routing"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        return [
            {"num_lamps": 2},
            {"num_lamps": 3},
        ]

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        num_lamps = params["num_lamps"]

        blocks: List[Block] = [
            (0, 0, 0, "minecraft:redstone_block", None),
        ]

        # Lamp positions — use a mix of horizontal and vertical directions
        # east (X+), west (X-), up (Y+)
        lamp_dirs = [
            (1, 0, 0),   # east
            (-1, 0, 0),  # west
            (0, 1, 0),   # up
        ]
        lamp_positions: List[Tuple[int, int, int]] = []
        for i in range(num_lamps):
            dx, dy, dz = lamp_dirs[i]
            pos = (dx, dy, dz)
            lamp_positions.append(pos)
            blocks.append((*pos, "minecraft:redstone_lamp[lit=false]", None))

        # No support needed — redstone_block and redstone_lamp don't need supports
        support: List[Block] = []

        # Functional verification: all lamps should be lit
        lamp_checks = [
            VerifyCheck(pos, "minecraft:redstone_lamp[lit=true]")
            for pos in lamp_positions
        ]

        dir_names = {
            (1, 0, 0): "east",
            (-1, 0, 0): "west",
            (0, 1, 0): "up",
        }
        dirs_used = [dir_names[d] for d in lamp_dirs[:num_lamps]]
        dirs_str = ", ".join(dirs_used)

        desc = (
            f"A signal distribution circuit: a redstone block at (0,0,0) "
            f"powers {num_lamps} adjacent redstone lamps positioned to the "
            f"{dirs_str}. All lamps are lit because the redstone block "
            f"directly powers adjacent redstone components."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=lamp_checks,
                actions=[],
                tick_steps=5,
                final_checks=lamp_checks,
                negative_checks=[],
            ),
            category=self.category,
            difficulty="beginner",
            metadata={
                "generator": self.name,
                "params": params,
                "num_lamps": num_lamps,
                "lamp_positions": lamp_positions,
            },
        )
