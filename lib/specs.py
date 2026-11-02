"""Hand-authored specs of the library components (the source of the generated lib/*.meta.json)."""

SPECS = {}


def comb(name, title, description, inputs, outputs, fn, **kw):
    SPECS[name] = dict(title=title, description=description, inputs=inputs, outputs=outputs, fn=fn, kind="comb", **kw)


comb("not_gate", "NOT gate", "Torch inverter: Y = !A.", ["A"], ["Y"], lambda A: 1 - A)
comb("nor2", "NOR gate", "Y = !(A | B).", ["A", "B"], ["Y"], lambda A, B: 1 - (A | B))
comb("or2", "OR gate", "Y = A | B (dust merge + repeater restore).", ["A", "B"], ["Y"], lambda A, B: A | B)
comb("and2", "AND gate", "Y = A & B (NOR of inverted inputs).", ["A", "B"], ["Y"], lambda A, B: A & B)
comb("half_adder", "Half adder", "S = A ^ B, C = A & B (comparator XOR).", ["A", "B"], ["S", "C"], lambda A, B: (A ^ B, A & B))
comb("full_adder", "1-bit full adder", "S = A ^ B ^ CIN, COUT = majority(A, B, CIN).", ["A", "B", "CIN"], ["S", "COUT"],
     lambda A, B, CIN: ((A + B + CIN) & 1, (A + B + CIN) >> 1))
comb("xor2", "XOR gate", "Y = A ^ B (two subtract comparators facing one block).", ["A", "B"], ["Y"], lambda A, B: A ^ B)
comb("mux2", "2:1 multiplexer", "Y = B if S else A.", ["A", "B", "S"], ["Y"], lambda A, B, S: B if S else A)
comb("demux2", "1-to-2 decoder cell with enable", "Y0 = EN & !A, Y1 = EN & A (one-hot demultiplexer; chain cells for n-to-2^n decoders).",
     ["EN", "A"], ["Y0", "Y1"], lambda EN, A: (EN & (1 - A), EN & A))


def pulse(port, t, width=8):
    return [(t, {port: 1}), (t + width, {port: 0})]


SPECS["sr_latch"] = dict(
    title="RS latch", kind="seq", description="NOR latch: pulse on S sets Q, pulse on R clears it (S and R never together).",
    inputs=["R", "S"], outputs=["Q"], settle=200,
    sequences=[
        dict(name="reset_set_hold", events=pulse("R", 0) + pulse("S", 20), expect=[["Q", 0, 14, 19], ["Q", 1, 34, 60]], duration=60),
        dict(name="set_reset_hold", events=pulse("R", 0) + pulse("S", 20) + pulse("R", 50),
             expect=[["Q", 1, 34, 49], ["Q", 0, 64, 90]], duration=90),
        dict(name="set_first", events=pulse("S", 0) + pulse("R", 30) + pulse("S", 60),
             expect=[["Q", 1, 14, 29], ["Q", 0, 44, 59], ["Q", 1, 74, 100]], duration=100),
        dict(name="reset_twice", events=pulse("S", 0) + pulse("R", 20) + pulse("R", 40) + pulse("S", 70) + pulse("S", 90),
             expect=[["Q", 0, 34, 69], ["Q", 1, 84, 120]], duration=120)],
    notes="Q is undefined before the first pulse (the latch comes up in whatever state the placement order leaves), so every use starts with a reset pulse.")

SPECS["edge_detector"] = dict(
    title="Rising-edge detector (pulse limiter)", kind="seq",
    description="Y pulses briefly when A rises; it is dark while A is held and on the falling edge.",
    inputs=["A"], outputs=["Y"], settle=200,
    sequences=[
        dict(name="rise", events=[(10, {"A": 1})], expect=[["Y", 0, 0, 9], ["Y", 1, 10, 24, "any"], ["Y", 0, 34, 80]], duration=80),
        dict(name="rise_fall_rise", events=[(10, {"A": 1}), (60, {"A": 0}), (110, {"A": 1})],
             expect=[["Y", 1, 10, 24, "any"], ["Y", 0, 34, 59], ["Y", 0, 60, 100], ["Y", 1, 110, 124, "any"], ["Y", 0, 134, 170]], duration=170)])

