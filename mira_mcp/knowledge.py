"""Stateless lookup tools: block-state properties, the component library (lib/), the corpus index (data/corpus/index.jsonl).

None of these touch a task or its hidden spec. They are research aids; a baseline session enables them only through
`extras` (see session.py), because they change what an agent knows relative to the plain build/verify arms.
"""
import functools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIB = ROOT / "lib"
CORPUS_INDEX = ROOT / "data" / "corpus" / "index.jsonl"
DECOMPILED = ROOT / "data" / "corpus" / "decompiled"
PROGRAM_CACHE = ROOT / "data" / "sessions" / "_cache" / "corpus"
MAX_PROGRAM_CHARS = 6000
CORPUS_USE_OK = ("clean", "internal-only")      # 'permission' entries are not served (need the author's consent)

BLOCK_NOTES = {
    "repeater": "facing = the side the signal comes FROM (output is the opposite side). Use delay 1..4 (2 game ticks each). Builder: repeater(flow=EAST, delay=1) takes the flow direction.",
    "comparator": "facing = the side the signal comes FROM (rear). mode=subtract outputs rear - max(side inputs); compare passes rear if >= side. Builder: comparator(flow=..., mode=...).",
    "redstone_wall_torch": "facing points AWAY from the block it hangs on (facing=east hangs on the block to its west). A NOT gate: off while that block is powered.",
    "redstone_torch": "floor torch, needs a solid block below; powers the block above and adjacent dust; off while the block below is powered.",
    "lever": "face=wall|floor|ceiling; facing = direction the lever points away from its support for wall levers. A lever powers the block it is attached to.",
    "stone_button": "pulse source; facing points away from the attachment block; releases after 20 ticks.",
    "redstone_wire": "place BARE (no connection properties): the simulator derives connections; needs a solid block below; power 15 loses 1 per block.",
    "observer": "facing = the direction it watches; the pulse leaves from the back.",
    "piston": "facing = push direction; sticky_piston pulls back.",
    "redstone_lamp": "lit=true when powered; lamps are typically fixed outputs in tasks.",
}


