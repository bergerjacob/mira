"""Rewards R0-R4 and model-facing diagnostics from a RunResult.

    reward = 0.1*R0 + 0.1*R1 + 0.1*R2 + 0.6*R3*[R1==1] + 0.1*R4*[R3==1]

R0 compiles (gate) | R1 structural invariants | R2 input->output reach | R3 balanced accuracy, constant output = 0,
min over outputs | R4 size vs reference, only after a full pass. Hidden vectors count in the scores but are never
described in the text.
"""
from dataclasses import dataclass, field

from env import blocks as B
from env.contract import Contract, box_volume
from env.runner import RunResult

W = {"r0": 0.1, "r1": 0.1, "r2": 0.1, "r3": 0.6, "r4": 0.1}
STRUCTURAL = ("no_forbidden", "no_popoff", "at_rest", "paste_stable")
BEHAVIOURAL = ("no_oscillation", "deterministic", "order_independent", "no_collateral", "keep_clear")
PARTIAL_CAP = 0.99  # R3 can only reach 1.0 when every behavioural invariant also holds


@dataclass
class Score:
    r0: float = 0.0
    r1: float = 0.0
    r2: float = 0.0
    r3: float = 0.0
    r4: float = 0.0
    reward: float = 0.0
    full_pass: bool = False          # every vector/sequence and every invariant
    r3_raw: float = 0.0              # before the invariant cap
    per_output: dict = field(default_factory=dict)   # output -> rescaled balanced accuracy
    n_vectors: int = 0
    n_vectors_ok: int = 0
    n_hidden: int = 0
    n_hidden_ok: int = 0
    blocks: int = 0
    volume: int = 0

    def to_dict(self):
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def balanced(samples):
    """samples: [(expected, ok)]. Balanced accuracy rescaled so chance/constant = 0; one class -> plain accuracy."""
    by = {}
    for e, ok in samples:
        by.setdefault(e, []).append(ok)
    if not by:
        return 0.0
    recalls = [sum(v) / len(v) for v in by.values()]
    if len(recalls) == 1:
        return recalls[0]
    return max(0.0, 2 * sum(recalls) / len(recalls) - 1)


def volume_of(build):
    if not build:
        return 0
    cs = list(build)
    lo = [min(c[i] for c in cs) for i in range(3)]
    hi = [max(c[i] for c in cs) for i in range(3)]
    return box_volume(lo + hi)


def score(contract: Contract, res: RunResult) -> Score:
    s = Score()
    pr = res.prepared
    s.blocks, s.volume = len(pr.build), volume_of(pr.build)
    if pr.errors or not res.compiled:
        return s
    s.r0 = 1.0
    # R1
    checks = [(n, res.invariants[n]["ok"]) for n in STRUCTURAL if n in res.invariants]
    checks.append(("engine_placement", not res.place_errors))
    s.r1 = sum(ok for _, ok in checks) / len(checks)
    # R2
    if res.connectivity:
        s.r2 = sum(c["reached"] for c in res.connectivity.values()) / len(res.connectivity)
    else:
        s.r2 = 1.0
    # R3
    samples = {}
    for v in res.vectors:
        s.n_vectors += 1
        s.n_vectors_ok += v.ok
        if not v.public:
            s.n_hidden += 1
            s.n_hidden_ok += v.ok
        for k, e in v.expect.items():
            if e is not None:
                samples.setdefault(k, []).append((e, v.got.get(k) == e and v.settled))
    for q in res.sequences:
        s.n_vectors += 1
        s.n_vectors_ok += q.ok
        if not q.public:
            s.n_hidden += 1
            s.n_hidden_ok += q.ok
        for w in q.windows:
            samples.setdefault(w.port, []).append((w.value, w.ok))
    s.per_output = {k: balanced(v) for k, v in samples.items()}
    s.r3_raw = min(s.per_output.values()) if s.per_output else 0.0
    beh_ok = all(res.invariants.get(n, {"ok": True})["ok"] for n in BEHAVIOURAL)
    s.r3 = s.r3_raw if beh_ok else min(s.r3_raw, PARTIAL_CAP)
    r1_pass = s.r1 == 1.0
    s.full_pass = s.r3_raw == 1.0 and beh_ok and r1_pass
    # R4 only counts once R3 is a full pass
    if s.r3 == 1.0 and r1_pass:
        ref = contract.reference or {}
        rb, rv = ref.get("blocks"), ref.get("volume")
        eff = []
        if rb:
            eff.append(min(1.0, rb / max(1, s.blocks)))
        if rv:
            eff.append(min(1.0, rv / max(1, s.volume)))
        s.r4 = sum(eff) / len(eff) if eff else 1.0
    s.reward = (W["r0"] * s.r0 + W["r1"] * s.r1 + W["r2"] * s.r2 + W["r3"] * s.r3 * r1_pass + W["r4"] * s.r4 * (s.r3 == 1.0))
    return s


