"""Delta-debugging minimiser (ddmin over blocks, then ops, then plan length) for divergent cases.

A candidate is a case with a block set removed plus the dependency closure (anything that loses its support goes
with it, so every candidate is admissible); it "keeps" the divergence when vanilla and Nucleation still differ with
the same phase and an overlapping mechanic label. The result is 1-minimal: removing any single block (with its
dependents) or any single stimulus op, or shortening the run, no longer reproduces it.

Many minimisations advance in lock step: each round gathers the candidates of every active minimisation into one
vanilla batch split over up to `jvms` JVMs (boot is the dominant cost of a batch, so rounds must be fat).

CLI (resumable; state in data/fuzz/<run>/min/):
    python scripts/diff_fuzz/minimise.py --run pilot1 --per-label 3 --budget 420
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).resolve().parents[2]))

from env import blocks as B  # noqa: E402
from scripts.diff_fuzz import canon, engine, gen  # noqa: E402
from scripts.diff_fuzz.common import FUZZ_DIR, Case, jdump, jload  # noqa: E402

MAX_G = 24


def labels_of(d: dict) -> set:
    return set(d["label"].split(";")) if d and d.get("label") else set()


def keeps(d0: dict, d: Optional[dict]) -> bool:
    return d is not None and d["phase"] == d0["phase"] and bool(labels_of(d0) & labels_of(d))


def admissible(blocks) -> bool:
    if not blocks:
        return False
    cells = {(b[0], b[1], b[2]): b[3] for b in blocks}
    try:
        if not all(gen.simulatable(B.canonical(s)) for s in set(cells.values())):
            return False
    except Exception:
        return False
    return not gen.bad_attachments(cells, {(b[0], b[1], b[2]): b[4] for b in blocks if b[4]})


def close_removal(case: Case, drop: set) -> Optional[Case]:
    """`case` without the cells in `drop` and everything that then floats; ops on vanished cells are dropped."""
    cells = {(b[0], b[1], b[2]): b for b in case.blocks if (b[0], b[1], b[2]) not in drop}
    while True:
        bad = gen.bad_attachments({p: b[3] for p, b in cells.items()}, {p: b[4] for p, b in cells.items() if b[4]})
        if not bad:
            break
        for p in bad:
            cells.pop(p, None)
    if not cells:
        return None
    mn = [min(p[i] for p in cells) for i in range(3)]
    sh = lambda p: (p[0] - mn[0], p[1] - mn[1], p[2] - mn[2])
    blocks = [(*sh(p), b[3], b[4]) for p, b in cells.items()]
    ops = [(t, sh(tuple(p))) for t, p in case.ops if tuple(p) in cells]
    # origin is the world position of the (new) min corner: keep it fixed, the structure is re-based
    return case.with_(blocks=blocks, ops=ops)


class Min:
    """One minimisation as a state machine: candidates() -> cases to test, feed(results) -> next state."""

    def __init__(self, mid: str, case: Case, d0: dict):
        self.id, self.cur, self.d0 = mid, case, d0
        self.last = d0
        self.stage, self.g, self.done = "blocks", 2, False
        self.tried = set()
        self.rounds = self.evals = 0
        self.pass2 = False

    # ---- persistence
    def to_json(self):
        return {"id": self.id, "cur": self.cur.to_json(), "d0": self.d0, "last": self.last, "stage": self.stage,
                "g": self.g, "done": self.done, "tried": sorted(self.tried), "rounds": self.rounds,
                "evals": self.evals, "pass2": self.pass2}

    @staticmethod
    def from_json(d):
        m = Min(d["id"], Case.from_json(d["cur"]), d["d0"])
        m.last, m.stage, m.g, m.done = d["last"], d["stage"], d["g"], d["done"]
        m.tried, m.rounds, m.evals, m.pass2 = set(d["tried"]), d["rounds"], d["evals"], d.get("pass2", False)
        return m

    # ---- stage logic
    def _order(self):
        return sorted(self.cur.blocks, key=lambda b: (b[1], b[2], b[0]))

    def candidates(self) -> List[Case]:
        while not self.done:
            cands = self._gen()
            cands = [c for c in cands if c.key() not in self.tried]
            if cands:
                return cands
            self._advance_without_success()
        return []

    def _gen(self) -> List[Case]:
        if self.stage == "blocks":
            L = self._order()
            n = len(L)
            g = min(self.g, n, MAX_G)
            size = -(-n // g)
            out, seen = [], set()
            for i in range(0, n, size):
                drop = {(b[0], b[1], b[2]) for b in L[i:i + size]}
                c = close_removal(self.cur, drop)
                if c is not None and len(c.blocks) < n and c.key() not in seen and admissible(c.blocks):
                    seen.add(c.key())
                    out.append(c)
            return out
        if self.stage == "ops":
            out = []
            if len(self.cur.ops) >= 1:
                out.append(self.cur.with_(ops=[]))
            if len(self.cur.ops) >= 2:
                for i in range(len(self.cur.ops)):
                    out.append(self.cur.with_(ops=self.cur.ops[:i] + self.cur.ops[i + 1:]))
            return out
        if self.stage == "plan":
            out = []
            t_stop = (self.last["tick"] + 4) if self.last["phase"] == "tick" else 4
            if t_stop < self.cur.ticks:
                out.append(self.cur.with_(ticks=max(t_stop, 2)))
            if self.cur.preroll:
                out.append(self.cur.with_(preroll=0))
            return out
        return []

    def _advance_without_success(self):
        if self.stage == "blocks":
            n = len(self.cur.blocks)
            if self.g < min(n, MAX_G):
                self.g = min(n, MAX_G, self.g * 2)
            else:
                self.stage, self.g = "ops", 2
        elif self.stage == "ops":
            self.stage = "plan"
        elif self.stage == "plan":
            if self.pass2:
                self.done = True
            else:
                self.pass2, self.stage, self.g = True, "blocks", 2

    def feed(self, results: Dict[str, Optional[dict]], cands: List[Case]):
        """results: case key -> divergence dict (or None / error) for the candidates requested."""
        self.rounds += 1
        self.evals += len(cands)
        hits = []
        for c in cands:
            d = results.get(c.key())
            if keeps(self.d0, d):
                hits.append((c, d))
            else:
                self.tried.add(c.key())
        if not hits:
            self._advance_without_success()
            return
        hits.sort(key=lambda h: (len(h[0].blocks), len(h[0].ops), h[0].ticks))
        self.cur, self.last = hits[0]
        if self.stage == "blocks":
            self.g = max(2, self.g - 1)
        # ops / plan stages stay and are retried on the shrunken case


def run_round(mins: List[Min], jvms: int, idle: int = 10, isolated: bool = False) -> Dict[str, Dict[str, Optional[dict]]]:
    """Evaluate the candidates of every active minimisation; vanilla in `jvms` parallel batches."""
    want: Dict[str, Case] = {}
    per_min: Dict[str, List[Case]] = {}
    for m in mins:
        cs = m.candidates()
        per_min[m.id] = cs
        for c in cs:
            want.setdefault(c.key(), c)
    keys = list(want)
    cases = [want[k] for k in keys]
    if not cases:
        return {}, per_min
    shards = [list(range(i, len(cases), jvms)) for i in range(jvms) if i < len(cases)]
    results: Dict[str, Optional[dict]] = {}

    def vanilla(idx):
        return engine.run_vanilla([cases[i] for i in idx], [f"m{i}" for i in idx], idle=idle, isolated=isolated)

    with ThreadPoolExecutor(len(shards)) as ex:
        outs = list(ex.map(vanilla, shards))
    for idx, fvs in zip(shards, outs):
        for i, fv in zip(idx, fvs):
            if isinstance(fv, engine.EngineError):
                results[keys[i]] = None
                continue
            try:
                fn = engine.run_nucleation(cases[i])
            except engine.EngineError:
                results[keys[i]] = None
                continue
            results[keys[i]] = canon.first_divergence(fv, fn)
    return {m.id: {c.key(): results.get(c.key()) for c in per_min[m.id]} for m in mins}, per_min


def finish(m: Min) -> dict:
    """Final record of a minimised case (the case, its divergence and traces' facts) for signature/confirmation."""
    return {"id": m.id, "case": m.cur.to_json(), "div": _slim(m.last), "orig_label": m.d0["label"],
            "orig_blocks": m.d0.get("n_blocks"), "rounds": m.rounds, "evals": m.evals, "n_blocks": len(m.cur.blocks)}


def _slim(d):
    if d is None:
        return None
    return {**{k: d[k] for k in d if k != "cells"}, "cells": [[list(p), v, n] for p, v, n in d["cells"]]}


def render(case: Case) -> str:
    """ASCII layers (y up, x right, z down) with a legend; for reports."""
    legend, rows = {}, []
    sym = lambda st: legend.setdefault(st, chr(ord("A") + len(legend)) if len(legend) < 26 else "?")
    xs = [b[0] for b in case.blocks]
    ys = [b[1] for b in case.blocks]
    zs = [b[2] for b in case.blocks]
    cells = {(b[0], b[1], b[2]): b[3] for b in case.blocks}
    for y in range(min(ys), max(ys) + 1):
        rows.append(f"y={y}")
        for z in range(min(zs), max(zs) + 1):
            rows.append(" ".join(sym(cells[(x, y, z)]) if (x, y, z) in cells else "." for x in range(min(xs), max(xs) + 1)))
    rows.append("legend: " + "; ".join(f"{v}={k.replace('minecraft:', '')}" for k, v in legend.items()))
    rows.append(f"ops: {case.ops} ticks={case.ticks} preroll={case.preroll} origin={case.origin}")
    return "\n".join(rows)


# ------------------------------------------------------------------------------------------------- CLI
def select_candidates(run: str, per_label: int, max_total: int, skip: set):
    """Pick divergent cases to minimise from the kept lists: a few smallest per (phase|label), diverse by arm/seed."""
    by = {}
    for f in sorted((FUZZ_DIR / run / "chunks").glob("chunk_*.json")):
        c = jload(f)
        for k in c["kept"]:
            d = k["div"]
            if d["phase"] == "error":
                continue
            by.setdefault(f"{d['phase']}|{d['label']}", []).append(k)
    picks = []
    for key, lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lst.sort(key=lambda k: len(k["case"]["blocks"]))
        seen_arm = set()
        chosen = []
        for k in lst:
            tag = (k["case"]["meta"].get("arm"), k["case"]["meta"].get("seed"))
            if tag in seen_arm and len(chosen) >= 1:
                continue
            seen_arm.add(tag)
            chosen.append(k)
            if len(chosen) >= per_label:
                break
        picks += [(key, k) for k in chosen]
    out = []
    for key, k in picks:
        case = Case.from_json(k["case"])
        mid = case.key()
        if mid not in skip:
            out.append((mid, key, case, k["div"]))
    return out[:max_total]


def cmd(a):
    run = a.run
    mdir = FUZZ_DIR / run / "min"
    mdir.mkdir(parents=True, exist_ok=True)
    states = {}
    for f in mdir.glob("m_*.json"):
        d = jload(f)
        states[d["id"]] = Min.from_json(d)
    finished = {f.stem[2:] for f in mdir.glob("f_*.json")}
    started = set(states) | finished
    for mid, key, case, d in select_candidates(run, a.per_label, a.max_cases, started):
        if len(states) + len(finished) >= a.max_cases:
            break
        d0 = {**d, "cells": [[tuple(p), v, n] for p, v, n in d["cells"]]}
        d0["n_blocks"] = len(case.blocks)
        states[mid] = Min(mid, case, d0)
    t0 = time.time()
    while time.time() - t0 < a.budget:
        active = [m for m in states.values() if not m.done]
        if not active:
            break
        res, per_min = run_round(active, a.jvms, idle=a.idle)
        for m in active:
            if per_min[m.id]:
                m.feed(res[m.id], per_min[m.id])
            if m.done:
                try:
                    jdump(mdir / f"f_{m.id}.json", finish(m))
                except Exception as e:
                    jdump(mdir / f"f_{m.id}.json", {"id": m.id, "error": str(e)})
                (mdir / f"m_{m.id}.json").unlink(missing_ok=True)
            else:
                jdump(mdir / f"m_{m.id}.json", m.to_json())
        states = {i: m for i, m in states.items() if not m.done}
        print(f"round: {len(active)} active, {sum(len(v) for v in per_min.values())} candidates, "
              f"sizes {sorted(len(m.cur.blocks) for m in active)[:12]}..., {time.time() - t0:.0f}s", flush=True)
    left = sum(1 for m in states.values() if not m.done)
    print(f"minimisation: {len(list(mdir.glob('f_*.json')))} finished, {left} still active")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--per-label", type=int, default=3)
    ap.add_argument("--max-cases", type=int, default=150)
    ap.add_argument("--budget", type=float, default=420)
    ap.add_argument("--jvms", type=int, default=3)
    ap.add_argument("--idle", type=int, default=5)
    cmd(ap.parse_args())
