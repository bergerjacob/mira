"""HPC files are not runnable here (no trl/peft/verl); check they at least parse and that the verl tool state is the harness."""
import json
import py_compile
from pathlib import Path

import pytest

from harness.reference import builder_source

ROOT = Path(__file__).resolve().parents[2]
HPC = [ROOT / "training" / "sft" / "hpc", ROOT / "training" / "rl" / "hpc"]


@pytest.mark.parametrize("path", [p for d in HPC for p in sorted(d.glob("*.py"))], ids=lambda p: p.name)
def test_hpc_python_compiles(path):
    py_compile.compile(str(path), doraise=True)


def test_hpc_configs_parse():
    yaml = pytest.importorskip("yaml")
    for d in HPC:
        for p in d.glob("*.yaml"):
            assert isinstance(yaml.safe_load(p.read_text()), dict), p
        for p in d.glob("*.json"):
            json.loads(p.read_text())


def test_verl_tool_state_is_the_harness(task):
    from training.rl.hpc.verl_mira_tool import MiraEpisodeState
    t = task("t0_lamp")
    st = MiraEpisodeState("t0_lamp", rounds=1)
    assert st.call("build", {"source": builder_source(t.solution)}).startswith("build loaded")
    assert "PASS" in st.call("run_spec", {}).upper()
    reward, fin = st.final_reward()
    assert reward == pytest.approx(1.0) and fin["full_pass"]
