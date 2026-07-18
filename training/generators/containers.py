"""
MIRA: Container Circuit Generator

Generates circuits involving containers (chests, hoppers, droppers, dispensers).
Uses structural verification (check_block) exclusively since the current
verification framework supports block state checking, not inventory checking.

Generators:
- HopperChainGenerator: Vertical hopper chain for item transport
- HopperTransportGenerator: Horizontal hopper transport line
- ChestArrayGenerator: Array of chests with items
- DropperDispenserGenerator: Droppers facing various directions
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from .base import (
    CircuitGenerator, CircuitExample, VerifyCheck, VerifyAction, VerifyContract,
    Block, add_support_blocks, format_state,
)


class HopperChainGenerator(CircuitGenerator):
    """Vertical hopper chain: chest → hopper(s) → chest.

    Items placed in the top chest flow down through hoppers to the bottom chest.
    Structural verification confirms all blocks are placed with correct states.
    """

    name = "hopper_chain"
    category = "containers"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for chain_length in [1, 2, 3]:
            params.append({"chain_length": chain_length})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        chain_length = params["chain_length"]

        # Layout: all on Z=0, X=0
        # chain_length=N: top chest at (0,N+1,0), hoppers at (0,N,0)...(0,1,0),
        # bottom chest at (0,0,0)
        blocks: List[Block] = []

        # Bottom chest
        bottom_chest_pos = (0, 0, 0)
        blocks.append((*bottom_chest_pos, "minecraft:chest", None))

        # Hoppers facing down (transfers to block below)
        hopper_positions = []
        for i in range(chain_length):
            y = i + 1
            hopper_pos = (0, y, 0)
            hopper_positions.append(hopper_pos)
            blocks.append((*hopper_pos, "minecraft:hopper[facing=down]", None))

        # Top chest
        top_chest_y = chain_length + 1
        top_chest_pos = (0, top_chest_y, 0)
        blocks.append((*top_chest_pos, "minecraft:chest", None))

        # No extra support needed: each hopper sits on the block below it
        # (bottom chest or another hopper in the chain)
        support: List[Block] = []

        # Structural verification
        checks = [
            VerifyCheck(bottom_chest_pos, "minecraft:chest"),
        ]
        for hp in hopper_positions:
            checks.append(VerifyCheck(hp, "minecraft:hopper[facing=down]"))
        checks.append(VerifyCheck(top_chest_pos, "minecraft:chest"))

        hopper_word = "hopper" if chain_length == 1 else "hoppers"
        desc = (
            f"A vertical hopper chain with {chain_length} {hopper_word}. "
            f"A chest at {top_chest_pos} feeds items into a chain of {chain_length} "
            f"{hopper_word} all facing downward, ending in a bottom chest at "
            f"{bottom_chest_pos}. Items placed in the top chest will flow down "
            f"through the {hopper_word} to the bottom chest via gravity."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=checks,
                actions=[],
                tick_steps=20,
                final_checks=checks,
                negative_checks=[],
            ),
            category=self.category,
            difficulty="beginner",
            metadata={
                "generator": self.name,
                "params": params,
                "chain_length": chain_length,
                "container_type": "hopper_chain",
            },
        )


class HopperTransportGenerator(CircuitGenerator):
    """Horizontal hopper transport: chest → hopper(s) facing east → chest.

    Items placed in the first chest get pulled into the hopper chain and moved
    horizontally to the destination chest. Structural verification confirms all
    blocks are placed with correct states and support blocks exist beneath
    each hopper.
    """

    name = "hopper_transport"
    category = "containers"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for chain_length in [1, 2, 3]:
            params.append({"chain_length": chain_length})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        chain_length = params["chain_length"]

        # Layout: all on Y=0, Z=0
        # Source chest at (0,0,0), N hoppers facing east,
        # destination chest at (N+1,0,0)
        blocks: List[Block] = []
        support: List[Block] = []

        # Source chest
        src_chest_pos = (0, 0, 0)
        blocks.append((*src_chest_pos, "minecraft:chest", None))

        # Hoppers facing east (transfers to block to the east)
        hopper_positions = []
        for i in range(chain_length):
            x = i + 1
            hopper_pos = (x, 0, 0)
            hopper_positions.append(hopper_pos)
            blocks.append((*hopper_pos, "minecraft:hopper[facing=east]", None))
            # Add support block underneath each hopper
            support.append((x, -1, 0, "minecraft:stone", None))

        # Destination chest
        dest_chest_x = chain_length + 1
        dest_chest_pos = (dest_chest_x, 0, 0)
        blocks.append((*dest_chest_pos, "minecraft:chest", None))

        # Structural verification — check blocks and support blocks
        checks = [
            VerifyCheck(src_chest_pos, "minecraft:chest"),
        ]
        for hp in hopper_positions:
            checks.append(VerifyCheck(hp, "minecraft:hopper[facing=east]"))
        checks.append(VerifyCheck(dest_chest_pos, "minecraft:chest"))
        # Also verify support stones under hoppers
        for sp in support:
            checks.append(VerifyCheck((sp[0], sp[1], sp[2]), "minecraft:stone"))

        hopper_word = "hopper" if chain_length == 1 else "hoppers"
        desc = (
            f"A horizontal hopper transport line with {chain_length} {hopper_word}. "
            f"A source chest at {src_chest_pos} feeds into {chain_length} {hopper_word} "
            f"facing east, ending at a destination chest at {dest_chest_pos}. "
            f"Items placed in the source chest will be pulled into the hopper chain "
            f"and transported horizontally to the destination chest."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=checks,
                actions=[],
                tick_steps=20,
                final_checks=checks,
                negative_checks=[],
            ),
            category=self.category,
            difficulty="beginner",
            metadata={
                "generator": self.name,
                "params": params,
                "chain_length": chain_length,
                "container_type": "hopper_transport",
            },
        )


class ChestArrayGenerator(CircuitGenerator):
    """Array of chests with items.

    A linear arrangement of chests, each containing different items.
    Structural verification confirms all chests are placed.
    """

    name = "chest_array"
    category = "containers"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for num_chests in [2, 4, 6]:
            params.append({"num_chests": num_chests})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        num_chests = params["num_chests"]

        # Layout: chests spaced 2 apart along X axis
        # Each chest at (x*2, 0, 0) for x in 0..num_chests-1
        blocks: List[Block] = []
        support: List[Block] = []

        chest_positions = []
        for i in range(num_chests):
            x = i * 2  # space chests 2 apart
            chest_pos = (x, 0, 0)
            chest_positions.append(chest_pos)
            blocks.append((*chest_pos, "minecraft:chest", None))

        # Structural verification
        checks = [
            VerifyCheck(pos, "minecraft:chest")
            for pos in chest_positions
        ]

        # Different item types for descriptive purposes
        item_types = [
            "diamond", "iron_ingot", "gold_ingot", "emerald",
            "redstone", "lapis_lazuli", "coal", "copper_ingot",
        ]
        items_desc = ", ".join(item_types[:num_chests])

        pos_strs = [str(p) for p in chest_positions]
        desc = (
            f"An array of {num_chests} chests arranged along the X-axis. "
            f"The chests are at positions: {', '.join(pos_strs)}. "
            f"Each chest contains a different item type: {items_desc}. "
            f"The chests are used for item storage and organization."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=checks,
                actions=[],
                tick_steps=5,
                final_checks=checks,
                negative_checks=[],
            ),
            category=self.category,
            difficulty="beginner",
            metadata={
                "generator": self.name,
                "params": params,
                "num_chests": num_chests,
                "container_type": "chest_array",
            },
        )


class DropperDispenserGenerator(CircuitGenerator):
    """Dropper facing various directions with inventory.

    Droppers placed facing north, south, east, west, up, and down.
    Structural verification confirms correct facing for each dropper placement.
    """

    name = "dropper_dispenser"
    category = "containers"

    FACINGS = ["north", "south", "east", "west", "up", "down"]

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for facing in self.FACINGS:
            params.append({"facing": facing})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        facing = params["facing"]

        # Single dropper at (0,0,0) with specified facing
        dropper_pos = (0, 0, 0)
        state = f"minecraft:dropper[facing={facing}]"

        blocks: List[Block] = [
            (*dropper_pos, state, None),
        ]
        support: List[Block] = []

        check = VerifyCheck(dropper_pos, state)
        desc = (
            f"A dropper placed at (0,0,0) facing {facing}. "
            f"The dropper can store up to 9 stacks of items and will eject "
            f"items in the {facing} direction when activated with a redstone signal."
        )

        return CircuitExample(
            description=desc,
            blocks=blocks,
            support_blocks=support,
            verify_contract=VerifyContract(
                initial_checks=[check],
                actions=[],
                tick_steps=5,
                final_checks=[check],
                negative_checks=[],
            ),
            category=self.category,
            difficulty="beginner",
            metadata={
                "generator": self.name,
                "params": params,
                "facing": facing,
                "container_type": "dropper",
            },
        )
