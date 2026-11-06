"""Hand-over files for a build: settled at-rest `.litematic`, vanilla structure `.nbt`, and a vanilla paste receipt.

    cells, info = settle(world)                          # Nucleation: canonical placement (env/sim.py), run to rest
    save_litematic(cells, "out.litematic", nbt, ents)    # via Nucleation; read back and compared
    write_structure_nbt(cells, "out.nbt", nbt)           # our own gzip NBT writer (Nucleation 0.12 cannot write vanilla .nbt)
    receipt = paste_check("out.litematic", ticks=40)     # vanilla 26.2 oracle: place the file, idle, read back, diff

`world` / `cells` are {(x, y, z): state}; nbt is {(x, y, z): snbt}. Coordinates are kept as given; files are written
with the min corner at (0, 0, 0). Only `paste_check` needs the oracle (tools/oracle, `scripts/oracle_setup.sh`).
"""
import gzip
import hashlib
import json
import os
import struct
import sys
from typing import Dict, List, Optional, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

Pos = Tuple[int, int, int]
DATA_VERSION = 4903   # Minecraft 26.2 (the oracle's version); see docs/ORACLE.md


def _is_air(state: str) -> bool:
    return state.split("[", 1)[0].split(":")[-1] in ("air", "cave_air", "void_air")


def canon(state: str) -> str:
    """minecraft: prefix, properties sorted (the oracle's canonical form)."""
    bid, _, rest = state.partition("[")
    if ":" not in bid:
        bid = "minecraft:" + bid
    props = sorted(kv for kv in rest.rstrip("]").split(",") if kv)
    return bid + (f"[{','.join(props)}]" if props else "")


# --------------------------------------------------------------------------- settle (Nucleation)
def settle(world: Dict[Pos, str], budget: int = 400) -> Tuple[Dict[Pos, str], dict]:
    """Place `world` the way the env does (env/sim.py: canonical = raw states + cross dust, loaded with Placement, as a structure
    / Litematica paste; MIRA_PLACEMENT=legacy = per-block engine placement), step until quiescent
    (at most `budget` ticks) and return the at-rest states. info: {ticks, at_rest, place_errors, changed}.
    Raises RuntimeError when the simulator cannot load the build (e.g. one of the 263 unloadable block ids)."""
    from env.sim import Sim, compile_cells
    sc, corner, errs = compile_cells(world)
    try:
        sim = Sim(sc, corner)
    except Exception as e:
        raise RuntimeError(f"simulator cannot load the build: {e}") from None
    ticks, rest = sim.settle(budget)
    snap = {p: s for p, s in sim.snapshot().items() if not _is_air(s)}
    changed = sum(1 for p, s in snap.items() if canon(world.get(p, "minecraft:air")) != canon(s))
    changed += sum(1 for p in world if p not in snap)
    return snap, {"ticks": ticks, "at_rest": rest, "place_errors": [f"{list(p)} {s}: {m}" for p, s, m in errs[:5]],
                  "changed": changed}


def path_check(world: Dict[Pos, str], baked: Dict[Pos, str], budget: int = 400) -> dict:
    """Is the at-rest state placement-path sensitive? Bistable parts (latches, locked-repeater flip-flops) can come up
    differently depending on how the blocks arrive. Compares the baked world (env placement + settle, what `settle`
    returns and what we export) with (a) the RAW program states pasted then settled (`raw_<mode>`) and (b) the baked
    world re-loaded and settled (`baked_<mode>`, = the env's paste_stable invariant), for Nucleation's InWorld and
    Placement modes. Values are numbers of differing cells (-1 = simulator error / never at rest). raw_inworld is
    nearly always > 0 (a quiet paste of raw states leaves dust unconnected); `sensitive` = raw_placement != 0."""
    from env.sim import Sim, compile_cells
    out = {}
    for label, src in (("raw", world), ("baked", baked)):
        sc, corner, _ = compile_cells(src, engine_place=False)
        for mode in ("inworld", "placement"):
            try:
                sim = Sim(sc, corner, mode)
                _, ok = sim.settle(budget)
                snap = {p: canon(s) for p, s in sim.snapshot().items() if not _is_air(s)}
            except Exception:   # noqa: BLE001
                out[f"{label}_{mode}"] = -1
                continue
            want = {p: canon(s) for p, s in baked.items()}
            out[f"{label}_{mode}"] = sum(1 for p in set(snap) | set(want) if snap.get(p) != want.get(p)) if ok else -1
    out["sensitive"] = out.get("raw_placement") != 0     # even a full update pass of the raw states lands elsewhere
    out["paste_stable"] = all(v == 0 for k, v in out.items() if k.startswith("baked_"))
    return out


