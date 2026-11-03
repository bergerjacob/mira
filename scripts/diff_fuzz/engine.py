"""Differential runner: the same Case through Nucleation (tools.oracle.sim) and vanilla 26.2 (tools.oracle.gametest),
compared on the whole-region per-tick trace after canonicalisation (scripts/diff_fuzz/canon.py).

Vanilla runs batched (one JVM per `run_vanilla` call: boot 2.5 s, then ~10-30 ms per small job); callers keep batches
large and cap concurrent JVMs themselves (`fuzz.py` uses a semaphore of 3). Confirmations use `isolated=True`.
"""
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.diff_fuzz import canon  # noqa: E402
from scripts.diff_fuzz.common import Case  # noqa: E402
from tools.oracle import gametest, sim as nsim  # noqa: E402


class EngineError(RuntimeError):
    pass


def run_nucleation(case: Case):
    """-> flattened Nucleation trace, or raises EngineError (simulator refused or crashed)."""
    try:
        tr = nsim.run_plan(case.blocks, case.plan(), origin=tuple(case.origin))
    except BaseException as e:      # pyo3 panics derive from BaseException
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        raise EngineError(f"{type(e).__name__}: {str(e)[:200]}")
    return canon.flat(tr, canon.region_box(case.blocks))


def _vanilla_job(name: str, case: Case):
    return (name, case.blocks, case.plan())


def run_vanilla(cases: Sequence[Case], names: Optional[Sequence[str]] = None, isolated: bool = False,
                workers: int = 1, timeout: float = 900.0, idle: int = 10) -> List[object]:
    """-> per case a flattened vanilla trace or an EngineError. One JVM for the whole call (isolated: one per case).

    A failing job makes `capture_batch` raise for the whole batch, so a failed batch is bisected until the failing
    jobs are isolated; those return an EngineError and the rest keep their traces."""
    names = list(names) if names is not None else [f"j{i}" for i in range(len(cases))]
    jobs = [_vanilla_job(n, c) for n, c in zip(names, cases)]
    out: List[object] = [None] * len(cases)

    def go(lo: int, hi: int):
        if lo >= hi:
            return
        try:
            trs = gametest.capture_batch(jobs[lo:hi], isolated=isolated, workers=workers, timeout=timeout, idle=idle)
            for i, tr in zip(range(lo, hi), trs):
                out[i] = canon.flat(tr, canon.region_box(cases[i].blocks))
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            if hi - lo == 1:
                out[lo] = EngineError(f"vanilla: {type(e).__name__}: {str(e)[:300]}")
                return
            mid = (lo + hi) // 2
            go(lo, mid)
            go(mid, hi)
    go(0, len(cases))
    return out


def compare_case(case: Case, fv, fn) -> Optional[dict]:
    """None when identical, else the first-divergence record (canon.first_divergence)."""
    return canon.first_divergence(fv, fn)


def diff_one(case: Case, isolated: bool = True):
    """Convenience for confirmation/minimisation of a single case: (divergence|None, flat_vanilla, flat_nucleation)."""
    fv = run_vanilla([case], ["one"], isolated=isolated)[0]
    if isinstance(fv, EngineError):
        raise fv
    fn = run_nucleation(case)
    return canon.first_divergence(fv, fn), fv, fn


def diff_many(cases: Sequence[Case], workers: int = 1, isolated: bool = False, idle: int = 10):
    """[(divergence|None | EngineError, flat_vanilla, flat_nucleation)] for a list of cases, vanilla batched."""
    names = [f"d{i}" for i in range(len(cases))]
    fvs = run_vanilla(cases, names, isolated=isolated, workers=workers, idle=idle)
    out = []
    for c, fv in zip(cases, fvs):
        if isinstance(fv, EngineError):
            out.append((fv, None, None))
            continue
        try:
            fn = run_nucleation(c)
        except EngineError as e:
            out.append((e, fv, None))
            continue
        out.append((canon.first_divergence(fv, fn), fv, fn))
    return out
