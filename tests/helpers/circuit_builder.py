"""
Circuit Builder DSL for MIRA tests.

Provides functions for building Minecraft block lists programmatically.
Each function returns a tuple (x, y, z, block_state_str, nbt) matching the
format used by replicator.py, parser.py, and other MIRA modules.

Boolean property values are converted to lowercase strings per Minecraft convention.
"""

from typing import Any, Dict, List, Optional, Tuple

# Re-export commonly used types
BlockTuple = Tuple[int, int, int, str, Optional[Any]]
BlockList = List[BlockTuple]


def _state(block_id: str, **properties) -> str:
    """Build a block state string from a block ID and keyword properties."""
    if not properties:
        return block_id
    props = ",".join(f"{k}={v}" for k, v in sorted(properties.items()))
    return f"{block_id}[{props}]"


def block(x: int, y: int, z: int, block_id: str, **properties) -> BlockTuple:
    """Create a generic block tuple.

    Args:
        x, y, z: Coordinates
        block_id: Full block ID (e.g. 'minecraft:stone')
        **properties: Block state properties (boolean → lowercase string)

    Returns:
        (x, y, z, state_string, None)
    """
    processed = {}
    for k, v in properties.items():
        if isinstance(v, bool):
            processed[k] = str(v).lower()
        else:
            processed[k] = str(v)
    return (x, y, z, _state(block_id, **processed), None)


def lever(x: int, y: int, z: int, facing: str = "east", powered: bool = False) -> BlockTuple:
    """Create a lever block tuple."""
    return block(x, y, z, "minecraft:lever", facing=facing, powered=powered)


def lamp(x: int, y: int, z: int, lit: bool = False) -> BlockTuple:
    """Create a redstone lamp block tuple."""
    return block(x, y, z, "minecraft:redstone_lamp", lit=lit)


def wire(x: int, y: int, z: int) -> BlockTuple:
    """Create a redstone wire block tuple."""
    return block(x, y, z, "minecraft:redstone_wire")


def repeater(
    x: int, y: int, z: int,
    facing: str = "east", delay: int = 1,
    powered: bool = False, locked: bool = False,
) -> BlockTuple:
    """Create a repeater block tuple."""
    return block(x, y, z, "minecraft:repeater",
                 facing=facing, delay=delay, powered=powered, locked=locked)


def redstone_block(x: int, y: int, z: int) -> BlockTuple:
    """Create a redstone block tuple."""
    return block(x, y, z, "minecraft:redstone_block")


def stone(x: int, y: int, z: int) -> BlockTuple:
    """Create a stone block tuple."""
    return block(x, y, z, "minecraft:stone")


def piston(x: int, y: int, z: int, facing: str = "east", extended: bool = False) -> BlockTuple:
    """Create a piston block tuple."""
    return block(x, y, z, "minecraft:piston", facing=facing, extended=extended)


def sticky_piston(x: int, y: int, z: int, facing: str = "east", extended: bool = False) -> BlockTuple:
    """Create a sticky piston block tuple."""
    return block(x, y, z, "minecraft:sticky_piston", facing=facing, extended=extended)


def observer(x: int, y: int, z: int, facing: str = "east", powered: bool = False) -> BlockTuple:
    """Create an observer block tuple."""
    return block(x, y, z, "minecraft:observer", facing=facing, powered=powered)


def comparator(
    x: int, y: int, z: int,
    facing: str = "east", mode: str = "compare", powered: bool = False,
) -> BlockTuple:
    """Create a comparator block tuple."""
    return block(x, y, z, "minecraft:comparator", facing=facing, mode=mode, powered=powered)


def hopper(x: int, y: int, z: int, facing: str = "down") -> BlockTuple:
    """Create a hopper block tuple."""
    return block(x, y, z, "minecraft:hopper", facing=facing)


def dropper(x: int, y: int, z: int, facing: str = "east") -> BlockTuple:
    """Create a dropper block tuple."""
    return block(x, y, z, "minecraft:dropper", facing=facing)


def dispenser(x: int, y: int, z: int, facing: str = "east") -> BlockTuple:
    """Create a dispenser block tuple."""
    return block(x, y, z, "minecraft:dispenser", facing=facing)


