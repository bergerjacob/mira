"""Block-state registry, validator, canonicalizer and rotate/mirror tables.

Nucleation accepts any string as a block state, so validity comes from the Minecraft 1.21.0
server data report (blocks.json) condensed into blockstates_1_21.json. Regenerate with:
    python simulation/blockstates.py --regen [server.jar]

Canonical state form: `minecraft:<id>[k=v,...]` with ALL properties present (defaults filled
in) and sorted by name; property-less blocks have no brackets. Blocks missing from the table
(newer versions, mods) pass through with sorted properties when strict=False.
"""

import difflib
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Dict, Optional, Tuple

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blockstates_1_21.json")
AIR_IDS = frozenset({"minecraft:air", "minecraft:cave_air", "minecraft:void_air"})

_STATE_RE = re.compile(r"^\s*([a-z0-9_.\-]+(?::[a-z0-9_./\-]+)?)\s*(?:\[(.*)\])?\s*$", re.S)


class StateError(ValueError):
    """Invalid block state; `kind` is unknown_block | unknown_property | bad_value | syntax."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@functools.lru_cache(maxsize=None)
def _table() -> Tuple[Dict[str, Tuple[Dict[str, Tuple[str, ...]], Dict[str, str]]], frozenset]:
    with open(DATA_PATH) as f:
        raw = json.load(f)
    sets = raw["sets"]
    table = {}
    for bid, (set_idx, default) in raw["blocks"].items():
        props = {k: tuple(v) for k, v in sets[set_idx].items()}
        defaults = dict(kv.split("=", 1) for kv in default.split(",")) if default else {}
        table["minecraft:" + bid] = (props, defaults)
    return table, frozenset(table)


def known_blocks() -> frozenset:
    return _table()[1]


def block_properties(block_id: str) -> Optional[Dict[str, Tuple[str, ...]]]:
    """Property name -> allowed values, or None for an unknown block."""
    entry = _table()[0].get(block_id)
    return entry[0] if entry else None


def block_defaults(block_id: str) -> Optional[Dict[str, str]]:
    entry = _table()[0].get(block_id)
    return entry[1] if entry else None


def parse_state(s: str) -> Tuple[str, Dict[str, str]]:
    """Split a state string into (namespaced block id, props); syntax checks only."""
    m = _STATE_RE.match(s) if isinstance(s, str) else None
    if not m:
        raise StateError("syntax", f"cannot parse block state {s!r} (expected id or id[k=v,...])")
    bid, body = m.group(1), m.group(2)
    if ":" not in bid:
        bid = "minecraft:" + bid
    props: Dict[str, str] = {}
    if body is not None and body.strip():
        for part in body.split(","):
            k, eq, v = part.partition("=")
            k, v = k.strip(), v.strip()
            if not eq or not k or not v:
                raise StateError("syntax", f"bad property {part.strip()!r} in {s!r} (expected key=value)")
            if k in props:
                raise StateError("syntax", f"duplicate property {k!r} in {s!r}")
            props[k] = v
    return bid, props


def format_state(block_id: str, props: Dict[str, str]) -> str:
    if not props:
        return block_id
    return block_id + "[" + ",".join(f"{k}={props[k]}" for k in sorted(props)) + "]"


def _suggest(word: str, options) -> str:
    close = difflib.get_close_matches(word, list(options), n=3, cutoff=0.6)
    return f" (did you mean {', '.join(close)}?)" if close else ""


@functools.lru_cache(maxsize=1 << 17)
def normalize(s: str, strict: bool = True) -> str:
    """Canonicalize a state string. strict=True raises StateError for unknown blocks, properties
    or values; strict=False never raises on semantics (invalid/unknown states keep their given
    properties, sorted) so foreign-version schematics can pass through."""
    bid, props = parse_state(s)
    entry = _table()[0].get(bid)
    if entry is None:
        if strict:
            short = bid[len("minecraft:"):] if bid.startswith("minecraft:") else bid
            raise StateError("unknown_block", f"unknown block {bid!r}" + _suggest(
                short, (b[len("minecraft:"):] for b in known_blocks())))
        return format_state(bid, props)
    allowed, defaults = entry
    for k, v in props.items():
        err = None
        if k not in allowed:
            valid = ", ".join(sorted(allowed)) or "none"
            err = StateError("unknown_property",
                             f"{bid} has no property {k!r} (valid: {valid})" + _suggest(k, allowed))
        elif v not in allowed[k]:
            err = StateError("bad_value", f"{bid}[{k}={v}] is invalid; allowed values: {', '.join(allowed[k])}"
                             + _suggest(v, allowed[k]))
        if err:
            if strict:
                raise err
            return format_state(bid, props)
    return format_state(bid, {**defaults, **props})


def is_air(state_or_id: str) -> bool:
    return state_or_id.split("[", 1)[0] in AIR_IDS


# --- rotate / mirror ------------------------------------------------------------------

_CW = ["north", "east", "south", "west"]  # clockwise seen from above (+y); rot=90 maps north->east
_FLIP = {"x": {"east": "west", "west": "east"}, "z": {"north": "south", "south": "north"}}
_SWAP_LR = {"left": "right", "right": "left", "inner_left": "inner_right", "inner_right": "inner_left",
            "outer_left": "outer_right", "outer_right": "outer_left"}


def _dir(d: str, rot: int, mirror: Optional[str]) -> str:
    """Mirror then rotate a direction name; non-horizontal names are unchanged."""
    if mirror:
        d = _FLIP[mirror].get(d, d)
    if d in _CW:
        d = _CW[(_CW.index(d) + rot // 90) % 4]
    return d


def _rail_shape(v: str, rot: int, mirror: Optional[str]) -> str:
    if v.startswith("ascending_"):
        return "ascending_" + _dir(v[len("ascending_"):], rot, mirror)
    a, b = v.split("_")
    a, b = _dir(a, rot, mirror), _dir(b, rot, mirror)
    # MC names: north_south, east_west, then north/south first for corners
    if {a, b} in ({"north", "south"}, {"east", "west"}):
        return "north_south" if "north" in (a, b) else "east_west"
    ns, ew = (a, b) if a in ("north", "south") else (b, a)
    return f"{ns}_{ew}"


def _transform(block_id: str, props: Dict[str, str], rot: int, mirror: Optional[str]) -> Dict[str, str]:
    out = dict(props)
    odd = (rot // 90) % 2 == 1
    for d in _CW:  # per-side connection props (wire, fences, walls, panes, vines...)
        if d in props:
            out[_dir(d, rot, mirror)] = props[d]
    for k, v in props.items():
        if k == "facing":
            out[k] = _dir(v, rot, mirror)
        elif k == "rotation":
            r = int(v)
            if mirror == "x":
                r = (16 - r) % 16
            elif mirror == "z":
                r = (8 - r) % 16
            out[k] = str((r + 4 * (rot // 90)) % 16)
        elif k == "axis":
            out[k] = {"x": "z", "z": "x"}.get(v, v) if odd else v
        elif k == "orientation":
            out[k] = "_".join(_dir(t, rot, mirror) for t in v.split("_"))
        elif k == "shape":
            if "rail" in block_id:
                out[k] = _rail_shape(v, rot, mirror)
            elif mirror:  # stairs: a mirror flips handedness
                out[k] = _SWAP_LR.get(v, v)
        elif k == "hinge" and mirror:
            out[k] = _SWAP_LR.get(v, v)
        elif k == "type" and mirror and v in ("left", "right") and block_id.endswith("chest"):
            out[k] = _SWAP_LR[v]
    return out


@functools.lru_cache(maxsize=65536)
def transform_state(state: str, rot: int = 0, mirror: Optional[str] = None) -> str:
    """Mirror (axis 'x' negates x, 'z' negates z) then rotate clockwise from above by rot degrees."""
    if rot % 90 or mirror not in (None, "x", "z"):
        raise ValueError(f"rot must be a multiple of 90 and mirror one of None/'x'/'z' (got {rot}, {mirror})")
    rot %= 360
    if rot == 0 and mirror is None:
        return state
    bid, props = parse_state(state)
    if not props:
        return state
    return format_state(bid, _transform(bid, props, rot, mirror))


def rotate_point(p: Tuple[int, int, int], rot: int, mirror: Optional[str]) -> Tuple[int, int, int]:
    x, y, z = p
    if mirror == "x":
        x = -x
    elif mirror == "z":
        z = -z
    for _ in range((rot % 360) // 90):
        x, z = -z, x  # clockwise from above: east(+x) -> south(+z)
    return x, y, z


# --- regeneration ---------------------------------------------------------------------

def regen(server_jar: Optional[str] = None) -> None:
    """Rebuild blockstates_1_21.json from the server jar's data reports."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    jar = server_jar or os.path.join(root, "simulation", "server", "server.jar")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["java", "-DbundlerMainClass=net.minecraft.data.Main", "-jar", jar,
                        "--reports", "--output", tmp], cwd=tmp, check=True, capture_output=True)
        with open(os.path.join(tmp, "reports", "blocks.json")) as f:
            raw = json.load(f)
    sets, set_ids, blocks = [], {}, {}
    for bid, v in sorted(raw.items()):
        props = {k: v["properties"][k] for k in sorted(v.get("properties", {}))}
        key = json.dumps(props, sort_keys=True)
        if key not in set_ids:
            set_ids[key] = len(sets)
            sets.append(props)
        dflt = next((s.get("properties", {}) for s in v["states"] if s.get("default")), {})
        blocks[bid[len("minecraft:"):]] = [set_ids[key], ",".join(f"{k}={dflt[k]}" for k in sorted(dflt))]
    with open(DATA_PATH, "w") as f:
        json.dump({"version": "1.21.0", "sets": sets, "blocks": blocks}, f, separators=(",", ":"))
    print(f"wrote {DATA_PATH}: {len(blocks)} blocks, {len(sets)} property sets")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        regen(next((a for a in sys.argv[1:] if a.endswith(".jar")), None))
    else:
        print(__doc__)
