#!/usr/bin/env python3
"""Index every schematic we have: one JSON line per file in data/corpus/index.jsonl.

    .venv/bin/python scripts/corpus_index.py                  # index everything (8 workers)
    .venv/bin/python scripts/corpus_index.py --workers 4 --limit 200
    .venv/bin/python scripts/corpus_index.py --report-only    # rebuild summary + docs/CORPUS_REPORT.md from index.jsonl

Per file: sha256, content hash (position/state-normalised, dedupes re-exports), dimensions, block/palette counts,
data version, redstone-relevant block counts, block entities, entities, whether Nucleation's TickSimulation
refuses the build (and why), size tier, connected components, and source metadata (Discord message/thread text,
Storage Catalog post data, READMEs, specs). Files are untrusted: each is parsed in a worker process with a memory
cap and a timeout, and crashes/timeouts are logged as failures instead of killing the run.
"""
import argparse
import collections
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import re
import resource
import sys
import time
from multiprocessing.connection import wait
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

EXTS = {".litematic", ".schem", ".schematic", ".nbt"}
OUT_DIR = ROOT / "data" / "corpus"
INDEX = OUT_DIR / "index.jsonl"
SUMMARY = OUT_DIR / "index_summary.json"
REPORT = ROOT / "docs" / "CORPUS_REPORT.md"
DISCORD = ROOT / "discord_scraper" / "data"
TIERS = [(40, "<=40"), (150, "<=150"), (500, "<=500"), (5000, "<=5000"), (None, ">5000")]
MAX_WORKERS = 8
TIMEOUT_S = 180
MEM_LIMIT = 6 << 30

# Internal (non-external) sources. External ones come from data/external/MANIFEST.json.
LOCAL_SOURCES = [
    ("discord-raw", "discord_scraper/data/raw_schematics", "internal-only"),
    ("discord-clean", "discord_scraper/data/clean_schematics", "internal-only"),
    ("local-raw", "data/raw_schematics", "internal-only"),
    ("local-simple", "data/simple_schematics", "internal-only"),
    ("local-sliced", "data/sliced_components", "internal-only"),
]
# canonical copy of a duplicate: lowest rank wins (licence-clean first)
ML_RANK = {"clean": 0, "permission": 1, "internal-only": 2}

# category -> predicate on the bare block name (no minecraft: prefix)
RS_EXACT = {
    "wire": {"redstone_wire"}, "repeater": {"repeater"}, "comparator": {"comparator"},
    "torch": {"redstone_torch", "redstone_wall_torch"}, "observer": {"observer"}, "piston": {"piston"},
    "sticky_piston": {"sticky_piston"}, "hopper": {"hopper"}, "dropper": {"dropper"}, "dispenser": {"dispenser"},
    "lever": {"lever"}, "lamp": {"redstone_lamp"}, "target": {"target"}, "daylight": {"daylight_detector"},
    "note_block": {"note_block"}, "rails": {"rail", "powered_rail", "detector_rail", "activator_rail"},
    "redstone_block": {"redstone_block"}, "tripwire_hook": {"tripwire_hook"}, "crafter": {"crafter"},
}
RS_SUFFIX = {"button": "_button", "plate": "_pressure_plate", "copper_bulb": "copper_bulb"}
# the "logic" categories used for density and for the small-logic flag (everything above except pure sources/sinks)
RS_CATS = list(RS_EXACT) + list(RS_SUFFIX)
LOGIC_CORE = {"wire", "repeater", "comparator", "torch", "observer", "piston", "sticky_piston", "copper_bulb"}
AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}


def tier_of(n):
    for lim, label in TIERS:
        if lim is None or n <= lim:
            return label


def rs_counts(names):
    out = collections.Counter()
    for full, n in names.items():
        b = full.split(":", 1)[-1]
        for cat, s in RS_EXACT.items():
            if b in s:
                out[cat] += n
        for cat, suf in RS_SUFFIX.items():
            if b.endswith(suf) or (cat == "copper_bulb" and "copper_bulb" in b):
                out[cat] += n
    return dict(out)


def prop_str(props):
    if isinstance(props, dict):
        items = sorted(props.items())
    else:
        items = sorted(tuple(p) if not isinstance(p, dict) else (p.get("name"), p.get("value")) for p in props)
    return ",".join(f"{k}={v}" for k, v in items)


# ---------------------------------------------------------------- worker side
def classify_sim_error(detail):
    """-> (kind, names). kinds: entity | unknown_block | block_entity | too_large | other"""
    d = detail or ""
    if "entity, which the engine cannot simulate" in d:
        m = re.search(r"`([^`]+)` entity", d)
        return "entity", [m.group(1)] if m else []
    if "need behaviour that is not implemented" in d:
        return "entity", sorted(set(re.findall(r"- (minecraft:[a-z0-9_]+) at", d)))
    if "`Passengers` on an entity" in d:
        return "entity", ["(passengers)"]
    if "blocks without behaviour" in d:
        return "unknown_block", sorted(set(re.findall(r"minecraft:[a-z0-9_]+", d.split("simulated as nothing:", 1)[-1])))
    if "has an inventory but no slot count" in d:
        return "block_entity", sorted(set(re.findall(r"minecraft:[a-z0-9_]+", d)))
    if "cell limit" in d or "over the" in d:
        return "too_large", []
    return "other", []


