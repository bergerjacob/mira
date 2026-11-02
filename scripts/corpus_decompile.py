"""Decompile the Discord schematic corpus into builder programs and measure the result.

For each .litematic/.schem: decompile() (gated on a lossless round trip), write the program to
<out>/decompiled/<stem>.py, and append a stats record to <out>/decompile_stats.jsonl (+ decompile_summary.json). Tries motifs first,
then falls back to a motif-free program; the failure reason of the first attempt is recorded.
Token counts are chars / 3.6. Flat baselines: `json` = json.dumps of the {x,y,z,state[,nbt]}
list, `compact` = palette lines + one "x y z idx" line per block.

Usage: python scripts/corpus_decompile.py [--src GLOB] [--out data/corpus] [--workers 16] [--limit N]
"""

import argparse
import glob
import json
import os
import signal
import statistics
import sys
import time
from collections import Counter
from multiprocessing import Pool

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from simulation import builder  # noqa: E402

CHARS_PER_TOKEN = 3.6
DEFAULT_SRC = "discord_scraper/data/raw_schematics/*/*.litematic"
FILE_TIMEOUT = 300


class _Timeout(Exception):
    pass


def _alarm(_sig, _frm):
    raise _Timeout()


def _flat_sizes(blocks, entities):
    """Character sizes of the flat JSON list and the compact palette form."""
    rows = []
    for (x, y, z), (st, nbt) in blocks.items():
        d = {"x": x, "y": y, "z": z, "state": st}
        if nbt is not None:
            d["nbt"] = nbt
        rows.append(d)
    json_chars = len(json.dumps(rows)) + sum(len(json.dumps({"id": e[3], "x": e[0], "y": e[1], "z": e[2], "nbt": e[4]}))
                                             for e in entities)
    pal = {}
    lines = []
    for (x, y, z), spec in blocks.items():
        i = pal.setdefault(spec, len(pal))
        lines.append(f"{x} {y} {z} {i}")
    compact_chars = sum(len(f"{i} {s} {n or ''}\n") for (s, n), i in pal.items()) + sum(len(l) + 1 for l in lines)
    return json_chars, compact_chars


def process(args):
    path, out_dir = args
    rec = {"file": os.path.relpath(path), "stem": os.path.splitext(os.path.basename(path))[0]}
    t0 = time.time()
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(FILE_TIMEOUT)
    try:
        try:
            blocks, entities, offset = builder.load_normalized(path)
        except Exception as e:  # noqa: BLE001
            rec.update(lossless=False, reason=getattr(e, "reason", "load_error"), detail=type(e).__name__ + ": " + str(e)[:120])
            return rec
        rec.update(blocks=len(blocks), entities=len(entities), kinds=len({s for s in blocks.values()}))
        rec["json_chars"], rec["compact_chars"] = _flat_sizes(blocks, entities)
        import nucleation
        sc = nucleation.Schematic.load_from_file(path)
        text, first_reason = None, None
        for mode, motifs in (("motifs", True), ("flat", False)):
            try:
                text = builder.decompile(sc, motifs=motifs, verify=True)
                rec["mode"] = mode
                break
            except builder.DecompileError as e:
                first_reason = first_reason or e.reason
                rec["detail"] = str(e)[:200]
            except Exception as e:  # noqa: BLE001
                first_reason = first_reason or "exception"
                rec["detail"] = (type(e).__name__ + ": " + str(e))[:200]
        rec["first_attempt_failure"] = first_reason
        if text is None:
            rec.update(lossless=False, reason=first_reason)
        else:
            rec.update(lossless=True, program_chars=len(text), arrays=text.count("b.array("))
            with open(os.path.join(out_dir, rec["stem"] + ".py"), "w", encoding="utf-8") as f:
                f.write(text)
    except _Timeout:
        rec.update(lossless=False, reason="timeout")
    except MemoryError:
        rec.update(lossless=False, reason="memory")
    finally:
        signal.alarm(0)
        rec["seconds"] = round(time.time() - t0, 3)
    return rec


