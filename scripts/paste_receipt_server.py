#!/usr/bin/env python3
"""Paste builds into the Fabric + Carpet 1.21.0 server the way Litematica does on a server, read every block back, write a receipt.

    python scripts/paste_receipt_server.py [--start-server] [--sessions [SUBSTR,...]] [--tasks t4_accum4,...] [--litematic F ...]
                                           [--blocks F.json ...] [--stimulate t1_clock-...|t0_and] [--json OUT]

Build sources: `--sessions` = every data/sandbox/*/session (strong-agent builds); `--tasks` = task reference solutions; for both the
HANDOVER is pasted: env's at-rest world exported with `env.runner.export_litematic` to a .litematic and loaded back with Nucleation
(the file a user would get). `--litematic` / `--blocks` paste a given file as is.

Paste (per build, in a fresh area at x = 3000 + 400 k, y = 100, z = 3000): forceload, clear the box + 2 cells of air, `tick freeze`,
`carpet fillUpdates false`, then bottom-up (y, z, x) one `setblock` per block, or one `fill` per run of identical blocks along x,
`data merge block` for block entities; at most `--rate` commands per 50 ms. Then EVERY cell of the box is read back (Carpet scarpet
`block` / `block_state`, one row per RCON command) while still frozen and compared with the source (air included); then
`tick unfreeze` + fillUpdates true, `--settle` ticks of real time, and a second read-back ("after unfreeze": does the pasted world stay
at rest on 1.21.0?). States the server rejects are listed with its error text.

`--stimulate NAME` additionally drives one pasted build with a Carpet fake player (`/player mira_bot spawn`, `look at` the lever,
`use once`) through the contract's stimulus (truth-table rows, or the longest sequence) and polls the outputs by game time.

`--start-server` starts simulation/server (as start_server.sh) and stops it at the end; otherwise RCON localhost:25575 must be up.
"""
import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from simulation.bridge import MinecraftBridge
from tools.oracle.sim import snbt_of
from tools.oracle.trace import canon_state

SERVER_DIR = ROOT / "simulation/server"
AIR = "minecraft:air"
ERRORS = ("Unknown", "does not have property", "Expected", "Invalid", "Incorrect", "not loaded", "Too many", "Cannot", "Could not")
ROW = 40            # cells per read-back command (keeps one RCON response < 4096 bytes)