def try_sim(nuc, schem):
    try:
        nuc.TickSimulation.from_schematic(schem, nuc.TickSettleMode.Placement, 0, 0, 0, "")
        return True, None, None
    except Exception as e:  # nucleation raises NucleationError with a code; detail is in last_error_detail()
        detail = nuc.TickSimulation.last_error_detail()
        return False, f"{e}", detail


def structure_nbt_to_schematic(nuc, path):
    """Vanilla structure-block .nbt (Nucleation 0.12 does not read it): palette + blocks -> Schematic. Block
    entity NBT and entities are not carried over."""
    import nbtlib
    d = nbtlib.load(str(path))
    pal = []
    for e in d["palette"]:
        props = ",".join(f"{k}={v}" for k, v in sorted((e.get("Properties") or {}).items()))
        pal.append(str(e["Name"]) + (f"[{props}]" if props else ""))
    s = nuc.Schematic.create(Path(path).stem)
    for b in d["blocks"]:
        x, y, z = (int(v) for v in b["pos"])
        s.set_block(x, y, z, pal[int(b["state"])])
    if "DataVersion" in d:
        s.set_source_data_version(int(d["DataVersion"]))
    return s


def repair_negative_regions(nuc, path, max_cells=8_000_000):
    """Litematica allows negative region sizes (blocks grow towards -axis); Nucleation rejects them. Rebuild the
    regions with positive sizes via litemapy. Block entities/entities are lost."""
    import tempfile
    from litemapy import Region, Schematic
    lm = Schematic.load(str(path))
    if sum(abs(r.width * r.height * r.length) for r in lm.regions.values()) > max_cells:
        raise ValueError("negative_region_size_too_large")
    new = Schematic(name=lm.name, author=lm.author, description=lm.description)
    for name, r in lm.regions.items():
        nr = Region(r.x + r.minx(), r.y + r.miny(), r.z + r.minz(), abs(r.width), abs(r.height), abs(r.length))
        for x in r.xrange():
            for y in r.yrange():
                for z in r.zrange():
                    b = r[x, y, z]
                    if b.id != "minecraft:air":
                        nr[x - r.minx(), y - r.miny(), z - r.minz()] = b
        new.regions[name] = nr
    fd, tmp = tempfile.mkstemp(suffix=".litematic")
    os.close(fd)
    try:
        new.save(tmp)
        return nuc.Schematic.load_from_file(tmp)
    finally:
        os.unlink(tmp)


def load_any(nuc, path):
    """-> (Schematic, notes). Fallbacks for files Nucleation's plain loader rejects."""
    notes = []
    try:
        return nuc.Schematic.load_from_file(str(path)), notes
    except Exception as first:
        raw = Path(path).read_bytes()
        if raw[:2] != b"\x1f\x8b":  # some exporters write uncompressed NBT
            try:
                s = nuc.Schematic.from_data(gzip.compress(raw))
                return s, ["recompressed_raw_nbt"]
            except Exception:
                pass
        ext = Path(path).suffix.lower()
        if ext == ".nbt":
            try:
                return structure_nbt_to_schematic(nuc, path), ["structure_nbt_converted"]
            except Exception:
                pass
        if ext == ".litematic":
            try:
                return repair_negative_regions(nuc, path), ["negative_regions_repaired", "entities_lost"]
            except Exception as e:
                if "negative_region_size_too_large" in str(e):
                    raise ValueError("negative_region_size_too_large") from first
        raise first