# --------------------------------------------------------------------------- explain_block
def explain_block(block_id: str) -> str:
    from simulation import blockstates as BS
    raw = (block_id or "").strip()
    if not raw:
        return "error: give a block id such as 'repeater' or 'minecraft:comparator' (or a full state to validate)"
    bid, _, rest = raw.partition("[")
    bid = bid.strip()
    if ":" not in bid:
        bid = "minecraft:" + bid
    props = BS.block_properties(bid)
    if props is None:
        close = BS._suggest(bid.split(":", 1)[1], [b.split(":", 1)[1] for b in BS.known_blocks() if b.startswith("minecraft:")])
        return f"error: unknown block {bid!r}" + (f" ({close.strip()})" if close else "")
    defaults = BS.block_defaults(bid) or {}
    lines = [f"{bid}"]
    if props:
        for k in sorted(props):
            lines.append(f"  {k}: {' | '.join(props[k])}  (default {defaults.get(k)})")
    else:
        lines.append("  (no properties)")
    short = bid.split(":", 1)[1]
    if short in BLOCK_NOTES:
        lines.append("  note: " + BLOCK_NOTES[short])
    try:
        from env import blocks as EB
        from env.sim import simulatable
        lines.append("  simulatable in this environment: " + ("yes" if simulatable(EB.canonical(bid)) else "NO (Nucleation cannot load it; set_blocks/build will reject it)"))
    except Exception as e:     # registry lookup is a nicety, never a failure
        lines.append(f"  simulatable: unknown ({type(e).__name__})")
    if rest:
        try:
            lines.append("  your state validates as: " + BS.normalize(raw))
        except BS.StateError as e:
            lines.append(f"  your state is INVALID ({e.kind}): {e}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- component library
def _tokens(text: str):
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _score(query: str, fields: dict) -> float:
    q = _tokens(query)
    if not q:
        return 1.0
    s = 0.0
    for tok in q:
        for w, text in fields.items():
            low = text.lower()
            if tok == low or tok in _tokens(text):
                s += w
            elif tok in low:
                s += w / 2
    return s


@functools.lru_cache(maxsize=1)
def _lib_meta():
    out = []
    for p in sorted(LIB.glob("*.meta.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue          # lib/ is being written by another agent: skip half-written files
    return out


def search_components(query: str, limit: int = 5) -> str:
    """Search lib/*.meta.json (hand-authored, oracle-verified builder components)."""
    metas = _lib_meta()
    if not metas:
        return "no component library found (lib/*.meta.json is missing or empty)"
    scored = []
    for m in metas:
        f = {4: m.get("name", ""), 3: m.get("title", ""), 2: m.get("description", ""), 1: " ".join(m.get("ports", {})) + " " + m.get("kind", "")}
        s = _score(query, f)
        if s > 0:
            scored.append((s, m))
    scored.sort(key=lambda t: (-t[0], t[1]["name"]))
    if not scored:
        return f"no component matches {query!r}; available: {', '.join(m['name'] for m in metas)}"
    out = []
    for _, m in scored[:max(1, int(limit))]:
        ports = "; ".join(f"{n}:{p['dir']}@{p['pos']}{'/' + p['face'] if p.get('face') else ''}" for n, p in m.get("ports", {}).items())
        fp = m.get("footprint", {})
        tm = m.get("timing_ticks", {})
        spec = m.get("spec") or {}
        v = m.get("verified", {})
        out.append(f"{m['name']} - {m.get('title', '')}: {m.get('description', '')}\n"
                   f"  kind {m.get('kind')}; size {fp.get('size')} ({fp.get('blocks')} blocks); ports {ports}\n"
                   f"  timing {tm}; requires {m.get('requires') or '-'}; spec rows {len(spec.get('rows') or [])}; "
                   f"verified orientations {len(v.get('env_ok_orientations', []))}/8, oracle_ok={v.get('oracle_ok')}\n"
                   f"  source lib/{m['name']}.py; use: build(source, components=['{m['name']}']) then in build(b): "
                   f"inst = b.place({m['name']}, at=(x,0,z), rot=0, floor=False, name='u1'); inst['<port>'] = world port position. "
                   f"Local y=0 is the support plane; floor=False when the task floor already exists."
                   + (f"\n  notes: {m['notes']}" if m.get("notes") else ""))
    return "\n\n".join(out) + (f"\n\n({len(scored) - limit} more matches)" if len(scored) > limit else "")


# --------------------------------------------------------------------------- corpus
@functools.lru_cache(maxsize=1)
def _corpus():
    rows = []
    if not CORPUS_INDEX.exists():
        return rows
    with open(CORPUS_INDEX) as fh:
        for i, line in enumerate(fh):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("status") != "ok" or not r.get("unique") or r.get("ml_use") not in CORPUS_USE_OK:
                continue
            rows.append({"id": (r.get("content_hash") or str(i))[:10], "name": r.get("meta_name") or Path(r["path"]).stem, "path": r["path"],
                         "source": r.get("group"), "licence": r.get("licence"), "ml_use": r.get("ml_use"), "blocks": r.get("block_count", 0),
                         "dims": r.get("dims"), "rs": r.get("rs") or {}, "sim_ok": bool(r.get("sim_ok")), "small_logic": bool(r.get("small_logic")),
                         "entity": bool(r.get("entity_dependent")), "top": r.get("top_blocks") or {}, "spec_checked": r.get("spec_checked")})
    return rows


def _program_for(row):
    """(path, text) of the builder program for a corpus row: pre-decompiled when present, else decompiled on demand and cached."""
    stem = Path(row["path"]).stem
    for cand in (DECOMPILED / f"{stem}.py", DECOMPILED / f"{Path(row['path']).parent.name}__{stem}.py"):
        if cand.exists():
            return cand, cand.read_text()
    cache = PROGRAM_CACHE / f"{row['id']}.py"
    if cache.exists():
        return cache, cache.read_text()
    src = ROOT / row["path"]
    if not src.exists():
        return None, f"(no program: source file {row['path']} is not on this machine)"
    from simulation import builder as BD
    try:
        text = BD.decompile(str(src))
    except Exception as e:
        return None, f"(no program: decompile failed: {type(e).__name__}: {str(e)[:120]})"
    PROGRAM_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    return cache, text


def search_corpus(query: str, max_blocks: int = 80, limit: int = 5, show_program: int = 1) -> str:
    """Search the corpus index; metadata for `limit` hits and the builder program text of the top `show_program` ones.
    Only 'clean' (permissive licence) and 'internal-only' entries are served; 'id:<hash>' fetches one entry."""
    rows = _corpus()
    if not rows:
        return "corpus index not found (data/corpus/index.jsonl)"
    limit, show_program = max(1, min(int(limit), 20)), max(0, min(int(show_program), 3))
    m = re.fullmatch(r"\s*id:([0-9a-f]+)\s*", query or "")
    if m:
        hits = [r for r in rows if r["id"].startswith(m.group(1))]
        scored = [(1, r) for r in hits]
    else:
        scored = []
        for r in rows:
            if r["blocks"] > max_blocks or not r["sim_ok"] or r["entity"]:
                continue
            f = {4: r["name"], 3: Path(r["path"]).stem.replace("_", " "), 2: str(r["source"]) + " " + " ".join(k.replace("minecraft:", "") for k in r["top"]),
                 1: " ".join(r["rs"])}
            s = _score(query, f)
            if s > 0:
                scored.append((s + (0.5 if r["small_logic"] else 0) + (0.3 if r["spec_checked"] else 0), r))
        scored.sort(key=lambda t: (-t[0], t[1]["blocks"], t[1]["id"]))
    if not scored:
        return f"no corpus entry matches {query!r} within {max_blocks} blocks (only sim_ok, entity-free, clean/internal-only builds are served)"
    out = []
    for rank, (_, r) in enumerate(scored[:limit]):
        rs = ", ".join(f"{k}:{v}" for k, v in sorted(r["rs"].items(), key=lambda kv: -kv[1])[:6])
        head = (f"[{r['id']}] {r['name']} - {r['blocks']} blocks, dims {r['dims']}, source {r['source']}, licence {r['licence']} ({r['ml_use']}); "
                f"redstone: {rs or '-'}" + ("; spec-checked" if r["spec_checked"] else ""))
        if rank < show_program:
            path, text = _program_for(r)
            body = text if len(text) <= MAX_PROGRAM_CHARS else text[:MAX_PROGRAM_CHARS] + f"\n... [program truncated at {MAX_PROGRAM_CHARS} of {len(text)} chars]"
            head += f"\n  program ({path.relative_to(ROOT) if path else 'n/a'}; relative coordinates from 0,0,0 - shift them into the task's build box):\n" + \
                    "\n".join("    " + ln for ln in body.splitlines())
        out.append(head)
    if len(scored) > limit:
        out.append(f"({len(scored) - limit} more matches; search_corpus('id:<id>') shows one entry with its program)")
    return "\n".join(out)
