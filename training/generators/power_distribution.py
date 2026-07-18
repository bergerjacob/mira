"""
MIRA: Power Distribution Circuit Generator

Generates circuits that distribute redstone power from a source to one or
more outputs (lamps). These are the simplest verifiable circuits:
- redstone_block → lamp (always on)
- lever → lamp (toggle)
- lever → wire → lamp
- redstone_block → multiple lamps (fan-out)
- lever → multiple lamps via wire

All circuits are verifiable via setblock (no repeaters/comparators/pistons).
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple
import itertools
from .base import (
    CircuitGenerator, CircuitExample, VerifyCheck, VerifyAction, VerifyContract,
    Block, add_support_blocks, make_plan, format_state,
)


class DirectPowerLamp(CircuitGenerator):
    """redstone_block adjacent to lamp — lamp is always on."""

    name = "direct_power_lamp"
    category = "power_distribution"

    DIRECTIONS = [
        ("east", 1, 0, 0, "facing=east"),
        ("west", -1, 0, 0, "facing=west"),
        ("north", 0, 0, -1, "facing=north"),
        ("south", 0, 0, 1, "facing=south"),
        ("up", 0, 1, 0, "facing=up"),
        ("down", 0, -1, 0, "facing=down"),
    ]

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for direction, dx, dy, dz, _ in self.DIRECTIONS:
            params.append({"direction": direction, "dx": dx, "dy": dy, "dz": dz})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        direction = params["direction"]
        dx, dy, dz = params["dx"], params["dy"], params["dz"]

        blocks: List[Block] = [
            (0, 0, 0, "minecraft:redstone_block", None),
            (dx, dy, dz, "minecraft:redstone_lamp[lit=false]", None),
        ]
        support = add_support_blocks(blocks)
        # Lamp should be lit=true because redstone_block powers it
        verify = VerifyContract(
            initial_checks=[
                VerifyCheck((dx, dy, dz), "minecraft:redstone_lamp[lit=true]"),
            ],
            actions=[],
            tick_steps=5,
            final_checks=[
                VerifyCheck((dx, dy, dz), "minecraft:redstone_lamp[lit=true]"),
            ],
            negative_checks=[],
        )
        desc = (f"A redstone block at position (0,0,0) directly powers a redstone lamp "
                f"at ({dx},{dy},{dz}) to the {direction}. The lamp is always on.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="beginner",
            metadata={"generator": self.name, "params": params},
        )


class LeverLampDirect(CircuitGenerator):
    """Lever directly adjacent to lamp — toggle lever to turn lamp on/off."""

    name = "lever_lamp_direct"
    category = "power_distribution"

    # (name, dx, dy, dz, lever_facing)
    DIRECTIONS = [
        ("east", 1, 0, 0, "east"),
        ("west", -1, 0, 0, "west"),
        ("north", 0, 0, -1, "north"),
        ("south", 0, 0, 1, "south"),
        ("up", 0, 1, 0, "north"),
        ("down", 0, -1, 0, "north"),
    ]

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for direction, dx, dy, dz, lever_facing in self.DIRECTIONS:
            params.append({"direction": direction, "dx": dx, "dy": dy, "dz": dz,
                           "lever_facing": lever_facing})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        direction = params["direction"]
        dx, dy, dz = params["dx"], params["dy"], params["dz"]
        lever_facing = params["lever_facing"]

        lever_pos = (0, 0, 0)
        lamp_pos = (dx, dy, dz)
        blocks: List[Block] = [
            (*lever_pos, f"minecraft:lever[facing={lever_facing},powered=false]", None),
            (*lamp_pos, "minecraft:redstone_lamp[lit=false]", None),
        ]
        support = add_support_blocks(blocks)
        verify = VerifyContract(
            initial_checks=[
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
            ],
            actions=[
                VerifyAction(lever_pos, f"minecraft:lever[facing={lever_facing},powered=true]"),
            ],
            tick_steps=20,
            final_checks=[
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=true]"),
            ],
            negative_checks=[
                VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
            ],
        )
        direction_word = direction if direction in ("up", "down") else f"the {direction}"
        desc = (f"A lever at (0,0,0) directly powers a redstone lamp at ({dx},{dy},{dz}) "
                f"to {direction_word}. Flipping the lever turns the lamp on.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="beginner",
            metadata={"generator": self.name, "params": params},
        )


class LeverWireLamp(CircuitGenerator):
    """Lever → wire chain → lamp. Variable wire length."""

    name = "lever_wire_lamp"
    category = "power_distribution"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for wire_length in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
            for direction in ["east", "west"]:
                params.append({"wire_length": wire_length, "direction": direction})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        wire_length = params["wire_length"]
        direction = params["direction"]
        dx = 1 if direction == "east" else -1
        # Lever must face the direction of the wire to power it
        lever_facing = direction

        blocks: List[Block] = [
            (0, 0, 0, f"minecraft:lever[facing={lever_facing},powered=false]", None),
        ]
        # Wire from x=1 to x=wire_length (or x=-1 to x=-wire_length)
        for i in range(1, wire_length + 1):
            blocks.append((i * dx, 0, 0, "minecraft:redstone_wire", None))
        lamp_x = (wire_length + 1) * dx
        lamp_pos = (lamp_x, 0, 0)
        blocks.append((*lamp_pos, "minecraft:redstone_lamp[lit=false]", None))

        support = add_support_blocks(blocks)
        desc = (f"A lever at (0,0,0) powers a redstone lamp at ({lamp_x},0,0) through "
                f"a chain of {wire_length} redstone wire blocks extending {direction}. "
                f"Flipping the lever turns the lamp on.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=[
                    VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
                ],
                actions=[
                    VerifyAction((0, 0, 0), f"minecraft:lever[facing={lever_facing},powered=true]"),
                ],
                tick_steps=20,
                final_checks=[
                    VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=true]"),
                ],
                negative_checks=[
                    VerifyCheck(lamp_pos, "minecraft:redstone_lamp[lit=false]"),
                ],
            ),
            category=self.category,
            difficulty="beginner" if wire_length <= 3 else "intermediate",
            metadata={"generator": self.name, "params": params},
        )


class FanOutLamp(CircuitGenerator):
    """redstone_block → multiple lamps in different directions."""

    name = "fan_out_lamp"
    category = "power_distribution"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        # Redstone block only powers ADJACENT blocks (distance 1).
        # Valid adjacent positions: east, west, north, south, up, down.
        # We can have 2-6 lamps in various combinations.
        params = []
        for num_lamps in [2, 3, 4, 5, 6]:
            params.append({"num_lamps": num_lamps})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        num_lamps = params["num_lamps"]

        blocks: List[Block] = [
            (0, 0, 0, "minecraft:redstone_block", None),
        ]
        # All 6 adjacent positions (east, west, north, south, up, down)
        dirs = [(1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0)]
        lamp_positions = []

        for i in range(num_lamps):
            dx, dy, dz = dirs[i]
            pos = (dx, dy, dz)
            lamp_positions.append(pos)
            blocks.append((*pos, "minecraft:redstone_lamp[lit=false]", None))

        support = add_support_blocks(blocks)
        verify = VerifyContract(
            initial_checks=[
                VerifyCheck(pos, "minecraft:redstone_lamp[lit=true]")
                for pos in lamp_positions
            ],
            actions=[],
            tick_steps=5,
            final_checks=[
                VerifyCheck(pos, "minecraft:redstone_lamp[lit=true]")
                for pos in lamp_positions
            ],
            negative_checks=[],
        )
        desc = (f"A redstone block at (0,0,0) powers {num_lamps} redstone lamps "
                f"placed directly adjacent to it. All lamps are always on.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="beginner" if num_lamps <= 2 else "intermediate",
            metadata={"generator": self.name, "params": params},
        )


class RedstoneBlockLamp(CircuitGenerator):
    """Lamp placed directly adjacent to a redstone block — always lit.

    Framed from the lamp's perspective: a redstone lamp placed next to a
    redstone block receives constant power and remains always on.
    """

    name = "redstone_block_lamp"
    category = "power_distribution"

    DIRECTIONS = [
        ("east", 1, 0, 0, "east"),
        ("west", -1, 0, 0, "west"),
        ("north", 0, 0, -1, "north"),
        ("south", 0, 0, 1, "south"),
        ("up", 0, 1, 0, "up"),
        ("down", 0, -1, 0, "down"),
    ]

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for direction, dx, dy, dz, _ in self.DIRECTIONS:
            params.append({"direction": direction, "dx": dx, "dy": dy, "dz": dz})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        direction = params["direction"]
        dx, dy, dz = params["dx"], params["dy"], params["dz"]

        blocks: List[Block] = [
            (dx, dy, dz, "minecraft:redstone_lamp[lit=false]", None),
            (0, 0, 0, "minecraft:redstone_block", None),
        ]
        support = add_support_blocks(blocks)
        direction_word = direction if direction in ("up", "down") else f"to the {direction}"
        verify = VerifyContract(
            initial_checks=[
                VerifyCheck((dx, dy, dz), "minecraft:redstone_lamp[lit=true]"),
            ],
            actions=[],
            tick_steps=5,
            final_checks=[
                VerifyCheck((dx, dy, dz), "minecraft:redstone_lamp[lit=true]"),
            ],
            negative_checks=[],
        )
        desc = (f"A redstone lamp at ({dx},{dy},{dz}) is placed {direction_word} of a "
                f"redstone block at (0,0,0). The lamp receives constant power from the "
                f"adjacent redstone block and remains lit at all times.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="beginner",
            metadata={"generator": self.name, "params": params},
        )


class LeverMultiLamp(CircuitGenerator):
    """Lever → multiple lamps via wire. Toggle lever to turn all lamps on/off."""

    name = "lever_multi_lamp"
    category = "power_distribution"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for num_lamps in [2, 3]:
            for wire_length in [1, 2]:
                params.append({"num_lamps": num_lamps, "wire_length": wire_length})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        num_lamps = params["num_lamps"]
        wire_length = params["wire_length"]

        blocks: List[Block] = [
            (0, 0, 0, "minecraft:lever[facing=east,powered=false]", None),
        ]
        # Main wire chain east from lever
        for i in range(1, wire_length + 1):
            blocks.append((i, 0, 0, "minecraft:redstone_wire", None))
        # T-junction at (wire_length, 0, 0) — wire already placed above
        # Branch paths: each goes south (Z+) then east (X+) to a lamp
        # This ensures each lamp is powered from its west (X-axis) side
        lamp_positions = []
        for i in range(num_lamps):
            branch_z = i + 1  # each branch at different Z
            # Wire south from junction
            blocks.append((wire_length, 0, branch_z, "minecraft:redstone_wire", None))
            # Wire east to lamp (ensures lamp is powered from X-axis)
            lamp_x = wire_length + 1
            lamp_pos = (lamp_x, 0, branch_z)
            # Add wire connecting to lamp if not adjacent to junction
            if lamp_x > wire_length + 1:
                for x in range(wire_length + 1, lamp_x):
                    blocks.append((x, 0, branch_z, "minecraft:redstone_wire", None))
            lamp_positions.append(lamp_pos)
            blocks.append((*lamp_pos, "minecraft:redstone_lamp[lit=false]", None))

        support = add_support_blocks(blocks)
        verify = VerifyContract(
            initial_checks=[
                VerifyCheck(pos, "minecraft:redstone_lamp[lit=false]")
                for pos in lamp_positions
            ],
            actions=[
                VerifyAction((0, 0, 0), "minecraft:lever[facing=east,powered=true]"),
            ],
            tick_steps=20,
            final_checks=[
                VerifyCheck(pos, "minecraft:redstone_lamp[lit=true]")
                for pos in lamp_positions
            ],
            negative_checks=[
                VerifyCheck(pos, "minecraft:redstone_lamp[lit=false]")
                for pos in lamp_positions
            ],
        )
        desc = (f"A lever at (0,0,0) powers {num_lamps} redstone lamps through a "
                f"wire chain of length {wire_length} that branches into separate paths. "
                f"Flipping the lever turns all lamps on simultaneously.")
        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=verify,
            category=self.category,
            difficulty="intermediate",
            metadata={"generator": self.name, "params": params},
        )