def analyze(path, tight_limit=4_000_000):
    import nucleation as nuc

    rec = {}
    raw = Path(path).read_bytes()
    rec["size_bytes"] = len(raw)
    rec["sha256"] = hashlib.sha256(raw).hexdigest()
    del raw
    s, notes = load_any(nuc, path)
    if notes:
        rec["load_notes"] = notes
    nb = int(s.block_count())
    ad = s.dimensions()
    td = s.tight_dimensions() if nb else ad
    rec["dims"] = [td.x, td.y, td.z]
    rec["alloc_dims"] = [ad.x, ad.y, ad.z]
    rec["block_count"] = nb
    rec["volume"] = td.x * td.y * td.z
    names = {k: v for k, v in json.loads(s.count_blocks_json()).items() if k not in AIR}
    rec["block_names"] = len(names)
    rec["palette_size"] = len(json.loads(s.palette_json()))
    rec["data_version"] = int(s.source_data_version())
    rec["mc_version_field"] = int(s.mc_version())
    rec["meta_name"] = s.name() or None
    rec["meta_author"] = s.author() or None
    rec["meta_description"] = s.description() or None
    rec["tier"] = tier_of(nb)
    rs = rs_counts(names)
    rec["rs"] = rs
    rs_total = sum(rs.values())
    rec["rs_total"] = rs_total
    rec["rs_density"] = round(rs_total / nb, 4) if nb else 0.0
    rec["rs_logic"] = sum(v for k, v in rs.items() if k in LOGIC_CORE)
    rec["top_blocks"] = dict(sorted(names.items(), key=lambda kv: -kv[1])[:12])
    rec["all_block_counts"] = names if len(names) <= 400 else dict(sorted(names.items(), key=lambda kv: -kv[1])[:400])

    bes = json.loads(s.get_all_block_entities_json())
    def be_id(b):
        i = b.get("id")
        if i and i != "unknown":
            return i
        return ((b.get("nbt") or {}).get("id") or {}).get("String") or "unknown"

    cnt = collections.Counter(be_id(b) for b in bes)
    rec["block_entities"] = len(bes)
    rec["block_entity_types"] = dict(cnt.most_common(10))
    ents = json.loads(s.get_entities_json())
    ecnt = collections.Counter(e.get("id") for e in ents)
    rec["entity_count"] = len(ents)
    rec["entity_types"] = dict(ecnt.most_common(15))

    if nb:
        rec["components"] = int(s.split_connected_attach_nearby(1, 0).len()) if nb <= tight_limit else None
        # content hash: states normalised to the min corner, order independent, entities included by id only
        if nb <= tight_limit:
            blocks = json.loads(s.get_non_air_blocks_json())
            mx = min(b["x"] for b in blocks)
            my = min(b["y"] for b in blocks)
            mz = min(b["z"] for b in blocks)
            keys = sorted((b["x"] - mx, b["y"] - my, b["z"] - mz, b["name"], prop_str(b["properties"])) for b in blocks)
            h = hashlib.blake2b(digest_size=16)
            for k in keys:
                h.update(repr(k).encode())
            rec["content_hash"] = h.hexdigest()
            del blocks, keys
    else:
        rec["components"] = 0

    ok, err, detail = try_sim(nuc, s)
    rec["sim_ok"] = ok
    rec["entity_dependent"] = False
    if not ok:
        kind, who = classify_sim_error(detail)
        rec["sim_refusal"] = kind
        rec["sim_error"] = (detail or err or "")[:300]
        rec["entity_dependent"] = kind == "entity"
        rec["sim_refused_entities"] = who if kind == "entity" else []
        rec["sim_refused_blocks"] = who if kind in ("unknown_block", "block_entity") else []
        if kind == "unknown_block":  # full states: e.g. a powered repeater at rest has no behaviour entry
            rec["sim_refused_states"] = re.findall(r"minecraft:[a-z0-9_]+(?:\[[^\]]*\])?",
                                                   detail.split("simulated as nothing:", 1)[-1])[:6]
        # Would it simulate with entities removed and behaviour-less blocks deleted? (the "simulatable core")
        c = s.deep_clone()
        for i in reversed(range(c.entity_count())):
            c.remove_entity(i)
        blocks_gone = set(rec["sim_refused_blocks"])
        ok2 = False
        for _ in range(4):
            ok2, _, d2 = try_sim(nuc, c)
            if ok2:
                break
            k2, w2 = classify_sim_error(d2)
            if k2 not in ("unknown_block", "block_entity") or not w2:
                rec["sim_refusal_stripped"] = k2
                break
            blocks_gone |= set(w2)
            c.replace_blocks_json(json.dumps({b: "minecraft:air" for b in w2}))
        rec["sim_ok_stripped"] = ok2
        if ok2:
            rec["stripped_blocks"] = nb - int(c.block_count())
            rec["stripped_block_names"] = sorted(blocks_gone)
            rec["stripped_entities"] = rec["entity_count"]
            rec["stripped_redstone"] = sum(1 for b in blocks_gone if rs_counts({b: 1}))
        rec["sim_refused_blocks"] = sorted(blocks_gone)
    else:
        rec["sim_ok_stripped"] = True
        rec["stripped_blocks"] = 0
        rec["stripped_entities"] = 0
        rec["stripped_redstone"] = 0
    rec["small_logic"] = bool(0 < nb <= 500 and rec["entity_count"] == 0 and rec["rs_logic"] > 0)
    return rec


def worker(conn):
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
    except (ValueError, OSError):
        pass
    while True:
        task = conn.recv()
        if task is None:
            return
        idx, path = task
        t = time.time()
        try:
            if os.environ.get("CORPUS_INDEX_TEST_HOOKS"):  # exercised by the robustness check, see docs
                if Path(path).name.startswith("crash_"):
                    os._exit(7)
                if Path(path).name.startswith("hang_"):
                    time.sleep(3600)
            rec = analyze(path)
            rec["status"] = "ok"
        except MemoryError:
            rec = {"status": "fail", "fail_reason": "memory", "error": "MemoryError"}
        except Exception as e:
            code = str(e) if str(e).startswith("NucleationErrorCode") or isinstance(e, ValueError) else type(e).__name__
            rec = {"status": "fail", "fail_reason": "load_error:" + code, "error": f"{type(e).__name__}: {e}"[:300]}
        rec["analyze_s"] = round(time.time() - t, 3)
        conn.send((idx, rec))


# ---------------------------------------------------------------- supervisor
class Pool:
    """Tiny worker pool that survives native crashes and timeouts (a crash only fails the file in flight)."""

    def __init__(self, n, timeout=TIMEOUT_S):
        self.ctx = mp.get_context("spawn")
        self.n = n
        self.timeout = timeout
        self.slots = []
        for _ in range(n):
            self.slots.append(self._spawn())

    def _spawn(self):
        parent, child = self.ctx.Pipe()
        p = self.ctx.Process(target=worker, args=(child,), daemon=True)
        p.start()
        child.close()
        return {"p": p, "conn": parent, "task": None, "t0": 0.0}

    def run(self, tasks, on_result):
        todo = list(tasks)
        done = 0
        total = len(todo)
        nxt = 0
        while done < total:
            for sl in self.slots:
                if sl["task"] is None and nxt < total:
                    sl["task"] = todo[nxt]
                    sl["t0"] = time.time()
                    sl["conn"].send(todo[nxt])
                    nxt += 1
            busy = [sl for sl in self.slots if sl["task"] is not None]
            ready = wait([sl["conn"] for sl in busy] + [sl["p"].sentinel for sl in busy], timeout=1.0)
            for i, sl in enumerate(self.slots):
                if sl["task"] is None:
                    continue
                idx, path = sl["task"]
                res = None
                if sl["conn"] in ready and sl["conn"].poll():
                    try:
                        _, res = sl["conn"].recv()
                    except (EOFError, OSError):
                        res = None
                if res is None and not sl["p"].is_alive():
                    res = {"status": "fail", "fail_reason": "crash", "error": f"worker exit code {sl['p'].exitcode}"}
                elif res is None and time.time() - sl["t0"] > self.timeout:
                    sl["p"].kill()
                    res = {"status": "fail", "fail_reason": "timeout", "error": f">{self.timeout}s"}
                if res is None:
                    continue
                on_result(idx, res)
                done += 1
                sl["task"] = None
                if not sl["p"].is_alive():
                    sl["p"].join()
                    self.slots[i] = self._spawn()
        return

    def close(self):
        for sl in self.slots:
            try:
                sl["conn"].send(None)
            except (OSError, BrokenPipeError):
                pass
        for sl in self.slots:
            sl["p"].join(timeout=3)
            if sl["p"].is_alive():
                sl["p"].kill()


