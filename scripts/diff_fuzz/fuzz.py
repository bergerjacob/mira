"""Differential fuzzer driver (family D, Nucleation vs vanilla 26.2).  See docs/DIFF_FUZZ.md.

    python scripts/diff_fuzz/fuzz.py run --run pilot1 --budget 480          # resumable; stops starting chunks at the budget
    python scripts/diff_fuzz/fuzz.py status --run pilot1
    python scripts/diff_fuzz/fuzz.py recall --run pilot1                    # settle=known_shape and pop-off arms (known classes 4, 5)
    python scripts/diff_fuzz/minimise.py --run pilot1 ...                   # delta debugging + signatures (see that file)

State lives in data/fuzz/<run>/: config.json, chunks/chunk_<id>.json (one per finished chunk: counts, label
histogram, the smallest divergent cases per label). A chunk is deterministic in (seed, id), so killing the process
loses at most the chunks in flight; rerunning continues with the missing ids.
"""
import argparse
import multiprocessing as mp
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.diff_fuzz import canon, engine, gen, seeds  # noqa: E402
from scripts.diff_fuzz.common import FUZZ_DIR, Case, jdump, jload  # noqa: E402

ARM_CYCLE = ["grammar"] * 6 + ["mut_corpus"] * 2 + ["mut_env", "mut_lib"]
KEEP_PER_LABEL = 6
KEEP_PER_CHUNK = 80


def run_dir(name: str) -> Path:
    return FUZZ_DIR / name


def origins_for(rng: random.Random):
    return [(rng.randint(-40, 40) * 2, rng.choice([100, 100, 96, 80]), rng.randint(-40, 40) * 2) for _ in range(3)]


def make_cases(arm: str, rng: random.Random, n: int, pool=None):
    origins = origins_for(rng)
    out = []
    for _ in range(n):
        if arm == "grammar":
            out.append(gen.generate(rng, origins))
        elif arm.startswith("mut_"):
            out.append(seeds.mutant(rng, pool, origins, arm, None if arm == "mut_any" else arm[4:]))
        elif arm == "recall_known_shape":
            c = gen.generate(rng, origins, arm)
            out.append(c.with_(settle="known_shape"))
        elif arm == "recall_popoff":
            out.append(recall_popoff(rng, origins))
        else:
            raise ValueError(arm)
    return out


def recall_popoff(rng, origins):
    """A grammar case with one attachable component's support removed (inadmissible by construction)."""
    for _ in range(100):
        c = gen.generate(rng, origins, "recall_popoff")
        cells = {(b[0], b[1], b[2]): b for b in c.blocks}
        att = [p for p, b in cells.items() if gen.B.attachment(b[3]) and gen.B.short(gen.B.split_state(b[3])[0]) in
               ("redstone_wall_torch", "redstone_torch", "lever", "redstone_wire", "repeater", "comparator")]
        if not att:
            continue
        p = rng.choice(att)
        d = gen.B.attachment(cells[p][3])[0]
        sup = gen.add(p, d)
        if sup not in cells:
            continue
        del cells[sup]
        return c.with_(blocks=list(cells.values()), meta={**c.meta, "removed_support": list(sup)})
    raise RuntimeError("popoff arm failed")


def process_chunk(cfg: dict, cid: int, sem, pool, arm: str = None) -> dict:
    t0 = time.time()
    arm = arm or ARM_CYCLE[cid % len(ARM_CYCLE)]
    rng = random.Random(f"{cfg['seed']}:{arm}:{cid}")
    cases = make_cases(arm, rng, cfg["chunk_size"], pool)
    for i, c in enumerate(cases):
        c.meta.update({"chunk": cid, "i": i})
    t_gen = time.time() - t0
    t1 = time.time()
    with sem:
        t_wait = time.time() - t1
        t2 = time.time()
        fvs = engine.run_vanilla(cases, [f"c{cid}_{i}" for i in range(len(cases))], idle=cfg.get("idle", 5))
        t_van = time.time() - t2
    t3 = time.time()
    labels, nuc_errs, van_errs, kept_by = Counter(), Counter(), Counter(), {}
    n_div = 0
    for c, fv in zip(cases, fvs):
        if isinstance(fv, engine.EngineError):
            van_errs[str(fv)[:100]] += 1
            continue
        try:
            fn = engine.run_nucleation(c)
        except engine.EngineError as e:
            nuc_errs[str(e)[:100]] += 1
            kept_by.setdefault("NUC_ERROR:" + str(e)[:60], []).append((len(c.blocks), c, {"phase": "error", "label": str(e)[:100]}))
            continue
        d = canon.first_divergence(fv, fn)
        if d is None:
            continue
        n_div += 1
        key = f"{d['phase']}|{d['label']}"
        labels[key] += 1
        kept_by.setdefault(key, []).append((len(c.blocks), c, d))
    kept = []
    for key, lst in kept_by.items():
        lst.sort(key=lambda t: t[0])
        for _n, c, d in lst[:KEEP_PER_LABEL]:
            kept.append({"case": c.to_json(), "div": {k: d[k] for k in d if k != "cells"} | {"cells": [[list(p), v, n] for p, v, n in d.get("cells", [])]}})
    kept = kept[:KEEP_PER_CHUNK]
    res = {"chunk": cid, "arm": arm, "n": len(cases), "n_div": n_div, "n_van_err": sum(van_errs.values()),
           "n_nuc_err": sum(nuc_errs.values()), "labels": dict(labels), "nuc_errs": dict(nuc_errs),
           "van_errs": dict(van_errs), "kept": kept,
           "t": {"gen": round(t_gen, 2), "wait": round(t_wait, 2), "vanilla": round(t_van, 2),
                 "nuc": round(time.time() - t3, 2), "total": round(time.time() - t0, 2)}}
    jdump(run_dir(cfg["name"]) / "chunks" / f"chunk_{cid:06d}.json", res)
    return res


