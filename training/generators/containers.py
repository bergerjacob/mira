"""
MIRA: Container Circuit Generator

Generates circuits involving containers (chests, hoppers, droppers, dispensers, barrels).
Uses structural verification (check_block) exclusively since the current
verification framework supports block state checking, not inventory checking.

Generators:
- HopperChainGenerator: Vertical hopper chain for item transport
- HopperTransportGenerator: Horizontal hopper transport line
- ChestArrayGenerator: Array of chests with items
- DropperDispenserGenerator: Droppers and dispensers facing various directions
- BarrelArrayGenerator: Array of barrels with items
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
        for chain_length in [1, 2, 3, 4, 5]:
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
        for chain_length in [1, 2, 3, 4, 5]:
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
        for num_chests in [2, 4, 6, 8, 10]:
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
            "netherite_scrap", "quartz",
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
            for container_type in ["dropper", "dispenser"]:
                params.append({"facing": facing, "container_type": container_type})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        facing = params["facing"]
        container_type = params["container_type"]  # "dropper" or "dispenser"

        # Single container at (0,0,0) with specified facing
        container_pos = (0, 0, 0)
        state = f"minecraft:{container_type}[facing={facing}]"

        blocks: List[Block] = [
            (*container_pos, state, None),
        ]
        support: List[Block] = []

        check = VerifyCheck(container_pos, state)

        ct_display = container_type  # already the right name
        desc = (
            f"A {ct_display} placed at (0,0,0) facing {facing}. "
            f"The {ct_display} can store up to 9 stacks of items and will eject "
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
                "container_type": container_type,
            },
        )


class BarrelArrayGenerator(CircuitGenerator):
    """Array of barrels with items.

    A linear arrangement of barrels, similar to ChestArrayGenerator but
    using barrels instead of chests. Barrels provide single-chest-equivalent
    storage and can be locked with redstone.

    Structural verification confirms all barrels are placed.
    3 variants: 2, 4, or 6 barrels.
    """

    name = "barrel_array"
    category = "containers"

    def enumerate_params(self) -> List[Dict[str, Any]]:
        params = []
        for num_barrels in [2, 4, 6]:
            params.append({"num_barrels": num_barrels})
        return params

    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        num_barrels = params["num_barrels"]

        # Layout: barrels spaced 2 apart along X axis
        blocks: List[Block] = []
        support: List[Block] = []

        barrel_positions = []
        for i in range(num_barrels):
            x = i * 2
            barrel_pos = (x, 0, 0)
            barrel_positions.append(barrel_pos)
            blocks.append((*barrel_pos, "minecraft:barrel[facing=up]", None))

        # Structural verification
        checks = [
            VerifyCheck(pos, "minecraft:barrel[facing=up]")
            for pos in barrel_positions
        ]

        pos_strs = [str(p) for p in barrel_positions]
        desc = (
            f"An array of {num_barrels} barrels arranged along the X-axis. "
            f"The barrels are at positions: {', '.join(pos_strs)}. "
            f"Each barrel is facing up and can store up to 27 stacks of items. "
            f"Barrels are used for item storage and remain accessible even when "
            f"a block is placed above them (unlike chests)."
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
                "num_barrels": num_barrels,
                "container_type": "barrel_array",
            },
        )