# ---------------------------------------------------------------- sources & metadata
def enumerate_sources(extra=None):
    """-> list of dict(source, ml_use, root, files). External sources come from MANIFEST.json."""
    out = []
    for name, rel, ml in LOCAL_SOURCES:
        root = ROOT / rel
        if root.exists():
            out.append(dict(source=name, ml_use=ml, root=root, licence="unknown/third-party"))
    man = ROOT / "data" / "external" / "MANIFEST.json"
    if man.exists():
        for s in json.loads(man.read_text())["sources"]:
            root = ROOT / s["dir"]
            if root.exists():
                out.append(dict(source=s["name"], ml_use=s["ml_use"], root=root, licence=s["licence"]))
    conv = OUT_DIR / "converted"
    if conv.exists():
        for d in sorted(conv.iterdir()):
            if d.is_dir():
                # converted files inherit the ML status of the source they were converted from
                ml = next((s["ml_use"] for s in json.loads(man.read_text())["sources"] if s["name"] == d.name), "internal-only")
                out.append(dict(source=f"converted/{d.name}", ml_use=ml, root=d, licence="see source"))
    if extra:
        out.append(dict(source="extra", ml_use="internal-only", root=Path(extra).resolve(), licence="unknown"))
    for s in out:
        files = []
        for dirpath, dirnames, filenames in os.walk(s["root"]):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for f in filenames:
                if Path(f).suffix.lower() in EXTS:
                    files.append(Path(dirpath) / f)
        s["files"] = sorted(files)
    return out


def load_discord_meta():
    """filename -> source metadata, from raw_messages (keyed by message id prefix of the attachment filename)."""
    by_file, by_thread = {}, collections.defaultdict(list)
    for f in sorted((DISCORD / "raw_messages").glob("*/*/messages.jsonl")):
        for line in f.open():
            m = json.loads(line)
            by_thread[m["channel_id"]].append(m)
            for sc in m.get("schematics", []):
                by_file[sc["filename"]] = m
    clean_names = {p.name for p in (DISCORD / "clean_schematics").glob("*/*")}
    meta = {}
    for fn, m in by_file.items():
        thread = sorted(by_thread[m["channel_id"]], key=lambda x: x["timestamp"])
        texts = [x["content"].strip() for x in thread
                 if x["content"].strip() and x["author_name"] != "Archiver Bot" and x["message_id"] != m["message_id"]]
        meta[fn] = dict(
            kind="discord", message_id=m["message_id"], thread_id=m["channel_id"], title=m["channel_name"],
            category=m["category"], timestamp=m["timestamp"], author_id=m["author_id"],
            message_text=m["content"].strip()[:2000], thread_text=" \n".join(texts)[:2000],
            thread_messages=len(thread), in_clean=fn in clean_names)
    return meta


def load_storage_meta(root):
    meta = {}
    for dj in root.glob("Archive/*/*/data.json"):
        try:
            d = json.loads(dj.read_text())
        except Exception:
            continue
        rec = d.get("records") or {}
        authors = [a.get("displayName") or a.get("username") for a in d.get("authors", [])]
        for att in d.get("attachments", []):
            if not att.get("path"):
                continue
            ll = att.get("litematic") or {}
            meta[str((dj.parent / att["path"]).resolve())] = dict(
                kind="storage-catalog", title=d.get("name"), code=d.get("code"),
                tags=[t.get("name") for t in d.get("tags", [])], authors=authors,
                description=rec.get("description"), features=rec.get("features"), considerations=rec.get("considerations"),
                notes=rec.get("notes"), instructions=rec.get("instructions"), compatibility=rec.get("compatibility"),
                declared_size=ll.get("size"), declared_mc=ll.get("version"),
                category=dj.parent.parent.name, thread=(d.get("post") or {}).get("threadURL"))
    return meta


def load_leroymilo_meta(root):
    meta = {}
    for md in root.glob("**/description.md"):
        parts = re.split(r"^# ", md.read_text(), flags=re.M)
        for part in parts:
            if not part.strip():
                continue
            title, _, body = part.partition("\n")
            meta[str((md.parent / (title.strip() + ".litematic")).resolve())] = dict(
                kind="readme", title=title.strip(), description=body.strip())
    return meta


def sidecar_meta(path):
    sc = path.with_name(path.name + ".meta.json")
    if sc.exists():
        try:
            return json.loads(sc.read_text())
        except Exception:
            return None


