"""Canonicalisation: (1) builds up to rotation / mirror / translation, (2) traces for comparison and the first
divergence between a vanilla trace and a Nucleation trace.

Symmetry group: the 8 elements of the dihedral group about the y axis (4 rotations x mirror none/x), then translation
of the min corner to the origin. Vanilla update order is NOT rotation invariant, so the group is used to MERGE
reports (same bug seen in another orientation), never to assume that a rotated build behaves identically.
"""
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.diff_fuzz.common import MARGIN, Case  # noqa: E402
from env.blocks import canonical as _canonical  # noqa: E402
from simulation.blockstates import rotate_point, transform_state  # noqa: E402

ELEMENTS = [(rot, mirror) for mirror in (None, "x") for rot in (0, 90, 180, 270)]
AIR = "minecraft:air"
_STATE = re.compile(r"^([^\[]+)(?:\[(.*)\])?$")


def parse(state: str) -> Tuple[str, Dict[str, str]]:
    m = _STATE.match(state)
    bid, props = m.group(1), m.group(2)
    return bid, dict(p.split("=", 1) for p in props.split(",")) if props else {}


def short(bid: str) -> str:
    return bid.split(":", 1)[-1]


# ----------------------------------------------------------------------------------------------- symmetry
def transform_blocks(blocks, rot: int = 0, mirror: Optional[str] = None):
    """Mirror, rotate (clockwise from above), then translate so the min corner is (0,0,0)."""
    moved = []
    for x, y, z, st, nbt in blocks:
        px, py, pz = rotate_point((x, y, z), rot, mirror)
        moved.append((px, py, pz, transform_state(st, rot, mirror), nbt))
    if not moved:
        return []
    mn = [min(b[i] for b in moved) for i in range(3)]
    return [(b[0] - mn[0], b[1] - mn[1], b[2] - mn[2], b[3], b[4]) for b in moved]


def transform_case(case: Case, rot: int = 0, mirror: Optional[str] = None) -> Case:
    """The same experiment under one group element (blocks, ops re-based to the new min corner)."""
    pts = [(b[0], b[1], b[2]) for b in case.blocks]
    moved = [rotate_point(p, rot, mirror) for p in pts]
    mn = [min(m[i] for m in moved) for i in range(3)]
    blocks = [(m[0] - mn[0], m[1] - mn[1], m[2] - mn[2], transform_state(b[3], rot, mirror), b[4])
              for m, b in zip(moved, case.blocks)]
    ops = []
    for t, p in case.ops:
        q = rotate_point(tuple(p), rot, mirror)
        ops.append((t, (q[0] - mn[0], q[1] - mn[1], q[2] - mn[2])))
    return case.with_(blocks=blocks, ops=ops)


def _key(blocks) -> tuple:
    return tuple(sorted((x, y, z, s, n or "") for x, y, z, s, n in blocks))


def canonical_build(blocks) -> Tuple[tuple, int]:
    """(lexicographically smallest sorted tuple over the 8 symmetries, index of the element that gives it)."""
    best, which = None, 0
    for i, (rot, mirror) in enumerate(ELEMENTS):
        k = _key(transform_blocks(blocks, rot, mirror))
        if best is None or k < best:
            best, which = k, i
    return best, which


def build_hash(blocks) -> str:
    """Symmetry-invariant identity of a build."""
    return hashlib.sha1(json.dumps(canonical_build(blocks)[0]).encode()).hexdigest()[:12]


