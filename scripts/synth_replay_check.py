"""Independent check of exported SFT rows: the LAST build source of random rows -> harness build rules -> full env run (eval seed).

    .venv/bin/python scripts/synth_replay_check.py --per-shard 5
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from training.synth import common as C  # noqa: E402


def check(line):
    from env.runner import run
    from env.score import score
    from training.synth.bugs import solution_of
    m = json.loads(line)
    meta = m["meta"]
    task, ref, synth = C.load(C.TASKS / meta["family"] / f"{meta['task']}.json")
    src = [json.loads(c["function"]["arguments"])["source"] for msg in m["messages"] if msg["role"] == "assistant"
           for c in msg.get("tool_calls", []) if c["function"]["name"] == "build"][-1]
    sc = score(task.contract, run(task.contract, solution_of(task, src), seed=task.contract.eval_seed))
    return meta["task"], meta["kind"], sc.full_pass, round(sc.reward, 3)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-shard", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    lines = []
    for shard in sorted(C.SFT.glob("*.jsonl")):
        rows = shard.read_text().splitlines()
        lines += rng.sample(rows, min(a.per_shard, len(rows)))
    res = list(C.pmap(check, lines, a.workers))
    for r in res:
        print(*r)
    ok = sum(r[2] for r in res)
    print(f"{ok}/{len(res)} replayed rows are full passes")
    return 0 if ok == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
