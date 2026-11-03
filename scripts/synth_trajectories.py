"""Scripted builder-arm SFT episodes (no LLM) from the stored synthetic tasks -> data/synth/sft/<out>.jsonl.

    .venv/bin/python scripts/synth_trajectories.py --family netlist --out netlist --workers 8
    .venv/bin/python scripts/synth_trajectories.py --family repair --out repair --kinds repair
    .venv/bin/python scripts/synth_trajectories.py --family netlist --match sn_easy --limit 20 --out smoke

Per task the default kinds are oneshot, verify, debug, debug_view (+ typo for every third task); repair tasks get `repair`
(+ verify). Rows already in the output file (same task + kind) are skipped, so a run can be resumed. Every row was replayed
through harness.agent.Episode with real env outputs and ends in a full-pass submit (meta.verified).
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from training.synth import common as C  # noqa: E402

DEFAULT_KINDS = ("oneshot", "verify", "debug", "debug_view", "typo")


def job(args):
    path, kind, seed = args
    from training.synth import bugs as G
    from training.synth import trajectories as T
    t0 = time.time()
    task, ref, synth = C.load(path)
    family = synth.get("family", Path(path).parent.name)
    info = {"netlist": synth.get("netlist"), "plan": synth.get("plan"), "router": synth.get("router")}
    rng = random.Random(f"{task.id}:{kind}:{seed}")
    bug, tried = None, 0
    try:
        if kind in ("debug", "debug_view"):
            for b in G.ordered(ref, rng)[:12]:
                tried += 1
                if G.verify_bug(task, b["text"])[0]:
                    bug = b
                    break
            if bug is None:
                return {"skip": f"no failing program bug ({tried} tried)", "task": task.id, "kind": kind}
            synth = {**synth, "bug_kind": bug["kind"]}
        elif kind == "typo":
            text, name = G.typo(ref, rng)
            if text is None:
                return {"skip": "no typo site", "task": task.id, "kind": kind}
            bug = {"text": text, "name": name}
            synth = {**synth, "bug_kind": "compile_typo"}
        elif kind == "repair":
            bug = {"text": Path(path).with_suffix(".broken.py").read_text(), **synth["bug"]}
            synth = {**synth, "bug_kind": synth["bug"]["kind"]}
        ep, final = T.episode(task, kind, ref, info, bug=bug, view_layer=synth.get("view_layer", 1), seed=seed)
    except Exception as e:
        return {"skip": f"error {type(e).__name__}: {str(e)[:200]}", "task": task.id, "kind": kind}
    if ep is None:
        return {"skip": f"episode not verified (reward {final.get('reward')}, ended {final.get('ended_by')})", "task": task.id, "kind": kind}
    row = T.record(task, "extend" if family == "extend" and kind == "repair" else kind, ep, final, synth, family)
    row["meta"]["seconds"] = round(time.time() - t0, 1)
    return row


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="netlist")
    ap.add_argument("--match", default="")
    ap.add_argument("--kinds", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stride", type=int, default=1, help="use every n-th task (subsample)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=C.MAX_WORKERS)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    paths = [p for p in C.stored(a.family) if a.match in p.stem and not p.stem.endswith(".broken")]
    paths = paths[::a.stride]
    if a.limit:
        paths = paths[:a.limit]
    out = C.SFT / f"{a.out or a.family}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for ln in out.read_text().splitlines():
            m = json.loads(ln)["meta"]
            done.add((m["task"], "repair" if m["kind"] == "extend" else m["kind"]))
    jobs = []
    for i, p in enumerate(paths):
        kinds = a.kinds.split(",") if a.kinds else (["repair", "verify"] if a.family in ("repair", "repair_env", "extend") else
                                                    [k for k in DEFAULT_KINDS if k != "typo" or i % 3 == 0])
        jobs += [(str(p), k, a.seed + i) for k in kinds if (p.stem, k) not in done]
    print(f"{len(jobs)} episodes to run ({len(done)} already in {out})", flush=True)
    ok = skipped = 0
    with out.open("a") as fh, (C.DATA / "trajectory_log.jsonl").open("a") as log:
        for r in C.pmap(job, jobs, a.workers):
            if "skip" in r:
                skipped += 1
                log.write(json.dumps(r) + "\n")
                print(f"skip {r['task']} {r['kind']}: {r['skip']}", flush=True)
                continue
            ok += 1
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            m = r["meta"]
            log.write(json.dumps({"task": m["task"], "kind": m["kind"], "ok": True, "seconds": m["seconds"], "turns": m["turns"]}) + "\n")
            if ok % 10 == 0:
                print(f"{ok} episodes written ({skipped} skipped)", flush=True)
    print(f"done: {ok} episodes written to {out}, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
