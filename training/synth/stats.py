"""Yield / runtime / size statistics of the synthetic data engine -> data/synth/stats.json (`scripts/synth_generate.py stats`)."""
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from training.synth import common as C  # noqa: E402


def pct(xs, ps=(0, 10, 50, 90, 100)):
    xs = sorted(xs)
    if not xs:
        return {}
    return {f"p{p}": xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] for p in ps}


def reason_key(r):
    r = r or ""
    for k in ("duplicate", "no netlist", "vacuous", "only", "reference does not", "does not compile", "never follows", "no failing bug",
              "no stored", "empty build"):
        if k in r:
            return k
    return r[:60] or "?"


def summary(write=True):
    att = defaultdict(list)
    if (C.DATA / "attempts.jsonl").exists():
        for ln in (C.DATA / "attempts.jsonl").read_text().splitlines():
            r = json.loads(ln)
            att[(r.get("family"), r.get("bucket", "-"))].append(r)
    gen = {}
    for (fam, bucket), rs in sorted(att.items(), key=lambda kv: str(kv[0])):
        ids = {}
        for r in rs:                                       # an id retried later (resume) counts once, last verdict wins
            ids[r.get("id") or f"{r.get('seed')}:{r.get('param')}:{r.get('variant')}:{r.get('src')}:{len(ids)}"] = r
        rs = list(ids.values())
        real = [r for r in rs if r.get("reason") not in ("duplicate netlist", "no netlist") and not r.get("cached")]
        acc = [r for r in real if r["accepted"]]
        gen[f"{fam}/{bucket}"] = {
            "attempts": len(rs), "skipped_dup_or_cached": len(rs) - len(real), "gated": len(real), "accepted": len(acc),
            "yield": round(len(acc) / max(1, len(real)), 3),
            "reject_reasons": dict(Counter(reason_key(r.get("reason")) for r in real if not r["accepted"])),
            "seconds_median": statistics.median([r["seconds"] for r in real]) if real else None,
            "seconds_p90": pct([r["seconds"] for r in real]).get("p90"),
            "blocks": pct([r["blocks"] for r in acc if r.get("blocks")]),
        }
    stored = {}
    for fam_dir in sorted(p for p in C.TASKS.iterdir() if p.is_dir()):
        rows = [json.loads(p.read_text()) for p in sorted(fam_dir.glob("*.json"))]
        by = defaultdict(list)
        for d in rows:
            by[str(d.get("synth", {}).get("difficulty"))].append(len(d["solution"]))
        stored[fam_dir.name] = {"tasks": len(rows), "by_difficulty": {k: {"n": len(v), "blocks": pct(v)} for k, v in sorted(by.items())},
                                "kinds": dict(Counter(d.get("synth", {}).get("bug", {}).get("kind", "-") for d in rows))}
    sft = {}
    for shard in sorted(C.SFT.glob("*.jsonl")):
        metas, chars = [], []
        for ln in shard.read_text().splitlines():
            r = json.loads(ln)
            metas.append(r["meta"])
            chars.append(sum(len(m.get("content") or "") + sum(len(c["function"]["arguments"]) for c in m.get("tool_calls", []))
                             for m in r["messages"]))
        sft[shard.stem] = {"episodes": len(metas), "kinds": dict(Counter(m["kind"] for m in metas)),
                           "difficulty": dict(Counter(str(m["difficulty"]) for m in metas)),
                           "tasks": len({m["task"] for m in metas}), "turns": pct([m["turns"] for m in metas]),
                           "chars": pct(chars), "est_tokens_p50": int(pct(chars).get("p50", 0) / 3.6) if chars else 0,
                           "seconds_median": statistics.median([m.get("seconds", 0) for m in metas]) if metas else None}
    out = {"generation": gen, "stored": stored, "sft": sft,
           "totals": {"validated_tasks": sum(v["tasks"] for v in stored.values()), "episodes": sum(v["episodes"] for v in sft.values())}}
    if write:
        (C.DATA / "stats.json").write_text(json.dumps(out, indent=1) + "\n")
    return out