def button(x: int, y: int, z: int, facing: str = "east", powered: bool = False) -> BlockTuple:
    """Create a stone button block tuple."""
    return block(x, y, z, "minecraft:stone_button", facing=facing, powered=powered)


def pressure_plate(x: int, y: int, z: int, powered: bool = False) -> BlockTuple:
    """Create a stone pressure plate block tuple."""
    return block(x, y, z, "minecraft:stone_pressure_plate", powered=powered)


def torch(x: int, y: int, z: int, lit: bool = True) -> BlockTuple:
    """Create a redstone torch block tuple."""
    return block(x, y, z, "minecraft:redstone_torch", lit=lit)


def chest(x: int, y: int, z: int, facing: str = "east") -> BlockTuple:
    """Create a chest block tuple."""
    return block(x, y, z, "minecraft:chest", facing=facing)


def barrel(x: int, y: int, z: int, facing: str = "east") -> BlockTuple:
    """Create a barrel block tuple."""
    return block(x, y, z, "minecraft:barrel", facing=facing)


def glass(x: int, y: int, z: int) -> BlockTuple:
    """Create a glass block tuple."""
    return block(x, y, z, "minecraft:glass")


def dirt(x: int, y: int, z: int) -> BlockTuple:
    """Create a dirt block tuple."""
    return block(x, y, z, "minecraft:dirt")


def air(x: int, y: int, z: int) -> BlockTuple:
    """Create an air block tuple (useful for gaps in circuits)."""
    return block(x, y, z, "minecraft:air")


def build(*blocks: BlockTuple) -> BlockList:
    """Combine multiple block tuples into a list.

    Args:
        *blocks: One or more block tuples

    Returns:
        A list of block tuples
    """
    return list(blocks)


def translate(blocks: BlockList, dx: int = 0, dy: int = 0, dz: int = 0) -> BlockList:
    """Translate all blocks by the given offset.

    Args:
        blocks: List of block tuples
        dx, dy, dz: Offset to apply to each block's coordinates

    Returns:
        New list of translated block tuples
    """
    return [(x + dx, y + dy, z + dz, state, nbt) for x, y, z, state, nbt in blocks]


def get_bounds(blocks: BlockList) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    """Return the bounding box of all blocks.

    Args:
        blocks: List of block tuples

    Returns:
        ((min_x, min_y, min_z), (max_x, max_y, max_z))

    Raises:
        ValueError: If the block list is empty
    """
    if not blocks:
        raise ValueError("Cannot compute bounds of an empty block list")
    xs = [b[0] for b in blocks]
    ys = [b[1] for b in blocks]
    zs = [b[2] for b in blocks]
    return (
        (min(xs), min(ys), min(zs)),
        (max(xs), max(ys), max(zs)),
    )


def add_support(blocks: BlockList, dy: int = -1) -> BlockList:
    """Add stone blocks underneath all non-air blocks that don't already have support.

    A block "has support" if there is already a block at (x, y+dy, z) in the list.
    Only non-air blocks get support added underneath.

    Args:
        blocks: List of block tuples
        dy: Vertical offset for support (default -1 = one block below)

    Returns:
        New list with support blocks added
    """
    # Build a set of occupied positions (x, y, z) for quick lookup
    occupied = {(b[0], b[1], b[2]) for b in blocks}

    support_blocks: BlockList = []
    for x, y, z, state, nbt in blocks:
        # Skip air blocks
        if state == "minecraft:air":
            continue
        support_pos = (x, y + dy, z)
        if support_pos not in occupied:
            support_blocks.append(stone(x, y + dy, z))
            occupied.add(support_pos)

    return blocks + support_blocks


def to_dict_list(blocks: BlockList) -> List[Dict[str, Any]]:
    """Convert block tuples to a JSON-serializable list of dicts.

    Each dict has keys: x, y, z, state, nbt

    Args:
        blocks: List of block tuples

    Returns:
        List of dicts suitable for serialization to JSON
    """
    result = []
    for x, y, z, state, nbt in blocks:
        entry: Dict[str, Any] = {
            "x": x,
            "y": y,
            "z": z,
            "state": state,
        }
        if nbt is not None:
            entry["nbt"] = nbt
        result.append(entry)
    return result
