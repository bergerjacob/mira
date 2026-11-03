#!/usr/bin/env python3
"""Contamination ledger + decontaminated SFT export (round-3 audit H1).

A synthetic task is contaminated when its reference solution is (up to translation) identical to a benchmark task in
env/tasks (T0-T5), or when it comes from a generator lineage that re-creates benchmark references (rpe_* repairs of env
references, sq_counter*/sq_shift* parameter sweeps of T3/T4 tasks, reference fixtures).

Writes data/synth/contamination_ledger.json and data/synth/sft_clean/*.jsonl (rows whose task is clean).
Usage: python scripts/synth_decontaminate.py [--out data/synth/sft_clean]
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LINEAGE_PREFIXES = ("rpe_", "sq_counter", "sq_shift", "rp_sq_shift")   # re-create benchmark references or sweeps of them
FILE_BLOCKLIST = ("repair_env.jsonl",)


def solution_hash(blocks):
    """Translation-invariant hash of a flat block list [[x,y,z,state,...], ...]."""
    if not blocks:
        return None
    mn = [min(b[i] for b in blocks) for i in range(3)]
    rows = sorted((b[0] - mn[0], b[1] - mn[1], b[2] - mn[2], b[3]) for b in blocks)
    return hashlib.sha1(json.dumps(rows).encode()).hexdigest()


def benchmark_hashes():
    out = {}
    for p in sorted((ROOT / "env/tasks").rglob("*.json")):
        if p.name in ("variants_report.json", "oracle_report.json"):
            continue
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("solution"):
            h = solution_hash(d["solution"])
            if h:
                out[h] = p.stem
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data/synth/sft_clean"))
    a = ap.parse_args()
    bench = benchmark_hashes()
    ledger, contaminated = {}, set()
    for p in sorted((ROOT / "data/synth/tasks").rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        tid = (d.get("contract") or {}).get("id") or p.stem
        h = solution_hash(d.get("solution"))
        reason = None
        if h in bench:
            reason = f"identical solution to benchmark task {bench[h]}"
        elif tid.startswith(LINEAGE_PREFIXES):
            reason = "generator lineage re-creates benchmark references"
        ledger[tid] = {"hash": h, "file": str(p.relative_to(ROOT)), "contaminated": bool(reason), "reason": reason}
        if reason:
            contaminated.add(tid)
    (ROOT / "data/synth").mkdir(parents=True, exist_ok=True)
    (ROOT / "data/synth/contamination_ledger.json").write_text(json.dumps(ledger, indent=1))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    kept = dropped = 0
    for f in sorted((ROOT / "data/synth/sft").glob("*.jsonl")):
        if f.name in FILE_BLOCKLIST:
            dropped += sum(1 for _ in f.open())
            continue
        with (out / f.name).open("w") as w:
            for line in f.open():
                r = json.loads(line)
                t = r.get("meta", {}).get("task", "")
                bt = r.get("meta", {}).get("base_task") or ""
                if t in contaminated or bt in contaminated or t.startswith(LINEAGE_PREFIXES) or re.match(r"t[0-5]_", bt or ""):
                    dropped += 1
                    continue
                w.write(line)
                kept += 1
    print(f"benchmark hashes: {len(bench)}; synth tasks: {len(ledger)}, contaminated: {len(contaminated)}")
    print(f"episodes kept {kept}, dropped {dropped} -> {out}")


if __name__ == "__main__":
    main()
