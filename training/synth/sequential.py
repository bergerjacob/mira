"""Sequential task families by parameter sweep (lib parts + wire_path; LLM-free):

  delay_<T>_<v>    lever -> delay_line(T ticks) -> lamp; T = 2..40, layout variant v (distances / offsets)
  offdelay_<D>_<v> lever -> or2(A, delay_line(A, D)) -> lamp: the lamp follows the lever on, and stays lit about D ticks after it is switched off
  counter<n>[_down] n-bit ripple counter of T flip-flops (env/tasks/author_t3.t3_counter), n = 1..4
  shift<n>         n-place shift register of D flip-flops (env/tasks/author_t4.t4_shift), n = 2..10

Timing windows of the delay families are MEASURED on the reference in Nucleation (rise/fall tick after the lever click) and the
spec tolerates +-2 ticks around them; the task text states the measured numbers. Counters / shift registers keep the specs of
their T3/T4 authoring functions (python model streams / timed sequences).
"""
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from training.synth import common as C  # noqa: E402

TOL = 2


def _plan(tid, title, size, desc):
    from env.tasks.author_t4 import Plan4
    p = Plan4(tid, title, size, desc)
    p.tier = "SYN"
    return p


def delay_plan(ticks, v):
    rng = random.Random(f"delay:{ticks}:{v}")
    n = max(1, -(-ticks // 8))
    a, c, zl, zc, zy = rng.randint(1, 4), rng.randint(2, 6), rng.randint(1, 3), rng.randint(1, 4), rng.randint(1, 4)
    xc = 1 + a
    lamp = (xc + n + 1 + c, 1, zy)
    size = (lamp[0] + 2, 4, max(zl, zc, zy) + 3)
    p = _plan(f"sq_delay_{ticks:02d}_{v}", f"delay line ({ticks} ticks)", size, "")
    p.dev_lever("A", (0, 1, zl), "input lever")
    p.dev_lamp("Y", lamp, "delayed output")
    body = f"""
def build(b):
    d = b.place(delay_line, at=({xc}, 0, {zc}), floor=False, ticks={ticks}, name="dl")
    route(b, DEV["A"], d["A"], 15)
    route(b, d["Y"], DEV["Y"], 15)
"""
    p.program(["delay_line"], body)
    return p, f"The lamp Y must repeat the lever A with a delay of about {ticks} game ticks (a repeater adds 2 ticks per delay step)."


def offdelay_plan(d, v):
    rng = random.Random(f"off:{d}:{v}")
    n = max(1, -(-d // 8))
    zo = rng.randint(1, 3)
    xo = n + 6 + rng.randint(0, 3)
    lamp = (xo + 4 + 1 + rng.randint(1, 4), 1, zo + 1)
    size = (lamp[0] + 2, 4, zo + 7)
    p = _plan(f"sq_offdelay_{d:02d}_{v}", f"off-delay timer ({d} ticks)", size, "")
    p.dev_lever("A", (0, 1, zo), "input lever")
    p.dev_lamp("Y", lamp, "output")
    body = f"""
def build(b):
    g = b.place(or2, at=({xo}, 0, {zo}), floor=False, name="or")
    d = b.place(delay_line, at=(2, 0, {zo + 3}), floor=False, ticks={d}, name="dl")
    route(b, DEV["A"], g["A"], 15, taps=[(2, 1, {zo})])          # direct path
    route(b, (2, 1, {zo}), d["A"], 15, zfirst=True)              # branch into the delay line
    route(b, d["Y"], g["B"], 15)                                 # delayed copy into the second OR input
    route(b, g["Y"], DEV["Y"], 15)
"""
    p.program(["delay_line", "or2"], body)
    return p, f"The lamp Y must light when the lever A is switched on and stay lit for about {d} more ticks after A is switched off (an off-delay timer)."


def measure(task, port="Y", inp="A", hold=120):
    """(rise, fall): ticks from switching the input on until the output reads 1, and from switching it off (after `hold`) until it reads 0."""
    from env.runner import Driver, prepare, read_port
    from env.sim import Sim, compile_cells
    c = task.contract
    pr = prepare(c, task.solution)
    sc, origin, _ = compile_cells(pr.world, pr.nbt)
    sim = Sim(sc, origin)
    sim.settle(c.budgets.rest)
    drv, ports = Driver(sim, c), {p.name: p for p in c.ports}
    out = []
    for val in (1, 0):
        drv.set(ports[inp], val)
        t = 0
        while read_port(sim, ports[port]) != val and t < hold:
            sim.step()
            t += 1
        out.append(t)
        sim.step(hold - t)
    return tuple(out)


def timed_spec(p, rise, fall, desc):
    """Sequences around the measured rise/fall ticks (+-TOL); a public and two hidden ones (different hold times)."""
    p.desc = (desc + f" Measured on the reference: the lamp turns on {rise} ticks after A is switched on and off {fall} ticks after A is "
              f"switched off; your build must match both within {TOL} ticks. Ticks: 20 per second.")
    for name, t_on, hold, public in (("on_off", 10, max(60, fall + rise + 20), True), ("late", 25, max(80, fall + rise + 40), False),
                                     ("twice", 5, max(70, fall + rise + 30), False)):
        t_off = t_on + hold
        t2 = t_off + fall + 30 if name == "twice" else None
        dur = (t2 + hold if t2 else t_off) + fall + 40
        quiet_end = t2 + rise - TOL - 1 if t2 else dur
        exp = [("Y", 0, 0, t_on + rise - TOL - 1), ("Y", 1, t_on + rise + TOL, t_off), ("Y", 1, t_off, t_off + max(0, fall - TOL - 1)),
               ("Y", 0, t_off + fall + TOL, quiet_end)]
        ev = [(t_on, {"A": 1}), (t_off, {"A": 0})]
        if t2:
            ev += [(t2, {"A": 1}), (t2 + hold, {"A": 0})]
            exp += [("Y", 1, t2 + rise + TOL, t2 + hold), ("Y", 0, t2 + hold + fall + TOL, dur)]
        exp = [e for e in exp if e[2] <= e[3]]
        p.seq(name, ev, exp, dur, public)
    p.depends = {"Y": ["A"]}
    p.budgets.rest = 120
    p.budgets.max_blocks = 400


def counter_plan(bits, up):
    from env.tasks.author_t3 import t3_counter
    p = t3_counter(bits, up)
    p.id = f"sq_counter{bits}" + ("" if up else "_down")
    p.tier = "SYN"
    # the T3 body writes wire_path supports onto the fixed y=0 floor (author_t3 strips them from the solution, but the harness'
    # build tool refuses a program that writes a fixed cell): floor_exists keeps the program loadable as it is
    p.body = p.body.replace("wire_path(b, a, z, repeat_every=every)", "wire_path(b, a, z, repeat_every=every, floor_exists=True)")
    return p


def shift_plan(n):
    from env.tasks.author_t4 import t4_shift
    p = t4_shift(n)
    p.id = f"sq_shift{n:02d}"
    p.tier = "SYN"
    return p


def jobs(mutants=8):
    out = [("delay", t, v, mutants) for t in range(2, 42, 2) for v in range(3)]
    out += [("offdelay", d, v, mutants) for d in (4, 8, 12, 16, 20, 24, 32, 40) for v in range(3)]
    out += [("counter", b, up, mutants) for b in (1, 2, 3, 4) for up in (True, False)]
    out += [("shift", n, 0, mutants) for n in range(2, 11)]
    return out


def attempt(args):
    from env import library as L  # noqa: F401  (registers the T3 reference models used by the counters)
    kind, a, b, mutants = args
    rec = {"family": "sequential", "bucket": kind, "param": a, "variant": b, "accepted": False}
    with C.Timer() as t:
        try:
            if kind in ("delay", "offdelay"):
                p, desc = delay_plan(a, b) if kind == "delay" else offdelay_plan(a, b)
                p.desc = desc
                p.seq("probe", [(0, {"A": 1})], [("Y", 1, 0, 10, "any")], 20)       # placeholder so build() validates; replaced below
                task = p.build()
                rise, fall = measure(task)
                if rise >= 120 or fall >= 120:
                    raise ValueError(f"layout variant does not work: the lamp never follows the lever (rise {rise}, fall {fall})")
                p.behaviour.sequences = []
                timed_spec(p, rise, fall, desc)
                rec.update(rise=rise, fall=fall)
            elif kind == "counter":
                p = counter_plan(a, b)
            else:
                p = shift_plan(a)
            task = p.build()
            from env import library as L
            if not C.program_matches(task, L.program(p.uses, p.body)):
                raise ValueError("reference program does not load to the task solution through the harness build rules")
            rep = C.gate(task, mutants)
            rec.update(id=task.id, blocks=len(task.solution), kill=f"{rep.get('killed', 0)}/{rep.get('mutants', 0)}",
                       accepted=rep["accepted"], reason="; ".join(rep["problems"]))
            if rep["accepted"]:
                from env import library as L
                diff = "easy" if kind in ("delay", "offdelay") or (kind == "counter" and a <= 2) else "medium" if a <= 5 else "hard"
                C.save(task, L.program(p.uses, p.body), {"family": "sequential", "source": f"synth/sequential/{kind}", "difficulty": diff,
                                                         "param": a, "variant": b, "n_blocks": len(task.solution), "kill": rec["kill"],
                                                         "plan": f"Plan: {p.title} from library parts ({', '.join(p.uses)}), joined with wire_path.",
                                                         **({"rise": rec["rise"], "fall": rec["fall"]} if "rise" in rec else {})}, "sequential")
        except Exception as e:
            rec["reason"] = f"{type(e).__name__}: {str(e)[:300]}"
    rec["seconds"] = t.s
    return rec
