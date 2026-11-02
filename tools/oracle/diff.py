"""Tick-by-tick comparison of two Traces (oracle vs Nucleation) with summary metrics."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from tools.oracle.trace import Plan, Pos, Trace, first_divergence, state_props

AIR = "minecraft:air"


@dataclass
class DiffResult:
    name: str
    mechanic: str
    placed_equal: bool
    placed_diffs: List[tuple]            # (pos, oracle_state, sim_state), capped
    ticks_total: int
    ticks_equal_full: int                # ticks where the whole region matches
    ticks_equal_probes: int              # ticks where every probe matches
    first_full: Optional[tuple]          # (tick, pos, oracle, sim)
    first_probe: Optional[tuple]
    probe_timeline: Dict[str, List[tuple]] = field(default_factory=dict)  # probe -> [(tick, oracle, sim)] at divergences
    expect_failures: List[tuple] = field(default_factory=list)            # oracle sanity
    sim_expect_failures: List[tuple] = field(default_factory=list)

    @property
    def agree_full(self) -> bool:
        return self.placed_equal and self.ticks_equal_full == self.ticks_total

    @property
    def agree_probes(self) -> bool:
        return self.ticks_equal_probes == self.ticks_total

    def to_json(self):
        d = dict(self.__dict__)
        d["agree_full"] = self.agree_full
        d["agree_probes"] = self.agree_probes
        return d


def _fmt(p):
    return None if p is None else {"tick": p[0], "pos": list(p[1]), "oracle": p[2], "sim": p[3]}


def region_equal_per_tick(a: Trace, b: Trace, positions=None) -> List[bool]:
    """For ticks 0..n-1: does the (optionally restricted) state of a equal b after that tick?"""
    n = min(a.n_ticks, b.n_ticks)
    sa, sb = a.state_at(-1), b.state_at(-1)
    keep = None if positions is None else set(positions)
    res = []
    for t in range(n):
        for p, _w, now in a.changes.get(t, ()):
            sa[p] = now
        for p, _w, now in b.changes.get(t, ()):
            sb[p] = now
        cells = set(sa) | set(sb) if keep is None else keep
        res.append(all(sa.get(c, AIR) == sb.get(c, AIR) or
                       (sa.get(c, AIR).startswith(AIR) and sb.get(c, AIR).startswith(AIR))
                       for c in cells))
    return res


def check_expect(trace: Trace, expect: Sequence[tuple]) -> List[tuple]:
    bad = []
    for tick, pos, sub in expect:
        got = trace.state_at(tick).get(tuple(pos), AIR)
        if sub not in got:
            bad.append((tick, tuple(pos), sub, got))
    return bad


def diff_traces(name: str, mechanic: str, oracle: Trace, sim: Trace, plan: Plan,
                expect: Sequence[tuple] = ()) -> DiffResult:
    cells = set(oracle.placed) | set(sim.placed)
    pdiff = [(p, oracle.placed.get(p, AIR), sim.placed.get(p, AIR)) for p in sorted(cells)
             if oracle.placed.get(p, AIR) != sim.placed.get(p, AIR)]
    full = region_equal_per_tick(oracle, sim)
    probes = [tuple(p) for p in plan.probes]
    prb = region_equal_per_tick(oracle, sim, probes) if probes else full
    ff = first_divergence(oracle, sim)
    fp = first_divergence(oracle, sim, probes) if probes else ff
    timeline = {}
    for p in probes:
        po, ps = oracle.probe(p), sim.probe(p)
        diffs = [(t, a, b) for t, (a, b) in enumerate(zip(po, ps)) if a != b]
        if diffs:
            timeline[",".join(map(str, p))] = diffs[:20]
    return DiffResult(
        name=name, mechanic=mechanic, placed_equal=not pdiff, placed_diffs=pdiff[:20],
        ticks_total=len(full), ticks_equal_full=sum(full), ticks_equal_probes=sum(prb),
        first_full=ff, first_probe=fp, probe_timeline=timeline,
        expect_failures=check_expect(oracle, expect), sim_expect_failures=check_expect(sim, expect))


def summarize(results: Sequence[DiffResult]) -> Dict[str, dict]:
    """Per-mechanic agreement: circuits agreeing fully / on probes / on placement."""
    out: Dict[str, dict] = {}
    for r in results:
        m = out.setdefault(r.mechanic, {"circuits": 0, "agree_full": 0, "agree_probes": 0,
                                        "placed_equal": 0, "ticks": 0, "ticks_equal_full": 0,
                                        "ticks_equal_probes": 0, "names": []})
        m["circuits"] += 1
        m["agree_full"] += r.agree_full
        m["agree_probes"] += r.agree_probes
        m["placed_equal"] += r.placed_equal
        m["ticks"] += r.ticks_total
        m["ticks_equal_full"] += r.ticks_equal_full
        m["ticks_equal_probes"] += r.ticks_equal_probes
        m["names"].append(r.name)
    return out
