#!/usr/bin/env python3
"""Dependency-light LoRA SFT (pure torch + transformers; no peft/trl/accelerate) for 1.7B-4B models on one 12 GB GPU.

    /usr/bin/python3 training/sft/train.py --model qwen3-1.7b --data data/synth/sft --out data/training_runs/sft_smoke \
        --max-len 8192 --steps 200 --grad-accum 4 --lr 2e-4 --time-budget-s 480
    # rerun the same command to resume from <out>/checkpoint (optimizer, scheduler, data position, RNG)

Memory plan: bf16 frozen base, fp32 LoRA (r=16 on q/k/v/o/gate/up/down), gradient checkpointing (non-reentrant), batch = one
episode, the LM head + cross-entropy evaluated only on assistant tokens in checkpointed 1024-token chunks (full-vocab logits of
an 8k episode would be 5 GB in fp32). Loss = mean over the assistant tokens of each optimizer step (grad accumulation).
Outputs: <out>/metrics.jsonl (train loss per step, eval loss), <out>/checkpoint/ (resumable), <out>/adapter/ (final, PEFT layout).
"""
import argparse
import json
import math
import os
import random
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from training.local.paths import setup_paths  # noqa: E402

setup_paths()

import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from torch.utils.checkpoint import checkpoint  # noqa: E402

from training.local import lora as L  # noqa: E402
from training.sft.dataset import IGNORE  # noqa: E402

LOSS_CHUNK = 1024


def decoder_of(model):
    return model.get_decoder() if hasattr(model, "get_decoder") else model.model


def _chunk_ce(lm_head, h, tgt):
    return F.cross_entropy(lm_head(h).float(), tgt, reduction="sum")


def masked_lm_loss(model, input_ids, labels, chunk: int = LOSS_CHUNK, ckpt: bool = True):
    """(sum of token NLLs over labelled positions, number of labelled positions). input_ids/labels: [1, T] tensors."""
    h = decoder_of(model)(input_ids=input_ids, use_cache=False).last_hidden_state[0, :-1]
    tgt = labels[0, 1:]
    m = tgt != IGNORE
    h, tgt = h[m], tgt[m]
    total = h.new_zeros((), dtype=torch.float32)
    for s in range(0, tgt.shape[0], chunk):
        if ckpt and torch.is_grad_enabled():
            total = total + checkpoint(_chunk_ce, model.lm_head, h[s:s + chunk], tgt[s:s + chunk], use_reentrant=False)
        else:
            total = total + _chunk_ce(model.lm_head, h[s:s + chunk], tgt[s:s + chunk])
    return total, int(m.sum())


def lr_lambda(warmup: int, total: int, min_ratio: float = 0.1):
    def f(step):
        if step < warmup:
            return (step + 1) / max(1, warmup)
        p = min(1.0, (step - warmup) / max(1, total - warmup))
        return min_ratio + (1 - min_ratio) * 0.5 * (1 + math.cos(math.pi * p))
    return f


