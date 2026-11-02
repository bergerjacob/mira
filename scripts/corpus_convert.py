#!/usr/bin/env python3
"""Convert non-schematic external corpora to settled .litematic files + `.meta.json` sidecars.

    .venv/bin/python scripts/corpus_convert.py            # rouge, claudestone, redstone-verify RSL
    .venv/bin/python scripts/corpus_convert.py claudestone

Output: data/corpus/converted/<source>/<...>.litematic and <...>.litematic.meta.json (picked up by
corpus_index.py as source 'converted/<source>'; the sidecar carries the spec / tests / description).
Blocks are placed through Nucleation with `{simulate=true}` so wire shapes, torches and repeaters are resolved
like a hand placement (at rest, inputs off). Inputs in claudestone/RSL (levers, driver cells) are recorded as ports in
the sidecar. Inputs are untrusted data: YAML is read with yaml.safe_load, nothing from them is executed.
"""
import argparse
import functools
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

EXT = ROOT / "data" / "external"
OUT = ROOT / "data" / "corpus" / "converted"
DIRS = {"N": "north", "S": "south", "E": "east", "W": "west"}
OPP = {"north": "south", "south": "north", "east": "west", "west": "east"}


def mc(name):
    return name if ":" in name.split("[", 1)[0] else "minecraft:" + name


