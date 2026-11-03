"""Text views of a build for an LLM: multi-resolution overview maps, ASCII slices, block diffs, timing diagrams.

Pure functions over the flat block format: `cells` is {(x, y, z): state} (canonical or short state strings) and
`prov` (optional) {(x, y, z): group name}. No simulator, no project state; `mira_mcp/project.py` feeds them.
Every renderer bounds its output (rows/cols) and says when it clipped or downsampled.
"""
import math
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Pos = Tuple[int, int, int]

# class char, name, matcher on the short block id; first match wins. Order = priority when a map cell has a tie.
CLASSES = [
    ("+", "wire", lambda b: b == "redstone_wire"),
    (">", "repeater", lambda b: b == "repeater"),
    ("C", "comparator", lambda b: b == "comparator"),
    ("i", "torch", lambda b: b in ("redstone_torch", "redstone_wall_torch")),
    ("L", "lever", lambda b: b == "lever"),
    ("b", "button/plate", lambda b: b.endswith(("_button", "_pressure_plate"))),
    ("O", "lamp", lambda b: b == "redstone_lamp"),
    ("R", "redstone block", lambda b: b == "redstone_block"),
    ("P", "piston", lambda b: b in ("piston", "sticky_piston", "piston_head", "moving_piston")),
    ("B", "observer", lambda b: b == "observer"),
    ("H", "container", lambda b: b in ("hopper", "dropper", "dispenser", "chest", "trapped_chest", "barrel", "crafter")
     or b.endswith("shulker_box")),
    ("T", "target/note", lambda b: b in ("target", "note_block")),
    ("=", "rail", lambda b: b.endswith("rail")),
    ("~", "fluid", lambda b: b in ("water", "lava")),
    ("s", "slime/honey", lambda b: b in ("slime_block", "honey_block")),
    ("_", "slab", lambda b: b.endswith("_slab")),
    ("g", "glass", lambda b: "glass" in b),
    ("#", "solid/other", lambda b: True),
]
COMPONENT_CHARS = set("+>CiLbORPBHT=s")
CLASS_NAME = {c: n for c, n, _ in CLASSES}
_PRIO = {c: i for i, (c, _, _) in enumerate(CLASSES)}
FLOW = {"west": ">", "east": "<", "south": "^", "north": "v"}   # repeater/comparator facing (input side) -> flow arrow
FACING_ARROW = {"east": ">", "west": "<", "north": "^", "south": "v", "up": "^", "down": "v"}


def split(state: str) -> Tuple[str, Dict[str, str]]:
    """'minecraft:repeater[delay=1,facing=east]' -> ('repeater', {'delay': '1', 'facing': 'east'})."""
    bid, _, rest = state.partition("[")
    props = dict(kv.split("=", 1) for kv in rest.rstrip("]").split(",") if "=" in kv)
    return bid.split(":", 1)[-1], props


def short(state: str, keep: Sequence[str] = ("facing", "delay", "mode", "face", "type", "lit", "powered", "power",
                                             "extended", "half", "axis")) -> str:
    """Compact state for listings: id plus the properties that usually matter for redstone."""
    bid, props = split(state)
    if bid == "redstone_wire":
        keep = ("power",)
    kept = [f"{k}={v}" for k, v in props.items() if k in keep]
    return bid + (f"[{','.join(kept)}]" if kept else "")


def klass(state: str) -> str:
    bid = split(state)[0]
    for ch, _, m in CLASSES:
        if m(bid):
            return ch
    return "#"


def glyph(state: str) -> str:
    """Full-resolution glyph: class char, but repeaters/comparators/observers/pistons show their direction."""
    bid, props = split(state)
    if bid in ("repeater", "comparator"):
        arrow = FLOW.get(props.get("facing", ""), "?")
        return arrow if bid == "repeater" else {">": "}", "<": "{", "^": "A", "v": "V"}.get(arrow, "C")
    if bid == "redstone_lamp" and props.get("lit") == "true":
        return "@"
    if bid == "redstone_wall_torch":
        return "t"
    return klass(state)