def verify_meta(path, root):
    """redstone-verify: attach spec.v of the circuit via the nearest entry.json."""
    for parent in path.parents:
        e = parent / "entry.json"
        if e.exists():
            try:
                ent = json.loads(e.read_text())
                spec = root / ent["spec"]
                return dict(kind="redstone-verify", tool=ent.get("tool"), circuit=ent.get("circuit"),
                            spec_path=ent.get("spec"), spec_v=spec.read_text()[:1500] if spec.exists() else None)
            except Exception:
                return None
        if parent == root:
            break
    return None


def source_metadata(src, path, caches):
    if src["source"] == "discord-raw" or src["source"] == "discord-clean":
        return caches["discord"].get(path.name)
    sc = sidecar_meta(path)
    if sc:
        return sc
    if src["source"] == "storage-catalog-archive":
        return caches["storage"].get(str(path.resolve()))
    if src["source"] == "litematicas-leroymilo":
        return caches["leroymilo"].get(str(path.resolve()))
    if src["source"] == "redstone-verify":
        return verify_meta(path, src["root"])
    return None


# ---------------------------------------------------------------- indexing
def build_index(args):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sources = enumerate_sources(args.extra)
    caches = {"discord": load_discord_meta()}
    for s in sources:
        if s["source"] == "storage-catalog-archive":
            caches["storage"] = load_storage_meta(s["root"])
        if s["source"] == "litematicas-leroymilo":
            caches["leroymilo"] = load_leroymilo_meta(s["root"])
    items = []
    for s in sources:
        for p in s["files"]:
            items.append((s, p))
    if args.limit:
        step = max(1, len(items) // args.limit)
        items = items[::step][:args.limit]
    print(f"{len(items)} files from {len(sources)} sources; {args.workers} workers", flush=True)

    results = {}
    tasks = [(i, str(p)) for i, (_, p) in enumerate(items)]
    t0 = time.time()

    def on_result(idx, res):
        results[idx] = res
        if len(results) % 250 == 0:
            print(f"  {len(results)}/{len(items)}  {time.time() - t0:.0f}s", flush=True)

    pool = Pool(args.workers, args.timeout)
    try:
        pool.run(tasks, on_result)
    finally:
        pool.close()
    print(f"analysed in {time.time() - t0:.0f}s", flush=True)

    recs = []
    for i, (s, p) in enumerate(items):
        rec = results[i]
        rec = dict(path=str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p), source=s["source"], group=group_of(s["source"], p), ml_use=s["ml_use"], licence=s["licence"],
                   ext=p.suffix.lower(), **rec)
        meta = source_metadata(s, p, caches)
        if meta:
            rec["src_meta"] = meta
            rec["spec_checked"] = bool(meta.get("spec_checked"))
            if meta.get("rebuilt_community_design"):  # someone else's design, MIT covers only the rebuilder's work
                rec["ml_use"] = "permission"
            if meta.get("label"):
                rec["spec_label"] = meta["label"]
        recs.append(rec)
    mark_duplicates(recs)
    out = OUT_DIR / "index.sample.jsonl" if args.limit else INDEX
    with out.open("w") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(recs)} records)")
    return recs


def group_of(source, path):
    """redstone-verify mixes REDA/rtl2mc compiler outputs with ~475 Redstone-Compiler candidate/tile files."""
    if source == "redstone-verify":
        parts = path.parts
        if "circuits_thirdparty" in parts:
            i = parts.index("circuits_thirdparty")
            tool = parts[i + 1]
            if tool == "redstone-compiler":
                return "redstone-verify/redstone-compiler-" + ("tiles" if "shipped" in parts else "candidates")
            return "redstone-verify/" + tool
    return source


def mark_duplicates(recs):
    """dup_of = path of the canonical copy (same sha256, or same normalised content); clean sources win."""
    def rank(r):
        return (ML_RANK.get(r["ml_use"], 3), 0 if r["source"] == "discord-clean" else 1, r["path"])
    for key in ("sha256", "content_hash"):
        groups = collections.defaultdict(list)
        for r in recs:
            if r.get(key) and r["status"] == "ok" and r.get("block_count", 0) > 0:
                groups[r[key]].append(r)
        for g in groups.values():
            if len(g) > 1:
                canon = min(g, key=rank)
                for r in g:
                    if r is not canon and "dup_of" not in r:
                        r["dup_of"] = canon["path"]
                        r["dup_kind"] = key
    for r in recs:
        r["unique"] = "dup_of" not in r and r["status"] == "ok"