# --------------------------------------------------------------------------- diagnostics
def _p(pos):
    return "(" + ",".join(map(str, pos)) + ")"


def feedback(contract: Contract, res: RunResult, sc: Score, final: bool = False, max_fail: int = 3) -> str:
    """Structured text for the model. Public vectors are described; hidden ones only as a count (and only
    when `final`)."""
    pr = res.prepared
    L = []
    L.append(f"BUILD: {sc.blocks} blocks, bounding volume {sc.volume}")
    if pr.errors:
        L.append("R0 COMPILE: FAIL")
        L += [f"  - {e}" for e in pr.errors]
        L.append(f"SCORE: reward={sc.reward:.2f} (fix compile errors first)")
        return "\n".join(L)
    for w in pr.warnings[:3]:
        L.append(f"  note: {w}")
    if not res.compiled:
        L.append("R0 COMPILE: FAIL - simulator refused to load")
        return "\n".join(L)
    L.append("R0 COMPILE: ok")
    bad = [(n, v) for n, v in res.invariants.items() if n in STRUCTURAL and not v["ok"]]
    if bad or res.place_errors:
        L.append("R1 STRUCTURE: FAIL")
        for n, v in bad:
            L.append(f"  - {n}: {v['detail'] or 'violated'}")
        for pos, st, msg in res.place_errors[:3]:
            L.append(f"  - engine could not place {B.short(B.split_state(st)[0])} at {_p(pos)}: {msg}")
    else:
        L.append("R1 STRUCTURE: ok (at rest, paste-stable, nothing floating)")
    L.append(f"R2 CONNECTIVITY: {sc.r2:.0%} of required input->output paths reached")
    for (i, o), c in res.connectivity.items():
        if not c["reached"]:
            L.append(f"  - {i} -> {o}: no circuit path from {i} to {o}: turning {i} on (in any context of the other inputs) changes nothing next to {o}")
    L.append(f"R3 BEHAVIOUR (public vectors): "
             f"{sum(v.ok for v in res.vectors if v.public) + sum(q.ok for q in res.sequences if q.public)}/"
             f"{sum(v.public for v in res.vectors) + sum(q.public for q in res.sequences)} pass")
    shown = 0
    for v in res.vectors:
        if v.ok or not v.public or shown >= max_fail:
            continue
        shown += 1
        exp = {k: e for k, e in v.expect.items() if e is not None}
        L.append(f"  FAIL vector: set {v.inputs} -> expected {exp}, got {v.got}"
                 + ("" if v.settled else "  [never settled: oscillating?]"))
        if v.diverge:
            d = v.diverge
            L.append(f"    first divergence: {d['port']} expected {d['expected']} but is {d['got']} from tick {d['tick']} on; "
                     f"waveforms: " + "; ".join(f"{k}: {t}" for k, t in v.trace.items()))
    for q in res.sequences:
        if q.ok or not q.public or shown >= max_fail:
            continue
        shown += 1
        for w in q.windows:
            if not w.ok:
                L.append(f"  FAIL sequence '{q.name}': {w.port} should be {w.value} {'throughout' if w.mode == 'all' else 'at some tick'} "
                         f"t={w.t1}..{w.t2}; first bad tick {w.first_bad}; waveform {w.port}: {q.trace[w.port]}")
                break
    if sc.per_output:
        L.append("  per-output score (balanced accuracy, constant output = 0): " + ", ".join(f"{k}={v:.2f}" for k, v in sc.per_output.items()))
    for n in BEHAVIOURAL:
        v = res.invariants.get(n)
        if v and not v["ok"]:
            L.append(f"  invariant {n}: {v['detail'] or 'violated'}")
    if res.silent:
        L.append("SILENT components (never changed in any test - dead branch or never powered): "
                 + ", ".join(f"{B.short(B.split_state(s)[0])}{_p(p)}" for p, s in res.silent[:8]) + (" ..." if len(res.silent) > 8 else ""))
    if final:
        L.append(f"HIDDEN checks: {sc.n_hidden_ok}/{sc.n_hidden} pass (details withheld)")
        L.append(f"SCORE: reward={sc.reward:.3f}  R0={sc.r0:.2f} R1={sc.r1:.2f} R2={sc.r2:.2f} R3={sc.r3:.2f} R4={sc.r4:.2f}  "
                 f"{'FULL PASS' if sc.full_pass else 'not a full pass'}")
    else:
        L.append(f"SCORE (public checks only): R1={sc.r1:.2f} R2={sc.r2:.2f} R3={sc.r3:.2f}" + ("  ALL PUBLIC CHECKS PASS" if sc.full_pass else ""))
    return "\n".join(L)