def bounds(cells: Iterable[Pos]):
    cells = list(cells)
    if not cells:
        return None
    return tuple(min(c[i] for c in cells) for i in range(3)), tuple(max(c[i] for c in cells) for i in range(3))


def palette_line(cells: Dict[Pos, str], top: int = 10) -> str:
    cnt = Counter(split(s)[0] for s in cells.values())
    items = [f"{b} {n}" for b, n in cnt.most_common(top)]
    more = len(cnt) - top
    return ", ".join(items) + (f", ... (+{more} kinds)" if more > 0 else "") + f"  [{len(cnt)} kinds]"


def _dominant(chars: Counter) -> str:
    comps = {c: n for c, n in chars.items() if c in COMPONENT_CHARS}
    pool = comps or chars
    return max(pool, key=lambda c: (pool[c], -_PRIO.get(c, 99)))


def _scale(span: int, cap: int) -> int:
    return max(1, math.ceil(span / cap))


def _ruler(lo: int, n: int, step: int, indent: int) -> List[str]:
    """Two header lines labelling every 10th column with its world coordinate (left edge of the column)."""
    marks = [" "] * n
    for c in range(n):
        v = lo + c * step
        if c % 10 == 0:
            s = str(v)
            for k, ch in enumerate(s):
                if c + k < n:
                    marks[c + k] = ch
    return [" " * indent + "".join(marks)]


