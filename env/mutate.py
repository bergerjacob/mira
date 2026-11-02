"""Broken variants of a reference solution (for task validation and Repair-style data).

A mutant is {"name", "kind", "edits": [("remove", pos) | ("set", pos, state)]} applied to the flat block list."""
import random

from env import blocks as B

TURN = {"north": "east", "east": "south", "south": "west", "west": "north"}


def apply(solution, edits):
    """solution: [[x,y,z,state,...]] -> new flat list with edits applied."""
    cells = {(b[0], b[1], b[2]): b[3] for b in solution}
    for e in edits:
        if e[0] == "remove":
            cells.pop(tuple(e[1]), None)
        else:
            cells[tuple(e[1])] = e[2]
    return [[x, y, z, s] for (x, y, z), s in cells.items()]


def candidates(solution, build_box=None):
    """All single-edit mutants of a solution, grouped by kind."""
    cells = {(b[0], b[1], b[2]): B.canonical(b[3]) for b in solution}
    out = []
    for pos, st in sorted(cells.items()):
        bid, props = B.split_state(st)
        name = B.short(bid)
        out.append({"name": f"remove {name} {list(pos)}", "kind": "remove", "edits": [("remove", pos)]})
        f = props.get("facing")
        if f in TURN:
            for tag, nf in (("flip", B.OPPOSITE[f]), ("turn", TURN[f])):
                new = B.join_state(bid, {**props, "facing": nf})
                out.append({"name": f"{tag} {name} {list(pos)} {f}->{nf}", "kind": "facing", "edits": [("set", pos, new)]})
        if name == "repeater":
            d = int(props["delay"])
            nd = d + 1 if d < 4 else d - 1
            out.append({"name": f"repeater {list(pos)} delay {d}->{nd}", "kind": "delay",
                        "edits": [("set", pos, B.join_state(bid, {**props, "delay": str(nd)}))]})
        if name in ("redstone_torch", "redstone_wall_torch"):
            out.append({"name": f"replace torch {list(pos)} with stone", "kind": "swap",
                        "edits": [("set", pos, "minecraft:stone")]})
        for dname in ("north", "south", "east", "west"):
            dx, _, dz = B.DIRS[dname]
            np_ = (pos[0] + dx, pos[1], pos[2] + dz)
            if np_ in cells or (build_box and not all(build_box[i] <= np_[i] <= build_box[i + 3] for i in range(3))):
                continue
            out.append({"name": f"shift {name} {list(pos)} {dname}", "kind": "shift",
                        "edits": [("remove", pos), ("set", np_, st)]})
            break  # one shift direction per block keeps the candidate list small
        if name == "redstone_wire":
            out.append({"name": f"replace wire {list(pos)} with stone", "kind": "swap", "edits": [("set", pos, "minecraft:stone")]})
    return out


def pick(solution, build_box=None, n=3, seed=0):
    """Deterministically order candidates round-robin across kinds, so a validator tries diverse breakages first."""
    rng = random.Random(seed)
    by = {}
    for c in candidates(solution, build_box):
        by.setdefault(c["kind"], []).append(c)
    for v in by.values():
        rng.shuffle(v)
    order = []
    while any(by.values()):
        for k in sorted(by):
            if by[k]:
                order.append(by[k].pop())
    return order