class Trainer:
    """Batch size 1 (one episode), `grad_accum` episodes per optimizer step. Data order = a seeded permutation per epoch;
    the position (epoch, index) is part of the checkpoint so a resumed run sees the same sequence of episodes."""

    def __init__(self, model, train, evals, out_dir, *, lr=2e-4, steps=200, warmup=10, grad_accum=4, clip=1.0, seed=0,
                 eval_every=50, save_every=25, eval_max=None, base_model_id="", device="cuda", log=print, extra_meta=None):
        self.model, self.train, self.evals = model, train, evals
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.steps, self.grad_accum, self.clip, self.seed = steps, grad_accum, clip, seed
        self.eval_every, self.save_every, self.eval_max = eval_every, save_every, eval_max
        self.base_model_id, self.device, self.log = base_model_id, device, log
        self.params = L.lora_parameters(model)
        self.opt = torch.optim.AdamW(self.params, lr=lr, betas=(0.9, 0.999), weight_decay=0.0)
        self.sched = torch.optim.lr_scheduler.LambdaLR(self.opt, lr_lambda(warmup, steps))
        self.state = {"step": 0, "epoch": 0, "idx": 0, "tokens_seen": 0, "train_seconds": 0.0}
        self.extra_meta = extra_meta or {}

    # -- data order
    def _order(self, epoch):
        idx = list(range(len(self.train)))
        random.Random(self.seed * 1000 + epoch).shuffle(idx)
        return idx

    def _next_example(self):
        order = self._order(self.state["epoch"])
        if self.state["idx"] >= len(order):
            self.state["epoch"] += 1
            self.state["idx"] = 0
            order = self._order(self.state["epoch"])
        ex = self.train[order[self.state["idx"]]]
        self.state["idx"] += 1
        return ex

    def _tensors(self, ex):
        ids = torch.tensor([ex.input_ids], device=self.device)
        lab = torch.tensor([ex.labels], device=self.device)
        return ids, lab

    # -- checkpointing
    def save(self, final=False):
        ck = self.out / "checkpoint"
        tmp = self.out / "checkpoint.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        L.save_adapter(self.model, tmp, self.base_model_id, {"step": self.state["step"], **self.extra_meta})
        torch.save({"opt": self.opt.state_dict(), "sched": self.sched.state_dict(), "torch_rng": torch.get_rng_state(),
                    "cuda_rng": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}, tmp / "optim.pt")
        (tmp / "state.json").write_text(json.dumps(self.state))
        shutil.rmtree(ck, ignore_errors=True)
        tmp.rename(ck)
        if final:
            ad = self.out / "adapter"
            shutil.rmtree(ad, ignore_errors=True)
            L.save_adapter(self.model, ad, self.base_model_id, {"step": self.state["step"], **self.extra_meta})

    def resume(self) -> bool:
        ck = self.out / "checkpoint"
        if not (ck / "state.json").exists():
            return False
        L.load_adapter(self.model, ck)
        o = torch.load(ck / "optim.pt", map_location="cpu", weights_only=False)
        self.opt.load_state_dict(o["opt"])
        self.sched.load_state_dict(o["sched"])
        torch.set_rng_state(o["torch_rng"])
        if o.get("cuda_rng") is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(o["cuda_rng"])
        self.state = json.loads((ck / "state.json").read_text())
        return True

    def _metric(self, rec):
        with open(self.out / "metrics.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    # -- loops
    @torch.no_grad()
    def evaluate(self, limit=None):
        self.model.eval()
        tot, n = 0.0, 0
        for ex in self.evals[:limit or self.eval_max]:
            ids, lab = self._tensors(ex)
            s, k = masked_lm_loss(self.model, ids, lab, ckpt=False)
            tot += float(s)
            n += k
        self.model.train()
        return tot / max(1, n)

    def train_step(self):
        self.model.train()
        group = [self._next_example() for _ in range(self.grad_accum)]
        n_tok = sum(e.n_train for e in group)
        self.opt.zero_grad(set_to_none=True)
        tot = 0.0
        for ex in group:
            ids, lab = self._tensors(ex)
            s, _ = masked_lm_loss(self.model, ids, lab)
            (s / n_tok).backward()
            tot += float(s.detach())
        gnorm = float(torch.nn.utils.clip_grad_norm_(self.params, self.clip))
        if not math.isfinite(gnorm):
            raise FloatingPointError(f"non-finite grad norm at step {self.state['step']}")
        self.opt.step()
        self.sched.step()
        self.state["step"] += 1
        self.state["tokens_seen"] += sum(e.n_tokens for e in group)
        return tot / n_tok, gnorm, n_tok

    def fit(self, deadline=None):
        t_start = time.time()
        if self.state["step"] == 0 and self.evals:
            ev = self.evaluate()
            self._metric({"step": 0, "eval_loss": ev, "t": time.time()})
            self.log(f"step 0 eval_loss {ev:.4f}")
        step_times = []
        while self.state["step"] < self.steps:
            if deadline is not None and step_times and time.time() + 1.5 * max(step_times[-5:]) + 30 > deadline:
                self.log(f"time budget reached at step {self.state['step']}; checkpointing")
                break
            t0 = time.time()
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            loss, gnorm, n_tok = self.train_step()
            dt = time.time() - t0
            step_times.append(dt)
            self.state["train_seconds"] += dt
            st = self.state["step"]
            rec = {"step": st, "loss": loss, "grad_norm": gnorm, "lr": self.sched.get_last_lr()[0], "train_tokens": n_tok,
                   "step_s": round(dt, 2), "t": time.time()}
            if torch.cuda.is_available():
                rec["peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
            if self.evals and (st % self.eval_every == 0 or st == self.steps):
                rec["eval_loss"] = self.evaluate()
            self._metric(rec)
            self.log(f"step {st} loss {loss:.4f} gnorm {gnorm:.3f} lr {rec['lr']:.2e} {dt:.1f}s"
                     + (f" peak {rec.get('peak_gb')} GB" if "peak_gb" in rec else "")
                     + (f" eval {rec['eval_loss']:.4f}" if "eval_loss" in rec else ""))
            if st % self.save_every == 0:
                self.save()
        done = self.state["step"] >= self.steps
        self.save(final=done)
        return {"done": done, "step": self.state["step"], "seconds": round(time.time() - t_start, 1)}


def ep_key(ex) -> str:
    return f"{ex.meta.get('file')}:{ex.meta.get('line')}"


def frozen_split(manifest: Path, data, eval_frac: float, seed: int):
    """Split once and remember it: the data directory may grow while a run is paused (the synth engine appends), so a resumed
    run must keep the same train/eval episodes. The manifest lists (file:line) keys of both sides."""
    from training.sft.dataset import split
    if manifest.exists():
        m = json.loads(manifest.read_text())
        by = {ep_key(e): e for e in data}
        missing = [k for k in m["train"] + m["eval"] if k not in by]
        if missing:
            raise RuntimeError(f"{len(missing)} manifest episodes are no longer in the data (e.g. {missing[0]})")
        return [by[k] for k in m["train"]], [by[k] for k in m["eval"]]
    train, evals = split(data, eval_frac, seed)
    manifest.write_text(json.dumps({"train": [ep_key(e) for e in train], "eval": [ep_key(e) for e in evals]}))
    return train, evals


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3-1.7b")
    ap.add_argument("--data", nargs="+", default=["data/synth/sft"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--steps", type=int, default=200)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=float, default=32.0)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--eval-frac", type=float, default=0.1)
    ap.add_argument("--eval-every", type=int, default=25)
    ap.add_argument("--eval-max", type=int, default=16, help="evaluate on at most this many held-out episodes")
    ap.add_argument("--save-every", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--all-episodes", action="store_true", help="also train on episodes without meta.verified")
    ap.add_argument("--time-budget-s", type=float, help="checkpoint and exit before this many seconds (rerun to resume)")
    a = ap.parse_args(argv)
    t0 = time.time()
    deadline = t0 + a.time_budget_s if a.time_budget_s else None
    from training.local.models import load_model
    from training.sft.dataset import build
    model, tok, info = load_model(a.model)
    data, rep = build(a.data, tok, a.max_len, verified_only=not a.all_episodes)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    train, evals = frozen_split(out / "manifest.json", data, a.eval_frac, a.seed)
    print(f"data: {json.dumps(rep)}; train {len(train)} / eval {len(evals)} episodes")
    if not train:
        print("error: no training episodes")
        return 2
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    L.apply_lora(model, a.rank, a.alpha, a.dropout)
    print(f"model {info['model_id']}: {info['params'] / 1e9:.2f}B params, LoRA trainable {L.count_trainable(model) / 1e6:.1f}M")
    meta = {"model": info["model_id"], "data": a.data, "max_len": a.max_len, "lr": a.lr, "rank": a.rank, "alpha": a.alpha,
            "grad_accum": a.grad_accum, "steps": a.steps, "train_tasks": sorted({str(e.meta.get('task')) for e in train}),
            "eval_tasks": sorted({str(e.meta.get('task')) for e in evals}), "data_report": rep}
    if not (out / "run.json").exists():
        (out / "run.json").write_text(json.dumps(meta, indent=1))
    tr = Trainer(model, train, evals, out, lr=a.lr, steps=a.steps, warmup=a.warmup, grad_accum=a.grad_accum, seed=a.seed,
                 eval_every=a.eval_every, save_every=a.save_every, eval_max=a.eval_max, base_model_id=info["model_id"],
                 extra_meta={"data": a.data})
    if tr.resume():
        print(f"resumed at step {tr.state['step']} (epoch {tr.state['epoch']}, idx {tr.state['idx']})")
    res = tr.fit(deadline)
    print(f"{'finished' if res['done'] else 'paused'} at step {res['step']} after {res['seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