# --------------------------------------------------------------------------- server
class Server:
    """Context manager: RCON bridge; starts/stops the server when `start` is set."""

    def __init__(self, start=False, port=25575):
        self.start, self.port, self.proc = start, port, None

    def up(self):
        try:
            socket.create_connection(("localhost", self.port), 1).close()
            return True
        except OSError:
            return False

    def __enter__(self):
        if self.start and not self.up():
            self.proc = subprocess.Popen(["java", "-Xmx4G", "-jar", "fabric-server-launch.jar", "nogui"], cwd=SERVER_DIR,
                                         stdout=open(SERVER_DIR / "server.log", "w"), stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
            for _ in range(180):
                if self.up():
                    break
                if self.proc.poll() is not None:
                    raise RuntimeError("server exited; see simulation/server/server.log")
                time.sleep(1)
        if not self.up():
            raise RuntimeError("RCON localhost:25575 is not up (bash start_server.sh, or --start-server)")
        self.bridge = MinecraftBridge(timeout=60)
        self.bridge.connect()
        return self

    def cmd(self, c):
        return self.bridge.run_command(c) or ""

    def __exit__(self, *exc):
        if self.proc:
            try:
                self.cmd("stop")
            except Exception:
                pass
            try:
                self.proc.wait(90)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.bridge.disconnect()


# --------------------------------------------------------------------------- builds
def handover_blocks(contract, blocks, name):
    """env's at-rest world as the .litematic a user gets: export_litematic -> file -> Nucleation load (relative coords)."""
    from env.runner import export_litematic
    from tools.oracle.gametest import load_build
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.litematic"
        export_litematic(contract, blocks, path)
        out, _dv = load_build(path)
    return out


def collect(args):
    """-> [(name, blocks (x,y,z,state,nbt) relative or absolute, contract or None, offset of the build frame)]"""
    from env.runner import prepare
    from env.task import load_task
    from tools.oracle.gametest import load_build
    items = []
    if args.sessions is not None:
        want = [w for w in args.sessions.split(",") if w]
        for d in sorted(ROOT.glob("data/sandbox/*/session")):
            if not (d / "final_blocks.json").exists() or (want and not any(w in d.parent.name for w in want)):
                continue
            st = json.loads((d / "state.json").read_text())
            task = load_task(st.get("task_ref") or st["task_id"])
            items.append((d.parent.name, json.loads((d / "final_blocks.json").read_text()), task.contract))
    for tid in (args.tasks.split(",") if args.tasks else []):
        task = load_task(tid)
        items.append((f"{tid}~reference", task.solution, task.contract))
    out = []
    for name, blocks, contract in items:
        pr = prepare(contract, blocks)
        if pr.errors:
            out.append((name, None, contract, None, "prepare errors: " + "; ".join(pr.errors[:2])))
            continue
        hb = handover_blocks(contract, blocks, "h")
        corner = tuple(min(p[i] for p in pr.world) for i in range(3))     # litematic origin = min corner of the at-rest world
        out.append((name, hb, contract, corner, ""))
    for f in args.litematic or []:
        out.append((Path(f).stem, load_build(f)[0], None, (0, 0, 0), ""))
    for f in args.blocks or []:
        out.append((Path(f).stem, json.loads(Path(f).read_text()), None, (0, 0, 0), ""))
    return out


def norm(blocks):
    res = {}
    for b in blocks:
        if isinstance(b, dict):
            b = (b["x"], b["y"], b["z"], b["state"], b.get("nbt"))
        st = canon_state(b[3])
        if st.split("[")[0] != AIR:
            res[(int(b[0]), int(b[1]), int(b[2]))] = (st, b[4] if len(b) > 4 else None)
    return res


# --------------------------------------------------------------------------- paste / read back
def boxes(lo, hi, size=32):
    for x in range(lo[0], hi[0] + 1, size):
        for y in range(lo[1], hi[1] + 1, size):
            for z in range(lo[2], hi[2] + 1, size):
                yield (x, y, z), (min(x + size - 1, hi[0]), min(y + size - 1, hi[1]), min(z + size - 1, hi[2]))


def bad(resp):
    return any(e in resp for e in ERRORS) and "Could not set the block" not in resp


def paste(srv, cells, origin, rate):
    """cells: {rel pos: (state, nbt)} -> stats. Litematica-style: frozen, no updates, bottom-up, fill for runs along x."""
    mn = tuple(min(p[i] for p in cells) for i in range(3))
    mx = tuple(max(p[i] for p in cells) for i in range(3))
    absb = lambda p: (origin[0] + p[0] - mn[0], origin[1] + p[1] - mn[1], origin[2] + p[2] - mn[2])
    lo, hi = absb(mn), absb(mx)
    clo, chi = (lo[0] - 2, lo[1] - 2, lo[2] - 2), (hi[0] + 2, hi[1] + 2, hi[2] + 2)
    for (a, b) in boxes((clo[0], 0, clo[2]), (chi[0], 0, chi[2]), 256):
        srv.cmd(f"forceload add {a[0]} {a[2]} {b[0]} {b[2]}")
    srv.cmd("tick freeze")
    srv.cmd("carpet fillUpdates false")
    for a, b in boxes(clo, chi):
        srv.cmd(f"fill {a[0]} {a[1]} {a[2]} {b[0]} {b[1]} {b[2]} air")
    t0, n_cmd, sent, rejected = time.perf_counter(), 0, 0, []
    order = sorted(cells, key=lambda p: (p[1], p[2], p[0]))
    i = 0
    while i < len(order):
        p = order[i]
        st, nbt = cells[p]
        j = i
        while (not nbt and j + 1 < len(order) and order[j + 1] == (order[j][0] + 1, p[1], p[2])
               and cells[order[j + 1]] == (st, None)):
            j += 1
        a, b = absb(p), absb(order[j])
        if j > i:
            r = srv.cmd(f"fill {a[0]} {a[1]} {a[2]} {b[0]} {b[1]} {b[2]} {st}")
        else:
            r = srv.cmd(f"setblock {a[0]} {a[1]} {a[2]} {st}")
        if bad(r):
            rejected.append({"pos": list(p), "state": st, "error": r[:160], "cells": j - i + 1})
        elif nbt:
            r = srv.cmd(f"data merge block {a[0]} {a[1]} {a[2]} {nbt if isinstance(nbt, str) else snbt_of(nbt)}")
            if bad(r):
                rejected.append({"pos": list(p), "state": st, "error": "nbt: " + r[:150], "cells": 1})
            n_cmd += 1
        n_cmd, sent, i = n_cmd + 1, sent + 1, j + 1
        if sent >= rate:
            time.sleep(0.05)
            sent = 0
    return {"origin": list(lo), "box": [list(clo), list(chi)], "commands": n_cmd, "paste_s": round(time.perf_counter() - t0, 2),
            "rejected": rejected, "abs": absb, "mn": mn}


def read_box(srv, lo, hi):
    """{abs pos: canonical state} for every cell of the box (air included) via one scarpet row per command."""
    out = {}
    for y in range(lo[1], hi[1] + 1):
        for z in range(lo[2], hi[2] + 1):
            for x0 in range(lo[0], hi[0] + 1, ROW):
                x1 = min(x0 + ROW - 1, hi[0])
                r = srv.cmd(f"script run join('|', map(range({x0}, {x1 + 1}), b = block(_, {y}, {z}); "
                            f"str(b) + '[' + join(',', map(pairs(block_state(b)), _:0 + '=' + _:1)) + ']'))")
                body = r.split("=", 1)[1].rsplit("(", 1)[0].strip() if r.startswith(" =") else ""
                vals = body.split("|")
                if len(vals) != x1 - x0 + 1:
                    raise RuntimeError(f"bad read-back row at {x0},{y},{z}: {r[:200]}")
                for k, v in enumerate(vals):
                    out[(x0 + k, y, z)] = canon_state(v.replace("[]", ""))
    return out


def compare(cells, info, got):
    """Every cell of the build box (air included) vs the read-back."""
    absb = info["abs"]
    want = {absb(p): st for p, (st, _n) in cells.items()}
    lo, hi = info["box"]
    match, bad_cells = 0, []
    for x in range(lo[0] + 2, hi[0] - 1):
        for y in range(lo[1] + 2, hi[1] - 1):
            for z in range(lo[2] + 2, hi[2] - 1):
                p = (x, y, z)
                w, g = want.get(p, AIR), got.get(p, AIR)
                if w == g:
                    match += 1
                else:
                    bad_cells.append({"pos": [x - info["origin"][0], y - info["origin"][1], z - info["origin"][2]], "want": w, "got": g})
    margin = [p for p, s in got.items() if s != AIR and not all(lo[i] + 2 <= p[i] <= hi[i] - 2 for i in range(3))]
    return {"cells": match + len(bad_cells), "matched": match, "mismatched": len(bad_cells), "examples": bad_cells[:6],
            "non_air_in_margin": len(margin), "blocks": len(want)}


def block_entities(srv, cells, info):
    be = [(p, n) for p, (_s, n) in cells.items() if n]
    ok = 0
    for p, _n in be:
        a = info["abs"](p)
        ok += "has the following block data" in srv.cmd(f"data get block {a[0]} {a[1]} {a[2]}")
    return {"expected": len(be), "present": ok}


# --------------------------------------------------------------------------- fake-player stimulus
def stimulate(srv, contract, info, world):
    """Drive the pasted build's input levers with a Carpet fake player; poll outputs per game tick (best effort over RCON:
    one poll takes a few ms, so most ticks are sampled). A click spawns/moves the bot next to the lever once, then
    `look at` + `use once`; the lever's `powered` state is read back to confirm every click. The bot stands on a pasted block
    next to the lever (`world` = read-back {abs pos: state}), so it does not fall away between clicks."""
    corner = info["corner"]
    absp = lambda p: info["abs"](tuple(p[i] - corner[i] for i in range(3)))
    gt = lambda: int(srv.cmd("time query gametime").split()[-1])
    want_state = {"lamp": "redstone_lamp[lit=true]", "torch": "redstone_torch[lit=true]", "piston": "#minecraft:pistons[extended=true]"}
    is_on = lambda port: "Test passed" in srv.cmd("execute if block {} {} {} {}".format(*absp(port.pos), want_state.get(port.kind, "air")))
    lever_on = lambda port: "Test passed" in srv.cmd("execute if block {} {} {} lever[powered=true]".format(*absp(port.pos)))
    bot, log, spot = "mira_bot", [], {}

    def place_bot(at):
        srv.cmd(f"player {bot} kill")
        time.sleep(0.3)
        srv.cmd(f"player {bot} spawn at {at[0] + 0.5} {at[1]} {at[2] + 0.5} facing 0 0 in minecraft:overworld in creative")
        for _ in range(50):     # spawning resolves the (offline) profile asynchronously
            if "Test passed" in srv.cmd(f"execute if entity {bot}"):
                return
            time.sleep(0.1)

    def click(port):
        lx, ly, lz = absp(port.pos)
        was = lever_on(port)
        air = lambda p: world.get(p, AIR) == AIR
        stand = sorted(((lx + dx, ly + dy, lz + dz) for dx in range(-3, 4) for dz in range(-3, 4) for dy in (0, 1) if dx or dz),
                       key=lambda p: (abs(p[0] - lx) + abs(p[2] - lz), p[1]))
        stand = [p for p in stand if air(p) and air((p[0], p[1] + 1, p[2])) and not air((p[0], p[1] - 1, p[2]))]
        cands = [spot[port.name]] if port.name in spot else stand[:6]
        for at in cands:
            if spot.get("_bot") != at:
                place_bot(at)
                spot["_bot"] = at
            srv.cmd(f"player {bot} look at {lx + 0.5} {ly + 0.1} {lz + 0.5}")
            srv.cmd(f"player {bot} use once")
            for _ in range(10):
                if lever_on(port) != was:
                    spot[port.name] = at
                    log.append({"click": port.name, "bot_at": list(at), "gametime": gt(), "lever_now": not was})
                    return True
                time.sleep(0.05)
        log.append({"click": port.name, "failed": True})
        return False

    ports = {p.name: p for p in contract.ports}
    outs = contract.outputs
    res = {"clicks": log}
    if contract.behaviour.truth_table:
        rows, level = [], {}
        for r in contract.behaviour.truth_table.rows:
            for k, v in r.inputs.items():
                if level.get(k, 0) != v:
                    click(ports[k])
                    level[k] = v
            time.sleep(1.0)
            got = {p.name: int(is_on(p)) for p in outs}
            rows.append({"inputs": r.inputs, "expect": r.expect, "got": got,
                         "ok": all(got[k] == e for k, e in r.expect.items() if e is not None)})
        res["rows"] = rows
        res["ok"] = all(r["ok"] for r in rows)
    else:
        seq = max(contract.behaviour.sequences, key=lambda s: (len(s.events), s.duration))
        evs = sorted(seq.events, key=lambda e: e.t)
        first = [e for e in evs if e.t == 0]
        for e in first:                 # t = 0 is the game tick right after the first click lands
            for k, v in e.set.items():
                click(ports[k])
        t0 = gt() - 1 if first else gt()
        samples, rest = [], [e for e in evs if e.t > 0]
        while True:
            t = gt() - t0
            while rest and rest[0].t <= t:
                for k, v in rest.pop(0).set.items():
                    click(ports[k])
                log[-1]["t"] = log[-1].get("gametime", 0) - t0
            samples.append((t, {p.name: int(is_on(p)) for p in outs}))
            if t > seq.duration:
                break
        res["sequence"] = seq.name
        res["ticks_sampled"] = len({t for t, _ in samples})
        trans = {}
        for p in outs:
            last = None
            for t, v in samples:
                if v[p.name] != last:
                    trans.setdefault(p.name, []).append([t, v[p.name]])
                    last = v[p.name]
        res["transitions"] = trans
        checks = []
        for e in seq.expect:
            vals = [v[e.port] for t, v in samples if e.t1 <= t <= e.t2]
            ok = bool(vals) and (any(x == e.value for x in vals) if e.mode == "any" else all(x == e.value for x in vals))
            checks.append({"port": e.port, "value": e.value, "window": [e.t1, e.t2], "mode": e.mode, "ok": ok, "n": len(vals)})
        res["checks"] = checks
        res["ok"] = all(c["ok"] for c in checks)
    srv.cmd(f"player {bot} kill")
    return res


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start-server", action="store_true")
    ap.add_argument("--sessions", nargs="?", const="", default=None,
                    help="data/sandbox/*/session builds (handover world); optional comma-separated name substrings")
    ap.add_argument("--tasks", help="comma-separated task ids: paste their reference handover")
    ap.add_argument("--litematic", nargs="*")
    ap.add_argument("--blocks", nargs="*")
    ap.add_argument("--stimulate", help="substring of one pasted build's name to drive with a fake player")
    ap.add_argument("--rate", type=int, default=64, help="commands per 50 ms (Litematica's default command rate)")
    ap.add_argument("--settle", type=float, default=3.0, help="seconds of real ticking before the after-unfreeze read-back")
    ap.add_argument("--keep", action="store_true", help="leave the pasted builds in the world")
    ap.add_argument("--json", default=str(ROOT / "data/oracle/paste_receipt_server.json"))
    args = ap.parse_args()
    items = collect(args)
    receipts = []
    with Server(args.start_server) as srv:
        ver = srv.cmd("script run system_info('game_version')")
        print("server", ver.strip())
        for k, (name, blocks, contract, corner, note) in enumerate(items):
            rec = {"name": name, "note": note}
            receipts.append(rec)
            if blocks is None:
                print(f"{name}: {note}")
                continue
            cells = norm(blocks)
            info = paste(srv, cells, (3000 + 400 * k, 100, 3000), args.rate)
            info["corner"] = corner
            frozen = read_box(srv, *info["box"])
            rec.update(blocks=len(cells), commands=info["commands"], paste_s=info["paste_s"], origin=info["origin"],
                       rejected=info["rejected"], frozen=compare(cells, info, frozen),
                       block_entities=block_entities(srv, cells, info))
            srv.cmd("carpet fillUpdates true")
            srv.cmd("tick unfreeze")
            time.sleep(args.settle)
            rec["after_unfreeze"] = compare(cells, info, read_box(srv, *info["box"]))
            if args.stimulate and args.stimulate in name and contract is not None:
                rec["stimulus"] = stimulate(srv, contract, info, frozen)
            f, a = rec["frozen"], rec["after_unfreeze"]
            print(f"{name[:44]:44s} blocks {len(cells):5d} cmds {info['commands']:5d} paste {info['paste_s']:6.1f}s | "
                  f"read-back {f['matched']}/{f['cells']} cells match, {len(info['rejected'])} rejected | "
                  f"after {args.settle:.0f}s unfrozen: {a['mismatched']} differ"
                  + (f" | stimulus {'pass' if rec['stimulus']['ok'] else 'FAIL'}" if "stimulus" in rec else ""))
            for e in (f["examples"] + a["examples"])[:3]:
                print("    ", e)
            if not args.keep:
                srv.cmd("tick freeze")
                for lo, hi in boxes(*info["box"]):
                    srv.cmd(f"fill {lo[0]} {lo[1]} {lo[2]} {hi[0]} {hi[1]} {hi[2]} air")
                srv.cmd("tick unfreeze")
                lo, hi = info["box"]
                srv.cmd(f"forceload remove {lo[0]} {lo[2]} {hi[0]} {hi[2]}")
        srv.cmd("carpet fillUpdates true")
        srv.cmd("tick unfreeze")
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps({"server": ver.strip(), "receipts": receipts}, indent=1, default=str))
    print("wrote", args.json)
    ok = all(r.get("frozen", {}).get("mismatched", 1) == 0 and not r.get("rejected") for r in receipts if r.get("blocks"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