# --------------------------------------------------------------------------- .litematic (Nucleation)
def save_litematic(cells: Dict[Pos, str], path: str, nbt: Optional[Dict[Pos, str]] = None,
                   entities: Optional[list] = None, name: str = "mira") -> dict:
    """Write cells (+ block-entity SNBT, + entities (x, y, z, id, snbt)) as .litematic with the min corner at 0,0,0,
    read the file back through Nucleation and compare. Returns {blocks, origin, sha256, readback_mismatches}."""
    import nucleation
    from simulation.builder import _snbt_ser, parse_snbt
    if not cells:
        raise ValueError("nothing to export (no blocks)")
    mn = tuple(min(p[i] for p in cells) for i in range(3))
    sc = nucleation.Schematic.create(name)
    for (x, y, z), st in sorted(cells.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        lx, ly, lz = x - mn[0], y - mn[1], z - mn[2]
        sc.set_block(lx, ly, lz, st)
        if nbt and (x, y, z) in nbt and nbt[(x, y, z)]:
            tree = parse_snbt(nbt[(x, y, z)])
            tree.setdefault("id", ("str", canon(st).split("[")[0]))
            sc.set_block_entity(lx, ly, lz, tree["id"][1], _snbt_ser(tree))
    for ex, ey, ez, eid, enbt in entities or []:
        tree = parse_snbt(enbt) if enbt else {}
        tree["id"] = ("str", eid)
        tree["Pos"] = [("num", f"{v - o!r}D") for v, o in zip((ex, ey, ez), mn)]
        sc.add_entity_from_snbt(_snbt_ser(tree))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sc.save_to_file_with_format(path, "litematic", "")
    back = read_blocks(path)
    want = {(x - mn[0], y - mn[1], z - mn[2]): canon(s) for (x, y, z), s in cells.items()}
    bad = [p for p in set(want) | set(back) if want.get(p) != back.get(p)]
    actual_version = read_nbt(path).get("MinecraftDataVersion")  # actual written tag, not the writer's intended/default version
    return {"blocks": len(cells), "origin": list(mn), "sha256": sha256(path), "data_version": actual_version, "readback_mismatches": len(bad),
            "readback_examples": [f"{list(p)} want {want.get(p)} file {back.get(p)}" for p in sorted(bad)[:3]]}


def read_blocks(path: str) -> Dict[Pos, str]:
    """Non-air blocks of a .litematic/.schem as {pos: canonical state} (file coordinates)."""
    from nucleation import Schematic
    sch = Schematic.load_from_file(str(path))
    out = {}
    for e in json.loads(sch.get_all_blocks_json()):
        if _is_air(e["name"]):
            continue
        props = ",".join(f"{k}={v}" for k, v in e.get("properties", []))
        out[(e["x"], e["y"], e["z"])] = canon(e["name"] + (f"[{props}]" if props else ""))
    return out


def sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------------------- vanilla structure .nbt (own writer)
_TAG = {"end": 0, "byte": 1, "short": 2, "int": 3, "long": 4, "float": 5, "double": 6, "byte_array": 7, "string": 8,
        "list": 9, "compound": 10, "int_array": 11, "long_array": 12}


class T:
    """A typed NBT value for the writer: T("int", 3), T("list", [T(...), ...]), T("compound", {k: T}), ..."""
    __slots__ = ("tag", "v")

    def __init__(self, tag: str, v):
        self.tag, self.v = tag, v

    def __eq__(self, o):
        return isinstance(o, T) and (self.tag, self.v) == (o.tag, o.v)

    def __repr__(self):
        return f"T({self.tag!r}, {self.v!r})"


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _payload(t: T) -> bytes:
    tag, v = t.tag, t.v
    if tag == "byte":
        return struct.pack(">b", v)
    if tag == "short":
        return struct.pack(">h", v)
    if tag == "int":
        return struct.pack(">i", v)
    if tag == "long":
        return struct.pack(">q", v)
    if tag == "float":
        return struct.pack(">f", v)
    if tag == "double":
        return struct.pack(">d", v)
    if tag == "string":
        return _str(v)
    if tag in ("byte_array", "int_array", "long_array"):
        fmt = {"byte_array": ">b", "int_array": ">i", "long_array": ">q"}[tag]
        return struct.pack(">i", len(v)) + b"".join(struct.pack(fmt, x) for x in v)
    if tag == "list":
        et = v[0].tag if v else "end"
        if any(x.tag != et for x in v):
            raise ValueError(f"NBT list with mixed element types: {sorted({x.tag for x in v})}")
        return struct.pack(">bi", _TAG[et], len(v)) + b"".join(_payload(x) for x in v)
    if tag == "compound":
        out = b""
        for k, x in v.items():
            out += struct.pack(">b", _TAG[x.tag]) + _str(k) + _payload(x)
        return out + b"\x00"
    raise ValueError(f"unknown NBT tag {tag}")


def nbt_bytes(root: T, name: str = "") -> bytes:
    return struct.pack(">b", _TAG["compound"]) + _str(name) + _payload(root)


def from_snbt(text: str) -> T:
    """SNBT (as stored on builder blocks) -> typed tree, using the builder's SNBT parser."""
    from simulation.builder import parse_snbt
    return _from_tree(parse_snbt(text))


def _from_tree(v) -> T:
    if isinstance(v, dict):
        return T("compound", {k: _from_tree(x) for k, x in v.items()})
    if isinstance(v, list):
        return T("list", [_from_tree(x) for x in v])
    kind = v[0]
    if kind == "str":
        return T("string", v[1])
    if kind == "array":
        tag = {"B": "byte_array", "I": "int_array", "L": "long_array"}[v[1]]
        return T(tag, [int(_from_tree(x).v) for x in v[2]])
    tok = v[1]
    if tok in ("true", "false"):
        return T("byte", 1 if tok == "true" else 0)
    suf = tok[-1].upper()
    if suf in "BSL" and not tok[-2:-1].isalpha():
        return T({"B": "byte", "S": "short", "L": "long"}[suf], int(tok[:-1]))
    if suf in "FD":
        return T("float" if suf == "F" else "double", float(tok[:-1]))
    if "." in tok or "e" in tok.lower():
        return T("double", float(tok))
    return T("int", int(tok))


def structure_tree(cells: Dict[Pos, str], nbt: Optional[Dict[Pos, str]] = None, entities: Optional[list] = None,
                   data_version: int = DATA_VERSION) -> T:
    """Vanilla structure template (what a structure block saves): DataVersion, size, palette (Name/Properties),
    blocks (pos, state index, nbt), entities. Air is not stored (pasting leaves those cells untouched)."""
    mn = tuple(min(p[i] for p in cells) for i in range(3))
    mx = tuple(max(p[i] for p in cells) for i in range(3))
    palette: Dict[str, int] = {}
    blocks = []
    for (x, y, z), st in sorted(cells.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        st = canon(st)
        idx = palette.setdefault(st, len(palette))
        row = {"pos": T("list", [T("int", x - mn[0]), T("int", y - mn[1]), T("int", z - mn[2])]), "state": T("int", idx)}
        if nbt and nbt.get((x, y, z)):
            tag = from_snbt(nbt[(x, y, z)])
            tag.v.setdefault("id", T("string", st.split("[")[0]))
            row["nbt"] = tag
        blocks.append(T("compound", row))
    pal = []
    for st in palette:
        name, _, rest = st.partition("[")
        entry = {"Name": T("string", name)}
        props = [kv.split("=", 1) for kv in rest.rstrip("]").split(",") if kv]
        if props:
            entry["Properties"] = T("compound", {k: T("string", v) for k, v in props})
        pal.append(T("compound", entry))
    ents = []
    for ex, ey, ez, eid, enbt in entities or []:
        tag = from_snbt(enbt) if enbt else T("compound", {})
        tag.v["id"] = T("string", eid)
        lx, ly, lz = ex - mn[0], ey - mn[1], ez - mn[2]
        tag.v["Pos"] = T("list", [T("double", lx), T("double", ly), T("double", lz)])
        ents.append(T("compound", {"pos": T("list", [T("double", lx), T("double", ly), T("double", lz)]),
                                   "blockPos": T("list", [T("int", int(lx // 1)), T("int", int(ly // 1)), T("int", int(lz // 1))]),
                                   "nbt": tag}))
    return T("compound", {
        "DataVersion": T("int", data_version),
        "size": T("list", [T("int", mx[i] - mn[i] + 1) for i in range(3)]),
        "palette": T("list", pal),
        "blocks": T("list", blocks),
        "entities": T("list", ents),
    })


def write_structure_nbt(cells: Dict[Pos, str], path: str, nbt: Optional[Dict[Pos, str]] = None,
                        entities: Optional[list] = None, data_version: int = DATA_VERSION) -> dict:
    data = gzip.compress(nbt_bytes(structure_tree(cells, nbt, entities, data_version)), mtime=0)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    back = read_structure_nbt(path)
    mn = tuple(min(p[i] for p in cells) for i in range(3))
    want = {(x - mn[0], y - mn[1], z - mn[2]): canon(s) for (x, y, z), s in cells.items()}
    bad = sum(1 for p in set(want) | set(back) if want.get(p) != back.get(p))
    return {"blocks": len(cells), "bytes": len(data), "sha256": sha256(path), "readback_mismatches": bad,
            "data_version": data_version}


# --- minimal reader (round-trip check of our own writer)
def _read(buf: bytes, i: int, tag: int):
    if tag == 1:
        return struct.unpack_from(">b", buf, i)[0], i + 1
    if tag == 2:
        return struct.unpack_from(">h", buf, i)[0], i + 2
    if tag == 3:
        return struct.unpack_from(">i", buf, i)[0], i + 4
    if tag == 4:
        return struct.unpack_from(">q", buf, i)[0], i + 8
    if tag == 5:
        return struct.unpack_from(">f", buf, i)[0], i + 4
    if tag == 6:
        return struct.unpack_from(">d", buf, i)[0], i + 8
    if tag in (7, 11, 12):
        n = struct.unpack_from(">i", buf, i)[0]
        size, fmt = {7: (1, ">b"), 11: (4, ">i"), 12: (8, ">q")}[tag]
        i += 4
        return [struct.unpack_from(fmt, buf, i + k * size)[0] for k in range(n)], i + n * size
    if tag == 8:
        n = struct.unpack_from(">H", buf, i)[0]
        return buf[i + 2:i + 2 + n].decode("utf-8"), i + 2 + n
    if tag == 9:
        et, n = struct.unpack_from(">bi", buf, i)
        i += 5
        out = []
        for _ in range(n):
            v, i = _read(buf, i, et)
            out.append(v)
        return out, i
    if tag == 10:
        out = {}
        while True:
            t = buf[i]
            i += 1
            if t == 0:
                return out, i
            k, i = _read(buf, i, 8)
            out[k], i = _read(buf, i, t)
    raise ValueError(f"bad NBT tag {tag}")


def read_nbt(path: str) -> dict:
    buf = gzip.decompress(open(path, "rb").read())
    if buf[0] != 10:
        raise ValueError("root is not a compound")
    _name, i = _read(buf, 1, 8)
    return _read(buf, i, 10)[0]


def read_structure_nbt(path: str) -> Dict[Pos, str]:
    d = read_nbt(path)
    pal = [canon(p["Name"] + ("[" + ",".join(f"{k}={v}" for k, v in p["Properties"].items()) + "]" if p.get("Properties") else ""))
           for p in d["palette"]]
    return {tuple(b["pos"]): pal[b["state"]] for b in d["blocks"] if not _is_air(pal[b["state"]])}


# --------------------------------------------------------------------------- paste check (vanilla oracle)
def require_state_only(path):
    """The current oracle receipts certify block states, not entity/inventory payloads."""
    from nucleation import Schematic
    sc = Schematic.load_from_file(str(path))
    entities = sc.entity_count()
    block_entities = len(json.loads(sc.get_all_block_entities_snbt_json()))
    if entities or block_entities:
        raise ValueError(f"unsupported verification payload: {entities} entities, {block_entities} block entities; "
                         "only payload-free block-state handovers are currently certified")


def _cmp(want: Dict[Pos, str], got: Dict[Pos, str], examples: int = 5) -> dict:
    missing = [p for p in want if p not in got]
    extra = [p for p in got if p not in want]
    changed = [p for p in want if p in got and want[p] != got[p]]
    id_changed = [p for p in changed if want[p].split("[")[0] != got[p].split("[")[0]]
    ex = ([f"{list(p)} missing (file {want[p]})" for p in sorted(missing)[:examples]]
          + [f"{list(p)} extra {got[p]}" for p in sorted(extra)[:examples]]
          + [f"{list(p)} file {want[p]} -> {got[p]}" for p in sorted(changed)[:examples]])
    return {"missing": len(missing), "extra": len(extra), "changed": len(changed), "block_id_changed": len(id_changed),
            "ok": not (missing or extra or changed), "examples": ex}


def paste_check(path: str, ticks: int = 40, modes=("known_shape", "place"), timeout: float = 900) -> dict:
    """Paste the exported file into vanilla 26.2 (oracle GameTest world, one fresh JVM per mode), idle `ticks`,
    read every block back and diff against the file.

    mode `known_shape`: quiet placement (no neighbour updates, like a Litematica paste / structure block without
    updates); `place`: vanilla placeInWorld with the full update pass (like /setblock or /clone). The receipt says,
    per mode, whether the placed world equals the file (`placed`), whether it still equals it after the idle ticks
    (`after_idle`) and how many block changes happened while idle. `exact` = known_shape placed == file;
    `stable_at_rest` = in every mode nothing changed while idle and the world equals the file at the end."""
    from tools.oracle import gametest
    from tools.oracle.trace import Plan
    if ticks <= 0 or not modes or len(set(modes)) != len(modes) or any(m not in ("known_shape", "place") for m in modes):
        raise ValueError("ticks must be positive; modes must be a non-empty unique subset of known_shape,place")
    require_state_only(path)
    if not gametest.available():
        raise RuntimeError("vanilla oracle not installed (bash scripts/oracle_setup.sh)")
    blocks, src_dv = gametest.load_build(path)
    want = {(b[0], b[1], b[2]): canon(b[3]) for b in blocks if not _is_air(b[3])}
    rec = {"file": os.path.abspath(path), "sha256": sha256(path), "blocks": len(want), "ticks": ticks,
           "file_data_version": src_dv, "oracle": gametest.versions(), "modes": {},
           "scope": "payload-free block states; placement and idle only, not behaviour"}
    for mode in modes:
        tr = gametest.capture(blocks, Plan(ticks=ticks, settle=mode), name=f"paste_{mode}",
                              source_data_version=DATA_VERSION, isolated=True, timeout=timeout)
        inside = lambda d: {p: canon(s) for p, s in d.items() if not _is_air(s)}
        placed, final = inside(tr.placed), inside(tr.state_at(ticks - 1))
        n_changes = sum(len(c) for c in tr.changes.values())
        last = max((t for t, c in tr.changes.items() if c), default=None)
        rec["modes"][mode] = {"placed": _cmp(want, placed), "after_idle": _cmp(want, final),
                              "idle_changes": n_changes, "last_change_tick": last,
                              "wall_s": round((tr.wall_ms or 0) / 1000, 1)}
    m = rec["modes"]
    rec["exact"] = bool(m.get("known_shape", {}).get("placed", {}).get("ok"))
    rec["stable_at_rest"] = all(v["idle_changes"] == 0 and v["after_idle"]["ok"] for v in m.values())
    rec["verdict"] = "PASS" if rec["exact"] and rec["stable_at_rest"] else "FAIL"
    if sha256(path) != rec["sha256"]:
        raise ValueError("artifact changed during paste-check; retry with an immutable file")
    return rec


def receipt_text(rec: dict) -> str:
    o = rec.get("oracle", {})
    L = [f"PASTE CHECK {rec['verdict']}: {rec['blocks']} blocks, vanilla {o.get('mc_version', '?')} (DataVersion {o.get('data_version', '?')}), "
         f"{rec['ticks']} idle ticks; exact={rec['exact']} stable_at_rest={rec['stable_at_rest']}",
         f"file {rec['file']} sha256 {rec['sha256'][:16]}"]
    for mode, v in rec["modes"].items():
        p, a = v["placed"], v["after_idle"]
        L.append(f"  {mode:<11} placed==file: {'yes' if p['ok'] else 'NO'} (missing {p['missing']}, extra {p['extra']}, changed {p['changed']})"
                 f"; after idle==file: {'yes' if a['ok'] else 'NO'} (missing {a['missing']}, extra {a['extra']}, changed {a['changed']})"
                 f"; idle block changes {v['idle_changes']}" + (f" (last at tick {v['last_change_tick']})" if v["last_change_tick"] is not None else ""))
        for e in (p["examples"] if not p["ok"] else a["examples"])[:4]:
            L.append(f"      {e}")
    return "\n".join(L)
