"""Confirmation protocol and certificates for a minimised divergence.

For every reported class representative:
  1. isolated JVM (one fresh vanilla server per capture, no batch state) at the case's origin and at a second origin;
  2. the same at two other orientations (rotation 90, and mirror-x + rotation 270) via the dihedral transform of
     scripts/diff_fuzz/canon.py (same blockstate tables as env/variants.py);
  3. a no-stimulus control (ops removed): the divergence must disappear for a stimulus-caused class
     (`causal`); a class that diverges without any stimulus is flagged `static`;
  4. the batched trace hash must equal the isolated one (otherwise the divergence is a batching artefact).
The certificate records everything needed to replay it:  python scripts/diff_fuzz/confirm.py replay CERT.json
"""
import argparse
import hashlib
import importlib.metadata
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.diff_fuzz import canon, engine, minimise, signature  # noqa: E402
from scripts.diff_fuzz.common import FUZZ_DIR, ROOT, Case, jdump, jload  # noqa: E402
from tools.oracle import gametest  # noqa: E402

ORIENTATIONS = [(90, None), (270, "x")]


def second_origin(o):
    return (o[0] + 48, o[1] - 4, o[2] - 64)


def provenance() -> dict:
    v = gametest.versions()
    try:
        nv = importlib.metadata.version("nucleation")
    except Exception:
        nv = "unknown"
    return {"mc_version": v.get("mc_version"), "data_version": v.get("data_version"),
            "server_jar_sha1": v.get("server_jar_sha1"), "oracle_nucleation_ref": v.get("nucleation_ref"),
            "nucleation_python": nv, "java": v.get("java")}


def experiments(case: Case) -> List[tuple]:
    """[(name, Case)] the confirmation runs."""
    out = [("origin1", case), ("origin2", case.with_(origin=second_origin(case.origin)))]
    for rot, mir in ORIENTATIONS:
        out.append((f"rot{rot}{'+m' + mir if mir else ''}", canon.transform_case(case, rot, mir)))
    out.append(("control_no_stimulus", case.with_(ops=[])))
    return out


def confirm(case: Case, d0: dict, workers: int = 3, batched_hash: Optional[str] = None) -> dict:
    exps = experiments(case)
    with ThreadPoolExecutor(workers) as ex:
        fvs = list(ex.map(lambda e: engine.run_vanilla([e[1]], ["iso"], isolated=True)[0], exps))
    runs = {}
    for (name, c), fv in zip(exps, fvs):
        if isinstance(fv, engine.EngineError):
            runs[name] = {"error": str(fv)}
            continue
        try:
            fn = engine.run_nucleation(c)
        except engine.EngineError as e:
            runs[name] = {"error": str(e)}
            continue
        d = canon.first_divergence(fv, fn)
        runs[name] = {"diverges": d is not None, "same_mechanic": minimise.keeps(d0, d), "phase": d and d["phase"],
                      "tick": d and d["tick"], "label": d and d["label"],
                      "vanilla_hash": canon.trace_hash(fv), "nucleation_hash": canon.trace_hash(fn),
                      "origin": list(c.origin), "n_blocks": len(c.blocks)}
    r1, r2 = runs.get("origin1", {}), runs.get("origin2", {})
    rots = [runs[k] for k in runs if k.startswith("rot")]
    ctrl = runs.get("control_no_stimulus", {})
    has_ops = bool(case.ops)
    verdict = {
        "isolated_reproduces": bool(r1.get("same_mechanic")),
        "origin2_reproduces": bool(r2.get("same_mechanic")),
        "orientations_reproduce": f"{sum(bool(r.get('same_mechanic')) for r in rots)}/{len(rots)}",
        "control_diverges": bool(ctrl.get("same_mechanic")) if has_ops else None,
        "causal": (not ctrl.get("same_mechanic")) if has_ops else None,
        "batched_matches_isolated": None if batched_hash is None else batched_hash == r1.get("vanilla_hash"),
    }
    verdict["confirmed"] = verdict["isolated_reproduces"] and verdict["origin2_reproduces"] and (verdict["causal"] in (True, None))
    return {"runs": runs, "verdict": verdict}


def certificate(case: Case, div: dict, sig: dict, conf: dict, known: Optional[str], hypothesis: str = "") -> dict:
    cert = {
        "provenance": provenance(),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "build": [list(b) for b in sorted(case.blocks, key=lambda b: (b[1], b[2], b[0]))],
        "plan": {"ops": [[t, list(p)] for t, p in sorted(case.ops)], "ticks": case.ticks, "preroll": case.preroll,
                 "settle": case.settle},
        "origins": {"primary": list(case.origin), "secondary": list(second_origin(case.origin))},
        "first_divergence": div,
        "signature": sig,
        "known_class": known,
        "minimised_blocks": len(case.blocks),
        "confirmation": conf,
        "hypothesis": hypothesis,
        "replay": "python scripts/diff_fuzz/confirm.py replay <this file>",
    }
    cert["id"] = hashlib.sha1(json.dumps([cert["build"], cert["plan"], cert["origins"]["primary"]]).encode()).hexdigest()[:12]
    return cert


def replay(path: str) -> int:
    cert = json.loads(Path(path).read_text())
    case = Case([(b[0], b[1], b[2], b[3], b[4] if len(b) > 4 else None) for b in cert["build"]],
                [(t, tuple(p)) for t, p in cert["plan"]["ops"]], cert["plan"]["ticks"], cert["plan"]["preroll"],
                cert["plan"]["settle"], tuple(cert["origins"]["primary"]))
    prov = provenance()
    same_jar = prov["server_jar_sha1"] == cert["provenance"]["server_jar_sha1"]
    d, fv, fn = engine.diff_one(case, isolated=True)
    r1 = cert["confirmation"]["runs"]["origin1"]
    ok = d is not None and canon.trace_hash(fv) == r1["vanilla_hash"] and canon.trace_hash(fn) == r1["nucleation_hash"]
    print(f"{cert['id']}: same jar {same_jar}; diverges {d is not None}; vanilla/nucleation trace hashes match the certificate: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "replay":
        sys.exit(replay(sys.argv[2]))
    print(__doc__)
