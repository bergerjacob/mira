"""Signatures, clustering and the known-bug filter.

signature(case, div) -> {"build": symmetry-invariant hash of the minimised build, "mech": orientation-free
label of the first divergence, "phase", "ids": sorted component ids, "coarse": the cluster key}.

Known classes (docs/ORACLE.md, tools/oracle/circuits.py) are recognised by detectors that look only at the minimised
build and the two traces; they are NEVER used to seed or steer the generator, only to label what was found
(recall measurement) and to keep known divergences out of the "new" list.
"""
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).resolve().parents[2]))

from env import blocks as B  # noqa: E402
from scripts.diff_fuzz import canon, gen  # noqa: E402
from scripts.diff_fuzz.common import Case  # noqa: E402

PLAIN = {s.split(":")[1] for s in gen.SAFE_PLAIN} | {"smooth_stone_slab"}
ATTACHED = {"redstone_wire", "repeater", "comparator", "redstone_torch", "redstone_wall_torch", "lever",
            "stone_button", "oak_button", "rail", "powered_rail", "activator_rail", "detector_rail"}
USE_TOGGLE = {"repeater", "comparator", "note_block", "oak_trapdoor", "oak_fence_gate"}
KNOWN = {
    "comparator_side_lever": "K1 comparator with a lever as side input (docs/ORACLE.md item 1)",
    "dropper_item_qc": "K2 dropper triggered by an arriving item / QC (item 2)",
    "torch_burnout": "K3 torch burnout clock (item 3)",
    "popoff_static": "K4 component with no support at placement is kept by Nucleation (item 4)",
    "popoff_dynamic": "K4' component loses its support at run time (piston moves it) and is kept by Nucleation (same missing pop-off)",
    "known_shape": "K5 settle=known_shape is not vanilla knownShape (item 5)",
}
GAPS = {"use_toggle": "right-click on repeater/comparator/note/trapdoor/gate is not implemented in Nucleation (interaction gap)"}


def case_state(case: Case, pos) -> str:
    for b in case.blocks:
        if (b[0], b[1], b[2]) == tuple(pos):
            return b[3]
    return "minecraft:air"


def ids_of(blocks) -> List[str]:
    return sorted({B.short(b[3].split("[")[0]) for b in blocks})


def comp_ids(blocks) -> List[str]:
    return [i for i in ids_of(blocks) if i not in PLAIN]


def mech_ids(div: dict) -> set:
    return {c.split("[")[0].split("!=")[0] for c in div["label"].split(";")}


# stimulus devices and conductors that are interchangeable for the mechanic: dropped from the cluster key
GENERIC = {"lever", "stone_button", "oak_button", "redstone_wire", "redstone_block"}


def signature(case: Case, div: dict) -> dict:
    ids = comp_ids(case.blocks)
    core = [i for i in ids if i not in GENERIC] or ids
    return {"build": canon.build_hash(case.blocks), "mech": div["label"], "phase": div["phase"], "ids": ids,
            "core": core, "n": len(case.blocks), "coarse": f"{div['phase']}|{div['label']}|{','.join(core)}"}


def _toggles(flat_trace: dict, pos, key="lit") -> int:
    """Number of times property `key` of the block at `pos` changes during the run."""
    n, prev = 0, flat_trace["initial"].get(pos, "")
    for t in sorted(flat_trace["ticks"]):
        cur = flat_trace["ticks"][t].get(pos)
        if cur is None:
            continue
        a, b = canon.parse(prev)[1].get(key) if prev else None, canon.parse(cur)[1].get(key) if cur else None
        if a != b:
            n += 1
        prev = cur
    return n


def known_class(case: Case, div: dict, fv: Optional[dict] = None, fn: Optional[dict] = None) -> Optional[str]:
    """Name of the known Nucleation divergence this minimised case is an instance of, else None."""
    cells = {(b[0], b[1], b[2]): B.split_state(b[3]) for b in case.blocks}
    names = {p: B.short(bid) for p, (bid, _pr) in cells.items()}
    label = div["label"]
    # K5 (settle mode) is decided by the caller, which can rerun the case under settle=place (classify.py)
    # K1: a comparator with a lever on one of its two side cells; K1b (same side-input filter): the side cell is a
    # block that a lever/button is attached to (strongly powered conductor, not a signal source)
    for p, (bid, pr) in cells.items():
        if names[p] == "comparator":
            f = pr.get("facing", "north")
            sides = ("east", "west") if f in ("north", "south") else ("north", "south")
            for s in sides:
                q = gen.add(p, s)
                if names.get(q) in ("lever", "stone_button", "oak_button", "redstone_torch", "redstone_wall_torch") \
                        and "comparator[powered]" in label:
                    return "comparator_side_lever"      # K1 family: a non-dust/non-diode source on a comparator side
                if q in cells and names[q] not in ATTACHED and any(
                        names.get(gen.add(q, d)) in ("lever", "stone_button", "oak_button") and
                        B.attachment(case_state(case, gen.add(q, d))) and
                        gen.add(gen.add(q, d), B.attachment(case_state(case, gen.add(q, d)))[0]) == q for d in gen.D6):
                    if "comparator[powered]" in label:
                        return "comparator_side_lever"
    # K2: a dropper/dispenser whose `triggered` differs
    if any(c.startswith(("dropper[", "dispenser[")) and "triggered" in c for c in label.split(";")):
        return "dropper_item_qc"
    # K3: a redstone torch that burns out in vanilla (many lit toggles) and a differing toggle count in Nucleation
    if fv is not None and fn is not None:
        for p, nm in names.items():
            if nm in ("redstone_torch", "redstone_wall_torch"):
                tv, tn = _toggles(fv, p), _toggles(fn, p)
                if max(tv, tn) >= 4 and tv != tn:
                    return "torch_burnout"
    # K4: vanilla removed an attached component that Nucleation kept
    if any(c.startswith("air!=") and c.split("!=")[1] in ATTACHED for c in label.split(";")):
        return "popoff_static" if div["phase"] == "placed" else "popoff_dynamic"
    # interaction gap: right-click on a repeater/comparator/...
    if case.ops and any(names.get(tuple(p)) in USE_TOGGLE for _t, p in case.ops):
        return "use_toggle"
    return None


def cluster(items: List[dict]) -> Dict[str, List[dict]]:
    """Group finished minimisations by coarse key (phase|label|component ids)."""
    out: Dict[str, List[dict]] = {}
    for it in items:
        out.setdefault(it["sig"]["coarse"], []).append(it)
    return out
