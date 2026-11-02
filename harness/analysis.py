"""Aggregate experiment transcripts into markdown tables.

Definitions
  pass        final.full_pass (every public+hidden vector/sequence, every invariant, R1 == 1; implies R3 == 1)
  mean reward final.reward (R0-R4 mix, env/score.py)
  lift        paired difference between R=k and R=0 for the same (model, arm). The cluster is the TASK: every task contributes
              one number per condition (its mean over seeds), the 95% CI is a percentile bootstrap over tasks (2000 resamples,
              seeded). `ALL` rows pool models (task value = mean over models and seeds).
  ratio       mean(R=k) / mean(R=0), same bootstrap; "n/a" when the R=0 mean is 0.
Only runs with complete=True are used; incomplete ones (budget stop, API error, STOP file) are counted separately.
"""
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from harness.agent import final_of
from harness.llm import DEFAULT_LEDGER

BOOT_N = 2000
HEAT = [(0.99, "█"), (0.67, "▓"), (0.34, "▒"), (1e-9, "░"), (-1, "·")]


# --------------------------------------------------------------------------- loading
def load_finals(exp_dir) -> dict:
    """-> {"complete": [final...], "incomplete": [final...]} from <exp_dir>/runs/*.jsonl."""
    out = {"complete": [], "incomplete": []}
    for p in sorted((Path(exp_dir) / "runs").glob("*.jsonl")):
        f = final_of(p)
        if f is None:
            out["incomplete"].append({"key": p.stem, "ended_by": "unfinished"})
        elif f.get("complete"):
            out["complete"].append(f)
        else:
            out["incomplete"].append(f)
    return out


def ledger_spent(exp_id: str, ledger=DEFAULT_LEDGER) -> Optional[float]:
    p = Path(ledger)
    if not p.exists():
        return None
    from harness.llm import Budget
    return Budget(1e9, exp_id, p).spent()


