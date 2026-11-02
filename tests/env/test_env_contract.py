import json

import pytest

from env.contract import Contract, Expect, Port, ReferenceModel, Vector
from env.models import get_model


def test_roundtrip_json_for_every_task():
    from env.task import list_task_ids, load_task
    for tid in list_task_ids():
        c = load_task(tid).contract
        d = json.loads(json.dumps(c.to_dict(), default=list))
        c2 = Contract.from_dict(d)
        assert c2.to_dict() == c.to_dict(), tid


def test_public_dict_hides_hidden_vectors_sequences_and_seeds(task):
    t = task("t1_rs_latch")
    pub = t.contract.public_dict()
    names = [s["name"] for s in pub["behaviour"]["sequences"]]
    assert "set_first" not in names and "reset_twice" not in names
    assert "reset_set_hold" in names
    assert pub["eval_seed"] is None and pub["reference"] == {}
    t = task("t1_xor")
    rows = t.contract.public_dict()["behaviour"]["truth_table"]["rows"]
    assert rows and all(r["public"] for r in rows)
    assert len(rows) < len(t.contract.behaviour.truth_table.rows)


def test_describe_never_mentions_hidden_sequences(task):
    c = task("t1_rs_latch").contract
    txt = c.describe(public=True)
    assert "set_first" not in txt and "reset_twice" not in txt
    assert "reset_set_hold" in txt
    assert "set_first" in c.describe(public=False)


def test_describe_documents_facing_conventions(task):
    txt = task("t0_not").contract.describe()
    assert "repeater" in txt and "FROM" in txt


def test_validate_flags_unknown_ports_and_bad_windows(task):
    c = Contract.from_dict(task("t0_lamp").contract.to_dict())
    c.behaviour.truth_table.rows.append(Vector({"Z": 1}, {"Y": 1}))
    assert any("unknown port Z" in e for e in c.validate())
    c = Contract.from_dict(task("t0_delay").contract.to_dict())
    c.behaviour.sequences[0].expect.append(Expect("Y", 1, 30, 999))
    assert any("bad window" in e for e in c.validate())
    c = Contract.from_dict(task("t0_delay").contract.to_dict())
    c.behaviour.sequences[0].expect.append(Expect("Y", 7, 0, 5))
    assert any("bad expected value" in e for e in c.validate())


def test_validate_requires_build_box_inside_bbox(task):
    c = Contract.from_dict(task("t0_lamp").contract.to_dict())
    c.build_box = [0, 1, 0, 99, 1, 0]
    assert "build_box not inside bbox" in c.validate()


def test_dependencies_derived_from_truth_table(task):
    assert task("t0_and").contract.dependencies() == {"Y": ["A", "B"]}
    assert task("t1_mux2").contract.dependencies() == {"Y": ["A", "B", "S"]}


def test_dependencies_from_sequences(task):
    assert task("t1_rs_latch").contract.dependencies() == {"Q": ["R", "S"]}


def test_reference_model_vectors_are_deterministic_and_split_public_hidden():
    rm = ReferenceModel("xor2", ["A", "B"], ["Y"], n_public=3, n_hidden=5, public_seed=1, hidden_seed=9)
    a, b = rm.generate(), rm.generate()
    assert [(v.inputs, v.expect) for v in a] == [(v.inputs, v.expect) for v in b]
    assert [v.public for v in a] == [True] * 3 + [False] * 5
    assert all(v.expect["Y"] == v.inputs["A"] ^ v.inputs["B"] for v in a)
    other = ReferenceModel("xor2", ["A", "B"], ["Y"], n_public=3, n_hidden=5, public_seed=1, hidden_seed=10).generate()
    assert [v.inputs for v in a[:3]] == [v.inputs for v in other[:3]] and [v.inputs for v in a[3:]] != [v.inputs for v in other[3:]]


def test_models_registry():
    assert get_model("full_adder")({}, {"A": 1, "B": 1, "CI": 1}) == {"S": 1, "CO": 1}
    assert get_model("adder")({}, {"A0": 1, "A1": 1, "B0": 1, "B1": 0}, bits=2) == {"S0": 0, "S1": 0, "CO": 1}
    with pytest.raises(KeyError):
        get_model("nope")


def test_port_kinds():
    assert Port("A", "click", (0, 0, 0)).is_input and not Port("A", "click", (0, 0, 0)).is_output
    assert Port("Y", "lamp", (0, 0, 0)).is_output