def exact_hash(blocks) -> str:
    return hashlib.sha1(json.dumps(_key(blocks)).encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------------------------- traces
def region_box(blocks, margin: int = MARGIN):
    """Inclusive box (lo, hi) the oracle records for a build, in the caller's frame."""
    lo = [min(b[i] for b in blocks) - margin for i in range(3)]
    hi = [max(b[i] for b in blocks) + margin + 1 for i in range(3)]   # OracleBatch max = origin + size + MARGIN
    return lo, hi


def _in(p, box) -> bool:
    lo, hi = box
    return all(lo[i] <= p[i] <= hi[i] for i in range(3))


_NZ: Dict[str, str] = {}


def _nz(state: str) -> str:
    """Normalised cell value: every air variant is the empty string; omitted properties are filled with the
    registry defaults (Nucleation's snapshot writes a default redstone wire as bare `minecraft:redstone_wire`,
    vanilla writes every property)."""
    r = _NZ.get(state)
    if r is None:
        if state.startswith(AIR) or state.startswith("minecraft:cave_air") or state.startswith("minecraft:void_air"):
            r = ""
        else:
            try:
                r = _canonical(state)
            except Exception:
                r = state
        _NZ[state] = r
    return r


def flat(trace, box) -> dict:
    """Trace -> {"placed": {pos: s}, "initial": {...}, "ticks": {t: {pos: s}}} clipped to `box`, air = ""."""
    keep = lambda d: {p: _nz(s) for p, s in d.items() if _in(p, box) and _nz(s)}
    ticks = {}
    for t, cs in trace.changes.items():
        row = {p: _nz(now) for p, _was, now in cs if _in(p, box)}
        if row:
            ticks[t] = row
    return {"placed": keep(trace.placed), "initial": keep(trace.initial), "ticks": ticks, "n": trace.n_ticks}


def cell_label(vs: str, ns: str) -> str:
    """Orientation-free description of how two cell values differ: block ids, or the property names that differ."""
    vb, vp = parse(vs) if vs else (AIR, {})
    nb, np_ = parse(ns) if ns else (AIR, {})
    if vb != nb:
        return f"{short(vb)}!={short(nb)}"
    keys = sorted(k for k in set(vp) | set(np_) if vp.get(k) != np_.get(k))
    return f"{short(vb)}[{','.join(keys)}]"


def _cells_diff(a: dict, b: dict) -> List[tuple]:
    return [(p, a.get(p, ""), b.get(p, "")) for p in sorted(set(a) | set(b), key=lambda q: (q[1], q[2], q[0]))
            if a.get(p, "") != b.get(p, "")]


def first_divergence(fv: dict, fn: dict) -> Optional[dict]:
    """First difference between flattened vanilla (`fv`) and Nucleation (`fn`) traces, else None.

    {phase: placed|initial|tick, tick, cells: [(pos, vanilla, nucleation)] (cells differing at that moment, <= 8),
     label: orientation-free mechanic string, div_ticks: ticks with at least one differing cell, v_events, n_events}
    """
    out = None
    for phase in ("placed", "initial"):
        d = _cells_diff(fv[phase], fn[phase])
        if d:
            out = {"phase": phase, "tick": -1, "cells": d[:8]}
            break
    sv, sn = dict(fv["initial"]), dict(fn["initial"])
    bad = {p for p, _a, _b in _cells_diff(sv, sn)}
    div_ticks = 0
    n = min(fv["n"], fn["n"])
    for t in range(n):
        rv, rn = fv["ticks"].get(t, {}), fn["ticks"].get(t, {})
        for p, s in rv.items():
            if s:
                sv[p] = s
            else:
                sv.pop(p, None)
        for p, s in rn.items():
            if s:
                sn[p] = s
            else:
                sn.pop(p, None)
        for p in set(rv) | set(rn):
            if sv.get(p, "") != sn.get(p, ""):
                bad.add(p)
            else:
                bad.discard(p)
        if bad:
            div_ticks += 1
            if out is None:
                cells = sorted(bad, key=lambda q: (q[1], q[2], q[0]))
                out = {"phase": "tick", "tick": t, "cells": [(p, sv.get(p, ""), sn.get(p, "")) for p in cells[:8]]}
    if out is None:
        return None
    out["label"] = ";".join(sorted({cell_label(v, n_) for _p, v, n_ in out["cells"]})[:4])
    out["div_ticks"] = div_ticks
    out["v_events"] = sum(len(r) for r in fv["ticks"].values())
    out["n_events"] = sum(len(r) for r in fn["ticks"].values())
    return out


def trace_hash(flat_trace: dict) -> str:
    """Stable hash of a flattened trace (what a certificate records)."""
    def enc(d):
        return sorted((list(p), s) for p, s in d.items())
    body = [enc(flat_trace["placed"]), enc(flat_trace["initial"]),
            [[t, enc(r)] for t, r in sorted(flat_trace["ticks"].items())], flat_trace["n"]]
    return hashlib.sha1(json.dumps(body).encode()).hexdigest()[:16]