def top_map(cells: Dict[Pos, str], groups: Optional[Dict[Pos, str]] = None, max_cols: int = 60, max_rows: int = 18,
            letters: Optional[Dict[str, str]] = None, background: str = "(fixed)") -> Tuple[List[str], str, set]:
    """Downsampled top-down map (x -> columns, z -> rows). Each char = dominant block class in that x-z cell over all y
    (redstone components win over plain blocks). With `groups` a second map of module letters is put beside it
    (or below when too wide); the `background` group (task scenery) only shows where nothing else is.
    Returns (lines, scale note, class chars used)."""
    bb = bounds(cells)
    if bb is None:
        return ["(empty)"], "", set()
    (x0, _, z0), (x1, _, z1) = bb
    sx, sz = _scale(x1 - x0 + 1, max_cols), _scale(z1 - z0 + 1, max_rows)
    nc, nr = (x1 - x0) // sx + 1, (z1 - z0) // sz + 1
    cls = defaultdict(Counter)
    grp = defaultdict(Counter)
    for (x, y, z), st in cells.items():
        k = ((x - x0) // sx, (z - z0) // sz)
        cls[k][klass(st)] += 1
        if groups is not None:
            grp[k][groups.get((x, y, z), "?")] += 1
    zw = max(len(str(z0)), len(str(z1)))
    left = []
    for r in range(nr):
        row = "".join(_dominant(cls[(c, r)]) if (c, r) in cls else "." for c in range(nc))
        left.append(row)
    right = None
    def top_group(cnt: Counter) -> str:
        fg = [(n, g) for g, n in cnt.items() if g != background]
        return max(fg)[1] if fg else background
    if groups is not None and letters:
        right = []
        for r in range(nr):
            right.append("".join(letters.get(top_group(grp[(c, r)]), "?") if (c, r) in grp else "."
                                 for c in range(nc)))
    head = _ruler(x0, nc, sx, zw + 1)
    lines = []
    side = right is not None and 2 * nc + zw + 4 <= 130
    if side:
        lines.append(head[0] + "   " + head[0][zw + 1:])
    else:
        lines += head
    for r in range(nr):
        z = z0 + r * sz
        line = f"{z:>{zw}} {left[r]}"
        if side:
            line += "   " + right[r]
        lines.append(line)
    if right is not None and not side:
        lines.append("modules:")
        lines += [f"{z0 + r * sz:>{zw}} {right[r]}" for r in range(nr)]
    note = f"1 char = {sx}x{sz} blocks (x by z)" if (sx, sz) != (1, 1) else "1 char = 1 block"
    return lines, note, set("".join(left))


def side_map(cells: Dict[Pos, str], max_cols: int = 60, max_rows: int = 10) -> Tuple[List[str], str]:
    """Side elevation: x -> columns, y -> rows (top row = highest), dominant class over all z."""
    bb = bounds(cells)
    if bb is None:
        return ["(empty)"], ""
    (x0, y0, _), (x1, y1, _) = bb
    sx, sy = _scale(x1 - x0 + 1, max_cols), _scale(y1 - y0 + 1, max_rows)
    nc, nr = (x1 - x0) // sx + 1, (y1 - y0) // sy + 1
    cls = defaultdict(Counter)
    for (x, y, z), st in cells.items():
        cls[((x - x0) // sx, (y - y0) // sy)][klass(st)] += 1
    yw = max(len(str(y0)), len(str(y1)))
    lines = _ruler(x0, nc, sx, yw + 1)
    for r in reversed(range(nr)):
        lines.append(f"{y0 + r * sy:>{yw}} " + "".join(_dominant(cls[(c, r)]) if (c, r) in cls else "." for c in range(nc)))
    note = f"1 char = {sx}x{sy} blocks (x by y)" if (sx, sy) != (1, 1) else "1 char = 1 block"
    return lines, note


def legend(chars: Iterable[str]) -> str:
    by = {}
    for c in sorted(set(chars) - {".", " "}, key=lambda c: _PRIO.get(c, 99)):
        by.setdefault(GLYPH_EXTRA.get(c) or CLASS_NAME.get(c, "?"), []).append(c)
    hint = {"repeater": " (arrow = signal flow)", "comparator": " (} { A V = flow east/west/north/south)"}
    return "  ".join(f"{''.join(cs)}={n}{hint.get(n, '') if len(cs) > 1 or cs[0] != 'C' else ''}" for n, cs in by.items())


GLYPH_EXTRA = {">": "repeater", "<": "repeater", "^": "repeater", "v": "repeater", "}": "comparator", "{": "comparator",
               "A": "comparator", "V": "comparator", "@": "lit lamp", "t": "wall torch"}


def slice_view(cells: Dict[Pos, str], y: int, box: Optional[Sequence[int]] = None, max_cols: int = 100,
               max_rows: int = 60, marks: Optional[Dict[Tuple[int, int], str]] = None) -> str:
    """Full-resolution ASCII slice at height y. box = (x0, z0, x1, z1) (default: the bbox of all cells).
    Repeaters/comparators show the flow direction (repeater > < ^ v, comparator } { A V). Clips with a notice."""
    bb = bounds(cells)
    if bb is None:
        return "(empty build)"
    if box is None:
        x0, z0, x1, z1 = bb[0][0], bb[0][2], bb[1][0], bb[1][2]
    else:
        x0, z0, x1, z1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    notes = []
    if x1 - x0 + 1 > max_cols:
        notes.append(f"x clipped to {x0}..{x0 + max_cols - 1} of {x0}..{x1}")
        x1 = x0 + max_cols - 1
    if z1 - z0 + 1 > max_rows:
        notes.append(f"z clipped to {z0}..{z0 + max_rows - 1} of {z0}..{z1}")
        z1 = z0 + max_rows - 1
    layer = {(x, z): s for (x, yy, z), s in cells.items() if yy == y and x0 <= x <= x1 and z0 <= z <= z1}
    zw = max(len(str(z0)), len(str(z1)))
    out = [f"layer y={y}  x {x0}..{x1} (columns)  z {z0}..{z1} (rows)  {len(layer)} blocks"]
    tens = "".join(str(abs(x) // 10 % 10) if (x % 10 == 0 or x == x0) else " " for x in range(x0, x1 + 1))
    out.append(" " * (zw + 1) + tens)
    out.append(" " * (zw + 1) + "".join(str(abs(x) % 10) for x in range(x0, x1 + 1)))
    used = set()
    for z in range(z0, z1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            ch = (marks or {}).get((x, z)) or (glyph(layer[(x, z)]) if (x, z) in layer else ".")
            used.add(ch)
            row.append(ch)
        out.append(f"{z:>{zw}} " + "".join(row))
    out.append("legend: " + legend(used))
    if notes:
        out.append("CLIPPED: " + "; ".join(notes) + " -> pass --box x0 z0 x1 z1 to see the rest")
    return "\n".join(out)


def axis_view(cells: Dict[Pos, str], axis: str, at: int, box: Optional[Sequence[int]] = None, max_cols: int = 100,
              max_rows: int = 60) -> str:
    """Full-resolution slice perpendicular to `axis` at coordinate `at`: axis 'y' = the layer view (box x0 z0 x1 z1);
    'x' = columns z, rows y (top = highest; box z0 y0 z1 y1); 'z' = columns x, rows y (box x0 y0 x1 y1)."""
    if axis == "y":
        return slice_view(cells, at, box, max_cols, max_rows)
    if axis not in ("x", "z"):
        return f"error: axis must be x, y or z, got {axis!r}"
    bb = bounds(cells)
    if bb is None:
        return "(empty build)"
    ci, name_c = (2, "z") if axis == "x" else (0, "x")      # horizontal axis of the picture
    layer = {(p[ci], p[1]): s for p, s in cells.items() if p[0 if axis == "x" else 2] == at}
    c0, c1, r0, r1 = bb[0][ci], bb[1][ci], bb[0][1], bb[1][1]
    if box is not None:
        c0, r0, c1, r1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    notes = []
    if c1 - c0 + 1 > max_cols:
        notes.append(f"{name_c} clipped to {c0}..{c0 + max_cols - 1} of {c0}..{c1}")
        c1 = c0 + max_cols - 1
    if r1 - r0 + 1 > max_rows:
        notes.append(f"y clipped to {r1 - max_rows + 1}..{r1} of {r0}..{r1}")
        r0 = r1 - max_rows + 1
    sel = {k: v for k, v in layer.items() if c0 <= k[0] <= c1 and r0 <= k[1] <= r1}
    yw = max(len(str(r0)), len(str(r1)))
    out = [f"slice {axis}={at}  {name_c} {c0}..{c1} (columns)  y {r0}..{r1} (rows, top = highest)  {len(sel)} blocks"]
    out.append(" " * (yw + 1) + "".join(str(abs(c) // 10 % 10) if (c % 10 == 0 or c == c0) else " " for c in range(c0, c1 + 1)))
    out.append(" " * (yw + 1) + "".join(str(abs(c) % 10) for c in range(c0, c1 + 1)))
    used = set()
    for r in range(r1, r0 - 1, -1):
        row = []
        for c in range(c0, c1 + 1):
            ch = glyph(sel[(c, r)]) if (c, r) in sel else "."
            used.add(ch)
            row.append(ch)
        out.append(f"{r:>{yw}} " + "".join(row))
    out.append("legend: " + legend(used))
    if notes:
        out.append("CLIPPED: " + "; ".join(notes) + f" -> pass --box {name_c}0 y0 {name_c}1 y1")
    return "\n".join(out)


def raw_vs_settled(raw: Dict[Pos, str], settled: Dict[Pos, str], examples: int = 3, tile: int = 16) -> str:
    """Which cells did settling (placement + running to rest) change relative to the raw program? Grouped by block kind,
    with the property-level change that dominates each kind."""
    added, removed, changed = diff_cells(raw, settled)
    if not (added or removed or changed):
        return "raw program == settled world (nothing changes on the way to rest)"
    by = defaultdict(lambda: {"n": 0, "what": Counter(), "ex": []})
    key = lambda p: (p[1], p[2], p[0])
    for p in sorted(changed, key=key):
        old, new = changed[p]
        kind = split(old)[0] if split(old)[0] == split(new)[0] else f"{split(old)[0]}->{split(new)[0]}"
        d = by[kind]
        d["n"] += 1
        d["what"][state_change(old, new)] += 1
        if len(d["ex"]) < examples:
            d["ex"].append(f"    {list(p)} {state_change(old, new)}")
    for p in sorted(removed, key=key):
        d = by[split(removed[p])[0] + " (removed)"]
        d["n"] += 1
        d["what"]["removed"] += 1
        if len(d["ex"]) < examples:
            d["ex"].append(f"    {list(p)} {short(removed[p])} removed")
    for p in sorted(added, key=key):
        d = by[split(added[p])[0] + " (added)"]
        d["n"] += 1
        d["what"]["added"] += 1
        if len(d["ex"]) < examples:
            d["ex"].append(f"    {list(p)} {short(added[p])} added")
    L = [f"settling changed {len(changed) + len(removed) + len(added)} of {len(raw)} cells "
         f"(~{len(changed)} changed, +{len(added)} added, -{len(removed)} removed), by block kind:"]
    for kind, d in sorted(by.items(), key=lambda kv: -kv[1]["n"])[:14]:
        top = "; ".join(f"{w} x{n}" for w, n in d["what"].most_common(2))
        L.append(f"  {kind}: {d['n']}  ({top})")
        L += d["ex"]
    if len(by) > 14:
        L.append(f"  ... (+{len(by) - 14} more kinds)")
    tiles = Counter((p[0] // tile * tile, p[2] // tile * tile) for p in [*changed, *removed, *added])
    L.append("by region (x-z tiles): " + "; ".join(f"x{x}..{x + tile - 1} z{z}..{z + tile - 1}: {n}" for (x, z), n in tiles.most_common(5)))
    return "\n".join(L)


def group_key(path: str) -> str:
    """Instance path -> module group: first path segment with array indices / trailing digits collapsed
    ('fa3/cell' -> 'fa#', 'row[2]' -> 'row[]')."""
    if path.startswith("net:"):      # router wiring (Part.route): one group, `nets` lists the individual nets
        return "(nets)"
    head = path.split("/", 1)[0]
    head = re.sub(r"\[\d+\]", "[]", head)
    return re.sub(r"\d+$", "#", head)


def compress_names(names: Sequence[str], limit: int = 12) -> List[str]:
    """['A0','A1',...,'A7','B0'] -> ['A0..A7', 'B0']: runs of names that differ only in one integer."""
    pat = defaultdict(list)
    order = []
    for n in names:
        m = list(re.finditer(r"\d+", n))
        if m:
            k = (n[:m[-1].start()], n[m[-1].end():])
            if k not in pat:
                order.append(k)
            pat[k].append((int(m[-1].group()), n))
        else:
            order.append(n)
    out = []
    for k in order:
        if isinstance(k, str):
            out.append(k)
            continue
        vals = sorted(pat[k])
        if len(vals) <= 2:
            out += [n for _, n in vals]
        else:
            nums = [v for v, _ in vals]
            contiguous = nums == list(range(nums[0], nums[0] + len(nums)))
            out.append(f"{k[0]}{{{nums[0]}..{nums[-1]}}}{k[1]}" if contiguous else f"{k[0]}{{{','.join(map(str, nums))}}}{k[1]}")
    if len(out) > limit:
        out = out[:limit] + [f"... (+{len(out) - limit} more)"]
    return out


def diff_cells(a: Dict[Pos, str], b: Dict[Pos, str]):
    """-> (added {pos: new}, removed {pos: old}, changed {pos: (old, new)})."""
    added = {p: s for p, s in b.items() if p not in a}
    removed = {p: s for p, s in a.items() if p not in b}
    changed = {p: (a[p], b[p]) for p in a.keys() & b.keys() if a[p] != b[p]}
    return added, removed, changed


def state_change(old: str, new: str) -> str:
    bo, po = split(old)
    bn, pn = split(new)
    if bo != bn:
        return f"{short(old)} -> {short(new)}"
    ch = [f"{k}={po.get(k)}->{pn.get(k)}" for k in sorted(set(po) | set(pn)) if po.get(k) != pn.get(k)]
    return f"{bo}[{', '.join(ch)}]"


def diff_text(a: Dict[Pos, str], b: Dict[Pos, str], group_a=None, group_b=None, examples: int = 10, tile: int = 16) -> str:
    """Block-level diff summarised by module group (group_*: pos -> name) and by x-z tile, plus examples."""
    added, removed, changed = diff_cells(a, b)
    L = [f"diff: +{len(added)} added, -{len(removed)} removed, ~{len(changed)} changed "
         f"({len(a)} -> {len(b)} blocks)"]
    if not (added or removed or changed):
        return L[0] + "  (identical)"
    by = defaultdict(lambda: [0, 0, 0])
    for p in added:
        by[(group_b or {}).get(p, "?")][0] += 1
    for p in removed:
        by[(group_a or {}).get(p, "?")][1] += 1
    for p in changed:
        by[(group_b or {}).get(p, "?")][2] += 1
    if group_a or group_b:
        L.append("by module: " + "; ".join(f"{g} +{v[0]} -{v[1]} ~{v[2]}" for g, v in sorted(by.items(), key=lambda kv: -sum(kv[1]))[:12]))
    tiles = Counter()
    for p in list(added) + list(removed) + list(changed):
        tiles[(p[0] // tile * tile, p[2] // tile * tile)] += 1
    L.append(f"by region ({tile}x{tile} x-z tiles, origin -> edits): " + "; ".join(
        f"x{x}..{x + tile - 1} z{z}..{z + tile - 1}: {n}" for (x, z), n in tiles.most_common(8))
        + (f"; ... (+{len(tiles) - 8} tiles)" if len(tiles) > 8 else ""))
    key = lambda p: (p[1], p[2], p[0])
    ex = [f"  + {list(p)} {short(added[p])}" for p in sorted(added, key=key)[:examples]]
    ex += [f"  - {list(p)} {short(removed[p])}" for p in sorted(removed, key=key)[:examples]]
    ex += [f"  ~ {list(p)} {state_change(*changed[p])}" for p in sorted(changed, key=key)[:examples]]
    L.append(f"examples (up to {examples} per kind):")
    L += ex
    return "\n".join(L)


def timing_diagram(signals: Dict[str, List[str]], marks: Optional[Dict[int, str]] = None, width: int = 100) -> str:
    """ASCII waveforms. signals: name -> one value char per tick ('0'/'1' or a hex power digit or a state letter).
    Ticks are downsampled to `width` columns; a column whose window holds several values shows '*'.
    marks: tick -> short label printed on a marker row (stimulus boundaries)."""
    n = max((len(v) for v in signals.values()), default=0)
    if n == 0:
        return "(no ticks)"
    k = max(1, math.ceil(n / width))
    cols = math.ceil(n / k)
    nw = max(len(s) for s in signals)

    def draw(v):
        out = []
        for c in range(cols):
            w = set(v[c * k:(c + 1) * k])
            ch = "*" if len(w) > 1 else (w.pop() if w else " ")
            out.append({"0": "_", "1": "#"}.get(ch, ch))
        return "".join(out)
    L = [f"ticks 0..{n - 1}, 1 column = {k} tick{'s' if k > 1 else ''}; _ = 0, # = 1, hex = wire power, * = changed inside the column"]
    if marks:
        row = [" "] * cols
        for t, lab in sorted(marks.items()):
            c = t // k
            if c < cols:
                row[c] = "|"
                for j, ch in enumerate(lab[:6]):
                    if c + 1 + j < cols and row[c + 1 + j] == " ":
                        row[c + 1 + j] = ch
        L.append(" " * (nw + 1) + "".join(row))
    ruler = [" "] * cols
    for c in range(0, cols, 10):
        for j, ch in enumerate(str(c * k)):
            if c + j < cols:
                ruler[c + j] = ch
    L.append(" " * (nw + 1) + "".join(ruler))
    for name, v in signals.items():
        L.append(f"{name:>{nw}} " + draw(v))
    return "\n".join(L)


def edges(v: List[str], limit: int = 12) -> str:
    """RLE 'value@tick' of a value string, capped."""
    out, last = [], None
    for t, ch in enumerate(v):
        if ch != last:
            out.append(f"{ch}@{t}")
            last = ch
    return " ".join(out[:limit]) + (f" ... (+{len(out) - limit})" if len(out) > limit else "")