# ---------------------------------------------------------------- summary & report
def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def summarize(recs):
    ok = [r for r in recs if r["status"] == "ok"]
    nonempty = [r for r in ok if r["block_count"] > 0]
    uniq = [r for r in nonempty if r["unique"]]
    S = {"generated": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "files": len(recs), "ok": len(ok),
         "failed": len(recs) - len(ok), "empty": len(ok) - len(nonempty), "unique_nonempty": len(uniq)}
    S["fail_reasons"] = dict(collections.Counter(r["fail_reason"] for r in recs if r["status"] != "ok").most_common())
    S["load_notes"] = dict(collections.Counter(n for r in recs for n in r.get("load_notes", [])).most_common())
    S["by_source"] = {}
    for src in sorted({r["group"] for r in recs}):
        rs = [r for r in recs if r["group"] == src]
        u = [r for r in rs if r.get("unique") and r.get("block_count", 0) > 0]
        S["by_source"][src] = dict(files=len(rs), ok=sum(r["status"] == "ok" for r in rs), unique=len(u),
                                   ml_use=rs[0]["ml_use"], small_logic=sum(bool(r.get("small_logic")) for r in u),
                                   sim_ok=sum(bool(r.get("sim_ok")) for r in u))
    spec = [r for r in recs if r.get("spec_checked") and r.get("status") == "ok"]
    S["spec_checked"] = {
        g: dict(files=sum(r["group"] == g for r in spec),
                accept=sum(r.get("spec_label") == "ACCEPT" for r in spec if r["group"] == g),
                reject=sum(r.get("spec_label") == "REJECT" for r in spec if r["group"] == g),
                unique=sum(bool(r.get("unique")) for r in spec if r["group"] == g),
                sim_ok=sum(bool(r["sim_ok"]) for r in spec if r["group"] == g),
                sim_ok_stripped=sum(bool(r.get("sim_ok_stripped")) for r in spec if r["group"] == g),
                le500=sum(r["block_count"] <= 500 for r in spec if r["group"] == g))
        for g in sorted({r["group"] for r in spec})}
    S["dup_files"] = sum(1 for r in recs if "dup_of" in r)
    for label, pool in (("all_ok", nonempty), ("unique", uniq)):
        T = {}
        T["tiers"] = {lab: sum(r["tier"] == lab for r in pool) for _, lab in TIERS}
        T["sim_ok"] = sum(bool(r["sim_ok"]) for r in pool)
        T["entity_dependent"] = sum(bool(r["entity_dependent"]) for r in pool)
        T["has_entities"] = sum(r["entity_count"] > 0 for r in pool)
        T["sim_refusal"] = dict(collections.Counter(r.get("sim_refusal") for r in pool if not r["sim_ok"]).most_common())
        T["entity_dependent_but_ok_stripped"] = sum(bool(r.get("entity_dependent") and r.get("sim_ok_stripped")) for r in pool)
        T["sim_ok_stripped"] = sum(bool(r.get("sim_ok_stripped")) for r in pool)
        T["tier_x_stripped"] = {lab: sum(r["tier"] == lab and bool(r.get("sim_ok_stripped")) for r in pool) for _, lab in TIERS}
        T["stripped_frac_le_5pct"] = sum(bool(r.get("sim_ok_stripped") and not r["sim_ok"] and
                                              r.get("stripped_blocks", 0) <= 0.05 * r["block_count"]) for r in pool)
        T["core_clean"] = sum(bool(r.get("sim_ok_stripped") and not r.get("stripped_redstone")) for r in pool)
        T["small_logic_stripped_ok"] = sum(bool(r["small_logic"] and r.get("sim_ok_stripped")) for r in pool)
        T["refused_block_names_top"] = dict(collections.Counter(
            n for r in pool for n in r.get("sim_refused_blocks", [])).most_common(20))
        T["small_logic"] = sum(bool(r["small_logic"]) for r in pool)
        T["small_logic_excl_compiler_tiles"] = sum(bool(r["small_logic"] and "redstone-compiler" not in r["group"]) for r in pool)
        T["small_logic_by_ml_use"] = dict(collections.Counter(r["ml_use"] for r in pool if r["small_logic"]))
        T["small_logic_sim_ok"] = sum(bool(r["small_logic"] and r["sim_ok"]) for r in pool)
        T["le500_no_entities"] = sum(r["block_count"] <= 500 and r["entity_count"] == 0 for r in pool)
        T["le500_no_entities_sim_ok"] = sum(r["block_count"] <= 500 and r["entity_count"] == 0 and r["sim_ok"] for r in pool)
        T["tier_x_sim"] = {lab: sum(r["tier"] == lab and r["sim_ok"] for r in pool) for _, lab in TIERS}
        dens = [r["rs_density"] for r in pool]
        T["density_buckets"] = {
            "0": sum(d == 0 for d in dens), "(0,0.02]": sum(0 < d <= 0.02 for d in dens),
            "(0.02,0.05]": sum(0.02 < d <= 0.05 for d in dens), "(0.05,0.15]": sum(0.05 < d <= 0.15 for d in dens),
            ">0.15": sum(d > 0.15 for d in dens)}
        comp = collections.Counter("1" if r.get("components") == 1 else "2-3" if (r.get("components") or 0) <= 3 else
                                   "4-10" if (r.get("components") or 0) <= 10 else ">10" for r in pool)
        T["components"] = dict(comp)
        dv = collections.Counter(r["data_version"] for r in pool)
        T["data_versions_top"] = {str(k): v for k, v in dv.most_common(12)}
        blocks = collections.Counter()
        files_with = collections.Counter()
        for r in pool:
            for b, n in r.get("all_block_counts", {}).items():
                blocks[b] += n
                files_with[b] += 1
        T["top_blocks_by_count"] = dict(blocks.most_common(25))
        T["top_blocks_by_files"] = dict(files_with.most_common(25))
        rs_files = collections.Counter()
        for r in pool:
            for k, v in r["rs"].items():
                if v:
                    rs_files[k] += 1
        T["redstone_files_with"] = dict(rs_files.most_common())
        T["entity_types_top"] = dict(collections.Counter(
            e for r in pool for e in r.get("entity_types", {})).most_common(15))
        T["refused_entity_names_top"] = dict(collections.Counter(
            n for r in pool for n in r.get("sim_refused_entities", [])).most_common(15))
        S[label] = T
    return S