# --------------------------------------------------------------------------- statistics
def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def percentile(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    i = q * (len(xs) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def bootstrap_ci(values, stat=mean, n=BOOT_N, seed=0, alpha=0.05):
    """Percentile bootstrap CI of stat(values); returns (lo, hi). NaN statistics are dropped."""
    values = list(values)
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    k = len(values)
    stats = []
    for _ in range(n):
        s = stat([values[rng.randrange(k)] for _ in range(k)])
        if not (isinstance(s, float) and math.isnan(s)):
            stats.append(s)
    if not stats:
        return (float("nan"), float("nan"))
    return (percentile(stats, alpha / 2), percentile(stats, 1 - alpha / 2))


def paired_lift(base: Dict[str, float], other: Dict[str, float], n=BOOT_N, seed=0):
    """base/other: {cluster: value}. -> dict(n, base, other, diff, diff_ci, ratio, ratio_ci) over shared clusters."""
    keys = sorted(set(base) & set(other))
    if not keys:
        return None
    a, b = [base[k] for k in keys], [other[k] for k in keys]
    diffs = [y - x for x, y in zip(a, b)]
    pairs = list(zip(a, b))

    def ratio(ps):
        ma, mb = mean(p[0] for p in ps), mean(p[1] for p in ps)
        return mb / ma if ma > 0 else float("nan")

    return {"n": len(keys), "base": mean(a), "other": mean(b), "diff": mean(diffs),
            "diff_ci": bootstrap_ci(diffs, mean, n, seed), "ratio": ratio(pairs), "ratio_ci": bootstrap_ci(pairs, ratio, n, seed)}


# --------------------------------------------------------------------------- aggregation helpers
def cond(f):
    return (f["model"], f["arm"], f["R"])


def group(finals, keyfn):
    g = defaultdict(list)
    for f in finals:
        g[keyfn(f)].append(f)
    return g


def per_task(finals, metric) -> Dict[str, float]:
    """task -> mean metric over the given finals (seeds/models pooled)."""
    g = group(finals, lambda f: f["task"])
    return {t: mean(metric(f) for f in fs) for t, fs in g.items()}


PASS = lambda f: 1.0 if f["full_pass"] else 0.0
REWARD = lambda f: f["reward"]


def fmt(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def fmt_ci(ci, nd=2):
    return "n/a" if ci is None or any(isinstance(v, float) and math.isnan(v) for v in ci) else f"[{ci[0]:+.{nd}f}, {ci[1]:+.{nd}f}]"


def table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def conditions(finals):
    return sorted({cond(f) for f in finals}, key=lambda c: (c[0], c[1], c[2]))


# --------------------------------------------------------------------------- sections
def pass_table(finals) -> str:
    rows = []
    for c in conditions(finals):
        fs = [f for f in finals if cond(f) == c]
        k = sum(f["full_pass"] for f in fs)
        lo, hi = wilson(k, len(fs))
        rows.append([c[0], c[1], c[2], len(fs), f"{k}/{len(fs)}", f"{k / len(fs):.2f}", f"[{lo:.2f}, {hi:.2f}]",
                     fmt(mean(f["reward"] for f in fs), 3), fmt(mean(f["r3"] for f in fs), 3)])
    return table(["model", "arm", "R", "runs", "pass", "pass rate", "Wilson 95%", "mean reward", "mean R3"], rows)


def lift_rows(finals, ks=(3, 8), metric=PASS, n=BOOT_N):
    """-> list of dict(model, arm, k, + paired_lift fields) for each (model|ALL, arm)."""
    out = []
    models = sorted({f["model"] for f in finals})
    arms = sorted({f["arm"] for f in finals})
    for model in models + ["ALL"]:
        for arm in arms:
            sub = [f for f in finals if f["arm"] == arm and (model == "ALL" or f["model"] == model)]
            if not sub:
                continue
            base = per_task([f for f in sub if f["R"] == 0], metric)
            for k in ks:
                oth = per_task([f for f in sub if f["R"] == k], metric)
                pl = paired_lift(base, oth, n)
                if pl:
                    out.append({"model": model, "arm": arm, "k": k, **pl})
    return out


def lift_table(finals, metric=PASS, ks=(3, 8)) -> str:
    rows = []
    for r in lift_rows(finals, ks, metric):
        ratio = "n/a" if math.isnan(r["ratio"]) else f"{r['ratio']:.2f}x {fmt_ci(r['ratio_ci'])}".replace("+", "")
        rows.append([r["model"], r["arm"], f"R={r['k']} vs R=0", r["n"], fmt(r["base"]), fmt(r["other"]),
                     f"{r['diff']:+.2f}", fmt_ci(r["diff_ci"]), ratio])
    return table(["model", "arm", "comparison", "tasks", "R=0", "R=k", "lift (abs)", "95% CI", "ratio"], rows)


def effort_table(finals) -> str:
    rows = []
    for c in conditions(finals):
        fs = [f for f in finals if cond(f) == c]
        solved = [f for f in fs if f["full_pass"]]
        cost, toks = sum(f["cost_usd"] for f in fs), sum(f["usage"]["prompt_tokens"] + f["usage"]["completion_tokens"] for f in fs)
        rows.append([c[0], c[1], c[2], len(fs), fmt(mean(f["rounds_used"] for f in fs), 2), fmt(mean(f["turns"] for f in fs), 1),
                     f"{mean(f['usage']['prompt_tokens'] for f in fs) / 1000:.1f}k", f"{mean(f['usage']['completion_tokens'] for f in fs) / 1000:.1f}k",
                     f"${cost / len(fs):.4f}", f"${cost / len(solved):.4f}" if solved else "n/a",
                     f"{toks / len(solved) / 1000:.0f}k" if solved else "n/a",
                     f"{sum(f['submitted'] for f in fs)}/{len(fs)}"])
    return table(["model", "arm", "R", "runs", "rounds used", "turns", "prompt tok/run", "output tok/run", "$/run", "$ per solved",
                  "tokens per solved", "submitted"], rows)


def heat(x):
    return next(sym for lim, sym in HEAT if x >= lim)


def heatmap(finals) -> str:
    cols = conditions(finals)
    tasks = sorted({f["task"] for f in finals})
    rows = []
    for t in tasks:
        row = [t]
        for c in cols:
            fs = [f for f in finals if f["task"] == t and cond(f) == c]
            row.append("" if not fs else f"{heat(mean(PASS(f) for f in fs))} {mean(PASS(f) for f in fs):.2f}")
        rows.append(row)
    tot = ["**pass rate**"]
    for c in cols:
        fs = [f for f in finals if cond(f) == c]
        tot.append(f"{mean(PASS(f) for f in fs):.2f}" if fs else "")
    rows.append(tot)
    heads = ["task"] + [f"{m}<br>{a} R={r}" for m, a, r in cols]
    return table(heads, rows) + "\n\nLegend: █ >= 0.99, ▓ >= 0.67, ▒ >= 0.34, ░ > 0, · 0 (pass rate over seeds)."


# -- failure taxonomy
R0_CAUSES = [
    ("outside the build box", r"outside the build box"),
    ("wrote a fixed cell", r"is a fixed cell"),
    ("reserved area", r"reserved area"),
    ("unknown block / property / bad value", r"unknown block|unknown property|invalid|not a valid|no such|bad value|unknown value|Unknown"),
    ("not simulatable by Nucleation", r"cannot be simulated|refused to load"),
    ("block budget exceeded", r"exceeds the budget"),
    ("malformed block list", r"malformed"),
]


def r0_cause(errors: List[str]) -> str:
    text = " ".join(errors)
    for name, pat in R0_CAUSES:
        if re.search(pat, text):
            return name
    return "other: " + (errors[0][:60] if errors else "unspecified")


def classify(f) -> str:
    """Exclusive failure class by gate order: pass | R0:<cause> | R1:<invariant> | R2 | R3:<kind>."""
    if f["full_pass"]:
        return "pass"
    d = f.get("details") or {}
    if f["r0"] < 1:
        return "R0: " + r0_cause(d.get("r0_errors") or [])
    if f["r1"] < 1:
        names = sorted({re.split(r"[: ]", v, maxsplit=1)[0] for v in d.get("r1_violations") or []}) or ["engine_placement"]
        return "R1: " + "+".join(names)
    if f["r2"] < 1:
        return "R2: input->output path missing"
    if f["blocks"] == 0:
        return "R3: empty build (never built anything)"
    if f["r3"] <= 0:
        return "R3: behaviour wrong (output constant / inverted)"
    if f["r3"] >= 0.99:
        inv = d.get("invariants_failed") or []
        return "R3: all vectors ok but invariant " + "+".join(sorted(set(inv))) if inv else "R3: near-pass (>= 0.99)"
    return "R3: partial (0 < R3 < 0.99)" if f["r3"] < 0.5 else "R3: mostly right (0.5 <= R3 < 0.99)"


def taxonomy_table(finals) -> str:
    cols = sorted({(f["arm"], f["R"]) for f in finals})
    counts = {c: Counter(classify(f) for f in finals if (f["arm"], f["R"]) == c) for c in cols}
    totals = {c: sum(counts[c].values()) for c in cols}
    classes = sorted({k for c in cols for k in counts[c]}, key=lambda k: (k != "pass", k))
    rows = [[k] + [f"{counts[c][k]} ({counts[c][k] / totals[c]:.0%})" if counts[c][k] else "" for c in cols] for k in classes]
    rows.append(["**runs**"] + [totals[c] for c in cols])
    return table(["class (gate order R0>R1>R2>R3)"] + [f"{a} R={r}" for a, r in cols], rows)


def builder_compile_table(finals) -> Optional[str]:
    fs = [f for f in finals if f["arm"] == "builder"]
    if not fs:
        return None
    kinds, attempts, failed = Counter(), 0, 0
    eps_with_fail = recovered = 0
    for f in fs:
        att = f.get("build_attempts") or []
        attempts += len(att)
        bad = [a for a in att if not a["ok"]]
        failed += len(bad)
        for a in bad:
            for k in set(a["kinds"]) or {"?"}:
                kinds[k] += 1
        if bad:
            eps_with_fail += 1
            recovered += int(att[-1]["ok"])
    if not attempts:
        return "builder arm: no build() attempts recorded."
    rows = [[k, v, f"{v / failed:.0%}"] for k, v in kinds.most_common()]
    head = (f"{attempts} build() attempts in {len(fs)} builder episodes: {failed} failed ({failed / attempts:.0%}); "
            f"{eps_with_fail} episodes had at least one failed attempt, {recovered} of them ended with a loaded build.\n\n")
    return head + table(["error kind (attempts containing it)", "attempts", "share of failed"], rows)


def headline(finals) -> str:
    """Per (model|ALL, arm): pass rate at each R, and the audit's continuation criterion
    (R=8 pass >= 2x R=0 pass and > 60% on T1 tasks) when R=8 exists."""
    out = []
    for model in sorted({f["model"] for f in finals}) + ["ALL"]:
        for arm in sorted({f["arm"] for f in finals}):
            sub = [f for f in finals if f["arm"] == arm and (model == "ALL" or f["model"] == model)]
            Rs = sorted({f["R"] for f in sub})
            if len(Rs) < 2:
                continue
            rates = ", ".join(f"R={r}: {mean(PASS(f) for f in sub if f['R'] == r):.0%}" for r in Rs)
            line = f"- **{model} / {arm}**: pass rate {rates}"
            if 8 in Rs and 0 in Rs:
                p0 = mean(PASS(f) for f in sub if f["R"] == 0)
                p8 = mean(PASS(f) for f in sub if f["R"] == 8)
                t1 = [f for f in sub if f["R"] == 8 and f["tier"] == "T1"]
                p8t1 = mean(PASS(f) for f in t1) if t1 else float("nan")
                ok = (p0 == 0 and p8 > 0 or (p0 > 0 and p8 >= 2 * p0)) and (not math.isnan(p8t1) and p8t1 > 0.6)
                line += f" -> criterion (R=8 >= 2x R=0 and > 60% on T1: {fmt(p8t1)}): **{'MET' if ok else 'not met'}**"
            out.append(line)
    return "\n".join(out) if out else "(need at least two R values to compare)"


CAVEATS = """\
- The scorer is the Nucleation simulator (inner loop), not the real game. Tasks are author-written and validated only against this
  simulator and (reference solutions) the vanilla 26.2 oracle (docs/ORACLE.md); a model may exploit a simulator quirk.
- Hidden vectors are of the same kind as the public ones. `run_spec` shows public vectors only, `submit` runs everything.
- R=0 means no simulator feedback; static validation (set_blocks rejections, builder compile errors, static `view`) is available in
  every condition. Port positions are fixed per task (the env has no port randomisation); `seed` changes the sampling seed sent to
  the provider and the order of public vectors in run_spec. Providers treat `seed` as best effort, so reruns are not bit-identical.
- Tasks are the clusters of the bootstrap; with few tasks the CIs are wide. A partial run (budget stop) covers the first tasks of the
  grid only (grid order seed > task > model > arm > R)."""


def build_report(exp_dir, title: Optional[str] = None, ledger=DEFAULT_LEDGER) -> str:
    exp_dir = Path(exp_dir)
    ld = load_finals(exp_dir)
    fin, inc = ld["complete"], ld["incomplete"]
    cfgp = exp_dir / "config.json"
    cfg = json.loads(cfgp.read_text()) if cfgp.exists() else {}
    L = [f"# Experiment {exp_dir.name}" + (f": {title}" if title else ""), ""]
    if not fin:
        L.append(f"No completed runs in `{exp_dir}/runs` ({len(inc)} incomplete).")
        return "\n".join(L) + "\n"
    spent = ledger_spent(exp_dir.name, ledger)
    L += ["## Setup", "",
          f"- runs: {len(fin)} complete, {len(inc)} incomplete" + (f" ({dict(Counter(f.get('ended_by') for f in inc))})" if inc else ""),
          f"- tasks: {len({f['task'] for f in fin})} ({', '.join(sorted({f['tier'] for f in fin}))}); models: {', '.join(sorted({f['model'] for f in fin}))}; "
          f"arms: {', '.join(sorted({f['arm'] for f in fin}))}; R: {sorted({f['R'] for f in fin})}; seeds: {sorted({f['seed'] for f in fin})}",
          f"- spend: ${sum(f['cost_usd'] for f in fin):.4f} in complete runs"
          + (f", ${spent:.4f} in the ledger (incl. incomplete runs and unsettled reservations)" if spent is not None else "")
          + (f"; cap ${cfg['cap_usd']:.2f}" if cfg.get("cap_usd") else ""),
          f"- ended by: {dict(Counter(f['ended_by'] for f in fin))}", ""]
    L += ["## Headline", "", headline(fin), ""]
    L += ["## 1. Pass rate and mean reward by model x arm x R", "", pass_table(fin), ""]
    L += ["## 2. Iteration lift (paired by task, bootstrap 95% CI over tasks)", "", "### Pass rate", "", lift_table(fin, PASS), "",
          "### Mean reward", "", lift_table(fin, REWARD), ""]
    L += ["## 3. Rounds, tokens and cost", "", effort_table(fin), ""]
    L += ["## 4. Per-task pass rate", "", heatmap(fin), ""]
    L += ["## 5. Failure taxonomy", "", taxonomy_table(fin), ""]
    bt = builder_compile_table(fin)
    if bt:
        L += ["### Builder arm: compile attempts", "", bt, ""]
    L += ["## 6. Caveats", "", CAVEATS, ""]
    return "\n".join(L)
