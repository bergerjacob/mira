"""Run every mira_env task's reference solution and its stored broken variants; print a table.

    python scripts/run_env_demo.py                 # table for all tasks
    python scripts/run_env_demo.py t1_xor t0_not   # selected tasks
    python scripts/run_env_demo.py --episode t0_and  # scripted agent episode through the tool surface (break, diagnose, fix, submit)
"""
import argparse
import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from env import mutate
from env.runner import run
from env.score import score
from env.task import list_task_ids, load_task
from env.tools import Env


def table(ids):
    print(f"{'task':20} {'blk':>4} {'ref':>5} {'R0 R1 R2 R3 R4':>15} {'broken variants (reward)':<56} {'s':>5}")
    bad = 0
    for tid in ids:
        t = load_task(tid)
        t0 = time.time()
        sc = score(t.contract, run(t.contract, t.solution))
        brk = []
        for b in t.broken:
            s = score(t.contract, run(t.contract, mutate.apply(t.solution, [tuple(e) for e in b["edits"]]), fast=True))
            brk.append(f"{b['kind']}:{s.reward:.2f}{'' if not s.full_pass else '!PASS'}")
            bad += s.full_pass
        ok = sc.full_pass and abs(sc.reward - 1) < 1e-9
        bad += not ok
        print(f"{tid:20} {len(t.solution):>4} {sc.reward:>5.2f} {sc.r0:>3.0f} {sc.r1:>2.0f} {sc.r2:>2.0f} {sc.r3:>2.0f} {sc.r4:>2.0f}   "
              f"{' '.join(brk):<56} {time.time() - t0:>5.1f}" + ("" if ok else "   <-- REFERENCE FAILS"))
    print(f"{len(ids)} tasks; {'all references pass and all broken variants fail' if not bad else str(bad) + ' problem(s)'}")
    return bad


def episode(tid):
    t = load_task(tid)
    env = Env(t, max_rounds=5)
    print(env.task_info(), "\n")
    print(">> set_blocks(reference minus one block)")
    victim = t.broken[0]["edits"] if t.broken else [["remove", t.solution[0][:3]]]
    print(env.set_blocks(mutate.apply(t.solution, [tuple(e) for e in victim])))
    print("\n>> view(layer=1)\n" + env.view(layer=1))
    print("\n>> run_spec()\n" + env.run_spec())
    print("\n>> (agent repairs) set_blocks(reference)")
    print(env.set_blocks(t.solution))
    print("\n>> run_spec()\n" + env.run_spec())
    print("\n>> submit()\n" + env.submit())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--episode", metavar="TASK")
    a = ap.parse_args()
    if a.episode:
        episode(a.episode)
    else:
        sys.exit(1 if table(a.ids or list_task_ids()) else 0)