def interesting_examples(recs, k=20):
    """Small, entity-free, simulatable circuits with several distinct logic components and a human title."""
    def title(r):
        m = r.get("src_meta") or {}
        return m.get("title") or r.get("meta_name") or Path(r["path"]).stem

    cands = []
    for r in recs:
        if r["status"] != "ok" or not r.get("unique") or not r.get("small_logic") or not r.get("sim_ok"):
            continue
        if not (12 <= r["block_count"] <= 150) or r["rs_density"] < 0.12:
            continue
        kinds = sum(1 for c, v in r["rs"].items() if v and c in LOGIC_CORE | {"lamp", "lever", "button", "hopper", "dropper", "dispenser", "target"})
        has_text = bool((r.get("src_meta") or {}).get("description") or (r.get("src_meta") or {}).get("message_text") or
                        (r.get("src_meta") or {}).get("spec_v"))
        score = kinds * 2 + (3 if has_text else 0) + (2 if r["ml_use"] == "clean" else 0) + min(r["rs_density"], 0.6) * 4
        cands.append((score, r))
    cands.sort(key=lambda x: (-x[0], x[1]["path"]))
    chosen, seen_titles, per_source = [], set(), collections.Counter()
    for score, r in cands:
        t = re.sub(r"[\W_]+", " ", title(r).lower()).strip()
        stem = re.sub(r"\d+|v\d+|final|copy", "", t)
        if stem in seen_titles or per_source[r["source"]] >= 5:
            continue
        seen_titles.add(stem)
        per_source[r["source"]] += 1
        chosen.append(r)
        if len(chosen) == k:
            break
    return chosen, title


