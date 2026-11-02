#!/usr/bin/env python3
"""Differential check: converted redstone-verify layouts, simulated by Nucleation, against their Verilog spec.

    .venv/bin/python scripts/corpus_check_rsl.py [circuit ...]

For every converted layout (data/corpus/converted/redstone-verify, see corpus_convert.py) whose spec.v consists of
single-bit `assign`/`wire` expressions, brute-force all input combinations in Nucleation's TickSimulation (levers
toggled with use_block, run to quiescence, lamps read) and compare with the Verilog. ACCEPT layouts are certified by the
source project against the spec (MCHPRS); REJECT mutants should differ somewhere. Prints a table, writes
data/corpus/rsl_check.json.
"""
import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))
CONV = ROOT / "data" / "corpus" / "converted" / "redstone-verify"


def parse_spec(text):
    """-> (inputs, outputs, {name: python expr}) for single-bit assign/wire specs, else None."""
    text = re.sub(r"//.*", "", text)
    m = re.search(r"module\s+\w+\s*\((.*?)\);", text, re.S)
    if not m or "[" in text:
        return None
    ins = re.findall(r"input\s+(\w+)", m.group(1))
    outs = re.findall(r"output\s+(\w+)", m.group(1))
    exprs = {}
    for lhs, rhs in re.findall(r"(?:assign|wire)\s+(\w+)\s*=\s*([^;]+);", text):
        rhs = re.sub(r"~\s*(\w+)", r"NOT(\1)", rhs)
        rhs = re.sub(r"~\s*\(", "NOT(", rhs)
        t = re.fullmatch(r"\s*(\w+)\s*\?\s*(\w+)\s*:\s*(\w+)\s*", rhs)
        if t:
            rhs = f"({t.group(2)} if {t.group(1)} else {t.group(3)})"
        if re.search(r"[{}+\-*<>=!]|\d'", rhs) or "?" in rhs:
            return None
        exprs[lhs] = rhs
    if not outs or not all(o in exprs for o in outs):
        return None
    return ins, outs, exprs


def spec_eval(exprs, outs, env):
    env = dict(env, NOT=lambda v: 1 - v)
    done = False
    for _ in range(len(exprs) + 1):  # wires may be defined in any order
        for k, e in exprs.items():
            if k not in env:
                try:
                    env[k] = eval(e, {"__builtins__": {}}, env) & 1
                except NameError:
                    pass
    return {o: env[o] for o in outs}


def simulate(nuc, path, ports, ins, outs):
    """-> {input tuple: output dict} from the simulator."""
    res = {}
    for combo in itertools.product((0, 1), repeat=len(ins)):
        s = nuc.Schematic.load_from_file(str(path))
        sim = nuc.TickSimulation.from_schematic(s, nuc.TickSettleMode.Placement, 0, 0, 0, "")
        sim.run_until_quiescent(300)
        for name, v in zip(ins, combo):
            if v:
                sim.use_block(*ports["in"][name])
        sim.run_until_quiescent(600)
        res[combo] = {o: int("lit=true" in sim.get_block(*ports["out"][o])) for o in outs}
    return res


def main():
    import nucleation as nuc
    want = set(sys.argv[1:])
    rows, out = [], {}
    for meta_path in sorted(CONV.glob("*/*.meta.json")):
        meta = json.loads(meta_path.read_text())
        if want and meta["circuit"] not in want:
            continue
        spec = parse_spec(meta["spec_v"] or "")
        if not spec or len(spec[0]) > 8:
            continue
        ins, outs, exprs = spec
        path = meta_path.with_suffix("").with_suffix(".litematic") if meta_path.name.endswith(".litematic.meta.json") else None
        path = Path(str(meta_path)[: -len(".meta.json")])
        try:
            got = simulate(nuc, path, meta["ports"], ins, outs)
        except Exception as e:
            rows.append((meta["title"], meta["label"], "error", str(e)[:60]))
            continue
        bad = [c for c in got if got[c] != spec_eval(exprs, outs, dict(zip(ins, c)))]
        verdict = "match" if not bad else f"differs on {len(bad)}/{len(got)}"
        rows.append((meta["title"], meta["label"], verdict, ""))
        out[meta["title"]] = dict(label=meta["label"], match=not bad, bad_inputs=len(bad), combos=len(got))
    for r in rows:
        print(f"{r[0]:45s} {r[1]:7s} {r[2]} {r[3]}")
    acc = [v for v in out.values() if v["label"] == "ACCEPT"]
    rej = [v for v in out.values() if v["label"] == "REJECT"]
    print(f"\nACCEPT layouts matching spec in Nucleation: {sum(v['match'] for v in acc)}/{len(acc)}; "
          f"REJECT mutants differing from spec: {sum(not v['match'] for v in rej)}/{len(rej)}")
    (ROOT / "data" / "corpus" / "rsl_check.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
