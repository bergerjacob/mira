"""Block-state helpers: validate/canonicalise states against Nucleation's registry,
plus static support checks (the simulator does not pop floating blocks off)."""
import json
import re
from functools import lru_cache

from nucleation import Blocks

_STATE_RE = re.compile(r"^([a-z0-9_:.]+)(?:\[([^\]]*)\])?$")

DIRS = {
    "north": (0, 0, -1), "south": (0, 0, 1), "west": (-1, 0, 0), "east": (1, 0, 0),
    "up": (0, 1, 0), "down": (0, -1, 0),
}
OPPOSITE = {"north": "south", "south": "north", "west": "east", "east": "west", "up": "down", "down": "up"}

# blocks that never sit on / hold up a component (air, fluids, thin things)
NO_SUPPORT_KINDS = {"minecraft:air", "minecraft:liquid", "minecraft:redstone_wire", "minecraft:repeater",
                    "minecraft:lever", "minecraft:button", "minecraft:comparator"}
FLOOR_ONLY = {"redstone_wire", "repeater", "comparator", "redstone_torch", "rail", "powered_rail",
              "detector_rail", "activator_rail"}
FLOOR_ONLY_SUFFIX = ("_pressure_plate",)
ATTACH_WALL = {"redstone_wall_torch"}
ATTACH_FACE = {"lever"}  # + *_button, handled by suffix


class BlockError(ValueError):
    pass


@lru_cache(maxsize=None)
def info(block_id: str):
    try:
        return json.loads(Blocks.get_json(block_id))
    except Exception:
        return None


def split_state(state: str):
    """'minecraft:lever[face=wall,facing=east]' -> ('minecraft:lever', {'face':'wall',...})"""
    m = _STATE_RE.match(state.strip())
    if not m:
        raise BlockError(f"malformed block state {state!r}")
    bid = m.group(1)
    if ":" not in bid:
        bid = "minecraft:" + bid
    props = {}
    if m.group(2):
        for kv in m.group(2).split(","):
            if "=" not in kv:
                raise BlockError(f"malformed property {kv!r} in {state!r}")
            k, v = kv.split("=", 1)
            props[k.strip()] = v.strip()
    return bid, props


def join_state(bid: str, props: dict) -> str:
    return bid + ("[" + ",".join(f"{k}={props[k]}" for k in sorted(props)) + "]" if props else "")


@lru_cache(maxsize=None)
def canonical(state: str) -> str:
    """Validate against the registry and return the full canonical state (all properties filled with
    defaults, sorted). Needed because Nucleation silently accepts typos and half-specified states (a
    repeater without locked/powered breaks TickSimulation loading)."""
    bid, props = split_state(state)
    i = info(bid)
    if i is None:
        raise BlockError(f"unknown block {bid!r}")
    full = dict(i["default_state"])
    for k, v in props.items():
        if k not in i["properties"]:
            raise BlockError(f"{bid} has no property {k!r} (valid: {sorted(i['properties'])})")
        if v not in i["properties"][k]:
            raise BlockError(f"{bid}[{k}={v}] invalid (valid: {i['properties'][k]})")
        full[k] = v
    return join_state(bid, full)


def short(bid: str) -> str:
    return bid.split(":", 1)[-1]


def is_air(state: str) -> bool:
    return split_state(state)[0] in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")


def supports(state: str, face: str) -> bool:
    """Can this block hold a component attached to its `face` (up/north/...)? Approximate vanilla rules."""
    bid, props = split_state(state)
    i = info(bid)
    if i is None or i["kind"] in NO_SUPPORT_KINDS:
        return False
    name = short(bid)
    if name.endswith("_slab"):
        t = props.get("type", "bottom")
        return t == "double" or (t == "top" and face == "up") or (t == "bottom" and face == "down")
    if i["kind"] == "minecraft:piston_base":
        return props.get("extended", "false") == "false"
    if name == "hopper":
        return face == "up"
    return bool(i["full_cube"])


def attachment(state: str):
    """Return (needed_support_dir, label) for blocks that need a neighbour to survive, else None.
    The direction is where the supporting block sits relative to the component."""
    bid, props = split_state(state)
    name = short(bid)
    if name in FLOOR_ONLY or name.endswith(FLOOR_ONLY_SUFFIX) or name == "redstone_torch":
        return "down", name
    if name in ATTACH_WALL:
        return OPPOSITE[props.get("facing", "north")], name
    if name == "lever" or name.endswith("_button"):
        face = props.get("face", "wall")
        if face == "floor":
            return "down", name
        if face == "ceiling":
            return "up", name
        return OPPOSITE[props.get("facing", "north")], name
    return None


def floating(world: dict):
    """world: {(x,y,z): canonical_state}. Yields (pos, state, reason) for components without support."""
    for pos, st in world.items():
        a = attachment(st)
        if a is None:
            continue
        d, name = a
        dx, dy, dz = DIRS[d]
        nb = world.get((pos[0] + dx, pos[1] + dy, pos[2] + dz))
        if nb is None or not supports(nb, OPPOSITE[d]):
            yield pos, st, f"{name} has no supporting block on its {d} side"