def write_report(recs, S):
    ok = [r for r in recs if r["status"] == "ok" and r["block_count"] > 0]
    L = []
    w = L.append
    w("# Corpus report")
    w("")
    w(f"Generated {S['generated']} UTC by `scripts/corpus_index.py` from `data/corpus/index.jsonl` "
      f"(index and summary: `data/corpus/index_summary.json`).")
    w("")
    w(f"- **{S['files']}** schematic files indexed, **{S['ok']}** parsed, **{S['failed']}** failed, "
      f"**{S['empty']}** parsed but empty (0 non-air blocks).")
    w(f"- **{S['unique_nonempty']}** unique non-empty builds after de-duplication "
      f"({S['dup_files']} files are duplicates by sha256 or normalised block content; the canonical copy prefers "
      f"licence-clean sources, then the cleaned Discord set).")
    w("- 'sim_ok' means Nucleation's `TickSimulation.from_schematic(..., Placement)` accepts the build "
      "(`scripts/corpus_index.py`, nucleation 0.12.0). Refusal reasons come from `TickSimulation.last_error_detail()`.")
    w("- 'entity_dependent' = refused because the build contains an entity the simulator cannot model "
      "(item frames, mobs, boats, ...). 'small_logic' = 1..500 non-air blocks, no entities, at least one wire/repeater/"
      "comparator/torch/observer/piston/copper bulb.")
    w("- Components: 26-neighbour connectivity via `Schematic.split_connected_attach_nearby(1, 0)`.")
    w("")
    if S["load_notes"]:
        w("Files that needed a loader fallback (Nucleation 0.12's plain loader rejects them): " +
          ", ".join(f"{k} {v}" for k, v in S["load_notes"].items()) + ". `entities_lost` builds have no entities/"
          "block entities in the index.")
        w("")
    w("## Failures")
    w("")
    if S["fail_reasons"]:
        w("| reason | files |\n|---|---|")
        for k, v in S["fail_reasons"].items():
            w(f"| `{k}` | {v} |")
    else:
        w("None.")
    fails = [r for r in recs if r["status"] != "ok"]
    for r in fails[:15]:
        w(f"- `{r['path']}`: {r['fail_reason']} — {r.get('error', '')[:120]}")
    w("")
    w("## By source")
    w("")
    w("| source / group | ML use | files | parsed | unique | sim_ok (unique) | small logic (unique) |\n|---|---|---|---|---|---|---|")
    for k, v in S["by_source"].items():
        w(f"| {k} | {v['ml_use']} | {v['files']} | {v['ok']} | {v['unique']} | {v['sim_ok']} | {v['small_logic']} |")
    if S["spec_checked"]:
        w("")
        w("## Spec-checked ground truth (converted)")
        w("")
        w("Built from machine-readable specs by `scripts/corpus_convert.py` (settled at-rest, inputs off). "
          "claudestone: tick-tested truth tables / waves on vanilla 26.3. redstone-verify: ACCEPT = SAT/LRAT-certified "
          "equal to `spec.v`, REJECT = refuted mutant (negative examples). rouge: prose-described, not verified.")
        w("")
        w("| group | files | ACCEPT | REJECT | unique | sim_ok | sim_ok after strip | <=500 blocks |\n|---|---|---|---|---|---|---|---|")
        for g, v in S["spec_checked"].items():
            w(f"| {g} | {v['files']} | {v['accept']} | {v['reject']} | {v['unique']} | {v['sim_ok']} | {v['sim_ok_stripped']} | {v['le500']} |")
        rc = ROOT / "data" / "corpus" / "rsl_check.json"
        if rc.exists():
            chk = json.loads(rc.read_text())
            acc = [v for v in chk.values() if v["label"] == "ACCEPT"]
            rej = [v for v in chk.values() if v["label"] == "REJECT"]
            w("")
            w(f"Differential check (`scripts/corpus_check_rsl.py`, single-bit specs only): Nucleation reproduces the Verilog "
              f"truth table for **{sum(v['match'] for v in acc)}/{len(acc)}** ACCEPT layouts and differs from it for "
              f"**{sum(not v['match'] for v in rej)}/{len(rej)}** REJECT mutants.")
    for label, title in (("unique", "Unique builds"), ("all_ok", "All parsed non-empty files (with duplicates)")):
        T = S[label]
        n = S["unique_nonempty"] if label == "unique" else len(ok)
        w("")
        w(f"## {title} (n={n})")
        w("")
        w("### Size tiers (non-air blocks)")
        w("")
        w("| tier | files | share | simulatable |\n|---|---|---|---|")
        for _, lab in TIERS:
            w(f"| {lab} | {T['tiers'][lab]} | {pct(T['tiers'][lab], n)} | {T['tier_x_sim'][lab]} |")
        w("")
        w("### Simulatability")
        w("")
        w(f"- simulatable (sim_ok): **{T['sim_ok']}** ({pct(T['sim_ok'], n)})")
        w(f"- entity-dependent (refused by entity name): **{T['entity_dependent']}** ({pct(T['entity_dependent'], n)}); "
          f"of those {T['entity_dependent_but_ok_stripped']} would simulate with entities removed and behaviour-less blocks deleted")
        w(f"- builds that contain any entity: {T['has_entities']} ({pct(T['has_entities'], n)})")
        w(f"- refusal kinds (of the refused): {json.dumps(T['sim_refusal'])}")
        w(f"- first refusing entity per entity-refused build (the simulator names only the first): {json.dumps(T['refused_entity_names_top'])}")
        w(f"- most common behaviour-less blocks (each makes the whole build refuse): {json.dumps(T['refused_block_names_top'])}")
        w(f"- **simulatable core** (`sim_ok_stripped`: entities removed + behaviour-less blocks deleted, then accepted): "
          f"**{T['sim_ok_stripped']}** ({pct(T['sim_ok_stripped'], n)}); of the originally refused, "
          f"{T['stripped_frac_le_5pct']} lose <=5% of their blocks")
        w(f"- simulatable core per tier: {json.dumps(T['tier_x_stripped'])}")
        w(f"- of the core, {T['core_clean']} lost no redstone-relevant block type (stripping buttons, crafters, daylight "
          f"detectors, lecterns, note-block-like blocks changes behaviour; those are in the 'core' only as a structure)")
        w("")
        w("### Small logic circuits (the training / module-library sub-corpus)")
        w("")
        w(f"- <=500 blocks and no entities: **{T['le500_no_entities']}** ({T['le500_no_entities_sim_ok']} simulatable)")
        w(f"- of those with at least one logic component (`small_logic`): **{T['small_logic']}** "
          f"(**{T['small_logic_sim_ok']}** simulatable as-is, {T['small_logic_stripped_ok']} after stripping behaviour-less blocks)")
        w(f"- `small_logic` excluding the ~475 Redstone-Compiler candidate/tile files from redstone-verify: "
          f"**{T['small_logic_excl_compiler_tiles']}**; by ML status: {json.dumps(T['small_logic_by_ml_use'])}")
        w("")
        w("### Redstone density (redstone-relevant blocks / non-air blocks)")
        w("")
        w("| density | files |\n|---|---|")
        for k, v in T["density_buckets"].items():
            w(f"| {k} | {v} |")
        w("")
        w(f"Connected components: {json.dumps(T['components'])}")
        w("")
        w("Files containing each redstone component: " + ", ".join(f"{k} {v}" for k, v in T["redstone_files_with"].items()))
        w("")
        w("### Top blocks")
        w("")
        w("| block | total count | | block | files containing |\n|---|---|---|---|---|")
        a = list(T["top_blocks_by_count"].items())
        b = list(T["top_blocks_by_files"].items())
        for i in range(min(len(a), len(b))):
            w(f"| {a[i][0]} | {a[i][1]} | | {b[i][0]} | {b[i][1]} |")
        w("")
        w(f"Data versions (top): {json.dumps(T['data_versions_top'])}")
    ex, title = interesting_examples(recs)
    w("")
    w("## 20 example small circuits")
    w("")
    w("Unique, simulatable, entity-free, 12-150 blocks, density >= 0.12, ranked by number of distinct logic components "
      "and available description (max 5 per source).")
    w("")
    w("| # | title | source | blocks | dims | components used | path |\n|---|---|---|---|---|---|---|")
    for i, r in enumerate(ex, 1):
        used = ", ".join(f"{k}:{v}" for k, v in sorted(r["rs"].items(), key=lambda kv: -kv[1]) if v)
        w(f"| {i} | {title(r)[:50]} | {r['source']} | {r['block_count']} | {'x'.join(map(str, r['dims']))} | {used} | `{r['path']}` |")
    REPORT.write_text("\n".join(L) + "\n")
    print(f"wrote {REPORT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--limit", type=int, default=0, help="index an evenly spaced sample of N files (testing)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--extra", help="also index this directory as source 'extra' (testing / one-off imports)")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S, help="per-file timeout in seconds")
    args = ap.parse_args()
    args.workers = max(1, min(args.workers, MAX_WORKERS))
    if args.report_only:
        recs = [json.loads(line) for line in INDEX.open()]
    else:
        recs = build_index(args)
    S = summarize(recs)
    SUMMARY.write_text(json.dumps(S, indent=2) + "\n")
    print(f"wrote {SUMMARY}")
    if not args.limit:
        write_report(recs, S)
    else:
        print(json.dumps({k: S[k] for k in ("files", "ok", "failed", "fail_reasons")}, indent=1))


if __name__ == "__main__":
    mp.freeze_support()
    main()