def pct(vals, q):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(q * len(vals)))] if vals else None


def summarize(recs):
    ok = [r for r in recs if r.get("lossless")]
    s = {"files": len(recs), "lossless": len(ok), "lossless_rate": round(len(ok) / max(1, len(recs)), 4),
         "failures_by_reason": dict(Counter(r["reason"] for r in recs if not r.get("lossless"))),
         "first_attempt_failures": dict(Counter(r["first_attempt_failure"] for r in recs if r.get("first_attempt_failure"))),
         "mode_counts": dict(Counter(r["mode"] for r in ok)),
         "total_seconds_cpu": round(sum(r["seconds"] for r in recs), 1),
         "seconds_per_file_median": statistics.median(r["seconds"] for r in recs),
         "seconds_per_file_max": max(r["seconds"] for r in recs)}
    if ok:
        tok = lambda c: c / CHARS_PER_TOKEN  # noqa: E731
        ratio_json = [r["json_chars"] / r["program_chars"] for r in ok]
        ratio_compact = [r["compact_chars"] / r["program_chars"] for r in ok]
        big = [r for r in ok if r["blocks"] >= 1000]
        s.update({
            "blocks_total_ok": sum(r["blocks"] for r in ok),
            "tokens_json_total": round(tok(sum(r["json_chars"] for r in ok))),
            "tokens_compact_total": round(tok(sum(r["compact_chars"] for r in ok))),
            "tokens_program_total": round(tok(sum(r["program_chars"] for r in ok))),
            "program_vs_json_ratio_median": round(statistics.median(ratio_json), 2),
            "program_vs_json_ratio_p10_p90": [round(pct(ratio_json, .1), 2), round(pct(ratio_json, .9), 2)],
            "program_vs_compact_ratio_median": round(statistics.median(ratio_compact), 2),
            "program_vs_json_ratio_median_ge1000_blocks": round(statistics.median(
                r["json_chars"] / r["program_chars"] for r in big), 2) if big else None,
            "program_vs_compact_ratio_median_ge1000_blocks": round(statistics.median(
                r["compact_chars"] / r["program_chars"] for r in big) if big else 0, 2),
            "files_ge1000_blocks": len(big),
            "files_with_arrays": sum(1 for r in ok if r["arrays"]),
            "tokens_per_block_program_median": round(statistics.median(
                tok(r["program_chars"]) / max(1, r["blocks"]) for r in ok), 3),
            "tokens_per_block_json_median": round(statistics.median(
                tok(r["json_chars"]) / max(1, r["blocks"]) for r in ok), 3),
            "program_tokens_p50_p90_max": [round(tok(pct([r["program_chars"] for r in ok], q))) for q in (.5, .9, 1.0)],
            "json_tokens_p50_p90_max": [round(tok(pct([r["json_chars"] for r in ok], q))) for q in (.5, .9, 1.0)],
        })
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default="data/corpus")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    files = sorted(glob.glob(a.src))
    if a.limit:
        files = files[:a.limit]
    out_dir = os.path.join(a.out, "decompiled")
    os.makedirs(out_dir, exist_ok=True)
    # biggest files first so the pool stays busy at the tail
    files.sort(key=lambda f: -os.path.getsize(f))
    t0 = time.time()
    recs = []
    with Pool(a.workers, maxtasksperchild=50) as pool, open(os.path.join(a.out, "decompile_stats.jsonl"), "w") as sf:
        for i, rec in enumerate(pool.imap_unordered(process, [(f, out_dir) for f in files]), 1):
            recs.append(rec)
            sf.write(json.dumps(rec) + "\n")
            if i % 200 == 0:
                print(f"{i}/{len(files)} ({time.time() - t0:.0f}s)", flush=True)
    summary = summarize(recs)
    summary["wall_seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(a.out, "decompile_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
