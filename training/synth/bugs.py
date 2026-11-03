"""Program-level bugs for synthetic netlist programs (training/synth/netlist.py layout) + a templated diagnosis per bug.

A bug is a text edit of the reference program that keeps it compiling; `verify_bug` keeps only bugs whose build fails the
public spec (so a run_spec shows the failure) - the fix is always the original program. Kinds:
  missing_tap    a `tap(...)` line is deleted: one gate input is never driven
  missing_out    an `out(...)` line is deleted: a gate output never reaches its bus
  wrong_signal   a `tap(b, s, ...)` takes another signal's bus s' (wrong net)
  short_bus      an output bus stops short of its lamp
  wrong_gate     `b.place(or2, ...)` <-> `b.place(and2, ...)` / nor2 -> or2 (wrong logic; geometry may change as well)
  missing_net    a `b.route(src, [sinks], name=...)` call of a router layout is deleted
  missing_route  a `route(b, ...)` call inside build(b) is deleted (any library program: sequential families, T3/T4 references)
  wrong_ticks    `ticks=T` of a delay_line changed by 4..8 ticks
  compile_typo   a misspelled builder call (compile error, not a spec failure)
"""
import random
import re

TAP = re.compile(r"^    tap\(b, (\d+), (-?\d+), (\([^)]*\))\)   # (\S+) -> gate input$")
OUT = re.compile(r"^    out\(b, (\([^)]*\)), (-?\d+), (\d+)\)   # (\S+) -> its bus$")
BUS = re.compile(r"^    bus\(b, (\d+), (-?\d+), (-?\d+), (\[[^\]]*\]), (True|False)\)   # (\S+)$")
ROUTE = re.compile(r"^\s+route\(b, .*\)\s*(#.*)?$")
BROUTE = re.compile(r"^\s+b\.route\((.*), name=\"(\w+)\".*\)\s*(#.*)?$")
TICKS = re.compile(r"ticks=(\d+)")
PLACE = re.compile(r"^    (g\d+) = b\.place\((and2|or2|nor2|not_gate), at=")
GATE_SWAP = {"or2": "and2", "and2": "or2", "nor2": "or2"}


def candidates(program):
    """All single-edit bugs of a netlist program: [{kind, line, old, new, text, why, fix}]."""
    lines = program.split("\n")
    buses = {}
    for ln in lines:
        m = BUS.match(ln)
        if m:
            buses[int(m.group(1))] = (int(m.group(2)), int(m.group(3)), m.group(6))
    out = []
    body = next((i for i, ln in enumerate(lines) if ln.startswith("def build(b)")), len(lines))

    def edit(i, new, kind, why, fix):
        text = "\n".join(lines[:i] + ([new] if new is not None else []) + lines[i + 1:])
        out.append({"kind": kind, "line": i + 1, "old": lines[i].strip(), "new": new.strip() if new else None, "text": text, "why": why, "fix": fix})

    for i, ln in enumerate(lines):
        if (m := TAP.match(ln)):
            s, x, port, sig = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
            edit(i, None, "missing_tap", f"the gate input at {port} is never driven: the branch of {sig} into it is missing",
                 f"add back `tap(b, {s}, {x}, {port})` so {sig} reaches that input")
            for s2, (x0, x1, sig2) in sorted(buses.items()):
                if s2 != s and x0 < x <= x1:
                    edit(i, ln.replace(f"tap(b, {s}, {x},", f"tap(b, {s2}, {x},").replace(f"# {sig} ->", f"# {sig2} ->"), "wrong_signal",
                         f"the gate input at {port} is fed from bus {s2} ({sig2}) instead of bus {s} ({sig})",
                         f"tap bus {s} ({sig}) at x={x}: `tap(b, {s}, {x}, {port})`")
        elif (m := OUT.match(ln)):
            ypos, xo, s, sig = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            edit(i, None, "missing_out", f"the output of gate {sig} at {ypos} is never routed onto its bus {s}",
                 f"add back `out(b, {ypos}, {xo}, {s})`")
        elif (m := BUS.match(ln)):
            s, x0, x1, taps, gd, sig = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5), m.group(6)
            tl = eval(taps)                                  # a literal list of ints written by netlist.layout
            last_tap = max(tl) if tl else x0
            cut = max(x1 - 2, last_tap + 1)
            if cut < x1 and x1 - last_tap >= 3:              # an output bus: runs on past its last tap to the lamp column
                edit(i, ln.replace(f"{x0}, {x1}, {taps}", f"{x0}, {cut}, {taps}"), "short_bus",
                     f"bus {s} ({sig}) ends at x={cut}, {x1 - cut} cell(s) short of its lamp at x={x1}",
                     f"run bus {s} all the way to x={x1}")
        elif i > body and (m := BROUTE.match(ln)):
            edit(i, None, "missing_net", f"net {m.group(2)} is never routed (its `b.route(...)` call is missing)",
                 f"route net {m.group(2)} again: `{ln.strip().split('   #')[0]}`")
        elif i > body and ROUTE.match(ln):
            call = ln.strip().split("#")[0].strip()
            edit(i, None, "missing_route", f"the connection `{call}` is missing, so that signal never arrives",
                 f"add back `{call}`")
        elif i > body and (m := TICKS.search(ln)) and "delay_line" in ln:
            t = int(m.group(1))
            t2 = t + 6 if t <= 20 else t - 6
            edit(i, ln.replace(f"ticks={t}", f"ticks={t2}"), "wrong_ticks", f"the delay line is set to {t2} ticks but the spec needs about {t}",
                 f"set `ticks={t}`")
        elif (m := PLACE.match(ln)) and m.group(2) in GATE_SWAP and "NAND" not in ln and i + 1 < len(lines) and "not_gate" not in lines[i + 1]:
            old, new = m.group(2), GATE_SWAP[m.group(2)]
            name = re.search(r'name="([^"]+)"', ln).group(1)
            edit(i, ln.replace(f"b.place({old},", f"b.place({new},"), "wrong_gate",
                 f"gate {name} is built from `{new}` but the spec needs `{old}` there",
                 f"place `{old}` for {name}")
    return out