SPECS["t_flipflop"] = dict(
    title="T flip-flop", kind="seq", description="Q toggles on every rising edge of T.", inputs=["T"], outputs=["Q"], settle=300,
    sequences=[
        dict(name="toggle", events=pulse("T", 0, 20) + pulse("T", 60, 20) + pulse("T", 120, 20),
             expect=[["Q", "init", 0, 0], ["Q", "!init", 25, 55], ["Q", "init", 85, 115], ["Q", "!init", 145, 180]], duration=180),
        dict(name="toggle4", events=pulse("T", 0, 20) + pulse("T", 50, 20) + pulse("T", 100, 20) + pulse("T", 150, 20),
             expect=[["Q", "!init", 25, 45], ["Q", "init", 75, 95], ["Q", "!init", 125, 145], ["Q", "init", 175, 200]], duration=200),
        dict(name="short_pulses", events=pulse("T", 0, 10) + pulse("T", 60, 10) + pulse("T", 120, 10),
             expect=[["Q", "!init", 25, 55], ["Q", "init", 85, 115], ["Q", "!init", 145, 180]], duration=180)],
    notes="Q changes 6 ticks after the rising edge of T. Nucleation: pulses 2..20 ticks wide toggle correctly and rising edges 12 (4-tick pulses) / 16 (10-tick pulses) ticks apart still toggle; the sequences here and the oracle runs use edges >= 50 ticks apart, so closer spacing is not verified against vanilla.")

comb("restorer", "Signal-strength restorer", "Y = A at full strength 15 for any input strength >= 1.", ["A"], ["Y"], lambda A: A)


def _delay_seqs(params):
    t = params.get("ticks", 8)
    return [dict(name="rise_fall", events=[(10, {"A": 1}), (10 + 3 * t + 40, {"A": 0})],
                 expect=[["Y", 0, 0, 10 + t - 1], ["Y", 1, 10 + t + 2, 10 + 3 * t + 40 + t - 2], ["Y", 0, 10 + 3 * t + 40 + t + 2, 10 + 3 * t + 40 + t + 30]],
                 duration=10 + 3 * t + 40 + t + 40)]


SPECS["delay_line"] = dict(
    title="Repeater delay line", kind="seq", description="Y follows A, delayed by `ticks` game ticks (rises and falls).",
    inputs=["A"], outputs=["Y"], sequences=_delay_seqs, characterize=True, variants=[{"ticks": 2}, {"ticks": 8}, {"ticks": 20}, {"ticks": 36}],
    notes="Measured delay = ticks (+/- 1 on the read).")

SPECS["wire_link"] = dict(
    title="wire_path link (turns, climbs, long runs)", kind="comb", inputs=["A"], outputs=["Y"], fn=lambda A: A, weak_input_ok=True,
    description="A dust run built with simulation.builder.wire_path from (0,0,0) to (dx,dy,dz).",
    variants=[{"dx": 6, "dy": 0, "dz": 0}, {"dx": 4, "dy": 0, "dz": 3}, {"dx": -4, "dy": 0, "dz": -3}, {"dx": 3, "dy": 3, "dz": 0},
              {"dx": 4, "dy": -2, "dz": 3}, {"dx": 3, "dy": 2, "dz": -4}, {"dx": 0, "dy": 0, "dz": 6}, {"dx": 24, "dy": 0, "dz": 0}, {"dx": 12, "dy": 4, "dz": 12}],
    notes="Y is the end of the run, not restored to 15; a repeater is inserted after every 14 dust.")

comb("crossover", "Two-signal crossover", "XE = XW and ZS = ZN, independently (the Z line hops over the X line at y=3).",
     ["XW", "ZN"], ["XE", "ZS"], lambda XW, ZN: (XW, ZN), notes="Both lines tolerate weak input; Z leaves at full strength (repeater), X keeps its strength minus 4.")


# --------------------------------------------------------------------------- provenance (everything not listed is original to this repo)
RV = {"origin": "adapted from formalized-si/redstone-verify (MIT): layout, lever/lamp interface replaced by dust ports and re-verified",
      "upstream": "https://github.com/formalized-si/redstone-verify", "upstream_commit": "0fbd001b3324ac6e4cc7744b9027413912a32fa0",
      "licence": "MIT (keep the upstream notice when redistributing)", "redistributable": True}
ENV_TASK = {"origin": "layout of this repo's env task (env/tasks/author.py), dust ports added", "licence": "repo licence", "redistributable": True}
SPECS["half_adder"]["provenance"] = {**RV, "upstream_layout": "circuits/half_adder/cand_comparators.rsl"}
SPECS["xor2"]["provenance"] = {**RV, "upstream_layout": "circuits/half_adder/cand_comparators.rsl (without the carry stage)"}
SPECS["full_adder"]["provenance"] = {**RV, "upstream_layout": "circuits/full_adder/cand_comparators.rsl"}
SPECS["mux2"]["provenance"] = {**RV, "upstream_layout": "circuits/mux2/cand_comparators.rsl"}
for _n in ("sr_latch", "edge_detector", "t_flipflop"):
    SPECS[_n]["provenance"] = ENV_TASK