def worker(cfg, counter, lock, deadline, sem, pool, arm, out_q):
    while True:
        with lock:
            while True:
                cid = counter.value
                counter.value += 1
                if cid >= cfg["max_chunks"] or time.time() > deadline:
                    out_q.put(None)
                    return
                if not (run_dir(cfg["name"]) / "chunks" / f"chunk_{cid:06d}.json").exists():
                    break
        try:
            r = process_chunk(cfg, cid, sem, pool, arm)
            out_q.put({k: r[k] for k in ("chunk", "arm", "n", "n_div", "n_van_err", "n_nuc_err", "t")})
        except Exception as e:
            out_q.put({"chunk": cid, "error": f"{type(e).__name__}: {e}"})


def cmd_run(a):
    name = a.run
    rd = run_dir(name)
    cfg = jload(rd / "config.json") or {"name": name, "seed": a.seed, "chunk_size": a.chunk_size, "max_chunks": a.max_chunks, "idle": a.idle}
    cfg["max_chunks"] = a.max_chunks if a.max_chunks else cfg["max_chunks"]
    cfg["chunk_size"] = a.chunk_size
    jdump(rd / "config.json", cfg)
    pool = seeds.load_pool()
    deadline = time.time() + a.budget
    ctx = mp.get_context("fork")
    sem, lock, counter, q = ctx.Semaphore(a.jvms), ctx.Lock(), ctx.Value("i", a.start), ctx.Queue()
    procs = [ctx.Process(target=worker, args=(cfg, counter, lock, deadline, sem, pool, a.arm, q)) for _ in range(a.workers)]
    t0 = time.time()
    for p in procs:
        p.start()
    done_workers, n, ndiv = 0, 0, 0
    while done_workers < len(procs):
        m = q.get()
        if m is None:
            done_workers += 1
            continue
        if "error" in m:
            print("CHUNK ERROR", m, flush=True)
            continue
        n += m["n"]
        ndiv += m["n_div"]
        print(f"chunk {m['chunk']:5d} {m['arm']:10s} n={m['n']} div={m['n_div']} van_err={m['n_van_err']} nuc_err={m['n_nuc_err']} "
              f"vanilla={m['t']['vanilla']}s wait={m['t']['wait']}s nuc={m['t']['nuc']}s total={m['t']['total']}s", flush=True)
    for p in procs:
        p.join()
    el = time.time() - t0
    print(f"done: {n} captures in {el:.0f}s = {n / max(el, 1e-9):.1f}/s, {ndiv} divergent")


def totals(name: str):
    rd = run_dir(name) / "chunks"
    tot = Counter()
    labels, arms = Counter(), {}
    t_van = t_all = 0.0
    for f in sorted(rd.glob("chunk_*.json")):
        c = jload(f)
        tot["chunks"] += 1
        for k in ("n", "n_div", "n_van_err", "n_nuc_err"):
            tot[k] += c[k]
        labels.update(c["labels"])
        a = arms.setdefault(c["arm"], Counter())
        a["n"] += c["n"]
        a["n_div"] += c["n_div"]
        t_van += c["t"]["vanilla"]
        t_all += c["t"]["total"]
    return tot, labels, arms, t_van, t_all


def cmd_status(a):
    tot, labels, arms, t_van, t_all = totals(a.run)
    print(dict(tot), f"vanilla JVM-seconds {t_van:.0f}, chunk-seconds {t_all:.0f}")
    for k, v in arms.items():
        print(f"  arm {k}: {v['n']} captures, {v['n_div']} divergent ({100 * v['n_div'] / max(v['n'], 1):.2f}%)")
    for k, v in labels.most_common(25):
        print(f"  {v:7d}  {k}")


def cmd_recall(a):
    a.arm = None
    for arm in ("recall_known_shape", "recall_popoff"):
        ns = argparse.Namespace(**vars(a))
        ns.run, ns.arm = a.run + "_" + arm, arm
        cmd_run(ns)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("run", "status", "recall"):
        p = sub.add_parser(nm)
        p.add_argument("--run", required=True)
        p.add_argument("--seed", type=int, default=1)
        p.add_argument("--chunk-size", type=int, default=600)
        p.add_argument("--max-chunks", type=int, default=100000)
        p.add_argument("--start", type=int, default=0)
        p.add_argument("--budget", type=float, default=480, help="seconds during which new chunks may start")
        p.add_argument("--workers", type=int, default=5)
        p.add_argument("--jvms", type=int, default=3)
        p.add_argument("--idle", type=int, default=5)
        p.add_argument("--arm", default=None, help="force one arm (default: the mixed cycle)")
    a = ap.parse_args()
    {"run": cmd_run, "status": cmd_status, "recall": cmd_recall}[a.cmd](a)


if __name__ == "__main__":
    main()