def load_yaml_files(paths):
    """yaml is not in the repo venv; fall back to the system python3 (safe_load only)."""
    try:
        import yaml
        return {str(p): yaml.safe_load(Path(p).read_text()) for p in paths}
    except ImportError:
        code = ("import sys,json,yaml\nout={}\nfor p in sys.argv[1:]:\n"
                "    try: out[p]=yaml.safe_load(open(p))\n    except Exception as e: out[p]={'__error__':str(e)}\n"
                "json.dump(out,sys.stdout,default=str)\n")
        r = subprocess.run(["python3", "-I", "-c", code, *map(str, paths)], capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise SystemExit("system python3 needs PyYAML: " + r.stderr[:300])
        return json.loads(r.stdout)


@functools.lru_cache(maxsize=None)
def default_props(nuc, name):
    try:
        return json.loads(nuc.Blocks.get_json(name)).get("default_state") or {}
    except Exception:
        return None  # unknown to the registry: leave the state as written


def complete_state(nuc, st):
    """Fill unspecified properties with the registry defaults ({simulate=true} rejects partial states)."""
    name, _, rest = st.partition("[")
    given = dict(kv.split("=", 1) for kv in rest.rstrip("]").split(",") if kv)
    base = default_props(nuc, name)
    if base is None:
        return st
    props = {**base, **given}
    return name + ("[" + ",".join(f"{k}={v}" for k, v in sorted(props.items())) + "]" if props else "")


def build_schematic(nuc, name, cells):
    """cells: {(x,y,z): state} -> (Schematic at the min corner, min corner, settle info).
    Blocks are written with registry-default properties filled in, then the whole build is run through the engine
    (TickSettleMode.Placement, run to quiescence) and the resolved states are baked back, so wire shapes and
    torch/repeater power are at-rest values. If the engine refuses the build (behaviour-less block, entity) the
    states stay as authored and info['settled'] is False."""
    if not cells:
        return None
    mn = [min(p[i] for p in cells) for i in range(3)]
    s = nuc.Schematic.create(name)
    for (x, y, z), st in cells.items():
        s.set_block(x - mn[0], y - mn[1], z - mn[2], complete_state(nuc, st))
    info = {"settled": False}
    try:
        sim = nuc.TickSimulation.from_schematic(s, nuc.TickSettleMode.Placement, 0, 0, 0, "")
        sim.run_until_quiescent(400)
        info["quiescent"] = bool(sim.is_quiescent())
        info["baked_blocks"] = int(sim.bake_to(s))
        info["settled"] = True
    except Exception:
        info["refusal"] = nuc.TickSimulation.last_error_detail()[:200]
    return s, mn, info


def save(s, path, sidecar):
    path.parent.mkdir(parents=True, exist_ok=True)
    s.save_to_file(str(path))
    Path(str(path) + ".meta.json").write_text(json.dumps(sidecar, indent=1, default=str) + "\n")


def manifest_entry(name):
    m = json.loads((EXT / "MANIFEST.json").read_text())
    return next(s for s in m["sources"] if s["name"] == name)


# ------------------------------------------------------------------ claudestone
def palette_cell(entry):
    """-> (state or None for air, role dict)"""
    role = {}
    if isinstance(entry, str):
        block = entry
    else:
        block = entry.get("block", "air")
        for k in ("input", "output", "name", "fixture", "reserve"):
            if k in entry:
                role[k] = entry[k]
        if "input" in entry or entry.get("reserve"):
            block = "air"
    nbt_dropped = "{" in block
    block = block.split("{", 1)[0].strip()
    if block in ("air", "minecraft:air"):
        return None, role, nbt_dropped
    return mc(block), role, nbt_dropped


def convert_claudestone():
    import nucleation as nuc
    src = EXT / "claudestone"
    me = manifest_entry("claudestone")
    paths = sorted(src.glob("library/*/*.redstone.yaml"))
    docs = load_yaml_files(paths)
    n_ok = n_bad = 0
    for p in paths:
        d = docs[str(p)]
        try:
            if not isinstance(d, dict) or "layers" not in d or "palette" not in d:
                raise ValueError(d.get("__error__", "no layers/palette") if isinstance(d, dict) else "not a mapping")
            cells, ports, nbt_dropped, named = {}, {"inputs": {}, "outputs": {}}, 0, {}
            fixtures = []
            for y, grid in d["layers"].items():
                for z, row in enumerate(str(grid).rstrip("\n").split("\n")):
                    for x, ch in enumerate(row):
                        if ch not in d["palette"]:
                            raise ValueError(f"glyph {ch!r} not in palette")
                        st, role, nd = palette_cell(d["palette"][ch])
                        nbt_dropped += nd
                        pos = (x, int(y), z)
                        if "input" in role:
                            ports["inputs"].setdefault(role["input"], []).append(list(pos))
                        if "output" in role:
                            ports["outputs"].setdefault(role["output"], []).append(list(pos))
                        if "name" in role:
                            named[role["name"]] = list(pos)
                        if role.get("fixture") and st:
                            fixtures.append(list(pos))
                        if st:
                            cells[pos] = st
            built = build_schematic(nuc, d["name"], cells)
            if built is None:
                raise ValueError("empty build")
            s, mn, settle_info = built
            shift = lambda l: [[a - b for a, b in zip(q, mn)] for q in l]  # noqa: E731
            for kind in ("inputs", "outputs"):
                ports[kind] = {k: shift(v) for k, v in ports[kind].items()}
            side = dict(
                kind="claudestone-spec", title=d["name"], family=p.parent.name, description=(d.get("description") or "").strip(),
                traits=d.get("traits"), ports=ports, named_cells={k: [a - b for a, b in zip(v, mn)] for k, v in named.items()},
                fixture_cells=shift(fixtures), tests=d.get("tests"), update_pass=d.get("update_pass"),
                has_entities=bool(d.get("entities")), nbt_dropped=nbt_dropped, settle=settle_info, origin_offset=mn,
                spec_path=str(p.relative_to(ROOT)), commit=me.get("commit"), licence=me["licence"],
                ground_truth="tests ran tick-by-tick on an unmodified vanilla Java 26.3 server (claudestone harness); "
                             "input cells are air here (the harness drives them with a redstone block)",
                spec_checked=bool(d.get("tests")),
                rebuilt_community_design=d["name"].startswith("ref_"))  # third-party designs rebuilt by the repo author
            save(s, OUT / "claudestone" / p.parent.name / (d["name"] + ".litematic"), side)
            n_ok += 1
        except Exception as e:
            n_bad += 1
            print(f"  skip {p.relative_to(src)}: {str(e)[:120]}")
    print(f"claudestone: {n_ok} converted, {n_bad} skipped")


# ------------------------------------------------------------------ rouge
def convert_rouge():
    import nucleation as nuc
    src = EXT / "rouge" / "src" / "main" / "resources" / "rouge" / "circuits"
    me = manifest_entry("rouge")
    n_ok = n_bad = 0
    for p in sorted(src.glob("*.json")):
        try:
            d = json.loads(p.read_text())
            if "steps" not in d:
                raise ValueError("no geometry (description-only entry)")
            cells = {}
            for step in d["steps"]:
                for b in step.get("blocks", []):
                    cells[(int(b["x"]), int(b["y"]), int(b["z"]))] = mc(b["block"])
            built = build_schematic(nuc, d["id"], cells)
            if built is None:
                raise ValueError("empty build")
            s, mn, settle_info = built
            side = dict(
                kind="rouge-circuit", title=d.get("title"), id=d["id"], category=d.get("category"),
                description=d.get("description"), aliases=d.get("aliases"), inputs=d.get("inputs"), outputs=d.get("outputs"),
                footprint=d.get("footprint"), steps=[{"title": s_.get("title"), "explanation": s_.get("explanation"),
                                                      "blocks": len(s_.get("blocks", []))} for s_ in d["steps"]],
                origin_offset=mn, settle=settle_info, spec_path=str(p.relative_to(ROOT)), commit=me.get("commit"), licence=me["licence"],
                ground_truth="none stated: geometry not simulator- or game-verified here; run the index's sim_ok as a "
                             "sanity gate only", spec_checked=False)
            save(s, OUT / "rouge" / (d["id"] + ".litematic"), side)
            n_ok += 1
        except Exception as e:
            n_bad += 1
            print(f"  skip {p.name}: {str(e)[:120]}")
    print(f"rouge: {n_ok} converted, {n_bad} skipped")


# ------------------------------------------------------------------ redstone-verify .rsl
def rsl_token(t):
    """-> (state or None, port or None). Mirrors redstone-verify docs/RSL.md / rsv/src/main.rs."""
    t, _, probe = t.partition("@")
    if t == ".":
        return None, None
    if t == "#":
        return "minecraft:sandstone", None
    if t == "w":
        return "minecraft:redstone_wire", ("probe", probe) if probe else None
    if t == "t":
        return "minecraft:redstone_torch[lit=true]", ("probe", probe) if probe else None
    if t.startswith("L:"):
        return "minecraft:lever[face=floor,facing=north,powered=false]", ("in", t[2:])
    if t.startswith("O:"):
        return "minecraft:redstone_lamp[lit=false]", ("out", t[2:])
    if len(t) > 3 and t[0] == "L" and t[2] == ":":
        return f"minecraft:lever[face=wall,facing={DIRS[t[1]]},powered=false]", ("in", t[3:])
    if len(t) == 2 and t[0] == "t":
        return f"minecraft:redstone_wall_torch[facing={DIRS[t[1]]},lit=true]", ("probe", probe) if probe else None
    if len(t) == 3 and t[0] == "r":
        return f"minecraft:repeater[delay={t[1]},facing={OPP[DIRS[t[2]]]}]", ("probe", probe) if probe else None
    if len(t) == 2 and t[0] in "cs":
        mode = "compare" if t[0] == "c" else "subtract"
        return f"minecraft:comparator[facing={OPP[DIRS[t[1]]]},mode={mode}]", ("probe", probe) if probe else None
    raise ValueError(f"unknown RSL token {t!r}")


def parse_rsl(text):
    name, floor, layers = "circuit", True, []
    for raw in text.splitlines():
        line = raw.split("%")[0].strip()
        if not line:
            continue
        if line.startswith("name:"):
            name = line[5:].strip()
        elif line.startswith("floor:"):
            floor = line[6:].strip() != "none"
        elif line == "layer":
            layers.append([])
        else:
            layers[-1].append(line.split())
    return name, floor, layers


def convert_rsl():
    import nucleation as nuc
    src = EXT / "redstone-verify"
    me = manifest_entry("redstone-verify")
    n_ok = n_bad = 0
    for p in sorted(src.glob("circuits/*/**/*.rsl")):
        rel = p.relative_to(src / "circuits")
        circuit = rel.parts[0]
        try:
            name, floor, layers = parse_rsl(p.read_text())
            cells, ports = {}, {"in": {}, "out": {}, "probe": {}}
            for li, layer in enumerate(layers):
                for z, row in enumerate(layer):
                    for x, tok in enumerate(row):
                        st, port = rsl_token(tok)
                        if st:
                            cells[(x, li + 1, z)] = st
                        if port:
                            ports[port[0]][port[1]] = [x, li + 1, z]
            if floor:
                for (x, y, z), st in list(cells.items()):
                    if y == 1 and re.match(r"minecraft:(redstone_wire|redstone_torch\[|repeater|comparator|lever\[face=floor)", st):
                        cells.setdefault((x, 0, z), "minecraft:sandstone")
            built = build_schematic(nuc, name, cells)
            if built is None:
                raise ValueError("empty build")
            s, mn, settle_info = built
            spec = src / "circuits" / circuit / "spec.v"
            label = "REJECT" if "mutants" in rel.parts else "ACCEPT"
            side = dict(
                kind="redstone-verify-rsl", title=f"{circuit}/{p.stem}", circuit=circuit, label=label,
                label_note="ACCEPT = seed/candidate layouts that the project's gate certifies equal to spec.v for every "
                           "input (steady state, acyclic subset); REJECT = mutants the gate refutes with a counterexample",
                ports={k: {n: [a - b for a, b in zip(v, mn)] for n, v in d_.items()} for k, d_ in ports.items()},
                spec_v=spec.read_text() if spec.exists() else None, floor=floor, origin_offset=mn, settle=settle_info,
                spec_path=str((src / "circuits" / circuit / "spec.v").relative_to(ROOT)), source_layout=str(p.relative_to(ROOT)),
                commit=me.get("commit"), licence=me["licence"],
                ground_truth="formal: SAT/LRAT-certified equivalence to the Verilog spec (ACCEPT) or counterexample (REJECT), "
                             "co-simulated on MCHPRS backends; layouts are placed as a player would, not hand-built in game",
                spec_checked=True)
            save(s, OUT / "redstone-verify" / circuit / (("mutants_" if label == "REJECT" else "") + p.stem + ".litematic"), side)
            n_ok += 1
        except Exception as e:
            n_bad += 1
            print(f"  skip {rel}: {str(e)[:120]}")
    print(f"redstone-verify .rsl: {n_ok} converted, {n_bad} skipped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("which", nargs="*", default=["rouge", "claudestone", "redstone-verify"])
    args = ap.parse_args()
    for w in args.which:
        {"rouge": convert_rouge, "claudestone": convert_claudestone, "redstone-verify": convert_rsl}[w]()


if __name__ == "__main__":
    main()