def typo(program, rng):
    """A compile error: one builder call misspelled (repeater -> repeter / wire_path -> wirepath / lever -> levr)."""
    subs = [("repeater(flow=NORTH)", "repeter(flow=NORTH)"), ("wire_path(b,", "wirepath(b,"), ("lever(face=", "levr(face=")]
    rng.shuffle(subs)
    for a, b in subs:
        idx = program.find(a, program.find("# --- task"))
        if idx >= 0:
            return program[:idx] + b + program[idx + len(a):], b.split("(")[0]
    return None, None


def pick(program, rng, n=2, kinds=None):
    c = [b for b in candidates(program) if kinds is None or b["kind"] in kinds]
    by = {}
    for b in c:
        by.setdefault(b["kind"], []).append(b)
    order = sorted(by)
    rng.shuffle(order)
    out = []
    for k in order:
        out.append(rng.choice(by[k]))
    return out[:n] if n else out


def verify_bug(task, bug_text):
    """Compile the bugged program like the harness does and run the PUBLIC spec. -> (ok, why): ok iff it compiles, loads and
    the public run is not a full pass."""
    from env.runner import run
    from env.score import score
    blocks = solution_of(task, bug_text)
    if isinstance(blocks, str):
        return False, blocks
    sc = score(task.contract, run(task.contract, blocks, seed=0, public_only=True, fast=True))
    return (not sc.full_pass), f"public full_pass={sc.full_pass} r3={sc.r3:.2f}"


def solution_of(task, program):
    """Program text -> the block list the harness build tool would load (fixed-identical blocks dropped), or an error string."""
    from env import blocks as EB
    from env.runner import prepare
    from simulation import builder as BD
    res = BD.compile(program, sandbox=False, timeout=60.0)
    if res.errors:
        return f"compile: {res.errors[0]}"
    fixed = prepare(task.contract, []).fixed
    put = []
    for x, y, z, st, nbt in res.blocks:
        cst = EB.canonical(st)
        if fixed.get((x, y, z)) == cst:
            continue
        put.append([x, y, z, cst])
    pr = prepare(task.contract, put)
    return "rules: " + pr.errors[0] if pr.errors else put


def ordered(program, rng):
    """Every candidate bug, round-robin over the kinds in a seeded order (diverse first, all eventually)."""
    by = {}
    for b in candidates(program):
        by.setdefault(b["kind"], []).append(b)
    for v in by.values():
        rng.shuffle(v)
    kinds = sorted(by)
    rng.shuffle(kinds)
    out = []
    while any(by.values()):
        for k in kinds:
            if by[k]:
                out.append(by[k].pop())
    return out


def pick_random(program, seed, n=2):
    return pick(program, random.Random(seed), n)
