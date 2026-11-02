"""Python reference models for `reference_model` behaviours: fn(state, inputs, **params) -> {output: 0/1}.
`state` is a dict that persists across the generated vectors of one stream (for sequential designs)."""

_MODELS = {}


def model(name):
    def deco(f):
        _MODELS[name] = f
        return f
    return deco


def get_model(name):
    if name not in _MODELS:
        raise KeyError(f"unknown reference model {name!r}; known: {sorted(_MODELS)}")
    return _MODELS[name]


@model("and2")
def and2(s, i):
    return {"Y": i["A"] & i["B"]}


@model("or2")
def or2(s, i):
    return {"Y": i["A"] | i["B"]}


@model("xor2")
def xor2(s, i):
    return {"Y": i["A"] ^ i["B"]}


@model("nand2")
def nand2(s, i):
    return {"Y": 1 - (i["A"] & i["B"])}


@model("mux2")
def mux2(s, i):
    return {"Y": i["B"] if i["S"] else i["A"]}


@model("half_adder")
def half_adder(s, i):
    return {"S": i["A"] ^ i["B"], "C": i["A"] & i["B"]}


@model("full_adder")
def full_adder(s, i):
    t = i["A"] + i["B"] + i["CI"]
    return {"S": t & 1, "CO": t >> 1}


@model("adder")
def adder(s, i, bits=2):
    a = sum(i[f"A{k}"] << k for k in range(bits))
    b = sum(i[f"B{k}"] << k for k in range(bits))
    t = a + b
    out = {f"S{k}": (t >> k) & 1 for k in range(bits)}
    out["CO"] = (t >> bits) & 1
    return out


@model("sr_latch")
def sr_latch(s, i):
    """Q after applying S/R; S=R=1 is forbidden by the generators using this model."""
    q = s.get("q", 0)
    if i["S"] and not i["R"]:
        q = 1
    elif i["R"] and not i["S"]:
        q = 0
    s["q"] = q
    return {"Q": q}
