"""Signature, clustering, known-bug filter and confirmation of the finished minimisations of a run.

    python scripts/diff_fuzz/classify.py --run pilot                 # classify + cluster (batched re-run for the traces), writes classes.json
    python scripts/diff_fuzz/classify.py --run pilot --confirm        # also confirm class representatives (isolated JVMs) and write certificates
    python scripts/diff_fuzz/classify.py --run pilot --show           # print the classes with ASCII renderings of the representatives

Hypotheses (one line per new class) are kept by hand in data/fuzz/<run>/hypotheses.json {coarse key or build hash: text}.
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.diff_fuzz import canon, confirm, engine, minimise, signature  # noqa: E402
from scripts.diff_fuzz.common import FUZZ_DIR, Case, jdump, jload  # noqa: E402

MAX_CLASS_BLOCKS = 12


def load_finished(run: str):
    out = []
    for f in sorted((FUZZ_DIR / run / "min").glob("f_*.json")):
        d = jload(f)
        if d.get("error") or not d.get("div"):
            continue
        out.append(d)
    return out


def batched_traces(cases, jvms=3):
    shards = [list(range(i, len(cases), jvms)) for i in range(jvms) if i < len(cases)]

    def go(idx):
        return engine.diff_many([cases[i] for i in idx], idle=10)
    with ThreadPoolExecutor(len(shards)) as ex:
        outs = list(ex.map(go, shards))
    res = [None] * len(cases)
    for idx, o in zip(shards, outs):
        for i, r in zip(idx, o):
            res[i] = r
    return res


def classify(run: str, jvms: int = 3):
    fin = load_finished(run)
    cases = [Case.from_json(d["case"]) for d in fin]
    traces = batched_traces(cases, jvms)
    # torch burnout needs a long run to be visible: the minimiser shortens the run to the first divergence, so
    # the detector sees an extended replay (>= 120 ticks) of every minimised case that contains a torch
    torch_idx = [i for i, c in enumerate(cases) if any("torch" in b[3] for b in c.blocks)]
    long_runs = {}
    if torch_idx:
        ext = [cases[i].with_(ticks=max(cases[i].ticks, 120), preroll=0) for i in torch_idx]
        for i, r in zip(torch_idx, batched_traces(ext, jvms)):
            long_runs[i] = r
    # K5 is "caused by the settle mode": a known_shape case counts only if the same build/plan under settle=place agrees
    ks_idx = [i for i, c in enumerate(cases) if c.settle == "known_shape"]
    place_runs = {}
    if ks_idx:
        for i, r in zip(ks_idx, batched_traces([cases[i].with_(settle="place") for i in ks_idx], jvms)):
            place_runs[i] = r[0]
    items = []
    for k, (d, c, (div, fv, fn)) in enumerate(zip(fin, cases, traces)):
        d0 = d["div"]
        if isinstance(div, engine.EngineError) or div is None or not minimise.keeps(d0, div):
            items.append({"id": d["id"], "unstable": True, "n_blocks": len(c.blocks), "orig_label": d["orig_label"]})
            continue
        sig = signature.signature(c, div)
        known = signature.known_class(c, div, fv, fn)
        if c.settle == "known_shape":
            p_div = place_runs.get(k)
            settle_caused = isinstance(p_div, dict) is False        # settle=place agrees (None) -> the settle mode is the cause
            known = "known_shape" if settle_caused else (known if known != "known_shape" else None)
            if known is None:
                known = signature.known_class(c.with_(settle="place"), div, fv, fn)
        if known is None and k in long_runs and isinstance(long_runs[k][0], dict):
            known = signature.known_class(c.with_(ticks=max(c.ticks, 120), preroll=0), div, long_runs[k][1], long_runs[k][2])
            if known != "torch_burnout":
                known = None
        items.append({"id": d["id"], "case": c.to_json(), "div": minimise._slim(div), "sig": sig, "known": known,
                      "n_blocks": len(c.blocks), "orig_label": d["orig_label"], "batched_vanilla_hash": canon.trace_hash(fv),
                      "arm": c.meta.get("arm"), "seed": c.meta.get("seed")})
    stable = [i for i in items if not i.get("unstable")]
    for it in stable:                       # a known class is one cluster whatever the devices around it
        if it["known"]:
            it["sig"]["coarse"] = "KNOWN:" + it["known"]
    classes = {}
    for it in stable:
        k = it["sig"]["coarse"]
        cl = classes.setdefault(k, {"coarse": k, "members": [], "known": set(), "builds": set()})
        cl["members"].append(it["id"])
        cl["known"].add(it["known"])
        cl["builds"].add(it["sig"]["build"])
    out = []
    for k, cl in classes.items():
        members = [i for i in stable if i["sig"]["coarse"] == k]
        rep = min(members, key=lambda i: (i["n_blocks"], i["id"]))
        out.append({"coarse": k, "rep": rep["id"], "n_members": len(members), "n_distinct_builds": len(cl["builds"]),
                    "known": sorted(x for x in cl["known"] if x) or None,
                    "min_blocks": rep["n_blocks"], "phase": rep["sig"]["phase"], "mech": rep["sig"]["mech"],
                    "ids": rep["sig"]["ids"]})
    out.sort(key=lambda c: (c["known"] is not None, c["min_blocks"], c["coarse"]))
    jdump(FUZZ_DIR / run / "classes.json", {"items": items, "classes": out})
    return items, out


def report(run: str, show: bool = False):
    data = jload(FUZZ_DIR / run / "classes.json")
    items = {i["id"]: i for i in data["items"]}
    hyp = jload(FUZZ_DIR / run / "hypotheses.json", {})
    print(f"{len(items)} minimised cases, {sum(1 for i in items.values() if i.get('unstable'))} unstable, {len(data['classes'])} classes")
    for c in data["classes"]:
        tag = ",".join(c["known"]) if c["known"] else "NEW"
        print(f"[{tag:22s}] {c['min_blocks']:2d} blocks, {c['n_members']:3d} members/{c['n_distinct_builds']} builds  {c['coarse']}")
        if show:
            it = items[c["rep"]]
            print(minimise.render(Case.from_json(it["case"])))
            print("  first divergence:", it["div"]["phase"], it["div"]["tick"], it["div"]["cells"][:2])
            if hyp.get(c["coarse"]):
                print("  hypothesis:", hyp[c["coarse"]])


def confirm_classes(run: str, only_new: bool = True, limit: int = 60):
    data = jload(FUZZ_DIR / run / "classes.json")
    items = {i["id"]: i for i in data["items"]}
    hyp = jload(FUZZ_DIR / run / "hypotheses.json", {})
    certs_dir = FUZZ_DIR / run / "certs"
    done = 0
    for c in data["classes"]:
        if only_new and c["known"] and not (c["known"] and c["rep"] and c["n_members"] >= 1 and done < 0):
            # one representative per known class is confirmed below
            pass
        it = items[c["rep"]]
        case = Case.from_json(it["case"])
        d0 = it["div"]
        conf = confirm.confirm(case, d0, batched_hash=it.get("batched_vanilla_hash"))
        cert = confirm.certificate(case, d0, it["sig"], conf, ",".join(c["known"]) if c["known"] else None,
                                   hyp.get(c["coarse"], ""))
        jdump(certs_dir / f"{cert['id']}.json", cert)
        c["certificate"] = cert["id"]
        c["confirmed"] = conf["verdict"]["confirmed"]
        c["verdict"] = conf["verdict"]
        v = conf["verdict"]
        print(f"{cert['id']} [{c['known'] or 'NEW'}] confirmed={v['confirmed']} iso={v['isolated_reproduces']} origin2={v['origin2_reproduces']} "
              f"orient={v['orientations_reproduce']} causal={v['causal']} batch==iso={v['batched_matches_isolated']}  {c['coarse']}", flush=True)
        done += 1
        if done >= limit:
            break
    jdump(FUZZ_DIR / run / "classes.json", data)


# ---------------------------------------------------------------------------------------------- root-cause families
FAMILIES = [
    ("F1 copper bulb", "copper bulb handled differently (conducts power to neighbours / comparator reads lit vs powered)",
     lambda ids, lab: any("copper_bulb" in i for i in ids) or any("copper_bulb" in x for x in lab)),
    ("F2 rail shape", "rail auto-shaping at placement (vanilla re-shapes rails next to rails/slopes, Nucleation keeps the stated shape)",
     lambda ids, lab: any("rail" in x for x in lab)),
    ("F3 note instrument", "derived note_block.instrument is not recomputed from the block below at placement",
     lambda ids, lab: any(x.startswith("note_block[instrument") for x in lab)),
    ("F4 piston movement", "piston/moving_piston timing, what a moved block re-evaluates, wire shape/pop-off around pistons",
     lambda ids, lab: "piston" in ids or "sticky_piston" in ids or any("piston" in x for x in lab)),
    ("F5 comparator", "comparator output/powered timing or input sampling (no bulb, no piston)",
     lambda ids, lab: "comparator" in ids),
    ("F6 repeater", "repeater powered/locked timing with two inputs (no bulb, no piston, no comparator)",
     lambda ids, lab: "repeater" in ids),
    ("F7 observer", "observer pulse seen/missed (no bulb, no piston)",
     lambda ids, lab: "observer" in ids or any(x.startswith("observer[") for x in lab)),
    ("F8 other", "not yet grouped", lambda ids, lab: True),
]


def family_of(item: dict) -> str:
    ids = set(item["sig"]["ids"])
    lab = item["sig"]["mech"].split(";")
    for name, _h, pred in FAMILIES:
        if pred(ids, lab):
            return name
    return "F8 other"


def families(run: str):
    data = jload(FUZZ_DIR / run / "classes.json")
    items = [i for i in data["items"] if not i.get("unstable") and not i.get("known")]
    out = {}
    for it in items:
        f = out.setdefault(family_of(it), {"members": [], "coarse": set()})
        f["members"].append(it)
        f["coarse"].add(it["sig"]["coarse"])
    return out


def cmd_families(run: str):
    for name, f in sorted(families(run).items()):
        ms = sorted(f["members"], key=lambda i: (i["n_blocks"], i["id"]))
        print(f"{name:22s} {len(ms):4d} minimised cases, {len(f['coarse']):3d} fine classes, smallest {ms[0]['n_blocks']} blocks, {sum(1 for m in ms if m['n_blocks'] <= MAX_CLASS_BLOCKS)} <= {MAX_CLASS_BLOCKS}")


def confirm_families(run: str, workers: int = 3):
    """Confirm (isolated JVMs, 2 origins, 2 orientations, no-stimulus control) the smallest representative of every
    new family and of every known class; write certificates + data/fuzz/<run>/families.json."""
    data = jload(FUZZ_DIR / run / "classes.json")
    items = {i["id"]: i for i in data["items"] if not i.get("unstable")}
    hyp = jload(FUZZ_DIR / run / "hypotheses.json", {})
    reps = []
    for name, f in sorted(families(run).items()):
        ms = sorted(f["members"], key=lambda i: (i["n_blocks"], i["id"]))
        reps.append((name, ms[0], len(f["members"]), len(f["coarse"])))
    seen = {}
    for it in sorted(items.values(), key=lambda i: (i["n_blocks"], i["id"])):
        if it["known"] and it["known"] not in seen:
            seen[it["known"]] = it
    members = {k: sum(1 for i in items.values() if i["known"] == k) for k in seen}
    for k, it in seen.items():
        reps.append(("KNOWN " + k, it, members[k], 1))
    out = []
    for name, it, n_members, n_fine in reps:
        case = Case.from_json(it["case"])
        d0 = it["div"]
        conf = confirm.confirm(case, d0, workers=workers, batched_hash=it.get("batched_vanilla_hash"))
        cert = confirm.certificate(case, d0, it["sig"], conf, it["known"], hyp.get(name, ""))
        jdump(FUZZ_DIR / run / "certs" / f"{cert['id']}.json", cert)
        v = conf["verdict"]
        row = {"family": name, "certificate": cert["id"], "minimised_blocks": len(case.blocks), "members": n_members,
               "fine_classes": n_fine, "verdict": v, "mech": it["sig"]["mech"], "phase": it["sig"]["phase"]}
        out.append(row)
        print(f"{name:26s} {cert['id']} blocks={row['minimised_blocks']} confirmed={v['confirmed']} origin2={v['origin2_reproduces']} "
              f"orient={v['orientations_reproduce']} causal={v['causal']} batch==iso={v['batched_matches_isolated']}", flush=True)
        jdump(FUZZ_DIR / run / "families.json", out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--families", action="store_true")
    ap.add_argument("--confirm-families", action="store_true")
    a = ap.parse_args()
    if a.families:
        cmd_families(a.run)
    elif a.confirm_families:
        confirm_families(a.run)
    elif a.report or a.show:
        report(a.run, a.show)
    elif a.confirm:
        confirm_classes(a.run, limit=a.limit)
    else:
        classify(a.run)
        report(a.run)
