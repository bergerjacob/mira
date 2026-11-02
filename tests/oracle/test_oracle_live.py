"""Tests that need the vanilla oracle (scripts/oracle_setup.sh); skipped when it isn't installed."""
import pytest

from tools.oracle import gametest
from tools.oracle.circuits import all_circuits
from tools.oracle.diff import diff_traces
from tools.oracle.sim import run_plan

pytestmark = pytest.mark.skipif(not gametest.available(), reason="vanilla oracle not installed")

CIRCUITS = {c.name: c for c in all_circuits(repros=True)}
# Divergences found 2026-11-01 (docs/ORACLE.md): Nucleation 0.12.0 vs vanilla 26.2. strict xfail: when
# upstream fixes one, the test XPASSes and fails loudly so the doc/list gets updated.
KNOWN_DIVERGENT = {"torch_clock_burnout", "comparator_subtract", "comparator_side_lever", "dropper_chain",
                   "dropper_feed_dropper_qc", "torch_unsupported"}
AGREEING = [n for n in CIRCUITS if n not in KNOWN_DIVERGENT]


@pytest.fixture(scope="module")
def captured():
    """All circuits in one JVM (also the batching path under test)."""
    traces = gametest.capture_batch([(n, c.blocks, c.plan) for n, c in CIRCUITS.items()])
    return dict(zip(CIRCUITS, traces))


def _diff(captured, name):
    c, ot = CIRCUITS[name], captured[name]
    return diff_traces(name, c.mechanic, ot, run_plan(c.blocks, c.plan, origin=ot.origin), c.plan, c.expect)


def test_oracle_sanity_expectations(captured):
    """Circuits with expectations must behave as designed in vanilla (else the circuit is wrong)."""
    for name, c in CIRCUITS.items():
        for tick, pos, sub in c.expect:
            assert sub in captured[name].state_at(tick).get(pos, "air"), (name, tick, pos)


def test_oracle_reports_version(captured):
    tr = captured["lever_wire_lamp"]
    assert tr.mc_version == "26.2" and tr.data_version == 4903


def test_batch_matches_fresh_jvm(captured):
    """A job in a multi-job JVM must equal the same job in its own JVM (isolation between jobs)."""
    for name in ("torch_not_gate", "dropper_chain", "sticky_piston_rb"):
        c = CIRCUITS[name]
        alone = gametest.capture(c.blocks, c.plan, name=name)
        assert alone.changes == captured[name].changes and alone.placed == captured[name].placed


@pytest.mark.parametrize("name", AGREEING)
def test_nucleation_agrees_with_vanilla(captured, name):
    r = _diff(captured, name)
    assert r.agree_full, r.first_full


@pytest.mark.parametrize("name", sorted(KNOWN_DIVERGENT))
@pytest.mark.xfail(strict=True, reason="known Nucleation 0.12.0 vs vanilla 26.2 divergence")
def test_known_divergences(captured, name):
    assert _diff(captured, name).agree_full
